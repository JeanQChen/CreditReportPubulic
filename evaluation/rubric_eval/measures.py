# -*- coding: utf-8 -*-
"""24 个指标的读数实现：全部只依赖被评 run 自己落盘的产物。

写在这里的每一条读数都要能被第三方**从产物重算**。因此本模块里不出现：网络、时钟、
随机、数据库连接、对 ChatClient 的调用。凡是"要问模型"或"要问人"的东西，一律走
:func:`evaluation.rubric_eval.result.pending`，并写明第一个缺的是什么。

三条不许越过的线：

* **不拿过程计数冒充业务正确率。** 材料份数、节点数、页命中数、路由条数都只能进
  ``details`` 或 ``note``，不能进 ``numerator``。
* **不跨 run 拼分子分母。** 每条读数的 ``source_run_id`` 就是它唯一的数据来源。
* **缺件与空集分开。** 文件不在盘上 → ``has()`` 为假；文件在盘上而集合为空 →
  分母为 0、``value=None``。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .reader import RunReader
from .result import MetricResult, measured, pending, ratio

SECTIONS: tuple[str, ...] = ("company", "financial")

#: 只有这个产出者身份算「本 run 的真实独立审阅」。离线替身 `offline_diagnostic_echo`
#: 会照常写出 `issues[]`，但它没有任何独立语义判断，P08／B13 都必须把它与真实审阅分开。
INDEPENDENT_REVIEW_PRODUCER = "independent_llm_review"

#: 阶段日志里每个已完成阶段应当留下的产物。用来回答"这个阶段是不是真的落了东西"。
_STAGE_ARTIFACT: dict[str, str] = {
    "run_input_verified": "run_input_binding.json",
    "financial_input_verified": "financial_input_binding.json",
    "environment_built": "table_proof_matrix.json",
    "table_proof_matrix": "table_proof_matrix.json",
    "financial_snapshot_rechecked": "financial_input_binding.json",
    "writer_requested": "cited_input_manifest.json",
    "writer_replied": "cited_prose.json",
    "sentence_checked": "sentence_checks.json",
    "review_requested": "review_issues.json",
    "review_replied": "review_issues.json",
    "report_version_written": "cited_report_version.json",
    "section_emitted": "source_table_display.json",
}

#: 节级产物文件名。
_FILES = {
    "prose": "cited_prose.json",
    "manifest": "cited_input_manifest.json",
    "checks": "sentence_checks.json",
    "review": "review_issues.json",
    "report": "cited_report_version.json",
    "gaps": "cited_gap_bins.json",
    "withheld": "withheld_candidates.json",
    "display": "source_table_display.json",
    "placement": "fact_placement.json",
    "routing": "presentation_routing.json",
    "rework": "cited_rework.json",
}

_NUM_UNIT = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|元|个百分点|%)")
#: 同一套数字，但允许**没有单位**（比率类格值如「1.57」既无单位也无数千分位）。
_NUM_ANY = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|元|个百分点|%)?")
#: 每单位下"两位小数显示"允许的绝对误差（以元 / 点位计）。
_UNIT_TOLERANCE = {"亿元": Decimal("500000"), "万元": Decimal("50"),
                   "元": Decimal("0.005"), "个百分点": Decimal("0.005"), "%": Decimal("0.005")}


# --------------------------------------------------------------------------
# 载入
# --------------------------------------------------------------------------
@dataclass
class Sec:
    """一节的全部产物。缺件一律是 ``None``，不是空字典。"""

    name: str
    objects: dict[str, Any] = field(default_factory=dict)
    refs: list[dict[str, Any]] = field(default_factory=list)

    def get(self, key: str) -> Any:
        return self.objects.get(key)

    def has(self, key: str) -> bool:
        return self.objects.get(key) is not None


def load_sections(reader: RunReader) -> dict[str, Sec]:
    """把两节会用到的产物一次读进来，并逐份登记证据。"""
    out: dict[str, Sec] = {}
    for name in SECTIONS:
        sec = Sec(name=name)
        for key, filename in _FILES.items():
            rel = f"{name}/{filename}"
            sec.objects[key] = reader.json(rel) if reader.has(rel) else None
            sec.refs.append(reader.ref(rel, f"$（{filename} 顶层）"))
        out[name] = sec
    return out


def _journal(reader: RunReader) -> list[dict[str, Any]]:
    path = reader.path("run_progress.jsonl")
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"stage": "<unparsable>", "status": "<unparsable>"})
    return events


def _sentences(prose: dict[str, Any] | None):
    """逐句迭代：产出 ``(subsection_id, paragraph, sentence)``。"""
    if not prose:
        return
    draft = prose.get("draft") or {}
    for sub in draft.get("subsections") or []:
        for para in sub.get("paragraphs") or []:
            for sent in para.get("sentences") or []:
                yield sub.get("subsection_id", ""), para, sent


def _all_sentences(prose: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [s for _, _, s in _sentences(prose)]


def _key_map(manifest: dict[str, Any] | None) -> dict[str, tuple[str, dict[str, Any]]]:
    """``citation_key -> ('material'|'fact', 条目)``。"""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    if not manifest:
        return out
    for kind, bucket in (("material", manifest.get("materials") or []),
                         ("fact", manifest.get("facts") or [])):
        for entry in bucket:
            key = entry.get("citation_key")
            if key:
                out[key] = (kind, entry)
    return out


def _uploaded_documents(reader: RunReader) -> dict[str, str]:
    """``document_id -> 本次上传对象的 sha256``。"""
    binding = reader.json("run_input_binding.json") or reader.input_json("run_input_manifest.json")
    out: dict[str, str] = {}
    for doc in (binding or {}).get("documents") or []:
        out[doc.get("document_id", "")] = doc.get("declared_sha256", "")
    return out


def _snapshot_id(reader: RunReader) -> str:
    fin = reader.json("financial_input_binding.json")
    return ((fin or {}).get("snapshot") or {}).get("snapshot_id", "")


def _verify_locator(entry: dict[str, Any], uploaded: dict[str, str],
                    snapshot_id: str) -> tuple[bool, str]:
    """一条材料/事实能不能回到**本 run 已登记的来源身份与定位**。"""
    locator = entry.get("locator_ref") or {}
    owner = locator.get("owner") or ""
    if owner.startswith("evidence_document:"):
        body, _, _frag = owner.partition("#")
        doc_part, _, ver_part = body[len("evidence_document:"):].partition("@sha256-")
        want = uploaded.get(doc_part)
        if not want:
            return False, f"{doc_part} 不在本次上传的三份里"
        if not ver_part.startswith(want[:16]):
            return False, f"{doc_part} 版本 {ver_part[:16]} 与本次上传 {want[:16]} 不一致"
        if not locator.get("locator_kind"):
            return False, "定位缺 locator_kind"
        return True, ""

    payload = entry.get("payload_ref")
    if isinstance(payload, dict):
        loc = payload.get("locator") or {}
        doc_part = loc.get("document_id", "")
        version = loc.get("document_version", "")
        want = uploaded.get(doc_part)
        if not want:
            return False, f"{doc_part} 不在本次上传的三份里"
        if not str(version).startswith("sha256-") or version[7:23] != want[:16]:
            return False, f"{doc_part} 版本 {version} 与本次上传不一致"
        return True, ""

    source = entry.get("source_identity") or ""
    if source.startswith("financial_snapshot:"):
        snap = source.split(":", 1)[1]
        if snap != snapshot_id:
            return False, f"财务快照 {snap} 不是本次绑定的 {snapshot_id or '<无快照>'}"
        return True, ""
    return False, f"来源身份不可解析：{source or locator or '两者都缺'}"


def _manifest_qualification(entry: dict[str, Any]) -> tuple[bool, str]:
    """一条 manifest 条目有没有 typed 资格决定及依据。"""
    if not entry.get("authority_kind"):
        return False, "缺 authority_kind"
    if not entry.get("provenance_identity"):
        return False, "缺 provenance_identity"
    if entry.get("fact_type") == "fact":
        if entry.get("payload_ref") is None and not (entry.get("source_identity") or "").startswith(
                "financial_snapshot:"):
            return False, "事实既无 payload_ref 又无财务快照来源"
        return True, ""
    qual = entry.get("content_qualification")
    if not isinstance(qual, dict) or "is_material" not in qual:
        return False, "材料缺 content_qualification"
    return True, ""


def _cell_numbers(text: str) -> list[tuple[str, str]]:
    """格值里的 (数字, 单位)；单位可能为空串（比率、倍数类格值）。"""
    return [(m.group(1).replace(",", ""), m.group(2) or "")
            for m in _NUM_ANY.finditer(text or "")]


def _cell_matches_fact(cell: str, fact_text: str) -> bool:
    """格值是否就是所引事实命题里的那个值。

    两条接受路径，都要求**数字逐字相同**（千分位归一）：

    * 带单位时：格值的 (数字, 单位) 在事实文本的数字元组集合里；
    * 无单位时（比率/倍数）：格值的数字在事实文本的数字集合里。

    都拒绝"四舍五入后相等"——那会把"读错一位"放过去。
    """
    want = _cell_numbers(cell)
    if not want or not str(fact_text or "").strip():
        return False
    have = _cell_numbers(fact_text)
    tuples = set(have)
    numbers = {n for n, _u in have}
    value, unit = want[-1]
    if (value, unit) in tuples:
        return True
    return unit == "" and value in numbers


# --------------------------------------------------------------------------
# D01–D03：开发与版本。这三条**不**来自报告 run，来自本批开发批次。
# --------------------------------------------------------------------------
def _dev_ref(reader: RunReader, field_path: str) -> dict[str, Any]:
    """指向本批开发测试台账（在评测输出目录里，不在被评 run 里）。"""
    dev = reader.dev_batch or {}
    return {"run_id": f"dev-batch::{reader.run_id}", "path": dev.get("path", ""),
            "field_path": field_path, "sha256": dev.get("sha256"),
            "exists": bool(dev), "note": "开发测试批次，与报告 run 分账"}


def d01(reader: RunReader) -> MetricResult:
    dev = reader.dev_batch
    if not dev:
        return pending(
            metric_id="D01", run_id=reader.run_id, scope="本批开发测试批次（与报告 run 分账）",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="NOT_RUN_UPSTREAM",
            verdict="本批聚焦测试未运行",
            first_missing="缺少本批聚焦测试的运行记录（由评测命令写出 dev_batch.json）",
            missing_chain=("未运行聚焦测试", "未记录命令/退出码/通过失败跳过数", "无法报告分子分母"),
            evidence_refs=(_dev_ref(reader, "tests[]"),),
            limitations=("开发测试结果与报告 run 是两本账，不得互相替代。",))
    tests = dev.get("tests") or []
    passed = sum(t.get("passed") or 0 for t in tests)
    failed = sum(t.get("failed") or 0 for t in tests)
    skipped = sum(t.get("skipped") or 0 for t in tests)
    crashed = [t for t in tests if t.get("crashed")]
    den = passed + failed
    return measured(
        metric_id="D01", run_id=reader.run_id, scope="本批开发测试批次（与报告 run 分账）",
        numerator=passed, denominator=den, unit="ratio",
        verdict=f"聚焦批次：{passed} 通过 / {failed} 失败 / {skipped} 跳过"
                + (f"；{len(crashed)} 个模块崩溃" if crashed else ""),
        evidence_refs=(_dev_ref(reader, "tests[].passed/failed/skipped"),),
        limitations=("单测通过只说明该批断言成立，不构成任何业务质量结论。",
                     "全量测试是否本批运行单列，不并入本行分子分母。"),
        details={"tests": tests, "full_suite_run": dev.get("full_suite_run", False),
                 "command": dev.get("command"), "mock_llm": dev.get("mock_llm")})


def d02(reader: RunReader) -> MetricResult:
    ledger = reader.ledger() or {}
    binding = reader.json("run_input_binding.json") or {}
    gaps = (reader.json("company/cited_gap_bins.json") or {})
    identity = {
        "run_id": reader.run_id,
        "mode": ledger.get("mode"),
        "approved_model": ledger.get("approved_model"),
        "budget_policy_version": ledger.get("budget_policy_version"),
        "prompt_versions": sorted({a.get("prompt_version") for a in
                                   (ledger.get("call_budget") or {}).get("attempts") or []}),
        "contract_fingerprint": gaps.get("contract_fingerprint"),
        "run_input_manifest_sha256": binding.get("manifest_sha256"),
        "run_input_version": binding.get("run_input_version"),
    }
    axes = [
        ("模型与预算策略", bool(identity["approved_model"] and identity["budget_policy_version"])),
        ("Prompt 版本", bool(identity["prompt_versions"])),
        ("Contract 指纹", bool(identity["contract_fingerprint"])),
        ("输入绑定哈希", bool(identity["run_input_manifest_sha256"])),
    ]
    num = sum(1 for _, ok in axes if ok)
    limitations = [
        "本读数只覆盖报告 run 自己声明的身份轴；代码提交与工作区状态不在产物里，需另由开发批次报告。",
        "工作区非 clean 时不得写成已封存。",
    ]
    return measured(
        metric_id="D02", run_id=reader.run_id, scope="报告 run 的身份轴（代码侧另计）",
        numerator=num, denominator=len(axes), unit="ratio",
        verdict=f"{num}/{len(axes)} 条身份轴可从产物唯一定位",
        evidence_refs=(reader.ref("cited_call_ledger.json", "mode/approved_model/budget_policy_version/call_budget.attempts[].prompt_version"),
                       reader.ref("run_input_binding.json", "manifest_sha256/run_input_version"),
                       reader.ref("company/cited_gap_bins.json", "contract_fingerprint")),
        limitations=tuple(limitations),
        details={"identity_axes": [{"axis": a, "locatable": ok} for a, ok in axes],
                 "identity": identity})


def d03(reader: RunReader) -> MetricResult:
    dev = reader.dev_batch
    if not dev:
        return pending(
            metric_id="D03", run_id=reader.run_id, scope="本批开发批次（与报告 run 分账）",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="NOT_RUN_UPSTREAM",
            verdict="本批缺陷台账未建立",
            first_missing="缺少本批缺陷台账（由评测命令写出 dev_batch.json）",
            missing_chain=("未登记本批缺陷", "无反例/正例对账", "无法报告闭环率"),
            evidence_refs=(_dev_ref(reader, "defects[]"),),
            limitations=("被评 run 里观察到的产品缺陷是**产品缺陷**，不等于本批已修；两者必须分开记。",))
    defects = dev.get("defects") or []
    closed = [d for d in defects if d.get("counter_example") and d.get("positive_case")]
    open_defects = [d for d in defects if not (d.get("counter_example") and d.get("positive_case"))]
    den = len(defects)
    return measured(
        metric_id="D03", run_id=reader.run_id, scope="本批开发批次（与报告 run 分账）",
        numerator=len(closed) if den else 0, denominator=den, unit="ratio",
        verdict=(f"{len(closed)}/{den} 条缺陷有完整证据链（首个失败点 + 反例 + 正例）"
                 if den else "本批未登记缺陷"),
        evidence_refs=(_dev_ref(reader, "defects[]"),),
        limitations=("不能用测试总数替代缺陷关闭结论。",
                     "被评 run 观察到的产品缺陷另列在 readback 的「未修留账」，不并入本行分子。"),
        measured_but_empty=(den == 0),
        details={"defects": defects, "still_open": open_defects,
                 "product_defects_observed_in_run": dev.get("product_defects_observed") or []})


# --------------------------------------------------------------------------
# P01：六件输入的身份与字节
# --------------------------------------------------------------------------
def _check_input(reader: RunReader) -> dict[str, Any]:
    binding = reader.json("run_input_binding.json") or reader.input_json("run_input_manifest.json")
    fin = reader.json("financial_input_binding.json") or reader.input_json("financial_input_binding.json")
    items: list[dict[str, Any]] = []

    for doc in (binding or {}).get("documents") or []:
        rel = doc.get("object_relpath", "")
        path = reader.input_path(rel)
        actual = reader.sha256_of(path)
        items.append({
            "kind": "pdf", "id": doc.get("document_id"), "rel": rel,
            "relpath": reader.input_rel(rel),
            "declared_sha256": doc.get("declared_sha256"), "actual_sha256": actual,
            "size_ok": path.is_file() and path.stat().st_size == doc.get("size_bytes"),
            "ok": bool(actual) and actual == doc.get("declared_sha256"),
        })

    snapshot = (fin or {}).get("snapshot") or {}
    snap_hashes = {h for _sid, h in (snapshot.get("source_versions") or [])}
    for src in (fin or {}).get("sources") or []:
        rel = src.get("object_relpath", "")
        path = reader.input_path(rel)
        actual = reader.sha256_of(path)
        items.append({
            "kind": "xlsx", "id": src.get("declared_filename"), "rel": rel,
            "relpath": reader.input_rel(rel),
            "declared_sha256": src.get("declared_sha256"), "actual_sha256": actual,
            "in_snapshot": src.get("declared_sha256") in snap_hashes,
            "ok": bool(actual) and actual == src.get("declared_sha256"),
        })
    return {"items": items, "snapshot_id": snapshot.get("snapshot_id"),
            "snapshot_version": snapshot.get("validity")}


def p01(reader: RunReader) -> MetricResult:
    checked = _check_input(reader)
    items = checked["items"]
    good = [i for i in items if i["ok"]]
    bad = [i for i in items if not i["ok"]]
    xlsx_in_snapshot = [i for i in items if i["kind"] == "xlsx" and i.get("in_snapshot")]
    limitations = [
        "同字节复用不是本次重新抽表；本读数只证明字节与绑定一致。",
        "三份 PDF 必须在只读 evidence 库里登记过；本读数不打开该库，登记侧的核对只到绑定的声明哈希。",
    ]
    if not items:
        return pending(
            metric_id="P01", run_id=reader.run_id, scope="本 run 六件输入",
            measurement_state="FAILED_TO_MEASURE", execution_state="FAILED",
            verdict="没有可核的绑定记录",
            first_missing="run_input_binding.json / financial_input_binding.json 都不在盘上",
            missing_chain=("绑定记录缺失", "无法重算任一对象的哈希", "无法给分母"),
            evidence_refs=(reader.ref("run_input_binding.json", "$"),),
            limitations=tuple(limitations))
    return measured(
        metric_id="P01", run_id=reader.run_id, scope="本 run 六件输入",
        numerator=len(good), denominator=len(items), unit="ratio",
        verdict=f"{len(good)}/{len(items)} 件身份与 SHA-256 逐字节一致"
                + (f"；{len(bad)} 件不符" if bad else ""),
        evidence_refs=(reader.ref("run_input_binding.json", "documents[].declared_sha256/size_bytes"),
                       reader.ref("financial_input_binding.json", "sources[].declared_sha256"),
                       reader.ref("financial_input_binding.json", "snapshot.source_versions"),
                       # 这里的 `rel` 是**输入目录内**的相对路径；`ref(uploaded=True)` 会自己
                       # 补上 `data/run_inputs/<run_id>/`。早先误把已补全的 `relpath` 再喂进去，
                       # 路径被前缀两次 ⇒ 取证全部 `exists=False`。P01 的分子不受影响（它自己
                       # 按绝对路径重算），但"上传的原始字节"这条最要紧的证据链当时是断的。
                       *[reader.ref(i["rel"], "$（整个对象重算哈希）", uploaded=True)
                         for i in items]),
        limitations=tuple(limitations),
        details={"items": items, "mismatches": bad,
                 "snapshot_id": checked["snapshot_id"],
                 "xlsx_matching_snapshot": len(xlsx_in_snapshot)})


# --------------------------------------------------------------------------
# P02：引用可回查
# --------------------------------------------------------------------------
def p02(reader: RunReader) -> MetricResult:
    uploaded = _uploaded_documents(reader)
    snapshot_id = _snapshot_id(reader)
    used: dict[str, list[str]] = {}
    per_section: list[dict[str, Any]] = []
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        prose, manifest = sec.get("prose"), sec.get("manifest")
        if prose is None or manifest is None:
            per_section.append({"section": name, "section_state": "NOT_RUN_UPSTREAM",
                                "used_keys": 0, "resolved": 0})
            continue
        km = _key_map(manifest)
        keys: set[str] = set()
        for sent in _all_sentences(prose):
            keys.update(sent.get("citations") or [])
        ok = 0
        diffs: list[dict[str, Any]] = []
        for key in sorted(keys):
            used.setdefault(key, []).append(name)
            entry_pair = km.get(key)
            if not entry_pair:
                diffs.append({"section": name, "citation_key": key,
                              "failure": "引用键不在本节 manifest 里（未登记）"})
                continue
            kind, entry = entry_pair
            good, why = _verify_locator(entry, uploaded, snapshot_id)
            if good:
                ok += 1
            else:
                diffs.append({"section": name, "citation_key": key, "entry_kind": kind,
                              "failure": why})
        per_section.append({"section": name, "section_state": "DID_RUN",
                            "used_keys": len(keys), "resolved": len(keys) - len(diffs),
                            "unresolved": diffs})

    total = sum(s["used_keys"] for s in per_section)
    resolved = sum(s["resolved"] for s in per_section)
    limitations = [
        "定位存在是「可回查」，不是「句意被支持」；后者需人读或 Gold。",
        "本读数不打开只读 evidence 库，只核 manifest 声明的来源身份与本次上传字节是否同一。",
    ]
    if total == 0:
        return pending(
            metric_id="P02", run_id=reader.run_id, scope="本 run 公司节与财务节正文引用",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="PARTIAL",
            verdict="没有正式正文，无引用可回查",
            first_missing="两节的 cited_prose.json 都不在盘上（或正文里没有任何引用键）",
            missing_chain=("无正式正文", "无引用键", "分母为 0，不报 0%"),
            evidence_refs=(reader.ref("company/cited_prose.json", "$"),
                           reader.ref("financial/cited_prose.json", "$"),
                           reader.ref("company/cited_input_manifest.json", "$"),
                           reader.ref("financial/cited_input_manifest.json", "$")),
            limitations=tuple(limitations),
            details={"per_section": per_section})
    return measured(
        metric_id="P02", run_id=reader.run_id, scope="本 run 公司节与财务节正文引用",
        numerator=resolved, denominator=total, unit="ratio",
        verdict=f"{resolved}/{total} 个引用键可回到本 run 已登记来源身份与定位",
        evidence_refs=(reader.ref("run_input_binding.json", "documents[].declared_sha256"),
                       reader.ref("company/cited_input_manifest.json", "materials[].locator_ref/payload_ref"),
                       reader.ref("financial/cited_input_manifest.json", "facts[].source_identity"),
                       reader.ref("company/cited_prose.json", "draft.subsections[].paragraphs[].sentences[].citations"),
                       reader.ref("financial/cited_prose.json", "draft.subsections[].paragraphs[].sentences[].citations")),
        limitations=tuple(limitations),
        details={"per_section": per_section, "distinct_keys": len(used)})


# --------------------------------------------------------------------------
# P05：Pack→Writer 清单守恒
# --------------------------------------------------------------------------
def p05(reader: RunReader) -> MetricResult:
    per_section: list[dict[str, Any]] = []
    total = same = 0
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        manifest, prose = sec.get("manifest"), sec.get("prose")
        if manifest is None:
            per_section.append({"section": name, "section_state": "NOT_RUN_UPSTREAM"})
            continue
        sent_set = {(m.get("citation_key"), m.get("material_id"), m.get("pack_id"),
                     m.get("member_ref")) for m in manifest.get("materials") or []}
        got = [(a.get("citation_key"), a.get("material_id"), a.get("pack_id"), a.get("member_ref"))
               for a in (prose or {}).get("adoptions") or []]
        got_set = set(got)
        missing = sorted(sent_set - got_set)
        extra = sorted(got_set - sent_set)
        duplicated = len(got) - len(got_set)
        entry = {
            "section": name, "section_state": "DID_RUN",
            "manifest_materials": len(sent_set), "adoption_records": len(got),
            "missing": [list(x) for x in missing], "extra": [list(x) for x in extra],
            "duplicated": duplicated, "equal": not missing and not extra and not duplicated,
            "dispositions": _disposition_counts((prose or {}).get("adoptions") or []),
            "facts_in_manifest": len(manifest.get("facts") or []),
        }
        per_section.append(entry)
        total += len(sent_set)
        if entry["equal"]:
            same += len(sent_set)
    limitations = [
        "集合恰好一致只证明交接未丢，不证明 Pack 足够，也不证明 Writer 采用。",
        "本读数只比材料身份；事实清单没有对应的采用记录，故不进分子分母。",
    ]
    if total == 0:
        return measured(
            metric_id="P05", run_id=reader.run_id, scope="本 run 各节 manifest 材料交接",
            numerator=0, denominator=0, unit="ratio",
            verdict="应送材料集合为空（两节都为空集）",
            evidence_refs=(reader.ref("company/cited_input_manifest.json", "materials[]"),
                           reader.ref("financial/cited_input_manifest.json", "materials[]")),
            limitations=tuple(limitations), measured_but_empty=True,
            details={"per_section": per_section},
            note="空集不是「守恒通过」，只是没有可比的材料。")
    return measured(
        metric_id="P05", run_id=reader.run_id, scope="本 run 各节 manifest 材料交接",
        numerator=same, denominator=total, unit="ratio",
        verdict=f"{same}/{total} 份应送材料与 Writer 侧记录逐身份一致",
        evidence_refs=(reader.ref("company/cited_input_manifest.json", "materials[].citation_key/material_id/pack_id/member_ref"),
                       reader.ref("company/cited_prose.json", "adoptions[]"),
                       reader.ref("financial/cited_input_manifest.json", "materials[]"),
                       reader.ref("financial/cited_prose.json", "adoptions[]")),
        limitations=tuple(limitations), details={"per_section": per_section})


def _disposition_counts(adoptions: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in adoptions:
        key = a.get("disposition", "<none>")
        out[key] = out.get(key, 0) + 1
    return out


# --------------------------------------------------------------------------
# P06：阶段与调用留痕
# --------------------------------------------------------------------------
def p06(reader: RunReader) -> MetricResult:
    events = _journal(reader)
    ledger = reader.ledger() or {}
    attempts = (ledger.get("call_budget") or {}).get("attempts") or []
    completed = [e for e in events if e.get("status") == "completed"]
    checked, with_artifact, orphans = 0, 0, []
    for event in completed:
        stage = event.get("stage", "")
        section = None
        rel = _STAGE_ARTIFACT.get(stage)
        if rel is None:
            continue
        # 阶段名在两节里重复出现：按 journal 顺序认领本节产物。
        section = _section_of(event, events)
        checked += 1
        target = f"{section}/{rel}" if section else rel
        if reader.has(target):
            with_artifact += 1
        else:
            orphans.append({"seq": event.get("seq"), "stage": stage, "expected": target})
    limitations = [
        "阶段事件不是可恢复 checkpoint；本 run 的日志没有 checkpoint_id。",
        "缺 journal 时必须标注「回复原文不在盘上」——本 run 的回复 journal 只挂写作客户端。",
        "调用留痕只证明发生过调用，不证明调用质量。",
    ]
    journal_files = [f"{s}/cited_call_journal.json" for s in SECTIONS if reader.has(f"{s}/cited_call_journal.json")]
    if not events:
        return pending(
            metric_id="P06", run_id=reader.run_id, scope="本 run 运行控制",
            measurement_state="FAILED_TO_MEASURE", execution_state="FAILED",
            verdict="没有阶段日志",
            first_missing="run_progress.jsonl 不在盘上",
            missing_chain=("阶段日志缺失", "无法对齐阶段与产物", "无法报告完成率"),
            evidence_refs=(reader.ref("run_progress.jsonl", "$"),),
            limitations=tuple(limitations),
            details={"calls": attempts})
    return measured(
        metric_id="P06", run_id=reader.run_id, scope="本 run 运行控制",
        numerator=with_artifact, denominator=checked, unit="ratio",
        verdict=f"{with_artifact}/{checked} 个已完成阶段找得到对应产物",
        evidence_refs=(reader.ref("run_progress.jsonl", "stage/status/at_utc/detail"),
                       reader.ref("cited_call_ledger.json", "call_budget.attempts[]")),
        limitations=tuple(limitations),
        details={
            "events": len(events), "completed": len(completed),
            "stages": [e.get("stage") for e in events],
            "orphan_stages": orphans,
            "calls": [{"ordinal": a.get("ordinal"), "section": a.get("section_id"),
                       "category": a.get("category"), "model": a.get("model"),
                       "prompt_version": a.get("prompt_version"), "status": a.get("status"),
                       "call_id": a.get("call_id"), "error": a.get("error")} for a in attempts],
            "attempt_total": (ledger.get("call_budget") or {}).get("attempt_total"),
            "refusal_total": (ledger.get("call_budget") or {}).get("refusal_total"),
            "journal_files": journal_files,
            "resumable": False})


def _section_of(event: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    """按 `section_started` 事件切段，判断这条阶段属于哪一节。"""
    current: str | None = None
    for e in events:
        if e.get("stage") == "section_started":
            # 两节顺序固定：company 在前，financial 在后。
            current = "financial" if current else "company"
        if e is event:
            return current
    return None


# --------------------------------------------------------------------------
# P07：解析与逐句安全
# --------------------------------------------------------------------------
def p07(reader: RunReader) -> MetricResult:
    rows: list[dict[str, Any]] = []
    num = den = 0
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        prose, checks = sec.get("prose"), sec.get("checks")
        if prose is None:
            rows.append({"section": name, "state": "NO_PROSE",
                         "reason": "模型回复未形成正式正文" if sec.has("rework") else "写作未产出该节产物"})
            continue
        if checks is None:
            rows.append({"section": name, "state": "NO_CHECK_REPORT",
                         "sentences": len(_all_sentences(prose))})
            continue
        hard = list(checks.get("hard_error_sentence_ids") or [])
        rows.append({
            "section": name, "state": "CHECKED",
            "sentences": checks.get("sentence_count"),
            "hard_errors": len(hard),
            "fact_safety": len(checks.get("fact_safety_sentence_ids") or []),
            "column_coverage": len(checks.get("column_coverage_sentence_ids") or []),
            "blocked": len(checks.get("blocked_sentence_ids") or []),
            "mechanical_verdict": checks.get("mechanical_verdict"),
            "publishability": checks.get("publishability"),
            "hard_error_ids": hard,
            "policy_version": checks.get("policy_version"),
        })
        num += len(hard)
        den += checks.get("sentence_count") or 0
    measured_sections = [r for r in rows if r["state"] == "CHECKED"]
    limitations = [
        "机械合格只证明底线，不能宣称材料语义支持句子。",
        "未产出正式正文的节不进分子分母，也不得记 0 硬错。",
    ]
    if not measured_sections:
        return pending(
            metric_id="P07", run_id=reader.run_id, scope="本 run 各节正文",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="PARTIAL",
            verdict="没有可核的正式正文",
            first_missing="没有一节落下了 cited_reply 的正式正文（写作返回未形成段落）",
            missing_chain=("无正式正文", "逐句机械核对未运行", "不能报 0 硬错"),
            evidence_refs=(reader.ref("company/sentence_checks.json", "$"),
                           reader.ref("company/cited_call_journal.json", "$")),
            limitations=tuple(limitations), details={"per_section": rows})
    return measured(
        metric_id="P07", run_id=reader.run_id, scope="本 run 各节正文",
        numerator=num, denominator=den, unit="ratio",
        verdict=f"{num}/{den} 句带事实安全/栏目覆盖硬错",
        evidence_refs=(reader.ref("company/sentence_checks.json", "hard_error_sentence_ids/sentence_count"),
                       reader.ref("financial/sentence_checks.json", "hard_error_sentence_ids/sentence_count")),
        limitations=tuple(limitations),
        details={"per_section": rows, "skipped_sections": [r["section"] for r in rows if r["state"] != "CHECKED"]})


# --------------------------------------------------------------------------
# P03 / P04
# --------------------------------------------------------------------------
def p03(reader: RunReader) -> MetricResult:
    sec = load_sections(reader)["company"]
    manifest = sec.get("manifest")
    materials = (manifest or {}).get("materials") or []
    limitations = [
        "材料份数、页命中率都不能替代取得率。",
        "合同必需信息点的离线 Gold 尚未建立，因此本读数既不报百分比，也不把「有 65 份材料」写成取得率。",
    ]
    return pending(
        metric_id="P03", run_id=reader.run_id, scope="公司节三份上传 PDF 的必需信息点",
        measurement_state="BENCHMARK_PENDING", execution_state="DID_RUN",
        verdict="运行侧材料清单可读；业务取得率待 Gold",
        first_missing="离线 Gold（必需信息点清单及其合格等价证据组）尚未建立",
        missing_chain=("无 Gold 必需信息点清单", "无等价证据组定义", "无法判定哪一份材料覆盖了哪个信息点"),
        denominator=None,
        evidence_refs=(reader.ref("company/cited_input_manifest.json", "materials[]"),
                       reader.ref("company/cited_input_manifest.json", "presentation_routing.column_gaps")),
        limitations=tuple(limitations),
        details={"run_observable": {
            "materials_in_manifest": len(materials),
            "facts_in_manifest": len((manifest or {}).get("facts") or []),
            "pack_set_fingerprint": (manifest or {}).get("pack_set_fingerprint"),
        }})


def p04(reader: RunReader) -> MetricResult:
    num = den = 0
    rows: list[dict[str, Any]] = []
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        manifest = sec.get("manifest")
        if manifest is None:
            rows.append({"section": name, "state": "NOT_RUN_UPSTREAM"})
            continue
        entries = [("material", m) for m in manifest.get("materials") or []] + \
                  [("fact", f) for f in manifest.get("facts") or []]
        bad = []
        for kind, entry in entries:
            ok, why = _manifest_qualification(entry)
            if not ok:
                bad.append({"kind": kind, "citation_key": entry.get("citation_key"), "why": why})
        num += len(entries) - len(bad)
        den += len(entries)
        withheld = sec.get("withheld") or {}
        rows.append({
            "section": name, "state": "DID_RUN",
            "entries": len(entries), "with_typed_basis": len(entries) - len(bad),
            "unqualified": bad[:20],
            "withheld": {"reasons": withheld.get("reasons"),
                         "counts": withheld.get("by_reason"),
                         "sentences": len(withheld.get("sentences") or [])},
            "placement_by_disposition": (sec.get("placement") or {}).get("by_disposition"),
            "gap_counts": (sec.get("gaps") or {}).get("axes", {}).get("by_bin"),
            "not_applicable_policies": len((sec.get("gaps") or {}).get("not_applicable_policies") or []),
        })
    limitations = [
        "本读数只核「有没有 typed 决定与依据」，不判决定本身对不对；误放/误杀需 Gold。",
        "撤回值未进正文这一条由 P07 的 withheld 相关机械核对与逐句核对覆盖，本行只报去向计数。",
    ]
    if den == 0:
        return measured(
            metric_id="P04", run_id=reader.run_id, scope="本 run 事实/材料候选",
            numerator=0, denominator=0, unit="ratio",
            verdict="两节 manifest 都没有条目", measured_but_empty=True,
            evidence_refs=(reader.ref("company/cited_input_manifest.json", "$"),),
            limitations=tuple(limitations), details={"per_section": rows})
    return measured(
        metric_id="P04", run_id=reader.run_id, scope="本 run 事实/材料候选",
        numerator=num, denominator=den, unit="ratio",
        verdict=f"{num}/{den} 条候选带 typed 资格依据",
        evidence_refs=(reader.ref("company/cited_input_manifest.json", "materials[].content_qualification/facts[].payload_ref"),
                       reader.ref("financial/cited_input_manifest.json", "facts[].source_identity"),
                       reader.ref("company/withheld_candidates.json", "by_reason"),
                       reader.ref("company/fact_placement.json", "by_disposition")),
        limitations=tuple(limitations),
        pending_axes=({"axis": "决定本身是否正确（误放/误杀）", "state": "BENCHMARK_PENDING",
                       "reason": "需要 Gold 标注哪些候选本应合格/本应撤回"},),
        details={"per_section": rows})


# --------------------------------------------------------------------------
# B01：公司必需栏目实答（六轴分列）
# --------------------------------------------------------------------------
def b01(reader: RunReader) -> MetricResult:
    sec = load_sections(reader)["company"]
    routing = (sec.get("routing") or {}).get("column_gaps") or []
    manifest = sec.get("manifest")
    prose = sec.get("prose")
    checks = sec.get("checks")
    if not routing:
        return pending(
            metric_id="B01", run_id=reader.run_id, scope="company_business 适用必需栏目",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="FAILED",
            verdict="没有落盘的栏目—展示档声明",
            first_missing="company/presentation_routing.json（column_gaps）不在盘上或无条目",
            missing_chain=("栏目全集不可知", "无法逐栏目分列六轴", "无法给分母"),
            evidence_refs=(reader.ref("company/presentation_routing.json", "column_gaps[]"),),
            limitations=("栏目全集取自本 run 落盘的 routing 声明；权威 Contract 的适用性政策未在本读数里重解。",))
    hard = set((checks or {}).get("hard_error_sentence_ids") or [])
    materials: dict[str, int] = {}
    facts: dict[str, int] = {}
    for m in (manifest or {}).get("materials") or []:
        for a in m.get("aspect_ids") or []:
            materials[a] = materials.get(a, 0) + 1
    for f in (manifest or {}).get("facts") or []:
        for a in f.get("aspect_ids") or []:
            facts[a] = facts.get(a, 0) + 1
    written: dict[str, int] = {}
    clean: dict[str, int] = {}
    for _sub, para, sent in _sentences(prose):
        for a in para.get("aspect_ids") or []:
            written[a] = written.get(a, 0) + 1
            if sent.get("sentence_id") not in hard:
                clean[a] = clean.get(a, 0) + 1

    rows = []
    for col in routing:
        aspect = col.get("column")
        rows.append({
            "column": aspect,
            "contract_display_tier": col.get("contract_display_tier"),
            "requirement_text": col.get("contract_requirement_text"),
            "found_material": materials.get(aspect, 0),
            "has_qualified_fact": facts.get(aspect, 0),
            "sent_to_writer": "DID" if manifest is not None else "NOT_RUN_UPSTREAM",
            "written_sentences": written.get(aspect, 0),
            "mechanically_clean_sentences": clean.get(aspect, 0),
            "human_accepted": "HUMAN_PENDING",
        })
    required = [r for r in rows if r["contract_display_tier"] == "required_body"]
    limitations = [
        "「有段落」不等于「实质回答」；最后一轴必须人读或标注抽检，不得由模型代填。",
        "栏目全集取自 presentation_routing.column_gaps；其展示档来自本 run 的 Contract 派生声明。",
        "本行不报「实答率」：分子需要人读认可，分母虽已知，也不得用机械通过句数顶替。",
    ]
    return pending(
        metric_id="B01", run_id=reader.run_id, scope="company_business 适用必需栏目（required_body）",
        measurement_state="HUMAN_PENDING", execution_state="DID_RUN" if prose is not None else "PARTIAL",
        verdict=f"{len(required)} 个必需栏目已分列五轴，第六轴待人读",
        first_missing="人工（或标注抽检）对「在题、被来源支持的自然正文」的逐栏目认可",
        missing_chain=("无人工裁决记录", "无 Gold 要点标注", "不得用机械通过数顶替实答数"),
        denominator=len(required),
        evidence_refs=(reader.ref("company/presentation_routing.json", "column_gaps[].contract_display_tier"),
                       reader.ref("company/cited_input_manifest.json", "materials[].aspect_ids/facts[].aspect_ids"),
                       reader.ref("company/cited_prose.json", "draft.subsections[].paragraphs[].aspect_ids"),
                       reader.ref("company/sentence_checks.json", "hard_error_sentence_ids")),
        limitations=tuple(limitations),
        details={"columns": rows, "required_body": len(required),
                 "optional_body": len(rows) - len(required),
                 "run_observable": {
                     "columns_with_material": sum(1 for r in rows if r["found_material"]),
                     "columns_with_sentence": sum(1 for r in rows if r["written_sentences"]),
                     "columns_with_clean_sentence": sum(1 for r in rows if r["mechanically_clean_sentences"]),
                 }})


# --------------------------------------------------------------------------
# B03：数字与来源可信（机械轴可测，语义轴待人工）
# --------------------------------------------------------------------------
_NUMERIC_KINDS = {"numeric_surface", "numeric_qualification"}


def b03(reader: RunReader) -> MetricResult:
    num = den = 0
    rows: list[dict[str, Any]] = []
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        prose, checks = sec.get("prose"), sec.get("checks")
        if prose is None or checks is None:
            rows.append({"section": name, "state": "NOT_RUN_UPSTREAM"})
            continue
        numeric_sentences = {s["sentence_id"] for s in _all_sentences(prose) if s.get("numeric_tokens")}
        bad: dict[str, list[str]] = {}
        for rec in checks.get("records") or []:
            if rec.get("check_kind") in _NUMERIC_KINDS and rec.get("verdict") == "hard_error":
                bad.setdefault(rec.get("sentence_id"), []).append(rec.get("check_kind"))
        ok = [s for s in numeric_sentences if s not in bad]
        num += len(ok)
        den += len(numeric_sentences)
        withheld = sec.get("withheld") or {}
        rows.append({
            "section": name, "state": "DID_RUN",
            "numeric_sentences": len(numeric_sentences),
            "authorized": len(ok),
            "unauthorized": sorted(bad.items()),
            "withheld_counts": withheld.get("by_reason"),
            "withheld_rule": (withheld.get("rule") or "")[:160],
        })
    measured_rows = [r for r in rows if r["state"] == "DID_RUN"]
    limitations = [
        "机械合格不能证明全部语义真实；语义忠实仍需人读（B06/B02）。",
        "本读数不含未产出正文的节。",
    ]
    if not measured_rows:
        return pending(
            metric_id="B03", run_id=reader.run_id, scope="本 run 正文高风险数值",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="PARTIAL",
            verdict="没有可核的数字句",
            first_missing="没有一节同时落下 cited_prose.json 与 sentence_checks.json",
            missing_chain=("无正文或无机核报告", "无数字授权判读", "不能报 0 未授权"),
            evidence_refs=(reader.ref("company/sentence_checks.json", "$"),),
            limitations=tuple(limitations), details={"per_section": rows})
    return measured(
        metric_id="B03", run_id=reader.run_id, scope="本 run 正文高风险数值",
        numerator=num, denominator=den, unit="ratio",
        verdict=f"{num}/{den} 个含数字句的数值授权与期间/来源机械核对全通过",
        evidence_refs=(reader.ref("company/cited_prose.json", "draft.subsections[].paragraphs[].sentences[].numeric_tokens"),
                       reader.ref("company/sentence_checks.json", "records[].check_kind= numeric_*"),
                       reader.ref("financial/cited_prose.json", "draft.subsections[].paragraphs[].sentences[].numeric_tokens"),
                       reader.ref("financial/sentence_checks.json", "records[].check_kind= numeric_*"),
                       reader.ref("company/withheld_candidates.json", "by_reason")),
        limitations=tuple(limitations),
        pending_axes=({"axis": "每个值是否确为该句业务/指标/口径下的真实披露", "state": "HUMAN_PENDING",
                       "reason": "需要人读或 Gold；机械层只保证有合格事实授权"},),
        details={"per_section": rows})


# --------------------------------------------------------------------------
# B04：财务权威与分析
# --------------------------------------------------------------------------
def _a1_grid(reader: RunReader, facts: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    tables = reader.json("financial/cited_metric_tables.json") or {}
    cells_total = 0
    cells_ok = 0
    mismatches: list[dict[str, Any]] = []
    column_periods: dict[tuple[str, int], set[str]] = {}
    for table in tables.get("tables") or []:
        for row in table.get("rows") or []:
            keys = row.get("citation_keys") or []
            cells = row.get("cells") or []
            periods = row.get("period_texts") or []
            for idx, cell in enumerate(cells):
                if not str(cell).strip():
                    continue
                cells_total += 1
                key = keys[idx] if idx < len(keys) else None
                entry = facts.get(key) if key else None
                if entry is None:
                    mismatches.append({"table_id": table.get("table_id"), "row": row.get("label"),
                                       "column": idx, "cell": cell,
                                       "failure": f"引用键 {key} 不在财务 manifest 里"})
                    continue
                _kind, fact = entry
                if _cell_matches_fact(str(cell), fact.get("text") or ""):
                    cells_ok += 1
                else:
                    mismatches.append({"table_id": table.get("table_id"), "row": row.get("label"),
                                       "column": idx, "cell": cell, "fact_text": fact.get("text"),
                                       "failure": "格值与所引事实命题文本不一致"})
                if idx < len(periods):
                    column_periods.setdefault((table.get("table_id", ""), idx), set()).add(
                        str(fact.get("period")))
    inconsistent_columns = [{"table_id": t, "column": c, "periods": sorted(v)}
                            for (t, c), v in column_periods.items() if len(v) > 1]
    coverage = tables.get("coverage_counts") or {}
    state_hist: dict[str, int] = {}
    for c in tables.get("coverage") or []:
        state_hist[c.get("state", "<none>")] = state_hist.get(c.get("state", "<none>"), 0) + 1
    return {"cells_total": cells_total, "cells_matching_fact_text": cells_ok,
            "mismatches": mismatches[:40], "mismatch_total": len(mismatches),
            "column_period_inconsistent": inconsistent_columns,
            "coverage_counts": coverage, "coverage_state_hist": state_hist,
            "tables": len(tables.get("tables") or []),
            "refusals": len(tables.get("refusals") or []),
            "rule_version": tables.get("rule_version")}


def _glob_section_artifacts(reader: RunReader, section: str, pattern: str) -> list[str]:
    """列某节下匹配的文件名（正斜杠相对名）。不递归、不看别的 run。"""
    folder = reader.run_dir / section
    if not folder.is_dir():
        return []
    return sorted(f"financial/{p.name}" if section == "financial" else f"{section}/{p.name}"
                  for p in folder.glob(pattern))


def _a2_grid(reader: RunReader) -> dict[str, Any]:
    readings_total = readings_ok = 0
    mismatches: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for rel in _glob_section_artifacts(reader, "financial", "cited_balance_structure__*.json"):
        doc = reader.json(rel) or {}
        for item in doc.get("items") or []:
            gaps = item.get("gaps") or []
            item_rows.append({"artifact": rel, "label": item.get("label"),
                              "aspect_id": item.get("aspect_id"),
                              "readings": len(item.get("readings") or []),
                              "gap_count": len(gaps),
                              "gap_labels": [g.get("code") or g.get("label") for g in gaps]})
            for r in item.get("readings") or []:
                if r.get("kind") != "value":
                    continue
                readings_total += 1
                try:
                    actual = Decimal(str(r.get("value_text")))
                except (InvalidOperation, TypeError):
                    actual = None
                want = _cell_numbers(str(r.get("display")))
                if actual is None or not want:
                    mismatches.append({"fact_id": r.get("fact_id"), "display": r.get("display"),
                                       "failure": "格值或权威值不可解析"})
                    continue
                shown = Decimal(want[-1][0])
                unit = want[-1][1]
                factor = {"亿元": Decimal(10) ** 8, "万元": Decimal(10) ** 4,
                          "元": Decimal(1)}.get(unit)
                if factor is None:
                    mismatches.append({"fact_id": r.get("fact_id"), "display": r.get("display"),
                                       "failure": f"未知单位 {unit}"})
                    continue
                diff = abs(shown * factor - actual)
                if diff <= _UNIT_TOLERANCE.get(unit, Decimal("0.005")):
                    readings_ok += 1
                else:
                    mismatches.append({"fact_id": r.get("fact_id"), "display": r.get("display"),
                                       "value_text": r.get("value_text"),
                                       "failure": f"显示值与权威值差 {diff}"})
    return {"readings_total": readings_total, "readings_matching": readings_ok,
            "mismatches": mismatches[:40], "mismatch_total": len(mismatches),
            "items": item_rows}


def b04(reader: RunReader) -> MetricResult:
    fin_manifest = reader.json("financial/cited_input_manifest.json")
    if fin_manifest is None:
        return pending(
            metric_id="B04", run_id=reader.run_id, scope="财务节 A1 与 A2",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="NOT_RUN_UPSTREAM",
            verdict="财务节未产出",
            first_missing="financial/cited_input_manifest.json 不在盘上（本 run 财务节未运行或未落盘）",
            missing_chain=("财务节未产出", "无 A1 指标表与 A2 结构产物", "不能报格值一致率"),
            evidence_refs=(reader.ref("financial/cited_input_manifest.json", "$"),),
            limitations=("A2 零模型调用不等于零缺口。",))
    facts = _key_map(fin_manifest)
    a1 = _a1_grid(reader, facts)
    a2 = _a2_grid(reader)
    src_scope = None
    for rel in _glob_section_artifacts(reader, "financial", "cited_source_scope__*.json"):
        doc = reader.json(rel) or {}
        src_scope = {"artifact": rel, "items": len(doc.get("items") or []),
                     "aspects": len(doc.get("contract_topic_aspects") or []),
                     "model_calls_issued": doc.get("model_calls_issued"),
                     "producer_kind": doc.get("producer_kind")}
    grid_total = a1["cells_total"] + a2["readings_total"]
    grid_ok = a1["cells_matching_fact_text"] + a2["readings_matching"]
    limitations = [
        "格值与结构可机械复算；「变化原因说得对不对」属人读，不在本行。",
        "A1 的格值比对基准是本 run manifest 里该引用键的**命题文本**，不是财务库原始格；本读数不打开该库。",
        "A2 零模型调用不等于零缺口；项目级 typed 缺口另计。",
    ]
    return measured(
        metric_id="B04", run_id=reader.run_id, scope="财务节 A1 指标表与 A2 确定性结构",
        numerator=grid_ok, denominator=grid_total, unit="ratio",
        verdict=f"A1 {a1['cells_matching_fact_text']}/{a1['cells_total']} 格、A2 {a2['readings_matching']}/{a2['readings_total']} 读数与权威事实一致",
        evidence_refs=(
            reader.ref("financial/cited_metric_tables.json", "tables[].rows[].cells/citation_keys"),
            reader.ref("financial/cited_input_manifest.json", "facts[].text/period"),
            *[reader.ref(rel, "items[].readings[].value_text/display")
              for rel in _glob_section_artifacts(reader, "financial", "cited_balance_structure__*.json")],
            *[reader.ref(rel, "items[]/contract_topic_aspects")
              for rel in _glob_section_artifacts(reader, "financial", "cited_source_scope__*.json")]),
        limitations=tuple(limitations),
        pending_axes=({"axis": "财务分析质量（趋势/结构/主科目/变动/附注案例/偿债指标的解读）",
                       "state": "HUMAN_PENDING", "reason": "需要人读；有数字不能推出说得对"},),
        details={"a1": a1, "a2": a2, "source_scope": src_scope,
                 "note": "A2 的债权结构项由其权威 facts 确定性生成；A2 与 A1 分开记账，不相互补齐。"})


# --------------------------------------------------------------------------
# B05：原表展示与人工确认
# --------------------------------------------------------------------------
def b05(reader: RunReader) -> MetricResult:
    sec = load_sections(reader)["company"]
    display = sec.get("display")
    if display is None:
        return pending(
            metric_id="B05", run_id=reader.run_id, scope="公司节原 PDF 表格区域",
            measurement_state="NOT_RUN_UPSTREAM", execution_state="PARTIAL",
            verdict="没有原表展示记录",
            first_missing="company/source_table_display.json 不在盘上",
            missing_chain=("无展示记录", "无法列目标区域", "无法分母"),
            evidence_refs=(reader.ref("company/source_table_display.json", "$"),),
            limitations=("页面能显示不授权正文格值，程序不得代签。",))
    regions = display.get("regions") or []
    confirmed = display.get("human_confirmed_count") or 0
    return measured(
        metric_id="B05", run_id=reader.run_id, scope="公司节原 PDF 表格区域（只读来源展示）",
        numerator=confirmed, denominator=len(regions), unit="ratio",
        verdict=f"{confirmed}/{len(regions)} 个目标区域获人工确认；可显示 {display.get('displayable_count')} 个、缺陷 {display.get('defect_count')} 个",
        evidence_refs=(reader.ref("company/source_table_display.json", "regions[]/displayable_count/defect_count/human_confirmed_count"),
                       reader.ref("company/source_table_display.md", "§ 人工确认台账")),
        limitations=(
            "确认数是**真实人工确认**的落盘值，0 表示尚未有人确认，不是「程序判定不合格」。",
            "原 PDF 区域可展示不等于正式表格结构化合格，也不授权正文数字。",
            "正式 TS5 表格能力另轨，不因本行改变状态。",
        ),
        details={"displayable": display.get("displayable_count"), "defects": display.get("defect_count"),
                 "human_confirmed": confirmed, "policy_version": display.get("policy_version"),
                 "registrations": len(display.get("registrations") or []),
                 "region_keys": [{"document_id": r.get("document_id"), "page": r.get("page_number"),
                                  "title": r.get("title")} for r in regions][:20]})


# --------------------------------------------------------------------------
# B02 / B06 / B07 / B08 / B09 / B10 / B11 / B12 / B13 / P08
# --------------------------------------------------------------------------
def p08(reader: RunReader) -> MetricResult:
    ledger = reader.ledger() or {}
    calls = {a.get("call_id"): a for a in (ledger.get("call_budget") or {}).get("attempts") or []}
    rows: list[dict[str, Any]] = []
    num = den = 0
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        review = sec.get("review")
        report = sec.get("report")
        if review is None and report is None:
            rows.append({"section": name, "state": "NOT_RUN_UPSTREAM"})
            continue
        judgeable = len((review or {}).get("sentence_ids") or [])
        if review is None:
            rows.append({"section": name, "state": "NO_REVIEW_ARTIFACT",
                         "judgeable": (report or {}).get("sentence_count"),
                         "system_review_state": (report or {}).get("system_review_state"),
                         "review_failure": (report or {}).get("review_failure")})
            continue
        outcome = review.get("outcome")
        call = review.get("call") or {}
        cid = call.get("call_id")
        in_ledger = bool(cid) and cid in calls
        if outcome != "reviewed":
            rows.append({"section": name, "state": "REVIEW_FAILED",
                         "judgeable": judgeable,
                         "review_failure": review.get("review_failure") or (report or {}).get("review_failure"),
                         "call_in_ledger": in_ledger, "call_status": call.get("status"),
                         "system_review_state": (report or {}).get("system_review_state")})
            continue
        producer = review.get("review_producer_kind")
        # 离线回声（`offline_diagnostic_echo`）**不是**独立审阅：它没有任何独立语义判断，
        # 却能照常写出 `issues[]` 与 `sentence_ids[]`。若不在这里挡住，一份离线回声就能把
        # 「本 run 被独立审阅覆盖」刷成 100%——这正是本指标要防的冒充。
        if producer != INDEPENDENT_REVIEW_PRODUCER or not in_ledger:
            rows.append({"section": name, "state": "NOT_INDEPENDENT",
                         "judgeable": judgeable,
                         "producer": producer, "call_in_ledger": in_ledger,
                         "call_status": call.get("status"),
                         "system_review_state": (report or {}).get("system_review_state")})
            continue
        covered = len({i.get("sentence_id") for i in review.get("issues") or []})
        num += covered
        den += judgeable
        rows.append({
            "section": name, "state": "REVIEWED",
            "judgeable": judgeable, "covered": covered,
            "issues": len(review.get("issues") or []),
            "blocking": len(review.get("blocking_issue_ids") or []),
            "producer": review.get("review_producer_kind"),
            "semantic_counts": review.get("semantic_counts"),
            "call_in_ledger": in_ledger, "call_status": call.get("status"),
            "prompt_version": call.get("prompt_version"),
            "report_version": review.get("report_version"),
            "system_review_state": (report or {}).get("system_review_state"),
            "publishability": (report or {}).get("publishability"),
            "human_review_state": (report or {}).get("human_review_state"),
        })
    limitations = [
        "意见必须绑定本稿版本与本 run 的真实调用；本读数逐条核了 call_id 是否在本 run 账本里。",
        "只有 `review_producer_kind == independent_llm_review` 且其 call_id 在本 run 账本里的节才算独立审阅；离线回声不算。",
        "issues=[] 只表示本次未报问题，不表示逐项核实。",
        "预览、系统放行、人工接受互不推导。",
        "审阅覆盖不等于审阅正确；检出率与误报率见 B13。",
    ]
    reviewed = [r for r in rows if r["state"] == "REVIEWED"]
    failed = [r for r in rows if r["state"] in ("REVIEW_FAILED", "NO_REVIEW_ARTIFACT", "NOT_INDEPENDENT")]
    if not reviewed:
        echo = [r for r in rows if r["state"] == "NOT_INDEPENDENT"]
        return pending(
            metric_id="P08", run_id=reader.run_id, scope="本 run 各节独立审阅",
            measurement_state="FAILED_TO_MEASURE", execution_state="FAILED",
            verdict=("各节只有离线回声，没有真实独立审阅" if echo
                     else "没有一节形成有效审阅结果"),
            first_missing=("各节 review_issues.json 的 review_producer_kind 都是离线回声，"
                           "且/或 call_id 不在本 run 账本里" if echo
                           else "各节 review_issues.json 的 outcome 都不是 reviewed"),
            missing_chain=("审阅回复不是本 run 的真实独立调用", "覆盖数不可算", "不得报 0 覆盖"),
            evidence_refs=tuple(r for s in ("company", "financial")
                                for r in (reader.ref(f"{s}/review_issues.json", "outcome/review_failure"),)),
            limitations=tuple(limitations), details={"per_section": rows})
    return measured(
        metric_id="P08", run_id=reader.run_id, scope="本 run 各节独立审阅",
        numerator=num, denominator=den, unit="ratio",
        verdict=f"{num}/{den} 句被本 run 真实独立审阅覆盖；未形成独立审阅的节 {len(failed)} 个",
        evidence_refs=(reader.ref("company/review_issues.json", "issues[].sentence_id/call.call_id"),
                       reader.ref("financial/review_issues.json", "issues[].sentence_id/call.call_id"),
                       reader.ref("cited_call_ledger.json", "call_budget.attempts[].call_id"),
                       reader.ref("company/cited_report_version.json", "system_review_state/publishability"),
                       reader.ref("financial/cited_report_version.json", "system_review_state/publishability")),
        limitations=tuple(limitations),
        details={"per_section": rows,
                 "report_level_review": "NOT_IMPLEMENTED（M930-4 的报告级审阅本 run 未运行）"})


def b13(reader: RunReader) -> MetricResult:
    rows: list[dict[str, Any]] = []
    opinions = 0
    specific: list[dict[str, Any]] = []
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        review = sec.get("review")
        if review is None or review.get("outcome") != "reviewed":
            rows.append({"section": name, "state": "NOT_RUN_UPSTREAM",
                         "review_failure": (review or {}).get("review_failure")})
            continue
        if review.get("review_producer_kind") != INDEPENDENT_REVIEW_PRODUCER:
            # 离线回声的意见不进入「本 run 独立审阅意见」这条账；它既不证明检出，也不证明过度审阅。
            rows.append({"section": name, "state": "NOT_INDEPENDENT",
                         "producer": review.get("review_producer_kind"),
                         "issues": len(review.get("issues") or [])})
            continue
        issues = review.get("issues") or []
        counts: dict[str, int] = {}
        for i in issues:
            counts[i.get("category", "<none>")] = counts.get(i.get("category", "<none>"), 0) + 1
            if i.get("category") not in ("supported", None, "") or i.get("blocking"):
                specific.append({"section": name, "issue_id": i.get("issue_id"),
                                 "sentence_id": i.get("sentence_id"),
                                 "category": i.get("category"),
                                 "severity": i.get("severity"),
                                 "blocking": i.get("blocking"),
                                 "reason": (i.get("reason") or "")[:400]})
        opinions += len(issues)
        rows.append({"section": name, "state": "REVIEWED", "issues": len(issues),
                     "category_counts": counts,
                     "blocking": len(review.get("blocking_issue_ids") or []),
                     "producer": review.get("review_producer_kind"),
                     "hard_error_override": len(review.get("hard_error_override_sentence_ids") or [])})
    return pending(
        metric_id="B13", run_id=reader.run_id, scope="本 run 独立审阅意见",
        measurement_state="BENCHMARK_PENDING",
        execution_state="DID_RUN" if any(r["state"] == "REVIEWED" for r in rows) else "FAILED",
        verdict=f"已提出意见 {opinions} 条；精确率/过度审阅率/误阻断率无裁决样本",
        first_missing="正反两类盲测 Gold（确实有问题的样本 + 无问题/非阻断样本）及其独立裁决记录",
        missing_chain=("无缺陷召回 Gold", "无「无依据/重复/夸大」裁决", "无「本不该 blocking」裁决",
                       "三个比值都不得计算，也不得用「审阅通过」顶替"),
        denominator=opinions or None,
        evidence_refs=(reader.ref("company/review_issues.json", "issues[].category/severity/blocking/reason"),
                       reader.ref("financial/review_issues.json", "issues[].category/severity/blocking/reason")),
        limitations=("没有裁决样本时不得显示精确率、误阻断率或「审阅通过」；靠沉默换高精确率不算。",
                     "`issues=[]` 只表示本次未报问题。"),
        details={"per_section": rows, "opinions": opinions,
                 "non_supported_or_blocking": specific})


def b02(reader: RunReader) -> MetricResult:
    sec = load_sections(reader)["company"]
    prose = sec.get("prose")
    sentences = _all_sentences(prose)
    return pending(
        metric_id="B02", run_id=reader.run_id, scope="本样本已标注「应呈现」要点",
        measurement_state="BENCHMARK_PENDING",
        execution_state="DID_RUN" if prose is not None else "PARTIAL",
        verdict="要点标注尚未建立",
        first_missing="离线 Gold：本样本「应呈现」要点清单及其等价表述",
        missing_chain=("无要点标注", "无法逐点对账遗漏/重复", "不得用引用材料比例顶替"),
        evidence_refs=(reader.ref("company/cited_prose.json", "draft.subsections[].paragraphs[].sentences[].text"),
                       reader.ref("company/cited_input_manifest.json", "materials[]"),),
        limitations=("引用材料比例不是该分数。",),
        details={"run_observable": {"sentences_written": len(sentences),
                                    "materials_adopted": sum(
                                        1 for a in (prose or {}).get("adoptions") or []
                                        if a.get("disposition") == "adopted")}})


def b06(reader: RunReader) -> MetricResult:
    return pending(
        metric_id="B06", run_id=reader.run_id, scope="本 run 报告（公司节 + 财务节）",
        measurement_state="HUMAN_PENDING", execution_state="DID_RUN",
        verdict="需要版本化人读表与评阅人",
        first_missing="未进行的人工评阅（回答问题/重点/结构/客观中性/期间口径/缺口诚实/信用相关性 各 0–4）",
        missing_chain=("无评阅人", "无逐项理由", "不得由模型代填"),
        evidence_refs=(reader.ref("company/cited_preview.md", "预览正文"),
                       reader.ref("financial/cited_preview.md", "预览正文"),),
        limitations=("单份演示不宣称跨公司泛化；4 分不等于系统放行、人工接受或正式阶段关闭。",),
        details={"rubric_items": ["回答问题", "重点", "结构", "客观中性", "期间口径", "缺口诚实", "信用相关性"],
                 "scale": "0–4（分项，无总分）"})


def b07(reader: RunReader) -> MetricResult:
    matrix = reader.json("table_proof_matrix.json") or {}
    docs = matrix.get("documents") or []
    declared = sum(d.get("declared_table_count") or 0 for d in docs)
    released = sum(d.get("released_table_count") or 0 for d in docs)
    refused = sum(d.get("refused_table_count") or 0 for d in docs)
    balanced = all(d.get("accounting_balanced") for d in docs) if docs else None
    from collections import Counter
    reasons: Counter = Counter()
    defects: Counter = Counter()
    qualified = 0
    total_tables = 0
    for d in docs:
        for t in d.get("tables") or []:
            total_tables += 1
            reasons[t.get("primary_reason")] += 1
            if t.get("reading_qualified"):
                qualified += 1
            for c in t.get("defect_codes") or []:
                defects[c] += 1
    display = load_sections(reader)["company"].get("display") or {}
    return pending(
        metric_id="B07", run_id=reader.run_id, scope="公司节三份上传 PDF 的结构关系",
        measurement_state="BENCHMARK_PENDING",
        execution_state="DID_RUN" if docs else "NOT_RUN_UPSTREAM",
        verdict=f"结构侧读数：{released}/{declared} 张表获签发，0 张达到可读材料资格",
        first_missing="逐关系的人工 Gold（标题—正文、表格行列—单位/脚注/续表、期间与来源角色）",
        missing_chain=("表格结构层未签发任何一张表（全部 structure_not_complete）",
                       "无 Gold 关系标注", "不得把区域展示数当结构保真率"),
        evidence_refs=(reader.ref("table_proof_matrix.json", "documents[].declared_table_count/released_table_count/tables[].primary_reason"),
                       reader.ref("company/source_table_display.json", "displayable_count/defect_count"),),
        limitations=("原 PDF 区域可展示不等于正式表格结构化合格，也不授权正文数字。",
                     "本行只报结构侧过程读数，不给业务保真率。"),
        details={"run_observable": {
            "documents": len(docs), "declared_tables": declared, "released_tables": released,
            "refused_tables": refused, "reading_qualified_tables": qualified,
            "accounting_balanced": balanced,
            "primary_reasons": dict(reasons), "defect_codes": dict(defects),
            "proof_rule_version": matrix.get("rule_version"),
            "displayable_regions": display.get("displayable_count"),
            "display_defects": display.get("defect_count"),
        }})


def b08(reader: RunReader) -> MetricResult:
    manifest = reader.json("company/cited_input_manifest.json") or {}
    routing = manifest.get("presentation_routing") or {}
    return pending(
        metric_id="B08", run_id=reader.run_id, scope="公司节树/图导航",
        measurement_state="BENCHMARK_PENDING", execution_state="PARTIAL",
        verdict="本切片没有导航 trace 落盘；正确率另需 Gold",
        first_missing="本 run 未落盘树/图导航 trace（沿父子、续表、表注、显式引用边的实际导航路径）",
        missing_chain=("导航 trace 未落盘", "无 Gold 必需信息点与允许路径", "不得把节点/边数当可达率",
                       "不得把 0 条错误边读成「没有错关系」"),
        evidence_refs=(reader.ref("company/cited_input_manifest.json", "presentation_routing"),
                       reader.ref("table_proof_matrix.json", "documents[].tables[].structure_state"),),
        limitations=("建出节点/边不等于可达正确证据；无图边的演示切片标不适用，不伪报零错误。",),
        details={"run_observable": {
            "route_topics": routing.get("route_topics"),
            "routes": len(routing.get("routes") or []),
            "unrouted_facts": len(routing.get("unrouted_facts") or []),
            "routing_version": routing.get("routing_version"),
            "note": "这是呈现层栏目路由，不是树/图导航 trace。",
        }})


def b09(reader: RunReader) -> MetricResult:
    return pending(
        metric_id="B09", run_id=reader.run_id, scope="本 run 研究侧 InformationNeed",
        measurement_state="BENCHMARK_PENDING", execution_state="PARTIAL",
        verdict="需要—决定未逐条落盘；合法率另需 Gold 允许路径",
        first_missing="逐条 InformationNeed 与 Router 决定的落盘（本 run 只落了离线研究结果与其下游产物）",
        missing_chain=("需求—决定未落盘", "无 SourcePolicy 允许集合的 Gold", "不得由「跑通了」推出路由正确"),
        evidence_refs=(reader.ref("table_proof_matrix.json", "livelihood_note"),
                       reader.ref("run_progress.jsonl", "stage=environment_built"),),
        limitations=("允许多个正确路径，不强制唯一答案；本行不报路由正确率。",),
        details={})


def b10(reader: RunReader) -> MetricResult:
    return pending(
        metric_id="B10", run_id=reader.run_id, scope="本 run 检索预算内",
        measurement_state="BENCHMARK_PENDING", execution_state="PARTIAL",
        verdict="查询/候选/排序 trace 未落盘；召回与精确另需 Gold",
        first_missing="逐需求的查询、候选、排序与实际读取 span 的落盘",
        missing_chain=("检索 trace 未落盘", "无 Gold 等价证据组", "页码命中不算片段命中",
                       "不得把材料份数当召回率"),
        evidence_refs=(reader.ref("company/cited_input_manifest.json", "materials[].locator_ref/pack_id"),
                       reader.ref("company/cited_input_manifest.json", "presentation_routing.column_gaps"),),
        limitations=("命中正确页而未命中正确业务片段不算取得。",),
        details={"run_observable": {
            "materials_in_company_pack": len((reader.json("company/cited_input_manifest.json") or {}).get("materials") or []),
            "column_gaps": len((reader.json("company/presentation_routing.json") or {}).get("column_gaps") or []),
        }})


def b11(reader: RunReader) -> MetricResult:
    events = _journal(reader)
    return pending(
        metric_id="B11", run_id=reader.run_id, scope="本 run Harness 调度",
        measurement_state="BENCHMARK_PENDING", execution_state="PARTIAL",
        verdict=f"阶段日志 {len(events)} 条；停止决策的对错另需 Gold 预期终态",
        first_missing="逐需求的 Gold 预期终态（应继续/应停止/应留 gap）",
        missing_chain=("无 Gold 预期终态", "无 FollowUpNeed 落盘（本 run 直接给了终局产物）",
                       "只有动作日志不能证明停止决策正确"),
        evidence_refs=(reader.ref("run_progress.jsonl", "stage/status/at_utc"),
                       reader.ref("company/cited_prose.json", "draft.follow_up_needs"),),
        limitations=("本行不把「跑完 21 个阶段」读成「调度正确」。",),
        details={"run_observable": {
            "stages": [e.get("stage") for e in events],
            "follow_up_needs_company": len(((reader.json("company/cited_prose.json") or {}).get("draft") or {}).get("follow_up_needs") or []),
            "follow_up_needs_financial": len(((reader.json("financial/cited_prose.json") or {}).get("draft") or {}).get("follow_up_needs") or []),
        }})


def b12(reader: RunReader) -> MetricResult:
    rows = []
    for name in SECTIONS:
        sec = load_sections(reader)[name]
        gaps = sec.get("gaps") or {}
        manifest = sec.get("manifest") or {}
        rows.append({
            "section": name,
            "contract_fingerprint": gaps.get("contract_fingerprint"),
            "gap_bins": (gaps.get("axes") or {}).get("by_bin"),
            "required_gap_count": gaps.get("required_gap_count"),
            "release_blocking_gap_count": gaps.get("axes", {}).get("release_blocking_gap_count"),
            "not_applicable": (gaps.get("axes") or {}).get("by_bin", {}).get("not_applicable"),
            "materials": len(manifest.get("materials") or []),
            "facts": len(manifest.get("facts") or []),
            "gap_ids": [g.get("gap_id") for g in gaps.get("gaps") or []],
            "gap_aspects": [{"aspect": g.get("contract_aspect_id"), "bin": g.get("gap_bin"),
                             "display_tier": g.get("display_tier"),
                             "requirement": g.get("requirement_text")} for g in gaps.get("gaps") or []],
        })
    return pending(
        metric_id="B12", run_id=reader.run_id, scope="company_business 适用必需 aspect",
        measurement_state="BENCHMARK_PENDING", execution_state="DID_RUN",
        verdict="Pack 清单与 typed 缺口可读；「素材是否充分」需逐栏目 Gold 裁决",
        first_missing="按 Gold 允许的多种充分证据组合做的逐栏目裁决",
        missing_chain=("无 Gold 充分性标准", "材料数/全文投递数不等于充分",
                       "缺口诚实率要另算，且不计入充分分子"),
        evidence_refs=(reader.ref("company/cited_input_manifest.json", "materials[]/facts[]"),
                       reader.ref("company/cited_gap_bins.json", "axes/gaps[]"),
                       reader.ref("financial/cited_gap_bins.json", "axes/gaps[]"),),
        limitations=("缺口诚实率不计入充分分子，也不得反过来冒充充分。",),
        details={"per_section": rows})


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------
MEASURES: dict[str, Callable[[RunReader], MetricResult]] = {
    "D01": d01, "D02": d02, "D03": d03,
    "P01": p01, "P02": p02, "P03": p03, "P04": p04, "P05": p05, "P06": p06, "P07": p07, "P08": p08,
    "B01": b01, "B02": b02, "B03": b03, "B04": b04, "B05": b05, "B06": b06, "B07": b07,
    "B08": b08, "B09": b09, "B10": b10, "B11": b11, "B12": b12, "B13": b13,
}


def assert_all_metrics_covered() -> tuple[str, ...]:
    """24 个指标必须**每个**都有读数函数；少一个就当场抛，不允许静默跳过。"""
    from .catalog import METRICS
    missing = [m.metric_id for m in METRICS if m.metric_id not in MEASURES]
    extra = [k for k in MEASURES if k not in {m.metric_id for m in METRICS}]
    if missing or extra:
        raise ValueError(f"指标覆盖不全：缺 {missing}，多 {extra}")
    return tuple(m.metric_id for m in METRICS)


def evaluate_run(reader: RunReader) -> list[MetricResult]:
    """按目录顺序跑完 24 个指标。**任何**指标抛异常都在这里变成一次失败读数，不吞掉。"""
    out: list[MetricResult] = []
    for metric_id in assert_all_metrics_covered():
        try:
            out.append(MEASURES[metric_id](reader))
        except Exception as exc:  # noqa: BLE001 —— 评测不许因为一条读数崩掉整份交付
            out.append(pending(
                metric_id=metric_id, run_id=reader.run_id, scope="（读数过程抛错）",
                measurement_state="FAILED_TO_MEASURE", execution_state="FAILED",
                verdict=f"读数函数抛错：{type(exc).__name__}",
                first_missing=f"{type(exc).__name__}: {exc}",
                missing_chain=("读数实现异常", "该指标本次无结果"),
                evidence_refs=()))
    return out


__all__ = ["SECTIONS", "Sec", "load_sections", "MEASURES", "evaluate_run",
           "assert_all_metrics_covered"]

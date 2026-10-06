"""M930-3 §八 #16 / §16.7.2：**真实权威输入**上的 current 写作主链与它的真实边界。

用法: python -m evals.test_demo_writer_formal_chain

链（§0.13 的唯一顺序）：真实 M930-2 authority input → `company_worker.run_backbone_writer_phase`
（唯一 Writer → 候选束 → P8 aggregate gate → P9 factual entailment → P10 accepted binding →
门后定稿 Claim / final Narrative / FND successor / current Result）→ `report_assembler` 组装。

本模块只做两件事，且都要在**真实输入**上做：

1. **贯通与守恒**：真实 Pack / 财务 artifact 上的事实目录、期间排除与缺口必须逐条守恒、
   可回查、无静默丢弃；写作相位的 P4 复算必须在真实临时 store 上可算。
2. **边界如实上报**：current 链在真实 DemoScope 上**走不到组装**——公司/行业两节的权威事实
   在冻结 Contract 的期间规则下**没有一条可成为候选**（那是权威侧的期间缺口，不是组织计划
   的缺陷），本节正文因此只有「标题 + 缺口附录」（`NS.has_registered_gaps`：缺口已如实登记
   时空正文合法，状态 COMPLETED_WITH_GAPS；**零 Claim 且没登记缺口**仍然 fail-closed）；
   财务节被写作相位的两道守卫挡在门外（section 级 `research_policy` 与
   `producer_kind`），而真实 DemoScope 的三段 scope 又必须与冻结投影集合守恒。本文件把这些
   如实钉成断言与 NOTE，**不**用加宽门、补默认值、自造相位或缩窄 scope 来换取「贯通」。

与 `test_demo_pack_writer` / `test_demo_report_assembler` 的分工：
    - 那两个模块是**字段形状替身上的反例集**（把上游改坏一点点，必须被拒），其中
      `test_demo_report_assembler` 用合成权威覆盖「候选→决定→accepted binding→Claim」正向；
    - 本模块**不复刻**它们的反例，只在真实输入上钉住「真实输入会在哪里 fail-closed、为什么」，
      并补齐 §16.10 #36：legacy 生产者（P25–P32）的旧 Claim/Result **不得进入 current 链**。

真实输入的来源**只能**是公开入口（与 `test_demo_topic_runtime` 同一路径）：冻结 Contract v2 →
DemoScope 投影 → 公司/行业 section Worker 的 backbone phase → `SqlitePackSetStore` 读回的
Pack；财务侧取自 `financial_worker.run_backbone_financial_phase`（只读 `data/financial_v2.db`）。

不调真实 LLM、不联网：叙事替身是 `_StubLlm`（只按脚本回门前提案束，不写任何事实文本），
语义门替身是 `_StubEntailmentClient`；Pack 只提交到**临时** SQLite，真实库只读；缺真实样本
时如实 skip。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.test_demo_report_assembler import (  # noqa: E402
    _ENTAILED, _StubEntailmentClient, _StubLlm, _cand, _context_edge, _fact_edge, _plan,
    _stub_final_sentence_client, _unit)
from evals.test_demo_topic_runtime import (  # noqa: E402
    _NeedBuilder, _sample_pdf, _Sink, _SpanRecallResearch, _route_context, _route_fn)
from contracts.loader_v2 import load_contract_v2  # noqa: E402
from document_structure import live_span_source as LSS  # noqa: E402
from document_structure import navigation as NAV  # noqa: E402
from document_structure import synopsis as SY  # noqa: E402
from evaluation import run_section_eval as R  # noqa: E402
from harness import r2_dependencies as R2  # noqa: E402
from harness import source_policy_resolver as SPR  # noqa: E402
from harness import topic_runtime as TR  # noqa: E402
from harness import topic_schema as TSCH  # noqa: E402
from harness.schema import CitationRef  # noqa: E402
from planning import demo_scope as SC  # noqa: E402
from planning import schema as PS  # noqa: E402
from scripts import run_phase4_demo as RPD  # noqa: E402
from sections import company_worker as CW  # noqa: E402
from sections import financial_pack_artifact as FPA  # noqa: E402
from sections import financial_worker as FW  # noqa: E402
from sections import industry_worker as IW  # noqa: E402
from sections import material_context as MCW  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402
from sections import pack_set as PSet  # noqa: E402
from sections import pack_writer as PW  # noqa: E402
from sections import presentation_profile as PP  # noqa: E402
from sections import publishable_report as PR  # noqa: E402
from sections import report_assembler as RA  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import store as ST  # noqa: E402
from sections import writing_spec as WS  # noqa: E402
from tools import adapters as A  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
STARTED_AT = "2026-09-20T00:00:00Z"
FIN_DB = REPO / "data/financial_v2.db"

#: 缺口正文里不得出现的「无证据断定不存在」措辞（§四 4：不得把「未取得」写成「不存在」）。
FORBIDDEN_ABSENCE_WORDING = ("不存在", "未披露", "没有披露", "未提及")

#: §六 期间排除的封闭原因码（`PW.scan_topic_pack` 只写这三个值；不含公司/文档专用规则）。
_PERIOD_EXCLUSION_REASONS = ("explicit_period_required", "vague_period_in_fact_text",
                             PW.PERIOD_UNRESOLVED_REASON)

#: context 草稿单元的背景句（**不含任何事实、数字或结论**；只说明本节的结构与背景）。
_CONTEXT_UNIT_TEXT = "本节就权威已取得的事实与未取得的缺口作背景与结构说明。"


def _draft_without_registered_gaps(draft):
    """同一候选束、**抹掉缺口登记**的 draft（内容身份随之重算）。

    它仍然是一条合法 wire（候选/单元/proposal 在场，所以 `SectionDraft` 的「空 draft 不得
    静默通过」不适用），因此能被送到定稿构造器与提交前封口检查面前——反例测的正是这两处判据
    本身：「缺口字段曾经非空」不是放行理由，判据是 draft 此刻**是否如实登记**了缺口。
    """
    payload = {k: v for k, v in draft.to_dict().items()
               if k not in ("draft_id", "schema_version")}
    payload["unresolved_ids"] = ()
    payload["unresolved_projections"] = []
    return NS.SectionDraft.create(**payload)


def _financial_dims(db: Path) -> dict | None:
    """只读地从财务库自己的 `current_snapshot` 指针读维度（不猜公司、不猜基准期）。"""
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT company_id, scope, currency, as_of_date, purpose, snapshot_id "
            "FROM current_snapshot ORDER BY company_id, as_of_date, scope LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return {"company_id": row[0], "scope": row[1], "currency": row[2],
            "as_of_date": row[3], "purpose": row[4], "snapshot_id": row[5]}


def _fact_topic_map(artifact, task) -> tuple[tuple[str, str], ...]:
    """artifact fact → topic 的组合根声明（与 §九 预览探针同一规则，显式可审计）。

    M930-5 才拥有正式组合根；本批由调用方显式声明。规则：指标事实按冻结注册表
    `FW._FORMULA_TO_TOPIC` 取真实主题，命中本节主题才归本节；原始报表项与其他主题的指标
    一律声明 `OUTSIDE_SECTION_TOPIC`——绝不把「本节之外」改写成某个本节主题。

    返回**规范形**（`(fact_id, topic_id)` 去重且升序）：`FinancialAuthorityInput` 的身份体
    要求 fact→topic 归属唯一且可复现。同一条 fact_id 被声明到两个不同 topic 是歧义声明，
    直接 fail-closed——不得靠排序去重悄悄丢掉其中一条。
    """
    topics = set(task.topic_ids)
    declared: dict[str, str] = {}

    def _declare(fact_id: str, code: str) -> None:
        fact_id = str(fact_id)
        topic = FW._FORMULA_TO_TOPIC.get(str(code or ""))
        topic = topic if topic in topics else PW.OUTSIDE_SECTION_TOPIC
        previous = declared.get(fact_id)
        if previous is not None and previous != topic:
            raise AssertionError(
                f"测试 fact_topic_map 把 {fact_id!r} 同时归到 {previous!r} 与 {topic!r}："
                "归属歧义，不得用排序掩盖")
        declared[fact_id] = topic

    for fact in artifact.facts:
        _declare(fact.fact_id, getattr(fact, "code", ""))
    for gap in getattr(artifact, "gaps", ()) or ():
        _declare(str(gap.get("fact_id") or ""), str(gap.get("formula_id") or ""))
    return tuple(sorted(declared.items()))


def _plan_from_catalog(scan) -> dict:
    """把**真实权威读视图**里的事实提为门前提案束（§十：组织计划不含任何事实文本）。

    候选文本逐字取自权威事实（模型只能「选择」权威目录里的行，不能自报身份或新写事实）；
    context 草稿单元只挂真实存在的 material。真实输入上目录为空时，本函数返回的就是
    `_plan()`（零候选、零单元）——那正是「模型在这份权威上无可组织的候选」的忠实表达，
    不是组织计划写坏了。

    **草稿层的出处轴按本节的真实材料面选**（`pw-16` / `pprov-1`），不由夹具偏好选：事实带
    `material_id`（公司/行业这类 Pack 材料边界内的节）走材料轴；本节事实全部不带
    `material_id`（纯 `FinancialFactPack` 的财务节，精确材料清单**合法**为空）走事实轴。
    事实轴的逐候选别名就是该事实在 `scan.facts` 里的位次（`f1..fN`）——候选正是按那个顺序
    建的，因此不必猜。材料轴统一用 `m1`：它只声明「这一段文字从哪一行材料来」，与候选的
    fact 支撑边无关，逐候选去反解 manifest 位次反而是在猜。
    """
    candidates = [_cand(f"c-{e.fact_id}", str(e.text), _fact_edge(scan, str(e.fact_id)))
                  for e in scan.facts]
    containers = sorted({str(e.container_identity) for e in scan.facts})
    materials = sorted({str(e.material_id) for e in scan.facts if e.material_id})
    units = ([_unit("u-1", _CONTEXT_UNIT_TEXT,
                    context=[_context_edge(containers[0], materials[0])])]
             if containers and materials else [])
    if materials:
        return _plan(candidates=candidates, units=units)
    return _plan(candidates=candidates, units=units, draft_axis="fact",
                 draft_ref=[f"f{i + 1}" for i in range(len(candidates))])


class _ForeignProducerAuthority:
    """合成反例：`producer_kind` 非 topic_harness 的**同形**权威（只用于隔离第二道守卫）。

    它不是任何真实入口的产物，也不被任何生产路径接受；这里只用它把「research_policy 守卫」
    与「producer_kind 守卫」分开测量——真实财务节会先被前者挡住，第二道守卫就测不到。
    """

    producer_kind = "financial_workflow"

    def __init__(self, task) -> None:
        self.task_id = task.task_id
        self.section_id = task.section_id


#: 明确的年 / 年月 / 日期 / 年度（**不是**「报告期内」「目前」「近年来」这类含糊措辞）。
_EXPLICIT_PERIOD_RE = re.compile(
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?"
    r"|\d{4}\s*[-/]\s*\d{1,2}(?:\s*[-/]\s*\d{1,2})?"
    r"|\d{4}\s*年度")


def _explicit_periods(text: str) -> tuple[str, ...]:
    """正文里**逐字存在**的明确期间 token（规范化后去重升序；不做任何推断或补全）。"""
    out = set()
    for match in _EXPLICIT_PERIOD_RE.findall(str(text or "")):
        out.add(re.sub(r"\s+", "", match).replace("/", "-"))
    return tuple(sorted(out))


def _evidence_report_periods(db: Path, evidence_ids: set[str]) -> dict[str, dict]:
    """只读地查 Evidence 库里被引用块的 `report_period` / `evidence_type`（不猜、不建库）。

    额外记下**父块正文里**逐字存在的明确期间：它把「整份来源本来就没有日期」与「日期只在父块里、
    并不在被 inspect 的那段 material 正文里」区分开——两者都判未决，但缺口不同，报告必须分清。
    """
    if not evidence_ids or not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        out: dict[str, dict] = {}
        ids = sorted(evidence_ids)
        for start in range(0, len(ids), 200):
            chunk = ids[start:start + 200]
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(
                    f"SELECT evidence_id, report_period, evidence_type, length(text), text "
                    f"FROM evidence_blocks WHERE evidence_id IN ({marks})", chunk):
                out[str(row[0])] = {"report_period": row[1], "evidence_type": row[2],
                                    "block_text_len": row[3],
                                    "parent_block_explicit_periods": list(
                                        _explicit_periods(row[4]))}
        return out
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _period_diagnosis(*, authority, task, spec, resolver, evidence_db: Path) -> dict:
    """§四 只读诊断表：真实事实的**期间来源**逐条摊开，回答「为什么被排除」。

    判据只用既有 typed 字段（`SupportedFact.period` / `ValueIdentity.period` /
    `CitationRef.period`）、冻结投影的 Contract `kind` 与 WritingSpec `period_language_policy`，
    以及被引用 material 的完整正文——不按公司名、页码、文档名或测试样本写规则。

    对每条事实给出一个 `verdict`（**只看两侧文本本身**，不预设结论）：
      * `typed_period_present`        —— 事实自带显式期间（规则 1：直接保留并验证）；
      * `extractable_from_both_sides` —— Claim 与被引 material 正文**同时**出现同一个明确期间
                                        （规则 2/3：唯一允许的提取路径）；
      * `date_only_in_source` / `date_only_in_claim` —— 只有一侧出现（规则 3 要求两侧都在）；
      * `no_material_bound`           —— 引用在本容器内没有可用的 material；
      * `no_explicit_date_anywhere`   —— 两侧都没有明确日期（规则 4：含糊措辞不算期间）。

    合法 material 就是**被 inspect 的 OutlineSpan 本身**（正式消费单位），因此 `whole_block=False`
    的 span 不是「片段」，不构成排除理由：`whole_block` / `is_fallback` 只作为注解随行报出，
    不参与裁决。真正的片段语义由 `parent_block_explicit_periods` 与 `source_text` 对照体现——
    日期只落在未被 inspect 的父块里时，仍判未决（规则 5），但报告会指出它落在哪儿。
    """
    scan = PW.scan_topic_pack(authority, task)
    meta = PW._pack_aspect_meta(authority.pack_set)
    exclusion = {str(i.get("fact_id")): i for i in scan.period_exclusions}
    policy_of_aspect = {str(m.get("aspect_id")): str(m.get("period_language_policy") or "")
                        for m in (getattr(spec, "mappings", ()) or ())}
    cited_ids = {str(ref.evidence_id) for pack in authority.pack_set.packs
                 for fact in pack.facts for ref in (fact.citation_refs or ())
                 if getattr(ref, "ref_type", "") == "evidence" and ref.evidence_id}
    block_info = _evidence_report_periods(evidence_db, cited_ids)

    rows: dict[str, dict] = {}
    for pack in authority.pack_set.packs:
        by_identity: dict[str, list] = {}
        for material in pack.materials:
            by_identity.setdefault(str(material.source_identity), []).append(material)
        for fact in pack.facts:
            fact_id = str(fact.fact_id)
            fact_text = str(getattr(fact, "text", "") or "")
            aspect_ids = tuple(str(a) for a in (getattr(fact, "aspect_ids", ()) or ()))
            citations, sources = [], []
            for ref in (fact.citation_refs or ()):
                citations.append({"ref_type": str(getattr(ref, "ref_type", "")),
                                  "evidence_id": getattr(ref, "evidence_id", None),
                                  "page_number": getattr(ref, "page_number", None),
                                  "period": getattr(ref, "period", None)})
                if str(getattr(ref, "ref_type", "")) != "evidence":
                    continue
                identity = TSCH.citation_source_identity(ref)
                for material in by_identity.get(identity, ()):
                    payload = resolver.resolve(material.payload_ref)
                    envelope = (json.loads(payload.payload_bytes.decode("utf-8"))
                                if payload is not None else {})
                    tree = envelope.get("tree_material") or {}
                    content = envelope.get("content") or {}
                    text = str(content.get("text") or "")
                    sources.append({
                        "material_id": str(material.material_id),
                        "page": getattr(material.locator, "page", None),
                        "whole_block": tree.get("whole_block"),
                        "is_fallback": (tree.get("provenance") or {}).get("is_fallback"),
                        "parent_evidence_type": (tree.get("parent_evidence") or {}).get(
                            "evidence_type") or content.get("evidence_type"),
                        "text_len": len(text),
                        "source_text": text,
                        "evidence": block_info.get(str(ref.evidence_id or ""), {}),
                        "explicit_periods_in_source": _explicit_periods(text)})
            claim_periods = _explicit_periods(fact_text)
            declared = str(getattr(fact, "period", "") or "").strip()
            if not declared:
                declared = str(getattr(getattr(fact, "value_identity", None), "period", "")
                               or "").strip()
            source_periods = {p for s in sources for p in s["explicit_periods_in_source"]}
            common = sorted(set(claim_periods) & source_periods)
            if declared:
                verdict = "typed_period_present"
            elif not sources:
                verdict = "no_material_bound"
            elif common:
                verdict = "extractable_from_both_sides"
            elif claim_periods and not source_periods:
                verdict = "date_only_in_claim"
            elif source_periods and not claim_periods:
                verdict = "date_only_in_source"
            else:
                verdict = "no_explicit_date_anywhere"
            rows[fact_id] = {
                "topic_id": str(pack.topic_id),
                "aspect_ids": list(aspect_ids),
                "fact_text": fact_text,
                "fact_type": str(getattr(fact, "fact_type", "") or ""),
                "contract_kinds": sorted({str(meta.get(a, {}).get("kind", ""))
                                          for a in aspect_ids}),
                "time_scopes": sorted({str(meta.get(a, {}).get("time_scope", ""))
                                       for a in aspect_ids}),
                "writing_spec_period_policy": sorted({policy_of_aspect.get(a, "")
                                                      for a in aspect_ids}),
                "fact_period": getattr(fact, "period", None),
                "value_identity_period": getattr(getattr(fact, "value_identity", None),
                                                 "period", None),
                "citation_refs": citations,
                "sources": sources,
                "claim_explicit_periods": list(claim_periods),
                "common_explicit_periods": common,
                "exclusion": exclusion.get(fact_id),
                "verdict": verdict,
            }
    return dict(sorted(rows.items()))


def _absence_trace(period_diagnosis: dict) -> tuple[list, list]:
    """§四 缺席措辞对账：事实文本里的每个缺席措辞，逐条找出**写出同一措辞的材料成员**。

    返回 `(全部命中, 无出处命中)`。判据只看两侧文本本身——**事实原文可以自己就写着
    「不存在」**（年报原文如此），缺陷在另一头：材料正文里没有、事实文本里却有，即抽取
    把原文没有的缺席表述写进了权威事实。
    """
    hits = [(str(fid), w, str(row.get("fact_text") or "")[:100],
             [str(s.get("material_id")) for s in (row.get("sources") or ())
              if w in str(s.get("source_text") or "")])
            for fid, row in period_diagnosis.items()
            for w in FORBIDDEN_ABSENCE_WORDING if w in str(row.get("fact_text") or "")]
    return hits, [hit for hit in hits if not hit[3]]


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    def fails(fn, exc, needle: str, msg: str) -> str:
        """断言 fail-closed：异常类型 + 诊断关键词；返回异常文本供进一步断言。"""
        nonlocal passed, failed
        try:
            fn()
        except exc as e:  # noqa: PERF203
            text = str(e)
            if needle and needle not in text:
                failed += 1
                details.append(f"FAIL {msg}：期望诊断含 {needle!r}，实际 {text[:160]!r}")
            else:
                passed += 1
            return text
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:140]}）")
            return ""
        failed += 1
        details.append(f"FAIL {msg}：未拒绝")
        return ""

    pdf = _sample_pdf()
    evidence_db = REPO / "data/evidence.db"
    if pdf is None or not evidence_db.exists():
        skipped += 1
        details.append("SKIP 缺 live 样本或 data/evidence.db")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}
    dims = _financial_dims(FIN_DB)
    company = pdf.parents[1].name
    if not dims or dims["company_id"] != company:
        skipped += 1
        details.append(
            f"SKIP 财务库 current_snapshot 与样本公司不一致或不可用"
            f"（样本 {company!r}，财务库 {dims['company_id'] if dims else None!r}）；"
            "本报告只写一家公司，禁止用别的公司口径顶替")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    from evidence import store as estore
    estore._db_path = evidence_db.resolve()
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc_version = "sha256-" + sha[:16]
    set_version = estore.current_evidence_set_ro(
        evidence_db.resolve(), company, pdf.stem, doc_version)
    if not isinstance(set_version, str) or set_version == "":
        skipped += 1
        details.append("SKIP Evidence 库里没有该文档的 current set（不猜值）")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
        company_id=company, document_id=pdf.stem, document_version=doc_version,
        raw_pdf_path=str(pdf), raw_pdf_sha256=sha,
        expected_current_evidence_set_version=set_version))

    profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
    policy_resolver = SPR.FrozenSourcePolicyResolver.from_asset(
        REPO / profile.source_policy_asset)
    contract = load_contract_v2(str(REPO / profile.contract_asset))
    report_as_of = dims["as_of_date"]
    business = PS.ReportJobInput(
        job_id="job_m930_3_formal_chain_0001", company_id=company, company_name=company,
        credit_type="general", report_as_of=report_as_of, contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_3_formal_chain_0001",
        "document_id": pdf.stem, "document_version": doc_version,
        "raw_pdf_sha256": sha, "current_evidence_set_version": set_version,
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest = SC.build_scope_input_manifest(profile, business, source_inputs)
    projection = SC.project_contract_v2_scope(contract, profile, manifest)
    SC.verify_demo_projection(projection, contract)

    reqs = {r.topic_id: r for r in projection.requirements}
    tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
    fin_task = tasks["financial"]
    company_task, industry_task = tasks["company"], tasks["industry"]

    budget = TR.ResearchBudgetPolicy(
        policy_id=profile.budget_policy_id, version=profile.budget_policy_version,
        tier="demo_backbone", max_need_rounds_per_aspect=2, max_need_rounds_per_topic=2,
        max_tool_calls_per_topic=3 * len(reqs[company_task.topic_ids[1]].aspects),
        max_tokens_per_topic=200000,
        max_llm_calls_per_topic=3 * len(reqs[company_task.topic_ids[1]].aspects),
        max_elapsed_ms_per_topic=900000, tree_max_spans_per_aspect=6,
        tree_max_chars_per_span=4000)

    spec = WS.load_writing_spec(str(REPO / profile.writing_spec_asset))
    pprofile = PP.load_presentation_profile(str(REPO / profile.presentation_profile_asset))

    with tempfile.TemporaryDirectory() as td:
        reg, session = A.build_tree_bound_registry(
            live_span_source=live, audit_dir=Path(td) / "audit")
        snapshot, outline = live.snapshot, live.document_outline
        synopses = SY.build_navigation_synopses(
            node_ids=sorted({n.node_id for n in outline.nodes}), spans=snapshot.spans,
            coverages=snapshot.coverages, dispositions=snapshot.dispositions,
            policy=live.qualification_policy)
        index = NAV.NavigationIndex(outline, synopses,
                                    span_node_ids={s.node_id for s in snapshot.spans})
        if index.unavailable_reason is not None:
            skipped += 1
            details.append(f"SKIP 该文档的导航索引不可用（{index.unavailable_reason}）")
            return {"passed": passed, "failed": failed, "skipped": skipped,
                    "details": details}
        document = TR.DocumentIdentity(**session.document_identity())
        db = Path(td) / "formal_chain.db"
        store = TR.SqliteTopicStoreAdapter(db)
        store.init()
        _, _sp, scv, sev = R2.build_r2_material_dependencies(db)
        reader = PSet.SqlitePackSetStore(path=db, resolver=session.payload_resolver)
        sink = _Sink()

        def _deps(requirement, run_context):
            # `ancestor_labels` / `sibling_keys` 与生产侧同源
            # （`run_m930_3_acceptance._deps` 用 `NAV.contract_ancestor_inputs`）：
            # `anp-6` 的读根依赖 `parent_keys`（父主题标题层）与兄弟项排除；不传则该层
            # 读根整片消失或兄弟栏目名重新混入，本测试跑的就不是生产那条链。
            _labels, _siblings = NAV.contract_ancestor_inputs(contract)
            nav_profile = NAV.build_navigation_profile(
                requirement.aspects, contract_version=requirement.contract_version,
                contract_fingerprint=requirement.contract_fingerprint,
                ancestor_labels=_labels, sibling_keys=_siblings)
            navigation = TR.IndexedTreeNavigation(index=index, profile=nav_profile)
            return TR.TopicRuntimeDependencies(
                registry=reg, llm=object(), navigation=navigation,
                information_need_builder=_NeedBuilder(),
                route_context_builder=lambda: _route_context(run_context.company_id),
                route_fn=_route_fn,
                budget_state=TR.TopicBudgetState(policy=budget),
                trace_sink=sink, store=store,
                payload_resolvers=(session.payload_resolver,),
                source_policy_resolver=policy_resolver,
                set_completeness_verifier=scv, set_enumeration_verifier=sev,
                clock=lambda: STARTED_AT,
                research_question=_SpanRecallResearch(
                    navigation=navigation, requirement=requirement, document=document,
                    tree_max_spans=budget.tree_max_spans_per_aspect,
                    tree_max_chars=budget.tree_max_chars_per_span))

        def _context(requirement, run_id):
            return TR.TopicRunContext(
                run_id=run_id, case_id=run_id + "-case", company_id=requirement.company_id,
                section_id=requirement.section_id, task_id=requirement.task_id,
                report_as_of=requirement.report_as_of,
                demo_scope_fingerprint=manifest.scope_input_fingerprint,
                projection_version=projection.projection_id, document=document,
                sources=TSCH.DocumentSourceSet.single_document(
                    company_id=document.company_id, document_id=document.document_id,
                    document_version=document.document_version,
                    evidence_set_version=document.evidence_set_version),
                budget_policy=budget, started_at=STARTED_AT)

        # ---------------- 1. 真实 authority input（公开入口，不手写身份） -------------
        pack_sets: dict[str, object] = {}
        for section_id, worker in (("company", CW), ("industry", IW)):
            task = tasks[section_id]
            section_reqs = tuple(reqs[t] for t in task.topic_ids)
            extra = {} if worker is IW else {"section_id": section_id}
            phase = worker.run_backbone_topic_phase(
                task, **extra, requirements=section_reqs,
                run_context=_context(section_reqs[0], f"m930-3-{section_id}"),
                dependencies_of=lambda req, ctx: _deps(req, ctx), store=reader)
            pack_sets[section_id] = phase.pack_set
        fin_phase = FW.run_backbone_financial_phase(
            fin_task, company_id=dims["company_id"],
            projection_id=projection.projection_id,
            contract_version=contract.contract_version,
            contract_fingerprint=projection.contract_fingerprint,
            company_name=company, fin_db=str(FIN_DB),
            scope=dims["scope"], currency=dims["currency"], purpose=dims["purpose"],
            as_of_date=dims["as_of_date"], snapshot_id=dims["snapshot_id"])
        artifact = fin_phase.artifact

        def _contract_identity(task):
            req = reqs[task.topic_ids[0]]
            return req.contract_version, req.contract_fingerprint

        def _projection_for(task):
            cv, cf = _contract_identity(task)
            return PW.ContractProjection.create(
                spec, section_id=task.section_id, contract_version=cv,
                contract_fingerprint=cf)

        def _authority_for(task):
            cv, cf = _contract_identity(task)
            if task.section_id == "financial":
                return PW.FinancialAuthorityInput.create(
                    task, artifact, note_gap=fin_phase.evidence_note_gap,
                    fact_topic_map=_fact_topic_map(artifact, task),
                    company_id=dims["company_id"], report_as_of=dims["as_of_date"],
                    contract_version=cv, contract_fingerprint=cf)
            return PW.TopicPackAuthorityInput.create(
                task, pack_sets[task.section_id], company_id=company,
                report_as_of=report_as_of, contract_version=cv,
                contract_fingerprint=cf)

        company_authority = _authority_for(company_task)
        industry_authority = _authority_for(industry_task)
        financial_authority = _authority_for(fin_task)
        check(company_authority.producer_kind == "topic_harness"
              and industry_authority.producer_kind == "topic_harness"
              and company_authority.topic_ids == tuple(company_task.topic_ids),
              "公司/行业权威输入来自真实 Pack 集合（exact-topic-set，身份非手写）")
        check(financial_authority.producer_kind == "financial_workflow"
              and financial_authority.company_id == company
              and financial_authority.report_as_of == dims["as_of_date"],
              "财务权威输入的公司/基准期取自财务库自己的 current_snapshot（不是常量）")
        check(financial_authority.note_facts is None
              and financial_authority.note_gap is not None,
              "本轮真实财务附注状态是**缺口**（如实保留，不得伪装成已取得附注）")
        for authority in (company_authority, industry_authority, financial_authority):
            check(authority.input_id.startswith("tainput_"),
                  "权威输入身份是内容寻址的 tainput_ id")

        # 跨生产者隔离：真实对象互换必须被拒（§八 #2/#3）。
        fails(
            lambda: PW.TopicPackAuthorityInput.create(
                fin_task, pack_sets["company"], company_id=company,
                report_as_of=report_as_of, **_dict2k(_contract_identity(fin_task))),
            PW.PackWriterError, "", "财务 task 上挂公司 Pack 集合必须被拒（跨生产者混装）")
        fails(
            lambda: PW.FinancialAuthorityInput.create(
                company_task, artifact, note_gap=fin_phase.evidence_note_gap,
                company_id=company, report_as_of=report_as_of,
                **_dict2k(_contract_identity(company_task))),
            PW.PackWriterError, "", "公司 task 上挂财务 artifact 必须被拒（跨生产者混装）")

        # ---------------- 2. current 写作主链（真实权威输入） ----------------------
        def _section_input(section_id, task, authority, *, run_id: str | None = None):
            """composition root 注入的 section 输入：真实 task / 权威 / 投影 / 策略 / 依赖指纹。

            `run_id` 由 composition root 显式给出（缺省仍是本节固定的那一个）：每次运行有自己
            的运行身份，否则第二次运行会把第一条 run 的裁决记录当成同一次。
            """
            section_reqs = tuple(reqs[t] for t in task.topic_ids)
            return CW.BackboneWriterSectionInput(
                task=task, authority=authority, projection=_projection_for(task),
                writing_spec=spec, presentation_profile=pprofile,
                dependency_fingerprint=projection.dependency_fingerprint,
                requirements=section_reqs,
                run_context=_context(section_reqs[0],
                                     run_id or f"m930-3-writer-{section_id}"))

        # 2.1 P4 复算在真实临时 store 上**可算**：后面的 fail-closed 不是被 Pack 集合身份挡下的。
        for section_id, task in (("company", company_task), ("industry", industry_task)):
            section_reqs = tuple(reqs[t] for t in task.topic_ids)
            resolved = PSet.resolve_pack_set(task, section_reqs, reader)
            check(tuple(str(p.pack_id) for p in resolved.packs)
                  == tuple(str(p.pack_id) for p in pack_sets[section_id].packs),
                  f"{section_id}: 传入 authority 的 Pack 集就是 store 里的 current 集"
                  "（P4 复算可算且一致，写作相位不得在这道门上失败）")

        # 2.2 真实事实目录 / 期间排除 / aspect 未覆盖：逐条守恒、可回查，无静默丢弃。
        topic_scans: dict[str, object] = {}
        for section_id, task, authority in (("company", company_task, company_authority),
                                           ("industry", industry_task, industry_authority)):
            scan = PW.scan_authority(authority, task)          # 公开唯一入口
            topic_scans[section_id] = scan
            real = sum(len(tuple(getattr(p, "facts", ()) or ()))
                       + len(tuple(getattr(p, "external_facts", ()) or ()))
                       for p in authority.pack_set.packs)
            usable = len(scan.facts)
            unresolved = len(scan.period_unresolved)
            uncovered = len(scan.excluded_facts)
            check(real == usable + unresolved + uncovered,
                  f"{section_id}: 每条真实权威事实恰好归入「可用目录 / 期间未决 / aspect 未覆盖」"
                  f"之一（{real} != {usable}+{unresolved}+{uncovered}）——不得静默丢弃")
            check(len(scan.period_exclusions) == unresolved
                  and {str(i.get("fact_id")) for i in scan.period_exclusions}
                  == {f for _c, f, _t in scan.period_unresolved},
                  f"{section_id}: 每个期间未决事实恰好一条 typed 排除记录，且与 "
                  "`period_unresolved` 逐条对应")
            check(all(str(i.get("reason")) in _PERIOD_EXCLUSION_REASONS
                      for i in scan.period_exclusions),
                  f"{section_id}: 期间排除原因码只来自封闭集合 "
                  f"（实际 {sorted({str(i.get('reason')) for i in scan.period_exclusions})}）")
            check(all(str(i.get("period_requirement")) in (PW.PERIOD_REQUIREMENT_EXPLICIT,
                                                           PW.PERIOD_REQUIREMENT_OPTIONAL,
                                                           PW.PERIOD_UNRESOLVED_REASON)
                      for i in scan.period_exclusions),
                  f"{section_id}: 排除记录必须带 typed 期间要求（不是自由文本理由）")
            check(all(str(i.get("fact_id")) and i.get("container_id")
                      for i in scan.period_exclusions),
                  f"{section_id}: 排除记录必须带自身容器与 fact 身份（可回查）")
            check(usable == 0 if real == unresolved else True,
                  f"{section_id}: 目录为空只能是因为事实全部有 typed 去向")

        # 2.3 §四 只读诊断：每条真实事实「为什么没进正文」逐条说清（判据只用 typed 字段）。
        period_diagnosis = _period_diagnosis(
            authority=company_authority, task=company_task, spec=spec,
            resolver=session.payload_resolver, evidence_db=evidence_db)
        real_company_facts = {str(f.fact_id) for p in company_authority.pack_set.packs
                              for f in tuple(getattr(p, "facts", ()) or ())}
        check(set(period_diagnosis) == real_company_facts,
              "§四 诊断必须覆盖公司节**全部**真实事实（不得只报被排除的那些）")
        check(all(row["verdict"] in ("typed_period_present", "extractable_from_both_sides",
                                    "date_only_in_claim", "date_only_in_source",
                                    "no_material_bound", "no_explicit_date_anywhere")
                  for row in period_diagnosis.values()),
              "§四 诊断裁决来自封闭集合")
        check(all((row["exclusion"] is None) == (row["verdict"] == "typed_period_present"
                                                or row["verdict"]
                                                == "extractable_from_both_sides")
                  for row in period_diagnosis.values()),
              "§四 只有真正带可用期间的事实才没有排除记录（裁决与排除记录必须一致）")
        absence_hits, untraceable_absence = _absence_trace(period_diagnosis)
        check(not untraceable_absence,
              "§四 事实文本里的缺席措辞必须在被引材料正文里有出处（权威原文原本这么说可以，"
              "由抽取改写出来的不可以）"
              f"（无出处 {[(fid, w, text) for fid, w, text, _ in untraceable_absence]}）")
        note("§四 缺席措辞出处对账（事实文本 → 写出同一措辞的材料成员；空列表=无出处）:"
             f"{[(fid, w, srcs) for fid, w, _, srcs in absence_hits]}")
        # 聚焦反例（不进真实结果，只钉判据）：同一个函数必须**放行**忠实摘录、**拦下**改写。
        synthetic = {
            "faithful": {"fact_text": "公司报告期不存在控股股东及其他关联方非经营性占用资金。",
                         "sources": [{"material_id": "mat-x",
                                      "source_text": "公司报告期不存在控股股东及其他关联方"
                                                     "非经营性占用资金。"}]},
            "rewritten": {"fact_text": "公司未披露报告期内的关联方情况。",
                          "sources": [{"material_id": "mat-y",
                                       "source_text": "公司报告期内与关联方发生的交易情况如下。"}]},
            "no_source": {"fact_text": "该事项未提及。", "sources": []},
            "clean": {"fact_text": "公司实现营业收入 3620.13 亿元。",
                      "sources": [{"material_id": "mat-z", "source_text": "营业收入 3620.13 亿元"}]},
        }
        syn_hits, syn_bad = _absence_trace(synthetic)
        check({fid for fid, _, _, _ in syn_hits} == {"faithful", "rewritten", "no_source"},
              "§四 反例：缺席措辞对账只对**真的写了**这些词的事实生效（clean 不命中）")
        check({fid for fid, _, _, _ in syn_bad} == {"rewritten", "no_source"},
              "§四 反例：忠实摘录放行（原文自己写着「不存在」），改写（材料里没有）与"
              "无材料可对（无出处）都必须被拦下")
        note("§四 公司事实期间诊断裁决汇总："
             f"{ {k: v['verdict'] for k, v in period_diagnosis.items()} }")
        note("§四 期间的落点（claim / 被 inspect 的 span / 未被 inspect 的父块）："
             f"{ {k: {'claim': v['claim_explicit_periods'], 'span': sorted({p for s in v['sources'] for p in s['explicit_periods_in_source']}), 'parent_block': sorted({p for s in v['sources'] for p in (s['evidence'] or {}).get('parent_block_explicit_periods', [])})} for k, v in period_diagnosis.items()} }")
        note("§四 material 形态（whole_block / is_fallback / 被引 Evidence 的 report_period）："
             f"{ {k: {'materials': [{ 'whole_block': s['whole_block'], 'is_fallback': s['is_fallback'], 'report_period': (s['evidence'] or {}).get('report_period'), 'evidence_type': (s['evidence'] or {}).get('evidence_type')} for s in v['sources']], 'contract_kinds': v['contract_kinds'], 'time_scopes': v['time_scopes'], 'exclusion': (v['exclusion'] or {}).get('reason')} for k, v in period_diagnosis.items()} }")

        # 2.4 门前提案束只能由**真实权威目录**构造；本样本上目录为空 ⇒ 零 factual 候选。
        topic_plans = {section_id: _plan_from_catalog(topic_scans[section_id])
                       for section_id in ("company", "industry")}
        check(all(not plan["claim_candidates"] for plan in topic_plans.values()),
              "真实 topic 权威上可提出的 factual（路径 A）候选为零（候选只能选自权威事实目录，"
              "而目录为空是权威侧的期间缺口，不是组织计划的缺陷）")
        # §三 A：材料正文上下文在本样本上**真实解析成功**（每个成员都经 payload 字节重算 +
        # 定位/身份/哈希复核），所以「路径 B 可达性」由材料正文决定，**不**由事实目录决定。
        company_context = MCW.resolve_writer_material_context(
            pack_set=company_authority.pack_set, resolver=session.payload_resolver,
            task_id=str(company_task.task_id), section_id=str(company_task.section_id))
        industry_context = MCW.resolve_writer_material_context(
            pack_set=industry_authority.pack_set, resolver=session.payload_resolver,
            task_id=str(industry_task.task_id), section_id=str(industry_task.section_id))
        check(all(not plan["narrative_draft_units"] for plan in topic_plans.values()),
              "本替身计划没有提出任何草稿单元（它只从事实目录派生候选）——这不等于"
              "「没有可绑定的材料」：真实材料正文上下文已解析出成员（数量见下方 NOTE）")
        note(f"§三 A 真实材料正文上下文：company {len(company_context.materials)} 个成员 / "
             f"industry {len(industry_context.materials)} 个成员（wmctx-1，全部经真实 payload "
             "字节重算、定位与身份复核）；路径 B 是否可写在**材料正文**，与事实目录是否为空无关")
        # §三 A 诊断面（只读）：逐成员摊开**替身的选材判读**——真实正文里到底有几条句子、其中
        # 有没有可用的描述性原子、其余被哪一条规则挡下。与 A1 的源头对账用**同一份**判读实现
        # （`run_m930_3_acceptance._slice_audit`），共同回答「材料是在哪一层掉的」。
        # 这里**不**判 A1：A1 只认经绑定/蕴含门成立的事实性 Claim，替身选材策略的诊断数不构成
        # 门判据。要钉的是「可读」与「可写」是两件事。
        from evaluation import run_m930_3_acceptance as ACC

        member_rows: list[dict] = []
        for member in company_context.materials:
            reading = str(member.reading_view or "")
            audit = ACC._slice_audit(reading)
            reasons: dict[str, int] = {}
            for row in audit["sentences"]:
                for reason in row["excluded_by"]:
                    reasons[reason] = reasons.get(reason, 0) + 1
            flat = " ".join(reading.split())
            # 只被**替身自己的选材条件**挡下的句子（长度窗口 / 勾选框字形）与那些还被**策略条件**
            # 挡下的（含数字 = 只能走路径 A、未绑定期间措辞 = 期间门、高风险表面 = 路径 B 不授权）
            # 必须分开数：前者说明「切句策略漏掉了本来可用的正文」，后者说明「这一段真实正文本来
            # 就不可授权」。把两者混成一个 0 会把「策略漏掉」误读成「语料没有」。
            #
            # 还有**第三类**必须与第一类分开：`acc-38` 之后「这段没有陈述对象」与「切不动（过长且
            # 无安全切分点）」也由替身选材判下（措辞同样带「替身选材」四字），但它们不是「本来可用
            # 的正文被窗口/字形挡下」——判据说的是**这段本身不能独立成一条事实**（替身不补主语、
            # 不强凑）。把它们算进「漏掉」，就会把「本来就不该进正文的残片」读成「策略漏”。
            _NOT_A_WINDOW_MISS = ("无独立主语", "无陈述对象", "无安全切分点")
            selection_only = [row["text"] for row in audit["sentences"]
                              if row["excluded_by"]
                              and all("替身选材" in reason for reason in row["excluded_by"])
                              and not any(mark in reason
                                          for reason in row["excluded_by"]
                                          for mark in _NOT_A_WINDOW_MISS)]
            member_rows.append({
                "material_id": str(member.material_id), "chars": len(reading),
                "sentences": len(audit["sentences"]), "eligible": len(audit["eligible"]),
                "reasons": dict(sorted(reasons.items())),
                "selection_only_exclusions": len(selection_only),
                # 一条句子都切不出来的成员必须能看出**它是什么**：是勾选行/表头残片，还是
                # 「只是一句缺终止符的叙述」。两者的后续处置完全不同，不能混成一句「无可用材料」。
                "preview": (flat[:60] + "…" if len(flat) > 60 else flat)
                           if not audit["sentences"] else ""})
        note(f"§三 A 逐成员选材判读（只读诊断，与 A1 源头对账同一份判读实现）：{member_rows}")
        check(bool(member_rows) and all(row["chars"] > 0 for row in member_rows),
              "§三 A 诊断面：公司节每个成员都必须有**可读**正文（可读为假才是真正的断链；"
              f"实际 {[row['material_id'] for row in member_rows if row['chars'] <= 0]} 为空才成立)")
        check(all(row["eligible"] <= row["sentences"] for row in member_rows),
              "§三 A 诊断面：可用原子数不得多于切出的句子数（判读实现不得自相矛盾）")
        check(all(row["selection_only_exclusions"] <= row["sentences"] for row in member_rows),
              "§三 A 诊断面：「只被替身选材条件挡下」的句数不得多于切出的句子数")
        note("§三 A 诊断面汇总：切出句子的成员 "
             f"{sum(1 for r in member_rows if r['sentences'])}/"
             f"{len(member_rows)}；切不出任何句子的成员 "
             f"{[r['material_id'] for r in member_rows if not r['sentences']]}；"
             f"只被**替身选材条件**（长度窗口/勾选框字形）挡下、未触碰任何策略条件的句子共 "
             f"{sum(r['selection_only_exclusions'] for r in member_rows)} 条"
             "（>0 才说明「切句策略漏掉了本来可用的正文」；0 说明这一层不是丢失点）")

        # 门后定稿与提交前的封口检查用的是**同一条**判据（`NS.has_registered_gaps`）：缺口已
        # 如实登记 ⇒ 本节正文就是缺口附录本身（§五 3「否则 typed 缺口」在人读面上的表达）；
        # 什么都没登记 ⇒ 空正文照旧 fail-closed（下面的反例逐层复现）。
        gap_phase = CW.run_backbone_writer_phase(
            (_section_input("company", company_task, company_authority),
             _section_input("industry", industry_task, industry_authority)),
            llm_client=_StubLlm(topic_plans["company"], topic_plans["industry"]),
            entailment_llm_client=_StubEntailmentClient(_ENTAILED),
            final_sentence_llm_client=_stub_final_sentence_client(),
            store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT)
        gap_outputs = {str(o.section_id): o for o in gap_phase.sections}
        check(set(gap_outputs) == {"company", "industry"},
              "缺口形态的相位必须如实返回这两节（缺口形态不是「失败即无声」）")
        for gap_section_id, gap_output in sorted(gap_outputs.items()):
            check(not gap_output.claims and not gap_output.narrative.paragraphs
                  and not gap_output.narrative.tables,
                  f"{gap_section_id}: 本替身计划零候选 ⇒ 本节零 Claim / 零段落 / 零表格"
                  "（缺口形态不是「补一段话」，更不得凭空产生正文）")
            # 状态是**派生量**，口径只有一条（`PW._derive_status` = canonical
            # `RC.derive_status`）：WAITING_HUMAN > 任一 SECTION/REPORT/JOB_BLOCKED ⇒
            # SECTION_BLOCKED > 有缺口 ⇒ COMPLETED_WITH_GAPS > COMPLETED。本节登记过缺口，
            # 因此**不得**升格成 COMPLETED；而有 blocking 级联效果的缺口还必须如实落成
            # SECTION_BLOCKED，不能被降级成「只是有缺口」（`sections/rules_evaluator` 的规则 3
            # 正是按这条口径复核，两处不得各行一套）。
            gap_effects = sorted({str(e) for u in gap_output.result.unresolved
                                  for e in (getattr(u, "blocking_effects", ()) or ())})
            canonical_status = PW._derive_status(gap_output.result.unresolved)
            check(gap_output.result.status == canonical_status,
                  f"{gap_section_id}: 章节状态必须等于唯一口径 `PW._derive_status`"
                  f"（实际 {gap_output.result.status!r}，口径 {canonical_status!r}）")
            check(gap_output.result.status != "COMPLETED",
                  f"{gap_section_id}: 登记过缺口时状态不得升格成 COMPLETED"
                  f"（实际 {gap_output.result.status!r}）")
            check(bool(gap_effects) == (gap_output.result.status == "SECTION_BLOCKED"),
                  f"{gap_section_id}: 缺口带 {gap_effects} 级联效果时状态必须是 "
                  f"SECTION_BLOCKED，不带时不得是（实际 {gap_output.result.status!r}）")
            check(bool(gap_output.draft.unresolved_ids)
                  and {str(u) for u in gap_output.draft.unresolved_ids}
                  == {str(u.unresolved_id) for u in gap_output.result.unresolved},
                  f"{gap_section_id}: draft 缺口与 Result unresolved 必须逐条相等且非空"
                  "（缺口不得被吞成空 Result）")
            check(NS.UNRESOLVED_APPENDIX_HEADING in gap_output.result.markdown,
                  f"{gap_section_id}: 零正文章节的正文就是缺口附录本身——缺口必须**可读**地"
                  "出现在 markdown 里，而不是只藏在字段里")
            check(all(not any(w in str(u.detail) for w in FORBIDDEN_ABSENCE_WORDING)
                      for u in gap_output.result.unresolved),
                  f"{gap_section_id}: 缺口正文不得把「未取得」写成「不存在/未披露」")
            check(gap_output.persistence.get("state") == "not_injected",
                  f"{gap_section_id}: 本模块未注入 section store，落库状态必须如实记为 "
                  "not_injected（不得读成「已落库且无异常」）")
            check(not gap_output.follow_up_needs,
                  f"{gap_section_id}: 本替身计划不制造补件诉求（有诉求时相位必须报错，"
                  "不是静默丢弃）")
        # 提交前的封口检查必须接受**同一形态**：否则同一条链在定稿口放行、在封口口被拒。
        gap_chain = ST.SectionChainV2(
            draft=gap_outputs["industry"].draft, claims=(),
            aggregate_decisions=tuple(gap_outputs["industry"].aggregate_decisions),
            entailment_decisions=tuple(gap_outputs["industry"].entailment_decisions),
            accepted_bindings=tuple(gap_outputs["industry"].acceptance.accepted_bindings),
            fact_narrative_dispositions=tuple(gap_outputs["industry"].dispositions),
            claim_narrative_dispositions=tuple(
                gap_outputs["industry"].claim_narrative_dispositions),
            narrative=gap_outputs["industry"].narrative,
            result=gap_outputs["industry"].result)
        check(gap_chain.result is gap_outputs["industry"].result,
              "登记过缺口的零正文章节必须能通过 `SectionChainV2` 的封口前检查"
              "（定稿口与封口口的判据必须是同一条）")

        # 门前分解：缺口在门前已经如实形成；定稿口与封口口随后都按同一条判据接受它。
        pre_gate = PW.write_section(
            company_task, company_authority, projection=_projection_for(company_task),
            writing_spec=spec, presentation_profile=pprofile,
            llm_client=_StubLlm(topic_plans["company"]), policy=None,
            dependency_fingerprint=projection.dependency_fingerprint, created_at=STARTED_AT,
            # §三 A：门前分解也必须走与写作相位**同一**入口的 wmctx-1 正文上下文
            # （用真实 session 的 PayloadResolver 现解析），否则这一步测的就不是真实形态。
            material_context=company_context)
        check(not pre_gate.draft.claim_candidates and not pre_gate.draft.narrative_draft_units,
              "门前 Draft 忠实反映模型提出的候选束（本样本为空，不凭空造）")
        check(not pre_gate.gate_result.blocking,
              "叙事硬门不得对空候选束 blocking（失败点不在硬门）")
        check({u.unresolved_id for u in pre_gate.unresolved}
              == set(pre_gate.draft.unresolved_ids),
              "门前缺口守恒：draft 缺口与 Writer unresolved 逐条一致（缺口不被静默丢弃）")
        check(all(str(u.topic_id) in tuple(company_task.topic_ids)
                  for u in pre_gate.unresolved),
              "门前缺口只能落在本任务 topic 集合内（不得把别的主题的缺口塞进来）")
        check(all(not any(w in str(getattr(u, "detail", "") or "")
                          for w in FORBIDDEN_ABSENCE_WORDING)
                  for u in pre_gate.unresolved),
              "缺口正文不得把「未取得」写成「不存在/未披露」")
        check(not hasattr(pre_gate, "claims") and not hasattr(pre_gate, "section_result")
              and not hasattr(pre_gate, "binding"),
              "Writer 门前产物不含任何定稿对象（Claim / Result / binding 只在门后由协调器形成）")
        # 门后缺口的**存在**不等于「缺口已如实登记」：把登记抹掉，同一个构造器必须照旧拒绝。
        # 本样本的 draft 连候选都没有，抹掉缺口登记会先被 wire 自己的「空 draft 不得静默通过」
        # 挡下（这是第一道，如实报出，不当成构造器判据的证据）；有候选的 draft 上的那一层反例
        # 在 §2.5 财务正向之后（那里存在带候选的 draft）。
        fails(
            lambda: _draft_without_registered_gaps(pre_gate.draft),
            NS.NarrativeSchemaError, "空 draft 不得静默通过",
            "零候选 draft 抹掉缺口登记后先被 wire 拒绝（空 draft 不得静默通过）")
        note(f"§2 真实 topic 权威的零候选证据：company facts={len(topic_scans['company'].facts)}/"
             f"期间未决={len(topic_scans['company'].period_unresolved)}/"
             f"aspect 未覆盖={len(topic_scans['company'].excluded_facts)}，"
             f"industry facts={len(topic_scans['industry'].facts)}；"
             f"门前缺口={len(pre_gate.unresolved)} 条"
             f"（topics={sorted({str(u.topic_id) for u in pre_gate.unresolved})}），"
             f"硬门 blocking={bool(pre_gate.gate_result.blocking)}")
        note("§2 结论（如实上报，不加宽门换绿）：**本替身计划**在真实 DemoScope 的公司/行业节上"
             "提不出任何候选——权威侧零可用事实（冻结 Contract 的显式期间要求 vs 真实事实的期间"
             "来源），而替身计划只从事实目录派生候选（路径 A）。这不等于公司节不可写："
             f"公司节有 {len(company_context.materials)} 份真实可读材料正文，路径 A 为空**不妨碍**"
             "合法的路径 B；本模块用的是替身计划，因此**没有**证明也没有否证「真实模型能否提出"
             "合法路径 B 候选」——那是 M930-3 真实验收门（create-only runner）要做的事。"
             "定稿侧现在**有**「零 Claim 但缺口齐全」的表达形态（正文 = 标题 + 缺口附录，"
             "状态 COMPLETED_WITH_GAPS），因此本替身计划下相位如实走完并留下可读缺口；"
             "「零 Claim 且**没有**登记缺口」仍然 fail-closed（上面两层反例各复现一次）。"
             "**这不等于人读内容通过**：本节没有任何正文，缺口是否可读仍待人工复核。")

        # 2.5 财务分支：**同一**协调器按 authority kind 泛化后必须服务财务（§三 D）。
        #
        # 两根轴不得混用：section 级 `research_policy`（真实冻结 Contract 里财务节是 `workflow`）
        # 决定「这一节走哪条研究相位」；authority 的 `producer_kind` 是**生产者类别**。下面先把
        # 两个字面量与它们各自的真实来源逐字对齐（防漂移），再在真实财务权威上跑正向主链。
        fin_input = _section_input("financial", fin_task, financial_authority)
        check(fin_task.research_policy == CW.FINANCIAL_SECTION_RESEARCH_POLICY
              == FW.SECTION_RESEARCH_POLICY,
              "真实冻结 Contract 的财务节 section 级 research_policy 必须逐字等于 "
              f"`CW.FINANCIAL_SECTION_RESEARCH_POLICY`/`FW.SECTION_RESEARCH_POLICY`"
              f"（实际 {fin_task.research_policy!r}）")
        check(financial_authority.producer_kind == CW.FINANCIAL_PRODUCER_KIND
              == FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND,
              "财务权威的 producer_kind 必须逐字等于 `CW.FINANCIAL_PRODUCER_KIND`"
              f"（实际 {financial_authority.producer_kind!r}）")
        fin_branch = CW.AUTHORITY_BRANCHES[CW.FINANCIAL_PRODUCER_KIND]
        check(tuple(fin_branch.research_policies) == (CW.FINANCIAL_SECTION_RESEARCH_POLICY,)
              and fin_branch.authority_type is PW.FinancialAuthorityInput
              and not fin_branch.material_bound and not fin_branch.pack_set_bound,
              "财务分支的**全部**三处差异必须是显式分支结论（无 ResearchMaterial 边界 ⇒ 无 "
              "wmctx-1 正文上下文；无 Pack set ⇒ 无 P4 复算；无 Pack successor ⇒ FollowUp 只能"
              "typed 终止），不得靠缺失字段的 AttributeError 兜底")
        check(CW._REQUIRED_KINDS_BY_PRODUCER.get(CW.FINANCIAL_PRODUCER_KIND) == ("financial_pack",)
              and not hasattr(FW, "run_backbone_writer_phase"),
              "财务仍**没有**第二套 writer runtime（`financial_worker` 不导出写作相位）："
              "财务只能走 `company_worker.run_backbone_writer_phase` 这条唯一主链")

        # 正向：真实财务权威上，路径 A 候选必须能走完 Draft → proposal → aggregate → entailment
        # → accepted binding → Claim → final Narrative → Result。
        fin_scan = PW.scan_authority(financial_authority, fin_task)
        check(len(fin_scan.facts) > 0,
              f"真实财务权威必须有可用事实（实际 {len(fin_scan.facts)} 条）："
              "零事实时下面的正向链就不是「真实链贯通」而是空转")
        fin_plans = _plan_from_catalog(fin_scan)
        check(not fin_plans["narrative_draft_units"],
              "财务分支没有 ResearchMaterial 边界：本替身计划不得凭空提出草稿单元"
              "（context 边只能挂 exact material）")
        fin_phase = CW.run_backbone_writer_phase(
            (fin_input,),
            llm_client=_StubLlm(fin_plans),
            entailment_llm_client=_StubEntailmentClient(_ENTAILED),
            final_sentence_llm_client=_stub_final_sentence_client(),
            store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT)
        fin_out = fin_phase.sections[0]
        check(fin_out.section_id == fin_task.section_id
              and fin_out.draft.section_id == fin_task.section_id
              and fin_out.draft.producer_kind == CW.FINANCIAL_PRODUCER_KIND,
              "财务 Draft 必须由同一条主链产出，且 producer_kind 逐字是财务分支值")
        fin_candidates = tuple(fin_out.draft.claim_candidates)
        fin_edges = tuple(fin_out.draft.proposed_support_refs)
        check(len(fin_candidates) == len(fin_scan.facts) == len(fin_edges) > 0,
              f"路径 A 候选必须逐条取自权威事实目录（候选 {len(fin_candidates)} / "
              f"目录 {len(fin_scan.facts)} / proposal {len(fin_edges)}）")
        check(all(p.authorization_path == "path_a_prevalidated"
                  and p.support_semantics == "factual"
                  and p.authority_kind == "financial_pack"
                  and p.material_id is None and p.payload_ref is None
                  and p.financial_fact_id for p in fin_edges),
              "财务 proposal 必须是**路径 A**：事实身份来自财务权威、不带 material/payload 锚点"
              "（财务分支没有 ResearchMaterial，不得用 topic_pack 的路径 B 混进来）")
        check(all(p.authority_container_id == p.authority_container_id.strip()
                  and p.binding_subject_kind == "claim_candidate" for p in fin_edges),
              "财务 proposal 只挂在候选上（门前提案束里没有草稿单元可挂）")
        fin_aggregates = tuple(fin_out.aggregate_decisions)
        fin_entailments = tuple(fin_out.entailment_decisions)
        fin_subject_ids = [str(d.subject_id) for d in fin_aggregates]
        fin_candidate_ids = {str(c.candidate_id) for c in fin_candidates}
        check(all(str(d.subject_kind) == "claim_candidate" for d in fin_aggregates)
              and sorted(fin_subject_ids) == sorted(fin_candidate_ids)
              and len(set(fin_subject_ids)) == len(fin_subject_ids),
              "每个 factual 候选（subject revision）**恰一个** aggregate 决定：不多、不少、不重"
              f"（候选 {len(fin_candidate_ids)} / 决定 {len(fin_aggregates)}）")
        check(len(fin_entailments) == len(fin_candidates)
              and {str(d.claim_candidate_id) for d in fin_entailments} == fin_candidate_ids
              and all(str(d.verdict) == "entailed" for d in fin_entailments),
              "factual 候选另有恰一个 entailment 决定（context subject 不进入该门），"
              f"实际 {len(fin_entailments)} 条")
        fin_by_subject = {str(d.subject_id): d for d in fin_aggregates}
        check(all(str(d.binding_decision_id)
                  == str(fin_by_subject[str(d.claim_candidate_id)].binding_decision_id)
                  and str(d.support_set_digest)
                  == str(fin_by_subject[str(d.claim_candidate_id)].support_set_digest)
                  and str(d.draft_revision)
                  == str(fin_by_subject[str(d.claim_candidate_id)].draft_revision)
                  for d in fin_entailments),
              "每条 entailment 决定必须绑定**唯一通过的** aggregate 决定，且与它同一 support-set "
              "digest 与同一 draft revision（不得跨候选串用决定）")
        fin_bindings = tuple(fin_out.acceptance.accepted_bindings)
        check(len(fin_bindings) == len(fin_edges) and not fin_out.acceptance.rejected_subjects,
              f"每条通过的 proposal 各自产生一条 accepted binding（{len(fin_bindings)}）；"
              "本正向样本上不得有被拒 subject")
        check(len(fin_out.claims) > 0
              and all(c.accepted_binding_ids and c.claim_candidate_id and c.topic_id
                      for c in fin_out.claims),
              "门后必须定稿出 Claim，且每条 Claim 都指回真实候选与 accepted binding")
        check(all(str(b.authority_kind) == "financial_pack" for b in fin_bindings),
              "财务 accepted binding 的权威类别逐字是 financial_pack（不得被改写成 topic_pack）")
        fin_paragraphs = tuple(fin_out.narrative.paragraphs)
        # §七 2：重复的「指标 × 期间」事实**优先**由结构化表格承载（24 条事实拼成一条 574 字
        # 长句不是可读正文）。这里断言的是「财务节有可读的结构化正文」：正文要么是段落，要么是
        # 表格 —— 二者都空才是「有 Claim 但组不成正文」。
        fin_tables = tuple(fin_out.narrative.tables)
        fin_rows = tuple(row for table in fin_tables for row in table.rows)
        check(bool(fin_paragraphs) or bool(fin_tables),
              f"财务节必须有可读正文（段落 {len(fin_paragraphs)} / 表格 {len(fin_tables)}）——"
              "「有 Claim 但组不成段落也不是表格」不是贯通")
        check(bool(fin_tables) and all(len(table.header) >= 3 for table in fin_tables),
              f"重复的「指标 × 期间」事实必须成表（实际 {len(fin_tables)} 张表）："
              "行 = 指标、列 = 期间，且至少两个期间列才叫「重复」")
        fin_claim_by_id = {str(c.claim_id): c for c in fin_out.claims}
        check(all(len(row.claim_ids) == len([c for c in row.cells if str(c).strip()])
                  and all(str(cell) in fin_claim_by_id[str(cid)].text
                          for cid, cell in zip(row.claim_ids,
                                               [c for c in row.cells if str(c).strip()]))
                  for row in fin_rows) and bool(fin_rows),
              "表格行的每个非空单元格必须逐字来自它对应的已定稿 Claim（单元格只能复用 Claim "
              "已承担的权威表面）；空单元格表达「本列期间没有已接受的权威事实」且不携带 Claim")
        fin_tabled_ids = set(NS.tabled_claim_ids(fin_tables))
        fin_prose_ids = {str(cid) for p in fin_paragraphs for s in p.sentences
                         for cid in s.claim_ids}
        check(fin_tabled_ids | fin_prose_ids == set(fin_claim_by_id),
              "本节每条已定稿 Claim 必须恰好有一个呈现位置（表格行或正文句），不得凭空消失："
              f"表格 {len(fin_tabled_ids)} / 正文 {len(fin_prose_ids)} / Claim "
              f"{len(fin_claim_by_id)}")
        check(not (fin_tabled_ids & fin_prose_ids),
              "同一 Claim 不得既陈列在表格里又写成正文句（重复的指标×期间事实以表格呈现）")
        fin_dispositions = {str(d.claim_id): d for d in fin_out.claim_narrative_dispositions}
        check(all(str(fin_dispositions.get(cid).disposition) == "omitted"
                  and str(fin_dispositions.get(cid).reason_code) == "presented_as_table_row"
                  for cid in fin_tabled_ids),
              "表格行的 Claim 去向必须由表格自己决定：omitted / presented_as_table_row"
              "（记成 selected 等于声明它被写进了正文句）")
        fin_composed = [s for p in fin_paragraphs for s in p.sentences
                        if str(getattr(s, "sentence_kind", "")) == "composed"]
        check(all(pid in fin_claim_by_id for s in fin_composed for pid in s.claim_ids),
              "composed 句绑定的 Claim id 必须都在本节最终 Claim 集合内（引用不得悬空）")
        check(all(tuple(table.topic_ids) for table in fin_tables),
              "财务表格必须声明它陈列的主题（表格不得成为无归属的正文）")
        check(tuple(str(r) for r in fin_out.result.source_run_ids)
              == tuple(str(r) for r in PW._source_run_ids(financial_authority))
              and fin_out.result.section_draft_id == fin_out.draft.draft_id,
              "财务 SectionResult 的 run 身份必须由权威自己派生（快照 id），"
              "且单向引用同一 draft（不得自报、不得反向引用）")
        check(not fin_out.follow_up_needs and not fin_phase.follow_up_runs,
              "本正向样本上不得有 FollowUp（有诉求而无 successor 能力必须报错，不是静默丢弃）")

        # 反例②（门后定稿构造器）：同一个**带候选**的 draft 把缺口登记抹掉后，零 Claim 定稿
        # 必须照旧被拒——判据是「缺口已如实登记」，不是「缺口字段曾经非空」。
        fin_gap_cleared = _draft_without_registered_gaps(fin_out.draft)
        fails(
            lambda: NS.build_section_narrative(
                draft=fin_gap_cleared, claims=(), context_binding_ids=()),
            NS.NarrativeSchemaError, "draft 也没有登记缺口",
            "带候选的 draft 抹掉缺口登记后，零 Claim 定稿在唯一 final Narrative 构造器处即被拒")
        # 反例③（提交前的封口检查）：零 Claim 集 + 已抹掉缺口登记的 draft 不得以 Result 封口。
        # 反例必须**自己造出**「零 Claim 且零表格」的正文：本样本的财务正文现在**有**表格
        # （§七 2），直接拿它当反例材料，命中的就不是「空正文」这条判据了。空段落空表格的
        # narrative 在 wire 层可构造（那正是「合法形态只有一种」要判的对象）。
        empty_narrative = NS.SectionNarrative.create(
            task_id=fin_out.draft.task_id, section_id=fin_out.draft.section_id,
            section_draft_id=fin_out.draft.draft_id,
            draft_revision=fin_out.draft.draft_revision, paragraphs=(), tables=())
        fails(
            lambda: ST.SectionChainV2(
                draft=fin_gap_cleared, claims=(), narrative=empty_narrative,
                result=fin_out.result),
            ST.SectionStorageConflictError, "不得以 Result 封口",
            "零 Claim 且**没有**登记缺口的门后束不得以 Result 封口"
            "（封口口的放宽只覆盖「已登记缺口」这一种形态）")

        # 反例①：`producer_kind` 守卫用同形合成反例单独隔离（真实财务节现在合法进入相位，
        # 第二道守卫只能这样测）。未登记的生产者类别必须 fail-closed。
        fails(
            lambda: CW.run_backbone_writer_phase(
                (_section_input("company", company_task,
                                _ForeignProducerAuthority(company_task)),),
                llm_client=_StubLlm(_plan()),
                entailment_llm_client=_StubEntailmentClient(_ENTAILED),
                final_sentence_llm_client=_stub_final_sentence_client(),
                store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT),
            CW.BackboneWriterPhaseError, "producer_kind",
            "未登记的 producer_kind 不得进入写作相位（合成反例隔离该守卫）")
        foreign = _ForeignProducerAuthority(company_task)
        foreign.producer_kind = "unregistered_producer_kind"
        foreign_error = fails(
            lambda: CW.run_backbone_writer_phase(
                (_section_input("company", company_task, foreign),),
                llm_client=_StubLlm(_plan()),
                entailment_llm_client=_StubEntailmentClient(_ENTAILED),
                final_sentence_llm_client=_stub_final_sentence_client(),
                store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT),
            CW.BackboneWriterPhaseError, "unregistered_producer_kind",
            "完全未登记的生产者类别必须被列出（不是泛化字符串）")
        check(all(str(k) in foreign_error for k in sorted(CW.AUTHORITY_BRANCHES)),
              f"拒绝理由必须列出全部已登记分支 {sorted(CW.AUTHORITY_BRANCHES)}")

        # 反例②：财务分支的 FollowUpNeed 只能 typed 终止。财务权威**不携带** Contract
        # requirement 对象（`scan.requirement_ids` 为空），所以「合法申请」在 Writer 侧就已经
        # 不可构造——伪造一个需求 id 必须被拒，而不是被静默接受。
        #
        # 后果的范围（M930-3 返修 §二 2.6）：不成立的是**那一条申请**。它落成一条 typed 拒绝
        # 记录（封闭原因码 + 序号 + 可读原因），而财务节**本身照常定稿**——真实 run r5 的
        # financial 正是被这样一条申请整节打掉的（门都过了，死在门后一条诉求上）。「申请杀死
        # 整节」不是「拒绝了申请」，它连已经通过硬门的候选一起丢掉，因此这里是反例而不是正例。
        fin_topics = tuple(fin_task.topic_ids)
        check(not dict(fin_scan.requirement_ids),
              "财务权威读视图**不含** requirement id（这是「财务 FollowUp 无真实需求可回指」"
              "这一事实的来源，不是缺省值）")
        # 同一份读视图**也不携带**任何 Contract aspect 投影（`scan_authority` 的财务分支显式置空）。
        # 两个「空」合起来决定了一件事：本分支上任何一条补件申请都会先在 **aspect 门**被判不成立，
        # 根本走不到需求门——`target_requirement_mismatch` 在财务分支**不可达**。这不是「测试写不
        # 出来」，而是这条分支的真实形状（r5 死的那一条正是 aspect 门）。因此下面只钉**真实可达**
        # 的那几个原因码，不为凑词表覆盖编一条走不到的反例。
        check(not dict(fin_scan.aspect_topic) and not dict(fin_scan.aspect_question)
              and not dict(fin_scan.aspect_status),
              "财务权威扫描不得携带 Contract aspect 投影（带上了，下面原因码的可达性就变了）")
        fin_req = reqs[fin_topics[0]]
        fin_question = tuple(fin_task.questions)[0]
        _fin_base = {"statement": "（合成反例）财务节需要补充口径说明。",
                     "target_requirement_id": f"{fin_task.section_id}::{fin_topics[0]}",
                     "topic_id": str(fin_topics[0]),
                     "question_id": str(fin_question.question_id),
                     # 取**真实 Contract 投影里**的 aspect（对申请最有利的那一条）：它同样会被
                     # aspect 门拒。用它而不是随便挑一个字符串，是为了让结论落在「这条分支没有
                     # 可回指的投影」上，而不是落在「反例自己挑错了 aspect」上。
                     "aspect_id": str(fin_req.aspects[0].aspect_id),
                     "requiredness": "required",
                     "expected_source_class": "company_industry"}
        forged_specs = (
            ({**_fin_base, "topic_id": "fin_topic_not_in_section"},
             "不属于本节权威输入的 topic", "topic_not_in_section"),
            ({**_fin_base, "question_id": "q-not-under-topic"},
             "不是该 topic 下问题的 question", "question_not_under_topic"),
            # ① 真实 Contract 投影里的 aspect：本分支不携带投影，因此它在投影内也照样不成立；
            # ② r5 逐字出现的那一个（`fin_statements_availability`，连投影字符串都不是）。
            (_fin_base, "真实投影里、但本分支不携带投影的 aspect", "aspect_not_in_topic"),
            ({**_fin_base, "aspect_id": "fin_statements_availability"},
             "r5 逐字的、不在该 topic 投影内的 aspect", "aspect_not_in_topic"),
        )
        # 对照基准必须是「**同一次生成输入**、只差那条诉求」的那一遍：拿 `fin_out` 当基准是在比
        # 两份不同的输入（它跑的是目录计划，这里跑的是反例计划），那样比出来的差异与诉求无关。
        # 控制组因此跑同一份真实目录计划、零诉求，并先证明它确实带着真实候选——否则「草稿没被杀」
        # 可能只是因为草稿本来就是空的。
        fin_forged_plan = _plan_from_catalog(fin_scan)
        control_out = PW.write_section(
            fin_task, financial_authority, projection=_projection_for(fin_task),
            writing_spec=spec, presentation_profile=pprofile,
            llm_client=_StubLlm(fin_forged_plan), policy=None,
            dependency_fingerprint=projection.dependency_fingerprint,
            created_at=STARTED_AT)
        check(len(control_out.draft.claim_candidates)
              == len(fin_forged_plan["claim_candidates"]) > 0,
              "对照组必须带着真实目录候选（零候选的对照证明不了「草稿没被杀」）")
        for spec_row, what, expect_code in forged_specs:
            forged_out = PW.write_section(
                fin_task, financial_authority,
                projection=_projection_for(fin_task), writing_spec=spec,
                presentation_profile=pprofile,
                llm_client=_StubLlm({**fin_forged_plan,
                                     "follow_up_needs": [dict(spec_row)]}), policy=None,
                dependency_fingerprint=projection.dependency_fingerprint,
                created_at=STARTED_AT)
            codes = [r["code"] for r in forged_out.follow_up_rejections]
            check(codes == [expect_code] and not forged_out.follow_up_needs,
                  f"财务节上伪造「{what}」的 FollowUpNeed 必须落成**恰好一条** typed 拒绝记录"
                  f"（期望 {expect_code}，实测 {codes}），且不得成为 FollowUpNeed")
            check(forged_out.draft.draft_id == control_out.draft.draft_id,
                  f"伪造「{what}」不得连带杀死财务节已经通过硬门的 Draft"
                  "（一条诉求不成立 ≠ 整节不成立；r5 的 financial 正是这样整节消失的）"
                  f"[diag forged={forged_out.draft.draft_id[:28]}"
                  f" control={control_out.draft.draft_id[:28]}"
                  f" 候选 {len(forged_out.draft.claim_candidates)}"
                  f"/{len(control_out.draft.claim_candidates)}]")
        # 边界：**形状**就不成立的那一条仍是整束 fail-closed——它是「这份响应不是一份合法提案束」，
        # 与上面「坐标指不回真实需求」不是一类事。但拒绝同样必须可审计：异常带出整束 typed 审计
        # 与那条不成形的原始诉求，消息点名**哪个字段**缺，而不是一句泛化的「提案被拒」。
        #
        # 「成形」有两个档，必须分开钉，因为它们在**原始响应**里长得不同：
        #   ① 键在、取值为空：原始响应里字段是全的，因此到逐条语义门时拿到的是坐标不成立那条路
        #      的原因码（本分支上总是 `aspect_not_in_topic`，因为 aspect 门先命中）；
        #   ② 键**根本不出现**：这才是 `spec_field_missing` 的真实来源——语义门连字段都取不到，
        #      如实记成缺字段形态，不编一个语义原因码。
        _structural_specs = (
            ({**_fin_base, "target_requirement_id": ""}, "需求 id 为空",
             "缺 target_requirement_id", "aspect_not_in_topic"),
            ({k: v for k, v in _fin_base.items() if k != "topic_id"},
             "topic_id 这个键根本不出现", "缺 topic_id", "spec_field_missing"),
        )
        for spec_row, what, needle, expect_code in _structural_specs:
            # C4 起：这一束的**形状**失败先对**那一批**按 `bsc-1` 反馈一次准确错误（同一批只
            # 纠正一次），纠正那一次再失败才落成整束 typed 拒绝。脚本因此给两次**同一份**非法
            # 响应——第二次就是纠正那一次的回答（与 `test_demo_pack_writer` 结构面反例同一写法：
            # 模型没改对，于是按「再次失败即停」收口）。这里把纠正那一次变成可观测量：调用恰好
            # 两次，且整束仍是**一条**被拒审计（纠正不是「整轮重跑」，也不新增第二束拒绝）。
            _bad = {**fin_forged_plan, "follow_up_needs": [dict(spec_row)]}
            _bad_stub = _StubLlm(_bad, _bad)
            try:
                PW.write_section(
                    fin_task, financial_authority, projection=_projection_for(fin_task),
                    writing_spec=spec, presentation_profile=pprofile,
                    llm_client=_bad_stub, policy=None,
                    dependency_fingerprint=projection.dependency_fingerprint,
                    created_at=STARTED_AT)
            except PW.ProposalSetRejectedError as exc:
                passed += 1
                check(needle in str(exc) and len(exc.rejections) == 1,
                      f"形状不成形的诉求（{what}）必须整束 fail-closed，且异常点名**哪个字段**缺"
                      f"并带出逐束审计（实测消息 {str(exc)[:120]!r} / "
                      f"审计 {len(exc.rejections)} 束）")
                check(len(_bad_stub.calls) == 2,
                      f"形状纠正必须真的发生且**只**发生一次（{what}）：实际生成调用 "
                      f"{len(_bad_stub.calls)} 次（期望 2 次 = 原调用 + 按 `bsc-1` 对那一批的"
                      "一次纠正，不是整轮重跑）")
                check(len(exc.follow_up_untypeable) == 1
                      and exc.follow_up_untypeable[0]["code"] == expect_code
                      and not exc.follow_up_needs,
                      f"不成形的诉求（{what}）必须原样带出（不许因为候选被拒就消失），"
                      "且不得被当成已成立的诉求"
                      f"（期望 {expect_code}，实测 "
                      f"{[r['code'] for r in exc.follow_up_untypeable]}）")
            else:
                failed += 1
                details.append(f"FAIL 形状不成形的补件申请（{what}）必须整束 fail-closed")

        # 协调器侧的**分支终止态**：真实财务节已合法进入相位（上面正向），因此这条守卫只能靠
        # 注入一条带 FollowUpNeed 的财务产物来隔离执行——它必须 typed 终止，既不得静默跳过，
        # 也不得为执行诉求而新建第二套财务研究运行时。注入是**测试侧**的反例探针，不是生产 wire。
        probe_sentence = "（合成反例：财务分支 FollowUpNeed 终止态隔离）"
        probe_aspect = (str(fin_scan.facts[0].aspect_ids[0]) if fin_scan.facts[0].aspect_ids
                        else str(fin_req.aspects[0].aspect_id))
        probe_need = TSCH.FollowUpNeed(
            need_id=TSCH.derive_follow_up_need_id(
                probe_sentence, f"{fin_task.section_id}::{fin_topics[0]}", probe_aspect,
                fin_task.section_id, fin_out.draft.draft_revision),
            need_schema_version=TSCH.FOLLOW_UP_NEED_SCHEMA_VERSION, statement=probe_sentence,
            target_requirement_id=f"{fin_task.section_id}::{fin_topics[0]}",
            topic_id=str(fin_topics[0]), question_id=str(fin_question.question_id),
            aspect_id=probe_aspect, section_id=fin_task.section_id,
            section_draft_revision=fin_out.draft.draft_revision,
            contract_authorized_scope=(str(fin_topics[0]),), requiredness="required",
            expected_source_class="company_industry", budget_hint="",
            writer_identity=str(fin_out.draft.writer_rules_version))
        with patch.object(CW, "_writer_pass",
                          return_value=dataclasses.replace(fin_out,
                                                          follow_up_needs=(probe_need,))):
            fin_fu_error = fails(
                lambda: CW.run_backbone_writer_phase(
                    (fin_input,), llm_client=_StubLlm(),
                    entailment_llm_client=_StubEntailmentClient(_ENTAILED),
                    final_sentence_llm_client=_stub_final_sentence_client(),
                    store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT),
                CW.BackboneWriterPhaseError, "FollowUpNeed",
                "财务分支上的 FollowUpNeed 必须以 typed 终止态上报（无 Pack successor 能力）")
        check("Pack successor" in fin_fu_error and CW.FINANCIAL_PRODUCER_KIND in fin_fu_error
              and "AttributeError" not in fin_fu_error,
              "终止态理由必须点名缺失的能力与本分支的生产者类别，且不得退化成 AttributeError")
        note("§二 D 财务分支：同一条主链在真实财务权威上贯通（"
             f"候选/proposal {len(fin_candidates)}、aggregate {len(fin_out.aggregate_decisions)}、"
             f"entailment {len(fin_out.entailment_decisions)}、accepted binding "
             f"{len(fin_bindings)}、Claim {len(fin_out.claims)}、段落 {len(fin_paragraphs)}、"
             f"composed 句 {len(fin_composed)}、表格 {len(fin_tables)}（行 {len(fin_rows)}，"
             f"承载 Claim {len(fin_tabled_ids)} / {len(fin_out.claims)}）、"
             f"Result {fin_out.result.section_result_id}）。"
             "本 NOTE 只声明「类型存在 + focused 通过」：**持久化**（`SectionChainV2` 事务与回读）"
             "与**正式 create-only 验收 runner**（`evaluation/run_m930_3_acceptance.py`）已在本批"
             "实现并有独立产物目录，因此「真实链贯通」由那份 runner 的 A1–A6 承担，不由本文件的"
             "合成/真实替身断言承担；本 NOTE 仍**不**声明「人读内容通过」。")

        # ---------------- 3. 组装边界（真实三段 scope） ----------------------------
        scope3 = tuple(RA.ScopeRequirement(
            section_id=t.section_id, title=t.title, topic_ids=tuple(t.topic_ids))
            for t in sorted(tasks.values(), key=lambda x: x.section_id))
        by_section = {t.section_id: t for t in projection.report_plan.section_tasks}
        check([r.section_id for r in scope3] == sorted(by_section),
              "真实 DemoScope 的 scope 是冻结投影 section 集合的忠实重复（顺序确定）")
        check(all(r.title == by_section[r.section_id].title
                  and tuple(r.topic_ids) == tuple(by_section[r.section_id].topic_ids)
                  for r in scope3),
              "真实 scope 的标题与 topic 集合逐字等于冻结投影（内容单位不得改写）")
        check(len(scope3) == 3 and {r.section_id for r in scope3}
              == {"company", "financial", "industry"},
              "真实 DemoScope 恰好三段；因此「两节组装」不可能是忠实 DemoScope")

        def _version_inputs(items) -> RA.ReportVersionInputs:
            return RA.ReportVersionInputs(
                scope_input_fingerprint=manifest.scope_input_fingerprint,
                plan_id=projection.plan_id,
                selected_task_ids=tuple(sorted(i.task.task_id for i in items)))

        fails(
            lambda: RA.assemble_report(projection=projection, scope=scope3,
                                       section_inputs=(), version_inputs=_version_inputs(()),
                                       generated_at=STARTED_AT),
            RA.ReportAssemblerError, "没有任何章节输入",
            "没有章节输入不得组装报告（fail-closed，不出空报告）")
        # `_verify_scope_authority` 先于逐项复核运行，因此这里的探针项字段不被读取；
        # 它只用来把「scope 必须与冻结投影集合守恒」这一门单独测出来。
        scope_probe = RA.SectionAssemblyInput(
            task=company_task, authority=company_authority, draft=None, gate_result=None,
            aggregate_decisions=(), entailment_decisions=(), acceptance=None, claims=(),
            narrative=None, claim_narrative_dispositions=(), dispositions=(), result=None,
            evaluation=None, binding=None)
        fails(
            lambda: RA.assemble_report(
                projection=projection,
                scope=tuple(r for r in scope3 if r.section_id != "financial"),
                section_inputs=(scope_probe,), version_inputs=_version_inputs((scope_probe,)),
                generated_at=STARTED_AT),
            RA.ReportAssemblerError, "scope 与冻结投影的 section 集合不守恒",
            "把真实 DemoScope 缩成两节必须被拒（缺 financial 即不守恒，缺口不得靠改 scope 绕过）")
        note("§3 结论（如实上报）：本模块**不**在真实三段上跑组装——真实三段的组装（含 §三 E 的"
             "持久化回读与跨节 gap 投影）由 create-only 验收 runner "
             "`evaluation/run_m930_3_acceptance.py` 承担，本文件不重复它的结论。这里钉住的是四面：没有"
             "章节输入不得出空报告、真实 scope 必须与冻结投影守恒（缺口不得靠缩窄 scope 绕过）、"
             "组装器的身份/保留/缺口守恒规则另有 `test_demo_report_assembler.py` 在合成权威上"
             "覆盖。**注意结论已经变化**：company/industry 在本模块的替身计划下**已能定稿**"
             "（正文 = 标题 + 缺口附录，状态 COMPLETED_WITH_GAPS，见 §2），因此「三段组装不可达」"
             "不再是本模块的结论；但「能定稿」既不等于「能组装」（那要回读重算），更不等于"
             "「人读内容通过」——四类结论必须分开写。")

        # ---------------- 3.5 §三 F：有界重写的**上界**（第二轮仍发 FollowUpNeed）--------
        # `test_demo_backbone_writer_phase` §6 的缺口 1 说明：那条上界守卫在**合成** Pack set
        # 上不可驱动（读门会用冻结依赖指纹重算，合成 Pack 必然 block）。这里用真库 + 真
        # runtime + 真 Requirement 把它补上，一次跑完「第 0 轮发出 → 唯一入口裁决并提交后继
        # Pack → 基于新 Pack 再写一轮 → 第二轮仍发出 ⇒ typed 终止」。旧 Pack 不回写、旧 Draft
        # 不落库都不靠本段证明（那两条各有专门反例），本段只钉上界。
        company_reqs = tuple(reqs[t] for t in company_task.topic_ids)
        target_req = company_reqs[0]
        base_pack = next(p for p in pack_sets["company"].packs
                         if str(p.topic_id) == str(target_req.topic_id))
        # 目标 aspect **数据驱动**选出：优先取 base Pack 里真有材料的那条（只有它能让本轮真的
        # 再走一次树工具）；退路是该需求的第一条。不写死任何主题 / 页码 / aspect 名。
        material_hit = next((r for r in base_pack.aspect_results
                             if tuple(getattr(r, "material_ids", ()) or ())), None)
        target_aspect = next(
            (a for a in target_req.aspects
             if material_hit is not None and str(a.aspect_id) == str(material_hit.aspect_id)),
            target_req.aspects[0])
        follow_spec = {
            "statement": "现有材料不足以覆盖该事项的当前期间要求，需在同一授权范围内补取。",
            "target_requirement_id": str(
                topic_scans["company"].requirement_ids[target_req.topic_id]),
            "topic_id": str(target_req.topic_id),
            "question_id": str(target_aspect.question_id),
            "aspect_id": str(target_aspect.aspect_id),
            "requiredness": "required",
            "expected_source_class": TSCH.SOURCE_CLASSES[0],
            "budget_hint": "tree_inspect:1"}
        follow_plan = {"claim_candidates": [], "narrative_draft_units": [],
                       "follow_up_needs": [follow_spec]}
        before_ids = tuple(str(p.pack_id) for p in PSet.resolve_pack_set(
            company_task, company_reqs, reader).packs)
        bounded_stub = _StubLlm(follow_plan, follow_plan)
        try:
            CW.run_backbone_writer_phase(
                (_section_input("company", company_task, company_authority),),
                llm_client=bounded_stub,
                entailment_llm_client=_StubEntailmentClient(_ENTAILED),
                final_sentence_llm_client=_stub_final_sentence_client(),
                store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT,
                material_resolver=session.payload_resolver,
                dependencies_of=lambda req, ctx: _deps(req, ctx))
        except CW.BackboneWriterPhaseError as exc:
            check("FollowUpNeed" in str(exc) and "有界重写" in str(exc),
                  f"第二轮仍发出 FollowUpNeed 必须以 typed 终止态上报（得到 {str(exc)[:140]}）")
        else:
            check(False, "第二轮仍发出 FollowUpNeed 必须 typed 终止（不得进入无界返修）")
        after_ids = tuple(str(p.pack_id) for p in PSet.resolve_pack_set(
            company_task, company_reqs, reader).packs)
        check(after_ids != before_ids and len(after_ids) == len(before_ids),
              "第 0 轮的 FollowUpNeed 必须真的经唯一入口执行并提交**后继** Pack："
              "只有如此，「第二轮」才是「基于新 Pack 的再一轮」而不是同一套 Pack 上的重复调用")
        check(len(bounded_stub.calls) >= 2,
              f"相位必须真的发起第二轮写作（实测生成调用 {len(bounded_stub.calls)} 次）")
        note("§三 F 上界：第 0 轮 FollowUpNeed 已提交后继 Pack（"
             f"{before_ids} → {after_ids}），第二轮仍发出同一条诉求 ⇒ 相位 typed 终止；"
             "本段**不**声明后继的 aspect/question 完整性（那由 `test_demo_pack_set` §6f 承担）")

        # ---------------- 3.6 §九：非首 topic 的 FollowUp 必须按**它自己**的 topic 解析 -----
        # §3.5 的目标是首个 topic。这里把同一件事放在**非首** topic 上再证一次，并把「用了谁的
        # 运行时依赖」变成可观测量：`dependencies_of` 的每次调用都被记录，于是「拿 requirements[0]
        # 的依赖去执行第二条诉求」会当场显形，而不是只靠读代码相信。旧 Pack / 旧权威对象逐 id
        # 不动也在这里一并核（那两条另有专门反例，本段只钉「这次 FollowUp 没碰它们」）。
        check(len(company_reqs) >= 2,
              f"company 节必须有 ≥2 条真实 topic 才能测「非首 topic 的 FollowUp」"
              f"（实际 {len(company_reqs)} 条）——这是真实范围断言，不得静默跳过")
        target_req2 = company_reqs[1] if len(company_reqs) >= 2 else None
        if target_req2 is None:
            note("§3.6 未执行：真实 company 节没有第二条 topic，非首 topic 的 FollowUp 正例"
                 "在本样本上不可驱动（如实上报，不用首 topic 顶替）")
        else:
            deps_calls: list[dict] = []

            def _recording_deps(requirement, run_context):
                """记录「哪条 requirement 的运行时依赖被解析」，再把同一套真实依赖交给 Harness。"""
                deps_calls.append({
                    "requirement_id": TR.topic_requirement_id(requirement),
                    "topic_id": str(requirement.topic_id),
                    "aspect_ids": tuple(str(a.aspect_id) for a in requirement.aspects)})
                return _deps(requirement, run_context)

            pre_set2 = PSet.resolve_pack_set(company_task, company_reqs, reader)
            pre_ids2 = {str(p.topic_id): str(p.pack_id) for p in pre_set2.packs}
            target_topic2 = str(target_req2.topic_id)
            target_pack2 = next(p for p in pre_set2.packs
                                if str(p.topic_id) == target_topic2)
            material_hit2 = next((r for r in target_pack2.aspect_results
                                  if tuple(getattr(r, "material_ids", ()) or ())), None)
            # 目标 aspect 仍是**数据驱动**选的（优先取该 topic 的 current Pack 里真有材料的那条，
            # 只有它能让这轮真的再走一次树工具）；不写死任何主题 / 页码 / aspect 名字。
            target_aspect2 = next(
                (a for a in target_req2.aspects
                 if material_hit2 is not None
                 and str(a.aspect_id) == str(material_hit2.aspect_id)),
                target_req2.aspects[0])
            follow_spec2 = {
                "statement": "现有材料不足以覆盖该事项的当前期间要求，需在同一授权范围内补取。",
                "target_requirement_id": str(
                    topic_scans["company"].requirement_ids[target_req2.topic_id]),
                "topic_id": target_topic2,
                "question_id": str(target_aspect2.question_id),
                "aspect_id": str(target_aspect2.aspect_id),
                "requiredness": "required",
                "expected_source_class": TSCH.SOURCE_CLASSES[0],
                "budget_hint": "tree_inspect:1"}
            follow_plan2 = {"claim_candidates": [], "narrative_draft_units": [],
                            "follow_up_needs": [follow_spec2]}
            # §3.5 之后首 topic 的 current 已是它的后继，所以这里的权威必须**重新**从 current
            # 解析（这正是 composition root 每轮要做的事，不是测试专用的凑法）。
            authority2 = PW.TopicPackAuthorityInput.create(
                company_task, pre_set2, company_id=company, report_as_of=report_as_of,
                **_dict2k(_contract_identity(company_task)))
            frozen_authority_ids = tuple(str(p.pack_id) for p in authority2.pack_set.packs)
            section_input2 = _section_input(
                "company", company_task, authority2,
                run_id="m930-3-writer-company-nonfirst")
            # 第 0 轮只发诉求；第 1 轮换成「本样本上合法的缺口形态」计划（空候选束 ⇒ 如实缺口）。
            # 正例的关键不是这条链跑完，而是它跑完时后继落在**第二条** topic 上。
            stub2 = _StubLlm(follow_plan2, topic_plans["company"])
            phase2 = CW.run_backbone_writer_phase(
                (section_input2,), llm_client=stub2,
                entailment_llm_client=_StubEntailmentClient(_ENTAILED),
                final_sentence_llm_client=_stub_final_sentence_client(),
                store=reader, created_at=STARTED_AT, evaluated_at=STARTED_AT,
                material_resolver=session.payload_resolver, dependencies_of=_recording_deps)
            section2 = phase2.sections[0]
            check(len(phase2.follow_up_runs) == 1 and section2.rewrite_round == 1,
                  f"非首 topic 的诉求必须真的换来一轮重写并**跑完**（runs="
                  f"{len(phase2.follow_up_runs)} / rewrite_round={section2.rewrite_round}）")
            run2 = phase2.follow_up_runs[0]
            decision2 = run2.decisions[0]
            check(decision2.executed is True and decision2.verdict in ("accepted", "reduced")
                  and run2.new_pack_ids == (decision2.new_pack_id,),
                  f"非首 topic 的 FollowUpNeed 必须经唯一入口真的执行并登记新 Pack"
                  f"（verdict={decision2.verdict!r} executed={decision2.executed} "
                  f"new={run2.new_pack_ids}）")
            check(str(decision2.new_pack_id)
                  and str(decision2.new_pack_id) != pre_ids2[target_topic2],
                  "后继必须是一条**新** Pack（不是把 base 原样登记一次）")
            after_set2 = PSet.resolve_pack_set(company_task, company_reqs, reader)
            after_ids2 = {str(p.topic_id): str(p.pack_id) for p in after_set2.packs}
            check(after_ids2[target_topic2] == str(decision2.new_pack_id),
                  "后继必须落在**该 need 自己的** topic 的 current 上（读门复核后一致）")
            check(all(after_ids2[t] == pre_ids2[t]
                      for t in pre_ids2 if t != target_topic2),
                  "这次 FollowUp 不得改写其它 topic 的 current（旧 Pack 不被回写）")
            check(tuple(str(p.pack_id) for p in authority2.pack_set.packs)
                  == frozen_authority_ids,
                  "第 0 轮的权威对象在本轮之后逐 id 不变（旧 Pack 对象不被原位改写）")
            successor2 = next(p for p in after_set2.packs
                              if str(p.topic_id) == target_topic2)
            check(successor2.identity() == TR.requirement_pack_identity(target_req2)
                  and set(r.aspect_id for r in successor2.aspect_results)
                  == set(target_req2.aspect_ids())
                  and set(successor2.question_ids) == set(target_req2.question_ids)
                  and successor2.dependency_fingerprint
                  == target_req2.dependency_fingerprint(),
                  "后继按**非首 topic 自己**的冻结 requirement 装配（identity / aspect / "
                  "question / 依赖指纹完整）")
            check([c["requirement_id"] for c in deps_calls]
                  == [TR.topic_requirement_id(target_req2)]
                  and all(c["topic_id"] == target_topic2 for c in deps_calls)
                  and all(set(c["aspect_ids"]) == set(target_req2.aspect_ids())
                          for c in deps_calls),
                  f"`dependencies_of` 恰以**该 need 自己的** requirement 被调用一次"
                  f"（实际 {[(c['topic_id'], c['requirement_id']) for c in deps_calls]}）"
                  "——不得对第二 topic 固定复用 requirements[0] 的运行时依赖")
            check(TR.topic_requirement_id(company_reqs[0])
                  not in [c["requirement_id"] for c in deps_calls],
                  "本条诉求的解析路径上不得出现首 topic 的 requirement（正例的反面证据）")
            # §四 1 / §4.6 身份分层：补件换的是**内容/容器**，不是**策略依赖**。同一报告各节
            # 的 `dependency_fingerprint` 是 Contract/SourcePolicy/WritingSpec/PresentationProfile
            # 四类资产指纹的冻结值，必须逐字节相同——操作性的补件运行（run id / trace refs）
            # 属 operational run identity，`AGENTS.md` §3 把它放在三套身份集**之外**，
            # 不得改写它。反过来，后继 Pack 的身份一个字都不能丢：它进 Draft 的
            # `authority_container_ids`（内容身份）与 exact manifest，Pack 集进
            # `ReportVersionInputs.topic_pack_ids`（版本身份）。
            check(section2.draft.dependency_fingerprint
                  == section_input2.dependency_fingerprint
                  and section2.result.dependency_fingerprint
                  == section2.draft.dependency_fingerprint,
                  "留下的这一份 Draft 的依赖指纹必须**仍是注入的冻结策略指纹**（补件是操作事件，"
                  "不是策略资产变更）：Draft="
                  f"{section2.draft.dependency_fingerprint} 注入={section_input2.dependency_fingerprint}")
            check(str(decision2.new_pack_id)
                  in tuple(str(c) for c in section2.draft.authority_container_ids),
                  "后继 Pack 的身份必须落在 Draft 的 authority_container_ids（内容身份）里，"
                  f"而不是靠改写依赖指纹来体现：后继={decision2.new_pack_id} 容器="
                  f"{tuple(str(c) for c in section2.draft.authority_container_ids)}")
            check(not section2.claims and bool(section2.follow_up_run_refs)
                  and all(str(run2.follow_up_run_id) in str(ref)
                          for ref in section2.follow_up_run_refs),
                  "留下的这一份是**基于新 Pack 的第 1 轮**产物：裁决 ref 非空、且每条 ref 都指向"
                  "**它自己**的 `follow_up_run_id`（两次有界执行的 trace 必须可区分）："
                  f"refs={tuple(section2.follow_up_run_refs)} run={run2.follow_up_run_id}")
            note("§3.6 非首 topic FollowUp 正例：target topic="
                 f"{target_topic2}（company_reqs[1]），target aspect="
                 f"{str(target_aspect2.aspect_id)}（数据驱动选中："
                 f"{'该 topic 的 current Pack 里真有材料' if material_hit2 is not None else '退路=首条 aspect'}），"
                 f"base→后继 {pre_ids2[target_topic2]} → {after_ids2[target_topic2]}；"
                 f"`dependencies_of` 调用记录 {[(c['topic_id'], c['requirement_id']) for c in deps_calls]}。"
                 "「第二轮仍发 Need ⇒ typed 终止」这条上界由 §3.5 承担：它在相位里位于逐 topic "
                 "解析**之前**（第 1 轮产物一出诉求即抛），因此与 target topic 是哪一条无关，"
                 "本段不重复跑一遍同一件事。")

        # ---------------- 4. legacy 生产者（P25–P32）不得进入 current 链 -------------
        # §16.10 #36：旧 worker/rework/service 产物是显式 legacy 视图；两条链的 wire、
        # reader、store 与组装入口都不得互相兜底。
        fin_qids = tuple(str(q.question_id) for q in fin_task.questions
                         if str(q.topic_id) == fin_topics[0])[:1]
        legacy_claim = SS.LegacySectionClaimV1(
            claim_id="c_legacy_formal_chain", section_id=fin_task.section_id,
            topic_id=fin_topics[0], question_ids=fin_qids,
            text="（legacy 章节产物示例，不参与任何正文）", claim_type="fact",
            citation_refs=(CitationRef(ref_type="evidence", evidence_id="ev_legacy_fc"),))
        legacy_result = SS.LegacySectionResultV1(
            section_result_id="sr_legacy_formal_chain", section_version="secver_legacy_fc",
            task_id=fin_task.task_id, section_id=fin_task.section_id, status="COMPLETED",
            claims=(legacy_claim,), markdown="# 财务分析（legacy 视图示例）")
        check(isinstance(legacy_result, SS.LegacySectionResultV1)
              and not isinstance(legacy_result, SS.SectionResult)
              and not hasattr(legacy_result, "schema_version")
              and not hasattr(legacy_result, "section_draft_id")
              and all(isinstance(c, SS.LegacySectionClaimV1) for c in legacy_result.claims),
              "legacy 章节产物显式是 `LegacySectionResultV1`/`LegacySectionClaimV1`："
              "既无 result-2 marker，也无 draft 引用（不可能被当成 current 形态）")

        cur_refs = (CitationRef(ref_type="evidence", evidence_id="ev_current_fc",
                                page_number=1),)
        cur_binding_ids = ("ab_current_fc",)
        current_claim = SS.SectionClaim(
            claim_id=SS.derive_claim_id("fact", fin_topics[0], ("q_fc",),
                                        "（current claim-2 示例）", cur_refs,
                                        "cc_current_fc", "rev-1", cur_binding_ids),
            schema_version=SS.CLAIM_SCHEMA_VERSION, section_id=fin_task.section_id,
            topic_id=fin_topics[0], question_ids=("q_fc",), text="（current claim-2 示例）",
            claim_type="fact", citation_refs=cur_refs, claim_candidate_id="cc_current_fc",
            claim_candidate_revision="rev-1", accepted_binding_ids=cur_binding_ids)
        current_result = SS.SectionResult(
            section_result_id="sr_current_fc", schema_version=SS.SECTION_RESULT_SCHEMA_VERSION,
            section_version="secver_current_fc", section_draft_id="sdraft_current_fc",
            task_id=fin_task.task_id, section_id=fin_task.section_id, status="COMPLETED")

        # (a) legacy 产物不得进入 current store（v2 链只接受 current narr-4 形态）。
        fails(lambda: ST.SectionChainV2(draft=legacy_result),
              ST.SectionStorageConflictError, "必须是 current",
              "legacy Result 不得当作 current Draft 进 `SectionChainV2`（legacy 只走 legacy reader）")
        fails(lambda: ST.commit_section_result(current_result),
              ST.SectionStorageConflictError, "只承载 legacy",
              "current result-2 不得经 legacy v1 表落库（否则旧 reader 会返回 current 对象）")
        fails(lambda: ST.commit_section_result(legacy_result.to_dict()),
              ST.SectionStorageConflictError, "LegacySectionResultV1",
              "裸 dict 不得冒充 legacy 视图落库（不按字段猜版本）")

        # (b) 两条链的 wire 单向分流：current 序列化器不吃 legacy，legacy reader 不吃 current。
        fails(lambda: SS.section_result_to_dict(legacy_result),
              SS.SectionSchemaError, "legacy",
              "current `section_result_to_dict` 不得宽松序列化 legacy Result")
        fails(lambda: SS.claim_to_dict(legacy_claim),
              SS.SectionSchemaError, "legacy",
              "current `claim_to_dict` 不得宽松序列化 legacy Claim")
        fails(lambda: SS.load_legacy_section_result_for_audit(
            SS.section_result_to_dict(current_result)),
            SS.SectionSchemaError, "不得经 legacy reader 读回",
            "legacy Result reader 不得读回 current result-2 载荷")
        fails(lambda: SS.load_legacy_claim_claim1_for_audit(SS.claim_to_dict(current_claim)),
              SS.SectionSchemaError, "legacy",
              "legacy Claim reader 不得读回 current claim-2 载荷")
        check(SS.SECTION_RESULT_SCHEMA_VERSION == "section-result-2"
              and SS.SECTION_RESULT_SCHEMA_VERSION not in SS.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS
              and SS.LegacySectionClaimV1.LEGACY_SCHEMA_VERSION == "claim-1",
              "current/legacy 版本域互不相交（claim-1 / result-2 各自登记）")

        # (c) P31 发布态（publishable_report）是 legacy-only：current Claim 进不去。
        fails(lambda: PR.section_publication_to_dict(PR.SectionPublication(
            section_id=fin_task.section_id, title=fin_task.title, publication_status="OK",
            blocking_reasons=(), adopted_claims=(current_claim,), excluded=(),
            key_gaps=(), remaining_gaps=(), body="")),
            PR.PublicationError, "legacy",
            "P31 发布态 legacy 序列化器必须拒 current claim-2")
        check(bool(legacy_claim.to_dict()),
              "P31 发布态消费的 legacy Claim 只经自己的 wire（无需 current 字段）")
        check(not any(hasattr(legacy_claim, name) for name in
                      ("schema_version", "claim_candidate_id", "accepted_binding_ids")),
              "legacy Claim 不含 claim-2 才有的候选/绑定字段（不可能被默认值补成 current）")

        # (d) P32 legacy evaluation runner / P35 legacy Phase-4 runner：current 产物一律拒。
        fails(lambda: R.require_legacy_result(current_result),
              R.SectionEvalRunnerError, "legacy",
              "P32 legacy evaluation runner 必须拒 current result-2")
        check(R.legacy_wire_marker()["claim_schema_version"]
              == SS.LegacySectionClaimV1.LEGACY_SCHEMA_VERSION
              and list(R.legacy_wire_marker()["result_schema_versions"])
              == list(SS.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS),
              "P32 legacy manifest marker 与登记 legacy 版本集一致")
        check(RPD.LEGACY_RESULT_MARKER in SS.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS
              and RPD.LEGACY_RESULT_MARKER != SS.SECTION_RESULT_SCHEMA_VERSION,
              "P35 legacy Phase-4 runner 写出的是 legacy marker（不冒充 result-2）")
        fails(lambda: RPD._require_legacy_section_result(current_result),
              ValueError, "M930",
              "P35 legacy Phase-4 runner 必须拒 current result-2 并指向 M930 current 路径")
        fails(lambda: RPD._require_legacy_section_result(
            SS.section_result_to_dict(current_result)),
            ValueError, "LegacySectionResultV1",
            "P35 legacy runner 不得按字段猜版本（current 载荷 dict 一律拒）")
        # (e) current assembler：legacy Result **没有** draft_id / coverage_summary，
        # legacy Claim **没有**候选与绑定身份，因此填不满 `SectionAssemblyInput` 的任何一项。
        check(not hasattr(legacy_result, "draft_id")
              and not hasattr(legacy_result, "coverage_summary"),
              "legacy Result 不含 draft_id / coverage_summary：组装项的 draft 字段无物可填")
        fails(lambda: RA.assemble_report(
            projection=projection, scope=scope3,
            section_inputs=(RA.SectionAssemblyInput(
                task=company_task, authority=company_authority, draft=legacy_result,
                gate_result=None, aggregate_decisions=(), entailment_decisions=(),
                acceptance=None, claims=(), narrative=None, claim_narrative_dispositions=(),
                dispositions=(), result=legacy_result, evaluation=None, binding=None),),
            version_inputs=_version_inputs(()), generated_at=STARTED_AT),
            RA.ReportAssemblerError, "NarrativeEvaluationBinding",
            "即使 scope 与冻结投影守恒，legacy Result 冒充 current Draft 也过不了组装项的门")

        note("§4 结论：六类 legacy producer（P25–P30）与 P31/P32/P35 的产物在**类型层**就不具备 "
             "current 形态（无 candidate revision / 无 accepted binding / 无 section_draft_id）；"
             "current store、current assembler 与 M930 写作相位的入口都只接受 current 对象，"
             "两条链的 wire/reader 互不兜底。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


def _dict2k(identity: tuple[str, str]) -> dict:
    cv, cf = identity
    return {"contract_version": cv, "contract_fingerprint": cf}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))

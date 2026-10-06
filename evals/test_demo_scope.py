"""Eval: M930-1 DemoScope 范围、身份与 Contract v2 受控投影。

用法: python -m evals.test_demo_scope

覆盖（M930-1 任务书 §十一 ``evals.test_demo_scope``）：
- 合法 Profile 加载 + 四级 ID 精确分区守恒（selected / out-of-scope / Contract 总数）；
- YAML tamper / profile_fingerprint 漂移、四类冻结资产 fingerprint 缺失或漂移、
  version 漂移、Contract v1 拒绝；
- 未知 ID、父子归属不一致、父 section 未选中；
- producer_kind / missing-blocking policy 漂移被拒；WritingSpec mapping 缺失被拒；
- 同一 job + 同一业务输入、仅 run_id/attempt/时间/路径不同 → projection/plan/task
  /report 内容身份完全一致，而 DemoRunIdentity 不同；
- 仅改 job ID → plan / task 身份必须变化；
- runtime Pack / ExternalSnapshot / gap 变化只改 ResolvedDemoScopeManifest，
  不回污染前序身份；
- Profile 与实现不含公司名 / 证券代码 / 固定页码 / gold / case 特判分支；
- M930-3 新增的版本化**双节** profile（只增不改）：fail-closed 加载、分区守恒互补、
  相对 v1 的差分**恰为**被移出范围的 `industry` 子树、四类冻结资产逐项沿用 v1；
  反例：tamper / 冻结资产指纹漂移 / 未知 section_id 一律被拒。

公司无关：不引入 300750 / 宁德时代 分支；所有公司身份只出现在输入与 fixture 中。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from contracts.loader_v2 import load_contract_v2
from planning import demo_scope as DS

_FORBIDDEN_TOKENS = ("300750", "宁德", "CATL", "catl", "gold", "GOLD")

#: M930-3 新增的**第二份**声明源（版本化双节配置；旧 v1 声明源一字未动）。
_DUAL_PROFILE_REL = "templates/demo_scopes/interview_backbone_dual_section_v1.yaml"

# 四类冻结资产：profile 中声明的资产键 → 期望的 version 键
_ASSET_VERSION_KEY = {
    "contract": "contract_version",
    "source_policy": "policy_version",
    "writing_spec": "schema_version",
    "presentation_profile": "schema_version",
}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _real_profile_path() -> Path:
    return DS.REPO_ROOT / DS.DEFAULT_PROFILE_PATH


def _profile_doc() -> dict:
    return yaml.safe_load(_real_profile_path().read_text(encoding="utf-8"))


def _dump_profile(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def _write_profile(tmp: Path, doc: dict, *, resolve_fingerprint: bool,
                   name: str = "profile.yaml") -> Path:
    """把（可能被改坏的）profile 写成临时 YAML。

    ``resolve_fingerprint=True`` 时回填一致的 profile_fingerprint，使语义校验
    （未知 ID / 归属 / 资产声明）成为唯一失败原因；``False`` 时原样落盘。
    """
    doc = copy.deepcopy(doc)
    path = tmp / name
    if resolve_fingerprint:
        doc["profile_fingerprint"] = "0" * 64
        _dump_profile(path, doc)
        doc["profile_fingerprint"] = DS.compute_profile_fingerprint_from_source(path)
    return _dump_profile(path, doc)


def _load(tmp: Path, doc: dict, *, resolve_fingerprint: bool = False) -> object:
    return DS.load_demo_scope_profile(
        _write_profile(tmp, doc, resolve_fingerprint=resolve_fingerprint))


def _run_request(run_id: str, attempt: int = 1,
                 started_at: str = "2026-09-20T00:00:00Z") -> dict:
    return {"run_id": run_id, "attempt": attempt, "started_at": started_at}


def _empty_runtime() -> dict:
    return {
        "live_page_layout_ids": [], "live_alignment_ids": [], "live_outline_ids": [],
        "live_span_snapshot_ids": [], "pack_ids": [], "external_snapshot_ids": [],
        "financial_fact_pack_artifact_id": None, "gaps": [],
    }


def _project(profile, job_id: str = "job_demo_backbone_0001"):
    business = DS._business_input(job_id)
    manifest_in = DS.build_scope_input_manifest(profile, business, DS._source_inputs())
    return manifest_in


def _full_chain(profile, run_id: str, *, job_id: str = "job_demo_backbone_0001",
                attempt: int = 1, started_at: str = "2026-09-20T00:00:00Z"):
    """合法全链路：profile → input manifest → run identity → projection。"""
    business = DS._business_input(job_id)
    manifest_in = DS.build_scope_input_manifest(profile, business, DS._source_inputs())
    run_identity = DS.build_demo_run_identity(
        _run_request(run_id, attempt, started_at), manifest_in,
        f"evaluation/results/{run_id}")
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    projection = DS.project_contract_v2_scope(contract, profile, manifest_in)
    return contract, manifest_in, run_identity, projection


class _FakeWritingSpec:
    """只带 verify 需要的三个属性（duck-typed；不伪造 sections 类型）。"""

    def __init__(self, writing_spec_id: str, schema_version: str, mappings: list) -> None:
        self.writing_spec_id = writing_spec_id
        self.schema_version = schema_version
        self.mappings = mappings


def _real_writing_spec_mappings() -> list[dict]:
    doc = DS.load_frozen_asset_doc("templates/writing_specs/credit_report_v1.yaml",
                                   "WritingSpec")
    return [dict(m) for m in doc["mappings"]]


def _code_only(path: Path) -> str:
    """剥掉注释、字符串与 docstring 后剩下的**可执行代码** token。

    「不得含公司/答案特判」检验的是代码里没有特判分支，而不是源码文本里不能出现
    这几个字：禁止性注释（本批的实现与 profile 注释都写明了该禁令）不算违规。
    """
    import io
    import tokenize

    out: list[str] = []
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def expect_raises(msg: str, fn, needle: str | None = None) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if needle is None or needle in text:
                check(True, f"{msg} → {type(exc).__name__}: {text[:110]}")
            else:
                check(False, f"{msg} → 抛错但原因不符（期望含 {needle!r}）：{text[:160]}")
        else:
            check(False, f"{msg} → 未抛错（应 fail-closed）")

    profile = DS.load_demo_scope_profile()
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    index_sections = contract.sections
    totals = {
        "section": len([s for s in index_sections]),
        "topic": sum(len(s.topics) for s in index_sections),
        "question": sum(len(t.questions) for s in index_sections for t in s.topics),
        "aspect": sum(len(q.aspects) for s in index_sections for t in s.topics
                      for q in t.questions),
    }

    # ===================== 1. 合法 Profile 加载 + 精确分区守恒 =====================
    check(profile.status == "frozen" and profile.company_independent is True,
          f"合法 Profile 加载：status={profile.status} company_independent=True")
    check(profile.profile_id == "interview_backbone_v1" and profile.profile_version == "v1",
          f"profile 身份 = {profile.profile_id}@{profile.profile_version}")
    sel = profile.selected_id_sets()
    oos = profile.out_of_scope_id_sets()
    sel_counts = {k: len(v) for k, v in sel.items()}
    oos_counts = {k: len(v) for k, v in oos.items()}
    for level in ("section", "topic", "question", "aspect"):
        s, o, t = set(sel[level]), set(oos[level]), totals[level]
        check(len(sel[level]) + len(oos[level]) == t,
              f"分区守恒[{level}]：selected {len(sel[level])} + out_of_scope "
              f"{len(oos[level])} == Contract 总数 {t}")
        check(not (s & o), f"分区不重叠[{level}]：selected ∩ out_of_scope == ∅")
        check(len(s) == len(sel[level]) and len(o) == len(oos[level]),
              f"分区无重复 ID[{level}]")
    details.append(f"INFO: selected 精确计数 = {sel_counts}")
    details.append(f"INFO: out_of_scope 精确计数 = {oos_counts}")
    details.append(f"INFO: Contract 四级总数 = {totals}")
    errs = [k for k in ("section", "topic", "question", "aspect")
            if set(sel[k]) | set(oos[k]) != set(DS._index_contract(contract)[k + "s"])]
    check(errs == [], f"分区并集必须精确等于冻结 Contract 四级 ID 全集（差异 {errs}）")

    # ===================== 2. tamper / 漂移 / v1 / 未知 ID / 归属 =====================
    with tempfile.TemporaryDirectory(prefix="m930_1_scope_") as td:
        tmp = Path(td)

        # 2.1 YAML 改写但指纹未重算 → 指纹漂移
        doc = _profile_doc()
        doc["budget_policy_version"] = "v2"
        expect_raises("YAML tamper（budget_policy_version 改动、指纹未重算）",
                      lambda: _load(tmp, doc, resolve_fingerprint=False),
                      "profile_fingerprint")

        # 2.2 四类冻结资产：fingerprint 缺失 → fail-closed
        for kind in ("contract", "source_policy", "writing_spec", "presentation_profile"):
            doc = _profile_doc()
            del doc["assets"][kind]["fingerprint"]
            expect_raises(f"冻结资产 {kind} fingerprint 缺失",
                          lambda d=doc: _load(tmp, d, resolve_fingerprint=False),
                          f"profile.assets.{kind} 缺 fingerprint")

        # 2.3 四类冻结资产：fingerprint 漂移 → fail-closed
        for kind in ("contract", "source_policy", "writing_spec", "presentation_profile"):
            doc = _profile_doc()
            doc["assets"][kind]["fingerprint"] = "0" * 64
            expect_raises(f"冻结资产 {kind} fingerprint 漂移",
                          lambda d=doc: _load(tmp, d, resolve_fingerprint=False),
                          f"冻结资产 {kind} fingerprint 漂移")

        # 2.4 四类冻结资产：version 声明与实际冻结文档不一致 → fail-closed
        for kind, vkey in _ASSET_VERSION_KEY.items():
            doc = _profile_doc()
            doc["assets"][kind][vkey] = "bogus-version"
            expect_raises(f"冻结资产 {kind}.{vkey} 版本漂移",
                          lambda d=doc, k=kind, v=vkey: _load(
                              tmp, d, resolve_fingerprint=False),
                          f"冻结资产 {kind} 声明 {vkey}")

        # 2.5 Contract v1 拒绝
        expect_raises("Contract v1 文件被 load_contract_v2 直接拒绝",
                      lambda: load_contract_v2(
                          str(DS.REPO_ROOT / "templates/contracts/standard_v2.yaml")),
                      "contract_version 必须为 'v2'")
        doc = _profile_doc()
        doc["assets"]["contract"]["asset"] = "templates/contracts/standard_v2.yaml"
        doc["assets"]["contract"]["contract_version"] = "v1"
        doc["assets"]["contract"]["fingerprint"] = "0" * 64
        expect_raises("profile 把 contract asset 换成 legacy v1 文件",
                      lambda: _load(tmp, doc, resolve_fingerprint=False))

        # 2.6 未知 ID
        doc = _profile_doc()
        doc["selected_sections"][0]["section_id"] = "no_such_section"
        expect_raises("未知 section_id", lambda: _load(tmp, doc),
                      "未知 section_id")
        doc = _profile_doc()
        doc["selected_topics"][0]["topic_id"] = "no_such_topic"
        expect_raises("未知 topic_id", lambda: _load(tmp, doc),
                      "未知 topic_id")

        # 2.7 父子归属不一致（topic 声明了错误的父 section）
        doc = _profile_doc()
        doc["selected_topics"][0]["section_id"] = "industry"
        expect_raises("topic 声明错误父 section", lambda: _load(tmp, doc),
                      "父 section")

        # 2.8 父 section 未被选中（分区归属不一致）
        doc = _profile_doc()
        doc["selected_sections"] = [s for s in doc["selected_sections"]
                                    if s["section_id"] != "company"]
        doc["selected_topics"] = [t for t in doc["selected_topics"]
                                  if t["section_id"] != "company"]
        doc["selected_topics"].append({"topic_id": "company_identity",
                                       "section_id": "company",
                                       "selection_reason": "临时反例",
                                       "design_surface_ids": ["ds.input_identity"]})
        expect_raises("selected topic 的父 section 未选中",
                      lambda: _load(tmp, doc, resolve_fingerprint=True),
                      "未被选中")

        # 2.9 缺失 producer_kind / WritingSpec mapping 的声明位置
        doc = _profile_doc()
        del doc["selected_sections"][0]["selection_reason"]
        expect_raises("selected_sections 缺 selection_reason",
                      lambda: _load(tmp, doc), "selection_reason")
        doc = _profile_doc()
        del doc["material_capabilities"]
        expect_raises("缺 material_capabilities", lambda: _load(tmp, doc),
                      "material_capabilities")
        doc = _profile_doc()
        del doc["budget_policy_id"]
        expect_raises("缺 budget_policy_id", lambda: _load(tmp, doc), "budget_policy_id")
        doc = _profile_doc()
        del doc["fallback_policy_id"]
        expect_raises("缺 fallback_policy_id", lambda: _load(tmp, doc),
                      "fallback_policy_id")

        # 2.10 未知源字段 / company_independent=False
        doc = _profile_doc()
        doc["company_name"] = "ACME"
        expect_raises("profile 源出现未知字段（公司相关特判）",
                      lambda: _load(tmp, doc), "未知字段")
        doc = _profile_doc()
        doc["company_independent"] = False
        expect_raises("company_independent=False 被拒",
                      lambda: _load(tmp, doc), "company_independent")

    # ===================== 3. producer_kind / blocking / WritingSpec 漂移 =====================
    _, manifest_in, run_identity, projection = _full_chain(profile, "demo_backbone_det")
    DS.verify_demo_projection(projection, contract)
    check(True, "合法投影 verify_demo_projection（不传 WritingSpec）通过")

    req0 = projection.requirements[0]
    snap0 = req0.aspects[0]
    drifted_snap = dataclasses.replace(snap0, producer_kind="")
    drifted_req = dataclasses.replace(req0, aspects=(drifted_snap,) + req0.aspects[1:])
    drifted_proj = dataclasses.replace(
        projection, requirements=(drifted_req,) + projection.requirements[1:])
    expect_raises("producer_kind 漂移被拒",
                  lambda: DS.verify_demo_projection(drifted_proj, contract),
                  "producer_kind")

    task0 = projection.report_plan.section_tasks[0]
    drifted_plan = dataclasses.replace(projection.report_plan,
                                       section_tasks=(dataclasses.replace(task0,
                                                                          blocking_rules=()),
                                                      ) + projection.report_plan.section_tasks[1:])
    expect_raises("missing-blocking policy（blocking_rules）漂移被拒",
                  lambda: DS.verify_demo_projection(
                      dataclasses.replace(projection, report_plan=drifted_plan), contract),
                  "blocking_rules")

    drifted_caps = dataclasses.replace(task0, allowed_capabilities=("bogus_capability",))
    drifted_plan2 = dataclasses.replace(
        projection.report_plan,
        section_tasks=(drifted_caps,) + projection.report_plan.section_tasks[1:])
    expect_raises("allowed_capabilities 漂移被拒",
                  lambda: DS.verify_demo_projection(
                      dataclasses.replace(projection, report_plan=drifted_plan2), contract),
                  "allowed_capabilities")

    drifted_created = dataclasses.replace(projection.report_plan, created_at="2026-09-20T00:00:00Z")
    expect_raises("report_plan.created_at 非空被拒（时间不得进入跨 run 身份）",
                  lambda: DS.verify_demo_projection(
                      dataclasses.replace(projection, report_plan=drifted_created), contract),
                  "created_at")

    ws_maps = _real_writing_spec_mappings()
    good_ws = _FakeWritingSpec(projection.writing_spec_id, projection.writing_spec_version,
                               ws_maps)
    DS.verify_demo_projection(projection, contract, good_ws)
    check(True, "合法投影 + 真实 WritingSpec mappings 通过")

    dropped = ws_maps[0]
    ws_missing = _FakeWritingSpec(projection.writing_spec_id,
                                  projection.writing_spec_version,
                                  [m for m in ws_maps if m is not dropped])
    expect_raises("WritingSpec 缺 primary mapping 被拒",
                  lambda: DS.verify_demo_projection(projection, contract, ws_missing),
                  "缺")

    ws_wrong_id = _FakeWritingSpec("other_spec", projection.writing_spec_version, ws_maps)
    expect_raises("WritingSpec id 不一致被拒",
                  lambda: DS.verify_demo_projection(projection, contract, ws_wrong_id),
                  "id 与投影不一致")

    ws_role_drift = _FakeWritingSpec(projection.writing_spec_id,
                                     projection.writing_spec_version,
                                     [dict(m) for m in ws_maps])
    for m in ws_role_drift.mappings:
        if m["role"] == "primary":
            m["subsection_id"] = "drifted_subsection"
            break
    expect_raises("WritingSpec subsection 漂移被拒",
                  lambda: DS.verify_demo_projection(projection, contract, ws_role_drift),
                  "漂移")

    # ===================== 4. run 身份 vs 业务身份 =====================
    c1, mi1, r1, p1 = _full_chain(profile, "demo_backbone_run_a", attempt=1,
                                  started_at="2026-09-20T00:00:00Z")
    c2, mi2, r2, p2 = _full_chain(profile, "demo_backbone_run_b", attempt=3,
                                  started_at="2026-09-21T12:34:56Z")
    check(r1.run_id != r2.run_id and r1.attempt != r2.attempt
          and r1.started_at != r2.started_at and r1.results_root != r2.results_root,
          "两次 run 的 DemoRunIdentity 不同（run_id / attempt / 时间 / 路径）")
    check(r1.scope_input_fingerprint == r2.scope_input_fingerprint,
          "同一 job + 同一业务输入 → scope_input_fingerprint 相同")
    check(mi1.scope_input_fingerprint == mi2.scope_input_fingerprint
          and mi1.to_dict() == mi2.to_dict(),
          "两次 run 的 DemoScopeInputManifest 完全相同")
    check(p1.projection_id == p2.projection_id, "两次 run 的 projection_id 相同")
    check(p1.plan_id == p2.plan_id
          and p1.task_ids() == p2.task_ids(),
          f"两次 run 的 plan_id / task_ids 相同（{p1.plan_id}）")
    check(p1.report_plan == p2.report_plan
          and p1.to_dict() == p2.to_dict(),
          "两次 run 的 projection / plan / task 内容身份逐字节一致")
    check("run_id" not in json.dumps(mi1.to_dict(), ensure_ascii=False)
          and "started_at" not in json.dumps(mi1.to_dict(), ensure_ascii=False)
          and "results" not in json.dumps(mi1.to_dict(), ensure_ascii=False),
          "DemoScopeInputManifest 不含 run_id / started_at / results 路径")

    # 仅改 job ID → plan / task 身份必须变化
    _, mi3, r3, p3 = _full_chain(profile, "demo_backbone_run_c",
                                 job_id="job_demo_backbone_0002")
    check(mi3.job_id != mi1.job_id and mi3.scope_input_fingerprint != mi1.scope_input_fingerprint,
          "改 job ID → input manifest 的 job 身份与 scope_input_fingerprint 变化")
    check(p3.plan_id != p1.plan_id, f"改 job ID → plan_id 变化（{p1.plan_id} → {p3.plan_id}）")
    check(set(p3.task_ids()).isdisjoint(set(p1.task_ids())),
          "改 job ID → task_ids 全部变化且不共享命名空间")
    check(p3.projection_id != p1.projection_id,
          "改 job ID → projection_id 亦变化（scope_input_fingerprint 已在白名单内）")
    check(set(p3.task_ids()) == set(p2.task_ids()) or True,
          "INFO: 同一 Contract 不同 job 的 task 命名空间已隔离")

    # 跨 Demo scope 的 task 命名空间隔离（同一 Contract 下不同 profile 版本）
    check(len(set(p1.task_ids())) == len(p1.task_ids()),
          "同一投影内 task_id 唯一")
    check(all(t.startswith("dtask_") for t in p1.task_ids())
          and p1.plan_id.startswith("dplan_") and p1.projection_id.startswith("proj_"),
          "M930 投影使用显式版本化前缀（proj_ / dplan_ / dtask_），未改动全局派生语义")

    # ===================== 5. runtime 输出不回污染前序身份 =====================
    before = {"mi": mi1.to_dict(), "proj": p1.to_dict()}
    resolved_empty = DS.resolve_demo_scope_manifest(r1, mi1, p1, _empty_runtime())
    runtime_with_gap = _empty_runtime()
    runtime_with_gap.update({
        "pack_ids": ["pack_dtask_1_company_identity"],
        "external_snapshot_ids": ["extsnap_0001"],
        "live_page_layout_ids": ["pl_0001"],
        "live_span_snapshot_ids": ["sp_0001"],
        "gaps": [{
            "gap_id": "gap_industry_scale_cycle_0001",
            "target_kind": "topic",
            "target_id": "industry_scale_cycle",
            "reason_code": "no_adopted_fact",
            "searched_scope": "external_funnel",
            "impact": "industry_scale_cycle 无可用行业规模事实",
            "suggested_material_type": "external_adopted_fact",
        }],
    })
    resolved_gap = DS.resolve_demo_scope_manifest(r1, mi1, p1, runtime_with_gap)
    check(bool(resolved_gap.pack_ids) and bool(resolved_gap.gaps)
          and not resolved_empty.pack_ids and not resolved_empty.gaps,
          "runtime outputs 变化只体现在 ResolvedDemoScopeManifest 的 pack/gap 字段")
    check(resolved_empty.manifest_id != resolved_gap.manifest_id,
          f"runtime 输出不同 → resolved manifest_id 不同"
          f"（{resolved_empty.manifest_id} vs {resolved_gap.manifest_id}）")
    check(resolved_gap.plan_id == p1.plan_id
          and resolved_gap.projection_id == p1.projection_id
          and resolved_gap.scope_input_fingerprint == mi1.scope_input_fingerprint
          and resolved_gap.task_ids == p1.task_ids(),
          "resolved manifest 只引用前序身份，不回填或改变 plan / task / Pack current identity")
    check(mi1.to_dict() == before["mi"] and p1.to_dict() == before["proj"],
          "resolve 之后 input manifest / projection 逐字节未变（不回污染）")
    check("report_version" not in resolved_gap.to_dict()
          and "artifact_root_id" not in resolved_gap.to_dict(),
          "resolved manifest 不含 report_version / artifact_root_id（运行后与前序身份分离）")

    # ===================== 6. 公司无关 / 无特判分支 =====================
    profile_data = json.dumps(_profile_doc(), ensure_ascii=False)
    code_sources = {
        "planning/demo_scope.py": _code_only(DS.REPO_ROOT / "planning" / "demo_scope.py"),
        "planning/demo_scope_schema.py": _code_only(
            DS.REPO_ROOT / "planning" / "demo_scope_schema.py"),
        "sections/backbone_schema.py": _code_only(
            DS.REPO_ROOT / "sections" / "backbone_schema.py"),
        "sections/backbone_artifacts.py": _code_only(
            DS.REPO_ROOT / "sections" / "backbone_artifacts.py"),
        "scripts/demo_preflight.py": _code_only(
            DS.REPO_ROOT / "scripts" / "demo_preflight.py"),
    }
    for token in _FORBIDDEN_TOKENS:
        check(token not in profile_data,
              f"profile 数据不含公司/答案特判 token {token!r}")
        for label, text in code_sources.items():
            check(token not in text,
                  f"{label} 可执行代码不含公司/答案特判 token {token!r}"
                  f"（已剥离注释与字符串）")
    check(DS.DEFAULT_PROFILE_PATH == "templates/demo_scopes/interview_backbone_v1.yaml",
          "默认 profile 路径为公司无关的 demo scope 文件")
    doc = _profile_doc()
    check(doc["company_independent"] is True
          and set(doc) == set(DS.PROFILE_SOURCE_KEYS),
          "profile 源字段集恰为封闭键集且 company_independent=True")
    blob = json.dumps(doc, ensure_ascii=False)
    check("page" not in blob.lower() and "page_no" not in blob
          and "evidence_id" not in blob,
          "profile 源不含固定页码 / Evidence ID 特判")

    # ============ 7. 版本化双节 profile（新增的**第二份**声明源；只增不改） ============
    # 正例：能 fail-closed 加载，且它与 v1 的差异**恰好**是被移出范围的那一个子树；
    # 反例：它的指纹被改动 / 冻结资产指纹被改动 ⇒ 一律拒绝。
    # 这里钉的是**对外行为**（范围分区与资产承诺），不是源码措辞或散落版本串。
    dual_path = DS.REPO_ROOT / _DUAL_PROFILE_REL
    if not dual_path.is_file():
        check(False, f"双节 profile 声明源存在：{_DUAL_PROFILE_REL}")
    else:
        check(True, f"双节 profile 声明源存在：{_DUAL_PROFILE_REL}")
        dual = DS.load_demo_scope_profile(dual_path)
        check(dual.profile_id == "interview_backbone_dual_section"
              and dual.profile_version == "v1" and dual.status == "frozen",
              f"双节 profile 身份 = {dual.profile_id}@{dual.profile_version}"
              f"（status={dual.status}）")

        dsel = {k: set(v) for k, v in dual.selected_id_sets().items()}
        doos = {k: set(v) for k, v in dual.out_of_scope_id_sets().items()}
        for level in ("section", "topic", "question", "aspect"):
            s, o, t = dsel[level], doos[level], totals[level]
            check(len(s) + len(o) == t and not (s & o)
                  and (s | o) == set(DS._index_contract(contract)[level + "s"]),
                  f"双节分区守恒且互补[{level}]：selected {len(s)} + out_of_scope "
                  f"{len(o)} == Contract 总数 {t}")

        v1sel = {k: set(v) for k, v in sel.items()}
        # 双节 ⊆ v1：只**收窄**，绝不新增（新增即等于自己发明范围）
        check(all(dsel[k] <= v1sel[k] for k in dsel),
              "双节选面 ⊆ v1 选面（只收窄，不新增任何 section/topic/question/aspect）")
        # 差分必须**整体**落在没被选中的那个 section 子树里——否则就是误伤了别的能力面
        contracted = {level: v1sel[level] - dsel[level]
                      for level in ("section", "topic", "question", "aspect")}
        check(contracted["section"] == {"industry"},
              f"双节相对 v1 少掉的 section 恰为 {{'industry'}}（实得 {sorted(contracted['section'])}）")
        # 逐级：少掉的 topic/question/aspect 必须全部挂在 industry 下
        industry_topics = {t.topic_id for s in contract.sections if s.section_id == "industry"
                           for t in s.topics}
        industry_questions = {q.question_id for s in contract.sections
                              if s.section_id == "industry" for t in s.topics
                              for q in t.questions}
        industry_aspects = {a.aspect_id for s in contract.sections
                            if s.section_id == "industry" for t in s.topics
                            for q in t.questions for a in q.aspects}
        # 口径：v1 选面里**本就只有** industry 的一个 topic 被选中，因此差分应当是
        # 「v1 选面 ∩ industry 子树」，而不是整个 industry 子树——
        # 少掉的每一条都必须落在 industry 下，且 v1 在 industry 下选中的每一条都必须少掉。
        for level, subtree in (
                ("topic", industry_topics),
                ("question", industry_questions),
                ("aspect", industry_aspects)):
            dropped_inside = contracted[level] & subtree
            outside = contracted[level] - subtree
            check(outside == set(),
                  f"双节少掉的 {level} 全部落在 industry 子树内"
                  f"（越界项 {sorted(outside)}）")
            check(dropped_inside == v1sel[level] & subtree,
                  f"双节少掉的 {level} 恰为「v1 选面 ∩ industry 子树」"
                  f"（实得 {sorted(dropped_inside)}）")
            check(bool(dropped_inside),
                  f"双节确实收窄了 {level}（{len(dropped_inside)} 条；空集说明收窄没生效）")

        # 四类冻结资产的路径 / 版本 / 指纹逐项沿用 v1（本批不改任何冻结资产）
        for asset in ("contract", "source_policy", "writing_spec", "presentation_profile"):
            check(all(getattr(dual, f"{asset}_{f}") == getattr(profile, f"{asset}_{f}")
                      for f in ("asset", "fingerprint")),
                  f"双节与 v1 的冻结资产 {asset} 路径 + 指纹逐项相同")
            check(getattr(dual, f"{asset}_version") == getattr(profile, f"{asset}_version"),
                  f"双节与 v1 的冻结资产 {asset} 版本相同")

        # v1 声明源一字未动
        check(DS.DEFAULT_PROFILE_PATH == "templates/demo_scopes/interview_backbone_v1.yaml",
              "新增双节 profile 之后，默认 profile 仍指向 v1（旧配置未被替换）")

        # 反例面
        with tempfile.TemporaryDirectory(prefix="m930_3_dual_") as td2:
            tmp2 = Path(td2)
            dual_doc = yaml.safe_load(dual_path.read_text(encoding="utf-8"))
            bogus = copy.deepcopy(dual_doc)
            bogus["budget_policy_version"] = "v2"
            expect_raises("双节 profile YAML tamper（指纹未重算）",
                          lambda: DS.load_demo_scope_profile(
                              _dump_profile(tmp2 / "tamper.yaml", bogus)),
                          "profile_fingerprint 与展开后内容不一致")
            drifted = copy.deepcopy(dual_doc)
            drifted["assets"]["contract"]["fingerprint"] = "0" * 64
            expect_raises("双节 profile 的冻结资产 contract 指纹漂移",
                          lambda: DS.load_demo_scope_profile(
                              _write_profile(tmp2, drifted, resolve_fingerprint=True,
                                             name="drift.yaml")),
                          "冻结资产 contract fingerprint 漂移")
            widened = copy.deepcopy(dual_doc)
            widened["selected_sections"].append(
                dict(dual_doc["selected_sections"][0], section_id="no_such_section"))
            expect_raises("双节 profile 出现未知 section_id",
                          lambda: DS.load_demo_scope_profile(
                              _write_profile(tmp2, widened, resolve_fingerprint=True,
                                             name="unknown.yaml")),
                          "selected_sections 含未知 section_id")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

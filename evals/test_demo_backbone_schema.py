"""Eval: M930-1 Demo Backbone 状态、报告版本与治理快照 schema。

用法: python -m evals.test_demo_backbone_schema

覆盖（M930-1 任务书 §十一 ``evals.test_demo_backbone_schema``）：
- 严格解码 / 未知字段拒绝 / 各类型 round-trip；
- 报告形成**前**不能伪造 report-bound 状态（RunProgressSnapshot 无 report_version 字段，
  且不能编造空 ReportVersionIdentity 凑满四状态）；
- 四状态互相不可推导（四个独立类型 + 两个方向的非蕴含反例）；
- human acceptance 默认 ``NOT_REVIEWED`` 且本批无任何更改入口；
- formal closure 与 report version / Assurance 正交；
- 运行身份变化不改变 report version；正文 / Pack / 关键 schema 版本变化必须改变
  report version；
- 旧 report version 不能被套用到新内容；
- report version 白名单与禁入名单不相交，且禁入字段逐项不得进入指纹；
- DemoBackboneRunManifest 的 report_version_identity 与 status 必须同时存在或同时为 None；
- DesignSurfaceMatrix 缺项可见、demonstrated 必须有可回查 evidence。

公司无关：不引入公司名 / 证券代码 / 固定页码 / gold 特判。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import demo_scope_schema as DSS
from sections import backbone_schema as BS

_PLAN = "dplan_" + "0" * 24


def _code_only(src: str) -> str:
    """剥掉注释、字符串与 docstring 后剩下的**可执行代码**文本。

    「不得存在产出非默认人工状态的入口」检验的是代码里没有这种分支，而不是源码
    文本里不能出现这几个词：禁止性注释与封闭词表字符串都不算实现。
    """
    import io
    import tokenize

    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def _rvi_base() -> dict:
    """报告版本内容依赖的完整默认值（逐项对应实施计划 §4.6 的每一类）。"""
    return dict(
        profile_fingerprint="1" * 64,
        scope_input_fingerprint="2" * 64,
        projection_id="proj_" + "0" * 24,
        plan_id=_PLAN,
        job_id="job_demo_backbone_0001",
        company_id="company_fixture_a",
        report_as_of="2026-06-30",
        selected_task_ids=("dtask_0001",),
        topic_pack_ids=("pack_dtask_0001_company_identity",),
        financial_fact_pack_artifact_id=None,
        contract_fingerprint="a" * 64,
        source_policy_fingerprint="3" * 64,
        writing_spec_fingerprint="b" * 64,
        presentation_profile_fingerprint="4" * 64,
        dependency_fingerprint="5" * 64,
        body_fingerprint="c" * 64,
        # M930-3 任务二（§4.6）：身份还绑定各节 SectionDraft 身份与「排除自身版本字段后的
        # assembled canonical payload」指纹。
        section_draft_ids=("sdraft_0001",),
        assembled_payload_fingerprint="d" * 64,
        narrative_schema_version="narrative-v1",
        claim_schema_version="claim-v1",
        table_schema_version="table-v1",
        writer_schema_version="writer-v1",
        assembler_schema_version="assembler-v1",
        # P16：判定链规则版本是 report-version v2 的显式 typed 字段（不再靠 schema 版本间接表达）
        claim_binding_gate_version="cbg-1",
        claim_entailment_rules_version="cer-1",
        prompt_version="prompt-v1",
        model_policy_id="demo_model_policy_v1",
    )


def _rvi(**over) -> BS.ReportVersionIdentity:
    base = _rvi_base()
    base.update(over)
    return BS.ReportVersionIdentity.build(**base)


def _rvi_for_scope(scope_manifest, manifest_in, **over) -> BS.ReportVersionIdentity:
    """与已解析 scope manifest 满足顶层身份 DAG 的报告版本。"""
    base = _rvi_base()
    base.update(
        profile_fingerprint=manifest_in.profile_fingerprint,
        scope_input_fingerprint=scope_manifest.scope_input_fingerprint,
        projection_id=scope_manifest.projection_id,
        plan_id=scope_manifest.plan_id,
        job_id=scope_manifest.job_id,
        company_id=scope_manifest.company_id,
        report_as_of=scope_manifest.report_as_of,
        selected_task_ids=tuple(sorted(scope_manifest.task_ids)),
        topic_pack_ids=tuple(sorted(scope_manifest.pack_ids)),
        financial_fact_pack_artifact_id=scope_manifest.financial_fact_pack_artifact_id,
    )
    base.update(over)
    return BS.ReportVersionIdentity.build(**base)


def _closure() -> BS.FormalPhaseClosureSnapshot:
    return BS.FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "open",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-09-20"},
                {"phase_id": "ts5", "status": "blocked",
                 "source_document": "TREE_STRUCTURE_ADJUSTMENT_TASK.md",
                 "recorded_at": "2026-09-19", "note": "TS5 未关闭"}),
        source_documents=({"path": "TREE_STRUCTURE_ADJUSTMENT_TASK.md", "sha256": "9" * 64},
                          {"path": "V2_TODO.md", "sha256": "8" * 64}),
        recorded_at="2026-09-20")


def _real_run_and_scope(source_over: dict | None = None, run_over: dict | None = None,
                        runtime_over: dict | None = None):
    """用 planning 的真实公开 API 造 DemoRunIdentity + ResolvedDemoScopeManifest。

    不手搓 dict / 手工填指纹：manifest 的指纹自证由 planning 侧负责。
    ``source_over`` 用于构造**另一条**合法但 scope_input_fingerprint 不同的链；
    ``run_over`` 只改操作性 run 字段（run_id / attempt / started_at），不改内容输入；
    ``runtime_over`` 只改本轮 runtime output（例如携带 Topic Pack 的链）。
    """
    from contracts.loader_v2 import load_contract_v2
    from planning import demo_scope as DS

    profile = DS.load_demo_scope_profile()
    business = DS._business_input("job_demo_backbone_0001")
    sources = DS._source_inputs()
    sources.update(source_over or {})
    manifest_in = DS.build_scope_input_manifest(profile, business, sources)
    request = {"run_id": "demo_backbone_20260920T000000Z", "attempt": 1,
               "started_at": "2026-09-20T00:00:00Z"}
    request.update(run_over or {})
    run_identity = DS.build_demo_run_identity(
        request, manifest_in, f"evaluation/results/{request['run_id']}")
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    projection = DS.project_contract_v2_scope(contract, profile, manifest_in)
    runtime = {
        "live_page_layout_ids": [], "live_alignment_ids": [], "live_outline_ids": [],
        "live_span_snapshot_ids": [], "pack_ids": [], "external_snapshot_ids": [],
        "financial_fact_pack_artifact_id": None, "gaps": [],
    }
    runtime.update(runtime_over or {})
    scope_manifest = DS.resolve_demo_scope_manifest(run_identity, manifest_in, projection,
                                                    runtime)
    return run_identity, scope_manifest, manifest_in


def _surfaces() -> BS.DesignSurfaceMatrix:
    return BS.DesignSurfaceMatrix.build((
        BS.DesignSurfaceRecord(surface_id="ds.state_separation",
                               axis="design_surface_coverage",
                               title="四状态分离", status="demonstrated",
                               evidence_kind="artifact",
                               evidence_refs=("sections/backbone_schema.py",)),
        BS.DesignSurfaceRecord(surface_id="ds.tree_material",
                               axis="demo_content_coverage",
                               title="树结构材料", status="gap",
                               evidence_kind="gap", limitation="M930-2 未执行"),
    ))


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
                check(False, f"{msg} → 抛错但原因不符（期望含 {needle!r}）：{text[:150]}")
        else:
            check(False, f"{msg} → 未抛错（应 fail-closed）")

    # ===================== 1. 所有权的静态检查 =====================
    mod_src = Path(BS.__file__).read_text(encoding="utf-8")
    check("import planning" in mod_src or "from planning" in mod_src,
          "sections/backbone_schema.py 只以引用方式依赖 planning 对象 ID")
    check("class DemoScopeProfile" not in mod_src
          and "class DemoPlanningProjection" not in mod_src
          and "class DemoRunIdentity" not in mod_src
          and "class ResolvedDemoScopeManifest" not in mod_src
          and "class DemoScopeInputManifest" not in mod_src,
          "本模块不重定义任何 scope/projection/run-identity 类型（唯一所有权在 planning）")
    for forbidden in ("ReportWriter", "Reviewer", "AssuranceController", "NarrativeBuilder"):
        check(f"class {forbidden}" not in mod_src,
              f"本批不实现 {forbidden}")
    owned = {"RunProgressSnapshot", "ReportVersionIdentity", "ProcessCompletionStatus",
             "PreviewAvailabilityStatus", "SystemAssuranceStatus", "HumanAcceptanceStatus",
             "ReportStatusSnapshot", "FormalPhaseClosureSnapshot", "DesignSurfaceRecord",
             "DesignSurfaceMatrix", "DemoBackboneRunManifest"}
    defined = {n for n, o in vars(BS).items()
               if inspect.isclass(o) and o.__module__ == BS.__name__
               and dataclasses.is_dataclass(o)}
    # P16 追加的唯一类型是 legacy report version 的**只读**视图：它没有 build、
    # 不参与 current 决策，故不是第 12 个 current wire 类型。M930-3 任务二把 v1 视图推广成
    # marker 分派的 v1/v2 通用视图（类名随之改），旧名 `LegacyReportVersionIdentityV1`
    # 仍作为别名存在，但别名不是第二个类。
    legacy_readonly = {"LegacyReportVersionIdentity", "LegacyReportVersionIdentityV1"}
    check(defined == owned | legacy_readonly,
          f"本模块定义的 wire 类型恰好为任务书 §八 的 11 个 + legacy 只读视图 1 个"
          f"（多 {sorted(defined - owned - legacy_readonly)}，"
          f"少 {sorted(owned - defined)}）")
    check(BS.LegacyReportVersionIdentityV1 is BS.LegacyReportVersionIdentity,
          "旧名 LegacyReportVersionIdentityV1 仍指向同一个只读视图类")
    check(not hasattr(BS.LegacyReportVersionIdentity, "build")
          and set(BS.LEGACY_REPORT_VERSION_V1_FIELDS)
          == {f for f in BS.LEGACY_REPORT_VERSION_V2_FIELDS
              if f not in ("claim_binding_gate_version",
                           "claim_entailment_rules_version")}
          and not (set(BS.LEGACY_REPORT_VERSION_V2_FIELDS)
                   & {"section_draft_ids", "assembled_payload_fingerprint"}),
          "legacy 只读视图无 build，v1 字段集 = v2 白名单去掉两个判定链规则版本键，"
          "且 v2 不含 v3 新增的两个身份键")

    # ===================== 2. 严格解码 / 未知字段 / round-trip =====================
    rvi = _rvi()
    check(BS.ReportVersionIdentity.from_dict(rvi.to_dict()).to_dict() == rvi.to_dict(),
          "ReportVersionIdentity round-trip 一致")
    bad = dict(rvi.to_dict())
    bad["extra_field"] = 1
    expect_raises("ReportVersionIdentity 未知字段被拒",
                  lambda: BS.ReportVersionIdentity.from_dict(bad), "未知字段")
    bad = dict(rvi.to_dict())
    del bad["body_fingerprint"]
    expect_raises("ReportVersionIdentity 缺字段被拒",
                  lambda: BS.ReportVersionIdentity.from_dict(bad), "缺必填字段")
    bad = dict(rvi.to_dict())
    bad["report_version"] = "rv_" + "f" * 24
    expect_raises("ReportVersionIdentity report_version 非派生值被拒",
                  lambda: BS.ReportVersionIdentity.from_dict(bad), "report_version")

    prog = BS.RunProgressSnapshot(schema_version=BS.RUN_PROGRESS_SCHEMA_VERSION,
                                  run_id="demo_backbone_20260920T000000Z", attempt=1,
                                  phase_id="preflight", phase_status="RUNNING")
    check(BS.RunProgressSnapshot.from_dict(prog.to_dict()).to_dict() == prog.to_dict(),
          "RunProgressSnapshot round-trip 一致")
    bad = dict(prog.to_dict())
    bad["report_version"] = "rv_" + "0" * 24
    expect_raises("RunProgressSnapshot 出现 report_version 字段即被拒",
                  lambda: BS.RunProgressSnapshot.from_dict(bad), "未知字段")
    bad = dict(prog.to_dict())
    bad["phase_status"] = "DONE"
    expect_raises("RunProgressSnapshot.phase_status 非法值被拒",
                  lambda: BS.RunProgressSnapshot.from_dict(bad), "phase_status")

    snap = BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("NOT_RUN"))
    check(BS.ReportStatusSnapshot.from_dict(snap.to_dict()).to_dict() == snap.to_dict(),
          "ReportStatusSnapshot round-trip 一致")
    bad = dict(snap.to_dict())
    del bad["human_acceptance"]
    expect_raises("ReportStatusSnapshot 缺 human_acceptance 被拒",
                  lambda: BS.ReportStatusSnapshot.from_dict(bad), "缺必填字段")

    closure = _closure()
    check(BS.FormalPhaseClosureSnapshot.from_dict(closure.to_dict()).to_dict()
          == closure.to_dict(), "FormalPhaseClosureSnapshot round-trip 一致")
    bad = dict(closure.to_dict())
    bad["derived_by_runtime"] = True
    expect_raises("formal closure derived_by_runtime=True 被拒",
                  lambda: BS.FormalPhaseClosureSnapshot.from_dict(bad),
                  "derived_by_runtime")
    bad = dict(closure.to_dict())
    bad["phases"][0]["status"] = "unknown_status"
    expect_raises("formal closure 非法 phase status 被拒",
                  lambda: BS.FormalPhaseClosureSnapshot.from_dict(bad), "status")

    matrix = _surfaces()
    check(BS.DesignSurfaceMatrix.from_dict(matrix.to_dict()).to_dict() == matrix.to_dict(),
          "DesignSurfaceMatrix round-trip 一致")
    bad = dict(matrix.to_dict())
    bad["records"][0]["surface_id"] = "ds.not_a_surface"
    expect_raises("DesignSurfaceRecord 未知 surface_id 被拒",
                  lambda: BS.DesignSurfaceMatrix.from_dict(bad), "surface_id")
    expect_raises("demonstrated 但 evidence_kind=none 被拒",
                  lambda: BS.DesignSurfaceRecord(
                      surface_id="ds.run_identity", axis="design_surface_coverage",
                      title="x", status="demonstrated", evidence_kind="none",
                      evidence_refs=("a",)), "evidence_kind")

    # ===================== 3. 报告形成前不得伪造 report-bound 状态 =====================
    check("report_version" not in prog.to_dict(),
          "RunProgressSnapshot 是纯 run-bound：没有 report_version 字段可供伪造")
    check(not hasattr(prog, "report_version_identity"),
          "RunProgressSnapshot 不持有 ReportVersionIdentity")
    expect_raises("用空 dict 伪造 ReportVersionIdentity 被拒",
                  lambda: BS.ReportVersionIdentity.from_dict({}), "缺必填字段")
    expect_raises("用空串 plan_id 伪造 report version 被拒",
                  lambda: _rvi(plan_id=""), "plan_id")
    expect_raises("ReportStatusSnapshot 不传真实 identity 被拒",
                  lambda: BS.ReportStatusSnapshot(
                      schema_version=BS.REPORT_STATUS_SCHEMA_VERSION,
                      report_version_identity=None,
                      process_completion=BS.ProcessCompletionStatus("COMPLETED"),
                      preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
                      system_assurance=BS.SystemAssuranceStatus("NOT_RUN"),
                      human_acceptance=BS.HumanAcceptanceStatus()), "ReportVersionIdentity")

    # ===================== 4. 四状态互相不可推导 =====================
    check(len({type(snap.process_completion), type(snap.preview_availability),
               type(snap.system_assurance), type(snap.human_acceptance)}) == 4,
          "四个状态是四个独立类型（不存在单一 status 枚举自动映射）")
    # 反例：preview available 但 assurance 未运行 / failed；process completed 但 assurance failed
    s1 = BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("NOT_RUN"))
    check(s1.preview_availability.status == "AVAILABLE"
          and s1.system_assurance.status == "NOT_RUN",
          "preview available ≠ system assurance passed（可同时 available + NOT_RUN）")
    s2 = BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("FAILED"))
    check(s2.system_assurance.status == "FAILED"
          and s2.human_acceptance.status == "NOT_REVIEWED",
          "system assurance failed ≠ human acceptance（人工状态仍为 NOT_REVIEWED）")
    s3 = BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("BLOCKED"),
        preview_availability=BS.PreviewAvailabilityStatus("UNAVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("NOT_RUN"))
    check(s3.report_version == s2.report_version == s1.report_version,
          "四状态取值不同但绑定同一 report version（状态不派生报告版本）")
    expect_raises("四状态共享同一 identity：不能只改其中一个的版本",
                  lambda: BS.ReportStatusSnapshot.from_dict({
                      **snap.to_dict(),
                      "report_version_identity": _rvi(body_fingerprint="d" * 64).to_dict(),
                  }), "report_version")

    # ===================== 5. 四状态词表完整 + 人工状态 wire 可读写 =====================
    # 四状态词的完整封闭词表（P1-1）：wire 必须能读写未来独立人工流程的结论，
    # 但不得存在任何按其它三轴自动推导人工状态的业务入口。
    check(BS.PREVIEW_AVAILABILITY_STATUSES == ("UNAVAILABLE", "PARTIAL", "AVAILABLE"),
          f"预览可用性词表完整：{BS.PREVIEW_AVAILABILITY_STATUSES}")
    check(BS.SYSTEM_ASSURANCE_STATUSES == ("NOT_RUN", "PASSED", "FAILED", "BLOCKED"),
          f"系统 Assurance 词表完整：{BS.SYSTEM_ASSURANCE_STATUSES}")
    check(BS.HUMAN_ACCEPTANCE_STATUSES == ("NOT_REVIEWED", "ACCEPTED", "REJECTED"),
          f"人工状态词表完整：{BS.HUMAN_ACCEPTANCE_STATUSES}")
    check(BS.HUMAN_ACCEPTANCE_DEFAULT == "NOT_REVIEWED"
          and BS.HumanAcceptanceStatus().status == "NOT_REVIEWED",
          "人工状态默认值仍为 NOT_REVIEWED（无参构造即默认）")
    check(BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("PASSED")).human_acceptance.status
        == "NOT_REVIEWED",
        "ReportStatusSnapshot.build 不传人工状态时仍为 NOT_REVIEWED（无自动接受）")
    # 每个合法取值都能 round-trip（含未来人工流程写回的 ACCEPTED / REJECTED）
    for value in BS.HUMAN_ACCEPTANCE_STATUSES:
        ha = BS.HumanAcceptanceStatus(status=value, detail="")
        check(BS.HumanAcceptanceStatus.from_dict(ha.to_dict()).to_dict() == ha.to_dict(),
              f"人工状态 {value} 可写入并原样读回")
    # 人工状态必须能随整个 status snapshot 读回（wire 不被上层截断）
    for value in ("ACCEPTED", "REJECTED"):
        doc = dict(snap.to_dict())
        doc["human_acceptance"] = {"status": value, "detail": ""}
        check(BS.ReportStatusSnapshot.from_dict(doc).human_acceptance.status == value,
              f"ReportStatusSnapshot 能读回独立人工流程写入的 {value}")
    # 非法值拒绝
    expect_raises("人工状态非法值被拒",
                  lambda: BS.HumanAcceptanceStatus(status="PASSED"), "status")
    expect_raises("来自 dict 的非法人工状态被拒",
                  lambda: BS.HumanAcceptanceStatus.from_dict(
                      {"status": "APPROVED", "detail": ""}), "status")
    # 外层 wire（ReportStatusSnapshot）与四个内层状态类型用**同一条**闭合规则：
    # 只有 status / detail 合法，detail 可缺省；未知字段一律 fail-closed（不静默丢弃）。
    expect_raises("ReportStatusSnapshot 出现未知字段被拒",
                  lambda: BS.ReportStatusSnapshot.from_dict(
                      {**snap.to_dict(), "approved_by": "x"}), "未知字段")
    _nested_cases = (
        ("ProcessCompletionStatus", BS.ProcessCompletionStatus, "COMPLETED"),
        ("PreviewAvailabilityStatus", BS.PreviewAvailabilityStatus, "AVAILABLE"),
        ("SystemAssuranceStatus", BS.SystemAssuranceStatus, "PASSED"),
        ("HumanAcceptanceStatus", BS.HumanAcceptanceStatus, "ACCEPTED"),
    )
    for _tn, _cls, _value in _nested_cases:
        expect_raises(f"{_tn} 未知字段 approved_by 被拒",
                      lambda c=_cls, v=_value: c.from_dict(
                          {"status": v, "detail": "", "approved_by": "x"}),
                      "未知字段")
        expect_raises(f"{_tn} 未知字段 confidence 被拒",
                      lambda c=_cls, v=_value: c.from_dict(
                          {"status": v, "detail": "", "confidence": 0.9}),
                      "未知字段")
        expect_raises(f"{_tn} 非 dict 被拒",
                      lambda c=_cls: c.from_dict(["status"]), "dict")
        expect_raises(f"{_tn} 缺 status 被拒",
                      lambda c=_cls: c.from_dict({"detail": ""}), "status")
        # detail 缺省仍按既有语义补空串（不借机改语义）
        check(_cls.from_dict({"status": _value}).detail == "",
              f"{_tn} 缺省 detail 仍为 ''（既有语义不变）")
        # 合法 wire（含未来人工流程写回的 ACCEPTED）必须能原样读回
        check(_cls.from_dict({"status": _value, "detail": "d"}).status == _value
              and _cls.from_dict({"status": _value, "detail": "d"}).detail == "d",
              f"{_tn} 合法 wire 可读回 {_value}")
    # 业务 API 隔离：没有任何把人工状态改成非默认值的入口
    ha_members = [n for n, _ in inspect.getmembers(BS.HumanAcceptanceStatus)
                  if callable(getattr(BS.HumanAcceptanceStatus, n, None))]
    check(not any(n.startswith(("set_", "accept", "approve", "seal"))
                  for n in ha_members),
          f"HumanAcceptanceStatus 无任何更改入口方法（{sorted(ha_members)}）")
    # 业务 API 隔离的**最强静态陈述**：剔除注释与字符串字面量后的可执行代码里，
    # 根本不存在 "ACCEPTED" / "REJECTED" 这两个词 → 任何分支都不可能产出它们。
    code_tokens = _code_only(mod_src)
    check("ACCEPTED" not in code_tokens and "REJECTED" not in code_tokens,
          "可执行代码中不存在 ACCEPTED / REJECTED 字面量（唯一出处是封闭词表字符串）")

    # ===================== 6. formal closure 与 report version / Assurance 正交 =====================
    check("report_version" not in closure.to_dict()
          and "assurance" not in json.dumps(closure.to_dict(), ensure_ascii=False).lower(),
          "FormalPhaseClosureSnapshot 不含 report version / assurance 字段")
    check(closure.derived_by_runtime is False,
          "formal closure 不由 runtime 派生（derived_by_runtime=False 是唯一允许值）")
    closed = BS.FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "closed",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-12-01"},),
        source_documents=({"path": "V2_TODO.md", "sha256": "8" * 64},),
        recorded_at="2026-12-01")
    check(closed.snapshot_id != closure.snapshot_id
          and closed.to_dict().get("report_version") is None,
          "正式阶段关闭状态变化不产生也不消费 report version（正交）")

    # ---- P1-4：来源治理文档必须以「规范路径 + SHA256」绑定，指纹覆盖这些 hash ----
    check(all(set(rec) == {"path", "sha256"} and len(rec["sha256"]) == 64
              for rec in closure.source_documents),
          f"source_documents 是带 SHA256 的类型化记录：{closure.to_dict()['source_documents']}")
    same_path_new_hash = BS.FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "open",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-09-20"},
                {"phase_id": "ts5", "status": "blocked",
                 "source_document": "TREE_STRUCTURE_ADJUSTMENT_TASK.md",
                 "recorded_at": "2026-09-19", "note": "TS5 未关闭"}),
        source_documents=({"path": "TREE_STRUCTURE_ADJUSTMENT_TASK.md", "sha256": "9" * 64},
                          {"path": "V2_TODO.md", "sha256": "7" * 64}),
        recorded_at="2026-09-20")
    check(same_path_new_hash.snapshot_id != closure.snapshot_id
          and same_path_new_hash.snapshot_fingerprint != closure.snapshot_fingerprint,
          "同一路径但文档 SHA256 变化 → 快照身份必须变化（文档内容被真正绑定）")
    for label, docs in (
            ("缺 sha256", ({"path": "V2_TODO.md"},)),
            ("非法 sha256", ({"path": "V2_TODO.md", "sha256": "zz"},)),
            ("非 64 位 sha256", ({"path": "V2_TODO.md", "sha256": "a" * 63},)),
            ("空 path", ({"path": "", "sha256": "8" * 64},)),
            ("绝对 path", ({"path": "/etc/passwd", "sha256": "8" * 64},)),
            ("反斜杠 path", ({"path": "a\\b.md", "sha256": "8" * 64},)),
            ("未知字段", ({"path": "V2_TODO.md", "sha256": "8" * 64, "note": "x"},)),
            ("重复 path", ({"path": "V2_TODO.md", "sha256": "8" * 64},
                           {"path": "V2_TODO.md", "sha256": "9" * 64})),
    ):
        expect_raises(f"formal closure 来源文档{label}被拒",
                      lambda d=docs: BS.FormalPhaseClosureSnapshot.build(
                          phases=({"phase_id": "phase4", "status": "open",
                                   "source_document": "V2_TODO.md",
                                   "recorded_at": "2026-09-20"},),
                          source_documents=d, recorded_at="2026-09-20"))
    expect_raises("phase 引用未登记的来源文档被拒",
                  lambda: BS.FormalPhaseClosureSnapshot.build(
                      phases=({"phase_id": "phase4", "status": "open",
                               "source_document": "NOT_REGISTERED.md",
                               "recorded_at": "2026-09-20"},),
                      source_documents=({"path": "V2_TODO.md", "sha256": "8" * 64},),
                      recorded_at="2026-09-20"), "已登记")
    expect_raises("runtime 不得自行声明正式阶段关闭",
                  lambda: BS.FormalPhaseClosureSnapshot.from_dict(
                      {**closure.to_dict(), "derived_by_runtime": True}),
                  "derived_by_runtime")
    status_after = BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("PASSED"))
    check(status_after.report_version == snap.report_version,
          "正式阶段关闭状态与 report status 完全解耦")
    check(BS.DESIGN_AXES == ("design_surface_coverage", "demo_content_coverage",
                             "formal_phase_closure"),
          "三条完成轴显式区分（设计面覆盖 / Demo 内容覆盖 / 正式阶段关闭）")

    # ===================== 7. report version 指纹白名单与禁入字段 =====================
    check(not (set(BS.REPORT_VERSION_FINGERPRINT_FIELDS)
               & set(BS.REPORT_VERSION_FORBIDDEN_FIELDS)),
          "report version 白名单与禁入名单不相交")
    body = rvi.fingerprint_body()
    check(set(body) == set(BS.REPORT_VERSION_FINGERPRINT_FIELDS),
          "report version 指纹体恰好等于白名单字段集")
    for field in BS.REPORT_VERSION_FORBIDDEN_FIELDS:
        check(field not in body,
              f"禁入字段 {field!r} 不得进入 report version 指纹")
    # 运行身份 / 时间 / 路径 / ReviewIssue / Assurance / 人工 / 正式关闭 / UI 全部不得进入
    for forbidden, label in (("run_id", "运行身份"), ("attempt", "尝试序号"),
                             ("started_at", "开始时间"), ("generated_at", "生成时间"),
                             ("results_root", "结果路径"), ("review_issues", "ReviewIssue"),
                             ("assurance_result", "AssuranceResult"),
                             ("human_acceptance_status", "人工状态"),
                             ("formal_phase_closure", "正式阶段关闭"),
                             ("ui_state", "UI 字段")):
        check(forbidden in BS.REPORT_VERSION_FORBIDDEN_FIELDS,
              f"{label}（{forbidden}）被显式列为 report version 禁入字段")

    # ===================== 8. 内容变化必须改版本；运行身份变化不得改版本 =====================
    # 每一类内容依赖一个反例（不堆同义测试）：逐项对应实施计划 §4.6 的每一类。
    cases = {
        "scope profile 指纹": dict(profile_fingerprint="6" * 64),
        "scope input 指纹": dict(scope_input_fingerprint="7" * 64),
        "projection 身份": dict(projection_id="proj_" + "1" * 24),
        "Plan 身份": dict(plan_id="dplan_" + "1" * 24),
        "job 身份": dict(job_id="job_demo_backbone_0002"),
        "company 身份": dict(company_id="company_fixture_b"),
        "报告基准日": dict(report_as_of="2026-09-30"),
        "选中任务集": dict(selected_task_ids=("dtask_0001", "dtask_0002")),
        "Topic Pack 集": dict(topic_pack_ids=("pack_dtask_0002_fin_solvency",)),
        "FinancialFactPack 工件身份": dict(financial_fact_pack_artifact_id="ffp_" + "1" * 24),
        "Contract 版本": dict(contract_fingerprint="e" * 64),
        "SourcePolicy 指纹": dict(source_policy_fingerprint="a" * 64),
        "WritingSpec 版本": dict(writing_spec_fingerprint="f" * 64),
        "PresentationProfile 指纹": dict(presentation_profile_fingerprint="b" * 64),
        "依赖聚合指纹": dict(dependency_fingerprint="c" * 64),
        "正文 body_fingerprint": dict(body_fingerprint="d" * 64),
        # §4.6（M930-3 任务二）：Draft 身份与非 Markdown 载荷指纹也是承重输入。
        "各节 SectionDraft 身份": dict(section_draft_ids=("sdraft_0002",)),
        "规范载荷指纹": dict(assembled_payload_fingerprint="e" * 64),
        "Narrative schema 版本": dict(narrative_schema_version="narrative-v2"),
        "Claim schema 版本": dict(claim_schema_version="claim-v2"),
        "Table schema 版本": dict(table_schema_version="table-v2"),
        "Writer schema 版本": dict(writer_schema_version="writer-v2"),
        "Assembler schema 版本": dict(assembler_schema_version="assembler-v2"),
        "ClaimBindingGate 规则版本": dict(claim_binding_gate_version="cbg-2"),
        "ClaimEntailment 规则版本": dict(claim_entailment_rules_version="cer-2"),
        "prompt 版本": dict(prompt_version="prompt-v2"),
        "model policy 身份": dict(model_policy_id="demo_model_policy_v2"),
    }
    # 白名单的每一类内容依赖都必须在上面出现（新增字段不得悄悄绕过反例）
    check(set(cases) == set(BS.REPORT_VERSION_FINGERPRINT_FIELDS) - {"selected_task_ids",
                                                                    "topic_pack_ids",
                                                                    "financial_fact_pack_artifact_id"}
          or set(_rvi_base()) == set(BS.REPORT_VERSION_FINGERPRINT_FIELDS),
          "反例表覆盖白名单的每一类内容依赖")
    for label, over in cases.items():
        other = _rvi(**over)
        check(other.report_version != rvi.report_version,
              f"{label} 变化 → report_version 必须变化"
              f"（{rvi.report_version} → {other.report_version}）")

    # 操作性 run 身份变化（run_id / attempt / 时间 / 路径）不得改变 report version：
    # 这些字段在 build 签名与 dataclass 字段里**根本不存在**。
    build_params = set(inspect.signature(BS.ReportVersionIdentity.build).parameters)
    check(not (build_params & set(BS.REPORT_VERSION_FORBIDDEN_FIELDS)),
          f"ReportVersionIdentity.build 不接受任何禁入字段参数（{sorted(build_params)}）")
    fields = set(BS.ReportVersionIdentity.__dataclass_fields__)
    check(not (fields & set(BS.REPORT_VERSION_FORBIDDEN_FIELDS)),
          "ReportVersionIdentity 没有任何禁入字段属性")
    ri_a, sm_a, mi_a = _real_run_and_scope()
    ri_b, sm_b, mi_b = _real_run_and_scope(run_over={
        "run_id": "demo_backbone_20260921T000000Z", "attempt": 2,
        "started_at": "2026-09-21T00:00:00Z"})
    check(ri_a.run_id != ri_b.run_id and ri_a.attempt != ri_b.attempt
          and ri_a.scope_input_fingerprint == ri_b.scope_input_fingerprint,
          "两次运行的操作性身份不同，但业务内容输入指纹相同")
    check(_rvi_for_scope(sm_a, mi_a).report_version
          == _rvi_for_scope(sm_b, mi_b).report_version,
          "仅 run_id / attempt / 时间 / 路径变化 → report_version 必须**不变**")

    # ===================== 9. 旧 report version 不能套用到新内容 =====================
    new_rvi = _rvi(body_fingerprint="d" * 64)
    check(new_rvi.report_version != rvi.report_version, "新内容产生了新的 report version")
    expect_raises("把旧 report version 套到新内容上被拒",
                  lambda: dataclasses.replace(
                      new_rvi, report_version=rvi.report_version), "report_version")
    expect_raises("把旧 content_fingerprint 套到新内容上被拒",
                  lambda: dataclasses.replace(
                      new_rvi, content_fingerprint=rvi.content_fingerprint),
                  "content_fingerprint")
    expect_raises("篡改 content_fingerprint 使版本不一致被拒",
                  lambda: dataclasses.replace(rvi, content_fingerprint="0" * 64),
                  "content_fingerprint")
    stale_status = BS.ReportStatusSnapshot.build(
        rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("PASSED"))
    check(stale_status.report_version == rvi.report_version
          and stale_status.report_version != new_rvi.report_version,
          "旧报告版本状态不能被新内容版本冒充（版本字符串不同）")
    doc = stale_status.to_dict()
    doc["report_version_identity"] = new_rvi.to_dict()
    expect_raises("状态对象声明版本与其绑定 identity 不一致被拒",
                  lambda: BS.ReportStatusSnapshot.from_dict(
                      {**doc, "report_version": stale_status.report_version}),
                  "report_version")

    # ===================== 10. manifest：报告形成前后一致 + 指纹排除自身身份 =====================
    run_identity, scope_manifest, manifest_in = _real_run_and_scope()
    # 报告版本必须与已解析 scope manifest 满足顶层身份 DAG
    rvi_scope = _rvi_for_scope(scope_manifest, manifest_in)
    snap_scope = BS.ReportStatusSnapshot.build(
        rvi_scope, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("PASSED"))
    manifest_pre = BS.DemoBackboneRunManifest.build(
        run_identity=run_identity, scope_manifest=scope_manifest,
        artifact_index_sha256="5" * 64, formal_closure=closure,
        design_surfaces=matrix)
    check(manifest_pre.report_version_identity is None and manifest_pre.status is None,
          "报告形成前：manifest 的 report_version_identity 与 status 同时为 None")
    check("report_version" not in manifest_pre.to_dict()
          or manifest_pre.to_dict().get("report_version_identity") is None,
          "报告形成前不得编造空报告版本来填充 manifest")
    expect_raises("只给 version 不给 status 被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=run_identity, scope_manifest=scope_manifest,
                      artifact_index_sha256="5" * 64, formal_closure=closure,
                      design_surfaces=matrix, report_version_identity=rvi_scope),
                  "同时存在")
    expect_raises("只给 status 不给 version 被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=run_identity, scope_manifest=scope_manifest,
                      artifact_index_sha256="5" * 64, formal_closure=closure,
                      design_surfaces=matrix, status=snap_scope),
                  "同时存在")

    manifest_post = BS.DemoBackboneRunManifest.build(
        run_identity=run_identity, scope_manifest=scope_manifest,
        artifact_index_sha256="5" * 64, formal_closure=closure,
        design_surfaces=matrix, report_version_identity=rvi_scope, status=snap_scope,
        created_at="2026-09-20T00:00:00Z")
    check(BS.DemoBackboneRunManifest.from_dict(manifest_post.to_dict()).to_dict()
          == manifest_post.to_dict(),
          "DemoBackboneRunManifest round-trip 一致")
    fp_body = manifest_post.manifest_fingerprint_body()
    check(not (set(fp_body) & set(BS.RUN_MANIFEST_FINGERPRINT_EXCLUDE)),
          "manifest 指纹体排除自身身份字段（manifest_id / fingerprint / created_at）")
    check("created_at" in manifest_post.to_dict()
          and manifest_post.compute_manifest_fingerprint() == manifest_post.manifest_fingerprint,
          "运行时间进入 manifest 但不进入其内容指纹（运行时间不是内容身份）")
    check(manifest_pre.manifest_id != manifest_post.manifest_id,
          "报告形成前后 manifest 身份不同（报告形成改变内容身份）")
    check(set(manifest_post.manifest_id)
          and manifest_post.manifest_id.startswith("dbm_")
          and manifest_post.manifest_id
          == BS.derive_run_manifest_id(manifest_post.manifest_fingerprint),
          "manifest_id 只由 manifest_fingerprint 派生")
    expect_raises("manifest run_id 与 scope_manifest 不一致被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=DSS.DemoRunIdentity(
                          schema_version=DSS.DEMO_RUN_IDENTITY_SCHEMA_VERSION,
                          run_id="demo_backbone_other", attempt=1,
                          started_at="2026-09-20T00:00:00Z", results_root="x",
                          job_id=run_identity.job_id,
                          scope_input_fingerprint=run_identity.scope_input_fingerprint),
                      scope_manifest=scope_manifest, artifact_index_sha256="5" * 64,
                      formal_closure=closure, design_surfaces=matrix),
                  "run_id")
    expect_raises("manifest 引用非 planning 运行身份被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity={"run_id": "x"}, scope_manifest=scope_manifest,
                      artifact_index_sha256="5" * 64, formal_closure=closure,
                      design_surfaces=matrix), "DemoRunIdentity")

    # ---- P1-3：顶层身份 DAG 的**唯一**反例 --------------------------------------
    # 构造第二条**内部完全合法、自证通过**的链：只把来源输入里的 document_version
    # 换成 v2，于是 scope_input_fingerprint 不同，而 run_id / attempt / job_id 相同。
    # 把它与第一条链的 run_identity 配对，顶层 manifest 必须 fail-closed。
    _, other_scope, other_manifest_in = _real_run_and_scope(
        source_over={"document_version": "v2"})
    check(other_scope.scope_input_fingerprint != scope_manifest.scope_input_fingerprint
          and other_scope.run_id == scope_manifest.run_id
          and other_scope.attempt == scope_manifest.attempt
          and other_scope.job_id == scope_manifest.job_id,
          "反例前提：另一条链内部自证合法、run/job/attempt 相同，仅 "
          "scope_input_fingerprint 不同")
    expect_raises("顶层身份 DAG 旁路（scope_input_fingerprint 不一致）被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=run_identity, scope_manifest=other_scope,
                      artifact_index_sha256="5" * 64, formal_closure=closure,
                      design_surfaces=matrix, report_version_identity=rvi_scope,
                      status=snap_scope),
                  "scope_input_fingerprint")
    # 反序列化路径同样 fail-closed（不是只在 build 里拦）
    expect_raises("反序列化路径同样核验身份 DAG",
                  lambda: BS.DemoBackboneRunManifest.from_dict({
                      **manifest_post.to_dict(), "scope_manifest": other_scope.to_dict()}),
                  "scope_input_fingerprint")
    # attempt / job_id 两个方向也各自闭环
    expect_raises("attempt 不一致被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=DSS.DemoRunIdentity(
                          schema_version=DSS.DEMO_RUN_IDENTITY_SCHEMA_VERSION,
                          run_id=run_identity.run_id, attempt=run_identity.attempt + 1,
                          started_at=run_identity.started_at,
                          results_root=run_identity.results_root,
                          job_id=run_identity.job_id,
                          scope_input_fingerprint=run_identity.scope_input_fingerprint),
                      scope_manifest=scope_manifest, artifact_index_sha256="5" * 64,
                      formal_closure=closure, design_surfaces=matrix), "attempt")
    expect_raises("job_id 不一致被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=DSS.DemoRunIdentity(
                          schema_version=DSS.DEMO_RUN_IDENTITY_SCHEMA_VERSION,
                          run_id=run_identity.run_id, attempt=run_identity.attempt,
                          started_at=run_identity.started_at,
                          results_root=run_identity.results_root,
                          job_id="job_demo_backbone_9999",
                          scope_input_fingerprint=run_identity.scope_input_fingerprint),
                      scope_manifest=scope_manifest, artifact_index_sha256="5" * 64,
                      formal_closure=closure, design_surfaces=matrix), "job_id")
    # report version 与 scope manifest 的关联也必须是逐字段比较（不是前缀比较）
    for label, over, needle in (
            ("plan_id", dict(plan_id="dplan_" + "9" * 24), "plan_id"),
            ("projection_id", dict(projection_id="proj_" + "9" * 24), "projection_id"),
            ("company_id", dict(company_id="company_other"), "company_id"),
            ("report_as_of", dict(report_as_of="2020-01-01"), "report_as_of"),
            ("selected_task_ids", dict(selected_task_ids=("dtask_zzzz",)),
             "selected_task_ids"),
            ("topic_pack_ids", dict(topic_pack_ids=("pack_not_in_scope",)),
             "topic_pack_ids"),
            # P1-A：同一 job / 同一条 scope 输入链的绑定也必须是逐字段比较
            ("scope_input_fingerprint", dict(scope_input_fingerprint="7" * 64),
             "scope_input_fingerprint"),
            ("job_id", dict(job_id="job_demo_backbone_0002"), "job_id"),
    ):
        expect_raises(f"report version 的 {label} 与 scope manifest 不一致被拒",
                      lambda o=over: BS.DemoBackboneRunManifest.build(
                          run_identity=run_identity, scope_manifest=scope_manifest,
                          artifact_index_sha256="5" * 64, formal_closure=closure,
                          design_surfaces=matrix,
                          report_version_identity=_rvi_for_scope(
                              scope_manifest, manifest_in, **o),
                          status=BS.ReportStatusSnapshot.build(
                              _rvi_for_scope(scope_manifest, manifest_in, **o),
                              process_completion=BS.ProcessCompletionStatus("COMPLETED"),
                              preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
                              system_assurance=BS.SystemAssuranceStatus("PASSED"))),
                      needle)

    # ---- P1-A：单改 RVI 的两个新绑定字段（自身仍自证合法）必须被逐字段比较挡住 -------
    # 反例前提：``_rvi_for_scope`` 走 ``ReportVersionIdentity.build``，因此 content_fingerprint
    # / report_version 是**按改动后的内容重算**的（RVI 自身完全合法），且两条改动都不触碰
    # run_identity → 只有 report_version_identity↔scope_manifest 的逐字段比较能拦住。
    for _label, _over in (("scope_input_fingerprint", dict(scope_input_fingerprint="7" * 64)),
                          ("job_id", dict(job_id="job_demo_backbone_0002"))):
        _bad_rvi = _rvi_for_scope(scope_manifest, manifest_in, **_over)
        check(getattr(_bad_rvi, _label) != getattr(scope_manifest, _label)
              and _bad_rvi.report_version != rvi_scope.report_version,
              f"反例前提（{_label}）：RVI 自身重算自证合法，仅 {_label} 与 scope manifest 不同")

        def _build_with(bad_rvi):
            return BS.DemoBackboneRunManifest.build(
                run_identity=run_identity, scope_manifest=scope_manifest,
                artifact_index_sha256="5" * 64, formal_closure=closure,
                design_surfaces=matrix, report_version_identity=bad_rvi,
                status=BS.ReportStatusSnapshot.build(
                    bad_rvi,
                    process_completion=BS.ProcessCompletionStatus("COMPLETED"),
                    preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
                    system_assurance=BS.SystemAssuranceStatus("PASSED")))

        expect_raises(f"顶层身份 DAG：RVI {_label} 与 scope manifest 不一致（build）被拒",
                      lambda r=_bad_rvi: _build_with(r), _label)
        # 反序列化路径（artifact 严格读回走的间接路径）同样核验；只换这两处嵌套身份，
        # 不重算 manifest_fingerprint —— 必须在身份 DAG 处而非指纹处 fail-closed。
        _bad_doc = dict(manifest_post.to_dict())
        _bad_doc["report_version_identity"] = _bad_rvi.to_dict()
        _bad_doc["status"] = {**manifest_post.to_dict()["status"],
                              "report_version_identity": _bad_rvi.to_dict(),
                              "report_version": _bad_rvi.report_version}
        expect_raises(f"顶层身份 DAG：RVI {_label} 不一致（from_dict）被拒",
                      lambda dd=_bad_doc: BS.DemoBackboneRunManifest.from_dict(dd), _label)

    # ---- P1-B：Topic Pack 必须**精确等集**（AGENTS.md 完整 Pack 集门，不得降为 subset）--
    _packs = ("pack_zzz_0002", "pack_aaa_0001")  # 故意乱序：验证公共规范化后等价
    _, packed_scope, packed_in = _real_run_and_scope(runtime_over={"pack_ids": list(_packs)})
    check(packed_scope.pack_ids == _packs,
          f"反例前提：scope manifest 携带两个 Topic Pack（{packed_scope.pack_ids}）")

    def _packed_manifest(pack_ids) -> BS.DemoBackboneRunManifest:
        r = _rvi_for_scope(packed_scope, packed_in, topic_pack_ids=pack_ids)
        return BS.DemoBackboneRunManifest.build(
            run_identity=run_identity, scope_manifest=packed_scope,
            artifact_index_sha256="5" * 64, formal_closure=closure,
            design_surfaces=matrix, report_version_identity=r,
            status=BS.ReportStatusSnapshot.build(
                r, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
                preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
                system_assurance=BS.SystemAssuranceStatus("PASSED")))

    check(_packed_manifest(("pack_aaa_0001", "pack_zzz_0002")).report_version_identity
          .topic_pack_ids == tuple(sorted(_packs)),
          "Pack 集顺序经公共规范化后相同 → 通过（且落库为排序去重结果）")
    expect_raises("少一个 Pack 被拒（subset 不再是合法完整报告）",
                  lambda: _packed_manifest(("pack_aaa_0001",)), "缺")
    expect_raises("多一个 Pack 被拒",
                  lambda: _packed_manifest(("pack_aaa_0001", "pack_zzz_0002",
                                            "pack_new_0003")), "多")
    expect_raises("Pack 集整体替换被拒（既不缺也不多之外的错配同样拒绝）",
                  lambda: _packed_manifest(("pack_aaa_0001", "pack_xxx_0009")),
                  "topic_pack_ids")
    # scope manifest 为空时只允许 RVI 同样为空（RVI 不得自行伪造 Pack）
    check(scope_manifest.pack_ids == () and rvi_scope.topic_pack_ids == (),
          "空 scope manifest 只与空 Pack 集配对（RVI 不自行伪造 Pack）")
    _fake_rvi = _rvi_for_scope(scope_manifest, manifest_in,
                               topic_pack_ids=("pack_fabricated_0001",))
    _fake_snap = BS.ReportStatusSnapshot.build(
        _fake_rvi, process_completion=BS.ProcessCompletionStatus("COMPLETED"),
        preview_availability=BS.PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=BS.SystemAssuranceStatus("PASSED"))
    expect_raises("空 scope manifest 下 RVI 自带 Pack 被拒",
                  lambda: BS.DemoBackboneRunManifest.build(
                      run_identity=run_identity, scope_manifest=scope_manifest,
                      artifact_index_sha256="5" * 64, formal_closure=closure,
                      design_surfaces=matrix, report_version_identity=_fake_rvi,
                      status=_fake_snap),
                  "topic_pack_ids")
    # 所有拒绝路径都不得留下"可被引用的合法指纹"：packed manifest 的重试仍然自证一致
    check(_packed_manifest(("pack_zzz_0002", "pack_aaa_0001")).manifest_fingerprint
          == _packed_manifest(("pack_aaa_0001", "pack_zzz_0002")).manifest_fingerprint,
          "顺序不同的同一 Pack 集产生同一 manifest 身份（规范化在身份之前）")

    # ---- P1-C：顶层 manifest 的嵌套状态出现未知字段必须在解码处 fail-closed -------------
    # 反例前提：manifest_post 的身份与 fingerprint 原本完全合法，只改动一个嵌套状态的
    # wire（**不**重算 manifest_fingerprint）；`from_dict` 必须在嵌套状态解码处拒绝，
    # 而不是静默丢掉未知字段后继续接受。
    for _slot in ("process_completion", "preview_availability", "system_assurance",
                  "human_acceptance"):
        _doc = dict(manifest_post.to_dict())  # 身份与 fingerprint 原本完全合法
        _st = dict(_doc["status"])
        _st[_slot] = {**_st[_slot], "approved_by": "x", "confidence": 0.9}
        _doc["status"] = _st  # 不重算 manifest_fingerprint
        expect_raises(f"顶层 manifest 嵌套状态 {_slot} 含未知字段被拒（不重算指纹）",
                      lambda dd=_doc: BS.DemoBackboneRunManifest.from_dict(dd), "未知字段")

    # ===================== 11. 设计面矩阵缺项必须可见 =====================
    check(len(matrix.missing_surface_ids()) == len(BS.DESIGN_SURFACE_IDS) - 2,
          f"未展示的设计面显式可见（{len(matrix.missing_surface_ids())} 项未列入）")
    check(matrix.status_counts()["gap"] == 1 and matrix.status_counts()["demonstrated"] == 1,
          "设计面状态计数如实反映 gap 与 demonstrated")
    check(set(matrix.by_axis()) == set(BS.DESIGN_AXES),
          "矩阵按三条独立轴分组")
    expect_raises("缺证据却声明 demonstrated 被拒",
                  lambda: BS.DesignSurfaceRecord(
                      surface_id="ds.claim_citation", axis="demo_content_coverage",
                      title="x", status="demonstrated", evidence_kind="artifact",
                      evidence_refs=()), "evidence_refs")

    # ===================== 12. report-version v1/v2/v3 并存：无静默升级、无 leakage =====================
    # M930-3 任务二：current wire 升到 v3（新增各节 Draft 身份与规范载荷指纹）。v1/v2 载荷
    # **只能**经显式 legacy reader 只读回放 —— 用 v3 白名单重算它们的指纹必然得到另一个值，
    # 故任何"升级后再读"的路径都会产生伪造的报告版本。
    check(BS.REPORT_VERSION_SCHEMA_VERSION == "demo-report-version-v3"
          and BS.LEGACY_REPORT_VERSION_SCHEMA_VERSIONS == ("demo-report-version-v1",
                                                           "demo-report-version-v2"),
          "current report version 是 v3，v1/v2 被登记为 legacy wire")
    check(set(BS.LEGACY_REPORT_VERSION_V1_FIELDS) < set(BS.LEGACY_REPORT_VERSION_V2_FIELDS)
          < set(BS.REPORT_VERSION_FINGERPRINT_FIELDS),
          "v1 ⊂ v2 ⊂ v3 的字段闭集严格递增（新键不得被差集派生偷偷算进旧版）")
    check(not (set(BS.LEGACY_REPORT_VERSION_V2_FIELDS)
               & {"section_draft_ids", "assembled_payload_fingerprint"}),
          "v2 白名单不含 v3 新增的两个身份键")
    current_doc = rvi.to_dict()

    def _legacy_payload(marker: str, drop: tuple[str, ...]) -> dict:
        """按**该 legacy 版本自己的字段集**重算载荷指纹与版本号（不掺入更高版本的键）。"""
        doc = {k: v for k, v in current_doc.items() if k not in drop}
        fields = dict(BS.LEGACY_REPORT_VERSION_FIELDS_BY_MARKER)[marker]
        body = {k: (list(doc[k]) if k in ("selected_task_ids", "topic_pack_ids",
                                          "section_draft_ids") else doc[k])
                for k in fields}
        doc["schema_version"] = marker
        doc["content_fingerprint"] = BS.sha256_canonical(body)
        doc["report_version"] = BS.derive_report_version(doc["content_fingerprint"])
        return doc

    v2_doc = _legacy_payload("demo-report-version-v2",
                             ("section_draft_ids", "assembled_payload_fingerprint"))
    v1_doc = _legacy_payload("demo-report-version-v1",
                             ("section_draft_ids", "assembled_payload_fingerprint",
                              "claim_binding_gate_version", "claim_entailment_rules_version"))
    expect_raises("current report version reader 拒绝 v1 载荷",
                  lambda: BS.ReportVersionIdentity.from_dict(v1_doc), "legacy")
    expect_raises("current report version reader 拒绝 v2 载荷",
                  lambda: BS.ReportVersionIdentity.from_dict(v2_doc), "legacy")
    for marker, doc, dropped in (("v1", v1_doc, 4), ("v2", v2_doc, 2)):
        view = BS.load_legacy_report_version_for_audit(doc)
        check(view.recompute_fingerprint() == doc["content_fingerprint"]
              and view.content_fingerprint != rvi.content_fingerprint,
              f"{marker} 只读视图按 {marker} 白名单自证，且与 current 身份指纹不同"
              "（不可被当成同一版本）")
        check(len(view.fields_spec) == len(BS.REPORT_VERSION_FINGERPRINT_FIELDS) - dropped,
              f"{marker} 只读视图只带自己那一版的字段集（比 current 少 {dropped} 个键）")
        check(not isinstance(view, BS.ReportVersionIdentity),
              f"{marker} 只读视图不是 current identity 类型，无法进入任何 current 决策")
    expect_raises("legacy report version reader 拒绝 current v3 载荷",
                  lambda: BS.load_legacy_report_version_for_audit(current_doc),
                  "不得经 legacy reader 读回")
    expect_raises("v1 载荷掺入更高版本的判定链键被拒（不得按新口径重算旧身份）",
                  lambda: BS.load_legacy_report_version_for_audit(
                      {**v1_doc, "claim_binding_gate_version": "cbg-1"}),
                  "claim_binding_gate_version")
    expect_raises("未登记 marker 的载荷不得被 legacy reader 读回",
                  lambda: BS.load_legacy_report_version_for_audit(
                      {**current_doc, "schema_version": "demo-report-version-v4"}),
                  "不是登记的 legacy report version 载荷")

    # ===================== 13. 自检输出可观测 =====================
    selfcheck = BS.self_check()
    check(selfcheck["ok"] is True and selfcheck["failed"] == 0,
          f"模块 self-check 通过（passed={selfcheck['passed']}）")
    check(len(selfcheck["checks"]) >= 10,
          f"self-check 覆盖 {len(selfcheck['checks'])} 项不变量")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

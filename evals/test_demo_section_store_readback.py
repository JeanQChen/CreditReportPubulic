"""Eval: Section Store current v2 链的真库提交/读回（§16.10 #25/#34，M930-3B P17）。

用法: python -m evals.test_demo_section_store_readback

本模块**不是 schema 测试**：全部断言都跑在真实临时 SQLite 上，覆盖 P17 在行为侧的要求：

 1. 整数迁移序列 1→5 真库落地；每行 wire discriminator 是**载荷自带**的 `schema_version`
    （列与载荷双向一致），migration 序号不替代它；
 2. 新旧 root 物理隔离：`commit_section_chain_v2` 只写 `*_v2` 表，v1 行一行不改、`current_section`
    指针不切；反过来 legacy 链在 v2 reader 里完全不可见；
 3. append-only：v2 root/member 表的 UPDATE / DELETE 都被触发器 ABORT；
 4. 公共 legacy reader 绕门：`commit_section_result` 拒 current 对象、`get_section_result` 只
    返回显式 `LegacySectionResultV1`、`load_legacy_section_chain_for_audit` 只读 v1 且只回 dict；
    `SectionChainV2` 拒非 current Draft（legacy narr-3 视图只走 legacy reader）；
 5. current reader 的 identity 重算：读回用 member 行重建 Draft、重算 `draft_revision`/`draft_id`；
    member 行被改（载荷被改、或被改后同样自洽）都 fail-closed；**复用路径同样逐行重算指纹**，
    不把「已存即复用」当放行；
 6. 行集与 root 声明的 exact-set 必须逐项相等：多一行 / 少一行都拒（复用路径与读回路径都拒）；
 7. v2 表清单闭合（writer 侧 ∪ 门后 ∪ Draft root），且本文件不建 follow-up 表（边界③）。

本模块的夹具是**门前链**（draft + 三类门侧对象，没有 Result），因此它对门后六个 family 只
断言「整体缺席时为 0 行」——门后束的落库与读回（完整链、往返稳定、失败不留半事务）由
`evals/test_demo_section_chain_persistence.py` 在**真实产出**的对象上覆盖。诚实边界：

 - 3C 已落库：aggregate `ClaimBindingDecision` / `ClaimEntailmentDecision` /
   `AcceptedSupportBinding`（本模块用**真实门**产出它们，不手搓决定对象）；
 - 本文件的 `wmctx-1` 正文上下文是**手搭**的（没有 `VerifiedPackSet`，「字节 → 哈希/定位/
   身份」那一半在 `test_demo_material_context` / `test_demo_pack_writer` 覆盖）；
 - `FollowUpNeed` 属边界③（`harness/topic_store.py`），本文件与 `sections/store.py` 都**不建**其表。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import topic_schema as TS  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import material_context as MC  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402
from sections import pack_writer as PW  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import store as ST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, exc_type, needle: str) -> None:
    try:
        fn()
    except exc_type as exc:  # noqa: BLE001
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:110]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

_TASK = "t1"
_SECTION = "company"
_WRITER_POLICY = PW.PACK_WRITER_POLICY_VERSION
_PROMPT_VERSION = "pack_section_writer_proposals_v1"
_FP = "a" * 64
#: 规范 `MaterialPayloadRef` dict（`wmm-2`）：`object_type` 只能取 `MATERIAL_TYPES` 里的值，
#: `content_hash` / `created_dependency_fingerprint` 必须是 64 位 sha256 hex，`locator` 必须是
#: 可解析的定位对象。夹具用**同一份**真实引用，成员侧与支撑边侧因此逐字一致。
_PAYLOAD = TS.MaterialPayloadRef(
    object_type="evidence_span", authority_identity="evidence:ev1", version="v1",
    content_hash=_FP,
    locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                               section_path="s1", page=3),
    created_dependency_fingerprint=_FP).to_dict()
_LOCATOR = NS.char_range_locator("evidence:ev1", 3, 40)
#: §三 A：`wmctx-1` 的**已解析正文**上下文。本夹具没有 `VerifiedPackSet`，因此不走
#: `resolve_writer_material_context`（「字节 → 哈希/定位/身份」那一半由
#: `test_demo_material_context` 在真实 PackSet 上覆盖），而是按公开构造入口手搭一份与 manifest
#: 成员**逐字段一致**的上下文。`evaluate_claim_chain` 的守卫是集合级的：manifest 非空即必须
#: 有正文上下文，否则绑定门与语义门会替一份它们从未见过的正文背书。
_READING_VIEW = "本节引用的公开材料记录了该公司 2024 年的经营情况，作为本节的背景材料。"
_RESOLVED_MATERIAL = MC.ResolvedWriterMaterial.create(
    member_ref=NS.manifest_member_ref("pack1", "m1"), topic_id="t1", pack_id="pack1",
    material_id="m1", material_type="evidence_span", research_material_disposition_id="rmd1",
    source_identity="evidence:ev1", provenance_identity="prov1", locator_ref=_LOCATOR,
    payload_ref=dict(_PAYLOAD), payload_hash=_FP, content_hash=_FP,
    material_content_fingerprint=_FP, reading_view=_READING_VIEW)
_MATERIAL_CONTEXT = MC.WriterMaterialContext.create(
    task_id=_TASK, section_id=_SECTION, pack_set_fingerprint="d" * 64,
    materials=(_RESOLVED_MATERIAL,))


def _draft(*, title: str = "") -> NS.SectionDraft:
    """一份最小的合法门前束：1 manifest 成员 + 1 WMPD + 1 候选 + 1 草稿单元 + 1 proposal。

    候选必须恰有一条 factual proposal（无据陈述不得进入 narrative wire），所以 subject family
    的 2 行由「1 候选 + 1 草稿单元」给出，而不是靠多加候选。
    """
    entry = NS.WriterMaterialManifestEntry.create(
        pack_id="pack1", material_id="m1", research_material_disposition_id="rmd1",
        source_identity="evidence:ev1", provenance_identity="prov1",
        material_content_fingerprint=_FP, topic_id="t1", material_type="evidence_span",
        payload_ref=dict(_PAYLOAD), locator_ref=_LOCATOR,
        payload_hash=_FP,
        reading_view_fingerprint=_RESOLVED_MATERIAL.reading_view_fingerprint)
    manifest = NS.WriterMaterialManifest.create(members=(entry,))
    revision = NS.derive_draft_revision(
        task_id=_TASK, section_id=_SECTION, company_id="c1", report_as_of="2024-12-31",
        contract_version="cv1", contract_fingerprint="cf1", writer_policy_version=_WRITER_POLICY,
        prompt_version=_PROMPT_VERSION, model_policy="stub", manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint())
    texts = ["公司2024年营业收入为1234.56亿元。"]
    candidates = tuple(NS.ClaimCandidate.create(
        draft_revision=revision, task_id=_TASK, section_id=_SECTION, company_id="c1",
        report_as_of="2024-12-31", contract_version="cv1", contract_fingerprint="cf1",
        claim_text=text, fact_type="metric") for text in texts)
    unit = NS.NarrativeDraftUnit.create(
        draft_revision=revision, section_id=_SECTION, index=0, unit_kind="paragraph",
        text=texts[0])
    proposal = NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id=candidates[0].candidate_id,
        draft_revision=revision, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), authority_kind="topic_pack",
        authority_container_id="pack1", source_identity="evidence:ev1",
        provenance_identity="prov1", support_role="primary", support_semantics="factual",
        authorization_path="path_a_prevalidated", content_fingerprint=_FP,
        dependency_fingerprint="dep-1", fact_id="f1", material_id="m1",
        payload_ref=dict(_PAYLOAD), locator_ref=_LOCATOR)
    wmpd = NS.WriterMaterialProcessingDisposition.create(
        manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint(),
        pack_id="pack1", material_id="m1", research_material_disposition_id="rmd1",
        material_content_fingerprint=_FP, processed=True, usage="used",
        support_usages=(proposal.proposed_support_id,), reason_code=None, reason_proof=None,
        writer_policy_version=_WRITER_POLICY)
    return NS.SectionDraft.create(
        task_id=_TASK, section_id=_SECTION, company_id="c1", report_as_of="2024-12-31",
        contract_version="cv1", contract_fingerprint="cf1", producer_kind="topic_harness",
        writer_policy_version=_WRITER_POLICY, prompt_version=_PROMPT_VERSION,
        model_policy="stub", authority_container_ids=("pack1",), material_manifest=manifest,
        material_dispositions=(wmpd,), claim_candidates=candidates,
        narrative_draft_units=(unit,), proposed_support_refs=(proposal,),
        unresolved_ids=(), unresolved_projections=(), coverage_summary={},
        conflict_projections=(), not_found_projections=(),
        dependency_fingerprint="dep-1", title=title)


class _StubEntailmentClient:
    """stub：返回固定「已蕴含」判定（离线，不联网、不真实调用）。"""

    def __init__(self, text: str = '{"verdict": "entailed", "reason_code": null, '
                                    '"rationale": "权威逐字给出"}'):
        self.text = text
        self.calls: list[dict] = []

    def evaluate(self, *, messages, system, prompt_version, model_policy):
        self.calls.append({"prompt_version": prompt_version})
        return PW.NarrationResult(
            text=self.text, call_id=f"call-{len(self.calls)}",
            model=PW.resolve_model_policy(model_policy), prompt_version=prompt_version,
            status="ok", error="")


def _authority(draft: NS.SectionDraft) -> PW.AuthorityScan:
    """按 3C 夹具的 proposal 造一份权威目录（坐标必须与 proposal 逐项一致）。"""
    proposal = draft.proposed_support_refs[0]
    fact = PW.AuthorityFactEntry(
        authority_kind=proposal.authority_kind,
        container_identity=proposal.authority_container_id, fact_id=proposal.fact_id,
        text="公司2024年营业收入为1234.56亿元。", topic_id="t1", aspect_ids=(),
        required=True, fact_type="metric", period="2024", scope="公司",
        material_id=proposal.material_id, payload_ref=dict(proposal.payload_ref),
        locator_ref=proposal.locator_ref, source_identity=proposal.source_identity,
        provenance_identity=proposal.provenance_identity,
        content_fingerprint=proposal.content_fingerprint)
    return PW.AuthorityScan(
        facts=(fact,), aspect_status={}, aspect_topic={}, aspect_impact={}, aspect_blocking={},
        aspect_question={}, excluded_facts=(), conflicts=(), not_found=(), gaps=(),
        coverage_counts={})


def _chain(draft: NS.SectionDraft, *,
           client: _StubEntailmentClient | None = None) -> ST.SectionChainV2:
    """真实门（P8→P9→P10）产出的完整链容器：draft + 三类门侧对象。

    本文件不手搓决定对象：三家门的 cardinality/digest 规则只有**真实产出**才能满足，
    手搓的决定会把「存储层断言」测成「夹具自洽」。因此这里跑真正的
    `evaluate_claim_chain`（P11 协调者）并把三个束交给 store。
    """
    from sections import rules_evaluator as RE
    outcome = RE.evaluate_claim_chain(draft, _authority(draft),
                                      llm_client=client or _StubEntailmentClient(),
                                      material_context=_MATERIAL_CONTEXT)
    return ST.SectionChainV2(
        draft=draft, aggregate_decisions=outcome.aggregate_decisions,
        entailment_decisions=outcome.entailment_decisions,
        accepted_bindings=outcome.accepted_bindings)


def _legacy_plan(plan_id: str) -> PS.ReportPlan:
    task = PS.SectionTask(
        task_id=PS.derive_task_id(plan_id, _SECTION), plan_id=plan_id, section_id=_SECTION,
        title="章节标题", purpose="purpose", research_policy="harness", topic_ids=("t1",),
        questions=(), output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=(), dependency_versions={})
    return PS.ReportPlan(
        plan_id=plan_id, job_id="job1", company_id="c1", company_name="示例公司",
        credit_type="other", report_as_of="2026-03-31", template_id="standard_v2",
        input_fingerprint="ifp1", contract_fingerprint="fp_c",
        planner_version=PS.PLANNER_VERSION, section_tasks=(task,),
        created_at="2026-01-01T00:00:00Z")


def _legacy_result(task_id: str) -> SS.LegacySectionResultV1:
    secver = SS.derive_legacy_section_version(task_id, (), (), renderer_version="rv",
                                              rules_version="rv")
    return SS.LegacySectionResultV1(
        section_result_id=SS.derive_section_result_id(secver), section_version=secver,
        task_id=task_id, section_id=_SECTION, status="DRAFT_READY",
        created_at="2026-01-01T00:00:00Z")


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _query(db: Path, sql: str, params: tuple = ()) -> list:
    conn = _connect(db)
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _exec(db: Path, sql: str, params: tuple = ()) -> None:
    conn = _connect(db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _count(db: Path, table: str, where: str = "", params: tuple = ()) -> int:
    clause = f" WHERE {where}" if where else ""
    return _query(db, f"SELECT COUNT(*) AS n FROM {table}{clause}", params)[0]["n"]


def _rewrite_payload(db: Path, table: str, where: str, params: tuple, mutate) -> None:
    """测试专用：改一行载荷并**连带重算指纹**（先 DROP 掉 append-only 触发器）。

    append-only 触发器是生产保护，测试要制造「损坏的库」只能先摘掉它；被摘掉的只是**临时库**
    的触发器，生产代码不动。
    """
    _exec(db, f"DROP TRIGGER trg_{table}_no_update")
    row = _query(db, f"SELECT * FROM {table} WHERE {where}", params)[0]
    payload = json.loads(row["payload_json"])
    mutate(payload)
    _exec(db, f"UPDATE {table} SET payload_json = ?, content_fingerprint = ?, "
              f"schema_version = ? WHERE {where}",
          (json.dumps(payload, ensure_ascii=False, sort_keys=True),
           ST._v2_fingerprint(payload), payload["schema_version"], *params))


# ---------------------------------------------------------------------------
# 1. 整数迁移序列 + 逐 payload wire discriminator
# ---------------------------------------------------------------------------

def _check_ledger_and_markers(db: Path, draft: NS.SectionDraft) -> None:
    applied = _query(db, "SELECT migration_id FROM schema_migrations ORDER BY migration_id")
    values = [r["migration_id"] for r in applied]
    check(values == [1, 2, 3, 4, 5, 6, 7] and all(isinstance(v, int) for v in values),
          f"真库 migration 序号是整数 1→7（实际 {values}）")
    check(ST.SECTION_STORE_SCHEMA_VERSION == 7
          and isinstance(ST.SECTION_STORE_SCHEMA_VERSION, int),
          "账本版本是整数 7（不写成字符串账本）")

    outcome = ST.commit_section_chain_v2(_chain(draft))
    check(outcome["ledger"] == "v2" and outcome["reused"] is False
          and outcome["draft_id"] == draft.draft_id,
          "commit_section_chain_v2 在真库写入 writer 侧链并回报 v2 账本")

    for family in ST.V2_WRITER_SIDE_FAMILIES:
        marker = ST.V2_FAMILY_WIRE_MARKER[family]
        rows = _query(db, f"SELECT schema_version, payload_json FROM {family} "
                          "WHERE draft_id = ?", (draft.draft_id,))
        check(rows and all(
            r["schema_version"] == marker
            and json.loads(r["payload_json"])["schema_version"] == marker for r in rows),
            f"{family} 每行的列 marker 与载荷 schema_version 都是 {marker!r}"
            "（migration 序号不替代 wire discriminator）")

    # 3C：三个决定/绑定 family 已在提交面内，逐行 marker 必须与载荷一致。
    for family in ST.V2_DECISION_FAMILIES:
        marker = ST.V2_FAMILY_WIRE_MARKER[family]
        rows = _query(db, f"SELECT schema_version, payload_json FROM {family} "
                          "WHERE draft_id = ?", (draft.draft_id,))
        check(rows and all(
            r["schema_version"] == marker
            and json.loads(r["payload_json"])["schema_version"] == marker for r in rows),
            f"{family} 每行的列 marker 与载荷 schema_version 都是 {marker!r}")

    for family in ST.V2_POST_GATE_FAMILIES:
        check(_count(db, family) == 0,
              f"{family} 在本文件的门前链上必须 0 行（门后束整体缺席才合法，"
              "不得出现「半条链」的行）")
    check(_count(db, "current_section_draft_v2", "draft_id = ?", (draft.draft_id,)) == 1,
          "本次 draft_id 在 v2 root 表恰 1 行")


# ---------------------------------------------------------------------------
# 2. 新旧 root 物理隔离
# ---------------------------------------------------------------------------

_V1_FACTS = ("section_result", "section_claim", "section_citation", "section_unresolved",
             "current_section", "current_plan", "section_evaluation", "section_rework")


def _check_root_isolation(db: Path, draft: NS.SectionDraft, legacy_id: str,
                          v1_before: dict) -> None:
    after = {t: _count(db, t) for t in _V1_FACTS}
    check(after == v1_before,
          f"commit_section_chain_v2 一行 v1 事实都不改（前 {v1_before} 后 {after}）")
    check(after["section_result"] == 1 and after["current_section"] == 1,
          "对照：legacy 链确实落在 v1 表（隔离断言不是「两边都空」的假绿）")
    check(_query(db, "SELECT 1 AS x FROM current_section WHERE task_id = ?",
                 (draft.task_id,)) == [],
          "v2 提交不切 current_section 指针（current 链只在 v2 表内自陈）")

    # 两条读面互不冒充。
    check(ST.load_current_section_chain_v2(legacy_id) is None,
          "v2 reader 读 legacy section_result_id 返回 None（不冒充 v1 行）")
    check(ST.load_legacy_section_chain_for_audit(draft.draft_id) is None,
          "legacy reader 读 v2 draft_id 返回 None（不冒充 current 行）")
    restored = ST.get_section_result(legacy_id)
    check(isinstance(restored, SS.LegacySectionResultV1)
          and not isinstance(restored, SS.SectionResult),
          "legacy reader 只返回**显式 legacy 视图**类型，调用方无法按 current-result-2 消费")

    chain = ST.load_legacy_section_chain_for_audit(legacy_id)
    check(chain is not None and chain["ledger"] == "v1"
          and set(chain) == {"ledger", "section_result_id", "section_result", "claims",
                             "citations", "unresolved"},
          "load_legacy_section_chain_for_audit 只回 v1 形状的纯 dict（无任何 v2 身份）")
    v2_keys = {"draft", "families", "members", "draft_id", "schema_version"}
    check(not (v2_keys & set(chain or {})),
          "legacy 审计结果里没有 v2 字段名（两套 wire 不混装）")


# ---------------------------------------------------------------------------
# 3. append-only 触发器（真库）
# ---------------------------------------------------------------------------

def _check_append_only_triggers(db: Path) -> None:
    for table in ("current_section_draft_v2", *ST.V2_WRITER_SIDE_FAMILIES):
        for verb, sql in (("UPDATE", f"UPDATE {table} SET ordinal = ordinal + 1"),
                          ("DELETE", f"DELETE FROM {table}")):
            expect_raises(f"{table} 拒绝 {verb}（append-only）",
                          lambda s=sql: _exec(db, s), sqlite3.IntegrityError,
                          f"{table} 是不可变历史表，禁止 {verb}")
    check(_count(db, "current_section_draft_v2") == 1,
          "触发器 ABORT 之后 root 行仍在（不是静默跳过）")


# ---------------------------------------------------------------------------
# 4. 公共 reader 绕门
# ---------------------------------------------------------------------------

def _check_public_reader_bypass(db: Path, draft: NS.SectionDraft) -> None:
    current_result = SS.SectionResult(
        section_result_id="sr_cur", schema_version=SS.SECTION_RESULT_SCHEMA_VERSION,
        section_version="secver_cur", section_draft_id=draft.draft_id, task_id=_TASK,
        section_id=_SECTION, status="COMPLETED")
    expect_raises("commit_section_result 拒 current SectionResult",
                  lambda: ST.commit_section_result(current_result),
                  ST.SectionStorageConflictError, "只承载 legacy 产物")
    expect_raises("commit_section_result 拒裸 dict",
                  lambda: ST.commit_section_result({"task_id": _TASK}),
                  ST.SectionStorageConflictError, "必须是显式 legacy 视图")
    expect_raises("commit_section_result 拒 current SectionDraft（门前对象也不得走 v1 入口）",
                  lambda: ST.commit_section_result(draft),
                  ST.SectionStorageConflictError, "必须是显式 legacy 视图")
    check(_count(db, "section_result") == 1,
          "三次被拒之后 v1 表行数不变（拒的是写入，不是先写后删）")

    # legacy narr-3 视图不是 current SectionDraft：不得进 current 容器。
    legacy_view = NS.Narr3SectionDraftView
    expect_raises("SectionChainV2 拒非 current Draft（legacy narr-3 视图只走 legacy reader）",
                  lambda: ST.SectionChainV2(draft=legacy_view), ST.SectionStorageConflictError,
                  "必须是 current")
    expect_raises("SectionChainV2 拒裸 dict（不得从容器偷渡未登记字段）",
                  lambda: ST.SectionChainV2(draft=draft.to_dict()),
                  ST.SectionStorageConflictError, "必须是 current")


# ---------------------------------------------------------------------------
# 5. identity 重算与篡改检测
# ---------------------------------------------------------------------------

def _check_identity_recompute(db: Path, draft: NS.SectionDraft) -> None:
    loaded = ST.load_current_section_chain_v2(draft.draft_id)
    check(loaded is not None and loaded.draft.draft_id == draft.draft_id,
          "读回用 member 行重建出同一 draft_id")
    check(loaded.draft.identity_body() == draft.identity_body(),
          "读回的 identity_body 与写入侧逐字段相同（重算是真的，不是把 root 快照还回来）")
    check(loaded.draft.draft_revision == draft.draft_revision
          and loaded.draft.material_manifest.manifest_id == draft.material_manifest.manifest_id,
          "读回重算的 draft_revision 与 manifest 身份一致（含 exact manifest）")
    check(sorted(loaded.families)
          == sorted((*ST.V2_WRITER_SIDE_FAMILIES, *ST.V2_DECISION_FAMILIES)),
          "读回的 family 集恰为 writer 侧四族 + 3C 三族（不读门后族、也不漏读决定族）")
    check(loaded.aggregate_decisions
          and loaded.entailment_decisions and loaded.accepted_bindings,
          "读回把三类门侧对象用各自 reader 重建出来（不是只回 payload dict）")
    check(loaded.to_chain().member_rows() == _chain(draft).member_rows(),
          "读回结果再提交得到的行集与首次提交逐行相同（往返稳定）")
    check(len(loaded.members["current_section_material_manifest_v2"]) == 1,
          "读回的 exact material manifest 恰 1 行")

    # 兜底：`_rebuild_draft_v2` 末端的「重建身份 vs root 声明」比对本批无法从**载荷**触发
    # （content-addressed id 会先一步拦住，见 (b1)/(b2)），因此在这里直接以被改的 root 声明
    # 触发它：manifest 指纹是重建产物，被改的 root 声明必然与重建结果不符。
    conn = _connect(db)
    try:
        _keys, payloads = ST._v2_collect_rows(conn, draft.draft_id)
        root_row = _query(db, "SELECT payload_json FROM current_section_draft_v2 "
                              "WHERE draft_id = ?", (draft.draft_id,))[0]["payload_json"]
    finally:
        conn.close()
    forged_root = json.loads(root_row)
    forged_root["manifest_fingerprint"] = "0" * 64
    expect_raises("兜底比对：root 声明的重建产物与重建结果不符，必须 fail-closed",
                  lambda: ST._rebuild_draft_v2(draft.draft_id, forged_root, payloads),
                  ST.SectionStorageCorruptionError, "身份与 root 载荷不一致")

    again = ST.commit_section_chain_v2(_chain(draft))
    check(again["reused"] is True
          and again["family_rows"] == ST.commit_section_chain_v2(
              _chain(draft))["family_rows"],
          "同 draft_id 再提交走复用路径（reused=True，行数稳定）")
    check(_count(db, "current_section_draft_v2") == 1
          and _count(db, "current_section_wmpd_v2") == 1,
          "复用不追加第二份 root/member（append-only 下也不重复写）")

    proposal_id = draft.proposed_support_ids[0]
    proposal_where = "proposal_id = ?"

    # (a) 载荷被改而列指纹未重算：指纹不符。**复用路径也必须发现**——只比主键不够。
    _rewrite_payload(db, "current_section_proposal_v2", proposal_where, (proposal_id,),
                     lambda p: p.update(support_role="secondary"))
    _exec(db, "UPDATE current_section_proposal_v2 SET content_fingerprint = ? "
              "WHERE proposal_id = ?", ("0" * 64, proposal_id))
    expect_raises("列指纹与载荷不符：读回必须 fail-closed",
                  lambda: ST.load_current_section_chain_v2(draft.draft_id),
                  ST.SectionStorageCorruptionError, "载荷指纹与列不符")
    expect_raises("列指纹与载荷不符：复用路径也必须 fail-closed（逐行重算，不只比主键）",
                  lambda: ST.commit_section_chain_v2(_chain(draft)),
                  ST.SectionStorageCorruptionError, "载荷指纹与列不符")

    # (b1) 指纹改回自洽但载荷已非法：重建这一行时就拒（连 Draft 都重建不出来）。
    _exec(db, "UPDATE current_section_proposal_v2 SET content_fingerprint = ? "
              "WHERE proposal_id = ?",
          (ST._v2_fingerprint(json.loads(_query(
              db, "SELECT payload_json FROM current_section_proposal_v2 WHERE proposal_id = ?",
              (proposal_id,))[0]["payload_json"])), proposal_id))
    expect_raises("被改行连重建都做不到（非法 support_role）：读回必须 fail-closed",
                  lambda: ST.load_current_section_chain_v2(draft.draft_id),
                  ST.SectionStorageCorruptionError, "无法重建出同一 Draft")
    expect_raises("被改行连重建都做不到：复用路径必须靠**重建**发现，不得当作复用放行",
                  lambda: ST.commit_section_chain_v2(_chain(draft)),
                  ST.SectionStorageCorruptionError, "无法重建出同一 Draft")

    # (b2) 改一个**仍然合法**的字段（候选正文）：content-addressing 先拦住——改正文就改了
    # candidate_id，行自身的 id 自洽性立刻不符。这说明「改内容」在 store 层走不到
    # `_rebuild_draft_v2` 末端的身份比对，身份比对是**兜底**而非唯一防线。
    _rewrite_payload(db, "current_section_subject_v2", "subject_kind = ? AND subject_id = ?",
                     ("claim_candidate", draft.claim_candidate_ids[0]),
                     lambda p: p.update(claim_text="公司2025年营业收入为9999.99亿元。"))
    expect_raises("被改行仍然合法但 id 自洽性不符（content-addressing 层）：读回必须 fail-closed",
                  lambda: ST.load_current_section_chain_v2(draft.draft_id),
                  ST.SectionStorageCorruptionError, "无法重建出同一 Draft")
    expect_raises("被改行仍然合法但 id 自洽性不符：复用路径同样必须 fail-closed",
                  lambda: ST.commit_section_chain_v2(_chain(draft)),
                  ST.SectionStorageCorruptionError, "无法重建出同一 Draft")

    # (c) 载荷 marker 被改成 legacy 值：current reader 不得读成 legacy。
    _rewrite_payload(db, "current_section_wmpd_v2", "disposition_id = ?",
                     (draft.material_disposition_ids[0],),
                     lambda p: p.update(schema_version="narr-3"))
    expect_raises("member 载荷 marker 为 legacy 值时 current reader 必须拒",
                  lambda: ST.load_current_section_chain_v2(draft.draft_id),
                  ST.SectionStorageCorruptionError, "载荷 wire marker 不是")
    _rewrite_payload(db, "current_section_draft_v2", "draft_id = ?", (draft.draft_id,),
                     lambda p: p.update(schema_version="narr-3"))
    expect_raises("root 载荷 marker 为 legacy 值时 current reader 必须拒",
                  lambda: ST.load_current_section_chain_v2(draft.draft_id),
                  ST.SectionStorageCorruptionError, "legacy 载荷只经 legacy reader 读")


# ---------------------------------------------------------------------------
# 6. 行集与 root 声明的 exact-set 逐项相等
# ---------------------------------------------------------------------------

def _check_exact_set(db: Path) -> None:
    draft = _draft(title="精确集")
    body = draft.identity_body()
    by_family: dict[str, list[str]] = {}
    for family, pk_values, _marker, _payload, _promoted in ST.SectionChainV2(
            draft=draft).member_rows():
        by_family.setdefault(family, []).append(ST._v2_row_key(pk_values))
    check(sorted(by_family["current_section_material_manifest_v2"]) == [body["manifest_id"]],
          "manifest 行集恰等于 root 声明的 manifest_id（0/1 行，不多不少）")
    check(sorted(by_family["current_section_wmpd_v2"])
          == sorted(body["material_disposition_ids"]),
          "WMPD 行集恰等于 root 声明的 material_disposition_ids")
    check(sorted(by_family["current_section_subject_v2"]) == sorted(
        [f"claim_candidate:{i}" for i in body["claim_candidate_ids"]]
        + [f"narrative_draft_unit:{i}" for i in body["narrative_draft_unit_ids"]]),
        "subject 行集恰等于 root 声明的候选 + 草稿单元（两类同族、逐项相等）")
    check(sorted(by_family["current_section_proposal_v2"])
          == sorted(body["proposed_support_ids"]),
          "proposal 行集恰等于 root 声明的 proposed_support_ids")

    ST.commit_section_chain_v2(_chain(draft))
    check(_count(db, "current_section_subject_v2", "draft_id = ?", (draft.draft_id,)) == 2,
          "真库中 subject 行数 = 1 候选 + 1 草稿单元（行集与声明逐项相等）")

    # 多一行：append-only 允许 INSERT，于是可以在库上制造「行集多出」。
    extra = NS.WriterMaterialProcessingDisposition.create(
        manifest_id=draft.material_manifest.manifest_id,
        manifest_fingerprint=draft.material_manifest.fingerprint(), pack_id="pack1",
        material_id="m9", research_material_disposition_id="rmd9",
        material_content_fingerprint="c" * 64, processed=False, usage="not_used",
        support_usages=(), reason_code="irrelevant_to_section_goal",
        reason_proof={"policy_version": "relpol-1", "policy_fingerprint": "b" * 64,
                      "relevance_decision_ref": "reldec_1"},
        writer_policy_version=_WRITER_POLICY)
    payload = extra.to_dict()
    _exec(db, "INSERT INTO current_section_wmpd_v2 (draft_id, disposition_id, member_ref, "
              "ordinal, schema_version, content_fingerprint, payload_json, created_at) "
              "VALUES (?,?,?,?,?,?,?,?)",
          (draft.draft_id, extra.wmpd_id, extra.member_ref, 1, extra.schema_version,
           ST._v2_fingerprint(payload),
           json.dumps(payload, ensure_ascii=False, sort_keys=True), "2026-01-01T00:00:00Z"))
    check(_count(db, "current_section_wmpd_v2", "draft_id = ?", (draft.draft_id,)) == 2,
          "对照：库里确实多了一行 WMPD（WMPD 行集现在比声明多 1）")
    expect_raises("已存链多出一行：复用前必须拒（复用不是「root 在就算数」）",
                  lambda: ST.commit_section_chain_v2(_chain(draft)),
                  ST.SectionStorageCorruptionError, "exact-set 不相等")
    expect_raises("已存链多出一行：读回必须拒（多出的成员让 Draft 根本重建不出来）",
                  lambda: ST.load_current_section_chain_v2(draft.draft_id),
                  ST.SectionStorageCorruptionError, "无法重建出同一 Draft")


def _check_table_closure() -> None:
    writer = set(ST.V2_WRITER_SIDE_FAMILIES)
    decision = set(ST.V2_DECISION_FAMILIES)
    post = set(ST.V2_POST_GATE_FAMILIES)
    check(not (writer & post) and not (decision & post) and not (writer & decision),
          "writer 侧 / 3C 决定 / 门后 family 两两不相交（同一表不得两属）")
    check(writer | decision | post | {"current_section_draft_v2"} == set(ST.V2_SECTION_TABLES),
          "v2 表清单闭合 = writer 侧 ∪ 3C 决定 ∪ 门后 ∪ Draft root（无未登记表、无遗漏表）")
    check(set(ST.V2_FAMILY_PK_COLUMNS) == writer | decision | post,
          "每个已落库 family（writer 侧 ∪ 决定 ∪ 门后）都登记了主键列名（写库侧不猜列名）")
    for family in (*ST.V2_WRITER_SIDE_FAMILIES, *ST.V2_POST_GATE_FAMILIES):
        check(family in ST.V2_FAMILY_WIRE_MARKER,
              f"{family} 登记了 wire marker（逐 payload discriminator）")
    migration_text = "".join(ST._migration_5_statements())
    check(not any("follow_up" in f for f in ST.V2_SECTION_TABLES)
          and "FollowUpNeed" not in migration_text
          and "topic_follow_up" not in migration_text,
          "P17-14：本文件不建 follow-up 表（FollowUpNeed 属边界③ harness/topic_store.py）")
    check(not hasattr(ST, "commit_followup_need") and not hasattr(ST, "load_followup_needs"),
          "sections/store.py 不提供 follow-up 读写入口（边界③ 不越界）")


def _check_decision_cardinality() -> None:
    """3C 反例（§16.10 风格的**逐项**缺/多/重）：提交面必须比「主键对得上」更严。

    每条反例都从真实门产出的一条合法链出发，只破坏一个基数维度。缺一条决定就能让
    「每个 subject revision 恰一条」变成纸面约束——所以这些必须由 store 自己拒，而不是
    指望调用方（或某次跑过的门）自觉。
    """
    import copy

    draft = _draft(title="基数")
    chain = _chain(draft)

    def commit(**over):
        """无参 = 提交真实门产出的完整合法链；带参数 = 只破坏一个基数维度。"""
        return ST.commit_section_chain_v2(
            chain if not over else ST.SectionChainV2(draft=draft, **over))

    def kw(**over):
        base = {"aggregate_decisions": chain.aggregate_decisions,
                "entailment_decisions": chain.entailment_decisions,
                "accepted_bindings": chain.accepted_bindings}
        base.update(over)
        return base

    commit()  # 对照：合法链可提交（本函数自己的前置条件）

    # 缺一条 aggregate 决定（某 subject 没有任何决定）。
    expect_raises("缺一条 aggregate 决定（subject 无决定）→ 拒",
                  lambda: commit(**kw(aggregate_decisions=chain.aggregate_decisions[:-1])),
                  ST.SectionStorageConflictError, "subject 集与 draft 的 proposal subject 集不相等")
    # 多一条：把同一条决定复制成本链之外的 subject。
    extra = copy.copy(chain.aggregate_decisions[0])
    object.__setattr__(extra, "binding_decision_id", "cbd_ghost")
    object.__setattr__(extra, "subject_id", "ccand_ghost")
    expect_raises("多一条 aggregate 决定（非本链 subject）→ 拒",
                  lambda: commit(**kw(aggregate_decisions=(*chain.aggregate_decisions, extra))),
                  ST.SectionStorageConflictError, "subject 集与 draft 的 proposal subject 集不相等")
    # 重复：同一 subject 两条决定。
    dup = copy.copy(chain.aggregate_decisions[0])
    expect_raises("同一 subject 两条 aggregate 决定 → 拒",
                  lambda: commit(**kw(aggregate_decisions=(*chain.aggregate_decisions, dup))),
                  ST.SectionStorageConflictError, "每个 subject revision 恰好一条")
    # 跨 revision 的决定不得混装。
    other_rev = copy.copy(chain.aggregate_decisions[0])
    object.__setattr__(other_rev, "draft_revision", "rev_other")
    expect_raises("跨 revision 的 aggregate 决定 → 拒",
                  lambda: commit(**kw(aggregate_decisions=(*chain.aggregate_decisions[:-1],
                                                           other_rev))),
                  ST.SectionStorageConflictError, "属于另一 draft revision")

    # 通过的 factual candidate 缺 entailment 决定。
    expect_raises("通过的 factual candidate 缺 entailment 决定 → 拒",
                  lambda: commit(**kw(entailment_decisions=())),
                  ST.SectionStorageConflictError, "缺 entailment 决定")
    # 同一 candidate revision 两条 entailment 决定。
    expect_raises("同一 candidate revision 两条 entailment 决定 → 拒",
                  lambda: commit(**kw(entailment_decisions=(*chain.entailment_decisions,
                                                            chain.entailment_decisions[0]))),
                  ST.SectionStorageConflictError, "恰好一条")
    # entailment 决定的 digest 与 aggregate 不同一（伪造绑定）。
    forged = copy.copy(chain.entailment_decisions[0])
    object.__setattr__(forged, "support_set_digest", "0" * 64)
    expect_raises("entailment 决定未绑定同一 support-set digest → 拒",
                  lambda: commit(**kw(entailment_decisions=(forged,))),
                  ST.SectionStorageConflictError, "同一 support-set digest")
    # 凭空造一条属于不存在候选的 entailment 决定（伪造决定）。
    ghost_ent = copy.copy(chain.entailment_decisions[0])
    object.__setattr__(ghost_ent, "claim_candidate_id", "ccand_ghost")
    expect_raises("entailment 决定指向没有 aggregate 决定的候选 → 拒",
                  lambda: commit(**kw(entailment_decisions=(*chain.entailment_decisions,
                                                            ghost_ent))),
                  ST.SectionStorageConflictError, "没有 aggregate 决定的候选")
    # failed aggregate 之上不得再跑语义门：它自身就是机械拒绝的 audit。
    failed = copy.copy(chain.aggregate_decisions[0])
    object.__setattr__(failed, "result", "fail")
    expect_raises("aggregate 未通过的候选仍有 entailment 决定 → 拒",
                  lambda: commit(**kw(aggregate_decisions=(failed,))),
                  ST.SectionStorageConflictError, "aggregate 未通过的候选")

    # accepted binding：指向未通过的 proposal / 重复 / 缺一条 / context 带 entailment。
    binding = chain.accepted_bindings[0]
    ghost = copy.copy(binding)
    object.__setattr__(ghost, "proposed_support_id", "psr_ghost")
    expect_raises("accepted binding 指向未通过的 proposal → 拒",
                  lambda: commit(**kw(accepted_bindings=(ghost,))),
                  ST.SectionStorageConflictError, "未通过的 proposal")
    expect_raises("同一 proposal 两条 accepted binding → 拒",
                  lambda: commit(**kw(accepted_bindings=(binding, binding))),
                  ST.SectionStorageConflictError, "恰好一条")
    expect_raises("通过的 proposal 缺 accepted binding → 拒",
                  lambda: commit(**kw(accepted_bindings=())),
                  ST.SectionStorageConflictError, "缺 accepted binding")
    factual = next(b for b in chain.accepted_bindings if b.support_semantics == "factual")
    no_entail = copy.copy(factual)
    object.__setattr__(no_entail, "entailment_decision_id", None)
    expect_raises("factual accepted binding 未引用本链的 entailment 决定 → 拒",
                  lambda: commit(**kw(accepted_bindings=(no_entail,))),
                  ST.SectionStorageConflictError, "未引用本链")
    # 反向同样要拒：context 支撑边带 entailment 引用等于把事实语义塞进背景材料。
    ctx = copy.copy(factual)
    object.__setattr__(ctx, "support_semantics", "context")
    expect_raises("context accepted binding 携带 entailment 决定 → 拒",
                  lambda: commit(**kw(accepted_bindings=(ctx,))),
                  ST.SectionStorageConflictError, "携带 entailment")

    # 反例之间不得互相污染：合法链仍然可提交（复用路径）。
    check(commit()["reused"] is True, "反例之后合法链仍可提交（反例未污染真实链）")


def main() -> dict:
    # Windows 下临时目录清理可能残留句柄；显式 close + 忽略清理错误。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "sections.sqlite"
        ST.init_db(db)

        # legacy 链先落库，作为「两条读面互不冒充」的对照。
        plan = _legacy_plan("plan1")
        ST.commit_plan(plan)
        legacy = _legacy_result(plan.section_tasks[0].task_id)
        ST.commit_section_result(legacy)
        check(ST.load_legacy_section_chain_for_audit(legacy.section_result_id) is not None,
              "对照：legacy 链已落 v1 表")
        v1_before = {t: _count(db, t) for t in _V1_FACTS}

        draft = _draft()
        _check_ledger_and_markers(db, draft)
        _check_root_isolation(db, draft, legacy.section_result_id, v1_before)
        _check_append_only_triggers(db)
        _check_public_reader_bypass(db, draft)
        _check_identity_recompute(db, draft)
        _check_decision_cardinality()
        _check_exact_set(db)
        _check_table_closure()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    raise SystemExit(0 if r["failed"] == 0 else 1)

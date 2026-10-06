"""M930-3 反例集：`sections.pack_writer` 的「权威输入封闭 + 生产者隔离 + 不发明」必须自证。

本模块是**反例优先**的（里程碑 §11「每个缺陷只增加能唯一对应该缺陷的反例」）。它不读库、
不连网、不跑真实文档、不调真实 LLM：所有权威输入都是按 `VerifiedPackSet` /
`FinancialPackArtifact` 的字段形状手工搭的替身，注入的 `NarrationClient` 是纯 stub。
因此断言在任何环境下都可稳定复现，且不会消耗任何真实配额。

覆盖该缺陷族（对应任务书 §八 反例清单）：
 1. PackSet 不完整 / 重复 / 非本任务 / 非本节 / topic 集合不一致 / 非本报告基准日；
 2. PackSet 内多 Pack 身份不一致；调用方声明与权威身份冲突；
 3. company/industry 混装 `FinancialFactPack`、financial 混装 `VerifiedPackSet`（双向拒）；
 4. derived 绕过上游身份（未被 DemoScope 选中 / 身份字段缺失 / 无选中 fact）；
 5. Writer 试图检索：计划里出现任何未登记字段（工具/检索意图）即拒；模块不具备检索入口；
 6. 事实句无 Claim、句子引用计划外 Claim、表格行既无 Claim 又未标非事实（或两者兼有）；
 7. 表格缺 entity_scope/unit/period、非事实行标记不在声明集合内、表格无 topic 归属；
 8. 裸数字 / 非权威数字进入句子（由 narrative 硬门独立复核后 fail-closed）；
 9. `partial/blocked/not_found` 不得被改写成 covered；「未取得」不得写成「不存在/未披露」；
10. required fact 未被写入时必须变成显式 unresolved，且不得静默丢失；
11. 附注缺口必须进入 Narrative/Unresolved；附注事实必须带 3 元 locator；
12. 重试策略只允许 0/1 次；连续被拒即 fail-closed，不得降级放行。

§三 A 追加（不在任务书 §八 的十二条里，故单列而不重编号）：材料正文上下文 `wmctx-1` 的
篡改面 —— resolver 缺席 / payload 缺失 / 字节被换（哈希不符）/ manifest 成员的 payload 哈希、
读视图指纹、locator 被改 / 成员不在上下文里 / 上下文来自别的 PackSet，逐条 typed fail-closed
（判据是封闭原因码，不是消息措辞）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_runtime as TR
from harness import topic_schema as TS
from planning import schema as PS
from sections import financial_pack_artifact as FPA
from sections import material_context as MC
from sections import narrative_schema as NS
from sections import pack_set as PSet
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import report_assembler as RA
from sections import rules_evaluator as RE
from sections import schema as SS
from sections import writing_spec as WS

SPEC_PATH = str(Path(__file__).resolve().parent.parent
                / "templates" / "writing_specs" / "credit_report_v1.yaml")
PROFILE_PATH = str(Path(__file__).resolve().parent.parent
                   / "templates" / "presentation_profiles" / "interview_demo_v1.yaml")

COMPANY_ID = "示例科技股份有限公司"       # 非 300750 样本：规则必须完全一致
REPORT_AS_OF = "2026-06-30"
#: O-11：报告生成日（运行级业务日期）与财务期末是**两个**量。多数用例让两者同值（历史形状），
#: 只有专门验证二者分离的用例才让它们取不同值——否则那段代码根本没有被测到。
REPORT_GENERATION_DAY = "2026-09-23"
FINANCIAL_PERIOD_END = "2026-03-31"
CONTRACT_VERSION = "v2"
CONTRACT_FINGERPRINT = "a" * 64
EV_ID = "ev-demo-1"
NOTE_EV_ID = "ev-demo-note-1"

# Contract 槽位逐字取自 frozen WritingSpec（不新造 aspect 词表，也不写公司专用规则）。
ASP_BUSINESS_MAIN = "company_business_main.main_business"
ASP_BUSINESS_SALES = "company_business_model.sales_mode"
ASP_BUSINESS_COST = "company_business_model.cost_structure"
ASP_IDENTITY_CAPITAL = "company_identity_basic.registered_capital"
ASP_LITIGATION_MAJOR = "company_litigation.major_litigation"
ASP_INDUSTRY_SCALE = "industry_scale_cycle.industry_scale"
ASP_FIN_SOLVENCY = "fin_solvency.short_term_solvency"
ASP_FIN_SHEET = "fin_statements_availability.balance_sheet_ready"

TOPIC_IDENTITY = "company_identity"
TOPIC_BUSINESS = "company_business"
TOPIC_LEGAL = "company_legal_risks"
TOPIC_FIN_SCOPE = "fin_source_scope"
TOPIC_FIN_SOLVENCY = "fin_solvency"
TOPIC_INDUSTRY_SCALE = "industry_scale_cycle"


# ---------------------------------------------------------------------------
# 合成工厂：**真实类型**的上游权威对象（不读库、不连网、不调真实 LLM）
#
# §三 P0：`WorkerAuthorityInput` 只接受真实类型的上游对象（`VerifiedPackSet` /
# `TopicResearchPack` / `FinancialPackArtifact` / `ValidatedEvidenceNoteFactSet` /
# `EvidenceNoteGap`），字段形状相同的替身一律被拒。因此本文件不再手搭「形状替身」，
# 而是用真实类型的公共构造器合成权威输入；下面带 `_` 的 dataclass 只是**测试侧规格**
# （`_AspectReq` / `_Req` / `_AspectResult` / `_Material` / `_Fact` / `_Pack` / `_PackSet`），
# 由 `_pack_set` 一次性物化成真实对象。
# ---------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SOURCE_POLICY_VERSION = "sp-demo-1"
DEPENDENCY_VERSIONS = TS.build_current_dependency_versions(
    contract_version=CONTRACT_VERSION, source_policy_version=SOURCE_POLICY_VERSION)
DEPENDENCY_FINGERPRINT = TS.compute_dependency_fingerprint(
    CONTRACT_FINGERPRINT, SOURCE_POLICY_VERSION, DEPENDENCY_VERSIONS)
DOCUMENT_ID = "doc-demo-1"
DOCUMENT_VERSION = "v1"
EVIDENCE_SET_VERSION = "es-demo-1"
SNAPSHOT_ID = "snap-demo-1"

#: 已登记的 material payload 信封字节（键 = sha256(字节) = `payload_ref.content_hash`）。
#: 真实链上这些字节来自 Evidence store 的 BLOB；夹具里由 `_material_of_identity` 生成，
#: `_StubPayloadResolver` 只负责「按 content_hash 取回」。**这不是替代校验**：
#: `TS.verify_material_payload_ref` 与 `wmctx-1` 的读取侧仍逐项复核身份/定位/哈希。
_PAYLOAD_BYTES: dict[str, bytes] = {}

#: 显式登记的 material 正文（键 = `(material_id, source_identity)`）。正文是 material 的
#: **内容**：同一份 material 被重新物化时必须拿到同一串字节，否则「材料重建后正文变了」这类
#: 假象会混进被测行为里。`_default_material_text` 优先读这里。
_MATERIAL_TEXTS: dict[tuple[str, str], str] = {}

#: `_aspect()` 的 `time_scope` 缺省值：**保守**取「按基准日 + 24 个月变动」，
#: 即「期间必须显式」。只有测试显式声明结构性 aspect 时期间才可选（§六）。
DEFAULT_TIME_SCOPE = "CURRENT_AS_OF_WITH_24M_CHANGES"


def _policy_ref() -> TS.SourcePolicyRef:
    return TS.SourcePolicyRef(policy_id="sp-demo", policy_version=SOURCE_POLICY_VERSION,
                              content_fingerprint=_sha("policy:" + SOURCE_POLICY_VERSION))


def _evidence_requirement_ref(aspect_id: str) -> TS.EvidenceRequirementRef:
    return TS.EvidenceRequirementRef(
        requirement_id=f"er-{aspect_id}", contract_sha256=_sha("contract"),
        requirement_fingerprint=_sha(f"req:{aspect_id}"), schema_version="1")


def _evidence_authority(evidence_id: str, *, page: int | None = 12
                        ) -> TS.EvidenceAuthorityAssessment:
    """证据来源权威：`verdict` 与其确定性重算一致（`is_current_*` + 定址 + sha256）。"""
    return TS.EvidenceAuthorityAssessment(
        evidence_id=evidence_id, document_id=DOCUMENT_ID,
        document_version=DOCUMENT_VERSION, company_id=COMPANY_ID,
        is_current_document=True, is_current_set=True, page=page,
        fetched_inspected_nonempty=True, content_hash=_sha(f"evidence:{evidence_id}"),
        verdict="authoritative", reason="", validator_version="vv-demo-1")


def _aspect_snapshot(spec: "_AspectReq") -> TS.TopicAspectRequirementSnapshot:
    """测试侧规格 → 真实 `TopicAspectRequirementSnapshot`。"""
    return TS.TopicAspectRequirementSnapshot(
        aspect_id=spec.aspect_id, question_id=spec.question_id, topic_id=spec.topic_id,
        requirement_text=spec.requirement_text or f"{spec.aspect_id} 的要求文本",
        kind=spec.kind, producer_kind="topic_harness", execution_path="direct",
        required_fields=(), coverage_rules=("direct_support",),
        complete_set_rule=spec.complete_set_rule,
        evidence_requirement_ids=(_evidence_requirement_ref(spec.aspect_id),),
        source_policy_ref=_policy_ref(), time_scope=spec.time_scope,
        display_tier=spec.display_tier, content_role=spec.content_role,
        missing_policy="none", blocking_policy=tuple(spec.blocking_policy),
        applicability_policy=None, impact_scope=tuple(spec.impact_scope),
        output_destination="body", derived_from=(), business_review_status="none",
        contract_version=CONTRACT_VERSION, contract_sha256=_sha("contract"),
        canonical_fingerprint=_sha(f"canonical:{spec.aspect_id}"),
        dependency_fingerprint=DEPENDENCY_FINGERPRINT)


@dataclasses.dataclass(frozen=True)
class _AspectReq:
    """`TopicAspectRequirementSnapshot` 的**测试侧规格**（由 `_aspect_snapshot` 物化）。

    `kind` / `time_scope` 是 §六 期间语义的判据来源：缺省 `fact_set` +
    `CURRENT_AS_OF_WITH_24M_CHANGES`（期间必须显式），需要「结构性、期间可选」或
    「指标/事件类」的用例显式声明。
    """

    aspect_id: str
    topic_id: str
    question_id: str
    impact_scope: tuple[str, ...] = ("subject",)
    blocking_policy: tuple[str, ...] = ()
    display_tier: str = "required_body"
    content_role: str = "paragraph"
    kind: str = "fact_set"
    time_scope: str = DEFAULT_TIME_SCOPE
    complete_set_rule: str = ""
    requirement_text: str = ""


@dataclasses.dataclass(frozen=True)
class _Req:
    topic_id: str
    aspects: tuple[_AspectReq, ...]


@dataclasses.dataclass(frozen=True)
class _AspectResult:
    aspect_id: str
    status: str


@dataclasses.dataclass(frozen=True)
class _Material:
    material_id: str
    source_identity: str
    #: material 自己的定位页。同一来源身份可以有多个候选（同页多段 / 跨页续写），
    #: 因此 `page` 是候选消歧的**唯一**依据；默认 12 与 `_citation()` 缺省页一致。
    page: int | None = 12


@dataclasses.dataclass(frozen=True)
class _ValueIdentity:
    """`ValueIdentity` 的**测试侧规格**（数字权威比对会读这几个字段）。"""

    amount_canonical: str
    value_kind: str = "amount"
    metric: str = ""
    unit: str = ""
    period: str = ""
    scope: str = ""


@dataclasses.dataclass(frozen=True)
class _Fact:
    fact_id: str
    text: str
    aspect_ids: tuple[str, ...]
    citation_refs: tuple = ()
    period: str = ""
    scope: str = ""
    value_identity: _ValueIdentity | None = None
    #: `mbind-1`：这条事实资格链**声明的输入材料**。缺省（`None`）= 单元素 {它自己的来源
    #: material}，一切旧夹具逐字不变；显式给多个 ⇒ 该事实的出处本身就不唯一，材料绑定**不得**
    #: 据此收窄（这正是「同页歧义仍然 fail-closed」的反例需要的那一格）。
    material_ids: tuple[str, ...] | None = None


@dataclasses.dataclass(frozen=True)
class _Pack:
    """`TopicResearchPack` 的**测试侧规格**（由 `_pack_set` 物化 + `TS.finalize_pack`）。

    `pack_id` 只是测试内标签：真实 `pack_id` 是**内容身份**（`content_fingerprint +
    dependency_fingerprint`），由 `TS.finalize_pack` 计算，不得由测试指定。
    """

    topic_id: str
    pack_id: str = ""
    run_id: str = "run-demo-1"
    company_id: str = COMPANY_ID
    report_as_of: str = REPORT_AS_OF
    contract_version: str = CONTRACT_VERSION
    contract_fingerprint: str = CONTRACT_FINGERPRINT
    facts: tuple = ()
    materials: tuple = ()
    aspect_results: tuple = ()
    gaps: tuple = ()
    conflicts: tuple = ()
    not_found_audits: tuple = ()
    #: formal `ExternalFact` 族（M930-3B #32）：它们由 current Pack 持久化引用，容器身份是
    #: **snapshot 记录**（`NS.external_authority_container_id`），与 Pack 容器分开登记，
    #: 因此 Path A 必须对它们同样可达（不得出现「进了 Pack 却读不到」的死路）。
    external_facts: tuple = ()
    #: `external_facts` 的资格链（`(FactCandidate, FactQualificationDecision)` 对）。Pack 的
    #: qualified-exact-set 校验要求资格决定回指**本 Pack 内**的候选，因此 external 的候选与
    #: 决定必须和事实一同进入 Pack（只放事实会被真实类型在构造期拒）。
    external_chains: tuple = ()
    #: 是否按事实引用**自动补** material（真实 Pack 的形状）。反例用例显式关掉它，用来构造
    #: 「事实引用的来源身份在本容器内没有 material」这一上游残缺形态。
    auto_materials: bool = True
    #: 本节 Pack 的**来源集**（`TopicResearchPack.source_set`）。默认是单文档夹具；离线重放
    #: 这类「复刻某次真实 run」的夹具必须把自己的真实来源集带进来——来源**角色**（`srsc-1`）
    #: 就是从这张表读的，夹具带错来源集会让角色台账与实际材料对不上，判据只能 fail-closed。
    source_set: TS.DocumentSourceSet | None = None


@dataclasses.dataclass(frozen=True)
class _PackSet:
    """`VerifiedPackSet` 的**测试侧规格**（由 `_pack_set` 物化）。"""

    task_id: str
    section_id: str
    topic_ids: tuple[str, ...]
    packs: tuple[_Pack, ...] = ()
    requirements: tuple[_Req, ...] = ()


@dataclasses.dataclass(frozen=True)
class _FinFact:
    """`FinancialFactProjection` 的**测试侧规格**（由 `_Artifact` 物化）。

    真实 artifact 里每条 fact 的 `citation.snapshot_id` 必须等于 artifact 快照身份，
    因此物化时会把缺省的 `snapshot_id` 补进 citation（测试不必逐条重复填写）。
    """

    fact_id: str
    label: str
    display: str
    period: str
    unit: str = ""
    value_text: str | None = "65.43"
    citation: dict | None = None
    kind: str = "ratio"
    code: str = ""
    status: str = "ok"
    reason_code: str | None = None
    note: str = ""


def _snapshot_identity(company_id: str, report_as_of: str) -> FPA.FinancialSnapshotIdentity:
    return FPA.FinancialSnapshotIdentity(
        snapshot_id=SNAPSHOT_ID, company_id=company_id, as_of_date=report_as_of,
        scope="合并", currency="CNY", purpose="credit_report", exists=True,
        is_current=True, validity="valid", report_blocked=False, quarantined=False)


def _Artifact(*, artifact_id: str = "", task_id: str, company_id: str = COMPANY_ID,
              report_as_of: str = REPORT_AS_OF, contract_version: str = CONTRACT_VERSION,
              contract_fingerprint: str = CONTRACT_FINGERPRINT, facts: tuple = (),
              gaps: tuple = (), diagnostic_gaps: tuple = (), periods: tuple = (),
              statements_available: tuple = (), excluded: tuple = (),
              projection_notes: tuple = (), snapshot: object | None = None,
              fact_selection_rule_version: str = "fsr-demo-1",
              projection_id: str = "proj-demo-1") -> FPA.FinancialPackArtifact:
    """真实 `FinancialPackArtifact` 的合成工厂（身份/内容指纹由真实实现计算）。

    `artifact_id` 只是测试内标签：真实 `artifact_id` 是**内容身份**
    （`"ffpa_" + content_fingerprint[:24]`），由 `verify()` 重算核对，不得由测试指定。
    `snapshot` 参数保留为「扁平替身」入口仅供历史用例传 `None`；真实身份一律取自
    `FinancialSnapshotIdentity`。
    """
    del artifact_id  # 只作标签：真实 id 由内容指纹决定（`verify()` 会重算核对）
    snapshot_identity = snapshot if isinstance(snapshot, FPA.FinancialSnapshotIdentity) \
        else _snapshot_identity(company_id, report_as_of)
    projections = []
    for spec in facts:
        citation = dict(spec.citation or {})
        citation.setdefault("snapshot_id", snapshot_identity.snapshot_id)
        projections.append(FPA.FinancialFactProjection(
            fact_id=spec.fact_id, kind=spec.kind, label=spec.label, code=spec.code,
            period=spec.period, value_text=spec.value_text, display=spec.display,
            unit=spec.unit, status=spec.status, reason_code=spec.reason_code,
            note=spec.note, citation=citation,
            # `ffpa-2` 起投影自己携带期间口径与期间表达（事实是时点量还是期间量）。工厂必须
            # 原样带过去：缺了它，下游（表格/正文）就没有办法把两者分开呈现。
            period_basis=getattr(spec, "period_basis", ""),
            period_label=getattr(spec, "period_label", "")))
    artifact = FPA.FinancialPackArtifact(
        schema_version=FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION, artifact_id="",
        task_id=task_id, projection_id=projection_id, contract_version=contract_version,
        contract_fingerprint=contract_fingerprint,
        producer_kind=FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND,
        fact_selection_rule_version=fact_selection_rule_version,
        projection_version=FPA.FINANCIAL_PACK_PROJECTION_VERSION,
        snapshot=snapshot_identity, periods=tuple(periods),
        statements_available=tuple(statements_available), period_note={},
        facts=tuple(projections),
        selected_fact_ids=tuple(p.fact_id for p in projections), excluded=tuple(excluded),
        gaps=tuple(gaps), diagnostic_gaps=tuple(diagnostic_gaps),
        projection_notes=tuple(projection_notes))
    fingerprint = artifact.compute_content_fingerprint()
    artifact = dataclasses.replace(
        artifact, content_fingerprint=fingerprint,
        artifact_id=FPA._ARTIFACT_ID_PREFIX + fingerprint[:24])
    artifact.verify()
    return artifact


def _NoteFact(*, fact_id: str, label: str = "", display: str = "", evidence_id: str = EV_ID,
              evidence_char_range: tuple[int, int] = (0, 10), span_id: str = "sp-1",
              value_text: str | None = "12.34", note: str = "") -> FPA.EvidenceNoteFact:
    """真实 `EvidenceNoteFact` 的合成工厂。"""
    return FPA.EvidenceNoteFact(
        fact_id=fact_id, label=label, value_text=value_text, display=display,
        evidence_id=evidence_id, evidence_char_range=evidence_char_range, span_id=span_id,
        note=note)


def _NoteSet(*, task_id: str, facts: tuple, company_id: str = COMPANY_ID,
             report_as_of: str = REPORT_AS_OF, contract_version: str = CONTRACT_VERSION,
             contract_fingerprint: str = CONTRACT_FINGERPRINT, searched_scope: tuple = (),
             searched_need_ids: tuple = (), validation_rule_version: str = "env-1"
             ) -> FPA.ValidatedEvidenceNoteFactSet:
    """真实 `ValidatedEvidenceNoteFactSet` 的合成工厂（空集合由真实类型直接拒绝）。"""
    return FPA.ValidatedEvidenceNoteFactSet(
        schema_version=FPA.EVIDENCE_NOTE_SCHEMA_VERSION, task_id=task_id,
        company_id=company_id, report_as_of=report_as_of, contract_version=contract_version,
        contract_fingerprint=contract_fingerprint, facts=tuple(facts),
        searched_scope=tuple(searched_scope), searched_need_ids=tuple(searched_need_ids),
        validation_rule_version=validation_rule_version)


def _NoteGap(*, task_id: str, reason_codes: tuple[str, ...] = ("note_extraction_not_implemented",),
             company_id: str = COMPANY_ID, report_as_of: str = REPORT_AS_OF,
             contract_version: str = CONTRACT_VERSION,
             contract_fingerprint: str = CONTRACT_FINGERPRINT, searched_scope: tuple = (),
             searched_need_ids: tuple = (), detail: str = "本轮未产出可用附注事实。"
             ) -> FPA.EvidenceNoteGap:
    """真实 `EvidenceNoteGap` 的合成工厂。"""
    return FPA.EvidenceNoteGap(
        schema_version=FPA.EVIDENCE_NOTE_SCHEMA_VERSION, task_id=task_id,
        company_id=company_id, report_as_of=report_as_of, contract_version=contract_version,
        contract_fingerprint=contract_fingerprint, searched_scope=tuple(searched_scope),
        searched_need_ids=tuple(searched_need_ids), reason_codes=tuple(reason_codes),
        detail=detail)


# ---------------------------------------------------------------------------
# 便捷构造
# ---------------------------------------------------------------------------

def _citation(page: int | None = 12) -> TS.CitationRef:
    """真实事实引用锚点（与 `TS.citation_source_identity` 同一身份域）。"""
    return TS.CitationRef(ref_type="evidence", evidence_id=EV_ID, page_number=page)


def _fact(fact_id: str, text: str, aspect_ids: tuple[str, ...],
          *, page: int | None = 12, period: str = REPORT_AS_OF,
          scope: str = "", material_ids: tuple[str, ...] | None = None) -> _Fact:
    # §十二 1/3：事实期间只能来自 fact 自身。没有显式期间的 fact 不进入正文（只留
    # `period_unresolved` 缺口），所以这里的权威替身默认带一个事实自己的期间。
    # §十一：fact.scope 与 fact.period 一起构成表格展示字段（主体/期间）的**唯一**合法来源。
    return _Fact(fact_id=fact_id, text=text, aspect_ids=aspect_ids,
                 citation_refs=(_citation(page),), period=period, scope=scope,
                 material_ids=material_ids)


def _task(section_id: str, topic_ids: tuple[str, ...], *,
          task_id: str = "task-demo-1", questions: tuple[PS.PlannedQuestion, ...] = (),
          title: str = "示例章节") -> PS.SectionTask:
    if not questions:
        questions = tuple(
            PS.PlannedQuestion(question_id=f"q-{tid}", question=f"{tid} 的问题？",
                               priority="required", topic_id=tid,
                               required_aspects=(), impact_scope=("subject",))
            for tid in topic_ids)
    return PS.SectionTask(
        task_id=task_id, plan_id="plan-demo-1", section_id=section_id, title=title,
        purpose="代表作纵向切片。", research_policy="topic_research", topic_ids=topic_ids,
        questions=questions, output_requirements=(), evaluation_rule_ids=(),
        allowed_capabilities=("local",), blocking_rules=())


def _aspect(aspect_id: str, topic_id: str, question_id: str, *, status: str = "covered",
            blocking: tuple[str, ...] = (), impact: tuple[str, ...] = ("subject",),
            kind: str = "fact_set", time_scope: str = DEFAULT_TIME_SCOPE,
            complete_set_rule: str = "", content_role: str = "paragraph",
            display_tier: str = "required_body", requirement_text: str = "") -> _AspectReq:
    """aspect requirement 规格（`status` 只作参数兼容：requirement 本身不带状态）。

    状态由 `_AspectResult` 单独声明（权威侧「要求」与「结果」是两个对象，不得混一张表）。

    `requirement_text`（§三 A / 3.1）：Contract **逐字**的中文业务要求。缺省留空时
    `_aspect_snapshot` 会补一个「<aspect_id> 的要求文本」占位；需要验证「要求文本真的进了
    请求面」的用例必须显式给一段可辨认的中文（占位串本身也含 aspect_id，不足以证明来源）。
    """
    del status
    return _AspectReq(aspect_id=aspect_id, topic_id=topic_id, question_id=question_id,
                      impact_scope=impact, blocking_policy=blocking, kind=kind,
                      time_scope=time_scope, complete_set_rule=complete_set_rule,
                      content_role=content_role, display_tier=display_tier,
                      requirement_text=requirement_text)


def _question_id(topic_id: str) -> str:
    """`_task()` 给每个 topic 生成的问题 id（与 `_aspect()` 里手写的 id 一致）。"""
    return f"q-{topic_id}"


def _material_of_identity(material_id: str, source_identity: str, *,
                          page: int | None = 12,
                          text: str | None = None) -> TS.ResearchMaterial:
    """按给定来源身份构造真实 `ResearchMaterial`（**不**做形态检查：反例专用）。

    真实类型自己会核对「三方身份一致」（`source_identity` == `payload_ref.authority_identity`
    == `authority_source_identity(authority)`），因此裸 id 之类的不合形态在构造期即被拒。

    §三 A：`payload_ref.content_hash` 不再是合成哈希，而是**真实 payload 信封字节**的 sha256
    （见 `_payload_envelope_bytes`），并登记进 `_PAYLOAD_BYTES` 供 `_payload_resolver()` 解析。
    这样夹具里的「材料正文」是真的能被解析、被重算、被送进 Writer 与语义门的一串字节。
    """
    raw_id = str(source_identity).partition(":")[2] or str(source_identity)
    locator = TS.EvidenceLocator(document_id=DOCUMENT_ID, document_version=DOCUMENT_VERSION,
                                 section_path="s1", page=page)
    authority = _evidence_authority(raw_id, page=page)
    # 两级内容身份（与真实链同形，不得混用）：
    #   * **来源层** `source_content_hash` = 父 Evidence 块的 `content_hash`，落在权威评估上
    #     （`_evidence_authority(content_hash=...)`），并写进信封；
    #   * **载体层** `payload_ref.content_hash` = payload **信封字节**的 sha256（= payload_id
    #     = payload_hash），`ResearchMaterial.content_hash` 必须与它一致。
    if text is not None:
        # 正文是 material 的**内容**，因此它必须跟着 `(material_id, source_identity)` 走：
        # 真实链上同一份 material 被重新物化（如 Pack 侧重建）拿到的仍是同一串字节。只放在
        # 构造参数里会让「重新物化 → 正文悄悄退回默认值」变成一个夹具特有的假象。
        _MATERIAL_TEXTS[(str(material_id), str(source_identity))] = str(text)
    source_content_hash = str(authority.content_hash)
    payload_bytes = _payload_envelope_bytes(
        material_id=material_id, source_identity=source_identity, page=page,
        text=text if text is not None else _default_material_text(material_id, source_identity),
        source_content_hash=source_content_hash)
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    _PAYLOAD_BYTES[payload_hash] = payload_bytes
    payload_ref = TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity=source_identity, version="v1",
        content_hash=payload_hash, locator=locator,
        created_dependency_fingerprint=DEPENDENCY_FINGERPRINT)
    return TS.ResearchMaterial(
        material_id=material_id, material_type="evidence_span",
        source_identity=source_identity, locator=locator, payload_ref=payload_ref,
        content_hash=payload_hash, authority_assessment=authority)


def _default_material_text(material_id: str, source_identity: str = "") -> str:
    """夹具材料的正文：显式登记过的优先（`_MATERIAL_TEXTS`），否则用无高风险表面的默认描述。

    路径 B 的授权基础是「正文里逐字可读出的非高风险描述」，因此默认正文必须是纯业务描述，
    否则夹具自身的材料就会把合法的路径 B 候选挡在门外，测出来的就不是被测规则。
    """
    registered = _MATERIAL_TEXTS.get((str(material_id), str(source_identity)))
    if registered is not None:
        return registered
    return f"{material_id} 覆盖的公开材料描述了该公司主营业务的构成与经营模式，属于必要背景。"


def _payload_envelope_bytes(*, material_id: str, source_identity: str, page: int | None,
                            text: str, structured: Any = None,
                            source_content_hash: str = "") -> bytes:
    """material payload 信封字节（真实链的同一形状，见 `wmctx-1` 读取侧）。"""
    envelope = {
        "material_payload_version": 1,
        "object_type": "evidence_span",
        "authority_identity": source_identity,
        "evidence_id": str(source_identity).partition(":")[2] or str(source_identity),
        "material_id": material_id,
        "source_content_hash": source_content_hash,
        "created_dependency_fingerprint": DEPENDENCY_FINGERPRINT,
        "locator": TS.EvidenceLocator(
            document_id=DOCUMENT_ID, document_version=DOCUMENT_VERSION,
            section_path="s1", page=page).to_dict(),
        "document_identity": {
            "company_id": COMPANY_ID, "document_id": DOCUMENT_ID,
            "document_version": DOCUMENT_VERSION,
            "evidence_set_version": EVIDENCE_SET_VERSION,
        },
        "content": {"text": text, "structured_payload": structured},
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")


class _StubPayloadResolver:
    """夹具 `PayloadResolver`：按 `content_hash` 返回已登记的 payload 字节。

    它**不是**替身语义：`TS.verify_material_payload_ref` 会独立复核 object_type /
    authority_identity / version / locator / content_hash 与字节哈希；本类只提供「去哪里取字节」
    这一个能力（真实链上是 Evidence store 的 sqlite BLOB）。
    """

    def __init__(self, payloads: dict[str, bytes] | None = None) -> None:
        self._payloads = dict(_PAYLOAD_BYTES if payloads is None else payloads)

    def resolve(self, payload_ref: TS.MaterialPayloadRef) -> TS.ResolvedPayload | None:
        raw = self._payloads.get(payload_ref.content_hash)
        if raw is None:
            return None
        return TS.ResolvedPayload(
            object_type=payload_ref.object_type,
            authority_identity=payload_ref.authority_identity, version=payload_ref.version,
            locator=payload_ref.locator, content_hash=payload_ref.content_hash,
            payload_bytes=raw)


def _payload_resolver() -> _StubPayloadResolver:
    return _StubPayloadResolver()


def _writer_material_context(pack_set: Any, *, task_id: str = "", section_id: str = ""):
    """夹具侧的正文上下文（与生产组合根同一入口，不做任何裁剪或降级）。"""
    return MC.resolve_writer_material_context(
        pack_set=pack_set, resolver=_payload_resolver(), task_id=task_id, section_id=section_id)


def _research_material(material_id: str, source_identity: str, *,
                       page: int | None = 12) -> TS.ResearchMaterial:
    """真实 `ResearchMaterial`：身份域即 `TS.citation_source_identity`（`evidence:<id>`）。

    §四：material 的 `source_identity` 与 `CitationRef` 必须落在**同一个身份域**，因此这里
    只接受类型化身份串（`evidence:<裸 id>`），不接受裸 id——裸 id 永远比不出结果。
    """
    kind, _, raw_id = str(source_identity).partition(":")
    if kind != "evidence" or not raw_id:
        raise AssertionError(
            f"测试 material 的来源身份必须是 `evidence:<id>` 形态，得到 {source_identity!r}")
    return _material_of_identity(material_id, source_identity, page=page)


def _chain_ids(inputs: tuple[TS.ResearchMaterial, ...], spec: _Fact
               ) -> tuple[TS.FactCandidate, TS.FactQualificationDecision]:
    """派生 (candidate, eligible 决定)：身份口径与 runtime 的资格门逐字段同形。

    M930-3 资格链是单向的：`FactCandidate → FactQualificationDecision → SupportedFact`。
    `inputs` 是这条链**声明的输入材料**（`mbind-1` 读的就是它）：单元素时逐字等于旧实现的
    三个 digest；多元素时按 `_check_decision_inputs` 同一口径复算。
    """
    material = inputs[0]
    candidate = TS.build_fact_candidate(
        candidate_source_kind="topic_material", statement=spec.text, fact_type="fact",
        aspect_ids=tuple(spec.aspect_ids), question_ids=(),
        material_ids=tuple(m.material_id for m in inputs),
        period=spec.period or None, scope=spec.scope or None)
    source_identity = TS.authority_source_identity(material.authority_assessment)
    locator_digest = TS.sha256_canonical(
        {"locators": [m.locator.to_dict() for m in inputs]})
    payload_digest = TS.sha256_canonical(
        {"payloads": [m.payload_ref.to_dict() for m in inputs]})
    identity_digest = TS.sha256_canonical({
        "candidate_id": candidate.candidate_id,
        "candidate_revision": candidate.candidate_revision,
        "material_ids": [m.material_id for m in inputs],
        "material_content_hashes": sorted({m.content_hash for m in inputs}),
        "source_identity": source_identity,
        "locator_digest": locator_digest,
        "payload_digest": payload_digest,
    })
    decision = TS.build_qualification_decision(
        candidate, verdict="eligible", input_identity_digest=identity_digest,
        input_source_identity=source_identity, input_locator_digest=locator_digest,
        input_payload_digest=payload_digest)
    return candidate, decision


def _supported_fact(spec: _Fact, inputs: tuple[TS.ResearchMaterial, ...]) -> TS.SupportedFact:
    """`_Fact` 规格 → 真实 `SupportedFact`（经 M930-3 资格链；引用锚点即真实 `TS.CitationRef`）。

    `inputs` 是该 fact 的**来源 material**（引用身份已对齐）；候选引用它，故
    「candidate → eligible 决定 → fact」的单向回指在真实类型层成立。
    """
    material = inputs[0]
    refs = tuple(spec.citation_refs)
    value_identity = None
    if spec.value_identity is not None:
        value_identity = TS.ValueIdentity(
            value_kind=spec.value_identity.value_kind or "amount",
            metric=spec.value_identity.metric or spec.fact_id,
            unit=spec.value_identity.unit, period=spec.value_identity.period or spec.period,
            scope=spec.value_identity.scope, amount_canonical=spec.value_identity.amount_canonical)
    evidence_id = str(getattr(refs[0], "evidence_id", "") or "") if refs else ""
    cand, dec = _chain_ids(inputs, spec)
    fact = TS.build_supported_fact(
        spec.fact_id, cand, dec, text=spec.text, fact_type="fact",
        citation_refs=refs, source_authority=_evidence_authority(evidence_id))
    return dataclasses.replace(fact, value_identity=value_identity,
                               period=spec.period or None, scope=spec.scope or None)


def _fact_material(materials: dict[str, TS.ResearchMaterial],
                   spec: _Fact) -> TS.ResearchMaterial:
    """该 fact 的来源 material（资格链的输入侧）：按第一个引用锚点的来源身份命中。

    反例夹具（`auto_materials=False`）下事实引用的来源身份可能在本容器内**没有** material：
    此时资格链仍须成立，故用一份**不入 Pack** 的合成来源 material 建链——Pack 的四道门只看
    candidates/decisions/results 的 bijection，不据此声称 material 存在。

    注意（M930-3B 复核更正）：这**不是**「引用无 material 由 Writer 硬门 fail-closed」。topic_pack
    的预验证事实由 `path_a_prevalidated` 支撑，本来就不需要 exact material，权威侧也不得凭空
    编造一份；真正的守卫在边上——事实性候选不得走 path B，context 边必须绑真实 material
    （「context 不得凭空」）。见 §16 的对应断言。
    """
    refs = tuple(spec.citation_refs)
    if refs:
        identity = TS.citation_source_identity(refs[0])
        hits = sorted((m for m in materials.values() if m.source_identity == identity),
                      key=lambda m: m.material_id)
        if hits:
            return hits[0]
        return _research_material(f"m-src-{spec.fact_id}", identity)
    if not materials:
        raise AssertionError(f"fact {spec.fact_id!r} 无引用锚点也无 material（夹具无法建链）")
    return materials[sorted(materials)[0]]


def _fact_chain_materials(materials: dict[str, TS.ResearchMaterial],
                          spec: _Fact) -> tuple[TS.ResearchMaterial, ...]:
    """该 fact 资格链**声明的输入材料**（`mbind-1` 的收窄集来源）。

    显式给了 `spec.material_ids` 就按 id 逐份解析（**声明顺序**，与 `_check_decision_inputs`
    的重算口径同序）；缺省 = 它自己的来源 material 单元素元组（旧夹具逐字不变）。
    """
    if spec.material_ids:
        missing = [mid for mid in spec.material_ids if mid not in materials]
        if missing:
            raise AssertionError(
                f"fact {spec.fact_id!r} 声明的输入 material {missing} 不在夹具材料表内")
        return tuple(materials[mid] for mid in spec.material_ids)
    return (_fact_material(materials, spec),)


def _successors(materials: dict[str, TS.ResearchMaterial],
                facts: tuple[TS.SupportedFact, ...],
                results: tuple[TS.AspectResearchResult, ...],
                specs: tuple[_Fact, ...],
                fact_materials: dict[str, tuple[TS.ResearchMaterial, ...]],
                external_facts: tuple = (),
                external_chains: tuple = ()) -> dict:
    """由 Pack 的 materials/facts/results 确定性重算 successor 六族（六族一律显式传入）。"""
    aspect_of: dict[str, list[str]] = {}
    for r in results:
        for mid in r.material_ids:
            aspect_of.setdefault(mid, [])
            if r.aspect_id not in aspect_of[mid]:
                aspect_of[mid].append(r.aspect_id)
    dispositions = tuple(
        TS.build_material_disposition(
            materials[aid], aspect_ids=tuple(aspect_of.get(aid, ("a1",))),
            admission_state="admitted", retention_state="retained",
            source_validation="validated", reason_code="aspect_material_admitted",
            reason_proof="pack writer fixture",
            policy_version=TS.MATERIAL_DISPOSITION_VERSION)
        for aid in sorted(materials))
    cands, decs = [], []
    for spec in specs:
        cand, dec = _chain_ids(fact_materials[spec.fact_id], spec)
        cands.append(cand)
        decs.append(dec)
    for ext_cand, ext_dec in external_chains:
        cands.append(ext_cand)
        decs.append(ext_dec)
    return {
        "material_dispositions": dispositions, "fact_candidates": tuple(cands),
        "fact_qualification_decisions": tuple(decs),
        "external_facts": tuple(external_facts),
        "contract_gaps": (), "research_blocks": ()}



def _materialize_pack(spec: _Pack, *, task_id: str, section_id: str, req_spec: _Req,
                      material_factory: Any = None
                      ) -> tuple[TS.TopicResearchRequirement, TS.TopicResearchPack]:
    """`_Pack`/`_Req` 规格 → (真实 `TopicResearchRequirement`, 真实 `TopicResearchPack`)。

    真实 `TopicResearchPack` 的 aspect 结果必须**恰好**覆盖 requirement 声明的每条 aspect
    （多一条、少一条、串 topic 都直接拒绝），因此这里先把两侧声明的 aspect 并集物化成
    requirement，再逐条派生结果（未声明状态的一律取 `covered`）。

    `material_factory`（可选，缺省 `None` = 逐字沿用 `_research_material`）只换**材料怎么造**
    这一件事：签名 `(material_id, source_identity, page) -> TS.ResearchMaterial`。离线重放用它
    注入真实运行记录下来的材料身份（真实 locator / 依赖指纹 / 来源层哈希），而 Pack 的其余
    结构（requirement / aspect 结果 / 六族 successor / 内容寻址 id）仍走同一条实现——两条路
    各建一份 Pack 物化就会让「重放的是同一条权威形状」不再成立。
    """
    if material_factory is None:
        material_factory = (lambda material_id, source_identity, page=None:
                            _research_material(material_id, source_identity, page=page))
    topic_id = str(spec.topic_id)
    declared: dict[str, _AspectReq] = {}
    for aspect in req_spec.aspects:
        declared[str(aspect.aspect_id)] = aspect
    status_by_id: dict[str, str] = {}
    for result in spec.aspect_results:
        aspect_id = str(result.aspect_id)
        declared.setdefault(aspect_id, _AspectReq(
            aspect_id=aspect_id, topic_id=topic_id, question_id=_question_id(topic_id)))
        status_by_id[aspect_id] = str(result.status)
    snapshots = tuple(_aspect_snapshot(declared[aid]) for aid in sorted(declared))
    requirement = TS.TopicResearchRequirement(
        task_id=task_id, company_id=spec.company_id, report_as_of=spec.report_as_of,
        contract_version=spec.contract_version,
        contract_fingerprint=spec.contract_fingerprint,
        source_policy_version=SOURCE_POLICY_VERSION, section_id=section_id,
        topic_id=topic_id, question_ids=tuple(dict.fromkeys(s.question_id for s in snapshots)),
        aspects=snapshots, allowed_capabilities=("local",),
        dependency_versions=dict(DEPENDENCY_VERSIONS))

    # material：显式声明的优先；其余按事实引用**自动补**（真实 Pack 必然包含被引用材料的
    # 那一份——否则 §四 的 material 绑定就无从成立，写作器会 fail-closed）。
    materials: dict[str, TS.ResearchMaterial] = {
        str(m.material_id): material_factory(str(m.material_id), str(m.source_identity),
                                             getattr(m, "page", 12))
        for m in spec.materials}
    covered_identities = {m.source_identity for m in materials.values()}
    # M930-3：fact 必须经「candidate → eligible 决定 → fact」资格链，候选回指来源 material，
    # 故材料（含自动补的）必须**先于**事实全部解析完毕。
    for fact_spec in sorted(spec.facts, key=lambda f: f.fact_id):
        if not spec.auto_materials:
            break
        for ref in fact_spec.citation_refs:
            identity = TS.citation_source_identity(ref)
            if identity in covered_identities:
                continue
            material_id = f"m-auto-{len(materials) + 1}"
            materials[material_id] = material_factory(
                material_id, identity, getattr(ref, "page_number", 12))
            covered_identities.add(identity)

    fact_materials: dict[str, tuple[TS.ResearchMaterial, ...]] = {}
    facts = []
    for fact_spec in spec.facts:
        chain_materials = _fact_chain_materials(materials, fact_spec)
        fact_materials[fact_spec.fact_id] = chain_materials
        facts.append(_supported_fact(fact_spec, chain_materials))
    facts = tuple(facts)

    fact_ids_by_aspect: dict[str, list[str]] = {}
    for fact in facts:
        for aspect_id in fact.aspect_ids:
            fact_ids_by_aspect.setdefault(aspect_id, []).append(fact.fact_id)
    results = tuple(
        TS.AspectResearchResult(
            aspect_id=snapshot.aspect_id,
            question_ids=(snapshot.question_id,) if snapshot.question_id else (),
            requirement_snapshot=snapshot, status=status_by_id.get(snapshot.aspect_id, "covered"),
            supported_fact_ids=tuple(fact_ids_by_aspect.get(snapshot.aspect_id, ())),
            material_ids=tuple(sorted(materials)), attempted_need_ids=(), unresolved_ids=())
        for snapshot in snapshots)
    required_aspect_ids = tuple(s.aspect_id for s in snapshots)
    stop_reason = "SECTION_BLOCKED" if any(
        r.status == "blocked" for r in results) else None
    process, coverage, derivation = TS.derive_pack_status(
        required_aspect_ids, results, stop_reason=stop_reason)
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id=spec.run_id,
        task_id=task_id, company_id=spec.company_id, report_as_of=spec.report_as_of,
        contract_version=spec.contract_version,
        contract_fingerprint=spec.contract_fingerprint,
        source_policy_version=SOURCE_POLICY_VERSION, section_id=section_id, topic_id=topic_id,
        question_ids=requirement.question_ids, aspect_results=results,
        materials=tuple(materials[aid] for aid in sorted(materials)), facts=facts,
        outcome_refs=(), external_funnel=None,
        conflicts=tuple(spec.conflicts), not_found_audits=tuple(spec.not_found_audits),
        unresolved=tuple(spec.gaps), usage=TS.TopicUsageSnapshot(
            budget_policy=TS.BudgetPolicySnapshot(
                schema_version=TS.TOPIC_PACK_SCHEMA_VERSION,
                canonical_hash=_sha("budget:" + topic_id), tier="demo"),
            cumulative_usage=(), stop_reason=None),
        uncertain_calls=(), process_status=process, coverage_status=coverage,
        status_derivation=derivation, dependency_fingerprint=DEPENDENCY_FINGERPRINT,
        source_set=(spec.source_set or TS.DocumentSourceSet.single_document(
            company_id=spec.company_id, document_id=DOCUMENT_ID,
            document_version=DOCUMENT_VERSION, evidence_set_version=EVIDENCE_SET_VERSION)),
        **_successors(materials, facts, results, tuple(spec.facts), fact_materials,
                      external_facts=tuple(spec.external_facts),
                      external_chains=tuple(spec.external_chains)))
    return requirement, TS.finalize_pack(pack)


def _pack_set(task: PS.SectionTask, *, facts: tuple = (), materials: tuple = (),
              aspect_results: tuple = (), requirements: tuple = (),
              topic_ids: tuple[str, ...] | None = None,
              packs: tuple | None = None, section_id: str | None = None,
              task_id: str | None = None, auto_materials: bool = True,
              material_factory: Any = None
              ) -> PSet.VerifiedPackSet:
    """规格 → **真实** `VerifiedPackSet`（§三 4：字段形状替身一律不得进入权威输入）。"""
    resolved_topic_ids = topic_ids if topic_ids is not None else task.topic_ids
    if packs is None:
        packs = tuple(
            _Pack(topic_id=tid, facts=facts, materials=materials, aspect_results=aspect_results,
                  auto_materials=auto_materials)
            for tid in resolved_topic_ids)
    req_by_topic = {str(r.topic_id): r for r in requirements}
    resolved_task_id = task_id or task.task_id
    resolved_section_id = section_id or task.section_id
    built = [_materialize_pack(
        spec, task_id=resolved_task_id, section_id=resolved_section_id,
        req_spec=req_by_topic.get(str(spec.topic_id), _Req(str(spec.topic_id), ())),
        material_factory=material_factory)
        for spec in packs]
    return PSet.VerifiedPackSet(
        task_id=resolved_task_id, section_id=resolved_section_id,
        topic_ids=tuple(str(t) for t in resolved_topic_ids),
        packs=tuple(pack for _, pack in built),
        requirements=tuple(req for req, _ in built),
        binding=PSet.PackSetBinding(
            task_id=resolved_task_id, section_id=resolved_section_id, company_id=COMPANY_ID,
            report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT,
            source_policy_version=SOURCE_POLICY_VERSION,
            dependency_fingerprint=DEPENDENCY_FINGERPRINT,
            dependency_versions=dict(DEPENDENCY_VERSIONS)))


class _StubLlm:
    """唯一的注入能力：按脚本返回文本。没有任何工具/检索入口。

    §五 1/6：stub 与真实 client 返回**同一**结构化结果 `PW.NarrationResult`——记录版本、实际
    模型与完整调用元数据都必须留在结果里，否则「测试里过的路径」与「真实跑的路径」不同。
    """

    def __init__(self, *responses, model: str | None = None, prompt_version: str = "") -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        #: 模拟「实际调用模型 / prompt 版本与记录不一致」的注入点（§五 3/4 反例用）。
        self.model = model or PW.MODEL_POLICY_STUB
        self.prompt_version = prompt_version

    def narrate(self, *, messages, system, prompt_version, model_policy) -> PW.NarrationResult:
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy,
                           "messages": messages, "system": system})
        if not self.responses:
            raise AssertionError("stub LLM 被超额调用（应当 fail-closed 而不是继续重试）")
        item = self.responses.pop(0)
        index = len(self.calls)
        if isinstance(item, Exception):
            return PW.NarrationResult(
                text="", call_id=f"err-{index}", model=self.model,
                prompt_version=self.prompt_version or prompt_version, status="error",
                error=f"{type(item).__name__}: {item}")
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return PW.NarrationResult(
            text=text, call_id=f"call-{index}", model=self.model,
            prompt_version=self.prompt_version or prompt_version, status="ok",
            input_tokens=128, output_tokens=64, latency_ms=1, finish_reason="stop")


class _FakePackSet:
    """`VerifiedPackSet` 的**形状替身**（字段名相同、类型不是真实类型）。§三 反例专用。"""

    def __init__(self, *, task_id: str, section_id: str, topic_ids: tuple,
                 packs: tuple, requirements: tuple) -> None:
        self.task_id = task_id
        self.section_id = section_id
        self.topic_ids = topic_ids
        self.packs = packs
        self.requirements = requirements
        self.binding = None


class _FakePack:
    """`TopicResearchPack` 的形状替身（同样只给字段名）。"""

    pack_id = "pack-forged"
    topic_id = TOPIC_BUSINESS
    facts: tuple = ()
    materials: tuple = ()
    aspect_results: tuple = ()
    conflicts: tuple = ()
    not_found_audits: tuple = ()
    gaps: tuple = ()

    def verify_pack_id(self) -> None:
        return None

    def content_fingerprint(self) -> str:
        return _sha("fake-pack")


class _FakeArtifact:
    """`FinancialPackArtifact` 的形状替身（身份字段一个不缺，但类型不是真实类型）。"""

    def __init__(self, task_id: str = "task-fin-solvency") -> None:
        self.task_id = task_id
        self.artifact_id = "ffpa_forged"
        self.schema_version = FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION
        self.producer_kind = FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND
        self.content_fingerprint = _sha("fake-artifact")
        self.facts: tuple = ()

    def verify(self) -> None:
        return None


def _company_authority(task: PS.SectionTask, *, facts, materials=(), aspects=(),
                       requirements=()):
    return PW.TopicPackAuthorityInput.create(
        task, _pack_set(task, facts=facts, materials=materials, aspect_results=aspects,
                        requirements=requirements),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
        contract_fingerprint=CONTRACT_FINGERPRINT)


def _financial_authority(task: PS.SectionTask, artifact, **kw):
    return PW.FinancialAuthorityInput.create(
        task, artifact, company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT, **kw)


# ---------------------------------------------------------------------------
# narr-4（M930-3B）门前提案束的构造助手
#
# 模型能表达的**只有**：候选（key + 文本 + factual 支撑边）、草稿单元（key + 类型 + 文本 +
# context 支撑边）、补件申请。支撑边的坐标一律**逐字取自权威读视图**（模型只能「选择」权威
# 目录里的行，不能自报身份）——因此下面的助手不手写身份，而是从 `scan` 回查。
# ---------------------------------------------------------------------------

def _fact_edge(scan, fact_id: str, *, role: str = "primary", **overrides) -> dict:
    """路径 A 支撑边：坐标逐字取自权威事实目录（`material_id` 必须缺省）。"""
    for entry in scan.facts:
        if entry.fact_id == fact_id:
            edge = {"authority_kind": entry.authority_kind,
                    "container_id": entry.container_identity, "fact_id": entry.fact_id,
                    "material_id": None, "support_role": role,
                    "support_semantics": "factual",
                    "authorization_path": "path_a_prevalidated"}
            edge.update(overrides)
            return edge
    raise AssertionError(f"权威事实目录里没有 fact {fact_id!r}")


def _material_edge(container_id: str, material_id: str, *, role: str = "primary") -> dict:
    """路径 B 支撑边：只给 material 身份，**不得**携带任何 fact 身份。"""
    return {"authority_kind": "topic_pack", "container_id": container_id, "fact_id": None,
            "material_id": material_id, "support_role": role,
            "support_semantics": "factual", "authorization_path": "path_b_material_derived"}


def _context_edge(container_id: str, material_id: str) -> dict:
    """context 支撑边：只挂草稿单元，只验证背景/结构/衔接，不授权事实。"""
    return {"authority_kind": "topic_pack", "container_id": container_id,
            "material_id": material_id, "support_role": "corroborating",
            "support_semantics": "context", "authorization_path": "context_only"}


def _cand(key: str, text: str, *edges, support=None) -> dict:
    return {"candidate_key": key, "claim_text": text,
            "support": [dict(e) for e in (support if support is not None else edges)]}


def _unit(key: str, text: str, *, kind: str = "paragraph", context=()) -> dict:
    return {"unit_key": key, "unit_kind": kind, "text": text,
            "context_support": [dict(e) for e in context]}


def _plan(*, candidates=(), units=(), follow_ups=(), prose=(),
          drafted: bool = True, draft_ref: "str | Sequence[str]" = "m1",
          draft_axis: str = "material") -> dict:
    """门前提案束（当前线 `PROPOSAL_WIRE_CURRENT` 的**唯一**模型输出形态）。

    输出里**没有** Claim / citation / accepted binding / final Narrative / `SectionResult` /
    任何 disposition / 任何决定字段 —— 这不是「测试没写」，而是词汇表里没有可写的表面。

    `prose`（`pw-15`）是**自然草稿层**：写作顺序是硬的（先起草稿、再为草稿里每个事实原子单独
    提交候选），因此它在键序上排在最前。`pw-16` 起「有候选、无草稿」是 typed failure，因此
    `drafted`（缺省 `True`）在**给了候选而没给 `prose`** 时按候选逐条回填草稿单元：

      * 回填的是**模型会写的形状**，不是夹具替写入侧补的东西——草稿的**文本**与候选文本相同是
        最省事的合法填法（原子闭合判的是键的映射，不是两段文字是否一样），`draft_ref` /
        `draft_axis` 仍要由用例按**本节的材料面**给对（公司/行业节 → 材料轴 `m1..`；材料面
        合法为空的财务节 → 事实轴 `f1..`）。给错轴不会被静默改写成对的那条：写入侧照拒。
        `draft_ref` 也可是**逐候选**的一串（多事实的节按本节别名表的真实顺序给，不猜）。
      * 它**不**掩盖缺陷：「有候选、无草稿」这条反例由 `drafted=False` 显式写出，当前线的
        端到端合法路径由带真实别名表的用例（`prose=` 显式给）钉住。
    """
    refs = ([draft_ref] * len(candidates) if isinstance(draft_ref, str)
            else list(draft_ref))
    if refs and len(refs) != len(candidates):
        raise AssertionError(
            f"_plan 的 draft_ref 逐候选给出时必须与候选一一对应（{len(refs)} vs "
            f"{len(candidates)}）——对不上就是夹具在猜哪条候选出自哪一行")
    prose_units = [dict(p) for p in prose]
    if not prose_units and drafted and candidates:
        for i, cand in enumerate(candidates):
            key = str(cand.get("candidate_key") or f"c{i + 1}")
            prose_units.append(_prose(f"p{i + 1}", str(cand.get("claim_text") or ""),
                                      members=([refs[i]] if draft_axis == "material" else []),
                                      facts=([refs[i]] if draft_axis == "fact" else []),
                                      atoms=[key]))
    return {"natural_prose_draft": prose_units,
            "claim_candidates": [dict(c) for c in candidates],
            "narrative_draft_units": [dict(u) for u in units],
            "follow_up_needs": [dict(f) for f in follow_ups]}


def _prose(key: str, text: str, *, members=(), facts=(), atoms) -> dict:
    """一段自然草稿：正文 + 出处（**短别名**，与候选的支撑边同一张别名表）+ 原子键。

    `members` / `facts` / `atoms` 都写**别名/短键**（`m1` / `f1` / `c1`），身份由写入侧展开——
    夹具不替它展开，那正是「草稿的出处与原子映射不是模型自报」这条判据要证的事。

    出处是**两条互斥的轴**（`pprov-1`）：`members` → 材料行（有材料面的节，公司/行业），
    `facts` → 权威事实行（材料面**合法为空**的节，财务节）。恰有一条非空，两轴同给即拒；
    不是「哪个顺手用哪个」——走错轴的草稿会让读者面回溯不到实际写的那段文字。
    """
    return {"prose_key": key, "text": text,
            "source_member_refs": list(members), "source_fact_refs": list(facts),
            "atom_candidate_keys": list(atoms)}


def _follow_up(statement: str, *, target_requirement_id: str, topic_id: str,
               question_id: str, aspect_id: str, **overrides) -> dict:
    """一条补件申请。`budget_hint` 默认**非空**（`pw-19`）。

    这份夹具此前默认 `budget_hint=""`：那一版下写入侧不查这一格，空值会一路走到 Harness
    获批执行时才抛 `TopicRuntimeError`——于是「一条合法的申请」在这份夹具里长得和「一条注定
    执行不了的申请」一模一样。`pw-19` 把这一格提到入站防线（`budget_hint_empty`），默认值
    随之改成非空；要测那一格的**拒绝**，显式传 `budget_hint=""`。
    """
    spec = {"statement": statement, "target_requirement_id": target_requirement_id,
            "topic_id": topic_id, "question_id": question_id, "aspect_id": aspect_id,
            "requiredness": "required", "expected_source_class": "company_industry",
            "budget_hint": "tree_inspect:1"}
    spec.update(overrides)
    return spec


def _fact_ref(scan, fact_id: str) -> str:
    """`scan.facts` 里某条事实的**短别名**（`f<N>`）：取自本节真实别名表的顺序。

    夹具不自己拼 `f1`——表的顺序就是写入侧展开时用的那条顺序，猜错了展开出来的是别的事实。
    """
    for i, entry in enumerate(scan.facts):
        if str(entry.fact_id) == str(fact_id):
            return f"{PW.FACT_REF_PREFIX}{i + 1}"
    raise AssertionError(f"{fact_id!r} 不在本节权威事实目录里，无从取别名")


def _member_ref(authority, *, task_id: str, section_id: str, material_id: str,
                container_id: str = "") -> str:
    """`material_id` 在本节 manifest 里的**短别名**（`m<N>`），按 manifest 自己的成员顺序取。

    为什么不能像早期那样直接写 `m1`：别名是**按成员位置**编号的，而成员顺序由
    `_derive_material_manifest`（Pack 按 `(topic_id, pack_id)`、Pack 内按 `material_id`）决定；
    一旦本节有自动补料（事实引用派生的 material），第一节点的成员往往**不是**候选实际引用的那份，
    缺省 `m1` 就把草稿的出处指到了另一份材料上。`npr-1` 的逐 occurrence 核对会照拒，夹具不替
    写入侧改写成对的那条。`container_id` 非空时按 `(pack_id, material_id)` 两把键取——同名材料
    在不同 Pack 里本来就是两行。
    """
    manifest = PW._derive_material_manifest(
        authority, material_context=_writer_material_context(
            authority.pack_set, task_id=str(task_id), section_id=str(section_id)))
    for i, entry in enumerate(manifest.entries):
        if str(entry.material_id) == str(material_id) and (
                not container_id or str(entry.pack_id) == str(container_id)):
            return f"{PW.MATERIAL_REF_PREFIX}{i + 1}"
    raise AssertionError(f"material {material_id!r} 不在本节 manifest 里，无从取别名"
                         f"（容器 {container_id!r}）")


def _material_ids_of(scan) -> dict:
    """容器 → material_id → 载荷引用（消歧反例用）。"""
    out: dict = {}
    for entry in scan.facts:
        if entry.material_id:
            out.setdefault(entry.container_identity, {})[entry.material_id] = entry.payload_ref
    return out


# ---------------------------------------------------------------------------
# §16.3 支撑边 / §16.4 材料与事实：formal `ExternalFact` 的测试侧构造
# ---------------------------------------------------------------------------

_EXT_SNAP = "ext1"
_EXT_URL = "https://x.example/1"
_EXT_DOMAIN = "x.example"
_EXT_BODY_HASH = TS.sha256_canonical({"body": _EXT_SNAP})
_EXT_POLICY_VERSION = "source-policy-1"


def _external_fact_spec(statement: str, *, snapshot_id: str = _EXT_SNAP,
                        aspect_ids: tuple[str, ...] = (ASP_BUSINESS_MAIN,),
                        ) -> TS.ExternalFact:
    """formal `ExternalFact`（走生产唯一 factory；snapshot 只作来源载体）。"""
    return _external_spec(statement, snapshot_id=snapshot_id, aspect_ids=aspect_ids)[2]


def _external_spec(statement: str, *, snapshot_id: str = _EXT_SNAP,
                   aspect_ids: tuple[str, ...] = (ASP_BUSINESS_MAIN,),
                   ) -> tuple[TS.FactCandidate, TS.FactQualificationDecision, TS.ExternalFact]:
    """`(候选, 资格决定, ExternalFact)` 三元组：三者必须一同进入 Pack。"""
    cand = TS.build_fact_candidate(
        candidate_source_kind="external_source", statement=statement, fact_type="fact",
        aspect_ids=tuple(aspect_ids), question_ids=(_question_id(TOPIC_BUSINESS),),
        source_snapshot_id=snapshot_id)
    dec = TS.build_qualification_decision(
        cand, verdict="eligible", input_identity_digest=_sha("ext-id"),
        input_source_identity=f"external_snapshot:{snapshot_id}",
        input_locator_digest=_sha("ext-loc"), input_payload_digest=_sha("ext-pay"),
        input_snapshot_id=snapshot_id)
    locator = TS.ExternalLocator(source_snapshot_id=snapshot_id, canonical_url=_EXT_URL,
                                 domain=_EXT_DOMAIN)
    payload_ref = TS.MaterialPayloadRef(
        object_type="external_snapshot", authority_identity=f"external_snapshot:{snapshot_id}",
        version="v1", content_hash=_EXT_BODY_HASH, locator=locator,
        created_dependency_fingerprint=DEPENDENCY_FINGERPRINT)
    authority = TS.ExternalSnapshotAuthorityAssessment(
        source_snapshot_id=snapshot_id, canonical_url=_EXT_URL, domain=_EXT_DOMAIN,
        fetched_nonempty=True, content_hash=_EXT_BODY_HASH, published_at="2025-01-01",
        time_qualified=True, source_grade="B", min_grade_met=True,
        independence_domain="independent.example", verdict="authoritative", reason="",
        validator_version="vv1")
    return cand, dec, TS.build_external_fact(
        cand, dec, statement=statement, canonical_url=_EXT_URL, body_hash=_EXT_BODY_HASH,
        source_policy_version=_EXT_POLICY_VERSION, as_of_date="2025-12-31", locator=locator,
        payload_ref=payload_ref, content_hash=_EXT_BODY_HASH, source_authority=authority)


def main() -> dict:
    passed = 0
    failed = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:160]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    spec = WS.load_writing_spec(SPEC_PATH)
    profile = PP.load_presentation_profile(PROFILE_PATH)
    projections = {sid: PW.ContractProjection.create(
        spec, section_id=sid, contract_version=CONTRACT_VERSION,
        contract_fingerprint=CONTRACT_FINGERPRINT)
        for sid in ("company", "industry", "financial")}

    def _write(task, authority, *, section_id: str, llm, policy=None, projection=None,
               writing_spec=None, dependency_fingerprint: str = DEPENDENCY_FINGERPRINT,
               created_at=None, material_context: Any = None):
        """唯一公开入口的薄包装：补齐本批**必需**的依赖指纹与材料正文上下文，不改任何语义。

        §三 A：topic authority 的写作必须带 `wmctx-1` 正文上下文。缺省由夹具的
        `_writer_material_context` 从**该次调用的 PackSet** 现解析（与生产组合根同一入口）；
        反例可以显式传 `material_context=None` 或不一致的上下文来验证 fail-closed。
        """
        if material_context is None and getattr(authority, "pack_set", None) is not None:
            material_context = _writer_material_context(
                authority.pack_set, task_id=str(task.task_id),
                section_id=str(section_id or task.section_id))
        return PW.write_section(
            task, authority, projection=projection or projections[section_id],
            writing_spec=writing_spec or spec, presentation_profile=profile, llm_client=llm,
            policy=policy, dependency_fingerprint=dependency_fingerprint,
            created_at=created_at, material_context=material_context)

    # ==================================================================
    # 1. 权威输入封闭：不完整 / 非本任务 / 非本节 / topic 集合不一致
    # ==================================================================
    co_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-business")
    base_aspects = (
        _AspectResult(ASP_BUSINESS_MAIN, "covered"),
        _AspectResult(ASP_BUSINESS_SALES, "partial"),
        _AspectResult(ASP_BUSINESS_COST, "blocked"),
    )
    base_facts = (_fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                        (ASP_BUSINESS_MAIN,)),)
    good_set = _pack_set(co_task, facts=base_facts, aspect_results=base_aspects)

    check(PW.TopicPackAuthorityInput.create(
        co_task, good_set, company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION,
        contract_fingerprint=CONTRACT_FINGERPRINT).producer_kind == "topic_harness",
        "合法 exact-topic-set PackSet 可构造")

    # 空 PackSet：真实 `VerifiedPackSet` 在构造期就拒绝（packs 与 topic_ids 不一致），
    # 这种状态根本到不了 Writer；这是上游门 + 本边界双层 fail-closed 的第一层。
    expect_error(
        lambda: _pack_set(co_task, packs=()),
        PSet.PackSetError, "空 PackSet（无任何 Pack）必须在真实门构造期被拒")
    expect_error(
        lambda: PW.TopicPackAuthorityInput.create(
            co_task, _pack_set(co_task, facts=base_facts, task_id="task-other")),
        PW.PackWriterError, "非本任务的 PackSet 必须被拒", needle="不属于本任务")
    expect_error(
        lambda: PW.TopicPackAuthorityInput.create(
            co_task, _pack_set(co_task, facts=base_facts, section_id="industry")),
        PW.PackWriterError, "非本节的 PackSet 必须被拒", needle="不属于本节")
    expect_error(
        lambda: PW.TopicPackAuthorityInput.create(
            co_task, _pack_set(co_task, facts=base_facts,
                               topic_ids=(TOPIC_BUSINESS, TOPIC_IDENTITY))),
        PW.PackWriterError, "topic 集合不一致（多一个主题）必须被拒", needle="完全一致")
    # 少一个主题：two-topic task ↔ 只有一个 Pack 的 PackSet（真实门放行，Writer 必须拒）。
    two_topic_task = _task("company", (TOPIC_BUSINESS, TOPIC_IDENTITY),
                           task_id="task-co-two-topics")
    expect_error(
        lambda: PW.TopicPackAuthorityInput.create(
            two_topic_task, _pack_set(two_topic_task, topic_ids=(TOPIC_BUSINESS,))),
        PW.PackWriterError, "topic 集合不一致（少一个主题）必须被拒", needle="完全一致")

    # Pack 身份不一致：同一个 PackSet 里的两个 Pack 属于不同公司（真实门只查 topic 顺序，
    # 身份一致性必须由本边界拒绝）。
    two_topic_set = _pack_set(two_topic_task, packs=(
        _Pack(topic_id=TOPIC_BUSINESS),
        _Pack(topic_id=TOPIC_IDENTITY, company_id="另一家公司")))
    expect_error(
        lambda: PW.TopicPackAuthorityInput.create(two_topic_task, two_topic_set),
        PW.PackWriterError, "PackSet 内 Pack 身份不一致必须被拒", needle="身份字段不一致")

    # 调用方声明与权威身份冲突：company / report_as_of / contract 三种
    for field, wrong, what in (("company_id", "别家公司", "公司"),
                               ("report_as_of", "2026-07-01", "基准期"),
                               ("contract_version", "v9", "Contract 版本"),
                               ("contract_fingerprint", "b" * 64, "Contract 指纹")):
        expect_error(
            lambda f=field, w=wrong: PW.TopicPackAuthorityInput.create(
                co_task, good_set, **{"company_id": COMPANY_ID, "report_as_of": REPORT_AS_OF,
                                      "contract_version": CONTRACT_VERSION,
                                      "contract_fingerprint": CONTRACT_FINGERPRINT, f: w}),
            PW.PackWriterError, f"调用方声明的{what}与权威身份冲突必须被拒", needle="声明不一致")

    # ==================================================================
    # 2. 生产者隔离（双向）：company/industry ↔ financial 不得混装
    # ==================================================================
    fin_task = _task("financial", (TOPIC_FIN_SOLVENCY,), task_id="task-fin-solvency",
                     title="偿债能力")
    fin_artifact = _Artifact(
        artifact_id="ffpa_demo", task_id=fin_task.task_id,
        facts=(_FinFact(fact_id="ff-short", label="短期偿债能力", display="流动比率为 1.20 倍。",
                        period=REPORT_AS_OF, unit="倍",
                        citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                  "page_number": 71}),))
    fin_authority = _financial_authority(fin_task, fin_artifact, note_gap=_NoteGap(
        task_id=fin_task.task_id))

    expect_error(
        lambda: projections["company"].require_producer_kind(fin_authority.producer_kind),
        PW.PackWriterError, "company 槽位不得接受 financial 权威（跨生产者混装）",
        needle="跨生产者混装已拒")
    expect_error(
        lambda: projections["financial"].require_producer_kind(
            PW.TopicPackAuthorityInput.create(
                co_task, good_set, company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
                contract_version=CONTRACT_VERSION,
                contract_fingerprint=CONTRACT_FINGERPRINT).producer_kind),
        PW.PackWriterError, "financial 槽位不得接受 topic Pack 权威（跨生产者混装）",
        needle="跨生产者混装已拒")

    # 把 topic Pack 权威当财务 artifact 传入：形状/内容都不是财务 artifact —— 拒绝
    expect_error(
        lambda: _financial_authority(
            fin_task, good_set, note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "把 topic Pack 权威当财务 artifact 传入必须被拒", needle="必须是真实的")
    # 真实 `FinancialPackArtifact` 本身在构造期就拒绝别的生产者冒充（跨生产者混装的第一道门）
    expect_error(
        lambda: dataclasses.replace(fin_artifact, producer_kind="topic_harness"),
        FPA.FinancialArtifactError, "真实 artifact 类型必须在构造期拒绝 topic harness 冒充")
    # financial artifact 的 task 绑定不得错
    expect_error(
        lambda: _financial_authority(
            fin_task, _Artifact(artifact_id="ffpa_x", task_id="task-other"),
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "非本任务的 FinancialPackArtifact 必须被拒", needle="不属于本任务")
    # 附注状态必须恰有其一
    note_one = _NoteSet(task_id=fin_task.task_id,
                        facts=(_NoteFact(fact_id="n-1", label="受限资金",
                                         display="受限资金 12.34 元。"),))
    expect_error(
        lambda: _financial_authority(fin_task, fin_artifact),
        PW.PackWriterError, "附注事实与附注缺口都不给必须被拒", needle="恰有其一")
    expect_error(
        lambda: _financial_authority(
            fin_task, fin_artifact, note_facts=note_one,
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "附注事实与附注缺口同时给出必须被拒", needle="恰有其一")
    expect_error(
        lambda: _financial_authority(
            fin_task, fin_artifact,
            note_facts=_NoteSet(task_id="task-other",
                                facts=(_NoteFact(fact_id="n-1"),))),
        PW.PackWriterError, "附注事实非本任务必须被拒", needle="task_id 与 SectionTask 不一致")

    # 真实 `FinancialPackArtifact` **没有** flat company_id / report_as_of：身份只存在于
    # `snapshot` 里。Writer 必须只读此身份（不得发明基准期），且完整性/一致性照旧 fail-closed。
    real_shape = _Artifact(task_id=fin_task.task_id)
    real_authority = _financial_authority(
        fin_task, real_shape, note_gap=_NoteGap(task_id=fin_task.task_id))
    check(real_authority.company_id == COMPANY_ID
          and real_authority.report_as_of == REPORT_AS_OF,
          "snapshot 形态的真实 artifact 身份必须被正确读出（company_id / report_as_of）")
    expect_error(
        lambda: PW.FinancialAuthorityInput.create(
            fin_task, real_shape, note_gap=_NoteGap(task_id=fin_task.task_id),
            company_id="999999", report_as_of=REPORT_AS_OF,
            contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT),
        PW.PackWriterError, "artifact 自报公司 ≠ 调用方声明必须被拒", needle="声明不一致")
    expect_error(
        lambda: PW.FinancialAuthorityInput.create(
            fin_task,
            _Artifact(task_id=fin_task.task_id,
                      snapshot=_snapshot_identity("999999", REPORT_AS_OF)),
            note_gap=_NoteGap(task_id=fin_task.task_id),
            company_id="999999", report_as_of=REPORT_AS_OF,
            contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT),
        PW.PackWriterError, "artifact 自报公司与附注身份不一致必须被拒", needle="不一致")
    expect_error(
        lambda: _financial_authority(
            fin_task,
            _Artifact(task_id=fin_task.task_id, company_id="", report_as_of="",
                      snapshot=None),
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "artifact 身份缺公司/基准期必须被拒", needle="身份不完整")
    expect_error(
        lambda: _financial_authority(
            fin_task,
            _Artifact(task_id=fin_task.task_id,
                      snapshot=_snapshot_identity(COMPANY_ID, "")),
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "snapshot 缺基准期必须被拒（不得发明 report_as_of）",
        needle="身份不完整")

    # O-11：`report_as_of` 是**本次报告生成日**，财务期末只是 artifact 的期间身份——两者必须
    # 各自存活、谁也不许顶替谁。上面所有用例里两者恰好同值，因此根本区分不出这段代码；这里
    # 只让二者**取不同的值**来验证真实语义。
    period_artifact = _Artifact(
        task_id=fin_task.task_id,
        snapshot=_snapshot_identity(COMPANY_ID, FINANCIAL_PERIOD_END))
    separated = PW.FinancialAuthorityInput.create(
        fin_task, period_artifact,
        note_gap=_NoteGap(task_id=fin_task.task_id, report_as_of=FINANCIAL_PERIOD_END),
        company_id=COMPANY_ID, report_as_of=REPORT_GENERATION_DAY,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    check(separated.report_as_of == REPORT_GENERATION_DAY
          and separated.report_as_of != FINANCIAL_PERIOD_END,
          "报告生成日与财务期末不同时，权威输入的 report_as_of 必须是报告生成日"
          "（不得从 FinancialSnapshot.as_of_date 推导）")
    # 结论日与期末都进身份体：任一项被换掉，输入 id 必然变，identity 不会静默复用。
    same_period_other_day = PW.FinancialAuthorityInput.create(
        fin_task, period_artifact,
        note_gap=_NoteGap(task_id=fin_task.task_id, report_as_of=FINANCIAL_PERIOD_END),
        company_id=COMPANY_ID, report_as_of="2026-09-24",
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    other_period = PW.FinancialAuthorityInput.create(
        fin_task,
        _Artifact(task_id=fin_task.task_id,
                  snapshot=_snapshot_identity(COMPANY_ID, "2025-12-31")),
        note_gap=_NoteGap(task_id=fin_task.task_id, report_as_of="2025-12-31"),
        company_id=COMPANY_ID, report_as_of=REPORT_GENERATION_DAY,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    check(separated.input_id != same_period_other_day.input_id
          and separated.input_id != other_period.input_id,
          "报告生成日与财务期末都必须进入权威输入身份：换任一项都必须得到新的 input_id")
    # 报告生成日不得留空：留空就是让财务期末去顶替结论日。
    expect_error(
        lambda: PW.FinancialAuthorityInput.create(
            fin_task, period_artifact,
            note_gap=_NoteGap(task_id=fin_task.task_id, report_as_of=FINANCIAL_PERIOD_END),
            company_id=COMPANY_ID, report_as_of="",
            contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT),
        PW.PackWriterError, "report_as_of 留空必须被拒（不得用财务期末顶替）",
        needle="不得用财务期末顶替")
    expect_error(
        lambda: PW.FinancialAuthorityInput.create(
            fin_task, period_artifact,
            note_gap=_NoteGap(task_id=fin_task.task_id, report_as_of=FINANCIAL_PERIOD_END),
            company_id=COMPANY_ID, report_as_of=None,
            contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT),
        PW.PackWriterError, "report_as_of 未声明必须被拒（不得默认成财务期末）",
        needle="必须显式声明本轮报告生成日")
    # 附注自身的 `report_as_of` 在财务侧是**快照基准期**：只能与财务期末对齐。用报告生成日
    # 顶替它（或拿别的期间的附注冒充）都必须被拒——同名不同义的两个字段不许互换。
    expect_error(
        lambda: _financial_authority(
            fin_task, period_artifact,
            note_gap=_NoteGap(task_id=fin_task.task_id, report_as_of=REPORT_AS_OF)),
        PW.PackWriterError, "附注基准期 ≠ 财务期末必须被拒",
        needle="附注不得挂到别的期间上")
    expect_error(
        lambda: _financial_authority(
            fin_task, period_artifact,
            note_gap=_NoteGap(task_id=fin_task.task_id,
                              report_as_of=REPORT_GENERATION_DAY)),
        PW.PackWriterError, "拿报告生成日充当附注基准期必须被拒（同名不同义）",
        needle="附注不得挂到别的期间上")

    # 多 topic 的财务任务：没有 fact_topic_map 就必须 fail-closed（事实没有 topic 字段）
    fin_multi = _task("financial", (TOPIC_FIN_SCOPE, TOPIC_FIN_SOLVENCY),
                      task_id="task-fin-multi", title="财务信息")
    expect_error(
        lambda: _financial_authority(fin_multi, _Artifact(
            artifact_id="ffpa_m", task_id=fin_multi.task_id), note_gap=_NoteGap(
                task_id=fin_multi.task_id)),
        PW.PackWriterError, "多 topic 财务任务缺 fact_topic_map 必须被拒",
        needle="必须显式给出 fact_topic_map")
    expect_error(
        lambda: _financial_authority(fin_multi, _Artifact(
            artifact_id="ffpa_m", task_id=fin_multi.task_id),
            note_gap=_NoteGap(task_id=fin_multi.task_id),
            fact_topic_map=(("ff-1", "some_other_topic"),)),
        PW.PackWriterError, "fact_topic_map 把 fact 归到本节主题之外必须被拒",
        needle="本节声明之外的主题")

    # 真实 artifact 会带**本节之外**的事实（其他财务章节的指标、或仅作为指标输入的原始报表项）。
    # 归属只能由组合根显式声明为「本节之外」，并且这些事实只能得到「本节不呈现」的去向记录。
    outside_task = _task("financial", (TOPIC_FIN_SOLVENCY,), task_id="task-fin-outside")
    outside_artifact = _Artifact(
        artifact_id="ffpa_outside", task_id=outside_task.task_id,
        facts=(_FinFact(fact_id="ff-ratio", label="速动比率", display="速动比率为 1.40。",
                        period=REPORT_AS_OF, unit="倍",
                        citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                  "page_number": 71}),
               _FinFact(fact_id="ff-item", label="货币资金", display="货币资金 30.80 元。",
                        period=REPORT_AS_OF, unit="元",
                        citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                  "page_number": 45})))
    outside_authority = PW.FinancialAuthorityInput.create(
        outside_task, outside_artifact, note_gap=_NoteGap(task_id=outside_task.task_id),
        fact_topic_map=(("ff-item", PW.OUTSIDE_SECTION_TOPIC), ("ff-ratio", TOPIC_FIN_SOLVENCY)),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    outside_scan = PW.scan_financial(outside_authority, outside_task)
    check([e.fact_id for e in outside_scan.facts] == ["ff-ratio"],
          "声明为「本节之外」的 fact 不得进入本节权威事实目录")
    check([f[1] for f in outside_scan.excluded_facts] == ["ff-item"],
          "声明为「本节之外」的 fact 必须进入 excluded（去向可查，不得静默丢弃）")
    check(not outside_scan.requirement_ids,
          "财务权威不携带 Contract requirement：requirement_id 不得凭字符串猜出来")
    # 财务节的精确材料清单**合法为空**，因此草稿的出处走**事实轴**（`source_fact_refs`）而不是
    # 材料轴——这正是 `pprov-1` 为纯 `FinancialFactPack` 留的那条轴：只记「这段文字从哪来」，
    # 不授权任何事实（授权仍由候选的路径 A 支撑边与其后的决定给）。
    outside_outcome = _write(
        outside_task, outside_authority, section_id="financial",
        llm=_StubLlm(_plan(
            candidates=[_cand("c1", "速动比率为 1.40。",
                              _fact_edge(outside_scan, "ff-ratio"))],
            prose=[_prose("p1", "速动比率为 1.40。", facts=["f1"], atoms=["c1"])])))
    check(outside_outcome.coverage_summary.get("facts_outside_section_topics") == 1,
          "本节之外的事实必须仍在计数里留痕（去向 + 计数，双重不静默）")
    # 归属在本节之外的事实：它仍是本次运行里「被选中的权威事实」，绝不能静默消失（去向记录
    # 必须存在，见上一条），但它的**内容单位**属于别的章节/别的内容单位，因此不能在本节造缺口
    # —— 那会把 DemoScope 之外的内容单位带进本节，组装期必须拒绝（§七 P0-4 2：内容单位守恒）。
    foreign_topics = sorted({u.topic_id for u in outside_outcome.unresolved
                             if u.topic_id and u.topic_id not in set(outside_task.topic_ids)})
    check(not foreign_topics,
          f"本节缺口不得携带本节 topic 之外的内容单位：{foreign_topics}")

    # 本节 topic 在权威输入里没有任何事实 → 只能是显式缺口，不得用别的主题事实顶替
    empty_topic_task = _task("financial", (TOPIC_FIN_SCOPE, TOPIC_FIN_SOLVENCY),
                             task_id="task-fin-empty-topic", title="财务信息")
    empty_topic_authority = PW.FinancialAuthorityInput.create(
        empty_topic_task,
        _Artifact(artifact_id="ffpa_empty", task_id=empty_topic_task.task_id,
                  facts=(_FinFact(fact_id="ff-ratio", label="速动比率",
                                  display="速动比率为 1.40。", period=REPORT_AS_OF,
                                  unit="倍",
                                  citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                            "page_number": 71}),)),
        note_gap=_NoteGap(task_id=empty_topic_task.task_id),
        fact_topic_map=(("ff-ratio", TOPIC_FIN_SOLVENCY),),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    empty_scan = PW.scan_financial(empty_topic_authority, empty_topic_task)
    check(empty_scan.topics_without_facts == (TOPIC_FIN_SCOPE,),
          "没有任何事实的 topic 必须显式登记为 topics_without_facts")
    empty_outcome = _write(
        empty_topic_task, empty_topic_authority, section_id="financial",
        llm=_StubLlm(_plan(
            candidates=[_cand("c1", "速动比率为 1.40。",
                              _fact_edge(empty_scan, "ff-ratio"))],
            prose=[_prose("p1", "速动比率为 1.40。", facts=["f1"], atoms=["c1"])])))
    no_fact_gaps = [p for p in empty_outcome.draft.unresolved_projections
                    if p["authority_status"] == PW.NO_FACT_AUTHORITY_STATUS]
    check(bool(no_fact_gaps) and no_fact_gaps[0]["topic_id"] == TOPIC_FIN_SCOPE,
          "没有任何事实的 topic 必须留下显式缺口（不得空写、不得改写成完整结论）")
    check("权威输入" in no_fact_gaps[0]["detail"] and "不存在" not in no_fact_gaps[0]["detail"],
          "无事实缺口必须陈述「本节权威输入里没有事实」，不得把「未取得」写成「不存在」")

    # ------------------------------------------------------------------
    # 2d. 财务断言文本：期间 + 科目/指标 + 权威数值渲染（真实 artifact 的 display 只是数值）
    # ------------------------------------------------------------------
    # 真实 `FinancialFactProjection.display` 是**数值渲染**（sections/common.format_yuan_amount
    # /format_metric_display 的产物，如 "1,251.59亿元"、"1.4"），不是整句。若断言文本直接取
    # display，正文就会退化成一串裸数字（无期间、无科目、无口径），人读不成立。
    text_task = _task("financial", (TOPIC_FIN_SOLVENCY,), task_id="task-fin-text",
                      title="财务信息")
    text_authority = _financial_authority(
        text_task,
        _Artifact(artifact_id="ffpa_text", task_id=text_task.task_id,
                  facts=(_FinFact(fact_id="ff-bare", label="速动比率", display="1.4",
                                  period="2025-12-31", unit="ratio",
                                  citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                            "page_number": 71}),)),
        note_gap=_NoteGap(task_id=text_task.task_id))
    text_scan = PW.scan_financial(text_authority, text_task)
    bare_text = text_scan.facts[0].text
    check(bare_text == "2025-12-31的速动比率为1.4。",
          f"财务断言必须带期间与科目，且数值只用权威渲染：{bare_text!r}")
    check(bare_text.count("1.4") == 1,
          "权威 display 已含单位，断言文本不得再补一次 unit（不造「倍倍」这类量纲）")

    # display 缺失时退回 value_text，同样不重算、不换算
    check(PW._financial_fact_text(_FinFact(fact_id="ff-nod", label="流动比率", display="",
                                           period="2025-12-31", value_text="1.57")) ==
          "2025-12-31的流动比率为1.57。",
          "display 缺失时必须退回权威 value_text，且不得追加 unit")

    # ------------------------------------------------------------------
    # 2d-2（3.6）：代理口径事实的**权威表面自带口径限定语**
    # ------------------------------------------------------------------
    # 权威侧本来就把口径写在 `status` / `note` 上（`financial_worker`：`CALCULATED_PROXY` +
    # `f"代理口径（{reason_code}）"`）。只做否证（「不得写成精确」）不够：一条既不声称精确、
    # 也不写口径的断言，读者会把它读成受审的精确值。因此限定语必须成为**权威表面自己**的
    # 一部分 —— 写入侧看到的行、硬门复算的表面，是同一份文本。
    proxy_fact = _FinFact(fact_id="ff-proxy", label="利息保障倍数", display="3.2",
                          period="2025-12-31", unit="ratio", code="INT_COVER",
                          status="CALCULATED_PROXY",
                          note="代理口径（INTEREST_EXPENSE_MISSING）",
                          citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                    "page_number": 71})
    check(PW._financial_fact_text(proxy_fact)
          == "2025-12-31的利息保障倍数为3.2。代理口径（INTEREST_EXPENSE_MISSING）。",
          "代理口径事实的权威表面必须自带权威自己的限定语（措辞不发明，逐字取 `note`）")
    check(PW._financial_fact_text(_FinFact(fact_id="ff-exact", label="流动比率", display="1.57",
                                           period="2025-12-31", status="CALCULATED_EXACT"))
          == "2025-12-31的流动比率为1.57。",
          "精确口径的事实不得被加任何口径限定语（限定语只对权威自己标为代理的事实成立）")
    check("代理口径（INTEREST_EXPENSE_MISSING）"
          in NS.authority_numeric_texts("financial_pack", proxy_fact),
          "限定语必须落在该事实自己的数字授权池里：否则正文照抄限定语反而会被判成未授权数字")
    # 写入侧真正看到的那一行（`facts_payload.text` 逐字取它）：限定语必须已经在那里。
    proxy_authority = _financial_authority(
        text_task,
        _Artifact(task_id=text_task.task_id, facts=(proxy_fact,)),
        note_gap=_NoteGap(task_id=text_task.task_id))
    proxy_scan = PW.scan_financial(proxy_authority, text_task)
    check(bool(proxy_scan.facts)
          and "代理口径（INTEREST_EXPENSE_MISSING）" in proxy_scan.facts[0].text,
          "写入侧的权威事实行必须带着口径限定语（否则模型无从保留它，规则只会打回）")

    # 附注事实没有 period 字段：不得凭空造期间，也不得把无数值的附注编出数字
    check(PW._note_fact_text(_NoteFact(fact_id="nf-bare", label="应收账款",
                                       display="12.34亿元", evidence_id=EV_ID,
                                       evidence_char_range=(0, 10))) == "应收账款为12.34亿元。",
          "附注断言必须是「科目为数值」，不得只写裸数值")
    check(PW._note_fact_text(_NoteFact(fact_id="nf-none", label="应收账款按账龄披露",
                                       display="", value_text=None, evidence_id=EV_ID,
                                       evidence_char_range=(0, 10))) == "应收账款按账龄披露",
          "无数值的附注事实只陈述科目名（不编期间、不补数字），也不加句号以外的修饰")
    expect_error(
        lambda: PW._note_fact_text(_NoteFact(fact_id="nf-x", label="", display="",
                                             value_text=None)),
        PW.PackWriterError, "既无数值也无科目名的附注事实没有可写文本，必须被拒")

    # ------------------------------------------------------------------
    # 2e. 权威根：断言文本必须与硬门复算出的权威表面**同源**（唯一实现）
    # ------------------------------------------------------------------
    fin_scan = PW.scan_financial(fin_authority, fin_task)
    check(all(e.text == NS.authoritative_fact_surface(e.authority_kind,
                                                      # 复算用真实权威对象，不用读视图文本
                                                      fin_artifact.facts[i])
              for i, e in enumerate(fin_scan.facts)),
          "财务断言文本必须逐字等于 `NS.authoritative_fact_surface` 的复算结果（同源）")

    # ==================================================================
    # 3. derived 一律 fail-closed（M930-3 未被 DemoScope 选中）
    # ==================================================================
    # 上游身份必须由**上游自身**带齐（company/report_as_of/contract 版本与指纹 + projection），
    # 章节在这里不自行编造 —— 所以每一条派生身份都从 `upstream_identity` 里取。
    derived_upstream = {"company_id": COMPANY_ID, "report_as_of": REPORT_AS_OF,
                        "contract_version": CONTRACT_VERSION,
                        "contract_fingerprint": CONTRACT_FINGERPRINT,
                        "projection_id": projections["company"].projection_id}
    expect_error(
        lambda: PW.DerivedAuthorityInput.create(
            co_task, upstream_identity=derived_upstream,
            selected_facts=(("f-1", "topic_pack"),), required_fact_ids=(),
            demo_scope_selected=False),
        PW.PackWriterError, "derived 章节在 DemoScope 未选中时必须 fail-closed",
        needle="DemoScope")
    expect_error(
        lambda: PW.scan_authority(
            PW.DerivedAuthorityInput.create(
                co_task, upstream_identity=derived_upstream,
                selected_facts=(("f-1", "topic_pack"),), required_fact_ids=(),
                demo_scope_selected=True),
            co_task),
        PW.PackWriterError, "已选中的 derived 章节在本批仍不启用（不得另开第二条写作链）",
        needle="未启用")
    # 上游身份缺一项就必须在**构造期**被拒（不得留到扫描期才发现）。
    expect_error(
        lambda: PW.DerivedAuthorityInput.create(
            co_task, upstream_identity={k: v for k, v in derived_upstream.items()
                                        if k != "projection_id"},
            selected_facts=(("f-1", "topic_pack"),), required_fact_ids=(),
            demo_scope_selected=True),
        PW.PackWriterError, "derived 章节的上游身份缺 projection_id 必须在构造期 fail-closed",
        needle="projection_id")

    # 3b. 上游真实类型的**封闭词表**必须在真实类型构造期就生效：未登记的 aspect 结果状态
    #     不得靠「写进 Pack 再让下游猜」蒙混过关（否则完成度判定会被绕过）。
    expect_error(
        lambda: _pack_set(
            _task("company", (TOPIC_BUSINESS,), task_id="task-co-bad-status"),
            facts=(_fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                         (ASP_BUSINESS_MAIN,)),),
            aspect_results=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                            _AspectResult(ASP_BUSINESS_SALES, "covered_and_verified"))),
        TS.SchemaValidationError,
        "未登记的 aspect 结果状态必须在真实类型构造期被拒（词表封闭）")
    for status in TS.ASPECT_RESULT_STATUSES:
        check(status in {"covered", "partial", "not_found", "blocked", "not_applicable"},
              f"aspect 结果状态词表不得被悄悄扩写（实际含 {status!r}）")

    # ==================================================================
    # 4. Writer 没有检索能力 + 提案词汇表封闭
    # ==================================================================
    src = (Path(__file__).resolve().parent.parent / "sections" / "pack_writer.py"
           ).read_text(encoding="utf-8")
    import_lines = [ln.strip() for ln in src.splitlines()
                    if re.match(r"^\s*(from|import)\s", ln)]
    check(not any(re.search(r"ToolRegistry|tool_registry|\brouter\b|route\b|retriev",
                           ln, re.IGNORECASE) for ln in import_lines),
          "pack_writer 不得 import 任何 Router/ToolRegistry/检索入口")

    # 4a. 顶层键集封闭：工具调用 / 补充检索 / 自评一律是**未登记字段**。
    for bad_payload, what in (
            ({"claim_candidates": [], "narrative_draft_units": [],
              "tool_calls": [{"name": "search"}]}, "工具调用字段"),
            ({"claim_candidates": [], "narrative_draft_units": [], "need_more": True},
             "补充检索字段"),
            ({"claim_candidates": [], "narrative_draft_units": [], "retrieve": ["更多材料"]},
             "检索意图字段"),
            ({"claim_candidates": [], "narrative_draft_units": [],
              "retrieval": {"query": "更多"}}, "检索请求字段"),
            # §16.7 P6：Writer 不再直出定稿对象 —— 定稿 Claim / citation / 决定 / 章节结果
            # 在提案词汇表里**不可表达**。
            ({"claim_candidates": [], "narrative_draft_units": [],
              "claims": [{"claim_id": "forged"}]}, "定稿 Claim 字段"),
            ({"claim_candidates": [], "narrative_draft_units": [],
              "section_result": {"status": "COMPLETED"}}, "章节结果字段"),
            ({"claim_candidates": [], "narrative_draft_units": [],
              "evaluation": {"decision": "PASS"}}, "自评/决定字段"),
            ({"claim_candidates": [], "narrative_draft_units": [],
              "accepted_bindings": []}, "接受绑定字段")):
        expect_error(
            lambda p=bad_payload: PW.parse_writer_proposals(json.dumps(p, ensure_ascii=False)),
            PW.PackWriterError, f"提案输出出现{what}必须被拒", needle="未登记字段")

    # 4b. 候选边的语法与语义声明必须自洽（缺一即拒，不做任何宽容）。
    good_edge = {"authority_kind": "topic_pack", "container_id": "pack-x", "fact_id": "fx",
                 "material_id": None, "support_role": "primary",
                 "support_semantics": "factual", "authorization_path": "path_a_prevalidated"}

    def _raw(payload) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def _cand_payload(edge: dict, *, text: str = "公司主营业务为动力电池系统的研发与销售。",
                      key: str = "c1") -> dict:
        return {"claim_candidates": [{"candidate_key": key, "claim_text": text,
                                      "support": [edge]}],
                "narrative_draft_units": []}

    # 缺 support_semantics：候选只接受 factual 边，缺失即拒。
    edge_no_semantics = {k: v for k, v in good_edge.items() if k != "support_semantics"}
    expect_error(lambda: PW.parse_writer_proposals(_raw(_cand_payload(edge_no_semantics))),
                 PW.PackWriterError, "支撑边缺 support_semantics 必须被拒", needle="support_semantics")
    # 声明 context：context 材料只验证背景/衔接，不授权事实。
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload(
            {**good_edge, "support_semantics": "context", "authorization_path": "context_only"}))),
        PW.PackWriterError, "候选的支撑边声明 context 必须被拒（context 不授权事实）",
        needle="context")
    # 路径 A 自带 material_id：material 锚点只能由权威事实自己的引用派生。
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload({**good_edge, "material_id": "m1"}))),
        PW.PackWriterError, "路径 A 自带 material_id 必须被拒（material 不由模型选择）",
        needle="路径 A 却自带 material_id")
    # 路径 A 缺 fact_id。
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload({**good_edge, "fact_id": ""}))),
        PW.PackWriterError, "路径 A 缺 fact_id 必须被拒", needle="必须给出 fact_id")
    # 路径 B 携带 fact 身份 / 非 topic_pack / 缺 material_id。
    for bad_edge, what, needle in (
            ({**good_edge, "authorization_path": "path_b_material_derived", "material_id": "m1"},
             "路径 B 携带 fact_id", "不得携带任何事实身份"),
            ({**good_edge, "authorization_path": "path_b_material_derived", "fact_id": "",
              "material_id": "m1", "authority_kind": "financial_pack"},
             "路径 B 声明非 topic_pack 权威", "只存在于 topic_pack"),
            ({**good_edge, "authorization_path": "path_b_material_derived", "fact_id": "",
              "material_id": ""}, "路径 B 缺 material_id", "必须给出 material_id")):
        expect_error(lambda e=bad_edge: PW.parse_writer_proposals(_raw(_cand_payload(e))),
                     PW.PackWriterError, f"{what}必须被拒", needle=needle)
    # 未登记的授权路径 / 语义 / role。
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload(
            {**good_edge, "authorization_path": "path_c_free_text"}))),
        PW.PackWriterError, "未登记的授权路径必须被拒", needle="不能支撑事实性候选")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload({**good_edge, "support_role": "boss"}))),
        PW.PackWriterError, "未登记的 support_role 必须被拒", needle="support_role")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload({**good_edge, "authority_kind": "web"}))),
        PW.PackWriterError, "未登记的 authority_kind 必须被拒", needle="authority_kind")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload({**good_edge, "container_id": ""}))),
        PW.PackWriterError, "支撑边缺 container_id 必须被拒", needle="container_id")
    # 未知事实身份字段（financial_fact_id / note_fact_id / external_fact_id / claim_id）
    # 都是未登记字段：身份必须走它自己 kind 的字段，不得跨 kind 冒充。
    for rogue_key, rogue_value in (("financial_fact_id", "ff-1"), ("note_fact_id", "nf-1"),
                                   ("external_fact_id", "ef-1"), ("claim_id", "claim-x"),
                                   ("citation_id", "cite-x"), ("text", "自由事实文本")):
        expect_error(
            lambda k=rogue_key, v=rogue_value: PW.parse_writer_proposals(
                _raw(_cand_payload({**good_edge, k: v}))),
            PW.PackWriterError, f"支撑边自带 {rogue_key} 必须被拒（身份/文本不得由模型自报）",
            needle="未登记字段")
    # 候选结构：空 support / 非 primary 首边 / 空文本 / 重复 key / 未登记字段。
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({"claim_candidates": [
            {"candidate_key": "c1", "claim_text": "公司主营业务为动力电池。", "support": []}],
            "narrative_draft_units": []})),
        PW.PackWriterError, "候选没有任何支撑边必须被拒", needle="至少有一条")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload({**good_edge,
                                                             "support_role": "corroborating"}))),
        PW.PackWriterError, "候选的第一条支撑边不是 primary 必须被拒", needle="必须是 primary")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw(_cand_payload(good_edge, text="   "))),
        PW.PackWriterError, "候选文本为空必须被拒", needle="不得为空")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({"claim_candidates": [
            {"candidate_key": "c1", "claim_text": "甲。", "support": [good_edge]},
            {"candidate_key": "c1", "claim_text": "乙。", "support": [good_edge]}],
            "narrative_draft_units": []})),
        PW.PackWriterError, "候选标签重复必须被拒", needle="重复")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({"claim_candidates": [
            {"candidate_key": "c1", "claim_text": "甲。", "support": [good_edge],
             "is_primary": True}], "narrative_draft_units": []})),
        PW.PackWriterError, "候选自带未登记字段必须被拒", needle="未登记字段")
    # 未登记的 sentence_kind / 表格行 / 连接语一律不可表达（narr-3 的正文组织词汇已移除）。
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({
            "claim_candidates": [], "narrative_draft_units": [
                {"unit_key": "u1", "unit_kind": "sentence", "text": "此外，"}]})),
        PW.PackWriterError, "未登记的 unit_kind 必须被拒", needle="unit_kind")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({
            "claim_candidates": [], "narrative_draft_units": [
                {"unit_key": "u1", "unit_kind": "paragraph", "text": "甲。",
                 "connector": "此外，"}]})),
        PW.PackWriterError, "草稿单元自带连接语必须被拒（连接语不得承载事实）",
        needle="未登记字段")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({
            "claim_candidates": [], "narrative_draft_units": [
                {"unit_key": "u1", "unit_kind": "table", "text": "表草稿",
                 "rows": [{"claim_ids": ["c1"]}]}]})),
        PW.PackWriterError, "草稿单元自带表格行必须被拒（表格行/元数据在门后形成）",
        needle="未登记字段")
    expect_error(
        lambda: PW.parse_writer_proposals(_raw({
            "claim_candidates": [], "narrative_draft_units": [
                {"unit_key": "u1", "unit_kind": "paragraph", "text": "  "}]})),
        PW.PackWriterError, "草稿单元文本为空必须被拒", needle="不得为空")
    # context 边：只允许 topic_pack material，且必须带真实 material / context_only / corroborating。
    good_ctx = {"authority_kind": "topic_pack", "container_id": "pack-x", "material_id": "m1",
                "support_role": "corroborating", "support_semantics": "context",
                "authorization_path": "context_only"}
    for bad_ctx, what, needle in (
            ({**good_ctx, "authority_kind": "evidence_note"},
             "context 边声明 evidence_note 权威", "不可表达为 context 边"),
            ({**good_ctx, "material_id": ""}, "context 边缺 material_id", "必须绑定一份真实 material"),
            ({**good_ctx, "support_semantics": "factual"}, "context 边声明 factual", "必须是 'context'"),
            ({**good_ctx, "authorization_path": "path_a_prevalidated"},
             "context 边声明路径 A", "必须是 'context_only'"),
            ({**good_ctx, "support_role": "primary"}, "context 边声明 primary", "一律同向"),
            ({**good_ctx, "fact_id": "fx"}, "context 边携带事实身份", "未登记字段")):
        expect_error(
            lambda e=bad_ctx: PW.parse_writer_proposals(_raw({
                "claim_candidates": [], "narrative_draft_units": [
                    {"unit_key": "u1", "unit_kind": "paragraph", "text": "甲。",
                     "context_support": [e]}]})),
            PW.PackWriterError, f"{what}必须被拒", needle=needle)
    # 合法的最小提案束必须能解析（证明上面的反例不是「一律拒」）。
    # `require_natural_draft=False`：这一组是**语法层**探针，不带 `aliases`，而草稿单元的出处
    # 恰有一条轴要非空、两轴都靠别名表展开 ⇒ 没有别名表时一份合法草稿层在语法上写不出来。
    # 关掉的是「有候选必须有草稿」这条**形状**判定，不是任何一条结构判定。
    check(PW.parse_writer_proposals(_raw(_cand_payload(good_edge)),
                                    require_natural_draft=False)["claim_candidates"]
          and PW.parse_writer_proposals(_raw({
              "claim_candidates": [], "narrative_draft_units": [
                  {"unit_key": "u1", "unit_kind": "paragraph", "text": "甲。",
                   "context_support": [good_ctx]}]}))["narrative_draft_units"][0][
                       "context_support"][0]["material_id"] == "m1",
          "合法的最小候选/草稿单元提案必须能解析")
    expect_error(
        lambda: PW.parse_writer_proposals("这不是 JSON"),
        PW.PackWriterError, "非 JSON 输出必须被拒", needle="不是合法 JSON")
    expect_error(
        lambda: PW.parse_writer_proposals("[]"),
        PW.PackWriterError, "顶层不是对象必须被拒", needle="必须是对象")

    # ==================================================================
    # 5. 合法写作：门前候选提案束（Claim / 草稿单元 / proposal）＋ 精确材料处理
    # ==================================================================
    # 本节之后 Writer 就到 `SectionDraft` 为止。定稿 Claim / 最终 Narrative / 接受绑定 /
    # `SectionResult` / 逐 fact 的 `FactNarrativeDisposition` 全部在门后（P8/P9/P10 + P15）。
    authority = _company_authority(
        co_task, facts=(_fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                              (ASP_BUSINESS_MAIN,), scope="本公司"),
                        _fact("f-2", "公司同时经营储能电池系统业务，2025 年该业务收入占比为 12.5%。",
                              (ASP_BUSINESS_MAIN,), scope="本公司")),
        materials=(_Material("m-extra-1", f"evidence:{NOTE_EV_ID}"),
                   _Material("m-extra-2", "evidence:ev-unused-1")),
        aspects=base_aspects,
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),
            _aspect(ASP_BUSINESS_SALES, TOPIC_BUSINESS, "q-company_business", status="partial"),
            _aspect(ASP_BUSINESS_COST, TOPIC_BUSINESS, "q-company_business", status="blocked"),)),))
    scan = PW.scan_topic_pack(authority, co_task)
    check(len(scan.facts) == 2, f"两个有 aspect 归属的权威 fact 各产出一条目录条目，实为 {len(scan.facts)}")
    check(scan.aspect_status[ASP_BUSINESS_COST] == "blocked", "aspect 状态原样读入")
    check(scan.coverage_counts == {"covered": 1, "partial": 1, "blocked": 1},
          f"覆盖计数按原状态统计，实为 {scan.coverage_counts}")
    facts_by_id = {e.fact_id: e for e in scan.facts}
    container = facts_by_id["f-1"].container_identity
    check(facts_by_id["f-1"].material_id
          and facts_by_id["f-1"].material_id == facts_by_id["f-2"].material_id,
          "同一来源身份（同一 Evidence）的两个事实共享同一个 material 锚点"
          "（锚点由引用派生，不由模型选）")
    check(all(e.container_identity == container for e in scan.facts),
          "同一 Pack 的事实共享同一容器身份（真实内容身份）")
    check("claims" not in {f for f in PW.AuthorityScan.__dataclass_fields__},
          "读视图只有 `facts`（权威事实目录）——不再有 narr-3 的 `claims`")

    # 当前线（`PROPOSAL_WIRE_CURRENT`）的写作顺序是硬的：先出自然草稿，再为草稿里每个事实
    # 原子单独提交候选。本节有材料面，因此草稿的出处走**材料轴**（`source_member_refs`），
    # 别名由写入侧同一张表派生——夹具不自己拼 `m1`。
    co_context = _writer_material_context(authority.pack_set, task_id=str(co_task.task_id),
                                          section_id="company")
    co_manifest = PW._derive_material_manifest(authority, material_context=co_context)
    co_aliases = PW._support_aliases(scan, co_manifest)
    co_member = {str(o.material_id): str(o.ref) for o in co_aliases.materials}
    fact_member = co_member[str(facts_by_id["f-1"].material_id)]
    plan_ok = _plan(
        candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(scan, "f-1")),
                    _cand("c2", facts_by_id["f-2"].text, _fact_edge(scan, "f-2"))],
        prose=[_prose("p1", facts_by_id["f-1"].text + facts_by_id["f-2"].text,
                      members=[fact_member], atoms=["c1", "c2"])],
        units=[_unit("u1", "以下按业务条线说明公司经营结构。",
                     context=[_context_edge(container, "m-extra-1")]),
               _unit("u2", "业务结构一览", kind="table")])
    stub = _StubLlm(plan_ok)
    outcome = _write(co_task, authority, section_id="company", llm=stub)
    draft = outcome.draft
    check(outcome.llm_calls == 1, f"一次成功的写作只调用一次生成，实为 {outcome.llm_calls}")
    check(len(draft.claim_candidates) == 2 and len(draft.narrative_draft_units) == 2,
          "候选与草稿单元按计划产出")
    check([u.unit_kind for u in draft.narrative_draft_units] == ["paragraph", "table"],
          "草稿单元类型逐字取自计划（表格单元只承载文本，元数据在门后形成）")
    factual = [p for p in draft.proposed_support_refs if p.support_semantics == "factual"]
    context = [p for p in draft.proposed_support_refs if p.support_semantics == "context"]
    check({p.binding_subject_id for p in factual} == {c.candidate_id for c in draft.claim_candidates},
          "每条候选恰有 factual proposal（候选必须有据）")
    check(len(context) == 1
          and context[0].binding_subject_kind == "narrative_draft_unit"
          and all(getattr(context[0], f) is None for f in NS.ALL_FACT_FIELDS),
          "context proposal 只挂在草稿单元上，且不带任何事实身份")
    check(all(p.material_id == facts_by_id["f-1"].material_id and p.payload_ref
              for p in factual),
          "路径 A proposal 的 material 锚点与 payload 由该事实自己的引用派生（不由模型选）")
    check(all(p.dependency_fingerprint == DEPENDENCY_FINGERPRINT
              for p in draft.proposed_support_refs),
          "每条 proposal 都结构性携带本次写作的依赖指纹（可追溯）")
    check(draft.dependency_fingerprint == DEPENDENCY_FINGERPRINT,
          "Draft 自己记录同一依赖指纹")

    # 材料三层集合等式：available = used ∪ not_used，逐成员恰一行
    available = {m.member_ref for m in draft.material_manifest.entries}
    used = {d.member_ref for d in draft.material_dispositions if d.usage == "used"}
    not_used = {d.member_ref for d in draft.material_dispositions if d.usage == "not_used"}
    check(len(available) == 3 and len(used) == 2 and len(not_used) == 1
          and used == {NS.manifest_member_ref(container, facts_by_id["f-1"].material_id),
                       NS.manifest_member_ref(container, "m-extra-1")}
          and (used | not_used) == available and not (used & not_used)
          and len(draft.material_dispositions) == len(available),
          f"材料处理必须满足三层集合等式（available={len(available)} used={len(used)} "
          f"not_used={len(not_used)}）")
    NS.validate_material_processing(draft.material_manifest, draft.material_dispositions,
                                    draft.proposed_support_refs)
    check(not_used == {NS.manifest_member_ref(container, "m-extra-2")}
          and all(d.reason_code in NS.MATERIAL_NOT_USED_REASONS
                  and d.reason_proof and d.processed and not d.support_usages
                  for d in draft.material_dispositions if d.usage == "not_used"),
          "未被引用的材料是 `not_used`（不是缺口），且必须带可重算的判定记录")

    check(outcome.gate_result.rules_passed and not outcome.gate_result.blocking,
          "合法候选束必须过 narrative 硬门")
    check(outcome.gate_result.section_draft_id == draft.draft_id,
          "硬门结果必须绑定本 draft")

    # §6.4：Writer 产物里**没有**定稿对象（类型层不可表达）
    draft_fields = set(NS.SectionDraft.__dataclass_fields__)
    outcome_fields = set(PW.PackWriteOutcome.__dataclass_fields__)
    for forbidden in ("section_result_id", "claim_ids", "section_claim_ids",
                      "narrative", "paragraphs", "tables", "dispositions"):
        check(forbidden not in draft_fields,
              f"SectionDraft 不得承载门后身份 `{forbidden}`（类型层不可表达）")
    for forbidden in ("binding", "section_result", "entailment", "accepted_bindings"):
        check(forbidden not in outcome_fields,
              f"PackWriteOutcome 不得承载门后身份 `{forbidden}`（Writer 不自评、不代签）")

    # 缺口：partial / blocked 原样保留，不得改写成 covered
    raw_of = {p["unresolved_id"]: p["authority_status"]
              for p in draft.unresolved_projections}
    check(set(raw_of.values()) == {"partial", "blocked"},
          f"缺口投影必须逐字保留权威状态，实为 {sorted(raw_of.values())}")
    check(all(u.state not in ("covered", "SATISFIED") for u in outcome.unresolved),
          "缺口不得被改写成 covered/SATISFIED")
    check(all(raw in next(u.detail for u in outcome.unresolved
                          if u.unresolved_id == uid)
              for uid, raw in raw_of.items()),
          "缺口 detail 必须写明权威原始状态（不得模糊成「未覆盖」）")
    check(outcome.coverage_summary["counts"] == {"covered": 1, "partial": 1, "blocked": 1},
          "覆盖计数在 sidecar 里如实保留原始状态")
    check({a["aspect_id"]: a["status"] for a in outcome.coverage_summary["aspects"]}
          == scan.aspect_status,
          "sidecar 的 aspect 状态必须是读视图的逐字副本（未改写）")

    # 「未取得」不得写成「不存在 / 未披露 / 没有」
    forbidden_words = tuple(FPA.EVIDENCE_NOTE_GAP_FORBIDDEN_WORDING)
    gap_text = json.dumps([dataclasses.asdict(u) for u in outcome.unresolved],
                          ensure_ascii=False, default=str)
    check(not any(w in gap_text for w in forbidden_words),
          f"缺口叙述不得出现 {list(forbidden_words)}（「未取得」≠「不存在」）")

    # ==================================================================
    # 6. 数字权威：候选/草稿单元文本里的数字必须被**它自己的路径 A 权威事实**授权
    # ==================================================================
    # narr-4 里 Writer 只输出候选文本（没有第二条数字通路：没有连接语、没有自由事实文本、
    # 没有表格行/单元格），所以「自造一个非权威数字」的**唯一**入口就是候选文本本身。
    num_task = _task("company", (TOPIC_IDENTITY,), task_id="task-co-identity")
    num_fact = _fact("f-cap", "公司注册资本为 1234.56 万元。", (ASP_IDENTITY_CAPITAL,),
                     scope="本公司")
    num_authority = _company_authority(
        num_task, facts=(num_fact,),
        aspects=(_AspectResult(ASP_IDENTITY_CAPITAL, "covered"),),
        requirements=(_Req(TOPIC_IDENTITY, (
            _aspect(ASP_IDENTITY_CAPITAL, TOPIC_IDENTITY, "q-company_identity"),)),))
    num_scan = PW.scan_topic_pack(num_authority, num_task)
    num_container = num_scan.facts[0].container_identity

    ok_num = _write(num_task, num_authority, section_id="company",
                    llm=_StubLlm(_plan(candidates=[
                        _cand("c1", num_fact.text, _fact_edge(num_scan, "f-cap"))])))
    check(not ok_num.gate_result.blocking,
          "权威数字可以进入候选文本（证明不是一律拒数字）")
    # 反例 1：候选文本自造一个非权威数字（权威是 1234.56）。
    expect_error(
        lambda: _write(num_task, num_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=[
                           _cand("c1", "公司注册资本为 9999.99 万元。",
                                 _fact_edge(num_scan, "f-cap"))]))),
        PW.PackWriterError, "候选文本自造非权威数字必须被硬门拒",
        needle="narrative_number_unauthorized")
    # 反例 2：改写权威数字的写法（把 1234.56 换成 1,234.56 之外的写法）同样未被逐字授权。
    expect_error(
        lambda: _write(num_task, num_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=[
                           _cand("c1", "公司注册资本为 1234.5600 万元。",
                                 _fact_edge(num_scan, "f-cap"))]))),
        PW.PackWriterError, "候选文本改写权威数字写法必须被硬门拒",
        needle="narrative_number_unauthorized")
    # 反例 3：路径 B（材料派生）的候选**不得携带任何数字**——数字是高风险硬事实，只能走
    # 路径 A 预验证；材料派生的描述性原子不含数字。
    #
    # §三 B：这条现在由**写入侧高风险表面预验证**在组装 Draft 之前拦住（比硬门更早），因为
    # 判据是「该数字必须能在它所绑定材料的正文里逐字找到」——而这里绑定的材料正文里没有它。
    # 两道网仍然都在：写入侧这条针对「材料里读不出来」的高风险表面，`gate_draft` 的
    # `narrative_number_unauthorized` 仍然独立复核任何手工构造的 Draft（见 narrative 门测试）。
    expect_error(
        lambda: _write(num_task, num_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=[
                           _cand("c1", "公司注册资本为 1234.56 万元。",
                                 _material_edge(num_container,
                                                num_scan.facts[0].material_id))]))),
        PW.PackWriterError, "只有路径 B 支撑的候选携带数字必须在组装 Draft 之前被拒",
        needle="高风险表面")
    # 反例 4：含糊期间措辞（候选文本与草稿单元文本各一次）。
    expect_error(
        lambda: _write(num_task, num_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=[
                           _cand("c1", "报告期内公司注册资本未发生变化。",
                                 _fact_edge(num_scan, "f-cap"))]))),
        PW.PackWriterError, "候选文本含未绑定权威期间的措辞必须被硬门拒",
        needle="narrative_vague_period")
    expect_error(
        lambda: _write(num_task, num_authority, section_id="company",
                       llm=_StubLlm(_plan(
                           candidates=[_cand("c1", num_fact.text,
                                             _fact_edge(num_scan, "f-cap"))],
                           units=[_unit("u1", "报告期内公司经营情况如下。")]))),
        PW.PackWriterError, "草稿单元文本含未绑定权威期间的措辞必须被硬门拒",
        needle="narrative_vague_period")
    # narr-3 的正文组织词汇已从契约里消失（不是「测试没写」，是不可表达）。
    check(not hasattr(PW, "parse_narration_plan"),
          "narr-3 的 `parse_narration_plan` 必须已删除（门前只解析候选提案束）")
    check(PW._PLAN_KEYS == ("natural_prose_draft", "claim_candidates",
                            "narrative_draft_units", "follow_up_needs"),
          "提案束顶层键集封闭（无连接语 / 句子类型 / 表格行 / 定稿 Claim 字段）。"
          "`pw-15` 起 `natural_prose_draft` **排在最前**：写作顺序是硬的（先起草稿、再为草稿里"
          "每个事实原子单独提交候选），键序就是这条顺序在产物里的表达")
    check(PW._PROSE_KEYS == ("prose_key", "text", "source_member_refs", "source_fact_refs",
                             "atom_candidate_keys"),
          "草稿单元的键集必须封闭在「文本 + 出处 + 原子映射」上："
          "句子类型、连接语、定稿决定都不得在这一层表达。`pw-16` 起出处是**两条互斥的轴**"
          "（材料行 / 权威事实行），恰有一条非空——事实轴的用处只在材料面**合法为空**的节"
          "（财务节的常态），它只记这段文字从哪来，**不**授权任何事实（出处不是授权）")

    # ==================================================================
    # 7. Contract 必需事实必须被 proposal 引用，否则出 rework 级提示（不得静默）
    # ==================================================================
    req_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-required")
    req_authority = _company_authority(
        req_task,
        facts=(_fact("f-must", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,)),
               _fact("f-opt", "公司销售模式以直销为主。", (ASP_BUSINESS_SALES,))),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                 _AspectResult(ASP_BUSINESS_SALES, "covered")),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business",
                    blocking=("SECTION_BLOCKED",)),
            _aspect(ASP_BUSINESS_SALES, TOPIC_BUSINESS, "q-company_business"),)),))
    req_scan = PW.scan_topic_pack(req_authority, req_task)
    req_entry = {e.fact_id: e for e in req_scan.facts}
    check([e.fact_id for e in req_scan.facts if e.required] == ["f-must"],
          "blocking aspect 支撑的 fact 才是 required")
    check(NS.required_fact_ids(req_authority) == frozenset({"f-must"}),
          "必需事实集合必须与硬门同源（`NS.required_fact_ids`）")
    check([p for p in NS.gate_draft(
        _write(req_task, req_authority, section_id="company",
               llm=_StubLlm(_plan(candidates=[
                   _cand("c1", req_entry["f-opt"].text, _fact_edge(req_scan, "f-opt"))]))).draft,
        req_authority).issues if p.rule_id == "required_fact_not_proposed"],
        "必需事实未被任何 proposal 引用时，硬门必须出 required_fact_not_proposed")
    partial_outcome = _write(
        req_task, req_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=[
            _cand("c1", req_entry["f-opt"].text, _fact_edge(req_scan, "f-opt"))])))
    reworks = partial_outcome.gate_result.issues_of("rework")
    check([i.rule_id for i in reworks] == ["required_fact_not_proposed"]
          and reworks[0].severity == "rework"
          and reworks[0].location == f"{req_entry['f-must'].container_identity}:f-must"
          and reworks[0].rework_target_ref == partial_outcome.draft.draft_id,
          f"必需事实未呈现只能是 rework 级提示，且必须点名容器:事实 与返修目标"
          f"（实际 {[i.to_dict() for i in reworks]}）")
    check(not partial_outcome.gate_result.blocking,
          "门前不得把「未呈现」直接判成 blocking —— 缺口判定要带 Contract 依据（门后 FND）")
    # 返修不是无限预算：`max_llm_retries=1` 意味着**最多两次调用**，返修原因必须回传给生成器，
    # 但预算耗尽后**不得**把「必需事实未呈现」升级成 blocking 或缺口 —— 缺口判定要带 Contract
    # 依据、发生在门后 FND（3D）。所以这里的诚实行为是：接受草案，并让
    # `required_fact_not_proposed` 以 rework 级提示原样留在门结果里，不静默丢弃。
    stuck_req = _StubLlm(_plan(candidates=[
        _cand("c1", req_entry["f-opt"].text, _fact_edge(req_scan, "f-opt"))]),
        _plan(candidates=[
            _cand("c1", req_entry["f-opt"].text, _fact_edge(req_scan, "f-opt"))]),
        _plan(candidates=[
            _cand("c1", req_entry["f-opt"].text, _fact_edge(req_scan, "f-opt"))]))
    stuck_outcome = _write(req_task, req_authority, section_id="company", llm=stuck_req,
                           policy=PW.WriterPolicy(max_llm_retries=1))
    check(len(stuck_req.calls) == 2,
          f"返修预算必须被策略钉死为 max_llm_retries+1 = 2 次（多给的响应不得被消费，"
          f"实为 {len(stuck_req.calls)}）")
    check("required_fact_not_proposed" in stuck_req.calls[1]["messages"][0]["content"],
          "返修原因必须回传给生成器（不是盲重试）")
    check([i.rule_id for i in stuck_outcome.gate_result.issues_of("rework")]
          == ["required_fact_not_proposed"]
          and not stuck_outcome.gate_result.blocking,
          "返修预算耗尽后不得把「必需事实未呈现」升级成 blocking（缺口要带 Contract 依据，属门后）")
    check(all(u.reason_code != "required_fact_not_proposed"
              for u in stuck_outcome.unresolved),
          "门前的 rework 提示不得被自动改写成缺口（拒绝 ≠ 缺口，缺口 ≠ 拒绝）")
    # 正例：必需事实被 proposal 引用时不得出现该提示（反例不是恒失败）。
    full_outcome = _write(req_task, req_authority, section_id="company",
                          llm=_StubLlm(_plan(candidates=[
                              _cand("c1", req_entry["f-must"].text,
                                    _fact_edge(req_scan, "f-must")),
                              _cand("c2", req_entry["f-opt"].text,
                                    _fact_edge(req_scan, "f-opt"))])))
    check(not full_outcome.gate_result.issues_of("rework")
          and not full_outcome.gate_result.blocking,
          "必需事实与可选事实都被引用时，硬门必须放行")

    # ==================================================================
    # 8. 财务：附注缺口进入 unresolved；附注/artifact 事实的路径 A 身份闭环
    # ==================================================================
    fin_scan = PW.scan_financial(fin_authority, fin_task)
    fin_entry = {e.fact_id: e for e in fin_scan.facts}
    check(fin_entry["ff-short"].authority_kind == "financial_pack",
          "财务 artifact 事实的 authority_kind 必须是 financial_pack")
    check(fin_scan.requirement_ids == {},
          "财务权威不携带 Contract requirement：requirement_id 不得凭字符串猜出来")
    # 财务节的精确材料清单合法为空 ⇒ 草稿出处走**事实轴**（`draft_axis="fact"`），别名取自
    # 本节真实别名表的顺序（`f1..`），而不是让夹具自己拼一个看起来对的字符串。
    gap_outcome = _write(
        fin_task, fin_authority, section_id="financial",
        llm=_StubLlm(_plan(candidates=[
            _cand("c1", fin_entry["ff-short"].text, _fact_edge(fin_scan, "ff-short"))],
            draft_axis="fact", draft_ref=[_fact_ref(fin_scan, "ff-short")])))
    note_gap_projection = [p for p in gap_outcome.draft.unresolved_projections
                           if p["authority_status"] == "blocked"]
    check(bool(note_gap_projection), "附注缺口必须以缺口形式登记在 draft 里")
    note_gap_unresolved = [u for u in gap_outcome.unresolved
                           if u.unresolved_id in {p["unresolved_id"]
                                                  for p in note_gap_projection}]
    check(bool(note_gap_unresolved), "附注缺口必须产出 SectionUnresolved")
    check(any("note_extraction_not_implemented" in u.detail for u in note_gap_unresolved),
          "附注缺口 detail 必须写明权威 reason_codes")

    fin_authority_notes = _financial_authority(
        fin_task, fin_artifact,
        note_facts=_NoteSet(task_id=fin_task.task_id, facts=(
            _NoteFact(fact_id="nf-1", label="应收账款", display="12.34亿元",
                      evidence_id=NOTE_EV_ID, evidence_char_range=(100, 140)),)))
    note_scan = PW.scan_financial(fin_authority_notes, fin_task)
    note_entry = {e.fact_id: e for e in note_scan.facts}["nf-1"]
    check(note_entry.authority_kind == "evidence_note",
          "附注事实的支撑边必须是 evidence_note 权威")
    # locator 是 `loc-1` 的 `char_range`（半开字符区间，owner = evidence_id），**不是** note set
    # 容器 id —— 容器 id 在 `container_identity` 上单独表达，两者不得混成同一个字段。
    check(note_entry.locator_ref is not None
          and NS.locator_sort_key(note_entry.locator_ref)
          == NS.locator_sort_key(NS.char_range_locator(NOTE_EV_ID, 100, 140))
          and note_entry.container_identity == NS.note_container_id(
              fin_authority_notes.note_facts)
          and str(note_entry.container_identity) != str(note_entry.locator_ref["owner"]),
          f"附注支撑边必须带 (evidence_id) 上的 char_range locator 且与容器身份分离，实为 "
          f"{note_entry.locator_ref!r} / {note_entry.container_identity!r}")
    check(note_entry.material_id is None and note_entry.payload_ref is None,
          "附注支撑边不得携带 material_id / payload（跨类混装）")
    check(note_entry.fact_id == "nf-1"
          and note_entry.container_identity == NS.note_container_id(
              fin_authority_notes.note_facts),
          "附注事实的容器身份是 note set 容器（不是 pack 容器）")

    note_outcome = _write(
        fin_task, fin_authority_notes, section_id="financial",
        llm=_StubLlm(_plan(candidates=[
            _cand("c1", fin_entry["ff-short"].text, _fact_edge(note_scan, "ff-short")),
            _cand("c2", note_entry.text, _fact_edge(note_scan, "nf-1"))],
            draft_axis="fact",
            draft_ref=[_fact_ref(note_scan, "ff-short"), _fact_ref(note_scan, "nf-1")])))
    check(not note_outcome.gate_result.blocking,
          "artifact 事实与附注事实各自走路径 A 时必须过门")
    note_props = [p for p in note_outcome.draft.proposed_support_refs
                  if p.authority_kind == "evidence_note"]
    check(len(note_props) == 1 and note_props[0].note_fact_id == "nf-1"
          and not note_props[0].financial_fact_id and not note_props[0].material_id,
          "附注 proposal 的 fact 身份只走 `note_fact_id`（不跨 kind 冒充）")

    # §十五 5 / §六：换一份 note set（同一 task → 同一容器 id）后，同一 draft 的同一 fact_id
    # 指向的**另一个 Evidence 锚点**必须被硬门逐字比对拦住。
    other_note_set = _NoteSet(task_id=fin_task.task_id, facts=(
        _NoteFact(fact_id="nf-1", label="应收账款", display="99.99亿元",
                  evidence_id="ev-other-filing", evidence_char_range=(7, 47)),))
    other_authority = _financial_authority(fin_task, fin_artifact, note_facts=other_note_set)
    other_ids = {i.rule_id for i in NS.gate_draft(note_outcome.draft, other_authority).issues}
    check("support_locator_mismatch" in other_ids,
          f"同一 fact_id 的附注换成另一份 note set（另一 Evidence 锚点）后必须被硬门拒"
          f"（实际 {sorted(other_ids)}）")
    # 反例不是恒失败：换回本节自己的 note set，同一 draft 必须仍然过门。
    check(not NS.gate_draft(note_outcome.draft, fin_authority_notes).blocking,
          "换回本节权威 note set 时同一 draft 必须仍然过门")

    # artifact 缺口 → SectionUnresolved（事实级缺口也要如实呈现）
    gap_artifact = _Artifact(
        artifact_id="ffpa_gap", task_id=fin_task.task_id,
        facts=(_FinFact(fact_id="ff-short", label="短期偿债能力", display="1.20",
                        period=REPORT_AS_OF, unit="倍",
                        citation={"ref_type": "evidence", "evidence_id": EV_ID,
                                  "page_number": 71}),),
        gaps=({"fact_id": "ff-short", "label": "短期偿债能力", "formula_id": "short_solvency",
               "status": "blocked", "reason_code": "missing_statement"},))
    gap_authority = _financial_authority(fin_task, gap_artifact,
                                         note_gap=_NoteGap(task_id=fin_task.task_id))
    gap_scan = PW.scan_financial(gap_authority, fin_task)
    artifact_gap_outcome = _write(
        fin_task, gap_authority, section_id="financial",
        llm=_StubLlm(_plan(candidates=[
            _cand("c1", "2026-06-30的短期偿债能力为1.20。",
                  _fact_edge(gap_scan, "ff-short"))],
            draft_axis="fact", draft_ref=[_fact_ref(gap_scan, "ff-short")])))
    check(any(u.reason_code == "missing_statement" for u in artifact_gap_outcome.unresolved),
          "artifact 缺口的原因码必须逐字进入 SectionUnresolved")
    check(any("blocked" in u.detail for u in artifact_gap_outcome.unresolved),
          "artifact 缺口的状态必须逐字进入缺口叙述")

    # ==================================================================
    # 9. 失败即 fail-closed：非法提案不得降级放行，重试上限 0/1
    # ==================================================================
    expect_error(
        lambda: PW.WriterPolicy(max_llm_retries=2),
        PW.PackWriterError, "重试上限 2 次必须被拒（只允许 0/1）", needle="max_llm_retries")
    check(PW.WriterPolicy(max_llm_retries=1).max_llm_retries == 1, "重试上限 1 次合法")

    bad_payload = {"claim_candidates": [], "narrative_draft_units": [], "tool_calls": []}
    bad = _StubLlm(bad_payload, bad_payload)
    expect_error(
        lambda: _write(co_task, authority, section_id="company", llm=bad,
                       policy=PW.WriterPolicy(max_llm_retries=1)),
        PW.PackWriterError, "连续两次非法提案必须 fail-closed（不得降级放行）",
        needle="连续被拒")
    check(len(bad.calls) == 2, f"重试次数必须被策略限制，实为 {len(bad.calls)}")

    one_bad = _StubLlm(bad_payload, _plan(
        candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(scan, "f-1"))],
        units=[_unit("u1", "以下按业务条线说明公司经营结构.",
                     context=[_context_edge(container, "m-extra-1")])]))
    recovered = _write(co_task, authority, section_id="company", llm=one_bad,
                       policy=PW.WriterPolicy(max_llm_retries=1))
    check(recovered.llm_calls == 2, "允许一次重试时调用次数为 2")
    check(len(one_bad.calls) == 2
          and "上一次输出被拒" in one_bad.calls[1]["messages"][0]["content"],
          "重试时必须把上一次的拒绝原因反馈回去（不是盲重试）")
    expect_error(
        lambda: _write(co_task, authority, section_id="company", llm=None),
        PW.PackWriterError, "没有注入 NarrationClient 时不得写作", needle="不具备除生成之外")

    # 既无权威事实、又无材料时**不是**空写：只能以「仅缺口」形态存在，且不调用生成能力。
    gap_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-gap-only")
    gap_only = _write(
        gap_task,
        _company_authority(gap_task, facts=(), aspects=(
            _AspectResult(ASP_BUSINESS_MAIN, "blocked"),),
            requirements=(_Req(TOPIC_BUSINESS, (_aspect(
                ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business",
                status="blocked"),)),)),
        section_id="company", llm=None)
    check(gap_only.llm_calls == 0,
          "仅缺口章节不得调用生成能力（无事实可写，llm_client=None 也必须成立）")
    check(gap_only.draft.claim_candidates == ()
          and gap_only.draft.proposed_support_refs == ()
          and gap_only.draft.narrative_draft_units == (),
          "仅缺口章节不得凭空出现候选/单元/proposal")
    check(len(gap_only.draft.unresolved_ids) >= 1, "仅缺口章节必须至少登记一个显式缺口")
    check(all(p["authority_status"] == "blocked"
              for p in gap_only.draft.unresolved_projections),
          "仅缺口章节必须原样保留权威状态（blocked 不得被改写）")
    check(len(gap_only.draft.proposed_support_refs) == 0
          and all(d.usage == "not_used" for d in gap_only.draft.material_dispositions),
          "仅缺口章节里没有任何材料被引用（空清单位合法，且不得伪造 used）")

    # ---- 路径 A 为空 ≠ 只能交白卷（§三 B）----
    # 这里曾断言「facts 为空 ⇒ Claim/context 必须为空」。那个前提**是错的**：它把「路径 A 为空」
    # 当成了「没有可写之物」，于是把合法的路径 B 一起判成违规。正确口径必须分成两种状态：
    #   (a) **无事实、且无材料**：没有任何可读正文，无从提出候选 → 只能是「仅缺口」章节；
    #   (b) **无事实、但有真实材料正文**：路径 A 为空**不妨碍**合法的路径 B。
    # 「不得凭空产生候选」的「凭空」= 没有任何材料正文可读，而不是「没有权威事实」。
    blank_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-blank")
    blank_outcome = _write(blank_task, _company_authority(blank_task, facts=()),
                           section_id="company", llm=None)
    check(blank_outcome.llm_calls == 0
          and blank_outcome.draft.claim_candidates == ()
          and blank_outcome.draft.proposed_support_refs == ()
          and blank_outcome.draft.narrative_draft_units == (),
          "无事实**且无材料**时不得凭空产生候选/单元/proposal（没有任何正文可读）")
    # 这份权威什么都没声明（无事实、无 aspect、无 Contract 需求），因此缺口落在**问题覆盖**
    # 路径上：每个声明过的问题要么有 Claim 覆盖、要么有显式缺口。空章节必然带缺口出场。
    check(len(blank_outcome.draft.unresolved_projections) >= 1
          and all(u.state == "NOT_PROVIDED" and u.reason_code == "unresolved"
                  for u in blank_outcome.unresolved)
          and any("q-company_business" in u.detail for u in blank_outcome.unresolved),
          "无事实且无材料的章节必须以显式缺口出场（缺口守恒：不存在空白章节）")
    # 缺口文案的**读者面**（`pwr-4`）：标识保留、`key=` 语法不留。人读缺口一行只呈现
    # `[权威原始状态/状态/原因码] + detail`，id 若从 detail 里消失这条缺口就没有主语；
    # 而 `question_id=` / `topic=` / `reason_codes=['…']` 这类字段语法是内部 plumbing。
    _blank_texts = [u.detail for u in blank_outcome.unresolved]
    check(any("「q-company_business」" in t for t in _blank_texts)
          and all("question_id=" not in t and "topic=" not in t for t in _blank_texts),
          f"缺口文案必须点名问题（自然措辞）而不得夹带 `question_id=` / `topic=` 字段语法"
          f"（实为 {_blank_texts}）")

    # (b) 路径 A 为空、材料正文可读 ⇒ 合法的路径 B 必须**能**被提出。旧的「facts 为空 ⇒ Claim
    #     为空」假设恰好会禁止这一合法行为（也就把 P6 的缺陷固化成期望值）。
    pb_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-path-b")
    # 材料正文**逐字包含**下面那条候选（`srsc-2` 之后这一点成为前提）：候选
    # 「主营业务由动力电池与储能两大业务条线构成。」在旧夹具里是「…构成，销售以直销为主。」
    # 的**改写**（句中断成句末、逗号换句号），于是独立支撑结论判它 `unproven`、整节 fail-closed。
    # 那不是判据的缺陷：按 `srsc-2`，材料里没有这句就证不出来。夹具要模拟的是
    # 「材料里**有**这句话、Writer 把它写成一条候选」这一合法形态，因此把材料改成两句。
    pb_material = _material_of_identity(
        "m-path-b-1", "evidence:co-path-b-1",
        text="该公司主营业务由动力电池与储能两大业务条线构成。销售以直销为主。")
    pb_authority = _company_authority(pb_task, facts=(), materials=(pb_material,))
    pb_pack_id = str(pb_authority.pack_set.packs[0].pack_id)
    # 正向对照：**真正不含任何高风险表面的单原子业务描述**。
    pb_text = "主营业务由动力电池与储能两大业务条线构成。"
    check(NS.high_risk_surface_tokens(pb_text) == (),
          f"正向对照夹具本身必须不含任何高风险表面（实测 {NS.high_risk_surface_tokens(pb_text)}）")
    pb_outcome = _write(pb_task, pb_authority, section_id="company",
                        llm=_StubLlm(_plan(candidates=[
                            _cand("c1", pb_text, _material_edge(pb_pack_id, "m-path-b-1"))])))
    check(pb_outcome.llm_calls == 1,
          "路径 A 为空但有材料正文时必须调用生成能力（材料正文本身就是可写之物）")
    check(len(pb_outcome.draft.claim_candidates) == 1
          and not pb_outcome.gate_result.blocking,
          "路径 A 为空不妨碍合法的路径 B：有可读正文时应当提出非高风险描述性候选")
    pb_props = tuple(p for p in pb_outcome.draft.proposed_support_refs
                     if p.binding_subject_kind == "claim_candidate")
    check(len(pb_props) == 1
          and all(p.authorization_path == "path_b_material_derived"
                  and p.material_id == "m-path-b-1" and p.fact_id is None
                  for p in pb_props),
          "路径 B 支撑边必须恰好绑到那份真实材料上，且不带任何事实身份")
    check([d.usage for d in pb_outcome.draft.material_dispositions] == ["used"],
          "被路径 B 引用的材料必须登记为 used（不得既被引用又写成 not_used）")
    check(not pb_outcome.gate_result.issues_of("blocking"),
          "不含任何高风险表面的单原子业务描述必须能走通路径 B（正向分支不得被误拒）")

    # ---- §二 / P0 反例：高风险硬事实不得由材料派生路径引入（`pw-5` / `cbg-2`）----
    # 判据是**命中即拒**，与「材料正文里逐字有没有」无关。下面**每一类**都单独构造一份材料，
    # 且材料正文**逐字包含**该高风险字面成分——也就是说，旧的「逐字在场 ⇒ 授权」口径会把它们
    # 全部放行。这不是一个数字反例换皮重复四次：四类分别落在否定、主体身份、状态标记与表格关系上，
    # 命中的是 `high_risk_surface_tokens` 里四个**不同的**封闭集合。
    pb_high_risk_cases = (
        ("negation", "显式否定", "未发生", "经营环境未发生重大变化。"),
        ("entity", "法人主体身份", "示例产业集团", "示例产业集团从事动力电池制造。"),
        ("checkbox", "勾选 / 适用状态", "不适用", "该事项不适用。"),
        ("table_relation", "表格行列关系", "占比", "主营业务收入中动力电池占比较高。"),
    )
    pb_forbidden_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-path-b-forbidden")
    for slug, label, surface, cand_text in pb_high_risk_cases:
        case_id = f"m-path-b-forbidden-{slug}"
        case_text = f"{cand_text}材料原文另述其经营与产品情况。"
        case_material = _material_of_identity(
            case_id, f"evidence:co-path-b-forbidden-{slug}",
            page=12, text=case_text)
        case_authority = _company_authority(pb_forbidden_task, facts=(),
                                            materials=(case_material,))
        case_pack_id = str(case_authority.pack_set.packs[0].pack_id)
        check(surface in case_text,
              f"反例前置条件：材料正文必须**逐字**包含「{surface}」（{label}）")
        case_llm = _StubLlm(_plan(candidates=[
            _cand("c1", cand_text, _material_edge(case_pack_id, case_id))]))
        expect_error(
            lambda case_llm=case_llm, case_authority=case_authority: _write(
                pb_forbidden_task, case_authority, section_id="company", llm=case_llm),
            PW.PackWriterError,
            f"路径 B 不得引入{label}类高风险硬事实——材料正文里逐字存在也不等于已资格化",
            needle="路径 B 候选携带了高风险表面")
        check(len(case_llm.calls) == 1,
              f"{label}类反例必须在写入侧预验证就被拒（不得进入后续门）")

    # ==================================================================
    # 9b. §二 3 / §二 2.6：整束被拒的 typed 审计 ＋ 被拒各轮的补件诉求留存
    # ==================================================================
    # 这一块钉住真实 run 里被点名丢失的两件事：（a）整束被拒后「哪些候选被拒、为什么」无从
    # 回查；（b）模型在**被拒那一轮**提出的补件诉求随候选一起蒸发（产物里只剩一个计数）。
    _pw_src = Path(PW.__file__).read_text(encoding="utf-8")
    _kinds_used = re.findall(r'_reject\(\s*"([a-z_]+)"', _pw_src)
    # 集合相等 = 「每个出口的原因码都已登记」且「每个已登记的原因码真有出口在用」。计数不强求
    # 相等：同一个 kind 可以有**两个**出口（硬门 blocking 与返修重试都是 `narrative_gate_blocking`）。
    check(set(_kinds_used) == set(PW.PROPOSAL_SET_REJECTION_KINDS),
          f"每个拒绝出口的原因码都必须已登记，且已登记的原因码不得是死的："
          f"出口 {sorted(set(_kinds_used))} vs 登记 {sorted(PW.PROPOSAL_SET_REJECTION_KINDS)}")
    check("run_follow_up_needs(" not in _pw_src,
          "写入侧**不得**具备裁决/执行补件的能力：待裁决诉求只留存、不执行（越权执行等于绕开"
          "Harness 的 Contract/预算/SourcePolicy 裁决）")
    expect_error(
        lambda: PW.ProposalSetRejectionRecord(
            attempt=1, rejection_kind="whatever", rejection_detail="x"),
        PW.PackWriterError, "未登记的拒绝 kind 必须被拒（审计的原因码是封闭集合）",
        needle="不在")

    # 正向对照（C4 起）：一批的返回形状不合法 ⇒ **只重问那一批**（`bsc-1`），本轮照常成形。
    # 关键不变式仍是「被拒过的返回不得被追溯赦免」：它连同 call_id / 错误原文留痕在**报告侧**
    # 的 `shape_corrections` 里，只是不再冒充「整束被拒」——被拒的是那一批的返回。
    _rounds = recovered.sidecar()["writer_batch_audit"]["rounds"]
    _corr = [c for r in _rounds for c in r["shape_corrections"]]
    check([c["rejection_kind"] for c in _corr] == ["schema_invalid"]
          and _corr[0]["rejected_call_id"] == "call-1"
          and _corr[0]["correction"]["call_id"] == "call-2"
          and _corr[0]["correction"]["outcome"] == "accepted"
          and _corr[0]["note_version"] == PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION,
          f"被拒的那一次返回必须逐条留痕（call_id / 错误原文 / 纠正那一次的去向），"
          f"纠正成功也不得把它抹掉（实测 {_corr}）")
    check(recovered.rejections == ()
          and recovered.draft.writer_attempt == 1
          and len(_rounds) == 1,
          f"被拒的是**某一批的返回**且已就地纠正 ⇒ 整束从未被拒：不得为它编一条整束拒绝，"
          f"也不得整轮重跑（实测 rejections={[r.to_dict() for r in recovered.rejections]}、"
          f"writer_attempt={recovered.draft.writer_attempt}）")
    check(json.loads(json.dumps(
        recovered.sidecar()["writer_batch_audit"], ensure_ascii=False))["rounds"][0][
            "shape_corrections"][0]["rejected_call_id"] == "call-1",
          "纠正文书的审计必须随产物 sidecar 一起落盘（可序列化、可回查）")

    # 逐条尽力抽取：这一路只在「连结构校验都没过」时用，取不出就如实为空、绝不猜。
    check(PW._best_effort_follow_up_specs("不是 JSON") == ()
          and PW._best_effort_follow_up_specs(json.dumps({"follow_up_needs": "x"})) == ()
          and PW._best_effort_follow_up_specs(json.dumps([{"statement": "x"}])) == (),
          "取不到 `follow_up_needs` 数组时如实为空（不得凭别的字段猜一条诉求出来）")
    _fu_probe = _follow_up("需要补充公司销售模式的口径说明。",
                           target_requirement_id="tr-x", topic_id=TOPIC_BUSINESS,
                           question_id="q-company_business", aspect_id=ASP_BUSINESS_SALES)
    check(PW._best_effort_follow_up_specs(
        json.dumps({"follow_up_needs": [_fu_probe, "不是对象", 3]}, ensure_ascii=False))
        == (PW._jsonable(dict(_fu_probe)),),
        "尽力抽取只取 Mapping 条目、逐键原样（不修正、不补齐、不掺入非对象项）")

    req_id_business = str(scan.requirement_ids.get(TOPIC_BUSINESS, ""))
    check(bool(req_id_business), "本节权威必须给出真实需求 id（否则无法构造可类型化的诉求）")
    forged_edge = _fact_edge(scan, "f-1")
    forged_edge["fact_id"] = "f-not-in-authority"
    fu_ok = _follow_up("需要补充公司销售模式的口径说明。",
                       target_requirement_id=req_id_business, topic_id=TOPIC_BUSINESS,
                       question_id="q-company_business", aspect_id=ASP_BUSINESS_SALES)
    fu_bad = _follow_up("需要补充一个不属于本节权威主题的材料。",
                        target_requirement_id=req_id_business, topic_id="topic-forged",
                        question_id="q-company_business", aspect_id=ASP_BUSINESS_SALES)
    # 束的**结构合法**（parse 通过），只有支撑边声明的身份在权威侧闭不上 —— 这是一条独立的
    # 拒绝路径，必须有自己的 typed 审计，不能并进 `schema_invalid`（两者的修法完全不同）。
    bad_identity = _plan(candidates=[_cand("c1", facts_by_id["f-1"].text, forged_edge)],
                         follow_ups=[fu_ok, fu_bad])
    id_llm = _StubLlm(bad_identity, bad_identity)
    try:
        _write(co_task, authority, section_id="company", llm=id_llm,
               policy=PW.WriterPolicy(max_llm_retries=1))
        check(False, "支撑边身份在权威侧闭不上的束必须 fail-closed（不得降级放行）")
    except PW.ProposalSetRejectedError as exc:
        recs = [r.to_dict() for r in exc.rejections]
        check([r["rejection_kind"] for r in recs]
              == ["proposal_identity_unresolvable"] * 2,
              f"结构合法但身份闭不上的束必须记成**自己的** kind（不是 schema_invalid），"
              f"实为 {[r['rejection_kind'] for r in recs]}")
        check([r["attempt"] for r in recs] == [1, 2],
              "被拒束必须按发生顺序逐轮留存（轮次不是装饰）")
        check(all(r["candidate_ids"] == ["c1"] and r["proposal_ids"] == []
                  and r["draft_unit_ids"] == [] and r["follow_up_count"] == 2
                  and r["narration_call_id"] for r in recs),
              f"此时没有可信的束身份：候选标签可尽力抽取，派生 id（proposal_id / draft_unit_id）"
              f"**不得**伪造，实为 {recs}")
        # (c4) 这里是最锋利的一条：候选标签**能**抽出来（`["c1"]`），但逐候选归属仍必须为空——
        # 这一束的结构校验**过不去**，没有任何可信的候选身份可挂原因。把「尽力抽取的标签」
        # 当成「逐候选审计的键」就是拿猜测冒充审计。
        check(all(r["candidate_audit"] == [] and r["named_subsections"] == []
                  and r["whole_set_rejected"] is True for r in recs),
              f"整束未成为结构化对象时逐候选归属必须为空（身份由尽力抽取的标签 + kind 自己说明），"
              f"实为 {[(r['rejection_kind'], r['candidate_audit']) for r in recs]}")
        # 守恒分两种情形，别混用一个式子：**整束终止**时每一次调用都被拒（束数 == 调用数）；
        # **成功**时账是「被拒束数 + 1 == 调用数」（多出来的那一次正是被采信的那一束）。
        check(len(recs) == len(id_llm.calls) == 2,
              f"整束终止时被拒束数必须逐次等于生成调用数（实为 {len(recs)} vs {len(id_llm.calls)}）")
        check(len(exc.follow_up_needs) == 2 and len(exc.follow_up_untypeable) == 2,
              f"被拒各轮的诉求必须随异常带出（两轮各 1 条可类型化 + 1 条不成立），实为 "
              f"{len(exc.follow_up_needs)} / {len(exc.follow_up_untypeable)}")
        check(all(str(n.aspect_id) == ASP_BUSINESS_SALES for n in exc.follow_up_needs),
              "可类型化的诉求必须指回真实的 Contract aspect（不得凭字符串猜一个需求）")
        check(all(u["attempt"] in (1, 2) and u["reason"] and u["topic_id"] == "topic-forged"
                  for u in exc.follow_up_untypeable),
              f"不成立的诉求同样原样留存（带轮次与不成立原因）：「诉求不成立」≠「没有诉求」，"
              f"实为 {exc.follow_up_untypeable}")
        check("待裁决提议" in str(exc),
              "终止消息必须把诉求的性质说清（待裁决提议 ≠ 已执行补件 ≠ 缺口）")
    else:
        pass

    # §二 3 的核心反例（被裁决点名的反模式）：一束里**同时**有一条能闭上的支撑边和一条闭不上的。
    # 正确行为是整束要么全过、要么全拒；**不得**把能闭上的那几个留下来、只把闭不上的丢掉，再把
    # 剩下的当成「原提案集」送聚合绑定门。被拒时审计必须列**全**该束的候选——含本可单独通过的那些。
    mixed = _plan(candidates=[_cand("c1", facts_by_id["f-1"].text, forged_edge),
                              _cand("c2", facts_by_id["f-2"].text, _fact_edge(scan, "f-2"))])
    mixed_llm = _StubLlm(mixed, mixed)
    try:
        _write(co_task, authority, section_id="company", llm=mixed_llm,
               policy=PW.WriterPolicy(max_llm_retries=1))
        check(False, "同一束里只要有一条支撑边闭不上，整束必须被拒（不得裁剪后放行）")
    except PW.ProposalSetRejectedError as exc:
        recs = [r.to_dict() for r in exc.rejections]
        check(len(recs) == 2
              and all(r["rejection_kind"] == "proposal_identity_unresolvable" for r in recs),
              f"混合束必须整束拒绝且原因码一致（实为 "
              f"{[r['rejection_kind'] for r in recs]}）")
        check(all(r["candidate_ids"] == ["c1", "c2"] for r in recs),
              f"被拒束的审计必须列**全且按原序**该束的候选——不得裁剪成只剩出问题的那一个，"
              f"也不得只留计数（实为 {[r['candidate_ids'] for r in recs]}）")
        check(len(recs) == len(mixed_llm.calls),
              "整束终止时每一次生成调用都恰好对应一条被拒审计（少一条即有一次尝试"
              "既没被采信、也没留下痕迹）")
        check(all(r["narration_call_id"] for r in recs),
              "每条被拒审计都必须指回本次生成调用（原始输出可按该 id 逐字回查）")
    else:
        pass

    # 连结构校验都没过的一束里，`follow_up_needs` 原文同样必须被取出来（同一段 JSON 里）。
    bad_schema = {"claim_candidates": [], "narrative_draft_units": [],
                  "tool_calls": [], "follow_up_needs": [fu_ok]}
    schema_llm = _StubLlm(bad_schema, bad_schema)
    try:
        _write(co_task, authority, section_id="company", llm=schema_llm,
               policy=PW.WriterPolicy(max_llm_retries=1))
        check(False, "顶层键集不符的输出必须 fail-closed")
    except PW.ProposalSetRejectedError as exc:
        # C4 起：同一批的第一次形状失败会按 `bsc-1` 对**那一批**纠正一次（附注重问），
        # 纠正那一次再失败才落成 typed 拒绝——因此这里是**一条**被拒审计，而不是两条。
        check([r.rejection_kind for r in exc.rejections] == ["schema_invalid"],
              "连结构校验都没过的束仍然是自己的 kind（不得与被权威侧拒绝混为一类）")
        check(len(exc.follow_up_needs) == 1 and not exc.follow_up_untypeable,
              f"这一束连结构都没过，但它提过的诉求仍必须被取出来（实为 "
              f"{len(exc.follow_up_needs)} / {len(exc.follow_up_untypeable)}）")
        # C4 起这条断言读的是**同一轮**里两次调用（原调用 + 纠正那次）加起来说的诉求：两次逐字
        # 相同 ⇒ 同一轮里的**同一条**诉求，取出来是**一条**。留成两条会让「模型提出了几条诉求」
        # 随我们的重问次数变化——记录的是我们的调用次数，而不是模型提过什么。
    else:
        pass

    # 反例不是恒有：原始输出里根本没有诉求时，两个桶都必须如实为空（不得凭空造一条）。
    no_spec = {"claim_candidates": [], "narrative_draft_units": [], "tool_calls": []}
    no_spec_llm = _StubLlm(no_spec, no_spec)
    try:
        _write(co_task, authority, section_id="company", llm=no_spec_llm,
               policy=PW.WriterPolicy(max_llm_retries=1))
        check(False, "顶层键集不符的输出必须 fail-closed")
    except PW.ProposalSetRejectedError as exc:
        check(not exc.follow_up_needs and not exc.follow_up_untypeable,
              "原始输出里没有诉求时必须如实为空——不得把「没有诉求」写成一条诉求")
    else:
        pass

    # ==================================================================
    # 9c. §四 身份错配（r4 现场形态）：材料**确实在本次精确清单里**，但被声明在**别的容器**下
    # ==================================================================
    # r4 的 company 节横跨三份 Pack（identity / business / legal_risks）。现场有一束把
    # `mat-tm-b6bc…`（**属于** `company_business` 容器）声明到 `company_legal_risks` 容器下。
    # 本节钉住四件事，缺一件都会让「拒绝」被误读：
    #   (a) 这是**身份错配**，不是「材料不存在」——所以必须先证明该材料就在清单里（在它自己的
    #       容器下），且**不存在于**被错绑的那个容器下；
    #   (b) 拒绝详情必须同时点名**声明的容器**与**材料**，读者据此才能看出错在哪；
    #   (c) **不得**按 material_id「就近重绑」到正确容器后放行（那等于替模型改身份）；
    #   (d) 同一份材料改回自己的容器后**必须**通过——证明被拒的是声明的身份，不是这份材料。
    cm_task = _task("company", (TOPIC_BUSINESS, TOPIC_LEGAL), task_id="task-co-container")
    cm_biz_material = _material_of_identity(
        "m-biz-1", "evidence:co-container-biz",
        text="该公司主营业务由动力电池与储能两大业务条线构成。销售以直销为主。")
    cm_legal_material = _material_of_identity(
        "m-legal-1", "evidence:co-container-legal",
        text="公司及其子公司报告期内涉及若干诉讼与仲裁事项，相关进展已按披露要求说明。")
    cm_pack_set = _pack_set(
        cm_task,
        packs=(_Pack(topic_id=TOPIC_BUSINESS, materials=(cm_biz_material,),
                     aspect_results=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),)),
              _Pack(topic_id=TOPIC_LEGAL, materials=(cm_legal_material,))),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)),)),))
    cm_authority = PW.TopicPackAuthorityInput.create(
        cm_task, cm_pack_set, company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    cm_by_topic = {str(p.topic_id): str(p.pack_id) for p in cm_authority.pack_set.packs}
    cm_biz_container = cm_by_topic[TOPIC_BUSINESS]
    cm_legal_container = cm_by_topic[TOPIC_LEGAL]
    check(cm_biz_container != cm_legal_container,
          "前置条件：两个 topic 的容器身份必须不同（否则本反例退化成同容器同名）")
    cm_manifest = PW._derive_material_manifest(
        cm_authority, material_context=_writer_material_context(
            cm_authority.pack_set, task_id=str(cm_task.task_id), section_id="company"))
    check(cm_manifest.entry_for(
              NS.manifest_member_ref(cm_biz_container, "m-biz-1")) is not None
          and cm_manifest.entry_for(
              NS.manifest_member_ref(cm_legal_container, "m-biz-1")) is None,
          "前置条件：m-biz-1 **确实**在本节精确清单里（在它自己的容器下）、"
          "且**不存在于**被错绑的那个容器下——这样反例才落在「身份错配」而不是「材料缺失」上")
    cm_text = "主营业务由动力电池与储能两大业务条线构成。"
    check(NS.high_risk_surface_tokens(cm_text) == (),
          f"本反例的候选文本必须不含任何高风险表面（实测 "
          f"{NS.high_risk_surface_tokens(cm_text)}），否则先撞上的是另一条门")
    cm_bad_plan = _plan(candidates=[
        _cand("c62", cm_text, _material_edge(cm_legal_container, "m-biz-1"))])
    cm_llm = _StubLlm(cm_bad_plan, cm_bad_plan)
    try:
        _write(cm_task, cm_authority, section_id="company", llm=cm_llm,
               policy=PW.WriterPolicy(max_llm_retries=1))
        check(False, "把本节材料声明到**别的容器**下必须整束被拒（不得按 material_id 就近重绑）")
    except PW.ProposalSetRejectedError as exc:
        rec = exc.rejections[0]
        check(rec.rejection_kind == "proposal_identity_unresolvable",
              f"跨容器错绑必须是 typed 身份拒绝（实测 {rec.rejection_kind!r}）")
        check(cm_legal_container in rec.rejection_detail
              and "m-biz-1" in rec.rejection_detail,
              f"拒绝详情必须同时点名**声明的容器**与**材料**（实测 "
              f"{rec.rejection_detail[:160]!r}）——否则读者会把身份错配读成「材料不存在」")
        check("不得被裁剪后重试" in rec.rejection_detail
              and "不得降级放行" in rec.rejection_detail,
              "被拒的一束不得被裁剪后重试、也不得降级放行（非法身份不因重试而合法）")
        check(len(cm_llm.calls) == 2,
              f"身份闭不上时只允许既有上限内的重试（max_llm_retries=1 ⇒ 2 次调用，实测 "
              f"{len(cm_llm.calls)}）——不得为了重试而反复采信身份不合法的原束")
    else:
        pass
    # 正向对照：同一份材料、同一段文本，只是**声明回它自己的容器** ⇒ 必须通过。
    cm_ok = _write(cm_task, cm_authority, section_id="company",
                   llm=_StubLlm(_plan(candidates=[
                       _cand("c1", cm_text, _material_edge(cm_biz_container, "m-biz-1"))])))
    check(len(cm_ok.draft.claim_candidates) == 1 and not cm_ok.gate_result.blocking
          and [p.material_id for p in cm_ok.draft.proposed_support_refs] == ["m-biz-1"],
          "同一份材料改回它自己的容器后必须通过——被拒的是**声明的身份**，不是这份材料")

    # ==================================================================
    # 10. 身份错配：Contract / task / section / WritingSpec / 依赖指纹
    # ==================================================================
    wrong_contract = PW.ContractProjection.create(
        spec, section_id="company", contract_version="v3",
        contract_fingerprint=CONTRACT_FINGERPRINT)
    expect_error(
        lambda: _write(co_task, authority, section_id="company", projection=wrong_contract,
                       llm=_StubLlm(_plan())),
        PW.PackWriterError, "Contract 身份不一致必须被拒", needle="不得跨 Contract 写作")
    other_spec = dataclasses.replace(spec, writing_spec_id="other_spec_v9")
    expect_error(
        lambda: _write(co_task, authority, section_id="company", writing_spec=other_spec,
                       llm=_StubLlm(_plan())),
        PW.PackWriterError, "ContractProjection 与传入 WritingSpec 不一致必须被拒",
        needle="与传入 WritingSpec 不一致")
    other_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-other")
    expect_error(
        lambda: _write(other_task, authority, section_id="company", llm=_StubLlm(_plan())),
        PW.PackWriterError, "权威输入不属于本任务必须被拒", needle="不属于本任务")
    # §16.7.1：依赖指纹是结构必需的非空字段 —— 空值不是「缺省」，不得就地编造。
    expect_error(
        lambda: _write(co_task, authority, section_id="company", llm=_StubLlm(_plan()),
                       dependency_fingerprint="   "),
        PW.PackWriterError, "依赖指纹为空时必须 fail-closed（不得就地编造一个指纹）",
        needle="不得就地编造")

    # ==================================================================
    # 11. 非样本公司同规则 + 内容寻址稳定 + 缺口叙述的数字边界
    # ==================================================================
    other_company_id = "另一家非样本公司"
    other_co_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-otherco")
    other_authority = PW.TopicPackAuthorityInput.create(
        other_co_task,
        _pack_set(other_co_task, packs=(_Pack(topic_id=TOPIC_BUSINESS,
                                              company_id=other_company_id),)),
        company_id=other_company_id, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    check(other_authority.company_id == other_company_id,
          "非样本公司走完全相同的构造与校验路径")

    stable = PW.TopicPackAuthorityInput.create(
        co_task, good_set, company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    check(stable.input_id.startswith(PW.AUTHORITY_INPUT_ID_PREFIX),
          "权威输入 id 是内容寻址的")
    check(stable.input_id == PW.TopicPackAuthorityInput.create(
        co_task, good_set, company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION,
        contract_fingerprint=CONTRACT_FINGERPRINT).input_id,
        "同内容必须得到同一权威输入 id（可复现）")

    check(PW._digits_free("fact f_300750_1 状态 blocked", "t", "f_300750_1") ==
          "fact f_300750_1 状态 blocked",
          "身份标识里的数字必须被允许（否则真实 run 会被误拒）")
    expect_error(
        lambda: PW._digits_free("公司收入 45.3 亿元", "缺口"),
        PW.PackWriterError, "缺口叙述夹带未绑定数字必须被拒", needle="未经绑定的数字")

    # aspect 要求里 blocking_policy / impact_scope 是 tuple，不得因 str() 而丢失
    check(PW._blocking_effects(("SECTION_BLOCKED",)) == ("SECTION_BLOCKED",),
          "blocking_policy 是 tuple 时必须被正确解析（不得 str() 掉）")
    check(PW._blocking_effects(("NONE",)) == (), "NONE 不构成阻塞")
    check(SS.normalize_blocking_effects(("NONE", "SECTION_BLOCKED", "SHUTDOWN", "NONE"))
          == ("SECTION_BLOCKED",),
          "阻断后果规范化：NONE 哨兵落空、保序去重、越界等级剔除")
    check(PW._blocking_effects(("SECTION_BLOCKED", "NONE")) ==
          SS.normalize_blocking_effects(("SECTION_BLOCKED", "NONE")),
          "写作器必须复用 schema 层的规范化（唯一一套，不得各写一份）")
    check(PW._impact_scopes_of(("subject", "bogus")) == ("subject",),
          "impact_scope 只取 canonical 词表内的值")
    tuple_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-tuple",
                       questions=(PS.PlannedQuestion(
                           question_id="q-company_business", question="主营业务？",
                           priority="required", topic_id=TOPIC_BUSINESS,
                           impact_scope=("subject",)),))
    check(PW._question_blocking_policy(tuple_task) == {"q-company_business": ()},
          "PlannedQuestion 的策略按 question_id 索引（不按 topic 猜）")
    for forbidden in ("_impact_scopes", "parse_narration_plan", "_amount_sentence"):
        check(not hasattr(PW, forbidden),
              f"narr-3 的 `{forbidden}` 必须已删除（不得留下第二套正文组织/数字通路）")

    # ==================================================================
    # 11b. §三 A：材料正文上下文（wmctx-1）的篡改面 —— typed fail-closed，不得降级
    # ==================================================================
    # §四 的反例清单要求「resolver 被移除 / payload 缺失 / 字节·哈希·locator 被篡改」。
    # 正文链有**两段**，必须各自有反例，不能只证一段：
    #   (i)  从 resolver 解析出正文（`resolve_writer_material_context`）；
    #   (ii) 下游按 manifest 成员取回正文（`reading_for_manifest_member` —— 机械门与语义门
    #        共用的**唯一**入口）。
    # 两段断言同一件事：任何一处不一致都**不得**退化成「只有 material ID 的写作」。
    # 判据是**typed 原因码**（`MaterialContextError.reason`），不是消息里的措辞：
    # 原因码是封闭词表里的一个取值，消息文本随时可以改。
    def expect_context_error(fn, reason: str, msg: str) -> None:
        nonlocal passed, failed
        try:
            fn()
        except MC.MaterialContextError as e:
            if str(getattr(e, "reason", "")) != reason:
                failed += 1
                details.append(
                    f"FAIL {msg}：原因码不符（期望 {reason!r}，得到 {getattr(e, 'reason', None)!r}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    ctx_ok = MC.resolve_writer_material_context(
        pack_set=authority.pack_set, resolver=_payload_resolver(),
        task_id=str(co_task.task_id), section_id="company")
    check(ctx_ok.materials and all(m.reading_view.strip() for m in ctx_ok.materials),
          "正向对照：合法包集上每个成员都解析出**非空正文**（否则下面的反例没有对照面）")
    member_entry = draft.material_manifest.entries[0]
    check(MC.reading_for_manifest_member(material_context=ctx_ok, member=member_entry)
          is not None,
          "正向对照：未被改动的 manifest 成员能从同一上下文取回正文")

    # (i-a) payload 缺失：resolver 拿不到该 payload（真实链上 = 该 BLOB 不在库里）。
    expect_context_error(
        lambda: MC.resolve_writer_material_context(
            pack_set=authority.pack_set, resolver=_StubPayloadResolver({}),
            task_id=str(co_task.task_id), section_id="company"),
        "payload_ref_unverifiable",
        "payload 缺失必须 typed fail-closed（不得降级成「只有 ID」）")
    # (i-b) 字节被换：payload_ref 原样、字节不同 ⇒ 重算哈希不符。
    victim = ctx_ok.materials[0]
    swapped_bytes = dict(_PAYLOAD_BYTES)
    swapped_bytes[str(victim.payload_hash)] = b'{"material_payload_version": 1}'
    expect_context_error(
        lambda: MC.resolve_writer_material_context(
            pack_set=authority.pack_set, resolver=_StubPayloadResolver(swapped_bytes),
            task_id=str(co_task.task_id), section_id="company"),
        "payload_ref_unverifiable", "payload 字节被换（哈希不符）必须 typed fail-closed")
    # (i-c) resolver 缺席：没有解析器就没有正文，入口不得猜、不得降级。
    expect_context_error(
        lambda: MC.resolve_writer_material_context(
            pack_set=authority.pack_set, resolver=None,
            task_id=str(co_task.task_id), section_id="company"),
        "resolver_missing", "缺 resolver 时材料正文入口必须 typed fail-closed")

    # (ii-a) 成员被改：payload 哈希 / 读视图指纹 / locator 三条各自的逐字比对。
    for field_name, patched in (
            ("payload_hash", "d" * 64),
            ("reading_view_fingerprint", "e" * 64),
            ("locator_ref", NS.char_range_locator("别的容器", 0, 1))):
        tampered_member = dataclasses.replace(member_entry, **{field_name: patched})
        expect_context_error(
            lambda m=tampered_member: MC.reading_for_manifest_member(
                material_context=ctx_ok, member=m),
            "member_payload_mismatch",
            f"manifest 成员的 {field_name} 与已解析正文不符必须被拒（不得借另一份正文通过）")
    # (ii-b) 成员根本不在上下文里（「清单里有 ID」≠「正文被解析过」）。
    foreign_member = dataclasses.replace(
        member_entry, pack_id="pack-foreign",
        member_ref=NS.manifest_member_ref("pack-foreign", member_entry.material_id))
    expect_context_error(
        lambda: MC.reading_for_manifest_member(
            material_context=ctx_ok, member=foreign_member),
        "member_not_in_context", "不在上下文里的 manifest 成员必须被拒")

    # 写作入口的两条同纪律反例：上下文缺席 / 上下文来自**别的** PackSet。
    expect_error(
        lambda: PW.write_section(
            co_task, authority, projection=projections["company"], writing_spec=spec,
            presentation_profile=profile, llm_client=_StubLlm(_plan()),
            dependency_fingerprint=DEPENDENCY_FINGERPRINT, material_context=None),
        PW.PackWriterError,
        "topic 权威缺材料正文上下文必须 fail-closed（不得只凭 material ID 写作）",
        needle="只拿到 material ID")
    foreign_ctx = _writer_material_context(
        other_authority.pack_set, task_id=str(other_co_task.task_id), section_id="company")
    expect_error(
        lambda: _write(co_task, authority, section_id="company", llm=_StubLlm(_plan()),
                       material_context=foreign_ctx),
        PW.PackWriterError, "别的 PackSet 的正文上下文不得带进本节写作",
        needle="pack_set_fingerprint")

    # ==================================================================
    # 12. 期间闭合（§十二 1/3/4）：没有显式期间的权威事实不得进入候选可利用集
    # ==================================================================
    np_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-noperiod")
    np_authority = _company_authority(
        np_task,
        facts=(_fact("f-dated", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,)),
               _fact("f-undated", "公司销售模式以直销为主。", (ASP_BUSINESS_SALES,),
                     period="")),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                 _AspectResult(ASP_BUSINESS_SALES, "covered")),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),
            _aspect(ASP_BUSINESS_SALES, TOPIC_BUSINESS, "q-company_business"))),))
    np_scan = PW.scan_topic_pack(np_authority, np_task)
    check([e.fact_id for e in np_scan.facts] == ["f-dated"],
          f"没有显式期间的权威事实不得进入候选可利用集（期间不得由基准期顶替），"
          f"实为 {[e.fact_id for e in np_scan.facts]}")
    check([t[1] for t in np_scan.period_unresolved] == ["f-undated"],
          "无期间事实必须进入 period_unresolved（缺口如实登记，不得静默丢弃）")
    np_outcome = _write(np_task, np_authority, section_id="company",
                        llm=_StubLlm(_plan(candidates=[
                            _cand("c1", np_scan.facts[0].text, _fact_edge(np_scan, "f-dated"))])))
    period_gaps = [u for u in np_outcome.unresolved if u.reason_code == "period_unresolved"]
    check(bool(period_gaps), "period_unresolved 缺口必须以入口 reason_code 出现")
    # 缺口叙述里**必须**出现「报告期」——但那只能出现在**禁止性**从句里（「不得用……这类
    # 未定义指代替代」），绝不可是把基准日/「报告期」当成该事实自己的期间。这两种用法只能
    # 靠「基准日期字面量不得出现」+「禁止性措辞必须同时出现」两条一起钉住。
    check(period_gaps
          and all(REPORT_AS_OF not in u.detail for u in period_gaps)
          and all("不得用本节基准日" in u.detail for u in period_gaps)
          and all(u.target_fact_ids == ("f-undated",) if hasattr(u, "target_fact_ids")
                  else True for u in period_gaps),
          "期间未闭合的缺口叙述不得把 report_as_of 当成该事实自己的期间（且必须显式写明"
          "不得以基准日或「报告期」替代）")
    check(period_gaps and all(u.state == "NOT_PROVIDED" and u.reason_code == "period_unresolved"
                              for u in period_gaps),
          "期间未闭合的缺口必须如实落在 NOT_PROVIDED，且原因码逐字保留权威侧取值")
    check(all(u["authority_status"] == PW.PERIOD_UNRESOLVED_AUTHORITY_STATUS
              for u in np_outcome.draft.unresolved_projections
              if u["unresolved_id"] in {g.unresolved_id for g in period_gaps}),
          "期间缺口的权威状态必须逐字来自权威侧（period_unresolved），不得重标")

    # (b) 权威文本自带未定义期间措辞（「报告期」）→ 候选文本照抄即被硬门拒（§十二 4）。
    vague_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-vague")
    vague_authority = _company_authority(
        vague_task,
        facts=(_fact("f-vague", "公司报告期未发生重大诉讼、仲裁事项。",
                     (ASP_BUSINESS_MAIN,)),),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),)),))
    vague_entry = PW.scan_topic_pack(vague_authority, vague_task).facts[0]
    expect_error(
        lambda: _write(vague_task, vague_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=[
                           _cand("c1", vague_entry.text, _fact_edge(
                               PW.scan_topic_pack(vague_authority, vague_task), "f-vague"))]))),
        PW.PackWriterError, "含未定义期间措辞「报告期」的候选文本必须被硬门拒绝",
        needle="narrative_vague_period")

    # (c) 结构性/描述性事实（time_scope 结构性）没有期间时必须照常可用。
    struct_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-structural")
    struct_authority = _company_authority(
        struct_task,
        facts=(_fact("f-struct", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,), period=""),),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business",
                    kind="fact_set", time_scope="STRUCTURAL_5Y_CURRENT"),)),))
    struct_scan = PW.scan_topic_pack(struct_authority, struct_task)
    check([e.fact_id for e in struct_scan.facts] == ["f-struct"],
          "结构性描述事实没有期间时必须照常可用（不得一刀切排除）")
    check(not struct_scan.period_unresolved,
          "结构性事实不得进入 period_unresolved（期间对它不是必须的）")
    struct_outcome = _write(struct_task, struct_authority, section_id="company",
                            llm=_StubLlm(_plan(candidates=[
                                _cand("c1", struct_scan.facts[0].text,
                                      _fact_edge(struct_scan, "f-struct"))])))
    check(not struct_outcome.gate_result.blocking,
          "结构性事实必须能成为候选（不得因期间为空被硬门挡）")

    # (d) 时段量/时点量（指标类 / 事件类 / 带 ValueIdentity 的余额）没有显式期间时必须逐条排除。
    undated_cases = (
        ("指标类", "financial_metric", _fact(
            "f-metric", "公司资产负债率为 65.43%。", (ASP_BUSINESS_MAIN,), period="")),
        ("事件类", "event_set", _fact(
            "f-event", "公司不存在重大诉讼、仲裁事项。", (ASP_BUSINESS_MAIN,), period="")),
        ("时点余额", "fact_set", _Fact(
            fact_id="f-balance", text="公司货币资金余额为 30.80 万元。",
            aspect_ids=(ASP_BUSINESS_MAIN,), citation_refs=(_citation(),), period="",
            scope="本公司",
            value_identity=_ValueIdentity(amount_canonical="30.80", unit="万元",
                                          period="", scope="本公司", metric="货币资金"))),
    )
    for what, kind, undated in undated_cases:
        undated_task = _task("company", (TOPIC_BUSINESS,),
                             task_id=f"task-co-undated-{kind}")
        undated_authority = _company_authority(
            undated_task, facts=(undated,),
            aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
            requirements=(_Req(TOPIC_BUSINESS, (
                _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business",
                        kind=kind),)),))
        undated_scan = PW.scan_topic_pack(undated_authority, undated_task)
        check(undated_scan.facts == (),
              f"{what}事实没有显式期间时不得进入候选可利用集（不得用基准期顶替）")
        check([t[1] for t in undated_scan.period_unresolved] == [undated.fact_id],
              f"{what}事实必须进入 period_unresolved（原因可查）")
        check(undated_scan.period_exclusions
              and undated_scan.period_exclusions[0]["reason"] == "explicit_period_required",
              f"{what}事实的排除原因必须是「必须有显式期间」，实为 "
              f"{undated_scan.period_exclusions}")
        check(REPORT_AS_OF not in json.dumps(undated_scan.period_exclusions, ensure_ascii=False),
              f"{what}事实的排除记录里不得出现 report_as_of（期间不得被基准期顶替）")

    # (e) 事实类型无法判定时必须 fail-closed 为 period_unresolved，而不是默认放行。
    unknown_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-unknown-kind")
    unknown_authority = _company_authority(
        unknown_task,
        facts=(_fact("f-unknown", "公司经营情况稳定。", (ASP_BUSINESS_MAIN,), period=""),),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business",
                    kind="single_judgment", time_scope=""),)),))
    unknown_scan = PW.scan_topic_pack(unknown_authority, unknown_task)
    check(unknown_scan.facts == (),
          "无法判定事实类型时不得默认放行（fail-closed 为 period_unresolved）")
    check(unknown_scan.period_exclusions
          and unknown_scan.period_exclusions[0]["reason"] == PW.PERIOD_UNRESOLVED_REASON,
          f"无法判定事实类型时的排除原因必须是 {PW.PERIOD_UNRESOLVED_REASON}，实为 "
          f"{unknown_scan.period_exclusions}")
    check(PW._period_requirement(
        _fact("f-probe", "公司经营情况稳定。", (ASP_BUSINESS_MAIN,), period=""),
        ("single_judgment",), ("",)) == PW.PERIOD_UNRESOLVED_REASON,
        "期间要求必须是三态判定（无法判定 ⇒ period_unresolved，不得默认 explicit/optional）")

    # (f) §十四 跨 topic 组织与 §十 事实表面守恒发生在**门后**（要看到最终 Narrative）：
    #     本批的门**不**实现它们，也不得把「没实现」当成「已通过」。这里如实断言其缺席。
    xt_task = _task("company", (TOPIC_BUSINESS, TOPIC_IDENTITY),
                    task_id="task-co-cross-topic")
    xt_packs = (
        _Pack(pack_id="pack-" + TOPIC_BUSINESS, topic_id=TOPIC_BUSINESS,
              facts=(_fact("f-biz", "公司主营业务为动力电池系统的研发、生产与销售。",
                           (ASP_BUSINESS_MAIN,)),),
              aspect_results=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),)),
        _Pack(pack_id="pack-" + TOPIC_IDENTITY, topic_id=TOPIC_IDENTITY,
              facts=(_fact("f-cap", "公司注册资本为 1234.56 万元。",
                           (ASP_IDENTITY_CAPITAL,)),),
              aspect_results=(_AspectResult(ASP_IDENTITY_CAPITAL, "covered"),)),
    )
    xt_authority = PW.TopicPackAuthorityInput.create(
        xt_task, _pack_set(xt_task, packs=xt_packs, requirements=(
            _Req(TOPIC_BUSINESS, (_aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                                          "q-company_business"),)),
            _Req(TOPIC_IDENTITY, (_aspect(ASP_IDENTITY_CAPITAL, TOPIC_IDENTITY,
                                          "q-company_identity"),)))),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    xt_scan = PW.scan_topic_pack(xt_authority, xt_task)
    xt_ids = {e.fact_id: e for e in xt_scan.facts}
    # 两个 topic ⇒ **两个 Pack**，各自带自己那份自动补料（同名 `m-auto-1`，容器不同）。两条候选
    # 的事实分别落在各自 Pack 上，因此草稿的**材料出处必须逐候选给对行**：回填的缺省别名 `m1`
    # （第一个 Pack 的那份材料）在这里是**错行**——它把第二段正文的出处指到了另一个 Pack 的材料
    # 上。`npr-1` 的逐 occurrence 核对正是为此存在（§四.1：出处在正文里是这句话的一部分），
    # 写入侧照拒，夹具不替它改写成对的那条。（事实轴在这里也走不通：本节材料面非空，题面出处
    # 只允许材料轴——那是另一条已经立着的规则。）
    #
    # 别名不猜：按**本节 manifest 自己的**成员顺序取，再用 `(pack_id, material_id)` 两把键
    # 区分同名材料（`_member_ref`）。
    xt_pack_of = {str(e.fact_id): str(e.container_identity) for e in xt_scan.facts}
    xt_ref_biz = _member_ref(xt_authority, task_id=str(xt_task.task_id), section_id="company",
                             material_id="m-auto-1", container_id=xt_pack_of["f-biz"])
    xt_ref_cap = _member_ref(xt_authority, task_id=str(xt_task.task_id), section_id="company",
                             material_id="m-auto-1", container_id=xt_pack_of["f-cap"])
    check(xt_ref_biz != xt_ref_cap,
          "夹具前提：两个 Pack 里的同名材料必须拿到**不同**别名（否则这一格测不到跨容器归属，"
          f"而会退化成同一个成员的两次出现）；实为 {xt_ref_biz!r} / {xt_ref_cap!r}")
    xt_outcome = _write(
        xt_task, xt_authority, section_id="company",
        llm=_StubLlm(_plan(
            candidates=[
                _cand("c1", xt_ids["f-biz"].text, _fact_edge(xt_scan, "f-biz")),
                _cand("c2", xt_ids["f-cap"].text, _fact_edge(xt_scan, "f-cap"))],
            draft_ref=[xt_ref_biz, xt_ref_cap])))
    check({tuple(u.source_member_refs) for u in xt_outcome.draft.natural_prose_draft}
          == {(NS.manifest_member_ref(xt_pack_of["f-biz"], "m-auto-1"),),
              (NS.manifest_member_ref(xt_pack_of["f-cap"], "m-auto-1"),)},
          "反查：两段草稿各自声明的成员必须**恰是**它那条候选自己的 Pack 里那份材料"
          "（按 (pack_id, material_id) 两把键判，不按裸 material_id）")
    xt_rules = {i.rule_id for i in xt_outcome.gate_result.issues}
    check("cross_topic_composition_not_obtained" not in xt_rules
          and "claim_surface_not_conserved" not in xt_rules,
          "门前硬门不实现跨 topic 组织与事实表面守恒（这两条属门后 3D，必须在 3D 落地，"
          "不得被当作已实现）")

    # ==================================================================
    # 13. 未解决项的阻断后果只能来自它登记的**权威问题**
    # ==================================================================
    blk_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-blocking",
                     questions=(PS.PlannedQuestion(
                         question_id="q-company_business", question="主营业务？",
                         priority="required", topic_id=TOPIC_BUSINESS,
                         blocking_policy=("NONE",), impact_scope=("subject",)),))
    blk_authority = _company_authority(
        blk_task,
        facts=(_fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,)),),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                 _AspectResult(ASP_BUSINESS_COST, "blocked")),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),
            _aspect(ASP_BUSINESS_COST, TOPIC_BUSINESS, "q-company_business",
                    blocking=("SECTION_BLOCKED",)))),))
    blk_scan = PW.scan_topic_pack(blk_authority, blk_task)
    blk_outcome = _write(blk_task, blk_authority, section_id="company",
                         llm=_StubLlm(_plan(candidates=[
                             _cand("c1", blk_scan.facts[0].text,
                                   _fact_edge(blk_scan, "f-1"))])))
    blk_effects = {tuple(u.blocking_effects) for u in blk_outcome.unresolved
                   if u.question_id == "q-company_business"}
    check(blk_effects == {()},
          "权威问题声明 NONE（不阻断）时，blocking_effects 必须是空集合且不得含字面量 NONE"
          f"（aspect 自称不得覆盖；实际 {sorted(blk_effects)}）")
    blk2_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-blocking-2",
                      questions=(PS.PlannedQuestion(
                          question_id="q-company_business", question="主营业务？",
                          priority="required", topic_id=TOPIC_BUSINESS,
                          blocking_policy=("SECTION_BLOCKED",),
                          impact_scope=("key_financial",)),))
    blk2_authority = _company_authority(
        blk2_task,
        facts=(_fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,)),),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                 _AspectResult(ASP_BUSINESS_COST, "blocked")),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),
            _aspect(ASP_BUSINESS_COST, TOPIC_BUSINESS, "q-company_business"))),))
    blk2_scan = PW.scan_topic_pack(blk2_authority, blk2_task)
    blk2_outcome = _write(blk2_task, blk2_authority, section_id="company",
                          llm=_StubLlm(_plan(candidates=[
                              _cand("c1", blk2_scan.facts[0].text,
                                    _fact_edge(blk2_scan, "f-1"))])))
    blk2_effects = {tuple(u.blocking_effects) for u in blk2_outcome.unresolved
                    if u.question_id == "q-company_business"}
    check(blk2_effects == {("SECTION_BLOCKED",)},
          "权威问题声明 SECTION_BLOCKED 时，后果字段不得被 aspect 的空白抹掉"
          f"（实际 {sorted(blk2_effects)}）")
    bad_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-blocking-bad",
                     questions=(PS.PlannedQuestion(
                         question_id="q-company_business", question="主营业务？",
                         priority="required", topic_id=TOPIC_BUSINESS,
                         blocking_policy=("SHUTDOWN",), impact_scope=("subject",)),))
    expect_error(
        lambda: PW._question_blocking_policy(bad_task),
        PW.PackWriterError, "权威问题声明未登记的 blocking_policy 等级时必须 fail-closed")

    # ==================================================================
    # 14. §三 P0：AuthorityInput 不得靠「直接调用 dataclass 构造器」绕过
    # ==================================================================
    expect_error(
        lambda: PW.TopicPackAuthorityInput(
            producer_kind="topic_harness", input_id=authority.input_id,
            task_id=co_task.task_id, section_id="company", company_id=COMPANY_ID,
            report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT, topic_ids=(TOPIC_BUSINESS,),
            pack_set=_FakePackSet(task_id=co_task.task_id, section_id="company",
                                  topic_ids=(TOPIC_BUSINESS,), packs=(_FakePack(),),
                                  requirements=())),
        PW.PackWriterError, "直接构造 TopicPackAuthorityInput 塞假 PackSet 必须被拒",
        needle="必须是真实的")
    expect_error(
        lambda: dataclasses.replace(authority, input_id="tainput_forged"),
        PW.PackWriterError, "直接构造出来的 input_id 必须被重算核对（伪造即拒）",
        needle="input_id 与输入内容不符")
    expect_error(
        lambda: PW.DerivedAuthorityInput(
            producer_kind="derived_section", input_id="tainput_forged",
            task_id=co_task.task_id, section_id="company", company_id=COMPANY_ID,
            report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT, topic_ids=(TOPIC_BUSINESS,),
            upstream_identity=(), selected_facts=(), required_fact_ids=()),
        PW.PackWriterError,
        "直接构造 DerivedAuthorityInput（demo_scope_selected 缺省 False）必须 fail-closed",
        needle="DemoScope")
    expect_error(
        lambda: PW.FinancialAuthorityInput(
            producer_kind="financial_workflow", input_id="tainput_forged",
            task_id=fin_task.task_id, section_id="financial", company_id=COMPANY_ID,
            report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT, topic_ids=(TOPIC_FIN_SOLVENCY,),
            fact_topic_map=(), artifact=_FakeArtifact(), note_facts=None,
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "直接构造 FinancialAuthorityInput 塞假 artifact 必须被拒",
        needle="必须是真实的")

    # ==================================================================
    # 15. §三 5/6/7/8：财务 artifact + 附注内容的身份覆盖与篡改检测
    # ==================================================================
    note_probe = dict(task_id=fin_task.task_id,
                      facts=(_NoteFact(fact_id="n-1", display="受限资金 12.34 元。"),))
    good_note = _NoteSet(**note_probe)
    for field, wrong, what in (("company_id", "别的公司", "公司"),
                               ("report_as_of", "2025-12-31", "报告基准日"),
                               ("contract_version", "v1", "Contract 版本"),
                               ("contract_fingerprint", "c" * 64, "Contract 指纹")):
        kw = dict(note_probe)
        kw[field] = wrong
        expect_error(
            lambda kw=kw: _financial_authority(fin_task, fin_artifact,
                                               note_facts=_NoteSet(**kw)),
            PW.PackWriterError, f"附注事实挂错{what}必须被拒", needle="不一致")
    expect_error(
        lambda: _financial_authority(
            fin_task, dataclasses.replace(fin_artifact, content_fingerprint="0" * 64),
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "被篡改的 artifact 必须在 verify() 处被拒", needle="verify() 失败")
    expect_error(
        lambda: _financial_authority(
            fin_task,
            dataclasses.replace(fin_artifact, artifact_id="ffpa_forged",
                                content_fingerprint=fin_artifact.compute_content_fingerprint()),
            note_gap=_NoteGap(task_id=fin_task.task_id)),
        PW.PackWriterError, "artifact_id 与内容不符必须在 verify() 处被拒",
        needle="verify() 失败")
    fin_gap_a = _NoteGap(task_id=fin_task.task_id)
    fin_gap_b = _NoteGap(task_id=fin_task.task_id, reason_codes=("no_admissible_note_span",),
                         searched_scope=("financial_statements_notes",),
                         searched_need_ids=("need-note-1",))
    inputs = {
        "note set A": _financial_authority(fin_task, fin_artifact, note_facts=good_note),
        "note set B": _financial_authority(
            fin_task, fin_artifact,
            note_facts=_NoteSet(task_id=fin_task.task_id, facts=(
                _NoteFact(fact_id="n-1", display="受限资金 12.34 元。"),
                _NoteFact(fact_id="n-2", display="受限资金 56.78 元。"),))),
        "note gap A": _financial_authority(fin_task, fin_artifact, note_gap=fin_gap_a),
        "note gap B": _financial_authority(fin_task, fin_artifact, note_gap=fin_gap_b),
        "artifact B": _financial_authority(
            fin_task,
            _Artifact(task_id=fin_task.task_id,
                      facts=(_FinFact(fact_id="ff-short", label="短期偿债能力",
                                      display="1.20", period=REPORT_AS_OF, unit="倍"),)),
            note_gap=fin_gap_a),
        "artifact C": _financial_authority(
            fin_task,
            _Artifact(task_id=fin_task.task_id,
                      facts=(_FinFact(fact_id="ff-short", label="短期偿债能力",
                                      display="1.20", period=REPORT_AS_OF, unit="倍"),),
                      projection_notes=("另一条投影说明。",)),
            note_gap=fin_gap_a),
    }
    ids = {name: obj.input_id for name, obj in inputs.items()}
    check(len(set(ids.values())) == len(ids),
          f"更换附注集合/附注缺口/artifact 内容必须改变权威输入 id（实际 {ids}）")
    other_company_input = PW.FinancialAuthorityInput.create(
        fin_task,
        _Artifact(task_id=fin_task.task_id, company_id="另一家公司",
                  snapshot=_snapshot_identity("另一家公司", REPORT_AS_OF)),
        note_gap=_NoteGap(task_id=fin_task.task_id, company_id="另一家公司"),
        company_id="另一家公司", report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    check(other_company_input.input_id != ids["note gap A"],
          "公司身份变化必须改变权威输入 id")
    check(all(i.startswith(PW.AUTHORITY_INPUT_ID_PREFIX) for i in ids.values()),
          "权威输入 id 必须带内容寻址前缀（可辨认、可重算）")

    # ==================================================================
    # 16. §四：material 身份域（正例 + 裸 id / 缺失 / payload 不符反例）
    # ==================================================================
    check(facts_by_id["f-1"].material_id
          and facts_by_id["f-1"].payload_ref is not None,
          "material 存在时必须同时绑定 material_id 与 payload reference（§四 4）")
    check(TS.citation_source_identity(_citation()) == f"evidence:{EV_ID}",
          "引用锚点 → 来源身份只能走唯一类型化入口（裸 id 与 material 侧不同域）")
    expect_error(
        lambda: _material_of_identity("m-raw", EV_ID),
        TS.SchemaValidationError, "裸 id 来源身份的 material 必须在真实类型构造期被拒")
    # 来源身份在、material 不在：这不是「绑定丢失」，而是**路径 A 的合法形态** —— topic_pack
    # 的预验证事实由 path_a_prevalidated 支撑，本来就不需要 exact material。权威侧不得因此
    # 凭空编出一份 material（否则就是伪造来源），也不得凭空空写：真正的保证在两条边上——
    #   (i) 事实边必须走 path A（path B 不得支撑事实性候选）；
    #   (ii) context 边**必须**绑真实 material（「context 不得凭空」）。
    nomat_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-nomaterial")
    nomat_authority = PW.TopicPackAuthorityInput.create(
        nomat_task,
        _pack_set(nomat_task,
                  facts=(_fact("f-nomat", "公司主营业务为动力电池系统的研发、生产与销售。",
                               (ASP_BUSINESS_MAIN,)),),
                  aspect_results=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
                  auto_materials=False),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    nomat_scan = PW.scan_topic_pack(nomat_authority, nomat_task)
    nomat_entry = nomat_scan.facts[0]
    check(nomat_entry.material_id is None and nomat_entry.payload_ref is None,
          "权威侧不得为「没有 material 的来源身份」凭空编造 material 绑定")
    check(nomat_entry.source_identity == f"evidence:{EV_ID}",
          f"material 缺失不得抹掉来源身份（来源仍可追溯，实为 {nomat_entry.source_identity!r}）")
    nomat_outcome = _write(
        nomat_task, nomat_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=[
            _cand("c1", nomat_entry.text, _fact_edge(nomat_scan, "f-nomat"))],
            draft_axis="fact", draft_ref=[_fact_ref(nomat_scan, "f-nomat")])))
    check(not nomat_outcome.gate_result.blocking
          and len(nomat_outcome.draft.material_manifest.entries) == 0,
          "路径 A 事实边不需要 material：不得因「上游没有 material」伪造出 support_material_unbound")
    # 同一权威上，事实边改走 path B 必须被拒（路径错配）。
    # C4 起：形状不合法先按 `bsc-1` 对**那一批**纠正一次（同一批只纠正一次），因此脚本给
    # 两次同一份非法响应——第二次就是纠正那一次的回答。整节仍然 fail-closed。
    _path_mismatch = _plan(candidates=[
        _cand("c1", nomat_entry.text, {
            **_fact_edge(nomat_scan, "f-nomat"),
            "authorization_path": "path_b_exact_material"})],
        draft_axis="fact", draft_ref=[_fact_ref(nomat_scan, "f-nomat")])
    expect_error(
        lambda: _write(nomat_task, nomat_authority, section_id="company",
                       llm=_StubLlm(_path_mismatch, _path_mismatch)),
        PW.PackWriterError, "topic_pack 事实性候选不得走 path B（路径错配必须 fail-closed）",
        needle="不能支撑事实性候选")
    # context 边没有 material = 凭空背景 → 必须 fail-closed（这才是「material 缺失」的真守卫）。
    _context_without_material = _plan(units=[
        _unit("u1", "以下按业务条线说明公司经营结构。",
              context=[{"authority_kind": "topic_pack",
                        "container_id": nomat_entry.container_identity,
                        "material_id": None,
                        "support_role": "corroborating",
                        "support_semantics": "context",
                        "authorization_path": "context_only"}])])
    expect_error(
        lambda: _write(nomat_task, nomat_authority, section_id="company",
                       llm=_StubLlm(_context_without_material,
                                    _context_without_material)),
        PW.PackWriterError, "context 边没有真实 material 时必须 fail-closed（context 不得凭空）",
        needle="context 不得凭空")
    # material 存在但 support edge 绑了别的 payload / 干脆漏绑 → 最终硬门必须拒。
    good_ref = next(p for p in draft.proposed_support_refs
                    if p.fact_id == "f-1")

    def _forge_ref(ref, **overrides):
        fields = ("binding_subject_kind", "binding_subject_id", "draft_revision",
                  "manifest_id", "manifest_fingerprint", "authority_kind",
                  "authority_container_id", "source_identity", "provenance_identity",
                  "support_role", "support_semantics", "authorization_path",
                  "content_fingerprint", "dependency_fingerprint", "fact_id",
                  "financial_fact_id", "note_fact_id", "external_fact_id", "material_id",
                  "payload_ref", "locator_ref")
        body = {k: getattr(ref, k) for k in fields}
        body.update(overrides)
        return NS.ProposedSupportRef.create(**body)

    def _rebuild(draft_obj, **overrides):
        return NS.SectionDraft.create(**{**draft_obj.to_dict(), **overrides})

    def _swap(draft_obj, old_ref, new_ref):
        """把一条 proposal 换成伪造版，并**同步**修正材料处理行里的用途引用。

        proposal 的身份是内容寻址的：换掉内容就换了 id，材料处理行的 `support_usages` 必须
        跟着改，否则先撞上的是「不闭合」而不是我们要验的那条门规则。
        """
        old_id = old_ref.proposed_support_id
        new_id = new_ref.proposed_support_id
        rows = tuple(
            NS.WriterMaterialProcessingDisposition.create(**{
                **row.to_dict(),
                "support_usages": [new_id if pid == old_id else pid
                                   for pid in row.support_usages]}).to_dict()
            for row in draft_obj.material_dispositions)
        return _rebuild(
            draft_obj,
            proposed_support_refs=tuple(
                new_ref if r.proposed_support_id == old_id else r
                for r in draft_obj.proposed_support_refs),
            material_dispositions=rows)

    tampered = _forge_ref(good_ref, material_id=good_ref.material_id,
                          payload_ref={**dict(good_ref.payload_ref), "content_hash": "d" * 64})
    tampered_rules = {i.rule_id for i in NS.gate_draft(
        _swap(draft, good_ref, tampered), authority).issues}
    check("support_payload_mismatch" in tampered_rules,
          f"support edge 在 material 存在时把 payload 换成别的必须被硬门拒"
          f"（实际 {sorted(tampered_rules)}）")

    # `support_material_unbound`（material 存在却绑到**别的** material）必须真的能触发。
    # 注意不能简单地把它置空：那样 Draft 自己的「三层集合等式」会先拒（引用过的材料被报成
    # 没用到），所以这里造一个**自洽**的 draft —— 把 f-1 的路径 A 边改绑到同容器内的另一份
    # 真实 material，并相应把该材料记为 used、把它本该绑的那份记为 not_used。
    extra_entry = next(e for e in draft.material_manifest.entries
                       if e.material_id == "m-extra-1")
    own_entry = next(e for e in draft.material_manifest.entries
                     if e.material_id == facts_by_id["f-1"].material_id)
    extra_candidate = NS.material_index(authority)[container]["evidence:" + NOTE_EV_ID][0]
    context_ref = next(p for p in draft.proposed_support_refs
                       if p.support_semantics == "context")
    misbound = _forge_ref(good_ref, material_id="m-extra-1",
                          payload_ref=extra_candidate.payload_ref)
    _rows = []
    for row in draft.material_dispositions:
        if row.member_ref == extra_entry.member_ref:
            usages = [pid for pid in row.support_usages
                      if pid != good_ref.proposed_support_id]
            usages.append(misbound.proposed_support_id)
            _rows.append(NS.WriterMaterialProcessingDisposition.create(
                **{**row.to_dict(), "support_usages": usages}))
        elif row.member_ref == own_entry.member_ref:
            # f-1 的边被改绑走了：这份材料只由 f-2 的边继续引用（used 的用途必须真实）。
            _rows.append(NS.WriterMaterialProcessingDisposition.create(**{
                **row.to_dict(),
                "support_usages": [pid for pid in row.support_usages
                                   if pid != good_ref.proposed_support_id]}))
        else:
            _rows.append(row)
    # `npr-1` 起，这种伪造在**草稿层**就先被拒：草稿说「这段正文出自 m-auto-N」，而它的支撑边被
    # 改绑到 `m-extra-1` —— 逐 occurrence 的出处核对发现这条候选**自己**的边一条也没落在本段声明
    # 的来源上。把这一层单独钉住（而不是把伪造写成能过的样子），因为「哪一层拦住」本身是产物
    # 可读性的一部分。
    expect_error(
        lambda: _rebuild(
            draft,
            proposed_support_refs=tuple(
                misbound if r.proposed_support_id == good_ref.proposed_support_id else r
                for r in draft.proposed_support_refs),
            material_dispositions=tuple(r.to_dict() for r in _rows)),
        NS.NarrativeSchemaError, "改绑到别的 material 必须先在草稿闭合核对处被拒（早于硬门）",
        needle="没有一条落在本段的来源上")
    # 硬门那条规则**仍然**要单独证明，但**形态**必须换：`npr-1` 之后带草稿层的 draft 已经到不了
    # 这里（上一条刚证明过）。这条规则要守的是**没有草稿层的形态**——`narr-7` 及更早的
    # `SectionDraft` 根本没有草稿层，而历史工件读回来后照样要过门，凭什么规则只对「有草稿层的
    # 新产物」存在？因此下面按无草稿层的形态从零构造一份**自洽**的 draft（修订按空草稿摘要重算，
    # 候选 / 边 / 处理行同源同版本），只把 f-1 的路径 A 边绑到同容器内的另一份真实 material 上。
    # 两层各自 fail-closed、互不替代：草稿层拦住新产物，不等于硬门这条规则可以拿掉。
    legacy_rev = NS.derive_draft_revision(
        task_id=draft.task_id, section_id=draft.section_id, company_id=draft.company_id,
        report_as_of=draft.report_as_of, contract_version=draft.contract_version,
        contract_fingerprint=draft.contract_fingerprint,
        writer_policy_version=draft.writer_policy_version, prompt_version=draft.prompt_version,
        model_policy=draft.model_policy, manifest_id=co_manifest.manifest_id,
        manifest_fingerprint=co_manifest.fingerprint(), natural_prose_digest="")
    check(legacy_rev != draft.draft_revision and draft.draft_revision == NS.derive_draft_revision(
        task_id=draft.task_id, section_id=draft.section_id, company_id=draft.company_id,
        report_as_of=draft.report_as_of, contract_version=draft.contract_version,
        contract_fingerprint=draft.contract_fingerprint,
        writer_policy_version=draft.writer_policy_version, prompt_version=draft.prompt_version,
        model_policy=draft.model_policy, manifest_id=co_manifest.manifest_id,
        manifest_fingerprint=co_manifest.fingerprint(),
        natural_prose_digest=NS.natural_prose_draft_digest(draft.natural_prose_draft)),
        "夹具前提：无草稿层的修订必须与带草稿层的那一份**不同**（相同的话这一格就不是历史形态，"
        f"而是同一份产物的复制）；实为 {legacy_rev!r} vs {draft.draft_revision!r}")
    legacy_cand = NS.ClaimCandidate.create(
        draft_revision=legacy_rev, task_id=draft.task_id, section_id=draft.section_id,
        company_id=draft.company_id, report_as_of=draft.report_as_of,
        contract_version=draft.contract_version,
        contract_fingerprint=draft.contract_fingerprint,
        claim_text=facts_by_id["f-1"].text, fact_type="metric")
    legacy_edge = _forge_ref(good_ref, draft_revision=legacy_rev,
                             binding_subject_id=legacy_cand.candidate_id,
                             material_id="m-extra-1",
                             payload_ref=extra_candidate.payload_ref)

    def _legacy_row(entry, usages=()):
        if usages:
            return NS.WriterMaterialProcessingDisposition.create(
                manifest_id=co_manifest.manifest_id,
                manifest_fingerprint=co_manifest.fingerprint(), pack_id=str(entry.pack_id),
                material_id=str(entry.material_id),
                research_material_disposition_id=str(entry.research_material_disposition_id),
                material_content_fingerprint=str(entry.material_content_fingerprint),
                processed=True, usage="used", support_usages=usages, reason_code=None,
                reason_proof=None, writer_policy_version=draft.writer_policy_version)
        return NS.WriterMaterialProcessingDisposition.create(
            manifest_id=co_manifest.manifest_id,
            manifest_fingerprint=co_manifest.fingerprint(), pack_id=str(entry.pack_id),
            material_id=str(entry.material_id),
            research_material_disposition_id=str(entry.research_material_disposition_id),
            material_content_fingerprint=str(entry.material_content_fingerprint),
            processed=True, usage="not_used", support_usages=(),
            reason_code="irrelevant_to_section_goal",
            reason_proof={"policy_version": "mnp-1", "policy_fingerprint": "c" * 64,
                          "relevance_decision_ref": "rd-legacy"},
            writer_policy_version=draft.writer_policy_version)

    legacy_rows = tuple(
        _legacy_row(entry, (legacy_edge.proposed_support_id,))
        if str(entry.material_id) == "m-extra-1" else _legacy_row(entry)
        for entry in co_manifest.entries)
    misbound_draft = NS.SectionDraft.create(
        task_id=draft.task_id, section_id=draft.section_id, company_id=draft.company_id,
        report_as_of=draft.report_as_of, contract_version=draft.contract_version,
        contract_fingerprint=draft.contract_fingerprint, producer_kind=draft.producer_kind,
        writer_policy_version=draft.writer_policy_version, prompt_version=draft.prompt_version,
        model_policy=draft.model_policy,
        authority_container_ids=tuple(draft.authority_container_ids),
        material_manifest=co_manifest, material_dispositions=legacy_rows,
        claim_candidates=(legacy_cand,), narrative_draft_units=(),
        proposed_support_refs=(legacy_edge,), unresolved_ids=(),
        unresolved_projections=(), coverage_summary={}, conflict_projections=(),
        not_found_projections=(), dependency_fingerprint=draft.dependency_fingerprint)
    misbound_rules = {i.rule_id for i in NS.gate_draft(misbound_draft, authority).issues}
    check("support_material_unbound" in misbound_rules,
          f"权威事实的引用**确实**能落到某 material 时，proposal 绑到别的 material 必须被"
          f"硬门拒（实际 {sorted(misbound_rules)}）")
    check(not NS.gate_draft(draft, authority).blocking,
          "反例不是恒失败：原 draft 对自己的权威输入必须仍然过门")

    # ==================================================================
    # 17. §五：prompt 版本 / 实际模型 / 调用 trace 三条链路
    # ==================================================================
    check(outcome.prompt_version == PW.NARRATION_PROMPT_VERSION
          and outcome.model_policy == PW.MODEL_POLICY_STUB,
          "产出必须记录实际加载的 prompt 版本与 model policy")
    check(len(outcome.llm_trace) == outcome.llm_calls == 1,
          f"调用一次必须恰有一条 trace（实际 {len(outcome.llm_trace)} 条 / "
          f"{outcome.llm_calls} 次）")
    trace = dict(outcome.llm_trace[0])
    check(trace.get("call_id") == "call-1" and trace.get("model") == PW.MODEL_POLICY_STUB
          and trace.get("prompt_version") == PW.NARRATION_PROMPT_VERSION
          and trace.get("status") == "ok" and isinstance(trace.get("input_tokens"), int)
          and isinstance(trace.get("output_tokens"), int)
          and isinstance(trace.get("latency_ms"), int)
          and trace.get("finish_reason") == "stop"
          and len(str(trace.get("response_hash") or "")) == 64,
          f"调用 trace 必须保留完整元数据（实际 {trace}）")
    check(not [t for t in gap_only.llm_trace] and gap_only.llm_calls == 0,
          "没有调用时 trace 必须为空（不得凭记录补一条）")
    check(len(recovered.llm_trace) == 2
          and [t["status"] for t in recovered.llm_trace] == ["ok", "ok"],
          "重试后两次调用各自留一条 trace")
    expect_error(
        lambda: _write(co_task, authority, section_id="company",
                       llm=_StubLlm(_plan()),
                       policy=PW.WriterPolicy(prompt_version="pack_section_writer_v0@1")),
        PW.PackWriterError, "记录版本与登记版本不一致必须被拒", needle="不一致")
    expect_error(
        lambda: PW.verify_prompt_asset(
            "公司/行业正文写作器（pack_section_writer_v1, revision 0）", PW.WriterPolicy()),
        PW.PackWriterError, "实际加载的 prompt 资产版本与记录版本不一致必须被拒",
        needle="不一致")
    check(PW.NARRATION_PROMPT_ASSET in PW.NARRATION_PROMPT_VERSION,
          "prompt 版本记录形态必须是 `<资产名>@<revision>`")
    expect_error(
        lambda: _write(co_task, authority, section_id="company",
                       llm=_StubLlm(_plan(), model="别的模型")),
        PW.PackWriterError, "实际调用模型与 model policy 解析结果不一致必须被拒",
        needle="实际调用模型")
    expect_error(
        lambda: _write(co_task, authority, section_id="company",
                       llm=_StubLlm(_plan(), prompt_version="pack_section_writer_v1@0")),
        PW.PackWriterError, "实际调用使用的 prompt 版本与 WriterPolicy 记录不一致必须被拒",
        needle="实际调用使用的 prompt 版本")
    expect_error(
        lambda: PW.resolve_model_policy("some_other_policy"),
        PW.PackWriterError, "未登记的 model policy 必须 fail-closed（无法确定性解析模型）",
        needle="未登记的 model policy")
    failing_client = _StubLlm(RuntimeError("provider 超时"))
    expect_error(
        lambda: _write(co_task, authority, section_id="company", llm=failing_client),
        PW.PackWriterError, "生成调用失败必须 fail-closed", needle="生成调用失败")
    check(len(failing_client.calls) == 1, "调用失败不得被重试掩盖（默认 0 次重试）")

    # ==================================================================
    # 18. 表格展示元数据：narr-4 的草稿单元**不可表达**表级/行级元数据（门后 3D）
    # ==================================================================
    # narr-3 的表格（caption/entity_scope/unit/period/header/rows）已从提案契约里整体移除：
    # `NarrativeDraftUnit(unit_kind="table")` 只承载文本。因此「表题夹带未受权数字 / 换单位 /
    # 换期间 / 换主体范围」这一类伪造面在门前**不存在**；它们必须由门后 P13/P14 在
    # `NarrativeTable` 真正形成时逐字回查（本批不实现，3D 必须落地）。
    unit_fields = set(NS.NarrativeDraftUnit.__dataclass_fields__)
    check(not ({"caption", "entity_scope", "unit", "period", "header", "rows", "cells",
                "label"} & unit_fields),
          f"草稿单元不得承载任何表格展示/行级元数据（实际字段 {sorted(unit_fields)}）")
    check(unit_fields == {"draft_unit_id", "schema_version", "draft_revision", "section_id",
                          "index", "unit_kind", "text"},
          f"草稿单元的字段集必须封闭（实际 {sorted(unit_fields)}）")
    table_outcome = _write(
        co_task, authority, section_id="company",
        llm=_StubLlm(_plan(
            candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(scan, "f-1")),
                        _cand("c2", facts_by_id["f-2"].text, _fact_edge(scan, "f-2"))],
            units=[_unit("u1", "业务结构一览", kind="table")])))
    table_unit = [u for u in table_outcome.draft.narrative_draft_units
                  if u.unit_kind == "table"][0]
    check("narrative_table_display_drift" not in {
        i.rule_id for i in table_outcome.gate_result.issues},
        "门前硬门不实现表格展示元数据回查（属门后 3D 的 final-Narrative 门）")
    check(table_unit.text == "业务结构一览" and table_unit.index == 0
          and table_unit.section_id == co_task.section_id,
          "表格草稿单元只承载文本与确定性索引（元数据留给门后）")

    # ==================================================================
    # 19. M930-3.2 §三/§四：同一来源身份下的**多个 material 候选**必须确定性消歧
    # ==================================================================
    CROSS = f"evidence:{EV_ID}"

    def _candidate_authority(task_id: str, materials):
        task = _task("company", (TOPIC_BUSINESS,), task_id=task_id)
        return task, _company_authority(
            task,
            facts=(_fact("f-cross", "公司主营业务为动力电池系统的研发、生产与销售。",
                         (ASP_BUSINESS_MAIN,)),),
            materials=tuple(materials),
            aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
            requirements=(_Req(TOPIC_BUSINESS, (
                _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)),)),))

    cross_task, cross_authority = _candidate_authority(
        "task-co-candidates", (_Material("m-p12", CROSS, page=12),
                               _Material("m-p20", CROSS, page=20)))
    cross_pack = cross_authority.pack_set.packs[0]
    cross_container = str(cross_pack.pack_id)
    cross_materials = {str(m.material_id): m for m in cross_pack.materials}
    index = NS.material_index(cross_authority)[cross_container][CROSS]
    check(sorted(c.material_id for c in index) == ["m-p12", "m-p20"],
          "同一来源身份下的全部 material 候选都必须保留（不得最后写入者胜出）")

    for page, expected_id in ((12, "m-p12"), (20, "m-p20")):
        bound = NS.material_binding_for_citation(cross_authority, cross_container, _citation(page))
        check(bound is not None and bound[0] == expected_id,
              f"citation page={page} 必须选中同页 material {expected_id}"
              f"（实际 {bound[0] if bound else None}）")
        check(bound is not None and NS.canonical_json(bound[1]) == NS.canonical_json(
            cross_materials[expected_id].payload_ref.to_dict()),
              f"{expected_id} 的绑定必须带它自己的 payload reference（不得跨候选拼装）")

    same_task, same_authority = _candidate_authority(
        "task-co-same-page", (_Material("m-a", CROSS, page=12),
                              _Material("m-b", CROSS, page=12)))
    same_container = str(same_authority.pack_set.packs[0].pack_id)
    #: `mbind-1`（M930-3 `ndc-2` 批）：这条事实的资格链**声明了唯一输入材料**（`_chain_ids`
    #: 按「该事实自己的来源 material」建链，同页两份里命中 `m-a`），因此同页歧义被收窄到
    #: `m-a` —— **不是**任选其一：收窄依据是这条事实自己的资格链，不是候选顺序或评分。
    same_pack = same_authority.pack_set.packs[0]
    narrowed = NS.material_binding_for_citation(same_authority, same_container, _citation(12),
                                                fact=same_pack.facts[0], pack=same_pack)
    check(narrowed is not None and narrowed[0] == "m-a",
          f"资格链声明唯一输入材料时，同页歧义必须按它收窄（实际 "
          f"{narrowed[0] if narrowed else None}）")

    #: 反例（**保留**的旧同页歧义拒绝）：这条事实的资格链自己就说输入是**两份**材料 ——
    #: 出处不唯一，收窄无从谈起，只能 fail-closed，绝不退回去任选其一。
    amb_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-same-page-ambiguous")
    amb_authority = _company_authority(
        amb_task,
        facts=(_fact("f-cross", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,), material_ids=("m-a", "m-b")),),
        materials=(_Material("m-a", CROSS, page=12), _Material("m-b", CROSS, page=12)),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)),)),))
    amb_container = str(amb_authority.pack_set.packs[0].pack_id)
    amb_pack = amb_authority.pack_set.packs[0]
    check(NS.fact_verified_input_material_ids(amb_pack, amb_pack.facts[0])
          == frozenset({"m-a", "m-b"}),
          "两输入事实的资格链必须可核验地读回两份输入材料（收窄集不是单元素）")
    expect_error(
        lambda: NS.material_binding_for_citation(amb_authority, amb_container, _citation(12),
                                                 fact=amb_pack.facts[0], pack=amb_pack),
        NS.MaterialBindingAmbiguityError,
        "同页多个 material 候选且事实出处不唯一时必须 fail-closed"
        "（不得任选第一条/最后一条/最长/最高分）")
    expect_error(
        lambda: PW.scan_topic_pack(amb_authority, amb_task),
        PW.PackWriterError, "material 候选歧义时 Writer 必须 fail-closed（不得任选其一）",
        needle="多个 material 候选")

    def _bind_with_order(order, page):
        shuffled = dataclasses.replace(cross_pack, materials=tuple(order))
        holder = type("_ReorderedAuthority", (), {})()
        holder.producer_kind = "topic_harness"
        holder.pack_set = type("_ReorderedPackSet", (), {})()
        holder.pack_set.packs = (shuffled,)
        return NS.material_binding_for_citation(holder, cross_container, _citation(page))

    forward = tuple(_bind_with_order(tuple(cross_materials.values()), p) for p in (12, 20))
    backward = tuple(_bind_with_order(tuple(reversed(tuple(cross_materials.values()))), p)
                     for p in (12, 20))
    check(tuple(b[0] for b in forward) == ("m-p12", "m-p20") and forward == backward,
          f"候选顺序反转必须给出逐字相同的结果（实际 {tuple(b[0] for b in forward)} / "
          f"{tuple(b[0] for b in backward)}）")

    cross_scan = PW.scan_topic_pack(cross_authority, cross_task)
    cross_entry = cross_scan.facts[0]
    cross_ok = _write(
        cross_task, cross_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=[_cand("c1", cross_entry.text,
                                             _fact_edge(cross_scan, "f-cross"))])))
    cross_ref = cross_ok.draft.proposed_support_refs[0]
    check(cross_ref.material_id == "m-p12",
          f"跨页候选下候选必须绑定同页 material（实际 {cross_ref.material_id}）")
    swapped = _forge_ref(cross_ref, material_id="m-p12",
                         payload_ref=cross_materials["m-p20"].payload_ref.to_dict())
    swapped_rules = {i.rule_id for i in NS.gate_draft(
        _swap(cross_ok.draft, cross_ref, swapped), cross_authority).issues}
    check("support_payload_mismatch" in swapped_rules,
          f"support edge 借用同一来源身份下**另一个候选**的 payload 必须被硬门拒"
          f"（实际 {sorted(swapped_rules)}）")

    # 歧义的第二道防线是独立审查门的 `support_material_ambiguous`。它的输入侧确实成立
    # （下面直接验证派生结果），但在**当前唯一写作链**上不可达：任何能让带歧义 fact 的
    # proposal 走到门前的路径，都会先被一条更早的确定性 fail-closed 拦下 ——
    #   (i) 歧义 fact 若可用 → `scan_topic_pack` 直接拒（上面已断言）；
    #   (ii) 歧义 fact 若被排除（例如无显式期间）→ 它不在本节权威事实目录内，路径 A 的
    #        「逐字选择」校验拒绝自报坐标。
    # 这里把这两点都钉死，并如实登记「门级规则在单链上不可达」这一事实，而不是伪造一个
    # 能触发它的 draft。
    amb_expectations, amb_keys = NS._authority_material_expectations(amb_authority)
    check(amb_keys == {(amb_container, "f-cross")} and not amb_expectations,
          f"歧义必须登记在 (容器, fact_id) 上且不产生任何可用绑定（实际 {sorted(amb_keys)} / "
          f"{sorted(amb_expectations)}）")
    #: 对照（`mbind-1`）：资格链声明唯一输入材料的那条事实**不**登记歧义，而是**按链**绑定到
    #: 那唯一一份——收窄是「读出来的」，不是「挑出来的」。
    same_expectations, same_keys = NS._authority_material_expectations(same_authority)
    check(not same_keys and same_expectations.get((same_container, "f-cross"), (None,))[0] == "m-a",
          f"资格链唯一输入材料 ⇒ 不登记歧义、按链绑定 m-a（实际 {sorted(same_keys)} / "
          f"{sorted(same_expectations)}）")
    check(bool(NS.NARRATIVE_GATE_VERSION)
          and "support_material_ambiguous" in (
              Path(__file__).resolve().parent.parent / "sections"
              / "narrative_schema.py").read_text(encoding="utf-8"),
          "门级规则 `support_material_ambiguous` 必须落在唯一的门实现里（词汇表封闭）")
    # (ii)：把歧义挪到一个**无显式期间**（因而被排除）的事实上 —— 扫描不再拒，但该事实
    # 也不得进入可用集，更不得带着「任选其一」的 material 混进来。这条事实的资格链同样声明
    # **两份**输入材料（`mbind-1` 下唯一能让同页歧义存活的前提）。
    excl_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-excluded-ambiguous")
    excl_authority = _company_authority(
        excl_task,
        facts=(_fact("f-amb", "公司销售模式以直销为主。", (ASP_BUSINESS_MAIN,), period="",
                     material_ids=("m-a", "m-b")),),
        materials=(_Material("m-a", CROSS, page=12), _Material("m-b", CROSS, page=12)),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                    _question_id(TOPIC_BUSINESS)),)),))
    excl_scan = PW.scan_topic_pack(excl_authority, excl_task)
    excl_container = str(excl_authority.pack_set.packs[0].pack_id)
    excl_expectations, excl_keys = NS._authority_material_expectations(excl_authority)
    check(excl_scan.facts == ()
          and [t[1] for t in excl_scan.period_unresolved] == ["f-amb"],
          "带歧义 material 的无期间事实必须被排除在可用集之外（不得任选其一混入候选）")
    check(excl_keys == {(excl_container, "f-amb")} and not excl_expectations,
          "被排除事实的 material 歧义仍须被门级派生识别（第二道防线不因排除而失效）")
    expect_error(
        lambda: _write(excl_task, excl_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=[
                           _cand("c1", "公司销售模式以直销为主。",
                                 {"authority_kind": "topic_pack",
                                  "container_id": excl_container, "fact_id": "f-amb",
                                  "material_id": None, "support_role": "primary",
                                  "support_semantics": "factual",
                                  "authorization_path": "path_a_prevalidated"})]))),
        PW.PackWriterError,
        "被排除事实不得被 proposal 自报坐标引用（路径 A 只能逐字选择事实目录内的事实）",
        needle="不在本节的权威事实目录内")
    ambiguous_rules = {i.rule_id for i in NS.gate_draft(draft, authority).issues}
    check("support_material_ambiguous" not in ambiguous_rules,
          "消歧成功的权威输入上不得出现歧义阻断（反例不是恒失败）")

    check(facts_by_id["f-1"].material_id
          == next(e.material_id for e in draft.material_manifest.entries
                  if e.material_id == facts_by_id["f-1"].material_id),
          "单一 material 的正例绑定不得回归")

    # ==================================================================
    # 20. §16.10 #32：formal ExternalFact 的路径 A 必须可达且身份闭环
    # ==================================================================
    ext_statement = "该公司所处行业的公开统计口径按年度更新。"
    ext_cand, ext_dec, ext_fact = _external_spec(ext_statement)
    ext_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-external")
    ext_authority = PW.TopicPackAuthorityInput.create(
        ext_task,
        _pack_set(ext_task, packs=(_Pack(
            topic_id=TOPIC_BUSINESS,
            aspect_results=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
            external_facts=(ext_fact,),
            external_chains=((ext_cand, ext_dec),)),),
            requirements=(_Req(TOPIC_BUSINESS, (
                _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),)),)),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    ext_scan = PW.scan_topic_pack(ext_authority, ext_task)
    ext_entry = {e.fact_id: e for e in ext_scan.facts}.get(ext_fact.external_fact_id)
    check(ext_entry is not None and ext_entry.authority_kind == "external_snapshot",
          "formal ExternalFact 必须出现在本节权威事实目录里（不得进了 Pack 却读不到）")
    check(ext_entry is not None
          and ext_entry.container_identity == NS.external_authority_container_id(ext_fact)
          and ext_entry.container_identity != ext_entry.fact_id,
          "external 的容器身份（snapshot 记录）必须与事实身份（external_fact_id）分开")
    check(ext_entry is not None and ext_entry.material_id is None
          and ext_entry.payload_ref is not None and ext_entry.locator_ref is not None,
          "external 路径 A 不带 material，但必须带 snapshot 载体与 exact locator")
    check(ext_entry is not None and ext_entry.provenance_identity
          == str(ext_fact.qualification_decision_id),
          "external 的 provenance 必须是该事实自己的资格决定（不是别的 id）")
    ext_outcome = _write(
        ext_task, ext_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=[_cand("c1", ext_statement, _fact_edge(
            ext_scan, ext_fact.external_fact_id))],
            draft_axis="fact",
            draft_ref=[_fact_ref(ext_scan, ext_fact.external_fact_id)])))
    check(not ext_outcome.gate_result.blocking,
          "formal ExternalFact 的路径 A 必须真的可达（不得出现「进了 Pack 却读不到」的死路）")
    ext_ref = ext_outcome.draft.proposed_support_refs[0]
    check(ext_ref.authority_kind == "external_snapshot"
          and ext_ref.external_fact_id == ext_fact.external_fact_id
          and not ext_ref.fact_id,
          "external proposal 的事实身份只走 `external_fact_id`（不跨 kind 冒充）")
    forged_carrier = _forge_ref(
        ext_ref, payload_ref={**(ext_ref.payload_ref or {}), "canonical_url": "https://y.example/9"})
    carrier_rules = {i.rule_id for i in NS.gate_draft(
        _swap(ext_outcome.draft, ext_ref, forged_carrier), ext_authority).issues}
    check("support_snapshot_ref_mismatch" in carrier_rules,
          f"snapshot 载体被换成别的 URL 必须被硬门拒（snapshot 只是来源载体）"
          f"（实际 {sorted(carrier_rules)}）")
    # 载体被伪造必须被拒：逐字段各改一个字节，五次都必须命中同一条门规则（不是碰巧）。
    for carrier_field in ("snapshot_id", "canonical_url", "body_hash",
                          "source_policy_version", "as_of_date"):
        forged_field = _forge_ref(ext_ref, payload_ref={
            **(ext_ref.payload_ref or {}), carrier_field: "tampered"})
        field_rules = {i.rule_id for i in NS.gate_draft(
            _swap(ext_outcome.draft, ext_ref, forged_field), ext_authority).issues}
        check("support_snapshot_ref_mismatch" in field_rules,
              f"snapshot 载体的 {carrier_field!r} 被篡改必须被硬门拒"
              f"（实际 {sorted(field_rules)}）")
    # external 的 locator 是 `loc-1` 的 `whole_payload` 变体，owner 是 snapshot 容器身份。
    # 它**不声称**任何字符位置：`ExternalLocator` 本来就没有字符区间，旧 wire 那个
    # `(容器, 0, len(body_hash))` 只是「长得像区间」而已（§四：不得靠模糊 tuple 猜类型）。
    check(isinstance(ext_ref.locator_ref, dict)
          and ext_ref.locator_ref.get("locator_kind") == "whole_payload"
          and ext_ref.locator_ref.get("owner") == ext_entry.container_identity
          == NS.external_authority_container_id(ext_fact),
          f"external 的 locator 必须是 (snapshot 容器) 上的 `whole_payload` locator，"
          f"实为 {ext_ref.locator_ref!r}")
    check("start" not in ext_ref.locator_ref and "end" not in ext_ref.locator_ref
          and NS.locator_sort_key(ext_ref.locator_ref) == NS.locator_sort_key(
              NS.whole_payload_locator(ext_entry.container_identity)),
          "`whole_payload` 变体不得夹带区间字段（不得把 digest 长度伪装成字符位置）")
    # 诚实边界：硬门的 `support_locator_mismatch` 只对 `evidence_note` 成立 —— 它比的是
    # `authoritative_locator(kind, fact)`，而该入口对其他 kind 一律返回 None（external 的
    # locator 只指向载体，载体本身的身份由 `support_snapshot_ref_mismatch` 校验）。因此
    # **伪造 external locator 不会被门拒**；这条边界必须被钉住并如实上报，而不是伪装成已覆盖。
    check(NS.authoritative_locator("external_snapshot", ext_fact) is None
          and NS.authoritative_locator("topic_pack", None) is None,
          "`authoritative_locator` 只对 evidence_note 给出 locator（其他 kind 一律 None）")
    forged_locator = _forge_ref(ext_ref, locator_ref=NS.whole_payload_locator(
        f"{ext_entry.container_identity}#other"))
    locator_rules = {i.rule_id for i in NS.gate_draft(
        _swap(ext_outcome.draft, ext_ref, forged_locator), ext_authority).issues}
    check("support_locator_mismatch" not in locator_rules,
          "已登记的边界：伪造 external locator 目前**不**触发 support_locator_mismatch"
          "（缺口必须如实上报，直到门补上 external 的 locator 回查）")
    # 反例：external 不带 material，也不得因此被误判成「漏绑 material」。
    expectations, _ambiguous = NS._authority_material_expectations(ext_authority)
    check(not expectations,
          "external 路径 A 不得产生 material 期望（否则会把「不带 material」误判成漏绑）")

    # ==================================================================
    # 21. §16.5 边界③：`FollowUpNeed` 是门前唯一的「请求更多材料」出口
    # ==================================================================
    req_id_business = scan.requirement_ids.get(TOPIC_BUSINESS, "")
    check(bool(req_id_business),
          "topic PackSet 必须给出该主题真实的 Contract 需求 id（否则申请无真实需求可回指）")
    fu_spec = _follow_up("需要补充公司销售模式的口径说明。",
                         target_requirement_id=req_id_business,
                         topic_id=TOPIC_BUSINESS, question_id="q-company_business",
                         aspect_id=ASP_BUSINESS_SALES)
    base_single = _write(
        co_task, authority, section_id="company",
        llm=_StubLlm(_plan(
            candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(scan, "f-1"))])))
    fu_outcome = _write(
        co_task, authority, section_id="company",
        llm=_StubLlm(_plan(
            candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(scan, "f-1"))],
            follow_ups=[fu_spec])))
    check(len(fu_outcome.follow_up_needs) == 1,
          f"补件申请必须从门前唯一出口产出（实际 {len(fu_outcome.follow_up_needs)}）")
    need = fu_outcome.follow_up_needs[0]
    check(need.target_requirement_id == req_id_business
          and need.aspect_id == ASP_BUSINESS_SALES
          and need.section_draft_revision == fu_outcome.draft.draft_revision,
          "补件申请必须指回真实需求、真实 aspect，并挂在同一个 draft revision 上")
    check(need.contract_authorized_scope
          and set(need.contract_authorized_scope)
          >= {ASP_BUSINESS_SALES, TOPIC_BUSINESS, "q-company_business"},
          f"补件申请必须带上 Contract 授权范围（同一口径），实际 {need.contract_authorized_scope}")
    check("follow_up_needs" not in fu_outcome.draft.to_dict()
          and "follow_up_need" not in json.dumps(fu_outcome.draft.to_dict(),
                                                 ensure_ascii=False),
          "补件申请是**独立身份**：不得进 draft 的身份（否则「写不出」会改变 draft 身份）")
    check(fu_outcome.draft.draft_revision == base_single.draft.draft_revision
          and fu_outcome.draft.draft_id == base_single.draft.draft_id,
          "同一权威 + 同一候选集下，补件申请的有无不得改变 draft 身份"
          "（申请是独立身份，不是 draft 的字段）")
    # 反例：申请不得凭空要材料 —— 需求 id / topic / question / aspect 任一项对不上即不成立。
    # 后果的范围（M930-3 返修 §二 2.6）：不成立的是**那一条申请**。它必须落成一条 typed 拒绝
    # 记录（封闭原因码 + 它自己那份响应里的序号 + 可读原因），而**不是**连同一份响应里已经
    # 通过硬门的候选与 Draft 一起消失——真实 run r5 的 financial 节正是这样整节死掉的：
    # 门都过了，死在门后一条填错 topic/aspect 的申请上。整节消失与悄悄丢掉那条申请，都不算
    # 处理「诉求不成立」，因为「提过一条填错的申请」与「没有提过申请」是两件事。
    for override, what, code in (
            ({"target_requirement_id": "tr-forged"},
             "需求 id 不是该主题的真实需求", "target_requirement_mismatch"),
            ({"topic_id": TOPIC_IDENTITY},
             "topic 不属于本节权威输入", "topic_not_in_section"),
            ({"question_id": "q-other"},
             "question 不是该 topic 下的问题", "question_not_under_topic"),
            ({"aspect_id": ASP_IDENTITY_CAPITAL},
             "aspect 不在该 topic 的 Contract 投影内", "aspect_not_in_topic")):
        spec_bad = dict(fu_spec)
        spec_bad.update(override)
        bad_outcome = _write(co_task, authority, section_id="company",
                             llm=_StubLlm(_plan(
                                 candidates=[_cand("c1", facts_by_id["f-1"].text,
                                                   _fact_edge(scan, "f-1"))],
                                 follow_ups=[spec_bad])))
        codes = [r["code"] for r in bad_outcome.follow_up_rejections]
        check(codes == [code] and not bad_outcome.follow_up_needs,
              f"补件申请{what}时：那一条必须落成**恰好一条** typed 拒绝记录（期望 {code}，"
              f"实测 {codes}），且不得成为 FollowUpNeed")
        matched = [r for r in bad_outcome.follow_up_rejections if r["code"] == code]
        check(matched and matched[0]["spec_index"] == 0
              and matched[0]["statement"] == spec_bad["statement"]
              and matched[0]["aspect_id"] == spec_bad["aspect_id"]
              and matched[0]["reason"].startswith("FollowUpNeedRejected:"),
              f"补件申请{what}的拒绝记录必须带**它自己那份响应里的序号**、原文陈述与原样坐标，"
              "并给出可读原因（否则「第几条错了」只能靠 statement 前缀猜）")
        check(bad_outcome.draft.draft_id == base_single.draft.draft_id
              and bad_outcome.draft.draft_revision == base_single.draft.draft_revision,
              f"补件申请{what}不得连带杀死同一份响应里**已经通过硬门**的候选与 Draft"
              "（申请不成立 ≠ 整节不成立；这正是 r5 financial 整节消失的反面）")
    # 结构面（连形状都不成立：陈述为空 / requiredness 不在声明集合内 / 来源类别不在声明集合内）
    # 仍然是 **fail-closed**：它是「这份响应不是一份合法提案束」，与上面「坐标对不上」
    # 不是一类事——后者形状合法、只是指不回真实需求，因此才必须逐条隔离而不是整节消失。
    # C4 起：形状不合法先对**那一批**按 `bsc-1` 反馈一次准确错误（同一批只纠正一次），
    # 仍不合法即停——因此这里脚本给两次同一份非法响应（第二次是纠正那一次的回答）。
    for override, what in (
            ({"statement": "  "}, "申请没有陈述"),
            ({"requiredness": "maybe"}, "requiredness 不在声明集合内"),
            ({"expected_source_class": "whatever"}, "来源类别不在声明集合内")):
        spec_bad = dict(fu_spec)
        spec_bad.update(override)
        _bad_plan = _plan(candidates=[_cand("c1", facts_by_id["f-1"].text,
                                            _fact_edge(scan, "f-1"))],
                          follow_ups=[spec_bad])
        expect_error(
            lambda p=_bad_plan: _write(co_task, authority, section_id="company",
                                       llm=_StubLlm(p, p)),
            PW.PackWriterError, f"补件申请{what}时必须被拒")
    check(not hasattr(PW, "_derive_status_used")
          and callable(getattr(PW, "_derive_status", None)),
          "门后状态派生函数保留但本模块不再使用（状态不是 Writer 的产物）")

    # ==================================================================
    # 22. §六：补件申请的**可申请目录**（`requestable_aspects`）随输入面给出
    # ==================================================================
    # 申请必须同时给出 topic_id / question_id / aspect_id / target_requirement_id，而这三个 id
    # 分属 `requirement_ids`（按 topic）与 `projection.aspects`（逐 aspect 的要求行）两个输入
    # 面——不给目录，模型只能猜，猜错即 fail-closed。目录只是把对应关系摆出来：**只读、不授权、不含
    # 任何事实或材料**，且**只列能通过 `_follow_up_needs` 的行**（否则就是在诱导注定被拒的申请）。
    prose_stub = _StubLlm(_plan(
        candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(scan, "f-1"))]))
    _write(co_task, authority, section_id="company", llm=prose_stub)
    payload = json.loads(prose_stub.calls[0]["messages"][0]["content"])
    catalog = payload["requestable_aspects"]
    check([r["aspect_id"] for r in catalog]
          == sorted({ASP_BUSINESS_MAIN, ASP_BUSINESS_SALES, ASP_BUSINESS_COST}),
          f"目录必须逐条列出本节的 Contract 投影 aspect（升序），实为 "
          f"{[r['aspect_id'] for r in catalog]}")
    check(all(r["topic_id"] == TOPIC_BUSINESS and r["question_id"] == "q-company_business"
              and r["target_requirement_id"] == req_id_business
              and r["status"] == scan.aspect_status[r["aspect_id"]] for r in catalog),
          "目录每行必须与权威逐字一致：topic/question/真实需求 id 与冻结的覆盖状态")
    check(catalog and not any(k in json.dumps(catalog, ensure_ascii=False)
                              for k in ("fact", "material", "claim", "proposal")),
          "目录不得夹带事实/材料/候选：它只说明「可以为什么申请材料」")
    # 目录不是「看起来像」的清单：每一行都必须真的能通过 `_follow_up_needs` 的全部校验。
    for row in catalog:
        row_spec = _follow_up("需要补充该方面的一手材料。",
                              target_requirement_id=row["target_requirement_id"],
                              topic_id=row["topic_id"], question_id=row["question_id"],
                              aspect_id=row["aspect_id"])
        row_outcome = _write(co_task, authority, section_id="company",
                             llm=_StubLlm(_plan(
                                 candidates=[_cand("c1", facts_by_id["f-1"].text,
                                                   _fact_edge(scan, "f-1"))],
                                 follow_ups=[row_spec])))
        check(len(row_outcome.follow_up_needs) == 1
              and row_outcome.follow_up_needs[0].aspect_id == row["aspect_id"],
              f"目录行 {row['aspect_id']} 必须真的可申请（列出来就必须能过门）")
    # 反例：目录**只列**能过的行。给一个「aspect 的问题不属于本任务」的权威，那些行必须**消失**
    # （而不是先列出来、再由门拒）。
    mismatch_task = _task(
        "company", (TOPIC_BUSINESS,), task_id="task-co-question-mismatch",
        questions=(PS.PlannedQuestion(question_id="q-other", question="另一个问题？",
                                      priority="required", topic_id=TOPIC_BUSINESS,
                                      required_aspects=(), impact_scope=("subject",)),))
    mismatch_authority = _company_authority(
        mismatch_task, facts=base_facts, aspects=base_aspects,
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),)),))
    mismatch_scan = PW.scan_topic_pack(mismatch_authority, mismatch_task)
    check(bool(mismatch_scan.aspect_status)
          and not PW._requestable_aspects(mismatch_scan, mismatch_task),
          "问题 id 不属于本任务的 aspect 不得出现在目录里"
          "（目录必须与 `_follow_up_needs` 用同一个「可申请」判据）")

    # ==================================================================
    # 22b. §三 A / 3.1：`projection.aspects` 必须是**要求行**，不是裸 id 表
    # ==================================================================
    # 「要写什么」不能只靠一串英文点号 id 表达。生成器必须同时看到：
    #   * Contract **逐字**的中文业务要求（`requirement_text`）——它是写作目标本身；
    #   * WritingSpec 为该要求分配的槽位（正文角色 / 表格 schema / 引用颗粒度 / 展示层级 /
    #     缺口展示政策 / 期间语言政策）——它们决定这段落怎么写、引用细到哪一级。
    # 两条来源**都不是本模块的措辞**：要求逐字取自 frozen Contract，槽位逐字取自 frozen
    # WritingSpec。因此这里的断言分两半：一半钉「与冻结来源逐字相等」，一半钉「这一行确实
    # 带上了要求，而不只是 id」。
    REAL_REQ_MAIN = "列示报告期内主营业务收入的构成、金额与占比，并说明各业务的经营模式。"
    REAL_REQ_SALES = "说明主要产品的销售模式、定价机制与结算方式及其报告期内的变化。"
    req_authority = _company_authority(
        co_task, facts=base_facts,
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                 _AspectResult(ASP_BUSINESS_SALES, "partial"),
                 _AspectResult(ASP_BUSINESS_COST, "blocked")),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                    requirement_text=REAL_REQ_MAIN),
            _aspect(ASP_BUSINESS_SALES, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                    requirement_text=REAL_REQ_SALES),
            _aspect(ASP_BUSINESS_COST, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)))),))
    req_scan = PW.scan_topic_pack(req_authority, co_task)
    req_stub = _StubLlm(_plan(
        candidates=[_cand("c1", facts_by_id["f-1"].text, _fact_edge(req_scan, "f-1"))]))
    _write(co_task, req_authority, section_id="company", llm=req_stub)
    req_payload = json.loads(req_stub.calls[0]["messages"][0]["content"])
    req_rows = {r["aspect_id"]: r for r in req_payload["projection"]["aspects"]}

    check(set(req_rows) == {ASP_BUSINESS_MAIN, ASP_BUSINESS_SALES, ASP_BUSINESS_COST},
          f"投影行必须逐条覆盖本节已投影的 aspect，实为 {sorted(req_rows)}")
    check(req_rows[ASP_BUSINESS_MAIN]["requirement_text"] == REAL_REQ_MAIN
          and req_rows[ASP_BUSINESS_SALES]["requirement_text"] == REAL_REQ_SALES,
          "投影行必须逐字携带 Contract 的 requirement_text（要求文本不得被改写、截断或省略）")
    check(req_rows[ASP_BUSINESS_COST]["requirement_text"]
          == f"{ASP_BUSINESS_COST} 的要求文本",
          "没有独立要求文本的 aspect 走 fixture 的占位串——行仍然必须有 requirement_text，"
          "不得因为要求缺失就把这一行降级成裸 id")
    # 槽位逐字来自 frozen WritingSpec（不是本模块的再设计，也不是从 Contract 快照另抄一份）。
    spec_slots = {str(m["aspect_id"]): m for m in WS.load_writing_spec(SPEC_PATH).mappings}
    for aspect_id, row in req_rows.items():
        expected = spec_slots[aspect_id]
        check(row["content_role"] == str(expected.get("content_role") or "")
              and row["table_schema"] == expected.get("table_schema")
              and row["citation_granularity"] == str(expected.get("citation_granularity") or "")
              and row["display_tier"] == str(expected.get("display_tier") or "")
              and row["gap_display_policy"] == str(expected.get("gap_display_policy") or "")
              and row["period_language_policy"]
              == str(expected.get("period_language_policy") or ""),
              f"{aspect_id} 的槽位必须逐字等于 WritingSpec 的分配（实为 "
              f"{ {k: row[k] for k in ('content_role', 'table_schema', 'citation_granularity', 'display_tier', 'gap_display_policy', 'period_language_policy')} }）")
        check(row["subsection_id"] == expected["subsection_id"]
              and row["topic_id"] == TOPIC_BUSINESS
              and row["question_id"] == _question_id(TOPIC_BUSINESS),
              f"{aspect_id} 的栏位归属必须是冻结槽位 + 权威 topic/question，不得自报")
    # 表明「表格」的槽位必须真的把表名摆出来：`paragraph_and_table` 只说了形态，
    # `table_schema` 才说了「表长什么样」，两者缺一，模型只能自己发明列。
    table_rows = [r for r in req_rows.values() if "table" in str(r["content_role"])]
    check(bool(table_rows) and all(r["table_schema"] for r in table_rows),
          f"声明了表格的投影行必须同时给出 table_schema，实为 "
          f"{[(r['aspect_id'], r['table_schema']) for r in table_rows]}")
    # 状态**原样**来自 topic runtime：缺口不得在请求面就已经被写成完整结论。
    check(req_rows[ASP_BUSINESS_COST]["status"] == req_scan.aspect_status[ASP_BUSINESS_COST]
          == "blocked"
          and req_rows[ASP_BUSINESS_SALES]["status"] == "partial",
          "投影行的 status 必须是 topic runtime 的原始状态（不改写、不美化）")
    # 反例：Contract 侧没有 requirement_text 时**不得**降级成裸 id 行——那会让「要写什么」
    # 重新退化成 id，且这种退化在真实运行里不可见（模型仍会输出候选，只是目标错了）。
    blank_authority = _company_authority(
        co_task, facts=base_facts,
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                    requirement_text=" "),)),))
    blank_scan = PW.scan_topic_pack(blank_authority, co_task)
    expect_error(
        lambda: PW._projection_aspects_payload(
            projections["company"], (ASP_BUSINESS_MAIN,), blank_scan),
        PW.PackWriterError, "Contract requirement_text 为空时投影行必须 fail-closed")
    # 反例：投影里解析不出槽位的 id 不得被降级成裸 id 行（身份不一致必须显式失败）。
    expect_error(
        lambda: PW._projection_aspects_payload(
            projections["company"], ("company_business_main.not_in_spec",), req_scan),
        PW.PackWriterError, "不在本节 Contract 投影内的 aspect 必须 fail-closed")
    # prompt 资产三侧一致（§五 2/3）：登记的「资产名@修订号」必须与资产自报一致，且正文被改过
    # 即拒。3.1 改了资产正文（→ proposals-4），3.3 又改了输出纪律（→ proposals-5），3.6 又加了
    # 代理口径纪律（→ proposals-6），返修 P2 又统一了口径并加子句纪律（→ proposals-7），
    # M930-3 定点返修加了**分批请求**（输入里的 `batch`，→ 资产 v6 / proposals-8），
    # M930-3 返修又收窄了**输出面**（支撑边只写短别名 `ref`，身份由系统展开；本批结算，
    # → 资产 v7 / proposals-9），返修 ④ 又给 materials 行加了**来源角色**并新增一条期间纪律
    # （→ proposals-10），恢复材料驱动写作又倒转了**写作顺序**（先自然草稿、后逐原子候选，
    # 输出面新增 `natural_prose_draft`，→ 资产 v8 / proposals-11），本批再给草稿单元加了**第二条
    # 出处轴**并让「有候选、无草稿」成为 typed failure（→ proposals-12），本批再把请求示例的
    # **剩余三格**按输入面分档（→ proposals-13），本批再加**逐批支撑范围**块
    # `batch_support_scope` 并写明「整节有材料 ≠ 本批写得出」「本批零候选 ⇒ 三个内容键全空」
    # （→ proposals-14），本批再写「材料披露的写法：三条日期轴」（归属语不由写者写、不得不成
    # 期间地写成「一直如此」、新旧实质差异不得抹平、新闻事件日与发布日分开、披露日未知就标未知
    # → proposals-15），本批再改**草稿闭合的读法**（键**集合**一一对上，但同一条候选**可以**
    # 出现在多个草稿单元里；多 occurrence 逐个核验出处，→ proposals-16），因此三侧必须同时
    # 前进，不能只改代码常量。
    check(PW.NARRATION_PROMPT_REVISION == "proposals-16"
          and PW.NARRATION_PROMPT_VERSION.endswith("@proposals-16")
          and PW.NARRATION_PROMPT_ASSET == "pack_section_writer_proposals_v12",
          "输入面/输出纪律/口径纪律/子句纪律/分批纪律/别名纪律/期间纪律/写作顺序/草稿出处/"
          "示例分档变了，prompt 修订号必须前进（proposals-3 = 只看得到裸 id；proposals-4 = 要求行；"
          "proposals-5 = 栏目覆盖纪律；"
          "proposals-6 = 代理口径限定语必须逐字保留；"
          "proposals-7 = 路径 A/栏目覆盖口径统一 + 候选为无句末标点的原子子句；"
          "proposals-8 = 分批请求：本批只回答 projection.aspects，其余输入面仍是完整的；"
          "proposals-9 = 支撑边只写短别名 ref（身份由系统从被引用的那一行展开），"
          "草稿单元/补件诉求/必需事实的义务按本批结算；"
          "proposals-10 = materials 行带来源角色 source_role（同类较旧材料不得写成当前状态）；"
          "proposals-11 = **先写自然草稿、再为草稿里每个事实原子单独提交候选**：输出面新增 "
          "`natural_prose_draft`（先于候选），候选不再是「拼文材料」而是草稿里那几句话的原子账；"
          "proposals-12 = 草稿单元的出处分**两条互斥的轴**（材料行 / 权威事实行，恰有一条非空）"
          "——材料面合法为空的节（财务节）也能写草稿，且出处**不**授权事实；同时「有候选、"
          "无草稿」不再是静默容忍的缺省；"
          "proposals-13 = 请求示例的**剩余三格**（候选首边 / context 边 / 补件 budget_hint）"
          "也按输入面分档——示例指向的表必须是本请求真的给了行的表，补件预算不得为空；"
          "proposals-14 = 请求面新增**逐批支撑范围** `batch_support_scope`（本批各 topic 的可"
          "引用材料行、每栏目研究侧原样记录的 status、绑定它的权威事实行），并把「整节材料目录"
          "非空不等于本批每个栏目都写得出」「本批一条候选都交不出时三个内容键一律空数组」"
          "写成纪律——缺失陈述不是缺口，缺口由系统判；"
          "proposals-15 = **材料披露的写法：三条日期轴**——归属语（「据某年年度报告披露」）由"
          "系统在门后按已登记来源确定性附加，**不由写者写**；不得把「该材料披露的情况」升格成"
          "「一直如此」；新旧材料存在实质差异时不得用相似文本抹平；新闻优先事件发生/生效日、"
          "只有发布日期时只能写「某日发布的报道提及……」；披露日未知就标未知；`report_as_of`"
          "与上面三条都不是一回事；"
          "proposals-16 = **草稿闭合的读法**——「草稿声明的原子键与候选集一一对上」改写成"
          "「键**集合**一一对上，但同一条候选**可以**出现在多个草稿单元里」：多 occurrence 不是"
          "免检通道，闭合核对**逐个 occurrence** 拿该候选自己的支撑边核对出处（材料 ID、来源身份/"
          "报告期、来源角色；事实轴比事实行），错来源、错版本照拒；同一份来源内写两遍仍然无意义，"
          "把两年相似表述并成「始终如此」仍然不行）")
    check("proposals-16" in str(req_stub.calls[0]["system"])
          and PW.NARRATION_PROMPT_ASSET in str(req_stub.calls[0]["system"]),
          "实际加载的资产必须自报新资产名@修订号（记录版本 = 实际加载版本）")

    # ==================================================================
    # 22c. §三 A / 3.2：`presentation_profile` 必须是**真实呈现规格**，不是空壳
    # ==================================================================
    # 修复前两个投影点都按**不存在的**字段名取值（`profile_id` / `tables` / `paragraphs` /
    # `citation_style` / `period_language_policy`），于是「在做投影」这件事看起来成立、
    # payload 却恒为 `{"schema_version": ...}`。因此这里的断言必须钉住**内容**，而不只是形状：
    # 逐字等于真实 YAML 载入的规格，且不得出现任何真实对象上没有的键。
    real_profile = PP.load_presentation_profile(PROFILE_PATH)
    sent_profile = json.loads(req_stub.calls[0]["messages"][0]["content"])["presentation_profile"]
    check(set(sent_profile) == set(PP._PRESENTATION_PAYLOAD_FIELDS),
          f"投入请求面的呈现视图必须恰好是声明的字段集，实为 {sorted(sent_profile)}")
    check(sent_profile["presentation_profile_id"] == real_profile.presentation_profile_id
          and sent_profile["profile_name"] == real_profile.profile_name
          and sent_profile["writing_spec_ref"] == real_profile.writing_spec_ref
          and sent_profile["status"] == real_profile.status
          and sent_profile["schema_version"] == real_profile.schema_version,
          "呈现视图的每项必须逐字等于已载入的呈现规格（不得改写、不得补默认值）")
    check(sent_profile["display_rules"] == PW._jsonable(real_profile.display_rules)
          and sent_profile["fold_levels"] == PW._jsonable(real_profile.fold_levels)
          and sent_profile["scope"] == PW._jsonable(real_profile.scope)
          and sent_profile["appendix_items"] == PW._jsonable(real_profile.appendix_items)
          and sent_profile["screenshot_regions"] == PW._jsonable(real_profile.screenshot_regions),
          "呈现规则 / 折叠层级 / 呈现边界 / 附录项 / 截图区必须真的进请求面"
          "（它们正是「呈现规格」的内容，不是装饰）")
    check(bool(sent_profile["display_rules"])
          and set(PP._REQUIRED_DISPLAY_RULES) <= set(sent_profile["display_rules"]),
          "呈现规格声明的必备展示规则必须逐条出现在请求面里")
    fabricated_names = {"profile_id", "tables", "paragraphs", "citation_style",
                        "period_language_policy"}
    check("raw" not in sent_profile
          and not (fabricated_names & set(sent_profile)),
          "视图不得夹带 `raw`，也不得再出现那五个**真实对象上不存在**的字段名"
          "（它们正是修复前 payload 恒为空的原因）")
    # 视图字段表必须与 dataclass **双向**同步：漏一个 = 新字段静默不进请求面；
    # 多一个 = 视图里有不存在的字段，等于假装投影。
    PP.assert_payload_fields_current()
    check(set(PP._PRESENTATION_PAYLOAD_FIELDS)
          == set(PP.PresentationProfile.__dataclass_fields__) - {"raw"},
          "呈现视图字段表必须恰好等于 dataclass 字段集减去 raw")
    # 反例：形状替身（没有这些字段的对象）如实给出 `{}`，而不是伪造一份内容，
    # 也不是把缺失字段补成空串（那会让「规格里没这项」与「规格说这项是空」不可区分）。
    check(PP.presentation_payload(object()) == {},
          "没有声明呈现字段的对象必须得到空视图（如实，不伪造、不补默认值）")
    # 反例：字段表漂移必须当场失败（两条方向都要能被抓住）。
    drifted_missing = tuple(f for f in PP._PRESENTATION_PAYLOAD_FIELDS if f != "display_rules")
    drifted_extra = PP._PRESENTATION_PAYLOAD_FIELDS + ("tables",)
    for drift, what in ((drifted_missing, "漏字段"), (drifted_extra, "多字段")):
        original = PP._PRESENTATION_PAYLOAD_FIELDS
        PP._PRESENTATION_PAYLOAD_FIELDS = drift
        try:
            expect_error(PP.assert_payload_fields_current, PP.PresentationProfileError,
                         f"呈现视图字段表{what}必须被拒")
        finally:
            PP._PRESENTATION_PAYLOAD_FIELDS = original
    check(PP.presentation_payload(real_profile) != {"schema_version":
                                                    real_profile.schema_version},
          "真实呈现规格的视图不得退化成「只有 schema_version」——那正是 3.2 修复前的形态")

    # ==================================================================
    # 22d. §三 A / 3.3：某个 Contract 栏目零产出时的**一次**栏目定向补足
    # ==================================================================
    # 「按栏目分步写作」不是「多问几遍」：判据分三层——(a) **什么时候**才触发（且只触发一次）；
    # (b) 第二次调用的**输入面**（原请求逐字保留、只追加定向说明，不裁剪/不换 prompt/不换模型）；
    # (c) 它仍是一条**被整束拒绝**的生成，必须留下 typed 审计，且「被拒束数 + 被采信 1 = 调用数」
    # 这条不变量继续成立。栏目覆盖本身**不是**门：第二次仍补不齐就如实采信并留痕，而不是把整节
    # 判失败（缺口必须由 Contract 必需事实那条规则产生，不能由「栏目没写满」冒充）。
    ASP_SUB = "company_subsidiaries.major_subsidiaries"          # co-h3
    REQ_SUB = "列示报告期内纳入合并范围的主要子公司及其持股比例。"
    check(ASP_SUB != ASP_BUSINESS_MAIN,
          "反例前置：本组需要两个**不同栏目**的 aspect，否则测的不是栏目覆盖")
    focus_projection = projections["company"]
    check(focus_projection.aspect(ASP_BUSINESS_MAIN).subsection_id
          != focus_projection.aspect(ASP_SUB).subsection_id,
          "反例前置：两个 aspect 必须分属不同 subsection（co-h4 vs co-h3）")
    focus_authority = _company_authority(
        co_task,
        facts=(base_facts[0],
               _fact("f-2", "公司纳入合并范围的主要子公司共 7 家。", (ASP_SUB,))),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                 _AspectResult(ASP_SUB, "covered")),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)),
            _aspect(ASP_SUB, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                    requirement_text=REQ_SUB))),))
    focus_scan = PW.scan_topic_pack(focus_authority, co_task)
    check(focus_scan.aspect_status[ASP_BUSINESS_MAIN] == "covered"
          and focus_scan.aspect_status[ASP_SUB] == "covered",
          "反例前置：两个 aspect 都必须是**已覆盖**状态——栏目零产出与状态无关，"
          "不得靠「把它标成 partial」来解释")
    check("subsection_uncovered" in PW.PROPOSAL_SET_REJECTION_KINDS,
          "栏目定向必须有自己的 typed 拒绝原因（复用既有词汇会让它读起来像别的失败）")
    # 第一束只覆盖 co-h4：结构合法、身份闭得上，唯独 co-h3 一个候选都没有。
    partial_plan = _plan(candidates=[
        _cand("c1", "公司主营业务为动力电池系统的研发、生产与销售。",
              _fact_edge(focus_scan, "f-1"))])
    full_plan = _plan(candidates=[
        _cand("c1", "公司主营业务为动力电池系统的研发、生产与销售。",
              _fact_edge(focus_scan, "f-1")),
        _cand("c2", "公司纳入合并范围的主要子公司共 7 家。",
              _fact_edge(focus_scan, "f-2"))])
    focus_stub = _StubLlm(partial_plan, full_plan)
    focus_outcome = _write(co_task, focus_authority, section_id="company", llm=focus_stub)
    check(len(focus_stub.calls) == 2,
          f"栏目零产出必须触发**恰好一次**栏目定向调用（实际 {len(focus_stub.calls)} 次）")
    check(focus_outcome.llm_calls == 2 and len(focus_outcome.llm_trace) == 2,
          "调用计数与 trace 必须都是 2（一次被拒 + 一次被采信）")
    first_content = str(focus_stub.calls[0]["messages"][0]["content"])
    second_content = str(focus_stub.calls[1]["messages"][0]["content"])
    check(second_content.startswith(first_content)
          and len(second_content) > len(first_content),
          "第二次调用的输入面必须**逐字包含**原请求：栏目前提是补充说明，不是重写输入"
          "（裁剪输入面正是本批反复拒绝的形态）")
    appended = second_content[len(first_content):]
    check(REQ_SUB in appended and ASP_SUB in appended,
          f"栏目定向说明必须逐字给出被点名栏目的要求（{REQ_SUB!r}）与其 aspect_id")
    check(f"{ASP_BUSINESS_MAIN} 的要求文本" not in appended,
          "定向说明**只**列零产出的栏目：把已经写到的栏目也列进去，就不是定向而是复述")
    check(focus_stub.calls[0]["system"] == focus_stub.calls[1]["system"]
          and focus_stub.calls[0]["prompt_version"] == focus_stub.calls[1]["prompt_version"],
          "两次调用必须是同一个 system 与同一个 prompt 版本"
          "（换任何一样都会让「同一个 prompt 版本」对着两种输入纪律）")
    # (c) 被拒的那一束留 typed 审计，且身份与「产出过零份 Draft 的那一次」逐一对应。
    check(len(focus_outcome.rejections) == 1,
          f"栏目零产出必须恰好留一条 typed 拒绝审计（实际 {len(focus_outcome.rejections)} 条）")
    rec = focus_outcome.rejections[0]
    check(rec.rejection_kind == "subsection_uncovered",
          f"拒绝原因必须是 subsection_uncovered（实际 {rec.rejection_kind!r}）")
    # 身份是**内容派生的**（`ccand_…`/`psr_…`），不是模型自报的 `candidate_key`——因此这里
    # 钉的是「完整有序且可回查」：恰好 1 条候选 / 1 条 proposal，指向那次调用的日志坐标。
    #
    # 「可回查」**不再**等于「在被采信的那一版里查得到同一个 id」：`draft_revision` 逐轮不同
    # （`attempt` 进 `derive_draft_revision`，见 M930-3 P1），因此同一句候选在被拒那一轮与
    # 被采信那一轮里逐字重现、派生 id 却必须不同。若两轮压成同一组身份，「被拒的那一束」与
    # 「被采信的那一束」在产物里就无从区分——那正是「把 26 条里违规的 13 条原地删掉后放行」
    # 的形态。两条性质合起来才是可回查：原文在日志（`narration_call_id`），逐轮身份在审计记录。
    accepted_by_text = {c.claim_text: str(c.candidate_id)
                        for c in focus_outcome.draft.claim_candidates}
    rejected_text = "公司主营业务为动力电池系统的研发、生产与销售。"
    check(len(rec.candidate_ids) == 1 and len(rec.proposal_ids) == 1
          and rec.narration_call_id == "call-1",
          f"被拒束必须保留**完整有序**身份（候选 id / proposal id）并指向那次调用的日志坐标"
          f"（实际 candidates={rec.candidate_ids!r} / proposals={rec.proposal_ids!r} / "
          f"call={rec.narration_call_id!r}）")
    check(rejected_text in accepted_by_text
          and set(rec.candidate_ids).isdisjoint(set(accepted_by_text.values())),
          f"同一句候选在被采信那一轮逐字重现，但两轮派生的 `candidate_id` 必须不同"
          f"（逐轮修订若不起作用，被拒与被采信就会共用一组身份）；实为 "
          f"rejected={set(rec.candidate_ids)} accepted={sorted(accepted_by_text.values())}")
    check(rec.attempt == 1,
          "被拒束的 attempt 是它在本节生成序列里的序号（第 1 次）")
    check("co-h3" in rec.rejection_detail and ASP_SUB in rec.rejection_detail,
          "拒绝原因必须点名**是哪个栏目**零产出（否则审计里读不出「为什么这一束不被采信」）")
    check(len(focus_outcome.rejections) + 1 == focus_outcome.llm_calls,
          "「被拒束数 + 被采信 1 = 调用数」这条不变量必须继续成立")
    # (c2) §二 3 / 3.4：逐候选 typed 审计。被点名栏目是**结构化**给出的（不只在散文 detail 里），
    # 且这条被拒束的每一条候选都有一条原因——「没被单独点名」也**必须**说出来，
    # 否则磁盘上「什么原因都没有」与「被采信」不可区分。
    check(tuple(rec.named_subsections) == ("co-h3",),
          f"被点名的栏目必须结构化落进审计（实际 {rec.named_subsections!r}）")
    check(tuple(a.candidate_id for a in rec.candidate_audit) == rec.candidate_ids,
          f"逐候选审计的键集必须**恰好**等于该束的完整有序候选身份（实际 "
          f"{[a.candidate_id for a in rec.candidate_audit]} vs {rec.candidate_ids}）")
    check([a.reasons for a in rec.candidate_audit] == [("not_individually_implicated",)],
          f"这一束的问题是**缺了一条候选**（栏目零产出），不是某条候选踩线：逐候选原因必须"
          f"如实记成「未被单独牵连」，而不是把整束的原因摊到候选头上"
          f"（实际 {[a.reasons for a in rec.candidate_audit]}）")
    check(rec.to_dict()["whole_set_rejected"] is True
          and json.loads(json.dumps(rec.to_dict(), ensure_ascii=False)) == rec.to_dict(),
          "落盘形态必须显式声明「整束被拒」且可 JSON 往返（该审计没有通过侧）")
    # (c3) 反例 B 之后在本文件下部（`repeat_outcome` 定义之后）继续钉。
    # 被采信的是**第二次**那一束：新草稿修订携带完整（这里是两条候选的）提案集。
    check(len(focus_outcome.draft.claim_candidates) == 2
          and len(focus_outcome.draft.proposed_support_refs) == 2,
          f"被采信的必须是第二次的**完整**提案集（实际候选 "
          f"{len(focus_outcome.draft.claim_candidates)} 条 / proposal "
          f"{len(focus_outcome.draft.proposed_support_refs)} 条）")
    check(sorted(str(c.claim_text) for c in focus_outcome.draft.claim_candidates)
          == sorted(["公司主营业务为动力电池系统的研发、生产与销售。",
                     "公司纳入合并范围的主要子公司共 7 家。"]),
          "被采信的草稿修订必须**同时**包含两个栏目的候选，而不是「原样加上漏掉的那条」之外"
          "又少了一条")
    # 反例 A：栏目覆盖齐全时**一次都不多发**（这条通道不是「每节固定加一次」）。
    complete_stub = _StubLlm(full_plan)
    complete_outcome = _write(co_task, focus_authority, section_id="company",
                              llm=complete_stub)
    check(len(complete_stub.calls) == 1 and not complete_outcome.rejections
          and complete_outcome.llm_calls == 1,
          f"覆盖齐全时不得触发栏目定向（实际 {len(complete_stub.calls)} 次调用 / "
          f"{len(complete_outcome.rejections)} 条拒绝）")
    # 反例 B：第二次**仍然**零产出时不再追问（`MAX_SUBSECTION_FOCUS_PASSES` 把它限成一次）：
    # 整节如实被采信（栏目没写满不是门），但那条审计留在产物里，缺口由别的规则去产生。
    repeat_stub = _StubLlm(partial_plan, partial_plan)
    repeat_outcome = _write(co_task, focus_authority, section_id="company", llm=repeat_stub)
    check(len(repeat_stub.calls) == 2
          and [r.rejection_kind for r in repeat_outcome.rejections] == ["subsection_uncovered"],
          f"栏目定向必须**恰好一次**：第二次仍零产出也只留一条审计、不再追问"
          f"（实际 {len(repeat_stub.calls)} 次调用 / "
          f"{[r.rejection_kind for r in repeat_outcome.rejections]}）")
    check(len(repeat_outcome.draft.claim_candidates) == 1,
          "第二次仍补不齐时如实采信那一束（栏目覆盖不是门），不伪造也不整节判失败")
    # (c3) 栏目零产出是**结构化** kind：它必须逐候选给出原因，且原因必须是「未被单独牵连」——
    # 这一束的病是「少了一条候选」，不是「某条候选踩线」，把整束的原因摊到候选头上就是发明归属。
    check(repeat_outcome.rejections[0].candidate_audit
          and tuple(repeat_outcome.rejections[0].named_subsections) == ("co-h3",)
          and [a.reasons for a in repeat_outcome.rejections[0].candidate_audit]
          == [("not_individually_implicated",)],
          f"栏目零产出必须逐候选留原因、并结构化给出被点名的栏目，实为 "
          f"{repeat_outcome.rejections[0].to_dict()}")
    # 反例 C：栏目定向那一次失败即整节 fail-closed，且**不**借重试额度再发一次——
    # 否则「格式抖动」与「栏目真的没材料」会被混成同一类结果，调用上界也不再可推导。
    broken_stub = _StubLlm(partial_plan, "这不是 JSON")
    try:
        _write(co_task, focus_authority, section_id="company", llm=broken_stub)
        check(False, "栏目定向那一次 schema 不合格时必须整节 fail-closed")
    except PW.ProposalSetRejectedError as exc:
        kinds = [r.rejection_kind for r in exc.rejections]
        check(kinds == ["subsection_uncovered", "schema_invalid"],
              f"两次失败必须各自留下自己的 typed 原因（实际 {kinds}）")
        check([r.attempt for r in exc.rejections] == [1, 2],
              f"attempt 必须**单调递增**，不得因栏目定向归零"
              f"（实际 {[r.attempt for r in exc.rejections]}）")
        check(len(broken_stub.calls) == 2,
              f"栏目定向失败后不得再借重试额度重发（实际 {len(broken_stub.calls)} 次调用）")
    # 反例 D：栏目定向**不是**重试——把 `MAX_SUBSECTION_FOCUS_PASSES` 抬到 2 必须能被看见
    # （证明这条通道的「恰好一次」来自预算常量，而不是来自「循环碰巧只走了一次」）。
    check(PW.MAX_SUBSECTION_FOCUS_PASSES == 1,
          "栏目定向的独立额度当前必须是 1（改用例必须同时改预算推导，见 "
          "`run_m930_3_acceptance._narration_structural_bound`）")
    # 反例 E：覆盖判据是**事实侧**——只有路径 A 边能让栏目算作被覆盖。本节事实目录里**有**
    # 该栏目的事实、而模型只给了路径 B 候选时，栏目仍算零产出（背景性描述不得冒充对事实型
    # 栏目要求的回答），且因为事实**在**，值得如实问一次。
    pb_focus_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-pb-focus")
    pb_focus_material = _material_of_identity(
        "m-focus-1", "evidence:co-focus-1",
        text="该公司主营业务由动力电池与储能两大业务条线构成。")
    pb_focus_authority = _company_authority(
        pb_focus_task,
        facts=(_fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                     (ASP_BUSINESS_MAIN,)),),
        materials=(pb_focus_material,),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                    _question_id(TOPIC_BUSINESS)),)),))
    pb_focus_pack_id = str(pb_focus_authority.pack_set.packs[0].pack_id)
    pb_focus_plan = _plan(candidates=[
        _cand("c1", "主营业务由动力电池与储能两大业务条线构成。",
              _material_edge(pb_focus_pack_id, "m-focus-1"))],
        # 本节事实 f-1 的引用会**自动补**一份 material，因此「第一份成员」不是这条候选引用的
        # 那份；出处逐候选给对本行（`_member_ref` 按 manifest 自己的成员顺序取）。
        draft_ref=_member_ref(pb_focus_authority, task_id=str(pb_focus_task.task_id),
                              section_id="company", material_id="m-focus-1"))
    pb_focus_stub = _StubLlm(pb_focus_plan, pb_focus_plan)
    pb_focus_outcome = _write(pb_focus_task, pb_focus_authority, section_id="company",
                              llm=pb_focus_stub)
    check(len(pb_focus_stub.calls) == 2
          and [r.rejection_kind for r in pb_focus_outcome.rejections]
          == ["subsection_uncovered"],
          f"只有路径 B 候选时该栏目仍算**零产出**：背景性描述不得冒充对事实型栏目要求的回答"
          f"（实际 {len(pb_focus_stub.calls)} 次调用 / "
          f"{[r.rejection_kind for r in pb_focus_outcome.rejections]}）")
    check(len(pb_focus_outcome.draft.claim_candidates) == 1
          and not pb_focus_outcome.gate_result.blocking,
          "即便如此，路径 B 候选本身仍是**合法**内容：栏目未覆盖不构成对它的事后否决"
          "（栏目覆盖不是门，它只触发一次定向补足）")
    check(len(pb_focus_outcome.rejections) + 1 == pb_focus_outcome.llm_calls,
          "栏目零产出的路径上，「被拒束数 + 被采信 1 = 调用数」同样必须成立")
    # 反例 F：该栏目在**本节事实目录里一条事实都没有**时**不问**——路径 A 只能绑事实目录里的
    # 行，第二次调用在原理上补不上它，再问一次就是纯循环（而且会诱导模型编造）。
    # 这种栏目应当由「Contract 必需事实未取得」那条规则形成缺口/gap，不由这里反复追问。
    no_fact_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-nofact")
    no_fact_authority = _company_authority(
        no_fact_task, facts=(),
        materials=(_material_of_identity("m-nofact-1", "evidence:co-nofact-1",
                                         text="该公司主营业务由动力电池构成。"),),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                    _question_id(TOPIC_BUSINESS)),)),))
    no_fact_pack_id = str(no_fact_authority.pack_set.packs[0].pack_id)
    no_fact_plan = _plan(candidates=[
        _cand("c1", "主营业务由动力电池构成。",
              _material_edge(no_fact_pack_id, "m-nofact-1"))])
    no_fact_stub = _StubLlm(no_fact_plan)
    no_fact_outcome = _write(no_fact_task, no_fact_authority, section_id="company",
                             llm=no_fact_stub)
    check(len(no_fact_stub.calls) == 1 and not no_fact_outcome.rejections,
          f"本节事实目录里没有该栏目的事实时必须**不**追问（注定失败的请求不是质量措施）："
          f"实际 {len(no_fact_stub.calls)} 次调用 / "
          f"{[r.rejection_kind for r in no_fact_outcome.rejections]}")
    # 同一件事在函数层钉一遍：`_uncovered_subsections` 报「没写到」，`_focusable_subsections`
    # 报「值得再问」——两者必须可分辨（只有后者驱动调用）。
    check(callable(PW._focusable_subsections) and callable(PW._uncovered_subsections),
          "「未覆盖」与「值得再问」必须是两个可分别调用的判据，不是同一个布尔")
    # (d) §二 3 / 3.4 的真实归属：一束里**只有一条**候选踩了路径 B 高风险面时，逐候选审计必须
    # 把原因落在它自己头上，而**不是**把整束的原因摊到每一条候选。这正是「1 与 20 是两种不同
    # 结论」的落点——旧形态只能从一段 detail 文本里搜，读的人分不出是几条踩线。
    mix_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-pb-mix")
    mix_material = _material_of_identity(
        "m-mix-1", "evidence:co-mix-1",
        text="该公司主营业务由动力电池与储能两大业务条线构成。")
    mix_authority = _company_authority(
        mix_task, facts=(),
        materials=(mix_material,),
        aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
        requirements=(_Req(TOPIC_BUSINESS, (
            _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                    _question_id(TOPIC_BUSINESS)),)),))
    mix_pack_id = str(mix_authority.pack_set.packs[0].pack_id)
    mix_plan = _plan(candidates=[
        _cand("c1", "主营业务由动力电池与储能两大业务条线构成。",
              _material_edge(mix_pack_id, "m-mix-1")),
        _cand("c2", "该事项不适用。", _material_edge(mix_pack_id, "m-mix-1"))])
    # 桩**两次都给同一份**提案集：裁出轮不调用模型（`model_calls_added` 恒为 0），因此第二次
    # 那一份**不该被取用**。给了它、再断言调用次数，就能证明「零新增调用」不是靠桩恰好没有
    # 第二份返回凑出来的。
    mix_stub = _StubLlm(mix_plan, mix_plan)
    check(NS.high_risk_surface_tokens("该事项不适用。"),
          "反例前置条件：第 2 条候选的文本必须真的命中高风险表面（否则这条用例测不到东西）")
    # §一.2 补充裁决（`cco-1`）：**含高风险表面的那一条被逐条拒掉，其余候选以新修订继续走链**。
    # 改前这里断言的是「整束被拒」——那是**过度**的：不合格的只有被点名的那一条，被连带作废的
    # 却是同一次模型输出里其余完全合法的候选。原束的完整有序身份、逐候选 typed 原因与整束留档
    # 一字不改（`whole_set_rejected=True` 保留）；被采信的那一份是**新修订**，不是原束删几条后
    # 的子集冒充原束。
    mix_outcome = _write(mix_task, mix_authority, section_id="company", llm=mix_stub,
                         policy=PW.WriterPolicy(max_llm_retries=1))
    check(len(mix_stub.calls) == 1,
          f"裁出**不新增任何模型调用**：被裁掉的那一条与新修订都来自已经付过费的那一次返回"
          f"（实为 {len(mix_stub.calls)} 次调用）")
    recs = mix_outcome.rejections
    check([r.rejection_kind for r in recs] == ["path_b_high_risk_surface"],
          f"原束必须留下它**自己**那条 typed 原因（实为 {[r.rejection_kind for r in recs]}）")
    # `whole_set_rejected` 是**投影到产物**的字段（记录对象本身没有这个属性）：留档说的是
    # 「这条记录讲的是整束为什么被拒」，因此断言读它的产物形态，而不是读一个对象属性。
    check(recs and recs[0].to_dict().get("whole_set_rejected") is True
          and recs[0].named_subsections == (),
          "原束照旧**整束留档**（`whole_set_rejected=True`）：裁出不改写这条记录，"
          "整束被拒与栏目点名仍是两件事")
    audit = recs[0].candidate_audit if recs else ()
    check(tuple(a.candidate_id for a in audit) == recs[0].candidate_ids
          and len(audit) == 2,
          f"逐候选审计必须覆盖**原束整束**（2 条），与实际输出逐一对应（实为 "
          f"{[a.candidate_id for a in audit]} vs {recs[0].candidate_ids if recs else ()}）")
    # 逐候选原因是**四条正交轴各自的判定**的并集，不是「一束里只挑一条最重的」。因此那条
    # 踩线的候选带**两个**原因——它自己携带了高风险表面（`srsc` 之外那条），而它的文本又
    # 不是本节材料正文的**严格抽取式**子串（`srsc-2`，逐字抄写才是可核实的形态）。这不是
    # 一条原因被另一条吃掉，也不是原因摊到了别的候选头上：**没踩线的那条一个字都没多**。
    check([a.reasons for a in audit]
          == [("not_individually_implicated",),
              ("path_b_high_risk_surface", "path_b_unproven_current_state")],
          f"原因必须落在**它自己**那条候选上：没踩线的那条记「未被单独牵连」，踩线那条只带"
          f"它自己的原因（实为 {[a.reasons for a in audit]}）")
    check(audit[1].surfaces == NS.high_risk_surface_tokens("该事项不适用。")
          and audit[1].surfaces and audit[0].surfaces == (),
          f"高风险表面必须**逐字**落在它自己那条候选上（实为 "
          f"{audit[1].surfaces!r} / {audit[0].surfaces!r}）")
    # 裁出对账本身必须进产物：原束每条候选 → 去向（幸存 / 被排除及其原因）→ 新修订身份。
    carve = recs[0].carve_out if recs else None
    # 旧身份（幸存者）在 `decision` 上，新身份在 `destinations`/`next_candidate_ids` 上——
    # 两个字段**不同名不同义**，不得互用（这正是「旧束删几条冒充旧束」读得出来的地方）。
    # 身份全部是内容寻址的哈希（`ClaimCandidate.candidate_id` 把 `draft_revision` 计入），
    # 所以断言按**位置与逐字文本**对读，不写死 "c1"/"c2" 之类的模型侧标签。
    _mix_survivor_text = "主营业务由动力电池与储能两大业务条线构成。"
    check(carve is not None
          and tuple(carve.decision.source_candidate_ids) == recs[0].candidate_ids
          and tuple(carve.decision.surviving_candidate_ids) == (recs[0].candidate_ids[0],)
          and [e.claim_text for e in carve.decision.excluded] == ["该事项不适用。"]
          and tuple(d.candidate_id for d in carve.destinations) == recs[0].candidate_ids,
          f"裁出必须逐条对账「原束每条候选去哪了」（实为 "
          f"{carve.to_dict() if carve else None}）")
    # 「新修订」必须是**另一份身份**，不是原束删几条后的同一份：`candidate_id` 把
    # `draft_revision` 计入身份，因此幸存者的新身份必然与原身份不同。这一条是把「旧束洗白」
    # 读得出来的地方——若新身份 == 旧身份，说明链上根本没有新修订，只是把原束就地删了几条。
    check(carve is not None and carve.model_calls_added == 0
          and carve.decision.to_revision
          and carve.next_candidate_ids
          and tuple(carve.next_candidate_ids)
          == tuple(c.candidate_id for c in mix_outcome.draft.claim_candidates)
          and not set(carve.next_candidate_ids) & set(carve.decision.source_candidate_ids)
          and carve.destinations[0].claim_text == _mix_survivor_text
          and carve.destinations[0].next_candidate_id,
          "裁出必须公开「零新增调用」与新修订身份（原束 → 新修订是可回查的两份身份，"
          f"不是同一份被就地删改；实为 {carve.to_dict() if carve else None}）")
    carved_texts = [c.claim_text for c in mix_outcome.draft.claim_candidates]
    check(carved_texts == ["主营业务由动力电池与储能两大业务条线构成。"],
          f"被采信的正文只含幸存候选，**逐字**不含被排除的那一条（实为 {carved_texts}）")
    check("该事项不适用。" not in "".join(carved_texts),
          "被逐条拒掉的候选不得以任何形式出现在被采信的正文里")

    # (e) §一.2 补充裁决的四条后果：裁出不是一次「就地删几条」，也不是一条规避 P8/P11 的捷径。
    #     上面把「原束怎么留档、新修订是什么」钉住了；这里钉的是**门与缺口两侧**：被采信的
    #     Draft 只能是新修订、门判的就是它、被拒那一束连一份 Draft 身份都没有、裁出本身不
    #     产生任何缺口，而两条判据（高风险面 / 支撑资格）一步都不能少。
    _rounds = list((mix_outcome.batch_audit or {}).get("rounds") or ())
    check([r.get("round") for r in _rounds] == [1, 2] and len(_rounds) == 2,
          f"裁出是一次**逐轮可核**的过程：被拒那一轮 + 零调用的裁出轮，两轮都要留痕"
          f"（实为 {[r.get('round') for r in _rounds]}）")
    check(bool(_rounds) and _rounds[0].get("carve_out") is False
          and _rounds[0].get("calls") == 1 and len(_rounds[0].get("batch_calls") or ()) == 1,
          "第 1 轮必须如实记成「**不是**裁出轮、确实发过一次调用」"
          f"（实为 carve_out={_rounds[0].get('carve_out') if _rounds else None} / "
          f"calls={_rounds[0].get('calls') if _rounds else None}）")
    check(len(_rounds) == 2 and _rounds[1].get("calls") == 0
          and not (_rounds[1].get("batch_calls") or ())
          and (_rounds[1].get("carve_out") or {}).get("version")
          == PW.CANDIDATE_CARVE_OUT_VERSION,
          "第 2 轮必须如实记成「**零调用的裁出轮**」并把那一次裁决挂在这一行上："
          "「裁出不新增调用」是逐轮读数，不是一句声明"
          f"（实为 calls={_rounds[1].get('calls') if len(_rounds) == 2 else None} / "
          f"batch_calls={len(_rounds[1].get('batch_calls') or ()) if len(_rounds) == 2 else None}）")
    # 被采信的那一份 Draft 只能是**新修订**：候选、支撑提案、材料处理去向三份身份都按新修订
    # 重新派生过（`ClaimCandidate` / `ProposedSupportRef` 把 `draft_revision` 计入内容身份），
    # 因此「把原束删几条冒充原束」在这里是可读出来的：原身份与新身份必然不相交。
    _draft = mix_outcome.draft
    _new_proposal_ids = {str(p.proposed_support_id) for p in _draft.proposed_support_refs}
    check(_draft.draft_revision == carve.decision.to_revision,
          f"被采信的 Draft 必须正是裁出裁决声明的目标修订（实为 {_draft.draft_revision!r} vs "
          f"{carve.decision.to_revision!r}）")
    check({str(c.draft_revision) for c in _draft.claim_candidates} == {_draft.draft_revision}
          and {str(p.draft_revision) for p in _draft.proposed_support_refs}
          == {_draft.draft_revision},
          "候选与支撑提案必须**整体**换到新修订（逐条 draft_revision 与 Draft 自己逐字相同）")
    check(mix_outcome.gate_result.section_draft_id == _draft.draft_id,
          "叙述硬门判的就是这一份新修订的 Draft（门结果自带它所判的那份身份）")
    _rec = recs[0].to_dict() if recs else {}
    check(_rec and not (set(_rec.get("candidate_ids") or ())
                        & ({_draft.draft_id, _draft.draft_revision}
                           | set(_draft.claim_candidate_ids)))
          and not (set(_rec.get("proposal_ids") or ()) & _new_proposal_ids),
          "被拒的那一束**没有**留下任何 Draft 身份：原束的候选/提案身份不得出现在被采信的 "
          f"Draft 里（实为 candidate_ids={_rec.get('candidate_ids')} / "
          f"proposal_ids={_rec.get('proposal_ids')}）")
    _used = [str(u) for d in _draft.material_dispositions for u in (d.support_usages or ())]
    check(_used and set(_used) <= _new_proposal_ids
          and not (set(_used) & set(_rec.get("proposal_ids") or ())),
          "材料处理去向（WMPD `support_usages`）必须按新修订**重新派生**：它引用的只能是新"
          f"修订的提案身份（实为 {_used} vs 新修订 {sorted(_new_proposal_ids)}）")
    # 「裁出本身**不**产生缺口」——与「Contract 必需内容因此仍未满足才另加 gap」是同一句话的
    # 两半，这里钉前一半：同一份权威下，把那条高风险候选**提出来又被裁掉**与它**从未被提出**
    # 必须是同一张缺口面。后一半由既有的「栏目零产出 / Contract 必需事实未取得 ⇒ gap」那组
    # 断言钉住（`gap_outcome` / `no_fact_outcome`），不由裁出这条出口另开一份。
    _ctrl_plan = _plan(candidates=[
        _cand("c1", _mix_survivor_text, _material_edge(mix_pack_id, "m-mix-1"))])
    _ctrl_stub = _StubLlm(_ctrl_plan)
    _ctrl_outcome = _write(mix_task, mix_authority, section_id="company", llm=_ctrl_stub,
                           policy=PW.WriterPolicy(max_llm_retries=1))
    check(len(_ctrl_stub.calls) == 1 and not _ctrl_outcome.rejections,
          f"对照组（那条高风险候选从未被提出）必须一次通过、零拒绝"
          f"（实为 {len(_ctrl_stub.calls)} 次调用 / "
          f"{[r.rejection_kind for r in _ctrl_outcome.rejections]}）")
    check([u.unresolved_id for u in mix_outcome.unresolved]
          == [u.unresolved_id for u in _ctrl_outcome.unresolved]
          and not [u for u in mix_outcome.unresolved
                   if "该事项不适用" in str(u.detail)],
          "裁出本身**不产生**缺口：「提出来又被裁掉」与「从未被提出」必须是同一张缺口面，"
          "且没有任何一条缺口是拿被排除的候选说事的"
          f"（裁出面 {[u.unresolved_id for u in mix_outcome.unresolved]} vs "
          f"对照组 {[u.unresolved_id for u in _ctrl_outcome.unresolved]}）")
    # 上面那条等式在「两侧都为空」时也会成立，因此把两侧的实际读数如实写出来：本夹具的缺口面
    # 由**权威状态**（aspect / 附注 / 期间）决定，与候选面无关；「必需内容真的未满足 ⇒ 缺口」
    # 那条落在门后 FND，不在写入侧，也不由裁出这条出口另开一份。
    details.append(
        f"NOTE 裁出的缺口面（本夹具）：裁出面 {len(mix_outcome.unresolved)} 条 / "
        f"对照面 {len(_ctrl_outcome.unresolved)} 条，逐条 id "
        f"{[u.unresolved_id for u in mix_outcome.unresolved]}；"
        "写入侧缺口只由权威状态派生（候选面不产生缺口）")
    # 静态审计（判据是**源码里的字面量**，不是这段注释）：四条判据一步都不能少，且裁出轮的
    # 存在**不豁免**任何一道判定——不能靠「改一个 `if high_risk`」把 P8/P11 绕过去。
    #
    # 为什么这条清单必须随判据数一起长：`srsc-2`（独立支撑结论）是**第四条**轴，它和另外三条
    # 一样各自独立判、共用同一份 `reject_kwargs`。清单少一条，就等于允许「新判据只在整束出口
    # 生效、裁出出口不认」这种半接线状态悄悄存在——而那正是这条静态审计唯一要挡的事。
    _pw_src = Path(PW.__file__).read_text(encoding="utf-8")
    _judge_lines = (
        "high_risk = _path_b_high_risk_surfaces(bundle=bundle)",
        "ineligible = _path_b_ineligible_scope(bundle=bundle, scope=selection_scope)",
        "unproven_current_state = _path_b_unproven_current_state(",
        "if high_risk or ineligible or history_only or unproven_current_state:",
        # 裁出的两条守卫（候选轴 / context 单元轴，`cco-6` 起两轴正交）必须逐字存在，且各自
        # **只**认「本轮是哪一轴」与「本轮是不是定点补件轮」——不以「本轮是不是裁出轮」为条件。
        # 守卫缺一条，就等于允许「某一轴的裁出绕过这四条判据」这种半接线状态悄悄存在。
        'carved = (None if (carve_out_axis == "candidates"',
        'carve_units = (None if (carve_out_axis == "context_units"',
        "reject_kwargs = dict(",
        "high_risk=high_risk,",
        "ineligible=ineligible, history_only=history_only,",
        "unproven_current_state=unproven_current_state,",
        '_reject("path_b_high_risk_surface", detail, **reject_kwargs)',
        '_reject("path_b_ineligible_material_scope", detail, **reject_kwargs)',
        '_reject("path_b_history_only_current_state", detail, **reject_kwargs)',
        '_reject("path_b_unproven_current_state", detail, **reject_kwargs)',
    )
    check(all(line in _pw_src for line in _judge_lines),
          "写入侧四条判据（高风险面 / 支撑资格 / 期间来源角色 / 独立支撑结论）、两轴裁出守卫与"
          "四条出口必须都在源码里逐字存在：四条判据各自**独立**判、四条出口共用同一份 "
          "`reject_kwargs`、守卫只认轴与定点补件轮，"
          f"缺哪一条都说明有人把它挪到 `if high_risk` 后面去了"
          f"（缺 {[l for l in _judge_lines if l not in _pw_src]}）")
    _gate_lines = (
        "if high_risk or ineligible or history_only or unproven_current_state:",
        "if gate_result.blocking:",
        "if (uncovered and focus_passes < MAX_SUBSECTION_FOCUS_PASSES):",
    )
    check(all(line in _pw_src and "is_carve_out_round" not in line for line in _gate_lines),
          "裁出轮**不豁免**任何一道判定：高风险/资格/期间/独立支撑结论、叙述硬门、栏目覆盖三处都不以"
          f"「本轮是不是裁出轮」为条件（实为 "
          f"{[l for l in _gate_lines if l not in _pw_src or 'is_carve_out_round' in l]}）")


    # ==================================================================
    # 组装器不再信任 Draft 的自报（`draft.authority_container_ids`），而是从**本节权威**独立
    # 派生合法容器集合，再要求 Draft 与之守恒（`test_demo_report_assembler.py` §3.7 覆盖
    # 反例方向）。两条派生**分别实现**（组装器不 import 写作器的助手），所以「它们一致」不是
    # 免费的：只要有一类 authority 的语义对不上，真实链上就会出现「合法容器被组装器判成
    # 缺失」的误杀。§3.7 只覆盖 topic Pack，财务 / 附注 / 外部快照三类只在这里核。
    class _AuthorityItem:
        """组装器只读 `item.authority` / `item.task`；这是只带这两个字段的最小替身。"""

        def __init__(self, authority, task) -> None:
            self.authority = authority
            self.task = task

    def _container_parity(authority, task, label: str) -> dict:
        lawful = RA._lawful_containers_for(_AuthorityItem(authority, task))
        flat = {c for values in lawful.values() for c in values}
        declared = set(PW._authority_container_ids(
            authority, PW.scan_authority(authority, task)))
        check(declared == flat,
              f"{label}：组装器独立派生的合法容器集合必须恰好等于写作器声明的集合"
              f"（两条实现分开写，所以「一致」必须由测试钉住）；实为 "
              f"declared={sorted(declared)} lawful={sorted(flat)}")
        same_container_two_kinds = [
            c for c in flat
            if sum(1 for values in lawful.values() if c in values) > 1]
        check(not same_container_two_kinds,
              f"{label}：同一容器不得同时归入两类 authority，实为 {same_container_two_kinds}")
        return lawful

    # 23.1 topic Pack（含事实 / 不含事实各一）
    parity_aspects = (_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                      _AspectResult(ASP_BUSINESS_SALES, "partial"))
    parity_reqs = (_Req(TOPIC_BUSINESS, (
        _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)),
        _aspect(ASP_BUSINESS_SALES, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS)))),)
    parity_authority = _company_authority(
        co_task, facts=base_facts, aspects=parity_aspects, requirements=parity_reqs)
    lawful_topic = _container_parity(parity_authority, co_task, "单 topic Pack")
    check(list(lawful_topic) == ["topic_pack"]
          and lawful_topic["topic_pack"] == PW._authority_container_ids(
              parity_authority, PW.scan_authority(parity_authority, co_task)),
          "topic 权威只应产出 topic_pack 一类容器，且逐字等于写作器声明的（含顺序）")
    # 无事实的 Pack 仍是本节合法容器：路径 B 的纯材料候选不需要任何事实
    empty_task = _task("company", (TOPIC_IDENTITY,), task_id="task-co-parity-empty")
    empty_authority = _company_authority(
        empty_task, facts=(), aspects=(_AspectResult(ASP_IDENTITY_CAPITAL, "partial"),),
        requirements=(_Req(TOPIC_IDENTITY, (
            _aspect(ASP_IDENTITY_CAPITAL, TOPIC_IDENTITY,
                    _question_id(TOPIC_IDENTITY)),)),))
    empty_scan = PW.scan_authority(empty_authority, empty_task)
    lawful_empty = _container_parity(empty_authority, empty_task, "无事实的 topic Pack")
    check(not empty_scan.facts and lawful_empty.get("topic_pack")
          and set(lawful_empty["topic_pack"]) == {str(empty_authority.pack_set.packs[0].pack_id)},
          "没有任何事实的 Pack 仍必须是本节的合法容器（不得按事实有无裁剪容器集合）")

    # 23.2 formal `ExternalFact`：容器是**snapshot 记录**，不是 Pack
    lawful_ext = _container_parity(ext_authority, ext_task, "含外部快照事实的 topic Pack")
    ext_container = NS.external_authority_container_id(ext_fact)
    check(lawful_ext.get("external_snapshot") == (ext_container,)
          and ext_container not in lawful_ext.get("topic_pack", ()),
          "外部快照容器必须单列一类，且不得与 Pack 容器混为一谈")
    # 反例方向：`ExternalFact.as_of_date` 在**类型层**就非空（`SchemaValidationError`），因此
    # 「期间未定」不是可达形态；可达的排除路径只有 aspect 不在本节投影里（读视图 `excluded`）。
    # 这条路径上写作器**不**把 snapshot 容器写进声明。若组装器按「Pack 里有就合法」结构性
    # 枚举，就会把「写作器依法没声明」判成「缺失」，误杀真实链 —— 本节必须钉住这一致性。
    expect_error(
        lambda: dataclasses.replace(ext_fact, as_of_date=""),
        TS.SchemaValidationError, "`ExternalFact` 的 as_of_date 为空必须在类型层被拒")
    foreign_cand, foreign_dec, foreign_fact = _external_spec(
        "该公司的诉讼事项按年度披露。", aspect_ids=(ASP_LITIGATION_MAJOR,))
    foreign_authority = PW.TopicPackAuthorityInput.create(
        ext_task,
        _pack_set(ext_task, packs=(_Pack(
            topic_id=TOPIC_BUSINESS,
            aspect_results=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
            external_facts=(foreign_fact,),
            external_chains=((foreign_cand, foreign_dec),)),),
            requirements=(_Req(TOPIC_BUSINESS, (
                _aspect(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),)),)),
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)
    foreign_scan = PW.scan_authority(foreign_authority, ext_task)
    check(not [e for e in foreign_scan.facts
               if e.fact_id == foreign_fact.external_fact_id],
          "前提：aspect 不在本节投影里的 external fact 不在本节事实目录里（读视图排除）")
    _container_parity(foreign_authority, ext_task, "aspect 不在本节投影里的外部快照事实")

    # 23.3 财务：artifact 是容器；附注事实是**另一类**容器，附注缺口则不是容器
    fin_note = _NoteSet(task_id=fin_task.task_id, facts=(
        _NoteFact(fact_id="n-1", label="受限资金", display="受限资金 12.34 元。"),))
    note_authority = _financial_authority(fin_task, fin_artifact, note_facts=fin_note)
    lawful_note = _container_parity(note_authority, fin_task, "财务 artifact + 附注事实")
    check(lawful_note.get("financial_pack") == (fin_artifact.artifact_id,)
          and lawful_note.get("evidence_note") == (NS.note_container_id(fin_note),)
          and NS.note_container_id(fin_note) != fin_artifact.artifact_id,
          "财务容器与附注容器必须分列两类、身份不同（来源权威不得混用）")
    gap_authority = _financial_authority(
        fin_task, fin_artifact, note_gap=_NoteGap(task_id=fin_task.task_id))
    lawful_gap = _container_parity(gap_authority, fin_task, "财务 artifact + 附注缺口")
    check("evidence_note" not in lawful_gap
          and lawful_gap.get("financial_pack") == (fin_artifact.artifact_id,),
          "附注缺口不是容器：缺口不得凭空给本节添一个「合法容器」")

    # ==================================================================
    # 24. r7b 后聚焦：2025 年「经营模式」**跨页**材料 → 采购 / 生产 / 销售 多 Claim 段落
    #
    #     现场（r7b 公司节）：路径 A 事实**为零**（46 个 aspect 全 `writable_facts == 0`），
    #     经营模式一组 7 条原始事实全被期间门以 `explicit_period_required` 拒（描述性语句
    #     没有显式期间，依法不该取得路径 A 资格），而 p15 / p16 两页材料**都在**、都被
    #     `admitted/retained`。于是这一组的合法写路径只有路径 B，且必须是**跨页**的。
    #
    #     本块只补这一件事：材料在、写不出来，到底卡在哪一层。三件事分开钉：
    #     24.1 两页材料 + 三条候选（采购 / 生产 / 销售）全部走路径 B，且**一条候选可以跨页**
    #          引用（主边在 p15、corroborating 边在 p16），边序与 role 逐条保留；
    #     24.2 两条材料都登记为 `used`（不得靠删料达标），Draft 的段落单元真的被提出；
    #     24.3 缺料栏目（收入构成）**不得**伪称覆盖：既不能由勾选材料派生候选，也要留下
    #          显式缺口。勾选行的正文**逐字**含「不适用」也照样不能变成事实。
    # ==================================================================
    ASP_PROCUREMENT = "company_business_model.procurement_mode"
    ASP_PRODUCTION = "company_business_model.production_mode"
    ASP_REVENUE = "company_business_main.revenue_breakdown"
    # 三条候选文本：单原子描述性子句，**逐字**（仅空白归一）是下面某一条锚边材料正文的连续
    # 子串，且**不含**任何高风险表面。
    #
    # 夹具为什么必须摆成逐字形态：`srsc-2` 的现行出口只有**严格抽取式**一种（指令二：
    # 「如果目前只能证明严格抽取式窄情形，那么其余记 unproven」）。旧夹具把三条候选写成
    # 材料正文的**改写**（「公司采购通过…合作」对着「采购方面，公司通过…紧密合作」），
    # 却在自己的注释里写着「逐字可从两页材料读出」——注释与事实相反，改写的候选会被判
    # `unproven`、整节 fail-closed。这里改的是**夹具自重**，不是判据。
    #
    # 如实记一条**本夹具照不到**的真实形态：一**句**真正跨页断开（p15 上半句 + p16 下半句）
    # 的候选，在 `srsc-2` 下同样判不出来——因为没有任何**单一份**材料正文含完整这句。那不是
    # 本夹具要证的事，也不能靠把夹具写宽来「过」；它属于指令三（材料驱动草稿）要解决的形态。
    TXT_PROC = "采购方面，公司通过长期协议与全球供应商紧密合作"
    TXT_PROD = "生产方面，公司以自建生产基地为主"
    TXT_SALES = "销售方面，公司以直销为主"
    # 两页材料的正文：p15 起（体系 + 采购 + 销售摘要）、p16 续（生产 + 销售明细）。
    # 销售那句在两页各出现一次（摘要行 + 明细行），因此跨页那条候选**确实**有两条可核实的
    # 锚边——夹具不是在凑边数，边归属的那页真的有那句话。
    MAT_P15 = ("公司拥有独立的研发、采购、生产和销售体系。"
               "采购方面，公司通过长期协议与全球供应商紧密合作。"
               "销售方面，公司以直销为主。")
    MAT_P16 = ("生产方面，公司以自建生产基地为主。"
               "销售方面，公司以直销为主，并以经销为辅。")
    for _label, _text in (("采购", TXT_PROC), ("生产", TXT_PROD), ("销售", TXT_SALES),
                          ("p15 材料", MAT_P15), ("p16 材料", MAT_P16)):
        check(NS.high_risk_surface_tokens(_text) == (),
              f"聚焦夹具前提：{_label}文本不得含任何高风险表面"
              f"（实测 {NS.high_risk_surface_tokens(_text)}）")

    bm_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-bm-crosspage")
    bm_aspects = (_AspectResult(ASP_PROCUREMENT, "partial"),
                  _AspectResult(ASP_PRODUCTION, "partial"),
                  _AspectResult(ASP_BUSINESS_SALES, "partial"),
                  _AspectResult(ASP_REVENUE, "blocked"))
    bm_reqs = (_Req(TOPIC_BUSINESS, (
        _aspect(ASP_PROCUREMENT, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                requirement_text="说明采购模式及其报告期内的变化。"),
        _aspect(ASP_PRODUCTION, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                requirement_text="说明生产模式及其报告期内的变化。"),
        _aspect(ASP_BUSINESS_SALES, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                requirement_text="说明销售模式及其报告期内的变化。"),
        _aspect(ASP_REVENUE, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                requirement_text="列示报告期内营业收入的构成、金额与占比。"))),)
    m_bm15 = _material_of_identity("m-bm-p15", "evidence:co-bm-p15", page=15, text=MAT_P15)
    m_bm16 = _material_of_identity("m-bm-p16", "evidence:co-bm-p16", page=16, text=MAT_P16)
    check(m_bm15.locator.page == 15 and m_bm16.locator.page == 16
          and m_bm15.content_hash != m_bm16.content_hash
          and m_bm15.payload_ref.locator.section_path == m_bm16.payload_ref.locator.section_path,
          "聚焦夹具前提：两页材料同属一节、页不同、内容身份不同（跨页续写，不是重复块）")
    bm_authority = _company_authority(
        bm_task, facts=(), materials=(m_bm15, m_bm16), aspects=bm_aspects, requirements=bm_reqs)
    bm_pack_id = str(bm_authority.pack_set.packs[0].pack_id)

    bm_plan = _plan(
        candidates=[
            _cand("c-proc", TXT_PROC, _material_edge(bm_pack_id, "m-bm-p15")),
            _cand("c-prod", TXT_PROD, _material_edge(bm_pack_id, "m-bm-p16")),
            # 跨页：同一条候选的主边在 p15 起页、corroborating 边在 p16 续页。
            _cand("c-sales", TXT_SALES, _material_edge(bm_pack_id, "m-bm-p15"),
                  _material_edge(bm_pack_id, "m-bm-p16", role="corroborating")),
        ],
        # 出处**逐候选**给对页：三条候选的支撑边分别落在 p15 / p16 / p15 上（`npr-1` 逐
        # occurrence 核对的就是这件事），缺省 `m1` 会把「生产」那条写成出自 p15。
        draft_ref=[_member_ref(bm_authority, task_id=str(bm_task.task_id),
                               section_id="company", material_id=mid)
                   for mid in ("m-bm-p15", "m-bm-p16", "m-bm-p15")],
        units=[_unit("u-bm", "公司拥有独立的研发、采购、生产和销售体系。")])
    bm_outcome = _write(bm_task, bm_authority, section_id="company",
                        llm=_StubLlm(bm_plan))
    check(not bm_outcome.gate_result.blocking,
          f"路径 A 为零、两页材料可读时，采购/生产/销售三条路径 B 候选必须能走通"
          f"（实际门问题 {[i.code for i in bm_outcome.gate_result.issues_of('blocking')][:4]}）")
    check(len(bm_outcome.draft.claim_candidates) == 3,
          f"三条候选必须全部进入 Draft（不得只因没有事实而少写）"
          f"（实际 {len(bm_outcome.draft.claim_candidates)}）")

    bm_props = tuple(p for p in bm_outcome.draft.proposed_support_refs
                     if p.binding_subject_kind == "claim_candidate")
    check(len(bm_props) == 4
          and {p.authorization_path for p in bm_props} == {"path_b_material_derived"}
          and {p.fact_id for p in bm_props} == {None},
          f"四条支撑边必须全部走路径 B 且不带任何事实身份"
          f"（实际 {[(p.authorization_path, p.material_id, p.fact_id) for p in bm_props]}）")
    _by_cand: dict[str, list] = {}
    for _p in bm_props:
        _by_cand.setdefault(str(_p.binding_subject_id), []).append(_p)
    check({len(v) for v in _by_cand.values()} == {1, 2},
          "三条候选分别挂 1 / 1 / 2 条支撑边（跨页那条挂两条）")
    check(sorted(p.material_id for p in bm_props)
          == ["m-bm-p15", "m-bm-p15", "m-bm-p16", "m-bm-p16"],
          f"两条材料必须都被引用到（跨页候选各占一次）"
          f"（实际 {sorted(p.material_id for p in bm_props)}）")
    check([d.usage for d in bm_outcome.draft.material_dispositions] == ["used", "used"],
          "两页材料都必须登记为 used：不得靠删掉其中一页来让本组「达标」"
          f"（实际 {[d.usage for d in bm_outcome.draft.material_dispositions]}）")
    check([str(u.unit_kind) for u in bm_outcome.draft.narrative_draft_units] == ["paragraph"]
          and str(bm_outcome.draft.narrative_draft_units[0].text).strip() != "",
          "经营模式这一组必须产出**自然段**单元（不是空正文，也不是逐条事实清单）")

    # 24.3 缺料栏目不得伪称覆盖：勾选行逐字含「不适用」也不能变成事实/候选。
    rev_task = _task("company", (TOPIC_BUSINESS,), task_id="task-co-revenue-checkbox")
    rev_aspects = (_AspectResult(ASP_REVENUE, "blocked"),)
    rev_reqs = (_Req(TOPIC_BUSINESS, (
        _aspect(ASP_REVENUE, TOPIC_BUSINESS, _question_id(TOPIC_BUSINESS),
                requirement_text="列示报告期内营业收入的构成、金额与占比。"),)),)
    cb_material = _material_of_identity(
        "m-rev-checkbox", "evidence:co-rev-checkbox", page=25,
        text="适用 □ 不适用 ☒")
    rev_authority = _company_authority(
        rev_task, facts=(), materials=(cb_material,), aspects=rev_aspects,
        requirements=rev_reqs)
    rev_pack_id = str(rev_authority.pack_set.packs[0].pack_id)
    check("不适用" in "适用 □ 不适用 ☒",
          "反例前置条件：勾选行正文**逐字**含「不适用」（逐字在场不等于已资格化）")
    rev_llm = _StubLlm(_plan(candidates=[
        _cand("c-rev", "报告期内营业收入构成为动力电池与储能电池两大板块。",
              _material_edge(rev_pack_id, "m-rev-checkbox"))]))
    expect_error(
        lambda: _write(rev_task, rev_authority, section_id="company", llm=rev_llm),
        PW.PackWriterError,
        "不相关的勾选行不得证明收入构成（勾选/适用状态是高风险表面，命中即拒）",
        needle="路径 B 候选携带了高风险表面")
    check(len(rev_llm.calls) == 1,
          "勾选行反例必须在写入侧预验证就被拒（不得进入后续门）")
    rev_outcome = _write(rev_task, rev_authority, section_id="company",
                         llm=_StubLlm(_plan()))
    check(any(ASP_REVENUE in str(u.detail) for u in rev_outcome.unresolved),
          f"没有任何候选的缺料栏目必须以**显式缺口**出场（不得静默当作已覆盖）"
          f"（实际缺口 {[str(u.detail)[:60] for u in rev_outcome.unresolved][:3]}）")
    check(not rev_outcome.draft.claim_candidates,
          "缺料栏目在没有任何合格材料时不得产生候选（不伪称覆盖）")

    # ==================================================================
    # 25. `pw-15`：先自然草稿、后逐原子候选（材料驱动写作的产物形态）
    # ==================================================================
    # §三 指令三：门前 Writer 先形成一段**可保留的自然散文草稿**，再为草稿里每个事实原子单独
    # 提交候选。候选是「草稿里那几句话的原子账」，不是「拼文材料」。本组逐条钉住：
    #   (a) 草稿层真的进了 `SectionDraft`，出处指向本节 exact manifest 的成员；
    #   (b) 原子键在写入侧展开成**真候选身份**（映射不是模型自报）；
    #   (c) 修订覆盖草稿内容：同批候选、换一段草稿 ⇒ 另一个 draft_revision；草稿相同 ⇒ 同一修订；
    #   (d) 草稿声明了本节不存在的原子/材料 ⇒ fail-closed（不得「顺手忽略」）；
    #   (e) 成功路径的补件诉求归属**这一轮**的修订 = Draft 自己的修订；
    #   (f) 不带草稿层的老形态产物一字不变（空草稿层与「没有这个键」同读）。
    bm_ctx = _writer_material_context(bm_authority.pack_set, task_id=str(bm_task.task_id),
                                      section_id="company")
    bm_scan = PW.scan_topic_pack(bm_authority, bm_task)
    bm_manifest = PW._derive_material_manifest(bm_authority, material_context=bm_ctx)
    bm_aliases = PW._support_aliases(bm_scan, bm_manifest)
    bm_ref = {str(o.material_id): str(o.ref) for o in bm_aliases.materials}
    check(set(bm_ref) == {"m-bm-p15", "m-bm-p16"},
          f"前置条件：两页材料各有一行短别名（实测 {bm_ref}）——草稿的出处只能写别名")
    P_A = ("公司拥有独立的研发、采购、生产和销售体系。"
           "采购方面，公司通过长期协议与全球供应商紧密合作。")
    P_B = ("公司拥有独立的研发、采购、生产和销售体系。"
           "生产方面，公司以自建生产基地为主。")
    prose_a = [_prose("p1", P_A, members=[bm_ref["m-bm-p15"]], atoms=["c-proc"]),
               _prose("p2", TXT_PROD + "。", members=[bm_ref["m-bm-p16"]], atoms=["c-prod"]),
               _prose("p3", TXT_SALES + "。", members=[bm_ref["m-bm-p15"]], atoms=["c-sales"])]
    prose_b = [dict(prose_a[0], text=P_B), prose_a[1], prose_a[2]]
    bm_candidates = [
        _cand("c-proc", TXT_PROC, _material_edge(bm_pack_id, "m-bm-p15")),
        _cand("c-prod", TXT_PROD, _material_edge(bm_pack_id, "m-bm-p16")),
        _cand("c-sales", TXT_SALES, _material_edge(bm_pack_id, "m-bm-p15"),
              _material_edge(bm_pack_id, "m-bm-p16", role="corroborating"))]
    pr_outcome = _write(
        bm_task, bm_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=bm_candidates, prose=prose_a)),
        material_context=bm_ctx)
    check(not pr_outcome.gate_result.blocking,
          f"带草稿层的束必须能走通硬门（实际 "
          f"{[i.code for i in pr_outcome.gate_result.issues_of('blocking')][:4]}）")
    pr_draft = pr_outcome.draft
    check(len(pr_draft.natural_prose_draft) == 3
          and [u.index for u in pr_draft.natural_prose_draft] == [0, 1, 2],
          f"三段草稿必须逐单元进 Draft、index 与顺序一致（实际 "
          f"{[u.index for u in pr_draft.natural_prose_draft]}）")
    check([u.text for u in pr_draft.natural_prose_draft] == [P_A, TXT_PROD + "。",
                                                             TXT_SALES + "。"],
          "草稿文本必须**逐字**进产物（候选是原子账，草稿是要读的字）")
    check(all(tuple(u.source_member_refs) ==
              (NS.manifest_member_ref(bm_pack_id, mid),)
              for u, mid in zip(pr_draft.natural_prose_draft, ("m-bm-p15", "m-bm-p16",
                                                               "m-bm-p15"))),
          "草稿的出处必须是**本节 exact manifest 的成员身份**（短别名由写入侧展开，"
          "不是模型自报的字符串）")
    by_text = {str(c.claim_text): str(c.candidate_id) for c in pr_draft.claim_candidates}
    check([tuple(u.atom_candidate_ids) for u in pr_draft.natural_prose_draft]
          == [(by_text[TXT_PROC],), (by_text[TXT_PROD],), (by_text[TXT_SALES],)],
          "草稿声明的原子键必须展开成**真候选身份**（一一对上，不得旁路塞候选）")
    # (c) 修订覆盖草稿内容：同一批候选、换一段草稿 ⇒ 另一个修订；草稿相同 ⇒ 同一修订。
    pr_same = _write(
        bm_task, bm_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=bm_candidates, prose=prose_a)),
        material_context=bm_ctx)
    pr_other = _write(
        bm_task, bm_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=bm_candidates, prose=prose_b)),
        material_context=bm_ctx)
    check(pr_same.draft.draft_revision == pr_draft.draft_revision
          and pr_same.draft.draft_id == pr_draft.draft_id,
          "同一候选集 + 同一草稿 ⇒ 同一修订（修订必须是纯函数，不看调用次数）")
    check(pr_other.draft.draft_revision != pr_draft.draft_revision,
          "同一候选集 + **不同草稿** ⇒ 必须换修订：草稿层进了身份，"
          "否则产物里「同一修订、两段不同的草稿」无法区分")
    check(pr_other.draft.claim_candidates[0].candidate_id
          != pr_draft.claim_candidates[0].candidate_id,
          "候选身份随修订走（候选挂在修订上，不能跨修订复用）")
    # 定点：候选身份依赖修订，修订又覆盖草稿内容，因此「先按草稿**规格**算修订」与
    # 「候选成形后按**单元**再算」必须是同一个数——两条公式并存，写入侧就只能等候选成形之后
    # 才知道修订，而候选身份反过来依赖修订，转不动。
    spec_projection = [{"index": u.index, "text": u.text,
                        "source_member_refs": list(u.source_member_refs)}
                       for u in pr_draft.natural_prose_draft]
    check(PW._prose_specs_digest(spec_projection)
          == NS.natural_prose_draft_digest(pr_draft.natural_prose_draft),
          "草稿**规格**摘要与草稿**单元**摘要必须相等（同一投影、同一实现）")
    # 端到端：让**真的**解析器把这一束 JSON 读成提案集，再按规格算一次摘要。
    pr_parsed = PW.parse_writer_proposals(
        json.dumps(_plan(candidates=bm_candidates, prose=prose_a), ensure_ascii=False),
        aliases=bm_aliases)
    check(PW._prose_specs_digest(pr_parsed["natural_prose_draft"])
          == NS.natural_prose_draft_digest(pr_draft.natural_prose_draft),
          "解析后的草稿规格（出处已由别名展开成员身份）与落进 Draft 的单元必须是**同一个摘要**："
          "写入侧在候选还不存在的那一刻就算得出修订，靠的正是这一点")
    # (e) 补件诉求归属**这一轮**的修订。
    pr_fu = _follow_up("需要补充公司采购模式的口径说明。",
                       target_requirement_id=bm_scan.requirement_ids.get(TOPIC_BUSINESS, ""),
                       topic_id=TOPIC_BUSINESS, question_id=_question_id(TOPIC_BUSINESS),
                       aspect_id=ASP_PROCUREMENT)
    pr_fu_outcome = _write(
        bm_task, bm_authority, section_id="company",
        llm=_StubLlm(_plan(candidates=bm_candidates, prose=prose_a, follow_ups=[pr_fu])),
        material_context=bm_ctx)
    check(len(pr_fu_outcome.follow_up_needs) == 1
          and pr_fu_outcome.follow_up_needs[0].section_draft_revision
          == pr_fu_outcome.draft.draft_revision,
          "有草稿层时，补件诉求的 section_draft_revision 必须等于该 Draft 自己的修订"
          "（差一项 prose_digest 就指向一个不存在的修订）")
    # (d) 反例：草稿声明本节不存在的原子 / 不存在的材料 ⇒ fail-closed。
    expect_error(
        lambda: _write(bm_task, bm_authority, section_id="company",
                       llm=_StubLlm(_plan(candidates=bm_candidates, prose=[
                           dict(prose_a[0], atom_candidate_keys=["c-nope"]),
                           prose_a[1], prose_a[2]])),
                       material_context=bm_ctx),
        PW.PackWriterError, "草稿声明了本束不存在的原子键必须 fail-closed（映射不得编造）",
        needle="指向本批不存在的")
    bad_alias = _plan(candidates=bm_candidates,
                      prose=[prose_a[0], prose_a[1],
                             dict(prose_a[2], source_member_refs=["m9"])])
    expect_error(
        lambda: _write(bm_task, bm_authority, section_id="company",
                       llm=_StubLlm(bad_alias, bad_alias),
                       material_context=bm_ctx),
        PW.PackWriterError, "草稿引用了未声明的材料别名必须 fail-closed（别名只能指向已声明的一行）",
        needle="不在本次请求声明的选项里")
    # (b2)/(f) 老形态（`proposals-10`，没有草稿层）**只能经显式标注的历史兼容线**读回：
    # 它必须显式声明 `proposal_wire="proposals-10"`（这条线只在离线 stand-in 下可用，见
    # `WriterPolicy.__post_init__`），且草稿层如实为空——不得由写入侧替它编一段草稿出来。
    #
    # 这条兼容线是**唯一**的出口：在当前线上，同一份载荷（有候选、无草稿）被整批拒
    # （`natural_prose_draft_missing`），因为那条形状在旧线下会一路走到「空草稿 + 把候选拼成
    # 正文」，而失败形状却是「成功」。两个方向各有一条反例，见下面两条。
    legacy_plan = _plan(candidates=[
        _cand("c-proc", TXT_PROC, _material_edge(bm_pack_id, "m-bm-p15")),
        _cand("c-prod", TXT_PROD, _material_edge(bm_pack_id, "m-bm-p16")),
        _cand("c-sales", TXT_SALES, _material_edge(bm_pack_id, "m-bm-p15"),
              _material_edge(bm_pack_id, "m-bm-p16", role="corroborating"))],
        units=[_unit("u-bm", "公司拥有独立的研发、采购、生产和销售体系。")],
        drafted=False)
    bm_plain = _write(bm_task, bm_authority, section_id="company",
                      llm=_StubLlm(legacy_plan), material_context=bm_ctx,
                      policy=PW.WriterPolicy(proposal_wire="proposals-10"))
    check(bm_plain.draft.natural_prose_draft == (),
          "`proposals-10` 形态的返回（没有草稿层）在历史兼容线上必须如实为空——"
          "不得由写入侧替它编一段草稿出来")
    # 老形态的两个**载荷**读法必须同读：`natural_prose_draft: []`（键在、空）与**根本没有这个
    # 键**（`proposals-10` 的真实返回）必须给出同一个修订与同一个 draft_id。这不是恒等式——
    # 两份载荷不同，若修订把「键在/键不在」算进身份，这里就会分叉。
    no_key_plan = {k: v for k, v in legacy_plan.items() if k != "natural_prose_draft"}
    bm_no_key = _write(bm_task, bm_authority, section_id="company",
                       llm=_StubLlm(no_key_plan), material_context=bm_ctx,
                       policy=PW.WriterPolicy(proposal_wire="proposals-10"))
    check(bm_no_key.draft.draft_revision == bm_plain.draft.draft_revision
          and bm_no_key.draft.draft_id == bm_plain.draft.draft_id,
          "空草稿层与「没有这个键」必须同读（历史线上老路径的修订与 draft 身份一字不变）")
    # 同一份载荷走**当前线**：整批被拒（typed failure，不是静默回落到空草稿 + 候选拼文）。
    expect_error(
        lambda: _write(bm_task, bm_authority, section_id="company",
                       llm=_StubLlm(legacy_plan, legacy_plan), material_context=bm_ctx),
        PW.PackWriterError, "当前线下「有候选、无草稿」必须整批被拒（不得走另一条成稿路径）",
        needle=PW.MISSING_NATURAL_DRAFT_FAILURE)
    # 历史线**不得**与真实模型组合：那等于把「没有草稿」记成一次真实成功。
    expect_error(
        lambda: PW.WriterPolicy(proposal_wire="proposals-10", model_policy="sonnet"),
        PW.PackWriterError, "历史线配真实模型必须 fail-closed（旧离线重放 ≠ 新版本真实成功）",
        needle="不是离线 stand-in")

    # (b3) `pw-20`：与上一条**互为镜像**的另一半。上一条管「有候选、没草稿」，这一条管
    # 「**一条候选都交不出，却还写了草稿单元**」。r8 公司节第 4/4 批的真实残形正是后者：
    # 0 条 `claim_candidates`、0 段 `natural_prose_draft`，却交了 3 段「本轮未取得 / 无法说明 /
    # 未找到」的 `narrative_draft_units`（出处轴与原子键都空），另加 10 条合法 `FollowUpNeed`。
    # 为什么此前能过：草稿单元那一层**从来没有证人要求**——`context_support: []` 本来就是合法值，
    # 于是这个形状一路走到 `SectionResult`，把「没拿到材料」写成读者可见的自然叙述。草稿那半边
    # 没漏（`_parse_one_prose_unit` + 合并层的原子解析会挡），所以本批只堵单元这一层。
    # 判据**不读**材料、不读 `status`、不看关键词（本仓库禁止关键词规则）：只看「候选为空 ⇒
    # 草稿与草稿单元都必须为空」这一条结构等价。
    legal_empty = _plan(candidates=(), prose=(), units=[],
                        follow_ups=[_follow_up("需补充公司销售模式的口径说明。",
                                               target_requirement_id=req_id_business,
                                               topic_id=TOPIC_BUSINESS,
                                               question_id="q-company_business",
                                               aspect_id=ASP_BUSINESS_SALES)])
    PW.assert_batch_no_witness_means_empty(legal_empty, source="正例")
    check(PW.BATCH_NO_WITNESS_FAILURE not in PW._PLAN_KEYS,
          "`batch_candidate_witness_missing` 是**拒绝原因**，不是缺口语汇——不得混进门前提案键表")
    residual = _plan(
        candidates=(), prose=(),
        units=[_unit("u1", "本栏目未取得可用材料。"),
               _unit("u2", "本轮无法说明该项情况。"),
               _unit("u3", "检索未找到相关表述。")],
        follow_ups=[_follow_up("需补充公司销售模式的口径说明。",
                               target_requirement_id=req_id_business,
                               topic_id=TOPIC_BUSINESS, question_id="q-company_business",
                               aspect_id=ASP_BUSINESS_SALES)])
    expect_error(
        lambda: PW.assert_batch_no_witness_means_empty(residual, source="反例"),
        PW.PackWriterError,
        "0 条候选 + 3 段无出处的「未取得材料」草稿单元必须被拒（草稿单元与草稿一样，"
        "都要指得出一条真实的输入行）", needle=PW.BATCH_NO_WITNESS_FAILURE)
    # 端到端：同一残形经**整批入口**也必须 fail-closed。整批通道是既有的那条——先逐候选补救
    # （零候选 ⇒ 救不回），再走 `bsc-1` 纠形（每个批次一次额度）；模型把同一残形再交一遍时，
    # 额度用尽，整批落 typed 拒绝。**不新增额度、不静默删坏草稿、不伪造引用**。
    expect_error(
        lambda: _write(bm_task, bm_authority, section_id="company",
                       llm=_StubLlm(*([residual] * 6)), material_context=bm_ctx),
        PW.PackWriterError,
        "同一残形经**整批入口**也必须被拒——判据挂在既有的逐批通道上，不走旁路",
        needle=PW.BATCH_NO_WITNESS_FAILURE)
    # 历史线（`proposals-10`，没有草稿层）**不回溯套用**这条：那条线上单元与候选的共存关系
    # 本来就不是这个形状。用同一份残形在历史线上读，判据不得开火——这正是本批没有破坏
    # r7b 离线重放逐字读数的那一步。
    legacy_residual = PW.WriterPolicy(proposal_wire="proposals-10")
    check(not legacy_residual.requires_natural_draft(),
          "历史线必须如实自报 `requires_natural_draft() == False`（新判据据此不回溯套用）")

    # ==================================================================
    # 27. `pw-16` 定点返修：判据对象是**实际发出去的那份请求**
    # ==================================================================
    # 本批修的是「请求自己自相矛盾」：prompt 资产要求先输出自然草稿、解析面按四键解析，而
    # **那一份请求**的 `output_schema` 只列了三个键，同时它的 rules 逐字写着「不得输出
    # `output_schema` 之外的字段」——模型照 prompt 写即被同一份请求判违规，不照 prompt 写就
    # 没有草稿。只查 prompt 资产、或只查解析器，都看不见这个缺陷（两边各自都"对"，错的是
    # 它们之间的那一份请求）。因此这一段只解码**被发出去的载荷**。
    probe_stub = _StubLlm(_plan(candidates=bm_candidates, prose=prose_a))
    _write(bm_task, bm_authority, section_id="company", llm=probe_stub,
           material_context=bm_ctx)
    sent = json.loads(probe_stub.calls[0]["messages"][0]["content"])
    check(tuple(sent["output_schema"]) == PW._PLAN_KEYS
          and tuple(sent["output_schema"])[0] == "natural_prose_draft",
          f"**实际发出**的请求里 output_schema 必须与解析面 `_PLAN_KEYS` 逐位同序、"
          f"且草稿在首位（实测 {tuple(sent['output_schema'])}）")
    check(tuple(sent["output_schema"]["natural_prose_draft"][0]) == PW._PROSE_KEYS,
          "请求里草稿单元的字段必须恰是解析面接受的那五个，实测 "
          f"{tuple(sent['output_schema']['natural_prose_draft'][0])}")
    draft_rule = next((str(r) for r in sent["rules"] if "source_fact_refs" in str(r)), "")
    check("source_member_refs" in draft_rule and "互斥" in draft_rule
          and "整批拒绝" in draft_rule,
          "请求的 rules 必须把「两条轴互斥、恰有一条非空」与「有候选、没草稿会被整批拒绝」"
          f"一次说完（否则模型无路可走），实为 {draft_rule[:140]!r}")
    check(probe_stub.calls[0]["prompt_version"] == PW.NARRATION_PROMPT_VERSION
          == f"{PW.NARRATION_PROMPT_ASSET}@proposals-16",
          f"实际调用的 prompt 身份必须等于登记身份（实测 "
          f"{probe_stub.calls[0]['prompt_version']!r}）")
    check(bool(sent["materials"]) and not sent["authority_facts"],
          "本节只有材料行、没有权威事实行——正是「只准走材料轴」这一档的前提"
          "（财务节是相反的一档：有事实行、精确材料清单合法为空）")
    # 非空验证：把 `_PLAN_KEYS` 的键序反过来，同一份请求必须**在构造时就失败**。这证明上面
    # 那条「逐位同序」不是恰好成立，而是请求侧真的在判——判据放在构造时，一份自相矛盾的请求
    # 根本发不出去，而不是等模型返回后再由解析器兜。
    original_plan_keys = PW._PLAN_KEYS
    try:
        PW._PLAN_KEYS = tuple(reversed(original_plan_keys))
        expect_error(
            lambda: _write(bm_task, bm_authority, section_id="company",
                           llm=_StubLlm(_plan(candidates=bm_candidates, prose=prose_a)),
                           material_context=bm_ctx),
            PW.PackWriterError,
            "键序一旦不一致，请求必须**在构造时**就被拒（不得发出去等模型踩坑）",
            needle="output_schema")
    finally:
        PW._PLAN_KEYS = original_plan_keys
    check(PW._PLAN_KEYS == original_plan_keys,
          "本组的变异必须复原（否则后续用例读到的是被改过的解析面）")

    # ---- 27b. 出处轴的**错类型**必须逐条被拒，且理由点到那条轴本身 ----
    # 两档节各用**自己那份真实夹具**造错法：公司节只有材料行（`facts=()`）、财务节只有事实行
    # （精确材料清单合法为空）。「本节有材料行却改用事实轴」这条判据在**别名展开之前**就成立
    # （轴的选择是节的性质，不是某一行的性质），故公司节这一条用字面 `f1` 即可——公司节的
    # 别名表里根本没有事实行，这正是「事实轴在这一节不可用」本身。
    check(bool(fin_scan.facts), "前置条件：财务节权威事实目录非空，事实别名才取得到")
    fin_fact_alias = _fact_ref(fin_scan, "ff-short")
    fin_candidate = _cand("c1", TXT_PROC, _fact_edge(fin_scan, "ff-short"))

    def _unit_with(text: str, *, member_refs=(), fact_refs=(), atoms=()) -> dict:
        """直接写**线格式**的两个键（不经过 `_prose` 的 `members=`/`facts=` 形参名——
        那两个是夹具助手的形参名，不是线上字段名）。"""
        return {"prose_key": "p1", "text": text,
                "source_member_refs": list(member_refs),
                "source_fact_refs": list(fact_refs),
                "atom_candidate_keys": list(atoms)}

    for unit, section, msg, needle in (
            (_unit_with(P_A, fact_refs=["f1"], atoms=["c-proc"]), "company",
             "本节**有**材料行时改用事实轴必须被拒（读者面会回溯不到你实际写的那段文字）",
             "本节**有**材料行"),
            (_unit_with(P_A, member_refs=[bm_ref["m-bm-p15"]], fact_refs=["f1"],
                        atoms=["c-proc"]), "company",
             "两条出处轴同时非空必须被拒（互斥轴不得同时出现）",
             "同时声明了"),
            (_unit_with(P_A, atoms=["c-proc"]), "company",
             "两条轴都空必须被拒（草稿不得凭空：每句都要指得出一行真实输入）",
             "必须恰好声明一条出处轴"),
            (_unit_with(TXT_PROC, member_refs=[fin_fact_alias], atoms=["c1"]), "financial",
             "把**事实行**当作材料出处（source_member_refs）必须被拒——材料轴只取材料行",
             "只取材料行")):
        if section == "company":
            probe = _plan(candidates=bm_candidates, prose=[unit])
            task, authority, ctx = bm_task, bm_authority, bm_ctx
        else:
            probe = _plan(candidates=[fin_candidate], prose=[unit])
            task, authority, ctx = fin_task, fin_authority, None
        expect_error(
            lambda probe=probe, task=task, authority=authority, ctx=ctx, section=section:
                _write(task, authority, section_id=section,
                       llm=_StubLlm(probe, probe), material_context=ctx),
            PW.PackWriterError, msg, needle=needle)
    # 正向对照：同一份财务夹具、改回事实轴，必须走通——否则上面那条「只取材料行」可能只是
    # 因为整节本来就写不出来。
    fin_ok_plan = _plan(candidates=[fin_candidate],
                        draft_axis="fact", draft_ref=[fin_fact_alias])
    fin_ok = _write(fin_task, fin_authority, section_id="financial",
                    llm=_StubLlm(fin_ok_plan))
    check(len(fin_ok.draft.natural_prose_draft) == 1
          and tuple(fin_ok.draft.natural_prose_draft[0].source_fact_refs) != ()
          and fin_ok.draft.natural_prose_draft[0].source_member_refs == (),
          "纯 `FinancialFactPack` 节（材料清单合法为空）的非空事实轴草稿必须能走通正式接口")

    # ---- 27c. `pw-18`：示例的**轴**必须与同一份请求的输入面一致（又是判「实际发出的那份请求」）----
    # `pw-16` 判的是示例的**键集**，这一组判的是**键值**：同一份请求里，`output_schema` 的示例
    # 若把两条轴同时填满（或填了本节没有的那条轴），模型照抄示例即被同一份请求自己的 rules 与
    # 解析面判违规——不照抄又等于要它猜到示例反着写。两档节各用自己那份真实夹具，
    # 断言的对象仍只有一处：`probe_stub.calls[0]["messages"][0]["content"]`。
    example_units = sent["output_schema"]["natural_prose_draft"]
    check(len(example_units) == 1, "有材料行的节必须给出一条示例草稿单元，实测 "
          f"{len(example_units)} 条")
    example = example_units[0]
    check(bool(example["source_member_refs"]) and not example["source_fact_refs"],
          "**有材料行**的请求：示例必须走材料轴、且事实轴为空——两条轴同时非空就是请求自己"
          f"与自己的 rules 矛盾（实测 member={example['source_member_refs']!r} / "
          f"fact={example['source_fact_refs']!r}）")
    check(all(isinstance(r, str) and r.strip()
              for r in list(example["source_member_refs"]) + list(example["source_fact_refs"])),
          "示例里的占位 ref 不得是空串（空占位等于没写轴，与「恰有一条非空」是两回事）")
    check(bool(example["atom_candidate_keys"]),
          "示例必须点出一个占位候选键：键序即声明——草稿先于候选被读出来")
    draft_rule_text = next((str(r) for r in sent["rules"] if "source_fact_refs" in str(r)), "")
    check("都为空" in draft_rule_text and "follow_up_needs" in draft_rule_text,
          "请求的 rules 必须把**两表都空**那一档也说完（示例在那一档是空数组，不得让模型对着"
          f"一条无从展开的轴写正文），实为 {draft_rule_text[-160:]!r}")

    # 相反的一档：纯 `FinancialFactPack` 节（`materials == []`）。同一份判据必须在**它**身上也
    # 成立——否则「示例与输入面一致」只是因为公司节恰好走材料轴。判据看的是实际发出去的那份请求。
    fin_probe = _StubLlm(fin_ok_plan)
    _write(fin_task, fin_authority, section_id="financial", llm=fin_probe)
    sent_fin = json.loads(fin_probe.calls[0]["messages"][0]["content"])
    check(not sent_fin["materials"] and bool(sent_fin["authority_facts"]),
          "财务节的前置条件：精确材料清单合法为空、权威事实行非空")
    check(tuple(sent_fin["output_schema"]) == PW._PLAN_KEYS
          and tuple(sent_fin["output_schema"])[0] == "natural_prose_draft",
          "财务节的请求同样必须把草稿摆在输出面首位、四键逐位同序")
    fin_example = sent_fin["output_schema"]["natural_prose_draft"][0]
    check(not fin_example["source_member_refs"] and bool(fin_example["source_fact_refs"]),
          "**只有权威事实行**的请求：示例必须走事实轴、且材料轴为空"
          f"（实测 member={fin_example['source_member_refs']!r} / "
          f"fact={fin_example['source_fact_refs']!r}）")

    # 非空验证：示例与本请求输入面矛盾时，必须**在构造请求时**就抛错（不是发出去让模型踩）。
    # 与 `pw-16` 那一条同样是构造时判据，故逐条直调判据本身，另有一条走完整 `_write`。
    _ok_unit = {"prose_key": "p1", "text": "x", "source_member_refs": ["<m>"],
                "source_fact_refs": [], "atom_candidate_keys": ["c1"]}
    for bad, mats, fcts, msg, needle in (
            ([dict(_ok_unit, source_fact_refs=["<f>"])], [1], [1],
             "两条轴同时非空的示例必须被拒（这正是本批要修掉的旧写法）", "两条都写或都空"),
            ([dict(_ok_unit, source_member_refs=[], source_fact_refs=["<f>"])], [1], [1],
             "在**有材料行**的请求里给事实轴示例必须被拒（rules 逐字要求有材料行时走材料轴）",
             "示例必须走材料轴"),
            ([_ok_unit], [], [1],
             "在**只有事实行**的请求里给材料轴示例必须被拒",
             "材料轴在这里无从展开"),
            ([], [1], [1],
             "有表的请求不得给出空示例（模型看不到形状）", "不得为空"),
            ([_ok_unit], [], [],
             "两表都空的请求必须给空示例（否则示例指了一条无从展开的轴）", "都**为空"),
            ([dict(_ok_unit, source_member_refs=["   "])], [1], [1],
             "示例里不得出现空白占位 ref", "空占位")):
        expect_error(lambda bad=bad, mats=mats, fcts=fcts:
                     PW._assert_prose_example_consistent(bad, materials=mats, facts=fcts),
                     PW.PackWriterError, msg, needle=needle)

    # ---- 27d. `pw-19`：示例的**其余三格**（候选首边 / context 边 / 补件示例）也按输入面分档 ----
    # `pw-18` 只把**草稿**那一格分了档，另外三格仍按「两张表都存在」写死。三种输入面各用一份
    # 真实夹具、断言的对象仍只有**实际发出去的那份请求**（`probe_stub.calls[0]`）。每一条都判
    # 两件事：示例指向的是**哪一张表**，以及那张表在本请求里**真的有行**（可展开）。
    sent_support = sent["output_schema"]["claim_candidates"][0]["support"]
    sent_context = sent["output_schema"]["narrative_draft_units"][0]["context_support"]
    check(sent["materials"] and not sent["authority_facts"]
          and sent["support_refs"]["declared_material_refs"]
          and not sent["support_refs"]["declared_fact_refs"],
          "**仅材料**那一档的前置条件：本节只有材料行（公司/行业节），事实别名表必须为空")
    check(len(sent_support) == 1 and "materials" in str(sent_support[0]["ref"])
          and "authority_facts" not in str(sent_support[0]["ref"]),
          "**仅材料**的请求：候选首边示例必须指向 materials（这一档没有事实行可展开；"
          f"v8 的 `f1` 正是指向了一条不存在的行），实测 {sent_support!r}")
    check(len(sent_context) == 1 and "materials" in str(sent_context[0]["ref"]),
          f"**仅材料**的请求：context 示例必须指向 materials，实测 {sent_context!r}")
    check(str(sent["output_schema"]["follow_up_needs"][0]["budget_hint"]).strip(),
          "补件示例的 budget_hint 不得为空（空值在检索执行门上是 fail-closed 的，"
          "照抄这个例子会白写一条注定执行不了的申请）")
    sent_example_fu = sent["output_schema"]["follow_up_needs"][0]
    for key in ("target_requirement_id", "topic_id", "question_id", "aspect_id"):
        check("requestable_aspects" in str(sent_example_fu[key]),
              f"补件示例的 {key} 必须指向 `requestable_aspects`（四个 id 必须逐字取自**同一行**；"
              "`authority_facts` 行里的同名 `target_requirement_id` 可能是空串，不是来源）")
    check(bool(sent["requestable_aspects"])
          and all({"aspect_id", "topic_id", "question_id", "target_requirement_id"}
                  <= set(row) for row in sent["requestable_aspects"]),
          "四个 id 必须**同在一行**里给出（分散在两处输入面里，模型无从把它们对上）")
    fu_rule = next((str(r) for r in sent["rules"] if "budget_hint" in str(r)), "")
    check("requestable_aspects" in fu_rule and "不得为空" in fu_rule,
          f"请求的 rules 必须把「四个 id 取自同一行」与「budget_hint 不得为空」一次说完，"
          f"实为 {fu_rule[:160]!r}")

    # 相反的一档：**仅权威事实**（纯 `FinancialFactPack` 节，精确材料清单合法为空）。
    fin_support = sent_fin["output_schema"]["claim_candidates"][0]["support"]
    fin_context = sent_fin["output_schema"]["narrative_draft_units"][0]["context_support"]
    check(fin_support and all("authority_facts" in str(e["ref"]) for e in fin_support)
          and sent_fin["support_refs"]["declared_fact_refs"]
          and not sent_fin["support_refs"]["declared_material_refs"],
          "**仅权威事实**的请求：候选首边示例必须指向 authority_facts（这一档没有材料行，"
          f"路径 B 无从展开），实测 {fin_support!r}")
    check(fin_context == [],
          "**仅权威事实**的请求：context 示例必须是**空数组**——context 边只能绑定一份真实"
          f"material，这一档没有可绑的行（v8 的 `m1` 正是一条不存在的行），实测 {fin_context!r}")
    check(str(sent_fin["output_schema"]["follow_up_needs"][0]["budget_hint"]).strip(),
          "**仅权威事实**的请求里补件示例的 budget_hint 同样不得为空")

    # 第三档：**两张表都空**——这一档没有可用内容，示例的四格都必须留空（`[]`），
    # 否则模型照抄示例就会写出一条无从展开的边。构造与上面两档同一条路（`build_narration_messages`），
    # 因为这一档在写作链上不会**走到**模型（没有可写的行），但请求面本身必须自洽。
    empty_authority = _company_authority(bm_task, facts=(), materials=())
    empty_ctx = _writer_material_context(empty_authority.pack_set,
                                         task_id=str(bm_task.task_id), section_id="company")
    empty_messages, _empty_system = PW.build_narration_messages(
        task=bm_task, authority=empty_authority,
        scan=PW.scan_topic_pack(empty_authority, bm_task),
        projection=projections["company"], projection_aspects=(),
        unresolved=(), presentation_profile=profile,
        manifest=PW._derive_material_manifest(empty_authority, material_context=empty_ctx),
        material_context=empty_ctx)
    empty_sent = json.loads(empty_messages[0]["content"])
    check(not empty_sent["materials"] and not empty_sent["authority_facts"],
          "**两表都空**那一档的前置条件：materials 与 authority_facts 都是空数组")
    check(tuple(empty_sent["output_schema"]) == PW._PLAN_KEYS
          and empty_sent["output_schema"]["natural_prose_draft"] == []
          and empty_sent["output_schema"]["claim_candidates"][0]["support"] == []
          and empty_sent["output_schema"]["narrative_draft_units"][0]["context_support"] == [],
          "**两表都空**的请求：示例的四处出处/支撑必须全部留空——这一档没有任何可展开的行，"
          f"键序仍须与解析面一致（实测 {dict(empty_sent['output_schema'])!r}）")

    # 补件执行门与 Writer 入站防线**同源**：模型仍给出空 `budget_hint` 时，那**一条**必须在
    # Writer 侧成为 typed 拒绝并保留审计，不得等 Harness 获批执行时才抛——
    # 也不得因此丢掉同一份响应里已经合法成形的整节草稿。
    bm_req_id = bm_scan.requirement_ids.get(TOPIC_BUSINESS, "")
    good_fu = _follow_up("需要补充公司销售模式的口径说明。",
                         target_requirement_id=bm_req_id, topic_id=TOPIC_BUSINESS,
                         question_id=_question_id(TOPIC_BUSINESS),
                         aspect_id=ASP_BUSINESS_SALES)
    bad_fu = dict(good_fu, statement="需要补充公司生产模式的产能说明。", budget_hint="")
    check(bool(bm_req_id) and str(good_fu["budget_hint"]).strip()
          and not str(bad_fu["budget_hint"]).strip(),
          "前置条件：本节有真实需求 id，且同一份响应里一条诉求预算非空、另一条为空")
    fu_plan = dict(bm_plan)
    fu_plan["follow_up_needs"] = [good_fu, bad_fu]
    mixed_fu = _write(bm_task, bm_authority, section_id="company", llm=_StubLlm(fu_plan))
    check([n.statement for n in mixed_fu.follow_up_needs] == [good_fu["statement"]],
          "空预算的那条被拒后，同一份响应里**合法**的那条诉求必须照常成立"
          f"（实测 {[n.statement for n in mixed_fu.follow_up_needs]!r}）")
    check([r["code"] for r in mixed_fu.follow_up_rejections] == ["budget_hint_empty"]
          and mixed_fu.follow_up_rejections[0]["spec_index"] == 1
          and mixed_fu.follow_up_rejections[0]["statement"] == bad_fu["statement"],
          "空 budget_hint 必须**逐条**落成 typed 拒绝（封闭原因码 + 它自己那份响应里的序号 + "
          f"原样陈述），实测 {[dict(r) for r in mixed_fu.follow_up_rejections]!r}")
    check(mixed_fu.draft.draft_id == bm_outcome.draft.draft_id
          and mixed_fu.draft.draft_revision == bm_outcome.draft.draft_revision,
          "空预算的申请不得连带杀死同一份响应里**已经通过硬门**的候选与整节草稿"
          "（申请不成立 ≠ 整节不成立）")
    check("budget_hint_empty" in PW.FOLLOW_UP_REJECTION_CODES,
          "空预算的原因码必须是封闭词表里的一员（否则审计里的 code 是自由字符串）")
    # 判据同源：Writer 入站防线与检索执行门（`TR.build_follow_up_focus`）必须判同一件事——
    # 两处判得不一样，就会出现「Writer 侧放行、检索侧炸」或反过来。
    typed_need = mixed_fu.follow_up_needs[0]
    TR.build_follow_up_focus(typed_need, grant_reason="同源核对")
    passed += 1
    expect_error(lambda: TR.build_follow_up_focus(
        dataclasses.replace(typed_need, budget_hint=""), grant_reason="同源核对"),
        TR.TopicRuntimeError,
        "检索执行门对空 budget_hint 是 fail-closed 的——Writer 侧的理由必须与它逐字同源")
    # 正向对照：把预算补上，同一条形状必须走通（否则上面那条可能只是因为整节本来就写不出来）。
    fixed_fu = dict(bad_fu, budget_hint="tree_inspect:1")
    fixed_plan = dict(bm_plan)
    fixed_plan["follow_up_needs"] = [good_fu, fixed_fu]
    fixed_outcome = _write(bm_task, bm_authority, section_id="company",
                           llm=_StubLlm(fixed_plan))
    check(len(fixed_outcome.follow_up_needs) == 2 and not fixed_outcome.follow_up_rejections,
          "同一条诉求补上非空预算后必须照常成立（拒绝的是**那一格**，不是这一条诉求本身）")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

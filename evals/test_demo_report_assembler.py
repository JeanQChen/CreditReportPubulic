"""M930-3 反例集：`sections.report_assembler` 必须**独立复核**门后整条链，而不是相信上游自证。

本模块是**反例优先**的（里程碑 §11）。组装器的价值只在于「拼装时再查一遍」；因此这里的
断言几乎全部是「把上游对象改坏一点点，组装必须拒绝」。全部离线、确定性：权威输入由
`test_demo_pack_writer` 同族的**真实类型**合成工厂物化（`VerifiedPackSet` /
`TopicResearchPack` / `ResearchMaterial` / `TS.CitationRef`），生成器是返回结构化
`PW.NarrationResult` 的 stub，不读库、不连网、不调真实 LLM。

**本批（M930-3）的口径变化**：链已经是 narr-5 / asm-2。合法输入不再是「写作器自己产出的
`SectionResult` + 三条 narr-3 `ClaimSupportRef`」，而是
`CW.run_backbone_writer_phase` 走完「候选 → 两道门 → accepted binding → 门后定稿 Claim /
final Narrative / FND / current Result → 章级评估」之后的那一整束产物。因此本文件：

  * 用真实的写作相位驱动（`research_policy="harness"` + 门前提案束 stub + entailment stub），
    不再调用写作器内部函数，也不自造 `NarrativeParagraph` 冒充正文；
  * 断言 factual 边**恰好两条**决定（aggregate + entailment），context 边恰好一条
    （aggregate）且**不携带任何事实身份**；
  * 把「改坏一点点再组装」统一收敛到 `retamper(...)`：它把 draft/Result/门/评估/binding
    全部重绑成**自洽**的一束，逼组装器只能靠自己的实质复核发现缺陷；
  * 覆盖该缺陷族（对应任务书 §八 反例清单第 5–10、13、15、16 项）：
     1. Claim 血缘：未知 Claim / 伪 citation / 事后补引用 / 决定链缺失；
     2. 事实与去向：删掉一条被选中权威事实的去向、把 claimed 的事实从正文删掉、去向挂空；
     3. 缺口静默丢失：draft 与 SectionResult 的缺口集合不一致；
     4. DemoScope 覆盖：section / topic 既无正文也无显式缺口；
     5. 身份绑定：Draft/Gate/Evaluation/Result 四者逐字段互绑，任一错配即拒；
     6. 跨章节**类型化**精确冲突：同一权威锚点被两条已写 Claim 以不同数字断言；
     7. Markdown 确定性重建：同内容同 `report_version`，任何正文改动都会改变它；
     8. 非 300750 样本走完全相同的规则；
     9. 组装器不做研究、不写正文、不调用 Reviewer、不出具任何放行结论。

**本批（§三 C / §三 G）关闭的两处缺口**（原先在本文件里如实记为 skipped，现已改为正向断言）：
  * 门**后**的 final-Narrative 核验不再缺席：`NS.verify_section_narrative` 是它**唯一**的
    实现（引用当前性 / 事实表面逐字可追溯 / context 不授权事实 / 处置完备），组织器、章级
    评估、组装器三处共用；跨主题与跨段落的**事实表面守恒**落在
    `RA._numeric_assertions` + `RA._cross_section_conflicts`，且现在同时覆盖**段落自然组织句**
    与**表格行**（narr-5 之后段落面上不再有 factual 句，若只认 factual 句，这条守恒会退化成
    空集）。`NS.gate_draft` 仍然**不**实现这两条——它在门前，看不到 final Narrative。
  * `sections/rules_evaluator.py` 的章级评估现在读**门后 final Narrative**（narr-5），
    不再读 narr-3 的 `draft.paragraphs`；§十五 12/13 两条 LLM evaluator 反例因此变为可驱动
    的正向断言（畸形输出必须阻断 / LLM 的 blocking 不得被降级）。
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from planning import demo_scope_schema as DS
from planning import schema as PS
from sections import company_worker as CW
from sections import final_sentence_fidelity as FSF
from sections import material_context as MC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_set as PSet
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import report_assembler as RA
from sections import rules_evaluator as RE
from sections import schema as SS
from sections import writing_spec as WS

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = str(ROOT / "templates" / "writing_specs" / "credit_report_v1.yaml")
PROFILE_PATH = str(ROOT / "templates" / "presentation_profiles" / "interview_demo_v1.yaml")

COMPANY_ID = "示例科技股份有限公司"       # 非 300750 样本：规则必须完全一致
REPORT_AS_OF = "2026-06-30"
CONTRACT_VERSION = "v2"
CONTRACT_FINGERPRINT = "a" * 64
EV_ID = "ev-demo-1"

ASP_BUSINESS_MAIN = "company_business_main.main_business"
ASP_BUSINESS_SALES = "company_business_model.sales_mode"
ASP_BUSINESS_PROCUREMENT = "company_business_model.procurement_mode"
ASP_BUSINESS_MODEL = "company_business_model.production_mode"
ASP_INDUSTRY_SCALE = "industry_scale_cycle.industry_scale"
ASP_FIN_SOLVENCY = "fin_solvency.short_term_solvency"

TOPIC_BUSINESS = "company_business"
TOPIC_BUSINESS_MODEL = "company_business_model"
TOPIC_INDUSTRY_SCALE = "industry_scale_cycle"
TOPIC_FIN_SOLVENCY = "fin_solvency"

SHARED_FACT_ID = "f-shared"

# 报告身份的**权威**来源：冻结投影（§四/§六）。组装器不再接收散装身份字符串。
PLAN_ID = "dplan_demo_0001"
JOB_ID = "job-demo-1"
PROFILE_FINGERPRINT = "e" * 64
SCOPE_INPUT_FINGERPRINT = "f" * 64
SOURCE_POLICY_FINGERPRINT = "1" * 64
WRITING_SPEC_FINGERPRINT = "2" * 64
PRESENTATION_PROFILE_FINGERPRINT = "3" * 64
DEPENDENCY_FINGERPRINT = "d" * 64
CONTRACT_ASSET = "credit_report_v1.yaml"

#: 已登记的 material payload 信封字节（键 = sha256(字节) = `payload_ref.content_hash`）。
#: 真实链上这些字节来自 Evidence store 的 BLOB；夹具里由 `_material_of_identity` 生成，
#: `_StubPayloadResolver` 只负责「按 content_hash 取回」。**这不是替代校验**：
#: `TS.verify_material_payload_ref` 与 `wmctx-1` 的读取侧仍逐项复核身份/定位/哈希。
_PAYLOAD_BYTES: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# 合成工厂：**真实类型**的上游权威对象（不读库、不连网、不调真实 LLM）
#
# §三 P0：`TopicPackAuthorityInput` 只接受真实 `sections.pack_set.VerifiedPackSet`
# （其内是真实 `TopicResearchPack`），字段形状相同的替身一律被拒。因此这里不再手搭
# 「形状替身」，下面带 `_` 的 dataclass 只是**测试侧规格**，由 `_pack_set` 一次性物化成
# 真实对象；Pack 容器身份（`pack_id`）是**内容身份**，由 `TS.finalize_pack` 计算，测试
# 不得指定。
# ---------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SOURCE_POLICY_VERSION = "sp-demo-1"
DEPENDENCY_VERSIONS = TS.build_current_dependency_versions(
    contract_version=CONTRACT_VERSION, source_policy_version=SOURCE_POLICY_VERSION)
DOCUMENT_ID = "doc-demo-1"
DOCUMENT_VERSION = "v1"

#: `_aspect()` 的 `time_scope` 缺省值：**保守**取「按基准日 + 24 个月变动」，即「期间必须
#: 显式」（§六）。本文件的权威事实都自带期间，因此缺省即合法。
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


@dataclasses.dataclass(frozen=True)
class _AspectResult:
    aspect_id: str
    status: str


@dataclasses.dataclass(frozen=True)
class _AspectReq:
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
class _Pack:
    """`TopicResearchPack` 的**测试侧规格**（由 `_pack_set` 物化 + `TS.finalize_pack`）。

    真实 `pack_id` 是内容身份（`content_fingerprint + dependency_fingerprint`），因此本规格
    里**没有** `pack_id` 字段：容器身份不能被测试指定，也不能靠标签在两个章节间共享。
    """

    topic_id: str
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
    #: 是否按事实引用**自动补** material（真实 Pack 的形状）；反例可显式关掉。
    auto_materials: bool = True


@dataclasses.dataclass(frozen=True)
class _PackSet:
    """`VerifiedPackSet` 的**测试侧规格**（由 `_pack_set` 物化；仅作类型说明）。"""

    task_id: str
    section_id: str
    topic_ids: tuple[str, ...]
    packs: tuple[_Pack, ...] = ()
    requirements: tuple[_Req, ...] = ()


def _material_of_identity(material_id: str, source_identity: str, *,
                          page: int | None = 12) -> TS.ResearchMaterial:
    """按给定来源身份构造真实 `ResearchMaterial`（真实类型自己核对三方身份一致）。

    §三 A：`payload_ref.content_hash` 是**真实 payload 信封字节**的 sha256（= 载体层身份
    = payload_hash），而不是合成哈希——否则写入侧与语义门都无法真正解析出正文，测出来的
    「Writer 看得到正文」就是假的。信封字节登记进 `_PAYLOAD_BYTES`，由 `_payload_resolver()`
    取回；`ResearchMaterial.content_hash` 必须等于它（`__post_init__` 强制）。
    """
    raw_id = str(source_identity).partition(":")[2] or str(source_identity)
    locator = TS.EvidenceLocator(document_id=DOCUMENT_ID, document_version=DOCUMENT_VERSION,
                                 section_path="s1", page=page)
    authority = _evidence_authority(raw_id, page=page)
    # 两级内容身份（与真实链同形，不得混用）：来源层 `source_content_hash` = 父 Evidence 块的
    # content_hash（落在权威评估上、写进信封）；载体层 = payload 信封字节的 sha256。
    payload_bytes = _payload_envelope_bytes(
        material_id=material_id, source_identity=source_identity, page=page,
        text=_default_material_text(material_id),
        source_content_hash=str(authority.content_hash))
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


def _default_material_text(material_id: str) -> str:
    """夹具材料正文：**不含**任何高风险表面（数字/期间/否定/勾选/主体后缀）。

    路径 B 的授权基础是「正文里逐字可读出的非高风险描述」，因此默认正文必须是纯业务描述。
    """
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
            "document_version": DOCUMENT_VERSION, "evidence_set_version": "esv-demo-1",
        },
        "content": {"text": text, "structured_payload": structured},
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")


class _StubPayloadResolver:
    """夹具 `PayloadResolver`：按 `content_hash` 返回已登记的 payload 字节。

    它**不是**替身语义：`TS.verify_material_payload_ref` 与 `wmctx-1` 读取侧仍独立复核
    object_type / authority_identity / version / locator / content_hash 与字节哈希。
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
    """真实 `ResearchMaterial`：身份域即 `TS.citation_source_identity`（`evidence:<id>`）。"""
    kind, _, raw_id = str(source_identity).partition(":")
    if kind != "evidence" or not raw_id:
        raise AssertionError(
            f"测试 material 的来源身份必须是 `evidence:<id>` 形态，得到 {source_identity!r}")
    return _material_of_identity(material_id, source_identity, page=page)


def _chain_ids(material: TS.ResearchMaterial, spec: _Fact
               ) -> tuple[TS.FactCandidate, TS.FactQualificationDecision]:
    """派生 (candidate, eligible 决定)：身份口径与 runtime 的资格门逐字段同形。"""
    candidate = TS.build_fact_candidate(
        candidate_source_kind="topic_material", statement=spec.text, fact_type="fact",
        aspect_ids=tuple(spec.aspect_ids), question_ids=(),
        material_ids=(material.material_id,),
        period=spec.period or None, scope=spec.scope or None)
    source_identity = TS.authority_source_identity(material.authority_assessment)
    locator_digest = TS.sha256_canonical({"locators": [material.locator.to_dict()]})
    payload_digest = TS.sha256_canonical({"payloads": [material.payload_ref.to_dict()]})
    identity_digest = TS.sha256_canonical({
        "candidate_id": candidate.candidate_id,
        "candidate_revision": candidate.candidate_revision,
        "material_ids": [material.material_id],
        "material_content_hashes": [material.content_hash],
        "source_identity": source_identity,
        "locator_digest": locator_digest,
        "payload_digest": payload_digest,
    })
    decision = TS.build_qualification_decision(
        candidate, verdict="eligible", input_identity_digest=identity_digest,
        input_source_identity=source_identity, input_locator_digest=locator_digest,
        input_payload_digest=payload_digest)
    return candidate, decision


def _supported_fact(spec: _Fact, material: TS.ResearchMaterial) -> TS.SupportedFact:
    """`_Fact` 规格 → 真实 `SupportedFact`（经 M930-3 资格链；引用锚点即真实 `TS.CitationRef`）。"""
    refs = tuple(spec.citation_refs)
    value_identity = None
    if spec.value_identity is not None:
        value_identity = TS.ValueIdentity(
            value_kind=spec.value_identity.value_kind or "amount",
            metric=spec.value_identity.metric or spec.fact_id,
            unit=spec.value_identity.unit,
            period=spec.value_identity.period or spec.period,
            scope=spec.value_identity.scope,
            amount_canonical=spec.value_identity.amount_canonical)
    evidence_id = str(getattr(refs[0], "evidence_id", "") or "") if refs else ""
    cand, dec = _chain_ids(material, spec)
    fact = TS.build_supported_fact(
        spec.fact_id, cand, dec, text=spec.text, fact_type="fact",
        citation_refs=refs, source_authority=_evidence_authority(evidence_id))
    return dataclasses.replace(fact, value_identity=value_identity,
                               period=spec.period or None, scope=spec.scope or None)


def _fact_material(materials: dict[str, TS.ResearchMaterial],
                   spec: _Fact) -> TS.ResearchMaterial:
    """该 fact 的来源 material（资格链输入侧）：按第一个引用锚点的来源身份命中。

    反例夹具（`auto_materials=False`）下引用身份可能在本容器内没有 material：此时用一份
    **不入 Pack** 的合成来源 material 建链（Pack 四道门只看 bijection，不据此声称 material 存在）。
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


def _successors(materials: dict[str, TS.ResearchMaterial],
                results: tuple[TS.AspectResearchResult, ...],
                specs: tuple[_Fact, ...],
                fact_materials: dict[str, TS.ResearchMaterial]) -> dict:
    """由 Pack 的 materials/results 确定性重算 successor 六族（六族一律显式传入）。"""
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
            reason_proof="report assembler fixture",
            policy_version=TS.MATERIAL_DISPOSITION_VERSION)
        for aid in sorted(materials))
    cands, decs = [], []
    for spec in specs:
        cand, dec = _chain_ids(fact_materials[spec.fact_id], spec)
        cands.append(cand)
        decs.append(dec)
    return {
        "material_dispositions": dispositions, "fact_candidates": tuple(cands),
        "fact_qualification_decisions": tuple(decs), "external_facts": (),
        "contract_gaps": (), "research_blocks": ()}


def _question_id(topic_id: str) -> str:
    """`_task()` 给每个 topic 生成的问题 id。"""
    return f"q-{topic_id}"


def _materialize_pack(spec: _Pack, *, task_id: str, section_id: str,
                      req_spec: _Req) -> tuple[TS.TopicResearchRequirement, TS.TopicResearchPack]:
    """`_Pack`/`_Req` 规格 → (真实 `TopicResearchRequirement`, 真实 `TopicResearchPack`)。"""
    topic_id = str(spec.topic_id)
    declared: dict[str, _AspectReq] = {str(a.aspect_id): a for a in req_spec.aspects}
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

    materials: dict[str, TS.ResearchMaterial] = {
        str(m.material_id): _research_material(str(m.material_id), str(m.source_identity))
        for m in spec.materials}
    covered_identities = {m.source_identity for m in materials.values()}
    # M930-3：材料（含自动补的）必须先于事实解析完毕 —— 候选要回指来源 material。
    for fact_spec in sorted(spec.facts, key=lambda f: f.fact_id):
        if not spec.auto_materials:
            break
        for ref in fact_spec.citation_refs:
            identity = TS.citation_source_identity(ref)
            if identity in covered_identities:
                continue
            material_id = f"m-auto-{len(materials) + 1}"
            materials[material_id] = _research_material(
                material_id, identity, page=getattr(ref, "page_number", 12))
            covered_identities.add(identity)

    fact_materials: dict[str, TS.ResearchMaterial] = {}
    facts = []
    for fact_spec in spec.facts:
        material = _fact_material(materials, fact_spec)
        fact_materials[fact_spec.fact_id] = material
        facts.append(_supported_fact(fact_spec, material))
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
    stop_reason = "SECTION_BLOCKED" if any(r.status == "blocked" for r in results) else None
    process, coverage, derivation = TS.derive_pack_status(
        required_aspect_ids, results, stop_reason=stop_reason)
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id=spec.run_id,
        task_id=task_id, company_id=spec.company_id, report_as_of=spec.report_as_of,
        contract_version=spec.contract_version,
        contract_fingerprint=spec.contract_fingerprint,
        source_policy_version=SOURCE_POLICY_VERSION, section_id=section_id,
        topic_id=topic_id, question_ids=requirement.question_ids, aspect_results=results,
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
        source_set=TS.DocumentSourceSet.single_document(
            company_id=spec.company_id, document_id="doc-assembler-fixture",
            document_version="dv-1", evidence_set_version="esv-1"),
        **_successors(materials, results, tuple(spec.facts), fact_materials))
    return requirement, TS.finalize_pack(pack)


def _pack_set(task: PS.SectionTask, *, facts: tuple = (), materials: tuple = (),
              aspect_results: tuple = (), requirements: tuple = (),
              topic_ids: tuple[str, ...] | None = None, packs: tuple | None = None,
              company_id: str = COMPANY_ID, auto_materials: bool = True
              ) -> PSet.VerifiedPackSet:
    """规格 → **真实** `VerifiedPackSet`（§三 4：字段形状替身一律不得进入权威输入）。"""
    resolved_topic_ids = topic_ids if topic_ids is not None else task.topic_ids
    if packs is None:
        packs = tuple(
            _Pack(topic_id=tid, facts=facts, materials=materials, aspect_results=aspect_results,
                  auto_materials=auto_materials, company_id=company_id,
                  report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
                  contract_fingerprint=CONTRACT_FINGERPRINT)
            for tid in resolved_topic_ids)
    req_by_topic = {str(r.topic_id): r for r in requirements}
    built = [_materialize_pack(
        spec, task_id=task.task_id, section_id=task.section_id,
        req_spec=req_by_topic.get(str(spec.topic_id), _Req(str(spec.topic_id), ())))
        for spec in packs]
    return PSet.VerifiedPackSet(
        task_id=task.task_id, section_id=task.section_id,
        topic_ids=tuple(str(t) for t in resolved_topic_ids),
        packs=tuple(pack for _, pack in built),
        requirements=tuple(req for req, _ in built),
        binding=PSet.PackSetBinding(
            task_id=task.task_id, section_id=task.section_id, company_id=company_id,
            report_as_of=REPORT_AS_OF, contract_version=CONTRACT_VERSION,
            contract_fingerprint=CONTRACT_FINGERPRINT,
            source_policy_version=SOURCE_POLICY_VERSION,
            dependency_fingerprint=DEPENDENCY_FINGERPRINT,
            dependency_versions=dict(DEPENDENCY_VERSIONS)))


def _pack_id_of(authority: PW.TopicPackAuthorityInput, topic_id: str) -> str:
    """本节某 topic 的**真实** Pack 容器身份（内容身份，不是测试标签）。"""
    return str(authority.pack_set.pack_for(topic_id).pack_id)


#: 「还没有取过任何条目」与「脚本条目恰好是 `None`」必须分得开。
_UNSET = object()


class _StubLlm:
    """唯一的注入能力：按脚本返回文本。没有任何工具/检索入口。

    §五 1：stub 与真实 client 必须走**同一**结果接口 `PW.NarrationResult`，否则「测试里过的
    路径」与「真实跑的路径」就不是同一条。

    §三 C：门后自然组织是**第二次** LLM 调用（`narrative_organizer`）。它的输入面完全由已
    定稿 Claim 决定，而 Claim 的 id 只在两道门跑完后才存在——脚本无法预先写出。因此这一路
    不走队列，而是**当场按输入面**合成一份合法计划（见 `_organizer_result`）；队列仍只服务
    门前提案束，`stub LLM 被超额调用` 这条纪律一字不改。

    `wbatch-1`：门前提案束按 Contract aspect 范围**分批**请求后，脚本的粒度是**轮**而不是
    调用——一个条目 = 这一节这一次要交代的提案集，同轮的每一批都拿到它（见 `_is_new_round`）。
    对这些夹具而言分批因此是**透明**的：同一份计划被切成几片再合并，去重后还是那份计划。
    这正是「分批只切请求面」在节级产物上的可观测形式；若哪天分批开始改变节级结果，这里会
    先炸。
    """

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        #: 当前这一轮的脚本条目（同轮的后续批次与缩小重问复用同一条）。
        self._current: object = _UNSET

    def _organizer_result(self, messages, prompt_version) -> PW.NarrationResult:
        """按输入 Claim **逐字**组织：同一 topic 的 Claim 用纯衔接语串成一句。

        文本是所绑 Claim 正文的逐字拼接 + 一个不含事实的衔接语（`，同时，`），不新增任何字面
        成分——因此它既证明「组织器能把多条 Claim 组织进一句」，又不会撞上 §三 C 的表面守恒
        （新增数字/主体/因果都要被拒）。§七 1 之后，**衔接语本身是必需的**：把 Claim 文本直接
        首尾相接（`"".join(...)`）正是「机械拼接」，会被门后的第 8 条判据拒绝。判据 e
        （`nrules-12`）之后，衔接语还**必须**落在分隔标点后面：接缝以 `，` 起头（`A，同时，B`），
        光杆写在接缝开头（`A同时，B`）读成一句病句，同样会被拒。
        """
        payload = json.loads(messages[-1]["content"])
        by_topic: dict[str, list[dict]] = {}
        for claim in payload["claims"]:
            by_topic.setdefault(str(claim["topic_id"]), []).append(claim)
        # 一句最多 `NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE` 条 Claim（§七 1）：超过就**多写一句**，
        # 不是把一节的事实倒进一个超长句（真实财务节 24 条 Claim 正是这么被写坏的）。替身因此按
        # 该上界切块，每块一句。
        #
        # 但切块之前还有一道**先决**约束（§七 1 判据 d）：一句里句末标点至多出现在末尾。因此
        # 两条各自以 `。` 收尾的 Claim **合不成一句**——`A。同时，B。` 在文本层就是两个句子，
        # 衔接语只是把断句伪装成了自然句。替身据此刻画「能不能接」：一个句末点都不带的 Claim 才
        # 参与相接，自带句末点的各自成句（单独成句时句末标点落在末尾，合法）。同一 topic 的所有
        # 句子仍在**同一个自然段**里，因此「多 Claim 自然段」这条仍然成立。
        limit = NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE
        terminators = tuple(NS.SENTENCE_TERMINATORS)

        def _joinable(text) -> bool:
            """这条 Claim 能否与别的 Claim 相接：它必须**一个句末点都不带**。"""
            return not any(t in str(text) for t in terminators)

        def _sentences_of(group: list[dict]) -> list[list[dict]]:
            """按输入顺序把同一 topic 的 Claim 切句（顺序不得重排：声明顺序要与正文一致）。"""
            out: list[list[dict]] = []
            pending: list[dict] = []
            for claim in group:
                if not _joinable(claim["text"]):
                    if pending:
                        out.append(pending)
                        pending = []
                    out.append([claim])
                    continue
                pending.append(claim)
                if len(pending) == limit:
                    out.append(pending)
                    pending = []
            if pending:
                out.append(pending)
            return out

        plan = {
            "paragraphs": [{"sentences": [
                {"text": "，同时，".join(str(c["text"]) for c in chunk),
                 "claim_ids": [str(c["claim_id"]) for c in chunk]}
                for chunk in _sentences_of(group)]}
                for group in by_topic.values()],
            "claim_dispositions": [
                {"claim_id": str(c["claim_id"]), "disposition": "selected", "reason_code": None}
                for c in payload["claims"]],
        }
        return PW.NarrationResult(
            text=json.dumps(plan, ensure_ascii=False),
            call_id=f"organizer-{len(self.calls)}", model=PW.MODEL_POLICY_STUB,
            prompt_version=prompt_version, status="ok",
            input_tokens=64, output_tokens=32, latency_ms=1, finish_reason="stop")

    def _is_new_round(self, messages) -> bool:
        """本请求是否开启**新一轮**（一轮 = 一次分批扫描，见 `wbatch-1`）。

        脚本里的一个条目是「这一节这一次要交代的提案集」，不是「一次调用要回什么」——分批把
        一次请求面切成几批之后，同一个提案集仍然只有一份。因此只有新一轮才取下一个条目：
        无 `batch` 块（零 aspect 的那一批）或 `index == 1 且 shrink_depth == 0`。
        同一批的**缩小重问**（`index == 1, shrink_depth > 0`）属于同一轮，不得再取条目。
        """
        try:
            payload, _end = json.JSONDecoder().raw_decode(messages[0]["content"])
        except Exception:  # noqa: BLE001
            return True
        batch = payload.get("batch")
        if not batch:
            return True
        return batch.get("index") == 1 and batch.get("shrink_depth", 0) == 0

    def narrate(self, *, messages, system, prompt_version, model_policy) -> PW.NarrationResult:
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy,
                           "messages": messages, "system": system})
        if prompt_version == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION:
            return self._organizer_result(messages, prompt_version)
        if self._is_new_round(messages) or self._current is _UNSET:
            if not self.responses:
                raise AssertionError("stub LLM 被超额调用")
            self._current = self.responses.pop(0)
        item = self._current
        index = len(self.calls)
        if isinstance(item, Exception):
            return PW.NarrationResult(
                text="", call_id=f"err-{index}", model=PW.MODEL_POLICY_STUB,
                prompt_version=prompt_version, status="error",
                error=f"{type(item).__name__}: {item}")
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return PW.NarrationResult(
            text=text, call_id=f"call-{index}", model=PW.MODEL_POLICY_STUB,
            prompt_version=prompt_version, status="ok",
            input_tokens=128, output_tokens=64, latency_ms=1, finish_reason="stop")


class _StubEntailmentClient:
    """语义门（P9）的结构化 stub：只回可编程判定文本，不联网、不真实调用。"""

    def __init__(self, text, *, status="ok") -> None:
        self.calls: list[dict] = []
        self.text = text
        self.status = status

    def evaluate(self, *, messages, system, prompt_version, model_policy, **extra):
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        return PW.NarrationResult(
            text=self.text, call_id=f"call-{len(self.calls)}",
            model=PW.resolve_model_policy(model_policy), prompt_version=prompt_version,
            status=self.status, error="" if self.status == "ok" else "transport down")


_ENTAILED = '{"verdict": "entailed", "reason_code": null, "rationale": "权威逐字给出"}'
_NOT_ENTAILED = ('{"verdict": "rejected", "reason_code": "unsupported_specificity", '
                 '"rationale": "过细"}')


def _stub_final_sentence_client():
    """最终句语义门（B / `nsfid-1`）的确定性替身。

    **它不能像蕴含门的 stub 那样只回一句常量**：B 门要求输出**逐句逐原子**认领请求面里机械
    定位出的每一条原子，聚合结论再由逐原子结果推出（见 `sections/final_sentence_fidelity.py`
    的判定面），一句固定的 `entailed` 在结构上必然被拒。因此这里直接复用验收 runner 的
    `OfflineFinalSentenceClient`——**同一实现**，不在这里复制一份规则（复制出来的第二份规则会在
    某一次修订后与生产那一份悄悄分叉）。

    它只读请求面、只逐条认领已定位的原子，**不做任何语义判断**：这不构成「语义门已覆盖」的
    证据，只是让这条链在 B 门**在场**时能贯通。
    """
    from evaluation import run_m930_3_acceptance as ACC

    return ACC.OfflineFinalSentenceClient()


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


def _prose_reference(index: int, *, draft_ref: str, draft_axis: str) -> dict:
    """一条草稿出处（短别名形式）。它是**出处标注**，不是授权：`pprov-1` 的两条轴互斥。"""
    if draft_axis == "material":
        return {"source_member_refs": [draft_ref]}
    return {"source_fact_refs": [draft_ref]}


def _plan(*, candidates=(), units=(), follow_ups=(), prose=(), drafted: bool = True,
          draft_ref: "str | Sequence[str]" = "m1", draft_axis: str = "material") -> dict:
    """门前提案束（当前线 `PROPOSAL_WIRE_CURRENT` 的**唯一**模型输出形态）。

    `natural_prose_draft`（`pw-15`）是**自然草稿层**：写作顺序是硬的（先起草稿、再为草稿里每个
    事实原子单独提交候选），因此它在键序上排在最前。`pw-16` 起「有候选、无草稿」是 typed
    failure（`natural_prose_draft_missing`）——旧夹具在这里少给了一层，于是每一节都会先被拒一次、
    再重问，stub 的脚本被吃光后以「超额调用」失败。`drafted`（缺省 `True`）在**给了候选而没给
    `prose`** 时按候选逐条回填草稿单元，回填的形状就是模型会写的形状（草稿文本与候选文本相同是
    最省事的合法填法；原子闭合判的是键的映射，不是两段文字是否一样）。

    `draft_ref` / `draft_axis`（缺省材料轴 `m1`）必须按**本节的材料面**给对：公司/行业节 ->
    材料轴（`m1..mM`）；材料面合法为空的节（纯 `FinancialFactPack` 的财务节）-> 事实轴
    （`f1..fN`）。给错轴不会被静默改写成对的那条：写入侧照拒。`draft_ref` 也可是**逐候选**的一
    串，长度必须与候选一一对应（多事实的节按本节别名表的真实顺序给，不猜）。
    `drafted=False` 显式写出「有候选、无草稿」这条反例。
    """
    refs = ([draft_ref] * len(candidates) if isinstance(draft_ref, str)
            else list(draft_ref))
    if refs and len(refs) != len(candidates):
        raise AssertionError(
            f"_plan 的 draft_ref 逐候选给出时必须与候选一一对应（{len(refs)} vs "
            f"{len(candidates)}）——对不上就是夹具在猜哪条候选出自哪一行")
    prose_units = [dict(p) for p in prose]
    if not prose_units and drafted and candidates:
        for index, cand in enumerate(candidates):
            key = str(cand.get("candidate_key") or f"c{index + 1}")
            prose_units.append({
                "prose_key": f"p{index + 1}",
                "text": str(cand.get("claim_text") or ""),
                **_prose_reference(index, draft_ref=refs[index], draft_axis=draft_axis),
                "atom_candidate_keys": [key]})
    return {"natural_prose_draft": prose_units,
            "claim_candidates": [dict(c) for c in candidates],
            "narrative_draft_units": [dict(u) for u in units],
            "follow_up_needs": [dict(f) for f in follow_ups]}


def _entries(scan) -> dict:
    """权威事实目录：`fact_id → AuthorityFactEntry`（坐标一律从这里回查）。"""
    return {str(e.fact_id): e for e in scan.facts}


def _material_ids_of(scan) -> dict:
    """容器 → material_id 集合（context 边只能挂确实存在的 material）。"""
    out: dict = {}
    for entry in scan.facts:
        if entry.material_id:
            out.setdefault(str(entry.container_identity), set()).add(str(entry.material_id))
    return out


def _citation(page: int | None = 12) -> TS.CitationRef:
    """真实事实引用锚点（与 `TS.citation_source_identity` 同一身份域）。"""
    return TS.CitationRef(ref_type="evidence", evidence_id=EV_ID, page_number=page)


def _fact(fact_id: str, text: str, aspect_ids: tuple[str, ...]) -> _Fact:
    # §十二 1/3：事实期间只能来自事实自身；没有显式期间的 fact 不进入正文（只留
    # `period_unresolved` 缺口），因此这里的权威 fixture 必须带自己的期间。
    return _Fact(fact_id=fact_id, text=text, aspect_ids=aspect_ids,
                 citation_refs=(_citation(),), period=REPORT_AS_OF)


def _projection_id(*, dependency_fingerprint: str = DEPENDENCY_FINGERPRINT) -> str:
    """冻结投影的身份（`PROJECTION_ID_FIELDS` 的 16 项内容依赖，与 `_projection` 同源）。

    只有这 16 项进 `projection_id`；`plan_id` / `report_plan` 不在其中，所以任务 ID
    可以先于投影体派生。
    """
    return DS.derive_demo_projection_id({
        "projection_rule_version": DS.DEMO_PROJECTION_ID_RULE_VERSION,
        "profile_fingerprint": PROFILE_FINGERPRINT,
        "scope_input_fingerprint": SCOPE_INPUT_FINGERPRINT,
        "contract_asset": CONTRACT_ASSET, "contract_version": CONTRACT_VERSION,
        "contract_fingerprint": CONTRACT_FINGERPRINT,
        "source_policy_id": "sp-demo", "source_policy_version": "v1",
        "source_policy_fingerprint": SOURCE_POLICY_FINGERPRINT,
        "writing_spec_id": "ws-demo", "writing_spec_version": "v1",
        "writing_spec_fingerprint": WRITING_SPEC_FINGERPRINT,
        "presentation_profile_id": "pp-demo", "presentation_profile_version": "v1",
        "presentation_profile_fingerprint": PRESENTATION_PROFILE_FINGERPRINT,
        "dependency_fingerprint": dependency_fingerprint,
    })


def _dtask(section_id: str, *, plan_id: str = PLAN_ID) -> str:
    """`dtask_` 任务 ID 用冻结规则派生（不手写字符串冒充投影里的任务）。"""
    return DS.derive_demo_task_id(_projection_id(), plan_id, section_id)


def _task(section_id: str, topic_ids: tuple[str, ...], *, task_id: str = "",
          title: str = "示例章节", plan_id: str = PLAN_ID,
          research_policy: str = "harness") -> PS.SectionTask:
    task_id = task_id or _dtask(section_id, plan_id=plan_id)
    questions = tuple(
        PS.PlannedQuestion(question_id=f"q-{tid}", question=f"{tid} 的问题？",
                           priority="required", topic_id=tid, impact_scope=("subject",))
        for tid in topic_ids)
    return PS.SectionTask(
        task_id=task_id, plan_id=plan_id, section_id=section_id, title=title,
        purpose="代表作纵向切片。", research_policy=research_policy, topic_ids=topic_ids,
        questions=questions, output_requirements=(), evaluation_rule_ids=(),
        allowed_capabilities=("local",), blocking_rules=())


def _projection(section_tasks, *, job_id: str = JOB_ID, company_id: str = COMPANY_ID,
                report_as_of: str = REPORT_AS_OF,
                dependency_fingerprint: str = DEPENDENCY_FINGERPRINT,
                plan_id: str = PLAN_ID):
    """构造**真的** `DemoPlanningProjection` 作为报告身份的权威根（§四/§六）。

    组装器只接受这个类型；`projection_id` 由冻结规则从投影规范体派生，因此这里的
    权威对象是自洽的，不是「同形替身」。
    """
    tasks = tuple(section_tasks)
    plan = PS.ReportPlan(
        plan_id=plan_id, job_id=job_id, company_id=company_id, company_name=company_id,
        credit_type="general_credit", report_as_of=report_as_of,
        template_id=CONTRACT_ASSET, input_fingerprint=SCOPE_INPUT_FINGERPRINT,
        contract_fingerprint=CONTRACT_FINGERPRINT, planner_version="demo-planner-v1",
        section_tasks=tasks, created_at="")
    body = {
        "schema_version": DS.DEMO_PLANNING_PROJECTION_SCHEMA_VERSION,
        "projection_rule_version": DS.DEMO_PROJECTION_ID_RULE_VERSION,
        "profile_id": "interview_demo_v1", "profile_version": "v1",
        "profile_fingerprint": PROFILE_FINGERPRINT,
        "scope_input_fingerprint": SCOPE_INPUT_FINGERPRINT,
        "job_id": job_id, "company_id": company_id, "report_as_of": report_as_of,
        "contract_asset": CONTRACT_ASSET, "contract_version": CONTRACT_VERSION,
        "contract_fingerprint": CONTRACT_FINGERPRINT,
        "source_policy_id": "sp-demo", "source_policy_version": "v1",
        "source_policy_fingerprint": SOURCE_POLICY_FINGERPRINT,
        "writing_spec_id": "ws-demo", "writing_spec_version": "v1",
        "writing_spec_fingerprint": WRITING_SPEC_FINGERPRINT,
        "presentation_profile_id": "pp-demo", "presentation_profile_version": "v1",
        "presentation_profile_fingerprint": PRESENTATION_PROFILE_FINGERPRINT,
        "dependency_fingerprint": dependency_fingerprint,
        "plan_id": plan_id, "report_plan": plan, "requirements": (),
        "aspect_writing_bindings": (),
        "selected_section_ids": tuple(t.section_id for t in tasks),
        "selected_topic_ids": tuple(sorted({tid for t in tasks for tid in t.topic_ids})),
        "selected_question_ids": (), "selected_aspect_ids": (),
        "out_of_scope_section_ids": (), "out_of_scope_topic_ids": (),
        "out_of_scope_question_ids": (), "out_of_scope_aspect_ids": (),
    }
    body["projection_id"] = DS.derive_demo_projection_id(
        {k: body[k] for k in DS.PROJECTION_ID_FIELDS})
    assert body["projection_id"] == _projection_id(
        dependency_fingerprint=dependency_fingerprint), "投影体与 _projection_id() 不同源"
    return DS.DemoPlanningProjection(**body)


def _version_inputs(ordered, *, topic_pack_ids, financial_fact_pack_artifact_id=None,
                    model_policy_id: str = "stub",
                    prompt_version: str = PW.NARRATION_PROMPT_VERSION,
                    scope_input_fingerprint: str = SCOPE_INPUT_FINGERPRINT,
                    plan_id: str = PLAN_ID) -> RA.ReportVersionInputs:
    """调用方**声明**的运行时身份（待组装器与权威对账，§四 1）。"""
    return RA.ReportVersionInputs(
        scope_input_fingerprint=scope_input_fingerprint, plan_id=plan_id,
        selected_task_ids=tuple(sorted(i.task.task_id for i in ordered)),
        topic_pack_ids=tuple(sorted(topic_pack_ids)),
        financial_fact_pack_artifact_id=financial_fact_pack_artifact_id,
        model_policy_id=model_policy_id, prompt_version=prompt_version)


def _authority(task: PS.SectionTask, *, facts: tuple, aspects: tuple,
               requirements: tuple, company_id: str = COMPANY_ID,
               auto_materials: bool = True, packs: tuple | None = None
               ) -> PW.TopicPackAuthorityInput:
    """真实 `TopicPackAuthorityInput`：容器身份由 Pack 内容派生，测试**不得**指定。

    `packs` 供「同一 task 的多个 topic 各带**不同** Pack 内容」的场景使用（逐节容器精确性
    的正例）；缺省时按 task 的 topic 集用同一批 facts 物化（历史上唯一用到的形态）。
    """
    pack_set = _pack_set(task, facts=facts, aspect_results=aspects,
                         requirements=requirements, company_id=company_id,
                         auto_materials=auto_materials, packs=packs)
    return PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=company_id, report_as_of=REPORT_AS_OF,
        contract_version=CONTRACT_VERSION, contract_fingerprint=CONTRACT_FINGERPRINT)


def _assembly_input(section, task, authority) -> RA.SectionAssemblyInput:
    """`BackboneSectionWriterOutput` → `SectionAssemblyInput`（**逐字段**搬运，不重算任何东西）。

    组装器接下来会自己重算决定链、final Narrative、覆盖/缺口与 FND 引用集；这里只是把
    门后定稿的那一整束产物按 asm-2 的字段原样交给它。
    """
    return RA.SectionAssemblyInput(
        task=task, authority=authority, draft=section.draft, gate_result=section.gate_result,
        aggregate_decisions=section.aggregate_decisions,
        entailment_decisions=section.entailment_decisions, acceptance=section.acceptance,
        claims=section.claims, narrative=section.narrative,
        claim_narrative_dispositions=section.claim_narrative_dispositions,
        dispositions=section.dispositions,
        result=section.result, evaluation=section.evaluation, binding=section.binding,
        # §12.4.4 第 4 步：门后定稿形成的那一条最终句决定（逐字段搬运，不重算）。
        final_sentence_decisions=tuple(section.final_sentence_decisions))


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

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:200]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:200]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    spec = WS.load_writing_spec(SPEC_PATH)
    profile = PP.load_presentation_profile(PROFILE_PATH)
    projections = {sid: PW.ContractProjection.create(
        spec, section_id=sid, contract_version=CONTRACT_VERSION,
        contract_fingerprint=CONTRACT_FINGERPRINT) for sid in ("company", "industry")}

    # ------------------------------------------------------------------
    # 0. 组装器自身的边界：不做研究、不写正文、不调用 Reviewer
    # ------------------------------------------------------------------
    src = (ROOT / "sections" / "report_assembler.py").read_text(encoding="utf-8")
    import_lines = [ln.strip() for ln in src.splitlines()
                    if re.match(r"^\s*(from|import)\s", ln)]
    check(not any(re.search(r"ToolRegistry|tool_registry|\brouter\b|retriev|review_agent|"
                            r"assurance", ln, re.IGNORECASE) for ln in import_lines),
          "组装器不得 import Router/ToolRegistry/Reviewer/Assurance 的任何入口")
    check("reviewer" not in {n.lower() for n in dir(RA)},
          "组装器不得暴露 Reviewer 入口")
    check("release" not in json.dumps(
        [n for n in dir(RA) if not n.startswith("_")]).lower(),
        "组装器不得暴露任何放行入口")

    # ------------------------------------------------------------------
    # 1. 两个章节（company / industry）的**真实**门后链 + 合法组装
    # ------------------------------------------------------------------
    co_task = _task("company", (TOPIC_BUSINESS,))
    # 三条 aspect：一条 covered、两条 partial。partial 的 aspect 上有权威事实但本节未呈现时，
    # 必须留下**事实级**缺口与带 unresolved 锚点的去向，而不是静默消失。
    co_aspects = (_AspectResult(ASP_BUSINESS_MAIN, "covered"),
                  _AspectResult(ASP_BUSINESS_SALES, "partial"),
                  _AspectResult(ASP_BUSINESS_PROCUREMENT, "partial"))
    co_requirements = (_Req(TOPIC_BUSINESS, (
        _AspectReq(ASP_BUSINESS_MAIN, TOPIC_BUSINESS, "q-company_business"),
        _AspectReq(ASP_BUSINESS_SALES, TOPIC_BUSINESS, "q-company_business"),
        _AspectReq(ASP_BUSINESS_PROCUREMENT, TOPIC_BUSINESS, "q-company_business"),)),)
    co_facts = (_fact("f-main", "公司主营业务为动力电池系统的研发、生产与销售。",
                      (ASP_BUSINESS_MAIN,)),
                _fact("f-sales", "公司销售模式以直销为主。", (ASP_BUSINESS_SALES,)),
                _fact("f-procurement", "公司原材料采购以长期协议为主。",
                      (ASP_BUSINESS_PROCUREMENT,)))
    co_authority = _authority(co_task, facts=co_facts, aspects=co_aspects,
                              requirements=co_requirements)
    co_pack_id = _pack_id_of(co_authority, TOPIC_BUSINESS)
    co_scan = PW.scan_topic_pack(co_authority, co_task)
    co_entry = _entries(co_scan)
    co_material_id = sorted(_material_ids_of(co_scan)[co_pack_id])[0]
    check(set(co_entry) == {"f-main", "f-sales", "f-procurement"},
          f"权威事实目录必须逐条可回查，实为 {sorted(co_entry)}")

    # 门前提案束：两条 factual 候选（逐字复用权威事实文本）+ 一个只挂 context 的草稿单元。
    # f-procurement 故意**不呈现**（partial aspect）：它必须留下显式去向而不是静默消失。
    co_plan = _plan(
        candidates=[_cand("c-main", co_entry["f-main"].text, _fact_edge(co_scan, "f-main")),
                    _cand("c-sales", co_entry["f-sales"].text, _fact_edge(co_scan, "f-sales"))],
        units=[_unit("u-1", "本节就公司主营业务与销售模式作背景说明。",
                     context=[_context_edge(co_pack_id, co_material_id)])])
    co_phase = CW.run_backbone_writer_phase(
        (CW.BackboneWriterSectionInput(
            task=co_task, authority=co_authority, projection=projections["company"],
            writing_spec=spec, presentation_profile=profile,
            dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
        llm_client=_StubLlm(co_plan),
        entailment_llm_client=_StubEntailmentClient(_ENTAILED),
        final_sentence_llm_client=_stub_final_sentence_client(),
        # §三 A：写作相位必须拿到材料正文解析器（组合根注入），否则 fail-closed。
        material_resolver=_payload_resolver())
    co_section = co_phase.sections[0]
    co_input = _assembly_input(co_section, co_task, co_authority)
    check(not co_phase.follow_up_runs and not co_section.follow_up_needs,
          "合法章节不发出 FollowUpNeed，也不产生补件运行")

    # --- narr-4 决定链的形状：factual 边恰好两条决定，context 边恰好一条 ---
    factual_bounds = tuple(b for b in co_section.acceptance.accepted_bindings
                           if str(b.support_semantics) == "factual")
    context_bounds = tuple(b for b in co_section.acceptance.accepted_bindings
                           if str(b.support_semantics) == "context")
    check(len(co_section.aggregate_decisions) == 3,
          f"3 个 subject revision（2 候选 + 1 草稿单元）各一条 aggregate 决定，实为 "
          f"{len(co_section.aggregate_decisions)}")
    check(len(co_section.entailment_decisions) == 2,
          f"只有 factual 候选进语义门：2 条 entailment 决定，实为 "
          f"{len(co_section.entailment_decisions)}")
    check(len(factual_bounds) == 2 and len(context_bounds) == 1,
          f"2 条 factual + 1 条 context accepted binding，实为 "
          f"{len(factual_bounds)}/{len(context_bounds)}")
    check(all(b.entailment_decision_id for b in factual_bounds),
          "factual accepted binding 必须携带 entailment 决定")
    check(all(not b.entailment_decision_id for b in context_bounds),
          "context accepted binding 不得携带 entailment 决定（context 不进入语义门）")
    check(not any(getattr(b, name, None) for b in context_bounds
                  for name in NS.ALL_FACT_FIELDS),
          "context accepted binding 不携带任何事实身份")
    check(co_section.result.status == "COMPLETED_WITH_GAPS",
          f"company 章节：有正文且有 partial 缺口，实为 {co_section.result.status!r}")
    check(co_section.evaluation.decision in ("PASS", "PASS_WITH_GAPS"),
          f"合法章节的评估结论应是 PASS/PASS_WITH_GAPS，实为 "
          f"{co_section.evaluation.decision!r}")
    check(co_section.evaluation.decision == "PASS_WITH_GAPS",
          "有显式缺口的章节评估只能是 PASS_WITH_GAPS（缺口必须在评估里体现）")
    check(len(co_section.claims) == 2 and len(co_section.narrative.paragraphs) == 1
          and not co_section.narrative.tables,
          f"final Narrative 由定稿 Claim 重算：2 条 Claim / 1 段 / 0 表，实为 "
          f"{len(co_section.claims)}/{len(co_section.narrative.paragraphs)}/"
          f"{len(co_section.narrative.tables)}")
    check(tuple(co_section.narrative.context_binding_ids) == tuple(sorted(
        b.accepted_support_binding_id for b in context_bounds)),
          "final Narrative 的 context_binding_ids 恰好等于已接受的 context binding 集")
    co_disp = {d.disposition_key: d for d in co_section.dispositions}
    check(len(co_section.dispositions) == 3,
          f"3 条被选中权威事实各恰一条 FND，实为 {len(co_section.dispositions)}")
    check(sorted(d.disposition for d in co_section.dispositions)
          == ["claimed", "claimed", "not_presented_with_reason"],
          "两条被呈现的事实 claimed、一条未呈现的事实 not_presented_with_reason："
          f"实为 {sorted(d.disposition for d in co_section.dispositions)}")
    co_unpresented = [d for d in co_section.dispositions
                      if d.disposition == "not_presented_with_reason"]
    check(len(co_unpresented) == 1
          and str(co_unpresented[0].disposition_key[2]) == "f-procurement"
          and co_unpresented[0].unresolved_id is None,
          "未呈现的是 f-procurement，且它**不是** Contract 必需事实因此不挂缺口"
          "（`not_used`/未呈现本身不是 gap）")

    ind_task = _task("industry", (TOPIC_INDUSTRY_SCALE,))
    ind_aspects = (_AspectResult(ASP_INDUSTRY_SCALE, "covered"),)
    ind_requirements = (_Req(TOPIC_INDUSTRY_SCALE, (
        _AspectReq(ASP_INDUSTRY_SCALE, TOPIC_INDUSTRY_SCALE, "q-industry_scale_cycle"),)),)
    ind_facts = (_fact("f-scale", "行业规模为 1200.00 亿元。", (ASP_INDUSTRY_SCALE,)),)
    ind_authority = _authority(ind_task, facts=ind_facts,
                               aspects=ind_aspects, requirements=ind_requirements)
    ind_pack_id = _pack_id_of(ind_authority, TOPIC_INDUSTRY_SCALE)
    ind_scan = PW.scan_topic_pack(ind_authority, ind_task)
    ind_entry = _entries(ind_scan)
    ind_material_id = sorted(_material_ids_of(ind_scan)[ind_pack_id])[0]
    ind_plan = _plan(
        candidates=[_cand("c-scale", ind_entry["f-scale"].text,
                          _fact_edge(ind_scan, "f-scale"))],
        units=[_unit("u-1", "本节说明行业规模与周期位置。",
                     context=[_context_edge(ind_pack_id, ind_material_id)])])
    ind_phase = CW.run_backbone_writer_phase(
        (CW.BackboneWriterSectionInput(
            task=ind_task, authority=ind_authority, projection=projections["industry"],
            writing_spec=spec, presentation_profile=profile,
            dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
        llm_client=_StubLlm(ind_plan),
        entailment_llm_client=_StubEntailmentClient(_ENTAILED),
        final_sentence_llm_client=_stub_final_sentence_client(),
        material_resolver=_payload_resolver())
    ind_section = ind_phase.sections[0]
    ind_input = _assembly_input(ind_section, ind_task, ind_authority)
    check(ind_section.result.status == "COMPLETED"
          and ind_section.evaluation.decision == "PASS",
          f"industry 章节无缺口：status={ind_section.result.status!r} / "
          f"decision={ind_section.evaluation.decision!r}")

    scope = (RA.ScopeRequirement("company", co_task.title, (TOPIC_BUSINESS,)),
             RA.ScopeRequirement("industry", ind_task.title, (TOPIC_INDUSTRY_SCALE,)))
    projection = _projection((co_task, ind_task))
    version_inputs = _version_inputs((co_input, ind_input),
                                     topic_pack_ids=(co_pack_id, ind_pack_id))

    def declared(*inputs, **kw):
        """按运行时已知事实**声明**身份输入（组装器随后与权威对账）。"""
        packs = tuple(sorted({
            str(r.authority_container_id) for i in inputs
            for r in i.draft.proposed_support_refs if str(r.authority_kind) == "topic_pack"}))
        return _version_inputs(inputs, topic_pack_ids=packs, **kw)

    def assemble(**overrides):
        kw = dict(projection=projection, scope=scope, version_inputs=version_inputs,
                  section_inputs=(co_input, ind_input))
        kw.update(overrides)
        return RA.assemble_report(**kw)

    report = assemble()
    check(report.company_id == COMPANY_ID and report.report_as_of == REPORT_AS_OF,
          "组装出的报告身份与输入一致")
    check(len(report.sections) == 2, "两个章节都被组装")
    check([s.section_id for s in report.sections] == ["company", "industry"],
          "章节顺序按 DemoScope 顺序（确定性）")
    check(len(report.scope_coverage) == 2, "DemoScope 的两个内容单位都有覆盖记录")
    check(all(e["has_body"] for e in report.scope_coverage), "两个内容单位都有正文")
    check(any(e["has_explicit_gap"] for e in report.scope_coverage),
          "company 的 partial 缺口必须在覆盖记录里显式体现")
    check(len(report.disposition_index) == 4,
          f"四个被选中权威事实都有去向记录，实为 {len(report.disposition_index)}")
    check(len(report.gap_index) == len(co_section.result.unresolved),
          f"缺口索引覆盖 company 的全部缺口（{len(co_section.result.unresolved)} 条），实为 "
          f"{len(report.gap_index)}")
    check(set(report.authority_container_ids) == {co_pack_id, ind_pack_id},
          f"权威容器清单必须完整，实为 {report.authority_container_ids}")
    check(not report.conflict_index, "无跨章节类型化冲突")
    # 每条 proposal（support edge）与其定稿 Claim 都在报告里可回查
    for item in (co_input, ind_input):
        for prop in item.draft.proposed_support_refs:
            check(prop.proposed_support_id in report.support_ref_ids,
                  f"support edge {prop.proposed_support_id} 必须进入报告索引")
        for claim in item.claims:
            check(claim.claim_id in report.claim_ids,
                  f"Claim {claim.claim_id} 必须进入报告索引")
    check(COMPANY_ID in report.markdown and REPORT_AS_OF in report.markdown,
          "Markdown 必须写明主体与报告基准日")
    co_retention = report.retention[f"company:{co_task.task_id}"]
    co_disp_index = [d for d in report.disposition_index if d["section_id"] == "company"]
    check(co_retention["dispositions_claimed"] == 2,
          f"保留链统计：company 有两条 claimed 事实，实为 "
          f"{co_retention['dispositions_claimed']}"
          f"（全部去向 {[(d['fact_id'], d['disposition']) for d in co_disp_index]}）")
    check(co_retention["dispositions_not_presented"] == 1,
          f"保留链统计：company 有一条未呈现（对应 partial aspect），实为 "
          f"{co_retention['dispositions_not_presented']}"
          f"（全部去向 {[(d['fact_id'], d['disposition']) for d in co_disp_index]}）")
    check(co_retention["factual_accepted_bindings"] == 2
          and co_retention["context_accepted_bindings"] == 1,
          f"保留链统计必须区分 factual / context 支撑边，实为 "
          f"{co_retention['factual_accepted_bindings']}/"
          f"{co_retention['context_accepted_bindings']}")

    # 确定性重建：同内容同 report_version；正文变化必变
    rebuilt = RA.rebuild_report(report)
    check(rebuilt.report_version == report.report_version,
          "同内容重建必须得到同一 report_version")
    check(RA.rebuild_report(report, override_markdown=report.markdown + "\n（改）"
                            ).report_version != report.report_version,
          "正文变化必须改变 report_version")
    check(RA.rebuild_report(report, generated_at="2099-01-01T00:00:00Z").report_version
          == report.report_version,
          "生成时间不参与 report_version（否则无法确定性重建）")
    check("release" not in report.markdown.lower() and "放行" not in report.markdown,
          "报告正文不得包含任何放行/发布结论")

    # ------------------------------------------------------------------
    # 2. 权威根：scope / 投影 / 声明身份三者必须守恒（§七 1、§四 1）
    # ------------------------------------------------------------------
    # 多一个 DemoScope 之外的 section：现在在**权威根**这一层就被拒（不再依赖调用方自报）。
    expect_error(
        lambda: assemble(scope=scope + (RA.ScopeRequirement("financial", "财务概况",
                                                            (TOPIC_FIN_SOLVENCY,)),)),
        RA.ReportAssemblerError,
        "DemoScope 里的章节没有正文也没有缺口必须被拒（且不得凭空多出内容单位）",
        needle="不守恒")
    # 少一个 section
    expect_error(
        lambda: assemble(scope=(scope[0],)),
        RA.ReportAssemblerError, "DemoScope 少一个 section 必须被拒",
        needle="不守恒")
    # 同一个 section 多一个 topic（内容单位不得由调用方改写）
    expect_error(
        lambda: assemble(scope=(RA.ScopeRequirement("company", co_task.title,
                                                    (TOPIC_BUSINESS, "未覆盖主题")),
                                scope[1])),
        RA.ReportAssemblerError, "DemoScope 里的 topic 集合与投影不一致必须被拒",
        needle="topic 集合")
    # 标题被改写
    expect_error(
        lambda: assemble(scope=(RA.ScopeRequirement("company", "换个标题", (TOPIC_BUSINESS,)),
                                scope[1])),
        RA.ReportAssemblerError, "scope 标题与冻结投影不一致必须被拒", needle="标题")
    expect_error(
        lambda: assemble(section_inputs=(co_input,)),
        RA.ReportAssemblerError, "缺少 DemoScope 章节时不得组装", needle="既没有正文")
    expect_error(
        lambda: assemble(section_inputs=(co_input, ind_input, co_input)),
        RA.ReportAssemblerError, "重复的章节输入必须被拒", needle="重复的章节输入")
    expect_error(
        lambda: assemble(section_inputs=()),
        RA.ReportAssemblerError, "没有任何章节输入必须被拒", needle="没有任何章节输入")

    # 同形对象不得冒充权威投影
    class _LookalikeProjection:
        pass

    expect_error(
        lambda: assemble(projection=_LookalikeProjection()),
        RA.ReportAssemblerError, "同形对象不得冒充冻结投影",
        needle="必须是 planning.demo_scope_schema.DemoPlanningProjection")

    # 声明身份与权威不一致：每一条都必须指名报出（§六）
    for overrides, what, needle in (
            ({"scope_input_fingerprint": "0" * 64}, "DemoScope 输入面指纹",
             "ReportVersionInputs.scope_input_fingerprint"),
            ({"plan_id": "dplan_other"}, "plan 身份", "ReportVersionInputs.plan_id"),
            ({"selected_task_ids": ("dtask_other",)}, "被选中任务集合",
             "ReportVersionInputs.selected_task_ids"),
            ({"topic_pack_ids": ("pack-other",)}, "Pack 集合",
             "ReportVersionInputs.topic_pack_ids"),
            ({"model_policy_id": "别的策略"}, "model policy",
             "ReportVersionInputs.model_policy_id"),
            ({"prompt_version": "别的 prompt"}, "prompt 版本",
             "ReportVersionInputs.prompt_version")):
        expect_error(
            lambda o=overrides: assemble(version_inputs=dataclasses.replace(
                version_inputs, **o)),
            RA.ReportAssemblerError, f"声明的{what}与权威不一致必须被拒", needle=needle)

    # ------------------------------------------------------------------
    # 3. 身份绑定：报告身份只能来自冻结投影（§六）
    # ------------------------------------------------------------------
    # 章节自带的公司 / 基准日 / Contract 与投影不一致时不得进入报告。
    mismatch_projection = _projection((co_task, ind_task), company_id="别家公司")
    expect_error(
        lambda: assemble(projection=mismatch_projection),
        RA.ReportAssemblerError, "章节公司不属于该投影必须被拒", needle="与冻结投影不一致")
    asof_projection = _projection((co_task, ind_task), report_as_of="2025-12-31")
    expect_error(
        lambda: assemble(projection=asof_projection),
        RA.ReportAssemblerError, "章节基准日不属于该投影必须被拒", needle="与冻结投影不一致")
    # task 的内容单位被改写（标题 / topic 集合）——即使 id 自洽也必须拒
    tampered_task = dataclasses.replace(co_task, title="被改写的标题")
    expect_error(
        lambda: assemble(section_inputs=(
            dataclasses.replace(co_input, task=tampered_task), ind_input)),
        RA.ReportAssemblerError, "SectionTask 标题被改写必须被拒",
        needle="title 与冻结投影不一致")
    other_plan_task = dataclasses.replace(co_task, plan_id="dplan_other")
    expect_error(
        lambda: assemble(section_inputs=(
            dataclasses.replace(co_input, task=other_plan_task), ind_input)),
        RA.ReportAssemblerError, "SectionTask 不属于该 plan 必须被拒", needle="plan_id")

    # 门后定稿的对象一律不得持有未来身份（单向 DAG：Draft 不含 Result/Claim/决定/评估 id）
    check(all(not hasattr(co_section.draft, name)
              for name in RA.DRAFT_FORBIDDEN_IDENTITY_FIELDS),
          "门前 Draft 不得持有门后对象身份（SectionResult/Claim/binding/决定/评估/Narrative）")
    # 反向注入（把 `sres_forged` 之类未来身份塞进 unresolved_projections）在本链上**不可构造**：
    # `SectionDraft` 的唯一公开构造入口 `SectionDraft.create` 会先把成员规范化成
    # `UnresolvedProjection` 对象，未知键在 `from_dict`/`create` 阶段即被拒，
    # 拿不到"字段集被扩张但对象仍合法"的 Draft。故该防御分支不可达，
    # 如实记为 SKIP，不伪造覆盖（上面的正向断言才是本链上可验证的那一半）。
    skip("§十五 1/§七 3：组装器的「Draft 持有尚未形成的对象身份」分支在本链上不可构造"
         "（`SectionDraft.create` 的成员规范化 + 未知键拒绝先于组装器生效），"
         "已改记正向断言：门前 Draft 不持有任何门后对象身份")

    other_eval = ind_section.evaluation
    expect_error(
        lambda: assemble(section_inputs=(
            dataclasses.replace(co_input, evaluation=other_eval), ind_input)),
        RA.ReportAssemblerError, "Evaluation 错配到别的章节必须被拒",
        needle="与实体不一致")

    def rebind(**overrides):
        """构造一条**自身自洽**（id 与内容一致）但指向错实体的 binding。"""
        fields = {"section_result_id": co_section.binding.section_result_id,
                  "section_draft_id": co_section.binding.section_draft_id,
                  "evaluation_id": co_section.binding.evaluation_id,
                  "narrative_gate_result_id": co_section.binding.narrative_gate_result_id,
                  "rules_version": co_section.binding.rules_version,
                  "gate_version": co_section.binding.gate_version}
        fields.update(overrides)
        return NS.NarrativeEvaluationBinding.create(**fields)

    for overrides, what, needle in (
            ({"section_draft_id": "sdraft_other"}, "draft 身份", "section_draft_id"),
            ({"section_result_id": "sres_other"}, "SectionResult 身份", "section_result_id"),
            # 这两个「错版」故意取**永远不会成为当前版本**的字面量：版本只前进，`-99` 不在
            # 任何一版的口径里。写一个"比当前早一版"的值会让夹具随版本前进而**悄悄失效**
            # （它那时候真的等于当前版本，突变就不再是错配——本行曾用 `nrules-9` 踩过）。
            ({"rules_version": "nrules-99"}, "rules_version", "rules_version"),
            ({"gate_version": "ngate-99"}, "gate_version", "gate_version"),
            ({"narrative_gate_result_id": "ngr_other"}, "门结果身份",
             "narrative_gate_result_id"),
            ({"evaluation_id": "eval_other"}, "evaluation 身份", "evaluation_id")):
        expect_error(
            lambda o=overrides: assemble(section_inputs=(
                dataclasses.replace(co_input, binding=rebind(**o)), ind_input)),
            RA.ReportAssemblerError, f"binding 的{what}错配必须被拒", needle=needle)
    expect_error(
        lambda: assemble(section_inputs=(dataclasses.replace(co_input, binding=None),
                                         ind_input)),
        RA.ReportAssemblerError, "缺少 binding 必须被拒", needle="NarrativeEvaluationBinding")

    # ------------------------------------------------------------------
    # `retamper`：制造**整条上游自证链都自洽**的篡改输入
    # （draft + final Narrative + Result + 门 + 评估 + binding 一起重绑）
    #
    # 关键在「自洽」：draft 按内容重算 `draft_id`，final Narrative 由**唯一**构造器在被篡改的
    # draft + 定稿 Claim 集上重算，正文按唯一渲染器重算、`markdown_fingerprint` /
    # `section_version` / `section_result_id` 全部重新派生，门伪造成 clean，binding 重新指向
    # 新实体。所有类型层与版本层校验因此都能通过，唯一剩下的防线就是组装器自己的独立复核。
    # ------------------------------------------------------------------
    def retamper(base, *, draft=None, claims=None, unresolved=None, narrative=None,
                 gate=None, evaluation=None, dispositions=None, aggregate_decisions=None,
                 entailment_decisions=None, acceptance=None, binding=None,
                 **draft_changes):
        bad = draft
        if bad is None and draft_changes:
            bad = NS.SectionDraft.create(**{
                **{k: v for k, v in base.draft.to_dict().items()
                   if k not in ("draft_id", "schema_version")},
                **draft_changes})
        if bad is None:
            bad = base.draft
        kept_claims = tuple(base.claims if claims is None else claims)
        kept_unresolved = tuple(base.result.unresolved if unresolved is None else unresolved)
        acceptance_out = base.acceptance if acceptance is None else acceptance
        if narrative is None:
            narrative_out = NS.build_section_narrative(
                draft=bad, claims=kept_claims,
                context_binding_ids=tuple(base.narrative.context_binding_ids))
        else:
            narrative_out = narrative
        # §12.4.4 第 4 步（B 门）：本助手可能重建正文 / 换 Claim 集 / 换支撑边，所以先按**当前**
        # 四样输入（正文 / 决定 / 定稿 Claim / 已接受支撑边）重算这条门的状态，而不是凭「原来
        # 有没有决定」猜。重算仍判 `valid`（正文身份、Claim 集与授权面逐位未变）时才沿用原决定；
        # 其余各档一律如实落成「本节最终句未经语义核验」的 typed block，并把决定清空。
        #
        # 这里**不**去重造一条「有效」决定：那需要权威读视图 + `wmctx-1` 正文上下文，而本助手的
        # 两样输入里都没有；凭不齐的输入伪造一份「已核验」正是这道门要挡的事。决定清空（缺省
        # 空元组）是旧产物 / 未核验状态的合法形态。
        #
        # 这一条不改任何既有反例的判定意图：它们要验的是 draft / Claim / 绑定 / 缺口的那一处
        # 篡改，block 只是让被篡改后的产物**自洽**地表述自己的未核验状态——组装器的缺口守恒
        # 判据是**双向**的，重算说要阻断而产物里没有那条 block，同样被拒。
        decisions_out = tuple(base.final_sentence_decisions)
        gate_state, _ = NS.final_sentence_gate_state(
            narrative=narrative_out, decisions=decisions_out, claims=kept_claims,
            accepted_bindings=tuple(acceptance_out.accepted_bindings))
        sentence_block = None
        if gate_state is not None:
            sentence_block = FSF.final_sentence_block_unresolved(
                section_id=str(narrative_out.section_id), narrative=narrative_out,
                decisions=(), claims=kept_claims,
                accepted_bindings=tuple(acceptance_out.accepted_bindings))
            decisions_out = ()
            kept_unresolved = kept_unresolved + (sentence_block,)
        rendered = NS.render_final_narrative_markdown(
            bad.title, narrative_out, tuple(bad.unresolved_projections),
            # `pwr-5`：来源索引与生产链同一个入口重建（这里用被保留的 Claim 集）。
            citation_sources=NS.citation_source_index(tuple(kept_claims)))
        fingerprint = NS.body_fingerprint_of(rendered)
        version = SS.derive_section_version(
            base.result.task_id, tuple(c.claim_id for c in kept_claims), kept_unresolved,
            section_draft_id=bad.draft_id,
            renderer_version=bad.writer_renderer_version,
            rules_version=bad.writer_rules_version,
            dependency_fingerprint=bad.dependency_fingerprint,
            markdown_fingerprint=fingerprint)
        result_id = SS.derive_section_result_id(version)
        result_out = dataclasses.replace(
            base.result, claims=kept_claims, unresolved=kept_unresolved, markdown=rendered,
            markdown_fingerprint=fingerprint, section_version=version,
            section_result_id=result_id, section_draft_id=bad.draft_id,
            # 状态一律按 canonical 派生重算（`PW._derive_status` → `RC.derive_status`）：先前
            # 这里写的是「有缺口就 COMPLETED_WITH_GAPS」的空集三元式，它会把带
            # `blocking_effects=("SECTION_BLOCKED",)` 的缺口（如 B 门 block、WAITING_HUMAN）
            # 降级成「仅存缺口」——产物于是自述 status 与自己的缺口互相矛盾，反例要验的那一处
            # 篡改还没被组装器看到就先被这条自造的状态骗过。状态派生只有一份口径，夹具也一样。
            status=PW._derive_status(kept_unresolved))
        if evaluation is not None:
            eval_out = evaluation
        else:
            eval_out = dataclasses.replace(
                base.evaluation, section_result_id=result_id,
                evaluation_id=NS.content_id("eval_", {"section_result_id": result_id}))
        if gate is not None:
            gate_out = gate
        elif bad is base.draft:
            gate_out = base.gate_result
        else:
            gate_out = NS.NarrativeGateResult.build(section_draft_id=bad.draft_id, issues=())
        if binding is not None:
            bind_out = binding
        else:
            bind_out = NS.NarrativeEvaluationBinding.create(
                section_result_id=result_id, section_draft_id=bad.draft_id,
                evaluation_id=eval_out.evaluation_id,
                narrative_gate_result_id=gate_out.gate_result_id,
                rules_version=NS.NARRATIVE_RULES_VERSION,
                gate_version=NS.NARRATIVE_GATE_VERSION)
        return dataclasses.replace(
            base, draft=bad, gate_result=gate_out,
            aggregate_decisions=tuple(base.aggregate_decisions
                                      if aggregate_decisions is None else aggregate_decisions),
            entailment_decisions=tuple(base.entailment_decisions
                                       if entailment_decisions is None else entailment_decisions),
            acceptance=acceptance_out,
            claims=kept_claims, narrative=narrative_out,
            dispositions=tuple(base.dispositions if dispositions is None else dispositions),
            result=result_out, evaluation=eval_out, binding=bind_out,
            # 重算仍判 `valid` 时沿用原决定；否则清零（见上方说明）。
            final_sentence_decisions=decisions_out)

    def _unwrap(bound):
        """取出以 `key=` 起头的 6 个 binding 字段（用于伪造同形绑定的反例）。"""
        return {k: getattr(bound, k) for k in
                ("section_result_id", "section_draft_id", "evaluation_id",
                 "narrative_gate_result_id", "rules_version", "gate_version")}

    # 硬门仍为 blocking 的章节不得进入组装（binding 与门**配套**地指向同一个真门结果）
    blocking_gate = NS.NarrativeGateResult.build(
        section_draft_id=co_section.draft.draft_id,
        issues=(NS.NarrativeGateIssue(rule_id="narrative_vague_period", severity="blocking",
                                      location="u-1", detail="草稿单元含含糊期间"),))
    expect_error(
        lambda: assemble(section_inputs=(
            dataclasses.replace(
                co_input, gate_result=blocking_gate,
                binding=rebind(narrative_gate_result_id=blocking_gate.gate_result_id)),
            ind_input)),
        RA.ReportAssemblerError, "narrative 硬门 blocking 的章节不得进入组装",
        needle="仍为 blocking")

    # ------------------------------------------------------------------
    # 3.5 门后裁定：门后可裁定集合里的 rework 不是免检（§0.13/§三 A）
    # ------------------------------------------------------------------
    # 该集合的成员只有一条「门在候选束上看不到门后对象」的**半条规则**：门只能把它标成
    # rework，裁定者换成组装器。下面的反例证明「换裁定者」不是「放行」。
    _DeferredIssue = NS.NarrativeGateIssue

    def _deferred_gate(location: str) -> NS.NarrativeGateResult:
        return NS.NarrativeGateResult.build(
            section_draft_id=co_input.draft.draft_id,
            issues=(_DeferredIssue(rule_id="required_fact_not_proposed", severity="rework",
                                   location=location, detail="门后裁定反例"),))

    def _assemble_with(gate: NS.NarrativeGateResult):
        return assemble(section_inputs=(
            dataclasses.replace(co_input, gate_result=gate,
                                binding=rebind(narrative_gate_result_id=gate.gate_result_id)),
            ind_input))

    claimed_disp = next(d for d in co_input.dispositions if str(d.disposition) == "claimed")
    unpresented_disp = next(d for d in co_input.dispositions
                            if str(d.disposition) == "not_presented_with_reason")
    check(not bool(unpresented_disp.required) and unpresented_disp.unresolved_id is None,
          "前提：本 fixture 的未呈现事实**不是** Contract 必需事实（它不挂缺口），"
          "因此它正好是「既未 claim 也无缺口」的反例素材")
    claimed_loc = (f"{claimed_disp.container_identity}:"
                   f"{claimed_disp.authority_specific_fact_id}")
    unpresented_loc = (f"{unpresented_disp.container_identity}:"
                       f"{unpresented_disp.authority_specific_fact_id}")

    class _AdjudicationItem:
        """白盒调用 `RA._adjudicate_post_gate_issues` 所需的三个真实字段（其余它不读）。"""

        def __init__(self, *, dispositions, result, draft) -> None:
            self.dispositions = tuple(dispositions)
            self.result = result
            self.draft = draft

    def _refnd(disp, **changes):
        """按内容重建一条 FND（`disposition_id` 是内容寻址派生的，不能 `replace` 留着旧 id）。"""
        body = {k: v for k, v in disp.to_dict().items() if k != "disposition_id"}
        body.update(changes)
        return NS.FactNarrativeDisposition.create(**body)

    def _adjudicate(*, dispositions, location=claimed_loc, result=None):
        RA._adjudicate_post_gate_issues(
            _AdjudicationItem(dispositions=dispositions,
                              result=(co_input.result if result is None else result),
                              draft=co_input.draft), _deferred_gate(location))

    # 通过：门看到的是「没有提案」；门后看到的是**已被 claim 的去向**（claimed 分支）
    _adjudicate(dispositions=co_input.dispositions)
    # 拒：location 不是 `<container_id>:<fact_id>`
    expect_error(
        lambda: _adjudicate(dispositions=co_input.dispositions,
                            location=str(claimed_disp.authority_specific_fact_id)),
        RA.ReportAssemblerError, "门后裁定规则的 location 形态非法必须被拒",
        needle="不是 '<container_id>:<fact_id>' 形式")
    # 拒：location 指向本节不存在的事实去向（跨 authority 裸 id 撞车也走这条）
    expect_error(
        lambda: _adjudicate(dispositions=co_input.dispositions,
                            location=f"{claimed_disp.container_identity}:fact-not-in-section"),
        RA.ReportAssemblerError, "门后定位不到事实去向必须被拒", needle="没有**恰好一条**")
    # 拒：既未 claim，也没有可显示在 Result 里的缺口（必需性被抹掉）→ 默认放行不成立
    expect_error(
        lambda: _adjudicate(dispositions=(unpresented_disp,), location=unpresented_loc),
        RA.ReportAssemblerError, "既未 claim 也无显式缺口的必需事实不得被默认放行",
        needle="既未 claim")
    gaps = tuple(str(u.unresolved_id) for u in co_input.result.unresolved)
    if not gaps:
        skip("§0.13/§三 A：本 fixture 的 Result 里没有显式缺口，"
             "「必需事实未 claim 但已留下显式缺口」这条通过路径在本文件无真实素材可依")
    else:
        # 通过：未 claim，但**它的**缺口确实在 Result 里（必需的债必须留下痕迹）
        _adjudicate(dispositions=(_refnd(unpresented_disp, required=True,
                                         unresolved_id=gaps[0]),),
                    location=unpresented_loc)
        # 拒：挂着**别人**的缺口 id（缺口对不上号也是默认放行）
        expect_error(
            lambda: _adjudicate(dispositions=(_refnd(unpresented_disp, required=True,
                                                     unresolved_id="unres-not-in-result"),),
                                location=unpresented_loc),
            RA.ReportAssemblerError, "必需事实挂的缺口不在本 Result 里必须被拒",
            needle="既未 claim")
    # 端到端接线：组装入口自己就会跑这条裁定（不是只在单测里成立）。人工门指向不存在的事实
    # 去向 → 必须拒在**门后裁定**这一条上，而不是别的原因。
    expect_error(lambda: _assemble_with(_deferred_gate(f"{claimed_disp.container_identity}"
                                                       ":fact-not-in-section")),
                 RA.ReportAssemblerError, "组装入口必须真的执行门后裁定",
                 needle="没有**恰好一条**")
    # 端到端：同样的人工门 + 真实门后对象（claimed）→ 裁定成立，失败点下移到「门必须是当前
    # 重算结果」这条既有防线（人工门必然不匹配），证明裁定本身不会误拒合法输入。
    expect_error(lambda: _assemble_with(_deferred_gate(claimed_loc)),
                 RA.ReportAssemblerError, "人工门即便裁定成立也必须被「当前重算」这道防线拒",
                 needle="与当前重算结果不一致")
    # 不在门后可裁定集合里的 rework 一律照旧拒绝（覆盖面不缩，只换裁定者）
    other_rework_gate = NS.NarrativeGateResult.build(
        section_draft_id=co_input.draft.draft_id,
        issues=(_DeferredIssue(rule_id="narrative_vague_period", severity="rework",
                               location="u-1", detail="不在门后裁定集合里"),))
    expect_error(lambda: _assemble_with(other_rework_gate),
                 RA.ReportAssemblerError, "非门后可裁定集合的 rework 仍必须拒绝组装",
                 needle="rework 级问题")

    # ------------------------------------------------------------------
    # 3.6 §四 1 身份分层：有界补件换的是**内容/容器**，不是**策略依赖**
    # ------------------------------------------------------------------
    # 三套身份各归各位：
    #   * `dependency_fingerprint` 是**冻结的策略**依赖（Contract / SourcePolicy /
    #     WritingSpec / PresentationProfile 四类资产指纹），同一报告各节逐字节相同；补件运行
    #     （run id / trace ref）属 operational run identity，不得改写它；
    #   * 后继 Pack 的身份进 Draft 的 `authority_container_ids`（内容身份）与 exact manifest，
    #     Pack 集进 `ReportVersionInputs.topic_pack_ids`（版本身份）：**换 Pack / 换材料就必须变**；
    #   * 错 Pack / 旧 Pack / 错投影一律 fail-closed。
    # 下面用**真实**后继权威证明这两面（内容确实换了：补进来的事实把 Pack 内容指纹一起换掉）。
    expect_error(
        lambda: assemble(section_inputs=(
            retamper(co_input, dependency_fingerprint="0" * 64), ind_input)),
        RA.ReportAssemblerError,
        "某节 Draft 的 dependency_fingerprint 被换成别族值（整条自证链自洽）必须在「依赖同族"
        "逐字节一致」这条上被拒",
        needle="dependency_fingerprint 与冻结投影不一致")
    succ_fact = _fact("f-followup", "公司为境外生产基地配套了本地化供应安排。",
                      (ASP_BUSINESS_PROCUREMENT,))
    succ_authority = _authority(co_task, facts=co_facts + (succ_fact,),
                                aspects=co_aspects, requirements=co_requirements)
    succ_pack_id = _pack_id_of(succ_authority, TOPIC_BUSINESS)
    check(succ_pack_id != co_pack_id,
          "前提：补件带进一条新事实 ⇒ 后继 Pack 的内容身份必须不同（"
          f"{co_pack_id} → {succ_pack_id}）")
    succ_scan = PW.scan_topic_pack(succ_authority, co_task)
    succ_entry = _entries(succ_scan)
    succ_material_id = sorted(_material_ids_of(succ_scan)[succ_pack_id])[0]
    succ_plan = _plan(
        candidates=[_cand("c-main", succ_entry["f-main"].text,
                          _fact_edge(succ_scan, "f-main")),
                    _cand("c-sales", succ_entry["f-sales"].text,
                          _fact_edge(succ_scan, "f-sales"))],
        units=[_unit("u-1", "本节就公司主营业务与销售模式作背景说明。",
                     context=[_context_edge(succ_pack_id, succ_material_id)])])
    succ_phase = CW.run_backbone_writer_phase(
        (CW.BackboneWriterSectionInput(
            task=co_task, authority=succ_authority, projection=projections["company"],
            writing_spec=spec, presentation_profile=profile,
            dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
        llm_client=_StubLlm(succ_plan),
        entailment_llm_client=_StubEntailmentClient(_ENTAILED),
        final_sentence_llm_client=_stub_final_sentence_client(),
        material_resolver=_payload_resolver())
    succ_section = succ_phase.sections[0]
    succ_input = _assembly_input(succ_section, co_task, succ_authority)
    succ_containers = {str(c) for c in succ_section.draft.authority_container_ids}
    check(succ_containers == {succ_pack_id} and co_pack_id not in succ_containers,
          "后继 Pack 的身份进的是 Draft 的 authority_container_ids（内容身份）：实为 "
          f"{sorted(succ_containers)}")
    check(succ_section.draft.draft_revision != co_section.draft.draft_revision
          and succ_section.result.section_version != co_section.result.section_version,
          "内容换了 ⇒ Draft 内容身份与章节版本必须随之变化（内容身份不看操作运行）")
    check(succ_section.draft.dependency_fingerprint
          == co_section.draft.dependency_fingerprint == DEPENDENCY_FINGERPRINT,
          "同一报告各节的策略依赖指纹逐字节一致，且不随内容/补件漂移")
    succ_report = RA.assemble_report(
        projection=projection, scope=scope,
        version_inputs=_version_inputs((succ_input, ind_input),
                                       topic_pack_ids=(succ_pack_id, ind_pack_id)),
        section_inputs=(succ_input, ind_input))
    check(succ_report.report_version != report.report_version
          and set(succ_report.authority_container_ids) == {succ_pack_id, ind_pack_id},
          "换后继 Pack ⇒ report_version 必须随之变化，且权威容器清单取**后继**身份："
          f"containers={sorted(succ_report.authority_container_ids)}")
    expect_error(
        lambda: RA.assemble_report(
            projection=projection, scope=scope, version_inputs=version_inputs,
            section_inputs=(succ_input, ind_input)),
        RA.ReportAssemblerError,
        "拿**旧 Pack** 的声明去组装后继章节必须 fail-closed（声明与权威逐字段对账）",
        needle="ReportVersionInputs.topic_pack_ids")
    expect_error(
        lambda: assemble(section_inputs=(
            dataclasses.replace(co_input, authority=ind_authority), ind_input)),
        RA.ReportAssemblerError,
        "把别的 topic 的 Pack 当成本节权威（错 Pack）必须被拒")

    # ------------------------------------------------------------------
    # 3.7 §六 **逐节**权威容器精确性：本节声明必须恰好等于本节权威派生的容器集合
    # ------------------------------------------------------------------
    # 写入侧声明的 `Draft.authority_container_ids` 只是**待核对的声明**：`SectionDraft` 自身
    # 只校验「提案引用的容器 ⊆ 已声明」，所以**多报**的容器在整条上游自证链里完全不可见
    # （`retamper` 会把 draft_id / Result / 门 / 评估 / binding 全部重算到自洽）。组装器若只
    # 看「全报告声明的并集」，另一节的合法 Pack、甚至一个凭空造出的 id，都能冒充本节权威
    # ——而且下面第一组反例里报告容器清单**看上去仍然完整**，污染连输出都看不出来。
    check(tuple(co_input.draft.authority_container_ids) == (co_pack_id,)
          and tuple(ind_input.draft.authority_container_ids) == (ind_pack_id,),
          "前提：两节各自只声明**自己**的 Pack："
          f"company={co_input.draft.authority_container_ids} / "
          f"industry={ind_input.draft.authority_container_ids}")
    check(RA._lawful_containers_for(co_input) == {"topic_pack": (co_pack_id,)}
          and RA._lawful_containers_for(ind_input) == {"topic_pack": (ind_pack_id,)},
          "合法容器集合由**本节自己的权威输入**派生，且与本节声明一致："
          f"company={RA._lawful_containers_for(co_input)} / "
          f"industry={RA._lawful_containers_for(ind_input)}")
    check(RA._authority_container_sets((co_input, ind_input))["topic_pack"]
          == frozenset({co_pack_id, ind_pack_id}),
          "全报告并集只作为 report_version 的输入面：它是两节合法集的按类并集")

    # 本节判据单独成立的三组反例（白盒：直接驱动 `_verify_declared_containers`）。
    #
    # 为什么白盒：**多报**这一侧在整链上还会先撞上另一条判据 —— 门前门
    # `narrative_container_unknown` 要求「声明 ⊆ 权威里**确实持事实**的容器」，而组装器会
    # 独立重算这道门。那条判据与「归属本节」不是同一条：它对**不带事实**的合法 Pack 完全
    # 无感（下面的 (A) 组正是走这条缝）。所以本节的三条判据必须独立成立，不能因为「碰巧被
    # 门前门拦下」就算数。
    lawful_pairs = (RA._lawful_containers_for(co_input),
                    RA._lawful_containers_for(ind_input))
    forged_extra_cross = dataclasses.replace(
        co_input, draft=retamper(co_input,
                                 authority_container_ids=(co_pack_id, ind_pack_id)).draft)
    expect_error(
        lambda: RA._verify_declared_containers(
            (forged_extra_cross, ind_input), lawful_pairs),
        RA.ReportAssemblerError,
        "本节 Draft 多报**另一节的合法 Pack** 必须被拒（不得用别的节的容器冒充本节权威）",
        needle="不得冒充本节权威")
    forged_extra_unknown = dataclasses.replace(
        co_input, draft=retamper(
            co_input, authority_container_ids=(co_pack_id, "pack_not_authoritative")).draft)
    expect_error(
        lambda: RA._verify_declared_containers(
            (forged_extra_unknown, ind_input), lawful_pairs),
        RA.ReportAssemblerError,
        "本节 Draft 多报一个凭空造出的容器必须被拒（报告容器清单只能来自权威输入）",
        needle="与本节权威输入不守恒")
    # 漏报：`SectionDraft.__post_init__` 要求「提案引用的容器 ⊆ 已声明」，所以**整份**漏报
    # 连构造都过不去（下面用旁路 `__post_init__` 的伪造对象证明组装器自己也有一道）。
    forged_missing = copy.copy(co_input.draft)
    object.__setattr__(forged_missing, "authority_container_ids", ())
    forged_item = dataclasses.replace(co_input, draft=forged_missing)
    expect_error(
        lambda: RA._verify_declared_containers((forged_item, ind_input), lawful_pairs),
        RA.ReportAssemblerError,
        "本节 Draft 漏报自己的 Pack 必须被拒（报告容器清单不得残缺）——"
        "上游 `SectionDraft.__post_init__` 已拦一层，组装器这道是独立复核",
        needle="与本节权威输入不守恒")

    # 正例：合法但**最终没有任何 Claim 用到它**的 Pack 必须保留。
    # 本节权威有两个 topic 的 Pack，提案只用到第一个；两个都进容器清单、也进版本身份。
    unused_fact = _fact("f-model", "公司生产以自建产线为主。", (ASP_BUSINESS_MODEL,))
    co2_task = _task("company", (TOPIC_BUSINESS, TOPIC_BUSINESS_MODEL))
    co2_requirements = (
        _Req(TOPIC_BUSINESS, (_AspectReq(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                                         "q-company_business"),)),
        _Req(TOPIC_BUSINESS_MODEL, (_AspectReq(ASP_BUSINESS_MODEL, TOPIC_BUSINESS_MODEL,
                                               "q-company_business_model"),)))
    co2_authority = _authority(co2_task, facts=(), aspects=(),
                               requirements=co2_requirements, packs=(
                                   _Pack(topic_id=TOPIC_BUSINESS, facts=co_facts,
                                         aspect_results=(
                                             _AspectResult(ASP_BUSINESS_MAIN, "covered"),)),
                                   _Pack(topic_id=TOPIC_BUSINESS_MODEL,
                                         facts=(unused_fact,),
                                         aspect_results=(
                                             _AspectResult(ASP_BUSINESS_MODEL, "partial"),))))
    co2_pack_a = _pack_id_of(co2_authority, TOPIC_BUSINESS)
    co2_pack_b = _pack_id_of(co2_authority, TOPIC_BUSINESS_MODEL)
    check(co2_pack_a != co2_pack_b,
          "前提：Pack 身份是内容身份，两个 topic 给出两个不同容器："
          f"{co2_pack_a} / {co2_pack_b}")
    co2_scan = PW.scan_topic_pack(co2_authority, co2_task)
    co2_entry = _entries(co2_scan)
    co2_material = sorted(_material_ids_of(co2_scan)[co2_pack_a])[0]
    co2_out = CW.run_backbone_writer_phase(
        (CW.BackboneWriterSectionInput(
            task=co2_task, authority=co2_authority, projection=projections["company"],
            writing_spec=spec, presentation_profile=profile,
            dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
        llm_client=_StubLlm(_plan(
            candidates=[_cand("c-main", co2_entry["f-main"].text,
                              _fact_edge(co2_scan, "f-main"))],
            units=[_unit("u-1", "本节就公司主营业务作背景说明。",
                         context=[_context_edge(co2_pack_a, co2_material)])])),
        entailment_llm_client=_StubEntailmentClient(_ENTAILED),
        final_sentence_llm_client=_stub_final_sentence_client(),
        material_resolver=_payload_resolver()).sections[0]
    co2_input = _assembly_input(co2_out, co2_task, co2_authority)
    used_containers = {str(b.authority_container_id)
                       for b in co2_out.acceptance.accepted_bindings}
    check(set(co2_input.draft.authority_container_ids) == {co2_pack_a, co2_pack_b}
          and used_containers == {co2_pack_a},
          "前提：本节权威有 2 个 Pack，声明 2 个，而支撑边只用到其中 1 个"
          f"（另一个**没有被任何 Claim 使用**）：declared="
          f"{sorted(co2_input.draft.authority_container_ids)} used={sorted(used_containers)}")
    check(set(RA._lawful_containers_for(co2_input)["topic_pack"]) == {co2_pack_a, co2_pack_b},
          "本节合法容器集合**含**那个未被使用的 Pack（权威给了就是本节输入）："
          f"{RA._lawful_containers_for(co2_input)}")
    co2_scope = (RA.ScopeRequirement("company", co2_task.title,
                                     (TOPIC_BUSINESS, TOPIC_BUSINESS_MODEL)),)
    co2_version = _version_inputs((co2_input,),
                                  topic_pack_ids=tuple(sorted((co2_pack_a, co2_pack_b))))
    co2_report = RA.assemble_report(
        projection=_projection((co2_task,)), scope=co2_scope,
        version_inputs=co2_version, section_inputs=(co2_input,))
    check(set(co2_report.authority_container_ids) == {co2_pack_a, co2_pack_b},
          "未被任何 Claim 使用的合法 Pack 仍须保留在报告容器清单里："
          f"{sorted(co2_report.authority_container_ids)}")

    # **整链可达的反例**（本批真正的增量）：声明**漏掉**那个没被任何 Claim 用到的合法
    # Pack。它落在「门前门」的判据之外（声明仍是权威里持事实容器的子集），所以旧逻辑下
    # 组装照旧通过 —— 而报告里 `authority_container_ids` 会是残缺的一个，`version_identity.
    # topic_pack_ids` 却是两个：同一份报告自己的两个字段互相矛盾，且没有任何一层报错。
    forged_drop = copy.copy(co2_input.draft)
    object.__setattr__(forged_drop, "authority_container_ids", (co2_pack_a,))
    dropped = retamper(co2_input, draft=forged_drop)
    check(tuple(dropped.draft.authority_container_ids) == (co2_pack_a,)
          and set(RA._authority_container_sets((dropped,))["topic_pack"])
          == {co2_pack_a, co2_pack_b},
          "前提：伪造声明只写一个 Pack，而版本身份（由权威派生）仍含两个 → "
          "旧逻辑下同一份报告的容器清单与版本输入面会互相矛盾")
    expect_error(
        lambda: RA.assemble_report(
            projection=_projection((co2_task,)), scope=co2_scope,
            version_inputs=co2_version, section_inputs=(dropped,)),
        RA.ReportAssemblerError,
        "漏报一个未被任何 Claim 使用的合法 Pack 必须被拒"
        "（报告容器清单与版本身份不得互相矛盾）",
        needle="与本节权威输入不守恒")
    # 「修前」对照：只把本批新增的那**一道**判据换成 no-op（逐节对账缺席），同一输入就能
    # 走到产出报告，而且产出报告的两个字段互相矛盾。这道对照证明「被拒」确实由本批新增的
    # 判据造成 —— 不是别的哪一层早就拒了（否则「这批修好了什么」无从验证）。
    saved_declared_check = RA._verify_declared_containers
    RA._verify_declared_containers = lambda *a, **k: None
    try:
        legacy_report = RA.assemble_report(
            projection=_projection((co2_task,)), scope=co2_scope,
            version_inputs=co2_version, section_inputs=(dropped,))
    finally:
        RA._verify_declared_containers = saved_declared_check
    check(set(legacy_report.authority_container_ids) == {co2_pack_a}
          and set(legacy_report.version_identity.topic_pack_ids)
          == {co2_pack_a, co2_pack_b},
          "对照（修前口径）：去掉本批这道判据后同一输入**照旧组装成功**，且报告里 "
          "authority_container_ids 与 version_identity.topic_pack_ids 互相矛盾："
          f"{sorted(legacy_report.authority_container_ids)} vs "
          f"{sorted(legacy_report.version_identity.topic_pack_ids)}")

    # ------------------------------------------------------------------
    # 4. Claim 血缘：伪 citation / 事后补引用 / 删 Claim / 决定链缺失
    # ------------------------------------------------------------------
    check(len(co_section.claims) == 2,
          f"前提：canonical 定稿恰好 2 条 Claim，实为 {len(co_section.claims)}")
    # 删掉一条被 accepted 的 Claim：canonical 定稿重算立刻发现「条数不符」。
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, claims=co_section.claims[:1]),
                                         ind_input)),
        RA.ReportAssemblerError, "删掉一条被接受的事实 Claim 必须被检出",
        needle="与 canonical 定稿")
    def _reclaim(claim, **changes):
        """改写一条 Claim 的字段并**重新派生** `claim_id`。

        `SectionClaim` 自身就是内容寻址的，所以「把 `text` 改掉却留旧 id」在类型层就被拒
        （那是另一条更浅的防线）。本助手刻意构造**自身自洽**的篡改对象，逼组装器只能靠
        「与 canonical 定稿重算结果逐项比对」发现它。
        """
        merged = {f.name: getattr(claim, f.name) for f in dataclasses.fields(claim)}
        merged.update(changes)
        merged["claim_id"] = SS.derive_claim_id(
            merged["claim_type"], merged["topic_id"], merged["question_ids"], merged["text"],
            merged["citation_refs"], merged["claim_candidate_id"],
            merged["claim_candidate_revision"], merged["accepted_binding_ids"])
        return SS.SectionClaim(**merged)

    # 改写一条 Claim 的命题（定稿不许改写命题）——自身自洽，只有 canonical 重算能发现
    rewritten = _reclaim(co_section.claims[0], text="公司已全面停产。")
    check(rewritten.claim_id != co_section.claims[0].claim_id,
          "前提：改写命题必然换出新 claim_id（对象自身自洽，不是因为 id 失配被拒）")
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, claims=(rewritten,)
                                                  + tuple(co_section.claims[1:])),
                                         ind_input)),
        RA.ReportAssemblerError, "改写 Claim 命题必须被检出",
        needle="与 canonical 定稿")
    # 事后补引用：把 Claim 的 citation 换成**别处**的来源（自身自洽，但引用不是它自己
    # factual binding 指向的权威引用）
    real_ref = co_section.claims[0].citation_refs[0]
    forged_ref = TS.CitationRef(ref_type="evidence", evidence_id="ev-other", page_number=1)
    check(SS.citation_identity(forged_ref) != SS.citation_identity(real_ref),
          "前提：伪造的引用锚点与真引用身份不同（反例不是恒失败）")
    forged_claim = _reclaim(co_section.claims[0], citation_refs=(forged_ref,))
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, claims=(forged_claim,)
                                                  + tuple(co_section.claims[1:])),
                                         ind_input)),
        RA.ReportAssemblerError, "Claim 引用被改写必须被检出", needle="与 canonical 定稿")

    # 决定链：aggregate 决定被换成「别的账」→ 组装器自己重算决定链并发现不一致
    # 判据是**按 subject 键**的集合相等 + 每个 subject 恰一条 + id 必须等于重算结果，因此
    # 「整个决定集被抹掉」表现为 subject 集不等（缺哪些账会被指名），而不是元组长度不等。
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, aggregate_decisions=()),
                                         ind_input)),
        RA.ReportAssemblerError, "aggregate 决定被抹掉（subject 集缺账）必须被拒",
        needle="aggregate 决定 subject 集")
    # 同一 subject 供两条决定：一个 subject revision 恰有一条 aggregate 决定，重复即拒
    expect_error(
        lambda: assemble(section_inputs=(retamper(
            co_input,
            aggregate_decisions=tuple(co_section.aggregate_decisions)
            + (co_section.aggregate_decisions[0],)), ind_input)),
        RA.ReportAssemblerError, "同一 subject 供两条 aggregate 决定必须被拒",
        needle="含重复 subject")
    # 顺序不是链的内容：把决定元组**逆序**交给组装器，必须与正序同样通过（重算逐条按 subject
    # 键比对，不比元组位置；store 提交时按 decision_id 排序，读回顺序与写入侧内存序本就不同）
    #
    # 这里只换决定元组的顺序，**其余输入逐字段不动**（`dataclasses.replace`）。不能用
    # `retamper`：它还顺带用确定性构造器把 final Narrative 重算一遍，而本节正文是门后自然组织
    # 的产物（含 composed 句）——「重算的那份正文」与「组织出来的那份正文」本来就不是同一份
    # 文本，拿它去比报告身份，比的是两件事（先前只是碰巧同字，§七 1 之后不再同字）。
    reversed_order = assemble(section_inputs=(
        dataclasses.replace(
            co_input,
            aggregate_decisions=tuple(reversed(co_section.aggregate_decisions))),
        ind_input))
    check(reversed_order.report_id == report.report_id,
          "逆序的 aggregate 决定元组必须被接受，且产出与正序**同一**报告身份"
          f"（{reversed_order.report_id!r} != {report.report_id!r}）")
    # 语义决定被抹掉：factual binding 缺 entailment 决定 → 组装器重算决定链时先发现束不一致
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, entailment_decisions=()),
                                         ind_input)),
        RA.ReportAssemblerError, "缺 entailment 决定不得进入组装",
        needle="accepted binding 束无法重算")

    # ------------------------------------------------------------------
    # 5. 事实与去向：删去向 / 去向挂空 / 缺口静默丢失
    # ------------------------------------------------------------------
    # 只删掉「已呈现事实」的去向（未呈现事实的去向一删就会被集合键核对发现）。
    expect_error(
        lambda: assemble(section_inputs=(retamper(
            co_input, dispositions=tuple(d for d in co_section.dispositions
                                         if d.disposition != "claimed")), ind_input)),
        RA.ReportAssemblerError, "删掉已呈现事实的去向必须被检出（事实静默丢失）",
        needle="FactNarrativeDisposition 集合键与权威选中事实")
    # 把 claimed 的去向改写成「未呈现」但保留完整 binding 集：逐条引用集闭合立刻发现
    flipped_fnd = tuple(
        NS.FactNarrativeDisposition.create(**{
            **{k: v for k, v in d.to_dict().items() if k != "disposition_id"},
            "disposition": "not_presented_with_reason",
            "accepted_binding_ids": (), "section_claim_ids": (),
            "reason_code": "not_required_for_selected_aspects"})
        if d.disposition == "claimed" else d
        for d in co_section.dispositions)
    expect_error(
        lambda: assemble(section_inputs=(
            retamper(co_input, dispositions=flipped_fnd), ind_input)),
        RA.ReportAssemblerError,
        "把 claimed 的去向改写成未呈现（binding 集被清空）必须被检出",
        needle="引用集不闭合")
    # 去向挂空：FND 指向一个本节缺口集合里不存在的 unresolved
    check(all(d.unresolved_id is None for d in co_section.dispositions
              if d.disposition == "not_presented_with_reason"),
          "前提：未呈现的事实**不是** Contract 必需事实，因此它本来就不挂缺口")
    dangling_fnd = tuple(
        NS.FactNarrativeDisposition.create(**{
            **{k: v for k, v in d.to_dict().items() if k != "disposition_id"},
            "unresolved_id": "unres_does_not_exist"})
        if d.disposition == "not_presented_with_reason" else d
        for d in co_section.dispositions)
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, dispositions=dangling_fnd),
                                         ind_input)),
        RA.ReportAssemblerError, "去向挂到不存在的缺口必须被拒",
        needle="不在本节缺口集合内")
    # 已 claimed 的去向仍绑定缺口：**FND 自己在构造期就拒**（比组装器更浅的一层防线）。因此
    # 组装器里的同名分支只能算 defense-in-depth，本文件从这个方向**触达不到**它——如实记录，
    # 不假装覆盖。
    expect_error(
        lambda: tuple(
            NS.FactNarrativeDisposition.create(**{
                **{k: v for k, v in d.to_dict().items() if k != "disposition_id"},
                "unresolved_id": co_section.result.unresolved[0].unresolved_id})
            if d.disposition == "claimed" else d
            for d in co_section.dispositions),
        NS.NarrativeSchemaError,
        "「已 claimed 的去向绑定缺口」在 FND 构造期即被拒（组装器侧同名检查因此不可达）",
        needle="已 claim 的事实不得同时绑定 unresolved_id")
    skip("§八 6/§十五 5：组装器的「FND 已 claimed 却仍绑定缺口」分支在本链上不可达"
         "（类型层已拒），本文件如实记为未覆盖的 defense-in-depth 分支")

    # 缺口静默丢失：从 canonical SectionResult 里抹掉缺口（draft 侧没法单独抹——那会被
    # SectionDraft 自身拒绝）。此时该 topic 仍有正文，所以**只有**保留链的缺口核对能发现。
    check(bool(co_section.result.unresolved)
          and any(c.topic_id == TOPIC_BUSINESS for c in co_section.claims),
          "缺口丢失用例的前提：该 topic 同时有正文与缺口")
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, unresolved=()), ind_input)),
        RA.ReportAssemblerError, "SectionResult 里的缺口被静默丢弃必须被检出",
        needle="缺口被静默丢弃")

    # ------------------------------------------------------------------
    # 5.2 §十五 7/8/9：覆盖视图改写 / 篡改 Markdown / 权威缺口两侧同删
    # ------------------------------------------------------------------
    # §十五 7：把权威里 partial 的 aspect 在**覆盖视图**里改写成 covered。`coverage_summary`
    # 属于 draft 身份（§七 5），所以先证明「改了覆盖视图必然换 draft_id」，再证明两条复用路径
    # 都被拒：沿用旧 binding（旧 id 指向旧 draft）与整体重绑（自洽但覆盖视图与权威不再守恒）。
    _rows = [dict(r) for r in co_section.draft.coverage_summary.get("aspects", ())]
    _flippable = [i for i, r in enumerate(_rows)
                  if str(r.get("status")) in NS.NON_COVERED_ASPECT_STATUSES]
    check(bool(_rows) and bool(_flippable),
          f"§十五 7 前提：覆盖视图里有一条非覆盖 aspect 可改写，实为 {_rows}")
    _rows[_flippable[0]]["status"] = "covered"
    flipped_coverage = {**co_section.draft.coverage_summary, "aspects": _rows}
    flipped_draft = NS.SectionDraft.create(**{
        **{k: v for k, v in co_section.draft.to_dict().items()
           if k not in ("draft_id", "schema_version")},
        "coverage_summary": flipped_coverage})
    check(flipped_draft.draft_id != co_section.draft.draft_id,
          "§十五 7 前提：覆盖视图变化必须改变 draft_id"
          f"（{_rows[_flippable[0]]['aspect_id']} 被改写成 covered）")
    expect_error(
        lambda: assemble(section_inputs=(
            retamper(co_input, draft=flipped_draft, binding=co_section.binding), ind_input)),
        RA.ReportAssemblerError,
        "§十五 7：覆盖投影被改写（正文/Result 都跟着重绑成新 draft）后**复用旧 binding** 必须被拒"
        "（binding 的 Result/Draft/Gate 身份与实体不一致）",
        needle="与实体不一致")
    expect_error(
        lambda: assemble(section_inputs=(
            retamper(co_input, coverage_summary=flipped_coverage), ind_input)),
        RA.ReportAssemblerError,
        "§十五 7：整体重绑也不能让被改写的覆盖视图过门（必须与权威重算守恒）",
        needle="不守恒")

    # §十五 8：旧 Evaluation + 被篡改的 Markdown。正文只能由唯一渲染器从 final Narrative
    # 重算，调用方自带的 Markdown 一律不信任；指纹字段不是信任根。
    forged_md = co_section.result.markdown + "\n公司已全面停产。\n"
    # 第一层：只改 Markdown 而留着旧指纹 —— `SectionResult` 自身就拒绝「正文与身份脱钩」。
    expect_error(
        lambda: dataclasses.replace(co_section.result, markdown=forged_md),
        SS.SectionSchemaError,
        "§十五 8 第一层：Markdown 与指纹脱钩在 SectionResult 层就被拒",
        needle="正文与身份不得脱钩")
    # 第二层：指纹跟着正文一起伪造（Result 自身自洽、旧 Evaluation 仍写着 PASS_WITH_GAPS），
    # 组装器必须按唯一渲染器从 final Narrative 重算正文并拒绝。
    forged_result = dataclasses.replace(
        co_section.result, markdown=forged_md,
        markdown_fingerprint=NS.body_fingerprint_of(forged_md))
    check(forged_result.markdown_fingerprint
          == NS.body_fingerprint_of(forged_result.markdown),
          "§十五 8 前提：被篡改的 Result 自身指纹自洽（第一层类型校验确实拦不住）")
    expect_error(
        lambda: assemble(section_inputs=(
            dataclasses.replace(co_input, result=forged_result), ind_input)),
        RA.ReportAssemblerError,
        "§十五 8：旧 Evaluation + 被篡改的 Markdown（指纹自洽）必须被拒",
        needle="正文与 final Narrative 重算结果不一致")

    # §十五 9 / §七 3：权威里已是 partial 的 aspect，把它的缺口从 **Draft 与 Result 两侧
    # 同时**删掉：保留链两侧仍然一致、覆盖视图也没动，只有「以 authority 为根」的缺口
    # 守恒能发现它已经消失。期望缺口身份由**写入侧同一实现**派生
    # （`expected_aspect_gap_ids`），不在这里另写一套口径。
    free_gap_ids = sorted(
        set(PW.expected_aspect_gap_ids(
            co_authority, co_task,
            unprojected=sorted(str(a) for a in (
                co_section.draft.coverage_summary.get("unprojected_aspects") or ()))))
        - {d.unresolved_id for d in co_section.dispositions
           if d.unresolved_id is not None})
    check(bool(free_gap_ids),
          "§十五 9 前提：本节有一条未被任何去向引用的权威 aspect 缺口可删")
    victim_gap = free_gap_ids[0]
    trimmed_draft = NS.SectionDraft.create(**{
        **{k: v for k, v in co_section.draft.to_dict().items()
           if k not in ("draft_id", "schema_version")},
        "unresolved_ids": tuple(i for i in co_section.draft.unresolved_ids
                                if i != victim_gap),
        "unresolved_projections": tuple(
            p for p in co_section.draft.unresolved_projections
            if str(p.get("unresolved_id") or "") != victim_gap)})
    kept_unresolved = tuple(u for u in co_section.result.unresolved
                            if u.unresolved_id != victim_gap)
    check(len(kept_unresolved) == len(co_section.result.unresolved) - 1
          and any(g["unresolved_id"] == victim_gap for g in report.gap_index),
          f"§十五 9 前提/反例不是恒失败：被删的缺口在合法报告里本来登记着（{victim_gap}）")
    expect_error(
        lambda: assemble(section_inputs=(retamper(co_input, draft=trimmed_draft,
                                                  unresolved=kept_unresolved), ind_input)),
        RA.ReportAssemblerError,
        "§十五 9：权威缺口被从 Draft 与 Result 两侧同时删掉必须被拒",
        needle="同时消失")

    # ------------------------------------------------------------------
    # 6. 跨章节类型化精确冲突只认**锚点身份**，不按 fact_id 语义猜
    # ------------------------------------------------------------------
    # §十三 1：冲突判据是 (authority_kind, authority_container_id, fact_id) 相同。容器 id 在
    # 真实链上是 Pack 的**内容身份**（`content_fingerprint + dependency_fingerprint`，内容含
    # task / section / topic），因此两个章节**不可能**共享同一个 Pack 容器 ——
    # 「同一容器、两份不同内容」只能是形状替身伪造出来的。这里如实拆成两半：
    #   (a) 组装器层面：fact_id 相同但容器不同 → 不得按语义猜出冲突（不同容器即不同锚点）；
    #   (b) 扫描边界：用一条**伪造的** `_VerifiedSection` 把两节的 Claim 指向同一个锚点，
    #       直接检验该规则本身确实会拒（段落句子面与表格行面各一次）。
    def _conf_mods(*, text: str):
        co_side = dict(facts=(_fact(SHARED_FACT_ID, text, (ASP_BUSINESS_MAIN,)),),
                       aspects=(_AspectResult(ASP_BUSINESS_MAIN, "covered"),),
                       requirements=(_Req(TOPIC_BUSINESS, (
                           _AspectReq(ASP_BUSINESS_MAIN, TOPIC_BUSINESS,
                                      "q-company_business"),)),))
        ind_side = dict(facts=(_fact(SHARED_FACT_ID, text, (ASP_INDUSTRY_SCALE,)),),
                        aspects=(_AspectResult(ASP_INDUSTRY_SCALE, "covered"),),
                        requirements=(_Req(TOPIC_INDUSTRY_SCALE, (
                            _AspectReq(ASP_INDUSTRY_SCALE, TOPIC_INDUSTRY_SCALE,
                                       "q-industry_scale_cycle"),)),))
        return co_side, ind_side

    def conflict_sections(*, co_text: str, ind_text: str):
        """两节各消费**自己的** Pack（同一个 fact_id，不同内容），返回 (输入, 两个容器 id)。"""
        co_mods, _ = _conf_mods(text=co_text)
        co_auth = _authority(co_task, **co_mods)
        co_cid = _pack_id_of(co_auth, TOPIC_BUSINESS)
        co_sc = PW.scan_topic_pack(co_auth, co_task)
        co_mat = sorted(_material_ids_of(co_sc)[co_cid])[0]
        co_out = CW.run_backbone_writer_phase(
            (CW.BackboneWriterSectionInput(
                task=co_task, authority=co_auth, projection=projections["company"],
                writing_spec=spec, presentation_profile=profile,
                dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
            llm_client=_StubLlm(_plan(
                candidates=[_cand("c-1", _entries(co_sc)[SHARED_FACT_ID].text,
                                  _fact_edge(co_sc, SHARED_FACT_ID))],
                units=[_unit("u-1", "以下为背景说明。",
                             context=[_context_edge(co_cid, co_mat)])])),
            entailment_llm_client=_StubEntailmentClient(_ENTAILED),
            final_sentence_llm_client=_stub_final_sentence_client(),
            material_resolver=_payload_resolver()).sections[0]
        _, ind_mods = _conf_mods(text=ind_text)
        ind_auth = _authority(ind_task, **ind_mods)
        ind_cid = _pack_id_of(ind_auth, TOPIC_INDUSTRY_SCALE)
        ind_sc = PW.scan_topic_pack(ind_auth, ind_task)
        ind_mat = sorted(_material_ids_of(ind_sc)[ind_cid])[0]
        ind_out = CW.run_backbone_writer_phase(
            (CW.BackboneWriterSectionInput(
                task=ind_task, authority=ind_auth, projection=projections["industry"],
                writing_spec=spec, presentation_profile=profile,
                dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
            llm_client=_StubLlm(_plan(
                candidates=[_cand("c-1", _entries(ind_sc)[SHARED_FACT_ID].text,
                                  _fact_edge(ind_sc, SHARED_FACT_ID))],
                units=[_unit("u-1", "以下为背景说明。",
                             context=[_context_edge(ind_cid, ind_mat)])])),
            entailment_llm_client=_StubEntailmentClient(_ENTAILED),
            final_sentence_llm_client=_stub_final_sentence_client(),
            material_resolver=_payload_resolver()).sections[0]
        return ((_assembly_input(co_out, co_task, co_auth),
                 _assembly_input(ind_out, ind_task, ind_auth)),
                (co_cid, ind_cid), (co_out, ind_out))

    diff_number_inputs, diff_containers, (dco, dind) = conflict_sections(
        co_text="公司注册资本下限为 1000.00 万元。",
        ind_text="行业口径下注册资本上限为 2000.00 万元。")
    check(len(set(diff_containers)) == 2,
          f"前提：两节的 Pack 容器身份由内容决定、必然不同（实为 {diff_containers}）")
    check(not assemble(section_inputs=diff_number_inputs,
                       version_inputs=declared(*diff_number_inputs)).conflict_index,
          "fact_id 相同但容器不同不算跨章节冲突（不得按语义猜测锚点）")

    same_number_inputs, _, _ = conflict_sections(co_text="公司注册资本为 1000.00 万元。",
                                                 ind_text="行业口径下注册资本为 1000.00 万元。")
    check(not assemble(section_inputs=same_number_inputs,
                       version_inputs=declared(*same_number_inputs)).conflict_index,
          "同一锚点、同一数字时不得误报跨章节冲突")

    # (b) 扫描边界：把两节的 Claim 指向同一个权威锚点（用伪造的 `_VerifiedSection`），
    # 段落句子面上的不同数字必须被拒。
    def _forged_verified(co_section_out, ind_section_out, co_item):
        ind_factual = tuple(b for b in ind_section_out.acceptance.accepted_bindings
                            if str(b.support_semantics) == "factual")
        return RA._VerifiedSection(
            rendered_markdown="",
            factual_bindings_by_claim={co_item.claims[0].claim_id: ind_factual},
            context_binding_ids=(), factual_bindings_by_fact={})

    def _verified_map(*pairs):
        return {item.task.task_id: verified for item, verified in pairs}

    diff_inputs = (diff_number_inputs[0], diff_number_inputs[1])
    expect_error(
        lambda: RA._cross_section_conflicts(
            diff_inputs,
            _verified_map((diff_inputs[0], _forged_verified(dco, dind, diff_inputs[0])),
                          (diff_inputs[1], RA._verify_binding(diff_inputs[1])))),
        RA.ReportAssemblerError, "同一权威锚点被两条已写 Claim 以不同数字断言必须被拒",
        needle="跨章节类型化精确冲突")
    # 反例不是恒失败：同一锚点、同一数字 → 不报冲突。
    same_inputs = (same_number_inputs[0], same_number_inputs[1])
    check(RA._cross_section_conflicts(
        same_inputs,
        _verified_map((same_inputs[0], _forged_verified(same_number_inputs[0],
                                                        same_number_inputs[1],
                                                        same_inputs[0])),
                      (same_inputs[1], RA._verify_binding(same_inputs[1])))) == (),
          "同一权威锚点、同一数字时不得误报冲突（反例不是恒失败）")

    # §十五 14 / §十三 1：数字只出现在**表格行**里时同样必须进入冲突扫描。
    # 归属规则：事实表格行的 `cells[i]` 逐字来自 `claim_ids[i]`，所以单元格数字只归它对应的
    # Claim —— 表格行与段落句子是同一套数字断言，不得只看段落。
    table_inputs = (diff_number_inputs[0], diff_number_inputs[1])
    co_table_claim = table_inputs[0].claims[0]
    co_table_text = co_table_claim.text
    co_cites = tuple(SS.derive_citation_id(co_table_claim.claim_id, ref)
                     for ref in co_table_claim.citation_refs)
    table_narrative = NS.SectionNarrative.create(
        task_id=table_inputs[0].draft.task_id, section_id=table_inputs[0].draft.section_id,
        section_draft_id=table_inputs[0].draft.draft_id,
        draft_revision=table_inputs[0].draft.draft_revision,
        paragraphs=(),
        tables=(NS.NarrativeTable.create(
            section_id=table_inputs[0].draft.section_id,
            topic_ids=(co_table_claim.topic_id,), index=0,
            caption=co_table_text, header=(co_table_text, co_table_text),
            row_specs=({"label": co_table_text, "cells": (co_table_text,),
                        "claim_ids": (co_table_claim.claim_id,),
                        "citation_ids": co_cites,
                        "unit": "万元", "period": REPORT_AS_OF},
                       ),
            entity_scope=co_table_text, unit="万元", period=REPORT_AS_OF),))
    check(not table_narrative.paragraphs and len(table_narrative.tables) == 1,
          "§十五 14 前提：该反例的数字断言**只**出现在表格行里（段落为空）")
    table_item = dataclasses.replace(table_inputs[0], narrative=table_narrative)
    expect_error(
        lambda: RA._cross_section_conflicts(
            (table_item, table_inputs[1]),
            _verified_map((table_item, _forged_verified(table_inputs[0], table_inputs[1],
                                                        table_inputs[0])),
                          (table_inputs[1], RA._verify_binding(table_inputs[1])))),
        RA.ReportAssemblerError,
        "§十五 14：表格行里的数字必须进入跨章节精确冲突扫描（不得只看段落）",
        needle="跨章节类型化精确冲突")

    # ------------------------------------------------------------------
    # 7. 非 300750 样本走完全相同的规则
    # ------------------------------------------------------------------
    other_company = "另一家非样本公司"

    def other_authority_for(task, *, facts, aspects, requirements):
        return _authority(task, facts=facts, aspects=aspects, requirements=requirements,
                          company_id=other_company)

    def _drive(task, authority, projection_id: str, plan: dict):
        return CW.run_backbone_writer_phase(
            (CW.BackboneWriterSectionInput(
                task=task, authority=authority, projection=projections[projection_id],
                writing_spec=spec, presentation_profile=profile,
                dependency_fingerprint=DEPENDENCY_FINGERPRINT),),
            llm_client=_StubLlm(plan),
            entailment_llm_client=_StubEntailmentClient(_ENTAILED),
            final_sentence_llm_client=_stub_final_sentence_client(),
            material_resolver=_payload_resolver()).sections[0]

    other_co_authority = other_authority_for(
        co_task, facts=co_facts, aspects=co_aspects, requirements=co_requirements)
    other_co_pack = _pack_id_of(other_co_authority, TOPIC_BUSINESS)
    other_co_scan = PW.scan_topic_pack(other_co_authority, co_task)
    other_co_mat = sorted(_material_ids_of(other_co_scan)[other_co_pack])[0]
    other_co_entry = _entries(other_co_scan)
    other_co_out = _drive(co_task, other_co_authority, "company", _plan(
        candidates=[_cand("c-main", other_co_entry["f-main"].text,
                          _fact_edge(other_co_scan, "f-main"))],
        units=[_unit("u-1", "本节就公司主营业务作背景说明。",
                     context=[_context_edge(other_co_pack, other_co_mat)])]))
    other_ind_authority = other_authority_for(
        ind_task, facts=ind_facts, aspects=ind_aspects, requirements=ind_requirements)
    other_ind_pack = _pack_id_of(other_ind_authority, TOPIC_INDUSTRY_SCALE)
    other_ind_scan = PW.scan_topic_pack(other_ind_authority, ind_task)
    other_ind_mat = sorted(_material_ids_of(other_ind_scan)[other_ind_pack])[0]
    other_ind_entry = _entries(other_ind_scan)
    other_ind_out = _drive(ind_task, other_ind_authority, "industry", _plan(
        candidates=[_cand("c-scale", other_ind_entry["f-scale"].text,
                          _fact_edge(other_ind_scan, "f-scale"))],
        units=[_unit("u-1", "本节说明行业规模。",
                     context=[_context_edge(other_ind_pack, other_ind_mat)])]))

    other_input = _assembly_input(other_co_out, co_task, other_co_authority)
    other_ind_input = _assembly_input(other_ind_out, ind_task, other_ind_authority)
    expect_error(
        lambda: assemble(section_inputs=(other_input, ind_input),
                         version_inputs=declared(other_input, ind_input)),
        RA.ReportAssemblerError,
        "同一份报告混入两家公司的章节必须被拒", needle="与冻结投影不一致")
    # 反向证明：同一套规则下，另一家公司的两节可以正常组装（没有公司专用分支）
    other_projection = _projection((co_task, ind_task), company_id=other_company)
    other_inputs = (other_input, other_ind_input)
    other_report = assemble(projection=other_projection,
                            section_inputs=other_inputs,
                            version_inputs=declared(*other_inputs))
    check(other_report.company_id == other_company,
          "非 300750 样本报告可正常组装（规则完全一致，无公司专用分支）")
    check(len(other_report.sections) == 2 and not other_report.conflict_index,
          "非 300750 样本报告同样是两节、无跨章节冲突")

    # ------------------------------------------------------------------
    # §十五 12/13：LLM evaluator 的**畸形输出**与**blocking 结论**都必须阻断。
    # 只用 stub，不联网：这条链的证据是「加问题的手续」而不是模型本身。
    #
    # M930-3（§三 C）之后 P11（`sections/rules_evaluator.py`）的 LLM evaluator 分支消费的是
    # **门后 final Narrative 的实际句**（已不再读 narr-3 的 `draft.paragraphs`），因此这两条
    # 反例在本链上真实可驱动。下面的 `except AttributeError` 只作**防御性守卫**：若哪天又有
    # 分支回退到门前正文字段，这里如实记 skip，而不是把「没法评」读成「评过了」。
    # ------------------------------------------------------------------
    try:
        llm_baseline = RE.evaluate_narrative_section(
            co_input, task=co_task, authority=co_authority,
            llm_client=_StubLlm({"issues": []}), allow_llm_evaluator=True,
            acceptance=co_input.acceptance)
    except AttributeError as exc:  # noqa: BLE001 — 防御性守卫，见上
        if "paragraphs" in str(exc):
            skip("§十五 12/13：LLM evaluator 分支回退到门前正文字段 `draft.paragraphs`"
                 f"（narr-4 门前 SectionDraft 无该字段，实际异常 {type(exc).__name__}: {exc}），"
                 "因此本节的两条反例（畸形输出必须阻断 / LLM blocking 不得被降级）"
                 "在本文件中未覆盖；如实上报，不假装跑过")
        else:
            check(False, f"§十五 12/13：LLM evaluator 抛出非预期 AttributeError（{exc}）")
    else:
        check(llm_baseline.evaluation.decision == "PASS_WITH_GAPS"
              and llm_baseline.llm_evaluator_calls == 1,
              "对照组：clean 的 LLM 结论不得改变既有的 PASS_WITH_GAPS 与调用计数")
        malformed = RE.evaluate_narrative_section(
            co_input, task=co_task, authority=co_authority,
            llm_client=_StubLlm("这不是 JSON"), allow_llm_evaluator=True,
            acceptance=co_input.acceptance)
        check(malformed.evaluation.decision == "BLOCKED"
              and any(i.rule_id == "narrative_evaluator_output_malformed"
                      for i in malformed.evaluation.issues),
              "§十五 12：LLM evaluator 畸形输出必须显式阻断（「模型没回答」≠「模型说没问题」）")
        check(malformed.llm_evaluator_calls == 1,
              "§九：畸形输出仍必须如实记一次调用（不能只记解析成功的次数）")
        llm_blocking = RE.evaluate_narrative_section(
            co_input, task=co_task, authority=co_authority,
            llm_client=_StubLlm({"issues": [
                {"rule_id": "unsupported_claim", "severity": "blocking",
                 "location": "company", "detail": "正文断言无法回到权威事实"}]}),
            allow_llm_evaluator=True, acceptance=co_input.acceptance)
        check(llm_blocking.evaluation.decision == "BLOCKED",
              "§十五 13 / §九：LLM 给出的 blocking 结论不得被降级成 PASS"
              f"（实为 {llm_blocking.evaluation.decision}）")
        check(llm_blocking.evaluation.llm_passed is False
              and llm_blocking.evaluation.rules_passed is True,
              "§九：LLM 只能加问题——确定性规则通过、LLM blocking，两者必须分别如实记录")

    # ------------------------------------------------------------------
    # §八 3/4 / §十五 1：门必须**每次重新计算**，不能相信调用方带来的 clean 门。
    #
    # narr-4 的可行反例：在被篡改的 draft 里多放一个含含糊期间措辞的草稿单元（不需要任何
    # 候选/支撑边级联，`SectionDraft` 自身照样接受），再把门伪造成 clean。支撑边、citation、
    # 去向链全部完好，保留链看不见它；唯一能发现它的就是「组装器自己重算一遍门」。
    # ------------------------------------------------------------------
    extra_unit = NS.NarrativeDraftUnit.create(
        draft_revision=co_section.draft.draft_revision, section_id="company",
        index=len(co_section.draft.narrative_draft_units),
        unit_kind="paragraph", text="报告期内公司经营情况如下。")
    vague_draft = NS.SectionDraft.create(**{
        **{k: v for k, v in co_section.draft.to_dict().items()
           if k not in ("draft_id", "schema_version")},
        "narrative_draft_units": tuple(co_section.draft.narrative_draft_units)
        + (extra_unit,)})
    recomputed_vague_gate = NS.gate_draft(vague_draft, co_authority)
    check(recomputed_vague_gate.blocking and not co_section.gate_result.blocking,
          "前提：多出的含糊期间草稿单元必须让**重算**的门 blocking，"
          "而调用方那份门是 clean 的")
    check("narrative_vague_period" in {i.rule_id for i in recomputed_vague_gate.issues
                                       if i.severity == "blocking"},
          "前提：含糊期间必须由 narrative_vague_period 规则发现")
    vague_input = retamper(co_input, draft=vague_draft)
    expect_error(
        lambda: assemble(section_inputs=(vague_input, ind_input)),
        RA.ReportAssemblerError,
        "§八 4：伪造的 clean 门不得让未绑定期限的草稿单元进入组装"
        "（组装器必须自己重算门，而不是复核调用方带来的门）",
        needle="narrative 硬门与当前重算结果不一致")

    # §八 3：评估入口同样必须用**重算**的门，且把「调用方那份门对不上」本身记成 blocking。
    vague_eval = RE.evaluate_narrative_section(vague_input, task=co_task,
                                               authority=co_authority,
                                               acceptance=vague_input.acceptance)
    check(vague_eval.evaluation.decision == "BLOCKED"
          and vague_eval.binding.narrative_gate_result_id
          != vague_input.gate_result.gate_result_id,
          "§八 3：Binding 只能绑到**当前重算**的门，调用方带来的旧 clean 门不得成为评估依据")
    check("narrative_gate_stale" in {i.rule_id for i in vague_eval.evaluation.issues
                                      if i.severity == "blocking"},
          "§十五 11：调用方带来的旧 clean 门与当前重算的门不一致**本身**必须是 blocking 规则"
          "（`narrative_gate_stale`），不得只靠下游的内容规则顺手拦住")
    check("narrative_gate_blocking" in {i.rule_id for i in vague_eval.evaluation.issues
                                        if i.severity == "blocking"},
          "§八 3：重算出的门 blocking 时，新叙事入口必须据此阻断")
    check(vague_eval.summary["narrative_gate_supplied_matched"] is False
          and vague_eval.summary["narrative_gate_blocking"] is True,
          "§八 3：评估摘要必须如实记录「门是重算的、调用方那份对不上、重算结果 blocking」")

    # §十五 11 的另一半：把被改写的 draft **错绑**到真·旧 clean 门上（binding 也照着那份
    # 旧门绑）—— 所有 id 交叉引用都自洽，但门根本不属于这份 draft。
    misbound = dataclasses.replace(
        vague_input, gate_result=co_section.gate_result,
        binding=rebind(section_draft_id=vague_input.draft.draft_id,
                       section_result_id=vague_input.result.section_result_id,
                       evaluation_id=vague_input.evaluation.evaluation_id,
                       narrative_gate_result_id=co_section.gate_result.gate_result_id))
    check(misbound.binding.narrative_gate_result_id
          == misbound.gate_result.gate_result_id,
          "§十五 11 前提：binding 与它带来的门 id 是「配套」的（错绑要靠门与 draft 的关系发现）")
    expect_error(
        lambda: assemble(section_inputs=(misbound, ind_input)),
        RA.ReportAssemblerError,
        "§十五 11：新 Draft 的正文错绑到旧 clean 门上必须被拒（门必须属于这份 draft）",
        needle="gate 对象错配")

    # §五 2：final Narrative 只能由唯一实现从**定稿 Claim** 派生 —— 自带一份被改写的段落
    # （指纹/版本全部跟着重算、整束自洽）必须被拒。
    co_first_claim = co_section.claims[0]
    drifted_para = NS.NarrativeParagraph.create(
        section_id="company", topic_ids=(co_first_claim.topic_id,), index=0,
        sentence_specs=[
            {"text": co_first_claim.text, "sentence_kind": "factual",
             "claim_ids": [co_first_claim.claim_id],
             "citation_ids": [SS.derive_citation_id(co_first_claim.claim_id, ref)
                              for ref in co_first_claim.citation_refs]},
            {"text": "公司已全面停产。", "sentence_kind": "factual",
             "claim_ids": [co_first_claim.claim_id],
             "citation_ids": [SS.derive_citation_id(co_first_claim.claim_id, ref)
                              for ref in co_first_claim.citation_refs]}])
    drifted_narrative = NS.SectionNarrative.create(
        task_id=co_section.narrative.task_id, section_id=co_section.narrative.section_id,
        section_draft_id=co_section.narrative.section_draft_id,
        draft_revision=co_section.narrative.draft_revision,
        paragraphs=(drifted_para,), tables=(),
        context_binding_ids=co_section.narrative.context_binding_ids)
    expect_error(
        lambda: assemble(section_inputs=(
            retamper(co_input, narrative=drifted_narrative), ind_input)),
        RA.ReportAssemblerError,
        "§五 2：调用方自带的 final Narrative 段落必须被「从定稿 Claim 重算」逐字段比对拒绝",
        needle="final Narrative 段落与从当前 Claim 集重算的结果不一致")

    # ------------------------------------------------------------------
    # §八 5 / §十五 10：同一个 WAITING_HUMAN 的 canonical SectionResult，在**旧规则入口**与
    # **新叙事入口**里都必须保持阻断。缺口的类型/后果/文本/影响范围全部照抄基线，只把
    # `state` 换成 `WAITING_HUMAN`（**不换 id**：换 id 会让缺口守恒先拒，反例就退化成了
    # 「另一条缺口」而不是「同一条缺口的新状态」）。
    # ------------------------------------------------------------------
    base_gap = co_section.result.unresolved[0]
    check(base_gap.state != "WAITING_HUMAN",
          f"前提：基线缺口状态不是 WAITING_HUMAN（实为 {base_gap.state!r}）")
    waiting_gaps = tuple(dataclasses.replace(u, state="WAITING_HUMAN")
                         for u in co_section.result.unresolved)
    waiting_input = retamper(co_input, unresolved=waiting_gaps)
    waiting_verdict = RE.evaluate_section(waiting_input.result, co_task)
    waiting_blocking = sorted({i.rule_id for i in waiting_verdict.issues
                               if i.severity == "blocking"})
    check(waiting_blocking == ["waiting_human"],
          "§八 5：同一 WAITING_HUMAN Result 在**旧规则入口**必须保持阻断，"
          f"且阻断只能来自这条语义（实为 {waiting_blocking}）")
    waiting_eval = RE.evaluate_narrative_section(
        waiting_input, task=co_task, authority=co_authority,
        acceptance=waiting_input.acceptance)
    check(waiting_eval.evaluation.decision == "BLOCKED"
          and waiting_eval.summary["blocking_source"] == "rules",
          "§十五 10：WAITING_HUMAN 在新叙事入口必须同样阻断，"
          f"实为 {waiting_eval.evaluation.decision}")
    check("waiting_human" in {i.rule_id for i in waiting_eval.evaluation.issues
                              if i.severity == "blocking"},
          "§八 5：新入口的阻断必须仍然来自同一条 waiting_human 规则语义")
    blocked_input = dataclasses.replace(waiting_input,
                                        evaluation=waiting_eval.evaluation,
                                        binding=waiting_eval.binding)
    check(blocked_input.binding.evaluation_id == blocked_input.evaluation.evaluation_id
          and blocked_input.binding.section_result_id
          == blocked_input.result.section_result_id,
          "§八 4 前提：BLOCKED 的评估与它的 binding 是配套的（拒绝必须来自评估结论本身）")
    expect_error(
        lambda: assemble(section_inputs=(blocked_input, ind_input)),
        RA.ReportAssemblerError,
        "§八 4：结论为 BLOCKED 的章节不得被当成 PASS 组装（WAITING_HUMAN 不得被放行）",
        needle="不得进入组装")

    # ------------------------------------------------------------------
    # §十五 19 / §十四 / §三 C / §三 G：门后 final-Narrative 核验现在是**已实现**的，
    # 而且只有一处实现。narr-4 的门（`NS.gate_draft`）只覆盖门前候选束，因此这两条
    # （跨 topic 组织 §十四、事实表面守恒 §十）**必须**落在门后，并且被三处调用点共用：
    # 组织器（发出前自检）、章级评估（`rules_evaluator`）、组装器（读回后复算）。
    # ------------------------------------------------------------------
    narrative_src = (ROOT / "sections" / "narrative_schema.py").read_text(encoding="utf-8")
    check("cross_topic_composition_not_obtained" not in narrative_src
          and "narrative_fact_surface_drift" not in narrative_src
          and "narrative_table_display_drift" not in narrative_src,
          "§十五 19 前提：门前的旧规则名不得复活（门后核验不得被写成门前门）")
    check("def verify_section_narrative(" in narrative_src,
          "§十五 19：门后 final-Narrative 的确定性核验必须由 narrative_schema 的**唯一**实现给出")
    check("本门不实现这两条" in narrative_src,
          "§十五 19：`gate_draft` 必须仍然显式声明**本门不实现**门后两条"
          "（门后核验是另一个位置，不得被读成门前门已经覆盖）")
    for module_name in ("narrative_organizer", "rules_evaluator", "report_assembler"):
        module_src = (ROOT / "sections" / f"{module_name}.py").read_text(encoding="utf-8")
        check("verify_section_narrative" in module_src,
              f"§三 C：sections/{module_name}.py 必须调用同一个门后核验"
              "（三处调用点 = 同一口径，不得各写一套）")
    organizer_src = (ROOT / "sections" / "narrative_organizer.py").read_text(encoding="utf-8")
    check("def verify_section_narrative(" not in organizer_src,
          "§三 C：组织器**不能**自己实现核验（否则它就成了自己输出的批准者）")
    # 行为面：段落自然组织句与表格行必须**都**在事实表面守恒面上（§三 G 的「跨段落」）。
    co_assertions = RA._numeric_assertions(diff_number_inputs[0])
    check(bool(co_assertions)
          and all(cid in {c.claim_id for c in diff_number_inputs[0].claims}
                  for cid, _tokens in co_assertions),
          "§三 G：门后自然组织句的数字断言必须进入跨章节守恒扫描，且归属仅限于本节已定稿 Claim"
          "（否则段落面的守恒在 narr-5 上会退化成空集）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""Eval: M930-3C P9 Claim 级语义核验门（§16.7.1 P9/P19；§16.10 第 5/27/29/40/42 项）。

用法: python -m evals.test_demo_claim_entailment_gate

本文件用 **stub client**（不联网、不真实调用）证明语义门的**纪律**，而不是它的判断质量：

* §5  context 错误进入 entailment：context `NarrativeDraftUnit` 零条决定、零次调用；
* §27 aggregate 通过却配到零条/两条 entailment 决定，或同一 candidate revision 的束出现两次
      → 拒（同一 candidate revision 恰好一条）；
* §29 aggregate=通过的决定必须逐条对得上（无对应候选的决定不得被静默丢掉）；
* §40 aggregate=fail 仍产生 entailment 决定、或 context 产生任意 entailment 决定 → 拒；
* §42 prompt 资产名/正文/hash 漂移但**仍**发起调用 → 必须在调用前拒（调用次数 0）；决定必须
      绑定 prompt 版本、rubric 版本、model policy 与 call id，且实际调用模型必须与决定一致；
* §cer-3 / §cer-4 镜像分支：`authority_fact_mirror` 是**内容相等**读数（不是语义判断）——
      `cer-4` 起在**标点归一读视图**上比较（只动句读标点，数值/期间/主体/口径限定语等实词
      逐字参与）；候选与**恰好一条**权威事实内容相等时不得判原子性拒绝码，`match_count` 为
      0 或多条时一律回到一般语义判据；机械读数**不代替**语义判断（门不替模型改判），也不把
      「拼两条事实」或「改一个实词」变成合法；
* 纪律：每个通过机械门的 factual candidate **至多一次**调用、只降级不升级（拿不准一律
      rejected）；传输失败/非 JSON/枚举越界一律抛出，**不伪造**一条 rejected。

不调 LLM、不联网、不写任何文件。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

from harness import topic_schema as TS
from llm import client as llm
from sections import claim_binding_gate as CBG
from sections import material_context as MC
from sections import claim_entailment_evaluator as CEE
from sections import narrative_schema as NS
from sections import pack_writer as PW

FP = "a" * 64

#: `wmm-2` 成员身份必须包含**可解析的真实引用**（§三 A.7）：只有 material ID 的成员现在
#: 在构造期就不可表达，因为「成员存在」与「正文已解析」必须同一件事。这里用真实
#: `MaterialPayloadRef` 的规范 dict 构造（与生产同形，不做形态替身）。
PAYLOAD_REF = TS.MaterialPayloadRef(
    object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
    content_hash=FP,
    locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                               section_path="s1", page=3),
    created_dependency_fingerprint="d" * 64).to_dict()


def _member_locator_ref() -> dict:
    """成员与引用它的路径 B / context 边共用的 exact locator（`loc-1`）。"""
    return NS.char_range_locator("evidence:ev-1", 3, 40)


def _manifest_member(*, content_fingerprint: str = FP) -> NS.WriterMaterialManifestEntry:
    """一份 `wmm-2` 成员（同一 material，可按内容指纹构造出身份不同的另一份）。"""
    payload_ref = TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
        content_hash=content_fingerprint,
        locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                                   section_path="s1", page=3),
        created_dependency_fingerprint="d" * 64).to_dict()
    return NS.WriterMaterialManifestEntry.create(
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=content_fingerprint, topic_id="t-1",
        material_type="evidence_span", payload_ref=payload_ref,
        locator_ref=_member_locator_ref(), payload_hash=content_fingerprint,
        reading_view_fingerprint=content_fingerprint)


def _scan(*facts):
    return PW.AuthorityScan(facts=tuple(facts), aspect_status={}, aspect_topic={},
                            aspect_impact={}, aspect_blocking={}, aspect_question={},
                            excluded_facts=(), conflicts=(), not_found=(), gaps=(),
                            coverage_counts={})


PACK_FACT = PW.AuthorityFactEntry(
    authority_kind="topic_pack", container_identity="pack-1", fact_id="f-1",
    text="公司2024年营业收入为1234.56亿元。", topic_id="t-1", aspect_ids=("a-1",),
    required=True, fact_type="metric", period="2024", scope="公司", material_id="m-1",
    payload_ref={"object_type": "research_material"}, locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40),
    source_identity="evidence:ev-1", provenance_identity="prov-1", content_fingerprint=FP)

MANIFEST = NS.WriterMaterialManifest.create(members=(_manifest_member(),))
REVISION = NS.derive_draft_revision(
    task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
    contract_version="cv-1", contract_fingerprint="cf-1", writer_policy_version="wp-1",
    prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
    manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint())
CANDIDATE = NS.ClaimCandidate.create(
    draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入为1234.56亿元。", fact_type="metric")
CANDIDATE_2 = NS.ClaimCandidate.create(
    draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入同比下降。", fact_type="metric")
UNIT = NS.NarrativeDraftUnit.create(
    draft_revision=REVISION, section_id="company", index=0, unit_kind="paragraph",
    text="本节说明公司经营情况。")


def _proposal(candidate, **over):
    kw = dict(binding_subject_kind="claim_candidate", binding_subject_id=candidate.candidate_id,
              draft_revision=REVISION, manifest_id=MANIFEST.manifest_id,
              manifest_fingerprint=MANIFEST.fingerprint(), authority_kind="topic_pack",
              authority_container_id="pack-1", source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="primary", support_semantics="factual",
              authorization_path="path_a_prevalidated", content_fingerprint=FP,
              dependency_fingerprint="dep-1", fact_id="f-1", material_id="m-1",
              payload_ref={"object_type": "research_material"},
              locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40))
    kw.update(over)
    return NS.ProposedSupportRef.create(**kw)


def _context_proposal():
    return NS.ProposedSupportRef.create(
        binding_subject_kind="narrative_draft_unit", binding_subject_id=UNIT.draft_unit_id,
        draft_revision=REVISION, manifest_id=MANIFEST.manifest_id,
        manifest_fingerprint=MANIFEST.fingerprint(), authority_kind="topic_pack",
        authority_container_id="pack-1", source_identity="evidence:ev-1",
        provenance_identity="prov-1", support_role="corroborating",
        support_semantics="context", authorization_path="context_only",
        content_fingerprint=FP, dependency_fingerprint="dep-1", material_id="m-1",
        # §四.2：context 边与路径 B 同一闭合口径（载体 + payload + locator 同在一条边上），
        # 两个字段都必须与该 manifest 成员**逐字**相同。
        payload_ref=dict(MANIFEST.entries[0].payload_ref),
        locator_ref=_member_locator_ref())


# ---------------------------------------------------------------------------
# §五 夹具：真实（自洽）的路径 B 材料上下文，以及它的伪造对照
# ---------------------------------------------------------------------------

#: 真实上下文里的正文（**只**在这一个地方定义：真伪两份**只**差在这串文本，其余全部同形）。
_REAL_READING = "公司主营业务覆盖工业产品的研发、生产与销售，产品应用于多个下游行业。"

#: 伪造者会写的「任意正文」：与真实正文完全不同，且**不含**任何真实材料里的内容。
_FORGED_READING_TEXT = "本节所述内容均以公开材料为准。"


def _real_reading() -> "MC.ResolvedWriterMaterial":
    """真实读视图：指纹由 `create` 从正文确定性重算（与生产同一入口）。"""
    return MC.ResolvedWriterMaterial.create(
        member_ref=NS.manifest_member_ref("pack-1", "m-1"), topic_id="t-1", pack_id="pack-1",
        material_id="m-1", material_type="evidence_span",
        research_material_disposition_id="rmd-1", source_identity="evidence:ev-1",
        provenance_identity="prov-1", locator_ref=_member_locator_ref(),
        payload_ref=dict(MANIFEST.entries[0].payload_ref), payload_hash=FP, content_hash=FP,
        material_content_fingerprint=FP, reading_view=_REAL_READING)


def _path_b_material():
    """路径 B 的成员 + **真实** `wmctx-1` 上下文。

    成员按**真实读视图**的指纹构造，因此「成员声明的那一份」与「上下文里解析出来的那一份」
    逐字相同——这正是正例的基线，也是伪造反例唯一要破坏的东西。
    """
    reading = _real_reading()
    member = NS.WriterMaterialManifestEntry.create(
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=dict(reading.payload_ref), locator_ref=dict(reading.locator_ref),
        payload_hash=reading.payload_hash,
        reading_view_fingerprint=reading.reading_view_fingerprint)
    context = MC.WriterMaterialContext.create(
        task_id="t-1", section_id="company", pack_set_fingerprint="e" * 64,
        materials=(reading,))
    return member, context


def _forged_reading(reading_view: str = _FORGED_READING_TEXT) -> "MC.ResolvedWriterMaterial":
    """同 `member_ref`、任意正文、**自洽重算**指纹的伪造读视图。

    它把「伪造」做到底：指纹不是抄来的，而是按伪造正文重新算出来的（`create` 会算）。
    因此任何只比较 `member_ref` 与「正文非空」的实现都拦不住它——唯一能拦住它的是拿成员
    声明的 payload/hash/locator/指纹回查真实正文的那个入口。
    """
    return MC.ResolvedWriterMaterial.create(
        member_ref=NS.manifest_member_ref("pack-1", "m-1"), topic_id="t-1", pack_id="pack-1",
        material_id="m-1", material_type="evidence_span",
        research_material_disposition_id="rmd-1", source_identity="evidence:ev-1",
        provenance_identity="prov-1", locator_ref=_member_locator_ref(),
        payload_ref=dict(MANIFEST.entries[0].payload_ref), payload_hash=FP, content_hash=FP,
        material_content_fingerprint=FP, reading_view=reading_view)


def _forged_context() -> "MC.WriterMaterialContext":
    """伪造正文的上下文（自洽：`context_id` 按伪造内容重算，不是照抄真实上下文）。"""
    return MC.WriterMaterialContext.create(
        task_id="t-1", section_id="company", pack_set_fingerprint="e" * 64,
        materials=(_forged_reading(),))


def _forged_bundle(proposal, member) -> "CEE.FactualCandidateRevision":
    """绕过 `factual_candidate_revisions`、直接用伪造上下文构造的束（模拟手工协调器）。

    成员用的是**真实**那一份，只有上下文被换成伪造正文——这正是 §五 描述的攻击面。
    """
    return CEE.FactualCandidateRevision(
        candidate=CANDIDATE, proposals=(proposal,), authority_facts=(),
        materials=(member,), material_context=_forged_context())


def _self_consistent_forged_bundle(candidate, revision,
                                   manifest) -> "CEE.FactualCandidateRevision":
    """**完全自洽**的伪造束：自造成员 + 与它配套的伪造上下文（成员 ↔ 正文一致）。

    它绕过了 `factual_candidate_revisions`，因此成员不是从本次 manifest 解析出来的：成员
    与正文互相印证，构造期看不出问题。唯一能发现它的是「成员是否属于本次 exact manifest」
    这条对齐。
    """
    reading = _forged_reading()
    forged_member = NS.WriterMaterialManifestEntry.create(
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=dict(reading.payload_ref), locator_ref=dict(reading.locator_ref),
        payload_hash=reading.payload_hash,
        reading_view_fingerprint=reading.reading_view_fingerprint)
    # 边本身是**真**的（挂在本次 manifest 上，digest 因此对得上）：伪造只发生在
    # 「成员 + 正文」这一侧——这样反例才落在 §五 的那条缝上，而不是被 digest 提前挡掉。
    proposal = NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id=candidate.candidate_id,
        draft_revision=revision, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), authority_kind="topic_pack",
        authority_container_id="pack-1", source_identity="evidence:ev-1",
        provenance_identity="prov-1", support_role="primary", support_semantics="factual",
        authorization_path="path_b_material_derived", content_fingerprint=FP,
        dependency_fingerprint="dep-1", material_id="m-1",
        payload_ref=dict(forged_member.payload_ref), locator_ref=dict(forged_member.locator_ref))
    return CEE.FactualCandidateRevision(
        candidate=candidate, proposals=(proposal,), authority_facts=(),
        materials=(forged_member,), material_context=_forged_context())


def _revision_for(manifest) -> str:
    """按给定 manifest 复算 draft revision（候选/边/草稿必须同属一个 revision）。"""
    return NS.derive_draft_revision(
        task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
        contract_version="cv-1", contract_fingerprint="cf-1", writer_policy_version="wp-1",
        prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
        manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint())


def _path_b_proposal(candidate, member, *, revision, manifest):
    """路径 B 支撑边：载体 + payload + exact locator 与成员逐字相同（§四.1 的闭合口径）。"""
    return NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id=candidate.candidate_id,
        draft_revision=revision, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), authority_kind="topic_pack",
        authority_container_id="pack-1", source_identity="evidence:ev-1",
        provenance_identity="prov-1", support_role="primary", support_semantics="factual",
        authorization_path="path_b_material_derived", content_fingerprint=FP,
        dependency_fingerprint="dep-1", material_id="m-1",
        payload_ref=dict(member.payload_ref), locator_ref=dict(member.locator_ref))


def _draft(proposals, candidates=(CANDIDATE,), manifest=None, units=(UNIT,)):
    manifest = manifest if manifest is not None else MANIFEST
    return NS.SectionDraft.create(
        task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
        contract_version="cv-1", contract_fingerprint="cf-1", producer_kind="topic_harness",
        writer_policy_version="wp-1", prompt_version="pack_section_writer_proposals_v1",
        model_policy="stub", authority_container_ids=("pack-1",), material_manifest=manifest,
        material_dispositions=(NS.WriterMaterialProcessingDisposition.create(
            manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint(),
            pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
            material_content_fingerprint=FP, processed=True, usage="used",
            support_usages=tuple(p.proposed_support_id for p in proposals), reason_code=None,
            reason_proof=None, writer_policy_version="wp-1"),),
        claim_candidates=tuple(candidates), narrative_draft_units=tuple(units),
        proposed_support_refs=tuple(sorted(proposals, key=lambda p: p.proposed_support_id)),
        unresolved_ids=(), unresolved_projections=(), coverage_summary={},
        conflict_projections=(), not_found_projections=(), dependency_fingerprint="dep-1")


class StubClient:
    """结构化 stub：记录每次调用的身份，返回可编程的判定文本。"""

    def __init__(self, text: str, *, status: str = "ok", model: str | None = None,
                 prompt_version: str | None = None):
        self.calls: list[dict] = []
        self.text = text
        self.status = status
        self.model = model
        self.prompt_version = prompt_version

    def evaluate(self, *, messages, system, prompt_version, model_policy):
        self.calls.append({"messages": messages, "system": system,
                           "prompt_version": prompt_version, "model_policy": model_policy})
        return PW.NarrationResult(
            text=self.text, call_id=f"call-{len(self.calls)}",
            model=self.model or PW.resolve_model_policy(model_policy),
            prompt_version=self.prompt_version or prompt_version, status=self.status,
            error="" if self.status == "ok" else "transport down")


ENTAILED = '{"verdict": "entailed", "reason_code": null, "rationale": "权威逐字给出"}'
REJECTED = '{"verdict": "rejected", "reason_code": "unsupported_specificity", "rationale": "过细"}'


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

    def expect_raises(msg: str, fn, exc, *, needle: str = "") -> None:
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

    scan = _scan(PACK_FACT)
    draft = _draft([_proposal(CANDIDATE), _context_proposal()])
    decisions = CBG.decide_draft_bindings(draft, scan)
    by_kind = {d.subject_kind: d for d in decisions}
    check(len(decisions) == 2 and all(d.result == "pass" for d in decisions),
          "前置条件：候选与草稿单元的聚合决定都必须通过")

    # ============================================================ §1 factual 束只含 factual 边
    bundles = CEE.factual_candidate_revisions(draft, scan)
    check(len(bundles) == 1 and bundles[0].candidate.candidate_id == CANDIDATE.candidate_id,
          "factual candidate 束必须只含候选（context 单元不产生束）")
    bundle = bundles[0]
    check(bundle.unresolved_proposal_ids == (),
          "通过机械门的束不得有未解析的支撑边")
    check(len(bundle.authority_facts) == 1 and bundle.authority_facts[0].fact_id == "f-1",
          "路径 A 边必须解析成真实权威事实条目（本门的唯一授权事实来源）")
    check(bundle.materials == (),
          "路径 A 边不解析成 Topic material（它由权威事实自己的材料引用承载）")
    check(bundle.authority_fact_keys() == (CEE.authority_fact_key("topic_pack", "pack-1", "f-1"),),
          "authority_fact_keys 必须与 FND 同形状（kind/container/authority-specific fact）")
    check(CEE.authority_fact_key("topic_pack", "pack-1", "f-1")
          != CEE.authority_fact_key("topic_pack", "pack-2", "f-1"),
          "裸 fact id 跨容器必须得到不同键（不得把两个容器的同一 fact id 折成一个）")
    expect_raises("空的 authority_fact_key 分量必须拒",
                  lambda: CEE.authority_fact_key("topic_pack", "", "f-1"),
                  CEE.ClaimEntailmentError)

    # ============================================================ §5 context 不进入语义门
    expect_raises("context subject 不得进入语义门（类型层拒）",
                  lambda: CEE.evaluate_entailment(
                      CEE.FactualCandidateRevision(
                          candidate=CANDIDATE, proposals=(_context_proposal(),),
                          authority_facts=(), materials=()),
                      by_kind["claim_candidate"], manifest=MANIFEST,
                      llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError)
    stub_ctx = StubClient(ENTAILED)
    CEE.evaluate_entailments(bundles, decisions, manifest=MANIFEST, llm_client=stub_ctx,
                             model_policy="stub")
    check(len(stub_ctx.calls) == 1,
          f"context 单元不得产生任何调用（实测 {len(stub_ctx.calls)} 次）")

    # ============================================================ §2 恰好一条决定
    stub = StubClient(ENTAILED)
    ent = CEE.evaluate_entailments(bundles, decisions, manifest=MANIFEST, llm_client=stub,
                                   model_policy="stub")
    check(len(ent) == 1 and len(stub.calls) == 1,
          "一个通过机械门的 factual candidate revision → 恰好一条决定、至多一次调用")
    decision = ent[0]
    check(decision.verdict == "entailed" and decision.reason_code is None,
          "entailed 决定不得携带原因码")
    check(decision.claim_candidate_id == CANDIDATE.candidate_id
          and decision.draft_revision == REVISION,
          "决定必须绑定 candidate revision")
    check(decision.binding_decision_id == by_kind["claim_candidate"].binding_decision_id
          and decision.support_set_digest == by_kind["claim_candidate"].support_set_digest
          and decision.support_set_digest == bundle.support_set_digest(MANIFEST),
          "决定必须绑定**同一**通过的 aggregate 决定与同一 support-set digest")
    check(decision.prompt_version == CEE.CLAIM_ENTAILMENT_PROMPT_VERSION
          and decision.rubric_version == CEE.CLAIM_ENTAILMENT_RULES_VERSION
          and decision.model_policy == "stub" and decision.call_id == "call-1",
          "决定必须绑定 prompt 版本、rubric 版本、model policy 与 call id（trace 不得缺）")
    check(decision.authorization_path == "path_a_prevalidated",
          "决定必须记录主导授权路径")
    check(CEE.CLAIM_ENTAILMENT_RULES_VERSION == "cer-4"
          and CEE.CLAIM_ENTAILMENT_PROMPT_VERSION == "claim_entailment_evaluator_v4@cer-4",
          "rubric / prompt 版本字面量必须是 cer-4 与 claim_entailment_evaluator_v4@cer-4"
          "（§三：新增 non_atomic_claim 与「原子性先于蕴含」；⑤：新增镜像分支；"
          "①：镜像读数改为标点归一读视图）")
    check(stub.calls[0]["prompt_version"] == CEE.CLAIM_ENTAILMENT_PROMPT_VERSION
          and stub.calls[0]["model_policy"] == "stub",
          "调用必须使用登记的 prompt 版本与 model policy")

    # ============================================================ §40 aggregate=fail → 零决定零调用
    failed_draft = _draft([_proposal(CANDIDATE, fact_id="f-ghost"), _context_proposal()])
    failed_decisions = CBG.decide_draft_bindings(failed_draft, scan)
    failed_bundle = CEE.factual_candidate_revisions(failed_draft, scan)
    check(len(failed_bundle) == 1
          and failed_bundle[0].unresolved_proposal_ids
          == (_proposal(CANDIDATE, fact_id="f-ghost").proposed_support_id,),
          "解析不到来源的边必须被记成 unresolved（不得为它猜一个来源，也不得整链吞掉）")
    stub_fail = StubClient(ENTAILED)
    ent_fail = CEE.evaluate_entailments(failed_bundle, failed_decisions, manifest=MANIFEST,
                                        llm_client=stub_fail, model_policy="stub")
    check(ent_fail == () and stub_fail.calls == [],
          "aggregate=fail 的候选必须零 entailment 决定、零 LLM 调用"
          "（失败 aggregate 自身就是 typed audit）")
    expect_raises("aggregate 未通过时单条核验必须拒",
                  lambda: CEE.evaluate_entailment(
                      failed_bundle[0], failed_decisions[0], manifest=MANIFEST,
                      llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="未通过")
    # 「aggregate 已 fail 却仍产生 entailment 决定」在 wire 上**不可表达**：同一构造器禁止
    # 「逐边有失败却 result=pass」。因此这条反例的正确落点是构造层，而不是语义门。
    expect_raises("逐边失败却自报 result=pass 的决定必须被 wire 构造器拒",
                  lambda: _swap_result(failed_decisions[0]),
                  NS.NarrativeSchemaError, needle="必须 fail")
    # 真正的门侧断言：通过的决定配到含未解析边的束 → 拒（通过的边必然可解析，这里不一致）。
    expect_raises(
        "通过的决定配到含未解析边的束必须拒",
        lambda: CEE.evaluate_entailments(
            failed_bundle, (_relabel_edges_pass(failed_decisions[0]),),
            manifest=MANIFEST, llm_client=StubClient(ENTAILED), model_policy="stub"),
        CEE.ClaimEntailmentError, needle="解析不到来源")

    # ============================================================ §27/§29 cardinality
    good_decisions = list(decisions)
    expect_raises("同一 candidate revision 出现两次 aggregate 决定必须拒",
                  lambda: CEE.evaluate_entailments(
                      bundles, good_decisions + [by_kind["claim_candidate"]],
                      manifest=MANIFEST, llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="必须恰有一条")
    expect_raises("零条 aggregate 决定必须拒",
                  lambda: CEE.evaluate_entailments(
                      bundles, [d for d in good_decisions if d.subject_kind != "claim_candidate"],
                      manifest=MANIFEST, llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="配到 0 条")
    expect_raises("同一 candidate revision 在输入束里出现两次必须拒",
                  lambda: CEE.evaluate_entailments(
                      (bundle, bundle), good_decisions, manifest=MANIFEST,
                      llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="出现多次")
    # 一条决定指向另一候选 → 不得被静默丢掉。
    other_draft = _draft([_proposal(CANDIDATE), _proposal(CANDIDATE_2),
                          _context_proposal()], candidates=(CANDIDATE, CANDIDATE_2))
    other_decisions = CBG.decide_draft_bindings(other_draft, scan)
    expect_raises("aggregate 决定的 subject 不在本批候选束里必须拒（不得静默丢掉）",
                  lambda: CEE.evaluate_entailments(
                      bundles, other_decisions, manifest=MANIFEST,
                      llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="不在本批")
    # digest 不一致：一条**自报**另一 digest 的决定不得被接受。
    tampered = _relabel_digest(by_kind["claim_candidate"])
    expect_raises("aggregate digest 与本地重算不一致必须拒（不得伪造决定）",
                  lambda: CEE.evaluate_entailment(
                      bundle, tampered, manifest=MANIFEST, llm_client=StubClient(ENTAILED),
                      model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="digest 不一致")
    # 决定与候选不是同一 subject（跨候选借证）。
    expect_raises("aggregate 决定与候选 revision 不是同一 subject 必须拒",
                  lambda: CEE.evaluate_entailment(
                      bundle, decisions[0].__class__.create(
                          **{**decisions[0].identity_body(),
                             "subject_id": CANDIDATE_2.candidate_id}),
                      manifest=MANIFEST, llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError)
    # manifest 不是同一份：manifest 身份是 digest 的输入，因此换 manifest 一定在 digest 重算
    # 那一步就被拒——这就是「digest 必须在**同一** manifest 上重算」的实际含义（本门的
    # `manifest` 因此是必填关键字，不是可选上下文）。
    other_manifest = NS.WriterMaterialManifest.create(members=(
        _manifest_member(content_fingerprint="c" * 64),))
    check(other_manifest.manifest_id != MANIFEST.manifest_id
          and other_manifest.fingerprint() != MANIFEST.fingerprint(),
          "前置条件：另一份 manifest 的身份与指纹都必须不同")
    expect_raises("换 manifest 必须在 digest 重算处被拒（拒绝运行，不伪造决定）",
                  lambda: CEE.evaluate_entailment(
                      bundle, by_kind["claim_candidate"], manifest=other_manifest,
                      llm_client=StubClient(ENTAILED), model_policy="stub"),
                  CEE.ClaimEntailmentError, needle="digest 不一致")

    # ============================================================ §42 prompt 资产与调用绑定
    check(CEE.CLAIM_ENTAILMENT_PROMPT_ASSET == "claim_entailment_evaluator_v4",
          "语义门必须登记资产名 claim_entailment_evaluator_v4（v1/v2/v3 都不原位修改，只被取代）")
    text = llm.load_prompt(CEE.CLAIM_ENTAILMENT_PROMPT_ASSET)
    check(CEE.verify_claim_entailment_prompt_asset(text) == text,
          "登记资产必须通过验签（自报身份 + 正文指纹都对得上）")
    expect_raises("换一份正文（资产名/修订号照抄）必须拒",
                  lambda: CEE.verify_claim_entailment_prompt_asset(text + "\n"),
                  CEE.ClaimEntailmentError, needle="正文指纹")
    expect_raises("资产自报另一身份必须拒",
                  lambda: CEE.verify_claim_entailment_prompt_asset(
                      "你是核验器（other_asset，revision cer-9）"),
                  CEE.ClaimEntailmentError, needle="自报")
    # 资产漂移时**不得**先调用再拒：换掉唯一加载点，调用次数必须是 0。
    original_loader = CEE.llm.load_prompt
    drift = StubClient(ENTAILED)
    CEE.llm.load_prompt = lambda name: original_loader(name) + "\n漂移一个字节"
    try:
        expect_raises("资产正文漂移必须在调用前拒",
                      lambda: CEE.evaluate_entailment(
                          bundle, by_kind["claim_candidate"], manifest=MANIFEST,
                          llm_client=drift, model_policy="stub"),
                      CEE.ClaimEntailmentError, needle="正文指纹")
        check(drift.calls == [],
              "资产漂移时不得消耗任何调用（拒绝必须发生在调用之前）")
    finally:
        CEE.llm.load_prompt = original_loader
    # 决定与实际调用必须绑定同一 prompt 版本 / 模型。
    expect_raises("实际调用用了另一个 prompt 版本必须拒",
                  lambda: CEE.evaluate_entailment(
                      bundle, by_kind["claim_candidate"], manifest=MANIFEST, model_policy="stub",
                      llm_client=StubClient(ENTAILED, prompt_version="claim_entailment_evaluator_v1@cer-9")),
                  CEE.ClaimEntailmentError, needle="prompt 版本")
    expect_raises("实际调用用了另一个模型必须拒",
                  lambda: CEE.evaluate_entailment(
                      bundle, by_kind["claim_candidate"], manifest=MANIFEST, model_policy="stub",
                      llm_client=StubClient(ENTAILED, model="some-other-model")),
                  CEE.ClaimEntailmentError, needle="模型")
    expect_raises("model policy 越界必须拒",
                  lambda: CEE.evaluate_entailment(
                      bundle, by_kind["claim_candidate"], manifest=MANIFEST,
                      llm_client=StubClient(ENTAILED), model_policy="not-a-policy"),
                  CEE.ClaimEntailmentError, needle="model policy")

    # ============================================================ §三 原子性先于蕴含（`cer-2`）
    # 原子性是**语义**判据，不是标点规则。下面把两件事分别钉住：
    #   (a) 「两个各自可判断真假的断言写成一条候选」必须被拒为 typed `non_atomic_claim`，
    #       且该码在决定 wire 上可往返；
    #   (b) 拒绝**不是**「出现逗号就拒绝」——同一对被拒的断言，去掉逗号仍然被拒；反过来，
    #       带逗号但只有一个断言的文本必须正常通过。结论只由模型的 typed 判定决定，
    #       本门（与 `NS`）里没有任何按标点做判断的规则。
    check("non_atomic_claim" in NS.ENTAILMENT_REJECTION_REASONS,
          "原子性拒绝码必须是封闭原因集里的一员（typed 码，不是自由字符串）")
    check(NS.ENTAILMENT_REJECTION_REASONS[0] == "non_atomic_claim",
          "原因集的顺序必须体现「原子性先于蕴含」（原子性是蕴含判断的前提）")
    _cee_src = (REPO / "sections" / "claim_entailment_evaluator.py").read_text(encoding="utf-8")
    _cee_tree = ast.parse(_cee_src)
    _cee_literals = [n.value for n in ast.walk(_cee_tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    check(not any("non_atomic_claim" in s for s in _cee_literals),
          "语义门不得自己产生原子性码：它只能来自封闭原因集 + 模型的 typed 判定")
    _punctuation = {"，", ",", "；", ";", "。", "、", "."}
    _punct_compare = [
        n for n in ast.walk(_cee_tree) if isinstance(n, ast.Compare)
        and any(isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value
                and set(c.value) <= _punctuation for c in n.comparators)]
    check(not _punct_compare,
          f"语义门不得存在「按标点字符串判断」的比较（实测 {len(_punct_compare)} 处）："
          "「出现逗号就拒绝」是被明确禁止的捷径")

    NON_ATOMIC = ('{"verdict": "rejected", "reason_code": "non_atomic_claim", '
                  '"rationale": "两个分句各说一件事，不能一次判断真假"}')
    TWO_ASSERTIONS_COMMA = "经营环境未发生重大变化，境外客户回款情况正常。"
    TWO_ASSERTIONS_NO_COMMA = "经营环境未发生重大变化。境外客户回款情况正常。"
    ONE_ASSERTION_COMMA = "主营产品包括动力电池与储能电池，覆盖乘用车与电网侧两类场景。"

    def _entail(text: str, stub_text: str):
        candidate = NS.ClaimCandidate.create(
            draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
            report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
            claim_text=text, fact_type="descriptive")
        one = _draft([_proposal(candidate)], candidates=(candidate,))
        out = CEE.evaluate_entailments(
            CEE.factual_candidate_revisions(one, scan), CBG.decide_draft_bindings(one, scan),
            manifest=MANIFEST, llm_client=StubClient(stub_text), model_policy="stub")
        check(len(out) == 1, f"原子性用例必须恰好产出一条决定（{text!r}）")
        return out[0]

    d_atomic = _entail(TWO_ASSERTIONS_COMMA, NON_ATOMIC)
    check(d_atomic.verdict == "rejected" and d_atomic.reason_code == "non_atomic_claim"
          and NS.ClaimEntailmentDecision.from_dict(d_atomic.to_dict()).to_dict()
          == d_atomic.to_dict(),
          "一条候选里两个各自可判断真假的断言必须被拒为 non_atomic_claim（且该码可往返）")
    d_no_comma = _entail(TWO_ASSERTIONS_NO_COMMA, NON_ATOMIC)
    check(d_no_comma.verdict == "rejected" and d_no_comma.reason_code == "non_atomic_claim"
          and "," not in TWO_ASSERTIONS_NO_COMMA and "，" not in TWO_ASSERTIONS_NO_COMMA,
          "一个标点都没有的两个断言同样必须被拒（原子性不是「有标点就拒」）")
    d_one_assertion = _entail(ONE_ASSERTION_COMMA, ENTAILED)
    check("，" in ONE_ASSERTION_COMMA and d_one_assertion.verdict == "entailed"
          and d_one_assertion.reason_code is None,
          "带逗号但只有一个断言（并列名词短语）必须正常通过：逗号本身不是拒绝理由")

    # ============================================================ ⑤ 逐字镜像分支（`cer-3`）
    # 现场（真实 run r4）：同形的四个期间候选（利息保障倍数 2023 / 2024 / 2025 / 2026Q1）都走
    # 路径 A，文本**逐字等于单条权威事实自身的文本**（含权威自己附上的口径限定语
    # 「代理口径（…）」），其中 2024 被判原子性拒绝码、其余判 entailed。分支只改这一种形状：
    # 候选逐字镜像**恰好一条**权威事实时不判原子性拒绝码；0 条与多条一律**回到**语义判据。
    #
    # 这里先钉住**机械读数**（字符串相等，不是语义判断），再钉住分支的两侧：
    def _fact(fact_id: str, text: str, period: str = "2024"):
        return dataclasses.replace(PACK_FACT, fact_id=fact_id, text=text, period=period)

    def _candidate(text: str):
        return NS.ClaimCandidate.create(
            draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
            report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
            claim_text=text, fact_type="metric")

    def _mirror_case(candidate, facts, stub_text: str | None = None):
        """一条候选 + 它引用的权威事实 ⇒ （模型输入里的机械读数, 决定或 None）。

        `stub_text=None` 时只取读数（不发起调用）；否则用同一个 stub 走完整门。
        """
        probe = _scan(*facts)
        one = _draft([_proposal(candidate, fact_id=f.fact_id) for f in facts],
                     candidates=(candidate,))
        bundle_one = CEE.factual_candidate_revisions(one, probe)[0]
        messages, _ = CEE.build_entailment_messages(
            bundle_one, CBG.decide_draft_bindings(one, probe)[0], MANIFEST)
        readout = json.loads(messages[0]["content"])["authority_fact_mirror"]
        if stub_text is None:
            return readout, None
        decisions = CEE.evaluate_entailments(
            (bundle_one,), CBG.decide_draft_bindings(one, probe),
            manifest=MANIFEST, llm_client=StubClient(stub_text), model_policy="stub")
        check(len(decisions) == 1, f"镜像用例必须恰好产出一条决定（{candidate.claim_text!r}）")
        return readout, decisions[0]

    # 正例：四个同形期间，每个候选逐字镜像**自己那条**权威事实（形状与现场一致：
    # 数值 + 权威自己附上的代理口径限定语，同一句）。
    _period_shapes = (("2023", "-9.94"), ("2024", "-14.29"), ("2025", "-10.28"),
                      ("2026年1-3月", "429.48"))
    for _period, _value in _period_shapes:
        _text = f"{_period}的利息保障倍数为{_value}。代理口径（PROXY_FINANCE_EXPENSES）。"
        _hit, _d = _mirror_case(_candidate(_text),
                                (_fact("f-1", _text, _period),), ENTAILED)
        check(_hit["match_count"] == 1 and _hit["verbatim_mirror_of_fact_id"] == "f-1"
              and _hit["verbatim_mirror_of_authority_kind"] == "topic_pack"
              and _hit["verbatim_mirror_of_container_id"] == "pack-1",
              f"逐字镜像单条权威事实时必须报出 match_count=1 与那一条的身份（{_period}）")
        # 镜像分支成立时，模型判 entailed 必须**如实**落成 entailed（门不因镜像而自行改判，
        # 也不因候选带口径限定语就替模型拒绝）。
        check(_d is not None and _d.verdict == "entailed" and _d.reason_code is None,
              f"镜像分支下模型判 entailed 必须如实落成 entailed（{_period}）")

    # 反例一：候选与**任何**权威事实都不逐字相等 ⇒ match_count=0，且**不**指向任何一条；
    # 同一段 stub 文本在这种形状下仍如实落成原子性拒绝码（分支不得外溢到非镜像候选）。
    _miss, _d_miss = _mirror_case(_candidate("2024年利息保障倍数为-14.29。"),
                                  (PACK_FACT,), NON_ATOMIC)
    check(_miss["match_count"] == 0 and _miss["verbatim_mirror_of_fact_id"] is None,
          "候选不是任何权威事实的逐字镜像时 match_count=0，且不指向任何一条")
    check(_d_miss is not None and _d_miss.verdict == "rejected"
          and _d_miss.reason_code == "non_atomic_claim",
          "非镜像候选不得廉价搭上镜像分支：同形文本仍按一般判据落成原子性拒绝码")

    # 反例二：候选等于**两条**相同文本的事实 ⇒ match_count>1、**不给** fact_id
    # （不唯一即不触发分支，否则「碰巧等于某条」会变成一条可被利用的旁路）。
    _dup_text = "公司2024年营业收入为1234.56亿元。"
    _dup, _ = _mirror_case(_candidate(_dup_text),
                           (_fact("f-a", _dup_text), _fact("f-b", _dup_text)))
    check(_dup["match_count"] == 2 and _dup["verbatim_mirror_of_fact_id"] is None
          and _dup["verbatim_mirror_of_authority_kind"] is None,
          "候选与多条权威事实逐字相同 ⇒ match_count>1 且不指向唯一一条（分支不成立）")

    # 反例三：把**两条**权威事实的文本拼成一条候选 ⇒ 不是任何单条事实的镜像，
    # 仍然回到一般语义判据（镜像分支不得被当成「材料里有的都能拼」的通行证）。
    _joined = _fact("f-a", "公司2024年营业收入为1234.56亿元。")
    _joined2 = _fact("f-b", "公司2024年营业收入同比下降。")
    _two = _candidate("公司2024年营业收入为1234.56亿元。公司2024年营业收入同比下降。")
    _mixed, _d_mixed = _mirror_case(_two, (_joined, _joined2), NON_ATOMIC)
    check(_mixed["match_count"] == 0,
          "把两条权威事实拼成一条候选时 match_count=0（镜像分支不成立）")
    check(_d_mixed is not None and _d_mixed.verdict == "rejected"
          and _d_mixed.reason_code == "non_atomic_claim",
          "拼起来的两条断言仍被拒为原子性拒绝码（分支只免掉逐字镜像那一种形状）")

    # ============================================================ ① 镜像读数的标点归一（`cer-4`）
    # 现场（真实 run r6）：写作侧契约要求候选**不带句末标点**，于是财务节 24 条候选的
    # `match_count` 全是 0 —— `cer-3` 的豁免一次都没生效，同一形状（2023 与 2025 的利息
    # 保障倍数候选逐字同形）被判出相反结论，必需事实无 Claim 可引、整节中止。下面的正例
    # 就是 r6 现场那两条候选的真实形状；反例钉住「错误数值 / 错误期间 / 遗漏代理口径限定语 /
    # 小数点被换掉」仍然不相等 —— 归一**只**动句读标点，实词一个都不动。
    for _period, _value in _period_shapes:
        _authority = f"{_period}的利息保障倍数为{_value}。代理口径（PROXY_FINANCE_EXPENSES）。"
        _written = f"{_period}的利息保障倍数为{_value}，代理口径（PROXY_FINANCE_EXPENSES）"
        _hit_p, _d_p = _mirror_case(_candidate(_written), (_fact("f-1", _authority, _period),),
                                    ENTAILED)
        check(_hit_p["match_count"] == 1 and _hit_p["verbatim_mirror_of_fact_id"] == "f-1",
              f"只差句读标点（权威 `。` → 候选 `，`、句末标点被契约要求省略）仍是镜像（{_period}）")
        check(_d_p is not None and _d_p.verdict == "entailed",
              f"镜像分支下模型判 entailed 必须如实落成 entailed（{_period} 标点变体）")

    # 单句权威事实：候选只是按契约省掉句末标点 —— 同样必须算镜像（r6 里这类共 20 条）。
    _one_authority = "2023年末的流动比率为1.57。"
    _one_hit, _d_one = _mirror_case(_candidate("2023年末的流动比率为1.57"),
                                    (_fact("f-1", _one_authority, "2023"),), ENTAILED)
    check(_one_hit["match_count"] == 1 and _d_one is not None and _d_one.verdict == "entailed",
          "单句权威事实 + 候选省掉句末标点必须仍是镜像（否则真实输出上豁免恒不生效）")

    # 反例：归一**不**放宽实词。每一种都必须回到一般语义判据（`match_count=0`）。
    _qualifier = "2023年度的利息保障倍数为-9.94。代理口径（PROXY_FINANCE_EXPENSES）。"
    _fact_only = _fact("f-1", _qualifier, "2023")
    for _label, _bad in (
            ("遗漏代理口径限定语", "2023年度的利息保障倍数为-9.94"),
            ("数值被改（-9.94 → -9.95）", "2023年度的利息保障倍数为-9.95，代理口径（PROXY_FINANCE_EXPENSES）"),
            ("期间被改（2023 → 2024）", "2024年度的利息保障倍数为-9.94，代理口径（PROXY_FINANCE_EXPENSES）"),
            ("小数点被换成逗号（-9.94 → -9，94）", "2023年度的利息保障倍数为-9，94，代理口径（PROXY_FINANCE_EXPENSES）"),
            ("凭空补出限定（加「同比」）", "2023年度的利息保障倍数为-9.94，代理口径（PROXY_FINANCE_EXPENSES），同比下降"),
            ("把两条事实的文本拼起来",
             "2023年度的利息保障倍数为-9.94。代理口径（PROXY_FINANCE_EXPENSES）。2023年末的流动比率为1.57。")):
        _facts = (_fact_only, _fact("f-2", "2023年末的流动比率为1.57。", "2023"))
        _bad_hit, _ = _mirror_case(_candidate(_bad), _facts)
        check(_bad_hit["match_count"] == 0 and _bad_hit["verbatim_mirror_of_fact_id"] is None,
              f"{_label}时必须不是镜像（标点归一只动句读标点，不动实词）：{_bad!r}")

    # 读数只进输入面：`match_count=1` 时门**不**替模型改判，也不因镜像而自行放行——
    # 模型仍输出原子性拒绝码时，决定必须如实落成 rejected（豁免写在资产里，不写在读数里）。
    _mirror_but_rejected, _d_mirror_rejected = _mirror_case(
        _candidate("2023年末的流动比率为1.57"), (_fact("f-1", _one_authority, "2023"),), NON_ATOMIC)
    check(_mirror_but_rejected["match_count"] == 1
          and _d_mirror_rejected is not None
          and _d_mirror_rejected.verdict == "rejected"
          and _d_mirror_rejected.reason_code == "non_atomic_claim",
          "镜像是输入面的读数：模型判 rejected 时决定必须如实落成 rejected（门不自行改判）")

    # ============================================================ §3 只降级不升级、不伪造
    rejected = CEE.evaluate_entailment(
        bundle, by_kind["claim_candidate"], manifest=MANIFEST, model_policy="stub",
        llm_client=StubClient(REJECTED))
    check(rejected.verdict == "rejected" and rejected.reason_code == "unsupported_specificity",
          "stub 返回 rejected 时决定必须如实记录（不得被升级成 entailed）")
    for label, text_out, needle in (
            ("原因码越界", '{"verdict": "rejected", "reason_code": "whatever"}', "reason_code"),
            ("entailed 带原因码", '{"verdict": "entailed", "reason_code": "unsupported_specificity"}',
             "不得携带"),
            ("verdict 越界", '{"verdict": "maybe", "reason_code": null}', "verdict"),
            ("非 JSON", "我觉得可以", "不是合法 JSON"),
            ("JSON 数组", "[1, 2, 3]", "必须是 JSON 对象"),
            ("多余文本包裹", '结果如下 {"verdict": "entailed", "reason_code": null}', "不是合法 JSON")):
        expect_raises(f"模型输出{label}必须 fail-closed 抛出（不伪造 rejected）",
                      lambda t=text_out: CEE.evaluate_entailment(
                          bundle, by_kind["claim_candidate"], manifest=MANIFEST,
                          llm_client=StubClient(t), model_policy="stub"),
                      CEE.ClaimEntailmentError, needle=needle)
    # 整整一层 markdown 围栏是唯一容忍的包装（模型常见输出），围栏内的越界仍然拒。
    fenced = CEE.evaluate_entailment(
        bundle, by_kind["claim_candidate"], manifest=MANIFEST, model_policy="stub",
        llm_client=StubClient(f"```json\n{ENTAILED}\n```"))
    check(fenced.verdict == "entailed",
          "整整一层 ```json 围栏必须被接受（否则真实调用会被格式噪声整批拒掉）")
    expect_raises("传输失败必须抛出（封闭原因码里没有『调用失败』）",
                  lambda: CEE.evaluate_entailment(
                      bundle, by_kind["claim_candidate"], manifest=MANIFEST, model_policy="stub",
                      llm_client=StubClient("", status="error")),
                  CEE.ClaimEntailmentError, needle="不伪造决定")
    expect_raises("client 不返回结构化结果必须拒",
                  lambda: CEE.evaluate_entailment(
                      bundle, by_kind["claim_candidate"], manifest=MANIFEST, model_policy="stub",
                      llm_client=_TextOnlyClient()),
                  CEE.ClaimEntailmentError, needle="NarrationResult")

    # ============================================================ §4 至多一次调用（含观测回调）
    seen: list[str] = []
    stub_once = StubClient(ENTAILED)
    CEE.evaluate_entailments(bundles, decisions, manifest=MANIFEST, llm_client=stub_once,
                             model_policy="stub", on_call=lambda b: seen.append(b.candidate.candidate_id))
    check(len(stub_once.calls) == 1 and seen == [CANDIDATE.candidate_id],
          "每个通过机械门的候选至多一次调用（观测回调不得改变调用次数）")

    # ============================================================ §五 公开入口不得吃自报正文
    # 真实链（canonical company worker）会给门注入一份完整的 `wmctx-1`。§五钉的是**公开
    # evaluator 入口**：同 `member_ref`、任意正文、自洽重算的读视图指纹，必须在模型调用前被拒。
    # 下面用一组真实（自洽）的正文/成员夹具把两件事分开证明：
    #   (a) 正例：真实上下文 + 逐字相符的成员 → 正常通过；
    #   (b) 反例：同成员的正文被换成任意文本（伪造者会顺手重算指纹，所以指纹是自洽的）→
    #       构造期即拒，不消耗调用；
    #   (c) 结构反例：`resolved_materials` 是派生字段（`init=False`），注入在类型层不可表达。
    b_member, b_context = _path_b_material()
    b_manifest = NS.WriterMaterialManifest.create(members=(b_member,))
    b_revision = _revision_for(b_manifest)
    b_candidate = NS.ClaimCandidate.create(
        draft_revision=b_revision, task_id="t-1", section_id="company", company_id="c-1",
        report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
        claim_text="公司主营业务覆盖工业产品的研发、生产与销售。", fact_type="descriptive")
    b_proposal = _path_b_proposal(b_candidate, b_member, revision=b_revision,
                                  manifest=b_manifest)
    b_draft = _draft([b_proposal], candidates=(b_candidate,), manifest=b_manifest, units=())
    b_bundles = CEE.factual_candidate_revisions(b_draft, _scan(), manifest=b_manifest,
                                                material_context=b_context)
    check(len(b_bundles) == 1 and len(b_bundles[0].resolved_materials) == 1
          and b_bundles[0].resolved_materials[0].reading_view == _REAL_READING,
          "正例：路径 B 边解析出的正文必须就是真实上下文里的那一份（基线，反例不是恒失败）")
    b_decisions = CBG.decide_draft_bindings(b_draft, _scan(), manifest=b_manifest,
                                            material_context=b_context)
    stub_b = StubClient(ENTAILED)
    b_ent = CEE.evaluate_entailments(b_bundles, b_decisions, manifest=b_manifest,
                                     llm_client=stub_b, model_policy="stub")
    check(len(b_ent) == 1 and len(stub_b.calls) == 1,
          "正例：真实上下文上的路径 B 候选正常产出恰好一条决定、恰好一次调用")
    # (b) 同 member_ref、任意正文、自洽重算指纹 → 构造期拒（模型调用前）。
    expect_raises("同 member_ref 的伪造正文（自洽重算指纹）必须拒",
                  lambda: CEE.factual_candidate_revisions(
                      b_draft, _scan(), manifest=b_manifest,
                      material_context=_forged_context()),
                  CEE.ClaimEntailmentError, needle="复核")
    stub_forged = StubClient(ENTAILED)
    expect_raises(
        "手工构造的伪造束即使绕过构造期，也必须在模型调用前拒",
        lambda: CEE.evaluate_entailments(
            (_forged_bundle(b_proposal, b_member),), b_decisions, manifest=b_manifest,
            llm_client=stub_forged, model_policy="stub"),
        CEE.ClaimEntailmentError, needle="复核")
    # (b2) 更强的一层：伪造者连成员一起自造（成员 ↔ 伪造上下文**自洽**，构造期拦不住），
    #      于是必须在调用前用「成员是否属于本次 exact manifest」把他钉回去。
    stub_forged2 = StubClient(ENTAILED)
    expect_raises(
        "自造成员 + 自洽伪造上下文（构造期自洽）必须在模型调用前被钉回 manifest",
        lambda: CEE.evaluate_entailments(
            (_self_consistent_forged_bundle(b_candidate, b_revision, b_manifest),), b_decisions,
            manifest=b_manifest, llm_client=stub_forged2, model_policy="stub"),
        CEE.ClaimEntailmentError, needle="本次 exact manifest")
    check(stub_forged2.calls == [],
          "伪造正文的三个反例都不得消耗任何一次模型调用")
    # (c) 派生字段不可注入：`resolved_materials` 不在构造签名里。
    _fcr_fields = {f.name: f for f in dataclasses.fields(CEE.FactualCandidateRevision)}
    check("resolved_materials" in _fcr_fields
          and not _fcr_fields["resolved_materials"].init
          and _fcr_fields["material_context"].init,
          "正文是**派生**字段（init=False），唯一入口是 material_context："
          "「自报正文」在类型层不可表达")
    expect_raises("向构造器注入自报正文必须不可表达",
                  lambda: CEE.FactualCandidateRevision(
                      candidate=b_candidate, proposals=(b_proposal,),
                      authority_facts=(), materials=(b_member,),
                      resolved_materials=(_forged_reading(),)),
                  TypeError)


    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


class _TextOnlyClient:
    """返回裸文本的 client（丢掉调用元数据）——不得被接受。"""

    def evaluate(self, *, messages, system, prompt_version, model_policy):
        return "entailed"


def _swap_result(decision):
    """把一条**自报失败**的决定改成 pass（逐边仍记 fail）：digest 不变，结论变了。"""
    body = dict(decision.identity_body())
    body["result"] = "pass"
    body["subject_kind"] = "claim_candidate"
    return NS.ClaimBindingDecision.create(**body)


def _relabel_edges_pass(decision):
    """把一条决定的逐边结果整体改写成 pass（含原因码）：用来构造「自报通过、边其实解析不到」。"""
    body = dict(decision.identity_body())
    body["edge_results"] = [
        {**e.to_dict(), "result": "pass", "reason_code": None} for e in decision.edge_results]
    body["result"] = "pass"
    return NS.ClaimBindingDecision.create(**body)


def _relabel_digest(decision):
    """一条自报另一 support-set digest 的决定（digest 不是身份的一部分，必须被重算检出）。"""
    body = dict(decision.identity_body())
    body["support_set_digest"] = "0" * 64
    return NS.ClaimBindingDecision.create(**body)


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

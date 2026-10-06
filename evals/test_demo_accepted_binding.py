"""Eval: M930-3C P10 门后 accepted 支撑边（§16.7.1 P10；§16.10 第 6/7/10/19/27/29 项）。

用法: python -m evals.test_demo_accepted_binding

覆盖（每条对应一个具体缺陷）：
* 每个**通过** proposal 各一条 binding；未通过的 proposal、未通过 aggregate 的 subject 都
      不产生 binding（失败 aggregate 自身是 typed audit）；
* §6  factual binding 缺 aggregate 或 entailment 任一决定 → 拒；两条决定必须同 subject /
      同 revision / 同 aggregate / 同 digest；
* §7  context binding 携带 entailment 决定 → 拒（context 不进入语义门）；
* §10 非 topic authority 声明 `path_b_material_derived` → 拒（schema 与门各拒一次）；
* §19 accepted binding 引用**未来**对象：wire 字段集里没有这些键，且 `create` 收到未知键
      必须拒（静默丢弃会让「企图引用未来身份」既不失败也不成形）；
* §21 context binding 携带任何 fact identity → 拒（schema 与门各拒一次）；
* §27 同一 candidate revision 的两条 aggregate 决定 / 零条决定 → 拒；
* §29 一条 aggregate 不得复用于另一 subject；一条决定不得指向另一 subject 的 proposal 集；
* 批量结论必须**完备**：每条 subject revision 要么有 accepted binding、要么有一条显式拒绝
      （机械阶段记逐边原因码、语义阶段记封闭原因码 + entailment 决定 id），不得静默丢弃。

不调 LLM、不联网、不写任何文件。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from sections import accepted_binding as AB
from sections import claim_binding_gate as CBG
from sections import narrative_schema as NS
from sections import pack_writer as PW

FP = "a" * 64


def _scan(*facts):
    return PW.AuthorityScan(facts=tuple(facts), aspect_status={}, aspect_topic={},
                            aspect_impact={}, aspect_blocking={}, aspect_question={},
                            excluded_facts=(), conflicts=(), not_found=(), gaps=(),
                            coverage_counts={})


#: 路径 B / context 边的**闭合**值（§四.1/§四.2）：边上的 payload_ref 与 exact locator 必须与
#: 它声称绑定的 manifest 成员逐字相同。
MEMBER_LOCATOR = NS.char_range_locator("evidence:ev-1", 3, 40)

PACK_FACT = PW.AuthorityFactEntry(
    authority_kind="topic_pack", container_identity="pack-1", fact_id="f-1",
    text="公司2024年营业收入为1234.56亿元。", topic_id="t-1", aspect_ids=("a-1",),
    required=True, fact_type="metric", period="2024", scope="公司", material_id="m-1",
    payload_ref={"object_type": "research_material"}, locator_ref=MEMBER_LOCATOR,
    source_identity="evidence:ev-1", provenance_identity="prov-1", content_fingerprint=FP)

#: 同一 subject 的**第二条**路径 A 支撑边（同一材料成员 `m-1`）：覆盖「逐 subject 多条
#: proposal」这一必然形态——多边是常态（primary + corroborating），而 canonical order 只是
#: 该 subject 的派生视图里的呈现顺序，不是 Draft 自身的顺序。
PACK_FACT_2 = PW.AuthorityFactEntry(
    authority_kind="topic_pack", container_identity="pack-1", fact_id="f-2",
    text="公司2024年营业收入较上年增长12.3%。", topic_id="t-1", aspect_ids=("a-1",),
    required=True, fact_type="metric", period="2024", scope="公司", material_id="m-1",
    payload_ref={"object_type": "research_material"}, locator_ref=MEMBER_LOCATOR,
    source_identity="evidence:ev-1", provenance_identity="prov-1", content_fingerprint=FP)

#: `wmm-2`：成员身份必须携带**可解析的真实引用**（§三 A.3/A.7）。只有 material ID 的成员
#: 现在在构造期就不可表达——「成员存在」与「正文已解析并校验」是同一件事。
PAYLOAD_REF = TS.MaterialPayloadRef(
    object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
    content_hash=FP,
    locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                               section_path="s1", page=3),
    created_dependency_fingerprint="d" * 64).to_dict()

MANIFEST = NS.WriterMaterialManifest.create(members=(
    NS.WriterMaterialManifestEntry.create(
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=PAYLOAD_REF, locator_ref=MEMBER_LOCATOR,
        payload_hash=FP, reading_view_fingerprint=FP),))
MEMBER_PAYLOAD = dict(MANIFEST.entries[0].payload_ref)
REVISION = NS.derive_draft_revision(
    task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
    contract_version="cv-1", contract_fingerprint="cf-1", writer_policy_version="wp-1",
    prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
    manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint())
CANDIDATE = NS.ClaimCandidate.create(
    draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入为1234.56亿元。", fact_type="metric")
UNIT = NS.NarrativeDraftUnit.create(
    draft_revision=REVISION, section_id="company", index=0, unit_kind="paragraph",
    text="本节说明公司经营情况。")


def _factual(fact_id="f-1", **over):
    kw = dict(binding_subject_kind="claim_candidate", binding_subject_id=CANDIDATE.candidate_id,
              draft_revision=REVISION, manifest_id=MANIFEST.manifest_id,
              manifest_fingerprint=MANIFEST.fingerprint(), authority_kind="topic_pack",
              authority_container_id="pack-1", source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="primary", support_semantics="factual",
              authorization_path="path_a_prevalidated", content_fingerprint=FP,
              dependency_fingerprint="dep-1", fact_id=fact_id, material_id="m-1",
              payload_ref={"object_type": "research_material"},
              locator_ref=MEMBER_LOCATOR)
    kw.update({k: v for k, v in over.items() if k != "fact_id"})
    return NS.ProposedSupportRef.create(**kw)


def _context(**over):
    kw = dict(binding_subject_kind="narrative_draft_unit", binding_subject_id=UNIT.draft_unit_id,
              draft_revision=REVISION, manifest_id=MANIFEST.manifest_id,
              manifest_fingerprint=MANIFEST.fingerprint(), authority_kind="topic_pack",
              authority_container_id="pack-1", source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="corroborating",
              support_semantics="context", authorization_path="context_only",
              content_fingerprint=FP, dependency_fingerprint="dep-1", material_id="m-1",
              # §四.2：context 边与路径 B 同一闭合口径。
              payload_ref=dict(MEMBER_PAYLOAD), locator_ref=MEMBER_LOCATOR)
    kw.update(over)
    return NS.ProposedSupportRef.create(**kw)


def _draft(proposals, *, bundle_order: bool = False):
    """一份 Draft。`bundle_order=True` 时 `proposed_support_refs` **按传入顺序**落库。

    Draft 自己的 proposal 顺序是**束的构造顺序**（候选序 × 逐边序），它进 `identity_body()`；
    production 的 `_build_pre_gate_bundle` 就是这样 append 的，逐 subject 的分组因此**不一定**
    是 canonical order。`support_usages` 则按 production（`_derive_material_processing`）取升序，
    与 Draft 的呈现顺序是两条不同的轴。
    """
    ordered = (tuple(proposals) if bundle_order
               else tuple(sorted(proposals, key=lambda p: p.proposed_support_id)))
    return NS.SectionDraft.create(
        task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
        contract_version="cv-1", contract_fingerprint="cf-1", producer_kind="topic_harness",
        writer_policy_version="wp-1", prompt_version="pack_section_writer_proposals_v1",
        model_policy="stub", authority_container_ids=("pack-1",), material_manifest=MANIFEST,
        material_dispositions=(NS.WriterMaterialProcessingDisposition.create(
            manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint(),
            pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
            material_content_fingerprint=FP, processed=True, usage="used",
            support_usages=tuple(sorted(p.proposed_support_id for p in proposals)),
            reason_code=None,
            reason_proof=None, writer_policy_version="wp-1"),),
        claim_candidates=(CANDIDATE,), narrative_draft_units=(UNIT,),
        proposed_support_refs=ordered,
        unresolved_ids=(), unresolved_projections=(), coverage_summary={},
        conflict_projections=(), not_found_projections=(), dependency_fingerprint="dep-1")


def _entailment(decision, *, verdict="entailed", reason_code=None, **over):
    body = dict(prompt_version="claim_entailment_evaluator_v1@cer-1", rubric_version="cer-1",
                model_policy="stub", call_id="call-1")
    body.update(over)
    return NS.ClaimEntailmentDecision.create(
        claim_candidate_id=body.pop("claim_candidate_id", CANDIDATE.candidate_id),
        draft_revision=body.pop("draft_revision", REVISION),
        binding_decision_id=body.pop("binding_decision_id", decision.binding_decision_id),
        support_set_digest=body.pop("support_set_digest", decision.support_set_digest),
        authorization_path=body.pop("authorization_path", "path_a_prevalidated"),
        authority_fact_keys=body.pop("authority_fact_keys",
                                     (("afk-1",),)[0]),
        verdict=verdict, reason_code=reason_code, **body)


def _mutated(proposal, **over):
    clone = copy.copy(proposal)
    for name, value in over.items():
        object.__setattr__(clone, name, value)
    return clone


def forged_digest(proposal) -> str:
    """手工伪造决定时用的 digest：仍走 P8 的**唯一**公式（伪造的是结论，不是算法）。"""
    return CBG.support_set_digest(
        subject_revision=CBG.BindingSubjectRevision.from_subject(UNIT),
        proposal_ids=(proposal.proposed_support_id,),
        proposal_hashes=(proposal.content_hash(),), manifest_id=MANIFEST.manifest_id,
        manifest_fingerprint=MANIFEST.fingerprint())


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
                details.append(f"FAIL {msg}：原因不符（{str(e)[:200]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:200]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    scan = _scan(PACK_FACT)
    p_factual = _factual()
    p_context = _context()
    draft = _draft([p_factual, p_context])
    decisions = {d.subject_kind: d for d in CBG.decide_draft_bindings(draft, scan)}
    sr_claim = CBG.BindingSubjectRevision.from_subject(CANDIDATE)
    sr_unit = CBG.BindingSubjectRevision.from_subject(UNIT)
    check(set(decisions) == {"claim_candidate", "narrative_draft_unit"}
          and all(d.result == "pass" for d in decisions.values()),
          "前置条件：候选与草稿单元的机械决定都必须通过")

    # ============================================================ §1 factual: 两条决定都必需
    ent = _entailment(decisions["claim_candidate"])
    factual_bindings = AB.accept_bindings(sr_claim, [p_factual],
                                          decisions["claim_candidate"], ent)
    check(len(factual_bindings) == 1, "一个通过 proposal → 恰好一条 accepted binding")
    binding = factual_bindings[0]
    check(binding.proposed_support_id == p_factual.proposed_support_id
          and binding.proposal_content_hash == p_factual.content_hash(),
          "binding 必须回指 proposal 及其**内容**哈希（只记 id 会让改内容不算改身份）")
    check(binding.binding_decision_id == decisions["claim_candidate"].binding_decision_id
          and binding.support_set_digest == decisions["claim_candidate"].support_set_digest,
          "binding 必须绑定 aggregate 决定与同一 support-set digest")
    check(binding.entailment_decision_id == ent.entailment_decision_id,
          "factual binding 必须绑定 entailment 决定")
    check(binding.fact_id == "f-1" and binding.material_id == "m-1"
          and binding.locator_ref == NS.char_range_locator("evidence:ev-1", 3, 40)
          and binding.support_role == "primary",
          "factual binding 必须携带被授权的权威事实/材料身份与精确 locator")
    check(binding.support_semantics == "factual"
          and binding.binding_subject_kind == "claim_candidate",
          "factual binding 的 subject 只能是 claim_candidate")
    check(NS.is_claim_support_ref_union_member(binding)
          and NS.is_claim_support_ref_union_member(p_factual),
          "`ClaimSupportRef` 只是兼容 union 名称：成员唯有 proposal 与 accepted binding")
    check(AB.accept_bindings(sr_claim, [p_factual], decisions["claim_candidate"], ent)[0]
          .to_dict() == binding.to_dict(),
          "同一输入的 accepted binding 必须逐字段相同（身份是内容寻址的）")

    # §6：缺 aggregate / 缺 entailment / 换 subject 的一条决定，都必须拒。
    expect_raises("factual binding 缺 entailment 决定必须拒",
                  lambda: AB.accept_bindings(sr_claim, [p_factual],
                                             decisions["claim_candidate"], None),
                  AB.AcceptedBindingError, needle="ClaimEntailmentDecision")
    expect_raises("factual binding 用了另一 candidate 的 entailment 决定必须拒",
                  lambda: AB.accept_bindings(
                      sr_claim, [p_factual], decisions["claim_candidate"],
                      _entailment(decisions["claim_candidate"], claim_candidate_id="ccand-other")),
                  AB.AcceptedBindingError, needle="另一个候选")
    expect_raises("factual binding 用了另一 revision 的 entailment 决定必须拒",
                  lambda: AB.accept_bindings(
                      sr_claim, [p_factual], decisions["claim_candidate"],
                      _entailment(decisions["claim_candidate"], draft_revision="sdrev-other")),
                  AB.AcceptedBindingError, needle="另一 candidate revision")
    expect_raises("entailment 决定没绑定本 subject 的 aggregate 必须拒",
                  lambda: AB.accept_bindings(
                      sr_claim, [p_factual], decisions["claim_candidate"],
                      _entailment(decisions["claim_candidate"], binding_decision_id="cbd-other")),
                  AB.AcceptedBindingError, needle="aggregate 决定")
    expect_raises("entailment 决定与 aggregate 的 digest 不同必须拒",
                  lambda: AB.accept_bindings(
                      sr_claim, [p_factual], decisions["claim_candidate"],
                      _entailment(decisions["claim_candidate"], support_set_digest="0" * 64)),
                  AB.AcceptedBindingError, needle="support-set digest")
    rejected = _entailment(decisions["claim_candidate"], verdict="rejected",
                           reason_code="unsupported_specificity")
    expect_raises("rejected 的 entailment 决定不得产生 accepted binding",
                  lambda: AB.accept_bindings(sr_claim, [p_factual],
                                             decisions["claim_candidate"], rejected),
                  AB.AcceptedBindingError, needle="终态")
    expect_raises("未通过的 aggregate 决定不得产生 accepted binding",
                  lambda: AB.accept_bindings(
                      sr_claim, [_factual(fact_id="f-ghost")],
                      CBG.decide_bindings(sr_claim, [_factual(fact_id="f-ghost")], scan,
                                          manifest=MANIFEST), ent),
                  AB.AcceptedBindingError, needle="未通过")

    # ============================================================ §7 context: 无 entailment、无 fact
    context_bindings = AB.accept_bindings(sr_unit, [p_context], decisions["narrative_draft_unit"])
    check(len(context_bindings) == 1 and context_bindings[0].entailment_decision_id is None,
          "context binding 必须无 entailment 决定（context 不进入语义门）")
    check(NS.support_fact_id(context_bindings[0]) == "",
          "context binding 不得携带任何 fact identity")
    check(context_bindings[0].binding_subject_kind == "narrative_draft_unit",
          "context binding 的 target 只能是 NarrativeDraftUnit")
    expect_raises("context binding 携带 entailment 决定必须拒",
                  lambda: AB.accept_bindings(sr_unit, [p_context],
                                             decisions["narrative_draft_unit"], ent),
                  AB.AcceptedBindingError, needle="不得携带 entailment")
    # 尾巴字段是类型层不可表达的：schema 构造器与门各自拒一次。
    docs = ("fact_id", "financial_fact_id", "note_fact_id", "external_fact_id")
    for field in docs:
        expect_raises(f"context proposal 携带 {field} 必须被 schema 拒",
                      lambda f=field: _context(**{f: "x-1"}),
                      NS.NarrativeSchemaError)
        expect_raises(f"context binding 携带 {field} 必须被门拒（schema 之后仍再拒一次）",
                      lambda f=field: AB.accept_bindings(
                          sr_unit, [_mutated(p_context, **{f: "x-1"})],
                          decisions["narrative_draft_unit"]),
                      (AB.AcceptedBindingError, NS.NarrativeSchemaError))

    # ============================================================ §10 非 topic 走路径 B
    expect_raises("非 topic authority 声明 path_b_material_derived 必须被 schema 拒",
                  lambda: NS.AcceptedSupportBinding.create(
                      proposed_support_id=p_context.proposed_support_id,
                      proposal_content_hash=p_context.content_hash(),
                      binding_subject_kind="narrative_draft_unit",
                      binding_subject_id=UNIT.draft_unit_id, draft_revision=REVISION,
                      binding_decision_id=decisions["narrative_draft_unit"].binding_decision_id,
                      support_set_digest=decisions["narrative_draft_unit"].support_set_digest,
                      authority_kind="financial_pack", authority_container_id="fin-1",
                      source_identity="financial:art-1", provenance_identity="prov-f-1",
                      support_role="corroborating", support_semantics="context",
                      authorization_path="path_b_material_derived", content_fingerprint=FP,
                      material_id=None, payload_ref={"object_type": "financial_fact"},
                      locator_ref=None),
                  NS.NarrativeSchemaError, needle="path_b_material_derived")
    # 门自己再拒一次（不假设上游校验过）：把 context 边的路径改成非 topic 的路径 B。机械门
    # 自己也会拒它（`authority_kind_mismatch`），所以这里**手工**伪造一条 pass 决定——正是
    # 「上游说通过、形状其实非法」的场景，accepted 侧必须自己复核形状而不是相信决定。
    tampered_path = _mutated(p_context, authorization_path="path_b_material_derived",
                             authority_kind="financial_pack")
    tampered_reasons = [e.reason_code for e in CBG.decide_bindings(
        sr_unit, [tampered_path], scan, manifest=MANIFEST).edge_results]
    check(tampered_reasons == ["authorization_path_mismatch"],
          f"前置条件：机械门对非 topic 路径 B 的 context 边必须判 fail（实测 {tampered_reasons}）")
    forged = NS.ClaimBindingDecision.create(
        subject_kind="narrative_draft_unit", subject_id=UNIT.draft_unit_id,
        draft_revision=REVISION, authority_kind="financial_pack",
        manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint(),
        support_set_digest=forged_digest(tampered_path), proposal_ids=(tampered_path.proposed_support_id,),
        proposal_hashes=(tampered_path.content_hash(),),
        edge_results=(NS.BindingEdgeResult(
            proposed_support_id=tampered_path.proposed_support_id,
            proposal_content_hash=tampered_path.content_hash(), result="pass", reason_code=None),),
        result="pass", rules_version=NS.CLAIM_BINDING_GATE_VERSION, structural_reason_code=None)
    expect_raises("非 topic 走路径 B 必须被门拒（伪造的 pass 决定也救不了它）",
                  lambda: AB.accept_bindings(sr_unit, [tampered_path], forged),
                  AB.AcceptedBindingError, needle="path_b_material_derived")

    # ============================================================ §19 不得引用未来对象
    future = sorted(set(AB.FORBIDDEN_FUTURE_IDENTITY_FIELDS))
    leaked = sorted(set(binding.to_dict()) & set(future))
    check(not leaked, f"accepted binding 的 wire 形状不得出现未来身份键（实测 {leaked}）")
    for field in ("section_claim_id", "section_result_id", "final_narrative_id"):
        expect_raises(f"accepted binding 传 {field} 必须被拒（不得静默丢弃）",
                      lambda f=field: NS.AcceptedSupportBinding.create(
                          **{**{k: v for k, v in binding.identity_body().items()
                                if k != "schema_version"}, f: "future-1"}),
                      NS.NarrativeSchemaError, needle="未知字段")
    expect_raises("accepted binding 的 from_dict 不得静默接受未来身份键",
                  lambda: NS.AcceptedSupportBinding.from_dict(
                      {**binding.to_dict(), "section_claim_id": "future-1"}),
                  NS.NarrativeSchemaError)
    expect_raises("context binding 的上游 proposal 引用未来身份必须被门拒",
                  lambda: AB.accept_bindings(
                      sr_unit, [_mutated(p_context, section_claim_id="future-1")],
                      decisions["narrative_draft_unit"]),
                  AB.AcceptedBindingError, needle="未来身份字段")

    # ============================================================ §2/§27/§29 批量 cardinality
    acceptance = AB.accept_draft_bindings(draft, list(decisions.values()),
                                          entailment_decisions=(ent,))
    check(len(acceptance.accepted_bindings) == 2 and acceptance.rejected_subjects == (),
          "一条通过候选 + 一条通过草稿单元 → 2 条 accepted binding、0 条拒绝")
    check(acceptance.subject_keys == (("claim_candidate", CANDIDATE.candidate_id),
                                     ("narrative_draft_unit", UNIT.draft_unit_id)),
          "批量结果必须记录 exact subject key 集")
    check(len(AB.bindings_for_subject(acceptance, "claim_candidate", CANDIDATE.candidate_id)) == 1
          and len(AB.bindings_for_subject(acceptance, "narrative_draft_unit",
                                          UNIT.draft_unit_id)) == 1,
          "按 subject 读取 accepted binding 必须各自恰好一条（引用由后继侧持有）")

    expect_raises("同一 candidate revision 的两条 aggregate 决定必须拒",
                  lambda: AB.accept_draft_bindings(
                      draft, list(decisions.values()) + [decisions["claim_candidate"]],
                      entailment_decisions=(ent,)),
                  AB.AcceptedBindingError, needle="多条 aggregate")
    expect_raises("缺一条 aggregate 决定必须拒",
                  lambda: AB.accept_draft_bindings(draft, [decisions["claim_candidate"]],
                                                   entailment_decisions=(ent,)),
                  AB.AcceptedBindingError, needle="不相等")
    expect_raises("aggregate 决定属于另一 draft revision 必须拒",
                  lambda: AB.accept_draft_bindings(
                      draft, [d for d in decisions.values()
                              if d.subject_kind != "claim_candidate"] + [
                          NS.ClaimBindingDecision.create(**{
                              **decisions["claim_candidate"].identity_body(),
                              "draft_revision": "sdrev-other"})],
                      entailment_decisions=(ent,)),
                  AB.AcceptedBindingError, needle="另一 draft revision")
    expect_raises("aggregate 已通过却没有 entailment 决定必须拒（缺结论不是拒绝）",
                  lambda: AB.accept_draft_bindings(draft, list(decisions.values())),
                  AB.AcceptedBindingError, needle="却没有 entailment 决定")
    expect_raises("两条 entailment 决定必须拒",
                  lambda: AB.accept_draft_bindings(draft, list(decisions.values()),
                                                   entailment_decisions=(ent, ent)),
                  AB.AcceptedBindingError, needle="两条 entailment")
    expect_raises("context subject 竟然有 entailment 决定必须拒",
                  lambda: AB.accept_draft_bindings(
                      draft, list(decisions.values()),
                      entailment_decisions=(ent, _entailment(
                          decisions["claim_candidate"],
                          claim_candidate_id=UNIT.draft_unit_id))),
                  AB.AcceptedBindingError, needle="context")
    expect_raises("一条 aggregate 复用于另一 subject 必须拒",
                  lambda: AB.accept_bindings(
                      sr_unit, [p_context], decisions["claim_candidate"]),
                  AB.AcceptedBindingError, needle="不是同一 subject")
    expect_raises("决定绑定的 proposal 集与本 subject 的不同源必须拒",
                  lambda: AB.accept_bindings(
                      sr_claim, [_factual(fact_id="f-ghost")],
                      CBG.decide_bindings(sr_claim, [_factual()], scan, manifest=MANIFEST), ent),
                  AB.AcceptedBindingError, needle="不同源")
    expect_raises("空 proposal 集必须拒",
                  lambda: AB.accept_bindings(sr_claim, [], decisions["claim_candidate"], ent),
                  AB.AcceptedBindingError, needle="不得为空")
    expect_raises("manifest 与 Draft 的 exact manifest 不同必须拒",
                  lambda: AB.accept_draft_bindings(
                      draft, list(decisions.values()), entailment_decisions=(ent,),
                      manifest=NS.WriterMaterialManifest.create(members=())),
                  AB.AcceptedBindingError, needle="不同")
    # 一条**被拒**候选的批量结论必须显式记录，不得静默丢弃。
    rejected_decision = CBG.decide_bindings(sr_claim, [_factual(fact_id="f-ghost")], scan,
                                           manifest=MANIFEST)
    acceptance_rej = AB.accept_draft_bindings(
        draft, [rejected_decision] + [d for d in decisions.values()
                                      if d.subject_kind != "claim_candidate"])
    check([(r.subject_kind, r.stage, r.reason_codes) for r in acceptance_rej.rejected_subjects]
          == [("claim_candidate", "mechanical", ("missing_authority_fact",))]
          and len(acceptance_rej.accepted_bindings) == 1,
          "机械拒绝必须形成显式 RejectedSubject（逐边原因码），且只有 context 那条被接受")
    expect_raises("未通过机械门的 subject 竟然带 entailment 决定必须拒（伪造决定）",
                  lambda: AB.accept_draft_bindings(
                      draft, [rejected_decision] + [d for d in decisions.values()
                                                    if d.subject_kind != "claim_candidate"],
                      entailment_decisions=(ent,)),
                  AB.AcceptedBindingError, needle="伪造决定")
    sem_rej = AB.accept_draft_bindings(
        draft, list(decisions.values()),
        entailment_decisions=(_entailment(decisions["claim_candidate"], verdict="rejected",
                                          reason_code="unauthorized_number_or_period"),))
    check([(r.stage, r.reason_codes, bool(r.entailment_decision_id))
           for r in sem_rej.rejected_subjects]
          == [("semantic", ("unauthorized_number_or_period",), True)],
          "语义拒绝必须记封闭原因码 + entailment 决定 id")
    check(len(sem_rej.accepted_bindings) == 1,
          "被语义拒绝的候选不得产生 factual accepted binding")
    expect_raises("机械拒绝不得携带 entailment 决定 id",
                  lambda: AB.RejectedSubject(
                      subject_kind="claim_candidate", subject_id=CANDIDATE.candidate_id,
                      draft_revision=REVISION, binding_decision_id="cbd-1", stage="mechanical",
                      reason_codes=("missing_authority_fact",), entailment_decision_id="ced-1"),
                  AB.AcceptedBindingError, needle="机械拒绝")
    expect_raises("语义拒绝必须携带 entailment 决定 id",
                  lambda: AB.RejectedSubject(
                      subject_kind="claim_candidate", subject_id=CANDIDATE.candidate_id,
                      draft_revision=REVISION, binding_decision_id="cbd-1", stage="semantic",
                      reason_codes=("unsupported_specificity",), entailment_decision_id=None),
                  AB.AcceptedBindingError, needle="必须带 entailment")
    expect_raises("同一 subject 既被接受又被拒绝必须拒",
                  lambda: AB.DraftAcceptance(
                      accepted_bindings=(binding,), rejected_subjects=(
                          AB.RejectedSubject(
                              subject_kind="claim_candidate", subject_id=CANDIDATE.candidate_id,
                              draft_revision=REVISION, binding_decision_id="cbd-1",
                              stage="mechanical", reason_codes=("forbidden_field_present",)),),
                      subject_keys=(("claim_candidate", CANDIDATE.candidate_id),)),
                  AB.AcceptedBindingError, needle="既被接受又被拒绝")
    expect_raises("结论不完备（某 subject 两种记录都没有）必须拒",
                  lambda: AB.DraftAcceptance(
                      accepted_bindings=(binding,), rejected_subjects=(),
                      subject_keys=(("claim_candidate", CANDIDATE.candidate_id),
                                    ("narrative_draft_unit", UNIT.draft_unit_id))),
                  AB.AcceptedBindingError, needle="完备")

    # ============================================================ 逐 subject 多条 proposal
    # 本批修复的缺陷：批量 accept 侧曾**不**把逐 subject 的 proposal 集规范化到 canonical
    # order，而单 subject 的 `accept_bindings` 要求该顺序、门侧协调器
    # `CBG._proposals_by_subject` 又已经规范化过——于是任何「一个 subject 有 ≥2 条 proposal」的
    # Draft 都会在 accept 处 fail-closed（多边是常态：primary + corroborating）。下面的夹具
    # **刻意**让 Draft 自带顺序不等于 canonical order（production 的 `_build_pre_gate_bundle`
    # 就是这个形态），因此它同时是这条修复的回归。
    scan_multi = _scan(PACK_FACT, PACK_FACT_2)
    p_factual_2 = _factual(fact_id="f-2", support_role="corroborating")
    multi_pair = tuple(sorted((p_factual, p_factual_2),
                              key=lambda p: p.proposed_support_id, reverse=True))
    multi_ids = [p.proposed_support_id for p in multi_pair]
    draft_multi = _draft([*multi_pair, p_context], bundle_order=True)
    check(multi_ids != sorted(multi_ids),
          "前置条件：本夹具的 Draft 自带逐 subject 顺序必须**非** canonical order"
          "（否则它证明不了这条修复）")
    check([p.proposed_support_id for p in
           draft_multi.proposals_for_subject("claim_candidate", CANDIDATE.candidate_id)]
          == multi_ids,
          "Draft 必须原样保留自己的束顺序（该顺序进 identity_body，不得被就地重排）")
    multi_decisions = {d.subject_kind: d for d in
                       CBG.decide_draft_bindings(draft_multi, scan_multi, manifest=MANIFEST)}
    multi_claim_decision = multi_decisions["claim_candidate"]
    check(multi_claim_decision.result == "pass"
          and list(multi_claim_decision.proposal_ids) == sorted(multi_ids),
          "门侧协调器给出的 aggregate 决定必须按 canonical order 绑定**全部两条**边")
    multi_ent = _entailment(multi_claim_decision)
    multi_acceptance = AB.accept_draft_bindings(
        draft_multi, list(multi_decisions.values()), entailment_decisions=(multi_ent,))
    multi_bindings = AB.bindings_for_subject(
        multi_acceptance, "claim_candidate", CANDIDATE.candidate_id)
    check(len(multi_bindings) == 2 and multi_acceptance.rejected_subjects == (),
          f"逐 subject 多条 proposal 必须逐条成形 accepted binding（实测 "
          f"{len(multi_bindings)} 条 / 拒绝 {len(multi_acceptance.rejected_subjects)} 条）")
    check([b.proposed_support_id for b in multi_bindings] == sorted(multi_ids),
          "accepted binding 束必须按 canonical order 呈现，且与决定绑定的 id 集逐项相同")
    check(all(b.binding_decision_id == multi_claim_decision.binding_decision_id
              and b.support_set_digest == multi_claim_decision.support_set_digest
              for b in multi_bindings),
          "同一 subject 的多条 binding 必须同属一条 aggregate 决定与同一个 support-set digest")
    # 修复**不得**把真实的集差异洗白成「顺序问题」：只少一条边的伪造决定仍必须拒。
    _keep = next(p for p in multi_pair if p.proposed_support_id == multi_ids[0])
    subset_decision = NS.ClaimBindingDecision.create(**{
        **multi_claim_decision.identity_body(),
        "proposal_ids": (multi_ids[0],),
        "proposal_hashes": (_keep.content_hash(),),
        "edge_results": tuple(e for e in multi_claim_decision.edge_results
                              if e.proposed_support_id == multi_ids[0]),
        "support_set_digest": CBG.support_set_digest(
            subject_revision=sr_claim, proposal_ids=(multi_ids[0],),
            proposal_hashes=(_keep.content_hash(),),
            manifest_id=MANIFEST.manifest_id,
            manifest_fingerprint=MANIFEST.fingerprint())})
    expect_raises("决定只绑定子集（候选无故消失）仍必须拒",
                  lambda: AB.accept_draft_bindings(
                      draft_multi,
                      [subset_decision, multi_decisions["narrative_draft_unit"]],
                      entailment_decisions=(multi_ent,)),
                  AB.AcceptedBindingError, needle="不同源")
    # Draft 侧少一条边（另一方向的「候选无故消失」）：决定绑两条、Draft 只有一条。
    expect_raises("Draft 少一条边而决定仍绑两条必须拒",
                  lambda: AB.accept_draft_bindings(
                      _draft([multi_pair[0], p_context]),
                      list(multi_decisions.values()), entailment_decisions=(multi_ent,)),
                  AB.AcceptedBindingError, needle="不同源")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

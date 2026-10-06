"""P10 门后 accepted 支撑边（`DESIGN_V2.md` §6.3.3，方向约束 P1-1）。

用法：`from sections import accepted_binding as AB`

职责（且仅此）：把**已通过**的门结论固化成 `AcceptedSupportBinding` —— 每个通过 proposal
**各产生一条**（不是每个 subject 一条，也不是每个 candidate 一条）。

* target 指向 **proposal + subject revision + 对应决定**：factual 边同时指向 aggregate
  `ClaimBindingDecision` **与** `ClaimEntailmentDecision` 两个决定；context 边**只**指向
  aggregate。两条决定必须与同一条 proposal 集、同一 `support_set_digest` 同源。
* 方向（P1-1）：`AcceptedSupportBinding` **不得**引用尚未形成的 `SectionClaim`、final
  Narrative 或 `SectionResult` —— 这些字段在本模块的类型层根本不存在，`_assert_no_future_identity`
  再对 wire 形状做一次回归断言（防止以后有人给 wire 加回指字段）。反向引用由后继侧持有
  （`SectionClaim.accepted_binding_ids`、final Narrative 的 claim/context binding ID 引用）。
* 边形状规则的**唯一**实现是 `narrative_schema.validate_support_edge_shape`：proposal 与
  accepted binding 共用它（本模块只调用，不复刻一份）；`ClaimSupportRef` 因此降为兼容的
  tagged union 名称（`ProposedSupportRef | AcceptedSupportBinding`），不再是第三种 wire。
* 本模块**不**重实现 P8 的 cardinality/digest 断言，**不**判语义，**不**调用 LLM，**不**写库。
  它只断言「拿到的决定与拿到的 proposal 集同源、同 subject、同 digest」，否则拒绝成形。

fail-closed 清单：

| 情形 | 行为 |
|---|---|
| aggregate 未通过 | 抛（失败 aggregate 自身就是该机械拒绝的 typed audit，不得跳过它造 binding） |
| 决定与 proposal 集不同源（少了/多了/换了 proposal） | 抛 |
| 逐边结果里有非 pass 的边 | 抛 |
| factual 边缺 entailment 决定 | 抛 |
| factual 边的 entailment 决定换 subject / 换 revision / 换 digest / 换 aggregate | 抛 |
| factual 边的 entailment `verdict != "entailed"` | 抛（拒绝是终态，不得由它产生 accepted binding） |
| context 边携带 entailment 决定 | 抛 |
| context 边携带任何 fact identity | 抛（类型层已拒，这里再显式拒一次） |
| 非 topic authority 声明 `path_b_material_derived` | 抛 |
| 批量：某 subject 配到零条/多条 aggregate；aggregate 被复用于另一 subject | 抛 |
| 批量：一条通过 aggregate 配不到**恰好**一条 entailment 决定 | 抛（不得把「没有决定」当成拒绝） |
| 批量：entailment 决定指向未通过聚合门/非 factual 的 subject | 抛（伪造决定） |

批量结果**不**把拒绝静默丢掉：每条被拒 subject 形成显式 `RejectedSubject`（机械拒绝记逐边
原因码、语义拒绝记封闭原因码），与 accepted binding 一起交给编排者；缺口不靠 `not_used` 冒充。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sections import claim_binding_gate as CBG
from sections import narrative_schema as NS

#: accepted binding 的 wire 字段集里**不得**出现的未来身份键（P1-1 回归断言）。
FORBIDDEN_FUTURE_IDENTITY_FIELDS: tuple[str, ...] = (
    "section_claim_id", "section_result_id", "section_draft_id", "claim_id",
    "final_narrative_id", "narrative_sentence_id", "narrative_paragraph_id",
    "narrative_table_id",
)

#: 成形时对**传入 proposal** 复核的封闭字段集：本模块不假设 P8 已经跑过（或被绕过），
#: 两类未来身份键一起查——门前的 proposal 不得携带未来 ID，门后的 accepted binding 亦然。
FORBIDDEN_PROPOSAL_FIELDS: tuple[str, ...] = tuple(sorted(
    set(FORBIDDEN_FUTURE_IDENTITY_FIELDS) | set(CBG.FORBIDDEN_PROPOSAL_FIELDS)))

#: 拒绝阶段：机械门（P8）与语义门（P9）。缺口必须能指出是哪一门拒的。
REJECTION_STAGES: tuple[str, ...] = ("mechanical", "semantic")


class AcceptedBindingError(Exception):
    """accepted binding 成形阶段的 fail-closed 错误。"""


@dataclass(frozen=True)
class RejectedSubject:
    """一条被拒 subject revision 的**显式**拒绝记录（不是 gap、也不是 `not_used`）。

    `stage="mechanical"` 时 `reason_codes` 是逐边机械原因码（canonical order）；`stage="semantic"`
    时是**一条**封闭语义原因码，且必须带 entailment 决定 id —— 拒绝只能来自真实决定，不能来自
    「决定不存在」。
    """

    subject_kind: str
    subject_id: str
    draft_revision: str
    binding_decision_id: str
    stage: str
    reason_codes: tuple[str, ...]
    entailment_decision_id: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in REJECTION_STAGES:
            raise AcceptedBindingError(f"RejectedSubject.stage={self.stage!r} 不在 {list(REJECTION_STAGES)}")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        if not self.reason_codes:
            raise AcceptedBindingError("RejectedSubject.reason_codes 不得为空：拒绝必须有原因码")
        if self.stage == "semantic":
            if not self.entailment_decision_id:
                raise AcceptedBindingError(
                    "语义拒绝必须带 entailment 决定 id：拒绝只能来自真实决定")
        elif self.entailment_decision_id is not None:
            raise AcceptedBindingError("机械拒绝不得携带 entailment 决定 id（机械门未过即无语义决定）")


@dataclass(frozen=True)
class DraftAcceptance:
    """一条 Draft 的接受结果：accepted binding 束 + 显式拒绝束（两者互斥且完备）。"""

    accepted_bindings: tuple[NS.AcceptedSupportBinding, ...]
    rejected_subjects: tuple[RejectedSubject, ...]
    subject_keys: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted_bindings", tuple(self.accepted_bindings))
        object.__setattr__(self, "rejected_subjects", tuple(self.rejected_subjects))
        object.__setattr__(self, "subject_keys", tuple(self.subject_keys))
        accepted_subjects = {(b.binding_subject_kind, b.binding_subject_id)
                             for b in self.accepted_bindings}
        rejected_subjects = {(r.subject_kind, r.subject_id) for r in self.rejected_subjects}
        overlap = sorted(accepted_subjects & rejected_subjects)
        if overlap:
            raise AcceptedBindingError(
                f"同一 subject 既被接受又被拒绝（{overlap}）：一条 revision 只能有一个结论")
        missing = sorted(set(self.subject_keys) - accepted_subjects - rejected_subjects)
        if missing:
            raise AcceptedBindingError(
                f"subject {missing} 既无 accepted binding 也无拒绝记录：结论必须完备"
                "（不得把「没有决定」当成拒绝，也不得静默丢弃）")

    @property
    def accepted_support_binding_ids(self) -> tuple[str, ...]:
        return tuple(b.accepted_support_binding_id for b in self.accepted_bindings)


# ---------------------------------------------------------------------------
# 单 subject 成形
# ---------------------------------------------------------------------------

def _assert_no_future_identity(binding: NS.AcceptedSupportBinding) -> None:
    """P1-1 回归断言：accepted binding 的 wire 形状里不得出现未来身份键。"""
    leaked = sorted(set(binding.to_dict()) & set(FORBIDDEN_FUTURE_IDENTITY_FIELDS))
    if leaked:
        raise AcceptedBindingError(
            f"accepted binding 引用了尚未形成的对象字段 {leaked}：方向是单向的，"
            "反向引用只能由 SectionClaim / final Narrative 侧持有")


def _check_entailment_decision(subject_revision: CBG.BindingSubjectRevision,
                               aggregate_decision: NS.ClaimBindingDecision,
                               entailment_decision: Any) -> NS.ClaimEntailmentDecision:
    """factual 边的第二个决定：必须是**同一** subject revision、同一 aggregate、同一 digest 的 entailed。"""
    if not isinstance(entailment_decision, NS.ClaimEntailmentDecision):
        raise AcceptedBindingError(
            "factual accepted binding 必须携带 ClaimEntailmentDecision："
            "「已接受但从未做过语义核验」不具备证据意义")
    if entailment_decision.claim_candidate_id != subject_revision.subject_id:
        raise AcceptedBindingError(
            "entailment 决定属于另一个候选"
            f"（决定 {entailment_decision.claim_candidate_id!r} ≠ subject "
            f"{subject_revision.subject_id!r}）：不得跨 candidate 借证")
    if entailment_decision.draft_revision != subject_revision.draft_revision:
        raise AcceptedBindingError(
            "entailment 决定属于另一 candidate revision（跨 revision 借证即拒）")
    if entailment_decision.binding_decision_id != aggregate_decision.binding_decision_id:
        raise AcceptedBindingError(
            "entailment 决定没有绑定本 subject 通过的 aggregate 决定"
            f"（{entailment_decision.binding_decision_id!r} ≠ "
            f"{aggregate_decision.binding_decision_id!r}）")
    if entailment_decision.support_set_digest != aggregate_decision.support_set_digest:
        raise AcceptedBindingError(
            "entailment 决定与 aggregate 决定的 support-set digest 不同：两个决定不是同一支撑集")
    if entailment_decision.verdict != "entailed":
        raise AcceptedBindingError(
            f"entailment verdict={entailment_decision.verdict!r}（reason_code="
            f"{entailment_decision.reason_code!r}）：被拒候选是终态，"
            "不得由一条 rejected 决定产生 accepted binding")
    return entailment_decision


def accept_bindings(subject_revision: CBG.BindingSubjectRevision,
                    proposals_for_subject: Sequence[NS.ProposedSupportRef],
                    aggregate_decision: NS.ClaimBindingDecision,
                    entailment_decision: NS.ClaimEntailmentDecision | None = None,
                    ) -> tuple[NS.AcceptedSupportBinding, ...]:
    """一个 subject revision 的**每一个通过 proposal** 各产生一条 accepted binding。

    决定与 proposal 集必须同源（同一 subject/revision/manifest、同一 proposal id 集、同一
    digest），且每条 proposal 在决定里的逐边结果都是 `pass`；否则拒绝成形而不是「挑几条接受」。
    """
    if not isinstance(subject_revision, CBG.BindingSubjectRevision):
        raise AcceptedBindingError(
            "accept_bindings 的 subject_revision 必须是 BindingSubjectRevision")
    if not isinstance(aggregate_decision, NS.ClaimBindingDecision):
        raise AcceptedBindingError(
            "accept_bindings 的 aggregate_decision 必须是 ClaimBindingDecision")
    proposals = tuple(proposals_for_subject)
    if not proposals:
        raise AcceptedBindingError("accept_bindings 的 proposal 集不得为空")
    if (aggregate_decision.subject_kind != subject_revision.subject_kind
            or aggregate_decision.subject_id != subject_revision.subject_id
            or aggregate_decision.draft_revision != subject_revision.draft_revision):
        raise AcceptedBindingError(
            "aggregate 决定与 subject revision 不是同一 subject：不得把一条决定复用于另一 subject")
    if aggregate_decision.result != "pass":
        raise AcceptedBindingError(
            f"aggregate 决定 {aggregate_decision.binding_decision_id} 未通过"
            f"（result={aggregate_decision.result!r}，structural="
            f"{aggregate_decision.structural_reason_code!r}）："
            "失败 aggregate 自身就是该机械拒绝的 typed audit，不是 accepted binding 的来源")

    ordered = sorted(proposals, key=lambda p: p.proposed_support_id)
    if [p.proposed_support_id for p in proposals] != [p.proposed_support_id for p in ordered]:
        raise AcceptedBindingError("proposal 集必须按 proposed_support_id 升序（canonical order）")
    ids = [p.proposed_support_id for p in proposals]
    if len(set(ids)) != len(ids):
        raise AcceptedBindingError("proposal 集含重复 id")
    if list(aggregate_decision.proposal_ids) != ids:
        raise AcceptedBindingError(
            "决定绑定的 proposal 集与本 subject 的 proposal 集不同源"
            f"（决定 {list(aggregate_decision.proposal_ids)} ≠ 传入 {ids}）")
    edges = {e.proposed_support_id: e for e in aggregate_decision.edge_results}
    if set(edges) != set(ids):
        raise AcceptedBindingError("决定的逐边结果与本 subject 的 proposal 集不同源")
    failed = [pid for pid in ids if edges[pid].result != "pass"]
    if failed:
        raise AcceptedBindingError(
            f"proposal {failed} 的逐边结果不是 pass：不得为未通过的边成形 accepted binding")

    out: list[NS.AcceptedSupportBinding] = []
    for proposal in proposals:
        leaked = sorted(name for name in FORBIDDEN_PROPOSAL_FIELDS
                        if getattr(proposal, name, None) is not None)
        if leaked:
            raise AcceptedBindingError(
                f"支撑边携带未来身份字段 {leaked}：门前 proposal 与门后 accepted binding 都不得"
                "引用尚未形成的对象（本模块不假设机械门已经跑过）")
        semantics = proposal.support_semantics
        if semantics == "factual":
            decision = _check_entailment_decision(
                subject_revision, aggregate_decision, entailment_decision)
            entailment_id: str | None = decision.entailment_decision_id
        else:
            if entailment_decision is not None:
                raise AcceptedBindingError(
                    "context accepted binding 不得携带 entailment 决定："
                    "context 边不进入语义门，也不授权事实")
            for field in NS.ALL_FACT_FIELDS:
                if getattr(proposal, field, None):
                    raise AcceptedBindingError(
                        f"context accepted binding 不得携带任何 fact identity（{field}）："
                        "context 只验证背景/结构/衔接")
            entailment_id = None
        if (proposal.authorization_path == "path_b_material_derived"
                and proposal.authority_kind != "topic_pack"):
            raise AcceptedBindingError(
                f"authority_kind={proposal.authority_kind!r} 不存在 path_b_material_derived："
                "正式采纳为非 topic 权威时必须先成为 Pack material 再改记 topic_pack 路径 B")
        binding = NS.AcceptedSupportBinding.create(
            proposed_support_id=proposal.proposed_support_id,
            proposal_content_hash=proposal.content_hash(),
            binding_subject_kind=proposal.binding_subject_kind,
            binding_subject_id=proposal.binding_subject_id,
            draft_revision=proposal.draft_revision,
            binding_decision_id=aggregate_decision.binding_decision_id,
            support_set_digest=aggregate_decision.support_set_digest,
            authority_kind=proposal.authority_kind,
            authority_container_id=proposal.authority_container_id,
            source_identity=proposal.source_identity,
            provenance_identity=proposal.provenance_identity,
            support_role=proposal.support_role,
            support_semantics=semantics,
            authorization_path=proposal.authorization_path,
            content_fingerprint=proposal.content_fingerprint,
            entailment_decision_id=entailment_id,
            fact_id=proposal.fact_id, financial_fact_id=proposal.financial_fact_id,
            note_fact_id=proposal.note_fact_id, external_fact_id=proposal.external_fact_id,
            material_id=proposal.material_id, payload_ref=proposal.payload_ref,
            locator_ref=proposal.locator_ref)
        _assert_no_future_identity(binding)
        out.append(binding)
    return tuple(out)


# ---------------------------------------------------------------------------
# 批量协调：一条 Draft 的 exact subject 集 → accepted binding 束 + 拒绝束
# ---------------------------------------------------------------------------

def _index_entailment_decisions(
        entailment_decisions: Sequence[NS.ClaimEntailmentDecision],
        ) -> dict[tuple[str, str], NS.ClaimEntailmentDecision]:
    by_subject: dict[tuple[str, str], NS.ClaimEntailmentDecision] = {}
    for decision in entailment_decisions:
        if not isinstance(decision, NS.ClaimEntailmentDecision):
            raise AcceptedBindingError(
                "entailment_decisions 含非 ClaimEntailmentDecision 对象")
        key = (decision.claim_candidate_id, decision.draft_revision)
        if key in by_subject:
            raise AcceptedBindingError(
                f"候选 {key[0]}（revision {key[1]}）有两条 entailment 决定："
                "同一 candidate revision 恰好一条，零条或两条均拒")
        by_subject[key] = decision
    return by_subject


def accept_draft_bindings(draft: NS.SectionDraft,
                          aggregate_decisions: Sequence[NS.ClaimBindingDecision], *,
                          entailment_decisions: Sequence[NS.ClaimEntailmentDecision] = (),
                          manifest: NS.WriterMaterialManifest | None = None,
                          ) -> DraftAcceptance:
    """一条 Draft 的确定性映射：subject → 唯一 aggregate（+ factual 的唯一 entailment）。

    `manifest` 缺省取 draft 自己的 exact manifest；显式传入时必须是**同一** manifest
    （id 与指纹都相同），否则拒绝 —— 否则「同一 support-set digest」会跨 manifest 成立。
    """
    if not isinstance(draft, NS.SectionDraft):
        raise AcceptedBindingError("accept_draft_bindings 的 draft 必须是 SectionDraft")
    manifest = manifest if manifest is not None else draft.material_manifest
    if not isinstance(manifest, NS.WriterMaterialManifest):
        raise AcceptedBindingError("accept_draft_bindings 的 manifest 必须是 WriterMaterialManifest")
    if (manifest.manifest_id != draft.material_manifest.manifest_id
            or manifest.fingerprint() != draft.material_manifest.fingerprint()):
        raise AcceptedBindingError(
            "accept_draft_bindings 的 manifest 与 draft 的 exact manifest 不同：拒绝成形")

    # subject 宇宙 = 拥有 ≥1 proposal 的 subject（没有 proposal 的 subject 没有机械决定，
    # 也就无从接受或拒绝；计划把这种情形定义为 mechanical fail-closed raise，不在这里兜底）。
    proposals_by_subject: dict[tuple[str, str], list[NS.ProposedSupportRef]] = {}
    for proposal in draft.proposed_support_refs:
        proposals_by_subject.setdefault(
            (proposal.binding_subject_kind, proposal.binding_subject_id), []).append(proposal)
    # 每个 subject 的 proposal 集**按 canonical order（`proposed_support_id` 升序）呈现**：与
    # 门侧协调器 `CBG._proposals_by_subject` 用**同一条**规则派生「该 subject 的完整有序集合」。
    # Draft 自己的 `proposed_support_refs` 顺序是**束的构造顺序**（候选序 × 逐边序），它是
    # `identity_body()` 的一部分（顺序参与 `derive_draft_id`，读回时成员行序是唯一原始来源），
    # 因此**不得**就地重排 Draft——canonical order 只是这份**派生视图**的呈现顺序。这里不排序
    # 会让「多边 subject」在 `accept_bindings` 的 canonical 断言上 fail-closed；排序**不改变**
    # 任何判据：proposal id 集、逐边结果、support_set_digest 与决定仍逐项复核（内容寻址 id 的
    # 升序是集合的确定性函数，不是「调用方顺手排好」）。
    for proposals in proposals_by_subject.values():
        proposals.sort(key=lambda p: p.proposed_support_id)
    subject_keys = tuple(sorted(proposals_by_subject))

    decision_by_subject: dict[tuple[str, str], NS.ClaimBindingDecision] = {}
    for decision in aggregate_decisions:
        if not isinstance(decision, NS.ClaimBindingDecision):
            raise AcceptedBindingError("aggregate_decisions 含非 ClaimBindingDecision 对象")
        if decision.draft_revision != draft.draft_revision:
            raise AcceptedBindingError(
                f"aggregate 决定 {decision.binding_decision_id} 属于另一 draft revision："
                "不得跨 revision 复用决定")
        key = (decision.subject_kind, decision.subject_id)
        if key in decision_by_subject:
            raise AcceptedBindingError(
                f"subject {key} 配到多条 aggregate 决定：每个 subject revision 恰好一条")
        decision_by_subject[key] = decision
    if set(decision_by_subject) != set(subject_keys):
        extra = sorted(set(decision_by_subject) - set(subject_keys))
        missing = sorted(set(subject_keys) - set(decision_by_subject))
        raise AcceptedBindingError(
            f"aggregate 决定的 subject key 集与 draft 的 proposal subject 集不相等"
            f"（多 {extra}，缺 {missing}）")

    entailments = _index_entailment_decisions(entailment_decisions)
    accepted: list[NS.AcceptedSupportBinding] = []
    rejected: list[RejectedSubject] = []
    for key in subject_keys:
        decision = decision_by_subject[key]
        subject_revision = CBG.BindingSubjectRevision(
            subject_kind=key[0], subject_id=key[1], draft_revision=draft.draft_revision)
        if key[0] == "narrative_draft_unit" and (key[1], draft.draft_revision) in entailments:
            raise AcceptedBindingError(
                f"context subject {key[1]} 竟然有 entailment 决定：context 不进入语义门")
        if decision.result != "pass":
            rejected.append(RejectedSubject(
                subject_kind=key[0], subject_id=key[1], draft_revision=draft.draft_revision,
                binding_decision_id=decision.binding_decision_id, stage="mechanical",
                reason_codes=tuple(dict.fromkeys(
                    e.reason_code for e in decision.edge_results if e.result != "pass"))))
            continue
        if key[0] == "claim_candidate":
            entailment = entailments.pop((key[1], draft.draft_revision), None)
            if entailment is None:
                raise AcceptedBindingError(
                    f"候选 {key[1]} 的 aggregate 已通过却没有 entailment 决定："
                    "「没有决定」不是拒绝，是缺结论，拒绝成形")
            if entailment.verdict != "entailed":
                rejected.append(RejectedSubject(
                    subject_kind=key[0], subject_id=key[1], draft_revision=draft.draft_revision,
                    binding_decision_id=decision.binding_decision_id, stage="semantic",
                    reason_codes=(str(entailment.reason_code),),
                    entailment_decision_id=entailment.entailment_decision_id))
                continue
        else:
            entailment = None
        accepted.extend(accept_bindings(
            subject_revision, tuple(proposals_by_subject[key]), decision,
            entailment_decision=entailment))
    if entailments:
        raise AcceptedBindingError(
            f"entailment 决定指向未通过聚合门/非 factual 的 subject：{sorted(entailments)}"
            "（伪造决定）")
    return DraftAcceptance(
        accepted_bindings=tuple(sorted(
            accepted, key=lambda b: (b.binding_subject_kind, b.binding_subject_id,
                                     b.proposed_support_id))),
        rejected_subjects=tuple(sorted(
            rejected, key=lambda r: (r.subject_kind, r.subject_id))),
        subject_keys=subject_keys)


def bindings_for_subject(acceptance: DraftAcceptance, subject_kind: str,
                         subject_id: str) -> tuple[NS.AcceptedSupportBinding, ...]:
    """读取某 subject 的 accepted binding 束（后继侧按此引用，不由 binding 反向引用）。"""
    return tuple(b for b in acceptance.accepted_bindings
                 if b.binding_subject_kind == subject_kind
                 and b.binding_subject_id == subject_id)

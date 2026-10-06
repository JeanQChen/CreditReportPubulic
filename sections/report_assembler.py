"""M930-3 确定性报告组装（§七）。

职责：把「已写作、已过两道门、已独立评估」的章节按 DemoScope 顺序拼成一份可审计报告，
并在拼装时**独立复核**保留链与决定链，而不是相信上游自证。

复核项（全部确定性、只读，不做研究、不写正文、不调用 Reviewer、不出具任何放行结论）：
1. DemoScope 内每个 section / topic 都必须有正文或显式缺口，且**集合恰好相等**
   （既不许漏，也不许凭空多出 DemoScope 之外的内容单位）；
2. Draft ↔ 决定链 ↔ Evaluation ↔ SectionResult 身份必须逐字段互绑（错配即拒），
   且硬门必须是**最新版本**门（旧门即便写着 pass 也不算）；
3. 决定链由组装器**自己**在 `(draft, authority)` 上重算：aggregate `ClaimBindingDecision`
   与 `DraftAcceptance` 必须与调用方带来的那一份逐项相等（伪造/陈旧决定即拒）；
   每个 factual `SectionClaim` 的 `accepted_binding_ids` 必须**恰好**等于它来源 candidate
   revision 的完整 factual accepted-binding 集；每条 factual binding 的两条决定（aggregate +
   entailment）必须同 subject revision、同 support-set digest；
4. 每条被选中权威 fact 必须有 `FactNarrativeDisposition`，且 `claimed` 的 fact 确实写在
   某条已定稿 Claim 里（引用集完整有序精确相等，双向闭合）；
5. 每条已定稿 Claim 的 citation 必须是**该 Claim 自己的 factual binding** 指向的权威 fact
   所给出的引用（不得事后补引用）；context accepted binding 不得携带任何 fact 身份；
6. 跨章节的**类型化**精确冲突：同一权威锚点 (kind, container, fact_id) 被两条已写 Claim
   以不同数字断言（语义矛盾不做猜测，只查类型化冲突）；
7. 正文由**唯一**渲染器从 final Narrative 重新渲染，必须与 `SectionResult.markdown` 逐字节
   相同；而 final Narrative 又必须等于**同一函数**在当前 Claim 集上重算的结果；
   `report_version` 只来自冻结的 `ReportVersionIdentity`，任何正文/依赖变化都会改变它；
8. **逐节**的权威容器精确性：每节的合法容器集合只从**本节自己的权威输入**派生，Draft 自报
   的容器清单必须与它恰好相等（多余/缺失/重复一律拒），支撑边也必须落回本节容器。不得用
   全报告容器并集顶替 —— 并集里出现某容器，既不证明它是哪一节的权威，也不证明它是任何一节
   的权威；合法但最终没有任何 Claim 用到它的 Pack 仍须保留在报告容器清单里。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sections import accepted_binding as AB
from sections import backbone_schema as BACKBONE
from sections import claim_binding_gate as CBG
from sections import claim_entailment_evaluator as CEE
from sections import final_sentence_fidelity as FSF
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

logger = logging.getLogger("sections.report_assembler")

#: asm-3：把「报告容器清单 = 各节声明的并集」改成「逐节从本节权威派生 + 声明必须恰好等于
#: 本节合法集」。这是一次**收紧**（新增 fail-closed 判据），因此必须换版本号：旧版本下组装
#: 出来的报告不能与新版本共用同一个版本口径（§四 1）。
#:
#: asm-4（M930-3 任务二）：身份补齐 §4.6 的两项绑定 —— 各节 SectionDraft 身份，以及「排除
#: 自身版本字段后的 assembled canonical payload」指纹。改 Draft ID、或改未呈现在 markdown
#: 中但属于报告身份的内容，现在都会换出新的 `report_version`；读回时独立重算，不一致即拒。
ASSEMBLER_VERSION = "asm-4"


class ReportAssemblerError(Exception):
    """组装期的 fail-closed 错误：宁可不出报告，也不出无法回查的报告。"""


@dataclass(frozen=True)
class ReportVersionInputs:
    """调用方**声明**的运行时身份输入（§四 1）。

    这些都是组装器无法凭空算出的运行时事实：DemoScope 输入面、投影计划、被选中任务、
    Pack 集合、财务权威 artifact，以及生成策略身份。

    注意：它们不是权威来源，而是**待核对的声明**。组装器会逐字段把它们与冻结投影
    （`scope_input_fingerprint` / `plan_id`）、**每节实际收到的权威输入**给出的容器集合
    （`topic_pack_ids` / `financial_fact_pack_artifact_id`）以及写入策略
    （`prompt_version` / `model_policy_id`）对账，任何不一致都 fail-closed。最终进入
    `ReportVersionIdentity` 的值一律取**权威侧**的值（§六）。

    容器集合取权威输入本身，而不是「已写 Claim 的支持边」：没有写成 Claim 的章节同样带着
    权威容器进入本次运行，版本身份必须覆盖它，否则同一份正文可以对应两个不同的输入面。
    """

    scope_input_fingerprint: str
    plan_id: str
    selected_task_ids: tuple[str, ...]
    topic_pack_ids: tuple[str, ...] = ()
    financial_fact_pack_artifact_id: str | None = None
    model_policy_id: str = ""
    prompt_version: str = ""


@dataclass(frozen=True)
class ScopeRequirement:
    """DemoScope 里本节必须交代的内容单位（section 或 section+topic）。"""

    section_id: str
    title: str
    topic_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.section_id:
            raise ReportAssemblerError("ScopeRequirement.section_id 不得为空")


@dataclass(frozen=True)
class SectionAssemblyInput:
    """**current 链**一节进入组装所需的全部已封存对象（3D：门后定稿形状）。

    与 M930-2 的 `outcome/evaluation/binding` 三元组相比，这里补齐的是**决定链与定稿侧**：

    * `draft` 仍是**门前**候选束（`claim_candidates` / `narrative_draft_units` /
      `proposed_support_refs`）——它**不**含 `section_result_id`，方向是单向的；
    * `aggregate_decisions` / `entailment_decisions` / `acceptance` 是两道门与 accepted
      binding 成形阶段的产物（P8 / P9 / P10）；
    * `claims` / `narrative` / `claim_narrative_dispositions` / `dispositions` / `result`
      是**门后**由 P15 协调器定稿的 current 对象（final Narrative 与 FND 都后于 Claim 形成）；
    * `evaluation` / `binding` 是本节的章级评估与 `Draft+Result+Evaluation+Gate` 绑定。

    组装器**不**相信以上任何一项：它自己在 `(draft, authority)` 上重算决定链、用
    `verify_section_narrative` 在最终 Claim 集上复算那五条机械判据（无 composed 句时仍要求
    逐段等于重算结果）、并用权威输入重算覆盖/缺口与 FND 引用集。`claim_narrative_dispositions`
    因此是**必填**字段：正文改为模型产物后，「没给我处置表」必须是构造期错误，而不是被缺省成
    空集合后当作「没有未处置的 Claim」通过。
    """

    task: Any
    authority: Any
    draft: NS.SectionDraft
    gate_result: NS.NarrativeGateResult
    aggregate_decisions: tuple[NS.ClaimBindingDecision, ...]
    entailment_decisions: tuple[NS.ClaimEntailmentDecision, ...]
    acceptance: AB.DraftAcceptance
    claims: tuple[SS.SectionClaim, ...]
    narrative: NS.SectionNarrative
    claim_narrative_dispositions: tuple[NS.ClaimNarrativeDisposition, ...]
    dispositions: tuple[NS.FactNarrativeDisposition, ...]
    result: SS.SectionResult
    evaluation: SS.SectionEvaluation
    binding: NS.NarrativeEvaluationBinding
    #: §12.4.4 第 4 步：本节 final Narrative 的最终句语义决定（`nsfid-1`，至多一条；缺省空元组）。
    #: 组装器**不**相信它——它按四样可重算输入（正文 / 本字段 / 定稿 Claim / 已接受支撑边）自己
    #: 重算 `NS.final_sentence_gate_state`，因此「这条决定是不是对**这份**正文、**这批**Claim 与
    #: **这批**支撑边下的判断」不靠调用方声明。缺省空元组是**旧产物**的合法形态（那是「没有
    #: 决定」而不是「无需核验」），此时若正文有承载事实的最终句，重算必然落在 missing 一档。
    final_sentence_decisions: tuple[NS.FinalSentenceFidelityDecision, ...] = ()

    @property
    def section_id(self) -> str:
        return self.draft.section_id

    @property
    def section_result(self) -> SS.SectionResult:
        """只读别名：让章级评估入口（`evaluate_narrative_section`）能消费同一份输入。"""
        return self.result


@dataclass(frozen=True)
class _VerifiedSection:
    """组装器**自己重算**出来的本节事实（下游一律用这些值，不再用调用方带来的产物字段）。"""

    rendered_markdown: str
    factual_bindings_by_claim: Mapping[str, tuple[NS.AcceptedSupportBinding, ...]]
    context_binding_ids: tuple[str, ...]
    factual_bindings_by_fact: Mapping[tuple[str, str, str],
                                      tuple[NS.AcceptedSupportBinding, ...]]


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ReportAssemblerError(message)


def _first_difference(actual: str, expected: str) -> str:
    """首处差异的可读定位（只做诊断展示，不参与任何判定）。"""
    limit = min(len(actual), len(expected))
    for i in range(limit):
        if actual[i] != expected[i]:
            return (f"；首处差异在第 {i} 字符：产物 {actual[i - 20:i + 20]!r} "
                    f"vs 重算 {expected[i - 20:i + 20]!r}")
    if len(actual) != len(expected):
        return (f"；长度不同：产物 {len(actual)} 字符，重算 {len(expected)} 字符，"
                f"产物尾部 {actual[limit:limit + 60]!r} vs 重算尾部 {expected[limit:limit + 60]!r}")
    return ""


def _final_sentence_gate_block(item: SectionAssemblyInput) -> SS.SectionUnresolved | None:
    """本节 Result 里**唯一**允许超出 Draft 缺口投影的那一条缺口：最终句门 block（可无）。

    §12.4.4 第 4 步：block **后于** Draft 形成（它核验的是最终句），Draft 里不可能有它的投影，
    因此「Result 缺口 == Draft 缺口」这条守恒判据必须收窄成「= Draft 缺口 ∪ 至多这一条」。
    收窄的界不是放宽：多出的第二条、或换掉 id 的那一条，下面照样拒。

    判据复用 wire 层唯一实现（`NS.final_sentence_gate_state`，经 `FSF` 的构造器），组装器**不**
    另写一套「算不算核过」。它只吃四样可重算输入——正文、决定、定稿 Claim、已接受支撑边——
    因此这里算出的 block 与定稿协调器、Store 读回算出的必须是**同一条**（id 内容寻址）。
    """
    return FSF.final_sentence_block_unresolved(
        section_id=str(item.section_id), narrative=item.narrative,
        decisions=item.final_sentence_decisions, claims=item.claims,
        accepted_bindings=tuple(item.acceptance.accepted_bindings))


def _require_gap_conservation(item: SectionAssemblyInput, *, where: str) -> None:
    """「Draft 缺口 ⊆ Result 缺口，且 Result 独有项**恰是**重算出的那一条最终句门 block」。

    **双向**是这条判据的要点，单向包含会留下一个洞：如果只要求「Result 独有项不超过重算出的
    block」，那么一个**没带** `final_sentence_decisions` 的调用方会让重算结果落在 `missing` 一档，
    而它的 Result 里并没有那条 block——「重算说要阻断」与「产物里没有阻断」于是被读成「一致」。
    所以这里要求两侧**逐条相等**：重算说要阻断时，产物里必须真有那一条（且字段逐项相同）；
    重算说通过时，产物里也不许多出任何一条。

    `where` 只用于诊断文案（说明是保留链还是守恒链在核）。"""
    block = _final_sentence_gate_block(item)
    expected = {str(u.unresolved_id): u for u in item.result.unresolved}
    draft_ids = {str(x) for x in item.draft.unresolved_ids}
    dropped = sorted(draft_ids - set(expected))
    added = sorted(set(expected) - draft_ids)
    want_extra = {str(block.unresolved_id)} if block is not None else set()
    _require(not dropped and set(added) == want_extra,
             f"section={item.section_id!r} 的 draft 缺口与 SectionResult 缺口不一致"
             f"（{where}）：draft 独有（缺口被静默丢弃）{dropped[:6]}，"
             f"result 独有 {added[:6]}，而最终句门重算要求独有 {sorted(want_extra)[:6]}"
             "（draft 缺口不得丢；Result 必带的最终句门 block 也不得缺席或多出）")
    if block is not None:
        _require(expected[str(block.unresolved_id)] == block,
                 f"section={item.section_id!r} 的最终句门 block 与重算结果不相等"
                 f"（{where}）：产物 {expected[str(block.unresolved_id)]!r}，"
                 f"重算 {block!r}（同一缺口集合只有一个内容寻址身份）")


def _binding_fact_key(binding: NS.AcceptedSupportBinding) -> tuple[str, str, str] | None:
    """factual accepted binding 的权威坐标 `(kind, container, fact_id)`；路径 B 返回 `None`。

    路径 B（材料派生的描述性原子）**没有**预验证权威事实身份，因此不参与 FND（FND 只记录
    「被选中的预验证权威事实」的去向）。返回 `None` 不等于「无支撑」——它的 citation 由
    material 自己派生，仍须逐条核验。坐标口径只有 `NS.binding_fact_key` 一份实现。
    """
    return NS.binding_fact_key(binding)


# ---------------------------------------------------------------------------
# 逐项复核
# ---------------------------------------------------------------------------

def _task_authority(projection: Any, task: Any) -> Any:
    """从冻结投影里取回同 task_id 的权威 SectionTask（§六：输入必须先对回权威）。"""
    for candidate in projection.report_plan.section_tasks:
        if candidate.task_id == task.task_id:
            return candidate
    raise ReportAssemblerError(
        f"SectionTask {task.task_id!r} 不在冻结投影 {projection.projection_id!r} 的 plan 里"
        "（组装输入必须来自权威 plan，不得自行拼装任务）")


#: `SectionDraft` 侧**不得**出现的未来身份键（P1-1：方向是单向的，Draft→Result 不回环）。
DRAFT_FORBIDDEN_IDENTITY_FIELDS: tuple[str, ...] = (
    "section_result_id", "claim_id", "claim_ids", "section_claim_id", "section_claim_ids",
    "accepted_binding_id", "accepted_binding_ids", "aggregate_decision_id",
    "entailment_decision_id", "evaluation_id", "narrative_id", "follow_up_need_id",
)

#: `SectionResult` 侧**不得**内嵌的字段（C8/C15/C16：Result 不得反向携带评估/决定）。
RESULT_FORBIDDEN_EMBEDDED_FIELDS: tuple[str, ...] = (
    "evaluation", "evaluation_id", "decisions", "binding", "binding_id",
)


def _verify_identity(item: SectionAssemblyInput, *, projection: Any) -> None:
    draft, result, narrative, task = item.draft, item.result, item.narrative, item.task
    authority_task = _task_authority(projection, task)
    for name, actual, expected in (
            ("company_id", draft.company_id, projection.company_id),
            ("report_as_of", draft.report_as_of, projection.report_as_of),
            ("contract_version", draft.contract_version, projection.contract_version),
            ("contract_fingerprint", draft.contract_fingerprint,
             projection.contract_fingerprint),
            ("plan_id", task.plan_id, projection.plan_id),
            ("dependency_fingerprint", draft.dependency_fingerprint,
             projection.dependency_fingerprint)):
        _require(actual == expected,
                 f"section={draft.section_id!r} 的 {name} 与冻结投影不一致："
                 f"{actual!r} != {expected!r}")
    # task 自身也必须与权威 Task 逐字段一致：section_id / title / topic 集合都是权威内容单位。
    for name, actual, expected in (
            ("section_id", task.section_id, authority_task.section_id),
            ("title", task.title, authority_task.title),
            ("topic_ids", tuple(task.topic_ids), tuple(authority_task.topic_ids))):
        _require(actual == expected,
                 f"SectionTask {task.task_id!r} 的 {name} 与冻结投影不一致："
                 f"{actual!r} != {expected!r}（任务内容单位不得由调用方改写）")
    _require(task.plan_id == projection.plan_id,
             f"SectionTask {task.task_id!r} 属于 plan {task.plan_id!r}，"
             f"不属于冻结投影的 plan {projection.plan_id!r}")
    _require(draft.section_id == task.section_id and result.section_id == task.section_id,
             f"draft/result 的 section_id 与 SectionTask 不一致（{draft.section_id!r}）")
    _require(draft.task_id == task.task_id and result.task_id == task.task_id,
             "draft/result 的 task_id 与 SectionTask 不一致")
    # §八：Draft → Result 单向。draft 不得持有任何未来身份（否则身份成环，DAG 不再单向）；
    # result 不得内嵌 evaluation/决定（C8：那条环正是 M930-3 要拆掉的）。
    leaked = sorted(set(draft.to_dict()) & set(DRAFT_FORBIDDEN_IDENTITY_FIELDS))
    _require(not leaked,
             f"section={draft.section_id!r} 的 Draft 持有了尚未形成的对象身份 {leaked}："
             "方向是单向的（Draft→Result），反向引用只能由 Claim/Narrative/Result 侧持有")
    # 用 current Result 的**唯一** writer 取字段集（`SectionResult` 没有 `to_dict`；legacy 的
    # `to_dict` 只属于 LegacySectionResultV1，拿它当 current 序列化等于放宽校验）。
    embedded = sorted(set(SS.section_result_to_dict(result)) & set(RESULT_FORBIDDEN_EMBEDDED_FIELDS))
    _require(not embedded,
             f"section={draft.section_id!r} 的 SectionResult 内嵌了 {embedded}："
             "current Result 禁止内嵌 evaluation/决定，绑定由 NarrativeEvaluationBinding 承载")
    _require(result.section_draft_id == draft.draft_id,
             f"SectionResult 单向引用的 Draft 不是本节 draft：{result.section_draft_id!r} != "
             f"{draft.draft_id!r}")
    _require(narrative.section_draft_id == draft.draft_id
             and narrative.draft_revision == draft.draft_revision,
             f"section={draft.section_id!r} 的 final Narrative 不属于本节 draft revision"
             f"（narrative.section_draft_id={narrative.section_draft_id!r}，"
             f"draft={draft.draft_id!r}/{draft.draft_revision!r}）")
    _require(narrative.task_id == task.task_id and narrative.section_id == task.section_id,
             "final Narrative 的 task/section 与 SectionTask 不一致")


def _recomputed_decision_chain(item: SectionAssemblyInput) -> AB.DraftAcceptance:
    """在 `(draft, authority)` 上**重算**决定链，并返回重算出的 acceptance 束。

    组装器不接受调用方自报的 aggregate / accepted binding：
    * `CBG.decide_draft_bindings` 从 draft 的 exact proposal 集确定性重算每个 subject
      revision 的**唯一** aggregate 决定（cardinality、完整有序 proposal 集、逐边结果、
      support-set digest 全在重算里），与调用方带来的那一份逐个 id 比对；
    * `AB.accept_draft_bindings` 用**重算出的** aggregate + 调用方带来的 entailment 决定
      重算 accepted binding 束（entailment 由语义门产生，不能确定性重算，因此它的
      **结构链接**在 `_verify_binding` 里逐条核对）。
    """
    draft = item.draft
    # 决定链吃的是权威的**确定性读视图**（`AuthorityScan`），不是权威输入本身：机械门明确
    # 拒绝「没有 `.facts` 条目序列」的对象——它不自己扫描权威。读视图走**唯一**公开入口，
    # 与写入侧、组装器别处的重算同源。
    scan = PW.scan_authority(item.authority, item.task)
    try:
        recomputed = CBG.decide_draft_bindings(draft, scan)
    except Exception as exc:  # noqa: BLE001 — 决定链无法重算即 fail-closed
        raise ReportAssemblerError(
            f"section={draft.section_id!r} 的决定链无法在 (draft, authority) 上重算"
            f"（{type(exc).__name__}: {exc}）：不得在无法复核的决定上宣称通过") from exc
    supplied = tuple(item.aggregate_decisions)
    # 逐条**按 subject 键**比对，不比元组位置：决定元组的顺序不是链的内容（store 提交时按
    # `decision_id` 排序，读回顺序因此与写入侧的内存序不同），把位置当判据会让**合法**的读回
    # 重组被拒。判据是「subject 集完全相等、每个 subject 恰一条、且 id 必须等于重算出来的那一条」，
    # 覆盖面与逐位比对相同（每个 subject 的唯一身份都被钉住），只是不再依赖调用方的排序。
    def _by_subject(values: Sequence[NS.ClaimBindingDecision]) -> dict:
        out: dict = {}
        for decision in values:
            key = (str(decision.subject_kind), str(decision.subject_id))
            _require(key not in out,
                     f"section={draft.section_id!r} 的 aggregate 决定含重复 subject {key}："
                     "每个 subject revision 恰有一条 aggregate 决定")
            out[key] = decision
        return out
    recomputed_by_subject = _by_subject(recomputed)
    supplied_by_subject = _by_subject(supplied)
    _require(set(recomputed_by_subject) == set(supplied_by_subject),
             f"section={draft.section_id!r} 的 aggregate 决定 subject 集与重算结果不一致："
             f"调用方独有 {sorted(set(supplied_by_subject) - set(recomputed_by_subject))[:3]}，"
             f"重算独有 {sorted(set(recomputed_by_subject) - set(supplied_by_subject))[:3]}"
             "（subject 宇宙必须等于拥有 proposal 的 subject 集）")
    mismatched: list[str] = []
    for key in sorted(recomputed_by_subject):
        expected = recomputed_by_subject[key]
        actual = supplied_by_subject[key]
        if expected.binding_decision_id != actual.binding_decision_id:
            mismatched.append(
                f"{key[0]}/{key[1]}: 调用方 "
                f"{actual.binding_decision_id!r}(result={actual.result!r}) ≠ 重算 "
                f"{expected.binding_decision_id!r}(result={expected.result!r})")
    _require(not mismatched,
             f"section={draft.section_id!r} 的 aggregate 决定与当前重算结果不一致："
             f"{mismatched[:3]}（决定必须在当前 Draft/权威输入上重算，调用方带来的旧决定或"
             "伪造决定不得作为组装依据）")
    try:
        acceptance = AB.accept_draft_bindings(
            draft, supplied, entailment_decisions=item.entailment_decisions)
    except Exception as exc:  # noqa: BLE001 — 成形阶段拒即组装拒
        raise ReportAssemblerError(
            f"section={draft.section_id!r} 的 accepted binding 束无法重算"
            f"（{type(exc).__name__}: {exc}）") from exc
    supplied_acceptance = item.acceptance
    _require(isinstance(supplied_acceptance, AB.DraftAcceptance),
             f"section={draft.section_id!r} 的 acceptance 必须是 DraftAcceptance")
    for name, recomputed_value, supplied_value in (
            ("accepted_bindings", acceptance.accepted_bindings,
             supplied_acceptance.accepted_bindings),
            ("rejected_subjects", acceptance.rejected_subjects,
             supplied_acceptance.rejected_subjects),
            ("subject_keys", acceptance.subject_keys, supplied_acceptance.subject_keys)):
        _require(tuple(recomputed_value) == tuple(supplied_value),
                 f"section={draft.section_id!r} 的 {name} 与重算结果不一致："
                 f"调用方 {len(tuple(supplied_value))} 项 vs 重算 "
                 f"{len(tuple(recomputed_value))} 项（accepted binding 必须逐 proposal "
                 "成形，且拒绝束必须显式保留、不得静默丢弃）")
    return acceptance


def _expected_claim_citations(item: SectionAssemblyInput,
                              bindings: Sequence[NS.AcceptedSupportBinding]) -> tuple[str, ...]:
    """一条 Claim 的权威 citation **身份串**集合（唯一来源：它自己 factual binding 指向的事实）。

    派生**不在这里**：`NS.claim_citation_refs` 是定稿与组装**共用**的唯一实现（路径 A 取权威
    fact 自己的引用、路径 B 取 exact material 自己的 payload/locator）。这里只把它折成身份串
    集合，组装器不接受「事后补引用」：Claim 的 citation 必须恰好等于这份集合。
    """
    try:
        refs = NS.claim_citation_refs(
            item.authority, bindings, what=f"section {item.section_id!r} 的 Claim")
    except Exception as exc:  # noqa: BLE001 — 引用无法派生即 fail-closed
        raise ReportAssemblerError(
            f"Claim 的权威 citation 无法派生（{type(exc).__name__}: {exc}）") from exc
    return tuple(sorted({SS.citation_identity(ref) for ref in refs}))


def _verify_claims_are_canonical(item: SectionAssemblyInput, acceptance: AB.DraftAcceptance) -> None:
    """Claim 定稿必须**逐字段**等于唯一确定性构造器 `NS.finalize_section_claims` 的输出。

    「组装器与协调器共用同一实现」不是一句口号：如果这里只重算身份与 binding 集，调用方仍然
    可以自带一套自己的定稿规则（换 topic 归属、换 question 集、换 claim_type 映射、调换顺序），
    产出**自洽但不 canonical** 的 Claim。因此这里直接重跑该构造函数，并要求整条序列逐字段
    （含顺序，正文分组顺序由此唯一确定）一致。
    """
    try:
        expected = NS.finalize_section_claims(task=item.task, authority=item.authority,
                                              draft=item.draft, acceptance=acceptance)
    except Exception as exc:  # noqa: BLE001 — 无法重算即 fail-closed
        raise ReportAssemblerError(
            f"section={item.draft.section_id!r} 的 Claim 无法按 canonical 定稿重算"
            f"（{type(exc).__name__}: {exc}）") from exc
    actual = tuple(item.claims)
    _require(len(actual) == len(expected),
             f"section={item.draft.section_id!r} 的 Claim 条数 {len(actual)} 与 canonical 定稿 "
             f"{len(expected)} 不一致（被接受的事实不得静默不呈现，也不得凭空增加 Claim）")
    fields = ("claim_id", "schema_version", "section_id", "topic_id", "question_ids", "text",
              "claim_type", "claim_candidate_id", "claim_candidate_revision",
              "accepted_binding_ids")
    for index, (got, want) in enumerate(zip(actual, expected)):
        for name in fields:
            left, right = getattr(got, name, None), getattr(want, name, None)
            _require(left == right,
                     f"section={item.draft.section_id!r} 第 {index} 条 Claim 的 {name} 与 "
                     f"canonical 定稿不一致：{left!r} != {right!r}（定稿只允许走唯一构造器）")
        got_cites = tuple(SS.citation_identity(r) for r in (got.citation_refs or ()))
        want_cites = tuple(SS.citation_identity(r) for r in (want.citation_refs or ()))
        _require(got_cites == want_cites,
                 f"section={item.draft.section_id!r} 第 {index} 条 Claim 的 citation_refs 与 "
                 "canonical 定稿不一致（引用必须由唯一实现派生，不得事后补）")


def _verify_claim_lineage(item: SectionAssemblyInput, acceptance: AB.DraftAcceptance,
                          *, verified: list[str]) -> tuple[
                              dict[str, tuple[NS.AcceptedSupportBinding, ...]],
                              dict[tuple[str, str, str], tuple[NS.AcceptedSupportBinding, ...]]]:
    """逐个 factual `SectionClaim` 独立重算身份与**完整** accepted-binding 集（P13）。

    只查「ID 在本 section 存在」是不够的：那样一条 Claim 可以把别的候选的 binding 算进
    自己的血缘，或只声明完整集的一部分。这里做的是**双向精确集合**重算：

    * Claim 的 candidate revision 必须唯一可解析到 draft 里的候选，且命题（`text`）与事实
      类型（`claim_type`）与候选逐字一致 —— 定稿不得改写命题；
    * `accepted_binding_ids` 必须**恰好等于**该 candidate revision 的完整 factual
      accepted-binding 集（多一条、少一条都拒），且非空（`factual` 类型）；
    * 每条 binding 必须能解析到一条 aggregate 决定（pass、同 subject revision、
      **同一 support-set digest**）与一条 entailment 决定（`entailed`、同 candidate
      revision、同 aggregate、同 digest）—— factual 边缺任何一条决定即拒；
    * citation 必须恰好等于这些 binding 指向的权威事实所给出的引用；
    * 整个 Claim 元组必须**逐字段**等于 `NS.finalize_section_claims` 在当前 draft + accepted
      集上的输出 —— 组装器与协调器共用**同一**定稿实现（§0.13），因此「另写一套定稿逻辑」
      不可能在这里蒙过去。
    """
    _verify_claims_are_canonical(item, acceptance)
    draft, task = item.draft, item.task
    candidates = {c.candidate_id: c for c in draft.claim_candidates}
    _require(len(candidates) == len(draft.claim_candidates),
             f"section={draft.section_id!r} 的 draft 含重复 candidate_id")
    aggregates = {d.binding_decision_id: d for d in item.aggregate_decisions}
    entailments = {d.entailment_decision_id: d for d in item.entailment_decisions}

    factual_by_subject: dict[str, list[NS.AcceptedSupportBinding]] = {}
    context_bindings: list[NS.AcceptedSupportBinding] = []
    for binding in acceptance.accepted_bindings:
        _require(binding.draft_revision == draft.draft_revision,
                 f"accepted binding {binding.accepted_support_binding_id!r} 属于另一 draft "
                 "revision：不得跨 revision 借证")
        semantics = str(binding.support_semantics)
        if semantics == "context":
            context_bindings.append(binding)
            continue
        _require(semantics == "factual",
                 f"accepted binding {binding.accepted_support_binding_id!r} 的 "
                 f"support_semantics={semantics!r} 未登记")
        _require(binding.binding_subject_kind == "claim_candidate",
                 "factual 支撑边的主体必须是 claim_candidate")
        factual_by_subject.setdefault(binding.binding_subject_id, []).append(binding)

    written_claim_ids = {c.claim_id for c in item.claims}
    _require(len(written_claim_ids) == len(item.claims),
             f"section={draft.section_id!r} 的 claims 含重复 claim_id")
    by_claim: dict[str, tuple[NS.AcceptedSupportBinding, ...]] = {}
    for claim in item.claims:
        candidate = candidates.get(claim.claim_candidate_id)
        _require(candidate is not None,
                 f"claim {claim.claim_id!r} 的 candidate {claim.claim_candidate_id!r} 不在本节 "
                 "draft 内：current Claim 必须回指**本节**候选")
        _require(claim.claim_candidate_revision == candidate.draft_revision
                 == draft.draft_revision,
                 f"claim {claim.claim_id!r} 的 candidate revision 与候选/draft 不一致："
                 f"{claim.claim_candidate_revision!r} / {candidate.draft_revision!r} / "
                 f"{draft.draft_revision!r}")
        _require(claim.section_id == draft.section_id,
                 f"claim {claim.claim_id!r} 不属于本节 {draft.section_id!r}")
        _require(claim.text == candidate.claim_text,
                 f"claim {claim.claim_id!r} 的命题与候选不一致（定稿不得改写命题）："
                 f"{claim.text!r} != {candidate.claim_text!r}")
        expected_type = NS.claim_type_for_fact_type(
            candidate.fact_type, f"claim {claim.claim_id!r} 的候选")
        _require(claim.claim_type == expected_type,
                 f"claim {claim.claim_id!r} 的 claim_type={claim.claim_type!r} 与候选声明的 "
                 f"fact_type={candidate.fact_type!r} 的 canonical 映射 {expected_type!r} 不一致")
        _require(claim.topic_id in tuple(task.topic_ids),
                 f"claim {claim.claim_id!r} 的 topic {claim.topic_id!r} 不属于本节 task "
                 f"{tuple(task.topic_ids)}")
        try:
            expected_questions = PW._question_ids_for_topic(
                task, claim.topic_id, f"claim {claim.claim_id!r}")
        except Exception as exc:  # noqa: BLE001 — 无法归属问题即 fail-closed
            raise ReportAssemblerError(
                f"claim {claim.claim_id!r} 的 question 归属无法从权威 task 派生"
                f"（{type(exc).__name__}: {exc}）") from exc
        _require(tuple(claim.question_ids) == tuple(expected_questions),
                 f"claim {claim.claim_id!r} 的 question_ids 与权威 task 不一致："
                 f"{tuple(claim.question_ids)} != {tuple(expected_questions)}"
                 "（问题归属必须来自任务侧权威，不得自行编号）")
        mine = tuple(sorted(factual_by_subject.get(claim.claim_candidate_id, ()),
                            key=lambda b: b.accepted_support_binding_id))
        declared = tuple(sorted(claim.accepted_binding_ids))
        _require(bool(mine),
                 f"claim {claim.claim_id!r} 的 candidate 没有任何 factual accepted binding："
                 "未被接受的候选不得成为正文事实来源")
        actual = tuple(b.accepted_support_binding_id for b in mine)
        _require(declared == actual,
                 f"claim {claim.claim_id!r} 的 accepted_binding_ids 不是该 candidate revision "
                 f"的**完整** factual accepted-binding 集：声明 {list(declared)}，"
                 f"实际 {list(actual)}（多一条/少一条都拒：只证明「至少一条」不算）")
        if claim.claim_type == "fact":
            _require(bool(declared),
                     f"factual claim {claim.claim_id!r} 没有 accepted binding（claim-2 硬约束）")
        for binding in mine:
            _require(binding.binding_subject_id == claim.claim_candidate_id,
                     "accepted binding 的 subject 与 Claim 的候选不一致")
            decision = aggregates.get(binding.binding_decision_id)
            _require(decision is not None,
                     f"claim {claim.claim_id!r} 的 binding "
                     f"{binding.accepted_support_binding_id!r} 指向的决定 "
                     f"{binding.binding_decision_id!r} 不在本节 aggregate 决定内")
            _require(decision.result == "pass",
                     f"claim {claim.claim_id!r} 的 binding 引用了未通过的 aggregate 决定"
                     f"（result={decision.result!r}）：被拒候选是终态，不得产生 accepted binding")
            _require(decision.subject_kind == binding.binding_subject_kind
                     and decision.subject_id == binding.binding_subject_id
                     and decision.draft_revision == draft.draft_revision,
                     "aggregate 决定与 binding 的 subject revision 不一致")
            _require(decision.support_set_digest == binding.support_set_digest,
                     f"claim {claim.claim_id!r} 的 binding 与 aggregate 决定的 "
                     "support-set digest 不一致：两条决定不是同一支撑集")
            entailment = entailments.get(str(getattr(binding, "entailment_decision_id", "") or ""))
            _require(entailment is not None,
                     f"claim {claim.claim_id!r} 的 factual binding "
                     f"{binding.accepted_support_binding_id!r} 没有 entailment 决定："
                     "factual 边必须**恰好两条**决定（aggregate + entailment），"
                     "「没有决定」是缺结论，不是通过")
            _require(entailment.verdict == "entailed",
                     f"claim {claim.claim_id!r} 的 entailment 决定 verdict="
                     f"{entailment.verdict!r}：未 entailed 的候选不得产生 accepted binding")
            _require(entailment.claim_candidate_id == binding.binding_subject_id
                     and entailment.draft_revision == draft.draft_revision,
                     "entailment 决定属于另一个 candidate revision（跨 revision 借证即拒）")
            _require(entailment.binding_decision_id == decision.binding_decision_id,
                     "entailment 决定没有绑定本 subject 通过的 aggregate 决定")
            _require(entailment.support_set_digest == decision.support_set_digest,
                     "entailment 决定与 aggregate 决定的 support-set digest 不同")
        expected_ids = _expected_claim_citations(item, mine)
        actual_ids = tuple(sorted(SS.citation_identity(r) for r in claim.citation_refs))
        _require(len(set(actual_ids)) == len(actual_ids),
                 f"claim {claim.claim_id!r} 的 citation_refs 含重复引用")
        _require(actual_ids == expected_ids,
                 f"claim {claim.claim_id!r} 的 citation 不是它自己 factual binding 指向的权威"
                 f"引用：声明 {list(actual_ids)}，应为 {list(expected_ids)}"
                 "（禁止事后补引用、禁止引用别的候选的来源）")
        by_claim[claim.claim_id] = mine
        verified.append(claim.claim_id)

    # 反向完备：每条被接受的 factual candidate 必须**恰好**有一条定稿 Claim（否则一条已被
    # 门接受的权威事实会静默消失在正文之外）。
    silent = []
    for subject_id, bindings in sorted(factual_by_subject.items()):
        matching = [c.claim_id for c in item.claims if c.claim_candidate_id == subject_id]
        if len(matching) != 1:
            silent.append(f"{subject_id}: {len(matching)} 条 Claim")
    _require(not silent,
             f"section={draft.section_id!r} 的 factual accepted candidate 与定稿 Claim 不一一"
             f"对应：{silent[:4]}（被接受的事实不得静默不呈现）"
             "（支撑 edge → citation 的派生由 claims 侧逐条核验）")
    by_fact: dict[tuple[str, str, str], list[NS.AcceptedSupportBinding]] = {}
    for claim_id, bindings in by_claim.items():
        for binding in bindings:
            key = _binding_fact_key(binding)
            if key is not None:
                by_fact.setdefault(key, []).append(binding)
    return (by_claim, {key: tuple(sorted(vals, key=lambda b: b.accepted_support_binding_id))
                       for key, vals in by_fact.items()})


def _adjudicate_post_gate_issues(item: SectionAssemblyInput,
                                 gate: NS.NarrativeGateResult) -> None:
    """门后裁定：`POST_GATE_ADJUDICATED_GATE_RULES` 里的每条 issue 必须**逐条**在门后成立。

    `required_fact_not_proposed` 是**门前的半条规则**（见 `NS.gate_draft`）：门在候选束上跑，
    结构上看不到门后的 `FactNarrativeDisposition`，因此只能把「该必需事实没有任何 factual
    proposal 引用它」标成 rework 而不是 blocking。把裁定权交回组装器，代价是组装器必须自己
    证明该事实在门后**已经有了去向**——不是「门没拦住就默认放行」。任何一条 issue 定位不到
    恰好一条 FND、或那条 FND 既没 claim 也没有可显示在 Result 里的显式缺口，本节仍然拒组装。
    """

    issues = [i for i in gate.issues
              if str(i.rule_id) in NS.POST_GATE_ADJUDICATED_GATE_RULES]
    if not issues:
        return
    # FND 的集合键是 `(authority_kind, container_identity, fact id)`；门 issue 的 location 是
    # `"<container_id>:<fact_id>"`（不含 authority_kind），因此这里按后两段建索引，并要求命中
    # **恰好一条**（跨 authority kind 撞上同一个裸 id 时必须拒，不能随便挑一条）。
    by_fact: dict[tuple[str, str], list[NS.FactNarrativeDisposition]] = {}
    for disp in item.dispositions:
        by_fact.setdefault((str(disp.container_identity),
                            str(disp.authority_specific_fact_id)), []).append(disp)
    result_gaps = {str(u.unresolved_id) for u in item.result.unresolved}
    for issue in issues:
        location = str(issue.location)
        container_id, sep, fact_id = location.rpartition(":")
        _require(bool(sep) and bool(container_id) and bool(fact_id),
                 f"section={item.draft.section_id!r} 的门后裁定规则 {issue.rule_id!r} 的 "
                 f"location={location!r} 不是 '<container_id>:<fact_id>' 形式：无法定位门后"
                 "去向，fail-closed 拒绝组装")
        matches = by_fact.get((container_id, fact_id), ())
        _require(len(matches) == 1,
                 f"section={item.draft.section_id!r} 的必需事实 {location!r} 没有**恰好一条** "
                 f"FactNarrativeDisposition（找到 {len(matches)} 条）：门后裁定不成立，"
                 "该 rework issue 仍然是拒绝项")
        disp = matches[0]
        if str(disp.disposition) == "claimed":
            continue
        _require(bool(disp.required) and disp.unresolved_id is not None
                 and str(disp.unresolved_id) in result_gaps,
                 f"section={item.draft.section_id!r} 的必需事实 {location!r} 在门后既未 claim，"
                 f"也未成为 Result 里的显式缺口（disposition={disp.disposition!r}, "
                 f"required={bool(disp.required)}, unresolved_id={disp.unresolved_id!r}）："
                 "门前的 rework 提示不得被默认放行")


def _verify_binding(item: SectionAssemblyInput) -> _VerifiedSection:
    draft, result, evaluation = item.draft, item.result, item.evaluation
    binding, gate = item.binding, item.gate_result
    pairs = (
        ("section_result_id", binding.section_result_id, result.section_result_id),
        ("section_draft_id", binding.section_draft_id, draft.draft_id),
        ("evaluation_id", binding.evaluation_id, evaluation.evaluation_id),
        ("narrative_gate_result_id", binding.narrative_gate_result_id, gate.gate_result_id),
    )
    for name, bound, actual in pairs:
        _require(bound == actual,
                 f"binding 的 {name} 与实体不一致：{bound!r} != {actual!r}"
                 f"（Draft/Gate/Evaluation/Result 必须逐字段互绑）")
    _require(evaluation.section_result_id == result.section_result_id,
             "SectionEvaluation 不属于该 SectionResult（评估对象错配）")
    _require(binding.rules_version == evaluation.rules_version,
             f"binding 的 rules_version={binding.rules_version!r} 与 evaluation 的 "
             f"{evaluation.rules_version!r} 不一致")
    _require(binding.gate_version == gate.gate_version,
             f"binding 的 gate_version={binding.gate_version!r} 与 gate 的 "
             f"{gate.gate_version!r} 不一致")
    _require(gate.section_draft_id == draft.draft_id,
             "NarrativeGateResult 不是针对该 draft 的（gate 对象错配）")
    # §七 3：绑定与门必须是**当前**版本，且不能处于 blocking / REWORK 状态。
    _require(binding.rules_version == NS.NARRATIVE_RULES_VERSION,
             f"binding 的 rules_version={binding.rules_version!r} 不是当前 narrative 规则版本 "
             f"{NS.NARRATIVE_RULES_VERSION!r}（旧版本评估不得组装）")
    _require(binding.gate_version == NS.NARRATIVE_GATE_VERSION
             and gate.gate_version == NS.NARRATIVE_GATE_VERSION,
             f"narrative 门版本不是当前 {NS.NARRATIVE_GATE_VERSION!r}"
             f"（binding={binding.gate_version!r}, gate={gate.gate_version!r}）："
             "旧门即便写着通过也不算通过")
    _require(not gate.blocking,
             f"section={draft.section_id!r} 的 narrative 硬门仍为 blocking，不得进入组装")
    # §0.13 / §三 A：门只能把「在候选束上看不到门后对象」的规则标成 rework
    # （`POST_GATE_ADJUDICATED_GATE_RULES`），其余 rework 一律照旧拒绝。被延后的那几条**不是**
    # 提示：裁定者从门换成组装器，组装器必须就**这一条 issue** 在门后对象（FND / Result）上
    # 逐条复核裁定已经成立，否则仍然拒绝。覆盖面不缩，只换裁定者。
    _require(not gate.issues_of("blocking"),
             f"section={draft.section_id!r} 的 narrative 硬门仍有 blocking 级问题"
             f"（{[i.rule_id for i in gate.issues_of('blocking')]}），不得进入组装")
    deferred_rework = sorted({str(i.rule_id) for i in gate.issues_of("rework")}
                             - set(NS.POST_GATE_ADJUDICATED_GATE_RULES))
    _require(not deferred_rework,
             f"section={draft.section_id!r} 的 narrative 硬门仍有 rework 级问题"
             f"（{deferred_rework}），不得进入组装")
    _adjudicate_post_gate_issues(item, gate)
    _require(evaluation.decision not in ("REWORK", "BLOCKED", "FAILED"),
             f"section={draft.section_id!r} 的章级评估结论为 {evaluation.decision!r}，"
             "不得进入组装（REWORK/BLOCKED/FAILED 都不是可发布状态）")
    # §八 3/4：门必须由**组装器自己**沿 authority 在当前 Draft 状态上重算。上面比较的都是
    # 调用方带来的产物字段（binding / gate / evaluation），它们可以被**一起**换成同一份旧的
    # clean 门 —— 只要候选束在算门之后被改过，那份门就不再描述当前内容。因此这里不看调用方
    # 的结论，重算一遍并要求逐字一致（无论重算结果是 clean 还是 blocking）。
    recomputed_gate = NS.gate_draft(draft, item.authority)
    recomputed_blocking = sorted(i.rule_id for i in recomputed_gate.issues
                                 if i.severity == "blocking")
    recomputed_detail = "; ".join(f"{i.rule_id}: {i.detail}" for i in recomputed_gate.issues[:4])
    _require(recomputed_gate.gate_result_id == gate.gate_result_id,
             f"section={draft.section_id!r} 的 narrative 硬门与当前重算结果不一致："
             f"调用方 {gate.gate_result_id!r}，重算 {recomputed_gate.gate_result_id!r}"
             f"（重算发现的 blocking 规则：{recomputed_blocking}；"
             f"重算问题明细：{recomputed_detail or '(无)'}）："
             "门必须在当前 Draft 状态上重新计算，调用方带来的旧门不得作为组装依据")
    _require(not recomputed_gate.blocking,
             f"section={draft.section_id!r} 的 narrative 硬门在重算后仍为 blocking"
             f"（{recomputed_blocking}），不得进入组装")

    # §八 3/4：决定链同样由组装器重算（aggregate 决定 + accepted binding 束）。
    acceptance = _recomputed_decision_chain(item)
    verified_claims: list[str] = []
    by_claim, by_fact = _verify_claim_lineage(item, acceptance, verified=verified_claims)

    # context 边：主体只能是本文的 narrative draft unit，且**不得**携带任何事实身份
    # （context 只验证背景/结构/衔接，不授权事实）。
    units = {u.draft_unit_id: u for u in draft.narrative_draft_units}
    context_ids: list[str] = []
    for binding_item in acceptance.accepted_bindings:
        if str(binding_item.support_semantics) != "context":
            continue
        _require(binding_item.binding_subject_kind == "narrative_draft_unit",
                 "context 支撑边的主体必须是 narrative_draft_unit")
        unit = units.get(binding_item.binding_subject_id)
        _require(unit is not None,
                 f"context accepted binding 的 subject "
                 f"{binding_item.binding_subject_id!r} 不是本文 draft 的草稿单元")
        _require(unit.draft_revision == draft.draft_revision,
                 "context 支撑边的草稿单元属于另一 draft revision")
        _require(not binding_item.entailment_decision_id,
                 "context 支撑边不得携带 entailment 决定（context 不进入语义门）")
        carried = sorted(name for name in ("fact_id", "financial_fact_id", "note_fact_id",
                                           "external_fact_id")
                         if getattr(binding_item, name, None))
        _require(not carried,
                 f"context accepted binding 携带事实身份 {carried}：context 不得授权任何事实")
        context_ids.append(binding_item.accepted_support_binding_id)

    # final Narrative：逐项解析 + 用**同一函数**从当前 Claim 集重算。
    narrative = item.narrative
    for claim_id in narrative.claim_ids:
        _require(claim_id in by_claim,
                 f"final Narrative 引用了本节未定稿的 Claim {claim_id!r}（必须逐项可解析）")
    for binding_id in narrative.context_binding_ids:
        _require(binding_id in context_ids,
                 f"final Narrative 引用了不存在的 context accepted binding {binding_id!r}")
    _require(tuple(sorted(narrative.context_binding_ids)) == tuple(sorted(context_ids)),
             "final Narrative 的 context_binding_ids 与已接受的 context binding 集不相等："
             f"声明 {list(narrative.context_binding_ids)}，已接受 "
             f"{sorted(context_ids)}（context 引用必须逐项成立，多一条/少一条都拒）")
    # §七 2：表格不是模型产物，而是门后**确定性**构造的「指标 × 期间」矩阵。组装器用**同一
    # 函数**在 (已定稿 Claim, 权威输入) 上重算整张表并要求逐表一致 —— 调用方自带一张表
    # （哪怕是照抄的）也不被信任：每个单元格都必须能从本节 Claim 与权威输入复算出来。
    try:
        expected_tables = NS.build_metric_period_tables(
            section_id=draft.section_id, claims=tuple(item.claims), authority=item.authority,
            acceptance=acceptance)
    except NS.NarrativeSchemaError as exc:
        raise ReportAssemblerError(
            f"section={draft.section_id!r} 的表格无法从当前 Claim 集与权威输入重算：{exc}"
            "（表格必须可由唯一实现复算，不得由调用方自带）") from exc
    _require(tuple(narrative.tables) == tuple(expected_tables),
             f"section={draft.section_id!r} 的 final Narrative 表格与从当前 Claim 集重算的结果"
             "不一致：表格只能由唯一实现从**定稿 Claim + 权威输入**派生"
             "（调用方自带的表格一律不信任）")
    tabled = set(NS.tabled_claim_ids(expected_tables))
    # §三 C：自然组织让正文文本成为**模型产物**，组装器不再可能「重算文本」；它能做并且必须
    # 做的是用**同一函数**在当前 Claim 集上复算那五条机械判据（组织器发出前自检、章级评估、
    # 组装器读回，三处同一个实现）。这不是放宽：判据从「文本等于确定性拼接」换成「每个字面
    # 成分都必须逐字可追溯到它自己声明的 Claim」，而**纯确定性**的正文（没有 composed 句）
    # 仍然要求逐段等于重算结果——那条旧保证在还能成立的路径上一条都没有丢。
    composed = [(p.paragraph_id, s.sentence_id) for p in narrative.paragraphs
                for s in p.sentences if s.sentence_kind == "composed"]
    if not composed:
        expected_narrative = NS.build_section_narrative(
            draft=draft,
            # 表格承载的 Claim 不参与正文段落的重算：它们的呈现位置是表格行（§七 2）。
            claims=tuple(c for c in item.claims if c.claim_id not in tabled),
            context_binding_ids=narrative.context_binding_ids, tables=expected_tables)
        _require(narrative.paragraphs == expected_narrative.paragraphs,
                 f"section={draft.section_id!r} 的 final Narrative 段落与从当前 Claim 集重算的"
                 "结果不一致：没有自然组织句时，正文只能由唯一实现从**定稿 Claim** 派生"
                 "（调用方自带段落一律不信任）")
    else:
        NS.verify_section_narrative(
            narrative=narrative, claims=tuple(item.claims),
            accepted_context_binding_ids=tuple(context_ids),
            dispositions=tuple(item.claim_narrative_dispositions))
    # §七 2：同一批事实不得既陈列在表格里、又写成正文句（去重是表格存在的理由之一）。
    in_prose = {cid for para in narrative.paragraphs for sent in para.sentences
                for cid in sent.claim_ids}
    duplicated = sorted(tabled & in_prose)
    _require(not duplicated,
             f"section={draft.section_id!r} 有 Claim 既在表格行里又出现在正文句子里："
             f"{duplicated[:6]}（重复的「指标 × 期间」事实以表格呈现，不得两处各写一遍）")
    for table in narrative.tables:
        for row in table.rows:
            _require(bool(row.claim_ids),
                     f"表格行 {row.row_id!r} 没有任何 Claim 支撑（不得写无来源的正文）")
            filled = [(pos, str(cell)) for pos, cell in enumerate(row.cells)
                      if str(cell).strip()]
            _require(len(filled) == len(row.claim_ids),
                     f"表格行 {row.row_id!r} 的非空单元格数 {len(filled)} 与 Claim 数 "
                     f"{len(row.claim_ids)} 不一致，数字归属不可判定"
                     "（空单元格表达「本列没有已接受的权威事实」，且不携带 Claim）")
            for (pos, cell), cell_claim_id in zip(filled, row.claim_ids):
                claim = next((c for c in item.claims if c.claim_id == cell_claim_id), None)
                _require(claim is not None,
                         f"表格行 {row.row_id!r} 引用了本节未定稿的 Claim {cell_claim_id!r}")
                _require(cell in str(claim.text),
                         f"表格行 {row.row_id!r} 第 {pos} 列单元格 {cell!r} 不是它所对应 Claim "
                         f"{cell_claim_id!r} 文本里的逐字片段：单元格只能复用该 Claim 已经"
                         "承担的权威表面，不得改写、换算或补位")
                allowed = frozenset(NS.scan_numeric_tokens(claim.text))
                unexpected = sorted(t for t in NS.scan_numeric_tokens(cell)
                                    if not NS.numeric_token_in_claims(t, allowed))
                _require(not unexpected,
                         f"表格行 {row.row_id!r} 的单元格含未被该 Claim 授权的数字 "
                         f"{unexpected}：表格数字必须逐字来自 Claim 文本")

    # §五 2/3：正文必须由唯一渲染器从 final Narrative 重算，且 Result 自带的指纹必须自洽。
    rendered = NS.render_final_narrative_markdown(
        draft.title, narrative, tuple(draft.unresolved_projections),
        # `pwr-5`：来源索引与写作器**同一个**入口、同一份 Claim 集重建——两处有一处漏给来源，
        # 下面这条逐字节相等判据立刻失败（来源是正文的一部分，不在正文之外）。
        citation_sources=NS.citation_source_index(tuple(item.claims)))
    _require(rendered == result.markdown,
             f"section={draft.section_id!r} 的正文与 final Narrative 重算结果不一致："
             "正文只能由唯一渲染器生成（调用方自带的 Markdown 一律不信任）"
             f"{_first_difference(result.markdown, rendered)}")
    actual_fingerprint = NS.body_fingerprint_of(rendered)
    _require(result.markdown_fingerprint == actual_fingerprint,
             f"section={draft.section_id!r} 的 markdown_fingerprint 与正文不符："
             f"{result.markdown_fingerprint!r} != {actual_fingerprint!r}")
    # §四：写入侧版本一律取自 **draft 自己留存的值**，不去猜模块常量。
    _require(draft.writer_rules_version and draft.writer_renderer_version,
             f"section={draft.section_id!r} 的 draft 未留存写入侧版本（writer_rules_version/"
             "writer_renderer_version），无法重算 section_version")
    _require(tuple(result.claims) == tuple(item.claims),
             f"section={draft.section_id!r} 的 SectionResult.claims 与本节定稿 Claim 集不相等"
             "（Result 必须携带**恰好**这些 Claim：多一条/少一条都拒）")
    expected_version = SS.derive_section_version(
        result.task_id, tuple(claim.claim_id for claim in result.claims), result.unresolved,
        section_draft_id=draft.draft_id,
        renderer_version=draft.writer_renderer_version,
        rules_version=draft.writer_rules_version,
        dependency_fingerprint=draft.dependency_fingerprint,
        markdown_fingerprint=actual_fingerprint)
    _require(result.section_version == expected_version,
             f"section={draft.section_id!r} 的 section_version 未覆盖正文/Claim/缺口/Draft："
             f"实际 {result.section_version!r}，应为 {expected_version!r}")
    expected_result_id = SS.derive_section_result_id(expected_version)
    _require(result.section_result_id == expected_result_id,
             f"section={draft.section_id!r} 的 section_result_id 与 section_version 不自洽："
             f"{result.section_result_id!r} != {expected_result_id!r}"
             "（内容寻址身份不得与版本脱钩）")
    return _VerifiedSection(
        rendered_markdown=rendered, factual_bindings_by_claim=by_claim,
        context_binding_ids=tuple(sorted(context_ids)), factual_bindings_by_fact=by_fact)


def _verify_retention(item: SectionAssemblyInput,
                      verified: _VerifiedSection) -> dict:
    """保留链：权威 fact → FND → accepted binding → Claim → final Narrative。"""
    draft, result, authority = item.draft, item.result, item.authority
    claims_by_id = {c.claim_id: c for c in item.claims}

    # 1. 写入侧材料处理：draft 自带的 WMPD 必须与 exact manifest 成员一一对应（不静默丢材料）。
    manifest_refs = {entry.member_ref for entry in draft.material_manifest.entries}
    wmpd_refs = {d.member_ref for d in draft.material_dispositions}
    _require(len(wmpd_refs) == len(draft.material_dispositions),
             f"section={draft.section_id!r} 的 WriterMaterialProcessingDisposition 有重复成员")
    _require(manifest_refs == wmpd_refs,
             f"section={draft.section_id!r} 的 manifest 成员与 Writer 处理去向不一一对应："
             f"缺 {sorted(manifest_refs - wmpd_refs)[:4]}，"
             f"多 {sorted(wmpd_refs - manifest_refs)[:4]}"
             "（每个 manifest 成员恰有一条处理去向）")

    # 2. 每条被选中的预验证权威事实恰有一条 FND，且键集**精确相等**（不得缺、多、重）。
    selected_keys = sorted(NS.authority_fact_entries(authority))
    try:
        NS.verify_fnd_key_set(selected_keys, item.dispositions)
    except Exception as exc:  # noqa: BLE001 — 集合不守恒即拒
        raise ReportAssemblerError(
            f"section={draft.section_id!r} 的 FactNarrativeDisposition 集合键与权威选中事实"
            f"不相等（{type(exc).__name__}: {exc}）") from exc
    dispositions = {d.disposition_key: d for d in item.dispositions}
    _require(len(dispositions) == len(item.dispositions),
             f"section={draft.section_id!r} 的 FND 含重复集合键")
    # `NS.required_fact_ids` 返回的是**裸 fact id**（按 producer_kind 从权威自身的类型化字段
    # 派生，与写入侧同一实现）。裸 id 在不同 authority kind 之间并不保证互不相同，因此这里
    # 只在**该 producer_kind 真正会产出 required id 的那几类 kind** 上比对，不跨 kind 硬套：
    # topic_harness 产出 topic_pack + external_snapshot 两类，financial_workflow 只产出
    # financial_pack 一类（附注事实不在返回集内，这是 P5 实现的既有口径，本批不改语义）。
    required_facts = NS.required_fact_ids(authority)
    required_kinds = {
        "topic_harness": ("topic_pack", "external_snapshot"),
        "financial_workflow": ("financial_pack",),
    }.get(getattr(authority, "producer_kind", None), ())

    claim_binding_ids = {claim_id: tuple(b.accepted_support_binding_id for b in bindings)
                         for claim_id, bindings in verified.factual_bindings_by_claim.items()}
    binding_roles = {b.accepted_support_binding_id: str(b.support_role)
                     for b in item.acceptance.accepted_bindings}
    claimed_fact_counts: dict[str, int] = {}
    #: `factual accepted binding id → 声明它的那条 FND`。用于全局闭合：一条支撑边只能有
    #: 一个事实去向（不得被两条 FND 同时认领，也不得无人认领）。
    declared_binding_owner: dict[str, tuple[str, str, str]] = {}
    for key, disp in sorted(dispositions.items()):
        bindings = verified.factual_bindings_by_fact.get(key, ())
        # 该 fact 实际参与的完整、有序 Claim 集（claim_id 升序：与 FND 的确定性排序同口径）。
        claims_here = tuple(sorted(
            claim_id for claim_id, binding_ids in claim_binding_ids.items()
            if any(b.accepted_support_binding_id in binding_ids for b in bindings)))
        # 反向闭合是**按本 fact 定界**的：§6.4.2 明确允许一句话绑定**多条**支撑边，因此一条
        # Claim 的 binding 集与一条 FND 的 binding 集之间是「并集」关系，不是相等关系。这里
        # 传给唯一校验器的映射只取「指向本 fact 的那部分」——把该 Claim 指向**别的事实**的
        # binding 也算进本 fact 的闭合集，会把合法的多事实句子判成非法。全局的两条闭合
        # （每条 binding 恰被一条 FND 声明、Claim 的完整 binding 集被 FND 全集覆盖）在下面
        # 的循环之后统一重算，不会因为这里的定界而变弱。
        binding_ids = {b.accepted_support_binding_id for b in bindings}
        scoped_binding_ids = {
            claim_id: tuple(bid for bid in claim_binding_ids[claim_id] if bid in binding_ids)
            for claim_id in claims_here}
        for claim_id in claims_here:
            _require(claim_id in claims_by_id,
                     f"FND {key} 引用的 Claim {claim_id!r} 不在本节定稿 Claim 内")
        try:
            NS.verify_fnd_reference_sets(
                disp,
                accepted_binding_ids=tuple(sorted(b.accepted_support_binding_id
                                                  for b in bindings)),
                section_claim_ids=claims_here,
                claim_binding_ids=scoped_binding_ids, binding_roles=binding_roles)
        except Exception as exc:  # noqa: BLE001 — 引用集不精确即拒
            raise ReportAssemblerError(
                f"section={draft.section_id!r} 的 FND {key} 引用集不闭合"
                f"（{type(exc).__name__}: {exc}）") from exc
        for binding_id in disp.accepted_binding_ids:
            owner = declared_binding_owner.setdefault(str(binding_id), key)
            _require(owner == key,
                     f"factual accepted binding {binding_id!r} 被两条 FND 同时声明："
                     f"{owner} 与 {key}（一条支撑边只能有一个事实去向）")
        if str(disp.authority_kind) in required_kinds \
                and str(disp.authority_specific_fact_id) in required_facts:
            _require(bool(disp.required),
                     f"section={draft.section_id!r} 的 FND {key} 的 required 标记弱于权威输入："
                     "权威声明为必需的事实不得被登记成非必需")
        if disp.disposition == "claimed":
            claimed_fact_counts[str(disp.authority_specific_fact_id)] = (
                claimed_fact_counts.get(str(disp.authority_specific_fact_id), 0) + 1)

    # 2b. **全局**支撑边闭合（多事实句子下唯一仍然成立的方向）。§6.4.2 允许一句话绑定多条
    # 支撑边，所以一条 Claim 的 binding 集与一条 FND 的 binding 集之间是并集关系；把逐 FND 的
    # 反向闭合当闭合判据会把合法的多事实句子判成非法。这里改成两侧全局闭合：
    #   * 每条 factual accepted binding 恰被一条 FND 认领（上一步已在认领时查重）；
    #   * 每条**路径 A** factual accepted binding 必须被某条 FND 认领（不得无人认领）；
    #   * 每条定稿 Claim 的**完整** factual binding 集必须被（FND ∪ 材料侧去向）覆盖。
    # 两条 factual 路径的去向**不同源**，因此不能合并成一条判据：
    #   * 路径 A（prevalidated authority fact）→ 该边恰被一条 FND 声明（§三 A / §三 E）；
    #   * 路径 B（exact material 派生）→ 该边所属 proposal 出现在**它自己那份 material** 的
    #     WMPD `support_usages` 里。路径 B **没有、也不可能有 FND**：`FactNarrativeDisposition`
    #     的边形状被 `validate_support_edge_shape(..., authorization_path="path_a_prevalidated")`
    #     固定，它必须绑定 authority 专属 **fact id**，并显式禁止 `path_b_material_derived` 边
    #     携带任何 fact identity。把「每条 factual 边都要有 FND」当守恒判据，等于要求材料派生
    #     的边伪造一条预验证事实身份（材料冒充事实）——那是**放宽**而不是收紧，且真实路径 B
    #     永远无法满足。覆盖面不缩：每条边仍然必须**在它自己那条路径上**恰好一条去向。
    factual_accepted = {str(b.accepted_support_binding_id): b
                        for b in item.acceptance.accepted_bindings
                        if str(b.support_semantics) == "factual"}
    path_a_edges = {bid for bid, b in factual_accepted.items()
                    if str(b.authorization_path) == "path_a_prevalidated"}
    path_b_edges = {bid for bid, b in factual_accepted.items()
                    if str(b.authorization_path) == "path_b_material_derived"}
    unclassified = sorted(set(factual_accepted) - path_a_edges - path_b_edges)
    _require(not unclassified,
             f"section={draft.section_id!r} 的 factual 支撑边的授权路径既不是路径 A 也不是"
             f"路径 B：{unclassified[:6]}（factual 边只有这两条路径；第三条路径必然绕过本节"
             "的守恒判据）")
    unclaimed_bindings = sorted(path_a_edges - set(declared_binding_owner))
    _require(not unclaimed_bindings,
             f"section={draft.section_id!r} 的路径 A factual accepted binding 没有任何 FND "
             f"声明它：{unclaimed_bindings[:6]}（预验证权威事实的去向必须唯一且不得静默省略）")
    # 路径 B 的材料侧同源：边 → manifest 成员 → 该成员的 WMPD，三段身份必须逐字对齐。
    # 与 `sections.store._assert_post_gate_cardinality` 检查 (5) 同一判据（此处是组装前的
    # 最后一道，落库前的那道不因此免检）。
    wmpd_by_member = {str(d.member_ref): d for d in draft.material_dispositions}
    path_b_claimed: set[str] = set()
    for binding_id in sorted(path_b_edges):
        binding = factual_accepted[binding_id]
        member_ref = NS.manifest_member_ref(str(binding.authority_container_id),
                                            str(binding.material_id))
        member = draft.material_manifest.entry_for(member_ref)
        _require(member is not None,
                 f"section={draft.section_id!r} 的路径 B 支撑边 {binding_id!r} 引用的 "
                 f"material {str(binding.material_id)!r}"
                 f"（容器 {str(binding.authority_container_id)!r}）不在本节 exact manifest 里："
                 "路径 B 只授权 manifest 内的真实材料")
        wmpd = wmpd_by_member.get(member_ref)
        _require(wmpd is not None,
                 f"section={draft.section_id!r} 的路径 B 支撑边 {binding_id!r} 的 manifest 成员 "
                 f"{member_ref} 没有处理去向（每个成员恰一条 WMPD）")
        _require(str(wmpd.usage) == "used"
                 and str(binding.proposed_support_id) in wmpd.support_usages,
                 f"section={draft.section_id!r} 的路径 B 支撑边 {binding_id!r} 的 proposal "
                 f"{str(binding.proposed_support_id)!r} 不在它自己那份 material 的 WMPD "
                 f"support_usages 里（usage={str(wmpd.usage)!r}）：材料侧去向与支撑边不同源")
        _require(str(binding.source_identity) == str(member.source_identity),
                 f"section={draft.section_id!r} 的路径 B 支撑边 {binding_id!r} 的 source_identity "
                 f"与 manifest 成员不符：{str(binding.source_identity)!r} ≠ "
                 f"{str(member.source_identity)!r}（同一 material_id 不得指向另一份来源）")
        _require(binding.payload_ref is not None
                 and dict(binding.payload_ref) == dict(member.payload_ref),
                 f"section={draft.section_id!r} 的路径 B 支撑边 {binding_id!r} 的 payload_ref "
                 "与 manifest 成员的 exact 载体不符或缺席（§四.1 边必须自闭合）")
        _require(NS.locator_sort_key(binding.locator_ref)
                 == NS.locator_sort_key(member.locator_ref),
                 f"section={draft.section_id!r} 的路径 B 支撑边 {binding_id!r} 的 locator_ref "
                 "与 manifest 成员不符（locator 必须精确落在同一份材料上）")
        path_b_claimed.add(binding_id)
    for claim in item.claims:
        for binding_id in tuple(claim.accepted_binding_ids):
            binding_id = str(binding_id)
            _require(binding_id in declared_binding_owner or binding_id in path_b_claimed,
                     f"Claim {claim.claim_id!r} 的 factual binding {binding_id!r} 在**它自己那条"
                     "路径上**没有任何去向（既无 FND，也不在材料侧 WMPD 的 support_usages 里）："
                     "Claim 不得私有未被登记去向的支撑边")

    # 3. 缺口不得静默消失：required-but-unclaimed 必须留下显式缺口，且缺口两侧一致
    #    （Result 独有项恰是重算出的那一条最终句门 block，见 `_require_gap_conservation`）。
    result_gaps = {u.unresolved_id: u for u in result.unresolved}
    _require_gap_conservation(item, where="保留链")
    for key, disp in sorted(dispositions.items()):
        if disp.disposition == "claimed":
            _require(disp.unresolved_id is None,
                     f"FND {key} 已 claimed 却仍绑定缺口 {disp.unresolved_id!r}")
            continue
        if disp.required:
            _require(disp.unresolved_id is not None,
                     f"required 事实 {key} 的去向是 {disp.disposition!r} 却没有显式缺口："
                     "required 事实不得静默不呈现")
            _require(disp.unresolved_id in result_gaps,
                     f"FND {key} 指向的 unresolved {disp.unresolved_id!r} 不在本节缺口集合内"
                     "（去向挂空）")
        elif disp.unresolved_id is not None:
            _require(disp.unresolved_id in result_gaps,
                     f"FND {key} 指向的 unresolved {disp.unresolved_id!r} 不在本节缺口集合内")

    # 4. 正文引用必须落回定稿 Claim：段落/表格引用的 Claim ID 逐项可解析。
    for claim_id in item.narrative.claim_ids:
        _require(claim_id in claims_by_id,
                 f"final Narrative 引用了不存在的 Claim {claim_id!r}")

    return {
        "claims": len(item.claims),
        "paragraphs": len(item.narrative.paragraphs),
        "tables": len(item.narrative.tables),
        "factual_accepted_bindings": sum(
            1 for b in item.acceptance.accepted_bindings
            if str(b.support_semantics) == "factual"),
        "context_accepted_bindings": len(verified.context_binding_ids),
        "rejected_subjects": len(item.acceptance.rejected_subjects),
        "material_manifest_members": len(manifest_refs),
        "material_dispositions": len(draft.material_dispositions),
        "dispositions": len(item.dispositions),
        "dispositions_claimed": sum(1 for d in item.dispositions
                                    if d.disposition == "claimed"),
        "dispositions_supporting_only": sum(1 for d in item.dispositions
                                            if d.disposition == "supporting_only"),
        "dispositions_not_presented": sum(1 for d in item.dispositions
                                          if d.disposition == "not_presented_with_reason"),
        "unresolved": len(draft.unresolved_ids),
        "authority_facts_selected": len(selected_keys),
    }


def _verify_gap_conservation(item: SectionAssemblyInput) -> dict:
    """§七 1/2/3：以 authority 为根，对 aspect 覆盖视图与权威缺口做**集合守恒**复核。

    保留链（`draft.unresolved_ids == result.unresolved`）对「两侧一致地少」免疫：权威里
    已经是 partial/blocked/not_found 的 aspect，只要把它的缺口从 Draft 与 Result **同时**
    删掉，两边仍然自洽。产物里留下的覆盖视图同样只是调用方的记载。因此这里必须在**权威
    输入**上重新扫描一遍：
    * 覆盖视图必须与重算结果**逐项相等**（状态被改写为 covered、aspect 被增删都会暴露）；
    * 每个投影内、状态 ∈ partial/blocked/not_found 的 aspect，必须仍有一条**身份精确**
      （由写入侧同一实现派生的 `unresolved_id`）的显式缺口，且其登记的原样状态与权威一致。
    """
    draft = item.draft
    result = item.result
    try:
        scan = PW.scan_authority(item.authority, item.task)
    except Exception as exc:  # noqa: BLE001 — 权威输入不可重扫即 fail-closed
        raise ReportAssemblerError(
            f"section={draft.section_id!r} 的权威输入无法重新扫描"
            f"（{type(exc).__name__}: {exc}）：覆盖与缺口守恒不得在无法复核的输入上宣称通过"
        ) from exc

    expected = {a: {"topic_id": str(scan.aspect_topic.get(a, "")),
                    "status": str(scan.aspect_status[a])}
                for a in scan.aspect_status}
    recorded: dict[str, dict] = {}
    for row in draft.coverage_summary.get("aspects", ()) or ():
        aspect_id = str(row.get("aspect_id") or "")
        _require(aspect_id and aspect_id not in recorded,
                 f"section={draft.section_id!r} 的 coverage_summary 含重复/空 aspect_id"
                 f"（{aspect_id!r}）：覆盖视图不得重复计数")
        recorded[aspect_id] = {"topic_id": str(row.get("topic_id") or ""),
                               "status": str(row.get("status") or "")}
    _require(recorded == expected,
             f"section={draft.section_id!r} 的覆盖视图与权威重算结果不守恒："
             f"产物独有 {sorted(set(recorded) - set(expected))[:6]}，"
             f"权威独有 {sorted(set(expected) - set(recorded))[:6]}，"
             f"状态不符 {[k for k in sorted(set(recorded) & set(expected)) if recorded[k] != expected[k]][:6]}"
             "（覆盖状态只能来自权威输入，不得改写）")

    unprojected = {str(a) for a in (draft.coverage_summary.get("unprojected_aspects") or ())}
    gaps_by_id = {u.unresolved_id: u for u in result.unresolved}
    projections = {str(p.get("unresolved_id") or ""): p
                   for p in draft.unresolved_projections}
    # 缺口投影 ⊆ Result 缺口，且 Result 独有项**恰是**本门重算出的那一条最终句门 block
    # （§12.4.4 第 4 步的收窄；判据与定稿协调器、Store 读回共用，见 `_require_gap_conservation`）。
    _require_gap_conservation(item, where="缺口投影与 canonical 缺口集合")
    _require(not (set(projections) - set(gaps_by_id)),
             f"section={draft.section_id!r} 的缺口投影与 canonical 缺口集合不一致："
             f"投影独有 {sorted(set(projections) - set(gaps_by_id))[:6]}")

    # 权威 → 缺口身份由**写入侧的同一实现**派生（`expected_aspect_gap_ids` 内部就是
    # `_aspect_unresolved`）。按 id 精确比对，因此「换一个 aspect 的缺口顶替」「状态被改写」
    # 都不成立；`unprojected_aspects` 由 draft 给出（投影之外，本就不产生缺口）。
    try:
        expected_gaps = PW.expected_aspect_gap_ids(
            item.authority, item.task, unprojected=sorted(unprojected))
    except Exception as exc:  # noqa: BLE001 — 权威→缺口派生失败即 fail-closed
        raise ReportAssemblerError(
            f"section={draft.section_id!r} 的权威缺口身份无法从权威输入派生"
            f"（{type(exc).__name__}: {exc}）：缺口守恒不得在无法复核的输入上宣称通过"
        ) from exc

    missing: list[str] = []
    rewritten: list[str] = []
    for unresolved_id, meta in sorted(expected_gaps.items()):
        if unresolved_id not in gaps_by_id:
            missing.append(f"{meta['aspect_id']}(status={meta['status']},"
                           f"topic={meta['topic_id']})")
            continue
        recorded_status = str(projections.get(unresolved_id, {}).get("authority_status") or "")
        if recorded_status != meta["status"]:
            rewritten.append(f"{meta['aspect_id']}:{recorded_status!r}≠{meta['status']!r}")
    _require(not missing,
             f"section={draft.section_id!r} 的权威缺口在 Draft 与 Result 中同时消失："
             f"{missing[:6]}（权威中的 partial/blocked/not_found 必须留下显式缺口，"
             "不得两侧一起删掉）")
    _require(not rewritten,
             f"section={draft.section_id!r} 的缺口登记的原样状态与权威不符：{rewritten[:6]}"
             "（缺口状态只能原样保留，不得被改写）")

    return {"aspects": len(expected), "non_covered": sum(
        1 for a in expected if expected[a]["status"] in NS.NON_COVERED_ASPECT_STATUSES),
        "unprojected_aspects": len(unprojected), "gaps": len(gaps_by_id)}


def _verify_scope(scope: Sequence[ScopeRequirement],
                  items: Sequence[SectionAssemblyInput]) -> tuple[dict, ...]:
    """DemoScope 内每个 section/topic 都必须有正文或显式缺口，且**集合恰好守恒**。

    §七 1：成员判定不够 —— 「DemoScope 少了两个 topic」与「凭空多出一个 DemoScope 之外的
    topic」都必须被拒，因此这里做的是双向集合相等，而不是单向包含。
    """
    by_section: dict[str, list[SectionAssemblyInput]] = {}
    for item in items:
        by_section.setdefault(item.section_id, []).append(item)
    scope_sections = [req.section_id for req in scope]
    _require(len(set(scope_sections)) == len(scope_sections),
             f"DemoScope 自身含重复 section_id：{sorted(scope_sections)}")
    scope_pairs: set[tuple[str, str]] = set()
    for req in scope:
        for topic_id in req.topic_ids:
            scope_pairs.add((req.section_id, topic_id))

    coverage: list[dict] = []
    covered_pairs: set[tuple[str, str]] = set()
    for req in scope:
        mine = by_section.get(req.section_id, [])
        _require(bool(mine),
                 f"DemoScope 的 section={req.section_id!r} 既没有正文也没有显式缺口")
        claim_topics = {c.topic_id for item in mine for c in item.claims}
        gap_topics = {u.topic_id for item in mine for u in item.result.unresolved}
        for topic_id in req.topic_ids:
            has_body = topic_id in claim_topics
            has_gap = topic_id in gap_topics
            _require(has_body or has_gap,
                     f"DemoScope 的 topic={topic_id!r}（section={req.section_id!r}）"
                     " 既无正文也无显式缺口")
            covered_pairs.add((req.section_id, topic_id))
            coverage.append({
                "section_id": req.section_id, "topic_id": topic_id,
                "has_body": has_body, "has_explicit_gap": has_gap,
                "claims": sum(1 for item in mine for c in item.claims
                              if c.topic_id == topic_id),
                "unresolved": sum(1 for item in mine for u in item.result.unresolved
                                  if u.topic_id == topic_id),
            })
    _require(covered_pairs == scope_pairs,
             "覆盖面与 DemoScope 内容单位不守恒："
             f"缺 {sorted(scope_pairs - covered_pairs)}，多 {sorted(covered_pairs - scope_pairs)}")

    # 反向守恒：产物里不得出现 DemoScope 之外的 section / topic（凭空多出的内容单位）。
    extra_sections = sorted(set(by_section) - {req.section_id for req in scope})
    _require(not extra_sections,
             f"组装输入含 DemoScope 之外的 section：{extra_sections}（内容单位不得凭空多出）")
    produced: set[tuple[str, str]] = set()
    for item in items:
        for claim in item.claims:
            produced.add((item.section_id, claim.topic_id))
        for unresolved in item.result.unresolved:
            # **节级缺口不是内容单位**（§12.4.4 第 4 步）：最终句语义门 block 由
            # `FSF.final_sentence_block_unresolved` 派生，它陈述的是「本节正文未经逐原子语义
            # 核验」——这件事不属于任何 topic，因此 `topic_id` 为空。把它按 `(section, "")` 塞进
            # 这个集合，会让**每一节**只要落一条 B 门 block 就被判成「产物含 DemoScope 之外的
            # section/topic」：一个正确产物被自己的诚实缺口拒掉。
            #
            # 反向守恒要挡的是「凭空多出的**内容单位**」，而一条无 topic 的节级缺口不携带任何
            # 内容单位（它不加正文、不加 Claim、也不改前方逐 topic 的覆盖判定）。因此这里只对
            # 声明了 topic 的缺口做守恒；无 topic 的一律不进这个面。
            if str(unresolved.topic_id):
                produced.add((item.section_id, unresolved.topic_id))
    extra_pairs = sorted(produced - scope_pairs)
    _require(not extra_pairs,
             f"产物含 DemoScope 之外的 section/topic：{extra_pairs}"
             "（不在 DemoScope 里的内容单位不得出现在报告里）")
    return tuple(coverage)


def _numeric_assertions(item: SectionAssemblyInput) -> list[tuple[str, frozenset[str]]]:
    """按**逐 Claim 归属**取出本节每个数字断言的 (claim_id, tokens)（§十三 1/2）。

    归属规则必须是精确的，并且**两种句子都要进这个面**（§三 G：门后事实表面守恒）：

    * 事实句（确定性路径产物）只绑一条 Claim，句子数字只归该 Claim；
    * 自然组织句（§三 C 门后产物）可以同时声明多条 Claim，但**门已经保证**句中每个高风险
      表面（含数字）逐字出现在它自己声明的 Claim 文本里。因此「本句数字 ∩ 该 Claim 文本的
      数字」就是这条 Claim 在本句里真正断言的那部分：交集为空即该 Claim 在本句没有数字断言，
      交集非空则是**可追溯的**归属。这不是「把整句数字复制到句中每条 Claim」（那才会制造
      假冲突），而是门后正文上仍然成立的那一半精确性。
    * 表格事实行的**非空** `cells[i]` 逐字来自 `claim_ids[i]`（空单元格表达「本列期间没有已
      接受的权威事实」，不携带 Claim），因此单元格数字只归它对应的 Claim。

    任何「一条文本绑多条 Claim 而数字来源不可追溯」的形态都无法判定归属 —— 直接 fail-closed。
    """
    draft = item.draft
    claims_by_id = {c.claim_id: c for c in item.claims}
    out: list[tuple[str, frozenset[str]]] = []
    for para in item.narrative.paragraphs:
        for sentence in para.sentences:
            tokens = frozenset(NS.scan_numeric_tokens(sentence.text))
            if sentence.sentence_kind == "composed":
                _require(sentence.claim_ids,
                         f"section={draft.section_id!r} 的自然组织句 {sentence.sentence_id!r} "
                         "没有绑定任何 Claim：无据句不得进入事实表面守恒面")
                for claim_id in sentence.claim_ids:
                    claim = claims_by_id.get(claim_id)
                    _require(claim is not None,
                             f"自然组织句 {sentence.sentence_id!r} 引用了本节未定稿的 Claim "
                             f"{claim_id!r}")
                    own = tokens & frozenset(NS.scan_numeric_tokens(claim.text))
                    if own:
                        out.append((claim_id, own))
                continue
            if sentence.sentence_kind != "factual":
                continue
            _require(len(sentence.claim_ids) == 1,
                     f"section={draft.section_id!r} 的事实句 {sentence.sentence_id!r} 绑定了 "
                     f"{len(sentence.claim_ids)} 条 Claim，数字归属不可判定（禁止把整句数字"
                     "复制到句中每条 Claim）")
            claim_id = sentence.claim_ids[0]
            _require(claim_id in claims_by_id,
                     f"事实句 {sentence.sentence_id!r} 引用了本节未定稿的 Claim {claim_id!r}")
            out.append((claim_id, tokens))
    for table in item.narrative.tables:
        for row in table.rows:
            if not row.claim_ids:
                continue
            filled = [cell for cell in row.cells if str(cell).strip()]
            _require(len(row.claim_ids) == len(filled),
                     f"表格行 {row.row_id!r} 的非空单元格数 {len(filled)} 与 Claim 数 "
                     f"{len(row.claim_ids)} 不一致，数字归属不可判定")
            for claim_id, cell in zip(row.claim_ids, filled):
                out.append((claim_id, frozenset(NS.scan_numeric_tokens(cell))))
    return out


def _cross_section_conflicts(items: Sequence[SectionAssemblyInput],
                             verified: Mapping[str, _VerifiedSection]) -> tuple[dict, ...]:
    """类型化精确冲突：同一权威锚点被两条已写 Claim 以不同数字断言。

    扫描面**同时包含段落句子与表格行**（§十三 1），锚点取该 Claim 自己的 factual accepted
    binding（路径 B 的 material 边没有权威事实坐标，不参与本类冲突）。
    """
    index: dict[tuple[str, str, str], list[tuple[str, str, frozenset]]] = {}
    for item in items:
        section_verified = verified[item.task.task_id]
        for claim_id, tokens in _numeric_assertions(item):
            for binding in section_verified.factual_bindings_by_claim.get(claim_id, ()):
                key = _binding_fact_key(binding)
                if key is None:
                    continue
                index.setdefault(key, []).append((item.section_id, claim_id, tokens))
    conflicts: list[dict] = []
    for key in sorted(index):
        entries = index[key]
        distinct = {tokens for _sec, _cid, tokens in entries}
        if len(distinct) <= 1:
            continue
        sections = sorted({sec for sec, _cid, _tokens in entries})
        conflicts.append({
            "authority_kind": key[0], "authority_container_id": key[1], "fact_id": key[2],
            "sections": sections,
            "claims": sorted({cid for _sec, cid, _tokens in entries}),
            "numeric_variants": sorted(sorted(t) for t in distinct),
        })
    _require(not conflicts,
             "跨章节类型化精确冲突（同一权威锚点被不同数字断言）："
             + str(conflicts[:3]))
    return tuple(conflicts)


def _render_report_markdown(*, company_id: str, report_as_of: str,
                            sections: Sequence[NS.AssembledSection],
                            scope_coverage: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"# {company_id} 信用报告（报告基准日 {report_as_of}）", ""]
    gaps = [x for x in scope_coverage if x["has_explicit_gap"]]
    lines.append(f"本报告覆盖 {len(scope_coverage)} 个 DemoScope 内容单位，"
                 f"其中 {len(gaps)} 个带有显式缺口（缺口按权威状态原样保留）。")
    for section in sections:
        lines.append("")
        lines.append(section.markdown)
    if gaps:
        lines.append("")
        lines.append("## 覆盖与缺口总览")
        for entry in scope_coverage:
            mark = "正文+缺口" if entry["has_body"] and entry["has_explicit_gap"] else (
                "正文" if entry["has_body"] else "仅缺口")
            lines.append(f"- {entry['section_id']}/{entry['topic_id']}：{mark}"
                         f"（claims={entry['claims']}, unresolved={entry['unresolved']}）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _verify_scope_authority(projection: Any, scope: Sequence[ScopeRequirement]) -> None:
    """`scope` 必须**恰好等于**冻结投影选中的内容单位（§六/§七 1）。

    DemoScope 的内容单位由投影给出，不由调用方重新声明；调用方带来的 scope 只允许是
    投影的忠实重复，多一个或少一个 section/topic 都 fail-closed。
    """
    tasks = {}
    for task in projection.report_plan.section_tasks:
        _require(task.section_id not in tasks,
                 f"冻结投影的 plan 里 section={task.section_id!r} 出现多个 SectionTask")
        tasks[task.section_id] = task
    declared = {req.section_id: req for req in scope}
    _require(len(declared) == len(scope),
             f"scope 含重复 section_id：{sorted(req.section_id for req in scope)}")
    missing = sorted(set(tasks) - set(declared))
    extra = sorted(set(declared) - set(tasks))
    _require(not missing and not extra,
             f"scope 与冻结投影的 section 集合不守恒：缺 {missing}，多 {extra}")
    for section_id, req in sorted(declared.items()):
        task = tasks[section_id]
        _require(req.title == task.title,
                 f"scope 中 section={section_id!r} 的标题 {req.title!r} 与冻结投影的 "
                 f"{task.title!r} 不一致")
        _require(tuple(req.topic_ids) == tuple(task.topic_ids),
                 f"scope 中 section={section_id!r} 的 topic 集合 {tuple(req.topic_ids)} "
                 f"与冻结投影的 {tuple(task.topic_ids)} 不一致（内容单位不得改写）")


def _lawful_containers_for(item: SectionAssemblyInput) -> dict[str, tuple[str, ...]]:
    """本节**合法**的权威容器集合：只从**本节自己的权威输入**派生（§六 / §6.2.3）。

    这是与写入侧**独立**的第二份实现。写作相位对同一份权威另有一份实现
    （`pack_writer._authority_container_ids`，Draft 容器清单的唯一写入方）；组装器不得调用
    它 —— 否则「声明」与「核对」同源，检查退化成同义反复。两份实现的一致性由测试在**真实
    权威**上断言，不靠共享代码保证（`test_demo_pack_writer.py` §23 覆盖四类 authority）。

    独立的是**核对的方向**：这里从权威派生，Draft 的声明只是待核对的输入。事实级的准入
    语义（哪条 external fact 属于本节）不是本检查的判据，因此那一条沿用写作侧的读视图
    （`PackWriter.scan_authority`），不重复实现第二遍。

    四类 authority 的容器身份各不相同，分桶后各自成序：
      * `topic_pack` → 每个 Pack 的 `pack_id`。**含**最终没有任何 Claim 用到它的 Pack：
        权威侧给了它就是本节的合法输入，「没被写进正文」不等于「不是本节的权威」；
      * `external_snapshot` → **本节权威读视图**里那些 `external_snapshot` 事实条目的容器
        身份（容器 ≠ 事实身份）。读视图只暴露通过本节自己的门（投影 aspect 等）的事实条目；
        被读视图排除的 external fact 不在本节可引用容器里 —— `ExternalFact.as_of_date` 由
        类型层保证非空，故「期间未定」不是可达形态，可达的排除路径就是这一条。这里复用
        同一个读视图而不是另写一遍排除判据：**重复实现**才是最容易失配的那一处，而本检查要
        防的是「Draft 自报」与「全报告并集」，与复不复用读视图无关；
      * `financial_pack` → 财务 artifact 的 `artifact_id`（内容身份）；
      * `evidence_note` → 附注事实集合的 note 容器身份；附注状态是**缺口**时该桶为空
        （缺口不是容器，不得凭空造一个 id 出来）。
    """
    authority = item.authority
    kind = getattr(authority, "producer_kind", None)
    ordered: dict[str, list[str]] = {name: [] for name in NS.AUTHORITY_KINDS}
    if kind == "topic_harness":
        for pack in authority.pack_set.packs:
            ordered["topic_pack"].append(str(pack.pack_id))
        for entry in PW.scan_authority(authority, item.task).facts:
            if str(entry.authority_kind) == "external_snapshot":
                ordered["external_snapshot"].append(str(entry.container_identity))
    elif kind == "financial_workflow":
        ordered["financial_pack"].append(str(authority.artifact.artifact_id))
        note_facts = getattr(authority, "note_facts", None)
        if note_facts is not None:
            ordered["evidence_note"].append(str(NS.note_container_id(note_facts)))
    else:
        raise ReportAssemblerError(
            f"section={item.task.section_id!r} 的权威输入 producer_kind={kind!r} 未登记，"
            "本节合法权威容器不得凭未知权威构造")
    return {name: tuple(dict.fromkeys(values))
            for name, values in ordered.items() if values}


def _verify_declared_containers(
        items: Sequence[SectionAssemblyInput],
        lawful: Sequence[Mapping[str, tuple[str, ...]]]) -> None:
    """Draft 自报的容器清单必须**恰好等于本节自己的合法容器集合**（§六）。

    写入侧声明的 `Draft.authority_container_ids` 只是**待核对的声明**：`SectionDraft` 自身
    只校验「提案引用的容器 ⊆ 已声明」，因此**多报**的容器在整条自证链里完全不可见 —— 它会被
    组装器原样并进报告容器清单，于是另一节的 Pack、或一个凭空造出的 id，都能冒充本节权威
    （报告容器清单因此既不完整也不可归因）。这里把声明与权威逐节对账：**多余、缺失、重复**
    一律 fail-closed，并指出多余的那个正是哪个章节的合法容器。

    合法但最终没有任何 Claim 用到它的 Pack **必须保留**：权威给了它就是本节的权威输入，
    「没写进正文」不是「不是我的权威」（§四 1：版本只能来自冻结身份根，且必须完整）。
    """
    lawful_sets = [{c for values in kinds.values() for c in values} for kinds in lawful]
    sections = [item.draft.section_id for item in items]
    for index, item in enumerate(items):
        declared = tuple(str(c) for c in item.draft.authority_container_ids)
        repeated = sorted({c for c in declared if declared.count(c) > 1})
        _require(not repeated,
                 f"section={sections[index]!r} 的 Draft.authority_container_ids 含重复容器："
                 f"{repeated}（容器清单是集合，重复即口径不清）")
        missing = sorted(lawful_sets[index] - set(declared))
        extra = sorted(set(declared) - lawful_sets[index])
        if not (missing or extra):
            continue
        impersonated = {
            container: sorted(
                sections[other] for other in range(len(items))
                if other != index and container in lawful_sets[other])
            for container in extra}
        cross = {c: owners for c, owners in impersonated.items() if owners}
        hint = (f"；其中 {sorted(cross)} 分别是别的章节的合法容器"
                f"（{cross}），不得冒充本节权威" if cross else "")
        raise ReportAssemblerError(
            f"section={sections[index]!r} 的 Draft.authority_container_ids 与本节权威输入"
            f"不守恒：缺 {missing}，多 {extra}"
            "（自报的容器清单必须恰好等于**本节**权威容器集合，不得用全报告并集顶替）"
            + hint)


def _authority_container_sets(
        items: Sequence[SectionAssemblyInput]) -> dict[str, frozenset]:
    """**全报告**的权威容器集合，按 authority kind 分桶（= 各节合法集合的按类并集）。

    必须取权威输入本身，不能只取「已写 Claim 的类型化支持边」：一节在权威侧被给了 Pack、但
    一条 Claim 都没写成（全部落下缺口）时支持边为空，若据此断言本节没有权威容器，报告版本
    就会漏掉本次真正提交的 Pack 集合 —— 两个 Pack 集合不同、正文相同的运行会撞上同一个
    `report_version`（§四 1：版本只能来自冻结身份根，且必须完整）。

    报告级并集只用于 `report_version` 的输入面；**逐节归属**由 `_lawful_containers_for` /
    `_verify_declared_containers` 负责，两者不可互相顶替：并集里出现某容器，不证明它是**哪一
    节**的权威，更不证明它**是**任何一节的权威。
    """
    found: dict[str, set[str]] = {kind: set() for kind in NS.AUTHORITY_KINDS}
    for item in items:
        for kind, values in _lawful_containers_for(item).items():
            found[kind].update(values)
    return {kind: frozenset(values) for kind, values in found.items()}


def _verify_support_edges(items: Sequence[SectionAssemblyInput], *,
                          verified: Mapping[str, _VerifiedSection],
                          lawful: Sequence[Mapping[str, tuple[str, ...]]]) -> None:
    """§六：每条支撑边（accepted binding，含被拒 subject）都必须落回**本节**的权威容器。

    与 `_lawful_containers_for` 是同一个坐标系的两端：权威侧给出**本节**的容器集合，支撑边
    必须被它覆盖，既不能指向别的章节的容器，也不能引用不存在的容器。这里刻意用**逐节**集合
    而不是全报告并集：并集会把「A 节的边指向 B 节的容器」判成合法，而那恰好是本节权威可以被
    另一节冒充的那条路。

    factual 边必须有**两条**决定（aggregate + entailment，已在 `_verify_claim_lineage` 逐条
    核对）；context 边必须**没有** entailment 决定、也不得携带任何 fact ID（context 不授权
    事实）。
    """
    for index, item in enumerate(items):
        section_verified = verified[item.task.task_id]
        section = item.draft.section_id
        containers = lawful[index]
        for binding in item.acceptance.accepted_bindings:
            kind = str(binding.authority_kind)
            expected = containers.get(kind)
            if expected is None:
                raise ReportAssemblerError(
                    f"section={section!r} 的支撑边 authority_kind={kind!r} 未登记")
            if str(binding.authority_container_id) not in expected:
                raise ReportAssemblerError(
                    f"section={section!r} 的支撑边指向本节权威之外的容器："
                    f"({kind}, {binding.authority_container_id!r}) 不在 "
                    f"{sorted(expected)} 之内")
            semantics = str(binding.support_semantics)
            if semantics == "factual":
                if not binding.entailment_decision_id:
                    raise ReportAssemblerError(
                        f"section={section!r} 的 factual 支撑边 "
                        f"{binding.accepted_support_binding_id!r} 没有 entailment 决定："
                        "factual 边必须恰好两条决定")
            else:
                if binding.entailment_decision_id:
                    raise ReportAssemblerError(
                        f"section={section!r} 的 context 支撑边 "
                        f"{binding.accepted_support_binding_id!r} 携带 entailment 决定："
                        "context 不进入语义门")
                carried = sorted(name for name in NS.ALL_FACT_FIELDS
                                 if getattr(binding, name, None))
                if carried:
                    raise ReportAssemblerError(
                        f"section={section!r} 的 context 支撑边携带事实身份 {carried}："
                        "context 不得授权事实")
        # 核验覆盖面：每条 accepted binding 必须已在 `_verify_binding` 里被逐条核对过
        # （factual 按它所属 Claim 进了 `factual_bindings_by_claim`，context 进了
        # `context_binding_ids`）。这里只做一次集合级自证，防止将来新增一种语义时被静默漏检。
        checked = {b.accepted_support_binding_id
                   for bindings in section_verified.factual_bindings_by_claim.values()
                   for b in bindings}
        checked |= set(section_verified.context_binding_ids)
        unchecked = sorted(b.accepted_support_binding_id
                           for b in item.acceptance.accepted_bindings
                           if b.accepted_support_binding_id not in checked)
        if unchecked:
            raise ReportAssemblerError(
                f"section={section!r} 的 accepted binding {unchecked[:4]} 未被任何核验覆盖："
                "核验必须覆盖**每一条**支撑边")


def _gate_versions(items: Sequence[SectionAssemblyInput]) -> tuple[str, str]:
    """两道门的**当前**规则版本（取自各节决定，并与模块常量核对）。

    §七 3：旧门即便写着 pass 也不算 —— 决定侧的规则版本必须等于当前版本常量，否则
    「换了一版规则但决定没变」会被同一个 report_version 掩盖。
    """
    binding_versions = {str(d.rules_version) for item in items
                        for d in item.aggregate_decisions}
    # entailment 决定的版本字段是 `rubric_version`（语义门的评分口径），不是 `rules_version`。
    entailment_versions = {str(d.rubric_version) for item in items
                           for d in item.entailment_decisions}
    _require(len(binding_versions) == 1,
             f"各节 aggregate 决定的规则版本不唯一，无法归属唯一报告版本："
             f"{sorted(binding_versions)}")
    version = binding_versions.pop()
    _require(version == CBG.CLAIM_BINDING_GATE_VERSION,
             f"aggregate 决定的规则版本 {version!r} 不是当前 "
             f"{CBG.CLAIM_BINDING_GATE_VERSION!r}（旧门即便写着 pass 也不算）")
    if len(entailment_versions) > 1:
        raise ReportAssemblerError(
            f"各节 entailment 决定的规则版本不唯一：{sorted(entailment_versions)}")
    if entailment_versions:
        entailment_version = entailment_versions.pop()
        _require(entailment_version == CEE.CLAIM_ENTAILMENT_RULES_VERSION,
                 f"entailment 决定的规则版本 {entailment_version!r} 不是当前 "
                 f"{CEE.CLAIM_ENTAILMENT_RULES_VERSION!r}")
    else:
        entailment_version = CEE.CLAIM_ENTAILMENT_RULES_VERSION
    return version, entailment_version


def _build_version_identity(*, projection: Any, version_inputs: ReportVersionInputs,
                            ordered: Sequence[SectionAssemblyInput],
                            report_markdown: str,
                            payload_fingerprint: str,
                            gate_versions: tuple[str, str]) -> BACKBONE.ReportVersionIdentity:
    """构造**唯一**报告版本身份；所有输入先对回权威，再进入冻结口径（§四/§六）。

    §4.6：身份必须同时绑定各节 SectionDraft 身份与「排除自身版本字段后的 assembled
    canonical payload」指纹。后者由 `assemble_report` 在同一批载荷字段上先算好传进来
    （身份是载荷的函数，不能让身份反过来依赖自己）。
    """
    _require(isinstance(version_inputs, ReportVersionInputs),
             "assemble_report 必须显式提供 ReportVersionInputs（版本身份不得由组装器发明）")
    actual_task_ids = tuple(sorted(item.task.task_id for item in ordered))
    containers = _authority_container_sets(ordered)
    actual_packs = containers["topic_pack"]
    actual_financials = containers["financial_pack"]
    prompt_versions = {item.draft.prompt_version for item in ordered}
    model_policies = {item.draft.model_policy for item in ordered}
    _require(len(prompt_versions) == 1,
             f"各节 prompt_version 不一致，无法归属唯一报告版本：{sorted(prompt_versions)}")
    _require(len(model_policies) == 1,
             f"各节 model_policy 不一致，无法归属唯一报告版本：{sorted(model_policies)}")
    prompt_version = prompt_versions.pop()
    model_policy_id = model_policies.pop()
    expected_financial = (sorted(actual_financials)[0] if actual_financials else None)
    _require(len(actual_financials) <= 1,
             f"财务权威 artifact 不唯一：{sorted(actual_financials)}")

    declared = {
        "scope_input_fingerprint": version_inputs.scope_input_fingerprint,
        "plan_id": version_inputs.plan_id,
        "selected_task_ids": tuple(version_inputs.selected_task_ids),
        "topic_pack_ids": tuple(version_inputs.topic_pack_ids),
        "financial_fact_pack_artifact_id": version_inputs.financial_fact_pack_artifact_id,
        "model_policy_id": version_inputs.model_policy_id,
        "prompt_version": version_inputs.prompt_version,
    }
    authoritative = {
        "scope_input_fingerprint": projection.scope_input_fingerprint,
        "plan_id": projection.plan_id,
        "selected_task_ids": actual_task_ids,
        "topic_pack_ids": tuple(sorted(actual_packs)),
        "financial_fact_pack_artifact_id": expected_financial,
        "model_policy_id": model_policy_id,
        "prompt_version": prompt_version,
    }
    for name in declared:
        _require(declared[name] == authoritative[name],
                 f"ReportVersionInputs.{name} 与权威不一致：声明 {declared[name]!r} "
                 f"≠ 权威 {authoritative[name]!r}（版本身份只能来自权威事实）")
    for name in ("scope_input_fingerprint", "plan_id", "prompt_version", "model_policy_id"):
        _require(bool(declared[name]), f"ReportVersionInputs.{name} 不得为空")

    dependency_fingerprint = projection.dependency_fingerprint
    for item in ordered:
        _require(item.draft.dependency_fingerprint == dependency_fingerprint,
                 f"section={item.section_id!r} 的 dependency_fingerprint 与冻结投影不一致："
                 f"{item.draft.dependency_fingerprint!r} != {dependency_fingerprint!r}"
                 "（依赖同族必须逐字节一致）")
    # §4.6：身份的第二个新增依赖是各节 Draft 身份（排序去重；`build` 内会再校验前缀）。
    draft_ids = tuple(sorted({item.draft.draft_id for item in ordered}))
    _require(len(draft_ids) == len(ordered),
             "各节 SectionDraft 身份不唯一，无法归属唯一报告版本："
             f"{sorted(item.draft.draft_id for item in ordered)}")
    return BACKBONE.ReportVersionIdentity.build(
        profile_fingerprint=projection.profile_fingerprint,
        scope_input_fingerprint=projection.scope_input_fingerprint,
        projection_id=projection.projection_id,
        plan_id=projection.plan_id,
        job_id=projection.job_id,
        company_id=projection.company_id,
        report_as_of=projection.report_as_of,
        selected_task_ids=actual_task_ids,
        topic_pack_ids=tuple(sorted(actual_packs)),
        financial_fact_pack_artifact_id=expected_financial,
        contract_fingerprint=projection.contract_fingerprint,
        source_policy_fingerprint=projection.source_policy_fingerprint,
        writing_spec_fingerprint=projection.writing_spec_fingerprint,
        presentation_profile_fingerprint=projection.presentation_profile_fingerprint,
        dependency_fingerprint=dependency_fingerprint,
        body_fingerprint=NS.body_fingerprint_of(report_markdown),
        section_draft_ids=draft_ids,
        assembled_payload_fingerprint=payload_fingerprint,
        narrative_schema_version=NS.NARRATIVE_SCHEMA_VERSION,
        claim_schema_version=SS.CLAIM_SCHEMA_VERSION,
        table_schema_version=SS.TABLE_SCHEMA_VERSION,
        writer_schema_version=PW.WRITER_RENDERER_VERSION,
        assembler_schema_version=ASSEMBLER_VERSION,
        claim_binding_gate_version=gate_versions[0],
        claim_entailment_rules_version=gate_versions[1],
        prompt_version=prompt_version,
        model_policy_id=model_policy_id)


def assemble_report(*, projection: Any, scope: Sequence[ScopeRequirement],
                    section_inputs: Sequence[SectionAssemblyInput],
                    version_inputs: ReportVersionInputs,
                    generated_at: str = "") -> NS.AssembledReport:
    """确定性组装：只读、只复核、只拼装。不做研究、不写正文、不放行。

    `projection` 是**冻结投影**（`planning.demo_scope_schema.DemoPlanningProjection`）：
    job / company / 基准日 / 四类冻结资产指纹 / projection_id / plan_id / 依赖指纹一律
    从它取，调用方不再逐个传字符串（§六）。正文一律由唯一渲染器从 final Narrative 重算
    （§五），final Narrative 本身又由同一函数从定稿 Claim 重算（§0.13）。
    """
    # 权威类型必须是**真的** M930 投影：同形对象不得冒充（§六）。
    from planning.demo_scope_schema import DemoPlanningProjection  # 延迟导入：避免模块环
    _require(isinstance(projection, DemoPlanningProjection),
             "assemble_report 的 projection 必须是 planning.demo_scope_schema."
             f"DemoPlanningProjection，得到 {type(projection).__name__}"
             "（报告身份只能来自冻结投影，不得用同形对象冒充权威）")
    _require(bool(section_inputs), "没有任何章节输入，不得组装报告")
    _verify_scope_authority(projection, scope)
    verified: dict[str, _VerifiedSection] = {}
    seen: set[str] = set()
    for item in section_inputs:
        key = (item.section_id, item.task.task_id)
        _require(key not in seen, f"重复的章节输入：section={key[0]!r} task={key[1]!r}")
        seen.add(key)
        _require(isinstance(item.binding, NS.NarrativeEvaluationBinding),
                 "缺少 NarrativeEvaluationBinding")
        _verify_identity(item, projection=projection)
        verified[item.task.task_id] = _verify_binding(item)
    # 逐节从**本节自己的权威输入**派生合法容器集合（§六）：声明与核对两个方向都走它，
    # 不走全报告并集 —— 并集里出现某容器不证明它是哪一节的权威。
    lawful = [_lawful_containers_for(item) for item in section_inputs]
    _verify_declared_containers(section_inputs, lawful)
    _verify_support_edges(section_inputs, verified=verified, lawful=lawful)
    scope_coverage = _verify_scope(scope, section_inputs)

    titles = {req.section_id: req.title for req in scope}
    order = [req.section_id for req in scope]
    ordered = sorted(section_inputs,
                     key=lambda i: (order.index(i.section_id)
                                    if i.section_id in order else len(order),
                                    i.task.task_id))
    lawful_of = {(item.section_id, item.task.task_id): kinds
                 for item, kinds in zip(section_inputs, lawful)}
    retention: dict[str, Any] = {}
    assembled: list[NS.AssembledSection] = []
    disposition_index: list[dict] = []
    gap_index: list[dict] = []
    containers: list[str] = []
    for item in ordered:
        draft, result = item.draft, item.result
        section_verified = verified[item.task.task_id]
        retention[f"{draft.section_id}:{draft.task_id}"] = _verify_retention(
            item, section_verified)
        # 覆盖/缺口守恒的结论是「拒或通过」，不新增 wire 字段：报告出得来本身就意味着这一门
        # 已经通过（fail-closed），另存一份自证结论只会变成第二套可伪造的口径。
        _verify_gap_conservation(item)
        for disp in item.dispositions:
            disposition_index.append({
                "section_id": draft.section_id, "task_id": draft.task_id,
                "authority_kind": disp.authority_kind,
                "authority_container_id": disp.authority_container_id,
                "fact_id": disp.authority_specific_fact_id, "disposition": disp.disposition,
                "required": disp.required, "claim_ids": list(disp.section_claim_ids),
                "binding_ids": list(disp.accepted_binding_ids),
                "reason_code": disp.reason_code, "unresolved_id": disp.unresolved_id})
        for unresolved in result.unresolved:
            gap_index.append({
                "section_id": draft.section_id, "unresolved_id": unresolved.unresolved_id,
                "topic_id": unresolved.topic_id, "state": unresolved.state,
                "reason_code": unresolved.reason_code, "detail": unresolved.detail})
        # 报告容器清单按「章节顺序 → 本节声明顺序」排布，但**每一条都必须是本节权威派生的
        # 合法容器**。声明集上一步已被证明与本节合法集逐字相等，这里只沿用排布的先后，不引
        # 入第二个真相来源（顺序不参与任何身份计算：`report_version` 用的是排序集合）。
        lawful_here = {c for values in lawful_of[
            (item.section_id, item.task.task_id)].values() for c in values}
        for container in draft.authority_container_ids:
            _require(container in lawful_here,
                     f"section={draft.section_id!r} 的 Draft 容器 {container!r} 不是本节"
                     "权威派生的合法容器（报告容器清单只能来自权威输入）")
            if container not in containers:
                containers.append(container)
        assembled.append(NS.AssembledSection(
            section_id=draft.section_id, title=titles.get(draft.section_id, draft.section_id),
            section_result_id=result.section_result_id, section_draft_id=draft.draft_id,
            evaluation_id=item.evaluation.evaluation_id,
            narrative_gate_result_id=item.gate_result.gate_result_id,
            binding_id=item.binding.binding_id, decision=item.evaluation.decision,
            coverage_summary=dict(draft.coverage_summary),
            claim_ids=tuple(c.claim_id for c in item.claims),
            paragraph_ids=tuple(item.narrative.paragraph_ids),
            table_ids=tuple(item.narrative.table_ids),
            unresolved_ids=tuple(u.unresolved_id for u in result.unresolved),
            markdown=section_verified.rendered_markdown))

    gate_versions = _gate_versions(ordered)
    conflict_index = _cross_section_conflicts(ordered, verified)
    markdown = _render_report_markdown(company_id=projection.company_id,
                                       report_as_of=projection.report_as_of,
                                       sections=assembled, scope_coverage=scope_coverage)
    # §4.6：规范载荷只构造一次 —— 身份指纹与最终产物必须来自**同一批**字段，不得各算一份。
    payload = {
        "schema_version": NS.REPORT_SCHEMA_VERSION,
        "job_id": projection.job_id, "company_id": projection.company_id,
        "report_as_of": projection.report_as_of,
        "profile_fingerprint": projection.profile_fingerprint,
        "projection_id": projection.projection_id,
        "contract_version": projection.contract_version,
        "contract_fingerprint": projection.contract_fingerprint,
        "assembler_version": ASSEMBLER_VERSION, "sections": tuple(assembled),
        "scope_coverage": scope_coverage, "disposition_index": tuple(disposition_index),
        "support_ref_ids": tuple(sorted({
            r.proposed_support_id for item in ordered
            for r in item.draft.proposed_support_refs})),
        "claim_ids": tuple(sorted({c.claim_id for item in ordered for c in item.claims})),
        "gap_index": tuple(gap_index), "conflict_index": conflict_index,
        "authority_container_ids": tuple(containers), "retention": retention,
        "markdown": markdown,
        "dependency_fingerprint": projection.dependency_fingerprint,
    }
    identity = _build_version_identity(
        projection=projection, version_inputs=version_inputs, ordered=ordered,
        report_markdown=markdown,
        payload_fingerprint=NS.assembled_payload_fingerprint(**payload),
        gate_versions=gate_versions)
    return NS.AssembledReport.create(version_identity=identity, **payload,
                                     generated_at=generated_at)


def rebuild_report(report: NS.AssembledReport, *,
                   override_markdown: str | None = None,
                   generated_at: str | None = None) -> NS.AssembledReport:
    """用同一批字段重建（可覆盖正文），用于证明「正文变化 ⇒ report_version 变化」。

    §四：报告版本只来自冻结身份根，所以覆盖正文时**必须**同时重建身份根的
    `body_fingerprint` **与** `assembled_payload_fingerprint`（§4.6）—— 否则「换正文却
    不换版本」会变成第二套版本口径。
    `generated_at` 不在 `identity_body()` 里（它不参与 `report_version`），因此重建时必须
    显式保留原值，否则会在重建路径上被静默清空。
    """
    _require(isinstance(report, NS.AssembledReport),
             f"rebuild_report 只接受 AssembledReport，得到 {type(report).__name__}")
    body = report.identity_body()
    body["generated_at"] = report.generated_at if generated_at is None else generated_at
    if override_markdown is not None:
        _require(isinstance(override_markdown, str), "override_markdown 必须是字符串")
        body["markdown"] = override_markdown
        if override_markdown != report.markdown:
            kw = report.version_identity.fingerprint_body()
            kw["body_fingerprint"] = NS.body_fingerprint_of(override_markdown)
            # 载荷指纹必须按**重算后的**载荷重算：markdown 是纳入字段，漏更新这一项会让
            # 身份声明值与新载荷脱钩，读回时被 `AssembledReport.__post_init__` 拒收。
            kw["assembled_payload_fingerprint"] = NS.assembled_payload_fingerprint(
                **NS.report_payload_kwargs(body))
            body["version_identity"] = BACKBONE.ReportVersionIdentity.build(**kw)
    return NS.AssembledReport.create(**body)

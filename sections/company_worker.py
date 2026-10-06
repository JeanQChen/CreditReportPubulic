"""Phase 4 Batch C — 公司信用研究 Worker（复用 Phase 3 Harness 公共入口）。

确定性流程（任务书 §11）：
    对 SectionTask 的每个必答问题 → build_need → Router.route → harness.run_question
    （复用 Phase 3 公共研究入口，不复制/重写 Harness loop）→ ResearchOutcome
    → convert_question_outcome（COMPLETED/COMPLETED_WITH_GAPS → 引用支持的 Claim(+缺口)；
    UNRESOLVED/NOT_IMPLEMENTED/FAILED → 不写肯定事实，仅 SectionUnresolved）
    → 状态派生（WAITING_HUMAN > SECTION_BLOCKED/JOB_BLOCKED > COMPLETED_WITH_GAPS）
    → 依赖指纹 → 渲染 Markdown → 结构校验 → （可选）原子 commit + current_section 切换。

硬约束：
- 不把 retrieval_observation 写成事实 claim；不把搜索 snippet/URL 当正式引用。
- 不把「未检索到」写成「不存在」（NOT_FOUND_AFTER_SEARCH ≠ 事实不存在）。
- 不针对任何公司/行业/case_id 写专用业务分支。
- 复用 harness.runtime.run_question（含 routing/registry/checkpoint/trace），不自造研究循环。

CLI:
    python -m sections.company_worker --task <task.json> --company <stock> \
        [--company-name <name>] [--ev-db <ev.db>] [--fin-db <fin.db>] \
        [--section-db <sections.db>] [--external-db <ext.db>] [--harness-db <h.db>] \
        [--validate-only] [--store] [--out <markdown>]
"""

from __future__ import annotations

import dataclasses
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from harness import policies as P
from harness import topic_runtime as TR
from planning import schema as PS
from sections import final_sentence_fidelity as FSF
from sections import material_context as MC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_set as PSet
from sections import pack_writer as PW
from sections import research_common as RC
from sections import rules_evaluator as RE
from sections import schema as SS

logger = logging.getLogger("sections.company_worker")

# 本批版本常量（变更必须递增，进依赖指纹 → section_version 派生）。
RENDERER_VERSION = "p4-comp-renderer-v1"
RULES_VERSION = "p4-comp-rules-v1"
PROMPT_VERSION = "research_answer_v1"   # 复用 Phase 3 冻结的研究答案 prompt
WORKER_VERSION = "p4-comp-worker-v1"

# topic_id → 中文标题（renderer 用，与 standard_v2.yaml 契约一致）。
_TOPIC_LABELS: dict[str, str] = {
    "company_identity": "企业基本信息与历史沿革",
    "company_control": "股权结构、控股股东、实际控制人及控制链条",
    "company_subsidiaries": "主要子公司、集团结构与重要关联方",
    "company_business": "主营业务、经营模式、产业链、收入成本毛利构成、客户与供应商集中度",
    "company_competitiveness": "核心竞争力、研发能力、发展计划和在建工程",
    "company_governance": "公司治理、内控、管理层稳定性及主要管理人员履历",
    "company_legal_risks": "重大诉讼、违约、处罚、失信、退市风险、关联交易、股权质押和舆情",
    "company_debt": "债务、授信、发债、金融机构借款和对外担保",
    "company_profit_quality": "非主营业务和利润质量",
    "company_investment": "重大投资、收并购、资产出售、定向增发等影响经营的事件",
    "company_equity_incentive": "股权激励计划及进展",
    "company_credit_summary": "公司层面核心信用优势、风险及其偿债影响",
}


def run_task(task: PS.SectionTask, *, company_id: str, company_name: str = "",
             run_id: str = "", scope: str = "consolidated", currency: str = "CNY",
             purpose: str = "credit_analysis", as_of_date: str | None = None,
             model: str | None = None, external_research_enabled: bool | None = None,
             budget: P.ResearchBudget | None = None,
             audit_dir: str | Path | None = None, registry=None, llm=None,
             build_context=None, route_fn=None, research_question=None,
             external_db: str | None = "data/external_sources.db",
             harness_db: str | None = "data/harness.db",
             checkpoint: bool = True,
             evidence_db: str | None = "data/evidence.db",
             financial_db: str | None = "data/financial_v2.db") -> RC.ResearchWorkerResult:
    """执行公司信用研究 Worker（复用 Harness + 确定性转换 + 渲染）。"""
    return RC.run_task(
        task, section_id="company", topic_labels=_TOPIC_LABELS,
        renderer_version=RENDERER_VERSION, rules_version=RULES_VERSION,
        prompt_version=PROMPT_VERSION, worker_version=WORKER_VERSION,
        company_id=company_id, company_name=company_name, run_id=run_id,
        scope=scope, currency=currency, purpose=purpose, as_of_date=as_of_date,
        model=model, external_research_enabled=external_research_enabled,
        budget=budget, audit_dir=audit_dir, registry=registry, llm=llm,
        build_context=build_context, route_fn=route_fn,
        research_question=research_question,
        external_db=external_db, harness_db=harness_db, checkpoint=checkpoint,
        evidence_db=evidence_db, financial_db=financial_db)


def main(argv: list[str] | None = None) -> int:
    return RC.run_cli(run_task, module_name="sections.company_worker", argv=argv)


# ---------------------------------------------------------------------------
# M930-2 Backbone worker phase 入口（topic_harness 路径）
#
# 上层 `sections.service` **不得**直接调用 `harness.topic_runtime`（计划 §186）：phase 入口
# 由 section Worker 拥有，只接受 composition root 注入的运行时依赖（registry/llm/navigation/
# route_fn/store/source_policy_resolver/trace sink 等），Worker 自身不新建第二套
# Router/Harness/ToolRegistry/Store/runtime。
# ---------------------------------------------------------------------------

BACKBONE_PHASE_VERSION = "m930-2-company-phase-v1"

#: 本 phase 服务的**section 级**研究政策（DESIGN_V2 §「research_policy: workflow | harness |
#: conditional_harness」）。注意它**不是** topic 的 `producer_kind`：冻结 Contract v2 里公司 /
#: 行业 section 的政策字面量是 `harness`，而 `topic_harness` 是 topic/aspect 的生产者类别。
#: 两者混用会让真实投影的 task 永远进不来（本 phase 将不可达），故此处按各自语义分别校验。
SECTION_POLICIES = ("harness", "conditional_harness")
#: 本 phase 只服务由 topic 研究 harness 生产的 aspect（R1-A §三：producer_kind 是显式声明，
#: 不靠 section 反推）。
PRODUCER_KIND = "topic_harness"

#: 财务分支的**section 级**研究政策字面量（冻结 Contract v2 里财务 section 是 `workflow`；
#: 与 `sections.financial_worker.SECTION_RESEARCH_POLICY` 同值，由 focused 测试钉住不许漂移）。
#: 它与 `FINANCIAL_PRODUCER_KIND` 是两根不同的轴：前者决定「这一节走哪条研究相位」，后者是
#: 财务 topic/aspect 的**生产者类别**。混用会让真实投影的财务 task 永远进不来。
FINANCIAL_SECTION_RESEARCH_POLICY = "workflow"
#: 财务分支的 authority 生产者类别（与 `FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND` 同值）。
FINANCIAL_PRODUCER_KIND = "financial_workflow"


class BackbonePhaseError(RuntimeError):
    """Backbone topic phase 的装配/调用错误（fail-closed；与「研究缺口」不是一回事）。"""


@dataclass(frozen=True)
class BackboneTopicPhase:
    """一个 section 的 backbone phase 结果：逐 topic 运行结果 + **已验证** pack set。

    `pack_set` 非空即代表：本 section 每个 topic 都有同 task/公司/报告日/合同/来源策略的
    current 权威 Pack，且材料与事实权威、覆盖记录完整。任何缺口（缺 Pack、异版本 current、
    aspect 投影不符、覆盖记录不完整）都以 `sections.pack_set.PackSetBlocked` fail-closed，
    **不会**降级成「部分可用」——所以本类型没有「pack_set 可为 None」的形态。
    """

    phase_version: str
    section_id: str
    task_id: str
    results: tuple[Any, ...]   # TR.TopicRuntimeResult（只读运行结果，按 task.topic_ids 顺序）
    pack_set: Any              # PSet.VerifiedPackSet（已验证：缺口已 fail-closed）

    def to_dict(self) -> dict:
        return {
            "phase_version": self.phase_version, "section_id": self.section_id,
            "task_id": self.task_id,
            "topics": [{"topic_id": r.identity.topic_id, "pack_id": r.pack_id,
                        "current": bool(r.current), "reused": bool(r.reused)}
                       for r in self.results],
            "pack_ids": [p.pack_id for p in self.pack_set.packs],
            "gate_version": self.pack_set.gate_version,
        }


def run_backbone_topic_phase(
        task: PS.SectionTask, *, section_id: str,
        requirements: Any, run_context: Any,
        dependencies_of: Callable[[Any, Any], Any],
        store: Any) -> BackboneTopicPhase:
    """按 `task.topic_ids` 逐个 topic 跑唯一正式研究链，再用 pack set 门验收。

    `dependencies_of(requirement, run_context)` 必须**每次返回一套全新的运行时依赖**
    （至少 `budget_state` 必须是新的 `TopicBudgetState`）：用量快照进 Pack 内容身份，
    复用已记账的账本会让同一内容的 `pack_id` 漂移，runtime 对此 fail-closed。
    """
    if section_id != task.section_id:
        raise BackbonePhaseError(
            f"phase section_id={section_id!r} 与 task.section_id={task.section_id!r} 不一致")
    if task.research_policy not in SECTION_POLICIES:
        raise BackbonePhaseError(
            f"Backbone topic phase 只服务 section 级 research_policy ∈ {SECTION_POLICIES} 的"
            f" section task；当前 {task.research_policy!r}"
            f"（财务章节走 run_backbone_financial_phase）")
    if run_context.task_id != task.task_id or run_context.section_id != task.section_id:
        raise BackbonePhaseError(
            f"run_context({run_context.task_id!r}/{run_context.section_id!r}) 与 "
            f"task({task.task_id!r}/{task.section_id!r}) 不一致（fail-closed）")

    reqs = tuple(requirements)
    if not reqs:
        raise BackbonePhaseError("requirements 为空：不得用空集合走完 phase 再宣称无缺口")
    by_topic: dict[str, Any] = {}
    for r in reqs:
        if r.topic_id in by_topic:
            raise BackbonePhaseError(f"同一 topic 出现多个 requirement: {r.topic_id}")
        by_topic[r.topic_id] = r
    missing = [t for t in task.topic_ids if t not in by_topic]
    if missing:
        raise BackbonePhaseError(f"task.topic_ids 中缺 requirement: {missing}（fail-closed）")
    unknown = sorted(set(by_topic) - set(task.topic_ids))
    if unknown:
        raise BackbonePhaseError(f"requirements 含 task.topic_ids 之外的 topic: {unknown}")

    # 生产者类别门：本 phase 只跑由 topic 研究 harness 生产的 aspect。判据取**aspect 快照
    # 自己的 producer_kind**（冻结 Contract 的显式声明），不拿 section 政策字符串顶替。
    foreign = {
        f"{topic_id}:{aspect.aspect_id}": aspect.producer_kind
        for topic_id, r in by_topic.items() for aspect in r.aspects
        if aspect.producer_kind != PRODUCER_KIND
    }
    if foreign:
        sample = dict(list(foreign.items())[:5])
        raise BackbonePhaseError(
            f"Backbone topic phase 只服务 producer_kind={PRODUCER_KIND!r} 的 aspect；"
            f"当前含其他生产者类别 {sample}（财务/派生章节各走自己的入口，fail-closed）")

    results: list[Any] = []
    # 顺序严格取 task.topic_ids：pack set 门也按该顺序校验 pack 顺序。
    for topic_id in task.topic_ids:
        requirement = by_topic[topic_id]
        deps = dependencies_of(requirement, run_context)
        if deps is None:
            raise BackbonePhaseError(f"{topic_id}: dependencies_of 未返回运行时依赖")
        results.append(TR.run_topic_requirement(
            requirement, run_context=run_context, dependencies=deps))

    # 只有全部 topic 都产出可验证的 current Pack 才算本 phase 成立；否则显式 block。
    pack_set = PSet.resolve_pack_set(task, reqs, store)
    return BackboneTopicPhase(
        phase_version=BACKBONE_PHASE_VERSION, section_id=section_id,
        task_id=task.task_id, results=tuple(results), pack_set=pack_set)


# ---------------------------------------------------------------------------
# M930-3 Backbone writer phase 入口（门后定稿链）
#
# 本相位是 M930-2 的 `run_backbone_topic_phase` 之后**唯一**的接线点：研究侧已经产出
# 已验证的 current Pack set，本相位把它交给唯一 Writer（P6/P8/P9/P10），再在门后**确定性**
# 派生 `FactNarrativeDisposition` successor、定稿 Claim / final Narrative / `SectionResult`。
#
# 硬边界（越界即缺陷）：
# - Writer **不回写** Pack：本相位只读 Pack 与 manifest，不改任何 Pack/材料/事实对象；
# - `FollowUpNeed` 只在 Harness（`harness.topic_runtime.run_follow_up_needs`）裁决后才执行，
#   「未裁决」不是「可以忽略」，因此无裁决能力即 fail-closed；
# - 本相位不新建第二套 Router / ToolRegistry / Retriever / Store / LLM runtime：LLM 客户端与
#   运行时依赖一律由 composition root 注入；
# - FND 由本协调器**重算**派生（禁止 Writer 自报），并逐条满足组装器 `_verify_retention` 的
#   重算契约；算不出来即 fail-closed，绝不发明缺口、绝不把 required 事实登记成非必需。
# ---------------------------------------------------------------------------

#: 写作相位版本。与 `BACKBONE_PHASE_VERSION`（topic 研究相位）分开命名：两件事共用常量会
#: 让「相位版本」这句话失去所指。本相位尚未落库，故不冒充研究侧 store 的 schema 版本。
BACKBONE_WRITER_PHASE_VERSION = "m930-3-writer-phase-v1"

#: 有界重写轮数：`FollowUpNeed` 最多触发一轮「新 Pack → 基于新 Pack 重写」。把上界写成显式
#: 常量而不是循环条件，是为了让「无界返修」在本相位不可表达。
MAX_FOLLOW_UP_ROUNDS = 1

#: 正式 M930 写作入口的版本（§九）。它与 `BACKBONE_WRITER_PHASE_VERSION` 分开命名：后者是
#: 相位本身的形态版本，前者是「哪一条入口算正式」的版本 —— 同一条相位可以有无 store 的单元
#: 测试入口，那**不是**正式入口。
FORMAL_M930_WRITER_ENTRY_VERSION = "m930-formal-writer-1"

#: 「按 producer_kind，哪些 authority kind 的 fact 会成为 required id」。这与
#: `sections.report_assembler._verify_retention` 的 required 口径**逐字同源**：裸 fact id 在
#: 不同 authority kind 之间不保证互不相同，所以判据必须同时限定 kind。
_REQUIRED_KINDS_BY_PRODUCER: dict[str, tuple[str, ...]] = {
    "topic_harness": ("topic_pack", "external_snapshot"),
    "financial_workflow": ("financial_pack",),
}

#: authority kind → FND 的 authority 专属 fact 字段名（与 `NS.FACT_FIELD_BY_AUTHORITY_KIND`
#: 同值；这里只用于把「目录键里的 fact_id」放回**该 kind 自己**的字段）。
_FACT_FIELD_BY_KIND: dict[str, str] = {
    "topic_pack": "fact_id", "financial_pack": "financial_fact_id",
    "evidence_note": "note_fact_id", "external_snapshot": "external_fact_id",
}

#: authority kind → FND 的 qualification/validation ref 字段名。
_FND_REF_FIELD_BY_KIND: dict[str, str] = {
    "topic_pack": "fact_qualification_decision_id",
    "external_snapshot": "fact_qualification_decision_id",
    "financial_pack": "financial_selection_rule_version",
    "evidence_note": "note_validation_rule_version",
}


@dataclass(frozen=True)
class _AuthorityBranch:
    """本协调器**已登记**的一条权威分支（§三 D：同一条写作主链，按 authority kind 泛化）。

    写作主链只有一条：这里登记的不是「第二套协调器」，而是每类权威**各自已有的约束**——
    「这一节允许哪种 section 级 research_policy」「权威输入必须是哪个真实类型」「这条分支
    有没有 exact `ResearchMaterial` 边界 / current Pack set」。把它们集中声明在一处，是为了
    让「缺字段」不会被 `getattr(..., default)` 静默读成「这一条分支没有这种约束」。
    """

    producer_kind: str
    research_policies: tuple[str, ...]
    authority_type: type
    #: 是否有 exact `ResearchMaterial` 边界（只有 topic Pack 分支有）：它决定 Writer 与语义门
    #: 是否必须拿到由 resolver 解析出的**真实正文**，缺解析器是否 fail-closed。
    material_bound: bool
    #: 是否有 current Pack set：P4 只读复算与 `FollowUpNeed` successor 都建立在它之上。
    pack_set_bound: bool


AUTHORITY_BRANCHES: dict[str, _AuthorityBranch] = {
    PRODUCER_KIND: _AuthorityBranch(
        producer_kind=PRODUCER_KIND, research_policies=SECTION_POLICIES,
        authority_type=PW.TopicPackAuthorityInput, material_bound=True, pack_set_bound=True),
    FINANCIAL_PRODUCER_KIND: _AuthorityBranch(
        producer_kind=FINANCIAL_PRODUCER_KIND,
        research_policies=(FINANCIAL_SECTION_RESEARCH_POLICY,),
        authority_type=PW.FinancialAuthorityInput, material_bound=False, pack_set_bound=False),
}


class BackboneWriterPhaseError(BackbonePhaseError):
    """写作相位的装配/调用错误（fail-closed）。

    它**不是**「研究缺口」也不是「写作缺口」：缺口必须以 `SectionUnresolved`/projection 的
    正式身份出现在产物里，本异常只表示「这一相位不该被放行」。
    """


class UnadjudicatedFollowUpNeeds(BackboneWriterPhaseError):
    """本相位**无法裁决**的 `FollowUpNeed`：诉求随异常带出，按**待裁决提议**留存。

    相位停止的理由（本分支没有 Pack successor / 重写轮数用尽 / 重写轮数为 0）**不是**「模型
    没有提出诉求」。诉求必须原样留在产物里，既不因相位终止而丢失，也**不得**被调用方自动执行
    ——补件的裁决权在 Harness（Contract / 预算 / SourcePolicy），不在写作相位，更不在组合根。
    它同样**不是**缺口：缺口要有 Contract 依据与检索范围，而这里只是一条尚未裁决的诉求。
    """

    def __init__(self, message: str, *, section_id: str,
                 follow_up_needs: tuple = ()) -> None:
        super().__init__(message)
        self.section_id = str(section_id)
        self.follow_up_needs = tuple(follow_up_needs)


def _branch_for_authority(authority: Any) -> _AuthorityBranch:
    """权威输入 → 已登记分支；未登记、或类型不是该分支的真实类型，一律 typed fail-closed。

    两步都不做降级：`producer_kind` 是**声明**，而分支的 `authority_type` 是**证据**——
    只信声明会让形状替身（字段名相同、类型不是真实类型）进入写作相位。
    """
    producer_kind = str(getattr(authority, "producer_kind", "") or "")
    branch = AUTHORITY_BRANCHES.get(producer_kind)
    if branch is None:
        raise BackboneWriterPhaseError(
            f"写作相位不认识 producer_kind={producer_kind!r} 的权威输入"
            f"（已登记 {sorted(AUTHORITY_BRANCHES)}）：四类 authority 各有自己的容器/字段口径，"
            "不得泛化，也不得另开第二条写作链（fail-closed）")
    if not isinstance(authority, branch.authority_type):
        raise BackboneWriterPhaseError(
            f"producer_kind={producer_kind!r} 的权威输入必须是 "
            f"{branch.authority_type.__name__} 的真实实例（得到 {type(authority).__name__}）："
            "形状替身不得进入写作相位（fail-closed）")
    return branch


def _branch_for_task(*, task: PS.SectionTask, authority: Any) -> _AuthorityBranch:
    """section 级 research_policy 与 authority 的 producer_kind 必须落在**同一条**分支上。

    两根轴各有语义（政策 = 这一节走哪条研究相位；producer_kind = 这份权威是哪类权威），
    但它们**不得各自成立而彼此不匹配**——那正会造出「财务政策 + topic 权威」这种混装。
    """
    listed = tuple(sorted(p for b in AUTHORITY_BRANCHES.values() for p in b.research_policies))
    policy = str(getattr(task, "research_policy", "") or "")
    branch = next((b for b in AUTHORITY_BRANCHES.values() if policy in b.research_policies), None)
    if branch is None:
        raise BackboneWriterPhaseError(
            f"Backbone writer phase 只服务 section 级 research_policy ∈ {list(listed)} 的 "
            f"section task；当前 {policy!r}（派生章节仍未启用，不得另开第二条写作链）")
    authority_branch = _branch_for_authority(authority)
    if authority_branch.producer_kind != branch.producer_kind:
        raise BackboneWriterPhaseError(
            f"section={task.section_id!r} 的 research_policy={policy!r} 要求 "
            f"producer_kind={branch.producer_kind!r} 的权威输入，而实际拿到 "
            f"producer_kind={authority_branch.producer_kind!r}"
            "（section 政策与 authority 生产者类别不得互相顶替，fail-closed）")
    if str(getattr(authority, "task_id", "")) != task.task_id \
            or str(getattr(authority, "section_id", "")) != task.section_id:
        raise BackboneWriterPhaseError(
            f"权威输入({getattr(authority, 'task_id', None)!r}/"
            f"{getattr(authority, 'section_id', None)!r}) 与 task"
            f"({task.task_id!r}/{task.section_id!r}) 不一致（fail-closed）")
    return authority_branch


@dataclass(frozen=True)
class BackboneWriterSectionInput:
    """一个 section 进入写作相位所需的一切（全部由 composition root 注入）。"""

    task: PS.SectionTask
    authority: Any                     # PW.TopicPackAuthorityInput / PW.FinancialAuthorityInput
    projection: Any                    # 冻结 Contract 投影
    writing_spec: Any
    presentation_profile: Any
    dependency_fingerprint: str
    #: 本 section 的 requirement 快照（`FollowUpNeed` 裁决与 P4 复算都要用）。可为空——为空时
    #: 一旦 Writer 发出 `FollowUpNeed`，本相位即 fail-closed（不得静默丢弃未裁决的诉求）。
    requirements: tuple[Any, ...] = ()
    run_context: Any = None


@dataclass(frozen=True)
class BackboneSectionWriterOutput:
    """一个 section 的门后定稿结果（含**全部**中间身份，供只读展示与组装器复算）。

    属性名 `draft` / `section_result` / `gate_result` / `narrative` 与
    `sections.rules_evaluator.evaluate_narrative_section` 读取的产物字段一致，因此章级评估
    直接消费本对象，不需要「再包一层」。
    """

    section_id: str
    task_id: str
    draft: Any                      # NS.SectionDraft（门前候选态）
    gate_result: Any                # NS.NarrativeGateResult（门前硬门）
    aggregate_decisions: tuple[Any, ...]
    entailment_decisions: tuple[Any, ...]
    acceptance: Any                 # AB.DraftAcceptance
    claims: tuple[Any, ...]         # SS.SectionClaim（门后定稿）
    narrative: Any                  # NS.SectionNarrative（final Narrative）
    dispositions: tuple[Any, ...]   # NS.FactNarrativeDisposition successor 集
    result: Any                     # SS.SectionResult（current，单向引用 draft）
    evaluation: Any                 # SS.SectionEvaluation（章级评估）
    binding: Any                    # NS.NarrativeEvaluationBinding
    follow_up_needs: tuple[Any, ...]
    prompt_version: str
    model_policy: str
    llm_calls: int
    rewrite_round: int
    follow_up_run_refs: tuple[str, ...]
    #: 每条已定稿 Claim 在 final Narrative 里的**去向**（narr-5 §三 C.5）。它是章级评估的
    #: 依据之一（`selected` 必须在正文里真被引用、`omitted` 必须给封闭理由码），因此必须与
    #: 正文一起离开定稿相位——只留在组织器的 trace 里就等于没人能复算它。
    claim_narrative_dispositions: tuple[Any, ...] = ()
    #: §二 3：本节写作里**被整束拒绝**的候选提案集的 typed 审计（`ProposalSetRejectionRecord`）。
    #: 它只带出已经发生的事实：被拒的束连同完整有序身份与 typed 原因留在这里，**不是**被裁剪
    #: 成「剩下的那些就是原来的提案集」。空元组表示本节每次生成都被采信。
    proposal_set_rejections: tuple[Any, ...] = ()
    #: §六：**被采信**的那一轮里，自己不成立的 `FollowUpNeed` 申请（`FOLLOW_UP_REJECTION_CODES`
    #: 的逐条记录：序号 / 封闭原因码 / 可读原因）。它与 `follow_up_needs` 是同一件事的两半——
    #: 「模型提过什么、其中哪几条不成立」。少了它，一条填错的申请要么让整节连已过门的 Draft
    #: 一起消失（r5 的 financial），要么被悄悄丢掉（= 把「诉求不成立」写成「没有诉求」）。
    follow_up_rejections: tuple[Any, ...] = ()
    #: 自然组织的调用 trace（prompt 版本 / 模型 / 状态 / 句数与 composed 句数）。缺省为空 dict
    #: 表示本节走了**确定性退化路径**（无已定稿 Claim），此时没有组织调用可言。
    disposition_trace: dict = dataclasses.field(default_factory=dict)
    #: §三 E：本节 current 链的**持久化结果**（恰一次提交 + 读回重算）。`state` 只有两个取值：
    #: `"v2_chain_committed_and_verified"`（注入 section store 并往返稳定）或 `"not_injected"`
    #: （组合根没注入 store，本节**没有**落库）。写成显式状态而不是「空 dict 就当成功」，是为了
    #: 让「未落库」在产物里可读——它不得被读成「已落库且无异常」。
    persistence: dict = dataclasses.field(default_factory=dict)
    #: §12.4.4 第 4 步：本节 final Narrative 的最终句语义决定（`nsfid-1`，至多一条）。
    #: 空元组有**两种**互不相同的含义，必须靠 `result.unresolved` 区分，不得合并读：
    #: 「本节没有承载事实的最终句」（本门无对象可核，不阻断）与「本门未能形成决定」
    #: （如实的调用现场原因随 typed block 一起留在缺口里，章节因此 `SECTION_BLOCKED`）。
    #: 它只**带出**已经发生的核验，不改写正文，也不构成发布许可。
    final_sentence_decisions: tuple[Any, ...] = ()
    #: 「本门未能形成决定」的**调用现场记录**（原文，未加工）。与 `final_sentence_decisions`
    #: 是同一件事的两半：前者是**可复算**的核验结果，后者是**只在现场存在**的失败原因，两者
    #: 都不得缺席。它**不是**事实、不是判定，也不参与任何 id 派生；空串表示本门既未失败、
    #: 也没有需要解释的现场（含「本节没有承载事实的最终句」与「已形成决定」两种）。
    final_sentence_gate_note: str = ""
    #: §六/§九：本 section **定稿所用**的权威输入。有界重写发生后它是 FollowUp 产生的 Pack
    #: successor，否则与注入的输入权威同一对象。调用方必须按**它**核对容器/材料/事实身份：
    #: 拿输入权威去核一份基于 successor 写出来的 draft，会得到「容器不在权威集合内」的假失败，
    #: 而这不属于「写作失败」。它只**带出**已经发生的事实，不新增任何可写面。
    authority: Any = None

    @property
    def section_result(self) -> Any:
        """`SectionResult` 的别名（章级评估按该名字取产物）。"""
        return self.result

    @property
    def evaluation_decision(self) -> str:
        return str(getattr(self.evaluation, "decision", "") or "")

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id, "task_id": self.task_id,
            "draft_id": self.draft.draft_id, "draft_revision": self.draft.draft_revision,
            "gate_result_id": self.gate_result.gate_result_id,
            "gate_blocking": bool(self.gate_result.blocking),
            "aggregate_decisions": len(self.aggregate_decisions),
            "entailment_decisions": len(self.entailment_decisions),
            "accepted_bindings": len(self.acceptance.accepted_bindings),
            "rejected_subjects": len(self.acceptance.rejected_subjects),
            "claims": len(self.claims),
            "final_sentence_decisions": len(self.final_sentence_decisions),
            "final_sentence_verdicts": sorted(
                str(getattr(d, "verdict", "") or "") for d in self.final_sentence_decisions),
            "final_sentence_gate_note": self.final_sentence_gate_note,
            "dispositions": len(self.dispositions),
            "narrative_id": self.narrative.narrative_id,
            "paragraphs": len(self.narrative.paragraphs),
            "tables": len(self.narrative.tables),
            "section_result_id": self.result.section_result_id,
            "section_version": self.result.section_version,
            "status": self.result.status,
            "gaps": len(self.result.unresolved),
            "evaluation_id": self.evaluation.evaluation_id,
            "evaluation_decision": self.evaluation_decision,
            "binding_id": self.binding.binding_id,
            "follow_up_needs": [n.need_id for n in self.follow_up_needs],
            "prompt_version": self.prompt_version, "model_policy": self.model_policy,
            "llm_calls": self.llm_calls, "rewrite_round": self.rewrite_round,
            "follow_up_run_refs": list(self.follow_up_run_refs),
            "persistence": dict(self.persistence),
            # §六：本节定稿所用的 Pack 集身份（有界重写后是 successor 集）。只读摘要，不是
            # 第二份权威：完整权威对象由 `authority` 带出。
            "authority_pack_ids": sorted(
                str(p.pack_id) for p in tuple(
                    getattr(getattr(self.authority, "pack_set", None), "packs", ()) or ())),
        }


@dataclass(frozen=True)
class BackboneWriterPhase:
    """写作相位结果：各 section 的门后定稿结果 + `FollowUpNeed` 执行 trace refs。"""

    phase_version: str
    sections: tuple[BackboneSectionWriterOutput, ...]
    #: `TR.FollowUpExecutionResult`（只读运行结果；`trace_refs` 即裁决/执行的可回查引子）。
    follow_up_runs: tuple[Any, ...]

    def to_dict(self) -> dict:
        return {
            "phase_version": self.phase_version,
            "sections": [s.to_dict() for s in self.sections],
            "follow_up_runs": [
                {"follow_up_run_id": r.follow_up_run_id, "rules_version": r.rules_version,
                 "needs": list(r.needs), "new_pack_ids": list(r.new_pack_ids),
                 "trace_refs": list(r.trace_refs)}
                for r in self.follow_up_runs],
        }


@dataclass(frozen=True)
class _FactIdentity:
    """一条权威事实在**门后**的坐标与载体读视图（FND 派生用；不新增第二套语义）。"""

    authority_kind: str
    container_identity: str
    fact_id: str
    owning_pack_id: str
    topic_id: str
    required: bool
    source_identity: str
    provenance_identity: str
    content_fingerprint: str
    #: 该 kind 的 qualification/validation ref 值（topic_pack / external_snapshot 取自事实
    #: **自己**的 `qualification_decision_id`；不借 provenance_identity 顶替）。
    authority_ref_value: str
    material_id: str | None
    payload_ref: dict | None
    locator_ref: dict | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.authority_kind, self.container_identity, self.fact_id)


def _authority_ref_value(kind: str, fact: Any, authority: Any) -> str:
    """该 authority kind 自己的 qualification/validation ref 值。

    topic_pack / external_snapshot 都取事实**自己**的 `qualification_decision_id`；它**不是**
    provenance identity（有 material 载体时后者来自 `ResearchMaterialDisposition`），两者不得
    互相顶替——FND 的 ref 字段要求的是事实自己的资格决定。

    `financial_pack` / `evidence_note` 的 ref 是**容器级**版本（选材规则版本 / 附注核验规则
    版本），它们不属于单条 fact：只能从权威输入自己那份 artifact / 附注事实集读回，不得在此
    写死一个版本常量（写死就等于把「这份权威当时用的规则」替换成「协调器记得的规则」）。
    """
    field = _FND_REF_FIELD_BY_KIND[kind]
    if field == "fact_qualification_decision_id":
        return str(getattr(fact, "qualification_decision_id", "") or "")
    if field == "financial_selection_rule_version":
        return str(getattr(authority.artifact, "fact_selection_rule_version", "") or "")
    if field == "note_validation_rule_version":
        note_facts = authority.note_facts
        if note_facts is None:
            raise BackboneWriterPhaseError(
                "附注事实存在而权威输入没有附注事实集：附注核验规则版本无权威来源"
                "（fail-closed，不得凭空值放行）")
        return str(getattr(note_facts, "validation_rule_version", "") or "")
    return str(getattr(fact, field, "") or "")


def _snapshot_owners(authority: Any) -> dict[str, str]:
    """snapshot 容器身份 → 承载它的 current Pack。容器出现在多个 Pack 即拒（不得任选）。

    财务分支返回空映射，这是由分支声明（`pack_set_bound=False`：财务权威没有 Pack 容器，
    因而也没有 snapshot 容器要归属）得出的**结论**，不是「字段取不到所以读空」。
    """
    branch = _branch_for_authority(authority)
    if not branch.pack_set_bound:
        return {}
    owners: dict[str, str] = {}
    for pack in tuple(authority.pack_set.packs):
        pack_id = str(getattr(pack, "pack_id", "") or "")
        for external in tuple(getattr(pack, "external_facts", ()) or ()):
            container = NS.external_authority_container_id(external)
            previous = owners.setdefault(container, pack_id)
            if previous != pack_id:
                raise BackboneWriterPhaseError(
                    f"snapshot 容器 {container!r} 同时出现在 Pack {previous!r} 与 {pack_id!r}："
                    "external 事实的承载 Pack 无法唯一确定（fail-closed）")
    return owners


def _pack_by_id(authority: Any, pack_id: str) -> Any:
    for pack in tuple(getattr(authority.pack_set, "packs", ()) or ()):
        if str(getattr(pack, "pack_id", "")) == pack_id:
            return pack
    raise BackboneWriterPhaseError(
        f"权威输入里没有 pack_id={pack_id!r} 的 Pack（容器身份必须可解析）")


def _container_of_record(kind: str, container: str, owners: dict[str, str]) -> str:
    """权威事实的**承载容器身份**：按 branch/kind 显式分支，绝不靠 `owners.get(..., "")` 兜底。

    容器身份是四类 authority 各自的既有事实（不是缺省值）：

    * `topic_pack`——容器就是 Pack 自己；
    * `external_snapshot`——快照容器可能横跨多个 Pack，承载 Pack 必须由 `owners` 唯一给出，
      给不出即 fail-closed（这正是「快照事实的承载 Pack 无法确定」）；
    * `financial_pack` / `evidence_note`——财务分支**没有 Pack set**（§三 D），容器是
      artifact / 附注事实集本身，没有「承载 Pack」这个概念可言。这里返回容器而不是塞一个空串，
      是为了让扫描读视图与 `_derive_fact_identity` 对同一事实给出**同一份**身份。
    """
    if kind in ("topic_pack", "financial_pack", "evidence_note"):
        return container
    if kind == "external_snapshot":
        owning = owners.get(container, "")
        if not owning:
            raise BackboneWriterPhaseError(
                f"snapshot 容器 {container!r} 不在任何 current Pack 内：external 事实的承载 "
                "Pack 无法确定（fail-closed）")
        return owning
    raise BackboneWriterPhaseError(
        f"写作相位不认识 authority_kind={kind!r} 的权威事实容器（已登记 "
        f"{sorted(_FACT_FIELD_BY_KIND)}）：四类 authority 各有自己的容器口径，不得泛化")


def _derive_fact_identity(authority: Any, key: tuple[str, str, str], fact: Any, *,
                          owners: dict[str, str]) -> _FactIdentity:
    """派生**未进入扫描读视图**的权威事实的身份/载体（period_unresolved、越范围事实）。

    这是 `sections.pack_writer` 同一套派生入口的再调用（`_material_binding_of` /
    `_pack_material_index` / `_fact_surface_fingerprint` / `NS.citation_source_identity`），
    不另写一套「材料绑定」或「内容指纹」语义：同一事实在扫描内外的身份必须同源。
    """
    kind, container, fact_id = (str(key[0]), str(key[1]), str(key[2]))
    if kind == "topic_pack":
        pack = _pack_by_id(authority, container)
        refs = tuple(NS.authoritative_citation_refs("topic_pack", fact) or ())
        if not refs:
            raise BackboneWriterPhaseError(
                f"权威事实 {key} 没有权威引用，无法派生 source/provenance/content 身份"
                "（不得手写、不得留空）")
        # 这条事实**不在**扫描读视图里（period_unresolved / 越出本节 aspect 范围）：它没有
        # 支撑边，材料绑定只是可选血缘，因此引用锚点歧义不得在这里升格成整节不写
        # （扫描侧仍恒为默认的严格口径）。
        material_id, payload_ref = PW._material_binding_of(
            authority, pack, container, refs[0], fact, for_support_edge=False)
        if material_id is not None:
            material, rmd = PW._pack_material_index(pack)[material_id]
            source_identity = str(getattr(material, "source_identity", "") or "")
            provenance_identity = str(getattr(rmd, "provenance_identity", "") or "")
            content_fingerprint = str(getattr(material, "content_hash", "") or "")
        else:
            source_identity = NS.citation_source_identity(refs[0])
            provenance_identity = str(getattr(fact, "qualification_decision_id", "") or "")
            content_fingerprint = PW._fact_surface_fingerprint(
                "topic_pack", container, fact_id,
                NS.authority_numeric_texts("topic_pack", fact))
        return _FactIdentity(
            authority_kind=kind, container_identity=container, fact_id=fact_id,
            owning_pack_id=container, topic_id=str(getattr(pack, "topic_id", "") or ""),
            required=False, source_identity=source_identity,
            provenance_identity=provenance_identity, content_fingerprint=content_fingerprint,
            authority_ref_value=str(getattr(fact, "qualification_decision_id", "") or ""),
            # topic_pack 的定位由 material payload 承担：FND 不得携带 locator_ref（该 kind
            # 的边形状由 `validate_support_edge_shape` 唯一裁决，这里不另立口径）。
            material_id=material_id, payload_ref=payload_ref, locator_ref=None)
    if kind == "external_snapshot":
        owning = _container_of_record(kind, container, owners)
        pack = _pack_by_id(authority, owning)
        return _FactIdentity(
            authority_kind=kind, container_identity=container, fact_id=fact_id,
            owning_pack_id=owning, topic_id=str(getattr(pack, "topic_id", "") or ""),
            required=False, source_identity=container,
            provenance_identity=str(getattr(fact, "qualification_decision_id", "") or ""),
            content_fingerprint=str(getattr(fact, "content_hash", "") or ""),
            authority_ref_value=str(getattr(fact, "qualification_decision_id", "") or ""),
            material_id=None, payload_ref=PW._snapshot_payload_ref(fact),
            locator_ref=PW._external_locator_ref(fact))
    if kind in ("financial_pack", "evidence_note"):
        # 财务分支：容器是 artifact / note 事实集（不是 Pack），事实没有 material 载体，
        # 其「定位」由附注自己的权威 locator 承担（`NS.authoritative_locator`），与扫描读视图
        # 里同一 kind 的条目**同一套派生入口**，因此两侧身份同源。
        citation = (PW._citation_from_mapping(getattr(fact, "citation", None))
                    if kind == "financial_pack" else PW._note_citation(fact))
        return _FactIdentity(
            authority_kind=kind, container_identity=container, fact_id=fact_id,
            owning_pack_id=container, topic_id=authority.topic_for_fact(fact_id),
            required=False,
            source_identity=NS.citation_source_identity(citation),
            provenance_identity=f"{kind}:{container}",
            content_fingerprint=PW._fact_surface_fingerprint(
                kind, container, fact_id, NS.authority_numeric_texts(kind, fact)),
            authority_ref_value=_authority_ref_value(kind, fact, authority),
            material_id=None, payload_ref=None,
            locator_ref=(NS.authoritative_locator(kind, fact) if kind == "evidence_note"
                         else None))
    raise BackboneWriterPhaseError(
        f"写作相位不认识 authority_kind={kind!r} 的权威事实（已登记 "
        f"{sorted(_FACT_FIELD_BY_KIND)}）：四类 authority 各有自己的容器/字段口径，不得泛化")


def _authority_fact_catalog(authority: Any, task: PS.SectionTask,
                            scan: Any) -> dict[tuple[str, str, str], _FactIdentity]:
    """本节权威事实目录：**精确等于** `NS.authority_fact_entries(authority)` 的键集。

    组装器（`_verify_retention`）按 `NS.authority_fact_entries` 重算集合键，其中包含扫描读
    视图**没有**收录的两类事实（越出本节 aspect 范围、以及 `period_unresolved`）。它们同样是
    「被选中的预验证权威事实」，必须逐条有去向；漏掉它们等于静默丢事实。
    """
    required_kinds = _REQUIRED_KINDS_BY_PRODUCER.get(str(authority.producer_kind), ())
    required_facts = NS.required_fact_ids(authority)
    owners = _snapshot_owners(authority)
    catalog = NS.authority_fact_entries(authority)

    out: dict[tuple[str, str, str], _FactIdentity] = {}
    for entry in scan.facts:
        kind = str(entry.authority_kind)
        container = str(entry.container_identity)
        owning_pack = _container_of_record(kind, container, owners)
        out[entry.key] = _FactIdentity(
            authority_kind=kind, container_identity=container, fact_id=str(entry.fact_id),
            owning_pack_id=owning_pack, topic_id=str(entry.topic_id),
            required=bool(entry.required) and kind in required_kinds,
            source_identity=str(entry.source_identity),
            provenance_identity=str(entry.provenance_identity),
            content_fingerprint=str(entry.content_fingerprint),
            authority_ref_value=_authority_ref_value(kind, catalog[entry.key], authority),
            material_id=(str(entry.material_id) if entry.material_id else None),
            payload_ref=(dict(entry.payload_ref) if entry.payload_ref else None),
            locator_ref=(dict(entry.locator_ref) if entry.locator_ref else None))

    for key, fact in sorted(catalog.items()):
        if key in out:
            continue
        identity = _derive_fact_identity(authority, key, fact, owners=owners)
        # required 的判据与组装器逐字同源：既要在权威声明的 required id 集里，也要落在该
        # producer_kind 真正会产出 required id 的那几类 authority kind 上（裸 id 不跨 kind 通用）。
        out[key] = dataclasses.replace(
            identity,
            required=str(key[2]) in required_facts and str(key[0]) in required_kinds)

    missing = sorted(set(catalog) - set(out))
    extra = sorted(set(out) - set(catalog))
    if missing or extra:
        raise BackboneWriterPhaseError(
            f"本节的权威事实目录与权威输入不相等：缺 {missing[:6]}，多 {extra[:6]}"
            "（FND 的集合键必须精确覆盖被选中的权威事实）")
    return out


def _binding_fact_key(binding: Any) -> tuple[str, str, str] | None:
    """accepted binding → 它指向的权威事实坐标；路径 B（无事实身份）返回 `None`。

    坐标口径只有 `NS.binding_fact_key` 一份实现：事实身份按 authority kind 取**该 kind 自己的**
    字段（一律读 `fact_id` 会让财务/附注/外部支撑边拿到空 key，把「有事实」静默判成「没有
    事实」），再补上 binding 自己声明的 kind 与容器。
    """
    return NS.binding_fact_key(binding)


def _required_unclaimed_unresolved_id(*, draft_gaps: dict[str, Any], identity: _FactIdentity,
                                      fact_topic: str) -> str:
    """required 事实未被任何 Claim 呈现时，必须指向**已存在**的显式缺口。

    `draft.unresolved_ids` 与 `SectionResult.unresolved` 被组装器要求逐项相等，因此本相位
    **不能**凭空造缺口（造缺口就是伪造完整性、并把研究侧缺口守恒打破）。目前唯一可安全引用
    的既有缺口是 Writer 为「期间无法证明」事实登记的 period 缺口。除此之外一律 fail-closed，
    并如实报告该限制：写入侧对「本节内 required 事实未被任何候选引用」只出 rework 级
    `required_fact_not_proposed` 提示，不登记缺口。
    """
    if not fact_topic:
        raise BackboneWriterPhaseError(
            f"required 事实 {identity.key} 未被呈现，且无法从权威派生其归属 topic："
            "不得猜主题去找缺口（fail-closed）")
    candidates = sorted(
        unresolved_id for unresolved_id, gap in draft_gaps.items()
        if str(getattr(gap, "reason_code", "")) == PW.PERIOD_UNRESOLVED_REASON
        and str(getattr(gap, "topic_id", "")) == fact_topic)
    if len(candidates) != 1:
        raise BackboneWriterPhaseError(
            f"required 事实 {identity.key} 未被任何 Claim 呈现，但本节没有**恰好一条**可引用的"
            f"显式缺口（topic={fact_topic!r}，候选 {candidates}）：本相位不得凭空造缺口，"
            "而应按原提示词把「写入侧缺少 required-unclaimed 的缺口登记」作为发现上报")
    return candidates[0]


def _fact_narrative_dispositions(
        *, authority: Any, draft: Any, outcome: Any, acceptance: Any, claims: tuple[Any, ...],
        scan: Any, catalog: dict[tuple[str, str, str], _FactIdentity]) -> tuple[Any, ...]:
    """门后**确定性**派生完整 FND successor 集（禁止 Writer 自报）。

    派生口径与组装器 `sections.report_assembler._verify_retention` 的重算**逐项同源**：
    `claimed` 的 `accepted_binding_ids` 是该事实实际参与的完整有序 binding 集，
    `section_claim_ids` 是这些 binding 所属的 Claim 集（升序）；未呈现的事实只允许
    `not_presented_with_reason` + 登记过的理由码（`supporting_only` 需要一条「不作为任何
    Claim 主权威」的 corroborating 边，本链不产生这种边，故不可达——不发明它）。
    """
    required_kinds = _REQUIRED_KINDS_BY_PRODUCER.get(str(authority.producer_kind), ())
    draft_gaps = {u.unresolved_id: u for u in outcome.unresolved}

    by_fact: dict[tuple[str, str, str], list[Any]] = {}
    for claim in claims:
        for binding_id in tuple(claim.accepted_binding_ids):
            binding = next((b for b in acceptance.accepted_bindings
                            if b.accepted_support_binding_id == binding_id), None)
            if binding is None:
                raise BackboneWriterPhaseError(
                    f"Claim {claim.claim_id!r} 声明的 binding {binding_id!r} 不在本次 accepted "
                    "binding 集内（门后身份不得悬空）")
            key = _binding_fact_key(binding)
            if key is not None:
                by_fact.setdefault(key, []).append(binding)

    excluded_pairs = {(str(c), str(f)) for c, f, _t in scan.excluded_facts}
    period_pairs = {(str(c), str(f)) for c, f, _t in scan.period_unresolved}
    period_topic = {(str(c), str(f)): str(t) for c, f, t in scan.period_unresolved}

    dispositions: list[Any] = []
    for key, identity in sorted(catalog.items()):
        fact_field = _FACT_FIELD_BY_KIND[identity.authority_kind]
        ref_field = _FND_REF_FIELD_BY_KIND[identity.authority_kind]
        if not identity.authority_ref_value:
            raise BackboneWriterPhaseError(
                f"权威事实 {key} 的 {ref_field} 为空：FND 的 authority 专属 ref 必须来自权威"
                "自己的类型化字段（不得手写、不得留空）")
        common = {
            "authority_kind": identity.authority_kind,
            "authority_container_id": identity.container_identity,
            "required": identity.required,
            "source_identity": identity.source_identity,
            "provenance_identity": identity.provenance_identity,
            "content_fingerprint": identity.content_fingerprint,
            "pack_id": identity.owning_pack_id,
            fact_field: identity.fact_id,
            ref_field: identity.authority_ref_value,
            "material_id": identity.material_id,
            "payload_ref": identity.payload_ref,
            "locator_ref": identity.locator_ref,
        }
        bindings = tuple(sorted(by_fact.get(key, ()),
                                key=lambda b: b.accepted_support_binding_id))
        if bindings:
            accepted_ids = tuple(b.accepted_support_binding_id for b in bindings)
            claim_ids = tuple(sorted(
                claim.claim_id for claim in claims
                if any(bid in tuple(claim.accepted_binding_ids) for bid in accepted_ids)))
            dispositions.append(NS.FactNarrativeDisposition.create(
                disposition="claimed", accepted_binding_ids=accepted_ids,
                section_claim_ids=claim_ids, reason_code=None, unresolved_id=None, **common))
            continue

        pair = (identity.owning_pack_id, identity.fact_id)
        if pair in excluded_pairs:
            reason_code = "outside_narrative_scope"
        elif pair in period_pairs:
            reason_code = "blocked_by_authority_state"
        else:
            reason_code = "not_required_for_selected_aspects"
        unresolved_id = None
        if identity.required:
            unresolved_id = _required_unclaimed_unresolved_id(
                draft_gaps=draft_gaps, identity=identity,
                fact_topic=period_topic.get(pair, identity.topic_id))
        dispositions.append(NS.FactNarrativeDisposition.create(
            disposition="not_presented_with_reason", accepted_binding_ids=(),
            section_claim_ids=(), reason_code=reason_code, unresolved_id=unresolved_id,
            **common))

    declared = tuple(dispositions)
    NS.verify_fnd_key_set(sorted(NS.authority_fact_entries(authority)), declared)
    return declared


def _finalize_section(*, section_input: BackboneWriterSectionInput, authority: Any,
                      outcome: Any, chain: Any, rewrite_round: int,
                      follow_up_run_refs: tuple[str, ...], evaluated_at: str,
                      llm_client: Any, llm_calls_before: int,
                      allow_llm_evaluator: bool,
                      allow_targeted_rework: bool,
                      final_sentence_llm_client: Any = None) -> BackboneSectionWriterOutput:
    """门后定稿：候选束 → 定稿 Claim / final Narrative → 确定性 FND → `SectionResult` →
    章级评估 + `NarrativeEvaluationBinding`。

    顺序是硬约束（§0.13）：FND 在两道门**之后**由本协调器派生；`SectionResult` **最后**形成并
    **单向引用** draft；章级评估只吃 draft/result/gate 三样，不吃 Pack、不改正文。
    """
    draft, task = outcome.draft, section_input.task
    scan = PW.scan_authority(authority, task)

    # 1) 门后定稿 Claim（与组装器共用**同一**实现 `NS.finalize_section_claims`）。
    claims = tuple(NS.finalize_section_claims(
        task=task, authority=authority, draft=draft, acceptance=chain.acceptance))
    # 1b) **最终句读回**（O-12 / `srsc-1`）：定稿 Claim 里不得留下「只由同类较旧材料支撑的
    #     无期间当前断言」。判据与门前同源（`PW.verify_finalized_current_state_scope` 复用
    #     `SRS.unqualified_current_state`），这里读的却是**定稿 Claim 与它自己那份 complete
    #     accepted 集**——两处结论不一致只可能来自上游某条边被换掉或被漏记，因此必须在
    #     `SectionResult` 形成之前当场停。组织那一侧的时点纪律另由门 `ng-13`（第 12 条）与
    #     组织器资产 `norg-6` 承担：那一侧改的是**时点措辞**，不是边。
    try:
        PW.verify_finalized_current_state_scope(
            authority=authority, claims=claims, acceptance=chain.acceptance,
            manifest=draft.material_manifest,
            material_context=outcome.material_context)
    except PW.PackWriterError as error:
        raise BackboneWriterPhaseError(str(error)) from error
    # 2) final Narrative：**门后自然组织**（§三 C）。输入只有已定稿 Claim、已接受的 context
    #    支撑、typed 缺口与两份规格；组织器读不到门前候选束，因此不可能绕过两道门。它复用
    #    **同一个** llm_client（不是第二个 writer/LLM runtime），并且必须在发出前通过
    #    `NarrativeSchemaError` 那套确定性核验。退化路径（本节没有任何已定稿 Claim）由
    #    `build_final_narrative` 内部走确定性构造器，**不**发起这次调用。
    context_bindings = tuple(
        b for b in chain.acceptance.accepted_bindings
        if str(b.support_semantics) == "context")
    context_binding_ids = tuple(sorted(
        b.accepted_support_binding_id for b in context_bindings))
    narrative, narrative_trace = NO.build_final_narrative(
        claims=claims, accepted_context_bindings=context_bindings,
        unresolved=tuple(outcome.unresolved), writing_spec=section_input.writing_spec,
        presentation_profile=section_input.presentation_profile,
        identity={"task_id": str(task.task_id), "section_id": str(task.section_id),
                  "section_draft_id": str(draft.draft_id),
                  "draft_revision": str(draft.draft_revision)},
        # §七 2：重复的「指标 × 期间」权威事实先成**表格**（确定性构造，不经模型），
        # 文字只承担表格之外的说明。表格由已定稿 Claim + 权威输入复算，组装器用同一函数重算。
        tables=NS.build_metric_period_tables(
            section_id=str(task.section_id), claims=claims, authority=authority,
            acceptance=chain.acceptance),
        # §二（`norg-4`）：门后**组织线索**——主题键由每条 Claim 的 factual 已接受绑定的材料标题
        # 路径确定性派生（`NO.claim_theme_keys`）。它只决定段落怎么分，不进正文、不改任何判据。
        accepted_bindings=tuple(chain.acceptance.accepted_bindings),
        # §三 C（`norg-8` / `orgmc-1`）：门后改写要有**材料正文**可依。只投草稿台账点名过的成员、
        # 逐成员核身份（`NO.organizer_material_context`）；纯 FinancialFactPack 的财务节草稿单元
        # 声明的是权威事实出处，因此这里对它天然是空表——不为凑非空输入伪造 Pack 材料。
        material_context=outcome.material_context,
        llm_client=llm_client, draft=draft)
    claim_dispositions = tuple(
        NS.ClaimNarrativeDisposition.from_dict(x)
        for x in narrative_trace.get("claim_narrative_dispositions", ()))
    # 3) FND successor：**确定性**派生，禁止 Writer 自报。
    catalog = _authority_fact_catalog(authority, task, scan)
    dispositions = _fact_narrative_dispositions(
        authority=authority, draft=draft, outcome=outcome, acceptance=chain.acceptance,
        claims=claims, scan=scan, catalog=catalog)
    # 3b) §12.4.4 第 4 步：**最终句语义门**（`nsfid-1`）。它核验的对象是**最终句**，因此只能在
    #     final Narrative 成形之后、current Result 封口之前跑——位置本身就是「不得提前」的约束。
    #     本章节里调用它**至多**一次，产出**至多**一条决定（一个 draft revision 恰一条）。
    #
    #     「没有决定」与「判为不通过」是两件事，各自记账：调用失败 / 输出不可解析而**未能形成**
    #     决定时，如实落成 `final_sentence_decision_missing` 的 typed block，**不**伪造一条
    #     `rejected`；决定判为 `rejected` 时，逐原子读数（含哪一条原子在已接受 Claim 集里没有
    #     位置）留在决定自己身上。四档（缺失 / 多头 / 过期 / rejected）的判据**不在**这里——
    #     它由 wire 层唯一实现 `NS.final_sentence_gate_state` 给出，组装器与 Store 读回用**同一份**
    #     重算，因此同一个缺口集合不可能读出两种状态。
    #
    #     block 只登记缺口、**不改写正文**：正文按已定稿 Claim 原样保留并被标为未核验，而不是
    #     「先把可疑句删掉、再当已核验」地洗白。章节状态也不由本门宣称——block 的后果字段是
    #     `SECTION_BLOCKED`，状态仍由唯一口径 `PW._derive_status` 从缺口派生。
    if final_sentence_llm_client is None:
        raise BackboneWriterPhaseError(
            "定稿路径必须注入最终句语义门的 llm_client：缺注入时本门无法形成决定，"
            "而「没有决定」不得被读成「已核验」（fail-closed）")
    sentence_gate_calls: list[int] = []
    sentence_decisions, undecided_reason = FSF.drive_final_sentence_gate(
        narrative=narrative, claims=claims,
        accepted_bindings=tuple(chain.acceptance.accepted_bindings),
        # `authority` 传的是**确定性读视图**（`scan`），不是权威输入本身：本门与 P8/P9 共用
        # `CBG.authority_fact_table` / `CBG.resolve_proposal_sources` 解析支撑边，而那条入口
        # 明确拒绝「没有 `.facts` 条目序列」的对象（不扫描权威、不构造材料）。传原始权威会
        # 当场抛——这正是「三处解析同一批边」的可证形态，不是可绕过的形式要求。
        authority=scan, manifest=draft.material_manifest,
        material_context=outcome.material_context, llm_client=final_sentence_llm_client,
        # `on_call` 只用于记账（每次调用前回调一次），不参与判定。
        on_call=lambda _bundle: sentence_gate_calls.append(1))
    sentence_block = FSF.final_sentence_block_unresolved(
        section_id=str(task.section_id), narrative=narrative, decisions=sentence_decisions,
        claims=claims, accepted_bindings=tuple(chain.acceptance.accepted_bindings))
    # 4) current Result 最后形成：正文由**唯一**渲染器从 final Narrative 重算。
    # `pwr-5`：来源索引由本节**定稿 Claim 集**现场重建（`citation_source_index`），取值只来自
    # Claim 自己的引用对象与权威的公式登记表。渲染器不接受自带文字、也不接受部分覆盖。
    markdown = NS.render_final_narrative_markdown(
        draft.title, narrative, tuple(draft.unresolved_projections),
        citation_sources=NS.citation_source_index(claims))
    # 缺口集的口径（§12.4.4 第 4 步收窄，**不是**放宽）：Result 的缺口 = draft 声明的缺口（逐条
    # 保留，不得丢）∪ **至多一条**由 `NS.final_sentence_gate_state` 重算出的最终句门 block。
    # 后者**后于** Draft 形成（它核验最终句，而最终句在两道门之后才成形），Draft 里不可能有它的
    # 投影；因此「Result 缺口 == Draft 缺口」这条判据必须按上面那句收窄，否则本门一落 block 就会
    # 被误判成「凭空新增缺口」。收窄的界**恰好**是一条、且必须是那一条：多出的第二项、或换掉的
    # id，都仍然当场拒（缺口不得被静默丢弃，也不得凭空新增）。
    unresolved = tuple(outcome.unresolved) + ((sentence_block,) if sentence_block is not None
                                              else ())
    draft_gap_ids = {str(x) for x in draft.unresolved_ids}
    result_gap_ids = {str(u.unresolved_id) for u in unresolved}
    allowed_extra = ({str(sentence_block.unresolved_id)}
                     if sentence_block is not None else set())
    dropped = sorted(draft_gap_ids - result_gap_ids)
    added = sorted(result_gap_ids - draft_gap_ids)
    if dropped or set(added) - allowed_extra:
        raise BackboneWriterPhaseError(
            f"section={draft.section_id!r} 的 draft 缺口与 SectionResult 缺口不一致："
            f"draft 独有（被丢弃）{dropped[:6]}，result 独有（超出最终句门 block）"
            f"{sorted(set(added) - allowed_extra)[:6]}"
            "（draft 缺口不得被静默丢弃；除本门重算出的那一条 block 外也不得凭空新增）")
    claim_ids = tuple(claim.claim_id for claim in claims)
    markdown_fingerprint = NS.body_fingerprint_of(markdown)
    section_version = SS.derive_section_version(
        task.task_id, claim_ids, unresolved, section_draft_id=draft.draft_id,
        renderer_version=draft.writer_renderer_version,
        rules_version=draft.writer_rules_version,
        dependency_fingerprint=draft.dependency_fingerprint,
        markdown_fingerprint=markdown_fingerprint)
    result = SS.SectionResult(
        section_result_id=SS.derive_section_result_id(section_version),
        schema_version=SS.SECTION_RESULT_SCHEMA_VERSION, section_version=section_version,
        section_draft_id=draft.draft_id, task_id=task.task_id, section_id=task.section_id,
        # 章节状态**只能**走唯一口径 `PW._derive_status`（= canonical `RC.derive_status`）。
        # 这里原先写的是「有无缺口」两分支，它不看 `blocking_effects`，会把权威声明为
        # SECTION_BLOCKED 的缺口降级成 `COMPLETED_WITH_GAPS` —— 既与 `sections/rules_evaluator`
        # 规则 3（blocking 级缺口 ⇒ status 必须是 SECTION_BLOCKED）直接冲突，也让 BLOCKED
        # 章节冒充「只是有缺口」。同一权威缺口集合不得因走哪条写作路径得到两种章节状态。
        status=PW._derive_status(unresolved),
        claims=claims, unresolved=unresolved, markdown=markdown,
        markdown_fingerprint=markdown_fingerprint,
        # 来源运行 id 按权威分支各自的口径取（topic = Pack 的 run；financial = 快照 id）：
        # 唯一实现在 `PW._source_run_ids`，这里不另写一套。
        source_run_ids=PW._source_run_ids(authority),
        source_question_ids=tuple(sorted(str(q.question_id) for q in task.questions)),
        dependency_fingerprint=draft.dependency_fingerprint,
        created_at=str(draft.created_at or ""))

    # 5) 章级评估 + NarrativeEvaluationBinding（评估**只能**在 Result 形成之后运行）。
    evaluation_outcome = RE.evaluate_narrative_section(
        _SectionEvaluationBundle(draft=draft, result=result, gate_result=outcome.gate_result,
                                 narrative=narrative,
                                 claim_narrative_dispositions=claim_dispositions),
        task=task, authority=authority, llm_client=llm_client,
        allow_llm_evaluator=allow_llm_evaluator,
        allow_targeted_rework=allow_targeted_rework, evaluated_at=evaluated_at,
        acceptance=chain.acceptance)
    return BackboneSectionWriterOutput(
        section_id=task.section_id, task_id=task.task_id, draft=draft,
        gate_result=outcome.gate_result, aggregate_decisions=tuple(chain.aggregate_decisions),
        entailment_decisions=tuple(chain.entailment_decisions), acceptance=chain.acceptance,
        claims=claims, narrative=narrative, claim_narrative_dispositions=claim_dispositions,
        # §12.4.4 第 4 步：本节的最终句语义决定（至多一条）随产物离开定稿相位——组装器与 Store
        # 要按它重算同一条 block。只**带出**已经发生的核验，不新增任何可写面。
        final_sentence_decisions=tuple(sentence_decisions),
        # 「本门未能形成决定」的**调用现场记录**（原始、未加工）。它**不**进缺口：block 的
        # `unresolved_id` 是内容寻址的（`detail` 是输入之一），而这条记录只存在于**本轮现场**，
        # 组装器与 Store 读回无法复算它——把它写进缺口会让三处重算出三条不同的 id。因此它单独
        # 带出，只用于只读展示「核验未完成，原因是什么」，不得被读成事实或判定。
        final_sentence_gate_note=" ".join(str(undecided_reason or "").split()),
        proposal_set_rejections=tuple(getattr(outcome, "rejections", ()) or ()),
        follow_up_rejections=tuple(getattr(outcome, "follow_up_rejections", ()) or ()),
        disposition_trace=narrative_trace, dispositions=dispositions, result=result,
        evaluation=evaluation_outcome.evaluation, binding=evaluation_outcome.binding,
        follow_up_needs=tuple(outcome.follow_up_needs), prompt_version=str(outcome.prompt_version),
        model_policy=str(outcome.model_policy),
        # 本节最终定稿一共发起了几次调用：门前提案（含解析失败的重试，`outcome.llm_calls`）
        # + 逐候选蕴含（`chain.llm_calls`）+ 有界重写的前几轮（`llm_calls_before`）
        # + **门后自然组织**（`narrative_trace['llm_calls']`）+ **最终句语义门**（每次至多一次）
        # + 可选章级评估。
        # 组织那一次原先漏记：它走的是同一个 client，是一次真实调用，却只出现在客户端侧的
        # 计数里，于是「各节之和」系统性少算「走了模型组织的节数」。漏记的正是这一项——离线
        # 完整链的账本实测见 M930-3 调用预算门报告（narrator 计数 − 各节之和 = 组织次数）。
        llm_calls=int(outcome.llm_calls) + int(chain.llm_calls) + llm_calls_before
        + int(narrative_trace.get("llm_calls") or 0)
        + len(sentence_gate_calls)
        + int(evaluation_outcome.llm_evaluator_calls),
        rewrite_round=rewrite_round, follow_up_run_refs=follow_up_run_refs)


def _persist_section_chain(*, output: BackboneSectionWriterOutput,
                           section_store: Any) -> dict:
    """§三 E：把门后定稿完成的**完整**链恰好提交一次，随后从 store 读回并重算。

    顺序是硬约束：先有完整门后束（Claim / final Narrative / FND / Result），**再**提交——
    门后任一环节算不出来的链根本不该被提交（`_finalize_section` 已经 fail-closed），因此这里
    不可能提交半条链。`SectionResult` 是提交序的最后一行（`V2_POST_GATE_FAMILIES` 唯一给序）。

    「恰一次」是结构约束而非纪律：提交与读回由 store 的**同一个**入口
    `commit_and_verify_section_chain_v2` 完成，本函数拿不到第二次提交的机会。

    能力缺失/形状不对一律 typed fail-closed：不得 `getattr(..., None)` 之后当成「不需要落库」，
    也不得让裸 `AttributeError` 冒出来。
    """
    if section_store is None:
        return {"state": "not_injected"}
    chain_cls = getattr(section_store, "SectionChainV2", None)
    commit_and_verify = getattr(section_store, "commit_and_verify_section_chain_v2", None)
    if not isinstance(chain_cls, type) or not callable(commit_and_verify):
        raise BackboneWriterPhaseError(
            "注入的 section store 不具备 current 链落库能力"
            f"（需要 SectionChainV2 + commit_and_verify_section_chain_v2，得到 "
            f"{type(section_store).__name__}）：缺能力时不得当成「本节无需落库」而放行（fail-closed）")
    chain = chain_cls(
        draft=output.draft, aggregate_decisions=tuple(output.aggregate_decisions),
        entailment_decisions=tuple(output.entailment_decisions),
        accepted_bindings=tuple(output.acceptance.accepted_bindings),
        fact_narrative_dispositions=tuple(output.dispositions),
        claim_narrative_dispositions=tuple(output.claim_narrative_dispositions),
        claims=tuple(output.claims), narrative=output.narrative,
        final_sentence_decisions=tuple(output.final_sentence_decisions),
        result=output.result)
    outcome = commit_and_verify(chain)
    return {"state": "v2_chain_committed_and_verified",
            "draft_id": output.draft.draft_id,
            "reused": bool(outcome["commit"]["reused"]),
            "roundtrip": str(outcome["roundtrip"]),
            "family_rows": dict(outcome["family_rows"])}


@dataclass(frozen=True)
class _SectionEvaluationBundle:
    """`RE.evaluate_narrative_section` 读取的产物字段（draft / result / gate / final Narrative）。

    正文是**门后**的 `SectionNarrative`（narr-5）：章级评估评的就是它，因此这里必须带上它，
    而不是退回门前候选束的字段（那个对象在 narr-4 之后没有正文）。
    """

    draft: Any
    result: Any
    gate_result: Any
    narrative: Any
    claim_narrative_dispositions: tuple[Any, ...] = ()

    @property
    def section_result(self) -> Any:
        return self.result


def _assert_pack_set_is_current(*, task: PS.SectionTask, authority: Any, requirements: Any,
                                store: Any) -> Any:
    """P4 复算：传入的 authority 必须就是 store 里**当前**的那套 Pack（可选，但一旦可算就必查）。

    不查的话，「写作」可以建立在一份过期/伪造的 Pack set 上——那正是 §6.7 第一条退出门
    （Writer 严格拒绝不完整或身份不匹配 Pack set）要挡的东西。`store` 缺省时不猜、不复制
    一套自己的 Pack 校验，而是**不做**这一步并在返回里如实标注（`pack_set_reverified=False`）。

    财务分支没有 Pack set（`pack_set_bound=False`），因此本步**由分支声明地**不适用：这不是
    「跳过一道门」，而是这条分支的权威根本不是 Pack set——它的「current」由 artifact 快照身份
    （`verify()` + 只读观测）承担，那一层在 `run_backbone_financial_phase` 已经跑过。
    """
    branch = _branch_for_authority(authority)
    if not branch.pack_set_bound:
        return None
    if store is None:
        return None
    resolved = PSet.resolve_pack_set(task, tuple(requirements), store)
    actual = tuple(str(p.pack_id) for p in authority.pack_set.packs)
    expected = tuple(str(p.pack_id) for p in resolved.packs)
    if actual != expected:
        raise BackboneWriterPhaseError(
            f"section={task.section_id!r} 的权威 Pack 集不是 store 里的 current 集合："
            f"{actual} != {expected}（不得在过期/伪造的 Pack 上写作）")
    if str(authority.pack_set.gate_version) != str(resolved.gate_version):
        raise BackboneWriterPhaseError(
            f"section={task.section_id!r} 的 Pack set 门版本与 store 复算结果不一致："
            f"{authority.pack_set.gate_version!r} != {resolved.gate_version!r}")
    return resolved


def _follow_up_authority(*, section_input: BackboneWriterSectionInput, needs: Any,
                         dependencies_of: Callable[[Any, Any], Any] | None,
                         store: Any) -> tuple[Any, tuple[Any, ...]]:
    """`FollowUpNeed` → Harness 裁决与执行（P3）→ **新** Pack set → **新**权威输入。

    「未获裁决不得执行」不是「可以不执行」：缺任一注入即 fail-closed，绝不静默丢弃 Writer
    在门前发出的诉求。旧 Pack / 旧 Draft 一律不回写——这里只新建对象，不改任何既有对象。

    §三 D：本入口**只**服务有 Pack set 的分支（successor 就是「新 Pack set」）。财务分支在
    调用方已被 typed 终止；这里再设一道同纪律的守卫，免得别处直接调用时退化成 AttributeError。

    §九（逐 target topic 解析）：运行时依赖是**逐 requirement** 的（导航 profile 由该
    requirement 自己的 aspects 派生），因此每条 `FollowUpNeed` 必须按自己的
    `target_requirement_id` 取对应 requirement 与 dependencies——**不得**对第二、第三 topic
    固定复用 `requirements[0]`。分组只决定「几次裁决调用」，不改变任何一条 need 的执行语义：
    返回的是**每个 target topic 一个** `FollowUpExecutionResult`（身份各自保留，不合并成
    一个伪批次）。target 落在本次注入的 requirements 之外即 fail-closed：诉求既不得被静默
    丢弃，也不得改用别的 topic 的依赖去执行。
    """
    # §二 2.6：本函数里所有「裁决不了」的出口都必须是**typed 且带诉求**的
    # （`UnadjudicatedFollowUpNeeds`），不得退化成只有一句消息的普通错误——否则诉求会随
    # 异常文本一起被截断丢掉，而「裁决不了」正是一条待裁决提议最常见的成因。
    _section_id = str(section_input.task.section_id)
    _all_needs = tuple(needs)
    if not _branch_for_authority(section_input.authority).pack_set_bound:
        raise UnadjudicatedFollowUpNeeds(
            f"section={_section_id!r} 的权威分支没有 Pack set："
            "`FollowUpNeed` 的 successor 只能是新 Pack set，本分支不具备执行能力"
            "（fail-closed，不得另建第二套研究运行时）",
            section_id=_section_id, follow_up_needs=_all_needs)
    requirements = tuple(section_input.requirements)
    if not requirements or section_input.run_context is None \
            or dependencies_of is None or store is None:
        raise UnadjudicatedFollowUpNeeds(
            f"section={_section_id!r} 的 Writer 发出了 "
            f"{len(_all_needs)} 条 FollowUpNeed，但没有注入裁决能力"
            "（requirements / run_context / dependencies_of / store）："
            "未获 Harness 裁决的 FollowUpNeed 不得执行，也不得被静默忽略（fail-closed）",
            section_id=_section_id, follow_up_needs=_all_needs)
    requirements_by_id: dict[str, Any] = {}
    for requirement in requirements:
        requirement_id = TR.topic_requirement_id(requirement)
        if requirement_id in requirements_by_id:
            raise BackboneWriterPhaseError(
                f"requirements 里出现重复的 requirement id {requirement_id!r}（Fail-closed）")
        requirements_by_id[requirement_id] = requirement
    # 逐 target topic 分组（首次出现顺序 = trace 顺序）；每组用**该 requirement 自己**的依赖。
    grouped: dict[str, list[Any]] = {}
    for need in tuple(needs):
        target_id = str(getattr(need, "target_requirement_id", "") or "")
        if target_id not in requirements_by_id:
            raise UnadjudicatedFollowUpNeeds(
                f"FollowUpNeed {getattr(need, 'need_id', '?')!r} 的 "
                f"target_requirement_id={target_id!r} 不在本次注入的 requirements "
                f"{sorted(requirements_by_id)} 内：诉求不得被静默丢弃，也不得改用别的 topic 的"
                "运行时依赖来执行（fail-closed，如实上报）",
                section_id=_section_id, follow_up_needs=_all_needs)
        grouped.setdefault(target_id, []).append(need)
    deps_by_id: dict[str, Any] = {}
    runs: list[Any] = []
    for target_id, group in grouped.items():
        deps = deps_by_id.get(target_id)
        if deps is None:
            deps = dependencies_of(requirements_by_id[target_id], section_input.run_context)
            if deps is None:
                raise UnadjudicatedFollowUpNeeds(
                    f"dependencies_of 未返回 FollowUpNeed 的运行时依赖"
                    f"（target topic {target_id!r}）：诉求不得在缺依赖时被静默跳过"
                    "（fail-closed，如实上报）",
                    section_id=_section_id, follow_up_needs=_all_needs)
            deps_by_id[target_id] = deps
        runs.append(TR.run_follow_up_needs(
            follow_up_needs=tuple(group), requirements_by_id=requirements_by_id,
            run_context=section_input.run_context, dependencies=deps))
    pack_set = PSet.resolve_pack_set(section_input.task, requirements, store)
    authority = PW.TopicPackAuthorityInput.create(
        section_input.task, pack_set,
        company_id=str(section_input.authority.company_id),
        report_as_of=str(section_input.authority.report_as_of),
        contract_version=str(section_input.authority.contract_version),
        contract_fingerprint=str(section_input.authority.contract_fingerprint))
    return authority, tuple(runs)


def _writer_pass(*, section_input: BackboneWriterSectionInput, authority: Any,
                 dependency_fingerprint: str, llm_client: Any, policy: Any,
                 created_at: str | None, rewrite_round: int,
                 entailment_llm_client: Any, final_sentence_llm_client: Any,
                 evaluated_at: str,
                 allow_llm_evaluator: bool, allow_targeted_rework: bool,
                 follow_up_run_refs: tuple[str, ...],
                 llm_calls_before: int,
                 material_resolver: Any = None) -> BackboneSectionWriterOutput:
    """一轮「唯一 Writer → 决定链 → 门后定稿」。

    §三 A：本轮的 Writer 与语义门都吃**真实材料正文**。正文上下文由组合根注入的
    `PayloadResolver` 从当前 PackSet 逐份解析而来（`MC.resolve_writer_material_context`），
    在**同一个**上下文对象上同时喂给 Writer 与决定链——「模型看过的正文」与「Evaluator
    复核的正文」因此是同一串字节，而不是两次各自解析的结果。缺 resolver 一律 typed
    fail-closed（`resolver_missing`），不得降级成「只有 material ID」。

    §三 D：`material_bound=False` 的分支（财务）**没有** exact `ResearchMaterial` 边界，
    `material_context` 因此是 `None`——这不是「解析失败」，而是这条分支的权威是预验证事实
    （路径 A），其正文由事实自己的 surface 承担；`_derive_material_manifest` 会拒绝任何
    「非 topic 权威却夹带 Pack 材料正文」的形态，所以这里不能靠 `getattr` 兜底，只能显式分派。
    """
    branch = _branch_for_authority(authority)
    if branch.material_bound:
        material_context = MC.resolve_writer_material_context(
            pack_set=authority.pack_set, resolver=material_resolver,
            task_id=str(section_input.task.task_id),
            section_id=str(section_input.task.section_id))
    else:
        material_context = None
    outcome = PW.write_section(
        section_input.task, authority, projection=section_input.projection,
        writing_spec=section_input.writing_spec,
        presentation_profile=section_input.presentation_profile,
        llm_client=llm_client, policy=policy,
        dependency_fingerprint=dependency_fingerprint,
        created_at=created_at, material_context=material_context)
    # 决定链（P8/P9/P10）吃的是权威的**确定性读视图**，不是权威输入本身：`authority_fact_table`
    # 明确拒绝「没有 `.facts` 条目序列」的对象（本门不扫描权威）。读视图必须走**唯一**公开入口
    # `PW.scan_authority`，不得在此另起一套扫描实现。
    chain = RE.evaluate_claim_chain(
        outcome.draft, PW.scan_authority(authority, section_input.task),
        manifest=outcome.draft.material_manifest,
        material_context=outcome.material_context,
        llm_client=entailment_llm_client)
    return _finalize_section(
        section_input=section_input, authority=authority, outcome=outcome, chain=chain,
        rewrite_round=rewrite_round, follow_up_run_refs=follow_up_run_refs,
        evaluated_at=evaluated_at, llm_client=llm_client, llm_calls_before=llm_calls_before,
        allow_llm_evaluator=allow_llm_evaluator,
        allow_targeted_rework=allow_targeted_rework,
        final_sentence_llm_client=final_sentence_llm_client)


def run_backbone_writer_phase(
        sections: Any, *,
        llm_client: Any = None,
        entailment_llm_client: Any = None,
        final_sentence_llm_client: Any = None,
        policy: Any = None,
        dependencies_of: Callable[[Any, Any], Any] | None = None,
        store: Any = None,
        material_resolver: Any = None,
        section_store: Any = None,
        created_at: str | None = None,
        evaluated_at: str = "",
        allow_llm_evaluator: bool = False,
        allow_targeted_rework: bool = False) -> BackboneWriterPhase:
    """把已验证的权威输入交给唯一 Writer，并在门后定稿一个或多个 section。

    `sections` 是 `BackboneWriterSectionInput` 序列；顺序即产物顺序（`section_id` 必须唯一，
    不得用同一 section 的两次输入冒充「两个 section」）。

    串接顺序（P4 → P6 → P8 → P9 → P10 → 定稿）：先（可选）用 P4 复算 current Pack set，再跑
    唯一 Writer（P6），再跑决定链（P8 机械门 → P9 语义门 → P10 accepted binding），最后在门后
    由本协调器定稿 Claim/final Narrative、派生 FND、形成 current `SectionResult`，再交章级
    评估。每一步都不越过自己的边界。

    §三 D：公司/行业（`topic_harness`）与财务（`financial_workflow`）走的是**这一条**主链，
    按 authority 分支泛化（见 `AUTHORITY_BRANCHES`）——不存在第二条财务 writer runtime，
    财务的差异只有三处：没有 `ResearchMaterial` 边界（故无 `wmctx-1` 正文上下文）、没有
    Pack set（故无 P4 复算）、没有 Pack successor（故 `FollowUpNeed` 只能 typed 终止）。

    **fail-closed**：
      * section 级研究政策 / producer_kind 不是本相位服务的对象（两者必须落在同一分支上）；
      * `store` 在场时先用 P4 复算 Pack set，与传入 authority 不一致即拒（topic 分支）；
      * `FollowUpNeed` 非空时必须由 Harness 裁决并执行；缺注入、无 successor 能力或超出
        有界轮数即拒；
      * 门后任一环节算不出来（缺口、身份、FND 集合键）即拒，绝不降级成「部分可用」；
      * 有 `ResearchMaterial` 边界的分支缺正文解析器（既没显式注入 `material_resolver`，
        注入的 `store` 也没有 resolver 能力）即拒：Writer 与语义门都不得在看不到正文时运行。

    §三 E（`section_store`）：注入后，每个 section 在**门后定稿完成**时把完整 current 链
    （Draft → 门前 member → 决定 → accepted binding → 定稿 Claim → final Narrative → FND →
    Result）**恰好提交一次**，随后由 store 读回并逐行重算；提交是原子的（任一步失败整事务
    回滚），读回不一致即存储损坏、fail-closed。原子的单位是**一条 section 链**：section 之间
    各自成链，不是「整相位一个事务」。未注入时不落库，并在产物里如实记为 `not_injected`
    （不得读成「已落库且无异常」）。
    """
    if llm_client is None:
        raise BackboneWriterPhaseError(
            "写作相位必须注入 Writer 的 llm_client：缺省 None 不是「无需生成」的开关")
    # 语义门**不得跳过**：`evaluate_claim_chain` 会拒绝 None，这里提前给出本相位自己的诊断。
    if entailment_llm_client is None:
        raise BackboneWriterPhaseError(
            "写作相位必须注入 claim entailment 的 llm_client："
            "「已接受但没有语义决定」的支撑边不具备证据意义（fail-closed）")
    # §12.4.4 第 4 步：最终句语义门**不得跳过**。缺注入时本门无法形成决定，而「本门未能形成
    # 决定」会如实落成 `final_sentence_decision_missing` 的 typed block（章节 `SECTION_BLOCKED`）
    # ——那是一个**业务阻断结论**，不是「接线没接好」该有的下场。因此这里用相位自己的诊断提前
    # 拒掉缺注入：门在场才谈得上「核过了没有」。
    if final_sentence_llm_client is None:
        raise BackboneWriterPhaseError(
            "写作相位必须注入最终句语义门的 llm_client："
            "「没有最终句决定」不得被读成「已核验」（fail-closed）")

    inputs = tuple(sections)
    if not inputs:
        raise BackboneWriterPhaseError("sections 为空：不得用空集合走完相位再宣称无缺口")
    seen: set[str] = set()
    outputs: list[BackboneSectionWriterOutput] = []
    follow_up_runs: list[Any] = []
    for section_input in inputs:
        if not isinstance(section_input, BackboneWriterSectionInput):
            raise BackboneWriterPhaseError(
                "sections 的每一项必须是 BackboneWriterSectionInput"
                f"（得到 {type(section_input).__name__}）")
        task = section_input.task
        if task.section_id in seen:
            raise BackboneWriterPhaseError(
                f"同一 section {task.section_id!r} 出现两次：不得用重复输入冒充多个 section")
        seen.add(task.section_id)
        authority = section_input.authority
        # section 级 research_policy 与 authority 的 producer_kind 必须落在同一条已登记分支上
        # （两根轴不得互相顶替；未登记即拒，不猜）。
        branch = _branch_for_task(task=task, authority=authority)
        if branch.material_bound and material_resolver is None:
            # 材料正文解析器：显式注入优先；否则从注入的 Pack store 的 `resolver` 能力取（能力
            # 不符走 `PackSetError`，**不**在这里吞成裸 AttributeError，也不降级）。两者都没有即拒。
            # 只有有 `ResearchMaterial` 边界的分支才需要它：财务分支没有正文可解析（见 `_writer_pass`）。
            if store is None:
                raise BackboneWriterPhaseError(
                    "写作相位必须注入 material_resolver 或带 resolver 能力的 Pack store："
                    "没有正文解析器时 Writer 与语义门都看不到正文（fail-closed）")
            try:
                material_resolver = PSet.resolver_of(store)
            except PSet.PackSetError as exc:
                raise BackboneWriterPhaseError(
                    f"写作相位无法取得材料正文解析器：{exc}"
                    "（「只有 material ID」不是可降级的输入形态）") from exc

        requirements = tuple(section_input.requirements)
        _assert_pack_set_is_current(
            task=task, authority=authority, requirements=requirements, store=store)

        # 第一轮：基准依赖指纹（由 composition root 给出），零次 FollowUp 裁决。
        current_authority = authority
        current_fingerprint = section_input.dependency_fingerprint
        refs: tuple[str, ...] = ()
        output = _writer_pass(
            section_input=section_input, authority=current_authority,
            dependency_fingerprint=current_fingerprint, llm_client=llm_client,
            policy=policy, created_at=created_at, rewrite_round=0,
            entailment_llm_client=entailment_llm_client,
            final_sentence_llm_client=final_sentence_llm_client, evaluated_at=evaluated_at,
            allow_llm_evaluator=allow_llm_evaluator,
            allow_targeted_rework=allow_targeted_rework, follow_up_run_refs=refs,
            llm_calls_before=0, material_resolver=material_resolver)

        # §二 3：先把第 0 轮的整束拒绝审计**取下来**——下面一旦进入有界重写，`output` 会被第 1 轮
        # 的结果整个替换掉，两轮的拒绝审计必须各自留存（见下方合并处）。
        round0_rejections = tuple(output.proposal_set_rejections)

        # 有界重写：FollowUpNeed →（Harness 裁决）→ 新 Pack → 基于新 Pack 再写一轮。
        if output.follow_up_needs:
            if not branch.pack_set_bound:
                # §三 D：财务分支没有 Pack successor。这不是「模型少写了一段」的写作缺口，而是
                # **本相位在该分支不具备执行这条诉求的能力**：必须以 typed 终止态如实上报——
                # 既不得静默跳过（那会把「需要更多材料」读成「没有诉求」），也不得为执行它而
                # 新建第二套财务研究运行时。
                raise UnadjudicatedFollowUpNeeds(
                    f"section={task.section_id!r}（producer_kind={branch.producer_kind!r}）的 "
                    f"Writer 发出 {len(output.follow_up_needs)} 条 FollowUpNeed，而本相位在该分支"
                    "没有 Pack successor 执行能力：诉求不得被静默丢弃，也不得另建第二套财务研究"
                    "运行时来执行它（fail-closed，如实上报）",
                    section_id=task.section_id, follow_up_needs=tuple(output.follow_up_needs))
            if MAX_FOLLOW_UP_ROUNDS < 1:
                raise UnadjudicatedFollowUpNeeds(
                    "FollowUpNeed 已发出但本相位的重写轮数为 0：诉求不得被静默丢弃",
                    section_id=task.section_id, follow_up_needs=tuple(output.follow_up_needs))
            current_authority, runs = _follow_up_authority(
                section_input=section_input, needs=output.follow_up_needs,
                dependencies_of=dependencies_of, store=store)
            follow_up_runs.extend(runs)
            refs = tuple(str(t) for r in runs
                         for t in tuple(getattr(r, "trace_refs", ()) or ()))
            # 旧 Pack / 旧 Draft **不回写**：新权威输入由新 Pack set 派生，旧对象原样保留。
            _assert_pack_set_is_current(
                task=task, authority=current_authority, requirements=requirements, store=store)
            # `current_fingerprint` **有意保持不变**（§四 1 / §4.6）：`dependency_fingerprint` 是
            # 冻结的**策略**依赖（Contract / SourcePolicy / WritingSpec / PresentationProfile 四类
            # 资产指纹，`planning.demo_scope_schema.compute_demo_dependency_fingerprint` 是唯一定义），
            # 同一报告各节必须逐字节相同。有界补件换掉的是**内容/容器**，不是这四类策略资产：
            #   * `follow_up_run_id` 与 trace refs 属 operational run identity，§4.6 明确把它排除在
            #     `report_version` 输入之外，`AGENTS.md` §3 也把 `FollowUpNeed` 放在三套身份集**之外**
            #     ——把它混进 Draft 的依赖指纹，等于让「操作事件」改写「策略依赖」；
            #   * 后继 Pack 的身份不在这里丢失：容器身份进 `Draft.authority_container_ids`（内容身份，
            #     见 `pack_writer._authority_container_ids`）与 exact manifest，Pack 集进
            #     `ReportVersionInputs.topic_pack_ids`（版本身份）。因此「换了后继 Pack 或材料 ⇒
            #     Draft／报告版本随之变化」仍然成立，而「只换了补件运行、材料与正文不变」不会漂移。
            output = _writer_pass(
                section_input=section_input, authority=current_authority,
                dependency_fingerprint=current_fingerprint, llm_client=llm_client,
                policy=policy, created_at=created_at, rewrite_round=1,
                entailment_llm_client=entailment_llm_client,
            final_sentence_llm_client=final_sentence_llm_client, evaluated_at=evaluated_at,
                allow_llm_evaluator=allow_llm_evaluator,
                allow_targeted_rework=allow_targeted_rework, follow_up_run_refs=refs,
                llm_calls_before=output.llm_calls, material_resolver=material_resolver)
            if output.follow_up_needs:
                raise UnadjudicatedFollowUpNeeds(
                    f"section={task.section_id!r} 在 {MAX_FOLLOW_UP_ROUNDS} 轮有界重写后仍发出 "
                    f"{len(output.follow_up_needs)} 条 FollowUpNeed：本相位不得进入无界返修"
                    "（fail-closed，如实上报）",
                    section_id=task.section_id, follow_up_needs=tuple(output.follow_up_needs))
            # §二 3：第 0 轮的**整束拒绝审计**不得因为「第 1 轮替换了它」而消失。第 1 轮换的是
            # 权威输入（后继 Pack）与其完整提案集，两轮的拒绝不是同一回事；把两轮按发生顺序并成
            # 一条时间线，才能回答「这一节到底被拒过几次、每次为什么」。
            output = dataclasses.replace(
                output,
                proposal_set_rejections=(round0_rejections
                                         + tuple(output.proposal_set_rejections)))
        # §三 E：**本节定稿后**才落库——不是「每跑一轮落一次库」。有界重写的第 0 轮产物若先落库，
        # 本节就会有两条 current 链（一条基于旧 Pack），而「恰一次正式提交」也无从保证。因此只有
        # 经过 FollowUp 裁决后**仍然留下来**的这一份输出被提交，被替换掉的那一轮 Draft 从不落库
        # （既没有「旧 Draft 被原位重写」，也没有「旧 Draft 冒充 current」）。
        outputs.append(dataclasses.replace(
            output, authority=current_authority,
            persistence=_persist_section_chain(
                output=output, section_store=section_store)))

    return BackboneWriterPhase(
        phase_version=BACKBONE_WRITER_PHASE_VERSION, sections=tuple(outputs),
        follow_up_runs=tuple(follow_up_runs))


def run_formal_m930_writer_phase(sections: Any, *, section_store: Any,
                                 **kwargs: Any) -> BackboneWriterPhase:
    """§九：M930 的**正式**写作入口 —— 强制落库，且每条 section 链都提交并独立读回。

    与 `run_backbone_writer_phase` 的分工是「能力」而不是「风格」：后者允许 `section_store=None`
    并以 `not_injected` 如实上报，那是给纯单元测试用的**非正式**入口；正式入口把它变成不可能：

      1. `section_store` 不得为 None（缺注入即 typed fail-closed）——`not_injected` 不是
         正式成功，也不是「本节无需落库」；
      2. 每条 section 的落库状态必须是 `v2_chain_committed_and_verified`，且读回结果是
         `verified`（行集逐行相等由 `commit_and_verify_section_chain_v2` 一次完成，本入口
         只复核它**确实**发生了，不另开第二次提交）；
      3. 任一节不满足即整相位拒 —— 不得「公司节落了库、行业节没落」地半成功返回。

    验收 runner 与未来 M930-5 的 UI/服务都必须调用本入口。产品 UI 仍未切换（那属于 M930-5）：
    「正式纵向切片已接入」不等于「产品调用链已迁移」。
    """
    if section_store is None:
        raise BackboneWriterPhaseError(
            "正式 M930 写作入口必须注入 Section Store：`not_injected` 不是正式成功"
            "（纯单元测试才用无 store 的非正式入口）")
    phase = run_backbone_writer_phase(sections, section_store=section_store, **kwargs)
    for output in phase.sections:
        section_id = str(getattr(output, "section_id", "") or "?")
        persistence = dict(getattr(output, "persistence", None) or {})
        state = str(persistence.get("state", ""))
        if state == "not_injected":
            raise BackboneWriterPhaseError(
                f"section={section_id!r} 报 not_injected："
                "正式入口下「未落库」不是成功态（fail-closed）")
        if state != "v2_chain_committed_and_verified":
            raise BackboneWriterPhaseError(
                f"section={section_id!r} 的落库状态为 {state!r}："
                "正式入口只承认「已提交且读回验证」")
        if str(persistence.get("roundtrip", "")) != "verified":
            raise BackboneWriterPhaseError(
                f"section={section_id!r} 的读回结果为 {persistence.get('roundtrip')!r}："
                "读回未验证即不得算作正式成功")
    return phase


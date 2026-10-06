"""M930-2 §5.8：**唯一** Topic runtime（`run_topic_requirement`）。

它是 Demo Backbone 中唯一把「Contract aspect → 树导航 → 合格材料 → focused follow-up →
`TopicResearchPack` → append-only commit」串起来的运行入口。设计约束（逐条来自 §5.8）：

- 按 **required aspects** 调度，不按「一个 broad answer」提前结束 topic；
- 每个 focused need 都先构造 `InformationNeed` / `RouteContext` / `RouterResult`，
  再以**完整参数**复用既有 `harness.runtime.run_question()`；
- 先按树导航装入合格材料，再按 gap 发起 focused follow-up；
- 记录 external funnel、uncertain calls、conflicts、not-found audits；
- 所有 validated relevant materials/facts 都保留进 Pack；
- 逐 aspect 派生终态后调用既有 `TS.finalize_pack()`；
- 提交只走既有 `commit_pack(pack, requirement, resolver, source_policy_resolver,
  set_completeness_verifier, set_enumeration_verifier)`（append-only）；
- 同 identity 的 stale / wrong contract / wrong source policy / wrong task 不得 current
  （由 `commit_pack` 的 requirement 一致性与 current 指针语义承担，本模块不绕过）。

公开 runtime **不接受**裸 Evidence reader、live tree source 或任意 DB connection：
证据只能经已登记的 Tool adapter 取得，payload 只能经注入的 `PayloadResolver` 复核。
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from document_structure import navigation as NAV
from document_structure import canonical as CANON
from document_structure.canonical import sha256_canonical
from harness import runtime as RT
from harness import source_manifest as SM
from harness import topic_schema as TS
from harness import period_extraction as PE
from harness import numeric_disclosure as ND
from harness import read_plan as RPO
from harness.payload_text import resolved_payload_text
from routing import schema as RS

#: 本运行时的语义版本（§5.2.1 裁决 6：具名常量，不伪装成 topic_schema 版本）。
TOPIC_RUNTIME_VERSION = TS.TOPIC_RUNTIME_VERSION

#: 树检查工具（§5.5 固定公开名）；延迟导入避免循环依赖（tree_tools → tree_materials）。
_TREE_TOOL_NAME = "inspect_outline_materials"

#: §七 有界 Evidence fallback 单次最多重切的父 Evidence 数（与工具 schema maxItems 一致）。
_TREE_FALLBACK_MAX_EVIDENCE_IDS = 40

#: §九 not-found 审计的派生规则版本（派生规则变更必须升版本；审计字段可据此复算）。
#: `nfap-2`：应有检索范围改由 aspect 的**冻结** EvidenceRequirement 语义派生
#: （`TS.derive_support_eligibility`），实际触达范围改由**真实轨迹的既有字段**派生；
#: 两者必须逐类可比，`allowed_capabilities` 不再被当作"范围已查过"的证明。
NOT_FOUND_AUDIT_POLICY_VERSION = "nfap-2"

#: **预算类终止理由的闭集**。`derive_not_found_audit` 的 `budget_exhausted` 只按**成员判定**，
#: **不得**再用字符串前缀（`stop_reason.startswith("BUDGET")`）。
#:
#: 为什么必须改成闭集：`run_follow_up_needs` 记的是 `"follow_up_budget_exhausted"`，它**不以
#: `BUDGET` 开头**，于是「因 follow-up 预算耗尽而停止」此前**不会**被判为 `budget_exhausted`
#: ——该 aspect 的审计可能因此凑齐条件而 `qualified=true`，把一次**因预算不足而没查完**的
#: 检索写成「在已列范围内已尝试、未命中」。那是 fail-open：一条被截断的检索冒充一次完成的
#: 检索。加一个理由就忘一处判定，前缀判据迟早还会漏；闭集则让新增理由**必须**显式表态。
#:
#: 闭集之外的终止理由（如 `PATH_NOT_IMPLEMENTED`）**不是**预算理由：它们不影响
#: `budget_exhausted`，由各自的判定分支承担。
BUDGET_STOP_REASONS = frozenset({
    "BUDGET_EXHAUSTED",             # 每轮事前预留不足（topic 预算）
    "BUDGET_ROUNDS",                # need 轮次用尽
    "follow_up_budget_exhausted",   # focused follow-up 的 topic 预算用尽
    # 事后计费越界：`can_afford(llm_calls=1)` 只**事前预留 1 次**，而一次
    # `research_question` 实际可能发多次 LLM 调用（action/answer/entailment 各自计数）。因此
    # 「预留 1 次放行 → 事后按真实用量 `charge_need`」这一步**可以**把累计值推过
    # `max_llm_calls_per_topic`。此前这条越界**不留任何痕迹**：循环下一轮的前事预留会因已超限
    # 而拒绝，于是运行继续往下走，账本上却出现一个大于自己声明上限的读数（离线 run
    # `…_anp3_offline_a9` 实测 `topic:company_legal_risks` = 37 > 36）。它不是 fail-open 的
    # 「多查了」，而是**声明的上限没有被真正执行**：必须显式表态，不能靠前缀或静默。
    "BUDGET_OVERSHOOT",             # 事后计费越出 topic 上限（越界量有界但非零）
})

#: §九 规定的 not_found 缺口文案（**唯一**出处：生产与测试共用同一常量，避免"文案规则只写在
#: 测试里、生产偷偷改掉"）。只能是「在本轮已纳入材料及检索范围内未取得」——**不得**写成
#: 「未披露 / 没有 / 不存在」：检索范围没查到不等于客观不存在。
NOT_FOUND_GAP_WORDING = "在本轮已纳入材料及检索范围内未取得"

#: 真实轨迹对象 → 该系统**实际存在**的来源类（与 `TS.AUTHORITY_SOURCE_CLASS_BY_TYPE`
#: 对 evidence / financial_snapshot / external_snapshot 的派生同一口径，**不新增映射**）。
#:
#: 左边是 `tools.contracts.ToolResult` 与 `harness.schema.ResearchState` 上**既有**的
#: 字段名：`evidence_ids`（本地证据库）/ `structured_result_refs`（`ResearchState.structured_refs`）/
#: `external_snapshot_ids`。字段为空就是**证不出**该来源类被触达过——绝不按"工具名 → 所有
#: 来源类"猜测（那正是本轮要修掉的缺陷）。
ATTEMPTED_SOURCE_CLASS_FIELDS = (
    ("evidence_ids", "company_industry"),
    ("structured_result_refs", "structured_db"),
    ("structured_refs", "structured_db"),
    ("external_snapshot_ids", "external"),
)


def attempted_source_classes(*traces: Any) -> tuple[str, ...]:
    """由**真实**工具结果 / 研究状态派生实际触达过的来源类（空 = 证不出，不是"没查"）。

    只读每条轨迹自带的 :data:`ATTEMPTED_SOURCE_CLASS_FIELDS` 字段；不做任何"工具名 →
    来源类"的猜测映射，也不把树工具等同于全部本地/结构化/外部来源。
    """
    found: list[str] = []
    for trace in traces:
        if trace is None:
            continue
        for field_name, source_class in ATTEMPTED_SOURCE_CLASS_FIELDS:
            values = getattr(trace, field_name, None)
            if values and source_class not in found:
                found.append(source_class)
    return tuple(found)


def required_search_scope(aspect: TS.TopicAspectRequirementSnapshot) -> tuple[str, ...]:
    """aspect 的**应有检索范围**：由冻结 EvidenceRequirement 语义确定性派生。

    唯一来源是 `TS.derive_support_eligibility(aspect)`（冻结 `EvidenceRequirementRef` 的
    `required_any_of` / `source_classes`）——**不是** `allowed_capabilities`（那是"允许调用
    哪些动作"，不是"查过哪些来源"）。返回空元组表示冻结契约未声明可比较的来源类，
    此时"应有范围已查过"无从证明，调用方必须 fail-closed。
    """
    eligibility = TS.derive_support_eligibility(aspect)
    return tuple(eligibility.required_source_classes)


def derive_not_found_audit(
        *, aspect: TS.TopicAspectRequirementSnapshot,
        searched_need_ids: tuple[str, ...],
        valid_attempt_count: int,
        traces: Sequence[Any],
        external_attempted: bool,
        alternative_candidate_ids: tuple[str, ...],
        unattempted_candidate_ids: tuple[str, ...],
        context_expansion_attempted: bool,
        stop_reason: str | None,
        budget_exhausted: bool) -> tuple[TS.NotFoundAudit, tuple[str, ...]]:
    """由**真实 trace/usage** 派生 not-found 审计，并逐条判定是否满足 not_found 条件。

    条件（§九，全部成立才允许写 `not_found`）：

    1. 原子 outcome 的停止原因明确为 `NOT_FOUND_AFTER_SEARCH`；
    2. 存在真实 searched need IDs（真的发起并执行过的 focused need）；
    3. 存在实际执行过的检索/工具尝试（`valid_attempt_count > 0`）；
    4. 至少有一条真实轨迹能证明触达过某个**具名来源类**（`traces` 的既有字段派生）；
    5. aspect 的冻结 EvidenceRequirement 声明了应有来源范围，且该范围**每一个来源类**
       都有真实轨迹证明查过（`required_search_scope_unproven` 即此条不成立）；
    6. 没有"真实存在过但被有界上限挡下"的未尝试候选；
    7. 不是因为预算耗尽而停止。

    `traces` 必须是**真实发生过**的调用/结果对象（`ToolResult`、`ResearchState`），
    派生只读它们自带的字段，因此调用方无法用自造标签把范围"补齐"。返回
    ``(audit, blockers)``：`blockers` 非空即表示**不得**写 not_found——此时 audit 仍原样
    返回（字段全部来自真实轨迹，供审计读"为什么不够格"），但不得据此判终态。
    """
    blockers: list[str] = []
    if stop_reason != "NOT_FOUND_AFTER_SEARCH":
        blockers.append(f"原子停止原因不是 NOT_FOUND_AFTER_SEARCH（实际 {stop_reason!r}）")
    if not searched_need_ids:
        blockers.append("没有真实 searched need IDs")
    if int(valid_attempt_count) <= 0:
        blockers.append("没有实际执行过的检索/工具尝试")

    scope = required_search_scope(aspect)
    attempted = list(attempted_source_classes(*traces))
    if external_attempted and "external" not in attempted:
        # 真实外部用量（UsageLedger / external_snapshot_ids）也是一条真实轨迹。
        attempted.append("external")
    attempted = sorted(set(attempted))
    if not attempted:
        blockers.append(
            "没有任何真实轨迹能证明触达过具名来源类（空轨迹不得由『没查到』反推）")
    if not scope:
        blockers.append(
            "required_search_scope_unproven：aspect 的冻结 EvidenceRequirement 未声明"
            "可比较的来源类，『应有范围已查过』无从证明")
    else:
        missing = sorted(set(scope) - set(attempted))
        if missing:
            blockers.append(
                f"required_search_scope_unproven：应有来源范围 {missing} 没有任何真实轨迹"
                f"证明查过（实际触达 {attempted}）；不得把 tree tool 等同于全部本地/"
                f"结构化/外部来源")
    if unattempted_candidate_ids:
        blockers.append(
            f"仍有 {len(unattempted_candidate_ids)} 个真实存在过的候选被有界上限挡下（未尝试）")
    if budget_exhausted:
        blockers.append("因预算耗尽停止（不是检索范围内确无）")

    audit_id = "nfa-" + sha256_canonical({
        "aspect_id": aspect.aspect_id,
        "policy_version": NOT_FOUND_AUDIT_POLICY_VERSION,
        "searched_need_ids": list(searched_need_ids),
    })[:24]
    audit = TS.NotFoundAudit(
        audit_id=audit_id,
        policy_version=NOT_FOUND_AUDIT_POLICY_VERSION,
        required_source_scope=scope,
        attempted_source_types=tuple(attempted),
        valid_attempt_count=int(valid_attempt_count),
        searched_need_ids=tuple(searched_need_ids),
        context_expansion_attempted=bool(context_expansion_attempted),
        alternative_candidate_ids=tuple(sorted(set(alternative_candidate_ids))),
        alternative_sources_attempted=tuple(attempted),
        time_window=aspect.time_scope or "unspecified",
        unattempted_candidate_ids=tuple(sorted(set(unattempted_candidate_ids))),
        budget_exhausted=bool(budget_exhausted),
        qualification_reasons=tuple(blockers),
        qualified=not blockers,
    )
    return audit, tuple(blockers)


# 2b. §L1.5 逐 aspect × 逐来源责任模型（**检索前**定，可复算，禁止倒填）
# ---------------------------------------------------------------------------
#
# **类型定义在 `harness/topic_schema.py`**（Pack v7 顶层要装它，schema 层不能反向 import
# runtime）；本模块负责**派生**：输入轴 → 责任三元组 + typed 理由。两者是同一批改动，
# 不新建模块、不新建运行时。
#
# 这里保留同名别名，使 `TR.AspectSourceResponsibility` / `TR.DocumentSourceSet` /
# `TR.ResponsibilityDerivationError` 与既有调用点、测试继续可用（**只有一个**类型对象）。
ASSUME_RULE_VERSION = TS.ASSUME_RULE_VERSION
PROOF_REQUIRED_VALUES = TS.PROOF_REQUIRED_VALUES
RESPONSIBILITY_BASIS_RULE_IDS = TS.RESPONSIBILITY_BASIS_RULE_IDS
NOT_REQUIRED_BASIS_RULE_IDS = TS.NOT_REQUIRED_BASIS_RULE_IDS
UPLOADED_DOCUMENT_SOURCE_CLASS = TS.UPLOADED_DOCUMENT_SOURCE_CLASS
ResponsibilityDerivationError = TS.ResponsibilityDerivationError
AspectSourceResponsibility = TS.AspectSourceResponsibility
DocumentSourceSet = TS.DocumentSourceSet
_basis_row = TS._basis_row

_AXIS_NAMES = ("company_id", "document_id", "document_version", "evidence_set_version")


def _member_source_classes_by_axes(
        member_source_classes: Any, sources: DocumentSourceSet) -> dict[tuple, str]:
    """把「逐份来源类」规范成 **四轴身份 → 来源类**（定点返修 T4）。

    接受以 `SourceDocumentKey` 或以四元组 `(company_id, document_id, document_version,
    evidence_set_version)` 为键的映射。**裸 `document_id` 字符串一律拒绝**：那正是「按 id
    就近匹配」的入口，会把「同 id、错版本」的关联静默接受下来——而四轴是内容寻址身份，
    同 id 的两份不同版本是两份**不同的文档**，来源类也必须逐份给。

    集合外成员同样 fail-closed，且**分开报**「同 id 错身份」与「根本不在集合内」两档：
    前者是身份写错，后者是漏登记，混成一句话会让人按错误的线索去查。
    """
    normalized: dict[tuple, str] = {}
    for raw_key, source_class in dict(member_source_classes or {}).items():
        if isinstance(raw_key, SM.SourceDocumentKey):
            axes = SM.source_key_axes(raw_key)
            shown: Any = raw_key.to_dict()
        elif isinstance(raw_key, (tuple, list)) and len(raw_key) == 4:
            axes = tuple(str(part) for part in raw_key)
            shown = dict(zip(_AXIS_NAMES, axes))
        else:
            raise ResponsibilityDerivationError(
                "member_source_classes 的键必须是**四轴身份**（SourceDocumentKey 或 "
                f"(company_id, document_id, document_version, evidence_set_version) 四元组），"
                f"实际 {raw_key!r}。裸 document_id 不再接受：同一 document_id 的不同版本/"
                "证据集是两份不同的文档，按 id 关联会把「声明了 B、拿到了 A」变成静默错配")
        if not isinstance(source_class, str) or source_class == "":
            raise ResponsibilityDerivationError(
                f"来源类必须非空字符串（key={shown!r}）")
        normalized[axes] = source_class

    member_axes = {SM.source_key_axes(k) for k, _ in sources.members}
    unknown = sorted(axes for axes in normalized if axes not in member_axes)
    if unknown:
        member_ids = {axes[1] for axes in member_axes}
        near = [axes for axes in unknown if axes[1] in member_ids]
        if near:
            raise ResponsibilityDerivationError(
                f"member_source_classes 的键与来源集成员**同 document_id、不同文档身份**："
                f"{near}；来源集成员是 {sorted(member_axes)}。四轴必须全同（fail-closed）")
        raise ResponsibilityDerivationError(
            f"member_source_classes 的键 {unknown} 不在本来源集内（fail-closed，不兜底）")
    return normalized


def _by_aspect_axes(mapping: Any, sources: DocumentSourceSet,
                    label: str) -> dict[tuple[str, tuple], Any]:
    """把「逐 `(aspect, 来源)`」的映射规范成 `(aspect_id, 四轴身份) → 值`（定点返修 T4）。

    与 :func:`_member_source_classes_by_axes` 同一条纪律：键里的来源必须是**四轴身份**
    （`SourceDocumentKey` 或四元组），裸 `document_id` 一律拒绝；集合外的身份 fail-closed，
    并分开报「同 id 错身份」与「根本不在集合内」。
    """
    normalized: dict[tuple[str, tuple], Any] = {}
    member_axes = {SM.source_key_axes(k) for k, _ in sources.members}
    for raw_key, value in dict(mapping or {}).items():
        if not isinstance(raw_key, (tuple, list)) or len(raw_key) != 2:
            raise ResponsibilityDerivationError(
                f"{label} 的键必须是 (aspect_id, 四轴身份)，实际 {raw_key!r}")
        aspect_id, raw_source = raw_key
        if isinstance(raw_source, SM.SourceDocumentKey):
            axes = SM.source_key_axes(raw_source)
            shown: Any = raw_source.to_dict()
        elif isinstance(raw_source, (tuple, list)) and len(raw_source) == 4:
            axes = tuple(str(part) for part in raw_source)
            shown = dict(zip(_AXIS_NAMES, axes))
        else:
            raise ResponsibilityDerivationError(
                f"{label} 的来源键必须是**四轴身份**（SourceDocumentKey 或四个轴的元组），"
                f"实际 {raw_source!r}。裸 document_id 不再接受：同 id 的不同版本/证据集是"
                "两份不同的文档，按 id 关联会把甲份的调用痕迹与材料记到乙份名下")
        if axes not in member_axes:
            if any(a[1] == axes[1] for a in member_axes):
                raise ResponsibilityDerivationError(
                    f"{label} 的来源键与来源集成员**同 document_id、不同文档身份**："
                    f"{shown}；成员是 {sorted(member_axes)}（四轴必须全同，fail-closed）")
            raise ResponsibilityDerivationError(
                f"{label} 的来源键 {shown} 不在本来源集内（fail-closed，不兜底）")
        normalized[(str(aspect_id), axes)] = value
    return normalized


def derive_aspect_source_responsibility(
        *, aspect: TS.TopicAspectRequirementSnapshot,
        sources: DocumentSourceSet,
        member_source_classes: dict[str, str] | None = None,
        current_state: str = SM.CURRENT_STATE_RESOLVED) -> tuple[AspectSourceResponsibility, ...]:
    """§L1.5 确定性派生：某个 aspect 对来源集**逐份**的检索前责任三元组。

    **签名纪律（§0.2.1 X-3，必须有测试）**：本函数的入参里**不得**出现任何检索结果、材料、
    facts、trace 或 stop_reason。责任只能在**看见任何结果之前**由输入轴定死；看见结果再倒填
    责任，会让「该查没查」看起来像「查过没命中」。

    入参只是 L1.5 的四条输入轴：

    - `aspect`：冻结 Contract 的 aspect 快照 —— `coverage_rules`（**第一约束**）与
      `evidence_requirement_ids` 的 authority（`required_any_of` / `supplemental_only`，
      经 `TS.derive_support_eligibility` 派生为来源类集合）；
    - `sources`：L0 的有序来源集（判断一 + 来源角色）；
    - `member_source_classes`：逐份的来源类，**键是四轴身份**（`SourceDocumentKey` 或四元组；
      见 :func:`_member_source_classes_by_axes`），缺省时全部取
      :data:`UPLOADED_DOCUMENT_SOURCE_CLASS`（上传文档的正文一律以 `evidence` 权威进入系统）；
    - `current_state`：L0 的当前锚解析状态（`ambiguous_current_state` 时全成员 `undetermined`）。

    规则逐条可复算，见 `CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md` §L1.5 规则 1/2/2b/3a/3b/3c。
    """
    classes = _member_source_classes_by_axes(member_source_classes, sources)

    eligibility = TS.derive_support_eligibility(aspect)
    required_classes = set(eligibility.required_source_classes)
    supplemental_classes = set(eligibility.supplemental_only_source_classes)
    coverage_rules = set(aspect.coverage_rules or ())
    set_complete = "set_complete" in coverage_rules

    records: list[AspectSourceResponsibility] = []
    for key, role in sources.members:
        source_class = classes.get(SM.source_key_axes(key), UPLOADED_DOCUMENT_SOURCE_CLASS)
        if not isinstance(source_class, str) or source_class == "":
            raise ResponsibilityDerivationError(
                f"来源类必须非空字符串（document_id={key.document_id!r}）")

        # 判断二：该成员所属来源类是否落在 aspect 声明的来源类内。
        retrieval_required = source_class in required_classes
        if retrieval_required:
            reason_rows = [_basis_row(
                "batch_scheduling_rule",
                f"source_class={source_class}：本批把「来源类 → 该类的全部树文档成员」逐份纳入"
                f"尝试；这是 {ASSUME_RULE_VERSION} 的批次调度规则，**不是** Contract 的逐份义务")]
        else:
            reason_rows = [_basis_row(
                "not_in_required_scope",
                f"source_class={source_class} 不在本 aspect 声明的来源类 "
                f"{sorted(required_classes)} 内")]

        # 判断三：三值，四步，第一约束在最前。
        if not set_complete:
            proof_required = "false"
            proof_rows = [_basis_row(
                "aspect_not_set_complete_no_enumeration_proof",
                f"aspect.coverage_rules={sorted(coverage_rules)} 不含 set_complete：普通事实栏目"
                f"不承担集合枚举证明责任（第一约束，永不可能取 true）")]
        elif current_state == SM.CURRENT_STATE_AMBIGUOUS:
            proof_required = "undetermined"
            proof_rows = [_basis_row(
                "ambiguous_current_state_no_proof_domain",
                "本来源集的当前锚不可判定（同系列成员期间不可核实）：全成员无证明域，"
                "不得由「有多份来源」或「其中一份是当前来源」推断通过")]
        elif not retrieval_required:
            proof_required = "false"
            proof_rows = []
        elif source_class in supplemental_classes:
            proof_required = "false"
            proof_rows = [_basis_row(
                "supplemental_only_never_proves_completeness",
                f"source_class={source_class} 属于 supplemental_only：可检索、可支撑，"
                f"但永不承担集合完整性证明")]
        elif role == "current_state_source":
            proof_required = "true"
            proof_rows = [_basis_row(
                "current_state_source_proves_completeness",
                "该份是本来源集唯一的当前状态兼容锚：只有它可能承担集合完整性证明")]
        else:
            proof_required = "undetermined"
            proof_rows = [_basis_row(
                "cross_period_responsibility_not_expressible_in_frozen_contract",
                f"source_role={role}：冻结 Contract 无法表达跨期集合要求，"
                f"该份对集合完整性的责任无从判定（既不得取 false，也不得取 true）")]

        records.append(AspectSourceResponsibility(
            aspect_id=aspect.aspect_id,
            source_document_key=key,
            in_source_set=True,
            retrieval_required=retrieval_required,
            proof_required=proof_required,
            basis=tuple(reason_rows + proof_rows),
            rule_version=ASSUME_RULE_VERSION,
        ))
    return tuple(records)


def aspect_source_responsibility_fingerprint(
        responsibilities: tuple[AspectSourceResponsibility, ...]) -> str:
    """责任台账的内容指纹（**只**覆盖 §L1.5 的输入轴与 typed `basis`，**不覆盖**检索结果）。

    按 `(aspect_id, 四轴身份)` 规范排序后再哈希，使「同一责任台账、不同遍历顺序」得到同一值，
    而「看见结果后倒填的责任」必然得到另一个值——这是「责任在检索前定死」的可测不变量。

    排序键用**四轴**而不是 `document_id`（定点返修 T4）：只按 id 排序时，两份同 id、不同版本
    的行谁在前取决于输入顺序，同一个台账会有两个指纹。
    """
    ordered = sorted(responsibilities,
                     key=lambda r: (r.aspect_id,
                                    SM.source_key_axes(r.source_document_key)))
    return sha256_canonical([r.to_dict() for r in ordered])


def _unfulfilled_impact(resp: AspectSourceResponsibility) -> str:
    """臂 C2 的影响：由**检索前责任**决定，不由结果决定。

    `proof_required == "true"` 的成员是本来源集里**唯一**可能承担集合完整性证明的那一份
    （见 §L1.5 规则 3c）；它没查成，该 aspect 就不再有取得证明的途径，故影响是
    `aspect_must_not_set_complete`。其余情形只影响该 aspect 的检索审计是否成立。
    """
    return ("aspect_must_not_set_complete" if resp.proof_required == "true"
            else "aspect_search_audit_not_satisfied")


def derive_source_aspect_outcomes(
        *, sources: DocumentSourceSet,
        responsibilities: tuple[AspectSourceResponsibility, ...],
        material_ids_by_aspect_source: dict[tuple[str, Any], tuple[str, ...]],
        search_traces: dict[str, list[dict]],
        not_found_audits_by_aspect: dict[str, Any],
        unfulfilled_reason_by_aspect: dict[str, str],
        dispatched_without_call: dict[tuple[str, Any], str] | None = None,
) -> tuple[TS.SourceAspectOutcome, ...]:
    """§0.3.3 四臂：逐 `(aspect, 来源)` 把**真实发生过的事**写成一条结果记录。

    铁律：**臂由真实发生过的事决定，不由责任记录决定**。责任说「这份不必查」而事实是查了，
    那条记录仍必须是「查过」（臂 A / B / C2），不得改写成为 C1——否则「我们多查了」这件事
    会在读回时消失。反过来，责任说「必须查」而一次调用都没发出，才是臂 C2 的
    `not_dispatched`，且**不得**为它造 `call_id`（没有发生过的事不能在痕迹里出现）。

    臂的判定顺序（不得颠倒）：

    1. 该 `(aspect, 来源)` 有真实调用痕迹且有材料 ⇒ **臂 A**（材料 id 逐一来自本 Pack）；
    2. 有真实调用痕迹、无材料，且至少一次调用**完成**（`SUCCESS`/`EMPTY`）⇒ **臂 B**：
       合格未命中（`qualified=true`）必须引到一张**真实存在**的 aspect 级 `NotFoundAudit`；
       取不到证书时仍落臂 B，但 `qualified=false` 并逐条写明 typed 理由
       （:data:`ARM_B_UNQUALIFIED_REASONS`）——**不是**把观察改判成臂 C2；
    3. 有真实调用痕迹、无材料，且无任何完成调用 ⇒ **臂 C2**，理由是真实的失败/截断原因；
    4. 无调用痕迹：责任说无需检索 ⇒ **臂 C1**（带 typed 依据）；责任说必须检索 ⇒
       **臂 C2**，理由取自 `dispatched_without_call`（该 `(aspect, 文档)` **派发发生过**、
       但没有发出任何调用：导航无候选 / 派发处预算不足），不在其中的才是 `not_dispatched`
       （**从未派发**，例如单文档导航下能力上限所致的非锚必需成员）。

    第 4 条的那两档必须分开（2026-09-25 定点返修 C2）：r3 实测 73 条臂 C2 的签名**完全同形**
    （`not_dispatched` + `attempts=[]` + `searched_need_ids=[]`），于是「已派发但导航给不出候选」
    被读成「从未派发」，进而被归因到写作/整束失败——一个与事实无关的结论。派发是否发生是
    **研究相位**的事实，由 `dispatched_without_call` 如实带入，**不由结果倒推**；两档都仍然
    `attempts=()`、不造 `call_id`——没有发生过的调用不得在痕迹里出现。

    第 2 条里「取不到证书」不是罕见边角：本来源集里 aspect 在**甲份**取到材料、在**乙份**
    查完却没有材料时，aspect 级未命中审计**按构造不存在**（它只在整条 aspect 一份材料、一条
    事实都没有时才派生）。此时补一张证书等于看见结果后倒填资格，改判 C2 则把「查过」写成
    「没查成」——两者都是失真。正确的形态是**保留臂 B 的观察、不签发合格性**。

    第 4 条是「注册了三份 ≠ 读了三份」的落点：未被分派的成员在这里留下的是**一条明写的
    未检索记录**，而不是一片沉默，也不是一个假的「未命中」。

    `material_ids_by_aspect_source` 的键是 `(aspect_id, 四轴身份)`：材料必须归到**产出它的
    那一次调用寻址的那一份**上。逐成员派发之后，同一条 aspect 在不同文档上各自产出材料，
    按 aspect 汇总会把 A 份的材料记到 B 份名下——臂 A 于是会凭一份看不懂是谁的材料成立。

    「那一份」的粒度是**四轴**而不是 `document_id`（定点返修 T4）：同 id、不同
    `document_version` / `evidence_set_version` 是两份不同的文档，按 id 关联会把甲份的调用
    痕迹与材料记到乙份名下，臂 A / 臂 B / 臂 C2 的判定也就跟着错位。三个逐份输入面
    （材料 / 派发结论 / 调用痕迹）因此都在四轴上核对；键里出现本集合外的身份一律 fail-closed。
    """
    material_by_axes = _by_aspect_axes(
        material_ids_by_aspect_source, sources, "material_ids_by_aspect_source")
    dispatch_outcomes = _by_aspect_axes(
        dispatched_without_call or {}, sources, "dispatched_without_call")
    known_material_ids = {mid for ids in material_by_axes.values() for mid in ids}
    by_pair = {(r.aspect_id, SM.source_key_axes(r.source_document_key)): r
               for r in responsibilities}
    outcomes: list[TS.SourceAspectOutcome] = []
    for (aspect_id, axes), resp in by_pair.items():
        key = resp.source_document_key
        traces = [t for t in search_traces.get(aspect_id, ())
                  if t.get("source_key") is not None
                  and SM.source_key_axes(t["source_key"]) == axes]
        materials = tuple(material_by_axes.get((aspect_id, axes), ()))
        if traces and materials:
            outcomes.append(TS.SourceAspectOutcome(
                aspect_id=aspect_id, source_document_key=key, arm="A",
                responsibility_fingerprint=resp.fingerprint(),
                material_ids=tuple(m for m in materials if m in known_material_ids),
                search_record=None, not_required_basis=()))
            continue
        if traces:
            complete = [t for t in traces if t["status"] in TS.COMPLETE_ATTEMPT_STATUSES]
            if complete:
                # 合格未命中必须挂在一张**真实存在**的 aspect 级证书上；本函数不颁发证书，
                # 只读 `derive_not_found_audit` 已经算出来的那一张（判据见 §0.3.3 第 4 条）。
                audit = not_found_audits_by_aspect.get(aspect_id)
                reasons: list[str] = []
                if audit is None:
                    # 该 aspect **在别的来源上取得了材料**：于是从没有人给它派生过 aspect 级
                    # 未命中审计，本份的「查完却没有材料」也就无从取得合格未命中的证书。
                    # 这里**不得**就地补一张证书（那是看见结果后倒填资格），也**不得**改判成
                    # 臂 C2（C2 的三个理由码都在说「该查没查成」，而这一次调用是完成的）。
                    # 如实记成臂 B + 一条 typed 理由：观察成立，合格性无证书。
                    reasons.append(NO_ASPECT_CERTIFICATE_REASON)
                else:
                    reasons.extend(audit.qualification_reasons)
                    if len(complete) != len(traces):
                        # Y-12/Y-14：本份自己还有失败/截断的调用 ⇒ 本份不得落合格未命中，
                        # 即便 aspect 级证书是合格的。
                        reasons.append(DOC_INCOMPLETE_CALLS_REASON)
                    if any(t.get("stop_reason") in BUDGET_STOP_REASONS for t in traces):
                        reasons.append(DOC_BUDGET_STOPPED_REASON)
                qualified = not reasons
                record = TS.SourceSearchOutcomeRecord(
                    record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
                    aspect_id=aspect_id, source_document_key=key, arm="B",
                    audit_id=(audit.audit_id if audit is not None else None),
                    attempts=_attempts(traces),
                    synthesized_stop_reason=_synthesized_stop_reason(traces),
                    searched_need_ids=(audit.searched_need_ids if audit is not None
                                       else tuple(t["call_id"] for t in traces)),
                    valid_attempt_count=len(complete),
                    # 时间窗只由 aspect 级证书给出（它由 `aspect.time_scope` 派生）。没有证书
                    # 就没有被审计过的时间窗——**如实留空**，不得拿别处的期间顶上。
                    time_window=(audit.time_window if audit is not None else ""),
                    qualified=qualified,
                    qualification_reasons=tuple(reasons),
                    projected_terminal=("NOT_FOUND_AFTER_SEARCH" if qualified
                                        else "UNQUALIFIED_SEARCH_OBSERVATION"),
                    unfulfilled_reason=None,
                    responsibility_fingerprint=resp.fingerprint(),
                    impact=None)
                outcomes.append(TS.SourceAspectOutcome(
                    aspect_id=aspect_id, source_document_key=key, arm="B",
                    responsibility_fingerprint=resp.fingerprint(), material_ids=(),
                    search_record=record, not_required_basis=()))
                continue
            reason = unfulfilled_reason_by_aspect.get(aspect_id, "call_failed")
            record = TS.SourceSearchOutcomeRecord(
                record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
                aspect_id=aspect_id, source_document_key=key, arm="C2",
                audit_id=None, attempts=_attempts(traces),
                synthesized_stop_reason=_first_stop_reason(traces),
                searched_need_ids=tuple(t["call_id"] for t in traces),
                valid_attempt_count=0, time_window="",
                qualified=False, qualification_reasons=(),
                projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
                unfulfilled_reason=reason,
                responsibility_fingerprint=resp.fingerprint(),
                impact=_unfulfilled_impact(resp))
            outcomes.append(TS.SourceAspectOutcome(
                aspect_id=aspect_id, source_document_key=key, arm="C2",
                responsibility_fingerprint=resp.fingerprint(), material_ids=(),
                search_record=record, not_required_basis=()))
            continue
        # 无任何调用痕迹。
        if not resp.retrieval_required:
            basis = tuple(row for row in resp.basis
                          if row[0] in TS.NOT_REQUIRED_BASIS_RULE_IDS)
            outcomes.append(TS.SourceAspectOutcome(
                aspect_id=aspect_id, source_document_key=key, arm="C1",
                responsibility_fingerprint=resp.fingerprint(), material_ids=(),
                search_record=None, not_required_basis=basis))
            continue
        record = TS.SourceSearchOutcomeRecord(
            record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
            aspect_id=aspect_id, source_document_key=key, arm="C2",
            audit_id=None, attempts=(), synthesized_stop_reason=None,
            searched_need_ids=(), valid_attempt_count=0, time_window="",
            qualified=False, qualification_reasons=(),
            projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
            # **派发发生过**的成员用导航/预算给出的真实结论，只有**从未派发**的才是
            # `not_dispatched`：两档都无调用痕迹，但不区分就会把「导航给不出候选」
            # 读成「从未派发」（r3 的 73 条正是这样被误归因的）。
            unfulfilled_reason=dispatch_outcomes.get((aspect_id, axes),
                                                     "not_dispatched"),
            responsibility_fingerprint=resp.fingerprint(),
            impact=_unfulfilled_impact(resp))
        outcomes.append(TS.SourceAspectOutcome(
            aspect_id=aspect_id, source_document_key=key, arm="C2",
            responsibility_fingerprint=resp.fingerprint(), material_ids=(),
            search_record=record, not_required_basis=()))
    return tuple(outcomes)


#: 臂 B **未取得合格未命中证书**时的 typed 理由码（封闭集合）。
#:
#: 三者都只说明「为什么这一份的观察不构成合格未命中」，都**不**把观察改判成臂 C2：
#: C2 的三个 `unfulfilled_reason` 都在说「该查没查成」，而手臂 B 的这三条都发生在
#: **有完成调用**的前提下。混成 C2 会让「查过」在读回时消失。
ARM_B_UNQUALIFIED_REASONS = (
    # 该 aspect 在**别的来源**上取得了材料 ⇒ 从没有人给它派生过 aspect 级未命中审计。
    # 本份「查完却没有材料」是事实，但合格性无从签发；就地补一张证书就是看见结果后倒填资格。
    "no_aspect_level_not_found_audit",
    # 本份自己的调用里既有完成也有失败/截断 ⇒ 本份的检索没有被完整执行（Y-12/Y-14）。
    "document_has_incomplete_calls",
    # 本份自己的调用承认了预算类停止 ⇒ 是「被截断」而不是「范围内确无」。
    "document_budget_stopped",
)
NO_ASPECT_CERTIFICATE_REASON = ARM_B_UNQUALIFIED_REASONS[0]
DOC_INCOMPLETE_CALLS_REASON = ARM_B_UNQUALIFIED_REASONS[1]
DOC_BUDGET_STOPPED_REASON = ARM_B_UNQUALIFIED_REASONS[2]


def _attempts(traces: list[dict]) -> tuple[TS.SourceSearchAttempt, ...]:
    return tuple(TS.SourceSearchAttempt(
        call_ordinal=t["call_ordinal"], call_id=t["call_id"],
        requested_source_key=t["source_key"],
        # 身份回声只证明「分派到了这一份」，不证明该份的范围被覆盖；未确认归因时留空。
        actual_source_key=t.get("actual_source_key"),
        requested_scope=tuple(t.get("requested_scope") or ()),
        unread_scope=tuple(t.get("unread_scope") or ()),
        status=t["status"], error_code=t.get("error_code"),
        stop_reason=t.get("stop_reason")) for t in traces)


def _first_stop_reason(traces: list[dict]) -> str | None:
    """按**真实调用序号**取第一条失败/截断/预算停止的结局（不按源集序位）。"""
    for t in sorted(traces, key=lambda x: x["call_ordinal"]):
        if t["status"] not in TS.COMPLETE_ATTEMPT_STATUSES:
            return t.get("stop_reason") or t.get("error_code") or t["status"]
    return None


def _synthesized_stop_reason(traces: list[dict]) -> str:
    """按**该文档自己的**调用序号折叠停止原因（§0.3.3 第 3 条）。

    取第一条失败/截断/预算停止的结局；本份没有任何此类调用时取 `NOT_FOUND_AFTER_SEARCH`
    ——那是「查完了、范围内没有」的结局名，**不是**合格性判定（合格性由 `qualified` 与它
    引用的 aspect 级证书单独承担）。**不得**改用别的文档或整个 aspect 的停止原因。
    """
    return _first_stop_reason(traces) or "NOT_FOUND_AFTER_SEARCH"


#: 外部漏斗记录的 schema / producer 版本（payload 内容寻址；不进 Pack 的其它字段）。
EXTERNAL_FUNNEL_SCHEMA_VERSION = "ext-funnel-1"
EXTERNAL_FUNNEL_PRODUCER_VERSION = "m930-2-runtime-1"

#: 可由 runtime **确定性证明** `not_applicable` 的 Contract applicability 规则集合。
#:
#: 冻结 Contract 中出现的取值只有 `None` / `"valid_no_controller"` /
#: `"not_applicable_no_comparable_period"` / `"not_applicable_no_plan"`：
#: `valid_no_controller` 证明的是"合法地没有实际控制人"（= 满足，不是不适用）；
#: 另两个要证明"确无计划 / 确无可比期间"必须依赖**正面证据**，而无证据不得推断不适用。
#: 因此本集合为空是**结论**而不是遗漏：没有可证明的 not_applicable 时，runtime 必须保留
#: blocked / partial，绝不伪造正例（§九）。
PROVABLE_NOT_APPLICABLE_RULES: frozenset[str] = frozenset()

#: 有界 Evidence 重切材料的**归属**策略版本（`fba-1`，M930-3 r9 后返修 C）。
#:
#: 判据只有一条：**读集为空时，兜底重切不得替这一栏造出读集**。锚对这一 aspect 的导航终态
#: 是 fallback（`read_node_ids == ()`：`low_confidence` / `no_anchored_read_root` /
#: `explicit_cross_reference`，见 `document_structure/navigation.py` 的三处 `_decision`），
#: 或锚这一轮**根本没有导航决策**时，重切交出的材料**不得**登记为该 aspect 的栏目材料
#: （`AspectResearchResult.material_ids` / 覆盖门 / 事实闭环）。
#:
#: 为什么：`material_ids` 是"这一栏目按标题树读到了什么"的落点，而重切材料只是"兜底这一次
#: 调用交出了什么"。r6/r9 实证——挂在 `founded_date` 下的股东/实控人勾选行、挂在客户集中度
#: 下的"不适用"行都**证明不了**那一栏，却以"该 aspect 读到的材料"的身份进了 `material_ids`，
#: 于是「兜底兜到的」被读成「这一栏目真的读到了」。验收端的 `mar-1` 只读诊断能看出这条差别，
#: 但**产物里读不出来**——这条规则把它落到根上，而不是让读过的人都记得别误解。
#:
#: 材料本身不丢：仍进 Pack 全局材料集（可回查、可被支撑边精确引用）、仍发
#: `EVIDENCE_FALLBACK_RECUT` / `TREE_CONTENT_DISPOSITION` 事件、仍按四轴身份记进
#: `material_ids_by_aspect_source`（"这一次调用真的交出了这些材料"是**调用**的事实，
#: 四臂台账照旧据此落臂 A）。被挡下的只是**栏目归属**这一件事，并另记一条 typed gap。
EVIDENCE_FALLBACK_ATTRIBUTION_VERSION = "fba-1"

#: 读集为空时重切材料**不予归属**的封闭原因（`fba-1`）：重切真的交出了 span，但它们
#: 不能充当这一栏目的材料。与 `evidence_fallback_no_span`（重切什么都没交出来）分开：
#: 前者是"交出来了、归属没落上"，后者是"什么都没交出来"。
EVIDENCE_FALLBACK_UNATTRIBUTED_REASON = "evidence_fallback_unattributed"

#: runtime 自己的**声明型** gap 原因（只出现在 `TopicRuntimeResult.gaps`）。
#: Pack 内的 `ResearchGap.reason_code` 仍必须取自 `TS.GAP_REASON_CODES`，两者不混用：
#: 前者是"这一轮为什么止步"，后者是 Store 语义化的缺口分类。
RUNTIME_GAP_REASONS = (
    "tree_structure_unavailable",
    "tree_no_material",
    "tree_material_gaps",
    "tree_material_bounded_out",
    # §L4.2：本 aspect 需要逐份派发，但派到某一份时 topic 级工具预算已不足以再发一次真实
    # 调用。与 `tree_no_material` 分开：那是「查了但没有材料」，这是「还该查却没查成」——
    # 两者在四臂台账里落成不同的臂，混成一条会让「预算截断」被读成「查过且没有」。
    "tree_budget_exhausted",
    # §二 2.2：Contract `content_role` 声明了**表格槽位**的 aspect，本轮材料里没有任何
    # 表格类材料。这条通道**结构性**不产表格材料（树材料人口只收 `role=="body"` 的正文
    # span，表内内容天然不进人口），因此必须是一条 typed gap——否则读回的人只会看到
    # "零材料"，分不清「查过、确实没有」与「这条通道根本不产」。
    "tree_table_material_unavailable",
    "focused_research_no_supported_fact",
    "focused_research_budget_exhausted",
    # §七 有界 Evidence fallback：候选父 Evidence 拿不到可重切 span / 预算不足
    "evidence_fallback_no_span",
    "evidence_fallback_budget_exhausted",
    # §七 + `fba-1`：重切交出了 span 材料，但锚的导航读集为空（兜底不得替这一栏造读集），
    # 因此这些材料没有被登记为该 aspect 的栏目材料。材料仍在 Pack 全局材料集与事件留痕里。
    EVIDENCE_FALLBACK_UNATTRIBUTED_REASON,
    "aspect_terminal_gap",
    # M930-3 业务取材纵链（`m930-3-tool-axis-1`）：本栏目未达 covered 的**逐条**原因。
    # 这一条本身不携带原因（原因是它自己的 `unmet_column_reasons` 键，取自闭集
    # `UNMET_COLUMN_REASONS`）；它是「粗粒度覆盖门」与「逐条 typed 原因」之间的桥，
    # 使读回的人不必再从 `coverage_gate_not_met` 与「材料不存在」里猜是哪一种。
    "column_unmet",
    # §九 终态判定：不满足 `not_found` 的确定性条件时**明说**缺哪一条（不得径直写 not_found）；
    # 以及 Contract 确定性规则证明为不适用的 aspect（不得由无证据/模型判断推断）。
    "not_found_conditions_unmet",
    # §九 P1-B：应有检索范围**证不出来**（冻结 EvidenceRequirement 未声明可比较的来源类，
    # 或声明了某个来源类却没有任何真实轨迹证明查过）时的封闭原因。与上一条分开，因为
    # 「还有别的条件没齐」和「范围本身不可证」是两种不同的止步理由，审计要读得出来。
    "required_search_scope_unproven",
    "aspect_not_applicable",
    # `_coverage_gate_reason` 的原因码（不得判 covered 的确定性理由）
    "coverage_rule_not_evaluable",
    "coverage_gate_not_met",
    "usage_scope_not_satisfied",
    "inference_lineage_required",
    "set_completeness_proof_unavailable",
    # M930-3A §16.3 #19：Contract 必需 aspect/fact 仍未取得时**另外**形成的 authoritative
    # research gap / block。它们是 typed `ContractGap`/`ResearchBlock` 的声明型投影，与
    # candidate rejection 完全无关（rejection 不会自动制造 gap）。
    "research_contract_gap",
    "research_block",
)

#: 冻结 Contract 的 `content_role` 里**带这个词**即声明了表格槽位（实际取值
#: `paragraph_and_table`）。判据是角色名本身，不是公司/年份/页码专用规则；写成标记词
#: 而不是穷举取值，是为了让 Contract 将来新增表格类角色时不会静默漏判。
TABLE_CONTENT_ROLE_MARKER = "table"

#: 表格槽位只能由表格类材料供给（`TS.MATERIAL_TYPES` 里的 `table_context`）。树通道
#: 只产出 `evidence_span`：`is_tree_material_candidate` 只收 `role=="body"`，表内内容
#: 天然不进材料人口。因此「树通道本轮无表格材料」是**结构性**事实，必须显式登记。
TABLE_MATERIAL_TYPE = "table_context"


def _requires_table_output(aspect: TS.TopicAspectRequirementSnapshot) -> bool:
    """该 aspect 的冻结 Contract `content_role` 是否声明了表格槽位。

    只读 Contract 自己声明的角色名，不推断、不看正文、不按公司/年份特判。
    """
    role = str(getattr(aspect, "content_role", "") or "").strip().lower()
    return TABLE_CONTENT_ROLE_MARKER in role.replace("-", "_").split("_")


class TopicRuntimeError(TS.SchemaValidationError):
    """runtime 输入/依赖/装配的 fail-closed 错误。"""


#: 运行标识允许的字符集。call_id 会被既有 `ToolRegistry` 用作 audit 文件名、run_id 会被
#: 用作 audit 目录名，因此含 `:` `/` 等字符的 id 会在 Windows 上直接落盘失败
#: （`[Errno 22] Invalid argument`）。这里**显式拒绝**而不是静默改名：静默改名会让
#: audit 轨迹与内容身份对不上。
_SAFE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _require_safe_id(value: str, what: str) -> str:
    if not isinstance(value, str) or value == "":
        raise TopicRuntimeError(f"{what} 必须为非空字符串")
    bad = sorted(set(value) - _SAFE_ID_CHARS)
    if bad:
        raise TopicRuntimeError(
            f"{what}={value!r} 含不可用作 audit 文件名/目录名的字符 {bad}"
            f"（只允许 A-Za-z0-9._-；fail-closed，不静默改名）")
    return value


def _join_id(*parts: str) -> str:
    return _require_safe_id("--".join(parts), "复合运行标识")


# ---------------------------------------------------------------------------
# 1. 只读导航提供方（只给 node id 与分数，不含任何内容）
# ---------------------------------------------------------------------------

class TreeNavigationProvider(Protocol):
    """把一个 aspect 映射为**候选 node 集合**的只读能力。

    实现只能暴露 node id / 分数 / 版本；不得暴露 span 文本、Evidence 或任何 payload。
    `candidates` 必须按**当前 aspect 与 requirement 的真实身份**取候选并逐项闭合；
    不接受裸 aspect_id 字符串（那正是"用别的 profile 导航"的入口）。
    """

    def candidates(self, aspect: TS.TopicAspectRequirementSnapshot, *,
                   requirement: TS.TopicResearchRequirement) -> NAV.NavigationDecision:
        ...


def _verify_navigation_identity(*, profile: NAV.AspectNavigationProfile,
                                entries: dict, aspect: TS.TopicAspectRequirementSnapshot,
                                requirement: TS.TopicResearchRequirement) -> None:
    """导航身份闭合（§四）：profile 与 aspect 快照必须逐项对得上，否则 fail-closed。

    抽成自由函数是**为了跨源实现复用同一条判据**：单文档 `IndexedTreeNavigation` 与跨源
    `SourceSetTreeNavigation` 必须用**同一套**身份复核，否则「换一份文档就少查一项」这种
    漂移会在两条路径之间悄悄出现。
    """
    if not isinstance(aspect, TS.TopicAspectRequirementSnapshot):
        raise TopicRuntimeError(
            "candidates 需要 TopicAspectRequirementSnapshot（不接受裸 aspect_id）")
    if not isinstance(requirement, TS.TopicResearchRequirement):
        raise TopicRuntimeError("candidates 需要 TopicResearchRequirement")
    aspect_id = aspect.aspect_id
    profile_id = profile.profile_id

    # 1) profile 的 Contract 身份必须与当前 requirement 一致。
    if profile.contract_version != requirement.contract_version:
        raise TopicRuntimeError(
            f"导航 profile {profile_id!r} 的 contract_version="
            f"{profile.contract_version!r} 与 requirement="
            f"{requirement.contract_version!r} 不一致（不得跨 Contract 导航）")
    if profile.contract_fingerprint != requirement.contract_fingerprint:
        raise TopicRuntimeError(
            f"导航 profile {profile_id!r} 的 contract_fingerprint 与 requirement "
            f"不一致（Contract 漂移，fail-closed）")

    # 2) profile 中不得存在当前 requirement 之外的同 ID 冒充：aspect 集合必须恰等。
    expected_ids = {a.aspect_id for a in requirement.aspects}
    if set(entries) != expected_ids:
        extra = sorted(set(entries) - expected_ids)
        missing = sorted(expected_ids - set(entries))
        raise TopicRuntimeError(
            f"导航 profile {profile_id!r} 的 aspect 集合与 requirement "
            f"{requirement.topic_id!r} 不一致（多余 {extra[:5]}，缺失 {missing[:5]}）；"
            f"不得用别的 topic/requirement 的 profile 导航")

    entry = entries[aspect_id]
    # 3) 同 ID 也必须同 question/topic：不接受"ID 相同、身份不同"的冒充。
    if entry.question_id != aspect.question_id:
        raise TopicRuntimeError(
            f"导航条目 {aspect_id!r} 的 question_id={entry.question_id!r} 与 aspect "
            f"快照 {aspect.question_id!r} 不一致（身份漂移，fail-closed）")
    if entry.topic_id != requirement.topic_id:
        raise TopicRuntimeError(
            f"导航条目 {aspect_id!r} 的 topic_id={entry.topic_id!r} 与 requirement "
            f"{requirement.topic_id!r} 不一致（身份漂移，fail-closed）")


class IndexedTreeNavigation:
    """由 `NavigationIndex` + `AspectNavigationProfile` 构成的确定性导航实现。

    只持有标题/路径/简介与节点拓扑，**没有 span、没有 Evidence、没有 authority**，
    因此它的输出不可能「顺带」变成证据。

    身份闭合（§四）：`candidates(aspect, requirement=...)` 逐项验证
    aspect_id / question_id / topic_id / Contract version+fingerprint，并要求 profile
    的 aspect 集合**恰等于**当前 requirement 的 aspect 集合（不存在同 ID 冒充）。
    任一不一致 → 具名 fail-closed，绝不"就近取一个 aspect"。
    """

    __slots__ = ("_index", "_profile", "_limits", "_entries", "_decisions")

    def __init__(self, *, index: NAV.NavigationIndex,
                 profile: NAV.AspectNavigationProfile,
                 limits: NAV.NavigationLimits | None = None) -> None:
        if not isinstance(index, NAV.NavigationIndex):
            raise TopicRuntimeError("IndexedTreeNavigation 需要 NavigationIndex")
        if not isinstance(profile, NAV.AspectNavigationProfile):
            raise TopicRuntimeError("IndexedTreeNavigation 需要 AspectNavigationProfile")
        self._index = index
        self._profile = profile
        self._limits = limits if limits is not None else NAV.NavigationLimits()
        self._entries = {e.aspect_id: e for e in profile.entries}
        self._decisions: dict[str, NAV.NavigationDecision] = {}

    @property
    def profile_id(self) -> str:
        return self._profile.profile_id

    @property
    def rule_version(self) -> str:
        return self._profile.rule_version

    @property
    def navigation_profile(self) -> NAV.AspectNavigationProfile:
        """只读导航 profile（读取计划 `rpo-1` 取 **topic 级** Contract 键并集用）。

        交出的是导航**已经在用**的那一份冻结投影，不另建一份键表。
        """
        return self._profile

    @property
    def outline_id(self) -> str:
        return self._index.outline_id

    def candidates(self, aspect: TS.TopicAspectRequirementSnapshot, *,
                   requirement: TS.TopicResearchRequirement) -> NAV.NavigationDecision:
        """按当前 requirement 的真实身份取候选；任何身份漂移一律 fail-closed。"""
        _verify_navigation_identity(
            profile=self._profile, entries=self._entries, aspect=aspect,
            requirement=requirement)
        aspect_id = aspect.aspect_id

        cached = self._decisions.get(aspect_id)
        if cached is None:
            cached = NAV.navigate(self._index, self._entries[aspect_id],
                                  profile=self._profile, limits=self._limits)
            self._decisions[aspect_id] = cached
        # 4) decision 自身也必须闭合（不采信实现自报的身份）。
        if (cached.aspect_id != aspect_id or cached.question_id != aspect.question_id
                or cached.topic_id != requirement.topic_id):
            raise TopicRuntimeError(
                f"导航 decision 身份与当前 aspect/requirement 不闭合："
                f"decision=({cached.aspect_id!r},{cached.question_id!r},{cached.topic_id!r}) "
                f"期望=({aspect_id!r},{aspect.question_id!r},{requirement.topic_id!r})")
        return cached

    def navigation_index_for(self, source_key: Any, *, anchor: Any = None
                             ) -> NAV.NavigationIndex:
        """本提供方为**这一份**算读集所用的只读导航索引（读取计划 `rpo-1` 的输入）。

        交出的必须**正是**产出该读集的那一棵索引：拿到另一份的 `body_chars` 会把排序建在
        别人的树上（静默错配）。单文档提供方只服务**锚**——非锚请求一律 fail-closed，
        与 `_navigate_one_source` 的判据逐字同源。
        """
        if source_key is None:
            raise TopicRuntimeError("取导航索引必须显式给出那一份来源")
        if anchor is not None and source_key != anchor:
            raise TopicRuntimeError(
                f"单文档导航提供方只服务锚 {getattr(anchor, 'document_id', anchor)!r}，"
                f"被要求对 {getattr(source_key, 'document_id', source_key)!r} 取索引："
                "fail-closed（不得用另一份的树给本读集排序）")
        return self._index

    def node_ids_for(self, decision: NAV.NavigationDecision) -> tuple[str, ...]:
        """**有界读集**（确定性；顺序即"候选强度优先"的读取顺序）。

        - `selected` → `decision.read_node_ids`：近分带各根下潜后的子树并集，已由
          `NavigationLimits.max_subtree_nodes` 截断；被截掉的范围在
          `decision.unread_node_ids` 里如实记录，**不当作"不相关"**；
        - `low_confidence` → **空集**：阈值之下的候选可见（`decision.ranked` 带分数与
          舍弃原因）但**不读**——没有任何声明键命名到树上的东西时，扩大读取只会把
          噪声当材料，这里 fail-closed（§5.5 的「有界 fallback」是候选可见、不是整块
          Evidence 升格）；
        - `structurally_unavailable` / `no_navigation_keys` / `explicit_cross_reference`
          → 空集（由调用方登记显式 gap）。
        """
        if decision.status == "selected":
            return tuple(decision.read_node_ids)
        return ()


class SourceSetTreeNavigation:
    """由 `SourceSetNavigationIndex` + `AspectNavigationProfile` 构成的**跨源**只读导航（§L2/L3）。

    与 `IndexedTreeNavigation` 的差别**只有域**：同一套导航规则、同一个 profile、同一批
    fail-closed 身份复核，逐份来源各跑一次。跨文档的「较新优先」**不进入导航打分**（§L2.5
    禁止项）——哪一份有材料是**研究/材料层**的决定，导航只回答「这个 aspect 在这份文档的这棵
    树上是哪个节点」。

    `candidates(...)` 仍返回**当前锚**那一份的决策（单文档读视图，签名与单文档实现一致）；
    `candidates_for(..., source_key=...)` 返回**指定那一份**的决策，**精确查表、无回退**：
    源集里没有这份键即 fail-closed。回退等于把「声明了 B、拿到 A」变成静默错配。
    """

    __slots__ = ("_indexes", "_profile", "_limits", "_entries", "_decisions", "_anchor_key")

    def __init__(self, *, indexes: NAV.SourceSetNavigationIndex,
                 profile: NAV.AspectNavigationProfile,
                 anchor_key: dict,
                 limits: NAV.NavigationLimits | None = None) -> None:
        if not isinstance(indexes, NAV.SourceSetNavigationIndex):
            raise TopicRuntimeError(
                "SourceSetTreeNavigation 需要 SourceSetNavigationIndex")
        if not isinstance(profile, NAV.AspectNavigationProfile):
            raise TopicRuntimeError("SourceSetTreeNavigation 需要 AspectNavigationProfile")
        if not isinstance(anchor_key, dict) or not anchor_key:
            raise TopicRuntimeError("SourceSetTreeNavigation 需要非空的当前锚文档键")
        if indexes.index_for(anchor_key) is None:
            raise TopicRuntimeError(
                f"当前锚 {anchor_key!r} 不在源集导航索引内（fail-closed，不取别的份）")
        self._indexes = indexes
        self._profile = profile
        self._limits = limits if limits is not None else NAV.NavigationLimits()
        self._entries = {e.aspect_id: e for e in profile.entries}
        self._decisions: dict[tuple, NAV.NavigationDecision] = {}
        self._anchor_key = dict(anchor_key)

    @property
    def profile_id(self) -> str:
        return self._profile.profile_id

    @property
    def rule_version(self) -> str:
        return self._profile.rule_version

    @property
    def navigation_profile(self) -> NAV.AspectNavigationProfile:
        """只读导航 profile（与单文档实现**同一份**冻结投影，不另建键表）。"""
        return self._profile

    def candidates(self, aspect: TS.TopicAspectRequirementSnapshot, *,
                   requirement: TS.TopicResearchRequirement) -> NAV.NavigationDecision:
        """当前锚那一份的决策（单文档读视图）。"""
        return self.candidates_for(aspect, requirement=requirement,
                                   source_key=self._anchor_key)

    def candidates_for(self, aspect: TS.TopicAspectRequirementSnapshot, *,
                       requirement: TS.TopicResearchRequirement,
                       source_key: dict) -> NAV.NavigationDecision:
        """指定来源那一份的决策；身份逐项闭合，取不到该份即 fail-closed。"""
        _verify_navigation_identity(
            profile=self._profile, entries=self._entries, aspect=aspect,
            requirement=requirement)
        index = self._indexes.index_for(source_key)
        if index is None:
            raise TopicRuntimeError(
                f"源集导航索引里没有文档 {source_key!r}；"
                f"已有 {list(self._indexes.document_keys())}（不回退到其中任何一份）")
        marker = (aspect.aspect_id, tuple(sorted(source_key.items())))
        cached = self._decisions.get(marker)
        if cached is None:
            cached = NAV.navigate(index, self._entries[aspect.aspect_id],
                                  profile=self._profile, limits=self._limits)
            self._decisions[marker] = cached
        if (cached.aspect_id != aspect.aspect_id
                or cached.question_id != aspect.question_id
                or cached.topic_id != requirement.topic_id):
            raise TopicRuntimeError(
                f"导航 decision 身份与当前 aspect/requirement 不闭合："
                f"decision=({cached.aspect_id!r},{cached.question_id!r},"
                f"{cached.topic_id!r}) 期望=({aspect.aspect_id!r},"
                f"{aspect.question_id!r},{requirement.topic_id!r})")
        return cached

    def navigation_index_for(self, source_key: Any, *, anchor: Any = None
                             ) -> NAV.NavigationIndex:
        """**这一份**的只读导航索引（跨源提供方按四轴身份精确查表，**不回退**）。

        `anchor` 在这里不参与判定：跨源域本来就要逐份各取各的树，回退到锚才是错。
        """
        if source_key is None:
            raise TopicRuntimeError("取导航索引必须显式给出那一份来源")
        index = self._indexes.index_for(source_key.to_dict())
        if index is None:
            raise TopicRuntimeError(
                f"源集导航索引里没有文档 {source_key.to_dict()!r}；"
                f"已有 {list(self._indexes.document_keys())}（不回退到其中任何一份）")
        return index

    def node_ids_for(self, decision: NAV.NavigationDecision) -> tuple[str, ...]:
        """有界读集；判据与单文档实现**逐字相同**（见 `IndexedTreeNavigation.node_ids_for`）。"""
        if decision.status == "selected":
            return tuple(decision.read_node_ids)
        return ()


# ---------------------------------------------------------------------------
# 2. 预算政策与用量账本
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchBudgetPolicy:
    """topic 级预算政策（版本化；进入 `BudgetPolicySnapshot`，但不含时间/用量）。"""

    policy_id: str
    version: str
    tier: str
    max_need_rounds_per_aspect: int
    #: **声明并校验，但全仓没有读取点**（`grep -rn max_need_rounds_per_topic` 只命中本文件这一处
    #: 与校验循环）。因此它**不是**「每个 topic 最多 N 轮 need」这一条生效规则：Contract 的一个
    #: topic 有多个 aspect，首轮广覆盖本身就可能超过两次，把 2 机械地套在 topic 的 need 次数上
    #: 会错误截断首轮。真正生效的有限门是每 aspect 轮次（`max_need_rounds_per_aspect`，经
    #: `BudgetForNeed.build()` → 真实 `MaxRounds` 生效）与 topic 级预算（`can_afford` 事前预留 +
    #: `charge_need` 事后复核）。保留字段是为了不改写既有政策身份；**不得**在报告或读回里把它
    #: 当作已生效的波次上界引用（验收报告按 `in_effect=False` 如实登记）。
    max_need_rounds_per_topic: int
    max_tool_calls_per_topic: int
    max_tokens_per_topic: int
    max_llm_calls_per_topic: int
    max_elapsed_ms_per_topic: int
    tree_max_spans_per_aspect: int = 20
    tree_max_chars_per_span: int = 4000

    def __post_init__(self) -> None:
        for name in ("policy_id", "version", "tier"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise TopicRuntimeError(f"ResearchBudgetPolicy.{name} 必须为非空字符串")
        for name in ("max_need_rounds_per_aspect", "max_need_rounds_per_topic",
                     "max_tool_calls_per_topic", "max_tokens_per_topic",
                     "max_llm_calls_per_topic", "max_elapsed_ms_per_topic",
                     "tree_max_spans_per_aspect", "tree_max_chars_per_span"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise TopicRuntimeError(
                    f"ResearchBudgetPolicy.{name} 必须为正整数，得到 {value!r}")

    def canonical_hash(self) -> str:
        return sha256_canonical(dataclasses.asdict(self))

    def to_snapshot(self) -> TS.BudgetPolicySnapshot:
        # 语义版本取 §5.2.1 裁决 6 的具名常量；政策**内容**身份由 canonical_hash 承担。
        return TS.BudgetPolicySnapshot(
            schema_version=TS.TOPIC_BUDGET_POLICY_VERSION,
            canonical_hash=self.canonical_hash(), tier=self.tier)

    def need_budget(self) -> "BudgetForNeed":
        """单个 focused need 的 `harness.policies.ResearchBudget` 构造参数（确定性）。"""
        return BudgetForNeed(policy=self)


@dataclass(frozen=True)
class BudgetForNeed:
    policy: ResearchBudgetPolicy

    def build(self) -> Any:
        from harness.policies import ResearchBudget
        return ResearchBudget(
            max_rounds=self.policy.max_need_rounds_per_aspect,
            max_tool_calls=max(2, self.policy.max_need_rounds_per_aspect * 2),
            max_local_searches=4, max_external_searches=4, max_fetches=4,
            max_action_repairs=1, max_added_needs=0,
            max_consecutive_no_new_evidence=2,
            max_tokens=self.policy.max_tokens_per_topic,
            max_elapsed_ms=self.policy.max_elapsed_ms_per_topic,
            max_retries_per_call=0)


@dataclass
class TopicBudgetState:
    """topic 级累计用量/检查点（**运行账本**，不进内容身份以外的自证）。

    `usage` 会进入 Pack 的 `TopicUsageSnapshot`（因此参与 content fingerprint），
    但账本自身的对象身份、开始时间、trace 与 checkpoint **不**参与内容寻址。
    """

    policy: ResearchBudgetPolicy
    tool_calls: int = 0
    llm_calls: int = 0
    rounds: int = 0
    local_searches: int = 0
    external_searches: int = 0
    fetches: int = 0
    snapshots: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # 两个 elapsed 维度语义不同，绝不能混：
    # - `elapsed_ms` 是**内容用量**：只累加各次 `run_question` 自己上报的 `UsageLedger.elapsed_ms`。
    #   它随权威内容确定（同一 task + 同一内容跨 run 恒等），因此可以进 `to_usage_snapshot()`。
    # - `wall_clock_ms` 是**运行用量**：runtime 实测墙钟，含树工具等不上报 ledger 的耗时。
    #   它天然跨 run 浮动，属「开始时间/轨迹」一类运行数据，**绝不进 Pack 内容身份**
    #   （否则 `content_fingerprint` 含 usage 会让同一内容的 pack_id 每次运行都变，
    #   跨 run 幂等复用被静默破坏）。
    elapsed_ms: int = 0
    wall_clock_ms: int = 0
    checkpoint_ref: str | None = None
    stop_reason: str | None = None
    #: 已经**预留**给「尚未发起的初次必读」的工具调用数（见 `MANDATORY_FIRST_READ_RULE_VERSION`）。
    #: 可选调用（有界 Evidence 兜底重切 / focused follow-up）不得动用它：初次必读是每个
    #: `retrieval_required` 的 `(栏目, 来源)` 的最基本义务，被可选补件挤掉就等于把「该查没查」
    #: 记成「查过没命中」。它是**运行账本**字段，不进 `to_usage_snapshot()`，也不进任何内容身份。
    reserved_tool_calls: int = 0

    def reserve_tool_calls(self, count: int) -> None:
        """预留若干次**初次必读**调用（进入 aspect 循环之前一次性按其结构上界预留）。"""
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise TopicRuntimeError(f"预留数必须为非负整数，得到 {count!r}")
        self.reserved_tool_calls += count

    def release_tool_calls(self, count: int) -> None:
        """打开某 aspect 的初次必读窗口：把它的预留**还回**可花费池。

        调用点只有一处——某个 aspect 的逐份派发循环**开始之前**。此后本 aspect 的必读调用与
        全场可选补件共享同一个池；本 aspect 没用完的份额就此归还，不会留给别的 aspect 冒用
        （「本栏目还没开始读」与「本栏目读完了」是两件事，混起来会让尾部栏目再次被挤掉）。
        """
        self.reserved_tool_calls = max(0, self.reserved_tool_calls - int(count))

    def can_afford_optional(self, *, tool_calls: int = 0, llm_calls: int = 0,
                            tokens: int = 0, elapsed_ms: int = 0) -> bool:
        """**可选**调用（兜底重切 / focused follow-up）的预算门。

        与 :meth:`can_afford` 的唯一差别：把 `reserved_tool_calls` 一并算进去。于是「预算够不够
        这一次补件」不再只看已花掉的数，还要看**还欠多少次初次必读**——这正是本批修掉的那条：
        可选的补件把必读的份额吃光，尾部栏目连一次调用都发不出去。LLM/ token / elapsed 三个
        维度与 :meth:`can_afford` 同口径（预留只针对工具轴，因为被挤掉的正是树检索）。
        """
        if not self.can_afford(tool_calls=tool_calls, llm_calls=llm_calls,
                               tokens=tokens, elapsed_ms=elapsed_ms):
            return False
        return (self.tool_calls + int(tool_calls) + self.reserved_tool_calls
                <= self.policy.max_tool_calls_per_topic)

    def _accumulate(self, src: Any) -> None:
        self.rounds += int(getattr(src, "rounds", 0) or 0)
        self.tool_calls += int(getattr(src, "tool_calls", 0) or 0)
        self.local_searches += int(getattr(src, "local_searches", 0) or 0)
        self.external_searches += int(getattr(src, "external_searches", 0) or 0)
        self.fetches += int(getattr(src, "fetches", 0) or 0)
        self.snapshots += int(getattr(src, "snapshots", 0) or 0)
        self.llm_calls += int(getattr(src, "llm_calls", 0) or 0)
        self.input_tokens += int(getattr(src, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(src, "output_tokens", 0) or 0)
        self.elapsed_ms += int(getattr(src, "elapsed_ms", 0) or 0)

    def charge_need(self, state: Any) -> None:
        """把一次 `run_question` 的 `UsageLedger` 计入累计用量。"""
        ledger = getattr(state, "usage", None)
        if ledger is None:
            return
        self._accumulate(ledger)

    def absorb(self, other: "TopicBudgetState") -> None:
        """把另一个 run 账本（如 follow-up 逐条子账本）的累计用量并入本账本。

        只并入**内容用量**（`to_usage_snapshot` 覆盖的那 10 个计数器）；`wall_clock_ms` 是运行
        用量、不影响 FollowUpDecision 的预算判定，故按 `can_afford` 的口径另行取 max。
        """
        self._accumulate(other)
        self.wall_clock_ms = max(self.wall_clock_ms, other.wall_clock_ms)
        if other.stop_reason is not None:
            self.note_stop(other.stop_reason)

    def can_afford(self, *, tool_calls: int = 0, llm_calls: int = 0,
                   tokens: int = 0, elapsed_ms: int = 0) -> bool:
        """topic 级预算门。耗时维度取内容用量与实测墙钟的**较大者**（fail-closed 上界）。

        两者都是「已花时间」的下界（墙钟含不上报 ledger 的树工具耗时，内容用量含已上报耗时），
        取 max 才不会低估实际消耗；`elapsed_ms` 参数表示本次调用的预估增量。
        """
        p = self.policy
        spent_ms = max(self.elapsed_ms, self.wall_clock_ms)
        return (self.tool_calls + tool_calls <= p.max_tool_calls_per_topic
                and self.llm_calls + llm_calls <= p.max_llm_calls_per_topic
                and self.input_tokens + self.output_tokens + tokens <= p.max_tokens_per_topic
                and spent_ms + elapsed_ms <= p.max_elapsed_ms_per_topic)

    def note_stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason

    def to_usage_snapshot(self) -> TS.TopicUsageSnapshot:
        entries = (
            TS.UsageEntry("rounds", self.rounds, "count"),
            TS.UsageEntry("tool_calls", self.tool_calls, "count"),
            TS.UsageEntry("local_searches", self.local_searches, "count"),
            TS.UsageEntry("external_searches", self.external_searches, "count"),
            TS.UsageEntry("fetches", self.fetches, "count"),
            TS.UsageEntry("snapshots", self.snapshots, "count"),
            TS.UsageEntry("llm_calls", self.llm_calls, "count"),
            TS.UsageEntry("input_tokens", self.input_tokens, "tokens"),
            TS.UsageEntry("output_tokens", self.output_tokens, "tokens"),
            TS.UsageEntry("elapsed_ms", self.elapsed_ms, "ms"),
        )
        return TS.TopicUsageSnapshot(budget_policy=self.policy.to_snapshot(),
                                     cumulative_usage=entries,
                                     stop_reason=self.stop_reason)


# ---------------------------------------------------------------------------
# 3. 运行上下文与依赖
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DocumentIdentity:
    """本次运行绑定的 current 文档身份（由 DemoScope/projection 冻结）。"""

    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str

    def __post_init__(self) -> None:
        for name in ("company_id", "document_id", "document_version",
                     "evidence_set_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise TopicRuntimeError(f"DocumentIdentity.{name} 必须为非空字符串")

    def to_dict(self) -> dict:
        return {"company_id": self.company_id, "document_id": self.document_id,
                "document_version": self.document_version,
                "evidence_set_version": self.evidence_set_version}


@dataclass(frozen=True)
class TopicRunContext:
    """一次 topic 研究的运行上下文（冻结身份 + 选定预算政策）。

    `run_id` 只标识 trace/checkpoint 所属的**运行尝试**：它不进入 Requirement/Pack/
    material 的内容寻址 ID 或 current key（`started_at` 同理）。
    """

    run_id: str
    case_id: str
    company_id: str
    section_id: str
    task_id: str
    report_as_of: str | None
    demo_scope_fingerprint: str
    projection_version: str
    document: DocumentIdentity
    #: Pack v7（§L4.2）：本次运行纳入的**有序来源集**。它是「注册了三份 ≠ 读了三份」这条判据
    #: 的落点——逐来源责任与逐来源四臂结果都以它的成员为域。`document` 保留为**当前锚的兼容
    #: 读视图**（旧单文档调用点仍可用），但它**不是**来源集的替代品：来源集必须显式给出，
    #: 且其当前锚成员必须与 `document` 指同一份文档（见 `__post_init__`）。
    sources: "SM.DocumentSourceSet"
    budget_policy: ResearchBudgetPolicy
    started_at: str

    def __post_init__(self) -> None:
        for name in ("run_id", "case_id", "company_id", "section_id", "task_id",
                     "demo_scope_fingerprint", "projection_version", "started_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise TopicRuntimeError(f"TopicRunContext.{name} 必须为非空字符串")
        # run_id 会成为既有 ToolRegistry 的 audit 目录名；task_id/section_id 会进入
        # call_id/need_id。两者都必须是路径安全的字节。
        _require_safe_id(self.run_id, "TopicRunContext.run_id")
        _require_safe_id(self.task_id, "TopicRunContext.task_id")
        _require_safe_id(self.section_id, "TopicRunContext.section_id")
        if not isinstance(self.document, DocumentIdentity):
            raise TopicRuntimeError("TopicRunContext.document 必须为 DocumentIdentity")
        if not isinstance(self.sources, TS.DocumentSourceSet):
            raise TopicRuntimeError("TopicRunContext.sources 必须为 DocumentSourceSet")
        if not isinstance(self.budget_policy, ResearchBudgetPolicy):
            raise TopicRuntimeError("TopicRunContext.budget_policy 必须为 ResearchBudgetPolicy")
        if self.document.company_id != self.company_id:
            raise TopicRuntimeError(
                f"document.company_id={self.document.company_id!r} 与 "
                f"run_context.company_id={self.company_id!r} 不一致（fail-closed）")
        # 来源集与「当前锚兼容视图」必须是同一份：不是同一份就说明调用方拿了 A 文档的
        # `document` 配 B 套来源集——那种组合下逐来源责任会算在错的域上，必须最先拦住。
        for key in self.sources.keys():
            if key.company_id != self.company_id:
                raise TopicRuntimeError(
                    f"source_set 成员 {key.document_id!r} 的主体 {key.company_id!r} 与 "
                    f"run_context.company_id={self.company_id!r} 不一致（fail-closed）")
        anchors = [k for k, role in self.sources.members if role == "current_state_source"]
        if len(anchors) == 1:
            anchor = anchors[0]
            if (anchor.document_id != self.document.document_id
                    or anchor.document_version != self.document.document_version
                    or anchor.evidence_set_version != self.document.evidence_set_version):
                raise TopicRuntimeError(
                    f"source_set 的 current_state_source={anchor.document_id!r} 与 "
                    f"run_context.document={self.document.document_id!r} 不是同一份（fail-closed）")

    def source_set_fingerprint(self) -> str:
        """来源集内容身份（进入 `content_identity`：换文档集就必须换运行身份）。"""
        return self.sources.fingerprint()

    def content_identity(self) -> dict:
        """进入内容寻址的上下文身份（**不含** run_id / started_at）。"""
        return {
            "task_id": self.task_id, "company_id": self.company_id,
            "section_id": self.section_id, "report_as_of": self.report_as_of,
            "demo_scope_fingerprint": self.demo_scope_fingerprint,
            "projection_version": self.projection_version,
            "document": self.document.to_dict(),
            # 有序来源集整体进身份：成员、**序位**与角色任一变化都必须换运行身份——
            # 否则「同一身份、两套来源集」会让逐来源台账与产物对不上。
            "source_set": self.sources.to_dict(),
            "source_set_fingerprint": self.source_set_fingerprint(),
            "budget_policy": {"policy_id": self.budget_policy.policy_id,
                              "version": self.budget_policy.version,
                              "canonical_hash": self.budget_policy.canonical_hash()},
            "runtime_version": TOPIC_RUNTIME_VERSION,
        }


class TraceSink(Protocol):
    """检索/研究轨迹汇聚点（只接收事件，不参与内容身份）。"""

    def emit(self, event_type: str, payload: dict) -> None:
        ...


class RunScopedTopicStore(Protocol):
    """run-scoped Store 适配器：只薄封装既有 topic_store 函数，不发明第二 Store。"""

    @property
    def db_path(self) -> Any:
        ...

    def commit_pack(self, pack: TS.TopicResearchPack,
                    requirement: TS.TopicResearchRequirement,
                    resolver: TS.PayloadResolver,
                    source_policy_resolver: TS.SourcePolicyResolver,
                    set_completeness_verifier: TS.SetCompletenessVerifier,
                    set_enumeration_verifier: TS.SetEnumerationVerifier) -> Any:
        ...

    def load_current(self, identity: TS.PackIdentity) -> TS.TopicResearchPack | None:
        ...

    # --- 边界③（follow-up）：追加式读写，既不覆盖 need 行，也不覆盖 run 行 ---------------
    def commit_follow_up_run(self, follow_up_run: TS.FollowUpRun,
                             needs: tuple[TS.FollowUpNeed, ...],
                             decisions: tuple[TS.FollowUpDecision, ...]) -> Any:
        ...

    def load_follow_up_needs(self, run_id: str) -> tuple[TS.FollowUpNeed, ...]:
        ...

    def load_follow_up_run(self, follow_up_run_id: str) -> TS.FollowUpRun | None:
        ...


class InformationNeedBuilder(Protocol):
    """把冻结 aspect 构造成 focused `InformationNeed`（不含任何材料）。"""

    def build(self, aspect: TS.TopicAspectRequirementSnapshot, *,
              need_id: str, company_id: str, section_id: str,
              report_as_of: str | None) -> RS.InformationNeed:
        ...


@dataclass(frozen=True)
class TopicRuntimeDependencies:
    """§5.8 要求的依赖协议集合（全部显式注入；测试可整体替换）。"""

    registry: Any
    llm: Any
    navigation: TreeNavigationProvider
    information_need_builder: InformationNeedBuilder
    route_context_builder: Callable[[], RS.RouteContext]
    route_fn: Callable[..., RS.RouterResult]
    budget_state: TopicBudgetState
    trace_sink: TraceSink
    store: RunScopedTopicStore
    payload_resolvers: tuple[TS.PayloadResolver, ...]
    source_policy_resolver: TS.SourcePolicyResolver
    set_completeness_verifier: TS.SetCompletenessVerifier
    set_enumeration_verifier: TS.SetEnumerationVerifier
    clock: Callable[[], str]
    #: 可选：focused follow-up 的 harness 入口（缺省 `harness.runtime.run_question`）。
    research_question: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if not self.payload_resolvers:
            raise TopicRuntimeError(
                "TopicRuntimeDependencies.payload_resolvers 不得为空"
                "（material payload 必须可独立复核，fail-closed）")
        if not isinstance(self.budget_state, TopicBudgetState):
            raise TopicRuntimeError("budget_state 必须为 TopicBudgetState")


# ---------------------------------------------------------------------------
# 4. payload resolver 组合（0 命中 = dangling，2 命中 = 歧义，都 fail-closed）
# ---------------------------------------------------------------------------

class CombinedPayloadResolver:
    """按顺序询问多个 resolver 的组合解析器。

    - 0 个命中 → `None`（dangling，由 `verify_material_payload_ref` fail-closed）；
    - ≥2 个命中 → 抛错（同一 payload 被两套来源同时认领 ⇒ 身份边界不可信）；
    - 单一命中 → 返回该结果。

    **它不是第二个 Store**：不落盘、不发起写入，只做只读分派。
    """

    __slots__ = ("_resolvers",)

    def __init__(self, resolvers: tuple[TS.PayloadResolver, ...]) -> None:
        if not resolvers:
            raise TopicRuntimeError("CombinedPayloadResolver 至少需要一个 resolver")
        self._resolvers = tuple(resolvers)

    def resolve(self, payload_ref: TS.MaterialPayloadRef) -> TS.ResolvedPayload | None:
        hits = []
        for resolver in self._resolvers:
            resolved = resolver.resolve(payload_ref)
            if resolved is not None:
                hits.append(resolved)
        if len(hits) > 1:
            raise TopicRuntimeError(
                f"payload {payload_ref.content_hash[:12]}… 被 {len(hits)} 个来源同时认领"
                f"（身份边界不可信，fail-closed）")
        return hits[0] if hits else None


# ---------------------------------------------------------------------------
# 5. 结果对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicRuntimeResult:
    """只包装已提交 Pack 的身份、用量/检查点、轨迹引用与显式 gaps。

    它**不**再定义一套 Pack 内容模型：Pack 内容只能经 `store.load_current(identity)`
    读回（typed readback）。
    """

    pack_id: str
    identity: TS.PackIdentity
    current: bool
    reused: bool
    aspect_statuses: tuple[tuple[str, str], ...]
    usage: TS.TopicUsageSnapshot
    checkpoint_ref: str | None
    trace_refs: tuple[str, ...]
    gaps: tuple[dict, ...]
    planned_need_ids: tuple[str, ...]
    versions: dict
    #: §三 F：后继装配时被继承的 Pack id（非后继运行时为 None）——本次提交的是它的后继。
    successor_of_pack_id: str | None = None
    #: M930-3A：显式暴露六族 successor 集合（与 Pack 内同一对象，便于消费方不经 Pack 解析
    #: 即可审计；**不**构成第二份 Pack 内容模型）。
    material_dispositions: tuple[TS.ResearchMaterialDisposition, ...] = ()
    fact_candidates: tuple[TS.FactCandidate, ...] = ()
    fact_qualification_decisions: tuple[TS.FactQualificationDecision, ...] = ()
    external_facts: tuple[TS.ExternalFact, ...] = ()
    contract_gaps: tuple[TS.ContractGap, ...] = ()
    research_blocks: tuple[TS.ResearchBlock, ...] = ()

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "identity": dataclasses.asdict(self.identity),
            "current": self.current,
            "reused": self.reused,
            "successor_of_pack_id": self.successor_of_pack_id,
            "aspect_statuses": {a: s for a, s in self.aspect_statuses},
            "usage": self.usage.to_dict(),
            "checkpoint_ref": self.checkpoint_ref,
            "trace_refs": list(self.trace_refs),
            "gaps": [dict(g) for g in self.gaps],
            "planned_need_ids": list(self.planned_need_ids),
            "versions": dict(self.versions),
            "material_disposition_ids": [d.disposition_id for d in self.material_dispositions],
            "fact_candidate_ids": [c.candidate_id for c in self.fact_candidates],
            "fact_qualification_decision_ids": [d.decision_id
                                                for d in self.fact_qualification_decisions],
            "external_fact_ids": [f.external_fact_id for f in self.external_facts],
            "contract_gap_ids": [g.gap_id for g in self.contract_gaps],
            "research_block_ids": [b.block_id for b in self.research_blocks],
        }


# ---------------------------------------------------------------------------
# 6. 内部装配辅助
# ---------------------------------------------------------------------------

def _require_identity(requirement: TS.TopicResearchRequirement,
                      run_context: TopicRunContext) -> None:
    """runtime 只服务与 run_context 完全同一身份/task/期间/投影的 requirement。"""
    checks = {
        "task_id": (requirement.task_id, run_context.task_id),
        "company_id": (requirement.company_id, run_context.company_id),
        "section_id": (requirement.section_id, run_context.section_id),
        "report_as_of": (requirement.report_as_of, run_context.report_as_of),
    }
    for name, (actual, expected) in checks.items():
        if actual != expected:
            raise TopicRuntimeError(
                f"requirement.{name}={actual!r} 与 run_context={expected!r} 不一致"
                f"（fail-closed：runtime 不为别的 task/公司/期间服务）")
    if requirement.company_id != run_context.document.company_id:
        raise TopicRuntimeError(
            "requirement.company_id 与 run_context.document.company_id 不一致")
    # 依赖版本门：本 runtime 只服务「用它自己这一代的依赖集构造」的 requirement。
    # 参考值来自唯一公共 factory（绝不手写 15 键），因此 runtime 版本 / 预算政策版本 /
    # 导航 profile / material_resolver 结构版本任一被换掉，旧 requirement 都会被拒绝 ——
    # 过期依赖集不得成为 current。
    expected = TS.build_current_dependency_versions(
        contract_version=requirement.contract_version,
        source_policy_version=requirement.source_policy_version)
    mismatched = {k: (requirement.dependency_versions.get(k), v)
                  for k, v in expected.items()
                  if requirement.dependency_versions.get(k) != v}
    if mismatched:
        raise TopicRuntimeError(
            f"requirement.dependency_versions 与本 runtime 的当前依赖集不一致"
            f"（过期/错配依赖集不得成为 current，fail-closed）：{mismatched}")


def _dispatch_source_keys(
        aspect_id: str, responsibilities: tuple[AspectSourceResponsibility, ...],
        *, anchor: Any, multi_source_navigation: bool) -> tuple[Any, ...]:
    """本 aspect 本轮**实际派发**的逐份来源（有序，取自互责任记录本身的序位）。

    派发域 = {当前锚} ∪ {责任判定为必须检索的成员}：

    - 锚**无条件**派发。这是 §L1.5 规则 6 的回归保护——单文档退化时工具请求逐字等价；
      同时锚是本 run 绑定的当前状态文档，既有研究链整条建在它的材料上。
    - 其余成员按 `retrieval_required` 派发（§L1.5 规则 2b：来源类 ∈ required_search_scope）。
    - 导航提供方**只有单文档能力**时（`multi_source_navigation=False`）一律只派发锚；被责任
      判定必须检索却没派发的成员，在四臂台账里如实落成臂 C2 `not_dispatched`，
      **不会**凭空消失，也**不会**被改写成「无需检索」（臂 C1）。
    """
    if anchor is None:
        raise TopicRuntimeError(
            "来源集没有 current_state_source 锚：本 run 绑定不到当前状态文档，"
            "fail-closed（不得退化成「谁都不派发」）")
    rows = [r for r in responsibilities if r.aspect_id == aspect_id]
    keys = []
    for row in rows:
        key = row.source_document_key
        if key == anchor or (multi_source_navigation and row.retrieval_required):
            keys.append(key)
    return tuple(keys)


#: ── 初次必读（mandatory first read）的结构量与账本口径 ──────────────────────────
#:
#: 本批（M930-3 业务取材纵链）要修掉的那条是**顺序**问题，不是额度问题：`retrieval_required`
#: 的 `(栏目, 来源)` 的第一次读取是「该查」的**最基本义务**，而可选补件（有界 Evidence 兜底
#: 重切 / focused follow-up）与它**共用同一个 topic 工具账本**，于是补件先把额度吃光，尾部栏目
#: 连第一次读取都发不出去。三个结构量把这条顺序变成可复算、可核对的东西：
#:
#:   1. `mandatory_dispatch_counts()`：逐 aspect 的初次必读派发基数（与 `_dispatch_source_keys`
#:      **同一条式子**，不另写一遍）；
#:   2. `TopicBudgetState.reserved_tool_calls` + `can_afford_optional()`：把「还欠多少次初次必读」
#:      计入可选调用的预算门，于是补件不可能挤掉必读；
#:   3. `focused_need_reserve()`：单次 focused need 的**结构上界**，让「事前预留 1 次、事后按真实
#:      用量计费」的越顶从「设计如此」变成「不该发生」（越顶仍留痕，作为预留算错的证据）。
#:
#: 三者的规则各自版本化：规则一变，由它们导出的上限下沿随之改变。
MANDATORY_FIRST_READ_RULE_VERSION = "mandatory-first-read/1"

#: 单次 focused need 的事前预留规则版本（`fnr-1`：预留量 = 内层 `max_tool_calls` 与
#: 「轮数 + force_converge + answer + entailment」两个结构上界，均从既有常量推出）。
FOCUSED_NEED_RESERVE_RULE_VERSION = "fnr-1"

#: 同读集复用（read-set reuse）的规则版本。`rsr-2`：在 `rsr-1`（同一份来源 + 同一个读集只真读
#: 一次，后来者复用同一批**材料对象**，不复制身份、不虚增调用数）之上补一条 —— **复用的读数
#: 是首读的完整读数**：`complete=False` 时首读的**停止原因**随材料一起交回（事件带
#: `stop_reason`，并按同一张对映表补一条 typed gap），因此「没读完」在复用侧同样有因可查，
#: 不会只留一个 `complete=False` 让人当成「读完」。复用仍只发生在**同一份文档 + 同一次运行
#: 的读集**上，因此是确定性的；读集本身仍逐 aspect 由导航决定。
READ_SET_REUSE_RULE_VERSION = "rsr-2"

#: 续读**未完即停**时顶层 gap 原因与停止原因的对映（顶层值必须留在 `RUNTIME_GAP_REASONS`
#: 闭集内；具体停止原因另存自己的键）。首读出口与**复用**出口共用这一张表：两处各写一份
#: 迟早会漂移，那时「预算截断」与「工具失败」就会在复用侧显示成另一种原因。
_CONTINUATION_STOP_GAP_REASONS = {
    "budget_exhausted": "tree_budget_exhausted",
    "fatal_error": "tree_structure_unavailable",
    "tool_retryable_error": "tree_structure_unavailable",
}
_CONTINUATION_STOP_GAP_DEFAULT = "tree_material_bounded_out"


def _continuation_stop_gap_reason(stop_reason: "str | None") -> str:
    """停止原因 → 顶层 gap 原因（闭集内）。查不到即落「有界止步」这一档。"""
    return _CONTINUATION_STOP_GAP_REASONS.get(stop_reason or "",
                                              _CONTINUATION_STOP_GAP_DEFAULT)

#: 「本栏目为什么没拿到可写材料」的**栏目级**原因闭集（人读页与 Pack 读回读同一本账）。
#:
#: 四类彼此不可互推，因此**不得**合并成笼统的 `coverage_gate_not_met`，更不得写成
#: 「语料里没有」：
#:
#:   * `budget_blocked_dispatch`：责任要求检索、导航也给出了候选，但 topic 级预算不足以发出
#:     这一次调用。**这是「本轮没查」，不是「查过没有」**——它必须与下面三类分开读；
#:   * `dispatched_no_candidate`：这一次派发真实发生、导航也给过结论，只是没有可读候选；
#:   * `material_found_but_unsupported`：材料拿到了（含同读集复用），但没有任何合格事实以它
#:     闭环背书本栏目（覆盖门/资格门未过）。**「取得」不等于「支持」**；
#:   * `table_material_unqualified`：Contract 声明了表格槽位，而本轮没有任何**合格**表格类
#:     材料（平铺表内文本不是表格对象，也不构成数字权威）；
#:   * `search_audit_incomplete`：真实发出过调用、也真实未命中，但 not-found 的条件未全部成立
#:     （→ 只能留 blocked，不得写成合格未命中）。
UNMET_COLUMN_REASONS = (
    "budget_blocked_dispatch",
    "dispatched_no_candidate",
    "material_found_but_unsupported",
    "table_material_unqualified",
    "search_audit_incomplete",
)


def mandatory_dispatch_counts(
        aspects: Iterable[Any], responsibilities: tuple[AspectSourceResponsibility, ...],
        *, anchor: Any, multi_source_navigation: bool,
        skip_aspect_ids: frozenset[str] = frozenset()) -> dict[str, int]:
    """逐 aspect 的**初次必读派发基数**（= 这一栏要发几次树调用才算「看过一遍」）。

    这不是估计值，是 `_dispatch_source_keys` 的逐字复算：同一份责任台账、同一个锚、同一个
    导航能力下，「本栏目必读几次」是**检索前就已定死**的结构量。它同时有两个用途：

    - 事后核对「派发了 N 次、真正发出 M 次」的分母；
    - topic 工具上限的**下沿**——上限低于它，运行必然在初次必读阶段被自己的预算截断，
      那不是更严格，而是把一次注定失败的运行伪装成可运行。

    `skip_aspect_ids`：后继装配里本轮**不重研、直接继承**的 aspect。继承不是「又读了一遍」，
    因此它们既不产生派发、也不占预留（把它们算进预留会虚占额度、把可选补件挤得更紧）。
    """
    counts: dict[str, int] = {}
    for aspect in aspects:
        aspect_id = str(getattr(aspect, "aspect_id", "") or "")
        if aspect_id in skip_aspect_ids:
            continue
        counts[aspect_id] = len(_dispatch_source_keys(
            aspect_id, responsibilities, anchor=anchor,
            multi_source_navigation=multi_source_navigation))
    return counts


def mandatory_dispatch_bound(
        aspects: Iterable[Any], responsibilities: tuple[AspectSourceResponsibility, ...],
        *, anchor: Any, multi_source_navigation: bool,
        skip_aspect_ids: frozenset[str] = frozenset()) -> int:
    """初次必读的结构上界（= 逐 aspect 派发基数之和）。见 :func:`mandatory_dispatch_counts`。"""
    return sum(mandatory_dispatch_counts(
        aspects, responsibilities, anchor=anchor,
        multi_source_navigation=multi_source_navigation,
        skip_aspect_ids=skip_aspect_ids).values())


@dataclass(frozen=True)
class FocusedNeedReserve:
    """单次 focused need 的**结构上界**（事前按真实可能消耗预留，事后按真实用量计费）。

    两个数都从既有常量推出，不新定数字：

    - `tool_calls` 取 `BudgetForNeed.build()` 交给内层运行时的 `max_tool_calls`
      （= `max(2, max_need_rounds_per_aspect × 2)`）。内层账本自己不会越过它，因此它是
      这一次 need 真实可能消耗的工具调用上界；
    - `llm_calls` 取「每 need 的 action 轮数 + 收敛时的一次 answer + 一次 entailment」。
      轮数 = `max_rounds` 本身 + `force_converge` 给出的那一轮 = `max_need_rounds_per_aspect + 1`
      （与 `evaluation/run_m930_3_acceptance.py` 的 `RESEARCH_LLM_CALLS_PER_NEED` 同一条推导；
      两侧任一改动都会让镜像复核不等而整轮拒绝，不会静默漂移）。
    """

    tool_calls: int
    llm_calls: int
    rule_version: str

    def to_dict(self) -> dict:
        return {"tool_calls": self.tool_calls, "llm_calls": self.llm_calls,
                "rule_version": self.rule_version}


def focused_need_reserve(policy: "ResearchBudgetPolicy") -> FocusedNeedReserve:
    inner = policy.need_budget().build()
    tool_calls = int(getattr(inner, "max_tool_calls", 0) or 0)
    if tool_calls <= 0:
        raise TopicRuntimeError(
            "内层 ResearchBudget 没有可用的 max_tool_calls：单次 need 的事前预留量不可知，"
            "拒绝以 0 预留（那等于让 follow-up 免费）")
    return FocusedNeedReserve(
        tool_calls=tool_calls,
        llm_calls=int(policy.max_need_rounds_per_aspect) + 3,
        rule_version=FOCUSED_NEED_RESERVE_RULE_VERSION)


def read_set_reuse_key(source_key: Any, read_node_ids: Iterable[Any], *,
                       plan_id: str | None = None,
                       max_spans: int | None = None,
                       max_chars_per_span: int | None = None,
                       cursor_key: str | None = None) -> tuple:
    """同一 topic 内「同一份来源 + 同一份读集 + **同一个消费计划与上限**」的复用键。

    同一读根展开出的读集是**同一个读集**：第二次读它不会得到另一批 span，却会再花一次
    工具调用。复用因此是**确定性**的（键由四轴身份与读集本身构成，不含任何结果字段），
    且复用交出的仍是**同一批材料对象**——一份原文只有一份材料身份，多栏目只是各自记录用途。

    M930-3 读取计划批（`rpo-1`/`tim-2`）**收紧**这个键：读集的**集合**相同不再足以复用。
    消费侧是有界且**对顺序敏感**的（按 `node_ids` 顺序逐 span 读到 `max_spans` 即停），
    因此「同一集合、不同顺序」会交出**另一批** span —— 那时复用就是把另一批材料冒充成本批
    结论。键因此再带三轴：**有序消费计划**（`plan_id` 覆盖次序与逐节点依据）、
    **两个上限**、**续读位**（`cursor_key`：首读为 `None`，续读为位置身份）。任一轴不同即
    不复用 ⇒ 各读各的，材料身份仍由 `material_id` 去重，绝不复制成第二份身份。

    三轴都给缺省时退回改前的「集合身份」语义（单文档退化与既有调用点逐字等价）。
    """
    base = (SM.source_key_axes(source_key),
            tuple(sorted(str(n) for n in read_node_ids)))
    if plan_id is None and max_spans is None and max_chars_per_span is None \
            and cursor_key is None:
        return base
    return base + (plan_id, max_spans, max_chars_per_span, cursor_key)


def _navigate_one_source(navigation: Any, aspect: TS.TopicAspectRequirementSnapshot, *,
                         requirement: TS.TopicResearchRequirement,
                         source_key: Any, anchor: Any) -> tuple[Any, tuple[str, ...]]:
    """对**一份**来源取导航决策与有界读集。

    跨源提供方（`SourceSetTreeNavigation`）逐份精确查表。

    单文档提供方（`IndexedTreeNavigation`）只服务**锚**：它绑定的那一份就是本 run 的当前
    状态文档，因此这里走它与改前**逐字相同**的 `candidates(...)`——§L1.5 规则 6 的单文档
    退化等价性就落在这一步。被要求对**非锚**导航即 fail-closed：那说明派发域被放宽了却没有
    换成跨源提供方，此时用锚的读集顶替另一份是静默错配（正是 §L3.3 明令禁止的回退）。
    """
    candidates_for = getattr(navigation, "candidates_for", None)
    if candidates_for is None:
        if source_key != anchor:
            raise TopicRuntimeError(
                f"导航提供方只支持单文档，却被要求对来源 {source_key.document_id!r} 导航"
                f"（锚是 {anchor.document_id!r}）：fail-closed（不得用另一份的读集顶替）")
        decision = navigation.candidates(aspect, requirement=requirement)
    else:
        decision = candidates_for(
            aspect, requirement=requirement, source_key=source_key.to_dict())
    return decision, navigation.node_ids_for(decision)


def _tree_call(requirement: TS.TopicResearchRequirement, run_context: TopicRunContext,
               *, need_id: str, aspect_id: str, node_ids: tuple[str, ...],
               source_key: "SM.SourceDocumentKey | None" = None,
               span_cursor: dict | None = None, round_index: int = 0) -> Any:
    """树导航 ToolCall。

    `source_key`（§L4.2 第 2 条）：调用**逐份显式**取该份成员的 `SourceDocumentKey.to_dict()`，
    不再把同一个 `run_context.document.to_dict()` 用于所有来源。缺省 `None` 时退回当前锚的
    兼容读视图（单文档退化时请求逐字等价）。

    `span_cursor`（`tim-2`，**可选**）：从读集内的某个 span 位置续读。缺省 `None` 时
    arguments 与改前**逐字相同**——续读是调用方显式选择，工具不会自己循环。`round_index`
    只进 `call_id`（工具审计的行键必须逐轮可分辨；首轮仍与改前逐字相同）。
    """
    from tools import contracts as C
    if source_key is None:
        if len(run_context.sources) > 1:
            raise TopicRuntimeError(
                "多成员来源集下必须显式给出本调用寻址的那一份（不得用锚的身份代发）")
        source_key = run_context.sources.keys()[0]
    # `call_id` 是工具审计的**行键**：多成员来源集下必须逐份可分辨，否则三次真实调用会
    # 在 `logs/tools/<run_id>/` 里撞成同一个文件名。单文档退化时与改前逐字相同。
    _parts = ["tree"]
    if len(run_context.sources) > 1:
        _parts.append(source_key.document_id)
    if round_index:
        _parts.append(f"cont{round_index}")
    call_id = _join_id(need_id, *_parts)
    arguments = {
        **source_key.to_dict(),
        "need_id": need_id,
        "aspect_id": aspect_id,
        "topic_id": requirement.topic_id,
        "node_ids": list(node_ids),
        "max_spans": run_context.budget_policy.tree_max_spans_per_aspect,
        "max_chars_per_span": run_context.budget_policy.tree_max_chars_per_span,
    }
    if span_cursor is not None:
        arguments["span_cursor"] = dict(span_cursor)
    return C.ToolCall(
        call_id=call_id,
        tool_name=_TREE_TOOL_NAME,
        arguments=arguments,
        idempotency_key=call_id,
        need_id=need_id, batch_id=requirement.topic_id)


def _tree_fallback_call(requirement: TS.TopicResearchRequirement,
                        run_context: TopicRunContext, *, need_id: str, aspect_id: str,
                        evidence_ids: tuple[str, ...],
                        source_key: "SM.SourceDocumentKey | None" = None) -> Any:
    """§七 有界 Evidence fallback 的 ToolCall：与树导航**同一个工具**，只换 selector。

    参数里只有 identity + 父 Evidence ID + 有界 limits：没有文件路径、没有 raw snapshot、
    没有 self-report 字段，因此"整块 Evidence 升格"在契约层就无从表达。
    """
    from tools import contracts as C
    if source_key is None:
        if len(run_context.sources) > 1:
            raise TopicRuntimeError(
                "多成员来源集下必须显式给出本调用寻址的那一份（不得用锚的身份代发）")
        source_key = run_context.sources.keys()[0]
    call_id = (_join_id(need_id, "tree-fallback") if len(run_context.sources) == 1
               else _join_id(need_id, "tree-fallback", source_key.document_id))
    return C.ToolCall(
        call_id=call_id,
        tool_name=_TREE_TOOL_NAME,
        arguments={
            **source_key.to_dict(),
            "need_id": need_id,
            "aspect_id": aspect_id,
            "topic_id": requirement.topic_id,
            "evidence_ids": list(evidence_ids),
            "max_spans": run_context.budget_policy.tree_max_spans_per_aspect,
            "max_chars_per_span": run_context.budget_policy.tree_max_chars_per_span,
        },
        idempotency_key=call_id,
        need_id=need_id, batch_id=requirement.topic_id)


def _materials_from_tool_result(result: Any) -> tuple[tuple[TS.ResearchMaterial, ...],
                                                      tuple[dict, ...], tuple[dict, ...]]:
    """把 `inspect_outline_materials` 的 ToolResult 还原为 typed 材料 + gaps。"""
    data = result.data or {}
    materials: list[TS.ResearchMaterial] = []
    seen: set[str] = set()
    for cand in data.get("candidates", ()) or ():
        raw = cand.get("material")
        if not isinstance(raw, dict):
            raise TopicRuntimeError("tree 候选缺 typed material（不接受自报字段）")
        material = TS.ResearchMaterial.from_dict(raw)
        if material.material_id in seen:
            continue
        seen.add(material.material_id)
        materials.append(material)
    return (tuple(materials), tuple(data.get("gaps", ()) or ()),
            tuple(data.get("skipped", ()) or ()))


#: 表材料返回体里、**不属于** `ResearchMaterial` 的导航/审计键（取出后必须剥掉，
#: 否则 `from_dict` 会按未知字段 fail-closed）。
#:
#: `v7` 起表材料来自图侧逐表证明通道（`gtm-1`），回指身份是 `release_id`（`gtr-*`）；
#: `v6` 及以前的 `released_object_id`（`tobj-*` 文本侧重解身份）**仍留在**词表里：历史 run
#: 的返回体与重放夹具按原样读回，不得因为换了新名字就让旧材料整批落 fail-closed。
_TABLE_MATERIAL_WIRE_KEYS = ("release_id", "released_object_id", "host_evidence_id",
                             "node_ids")


def _table_materials_from_tool_result(result: Any) -> tuple[tuple[TS.ResearchMaterial, ...],
                                                           tuple[dict, ...],
                                                           tuple[dict, ...]]:
    """把 `inspect_outline_materials` 的表对象分支还原为 typed 材料 + 对象/拒绝记录。

    返回 ``(表材料, 表对象, 逐条拒绝)``。三条边界：

    1. 材料只从 `table_materials` 取，**绝不**从 `table_objects` 现场拼一份——对象是「求解出
       了什么」，材料是「什么成为 Pack 材料」，两者不是同一个集合（未获资格的对象只有前者）。
    2. wire 上的导航回指键取出前先剥掉：材料身份在工具侧已算好，`node_id` 不得参与，
       也不得因一个未知键让整批材料落 fail-closed。
    3. 拒绝记录逐条透传（两套 typed 原因：放行门 / 材料门），由调用方落入轨迹；它们**不是**
       gap，也不得被读成「语料里没有」。
    """
    data = result.data or {}
    materials: list[TS.ResearchMaterial] = []
    for raw in data.get("table_materials", ()) or ():
        if not isinstance(raw, dict):
            raise TopicRuntimeError("tree 表材料条目不是 object（不接受自报字段）")
        payload = {k: v for k, v in raw.items() if k not in _TABLE_MATERIAL_WIRE_KEYS}
        materials.append(TS.ResearchMaterial.from_dict(payload))
    objects = tuple(dict(o) for o in (data.get("table_objects", ()) or ())
                    if isinstance(o, dict))
    refusals = tuple(dict(r) for r in (data.get("table_refusals", ()) or ())
                     if isinstance(r, dict))
    return tuple(materials), objects, refusals


def _reason_histogram(records: Sequence[dict]) -> dict:
    """typed 原因的直方图（只由记录本身派生，空记录 → 空字典）。"""
    counts: dict = {}
    for record in records or ():
        reason = str(record.get("reason") or "")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _content_dispositions_from_tool_result(result: Any) -> tuple[dict, ...]:
    """工具上报的**内容处置**条目（§二 2.3）。

    这些候选在材料人口内、结构上也能重切，只是内容形态不合格（孤立标题 / 纯版式碎片 /
    读不出选中状态的勾选行）。它们**不是** gap：`not_used` 不是 gap，把它们写进 gap
    集合会让读回的人以为重切失败、并污染 `ResearchGap.reason_code` 的封闭集合。
    但它们必须留在审计链里（原文摘录 + 精确 locator 由工具侧给出），因此单独取出来
    逐条落到 trace，而不是丢弃或并入 gap。
    """
    data = result.data or {}
    raw = data.get("content_dispositions", ()) or ()
    return tuple(dict(item) for item in raw if isinstance(item, dict))


def _unattempted_from_skipped(skipped) -> tuple[str, ...]:
    """工具上报的受限条目 → 真实存在过但**未被尝试**的候选 id（只取具名候选）。"""
    ids: list[str] = []
    for item in skipped or ():
        if not isinstance(item, dict):
            continue
        for key in ("span_id", "evidence_id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                ids.append(value)
                break
    return tuple(ids)


def _read_cursor_from_tool_result(result: Any) -> dict:
    """从一次树读取的返回体取出**续读读数**（`tim-2`）。

    工具的 `over_max_spans` 条目是本调用**真实截断**的唯一凭据：它给出下一位置
    `next_cursor`、还剩多少未读位置 `unread_span_positions`、位置总数
    `total_span_positions`、以及本轮的起始位置 `resumed_at_span_position`。没有截断
    （或本轮是 `evidence_ids` selector）时全部落零/`None` —— 调用方据此判定「读完了」，
    而不是拿 `gap_count == 0` 冒充「已读完整」。

    返回体是**只读**读数：缺键、类型不对一律退回零值，绝不从「没读到」反推一个位置。
    """
    for item in (result.data or {}).get("skipped", ()) or ():
        if not isinstance(item, dict) or item.get("reason") != "over_max_spans":
            continue
        cursor = item.get("next_cursor")
        return {
            "next_cursor": dict(cursor) if isinstance(cursor, dict) else None,
            "unread_span_positions": int(item.get("unread_span_positions") or 0),
            "total_span_positions": int(item.get("total_span_positions") or 0),
            "resumed_at_span_position": int(
                item.get("resumed_at_span_position") or 0),
        }
    return {"next_cursor": None, "unread_span_positions": 0,
            "total_span_positions": 0, "resumed_at_span_position": 0}


def _impact_for(aspect: TS.TopicAspectRequirementSnapshot) -> str:
    """`ResearchGap.impact` 必须取自 `TS.GAP_IMPACTS`；冻结 impact_scope 不在枚举内时取 subject。"""
    for scope in aspect.impact_scope:
        if scope in TS.GAP_IMPACTS:
            return scope
    return "subject"


def _prove_not_applicable(aspect: TS.TopicAspectRequirementSnapshot) -> str | None:
    """Contract 的**确定性** applicability 规则能否证明该 aspect 不适用。

    返回证明依据（规则名）或 ``None``。`None` 的含义是"证明不了"，调用方必须保留
    blocked / partial：无证据或模型判断都不得推断不适用。声明了规则但规则不在
    `PROVABLE_NOT_APPLICABLE_RULES` 里同样是"证明不了"——声明不等于证明。
    """
    policy = getattr(aspect, "applicability_policy", None)
    if policy is None or policy not in PROVABLE_NOT_APPLICABLE_RULES:
        return None
    return policy


# ---------------------------------------------------------------------------
# §九 外部漏斗 / not-found 审计：只由真实 trace/usage 派生
# ---------------------------------------------------------------------------

def _external_attempt_entry(need_id: str, state: Any) -> dict | None:
    """一次 focused need 的真实外部用量 → 漏斗尝试项；**完全没走外部**时返回 ``None``。

    只读该 need 自己上报的 `UsageLedger`（external_searches / fetches / snapshots）与
    `state.external_snapshot_ids`：没有外部活动就什么都不记（不得凭空造外部尝试）。
    """
    ledger = getattr(state, "usage", None)
    counts = {key: int(getattr(ledger, key, 0) or 0)
              for key in ("external_searches", "fetches", "snapshots")}
    snapshot_ids = tuple(getattr(state, "external_snapshot_ids", ()) or ())
    if not snapshot_ids and not any(counts.values()):
        return None
    return {"need_id": need_id, **counts, "external_snapshot_ids": snapshot_ids}


def external_funnel_record(attempts) -> dict | None:
    """由**真实 trace** 派生外部漏斗记录；一次外部尝试都没有 → ``None``。

    ``attempts`` 是本次运行里**实际发生**的外部尝试：每项含 ``need_id`` 与该次
    `run_question` 上报的外部用量（``external_searches`` / ``fetches`` / ``snapshots``）
    与外部快照 id。没有外部尝试时返回 ``None`` 是**真实结论**（不得伪造空审计对象），
    而不是"固定写死 None"——只要真发生过外部调用，这里必然产出记录。
    """
    entries = []
    for item in attempts or ():
        need_id = getattr(item, "need_id", None) if not isinstance(item, dict) else item.get("need_id")
        if not need_id:
            raise TopicRuntimeError("外部漏斗尝试项缺 need_id（不接受匿名外部用量）")
        get = (lambda k, d=None: item.get(k, d)) if isinstance(item, dict) \
            else (lambda k, d=None: getattr(item, k, d))
        snapshot_ids = sorted(str(s) for s in (get("external_snapshot_ids") or ()))
        entries.append({
            "need_id": need_id,
            "external_searches": int(get("external_searches", 0) or 0),
            "fetches": int(get("fetches", 0) or 0),
            "snapshots": int(get("snapshots", 0) or 0),
            "external_snapshot_ids": snapshot_ids,
        })
    if not entries:
        return None
    entries.sort(key=lambda e: e["need_id"])
    return {"schema_version": EXTERNAL_FUNNEL_SCHEMA_VERSION,
            "producer_version": EXTERNAL_FUNNEL_PRODUCER_VERSION,
            "attempts": entries}


def external_funnel_payload_bytes(record: dict) -> bytes:
    """漏斗记录的规范 payload 字节（`verify_external_funnel_payload` 复验用的同一份字节）。"""
    return CANON.canonical_json(record).encode("utf-8")


def external_funnel_snapshot(record: dict | None) -> TS.ExternalFunnelSnapshot | None:
    """记录 → 内容寻址的 `ExternalFunnelSnapshot`；记录为 ``None`` → ``None``。"""
    if record is None:
        return None
    return TS.ExternalFunnelSnapshot(
        schema_version=EXTERNAL_FUNNEL_SCHEMA_VERSION,
        canonical_hash=sha256_canonical(record),
        producer_version=EXTERNAL_FUNNEL_PRODUCER_VERSION)


def _citation_for(material: TS.ResearchMaterial) -> TS.CitationRef:
    """material → 同源 `CitationRef`（来源身份与 authority 同域）。"""
    authority = material.authority_assessment
    if not isinstance(authority, TS.EvidenceAuthorityAssessment):
        raise TopicRuntimeError(
            f"tree 材料的 authority 必须是 EvidenceAuthorityAssessment，得到 "
            f"{type(authority).__name__}")
    page = None
    if isinstance(material.locator, TS.EvidenceLocator):
        page = material.locator.page
    return TS.CitationRef(ref_type="evidence", evidence_id=authority.evidence_id,
                          page_number=page)


def _index_materials_by_evidence(materials) -> dict:
    """``evidence_id -> tuple[材料, ...]``（**绝不做最后写入者胜出**）。

    同一父 Evidence 块可以被切成多个标题 span，因而对应多份材料；把它们全部保留成
    有序元组，由 :func:`_resolve_material_for_citation` 在事实采纳处要求唯一性，
    而不是在这里静默丢掉先出现的那几份。
    """
    index: dict = {}
    for material in materials:
        authority = material.authority_assessment
        if not isinstance(authority, TS.EvidenceAuthorityAssessment):
            continue
        index.setdefault(authority.evidence_id, []).append(material)
    return {key: tuple(values) for key, values in index.items()}


def _resolve_material_for_citation(citation: Any, material_by_evidence: dict) -> tuple[Any, str | None]:
    """citation → **唯一**已准入 material；歧义或无法确定时返回 ``(None, reason)``。

    同一个 Evidence ID 可以对应多份材料（同一父块被切成多个标题 span）。此时只有 citation
    自带的精确 locator（页号）能证明"就是这一份"时才允许采用；证明不了就拒绝，**绝不**
    任选第一条 / 最后一条 / 最长一条 / 最高分一条，也绝不把整块 Evidence 升格为边界。
    """
    evidence_id = getattr(citation, "evidence_id", None)
    candidates = tuple(material_by_evidence.get(evidence_id, ()))
    if not candidates:
        return None, "citation_not_backed_by_aspect_material"
    if len(candidates) == 1:
        return candidates[0], None
    page = getattr(citation, "page_number", None)
    if isinstance(page, int) and not isinstance(page, bool):
        narrowed = tuple(
            m for m in candidates
            if isinstance(m.locator, TS.EvidenceLocator) and m.locator.page == page)
        if len(narrowed) == 1:
            return narrowed[0], None
    return None, "citation_ambiguous_across_spans"


def _material_span_text(material: Any, resolver: Any, memo: dict) -> str:
    """一份材料的**精确定位原文**（payload 信封 ``content.text`` = 该 OutlineSpan 片段）。

    期间只能从这里读。解析不出来 → ``""``（fail-closed 到"本轮读不到显式期间"，
    由 Pack 侧期间门如实排除并留缺口），**绝不**退回文件名 / 报告生成日 / 快照期末 /
    相邻标题等旁路字段顶替。
    """
    material_id = str(getattr(material, "material_id", "") or "")
    if material_id in memo:
        return memo[material_id]
    text = ""
    payload_ref = getattr(material, "payload_ref", None)
    if payload_ref is not None and resolver is not None:
        resolved = resolver.resolve(payload_ref)
        if resolved is not None:
            text = resolved_payload_text(resolved)
    memo[material_id] = text
    return text


def _cited_explicit_period(resolved_materials, resolver: Any, memo: dict,
                           *, statement: str | None = None,
                           ) -> tuple[str | None, str | None]:
    """被引材料的原文 → 该事实**唯一**的显式期间；返回 ``(period, failure_reason)``。

    - 任一原文出现多个互不包含的期间 → ``period_ambiguous_in_text``（不选、不猜）；
    - 各材料给出的期间互不相同 → 同样歧义（不得按材料顺序任取一条）；
    - 一份都没给出 → ``(None, None)``：**不是**拒绝，而是"本轮未取得显式期间"，
      照旧由 Pack 侧期间门排除并形成期间缺口/待裁决补件（唯一排除点）。

    **期间必须落在「值自己那句话」里**（`period-extract-2`，2026-10-04）：给了 ``statement``
    时必须先用它把期间抽取限定到该命题所在的那一句再抽——一段长 span 里前句写「报告期内」、
    后句另写「2025 年」时，**后文的年份不得追认前句**。定位不到该句（命题不是逐字/重复出现）
    就按「本轮未取得显式期间」处理，**不**退回整段扫描。``statement`` 为 ``None`` 时才保留
    整段口径（仅供既有测试与诊断对照；生产调用点一律传命题文本）。
    """
    displays: dict[str, str] = {}
    for material in resolved_materials:
        span_text = _material_span_text(material, resolver, memo)
        if statement is None:
            extraction = PE.extract_explicit_period(span_text)
        else:
            extraction = PE.extract_explicit_period_in_sentence(span_text, anchor=statement)
        if extraction.status == PE.PERIOD_AMBIGUOUS:
            return None, "period_ambiguous_in_text"
        if extraction.status == PE.PERIOD_EXTRACTED:
            displays.setdefault(extraction.display, str(getattr(material, "material_id", "")))
    if len(displays) > 1:
        return None, "period_ambiguous_in_text"
    if not displays:
        return None, None
    return next(iter(displays)), None


def _qualify_and_adopt(
        aspect: TS.TopicAspectRequirementSnapshot, *,
        statement: str, fact_type: str, period: str | None,
        period_failure: str | None, materials: Sequence[Any],
        refs: Sequence[TS.CitationRef], source_key: str,
        force_reject_reason: str | None = None,
        value_identity: TS.ValueIdentity | None = None,
        ) -> tuple[TS.FactCandidate, TS.FactQualificationDecision, TS.SupportedFact | None]:
    """候选 → 资格决定 →（可选）qualified result。**唯一**一处实现这三个 digest 与判据。

    两条事实来源（模型 claim 与确定性数值披露）都必须走这里：三个 digest 必须能由决定自己
    声明的 ``input_material_ids`` **确定性复算**（``sections/pack_set.py::_check_decision_inputs``
    会真的核对），抄一份出来迟早分叉。

    ``value_identity``（可空）是**确定性数值披露那条路**自己的逐值身份：它随 qualified result
    一起进 Pack（`SupportedFact.value_identity`），让写作侧不必从 `statement` 那段**逐字前缀**
    反推「这条事实授权的是哪个值」。候选与决定**不带**它（`FactCandidate` 的键集不变）。

    返回 ``(candidate, decision, fact_or_None)``；只有 ``eligible`` 决定给出 fact。
    """
    material_ids = tuple(sorted({str(m.material_id) for m in materials}))
    #: 三个 digest 必须**只**由决定自己声明的输入（排序、去重）复算得出——citation 解析顺序
    #: 不在 wire 里，同一份材料被引两次时还会多算一份。真实 run 实证：一条 claim 引两份材料且
    #: citation 顺序与 sorted 相反时，Pack 门以 `decision_input_identity_mismatch` 三条挡住了
    #: 整个 topic 的 Pack set（`sections/pack_set.py::_check_decision_inputs`）。
    _by_id = {str(m.material_id): m for m in materials}
    canonical_inputs = [_by_id[mid] for mid in material_ids]
    candidate = TS.build_fact_candidate(
        candidate_source_kind="topic_material", statement=statement, fact_type=fact_type,
        aspect_ids=(aspect.aspect_id,), question_ids=(aspect.question_id,),
        material_ids=material_ids, period=period)
    source_authority = materials[0].authority_assessment
    source_identity = TS.authority_source_identity(source_authority)
    identity_closed = all(TS.citation_source_identity(c) == source_identity for c in refs)
    authority_verdict = TS.recompute_authority_verdict(source_authority)
    authority_ok = (authority_verdict == "authoritative"
                    and source_authority.verdict == authority_verdict)
    qualified = bool(identity_closed and authority_ok and period_failure is None
                     and force_reject_reason is None)
    if qualified:
        rejection_reason = None
    elif force_reject_reason is not None:
        rejection_reason = force_reject_reason
    elif not identity_closed:
        rejection_reason = "citation_source_identity_mismatch"
    elif not authority_ok:
        rejection_reason = f"authority_verdict={authority_verdict}"
    else:
        #: 权威成立、只是期间读不出唯一值时，该候选仍是**一条** typed rejection audit：
        #: 候选留在审计里（含被拒决定），零条 qualified result；不制造 gap（那是 Contract
        #: 必需事实仍未取得时的**另外**一件事）。
        rejection_reason = period_failure
    locator_digest = sha256_canonical(
        {"locators": [m.locator.to_dict() for m in canonical_inputs]})
    payload_digest = sha256_canonical(
        {"payloads": [m.payload_ref.to_dict() for m in canonical_inputs]})
    identity_digest = sha256_canonical({
        "candidate_id": candidate.candidate_id,
        "candidate_revision": candidate.candidate_revision,
        "material_ids": list(material_ids),
        "material_content_hashes": sorted({str(m.content_hash) for m in canonical_inputs}),
        "source_identity": source_identity,
        "locator_digest": locator_digest,
        "payload_digest": payload_digest,
    })
    decision = TS.build_qualification_decision(
        candidate, verdict=("eligible" if qualified else "rejected"),
        input_identity_digest=identity_digest, input_source_identity=source_identity,
        input_locator_digest=locator_digest, input_payload_digest=payload_digest,
        rejection_reason=rejection_reason)
    if not qualified:
        return candidate, decision, None
    fact_id = "fact-tr-" + sha256_canonical({
        "aspect_id": aspect.aspect_id, "source_key": source_key, "text": statement,
        "sources": sorted({TS.citation_source_identity(c) for c in refs}),
    })[:32]
    fact = TS.build_supported_fact(
        fact_id, candidate, decision, text=statement, fact_type=fact_type,
        citation_refs=tuple(refs), source_authority=source_authority,
        value_identity=value_identity)
    return candidate, decision, fact


def _adopt_facts(aspect: TS.TopicAspectRequirementSnapshot, outcome: Any,
                 material_by_evidence: dict, *, resolver: Any,
                 ) -> tuple[tuple[TS.FactCandidate, ...],
                            tuple[TS.FactQualificationDecision, ...],
                            tuple[TS.SupportedFact, ...],
                            tuple[str, ...]]:
    """把一次 `run_question` 结果转换为 **候选 → 资格决定 → qualified result**（单向链）。

    返回值：``(candidates, decisions, supported_facts, rejected_claim_diagnostics)``。

    分层（不得合并成一个状态）：

    1. **候选筛选**：claim kind == "fact"、entailment verdict == SUPPORTED、每个 evidence
       引用能在同 aspect 材料里唯一确定一份 `OutlineSpan` 材料。任一不满足 → 该 claim
       **不构成候选**，只留 `rejected` 诊断（不冒充资格决定）；
    2. **候选构造**：``FactCandidate``（`candidate_source_kind="topic_material"`）；
    3. **期间核验**：从**被引材料自己的精确定位原文**抽取该事实唯一显式期间，放进候选
       （`FactCandidate.period`，再被唯一构造器逐字复制进 `SupportedFact`）；读到互不相同
       的多个期间 → 拒绝（`period_ambiguous_in_text`）。抽不到 ≠ 拒绝，而是"本轮未取得显式
       期间"，由 Pack 侧期间门排除并留缺口。**不得**用报告生成日 / 文件名 / 相邻标题顶替；
    4. **资格门**：对**每条候选**产出**恰好一条**版本化 ``FactQualificationDecision``：
       来源权威必须确定性重算为 ``authoritative``、且每条 citation 的来源身份与事实权威身份
       一致。不满足 → ``verdict="rejected"``，该决定本身即唯一 typed rejection audit；
    5. **qualified result**：仅 ``eligible`` 决定产生恰一条 ``SupportedFact``，单向回指其
       candidate id/revision 与决定 id。

    rejection **不**自动制造 gap：Contract-required aspect/事实仍未取得时由调用方**另外**
    形成 ``ContractGap``/``ResearchBlock``（见 `run_topic_requirement` 的 §7.5）。
    """
    answer = getattr(outcome, "answer", None)
    if answer is None:
        return (), (), (), ("no_answer",)
    verdicts = {v.claim_id: v for v in (getattr(outcome.state, "entailment_verdicts", ()) or ())}
    citations = list(getattr(answer, "citations", ()) or ())
    candidates: list[TS.FactCandidate] = []
    decisions: list[TS.FactQualificationDecision] = []
    adopted: list[TS.SupportedFact] = []
    rejected: list[str] = []
    # 同一份材料的多条 claim 共用一次 payload 原文读取（只读、确定性；不改写已验字节）。
    period_text_memo: dict[str, str] = {}
    for claim in (getattr(answer, "claims", ()) or ()):
        kind = getattr(claim, "kind", "")
        if kind != "fact":
            rejected.append(f"{getattr(claim, 'claim_id', '?')}:kind={kind}")
            continue
        verdict = verdicts.get(claim.claim_id)
        if verdict is None or getattr(verdict, "verdict", "") != "SUPPORTED":
            rejected.append(f"{claim.claim_id}:entailment={getattr(verdict, 'verdict', None)}")
            continue
        refs = []
        resolved_materials = []
        failure: str | None = None
        for idx in (getattr(claim, "citation_refs", ()) or ()):
            if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= len(citations):
                failure = "citation_not_backed_by_aspect_material"
                break
            citation = citations[idx]
            if getattr(citation, "ref_type", "") != "evidence":
                failure = "citation_not_backed_by_aspect_material"
                break
            material, reason = _resolve_material_for_citation(citation, material_by_evidence)
            if material is None:
                failure = reason
                break
            resolved_materials.append(material)
            refs.append(_citation_for(material))
        if failure is not None or not refs:
            rejected.append(f"{claim.claim_id}:{failure or 'citation_not_backed_by_aspect_material'}")
            continue
        # --- 候选 → 资格决定 → qualified result（与确定性数值披露共用同一个 helper）---
        # 期间**只能**来自被引材料自己的精确定位原文（见 `_cited_explicit_period`）：抽到就是
        # 该事实的期间，抽不到就是没有（后续由 Pack 侧期间门 + 缺口/补件如实承接），歧义则拒绝。
        # `period-extract-2`：期间还必须落在**值自己那句话**里——命题文本只用于**定位**那一句，
        # 它**不**提供期间；后文句子里的年份不得追认前句。
        period, period_failure = _cited_explicit_period(
            resolved_materials, resolver, period_text_memo, statement=claim.text)
        candidate, decision, fact = _qualify_and_adopt(
            aspect, statement=claim.text, fact_type=kind, period=period,
            period_failure=period_failure, materials=resolved_materials,
            refs=tuple(refs), source_key=claim.claim_id)
        candidates.append(candidate)
        decisions.append(decision)
        if fact is None:
            rejected.append(f"{claim.claim_id}:qualification_rejected:"
                            f"{decision.rejection_reason}")
            continue
        adopted.append(fact)
    return tuple(candidates), tuple(decisions), tuple(adopted), tuple(rejected)


#: `_evaluate_coverage_rules` 可确定性表达的规则集合（其余一律拒绝）。
_EVALUABLE_COVERAGE_RULES = frozenset({
    "set_complete", "required_fields_complete", "minimum_sources",
    "direct_support", "search_audit", "applicability",
})


def derive_column_unmet_reasons(
        *, materials: Sequence[Any], facts: Sequence[Any],
        dispatched_without_call: dict[str, str], search_trace_count: int,
        requires_table_output: bool, table_material_count: int,
        not_found_branch: bool = False, not_found_qualified: bool | None = None,
        optional_budget_blocked: int = 0
) -> tuple[tuple[str, ...], dict[str, int]]:
    """本栏目未达 covered 的**typed** 原因（闭集 `UNMET_COLUMN_REASONS` 的子集）+ 派发计数。

    返回 `(reasons, dispatch_counts)`；两者都只由**真实轨迹**派生，不由「没有材料」倒推。

    为什么是**一串**而不是一个码：四类原因彼此不可互推，一个栏目可以同时成立多条
    （例如「A 份被预算挡下、B 份读过但没产出材料、本栏目还要表而表未获资格」）。塌成一个
    码就是本批要修掉的那种读法——把「预算没让检索发生」读成「语料里没有」。

    `dispatch_counts` 把这一次派发的真实去路分开计数（键是 `UNFULFILLED_REASONS` 的子集 +
    `call_fired` / `optional_blocked`）：`budget_blocked` 与 `no_candidate` 都**不是**「未命中」，
    前者是「本轮没查」、后者是「派发过、导航给不出候选」。

    `optional_budget_blocked`：本栏目**初次必读已经派发**、但可选补件（有界重切 / focused
    follow-up）被预算挡下的次数。它与 `budget_blocked` 落**同一个**原因码（两者都是「预算
    没让该查的调用发生」，不是「查过没有」），但**分两个键计数**：前者是必读发不出去（结构性
    缺陷），后者是必读发了、补件没了（预算被如实用在必读上）。缺了这条，一个「材料一份没有、
    必读却全部完成」的栏目只会剩下 `material_found_but_unsupported`/空原因串，读回的人便
    无法把这栏目的空白归因到预算，只能归因到语料。
    """
    reasons: list[str] = []
    counts = {
        "budget_blocked": sum(1 for v in dispatched_without_call.values()
                              if v == "budget_exhausted"),
        "no_candidate": sum(1 for v in dispatched_without_call.values()
                            if v == "dispatched_no_candidate"),
        "call_fired": int(search_trace_count),
        "optional_blocked": max(0, int(optional_budget_blocked)),
    }
    if counts["budget_blocked"] or counts["optional_blocked"]:
        # 责任要求检索、导航也给出了候选，只是预算不允许发出这一次调用（必读那一档）；
        # 或必读已如实发完、可选补件被同一本账挡下（`optional_blocked` 那一档）。
        # 两档都不是「查过且没有」，故落同一个原因码，计数分开。
        reasons.append("budget_blocked_dispatch")
    if counts["no_candidate"]:
        reasons.append("dispatched_no_candidate")
    if materials and not facts:
        # 材料拿到了（含同读集复用），却没有一条合格事实以它闭环背书本栏目。
        reasons.append("material_found_but_unsupported")
    if requires_table_output and table_material_count == 0:
        # Contract 声明了表格槽位，而本轮没有任何**合格**表格类材料。平铺表内文本不是表格
        # 对象、也不构成数字权威，因此不能算「有表材料」。
        reasons.append("table_material_unqualified")
    if not_found_branch and not_found_qualified is False:
        # 真实发出过调用、也真实未命中，但 not-found 的条件未全部成立 ⇒ 只能留 blocked。
        reasons.append("search_audit_incomplete")
    return tuple(reasons), counts


def _coverage_gate_reason(aspect: TS.TopicAspectRequirementSnapshot,
                          facts: tuple[TS.SupportedFact, ...],
                          materials: tuple[TS.ResearchMaterial, ...]) -> str | None:
    """runtime 自跑的确定性覆盖硬门（先于 commit，避免把 commit 当校验器）。

    返回 None 表示「可以判 covered」；返回原因码表示只能降级为 partial。本函数逐条镜像
    Store `_validate_aspect_semantics` 的 covered 分支（权威重算 / citation↔authority 闭环 /
    同源 material / coverage_rules / usage-scope / inference lineage / set_complete），
    因此 runtime 声明的 covered 不可能在 `commit_pack` 处才炸。它**不新增**语义：
    判定用的全是 `topic_schema` 的公开确定性函数。

    两条本批**不能**自证的规则按 schema 自己的规定拒绝升格为 covered：

    - `set_complete`：需要受信任、版本化、确定性的枚举器从真实 payload 枚举成员
      （`topic_schema` 明确要求「接线前生产运行链不得将 set_complete aspect 提升为 covered」）；
    - `conditional_transmission` 层：正式结果必须是带完整 `InferenceLineage` 的 inference 事实，
      而血缘只能由冻结规则推导——本批不产出 inference claim，故不得伪造血缘。
    """
    snap = aspect
    rules = set(snap.coverage_rules)
    if rules - _EVALUABLE_COVERAGE_RULES:
        return "coverage_rule_not_evaluable"
    if not materials or not facts:
        return "coverage_gate_not_met"
    for material in materials:
        rv = TS.recompute_authority_verdict(material.authority_assessment)
        if rv != "authoritative" or material.authority_assessment.verdict != rv:
            return "coverage_gate_not_met"
    for fact in facts:
        rv = TS.recompute_authority_verdict(fact.source_authority)
        if rv != "authoritative" or fact.source_authority.verdict != rv:
            return "coverage_gate_not_met"
        aid = TS.authority_source_identity(fact.source_authority)
        if not fact.citation_refs or not any(
                TS.citation_source_identity(c) == aid for c in fact.citation_refs):
            return "coverage_gate_not_met"
        if not any(TS.authority_source_identity(m.authority_assessment) == aid
                   for m in materials):
            return "coverage_gate_not_met"
    if "required_fields_complete" in rules:
        obtained: set[str] = set()
        for f in facts:
            obtained |= set(f.obtained_fields)
        if set(snap.required_fields) - obtained:
            return "coverage_gate_not_met"
    if "minimum_sources" in rules:
        if not {TS.authority_source_identity(f.source_authority) for f in facts}:
            return "coverage_gate_not_met"
    elig = TS.derive_support_eligibility(snap)
    required = set(elig.required_source_classes)
    supplemental = set(elig.supplemental_only_source_classes)
    if required or supplemental:
        has_required = False
        for fact in facts:
            sc = TS.authority_source_class(fact.source_authority)
            if sc in required:
                has_required = True
            elif sc in supplemental:
                continue
            else:
                return "usage_scope_not_satisfied"
        if not has_required:
            return "usage_scope_not_satisfied"
    if "conditional_transmission" in set(snap.transmission_layers):
        return "inference_lineage_required"
    if "set_complete" in rules:
        return "set_completeness_proof_unavailable"
    return None


# ---------------------------------------------------------------------------
# 6.5 Pack successor 装配（§16.5 边界③ 的后继语义）
# ---------------------------------------------------------------------------
#
# 为什么需要它：`FollowUpNeed` 只授权**重研一条 aspect**，但一个 Pack 的 current 身份要求
# `aspect 集合 / question 集合 / 依赖指纹` 与冻结 requirement **精确相等**。若把「只重研一条
# aspect」的中间结果直接提交，同一 identity 的 current 会变成**缺 aspect** 的 Pack：后续 P4
# 读门立刻以 `aspect_set_mismatch` / `question_set_mismatch` block（不是「更省的新版本」，
# 而是读不到完整权威）。因此 follow-up 的产物必须在内存里装配成**完整** aspect 集的后继：
# 其他 aspect **确定性继承**既有 current Pack 的结果，被重研的那条用**本次运行**的结果替换，
# 再整体过一遍 `derive_pack_status`（完整 exact-set 重新派生），最后由**既有** `commit_pack`
# 用**完整** requirement 做一次原子提交（Store 侧语义门 + 集合门全部重跑）。
#
# 继承的判据全部取自各行自身的 aspect 身份（`aspect_ids` / `aspect_id`），不猜、不从顺序推：
# 与重研 aspect 相交的行由本次运行的结果替代，其余逐行原样继承。任何**同键不同内容**、
# 或继承侧引用不闭合（继承来的 aspect 结果引用了被替换掉的 fact/material/gap/audit）都
# fail-closed —— 宁可 typed 拒绝，也不产出「看起来完整但引用悬空」的后继。

#: 后继装配规则版本（进 trace 与 FollowUpDecision 的理由文本，不进 Pack 内容身份）。
PACK_SUCCESSOR_ASSEMBLY_VERSION = "psa-1"

#: `FollowUpNeed` 诉求在 `InformationNeed.metadata` 里的键名（`routing.schema.InformationNeed`
#: 已登记的向后兼容扩展位；Router 规则只读白名单键，不遍历 metadata）。
FOLLOW_UP_FOCUS_METADATA_KEY = "follow_up_focus"

#: 诉求**必须**带进检索的字段（缺任一即拒：只留在裁决记录里的诉求没有进入检索）。
FOLLOW_UP_FOCUS_FIELDS = (
    "need_id", "statement", "topic_id", "aspect_id", "question_id", "section_id",
    "section_draft_revision", "requiredness", "expected_source_class", "budget_hint",
    "grant_reason", "rules_version",
)


def build_follow_up_focus(need: TS.FollowUpNeed, *, grant_reason: str) -> dict:
    """把一条**已获裁决**的 `FollowUpNeed` 折成进检索的诉求（缺字段/空白即拒）。"""
    if not isinstance(need, TS.FollowUpNeed):
        raise TopicRuntimeError("build_follow_up_focus 只接受 FollowUpNeed")
    focus = {
        "need_id": need.need_id,
        "statement": need.statement,
        "topic_id": need.topic_id,
        "aspect_id": need.aspect_id,
        "question_id": need.question_id,
        "section_id": need.section_id,
        "section_draft_revision": need.section_draft_revision,
        "requiredness": need.requiredness,
        "expected_source_class": need.expected_source_class,
        "budget_hint": need.budget_hint,
        "contract_authorized_scope": list(need.contract_authorized_scope),
        "grant_reason": str(grant_reason),
        "rules_version": FOLLOW_UP_RULES_VERSION,
    }
    for field_name in FOLLOW_UP_FOCUS_FIELDS:
        value = focus[field_name]
        if not (isinstance(value, str) and value.strip()):
            raise TopicRuntimeError(
                f"FollowUpNeed 的 {field_name} 为空：诉求不得带着空字段进入检索（fail-closed）")
    if not focus["contract_authorized_scope"]:
        raise TopicRuntimeError("FollowUpNeed 的 contract_authorized_scope 为空：授权范围未进入检索")
    return focus


def _bind_follow_up_focus(need: RS.InformationNeed, focus: dict,
                          *, bound_need_id: str) -> RS.InformationNeed:
    """在**同一个** `InformationNeed` 上绑定 follow-up 诉求（返回替换后的对象）。

    「已裁决」不等于「已进入检索」：若诉求只留在裁决记录与 Pack 里，本轮检索仍按 aspect 泛跑，
    下一轮 Writer 会看到与上一轮无差别的材料。这里绑定到 metadata（类型自带的扩展位），该对象
    随即被交给 `route_fn` 与 `research_question`，因此「模型检索时看到的诉求」可被逐字审计。
    """
    for field_name in FOLLOW_UP_FOCUS_FIELDS:
        if field_name not in focus:
            raise TopicRuntimeError(
                f"follow-up 诉求缺少 {field_name!r}：不得用残缺诉求绑定检索（fail-closed）")
    metadata = dict(need.metadata or {})
    metadata[FOLLOW_UP_FOCUS_METADATA_KEY] = {**focus, "bound_need_id": bound_need_id}
    return dataclasses.replace(need, metadata=metadata)


def build_material_dispositions(*, materials: Sequence[TS.ResearchMaterial],
                                aspect_results: Sequence[TS.AspectResearchResult],
                                candidates: Sequence[TS.FactCandidate],
                                extra_material_aspects: Mapping[str, Sequence[str]]
                                | None = None
                                ) -> tuple[TS.ResearchMaterialDisposition, ...]:
    """每份 material **恰一条** `ResearchMaterialDisposition`（运行装配与后继装配的唯一口径）。

    admission/retention/来源校验结论全部**确定性重算**（authority verdict 由 material 字段
    重算，不信自报），aspect 归属由 aspect 结果的 `material_ids` 反查汇总。P4 读门会用
    `TS.build_material_disposition` + 自报 `aspect_ids` 重算比对，因此这里必须是**同一**构造，
    不得在后继装配里另写一份。

    `extra_material_aspects`：**表对象材料**（v6 表对象通道产出的 `table_context`）的栏目
    归属不来自 `material_ids`（那一列只放**可充当引用索引**的材料，表对象材料与之同宿主块，
    混进去会让同块引用一律歧义，见 `_attribute_table_materials` 处的注释）。两边的取法都是
    **精确**的、不是猜的：运行装配侧直接取本栏目表材料列，后继装配侧取 base/new 自己的
    RMD（只为**未被 `material_ids` 归属过**的材料补，因此对正文材料是零影响、纯增量）。
    """
    material_aspects: dict[str, list[str]] = {}
    for r in aspect_results:
        for mid in r.material_ids:
            material_aspects.setdefault(mid, []).append(r.aspect_id)
    for mid, aspect_ids in (extra_material_aspects or {}).items():
        if mid in material_aspects:
            # 已经被 `material_ids` 归属过的材料不在此处二次归属：这条通道只补表对象材料，
            # 不改变正文材料的 RMD（P4 读门重算比对因此不受影响）。
            continue
        material_aspects[mid] = list(aspect_ids)
    cited_material_ids = {mid for c in candidates for mid in c.material_ids}
    dispositions: list[TS.ResearchMaterialDisposition] = []
    for material in materials:
        rv = TS.recompute_authority_verdict(material.authority_assessment)
        validated = (rv == "authoritative" and material.authority_assessment.verdict == rv)
        aspect_ids = tuple(sorted(set(material_aspects.get(material.material_id, ()))))
        if validated:
            admission_state, retention_state = "admitted", "retained"
            source_validation, reason_code = "validated", "aspect_material_admitted"
            proof = (f"authority 重算 = authoritative；material {material.material_id} "
                     f"被 {len(cited_material_ids & {material.material_id})} 条候选引用")
        else:
            admission_state, retention_state = "rejected", "dropped"
            source_validation, reason_code = "rejected", "authority_rejected"
            proof = (f"authority 重算 = {rv}（自报 {material.authority_assessment.verdict}），"
                     f"material {material.material_id} 不得作为合格材料")
        dispositions.append(TS.build_material_disposition(
            material, aspect_ids=aspect_ids, admission_state=admission_state,
            retention_state=retention_state, source_validation=source_validation,
            reason_code=reason_code, reason_proof=proof,
            policy_version=TS.MATERIAL_DISPOSITION_VERSION))
    return tuple(dispositions)


def requirement_pack_identity(requirement: TS.TopicResearchRequirement) -> TS.PackIdentity:
    """由冻结 requirement 确定性派生的 Pack 身份（**唯一**口径：current 读写两侧共用）。

    `TopicResearchRequirement` 自身不带 `PackIdentity`，但 current 指针的键必须两侧同构；
    在这里派生而不是各调用点各拼一次，避免「读 current 用一套键、提交用另一套」的静默错配。
    """
    if not isinstance(requirement, TS.TopicResearchRequirement):
        raise TopicRuntimeError("requirement_pack_identity 需要 TopicResearchRequirement")
    return TS.PackIdentity(
        task_id=requirement.task_id, company_id=requirement.company_id,
        report_as_of=requirement.report_as_of,
        contract_fingerprint=requirement.contract_fingerprint,
        source_policy_version=requirement.source_policy_version,
        section_id=requirement.section_id, topic_id=requirement.topic_id)


def _merge_rows(base_rows: Sequence[Any], new_rows: Sequence[Any], key: str,
                label: str) -> tuple[Any, ...]:
    """按身份键合并两批行：base 顺序在前、new 独有行按 new 顺序追加。

    同一个键出现在两侧时**必须逐字段相同**（行身份是内容寻址的，同键不同内容即装配错配）；
    宁可 typed fail-closed，也不静默取一侧。
    """
    merged: dict[str, Any] = {}
    for row in base_rows:
        merged[getattr(row, key)] = row
    for row in new_rows:
        row_key = getattr(row, key)
        previous = merged.get(row_key)
        if previous is not None and previous != row:
            raise TopicRuntimeError(
                f"后继装配的 {label} 身份 {row_key!r} 在两套来源里内容不同："
                f"不得静默取一侧（fail-closed）")
        merged[row_key] = row
    return tuple(merged.values())


def _owned_by_research(aspect_ids: Sequence[str], researched: frozenset[str]) -> bool:
    """该行的 aspect 身份是否与本次重研的 aspect 相交（相交即由本次运行替代）。"""
    return bool(researched & set(aspect_ids))


def _merge_source_set(base: TS.DocumentSourceSet,
                      new: TS.DocumentSourceSet) -> TS.DocumentSourceSet:
    """有序并集：`base` 的序位原样保持，`new` 的**新**成员按其自身序位追加。

    不做重排（序位是语义），不做去重猜测（同 `document_id` 但 key 不同的两行一律拒）。
    两锚冲突（base 与 new 各有一个不同的 `current_state_source`）由
    `DocumentSourceSet.__post_init__` fail-closed，不在这里兜底选一个。
    """
    seen = {k.document_id: k for k, _ in base.members}
    merged = list(base.members)
    for key, role in new.members:
        previous = seen.get(key.document_id)
        if previous is not None:
            if previous != key:
                raise TopicRuntimeError(
                    f"后继来源集的 {key.document_id!r} 在两套来源里 key 内容不同："
                    f"不得静默取一侧（fail-closed）")
            continue
        seen[key.document_id] = key
        merged.append((key, role))
    return TS.DocumentSourceSet(members=tuple(merged))


def merge_source_axis(
        *, base: TS.TopicResearchPack, new: TS.TopicResearchPack,
        aspects: tuple[TS.TopicAspectRequirementSnapshot, ...],
        researched_aspect_ids: tuple[str, ...],
) -> tuple[TS.DocumentSourceSet,
           tuple[AspectSourceResponsibility, ...],
           tuple[TS.SourceAspectOutcome, ...]]:
    """把两套来源轴台账合成完整后继口径（§L4.2）。

    责任**在合并后的来源集上整套重派生**，不照抄任何一侧：责任只由 §L1.5 的输入轴决定，
    多一份来源理应多一整列责任边。已存在的逐来源结果按 `(aspect, 来源)` 保留，但**必须**
    与重派生行的指纹逐条相等——不相等说明责任轴在结果成文之后被改过，fail-closed。

    **逐来源结果按「谁真的跑过这条 aspect」归属，与其它行族同一条规矩**：被重研 aspect 的
    结果取自 `new`（那是本轮真实痕迹的产物），其余 aspect 的结果取自 `base`（本轮**故意**
    没有导航它们，`new` 里那些 `not_dispatched` 是「本轮没跑」的记录，不是证据）。若反过来
    两侧对同一 `(aspect, 来源)` 做逐字段比对，则每一次真实的后继都会因为「继承来的 aspect
    在 `new` 里必然是没有材料的 C2」而必然冲突——那是把「本轮没重研」误判成「两套来源打架」。

    继承侧仍要挡「本轮自造」：非重研 aspect 在 `new` 里不得出现臂 A（没导航就没有材料）。

    合并后仍**没有**结果记录的 `(aspect, 来源)` 对，只能是「两侧谁都没查过这一对」：
    按时如实写成臂 C2 `not_dispatched`（责任说必须查）或臂 C1（责任说不必查），
    **不得**拿另一份来源的结果顶上，也不得留白。
    """
    researched = frozenset(researched_aspect_ids)
    if not researched:
        raise TopicRuntimeError(
            "来源轴合并必须显式指出至少一条被重研的 aspect（fail-closed，否则无从归属）")
    sources = _merge_source_set(base.source_set, new.source_set)
    derived: dict[tuple[str, str], AspectSourceResponsibility] = {}
    for aspect in aspects:
        for row in derive_aspect_source_responsibility(
                aspect=aspect, sources=sources,
                current_state=SM.CURRENT_STATE_RESOLVED):
            derived[(row.aspect_id, row.source_document_key.document_id)] = row

    existing: dict[tuple[str, str], TS.SourceAspectOutcome] = {}
    for pack, is_new_side in ((base, False), (new, True)):
        for row in pack.source_aspect_outcomes:
            pair = (row.aspect_id, row.source_document_key.document_id)
            mine = row.aspect_id in researched
            if is_new_side and not mine and row.arm == "A":
                # 本轮**没有**导航这条 aspect，却在本轮结果里给出材料归属——那是自造。
                # 继承侧(base)的臂 A 是**预期**的：那是上一轮真查过的证据，正是要继承的东西，
                # 所以这条只对 `new` 生效。
                raise TopicRuntimeError(
                    f"非重研 aspect {pair[0]!r} 在本轮结果里落臂 A（本轮没有导航它）："
                    f"不得凭未发生的检索取得材料归属（fail-closed）")
            if mine != is_new_side:
                continue
            if mine and row.arm == "A" and not row.material_ids:
                # 类型层已挡（臂 A 必须带材料）；这里只是把「重研 = 真有材料」再钉一次。
                raise TopicRuntimeError(
                    f"后继装配的重研 ({pair!r}) 落臂 A 却没有材料归属（fail-closed）")
            previous = existing.get(pair)
            if previous is not None and previous != row:
                raise TopicRuntimeError(
                    f"后继装配的逐来源结果 {pair!r} 在同一侧出现两次且内容不同："
                    f"不得静默取一条（fail-closed）")
            existing[pair] = row

    outcomes: list[TS.SourceAspectOutcome] = []
    for pair, resp in derived.items():
        row = existing.get(pair)
        if row is None:
            if resp.retrieval_required:
                record = TS.SourceSearchOutcomeRecord(
                    record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
                    aspect_id=resp.aspect_id, source_document_key=resp.source_document_key,
                    arm="C2", audit_id=None, attempts=(), synthesized_stop_reason=None,
                    searched_need_ids=(), valid_attempt_count=0, time_window="",
                    qualified=False, qualification_reasons=(),
                    projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
                    unfulfilled_reason="not_dispatched",
                    responsibility_fingerprint=resp.fingerprint(),
                    impact=_unfulfilled_impact(resp))
                outcomes.append(TS.SourceAspectOutcome(
                    aspect_id=resp.aspect_id, source_document_key=resp.source_document_key,
                    arm="C2", responsibility_fingerprint=resp.fingerprint(),
                    material_ids=(), search_record=record, not_required_basis=()))
            else:
                basis = tuple(b for b in resp.basis
                              if b[0] in TS.NOT_REQUIRED_BASIS_RULE_IDS)
                outcomes.append(TS.SourceAspectOutcome(
                    aspect_id=resp.aspect_id, source_document_key=resp.source_document_key,
                    arm="C1", responsibility_fingerprint=resp.fingerprint(),
                    material_ids=(), search_record=None, not_required_basis=basis))
            continue
        if row.responsibility_fingerprint != resp.fingerprint():
            raise TopicRuntimeError(
                f"后继装配的逐来源结果 {pair!r} 的责任指纹与合并后重派生的责任不一致："
                f"责任轴在结果成文之后被动过（fail-closed）")
        outcomes.append(row)
    ordered = sorted(outcomes,
                     key=lambda o: (o.aspect_id, o.source_document_key.document_id))
    responsibility_rows = tuple(
        derived[key] for key in sorted(derived))
    return sources, responsibility_rows, tuple(ordered)


def _merge_source_comparison_audit(
        base: TS.TopicResearchPack,
        new: TS.TopicResearchPack) -> TS.SourceComparisonAudit | None:
    """两侧来源互比审计的确定性合并：两侧都有且不同即拒，一侧有一侧无则取有的一侧。"""
    if base.source_comparison_audit is None:
        return new.source_comparison_audit
    if new.source_comparison_audit is None:
        return base.source_comparison_audit
    if base.source_comparison_audit != new.source_comparison_audit:
        raise TopicRuntimeError(
            "后继装配的来源互比审计在两套来源里内容不同：不得静默取一侧（fail-closed）")
    return base.source_comparison_audit


def build_pack_successor(*, base: TS.TopicResearchPack, new: TS.TopicResearchPack,
                         requirement: TS.TopicResearchRequirement,
                         researched_aspect_ids: tuple[str, ...]) -> TS.TopicResearchPack:
    """把「重研一条 aspect 的运行结果」装配成**完整** aspect 集的确定性后继 Pack。

    前置（任一不满足即 typed fail-closed，不做任何降级猜测）：

    - `base` 必须就是该 requirement 的**完整** current Pack（身份 7 项 + aspect 集合 +
      依赖指纹 + question 集合逐项相等）；
    - `researched_aspect_ids` 非空且 ⊆ requirement 的 aspect 集合，且 `new` 确实给出了
      这些 aspect 的结果（本次运行没跑出结果不得靠继承掩盖）；`new` 里其余 aspect 只允许
      是**与 base 逐字段相同**的继承副本，本轮自造出来的第三条来源一律拒；
    - 继承侧引用必须闭合：继承来的 aspect 结果引用的 fact/material/gap/audit 必须仍在后继
      里（被替换 aspect 独占的 fact/material 不会被带进来，因此共用情形必须显式拒绝）。

    继承是**逐行**的：只有与重研 aspect 相交的行被替换，其余行原样（同一对象）继承；材料
    按「被继承 aspect 结果引用」判定归属；处置（RMD）在合并后的数据上**整体重算**，与会话
    运行时的唯一构造口径一致。
    """
    researched = frozenset(researched_aspect_ids)
    if not researched:
        raise TopicRuntimeError("后继装配必须显式指出至少一条被重研的 aspect（fail-closed）")
    required_ids = tuple(requirement.aspect_ids())
    if not researched <= set(required_ids):
        raise TopicRuntimeError(
            f"被重研的 aspect {sorted(researched - set(required_ids))!r} 不在 requirement 的 "
            f"aspect 集合内（越界即拒）")
    expected_identity = requirement_pack_identity(requirement)
    if base.identity() != expected_identity:
        raise TopicRuntimeError(
            "后继的 base Pack 与 requirement 身份不一致（继承必须来自同 identity 的 current Pack）")
    if base.dependency_fingerprint != requirement.dependency_fingerprint():
        raise TopicRuntimeError(
            "后继的 base Pack 依赖指纹与 requirement 重算不一致（不得继承过期 Pack）")
    if set(r.aspect_id for r in base.aspect_results) != set(required_ids):
        raise TopicRuntimeError(
            "后继的 base Pack aspect 集合与 requirement 不相等（base 不是完整 current Pack，"
            "继承会产出缺 aspect 的后继）")
    if set(requirement.question_ids) != set(base.question_ids):
        raise TopicRuntimeError("后继的 base Pack question 集合与 requirement 不相等")
    base_by_aspect = {r.aspect_id: r for r in base.aspect_results}
    new_by_aspect = {r.aspect_id: r for r in new.aspect_results}
    new_aspects = set(new_by_aspect)
    if researched - new_aspects:
        raise TopicRuntimeError(
            f"本次运行没有给出被重研 aspect {sorted(researched - new_aspects)!r} 的结果："
            f"用继承掩盖「本轮其实没跑」不合法")
    # `new` 是**同一次运行**的产物：它为让中间 Pack 自身就是一份完整 Pack，除本轮真的重研的
    # aspect 外，还会原样装入继承结果。因此「越权重研」的判据不是「出现在 new 里」，而是
    # 「出现在 new 里却与 base 不逐字段相同」——那才是本轮自己造出来的东西。只允许两种来源：
    # 本轮重研，或**逐字段原样**继承。
    invented = sorted(
        a for a in new_aspects - researched
        if a not in base_by_aspect
        or new_by_aspect[a].to_dict() != base_by_aspect[a].to_dict())
    if invented:
        raise TopicRuntimeError(
            f"本次运行给出了未被授权重研、又不是 base 原样的 aspect {invented!r}："
            f"后继里每条 aspect 只允许两种来源（本轮重研 / 逐字段继承）")

    # 继承侧取 base 的**同一个对象**（不是 new 里的等值副本），使「继承」在对象级也成立。
    fused_results = tuple(
        new_by_aspect[a.aspect_id] if a.aspect_id in researched
        else base_by_aspect[a.aspect_id]
        for a in requirement.aspects)
    # 冻结投影必须逐字段一致（继承来的与本次运行的都不得偏离冻结 Contract）。
    for r in fused_results:
        frozen = next(a for a in requirement.aspects if a.aspect_id == r.aspect_id)
        if r.requirement_snapshot.to_dict() != frozen.to_dict():
            raise TopicRuntimeError(
                f"后继的 aspect {r.aspect_id!r} 冻结投影与 requirement 不一致"
                f"（不得继承/产出偏离 Contract 的 aspect 结果）")

    # --- 行族继承：与重研 aspect 相交的行由本次运行替代 ---
    inherited_results = tuple(r for r in base.aspect_results
                              if not _owned_by_research((r.aspect_id,), researched))
    candidates = _merge_rows(
        [c for c in base.fact_candidates
         if not _owned_by_research(c.aspect_ids, researched)],
        new.fact_candidates, "candidate_id", "FactCandidate")
    inherited_candidate_ids = {c.candidate_id for c in candidates}
    decisions = _merge_rows(
        [d for d in base.fact_qualification_decisions
         if d.candidate_id in inherited_candidate_ids],
        new.fact_qualification_decisions, "decision_id", "FactQualificationDecision")
    facts = _merge_rows(
        [f for f in base.facts if not _owned_by_research(f.aspect_ids, researched)],
        new.facts, "fact_id", "SupportedFact")
    external_facts = _merge_rows(
        [f for f in base.external_facts if not _owned_by_research(f.aspect_ids, researched)],
        new.external_facts, "external_fact_id", "ExternalFact")
    unresolved = _merge_rows(
        [g for g in base.unresolved
         if not _owned_by_research(g.aspect_ids, researched)],
        new.unresolved, "unresolved_id", "ResearchGap")
    contract_gaps = _merge_rows(
        [g for g in base.contract_gaps
         if not _owned_by_research((g.aspect_id,), researched)],
        new.contract_gaps, "gap_id", "ContractGap")
    research_blocks = _merge_rows(
        [b for b in base.research_blocks
         if not _owned_by_research((b.aspect_id,), researched)],
        new.research_blocks, "block_id", "ResearchBlock")
    # 审计按「是否仍被继承的 aspect 结果 / 继承的 not_found gap 引用」继承（审计不是按 aspect
    # 分行的族，若按 aspect 猜会丢失被引用的行）。
    inherited_audit_ids = {
        r.not_found_audit_id for r in inherited_results if r.not_found_audit_id}
    inherited_audit_ids |= {g.not_found_audit_id for g in unresolved
                            if g.not_found_audit_id}
    not_found_audits = _merge_rows(
        [a for a in base.not_found_audits if a.audit_id in inherited_audit_ids],
        new.not_found_audits, "audit_id", "NotFoundAudit")
    # 材料按「被继承 aspect 结果引用」判定归属：只被被替换 aspect 用过的材料不再继承（若本次
    # 运行重新取得同一 material_id，则由 new 侧带回来，身份相同故合并无冲突）。
    inherited_material_ids = {mid for r in inherited_results for mid in r.material_ids}
    # 表对象材料不在 `material_ids` 里（理由见该列声明处），但它们的栏目归属记在 base 自己的
    # RMD 上：「该 RMD 的 aspect 里还有被继承的」就是这条材料的继承依据。与正文材料**同一条
    # 纪律**（引用身份内容寻址，故同一份文档版本上重切必然给出同一 payload 身份）；文档版本变了
    # 就如实悬空，由提交流程 fail-closed，不在这里悄悄换一份。
    inherited_aspect_ids = {r.aspect_id for r in inherited_results}
    inherited_material_ids |= {
        d.material_id for d in base.material_dispositions
        if set(d.aspect_ids) & inherited_aspect_ids}
    materials = _merge_rows(
        [m for m in base.materials if m.material_id in inherited_material_ids],
        new.materials, "material_id", "ResearchMaterial")

    # --- 继承侧引用闭合：不闭合即拒（不得产出引用悬空的后继） ---
    fact_ids = {f.fact_id for f in facts}
    material_id_set = {m.material_id for m in materials}
    gap_ids = {g.unresolved_id for g in unresolved}
    audit_ids = {a.audit_id for a in not_found_audits}
    for r in inherited_results:
        dangling_facts = sorted(set(r.supported_fact_ids) - fact_ids)
        dangling_materials = sorted(set(r.material_ids) - material_id_set)
        dangling_gaps = sorted(set(r.unresolved_ids) - gap_ids)
        dangling_audits = ([r.not_found_audit_id]
                           if r.not_found_audit_id and r.not_found_audit_id not in audit_ids
                           else [])
        if dangling_facts or dangling_materials or dangling_gaps or dangling_audits:
            raise TopicRuntimeError(
                f"后继装配无法保持继承 aspect {r.aspect_id!r} 的引用闭合："
                f"facts={dangling_facts} materials={dangling_materials} "
                f"gaps={dangling_gaps} audits={dangling_audits}"
                "（该行族与被重研 aspect 共用，不得只替换一半；fail-closed）")

    # 表对象材料的栏目归属只从 base/new **自己的** RMD 里取（两侧都是同一构造产出的），并且
    # 只为**未被 `material_ids` 归属过**的材料补——正文材料的 RMD 因此与不带本参数时逐字段
    # 相同，P4 读门的重算比对不受影响。
    attributed_material_ids = {mid for r in fused_results for mid in r.material_ids}
    extra_material_aspects: dict[str, set[str]] = {}
    for disposition in (*base.material_dispositions, *new.material_dispositions):
        if disposition.material_id in attributed_material_ids:
            continue
        extra_material_aspects.setdefault(disposition.material_id, set()).update(
            disposition.aspect_ids)
    dispositions = build_material_dispositions(
        materials=materials, aspect_results=fused_results, candidates=candidates,
        extra_material_aspects={mid: sorted(ids)
                                for mid, ids in extra_material_aspects.items()
                                if ids})

    # --- 双轴状态在**完整** aspect 集上重新派生（不照抄任何一侧） ---
    blocking_gap_ids_by_aspect: dict[str, set[str]] = {}
    for g in unresolved:
        if g.blocking:
            for aid in g.aspect_ids:
                blocking_gap_ids_by_aspect.setdefault(aid, set()).add(g.unresolved_id)
    needs_stop_reason = any(
        r.status == "blocked"
        and not (set(r.unresolved_ids) & blocking_gap_ids_by_aspect.get(r.aspect_id, set()))
        for r in fused_results)
    # hard stop 的继承是**有条件**的：只有继承来的 blocked aspect 缺 blocking gap 时，才必须
    # 把 base 的 stop_reason 带过来（否则那条 aspect 的终态在后继里失去依据）；本轮自己产生的
    # stop_reason 优先。两轮都无 stop 且无此需要时如实为空。
    stop_reason = new.usage.stop_reason or (base.usage.stop_reason if needs_stop_reason else None)
    usage = new.usage if stop_reason == new.usage.stop_reason else dataclasses.replace(
        new.usage, stop_reason=stop_reason)
    process, coverage, derivation = TS.derive_pack_status(
        required_ids, fused_results, stop_reason=stop_reason)

    # 来源轴按**合并后的完整 aspect 集**整套重派生（责任）+ 保留/补齐（结果）；见
    # `merge_source_axis`。这不照抄任何一侧：base 的源集可能比本轮窄。
    merged_source_set, merged_responsibility, merged_outcomes = merge_source_axis(
        base=base, new=new, aspects=requirement.aspects,
        researched_aspect_ids=tuple(researched_aspect_ids))

    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="",
        run_id=new.run_id, task_id=requirement.task_id,
        company_id=requirement.company_id, report_as_of=requirement.report_as_of,
        contract_version=requirement.contract_version,
        contract_fingerprint=requirement.contract_fingerprint,
        source_policy_version=requirement.source_policy_version,
        section_id=requirement.section_id, topic_id=requirement.topic_id,
        question_ids=requirement.question_ids,
        aspect_results=fused_results, materials=materials, facts=facts,
        material_dispositions=dispositions, fact_candidates=candidates,
        fact_qualification_decisions=decisions, external_facts=external_facts,
        contract_gaps=contract_gaps, research_blocks=research_blocks,
        outcome_refs=tuple(dict.fromkeys((*base.outcome_refs, *new.outcome_refs))),
        # 漏斗快照是**每次运行**的观测：本轮真的走过外部来源就取本轮的，否则继承 base 的
        # （它不进任何语义门，只作来源轨迹）。
        external_funnel=(new.external_funnel if new.external_funnel is not None
                         else base.external_funnel),
        conflicts=_merge_rows(base.conflicts, new.conflicts, "conflict_id", "ResearchConflict"),
        not_found_audits=not_found_audits, unresolved=unresolved, usage=usage,
        uncertain_calls=_merge_rows(base.uncertain_calls, new.uncertain_calls, "call_key",
                                    "UncertainToolCallRecord"),
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=requirement.dependency_fingerprint(),
        source_set=merged_source_set,
        aspect_source_responsibility=merged_responsibility,
        source_aspect_outcomes=merged_outcomes,
        # 来源互比审计是**每次运行**的观测（它记的是「本轮把哪几份摆在一起比过」），不进任何
        # 语义门；两侧都有且不同即拒，而不是静默取一侧。两侧都没有时如实为 None。
        source_comparison_audit=_merge_source_comparison_audit(base, new))
    return TS.finalize_pack(pack)


# ---------------------------------------------------------------------------
# 7. 公开入口
# ---------------------------------------------------------------------------

def run_topic_requirement(requirement: TS.TopicResearchRequirement, *,
                          run_context: TopicRunContext,
                          dependencies: TopicRuntimeDependencies,
                          researched_aspect_ids: tuple[str, ...] | None = None,
                          successor_of: TS.TopicResearchPack | None = None,
                          follow_up_focus: dict | None = None) -> TopicRuntimeResult:
    """按 required aspects 调度研究，装配并 append-only 提交一个 current TopicResearchPack。

    边界③（`FollowUpNeed`）用两个**显式**参数复用本函数，不新建第二套研究运行时：

    - `researched_aspect_ids` + `successor_of`：只重研指定的 aspect，其余 aspect 从同 identity
      的 current Pack **确定性继承**，装配成**完整** aspect 集的后继后由**本函数**用**完整**
      requirement 提交（恰一次），因此不存在「先提交一条缺 aspect 的中间 Pack」的窗口；
    - `follow_up_focus`：把该 `FollowUpNeed` 的诉求绑定进本轮检索真正使用的 `InformationNeed`。

    **作用域**：本函数是本仓唯一的研究入口，因此「研究调用落在哪个作用域」只有这一处可定。
    整个函数体在**本 topic** 的作用域（`llm.budget.research_scope(topic_id)`）内执行，于是研究
    阶段的每一次 `chat_with_usage` 都带着 topic 身份到达事前门——按 topic 计量、与写作按节
    计量的账**互不消耗**；没有 topic 作用域的研究请求会在发出前被拒，而不是以「某节发生了
    研究调用」的形态记进账本。写作侧仍由 `section_scope` 按节计量，两者不嵌套。
    """
    if not isinstance(requirement, TS.TopicResearchRequirement):
        raise TopicRuntimeError("run_topic_requirement 需要 TopicResearchRequirement")
    topic_id = str(getattr(requirement, "topic_id", "") or "")
    if not topic_id:
        raise TopicRuntimeError(
            "requirement 没有 topic_id：研究调用的作用域无法确定，"
            "不得在没有 topic 身份的账上发起研究请求（fail-closed）")
    from llm import budget as LB

    with LB.research_scope(topic_id):
        return _run_topic_requirement_scoped(
            requirement, run_context=run_context, dependencies=dependencies,
            researched_aspect_ids=researched_aspect_ids, successor_of=successor_of,
            follow_up_focus=follow_up_focus)


def _run_topic_requirement_scoped(requirement: TS.TopicResearchRequirement, *,
                                  run_context: TopicRunContext,
                                  dependencies: TopicRuntimeDependencies,
                                  researched_aspect_ids: tuple[str, ...] | None = None,
                                  successor_of: TS.TopicResearchPack | None = None,
                                  follow_up_focus: dict | None = None) -> TopicRuntimeResult:
    """`run_topic_requirement` 的函数体（**不加作用域**；作用域只由上面那一个入口负责）。"""
    if not isinstance(requirement, TS.TopicResearchRequirement):
        raise TopicRuntimeError("run_topic_requirement 需要 TopicResearchRequirement")
    if not isinstance(run_context, TopicRunContext):
        raise TopicRuntimeError("run_topic_requirement 需要 TopicRunContext")
    if not isinstance(dependencies, TopicRuntimeDependencies):
        raise TopicRuntimeError("run_topic_requirement 需要 TopicRuntimeDependencies")
    _require_identity(requirement, run_context)

    deps = dependencies
    ledger = deps.budget_state
    if ledger.policy != run_context.budget_policy:
        raise TopicRuntimeError(
            "budget_state.policy 与 run_context.budget_policy 不一致（fail-closed）")
    # `TopicBudgetState` 是**单次运行**的记账对象：它同时产出该次运行的 usage 快照，而 usage
    # 进 Pack 内容身份。若允许复用已记账的 ledger，同一 task + 同一权威内容跨 run 会得到不同
    # pack_id（用量被叠加），幂等复用被静默破坏，且预算是跨运行虚耗。故要求每次运行用全新账本。
    if (ledger.rounds or ledger.tool_calls or ledger.llm_calls or ledger.input_tokens
            or ledger.output_tokens or ledger.elapsed_ms or ledger.wall_clock_ms
            or ledger.stop_reason is not None or ledger.checkpoint_ref is not None):
        raise TopicRuntimeError(
            "budget_state 已经记过账（rounds/tool_calls/llm_calls/tokens/elapsed/stop/"
            "checkpoint 任一非空）：每次运行必须使用全新 TopicBudgetState（fail-closed）")
    resolver = CombinedPayloadResolver(deps.payload_resolvers)
    research_question = deps.research_question or RT.run_question
    need_budget = run_context.budget_policy.need_budget().build()
    # 单次 focused need 的**结构上界**：由同一个 policy 推出，因此「事前预留多少」与
    # 「内层最多花多少」永远同源（内层预算与这里读的是同一个 `need_budget()`）。
    need_reserve = focused_need_reserve(run_context.budget_policy)
    t0 = time.perf_counter()

    trace_refs: list[str] = []
    outcome_refs: list[str] = []
    all_materials: list[TS.ResearchMaterial] = []
    material_ids: set[str] = set()
    all_facts: dict[str, TS.SupportedFact] = {}
    # M930-3A：候选 / 资格决定 / authority-specific qualified result / 材料处置 逐层累积。
    all_candidates: dict[str, TS.FactCandidate] = {}
    all_decisions: dict[str, TS.FactQualificationDecision] = {}
    all_external_facts: dict[str, TS.ExternalFact] = {}
    all_contract_gaps: dict[str, TS.ContractGap] = {}
    all_research_blocks: dict[str, TS.ResearchBlock] = {}
    all_gaps: list[TS.ResearchGap] = []
    declared_gaps: list[dict] = []
    aspect_results: list[TS.AspectResearchResult] = []
    planned_need_ids: list[str] = []
    # §九：Pack 的 `not_found_audits` / `external_funnel` **只由真实轨迹派生**，不再固定为空。
    not_found_audits: list[TS.NotFoundAudit] = []
    external_attempts: list[dict] = []

    def _emit(event_type: str, payload: dict) -> None:
        trace_refs.append(f"{run_context.run_id}:{event_type}:{len(trace_refs)}")
        deps.trace_sink.emit(event_type, payload)

    source_policy = None
    if requirement.aspects:
        refs = {a.source_policy_ref for a in requirement.aspects}
        if len(refs) != 1:
            # Store 也只接受恰好一个 distinct SourcePolicyRef（一个 Pack 绑一份政策）。
            raise TopicRuntimeError(
                f"requirement 的 aspect 携带 {len(refs)} 个不同 SourcePolicyRef"
                f"（一个 Pack 只能绑定一份冻结来源政策，fail-closed）")
        first_ref = requirement.aspects[0].source_policy_ref
        try:
            source_policy = TS.verify_frozen_source_policy(
                first_ref, deps.source_policy_resolver.resolve(first_ref))
        except TS.SchemaValidationError as e:
            raise TopicRuntimeError(
                f"SourcePolicy 无法从独立冻结来源解析（fail-closed）: {e}") from e

    # --- §L1.5 逐 aspect × 逐来源责任：**在任何检索之前**由输入轴定死 -------------
    #
    # 这是本轮的「检索前一刻」：此刻还没有发出任何一次工具调用，因此派生结果不可能含结果字段。
    # 责任台账覆盖**整个 requirement 的 aspect 集合**，包括后继模式下本轮不重研、直接继承的
    # 那些（继承不是「这一轮不查所以不用负责」——它们仍在本轮 Pack 的域内）。
    #
    # `current_state` 传 `CURRENT_STATE_RESOLVED` 是**如实**而非乐观：本运行能走到这里，说明
    # 来源集已经通过 `_admit_source_set`（`ambiguous_current_state` / `no_eligible_current` 在
    # 构造 `TopicRunContext` 之前就被阻断）。若将来放开那道门，这里必须改成从来源清单读真实
    # 状态，否则 `ambiguous_current_state_no_proof_domain` 这条规则永远不会触发。
    aspect_source_responsibility = tuple(
        row
        for aspect in requirement.aspects
        for row in derive_aspect_source_responsibility(
            aspect=aspect, sources=run_context.sources,
            current_state=SM.CURRENT_STATE_RESOLVED))
    responsibility_fingerprint = aspect_source_responsibility_fingerprint(
        aspect_source_responsibility)
    _emit("ASPECT_SOURCE_RESPONSIBILITY", {
        "responsibility_fingerprint": responsibility_fingerprint,
        "source_set_fingerprint": run_context.source_set_fingerprint(),
        "rule_version": ASSUME_RULE_VERSION,
        # 逐条可核：读回的人能看到「这条责任凭哪条规则定」以及「它是不是结果字段倒填的」。
        "rows": [r.to_dict() for r in aspect_source_responsibility],
    })
    # 逐 aspect 的**真实检索痕迹**（由工具调用本身记录，不由"没查到"反推）。键是 aspect_id。
    per_aspect_search_traces: dict[str, list[dict]] = {}
    # §0.3.3 逐来源结果的三个真实输入面：该 `(aspect, 来源)` 产出的材料 / 该 aspect 的
    # not-found 审计 / 该 aspect 未完成的**真实**理由。三者都来自实际执行，不由责任记录反推。
    # 材料的键带文档身份：逐成员派发后同一条 aspect 会在多份文档上各自产出材料，按 aspect
    # 汇总会让臂 A 凭「另一份的材料」成立（§L2.4 同一条纪律：来源必须写在行上）。
    material_ids_by_aspect_source: dict[tuple[str, str], list[str]] = {}
    # 表对象材料的**栏目归属**：`material_id → {aspect_id}`。它不是从 `material_ids` 反查的
    # （那一列不收表材料），而是在 `_attribute_table_materials` 里就地记下**是谁的读取把这块
    # 定位到的**。RMD 的 `aspect_ids` 因此对表材料同样成立，而覆盖门 / 引用索引 / set_complete
    # 的判据面不受影响（它们只看 `material_ids`）。
    table_material_aspects: dict[str, set[str]] = {}
    per_aspect_not_found_audit: dict[str, Any] = {}
    unfulfilled_reason_by_aspect: dict[str, str] = {}
    # §0.3.3 / 定点返修 C2：**派发发生过、但没有发出任何调用**的 `(aspect_id, document_id)`
    # 及其 typed 原因。这是研究相位的事实（这一次派发确实执行了、也确实有结论），不是从
    # 「没有材料」倒推的结论。未派发的成员**不在此**——它们由四臂台账记成 `not_dispatched`。
    dispatched_without_call: dict[tuple[str, str], str] = {}
    # 本栏目的**可选**调用（有界重切 / focused follow-up）被 topic 级预算挡下的次数。与
    # `dispatched_without_call` 分开记：那是「该派的必读没发出去」，这是「必读如实发完了，
    # 补件没有额度」。两者都进本栏目 `column_unmet` 的 `budget_blocked_dispatch`，但计数分列，
    # 读回的人因此能分开「结构性缺陷」与「预算被如实用在了必读上」（§四 原因不得合并）。
    optional_blocked_by_aspect: dict[str, int] = {}

    # --- 边界③ 后继模式：只重研指定 aspect，其余 aspect 确定性继承 ---
    if successor_of is None and researched_aspect_ids is not None:
        raise TopicRuntimeError(
            "researched_aspect_ids 只服务后继装配：必须同时给出 successor_of（继承来源），"
            "否则「其余 aspect 从哪来」不可知（fail-closed）")
    if successor_of is not None and not researched_aspect_ids:
        raise TopicRuntimeError(
            "后继装配必须显式指出被重研的 aspect（不得靠缺省值把「整 requirement 继承一遍」"
            "伪装成一次 follow-up 执行）")
    researched_set = frozenset(researched_aspect_ids or ())
    inherited_by_aspect: dict[str, TS.AspectResearchResult] = {}
    if successor_of is not None:
        for r in successor_of.aspect_results:
            inherited_by_aspect[r.aspect_id] = r

    # --- 初次必读的**预留**：在本轮任何工具调用之前一次性定死 ---------------------
    #
    # 这一步只做两件事：算出本 topic 的初次必读结构上界，并把它**预留**下来。它不改任何
    # 上限（上限是 `ResearchBudgetPolicy` 的事，由调用方按同一条式子推导），也不改判据；
    # 它保证的是**顺序**：可选补件（兜底重切 / focused follow-up）花不到这笔预留，
    # 因此 `retrieval_required` 的 `(栏目, 来源)` 不会被补件挤掉第一次读取。
    #
    # 继承的 aspect 不派发、不占预留（见 `mandatory_dispatch_counts` 的 `skip_aspect_ids`）。
    inherited_aspect_ids = frozenset(
        str(a.aspect_id) for a in requirement.aspects
        if successor_of is not None and a.aspect_id not in researched_set)
    anchor_key = run_context.sources.current_state_key()
    multi_source_navigation = hasattr(deps.navigation, "candidates_for")
    mandatory_counts = mandatory_dispatch_counts(
        requirement.aspects, aspect_source_responsibility, anchor=anchor_key,
        multi_source_navigation=multi_source_navigation,
        skip_aspect_ids=inherited_aspect_ids)
    mandatory_bound = int(sum(mandatory_counts.values()))
    if mandatory_bound > int(ledger.policy.max_tool_calls_per_topic):
        # 上限盖不住初次必读的结构上界 ⇒ 本 run 必然在必读阶段被自己的预算截断。这不是
        # 「更严格」，而是把一次注定失败的运行伪装成可运行（同一文件 :10400 已就写作/研究
        # 的 LLM 轴写明这条判据）。fail-closed：不发任何调用，由调用方按同一条式子重推上限。
        raise TopicRuntimeError(
            f"topic 工具上限 {ledger.policy.max_tool_calls_per_topic} 低于本 topic 的初次必读"
            f"结构上界 {mandatory_bound}（逐 aspect 派发基数 {mandatory_counts}）："
            "上限必须盖得住结构上界，否则运行必然在必读阶段被自己的预算截断。"
            f"规则见 {MANDATORY_FIRST_READ_RULE_VERSION}")
    ledger.reserve_tool_calls(mandatory_bound)
    _emit("MANDATORY_FIRST_READ_BUDGET", {
        "rule_version": MANDATORY_FIRST_READ_RULE_VERSION,
        "topic_id": str(requirement.topic_id),
        "per_aspect_dispatch": mandatory_counts,
        "mandatory_bound": mandatory_bound,
        "max_tool_calls_per_topic": ledger.policy.max_tool_calls_per_topic,
        "optional_allowance": (int(ledger.policy.max_tool_calls_per_topic)
                               - mandatory_bound),
        # 上面那个数是**静态**上界（上限 − 必读结构上界），不是「本轮补件实际发了几次」：
        # 同读集复用（`rsr-1`）与「导航无候选」都不花调用，未花掉的必读份额会**真实**变成
        # 现场可选余量。把它读成「补件必然为 0」是本批要避免的第二种误读（第一种是把
        # 「预算没让检索发生」读成「语料里没有」），故在这里写明来路与去处。
        "optional_allowance_basis": (
            "static_upper_bound=ceiling-mandatory_bound；现场可选余量还取决于必读的真实花费"
            "（`rsr-1` 复用与导航无候选不花调用，未花掉的必读份额会转为可选余量）；"
            "每一次可选调用仍须通过 `can_afford_optional`，预留份额不会被补件吃掉"),
        "reserve_rule_version": FOCUSED_NEED_RESERVE_RULE_VERSION,
        "inherited_aspects": sorted(inherited_aspect_ids),
        "anchor_document_id": str(getattr(anchor_key, "document_id", "") or ""),
        "multi_source_navigation": multi_source_navigation})
    #: 同一 topic 内「同一份来源 + 同一个读集 + 同一个消费计划与上限 + 同一续读位」的
    #: 真实结果缓存（键不含任何结果字段）。
    #:
    #: 值不是材料元组，而是一次读取的**全部真实读数**：材料、被上限挡下的候选、表材料，
    #: 以及截断读数（`gap_count` / `content_disposition_count` / `rounds` /
    #: `unread_span_positions` / `complete`）。复用必须把这份读数**整体**交回——否则
    #: 「首读被截断」的结论会被复用的 `gap_count=0` 洗成「已读完整」。
    #: 复用交出的仍是**同一批材料对象**，因此不新增材料身份、不虚增调用数。
    read_set_cache: dict[tuple, dict] = {}

    for aspect in requirement.aspects:
        if successor_of is not None and aspect.aspect_id not in researched_set:
            # 继承的 aspect 本轮**不重研**：原样取用 base 的同一个结果对象，不跑工具、不重算
            # 状态、不记 planned need（继承不是「又研究了一遍」）。
            inherited = inherited_by_aspect.get(aspect.aspect_id)
            if inherited is None:
                raise TopicRuntimeError(
                    f"后继装配缺少 aspect {aspect.aspect_id!r} 的继承来源"
                    f"（base Pack 没有该 aspect 的结果）")
            aspect_results.append(inherited)
            continue
        need_id = _join_id(run_context.task_id, aspect.aspect_id)
        planned_need_ids.append(need_id)
        aspect_materials: list[TS.ResearchMaterial] = []
        # 表对象材料（`table_context`，本通道解析语义版本 `tom-1`）**另立一列**：它们进 Pack
        # 全局集、进四轴台账、进本栏目的表格材料计数，但**不进** `aspect_materials`。
        # 理由不是洁癖：`aspect_materials` 会被 `_index_materials_by_evidence` 按
        # `authority.evidence_id` 建索引，而引用解析要求该索引在同一块上**唯一**
        # （`_resolve_material_for_citation`：同页不能收窄），表材料与正文材料常常同宿主块，
        # 混进去会让同块引用一律落 `citation_ambiguous_across_spans`。因此两条列分开。
        aspect_table_materials: list[TS.ResearchMaterial] = []
        aspect_gaps: list[TS.ResearchGap] = []
        rejected_claims: list[str] = []
        attempted: list[str] = []
        # §九 终态所需的**逐 aspect 真实轨迹**：全部来自实际执行过的工具调用/工具上报，
        # 绝不由"没查到"反推。
        valid_attempts = 0            # 实际执行过的检索/工具尝试次数
        # §九 P1-B：真实轨迹**对象**（ToolResult / ResearchState）。触达了哪些来源类一律由
        # `attempted_source_classes()` 从这些对象自带的既有字段派生——不接受自造标签。
        traces: list[Any] = []
        external_attempted = False    # 是否真有外部用量（由 UsageLedger/快照派生，非标签）
        candidate_ids: set[str] = set()   # 工具真实上报过的候选父 Evidence id
        unattempted_ids: list[str] = []   # 被有界上限真实挡下的候选（span id）
        context_expansion = False         # 是否真的走过 bounded Evidence 扩读（重切）路径
        last_stop_reason: str | None = None

        # --- 7.1 树导航 → 有界候选 node → inspect_outline_materials ---------
        # 跨源（§L3/§L4.2）：**逐份**来源各导航一次、各发一次**带该份身份**的调用。
        # 不同文档**不得**合成一次不带文档身份的调用（§L4.3）；同一次调用被多个 aspect
        # 引用不在此处发生——本环一趟只服务本 aspect。
        dispatch_keys = _dispatch_source_keys(
            aspect.aspect_id, aspect_source_responsibility, anchor=anchor_key,
            multi_source_navigation=multi_source_navigation)
        if len(dispatch_keys) != mandatory_counts.get(aspect.aspect_id):
            # 预留量与现场派发域**必须同源**：两者由同一个 `_dispatch_source_keys` 决定。
            # 不等即说明预留是在另一个输入轴上算出来的（那笔预留保护的不是本栏目的必读）。
            raise TopicRuntimeError(
                f"aspect {aspect.aspect_id} 的现场派发域 {len(dispatch_keys)} 与预留基数 "
                f"{mandatory_counts.get(aspect.aspect_id)} 不一致（fail-closed）")
        navigated: list[tuple[Any, Any, tuple[str, ...]]] = []
        for source_key in dispatch_keys:
            source_decision, source_node_ids = _navigate_one_source(
                deps.navigation, aspect, requirement=requirement,
                source_key=source_key, anchor=anchor_key)
            navigated.append((source_key, source_decision, source_node_ids))
            _emit("TREE_NAVIGATION", {
                "aspect_id": aspect.aspect_id, "status": source_decision.status,
                # 「这一份的读集」必须带文档身份：源集合并后只给 node_id，读者无法**直接**知道
                # 某节点属于哪份文档（靠反解哈希 id 判断归属不可接受，§L2.4）。
                "source_document_key": source_key.to_dict(),
                "fallback_reason": source_decision.fallback_reason,
                "selected_node_id": source_decision.selected_node_id,
                "candidate_node_ids": list(source_node_ids[:8]),
                "profile_id": deps.navigation.profile_id if hasattr(deps.navigation, "profile_id") else None,
                #: `anp-4`：定位这条 aspect 用的是**哪两层键**、以及实际生效的是哪一层。
                #: 「读集是怎么来的」在产物里必须能回答——本层键（Contract 声明字段）还是
                #: 祖先层键（Contract 主题/问题文本派生的父节点标签）；哪一层生效由候选依据
                #: 里的层标识判定，不由调用方声明。只读回读，不参与任何判据。
                "nav_keys": list(getattr(source_decision, "nav_keys", ())),
                "parent_keys": list(getattr(source_decision, "parent_keys", ())),
                "key_tier": (
                    "ancestor" if any(
                        NAV.NAV_KEY_TIER_ANCESTOR in reason
                        for c in tuple(getattr(source_decision, "ranked", ()))
                        for reason in getattr(c, "reasons", ()))
                    else "declared"),
                "rule_version": getattr(source_decision, "rule_version", None),
                # 有界考察的完整依据面（只读、确定性）：哪些根候选在近分带内、读集是什么、
                # 被 node 预算截掉的真实范围有多大，以及每条候选凭什么被选中/被舍弃。
                "band_node_ids": list(getattr(source_decision, "band_node_ids", ()))[:8],
                # 读根 = 近分带根上提到最上层命中祖先后的结果；读集由读根子树展开。两者都进
                # 轨迹，"哪些候选被考察"与"实际读了哪几棵子树"才逐层对得上号。
                "read_root_node_ids": list(
                    getattr(source_decision, "read_root_node_ids", ()))[:8],
                "read_node_ids": list(source_node_ids[:16]),
                "unread_node_ids": list(
                    getattr(source_decision, "unread_node_ids", ()))[:8],
                "unread_total": int(getattr(source_decision, "unread_total", 0)),
                "candidates": [
                    {"node_id": c.node_id, "title": c.title, "score": c.score,
                     "in_read_set": getattr(c, "in_read_set", None),
                     "discard_reason": getattr(c, "discard_reason", None)}
                    for c in tuple(getattr(source_decision, "ranked", ()))[:8]],
            })
        # 本 aspect 的初次必读窗口**就此打开**：导航（不花工具调用）已完成，接下来是逐份
        # 派发。预留在此刻归还到共享池——「本栏目还没开始读」与「本栏目读完了」是两件事，
        # 早一步归还只会让尾部栏目重复受害，晚一步归还则会让本栏目读完后池子仍然被虚占。
        ledger.release_tool_calls(len(dispatch_keys))

        # --- 7.1a 读取计划（`rpo-1`）：只决定**先读什么** ----------------------------------
        # 导航交出的读集**集合**一字不改；这里只把它置换成一个可审计的消费次序，使并列业务
        # 分支先于模板勾选 / 短残句 / 空容器取得读取机会。键取**topic 级**并集（冻结 Contract
        # 派生），因此同一 topic 的多个栏目对同一读集算出**同一个** `plan_id` —— 复用是按
        # 「同一份来源 + 同一个读集 + 同一个计划」发生的，先执行的栏目不再独占名额。
        _plan_nav_keys, _plan_parent_keys = RPO.topic_plan_keys(
            deps.navigation.navigation_profile, requirement.topic_id)
        planned: list[tuple[Any, Any, "RPO.ReadPlan"]] = []
        for source_key, source_decision, source_node_ids in navigated:
            if not source_node_ids:
                planned.append((source_key, source_decision, None))
                continue
            source_index = deps.navigation.navigation_index_for(
                source_key, anchor=anchor_key)
            plan = RPO.plan_read_order(
                source_index, source_node_ids,
                topic_nav_keys=_plan_nav_keys, topic_parent_keys=_plan_parent_keys)
            planned.append((source_key, source_decision, plan))
            _emit("TREE_READ_PLAN", {
                "aspect_id": aspect.aspect_id,
                "source_document_key": source_key.to_dict(),
                "read_node_ids": list(source_node_ids),
                **plan.to_dict()})

        def _attribute_tree_materials(source_key: Any, mats: Iterable[Any]) -> None:
            """把一次树读取交出的材料登记进 Pack 全局集、本栏目材料池与逐份台账。

            材料归到**产出它的那一次调用寻址的那一份**上（不是"本 aspect 的材料池"）；
            「那一份」是四轴身份，不是 document_id（定点返修 T4）。同一批材料只登记一次身份
            （`material_ids` 去重），多栏目复用因此不会产生第二份材料身份。
            """
            for material in mats:
                TS.verify_material_payload_ref(material.payload_ref, resolver)
                if material.material_id not in material_ids:
                    material_ids.add(material.material_id)
                    all_materials.append(material)
                aspect_materials.append(material)
                material_ids_by_aspect_source.setdefault(
                    (aspect.aspect_id, SM.source_key_axes(source_key)), []).append(
                        material.material_id)

        def _attribute_table_materials(source_key: Any, mats: Iterable[Any]) -> None:
            """把一次树读取交出的**表对象材料**登记进 Pack 全局集、本栏目表材料列与台账。

            与 `_attribute_tree_materials` 同构，只差落点：本栏目的**表材料列**而不是
            `aspect_materials`（理由见该列声明处的注释）。身份去重共用同一个 `material_ids`
            集：同一份材料被多栏目复用仍然只有一份身份。
            """
            for material in mats:
                TS.verify_material_payload_ref(material.payload_ref, resolver)
                if material.material_id not in material_ids:
                    material_ids.add(material.material_id)
                    all_materials.append(material)
                aspect_table_materials.append(material)
                table_material_aspects.setdefault(material.material_id, set()).add(
                    aspect.aspect_id)
                material_ids_by_aspect_source.setdefault(
                    (aspect.aspect_id, SM.source_key_axes(source_key)), []).append(
                        material.material_id)

        # 逐份派发：每份各自决定「有没有可读的候选」与「预算还够不够」。
        # 消费次序取 **7.1a 的读取计划**（`plan.ordered_node_ids`），不再是导航原序：
        # 集合一字未改，改的只是「先读谁」。派发域外的成员**不在这里**留任何痕迹——
        # 它们由四臂台账如实记成 C1/C2。
        for source_key, source_decision, plan in planned:
            source_node_ids = tuple(plan.ordered_node_ids) if plan is not None else ()
            if not source_node_ids:
                # 这一份**已经派发过**（导航为它跑过、`TREE_NAVIGATION` 上有它的结论），
                # 只是没有候选可读，故一次调用都没发出。如实登记这一档，四臂台账才不会
                # 把它读成「从未派发」。
                dispatched_without_call[(aspect.aspect_id,
                                         SM.source_key_axes(source_key))] = (
                    "dispatched_no_candidate")
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": "tree_structure_unavailable",
                    "detail": f"来源 {source_key.document_id} 导航未给出可用候选 node"
                              f"（{source_decision.fallback_reason or source_decision.status}）",
                    "source_document_key": source_key.to_dict(),
                })
                continue
            _max_spans = run_context.budget_policy.tree_max_spans_per_aspect
            _max_chars = run_context.budget_policy.tree_max_chars_per_span
            # `rsr-1`（同读集复用）：同一 topic 内「同一份来源 + 同一个读集 + **同一个消费
            # 计划与上限**」此前已经真读过，再读一次不会得到另一批 span，只会再花一次工具
            # 调用。消费侧对顺序敏感（按 `node_ids` 顺序读到 `max_spans` 即停），因此
            # 「同一集合、不同顺序」**必须**判为不同键——否则就是拿另一批 span 冒充本批结论。
            # `cursor_key=None` 是**读的起点**：复用只在这里查一次，就在第 0 轮之前；续读各轮
            # 都在下面同一个循环体内，**不查缓存**（否则一次续读会绕开它自己该记的调用账，
            # 还会把「从位置 k 起的那批 span」冒充成「整个读集的结论」）。键本身含续读位这一
            # 轴，因此将来若有从游标处起读的新派发点，只要照传 `cursor_key` 就不会误命中；
            # 缓存条目另存首读的 `stop_reason`，复用侧照原样交回（见 `rsr-2`）。
            _reuse_key = read_set_reuse_key(
                source_key, source_node_ids, plan_id=plan.plan_id,
                max_spans=_max_spans, max_chars_per_span=_max_chars,
                cursor_key=None)
            _cached = read_set_cache.get(_reuse_key)
            if _cached is not None:
                _attribute_tree_materials(source_key, _cached["materials"])
                _attribute_table_materials(source_key, _cached["tables"])
                unattempted_ids.extend(_cached["unattempted"])
                _emit("TREE_READ_SET_REUSED", {
                    "aspect_id": aspect.aspect_id,
                    "source_document_key": source_key.to_dict(),
                    "read_node_ids": list(source_node_ids[:16]),
                    "read_plan_id": plan.plan_id,
                    "reused_material_ids": [m.material_id
                                            for m in _cached["materials"]][:16],
                    "reused_material_count": len(_cached["materials"]),
                    # 表材料与正文材料一起复用：同一个读集交出的就是同一批材料。
                    "reused_table_material_count": len(_cached["tables"]),
                    "tool_calls": ledger.tool_calls,
                    "rule_version": READ_SET_REUSE_RULE_VERSION,
                    # 复用必须把**首读的完整读数**一并交回（§需求 2.3）：截断、未读位置、
                    # 轮数、是否读完都随材料一起移交，`gap_count` 不再被洗成 0。
                    "rounds": _cached["rounds"],
                    "complete": _cached["complete"],
                    # 首读的**停止原因**也一并交回：只有 `complete=False` 而没有原因，读的人
                    # 仍不知道是该等预算、该续读、还是工具失败。续读位同样随读数移交。
                    "stop_reason": _cached.get("stop_reason"),
                    "unread_span_positions": _cached["unread_span_positions"],
                    "total_span_positions": _cached["total_span_positions"]})
                _emit("TREE_TOOL_RESULT", {
                    "aspect_id": aspect.aspect_id, "status": "REUSED_READ_SET",
                    "source_document_key": source_key.to_dict(),
                    "error_code": None, "tool_calls": ledger.tool_calls,
                    "evidence_ids": [], "candidate_count": len(_cached["materials"]),
                    "table_material_count": len(_cached["tables"]),
                    "read_plan_id": plan.plan_id, "read_round": 0,
                    "gap_count": _cached["gap_count"],
                    "content_disposition_count": _cached["content_disposition_count"],
                    "unread_span_positions": _cached["unread_span_positions"],
                    "total_span_positions": _cached["total_span_positions"],
                    "complete": _cached["complete"],
                    "reused_from_read_set": True})
                if not _cached["materials"]:
                    declared_gaps.append({
                        "aspect_id": aspect.aspect_id, "reason": "tree_no_material",
                        "detail": f"来源 {source_key.document_id} 的同读集此前读过且未产出可重切的"
                                  f" OutlineSpan 材料（复用同读集结论，不重复调用）",
                        "source_document_key": source_key.to_dict()})
                if not _cached["complete"]:
                    # 复用的是**一份没读完的读数**：首读为什么停，必须在复用侧同样可查。
                    # 否则 `complete=False` 会退化成一句无因的读数，读回时被当成「读完」。
                    declared_gaps.append({
                        "aspect_id": aspect.aspect_id,
                        "reason": _continuation_stop_gap_reason(
                            _cached.get("stop_reason")),
                        "detail": f"来源 {source_key.document_id} 复用的读集此前未读完"
                                  f"（读集 {len(source_node_ids)} 个 root、共 "
                                  f"{_cached['total_span_positions']} 个 span 位置，仍剩 "
                                  f"{_cached['unread_span_positions']} 个未读，停因 "
                                  f"{_cached.get('stop_reason')}）",
                        "source_document_key": source_key.to_dict(),
                        "read_plan_id": plan.plan_id,
                        "continuation_stop_reason": _cached.get("stop_reason"),
                        "unread_span_positions": _cached["unread_span_positions"],
                        "total_span_positions": _cached["total_span_positions"],
                        "rounds": _cached["rounds"],
                        "reused_read_set": True})
                continue
            if not ledger.can_afford(tool_calls=1):
                # 预算不足以再派发一次真实调用：**如实**停在这里，并说明是哪一份被停掉。
                # 不得静默少一次调用（那会让四臂台账把「预算截断」读成「从未要求」）。
                # 注意这一份与上面那一份**不是同一件事**：它已经有了可读候选、是预算没让
                # 调用发出去，故落 `budget_exhausted` 而不是 `dispatched_no_candidate`。
                ledger.note_stop("BUDGET_EXHAUSTED")
                dispatched_without_call[(aspect.aspect_id,
                                         SM.source_key_axes(source_key))] = (
                    "budget_exhausted")
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id, "reason": "tree_budget_exhausted",
                    "detail": f"topic 级预算不足以再派发一次树检索"
                              f"（来源 {source_key.document_id}）",
                    "source_document_key": source_key.to_dict(),
                })
                continue

            # --- 首次调用 + **有界续读**（`tim-2`）----------------------------------------
            # 一次调用只消费 `max_spans` 个 span 位置。读满**不等于**本栏目取材完成：
            # 工具的 `over_max_spans` 条目给出下一位置，剩下的位置按**同一份来源的同一个
            # 计划**继续读。每一次续读在派发前都过 `can_afford`，且轮数上界由工具自报的
            # `total_span_positions` 推出（每轮至少前进一个位置），因此既不越顶也不死循环。
            _round = 0
            _cursor: dict | None = None
            _hard_cap = 0
            _seen_cursors: set[tuple] = set()
            _round_gap_count = 0
            _round_disposition_count = 0
            _round_unread = 0
            _round_total = 0
            _round_seen_ids: set[str] = set()
            _round_materials: list[Any] = []
            _round_tables: list[Any] = []
            _round_unattempted: list[str] = []
            complete = False
            _stop_reason: str | None = None

            while True:
                call = _tree_call(requirement, run_context, need_id=need_id,
                                  aspect_id=aspect.aspect_id,
                                  node_ids=source_node_ids, source_key=source_key,
                                  span_cursor=_cursor, round_index=_round)
                result = deps.registry.execute(call, route="DIRECT_EVIDENCE",
                                               run_id=run_context.run_id)
                # §0.3.3：这一次**真实发出**的调用留一条原始痕迹（含失败）。未发出的调用不在这里
                # 造记录——「从未发出」与「发出后失败」是两种不同的未完成原因，不得混成一种。
                per_aspect_search_traces.setdefault(aspect.aspect_id, []).append({
                    "call_ordinal": len(per_aspect_search_traces.get(aspect.aspect_id, ())),
                    "call_id": call.call_id,
                    "source_key": source_key,
                    "requested_scope": tuple(("node_id", str(n)) for n in source_node_ids),
                    "read_plan_id": plan.plan_id,
                    "read_round": _round,
                    "span_cursor": dict(_cursor) if _cursor else None,
                    "status": result.status,
                    "error_code": result.error_code,
                    "stop_reason": (result.data or {}).get("stop_reason"),
                })
                ledger.tool_calls += 1
                ledger.local_searches += 1
                valid_attempts += 1
                candidate_ids |= {str(e) for e in (result.evidence_ids or ())}
                _cursor_info = _read_cursor_from_tool_result(result)
                _emit("TREE_TOOL_RESULT", {
                    "aspect_id": aspect.aspect_id, "status": result.status,
                    "source_document_key": source_key.to_dict(),
                    "error_code": result.error_code, "tool_calls": ledger.tool_calls,
                    "read_plan_id": plan.plan_id, "read_round": _round,
                    "resumed_at_span_position": _cursor_info[
                        "resumed_at_span_position"],
                    # 检索轨迹必须能回答"这一步拿到了哪些父 Evidence"（不含文本，只记 id）。
                    "evidence_ids": list(result.evidence_ids or ())[:16],
                    "candidate_count": len((result.data or {}).get("candidates", ()) or ()),
                    # 表对象分支与正文候选**分开计数**：表材料的在场与否不得被并进
                    # candidate_count（那会让「只以表格存在的栏目」看起来像「取了正文 span」）。
                    "table_object_count": len(
                        (result.data or {}).get("table_objects", ()) or ()),
                    "table_material_count": len(
                        (result.data or {}).get("table_materials", ()) or ()),
                    "table_refusal_count": len(
                        (result.data or {}).get("table_refusals", ()) or ()),
                    # 候选的三条去路分开计数：少了第三项，被内容处置掉的候选会被读成"从未进过人口"。
                    "gap_count": len((result.data or {}).get("gaps", ()) or ()),
                    "content_disposition_count": len(
                        (result.data or {}).get("content_dispositions", ()) or ()),
                    # 截断读数：本调用真的读满了吗、还剩多少位置没读——**不得**靠 gap_count=0 反推。
                    "unread_span_positions": _cursor_info["unread_span_positions"],
                    "total_span_positions": _cursor_info["total_span_positions"],
                })
                if _round_total == 0 and _cursor_info["total_span_positions"]:
                    _round_total = _cursor_info["total_span_positions"]
                    # 每轮至少前进一个位置 ⇒ 轮数上界就是位置总数（由工具自报，不是拍的数）。
                    _hard_cap = max(1, _cursor_info["total_span_positions"])
                if result.status == "FATAL_ERROR":
                    declared_gaps.append({
                        "aspect_id": aspect.aspect_id,
                        "reason": "tree_structure_unavailable",
                        "detail": f"来源 {source_key.document_id} tree inspection 失败"
                                  f"（第 {_round + 1} 轮）："
                                  f"{result.error_code} {result.message}",
                        "source_document_key": source_key.to_dict(),
                        "read_plan_id": plan.plan_id,
                        "tool_status": result.status,
                        "error_code": result.error_code,
                        "retryable": False})
                    _stop_reason = "fatal_error"
                    break
                if result.status == "RETRYABLE_ERROR":
                    # 工具的**可重试失败**（到这里重试已耗尽）与 fatal 是两种不同的结果，
                    # 但两者都不能被读成「读完了」：这种返回既不带候选也不带续读位，照常往
                    # 下走时 `next_cursor is None` 会把一次失败冒充成 `complete=True`——正是
                    # 「把工具失败吞成普通材料缺口」的翻版（更糟：连缺口都不留）。据此在此
                    # 止步，并留下自己的 typed gap；顶层原因仍在闭集内，工具状态与错误码另开键。
                    declared_gaps.append({
                        "aspect_id": aspect.aspect_id,
                        "reason": "tree_structure_unavailable",
                        "detail": f"来源 {source_key.document_id} tree inspection 可重试失败"
                                  f"（第 {_round + 1} 轮，重试已耗尽）："
                                  f"{result.error_code} {result.message}",
                        "source_document_key": source_key.to_dict(),
                        "read_plan_id": plan.plan_id,
                        "tool_status": result.status,
                        "error_code": result.error_code,
                        "retryable": True})
                    _stop_reason = "tool_retryable_error"
                    break
                tree_materials, tree_gaps, skipped = _materials_from_tool_result(result)
                _round_gap_count += len(tree_gaps)
                traces.append(result)
                # §二 2.3：内容处置逐条留痕（**不是** gap，因此不进 `declared_gaps`）。
                # 条目自带 typed reason、原文摘录与 span/evidence 级 locator。
                _round_dispositions = _content_dispositions_from_tool_result(result)
                _round_disposition_count += len(_round_dispositions)
                for disposition in _round_dispositions:
                    _emit("TREE_CONTENT_DISPOSITION", {
                        "aspect_id": aspect.aspect_id,
                        "read_round": _round, **disposition})
                # 被有界上限挡下的候选是**真实存在过**的候选：如实记成 unattempted。
                # 截断型 `over_max_spans`（本批要续读的那一条）**不在此列**——它指的是
                # 「还没读到」，续读会去读它，把它同时记成 unattempted 会双重计数。
                _skipped_ids = _unattempted_from_skipped(skipped)
                unattempted_ids.extend(_skipped_ids)
                _round_unattempted.extend(_skipped_ids)
                for _mat in tree_materials:
                    if _mat.material_id in _round_seen_ids:
                        continue
                    _round_seen_ids.add(_mat.material_id)
                    _round_materials.append(_mat)
                    _attribute_tree_materials(source_key, (_mat,))
                # v6：同一次调用里的**表对象材料**（已定位块 → 放行 → 材料）一并归属。
                # 三类痕迹分开报：对象数（求解出了什么）、材料数（什么进了 Pack）、逐条拒绝
                # （为什么没进）——「有表但未获资格」不得被读成「语料里没有表」。
                table_mats, table_objs, table_refusals = \
                    _table_materials_from_tool_result(result)
                _attribute_table_materials(source_key, table_mats)
                _round_tables.extend(table_mats)
                for refusal in table_refusals:
                    _emit("TREE_TABLE_OBJECT_REFUSAL", {
                        "aspect_id": aspect.aspect_id,
                        "source_document_key": source_key.to_dict(),
                        "release_rule_version": refusal.get("release_rule_version"),
                        **refusal})
                if table_objs or table_mats or table_refusals:
                    _emit("TREE_TABLE_OBJECTS", {
                        "aspect_id": aspect.aspect_id,
                        "source_document_key": source_key.to_dict(),
                        "read_round": _round,
                        "table_object_count": len(table_objs),
                        "table_material_count": len(table_mats),
                        "table_material_ids": [m.material_id for m in table_mats][:16],
                        "refusal_count": len(table_refusals),
                        "refusal_reason_counts": _reason_histogram(table_refusals),
                        "table_object_material_version":
                            ((result.data or {}).get("versions") or {}).get(
                                "table_object_material_version"),
                        "tool_calls": ledger.tool_calls})
                # 顶层 `reason` 必须留在 `RUNTIME_GAP_REASONS` 封闭集合内（消费方/UI 只按它
                # 解释）；材料级 / 限流级的具体原因（各自另有封闭集合）放进**自己的键**，
                # 不覆盖顶层原因码——否则声明型 gap 会携带未登记原因，闭集失效。
                for raw_gap in tree_gaps:
                    declared_gaps.append({
                        "aspect_id": aspect.aspect_id, "reason": "tree_material_gaps",
                        "detail": f"span {raw_gap.get('span_id')}: {raw_gap.get('reason')}",
                        "source_document_key": source_key.to_dict(),
                        "read_round": _round,
                        "material_gap": dict(raw_gap)})
                for raw_skip in skipped:
                    if raw_skip.get("reason") == "over_max_spans":
                        # 截断是**续读依据**，不是终态：本条不带具名候选（只有续读读数），
                        # 把它当「被挡下的候选」会双重计数，还会把「还没读到」写成
                        # 「已判定为超界」。最终是否仍有未读位置由循环出口统一声明。
                        continue
                    declared_gaps.append({
                        "aspect_id": aspect.aspect_id,
                        "reason": "tree_material_bounded_out",
                        "detail": f"span {raw_skip.get('span_id')}: {raw_skip.get('reason')}",
                        "source_document_key": source_key.to_dict(),
                        "read_round": _round,
                        "skipped_material": dict(raw_skip)})

                _next_cursor = _cursor_info["next_cursor"]
                _round_unread = _cursor_info["unread_span_positions"]
                if _round_total == 0:
                    _round_total = _cursor_info["total_span_positions"]
                if _next_cursor is None:
                    complete = True
                    break
                _cursor_identity = (str(_next_cursor.get("node_id")),
                                    int(_next_cursor.get("span_index", -1)))
                if _cursor_identity in _seen_cursors:
                    # 工具报了不前进的游标（不该发生）：停下并如实记因，不得原地打转。
                    _stop_reason = "cursor_not_advancing"
                    break
                _seen_cursors.add(_cursor_identity)
                if _round + 1 >= _hard_cap:
                    _stop_reason = "rounds_cap"
                    break
                if not ledger.can_afford(tool_calls=1):
                    ledger.note_stop("BUDGET_EXHAUSTED")
                    _stop_reason = "budget_exhausted"
                    break
                _emit("TREE_READ_CONTINUATION", {
                    "aspect_id": aspect.aspect_id,
                    "source_document_key": source_key.to_dict(),
                    "read_plan_id": plan.plan_id,
                    "next_round": _round + 1,
                    "span_cursor": dict(_next_cursor),
                    "unread_span_positions": _round_unread,
                    "total_span_positions": _round_total,
                    "hard_cap_rounds": _hard_cap,
                    "tool_calls": ledger.tool_calls})
                _cursor = dict(_next_cursor)
                _round += 1

            # 工具失败（fatal / 可重试）**已在上面的轮内**留下了自己的 gap（带工具状态与错误码），
            # 这里不得再补一条：同一次止步留两条缺口，读回时会以为是两个独立问题。
            if not complete and _stop_reason not in ("fatal_error",
                                                     "tool_retryable_error"):
                # 仍有未读位置却停了：这是**真实的取证缺口**，按 top-level 闭集如实声明，
                # 具体的停止原因放进自己的键（预算 / 游标 / 轮数上界各自可辨）。
                _stop_gap_reason = _continuation_stop_gap_reason(_stop_reason)
                _emit("TREE_READ_CONTINUATION_STOPPED", {
                    "aspect_id": aspect.aspect_id,
                    "source_document_key": source_key.to_dict(),
                    "read_plan_id": plan.plan_id,
                    "reason": _stop_reason,
                    "rounds": _round + 1,
                    "unread_span_positions": _round_unread,
                    "total_span_positions": _round_total,
                    "tool_calls": ledger.tool_calls})
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id, "reason": _stop_gap_reason,
                    "detail": f"来源 {source_key.document_id} 读集未读完即停"
                              f"（读集 {len(source_node_ids)} 个 root、共 {_round_total} 个 "
                              f"span 位置，仍剩 {_round_unread} 个未读，"
                              f"停因 {_stop_reason}）",
                    "source_document_key": source_key.to_dict(),
                    "read_plan_id": plan.plan_id,
                    "continuation_stop_reason": _stop_reason,
                    "unread_span_positions": _round_unread,
                    "total_span_positions": _round_total,
                    "rounds": _round + 1})
            # 同读集的**真实结论**就此缓存：后来者不必再花一次调用去得到同一批 span，
            # 也不会得到另一批。缓存只在本 topic 的本次运行内存活，不进任何身份。
            read_set_cache.setdefault(_reuse_key, {
                "materials": tuple(_round_materials),
                "unattempted": tuple(_round_unattempted),
                "tables": tuple(_round_tables),
                "gap_count": _round_gap_count,
                "content_disposition_count": _round_disposition_count,
                "rounds": _round + 1,
                "unread_span_positions": _round_unread,
                "total_span_positions": _round_total,
                "complete": complete,
                # 停止原因进缓存：复用侧要能把「首读为什么没读完」原样交回（`rsr-2`）。
                "stop_reason": _stop_reason})
            if not _round_seen_ids:
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id, "reason": "tree_no_material",
                    "detail": f"来源 {source_key.document_id} 的树导航未产出可重切的"
                              f" OutlineSpan 材料（不升格整块 Evidence）",
                    "source_document_key": source_key.to_dict()})

        material_by_evidence = _index_materials_by_evidence(aspect_materials)

        # --- 7.1b 有界 Evidence fallback（§七）：同一工具、evidence selector、必须重切 ---
        # 触发条件：树侧**没有产出任何可用 span 材料**（结构不可用 / 显式跨引用 / 低置信
        # / 候选为空 都落在这里）。顺序固定：先走既有 bounded Evidence 路径（即下面的
        # focused follow-up `run_question`），从**真实结果**取被引用的父 Evidence ID，再用
        # `inspect_outline_materials` 的 evidence selector 重新定位并重切为 OutlineSpan；
        # 重切成功后才允许材料/事实进入 Pack，重切不出来就留显式 gap，绝不整块升格。
        # 触发原因取**锚**的导航决策：锚是本次 run 绑定的当前状态文档，focused follow-up 的
        # `run_question` 与随后的 evidence-selector 重切都只寻址锚的 session。别的成员的读集
        # 情况由它们各自的 `TREE_NAVIGATION` 事件与四臂台账如实呈现，不冒充本触发原因。
        anchor_decision = next((d for k, d, _ in navigated if k == anchor_key), None)
        fallback_allowed = not aspect_materials
        # 缺省档是「锚这一次**根本没有导航决策**」（`navigated` 里没有锚的条目），与四臂台账
        # 里的 `not_dispatched`（=没有把这一次派发出去）**不是同一件事**，故用独立取值
        # `anchor_not_navigated`，避免同一个字符串在同一个模块里承载两种含义。
        fallback_trigger = (
            (anchor_decision.fallback_reason or anchor_decision.status)
            if anchor_decision is not None else "anchor_not_navigated")
        fallback_attempted = False
        # `fba-1`（读集为空 ⇒ 兜底不得替这一栏造读集）：只有锚**真的开着读集**时，重切
        # 交出的材料才登记为本 aspect 的栏目材料。读集为空的三种 fallback 终态都走
        # `_decision(..., None, (), ranked)`（第四位就是读集），因此判据取 `read_node_ids`：
        # 它是"这一栏按标题树真的读到了什么"的唯一依据，比看触发字串更硬。
        # 锚这一轮**没有导航决策**（`anchor_not_navigated`）同样没有读集，一并不予归属。
        recut_read_set_open = bool(
            getattr(anchor_decision, "read_node_ids", ()) if anchor_decision is not None
            else ())
        recut_attributed: list[str] = []

        def _bounded_evidence_recut(need_id: str, evidence_ids: tuple,
                                    outcome_ref: str | None) -> bool:
            """把被引用的父 Evidence 重切成 span 材料；返回是否**给本 aspect 加进了栏目材料**。

            调用**显式寻址锚**（当前状态文档）：被引用的父 Evidence 来自锚 session 上的
            `run_question` 结果。若某个被引用的 id 其实属于别的文档，工具侧按四轴身份
            fail-closed（typed `SOURCE_NOT_BOUND`），**不得**把这一份的重切结果顶替过去。

            `fba-1`：重切交出的材料**只在锚真的开着读集时**才成为本 aspect 的栏目材料
            （`read_node_ids` 非空）。读集为空（三种 fallback 终态）或锚这一轮没有导航决策
            时，材料照旧进 Pack 全局材料集、照旧按四轴身份记入台账、照旧留事件与内容处置，
            但**不进** `aspect_materials`（⇒ 不进 `material_ids`／覆盖门／事实闭环），
            并另记一条 typed gap `evidence_fallback_unattributed`。
            返回值因此是"有没有加进栏目材料"，与"有没有交出材料"是两件事——后者看局部
            `recut` 与事件载荷里的 `recut_material_count`。
            """
            # `candidate_ids` 在这里是**增补**：重切用 evidence selector 真实返回的父 Evidence
            # 同样是「工具真实上报过的候选」，`not_found` 审计的替代/未尝试候选面才完整。
            # 缺 `nonlocal` 会让 `|=` 把名字变成嵌套函数的局部变量（未赋值即读 → 崩），
            # 这条路径此前没有真实走过，因此没有暴露。
            nonlocal valid_attempts, context_expansion, candidate_ids
            if not ledger.can_afford_optional(tool_calls=1):
                ledger.note_stop("BUDGET_EXHAUSTED")
                optional_blocked_by_aspect[aspect.aspect_id] = (
                    optional_blocked_by_aspect.get(aspect.aspect_id, 0) + 1)
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": "evidence_fallback_budget_exhausted",
                    "detail": "topic 级预算（含尚未发起的初次必读预留）不足以重切有界 Evidence "
                              "候选（不整块升格）",
                    "unmet_column_reason": "budget_blocked_dispatch",
                    "reserved_tool_calls": ledger.reserved_tool_calls})
                return False
            call = _tree_fallback_call(requirement, run_context, need_id=need_id,
                                       aspect_id=aspect.aspect_id,
                                       evidence_ids=evidence_ids,
                                       source_key=anchor_key)
            result = deps.registry.execute(call, route="DIRECT_EVIDENCE",
                                           run_id=run_context.run_id)
            ledger.tool_calls += 1
            ledger.local_searches += 1
            # 真实发生过：算一次有效尝试、算一次扩读，并把**这条真实结果对象**记为轨迹
            # （触达了哪些来源类由它自己的既有字段派生，失败结果天然派不出任何来源类）。
            valid_attempts += 1
            context_expansion = True
            traces.append(result)
            candidate_ids |= {str(e) for e in (result.evidence_ids or ())}
            if result.status == "FATAL_ERROR":
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id, "reason": "tree_structure_unavailable",
                    "detail": f"bounded Evidence 重切失败：{result.error_code} {result.message}"})
                return False
            recut, recut_gaps, recut_skipped = _materials_from_tool_result(result)
            unattempted_ids.extend(_unattempted_from_skipped(recut_skipped))
            added: list = []
            for material in recut:
                TS.verify_material_payload_ref(material.payload_ref, resolver)
                if material.material_id not in material_ids:
                    material_ids.add(material.material_id)
                    all_materials.append(material)
                added.append(material.material_id)
                # 重切寻址锚，材料也就归在锚名下（键与树路径同构）。
                material_ids_by_aspect_source.setdefault(
                    (aspect.aspect_id, SM.source_key_axes(anchor_key)), []).append(
                        material.material_id)
                if not recut_read_set_open:
                    # `fba-1`：读集为空 ⇒ 不作为本 aspect 的**栏目材料**。上面两步照旧
                    # （材料进 Pack 全局集与四轴台账：那是"这一次调用交出了什么"的事实），
                    # 这里只挡住"这一栏目的读集"这一件事。
                    continue
                aspect_materials.append(material)
                recut_attributed.append(material.material_id)
            for raw_gap in recut_gaps:
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id, "reason": "tree_material_gaps",
                    "detail": f"span {raw_gap.get('span_id')}: {raw_gap.get('reason')}",
                    "source_document_key": anchor_key.to_dict(),
                    "material_gap": dict(raw_gap)})
            for raw_skip in recut_skipped:
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": "tree_material_bounded_out",
                    "detail": f"span {raw_skip.get('span_id')}: {raw_skip.get('reason')}",
                    "source_document_key": anchor_key.to_dict(),
                    "skipped_material": dict(raw_skip)})
            # §二 2.3：与树导航路径同一条规则——内容处置逐条留痕，不并入 gap。
            recut_dispositions = _content_dispositions_from_tool_result(result)
            for disposition in recut_dispositions:
                _emit("TREE_CONTENT_DISPOSITION", {
                    "aspect_id": aspect.aspect_id, "via": "evidence_fallback", **disposition})
            # 原 ToolCall/ToolResult 与预算必须可追溯：记 upstream outcome ref、被请求的父
            # Evidence ID、工具状态/错误码与本次 tool_calls 计数。
            _emit("EVIDENCE_FALLBACK_RECUT", {
                "aspect_id": aspect.aspect_id, "trigger": fallback_trigger,
                "upstream_need_id": need_id, "upstream_outcome_ref": outcome_ref,
                "requested_evidence_ids": list(evidence_ids),
                "status": result.status, "error_code": result.error_code,
                "tool_calls": ledger.tool_calls,
                "recut_material_ids": added[:16], "recut_material_count": len(added),
                "gap_count": len(recut_gaps), "skipped_count": len(recut_skipped),
                "content_disposition_count": len(recut_dispositions),
                # `fba-1`：这一次重切交出的材料里，有几份登记成了本 aspect 的栏目材料。
                # 读集为空时恒为 0——事件里读得出「交出了 N 份、归到本栏目 0 份」。
                "attribution_policy": EVIDENCE_FALLBACK_ATTRIBUTION_VERSION,
                "attributed_material_count": len(recut_attributed),
                "read_set_open": recut_read_set_open})
            if not recut:
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": "evidence_fallback_no_span",
                    "detail": f"有界 Evidence 候选（{len(evidence_ids)} 个父 Evidence）"
                              f"未重切出任何合格 OutlineSpan 材料（不整块升格）："
                              f"{result.error_code or result.status}"})
            elif not recut_read_set_open:
                # 交出来了、但这一步的导航读集为空：兜底不得替这一栏造读集。材料不丢
                # （Pack 全局集 + 四轴台账 + 事件），只是**不成为这一栏目的材料**。
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": EVIDENCE_FALLBACK_UNATTRIBUTED_REASON,
                    "attribution_policy": EVIDENCE_FALLBACK_ATTRIBUTION_VERSION,
                    "trigger": fallback_trigger,
                    "recut_material_count": len(added),
                    "read_node_count": 0,
                    "detail": f"有界 Evidence 重切交出 {len(added)} 份 span 材料，但锚对这一栏的"
                              f"导航读集为空（{fallback_trigger}）：兜底不替本栏目造读集，"
                              f"这 {len(added)} 份材料不作为本 aspect 的栏目材料，"
                              f"本 aspect 按缺口处理（材料仍在 Pack 全局材料集与事件留痕里）"})
            # 返回值 = 本次重切**是否给本 aspect 加进了栏目材料**：加进了才需要重建
            # `material_by_evidence` 并再跑一次事实闭环（调用方据此决定）。
            return bool(recut_attributed)

        # --- 7.2 focused follow-up（按 gap 发起，复用 run_question）---------
        # 循环条件：本 aspect 尚未取得可用事实，且**上一次尝试带来了新的 cited evidence**
        # （新证据才可能改变闭环背书结果），且 topic 级预算与逐 aspect 回合数都还有余量。
        # 没有新信息就停，绝不靠"多试几次"凑数。
        facts: tuple[TS.SupportedFact, ...] = ()
        attempt_rounds = 0
        seen_evidence: set[str] = set()
        while True:
            ledger.wall_clock_ms = int((time.perf_counter() - t0) * 1000)
            # 事前按**真实可能消耗**预留（不是按 1 次预留、再事后按真实用量计费）：一次
            # `research_question` 在结构上最多用掉 `need_reserve` 那两笔，因此这里就按它判。
            # 两个后果都是要的：可选补件挤不掉尚未发起的初次必读（`can_afford_optional`），
            # 且「预留 1 次 → 事后越顶」这条通道不再是设计的一部分（越顶仍留痕，作为预留
            # 算错的证据；见下面的 `TOPIC_BUDGET_OVERSHOOT`）。
            if not ledger.can_afford_optional(
                    tool_calls=need_reserve.tool_calls,
                    llm_calls=need_reserve.llm_calls):
                ledger.note_stop("BUDGET_EXHAUSTED")
                optional_blocked_by_aspect[aspect.aspect_id] = (
                    optional_blocked_by_aspect.get(aspect.aspect_id, 0) + 1)
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": "focused_research_budget_exhausted",
                    "detail": f"topic 级累计预算不足以继续 focused follow-up："
                              f"本次 need 的结构上界为 {need_reserve.tool_calls} 次工具调用 / "
                              f"{need_reserve.llm_calls} 次 LLM 调用，"
                              f"已花 {ledger.tool_calls}/{ledger.policy.max_tool_calls_per_topic} "
                              f"次工具调用、另有 {ledger.reserved_tool_calls} 次初次必读待发起",
                    "unmet_column_reason": "budget_blocked_dispatch",
                    "need_reserve": need_reserve.to_dict(),
                    "reserved_tool_calls": ledger.reserved_tool_calls})
                break
            if attempt_rounds >= run_context.budget_policy.max_need_rounds_per_aspect:
                ledger.note_stop("BUDGET_ROUNDS")
                break
            attempt_rounds += 1
            follow_id = _join_id(need_id, f"follow{attempt_rounds}")
            attempted.append(follow_id)
            need = deps.information_need_builder.build(
                aspect, need_id=follow_id, company_id=run_context.company_id,
                section_id=run_context.section_id, report_as_of=run_context.report_as_of)
            if follow_up_focus is not None:
                # 边界③：把该 `FollowUpNeed` 的诉求**真正**绑定进本轮检索使用的那一个
                # `InformationNeed`（同一个对象随后交给 route_fn 与 research_question）。
                need = _bind_follow_up_focus(need, follow_up_focus, bound_need_id=follow_id)
                _emit("FOLLOW_UP_FOCUS_BOUND", {
                    "aspect_id": aspect.aspect_id, "bound_need_id": follow_id, **follow_up_focus})
            # 每轮 follow-up 只构造一次 RouteContext，并把它**同一个对象**交给
            # route_fn 与 research_question：两次构造会让「路由看到的上下文」与
            # 「研究跑起来的上下文」可能不同（非确定性 builder 下直接变成静默错配）。
            context = deps.route_context_builder()
            route_result = deps.route_fn(need, context)
            outcome = research_question(
                need=need, route_result=route_result, registry=deps.registry,
                llm=deps.llm, budget=need_budget, run_id=run_context.run_id,
                case_id=_join_id(run_context.case_id, follow_id),
                company_id=run_context.company_id,
                section_id=run_context.section_id, trace_enabled=True, context=context)
            ledger.charge_need(outcome.state)
            # 事后复核：事前只预留了 1 次 LLM 调用，本次 need 的真实用量可能更多，于是累计值
            # 可以越过 topic 上限。越界**必须留痕**（否则账本里会出现一个大于自身声明上限的
            # 读数而无人认领，见 `BUDGET_STOP_REASONS` 的 `BUDGET_OVERSHOOT`）。越界量有界
            # （≤ 单次 need 的真实调用数 − 1），且不影响本次已取得的材料/事实——它们是真调用
            # 换来的，不回滚；这里只把「这次检索是在预算已被越过的情况下结束的」记成事实。
            _llm_over = ledger.llm_calls - ledger.policy.max_llm_calls_per_topic
            _tool_over = ledger.tool_calls - ledger.policy.max_tool_calls_per_topic
            if _llm_over > 0 or _tool_over > 0:
                ledger.note_stop("BUDGET_OVERSHOOT")
                _emit("TOPIC_BUDGET_OVERSHOOT", {
                    "aspect_id": aspect.aspect_id, "need_id": follow_id,
                    "llm_calls": ledger.llm_calls,
                    "max_llm_calls_per_topic": ledger.policy.max_llm_calls_per_topic,
                    "llm_calls_over": max(0, _llm_over),
                    "tool_calls": ledger.tool_calls,
                    "max_tool_calls_per_topic": ledger.policy.max_tool_calls_per_topic,
                    "tool_calls_over": max(0, _tool_over),
                    "cause": "事前按 1 次预留、事后按真实用量计费的差额（非放宽上限）"})
            # §九：真实轨迹——本轮真的跑过一次 focused 检索、最后一次原子停止原因是什么、
            # 有没有真的走外部来源（只按 outcome 自己上报的用量与快照 id 记）。
            valid_attempts += 1
            last_stop_reason = getattr(outcome, "stop_reason", None)
            # 这次 focused need 的**真实状态对象**也是轨迹：它自带的 evidence_ids /
            # structured_refs / external_snapshot_ids 才是"触达过哪些来源类"的证据。
            traces.append(outcome.state)
            external_entry = _external_attempt_entry(follow_id, outcome.state)
            if external_entry is not None:
                external_attempted = True
                external_attempts.append(external_entry)
            # 只记录**真实** outcome 引用（含原子 completion_status），不合成 ref。
            outcome_ref: str | None = None
            try:
                outcome_ref = TS.outcome_to_ref(outcome)
                outcome_refs.append(outcome_ref)
            except TS.StateAdaptationError as e:
                _emit("OUTCOME_REF_REJECTED", {"need_id": follow_id, "error": str(e)[:200]})
            _emit("FOLLOW_UP_OUTCOME", {
                "aspect_id": aspect.aspect_id, "need_id": follow_id,
                "completion_status": getattr(outcome, "completion_status", None),
                "stop_reason": getattr(outcome, "stop_reason", None)})
            cited = {getattr(c, "evidence_id", None)
                     for c in (getattr(getattr(outcome, "answer", None), "citations", ()) or ())}
            cited.discard(None)
            fresh = cited - seen_evidence
            seen_evidence |= cited
            new_candidates, new_decisions, adopted, rejected = _adopt_facts(
                aspect, outcome, material_by_evidence, resolver=resolver)
            rejected_claims.extend(rejected)
            if not adopted and cited and fallback_allowed and not fallback_attempted:
                # 树侧一份材料都没有 → 用**真实 outcome 引用过的**父 Evidence 重切一次。
                fallback_attempted = True
                recut_ids = tuple(sorted(cited))[:_TREE_FALLBACK_MAX_EVIDENCE_IDS]
                if _bounded_evidence_recut(follow_id, recut_ids, outcome_ref):
                    material_by_evidence = _index_materials_by_evidence(aspect_materials)
                    new_candidates, new_decisions, adopted, rejected = _adopt_facts(
                        aspect, outcome, material_by_evidence, resolver=resolver)
                    rejected_claims.extend(rejected)
            for candidate in new_candidates:
                all_candidates.setdefault(candidate.candidate_id, candidate)
            for decision in new_decisions:
                all_decisions.setdefault(decision.decision_id, decision)
            if adopted:
                for fact in adopted:
                    all_facts.setdefault(fact.fact_id, fact)
                facts = tuple(all_facts[f.fact_id] for f in adopted)
                break
            if not fresh:
                _emit("FOLLOW_UP_NO_NEW_EVIDENCE", {
                    "aspect_id": aspect.aspect_id, "need_id": follow_id,
                    "cited_evidence": sorted(cited)[:5]})
                break
            # 有新证据但未形成闭环事实 → 允许下一轮（仍受上面两道预算门约束）。

        # --- 7.2c 确定性数值披露候选（`nd-1`）-------------------------------
        # **为什么需要这一步。** 此前唯一的候选构造器是 `_adopt_facts`，它的 `statement` 恒取
        # 模型命题文本（`claim.text`）。于是「材料已把分业务营收原文送进 Pack、却一条合格事实
        # 也没有」这类缺口发生在**候选生成之前**：不是 Contract、不是权威门、不是 Writer 面的
        # 问题。本步把本 aspect 已准入 Pack 的**精确定位正文**按句拆成**单值命题**，交给
        # **同一套**资格门（`_qualify_and_adopt` → `build_fact_candidate` /
        # `build_qualification_decision` / `build_supported_fact`）——不建立第二条通道。
        #
        # **边界（每条都不得放宽）**：
        #   * 覆盖面由 `ND.ASPECT_COVERAGE` 按**冻结 Contract 的 aspect_id 与字段标签**声明，
        #     表外栏目 `covered_fields` 恒空 ⇒ 本步整个不发生；
        #   * 期间**只**来自值自己那句话里的年份头（并列序列按位置配对），**不**调
        #     `extract_explicit_period`：那个函数回答「这句话披露了哪个期间」，对
        #     `2023-2025 年…分别为 A、B、C` 只会答 `2025`，是错的**值级**期间（`nd-1` 边界 4）；
        #   * 并列三年数字**必须**拆成单值命题：年份个数与值个数对不上、没有枚举开标记、单位
        #     不一致、占比没有分母、指标窗里出现增速动词 ⇒ 该值 **typed 跳过**，不铸候选；
        #   * 冲突值（同期间/字段/单位下两个不同原值）**两处都**铸候选、各判一条 `rejected`
        #     决定（`conflicting_value_for_same_identity`），不静默取一个；
        #   * 抽不到某个值时**不**因此形成 gap：这里只产出候选与事实，缺口仍由既有的
        #     `if not facts:` 与 §7.5 按「Contract 必需事实是否仍未取得」另行判定。
        nd_facts: list[TS.SupportedFact] = []
        if ND.covered_fields(aspect.aspect_id, aspect.required_fields):
            nd_memo: dict[str, str] = {}
            for material in sorted(aspect_materials, key=lambda m: str(m.material_id)):
                if str(getattr(material, "material_type", "")) == TABLE_MATERIAL_TYPE:
                    #: 表格类材料走**格级**授权（`table_cell_source`），不进这条正文轴。
                    continue
                body = ND.normalize_text(_material_span_text(material, resolver, nd_memo))
                if not body:
                    continue
                identities, conflicts, skipped = ND.extract_disclosures(
                    body, aspect_id=aspect.aspect_id,
                    required_fields=aspect.required_fields)
                for skip in skipped:
                    _emit("NUMERIC_DISCLOSURE_SKIPPED", {
                        "aspect_id": aspect.aspect_id,
                        "material_id": str(material.material_id),
                        "reason_code": skip.reason_code, "raw_number": skip.raw_number,
                        "detail": skip.detail,
                        "span": list(skip.value_span)})
                rejected_set = {id(c) for c in conflicts}
                for identity in tuple(identities) + tuple(conflicts):
                    resource = _citation_for(material)
                    candidate, decision, fact = _qualify_and_adopt(
                        aspect, statement=identity.statement, fact_type="fact",
                        period=identity.period_display, period_failure=None,
                        materials=(material,), refs=(resource,),
                        source_key="nd-" + str(material.material_id),
                        force_reject_reason=(ND.CONFLICT_REASON
                                             if id(identity) in rejected_set else None),
                        #: 逐值身份随事实一起进 Pack（`SupportedFact.value_identity`）。六个分量
                        #: **只**来自抽取器对这段正文的读数：目标原值（`amount_canonical`，不是
                        #: 「文本里最后一个数字」）、值级期间、字段标签、单位与单位类、业务作用域。
                        #: 写作侧据此判「这条事实授权的是哪个命题」，不再从 `statement` 那段
                        #: **逐字前缀**（年份头起到本值止，含前几年的值）反推。
                        value_identity=TS.ValueIdentity(
                            value_kind=identity.unit_kind, metric=identity.field_label,
                            unit=identity.unit, period=identity.period_display,
                            scope=identity.scope_text, amount_canonical=identity.raw_number))
                    all_candidates.setdefault(candidate.candidate_id, candidate)
                    all_decisions.setdefault(decision.decision_id, decision)
                    if fact is not None:
                        all_facts.setdefault(fact.fact_id, fact)
                        nd_facts.append(fact)
                    _emit("NUMERIC_DISCLOSURE_MINTED", {
                        "aspect_id": aspect.aspect_id,
                        "material_id": str(material.material_id),
                        "candidate_id": candidate.candidate_id,
                        "decision_id": decision.decision_id,
                        "verdict": decision.verdict,
                        "rejection_reason": decision.rejection_reason,
                        "fact_id": (fact.fact_id if fact is not None else None),
                        "field_label": identity.field_label,
                        "period_key": identity.period_key,
                        "unit": identity.unit, "raw_number": identity.raw_number,
                        "ordinal": identity.ordinal,
                        "span": list(identity.value_span)})
        if nd_facts:
            merged = {f.fact_id: f for f in facts}
            for fact in nd_facts:
                merged.setdefault(fact.fact_id, fact)
            facts = tuple(merged.values())

        if not facts:
            declared_gaps.append({
                "aspect_id": aspect.aspect_id,
                "reason": "focused_research_no_supported_fact",
                "detail": f"未取得 SUPPORTED 且被同 aspect 材料闭环背书的事实；"
                          f"被拒 claim：{list(rejected_claims[:5])}"})

        # --- 7.2b 表格槽位的材料可得性（§二 2.2）--------------------------
        # `content_role` 声明了表格槽位的 aspect，其表格只能由**表格类材料**供给。本 runtime
        # 有两条供给路径：①正文 span 材料里本就带 `table_context` 的（少）；②v6 表对象通道
        # （已定位块 → 放行 → `tom-1` 材料）。因此"零表格材料"现在是**可复算的结论**而不是
        # 结构性宿命：它要么说明本栏目的表格确实没被定位到，要么说明定位到了但未获资格 ——
        # 后一种情形的原因逐条留在 `TREE_TABLE_OBJECT_REFUSAL` 轨迹里，不在本 gap 里冒充。
        #
        # 它**只登记缺口的维度**，不改 aspect 终态：不得把"表格不可用"扩大成"整个 aspect
        # 没有任何可写内容"，其他正文材料的可得性、覆盖门与终态判定一律照旧。
        table_material_count = sum(
            1 for m in aspect_materials if m.material_type == TABLE_MATERIAL_TYPE)
        table_material_count += len(aspect_table_materials)
        if _requires_table_output(aspect) and table_material_count == 0:
            declared_gaps.append({
                "aspect_id": aspect.aspect_id,
                "reason": "tree_table_material_unavailable",
                "content_role": aspect.content_role,
                "table_material_count": 0,
                "material_count": len(aspect_materials),
                "table_object_material_count": len(aspect_table_materials),
                "detail": f"Contract content_role={aspect.content_role!r} 声明了表格槽位，"
                          f"但本轮 {len(aspect_materials)} 份正文材料与 "
                          f"{len(aspect_table_materials)} 份表对象材料里没有一份表格类材料"
                          f"（{TABLE_MATERIAL_TYPE}）：该栏目的表**没有在本轮被定位到并放行**。"
                          f"未放行的原因逐条见 `TREE_TABLE_OBJECT_REFUSAL` 轨迹；"
                          f"这是「本栏目本轮未取得合格表材料」，不是「语料里没有这张表」。"})

        # --- 7.3 逐 aspect 派生终态 ---------------------------------------
        gate_reason = _coverage_gate_reason(aspect, facts, tuple(aspect_materials))
        not_applicable_basis = _prove_not_applicable(aspect)
        not_found_audit: TS.NotFoundAudit | None = None
        not_found_blockers: tuple[str, ...] = ()
        unresolved_ids: list[str] = []
        if gate_reason is None and facts and aspect_materials:
            status = "covered"
        elif not_applicable_basis is not None:
            # 仅当 Contract 的确定性 applicability 规则能**证明**不适用时才成立
            # （当前冻结规则集合为空 → 本轮不可能走到这里，保留 blocked/partial）。
            status = "not_applicable"
        elif not aspect_materials and not facts:
            # 既没材料也没事实。**不得**径直写 not_found：先按真实轨迹派生审计并逐条验条件，
            # 条件不齐就如实保留 blocked（并把缺哪一条写进 gap，而不是含糊其辞）。
            not_found_audit, not_found_blockers = derive_not_found_audit(
                aspect=aspect,
                searched_need_ids=tuple(attempted),
                valid_attempt_count=valid_attempts,
                traces=tuple(traces),
                external_attempted=external_attempted,
                alternative_candidate_ids=tuple(candidate_ids),
                unattempted_candidate_ids=tuple(unattempted_ids),
                context_expansion_attempted=context_expansion,
                stop_reason=last_stop_reason,
                # 闭集成员判定（**不是**前缀）：`follow_up_budget_exhausted` 不以 `BUDGET`
                # 开头，前缀判定会把它漏掉，于是「因预算耗尽而没查完」可能被判成
                # 一次完成的检索。见 `BUDGET_STOP_REASONS`。
                budget_exhausted=bool(ledger.stop_reason
                                      and ledger.stop_reason in BUDGET_STOP_REASONS))
            per_aspect_not_found_audit[aspect.aspect_id] = not_found_audit
            if not_found_audit.qualified:
                status = "not_found"
            else:
                status = "blocked"
                # 「应有检索范围本身不可证」与「还有别的条件没齐」是两种止步理由，分开登记。
                scope_unproven = any("required_search_scope_unproven" in b
                                     for b in not_found_blockers)
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id,
                    "reason": ("required_search_scope_unproven" if scope_unproven
                               else "not_found_conditions_unmet"),
                    "detail": "不得写 not_found（条件未全部成立）：" + "；".join(not_found_blockers)})
        else:
            status = "partial"
        if status == "not_applicable":
            declared_gaps.append({
                "aspect_id": aspect.aspect_id, "reason": "aspect_not_applicable",
                "applicability_policy": not_applicable_basis,
                "detail": f"Contract applicability 规则确定性证明不适用：{not_applicable_basis}"})
        elif status == "not_found":
            # 文案只能是 NOT_FOUND_GAP_WORDING（不得写成"未披露/不存在"）。
            gap = TS.ResearchGap(
                unresolved_id=f"gap-tr-nf-{sha256_canonical({'a': aspect.aspect_id, 'nfa': not_found_audit.audit_id})[:24]}",
                aspect_ids=(aspect.aspect_id,), reason_code="not_found",
                detail=f"{NOT_FOUND_GAP_WORDING}: {aspect.requirement_text[:120]}",
                attempted_need_ids=tuple(attempted), blocking=False,
                impact=_impact_for(aspect),
                not_found_audit_id=not_found_audit.audit_id)
            aspect_gaps.append(gap)
            unresolved_ids.append(gap.unresolved_id)
            not_found_audits.append(not_found_audit)
        elif status != "covered":
            reason = gate_reason or ("tree_structure_unavailable" if not node_ids
                                     else "focused_research_no_supported_fact")
            gap = TS.ResearchGap(
                unresolved_id=f"gap-tr-{sha256_canonical({'a': aspect.aspect_id, 'r': reason})[:24]}",
                aspect_ids=(aspect.aspect_id,), reason_code="unresolved",
                detail=f"{reason}: {aspect.requirement_text[:120]}",
                attempted_need_ids=tuple(attempted), blocking=(status == "blocked"),
                impact=_impact_for(aspect))
            aspect_gaps.append(gap)
            unresolved_ids.append(gap.unresolved_id)

        # 本栏目未达 covered 时的**typed** 原因：只由真实轨迹派生，逐条独立，**不**塌成
        # `coverage_gate_not_met`。`budget_blocked_dispatch`（本轮没查）与
        # `material_found_but_unsupported`（取得≠支持）与 `dispatched_no_candidate`
        # （派发过、导航给不出候选）是三件不同的事，读回时必须分得开。
        unmet_column_reasons: tuple[str, ...] = ()
        column_dispatch_counts: dict[str, int] = {}
        if status != "covered":
            _column_dispatch: dict[str, str] = {}
            for (_aid, _axes), _reason in dispatched_without_call.items():
                if _aid == aspect.aspect_id:
                    _column_dispatch[f"{_axes[1]}@{_axes[3]}"] = _reason
            unmet_column_reasons, column_dispatch_counts = derive_column_unmet_reasons(
                materials=tuple(aspect_materials), facts=tuple(facts),
                dispatched_without_call=_column_dispatch,
                search_trace_count=len(per_aspect_search_traces.get(aspect.aspect_id, ())),
                requires_table_output=_requires_table_output(aspect),
                # `table_material_count` 取**两条供给路径之和**（正文里的表格类材料 + v6 表对象
                # 材料）：任一为真都说明该栏目的表格槽位本轮真的拿到了合格表材料。
                table_material_count=table_material_count,
                not_found_branch=bool(not aspect_materials and not facts),
                not_found_qualified=(not_found_audit.qualified
                                     if not_found_audit is not None else None),
                optional_budget_blocked=optional_blocked_by_aspect.get(
                    aspect.aspect_id, 0))
            declared_gaps.append({
                "aspect_id": aspect.aspect_id, "reason": "column_unmet",
                "column_status": status,
                "unmet_column_reasons": list(unmet_column_reasons),
                "dispatch_counts": column_dispatch_counts,
                "dispatched_without_call": _column_dispatch,
                "material_count": len(aspect_materials),
                "table_material_count": table_material_count,
                "table_object_material_count": len(aspect_table_materials),
                "fact_count": len(facts),
                "rule_version": MANDATORY_FIRST_READ_RULE_VERSION,
                "closed_set": list(UNMET_COLUMN_REASONS),
                "detail": ("本栏目未达 covered 的**逐条**原因（彼此不可互推）："
                           + ("、".join(unmet_column_reasons) if unmet_column_reasons
                              else "无——未命中 `UNMET_COLUMN_REASONS` 的任何一条")
                           + f"；派发去路 {column_dispatch_counts}，"
                             f"正文材料 {len(aspect_materials)} 份、"
                             f"表对象材料 {len(aspect_table_materials)} 份、"
                             f"事实 {len(facts)} 条")})

        result_obj = TS.AspectResearchResult(
            aspect_id=aspect.aspect_id, question_ids=(aspect.question_id,),
            requirement_snapshot=aspect, status=status,
            supported_fact_ids=tuple(f.fact_id for f in facts),
            # 这一列是**引用索引与覆盖/集合门的判据面**（`topic_store` 会拿它反查材料后跑
            # `_evaluate_coverage_rules` / `_validate_set_completeness`），因此只放能充当
            # 引用锚点的正文材料。表对象材料是 `reading_material=True` /
            # `numeric_authority=False` 的**阅读材料**：它们与正文材料同宿主块，混进这一列会让
            # 按 `authority.evidence_id` 建的引用索引在同一块上不唯一（
            # `citation_ambiguous_across_spans`）。它们的栏目归属走
            # `table_material_aspects` → RMD `aspect_ids` 这条独立轴，材料本身照旧进 Pack 全局集。
            material_ids=tuple(m.material_id for m in aspect_materials),
            attempted_need_ids=tuple(attempted),
            unresolved_ids=tuple(unresolved_ids),
            not_found_audit_id=(not_found_audit.audit_id if status == "not_found" else None))
        if status == "covered":
            sufficiency = TS.recompute_sufficiency(result_obj, facts, source_policy)
            if sufficiency is not None and not sufficiency.threshold_met:
                # sufficiency 未达标 ⇒ 不得 covered（降级为 partial，保留真实缺口）。
                suf_key = sha256_canonical({"a": aspect.aspect_id})[:20]
                gap = TS.ResearchGap(
                    unresolved_id=f"gap-tr-suf-{suf_key}",
                    aspect_ids=(aspect.aspect_id,), reason_code="unresolved",
                    detail=f"sufficiency 未达标（rule={sufficiency.rule}）",
                    attempted_need_ids=tuple(attempted), blocking=False,
                    impact=_impact_for(aspect))
                aspect_gaps.append(gap)
                unresolved_ids.append(gap.unresolved_id)
                declared_gaps.append({"aspect_id": aspect.aspect_id,
                                      "reason": "coverage_gate_not_met",
                                      "unmet_column_reasons": ["material_found_but_unsupported"],
                                      "dispatch_counts": column_dispatch_counts,
                                      "detail": "sufficiency 未达标"})
                status = "partial"
                result_obj = dataclasses.replace(
                    result_obj, status="partial", unresolved_ids=tuple(unresolved_ids))
            elif sufficiency is not None:
                result_obj = dataclasses.replace(
                    result_obj, sufficiency_assessment=sufficiency,
                    support_eligibility=TS.derive_support_eligibility(aspect))
            # sufficiency is None → 无 sufficiency 门，不携带多余自证。

        all_gaps.extend(aspect_gaps)
        for extra in aspect_gaps:
            declared_gaps.append({"aspect_id": aspect.aspect_id,
                                  "reason": "aspect_terminal_gap",
                                  "unresolved_id": extra.unresolved_id,
                                  "detail": extra.detail})
        if status == "partial" and gate_reason is not None:
            # 声明型 gap 一律带说明：消费方/UI 只按 `reason` 码解释，但**没有说明**的条目
            # 无法回答"这一步为什么止步"，等于静默止步。
            #
            # 覆盖门的原因码是**粗粒度**的（只回答"没过"），因此必须同时带上逐条 typed 原因：
            # 否则读回的人只能看到 `coverage_gate_not_met`，把「预算没让检索发生」「话对不上
            # 栏目」「表未获资格」读成同一件事——那正是本批要修掉的读法。
            declared_gaps.append({
                "aspect_id": aspect.aspect_id, "reason": gate_reason,
                "unmet_column_reasons": list(unmet_column_reasons),
                "dispatch_counts": column_dispatch_counts,
                "material_count": len(aspect_materials),
                "fact_count": len(facts),
                "detail": f"覆盖门未通过（{gate_reason}）：不得判 covered，如实保留 partial；"
                          f"逐条原因 "
                          f"{list(unmet_column_reasons) or '（未命中 UNMET_COLUMN_REASONS 任一条）'}"})
        # --- 7.5 Contract-required 仍未取得 → **另外**形成 research ContractGap/ResearchBlock --
        # 触发条件是「Contract 必需 aspect 未取得任何合格事实」，**不是**「有 candidate 被拒」：
        # rejection 的唯一留痕是它自己的 `FactQualificationDecision(rejected)`，绝不自动造 gap。
        if status not in ("covered", "not_applicable") and not facts:
            unmet = tuple(aspect.required_fields) or ("required_fact",)
            basis_fp = (aspect.canonical_fingerprint or aspect.contract_sha256
                        or sha256_canonical(aspect.to_dict()))
            reason_code = "not_found" if status == "not_found" else "blocked"
            proof = (f"aspect {aspect.aspect_id} 终态 {status}：未取得任何合格事实；"
                     f"被拒 claim 诊断：{list(rejected_claims[:5])}")
            gap = TS.ContractGap(
                gap_id=TS.derive_contract_gap_id(aspect.topic_id, aspect.aspect_id,
                                                 reason_code, unmet),
                topic_id=aspect.topic_id, aspect_id=aspect.aspect_id,
                question_ids=(aspect.question_id,),
                contract_version=aspect.contract_version,
                contract_sha256=aspect.contract_sha256,
                requirement_fingerprint=basis_fp, unmet_required_items=unmet,
                reason_code=reason_code,
                impact_scopes=tuple(s for s in aspect.impact_scope if s in TS.GAP_IMPACTS),
                required_unmet_proof=proof,
                rejected_candidate_ids=tuple(sorted(
                    d.candidate_id for d in all_decisions.values()
                    if d.verdict == "rejected"
                    and aspect.aspect_id in all_candidates[
                        d.candidate_id].aspect_ids)))
            all_contract_gaps.setdefault(gap.gap_id, gap)
            # 声明型 detail 只描述**研究侧事实**：不内联 Contract 字段名/requirement 原文
            # （那些是上游冻结文本，混进自由文案会让「不得判断不存在」的措辞门失真）。
            # 精确的未取得项在 typed `ContractGap.unmet_required_items` 里，不在自由文本里。
            declared_gaps.append({
                "aspect_id": aspect.aspect_id, "reason": "research_contract_gap",
                "gap_id": gap.gap_id, "unmet_required_item_count": len(unmet),
                "detail": f"Contract 必需项仍未取得（{reason_code}）：本 aspect 在本轮已纳入"
                          f"材料与检索范围内未取得任何合格事实"})
            if status == "blocked":
                block = TS.ResearchBlock(
                    block_id=TS.derive_research_block_id(
                        aspect.topic_id, aspect.aspect_id, "structure", unmet),
                    topic_id=aspect.topic_id, aspect_id=aspect.aspect_id,
                    question_ids=(aspect.question_id,),
                    contract_version=aspect.contract_version,
                    contract_sha256=aspect.contract_sha256,
                    requirement_fingerprint=basis_fp, unmet_required_items=unmet,
                    block_kind="structure", detail=proof, required_unmet_proof=proof)
                all_research_blocks.setdefault(block.block_id, block)
                declared_gaps.append({
                    "aspect_id": aspect.aspect_id, "reason": "research_block",
                    "block_id": block.block_id,
                    "detail": f"研究路径被硬性阻断（structure）：该 aspect 的取数路径在本轮"
                              f"不可用，Contract 必需项因而仍未取得"})
        aspect_results.append(result_obj)
        unfulfilled_reason_by_aspect[aspect.aspect_id] = (
            "budget_exhausted"
            if (ledger.stop_reason and ledger.stop_reason in BUDGET_STOP_REASONS)
            else "call_failed")

    # --- 7.4 装配 Pack（逐 aspect 结果 → 双轴状态 → finalize → commit）-----
    # 有 aspect 终态为 blocked（该 requirement 的证据路径取不到任何可用材料）时，流程轴
    # 必须带一个**已登记**的 hard stop reason：`derive_pack_status` 会把 stop_reason 透传为
    # `PackProcessStatus.hard_stop_reason`，而 `blocked` 状态缺它就是非法状态。
    # 预算停账优先级更高（`derive_pack_status` 先判 BUDGET 前缀），故只在未停账时补记。
    if ledger.stop_reason is None and any(
            r.status == "blocked" for r in aspect_results):
        ledger.note_stop("PATH_NOT_IMPLEMENTED")
    process, coverage, derivation = TS.derive_pack_status(
        tuple(a.aspect_id for a in requirement.aspects), tuple(aspect_results),
        stop_reason=ledger.stop_reason)
    # §0.3.3 逐来源四臂结果台账（检索后由**真实痕迹**派生；责任指纹取自检索前的同一次派生）。
    source_aspect_outcomes = derive_source_aspect_outcomes(
        sources=run_context.sources,
        responsibilities=aspect_source_responsibility,
        material_ids_by_aspect_source={
            pair: tuple(ids) for pair, ids in material_ids_by_aspect_source.items()},
        search_traces=per_aspect_search_traces,
        not_found_audits_by_aspect=per_aspect_not_found_audit,
        # 已派发、但没有发出任何调用的逐份结论（导航无候选 / 派发处预算不足）：
        # 少了它，「已派发但导航给不出候选」会被记成 `not_dispatched`（=从未派发）。
        dispatched_without_call=dispatched_without_call,
        unfulfilled_reason_by_aspect=unfulfilled_reason_by_aspect)
    # 责任在检索前定死这条不变量的事后核对。
    #
    # **这一步能查出的**：来源集在「责任派生」与「结果成文」之间被动过（重算台账指纹不等），
    # 或某条结果挂在了不属于本次责任台账的指纹上（结果倒填责任）。
    # **这一步查不出的**：「一开始就是看着结果写的责任」——只要输入轴没变，重算必然相等。
    # 后者不靠这里挡，靠两处更硬的东西：类型的字段闭集（`AspectSourceResponsibility` 在
    # 结构上就没有任何结果字段可写）与 `evals/test_aspect_source_responsibility.py` 的签名反例。
    recomputed = aspect_source_responsibility_fingerprint(tuple(
        row for aspect in requirement.aspects
        for row in derive_aspect_source_responsibility(
            aspect=aspect, sources=run_context.sources,
            current_state=SM.CURRENT_STATE_RESOLVED)))
    if recomputed != responsibility_fingerprint:
        raise TopicRuntimeError(
            "来源集在责任派生与结果成文之间发生了变化（fail-closed）："
            f"{responsibility_fingerprint!r} != {recomputed!r}")
    live_fingerprints = {r.fingerprint() for r in aspect_source_responsibility}
    for outcome in source_aspect_outcomes:
        if outcome.responsibility_fingerprint not in live_fingerprints:
            raise TopicRuntimeError(
                f"逐来源结果 {outcome.aspect_id!r}×"
                f"{outcome.source_document_key.document_id!r} 的责任指纹不属于本次责任台账")
    _emit("SOURCE_ASPECT_OUTCOMES_ASSEMBLED", {
        "responsibility_fingerprint": responsibility_fingerprint,
        "source_set_fingerprint": run_context.source_set_fingerprint(),
        "arm_counts": {arm: sum(1 for o in source_aspect_outcomes if o.arm == arm)
                       for arm in TS.SOURCE_ASPECT_OUTCOME_ARMS},
        "outcomes": [o.to_dict() for o in source_aspect_outcomes]})
    # 实测墙钟只作运行观测（落 trace 事件），不进 usage 快照 / Pack 内容身份。
    ledger.wall_clock_ms = int((time.perf_counter() - t0) * 1000)
    usage = ledger.to_usage_snapshot()
    # 装配依据可审计：审计条数/是否 qualified 与其策略版本、漏斗尝试项的来源与条数。
    _emit("NOT_FOUND_AUDITS_ASSEMBLED", {
        "policy_version": NOT_FOUND_AUDIT_POLICY_VERSION,
        "audit_count": len(not_found_audits),
        "qualified_count": sum(1 for a in not_found_audits if a.qualified),
        "audit_ids": [a.audit_id for a in not_found_audits],
        "blockers": [list(a.qualification_reasons) for a in not_found_audits]})
    _emit("EXTERNAL_FUNNEL_ASSEMBLED", {
        "schema_version": EXTERNAL_FUNNEL_SCHEMA_VERSION,
        "attempt_count": len(external_attempts),
        "need_ids": [e.get("need_id") for e in external_attempts],
        "snapshot_id_count": sum(len(e.get("external_snapshot_ids") or ())
                                for e in external_attempts)})
    # --- 7.4b 每份 material **恰一条** ResearchMaterialDisposition ------------------
    # 两轴（admission/retention）+ 来源校验结论 + typed 理由，全部确定性派生；**不含**
    # used/not_used（那是 Writer 侧 `WriterMaterialProcessingDisposition`）。构造走与后继装配
    # **同一个**公开口径 `build_material_dispositions`（P4 读门会用它重算比对）。
    material_dispositions = list(build_material_dispositions(
        materials=all_materials, aspect_results=aspect_results,
        candidates=tuple(all_candidates.values()),
        extra_material_aspects={mid: sorted(ids)
                                for mid, ids in table_material_aspects.items()}))

    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="",
        run_id=run_context.run_id, task_id=requirement.task_id,
        company_id=requirement.company_id, report_as_of=requirement.report_as_of,
        contract_version=requirement.contract_version,
        contract_fingerprint=requirement.contract_fingerprint,
        source_policy_version=requirement.source_policy_version,
        section_id=requirement.section_id, topic_id=requirement.topic_id,
        question_ids=requirement.question_ids,
        aspect_results=tuple(aspect_results),
        materials=tuple(all_materials), facts=tuple(all_facts.values()),
        material_dispositions=tuple(material_dispositions),
        fact_candidates=tuple(all_candidates.values()),
        fact_qualification_decisions=tuple(all_decisions.values()),
        external_facts=tuple(all_external_facts.values()),
        contract_gaps=tuple(all_contract_gaps.values()),
        research_blocks=tuple(all_research_blocks.values()),
        outcome_refs=tuple(dict.fromkeys(outcome_refs)),
        # §九：漏斗与 not-found 审计都**由真实轨迹派生**——本轮真的走过外部来源就必然产出
        # 内容寻址的漏斗快照，真的满足 not_found 条件就必然带一条 qualified 审计；两者都不
        # 再固定写死。`conflicts` 只在同一事实出现真实互斥时登记（本轮无互斥检出，如实为空）；
        # `uncertain_calls` 只在"请求已发出但响应未确认持久化"时登记（本轮无此情形，不为凑
        # 形状造记录）。
        external_funnel=external_funnel_snapshot(external_funnel_record(external_attempts)),
        conflicts=(), not_found_audits=tuple(not_found_audits),
        unresolved=tuple(all_gaps), usage=usage, uncertain_calls=(),
        process_status=process, coverage_status=coverage,
        status_derivation=derivation,
        dependency_fingerprint=requirement.dependency_fingerprint(),
        # §L4.2：本次运行纳入的**有序**来源集；责任台账取自检索前的那一次派生（指纹已在上方
        # 核对），逐来源结果取自**真实痕迹**（臂由发生过的事决定，不由责任记录决定）。
        source_set=run_context.sources,
        aspect_source_responsibility=aspect_source_responsibility,
        source_aspect_outcomes=source_aspect_outcomes,
        # 本轮未做跨来源互比（L4 之后的来源互比审计尚未在本运行时接线），如实为 None；
        # 不为凑形状造一条空审计。
        source_comparison_audit=None)
    pack = TS.finalize_pack(pack)
    # 后继模式：本轮的局部结果（只含被重研 aspect 的行）**不单独提交**——它缺 aspect，直接
    # 提交会让该 identity 的 current 变成读不到完整权威的 Pack。先在内存里与继承结果装配成
    # 完整后继，再**在这里**用**完整** requirement 提交（恰一次；先提交再合并会留下一个
    # 缺 aspect 的中间 current，读门与并发消费者都可能撞上它）。
    successor_of_pack_id: str | None = None
    if successor_of is not None:
        successor_of_pack_id = successor_of.pack_id
        pack = build_pack_successor(
            base=successor_of, new=pack, requirement=requirement,
            researched_aspect_ids=tuple(researched_aspect_ids or ()))

    versions = {
        "topic_runtime": TOPIC_RUNTIME_VERSION,
        "topic_pack_schema": TS.TOPIC_PACK_SCHEMA_VERSION,
        "budget_policy": run_context.budget_policy.version,
        "tree_material_resolver": TS.TREE_MATERIAL_RESOLVER_VERSION,
    }
    if hasattr(deps.navigation, "rule_version"):
        versions["navigation_rule"] = deps.navigation.rule_version
    if hasattr(deps.navigation, "profile_id"):
        versions["navigation_profile"] = deps.navigation.profile_id

    commit = deps.store.commit_pack(
        pack, requirement, resolver, deps.source_policy_resolver,
        deps.set_completeness_verifier, deps.set_enumeration_verifier)
    identity = pack.identity()
    current = deps.store.load_current(identity)
    _emit("PACK_COMMITTED", {
        "pack_id": pack.pack_id, "reused": commit.reused,
        "aspect_count": commit.aspect_count, "material_count": commit.material_count,
        "fact_count": commit.fact_count,
        "successor_of": successor_of_pack_id,
        "wall_clock_ms": ledger.wall_clock_ms})

    return TopicRuntimeResult(
        pack_id=commit.pack_id, identity=identity,
        current=(current is not None and current.pack_id == commit.pack_id),
        reused=bool(commit.reused),
        successor_of_pack_id=successor_of_pack_id,
        aspect_statuses=tuple((r.aspect_id, r.status) for r in pack.aspect_results),
        usage=pack.usage, checkpoint_ref=ledger.checkpoint_ref,
        trace_refs=tuple(trace_refs), gaps=tuple(declared_gaps),
        planned_need_ids=tuple(planned_need_ids), versions=versions,
        material_dispositions=tuple(pack.material_dispositions),
        fact_candidates=tuple(pack.fact_candidates),
        fact_qualification_decisions=tuple(pack.fact_qualification_decisions),
        external_facts=tuple(pack.external_facts),
        contract_gaps=tuple(pack.contract_gaps),
        research_blocks=tuple(pack.research_blocks))


# ---------------------------------------------------------------------------
# 7.6 FollowUpNeed 的 Harness 裁决与执行入口（§16.5 边界③）
# ---------------------------------------------------------------------------
#
# 边界③是一条**独立运行轨**：Writer 只发出 `FollowUpNeed`（不含结果、不含 Pack/material
# 引用），由 Harness 在这里逐条裁决。它复用既有 `InformationNeedBuilder` / `route_fn` /
# `research_question` / `run_topic_requirement` / 同一个 Store 适配器 —— 不新建第二套
# Router、ToolRegistry、Retriever、Store 或 LLM runtime；旧 Pack 与旧 Draft 一律不回写。

#: `FollowUpDecision` 的裁决规则版本（只失效 follow-up 自身，不进任何 Pack 内容身份）。
#: `fud-2`（M930-3 §三 F）：执行语义从「提交只含一条 aspect 的窄 Pack」改为「装配并提交
#: **完整** aspect 集的确定性**后继**」，并新增两条 typed 拒绝码；裁决身份随之改变。
FOLLOW_UP_RULES_VERSION = "fud-2"

#: typed 拒绝码（封闭集合；不得塞进泛化字符串，也不得与 gap/rejection audit 混用）。
FOLLOW_UP_REJECTION_REASONS = (
    "target_requirement_unresolved",
    "cross_section_target",
    "cross_topic_target",
    "question_not_in_requirement",
    "aspect_unresolved",
    "aspect_ambiguous",
    "question_mismatch",
    "identity_mismatch",
    "scope_not_authorized",
    "unknown_source_class",
    "source_policy_unresolved",
    "budget_exhausted",
    # §三 F：后继装配的前置不成立（没有可继承的完整 current Pack / 它不满足冻结 requirement）。
    "successor_base_missing",
    "successor_base_incomplete",
)


def topic_requirement_id(requirement: TS.TopicResearchRequirement) -> str:
    """`FollowUpNeed.target_requirement_id` 的唯一确定性构造口径。

    `TopicResearchRequirement` 本身没有 `requirement_id` 字段，故由 (section_id, topic_id)
    这一对**在 requirement 内唯一且冻结**的身份确定性派生；Writer 与 Harness 必须用同一函数。
    """
    if not isinstance(requirement, TS.TopicResearchRequirement):
        raise TopicRuntimeError("topic_requirement_id 需要 TopicResearchRequirement")
    return f"{requirement.section_id}::{requirement.topic_id}"


def _authorized_scope(aspect: TS.TopicAspectRequirementSnapshot) -> tuple[str, ...]:
    """由冻结 aspect 确定性派生「Contract 授权范围」（不含任何自由文本/公司专用规则）。

    授权标识 = aspect/topic/question 身份 + 冻结 `impact_scope`。这不是新 schema：全部元素
    都取自冻结投影，仅用于判断 Writer 的 ask 是否落在该 aspect 的授权边界内。
    """
    return tuple(sorted({aspect.aspect_id, aspect.topic_id, aspect.question_id,
                         *aspect.impact_scope}))


def _follow_up_decision_id(need_id: str, verdict: str, reason: str) -> str:
    return TS.derive_follow_up_decision_id(need_id, verdict, reason, FOLLOW_UP_RULES_VERSION)


@dataclass(frozen=True)
class FollowUpExecutionResult:
    """一次 follow-up 裁决/执行批次的结果（含逐条 `FollowUpDecision`）。"""

    needs: tuple[TS.FollowUpNeed, ...]
    decisions: tuple[TS.FollowUpDecision, ...]
    new_pack_ids: tuple[str, ...]
    trace_refs: tuple[str, ...]
    usage: TS.TopicUsageSnapshot
    rules_version: str = FOLLOW_UP_RULES_VERSION
    follow_up_run_id: str = ""

    def __post_init__(self) -> None:
        # 每条输入 need **恰一条**裁决；缺失、重复或额外都 fail-closed。
        need_ids = [n.need_id for n in self.needs]
        decision_ids = [d.need_id for d in self.decisions]
        if len(set(need_ids)) != len(need_ids):
            raise TopicRuntimeError("FollowUpExecutionResult 输入 need 重复")
        if sorted(need_ids) != sorted(decision_ids):
            raise TopicRuntimeError(
                "FollowUpExecutionResult 必须对每条 need 恰给一条 FollowUpDecision"
                "（缺失/重复/额外均 fail-closed）")
        packs = tuple(d.new_pack_id for d in self.decisions if d.executed)
        if packs != self.new_pack_ids:
            raise TopicRuntimeError(
                "FollowUpExecutionResult.new_pack_ids 必须与已执行裁决一一对应")

    def accepted_need_ids(self) -> tuple[str, ...]:
        return tuple(d.need_id for d in self.decisions if d.verdict == "accepted")

    def reduced_need_ids(self) -> tuple[str, ...]:
        return tuple(d.need_id for d in self.decisions if d.verdict == "reduced")

    def rejected_need_ids(self) -> tuple[str, ...]:
        return tuple(d.need_id for d in self.decisions if d.verdict == "rejected")

    def to_dict(self) -> dict:
        return {
            "follow_up_run_id": self.follow_up_run_id,
            "rules_version": self.rules_version,
            "needs": [n.to_dict() for n in self.needs],
            "decisions": [d.to_dict() for d in self.decisions],
            "accepted_need_ids": list(self.accepted_need_ids()),
            "reduced_need_ids": list(self.reduced_need_ids()),
            "rejected_need_ids": list(self.rejected_need_ids()),
            "new_pack_ids": list(self.new_pack_ids),
            "trace_refs": list(self.trace_refs),
            "usage": self.usage.to_dict() if hasattr(self.usage, "to_dict") else self.usage,
        }


def _reject_follow_up(need: TS.FollowUpNeed, reason: str, proof: str,
                      requirement: TS.TopicResearchRequirement | None,
                      aspect: TS.TopicAspectRequirementSnapshot | None,
                      trace_refs: tuple[str, ...] = ()) -> TS.FollowUpDecision:
    if reason not in FOLLOW_UP_REJECTION_REASONS:
        raise TopicRuntimeError(f"未登记的 follow-up 拒绝码: {reason!r}")
    return TS.FollowUpDecision(
        decision_id=_follow_up_decision_id(need.need_id, "rejected", reason),
        need_id=need.need_id, verdict="rejected", rules_version=FOLLOW_UP_RULES_VERSION,
        authorized_requirement_id=(topic_requirement_id(requirement) if requirement else None),
        resolved_topic_id=(requirement.topic_id if requirement else None),
        resolved_aspect_id=(aspect.aspect_id if aspect else None),
        reason=reason, executed=False, new_pack_id=None, trace_refs=trace_refs)


def _resolve_follow_up_target(
        need: TS.FollowUpNeed,
        requirements_by_id: dict[str, TS.TopicResearchRequirement],
        run_context: TopicRunContext
) -> tuple[TS.TopicResearchRequirement | None,
           TS.TopicAspectRequirementSnapshot | None,
           TS.FollowUpDecision | None]:
    """解析唯一目标（requirement + aspect）；任一不唯一/越界即 typed reject。"""
    requirement = requirements_by_id.get(need.target_requirement_id)
    if requirement is None:
        return None, None, _reject_follow_up(need, "target_requirement_unresolved",
                                             "target_requirement_id 不在本次可服务集合内",
                                             None, None)
    if requirement.section_id != need.section_id:
        return requirement, None, _reject_follow_up(
            need, "cross_section_target",
            f"need.section_id={need.section_id!r} 与目标 requirement "
            f"section_id={requirement.section_id!r} 不一致", requirement, None)
    if requirement.topic_id != need.topic_id:
        return requirement, None, _reject_follow_up(
            need, "cross_topic_target",
            f"need.topic_id={need.topic_id!r} 与目标 requirement topic_id="
            f"{requirement.topic_id!r} 不一致", requirement, None)
    if need.question_id not in requirement.question_ids:
        return requirement, None, _reject_follow_up(
            need, "question_not_in_requirement",
            f"need.question_id={need.question_id!r} 不在目标 requirement 的 "
            f"{list(requirement.question_ids)!r} 内", requirement, None)
    matches = [a for a in requirement.aspects if a.aspect_id == need.aspect_id]
    if not matches:
        return requirement, None, _reject_follow_up(
            need, "aspect_unresolved",
            f"aspect_id={need.aspect_id!r} 不在目标 requirement 内", requirement, None)
    if len(matches) > 1:
        return requirement, None, _reject_follow_up(
            need, "aspect_ambiguous",
            f"aspect_id={need.aspect_id!r} 在目标 requirement 内命中 {len(matches)} 次",
            requirement, None)
    aspect = matches[0]
    if aspect.question_id != need.question_id:
        return requirement, aspect, _reject_follow_up(
            need, "question_mismatch",
            f"need.question_id={need.question_id!r} 与 aspect.question_id="
            f"{aspect.question_id!r} 不一致", requirement, aspect)
    try:
        _require_identity(requirement, run_context)
    except TopicRuntimeError as exc:
        return requirement, aspect, _reject_follow_up(
            need, "identity_mismatch", str(exc), requirement, aspect)
    return requirement, aspect, None


def run_follow_up_needs(*, follow_up_needs: tuple[TS.FollowUpNeed, ...],
                        requirements_by_id: dict[str, TS.TopicResearchRequirement],
                        run_context: TopicRunContext,
                        dependencies: TopicRuntimeDependencies) -> FollowUpExecutionResult:
    """逐条裁决 Writer 发出的 `FollowUpNeed`，通过的经**既有**研究链形成新 Pack。

    fail-closed 不变量（任一不满足即 typed reject 且**不执行**，绝不降级执行）：

    - 目标身份必须**唯一**：requirement / aspect / question 三层都必须精确命中且互不跨
      requirement（缺失、歧义、跨段、跨 topic 一律拒绝）；
    - `section_draft_revision` 只作**回指记录**：本入口不读也不写任何 Draft，旧 Draft 不回写；
    - 授权范围必须落在冻结 aspect 的 Contract 授权边界内；
    - `expected_source_class` 必须属于封闭来源类集合（`TS.SOURCE_CLASSES`）；
    - SourcePolicy 必须由既有 resolver **真实解析并身份闭合**（dangling / 版本不符即拒绝）；
    - 预算必须经既有 `TopicBudgetState.can_afford` 放行，且已花用量经 `absorb` 累计，
      任一维度超限后剩余 need 直接拒绝并 `note_stop`。

    通过的 need 走既有 `InformationNeedBuilder` → 既有 `route_fn` / `research_question` →
    既有 `run_topic_requirement`（**收窄到该单条 aspect** 的 requirement）+ 既有 Store 适配器
    提交 append-only 新 Pack。本函数不新建任何第二套 runtime，也不触碰旧 Pack。
    """
    if not isinstance(run_context, TopicRunContext):
        raise TopicRuntimeError("run_follow_up_needs 需要 TopicRunContext")
    if not isinstance(dependencies, TopicRuntimeDependencies):
        raise TopicRuntimeError("run_follow_up_needs 需要 TopicRuntimeDependencies")
    needs = tuple(follow_up_needs)
    if not needs:
        raise TopicRuntimeError("run_follow_up_needs 至少需要一条 FollowUpNeed")
    for need in needs:
        if not isinstance(need, TS.FollowUpNeed):
            raise TopicRuntimeError("run_follow_up_needs 只接受 FollowUpNeed")
    seen: set[str] = set()
    for need in needs:
        if need.need_id in seen:
            raise TopicRuntimeError(f"follow_up_needs 含重复 need_id: {need.need_id!r}")
        seen.add(need.need_id)
    if not isinstance(requirements_by_id, dict):
        raise TopicRuntimeError("requirements_by_id 必须为 dict")
    # 一个批次 = 一个 Writer 调用 = 一份 (section, draft revision, writer identity)。混合批次
    # 会让 run 行的身份含糊，故直接 fail-closed（不猜、不取第一条）。
    for name in ("section_id", "section_draft_revision", "writer_identity"):
        distinct = {getattr(n, name) for n in needs}
        if len(distinct) != 1:
            raise TopicRuntimeError(
                f"同一 follow-up 批次内 {name} 必须唯一，得到 {sorted(distinct)!r}"
                "（fail-closed：混合批次的身份不可信）")

    deps = dependencies
    ledger = TopicBudgetState(policy=run_context.budget_policy)
    trace_refs: list[str] = []
    decisions: list[TS.FollowUpDecision] = []
    new_pack_ids: list[str] = []
    # 本次执行的**真实身份**必须出现在它自己的 trace ref 里。同一 task 上的两次有界执行（不同
    # need 集）此前会得到逐字相同的 ref（旧形 `<run_id>:<事件>:<本调用内序号>`，两次都是
    # `…:FOLLOW_UP_EXECUTED:0`），产物里分不出「1 次」还是「2 次」，也就谈不上「各自指向真实
    # run / 需求 / 后继 Pack」。身份量在这里**先**由 need 集确定性派生（`derive_follow_up_run_id`
    # 只吃 run_id + need_ids + 规则版本，不吃 trace，故不构成循环），并与末尾落库的那一行是
    # 同一个值；它**不**进任何内容指纹（§4.6 明确把 operational run identity 排除在版本输入外）。
    need_ids = tuple(n.need_id for n in needs)
    follow_up_run_id = TS.derive_follow_up_run_id(
        run_context.run_id, need_ids, FOLLOW_UP_RULES_VERSION)

    def _emit(event_type: str, payload: dict) -> None:
        trace_refs.append(
            f"{run_context.run_id}:{event_type}:{follow_up_run_id}:{len(trace_refs)}")
        deps.trace_sink.emit(event_type, payload)

    for need in needs:
        requirement, aspect, rejected = _resolve_follow_up_target(
            need, requirements_by_id, run_context)
        if rejected is not None:
            decisions.append(rejected)
            _emit("FOLLOW_UP_REJECTED", {
                "need_id": need.need_id, "reason": rejected.reason,
                "verdict": rejected.verdict})
            continue

        authorized = set(_authorized_scope(aspect))
        unauthorized = tuple(sorted(set(need.contract_authorized_scope) - authorized))
        if not need.contract_authorized_scope or unauthorized:
            decisions.append(_reject_follow_up(
                need, "scope_not_authorized",
                f"need.contract_authorized_scope={list(need.contract_authorized_scope)!r} "
                f"越出该 aspect 的 Contract 授权范围；越界项={list(unauthorized)!r}",
                requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "scope_not_authorized"})
            continue

        if need.expected_source_class not in TS.SOURCE_CLASSES:
            decisions.append(_reject_follow_up(
                need, "unknown_source_class",
                f"expected_source_class={need.expected_source_class!r} 不在封闭来源类 "
                f"{list(TS.SOURCE_CLASSES)!r} 内", requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "unknown_source_class"})
            continue

        try:
            verified_policy = TS.verify_frozen_source_policy(
                aspect.source_policy_ref, deps.source_policy_resolver.resolve(
                    aspect.source_policy_ref))
        except TS.SchemaValidationError as exc:
            decisions.append(_reject_follow_up(
                need, "source_policy_unresolved", str(exc), requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "source_policy_unresolved"})
            continue
        if verified_policy.policy_version != requirement.source_policy_version:
            decisions.append(_reject_follow_up(
                need, "source_policy_unresolved",
                f"目标 requirement.source_policy_version="
                f"{requirement.source_policy_version!r} 与冻结政策 "
                f"{verified_policy.policy_version!r} 不一致", requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "source_policy_unresolved"})
            continue

        if not ledger.can_afford(tool_calls=1, llm_calls=1, elapsed_ms=0):
            ledger.note_stop("follow_up_budget_exhausted")
            decisions.append(_reject_follow_up(
                need, "budget_exhausted",
                "既有 TopicBudgetState.can_afford 拒绝：follow-up 批次预算已耗尽",
                requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "budget_exhausted"})
            continue

        # 后继语义（§三 F）：先确认该 identity 有一份**完整**的 current Pack 可以继承。
        # 缺它就没有「其余 aspect 从哪来」的答案——不得退化成「只提交被重研的那一条 aspect」：
        # 那样提交出来的 Pack 缺 aspect，同一 identity 的 current 会立刻被 P4 读门以
        # `aspect_set_mismatch` / `question_set_mismatch` block，等于把权威读没了。
        base_pack = deps.store.load_current(requirement_pack_identity(requirement))
        if base_pack is None:
            decisions.append(_reject_follow_up(
                need, "successor_base_missing",
                "该 requirement 的 identity 在 Store 里没有 current Pack：没有可继承的完整 "
                "aspect 集，follow-up 不得只提交被重研的那一条 aspect", requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "successor_base_missing"})
            continue
        if (set(r.aspect_id for r in base_pack.aspect_results) != set(requirement.aspect_ids())
                or set(base_pack.question_ids) != set(requirement.question_ids)
                or base_pack.dependency_fingerprint != requirement.dependency_fingerprint()):
            decisions.append(_reject_follow_up(
                need, "successor_base_incomplete",
                "current Pack 的 aspect/question 集合或依赖指纹与冻结 requirement 不相等："
                "继承来源本身不完整，不得据此装配后继", requirement, aspect))
            _emit("FOLLOW_UP_REJECTED", {"need_id": need.need_id,
                                          "reason": "successor_base_incomplete"})
            continue

        # 收窄到**重研集合**：只重研该 need 请求的那一条 aspect，其余继承 base；提交的是
        # **完整** aspect 集的后继，且用的仍是冻结的**完整** requirement。
        grant_reason = (f"need {need.need_id} 目标唯一（requirement/aspect/question 精确命中）"
                        f"且授权范围、来源类、SourcePolicy 与预算均通过")
        focus = build_follow_up_focus(need, grant_reason=grant_reason)
        need_ledger = TopicBudgetState(policy=run_context.budget_policy)
        sub_deps = dataclasses.replace(deps, budget_state=need_ledger)
        result = run_topic_requirement(
            requirement, run_context=run_context, dependencies=sub_deps,
            researched_aspect_ids=(aspect.aspect_id,), successor_of=base_pack,
            follow_up_focus=focus)
        ledger.absorb(need_ledger)
        new_pack_ids.append(result.pack_id)
        statuses = dict(result.aspect_statuses)
        status = statuses.get(aspect.aspect_id, "unknown")
        verdict = "accepted" if status == "covered" else "reduced"
        reason = (f"aspect {aspect.aspect_id} status={status}；新 Pack {result.pack_id} 是 "
                  f"{base_pack.pack_id} 的后继（{PACK_SUCCESSOR_ASSEMBLY_VERSION}：其余 "
                  f"{len(result.aspect_statuses) - 1} 条 aspect 确定性继承；旧 Pack 未回写）")
        decisions.append(TS.FollowUpDecision(
            decision_id=_follow_up_decision_id(need.need_id, verdict, reason),
            need_id=need.need_id, verdict=verdict,
            rules_version=FOLLOW_UP_RULES_VERSION,
            authorized_requirement_id=topic_requirement_id(requirement),
            resolved_topic_id=requirement.topic_id,
            resolved_aspect_id=aspect.aspect_id,
            reason=reason, executed=True, new_pack_id=result.pack_id,
            trace_refs=tuple(result.trace_refs)))
        _emit("FOLLOW_UP_EXECUTED", {
            "need_id": need.need_id, "verdict": verdict, "new_pack_id": result.pack_id,
            "successor_of_pack_id": base_pack.pack_id,
            "assembly_version": PACK_SUCCESSOR_ASSEMBLY_VERSION,
            "aspect_status": status,
            "candidate_count": len(result.fact_candidates),
            "material_count": len(result.material_dispositions)})

    follow_up_run = TS.FollowUpRun(
        follow_up_run_id=follow_up_run_id,
        run_id=run_context.run_id, task_id=run_context.task_id,
        section_id=needs[0].section_id,
        section_draft_revision=needs[0].section_draft_revision,
        writer_identity=needs[0].writer_identity,
        rules_version=FOLLOW_UP_RULES_VERSION,
        need_ids=need_ids, decision_ids=tuple(d.decision_id for d in decisions),
        new_pack_ids=tuple(new_pack_ids), trace_refs=tuple(trace_refs))
    # 边界③：need 行与 run 行**只追加**；已存在的 need/run 行不被覆盖（Store 侧幂等）。
    deps.store.commit_follow_up_run(follow_up_run, needs, tuple(decisions))
    return FollowUpExecutionResult(
        needs=needs, decisions=tuple(decisions),
        new_pack_ids=tuple(new_pack_ids), trace_refs=tuple(trace_refs),
        usage=ledger.to_usage_snapshot(),
        follow_up_run_id=follow_up_run.follow_up_run_id)


# ---------------------------------------------------------------------------
# 8. run-scoped Store 适配器（只薄封装既有函数）
# ---------------------------------------------------------------------------

@dataclass
class SqliteTopicStoreAdapter:
    """薄封装既有 `harness.topic_store` 的 run-scoped Store 适配器。

    它**不**新建 schema、不新建 Table、不发明第二 Store：只把既有
    `init_topic_store` / `commit_pack` / `load_current_pack` 绑定到一个确定的库路径。
    """

    path: Any

    def init(self) -> None:
        from harness import topic_store as Store
        Store.init_topic_store(self.path)

    @property
    def db_path(self) -> Any:
        return self.path

    def commit_pack(self, pack, requirement, resolver, source_policy_resolver,
                    set_completeness_verifier, set_enumeration_verifier):
        from harness import topic_store as Store
        return Store.commit_pack(
            pack, requirement, resolver, source_policy_resolver,
            set_completeness_verifier, set_enumeration_verifier)

    def load_current(self, identity: TS.PackIdentity) -> TS.TopicResearchPack | None:
        from harness import topic_store as Store
        load = Store.load_current_pack(identity)
        return load.pack if load.available else None

    def commit_follow_up_run(self, follow_up_run: TS.FollowUpRun,
                             needs: tuple[TS.FollowUpNeed, ...],
                             decisions: tuple[TS.FollowUpDecision, ...]) -> Any:
        from harness import topic_store as Store
        return Store.commit_follow_up_run(follow_up_run, needs, decisions)

    def load_follow_up_needs(self, run_id: str) -> tuple[TS.FollowUpNeed, ...]:
        from harness import topic_store as Store
        return Store.load_follow_up_needs(run_id)

    def load_follow_up_run(self, follow_up_run_id: str) -> TS.FollowUpRun | None:
        from harness import topic_store as Store
        return Store.load_follow_up_run(follow_up_run_id)


__all__ = [
    "BudgetForNeed",
    "CombinedPayloadResolver",
    "DocumentIdentity",
    "EXTERNAL_FUNNEL_PRODUCER_VERSION",
    "EXTERNAL_FUNNEL_SCHEMA_VERSION",
    "FOLLOW_UP_REJECTION_REASONS",
    "FOLLOW_UP_RULES_VERSION",
    "FollowUpExecutionResult",
    "IndexedTreeNavigation",
    "InformationNeedBuilder",
    "NOT_FOUND_AUDIT_POLICY_VERSION",
    "PROVABLE_NOT_APPLICABLE_RULES",
    "RUNTIME_GAP_REASONS",
    "TABLE_CONTENT_ROLE_MARKER",
    "TABLE_MATERIAL_TYPE",
    "external_funnel_payload_bytes",
    "external_funnel_record",
    "external_funnel_snapshot",
    "ResearchBudgetPolicy",
    "RunScopedTopicStore",
    "SqliteTopicStoreAdapter",
    "TOPIC_RUNTIME_VERSION",
    "TopicBudgetState",
    "TopicRunContext",
    "TopicRuntimeDependencies",
    "TopicRuntimeError",
    "TopicRuntimeResult",
    "TraceSink",
    "TreeNavigationProvider",
    "run_follow_up_needs",
    "run_topic_requirement",
    "topic_requirement_id",
]

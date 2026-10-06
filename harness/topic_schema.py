"""R1-B：唯一 TopicResearchPack typed schema + AspectV2 冻结投影 + 三类权威/locator 联合
+ 双轴 Pack 状态 + ResearchOutcome 兼容输入边界。

纯声明式模块：不 I/O、不写库、不调 LLM / Router / ToolRegistry / 网络。它只被
``harness/topic_store.py`` / ``harness/topic_checkpoint.py`` / ``harness/topic_store_cli.py``
与离线测试引用。

设计要点（R1B_IMPLEMENTATION_PLAN.md + 编码前最终收口裁决）：

- 所有类型 ``@dataclass(frozen=True)``（不可变）。
- 每个类型配 ``to_dict`` / ``from_dict``；``from_dict`` 对未知字段、缺必填字段、非法枚举、
  错误嵌套类型全部 fail-closed（抛 ``SchemaValidationError``）。
- 状态 / discriminator / 枚举一律严格解析（不允许自由字符串），跨状态空间同名字符串被拒绝。
- JSON canonical 序列化确定（sort_keys、Decimal→str）；内容身份（pack_id）不含 run_id /
  timestamp / call_id / 日志路径 / 隐藏推理。
- ``required`` 语义不落字段（§五 强制裁决）：一个 aspect 是否 required 由「是否属于本 Topic
  冻结 requirement 集合」确定，与 display_tier / content_role / 是否检索到内容无关。

状态空间隔离（架构约束 1）：aspect 状态 / Pack 双轴状态与 ``harness.schema`` 的
QuestionStatus / CompletionStatus / EntailmentVerdict 是不同状态空间，禁止字符串名互通。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from contracts.schema_v2 import (
    REQUIRED_ASPECT_FIELDS,
    SOURCE_GRADES,
    TIME_POLICIES,
)
from document_structure import versions as DSV
from harness import source_manifest as SM
from harness.schema import CITATION_TYPES, COMPLETION_STATUSES, ENTAILMENT_VERDICTS

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

# TopicResearchPack 序列化 schema 版本（to_dict/from_dict 契约版本）。
# v2：JSON payload/schema 解释语义升级（set_complete 独立枚举证明 + SourcePolicyRef 唯一绑定），
#     无新增 SQLite 列；由迁移 2 记录该解释语义升级（见 topic_store.topic_schema_migrations）。
# v3：DEPENDENCY_VERSION_KEYS 增 set_enumerator（正式 SetEnumerationVerifier 进入依赖指纹），
#     与 Store schema v3（topic_material_payload 表）是两个独立版本维度，不重新耦合。
# v4：SetCompletenessAssessment 增 boundary_proof（EnumerationBoundaryProof 进入正式 typed schema
#     与 Pack content identity；历史 v3 Pack 只读保留，读取时 fail-closed 不静默补证）。
# v5：§5.2.1 successor —— DEPENDENCY_VERSION_KEYS 扩为**精确 15 键**（新增 topic_runtime /
#     budget_policy / navigation_profile / synopsis / layout / alignment / outline / span /
#     material_resolver），Requirement / Pack / commit / readback 一律执行精确等集校验：缺键
#     不再被静默接受为子集。依赖 dict 只能由唯一公共 factory build_current_dependency_versions
#     构造。v4 Pack 不再 current：current reader 读 v4 一律 schema_version_stale，
#     仅显式 legacy audit 入口可只读回看（见 LegacyTopicPackAuditView）。
# v6：M930-3 successor ——
#     1) DEPENDENCY_VERSION_KEYS 扩为**精确 18 键**（新增 material_disposition /
#        fact_qualification / external_fact）；
#     2) Pack 新增六个 successor 集合字段（material_dispositions / fact_candidates /
#        fact_qualification_decisions / external_facts / contract_gaps / research_blocks），
#        并执行「每个 material 恰一条 RMD」「每个 candidate 恰一条资格决定」「每个 eligible
#        决定恰一条 authority-specific qualified result / 每个 rejected 决定零条」「每个
#        current SupportedFact / ExternalFact 单向回指并反向解析到恰一个 candidate + eligible
#        决定」四道正式门（见 __post_init__）；
#     3) `SupportedFact` 扩展为单向引用 topic-material candidate + eligible decision。
#     v5 Pack 不再 current：current reader 读 v5 一律 schema_version_stale，仅显式 legacy
#     audit 入口（load_legacy_topic_pack_for_audit）可只读回看。
# v7：跨文档联合检索（`CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md` §0.2.2/§0.3.2/§0.3.3）——
#     1) 顶层新增**有序源集** `source_set`（四轴 `SourceDocumentKey` + `source_role` +
#        `source_set_fingerprint`），`primary_document_id` 降为唯一当前锚的**兼容读视图**；
#     2) 顶层新增逐 `(aspect, 来源文档)` 的**检索前责任** `aspect_source_responsibility` 与
#        **四类臂终态** `source_aspect_outcomes`（A/B/C1/C2）——这样**没有正式 proof 的 partial
#        Pack 也能独立保存并读回**材料归属、责任与未履行/未命中台账（v6 把它们塞在可为 None 的
#        `SetCompletenessAssessment` 里，partial 时根本无处可存）；
#     3) `SetCompletenessAssessment` 新增**稀疏** `source_set_proofs`（`KeyedSourceBoundaryProof`），
#        `boundary_proof` 降为兼容读视图（恰一条时等于它，恰零/多条时为 None 且不得被下游当作
#        完整证据）；
#     4) 顶层新增 `source_comparison_audit`（跨源比较审计）与 v7 `ResearchConflict`
#        （`ConflictSide` 带可核句子/哈希，一侧资格未通过时也有地方可放）。
#     v6 Pack 不再 current：current reader 读 v6 一律 schema_version_stale。
#     **如实边界（未实现，待裁决）**：方案 §四 要求 v6 行「只作 legacy 审计读回」并导出单成员
#     审计视图（缺 `boundary_proof`/`document_version` 时标 `legacy_source_set_unavailable`）。
#     **现场没有这条路径**：`LEGACY_TOPIC_PACK_SCHEMA_VERSIONS` 只登记 v4/v5，
#     `load_legacy_topic_pack_for_audit` 因此对 v6 行同样拒绝（`TopicStoreValidationError`），
#     `LegacyTopicPackAuditView` 也没有 `legacy_source_set_unavailable` 这一位。
#     实测（临时库写入 schema_version='6' 行，探针 `_m930_3_v6_readback_probe.py`）：
#     current reader → `SchemaVersionIncompatibleError`；get_pack → 同；legacy 审计入口 → 拒绝。
#     即**v6 行当前两条路都读不回来**。这不是设计裁决，是未实现；不得据本段注释以为已有该能力。
TOPIC_PACK_SCHEMA_VERSION = "7"
# Store 结构 schema 版本（独立维度，与 TOPIC_PACK_SCHEMA_VERSION 解耦；断言
# init_topic_store 时 STORE_SCHEMA_VERSION == MIGRATIONS[-1][0]）。
# v4：追加「Pack v5 wire/dependency 解释语义升级」迁移记录（非破坏式 no-op：无 DDL、无改列、
#     不重写历史行），仅让迁移账本显式记录本次语义升级。
# v5：M930-3 纯追加迁移 —— 新增 Pack successor 六族子表 + 边界③ `FollowUpNeed` 三表
#     （topic_follow_up_need / topic_follow_up_decision / topic_follow_up_run）。不改历史列、
#     不重写历史行、不原位迁移 v5 包。
# v6：跨文档联合检索 §0.2.2 —— 纯追加迁移：新增 Pack v7 三族子表
#     （topic_pack_source_set / topic_pack_aspect_source_responsibility /
#     topic_pack_source_aspect_outcome）+ 顶层 `source_comparison_audit` 列。
#     只新增列/子表，**不改写历史行**、不原位迁移 v6 包。
STORE_SCHEMA_VERSION = "6"
# StatusDerivation 推导规则版本（derive_pack_status 语义版本）。
STATUS_DERIVATION_RULE_VERSION = "1"

# M930-2 三项新能力的版本身份（具名且带前缀，**不是**裸 "1"，也**不是** topic_schema 版本）：
# - TOPIC_RUNTIME_VERSION：Harness-owned Topic runtime 的编排语义版本；
# - TOPIC_BUDGET_POLICY_VERSION：Topic 研究预算策略快照的语义版本；
# - TREE_MATERIAL_RESOLVER_VERSION：verified outline span → ResearchMaterial 的解析语义版本。
TOPIC_RUNTIME_VERSION = "tr-1"
TOPIC_BUDGET_POLICY_VERSION = "tbp-1"
# `tmr-2`（M930-3 定点返修 ②）：`selection_form` 的形状判据改为「**选项串 + 尾随所述内容**」——
# 记号之间与最后一个记号之后的标签仍按 `SELECTION_OPTION_MAX_CHARS` 约束，长度约束落在**选项串**
# 上而不是整段文本上，尾随那句以第六列 `trailing_content` 随行留档并排除在支撑之外
# （排除项在 `TREE_MATERIAL_SELECTION_EXCLUSIONS` 的 `no_support_from_trailing_content` 逐字写明；
# 列怎么摊由 `tree_materials.selection_form_columns` **一处**决定）。同一段真实
# 文本在 `tmr-1` 下会读成 `text`（普通正文）、在 `tmr-2` 下读成 `selection_form`（表单行），
# 两者的 payload 字节与 content 指纹都不同，因此版本必须前进：两份 payload 不得共用一个身份。
#
# `tmr-3`（M930-3 定点业务纠正①）：**所问事项只取行内前缀**，行内没写主语时这一列留空，
# **不再**用 `node_title` 顶替（`tmr-2` 及以前会回指所在节点标题并标明 `question_scope="node_title"`）。
# 理由不是措辞：`tmr-2` 的回指会把**子项**的勾选状态锚到整个**栏目**上。实测 NDSD_2025 第 28 页
# 那一行 `□适用 √不适用` 所在节点是 `（8） 主要销售客户和主要供应商情况`——那是一个**栏目**，
# 原件的这张勾选框属于其下「主要客户其他情况说明」「主要供应商其他情况说明」两个**子项**；
# 同一页紧跟着披露的集中度表（前五名合计销售额 / 占比）在原件里真实存在，与那行勾选框无关。
# 回指一旦成立，包侧就把「集中度这一栏不适用」记成可由表单行证明的结论，而同一材料里
# 既没有该栏的合格数字、也没有任何一句话支持这个否定。空 `asked_item` 是 fail-closed：
# `pack_writer._scope_confined` 对空所问事项一律返回 False ⇒ 该行授权不了任何支撑；
# 行本身仍是 `selection_form` 材料（原文、locator、指纹、选中状态全留档），
# 因此材料总数不变、`证据原文 + 来源`不丢，丢的只是那条**凭空合成**的归属。
# 同时 `question_not_resolvable` 退出封闭子原因词表：所问事项为空不再是"读不定"。
TREE_MATERIAL_RESOLVER_VERSION = "tmr-3"

# M930-3 三项新资格的版本身份（与研究材料的处置 / 事实候选的资格 / 外部事实 wire 各自绑定）：
# - MATERIAL_DISPOSITION_VERSION：ResearchMaterialDisposition 的判定规则版本；
# - FACT_QUALIFICATION_VERSION：FactQualificationDecision 的判定规则版本；
# - EXTERNAL_FACT_VERSION：formal ExternalFact 的 wire/schema 版本。
# 三者是 Pack 依赖指纹的三个新轴，唯一值源就是这三个常量。
MATERIAL_DISPOSITION_VERSION = "md-1"
FACT_QUALIFICATION_VERSION = "fq-1"
EXTERNAL_FACT_VERSION = "ef-1"

# 边界③ `FollowUpNeed` 的 wire 版本。它**不进入** Pack 依赖指纹，也不进 Section/Writer 或
# Review/Assurance 身份集：该版本键只失效 `FollowUpNeed` 自身。
FOLLOW_UP_NEED_SCHEMA_VERSION = "fun-1"

# 边界③ `FollowUpRun`（run 行）的 wire 版本。与 `FollowUpNeed` 同理：只失效边界③自身。
FOLLOW_UP_RUN_SCHEMA_VERSION = "furun-1"

# 跨文档联合检索（`CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md`）的四个版本身份。它们是 Pack v7 的
# 新轴，各自只失效自己的对象；**不进入** `DEPENDENCY_VERSION_KEYS`（本批不扩依赖键集）。
# - SOURCE_SEARCH_OUTCOME_RECORD_VERSION：逐 `(aspect, 文档)` 的未命中/未履行记录（臂 B/C2）；
# - SOURCE_ASPECT_OUTCOME_VERSION：四类臂包装记录的规则版本（A/B/C1/C2 划分）；
# - CONFLICT_RULE_VERSION：跨源冲突判定规则版本（`ConflictSide` + 期间口径）；
# - SOURCE_COMPARISON_AUDIT_VERSION：跨源比较审计的规则版本。
SOURCE_SEARCH_OUTCOME_RECORD_VERSION = "ssor-2"
#: 已登记的**历史**记录版本（current reader 拒绝，见 `SourceSearchOutcomeRecord.__post_init__`）。
#:
#: `ssor-1` → `ssor-2` 的唯一差别是臂 C2 的 `unfulfilled_reason` **取值域多了一码**
#: `dispatched_no_candidate`（见 `UNFULFILLED_REASONS`）：在 `ssor-1` 里「已派发但导航没有产出
#: 候选」被记成 `not_dispatched`，与「从未派发」同码——读回的人因此无从区分「这次派发没发生」
#: 与「派发发生了、只是导航给不出候选」。**旧读者见到新码会当成未知值，新读者见到 `ssor-1`
#: 记录则无法确定那一档当年怎么记的**，两个方向都不能静默解释，故升版。
#: **现场没有 `ssor-1` 的记录读回通道**（与 v6 Pack 同样不可回看，见
#: `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §16.8.2/§16.8.7）：如实登记为「已识别、不可读回」，
#: 不假装有，也不静默按 `ssor-2` 解释。
LEGACY_SOURCE_SEARCH_OUTCOME_RECORD_VERSIONS = ("ssor-1",)
SOURCE_ASPECT_OUTCOME_VERSION = "sao-1"
CONFLICT_RULE_VERSION = "ccr-1"
SOURCE_COMPARISON_AUDIT_VERSION = "sca-1"

#: 四类臂的闭集（**划分**：每个 `(aspect, source_document_key)` 恰好落在一个臂里，不重不漏）。
#:
#: - `A` `material_found`：该份确有材料归因到本 aspect；
#: - `B` `no_material_in_declared_scope`：**已尝试**，在该份**自己的**声明搜索范围内未命中；
#: - `C1` `not_required`：未尝试，**因为检索前责任记录说无需检索**；
#: - `C2` `required_search_unfulfilled`：责任要求检索，但因预算耗尽/调用失败/未发出/导航无候选
#:   而**没有**完成可归因的检查。
#:
#: b 轮把原来的「三臂」拆成四类：把「要求查但没查成」塌进「无需查」，读回时**看不见未履行的
#: 责任**——那正是本项要修掉的 fail-open。
SOURCE_ASPECT_OUTCOME_ARMS = ("A", "B", "C1", "C2")

#: 臂 B/C2 可投影的终态（**三值互斥**）。后两者**不得**投影成 aspect 级合格未命中；
#: 尤其「从未发出」不得叫 `ATTEMPT_WITHOUT_QUALIFIED_NOT_FOUND`。
PROJECTED_TERMINALS = (
    "NOT_FOUND_AFTER_SEARCH",            # 臂 B 且 qualified=true
    "UNQUALIFIED_SEARCH_OBSERVATION",    # 臂 B 但条件不齐
    "UNFULFILLED_REQUIRED_SEARCH",       # 臂 C2
)

#: 臂 C2 的未履行原因闭集（「要求查但没查成」只有这四种，不得自由文本）。
#:
#: `not_dispatched` 的原义**只是**「责任要求检索，但本轮**没有把这一次派发出去**」（单文档
#: 导航下的非锚必需成员即属此类：能力上限所致，不是这次导航的结论）。2026-09-25 定点返修 C2
#: 之前它还被**冒用**于另一种事：「已派发、但导航没有产出任何候选 node」——那一次派发真实
#: 发生过、导航也真实给过结论，与「从未派发」是同码异事，读回时无从区分，且会被误归因成
#: 写作/整束失败。现拆出 `dispatched_no_candidate` 承担后者，`not_dispatched` 回到原义。
#: 两者**都不得**为它造 `call_id` 或编 `attempts`：没有发生过的调用不能在痕迹里出现。
UNFULFILLED_REASONS = ("budget_exhausted", "call_failed", "not_dispatched",
                       "dispatched_no_candidate")

#: 臂 C2 的影响闭集（该 `(aspect, key)` 的未履行对 aspect 终态意味着什么）。
UNFULFILLED_IMPACTS = (
    "aspect_must_not_set_complete",
    "aspect_search_audit_not_satisfied",
)

#: 责任派生规则版本（§L1.5）。`asr-1`：`proof_required` 为**三值**并带**第一约束**（aspect 自身
#: 是否含 `set_complete`），逐份尝试的来由标注为 `batch_scheduling_rule`（**不是** Contract 的
#: 逐份义务）。冻结契约将来能表达「跨期集合要求」时升 `asr-2` 并**保留 `asr-1` 读回**（旧 Pack
#: 不得被静默重解释）。
ASSUME_RULE_VERSION = "asr-1"

#: `proof_required` 的取值域。**三值，不是布尔**——「无法确定」是必须能表达的**一等结论**，
#: 不能退化成 `false`（那等于宣称「仅当前来源必然为真」，也就是用一个不存在的映射去支撑通过）。
PROOF_REQUIRED_VALUES = ("true", "false", "undetermined")

#: 一次工具调用的 status 取值域。权威位置是 `tools.contracts.TOOL_STATUSES`（本模块不 import
#: 它，避免把工具层拉进 schema 层；由聚焦测试逐值对账防止漂移）。
ATTEMPT_STATUSES = (
    "SUCCESS",
    "PARTIAL",
    "EMPTY",
    "RETRYABLE_ERROR",
    "FATAL_ERROR",
)

#: 可支撑「该文档的范围已覆盖」的 status：**只有**这两个。`PARTIAL` 与失败**不足以**支撑
#: 范围已覆盖（截断的检索不得冒充完成的检索）。
COMPLETE_ATTEMPT_STATUSES = ("SUCCESS", "EMPTY")

#: `ConflictSide.qualification_state` 的**封闭值集**。一侧要么已确认合格（可进正式
#: `ResearchConflict`），要么带 typed 理由落在 `SourceComparisonAudit`，不存在第三种含糊。
CONFLICT_SIDE_QUALIFICATION_STATES = ("qualified", "rejected", "undetermined")

#: `SourceComparisonAudit.outcome` 的**封闭值集**（§0.3.5）。注意：**没有** `conflict`——
#: 已证冲突写正式 `ResearchConflict`，不写审计。
COMPARISON_OUTCOMES = ("comparability_pending", "observed_divergence")

#: 可比性三轴（`ccr-1`）。三轴都必须**逐轴**给结论，不得只说"可比/不可比"。
COMPARABILITY_AXES = ("period", "caliber", "subject_matter")

#: 单轴结论的封闭值集：`comparable` / `not_comparable` / `unknown`。`unknown` 与
#: `not_comparable` 语义不同——前者"未核"，后者"已证不可比"，不得合并。
COMPARABILITY_STATES = ("comparable", "not_comparable", "unknown")

# 依赖版本字典的**精确**键集：既不允许任意语义 dict，也不允许子集。
DEPENDENCY_VERSION_KEYS = (
    "contract",
    "source_policy",
    "topic_schema",
    "assessor",
    "validator",
    "set_enumerator",
    "topic_runtime",
    "budget_policy",
    "navigation_profile",
    "synopsis",
    "layout",
    "alignment",
    "outline",
    "span",
    "material_resolver",
    "material_disposition",
    "fact_qualification",
    "external_fact",
)

# v4/v5（legacy）wire 版本与依赖键集：**只**用于显式只读 legacy audit view，
# 不得用于构造 current Requirement / Pack，也不得据以自动补全 v6 键。
LEGACY_V4_TOPIC_PACK_SCHEMA_VERSION = "4"
LEGACY_V4_DEPENDENCY_VERSION_KEYS = (
    "contract",
    "source_policy",
    "topic_schema",
    "assessor",
    "validator",
    "set_enumerator",
)

LEGACY_V5_TOPIC_PACK_SCHEMA_VERSION = "5"
LEGACY_V5_DEPENDENCY_VERSION_KEYS = (
    "contract",
    "source_policy",
    "topic_schema",
    "assessor",
    "validator",
    "set_enumerator",
    "topic_runtime",
    "budget_policy",
    "navigation_profile",
    "synopsis",
    "layout",
    "alignment",
    "outline",
    "span",
    "material_resolver",
)

# legacy audit view 可承载的历史 wire 版本（**只读**；不升级、不重算指纹）。
LEGACY_TOPIC_PACK_SCHEMA_VERSIONS = (
    LEGACY_V4_TOPIC_PACK_SCHEMA_VERSION,
    LEGACY_V5_TOPIC_PACK_SCHEMA_VERSION,
)

# ---------------------------------------------------------------------------
# 枚举白名单（本状态空间）
# ---------------------------------------------------------------------------

# aspect 层结果状态（5 态；not_found 只属于 aspect 层，不是 Pack 流程状态）。
ASPECT_RESULT_STATUSES = (
    "covered",
    "partial",
    "not_found",
    "blocked",
    "not_applicable",
)

# Pack 双轴之一：研究流程是否已停止（正交于 coverage）。
PACK_PROCESS_STATUSES = (
    "pending",
    "running",
    "finished",
    "stopped_by_budget",
    "blocked",
    "failed",
)

# Pack 双轴之二：required aspect 内容覆盖（正交于 process）。
PACK_COVERAGE_STATUSES = (
    "complete",
    "complete_with_gaps",
    "insufficient",
    "unavailable",
)

# material 类型（与 locator / authority 变体严格绑定）。
MATERIAL_TYPES = (
    "evidence_span",
    "table_context",
    "structured",
    "external_snapshot",
)

# 已采用事实类别（镜像 harness.schema.CLAIM_KINDS 的 fact/inference 子集；
# retrieval_observation 是运行时诊断，不得作为 adopted fact）。
FACT_TYPES = ("fact", "inference")

# 三类来源权威 discriminator / locator discriminator。
AUTHORITY_TYPES = ("evidence", "financial_snapshot", "external_snapshot")

# 权威结论（authority gate 输出，供关键结论 sufficiency 参考；不合并三类来源判据）。
AUTHORITY_VERDICTS = ("authoritative", "supplemental_only", "rejected")

# 财务快照有效性命中值（FinancialSnapshotAuthorityAssessment.validity）。
FINANCIAL_VALIDITIES = ("valid", "stale", "superseded", "invalid")

# 冲突类别与状态。
CONFLICT_CATEGORIES = (
    "value_conflict",
    "source_conflict",
    "scope_conflict",
    "period_conflict",
    "unit_conflict",
)
CONFLICT_STATUSES = ("open", "resolved", "escalated")

# 缺口原因码与影响范围（impact 沿用契约 IMPACT_SCOPES 语义）。
GAP_REASON_CODES = (
    "not_found",
    "blocked",
    "budget_exhausted",
    "source_rejected",
    "unresolved",
)
GAP_IMPACTS = ("subject", "solvency", "key_financial", "credit_scheme")

# --- M930-3 候选 / 资格 / 材料处置 的封闭枚举 -------------------------------------

# 事实候选的**唯一**来源类别。它封闭候选进入资格门的路径：topic material（内部材料）
# 走 `SupportedFact`，external source（外部来源）走 formal `ExternalFact`；二者不可交叉。
CANDIDATE_SOURCE_KINDS = ("topic_material", "external_source")

# 资格结论（FactQualificationDecision.verdict）。`rejected` 决定本身就是该候选**唯一**的
# typed rejection audit —— 不另造第五种权威对象。
QUALIFICATION_VERDICTS = ("eligible", "rejected")

# 材料处置轴（ResearchMaterialDisposition）。**不含** used/not_used 语义：是否被本次写作
# 使用属于 Writer 侧 `WriterMaterialProcessingDisposition`，两个身份不得混装。
MATERIAL_ADMISSION_STATES = ("admitted", "rejected")
MATERIAL_RETENTION_STATES = ("retained", "dropped")
# 材料载体自身的来源校验结论（独立于 admission/retention 两轴）。
MATERIAL_SOURCE_VALIDATIONS = ("validated", "rejected")

# 材料处置的 typed 理由码（admission/retention 各自的确定性证明标签）。
MATERIAL_DISPOSITION_REASON_CODES = (
    "aspect_material_admitted",
    "authority_rejected",
    "payload_unresolvable",
    "material_type_mismatch",
    "bounded_out",
    "superseded_by_recut",
)

# 研究侧 block 类别（ResearchBlock）。block ≠ rejection：它描述「研究路径被硬性挡住」。
RESEARCH_BLOCK_KINDS = (
    "source_policy",
    "budget",
    "capability",
    "authority",
    "structure",
)

# `FollowUpNeed.requiredness`：只决定优先级与「是否可能另外产生 gap/block」，
# 不改变 need 本身是否被 Harness 裁决。
FOLLOW_UP_REQUIREDNESS = ("required", "optional")

# `FollowUpDecision.verdict`（Harness 裁决）：accepted 才执行；rejected/reduced 不执行。
FOLLOW_UP_DECISION_VERDICTS = ("accepted", "rejected", "reduced")

# 可检索来源类（`FollowUpNeed.expected_source_class` 的封闭集合，与权威类型一一对应）。
SOURCE_CLASSES = ("company_industry", "structured_db", "external")

# 运行消耗 metric 枚举（UsageEntry.metric）。
USAGE_METRICS = (
    "rounds",
    "tool_calls",
    "local_searches",
    "external_searches",
    "fetches",
    "snapshots",
    "llm_calls",
    "input_tokens",
    "output_tokens",
    "elapsed_ms",
)

# 预算耗尽 / 硬 block / 运行期失败 的停止原因（derive_pack_status 用于 process 轴判定）。
_BUDGET_STOP_PREFIXES = ("BUDGET",)
_BLOCK_STOP_CODES = ("PATH_NOT_IMPLEMENTED", "REPORT_BLOCKED", "SECTION_BLOCKED", "JOB_BLOCKED")
_FATAL_STOP_CODES = (
    "ACTION_SCHEMA_INVALID",
    "MODEL_OUTPUT_INVALID",
    "FATAL_TOOL_ERROR",
    "VERSION_INCOMPATIBLE",
    "SESSION_POISONED",
)

# material type → locator discriminator / authority discriminator 的严格绑定。
_LOCATOR_TYPE_BY_MATERIAL = {
    "evidence_span": "evidence",
    "table_context": "evidence",
    "structured": "financial_snapshot",
    "external_snapshot": "external_snapshot",
}
_AUTHORITY_TYPE_BY_MATERIAL = {
    "evidence_span": "evidence",
    "table_context": "evidence",
    "structured": "financial_snapshot",
    "external_snapshot": "external_snapshot",
}

# 权威类型 → 来源类（source_class，对齐 contracts.schema_v2 / source_policy_v1 的 source_classes）。
# financial_snapshot 是公司结构化披露（非 external），与 company disclosure 同属「非外部」来源。
AUTHORITY_SOURCE_CLASS_BY_TYPE = {
    "evidence": "company_industry",
    "financial_snapshot": "structured_db",
    "external_snapshot": "external",
}

# 行业风险传导四层（对齐 contracts.schema_v2.TRANSMISSION_LAYERS）。
TRANSMISSION_LAYERS = (
    "industry_background",
    "conditional_transmission",
    "company_exposure",
    "actual_company_impact",
)

# sufficiency gate 评估器版本与规则 ID。规则本身由冻结输入（EvidenceRequirement +
# SourcePolicy + transmission_layers）派生，绝不硬编码 Topic 名单。
SUFFICIENCY_ASSESSOR_VERSION = "1"
KEY_CONCLUSION_RULE = "key_conclusion_ab_c"
KEY_CONCLUSION_RULE_VERSION = "1"
TRANSMISSION_SUFFICIENCY_RULES = {
    "industry_background": ("industry_background_external", "1"),
    "conditional_transmission": ("conditional_transmission_verified_fact", "1"),
    "company_exposure": ("company_exposure_company_disclosure", "1"),
    "actual_company_impact": ("actual_company_impact_company_disclosure", "1"),
}

# support eligibility 政策版本（usage-scope gate 的派生结果版本）。
SUPPORT_ELIGIBILITY_POLICY_VERSION = "1"

# set_complete 评估器版本 / 规则版本 / 可信 verifier 版本（Fix 4 强制一致）。
SET_COMPLETENESS_ASSESSOR_VERSION = "1"
SET_COMPLETENESS_RULE_VERSION = "1"
SET_COMPLETENESS_VERIFIER_VERSION = "1"
# set_complete 独立枚举 verifier 版本（Fix 2）。SetEnumerationVerifier 是受信任、版本化、
# 确定性的运行时依赖：Store 只能校验它返回的 payload_hash / boundary_identity / enumerated
# 集合关系与真实解析 payload 身份一致，无法证明任意注入实现「内部确实读取过 payload bytes」。
# R2 须实现正式确定性枚举器并由唯一正式组合入口注入；接线前生产运行链不得将 set_complete
# aspect 提升为 covered。此版本必须进入 R2 dependency fingerprint（本轮只记录该硬门）。
SET_ENUMERATION_VERIFIER_VERSION = "1"

# 条件性行业传导 inference 政策（版本化允许枚举；冻结 Contract 只枚举字段名，不枚举字段值，
# 故 policy 身份/版本与 direction 枚举由本 schema 版本化定义，Store 据此 fail-closed 校验）。
CONDITIONAL_INFERENCE_POLICY_ID = "conditional-transmission-inference-v1"
CONDITIONAL_INFERENCE_POLICY_VERSION = "1"
INFERENCE_DIRECTIONS = ("industry_to_company",)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class SchemaValidationError(ValueError):
    """schema 反序列化 / 构造 fail-closed（未知字段、缺必填、非法枚举、错误嵌套类型）。"""


class StateAdaptationError(ValueError):
    """状态空间适配失败：输入不在已知枚举内，绝不静默映射为「合格」。"""


# ---------------------------------------------------------------------------
# canonical 序列化 / 指纹
# ---------------------------------------------------------------------------

def _to_json_value(v: Any) -> Any:
    """把 frozen 对象图转换为 JSON 安全 primitive（Decimal→str，tuple→list，嵌套→to_dict）。"""
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, tuple):
        return [_to_json_value(x) for x in v]
    if isinstance(v, list):
        return [_to_json_value(x) for x in v]
    if isinstance(v, (frozenset, set)):
        return sorted((_to_json_value(x) for x in v), key=lambda s: str(s))
    if isinstance(v, dict):
        return {k: _to_json_value(x) for k, x in v.items()}
    if hasattr(v, "to_dict"):
        return v.to_dict()
    raise TypeError(f"不可序列化类型: {type(v).__name__}")


def canonical_json(obj: Any) -> str:
    """确定性 JSON 字符串（sort_keys、Decimal→str、ensure_ascii=False）。"""
    return json.dumps(_to_json_value(obj), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def sha256_canonical(obj: Any) -> str:
    """对对象图求稳定 sha256（确定性规范形）。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _is_sha256_hex(v: str) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


# ---------------------------------------------------------------------------
# from_dict 解析助手（全部 fail-closed）
# ---------------------------------------------------------------------------

def _reject_unknown(d: Any, allowed: set[str], typename: str) -> dict:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"{typename}.from_dict 需要 dict，得到 {type(d).__name__}")
    unknown = set(d) - allowed
    if unknown:
        raise SchemaValidationError(f"{typename} 含未知字段: {sorted(unknown)}")
    return d


def _get_str(d: dict, key: str, typename: str, allow_none: bool = False,
             allow_empty: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        if allow_none:
            return None
        raise SchemaValidationError(f"{typename} 缺必填字段: {key}")
    if not isinstance(v, str):
        raise SchemaValidationError(f"{typename}.{key} 必须为字符串，得到 {type(v).__name__}")
    if v == "" and not allow_empty:
        raise SchemaValidationError(f"{typename}.{key} 必须为非空字符串")
    return v


def _get_bool(d: dict, key: str, typename: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise SchemaValidationError(f"{typename}.{key} 必须为 bool，得到 {v!r}")
    return v


def _get_int(d: dict, key: str, typename: str, allow_none: bool = False) -> int | None:
    v = d.get(key)
    if v is None and allow_none:
        return None
    if not isinstance(v, int) or isinstance(v, bool):
        raise SchemaValidationError(f"{typename}.{key} 必须为 int，得到 {v!r}")
    return v


def _get_str_tuple(d: dict, key: str, typename: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        return default
    if not isinstance(v, list):
        raise SchemaValidationError(f"{typename}.{key} 必须为 list[str]，得到 {type(v).__name__}")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str):
            raise SchemaValidationError(f"{typename}.{key} 含非字符串元素: {x!r}")
        out.append(x)
    return tuple(out)


def _get_int_pair(d: dict, key: str, typename: str, allow_none: bool = False) -> tuple[int, int] | None:
    v = d.get(key)
    if v is None:
        if allow_none:
            return None
        raise SchemaValidationError(f"{typename} 缺必填字段: {key}")
    if (not isinstance(v, list) or len(v) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        raise SchemaValidationError(f"{typename}.{key} 必须为 [int, int]，得到 {v!r}")
    return (v[0], v[1])


def _get_enum(v: str, allowed: tuple[str, ...], typename: str, key: str) -> str:
    if v not in allowed:
        raise SchemaValidationError(
            f"{typename}.{key} 非法枚举 {v!r}（允许 {allowed}）")
    return v


# ---------------------------------------------------------------------------
# 冻结投影引用类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceClassGroup:
    """required_any_of 的一个可选组：一组来源类 + 可选最低等级 + 可选事实类别。"""

    source_classes: tuple[str, ...]
    min_grade: str | None = None
    kind: str | None = None

    def __post_init__(self) -> None:
        if not self.source_classes:
            raise SchemaValidationError("SourceClassGroup.source_classes 必须非空")
        if self.min_grade is not None:
            _get_enum(self.min_grade, SOURCE_GRADES, "SourceClassGroup", "min_grade")

    def to_dict(self) -> dict:
        return {
            "source_classes": list(self.source_classes),
            "min_grade": self.min_grade,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceClassGroup":
        d = _reject_unknown(d, {"source_classes", "min_grade", "kind"}, "SourceClassGroup")
        return cls(
            source_classes=_get_str_tuple(d, "source_classes", "SourceClassGroup"),
            min_grade=_get_str(d, "min_grade", "SourceClassGroup", allow_none=True),
            kind=_get_str(d, "kind", "SourceClassGroup", allow_none=True),
        )


@dataclass(frozen=True)
class EvidenceAuthorityPolicy:
    """冻结证据需求的 authority 使用范围（required_any_of / supplemental_only / inference_lineage）。

    这是「来源使用资格」而非「来源权威」：required_any_of 决定哪些来源类可作为 formal
    事实支撑该 aspect；supplemental_only 决定哪些来源类只能补充、不能独立支撑；
    inference_lineage_required 决定是否需要推断链。
    """

    required_any_of: tuple[SourceClassGroup, ...]
    supplemental_only: tuple[str, ...] = ()
    inference_lineage_required: bool = False

    def __post_init__(self) -> None:
        if not self.required_any_of:
            raise SchemaValidationError("EvidenceAuthorityPolicy.required_any_of 必须非空")

    def to_dict(self) -> dict:
        return {
            "required_any_of": [g.to_dict() for g in self.required_any_of],
            "supplemental_only": list(self.supplemental_only),
            "inference_lineage_required": self.inference_lineage_required,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceAuthorityPolicy":
        d = _reject_unknown(d, {"required_any_of", "supplemental_only", "inference_lineage_required"},
                            "EvidenceAuthorityPolicy")
        return cls(
            required_any_of=tuple(SourceClassGroup.from_dict(x) for x in _as_list(
                d.get("required_any_of"), "EvidenceAuthorityPolicy", "required_any_of")),
            supplemental_only=_get_str_tuple(d, "supplemental_only", "EvidenceAuthorityPolicy"),
            inference_lineage_required=_get_bool(d, "inference_lineage_required", "EvidenceAuthorityPolicy"),
        )


@dataclass(frozen=True)
class EvidenceRequirementRef:
    """证据需求权威引用（绑定 requirement ID + 所属 Contract SHA + requirement 指纹 + schema/version
    + 来源类使用资格）。source_classes / authority 用于 usage-scope gate（Fix 1），可空表示
    无冻结使用资格信息（旧合成引用不触发 usage-scope gate）。"""

    requirement_id: str
    contract_sha256: str
    requirement_fingerprint: str
    schema_version: str
    source_classes: tuple[str, ...] = ()
    authority: EvidenceAuthorityPolicy | None = None

    def __post_init__(self) -> None:
        if not self.requirement_id:
            raise SchemaValidationError("EvidenceRequirementRef.requirement_id 必须非空")
        if not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("EvidenceRequirementRef.contract_sha256 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.requirement_fingerprint):
            raise SchemaValidationError("EvidenceRequirementRef.requirement_fingerprint 必须为 64 位 sha256 hex")
        if not self.schema_version:
            raise SchemaValidationError("EvidenceRequirementRef.schema_version 必须非空")

    def to_dict(self) -> dict:
        return {
            "requirement_id": self.requirement_id,
            "contract_sha256": self.contract_sha256,
            "requirement_fingerprint": self.requirement_fingerprint,
            "schema_version": self.schema_version,
            "source_classes": list(self.source_classes),
            "authority": self.authority.to_dict() if self.authority is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceRequirementRef":
        d = _reject_unknown(d, {"requirement_id", "contract_sha256", "requirement_fingerprint",
                                "schema_version", "source_classes", "authority"},
                            "EvidenceRequirementRef")
        auth = d.get("authority")
        return cls(
            requirement_id=_get_str(d, "requirement_id", "EvidenceRequirementRef"),
            contract_sha256=_get_str(d, "contract_sha256", "EvidenceRequirementRef"),
            requirement_fingerprint=_get_str(d, "requirement_fingerprint", "EvidenceRequirementRef"),
            schema_version=_get_str(d, "schema_version", "EvidenceRequirementRef"),
            source_classes=_get_str_tuple(d, "source_classes", "EvidenceRequirementRef"),
            authority=EvidenceAuthorityPolicy.from_dict(auth) if auth is not None else None,
        )


@dataclass(frozen=True)
class SourcePolicyRef:
    """来源政策权威引用（绑定 policy version + content fingerprint）。"""

    policy_id: str
    policy_version: str
    content_fingerprint: str

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise SchemaValidationError("SourcePolicyRef.policy_id 必须非空")
        if not self.policy_version:
            raise SchemaValidationError("SourcePolicyRef.policy_version 必须非空")
        if not _is_sha256_hex(self.content_fingerprint):
            raise SchemaValidationError("SourcePolicyRef.content_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourcePolicyRef":
        d = _reject_unknown(d, {"policy_id", "policy_version", "content_fingerprint"}, "SourcePolicyRef")
        return cls(
            policy_id=_get_str(d, "policy_id", "SourcePolicyRef"),
            policy_version=_get_str(d, "policy_version", "SourcePolicyRef"),
            content_fingerprint=_get_str(d, "content_fingerprint", "SourcePolicyRef"),
        )


@dataclass(frozen=True)
class DerivedFromScope:
    """typed include/exclude 语义（schema_v2.DERIVED_FROM_SCOPE_KEYS）。"""

    include_sections: tuple[str, ...]
    exclude_producer_kinds: tuple[str, ...] = ()
    exclude_display_tiers: tuple[str, ...] = ("diagnostic_only",)
    exclude_aspect_ids: tuple[str, ...] = ()
    exclude_terminal_states: tuple[str, ...] = ("NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED")

    def __post_init__(self) -> None:
        if len(self.include_sections) == 0:
            raise SchemaValidationError("DerivedFromScope.include_sections 必须非空")

    def to_dict(self) -> dict:
        return {
            "include_sections": list(self.include_sections),
            "exclude_producer_kinds": list(self.exclude_producer_kinds),
            "exclude_display_tiers": list(self.exclude_display_tiers),
            "exclude_aspect_ids": list(self.exclude_aspect_ids),
            "exclude_terminal_states": list(self.exclude_terminal_states),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DerivedFromScope":
        d = _reject_unknown(d, {"include_sections", "exclude_producer_kinds", "exclude_display_tiers",
                                "exclude_aspect_ids", "exclude_terminal_states"}, "DerivedFromScope")
        return cls(
            include_sections=_get_str_tuple(d, "include_sections", "DerivedFromScope"),
            exclude_producer_kinds=_get_str_tuple(d, "exclude_producer_kinds", "DerivedFromScope"),
            exclude_display_tiers=_get_str_tuple(d, "exclude_display_tiers", "DerivedFromScope",
                                                 ("diagnostic_only",)),
            exclude_aspect_ids=_get_str_tuple(d, "exclude_aspect_ids", "DerivedFromScope"),
            exclude_terminal_states=_get_str_tuple(
                d, "exclude_terminal_states", "DerivedFromScope",
                ("NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED")),
        )


@dataclass(frozen=True)
class TopicAspectRequirementSnapshot:
    """冻结 AspectV2 的完整投影。

    前 26 字段名与 ``contracts/schema_v2.py::AspectV2`` 逐一一致（22 REQUIRED_ASPECT_FIELDS
    + 4 扩展字段），不得发明近义字段替代。evidence_requirement_ids 投影为
    ``EvidenceRequirementRef``（绑定指纹），source_policy_ref 投影为 ``SourcePolicyRef``。
    """

    # 22 REQUIRED_ASPECT_FIELDS
    aspect_id: str
    question_id: str
    topic_id: str
    requirement_text: str
    kind: str
    producer_kind: str
    execution_path: str
    required_fields: tuple[str, ...]
    coverage_rules: tuple[str, ...]
    complete_set_rule: str
    evidence_requirement_ids: tuple[EvidenceRequirementRef, ...]
    source_policy_ref: SourcePolicyRef
    time_scope: str
    display_tier: str
    content_role: str
    missing_policy: str
    blocking_policy: tuple[str, ...]
    applicability_policy: str | None
    impact_scope: tuple[str, ...]
    output_destination: str
    derived_from: tuple[str, ...]
    business_review_status: str
    # 4 扩展字段
    business_review_reason: str = ""
    derived_from_scope: DerivedFromScope | None = None
    transmission_layers: tuple[str, ...] = ()
    transmission_channel: str = ""
    # deterministic derived 便捷字段（标注派生，不取代原始冻结字段）
    freshness_window: str | None = None
    # 版本/指纹绑定（回查不可变 typed snapshot 所需）
    contract_version: str = ""
    contract_sha256: str = ""
    canonical_fingerprint: str = ""
    dependency_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.aspect_id or not self.topic_id:
            raise SchemaValidationError("TopicAspectRequirementSnapshot.aspect_id/topic_id 必须非空")
        if self.contract_sha256 and not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("TopicAspectRequirementSnapshot.contract_sha256 必须为 64 位 sha256 hex")
        if self.canonical_fingerprint and not _is_sha256_hex(self.canonical_fingerprint):
            raise SchemaValidationError("TopicAspectRequirementSnapshot.canonical_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "question_id": self.question_id,
            "topic_id": self.topic_id,
            "requirement_text": self.requirement_text,
            "kind": self.kind,
            "producer_kind": self.producer_kind,
            "execution_path": self.execution_path,
            "required_fields": list(self.required_fields),
            "coverage_rules": list(self.coverage_rules),
            "complete_set_rule": self.complete_set_rule,
            "evidence_requirement_ids": [r.to_dict() for r in self.evidence_requirement_ids],
            "source_policy_ref": self.source_policy_ref.to_dict(),
            "time_scope": self.time_scope,
            "display_tier": self.display_tier,
            "content_role": self.content_role,
            "missing_policy": self.missing_policy,
            "blocking_policy": list(self.blocking_policy),
            "applicability_policy": self.applicability_policy,
            "impact_scope": list(self.impact_scope),
            "output_destination": self.output_destination,
            "derived_from": list(self.derived_from),
            "business_review_status": self.business_review_status,
            "business_review_reason": self.business_review_reason,
            "derived_from_scope": self.derived_from_scope.to_dict() if self.derived_from_scope else None,
            "transmission_layers": list(self.transmission_layers),
            "transmission_channel": self.transmission_channel,
            "freshness_window": self.freshness_window,
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "canonical_fingerprint": self.canonical_fingerprint,
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicAspectRequirementSnapshot":
        allowed = set(REQUIRED_ASPECT_FIELDS) | {
            "business_review_reason", "derived_from_scope", "transmission_layers", "transmission_channel",
            "freshness_window", "contract_version", "contract_sha256", "canonical_fingerprint",
            "dependency_fingerprint",
        }
        d = _reject_unknown(d, allowed, "TopicAspectRequirementSnapshot")
        dfs = d.get("derived_from_scope")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "TopicAspectRequirementSnapshot"),
            question_id=_get_str(d, "question_id", "TopicAspectRequirementSnapshot"),
            topic_id=_get_str(d, "topic_id", "TopicAspectRequirementSnapshot"),
            requirement_text=_get_str(d, "requirement_text", "TopicAspectRequirementSnapshot"),
            kind=_get_str(d, "kind", "TopicAspectRequirementSnapshot"),
            producer_kind=_get_str(d, "producer_kind", "TopicAspectRequirementSnapshot"),
            execution_path=_get_str(d, "execution_path", "TopicAspectRequirementSnapshot"),
            required_fields=_get_str_tuple(d, "required_fields", "TopicAspectRequirementSnapshot"),
            coverage_rules=_get_str_tuple(d, "coverage_rules", "TopicAspectRequirementSnapshot"),
            complete_set_rule=_get_str(d, "complete_set_rule", "TopicAspectRequirementSnapshot",
                                       allow_empty=True),
            evidence_requirement_ids=tuple(
                EvidenceRequirementRef.from_dict(x)
                for x in (_as_list(d.get("evidence_requirement_ids"), "TopicAspectRequirementSnapshot",
                                   "evidence_requirement_ids"))
            ),
            source_policy_ref=SourcePolicyRef.from_dict(_as_dict(
                d.get("source_policy_ref"), "TopicAspectRequirementSnapshot", "source_policy_ref")),
            time_scope=_get_str(d, "time_scope", "TopicAspectRequirementSnapshot"),
            display_tier=_get_str(d, "display_tier", "TopicAspectRequirementSnapshot"),
            content_role=_get_str(d, "content_role", "TopicAspectRequirementSnapshot"),
            missing_policy=_get_str(d, "missing_policy", "TopicAspectRequirementSnapshot"),
            blocking_policy=_get_str_tuple(d, "blocking_policy", "TopicAspectRequirementSnapshot"),
            applicability_policy=_get_str(d, "applicability_policy", "TopicAspectRequirementSnapshot",
                                         allow_none=True),
            impact_scope=_get_str_tuple(d, "impact_scope", "TopicAspectRequirementSnapshot"),
            output_destination=_get_str(d, "output_destination", "TopicAspectRequirementSnapshot"),
            derived_from=_get_str_tuple(d, "derived_from", "TopicAspectRequirementSnapshot"),
            business_review_status=_get_str(d, "business_review_status", "TopicAspectRequirementSnapshot"),
            business_review_reason=_get_str(d, "business_review_reason", "TopicAspectRequirementSnapshot",
                                            allow_empty=True, allow_none=True) or "",
            derived_from_scope=DerivedFromScope.from_dict(dfs) if dfs is not None else None,
            transmission_layers=_get_str_tuple(d, "transmission_layers", "TopicAspectRequirementSnapshot"),
            transmission_channel=_get_str(d, "transmission_channel", "TopicAspectRequirementSnapshot",
                                          allow_empty=True),
            freshness_window=_get_str(d, "freshness_window", "TopicAspectRequirementSnapshot", allow_none=True),
            contract_version=_get_str(d, "contract_version", "TopicAspectRequirementSnapshot",
                                      allow_empty=True),
            contract_sha256=_get_str(d, "contract_sha256", "TopicAspectRequirementSnapshot",
                                     allow_empty=True),
            canonical_fingerprint=_get_str(d, "canonical_fingerprint", "TopicAspectRequirementSnapshot",
                                           allow_empty=True),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "TopicAspectRequirementSnapshot",
                                            allow_empty=True),
        )


def _as_list(v: Any, typename: str, key: str) -> list:
    if not isinstance(v, list):
        raise SchemaValidationError(f"{typename}.{key} 必须为 list，得到 {type(v).__name__}")
    return v


def _as_dict(v: Any, typename: str, key: str) -> dict:
    if not isinstance(v, dict):
        raise SchemaValidationError(f"{typename}.{key} 必须为 dict，得到 {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# Locator 三类联合（按 material_type 区分）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceLocator:
    """evidence_span / table_context 的定位（material_type 绑定）。"""

    locator_type: str = field(init=False, default="evidence")
    document_id: str = ""
    document_version: str = ""
    section_path: str = ""
    page: int | None = None
    table_title: str | None = None
    block_range: tuple[int, int] | None = None
    offset: int | None = None

    def __post_init__(self) -> None:
        # 定位最低要求：page / block_range / section_path 至少一个有效；表格场景保留 table title。
        if self.page is None and self.block_range is None and not self.section_path:
            raise SchemaValidationError("EvidenceLocator 至少需要 page / block_range / section_path 之一")
        if not self.document_id and not self.document_version:
            raise SchemaValidationError("EvidenceLocator 需提供 document_id 或 document_version")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "section_path": self.section_path,
            "page": self.page,
            "table_title": self.table_title,
            "block_range": list(self.block_range) if self.block_range is not None else None,
            "offset": self.offset,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceLocator":
        d = _reject_unknown(d, {"locator_type", "document_id", "document_version", "section_path",
                                "page", "table_title", "block_range", "offset"}, "EvidenceLocator")
        if d.get("locator_type") not in (None, "evidence"):
            raise SchemaValidationError(f"EvidenceLocator.locator_type 必须为 'evidence'，得到 {d.get('locator_type')!r}")
        return cls(
            document_id=_get_str(d, "document_id", "EvidenceLocator", allow_empty=True) or "",
            document_version=_get_str(d, "document_version", "EvidenceLocator", allow_empty=True) or "",
            section_path=_get_str(d, "section_path", "EvidenceLocator", allow_empty=True) or "",
            page=_get_int(d, "page", "EvidenceLocator", allow_none=True),
            table_title=_get_str(d, "table_title", "EvidenceLocator", allow_none=True),
            block_range=_get_int_pair(d, "block_range", "EvidenceLocator", allow_none=True),
            offset=_get_int(d, "offset", "EvidenceLocator", allow_none=True),
        )


@dataclass(frozen=True)
class FinancialLocator:
    """structured（FinancialSnapshot）的定位。"""

    locator_type: str = field(init=False, default="financial_snapshot")
    snapshot_id: str = ""
    company_id: str = ""
    scope: str = ""
    report_as_of: str = ""
    formula_id: str | None = None
    formula_version: str | None = None
    item_code: str | None = None
    period: str | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise SchemaValidationError("FinancialLocator.snapshot_id 必须非空")
        # item_code 与 formula_id 至少一个有效。
        if not self.item_code and not self.formula_id:
            raise SchemaValidationError("FinancialLocator 至少需要 item_code 或 formula_id 之一")
        # Fix 2：formula_id 存在 → 必须可验证 formula_version；item-only 不得伪造公式版本。
        if self.formula_id is not None and not self.formula_version:
            raise SchemaValidationError("FinancialLocator 引用 formula_id 必须携带 formula_version")
        if self.formula_id is None and self.formula_version is not None:
            raise SchemaValidationError("FinancialLocator.formula_version 不得脱离 formula_id 存在")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "snapshot_id": self.snapshot_id,
            "company_id": self.company_id,
            "scope": self.scope,
            "report_as_of": self.report_as_of,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "item_code": self.item_code,
            "period": self.period,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialLocator":
        d = _reject_unknown(d, {"locator_type", "snapshot_id", "company_id", "scope", "report_as_of",
                                "formula_id", "formula_version", "item_code", "period"}, "FinancialLocator")
        if d.get("locator_type") not in (None, "financial_snapshot"):
            raise SchemaValidationError(
                f"FinancialLocator.locator_type 必须为 'financial_snapshot'，得到 {d.get('locator_type')!r}")
        return cls(
            snapshot_id=_get_str(d, "snapshot_id", "FinancialLocator"),
            company_id=_get_str(d, "company_id", "FinancialLocator", allow_empty=True) or "",
            scope=_get_str(d, "scope", "FinancialLocator", allow_empty=True) or "",
            report_as_of=_get_str(d, "report_as_of", "FinancialLocator", allow_empty=True) or "",
            formula_id=_get_str(d, "formula_id", "FinancialLocator", allow_none=True),
            formula_version=_get_str(d, "formula_version", "FinancialLocator", allow_none=True),
            item_code=_get_str(d, "item_code", "FinancialLocator", allow_none=True),
            period=_get_str(d, "period", "FinancialLocator", allow_none=True),
        )


@dataclass(frozen=True)
class ExternalLocator:
    """external_snapshot 的定位。"""

    locator_type: str = field(init=False, default="external_snapshot")
    source_snapshot_id: str = ""
    canonical_url: str = ""
    domain: str = ""
    fetched_at: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        if not self.source_snapshot_id:
            raise SchemaValidationError("ExternalLocator.source_snapshot_id 必须非空")
        if not self.canonical_url:
            raise SchemaValidationError("ExternalLocator.canonical_url 必须非空")
        if not self.domain:
            raise SchemaValidationError("ExternalLocator.domain 必须非空")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "source_snapshot_id": self.source_snapshot_id,
            "canonical_url": self.canonical_url,
            "domain": self.domain,
            "fetched_at": self.fetched_at,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalLocator":
        d = _reject_unknown(d, {"locator_type", "source_snapshot_id", "canonical_url", "domain",
                                "fetched_at", "published_at"}, "ExternalLocator")
        if d.get("locator_type") not in (None, "external_snapshot"):
            raise SchemaValidationError(
                f"ExternalLocator.locator_type 必须为 'external_snapshot'，得到 {d.get('locator_type')!r}")
        return cls(
            source_snapshot_id=_get_str(d, "source_snapshot_id", "ExternalLocator"),
            canonical_url=_get_str(d, "canonical_url", "ExternalLocator"),
            domain=_get_str(d, "domain", "ExternalLocator"),
            fetched_at=_get_str(d, "fetched_at", "ExternalLocator", allow_none=True),
            published_at=_get_str(d, "published_at", "ExternalLocator", allow_none=True),
        )


MaterialLocator = EvidenceLocator | FinancialLocator | ExternalLocator


def locator_from_dict(d: Any) -> MaterialLocator:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"MaterialLocator 需要 dict，得到 {type(d).__name__}")
    t = d.get("locator_type")
    if t == "evidence":
        return EvidenceLocator.from_dict(d)
    if t == "financial_snapshot":
        return FinancialLocator.from_dict(d)
    if t == "external_snapshot":
        return ExternalLocator.from_dict(d)
    raise SchemaValidationError(f"未知 locator_type: {t!r}")


# ---------------------------------------------------------------------------
# 三类权威联合（authority gate，架构约束 2）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceAuthorityAssessment:
    """evidence/document 权威（current document/current set/company/正文/hash/定位）。"""

    authority_type: str = field(init=False, default="evidence")
    evidence_id: str = ""
    document_id: str = ""
    document_version: str = ""
    company_id: str = ""
    is_current_document: bool = False
    is_current_set: bool = False
    page: int | None = None
    block_range: tuple[int, int] | None = None
    fetched_inspected_nonempty: bool = False
    content_hash: str = ""
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id:
            raise SchemaValidationError("EvidenceAuthorityAssessment.evidence_id 必须非空")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "EvidenceAuthorityAssessment", "verdict")

    def to_dict(self) -> dict:
        return {
            "authority_type": self.authority_type,
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "company_id": self.company_id,
            "is_current_document": self.is_current_document,
            "is_current_set": self.is_current_set,
            "page": self.page,
            "block_range": list(self.block_range) if self.block_range is not None else None,
            "fetched_inspected_nonempty": self.fetched_inspected_nonempty,
            "content_hash": self.content_hash,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "evidence_id", "document_id", "document_version",
                                "company_id", "is_current_document", "is_current_set", "page",
                                "block_range", "fetched_inspected_nonempty", "content_hash", "verdict",
                                "reason", "validator_version"}, "EvidenceAuthorityAssessment")
        if d.get("authority_type") not in (None, "evidence"):
            raise SchemaValidationError(
                f"EvidenceAuthorityAssessment.authority_type 必须为 'evidence'，得到 {d.get('authority_type')!r}")
        return cls(
            evidence_id=_get_str(d, "evidence_id", "EvidenceAuthorityAssessment"),
            document_id=_get_str(d, "document_id", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            document_version=_get_str(d, "document_version", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            company_id=_get_str(d, "company_id", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            is_current_document=_get_bool(d, "is_current_document", "EvidenceAuthorityAssessment"),
            is_current_set=_get_bool(d, "is_current_set", "EvidenceAuthorityAssessment"),
            page=_get_int(d, "page", "EvidenceAuthorityAssessment", allow_none=True),
            block_range=_get_int_pair(d, "block_range", "EvidenceAuthorityAssessment", allow_none=True),
            fetched_inspected_nonempty=_get_bool(d, "fetched_inspected_nonempty", "EvidenceAuthorityAssessment"),
            content_hash=_get_str(d, "content_hash", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            verdict=_get_str(d, "verdict", "EvidenceAuthorityAssessment"),
            reason=_get_str(d, "reason", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            validator_version=_get_str(d, "validator_version", "EvidenceAuthorityAssessment",
                                       allow_empty=True) or "",
        )


@dataclass(frozen=True)
class FinancialSnapshotAuthorityAssessment:
    """FinancialSnapshot 权威（current/valid/company/scope/currency/purpose/report_as_of/blocked/quarantine）。"""

    authority_type: str = field(init=False, default="financial_snapshot")
    snapshot_id: str = ""
    company_id: str = ""
    scope: str = ""
    currency: str = ""
    purpose: str = ""
    report_as_of: str = ""
    is_current: bool = False
    validity: str = "invalid"
    report_blocked: bool = False
    quarantine: bool = False
    item_code: str | None = None
    formula_id: str | None = None
    formula_version: str | None = None
    period: str | None = None
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise SchemaValidationError("FinancialSnapshotAuthorityAssessment.snapshot_id 必须非空")
        _get_enum(self.validity, FINANCIAL_VALIDITIES, "FinancialSnapshotAuthorityAssessment", "validity")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "FinancialSnapshotAuthorityAssessment", "verdict")
        # Fix 2：formula_id 存在 → 必须可验证 formula_version；item-only 不得伪造公式版本。
        if self.formula_id is not None and not self.formula_version:
            raise SchemaValidationError(
                "FinancialSnapshotAuthorityAssessment 引用 formula_id 必须携带 formula_version")
        if self.formula_id is None and self.formula_version is not None:
            raise SchemaValidationError(
                "FinancialSnapshotAuthorityAssessment.formula_version 不得脱离 formula_id 存在")

    def to_dict(self) -> dict:
        return {
            "authority_type": self.authority_type,
            "snapshot_id": self.snapshot_id,
            "company_id": self.company_id,
            "scope": self.scope,
            "currency": self.currency,
            "purpose": self.purpose,
            "report_as_of": self.report_as_of,
            "is_current": self.is_current,
            "validity": self.validity,
            "report_blocked": self.report_blocked,
            "quarantine": self.quarantine,
            "item_code": self.item_code,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "period": self.period,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialSnapshotAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "snapshot_id", "company_id", "scope", "currency",
                                "purpose", "report_as_of", "is_current", "validity", "report_blocked",
                                "quarantine", "item_code", "formula_id", "formula_version", "period",
                                "verdict", "reason", "validator_version"}, "FinancialSnapshotAuthorityAssessment")
        if d.get("authority_type") not in (None, "financial_snapshot"):
            raise SchemaValidationError(
                f"FinancialSnapshotAuthorityAssessment.authority_type 必须为 'financial_snapshot'，"
                f"得到 {d.get('authority_type')!r}")
        return cls(
            snapshot_id=_get_str(d, "snapshot_id", "FinancialSnapshotAuthorityAssessment"),
            company_id=_get_str(d, "company_id", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            scope=_get_str(d, "scope", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            currency=_get_str(d, "currency", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            purpose=_get_str(d, "purpose", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            report_as_of=_get_str(d, "report_as_of", "FinancialSnapshotAuthorityAssessment",
                                  allow_empty=True) or "",
            is_current=_get_bool(d, "is_current", "FinancialSnapshotAuthorityAssessment"),
            validity=_get_str(d, "validity", "FinancialSnapshotAuthorityAssessment"),
            report_blocked=_get_bool(d, "report_blocked", "FinancialSnapshotAuthorityAssessment"),
            quarantine=_get_bool(d, "quarantine", "FinancialSnapshotAuthorityAssessment"),
            item_code=_get_str(d, "item_code", "FinancialSnapshotAuthorityAssessment", allow_none=True),
            formula_id=_get_str(d, "formula_id", "FinancialSnapshotAuthorityAssessment", allow_none=True),
            formula_version=_get_str(d, "formula_version", "FinancialSnapshotAuthorityAssessment",
                                     allow_none=True),
            period=_get_str(d, "period", "FinancialSnapshotAuthorityAssessment", allow_none=True),
            verdict=_get_str(d, "verdict", "FinancialSnapshotAuthorityAssessment"),
            reason=_get_str(d, "reason", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            validator_version=_get_str(d, "validator_version", "FinancialSnapshotAuthorityAssessment",
                                       allow_empty=True) or "",
        )


@dataclass(frozen=True)
class ExternalSnapshotAuthorityAssessment:
    """ExternalSnapshot 权威（fetched 正文/hash/URL/domain/日期/时间资格/A·B·C·D/独立性）。"""

    authority_type: str = field(init=False, default="external_snapshot")
    source_snapshot_id: str = ""
    canonical_url: str = ""
    domain: str = ""
    fetched_nonempty: bool = False
    content_hash: str = ""
    published_at: str | None = None
    time_qualified: bool = False
    source_grade: str = "D"
    min_grade_met: bool = False
    independence_domain: str = ""
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.source_snapshot_id:
            raise SchemaValidationError("ExternalSnapshotAuthorityAssessment.source_snapshot_id 必须非空")
        _get_enum(self.source_grade, SOURCE_GRADES, "ExternalSnapshotAuthorityAssessment", "source_grade")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "ExternalSnapshotAuthorityAssessment", "verdict")

    def to_dict(self) -> dict:
        return {
            "authority_type": self.authority_type,
            "source_snapshot_id": self.source_snapshot_id,
            "canonical_url": self.canonical_url,
            "domain": self.domain,
            "fetched_nonempty": self.fetched_nonempty,
            "content_hash": self.content_hash,
            "published_at": self.published_at,
            "time_qualified": self.time_qualified,
            "source_grade": self.source_grade,
            "min_grade_met": self.min_grade_met,
            "independence_domain": self.independence_domain,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalSnapshotAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "source_snapshot_id", "canonical_url", "domain",
                                "fetched_nonempty", "content_hash", "published_at", "time_qualified",
                                "source_grade", "min_grade_met", "independence_domain", "verdict",
                                "reason", "validator_version"}, "ExternalSnapshotAuthorityAssessment")
        if d.get("authority_type") not in (None, "external_snapshot"):
            raise SchemaValidationError(
                f"ExternalSnapshotAuthorityAssessment.authority_type 必须为 'external_snapshot'，"
                f"得到 {d.get('authority_type')!r}")
        return cls(
            source_snapshot_id=_get_str(d, "source_snapshot_id", "ExternalSnapshotAuthorityAssessment"),
            canonical_url=_get_str(d, "canonical_url", "ExternalSnapshotAuthorityAssessment",
                                   allow_empty=True) or "",
            domain=_get_str(d, "domain", "ExternalSnapshotAuthorityAssessment", allow_empty=True) or "",
            fetched_nonempty=_get_bool(d, "fetched_nonempty", "ExternalSnapshotAuthorityAssessment"),
            content_hash=_get_str(d, "content_hash", "ExternalSnapshotAuthorityAssessment",
                                  allow_empty=True) or "",
            published_at=_get_str(d, "published_at", "ExternalSnapshotAuthorityAssessment", allow_none=True),
            time_qualified=_get_bool(d, "time_qualified", "ExternalSnapshotAuthorityAssessment"),
            source_grade=_get_str(d, "source_grade", "ExternalSnapshotAuthorityAssessment"),
            min_grade_met=_get_bool(d, "min_grade_met", "ExternalSnapshotAuthorityAssessment"),
            independence_domain=_get_str(d, "independence_domain", "ExternalSnapshotAuthorityAssessment",
                                         allow_empty=True) or "",
            verdict=_get_str(d, "verdict", "ExternalSnapshotAuthorityAssessment"),
            reason=_get_str(d, "reason", "ExternalSnapshotAuthorityAssessment", allow_empty=True) or "",
            validator_version=_get_str(d, "validator_version", "ExternalSnapshotAuthorityAssessment",
                                       allow_empty=True) or "",
        )


AuthorityAssessment = (
    EvidenceAuthorityAssessment
    | FinancialSnapshotAuthorityAssessment
    | ExternalSnapshotAuthorityAssessment
)


def authority_from_dict(d: Any) -> AuthorityAssessment:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"AuthorityAssessment 需要 dict，得到 {type(d).__name__}")
    t = d.get("authority_type")
    if t == "evidence":
        return EvidenceAuthorityAssessment.from_dict(d)
    if t == "financial_snapshot":
        return FinancialSnapshotAuthorityAssessment.from_dict(d)
    if t == "external_snapshot":
        return ExternalSnapshotAuthorityAssessment.from_dict(d)
    raise SchemaValidationError(f"未知 authority_type: {t!r}")


# ---------------------------------------------------------------------------
# material / payload / fact / citation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitationRef:
    """一条可回查引用（三类 ref_type，与 harness.schema.CITATION_TYPES 一致）。"""

    ref_type: str
    evidence_id: str | None = None
    evidence_fact_id: str | None = None
    snapshot_id: str | None = None
    item_code: str | None = None
    formula_id: str | None = None
    formula_version: str | None = None
    period: str | None = None
    source_snapshot_id: str | None = None
    page_number: int | None = None

    def __post_init__(self) -> None:
        _get_enum(self.ref_type, CITATION_TYPES, "CitationRef", "ref_type")

    def to_dict(self) -> dict:
        return {
            "ref_type": self.ref_type,
            "evidence_id": self.evidence_id,
            "evidence_fact_id": self.evidence_fact_id,
            "snapshot_id": self.snapshot_id,
            "item_code": self.item_code,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "period": self.period,
            "source_snapshot_id": self.source_snapshot_id,
            "page_number": self.page_number,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "CitationRef":
        d = _reject_unknown(d, {"ref_type", "evidence_id", "evidence_fact_id", "snapshot_id",
                                "item_code", "formula_id", "formula_version", "period",
                                "source_snapshot_id", "page_number"}, "CitationRef")
        return cls(
            ref_type=_get_str(d, "ref_type", "CitationRef"),
            evidence_id=_get_str(d, "evidence_id", "CitationRef", allow_none=True),
            evidence_fact_id=_get_str(d, "evidence_fact_id", "CitationRef", allow_none=True),
            snapshot_id=_get_str(d, "snapshot_id", "CitationRef", allow_none=True),
            item_code=_get_str(d, "item_code", "CitationRef", allow_none=True),
            formula_id=_get_str(d, "formula_id", "CitationRef", allow_none=True),
            formula_version=_get_str(d, "formula_version", "CitationRef", allow_none=True),
            period=_get_str(d, "period", "CitationRef", allow_none=True),
            source_snapshot_id=_get_str(d, "source_snapshot_id", "CitationRef", allow_none=True),
            page_number=_get_int(d, "page_number", "CitationRef", allow_none=True),
        )


@dataclass(frozen=True)
class MaterialPayloadRef:
    """§7 不可变解析引用（替代无法验证的裸字符串）。"""

    object_type: str
    authority_identity: str
    version: str
    content_hash: str
    locator: MaterialLocator
    created_dependency_fingerprint: str

    def __post_init__(self) -> None:
        _get_enum(self.object_type, MATERIAL_TYPES, "MaterialPayloadRef", "object_type")
        if not self.authority_identity:
            raise SchemaValidationError("MaterialPayloadRef.authority_identity 必须非空")
        if not self.version:
            raise SchemaValidationError("MaterialPayloadRef.version 必须非空")
        if not _is_sha256_hex(self.content_hash):
            raise SchemaValidationError("MaterialPayloadRef.content_hash 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.created_dependency_fingerprint):
            raise SchemaValidationError(
                "MaterialPayloadRef.created_dependency_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "authority_identity": self.authority_identity,
            "version": self.version,
            "content_hash": self.content_hash,
            "locator": self.locator.to_dict(),
            "created_dependency_fingerprint": self.created_dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "MaterialPayloadRef":
        d = _reject_unknown(d, {"object_type", "authority_identity", "version", "content_hash",
                                "locator", "created_dependency_fingerprint"}, "MaterialPayloadRef")
        return cls(
            object_type=_get_str(d, "object_type", "MaterialPayloadRef"),
            authority_identity=_get_str(d, "authority_identity", "MaterialPayloadRef"),
            version=_get_str(d, "version", "MaterialPayloadRef"),
            content_hash=_get_str(d, "content_hash", "MaterialPayloadRef"),
            locator=locator_from_dict(_as_dict(d.get("locator"), "MaterialPayloadRef", "locator")),
            created_dependency_fingerprint=_get_str(d, "created_dependency_fingerprint",
                                                    "MaterialPayloadRef", allow_empty=True) or "",
        )


@dataclass(frozen=True)
class ResolvedPayload:
    """payload resolver 的解析结果（不可变 payload 的身份 + 内容哈希）。"""

    object_type: str
    authority_identity: str
    version: str
    locator: MaterialLocator
    content_hash: str
    payload_bytes: bytes | None = None


class PayloadResolver(Protocol):
    """R1-B 最小 payload resolver 边界（依赖注入；不直接依赖 Evidence/Financial/External Store）。"""

    def resolve(self, payload_ref: MaterialPayloadRef) -> ResolvedPayload | None:
        """解析 payload_ref 到不可变 payload；dangling（目标不存在）→ None。"""
        ...


def verify_material_payload_ref(payload_ref: MaterialPayloadRef,
                                resolver: PayloadResolver) -> ResolvedPayload:
    """校验 payload_ref 可解析且身份/哈希一致。

    dangling / object_type 不符 / authority_identity 不符 / version 不符 / locator 不一致 /
    content_hash 不符 / payload 字节哈希不匹配 → 全部 fail-closed（SchemaValidationError）。
    """
    resolved = resolver.resolve(payload_ref)
    if resolved is None:
        raise SchemaValidationError(
            f"MaterialPayloadRef dangling：{payload_ref.object_type}:{payload_ref.authority_identity}"
            f"@{payload_ref.version}")
    if resolved.object_type != payload_ref.object_type:
        raise SchemaValidationError(
            f"MaterialPayloadRef object_type 不符：期望 {payload_ref.object_type!r}，"
            f"得到 {resolved.object_type!r}")
    if resolved.authority_identity != payload_ref.authority_identity:
        raise SchemaValidationError(
            f"MaterialPayloadRef authority_identity 不符：期望 {payload_ref.authority_identity!r}，"
            f"得到 {resolved.authority_identity!r}")
    if resolved.version != payload_ref.version:
        raise SchemaValidationError(
            f"MaterialPayloadRef version 不符：期望 {payload_ref.version!r}，得到 {resolved.version!r}")
    if resolved.locator.to_dict() != payload_ref.locator.to_dict():
        raise SchemaValidationError("MaterialPayloadRef locator 与解析目标不一致")
    if resolved.content_hash != payload_ref.content_hash:
        raise SchemaValidationError(
            f"MaterialPayloadRef content_hash 不符：期望 {payload_ref.content_hash!r}，"
            f"得到 {resolved.content_hash!r}")
    if resolved.payload_bytes is not None:
        if hashlib.sha256(resolved.payload_bytes).hexdigest() != payload_ref.content_hash:
            raise SchemaValidationError("MaterialPayloadRef payload 内容哈希不匹配")
    return resolved


def recompute_authority_verdict(authority: AuthorityAssessment) -> str:
    """确定性重算权威结论（不信任调用方自填 verdict）。

    权威门只判断「来源及事实载体是否真实、完整、可回查、版本有效」，不判断「该来源能否独立
    证明公司级结论」（后者属于 aspect usage-scope gate / sufficiency gate，见 Fix 1/Fix 4）。

    - Evidence / Financial：满足该来源类型支持「正式事实」的全部资格字段 → authoritative。
      Financial 的 item_code / formula_id 至少一个有效（Fix 2：item-only / formula-only 合法）。
    - External：fetched 正文非空 + content_hash 有效 + canonical URL/domain 有效 + grade != D
      + 时间资格有效 + 来源身份一致 → authoritative（A/B/C 单条 external 事实可过权威门，
      但「权威」≠「充分」，是否可作 formal 行业事实由 usage-scope gate 判定；D → rejected）。
    """
    if isinstance(authority, EvidenceAuthorityAssessment):
        ok = (authority.is_current_document and authority.is_current_set
              and authority.fetched_inspected_nonempty
              and bool(authority.document_id) and bool(authority.company_id)
              and (authority.page is not None or authority.block_range is not None)
              and _is_sha256_hex(authority.content_hash))
        return "authoritative" if ok else "rejected"
    if isinstance(authority, FinancialSnapshotAuthorityAssessment):
        ok = (authority.is_current and authority.validity == "valid"
              and not authority.report_blocked and not authority.quarantine
              and bool(authority.company_id) and bool(authority.scope)
              and bool(authority.currency) and bool(authority.purpose)
              and bool(authority.report_as_of)
              and (authority.item_code is not None or authority.formula_id is not None)
              and authority.period is not None)
        return "authoritative" if ok else "rejected"
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        ok = (authority.fetched_nonempty and _is_sha256_hex(authority.content_hash)
              and bool(authority.canonical_url) and bool(authority.domain)
              and authority.source_grade != "D" and authority.min_grade_met
              and authority.time_qualified)
        return "authoritative" if ok else "rejected"
    raise TypeError(f"未知 authority 类型: {type(authority).__name__}")


def authority_source_identity(authority: AuthorityAssessment) -> str:
    """三类权威的来源身份字符串（material authority ↔ fact source_authority ↔ CitationRef 一致性域）。"""
    if isinstance(authority, EvidenceAuthorityAssessment):
        return f"evidence:{authority.evidence_id}"
    if isinstance(authority, FinancialSnapshotAuthorityAssessment):
        return f"financial_snapshot:{authority.snapshot_id}"
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return f"external_snapshot:{authority.source_snapshot_id}"
    raise TypeError(f"未知 authority 类型: {type(authority).__name__}")


def citation_source_identity(citation: CitationRef) -> str:
    """CitationRef 的来源身份（与 authority_source_identity 同域，供一致性比对）。"""
    if citation.ref_type == "evidence":
        return f"evidence:{citation.evidence_id or ''}"
    if citation.ref_type == "structured":
        return f"financial_snapshot:{citation.snapshot_id or ''}"
    if citation.ref_type == "external":
        return f"external_snapshot:{citation.source_snapshot_id or ''}"
    raise SchemaValidationError(f"未知 CitationRef.ref_type: {citation.ref_type!r}")


def authority_source_class(authority: AuthorityAssessment) -> str:
    """三类权威 → 来源类（source_class）。用于 usage-scope gate（Fix 1）。"""
    if isinstance(authority, EvidenceAuthorityAssessment):
        return "company_industry"
    if isinstance(authority, FinancialSnapshotAuthorityAssessment):
        return "structured_db"
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return "external"
    raise TypeError(f"未知 authority 类型: {type(authority).__name__}")


def authority_source_grade(authority: AuthorityAssessment) -> str | None:
    """来源等级（A/B/C/D）。仅 external 有 grade；evidence/financial 为公司披露/结构化，
    不属于 external 分级体系 → None。"""
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return authority.source_grade
    return None


def authority_independence_domain(authority: AuthorityAssessment) -> str | None:
    """独立性域（仅 external 有意义，用于 sufficiency 的「≥2 相互独立 C」复算）。"""
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return authority.independence_domain or None
    return None


def _material_consistency(material_type: str, locator: MaterialLocator,
                          authority: AuthorityAssessment) -> None:
    """material type ↔ locator 变体 ↔ authority 变体 三者必须匹配（§8 强制不变量）。"""
    exp_loc = _LOCATOR_TYPE_BY_MATERIAL.get(material_type)
    exp_auth = _AUTHORITY_TYPE_BY_MATERIAL.get(material_type)
    if exp_loc is None:
        raise SchemaValidationError(f"未知 material_type: {material_type!r}")
    if locator.locator_type != exp_loc:
        raise SchemaValidationError(
            f"material_type={material_type!r} 要求 locator_type={exp_loc!r}，"
            f"得到 {locator.locator_type!r}")
    if authority.authority_type != exp_auth:
        raise SchemaValidationError(
            f"material_type={material_type!r} 要求 authority_type={exp_auth!r}，"
            f"得到 {authority.authority_type!r}")
    # Fix 2：structured 的 locator↔authority 必须形成完整财务身份闭环。
    # item-only / formula-only / 双身份 各按规则强制一致，跨身份错配、无谓 formula、版本/period
    # 不对称一律拒绝（不允许 locator 与 authority 跨身份错配，也不允许 item-only 伪造 formula）。
    if material_type == "structured":
        validate_financial_identity(locator, authority)


def validate_financial_identity(locator: "FinancialLocator",
                                authority: "FinancialSnapshotAuthorityAssessment") -> None:
    """locator ↔ authority 财务身份闭环（snapshot/item/formula/formula_version/period）。"""
    if locator.snapshot_id != authority.snapshot_id:
        raise SchemaValidationError(
            f"financial locator.snapshot_id={locator.snapshot_id!r} 与 "
            f"authority.snapshot_id={authority.snapshot_id!r} 不一致")
    if (locator.period or "") != (authority.period or ""):
        raise SchemaValidationError(
            f"financial locator.period={locator.period!r} 与 "
            f"authority.period={authority.period!r} 不一致")
    # item 身份：两侧必须一致地给出/缺失，且值相等（item-only 不得混入 formula）。
    if (locator.item_code is not None) != (authority.item_code is not None):
        raise SchemaValidationError(
            "financial item_code 身份不对称：locator/authority 一侧 item-only 另一侧无 item")
    if locator.item_code is not None and locator.item_code != authority.item_code:
        raise SchemaValidationError(
            f"financial locator.item_code={locator.item_code!r} 与 "
            f"authority.item_code={authority.item_code!r} 不一致")
    # formula 身份：两侧必须一致地给出/缺失，formula_id + formula_version 都相等。
    if (locator.formula_id is not None) != (authority.formula_id is not None):
        raise SchemaValidationError(
            "financial formula_id 身份不对称：locator/authority 一侧 formula-only 另一侧无 formula")
    if locator.formula_id is not None and locator.formula_id != authority.formula_id:
        raise SchemaValidationError(
            f"financial locator.formula_id={locator.formula_id!r} 与 "
            f"authority.formula_id={authority.formula_id!r} 不一致")
    if locator.formula_id is not None and (locator.formula_version or "") != (authority.formula_version or ""):
        raise SchemaValidationError(
            f"financial locator.formula_version={locator.formula_version!r} 与 "
            f"authority.formula_version={authority.formula_version!r} 不一致")


@dataclass(frozen=True)
class ResearchMaterial:
    """一个 material（evidence_span/table_context/structured/external_snapshot）。"""

    material_id: str
    material_type: str
    source_identity: str
    locator: MaterialLocator
    payload_ref: MaterialPayloadRef
    content_hash: str
    authority_assessment: AuthorityAssessment
    context_parent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.material_id:
            raise SchemaValidationError("ResearchMaterial.material_id 必须非空")
        _get_enum(self.material_type, MATERIAL_TYPES, "ResearchMaterial", "material_type")
        if not self.content_hash:
            raise SchemaValidationError("ResearchMaterial.content_hash 必须非空")
        if not _is_sha256_hex(self.content_hash):
            raise SchemaValidationError("ResearchMaterial.content_hash 必须为 64 位 sha256 hex")
        _material_consistency(self.material_type, self.locator, self.authority_assessment)
        # material 与 payload_ref 的 typed 身份必须严格一致（§三.1：不得只验证 resolver 自报字段）。
        if self.payload_ref.object_type != self.material_type:
            raise SchemaValidationError(
                f"ResearchMaterial.material_type={self.material_type!r} 与 "
                f"payload_ref.object_type={self.payload_ref.object_type!r} 不一致")
        if self.payload_ref.locator.to_dict() != self.locator.to_dict():
            raise SchemaValidationError("ResearchMaterial.locator 与 payload_ref.locator 不一致")
        # §三.1：material 载体层哈希必须与 payload_ref.content_hash 严格一致（== payload_hash ==
        #       sha256(payload_bytes)）；两者不一致立即 fail-closed，绝不把载体身份与引用身份拆开。
        if self.content_hash != self.payload_ref.content_hash:
            raise SchemaValidationError(
                f"ResearchMaterial.content_hash={self.content_hash!r} 与 "
                f"payload_ref.content_hash={self.payload_ref.content_hash!r} 不一致")
        # §四.1：来源身份三方严格一致（source_identity == payload_ref.authority_identity
        #       == authority_source_identity(authority_assessment)）。
        auth_identity = authority_source_identity(self.authority_assessment)
        if self.source_identity != self.payload_ref.authority_identity:
            raise SchemaValidationError(
                f"ResearchMaterial.source_identity={self.source_identity!r} 与 "
                f"payload_ref.authority_identity={self.payload_ref.authority_identity!r} 不一致")
        if self.source_identity != auth_identity:
            raise SchemaValidationError(
                f"ResearchMaterial.source_identity={self.source_identity!r} 与 "
                f"authority_source_identity={auth_identity!r} 不一致")

    def to_dict(self) -> dict:
        return {
            "material_id": self.material_id,
            "material_type": self.material_type,
            "source_identity": self.source_identity,
            "locator": self.locator.to_dict(),
            "payload_ref": self.payload_ref.to_dict(),
            "context_parent_id": self.context_parent_id,
            "content_hash": self.content_hash,
            "authority_assessment": self.authority_assessment.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchMaterial":
        d = _reject_unknown(d, {"material_id", "material_type", "source_identity", "locator",
                                "payload_ref", "context_parent_id", "content_hash",
                                "authority_assessment"}, "ResearchMaterial")
        return cls(
            material_id=_get_str(d, "material_id", "ResearchMaterial"),
            material_type=_get_str(d, "material_type", "ResearchMaterial"),
            source_identity=_get_str(d, "source_identity", "ResearchMaterial", allow_empty=True) or "",
            locator=locator_from_dict(_as_dict(d.get("locator"), "ResearchMaterial", "locator")),
            payload_ref=MaterialPayloadRef.from_dict(_as_dict(d.get("payload_ref"), "ResearchMaterial",
                                                             "payload_ref")),
            context_parent_id=_get_str(d, "context_parent_id", "ResearchMaterial", allow_none=True),
            content_hash=_get_str(d, "content_hash", "ResearchMaterial"),
            authority_assessment=authority_from_dict(
                _as_dict(d.get("authority_assessment"), "ResearchMaterial", "authority_assessment")),
        )


@dataclass(frozen=True)
class ValueIdentity:
    """规范化数字语义（替代裸 dict）。"""

    value_kind: str
    metric: str
    unit: str
    period: str
    scope: str
    amount_canonical: str

    def __post_init__(self) -> None:
        if not self.value_kind or not self.metric:
            raise SchemaValidationError("ValueIdentity.value_kind/metric 必须非空")
        if not self.amount_canonical:
            raise SchemaValidationError("ValueIdentity.amount_canonical 必须非空")

    def to_dict(self) -> dict:
        return {
            "value_kind": self.value_kind,
            "metric": self.metric,
            "unit": self.unit,
            "period": self.period,
            "scope": self.scope,
            "amount_canonical": self.amount_canonical,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ValueIdentity":
        d = _reject_unknown(d, {"value_kind", "metric", "unit", "period", "scope",
                                "amount_canonical"}, "ValueIdentity")
        return cls(
            value_kind=_get_str(d, "value_kind", "ValueIdentity"),
            metric=_get_str(d, "metric", "ValueIdentity"),
            unit=_get_str(d, "unit", "ValueIdentity", allow_empty=True) or "",
            period=_get_str(d, "period", "ValueIdentity", allow_empty=True) or "",
            scope=_get_str(d, "scope", "ValueIdentity", allow_empty=True) or "",
            amount_canonical=_get_str(d, "amount_canonical", "ValueIdentity"),
        )


@dataclass(frozen=True)
class InferenceLineage:
    """条件性行业传导的推断血缘（Fix 3 类型化载体，绑定 inference SupportedFact）。

    字段名与冻结 Contract `er_ind_transmission_conditional.authority.inference_lineage.fields`
    逐一对应（inference_policy_ref / channel / direction / conditions / limitation /
    derived_from_fact_ids），另附版本化 rule_version。缺任一必填字段 → 构造即 fail-closed。
    """

    inference_policy_ref: str
    rule_version: str
    channel: str
    direction: str
    conditions: tuple[str, ...]
    limitation: tuple[str, ...]
    derived_from_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.inference_policy_ref:
            raise SchemaValidationError("InferenceLineage.inference_policy_ref 必须非空")
        if not self.rule_version:
            raise SchemaValidationError("InferenceLineage.rule_version 必须非空")
        if not self.channel:
            raise SchemaValidationError("InferenceLineage.channel 必须非空")
        _get_enum(self.direction, INFERENCE_DIRECTIONS, "InferenceLineage", "direction")
        if not self.conditions:
            raise SchemaValidationError("InferenceLineage.conditions 必须非空")
        if not self.limitation:
            raise SchemaValidationError("InferenceLineage.limitation 必须非空")
        if not self.derived_from_fact_ids:
            raise SchemaValidationError("InferenceLineage.derived_from_fact_ids 必须非空")
        if len(set(self.derived_from_fact_ids)) != len(self.derived_from_fact_ids):
            raise SchemaValidationError("InferenceLineage.derived_from_fact_ids 不得重复")

    def to_dict(self) -> dict:
        return {
            "inference_policy_ref": self.inference_policy_ref,
            "rule_version": self.rule_version,
            "channel": self.channel,
            "direction": self.direction,
            "conditions": list(self.conditions),
            "limitation": list(self.limitation),
            "derived_from_fact_ids": list(self.derived_from_fact_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "InferenceLineage":
        d = _reject_unknown(d, {"inference_policy_ref", "rule_version", "channel", "direction",
                                "conditions", "limitation", "derived_from_fact_ids"}, "InferenceLineage")
        return cls(
            inference_policy_ref=_get_str(d, "inference_policy_ref", "InferenceLineage"),
            rule_version=_get_str(d, "rule_version", "InferenceLineage"),
            channel=_get_str(d, "channel", "InferenceLineage"),
            direction=_get_str(d, "direction", "InferenceLineage"),
            conditions=_get_str_tuple(d, "conditions", "InferenceLineage"),
            limitation=_get_str_tuple(d, "limitation", "InferenceLineage"),
            derived_from_fact_ids=_get_str_tuple(d, "derived_from_fact_ids", "InferenceLineage"),
        )


# ---------------------------------------------------------------------------
# M930-3 材料处置 / 候选 / 资格决定 / authority-specific qualified result
# 单向链：FactCandidate → FactQualificationDecision → qualified result
# ---------------------------------------------------------------------------

# 各 successor 类型的 wire 关键字（用于同型冒充的显式拒绝）。
CANDIDATE_FORBIDDEN_KEYS = ("fact_id", "external_fact_id", "qualification_decision_id",
                            "material_id", "payload_ref")
DECISION_FORBIDDEN_KEYS = ("fact_id", "external_fact_id", "text", "candidate_text")
FOLLOW_UP_NEED_FORBIDDEN_KEYS = ("result", "execution_result", "pack_id", "pack_ref",
                                "material_id", "material_ref", "outcome_refs")


def _derive_material_disposition_id(material_id: str, admission_state: str,
                                    retention_state: str, reason_code: str,
                                    policy_version: str) -> str:
    return "rmd_" + sha256_canonical({
        "kind": "research_material_disposition",
        "version": MATERIAL_DISPOSITION_VERSION,
        "material_id": material_id,
        "admission_state": admission_state,
        "retention_state": retention_state,
        "reason_code": reason_code,
        "policy_version": policy_version,
    })[:32]


def material_container_identity(material: "ResearchMaterial") -> str:
    """material 的容器身份（按 locator 变体确定性派生，不跨变体混用）。"""
    loc = material.locator
    if isinstance(loc, EvidenceLocator):
        return f"evidence_document:{loc.document_id}@{loc.document_version}"
    if isinstance(loc, FinancialLocator):
        return f"financial_snapshot:{loc.snapshot_id}"
    if isinstance(loc, ExternalLocator):
        return f"external_snapshot:{loc.source_snapshot_id}"
    raise SchemaValidationError(f"未知 locator 类型: {type(loc).__name__}")


def material_provenance_identity(material: "ResearchMaterial") -> str:
    """material 的来源/版本 provenance 身份（authority + version + 依赖指纹）。"""
    pr = material.payload_ref
    return f"{pr.authority_identity}@{pr.version}#{pr.created_dependency_fingerprint}"


def material_content_fingerprint(material: "ResearchMaterial") -> str:
    """material 的内容身份（内容哈希 + 精确 locator + 来源身份）。"""
    return sha256_canonical({
        "kind": "research_material_content",
        "material_id": material.material_id,
        "material_type": material.material_type,
        "content_hash": material.content_hash,
        "locator": material.locator.to_dict(),
        "source_identity": material.source_identity,
    })


def build_material_disposition(material: "ResearchMaterial", *, aspect_ids: tuple[str, ...],
                               admission_state: str, retention_state: str,
                               source_validation: str, reason_code: str, reason_proof: str,
                               policy_version: str) -> ResearchMaterialDisposition:
    """从 material + 两个处置轴 + typed 理由确定性构造 RMD（**唯一**构造实现）。

    runtime 与 pack-set/verifier 使用同一函数重算，因此「material ↔ RMD」的等式
    不依赖任何一方自报。
    """
    return ResearchMaterialDisposition(
        disposition_id=_derive_material_disposition_id(
            material.material_id, admission_state, retention_state, reason_code, policy_version),
        material_id=material.material_id,
        aspect_ids=aspect_ids,
        admission_state=admission_state,
        retention_state=retention_state,
        source_validation=source_validation,
        container_identity=material_container_identity(material),
        source_identity=material.source_identity,
        provenance_identity=material_provenance_identity(material),
        reason_code=reason_code,
        reason_proof=reason_proof,
        policy_version=policy_version,
        content_fingerprint=material_content_fingerprint(material),
    )


@dataclass(frozen=True)
class ResearchMaterialDisposition:
    """一份 Pack material 的**研究侧**处置（§16.3 #2）。

    身份边界（不得混用）：

    - **不含** used / not_used 语义 —— 「本次写作是否用到」属于 Writer 侧
      ``WriterMaterialProcessingDisposition``（另一套 identity、另一持久化边界）；
    - **不是** rejection audit，**不是** gap：``admission_state="rejected"`` 只是材料侧结论，
      绝不自动产生 ``ContractGap``/``ResearchBlock``；
    - obligation 方向是 Pack 侧：「每个 material 恰一条 RMD」由 ``TopicResearchPack.__post_init__``
      与 pack-set 门（``sections/pack_set.py``）双重断言。
    """

    disposition_id: str
    material_id: str
    aspect_ids: tuple[str, ...]
    admission_state: str
    retention_state: str
    source_validation: str
    container_identity: str
    source_identity: str
    provenance_identity: str
    reason_code: str
    reason_proof: str
    policy_version: str
    content_fingerprint: str

    def __post_init__(self) -> None:
        if not self.disposition_id:
            raise SchemaValidationError("ResearchMaterialDisposition.disposition_id 必须非空")
        if not self.material_id:
            raise SchemaValidationError("ResearchMaterialDisposition.material_id 必须非空")
        _get_enum(self.admission_state, MATERIAL_ADMISSION_STATES,
                  "ResearchMaterialDisposition", "admission_state")
        _get_enum(self.retention_state, MATERIAL_RETENTION_STATES,
                  "ResearchMaterialDisposition", "retention_state")
        _get_enum(self.source_validation, MATERIAL_SOURCE_VALIDATIONS,
                  "ResearchMaterialDisposition", "source_validation")
        _get_enum(self.reason_code, MATERIAL_DISPOSITION_REASON_CODES,
                  "ResearchMaterialDisposition", "reason_code")
        if not self.reason_proof:
            raise SchemaValidationError("ResearchMaterialDisposition.reason_proof 必须非空")
        if not self.policy_version:
            raise SchemaValidationError("ResearchMaterialDisposition.policy_version 必须非空")
        for name, value in (("container_identity", self.container_identity),
                            ("source_identity", self.source_identity),
                            ("provenance_identity", self.provenance_identity)):
            if not value:
                raise SchemaValidationError(f"ResearchMaterialDisposition.{name} 必须非空")
        if not _is_sha256_hex(self.content_fingerprint):
            raise SchemaValidationError(
                "ResearchMaterialDisposition.content_fingerprint 必须为 64 位 sha256 hex")
        expected = _derive_material_disposition_id(
            self.material_id, self.admission_state, self.retention_state,
            self.reason_code, self.policy_version)
        if self.disposition_id != expected:
            raise SchemaValidationError(
                f"ResearchMaterialDisposition.disposition_id={self.disposition_id!r} "
                f"与确定性派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "disposition_id": self.disposition_id,
            "material_id": self.material_id,
            "aspect_ids": list(self.aspect_ids),
            "admission_state": self.admission_state,
            "retention_state": self.retention_state,
            "source_validation": self.source_validation,
            "container_identity": self.container_identity,
            "source_identity": self.source_identity,
            "provenance_identity": self.provenance_identity,
            "reason_code": self.reason_code,
            "reason_proof": self.reason_proof,
            "policy_version": self.policy_version,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchMaterialDisposition":
        d = _reject_unknown(d, {"disposition_id", "material_id", "aspect_ids", "admission_state",
                                "retention_state", "source_validation", "container_identity",
                                "source_identity", "provenance_identity", "reason_code",
                                "reason_proof", "policy_version", "content_fingerprint"},
                            "ResearchMaterialDisposition")
        return cls(
            disposition_id=_get_str(d, "disposition_id", "ResearchMaterialDisposition"),
            material_id=_get_str(d, "material_id", "ResearchMaterialDisposition"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "ResearchMaterialDisposition"),
            admission_state=_get_str(d, "admission_state", "ResearchMaterialDisposition"),
            retention_state=_get_str(d, "retention_state", "ResearchMaterialDisposition"),
            source_validation=_get_str(d, "source_validation", "ResearchMaterialDisposition"),
            container_identity=_get_str(d, "container_identity", "ResearchMaterialDisposition"),
            source_identity=_get_str(d, "source_identity", "ResearchMaterialDisposition"),
            provenance_identity=_get_str(d, "provenance_identity", "ResearchMaterialDisposition"),
            reason_code=_get_str(d, "reason_code", "ResearchMaterialDisposition"),
            reason_proof=_get_str(d, "reason_proof", "ResearchMaterialDisposition"),
            policy_version=_get_str(d, "policy_version", "ResearchMaterialDisposition"),
            content_fingerprint=_get_str(d, "content_fingerprint", "ResearchMaterialDisposition"),
        )


def derive_fact_candidate_id(candidate_source_kind: str, statement: str,
                             aspect_ids: tuple[str, ...],
                             material_ids: tuple[str, ...],
                             source_snapshot_id: str | None) -> str:
    """候选身份 = 来源类别 + 原子命题 + aspect + 该来源类别的输入身份。"""
    return "fc_" + sha256_canonical({
        "kind": "fact_candidate",
        "candidate_source_kind": candidate_source_kind,
        "statement": statement,
        "aspect_ids": list(aspect_ids),
        "material_ids": list(material_ids),
        "source_snapshot_id": source_snapshot_id,
    })[:32]


@dataclass(frozen=True)
class FactCandidate:
    """一条**候选**原子事实（§16.3 #3），与任何 qualified result 身份严格分离。

    fail-closed 边界：

    - 候选声明封闭的 ``candidate_source_kind``（``topic_material`` / ``external_source``）；
      二者输入形状互斥：``topic_material`` 必须给 ``material_ids`` 且不得给 snapshot；
      ``external_source`` 必须给 ``source_snapshot_id`` 且不得给 ``material_ids``；
    - 候选**不得**携带已采纳身份 ``fact_id`` / ``external_fact_id`` / ``qualification_decision_id``
      （那是资格门之后的产物）；``from_dict`` 对上述键显式拒绝；
    - 候选也**不得**携带 ``payload_ref`` —— 载荷/定位属于 material 或 snapshot 载体，
      由资格决定引用，不属于候选身份。
    """

    candidate_id: str
    candidate_revision: str
    candidate_source_kind: str
    statement: str
    fact_type: str
    aspect_ids: tuple[str, ...]
    question_ids: tuple[str, ...]
    material_ids: tuple[str, ...] = ()
    source_snapshot_id: str | None = None
    content_hash: str = ""
    semantic_tags: tuple[str, ...] = ()
    period: str | None = None
    scope: str | None = None
    confidence: str | None = None
    inference_lineage: InferenceLineage | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise SchemaValidationError("FactCandidate.candidate_id 必须非空")
        if not self.candidate_revision:
            raise SchemaValidationError("FactCandidate.candidate_revision 必须非空")
        _get_enum(self.candidate_source_kind, CANDIDATE_SOURCE_KINDS, "FactCandidate",
                  "candidate_source_kind")
        if not self.statement:
            raise SchemaValidationError("FactCandidate.statement 必须非空")
        _get_enum(self.fact_type, FACT_TYPES, "FactCandidate", "fact_type")
        if not self.aspect_ids:
            raise SchemaValidationError("FactCandidate.aspect_ids 必须非空")
        if self.candidate_source_kind == "topic_material":
            if not self.material_ids:
                raise SchemaValidationError(
                    "FactCandidate.candidate_source_kind=topic_material 必须给出 material_ids")
            if self.source_snapshot_id is not None:
                raise SchemaValidationError(
                    "FactCandidate.candidate_source_kind=topic_material 不得携带 source_snapshot_id")
        else:
            if self.material_ids:
                raise SchemaValidationError(
                    "FactCandidate.candidate_source_kind=external_source 不得携带 material_ids")
            if not self.source_snapshot_id:
                raise SchemaValidationError(
                    "FactCandidate.candidate_source_kind=external_source 必须给出 source_snapshot_id")
        if self.fact_type == "inference" and self.inference_lineage is None:
            raise SchemaValidationError("FactCandidate.fact_type=inference 必须携带 inference_lineage")
        if self.fact_type == "fact" and self.inference_lineage is not None:
            raise SchemaValidationError("FactCandidate.fact_type=fact 不得携带 inference_lineage")
        if self.confidence is not None and self.confidence not in ("high", "low"):
            raise SchemaValidationError(f"FactCandidate.confidence 非法: {self.confidence!r}")
        expected = derive_fact_candidate_id(self.candidate_source_kind, self.statement,
                                            self.aspect_ids, self.material_ids,
                                            self.source_snapshot_id)
        if self.candidate_id != expected:
            raise SchemaValidationError(
                f"FactCandidate.candidate_id={self.candidate_id!r} 与确定性派生值 {expected!r} 不符")

    def compute_revision(self) -> str:
        """候选 revision = 候选内容规范形哈希（改命题/来源/标签即改 revision）。"""
        return "fcr_" + sha256_canonical({
            "kind": "fact_candidate_revision",
            "candidate_id": self.candidate_id,
            "candidate_source_kind": self.candidate_source_kind,
            "statement": self.statement,
            "fact_type": self.fact_type,
            "aspect_ids": list(self.aspect_ids),
            "question_ids": list(self.question_ids),
            "material_ids": list(self.material_ids),
            "source_snapshot_id": self.source_snapshot_id,
            "content_hash": self.content_hash,
            "semantic_tags": list(self.semantic_tags),
            "period": self.period,
            "scope": self.scope,
            "confidence": self.confidence,
            "inference_lineage": self.inference_lineage.to_dict() if self.inference_lineage else None,
        })[:32]

    def verify_revision(self) -> None:
        expected = self.compute_revision()
        if self.candidate_revision != expected:
            raise SchemaValidationError(
                f"FactCandidate.candidate_revision={self.candidate_revision!r} "
                f"与内容派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "candidate_source_kind": self.candidate_source_kind,
            "statement": self.statement,
            "fact_type": self.fact_type,
            "aspect_ids": list(self.aspect_ids),
            "question_ids": list(self.question_ids),
            "material_ids": list(self.material_ids),
            "source_snapshot_id": self.source_snapshot_id,
            "content_hash": self.content_hash,
            "semantic_tags": list(self.semantic_tags),
            "period": self.period,
            "scope": self.scope,
            "confidence": self.confidence,
            "inference_lineage": self.inference_lineage.to_dict() if self.inference_lineage else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FactCandidate":
        d = _reject_unknown(d, {"candidate_id", "candidate_revision", "candidate_source_kind",
                                "statement", "fact_type", "aspect_ids", "question_ids",
                                "material_ids", "source_snapshot_id", "content_hash",
                                "semantic_tags", "period", "scope", "confidence",
                                "inference_lineage"}, "FactCandidate")
        for key in CANDIDATE_FORBIDDEN_KEYS:
            if key in d:
                raise SchemaValidationError(
                    f"FactCandidate 不得携带已采纳身份/载荷字段: {key!r}")
        il = d.get("inference_lineage")
        return cls(
            candidate_id=_get_str(d, "candidate_id", "FactCandidate"),
            candidate_revision=_get_str(d, "candidate_revision", "FactCandidate"),
            candidate_source_kind=_get_str(d, "candidate_source_kind", "FactCandidate"),
            statement=_get_str(d, "statement", "FactCandidate"),
            fact_type=_get_str(d, "fact_type", "FactCandidate"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "FactCandidate"),
            question_ids=_get_str_tuple(d, "question_ids", "FactCandidate"),
            material_ids=_get_str_tuple(d, "material_ids", "FactCandidate"),
            source_snapshot_id=_get_str(d, "source_snapshot_id", "FactCandidate", allow_none=True),
            content_hash=_get_str(d, "content_hash", "FactCandidate", allow_empty=True) or "",
            semantic_tags=_get_str_tuple(d, "semantic_tags", "FactCandidate"),
            period=_get_str(d, "period", "FactCandidate", allow_none=True),
            scope=_get_str(d, "scope", "FactCandidate", allow_none=True),
            confidence=_get_str(d, "confidence", "FactCandidate", allow_none=True),
            inference_lineage=InferenceLineage.from_dict(il) if il is not None else None,
        )


def derive_fact_qualification_decision_id(candidate_id: str, candidate_revision: str,
                                          candidate_source_kind: str, verdict: str,
                                          rules_version: str) -> str:
    return "fqd_" + sha256_canonical({
        "kind": "fact_qualification_decision",
        "version": FACT_QUALIFICATION_VERSION,
        "candidate_id": candidate_id,
        "candidate_revision": candidate_revision,
        "candidate_source_kind": candidate_source_kind,
        "verdict": verdict,
        "rules_version": rules_version,
    })[:32]


@dataclass(frozen=True)
class FactQualificationDecision:
    """版本化资格决定（§16.3 #4）—— 每个 candidate revision **恰一条**。

    身份/方向约束：

    - 决定身份**不得**引用尚未形成的 qualified result：本类型没有任何 ``fact_id`` /
      ``external_fact_id`` 字段，``from_dict`` 对这类键显式拒绝；
    - ``verdict="rejected"`` 的决定**本身就是该候选唯一的 typed rejection audit**，
      不另造第五种权威对象；rejection ≠ gap（gap 只在 Contract required 仍未取得时**另外**形成）；
    - ``eligible`` 决定必须能按 ``candidate_source_kind`` 恰好支撑一条 authority-specific
      qualified result（topic_material → ``SupportedFact``；external_source → ``ExternalFact``）；
      该 cardinality 由 Pack 门与 pack-set 门逐条重算。
    """

    decision_id: str
    candidate_id: str
    candidate_revision: str
    candidate_source_kind: str
    verdict: str
    rules_version: str
    input_identity_digest: str
    input_material_ids: tuple[str, ...] = ()
    input_snapshot_id: str | None = None
    input_source_identity: str = ""
    input_locator_digest: str = ""
    input_payload_digest: str = ""
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise SchemaValidationError("FactQualificationDecision.decision_id 必须非空")
        if not self.candidate_id:
            raise SchemaValidationError("FactQualificationDecision.candidate_id 必须非空")
        if not self.candidate_revision:
            raise SchemaValidationError("FactQualificationDecision.candidate_revision 必须非空")
        _get_enum(self.candidate_source_kind, CANDIDATE_SOURCE_KINDS,
                  "FactQualificationDecision", "candidate_source_kind")
        _get_enum(self.verdict, QUALIFICATION_VERDICTS, "FactQualificationDecision", "verdict")
        if not self.rules_version:
            raise SchemaValidationError("FactQualificationDecision.rules_version 必须非空")
        if not _is_sha256_hex(self.input_identity_digest):
            raise SchemaValidationError(
                "FactQualificationDecision.input_identity_digest 必须为 64 位 sha256 hex")
        # 输入形状按来源类别封闭：topic material 走 material 集合，external 走 snapshot。
        if self.candidate_source_kind == "topic_material":
            if not self.input_material_ids:
                raise SchemaValidationError(
                    "FactQualificationDecision(topic_material) 必须绑定输入 material 集合")
            if self.input_snapshot_id is not None:
                raise SchemaValidationError(
                    "FactQualificationDecision(topic_material) 不得绑定 input_snapshot_id")
        else:
            if self.input_material_ids:
                raise SchemaValidationError(
                    "FactQualificationDecision(external_source) 不得绑定 input_material_ids")
            if not self.input_snapshot_id:
                raise SchemaValidationError(
                    "FactQualificationDecision(external_source) 必须绑定 input_snapshot_id")
        if not self.input_source_identity:
            raise SchemaValidationError(
                "FactQualificationDecision.input_source_identity 必须非空")
        if not _is_sha256_hex(self.input_locator_digest):
            raise SchemaValidationError(
                "FactQualificationDecision.input_locator_digest 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.input_payload_digest):
            raise SchemaValidationError(
                "FactQualificationDecision.input_payload_digest 必须为 64 位 sha256 hex")
        if self.verdict == "rejected" and not self.rejection_reason:
            raise SchemaValidationError(
                "FactQualificationDecision.verdict=rejected 必须携带非空 rejection_reason")
        if self.verdict == "eligible" and self.rejection_reason is not None:
            raise SchemaValidationError(
                "FactQualificationDecision.verdict=eligible 不得携带 rejection_reason")
        expected = derive_fact_qualification_decision_id(
            self.candidate_id, self.candidate_revision, self.candidate_source_kind,
            self.verdict, self.rules_version)
        if self.decision_id != expected:
            raise SchemaValidationError(
                f"FactQualificationDecision.decision_id={self.decision_id!r} "
                f"与确定性派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "candidate_source_kind": self.candidate_source_kind,
            "verdict": self.verdict,
            "rules_version": self.rules_version,
            "input_identity_digest": self.input_identity_digest,
            "input_material_ids": list(self.input_material_ids),
            "input_snapshot_id": self.input_snapshot_id,
            "input_source_identity": self.input_source_identity,
            "input_locator_digest": self.input_locator_digest,
            "input_payload_digest": self.input_payload_digest,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FactQualificationDecision":
        d = _reject_unknown(d, {"decision_id", "candidate_id", "candidate_revision",
                                "candidate_source_kind", "verdict", "rules_version",
                                "input_identity_digest", "input_material_ids",
                                "input_snapshot_id", "input_source_identity",
                                "input_locator_digest", "input_payload_digest",
                                "rejection_reason"}, "FactQualificationDecision")
        for key in DECISION_FORBIDDEN_KEYS:
            if key in d:
                raise SchemaValidationError(
                    f"FactQualificationDecision 不得引用尚未形成的 qualified result: {key!r}")
        return cls(
            decision_id=_get_str(d, "decision_id", "FactQualificationDecision"),
            candidate_id=_get_str(d, "candidate_id", "FactQualificationDecision"),
            candidate_revision=_get_str(d, "candidate_revision", "FactQualificationDecision"),
            candidate_source_kind=_get_str(d, "candidate_source_kind", "FactQualificationDecision"),
            verdict=_get_str(d, "verdict", "FactQualificationDecision"),
            rules_version=_get_str(d, "rules_version", "FactQualificationDecision"),
            input_identity_digest=_get_str(d, "input_identity_digest",
                                           "FactQualificationDecision"),
            input_material_ids=_get_str_tuple(d, "input_material_ids",
                                              "FactQualificationDecision"),
            input_snapshot_id=_get_str(d, "input_snapshot_id", "FactQualificationDecision",
                                       allow_none=True),
            input_source_identity=_get_str(d, "input_source_identity",
                                           "FactQualificationDecision"),
            input_locator_digest=_get_str(d, "input_locator_digest",
                                          "FactQualificationDecision"),
            input_payload_digest=_get_str(d, "input_payload_digest",
                                          "FactQualificationDecision"),
            rejection_reason=_get_str(d, "rejection_reason", "FactQualificationDecision",
                                      allow_none=True),
        )


@dataclass(frozen=True)
class ExternalFact:
    """formal 外部事实（§16.3 #6）—— ``external_source`` 候选的唯一 qualified result。

    ``ExternalSnapshot`` 只作**来源载体**；事实授权对象只能是本类型。构造门（缺一即拒）：
    资格决定、snapshot/body hash、SourcePolicy、日期、命题、locator。
    """

    external_fact_id: str
    candidate_id: str
    candidate_revision: str
    qualification_decision_id: str
    statement: str
    aspect_ids: tuple[str, ...]
    source_snapshot_id: str
    canonical_url: str
    body_hash: str
    source_policy_version: str
    as_of_date: str
    locator: ExternalLocator
    payload_ref: MaterialPayloadRef
    content_hash: str
    source_authority: ExternalSnapshotAuthorityAssessment
    obtained_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.external_fact_id:
            raise SchemaValidationError("ExternalFact.external_fact_id 必须非空")
        # 单向回指：candidate + eligible decision。缺一即拒。
        if not self.candidate_id or not self.candidate_revision:
            raise SchemaValidationError("ExternalFact 必须单向回指 candidate id + revision")
        if not self.qualification_decision_id:
            raise SchemaValidationError("ExternalFact 必须绑定资格决定 id")
        if not self.statement:
            raise SchemaValidationError("ExternalFact.statement 必须非空")
        if not self.aspect_ids:
            raise SchemaValidationError("ExternalFact.aspect_ids 必须非空")
        if not self.source_snapshot_id:
            raise SchemaValidationError("ExternalFact.source_snapshot_id 必须非空")
        if not self.canonical_url:
            raise SchemaValidationError("ExternalFact.canonical_url 必须非空")
        if not self.source_policy_version:
            raise SchemaValidationError("ExternalFact.source_policy_version 必须非空")
        if not self.as_of_date:
            raise SchemaValidationError("ExternalFact.as_of_date 必须非空")
        if not _is_sha256_hex(self.body_hash):
            raise SchemaValidationError("ExternalFact.body_hash 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.content_hash):
            raise SchemaValidationError("ExternalFact.content_hash 必须为 64 位 sha256 hex")
        if not isinstance(self.locator, ExternalLocator):
            raise SchemaValidationError(
                "ExternalFact.locator 必须为 ExternalLocator（external 身份不得走证据/财务 locator）")
        if not isinstance(self.source_authority, ExternalSnapshotAuthorityAssessment):
            raise SchemaValidationError(
                "ExternalFact.source_authority 必须为 ExternalSnapshotAuthorityAssessment")
        # snapshot / body hash 三方闭环。
        if self.body_hash != self.content_hash:
            raise SchemaValidationError(
                f"ExternalFact.body_hash={self.body_hash!r} 与 content_hash={self.content_hash!r} 不一致")
        if self.source_authority.content_hash != self.body_hash:
            raise SchemaValidationError(
                "ExternalFact.body_hash 与 source_authority.content_hash 不一致")
        if self.payload_ref.content_hash != self.body_hash:
            raise SchemaValidationError(
                "ExternalFact.body_hash 与 payload_ref.content_hash 不一致")
        if self.payload_ref.object_type != "external_snapshot":
            raise SchemaValidationError(
                f"ExternalFact.payload_ref.object_type 必须为 'external_snapshot'，"
                f"得到 {self.payload_ref.object_type!r}")
        if self.payload_ref.locator.to_dict() != self.locator.to_dict():
            raise SchemaValidationError("ExternalFact.locator 与 payload_ref.locator 不一致")
        # snapshot 身份四方闭环（locator / authority / payload_ref.authority_identity）。
        expected_source_identity = f"external_snapshot:{self.source_snapshot_id}"
        if self.locator.source_snapshot_id != self.source_snapshot_id:
            raise SchemaValidationError("ExternalFact.locator.source_snapshot_id 与 source_snapshot_id 不一致")
        if self.source_authority.source_snapshot_id != self.source_snapshot_id:
            raise SchemaValidationError(
                "ExternalFact.source_authority.source_snapshot_id 与 source_snapshot_id 不一致")
        if self.payload_ref.authority_identity != expected_source_identity:
            raise SchemaValidationError(
                "ExternalFact.payload_ref.authority_identity 与 snapshot 来源身份不一致")
        if self.locator.canonical_url != self.canonical_url:
            raise SchemaValidationError("ExternalFact.locator.canonical_url 与 canonical_url 不一致")
        # 来源权威必须已判定为 authoritative（D 级 / 未过时间资格不得成为 formal ExternalFact）。
        if self.source_authority.verdict != "authoritative":
            raise SchemaValidationError(
                f"ExternalFact.source_authority.verdict={self.source_authority.verdict!r} "
                f"不得形成 formal ExternalFact")

    def to_dict(self) -> dict:
        return {
            "external_fact_id": self.external_fact_id,
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "qualification_decision_id": self.qualification_decision_id,
            "statement": self.statement,
            "aspect_ids": list(self.aspect_ids),
            "source_snapshot_id": self.source_snapshot_id,
            "canonical_url": self.canonical_url,
            "body_hash": self.body_hash,
            "source_policy_version": self.source_policy_version,
            "as_of_date": self.as_of_date,
            "locator": self.locator.to_dict(),
            "payload_ref": self.payload_ref.to_dict(),
            "content_hash": self.content_hash,
            "source_authority": self.source_authority.to_dict(),
            "obtained_fields": list(self.obtained_fields),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalFact":
        d = _reject_unknown(d, {"external_fact_id", "candidate_id", "candidate_revision",
                                "qualification_decision_id", "statement", "aspect_ids",
                                "source_snapshot_id", "canonical_url", "body_hash",
                                "source_policy_version", "as_of_date", "locator", "payload_ref",
                                "content_hash", "source_authority", "obtained_fields"},
                            "ExternalFact")
        return cls(
            external_fact_id=_get_str(d, "external_fact_id", "ExternalFact"),
            candidate_id=_get_str(d, "candidate_id", "ExternalFact"),
            candidate_revision=_get_str(d, "candidate_revision", "ExternalFact"),
            qualification_decision_id=_get_str(d, "qualification_decision_id", "ExternalFact"),
            statement=_get_str(d, "statement", "ExternalFact"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "ExternalFact"),
            source_snapshot_id=_get_str(d, "source_snapshot_id", "ExternalFact"),
            canonical_url=_get_str(d, "canonical_url", "ExternalFact"),
            body_hash=_get_str(d, "body_hash", "ExternalFact"),
            source_policy_version=_get_str(d, "source_policy_version", "ExternalFact"),
            as_of_date=_get_str(d, "as_of_date", "ExternalFact"),
            locator=ExternalLocator.from_dict(_as_dict(d.get("locator"), "ExternalFact", "locator")),
            payload_ref=MaterialPayloadRef.from_dict(_as_dict(d.get("payload_ref"), "ExternalFact",
                                                             "payload_ref")),
            content_hash=_get_str(d, "content_hash", "ExternalFact"),
            source_authority=ExternalSnapshotAuthorityAssessment.from_dict(_as_dict(
                d.get("source_authority"), "ExternalFact", "source_authority")),
            obtained_fields=_get_str_tuple(d, "obtained_fields", "ExternalFact"),
        )


#: 期间要求口径版本（研究侧与写作侧**同一实现**；任一改动都是口径升级）。
PERIOD_REQUIREMENT_POLICY_VERSION = "period-req-1"

#: 事实类型 → 期间要求（三态，全部来自冻结的 typed metadata）：
#:   * `explicit`   —— 必须是时段/时点量：必须有**可核验的显式期间**（事实自己的 `period`）；
#:   * `optional`   —— 结构性/描述性、非时间敏感，且正文自身不使用含糊期间措辞；
#:   * `period_unresolved` —— 类型无法判定 → fail-closed，记期间缺口。
PERIOD_REQUIREMENT_EXPLICIT = "explicit"
PERIOD_REQUIREMENT_OPTIONAL = "optional"
PERIOD_UNRESOLVED_REASON = "period_unresolved"

#: 必须带显式期间的 aspect 类型（frozen Contract `ASPECT_KINDS`）：指标与事件/活动类是时段量
#: （余额/时点量同理，只是它的显式期间本身就是日期），没有期间就没有意义。
PERIOD_BEARING_ASPECT_KINDS = ("financial_metric", "event_set")


def fact_explicit_period(fact: Any) -> str:
    """事实**自己**的显式期间：`fact.period`，其次它自己的 `ValueIdentity.period`。

    只有事实自己的类型化字段可以用在这里——绝不用 `report_as_of`/快照期末顶替
    （那是把「本节截止日」偷换成「这条事实发生在这一天」），也不用任何正文措辞推断。
    """
    period = str(getattr(fact, "period", "") or "").strip()
    if period:
        return period
    value_identity = getattr(fact, "value_identity", None)
    return str(getattr(value_identity, "period", "") or "").strip()


def period_requirement(*, fact_type: str, has_value_identity: bool,
                       aspect_kinds: tuple[str, ...],
                       time_scopes: tuple[str, ...]) -> str:
    """事实类型 + 冻结 aspect 元数据 → 期间要求（**唯一实现**，研究侧与写作侧共用）。

    判据顺序（§六）：推断类事实 / 带类型化数字身份的事实是时段量 → 必须显式；aspect 类型属
    指标或事件类 → 必须显式；aspect 的 `time_scope` 全是结构性的 → 期间可选；其余（含无法判定）
    → fail-closed。
    """
    if str(fact_type or "") == "inference":
        return PERIOD_REQUIREMENT_EXPLICIT
    if has_value_identity:
        return PERIOD_REQUIREMENT_EXPLICIT
    if any(kind in PERIOD_BEARING_ASPECT_KINDS for kind in aspect_kinds):
        return PERIOD_REQUIREMENT_EXPLICIT
    scopes = {scope for scope in time_scopes if scope}
    if scopes and scopes <= {"STRUCTURAL_5Y_CURRENT"}:
        return PERIOD_REQUIREMENT_OPTIONAL
    if scopes and scopes <= set(TIME_POLICIES):
        return PERIOD_REQUIREMENT_EXPLICIT
    return PERIOD_UNRESOLVED_REASON


def build_fact_candidate(*, candidate_source_kind: str, statement: str, fact_type: str,
                         aspect_ids: tuple[str, ...], question_ids: tuple[str, ...],
                         material_ids: tuple[str, ...] = (),
                         source_snapshot_id: str | None = None,
                         content_hash: str = "", semantic_tags: tuple[str, ...] = (),
                         period: str | None = None, scope: str | None = None,
                         confidence: str | None = None,
                         inference_lineage: InferenceLineage | None = None
                         ) -> FactCandidate:
    """**唯一**候选构造实现：确定性派生 `candidate_id` 与内容派生的 `candidate_revision`。

    生产（`harness/topic_runtime.py`）与各测试夹具共用同一实现，避免第二套候选身份口径。
    """
    candidate_id = derive_fact_candidate_id(candidate_source_kind, statement, aspect_ids,
                                            material_ids, source_snapshot_id)
    draft = FactCandidate(
        candidate_id=candidate_id, candidate_revision="__pending__",
        candidate_source_kind=candidate_source_kind, statement=statement, fact_type=fact_type,
        aspect_ids=tuple(aspect_ids), question_ids=tuple(question_ids),
        material_ids=tuple(material_ids), source_snapshot_id=source_snapshot_id,
        content_hash=content_hash, semantic_tags=tuple(semantic_tags), period=period,
        scope=scope, confidence=confidence, inference_lineage=inference_lineage)
    return dataclasses.replace(draft, candidate_revision=draft.compute_revision())


def build_qualification_decision(candidate: FactCandidate, *, verdict: str,
                                 input_identity_digest: str,
                                 input_source_identity: str,
                                 input_locator_digest: str,
                                 input_payload_digest: str,
                                 input_snapshot_id: str | None = None,
                                 rejection_reason: str | None = None,
                                 rules_version: str | None = None
                                 ) -> FactQualificationDecision:
    """**唯一**资格决定构造实现：身份由 candidate revision + verdict + 规则版本确定性派生。

    输入形状按 `candidate.candidate_source_kind` 封闭：topic material 走
    `candidate.material_ids`，external source 走 `input_snapshot_id`。决定身份**不引用**
    任何尚未形成的 qualified result。
    """
    rules = rules_version or FACT_QUALIFICATION_VERSION
    if verdict == "rejected" and not rejection_reason:
        raise SchemaValidationError("build_qualification_decision: rejected 必须给出 rejection_reason")
    if verdict == "eligible" and rejection_reason is not None:
        raise SchemaValidationError("build_qualification_decision: eligible 不得携带 rejection_reason")
    return FactQualificationDecision(
        decision_id=derive_fact_qualification_decision_id(
            candidate.candidate_id, candidate.candidate_revision,
            candidate.candidate_source_kind, verdict, rules),
        candidate_id=candidate.candidate_id, candidate_revision=candidate.candidate_revision,
        candidate_source_kind=candidate.candidate_source_kind, verdict=verdict,
        rules_version=rules, input_identity_digest=input_identity_digest,
        input_material_ids=(tuple(candidate.material_ids)
                            if candidate.candidate_source_kind == "topic_material" else ()),
        input_snapshot_id=(input_snapshot_id
                           if candidate.candidate_source_kind == "external_source" else None),
        input_source_identity=input_source_identity,
        input_locator_digest=input_locator_digest,
        input_payload_digest=input_payload_digest, rejection_reason=rejection_reason)


def build_supported_fact(fact_id: str, candidate: FactCandidate,
                         decision: FactQualificationDecision, *, text: str, fact_type: str,
                         citation_refs: tuple[CitationRef, ...],
                         source_authority: "SourceAuthorityAssessment",
                         value_identity: ValueIdentity | None = None) -> SupportedFact:
    """**唯一** `topic_material` qualified-result 构造实现（candidate + eligible 决定 → fact）。

    `period` / `scope` 一律**从候选逐字复制**：qualified result 的期间只能来自候选自己
    （候选的期间又只能来自被引材料的精确定位原文），构造器不接受旁路实参——否则同一个事实
    会出现「候选一套期间、结果另一套期间」，读回重算也就不一致了。候选没有期间则结果没有，
    由 Pack 侧期间门如实排除，**不得**在这里用报告生成日/快照期末顶替。

    `value_identity`（可空）是**这条事实自己声明的逐值身份**（`nd-2`：分业务营收事实由
    确定性抽取器逐值给出）。它**不是**旁路实参，而是同一条身份链的**放大**：声明里的期间必须
    与候选的期间**逐字相同**，否则拒——不然一份事实会同时说「我是 2025 年那条」和「我授权
    2023 年那个值」。没有逐值身份的事实（模型 claim、财务、附注、外部）照旧传 `None`。
    """
    if decision.verdict != "eligible":
        raise SchemaValidationError(
            "build_supported_fact 只接受 eligible 决定（rejected 决定零条 qualified result）")
    if value_identity is not None:
        declared_period = str(value_identity.period or "")
        if declared_period != str(candidate.period or ""):
            raise SchemaValidationError(
                f"SupportedFact 声明值级期间 {declared_period!r} 与候选期间 "
                f"{candidate.period!r} 不一致（值身份不得绕过候选自己的期间）")
    return SupportedFact(
        fact_id=fact_id, text=text, fact_type=fact_type,
        aspect_ids=tuple(candidate.aspect_ids), citation_refs=tuple(citation_refs),
        source_authority=source_authority, value_identity=value_identity,
        period=candidate.period, scope=candidate.scope,
        confidence=candidate.confidence,
        candidate_id=candidate.candidate_id,
        candidate_revision=candidate.candidate_revision,
        qualification_decision_id=decision.decision_id)


def external_fact_id_for(snapshot_id: str, statement: str,
                         locator: ExternalLocator) -> str:
    """`ExternalFact` 身份的确定性派生（同一 snapshot + 命题 + locator ⇒ 同一事实身份）。"""
    return "ef-" + sha256_canonical({
        "kind": "external_fact",
        "version": EXTERNAL_FACT_VERSION,
        "source_snapshot_id": snapshot_id,
        "statement": statement,
        "locator": locator.to_dict(),
    })[:32]


def build_external_fact(candidate: FactCandidate, decision: FactQualificationDecision, *,
                        statement: str, canonical_url: str, body_hash: str,
                        source_policy_version: str, as_of_date: str, locator: ExternalLocator,
                        payload_ref: MaterialPayloadRef, content_hash: str,
                        source_authority: ExternalSnapshotAuthorityAssessment,
                        obtained_fields: tuple[str, ...] = ()) -> ExternalFact:
    """**唯一** `external_source` qualified-result 构造实现（candidate + eligible 决定 → fact）。

    `ExternalSnapshot` 只作来源载体：事实身份仍必须绑定 snapshot / body hash / SourcePolicy /
    日期 / 命题 / locator，并对 candidate + 决定单向回指。
    """
    if decision.verdict != "eligible":
        raise SchemaValidationError(
            "build_external_fact 只接受 eligible 决定（rejected 决定零条 qualified result）")
    return ExternalFact(
        external_fact_id=external_fact_id_for(candidate.source_snapshot_id or "", statement,
                                              locator),
        candidate_id=candidate.candidate_id, candidate_revision=candidate.candidate_revision,
        qualification_decision_id=decision.decision_id, statement=statement,
        aspect_ids=tuple(candidate.aspect_ids),
        source_snapshot_id=candidate.source_snapshot_id or "",
        canonical_url=canonical_url, body_hash=body_hash,
        source_policy_version=source_policy_version, as_of_date=as_of_date, locator=locator,
        payload_ref=payload_ref, content_hash=content_hash, source_authority=source_authority,
        obtained_fields=tuple(obtained_fields))


def derive_contract_gap_id(topic_id: str, aspect_id: str, reason_code: str,
                           unmet_required_items: tuple[str, ...]) -> str:
    return "cgap_" + sha256_canonical({
        "kind": "contract_gap",
        "topic_id": topic_id,
        "aspect_id": aspect_id,
        "reason_code": reason_code,
        "unmet_required_items": list(unmet_required_items),
    })[:32]


@dataclass(frozen=True)
class ContractGap:
    """research 侧权威 Contract 缺口（§16.3 #19 / C17）。

    只在 Contract **必需** aspect/事实仍未取得时形成，且必须绑定 current Contract basis
    与非空 required-unmet 证明。它**不是** rejection 的替身，也不得由单个 candidate rejected
    自动生成；本类型只落研究侧边界（``harness/topic_schema.py`` + ``harness/topic_store.py``）。
    """

    gap_id: str
    topic_id: str
    aspect_id: str
    question_ids: tuple[str, ...]
    contract_version: str
    contract_sha256: str
    requirement_fingerprint: str
    unmet_required_items: tuple[str, ...]
    reason_code: str
    impact_scopes: tuple[str, ...]
    required_unmet_proof: str
    rejected_candidate_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.gap_id:
            raise SchemaValidationError("ContractGap.gap_id 必须非空")
        if not self.topic_id or not self.aspect_id:
            raise SchemaValidationError("ContractGap.topic_id/aspect_id 必须非空")
        if not self.contract_version or not self.requirement_fingerprint:
            raise SchemaValidationError(
                "ContractGap 必须绑定 current Contract basis（contract_version + requirement_fingerprint）")
        if self.contract_sha256 and not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("ContractGap.contract_sha256 必须为 64 位 sha256 hex")
        if not self.unmet_required_items:
            raise SchemaValidationError(
                "ContractGap 必须携带非空 unmet_required_items（无 Contract-required 未满足项即不得形成 gap）")
        _get_enum(self.reason_code, GAP_REASON_CODES, "ContractGap", "reason_code")
        for s in self.impact_scopes:
            _get_enum(s, GAP_IMPACTS, "ContractGap", "impact_scopes")
        if not self.required_unmet_proof:
            raise SchemaValidationError("ContractGap.required_unmet_proof 必须非空")
        expected = derive_contract_gap_id(self.topic_id, self.aspect_id, self.reason_code,
                                          self.unmet_required_items)
        if self.gap_id != expected:
            raise SchemaValidationError(
                f"ContractGap.gap_id={self.gap_id!r} 与确定性派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "gap_id": self.gap_id,
            "topic_id": self.topic_id,
            "aspect_id": self.aspect_id,
            "question_ids": list(self.question_ids),
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "requirement_fingerprint": self.requirement_fingerprint,
            "unmet_required_items": list(self.unmet_required_items),
            "reason_code": self.reason_code,
            "impact_scopes": list(self.impact_scopes),
            "required_unmet_proof": self.required_unmet_proof,
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ContractGap":
        d = _reject_unknown(d, {"gap_id", "topic_id", "aspect_id", "question_ids",
                                "contract_version", "contract_sha256", "requirement_fingerprint",
                                "unmet_required_items", "reason_code", "impact_scopes",
                                "required_unmet_proof", "rejected_candidate_ids"}, "ContractGap")
        return cls(
            gap_id=_get_str(d, "gap_id", "ContractGap"),
            topic_id=_get_str(d, "topic_id", "ContractGap"),
            aspect_id=_get_str(d, "aspect_id", "ContractGap"),
            question_ids=_get_str_tuple(d, "question_ids", "ContractGap"),
            contract_version=_get_str(d, "contract_version", "ContractGap"),
            contract_sha256=_get_str(d, "contract_sha256", "ContractGap", allow_empty=True) or "",
            requirement_fingerprint=_get_str(d, "requirement_fingerprint", "ContractGap"),
            unmet_required_items=_get_str_tuple(d, "unmet_required_items", "ContractGap"),
            reason_code=_get_str(d, "reason_code", "ContractGap"),
            impact_scopes=_get_str_tuple(d, "impact_scopes", "ContractGap"),
            required_unmet_proof=_get_str(d, "required_unmet_proof", "ContractGap"),
            rejected_candidate_ids=_get_str_tuple(d, "rejected_candidate_ids", "ContractGap"),
        )


def derive_research_block_id(topic_id: str, aspect_id: str, block_kind: str,
                             unmet_required_items: tuple[str, ...]) -> str:
    return "rblk_" + sha256_canonical({
        "kind": "research_block",
        "topic_id": topic_id,
        "aspect_id": aspect_id,
        "block_kind": block_kind,
        "unmet_required_items": list(unmet_required_items),
    })[:32]


@dataclass(frozen=True)
class ResearchBlock:
    """研究路径被硬性挡住（§16.3 #19）—— 与 rejection 是两条独立记录。

    ``detail`` 只描述**研究侧阻断事实**；资格决定、gap 去向与 disposition 一律不得塞进
    本字段（CLAUDE.md 禁则：不得把资格决定塞进泛化字符串）。
    """

    block_id: str
    topic_id: str
    aspect_id: str
    question_ids: tuple[str, ...]
    contract_version: str
    contract_sha256: str
    requirement_fingerprint: str
    unmet_required_items: tuple[str, ...]
    block_kind: str
    detail: str
    required_unmet_proof: str

    def __post_init__(self) -> None:
        if not self.block_id:
            raise SchemaValidationError("ResearchBlock.block_id 必须非空")
        if not self.topic_id or not self.aspect_id:
            raise SchemaValidationError("ResearchBlock.topic_id/aspect_id 必须非空")
        if not self.contract_version or not self.requirement_fingerprint:
            raise SchemaValidationError(
                "ResearchBlock 必须绑定 current Contract basis")
        if self.contract_sha256 and not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("ResearchBlock.contract_sha256 必须为 64 位 sha256 hex")
        _get_enum(self.block_kind, RESEARCH_BLOCK_KINDS, "ResearchBlock", "block_kind")
        if not self.detail:
            raise SchemaValidationError("ResearchBlock.detail 必须非空")
        if not self.required_unmet_proof:
            raise SchemaValidationError("ResearchBlock.required_unmet_proof 必须非空")
        expected = derive_research_block_id(self.topic_id, self.aspect_id, self.block_kind,
                                            self.unmet_required_items)
        if self.block_id != expected:
            raise SchemaValidationError(
                f"ResearchBlock.block_id={self.block_id!r} 与确定性派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "topic_id": self.topic_id,
            "aspect_id": self.aspect_id,
            "question_ids": list(self.question_ids),
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "requirement_fingerprint": self.requirement_fingerprint,
            "unmet_required_items": list(self.unmet_required_items),
            "block_kind": self.block_kind,
            "detail": self.detail,
            "required_unmet_proof": self.required_unmet_proof,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchBlock":
        d = _reject_unknown(d, {"block_id", "topic_id", "aspect_id", "question_ids",
                                "contract_version", "contract_sha256", "requirement_fingerprint",
                                "unmet_required_items", "block_kind", "detail",
                                "required_unmet_proof"}, "ResearchBlock")
        return cls(
            block_id=_get_str(d, "block_id", "ResearchBlock"),
            topic_id=_get_str(d, "topic_id", "ResearchBlock"),
            aspect_id=_get_str(d, "aspect_id", "ResearchBlock"),
            question_ids=_get_str_tuple(d, "question_ids", "ResearchBlock"),
            contract_version=_get_str(d, "contract_version", "ResearchBlock"),
            contract_sha256=_get_str(d, "contract_sha256", "ResearchBlock", allow_empty=True) or "",
            requirement_fingerprint=_get_str(d, "requirement_fingerprint", "ResearchBlock"),
            unmet_required_items=_get_str_tuple(d, "unmet_required_items", "ResearchBlock"),
            block_kind=_get_str(d, "block_kind", "ResearchBlock"),
            detail=_get_str(d, "detail", "ResearchBlock"),
            required_unmet_proof=_get_str(d, "required_unmet_proof", "ResearchBlock"),
        )


def derive_follow_up_need_id(statement: str, target_requirement_id: str, aspect_id: str,
                             section_id: str, section_draft_revision: str) -> str:
    return "fun_" + sha256_canonical({
        "kind": "follow_up_need",
        "version": FOLLOW_UP_NEED_SCHEMA_VERSION,
        "statement": statement,
        "target_requirement_id": target_requirement_id,
        "aspect_id": aspect_id,
        "section_id": section_id,
        "section_draft_revision": section_draft_revision,
    })[:32]


@dataclass(frozen=True)
class FollowUpNeed:
    """Writer 侧「需要更多材料」的**独立身份**（§16.5 边界③）。

    fail-closed 边界：

    - **不是** ``SectionDraft`` 的字段，也不进其 ``identity_body``；Writer 结果以
      ``draft + follow_up_needs`` 并列返回，两者各自独立身份；
    - **不是** gap，也**不是** ``SectionUnresolved``：三者不得共享表、不得共享语义、不得互相填充；
    - **不含**任何执行结果、Pack id/引用或 material 引用 —— 那些是 Harness 裁决与执行之后的产物；
    - 位于三套 identity 之外：其版本键 ``FOLLOW_UP_NEED_SCHEMA_VERSION`` 只失效自身。
    """

    need_id: str
    need_schema_version: str
    statement: str
    target_requirement_id: str
    topic_id: str
    question_id: str
    aspect_id: str
    section_id: str
    section_draft_revision: str
    contract_authorized_scope: tuple[str, ...]
    requiredness: str
    expected_source_class: str
    budget_hint: str
    writer_identity: str

    def __post_init__(self) -> None:
        if self.need_schema_version != FOLLOW_UP_NEED_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"FollowUpNeed.need_schema_version 必须为 {FOLLOW_UP_NEED_SCHEMA_VERSION!r}，"
                f"得到 {self.need_schema_version!r}")
        for name, value in (("need_id", self.need_id), ("statement", self.statement),
                            ("target_requirement_id", self.target_requirement_id),
                            ("topic_id", self.topic_id), ("question_id", self.question_id),
                            ("aspect_id", self.aspect_id), ("section_id", self.section_id),
                            ("section_draft_revision", self.section_draft_revision),
                            ("expected_source_class", self.expected_source_class),
                            ("writer_identity", self.writer_identity)):
            if not value:
                raise SchemaValidationError(f"FollowUpNeed.{name} 必须非空")
        _get_enum(self.requiredness, FOLLOW_UP_REQUIREDNESS, "FollowUpNeed", "requiredness")
        expected = derive_follow_up_need_id(self.statement, self.target_requirement_id,
                                            self.aspect_id, self.section_id,
                                            self.section_draft_revision)
        if self.need_id != expected:
            raise SchemaValidationError(
                f"FollowUpNeed.need_id={self.need_id!r} 与确定性派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "need_id": self.need_id,
            "need_schema_version": self.need_schema_version,
            "statement": self.statement,
            "target_requirement_id": self.target_requirement_id,
            "topic_id": self.topic_id,
            "question_id": self.question_id,
            "aspect_id": self.aspect_id,
            "section_id": self.section_id,
            "section_draft_revision": self.section_draft_revision,
            "contract_authorized_scope": list(self.contract_authorized_scope),
            "requiredness": self.requiredness,
            "expected_source_class": self.expected_source_class,
            "budget_hint": self.budget_hint,
            "writer_identity": self.writer_identity,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FollowUpNeed":
        d = _reject_unknown(d, {"need_id", "need_schema_version", "statement",
                                "target_requirement_id", "topic_id", "question_id", "aspect_id",
                                "section_id", "section_draft_revision",
                                "contract_authorized_scope", "requiredness",
                                "expected_source_class", "budget_hint", "writer_identity"},
                            "FollowUpNeed")
        for key in FOLLOW_UP_NEED_FORBIDDEN_KEYS:
            if key in d:
                raise SchemaValidationError(
                    f"FollowUpNeed 不得携带执行结果/Pack/material 引用: {key!r}")
        return cls(
            need_id=_get_str(d, "need_id", "FollowUpNeed"),
            need_schema_version=_get_str(d, "need_schema_version", "FollowUpNeed"),
            statement=_get_str(d, "statement", "FollowUpNeed"),
            target_requirement_id=_get_str(d, "target_requirement_id", "FollowUpNeed"),
            topic_id=_get_str(d, "topic_id", "FollowUpNeed"),
            question_id=_get_str(d, "question_id", "FollowUpNeed"),
            aspect_id=_get_str(d, "aspect_id", "FollowUpNeed"),
            section_id=_get_str(d, "section_id", "FollowUpNeed"),
            section_draft_revision=_get_str(d, "section_draft_revision", "FollowUpNeed"),
            contract_authorized_scope=_get_str_tuple(d, "contract_authorized_scope", "FollowUpNeed"),
            requiredness=_get_str(d, "requiredness", "FollowUpNeed"),
            expected_source_class=_get_str(d, "expected_source_class", "FollowUpNeed"),
            budget_hint=_get_str(d, "budget_hint", "FollowUpNeed", allow_empty=True) or "",
            writer_identity=_get_str(d, "writer_identity", "FollowUpNeed"),
        )


def derive_follow_up_decision_id(need_id: str, verdict: str, reason: str,
                                 rules_version: str) -> str:
    return "fund_" + sha256_canonical({
        "kind": "follow_up_decision",
        "version": FOLLOW_UP_NEED_SCHEMA_VERSION,
        "need_id": need_id,
        "verdict": verdict,
        "reason": reason,
        "rules_version": rules_version,
    })[:32]


@dataclass(frozen=True)
class FollowUpDecision:
    """Harness 对一条 `FollowUpNeed` 的裁决（§16.5 边界③）。

    「need 已裁决」只能靠追加本对象表达，绝不覆盖 `need` 行或 `run` 行。
    - `accepted`：已执行，且产出**新 Pack**（旧 Pack 不回写）；
    - `reduced`：已执行并产出新 Pack，但**实际取得的材料少于 ask**（`reason` 必须写明缩减
      依据）—— 它**不是** gap，也不是 rejection；
    - `rejected`：未执行、未联网、未产出 Pack，`reason` 为 typed 拒绝码。

    三种裁决都不新建第二套 Router / ToolRegistry / Retriever / store / LLM runtime。
    """

    decision_id: str
    need_id: str
    verdict: str
    rules_version: str
    authorized_requirement_id: str | None
    resolved_topic_id: str | None
    resolved_aspect_id: str | None
    reason: str
    executed: bool
    new_pack_id: str | None = None
    trace_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.decision_id or not self.need_id:
            raise SchemaValidationError("FollowUpDecision.decision_id/need_id 必须非空")
        _get_enum(self.verdict, FOLLOW_UP_DECISION_VERDICTS, "FollowUpDecision", "verdict")
        if not self.rules_version:
            raise SchemaValidationError("FollowUpDecision.rules_version 必须非空")
        if not self.reason:
            raise SchemaValidationError("FollowUpDecision.reason 必须非空")
        executed_verdicts = ("accepted", "reduced")
        if self.verdict in executed_verdicts:
            if not self.executed:
                raise SchemaValidationError(
                    f"FollowUpDecision.verdict={self.verdict!r} 必须标记 executed")
            if not self.new_pack_id:
                raise SchemaValidationError(
                    f"FollowUpDecision.verdict={self.verdict!r} 必须绑定新 Pack identity"
                    "（append-only successor；不回写旧 Pack）")
        else:
            if self.executed:
                raise SchemaValidationError(
                    f"FollowUpDecision.verdict={self.verdict!r} 不得标记 executed")
            if self.new_pack_id is not None:
                raise SchemaValidationError(
                    f"FollowUpDecision.verdict={self.verdict!r} 不得携带 new_pack_id")
        expected = derive_follow_up_decision_id(self.need_id, self.verdict, self.reason,
                                                self.rules_version)
        if self.decision_id != expected:
            raise SchemaValidationError(
                f"FollowUpDecision.decision_id={self.decision_id!r} 与确定性派生值 {expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "need_id": self.need_id,
            "verdict": self.verdict,
            "rules_version": self.rules_version,
            "authorized_requirement_id": self.authorized_requirement_id,
            "resolved_topic_id": self.resolved_topic_id,
            "resolved_aspect_id": self.resolved_aspect_id,
            "reason": self.reason,
            "executed": self.executed,
            "new_pack_id": self.new_pack_id,
            "trace_refs": list(self.trace_refs),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FollowUpDecision":
        d = _reject_unknown(d, {"decision_id", "need_id", "verdict", "rules_version",
                                "authorized_requirement_id", "resolved_topic_id",
                                "resolved_aspect_id", "reason", "executed", "new_pack_id",
                                "trace_refs"}, "FollowUpDecision")
        return cls(
            decision_id=_get_str(d, "decision_id", "FollowUpDecision"),
            need_id=_get_str(d, "need_id", "FollowUpDecision"),
            verdict=_get_str(d, "verdict", "FollowUpDecision"),
            rules_version=_get_str(d, "rules_version", "FollowUpDecision"),
            authorized_requirement_id=_get_str(d, "authorized_requirement_id", "FollowUpDecision",
                                               allow_none=True),
            resolved_topic_id=_get_str(d, "resolved_topic_id", "FollowUpDecision",
                                       allow_none=True),
            resolved_aspect_id=_get_str(d, "resolved_aspect_id", "FollowUpDecision",
                                        allow_none=True),
            reason=_get_str(d, "reason", "FollowUpDecision"),
            executed=_get_bool(d, "executed", "FollowUpDecision"),
            new_pack_id=_get_str(d, "new_pack_id", "FollowUpDecision", allow_none=True),
            trace_refs=_get_str_tuple(d, "trace_refs", "FollowUpDecision"),
        )


def derive_follow_up_run_id(run_id: str, need_ids: tuple[str, ...],
                            rules_version: str) -> str:
    """`FollowUpRun` 的内容寻址身份：同一批 need + 同一规则版本 ⇒ 同一行（append 幂等）。"""
    return "fur_" + sha256_canonical({
        "kind": "follow_up_run",
        "version": FOLLOW_UP_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "need_ids": list(need_ids),
        "rules_version": rules_version,
    })[:32]


@dataclass(frozen=True)
class FollowUpRun:
    """边界③的 run 行：一次 follow-up 裁决批次的**追加式**记录。

    它只记录「谁请求了什么、裁决成了什么、是否形成了新 Pack、轨迹落在哪」。它**不含**任何
    Pack/Draft 的业务语义，也不与 gate 结果、gap、rejection audit 共享语义或表。
    """

    follow_up_run_id: str
    run_id: str
    task_id: str
    section_id: str
    section_draft_revision: str
    writer_identity: str
    rules_version: str
    need_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    new_pack_ids: tuple[str, ...]
    trace_refs: tuple[str, ...]
    schema_version: str = FOLLOW_UP_RUN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FOLLOW_UP_RUN_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"FollowUpRun.schema_version 必须为 {FOLLOW_UP_RUN_SCHEMA_VERSION!r}")
        for name, value in (("follow_up_run_id", self.follow_up_run_id),
                            ("run_id", self.run_id), ("task_id", self.task_id),
                            ("section_id", self.section_id),
                            ("section_draft_revision", self.section_draft_revision),
                            ("writer_identity", self.writer_identity),
                            ("rules_version", self.rules_version)):
            if not value:
                raise SchemaValidationError(f"FollowUpRun.{name} 必须非空")
        if not self.need_ids:
            raise SchemaValidationError("FollowUpRun.need_ids 不得为空")
        if len(set(self.need_ids)) != len(self.need_ids):
            raise SchemaValidationError("FollowUpRun.need_ids 不得重复")
        if len(self.decision_ids) != len(self.need_ids):
            raise SchemaValidationError(
                "FollowUpRun 必须对每条 need 恰有一条 decision（缺失/额外均 fail-closed）")
        if len(set(self.decision_ids)) != len(self.decision_ids):
            raise SchemaValidationError("FollowUpRun.decision_ids 不得重复")
        if len(self.new_pack_ids) > len(self.need_ids):
            raise SchemaValidationError(
                "FollowUpRun.new_pack_ids 不得多于 need 数（每条 need 至多一个新 Pack）")
        if len(set(self.new_pack_ids)) != len(self.new_pack_ids):
            raise SchemaValidationError("FollowUpRun.new_pack_ids 不得重复")
        expected = derive_follow_up_run_id(self.run_id, self.need_ids, self.rules_version)
        if self.follow_up_run_id != expected:
            raise SchemaValidationError(
                f"FollowUpRun.follow_up_run_id={self.follow_up_run_id!r} 与确定性派生值 "
                f"{expected!r} 不符")

    def to_dict(self) -> dict:
        return {
            "follow_up_run_id": self.follow_up_run_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "section_id": self.section_id,
            "section_draft_revision": self.section_draft_revision,
            "writer_identity": self.writer_identity,
            "rules_version": self.rules_version,
            "need_ids": list(self.need_ids),
            "decision_ids": list(self.decision_ids),
            "new_pack_ids": list(self.new_pack_ids),
            "trace_refs": list(self.trace_refs),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FollowUpRun":
        d = _reject_unknown(d, {"follow_up_run_id", "run_id", "task_id", "section_id",
                                "section_draft_revision", "writer_identity", "rules_version",
                                "need_ids", "decision_ids", "new_pack_ids", "trace_refs",
                                "schema_version"}, "FollowUpRun")
        return cls(
            follow_up_run_id=_get_str(d, "follow_up_run_id", "FollowUpRun"),
            run_id=_get_str(d, "run_id", "FollowUpRun"),
            task_id=_get_str(d, "task_id", "FollowUpRun"),
            section_id=_get_str(d, "section_id", "FollowUpRun"),
            section_draft_revision=_get_str(d, "section_draft_revision", "FollowUpRun"),
            writer_identity=_get_str(d, "writer_identity", "FollowUpRun"),
            rules_version=_get_str(d, "rules_version", "FollowUpRun"),
            need_ids=_get_str_tuple(d, "need_ids", "FollowUpRun"),
            decision_ids=_get_str_tuple(d, "decision_ids", "FollowUpRun"),
            new_pack_ids=_get_str_tuple(d, "new_pack_ids", "FollowUpRun"),
            trace_refs=_get_str_tuple(d, "trace_refs", "FollowUpRun"),
            schema_version=_get_str(d, "schema_version", "FollowUpRun",
                                    allow_empty=True) or FOLLOW_UP_RUN_SCHEMA_VERSION,
        )


@dataclass(frozen=True)
class SupportedFact:
    """一条通过校验的 adopted fact（SUPPORTED entailment）。

    M930-3 起为 ``topic_material`` 候选的唯一 qualified result：必须**单向**回指其
    candidate id/revision 与 eligible 资格决定（缺一即拒）。它**不得**指向
    ``external_source`` 候选（那是 ``ExternalFact`` 的职责）。
    """

    fact_id: str
    text: str
    fact_type: str
    aspect_ids: tuple[str, ...]
    citation_refs: tuple[CitationRef, ...]
    source_authority: AuthorityAssessment
    value_identity: ValueIdentity | None = None
    semantic_tags: tuple[str, ...] = ()
    period: str | None = None
    scope: str | None = None
    confidence: str | None = None
    # 本事实取得的 required_fields 标识（用于 required_fields_complete 覆盖证明；无运行时集合证明）。
    obtained_fields: tuple[str, ...] = ()
    # Fix 3：inference fact 的类型化推断血缘（普通 fact 为 None）。
    inference_lineage: InferenceLineage | None = None
    # M930-3：单向回指 topic-material candidate + eligible 资格决定（无资格链的 fact 不得存在）。
    candidate_id: str = ""
    candidate_revision: str = ""
    qualification_decision_id: str = ""

    def __post_init__(self) -> None:
        if not self.fact_id:
            raise SchemaValidationError("SupportedFact.fact_id 必须非空")
        if not self.text:
            raise SchemaValidationError("SupportedFact.text 必须非空")
        _get_enum(self.fact_type, FACT_TYPES, "SupportedFact", "fact_type")
        if len(self.aspect_ids) == 0:
            raise SchemaValidationError("SupportedFact.aspect_ids 必须非空")
        if self.confidence is not None and self.confidence not in ("high", "low"):
            raise SchemaValidationError(f"SupportedFact.confidence 非法: {self.confidence!r}")
        if self.fact_type == "inference" and self.inference_lineage is None:
            raise SchemaValidationError("SupportedFact.fact_type=inference 必须携带 inference_lineage")
        if self.fact_type == "fact" and self.inference_lineage is not None:
            raise SchemaValidationError("SupportedFact.fact_type=fact 不得携带 inference_lineage")
        # 资格链单向闭合：缺任一即拒（不允许无候选/无决定的事实进入 current Pack）。
        if not self.candidate_id or not self.candidate_revision:
            raise SchemaValidationError(
                "SupportedFact 必须单向回指 candidate id + revision（M930-3 资格链）")
        if not self.qualification_decision_id:
            raise SchemaValidationError("SupportedFact 必须绑定 eligible 资格决定 id")

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "text": self.text,
            "fact_type": self.fact_type,
            "aspect_ids": list(self.aspect_ids),
            "citation_refs": [c.to_dict() for c in self.citation_refs],
            "source_authority": self.source_authority.to_dict(),
            "value_identity": self.value_identity.to_dict() if self.value_identity else None,
            "semantic_tags": list(self.semantic_tags),
            "period": self.period,
            "scope": self.scope,
            "confidence": self.confidence,
            "obtained_fields": list(self.obtained_fields),
            "inference_lineage": self.inference_lineage.to_dict() if self.inference_lineage else None,
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "qualification_decision_id": self.qualification_decision_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SupportedFact":
        d = _reject_unknown(d, {"fact_id", "text", "fact_type", "aspect_ids", "citation_refs",
                                "source_authority", "value_identity", "semantic_tags", "period",
                                "scope", "confidence", "obtained_fields", "inference_lineage",
                                "candidate_id", "candidate_revision",
                                "qualification_decision_id"},
                            "SupportedFact")
        vi = d.get("value_identity")
        il = d.get("inference_lineage")
        return cls(
            fact_id=_get_str(d, "fact_id", "SupportedFact"),
            text=_get_str(d, "text", "SupportedFact"),
            fact_type=_get_str(d, "fact_type", "SupportedFact"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "SupportedFact"),
            citation_refs=tuple(CitationRef.from_dict(x) for x in _as_list(
                d.get("citation_refs"), "SupportedFact", "citation_refs")),
            source_authority=authority_from_dict(_as_dict(d.get("source_authority"), "SupportedFact",
                                                          "source_authority")),
            value_identity=ValueIdentity.from_dict(vi) if vi is not None else None,
            semantic_tags=_get_str_tuple(d, "semantic_tags", "SupportedFact"),
            period=_get_str(d, "period", "SupportedFact", allow_none=True),
            scope=_get_str(d, "scope", "SupportedFact", allow_none=True),
            confidence=_get_str(d, "confidence", "SupportedFact", allow_none=True),
            obtained_fields=_get_str_tuple(d, "obtained_fields", "SupportedFact"),
            inference_lineage=InferenceLineage.from_dict(il) if il is not None else None,
            candidate_id=_get_str(d, "candidate_id", "SupportedFact"),
            candidate_revision=_get_str(d, "candidate_revision", "SupportedFact"),
            qualification_decision_id=_get_str(d, "qualification_decision_id", "SupportedFact"),
        )


@dataclass(frozen=True)
class ConflictSide:
    """冲突的**一侧**（§0.3.5，`ccr-1`）。闭集联合，二选一：

    - `QualifiedFactSide`：`fact_id` 指向**本 Pack 内**的合格 fact；
    - `ObservedStatementSide`：`statement_ref` + **必填** `statement_payload_hash` 指向一份
      **已持久化**的候选/材料陈述——可核的原句/值，**不只是**一串自报 id。

    为什么必须新造这个类型：`ResearchConflict` 原来的字段只有
    `conflict_id / fact_ids / category / detail / status`，而 Store 要求 `fact_ids` 的**每一项**
    都存在于 `pack.facts`。于是**一侧资格未通过时它没有地方可放**，只能塞进自由文本 `detail`
    ——读回时看不到该侧的期间、来源与定位。`ConflictSide` 给两侧各自一个位置，且逐侧可解析
    原句/值、hash、来源与 locator。
    """

    fact_id: str | None
    statement_ref: str | None
    statement_payload_hash: str | None
    source_document_key: "SM.SourceDocumentKey | None"
    locator: tuple[tuple[str, str], ...] | None
    period_state: str | None
    period_start: str | None
    period_end: str | None
    disclosure_date_state: str | None
    authority_ref: str | None
    qualification_state: str
    rejection_reason: str | None
    side_rule_version: str

    def __post_init__(self) -> None:
        if self.qualification_state not in CONFLICT_SIDE_QUALIFICATION_STATES:
            raise SchemaValidationError(
                f"ConflictSide.qualification_state={self.qualification_state!r} 不在 "
                f"{CONFLICT_SIDE_QUALIFICATION_STATES} 内")
        if self.side_rule_version != CONFLICT_RULE_VERSION:
            raise SchemaValidationError(
                f"ConflictSide.side_rule_version 必须是 {CONFLICT_RULE_VERSION!r}")
        if self.qualification_state != "qualified" and not self.rejection_reason:
            raise SchemaValidationError(
                "ConflictSide 未合格时必须写明 typed rejection_reason（不得只给一个状态）")
        has_fact = self.fact_id is not None
        has_statement = self.statement_ref is not None
        if has_fact == has_statement:
            raise SchemaValidationError(
                "ConflictSide 只能是 QualifiedFactSide（fact_id）或 ObservedStatementSide"
                "（statement_ref）之一，不得两者都有或都没有")
        if has_statement and not self.statement_payload_hash:
            raise SchemaValidationError(
                "ObservedStatementSide 必须带 statement_payload_hash（可核原句/值）")
        if has_statement and not _is_sha256_hex(self.statement_payload_hash):
            raise SchemaValidationError(
                "ConflictSide.statement_payload_hash 必须为 64 位 sha256 hex")
        if has_fact and self.qualification_state != "qualified":
            raise SchemaValidationError(
                "QualifiedFactSide 的 qualification_state 必须是 qualified"
                "（未合格的一侧不得冒充 Pack 内合格 fact）")

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "statement_ref": self.statement_ref,
            "statement_payload_hash": self.statement_payload_hash,
            "source_document_key": (self.source_document_key.to_dict()
                                    if self.source_document_key is not None else None),
            "locator": [list(x) for x in self.locator] if self.locator is not None else None,
            "period_state": self.period_state,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "disclosure_date_state": self.disclosure_date_state,
            "authority_ref": self.authority_ref,
            "qualification_state": self.qualification_state,
            "rejection_reason": self.rejection_reason,
            "side_rule_version": self.side_rule_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConflictSide":
        d = _reject_unknown(d, {
            "fact_id", "statement_ref", "statement_payload_hash", "source_document_key",
            "locator", "period_state", "period_start", "period_end", "disclosure_date_state",
            "authority_ref", "qualification_state", "rejection_reason", "side_rule_version",
        }, "ConflictSide")
        raw_key = d.get("source_document_key")
        raw_locator = d.get("locator")
        return cls(
            fact_id=_get_str(d, "fact_id", "ConflictSide", allow_none=True),
            statement_ref=_get_str(d, "statement_ref", "ConflictSide", allow_none=True),
            statement_payload_hash=_get_str(
                d, "statement_payload_hash", "ConflictSide", allow_none=True),
            source_document_key=(SM.SourceDocumentKey.from_dict(raw_key)
                                 if raw_key is not None else None),
            locator=(tuple(tuple(x) for x in raw_locator)
                     if raw_locator is not None else None),
            period_state=_get_str(d, "period_state", "ConflictSide", allow_none=True),
            period_start=_get_str(d, "period_start", "ConflictSide", allow_none=True),
            period_end=_get_str(d, "period_end", "ConflictSide", allow_none=True),
            disclosure_date_state=_get_str(
                d, "disclosure_date_state", "ConflictSide", allow_none=True),
            authority_ref=_get_str(d, "authority_ref", "ConflictSide", allow_none=True),
            qualification_state=_get_str(d, "qualification_state", "ConflictSide"),
            rejection_reason=_get_str(d, "rejection_reason", "ConflictSide", allow_none=True),
            side_rule_version=_get_str(d, "side_rule_version", "ConflictSide"),
        )


@dataclass(frozen=True)
class ResearchConflict:
    """事实冲突（相关但不足材料进入冲突区并写明原因）。

    v7（§0.3.5）：本类型**仅**承载「已证可比、取值互斥且**两侧资格均已确认**」的正式冲突。
    其 `sides`（≥2）**每一侧必须是 `QualifiedFactSide`**，每个 `fact_id` 必须解析到本 Pack 的
    合格 fact；`fact_ids` 是全部 sides 的**一一对应兼容读视图**，**绝不允许**观察侧使它少于
    sides。资格未通过或三轴未决的观察走 Pack 顶层的 `SourceComparisonAudit`，不塞进这里。
    """

    conflict_id: str
    fact_ids: tuple[str, ...]
    category: str
    detail: str
    status: str
    sides: tuple[ConflictSide, ...] = ()
    conflict_rule_version: str = CONFLICT_RULE_VERSION

    def __post_init__(self) -> None:
        if not self.conflict_id:
            raise SchemaValidationError("ResearchConflict.conflict_id 必须非空")
        _get_enum(self.category, CONFLICT_CATEGORIES, "ResearchConflict", "category")
        _get_enum(self.status, CONFLICT_STATUSES, "ResearchConflict", "status")
        if self.conflict_rule_version != CONFLICT_RULE_VERSION:
            raise SchemaValidationError(
                f"ResearchConflict.conflict_rule_version 必须是 {CONFLICT_RULE_VERSION!r}")
        if len(self.sides) < 2:
            raise SchemaValidationError(
                "ResearchConflict.sides 至少两侧（正式冲突必须有可核的两侧）")
        for side in self.sides:
            if side.fact_id is None:
                raise SchemaValidationError(
                    "正式冲突的每一侧都必须是 QualifiedFactSide（观察侧走 "
                    "SourceComparisonAudit，不得进 ResearchConflict）")
            if side.qualification_state != "qualified":
                raise SchemaValidationError(
                    "正式冲突的每一侧资格都必须已确认（qualified）")
        if self.fact_ids != tuple(s.fact_id for s in self.sides):
            raise SchemaValidationError(
                "ResearchConflict.fact_ids 必须是 sides 的一一对应兼容读视图，"
                "不得由观察侧使它少于 sides")

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "fact_ids": list(self.fact_ids),
            "category": self.category,
            "detail": self.detail,
            "status": self.status,
            "sides": [s.to_dict() for s in self.sides],
            "conflict_rule_version": self.conflict_rule_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchConflict":
        d = _reject_unknown(d, {"conflict_id", "fact_ids", "category", "detail", "status",
                                "sides", "conflict_rule_version"}, "ResearchConflict")
        return cls(
            conflict_id=_get_str(d, "conflict_id", "ResearchConflict"),
            fact_ids=_get_str_tuple(d, "fact_ids", "ResearchConflict"),
            category=_get_str(d, "category", "ResearchConflict"),
            detail=_get_str(d, "detail", "ResearchConflict", allow_empty=True) or "",
            status=_get_str(d, "status", "ResearchConflict"),
            sides=tuple(ConflictSide.from_dict(x) for x in d.get("sides") or ()),
            conflict_rule_version=_get_str(
                d, "conflict_rule_version", "ResearchConflict",
                allow_empty=True) or CONFLICT_RULE_VERSION,
        )


@dataclass(frozen=True)
class SourceComparisonAudit:
    """跨源比较审计（Pack v7 顶层；**不**写入 `ResearchConflict`）（§0.3.5，`sca-1`）。

    判定的**顺序**（不得颠倒，否则会把"未核"读成"已冲突"）：
    一侧资格 rejected/undetermined 且观察到差异 ⇒ 先记 `observed_divergence`（同时保留三轴未决
    状态）；两侧均合格而三轴有未知且无已证不相容 ⇒ `comparability_pending`；两侧均合格、三轴
    已证可比且值互斥 ⇒ 才可写正式 `ResearchConflict`。

    它**不是** gap（§0.13 第 9 条的两记录纪律）：只有 Contract 要求且**因该差异**仍未满足时，
    才**另外**形成 gap/block。
    """

    schema_version: str
    comparison_audit_id: str
    outcome: str
    sides: tuple[ConflictSide, ...]
    period_comparability: str
    caliber_comparability: str
    subject_matter_comparability: str
    unresolved_axes: tuple[str, ...]
    qualification_gaps: tuple[str, ...]
    basis_rule_ids: tuple[str, ...]
    conflict_rule_version: str = CONFLICT_RULE_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_COMPARISON_AUDIT_VERSION:
            raise SchemaValidationError(
                f"SourceComparisonAudit.schema_version 必须是 "
                f"{SOURCE_COMPARISON_AUDIT_VERSION!r}")
        if not self.comparison_audit_id:
            raise SchemaValidationError("SourceComparisonAudit.comparison_audit_id 必须非空")
        if self.outcome not in COMPARISON_OUTCOMES:
            raise SchemaValidationError(
                f"SourceComparisonAudit.outcome={self.outcome!r} 不在 {COMPARISON_OUTCOMES} 内")
        if len(self.sides) < 2:
            raise SchemaValidationError("SourceComparisonAudit.sides 至少两侧")
        for name in ("period_comparability", "caliber_comparability",
                     "subject_matter_comparability"):
            value = getattr(self, name)
            if value not in COMPARABILITY_STATES:
                raise SchemaValidationError(
                    f"SourceComparisonAudit.{name}={value!r} 不在 {COMPARABILITY_STATES} 内")
        if not set(self.unresolved_axes) <= COMPARABILITY_AXES:
            raise SchemaValidationError(
                f"SourceComparisonAudit.unresolved_axes 必须是 {COMPARABILITY_AXES} 的封闭子集")
        if self.outcome == "comparability_pending" and not self.unresolved_axes:
            raise SchemaValidationError(
                "comparability_pending 必须逐条点名待核的三轴（不得只说「待核」）")
        if self.outcome == "observed_divergence" and not self.qualification_gaps:
            raise SchemaValidationError(
                "observed_divergence 必须点名未合格侧的 typed 身份/理由")
        if self.conflict_rule_version != CONFLICT_RULE_VERSION:
            raise SchemaValidationError(
                f"SourceComparisonAudit.conflict_rule_version 必须是 {CONFLICT_RULE_VERSION!r}")
        if not self.basis_rule_ids:
            raise SchemaValidationError(
                "SourceComparisonAudit.basis_rule_ids 必须非空（不得以自由文本代替规则依据）")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "comparison_audit_id": self.comparison_audit_id,
            "outcome": self.outcome,
            "sides": [s.to_dict() for s in self.sides],
            "period_comparability": self.period_comparability,
            "caliber_comparability": self.caliber_comparability,
            "subject_matter_comparability": self.subject_matter_comparability,
            "unresolved_axes": list(self.unresolved_axes),
            "qualification_gaps": list(self.qualification_gaps),
            "basis_rule_ids": list(self.basis_rule_ids),
            "conflict_rule_version": self.conflict_rule_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceComparisonAudit":
        d = _reject_unknown(d, {
            "schema_version", "comparison_audit_id", "outcome", "sides",
            "period_comparability", "caliber_comparability", "subject_matter_comparability",
            "unresolved_axes", "qualification_gaps", "basis_rule_ids", "conflict_rule_version",
        }, "SourceComparisonAudit")
        return cls(
            schema_version=_get_str(d, "schema_version", "SourceComparisonAudit"),
            comparison_audit_id=_get_str(d, "comparison_audit_id", "SourceComparisonAudit"),
            outcome=_get_str(d, "outcome", "SourceComparisonAudit"),
            sides=tuple(ConflictSide.from_dict(x) for x in d.get("sides") or ()),
            period_comparability=_get_str(d, "period_comparability", "SourceComparisonAudit"),
            caliber_comparability=_get_str(d, "caliber_comparability", "SourceComparisonAudit"),
            subject_matter_comparability=_get_str(
                d, "subject_matter_comparability", "SourceComparisonAudit"),
            unresolved_axes=_get_str_tuple(d, "unresolved_axes", "SourceComparisonAudit"),
            qualification_gaps=_get_str_tuple(
                d, "qualification_gaps", "SourceComparisonAudit"),
            basis_rule_ids=_get_str_tuple(d, "basis_rule_ids", "SourceComparisonAudit"),
            conflict_rule_version=_get_str(
                d, "conflict_rule_version", "SourceComparisonAudit",
                allow_empty=True) or CONFLICT_RULE_VERSION,
        )


@dataclass(frozen=True)
class NotFoundAudit:
    """搜索未取得的结构化审计（只有 qualified=true 才能投影为 NOT_FOUND_AFTER_SEARCH）。"""

    audit_id: str
    policy_version: str
    required_source_scope: tuple[str, ...]
    attempted_source_types: tuple[str, ...]
    valid_attempt_count: int
    searched_need_ids: tuple[str, ...]
    context_expansion_attempted: bool
    alternative_candidate_ids: tuple[str, ...]
    alternative_sources_attempted: tuple[str, ...]
    time_window: str
    unattempted_candidate_ids: tuple[str, ...]
    budget_exhausted: bool
    qualification_reasons: tuple[str, ...]
    qualified: bool

    def __post_init__(self) -> None:
        if not self.audit_id:
            raise SchemaValidationError("NotFoundAudit.audit_id 必须非空")

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "policy_version": self.policy_version,
            "required_source_scope": list(self.required_source_scope),
            "attempted_source_types": list(self.attempted_source_types),
            "valid_attempt_count": self.valid_attempt_count,
            "searched_need_ids": list(self.searched_need_ids),
            "context_expansion_attempted": self.context_expansion_attempted,
            "alternative_candidate_ids": list(self.alternative_candidate_ids),
            "alternative_sources_attempted": list(self.alternative_sources_attempted),
            "time_window": self.time_window,
            "unattempted_candidate_ids": list(self.unattempted_candidate_ids),
            "budget_exhausted": self.budget_exhausted,
            "qualification_reasons": list(self.qualification_reasons),
            "qualified": self.qualified,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "NotFoundAudit":
        d = _reject_unknown(d, {"audit_id", "policy_version", "required_source_scope",
                                "attempted_source_types", "valid_attempt_count", "searched_need_ids",
                                "context_expansion_attempted", "alternative_candidate_ids",
                                "alternative_sources_attempted", "time_window",
                                "unattempted_candidate_ids", "budget_exhausted",
                                "qualification_reasons", "qualified"}, "NotFoundAudit")
        return cls(
            audit_id=_get_str(d, "audit_id", "NotFoundAudit"),
            policy_version=_get_str(d, "policy_version", "NotFoundAudit"),
            required_source_scope=_get_str_tuple(d, "required_source_scope", "NotFoundAudit"),
            attempted_source_types=_get_str_tuple(d, "attempted_source_types", "NotFoundAudit"),
            valid_attempt_count=_get_int(d, "valid_attempt_count", "NotFoundAudit"),
            searched_need_ids=_get_str_tuple(d, "searched_need_ids", "NotFoundAudit"),
            context_expansion_attempted=_get_bool(d, "context_expansion_attempted", "NotFoundAudit"),
            alternative_candidate_ids=_get_str_tuple(d, "alternative_candidate_ids", "NotFoundAudit"),
            alternative_sources_attempted=_get_str_tuple(d, "alternative_sources_attempted", "NotFoundAudit"),
            time_window=_get_str(d, "time_window", "NotFoundAudit"),
            unattempted_candidate_ids=_get_str_tuple(d, "unattempted_candidate_ids", "NotFoundAudit"),
            budget_exhausted=_get_bool(d, "budget_exhausted", "NotFoundAudit"),
            qualification_reasons=_get_str_tuple(d, "qualification_reasons", "NotFoundAudit"),
            qualified=_get_bool(d, "qualified", "NotFoundAudit"),
        )


@dataclass(frozen=True)
class ResearchGap:
    """缺口结构化保留（缺失事项/已查范围/原因/影响/建议材料类型/未来动作类型）。"""

    unresolved_id: str
    aspect_ids: tuple[str, ...]
    reason_code: str
    detail: str
    attempted_need_ids: tuple[str, ...]
    blocking: bool
    impact: str
    not_found_audit_id: str | None = None

    def __post_init__(self) -> None:
        if not self.unresolved_id:
            raise SchemaValidationError("ResearchGap.unresolved_id 必须非空")
        _get_enum(self.reason_code, GAP_REASON_CODES, "ResearchGap", "reason_code")
        _get_enum(self.impact, GAP_IMPACTS, "ResearchGap", "impact")

    def to_dict(self) -> dict:
        return {
            "unresolved_id": self.unresolved_id,
            "aspect_ids": list(self.aspect_ids),
            "reason_code": self.reason_code,
            "detail": self.detail,
            "attempted_need_ids": list(self.attempted_need_ids),
            "blocking": self.blocking,
            "impact": self.impact,
            "not_found_audit_id": self.not_found_audit_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchGap":
        d = _reject_unknown(d, {"unresolved_id", "aspect_ids", "reason_code", "detail",
                                "attempted_need_ids", "blocking", "impact", "not_found_audit_id"},
                            "ResearchGap")
        return cls(
            unresolved_id=_get_str(d, "unresolved_id", "ResearchGap"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "ResearchGap"),
            reason_code=_get_str(d, "reason_code", "ResearchGap"),
            detail=_get_str(d, "detail", "ResearchGap", allow_empty=True) or "",
            attempted_need_ids=_get_str_tuple(d, "attempted_need_ids", "ResearchGap"),
            blocking=_get_bool(d, "blocking", "ResearchGap"),
            impact=_get_str(d, "impact", "ResearchGap"),
            not_found_audit_id=_get_str(d, "not_found_audit_id", "ResearchGap", allow_none=True),
        )


# ---------------------------------------------------------------------------
# 运行消耗 / 预算 / 外部漏斗 / 不确定调用
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UsageEntry:
    """运行消耗单项（metric 为枚举）。"""

    metric: str
    value: int | float | str | Decimal
    unit: str

    def __post_init__(self) -> None:
        _get_enum(self.metric, USAGE_METRICS, "UsageEntry", "metric")
        if not isinstance(self.value, (int, float, str, Decimal)) or isinstance(self.value, bool):
            raise SchemaValidationError(f"UsageEntry.value 非法: {self.value!r}")

    def to_dict(self) -> dict:
        return {"metric": self.metric, "value": _to_json_value(self.value), "unit": self.unit}

    @classmethod
    def from_dict(cls, d: Any) -> "UsageEntry":
        d = _reject_unknown(d, {"metric", "value", "unit"}, "UsageEntry")
        return cls(
            metric=_get_str(d, "metric", "UsageEntry"),
            value=d.get("value"),
            unit=_get_str(d, "unit", "UsageEntry", allow_empty=True) or "",
        )


@dataclass(frozen=True)
class BudgetPolicySnapshot:
    """版本化预算政策快照。"""

    schema_version: str
    canonical_hash: str
    tier: str

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise SchemaValidationError("BudgetPolicySnapshot.schema_version 必须非空")

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "canonical_hash": self.canonical_hash,
                "tier": self.tier}

    @classmethod
    def from_dict(cls, d: Any) -> "BudgetPolicySnapshot":
        d = _reject_unknown(d, {"schema_version", "canonical_hash", "tier"}, "BudgetPolicySnapshot")
        return cls(
            schema_version=_get_str(d, "schema_version", "BudgetPolicySnapshot"),
            canonical_hash=_get_str(d, "canonical_hash", "BudgetPolicySnapshot", allow_empty=True) or "",
            tier=_get_str(d, "tier", "BudgetPolicySnapshot", allow_empty=True) or "",
        )


@dataclass(frozen=True)
class TopicUsageSnapshot:
    """Pack 运行消耗/预算快照（替代 budget_policy/cumulative_usage/stop_reason 裸字段）。

    stop_reason 仅是 usage/预算停账理由，不是第三套 Pack 状态（Pack 状态见 process/coverage 双轴）。
    """

    budget_policy: BudgetPolicySnapshot
    cumulative_usage: tuple[UsageEntry, ...]
    stop_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "budget_policy": self.budget_policy.to_dict(),
            "cumulative_usage": [u.to_dict() for u in self.cumulative_usage],
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicUsageSnapshot":
        d = _reject_unknown(d, {"budget_policy", "cumulative_usage", "stop_reason"}, "TopicUsageSnapshot")
        return cls(
            budget_policy=BudgetPolicySnapshot.from_dict(_as_dict(
                d.get("budget_policy"), "TopicUsageSnapshot", "budget_policy")),
            cumulative_usage=tuple(UsageEntry.from_dict(x) for x in _as_list(
                d.get("cumulative_usage"), "TopicUsageSnapshot", "cumulative_usage")),
            stop_reason=_get_str(d, "stop_reason", "TopicUsageSnapshot", allow_none=True),
        )


@dataclass(frozen=True)
class ExternalFunnelSnapshot:
    """版本化不透明外部漏斗快照（R1-B 不正式定义内部结构，R4 再解析；不使用任意 dict）。

    canonical_hash 即内容寻址（opaque payload 的 sha256），供 hash mismatch fail-closed 复验；
    R1-B 的 mock Pack 若无外部漏斗可用 None，不得伪造空审计对象。
    """

    schema_version: str
    canonical_hash: str
    producer_version: str

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise SchemaValidationError("ExternalFunnelSnapshot.schema_version 必须非空")
        if not _is_sha256_hex(self.canonical_hash):
            raise SchemaValidationError("ExternalFunnelSnapshot.canonical_hash 必须为 64 位 sha256 hex")
        if not self.producer_version:
            raise SchemaValidationError("ExternalFunnelSnapshot.producer_version 必须非空")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "canonical_hash": self.canonical_hash,
            "producer_version": self.producer_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalFunnelSnapshot":
        d = _reject_unknown(d, {"schema_version", "canonical_hash", "producer_version"},
                            "ExternalFunnelSnapshot")
        return cls(
            schema_version=_get_str(d, "schema_version", "ExternalFunnelSnapshot"),
            canonical_hash=_get_str(d, "canonical_hash", "ExternalFunnelSnapshot"),
            producer_version=_get_str(d, "producer_version", "ExternalFunnelSnapshot"),
        )


def verify_external_funnel_payload(snapshot: ExternalFunnelSnapshot, payload_bytes: bytes) -> bool:
    """复验外部漏斗 payload：sha256(payload) == canonical_hash。dangling/hash mismatch → False。"""
    return hashlib.sha256(payload_bytes).hexdigest() == snapshot.canonical_hash


@dataclass(frozen=True)
class UncertainToolCallRecord:
    """不确定工具调用记录（请求已发出但响应未确认持久化；不假设从未执行）。"""

    call_key: str
    tool_name: str
    input_fingerprint: str
    output_fingerprint: str
    outcome: str
    recorded_fingerprint: str

    def __post_init__(self) -> None:
        if not self.call_key or not self.tool_name:
            raise SchemaValidationError("UncertainToolCallRecord.call_key/tool_name 必须非空")

    def to_dict(self) -> dict:
        return {
            "call_key": self.call_key,
            "tool_name": self.tool_name,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
            "outcome": self.outcome,
            "recorded_fingerprint": self.recorded_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "UncertainToolCallRecord":
        d = _reject_unknown(d, {"call_key", "tool_name", "input_fingerprint", "output_fingerprint",
                                "outcome", "recorded_fingerprint"}, "UncertainToolCallRecord")
        return cls(
            call_key=_get_str(d, "call_key", "UncertainToolCallRecord"),
            tool_name=_get_str(d, "tool_name", "UncertainToolCallRecord"),
            input_fingerprint=_get_str(d, "input_fingerprint", "UncertainToolCallRecord",
                                       allow_empty=True) or "",
            output_fingerprint=_get_str(d, "output_fingerprint", "UncertainToolCallRecord",
                                        allow_empty=True) or "",
            outcome=_get_str(d, "outcome", "UncertainToolCallRecord", allow_empty=True) or "",
            recorded_fingerprint=_get_str(d, "recorded_fingerprint", "UncertainToolCallRecord",
                                          allow_empty=True) or "",
        )


# ---------------------------------------------------------------------------
# 双轴状态 + 派生记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackProcessStatus:
    """研究流程是否已停止（正交于 coverage）。"""

    status: str
    hard_stop_reason: str | None = None

    def __post_init__(self) -> None:
        _get_enum(self.status, PACK_PROCESS_STATUSES, "PackProcessStatus", "status")
        if self.status in ("blocked", "stopped_by_budget", "failed") and not self.hard_stop_reason:
            raise SchemaValidationError(
                f"PackProcessStatus.status={self.status!r} 必须提供 hard_stop_reason")

    def to_dict(self) -> dict:
        return {"status": self.status, "hard_stop_reason": self.hard_stop_reason}

    @classmethod
    def from_dict(cls, d: Any) -> "PackProcessStatus":
        d = _reject_unknown(d, {"status", "hard_stop_reason"}, "PackProcessStatus")
        return cls(
            status=_get_str(d, "status", "PackProcessStatus"),
            hard_stop_reason=_get_str(d, "hard_stop_reason", "PackProcessStatus", allow_none=True),
        )


@dataclass(frozen=True)
class PackCoverageStatus:
    """required aspect 内容覆盖（正交于 process）。"""

    status: str
    covered_aspect_ids: tuple[str, ...]
    gap_aspect_ids: tuple[str, ...]
    not_applicable_aspect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _get_enum(self.status, PACK_COVERAGE_STATUSES, "PackCoverageStatus", "status")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "covered_aspect_ids": list(self.covered_aspect_ids),
            "gap_aspect_ids": list(self.gap_aspect_ids),
            "not_applicable_aspect_ids": list(self.not_applicable_aspect_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "PackCoverageStatus":
        d = _reject_unknown(d, {"status", "covered_aspect_ids", "gap_aspect_ids",
                                "not_applicable_aspect_ids"}, "PackCoverageStatus")
        return cls(
            status=_get_str(d, "status", "PackCoverageStatus"),
            covered_aspect_ids=_get_str_tuple(d, "covered_aspect_ids", "PackCoverageStatus"),
            gap_aspect_ids=_get_str_tuple(d, "gap_aspect_ids", "PackCoverageStatus"),
            not_applicable_aspect_ids=_get_str_tuple(d, "not_applicable_aspect_ids", "PackCoverageStatus"),
        )


@dataclass(frozen=True)
class AspectStatusEntry:
    """StatusDerivation.per_aspect 的类型化条目（非无约束二元字符串 tuple）。"""

    aspect_id: str
    aspect_status: str

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("AspectStatusEntry.aspect_id 必须非空")
        _get_enum(self.aspect_status, ASPECT_RESULT_STATUSES, "AspectStatusEntry", "aspect_status")

    def to_dict(self) -> dict:
        return {"aspect_id": self.aspect_id, "aspect_status": self.aspect_status}

    @classmethod
    def from_dict(cls, d: Any) -> "AspectStatusEntry":
        d = _reject_unknown(d, {"aspect_id", "aspect_status"}, "AspectStatusEntry")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "AspectStatusEntry"),
            aspect_status=_get_str(d, "aspect_status", "AspectStatusEntry"),
        )


@dataclass(frozen=True)
class StatusDerivation:
    """类型化、版本化的确定性推导记录（derive_pack_status 输出）。"""

    schema_version: str
    rule_version: str
    derivation_fingerprint: str
    per_aspect: tuple[AspectStatusEntry, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "rule_version": self.rule_version,
            "derivation_fingerprint": self.derivation_fingerprint,
            "per_aspect": [e.to_dict() for e in self.per_aspect],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "StatusDerivation":
        d = _reject_unknown(d, {"schema_version", "rule_version", "derivation_fingerprint",
                                "per_aspect"}, "StatusDerivation")
        return cls(
            schema_version=_get_str(d, "schema_version", "StatusDerivation"),
            rule_version=_get_str(d, "rule_version", "StatusDerivation"),
            derivation_fingerprint=_get_str(d, "derivation_fingerprint", "StatusDerivation",
                                            allow_empty=True) or "",
            per_aspect=tuple(AspectStatusEntry.from_dict(x) for x in _as_list(
                d.get("per_aspect"), "StatusDerivation", "per_aspect")),
        )


# ---------------------------------------------------------------------------
# 原子结果 / 资格 / aspect 结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrozenSourcePolicySnapshot:
    """冻结 SourcePolicy 的最小投影（sufficiency 复算所需的独立冻结输入）。

    只携带可确定性复算 sufficiency 的字段；绝不含 Pack 自证布尔（不信任调用方）。
    key_industry_topics 由 source_policy.load_source_policy 派生，不硬编码。
    """

    policy_id: str
    policy_version: str
    content_fingerprint: str
    key_industry_topics: tuple[str, ...] = ()
    key_conclusion_rule: str = KEY_CONCLUSION_RULE
    key_conclusion_rule_version: str = KEY_CONCLUSION_RULE_VERSION

    def __post_init__(self) -> None:
        if not self.policy_id or not self.policy_version:
            raise SchemaValidationError("FrozenSourcePolicySnapshot.policy_id/policy_version 必须非空")
        if not _is_sha256_hex(self.content_fingerprint):
            raise SchemaValidationError("FrozenSourcePolicySnapshot.content_fingerprint 必须为 64 位 sha256 hex")
        if not self.key_conclusion_rule or not self.key_conclusion_rule_version:
            raise SchemaValidationError("FrozenSourcePolicySnapshot.key_conclusion_rule/version 必须非空")

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "content_fingerprint": self.content_fingerprint,
            "key_industry_topics": list(self.key_industry_topics),
            "key_conclusion_rule": self.key_conclusion_rule,
            "key_conclusion_rule_version": self.key_conclusion_rule_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FrozenSourcePolicySnapshot":
        d = _reject_unknown(d, {"policy_id", "policy_version", "content_fingerprint",
                                "key_industry_topics", "key_conclusion_rule",
                                "key_conclusion_rule_version"}, "FrozenSourcePolicySnapshot")
        return cls(
            policy_id=_get_str(d, "policy_id", "FrozenSourcePolicySnapshot"),
            policy_version=_get_str(d, "policy_version", "FrozenSourcePolicySnapshot"),
            content_fingerprint=_get_str(d, "content_fingerprint", "FrozenSourcePolicySnapshot"),
            key_industry_topics=_get_str_tuple(d, "key_industry_topics", "FrozenSourcePolicySnapshot"),
            key_conclusion_rule=_get_str(d, "key_conclusion_rule", "FrozenSourcePolicySnapshot"),
            key_conclusion_rule_version=_get_str(d, "key_conclusion_rule_version", "FrozenSourcePolicySnapshot"),
        )


class SourcePolicyResolver(Protocol):
    """R1-B 最小 SourcePolicy 解析边界（依赖注入；不直接信任调用方构造的 FrozenSourcePolicySnapshot）。

    resolve 从独立冻结来源按 SourcePolicyRef 解析不可变政策投影；不可解析 / dangling → None。
    """

    def resolve(self, source_policy_ref: SourcePolicyRef) -> FrozenSourcePolicySnapshot | None:
        """解析 SourcePolicyRef 到不可变冻结投影；dangling → None。"""
        ...


def verify_frozen_source_policy(source_policy_ref: SourcePolicyRef,
                                resolved: FrozenSourcePolicySnapshot | None) -> FrozenSourcePolicySnapshot:
    """校验 resolver 返回的冻结 SourcePolicy 与 SourcePolicyRef 身份闭合一致（Fix 1 fail-closed）。

    dangling / policy_id 不一致 / policy_version 不一致 / content_fingerprint 不一致 /
    key_industry_topics 为空（伪造）/ key_conclusion_rule·version 与可信常量不一致 → 全部拒绝。
    绝不把调用方任意构造的 FrozenSourcePolicySnapshot 当作可信输入，也绝不通过空 topic 名单
    或省略 resolver 关闭 sufficiency gate。
    """
    if resolved is None:
        raise SchemaValidationError(
            f"SourcePolicyRef 无法解析（dangling）：{source_policy_ref.policy_id}"
            f"@{source_policy_ref.policy_version}")
    if resolved.policy_id != source_policy_ref.policy_id:
        raise SchemaValidationError(
            f"SourcePolicy policy_id 不一致：期望 {source_policy_ref.policy_id!r}，"
            f"得到 {resolved.policy_id!r}")
    if resolved.policy_version != source_policy_ref.policy_version:
        raise SchemaValidationError(
            f"SourcePolicy policy_version 不一致：期望 {source_policy_ref.policy_version!r}，"
            f"得到 {resolved.policy_version!r}")
    if resolved.content_fingerprint != source_policy_ref.content_fingerprint:
        raise SchemaValidationError(
            f"SourcePolicy content_fingerprint 不一致：期望 {source_policy_ref.content_fingerprint!r}，"
            f"得到 {resolved.content_fingerprint!r}")
    if not resolved.key_industry_topics:
        raise SchemaValidationError("SourcePolicy key_industry_topics 为空（伪造；fail-closed）")
    if resolved.key_conclusion_rule != KEY_CONCLUSION_RULE:
        raise SchemaValidationError(
            f"SourcePolicy key_conclusion_rule 不一致：期望 {KEY_CONCLUSION_RULE!r}，"
            f"得到 {resolved.key_conclusion_rule!r}")
    if resolved.key_conclusion_rule_version != KEY_CONCLUSION_RULE_VERSION:
        raise SchemaValidationError(
            f"SourcePolicy key_conclusion_rule_version 不一致：期望 {KEY_CONCLUSION_RULE_VERSION!r}，"
            f"得到 {resolved.key_conclusion_rule_version!r}")
    return resolved


@dataclass(frozen=True)
class SupportEligibilityAssessment:
    """usage-scope gate 派生结果（Fix 1）：某 aspect 允许哪些来源类作 required / supplemental。

    由冻结 EvidenceRequirementRef.source_classes/authority 确定性派生
    （derive_support_eligibility），不硬编码 Topic ID，不含自由文本。
    """

    aspect_id: str
    policy_version: str
    required_source_classes: tuple[str, ...]
    supplemental_only_source_classes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("SupportEligibilityAssessment.aspect_id 必须非空")
        if not self.policy_version:
            raise SchemaValidationError("SupportEligibilityAssessment.policy_version 必须非空")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "policy_version": self.policy_version,
            "required_source_classes": list(self.required_source_classes),
            "supplemental_only_source_classes": list(self.supplemental_only_source_classes),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SupportEligibilityAssessment":
        d = _reject_unknown(d, {"aspect_id", "policy_version", "required_source_classes",
                                "supplemental_only_source_classes"}, "SupportEligibilityAssessment")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SupportEligibilityAssessment"),
            policy_version=_get_str(d, "policy_version", "SupportEligibilityAssessment"),
            required_source_classes=_get_str_tuple(d, "required_source_classes", "SupportEligibilityAssessment"),
            supplemental_only_source_classes=_get_str_tuple(d, "supplemental_only_source_classes",
                                                           "SupportEligibilityAssessment"),
        )


# 空扩读（expansions == ()）产生的 trace 指纹 == sha256("")：不得被当作「真实扩读已执行」的
# 闭合证明。§三：空证明缺口——violation() 必须拒绝该指纹。
_EMPTY_TRACE_FINGERPRINT = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class EnumerationBoundaryProof:
    """typed、确定性、可审计的枚举边界闭合输入（§六 / R2 item 6）。

    由真实 ``ExpansionResult/Trace/UnreadScope`` 经 set_enumeration.derive_enumeration_boundary_proof
    派生，绑定「该 aspect 的声明披露边界是否已读尽」所需的结构信号。``FormalSetEnumerationVerifier``
    只有在 ``violation() is None``（边界可确定 + 无未读候选 + 无工具错误 + 无预算耗尽 +
    无 dangling 显式引用 + 无未闭合续表）且枚举成员与实际 payload 一致时才返回
    ``material_type_supported=True``。不把调用者自报 ``scope_complete=True`` 或「同 section_path」
    当证明。

    指纹确定性（§三）：本 dataclass 与 ``trace_fingerprint`` 绝不绑定随机 trace_id/run_id/call_id/
    result_trace_id/timestamp/日志路径；只绑定工具身份、归一化参数、输出身份、逐块结果、结果状态/
    错误码、停止原因、未读范围、component material IDs 与 dependency fingerprint。
    """

    aspect_id: str
    seed_evidence_ids: tuple[str, ...]
    document_id: str
    document_version: str
    evidence_set_version: str
    source_boundary_identity: str
    component_material_ids: tuple[str, ...]
    trace_fingerprint: str
    direction_stop_reasons: tuple[tuple[str, str], ...]
    unread_candidate_refs: tuple[str, ...]
    unresolved_explicit_refs: tuple[str, ...]
    unclosed_continuations: tuple[str, ...]
    tool_errors: tuple[str, ...]
    budget_exhausted: bool
    dependency_fingerprint: str

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("EnumerationBoundaryProof.aspect_id 必须非空")
        if not _is_sha256_hex(self.trace_fingerprint):
            raise SchemaValidationError(
                "EnumerationBoundaryProof.trace_fingerprint 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.dependency_fingerprint):
            raise SchemaValidationError(
                "EnumerationBoundaryProof.dependency_fingerprint 必须为 64 位 sha256 hex")

    def violation(self) -> str | None:
        """返回第一条不满足的闭合条件（None == 边界闭合输入自洽，可继续枚举）。

        §三（空证明缺口）：除工具错误/预算/未读/未闭合/dangling 外，还必须拒绝空的
        document_id/document_version/evidence_set_version/source_boundary_identity/
        seed_evidence_ids/component_material_ids，以及「空扩读」（expansions == ()）产生的
        空 trace 指纹（== sha256("")），否则调用者可用空字段或空扩读伪造「已闭合」。
        """
        if not self.document_id:
            return "来源边界不可确定（缺 document_id）"
        if not self.document_version:
            return "来源边界不可确定（缺 document_version）"
        if not self.evidence_set_version:
            return "来源边界不可确定（缺 evidence_set_version）"
        if not self.source_boundary_identity:
            return "来源边界不可确定（缺 source_boundary_identity）"
        if not self.seed_evidence_ids:
            return "无种子证据（缺 seed_evidence_ids）"
        if not self.component_material_ids:
            return "无组件材料（缺 component_material_ids）"
        if self.trace_fingerprint == _EMPTY_TRACE_FINGERPRINT:
            return "trace 指纹为空扩读（无真实 expansion 输入，不得视为已闭合）"
        if self.tool_errors:
            return f"存在工具错误: {self.tool_errors[0]}"
        if self.budget_exhausted:
            return "预算耗尽提前停止"
        if self.unresolved_explicit_refs:
            return f"存在 dangling explicit reference: {self.unresolved_explicit_refs[0]}"
        if self.unclosed_continuations:
            return f"存在未闭合 table continuation: {self.unclosed_continuations[0]}"
        if self.unread_candidate_refs:
            return f"边界内仍有未读候选: {self.unread_candidate_refs[0]}"
        return None

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "seed_evidence_ids": list(self.seed_evidence_ids),
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "source_boundary_identity": self.source_boundary_identity,
            "component_material_ids": list(self.component_material_ids),
            "trace_fingerprint": self.trace_fingerprint,
            "direction_stop_reasons": [list(p) for p in self.direction_stop_reasons],
            "unread_candidate_refs": list(self.unread_candidate_refs),
            "unresolved_explicit_refs": list(self.unresolved_explicit_refs),
            "unclosed_continuations": list(self.unclosed_continuations),
            "tool_errors": list(self.tool_errors),
            "budget_exhausted": self.budget_exhausted,
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EnumerationBoundaryProof":
        d = _reject_unknown(d, {
            "aspect_id", "seed_evidence_ids", "document_id", "document_version",
            "evidence_set_version", "source_boundary_identity", "component_material_ids",
            "trace_fingerprint", "direction_stop_reasons", "unread_candidate_refs",
            "unresolved_explicit_refs", "unclosed_continuations", "tool_errors",
            "budget_exhausted", "dependency_fingerprint",
        }, "EnumerationBoundaryProof")
        stops_raw = d.get("direction_stop_reasons")
        if stops_raw is None:
            stops_raw = []
        if not isinstance(stops_raw, list) or not all(
                isinstance(p, list) and len(p) == 2
                and all(isinstance(x, str) for x in p) for p in stops_raw):
            raise SchemaValidationError(
                "EnumerationBoundaryProof.direction_stop_reasons 必须为 [[str, str], ...]")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "EnumerationBoundaryProof"),
            seed_evidence_ids=_get_str_tuple(d, "seed_evidence_ids", "EnumerationBoundaryProof"),
            document_id=_get_str(d, "document_id", "EnumerationBoundaryProof",
                                 allow_empty=True) or "",
            document_version=_get_str(d, "document_version", "EnumerationBoundaryProof",
                                      allow_empty=True) or "",
            evidence_set_version=_get_str(d, "evidence_set_version", "EnumerationBoundaryProof",
                                          allow_empty=True) or "",
            source_boundary_identity=_get_str(d, "source_boundary_identity",
                                              "EnumerationBoundaryProof", allow_empty=True) or "",
            component_material_ids=_get_str_tuple(d, "component_material_ids",
                                                  "EnumerationBoundaryProof"),
            trace_fingerprint=_get_str(d, "trace_fingerprint", "EnumerationBoundaryProof"),
            direction_stop_reasons=tuple((p[0], p[1]) for p in stops_raw),
            unread_candidate_refs=_get_str_tuple(d, "unread_candidate_refs",
                                                 "EnumerationBoundaryProof"),
            unresolved_explicit_refs=_get_str_tuple(d, "unresolved_explicit_refs",
                                                    "EnumerationBoundaryProof"),
            unclosed_continuations=_get_str_tuple(d, "unclosed_continuations",
                                                  "EnumerationBoundaryProof"),
            tool_errors=_get_str_tuple(d, "tool_errors", "EnumerationBoundaryProof"),
            budget_exhausted=_get_bool(d, "budget_exhausted", "EnumerationBoundaryProof"),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "EnumerationBoundaryProof"),
        )


# ---------------------------------------------------------------------------
# 跨文档联合检索 §L1.5 / §L4：来源集、检索前责任、四类臂（Pack v7 顶层）
# ---------------------------------------------------------------------------

class ResponsibilityDerivationError(Exception):
    """责任记录无法由 **§L1.5 输入轴**确定性派生（fail-closed，绝不退回"猜一个"）。"""


#: 责任记录 `basis` 的**封闭** rule_id 集。未登记的理由不得出现；新增理由必须在此显式表态，
#: 否则「这条责任是怎么来的」会在读回时退化成自由文本。
RESPONSIBILITY_BASIS_RULE_IDS = frozenset({
    # 判断二（`retrieval_required=false` 的两个合法来源之一的 ①）
    "not_in_required_scope",
    # 判断二（合法来源 ②）与本批逐份调度的来由：`required_any_of` 只声明**来源类**的支撑
    # 资格，**不等于**该类下每份 PDF 都是 Contract 强制的检索对象。本批「来源类 → 该类的
    # 全部树文档成员逐份尝试」是 `asr-1` 的**批次调度规则**。
    "batch_scheduling_rule",
    # 判断三：3a 第一约束
    "aspect_not_set_complete_no_enumeration_proof",
    # 判断三：3b 逐份表
    "supplemental_only_never_proves_completeness",
    "current_state_source_proves_completeness",
    "cross_period_responsibility_not_expressible_in_frozen_contract",
    "ambiguous_current_state_no_proof_domain",
})

#: 臂 C1（`not_required`，「未尝试，因为责任记录说无需检索」）可引用的 rule_id **恰好**是这一对。
#: c 轮收紧：`supplemental_only_never_proves_completeness` 是 **proof** 依据，**永不**是
#: 「无需检索」的依据——`supplemental_only` 且 `retrieval_required=true` 的成员属于臂 A 或 B。
NOT_REQUIRED_BASIS_RULE_IDS = frozenset({
    "not_in_required_scope",
    "batch_scheduling_rule",
})

#: 上传文档经树工具进入系统后的 Contract 来源类（**唯一**一处桥接，不新增词汇）。
#:
#: 冻结 Contract 的 `source_classes` 取值域是
#: `company_industry / structured_db / external / financial`（`standard_v3.yaml`，逐值可核）。
#: 上传 PDF 的登记侧另有 `material_group`（`company_industry / financial / project`），但它
#: **不改变**该 PDF 产出材料的来源类：`evidence/builder.py` 把上传文档的正文一律建成
#: `evidence` 权威材料，而 `AUTHORITY_SOURCE_CLASS_BY_TYPE["evidence"] == "company_industry"`。
#: 因此**不得**用 `material_group` 当成员来源类：一份被登记为 `financial` 分组的年报，其正文
#: 材料仍以 `company_industry` 权威进入系统，按 `material_group` 判就会把「Contract 要求检索的
#: 年报」误判成 `retrieval_required=false`（臂 C1「无需检索」）——那是 fail-open，会让一份必须
#: 查的文档被静默跳过。反过来取 `company_industry` 只会**多**要求检索，最坏留 gap/partial。
UPLOADED_DOCUMENT_SOURCE_CLASS = AUTHORITY_SOURCE_CLASS_BY_TYPE["evidence"]


def _basis_row(rule_id: str, detail: str) -> tuple[str, str]:
    if rule_id not in RESPONSIBILITY_BASIS_RULE_IDS:
        raise ResponsibilityDerivationError(
            f"basis rule_id={rule_id!r} 不在封闭集 {sorted(RESPONSIBILITY_BASIS_RULE_IDS)} 内")
    if not isinstance(detail, str) or detail == "":
        raise ResponsibilityDerivationError(f"basis rule_id={rule_id!r} 的 detail 必须非空")
    return (rule_id, detail)


@dataclass(frozen=True)
class AspectSourceResponsibility:
    """某个 `(aspect, 来源文档)` 的**检索前**责任三元组（§L1.5）。

    **为什么 `proof_required` 是三值而不是布尔（3d，不得"简化"回去）**：
    把 `undetermined` 压成 `false`，效果就是「仅当前来源必然为真」——即用**一个不存在的映射**
    去**支撑**一个通过结论，与「倒填责任」是同一种错误的两个方向；压成 `true`，则会要求
    无人能验证的证明（`EnumerationBoundaryProof` 在结构上就拒绝为空材料来源造证明）。
    两种压缩都会让读回的人**看不到「这里其实没判定」**，所以「无法确定」必须是一等结论。

    本记录**只**由 §L1.5 的输入轴派生（`coverage_rules` / `required_any_of` /
    `supplemental_only`、来源角色、来源类）；**不得**含任何结果字段——无 `material_found`、
    无 `attempted`、无 `hit`。结果在 `SourceAspectOutcome` 那个**另一个**对象里。
    """

    aspect_id: str
    source_document_key: "SM.SourceDocumentKey"
    in_source_set: bool
    retrieval_required: bool
    proof_required: str
    basis: tuple[tuple[str, str], ...]
    rule_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.aspect_id, str) or self.aspect_id == "":
            raise ResponsibilityDerivationError(
                "AspectSourceResponsibility.aspect_id 必须为非空字符串")
        if not isinstance(self.source_document_key, SM.SourceDocumentKey):
            raise ResponsibilityDerivationError(
                "AspectSourceResponsibility.source_document_key 必须为 SourceDocumentKey")
        if not isinstance(self.in_source_set, bool):
            raise ResponsibilityDerivationError(
                "AspectSourceResponsibility.in_source_set 必须为 bool")
        if not isinstance(self.retrieval_required, bool):
            raise ResponsibilityDerivationError(
                "AspectSourceResponsibility.retrieval_required 必须为 bool")
        if self.proof_required not in PROOF_REQUIRED_VALUES:
            raise ResponsibilityDerivationError(
                f"AspectSourceResponsibility.proof_required={self.proof_required!r} 不在三值域 "
                f"{PROOF_REQUIRED_VALUES} 内")
        if self.rule_version != ASSUME_RULE_VERSION:
            raise ResponsibilityDerivationError(
                f"AspectSourceResponsibility.rule_version 必须是 {ASSUME_RULE_VERSION!r}")
        if not self.basis:
            raise ResponsibilityDerivationError(
                "AspectSourceResponsibility.basis 必须至少有一行 typed 理由")
        for row in self.basis:
            if (not isinstance(row, tuple) or len(row) != 2
                    or row[0] not in RESPONSIBILITY_BASIS_RULE_IDS
                    or not isinstance(row[1], str) or row[1] == ""):
                raise ResponsibilityDerivationError(
                    f"AspectSourceResponsibility.basis 含非法行 {row!r}")
        rule_ids = {row[0] for row in self.basis}
        if not self.retrieval_required and "not_in_required_scope" not in rule_ids:
            raise ResponsibilityDerivationError(
                "retrieval_required=false 必须由 not_in_required_scope 依据支撑")
        if self.proof_required == "true" and not self.retrieval_required:
            raise ResponsibilityDerivationError(
                "proof_required='true' 蕴含 retrieval_required=true（未查的来源不可能有证明域）")
        if self.proof_required == "undetermined" and not self.in_source_set:
            raise ResponsibilityDerivationError(
                "源集之外不得产生 undetermined 责任边（本记录只对源集成员产生）")

    def fingerprint(self) -> str:
        return sha256_canonical(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "source_document_key": self.source_document_key.to_dict(),
            "in_source_set": self.in_source_set,
            "retrieval_required": self.retrieval_required,
            "proof_required": self.proof_required,
            "basis": [list(row) for row in self.basis],
            "rule_version": self.rule_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AspectSourceResponsibility":
        d = _reject_unknown(d, {
            "aspect_id", "source_document_key", "in_source_set", "retrieval_required",
            "proof_required", "basis", "rule_version",
        }, "AspectSourceResponsibility")
        basis = d.get("basis")
        if not isinstance(basis, (list, tuple)):
            raise ResponsibilityDerivationError("AspectSourceResponsibility.basis 必须是列表")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "AspectSourceResponsibility"),
            source_document_key=SM.SourceDocumentKey.from_dict(d["source_document_key"]),
            in_source_set=_get_bool(d, "in_source_set", "AspectSourceResponsibility"),
            retrieval_required=_get_bool(d, "retrieval_required", "AspectSourceResponsibility"),
            proof_required=_get_str(d, "proof_required", "AspectSourceResponsibility"),
            basis=tuple(tuple(row) for row in basis),
            rule_version=_get_str(d, "rule_version", "AspectSourceResponsibility"),
        )


@dataclass(frozen=True)
class DocumentSourceSet:
    """一次运行纳入的**有序**来源集（§L4.2）。每成员是 `(SourceDocumentKey, source_role)`。

    有序即语义：序位取自 L0 清单的 `retrieval_order`，报告与逐份台账都按它读，**不得**重排。
    这里**不**要求恰一个 `current_state_source`：期间不可核实的源集（`ambiguous_current_state` /
    `no_eligible_current`）经 `_admit_source_set` 阻断，**不构造** `TopicRunContext`，但它的成员
    责任台账仍必须完整存在（§0.3.4 Y-17），所以本类型只挡「多于一个当前锚」。
    """

    members: tuple[tuple["SM.SourceDocumentKey", str], ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise ResponsibilityDerivationError("DocumentSourceSet 必须非空")
        seen: list[SM.SourceDocumentKey] = []
        for member in self.members:
            if not isinstance(member, tuple) or len(member) != 2:
                raise ResponsibilityDerivationError(
                    f"DocumentSourceSet 成员必须是 (SourceDocumentKey, source_role)，得到 {member!r}")
            key, role = member
            if not isinstance(key, SM.SourceDocumentKey):
                raise ResponsibilityDerivationError(
                    "DocumentSourceSet 成员的 key 必须为 SourceDocumentKey")
            if role not in SM.SOURCE_ROLES:
                raise ResponsibilityDerivationError(
                    f"DocumentSourceSet 成员的 source_role={role!r} 不在 {SM.SOURCE_ROLES} 内")
            if key.document_id in {k.document_id for k in seen}:
                raise ResponsibilityDerivationError(
                    f"DocumentSourceSet 出现重复 document_id={key.document_id!r}")
            if seen and not seen[0].same_subject_as(key):
                raise ResponsibilityDerivationError(
                    "DocumentSourceSet 成员主体不一致（fail-closed，不取多数票）")
            seen.append(key)
        anchors = [k for k, role in self.members if role == "current_state_source"]
        if len(anchors) > 1:
            raise ResponsibilityDerivationError(
                f"DocumentSourceSet 至多一个 current_state_source，得到 {len(anchors)} 个")

    def keys(self) -> tuple["SM.SourceDocumentKey", ...]:
        return tuple(key for key, _ in self.members)

    @classmethod
    def single_document(cls, *, company_id: str, document_id: str, document_version: str,
                        evidence_set_version: str,
                        source_role: str = "current_state_source") -> "DocumentSourceSet":
        """单文档源集（§L4 的退化情形：一次运行只纳入一份文档）。

        这是**真实且常见**的情形，不是测试捷径：它照常走本类型构造器，全部校验（非空、主体
        一致、至多一个当前锚）一条不落。写成具名构造器是为了让「这份 Pack 是单文档研究」
        在读回时一眼可见，而不是散落在各处的字面量。
        """
        return cls(members=((
            SM.SourceDocumentKey(company_id=company_id, document_id=document_id,
                                 document_version=document_version,
                                 evidence_set_version=evidence_set_version),
            source_role,
        ),))

    def roles(self) -> tuple[str, ...]:
        return tuple(role for _, role in self.members)

    def member_key(self, key: "SM.SourceDocumentKey") -> "SM.SourceDocumentKey":
        """按**完整四轴**取回本来源集里的成员键（定点返修 T4）。

        匹配单位是 :func:`harness.source_manifest.source_key_axes` 的四元组，**不是**
        `document_id`。同 `document_id` 而 `document_version` / `evidence_set_version`
        不同的键**不是**本集合的成员：那条路必须 fail-closed，且错误信息要如实说出「同 id、
        不同身份」，不能退化成一句「不在集合内」——后者会把「身份写错了」读成「漏登记了」。
        """
        if not isinstance(key, SM.SourceDocumentKey):
            raise ResponsibilityDerivationError(
                f"来源集成员匹配只接受 SourceDocumentKey，实际 {type(key).__name__}")
        axes = SM.source_key_axes(key)
        for k, _ in self.members:
            if SM.source_key_axes(k) == axes:
                return k
        near = [k for k, _ in self.members if SM.same_document_id_only(k, key)]
        if near:
            raise ResponsibilityDerivationError(
                f"同 document_id、不同文档身份：本来源集里 {key.document_id!r} 的成员是 "
                f"{[k.to_dict() for k in near]}，而查询键是 {key.to_dict()}。"
                "四轴必须全同——不得按 document_id 就近匹配（fail-closed）")
        raise ResponsibilityDerivationError(
            f"source_document_key={key.to_dict()} 不在本来源集内（不兜底）")

    def has_member(self, key: "SM.SourceDocumentKey") -> bool:
        """四轴身份成员判定（**不**抛错的读法；同 id 错版本一律为 `False`）。"""
        axes = SM.source_key_axes(key)
        return any(SM.source_key_axes(k) == axes for k, _ in self.members)

    def role_of(self, key: "SM.SourceDocumentKey") -> str:
        member = self.member_key(key)
        axes = SM.source_key_axes(member)
        for k, role in self.members:
            if SM.source_key_axes(k) == axes:
                return role
        raise ResponsibilityDerivationError(  # pragma: no cover - member_key 已保证命中
            f"source_document_key={member.to_dict()} 不在本来源集内（不兜底）")

    def current_state_key(self) -> "SM.SourceDocumentKey | None":
        for key, role in self.members:
            if role == "current_state_source":
                return key
        return None

    def __len__(self) -> int:
        return len(self.members)

    def fingerprint(self) -> str:
        """源集内容身份（不含 run_id / 路径 / 入库时间；供 Pack 与报告复算比对）。"""
        return sha256_canonical(
            [{"key": key.to_dict(), "source_role": role} for key, role in self.members])

    def to_dict(self) -> dict:
        return {
            "members": [{"source_document_key": key.to_dict(), "source_role": role}
                        for key, role in self.members],
            "source_set_fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DocumentSourceSet":
        d = _reject_unknown(d, {"members", "source_set_fingerprint"}, "DocumentSourceSet")
        raw = d.get("members")
        if not isinstance(raw, (list, tuple)):
            raise ResponsibilityDerivationError("DocumentSourceSet.members 必须是列表")
        return cls(members=tuple(
            (SM.SourceDocumentKey.from_dict(m["source_document_key"]), m["source_role"])
            for m in raw))


@dataclass(frozen=True)
class KeyedSourceBoundaryProof:
    """按来源文档**带键**的枚举边界证明包装（§0.2.2）。

    `EnumerationBoundaryProof` 本体与它的 `violation()` **一个字节都不改**：那条结构性
    anti-fabrication（空 `seed_evidence_ids` / 空扩读 / 未读候选任一存在即拒绝）恰好就是
    「无材料来源不可能有非空证明」的不变量。**键不进 proof 本体的字段集与本体指纹**；但
    `(source_document_key, proof 原指纹)` 必须进入**包装记录及 Pack 的内容身份**——换键必须
    换 Pack 内容身份，禁止把证明挪给另一份文档而不留痕。
    """

    source_document_key: "SM.SourceDocumentKey"
    proof: "EnumerationBoundaryProof"

    def __post_init__(self) -> None:
        if not isinstance(self.source_document_key, SM.SourceDocumentKey):
            raise SchemaValidationError(
                "KeyedSourceBoundaryProof.source_document_key 必须为 SourceDocumentKey")
        if not isinstance(self.proof, EnumerationBoundaryProof):
            raise SchemaValidationError(
                "KeyedSourceBoundaryProof.proof 必须为 EnumerationBoundaryProof")

    def to_dict(self) -> dict:
        return {"source_document_key": self.source_document_key.to_dict(),
                "proof": self.proof.to_dict()}

    @classmethod
    def from_dict(cls, d: Any) -> "KeyedSourceBoundaryProof":
        d = _reject_unknown(d, {"source_document_key", "proof"}, "KeyedSourceBoundaryProof")
        return cls(
            source_document_key=SM.SourceDocumentKey.from_dict(d["source_document_key"]),
            proof=EnumerationBoundaryProof.from_dict(d["proof"]),
        )


@dataclass(frozen=True)
class SourceSearchAttempt:
    """**一次真实工具调用**（§0.3.3）。未发出的调用**不造实例**——所以「从未发出」不可能有 `call_id`。

    `actual_source_key` 在失败/未归因时可以为空，**绝不**拿请求值冒充实际读到的文档：身份回声
    只证明「分派到了这一份」，**不证明**该份的范围被覆盖。
    """

    call_ordinal: int
    call_id: str
    requested_source_key: "SM.SourceDocumentKey"
    actual_source_key: "SM.SourceDocumentKey | None"
    requested_scope: tuple[tuple[str, str], ...]
    unread_scope: tuple[str, ...]
    status: str
    error_code: str | None
    stop_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.call_ordinal, int) or self.call_ordinal < 0:
            raise SchemaValidationError("SourceSearchAttempt.call_ordinal 必须为非负整数")
        if not self.call_id:
            raise SchemaValidationError("SourceSearchAttempt.call_id 必须非空")
        if not isinstance(self.requested_source_key, SM.SourceDocumentKey):
            raise SchemaValidationError(
                "SourceSearchAttempt.requested_source_key 必须为 SourceDocumentKey")
        if self.actual_source_key is not None \
                and not isinstance(self.actual_source_key, SM.SourceDocumentKey):
            raise SchemaValidationError(
                "SourceSearchAttempt.actual_source_key 必须为 SourceDocumentKey 或 None")
        if self.status not in ATTEMPT_STATUSES:
            raise SchemaValidationError(
                f"SourceSearchAttempt.status={self.status!r} 不在 {ATTEMPT_STATUSES} 内")

    def to_dict(self) -> dict:
        return {
            "call_ordinal": self.call_ordinal,
            "call_id": self.call_id,
            "requested_source_key": self.requested_source_key.to_dict(),
            "actual_source_key": (self.actual_source_key.to_dict()
                                  if self.actual_source_key is not None else None),
            "requested_scope": [list(x) for x in self.requested_scope],
            "unread_scope": list(self.unread_scope),
            "status": self.status,
            "error_code": self.error_code,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceSearchAttempt":
        d = _reject_unknown(d, {
            "call_ordinal", "call_id", "requested_source_key", "actual_source_key",
            "requested_scope", "unread_scope", "status", "error_code", "stop_reason",
        }, "SourceSearchAttempt")
        raw_key = d.get("actual_source_key")
        return cls(
            call_ordinal=d["call_ordinal"],
            call_id=_get_str(d, "call_id", "SourceSearchAttempt"),
            requested_source_key=SM.SourceDocumentKey.from_dict(d["requested_source_key"]),
            actual_source_key=(SM.SourceDocumentKey.from_dict(raw_key)
                               if raw_key is not None else None),
            requested_scope=tuple(tuple(x) for x in d.get("requested_scope") or ()),
            unread_scope=_get_str_tuple(d, "unread_scope", "SourceSearchAttempt"),
            status=_get_str(d, "status", "SourceSearchAttempt"),
            error_code=_get_str(d, "error_code", "SourceSearchAttempt", allow_none=True),
            stop_reason=_get_str(d, "stop_reason", "SourceSearchAttempt", allow_none=True),
        )


@dataclass(frozen=True)
class SourceSearchOutcomeRecord:
    """臂 **B / C2** 的逐 `(aspect, 文档)` 记录（§0.3.3，版本 `ssor-2`）。

    **为什么不能只给 `NotFoundAudit` 加一个 `source_document_key` 字段**：那 14 个字段是
    **aspect 级**的，装得下「该 aspect 在哪些来源类上试过」，**装不下**「**这一份文档自己的**
    成功调用集合 / 该次请求声明的搜索范围 / 该次返回的未读范围 / 该次调用自己的停止原因」。
    只加一个键，等于给一个 aspect 级记录贴一张标签，四项证据仍然无处可放。

    **多个调用如何合成该文档的停止原因（确定性折叠，不得改）**：
    1. `attempts` 按**真实调用序号**保留每条原始记录（含失败），合成**不覆盖**原始证据；
       未发出的 C2 只记 typed `unfulfilled_reason`，**不得**造 `call_id`（含
       `dispatched_no_candidate`：派发发生过、导航给过结论，但一次调用都没有发出）；
    2. 若**没有任何**成功调用 ⇒ **不是臂 B**，落臂 C2；
    3. 若存在完成实际检查的调用 ⇒ 可落臂 B；`synthesized_stop_reason` 按**该文档实际调用序号**
       取第一条失败/截断/预算停止的结局，无此类调用时取 `NOT_FOUND_AFTER_SEARCH`；**不能**按
       源集序位（源集序位是文档排序，不是调用顺序）；
    4. `qualified=true` 的必要充分条件：该文档每条相关调用均为完整 `SUCCESS/EMPTY`、请求范围
       并集覆盖该 aspect 声明范围、未读范围为空、未因预算停止且既有七条件全数成立
       （`PARTIAL` 或失败不算完整）。
    """

    record_version: str
    aspect_id: str
    source_document_key: "SM.SourceDocumentKey"
    arm: str
    audit_id: str | None
    attempts: tuple[SourceSearchAttempt, ...]
    synthesized_stop_reason: str | None
    searched_need_ids: tuple[str, ...]
    valid_attempt_count: int
    time_window: str
    qualified: bool
    qualification_reasons: tuple[str, ...]
    projected_terminal: str
    unfulfilled_reason: str | None
    responsibility_fingerprint: str
    impact: str | None

    def __post_init__(self) -> None:
        if self.record_version != SOURCE_SEARCH_OUTCOME_RECORD_VERSION:
            if self.record_version in LEGACY_SOURCE_SEARCH_OUTCOME_RECORD_VERSIONS:
                raise SchemaValidationError(
                    f"SourceSearchOutcomeRecord.record_version={self.record_version!r} 是"
                    f"**已登记的历史版本**"
                    f"（{LEGACY_SOURCE_SEARCH_OUTCOME_RECORD_VERSIONS!r}）：current reader 拒绝，"
                    f"且现场没有该版本的历史读回通道——不得静默按 "
                    f"{SOURCE_SEARCH_OUTCOME_RECORD_VERSION!r} 解释（两版的臂 C2 取值域不同）。")
            raise SchemaValidationError(
                f"SourceSearchOutcomeRecord.record_version 必须是 "
                f"{SOURCE_SEARCH_OUTCOME_RECORD_VERSION!r}")
        if not self.aspect_id:
            raise SchemaValidationError("SourceSearchOutcomeRecord.aspect_id 必须非空")
        if not isinstance(self.source_document_key, SM.SourceDocumentKey):
            raise SchemaValidationError(
                "SourceSearchOutcomeRecord.source_document_key 必须为 SourceDocumentKey")
        if self.arm not in ("B", "C2"):
            raise SchemaValidationError(
                f"SourceSearchOutcomeRecord 只承载臂 B/C2（实际 arm={self.arm!r}）")
        if self.projected_terminal not in PROJECTED_TERMINALS:
            raise SchemaValidationError(
                f"SourceSearchOutcomeRecord.projected_terminal={self.projected_terminal!r} "
                f"不在 {PROJECTED_TERMINALS} 内")
        # 投影终态与 arm/qualified 双向校验（§0.3.3）：三者互斥，不得混用。
        if self.arm == "C2":
            if self.projected_terminal != "UNFULFILLED_REQUIRED_SEARCH":
                raise SchemaValidationError(
                    "臂 C2 的投影终态只能是 UNFULFILLED_REQUIRED_SEARCH")
            if self.unfulfilled_reason not in UNFULFILLED_REASONS:
                raise SchemaValidationError(
                    f"臂 C2 的 unfulfilled_reason={self.unfulfilled_reason!r} 不在 "
                    f"{UNFULFILLED_REASONS} 内")
            if self.impact is not None and self.impact not in UNFULFILLED_IMPACTS:
                raise SchemaValidationError(
                    f"臂 C2 的 impact={self.impact!r} 不在 {UNFULFILLED_IMPACTS} 内")
        else:
            expected = ("NOT_FOUND_AFTER_SEARCH" if self.qualified
                        else "UNQUALIFIED_SEARCH_OBSERVATION")
            if self.projected_terminal != expected:
                raise SchemaValidationError(
                    f"臂 B qualified={self.qualified} 的投影终态必须是 {expected!r}，"
                    f"得到 {self.projected_terminal!r}")
            if self.unfulfilled_reason is not None:
                raise SchemaValidationError("臂 B 不得带 unfulfilled_reason（那是臂 C2 专有）")
            if self.qualified and self.audit_id is None:
                # 判**合格**未命中必须挂在一张真实的 aspect 级证书上：只有身份回声不足以支撑
                # 「在声明范围内已查过」这个结论（§7 禁止项 21）。
                #
                # `qualified=false` 时**允许** `audit_id is None`：那只发生在该 aspect 在别的
                # 来源上取得了材料、因而根本没有 aspect 级审计的情形。此时本份的观察照记
                # （臂 B、逐条 typed 理由、投影 UNQUALIFIED_SEARCH_OBSERVATION），只是没有
                # 证书可挂——**不得**逼实现就地补一张（那是看见结果后倒填资格）。
                raise SchemaValidationError(
                    "臂 B 判为合格未命中时必须引用其 aspect 级 NotFoundAudit 的 id"
                    "（不得只凭身份回声判未命中）")
        if not _is_sha256_hex(self.responsibility_fingerprint):
            raise SchemaValidationError(
                "SourceSearchOutcomeRecord.responsibility_fingerprint 必须为 64 位 sha256 hex")
        if self.qualified and self.qualification_reasons:
            raise SchemaValidationError(
                "qualified=true 时不得同时带 qualification_reasons（自相矛盾）")
        if not self.qualified and self.arm == "B" and not self.qualification_reasons:
            raise SchemaValidationError(
                "合格未命中不成立时必须逐条写明理由（不得只给一个布尔）")

    def to_dict(self) -> dict:
        return {
            "record_version": self.record_version,
            "aspect_id": self.aspect_id,
            "source_document_key": self.source_document_key.to_dict(),
            "arm": self.arm,
            "audit_id": self.audit_id,
            "attempts": [a.to_dict() for a in self.attempts],
            "synthesized_stop_reason": self.synthesized_stop_reason,
            "searched_need_ids": list(self.searched_need_ids),
            "valid_attempt_count": self.valid_attempt_count,
            "time_window": self.time_window,
            "qualified": self.qualified,
            "qualification_reasons": list(self.qualification_reasons),
            "projected_terminal": self.projected_terminal,
            "unfulfilled_reason": self.unfulfilled_reason,
            "responsibility_fingerprint": self.responsibility_fingerprint,
            "impact": self.impact,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceSearchOutcomeRecord":
        d = _reject_unknown(d, {
            "record_version", "aspect_id", "source_document_key", "arm", "audit_id", "attempts",
            "synthesized_stop_reason", "searched_need_ids", "valid_attempt_count", "time_window",
            "qualified", "qualification_reasons", "projected_terminal", "unfulfilled_reason",
            "responsibility_fingerprint", "impact",
        }, "SourceSearchOutcomeRecord")
        return cls(
            record_version=_get_str(d, "record_version", "SourceSearchOutcomeRecord"),
            aspect_id=_get_str(d, "aspect_id", "SourceSearchOutcomeRecord"),
            source_document_key=SM.SourceDocumentKey.from_dict(d["source_document_key"]),
            arm=_get_str(d, "arm", "SourceSearchOutcomeRecord"),
            audit_id=_get_str(d, "audit_id", "SourceSearchOutcomeRecord", allow_none=True),
            attempts=tuple(SourceSearchAttempt.from_dict(a) for a in d.get("attempts") or ()),
            synthesized_stop_reason=_get_str(
                d, "synthesized_stop_reason", "SourceSearchOutcomeRecord", allow_none=True),
            searched_need_ids=_get_str_tuple(d, "searched_need_ids", "SourceSearchOutcomeRecord"),
            valid_attempt_count=d["valid_attempt_count"],
            # `time_window` 取自 `NotFoundAudit`；**臂 C2 没有审计**（没查成 / 从未发出），
            # 因此它的时间窗**如实为空**——不得为一个未完成的检索编造一个窗口。类型层不要求
            # 非空（见 `__post_init__`），故这里也允许空串，读写必须能往返。
            time_window=_get_str(d, "time_window", "SourceSearchOutcomeRecord",
                                 allow_empty=True),
            qualified=_get_bool(d, "qualified", "SourceSearchOutcomeRecord"),
            qualification_reasons=_get_str_tuple(
                d, "qualification_reasons", "SourceSearchOutcomeRecord"),
            projected_terminal=_get_str(d, "projected_terminal", "SourceSearchOutcomeRecord"),
            unfulfilled_reason=_get_str(
                d, "unfulfilled_reason", "SourceSearchOutcomeRecord", allow_none=True),
            responsibility_fingerprint=_get_str(
                d, "responsibility_fingerprint", "SourceSearchOutcomeRecord"),
            impact=_get_str(d, "impact", "SourceSearchOutcomeRecord", allow_none=True),
        )


@dataclass(frozen=True)
class SourceAspectOutcome:
    """**四类臂的封闭包装**（§0.3.3）：每个 `(aspect_id, source_document_key)` 恰一条。

    必备字段随臂而定，`__post_init__` 逐臂强制——这样「顶层少一条 A/C1 行」或「B/C2 丢了
    `search_record`」在**类型层**就被拒绝，partial Pack 也不例外。
    """

    aspect_id: str
    source_document_key: "SM.SourceDocumentKey"
    arm: str
    responsibility_fingerprint: str
    material_ids: tuple[str, ...] = ()
    search_record: SourceSearchOutcomeRecord | None = None
    not_required_basis: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("SourceAspectOutcome.aspect_id 必须非空")
        if not isinstance(self.source_document_key, SM.SourceDocumentKey):
            raise SchemaValidationError(
                "SourceAspectOutcome.source_document_key 必须为 SourceDocumentKey")
        if self.arm not in SOURCE_ASPECT_OUTCOME_ARMS:
            raise SchemaValidationError(
                f"SourceAspectOutcome.arm={self.arm!r} 不在 {SOURCE_ASPECT_OUTCOME_ARMS} 内")
        if not _is_sha256_hex(self.responsibility_fingerprint):
            raise SchemaValidationError(
                "SourceAspectOutcome.responsibility_fingerprint 必须为 64 位 sha256 hex")
        if self.arm == "A":
            if not self.material_ids:
                raise SchemaValidationError(
                    "臂 A 必须带可核的材料 ID（有材料归属，不得为空）")
            if self.search_record is not None or self.not_required_basis:
                raise SchemaValidationError(
                    "臂 A 不得带未命中记录或「无需检索」依据")
        elif self.arm == "C1":
            if self.material_ids:
                raise SchemaValidationError("臂 C1（未尝试）不得带材料归属")
            if not self.not_required_basis:
                raise SchemaValidationError(
                    "臂 C1 必须引用 §L1.5 的 typed 依据（不得只说「无需检索」而说不出理由）")
            rule_ids = {row[0] for row in self.not_required_basis}
            if not rule_ids <= NOT_REQUIRED_BASIS_RULE_IDS:
                raise SchemaValidationError(
                    f"臂 C1 的 rule_id 集必须 ⊆ {sorted(NOT_REQUIRED_BASIS_RULE_IDS)}，"
                    f"实际 {sorted(rule_ids)}")
            if self.search_record is not None:
                raise SchemaValidationError(
                    "臂 C1 不得带未命中记录（没查过不能说「查了没有」）")
        else:
            if self.material_ids:
                raise SchemaValidationError(
                    f"臂 {self.arm} 不得带材料归属（那是臂 A 的记录）")
            if self.not_required_basis:
                raise SchemaValidationError(
                    f"臂 {self.arm} 不得带「无需检索」依据（它是必须检索的）")
            if self.search_record is None:
                raise SchemaValidationError(
                    f"臂 {self.arm} 必须带 SourceSearchOutcomeRecord（不得只给一个 arm 标签）")
            if self.search_record.arm != self.arm:
                raise SchemaValidationError(
                    f"SourceAspectOutcome.arm={self.arm!r} 与 "
                    f"search_record.arm={self.search_record.arm!r} 不一致")
            if self.search_record.aspect_id != self.aspect_id:
                raise SchemaValidationError(
                    "search_record.aspect_id 必须与本记录一致")
            if self.search_record.source_document_key.document_id \
                    != self.source_document_key.document_id:
                raise SchemaValidationError(
                    "search_record 必须属于本记录所指的那一份文档")
            if self.search_record.responsibility_fingerprint != self.responsibility_fingerprint:
                raise SchemaValidationError(
                    "search_record 必须绑定本记录同一条检索前责任指纹")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "source_document_key": self.source_document_key.to_dict(),
            "arm": self.arm,
            "responsibility_fingerprint": self.responsibility_fingerprint,
            "material_ids": list(self.material_ids),
            "search_record": (self.search_record.to_dict()
                              if self.search_record is not None else None),
            "not_required_basis": [list(row) for row in self.not_required_basis],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceAspectOutcome":
        d = _reject_unknown(d, {
            "aspect_id", "source_document_key", "arm", "responsibility_fingerprint",
            "material_ids", "search_record", "not_required_basis",
        }, "SourceAspectOutcome")
        raw_record = d.get("search_record")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SourceAspectOutcome"),
            source_document_key=SM.SourceDocumentKey.from_dict(d["source_document_key"]),
            arm=_get_str(d, "arm", "SourceAspectOutcome"),
            responsibility_fingerprint=_get_str(
                d, "responsibility_fingerprint", "SourceAspectOutcome"),
            material_ids=_get_str_tuple(d, "material_ids", "SourceAspectOutcome"),
            search_record=(SourceSearchOutcomeRecord.from_dict(raw_record)
                           if raw_record is not None else None),
            not_required_basis=tuple(
                tuple(row) for row in d.get("not_required_basis") or ()),
        )


@dataclass(frozen=True)
class SetCompletenessAssessment:
    """set_complete 的类型化证明（Fix 3）：在明确权威披露范围内完整归拢枚举。

    绑定 material 身份 + 文档版本 + 章节/表边界 + expected/observed 成员 + 排除理由 +
    supporting material/fact + scope_complete + 评估器/推导版本 + Contract/dependency 指纹 +
    boundary_proof（§六：枚举边界闭合输入，set_complete 必须携带；Store 与 verifier 对缺失
    fail-closed）。非 set_complete aspect 可为 None。
    """

    aspect_id: str
    rule_version: str
    source_material_ids: tuple[str, ...]
    document_version: str
    source_boundary: str
    expected_member_ids: tuple[str, ...]
    observed_member_ids: tuple[str, ...]
    excluded_member_ids: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    supporting_material_ids: tuple[str, ...]
    supporting_fact_ids: tuple[str, ...]
    scope_complete: bool
    assessor_version: str
    contract_sha256: str
    dependency_fingerprint: str
    boundary_proof: "EnumerationBoundaryProof | None" = None
    #: Pack v7：按来源文档**带键**的证明（**稀疏**子序列）。长度上界是源集大小，
    #: 但**不**要求等长：只有「有材料 ∧ `proof_required == "true"`」的成员才有 proof，
    #: 臂 B/C1/C2 的成员**按构造没有** proof（且不得为它们造空 proof）。源集 1 份时二者
    #: 也未必相等——该份落臂 B/C1/C2 时 proof 数**恰为 0**。
    source_set_proofs: tuple["KeyedSourceBoundaryProof", ...] = ()

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("SetCompletenessAssessment.aspect_id 必须非空")
        # 兼容读视图 `boundary_proof` 与 `source_set_proofs` 必须是**同一个**真值，不得双真值：
        # 恰一条 ⇒ 等于该条的 proof；两条以上 ⇒ None（多来源的集合完整性必须逐键读，不能塌成
        # 一份）。**「零条」这一情形故意不在本类型里下判**：一份没有逐来源证明的 set_complete
        # 是合法还是伪造，取决于该 Pack 的跨源轴状态（`declared` / `not_declared`）——那是
        # `TopicResearchPack` 才知道的事。本类型只知道自己的字段，硬在这里拦会同时误杀
        # 「单文档、未声明跨源轴」的合法 Pack。该判据的落点在
        # `verify_pack_source_set_ledger`（它看得见轴状态）。
        keys = [k.source_document_key.document_id for k in self.source_set_proofs]
        if len(set(keys)) != len(keys):
            raise SchemaValidationError(
                "SetCompletenessAssessment.source_set_proofs 不得出现重复的 source_document_key")
        if len(self.source_set_proofs) == 1:
            if self.boundary_proof != self.source_set_proofs[0].proof:
                raise SchemaValidationError(
                    "boundary_proof 兼容读视图与唯一一条 source_set_proofs 必须一致")
        elif self.source_set_proofs:
            if self.boundary_proof is not None:
                raise SchemaValidationError(
                    "多条 source_set_proofs 时 boundary_proof 必须为 None（不得塌成一份）")
        if not self.rule_version:
            raise SchemaValidationError("SetCompletenessAssessment.rule_version 必须非空")
        if not self.source_material_ids:
            raise SchemaValidationError("SetCompletenessAssessment.source_material_ids 必须非空")
        if not self.document_version or not self.source_boundary:
            raise SchemaValidationError("SetCompletenessAssessment.document_version/source_boundary 必须非空")
        if not self.expected_member_ids:
            raise SchemaValidationError("SetCompletenessAssessment.expected_member_ids 必须非空")
        if len(self.excluded_member_ids) != len(self.exclusion_reasons):
            raise SchemaValidationError(
                "SetCompletenessAssessment.excluded_member_ids 与 exclusion_reasons 必须一一对应")
        if not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("SetCompletenessAssessment.contract_sha256 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.dependency_fingerprint):
            raise SchemaValidationError("SetCompletenessAssessment.dependency_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "rule_version": self.rule_version,
            "source_material_ids": list(self.source_material_ids),
            "document_version": self.document_version,
            "source_boundary": self.source_boundary,
            "expected_member_ids": list(self.expected_member_ids),
            "observed_member_ids": list(self.observed_member_ids),
            "excluded_member_ids": list(self.excluded_member_ids),
            "exclusion_reasons": list(self.exclusion_reasons),
            "supporting_material_ids": list(self.supporting_material_ids),
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "scope_complete": self.scope_complete,
            "assessor_version": self.assessor_version,
            "contract_sha256": self.contract_sha256,
            "dependency_fingerprint": self.dependency_fingerprint,
            "boundary_proof": (self.boundary_proof.to_dict()
                               if self.boundary_proof is not None else None),
            "source_set_proofs": [k.to_dict() for k in self.source_set_proofs],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SetCompletenessAssessment":
        d = _reject_unknown(d, {
            "aspect_id", "rule_version", "source_material_ids", "document_version",
            "source_boundary", "expected_member_ids", "observed_member_ids", "excluded_member_ids",
            "exclusion_reasons", "supporting_material_ids", "supporting_fact_ids", "scope_complete",
            "assessor_version", "contract_sha256", "dependency_fingerprint", "boundary_proof",
            "source_set_proofs",
        }, "SetCompletenessAssessment")
        bp_raw = d.get("boundary_proof")
        boundary_proof = EnumerationBoundaryProof.from_dict(bp_raw) if bp_raw is not None else None
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SetCompletenessAssessment"),
            rule_version=_get_str(d, "rule_version", "SetCompletenessAssessment"),
            source_material_ids=_get_str_tuple(d, "source_material_ids", "SetCompletenessAssessment"),
            document_version=_get_str(d, "document_version", "SetCompletenessAssessment"),
            source_boundary=_get_str(d, "source_boundary", "SetCompletenessAssessment"),
            expected_member_ids=_get_str_tuple(d, "expected_member_ids", "SetCompletenessAssessment"),
            observed_member_ids=_get_str_tuple(d, "observed_member_ids", "SetCompletenessAssessment"),
            excluded_member_ids=_get_str_tuple(d, "excluded_member_ids", "SetCompletenessAssessment"),
            exclusion_reasons=_get_str_tuple(d, "exclusion_reasons", "SetCompletenessAssessment"),
            supporting_material_ids=_get_str_tuple(d, "supporting_material_ids", "SetCompletenessAssessment"),
            supporting_fact_ids=_get_str_tuple(d, "supporting_fact_ids", "SetCompletenessAssessment"),
            scope_complete=_get_bool(d, "scope_complete", "SetCompletenessAssessment"),
            assessor_version=_get_str(d, "assessor_version", "SetCompletenessAssessment",
                                      allow_empty=True) or "",
            contract_sha256=_get_str(d, "contract_sha256", "SetCompletenessAssessment"),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "SetCompletenessAssessment"),
            boundary_proof=boundary_proof,
            source_set_proofs=tuple(
                KeyedSourceBoundaryProof.from_dict(x)
                for x in d.get("source_set_proofs") or ()),
        )


@dataclass(frozen=True)
class SetCompletenessVerdict:
    """SetCompletenessVerifier 的确定性判定（不信任 Pack 自填 scope_complete）。"""

    set_complete: bool
    verifier_version: str
    reason: str = ""


class SetCompletenessVerifier(Protocol):
    """R1-B 最小 set_complete 可信评估边界（依赖注入；确定性复算集合关系）。

    不得直接信任 SetCompletenessAssessment.scope_complete；由 verifier 从集合关系 +
    依赖指纹确定性复算 set_complete。verifier 缺失 / 版本不符 / 身份不符 / 复算 False →
    fail-closed。
    """

    def verify(self, assessment: "SetCompletenessAssessment",
               dependency_fingerprint: str) -> SetCompletenessVerdict | None:
        """复算 set_complete；无法验证 → None。"""
        ...


def compute_set_completeness_verdict(assessment: "SetCompletenessAssessment",
                                     dependency_fingerprint: str) -> SetCompletenessVerdict:
    """确定性复算 set_complete 集合关系（引用实现；不信任 scope_complete 布尔）。

    校验：成员 ID 唯一非空；observed ∩ excluded 空；expected = observed ∪ excluded；
    每个 excluded 有非空 reason；dependency_fingerprint 与当前 Pack/Requirement 严格一致。
    contract_sha256 / rule_version / assessor_version / 成员绑定由 Store 单独强校验。
    """
    reasons: list[str] = []
    expected = assessment.expected_member_ids
    observed = assessment.observed_member_ids
    excluded = assessment.excluded_member_ids
    excl_reasons = assessment.exclusion_reasons
    all_members = expected + observed + excluded
    if any(not m for m in all_members):
        reasons.append("member id 为空")
    if len(set(expected)) != len(expected):
        reasons.append("expected 含重复 member id")
    if len(set(observed)) != len(observed):
        reasons.append("observed 含重复 member id")
    if len(set(excluded)) != len(excluded):
        reasons.append("excluded 含重复 member id")
    if set(observed) & set(excluded):
        reasons.append("observed ∩ excluded 非空")
    if set(expected) != (set(observed) | set(excluded)):
        reasons.append("expected != observed ∪ excluded")
    if len(excluded) != len(excl_reasons) or any(not r for r in excl_reasons):
        reasons.append("excluded 成员缺非空 reason")
    if assessment.dependency_fingerprint != dependency_fingerprint:
        reasons.append("dependency_fingerprint 与当前 Pack/Requirement 不一致")
    return SetCompletenessVerdict(
        set_complete=(len(reasons) == 0),
        verifier_version=SET_COMPLETENESS_VERIFIER_VERSION,
        reason="; ".join(reasons),
    )


@dataclass(frozen=True)
class SetEnumerationResult:
    """set_complete 的独立枚举结果（Fix 2）：受信任枚举器产出的确定性枚举成员集合。

    不含 Pack 自证布尔。enumerated_member_ids 是枚举器从明确边界 + payload 枚举得到的成员集合；
    payload_hash / boundary_identity 由枚举器对真实 payload 与边界确定性计算，Store 据此与
    assessment 自填 expected/observed/excluded 及实际解析 payload 身份/hash 交叉复核。
    material_type_supported=False 表示该 material 类型无法枚举（Store fail-closed）。

    信任边界：这些字段是「受信任、版本化、确定性的枚举器」的自报结果。Store 只能校验它们
    与真实解析 payload 的身份/哈希/边界/集合关系是否一致，无法证明该枚举器内部确实读取过
    payload bytes。R2 由唯一正式组合入口注入正式枚举器后才建立该信任。
    """

    material_type_supported: bool
    enumerated_member_ids: tuple[str, ...] = ()
    payload_hash: str = ""
    boundary_identity: str = ""
    verifier_version: str = ""
    reason: str = ""
    # 修复 B：源对象清单 → 恢复结果 闭环（ExpectedSourceObjectInventory.to_dict()）。
    # 仅 set_complete 摊平恢复路径产出；结构式枚举或不可枚举时为 None。
    source_object_inventory: dict | None = None

    def __post_init__(self) -> None:
        if not self.verifier_version:
            raise SchemaValidationError("SetEnumerationResult.verifier_version 必须非空")
        if self.material_type_supported:
            if not _is_sha256_hex(self.payload_hash):
                raise SchemaValidationError(
                    "SetEnumerationResult.payload_hash 必须为 64 位 sha256 hex")
            if not self.boundary_identity:
                raise SchemaValidationError("SetEnumerationResult.boundary_identity 必须非空")
            if not self.enumerated_member_ids:
                raise SchemaValidationError("SetEnumerationResult.enumerated_member_ids 必须非空")
            if any(not m for m in self.enumerated_member_ids):
                raise SchemaValidationError("SetEnumerationResult.enumerated_member_ids 含空成员")

    def to_dict(self) -> dict:
        return {
            "material_type_supported": self.material_type_supported,
            "enumerated_member_ids": list(self.enumerated_member_ids),
            "payload_hash": self.payload_hash,
            "boundary_identity": self.boundary_identity,
            "verifier_version": self.verifier_version,
            "reason": self.reason,
            "source_object_inventory": self.source_object_inventory,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SetEnumerationResult":
        d = _reject_unknown(d, {
            "material_type_supported", "enumerated_member_ids", "payload_hash",
            "boundary_identity", "verifier_version", "reason",
            "source_object_inventory"}, "SetEnumerationResult")
        return cls(
            material_type_supported=_get_bool(d, "material_type_supported", "SetEnumerationResult"),
            enumerated_member_ids=_get_str_tuple(d, "enumerated_member_ids", "SetEnumerationResult"),
            payload_hash=_get_str(d, "payload_hash", "SetEnumerationResult", allow_empty=True) or "",
            boundary_identity=_get_str(d, "boundary_identity", "SetEnumerationResult",
                                       allow_empty=True) or "",
            verifier_version=_get_str(d, "verifier_version", "SetEnumerationResult"),
            reason=_get_str(d, "reason", "SetEnumerationResult", allow_empty=True) or "",
            source_object_inventory=d.get("source_object_inventory"),
        )


class SetEnumerationVerifier(Protocol):
    """R1-B 最小独立枚举边界（Fix 2）：受信任、版本化、确定性的运行时依赖。

    枚举器从 Store 解析出的 resolved_payloads 枚举集合成员，并确定性计算 payload_hash /
    boundary_identity。Store 交叉复核 material/payload 身份、payload hash、document version、
    source boundary、dependency fingerprint、以及 enumerated/expected/observed/excluded 集合关系；
    任何一项不符 → fail-closed。

    信任边界（不得高估）：Store 无法证明一个任意注入的 Python 实现「内部确实读取过 payload
    bytes」；payload 缺失 / bytes 不可用 / 边界不可验证 / 不支持该 material 类型 → 返回
    material_type_supported=False 或 None（Store fail-closed）。

    R2 硬门：R2 必须实现正式、版本化、确定性的文档枚举器，且必须由唯一正式组合入口注入；
    正式枚举器接线之前，生产运行链不得将 set_complete aspect 提升为 covered。R2 后续计划必须
    把枚举器版本纳入 dependency fingerprint（本轮只记录该硬门，不实现 R2）。测试 fake 只证明
    接口与 Store 绑定关系成立，不代表正式文档枚举已实现。

    版本绑定（§五.7 / item 7）：实现必须暴露 ``verifier_version == SET_ENUMERATION_VERIFIER_VERSION``，
    Store 门禁据此与 ``requirement.dependency_versions["set_enumerator"]`` 精确比对；缺键/空/不匹配
    → fail-closed。测试 fake 亦须携带该版本（否则 Store 拒绝 set_complete 提交）。
    """

    verifier_version: str

    def enumerate(self, assessment: "SetCompletenessAssessment",
                  materials: tuple["ResearchMaterial", ...],
                  resolved_payloads: tuple["ResolvedPayload", ...],
                  dependency_fingerprint: str) -> SetEnumerationResult | None:
        """枚举成员；无法枚举（payload 缺失/bytes 不可用/不支持类型）→ None 或 supported=False。"""
        ...


def compute_boundary_identity(document_version: str, source_boundary: str) -> str:
    """边界身份指纹（Fix 2）：把 document_version + source_boundary 固化为确定性 sha256。

    枚举器与 Store 各自对同一 (document_version, source_boundary) 计算，须一致；不一致即
    枚举边界与 assessment 自填边界不匹配（fail-closed）。
    """
    return sha256_canonical({
        "document_version": document_version,
        "source_boundary": source_boundary,
    })


def compute_source_payload_hash(resolved_payloads: tuple["ResolvedPayload", ...]) -> str:
    """来源 payload 集合的确定性哈希（枚举器与 Store 各自独立计算，须一致）。

    以 content_hash（已由 verify_material_payload_ref 复核 == sha256(payload_bytes)）为规范形，
    使枚举器与 Store 无需各自重算字节哈希即得同一值；绑定枚举结果到真实 payload 身份。
    枚举结果 payload_hash 与该值不一致 → 判定枚举结果与真实解析 payload 身份不一致（fail-closed）。
    """
    return sha256_canonical([rp.content_hash for rp in resolved_payloads])


def derive_support_eligibility(snap: TopicAspectRequirementSnapshot) -> SupportEligibilityAssessment:
    """从冻结 EvidenceRequirementRef 派生 aspect 的 usage-scope（Fix 1）。

    required = 所有 required_any_of 组 source_classes 的并集；supplemental_only = 各 ref 的
    supplemental_only 并集。无冻结使用资格信息（source_classes/authority 全空）→ 两者皆空
    （Store 此时不触发 usage-scope gate，仅权威门 + coverage + sufficiency）。
    """
    required: set[str] = set()
    supplemental: set[str] = set()
    for er in snap.evidence_requirement_ids:
        if er.authority is not None:
            for grp in er.authority.required_any_of:
                required |= set(grp.source_classes)
            supplemental |= set(er.authority.supplemental_only)
        else:
            required |= set(er.source_classes)
    return SupportEligibilityAssessment(
        aspect_id=snap.aspect_id,
        policy_version=SUPPORT_ELIGIBILITY_POLICY_VERSION,
        required_source_classes=tuple(sorted(required)),
        supplemental_only_source_classes=tuple(sorted(supplemental)),
    )


def recompute_sufficiency(aspect: AspectResearchResult,
                          facts: tuple[SupportedFact, ...],
                          source_policy: FrozenSourcePolicySnapshot | None) -> SufficiencyAssessment | None:
    """确定性复算 sufficiency（Fix 4）。规则来源 = 冻结输入，绝不硬编码 Topic 名单。

    优先级：transmission_layers（四层各判）> key_industry_topics（key_conclusion_ab_c）> 无门。
    复算 threshold_met / independent_c_count / supporting ids 全部来自 facts 的真实 authority
    grade / canonical domain / source class；调用方自填 SufficiencyAssessment 必须与本函数一致。
    """
    snap = aspect.requirement_snapshot
    layers = snap.transmission_layers
    layer = layers[0] if layers else None

    fact_ids = tuple(f.fact_id for f in facts)
    source_ids = tuple(authority_source_identity(f.source_authority) for f in facts)
    fact_types = tuple(f.fact_type for f in facts)
    triples = [(authority_source_class(f.source_authority),
                authority_source_grade(f.source_authority),
                authority_independence_domain(f.source_authority))
               for f in facts]

    def build(rule: str, rule_version: str, threshold_met: bool,
              independent_c_count: int) -> SufficiencyAssessment:
        return SufficiencyAssessment(
            aspect_id=aspect.aspect_id, conclusion_id=None,
            supporting_fact_ids=fact_ids, supporting_source_ids=source_ids,
            rule=rule, rule_version=rule_version, threshold_met=threshold_met,
            independent_c_count=independent_c_count, assessor_version=SUFFICIENCY_ASSESSOR_VERSION,
        )

    if layer in TRANSMISSION_SUFFICIENCY_RULES:
        rule, rule_version = TRANSMISSION_SUFFICIENCY_RULES[layer]
        if layer in ("company_exposure", "actual_company_impact"):
            # 公司暴露 / 实际影响必须由公司披露（非 external）支撑；external 仅 supplemental。
            non_external = any(sc != "external" for sc, _g, _d in triples)
            return build(rule, rule_version, non_external, 0)
        if layer == "conditional_transmission":
            # 需 external / company_industry 的 verified inference fact（fact_type=inference 且带类型化血缘）。
            ok = any(sc in ("external", "company_industry") and g != "D" and ft == "inference"
                     and f.inference_lineage is not None
                     for (sc, g, _d), ft, f in zip(triples, fact_types, facts))
            return build(rule, rule_version, ok, 0)
        # industry_background：external A/B/C（权威门已排除 D）。
        ok = any(g in ("A", "B", "C") for _sc, g, _d in triples)
        return build(rule, rule_version, ok, 0)

    if source_policy is not None and snap.topic_id in source_policy.key_industry_topics:
        ab = any(g in ("A", "B") for _sc, g, _d in triples)
        c_domains = {d for _sc, g, d in triples if g == "C" and d}
        independent_c_count = len(c_domains)
        threshold_met = ab or independent_c_count >= 2
        return build(source_policy.key_conclusion_rule, source_policy.key_conclusion_rule_version,
                     threshold_met, independent_c_count)

    return None


@dataclass(frozen=True)
class SufficiencyAssessment:
    """sufficiency-gate 记录（绑定 aspect/conclusion + 实际事实/来源 + 规则版本）。"""

    aspect_id: str | None
    conclusion_id: str | None
    supporting_fact_ids: tuple[str, ...]
    supporting_source_ids: tuple[str, ...]
    rule: str
    rule_version: str
    threshold_met: bool
    independent_c_count: int
    assessor_version: str

    def __post_init__(self) -> None:
        if self.aspect_id is None and self.conclusion_id is None:
            raise SchemaValidationError("SufficiencyAssessment 至少需要 aspect_id 或 conclusion_id")
        if not self.rule or not self.rule_version:
            raise SchemaValidationError("SufficiencyAssessment.rule/rule_version 必须非空")
        if self.independent_c_count < 0:
            raise SchemaValidationError("SufficiencyAssessment.independent_c_count 必须 ≥ 0")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "conclusion_id": self.conclusion_id,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "supporting_source_ids": list(self.supporting_source_ids),
            "rule": self.rule,
            "rule_version": self.rule_version,
            "threshold_met": self.threshold_met,
            "independent_c_count": self.independent_c_count,
            "assessor_version": self.assessor_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SufficiencyAssessment":
        d = _reject_unknown(d, {"aspect_id", "conclusion_id", "supporting_fact_ids",
                                "supporting_source_ids", "rule", "rule_version", "threshold_met",
                                "independent_c_count", "assessor_version"}, "SufficiencyAssessment")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SufficiencyAssessment", allow_none=True),
            conclusion_id=_get_str(d, "conclusion_id", "SufficiencyAssessment", allow_none=True),
            supporting_fact_ids=_get_str_tuple(d, "supporting_fact_ids", "SufficiencyAssessment"),
            supporting_source_ids=_get_str_tuple(d, "supporting_source_ids", "SufficiencyAssessment"),
            rule=_get_str(d, "rule", "SufficiencyAssessment"),
            rule_version=_get_str(d, "rule_version", "SufficiencyAssessment"),
            threshold_met=_get_bool(d, "threshold_met", "SufficiencyAssessment"),
            independent_c_count=_get_int(d, "independent_c_count", "SufficiencyAssessment"),
            assessor_version=_get_str(d, "assessor_version", "SufficiencyAssessment",
                                      allow_empty=True) or "",
        )


@dataclass(frozen=True)
class AspectResearchResult:
    """topic_harness aspect 的独立状态（每 aspect 一条）。"""

    aspect_id: str
    question_ids: tuple[str, ...]
    requirement_snapshot: TopicAspectRequirementSnapshot
    status: str
    supported_fact_ids: tuple[str, ...]
    material_ids: tuple[str, ...]
    attempted_need_ids: tuple[str, ...]
    unresolved_ids: tuple[str, ...]
    not_found_audit_id: str | None = None
    authority_assessment: AuthorityAssessment | None = None
    sufficiency_assessment: SufficiencyAssessment | None = None
    support_eligibility: SupportEligibilityAssessment | None = None
    set_completeness: SetCompletenessAssessment | None = None

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("AspectResearchResult.aspect_id 必须非空")
        _get_enum(self.status, ASPECT_RESULT_STATUSES, "AspectResearchResult", "status")
        # 冻结投影身份：aspect 结果必须绑定同名 aspect 的冻结投影。
        if self.requirement_snapshot.aspect_id != self.aspect_id:
            raise SchemaValidationError(
                f"AspectResearchResult.aspect_id={self.aspect_id!r} 与 "
                f"requirement_snapshot.aspect_id={self.requirement_snapshot.aspect_id!r} 不一致")
        if self.status == "not_found" and not self.not_found_audit_id:
            raise SchemaValidationError("AspectResearchResult.status=not_found 必须绑定 not_found_audit_id")
        if self.support_eligibility is not None and self.support_eligibility.aspect_id != self.aspect_id:
            raise SchemaValidationError(
                f"AspectResearchResult.support_eligibility.aspect_id={self.support_eligibility.aspect_id!r} "
                f"与 aspect_id={self.aspect_id!r} 不一致")
        if self.set_completeness is not None and self.set_completeness.aspect_id != self.aspect_id:
            raise SchemaValidationError(
                f"AspectResearchResult.set_completeness.aspect_id={self.set_completeness.aspect_id!r} "
                f"与 aspect_id={self.aspect_id!r} 不一致")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "question_ids": list(self.question_ids),
            "requirement_snapshot": self.requirement_snapshot.to_dict(),
            "status": self.status,
            "supported_fact_ids": list(self.supported_fact_ids),
            "material_ids": list(self.material_ids),
            "attempted_need_ids": list(self.attempted_need_ids),
            "unresolved_ids": list(self.unresolved_ids),
            "not_found_audit_id": self.not_found_audit_id,
            "authority_assessment": self.authority_assessment.to_dict()
                if self.authority_assessment is not None else None,
            "sufficiency_assessment": self.sufficiency_assessment.to_dict()
                if self.sufficiency_assessment is not None else None,
            "support_eligibility": self.support_eligibility.to_dict()
                if self.support_eligibility is not None else None,
            "set_completeness": self.set_completeness.to_dict()
                if self.set_completeness is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AspectResearchResult":
        d = _reject_unknown(d, {"aspect_id", "question_ids", "requirement_snapshot", "status",
                                "supported_fact_ids", "material_ids", "attempted_need_ids",
                                "unresolved_ids", "not_found_audit_id", "authority_assessment",
                                "sufficiency_assessment", "support_eligibility",
                                "set_completeness"}, "AspectResearchResult")
        aa = d.get("authority_assessment")
        sa = d.get("sufficiency_assessment")
        se = d.get("support_eligibility")
        sc = d.get("set_completeness")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "AspectResearchResult"),
            question_ids=_get_str_tuple(d, "question_ids", "AspectResearchResult"),
            requirement_snapshot=TopicAspectRequirementSnapshot.from_dict(_as_dict(
                d.get("requirement_snapshot"), "AspectResearchResult", "requirement_snapshot")),
            status=_get_str(d, "status", "AspectResearchResult"),
            supported_fact_ids=_get_str_tuple(d, "supported_fact_ids", "AspectResearchResult"),
            material_ids=_get_str_tuple(d, "material_ids", "AspectResearchResult"),
            attempted_need_ids=_get_str_tuple(d, "attempted_need_ids", "AspectResearchResult"),
            unresolved_ids=_get_str_tuple(d, "unresolved_ids", "AspectResearchResult"),
            not_found_audit_id=_get_str(d, "not_found_audit_id", "AspectResearchResult", allow_none=True),
            authority_assessment=authority_from_dict(aa) if aa is not None else None,
            sufficiency_assessment=SufficiencyAssessment.from_dict(sa) if sa is not None else None,
            support_eligibility=SupportEligibilityAssessment.from_dict(se) if se is not None else None,
            set_completeness=SetCompletenessAssessment.from_dict(sc) if sc is not None else None,
        )


@dataclass(frozen=True)
class AtomicOutcomeEligibility:
    """adapt_outcome_completion 输出（原子资格，不产出 Pack 状态）。"""

    eligible: bool
    reason_code: str
    outcome_ref: str

    def to_dict(self) -> dict:
        return {"eligible": self.eligible, "reason_code": self.reason_code, "outcome_ref": self.outcome_ref}

    @classmethod
    def from_dict(cls, d: Any) -> "AtomicOutcomeEligibility":
        d = _reject_unknown(d, {"eligible", "reason_code", "outcome_ref"}, "AtomicOutcomeEligibility")
        return cls(
            eligible=_get_bool(d, "eligible", "AtomicOutcomeEligibility"),
            reason_code=_get_str(d, "reason_code", "AtomicOutcomeEligibility"),
            outcome_ref=_get_str(d, "outcome_ref", "AtomicOutcomeEligibility"),
        )


# ---------------------------------------------------------------------------
# 身份 / 输入 requirement
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackIdentity:
    """current 指针业务键 = (task_id, company_id, report_as_of, contract_fingerprint,
    source_policy_version, section_id, topic_id)。"""

    task_id: str
    company_id: str
    report_as_of: str | None
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str

    def key(self) -> tuple:
        return (self.task_id, self.company_id, self.report_as_of or "", self.contract_fingerprint,
                self.source_policy_version, self.section_id, self.topic_id)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "company_id": self.company_id, "report_as_of": self.report_as_of,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "section_id": self.section_id, "topic_id": self.topic_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "PackIdentity":
        d = _reject_unknown(d, {"task_id", "company_id", "report_as_of", "contract_fingerprint",
                                "source_policy_version", "section_id", "topic_id"}, "PackIdentity")
        return cls(
            task_id=_get_str(d, "task_id", "PackIdentity"),
            company_id=_get_str(d, "company_id", "PackIdentity"),
            report_as_of=_get_str(d, "report_as_of", "PackIdentity", allow_none=True),
            contract_fingerprint=_get_str(d, "contract_fingerprint", "PackIdentity"),
            source_policy_version=_get_str(d, "source_policy_version", "PackIdentity"),
            section_id=_get_str(d, "section_id", "PackIdentity"),
            topic_id=_get_str(d, "topic_id", "PackIdentity"),
        )


def validate_dependency_versions(d: Any) -> dict[str, str]:
    """校验并返回排序规范形。

    **精确等集**语义（v5 / §5.2.1）：缺键 / 未知键 / 空值 / 空 dict 一律 fail-closed。
    不再接受「合法子集」——历史缺键 dict 不得被静默升级为 current。
    规范形只做确定性排序，不新增、不删除、不替换任何键值（故不改变依赖指纹）。
    """
    if not isinstance(d, dict):
        raise SchemaValidationError(f"dependency_versions 必须为 dict，得到 {type(d).__name__}")
    if not d:
        raise SchemaValidationError(
            f"dependency_versions 不得为空（current 必须精确给出 {len(DEPENDENCY_VERSION_KEYS)} 键）")
    out: dict[str, str] = {}
    for k, v in d.items():
        if k not in DEPENDENCY_VERSION_KEYS:
            raise SchemaValidationError(f"dependency_versions 未知键 {k!r}（允许 {DEPENDENCY_VERSION_KEYS}）")
        if not isinstance(v, str) or v == "":
            raise SchemaValidationError(f"dependency_versions[{k}] 必须为非空字符串")
        out[k] = v
    missing = [k for k in DEPENDENCY_VERSION_KEYS if k not in out]
    if missing:
        raise SchemaValidationError(
            f"dependency_versions 缺键 {missing}（必须与 DEPENDENCY_VERSION_KEYS 精确等集，"
            f"共 {len(DEPENDENCY_VERSION_KEYS)} 键；缺键不得自动补全）")
    return dict(sorted(out.items()))


def _named_version_combination(*parts: tuple[str, str]) -> str:
    """多轴版本 → 确定性**具名**规范形（不是某个 dict 的偶然字符串 repr）。

    例：``_named_version_combination(("schema", "als-3"), ("aligner", "al-3"))``
    → ``"schema=als-3;aligner=al-3"``。轴序由调用点固定，故对同一组版本恒等。
    """
    return ";".join(f"{name}={value}" for name, value in parts)


def build_current_dependency_versions(*, contract_version: str,
                                      source_policy_version: str) -> dict[str, str]:
    """构造 current 依赖版本 dict 的**唯一公共入口**。

    返回恰好 ``DEPENDENCY_VERSION_KEYS``（18 键）的全新 dict，每次调用新对象；
    内部再经 ``validate_dependency_versions`` 复核（构造即自证），调用方不得覆盖任何键、
    不得各自手写 18 键、不得追加 legacy 自动补键。

    ``contract_version`` / ``source_policy_version`` 是仅有的两个真正随运行输入变化的轴；
    其余 16 轴一律取自权威常量：结构版本来自 ``document_structure.versions``（唯一允许持有
    结构版本字面量的模块），三项 M930-2 能力版本与三项 M930-3 资格版本取自本模块的具名常量。
    """
    if not isinstance(contract_version, str) or contract_version == "":
        raise SchemaValidationError("build_current_dependency_versions: contract_version 必须为非空字符串")
    if not isinstance(source_policy_version, str) or source_policy_version == "":
        raise SchemaValidationError("build_current_dependency_versions: source_policy_version 必须为非空字符串")
    out = {
        "contract": contract_version,
        "source_policy": source_policy_version,
        "topic_schema": TOPIC_PACK_SCHEMA_VERSION,
        "assessor": SET_COMPLETENESS_ASSESSOR_VERSION,
        "validator": SET_COMPLETENESS_VERIFIER_VERSION,
        "set_enumerator": SET_ENUMERATION_VERIFIER_VERSION,
        "topic_runtime": TOPIC_RUNTIME_VERSION,
        "budget_policy": TOPIC_BUDGET_POLICY_VERSION,
        "navigation_profile": _named_version_combination(
            ("schema", DSV.PROFILE_SCHEMA_VERSION), ("rule", DSV.PROFILE_RULE_VERSION)),
        "synopsis": _named_version_combination(
            ("schema", DSV.SYNOPSIS_SCHEMA_VERSION), ("version", DSV.SYNOPSIS_VERSION)),
        "layout": DSV.LAYOUT_SCHEMA_VERSION,
        "alignment": _named_version_combination(
            ("schema", DSV.ALIGN_SCHEMA_VERSION), ("aligner", DSV.ALIGNER_VERSION),
            ("partition_validator", DSV.ALIGNMENT_PARTITION_VALIDATOR_VERSION)),
        "outline": DSV.OUTLINE_SCHEMA_VERSION,
        "span": DSV.SPAN_SCHEMA_VERSION,
        "material_resolver": TREE_MATERIAL_RESOLVER_VERSION,
        "material_disposition": MATERIAL_DISPOSITION_VERSION,
        "fact_qualification": FACT_QUALIFICATION_VERSION,
        "external_fact": EXTERNAL_FACT_VERSION,
    }
    return validate_dependency_versions(out)


def compute_dependency_fingerprint(contract_fingerprint: str, source_policy_version: str,
                                   dependency_versions: dict[str, str]) -> str:
    dv = validate_dependency_versions(dependency_versions)
    return sha256_canonical({
        "contract_fingerprint": contract_fingerprint,
        "source_policy_version": source_policy_version,
        "dependency_versions": dv,
    })


@dataclass(frozen=True)
class TopicResearchRequirement:
    """Pack 的身份/输入上下文（可校验字段；R3 调度器消费）。"""

    task_id: str
    company_id: str
    report_as_of: str | None
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    aspects: tuple[TopicAspectRequirementSnapshot, ...]
    allowed_capabilities: tuple[str, ...]
    dependency_versions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.topic_id or not self.section_id:
            raise SchemaValidationError("TopicResearchRequirement.topic_id/section_id 必须非空")
        if not self.contract_fingerprint:
            raise SchemaValidationError("TopicResearchRequirement.contract_fingerprint 必须非空")
        # aspect 集合：每条冻结投影 topic_id 必须与本 requirement 一致，且 aspect_id 唯一。
        seen: set[str] = set()
        for a in self.aspects:
            if a.topic_id != self.topic_id:
                raise SchemaValidationError(
                    f"aspect {a.aspect_id!r} topic_id={a.topic_id!r} 与 requirement topic_id={self.topic_id!r} 不一致")
            if a.aspect_id in seen:
                raise SchemaValidationError(f"requirement 含重复 aspect_id: {a.aspect_id!r}")
            seen.add(a.aspect_id)
        validate_dependency_versions(self.dependency_versions)

    def aspect_ids(self) -> tuple[str, ...]:
        return tuple(a.aspect_id for a in self.aspects)

    def dependency_fingerprint(self) -> str:
        return compute_dependency_fingerprint(self.contract_fingerprint, self.source_policy_version,
                                             self.dependency_versions)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "company_id": self.company_id, "report_as_of": self.report_as_of,
            "contract_version": self.contract_version, "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version, "section_id": self.section_id,
            "topic_id": self.topic_id, "question_ids": list(self.question_ids),
            "aspects": [a.to_dict() for a in self.aspects],
            "allowed_capabilities": list(self.allowed_capabilities),
            "dependency_versions": dict(self.dependency_versions),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicResearchRequirement":
        d = _reject_unknown(d, {"task_id", "company_id", "report_as_of", "contract_version",
                                "contract_fingerprint", "source_policy_version", "section_id",
                                "topic_id", "question_ids", "aspects", "allowed_capabilities",
                                "dependency_versions"}, "TopicResearchRequirement")
        return cls(
            task_id=_get_str(d, "task_id", "TopicResearchRequirement"),
            company_id=_get_str(d, "company_id", "TopicResearchRequirement"),
            report_as_of=_get_str(d, "report_as_of", "TopicResearchRequirement", allow_none=True),
            contract_version=_get_str(d, "contract_version", "TopicResearchRequirement"),
            contract_fingerprint=_get_str(d, "contract_fingerprint", "TopicResearchRequirement"),
            source_policy_version=_get_str(d, "source_policy_version", "TopicResearchRequirement"),
            section_id=_get_str(d, "section_id", "TopicResearchRequirement"),
            topic_id=_get_str(d, "topic_id", "TopicResearchRequirement"),
            question_ids=_get_str_tuple(d, "question_ids", "TopicResearchRequirement"),
            aspects=tuple(TopicAspectRequirementSnapshot.from_dict(x) for x in _as_list(
                d.get("aspects"), "TopicResearchRequirement", "aspects")),
            allowed_capabilities=_get_str_tuple(d, "allowed_capabilities", "TopicResearchRequirement"),
            dependency_versions=validate_dependency_versions(d.get("dependency_versions", {})),
        )


# ---------------------------------------------------------------------------
# TopicResearchPack（P3 正式交付物）
# ---------------------------------------------------------------------------

def _require_unique(values: list[str], label: str, where: str) -> None:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise SchemaValidationError(f"{where}: {label} 重复: {v!r}")
        seen.add(v)


def verify_material_disposition_exact_set(
        materials: tuple["ResearchMaterial", ...],
        dispositions: tuple["ResearchMaterialDisposition", ...],
        *, where: str = "Pack") -> None:
    """门 1：**每个 material 恰一条** ``ResearchMaterialDisposition``（不多、不少、不孤儿）。"""
    mat_ids = [m.material_id for m in materials]
    _require_unique(mat_ids, "material_id", where)
    _require_unique([d.disposition_id for d in dispositions],
                    "ResearchMaterialDisposition.disposition_id", where)
    mat_set = set(mat_ids)
    by_material: dict[str, list[ResearchMaterialDisposition]] = {}
    for d in dispositions:
        by_material.setdefault(d.material_id, []).append(d)
    for mid in by_material:
        if mid not in mat_set:
            raise SchemaValidationError(
                f"{where}: ResearchMaterialDisposition 引用了不存在的 material {mid!r}")
    for mid in mat_ids:
        got = by_material.get(mid, [])
        if len(got) != 1:
            raise SchemaValidationError(
                f"{where}: material {mid!r} 必须恰有一条 ResearchMaterialDisposition，"
                f"得到 {len(got)} 条")


def verify_candidate_decision_exact_set(
        candidates: tuple["FactCandidate", ...],
        decisions: tuple["FactQualificationDecision", ...],
        *, where: str = "Pack") -> None:
    """门 2：**每个 candidate 恰一条** ``FactQualificationDecision``，且 revision/kind 逐条一致。"""
    _require_unique([c.candidate_id for c in candidates], "FactCandidate.candidate_id", where)
    _require_unique([c.candidate_revision for c in candidates],
                    "FactCandidate.candidate_revision", where)
    _require_unique([d.decision_id for d in decisions],
                    "FactQualificationDecision.decision_id", where)
    cand_by_id = {c.candidate_id: c for c in candidates}
    by_candidate: dict[str, list[FactQualificationDecision]] = {}
    for d in decisions:
        by_candidate.setdefault(d.candidate_id, []).append(d)
    for cid in by_candidate:
        if cid not in cand_by_id:
            raise SchemaValidationError(
                f"{where}: FactQualificationDecision 引用了不存在的 candidate {cid!r}")
    for c in candidates:
        got = by_candidate.get(c.candidate_id, [])
        if len(got) != 1:
            raise SchemaValidationError(
                f"{where}: candidate {c.candidate_id!r} 必须恰有一条资格决定，得到 {len(got)} 条")
        dec = got[0]
        if dec.candidate_revision != c.candidate_revision:
            raise SchemaValidationError(
                f"{where}: candidate {c.candidate_id!r} 的资格决定 revision "
                f"{dec.candidate_revision!r} 与候选 {c.candidate_revision!r} 不一致")
        if dec.candidate_source_kind != c.candidate_source_kind:
            raise SchemaValidationError(
                f"{where}: candidate {c.candidate_id!r} 的资格决定 candidate_source_kind "
                f"{dec.candidate_source_kind!r} 与候选 {c.candidate_source_kind!r} 不一致")


def verify_qualified_result_exact_set(
        candidates: tuple["FactCandidate", ...],
        decisions: tuple["FactQualificationDecision", ...],
        facts: tuple["SupportedFact", ...],
        external_facts: tuple["ExternalFact", ...],
        *, where: str = "Pack") -> None:
    """门 3+4：eligible 决定按 kind 恰一条 qualified result；rejected 决定零条；
    每条 qualified result 反解到恰一个 matching candidate + eligible 决定。"""
    _require_unique([f.fact_id for f in facts], "SupportedFact.fact_id", where)
    _require_unique([f.external_fact_id for f in external_facts], "ExternalFact.external_fact_id",
                    where)
    cand_by_id = {c.candidate_id: c for c in candidates}
    dec_by_id = {d.decision_id: d for d in decisions}
    facts_by_cand: dict[str, list[SupportedFact]] = {}
    for f in facts:
        facts_by_cand.setdefault(f.candidate_id, []).append(f)
    ext_by_cand: dict[str, list[ExternalFact]] = {}
    for f in external_facts:
        ext_by_cand.setdefault(f.candidate_id, []).append(f)
    for cid in list(facts_by_cand) + list(ext_by_cand):
        if cid not in cand_by_id:
            raise SchemaValidationError(
                f"{where}: qualified result 引用了不存在的 candidate {cid!r}")
    for f in facts:
        cand = cand_by_id[f.candidate_id]
        if cand.candidate_source_kind != "topic_material":
            raise SchemaValidationError(
                f"{where}: SupportedFact {f.fact_id!r} 不得由 external_source 候选产生")
        if f.candidate_revision != cand.candidate_revision:
            raise SchemaValidationError(
                f"{where}: SupportedFact {f.fact_id!r} 的 candidate_revision 与候选不一致")
    for f in external_facts:
        cand = cand_by_id[f.candidate_id]
        if cand.candidate_source_kind != "external_source":
            raise SchemaValidationError(
                f"{where}: ExternalFact {f.external_fact_id!r} 不得由 topic_material 候选产生")
        if f.candidate_revision != cand.candidate_revision:
            raise SchemaValidationError(
                f"{where}: ExternalFact {f.external_fact_id!r} 的 candidate_revision 与候选不一致")
    for dec in decisions:
        topic_results = facts_by_cand.get(dec.candidate_id, [])
        ext_results = ext_by_cand.get(dec.candidate_id, [])
        if dec.verdict == "rejected":
            if topic_results or ext_results:
                raise SchemaValidationError(
                    f"{where}: rejected 决定 {dec.decision_id!r} 不得产生任何 qualified result")
            continue
        if dec.candidate_source_kind == "topic_material":
            if len(topic_results) != 1 or ext_results:
                raise SchemaValidationError(
                    f"{where}: eligible topic_material 决定 {dec.decision_id!r} 必须恰有一条 "
                    f"SupportedFact 且零条 ExternalFact（得到 {len(topic_results)}/"
                    f"{len(ext_results)}）")
            if topic_results[0].qualification_decision_id != dec.decision_id:
                raise SchemaValidationError(
                    f"{where}: SupportedFact {topic_results[0].fact_id!r} 未回指其 eligible 决定")
        else:
            if len(ext_results) != 1 or topic_results:
                raise SchemaValidationError(
                    f"{where}: eligible external_source 决定 {dec.decision_id!r} 必须恰有一条 "
                    f"ExternalFact 且零条 SupportedFact（得到 {len(ext_results)}/"
                    f"{len(topic_results)}）")
            if ext_results[0].qualification_decision_id != dec.decision_id:
                raise SchemaValidationError(
                    f"{where}: ExternalFact {ext_results[0].external_fact_id!r} "
                    f"未回指其 eligible 决定")
    # 反向：每条 qualified result 的资格决定必须存在、eligible、且 matching。
    for f in facts:
        dec = dec_by_id.get(f.qualification_decision_id)
        if dec is None:
            raise SchemaValidationError(
                f"{where}: SupportedFact {f.fact_id!r} 的资格决定不存在")
        if dec.verdict != "eligible" or dec.candidate_id != f.candidate_id:
            raise SchemaValidationError(
                f"{where}: SupportedFact {f.fact_id!r} 必须反解到同一 candidate 的 eligible 决定")
    for f in external_facts:
        dec = dec_by_id.get(f.qualification_decision_id)
        if dec is None:
            raise SchemaValidationError(
                f"{where}: ExternalFact {f.external_fact_id!r} 的资格决定不存在")
        if dec.verdict != "eligible" or dec.candidate_id != f.candidate_id:
            raise SchemaValidationError(
                f"{where}: ExternalFact {f.external_fact_id!r} 必须反解到同一 candidate 的 eligible 决定")


def verify_pack_successor_sets(pack: "TopicResearchPack", *, where: str = "TopicResearchPack") -> None:
    """四道正式门（§16.8.1）：不得靠含混默认值绕过。"""
    verify_material_disposition_exact_set(pack.materials, pack.material_dispositions, where=where)
    verify_candidate_decision_exact_set(pack.fact_candidates, pack.fact_qualification_decisions,
                                        where=where)
    verify_qualified_result_exact_set(pack.fact_candidates, pack.fact_qualification_decisions,
                                      pack.facts, pack.external_facts, where=where)


#: Pack 的跨源轴状态。`not_declared` **不是**「无需责任」——它是「本 Pack 没有逐来源责任
#: 记录」，因此也**不得**携带任何逐来源完整性证明。这个二分必须显式落库，读回时不能靠猜。
SOURCE_AXIS_STATES = ("declared", "not_declared")


def source_axis_state(pack: "TopicResearchPack") -> str:
    """Pack 的跨源轴状态：有责任记录 ⇒ `declared`；否则 `not_declared`。"""
    return "declared" if pack.aspect_source_responsibility else "not_declared"


def verify_pack_source_set_ledger(pack: "TopicResearchPack",
                                  *, where: str = "TopicResearchPack") -> None:
    """跨源轴双向穷尽核验（Pack v7）。

    **为什么双向**：只查「每条责任都有结果」会放过**凭空多出来的结果行**——一行没有责任依据的
    结果记录可以事后声明「这条 aspect 在这份文档里找到了材料」，而没有任何检索前的责任说它
    该被查。反过来只查「每条结果都有责任」会放过**被静默丢弃的必查来源**——那正是「注册了三份
    文档、只读了一份」却仍然报 complete 的形态。两个方向都必须关掉。

    臂的逐条约束（§0.3.3）也在这里，因为它是**臂**的定义，不是调用方的礼貌：

    - 臂 A（`material_found`）：必须有材料 id，且每个 id 属于本 Pack 的 `materials`；不得带
      检索失败记录（找到了就不是"没查成"）。
    - 臂 B（`retrieval_unqualified` / 查到但不够格）与臂 C2（查了、无 | 失败）：必须带**恰一条**
      `SourceSearchOutcomeRecord`——「查过」必须有一次真实调用的痕迹，不能只写一个结论。
    - 臂 C1（`not_required`）：必须引用 `NOT_REQUIRED_BASIS_RULE_IDS` 内的 typed 依据；且
      对应的责任边必须真的是 `retrieval_required=false`。
    """
    keys = pack.source_set.keys()
    key_set = {k.document_id for k in keys}
    aspects = [r.aspect_id for r in pack.aspect_results]
    aspect_set = set(aspects)

    # -- 责任记录：域、唯一性、逐条自洽 --------------------------------------
    resp_seen: set[tuple[str, str]] = set()
    resp_by_key: dict[tuple[str, str], AspectSourceResponsibility] = {}
    for r in pack.aspect_source_responsibility:
        if r.aspect_id not in aspect_set:
            raise SchemaValidationError(
                f"{where}: 责任记录 aspect_id={r.aspect_id!r} 不是本 Pack 的 aspect")
        if r.source_document_key.document_id not in key_set:
            raise SchemaValidationError(
                f"{where}: 责任记录 source_document_key="
                f"{r.source_document_key.document_id!r} 不在 source_set 内")
        pair = (r.aspect_id, r.source_document_key.document_id)
        if pair in resp_seen:
            raise SchemaValidationError(f"{where}: 重复的 (aspect, 来源) 责任 {pair!r}")
        resp_seen.add(pair)
        resp_by_key[pair] = r

    # -- 结果记录：域、唯一性 -------------------------------------------------
    out_seen: set[tuple[str, str]] = set()
    for o in pack.source_aspect_outcomes:
        if o.aspect_id not in aspect_set:
            raise SchemaValidationError(
                f"{where}: 结果记录 aspect_id={o.aspect_id!r} 不是本 Pack 的 aspect")
        if o.source_document_key.document_id not in key_set:
            raise SchemaValidationError(
                f"{where}: 结果记录 source_document_key="
                f"{o.source_document_key.document_id!r} 不在 source_set 内")
        pair = (o.aspect_id, o.source_document_key.document_id)
        if pair in out_seen:
            raise SchemaValidationError(f"{where}: 重复的 (aspect, 来源) 结果 {pair!r}")
        out_seen.add(pair)
        if pair not in resp_by_key:
            raise SchemaValidationError(
                f"{where}: 结果记录 {pair!r} 没有对应的检索前责任——结果不得凭空出现")

    # -- 双向穷尽：每条责任都要有结果（未被静默丢弃）-------------------------
    missing = sorted(set(resp_by_key) - out_seen)
    if missing:
        raise SchemaValidationError(
            f"{where}: 以下 (aspect, 来源) 有责任记录但无结果记录（禁止静默丢弃来源）：{missing}")

    material_ids = {m.material_id for m in pack.materials}
    for o in pack.source_aspect_outcomes:
        pair = (o.aspect_id, o.source_document_key.document_id)
        resp = resp_by_key[pair]
        if o.responsibility_fingerprint != resp.fingerprint():
            raise SchemaValidationError(
                f"{where}: 结果 {pair!r} 的责任指纹与检索前责任不一致（禁止倒填责任）")
        if o.arm == "A":
            if not o.material_ids:
                raise SchemaValidationError(
                    f"{where}: 臂 A {pair!r} 必须给出至少一个 material_id")
            unknown = [m for m in o.material_ids if m not in material_ids]
            if unknown:
                raise SchemaValidationError(
                    f"{where}: 臂 A {pair!r} 引用了不属于本 Pack 的材料 {unknown!r}")
            if o.search_record is not None:
                raise SchemaValidationError(
                    f"{where}: 臂 A {pair!r} 不得携带检索结果记录（已找到 ≠ 未查成）")
        elif o.arm in ("B", "C2"):
            if o.material_ids:
                raise SchemaValidationError(
                    f"{where}: 臂 {o.arm} {pair!r} 不得给出 material_ids（该臂未产生可用材料）")
            if o.search_record is None:
                raise SchemaValidationError(
                    f"{where}: 臂 {o.arm} {pair!r} 必须携带一条 SourceSearchOutcomeRecord"
                    "（「查过」必须有一次真实调用的痕迹）")
        elif o.arm == "C1":
            if o.material_ids:
                raise SchemaValidationError(
                    f"{where}: 臂 C1 {pair!r} 不得给出 material_ids（该臂从未检索）")
            if o.search_record is not None:
                raise SchemaValidationError(
                    f"{where}: 臂 C1 {pair!r} 不得携带检索结果记录（该臂从未发出调用）")
            if not o.not_required_basis:
                raise SchemaValidationError(
                    f"{where}: 臂 C1 {pair!r} 必须引用 typed not_required 依据")
            for rule_id, detail in o.not_required_basis:
                if rule_id not in NOT_REQUIRED_BASIS_RULE_IDS:
                    raise SchemaValidationError(
                        f"{where}: 臂 C1 {pair!r} 的依据 {rule_id!r} 不在 "
                        f"{sorted(NOT_REQUIRED_BASIS_RULE_IDS)} 内")
                if not detail:
                    raise SchemaValidationError(
                        f"{where}: 臂 C1 {pair!r} 的依据 {rule_id!r} 必须带非空说明")
            if resp.retrieval_required:
                raise SchemaValidationError(
                    f"{where}: 臂 C1 {pair!r} 冒充「无需检索」，但责任记录说必须检索")
        else:
            raise SchemaValidationError(f"{where}: 未知臂 {o.arm!r}")

    # -- 跨源轴状态与逐来源完整性证明的双向对账 ------------------------------
    if not pack.aspect_source_responsibility:
        # `not_declared`：没有责任记录 ⇒ 没有证明域。逐来源证明一律不得存在。
        # （`boundary_proof` 兼容读视图在这一态下**允许**存在：单文档、未声明跨源轴的 Pack
        # 仍是一份合法的研究记录，它的边界闭合靠那一份无键证明表达。）
        for r in pack.aspect_results:
            sca = r.set_completeness
            if sca is not None and sca.source_set_proofs:
                raise SchemaValidationError(
                    f"{where}: 跨源轴为 not_declared 时不得携带逐来源完整性证明"
                    f"（aspect {r.aspect_id!r}）——没有责任记录就没有证明域")
        return

    # `declared`：证明域**恰好**是 `proof_required == "true"` 的那些 (aspect, 来源)。
    proof_domain: dict[str, set[str]] = {}
    for r in pack.aspect_source_responsibility:
        if r.proof_required == "true":
            proof_domain.setdefault(r.aspect_id, set()).add(
                r.source_document_key.document_id)
    for r in pack.aspect_results:
        sca = r.set_completeness
        if sca is None:
            continue
        expected = proof_domain.get(r.aspect_id, set())
        actual = {k.source_document_key.document_id for k in sca.source_set_proofs}
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise SchemaValidationError(
                f"{where}: aspect {r.aspect_id!r} 的逐来源完整性证明与责任域不一致"
                f"（缺 {missing!r} / 多 {extra!r}）——"
                "set_complete 的合取域恰是 proof_required=='true' 的那些来源，"
                "少了就是拿未证明的来源冒充已闭合，多了就是给没有证明域的来源造证明")
        # 单键时兼容读视图必须等于那一条；多键时已由类型层要求为 None。
        if len(sca.source_set_proofs) == 1 and sca.boundary_proof is None:
            raise SchemaValidationError(
                f"{where}: aspect {r.aspect_id!r} 有唯一一条逐来源证明时，"
                "boundary_proof 兼容读视图必须指向它（不得留空）")
        # 有 `undetermined` 边的 aspect **不得** set_complete：三值里的「无法确定」是
        # 一等结论，它意味着**没有**判定域。原因码与 `set_completeness_proof_unavailable`
        # 分开，读回时才能区分「证明拿不到」与「这套冻结契约根本表达不了跨期责任」。
        undetermined = {k for (a, k) in resp_by_key
                        if a == r.aspect_id
                        and resp_by_key[(a, k)].proof_required == "undetermined"}
        if undetermined:
            raise SchemaValidationError(
                f"{where}: aspect {r.aspect_id!r} 存在 undetermined 责任边 "
                f"{sorted(undetermined)!r}，不得 set_complete"
                "（原因码 cross_period_responsibility_not_expressible_in_frozen_contract）")


def verify_pack_source_comparison(pack: "TopicResearchPack",
                                  *, where: str = "TopicResearchPack") -> None:
    """冲突与审计的对象分工：正式冲突两侧必须都是**本 Pack 的合格 fact**。"""
    fact_ids = {f.fact_id for f in pack.facts}
    for c in pack.conflicts:
        for side in c.sides:
            if side.fact_id not in fact_ids:
                raise SchemaValidationError(
                    f"{where}: 冲突 {c.conflict_id!r} 的一侧 fact_id={side.fact_id!r} "
                    "不解析到本 Pack 的合格 fact")
    audit = pack.source_comparison_audit
    if audit is not None:
        for side in audit.sides:
            if side.fact_id is not None and side.fact_id not in fact_ids:
                raise SchemaValidationError(
                    f"{where}: 比较审计 {audit.comparison_audit_id!r} 的一侧 "
                    f"fact_id={side.fact_id!r} 不解析到本 Pack 的合格 fact")


@dataclass(frozen=True)
class TopicResearchPack:
    schema_version: str
    pack_id: str
    run_id: str
    task_id: str
    company_id: str
    report_as_of: str | None
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    aspect_results: tuple[AspectResearchResult, ...]
    materials: tuple[ResearchMaterial, ...]
    facts: tuple[SupportedFact, ...]
    # M930-3 successor 六族：**必须显式传入**（允许显式空 tuple，但不得靠默认值绕门）。
    material_dispositions: tuple[ResearchMaterialDisposition, ...]
    fact_candidates: tuple[FactCandidate, ...]
    fact_qualification_decisions: tuple[FactQualificationDecision, ...]
    external_facts: tuple[ExternalFact, ...]
    contract_gaps: tuple[ContractGap, ...]
    research_blocks: tuple[ResearchBlock, ...]
    outcome_refs: tuple[str, ...]
    external_funnel: ExternalFunnelSnapshot | None
    conflicts: tuple[ResearchConflict, ...]
    not_found_audits: tuple[NotFoundAudit, ...]
    unresolved: tuple[ResearchGap, ...]
    usage: TopicUsageSnapshot
    uncertain_calls: tuple[UncertainToolCallRecord, ...]
    process_status: PackProcessStatus
    coverage_status: PackCoverageStatus
    status_derivation: StatusDerivation
    dependency_fingerprint: str
    # -- Pack v7 跨源轴（§L0 / §L1.5 / §L4）-----------------------------------
    #
    # 为什么 `source_set` 是**必需**且**有序**：本轮研究实际消费的是哪几份文档、谁是本期基准，
    # 是「注册了三份 ≠ 读了三份」这条判据的落点。没有它就无从表达逐来源责任与逐来源结果，
    # 也无法在读回时区分「这份文档没被查」和「这份文档不存在」。单文档研究同样是**集合**。
    source_set: DocumentSourceSet
    # 逐 aspect × 逐来源**检索前**责任（`asr-1`）。与 `source_aspect_outcomes` 同域、双向穷尽。
    aspect_source_responsibility: tuple[AspectSourceResponsibility, ...] = ()
    # 逐 `(aspect, 来源)` 四臂结果（§0.3.3）。臂 A 必须有材料；B/C2 必须带一条
    # `SourceSearchOutcomeRecord`；C1 必须引用 `NOT_REQUIRED_BASIS_RULE_IDS` 里的 typed 依据。
    source_aspect_outcomes: tuple[SourceAspectOutcome, ...] = ()
    # 跨源比较审计（`sca-1`）。**不是** gap；无跨源观察时为 None。
    source_comparison_audit: SourceComparisonAudit | None = None

    def __post_init__(self) -> None:
        if self.schema_version != TOPIC_PACK_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"TopicResearchPack.schema_version 必须为 {TOPIC_PACK_SCHEMA_VERSION!r}，"
                f"得到 {self.schema_version!r}")
        if not self.topic_id:
            raise SchemaValidationError("TopicResearchPack.topic_id 必须非空")
        # aspect 结果恰好覆盖每条 aspect 一次（不缺失、不重复、不混入其他 topic aspect）。
        seen: set[str] = set()
        for r in self.aspect_results:
            if r.requirement_snapshot.topic_id != self.topic_id:
                raise SchemaValidationError(
                    f"aspect_result {r.aspect_id!r} topic_id={r.requirement_snapshot.topic_id!r} "
                    f"与 Pack topic_id={self.topic_id!r} 不一致")
            if r.aspect_id in seen:
                raise SchemaValidationError(f"Pack 含重复 aspect_result: {r.aspect_id!r}")
            seen.add(r.aspect_id)
        # M930-3 四道正式门（§16.8.1）：每个 material 恰一条 RMD；每个 candidate 恰一条
        # 资格决定；每个 eligible 决定按 candidate_source_kind 恰一条 qualified result
        # （topic_material → SupportedFact；external_source → ExternalFact），rejected 决定零条；
        # 每条 qualified result 单向回指并反向解析到恰一个 matching candidate + eligible 决定。
        verify_pack_successor_sets(self)
        # Pack v7 跨源轴：逐 (aspect, 来源) 责任与结果必须同域、双向穷尽、逐臂可核。
        verify_pack_source_set_ledger(self)
        # v7 冲突/审计的对象分工：正式冲突两侧都必须是本 Pack 的合格 fact。
        verify_pack_source_comparison(self)
        # gap/block 必须绑定 Contract basis（类型层已保证非空），且不得重复。
        _require_unique([g.gap_id for g in self.contract_gaps], "ContractGap.gap_id",
                        "TopicResearchPack")
        _require_unique([b.block_id for b in self.research_blocks], "ResearchBlock.block_id",
                        "TopicResearchPack")

    # -- 身份 / 指纹 --------------------------------------------------------

    def identity(self) -> PackIdentity:
        return PackIdentity(
            task_id=self.task_id, company_id=self.company_id, report_as_of=self.report_as_of,
            contract_fingerprint=self.contract_fingerprint,
            source_policy_version=self.source_policy_version, section_id=self.section_id,
            topic_id=self.topic_id,
        )

    def content_fingerprint(self) -> str:
        """内容身份规范形：除 run_id / pack_id / dependency_fingerprint 外的全部稳定字段。

        含 process/coverage/status_derivation/usage/uncertain_calls/outcome_refs，使同一
        pack_id 只能对应同一份不可变 Pack 内容（改任一字段即改 pack_id，杜绝静默复用）。
        """
        body = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "section_id": self.section_id,
            "topic_id": self.topic_id,
            "question_ids": self.question_ids,
            "aspect_results": [r.to_dict() for r in self.aspect_results],
            "materials": [m.to_dict() for m in self.materials],
            "facts": [f.to_dict() for f in self.facts],
            "material_dispositions": [d.to_dict() for d in self.material_dispositions],
            "fact_candidates": [c.to_dict() for c in self.fact_candidates],
            "fact_qualification_decisions": [d.to_dict() for d in self.fact_qualification_decisions],
            "external_facts": [f.to_dict() for f in self.external_facts],
            "contract_gaps": [g.to_dict() for g in self.contract_gaps],
            "research_blocks": [b.to_dict() for b in self.research_blocks],
            "outcome_refs": self.outcome_refs,
            # v7 跨源轴：源集身份、逐来源责任、逐来源结果、比较审计都进内容身份——
            # 换文档/换责任/换结果就必须换 Pack 身份，禁止「同一 pack_id 两种来源集」。
            "source_set": self.source_set.to_dict(),
            "source_axis_state": source_axis_state(self),
            "aspect_source_responsibility": [r.to_dict()
                                             for r in self.aspect_source_responsibility],
            "source_aspect_outcomes": [o.to_dict() for o in self.source_aspect_outcomes],
            "source_comparison_audit": (self.source_comparison_audit.to_dict()
                                        if self.source_comparison_audit is not None else None),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "not_found_audits": [n.to_dict() for n in self.not_found_audits],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "external_funnel": self.external_funnel.to_dict() if self.external_funnel else None,
            "usage": self.usage.to_dict(),
            "uncertain_calls": [u.to_dict() for u in self.uncertain_calls],
            "process_status": self.process_status.to_dict(),
            "coverage_status": self.coverage_status.to_dict(),
            "status_derivation": self.status_derivation.to_dict(),
        }
        return sha256_canonical(body)

    def compute_pack_id(self) -> str:
        """pack_id = content_fingerprint + dependency_fingerprint（确定性）。"""
        return f"{self.content_fingerprint()}_{self.dependency_fingerprint}"

    def verify_pack_id(self) -> None:
        expected = self.compute_pack_id()
        if self.pack_id and self.pack_id != expected:
            raise SchemaValidationError(
                f"TopicResearchPack.pack_id={self.pack_id!r} 与内容/依赖指纹不符（期望 {expected!r}）")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "section_id": self.section_id,
            "topic_id": self.topic_id,
            "question_ids": list(self.question_ids),
            "aspect_results": [r.to_dict() for r in self.aspect_results],
            "materials": [m.to_dict() for m in self.materials],
            "facts": [f.to_dict() for f in self.facts],
            "material_dispositions": [d.to_dict() for d in self.material_dispositions],
            "fact_candidates": [c.to_dict() for c in self.fact_candidates],
            "fact_qualification_decisions": [d.to_dict() for d in self.fact_qualification_decisions],
            "external_facts": [f.to_dict() for f in self.external_facts],
            "contract_gaps": [g.to_dict() for g in self.contract_gaps],
            "research_blocks": [b.to_dict() for b in self.research_blocks],
            "outcome_refs": list(self.outcome_refs),
            "external_funnel": self.external_funnel.to_dict() if self.external_funnel else None,
            "source_set": self.source_set.to_dict(),
            "source_axis_state": source_axis_state(self),
            "aspect_source_responsibility": [r.to_dict()
                                             for r in self.aspect_source_responsibility],
            "source_aspect_outcomes": [o.to_dict() for o in self.source_aspect_outcomes],
            "source_comparison_audit": (self.source_comparison_audit.to_dict()
                                        if self.source_comparison_audit is not None else None),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "not_found_audits": [n.to_dict() for n in self.not_found_audits],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "usage": self.usage.to_dict(),
            "uncertain_calls": [u.to_dict() for u in self.uncertain_calls],
            "process_status": self.process_status.to_dict(),
            "coverage_status": self.coverage_status.to_dict(),
            "status_derivation": self.status_derivation.to_dict(),
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicResearchPack":
        d = _reject_unknown(d, {
            "schema_version", "pack_id", "run_id", "task_id", "company_id", "report_as_of",
            "contract_version", "contract_fingerprint", "source_policy_version", "section_id",
            "topic_id", "question_ids", "aspect_results", "materials", "facts", "outcome_refs",
            "material_dispositions", "fact_candidates", "fact_qualification_decisions",
            "external_facts", "contract_gaps", "research_blocks",
            "external_funnel", "conflicts", "not_found_audits", "unresolved", "usage",
            "uncertain_calls", "process_status", "coverage_status", "status_derivation",
            "dependency_fingerprint",
            "source_set", "source_axis_state", "aspect_source_responsibility",
            "source_aspect_outcomes", "source_comparison_audit",
        }, "TopicResearchPack")
        ef = d.get("external_funnel")
        raw_audit = d.get("source_comparison_audit")
        raw_source_set = d.get("source_set")
        if raw_source_set is None:
            raise SchemaValidationError(
                "TopicResearchPack.source_set 必须存在——v7 Pack 不能没有来源集"
                "（否则无从区分「没查这份文档」与「这份文档不存在」）")
        return cls(
            schema_version=_get_str(d, "schema_version", "TopicResearchPack"),
            pack_id=_get_str(d, "pack_id", "TopicResearchPack", allow_empty=True) or "",
            run_id=_get_str(d, "run_id", "TopicResearchPack", allow_empty=True) or "",
            task_id=_get_str(d, "task_id", "TopicResearchPack"),
            company_id=_get_str(d, "company_id", "TopicResearchPack"),
            report_as_of=_get_str(d, "report_as_of", "TopicResearchPack", allow_none=True),
            contract_version=_get_str(d, "contract_version", "TopicResearchPack"),
            contract_fingerprint=_get_str(d, "contract_fingerprint", "TopicResearchPack"),
            source_policy_version=_get_str(d, "source_policy_version", "TopicResearchPack"),
            section_id=_get_str(d, "section_id", "TopicResearchPack"),
            topic_id=_get_str(d, "topic_id", "TopicResearchPack"),
            question_ids=_get_str_tuple(d, "question_ids", "TopicResearchPack"),
            aspect_results=tuple(AspectResearchResult.from_dict(x) for x in _as_list(
                d.get("aspect_results"), "TopicResearchPack", "aspect_results")),
            materials=tuple(ResearchMaterial.from_dict(x) for x in _as_list(
                d.get("materials"), "TopicResearchPack", "materials")),
            facts=tuple(SupportedFact.from_dict(x) for x in _as_list(
                d.get("facts"), "TopicResearchPack", "facts")),
            material_dispositions=tuple(ResearchMaterialDisposition.from_dict(x) for x in _as_list(
                d.get("material_dispositions"), "TopicResearchPack", "material_dispositions")),
            fact_candidates=tuple(FactCandidate.from_dict(x) for x in _as_list(
                d.get("fact_candidates"), "TopicResearchPack", "fact_candidates")),
            fact_qualification_decisions=tuple(
                FactQualificationDecision.from_dict(x) for x in _as_list(
                    d.get("fact_qualification_decisions"), "TopicResearchPack",
                    "fact_qualification_decisions")),
            external_facts=tuple(ExternalFact.from_dict(x) for x in _as_list(
                d.get("external_facts"), "TopicResearchPack", "external_facts")),
            contract_gaps=tuple(ContractGap.from_dict(x) for x in _as_list(
                d.get("contract_gaps"), "TopicResearchPack", "contract_gaps")),
            research_blocks=tuple(ResearchBlock.from_dict(x) for x in _as_list(
                d.get("research_blocks"), "TopicResearchPack", "research_blocks")),
            outcome_refs=_get_str_tuple(d, "outcome_refs", "TopicResearchPack"),
            external_funnel=ExternalFunnelSnapshot.from_dict(ef) if ef is not None else None,
            source_set=DocumentSourceSet.from_dict(raw_source_set),
            aspect_source_responsibility=tuple(
                AspectSourceResponsibility.from_dict(x) for x in _as_list(
                    d.get("aspect_source_responsibility"), "TopicResearchPack",
                    "aspect_source_responsibility")),
            source_aspect_outcomes=tuple(
                SourceAspectOutcome.from_dict(x) for x in _as_list(
                    d.get("source_aspect_outcomes"), "TopicResearchPack",
                    "source_aspect_outcomes")),
            source_comparison_audit=(
                SourceComparisonAudit.from_dict(raw_audit) if raw_audit is not None else None),
            conflicts=tuple(ResearchConflict.from_dict(x) for x in _as_list(
                d.get("conflicts"), "TopicResearchPack", "conflicts")),
            not_found_audits=tuple(NotFoundAudit.from_dict(x) for x in _as_list(
                d.get("not_found_audits"), "TopicResearchPack", "not_found_audits")),
            unresolved=tuple(ResearchGap.from_dict(x) for x in _as_list(
                d.get("unresolved"), "TopicResearchPack", "unresolved")),
            usage=TopicUsageSnapshot.from_dict(_as_dict(d.get("usage"), "TopicResearchPack", "usage")),
            uncertain_calls=tuple(UncertainToolCallRecord.from_dict(x) for x in _as_list(
                d.get("uncertain_calls"), "TopicResearchPack", "uncertain_calls")),
            process_status=PackProcessStatus.from_dict(_as_dict(
                d.get("process_status"), "TopicResearchPack", "process_status")),
            coverage_status=PackCoverageStatus.from_dict(_as_dict(
                d.get("coverage_status"), "TopicResearchPack", "coverage_status")),
            status_derivation=StatusDerivation.from_dict(_as_dict(
                d.get("status_derivation"), "TopicResearchPack", "status_derivation")),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "TopicResearchPack"),
        )


# ---------------------------------------------------------------------------
# v4 legacy audit view（§5.2.1 裁决 4 / §六：显式只读，不 current、不 commit、不可消费）
# ---------------------------------------------------------------------------

# legacy-only 类型名登记（诊断/审计用；这些类型不是 current 消费对象）。
LEGACY_AUDIT_ONLY_TYPE_NAMES = ("LegacyTopicPackAuditView",)


@dataclass(frozen=True)
class LegacyTopicPackAuditView:
    """v4/v5 ``TopicResearchPack`` 的**显式只读审计视图**——不冒充、不继承 current Pack。

    能力隔离（逐条对应 §5.2.1 裁决 4 与 §六）：

    - **不继承** ``TopicResearchPack``，也不实现其协议：``isinstance(view, TopicResearchPack)``
      恒为 False，故 runtime / Worker / Writer 的类型检查天然拒绝它；
    - **不进入 ``commit_pack()``**：``topic_store.commit_pack`` 有显式类型门，非
      ``TopicResearchPack`` 入参直接 TypeError（不存在经本类型写库/切 current 的路径）；
    - **不重算、不补键**：``dependency_fingerprint`` / ``content_fingerprint`` 均为落库原值，
      本视图不做任何重算；
    - **只经显式入口**：``topic_store.load_legacy_topic_pack_for_audit(pack_id)``，
      current reader（``get_pack`` / ``load_current_pack`` / ``list_*``）读取 v4/v5 仍一律
      ``schema_version_stale``。

    **如实边界**：v4/v5 的 ``dependency_versions`` 字典（v4 的 6 键、v5 的 15 键）**从未落库**——
    ``topic_pack`` 只存 ``dependency_fingerprint`` 字符串，子行快照也只带该字符串。因此本视图
    **不提供** ``dependency_versions``，也绝不据此反推或自动填充 v6 的 18 键。
    """

    legacy_schema_version: str
    pack_id: str
    run_id: str
    identity: PackIdentity
    contract_version: str
    dependency_fingerprint: str
    content_fingerprint: str
    created_at: str | None
    aspect_count: int
    material_count: int
    fact_count: int

    # legacy（v4/v5）依赖键字典未落库（见 docstring「如实边界」）；审计视图据此不得声称拥有它。
    DEPENDENCY_VERSIONS_PERSISTED = False

    def __post_init__(self) -> None:
        if self.legacy_schema_version not in LEGACY_TOPIC_PACK_SCHEMA_VERSIONS:
            raise SchemaValidationError(
                f"LegacyTopicPackAuditView 只承载 legacy（{LEGACY_TOPIC_PACK_SCHEMA_VERSIONS!r}）"
                f"历史行，得到 {self.legacy_schema_version!r}")
        for name, value in (("pack_id", self.pack_id),
                            ("dependency_fingerprint", self.dependency_fingerprint),
                            ("content_fingerprint", self.content_fingerprint)):
            if not isinstance(value, str) or value == "":
                raise SchemaValidationError(f"LegacyTopicPackAuditView.{name} 必须为非空字符串")
        for name, count in (("aspect_count", self.aspect_count),
                            ("material_count", self.material_count),
                            ("fact_count", self.fact_count)):
            if not isinstance(count, int) or count < 0:
                raise SchemaValidationError(f"LegacyTopicPackAuditView.{name} 必须为非负整数")

    def to_audit_dict(self) -> dict:
        """审计导出。**不是** Pack wire 形：键名与 ``TopicResearchPack.to_dict`` 显式区分，
        且不含任何可被 current reader 接受的字段组合。"""
        return {
            "audit_view": type(self).__name__,
            "legacy_schema_version": self.legacy_schema_version,
            "pack_id": self.pack_id,
            "run_id": self.run_id,
            "identity": self.identity.to_dict(),
            "contract_version": self.contract_version,
            "dependency_fingerprint": self.dependency_fingerprint,
            "content_fingerprint": self.content_fingerprint,
            "created_at": self.created_at,
            "aspect_count": self.aspect_count,
            "material_count": self.material_count,
            "fact_count": self.fact_count,
            "dependency_versions_persisted": self.DEPENDENCY_VERSIONS_PERSISTED,
        }


def finalize_pack(pack: TopicResearchPack) -> TopicResearchPack:
    """回填确定性 pack_id（内容身份 + 依赖指纹）。"""
    pack.verify_pack_id()
    return dataclasses.replace(pack, pack_id=pack.compute_pack_id())


def verify_pack_payloads(pack: TopicResearchPack, resolver: PayloadResolver) -> None:
    """校验 Pack 内全部 material 的 payload_ref 可解析（dangling/类型/版本/locator/hash → fail-closed）。

    由 commit/finalize 边界注入 resolver 调用；R1-B 不直接依赖 Evidence/Financial/External
    Store，离线测试使用 fake resolver。
    """
    for m in pack.materials:
        verify_material_payload_ref(m.payload_ref, resolver)


# ---------------------------------------------------------------------------
# 状态适配（架构约束 1：状态空间隔离，未知 fail-closed）
# ---------------------------------------------------------------------------

def outcome_to_ref(outcome: Any) -> str:
    """ResearchOutcome → 只读引用（确定性、不含 run_id）。"""
    qid = getattr(getattr(outcome, "state", None), "question_id", None)
    cs = getattr(outcome, "completion_status", None)
    if not qid or not cs:
        raise StateAdaptationError("outcome 缺 state.question_id / completion_status")
    if cs not in COMPLETION_STATUSES:
        raise StateAdaptationError(f"未知 completion_status: {cs!r}")
    return f"outcome:{qid}:{cs}"


def adapt_outcome_completion(outcome: Any) -> AtomicOutcomeEligibility:
    """单个原子 ResearchOutcome 是否可作 Pack 候选输入（原子资格，不产出 Pack 状态）。

    一个原子 ANSWER/COMPLETED 只结束当前 need，不结束整个 Topic。
    """
    cs = getattr(outcome, "completion_status", None)
    if cs not in COMPLETION_STATUSES:
        raise StateAdaptationError(f"未知 completion_status: {cs!r}")
    ref = outcome_to_ref(outcome)
    if cs == "COMPLETED":
        return AtomicOutcomeEligibility(True, "ATOMIC_COMPLETED", ref)
    if cs == "COMPLETED_WITH_GAPS":
        return AtomicOutcomeEligibility(True, "ATOMIC_COMPLETED_WITH_GAPS", ref)
    return AtomicOutcomeEligibility(False, f"ATOMIC_{cs}", ref)


def adapt_entailment_supported(verdict: str) -> bool:
    """EntailmentVerdict SUPPORTED → fact 可 adopted（其余 fail-closed）。"""
    if verdict not in ENTAILMENT_VERDICTS:
        raise StateAdaptationError(f"未知 EntailmentVerdict: {verdict!r}")
    return verdict == "SUPPORTED"


def adapt_entailment_reject(verdict: str) -> bool:
    """EntailmentVerdict PARTIAL / UNSUPPORTED → fact 不 adopted（候选区）。"""
    if verdict not in ENTAILMENT_VERDICTS:
        raise StateAdaptationError(f"未知 EntailmentVerdict: {verdict!r}")
    return verdict in ("PARTIAL", "UNSUPPORTED")


def _is_budget_stop(stop_reason: str) -> bool:
    return stop_reason.startswith(_BUDGET_STOP_PREFIXES)


def _is_block_stop(stop_reason: str) -> bool:
    return stop_reason in _BLOCK_STOP_CODES


def _is_fatal_stop(stop_reason: str) -> bool:
    return stop_reason in _FATAL_STOP_CODES


def derive_pack_status(required_aspect_ids: tuple[str, ...],
                       aspect_results: tuple[AspectResearchResult, ...],
                       stop_reason: str | None = None) -> tuple[PackProcessStatus, PackCoverageStatus, StatusDerivation]:
    """读取完整 required aspect 集合，确定性派生双轴状态（流程轴 ⊥ 覆盖轴）。

    - 校验：每个 required aspect 恰好一条结果，不缺失、不重复、不混入其他 aspect；
    - not_found 是 aspect 层证据结果，不直接作为 Pack 流程状态；
    - 流程已结束但含合格 not_found → process=finished + coverage=complete_with_gaps。
    """
    if len(set(required_aspect_ids)) != len(required_aspect_ids):
        raise StateAdaptationError("required_aspect_ids 含重复 aspect_id")
    by_id: dict[str, AspectResearchResult] = {}
    for r in aspect_results:
        if r.aspect_id in by_id:
            raise StateAdaptationError(f"aspect_result 重复: {r.aspect_id!r}")
        by_id[r.aspect_id] = r
    for aid in required_aspect_ids:
        if aid not in by_id:
            raise StateAdaptationError(f"缺失 required aspect 结果: {aid!r}")
    foreign = set(by_id) - set(required_aspect_ids)
    if foreign:
        raise StateAdaptationError(f"混入其他 aspect 结果: {sorted(foreign)!r}")

    statuses = {aid: by_id[aid].status for aid in required_aspect_ids}
    covered = tuple(a for a in required_aspect_ids if statuses[a] == "covered")
    not_applicable = tuple(a for a in required_aspect_ids if statuses[a] == "not_applicable")
    not_found = tuple(a for a in required_aspect_ids if statuses[a] == "not_found")
    gaps = tuple(a for a in required_aspect_ids if statuses[a] in ("partial", "not_found", "blocked"))
    terminal = {"covered", "not_applicable", "not_found"}

    all_terminal = all(statuses[a] in terminal for a in required_aspect_ids)

    # coverage 轴
    if all(statuses[a] in ("covered", "not_applicable") for a in required_aspect_ids):
        coverage_status = "complete"
    elif all_terminal:
        coverage_status = "complete_with_gaps"
    else:
        coverage_status = "insufficient"

    # process 轴
    if stop_reason is not None:
        if _is_budget_stop(stop_reason):
            process_status = "stopped_by_budget"
        elif _is_block_stop(stop_reason):
            process_status = "blocked"
        elif _is_fatal_stop(stop_reason):
            process_status = "failed"
        elif all_terminal:
            process_status = "finished"
        else:
            process_status = "blocked"
    else:
        if all_terminal:
            process_status = "finished"
        elif any(statuses[a] == "blocked" for a in required_aspect_ids):
            process_status = "blocked"
        elif len(covered) == 0:
            process_status = "pending"
        else:
            process_status = "running"

    per_aspect = tuple(AspectStatusEntry(a, statuses[a]) for a in required_aspect_ids)
    derivation_fingerprint = sha256_canonical({
        "required_aspect_ids": required_aspect_ids,
        "per_aspect": [e.to_dict() for e in per_aspect],
        "rule_version": STATUS_DERIVATION_RULE_VERSION,
    })
    process = PackProcessStatus(process_status, hard_stop_reason=stop_reason
                                if process_status in ("blocked", "stopped_by_budget", "failed")
                                else None)
    coverage = PackCoverageStatus(
        status=coverage_status, covered_aspect_ids=covered, gap_aspect_ids=gaps,
        not_applicable_aspect_ids=not_applicable)
    derivation = StatusDerivation(
        schema_version=TOPIC_PACK_SCHEMA_VERSION, rule_version=STATUS_DERIVATION_RULE_VERSION,
        derivation_fingerprint=derivation_fingerprint, per_aspect=per_aspect)
    return process, coverage, derivation

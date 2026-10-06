# Prompt 角色索引

> 本目录文件是运行时资产，不是给开发代理的指令。
> 当前迁移依据：`DESIGN_V2.md` v0.13、父任务书、树任务与 Demo 实施计划。现行链从 pre-gate `SectionDraft`（`ClaimCandidate`、`NarrativeDraftUnit`、proposals）开始，每个 subject revision 恰有一个 aggregate binding decision；只有 factual candidate 进入 entailment，context 不进入；accepted bindings 后才形成 Claim/final Narrative 与 post-gate Result。本目录提示词不得绕过该链或自行批准对象。
> 本索引只说明角色；在对应代码批次获批前不修改 Prompt 内容或调用行为。

## 角色定义

- `ACTIVE_V2_SPECIALIZED`：现行专用能力，可继续使用，但仍受正式接口与版本指纹约束。
- `ACTIVE_V2_ATOMIC_COMPAT`：现行单题/原子执行兼容 Prompt；P3R 需要迁移，不能直接作为 Topic/P4 输出。
- `ACTIVE_PHASE4_BASE_MIGRATION_REQUIRED`：Phase 4 基础 Prompt；P4R 必须按 Pack/WritingSpec 接口升级后才能用于内容关闭。
- `HISTORICAL_EVAL_ONLY`：只供历史评测或预览，不进入正式生产内容链。
- `LEGACY_CHAPTER_LEVEL_ONLY`：只供 legacy 章节级兼容链；M930 current 写作主链调用次数必须为 0，其职责已被 Claim 级 owner 取代。
- `EXPERIMENTAL_ONLY`：纵向实验资产，可迁移纯思路，不得启用第二套运行时。
- `V1_LEGACY_ONLY`：仅供 V1 兼容/历史回放，不得进入 V2 正式链。
- `SUPERSEDED`：已被现行设计取代，不得继续调用。

## 文件分类

| 文件 | 角色 | 当前约束 / 迁移动作 |
|---|---|---|
| `audit_opinion_extract.txt` | ACTIVE_V2_SPECIALIZED | 仅抽取审计意见/事务所；Evidence 引用与缺失规则保持 |
| `router_v2.txt` | ACTIVE_V2_ATOMIC_COMPAT | 仅规则 Router 低置信/冲突 fallback；R3 前评估加入 aspect、required source、freshness/time scope 输入 |
| `research_action_v1.txt` | ACTIVE_V2_ATOMIC_COMPAT | 只选择一个原子 need 的下一动作；不得决定 Topic 完成 |
| `research_answer_v1.txt` | ACTIVE_V2_ATOMIC_COMPAT | 1～3 句仅为历史原子答案形式；文件当前仍把 inference 引用写成“尽量”，这是已知兼容缺口。R3 必须升版 Prompt 或由确定性规则 fail-closed 保证正式 fact/calculation/inference 全部有引用；它不能限制 Pack 内容量或成为 P4 唯一输入 |
| `research_entailment_v1.txt` | ACTIVE_V2_ATOMIC_COMPAT | 继续用于原子 claim 支撑判断；Topic 级覆盖由 Harness Topic runtime 判定 |
| `section_evaluator.txt` | LEGACY_CHAPTER_LEVEL_ONLY | 仅 legacy 章节级兼容资产：M930 current Claim entailment 的**唯一** owner 是 `sections/claim_entailment_evaluator.py`（P9，消费新资产 `claim_entailment_evaluator_v1`），本文件不得同时实现 Claim entailment，也不得进入 current 写作主链 |
| `pack_section_writer_proposals_v1.txt` | ACTIVE_V2_SPECIALIZED | M930 current Writer 唯一入口的 proposal 资产（新增资产名，`pack_section_writer_v1.txt` 不被原位修改）；输出契约固定为 `ClaimCandidate[]` + `NarrativeDraftUnit[]` + `ProposedSupportRef[]`（含 `authority_kind` / `support_semantics` 声明）+ 可选 `FollowUpNeed[]`；不得输出 `SectionClaim`、accepted binding、final Narrative 或 `SectionResult` 字段，不检索、不联网、不写回 Pack |
| `claim_entailment_evaluator_v1.txt` | ACTIVE_V2_SPECIALIZED | 由 `sections/claim_entailment_evaluator.py`（P9）唯一消费；只收 factual candidate revision + 唯一通过的 aggregate `ClaimBindingDecision` + 相同 support-set digest，每 revision 输出一个 `ClaimEntailmentDecision`；context 不进入该门 |
| `section_financial.txt` | ACTIVE_PHASE4_BASE_MIGRATION_REQUIRED | 只输出 pre-gate candidate/narrative-draft/proposals；高风险财务事实只能走 FinancialFactPack 或 validated note fact 路径 A，不得自造 authority；期间语言必须明确 |
| `section_repair.txt` | ACTIVE_PHASE4_BASE_MIGRATION_REQUIRED | 仅按结构化 issue 重写 Draft；材料缺口回 Topic runtime；不得检索、改 Pack/既有决定或复用旧 decision |
| `research_preview_v1.txt` | HISTORICAL_EVAL_ONLY | 只消费 ResearchOutcome 的旧预览，禁止成为 P4R consumer |
| `topic_fact_validation_v1.txt` | EXPERIMENTAL_ONLY | 仅纵向实验；事实类别不覆盖完整 Contract，不得形成正式第二链 |
| `topic_chapter_writer_v1.txt` | EXPERIMENTAL_ONLY | 固定句数/段落仅实验；不得替代 versioned WritingSpec |
| `publication_editor.txt` | SUPERSEDED | 逐字保留 Claim 的旧发布投影无法形成多 Claim 叙述；正式 P4R 不调用 |
| `company_subject.txt` | V1_LEGACY_ONLY | 自建 V1 八维素材结构，不得覆盖 Section Contract |
| `industry.txt` | V1_LEGACY_ONLY | 自建 V1 五维/篇幅约束，不得进入 P3R/P4R |
| `financial_analysis.txt` | V1_LEGACY_ONLY | 含旧阈值/旧财务写法；不得覆盖 Formula Review 或 Financial V2 |
| `synthesizer.txt` | V1_LEGACY_ONLY | V1 全文重写与缺失措辞存在风险，不得进入 V2 正式链 |
| `schema_mapping.txt` | V1_LEGACY_ONLY | 仅 V1 schema mapping 兼容路径 |

## P3R/P4R 完成门

树结构调整期间不新增用于“猜标题边界”的 LLM Prompt。`PageLayout`、`DocumentOutline`、`OutlineSpan` 和 `TableObject` 必须由版本化、可复核的结构算法产生；如后续允许模型提供候选，也只能作为不具权威的导航候选，不能生成节点真值、覆盖状态或 Citation。

R3/R5 必须以调用链测试证明：

1. 原子 Prompt 产物进入唯一 `TopicResearchPack`，不直接成为完整章节。
2. 正式 P4 writer 消费 complete exact material manifest、三类 disposition 与 authority facts，只输出 pre-gate Draft；aggregate mechanical gate、factual entailment、context no-entailment 与 accepted-binding 调用链必须独立测试，Prompt 无权自批或写回 Pack。
3. `HISTORICAL_EVAL_ONLY`、`EXPERIMENTAL_ONLY`、`V1_LEGACY_ONLY`、`SUPERSEDED`、`LEGACY_CHAPTER_LEVEL_ONLY` 文件在正式链调用次数为 0。
4. Prompt 版本、WritingSpec/Contract 版本和模型进入依赖指纹；Prompt 变更有专项回归。

# 文档治理与权威索引

> 版本：v1.10 · 2026-10-01
> 用途：告诉开发者和开发代理“当前应读什么、什么只是历史、冲突时听谁的”。
> 本文件不定义业务规则或代码接口；具体规则以对应权威文档为准。

## 1. 权威顺序

发生冲突时按以下顺序处理，不得把时间更晚但层级更低的执行报告当作上位设计：

1. `AGENTS.md`：项目宪法、不可违反的工程与安全边界。
2. `DESIGN_V2.md`：现行产品、业务与架构设计。
3. 已确认业务与运行时基线：冻结 Contract v2 `templates/contracts/standard_v3.yaml`、`templates/policies/source_policy_v1.yaml`、`templates/writing_specs/credit_report_v1.yaml`、`templates/presentation_profiles/interview_demo_v1.yaml`，以及仍分别有效的 `contracts/sc_decisions.yaml`、`FORMULA_REVIEW.md`；`templates/contracts/standard_v2.yaml` 是固定哈希的 V1 兼容资产，不是当前业务定义的优先来源。各资产只在其声明的版本/范围内有效。
4. `V2_IMPLEMENTATION_PLAN.md`：阶段顺序、依赖和出口。
5. `2026-09-30_DEMO_BACKBONE_MILESTONE.md`：经上位设计批准的限时纵向演示里程碑；只在其范围内优先于正式父任务的执行顺序，不改变冻结业务语义或正式阶段关闭条件。
6. 当前正式总任务书：`PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`；树结构主线由 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` 管理。
7. `V2_TODO.md`：事实进度与下一动作；不能创造或覆盖设计。
8. `CLAUDE.md`：Claude Code 启动导航；不得复制或覆盖上述文档。

任何历史任务书、验收报告、调试记录、代码审计、生成报告或 Prompt 都不能改变以上顺序。若 `DESIGN_V2.md` 与机器 Contract/已确认业务配置发生语义冲突，不得静默按优先级挑一个继续执行；必须 fail-closed，先修正文档并发布兼容的新 Contract/配置版本。

## 2. 当前全局修复方向

当前不是继续润色旧发布层，而是修复 P3 到 P4 的正式内容接口：

**2026-10-01 现行覆盖索引：**`DESIGN_V2.md` §0.21 把**本次演示**原表呈现改为经人工确认的原 PDF 区域只读来源视图；业务正文实际采用的少量营收/成本/毛利数字另由合格格级事实授权并由 Python/Decimal 计算。原 PDF 区域**不**是 `TableObject`、Pack/Writer 表材料、数字权威或 Contract `set_complete`；§0.17–§0.19 的逐表结构化签发仍属历史裁决/正式能力轨，**不再是本次 M930-3 演示前置**。§0.20 的 Pack 驱动逐句引用写作、确定性硬核对和独立只读审阅继续有效。研究侧 Pack/事实资格、财务权威、冻结业务资产与正式 TS5 门不变，财务表不得换成原 PDF 截图。下方旧链图与原子/绑定/蕴含说明仅为历史迁移对照。此项仅更新设计和验收，尚未证明代码或真实内容通过。

```text
SectionContract / SectionTask
  → P4 Worker orchestration shell
  → Harness-owned TopicResearchState
  → (InformationNeed → Router → 既有单题执行器 → ToolRegistry)*
  → EvidenceBlock（不可变来源锚点）
  → PageLayout / DocumentOutline（版本化、只读）
  → OutlineSpan / TableObject 检查 + 有界后备扩读
  → 事实/来源校验
  → ResearchMaterial 完整保留
  → 高风险事实预验证（非穷尽）
       → FactCandidate
       → versioned FactQualificationDecision
            ├─ eligible → qualified SupportedFact
            └─ rejected → typed rejection decision / audit（原材料仍完整保留；
               仅当 Contract 必需 aspect/事实仍未取得时才形成 gap/block）
  → authoritative TopicResearchPack successor
  → exact Writer material context（manifest 固化：current Pack materials 并集）
  → SectionDraft
       ├─ ClaimCandidate[] + NarrativeDraftUnit[]
       ├─ ProposedSupportRef[]
       └─ FollowUpNeed[]
  → Claim Binding Gate：每个 subject revision 的完整 proposal 集恰有一个 aggregate ClaimBindingDecision
       ├─ factual candidate → Section Evaluator → ClaimEntailmentDecision
       └─ context NarrativeDraftUnit → 不进入 entailment
  → 每个通过的 proposal → typed AcceptedSupportBinding
  → SectionClaim + final NarrativeSentence / Paragraph / Table + Unresolved
  → post-gate SectionResult（单向引用 section_draft_id）
  → deterministic assembly
  → Assurance Controller orchestration
      ├─ deterministic hard gates
      ├─ Independent Review Agent（独立上下文、只读、只输出 ReviewIssue[]）
      └─ deterministic version-bound aggregation
  → separate human-acceptance status
```

其中：

- `ResearchOutcome` 是一次原子研究记录，不再是 P4 的唯一内容输入。
- `EvidenceBlock` 只承担来源身份、原文回查和引用锚点，不再被视为可靠段落、标题、表格或业务边界；历史 Evidence ID/Evidence Set 不回写。
- 目录/书签用于产生标题候选，正文大小标题和小标题、编号连续性与版式锚点负责确认边界；当前扁平 `section_path` 只能作弱提示，不能反推正式标题树。
- 本地 RAG、材料包和 P4 的正式消费单位是 `OutlineSpan` 与 `TableObject`；标题/简介相似度只做节点候选定位，不得直接证明 aspect covered。
- 相邻块/页、rolling frontier 和显式引用扩读降为 outline 不可用、低置信或跨节点引用时的有界后备机制。
- required aspects 是研究调度与完成判断单位；一次查询可覆盖多个 aspect，只对缺口补检。
- 命中候选节点后应读取节点/必要子树的连续 spans、关联表格和明确引用；只有 outline 不可用或低置信时才有界读取相邻块/页，不能只保留一条短答。
- **研究侧预验证事实**进入唯一 `TopicResearchPack`；写作侧路径 B 产生的正式 `SectionClaim` 属于 Writer/Section successor，不进入 Pack。不得启用第二套 topic research 运行时。
- 公司/行业章节消费 Pack；财务章节继续消费 `FinancialFactPack`，并组合 Evidence 背书的附注事实。
- Contract 负责定义“必须研究什么、证据和缺口门槛”；版本化 `SectionWritingSpec` / `ReportPresentationProfile` 负责定义“如何组合为小节、段落和表格”，二者都必须进入依赖指纹，Prompt 和旧 Markdown 模板不得承担影子 Contract。
- 一个 Section 必须消费与 `SectionTask.topic_ids` 完全匹配的完整 Pack 集；缺少整个 Topic 的 Pack 也必须显式暴露，不能通过挑选已有材料生成看似完整的章节。
- 材料保留与事实资格是两条不同轴：材料层回答“来源里有什么”，事实层回答“哪些内容构成完整、可独立判断真假的命题”；候选事实被拒绝不得删除、裁碎、改写或降级原材料。每个被拒绝候选必然产生且恰好一条 typed、版本化 rejection decision/audit；若 Contract 必需 aspect/事实仍未取得，再另外形成 gap/block。Audit 与 gap 都不能替代对方。当前 8 条 wire-adopted candidates **不等于** 8 条合格事实；旧 Pack 与旧 run 保持历史只读。
- 事实处理是**双通道**的：研究侧对高风险硬事实做非穷尽预验证，写作侧由 Writer 在完整材料上提出原子 `ClaimCandidate`。路径 A 绑定 authority-specific 的预验证事实；仅 topic authority 必须绑定其 ResearchMaterial，financial/note/external 使用各自 payload/locator，禁止伪造材料。正式外部权威是 `ExternalFact`，必须绑定 qualification decision、snapshot/body hash、SourcePolicy、日期、命题与 locator；`ExternalSnapshot` 仅为来源载体。路径 B 只用于非高风险描述性原子，绑定 exact topic material/payload/locator。两条路径都先进入每个 subject revision 唯一的 aggregate Binding Gate；只有 factual candidate 再进入 Section Evaluator，context 以 `NarrativeDraftUnit` 为 target 且不进入 entailment。Writer 不检索、不联网、不自批，需要更多材料时只能发出结构化 `FollowUpNeed`（`DESIGN_V2.md` §0.13）。
- Writer 的 material context 是**精确集合**：必须精确等于当前 `VerifiedPackSet` 中所有 current Pack 的 `materials` 并集，并由确定性、版本化的 manifest 固化；每个 manifest 成员都有引用其 Pack 侧 `ResearchMaterialDisposition` 的 `WriterMaterialProcessingDisposition`。合格权威事实是否 claimed/supporting/not-presented 另由 `FactNarrativeDisposition` 记录。三者职责与 identity 不得混用。「未使用」本身不是 gap，但 Contract 必需事实未取得必须形成 gap/block，不得伪装成 `not_used`。
- 支撑边是正交轴：`authority_kind`、`support_semantics`、`support_role` 与 authorization path 彼此独立。所有合法变体都必须有 stage/target、container、source/provenance、citation/fingerprint；authority-specific payload/locator 按封闭 tagged union 校验。`ClaimSupportRef` 只是 `ProposedSupportRef | AcceptedSupportBinding` 的兼容 union，不是第三种 wire。factual accepted binding 引用 aggregate binding 与 entailment decision；context accepted binding 以 `NarrativeDraftUnit` 为 target，只引用 aggregate binding且不得授权事实。完整字段表见 `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §6.3。
- 持久化分为**四个边界**，不得由同一个 successor 承担全部：(1) 研究/Pack successor 保存材料、`ResearchMaterialDisposition`、事实候选/资格决定/结果、formal `ExternalFact` 与 snapshot refs、研究 gaps/conflicts/not-found；(2) Writer/Section/Narrative successor 保存 exact manifest、`WriterMaterialProcessingDisposition`、`FactNarrativeDisposition`、SectionDraft/candidates/narrative-draft/proposals/decisions/accepted bindings、SectionClaim、final Narrative、SectionResult 与 Unresolved；(3) `FollowUpNeed` 是独立 run/trace，执行后形成新 Pack；(4) Review/Assurance 保存 report-version-bound issues/status。写作侧对象不得计入 Pack content identity。
- 三套 dependency/content identity **与上述边界是不同轴的划分，不是一一对应关系**：Pack 资格 identity 覆盖资格政策/schema/runtime、材料 disposition、候选/决定/结果、ExternalFact 与 research gaps；Section/Writer/Evaluator identity 覆盖 spec/profile/prompt/model、manifest、两类 Writer disposition、Draft subjects/proposals、aggregate binding、factual entailment、accepted bindings、Claim/Narrative/Result/Unresolved；Review/Assurance identity 覆盖 report-version-bound issues/status。`FollowUpNeed` 位于三套 content identity 之外。历史对象不删除、不改写，上游 current identity 变化只使依赖旧输入的下游对象失去 current eligibility。
- P4 同时保留细粒度可审计 Claim 和面向人的完整段落/表格，安全正确与内容完整分别验收。
- 当前面试版在生成后只读展示状态与信息缺口，不实现用户补件、缺口绑定、Evidence 增量更新或用户触发续跑；相应字段只保留未来扩展接口。
- Phase 4 的 Section Evaluator 不是最终放行者，但它是 factual `ClaimCandidate` 原子语义核验的唯一位置：每个 factual candidate revision 只接受一个通过的 aggregate binding decision 与相同 support-set digest，并产出一个 `ClaimEntailmentDecision`；context subjects 不进入该门。独立 Review Agent 在章节定稿与确定性组装之后运行，只读报告、Claims、引用、材料索引与缺口并输出 `ReviewIssue[]`；不能重写、补研究、联网、重复机械门、覆盖 hard failure 或改写既有决定。确定性 Assurance Controller 先执行硬门再聚合 issue，最高系统状态仍需人工最终确认。
- 正式 Phase 5 在 P3R/P4R 内容门通过前不得开始；M930 仅提前实现代表性、隔离的接口纵切，不能被记为 Phase 5/6 关闭。

## 3. 文档分类

### 3.1 当前权威与当前执行

| 文件 | 状态 | 职责 |
|---|---|---|
| `AGENTS.md` | ACTIVE / CONSTITUTION | 硬约束、文档优先级、实现纪律 |
| `DESIGN_V2.md` | ACTIVE / DESIGN | V2 产品、架构、数据模型、质量门 |
| `V2_IMPLEMENTATION_PLAN.md` | ACTIVE / ROADMAP | 阶段顺序、依赖、出口 |
| `2026-09-30_DEMO_BACKBONE_MILESTONE.md` | ACTIVE / TIME-BOXED MILESTONE | 9月30日前代表性纵向 Demo 的范围、三轴完成语义、批次与停止条件 |
| `V2_TODO.md` | ACTIVE / STATUS | 已完成、进行中、下一步 |
| `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` | ACTIVE / UMBRELLA TASK | P3R/P4R 父级范围、阶段与总门禁 |
| `TREE_STRUCTURE_ADJUSTMENT_TASK.md` | ACTIVE / FORMAL TREE GATE | R2→R3 前的 PageLayout/DocumentOutline/OutlineSpan/TableObject 正式强制门；当前停在 TS5 no-go |
| `TREE_STRUCTURE_IMPLEMENTATION_PLAN.md` | ACTIVE / PLAN（从属） | 树结构调整的逐文件实施计划；只细化、不覆盖树任务书与上位设计 |
| `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` | ACTIVE / PLAN（从属） | M930 逐文件实施计划与编码前入口门；只细化、不覆盖里程碑说明与上位设计 |
| `README.md` | ACTIVE / USER OVERVIEW | 项目入口与当前可用范围；不作为开发规格 |
| `CLAUDE.md` | ACTIVE / TOOL BOOTSTRAP | 仅导航 Claude Code 阅读权威文档 |

### 3.2 已确认业务基线

| 文件 | 状态 | 说明 |
|---|---|---|
| `templates/contracts/standard_v2.yaml` | FROZEN COMPAT RUNTIME CONTRACT v1 | 历史兼容 52 问 Contract，固定 hash，禁止覆盖；不再代表 P3R 现行业务粒度 |
| `templates/contracts/standard_v3.yaml` | FROZEN CONTRACT v2 | 52 问 / 187 aspects 的现行冻结业务契约；树结构调整不得修改其业务语义 |
| `templates/policies/source_policy_v1.yaml` | FROZEN SOURCE POLICY | 来源等级、充分性与外部事实门槛 |
| `templates/writing_specs/credit_report_v1.yaml` | FROZEN WRITING SPEC | aspects 到授信报告小节/表格/正文角色的确定性映射 |
| `templates/presentation_profiles/interview_demo_v1.yaml` | FROZEN PRESENTATION PROFILE | 当前面试版呈现边界 |
| `contracts/sc_decisions.yaml` | CONFIRMED V1 BUSINESS DECISIONS / COMPAT | SC-01～SC-05 的历史兼容业务基线；现行来源细则以冻结 Source Policy/Contract v2 为准 |
| `contracts/review/section_contract_review.md` | CONFIRMED_V1_REVIEW / HISTORICAL_SCOPE | Phase 0B 对 Contract v1 的业务复核记录，不证明 P3R 内容完整性 |
| `contracts/review/required_aspects_review.md` | CONFIRMED_V1_REVIEW / HISTORICAL_SCOPE | 现有 required aspects 派生复核；不替代 P3R 全量 aspect/evidence/display 审计 |
| `FORMULA_REVIEW.md` | CONFIRMED FINANCIAL POLICY | Financial V2 公式与代理/缺失口径 |

### 3.3 历史设计、任务书和交付报告

下列文件保留当时事实与审计价值，但不是当前实施指令：

- V1：`DESIGN.md`、`shouxin_cj_zongjie.md`、`OPTIMIZE.md`、`tree.txt`。
- Phase 0～2：`BASELINE_RUNNER_DEVELOPMENT_TASK.md`、`SECTION_CONTRACTS_DEVELOPMENT_TASK.md`、`EVIDENCE_ARCHITECTURE_DEVELOPMENT_TASK.md`、`FINANCIAL_PROVENANCE_RECONCILIATION_DEVELOPMENT_TASK.md`、`FINANCIAL_A2_A5_DEVELOPMENT_TASK.md`、`FINANCIAL_A6_A7_DEVELOPMENT_TASK.md`、`ROUTER_HYBRID_RETRIEVAL_DEVELOPMENT_TASK.md`。
- 财务交付：`A2_A5_DELIVERY_REPORT.md`、`A6_A7_DELIVERY_REPORT.md`。
- Phase 3 v1：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` 及全部 `PHASE3_*REPORT*`、`PHASE3_*ACCEPTANCE*`、`PHASE3_*VALIDATION*`、`PHASE3_POST_UNSEEN_REGRESSION.md`、`PHASE3_DEMO_ENV_RESTORE_ACCEPTANCE.md`。
- Phase 4 基础：`PHASE4_DEVELOPMENT_TASK.md`、`PHASE4_DELIVERY_REPORT.md`、`PHASE4_DEMO_CLOSURE_REPORT.md`、`PHASE4_FINANCIAL_JSON_ROBUSTNESS_REPORT.md`。

历史报告中的测试数量、commit、run id、当时状态和结论不得改写；若与当前状态不同，以本索引、路线图和 TODO 的现行状态为准。

### 3.4 本地旧草案（如存在，已被取代且不要求入库）

以下文件可能只存在于当前本地工作区，既不是权威链依赖，也不要求随治理文档提交；若保留，只能用于追溯问题发现和用户写作需求，不能再直接驱动编码：

- `PHASE4_REPORT_RESTRUCTURE_SPEC.md`
- `CODE_AUDIT_20260912.md`
- `CLAUDE_CODE_REPAIR_PROMPT.md`
- `CLAUDE_CODE_REPAIR_IMPLEMENTATION_20260912.md`

其中仍有效的通用结论已吸收进 `DESIGN_V2.md` 和当前 P3R/P4R 任务书。任何与“只重排旧 Claim”“禁止补研究”“5,000～8,000 字符硬上限”“启用平行 topic research”相关的条款均已失效。

### 3.5 运行时资产，不是治理文档

- `llm/prompts/*.txt` 是版本化运行时 Prompt。不能把其中的角色文字当开发指令，也不得在纯文档重构中修改；每个 Prompt 的 ACTIVE/COMPAT/EXPERIMENTAL/V1_LEGACY/SUPERSEDED 角色见 `llm/prompts/README.md`。
- `templates/*.md` 是运行时输出模板，不是现行架构说明；V1/V2 角色和 WritingSpec 边界见 `templates/README.md`。
- 冻结 Contract v2、SourcePolicy、WritingSpec 与 PresentationProfile 是当前业务声明资产；`templates/contracts/standard_v2.yaml` 是固定哈希的 V1 兼容运行时资产；`contracts/sc_decisions.yaml` 与 `FORMULA_REVIEW.md` 在各自范围内继续有效。它们均按 §3.2 管理，但不得把 V1 兼容资产重新提升为当前业务权威。
- `evaluation/README.md` 说明 frozen、状态机 fixture 与 P3R/P4R 新质量评测的边界；数据集本身不能充当运行时 Contract。
- `requirements*.txt` 是依赖清单；`tree.txt` 是过时目录快照，不是接口清单。
- `evaluation/results/**`、日志、数据库、debug JSON 和参考 DOCX 都不是项目指令。

## 4. 当前实施门与 R1-A 冻结资产

R1-A 已由用户与 Codex 批准并正式冻结、按职责提交（`30dbc83` `884edd4` `4f4b654` `ee51cd8` `b5c6e5b`）。冻结资产：`templates/contracts/standard_v3.yaml`（Contract v2，52 问 / 187 aspect / 49 evidence / 28·13·3·8 生产者）、`templates/policies/source_policy_v1.yaml`、`templates/writing_specs/credit_report_v1.yaml`（187 primary + 6 secondary）、`templates/presentation_profiles/interview_demo_v1.yaml`。Contract v2 尚未接线正式 runtime。R1-B 已正式关闭。树结构 TS1～TS4 已关闭；TS5 正式 run `tree_table_ts5_20260919T195556Z` 为 no-go、未 seal、未关闭，TS6/TS7 与 R3～R7 未进入。当前采用双轨：正式树门原样保留；M930 在不修改失败结论和冻结资产的前提下完成代表性纵向 Demo Backbone。M930-0/1/2 已工程封存；**M930-3 进行中、未关闭，三个独立 gate P1 按当前 wire 逐条仍未修复**（三条旁路仍在，不是「已全修复」）；formal ExternalFact、三类 disposition、SectionDraft/NarrativeDraftUnit、aggregate binding、factual entailment/context 分支、per-proposal accepted bindings、post-gate SectionResult、FollowUpNeed 与自然 Narrative successor **在工作区已有实现（未提交、未评审），真实纵链未复验**；**M930-4/5 未开始**。权威链同步只统一规则表述，不等于代码已实现；工作区已有实现也不等于真实纵链已复验。
**（2026-09-27 追加）** 定点业务闭环批把两条口径写进了同一份权威链，**均未关闭**：① **三条日期轴分开**——事实适用期（风险高，只走路径 A 或材料逐字原文）/ **来源归属**（材料身份、版本、页码与可核实披露日，由来源身份**确定性渲染**，作者不手写，披露日不可核实即 `unknown`，入库时间·PDF 元数据·财务期末一律不得冒充）/ `report_as_of`（报告生成日）；非数值的一般经营描述不要求每句机械重复期间，但不得写成“一直如此/截至报告生成日仍然如此”，只由旧材料支持的内容保留历史归属或留缺口，新闻只有发布日时只能写“某日发布的报道提及……”。权威章节：`AGENTS.md` §5、`DESIGN_V2.md` §3.1 第 13 条与 **§0.13 第 11 条**。② **最终句语义门（裁决 = B）**：每条承载事实的最终句（含 `composed`）须由唯一 aggregate `FinalSentenceFidelityDecision` 逐事实原子核验，缺失/拒绝/过期三态阻断定稿；**B 落地前真实 run 的正文只受 `natfid-1` 表面比较器保护**。此外，逐批拒绝不得抹掉其它批次已取得的合法内容，失败须另给明确标注**「未核验、不可发布」**的只读门前诊断视图与逐批 typed 拒绝原因，该视图不得冒充 `SectionResult` 或正式预览。

**（2026-09-28 追加）** 演示主题**目标表资格门**（权威 `DESIGN_V2.md` §0.17，同步 `AGENTS.md` §7/§8、`V2_IMPLEMENTATION_PLAN.md`、`2026-09-30_DEMO_BACKBONE_MILESTONE.md`、`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §6.7.3、`CLAUDE.md`、`V2_TODO.md`）：演示 `company_business` **必须同时**呈现有实质内容的经营文字**和**合格的原始业务表格；「完整列出」限于本主题 **18 个 aspect × 三份上传材料**逐格列候选／读取／资格／去向／未取得原因，**不**要求覆盖全报告 187 个 aspect，也**不**是把来源原文堆进正文。退出条件两分且不得合并：目标表**逐对象取得正式资格并经现有图／工具链进入 Pack**；材料里确有表格却因识别、续表、资格或接线失败而未呈现 ⇒ **系统能力缺陷**、演示主营业务内容门**不得通过**，**只有**可核查检索确认来源确无该内容才标**来源缺口**。本门**不**宣称所有类型 TS5 表格能力已正式关闭；诊断平铺文本与 `refused`/`partial` 对象不得升格为材料或数字权威；「未核验、不可发布」的诊断预览不能替代最终验收。历史 TS5 no-go、信任根与既有 run/验收报告事实**一字未改**。

**（2026-09-29 追加，按 §0.19–§0.20 更新）** 先选 `company_business`＋财务两 topic 验 M930-3 核心真实纵链，再在同一切片验独立审阅与只读展示，最后扩行业/外部检索；旧三节 scope 和原完整验收保留。目标业务表由同一图侧来源**逐表完整证明**后经既有工具/Pack/Writer 单通道投递，`tobj-*` 不再独立放行；无关页的文档级拒发不自动否决本表，全文档 TS5 拒发状态仍保留。Writer 新主线是 Pack 直接生成逐句引用正文，旧 Claim 拼文链仅作历史兼容。不得把图侧诊断、对象 `complete`、MOCK 全绿或未审预览读成正式放行。代码与真实内容仍待验。

**（2026-10-01 本次演示内容门覆盖）** 上述 2026-09-28/29 目标表逐表签发与图→Pack 投递的文字保留当时决策事实，但已由 `DESIGN_V2.md` §0.21 替代为**本次 M930-3 演示**的原 PDF 区域只读展示；正式 TS5/结构化表能力轨不随之关闭。演示需另有少量合格格级事实授权正文数字，财务表维持 FinancialFactPack/FinancialSnapshot 确定性生成并核对诊断/正文展示层级。`AGENTS.md` §7、里程碑 §12 第 4c 条为同步验收口径；旧报告/run/测试结论不倒改。

以下事项包含已批准但尚待实施/验收的架构门，以及后续仍须用证据决定的技术项；开发代理不得改变其业务含义：

1. **双轨门禁**：正式树结构调整仍须通过真实小标题、跨标题块、表格/附注和非 300750 样本后才能进入 R3；M930 只允许代表性纵切，可如实展示 `needs_changes`/gap，不能将未签发表对象用于事实或把 Demo 通过映射为正式关闭。
2. **Topic 预算具体数值**：S/M/L/XL 只是初始分档，须由合成测试和少量真实纵向样本校准；不得按 300750 或 case id 调参。
3. **搜索 Provider 是否更换**：当前正式运行时仍为博查；先区分查询规划、候选排序、fetch 可达性和 Provider 召回，再决定是否单独做对照。
4. **统一数字事实层的物理存储**：方向是统一只读 Fact Registry/语义身份，不是立即把 FinancialSnapshot、Evidence 附注事实和 formal `ExternalFact` 合并进一张物理表；`ExternalSnapshot` 只保留来源载体身份。
5. **报告最终篇幅**：不设 8,000 字符硬门；2～3 万中文字符仅为人工参考，最终由 Contract 覆盖、信息密度、可读性与演示时间共同决定。
6. **R0/R1-A 实际测试与工作区状态**：调用链复验、当前 diff 和测试数字只记录在 `V2_TODO.md`；长期治理文档不写死易过期的工作区状态。

## 5. 文档维护规则

1. 新业务或架构决定先改 `DESIGN_V2.md`，再同步路线图、当前任务书和 TODO。
2. 阶段任务书只细化上位设计，不得反向覆盖设计；执行结束后改状态，不把“尚未编码”永久留在旧任务书顶部。
3. 交付报告只记录事实，不充当下一轮指令；被后续发现推翻的关闭结论必须在顶部增加现行状态说明。
4. `CLAUDE.md` 只保留导航与当前门，不复制项目宪法、目录树或接口全文。
5. `README.md` 面向使用者；尚未重验的新机安装或端到端流程必须明确标注，不写成已保证可用。
6. 每次调整权威文档后检查版本互链、相对链接、状态词和废止条款；文档变更与代码变更分开提交。

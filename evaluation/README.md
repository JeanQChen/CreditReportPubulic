# Evaluation 资产角色与冻结边界

> Evaluation 文件验证特定能力，不是运行时 Contract，也不能直接定义报告目录或研究充分性。

## 历史冻结资产

- `datasets/v1_baseline.jsonl`、`baseline_contract_mapping.jsonl`、`v1_baseline.split_manifest.json` 及既有 frozen/unseen 结果只用于 V1 检索、Router、单题实际路径和安全回归。
- 历史 `coverage_role=full` 表示当时原子 case/mapping 的口径，**不表示**完整 Topic 的 required aspects 或整章内容已覆盖。
- Gold document/page 只进入运行后离线诊断，不得进入 query、Router、Topic scheduler、补检、充分性、停止、写作或运行时评分。
- 冻结 dataset、split、gold、run 和历史指标不得为了 P3R/P4R 提分而修改、重标或覆盖。

## Phase 4 基础状态机资产

- `datasets/section_cases_v1.json` 主要验证 Section 状态流转、Store、Evaluator 和 fail-closed 编排。
- 合成引用或语义不真实的 fixture 只能证明状态机行为，不能作为主营业务、行业或财务章节内容正确性的证据。

## P3R/P4R 新评测

新版本必须与历史冻结集分开，至少覆盖：

1. 本地长叙述与受控上下文扩读；
2. 本地表格/财务附注；
3. 结构化财务与跨期变化；
4. 外部时效研究和来源政策；
5. 事件/负面核验；
6. 本地 + 结构化 + 外部混合 Topic；
7. 非 300750 公司和未见 Topic 组合。

结果必须分别报告：安全正确性、required-aspect 终态与覆盖、材料/事实保留、三类 disposition、外部 funnel 的 formal ExternalFact yield、完整 Pack 集与 exact manifest、proposal-set 完整性、每 subject revision 唯一 aggregate binding、每 factual candidate 唯一 entailment、context no-entailment、accepted-binding→Claim/final Narrative 映射、Draft→Result 单向性和人工可读性。完整 eval 0 failed 不能单独宣布内容通过。

## 树结构调整评测（R3 前强制门）

该评测验证正式材料边界，不用“旧 Evidence 命中”或“单元测试全绿”替代真实文档检查。至少分别报告：

1. `PageLayout` 是否覆盖全部电子 PDF 页面、正文行和可获得的源坐标；目录关键词误命中不得导致正文丢失。
2. 目录/书签候选与正文一级至小标题的对齐率、层级准确率、重复标题路径身份和低置信 reason codes。
3. 正文归属率：所有内容进入可信节点/祖先或显式 `unassigned`，静默丢失为 0。
4. 跨标题 Evidence 的 span 切分准确率与无损重构；不得把整块混合 Evidence 继续作为正式材料输入。
5. `TableObject` 的表题、单位、物理表头、表体、合计、续表和 component provenance 完整率；表格与说明文字分离但关系可回查。
6. 树感知检索实际返回 node/span/table；报告跨标题污染率、重复率和 legacy Evidence fallback 次数/原因。
7. Contract→标题/简介相似度只产生候选；必须用反例证明它不能自动改变 aspect coverage、set_complete 或 sufficiency。
8. Pack/P4 引用落到底层 span/table component 或结构化权威；导航简介直接被引用的数量必须为 0。
9. 主营业务、核心竞争力、主要子公司、财务附注、显式引用及非 300750 样本的 before/after。
10. 历史 Evidence、索引、Pack 与结果仍可读；outline/span/table/index 版本变化正确触发 stale。
11. 每个可导航节点具有抽取式、可回溯简介或明确 unavailable 原因；版本化 AspectNavigationProfile 的候选召回、误召回和公司无关性可审计。
12. PageLayout 原文与 Evidence 规范化文本的 alignment 可复核；歧义映射 fail-closed，字符偏移不得猜测。
13. fallback/unassigned 仍输出精确 OutlineSpan，不能把 whole Evidence 升级为材料，也不能在缺少已验证 outline/table 边界时单独证明 set_complete。

真实树结构产物使用新 `evaluation/results/tree_structure_<run_id>/`，不得覆盖历史 R2 目录。至少三份真实电子 PDF 必须逐份产出和审查，另有一个非 300750 fixture；不得用同一文档重复冒充多份真实样本。`boundary_incomplete` 或 `unassigned` 可以是诚实结果，但必须说明范围、原因和对后续报告的影响。

## 2026-09-30 Demo Backbone 评测

M930 使用独立的新 run_id 和产物目录，只验证**代表性纵向演示**，不替代上面的正式树门或 P3R/P4R、Phase 5/6 关闭评测。其报告必须分开给出：

1. `design_surface_coverage`：来源/结构、材料/事实、Pack、Writer、章节检查/组装、独立 Review Agent、确定性 Assurance、只读 UI 是否真实连通；
2. `demo_content_coverage`：被选代表性 aspect/材料是否足以演示，未纳入内容和 gap 必须列明；
3. `formal_phase_closure`：TS5、树门及各正式阶段仍按原门禁记录，不因 Demo 可运行而改变；
4. 状态分层：流程完成、预览可用、系统 Assurance、人工确认四项绑定当前 report version；正式阶段关闭是独立的只读治理快照，不由 Demo Controller 计算；
5. 来源与内容门：错误事实、错误数字和伪造引用为 0；未签发的 TS5 表格只能作为诊断/gap 展示，不能进入正式 Pack、Claim 或正文；
6. 可重复演示：现场默认加载持久化真实产物，不以临场网络、LLM 或完整 eval 成功作为页面可看的前置条件。

所有 M930 写作链评测必须 fail-closed：Draft 引用未来 decision/Claim/final Narrative/Result；proposal 缺失、重复、额外或 digest 漂移；context 指向 Claim、携带 entailment 或授权事实；以及裸 `ExternalSnapshot` 冒充事实 authority，均必须失败。

`preview_available + system_blocked + human_not_reviewed` 是合法且可演示的真实状态；不得改写为成功，也不得因此把 Demo 评测记为失败，只要缺口、影响和阻断原因均可回查。

`evaluation/results/**` 是生成的审计证据，不是项目指令，默认不得提交或批量改写。

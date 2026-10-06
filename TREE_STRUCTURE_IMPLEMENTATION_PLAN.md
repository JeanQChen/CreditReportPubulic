# TREE_STRUCTURE_IMPLEMENTATION_PLAN.md

> 本文件是 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` §13 要求的逐文件实施计划，**只写计划，不写代码**。
> 本文件不覆盖任何上位设计；冲突时严格按项目宪法的权威顺序处理：
> `AGENTS.md` > `DESIGN_V2.md` > 已确认业务基线（冻结 Contract v2 / Source Policy /
> WritingSpec / PresentationProfile / `FORMULA_REVIEW.md`，以及各自声明范围内的 v1 兼容资产）>
> `V2_IMPLEMENTATION_PLAN.md` > `2026-09-30_DEMO_BACKBONE_MILESTONE.md` > `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` >
> `TREE_STRUCTURE_ADJUSTMENT_TASK.md` > `V2_TODO.md` > `CLAUDE.md`。
> `DOCUMENTATION_INDEX.md` 只负责文档地图与状态导航，不插入业务/架构权威链。
>
> 状态：**TS4 已关闭；TS5 未关闭**（TS5 的机制实现与真实运行已完成，但内容门 no-go，详见下方追加记录）。
> TS4-B 的完整 `factor_entries` 与 `SPAN_CONFIDENCE_MIN = 0.85` 已冻结并进入身份，见下方「TS4 关闭记录（2026-09-19）」。
> **TS5 §十九仍是该批次的冻结执行规格，但正式 run `tree_table_ts5_20260919T195556Z` 未通过关闭门**；
> 不得 seal、改 trust roots、重写人工 verdict 或将其包装为通过。上位批准的 `2026-09-30_DEMO_BACKBONE_MILESTONE.md` 代表性
> Demo 轨已走过 M930-0/1/2，M930-3 未关闭。下一步唯一是新写作主链的 file-level changelist 与全仓影响清点，覆盖 factual A/B、四类 authority 与 formal `ExternalFact`、audit + conditional gap、四个持久化边界、三套 identity、RMD/WMPD/FND 三类 disposition、Draft subjects/proposals→aggregate binding→factual entailment/context binding→accepted bindings→Claim/final Narrative→post-gate Result、`FollowUpNeed` 与 Reviewer/Controller；
> 该时间盒不进入 TS6/TS7/R3～R7 的正式关闭流程，也不宣布树结构调整或 Phase 4 关闭。
>
> **更新（2026-09-28，演示主题目标表资格门；权威 `DESIGN_V2.md` §0.17）**：演示 `company_business` **必须同时**呈现有实质内容的
> 经营文字**和**合格的原始业务表格；「完整列出」限本主题 **18 个 aspect × 三份上传材料**逐格列出候选／实际读取／资格／Pack 与 Writer
> 去向／未取得原因，**不**要求覆盖全报告 187 个 aspect，也**不**是把来源原文堆进正文。退出条件两分且不得合并：目标表**逐对象取得正式资格
> 并经现有图／工具链进入 Pack**；材料里确有表格却因识别、续表、资格或工具接线失败而未呈现 ⇒ **系统能力缺陷**、演示主营业务内容门
> **不得通过**；**只有**可核查检索确认来源确无该内容 ⇒ **来源缺口**。本门**不**宣称所有类型 TS5 表格能力已正式关闭，TS5 no-go
> 与本节冻结规格**一字未改**。诊断平铺文本与 `refused`/`partial` 对象不得升格为材料或数字权威；「未核验、不可发布」的诊断预览
> **不能替代**最终验收。工期影响：先完成相关文档最小一致性修订，再产出第二批 file-level changelist + 全仓影响清点（含把表内数字升格为
> 合格事实所需的路径 A 预验证＝触及冻结 Contract/SourcePolicy 的子项）并获逐次授权，之后才动 TS5／图表接线代码。
>
> **更新（2026-09-17）：TS2 已完成并通过**（含 TS2.1 与最终关闭轮，见下方「TS2 关闭记录」）。
> **更新（2026-09-18）：TS3 已由用户与 Codex 独立验收通过并关闭**（`hq-4` / `trg-3` / `tocr-2` 与当前真实
> 节点集合冻结；诚实存在的 `toc_target_unresolved`、编号歧义与 fixture 无目标不是缺陷，不再修到清零）。
> **更新（2026-09-18）：TS4-A 已由用户 + Codex 独立验收通过并工程收口**（原子 commit `468f431`；完整 eval
> `14015 passed / 0 failed / 4 skipped`；真实产物 `evaluation/results/tree_span_ts4_ts4a_sb7_20260918T200000Z/`）。
> TS4-A 范围内 P0=0、P1=0；`SPAN_CONFIDENCE_MIN` 仍为 `None`、completion 仍为 `False`（`stage = distribution_only`）。
> **更新（2026-09-19）：TS4-B 已由用户 + Codex 独立验收通过，TS4 整体关闭**（TS4-B 原子 policy/test commit
> `9537695`；真实产物 `evaluation/results/tree_span_ts4_ts4b_20260918T174908Z/`；完整 eval
> `14270 passed / 0 failed / 4 skipped`）。
> **TS5 已执行但未通过真实内容门、未 seal、未关闭**；**TS6 / TS7A / TS7B、R3 与正式 P4 Writer 均尚未开始**；
> TS4 的 span **不**替代 TS5 的 `TableObject`，表格结构化能力**尚未**通过验收，真实报告**尚未**生成；
> 本文件其余章节的阶段划分与停止条件不变。
> 本文件的**历史实测数字一律不改写**；新记录只追加。
>
> **批次口径（2026-09-17）**：唯一权威批次表是 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` §9 的
> TS0 / TS1 公共 schema/types/invariants / TS2 PageLayout / TS3 DocumentOutline + 正式 aligner /
> TS4 OutlineSpan + synopsis / TS5 TableObject / TS6 AspectNavigationProfile /
> TS7A 跨树聚合与材料库样板 / TS7B Retriever/ToolRegistry/Store/Pack/P4 provenance 正式接线。
> 本文件 §13.2 的分批表与之一致；与之冲突的历史描述以 §9 为准。
> **整体树门的关闭同时要求 TS7A 与 TS7B 完成。**
> 两个落在表格内部的段落级小标题（`（1）利息的支付` / `（2）本金的兑付`）是 **TS5** 的
> **材料保留验收项**，在 TS3 内**不得**被回收进 `DocumentOutline`。
>
> 本文件自身是树结构调整的计划资产；本轮由 Codex 编制 §十九，并最小同步
> `DESIGN_V2.md`、两份上位任务书、`V2_IMPLEMENTATION_PLAN.md` 与 `V2_TODO.md` 的阶段状态；不修改
> `.gitignore`、数据库、运行时代码、测试或生成产物。

### TS5 执行停止记录（2026-09-20，追加）

- **正式真实 run：** `evaluation/results/tree_table_ts5_20260919T195556Z/`；历史 run、fixture、trust roots 与人工模板原样保留。
- **机器结论：** `machine_gates_passed=false`，`review_decision=needs_changes`；三份真实文档因 `final_conservation_ineligible` 拒绝签发，仅 non-300750 fixture 签发。
- **内容结论：** 固定真实样本 10 项仅 fixture 通过，其余 9 项未取得合格对象/关系/typed gap；三份真实文档仍存在 9440 / 8799 / 2563 个 residual 字符，并有截断、错列、散文误表等人读质量问题。
- **人工结论：** 20 项 reviewer verdict 均为 `not_reviewed`，无 attestation、无 seal、无 TS5 关闭。
- **测试事实：** 完整 eval 全绿只证明机制回归，不覆盖上述内容门失败；不得据此宣布 TS5 或树门通过。
- **后续边界：** 2026-09-30 前不继续无界修表。M930 可把该 no-go 与 gap 作为可审计状态展示，但未签发 `TableObject` 不得进入正式 Pack、Claim 或正文。TS5 的正式返修与关闭留在里程碑后的树主线。
- **当前入口（2026-09-21 更新）：** M930-3 未关闭；下一步唯一是新写作主链的 file-level changelist 与全仓影响清点，范围与本文件顶部一致。保护当前未提交实现与生成结果，不 reset、stash、clean、restore 或混入文档提交；历史实测数字与 TS5 失败事实不改。

### TS2 关闭记录（2026-09-17，追加）

| 项 | 结论 |
|---|---|
| 批次状态 | **TS2 已完成并通过**（真实电子 PDF → 确定性 `PageLayout` + 全量对齐分布诊断） |
| 阈值 | **`ALIGN_MIN = 0.90`**（**用户 + Codex** 于 2026-09-17 批准冻结；**单一**文本对齐阈值，**不设**正文页/表格页两套阈值） |
| 对齐器版本 | `ALIGNER_VERSION = al-2`（`ALIGN_SCHEMA_VERSION als-1`、`NORMALIZATION_VERSION norm-1` 未变） |
| 可引用规则 | 只有 `aligned`（`coverage >= 0.90` **且**无 `unexplained`）可引用；`partially_aligned` / `unaligned` 一律不可引用 |
| 残差放行 | `width_fold`（事后全页搜索式消除残差）**已退出 residual 放行路径**；`ENGINE_ARTIFACT_RULES` 仅保留 `blank` / `invisible_codepoint` |
| `column_reorder` | 属"残差已被结构性解释"的类别，**不单独决定引用资格**；表格重排由**未来的 `TableObject` 路径**承接 |
| 真实样本结果 | 三份真实电子 PDF、769 块：`aligned 522` / `partially_aligned 70` / `unaligned 177`；可引用面 **522** |
| 产物 | `evaluation/results/tree_structure_ts2_layout_ts2_*`（历史产物全部保留、不覆盖） |
| 下一阶段 | **TS3**（`outline_builder.py` / `aligner.py`）；本记录**不代表** TS3 已开始 |

### TS5 后续验收项登记（2026-09-17 TS3 轮追加；TS3 已于 2026-09-18 关闭）

> 本节只登记**真实样本缺口与后续批次的验收要求**，不改冻结 Contract / SourcePolicy / WritingSpec /
> PresentationProfile。本节不是关闭依据：TS3 的关闭依据是用户 + Codex 于 2026-09-18 的独立验收。

| 项 | 内容 |
|---|---|
| 样本 | `NDSD_KCZ_2026` 物理第 20 页，两个**表格单元格内部**的段落级小标题：`（1）利息的支付`（span `os-083281dad26adbb5`，行 9）与 `（2）本金的兑付`（span `os-2d7ceaea5a8d55a4`，行 16） |
| 现状（TS3） | 两者均为 `formal_unassigned`，`unassigned_reason = hierarchy_conflict`，`char_range [0, 8]`（`（1）利息的支付`）等，**原文与 `layout_line_refs` 位置完整保留**；候选审计为 `table_scope = inside_table`、`soft_reasons = table_region_candidate`、`primary_evidence = -`、`source_landing =`（空）——**无对象级已验证 landing**，按本轮 `inside_table` 收紧规则**不得穿透**，因此**留在 `formal_unassigned` 是诚实缺口，TS3 不以几何规则强行恢复** |
| 缺口定性 | 这两个样本属于**"表格单元格内部具有段落级小标题"**的**真实样本**（不是误判、不是噪声、不是阈值问题） |
| TS5 验收要求 | ① `TableObject` 保留**完整**单元格文字；② 能**表示或显化**单元格**内部**的小标题/段落结构；③ 即使它们**未进入 `DocumentOutline`**，也**不得丢失、截断或不可检索**；④ TS5 真实验收**必须**检查这两个样本的**材料保留与可定位性**（不是只检查表格几何判据） |
| 边界 | 本登记**不**授权在 TS3 内实现完整 TS5 `TableObject`；TS3 中这两项**保持 `formal_unassigned`** |

### TS3 实施记录（2026-09-17 落地 / 2026-09-18 关闭）

> 本节只登记**已落地的文件与版本**，便于独立验收方定位；不改写本计划的历史实测数字。
> §13.1 的测试模块表是**计划**用名，实际落地名以下表为准。

| 项 | 内容 |
|---|---|
| 生产文件 | `document_structure/outline_builder.py`（新增）、`document_structure/aligner.py`（新增）、`document_structure/{__init__,schema,versions}.py`（改动） |
| 版本 | `OUTLINE_ALGORITHM_VERSION` / `OUTLINE_SCHEMA_VERSION` 见 `versions.py`；标题资格 profile `hq-4`（`hq-3` 及更早登记为 legacy，须显式识别并重算）；表格区域资格 `trg-3`（本轮语义未变）；`ALIGNER_VERSION al-3`、`ALIGN_MIN 0.90` 未变 |
| 实际测试模块 | `test_tree_outline_builder.py`、`test_tree_outline_hierarchy.py`、`test_tree_heading_qualification.py`、`test_tree_aligner.py`、`test_tree_alignment_partition.py`、`test_tree_structure_artifacts.py`、`test_tree_outline_baseline.py`（冻结基线）、`test_tree_structure_{schema,focused,adversarial}.py`、`test_tree_page_layout.py` |
| 验收脚本 | `evaluation/run_tree_outline_acceptance.py`（只读，产出 hq-4/trg-3 真实产物） |
| 当时状态 | **TS3 已完成并通过（用户 + Codex 独立验收 2026-09-18）**；该行记录 TS3 关闭时快照，后续实际状态见文件顶部 TS4 关闭记录与 §十九 |
| 关闭轮产物 | `evaluation/results/tree_structure_ts3_outline_ts3_outline_tocr2_closure_p2final_20260918T130000Z`（本地未跟踪产物；顶层 manifest 与逐份 `structural_review_findings.json` 的 `tocr-2` 版本与阻断桶逐项一致，`toc_body_unassigned = 0`，769 条守恒与 TS2 冻结等价性未变） |

### TS4 关闭记录（2026-09-19，追加）

> 本节只记录**已实际发生的验收与提交**，不改写本计划任何历史实测数字，也不代表 TS5 已开始。
> 关闭依据是**用户 + Codex 于 2026-09-19 的独立验收裁决**，不是本节的自我认定。

| 项 | 结论 |
|---|---|
| 批次状态 | **TS4 已完成并通过**（TS4-A 于 2026-09-18、TS4-B 于 2026-09-19 分别由用户 + Codex 独立验收通过）；TS4 整体关闭 |
| TS4-A（保留） | 作为**分布观察阶段**保留：`stage = distribution_only`、`SPAN_CONFIDENCE_MIN = None`；原子 commit `468f431`；产物 `evaluation/results/tree_span_ts4_ts4a_sb7_20260918T200000Z/` |
| TS4-B | 人工裁决与策略冻结已完成：`stage = threshold_enabled`、`completion_enabled = true` |
| 冻结阈值 | **`SPAN_CONFIDENCE_MIN = 0.85`**——**必要条件**，不使任何 span / topic / aspect 自动完成 |
| 因子表 | **12 个资格因子**及已批准权重全部冻结（左：`preceding_heading 1.0` / `resume_after_empty 0.9` / `resume_after_table_inside 0.85` / `resume_after_table_adjacency 0.75` / `resume_after_non_content 0.75`；右：`next_heading 1.0` / `document_end 0.9` / `empty 0.9` / `table_inside 0.85` / `table_adjacency 0.75` / `formal_unassigned 0.75` / `non_content 0.75`） |
| 策略资产与注册表绑定 | `document_structure/policies/span_qualification_approval_v1.json`（file sha256 `cd4139c4b87cf196b134944468b75c17709556c8b349b8c4335f3182b2b7400e`）与 `span_qualification_frozen_v1.json`（file sha256 `1b2bf17b8b9672c25e6de2075d61f5512eef26fa112bdb9045b18da8db9cd898`，`policy_fingerprint 91039f649eaee551b966ac313780c0823911377b65d4828071d901808cb5819f`）已入库，`registry_v1.json`（file sha256 `c1b118ee2f93fc40fa49be456f091e72bedcc61ceb444817ee4c7ee7cfd926ae`）绑定其指纹；绑定关系已通过验收 |
| A 阶段封存身份 | sealed attestation identity `3af34696a8c9e06f12ae76acaec96d57287aadcd6886d3aaeec0ce1586953b78`（来源 A run `tree_span_ts4_ts4a_sb7_20260918T200000Z`）；TS4-B 只**引用**该身份，**不进行第二次 seal** |
| 真实产物 | `evaluation/results/tree_span_ts4_ts4b_20260918T174908Z/`（本地未跟踪产物，**不进 Git**） |
| 最终统计 | 991 spans / 4410 dispositions / 68225 components / 991 coverages / 1517 synopses；`conservation_gap_count = 0` |
| 分数分布 | `1.00: 713` / `0.90: 3` / `0.85: 79` / `0.75: 196`（0.75 保留但不可升级为完成） |
| 完整 eval | `python -m evals.run_evals` **14270 passed / 0 failed / 4 skipped** |
| 提交 | TS4-B 原子 policy/test commit `9537695`（24 文件）；状态文档按提交纪律另交（本节所在的文档提交紧随其后） |
| 当时的下一批次 | **TS5（`TableObject`）权威实施计划已写入 §十九并于 2026-09-19 获用户批准；此处保留 TS4 关闭时的历史快照，当前结果见顶部“TS5 执行停止记录”** |
| 仍受门控制 | Phase 5、Phase 6 与 P3R/P4R 后续运行时接线仍由既有阶段门控制，本轮未进入 |

**关于 `d117ac0` 的定性（本计划全文适用）**：

> `d117ac0` 是**包含文档、运行时代码、测试、临时诊断脚本和大量生成结果的混合工作快照**；
> **不得**将其视为已按职责独立批准的文档提交，**也不得**视为干净的 R2 基线。
> 本轮不改写该提交、不清理该提交；**后续提交必须继续精确 staging**（逐文件 `git add`，
> 禁止 `git add .`／`-A`，并在提交前核对 `git diff --cached --name-only` 与预期清单完全一致）。

**关于本轮的阶段定名**：本计划的正式阶段代号为 **TS1–TS5 / TS6 / TS7A / TS7B / R3 / P4-R5**
（见 §十三）。此前草案中的 "TS7" 已被拆分，不再单独使用。

---

## 〇、本轮实测基线（计划的事实前提）

以下事实由本轮只读实测获得，**其中 F4 与 §3 的设计直接冲突，是本计划最重要的输入**。
凡与设计文档的乐观假设不一致处，以实测为准。

| 编号 | 事实 | 证据/命令 | 对设计的影响 |
|---|---|---|---|
| F1 | 三份真实电子 PDF：`NDSD_2024_year.pdf` 229 页、`NDSD_2025_year.pdf` 232 页、`NDSD_KCZ_2026.pdf` 141 页 | PyMuPDF `page_count` | 与 Evidence DB 中 `documents.page_count` 完全一致 |
| F2 | **书签数 0 / 0 / 96**：两份年报没有任何 PDF outline，只有可转债募集说明书（KCZ）有 96 条 | `doc.get_toc()` | outline 算法**不得以书签为前提**；目录页检测 + 正文标题排版信号是必需路径，书签只能是可选候选源 |
| F3 | 现有 PDF 解析用 **pypdf `extraction_mode="layout"`**；`TextChunk` **无 bbox / 无字体 / 无字号 / 无加粗 / 无行号**（`parsers/pdf_parser.py:17-30`） | 只读代码审计 | PageLayout 必须新建几何抽取层（PyMuPDF/pdfplumber 本地已装），不能复用 `parsers/` |
| F4 | **存量 Evidence 与几何层 PageLayout 的文本对齐远弱于假设**：归一化后"精确包含"仅 **3–7%**；但按顺序连续片段覆盖率 ≥0.90 达 **73% / 90% / 94%**，中位数 **≈0.99**，最小值 **0.412 / 0.551 / 0.879** | 见 §3.1 实测方法 | **禁止把 alignment 定义为文本相等**；必须定义为"顺序覆盖率 + 页眉页脚残留分类"的阈值判据，并对尾部 fail-closed |
| F5 | 存量 Evidence：769 块，单一 evidence set `set-501395a7ad5a`，三份文档均 `status='current'`；`document_version` 与磁盘文件哈希**逐一 MATCH**（`sha256-c15272977147dee7` / `sha256-b4f1713d7b821eb0` / `sha256-2b3a1fb3de97f23c`） | 只读 `file:data/evidence.db?mode=ro` | 对齐有稳定锚点；current/stale 可由指纹判定而非人工 |
| F6 | `evidence/builder.py:98` 把 `evidence_type` **硬编码为 `"paragraph"`**，`structured_payload` 恒为 `None` | 只读代码审计 | `heading`/`table`/`table_row` 三种 Evidence 类型**从未产出**；`TableObject` 必须是**存量 Evidence 之外的新结构**，不能指望 Evidence 侧已有表格 |
| F7 | 现有标题检测**只扫每页前 5 行**、只维护**单个 title+level**、`section_path` 实测**长度 0 或 1**（`evidence/builder.py:73`）；**目录页被整页跳过丢弃**（`parsers/pdf_parser.py:393-396`） | 只读代码审计 | 现状既无层级路径、又无多级标题；标题树是新建，不是改造 |
| F8 | 三份真实 PDF 位于 `data/samples/**`，被 `.gitignore` 忽略 ⇒ **fresh clone 无法复现验收输入** | 只读 `.gitignore` | 验收清单必须由 `manifest.json` 记录**输入文件内容哈希**，否则结论不可复现 |
| F9 | `.gitignore` 已有 4 条 `evaluation/results/` 规则，但 **`evaluation/results/tree_structure_*/` 未被忽略** | 只读 `.gitignore` | 新增 ignore 规则属于 **TS7B** 提交批次的一部分（见 §13/§15） |
| F10 | **两个 `TopicResearchPack` 定义并存**：`harness/topic_schema.py:3082`（自述"P3 正式交付物"，有 `schema_version`/`process_status`/`coverage_status`/`dependency_fingerprint`）与 `sections/topic_research.py:224`（无 schema_version、无双轴状态、有 `funnel`/`matrix`/`adopted_fact_ids()`） | 只读代码 | 树结构只绑定 `harness/topic_schema.py` 的 Pack；`sections/topic_research.py` 是 CLAUDE.md 禁止启用的"第二套研究运行时"，**不得扩展** |
| F11 | `tools/contracts.py`：`ToolResult.evidence_ids: list[str]`、`data: dict`、`structured_result_refs`、`external_snapshot_ids`；`retrieval/retriever_v2.py:576` `retrieve(...) -> S.EvidencePack`，`ev_db` 非 None 走**只读发现路径** | 只读代码 | 集成点已存在：沿用 `EvidencePack` 返回契约，**新增** span/table 引用，禁止把 span 塞进 `evidence_ids` 冒充 Evidence |
| F12 | 现有 CLI 惯例：`def _main(argv) -> int` + argparse（如 `retrieval/retriever_v2.py:592`，`prog="python -m retrieval.retriever_v2"`）；其 CLI **会调用 `estore.init_db()`** | 只读代码 | 新 CLI 沿用 `_main(argv)->int` 惯例，但 `--validate-only` **禁止**调用任何 `init_db()` |
| F13 | `document_structure/` 包**不存在**；`PageLayout`/`DocumentOutline`/`OutlineSpan`/`TableObject` 符号在 `.py` 中**零实现**，仅出现在规划文档与 2 处字符串引用 | Glob + Grep | 上述四类符号为新增；但结构判定原语**已有存量实现**，见 F14/F15 |
| F14 | **`harness/heading_structure.py`（P1-A.3）已实现确定性、公司无关、可版本化的标题层级原语**：`numbering_level` / `leading_heading_level` / `iter_heading_spans`，层级序 `第N节章=1 / 一、=2 / （一）=3 / 1、=4 / （1）=5`，并自述"只做结构判定，不做语义判定" | 只读代码 | 本计划**不得**另写一套编号→层级规则；§2.2 的 S3 与 §5 的通用规则必须建立在其上 |
| F15 | **`harness/table_structure.py`（P1-B.3）已实现复合结构信号的表格起点原语** `detect_table_start_flags`（表题形态 + 前驱合法 + 结构跟随 + 无中介表题四项同时成立），且 `TABLE_TITLE_KEYWORDS` 已明确标注**"已废弃"** | 只读代码 | 本计划**不得**用关键词判表；§2.4 必须在既有原语上加几何，且不得两处各写一套规则（该模块自述禁止） |
| F16 | `MaterialPayloadRef.object_type` 已支持 `evidence_span/table_context`；但 `TopicMaterialPayloadResolver._validate_envelope` 在 `locator.offset` 非空时只绑定完整 Evidence 身份，**不会**从真实 block 独立重算片段正文 | 只读代码 | 无需新增对象类型；TS7B 仍必须增加 verified-final-object slice verifier，现 resolver 单独不足以授权 tree fragment |
| F17 | `compute_dependency_fingerprint(contract_fingerprint, source_policy_version, dependency_versions)`，`dependency_versions` 受 `DEPENDENCY_VERSION_KEYS` 约束；`verify_dependency_fingerprint` 用**严格相等**判定 | 只读代码 | §12.4 的扩展必须落在 `dependency_versions` 的**键**上，旧指纹因缺键自然判为 stale |
| F18 | Pack 的 `current` 是 `topic_current` 表的指针；**`stale` 不是列**，而是 `topic_event` 的**追加式事件**（`EVENT_TYPES` 含 `stale`/`invalidated`/`quarantined`）；`load_current_pack` 在有终态失效时返回 `CurrentPackLoad(pack=None, reason=...)` | 只读代码 | §12.5 必须按"指针 + 事件"表述，**不得**假设存在 status 列 |
| F19 | **`NarrativeParagraph` 在 `.py` 中零实现**（仅存在于设计 Markdown）；P4 实际类型是 `harness/schema.py` 的 `CitationRef`/`Claim`/`ResearchAnswer`、`sections/schema.py` 的 `SectionClaim`、`sections/chapter_writer.py` 的 `SentenceDraft`/`ParagraphDraft`/`TableDraft`/`ChapterDraft`；渲染器**已存在**（函数式，`PURE_RENDER_CANDIDATE = True`） | 只读代码 | §10 必须按**实际类型**描述追溯链；不得假设 `NarrativeParagraph` 可用 |
| F20 | `commit_pack(..., set_completeness_verifier, set_enumeration_verifier)` 是唯一提交入口，内部有 `_validate_set_completeness`；R2 依赖装配为 `build_r2_material_dependencies()` → `FormalSetEnumerationVerifier`（`harness/set_enumeration.py`） | 只读代码 | §8 的"fallback 不得单独证明 `set_complete`"有**确定的强制挂点**，无需新造机制 |
| F21 | **本计划草案的能力缺口（本轮修订要补的核心）**：草案只覆盖"建树（TS1–TS5）—导航 profile（TS6）—检索集成—Pack 兼容"，**遗漏了"多个树节点如何形成完整主题材料库"**，即跨树／跨文档／跨年份的 aspect 材料聚合 | 对草案的自审 + Codex 独立审查结论 | 必须新增独立章节（§7）与 TS7A 批次；**不得把跨树聚合推迟到 R3 才首次设计或首次验证** |
| F22 | `harness/heading_structure.py` 与 `harness/table_structure.py` 是 R2 已在用的**结构真相来源**（`source_object_inventory` 与 `set_enumeration.recover_flattened_tables` 共用其判定原语） | 只读代码 | 本轮**不迁移**这两个模块（已裁决，见 §16-6）；结构规则**只能有一处真相** |

---

## 一、公共类型、身份与版本（对应 §六(1)）

### 1.0 通用约定

- 全部为 `@dataclass(frozen=True)`，带 `schema_version`、`to_dict()`、`from_dict()`，
  并在 `__post_init__` 抛 `SchemaValidationError` 校验不变量 —— 沿用 `harness/topic_schema.py` 的既有惯例。
- 身份（id）**一律是内容/推导的函数**，不使用随机数、时间戳、自增计数、字典遍历序。
- 浮点一律 `round(x, 3)` 后才参与哈希与持久化，保证跨平台确定性。
- 每个 id 形如 `<prefix>-<sha256(canonical_json)[:16]>`，与 `evals`/`evidence` 现有 `set-<12hex>` 风格一致。
- **每个类型都携带其推导来源的版本**，用于 current/stale 判定（§12.5）。

### 1.1 常量（新增 `document_structure/versions.py`）

```
LAYOUT_SCHEMA_VERSION      = "pl-1"
LAYOUT_ENGINE              = "pymupdf"
NORMALIZATION_VERSION      = "norm-1"     # 定义空白/连字符/全角半角归一规则
OUTLINE_SCHEMA_VERSION     = "do-1"
OUTLINE_ALGORITHM_VERSION  = "oa-1"
SPAN_SCHEMA_VERSION        = "os-1"
SPAN_BUILDER_VERSION       = "sb-1"
TABLE_SCHEMA_VERSION       = "to-1"
TABLE_BUILDER_VERSION      = "tb-1"
SYNOPSIS_VERSION           = "ns-1"
PROFILE_RULE_VERSION       = "anp-1"      # 公司无关通用规则文件的版本
ALIGNER_VERSION            = "al-1"
```

### 1.2 逐类型定义

| 类型 | 身份（id 输入） | 关键字段 | 不变量（fail-closed） |
|---|---|---|---|
| **PageLayout** | `pl-<h(document_version, LAYOUT_ENGINE, engine_version, LAYOUT_SCHEMA_VERSION, NORMALIZATION_VERSION)>` | `document_id`、`document_version`、`company_id`、`page_count`、`pages[]`、`engine`、`engine_version`、`normalization_version`、`schema_version`、`source_file_sha256` | `len(pages)==page_count`；页序严格递增且从 1 开始；每页 `width/height>0`；`source_file_sha256` 必填 |
| **LayoutPage** | 页内无独立 id，以 `(page_layout_id, page_number)` 寻址 | `page_number`、`width`、`height`、`rotation`、`lines[]`、`has_text_layer` | `rotation ∈ {0,90,180,270}`；`lines` 按 `reading_order` 严格递增 |
| **LayoutLine** | `(page_layout_id, page_number, line_index)` | `line_index`、`bbox[x0,y0,x1,y1]`、`spans[]`、`text`、`is_furniture`、`furniture_kind`、`reading_order`、`column_index` | `x1>x0 and y1>y0`；`spans` 非空；`is_furniture=True` 时 `furniture_kind != null` |
| **LayoutSpan（排版片段，非 OutlineSpan）** | `(page_layout_id, page_number, line_index, span_index)` | `text`、`bbox`、`font`、`size`、`is_bold`、`char_start`、`char_end` | `char_end>char_start`；`size>0` |
| **DocumentOutline** | `do-<h(page_layout_id, OUTLINE_ALGORITHM_VERSION, OUTLINE_SCHEMA_VERSION)>` | `nodes[]`、`edges[]`、`unassigned[]`、`candidate_sources[]`、`schema_version` | `nodes` 按文档顺序；`node_id` 全局唯一；每个 `parent_id` 必须存在于 `nodes` 或为 `null`；无环 |
| **OutlineNode** | `on-<h(document_outline_id, structural_path, source_anchor)>` | `node_id`、`parent_id`、`level`、`title`、`title_normalized`、`structural_path[]`、`source_anchor`、`child_ids[]`、`ordinal` | **同名标题必须以完整路径 + 源位置区分**：`structural_path` 含全部祖先标题，`source_anchor` 含 `(page_number, line_index, bbox)`；`level>=0`；`title` 非空且 ≤60 字；`structural_path[-1]==title_normalized` |
| **NavigationSynopsis** | `ns-<h(node_id, SYNOPSIS_VERSION, source_span_ids)>` | `node_id`、`status ∈ {available, synopsis_unavailable}`、`reason_code`、**`snippets[]`**、`source_span_ids[]` | `status=="available"` ⇒ `snippets` 非空，且**每个 snippet** 都是**单个来源 span** 的连续原文片段、带独立 `span_id / char_start / char_end` 并可逐字符回溯；`status=="synopsis_unavailable"` ⇒ `snippets == []` 且 `reason_code != null`（§4） |
| **SynopsisSnippet** | `(synopsis_id, snippet_index)` | `span_id`、`char_start`、`char_end`、`text` | `char_end > char_start`；坐标域是 span 的 `normalized_text`，`text` **必须**逐字符等于 `normalized_text[char_start:char_end]`；底层原文由 component/Evidence 定位回查，**不得**跨 span 拼接（§4） |
| **AspectNavigationProfile** | `anp-<h(contract_version, contract_fingerprint, PROFILE_RULE_VERSION)>` | `profile_id`、`contract_version`、`contract_fingerprint`、`rule_version`、`entries[]`、`schema_version` | `entries` 对 Contract v2 每条 aspect 恰好一条；条目内**禁止**出现公司名/股票代码/固定页码/表号/gold 词（§5 校验器强制） |
| **AspectNavigationEntry** | `(profile_id, aspect_id)` | `aspect_id`、`question_id`、`topic_id`、`content_role`、`display_tier`、`nav_keys[]`、`expected_forms[]`、`derivation[]` | `derivation` 必须列明来自哪条 Contract 字段与哪条通用规则，禁止空 |
| **TextAlignmentRecord** | `al-<h(page_layout_id, evidence_set_version, ALIGNER_VERSION, page_number, block_index)>` | `verdict ∈ {aligned, partially_aligned, unaligned}`、`coverage`、`evidence_block_ref`、`span_ref`、`char_map[]`、`residue[]`、`residue_class` | `verdict=="aligned"` ⇒ `coverage>=ALIGN_MIN` 且 `residue` 全部已分类；**`unaligned` 不得被下游当作可引证材料**（§3） |
| **OutlineSpan** | `os-<h(document_outline_id, evidence_set_version, start_anchor, end_anchor, 对象实际 span_builder_version)>` | `span_id`、`node_id`、`document_id`、`document_version`、`evidence_set_version`、`page_range`、`char_range`、`layout_line_refs[]`、`evidence_block_refs[]`、`is_fallback`、`fallback_derivation`、`is_cross_heading`、`confidence` | 按源顺序的精确 locator **必须能回读其覆盖原文**；`normalized_text` 只是确定性规范化重构，不以字符串拼接冒充原文无损；`is_fallback=False` ⇒ `fallback_derivation is None`；`is_fallback=True` ⇒ `fallback_derivation != None` 且**不得单独支撑 `set_complete`**（§7） |
| **TableObject** | `to-<h(page_layout_id, page_number, table_index_on_page, table_bbox, TABLE_BUILDER_VERSION)>` | `table_id`、`page_range`、`title`、`title_source`、`unit`、**`structure_class`**、`header_rows[]`、`body_rows[]`、`total_rows[]`、`continuation_of`、`continuation_ids[]`、`cell_grid[]`、`structure_evidence` | **没有真实结构信号时不得把普通段落伪装为 table/table_row**：`structure_evidence` 必须给出列边界一致性证据，否则该对象**不得创建**；`structure_class ∈ {financial_main_statement, note_table, ordinary_business_table}` 由所属 outline 节点的章节路径判定（§11），且**只是结构分类，不是 authority verdict** —— 金额权威一律来自 `FinancialSnapshot` / `FinancialFactPack` |
| **ReferenceEdge** | `re-<h(document_outline_id, from_ref, to_ref, edge_kind, resolution_evidence)>` | `edge_id`、`from_ref`、`to_ref`、`edge_kind ∈ {parent_child, cross_reference, table_continuation, toc_to_body}`、`resolution_evidence`、`is_resolved` | **不得用"第一张表/最近页面/自报 target"代替确定性绑定**：`is_resolved=True` ⇒ `resolution_evidence` 必须是确定的文本/位置证据；否则 `is_resolved=False` 且进 `unassigned` |

### 1.3 在 Contract v2 中的位置（对应 §六(1) 的"与冻结 Contract 的关系"）

`AspectNavigationProfile` 是**唯一**读 Contract 的新类型，其输入是冻结的
`templates/contracts/standard_v3.yaml`（`contract_version: v2`、`schema_version: contract-v2`、
声明 `content_sha256: "5c45eabcad4989622aa3d483d9414304b3dbd0a5127db726f513409d14c6b410"`、
`status: frozen`），经 `contracts/loader_v2.py` 读取。
`REQUIRED_ASPECT_FIELDS`（22 项，`contracts/schema_v2.py:200-223`）中的
`requirement_text / topic_id / question_id / kind / content_role / display_tier /
required_fields / coverage_rules / time_scope / impact_scope` 是 profile 派生的**允许输入**；
`standard_v3.yaml` 头部注释明确禁止 `case_id / 300750 / 宁德时代 / 固定页码 / gold / 固定答案关键词`，
且声明该文件**只能**被 `contracts/loader_v2.py`、`contracts/validator_v2.py`、
`review/topic_aspect_evidence_review.py` 与离线测试读取，
**不得接入正式 Router / Harness runtime / Worker / Writer** —— 本计划的 profile 生成属于"离线派生 + 版本化产物"，
消费方只能读取**生成后的 profile JSON**，不得在 runtime 直接读 yaml。

---

## 二、算法（对应 §六(2)）

### 2.1 `build_page_layout(pdf_path, context) -> PageLayout`

1. 只读打开（PyMuPDF），记录 `engine_version`；计算 `source_file_sha256`（全文件）。
2. 逐页读 `page.rect` / `rotation`；`get_text("dict")` → blocks → lines → spans，
   取 `bbox / font / size / flags(加粗位)`。
3. **页眉页脚/页码检测**：对全文件统计归一化行文本的页间出现率与 y 带位置；
   出现率 ≥ 阈值且落在页边距带的行标 `is_furniture=True` + `furniture_kind ∈ {running_header, running_footer, page_number}`。
   ⇒ 该步骤是 §3 对齐的必要条件：F4 已证明两引擎的**残留几乎全部是这类浮动页码/页眉token**，
   必须显式建模并保留（**不是删除**），否则对齐无法解释残差。
4. **阅读顺序**：按 `(y, x)` 排序得基线顺序；做 x 区间聚类检测多栏，
   多栏时按栏内 `(y,x)`、栏间从左到右排列，写 `reading_order` 与 `column_index`。
5. **确定性**：所有坐标 `round(3)`；排序键全序化（`y` 相等时以 `x`、再以 `line_index` 打破平局）；
   禁止依赖 `dict` 迭代序。
6. 不调用 LLM、Router、Contract、网络、数据库 —— 满足 `DESIGN_V2.md` §16.2.1
   "不依赖 LLM、Router 或业务 Contract 才能形成基础标题树"。

### 2.2 `build_document_outline(layout) -> DocumentOutline`

三条独立候选源，**全部只是候选，均不构成事实/引用/覆盖证明**：

- **S1 目录页候选**：找含 `目录|目次` 或 ≥3 行 leader-dot+页码 的页，解析 `(title, declared_page)`。
  注意 F7：现状把目录页整页丢弃（`parsers/pdf_parser.py:393-396`）；新流程**保留**目录页作为候选源，
  但目录项**不得**直接成为 `OutlineNode`。
- **S2 书签候选**：`doc.get_toc()` 有则读。F2 证明 2/3 文档为空 ⇒ **S2 必须是可选的**，
  算法在 S2 为空时行为完全一致，不得降级为"无标题树"。
- **S3 正文标题排版候选（主路径）**：对 `LayoutLine` 打分：
  相对字号秩（对页内众数字号的比值）、`is_bold`、编号前缀、短行（≤60 字）、不以 `。` 结尾、
  不在表格单元内、水平居中程度。
  **编号→层级一律复用 `harness/heading_structure.py` 的 `leading_heading_level` /
  `iter_heading_spans`（F14），不得另写一套**；若其层级序需要扩展，则**就地扩展该模块并升版本**，
  而不是在 `document_structure/` 复制。该模块已有的中文披露编号层级序
  （`第N节章=1 / 一、=2 / （一）=3 / 1、=4 / （1）=5`）是**通用、公司无关、可版本化**的确定性证据，
  正好满足"标题层级与公司词表无关"的要求。
  同时保留 `parsers/pdf_parser.py:49-65` 的 `_SECTION_FALSE_POSITIVE` 守卫
  （`^\d{4}年|^\d{2,4}万|…`）防止"2025年"类误判。
- **合并规则**：S1/S2 只用于 (a) 佐证 S3 已检出的标题、(b) 提供声明页码提示。
  **有目录项但正文无对应标题 ⇒ 记 `toc_only_candidate` 进 `unassigned`，不建节点**（fail-closed）。
- **层级构造**：以编号深度为主、字号秩为并列打破；父节点 = 最近的前序更低 level 标题。
- **同名标题**：身份含**完整 `structural_path` + `source_anchor`**，因此"（一）基本情况"在多个章节下
  得到不同 `node_id` —— 直接满足"同名标题必须以完整路径和源位置区分"。
- **ReferenceEdge 候选**：`toc_to_body`（目录项→正文标题，需确定性文本+页码证据）、
  `parent_child`（结构内）、`cross_reference`（"详见第X节/第X页"类显式互指，须有文本触发词证据）。
  无法确定性绑定的记 `is_resolved=False` 进 `unassigned`，**绝不用最近页面/第一张表代替**。

### 2.3 `build_outline_spans(outline, evidence_set) -> list[OutlineSpan]`

1. 节点正文区 = `[该节点 start_anchor, 下一节点 start_anchor)`，并按子树归属裁剪。
2. **跨标题切分**：一个节点的 span **不得**把其子节点的正文并入自身；遇到子标题即切分，
   并置 `is_cross_heading=True` 记录该 span 跨越了标题边界。
3. 边界只能落在 `LayoutLine` 边界，**不得切在行中**；允许跨页。
4. **无损重构不变式（可直接测试）**：对任一区域，按源顺序拼接其 spans 的文本，
   经 `NORMALIZATION_VERSION` 归一后，必须等于该区域 PageLayout 的归一文本。
   违反 ⇒ 该 span 集合不产出（fail-closed），记入 `unassigned_content.json`。
5. `evidence_block_refs` 由 §3 的 `TextAlignmentRecord` 填充；**可以为空** ——
   此时该 span 只能用于导航，**不可作为引用锚点**。

### 2.4 `build_table_objects(layout, outline, spans) -> list[TableObject]`（历史草案，禁止实施）

> **已由 §十九完整替代。**其中“`detect_table_start_flags` AND 几何网格”的双重必要门与现有
> 显式表号/续表排除语义冲突，会漏真实表；TS5 只能按 §19.5 的“候选并集 → 统一结构硬门”实施。
> 本小节仅保留历史计划事实，不再是可执行规格。

1. **表格起点判定一律复用 `harness/table_structure.py::detect_table_start_flags`（F15）**：
   该项已实现"表题形态 + 前驱合法 + 结构跟随 + 无中介表题"四项复合信号，
   且已明确**废弃**关键词判表。`document_structure/` **只在其上增加几何层**（列边界/单元格网格/
   跨页续表），**不得**再写一套"表格起点"规则 —— 该模块自述：两处各写一套会使同一源文本派生
   两套恢复真相。若需修改判定规则，**就地修改并升版本**，同时跑该模块既有回归。
2. 几何候选来自 pdfplumber `find_tables()`（复用 `evidence/table_probe.py` 的思路，
   实现在 `document_structure/table_builder.py`，**不修改** `table_probe.py`）。
3. **结构信号门（双重）**：既有原语判定为表起点 **且** 几何网格满足 ≥2 行 × ≥2 列、列边界在行间一致；
   任一不满足 ⇒ **不创建 TableObject**，内容留在 span 里
   （满足"不得把普通段落伪装为结构化 table/table_row"）。
4. 标题取最近的前序标题/题注行；`unit` 由题注或邻近行的 `单位：X` 模式抽取；
   取不到则 `unit=None` 并记 reason，**不得猜**。
5. 表头行 = 首个表头块；合计行由 `合计/总计/小计` 前导词识别；续表由表头签名匹配跨页关联，
   `continuation_of` 指向首表，`continuation_ids` 反向持有，并以
   `ReferenceEdge(edge_kind="table_continuation")` 连接。
6. `structure_class` 由**所属 outline 节点的章节路径**判定（§11），**禁止**用页码/表号硬编码；
   该字段**只表达结构分类与绑定提示，不裁决金额权威** —— 财务主表金额一律取自
   `FinancialSnapshot` / `FinancialFactPack`（§11.1）。

### 2.5 确定性总要求

同一输入、同一版本常量，连续构建两次必须得到**逐字节相同**的 JSON 与相同 id 集合；
`self_check` 内置该断言（§11.2）。

---

## 三、PageLayout 与存量 Evidence 的可验证对齐（对应 §六(3)）

### 3.1 本轮实测（F4 的来源）

方法（只读，不改任何文件）：取三份文档的存量 `evidence_blocks` 每 10 块抽样；
`tight(s) = ''.join(s.split())` 做全空白归一；用 `difflib.SequenceMatcher`（`autojunk=False`）
计算块文本在**同页** PageLayout 文本中的**顺序连续片段覆盖率**
`coverage = Σmatching_block.size / len(block_text)`。实测：

| 文档 | 样本 | 精确包含（`tight(block) in tight(page)`） | coverage ≥0.98 | coverage ≥0.90 | 中位数 | 最小值 |
|---|---|---|---|---|---|---|
| NDSD_2025_year | 30 | —— | **53.3%** | **73.3%** | 0.987 | **0.412** |
| NDSD_2024_year | 30 | —— | **60.0%** | **90.0%** | 0.993 | **0.551** |
| NDSD_KCZ_2026 | 18 | —— | **83.3%** | **94.4%** | 0.996 | **0.879** |
| （第二轮，25 块样本） | 25 | **仅 1/25 精确；3/25 去空白精确** | | | | |

**个案诊断（决定性）**：2025 年报第 13 页 block 0，长度 192，coverage **0.99**，
单一匹配片段 `(block_pos=0, page_pos=28, size=190)`；唯一残差是浮动页码 token `13`
被两引擎放在不同位置。第 22 页首块同样为页内偏移 28 起、长 929 的**单一连续片段**，
残差是 PyMuPDF 保留了 `…2025年年度报告全文22` 的**running header**，
而 `parsers/pdf_parser.py` 的 `_detect_repeated_lines`/`_strip_lines` 已将其剥离。

**结论**：两引擎**内容一致、序列基本一致**，差异集中在
(a) 页眉/页脚/页码等**浮动家具 token** 的归属与位置，(b) 少数表格页的列序。
因此：

- ❌ **不可**把对齐定义为"文本相等"或"去空白后精确包含" —— 实测会失败 93–97%，整个树结构将无法与存量 Evidence 建立任何绑定。
- ✅ **必须**定义为"**顺序覆盖率 ≥ 阈值 + 残差分类通过**"，并把浮动家具**显式建模**（§2.1 第 3 步）。
- ⚠️ 尾部（min 0.412 / 0.551）**尚未完成归因**：因此**阈值不得在归因前写死**（见 §3.4 TS 批次）。

### 3.2 对齐判据（确定性、可验证）

对每个 `(page_layout, evidence_set_version, page, block)`：

1. 双侧 `tight()` 归一（复用 `evidence/ids.py:47-49 _normalize_text` 的**语义**，
   但树结构侧独立实现同一规则并版本化为 `NORMALIZATION_VERSION`，避免耦合改动存量哈希）。
2. 计算 `coverage`（顺序连续片段覆盖率）与 `residue`（未匹配字符及其页内位置）。
3. 残差分类 `residue_class`：
   - `page_furniture`：残差落在 `is_furniture=True` 的行内；
   - `engine_artifact`：残差为字符级差异（全半角/连字符/空格/软连字符）；
   - `column_reorder`：残差可由同页多栏重排解释；
   - `unexplained`：以上皆不能解释。
4. `verdict`：`coverage ≥ ALIGN_MIN` **且** 无 `unexplained` ⇒ `aligned`；
   `coverage ≥ ALIGN_MIN` 但有 `unexplained` ⇒ `partially_aligned`；
   否则 `unaligned`。
5. `char_map` 记录 block 字符 → PageLayout `(page, line_index, span_index, char_offset)` 的逐段映射，
   使"可回溯"可被测试。

### 3.3 fail-closed 与"追加而非覆盖"

- `unaligned` / `partially_aligned` 的块**不得**被下游当作可引证材料（§1.2 不变量）。
- 若某一文档版本的对齐失败率超过审批阈值，**允许为同一文档版本追加新的 `evidence_set_version`**
  （由新的 `NORMALIZATION_VERSION`/`BUILDER_VERSION` 派生，形如 `set-<sha256[:12]>`，
  复用 `evidence/ids.py:37-44 derive_evidence_set_version` 的现有机制），
  **禁止覆盖、禁止删除、禁止就地改写历史 Evidence**；
  历史 set 保持 `status` 语义不变，新 set 记 `current`，二者可并存对照。
- `document_version` 不变（文件哈希未变，F5 已证明三者 MATCH）—— **这正是"新增而非覆盖"的可行性基础**：
  对齐失败是**解析层版本**问题，不是**源文件**问题。

### 3.4 阈值 `ALIGN_MIN` 的确定程序（**不得跳过**）

`ALIGN_MIN` 在 TS2 批次由**归因结果**决定，而不是先验选定：
1. 对三份文档做**全量**（非抽样）coverage 分布统计；
2. 对 `coverage < 0.90` 的块逐类归因，直到 `unexplained` 占比可判定；
3. 取"使 `unexplained` 归零或最小"的最低阈值作为 `ALIGN_MIN` 候选，附分布证据提交审批；
4. 审批前所有下游一律按 fail-closed 处理。

---

## 四、逐可导航节点的抽取式可回溯简介（对应 §六(4)）

**简介不是一个连续文本片段，而是 snippet 列表。** 一个节点可能由其下多个不相邻的段落
（例如被表格隔断的正文）共同说明，因此 `NavigationSynopsis.snippets[]` 是正常工作形态。

- **生成**：`snippets` 中**每个 snippet 必须是单个来源 span 的连续原文片段**，
  携带独立 `span_id / char_start / char_end / text`，
  且 `text` 必须与源 span 的 `normalized_text[char_start:char_end]` **逐字符相等**；底层原文由
  component/Evidence 坐标回查，不得把 snippet 坐标误当 Evidence 原域。
  多个 snippets 可按源顺序组合成供人阅读的导航简介，
  但**组合只发生在展示层**；数据层**禁止**跨 span 拼接成一个字符串。
- **禁止**：任何生成式摘要、同义改写、无来源拼接、跨 span 内容融合。
- **版本化确定性**：`SYNOPSIS_VERSION` + id 含 `source_span_ids` ⇒ 原文变则简介必然变，
  不存在"简介与源不一致"的状态。
- **不可用时的记录**：当节点无可用 span、或可用文本为空、或超出长度上限导致
  **无法在不改写的前提下**给出任何片段时，产出 `status="synopsis_unavailable"`
  + `reason_code ∈ {no_span, table_only_pending_ts5, empty_text, alignment_failed, length_exceeded}`
  + `snippets=[]`；该五值集合与 §18.8.3、T4 的封闭词表完全一致。
  **不允许**"省略该节点"或"用空字符串冒充" —— 缺失必须显式可见。
- **边界（强约束）**：简介与标题**只作候选导航**，**永不**是事实、引用或覆盖证明
  （CLAUDE.md 与任务书 §4.4 同一要求）。具体地：
  下游任何 `set_complete`／覆盖／权威判定**不得**读取 `NavigationSynopsis`；
  跨树聚合（§7）中，`snippets` **只可用于候选定位**，
  **不得**参与"材料是否采用""事实是否成立""aspect 是否 covered"的判定。

---

## 五、导航 profile 的确定性派生（对应 §六(5)）

### 5.1 输入（且仅此二者）

1. 冻结 Contract v2（`templates/contracts/standard_v3.yaml`，经 `contracts/loader_v2.py`，只读）；
2. **公司无关、版本化**的通用规则文件 `document_structure/nav_rules/anp-1.yaml`（新增，纯通用语言学/结构规则）。

### 5.2 派生规则（示例，全部通用）

- `content_role` → 期望呈现形态：`paragraph` → 段落型候选；`table` → 表格型候选；
  `paragraph_and_table` → 两者；`risk_note` → 风险提示型；`search_scope_note` → 检索范围说明；`audit_only` → 仅审计。
- `display_tier` → 候选召回预算：`required_body` > `optional_body` > `diagnostic_only`。
- `kind` / `required_fields` → `expected_forms`（如含金额字段 ⇒ 期望表格或带单位的段落）。
- `topic_id` / `question_id` → 同 topic/question 内 aspect 的候选共享（结构内互证，仍只是候选）。
- `requirement_text` → 经通用分词与停用词后生成 `nav_keys`；**得分只用于排序**。

### 5.3 强制禁止（由校验器机器强制，非人工承诺）

`profile` 生成后必须通过 `document_structure/profile_validator.py`：

1. **公司无关性**：对同一 Contract 段、两个不同 `company_id` 生成 profile，
   输出的 `entries` 必须**逐字节相同**（`company_id` 不得进入任何派生输入）。
2. **禁词扫描**：`entries` 中不得出现公司名、股票代码（`\b\d{6}\b`）、固定页码、表号
   （`表\s*\d+` / `Table\s*\d+`）、gold 答案词、调用者临时自报同义词。
3. **无 shadow Contract**：`nav_keys` 必须能由 `derivation[]` 回溯到**具体 Contract 字段 + 具体规则条目**；
   任何不能回溯的条目 ⇒ 校验失败（即禁止按 aspect 手写特例）。
4. **变更只能来自版本**：任何输出变化必须同时体现 `rule_version` 或 `contract_fingerprint` 变化；
   二者都不变而输出变化 ⇒ 校验失败（确定性）。

- 无命中候选的 aspect ⇒ `entries[i].nav_keys = []` 且 `expected_forms` 保留，
  下游按"无导航候选"处理并显式 gap，**不得**反推"无覆盖"。

---

## 六、现有 Retriever / ToolRegistry 的树感知集成（对应 §六(6)）

**结论：不新建第二套运行时。** 在既有契约上做**向后兼容的加字段**扩展。

1. **读路径复用**：`retrieval/retriever_v2.py:576 retrieve(...) -> S.EvidencePack` 已有
   `ev_db` 非 None 的**只读发现路径**（R2 item 五）。树结构侧直接复用该路径读取存量 Evidence，
   **不新增 Evidence 读取实现**。检索返回类型仍是 `EvidencePack`（契约不变）。
2. **引用扩展**：`tools/contracts.py:242 ToolResult` 已有
   `evidence_ids / structured_result_refs / external_snapshot_ids / data`。
   树结构新增 span/table 引用时——
   - **必须**新增独立字段（如 `outline_span_ids` / `table_object_ids`，或复用
     `structured_result_refs` 的既有信封），
   - **绝对禁止**把 `OutlineSpan`/`TableObject` id 塞进 `evidence_ids` ——
     否则 span 会被下游误认为 Evidence 引用锚点，违反"formal local material 只能是
     `OutlineSpan`/`TableObject`，而非 whole Evidence 的替代"这一区分。
3. **索引层的加字段**：`retrieval/indexer_v2.py` / `sparse.py` 的既有索引记录
   增加 `outline_node_id / outline_span_id / table_object_id / structural_path / page_range` 元数据，
   索引**一次**、字段可空（旧索引仍可读）。`OutlineSpan`/`TableObject` 的向量化可与 Evidence 共库，
   但**通过元数据区分**，不混身份。
4. **ToolRegistry**：`tools/registry.py:54 ToolRegistry` + `ToolSpec`（`tools/contracts.py:214`）
   新增工具声明（同一注册表，不新增注册表）：如
   `search_outline_node` / `read_outline_span` / `list_table_objects`，
   `allowed_routes` 与既有路由门控一致，`cost_class` 为本地零成本。
   `ToolResult.data` 承载节点/span/表，`status` 沿用既有枚举（含 `EMPTY`）。
5. **Router 不变**：`routing/` 不因树结构改变；树感知发生在检索与材料装配层。
6. **不启用** `sections/topic_research`（F10）——它是 CLAUDE.md 明令禁止的第二套研究运行时。
7. **不复制结构规则**：编号→层级一律走 `harness/heading_structure.py`，表格起点一律走
   `harness/table_structure.py`（F14/F15）。`document_structure/` 是"几何 + 身份 + 持久化 + 对齐"层，
   **不是**第二套结构判定实现 —— 否则同一份源文本会派生两套真相。
8. **不新建 Evidence 读取实现**：复用 `retriever_v2` 的 `ev_db` 只读路径与
   `build_readonly_discovery_registry`（`tools/adapters.py:705`）。

---

## 七、跨树、跨文档 Aspect 材料聚合（对应 §六 补充能力；**本计划草案的缺口，本轮补上**）

**为什么必须独立成章**：TS1–TS6 只解决"单份文档如何建树、以及如何把 aspect 映射到候选节点"，
但**没有回答"一个 aspect 的材料库如何从多个树、多个文档、多个年份中形成"**。
这是 R3 全量调度（115 个 `topic_harness` aspects）与 P4 写作的直接输入，
**不得推迟到 R3 才首次设计或首次验证** —— 基础聚合能力必须在树结构调整阶段完成，
并在真实小样本上通过（TS7A 退出门，见 §13）。

### 7.1 公共类型与入口（名称可微调，**语义不得缺失**）

```text
AspectMaterialAggregationInput      # 输入信封
AspectMaterialOccurrence            # 一个 aspect 材料的一次结构性出现（occurrence）
MaterialDedupCluster                # 精确重复簇
MaterialConflictGroup               # 冲突事实组
AspectMaterialAggregationResult     # 聚合结果（含采用/未采用/审计面）

aggregate_aspect_materials(input: AspectMaterialAggregationInput)
    -> AspectMaterialAggregationResult
```

| 类型 | 身份 | 关键语义 |
|---|---|---|
| `AspectMaterialAggregationInput` | `(aspect_id, aggregation_version, 输入文档集指纹)` | 一次聚合的完整输入；**只读**，不写任何库 |
| `AspectMaterialOccurrence` | `occ-<h(document_outline_id, node_id, span_id \| table_id, aspect_id)>` | 材料的**一次出现**：来源文档 + 版本 + 年份 + 结构路径 + 页码 + span/table 身份 + 角色；**identity 不因去重而合并** |
| `MaterialDedupCluster` | `dc-<h(material_content_key)>` | **仅**当 payload 与来源身份**均相同**时才成立；簇内保留**全部** occurrence |
| `MaterialConflictGroup` | `cg-<h(aspect_id, 事实槽位)>` | 同一事实槽位上互不相容的取值；**全部保留**，不做仲裁 |
| `AspectMaterialAggregationResult` | `amr-<h(input 指纹)>` | 采用/未采用/dedup/conflict/未读/fallback/unassigned/资格判定 |

### 7.2 输入的必备字段

- `aspect_id / question_id / topic_id`；
- `AspectNavigationProfile` 中该 aspect 的对应条目（`AspectNavigationEntry`）；
- **多个** `DocumentOutline`（可来自不同文档、不同年份、不同文档版本）；
- 候选 `OutlineNode / OutlineSpan / TableObject`；
- document / version / `as_of` / period / entity / scope / authority 信息；
- `fallback`、`unassigned`、alignment 状态（含 `TextAlignmentRecord.verdict`）；
- **未读范围**与检索审计信息（哪些范围从未被检索/未命中）。

### 7.3 输出的必备内容

- **正式采用**的材料 occurrence；
- **候选但未采用**的材料，及**确定性原因**（如 `duplicate_of` / `superseded_version` / `out_of_scope` / `alignment_failed` / `authority_insufficient`）；
- **精确重复簇**（`MaterialDedupCluster`）；
- 内容相近但**时期 / 主体 / 口径 / 文档版本不同**的并列材料（**不得合并**）；
- **冲突事实组**（`MaterialConflictGroup`）；
- `unread` / `unassigned` / `fallback-only` / `alignment-failed` 四类如实清单；
- 每项材料的来源、结构路径、页码、span/table 身份；
- 每项材料的**是否具备进入 `TopicResearchPack` 的资格**（及不合格原因）。

### 7.4 聚合范围规则（10 条，全部为强制）

1. **子树闭包**：命中父节点时，**不只**读取父节点自身正文；按**版本化规则**计算允许的"子树闭包"
   （默认含其全部后代节点，规则版本化为 `SUBSTREE_CLOSURE_VERSION`），闭包范围必须记录在 occurrence 上。
2. **同文档多命中**：同一 aspect 可以同时命中**同一文档的多个不同子树**，各自成为独立 occurrence，
   不得因为"同一文档只取一次"而丢失去重前的可达性。
3. **跨文档聚合**：可跨年报、募集说明书及其他**本地权威**文档聚合；来源类型不同的材料**并列保留**。
4. **不可相互覆盖**：跨年份、跨主体、跨口径、跨币种、跨期间的材料**不得相互覆盖**，
   必须作为并列 occurrence 存在（期间/口径差异写入 `scope_period_matrix.json`）。
5. **精确去重条件**：**只有 payload 与来源身份均相同**的材料才允许精确去重。
6. **同内容不同来源**：内容相同但**来源、年份或版本不同**，**只能建立重复簇**，
   必须保留各 occurrence 及**各自引用**（引用不得合并成一个）。
7. **冲突全保留**：冲突内容**全部保留**，交由后续事实验证/权威规则裁决；
   **聚合器不得让 AI 自行选一个结论**，也不得按"较新/较长/较权威"的启发式静默择一。
8. **导航信号不作判定**：`NavigationSynopsis` 与标题相似度**只用于候选定位**，
   **不得**参与权威、事实成立或 coverage 判定。
9. **fallback 边界**：fallback 材料可进入**补充材料集合**，但**不得单独证明 `set_complete`**
   （强制挂点同 §8：`commit_pack → _validate_set_completeness`）。
10. **可见性**：未读、未归属、低置信度和无法对齐的内容**必须可见**，
    **不得通过丢弃改善完成率**；其数量与原因进入 `unread_unassigned.json`。

### 7.5 边界声明（必须随结果一同输出）

> **聚合结果本身不等于 aspect 已 covered。**
> **不能仅凭标题、导航简介或相似度证明完整性。**
> `AspectMaterialAggregationResult` 只声明"在这些输入范围内，找到了这些材料、这些未读范围、
> 这些冲突"，**不**声明"这个 aspect 的材料已完整"。
> 完整性与 `set_complete` 仍由事实、引用、authority 与 completion rules 判定（§8、§10）。

---

## 八、fallback 仍须产出精确定位 span，且不得单独证明 `set_complete`（对应 §六(7)）

1. **fallback 触发**：当某 aspect 的导航候选未命中任何 `OutlineSpan` 时，
   从最近的结构锚点出发做**有界扩读**（相邻块/相邻页），扩读上限由版本化常量给定。
2. **产出仍是精确定位 span**：结果**必须**是带 `page_range` + `line_refs` + `char_range` 的
   `OutlineSpan`，且置 `is_fallback=True` 与 `fallback_derivation`（记录从哪个锚点、按何规则、扩了几步）。
   **whole Evidence 不能成为正式材料** —— 因此 fallback 也**不得**以 `EvidenceBlock` 为返回单位。
3. **不得单独证明 `set_complete`**：**强制挂点已存在（F20）**，无需新造机制 ——
   `harness/topic_store.py::commit_pack(...)` 是唯一提交入口，内部 `_validate_set_completeness`
   调用注入的 `set_completeness_verifier` / `set_enumeration_verifier`（R2 装配为
   `build_r2_material_dependencies()` → `FormalSetEnumerationVerifier`，见 `harness/set_enumeration.py`）。
   因此只需在该验算器可见的材料集合口径上增加条件：参与"集合完整"证明的材料中，
   `is_fallback=True` 的 span **不计数**，`unassigned_content` 中的内容**不计数**；
   它们只能在被**非 fallback** 材料独立佐证时作为补充出现。
   注意：`sections/topic_research.py` 那条第二运行时路径**不参与**该强制（§6/F10）。
4. `unassigned` 内容一律**可见且计数**（进 `unassigned_content.json` 与 Pack 的审计字段），
   **不得静默丢弃**以改善观感。

---

## 九、对 R1-B/R2 Pack 的后继兼容性（对应 §六(8)）

绑定对象是 **`harness/topic_schema.py:3082` 的 `TopicResearchPack`**（F10：P3 正式交付物）。
`sections/topic_research.py:224` 的同名类型**不扩展、不迁移、不启用**。

| 关注点 | 方案（基于实测的真实类型） | 兼容性 |
|---|---|---|
| **locator（定位）** | 真实类型是**判别式联合** `MaterialLocator = EvidenceLocator \| FinancialLocator \| ExternalLocator`，由 `locator_type` 区分，经 `locator_from_dict()` 反序列化（`harness/topic_schema.py:704-869`）。树结构扩展现有 Evidence 分支而非新增联合成员；规范 tree 字段固定为 `final_structure_snapshot_id/component_id/(span_id\|table_id)/evidence_id/evidence_char_range/span_local_char_range/coordinate_domain` 与各结构版本 | `TOPIC_PACK_SCHEMA_VERSION 4→5`；tree-v5 条件必填，非 tree 变体按联合真值表。v4 只由显式 history reader 返回且不得 current/completion；旧 reader 对 v5 明确拒绝，任何 adapter 不得过滤未知字段 |
| **payload（载荷）** | 真实类型 `MaterialPayloadRef`（`object_type / authority_identity / version / content_hash / locator / created_dependency_fingerprint`），`__post_init__` 强制 `MATERIAL_TYPES`、非空身份/版本、`content_hash` 与 `created_dependency_fingerprint` 必须是 **64 位 hex sha256** | 载荷语义不变；新增字段需同时满足上述强制 |
| **resolver（解析）** | `TopicMaterialPayloadResolver.resolve()` 已支持 `evidence_span/table_context`，但现 `_validate_envelope` 在 `locator.offset` 非空时不重算片段正文 | 对象类型无需新增；TS7B 必须叠加 final object-level verifier，从 verified final snapshot + 只读 Evidence 独立切片并重算 component/payload。篡改片段并同步重算 envelope/hash 仍拒绝；失败显式 unresolved，禁止回退同页任意块 |
| **Store（存储）** | 树结构产物写入**新目录/新文件**（§14），不落进 Evidence stores；Pack 存储沿用 `data/harness.db`，表为**不可变表 + BEFORE UPDATE/DELETE 触发器** | 迁移只能是**加表/加列**；不可变性触发器意味着**不能改历史行**，只能追加 |
| **migration（迁移）** | Store migration 追加 `"4"`；Pack wire 独立升为 v5。历史 Evidence/Pack 行零改写；version-dispatch history reader 保留 v4 原始审计视图 | 回滚 = 停止写/引用 v5；v4 历史仍可显式读，但不会被误升为 current |
| **checkpoint（检查点）** | `harness/topic_checkpoint.py` 是**只读**模块（`load_checkpoint` / `load_current_pack` / `verify_dependency_fingerprint`），并**跳过已失效的 current** | 树结构不新增 checkpoint 机制，只让指纹参与既有 `verify_dependency_fingerprint` 比对 |
| **`dependency_fingerprint`** | 沿用 `compute_dependency_fingerprint(...)`，以新增封闭键绑定 verified final material snapshot、qualification policy、synopsis、table decision/TableObject、component schema 与 profile 版本，不另立平行指纹 | 严格相等；v4 缺键只能 history/stale，不能 current |
| **双轴状态** | 沿用 `process_status` / `coverage_status` 双轴与 `NotFoundAudit.qualified` 规则；树结构不新增第三套状态 | 与 R2 三轴验收模型（`material_state`/`capability_verdict`/`report_impact`）不冲突，且不自动互相映射 |

---

## 十、P4 Claim/Citation 的溯源（对应 §六(9)；**不实现正式 Writer**）

- 只需保证**引用可回溯**，不写 Writer、不动 `WritingSpec`/`PresentationProfile`。
- successor 追溯链（每一跳都必须以 id/hash/locator 逐跳验证）：
  `ProposedSupportRef / AcceptedSupportBinding → authority-specific payload + locator → Pack ResearchMaterial / fact / citation → verified FinalMaterialStructureSnapshot → component → (span_id | table_id) → TextAlignmentRecord/RefusalRecord → EvidenceBlock exact slice`。
  `SectionClaim` 只引用 factual accepted-binding IDs；final Narrative 引用 accepted Claim IDs 与 context accepted-binding IDs。
  财务权威另加一跳：`→ FinancialSnapshot`（仅当 `structure_class == financial_main_statement`
  且**金额一律取自 `FinancialFactPack`**，TableObject 本身不授权金额，§11.1）。
- **真实类型（F19，不得假设设计文档中的类型已存在）**：
  `harness/schema.py` 的 `CitationRef`（`ref_type / evidence_id / evidence_fact_id /
  snapshot_id / item_code / formula_id / formula_version / period / source_snapshot_id / page_number`）、
  `Claim`（`claim_id / text / kind / citation_refs: list[int]` —— **指向 `ResearchAnswer.citations` 的下标**）、
  `ResearchAnswer`；`sections/schema.py` 的 `SectionClaim`；
  渲染侧 `sections/chapter_writer.py` 的 `SentenceDraft / ParagraphDraft / TableDraft / ChapterDraft`。
  **`NarrativeParagraph` 在代码中零实现**（仅见于设计 Markdown）⇒ 本计划**不得**以它为前提。
- 两套 legacy `CitationRef`（Pack 内 `harness.topic_schema.CitationRef` 与 runtime
  `harness.schema.CitationRef`）只能作为显式 compatibility adapter 的输入，不得成为第三种 support wire；同步发布后继并逐字段转换。对 v5 tree citation，
  `final_structure_snapshot_id/component_id/(span_id|table_id)/evidence_char_range/
  span_local_char_range/coordinate_domain` 是**条件必填**，不是一组可随意省略的可选字段；旧非 tree Claim
  仍按旧变体合法。`citation_refs` 下标含义不变。
- **可验证性（沿用既有链路，不新造）**：
  `sections/research_common.py:148 resolve_claim_refs`（下标→`CitationRef`）、
  `sections/citation_authority.py:412 build_citation_authority` + `CitationAuthority.validate(ref)`；
  树结构需增加“verified final snapshot → component → span/table → alignment terminal → EvidenceBlock”一跳，
  并从真实 block 独立切片重算，不得只信 payload/envelope。
  解析失败 ⇒ `unresolved_reference` 且**不得**进入正式内容（fail-closed）。
- **不实现正式 Writer、不改 Renderer**：`sections/chapter_writer.py` **已存在**（函数式渲染器，
  自述 `PURE_RENDER_CANDIDATE = True`），`sections/publishable_report.py::write_publication` 亦已存在。
  TS4 本批不修改它们；TS7B 必须修改 `harness.runtime`、`sections.schema` 与 citation authority，禁止当前
  “只保留已知字段”的静默降级，确保可消费并验证 v5 tree citation。
- **P4 不得只遍历 `answer.claims`**：exact manifest 的每个成员须有 WMPD 并回指 RMD，预验证事实另有 FND；追溯覆盖 `ClaimCandidate`、`NarrativeDraftUnit`、proposals/accepted bindings、Claim 与 final Narrative。
- 公司/行业写作消费与 `SectionTask.topic_ids` **完全匹配**的一组 Pack；缺 Pack ⇒ 显式 gap/block。
- **下游归属消歧（2026-09-21）**：本节只定义逐跳追溯接口，不提前实现写作主链。SectionDraft subjects/proposals、aggregate binding、factual entailment/context binding、accepted bindings、Claim/final Narrative/Result、formal `ExternalFact`、三类 disposition、`FollowUpNeed` 与 Reviewer/Controller 均属于独立 changelist；树层产物只提供精确材料与 provenance，不得当作合格事实或 accepted Claim。

## 十一、四类内容的读取与权威边界（对应 §六(10)）

### 11.1 财务主表权威（**本轮修正的技术语义之一**）

> **`TableObject` 只能提供结构定位、原表复核和引用路径。**
> **财务报表主表数字的权威仍只能来自 `FinancialSnapshot` / `FinancialFactPack`。**
> **即使 `TableObject` 位于财务主表章节，也不得自行授权金额。**

- `financial_main_statement` **如保留，只能表达结构分类或绑定提示**，
  **不是 authority verdict**；因此该字段在类型上**更名为 `structure_class`**
  （取值 `financial_main_statement` / `note_table` / `ordinary_business_table`），
  并在命名与文档上明确它**不裁决权威**。
- 财务主表章节的 `TableObject` 与 `FinancialSnapshot` 之间是**绑定/对照**关系
  （用于原表复核与引用定位），**不是**替代关系；金额取用一律走 `FinancialFactPack`。
- **财务附注表**与**普通业务表**仍是 Evidence-backed 材料；其事实授权分别通过经验证的 note fact、预验证事实或 topic material path B，**不得**因“也是表”而被并入 FinancialSnapshot 权威。

### 11.2 四类内容的读取与权威边界

| 内容类 | 读取来源 | 权威 | 可否进正文 | 可否证明 `set_complete` |
|---|---|---|---|---|
| **财务报表主表** | `FinancialSnapshot` / `FinancialFactPack`（`TableObject` 仅供定位与复核） | **财务权威（唯一）** | 经 `FinancialFactPack` | 是（按其自身规则） |
| **附注散文** | `OutlineSpan`（正文） | Evidence 支撑的事实 | 是，需引用 | 是（需非 fallback） |
| **附注表格** | `TableObject`（`structure_class="note_table"`） | Evidence-backed note material / validated note fact | 按事实或 material path，需引用 | 是（需非 fallback） |
| **普通业务表格** | `TableObject`（`structure_class="ordinary_business_table"`） | topic material；必要时预验证为事实 | 按事实或 material path，需引用 | 是（需非 fallback） |
| **`ExternalFact`（snapshot 为来源载体）** | 外部来源库 | formal external fact authority | 按 SourcePolicy 与资格决定 | 独立判定，**不得**与本地混权 |

- 四类**读取路径与权威标签分离**，统一的是 **Fact Registry 读视图与语义身份**，
  **不是**把数字合并进一张权威表（CLAUDE.md 明禁）。
- `structure_class` 的判定**只能**由章节路径 + 结构位置得出；**禁止**页码/表号/公司特例。
- `FinancialFact` / Evidence note fact / topic fact-or-material / `ExternalFact`
  的授权分支互不顶替；`ExternalSnapshot` 只提供 body hash、URL、日期与 SourcePolicy provenance。
- **跨树聚合（§7）同样受此约束**：聚合器可以把财务主表章节的 `TableObject` 作为
  **定位与 occurrence** 纳入材料库，但**不得**据此产生权威金额；
  金额类冲突必须交给 `FinancialFactPack` 侧的规则裁决。

## 十二、CLI、自检、追加式迁移、依赖指纹、current/stale、旧对象兼容（对应 §六(11)）

### 11.1 CLI（沿用 `_main(argv)->int` + argparse 惯例，F12）

```
python -m document_structure.build_outline <electronic.pdf> --company <company_id> --validate-only
python -m document_structure.inspect_outline --document-id <id> --version <version>
python -m document_structure.build_layout   <electronic.pdf> --company <company_id> --validate-only
python -m document_structure.build_spans    --document-id <id> --version <version> --validate-only
python -m document_structure.build_tables   --document-id <id> --version <version> --validate-only
python -m document_structure.build_profile  --validate-only     # 离线派生 AspectNavigationProfile
python -m document_structure.self_check
```

- `--validate-only` **禁止**调用任何 `init_db()`（对比 F12：现有 `retriever_v2` CLI 会调用 `estore.init_db()`）；
  所有构建命令默认**只写文件产物**到 `evaluation/results/tree_structure_<run_id>/`。
- 不依赖 LLM / Router / Contract 即可完成基础标题树（`DESIGN_V2.md` §16.2.1）。

### 11.2 自检 `self_check`

1. schema 与不变量（含 §1.2 全部 fail-closed 条款）；
2. **确定性**：同一输入连续构建两次，产物 JSON 逐字节相同、id 集合相同；
3. **无损重构**：span 拼接可重构覆盖原文（§2.3）；
4. **无公司特例**：全产物扫描公司名/股票代码/固定页码/表号/gold 词 ⇒ 必须零命中；
5. **profile 公司无关性**：两公司生成结果逐字节相同（§5.3）；
6. **对齐可信度**：报告 `coverage` 分布与 `unexplained` 占比；超阈即失败。

### 11.3 追加式迁移

- 新 `document_outline` / `PageLayout` / `evidence_set_version` **只追加**，历史对象**永不覆盖/删除**。
- 触发新 set 的条件：`NORMALIZATION_VERSION` / 解析 builder 版本变化，或对齐失败率超阈。
- 迁移脚本必须**幂等**且**可回滚**（回滚 = 停止引用新 id）。

### 11.4 依赖指纹（落在既有机制上，F17）

**不另立平行指纹方案。** Pack 级指纹已由
`compute_dependency_fingerprint(contract_fingerprint, source_policy_version, dependency_versions)`
计算，`dependency_versions` 受 `DEPENDENCY_VERSION_KEYS` 白名单约束。因此扩展方式只有一种：
**向 `dependency_versions` 增加键**（受 `validate_dependency_versions` 校验），建议键名
`layout_version` / `outline_version` / `span_version` / `table_version` / `profile_rule_version`。

树结构自身的多级指纹（用于文档级 current/stale，独立于 Pack）：

- `PageLayout` 指纹 = `h(source_file_sha256, LAYOUT_ENGINE, engine_version, LAYOUT_SCHEMA_VERSION, NORMALIZATION_VERSION)`
- `DocumentOutline` 指纹 = `h(page_layout_fingerprint, OUTLINE_ALGORITHM_VERSION, OUTLINE_SCHEMA_VERSION)`
- TS4 span 指纹 = `h(verified_span_snapshot_id, qualification_policy_id,
  TS4_BODY_SPAN_BUILDER_VERSION, evidence_set_version)`；TS5 后改绑定 verified
  `FinalMaterialStructureSnapshot`。单个 span locator 始终使用**对象实际** `span_builder_version`；
- table 指纹 = `h(final_material_snapshot_id, TABLE_BUILDER_VERSION, evidence_set_version)`。

一致性要求：文档级指纹变化时，Pack 级 `dependency_versions` 中对应键必须同步变化，
否则会出现"文档已重建而 Pack 仍判 current"的静默过期。

### 11.5 current / stale（按实测的真实模型，F18）

- **不假设存在 status 列**。Pack 侧：`current` 是 `topic_current` 的**指针**；
  `stale`/`invalidated`/`quarantined` 是 `topic_event` 的**追加式终态事件**；
  `load_current_pack` 在有终态失效时返回 `CurrentPackLoad(pack=None, reason=<event_type>)`。
- 文档级对象（`PageLayout`/`DocumentOutline`/`span`/`table`）为**新增产物**，
  其 current/stale 由**指纹比对**判定（重算 ≠ 存储 ⇒ `stale`；存在更新的同文档 outline ⇒ 旧者 `superseded`），
  并明确记录在 §15 的 `manifest.json` 中。
- 判定**不靠人工**。F5 已证明三份文档的 `document_version` 与磁盘哈希逐一 MATCH
  ⇒ 文档级指纹判定在当前工作区**立即可用**。

### 11.6 旧对象兼容

- `evidence/adapters.py:24,37` 的 `section_title = block.section_path[0]` 单标题兼容路径**保留不动**；
- `EvidenceBlock` **不新增必填字段**（避免破坏 `content_hash` / `evidence_id` 派生，F5 的 769 块不受影响）；
- `TextChunk.section_level` 在 Evidence 往返中被丢弃（`evidence/adapters.py:25` 置 0）的现状**不修改**；
  层级信息由新的 `DocumentOutline` 承载，而非回填 Evidence。

## 十三、文件级提交拆分、阶段关系、工作量与分批停止条件（对应 §六(12)）

按 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` §12 的 9 组，映射到 **TS0 / TS1–TS5 / TS6 / TS7A / TS7B**。

### 13.1 阶段关系与"材料库建立时点"（**不得把跨树聚合推迟到 R3**）

```text
TS1–TS5
  建立只读、版本化的文档材料底库：
  PageLayout → DocumentOutline → OutlineSpan / TableObject

TS6
  Contract v2 → AspectNavigationProfile → 候选树节点

TS7A
  跨树、跨文档、跨年份聚合
  → AspectMaterialAggregationResult
  → 人工可读材料库样板

TS7B
  ToolRegistry / Retriever / TopicResearchPack / Citation / Store 兼容接线

R3
  对全部 115 个 topic_harness aspects 正式调度和运行跨树聚合
  → 主题材料库 / TopicResearchPack

P4/R5
  Writer 消费完整 Pack，把材料组织成人读章节
```

**硬性要求**：跨树聚合（`aggregate_aspect_materials`）的基础能力**必须在树结构调整阶段
（TS7A）完成并通过真实小样本**；**R3 只负责全量调度和执行，不负责首次设计或首次验证**。
树结构调整**不得仅凭"标题树构建成功"而关闭** —— 必须能看到可**供人检查**的跨树材料库样板（§15）。

### 13.2 分批提交拆分与停止条件

| 批次 | 组 | 新增/修改文件 | 工作量 | **停止条件（不满足即停，不进入下一批）** |
|---|---|---|---|---|
| **TS0** | 0 | 本文件 + 冲突审计报告 | 已完成 | 用户 + Codex 批准本计划；§3.4 的 `ALIGN_MIN` 程序获批 |
| **TS1** | 1 | `document_structure/{__init__,versions,schema}.py` | 2–3 人日 | 类型/不变量/确定性单测全绿；无公司特例扫描零命中 |
| **TS2** | 2 | `document_structure/layout_builder.py`、`normalization.py` | **已完成并通过**（2026-09-17） | 三份真实 PDF 全量构建成功；家具检测与阅读顺序单测绿；**产出全量 coverage 分布并完成 §3.4 归因**；`ALIGN_MIN` 获批准（**`ALIGN_MIN = 0.90`，用户 + Codex 批准冻结**）→ 见「TS2 关闭记录」 |
| **TS3** | 3 | `document_structure/outline_builder.py`、`aligner.py`；**复用** `harness/heading_structure.py`（如需扩展层级序则就地改并升版本，附回归） | 4–5 人日 | 层级/同名标题/跨目录候选单测绿；S1/S2 为空时行为不变（F2）；对齐 verdict 分布达标；`heading_structure` 既有回归不回归失败 |
| **TS4** | 4 | 新增 `document_structure/{evidence_gateway,span_schema,span_policy,span_builder,span_verifier,synopsis}.py` 与 policy/fixture/runner/tests；修改 `document_structure/{__init__,versions,schema,outline_builder,layout_builder,aligner}.py`、`evals/run_evals.py` 及 3 个既有阈值测试；**不改** `harness/**` / Pack Store | 7–9 人日 | 封闭 `VerifiedTS3Handoff` 组合根、正式 Evidence gateway 的只读/authority 反例、TS3→TS4 结构快照受信重建、坐标投影、全快照重算、三层守恒、synopsis 来源复核与非 300750 正向 fixture 全绿；TS4-A 只出分布，人工冻结资格策略后 TS4-B 重跑；每次代码提交前完整离线 eval 通过 |
| **TS5** | 5 | 以本文件 **§十九** 为唯一规格：`TableObject to-4`、同源 geometry、cell 级 provenance/citability、typed relations、immutable decisions/bindings、final snapshot/capability；`detect_table_start_flags` 只作 generic 文本候选，不是所有表的必要条件 | 不设工时承诺 | §十九的 schema/反例/formal-chain/守恒/真实样本与人工 review seal 全部通过；诚实 partial/unsupported 可保留，但普通段落零伪表、两个表内小标题完整、全部 TS4 components 唯一绑定、只有 `VerifiedFinalMaterialStructureSnapshot` 可供 TS6/TS7 使用 |
| **TS6** | 6 | `document_structure/profile_builder.py`、`profile_validator.py`、`nav_rules/anp-1.yaml`，并通过**既有** Retriever / ToolRegistry adapter 接入候选导航（不接 Pack/Store） | 不设工时承诺 | 公司无关性逐字节测试、禁词扫描、无 shadow Contract 回溯与正式 Router→ToolRegistry 候选导航集成测试全绿；Pack/Store/P4 provenance 仍留 TS7B |
| **TS7A** | 7 | `document_structure/aggregation.py`（`aggregate_aspect_materials` 及 §7.1 五类公共类型）、`document_structure/catalog.py`（材料库样板）、`scripts/` 聚合预览 runner | 不设工时承诺 | **§13.3 的 7 条真实退出门全部通过**；`aspect_material_catalog.md` 可人工阅读且含实际文本；确定性（seed/候选顺序无关）测试绿；无公司特例扫描零命中 |
| **TS7B** | 8 | `retrieval/*` 元数据扩展、`tools/*` 新工具声明、`document_structure/{store,cli,self_check}.py`；`harness/topic_schema.py`（`TOPIC_PACK_SCHEMA_VERSION 4→5` + tree locator/citation 后继）、`harness/topic_store.py`（Store migration **4** + version-dispatch 历史读 + final-object verifier + dependency 增键）、`harness/schema.py` 与 `harness/runtime.py`（运行时 CitationRef 后继及严格转换）、`sections/{schema,citation_authority,research_common}.py`（禁止过滤未知字段并重查 final provenance）、必要 adapter、`.gitignore` 与 eval | 不设工时承诺 | 树感知检索集成测试绿；Pack v5 身份/current-stale/checkpoint 测试绿；v4 仅经显式 version-dispatch history API 可读、不可 current/completion，v5 被旧读器明确拒绝且字段不静默丢失；payload 与 citation 均从真实 Evidence + verified final snapshot 独立重算 component slice；P4 两条 CitationRef 链一致；H1–H4 测试卫生收口；三份真实 PDF + 一份非 300750 fixture 逐一通过；产物规格落盘 |

TS1～TS4 列中的旧估算仅保留为历史计划信息，不作执行承诺；自 TS5 起不再给人日或墙钟估算，
只按停止条件、质量门和真实验收判断是否完成。每批**独立提交、独立回归**；每批结束时必须输出：
(a) 代码/回归是否通过；(b) 正式调用链是否唯一；(c) aspect 覆盖/材料与事实保留是否通过；
(d) 外部漏斗是否产出可采用事实；(e) 段落/表格是否达"人读内容门"；(f) 剩余 gap 与来源限制。
**全绿不得单独宣称树结构调整或 Phase 4 关闭。**

### 13.3 TS7A 真实退出门（**7 条，全部必须覆盖**）

跨树聚合必须用**真实小样本**证明可交付，而不是只证明"标题树能建出来"：

| # | 退出门 | 要证明的具体事项 |
|---|---|---|
| 1 | **主营业务** | 同一文档的**多个标题节点与表格**被聚合到同一 aspect 材料库 |
| 2 | **核心竞争力** | **多个子标题、多个文档版本并列**；**不因重复表达而被错误删除**（只建重复簇、保留各 occurrence） |
| 3 | **主要子公司** | **表格节点、散文节点及跨文档材料分别保留**，不因来自同一主题而被合并 |
| 4 | **授信额度** | **拟申请额度 / 实际获批额度 / 已使用 / 未使用** 四种语义**不得混合或错误求和**（对应 §7.4-4 的语义隔离） |
| 5 | **非 300750 fixture** | 全过程在**一个非 300750 fixture** 上同样成立 |
| 6 | **多路径可达去重** | 同一材料从**多个导航路径**抵达时**去重但保留 occurrence / reachability** |
| 7 | **如实展示** | **真实冲突、未读范围、fallback 与 unassigned 均如实展示**，**不得**通过丢弃改善完成率 |

## 十四、Eval 计划（与实现**同等**的交付物）

`evals/` 新增（并注册进 `evals/run_evals.py:EVAL_MODULES`；注意 F10 之外的既有事实：
`test_r2_source_object_closure.py` 从未被注册，本计划**不擅自改动**该现状，仅在新模块中登记）：

| 模块 | 覆盖点 |
|---|---|
| `test_tree_page_layout.py` | 全页覆盖率（三份真实 PDF 逐页无遗漏）；家具检测；阅读顺序；确定性双跑 |
| `test_tree_outline_hierarchy.py` | 标题/子标题层级；**同名标题以完整路径+源位置区分**；S1/S2 缺失（F2：0 书签）时行为不变 |
| `test_tree_outline_span_split.py` | 跨标题切分；**无损重构**不变式；跨页 span |
| `test_tree_alignment.py` | **正例**（≥`ALIGN_MIN` 且残差已分类 ⇒ aligned）；**负例**（残差 `unexplained` ⇒ 不得 aligned）；家具与被污染样本；fail-closed 触发追加新 `evidence_set_version` |
| `test_tree_table_object.py` | 标题/单位/表头/正文/合计/续表；**负例：普通段落不得成表**；`structure_class` 不得由页码/表号得出；**财务主表章节的 TableObject 不得自行授权金额**（§11.1） |
| `test_tree_nav_profile.py` | 候选召回率、**误召回率**、**公司无关性**（两公司逐字节相同）、禁词零命中、无 shadow Contract |
| `test_tree_fallback_rules.py` | fallback 仍产精确定位 span；**fallback/unassigned 不得单独支撑 `set_complete`** |
| `test_tree_retriever_integration.py` | 树感知 Retriever/ToolRegistry 返回 span/table 且**不冒充 Evidence**（不写入 `evidence_ids`） |
| `test_tree_pack_compat.py` | Pack v5 身份/`stale`/`checkpoint`/dependency；v4 仅显式 history 可读且不能 current/completion；v5 被旧 reader 拒绝；转换不丢未知/树字段 |
| `test_tree_p4_provenance.py` | 两套 CitationRef + adapter 全链：Claim → material → verified final snapshot → component → span/table → Evidence exact slice/FinancialSnapshot 逐跳可验；篡改片段并重算 payload/hash 仍拒绝；解析失败即 unresolved |
| `test_tree_no_company_specialcase.py` | 全产物扫描：公司名/股票代码/固定页码/表号/gold 词零命中 |
| `test_tree_real_pdfs.py` | 三份真实电子 PDF **逐一**验证（229 / 232 / 141 页，F1）+ **一份非 300750 fixture**；输入文件哈希记入 manifest（F8） |
| `test_tree_aspect_navigation_e2e.py` | 主营业务 / 核心竞争力 / 主要子公司 / 财务附注 / 显式互指，各自 **before/after** 对照 |
| `test_tree_structure_primitives_regression.py` | **复用回归**：`harness/heading_structure.py` 的层级序与 `iter_heading_spans`、`harness/table_structure.py` 的 `detect_table_start_flags` 在树结构接入后行为不变；**只允许一处真相**——断言 `document_structure/` 未复制编号→层级、表格起点规则（F14/F15） |

**跨树聚合专项（TS7A，与 §7 一一对应，必须全部新增并注册）**：

| 模块 | 覆盖点 |
|---|---|
| `evals/test_tree_aspect_aggregation.py` | **父节点命中后的子树闭包**（§7.4-1）；**同文档多个节点**（§7.4-2）；fallback 不证明 `set_complete`（§7.4-9）；聚合结果**不等于** aspect covered（§7.5） |
| `evals/test_tree_cross_document_dedup.py` | **跨文档和跨年份**（§7.4-3）；**精确去重与 occurrence 保留**（§7.4-5/6）；**多路径可达时去重但保留 occurrence/reachability**（§13.3-6）；来源不同**引用不被合并** |
| `evals/test_tree_scope_period_conflict.py` | **同金额不同语义不得替代**（§13.3-4 授信额度四语义）；跨年份/跨主体/跨口径/跨币种**不得相互覆盖**（§7.4-4）；**冲突并存不仲裁**（§7.4-7）；`scope_period_matrix` 正确 |
| `evals/test_tree_material_catalog.py` | **材料目录能展示实际文本和来源**（§15.2 的 10 条可见性）；重复簇**逐条列出 occurrence**；未读范围如实展示；**seed 顺序、候选顺序改变时输出仍确定** |
| `evals/test_tree_pack_projection.py` | `pack_projection_preview` 的资格判定与不合格原因；**非 300750 fixture**（§13.3-5）；**无公司名、股票代码、固定页码、表号和 gold 词生产特例** |

其中"**材料目录能展示实际文本和来源**"必须断言到**文本内容本身**（例如断言目录中存在源文本片段、
且与 span/table 原文逐字符一致），**不得**只断言"有 N 条记录"。

**验收输入的可复现性（F8）**：三份真实 PDF 被 `.gitignore` 忽略 ⇒ 验收 **不依赖**其可被 fresh clone 取到，
而是 (a) 由 `manifest.json` 记录每份输入的 `sha256` 与页数，(b) fixture 缺陷时**明确报 `ENVIRONMENT_DISCREPANCY`**，
**不得**记为代码失败、也**不得**记为通过。

## 十五、产物规格 `evaluation/results/tree_structure_<run_id>/`

对齐 `DESIGN_V2.md` §12.5.7 的 run-dir 契约。文档级产物（每文档一子目录）：

```
evaluation/results/tree_structure_<run_id>/
  run_manifest.json              # run_id、契约/规则版本、输入文件 sha256+页数、版本常量、结论汇总
  page_layout.json               # PageLayout（含 pages/lines/spans/家具标记/阅读顺序）
  document_outline.json          # DocumentOutline（nodes/edges/unassigned/candidate_sources）
  outline.md                     # 人读标题树
  span_index.json                # OutlineSpan 索引（含 fallback 标记与 derivation）
  normalization_alignment.json   # TextAlignmentRecord 全量（verdict/coverage/residue_class/char_map）
  navigation_synopsis_validation.json  # 每节点 synopsis 状态 + reason_code + 可回溯校验
  table_objects.json             # TableObject（含 structure_class/structure_evidence/续表链）
  unassigned_content.json        # 未归属内容（**显式计数，不得静默丢弃**）
  structure_validation.json      # self_check 结果（确定性/无损/无特例/分布）
  before_after.md                # 树结构调整前后对照
  manifest.json                  # 文档级清单：id/指纹/status(current|stale)/依赖版本
  aspect_navigation_profiles.json  # run 级：AspectNavigationProfile（公司无关）
  navigation_audit.jsonl         # run 级：候选召回/误召回/未命中审计
  # ---- 以下为跨树材料库预览产物（TS7A，§15.2）----
  aspect_material_catalog.json   # 机器可读材料目录（按 aspect 组织）
  aspect_material_catalog.md     # **人工可读材料库样板**（见 §15.2 的可见性要求）
  cross_tree_aggregation.json    # 跨树/跨文档/跨年份聚合的采用与未采用结论
  material_occurrences.json      # 全部 occurrence（**去重后仍保留**，含可达性）
  dedup_clusters.json            # 精确重复簇（payload + 来源身份均相同）
  conflict_groups.json           # 冲突事实组（**全部保留，不仲裁**）
  scope_period_matrix.json       # 年份/主体/口径/币种/期间的并列矩阵
  unread_unassigned.json         # 未读/未归属/fallback-only/alignment-failed 如实清单
  pack_projection_preview.json   # 将如何投影进 TopicResearchPack（含资格判定）
```

- 产物写入**新目录**，默认**不提交**、**不覆盖历史目录**；`.gitignore` 增补
  `evaluation/results/tree_structure_*/`（F9，属 **TS7B** 提交批次）。
- `case_results.jsonl` / `metrics.json` / `data_quality.json` / `report.md` 沿用 §12.5.7 既有命名，
  使新产物与既有 run-dir 可被同一套读取器消费。

### 15.2 `aspect_material_catalog.md` 的硬性可见性要求

**不得把这些产物做成只有计数和状态、看不到实际材料的审计文件。** 该 Markdown 必须能让人工**直接看到**：

1. **aspect**（aspect_id / question_id / topic_id 及 `requirement_text`）；
2. **命中的全部结构路径**（每一跳的完整 `structural_path`）；
3. 每个节点的**来源文件、版本、年份和页码**；
4. **正文/表格全文，或明确标注的可读预览**（截断必须显式写明截断位置与长度，不得静默省略）；
5. 每项材料的角色：`source` / `context` / `fallback` / `unassigned`；
6. **重复簇**，且**保留的来源 occurrence 逐条列出**（不得只显示"已去重 N 条"）；
7. **冲突、口径差异、期间差异**（并列呈现，不做仲裁）；
8. **未读范围**（哪些范围从未检索/未命中）；
9. **为什么采用或不采用**（确定性原因码 + 人类可读解释）；
10. **将如何投影进 `TopicResearchPack`**（含资格判定与不合格原因）。

判定标准：**评审者只读这一个文件，就能判断聚合是否正确**；若必须回去查 JSON 或日志才能理解，
则该产物视为未达标。

## 十六、已裁决事项与实施前置条件

### 16.1 已裁决事项（原 R1–R8 开放式提问已删除，以下为**确定结论**，不再列为待确认项）

| # | 结论 | 对计划的落点 |
|---|---|---|
| 1 | **`ALIGN_MIN` 不预先拍固定值。** TS2 输出全量分布与残差归因，**由 Codex 依据证据批准版本化阈值**；**不作为业务用户选择题** | §3.4 的归因程序不变，仅明确批准方为 Codex |
| 2 | **含不可解释残差的内容不得作为正式引用**；仍**保留为导航／未对齐内容**，**不得静默删除** | 强化 §3.2/§3.3 与 §7.4-10 的 fail-closed |
| 3 | 82 个临时诊断／生成文件及 STOP_REPORT **本轮不处理**，**不阻塞树结构编码**，后续单独治理 | 原 R3 关闭；§17 保留"不动"约束 |
| 4 | 两个 tracked deletion **保持不动**，**禁止进入任何提交** | 原 R4 关闭 |
| 5 | 正式 Pack **只有** `harness.topic_schema.TopicResearchPack`；`sections.topic_research` **不进入正式链**，本轮**也不删除** | 原 R5 关闭；§9 只绑定前者，§7 聚合只面向前者 |
| 6 | **复用** `harness/heading_structure.py` 与 `harness/table_structure.py`，**本轮不迁移**；**结构规则只能有一处真相** | 原 R6 关闭；§2.2/§2.4/§6/§13/§14 生效；F22 记录 |
| 7 | Pack 新增字段时 `TOPIC_PACK_SCHEMA_VERSION 4→5`，Store migration 追加 4；v4 只经显式 history reader 可读且不可 current/completion，v5 被旧读器明确拒绝；不得静默补字段或丢字段 | 原 R7 关闭；§9 / §18.10.3 的严格 version-dispatch 语义 |
| 8 | **只有真实样本证明现有标题层级不足时**，才在 `harness/heading_structure.py` 中**就地扩展、升版本并补回归**；**禁止在 `document_structure/` 复制规则** | 原 R8 关闭；成为 §13 TS3 的停止条件 |

原编号 R1–R8 不再作为"待用户确认项"使用；如需引用，按上表编号。

### 16.2 实施前置条件（测试卫生：**只记录，本轮不修复**）

以下四项**如实记录**，**不在本轮修改**，**也不阻塞 TS1**；
但**必须在 TS7B 正式集成门前完成独立测试卫生收口**：

| # | 事项 | 影响 |
|---|---|---|
| H1 | `evals/test_r2_source_object_closure.py` **尚未注册**进 `EVAL_MODULES`（`ecb66a8` 与 `d117ac0` 均未注册，全量输出中亦不出现） | 32 项测试**不在回归保护范围内**；树结构改动可能静默打破它 |
| H2 | `test_schema_mapper_llm` 存在**真实 HTTP 调用**且异常被吞 ⇒ **"完整离线 eval"的表述不完全准确** | 回归结论的解释必须带此保留，不得据其宣称离线完备 |
| H3 | `test_financial_v2_snapshot_admission.py` 存在 **fresh-clone fixture 脆弱性**（依赖未跟踪 xlsx 且无守卫） | 换环境复现可能不一致 |
| H4 | `test_intermediate_preview.py` 存在 **300750 测试特例** | 测试面含公司特例，与"产物无公司特例"的目标相冲突 |

补充记录（非阻塞、同样本轮不修）：`test_r2_boundary_gate.py` 的 fixture 卫生与其自身 L80 注释矛盾；
两处"放宽 fail-closed"反转（`source_object_gate` 在可归因的 `target_not_obtained` 上）
需确认 `set_enumeration._enumerate_business` 是否吸收完整性后果；
注释卫生（`set_enumeration.py:302` P51、`table_structure.py:680/682/928`、`context_expansion.py:459/557`）。

## 十七、本计划明确不做的事

- 不写任何树结构运行时代码或测试代码（本轮为**计划修订**，不是 TS1）；
- **不实现正式 Writer**，不修改 `sections/chapter_writer.py` / `sections/publishable_report.py`；
- 不提交现存 R2 runtime/eval 改动；不修改任何冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile；
- **不迁移** `harness/heading_structure.py` / `harness/table_structure.py`（§16.1-6）；
- **不修复** §16.2 的 H1–H4 测试卫生问题（只在 TS7B 前收口）；
- 不修改 `.gitignore` 以掩盖未分类文件（`tree_structure_*/` 规则属 **TS7B** 获批批次）；
- 不删除、移动、恢复或覆盖任何现存文件；**不处理**两个 tracked deletion，且禁止其进入任何提交；
- **不清理、不移动、不归档** `_probe_*` / `_gen_*` / `_build_*` / STOP_REPORT / 结果目录（§16.1-3）；
- 不 reset / checkout / restore / stash / rebase，**不改写 `d117ac0`**（混合工作快照，见文首定性）；
- 不执行 `git add`、不 commit；
- 不初始化、迁移或写入 Evidence / Financial 数据库；不调用真实 LLM / 博查 / 网络；
- 不进入 TS1 编码、不进入 R3/R4/R5 与 Phase 5/6；
- 不宣布 R2、树结构调整或 Phase 4 关闭；**不得仅凭标题树构建成功而关闭树结构调整**（§13.1）。

---

# 十八、TS4（`OutlineSpan` + `NavigationSynopsis`）编码前执行规格

> **编写**：2026-09-18 首次编写；**2026-09-18 R1 集中修订；2026-09-18 Codex 独立审查后原位收口**。
> **现行状态说明**：TS4 已于 2026-09-19 正式关闭；本节保留为已执行、已冻结的历史规格。
> 本节中“未开始/待批准/编码前现值”等措辞均是当时执行门快照，不得覆盖文件顶部关闭记录或 §十九的
> TS5 现行计划。
>
> 本节只写规格：不写代码、不新增测试、不生成真实产物、不 commit。本节是 §13.2 表中 TS4 行的
> 可直接编码展开，**不是**第二份平行计划：它细化 §2.3 与 §四 中含糊处，不改写任何历史实测数字，
> 与 §13.2 的活动批次表必须一致；本次已同步纠正其中会直接指导后续编码的 TS4/TS6 边界。
>
> **R1 修订范围**：仅本节。旧 §十八 中与已裁决结论冲突的规则一律**原位修正或明确删除**
> （清单见 §18.0），本节内**只保留一套可执行语义**；未新增平行计划，未追加"只负责覆盖前文"的
> 补丁章节。以下裁决与缺陷结论是**固定输入**，本节不再重新论证，只落地。

## 18.0 R1 修订清单：旧规则 → 处置（本节唯一语义的唯一来源）

| # | 旧 §十八 规则（2026-09-18 首次编写版） | R1 处置 | 新位置 |
|---|---|---|---|
| 1 | `SpanBuildInput.line_states: tuple[dict, ...]`、`table_scopes: dict`（调用方自报派生结果） | **删除**：改为真实对象字段，派生结果由入口**内部**调用冻结函数 | §18.3.2 / §18.3.3 |
| 2 | `SpanBuildResult.table_deferred / empty_text_ranges / components / conservation` 为 `tuple[dict, ...]` / `dict` | **删除**：改为 8 个 typed、版本化、确定性身份的顶层对象及封闭 typed 子结构 | §18.4.3 |
| 3 | 以 `block[bs:be]` 直接切片核验 char_map（未取真实 Evidence 文本） | **删除**：补真实 Evidence 文本，改为 E→L walk 公式 | §18.5.2 |
| 4 | "TS4 接受乱序 `char_map` 并排序"及其测试 | **删除**：TS4 不得排序；冻结 `als-3/alr-2` 仍可历史读回，但乱序对象在 TS4 正式输入 verifier 拒绝 | §18.5.5 / §18.13 |
| 5 | "`from_dict` 已免自证"结论 | **删除**：形状/版本/内部一致性 ≠ 来源真实；改为结构、span、synopsis 三个对象级验证器 + 篡改并重算反例 | §18.4.2 |
| 6 | `confidence` 由 Evidence 引用字符数 / 覆盖度计算 | **删除**：改为**边界置信度**表；引用覆盖另走 typed 覆盖投影 | §18.8.5 |
| 7 | A5/A6 表格边界（"少数表题可能污染正文"列为已知可接受残留风险） | **删除**：改为 `deferred_to_ts5` + `table_adjacency_pending_ts5` + TS5 决议接口 | §18.7 |
| 8 | 三维字符总量相加公式 `C_B = C_S + C_T + C_U + C_E` 等 | **删除**：改为文档层、正文域层、Evidence 独立分区层的三级 `⊎` 守恒 | §18.9 |
| 9 | "末节点正文因无后随标题判 `below_last_heading`"及其伪测试 | **删除**：末节点所有权候选仍受真实结构边界切断；仅真实 run 抵达末行时取文档末边界 | §18.6.3 |
| 10 | "每节点都有 span" | **删除**：改为"每个合格最大正文 run 产生一个 span；节点允许零个 span" | §18.6.2 |
| 11 | `MIN_SNIPPET_CHARS` 一处允许小于 20、一处要求至少 20 | **统一**为硬下界（唯一口径） | §18.8.6 |
| 12 | "`sb-1` 从未被真实使用" | **删除**（事实错误）；全局 `SPAN_BUILDER_VERSION=sb-1` 保留给 TS3/unassigned 历史与重建，新增 `TS4_BODY_SPAN_BUILDER_VERSION=sb-2` 专供 TS4 正文 | §18.12.1 / §18.12.2 |
| 13 | "冻结实现优先于任何文档" | **删除**：改为 `AGENTS.md` 的权威顺序；语义冲突必须上报，不得由后继规格自行消解 | §18.10.1 |
| 14 | "P1 不阻塞本批完成" | **删除**：身份 / 权威 / 守恒类 P1 **必须阻塞**；诚实的低覆盖等可登记为非阻塞 gap | §18.17 |
| 15 | "无新增任何类型" | **修正**："不盲目改动既有冻结 wire 类型；允许新增严格验证、版本化的 runtime/snapshot/disposition 类型" | §18.4.3 |
| 16 | "相邻表格行先按正文处理、TS5 再判" | **删除**：改为独立 provisional 处置，且不得进入 Synopsis / 正式段落材料 | §18.7.2 |
| 17 | `SpanBuildInput.evidence_set_version: str` 裸字符串 / 只传 `records` | **删除**：改为真实 `EvidenceSetSnapshot` 对象级核验 + 全终态闭合 | §18.3.2 / §18.3.4 |
| 18 | `TextAlignmentRecord` 与 `AlignmentRefusalRecord` 只按需取用 | **删除**：要求**全终态**闭合（每个 Evidence block 恰好一个 terminal），拒绝记录也必须提供 | §18.3.4 |

**删除即失效**：上表"处置=删除"的规则在本计划内不再具有任何约束力；若某条旧规则的意图仍然成立，
只能通过上表"新位置"指向的条款表达。

## 18.1 当前基线、冻结输入与实测坐标事实

### 18.1.1 批次门与冻结常量（只读、禁止修改）

- TS0 / TS1 / TS2 / TS3 已关闭；TS3 冻结点：`hq-4 / trg-3 / tocr-2`，当前真实 `DocumentOutline`
  节点集冻结（`OUTLINE_ALGORITHM_VERSION = oa-3`，`OUTLINE_SCHEMA_VERSION = do-4`）。
- TS4 只实现 `OutlineSpan + NavigationSynopsis`；TS5 / TS6 / TS7A / TS7B / R3 / Writer 未开始；
  **整体树结构门未关闭**。
- 编码前现值（Layout/Outline/aligner 冻结值不改；TS4 后继版本按 §18.12 升级）：
  `LAYOUT_SCHEMA_VERSION pl-3`、`OUTLINE_SCHEMA_VERSION do-4`、`SPAN_SCHEMA_VERSION os-4`、
  `TABLE_SCHEMA_VERSION to-3`、`SYNOPSIS_SCHEMA_VERSION nss-1`、`ALIGN_SCHEMA_VERSION als-3`、
  `ALIGN_REFUSAL_SCHEMA_VERSION alr-2`、`PROFILE_SCHEMA_VERSION anps-2`、
  `REFERENCE_EDGE_SCHEMA_VERSION res-4`、`NORMALIZATION_VERSION norm-1`、
  `OUTLINE_ALGORITHM_VERSION oa-3`、`HEADING_QUALIFICATION_PROFILE_VERSION hq-4`、
  `TABLE_REGION_QUALIFICATION_VERSION trg-3`、`TOC_BODY_RECONCILIATION_VERSION tocr-2`、
  `ALIGNER_VERSION al-3`、`ALIGN_MIN 0.90`、`SPAN_BUILDER_VERSION sb-1`、
  `EVIDENCE_BLOCK_INPUT_VERSION ebi-2`、`EVIDENCE_SET_SNAPSHOT_VERSION ess-1`、
  **`SPAN_CONFIDENCE_MIN None`**（未裁决）、`SYNOPSIS_VERSION ns-1`。TS4 新写路径将按
  §18.12 新增 `TS4_BODY_SPAN_BUILDER_VERSION=sb-2` 并使用 `nss-2/ns-2`；全局
  `SPAN_BUILDER_VERSION=sb-1` 保持兼容别名；新增
  `OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION=sb-1` 与 `TS4_BODY_SPAN_BUILDER_VERSION=sb-2`，
  以保证 TS3 等价重建并隔离 TS4 正文语义；`nss-1/ns-1` 仅被识别为旧版本后**显式拒绝并要求重算**，
  不宣称可作为 current synopsis 读回消费。
- **两门结构（本批唯一执行节奏）**：
  - **TS4-A**：编码 + 在真实样本上产出**边界置信度分布**（§18.8.5）；`SPAN_CONFIDENCE_MIN`
    仍为 `None`。
  - **人工门**：用户 + Codex 查看分布并冻结**完整资格策略**（因子表、阈值、批准指纹），发布
    新 `SpanQualificationPolicy` 身份；不得用常量变化重解释 TS4-A 快照。
  - **TS4-B**：以 frozen policy 重跑；对应常量与 policy 同步发布后再判断是否正式关闭。
  - `SPAN_CONFIDENCE_MIN is None` 期间：允许开始 TS4-A 编码，但**不得**宣布 TS4 关闭、不得进入
    TS5、任何 span 不得支撑 `set_complete`（`is_structurally_eligible()` 在 `None` 下恒为 False）。

### 18.1.2 TS1 已提供、TS4 只使用不改动的类型

`document_structure/schema.py`：`OutlineSpan`（26 字段）、`NavigationSynopsis`（10 字段，
**无 `text`**）、`SynopsisSnippet`、`TextAlignmentRecord`、`AlignmentRefusalRecord`、`TableObject`、
`PageLayout`、`DocumentOutline`、`OutlineNode`（**无正文范围**）、以及运行时类型
`ReferenceValidationContext` / `VerifiedReferences` / `TocSource`。

**不得发明**：`SpanComponent`、`SpanSlice`、`SourceSlice`、`SpanLocator`、`AlignmentBinding`、
独立 `NavigationProfile` 等不存在的类型；也不得给 `OutlineNode` 增加正文范围（节点集冻结）。

### 18.1.3 实测坐标事实（R1 于真实冻结产物上测得，作为本节公式的事实基础）

在 TS3 关闭产物 `evaluation/results/tree_structure_ts3_outline_ts3_outline_tocr2_closure_p2final_20260918T130000Z/`
的四个文档（`FIXTURE_BOND_2026` / `NDSD_2024_year` / `NDSD_2025_year` / `NDSD_KCZ_2026`）上，
以只读方式（`data/evidence.db` 以 `?mode=ro` 打开）核验 `normalization_alignment.json` 与
`page_layout.json`、`evidence_blocks.text`：

| 事实 | 实测值 |
|---|---|
| 对齐终态记录总数 | **769** |
| `char_map` 段总数 | **63418** |
| 存储顺序非升序的记录数 | **0**（升序＝存储顺序，全量成立） |
| 拒绝终态记录数 / 其中携带 `char_map` 者 | **1 / 1** |
| walk 公式（§18.5.2）逐段成立 | **63418 / 63418**，0 失败 |
| `(page,line,span_index,char_offset)` 命中真实 `PageLayout` 且该字符非空白 | **63418 / 63418**，0 失败 |
| 终态引用的 Evidence block 在只读库中取不到真实文本者 | **0** |

结论（固定输入）：**升序收紧**不破坏任何现有产物（0/769）；**walk 公式**在真实数据上全量成立；
终态与 Evidence、终态与 Layout 三者可逐项对上。TS4 的验收必须以本表所依据的**同一份冻结产物**为准
（§18.14），不得从 PDF 重建另一棵树。

### 18.1.4 权威顺序

`AGENTS.md` > `DESIGN_V2.md` > 已确认业务基线（冻结 Contract v2 / Source Policy /
WritingSpec / PresentationProfile / `FORMULA_REVIEW.md` 及其声明范围内的 v1 兼容资产）>
`V2_IMPLEMENTATION_PLAN.md` > `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` >
`TREE_STRUCTURE_ADJUSTMENT_TASK.md` > `V2_TODO.md` > `CLAUDE.md`。`DOCUMENTATION_INDEX.md`
负责文档地图与状态导航，不插入上述业务/架构权威链。历史事实只追加、不改写。

## 18.2 目标 / 非目标 / 两门结构

### 18.2.1 目标（全部负面可判定）

1. 在**真实冻结 TS3 输入**上，把"正文域"切成可判定、可对账的 typed 范围处置；
2. 产出 `OutlineSpan` 主体 span（每个合格最大正文 run 一个），并让**不合格范围**以 typed 终态
   留在正式链条内（不得消失进临时 JSON）；
3. 建立 span-local 与 Evidence 两个坐标域的**确定性映射**与**可引用覆盖**判定；
4. 产出 `NavigationSynopsis`（导航专用，不作证据），并证明其每个片段都被合格 Evidence 连续覆盖；
5. 三级守恒全量成立（文档层 + 正文域层 + Evidence 独立分区层）；
6. 全链条可**对象级验证**：任何"内容被篡改后重算全部派生 id / 哈希"的载荷都必须被拒绝；
7. 对外只落验收投影（JSON / Markdown），正式接口只有 typed 对象。

### 18.2.2 非目标（**本轮明确不做**）

- 不实现 TS5（表格对象 / 表格吸收）、TS6、TS7A、TS7B、R3、Writer；
- 不改 `PageLayout` / aligner 的 wire schema、算法判定、标题资格 / 层级、表格污染规则、TOC /
  Bookmark 信任边界；允许在 `layout_builder.py` / `aligner.py` **只追加不可序列化的已验证运行时
  capability 签发接口**，以封闭 TS3→TS4 信任链，严禁借此重跑、改写或放宽既有算法；
- 不重跑对齐、不改 `ALIGN_MIN`、不新建第二个 Evidence Set；
- 不改写任何 TS3 冻结产物；不把 `evaluation/results/**` 当权威输入（§18.3.1）；
- 不改既有冻结 wire 类型的字段与语义；不给 `OutlineNode` 补正文范围；
- 不采信"统一放大预算 / top-k / 放宽 fail-closed / D 级来源 / snippet 进正文"换取"完整"；
- 不提交 `.env`、Key、数据库、日志、生成结果、debug 文件、个人设置或参考材料。

## 18.3 唯一正式输入链与组合入口

### 18.3.1 禁止把验收投影当权威输入

生产代码**不得**读取 `evaluation/results/**`、`line_structure_assignment.json`、
`normalization_alignment.json`、`formal_unassigned.json`、`table_deferred_ranges.json`、
`span_components.jsonl` 或任何 runner 产物作为权威输入。这些一律只是**验收投影**
（§18.4.3 / §18.11）。TS4 的权威输入只能是：真实 `PageLayout`、真实 `DocumentOutline`、
版本化并经对象级验证的 `OutlineStructureSnapshot`、受信只读 Evidence gateway 返回的
`EvidenceSetSnapshot` + 全量 `EvidenceBlockInput`，以及与同一 Evidence 集闭合的全量对齐终态。
`OutlineBuildResult` 是 TS3 构建过程中的内部对象，既无 wire 版本也无 `from_dict`，**不得**成为
TS4 的跨阶段正式输入。

> 真实验收（§18.14）可以读取已由**仓库内受信根摘要**锁定的 TS3 manifest / artifact index，以
> 定位正式 typed 输出；同目录内的 manifest 与 index 互相自证不构成信任锚。历史 TS3 run 未持久化
> `OutlineStructureSnapshot`，因此验收 runner 只能用同一原 PDF + 冻结 `PageLayout` + 冻结算法
> 确定性重建内部 `OutlineBuildResult`，要求重建的 `DocumentOutline.to_dict()` 与冻结
> `document_outline.json` canonical 字节**完全相等**，随后生成并验证结构快照；不等即停止。

### 18.3.2 `SpanBuildInput`：内部 capability（**无公开构造、无裸 dict、无自报派生结果**）

```python
@dataclass(frozen=True, init=False)
class SpanBuildInput:
    """仅由 build_span_input() 创建的非序列化内部 capability；不是公开 wire input。"""

    page_layout: S.PageLayout                  # 真实版式
    document_outline: S.DocumentOutline        # 真实大纲（TS3 冻结节点集）
    structure_snapshot: SS.OutlineStructureSnapshot  # TS3→TS4 正式结构终态
    evidence_snapshot: A.EvidenceSetSnapshot   # 仅由受信只读 gateway 内部取得
    evidence_blocks: tuple[A.EvidenceBlockInput, ...]  # 同一 gateway 的全量成员
    alignment_records: tuple[S.TextAlignmentRecord, ...]
    alignment_refusals: tuple[S.AlignmentRefusalRecord, ...]
    qualification_policy: SS.SpanQualificationPolicy
    _factory_capability: object                     # 模块私有 identity token，不参与序列化/哈希
```

- `SpanBuildInput` 不导出构造器、没有 `to_dict/from_dict`，并显式禁止 `copy/deepcopy/pickle`；
  `dataclasses.replace` 因 `init=False` 不可用。它只是一次正式调用内部将已核验对象传给纯算法的
  短生命周期 capability，不是来源证明或跨阶段资产。
- TS3→TS4 只通过不可序列化、私有构造的 `VerifiedTS3Handoff` 交接。该 wrapper 同时绑定：
  `VerifiedPageLayout`、经同一 raw PDF / canonical layout source 确定性重建并 canonical 等值的
  `DocumentOutline` / `OutlineStructureSnapshot`、由正式 current Evidence authority 取得的
  `EvidenceSetSnapshot` / 全量 blocks，以及正式 aligner 同次执行签发的
  `VerifiedEvidenceSetAlignment`。三种 wrapper 均无公共构造器、无 `to_dict/from_dict`、禁止
  copy/deepcopy/pickle，并绑定 issuer version、真实根身份与内容指纹。issuer 在模块私有 issued-object
  registry 中登记实例；正式入口必须验证对象由当前进程对应 issuer 实际签发，单靠 exact class、字段、
  私有属性名或 `object.__new__` 均不能取得资格。
- **唯一公开 TS4 builder/verifier 不接受** `page_layout`、`document_outline`、provider、gateway、
  `db_path`、policy path、plain `EvidenceSetAlignment` 或任何调用者装配的 `SpanBuildInput`；只接受
  `VerifiedTS3Handoff` 与 `stage`。provider/gateway 只作为组合根内部私有实现细节。
- live handoff 只能由正式 TS2 layout builder 与正式 aligner 的同一调用链签发：PageLayout 必须从
  raw PDF 重建并全对象 canonical 比较；alignment capability 必须由正式 `align_evidence_set` 执行结果
  当场签发，且该执行本身须消费上层正式 current Evidence authority capability，**不得**由普通
  `EvidenceSetAlignment` 包装。正式 current Evidence gateway 只能由上层
  已验证运行时依赖 capability 内部取得已登记数据库身份，TS4 不提供任意 `db_path` factory。
- live 正向签发链的具名接口固定为：
  `evidence_gateway.bind_current_evidence_authority() -> VerifiedCurrentEvidenceAuthority`（无参数，读取
  service 已预检绑定的 current store，不 init/migrate）；
  `layout_builder.build_verified_page_layout(raw_pdf) -> VerifiedPageLayout`（从 PDF bytes 直接构建，
  不接受调用方给定 PageLayout）；
  `aligner.align_evidence_set_verified(verified_layout, evidence_authority)
  -> VerifiedEvidenceSetAlignment`（同次正式对齐结果签发）；
  `span_builder.issue_live_ts3_handoff(verified_layout, verified_alignment, evidence_authority)
  -> VerifiedTS3Handoff`（内部调用冻结 TS3 builder 重建 DocumentOutline/structure snapshot，**不接受**
  plain DocumentOutline/structure snapshot）。四步任何身份不一致均 fail-closed。
- 历史验收只允许 `ts4_trust_roots.json` 全量锁定并逐成员验证后，由 evaluation-only
  `_issue_pinned_ts3_handoff(...)` 签发；测试 fixture 只走 `_testing_issue_*`。二者均不得注册为生产入口。
  handoff 的 `issuer_scope ∈ {live,pinned_acceptance,testing}` 进入身份并严格分流：公开生产
  `build_span_snapshot` 只接受 `live`；验收 runner 只经不导出的 `_build_from_pinned_handoff` 接受
  `pinned_acceptance`；测试 helper 只接受 `testing`。非 300750 正向验收 fixture 也属于
  `pinned_acceptance`，但 `source_kind="versioned_fixture"`，其 manifest/member hashes 进入身份；
  `testing` 永不进入真实验收 aggregate。跨 scope 复用一律拒绝，三者仅共享私有纯算法核。
- **不得**出现调用方自报的 `line_states` / `table_scopes` / `node_by_line`。行状态只来自经验证的
  `OutlineStructureSnapshot`；表格范围由入口调用冻结的 `OB.table_region_scopes(page_layout)` 重算。
- **闭合要求（缺一项即 `SpanBuildError`）**：
  1. `structure_snapshot.verify_against(page_layout, document_outline)` 通过，逐行集合与真实
     `PageLayout` 非家具行精确闭合；所有 node ID 都存在于真实 `DocumentOutline`，结构快照所绑定的
     `oa/hq/trg/tocr` 版本、layout/outline identity 与真实对象一致；
  2. `document_outline.schema_version == do-4`、`page_layout.schema_version == pl-3`；
  3. 公司 / 文档 / 版本三者一致：`page_layout`、`document_outline`(经 span 的
     `document_id/document_version`)、`evidence_snapshot` 必须同公司同文档同版本
     （复用 `EvidenceSetSnapshot.assert_bound_to(layout)`）；
  4. `evidence_snapshot.assert_current()` 通过，且
     `A.assert_members_exact(tuple(_member_of(b) for b in evidence_blocks), evidence_snapshot)`
     逐位置精确通过；
  5. 每个 `EvidenceBlockInput`：`assert_identity()`（内容哈希 / evidence_id 重算）+
     `assert_bound_to(page_layout)` + `evidence_set_version == evidence_snapshot.evidence_set_version`；
  6. **全终态闭合**：`{r.evidence_block_id for r in alignment_records}` ∪
     `{r.evidence_block_id for r in alignment_refusals}` 与
     `{b.evidence_block_id for b in evidence_blocks}` **完全相等**，且每个 block 恰好一个终态
     （缺失 / 多余 / 重复一律拒绝；`AlignmentRefusalRecord` 是**必须提供**的正式终态，不是"记录缺失"）；
  7. 每个终态的 `page_layout_id == page_layout.page_layout_id`、
     `evidence_set_version == snapshot.evidence_set_version`、`aligner_version == al-3`，且 schema
     分别为当前 `als-3/alr-2`；终态的 `page_number/block_index/evidence_block_id` 必须与对应
     `EvidenceBlockInput` 逐字段相等，并按真实 block 重算 terminal locator / ID；
  8. 每个终态的 `block_char_length == len(tight(evidence_block.text))`（用真实 Evidence 文本重算，
     不采信终态自报）；
  9. `qualification_policy` 必须与 trusted provider 按 `policy_id` 解析出的版本化 authority record
     **canonical 全等**并进入 input fingerprint。**新建** TS4-A 快照时要求
     `distribution_only + threshold=None + V.SPAN_CONFIDENCE_MIN is None`；历史 A 快照在 TS4-B 发布后仍须
     能按其内嵌 policy identity 读回、重建和验证，但 completion 恒 False，不能要求当前全局常量仍为 None。
     **新建** TS4-B 快照要求 `frozen + finite threshold + 用户/Codex批准指纹`，且
     `policy.threshold == V.SPAN_CONFIDENCE_MIN`。完整 typed 因子表、TS4-A 分布/snapshot 哈希与 approval
     authority fingerprint 都必须匹配批准记录；只验证调用者自报 fingerprint 不算通过。两阶段不得得到
     相同 policy 或 snapshot identity。

### 18.3.3 入口内部必须调用冻结函数（不得由调用方自报）

唯一生产组合入口是
`span_builder.build_span_snapshot(handoff: VerifiedTS3Handoff, *, stage) -> SpanBuildSnapshot`；
它内部调用私有 `_build_span_input(handoff, stage) -> SpanBuildInput`，失败抛 `SpanBuildError`。
唯一正式验证入口是
`span_verifier.verify_span_snapshot(snapshot, handoff: VerifiedTS3Handoff) -> VerifiedSpanSnapshot`；
它不得接收 provider/gateway/path/plain roots，而是从 handoff 绑定的真实根对象**重新取得一次**正式输入，
独立重建 expected snapshot 后比较。内部必须：

上述两个公开函数只接受 `issuer_scope=live`。历史验收分别经不导出的
`_build_from_pinned_handoff/_verify_from_pinned_handoff` 接受 `pinned_acceptance`，测试走 `_testing_*`；
三条 wrapper 只负责 scope/issued-registry 门，随后调用同一私有纯构建/验证核，禁止复制第二套算法。

- 由受信 `OutlineStructureSnapshotProvider` 独立重建 expected snapshot，再调用
  `verify_outline_structure_snapshot()` 做 canonical 全对象比较；不接受任意调用方重算哈希后注入的替代快照；
- 调 `OB.table_region_scopes(page_layout)` 得到 `{(page,line): (scope, reason)}`；
- 调 `OB.table_like_region_lines` / `OB.table_row_line_indices` 仅在需要交叉核对时使用；
- 由 handoff 内正式 Evidence authority 重新打开同一已登记数据库身份，读取 Evidence
  snapshot/blocks，并验证连接为 `mode=ro + query_only`；不得从调用参数取得数据库路径；
- 由 `VerifiedEvidenceSetAlignment` 取得全量 typed alignment terminals，再执行逐 block 闭合；plain
  `EvidenceSetAlignment`、调用者重建的 terminal tuple 或“官方 factory + 手造 alignment”一律拒绝；
- 由组合根内固定资产 registry 对应的 `QualificationPolicyProvider` 取得权威 policy：新写 TS4-A 只能解析仓库内
  `distribution_only` 记录；TS4-B 只能解析经用户 + Codex 批准并受版本控制的 frozen approval record。
  历史验证必须按 snapshot 的 `policy_id` 解析相应版本记录，不得用 current policy 重解释历史快照。
  provider 必须核验其 authority fingerprint，且不得接受调用者临时注入记录。

**禁止**：由调用方提交 provider/gateway/path/plain TS3 结果、`SpanBuildInput`，或任何
"已算好的行状态 / 表格范围 / 正文 run / 组件"。
任何这类入参一律不接受（不是"校验后再用"，而是公开签名中**字段不存在**）。T5 必须证明：直接构造、
`object.__new__`、copy/replace 或伪造 provider 指纹并重算全部身份，都没有可注入正式 builder/verifier 的
公开参数；私有 helper 同时核验 module capability，不得被注册为正式入口。

### 18.3.4 可复用 / 禁止

- **允许**复用 `document_structure.aligner` 的**输入类型与纯校验函数**：
  `EvidenceBlockInput`、`EvidenceSetMember`、`EvidenceSetSnapshot`、`assert_members_exact`、
  `snapshot_from_gateway`、`EVIDENCE_SET_STATUSES`、`alignment_terminals`、
  `alignment_terminal_report`。
- **禁止**：重跑对齐（`align_block` / `align_evidence_set` 一律不得在本批调用路径中出现）、
  改 `ALIGN_MIN`、新建第二个 Evidence Set、"补"一个缺失终态、按 `page`/`block` 近似配对。
- **禁止**：把 `line_structure_diagnostic` 的 `body_attachment` 当作正文边界权威（其 docstring 明确
  声明它只是可复核提示，正式未归属状态只以 `DocumentOutline.unassigned` 为准）。TS3 的受信 provider
  可用该诊断生成待验证结构快照；TS4 **不直接调用或采信诊断投影**，只用已通过完整重建验证的
  `OutlineStructureSnapshot` 与 `DocumentOutline`/真实标题行判定边界（§18.6）。

### 18.3.5 `input_fingerprint` 唯一公式与封闭组合根

`input_fingerprint = sha256_canonical(payload)`；`payload` 是封闭 typed 结构，字段不得增删或换序：

```text
page_layout = (page_layout_id, schema_version, engine_version, normalization_version,
               source_file_sha256,
               page_layout_payload_sha256=sha256_canonical(page_layout.to_dict()))
outline = (outline_locator, outline_id, schema_version, outline_algorithm_version,
           heading_profile_version, table_region_version, toc_reconciliation_version,
           outline_payload_sha256=sha256_canonical(document_outline.to_dict()))
structure = (structure_snapshot_id, schema_version, content_fingerprint,
             outline_structure_provider_version, provider_authority_fingerprint)
evidence = (snapshot.fingerprint, snapshot_version, snapshot.gateway_version,
            EVIDENCE_GATEWAY_PROVIDER_VERSION,
            evidence_gateway_authority_fingerprint, member_identity_sha256,
            按(page,block,evidence_id)排序的全部 member.identity)
terminals = 按(page,block,terminal_kind,terminal_id)排序的
            (kind,id,locator,schema_version,aligner_version,partition_validator_version,
             alignment_terminal_provider_version, terminal_provider_authority_fingerprint)
qualification = (policy_id, schema_version, policy_version, stage, threshold,
                 factor_entries, factor_table_fingerprint, approval_authority_fingerprint,
                 bound_ts4a_snapshot_hash, bound_distribution_hash,
                 bound_review_attestation_hash,
                 qualification_policy_provider_version, policy_provider_authority_fingerprint)
rules = (TS4_BODY_SPAN_BUILDER_VERSION, NORMALIZATION_VERSION, ALIGNER_VERSION,
         ALIGNMENT_PARTITION_VALIDATOR_VERSION, SYNOPSIS_VERSION)
handoff = (issuer_scope, source_kind, handoff_identity, handoff_version,
           verified_page_layout_identity, layout_issuer_version,
           verified_alignment_identity, alignment_issuer_version,
           evidence_runtime_authority_identity, evidence_authority_issuer_version)
```

四个受信边界可在模块内用私有 Protocol 分层，但**不作为正式入口参数、不从 `__init__.py` 导出**。
唯一生产 composition root 从 `VerifiedTS3Handoff` 与固定资产 registry 内部解析它们：

- `OutlineStructureSnapshotProvider.rebuild(page_layout, document_outline) -> OutlineStructureSnapshot`；
- `ReadonlyCurrentEvidenceGateway.load_snapshot_and_blocks(company_id, document_id,
  document_version) -> tuple[EvidenceSetSnapshot, tuple[EvidenceBlockInput,...]]`；
- `AlignmentTerminalProvider.load(evidence_snapshot) -> tuple[AlignmentTerminal, ...]`；
- `QualificationPolicyProvider.current_for_new_build(stage) -> SpanQualificationPolicy`；
- `QualificationPolicyProvider.resolve_by_id(policy_id) -> SpanQualificationPolicy`（历史读回/复核）。

各 authority fingerprint 的 canonical payload 唯一如下，均由组合根重算，不采信对象字段；
`testing` 只用于单元测试、永不持久化或进入 aggregate：

| scope/source_kind | structure/layout authority payload | Evidence authority payload | terminal authority payload |
|---|---|---|---|
| `live/current_store` | `("osp-1","live",VPLI,source_file_sha256,page_layout_payload_sha256,outline_payload_sha256,oa/hq/trg/tocr versions)` | `("egp-1","live",VEA,resolved_db_identity,readonly_primitive_version,current_set_query_contract_version,snapshot/member hash)` | `("atp-1","live",VAI,alignment_input_fingerprint,terminal_set_sha256,aligner/partition versions)` |
| `pinned_acceptance/historical_run` | `("osp-1","pinned_acceptance","historical_run",VPLIP,trust_root_file_sha256,pdf/layout/outline artifact SHA256,payload hashes,oa/hq/trg/tocr versions)` | `("egp-1","pinned_acceptance","historical_run",VEAP,trust_root_file_sha256,resolved_db_identity,current_set_query_contract_version,snapshot/member hash)` | `("atp-1","pinned_acceptance","historical_run",VAIP,trust_root_file_sha256,alignment_artifact_sha256,terminal_set_sha256)` |
| `pinned_acceptance/versioned_fixture` | `("osp-1","pinned_acceptance","versioned_fixture",VPLIF,fixture_manifest_sha256,ordered_layout/outline member SHA256,payload hashes,oa/hq/trg/tocr versions)` | `("egp-1","pinned_acceptance","versioned_fixture",VEAF,fixture_manifest_sha256,ordered_evidence_member_sha256,snapshot/member hash)` | `("atp-1","pinned_acceptance","versioned_fixture",VAIF,fixture_manifest_sha256,ordered_terminal_member_sha256,terminal_set_sha256)` |

表内缩写必须对应 §18.12.4 的具名常量，不得写新的字面版本。`resolved_db_identity` 来自
`VerifiedCurrentEvidenceAuthority` 或 pinned root 并写入 run manifest，TS4 无 `db_path` 参数。
handoff identity 再绑定对应 `VTH/VTHP/VTHF`、上述三项 authority hash 与 policy authority hash。
policy authority 见下述逐 policy 稳定公式。

policy provider 使用代码内固定 registry 路径，不接受调用者 path。对**解析出的单个 policy**：
`policy_provider_authority_fingerprint = sha256_canonical((provider_version,
registry_entry_sha256, policy_id, distribution_record_sha256,
approval_record_sha256_or_null, frozen_record_sha256_or_null))`。registry entry 的 canonical 内容必须重算；
TS4-A 只绑定 distribution entry/record，后续新增 B entry/approval/frozen **不得改变历史 A 指纹**；TS4-B
绑定 distribution + approval + frozen 三份记录。替换任一记录并同步重算其内部 ID/hash 仍须因固定 registry
entry 或仓库代码指纹不符而拒绝。

测试 fake 只能由 `_testing_*` 私有 factory 构造；正式 composition root 不接受外部实现。T8b 必须对
上式每一组字段分别改单项并断言 fingerprint 改变，并覆盖：官方 gateway 配替代 DB、官方 terminal
factory 配手造 `EvidenceSetAlignment`、保持 PDF SHA 却篡改 PageLayout 并重算全部身份、伪造完整 frozen
policy 并重算全 ID/hash——四者均拒绝。
`approval_authority_fingerprint` 的唯一非循环公式是
`sha256_canonical(approval_record 去掉 approval_authority_fingerprint 字段后的 payload)`；provider 必须重算，
不得比较记录内的自报字符串。frozen record 再逐字段绑定 approval record 与该重算值。

## 18.4 公共 / 运行时类型与身份

### 18.4.1 TS1 冻结类型的使用面（**不改**，但必须知道边界在哪）

| 类型 | TS4 用法 | 已由 TS1 强制 | **TS1 未强制、TS4 必须自己强制** |
|---|---|---|---|
| `OutlineSpan` | 生产 `sb-2` 正文 span；TS3 `sb-1` unassigned 只由 ID 引用、不复制进新材料集合 | `node_id is None ⟺ role=="unassigned"` 且必带 `unassigned_reason`；`is_fallback ⟺ fallback_derivation`；`char_range` 正长且 `== len(normalized_text)`；`normalized_text` 非空；`content_fingerprint == sha256(normalized_text)`；`layout_line_refs` 严格递增且首尾 == 首尾锚点；`page_range == (start.page, end.page)`；`component_evidence_refs` 唯一且 `0 <= cs < ce`；有 component 必有 `alignment_ids`；`span_locator` / `span_id` 必须等于派生值 | `cs/ce` 的**坐标域**（必须为 Evidence tight 域）与 **上界** `ce <= block_char_length`；每个 `alignment_id` 必属本 span 的 admitted component；`char_range==(0,len)`；`is_cross_heading=False` |
| `NavigationSynopsis` | 生产节点导航简介 | `available ⟹ snippets 非空 ∧ reason_code is None ∧ snippet_index == 位置 ∧ source_span_ids == snippets 的 span_id 去重后按来源序`；不可用时 `snippets == ()` 且 `reason_code ∈ SYNOPSIS_REASON_CODES`；`is_navigation_only()` 恒 True | 每个 snippet 的 `[char_start, char_end)` **必须落在单个最大可引用区间内**（§18.8.3）；`source_span_ids` 必须全部来自**本节点**的可采信 span |
| `TextAlignmentRecord` | 只读输入 | `validate_alignment_partition`：`char_map` 与 residue 并集**恰好覆盖** `[0, block_char_length)`、无洞无重叠、`matched_chars == Σ char_map 段长` | 历史 wire 可读；TS4 正式输入要求存储序升序（§18.5.5）；`block_char_length == len(tight(evidence_text))`（§18.3.4-8） |
| `AlignmentRefusalRecord` | 只读输入（**必须提供**） | 同上分区校验；**同样携带 `char_map`**（实测：1/1 拒绝记录带 `char_map`） | 拒绝记录的 `char_map` **只作非可引用来源留痕**，永不晋级为可引用（§18.8.1） |
| `DocumentOutline.unassigned` | 只读携带 | TS3 已产出（实测 232 个：2024 年 97 / 2025 年 113 / KCZ 21 / 夹具 1，全部 `span_builder_version == "sb-1"`，`role == "unassigned"`） | TS4 **不重算、不重判、不新增** unassigned `OutlineSpan`；`unassigned_reason` 分布必须与冻结产物逐项一致（§18.12.2） |
| 新增：`SpanEvidenceComponent` 等 | TS4 **新增** | —— | 见 §18.4.3 |

> **类型存在性澄清**：TS1 从未提供名为 `SpanComponent` / `SpanSlice` / `SourceSlice` /
> `SpanLocator` / `AlignmentBinding` / 独立 `NavigationProfile` 的类型，本批**不得**声称它们存在。
> §18.4.3 新增的 `SpanEvidenceComponent` / `SpanCitableCoverage` / … 是 TS4 自有的新类型，
> 与上述名字无关。

### 18.4.2 反自证边界（**删除**"`from_dict` 已免自证"）

**唯一结论（R1）**：`to_dict` / `from_dict` 只能证明**字段形状、版本、内部载荷自洽**；
它**不能**证明载荷来自真实 PDF / Layout / Outline / Evidence。调用方可以篡改内容并**重算全部
派生 id 与哈希**，使 `from_dict` 与 `__post_init__` 全部通过。因此必须有三个**对象级验证器**，
并且 TS5 / TS4 验收**必须**调用它们；TS6 及以后只能消费 TS5 再验证后的 final capability（§18.7.3）：

1. 私有 `span_verifier._verify_outline_structure_snapshot(snapshot, handoff) -> OutlineStructureSnapshot`：
   从 `VerifiedTS3Handoff` 绑定的 verified layout/raw-source identity 独立重建 expected snapshot，
   canonical 比较**全部字段及嵌套行状态**；同时逐行重算 line identity、状态、node 归属、counts、版本、
   layout/outline identity 与 fingerprint。snapshot 的 line state 集必须与全部非家具行严格等集，未知、
   缺失、多余、重复或调用者重算全套 ID/hash 的替代快照均拒绝。
2. `span_verifier.verify_span_snapshot(snapshot, handoff: VerifiedTS3Handoff)
   -> VerifiedSpanSnapshot` 成功返回私有构造的运行时 capability wrapper、失败抛
   `SpanVerificationError`。验证器不得接收调用者装配的 `SpanBuildInput`、provider/gateway/path 或 plain
   TS3 对象；它必须从 handoff 绑定的根对象与内部组合根重新取得 capability，
   **独立重建完整 expected snapshot**，与待验 snapshot 做 canonical 全对象等值比较；不得只抽查几个
   span 字段。独立重建至少覆盖（缺一项即拒绝）：
   - 真实大纲 / 节点：每个非 unassigned span 的 `node_id` 必须存在于 `document_outline.nodes`，
     且 `document_outline.outline_locator == span.document_outline_locator`；
   - 真实 Layout：`layout_line_refs` 每一行都必须在 `page_layout` 上存在，`start_anchor` /
     `end_anchor` 的 `bbox` 与真实 `LayoutLine.bbox` 一致；`normalized_text` 必须能由真实行
     **确定性重算**（§18.5.3 的 L→S 公式反推）；
   - Evidence：每个 `component_evidence_refs` 的 `(evidence_id, cs, ce)` 必须能定位到真实
     `EvidenceBlockInput`，`ce <= block_char_length`，且其 `[cs, ce)` 必须被终态 `char_map` 的
     段并集**覆盖**（§18.5.2 的 walk 逐段成立）；
   - 身份：`span_locator` / `span_id` / `content_fingerprint` 必须等于按真实上游对象重算的值
     （不是"等于载荷里写的值"）；上游身份（`page_layout_id`、`outline_locator`、
     `evidence_set_version`、`snapshot.fingerprint`）必须与真实对象一致；
   - 终态：用 `span.verify_alignment_closure(tuple(records_by_id[a] for a in span.alignment_ids))`
     核验（**不得**传全文档记录列表：`alignment_closure_errors` 会把未引用的记录判为 extra）。
   - 集合闭合：dispositions、TS4 regular spans、components、coverages、conservation 与 synopses
     都必须与重建结果精确等值；每个 regular span 恰一份 coverage，每个真实
     outline node 恰一份 synopsis，无缺失/多余/重复；`input_fingerprint` 与资格策略身份一并重算。
   - 两阶段顺序固定：先由受信输入构造私有 `SpanBuildDraft` 并封装 raw `SpanBuildSnapshot`；与待验
     snapshot canonical 全等后，再用该 expected draft 的 span/coverage/policy registries 逐 synopsis
     生成 `SynopsisSourceValidation`，最后连同 raw snapshot 封装 `VerifiedSpanSnapshot`。validation 不得
     反向成为 raw snapshot 的构造输入，杜绝“先 verified 才能生成 validation”的环。
3. `synopsis_verifier.verify_synopsis_sources(synopsis, *, span_registry,
   coverage_registry, verified_policy, page_layout, records_by_id) -> SynopsisSourceValidation`
   独立重算：片段 `[char_start, char_end)` 落在哪个 span-local 域、是否被**单个**最大可引用区间
   连续覆盖、`normalized_text[cs:ce]` 与 snippet 文本是否逐字一致、来源 span 是否**可采信**
   （`node_id == synopsis.node_id` 且导航资格成立）、`source_span_ids` 是否就是这些 span 的
   去重来源序、`synopsis_locator` / `synopsis_id` 是否等于重算值。

**反例测试（强制）**：篡改内容 **并重算全部派生 id / 哈希**（`span_id`、`span_locator`、
`content_fingerprint`、`synopsis_id`、`snapshot_id`），使 `from_dict` 与 `__post_init__` 全部通过，
`verify_span_snapshot` / `verify_synopsis_sources` **仍必须拒绝**（因为它重算的是真实上游对象上的值）。
仅 `verify_span_snapshot` 能创建 `VerifiedSpanSnapshot`；单独调用 synopsis verifier不授予消费资格。
`VerifiedSpanSnapshot` 同时绑定内部取得的 qualification policy、`VerifiedTS3Handoff` identity 与各
authority fingerprint；调用方手造一套自洽 snapshot/input/provider fingerprint 不能取得该 capability。

### 18.4.3 TS4 新增类型（严格 typed、版本化、确定性身份、可读回）

**新增类型全部放 `document_structure/span_schema.py`**（不改 `schema.py` 的既有类型），
共用 `document_structure.canonical` 的原语（`SchemaValidationError` / `identity` / `locator` /
`canonical_text` / `to_json_value` / `is_finite` / `sha256_canonical`）。每个类型都必须有：

- 明确字段（下表）；**schema 版本**（自有常量，见 §18.12.4）+ **算法版本**（`sb-2` / `ns-2` / qualification policy）；
- 确定性身份：`<name>_locator = locator("<kind>", payload)`、`<name>_id = identity("<kind>", payload)`，
  payload 只含已规范化的字段（`FLOAT_PRECISION` 量化后）；
- 严格 `to_dict` / `from_dict`：未知字段**拒绝**、缺字段**拒绝**、类型不符**拒绝**、
  NaN / ±Inf **拒绝**（复用 `to_json_value`）；`from_dict` 只做形状 + 版本 + 内部自洽校验
  （§18.4.2），不做来源校验；
- 上游对象校验入口（`verify_*(...)`，见 §18.4.2 与各类型行）。

| 类型 | schema 版本 | 覆盖内容 | 关键字段 |
|---|---|---|---|
| `OutlineStructureSnapshot` | `obs-1` | TS3→TS4 的正式结构终态；替代不可持久化的 `OutlineBuildResult` | `structure_snapshot_locator/id` / `schema_version` / `outline_algorithm_version` / `heading_profile_version` / `table_region_version` / `toc_reconciliation_version` / `document_id/version` / `page_layout_id` / `outline_locator/id` / `line_states`（每个非家具行恰一条 typed `LineStructureState`）/ `counts` / `content_fingerprint` |
| `SpanQualificationPolicy` | `sqp-1` | 把 TS4-A/B 资格解释绑定进身份 | `policy_locator/id` / `schema_version` / `policy_version` / `provider_version` / `stage ∈ {distribution_only,frozen}` / `threshold: float\|None` / `factor_entries: tuple[BoundaryFactor,...]`（完整值，不只存哈希）/ `factor_table_fingerprint`（由 entries 重算）/ `approval_authority_fingerprint: str\|None` / `bound_ts4a_snapshot_hash: str\|None`（固定序 aggregate snapshot manifest 的文件 SHA256）/ `bound_distribution_hash: str\|None` / `bound_review_attestation_hash: str\|None` / `content_fingerprint` |
| `BodyRangeDisposition` | `sps-1` | 正文域**每个最大范围一个记录**：range disposition / table deferred / table adjacency pending / empty / unassigned | `disposition_locator/id` / `schema_version` / `algorithm_version=sb-2` / `range_kind ∈ {regular,table_inside,table_adjacency,unassigned,empty}` / `node_id \| None` / `unassigned_reason \| None` / `table_scope/reason` / `left_boundary_cause/right_boundary_cause` / `page_range` / `layout_line_refs` / anchors / tight/line counts / `normalized_text \| None` / `span_id \| None`（仅 regular 非空）/ `content_fingerprint`；最大范围合并键固定为“源序连续 + 同 range_kind/node/table_scope/table_reason/boundary ownership”，任一键变化即切断 |
| `SpanEvidenceComponent` | `spc-1` | 组件 provenance：**含不可引用者、不可核验 map 与 residue**；terminal/disposition/span 三向绑定 | `component_locator/id` / `schema_version` / `algorithm_version` / `span_id \| None` / `disposition_id \| None` / `node_id \| None` / `evidence_block_id/set_version` / `terminal_kind ∈ {alignment,refusal}` / `terminal_id`（始终非空）/ `terminal_locator` / `terminal_schema_version` / `verdict: str\|None` / `refusal_reason: str\|None` / `residue_class: str\|None` / `evidence_char_range` / `span_local_char_range \| None` / `layout_hits` / `admitted` / `admission_reason` / `landing ∈ {body_span,table_inside,table_adjacency,body_unassigned,body_empty,heading_node,formal_unassigned,non_content,outside_body,alignment_offset_unverifiable,alignment_residue_unmapped}` / `content_fingerprint` |
| `SpanCitableCoverage` | `spv-1` | span-local 覆盖区间（**区间并集**，非求和） | `coverage_locator/id` / `schema_version` / `algorithm_version` / `span_id` / `span_local_length` / `required_content_intervals` / `citable_source_intervals` / `non_citable_source_intervals` / `uncovered_source_intervals` / `normalization_only_intervals` / `effective_citable_intervals` / 计数 / `covering_component_ids` / `content_fingerprint`；四类基础区间两两不交且并集恰好为 `[0,len)`，effective 仅为可重算派生值 |
| `SpanConservation` | `spr-1` | 三级守恒结论 | `conservation_locator` / `conservation_id` / `schema_version` / `algorithm_version` / `document_id` / `document_version` / `page_layout_id` / `outline_locator` / `evidence_set_version` / `document_layer`（`H/F/N/B` 行数与 tight 字符数）/ `body_layer`（`S/A/T/U/E` 行数与 tight 字符数）/ `evidence_layer`（每条 Evidence 的 `[0, block_char_length)` 分区结论 + 跨界落点计数）/ `conserved` / `gaps`（typed 缺口行）/ `content_fingerprint` |
| `SpanBuildSnapshot` | `spn-1` | **完整原始构建结果**（可序列化，但不自带“已验证”能力） | `snapshot_locator/id` / `schema_version` / `span_builder_version=sb-2` / `structure_snapshot_id` / `qualification_policy`（嵌入完整对象，而非只存 ID）/ 文档/layout/outline/evidence 身份 / `terminal_count` / `input_fingerprint` / `dispositions` / `spans`（**只含 TS4 regular spans**）/ `inherited_unassigned_span_ids`（精确引用 `DocumentOutline.unassigned`，不复制为本 evidence set 的材料）/ `components` / `coverages` / `conservation` / `synopses` / `content_fingerprint`；**不含**验证结论 |
| `SynopsisSourceValidation` | `nsv-1` | synopsis 来源复核结论 | `validation_locator` / `validation_id` / `schema_version` / `algorithm_version`（`ns-2`）/ `node_id` / `synopsis_id` / `snippet_checks`（逐片段：span-local 区间 / 覆盖它的可引用区间 / 重算文本哈希）/ `ok` / `problems` |

**嵌套类型规则（不得退化为自由 dict/list）**：至少按下表冻结字段；其余均采用 frozen 子结构或精确
tuple schema。每个封闭 reason/landing/admission/boundary 词表集中声明，顺序、唯一性、空值、区间互斥与
unknown-key 拒绝均须测试。

| 子类型 | 必需字段 / 判别规则 | 顺序与唯一键 |
|---|---|---|
| `LineStructureState` | `page_number,line_index,line_identity,state,node_id\|None,body_attachment\|None,reason_code`；各 state 的 node/attachment 空值真值表封闭 | `(page_number,line_index)` 严格升序且唯一，恰好覆盖非家具行 |
| `BoundaryFactor` | `side ∈ {left,right}, cause, factor`；factor 有限且量化 | `(side,cause)` 唯一并按固定词表顺序；全集恰好等于 §18.8.5 |
| `LayoutHit` | `page_number,line_index,span_index,line_char_range,span_char_range,bbox` | 按源坐标升序、区间非空、同 component 不重叠 |
| `ClosedInterval` | `start,end`，`0 <= start < end <= domain_length` | 严格升序、不重叠、相邻同类必须预先合并 |
| `EvidenceConservationRow` | `evidence_id,block_char_length,mapped_intervals,unverifiable_intervals,residue_intervals,landing_counts,conserved,problems` | 按权威 member 顺序；evidence_id 唯一，三支区间互斥完备 |
| `ConservationGap` | `gap_id,layer,reason_code,source_identity,interval_or_line_ref,detail` | `(layer,source_identity,interval_or_line_ref,reason_code)` 唯一、规范排序 |
| `SnippetCheck` | `snippet_index,span_id,char_range,source_text_hash,covering_effective_interval,ok,problems` | snippet_index 连续；与 synopsis.snippets 一一对应 |

`VerifiedSpanSnapshot` 是**不可序列化的运行时 capability wrapper**，不进入上述 8 个 wire 类型：构造器
私有，只能由 `verify_span_snapshot()` 返回，字段为 `raw_snapshot`、`verified_policy`、
`synopsis_validations`、`trusted_input_fingerprint`，以及经同次验证绑定的只读根对象
`page_layout/document_outline/structure_snapshot/evidence_snapshot/evidence_blocks/alignment_terminals`
与 `VerifiedTS3Handoff` identity、四个内部 provider/gateway authority identities。根对象不序列化，
磁盘 raw snapshot 读回后必须重新取得并
验证才能恢复 capability。TS5 与 TS4 completion predicate 只接受该 wrapper；
TS6 及以后只接受 TS5 的 `VerifiedFinalMaterialStructureSnapshot`；
从磁盘读回 raw snapshot 后必须重新验证才能取得能力，不能凭类型名或布尔字段自报已验证。

**规则**：

- `table_deferred_ranges.json` / `span_components.jsonl` 等**只是** `SpanBuildSnapshot` 的
  验收投影（§18.14），**永远不是**生产真值；
- TS5 **必须**消费经 `verify_span_snapshot` 校验后的 wrapper；TS6 / TS7A / TS7B 必须消费 TS5 的
  `VerifiedFinalMaterialStructureSnapshot`，不得跳过 TS5；所有正式消费者均**不得**读
  `evaluation/results/**`；
- 新增类型使用 §18.12.4 的独立但同等权威登记表，并与既有登记组成全局无重名并集；**无 SQLite migration**。

### 18.4.4 封闭词表：新增项与决策

| 词表 | 处置 | 理由 |
|---|---|---|
| `SPAN_ROLES` | **不变**（`body, list, table_caption, table_note, unassigned`） | 表格相邻 provisional 范围**不是** span（§18.7.2），因此不需要新 role；也不得预判 `table_caption` / `table_note`（那是 TS5 的几何决策） |
| `SYNOPSIS_REASON_CODES` | **追加 `table_only_pending_ts5`**（已批准） | 节点内容全部落在表格延期 / 相邻待决范围时，其简介必须显式"等 TS5"，不能与 `no_span` 混淆 |
| `UNASSIGNED_REASONS` | **不变** | TS4 不新增 unassigned 语义（§18.6.2）；`below_last_heading` **只作携带值**保留在词表内 |
| `ALIGNMENT_VERDICTS` | **不变**（仅 `aligned` 可引用） | 拒绝记录的 `char_map` 只作留痕 |
| `TABLE_SCOPES` | **不变**（`inside_table` / `adjacent_to_table` / `none`） | 复用 `trg-3` 冻结判据，不改表格污染规则 |

## 18.5 坐标映射与 `char_map` 核验

### 18.5.1 四个坐标域（本节全部公式只用这四个域）

| 域 | 名称 | 定义 | 长度 |
|---|---|---|---|
| `T_e` | Evidence tight 域 | `tight(evidence_block.text)`（`"".join(text.split())`，去**全部**空白） | `block_char_length` |
| `L_s` | LayoutSpan 原域 | `LayoutLine.spans[span_index].text` 内的偏移 `char_offset` | `len(layout_span.text)` |
| `L_l` | LayoutLine 原域 | `LayoutLine.text` 内的偏移；`LayoutSpan.char_start/char_end` 就定义在此域 | `len(layout_line.text)` |
| `S` | span-local 规范化域 | `span.normalized_text` 的下标，`span.char_range == (0, len(normalized_text))` | `len(canonical_text(joined))` |

`component_evidence_refs` / `SpanEvidenceComponent.evidence_char_range` 用 **`T_e` 域**；
`SynopsisSnippet.char_start/char_end` / `SpanCitableCoverage.*_intervals` 用 **`S` 域**；
`char_map` 的 `char_offset` 用 **`L_s` 域**；投影到行时必须加真实
`LayoutSpan.char_start` 才进入 `L_l`。四域之间**禁止**混用位移。

### 18.5.2 `T_e → L`：walk 公式（**替换** 旧的 `block[bs:be]` 直接切片）

对 `char_map` 的每一段 `(bs, be, page, line, span_index, char_offset)`：

```text
layout_line = page_layout[page].lines[line]
layout_span = layout_line.spans[span_index]
raw = layout_span.text
step1 命中：0 <= char_offset < len(raw) 且 raw[char_offset] 非空白
step2 走读：从 char_offset 起向后扫描，只累计**非空白**字符，直到累计到 (be - bs) 个；
          span_stop = 走读结束处的 LayoutSpan 域下标；walked = raw[char_offset:span_stop]
step3 等值：tight(walked) == tight(evidence_text)[bs:be]
step4 行内定位：line_start = layout_span.char_start + char_offset；
              line_stop = layout_span.char_start + span_stop；
              layout_line.text[line_start:line_stop] == walked
```

- **禁止** `raw[char_offset : char_offset + (be - bs)]`（把 tight 计数当原域位移）。
- 三段任一不成立 → 该段 `offset_unverifiable`，其覆盖的 Evidence 区间**不得**进入
  `admitted=True` 组件（fail-closed），但仍必须生成 `SpanEvidenceComponent` 记录
  （`admitted=False`、`landing="alignment_offset_unverifiable"`、`layout_hits=()`、
  `span_local_char_range=None`，`admission_reason` 说明），不得从正式 provenance 中消失，也不得冒充
  已有 Layout 落点。
- 实测：本公式在冻结产物上 **63418 / 63418** 成立（§18.1.3）。

### 18.5.3 `L → S`：行内映射与 span-local 映射（确定性，可重算）

1. **span 文本重构（确定性规范化重构）**：
   `normalized_text = " ".join(canonical_text(line.text) for line in run_lines)`。
   引理：`canonical_text(" ".join(parts)) == " ".join(canonical_text(p) for p in parts)`
   （逐行归一再以单空格相连，与整体归一等价；须有测试）。
2. **行在 `S` 域中的落点**：设 run 的行序列为 `l_1..l_k`（**均无空行**，空行已归 `E_empty` 而
   不进 run），令 `off_i = Σ_{j<i} (len(canonical_text(l_j.text)) + 1)`，则
   `l_i` 占 `S` 域区间 `[off_i, off_i + len(canonical_text(l_i.text)))`（`i<k` 时末尾的 1 为连接空格）。
   同时重算 `normalization_only_intervals`：所有由原始空白折叠产生的单空格，以及相邻行之间人为插入的
   单空格，均属于 normalization-only；它们没有 `T_e` offset，**不是** Evidence 缺口。
3. **行内 `L_l → S`**：对真实 `LayoutLine.text`（记 `line_raw`）与行内偏移 `o`，
   `Lline_to_S(line_raw, o) = len(canonical_text(line_raw[:o]))`。当前规范化只折叠空白，任意
   `0 <= o <= len(line_raw)` 都可确定性映射；**不得**添加“前一字符必须为空白”的伪条件。
4. **`T_e → S` 投影**：`T_e` 区间 `[cs, ce)` 经 `char_map` 段先落到 `L_s`，再按
   `line_start = layout_span.char_start + char_offset`、`line_stop = layout_span.char_start + span_stop`
   落到 `L_l`，最后得到
   `[off_i + Lline_to_S(line_raw,line_start), off_i + Lline_to_S(line_raw,line_stop))`。
   越界、真实切片不等或端点跨出本 run 时标记 `offset_unverifiable/boundary_inexact`，不得计入
   可引用覆盖或 snippet，但保留在组件记录中。
5. `SpanCitableCoverage.citable_source_intervals` = 全部 `admitted=True` 且投影可定义的 `S` **非空白
   来源字符**区间的并集（排序 + 合并）；`required_content_intervals = [0,len) -
   normalization_only_intervals`。四类基础区间按 §18.8.2 分账。

### 18.5.4 可引用覆盖 = **区间并集**（禁止跨 Evidence 直接相加）

- 来源可引用覆盖**只能**由 `S` 域区间求并集得到；**禁止**把多个 Evidence 的 `char_map` 段长相加，
  更**禁止**用 `min(1.0, ...)` 之类方式掩盖重复覆盖。
- **不成立**："一个 span 里有一条 `aligned` Evidence ⇒ 整个 span 可引用"。必须逐区间判定：
  所有 `required_content_intervals` 都要被来源并集覆盖。`normalization_only_intervals` 仅是确定性排版
  分隔符：不要求 Evidence offset，也不能掩盖任一非空白来源字符缺口。
- 重叠 Evidence（同一段 `S` 文本被两条 Evidence 覆盖）在并集中**只算一次**，且**不得**抬高
  `confidence`（§18.8.5）或覆盖计数（须有反例测试）。

### 18.5.5 乱序 `char_map`：TS4 正式输入边界拒绝，不改冻结 wire 语义

- TS4 **不得**对终态的 `char_map` 排序。
- 现状事实：`validate_alignment_partition` 在**排序副本**上校验不重叠与覆盖，因此**存储序升序
  当前未被强制**；实测 4 份冻结产物 769 条终态 **0 条乱序**，故收紧为**零破坏**。
- `als-3/alr-2` 是冻结 TS3 wire 语义，本批不在原版本上改变 `from_dict` 接受集合；乱序对象允许按
  历史 schema 读回，但必须在 `build_span_input` / `verify_span_snapshot` 的 TS4 正式输入边界被拒绝。
- 测试：删除“TS4 排序后继续”用例；新增“历史 from_dict 可读、TS4 正式入口拒绝乱序”的兼容测试。

## 18.6 span 边界算法

### 18.6.1 最大正文 run 的定义（7 条，全部只依赖冻结 TS3 事实）

输入：经 `verify_outline_structure_snapshot()` 通过的
`OutlineStructureSnapshot.line_states`（按 `(page,line)` 升序）与基于受信 `PageLayout` 重新计算的
`table_region_scopes(page_layout)`。正式 builder **不得接收** `outline_result`、调用者传入的
`line_states/table_scopes` 或任意自报投影。**只有 `structure_state == "body_under_node"` 的行**参与正文域。

1. 按 `(page, line)` 升序遍历 `body_under_node` 行；**行状态或表格范围发生变化**即切断 run：
   - 遇到非 `body_under_node` 行（`heading_node` / `formal_unassigned` / `non_content`）→ 切断；
   - `table_scope == "inside_table"` 的行 → 归 `T_inside_table`（**不进入**段落 run，也不产生 span）；
   - `table_scope == "adjacent_to_table"` 的行 → 归 `A_adjacent_provisional`（**独立 provisional
     范围**，不与普通正文同 run，见 §18.7.2）；
   - 行 `tight(text) == ""`（空白行）→ 归 `E_empty`，切断 run（保证 §18.5.3 引理前提）。
2. 一个 run 必须**连续**且**同一 `owning_node_id`**（`body_attachment == "preceding_heading"`）。
3. 跨页 run 允许（`page_range` 覆盖两页）。
4. run 的 `start_anchor` / `end_anchor` = 首/末行的 `(page, line, bbox)`，取自真实 `LayoutLine.bbox`。
5. `layout_line_refs` = run 内全部 `(page, line)`，严格递增（由遍历顺序保证）。
6. `normalized_text` = §18.5.3-1 的确定性规范化重构；**非空**（空行已被切除）。
7. 一个 run ⇒ **恰好一个** `OutlineSpan`（`role="body"`，`node_id = owning_node_id`，
   `is_cross_heading=False`，`is_fallback=False`，`char_range=(0, len(normalized_text))`）。
   **节点允许零个 span**：若某节点无任何合格 run，不得为它造空 span 或占位 span；其"终端状态"
   由 `BodyRangeDisposition` 的 `range_kind ∈ {table_inside, table_adjacency, empty, unassigned}`
   与该节点的 `NavigationSynopsis(unavailable, reason_code)` 共同表达。

### 18.6.2 节点 ↔ span 关系（**替换** "每节点都有 span"）

- 合格最大正文 run ⇒ 一个 span；**节点零个 span 是合法终态**，且必须能被区分：`no_span`
  （该节点无任何正文内容）/ `table_only_pending_ts5`（内容被表格范围占用）/ `empty_text`
  （仅有空白行）/ `length_exceeded`（长度约束无法满足，§18.8.6）。
- TS4 **不新增** unassigned `OutlineSpan`：不可归属的正文范围（`body_attachment` 为
  `before_first_heading` / `after_formal_unassigned_boundary`，或诊断与大纲不一致）以
  `BodyRangeDisposition(range_kind="unassigned")` 表达，`unassigned_reason` 取词表内既有值
  （`no_heading_context` / `boundary_ambiguous`），**不产生** `OutlineSpan`。
- TS4 **不重算、不重判、不新增** TS3 的 unassigned span：TS3 的 232 个 `sb-1` unassigned span
  留在 `DocumentOutline.unassigned`；`SpanBuildSnapshot.inherited_unassigned_span_ids` 只做精确引用，
  不把这些 `no-evidence-set` 对象混进当前 Evidence 集的 `spans/components/coverages`。
- 防御：若 TS4 产生的任何 `span_id` 与 `document_outline.unassigned` 的 `span_id` 相同，
  **立即 fail-closed 停止**（不得静默合并或覆盖），并在报告中列为版本冲突。

### 18.6.3 删除 `below_last_heading` 伪状态，但不越过真实结构边界

- **规则**：最后一个正常节点的**所有权候选**可以延续到文档结尾，但每个 regular run 仍必须在
  `formal_unassigned`、`non_content`、`table_inside`、`table_adjacency`、`empty` 等真实边界处切断。
  只有最后一个 regular run **实际抵达**最后一行非家具行时，才使用“右端 = 文档末”档；不得把
  中间边界后的文字倒灌回最后一个 span。
- TS4 **不产出** `below_last_heading`：`UNASSIGNED_REASONS` 保持词表不变（只作携带值），
  TS4 的任何判定分支都不得返回它。
- **删除**旧 §18.5 中"末节点正文因无后随标题判 `below_last_heading`"的规则与其测试；
  **新增**正向测试：末节点正文归属到文档末且 confidence 为该档定值；**新增**负向测试：末节点
  正文**不**被任何 unassigned 处置吞掉。
- 实测支持：4 份冻结产物的 232 个 unassigned span 中 `below_last_heading` **出现 0 次**
  （实际出现的 reason 为 `toc_unmatched` / `insufficient_heading_evidence` /
  `numbered_list_ambiguity` / `hierarchy_conflict`）。

### 18.6.4 术语与权威表述修正

- `canonical_text` 是**确定性规范化重构**，**不是**"无损原文重构"：原文无损性由 `PageLayout`
  与 `(page, line, span_index, char_offset)` 定位器保证，规范化文本只用于 span-local 下标与
  snippet 切片。
- 权威顺序按 §18.1.4：**设计优先**；后继规格只能细化设计**明确允许**的字段草图；语义冲突必须
  上报（§18.19），不得在本节内自行消解上位设计。

## 18.7 表格边界裁决（**替换** 旧 A5/A6）

### 18.7.1 `inside_table` → `deferred_to_ts5`

`table_region_scopes` 判为 `inside_table` 的行（`trg-3` 冻结判据）：

- 归 `BodyRangeDisposition(range_kind="table_inside")`，保留原文本、`page_range`、
  `layout_line_refs`、`table_scope` 与 `table_region_reason`；
- **不产生**段落正文 span，**不得**进入 `NavigationSynopsis`，**不得**作为正式段落材料；
- **不得**降级为"已知可接受残留风险"；它是一条**显式延期项**，必须在验收投影中逐条可见。

### 18.7.2 `adjacent_to_table` → `table_adjacency_pending_ts5`（独立 provisional 范围）

- 归 `BodyRangeDisposition(range_kind="table_adjacency")`；该 `range_kind` 即本批固定的
  provisional 处置值（语义等价于"`table_adjacency_pending_ts5`"）；
- **必须**：独立成范围，**不与普通正文同 run**；不得被声称为表格成员；原文本与定位器保留；
- **在 TS5 决议前**：**不得**进入 `NavigationSynopsis`，**不得**作为正式段落材料，
  **不得**计入可引用覆盖或 confidence；
- 若某节点的内容**全部**落在 `table_inside` / `table_adjacency`，其简介状态为
  `synopsis_unavailable`，`reason_code = table_only_pending_ts5`。
- 该 provisional 范围**不产生** `OutlineSpan`（避免用冻结 `role` 词表预判 caption / note / prose）。

### 18.7.3 TS5 决议接口（TS4 只冻结输入；TS5 以不可变 overlay 实现）

`BodyRangeDisposition` 是不可变 TS4 初态，TS5 **不得原位改写**。TS5 必须新增版本化、确定性身份的
`TableRangeDecision(disposition_id, decision, target_id|None, reason_code, geometry_evidence)`
overlay；每个 `table_inside/table_adjacency` disposition 恰好一个 decision，且允许诚实未决：

| 决议 | 条件（TS5 判定，几何） | 后果 |
|---|---|---|
| `absorbed_as_caption` / `absorbed_as_unit` / `absorbed_as_note` | 该范围与某个 `TableObject` 的几何区域/位序构成表题、单位或表注 | 由 `TableObject` 经 typed 关系吸收/引用；**不再**作为段落 |
| `absorbed_as_table_body` | `table_inside` 成功恢复为某个 TableObject 的表头/表体/合计成员 | 绑定目标 table/component；原 disposition 保留 |
| `kept_as_paragraph` | 该范围为普通正文（不与任何表格几何相邻成立） | 转为 `role="body"` 的段落 span（TS5 批次按 §18.12 的版本规则处理） |
| `absorbed_into_body` | 该范围与相邻正文连续且属同一段落 | 并入相邻段落 span（合并后重算身份） |
| `unsupported_table_structure` | `table_inside` 有真实范围但不足以构造 TableObject | 保留全文、provenance 与 partial/gap；不得消失 |
| `unresolved_geometry` | 几何证据不足以唯一裁决 | 保持 pending；不得进入段落/synopsis，也不得假装已吸收 |

`target_id` 真值表固定为：前三类 `absorbed_as_caption/unit/note` 与
`absorbed_as_table_body` 必须是 `table_id`；`kept_as_paragraph` / `absorbed_into_body` 必须是最终
`span_id`；`unsupported_table_structure` / `unresolved_geometry` 必须为 `None`。`target_kind` 采用封闭
判别值 `table_object|final_span|none` 并与上述决议严格联动，禁止仅凭字符串猜目标类型。

决议必须互斥；被表格吸收的范围不得同时成为段落。TS5 新增独立算法版本
`TS5_FINAL_SPAN_BUILDER_VERSION="sbf-1"`（符合现行版本字面量只允许小写字母前缀的规则）；任何
`kept_as_paragraph/absorbed_into_body` 所生成或合并的
最终段落 span 都使用该版本，不能继续冒充 TS4 `sb-2`。

TS5 还必须新增版本化 `FinalComponentBinding` overlay：其 `component_id` 集合与经验证 TS4 snapshot 的
**全部 component_id 精确等集**；每条可附带原 `disposition_id|None`，记录
`admission ∈ {final_span,table_object,pending,rejected}`、目标 `span_id|table_id|None` 与理由。regular
component 也必须映射，即使原 disposition_id 为 None 或 TS5 合并后 span_id 改变；任何 component 不得遗漏，
且一个源组件
不得同时进入 final span 与 table。

TS5 最终产出版本化 `FinalMaterialStructureSnapshot`，绑定经验证的 TS4 snapshot、全部
`TableRangeDecision`、`FinalComponentBinding`、`TableObject to-4`、最终 spans、最终 typed coverage、
最终 conservation 与**基于最终 spans/tables 重新生成**的 synopses。原 TS4 snapshot 保留为不可变上游，
不得被就地覆盖。`final_verifier.verify_final_material_snapshot(...) ->
VerifiedFinalMaterialStructureSnapshot` 必须从经验证 TS4 输入与正式 TS5 builder 独立重建全部 final 对象，
canonical 全比较后才返回不可直接构造、不可序列化的 capability wrapper；删/增/改 decision、binding、
span/table、coverage、conservation、synopsis 并重算所有 ID/hash 仍须拒绝。

TS5 的 **TS4-material capability** 只接受 `VerifiedSpanSnapshot`，并从 wrapper 取得同一身份的真实
PageLayout/DocumentOutline/Evidence/terminal 根对象来构建几何与表格，不能只看 raw snapshot 投影；
TS6/TS7A/TS7B 的 **final-material capability** 只接受 `VerifiedFinalMaterialStructureSnapshot`，不得直接
消费原始 TS4 snapshot 或 raw final JSON。它们仍分别接收自身权威输入（TS6 的冻结 Contract/profile，
TS7A 的 aspect 与多文档 final wrappers，TS7B 的 Store/Pack authority），不得把 capability 误当唯一业务输入。
TS4 验收只检查每条表格范围具备 `disposition_id`、原文本、精确 locator、全量 Evidence components，
足以供 TS5 决议。

当前 `TableObject(to-3)` 强制非空 `component_span_ids`，而 TS4 不为 table body 造假段落。
因此 TS5 编码前必须发布经审查的 successor（推荐 `to-4`）：表体以
`SpanEvidenceComponent.component_id/disposition_id` 做原子 provenance，caption/note 等仍可绑定
真实 OutlineSpan；或提出能在不混淆表体与段落语义的等价方案。不得把空 span、普通 body span 或
字符串 ID 填入 `to-3` 以绕过门禁。`to-3` 只允许经显式 legacy/history reader 读取，不得 current、
不得转换成 final capability、不得由 TS6/TS7 消费；`to-4` 读器遇到 `to-3` 必须显式拒绝并要求重建，
不得静默补 component/disposition 字段。该 legacy/rebuild 矩阵须在 TS5 规格冻结时写入测试。

## 18.8 provenance、可引用资格与 synopsis

### 18.8.1 provenance ⊥ 可引用资格（**两件事，不得合并**）

- **provenance（来源）**：每一条把 Evidence 与 span / 范围关联起来的事实，无论 verdict 是
  `aligned` / `partially_aligned` / `unaligned` / 拒绝，都必须产出 `SpanEvidenceComponent` 记录，
  **保留来源身份、verdict、坐标与拒绝原因**。provenance 必须留在**正式 typed 快照**里。
- **citable（可引用资格）**：只有 `verdict == "aligned"` **且** §18.5.2 的 walk 逐段成立
  **且** §18.5.3 的投影可定义 **且** 落在正文域（`landing == "body_span"`）的区间，
  才可 `admitted=True`。
- 不合格区间**不得**进入可引用组件，但**也绝不允许**从正式 provenance 消失到临时 eval JSON 里：
  `SpanBuildSnapshot.components` 必须包含全部组件（含 `admitted=False`）。
- 拒绝终态（`AlignmentRefusalRecord`）的 `char_map` **只能**用于非可引用的来源留痕审计
  （`admitted=False`，`admission_reason="refusal_record"`），**永不晋级**。
- terminal 联合必须忠实：alignment 分支使用 `terminal_id=alignment_id`、`verdict!=None`、
  `refusal_reason=None`；refusal 分支使用 `terminal_id=refusal_id`、`verdict=None`、
  `refusal_reason!=None`。两分支都必须携带真实 terminal locator/schema version；不得用空 alignment ID
  或伪造 verdict 表示 refusal。
- `landing==body_span` 时恰好绑定 `span_id`；`table_inside/table_adjacency/body_unassigned/body_empty`
  时恰好绑定 `disposition_id`；标题/正式未归属/非内容/outside/residue 不伪造 span/disposition 身份。
- `alignment_offset_unverifiable` 与 `alignment_residue_unmapped` 都不绑定 span/disposition、
  `layout_hits=()` 且永远 `admitted=False`；前者保留失败 char-map 区间，后者逐 residue 段保留
  `residue_class`，两者不得合并或互相冒充。
- `OutlineSpan.component_evidence_refs` **只**等于该 span 中 admitted、aligned、投影精确的组件区间；
  `OutlineSpan.alignment_ids` 等于这些组件对应的 alignment terminal ID 按源序去重。snapshot 的全量
  components 可以多于 span 引用，但 admitted 集与 span 两字段必须双向精确闭合。

### 18.8.2 覆盖必须来自**区间并集**（见 §18.5.4）

`SpanCitableCoverage` 是"span 可引用性"的唯一正式载体：区间合并后的
`citable_source_intervals` / `non_citable_source_intervals` / `uncovered_source_intervals` /
`normalization_only_intervals` 与可重算的 `effective_citable_intervals`。**禁止**：
多 Evidence 覆盖直接相加；`min(1.0, ...)` 掩盖重复；用"存在一条 `aligned`"代替逐区间判定。

四类基础区间定义：`normalization_only` 由 §18.5.3 的原始空白折叠/行连接映射重算；
`required_content=[0,len)-normalization_only`；`citable_source=union(admitted)∩required_content`；
`rejected_projected=union(可投影但不 admitted)∩required_content`；
`non_citable_source=rejected_projected-citable_source`；
`uncovered_source=required_content-(citable_source∪non_citable_source)`。四类基础区间规范化、两两不交，
并集恰好覆盖整个 span；重叠来源处 citable 优先且只计一次。

`effective_citable_intervals` 是最大区间：区间内全部**非 normalization-only**字符都属于
`citable_source`，且至少含一个来源字符；它允许透明跨越中间的 normalization-only 分隔符，但绝不
跨越 `non_citable_source/uncovered_source`。它只能由上述四类重算，不能单独自报。

树路径的唯一材料级 completion 资格入口是
`span_verifier.is_completion_eligible(verified_snapshot, span_id)`：要求 policy 为 frozen、结构分值达阈值、
非 fallback/跨标题/unassigned，且全部 `required_content_intervals` 被
`citable_source_intervals` 覆盖（即 non-citable/uncovered source 均空）；normalization-only 不需要
Evidence offset，也不能掩盖任何来源字符缺口。旧
`OutlineSpan.can_support_set_complete()` 不接 typed coverage，不能在 TS7B 正式树路径中单独调用；aspect
是否 complete 仍由后续 Contract completion rule 在全部材料/事实层判定，材料级资格不自行提升 aspect。

### 18.8.3 snippet 必须落在**单个**最大可引用区间内

设 `A_1 < A_2 < … < A_m` 为 `SpanCitableCoverage.effective_citable_intervals`（重算后、升序）。

1. 片段区间 `[cs, ce)` **必须**满足 `∃i: A_i.start <= cs < ce <= A_i.end`（**落在同一个 `A_i` 内**）。
2. **span 开头未被覆盖、只有尾部被覆盖**时：**禁止**从 `cs = 0` 生成片段；只能从被覆盖区间的
   起点开始（即 `cs = A_i.start > 0`）。
3. **多行 span 只有部分行可引用**时：未覆盖行**不得**进入片段——由于 `[cs, ce)` 必须落在单个
   `A_i` 内，跨未覆盖 source gap 的片段在结构上不可能成立；只有 normalization-only 分隔符可透明跨越。
4. **完整覆盖**正向用例必须有：多行 span 的全部 required content 均有 Evidence、行间合成空格只有
   normalization-only 身份时，整 span 可形成一个 effective interval 并取得 completion；真实来源字符
   空洞仍必须失败。
5. `alignment_citable()`（TS1 既有）**只是**本管道里的一个环节（判断被引用的记录是否全部可引用），
   **不能单独**证明某片段区间可引用；可引用的最终判据只能是 §18.5.4 的并集覆盖。

### 18.8.4 片段选取（确定性，不依赖任何公司专用规则）

```text
for i in 1..m:                                  # 按 A_1..A_m 顺序
    lo = A_i.start, hi = A_i.end
    if hi - lo < MIN_SNIPPET_CHARS: continue    # 不可用（见 §18.8.6）
    window_end = min(hi, lo + MAX_SNIPPET_CHARS)
    p = 最后一个句末标点位置（SENTENCE_TERMINATORS）满足 lo + MIN <= p + 1 <= window_end
    if p 存在: 取 [lo, p + 1]
    elif hi - lo <= MAX_SNIPPET_CHARS: 取 [lo, hi]
    else: continue                                # 禁止硬截断
    追加片段（cs=lo, ce=…），直到达到 MAX_SNIPPETS_PER_NODE 或 MAX_TOTAL_SNIPPET_CHARS
```

- 片段文本 = `span.normalized_text[cs:ce]`（**逐字**取自规范化文本，抽取式回指原文）；
- 同一节点多个 span 时，先按 `(start_page,start_line,end_page,end_line,span_id)` 排序，再遍历各 span 的
  effective intervals；`MAX_SNIPPETS_PER_NODE=3` 与 `MAX_TOTAL_SNIPPET_CHARS=300` 是**节点级全局配额**，
  不是每个 span 各自重置；
- `snippet_index` 按节点级追加顺序 0..n-1；`source_span_ids` = 片段 `span_id` 按上述来源序去重；
- 片段长度**一律**落在 `[MIN_SNIPPET_CHARS, MAX_SNIPPET_CHARS]`；总长 `<= MAX_TOTAL_SNIPPET_CHARS`。

### 18.8.5 `confidence` 语义（**替换** Evidence 字符数口径）

- `OutlineSpan.confidence` **只**表达**结构边界 / 节点归属置信度**，
  **永不**由 Evidence 引用字符数、条数或覆盖度计算。
- 引用 / 对齐覆盖走**独立**的 typed 覆盖投影（`SpanCitableCoverage` + `SpanEvidenceComponent`），
  二者**互不代入**。
- 重叠 Evidence **不得**抬高 `confidence` 或使覆盖重复计数。

**确定性、公司无关的边界特征规则**：每个 regular disposition 必须持久化
`left_boundary_cause/right_boundary_cause`。TS4-A 先按下表产出边界特征与候选分值分布；人工门冻结
完整 `SpanQualificationPolicy`（因子表指纹 + threshold + 批准指纹），TS4-B 才取得 completion
资格。未知 cause 一律 fail-closed，不得落入默认分值。

候选分值为 `confidence = quantize(min(left_factor, right_factor))`：

| 因子 | 取值 | 条件 |
|---|---|---|
| `left_factor` | 1.00 | `preceding_heading`（本节点标题后的首个 regular run） |
| | 0.90 | `resume_after_empty` |
| | 0.85 | `resume_after_table_inside` |
| | 0.75 | `resume_after_table_adjacency` 或 `resume_after_non_content` |
| `right_factor` | 1.00 | `next_heading` |
| | 0.90 | `document_end` 或 `empty` |
| | 0.85 | `table_inside` |
| | 0.75 | `table_adjacency`、`formal_unassigned` 或 `non_content` |

`before_first_heading` / `after_formal_unassigned_boundary` 归 `U_ts4_unassigned`，不产生 span，也不参与
分值。上表必须与 run 切断原因形成穷尽真值表；构建器若遇到表外原因立即拒绝。因子值在 TS4-A
只是候选策略内容，以完整 typed `BoundaryFactor` entries 存入 policy，fingerprint 必须由 entries 重算；
人工门可冻结、调整或拒绝整张表，不能只改阈值，也不能只保存一个无法解析的哈希。

- 不变式（须测试）：regular run 内**不存在** `table_scope != "none"` 的行（表格相邻行已独立成
  disposition），因此置信度**不可能**受表格污染影响；
- TS4-A 的 `SpanQualificationPolicy.stage="distribution_only"`、threshold=None；
  TS4-B 的 policy 必须绑定人工批准的整张因子表与 threshold。旧 A 快照在 B 阶段保持
  distribution-only，不得因全局常量变化而被重新解释；B 环境必须有“历史 A 快照仍可验证、但
  completion 恒 False”的正例。
- 所有 factor 与 frozen threshold 必须是量化后的有限值且位于 `[0,1]`；A 阶段
  `approval_authority_fingerprint/bound_ts4a_snapshot_hash/bound_distribution_hash/
  bound_review_attestation_hash` **全部为 None**；B 阶段四者**全部为 64 位 sha256**并与仓库内
  approval record 精确一致，且
  `threshold == V.SPAN_CONFIDENCE_MIN`。三态之外或半填写一律拒绝。
- B 阶段的 `bound_ts4a_snapshot_hash` 不是“任选一个文档 snapshot”或仅含计数的索引哈希：它唯一等于
  A 阶段按固定文档序汇总的 `span_snapshot_aggregate.json` **文件 SHA256**；该 aggregate 的每个成员
  都绑定完整 canonical `span_build_snapshots/<document_key>.json` 的文件 SHA256、raw snapshot
  `content_fingerprint`、`snapshot_id` 与 `input_fingerprint`，覆盖三份真实 Evidence-backed 文档与
  非 300750 正向 fixture。`bound_distribution_hash` 唯一等于同一 A run 的
  `confidence_distribution.json` 文件 SHA256；`bound_review_attestation_hash` 唯一等于一次性封存的
  `review_attestation.json` 文件 SHA256。approval record 还必须写入 A run identity、A 根 manifest、
  machine artifact index 与 review attestation SHA256；任一不符即拒绝。
- `confidence_distribution.json` 除候选分值直方图外，必须保存**与 factor 数值无关**的
  `(left_boundary_cause,right_boundary_cause)` 交叉表及逐 disposition feature row。人工门若调整
  factor table，`--seal-review` 必须以获批候选 policy 对这些冻结 feature row 做确定性重放并把
  `replayed_distribution_hash` 写入 attestation；不得靠重新跑材料构建来迁就阈值。
- `approval_authority_fingerprint = sha256_canonical(approval_payload_without_fingerprint)`；不得把包含
  自身 fingerprint 的 JSON 全体再哈希形成循环定义，也不得采信自报 fingerprint。

### 18.8.6 synopsis 常数与 `MIN_SNIPPET_CHARS` 一致性（**统一**口径）

`MAX_SNIPPETS_PER_NODE = 3`、`MAX_SNIPPET_CHARS = 120`、`MIN_SNIPPET_CHARS = 20`、
`MAX_TOTAL_SNIPPET_CHARS = 300`。句末规则冻结为：
`SENTENCE_TERMINATORS = ("……", "。", "！", "？", ".", "!", "?", ";", "；")`，
实现时须使用无歧义的普通引号字面量；匹配按 token 长度降序。句末 token 后允许紧随的闭引号集合
`CLOSING_QUOTES = ("”", "’", "\"", "'")`，闭引号与其前句末 token 一起计入片段；单个省略号字符
或普通逗号不算句末。该常量的精确 Python 表达须由 schema/self-check 与测试锁定，不得由正则隐式扩展。

- **`MIN_SNIPPET_CHARS` 是硬下界**：任何路径（含"整段取用"分支）都**不得**产出短于它的片段；
- **`length_exceeded` 的唯一定义**＝"长度约束无法满足"：既包含"过长且在 `MAX_SNIPPET_CHARS` 窗口内
  找不到句末标点（禁止硬截断）"，也包含"可引用区间短于 `MIN_SNIPPET_CHARS`"。**一处只有这一种口径**，
  旧版"一处允许 <20 字、一处要求 ≥20 字"的冲突已删除。
- 后果（明示）：极短但可引用的节点会得到 `length_exceeded`，这是**诚实缺口**，登记为 P2 gap，
  不阻塞批次。
- synopsis 使用独立 `navigation_admissible`（节点归属明确、非 fallback、非跨标题、存在可引用区间），
  **不得**复用 completion eligibility；否则 TS4-A threshold=None 会让全部导航简介退化。
- `reason_code` 判定是全函数（互斥，首个命中）：
  1. 节点无 regular span 且无任何处置范围，或只有 unassigned 范围 → `no_span`；
  2. 节点无 regular span 且含任意 `table_inside/table_adjacency`（可同时含 empty）→
     `table_only_pending_ts5`；
  3. 节点无 regular span且只含 empty → `empty_text`；
  4. 有 regular span但所有 span 的可引用并集为空 → `alignment_failed`；
  5. 有可引用覆盖但无片段满足长度约束 → `length_exceeded`；
  6. 仅当已实际生成至少一条合法 snippet 才为 `available`。
- `source_span_ids`：情形 1/2 为 `()`；情形 3/4/5 为**该节点可采信 span 的来源序 id 列表**
  （`NavigationSynopsis.__post_init__` 允许不可用时携带 `source_span_ids`），使缺口可审计。

## 18.9 三级守恒公式（文档层、正文域层、Evidence 独立分区层）

### 18.9.1 第 1 层：TS3 / 文档级复核（行与 tight 字符都算）

```text
D_nonfurniture = H_heading ⊎ F_formal_unassigned ⊎ N_non_content ⊎ B_body
```

- 四项 = 四态行诊断的四个状态（`line_structure_diagnostic` 的 `heading_node` /
  `formal_unassigned` / `non_content` / `body_under_node`）；
- `⊎` = **互斥并集**（按 `(page, line)` 行身份；同时按 tight 字符数分账）；
- **TS3 的 `formal_unassigned` 单独计账**，**永不**折算进 `U_ts4_unassigned`；
- 标题行与非内容行**不得**从文档级守恒中消失（旧公式把它们省掉，已删除）；
- 断言：`four_state_sum == total_lines == |D_nonfurniture|`，且
  `Σ tight(non_furniture lines) == Σ tight(H) + Σ tight(F) + Σ tight(N) + Σ tight(B)`。

### 18.9.2 第 2 层：TS4 正文域（`B_body` 内部）

```text
B_body = S_regular ⊎ A_adjacent_provisional ⊎ T_inside_table ⊎ U_ts4_unassigned ⊎ E_empty
```

- 五项 = `BodyRangeDisposition.range_kind` 的五个取值（`regular` / `table_adjacency` /
  `table_inside` / `unassigned` / `empty`），与 §18.4.3 的 `range_kind` 词表**一一对应**；
- 断言：五类处置的 `layout_line_refs` 两两不交且并集 == `B_body` 的行集合；
  `Σ tight(五类) == Σ tight(B_body)`；`S_regular` 的每一段**恰好**对应一个 `OutlineSpan`
  （`span_id` 非空），其余四类 `span_id` 必须为 `None`。

### 18.9.3 第 3 层：Evidence 维度（按 `(evidence_id, tight_offset)` 独立分区）

- **先**逐 block 证明：对每条终态，`[0, block_char_length) = char_map 段并集 ⊎ residue 段并集`
  （TS1 `validate_alignment_partition` 已保证；TS4 必须用**真实 Evidence 文本**重算
  `block_char_length` 后再确认，§18.3.4-8）；
- 对 `char_map` 区间尝试执行 `T_e → L_s → L_l → 结构范围`；成功者进入 mapped landing，只有
  `body_span` 再投影到 `S`。walk/切片/边界任一步失败者进入
  `alignment_offset_unverifiable`，不得被遗漏或强塞进 mapped landing；
- residue 没有 Layout 坐标，必须单独生成 `landing="alignment_residue_unmapped"` 的
  `SpanEvidenceComponent`：`admitted=False`、`layout_hits=()`、`span_local_char_range=None`。
  **不得**把 residue 冒充 `outside_body`，也不得强行执行不存在的 `T_e→L`；
- 因而每条 Evidence 的严格分区为
  `[0,block_char_length) = mapped_char_map_landings ⊎ alignment_offset_unverifiable ⊎
  residue_unmapped`，三支合计守恒；
- **禁止**跨不同 Evidence 累加坐标；**禁止**用 `min(1.0, ...)` 掩盖重复覆盖；区间**必须先合并去重**；
- `SpanConservation.evidence_layer` 必须逐条 Evidence 记录：段数、residue 段数、投影成功 / 失败段数、
  各 `landing` 计数，以及"未落到任何正文范围"、`alignment_offset_unverifiable` 的计数。

### 18.9.4 缺口必须诚实显示

任何不守恒、任何 `offset_unverifiable`、任何 `boundary_inexact`、任何 `admitted=False`，
都必须在验收投影（`conservation.json` / `manual_review.md`）中**逐条**可见，不得聚合掩盖。

## 18.10 权威与兼容边界

### 18.10.1 TS4 的权威边界（**替换**"冻结实现优先于任何文档"）

- 权威顺序按 §18.1.4（`AGENTS.md` 优先）。**设计优先**：本节的实现只能细化 `DESIGN_V2.md` /
  `V2_IMPLEMENTATION_PLAN.md` **明确允许**的字段草图；**语义冲突必须上报**（§18.19），
  **不得**由"冻结实现"或本规格自行消解上位设计。
- TS4 不得改写的权威：`PageLayout`、`DocumentOutline` 节点集、标题资格 / 层级（`hq-4`）、
  表格污染规则（`trg-3`）、TOC / Bookmark 信任边界（`tocr-2`）、aligner（`al-3`）与 `ALIGN_MIN`。
- 标题树是**主边界**；相邻块 / 页扩读只是**有界 fallback**；`EvidenceBlock` 是不可变来源 /
  引用锚点，**不再是**默认业务材料边界。

### 18.10.2 → TS5 / TS6 接口（可复核即可）

- TS5 的 **TS4-material capability** 输入是 `VerifiedSpanSnapshot`（只能由
  `verify_span_snapshot` 取得）。该 wrapper 不只绑定 `S_regular` span、`T_inside_table` 与
  `A_adjacent_provisional` 处置，还绑定同次验证取得的只读 PageLayout、DocumentOutline、
  structure snapshot、Evidence snapshot/blocks 与 alignment terminals；TS5 必须从 wrapper 取得这些
  真实根对象重建几何与表格，不得仅消费 raw snapshot 投影或由调用方另行拼装根对象；
- TS5 输出（本批不实现）：§18.7.3 的不可变 `TableRangeDecision`、`FinalComponentBinding`、
  TableObject successor、`FinalMaterialStructureSnapshot` 以及唯一正式验证所得
  `VerifiedFinalMaterialStructureSnapshot`；
- TS6/TS7A/TS7B 的 **final-material capability** 只接受
  `VerifiedFinalMaterialStructureSnapshot`；不得绕过 TS5 直接用 `VerifiedSpanSnapshot`，也不得用
  raw final snapshot/JSON。该限制只约束材料结构信任链；各阶段仍须另外取得各自权威的 Contract、
  navigation profile、aspect requirement、source policy 等业务输入，不得把 final wrapper 冒充全部业务输入；
- 边界：TS4 的 provisional 范围在 TS5 决议前**既不是**段落材料**也不是**表格成员；
  任何"同一范围长期双重身份"都是禁止状态。

### 18.10.3 → TS7B 的后继兼容口径（**冻结方向，本批不编码**）

- 每个 **Evidence 组件区间**仍是原子来源；TS7B 固定采用“**一 component 一 ResearchMaterial，
  span/table 作为 shared assembly**”的映射，避免把多来源装配伪装成单一 authority；`OutlineSpan` 是这些原子组件的
  **结构化装配 / 材料边界**；组合 span **不得**冒充单一 Evidence 的权威；
- TS7B 后继的 locator / payload **必须**携带同一规范字段集：`final_structure_snapshot_id`、
  `component_id`、互斥的 `span_id|table_id`、`evidence_id`、`evidence_char_range=(cs,ce)`、
  `span_local_char_range|None`、`coordinate_domain="tight_evidence_text"`、component/schema/builder 版本，
  以及归一化/对齐/layout/outline/final-structure 各自版本；Citation 必须回到单一 component 的 authority，
  assembly 只聚合，不能只写页码或整个 Evidence ID；
- 现状核对（已实测，作为 TS7B 的兼容前提）：`MATERIAL_TYPES` 已含 `evidence_span` 与
  `table_context`；`EvidenceLocator` **无** `outline_span_id` / `char_range`
  ⇒ 需要**Pack v5 后继 + Store migration 4**。这两个数字属于不同版本轴，文档、代码和测试不得混称；
  双哈希分层（来源层 `source_content_hash` vs 载体层 `payload_hash` / `payload_id`）必须保持不混淆；
- 两条 CitationRef 链必须同时升级并以显式 adapter 对齐：
  1. `harness.topic_schema.CitationRef`（TopicResearchPack 内）；
  2. `harness.schema.CitationRef`（runtime / `sections.schema` / SectionClaim 内）。
  `harness.runtime._citation_from_dict` 与 `sections.schema.citation_from_dict` 当前会筛掉未知字段，TS7B
  必须改为严格版本分派：tree-v5 必需字段缺失或未知字段一律拒绝，不得静默过滤；转换前后上述规范字段
  逐项相等，`sections.citation_authority` 再对 verified final snapshot 重查。
- Pack v4 不“伪升级”为 v5：新增显式 version-dispatch history reader，只返回 history-only legacy view；
  v4 可按 pack_id 审计读取，但不得成为 current、checkpoint resume、completion 或 Writer 输入。v5 新 tree
  material 的这些字段是**条件必填**（非 tree/financial/external 变体按各自真值表）；旧 reader 必须对 v5
  明确报 incompatible，不能忽略新增字段。迁移 4 只追加台账/必要列或索引，不重写历史 payload。
- 当前 `TopicMaterialPayloadResolver._validate_envelope` 在 `locator.offset` 非空时跳过片段正文重哈希；
  TS7B 因而必须新增 final object-level verifier，不能把现有 resolver 当成充分证明。该 verifier 从
  `VerifiedFinalMaterialStructureSnapshot` 定位 component，再经只读 Evidence gateway 取真实 block，按
  `evidence_char_range` 独立切片，重算 component identity、payload 正文、source hash 与 payload hash；
  “篡改 fragment 后同步重算 envelope/payload_id”仍须拒绝。表格组件同样回到 verified TableObject/component
  绑定，不得只信 payload 自报 locator。
- **本批不改 Pack Store**；以上是 TS7B 开工前必须原样带入的迁移/信任边界，不授权 TS4 越界实现。
- Pack dependency fingerprint 必须绑定 final material snapshot、qualification policy、synopsis、
  table decision/TableObject、component schema 与对应内容指纹；任一变化产生 stale，不得复用旧 current。

### 18.10.4 与 §2.3 口径的校准（**细化为兼容，不构成冲突**）

TS4 在本节内只细化 §2.3 的"材料边界 / 证据边界"表述：材料边界 = 标题树 + span；
证据锚点 = Evidence 组件区间。二者不互相顶替，也不把 `NavigationSynopsis` 当证据。

## 18.11 文件级 changelist

### 18.11.1 新增

| 文件 | 内容 |
|---|---|
| `document_structure/evidence_gateway.py` | `VerifiedCurrentEvidenceAuthority` 私有 runtime capability 与无参 `bind_current_evidence_authority()`；从 service 已预检绑定的 current Store 签发（不 init/migrate、不改 `_db_path`），随后只调用 `current_evidence_set_ro/list_document_evidence_ro`（`mode=ro + query_only`）；缺库/未绑定/空集/非 current 均 fail-closed。生产 API 无 `db_path`；runner 另走根锁固定路径的 issuer |
| `document_structure/span_schema.py` | §18.4.3 八个 versioned 顶层类型、typed 子结构、各自 `to_dict`/`from_dict` + `self_check()`（纯函数） |
| `document_structure/span_policy.py` | 模块私有 `QualificationPolicyProvider` 分层、固定 registry 的唯一 production resolver、A/B authority record 与逐 policy 稳定 fingerprint 全字段校验；正式入口不接受任意 policy/provider/path |
| `document_structure/span_builder.py` | 非公开构造、不可序列化的 `SpanBuildInput` / `VerifiedTS3Handoff` capability、issued-object registry、`SpanBuildError`、内部 structure/evidence/terminal/policy 组合；具名 live issuer `issue_live_ts3_handoff(...)` 内部重建 outline/structure；唯一公开构建入口 `build_span_snapshot(live_handoff,stage)`；pinned/testing scope 各走不导出的 wrapper，共享私有算法核 |
| `document_structure/span_verifier.py` | 私有 `_verify_outline_structure_snapshot(snapshot,handoff)`、唯一公开 `verify_span_snapshot(snapshot,handoff)`、`is_completion_eligible(...)`（§18.4.2 / §18.8.2）与逐项失败原因；无 provider/path 入口 |
| `document_structure/synopsis.py` | `admissible_source_spans(...)`、`select_snippets(span, coverage)`、`unavailable_reason(...)`、`build_node_synopsis(...)`、`build_navigation_synopses(...)`、`verify_synopsis_sources(...)`、`self_check()`、`_main(argv)` |
| `evals/fixtures/tree_structure/ts4_trust_roots.json` | 固定 §18.14.2 的 TS3 run 根哈希、artifact 索引成员身份与原 PDF/layout/outline/alignment 期望身份；测试和 runner 只读 |
| `evals/fixtures/tree_structure/non_300750_ts4/` | 版本化的非 300750 PageLayout/Outline/Evidence/terminal manifest 正向 fixture；与旧“无 Evidence 集”负例分离 |
| `evals/test_tree_span_formal_chain.py` | bounded live 正向集成：经现有 service 只读依赖预检 → `VerifiedCurrentEvidenceAuthority` → `build_verified_page_layout` → `align_evidence_set_verified` → `issue_live_ts3_handoff` → 公开 build/verify；全程离线、临时 fixture DB、无第二套 runtime |
| `document_structure/policies/registry_v1.json` | 固定 policy_id→版本控制资产相对路径的封闭 registry；entry canonical hash 独立计算，TS4-B 只能追加新 entry，不得改写 TS4-A entry；production resolver 使用代码内固定 registry 路径 |
| `document_structure/policies/span_qualification_distribution_v1.json` | TS4-A 唯一 distribution-only authority record：完整 factor entries、threshold=null、四项 approval/bound 字段为 null、provider/schema/algorithm 版本与内容哈希 |
| `document_structure/policies/span_qualification_approval_v1.json` | **仅在人工门批准后由 TS4-B 新增**：绑定 TS4-A run identity、根 manifest、machine artifact index、完整 aggregate snapshot、distribution 与 review attestation SHA256、完整批准因子表、`threshold∈[0,1]`；`approval_authority_fingerprint` 按“不含自身字段的 canonical payload”重算；不得在 TS4-A 预造 |
| `document_structure/policies/span_qualification_frozen_v1.json` | **仅在 TS4-B 新增**：由 approval record 确定性派生并逐字段等值校验；它是 frozen current policy，不得由调用者临时生成 |

### 18.11.2 修改（**逐项最小**）

| 文件 | 改动 | 影响面 |
|---|---|---|
| `document_structure/versions.py` | ① 追加 §18.12.4 的 8 个 schema 常量、`SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS` 与全局 union 自检；②保持 `SPAN_BUILDER_VERSION=sb-1`，新增 `TS4_BODY_SPAN_BUILDER_VERSION=sb-2`；③ `SYNOPSIS_SCHEMA_VERSION nss-1→nss-2`、`SYNOPSIS_VERSION ns-1→ns-2` 并登记 legacy；④新增 `SPAN_QUALIFICATION_POLICY_VERSION` | 既有 9 类型登记内容保持不变，但全局自检必须覆盖两表并集；不是为保持旧测试数字而漏登记新 wire 类型 |
| `document_structure/schema.py` | ① `NavigationSynopsis.__post_init__` 的 `_check_version` 绑定 schema/algorithm constant；② `SYNOPSIS_REASON_CODES` 追加 `table_only_pending_ts5` 并写 nss-2；③ `OutlineSpan` os-4 兼容读层允许已登记的 `sb-1/sb-2`，不在 base class 按 role 拒绝历史 assigned sb-1；④旧 `OutlineSpan.can_support_set_complete()` 不再作为树路径正式资格入口 | **不改** `als-3/alr-2` 构造/from_dict 接受语义；char_map 升序只在 TS4 正式输入验证器收紧 |
| `document_structure/outline_builder.py` | `_formal_unassigned_span` 显式传 `OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION=sb-1`，不再依赖可能变化的默认值 | 输出身份与冻结 TS3 树逐字节不变，须有 canonical 重建回归 |
| `document_structure/layout_builder.py` | 追加 `VerifiedPageLayout` 私有 wrapper 与 `build_verified_page_layout(raw_pdf)`；直接从 PDF bytes 构建并签发，不接受 caller PageLayout | plain PageLayout 不取得 handoff 资格；篡改 layout 并重算 ID 仍拒绝；wire/算法不变 |
| `document_structure/aligner.py` | 追加 `VerifiedEvidenceSetAlignment` 私有 wrapper 与 `align_evidence_set_verified(verified_layout,evidence_authority)`；只给同次正式结果签发 | plain `EvidenceSetAlignment` 不可包装；不改 `al-3/als-3/alr-2` wire 或判定 |
| `document_structure/__init__.py` | 导出新 wire 类型与唯一 `build_span_snapshot/verify_span_snapshot`；**不导出** provider/gateway factory、私有 capability 构造器或 `_testing/_pinned` issuer | 纯追加且封闭组合根 |
| `evals/run_evals.py` | `EVAL_MODULES` 末尾追加 TS4 测试模块（在 `test_tree_structure_artifacts` 之后） | 纯追加 |
| `evaluation/run_tree_span_acceptance.py` | 新增 TS4 专用 runner（§18.14） | 新文件，不改既有 runner |
| `evals/test_tree_page_layout.py`、`evals/test_tree_structure_schema.py`、`evals/test_tree_structure_adversarial.py` | 把既有 `SPAN_CONFIDENCE_MIN is None` 断言改为 A/B 分阶段真值表：A 为 None；B 等于 trusted frozen policy.threshold；历史 A 按 policy_id 验证且 completion=False。adversarial 临时未裁决分支须 `try/finally` 恢复当前批准值 | 保证 TS4-B 有限阈值下完整 `run_evals` 可达，不以删除旧测试换通过 |

### 18.11.3 明确不改

`harness/**`、`sections/**`、`TOPIC_PACK_SCHEMA_VERSION` / `MIGRATIONS` / Store、
`evidence/**`、`evaluation/run_tree_outline_acceptance.py`、任何冻结输入产物、
`AGENTS.md` / `DOCUMENTATION_INDEX.md` / `DESIGN_V2.md` / `V2_IMPLEMENTATION_PLAN.md`。
`V2_TODO.md` 只允许在独立状态文档提交中记录已发生事实，代码批次不改。

## 18.12 版本与 legacy 矩阵

### 18.12.1 既有事实修正（删除错误断言）

**删除**旧 §十八的"`sb-1` 从未被真实使用"。实测事实：TS3 已用 **`sb-1`** 产出**正式 unassigned
`OutlineSpan`**——4 份冻结产物共 **232 个**（`FIXTURE_BOND_2026` 1 / `NDSD_2024_year` 97 /
`NDSD_2025_year` 113 / `NDSD_KCZ_2026` 21），全部 `role == "unassigned"`、
`span_builder_version == "sb-1"`，reason 分布为 `insufficient_heading_evidence`(201) /
`numbered_list_ambiguity`(24) / `hierarchy_conflict`(6) / `toc_unmatched`(1)。
因此这些对象的**可读回性**是硬约束。

### 18.12.2 `sb-1` 保持 TS3 语义，TS4 正文显式使用独立 `sb-2`

- 232 个 TS3 unassigned `OutlineSpan` 保持 `sb-1`、逐字节 round-trip 与 Layout/Outline 上游身份核验；
  它们的 `evidence_set_version="no-evidence-set"`，**不得**伪装成当前 Evidence 集的可引用材料。
- 全局 `SPAN_BUILDER_VERSION` **不得改变**，TS3 builder 与冻结树重建继续显式/默认写 `sb-1`；
  新增 `TS4_BODY_SPAN_BUILDER_VERSION="sb-2"`，TS4 builder 构造正文 span 时必须显式传入它。
- `OutlineSpan.__post_init__/from_dict` 属 os-4 兼容读层：既有 assigned/unassigned `sb-1` 均继续可读，
  新 `sb-2` 也可读；**不**在 base wire class 改变旧接受集合。正式 TS4 builder/verifier 另行强制：
  `SpanBuildSnapshot.spans` 全部为已归属 `sb-2`；`inherited_unassigned_span_ids` 全部精确指向冻结
  `sb-1 + role=unassigned + no-evidence-set` 对象。历史 assigned sb-1 可 round-trip，但不得进入 TS4 snapshot。
- `SpanBuildSnapshot.spans` 只含 `sb-2` 新正文 span；旧对象只以
  `inherited_unassigned_span_ids` 精确引用，实体仍由 `DocumentOutline.unassigned` 权威持有。
- `sb-1` **不登记为失效 legacy**：它仍是 TS3 unassigned 的 current 算法。`sb-2` 以独立常量登记；
  读旧对象按对象自身版本校验，不用新算法重算其 ID。`outline_builder._formal_unassigned_span` 不得
  因 TS4 代码变化而改写版本，因此历史 TS3 rebuild 仍可 canonical 等于冻结树。
- 断言新旧 span ID 无交集；任何尝试把 inherited unassigned 纳入 citable coverage、synopsis 或
  completion eligibility 都必须拒绝。

### 18.12.3 version 矩阵（最终）

| 常量 / 对象 | 变更前 | 变更后 | 处置 |
|---|---|---|---|
| `SPAN_SCHEMA_VERSION`（`OutlineSpan` wire） | `os-4` | **`os-4`** | 不变 |
| `SPAN_BUILDER_VERSION` | `sb-1` | **`sb-1`（不变）** | TS3 unassigned 与历史树重建继续使用 |
| `TS4_BODY_SPAN_BUILDER_VERSION` | —— | **`sb-2`** | 新 TS4 正文算法；必须显式传入，不能改变 TS3 默认值 |
| `SYNOPSIS_SCHEMA_VERSION`（`NavigationSynopsis` wire） | `nss-1` | **`nss-2`** | 封闭 reason 词表扩充必须升 wire 版；`nss-1` 仅识别后拒绝，须重算 |
| `SYNOPSIS_VERSION`（算法） | `ns-1` | **`ns-2`** | `ns-1` 登记入 legacy，仅用于给出“须重算”的确定性错误，不作为 current 读取 |
| `SYNOPSIS_REASON_CODES` | 4 值 | **5 值**（+`table_only_pending_ts5`） | 追加（已批准） |
| `_check_version` 调用（`NavigationSynopsis`） | 未绑定 `constant_name` | **绑定 `"SYNOPSIS_VERSION"`** | 修复：`ns-1` 走"须显式迁移 / 重算"专用错误；未知版本走"必须为当前版本" / 未知版本拒绝；`ns-2` 正常读写 |
| `Alignment*.char_map` 存储序 | wire 不强制 | wire **不变**；TS4 正式输入边界强制升序 | 保持 als-3/alr-2 兼容，避免无迁移升版 |
| `SPAN_CONFIDENCE_MIN` | `None` | TS4-A `None`；TS4-B 与批准的 frozen policy 同步发布批准值 | A/B snapshot 绑定不同 policy identity；禁止用常量变化重解释 A 快照 |
| 新增 8 个 schema 版本常量 | —— | `obs-1` / `sqp-1` / `sps-1` / `spc-1` / `spv-1` / `spr-1` / `spn-1` / `nsv-1` | 新增，同等权威登记（§18.12.4） |

### 18.12.4 新类型的版本登记方式（完整双射，不靠测试数量规避登记）

- 明确常量名：`OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION=obs-1`、
  `SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION=sqp-1`、`BODY_RANGE_DISPOSITION_SCHEMA_VERSION=sps-1`、
  `SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION=spc-1`、`SPAN_CITABLE_COVERAGE_SCHEMA_VERSION=spv-1`、
  `SPAN_CONSERVATION_SCHEMA_VERSION=spr-1`、`SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION=spn-1`、
  `SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION=nsv-1`；另有算法常量
  `SPAN_QUALIFICATION_POLICY_VERSION=sqpr-1`（与 `sqp-1` wire schema 分开命名、分别登记语义）。
- 算法常量另明确：`OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION=sb-1`、
  `TS4_BODY_SPAN_BUILDER_VERSION=sb-2`、`OUTLINE_STRUCTURE_PROVIDER_VERSION=osp-1`、
  `EVIDENCE_GATEWAY_PROVIDER_VERSION=egp-1`、
  `ALIGNMENT_TERMINAL_PROVIDER_VERSION=atp-1`、`QUALIFICATION_POLICY_PROVIDER_VERSION=qpp-1`、
  live 的 `VERIFIED_PAGE_LAYOUT_ISSUER_VERSION=vpli-1`、
  `VERIFIED_ALIGNMENT_ISSUER_VERSION=vai-1`、
  `VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION=vea-1`、`VERIFIED_TS3_HANDOFF_VERSION=vth-1`；
  historical pinned 的 `PINNED_PAGE_LAYOUT_ISSUER_VERSION=vplip-1`、
  `PINNED_ALIGNMENT_ISSUER_VERSION=vaip-1`、
  `PINNED_EVIDENCE_AUTHORITY_VERSION=veap-1`、`PINNED_TS3_HANDOFF_VERSION=vthp-1`；
  versioned fixture 的 `FIXTURE_PAGE_LAYOUT_ISSUER_VERSION=vplif-1`、
  `FIXTURE_ALIGNMENT_ISSUER_VERSION=vaif-1`、
  `FIXTURE_EVIDENCE_AUTHORITY_VERSION=veaf-1`、`FIXTURE_TS3_HANDOFF_VERSION=vthf-1`；
  unit-test only `TESTING_TS3_HANDOFF_VERSION=vtht-1`。
  它们全部进入 `VERSION_CONSTANTS`、`MANDATED_VERSION_NAMES` 与算法版本 self-check；不得只在业务代码里
  写字符串。兼容 `SPAN_BUILDER_VERSION=sb-1` 保持现值。
- 八个类型进入 `SPAN_RECORD_PUBLIC_TYPES` 与
  `SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS: type_name -> (schema_field, constant_name)`；
  `versions.py` 提供 `ALL_VERSIONED_OBJECT_SCHEMA_FIELDS` 作为既有 9 类型与新表的无重名并集，
  `versions.self_check` / `span_schema.self_check` 共同验证“公共类型 ↔ schema 常量”严格双射。
- 既有 `schema.PUBLIC_TYPES` 与 9 项 `VERSIONED_OBJECT_SCHEMA_FIELDS` 内容可保持不变，因为新类型在
  新模块；但项目级自检必须检查 union，不能把正式序列化快照称作 runtime-only 以逃避登记。
- **无 SQLite migration**：新类型不写 Store；现状 `MIGRATIONS = [("1", None), ("2", …), ("3", …)]`、
  `STORE_SCHEMA_VERSION = "3"`、`TOPIC_PACK_SCHEMA_VERSION = "4"` **一律不动**。

## 18.13 测试矩阵（全部为新增模块，`python -m evals.<module>` 运行）

| 模块 | 用例 | 断言要点 |
|---|---|---|
| `evals/test_tree_span_schema.py` | T1 类型与版本登记 | 8 个新类型与精确常量名在 `SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS` 严格双射；与既有 9 类型并集无重名、无漏项；current/legacy/unknown 均有反例 |
| | T2 `to_dict`/`from_dict` 严格性 | 未知字段拒绝、缺字段拒绝、类型不符拒绝、`NaN`/`±Inf` 拒绝；`from_dict`→`to_dict` 往返等值 |
| | T3 身份确定性 | 同输入 → 同 `disposition_id` / `component_id` / `coverage_id` / `snapshot_id`；任一规范化字段变化 → id 变化 |
| | T4 词表 | `SYNOPSIS_REASON_CODES` 恰为 5 值（含 `table_only_pending_ts5`）；`SPAN_ROLES` / `UNASSIGNED_REASONS` / `ALIGNMENT_VERDICTS` / `TABLE_SCOPES` 值集不变 |
| `evals/test_tree_span_input.py` | T5 **入口拒绝伪造派生结果与 input capability** | 正式入口只接受 `VerifiedTS3Handoff+stage`，不接受 provider/gateway/db_path/policy path/plain `EvidenceSetAlignment`/`SpanBuildInput/line_states/table_scopes/outline_result/snapshot+blocks`；直接构造、`object.__new__`、copy/replace 或伪造 provider 指纹后重算全部身份均无正式注入路径 |
| | T6 Evidence 快照缺 / 多 / 异文档 / 异版本 / 异集合 | 生产入口只能经只读 gateway；伪造一整套内部自洽 snapshot+blocks 仍不能注入；成员缺/多、跨公司/文档/版本/集合分别拒绝 |
| | T7 全终态闭合 | 删一个终态 → 拒绝（缺失）；塞一个不属于本集合的终态 → 拒绝（多余）；同一 block 两个终态 → 拒绝（重复）；`terminals_missing_count` 与自算结果必须一致 |
| | T8 终态 / Evidence / Layout 三方对齐 | 除长度/layout/set/aligner 外，交换两个 block 的 `page_number/block_index`、伪造 terminal schema/locator/ID 均拒绝；structure snapshot 的 layout/outline/version 任一不符也拒绝 |
| | T8b handoff/provider/policy 与 input fingerprint | 四个内部受信边界的 version 与 authority fingerprint 均入封闭公式；官方 gateway+替代 DB、官方 terminal factory+手造 `EvidenceSetAlignment`、保持 PDF SHA 却篡改 Layout 并重算全 ID、任意 Protocol、自报 fingerprint、伪造 frozen policy/approval 后重算全 ID 均拒绝；增加 B 资产后历史 A provider/input fingerprint 不变，替换任一 policy record 仍拒绝 |
| `evals/test_tree_span_coords.py` | T9 walk 公式 | 真实样本逐段成立；构造 `char_offset` 指向空白字符 → `offset_unverifiable`；构造 `be-bs` 超出剩余非空白数 → `offset_unverifiable` |
| | T10 禁止原域误用 | 断言实现**不**使用 `raw[char_offset : char_offset + (be-bs)]`（以紧邻非空白被空白隔开的样本给出反例：原域切片与 tight 切片不等） |
| | T11 乱序 `char_map` TS4 边界拒绝 | 历史 `als-3/alr-2` payload 可按原 wire 读回，但正式 `build_span_input/verify_span_snapshot` 拒绝乱序；TS4 不排序、不改冻结 from_dict 语义 |
| | T12 `L_s→L_l→S` 映射 | 多 LayoutSpan 行、目标 span 前有正文、span 间空白、映射从目标 span 中部开始/结束；必须加 `LayoutSpan.char_start`，snippet 绝不能误取同一行前一个 span 文本 |
| `evals/test_tree_span_builder.py` | T13 run 切分 | 表格 `inside` / `adjacent` / 空行 / 非 `body_under_node` 行四类切断各自生效；跨页 run 合法 |
| | T14 处置五类互斥且完备 | 每个 `body_under_node` 行恰属一类；`regular` 的 `span_id` 非空，其余四类 `span_id is None` |
| | T15 节点零 span 合法 | 构造"节点内容全在表格范围"→ 无 span、`BodyRangeDisposition(range_kind="table_inside")` 存在、节点简介 `table_only_pending_ts5` |
| | T16 **末节点不越界** | 仅实际抵达文档末的 run 用 0.90；末节点后分别存在 formal-unassigned/non-content/table/empty 时都在真实边界停止，不产生 `below_last_heading` |
| | T17 confidence / policy | 每一种左右 boundary cause 有正例；未知 cause 拒绝；Evidence 数量不影响候选分值；A/B policy ID 与 snapshot ID 必不同；B 常量发布后历史 A 快照仍按 policy_id 成功重验但 completion 恒 False；approval 的 aggregate raw snapshots/distribution/machine index/review attestation/root 哈希及非循环 authority 指纹任一改变均拒绝；调整 factor 后只用冻结 feature row 重放分布 |
| | T18 引用 TS3 unassigned span | 232 个旧对象 round-trip 等值、ID 精确进入 `inherited_unassigned_span_ids`、不出现在新 spans/components/coverage/synopsis；新旧 ID 无交集 |
| | T19 新旧算法版本 | 冻结与既有测试中的 assigned/unassigned `sb-1` 均可 os-4 round-trip，TS3 rebuild 仍写 `sb-1`；TS4 snapshot 新正文只接受 `sb-2`，把历史 assigned sb-1 或 unassigned sb-2 塞进 snapshot 均由 TS4 verifier 拒绝；旧 no-evidence-set fixture 只做 Layout/Outline 核验并显式 gap |
| `evals/test_tree_span_verifier.py` | T20 **全快照篡改 + 重算全部身份仍被拒** | 对 dispositions/spans/components/coverages/conservation/synopses 分别执行删一条、加一条、改一条（含 node 错绑、run 截短、coverage 扩大），重算所有 ID/hash 后仍因 expected snapshot canonical 不等而拒绝；验证结论只存在 `VerifiedSpanSnapshot` wrapper，不写回 raw snapshot |
| | T21 synopsis 来源复核 | 改 snippet 文本 / 区间 / 来源集合，重算 `synopsis_id` → 拒绝；每个真实 node 恰一 synopsis，混合 table+empty/no regular 等组合走穷尽 unavailable 状态 |
| | T22 `alignment_closure_errors` 陷阱 | 传全文档记录列表 → 抛 `SchemaValidationError`（extra）；传本 span 的 `alignment_ids` 精确列表 → 通过 |
| `evals/test_tree_synopsis.py` | T23 **头部未覆盖、尾部覆盖** | 断言**不**从 `cs = 0` 出片段；片段起点 = 最大可引用区间起点 |
| | T24 **多行 span 仅部分行可引用** | 未覆盖来源行不得进入片段；normalization-only 可透明跨越，但任一 non-citable/uncovered source gap 均不可跨 |
| | T25 **多条重叠 Evidence 不重复计数** | 覆盖计数等于并集长度（严格小于逐条求和）；`confidence` 不变 |
| | T26 多行完整来源覆盖正向 | 两行及同一行多 LayoutSpan 的全部 required content 均可引用、只有合成/折叠空格为 normalization-only → 可生成跨空格 effective interval 且 coverage 完整；A 阶段 eligibility 仍 False，B 仅在 frozen policy 下另断言 completion；删除一个真实来源字符即失败 |
| | T27 `MIN_SNIPPET_CHARS` 一致性 | 可引用区间短于 20 → 无片段且 `reason_code == "length_exceeded"`；**不得**出现短于 20 字的片段（唯一口径） |
| | T27b 多 span 节点级配额 | 同一节点 ≥2 个 span 时按源锚点稳定排序；3 条/300 字配额只初始化一次，不得按 span 重置；输入 span 顺序打乱后 synopsis 字节相同 |
| | T28 表格 provisional 不进 Synopsis | table_inside/adjacency 均不产生片段；每条仍有 disposition、原文、定位器与全量 component；模拟未来不可变 decision overlay 后原 disposition 不变 |
| `evals/test_tree_span_conservation.py` | T29 三级守恒正向 | 文档层 `four_state_sum == total_lines == |D_nonfurniture|` 且 tight 字符分账成立；正文层五类互斥完备且与 `B_body` 相等；Evidence 层逐条分区完备 |
| | T30 文档层负例（每类一个） | ①删一个标题行；②把 `formal_unassigned` 折算进 `U_ts4_unassigned`；③删一个 `non_content` 行；④把一个 body 行重复计入两项 —— 四者各自必须被抓出 |
| | T31 正文层负例（每类一个） | ①删一个 `table_inside`；②把 `table_adjacency` 与普通正文合进同一 run；③把 `empty` 行并入相邻 run；④把 `unassigned` 范围伪造成 `regular` —— 四者各自必须被抓出 |
| | T32 Evidence 层负例 | 跨 Evidence 累加、重复覆盖掩盖、区间未合并均被抓出；失败 char-map 必进 `alignment_offset_unverifiable`，residue 必进 `alignment_residue_unmapped` 且保留 residue_class；二者不得伪造 Layout 落点；heading/formal-unassigned/non-content/table-adjacency 各有落点正例 |
| `evals/test_tree_span_artifacts.py` | T33 runner 拒绝覆盖 | 普通运行目标目录已存在 → `SystemExit` 且**不**创建任何文件；唯一例外是 `--seal-review` 对既有、未封存的同一 A 目录只追加 attestation |
| | T34 冻结根与整组篡改 | manifest/index 单独篡改拒绝；连同目录内 index/check 一致重写也因仓库内 pinned root digest 不符而拒绝 |
| | T35 机器产物哈希、完整 snapshot 与两阶段 review seal | 每份完整 canonical snapshot 的文件 SHA/content fingerprint 与 index 一致；`machine_artifact_index` 不含可编辑文件。seal 必须重跑仓库根锁、当前代码指纹与每份 raw snapshot 的 pinned verifier；整套 A 目录+index 一起改写、代码改变后 seal、missing/extra machine file、`reject/needs_changes`、任一 checklist 非 pass 均不得产生 B-eligible attestation；合法 approve 只追加一次，seal 后改任一字节或重复 seal 均拒绝 |
| | T36 数据库字节不变 | 运行前后 `data/evidence.db` 的 `size` / `mtime_ns` / `sha256` 三项不变；`immutability_report` 的 `changed` 为空 |
| | T37 不读结果目录（生产代码） | 作用域为 TS4 新增的六个生产模块（`evidence_gateway.py` / `span_schema.py` / `span_policy.py` / `span_builder.py` / `span_verifier.py` / `synopsis.py`）：不含 `evaluation/results` 字面量（不能施于全包，历史注释可保留），且 TS4 生产路径运行时不打开 `evaluation/results/**`（审计钩子断言无 `open`） |
| `evals/test_tree_span_fixture.py` | T38-A 非 300750 正向 fixture（A 必跑） | 使用仓库内版本化 fixture Evidence/terminal manifest 走 TS4-A builder/verifier/conservation/synopsis，completion 必为 False；另保留“冻结旧 fixture 无 Evidence 集”fail-closed 负例 |
| | T38-B 非 300750 正向 fixture（B 获批后启用） | 只在 frozen policy/attestation 存在后走 TS4-B；断言全局阈值等于 trusted policy、历史 A 仍可验证但 completion=False；不得在 A 阶段预造 B 资产或伪造通过 |
| `evals/test_tree_span_completion.py` | T39 唯一 completion predicate | 只能在 verified snapshot + frozen policy + 全 span 可引用覆盖下返回 material eligible；头部/中部有 uncovered gap 即使存在 aligned Evidence 也拒绝；TS7B 禁止直接调用旧 `OutlineSpan.can_support_set_complete()` |
| `evals/test_tree_span_formal_chain.py` | T40 live 唯一正式链可达 | 实际签发 live 四级 capability 并由公开 builder/verifier 正向通过；spy 证明走现有 service current-store 只读预检、正式 layout/aligner/TS3 builder，且没有第二套 Router/Harness；把 pinned/testing handoff 传公开入口必须拒绝；运行前后 DB 字节不变 |

**测试卫生**：全部用例为"构造 + 断言"，不连网络、不写数据库、不依赖执行顺序；
TS4-A 中 `SPAN_CONFIDENCE_MIN is None`，任何“支撑 `set_complete`”断言必须预期失败；TS4-B 则按
trusted frozen policy 的批准值运行。现有三组阈值测试必须走同一 A/B 真值表，不能保留无条件 `is None`。

## 18.14 真实验收与 runner

### 18.14.1 runner

新增 `evaluation/run_tree_span_acceptance.py`，沿用既有 runner 约定（`_main(argv) -> int` +
argparse `--run-id` / `--validate-only`；结果目录已存在时 `raise SystemExit("结果目录已存在，拒绝覆盖历史结果：…")`；
`sha256_file` / `snapshot` / `immutability_report`；`CODE_FINGERPRINT_FILES`）；
`--results-root` 默认 `evaluation/results`，`--run-id` 形如 `tree_span_ts4_<stage>_<UTC>`。
唯一特例是独立 `--seal-review <existing-ts4-a-dir>` 模式：它不运行构建器、不改任何既有文件，只在
完整复核后 create-once 写 `review_attestation.json`；目标不存在、已封存或任一绑定不符均拒绝。

`CODE_FINGERPRINT_FILES` 必须逐文件显式列出（不得用目录通配或“全部测试”代替）：
`document_structure/__init__.py`、`document_structure/versions.py`、`document_structure/canonical.py`、
`document_structure/schema.py`、`document_structure/outline_builder.py`、`document_structure/layout_builder.py`、
`document_structure/aligner.py`、
`document_structure/evidence_gateway.py`、`document_structure/span_schema.py`、
`document_structure/span_policy.py`、`document_structure/span_builder.py`、
`document_structure/span_verifier.py`、`document_structure/synopsis.py`；
`evidence/store.py`（正式 gateway 使用的只读原语）、`sections/service.py`（正式只读依赖预检）；
`evaluation/run_tree_span_acceptance.py`；
`evals/run_evals.py`、`evals/test_tree_span_schema.py`、`evals/test_tree_span_input.py`、
`evals/test_tree_span_coords.py`、`evals/test_tree_span_builder.py`、
`evals/test_tree_span_verifier.py`、`evals/test_tree_synopsis.py`、
`evals/test_tree_span_conservation.py`、`evals/test_tree_span_artifacts.py`、
`evals/test_tree_span_fixture.py`、`evals/test_tree_span_completion.py`、
`evals/test_tree_span_formal_chain.py`、
`evals/test_tree_page_layout.py`、`evals/test_tree_structure_schema.py`、
`evals/test_tree_structure_adversarial.py`；
`evals/fixtures/tree_structure/ts4_trust_roots.json`；
`evals/fixtures/tree_structure/non_300750_ts4/manifest.json` 及 manifest 逐项列出的 fixture 成员文件；
TS4-A 固定列 `document_structure/policies/registry_v1.json` 与
`document_structure/policies/span_qualification_distribution_v1.json`；TS4-B 另固定列
`document_structure/policies/span_qualification_approval_v1.json` 与
`document_structure/policies/span_qualification_frozen_v1.json`。manifest 必须写 typed
`stage ∈ {TS4-A,TS4-B}`、
qualification policy ID/version/threshold/approval fingerprint，并断言 A⇔threshold=None、
B⇔已批准有限数值；另写 `factor_table_fingerprint`、TS4-A 绑定的 run/root/machine-index/review-attestation
身份及 `span_snapshot_aggregate.json` / `confidence_distribution.json` 文件 SHA256。run-id 文本不能代替该门。

### 18.14.2 冻结输入绑定（必须有仓库内信任锚；允许等价重建，不允许另一棵树）

绑定目标：`evaluation/results/tree_structure_ts3_outline_ts3_outline_tocr2_closure_p2final_20260918T130000Z/`
（`run_manifest.json` + `artifact_index.json` + 四份文档目录）。仓库内新增只读 lock fixture，至少固定：

- `run_manifest.json` SHA256 = `c418f74cf44f12e38ce0dd86099ca0386228f8d98ea00f3d5cfef37354b466a4`；
- `artifact_index.json` SHA256 = `e3a26ddbb5dce6f6516558881f07bb248f4061a9f7bd6f5b8241884466cfe15e`；
- 每份原 PDF/layout/outline/alignment artifact 的 index hash 与期望 document identity。

同目录 manifest/index/check 互相一致不构成信任；runner 必须先对 pinned root，再做目录内闭合：

1. 校验 pinned root、manifest / index 的哈希与读回（T34 / T35）；
2. 对冻结 run 的四份文档，用公开 `from_dict` 还原 `page_layout.json → PageLayout`、
   `document_outline.json → DocumentOutline`；随后必须用 lock 指向的原 PDF 调用
   `validate_layout_readback`/正式 layout verifier，比较 raw source SHA 与完整 canonical PageLayout 后才签发
   evaluation-only `VerifiedPageLayout`。`formal_unassigned.json` 只做恢复后交叉核对，**不得**参与
   `DocumentOutline` 构造；仅“PDF SHA 相同 + 调用者重算 layout ID”不算通过；
3. 对三份真实 Evidence-backed 文档，对齐终态从冻结 `normalization_alignment.json` 的 `rows` 用 `from_dict` 还原
   （**禁止**重跑 `align_block` / `align_evidence_set`）；同时核对
   `terminals_missing_count == 0`、`block_count`、`terminal_schema_versions`；全部通过后才由
   `_issue_pinned_ts3_handoff` 内部按 `PINNED_ALIGNMENT_ISSUER_VERSION=vaip-1` 签发 terminal
   capability，普通
   `EvidenceSetAlignment`/terminal tuple 不得进入正式 TS4 入口；
4. 三份真实文档的权威快照**只**从 `document_structure/evidence_gateway.py` 的 pinned-root issuer 取得；
   issuer 固定读取工作区正式 `data/evidence.db`，先绑定 resolved path + 文件 size/mtime/SHA256 + root lock，
   **无 CLI `--ev-db` 或任意 path 参数**，再经 `evidence.store`（`?mode=ro + query_only`）→
   `snapshot_from_gateway(...)`；并交叉核对冻结产物记录的
   `evidence_set_snapshot.fingerprint` / `member_identity_sha256` / `block_count` /
   `evidence_set_version`（`member_identity_sha256` 的算法 = 按 `(page_number, block_index)`
   排序后的 `member.identity` 清单的 `json.dumps(..., separators=(",",":"))` 之 sha256）；
5. 三份真实文档的 `EvidenceBlockInput` 全部由同一 pinned-root 只读 issuer 构造；禁止 runner 另写
   SQL/gateway；运行前后 DB identity 必须逐字段相等；
6. 对冻结 run 的四份文档：用 lock 中同一原 PDF、冻结 PageLayout 与当前冻结 oa/hq/trg/tocr 版本调用
   TS3 builder 确定性重建内部 `OutlineBuildResult`；重建 outline 的 canonical JSON 必须与第 2 步
   冻结 outline **逐字节相等**。只在相等后调用 `OutlineStructureSnapshot.from_build_result(...)`，
   并以对象级验证器核对每一非家具行；不相等即停止，不挑用投影行状态兜底；
7. 前述 root/PDF/outline/Evidence/terminal 核验全部通过后，每份文档只签发一个不可序列化
   `VerifiedTS3Handoff`；三份真实 Evidence-backed 文档随后由 TS4 自己算：表格范围、run、span、组件、覆盖、守恒。
   冻结产物里的 `line_structure_assignment/formal_unassigned/table scope/components/coverage`
   一律不得进入正式输入；
8. 冻结 run 内的 `FIXTURE_BOND_2026` 因 `evidence_set_version=null`、0 block，**只能**得到 typed
   `missing_evidence_set` fail-closed 负例，不得生成 `SpanBuildSnapshot`，也不得被计作正向第四文档；
9. 另从 `evals/fixtures/tree_structure/non_300750_ts4/manifest.json` 读取一个版本化正向 fixture，
   由 fixture-root issuer 逐成员哈希核验后签发
   `issuer_scope=pinned_acceptance, source_kind=versioned_fixture` 的 handoff（**不是 testing scope**），并走
   与三份真实文档相同的 TS4 私有纯构建/验证核。manifest 成员缺失、身份不符、testing handoff 进入
   aggregate 或越权使用生产 gateway 均拒绝。

**说明**：对齐终态禁止重跑；树的历史重建仅用于弥补 TS3 当时未持久化 build snapshot，且必须
canonical 等价。未来正式链在 TS3 构建当次直接产出 `OutlineStructureSnapshot`，TS4 不依赖内部
`OutlineBuildResult`。正向非 300750 fixture 不写 `data/*.db`；旧冻结 fixture 的“无 Evidence 集”仅保留
为独立负例，不能代替公司无关性的正向验收。runner 必须有**两个显式分支**，不得用“对四份文档执行
同一 Evidence 步骤”把负例伪装成正向或令整轮不可达。

### 18.14.3 产物清单与两阶段 create-only seal（全部是**验收投影**）

机器阶段一次性写入后不可修改：`run_manifest.json`、`machine_artifact_index.json`、
`machine_artifact_index_check.json`、`database_immutability.json`、`structure_snapshot_index.json`、
`qualification_policy.json`、`span_dispositions.json`、`span_components.jsonl`、`span_coverage.json`、
`conservation.json`、`synopsis.json`、`confidence_distribution.json`（直方图 + 每档行/字符数 +
factor-independent boundary-cause cross-tab + 逐 disposition feature row）、
`span_build_snapshots/<document_key>.json`（每个正向样本的**完整 canonical `SpanBuildSnapshot`**）、
`span_snapshot_index.json`（每项含文件 SHA256、raw snapshot `content_fingerprint`、`snapshot_id`、
`input_fingerprint` 与计数）及固定文档序的 `span_snapshot_aggregate.json`。

runner 同时创建但**不纳入 machine index**的 `manual_review.md` 空白模板与严格版本化的
`policy_decision.json` 空白模板。evaluation-only 常量固定为
`POLICY_DECISION_REVIEW_SCHEMA_VERSION="pd-1"` 与
`REVIEW_ATTESTATION_SCHEMA_VERSION="ra-1"`，写入 run manifest 与对应 JSON；它们不是生产 wire 类型，
但仍进入 runner code fingerprint。`PolicyDecisionV1(pd-1)` 的封闭字段为：`decision ∈
{approve,reject,needs_changes}`、A run/root/machine-index/aggregate/distribution identities、完整
`factor_entries`、`threshold: float|None`、按固定 8 个 review check ID 恰好各一条的
`review_verdict ∈ {pass,fail,not_reviewed}`、`reviewer_roles=(user,codex)` 与 review 文件 SHA；未知/缺失/
重复字段一律拒绝。`approve` 要求有限 threshold、完整 factor table、8 项全 `pass`；其余 decision
不得取得 approval eligibility。

用户 + Codex 填写后，独立命令 `--seal-review <same-run-dir>` **不能只复核目录内部哈希**；它必须先从
仓库 `ts4_trust_roots.json` 重新执行 §18.14.2 第 1–9 项全部根校验，重算当前
`CODE_FINGERPRINT_FILES` 并与 A run manifest 精确一致，拒绝 machine index 的 missing/extra 文件；
再为每份完整 raw snapshot 重建对应 historical/fixture pinned handoff，调用
`_verify_from_pinned_handoff` 做 canonical 全对象验证；最后解析 `PolicyDecisionV1`、按获批
factor/threshold 重放冻结 feature rows，并**只追加一次** `review_attestation.json`。
attestation 绑定：machine index SHA、final manual review SHA、policy decision SHA、decision、review schema、
A run/root identity、aggregate snapshot SHA、distribution SHA 与 replayed distribution SHA。
已存在 attestation 时重复 seal、seal 后任何机器/review/decision 字节变化、或尝试覆盖旧文件均拒绝。
`reject/needs_changes` 可得到 `approval_eligible=false` 的审计 attestation，但 TS4-B approval/frozen asset
导出与 B resolver **只接受** `decision=approve && all_required_review_checks_pass` 的 attestation。
TS4-B approval record 必须绑定 attestation 文件 SHA；不得直接绑定未封存的 Markdown。

### 18.14.4 人工验收清单（至少覆盖，逐项在 `manual_review.md` 留痕并经 attestation 封存）

1. **主营业务**正文是否完整（无缺行、无错切）；
2. **核心竞争力**下的子标题是否被正确分离（不并入父节点正文、不切断父节点正文）；
3. **主要子公司**材料是否**未**串入公司治理或财务风险部分；
4. 财务附注文本与表格是否被隔离（`table_inside` 全量可见、无一段落入正文 span）；
5. 一条真实**跨标题 Evidence**（同一 block 覆盖两个节点内容）是否被正确拆成**多个 provenance
   组件区间**（每个落点各有一条组件记录，且**无任何** span 声称独占整条 Evidence）；
   若真实样本中**未出现**此类 Evidence，必须在 `manual_review.md` 中**明确写"未出现"**并给出
   检索口径（不得以沉默代替结论）；
6. 表题 / 单位行是否**未**出现在任何 snippet 中；
7. **非 300750 正向 fixture**是否以独立版本化 Evidence/terminal manifest 走完与真实文档相同的
   span、component、coverage、conservation、synopsis 流程；同时旧冻结 fixture 无 Evidence 集的路径
   是否仍 fail-closed。前者证明通用性，后者证明缺源安全性，二者不得互相替代；
8. 全部守恒缺口与不可引用缺口是否**逐条**诚实显示（不聚合、不掩盖）。

### 18.14.5 验收阶段与门

- **TS4-A**：产出 `confidence_distribution.json` 与上述产物，`SPAN_CONFIDENCE_MIN` 仍为 `None`
  （completion 资格 False，但可独立验证 navigation synopsis；不得宣称关闭或支撑 `set_complete`）；
- **人工门**：用户 + Codex 查看 boundary-feature 分布与完整 raw snapshots，填写 review 与精确 policy
  decision 后执行一次 create-only `--seal-review`，冻结完整 `SpanQualificationPolicy`（因子表 + threshold
  + attestation + 批准指纹）。只有 sealed attestation 可导出 TS4-B approval/frozen assets；
  **不得**用修改全局常量的方式重新解释 TS4-A 快照；
- **TS4-B**：以 frozen policy 的新身份重跑，`SPAN_CONFIDENCE_MIN` 仅与该 policy 同步发布；
  逐项通过后方可正式关闭 TS4。

## 18.15 编码与提交节奏（两批编码；不是 A1–A8 逐项编码）

| 批次 | 一次性内容 | 验证与提交门 |
|---|---|---|
| TS4-A | **同一编码批**：8 个 typed 顶层对象与全局版本登记、受信输入/完整重算 verifier、span builder、四域坐标投影、三级守恒、synopsis、completion predicate、全部反例测试、runner、根 lock 与非 300750 正向 fixture | 先跑全部聚焦测试，再跑**一次完整** `python -m evals.run_evals`，再生成一次全新 TS4-A 真实产物；实施方停止，不 stage、不 commit。用户 + Codex 独立验收通过后，允许一个“TS4-A 实现+测试”原子 commit；产物不提交 |
| TS4-B | 仅把 attestation 封存的完整 `SpanQualificationPolicy`（因子表、阈值、批准指纹）冻结并进入身份；按 §18.11.2 同步 3 个既有阈值测试与新增 A/B 真值表测试；生成一次全新 TS4-B 真实产物 | 同样先聚焦、后**一次完整** eval；必须证明历史 A 指纹/验证不变且 completion=False；实施方停止，不 stage、不 commit。独立验收通过后允许一个 TS4-B policy/test commit |
| 文档状态 | 只记录实际已发生的验收与 commit，不预写“通过/关闭” | 与代码分开提交；`manual_review.md` 属生成产物，不进 Git |

**提交纪律**：A1–A8 是同一套不变量，**不得**拆成八轮实现/申请；也不得在未跑完整 eval 时提交任何
中间状态。每一阶段最多一个代码/测试原子 commit，文档另交。任何 commit 前均须已有同一内容状态下的
完整 eval 结果且经用户 + Codex 验收。现存 tracked deletion、未跟踪结果目录及其他任务产物**一律不得**进入提交。

## 18.16 工时估算（**7 – 9 人日**；按两批执行，不按决策项逐轮）

| 项 | 人日 |
|---|---|
| typed 类型、版本/legacy、根 lock、受信输入与完整重算 verifier | 1.5 – 1.8 |
| 唯一输入链、run/处置/span、四域映射、组件与覆盖 | 2.0 – 2.5 |
| 三级守恒、synopsis、唯一 completion predicate | 1.1 – 1.4 |
| 反例矩阵、非 300750 正向 fixture、runner、TS4-A 真实验收 | 1.6 – 2.0 |
| TS4-B policy 冻结、重跑、独立验收与状态文档 | 0.8 – 1.3 |
| **合计** | **7.0 – 9.0** |

**不得**通过删减下列任一项来压缩：权威输入闭合（§18.3）、字符映射（§18.5）、三级守恒（§18.9）、
真实验收（§18.14）。

## 18.17 P0 / P1 停止条件

**P0（必须立即停止并上报）**：任何"放宽 fail-closed / 采信自报 / 改写冻结产物 / 伪造完备性"的处置。

**P1（**必须阻塞**本批完成；不得再写"P1 不阻塞本批完成"）**：

1. 身份类：`span_id` / `component_id` / `coverage_id` / `snapshot_id` / `synopsis_id` 非确定性，
   或不由真实上游对象重算；
2. 权威类：任何自报派生结果进入输入、任何"冻结投影当权威"、任何终态缺失 / 多余被放过；
3. 守恒类：三级守恒任一不成立，或缺口被聚合掩盖，或跨 Evidence 相加被放过；
4. 覆盖类：可引用覆盖非并集、片段跨越未覆盖区间、重叠 Evidence 重复计数；
5. 版本类：§18.12.2 任一项证明不成立（此时按"唯一显式阻塞项"上报）。

**可以登记为不阻塞 gap / P2（必须逐条可见）**：诚实的低覆盖、`length_exceeded` 的极短节点、
`offset_unverifiable` / `boundary_inexact` 计数、`table_inside` / `table_adjacency` 体量、
fixture 无 Evidence 集这一环境限制。

## 18.18 已裁决实现口径与唯一后置人工门

除 A1 的**具体冻结数值**必须基于 TS4-A 真实分布后再决定外，其余项已由本次计划审查裁决，
Claude Code 不得逐项再次申请，也不得自行改写：

| # | 事项 | 已裁决口径 | 违反后的影响 |
|---|---|---|---|
| A1 | `SpanQualificationPolicy` 的因子表与阈值 | TS4-A 固定 `stage=distribution_only`、threshold=None；真实分布与完整 raw snapshots 后由用户 + Codex **共同冻结整张因子表 + threshold**，经 create-only review attestation 封存，再由 TS4-B 发布 approval/frozen policy 身份 | 编码前猜阈值、只批准阈值不批准因子表或绕过 attestation，会用参数换“完整”；修改全局值重解释 A 快照会破坏不可变身份 |
| A2 | 验收 runner 是否可用 `from_dict` 还原冻结 TS3 对齐终态 | **可以，且必须**（重跑对齐被禁；还原后须过 §18.3.4 全部对象级闭合） | 不允许则只能重跑对齐（违反禁则）或放弃真实验收（验收退化） |
| A3 | synopsis reason 词表与 `_check_version` | `SYNOPSIS_SCHEMA_VERSION nss-1→nss-2`、`SYNOPSIS_VERSION ns-1→ns-2`；`ns-1/nss-1` 只识别后显式拒绝并要求重算，不作为 current 读回；`_check_version` 绑定常量名 | 在 nss-1 上扩词会同一 wire 版本两套接受集合；不绑定会混淆 legacy 与未知版本 |
| A4 | `char_map` 存储序升序的收紧位置 | **只在 TS4 正式输入 verifier** 拒绝乱序；不改变冻结 `als-3/alr-2` 的构造/from_dict 接受语义，TS4 也不得排序后继续 | 修改旧 wire 无 migration 会破坏兼容；排序后继续会把不可信输入变成“合法” |
| A5 | `component_evidence_refs` 的 `cs/ce` 坐标域 | **`T_e`（Evidence tight 域）**，且 TS4 追加 `ce <= block_char_length` 与 walk 逐段证明 | 不定域则 TS1 校验不覆盖上界，伪造的越界 offset 无法被发现 |
| A6 | 句末 token 与闭引号 | `SENTENCE_TERMINATORS=("……","。","！","？",".","!","?",";","；")`；按 token 长度降序；后接 `CLOSING_QUOTES=("”","’","\"","'")` 时一并收录。单个省略号/逗号不算句末 | 不定则片段边界不可复核，`length_exceeded` 判定会随实现漂移 |
| A7 | 非 300750 fixture | 新增仓库内**版本化、非公司专用** Evidence/terminal manifest，走完整 TS4-A/B 正向路径；旧冻结 fixture 无 Evidence 集继续作为独立 fail-closed 负例 | 只有无 Evidence 负例不能证明通用性；临时伪造线上 Evidence 又会破坏权威边界 |
| A8 | `E_empty` 是否切 run | **切**（保证 §18.5.3-1 引理前提：run 内无空行） | 不切则 `off_i` 会引入空行占位，`L→S` 映射不再可重算 |

## 18.19 架构冲突与兼容迁移审计（C 系列）

| # | 位置 | 口径 A | 口径 B | 推荐裁决 | 不裁决的影响 |
|---|---|---|---|---|---|
| C1 | `schema.py:2279` `NavigationSynopsis._check_version` 调用 | 现实现：4 参数，未绑定 `constant_name`（`ns-1` 只能报通用错误） | 已裁决口径：legacy 版本必须走"须显式迁移 / 重算"专用错误；扩词同时要求 nss-2 | **已由 A3 裁决**：升级 nss/ns 并绑定常量名 | 旧版本接受集合或错误语义漂移 |
| C2 | `validate_alignment_partition`（`schema.py:2663`） | 实测：在**排序副本**上校验，**存储序升序未强制** | 冻结 `als-3/alr-2` 不得无迁移改变接受集合；TS4 正式输入又必须拒绝乱序 | **已由 A4 裁决**：旧 wire 可读，TS4 verifier 拒绝，绝不排序后继续 | 修改旧 wire 会破坏兼容；TS4 排序会掩盖不可信输入 |
| C3 | 冻结产物 `FIXTURE_BOND_2026` | 实测：`evidence_set_version = null`、0 block、无 `evidence_set_snapshot` | 公司无关性需要完整正向链，同时缺 Evidence 必须 fail-closed | **已由 A7 裁决**：新正向 fixture + 旧负例并存 | 只跑旧负例无法证明泛化；把负例写成通过违反 fail-closed |
| C4 | `SYNOPSIS_REASON_CODES`（`schema.py:116`） | 冻结词表 4 值 | TS4 需要表达"等 TS5" | 追加 `table_only_pending_ts5`（已批准） | 该状态只能塞进 `no_span`，缺口不可审计 |
| C5 | 本计划 §13.2 的 TS4 工作量 | 原历史表述：3–4 人日 | 本次完整审计：7–9 人日（新增受信结构快照、完整重算 verifier、四域映射、三级守恒、正向 fixture、A/B 门） | **已同步修改 §13.2 与 §18.16 为 7–9 人日** | 若仍按 3–4 人日排期，会压缩权威闭合、反例与真实验收 |
| C6 | `sb-1` 使用事实 | 旧 §十八断言"`sb-1` 从未被真实使用" | 实测：TS3 已用 `sb-1` 产出 **232** 个 unassigned span，且 builder 默认依赖该常量 | 保持 `SPAN_BUILDER_VERSION=sb-1`；新增独立 `TS4_BODY_SPAN_BUILDER_VERSION=sb-2`，用角色/写路径真值表隔离 | 升全局常量会令冻结树无法读回且无法 canonical 重建；继续用 sb-1 写新正文又会混淆算法语义 |
| C7 | §13.2 曾要求 TS4 改 `harness/topic_schema.py` 的 `MATERIAL_TYPES` | 实测：`MATERIAL_TYPES` 已含 `evidence_span/table_context` | Pack locator 后继与迁移属于 TS7B | **§13.2 已同步**：TS4 不改 `harness/**`，TS7B 处理 Pack 后继 | 提前动 Pack 会跨批次并制造第二套语义 |
| C8 | §13.2 曾写"无损重构不变式" | `canonical_text` 是确定性规范化重构 | 原文无损性由 PageLayout+定位器和三级守恒保证 | **§13.2 已改为确定性重构/三级守恒** | 保留旧词会暗示 normalized text 可替代原文 |
| C9 | §13.2 曾把 resolver `_validate_envelope` 列作 TS4 门 | Pack resolver 属 TS7B | TS4 只验证 span snapshot | **§13.2 已同步移交 TS7B** | 强留会迫使 TS4 越界改 Pack Store |
| C10 | fallback 计数 | TS3 的 232 个 inherited unassigned span 均非 fallback；TS4 regular span 禁止 fallback | T18 对 **TS4 新 spans + inherited 引用集合**分别验证：新 spans fallback=0；inherited 对象保持历史字段不被重新解释 | 采用该双集合断言；有界相邻扩读仍归后续 fallback 批次 | 把 inherited ID 当 TS4 span，或在本批实现扩读，都会混淆边界 |

**冲突处置纪律**：C 系列只**列出**与推荐裁决，**不得**由本节自行修改上位设计
（`AGENTS.md` / `DOCUMENTATION_INDEX.md` / `DESIGN_V2.md` / `V2_IMPLEMENTATION_PLAN.md`）；
凡推荐裁决与上位设计冲突，一律以上位设计为准并退回重写本节。

## 十八 · 小结

TS4 的可执行语义**只有本节这一套**：唯一正式输入链（§18.3，受信 gateway/provider + 完整对象重算）、
四域坐标映射与 walk（§18.5，`T_e→L_s→L_l→S`，实测 63418/63418）、最大正文 run
（§18.6，仅真实 run 抵达末行时使用文档末边界）、
表格 `deferred_to_ts5` / `table_adjacency_pending_ts5`（§18.7）、provenance ⊥ 可引用资格与
并集覆盖（§18.8）、三级 `⊎` 守恒（§18.9）、8 个 typed 顶层对象与完整反自证验证器（§18.4）、
版本与 legacy 矩阵（§18.12）、测试与真实验收（§18.13 / §18.14）、两门结构（§18.1.1 / §18.14.5）。

> **历史执行前结论（已由文件顶部“TS4 关闭记录（2026-09-19）”取代）**：
> 当时 TS4-A 规格已具备作为完整编码批开始的条件，A2–A8 不再逐项等待确认；A1 仍待真实分布后由
> 用户 + Codex 冻结。该门现已实际完成并关闭，不得据此重新启动 TS4 或改动冻结策略。任何 span
> 仍不得仅凭本阶段资格自动支撑 `set_complete`。

---

# 十九、TS5 TableObject 权威实施计划（Codex 直接起草，用户已批准）

## 19.0 文档控制、适用范围与替代关系

本节是 TS5 的**唯一权威编码前执行规格**。它在不改变上位业务设计的前提下，细化
`TREE_STRUCTURE_ADJUSTMENT_TASK.md` §4.5、§9、§10 与本文件 §18.7.3 / §18.10.2 已冻结的
TS4→TS5 交接语义。**本段状态句记录 2026-09-19 批准时快照**：计划已获用户批准并授权一个完整编码批；实际执行后的 no-go 结论见文件顶部“TS5 执行停止记录”，不得用本段旧快照覆盖。

### 19.0.1 编码中 P1 架构勘误（2026-09-19，用户已批准方案 C）

TS5 首轮编码暴露出本节原版本表的一处阶段所有权遗漏：原 §19.4.1 把全局
`SYNOPSIS_SCHEMA_VERSION / SYNOPSIS_VERSION` 直接升为 `nss-3 / ns-3`，会使 TS4 的
`NavigationSynopsis`、`SpanBuildSnapshot spn-1` 与 `SynopsisSourceValidation nsv-1` 也随全局常量
改写，从而改变 TS4-B 已封存快照的 canonical 内容、snapshot ID 与 verification fingerprint，并使
`evals.test_tree_span_policy_b` B6 的完整历史读回契约失效。该问题属于本计划的版本边界遗漏，不是
允许实施方通过缩窄 B6 或新增 `spn-2` 消化的普通测试问题。

用户批准采用**方案 C**作为本节的强制勘误，优先于 §十九后文任何冲突旧句：

1. TS4 `NavigationSynopsis` 永久冻结为 `nss-2 / ns-2`，使用 TS4 五值 reason 词表；
2. `SpanBuildSnapshot spn-1` 与 `SynopsisSourceValidation nsv-1` 只接受该精确组合，TS4 build/read/verify
   必须继续逐字复现已封存 TS4-B snapshot ID；不得新增 `spn-2`；
3. TS5 新增独立 public wire type `FinalNavigationSynopsis`，使用独立版本轴
   `FINAL_SYNOPSIS_SCHEMA_VERSION=nss-3` / `FINAL_SYNOPSIS_VERSION=ns-3`，来源只允许
   `FinalOutlineSpan (fos-*)`，且只允许进入 `FinalMaterialStructureSnapshot fms-1`；
4. `SpanBuildSnapshot` 必须拒绝 `FinalNavigationSynopsis` 与 `nss-3/ns-3`；final snapshot 必须拒绝
   TS4 `NavigationSynopsis` 与 `nss-2/ns-2`；不得按 child 猜 reader、不得混装；
5. B6 的“完整 TS4-A 快照在后续环境仍可完整读回、canonical/ID/fingerprint 不变并保持
   completion=False”契约原样保留，不得降级为只读取 synopsis 叶子；
6. 首轮编码生成的、绑定已漂移 TS4 snapshot ID 的 `ts5_trust_roots.json` 不是合法基线，必须从已封存
   TS4-B run/index/policy 身份重新派生并逐份比对；
7. TS5 final synopsis 的新 reason 不得进入 TS4 五值词表；TS4 的
   `table_only_pending_ts5` 也不得进入 final reason 词表。

本勘误只隔离 TS4 冻结输入与 TS5 final 输出，不改变表格业务范围、真实样本、守恒、authority 或
TS5 关闭门。**以下是 2026-09-19 编码时指令，现已执行完毕并暂停**：实施方当时应在现有未提交 TS5
工作区上做精确修复，不得 reset、checkout 或丢弃其他已完成且符合 §十九的实现。当前入口以文件顶部
“TS5 执行停止记录”和 Demo 轨 M930-3 的 successor 编码前门为准，不得据此继续 TS5 编码。

本节同时作出两项明确替代，防止实施方从历史段落中任选一种解释：

1. **替代旧 §2.4 的“文本起点规则 AND 几何网格”算法。**现有
   `detect_table_start_flags` 会主动排除显式“表 N”和续表标记；若把它作为所有表格的必要条件，
   会系统性漏掉真实带表号表格和续表。本节改为“多源候选并集 → 同一几何/来源/守恒验证门”；
   无真实结构仍不得构造 `TableObject`。
2. **具体化 §18.7.3 的 successor 方向。**TS5 必须发布 `TableObject to-4`、不可变 overlay 与
   final capability；不得继续用 `to-3` 的非空 `component_span_ids` 要求迫使表体伪造
   `OutlineSpan`。

本节中的版本号、public type、输入输出、真值表、测试、真实验收、提交节奏与停止条件均已裁决。
Claude Code 负责按规格实现，不得重新解释业务、改用另一套表格真相、按 A 项逐轮申请，也不得因
时间压力删减真实验收或加入 300750/页码/表号特例。若现场事实与本节矛盾，只能停止并提交
“冲突位置 + 复现证据 + 最小推荐”，不得自行改写本节。

## 19.1 目标、完成定义与非目标

### 19.1.1 本批目标

TS5 在 TS4 冻结 span 能力之上，一次性完成以下闭环：

1. 只接受经验证的 `VerifiedSpanSnapshot`，从其中绑定的同一份原 PDF bytes、
   `PageLayout`、`DocumentOutline`、Evidence snapshot/blocks 与 alignment terminals
   确定性恢复表格；
2. 把单页物理表格表达为版本化、只读、可回查的 `TableObject to-4`，分别保留表题、单位、
   物理表头、表体、小计/合计、单元格内部结构与完整 provenance；
3. 用 typed relation 表达表前引入、表后解释、表注、显式引用和跨页续表；文字与表格分别成为材料，
   但可以通过关系组合；
4. 对 TS4 的 provisional 范围产出不可变 `TableRangeDecision`，对**全部** TS4 components
   产出精确等集的 `FinalComponentBinding`；
5. 对保留为正文的 provisional 范围重建 final span、可引用覆盖、守恒与 synopsis；原 TS4 快照
   永不原位修改；
6. 产出 `FinalMaterialStructureSnapshot`，经独立重建与 canonical 全比较后，只以
   `VerifiedFinalMaterialStructureSnapshot` 交给 TS6/TS7；
7. 对无法稳定恢复的区域诚实输出 `partial / unsupported / unresolved` 与完整原文定位，
   不以“所有候选都成功恢复”为关闭条件；
8. 通过三份真实 PDF、非 300750 正向 fixture 和人工可读预览，证明通用表格、跨页续表、
   财务附注、非财务表、表内小标题与视觉对象负例都被正确处理。

### 19.1.2 TS5 的正确完成定义

TS5 关闭要求的是“**能力正确、守恒、可复核**”，不是“所有表格均 complete”：

- 每个 `table_inside/table_adjacency` disposition 有且仅有一个诚实终态；
- 每个 TS4 component 有且仅有一个 final binding；
- 成功恢复的对象通过几何、网格、来源、可引用覆盖、关系和 continuation 的对象级验证；
- 未成功恢复的对象仍保留全文、来源、定位、候选原因和 gap；
- 普通段落、组织结构图等视觉对象不会被伪造成表格；
- 两个表内小标题及其后续多段文字完整保留、可定位、可检索；
- final 守恒差额为 0，任何不可引用部分逐项显示；
- 真实负面终态是合法业务结果，不因不是 `complete` 自动等于 capability 失败。

### 19.1.3 非目标与阶段边界

TS5 **不做**：

- TS6 的 `AspectNavigationProfile`、节点/表格的 aspect 排名；
- TS7A 的跨树、跨文档、跨版本材料聚合和完整材料库样板；
- TS7B 的 Retriever/ToolRegistry/Store/Pack/Citation/P4 provenance 接线；
- Pack v5、Store migration 4、SQLite 表、索引或 checkpoint；
- R3 aspect 调度/事实形成、R4 外部漏斗、R5 Writer、章节或报告正文；
- FinancialSnapshot/FinancialFactPack 的数字抽取、计算、勾稽或权威替代；
- OCR、扫描 PDF、MinerU、Docling 或其他外部结构引擎；
- 重写 TS3 标题树、TS4 span、冻结策略、历史 Evidence/Evidence Set 或历史产物。

## 19.2 已裁决架构口径（不再逐项申请）

| # | 事项 | TS5 冻结口径 | 禁止的替代 |
|---|---|---|---|
| T1 | current table wire | 发布 `to-4`；现有 `to-3/tb-1` 仅 history reader 识别后明确要求重建 | 在 `to-3` 填空 span、假 span 或字符串 ID |
| T2 | 物理/逻辑边界 | 一个 `TableObject` 仍是**单页物理片段**；跨页以 typed `continued_by` 链连接 | 一个 bbox 冒充多页表，或跨文档合并 |
| T3 | 候选入口 | 冻结 table dispositions、显式表题/续表、generic table start、真实几何网格为**候选并集**；统一过结构验证门 | 把 `detect_table_start_flags` 当所有表格的硬性必要条件 |
| T4 | 无传统表头 | `structure_kind` 支持 `headered_grid / key_value_form / headerless_grid`；只有真实网格/表单证明才允许无表头 | 为 key-value 表伪造一行表头，或把普通双栏文字当表 |
| T5 | cell 内容 | cell 保留完整文本及有序内部 blocks，可表达换行、多段、列表和 cell 内小标题 | 只保存首个 LayoutSpan locator 或压成无结构单行文本 |
| T6 | provenance | 表体原子 provenance 绑定真实 `SpanEvidenceComponent.component_id` 与可选 `disposition_id`；被吸收的 caption/unit/note 绑定其 source component 与 typed relation，只有被决议保留为段落的表前/表后说明才绑定真实 final span | 以 `component_span_ids` 证明表体、以调用方自报 ID 自证 |
| T7 | 可引用资格 | cell/range 逐段计算；结构成功不等于全表可引用 | 一条 aligned component 自动授权整表 |
| T8 | typed relation | 新增独立 `TableRelation` overlay，不改冻结 `DocumentOutline/ReferenceEdge` | 把所有关系塞回 outline，或仅凭字符串自报关系 |
| T9 | TS4 regular span | 不静默把 `regular/body_span` 重分类为表；发现此类几何冲突输出 `upstream_table_scope_miss` 并阻断对应 final completion | 只改 binding target 就偷换已经冻结的正文 |
| T10 | unassigned component | 已验证 cell 几何可吸收同一物理表内的 inherited/formal/body-unassigned component；保留其原 landing 与原因 | 只遍历 table dispositions，导致表内标题/正文丢失 |
| T11 | final paragraph | `kept_as_paragraph/absorbed_into_body` 使用 `sbf-1` 重建，并重新应用冻结 TS4-B 0.85/12 因子策略 | decision 本身自动升级为 citable |
| T12 | synopsis | final synopsis 仍只抽取 final 可引用 paragraph span；table 由 TS6 直接索引标题/表头/路径 | 把 table ID 填进现有 `span_id` 或把表题冒充证据 snippet |
| T13 | table-only node | 新增独立 `FinalNavigationSynopsis`（`nss-3/ns-3`）并使用新 reason `table_material_available_no_text_synopsis`；TS4 `NavigationSynopsis`（`nss-2/ns-2`）继续保持 current/frozen 契约，不得降为 history-only | TS5 后继续使用 `table_only_pending_ts5`，或让 TS4/TS5 synopsis 共用类型、版本轴或 reason 词表 |
| T14 | authority | `TableObject.is_financial_authority() == False` 永远成立；结构分类与数字权威正交 | 用完整表格结构替代 FinancialSnapshot 或自动授权金额 |
| T15 | persistence | 本批只写 create-only 验收产物，无 SQLite migration/Store 写入 | 提前实现 Pack v5/migration 4 或修改 Evidence DB |
| T16 |实现引擎 | 复用当前 PyMuPDF PageLayout + 固定版本 pdfplumber 几何；不引入外部引擎 | 为赶进度改技术栈，或通过 LLM 猜单元格 |

## 19.3 唯一受信输入链与运行时能力

### 19.3.1 唯一 public I/O

```python
def build_final_material_snapshot(
    verified_span: VerifiedSpanSnapshot,
) -> FinalMaterialStructureSnapshot: ...

def verify_final_material_snapshot(
    snapshot: FinalMaterialStructureSnapshot,
    verified_span: VerifiedSpanSnapshot,
) -> VerifiedFinalMaterialStructureSnapshot: ...
```

正式入口不接受 raw `SpanBuildSnapshot`、JSON、PDF path、任意 bytes、调用方自组 PageLayout/
DocumentOutline/Evidence/terminal 集合，也不接受测试 fake 冒充正式能力。验收 runner 只接受由 pinned
TS4 trust root 重新签发的 `issuer_scope=pinned_acceptance` wrapper；未来生产只接受正式 production
issuer。testing scope 只能用于单元测试，不得进入真实验收聚合。

### 19.3.2 PDF 与根对象来源

TS5 必须从 `verified_span.handoff.layout_capability.source_bytes` 读取几何；这些 bytes 已与构建
`PageLayout` 的 raw source identity 绑定。禁止另收 path 后重读文件，避免 TOCTOU。构建前逐项核对：

- wrapper issuer/scope/version 与不可序列化能力登记；
- TS4 `snapshot_id/content_fingerprint`；
- `PageLayout/DocumentOutline/structure_snapshot` 身份；
- Evidence snapshot/member identity、全部 Evidence blocks；
- alignment terminal 集合、schema/algorithm/version；
- TS4-B frozen policy ID/version/file hash/policy fingerprint、0.85 阈值和 12 因子；
- raw PDF SHA256、pdfplumber 版本与固定 settings fingerprint。

上述任一不一致立即 fail-closed。相同输入与相同版本束重复构建，所有对象、顺序、JSON 和指纹必须
逐字节一致。

### 19.3.3 final capability

`VerifiedFinalMaterialStructureSnapshot` 是 runtime-only capability：

- 不能直接构造、序列化、copy、deepcopy 或 pickle；
- 持有 raw final snapshot、verified TS4 wrapper、scope/source kind/issuer version 与 verification
  fingerprint；
- 只有 verifier 从同一受信输入**独立重建**全部 candidates、objects、relations、decisions、
  bindings、final spans、coverage、conservation 与 synopses，并作 canonical 全比较后才能签发；
- verifier **禁止 import、调用或委托** `build_final_material_snapshot()`、`final_material_builder`
  的高层组装函数以及 `table_builder` 的候选裁决/行列组装函数；只允许复用 schema 校验、canonical
  编码、坐标变换和 raw geometry 读取等不含候选选择/业务裁决的低层纯原语；
- verifier 必须直接从 verified trust roots 重新枚举候选、重取 source fragments、重算 grid/cell、
  classification、relations、bindings、coverage 与三级守恒。候选 snapshot 自报的 decision、binding、
  table/cell identity 只能作为**待比较对象**，不能作为 verifier 的重建输入；
- 删除、增加、篡改任一成员后同步重算全部下游 ID/hash，仍必须被独立重建拒绝；
- TS6/TS7 只接受该 wrapper，不接受 raw JSON 或 `VerifiedSpanSnapshot` 绕过 TS5。

## 19.4 公共类型、封闭词表与版本轴

### 19.4.1 版本常量（编码时精确登记）

下列版本轴必须加入 `document_structure/versions.py` 的 current/legacy/registry/self-check，不得只在
业务模块放字符串：

| 版本轴 | TS5 current | 兼容策略 |
|---|---|---|
| `TABLE_SCHEMA_VERSION` | `to-4` | `to-3` 加入 legacy；history-only，current/final 一律要求重建 |
| `TABLE_BUILDER_VERSION` | `tb-2` | `tb-1` history-only |
| `TABLE_CELL_SCHEMA_VERSION` | `tc-2` | 旧 cell 只能随 to-3 history 读取 |
| `TABLE_GEOMETRY_VERSION` | `tgeo-1` | 首版，绑定坐标变换与 pdfplumber settings |
| `TABLE_CLASSIFICATION_PROFILE_VERSION` | `tcp-1` | 首版；绑定 table `structure_class` 的封闭规则与 profile file hash |
| `TABLE_CELL_BLOCK_PROFILE_VERSION` | `tcbp-1` | 首版；绑定 cell 内 heading/paragraph/list/line/unclassified 的封闭规则 |
| `TABLE_RELATION_SCHEMA_VERSION` / builder | `trl-1` / `trb-1` | 首版 |
| `TABLE_RANGE_DECISION_SCHEMA_VERSION` | `trd-1` | 首版 |
| `FINAL_COMPONENT_BINDING_SCHEMA_VERSION` | `fcb-1` | 首版 |
| `TABLE_CITABLE_COVERAGE_SCHEMA_VERSION` | `tcc-1` | 首版 |
| `TABLE_STRUCTURE_GAP_SCHEMA_VERSION` | `tsg-1` | 首版 |
| `FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION` | `fmc-1` | 首版 |
| `FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION` / builder | `fms-1` / `fmb-1` | 首版 |
| `VERIFIED_FINAL_MATERIAL_ISSUER_VERSION` | `vfmi-1` | runtime capability |
| `TS5_FINAL_SPAN_BUILDER_VERSION` | `sbf-1` | §18.7.3 已冻结 |
| `SYNOPSIS_SCHEMA_VERSION` / `SYNOPSIS_VERSION` | **保持 `nss-2` / `ns-2`** | TS4 `NavigationSynopsis`、`SpanBuildSnapshot spn-1`、`SynopsisSourceValidation nsv-1` 的冻结版本轴；TS5 不得修改或降为 history-only |
| `FINAL_SYNOPSIS_SCHEMA_VERSION` / `FINAL_SYNOPSIS_VERSION` | `nss-3` / `ns-3` | TS5 `FinalNavigationSynopsis` 独立版本轴；只允许 `fos-*` final span 来源，只进入 `fms-1`；不得进入 TS4 容器 |

编码前先运行版本注册表自检，新增类型逐一有 current、legacy/unknown 真值表。不存在 SQLite schema
变更，因此不得修改任何 DB migration 版本。

`FinalNavigationSynopsis` 是 TS5 新增的第 11 个顶层 public wire type，登记到 TS5 版本表并与
`NavigationSynopsis` 分属不同 schema 常量。二者即使字段形状接近，也不得使用 alias、继承式自动适配、
通用 `from_dict` 猜版本或“同一类挂隐藏历史标记”的方式合并；aggregate 类型本身必须决定合法 leaf。

以下 evaluation-only 版本不进入生产 wire registry，但必须集中定义在新增
`evaluation/tree_table_acceptance_schema.py`，由 runner、validate-only、seal-review 与 artifact tests
共同引用；禁止在 runner 内散落裸字符串：

| evaluation 版本轴 | TS5 current | 约束 |
|---|---|---|
| `TS5_ACCEPTANCE_RUNNER_VERSION` | `tar-1` | 绑定 runner 的机器验收语义；进入 run manifest 与 attestation |
| `TS5_TRUST_ROOT_SCHEMA_VERSION` | `ttr-1` | trust-roots 文件严格字段、固定排序、file/content hash；unknown 拒绝 |
| `TS5_RUN_MANIFEST_SCHEMA_VERSION` | `trm-1` | run identity、输入根、版本束、命令模式与 create-only 状态 |
| `TS5_ARTIFACT_INDEX_SCHEMA_VERSION` | `tai-1` | machine artifact index/check 的完整成员集合与逐文件 hash |
| `TS5_REVIEW_SCHEMA_VERSION` | `trv-1` | manual review/check IDs、reviewer roles 与 decision 的封闭 schema |
| `TS5_REVIEW_ATTESTATION_SCHEMA_VERSION` | `tra-1` | seal 身份、machine/review hashes、verifier/DB immutability 结论 |

evaluation schema 一律拒绝未知/缺失字段和未知版本，不做“尽力读取”；任何版本/file hash/check-ID 集合
变化都必须使用新 run_id 和新版本，不能原位重解释或覆盖既有 run。

### 19.4.2 `TableObject to-4` 与 cell 内部结构

新模块 `document_structure/table_schema.py` 定义 current wire，public class **固定为**
`TableObjectV4` 并导出 current alias `TableObject`；现有
`document_structure.schema.TableObject` 保持历史实现，不原位改写，显式作为 legacy v3 reader。
`document_structure.__init__` 的 current 导出指向 to-4，同时保留清晰的 legacy import 路径。

`TableObjectV4` 至少包含：

- locator/revision 双身份、schema/builder/geometry/settings 版本；
- document/layout/outline/verified-span 上游身份与 `upstream_dependency_fingerprint`；**不得**包含尚未
  生成的 final snapshot identity/fingerprint；
- 单页 page/bbox/index 与 mandatory `TableOwnerRef`；
- `structure_kind ∈ {headered_grid,key_value_form,headerless_grid}`；
- `structure_class ∈ {financial_main_statement,note_table,ordinary_business_table,unclassified}`；
- `structure_state ∈ {complete,partial}`、`missing_or_uncertain_fields`；
- title、unit、header/body/subtotal/total 的独立 typed rows；
- 完整 cell grid、merged-cell rowspan/colspan、每格有序内部 blocks；
- 表体 component IDs、Evidence ranges、terminal refs 与 cell 级来源；不得假设每个来源都有
  `alignment_id`；
- continuation candidate/anchor locator、内容/结构/provenance fingerprints；**不得**内嵌后生成的
  `TableRelation.relation_id` 或反向 relation 列表；
- `is_financial_authority() -> False` 的不可覆盖实现。

`TableCellV4` 不再只有“单行 text + 一个首 locator”。它包含 `source_fragments`：

```text
TableCellSourceRef:
  page/line/span/char_range + bbox
  component_id + evidence_id/evidence_char_range
  terminal_kind + terminal_id + terminal_locator + terminal_schema_version
  verdict + refusal_reason + citable reason

TableCellBlock:
  role = heading | paragraph | list_item | line | unclassified
  text + ordered source_ref_ids + content_fingerprint
```

`TableOwnerRef` 是严格 discriminated union，禁止 orphan：

- `owner_kind="outline_node"`：必须带 verified `node_id + node_locator + source_boundary_id`，不得带
  unassigned ref；三者均从同一 document/layout/outline roots 回查；
- `owner_kind="unassigned_boundary"`：`node_id=None`，必须带 verified TS3 unassigned span 或 TS4
  body/formal-unassigned disposition 的 locator/ID + source boundary；不得凭 bbox/字符串自造 owner；
- 两分支恰好一个成立。两者都缺、两者同时存在、跨 document/outline、source boundary 不包含 table
  physical locator 均拒绝。unassigned owner 的 `structure_class` 默认 `unclassified`，不得因表内文字猜成
  ordinary/financial/note class。

cell 的 canonical text 由 blocks 按源顺序以明确换行规则派生；source refs 精确并集必须等于该 cell
占用的真实 LayoutSpan fragments，不能缺失、重复、重叠或跨 cell。两个表内小标题作为
`role=heading` 的 cell block 保留，但**不**成为 `OutlineNode`。

terminal 字段必须沿用 `SpanEvidenceComponent` 的既有封闭真值表：alignment 与 refusal 都使用非空
`terminal_id/locator/schema_version`；只有 `terminal_kind="alignment"` 时才可派生可选
`alignment_id=terminal_id`，refusal 分支不得伪造 alignment ID。`alignment_offset_unverifiable` 与
`alignment_residue_unmapped` 因无可核验 cell layout fragment，不产生 `TableCellSourceRef`，而是连同其
terminal ref、Evidence interval 与原因保存在 `FinalComponentBinding(pending)`；不得为满足 cell schema
伪造坐标或 alignment。

### 19.4.3 overlay 与 final 类型

至少新增以下 frozen typed 对象；均严格 `to_dict/from_dict`、拒绝未知字段、内容寻址、固定排序：

1. `TableRangeDecision`：覆盖每个 table provisional disposition；字段和 target 真值表沿用
   §18.7.3，不得扩成自由字符串。
2. `TableRelation`：关系类型封闭为
   `introduces / caption_of / unit_of / explains / footnote_of / continued_by / references`；
   `reconciles_with` 仅保留 schema 扩展位，本批不得产生 resolved 关系或据此裁决数字。
3. `FinalComponentBinding`：覆盖全部 TS4 component，admission 为
   `final_span / table_object / pending / rejected`，一个 component 只能出现一次。
4. `TableCitableCoverage`：按 table→row→cell→source ref 记录 aligned/non-citable 区间及原因；
   不得以整表 bool 取代。
5. `TableStructureGap`：至少包含
   `unsupported_table_structure / unresolved_geometry / upstream_table_scope_miss /`
   `visual_object_not_table / ambiguous_continuation / provenance_incomplete / root_identity_mismatch`。
6. `FinalMaterialConservation`：记录 TS4 disposition/component、final span/table/pending/rejected
   的逐项分区、字符/行/组件三层守恒。
7. `FinalMaterialStructureSnapshot`：绑定 verified TS4 identity、tables、relations、decisions、
   bindings、final spans、coverage、gaps、conservation、final synopses 与所有版本/依赖指纹。

所有对象 ID 必须绑定规范化业务内容与上游身份，不含 run_id、timestamp、临时路径、调用 ID 或隐藏推理。
同名的 legacy `harness.table_structure.table_object_id()` 是 R2 裸 digest，**不得**复用或重解释为
TS5 `table_id`。

### 19.4.4 身份有向无环图（DAG，禁止回指）

身份生成顺序固定为：

```text
verified TS4/raw roots + TS5 versions/profile hashes
  → upstream_dependency_fingerprint
  → TableObjectV4 + final spans
  → TableRangeDecision
  → FinalComponentBinding + TableCitableCoverage
  → TableRelation（端点只能引用已完成对象）
  → FinalMaterialConservation + final synopses + gaps
  → FinalMaterialStructureSnapshot（唯一终端聚合节点）
  → VerifiedFinalMaterialStructureSnapshot capability
```

- `TableObjectV4.table_id/revision_id` 只绑定上游依赖、单页物理内容与 provenance；不绑定 relation 或
  final snapshot。
- `table_locator` 只绑定 raw PDF/document version、page、quantized physical bbox、page-local source order 与
  geometry version；classification/profile 变化不得改变物理 locator。`table_revision_id/table_id` 再绑定
  locator、规范化 cell/content/provenance、`structure_class` 与 upstream dependency；profile bump 因此只使
  revision/member/final snapshot 失效，不伪造“物理位置改变”。
- `TableRelation.relation_id` 在端点对象完成后派生，绑定双方稳定 locator/ID、关系类型和关系证明；
  relation 不得回写或改变任一端点 ID。
- `TableRangeDecision`、`FinalComponentBinding` 与 `TableCitableCoverage` 只单向引用已完成的 table/final
  span/cell；`TableObjectV4` 与 final span 不得绑定这些后生成 overlay 的 ID/fingerprint。
- `FinalMaterialStructureSnapshot.content_fingerprint` 最后由
  `upstream_dependency_fingerprint + canonical(sorted member IDs/fingerprints) + final versions`
  派生；snapshot identity 不得反向进入任何 member identity 或上游依赖束。
- runtime verification fingerprint 可绑定 final snapshot fingerprint 与 issuer version，但同样不得回写
  snapshot 或 member。任何 Table→Snapshot→Table、Table→Relation→Table、dependency→snapshot→dependency
  的循环都必须由 schema/identity tests 拒绝。

## 19.5 表格候选、几何恢复与误判防线

### 19.5.1 候选源是并集，正式资格是统一硬门

候选只决定“检查哪里”，不决定“这里已经是表”。按以下优先级生成并去重：

1. TS4 冻结的 `table_inside` / `table_adjacency` ranges；
2. range 内或紧邻范围内经真实 line/span 定位的显式表题、单位、续表标记；
3. 对**无显式表号**的通用表题/结构文本，调用既有
   `harness.table_structure.detect_table_start_flags` 作为文本候选；
4. 从同一 raw PDF bytes 得到的 pdfplumber ruled/text geometry candidates；
5. `PageLayout` 的稳定 x-column / y-row / bbox 聚类，只作为 borderless geometry 的第二佐证。

`detect_table_start_flags` 不是所有候选的必要条件；它也不得被复制到
`document_structure/` 形成第二套规则。显式表号、续表和 generic start 是不同候选通道。

正式 `TableObject` 必须通过统一门：

- 候选 bbox 能唯一映射到当前 PageLayout 的同页 geometry；
- 至少 2 列，且存在真实 cell/row 结构；`key_value_form` 可无独立 header，但必须有稳定左键/右值
  结构和至少两行真实内容；
- cell 覆盖无重叠、无越界、无未解释空洞；merged cells 有明确 rowspan/colspan；
- 表体至少一行；仅标题、仅表头、纯装饰线或组织结构图不得成表；
- 每个 cell 内容可从 PageLayout source fragments 确定性重建；
- 每个 source fragment 可追到 TS4 component/Evidence/alignment，无法追到的部分进入 gap；
- 表格边界可闭合，或对象明确为 `partial` 且列出边界缺口；
- 通过普通段落/列表/数字散文的负门。

没有真实网格/表单结构时，不运行“文本状态机补成一张完整表”。旧 R2 flattened recovery 只作为回归
对照，不是 TS5 权威对象来源。

### 19.5.2 几何坐标与同源文本

`table_geometry.py` 只能用 `VerifiedPageLayout.source_bytes` 打开 pdfplumber。坐标变换必须绑定：

- PDF page width/height、CropBox/MediaBox、rotation；
- pdfplumber top-left 坐标与 PageLayout bbox 的明确变换公式；
- quantization/tolerance；
- ruled/text strategy 固定 settings；
- pdfplumber/PyMuPDF 版本与 settings fingerprint。

页尺寸或旋转无法唯一对齐时，整页候选为 `unresolved_geometry`，不得通过扩大容差强行匹配。
pdfplumber 的 extracted text 仅作诊断；正式 cell text 必须从同一 PageLayout 的真实 source fragments
重建，从而与 TS2/TS3/TS4 的来源链一致。

### 19.5.3 重叠候选与 upstream scope miss

同页重叠候选按以下确定性顺序裁决：完整闭合网格 > component/provenance 覆盖更全 >
结构缺口更少 > bbox 更小；仍并列则全部 `unresolved_geometry`，不得选择“第一张”。

默认只在冻结 table/provisional 范围内建立正式对象。几何若覆盖：

- inherited/formal/body-unassigned components：在 cell/source-fragment 逐项证明后可以吸收；
  原 landing、unassigned reason 与 component identity 原样保留；
- 已冻结的 regular `body_span`：**不得静默改成表格**，输出
  `upstream_table_scope_miss`，保留原 TS4 span，并阻断该文档 final capability 与 TS5 关闭；必须先由
  上游 successor 明确修复/重建后才可继续，不能作为可保留 P2。

这一区分专门保证真实 p20 表内小标题与正文可进入 cell，而不会把任意 TS4 正文重新解释。

## 19.6 行、列、表头、合计与 cell 内部段落

### 19.6.1 结构类型与表头真值表

| `structure_kind` | 最小证明 | header 规则 | completion 含义 |
|---|---|---|---|
| `headered_grid` | ≥2 列、header + body、闭合 grid | 至少一层物理 header；多层 header 保留 merged geometry | 可 complete；title/unit/total 可合法缺省但缺省原因必须显式 |
| `key_value_form` | 稳定 key/value 列、≥2 行、真实 cell 边界 | 允许无独立 header；不得伪造 header | 可 complete；key cells 是字段名，不冒充 outline 标题 |
| `headerless_grid` | 真实 row/column grid + body，但无法证明 header | header 为空且 reason 固定 | 只能 partial；可检索/展示，不得单独授权集合完备或列语义 |

header 判定不能仅靠“第一行不是数字”；需综合 rowspan/colspan、重复跨页 header、字体、位置、列标签
一致性与后续行模式。第一条数据行不得被吞成 header。subtotal/total 分开保留，无法确定时作为 body row
并记录 uncertainty，不能用关键词强改。

### 19.6.2 cell 文本与内部小结构

每个 cell 的 blocks 按物理阅读顺序构建。角色只表示 cell 内部呈现结构，不进入标题树：

- `heading`：结构上独立、后接同 cell 内容且有真实字体/编号/换行证据；
- `paragraph`：完整段落；
- `list_item`：同 cell 列表项；
- `line`：只能证明行边界；
- `unclassified`：内容保留但不猜角色。

角色分类器必须公司无关并版本化；缺证据时用 `unclassified`，不为提高命中率硬判。
cell canonical text 必须能由 block text 以冻结分隔规则重构；block text 又必须逐字符来自 source refs。
两条已登记小标题及其后续段落只是验收样本，生产代码不得出现它们的文字、页码、表号或 Evidence ID。

### 19.6.3 普通非财务表与财务表统一结构、分离权威

任职情况、募集资金募集与使用、主要子公司、主营业务构成等普通/业务表均走同一 geometry、cell、
provenance 与 relation 机制，不按主题另写提取器。财务主表和附注表也使用同一结构对象，但：

- `financial_main_statement`：TableObject 可导航、预览和回查，**不授权任何金额**；
- `note_table`：可承载 Evidence-backed 附注事实的来源结构，事实资格仍由后续 authority 校验；
- `ordinary_business_table`：可承载普通业务事实来源；
- `unclassified`：结构保留，但不得授权事实或 `set_complete`。

分类只依据可信 outline path、结构位置和版本化通用规则。`node_id=None` 只允许出现在已对象级验证的
`TableOwnerRef(owner_kind="unassigned_boundary")`，此时分类为 `unclassified`；两类 owner 都缺失则
TableObject 构造失败，不得降级成 orphan `unclassified`。其他分类证据不足同样用 `unclassified`，不得把
“非财务主表/附注”一律默认成普通业务表。

### 19.6.4 `structure_class` 与 cell block role 的版本化封闭规则

`structure_class` 不允许散落在 builder 内用临时字符串/正则判断。TS5 新增只读资产
`document_structure/policies/table_classification_profile_v1.json`，由
`TABLE_CLASSIFICATION_PROFILE_VERSION="tcp-1"` 与 file SHA256 共同绑定；另以
`table_cell_block_profile_v1.json` + `TABLE_CELL_BLOCK_PROFILE_VERSION="tcbp-1"` 绑定 cell 内角色规则。
两份资产登记在新增的 `table_profile_registry_v1.json`，不得改写 TS4-B 已冻结的 span policy registry。
它们在 TS5 代码审查时作为确定性实现资产一并批准，不另设可由执行者调参的人工阈值轮；registry 必须
绑定 file SHA256/profile content fingerprint，未登记、未知版本或字节漂移均 fail-closed。

table 分类器的**全部允许输入**只有：verified outline node/path（真实标题，不含 synopsis）、
source boundary、table 相对 node 的结构位置、profile 内容及 normalization version。真值表按下列优先级
执行，首个成立者生效：

| 顺序 | 条件 | `structure_class` |
|---|---|---|
| 1 | owner node/path/boundary 缺失，或不同规则同时命中且无法按 profile 唯一消歧 | `unclassified` |
| 2 | verified ancestor path 命中 profile 中的财务报表附注/报表项目注释类节点 | `note_table` |
| 3 | 不在附注子树内，且最近有效标题命中 profile 的资产负债表、利润表、现金流量表或所有者权益变动表类型 | `financial_main_statement` |
| 4 | 位于 verified 正文节点/边界内，且 1–3 均不成立 | `ordinary_business_table` |
| 5 | 其余情况 | `unclassified` |

profile 只允许公司无关的标题归一模式、祖先/最近节点关系、优先级与冲突规则；禁止公司名、证券代码、
页码、表号、Evidence ID、gold 字段。修改任一模式、优先级或 normalization version 必须升 profile
version/file hash 并改变 upstream dependency fingerprint。`structure_class` 仍只是结构标签，永不授予
数字 authority。

cell block role profile 同样只接受真实 cell 内的字体/编号/换行/相对 bbox/相邻 block 信号，输出
`heading/paragraph/list_item/line/unclassified`；输入不闭合或多规则冲突一律 `unclassified`。两份
profile 都必须有独立真值表、unknown-field 拒绝、非 300750 fixture 与 profile 漂移测试。

## 19.7 表题、单位、表注、分析文字与 final span 决议

### 19.7.1 一条 provisional range 恰好一个 decision

每个 `table_inside/table_adjacency` disposition 必须且只能得到下列之一：

| decision | target | 资格 |
|---|---|---|
| `absorbed_as_caption` | table | bbox/位序/文本结构共同证明表题 |
| `absorbed_as_unit` | table | 与该表唯一绑定的单位行 |
| `absorbed_as_note` | table | 表下注释/口径说明；内容保持独立来源 block |
| `absorbed_as_table_body` | table | source fragments 落入真实 cell grid |
| `kept_as_paragraph` | final span | 与表格无成员关系且正文边界独立成立 |
| `absorbed_into_body` | final span | 与相邻正文同节点、同段且合并边界可重算 |
| `unsupported_table_structure` | none | 有真实范围但不足以建表 |
| `unresolved_geometry` | none | 多候选或几何/来源不能唯一裁决 |

表前业务引入和表后分析通常**保留为 final span**，再通过 `introduces/explains` 关系连接；不得为了
“表格完整”把整段分析塞入 cell 或 table note。caption/unit/note/body 四类被表格吸收后不得同时成为 span。

### 19.7.2 final paragraph 资格

`kept_as_paragraph/absorbed_into_body` 由 `sbf-1` 生成新身份，不复用 TS4 `sb-7` 身份。
构建器必须重新计算冻结 TS4-B 的 12 个边界因子与 0.85 必要阈值；decision 不构成 citable 资格。
无法证明边界时保留 pending/non-citable 和 gap。final span 的 Evidence components、覆盖与字符范围
从真实 TS4 components 重建，不允许仅复制旧 projection。

## 19.8 typed relations 与跨页续表

### 19.8.1 relation 模型

`TableRelation` 属于 final snapshot overlay，不回写 `DocumentOutline/ReferenceEdge`：

- `TableEndpointRef`：`table_locator + table_id/revision_id + document/layout/outline identity`；
- `FinalSpanEndpointRef`：`span_locator + span_id/revision + node/source-boundary identity`；
- `ComponentEndpointRef`：`component_id + evidence_char_range + terminal_kind/id + role`，其中
  `role ∈ {caption,unit,note}`；
- `ReferenceOccurrenceEndpointRef`：verified occurrence locator/identity、owning source object、
  occurrence range 与 verified reference edge identity。

四者组成 `TableRelationEndpointRef` 的严格 discriminated union；每个分支拒绝其他分支字段，所有对象
必须从同一 verified roots 或 final member set 对象级解析。通用 `source_id/target_id: str`、仅凭字符串存在、
调用方自报 endpoint kind 或跨文档拼接一律拒绝。`TableRelation` 至少包含 schema/builder version、
relation kind、typed source/target、relation proof refs、upstream dependency fingerprint 与 content fingerprint。

relation 的 source→target algebra 固定如下：

| relation kind | source endpoint | target endpoint | 额外硬门 |
|---|---|---|---|
| `introduces` | `FinalSpanEndpointRef` | `TableEndpointRef` | 表前 span 与 table 同 node/source boundary，物理源序相邻且中间无结构切断 |
| `caption_of` | `ComponentEndpointRef(role=caption)` | `TableEndpointRef` | component 子区间、caption decision 与 table title source 三向一致 |
| `unit_of` | `ComponentEndpointRef(role=unit)` | `TableEndpointRef` | unit source 与 table.unit/source refs 三向一致 |
| `explains` | `TableEndpointRef` | `FinalSpanEndpointRef` | 表后 span 独立正文边界成立、源序在 table 后且未跨新标题/表 |
| `footnote_of` | `ComponentEndpointRef(role=note)` 或独立 note `FinalSpanEndpointRef` | `TableEndpointRef` | note marker/geometry/源序唯一；两种 source 分支互斥 |
| `continued_by` | `TableEndpointRef` | `TableEndpointRef` | 满足 §19.8.2 全部 continuation 门，same document/version，端点不是自身 |
| `references` | `ReferenceOccurrenceEndpointRef` | `TableEndpointRef` | occurrence 的**来源位置**已由 verified reference chain 对象级验证，TS5 再用自身 table-target proof 落地目标；不以“最近表/第一张表”猜测 |
| `reconciles_with` | — | — | 仅保留 enum；TS5 构造任何 resolved relation 均拒绝 |

每条 resolved relation 必须绑定真实 source/target 对象、几何/occurrence 证明和依赖指纹。字符串 ID
集合不能证明对象存在。已有 verified `ReferenceEdge.cross_reference/table_continuation` 可以作为
佐证，但不能单独自证 TS5 relation；双方不一致则 gap/fail-closed。

TS3 中“source occurrence 已对象级验证、但因当时尚无 to-4 TableObject 而 target unresolved”的 edge
可以作为 `ReferenceOccurrenceEndpointRef` 来源；TS5 不要求上游预先虚构 resolved table target，而是必须
用当前 verified source occurrence + 当前 TableObject 的 geometry/identity/target proof 独立完成目标落地。
上游连 source occurrence 都未验证时才一律拒绝。

### 19.8.2 continuation 真值表

续表正例必须同时满足：

1. 同 document_id/document_version/evidence_set/layout/outline；
2. 相邻物理页，或有经验证的显式 continuation occurrence；
3. compatible column count、列边界、header signature 与 unit；
4. 前片段结束、后片段起始与页面源序闭合；
5. 中间无新标题、新表题或另一个表对象切断逻辑边界；
6. 双向 locator/relation 一致、链无环、每个 fragment 至多一个前驱和一个后继。

单位变化、列结构变化、跨文档版本、非相邻页、只有重复 header、相同标题但另起一表，均不能单独证明
continuation。模糊时两个单页对象都保留，关系为 `ambiguous_continuation` gap。TS5 不跨文档合成
逻辑大表；跨树聚合属于 TS7A。

## 19.9 provenance、可引用覆盖与三级守恒

### 19.9.1 component 精确等集

最终 `FinalComponentBinding.component_id` 集合必须与 verified TS4 snapshot 的**全部 components
精确等集**；不是只覆盖表格相关 ranges。当前 TS4-B 验收基线为 68,225 components；另有 3,204 个
table-related dispositions（range_kind 为 2,323 `table_inside` + 881 `table_adjacency`）。components 中的
landing 基线则为 37,834 `table_inside` + 2,668 `table_adjacency` = 40,502。三组数字对象不同，均只作
验收对账基线；正式算法必须从 wrapper 实时重算，不得写入生产逻辑。

每个 component 恰好落入：

- 一个 final span；
- 一个 table/cell；
- pending（诚实未决）；
- rejected（保留拒绝原因）。

不得遗漏、重复、同时进入 span 与 table，亦不得因 non-citable 而删除 provenance。

现有 `COMPONENT_LANDINGS` 11 类到 final admission 的映射必须是下表这一个封闭函数；不得在
builder/verifier/runner 各自解释：

| TS4 `landing` | TS5 唯一允许终态 | 约束/理由 |
|---|---|---|
| `body_span` | `final_span / pending` | 正常绑定原 span 或按 sbf-1 重建的对应 final span；若 verified table geometry 与其冲突，原 TS4 span 仍作为上游历史保留，但该 component 的 final binding 改为 pending，追加 `upstream_table_scope_miss` 并阻断该文档 final capability/TS5 关闭，绝不改绑 table |
| `table_inside` | `table_object / final_span / pending` | 严格由同一 `TableRangeDecision` 决定：四类 absorbed→table；kept/absorbed_into_body→final span；unsupported/unresolved→pending |
| `table_adjacency` | `table_object / final_span / pending` | 与上一行同一 decision 真值表；仅 adjacency 不能自动进 table |
| `body_unassigned` | `table_object / pending` | 只有 exact cell/caption/unit/note geometry + provenance 闭合才进同一 table；否则 pending，禁止凭 TS5 补造普通段落 |
| `body_empty` | `rejected / pending` | 从 verified roots 复核 source interval 确为空才 `rejected(empty_source_text)`；若真实区间非空或根对象不一致则 pending + `root_identity_mismatch`，并阻断该文档 final capability；不得吞掉异常内容或造空 cell/span |
| `heading_node` | `rejected / pending` | 正常为 `structural_heading_only`；若 table geometry 覆盖冻结 heading，改为 pending + `upstream_table_scope_miss` 并阻断该文档 final capability，不得静默吞入 cell |
| `formal_unassigned` | `table_object / pending` | exact geometry/provenance 闭合时可进同一 table；否则保持 pending，原 unassigned identity/reason 不变 |
| `non_content` | `rejected / pending` | 重新核验为家具/非内容则 `rejected(non_content_region)`；若 table geometry 与之冲突则 pending + upstream gap，不得直接吸收 |
| `outside_body` | `rejected / pending` | 确认位于正式正文域外则 `rejected(outside_formal_body)`；若 table geometry 与之冲突则 pending + upstream gap |
| `alignment_offset_unverifiable` | `pending` | `provenance_incomplete`；无 Layout hit/精确 offset 时永不进入可引用 cell/span |
| `alignment_residue_unmapped` | `pending` | `provenance_incomplete`；保留 residue class/interval，不伪造 Layout/table 位置 |

这里 `rejected` 是带完整 component identity、区间和 reason 的**终端账本项**，不是删除来源。一个
component 仍只有一条 `FinalComponentBinding`。同一 component 可为**同一个 TableObject** 的多个 cell、
caption、unit 或 note 提供 source refs，但必须切成按源序、互斥、无空洞且可从 layout fragments 回读的
Evidence 子区间；这些子区间的并集必须恰好等于完整 `component.evidence_char_range`。若一个 component
需要同时跨 table/span、跨两个 table object、存在无归宿剩余区间，或子区间无法唯一切分，则整条
component 进入 `pending(provenance_incomplete)`；禁止部分消费后隐藏尾部、复制 binding 或一源多投。

### 19.9.2 cell/range 可引用覆盖

`TableCitableCoverage` 逐 cell source ref 判断：

- 只有原 component alignment verdict 为 `aligned`、真实 Evidence walk 成立、offset/bbox/fragment
  全部闭合的范围才 citable；
- partially_aligned、unaligned、refused、offset_unverifiable 均保留但 non-citable；
- 一个 cell 可同时有 citable 与 non-citable fragments；
- table structure 可以 complete，但 `all_cells_citable=False`；
- 下游 citation 必须落到单一 component/Evidence range，不能只引用整张表或页码。

### 19.9.3 守恒

分别验证、不得跨层相加：

1. **处置守恒**：TS4 table provisional dispositions =
   absorbed-to-table ⊎ final-paragraph ⊎ pending/unsupported；
2. **组件守恒**：全部 TS4 components =
   final-span ⊎ table-object ⊎ pending ⊎ rejected；
3. **Layout 文本守恒**：每个 provisional/table geometry 范围内全部 non-empty LayoutSpan fragments =
   cell source refs ⊎ caption/unit/note refs ⊎ final paragraph refs ⊎ residual gap；
4. **Evidence 区间守恒**：每条 Evidence component interval 原样保留；不得因 merged cell、跨页 header
   去重或逻辑视图去重而删除物理 provenance。

任何差额、重复、越界或“为凑平而自动修正”都是 P1。重复 header 可在逻辑展示层标记
`repeated_header`，但物理 fragment 和来源必须保留。

## 19.10 final synopsis 与 TS6 导航接口

TS5 不把表题、表头或 cell 伪装成 TS4 的 `NavigationSynopsis` / `SynopsisSnippet`。两条 synopsis
契约严格分域：

- TS4 `NavigationSynopsis`：固定 `nss-2/ns-2`，来源是 TS4 `OutlineSpan`，reason 词表固定为
  `no_span / empty_text / length_exceeded / alignment_failed / table_only_pending_ts5`；
- TS5 `FinalNavigationSynopsis`：固定 `nss-3/ns-3`，来源只能是 `FinalOutlineSpan (fos-*)`，reason
  词表固定为 `no_span / empty_text / length_exceeded / alignment_failed /
  table_material_available_no_text_synopsis`；不含 `table_only_pending_ts5`。

`FinalNavigationSynopsis` 至少包含独立的 `schema_type`、locator/revision 双身份、node ID、
schema/algorithm versions、status、封闭 reason、final snippets 与 `source_final_span_ids`；每个 snippet
必须精确绑定单一 `FinalOutlineSpan` 的连续字符区间，且其 locator/id/version/文本可逐字符回查。
TS4 `SynopsisSnippet.span_id` 不得直接搬入 final 对象，旧 synopsis 也不得被 alias/cast 为 final。

final synopsis 规则：

- 有 final 可引用 paragraph span：按 `ns-3` 从该 span 抽取，来源仍是精确 span；
- 节点只有正式 TableObject、没有可引用 paragraph：`synopsis_unavailable`，
  `reason_code=table_material_available_no_text_synopsis`；
- 节点仍只有 unsupported/unresolved table range：使用显式结构 gap reason，不写空白 synopsis；
- TS6 直接索引 TableObject 的 title、physical headers、outline path 与结构状态；这些是导航元数据，
  不是事实或 citation。

TS4 `nss-2/ns-2` synopsis 与 `table_only_pending_ts5` 仍是 TS4 当前冻结契约，不原位改写、不降为
generic legacy leaf，也不随 TS5 全局升版。final snapshot 必须重新生成全部 affected node 的
`FinalNavigationSynopsis`，不能选择性沿用旧对象或旧状态。`SpanBuildSnapshot spn-1` 与
`FinalMaterialStructureSnapshot fms-1` 在 create/from_dict/__post_init__/verifier 四个边界都必须拒绝
对方的 synopsis 类型与版本。

## 19.11 模块、public functions、CLI 与逐文件 changelist

### 19.11.1 新增生产模块

| 文件 | 唯一职责 | 主要 public API |
|---|---|---|
| `document_structure/table_schema.py` | to-4 cell/table、relation、decision、binding、coverage、gap、conservation、`FinalNavigationSynopsis`、final snapshot wire types 与 identity | 严格 `create/from_dict/to_dict`、current/legacy dispatch；final snapshot 只接受 nss-3/ns-3 final synopsis |
| `document_structure/table_geometry.py` | 同源 PDF bytes → page geometry candidates、坐标变换、cell/source-fragment 映射；无业务 authority | `extract_table_geometry(...)`、`verify_geometry_mapping(...)` |
| `document_structure/table_classification.py` | 读取受信 outline path/source boundary/structure position 与已注册 profile，唯一派生 `structure_class` 和 cell block role；不参与 geometry admission/数字 authority | `classify_structure_class(...)`、`classify_cell_block_role(...)` |
| `document_structure/table_builder.py` | candidates 去重、row/cell/fragment、caption/unit/note、continuation 与 relations | 私有纯核 + `self_check`；不暴露 raw-root 入口 |
| `document_structure/final_material_builder.py` | TS4 overlays、sbf-1 final spans、全部 component binding、coverage/conservation/synopsis、final snapshot | `build_final_material_snapshot(VerifiedSpanSnapshot)` |
| `document_structure/final_verifier.py` | 从 verified TS4 roots 独立重建并 canonical 全比较；禁止 import/call public builder 或高层 assembly；签发 runtime capability | `verify_final_material_snapshot(...)`、`VerifiedFinalMaterialStructureSnapshot` |

实现可在不改变职责的前提下合并私有 helper，但不得把 geometry、wire、final verifier 和 evaluation runner
混成一个文件，也不得在 runner 内复制生产算法。

### 19.11.2 修改生产模块

| 文件 | 允许的最小改动 |
|---|---|
| `document_structure/versions.py` | 保持 TS4 `SYNOPSIS_SCHEMA_VERSION=nss-2` / `SYNOPSIS_VERSION=ns-2`；新增独立 `FINAL_SYNOPSIS_SCHEMA_VERSION=nss-3` / `FINAL_SYNOPSIS_VERSION=ns-3`，登记 §19.4 其余 current/legacy/algorithm/capability 版本并扩展 registry self-check |
| `document_structure/__init__.py` | 导出 current to-4 与唯一 build/verify API；保留明确 legacy v3 入口 |
| `document_structure/schema.py` | **不改 to-3 或 TS4 `NavigationSynopsis` 业务语义**；把 legacy table class 对 schema/builder 的校验钉到固定 `to-3/tb-1`；TS4 `NavigationSynopsis` 继续只接受 nss-2/ns-2 与原五值 reason；删除/拒绝把 nss-2 作为“复用当前六值词表”的隐藏历史对象的实现 |
| `document_structure/span_schema.py` | 增加 final capability 登记所需的封闭接口；同时明确修复授权：把 `SpanBuildSnapshot spn-1` 与 `SynopsisSourceValidation nsv-1` 的 create/from_dict/__post_init__/verify 精确钉到 TS4 nss-2/ns-2，完整读回封存对象；拒绝 nss-3/ns-3 和 `FinalNavigationSynopsis`；这叫保持 TS4 语义，不是改写 TS4 语义 |
| `document_structure/synopsis.py`（若现有实现落点不同，以实码为准） | 保持 TS4 build/verify 路径固定生成 nss-2/ns-2；新增 ns-3 final-span-only builder/validator，返回 `FinalNavigationSynopsis` 并使用独立 final reason 词表；禁止共享会被全局版本改变的默认 factory |
| `requirements-probe.txt` | 将“pdfplumber 仅 dev probe、不进 runtime”的旧说明标为已被 TS5 取代；正式依赖继续使用 `requirements.txt` 已固定的 `pdfplumber==0.11.4`，不得无审查升版 |

新增只读 profile 资产：

- `document_structure/policies/table_classification_profile_v1.json`；
- `document_structure/policies/table_cell_block_profile_v1.json`；
- `document_structure/policies/table_profile_registry_v1.json`（只注册本批两份新资产及 file hash；不得修改
  TS4-B 冻结的 `registry_v1.json`）。

不得修改 `harness/**`、Retriever、ToolRegistry、Pack Store、sections、Contract/SourcePolicy/
WritingSpec/PresentationProfile、Evidence/Financial 数据库及其 migration。

### 19.11.3 evaluation、fixture 与测试文件

新增：

- `evaluation/tree_table_acceptance_schema.py`（§19.4.1 的 evaluation-only 版本、严格 schema、check IDs）；
- `evaluation/run_tree_table_acceptance.py`；
- `evals/fixtures/tree_structure/ts5_trust_roots.json`；
- `evals/fixtures/tree_structure/non_300750_ts5/manifest.json` 及 manifest 固定成员；
- `evals/test_tree_table_schema.py`；
- `evals/test_tree_table_geometry.py`；
- `evals/test_tree_table_builder.py`；
- `evals/test_tree_table_cells.py`；
- `evals/test_tree_table_relations.py`；
- `evals/test_tree_table_continuation.py`；
- `evals/test_tree_table_authority.py`；
- `evals/test_tree_final_material.py`；
- `evals/test_tree_table_conservation.py`；
- `evals/test_tree_table_formal_chain.py`；
- `evals/test_tree_table_artifacts.py`。

修改 `evals/run_evals.py` 只做显式注册。若现有测试命名/模块职责已覆盖，可少建文件，但测试类别与
反例一项不能少；不得把所有断言塞入一个巨型测试掩盖职责。

为完成 §19.0.1 方案 C，额外允许**精确加强/恢复**以下既有 TS4 回归文件：

- `evals/test_tree_span_policy_b.py`：B6 完整快照读回、canonical/ID/fingerprint 与 completion=False
  断言不得删除、改弱或改成叶子 reader；只可新增 TS4/final 混装拒绝反例；
- `evals/test_tree_span_schema.py`、`evals/test_tree_synopsis.py`：恢复并锁定 nss-2/ns-2 与五值 TS4
  reason 词表，新增 final 类型隔离反例；
- 如确有直接依赖，允许最小修改 `evals/test_tree_span_verifier.py`、
  `evals/test_tree_span_artifacts.py`、`evals/test_tree_structure_schema.py`、
  `evals/test_tree_structure_adversarial.py`、`evals/test_tree_structure_focused.py` 与
  `evals/test_tree_page_layout.py`；这些修改只能恢复冻结 TS4 身份或适配合法 TS5 successor，不能改变
  TS4 业务真值表、放宽 fail-closed 或降低原断言。

### 19.11.4 CLI 与 self-check

至少提供：

```text
python -m document_structure.table_schema
python -m document_structure.table_builder --self-check
python -m document_structure.final_verifier --self-check
python -m evaluation.run_tree_table_acceptance --run-id <new-id>
python -m evaluation.run_tree_table_acceptance --validate-only <run-dir>
python -m evaluation.run_tree_table_acceptance --seal-review <same-run-dir>
```

正式 acceptance runner 从仓库内 `ts5_trust_roots.json` 取得已封存 TS4-B run/root/policy/PDF/
Evidence identities，不提供任意 `--pdf`、`--ev-db`、`--layout` 或 raw snapshot 参数。
其中每份 `ts4_snapshot_id/content_fingerprint` 必须逐字等于已封存 TS4-B
`span_snapshot_index.json` 的相应成员；不得接受在全局 nss-3/ns-3 下重建所得的新 TS4 ID，也不得让
TS5 fixture 自报值反向覆盖封存 index。
run 目录必须 create-only，已存在即拒绝；`--validate-only` 只读且不得补文件；`--seal-review`
只在重新执行 trust-root、机器产物、final verifier 与 review schema 全部核验后，追加一次不可覆盖的
`review_attestation.json`。

## 19.12 测试矩阵（反例先行，但一次性完成同一编码批）

### 19.12.1 schema、身份与信任接口

1. to-4/cell/relation/decision/binding/coverage/gap/final snapshot 序列化往返；
2. 未知字段、缺字段、错误 enum、错误排序、重复成员、非法 hash 全部拒绝；
3. to-3/tb-1 history 可显式读，但 current/final/TS6 消费一律拒绝并要求重建；
4. raw `SpanBuildSnapshot`、JSON、伪 wrapper、testing wrapper 进入正式链均拒绝；
5. copy/deepcopy/pickle/serialize final capability 均拒绝；
6. 篡改上游 PDF/layout/outline/Evidence/alignment/policy/version/settings 任一身份，重算下游 ID 仍拒绝；
7. 同输入双构建的对象、顺序、JSON、指纹逐字节相同；
8. 旧 `harness.table_structure.table_object_id` 不得冒充 to-4 identity；
9. 身份 DAG 自动检查无环：TableObject 无 final snapshot/relation/decision/binding/coverage ID 字段；只改
   relation 不改变 table ID，改 table 则按 Table→Relation→FinalSnapshot 单向传播；final snapshot
   fingerprint 不得进入 upstream bundle；
10. monkeypatch public builder 返回“篡改后同步重算全部 ID/hash、内部自洽”的 snapshot，verifier 仍从 roots
    重建并拒绝；AST/import guard 证明 verifier 不 import/call public builder 或高层 assembly；
11. `tcp-1/tcbp-1` current/legacy/unknown 与 file-hash 漂移真值表；四类 structure class、五类 cell block
    role、冲突/已验证 unassigned owner→unclassified；公司名/页码/表号扰动不得改变分类，任意 class
    均不产生 authority；
12. `TableOwnerRef` 两分支恰一成立；node/unassigned owner 的 document/layout/outline/source-boundary
    对象级回查通过，二者都缺、二者同时存在、跨根、bbox 不在 boundary、`node_id=None` 却无 verified
    unassigned ref 均拒绝。
13. 方案 C 版本隔离反例必须全部成立：
    - 四份封存 TS4-B `SpanBuildSnapshot spn-1` 在当前环境经原完整入口读回、canonical round-trip、
      snapshot ID/content/input fingerprint 与独立 verifier 全部逐字不变；
    - TS4 重新构建仍逐份产生封存 index 中的相同 snapshot ID，不得被 final 版本常量改变；
    - `spn-1`、`nsv-1` 只接受 nss-2/ns-2，拒绝 nss-3/ns-3 与 `FinalNavigationSynopsis`；
    - `fms-1` 只接受 nss-3/ns-3 `FinalNavigationSynopsis`，拒绝 TS4 `NavigationSynopsis`；
    - TS4 五值 reason 集与 final 五值 reason 集分别精确相等，互相专属值混入即拒绝；
    - `FinalNavigationSynopsis` 的每个 snippet 只能解析到同一 final snapshot 的 `fos-*`，传入 TS4
      span ID、TableObject/cell ID 或不存在成员均拒绝；
    - registry 明确登记两个 public wire type 与两条版本轴，不允许 alias、重复类型名或同常量双重解释；
    - 不存在 `spn-2`，不得靠升级 TS4 容器通过测试。

### 19.12.2 候选与普通段落负门

1. 显式表号表、无表号 generic 表、续表分别能成为候选；
2. `detect_table_start_flags=False` 但完整几何网格成立的显式表号表仍可验证；
3. 只有表题无网格、普通数字段落、编号列表、双栏排版、表字标题、组织结构图均不得成表；
4. 只有 `adjacent_to_table` 不得自动成表；
5. 无线框表只有在 row/column/content 多重证据闭合时可恢复，否则 unsupported；
6. 两个重叠候选无法唯一裁决时 unresolved，不选“第一个”；
7. geometry 命中 frozen regular span 时输出 `upstream_table_scope_miss`，不静默重分类。

### 19.12.2A 全部 component landing 封闭映射

对 §19.9.1 的 11 类逐类构造正例与反例，并断言：

1. `FinalComponentBinding.component_id` 与 verified TS4 components 精确等集、每个只出现一次；
2. table dispositions 的 decision→admission/target 真值表逐项成立；
3. body/formal unassigned 只有 exact geometry/provenance 闭合才进入 table；
4. heading/non-content/outside-body 与 table 冲突时进入 pending + upstream gap，不被静默吸收；
5. unverifiable/residue 永远 pending/non-citable；empty 只作为带理由 rejected ledger；
6. 同 component 跨同一表多个 cell 时，Evidence 子区间有序、互斥、无洞且并集相等；跨 terminal 或跨
   table object 时 fail-closed 为 pending，不复制 binding；
7. alignment/refusal 两类 terminal 均可序列化、回读并复核；refusal 不要求也不允许伪造 alignment ID；
   offset-unverifiable/residue 只以 pending binding 保留 terminal/Evidence 来源，不得进入 cell source ref；
8. 删除一个 component、重复一个 component、改变子区间端点或把 rejected 来源从账本删除，三级守恒均抓出。

### 19.12.3 cell 与网格

1. 简单 grid、key-value form、headerless partial；
2. 空 cell、换行/折行 cell、多段 cell、列表、cell 内小标题；
3. rowspan、colspan、二者组合、多层物理 header；
4. 网格空洞、重叠、bbox 越界、source fragment 缺失/重复/跨 cell；
5. cell text 与 source fragments 不一致、source order 错、char range 越界；
6. 第一条数据行不误作 header；subtotal 与 total 分开；
7. 改企业名、比例、单位、表头、合计或任一 fragment，身份必须变化或被拒；
8. 两个既定表内小标题的 fixture 断言全文、blocks、locator 和 component 回查完整，但生产代码零硬编码。

### 19.12.4 caption/note/final span 与 relations

1. caption/unit/note/body/paragraph/merge/unsupported/unresolved 八类 decision 真值表；
2. `introduces/caption_of/unit_of/explains/footnote_of/continued_by/references` 的 endpoint algebra
   逐格正例与反例；每类 locator/revision/content/verification identity 可对象级解析；
3. 被表格吸收的范围不得同时成为 final span；
4. kept/merged paragraph 重新计算 12 因子与 0.85 门，不能因 decision 自动 citable；
5. relations 的 source/target kind 错配、对象缺失、跨文档、身份不一致、component range 越界、
   occurrence 未验证、字符串自证均拒绝；
6. `reconciles_with` 在 TS5 不能产 resolved 关系；
7. final synopsis 只引用 final span；table-only node 使用新 unavailable reason。

### 19.12.5 continuation

1. 相邻页、列边界/header/unit/文档身份闭合的真实续表正例；
2. 不同单位、不同列结构、新标题/新表切断、跨 document version、非相邻页、仅 header 重复负例；
3. 两张相邻同列数表、相同标题但另起新表不得合并；
4. chain 双向一致、无环、in/out degree ≤1；
5. repeated header 逻辑去重不删除物理 provenance；
6. ambiguous continuation 保留两个 fragment 与 gap。

### 19.12.6 provenance、可引用与守恒

1. 每个 cell source ref 逐跳回查 LayoutSpan→TS4 component→Evidence range→alignment；
2. aligned/non-citable 混合表不能整表授权；
3. 删除/增加/重复一个 component、decision、binding、source fragment 或 gap，final verifier 拒绝；
4. 全部 components 精确等集和互斥落点；
5. inherited/formal/body-unassigned component 可在真实 cell proof 下进入 table，并保留原 landing；
6. regular body span 不被偷换；
7. disposition/component/Layout fragment/Evidence interval 四类守恒各自为零差额；
8. unsupported/unresolved 的全文/provenance/gap 不丢失；
9. financial main statement TableObject 的 authority 恒 False；
10. TableObject 或 synopsis 不得单独证明 `set_complete`。

### 19.12.7 正式链、泛化与禁止项

1. 唯一链为 `VerifiedSpanSnapshot → final builder → final verifier → verified final wrapper`；
2. 不调用第二 Router/Harness/Retriever/ToolRegistry，不调用 LLM/网络/Bocha；
3. non-300750 fixture 走同一公共入口；
4. 生产文件扫描无公司名、代码、固定页码、表号、Evidence ID、gold 关键词；
5. Evidence/Financial DB 在所有 focused/acceptance run 前后 identity 不变；
6. evaluation runner 缺 root、hash 漂移、目录已存在、产物缺失/多余、tamper、重复 seal 均 fail-closed；
7. `tar-1/ttr-1/trm-1/tai-1/trv-1/tra-1` 的 unknown/缺字段/多字段/版本漂移全部拒绝；固定 10 个
   review check IDs 少一项、多一项、改顺序、任一角色 not_reviewed/fail 却自报 approve、人工 Markdown
   与 review JSON 不一致、seal 后改字节，均不得签发/继续有效。

## 19.13 真实纵向样本与人工检查

页码、表号、特定文字只允许写进 evaluation manifest、review 文案与测试断言，绝不能进入生产分支。
至少对以下能力逐项读回：

| 能力 | 验收样本 | 必须人工确认 |
|---|---|---|
| 普通非财务表 | `NDSD_KCZ_2026` p23 表4-1 | 表题、单位、多层 header、merged cell、body、total、表后备注 |
| 主营业务与续表 | `NDSD_KCZ_2026` p50–52 | 表5-10～5-13 四表不误合并；表5-11 跨页 continuation 正确 |
| 主要子公司 | `NDSD_KCZ_2026` p40–41 | 跨页、长文本/merged cell、前导说明 relation，无治理污染 |
| 表内小标题/key-value form | `NDSD_KCZ_2026` p20 | 两个小标题及其后续全文、内部 blocks、精确位置、components 均完整 |
| 视觉对象负例 | `NDSD_KCZ_2026` p43 组织结构图 | 有“表”字但无可靠网格，不建 TableObject，显式 visual-object gap |
| 任职情况 | `NDSD_2024_year` p50 | 两类任职表按普通非财务表处理，不误归财务权威 |
| 募集资金 | `NDSD_2025_year` p88–91 | 募投项目表、续页、监管文字和表后说明分离并关联 |
| 财务主表 | `NDSD_2025_year` p111 起 | 结构可回查；金额 authority 永远不由 TableObject 授予 |
| 财务附注 | `NDSD_2025_year` p164“货币资金” | 文字说明与表格分读，typed relation 可回查，note-table 权威不越界 |
| 泛化正例 | `non_300750_ts5` versioned fixture | 同一 schema/builder/verifier/守恒，无公司和页码分支 |

三份真实 PDF 必须逐份运行和报告，不得用同一文档逻辑复用成三份。若个别样本在当前 raw PDF 中确实
无法形成 complete table，应诚实给出 partial/unsupported 与原因，但同一能力须有至少一个正向结构样本；
不得用负面终态伪装正向能力通过。

人工 review 至少回答：

1. 是否存在普通正文/视觉对象伪表；
2. 表题、单位、header/body/subtotal/total 是否错位、丢失或串到相邻表；
3. merged cells 与多行 cell 是否读得懂且能回查；
4. 表前说明、表注、表后分析是否分开保存并通过 relation 组合；
5. continuation 是否只连接同一逻辑表；
6. 表内小标题及后续段落是否完整；
7. 任职、募集资金、子公司、主营等非财务表是否走通用机制；
8. 财务主表是否仍无金额 authority；
9. 每个 unsupported/unresolved gap 是否诚实可见；
10. final span/table/component 守恒是否为零差额。

## 19.14 验收 runner、产物与 review seal

### 19.14.1 create-only 机器产物

每个新 run 至少产出：

- `run_manifest.json`；
- `machine_artifact_index.json` 与 `machine_artifact_index_check.json`；
- `database_immutability.json`；
- `table_candidate_audit.jsonl`（accepted/rejected/unresolved 全量候选）；
- `table_objects.json`、`table_objects.md` / `table_preview.md`；
- `table_cell_fragments.jsonl`；
- `table_range_decisions.json`；
- `final_component_bindings.jsonl`；
- `table_relations.json`；
- `table_terminal_ledger.json`；
- `table_citable_coverage.json`；
- `final_spans.json`；
- `final_navigation_synopsis.json`；
- `table_gaps.json` 与 `visual_object_gaps.json`；
- `final_conservation.json`；
- `authority_separation.json`；
- `final_material_structure_snapshots/<document_key>.json`；
- `final_snapshot_index.json`；
- `table_acceptance_matrix.json` / `.md`；
- `before_after.md`；
- `manual_review.md` 空模板；
- `review_decision.json` 空模板。

machine index 不包含待人工填写文件。每项记录 file SHA256、schema/algorithm/settings versions、上游
trust root、TS4 snapshot/policy、raw PDF、Evidence DB identity 和 counts；缺文件、多文件、排序漂移或
内容 tamper 都必须被 validate-only 发现。

`run_manifest.json` 必须携带 `tar-1/ttr-1/trm-1/tai-1/trv-1/tra-1` 全部版本、trust-root file/content
hash、upstream/final fingerprints 与预期 machine artifact membership；machine index/check 必须分别按
`tai-1` 重算成员集合和逐文件 hash，不能互相复制自报。任一 evaluation schema/version/check-ID 漂移均
使 validate-only/seal fail-closed。

### 19.14.2 人工裁决与封存

TS5 没有需要事后挑选的数值阈值，不重复 TS4-A/B 两阶段。流程只有：

1. 一次完整编码批；
2. 一次正式机器 run；
3. 用户 + Codex 阅读真实预览，填写封闭 review check IDs 与
   `decision ∈ {approve,reject,needs_changes}`；
4. `--seal-review` 重新执行 trust root、final verifier、artifact index、DB immutability 与 review
   schema 后，create-only 追加 `review_attestation.json`。

`trv-1` 的 required check IDs 恰为以下 10 项，固定顺序、不得漏项或自由新增同义项：

1. `no_false_positive_table`；
2. `table_parts_and_boundaries_correct`；
3. `merged_multiline_cells_traceable`；
4. `surrounding_text_relations_correct`；
5. `continuation_identity_correct`；
6. `internal_cell_headings_retained`；
7. `nonfinancial_tables_use_generic_path`；
8. `financial_authority_separated`；
9. `explicit_gaps_are_honest`；
10. `final_conservation_zero`。

`review_decision.json` 严格包含 schema version、run/final snapshot identity、上述 check IDs、
`reviewer_roles == [user,codex]`、每角色每检查的 `pass/fail/not_reviewed + concise_observation +
artifact_refs`，以及 `decision ∈ {approve,reject,needs_changes}`。`approve` 必须由“两个角色对 10 项全部
pass、无 not_reviewed/fail、机器门全过”确定性派生；文件自报的 decision 与派生值不一致即拒绝。
`manual_review.md` 只是该 JSON 的人读投影，不能反向授权。实施方只能生成全部 `not_reviewed` 模板，
不得代填用户/Codex 裁决。

`tra-1` attestation identity 绑定：runner/trust-root/run-manifest/artifact-index/review-decision 的 schema
version 与 file/content hashes、全部 final snapshot fingerprints、final verifier verification fingerprints、
DB immutability 结论和 issuer version。timestamp 可作审计字段但不进入内容身份。任一已绑定字节变化、
重复 seal 或同 run 已存在 attestation 都必须拒绝。

实施方不得自行勾选 review、不得宣布关闭。reject/needs_changes 可封存为审计事实，但只有
`approve + all required checks pass` 才具备 TS5 关闭资格。seal 后任何机器/review 字节改变均使
attestation 失效，不能覆盖后重签同一 run。

## 19.15 兼容、migration 与依赖指纹

### 19.15.1 wire 兼容

- 旧 `schema.TableObject` 与旧 cell/row 必须使用类内固定 legacy 常量验证 `to-3/tb-1`，不能继续
  读取全局 current 常量；否则 current 升到 to-4 会把历史类自身变成错误解释器；
- `to-3/tb-1`：可由 history reader 审计读取；不得成为 current、不得适配成 to-4、不得进入 final
  snapshot/capability；
- `to-4/tb-2`：只从 verified TS4 roots 新建；
- legacy/unknown 错误必须区分：“已识别旧版、须重建”与“未知版本、拒绝”；
- TS4 `NavigationSynopsis` 与 `SpanBuildSnapshot spn-1` **保持当前冻结契约** `nss-2/ns-2`，不是
  TS5 可重新解释的 generic history payload；
- TS5 `FinalNavigationSynopsis` 使用独立 `FINAL_SYNOPSIS_SCHEMA_VERSION=nss-3` /
  `FINAL_SYNOPSIS_VERSION=ns-3`，只进入 `fms-1`；两类 synopsis 不做 migration、alias 或隐式适配；
- 不改历史 JSON、TS4 artifact、Evidence ID/Evidence Set 或 accepted run。

### 19.15.2 migration

TS5 **无 SQLite migration、无 Store 写入**。Pack v5、Store migration 4、tree locator/payload、
Citation successor、resolver 与 checkpoint stale/current 属 TS7B。本批不得提前创建“临时 Store”或把
验收 JSON 当生产数据库。

### 19.15.3 上游依赖指纹与 final 终端指纹

`upstream_dependency_fingerprint` 只绑定**先于 TS5 成员存在**的根身份与版本束，至少包含：

- raw PDF identity；
- PageLayout/Outline/TS4 snapshot/TS4 policy identities；
- Evidence set/member/alignment identities；
- to-4/cell/geometry/table builder/relation/decision/binding/coverage/gap/final material versions；
- pdfplumber/PyMuPDF 实际版本与固定 settings fingerprint；
- TS4 冻结 nss-2/ns-2、sbf-1 与 TS5 final nss-3/ns-3（使用独立常量名）；
- table classification/cell-block profile version、file hash 与 profile registry identity。

它**不得**包含 TableObject、TableRelation、FinalMaterialStructureSnapshot 或 final snapshot content
fingerprint。各 TableObject/final span/decision/binding 先由此上游指纹与自身规范化内容派生；relations
再由已完成端点派生；最后
`final_snapshot_content_fingerprint = H(upstream_dependency_fingerprint, canonical final members,
final schema/builder versions)`。runtime wrapper verification fingerprint 可单向绑定该终端 fingerprint 与
issuer version。上述三个指纹层级不得互相回填，schema self-check 必须做 DAG/无环断言。

run_id、timestamp、输出路径、call_id 与日志不进入内容身份。

## 19.16 编码批次、测试顺序、提交与工时

### 19.16.1 一次性编码，不按问题拆成多轮

Claude Code 获批后按一个完整 TS5 编码批实现 §19.3–§19.15，不得把
to-4、geometry、cell provenance、relations、final overlay、verifier、测试和 runner 拆成逐项申请。
执行顺序：

1. 记录初始 `git status`，保护现有 tracked deletion、untracked results/probes；
2. 先落 counter-example tests 与 public schema/version registry；
3. 实现 geometry/cell/table；
4. 实现 overlays/final spans/relations/coverage/conservation/synopsis；
5. 实现独立 final verifier；
6. 运行全部 TS5 focused tests；
7. 运行 TS2/TS3/TS4 关键回归；
8. 只运行**一次**完整 `python -m evals.run_evals`；
9. 生成一次全新正式 TS5 acceptance run；
10. 实施方输出停止报告，**不 stage、不 commit、不宣布关闭**。

如果 focused tests 发现普通实现缺陷，应在本批内补反例并修完，不因每个小问题重新申请方向；只有新的
P0/P1 架构冲突、冻结资产冲突或必须新增业务裁决时才停止。

### 19.16.2 独立验收后 commit

用户 + Codex approve 后允许按职责提交：

1. `feat(document-structure): add TS5 table and final material capability`
   —— wire/version/geometry/builder/final verifier 与其直接单测；
2. `test(document-structure): add TS5 formal-chain and real acceptance gates`
   —— trust lock、non-300750 fixture、runner、artifact/formal-chain tests、eval registration；
3. `docs(status): close TS5 and hand off to TS6 planning`
   —— 只在实际关闭后更新状态。

每个 commit 前精确暂存并核对允许清单，禁止 `git add .` / `-A`。生成结果、数据库、日志、probe、
参考材料、publication 环境差异不得提交。若验收不批准，不得提交“半成品”。

### 19.16.3 执行节奏（不设工时承诺）

TS5 不写预计人日或墙钟承诺。完成与否只看 §19.17 的退出门；模型速度、额度或会话时长不得成为删减
cell provenance、守恒、负例、真实样本或人工审查的理由，也不得为了“按时完成”写死公司/页码/表号规则。

## 19.17 P0/P1/P2、关闭门与待确认项

### 19.17.1 P0（立即停止）

- 放宽 fail-closed、伪造 span/cell/header/continuation、改写 TS4/历史 Evidence；
- 用公司/页码/表号/gold 特例让真实样本通过；
- 把 TableObject 升成 Financial authority；
- 接受 raw snapshot/JSON/路径而绕过 verified wrapper；
- 为通过验收删掉 unsupported/unresolved、缩小候选范围或掩盖 conservation gap；
- 越界实现 TS6/TS7/R3/P4 或写 Evidence/Financial/Pack Store。

### 19.17.2 P1（阻塞 TS5 完成）

1. to-4 无法表达 key-value form、cell 内 blocks 或 component provenance；
2. 只处理 table dispositions，遗漏全部 components 中位于真实 cell 的 unassigned 内容；
3. 普通段落/视觉对象伪表，或真实表格因旧 AND 门系统性漏掉；
4. cell 只保存首 locator、换行/merged structure 丢失；
5. caption/note/analysis 与 table body 混在一起；
6. continuation 跨错表、错页、错文档或仅凭重复 header 自证；
7. cell-level citable coverage 缺失，整表被一条 aligned 来源授权；
8. disposition/component/Layout/Evidence 任一守恒不为零；
9. final verifier 不能独立重建，或 capability 可伪造/序列化；
10. 身份图有环、TableObject 回指 relation/final snapshot、或 final fingerprint 回填上游依赖；
11. 11 类 component landing 没有按 §19.9.1 封闭映射，或同一 component 被复制到多个 terminal；
12. table/cell 分类规则无已登记 profile/version/file hash，或分类产生数字 authority；
13. 任一 `upstream_table_scope_miss` 尚未通过上游 successor 裁决消除；该状态不是可保留 P2，直接
    阻断对应文档 final capability 与 TS5 关闭；
14. 任一 `root_identity_mismatch` 尚未消除；不得把上游“空/非内容/正文域外”与真实来源不一致当作
    普通 P2；
15. TableObject 存在 orphan/双 owner，或任一 relation endpoint 不能按封闭 algebra 对象级解析；
16. acceptance/review/seal 缺版本、check IDs 不闭合、自报 approve 与双角色逐项结果不一致；
17. 三份真实 PDF、non-300750、表内小标题、非财务表、财务权威负门任一未验收。

### 19.17.3 可保留但必须可见的 P2/gap

- 个别真实区域为 partial/unsupported/unresolved；
- title/unit/total 在源文档中确实不存在；
- headerless grid 无法证明列语义；
- continuation ambiguity；
- non-citable cell fragments；
- visual object not table；
- table-only node 无 paragraph synopsis。

这些诚实 gap 不自动等于能力失败，但不得计为恢复成功、不得支撑 `set_complete`，并须进入产物和
后续 TS6/TS7 缺口视图。

### 19.17.4 TS5 最终关闭门

只有以下全部成立，用户 + Codex 才可批准关闭：

1. to-4 successor/current-legacy 真值表与唯一受信 I/O 通过独立代码审查；
2. 全部 table-related dispositions 唯一终态，全部 TS4 components 唯一 final binding；
3. final conservation 零差额；
4. 每个正式 TableObject 通过 grid、cell fragments、上游身份、Evidence/alignment、relation 与
   continuation 对象级核验；
5. 所列真实业务样本与 non-300750 fixture 经人工读回；
6. 表内小标题完整保留，普通正文/组织结构图零伪表；
7. 财务主表没有任何金额 authority 升级；
8. focused + TS2/TS3/TS4 回归 + 完整 eval 全绿，并单独报告真实内容质量；
9. Evidence/Financial DB identity 未变，生成产物 create-only 且 review attestation 有效；
10. identity DAG 自动检查通过，final snapshot 为唯一终端聚合节点，成员/relations 无任何反向回指；
11. 11 类 component landing 封闭映射与子区间分区反例全绿，全部 TS4 components 精确等集且只绑定
    一个 terminal；
12. `tcp-1/tcbp-1` 及 file hashes 已登记并进入上游指纹，legacy/unknown/profile 漂移 fail-closed，
    分类不产生 authority；
13. verifier independence 的 AST/import guard、builder monkeypatch、tamper-and-rehash 反例全绿；
14. `upstream_table_scope_miss` 为 0；如真实存在则先回到相应上游 successor 处理，不得带病关闭 TS5；
15. `root_identity_mismatch` 为 0；
16. 所有 TableObject 恰有一个 verified owner，全部 resolved relations 满足 endpoint algebra 与对象级回查；
17. `tar-1/ttr-1/trm-1/tai-1/trv-1/tra-1`、固定 10 个 check IDs、双角色全 pass 与 create-only
    attestation 均通过篡改/版本漂移反例；
18. 生产代码零公司/页码/表号/Evidence ID/gold 特例，且未进入 TS6/TS7/R3。
19. 方案 C 隔离门通过：封存 TS4-B 四份 snapshot identity 与 B6 完整读回零漂移；TS4/final synopsis
    类型、版本和 reason 词表不可混装；`ts5_trust_roots.json` 逐份绑定封存 index，而非首轮错误重建 ID；
    无 `spn-2`，无通过缩窄 TS4 回归换取全绿。

### 19.17.5 批准记录与执行授权

本计划没有新增开放性业务问题。上述技术裁决均遵循已批准的树结构方向、数字权威分离和只读演示边界。
用户已于 2026-09-19 批准 §十九作为 TS5 唯一执行规格，并授权 Claude Code 按一个完整编码批实现。
用户并于 2026-09-19 批准 §19.0.1 的方案 C 勘误。该授权不包括自行变更其他业务语义、缩减必要范围、
进入 TS6/TS7/R3、提交实现代码或自行宣布 TS5 关闭；编码与真实验收产物完成后仍须停止等待用户 +
Codex 独立验收。

## 十九 · 小结

TS5 的核心不是“尽量多抓几张表”，而是把 TS4 留下的表格范围和全部来源 components，确定性转换为：

```text
VerifiedSpanSnapshot
  → geometry-backed single-page TableObject to-4
  → cell-level provenance/citability + typed relations/continuations
  → immutable decisions/bindings + final spans + FinalNavigationSynopsis nss-3/ns-3
  → zero-conservation-gap FinalMaterialStructureSnapshot + explicit content gaps
  → independently verified runtime capability
```

成功恢复与诚实缺口并存；结构对象与数字权威分离；物理 fragment 与未来跨树聚合分离。该批通过后只允许
进入 **TS6 计划/实施**，并不代表材料库、正式 Retriever/Pack/P4 或整棵树门已经关闭。

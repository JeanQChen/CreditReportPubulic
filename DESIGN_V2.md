# 授信报告生成器 V2 设计文档

> 状态：实施纲领 v0.15 · 2026-10-05（正式树结构门仍开放；TS5 no-go、未 seal、未关闭；Demo Backbone 处于 M930-3、未关闭。§0.23 为现行三屏演示裁决，取代 §0.22 第 2 条：同一次上传创建同一个新 run 并同时产出 `company` 与 `financial`，首屏增列三份财务 XLSX 的 typed 绑定（`cfi-1`）。§0.21 是现行演示原表呈现及 M930-3 内容验收裁决，替代 §0.17–§0.19 对演示目标表的结构化签发前置；§0.20 的 Pack 驱动写作继续有效。旧写作侧原子 Claim 链为历史兼容。）
> 基线：历史 V1 `DESIGN.md`、已交付的 V2 基础能力与当前代码
> 目的：定义 V2 的产品边界、报告契约、Evidence 架构、检索、Research Harness、评测与全报告质量保障。本文首先用于确认设计，不代表所有模块已经实现。
> 实现状态：Phase 0A～3 的历史验收和冻结结果原样保留；Phase 3 frozen_final 是安全性、路由与单题实际路径基线，不等于已经满足完整主题研究。Phase 4 的规划、Worker、Evaluator、Store 与 UI 基础已实现，但因 P3→P4 信息吞吐和内容完整性不足，于 2026-09-12 重开 P3R/P4R 内容能力门。正式 Phase 5/6 仍未进入；仅允许按 `2026-09-30_DEMO_BACKBONE_MILESTONE.md` 在同一生产接口上实现代表性纵向演示切片。§4.3 财务指标口径仍以 `FORMULA_REVIEW.md` 为准。
> 文档治理：权威顺序、历史资料和运行时资产的角色见 `DOCUMENTATION_INDEX.md`；语义冲突必须 fail-closed 修正文档/发布新版本，不能靠“挑一份喜欢的文档”继续实现。

**现行覆盖说明（2026-10-01）：**§0.21 将本次 Demo 的“原表呈现”改为经人工确认范围的**原 PDF 表格区域只读展示**，不再把六张目标表全部自动重建并签发为 `TableObject` 当作 M930-3 演示内容门的前置；正文中的营业收入、成本、毛利及比较数字仍须另有少量格级合格事实，计算仍由 Python/Decimal 完成。§0.17–§0.19 的目标表自动签发门只保留作此前裁决和正式表格能力轨的待办，不再指挥本次 Demo 的验收。§0.20 的 Writer 直接从 Pack 成文、逐句硬核对及独立审阅继续有效；研究侧与财务权威、冻结 Contract/SourcePolicy/WritingSpec、TS5 正式关闭门不变。**这是设计与验收范围变更，不是代码已实现或真实内容已通过。**

---

## 0. 阅读说明与待办标记

本文使用五种标记，明确哪些内容已经确定、哪些必须由产品/业务负责人确认。

| 标记 | 含义 | 谁负责 |
|---|---|---|
| `[继承V1]` | 沿用现有硬约束或已验证设计 | 无需重新决策，除非主动推翻 |
| `[建议默认]` | V2 推荐方案，可先按此实施 | 技术实现方 |
| `[已确认]` | 已由业务负责人确认，可作为后续实现依据 | 产品与技术共同遵守 |
| `[待你确认]` | 会改变产品或报告口径，不能由 Coding Agent 擅自决定 | 产品/业务负责人 |
| `[待你补齐]` | 需要业务知识、模板、样例或人工标注 | 产品/业务负责人 |

### 0.1 已确认的产品决策

| ID | 已确认决策 | 实施含义 |
|---|---|---|
| D-01 | 第一阶段不开发项目分析；第二阶段加入 | 第一阶段只运行公司信用、财务、行业和综合评价；项目分析仅在固定资产贷款或项目贷款时启用 |
| D-02 | 第一阶段保留四段输出，但第四段改为“综合评价” | 不主动生成新的授信额度和期限建议 |
| D-03 | 公司、财务、行业章节主题按 §4 当前版本执行 | 进入 Section Contract 固化与评测题映射 |
| D-04 | 综合评价只判断用户提交的授信方案 | 除明显不合理外，主要输出方案优缺点和综合结论，并声明“AI 生成，仅供参考” |
| D-05 | 项目材料最小范围按 §4.5 保留 | 第二阶段实施，预测数据强制 Excel，项目研究禁止联网 |
| D-06 | 第一阶段只支持 A 股上市公司 | 外部核验和样本范围均围绕公开上市公司 |
| D-07 | 第一阶段仅接受电子 PDF 和 Excel；财务允许二者混合上传 | 扫描 PDF/OCR、Word、PPT、图片放入第二阶段规划 |
| D-08 | blocking 问题阻止系统审核通过 | 允许查看带问题的预览版；修复并复检后才可达到“系统审核通过、可供人工确认”，人工最终确认仍是独立状态 |

### 0.2 补充确认事项

| ID | 已确认决策 | 实施方案 |
|---|---|---|
| O-01 | 第一阶段财务 PDF 仅支持电子 PDF，不支持扫描 PDF/OCR | 低文本质量或扫描件 fail fast，提示改用电子年报 PDF 或 Excel |
| O-02 | 多个财务来源数字冲突时不自动选口径 | 保留各来源值并生成 reconciliation issue，交客户经理确认 |
| O-03 | 企业核验 MCP 不可用时允许降级 | 降级至交易所公告、国家企业信用信息公示系统等公开来源，并显式提示 |
| O-04 | 主体或控制关系异常时不销毁任务 | 阻止系统审核通过，保留处理结果并标记需人工最终确认；无实际控制人不等于主体不合法 |
| O-05 | 新闻和行业规模 2 年为默认回溯窗口 | 历史沿革和周期比较允许使用更早资料并标注年份 |
| O-06 | 重资产 70%、轻资产 40% 为关注提示 | 不作为自动否决线 |
| O-07 | Evidence 长期保留并允许主动删除 | 按公司/任务删除时先检查最终报告引用关系 |
| O-08 | 第一轮 Retrieval baseline 先评正确页码命中 | 保留 gold answer，答案质量评测后置 |
| O-09 | `report_as_of` 是正式结论截止日 | 晚于该日期的信息只能列为期后事项；发布日期未知的内容不得支持强时点结论。其**取值来源**见 O-11 |
| O-10 | 外部来源按 P3-B02 分级充分性规则使用 | A/B 级可单独支持一般事实；关键负面、主体重大变化、重大风险及关键行业规模/份额结论，至少需要 1 个直接支持的 A/B 级来源，或 2 个相互独立且内容一致的 C 级来源；单一 C 级只作线索或带限制的非关键说明，D 级不得作为关键结论唯一依据 |
| O-11 | 报告结论日期是**本次报告生成日** | 一次运行只捕获**一个时钟瞬间**：`generated_at` 是该瞬间的 UTC 时间戳，`report_as_of` 是**同一瞬间**按**显式配置时区**换算的报告生成日；二者是不同字段且有确定派生关系，不得分别采样（会造成跨午夜错位）。`FinancialSnapshot.as_of_date` 始终只表示财务数据期末，**不得**再被用来推导 `report_as_of`。时区必须来自版本化配置资产，缺配置即 fail-closed。重读历史 run 保留其原始日期身份，不得改写成今日 |
| O-12 | 来源清单与披露日期口径 | 为**全部当前、政策允许**的上传材料建立可回查来源清单；分别登记可核实的披露/发行日期**及其提取依据与置信状态**、事实或财务期间、入库时间。「全部登记、可检索」不等于「全部同权用于当前事实」，也不等于把全文无筛选送给 Writer。同类材料按「**较新且可核实者优先表达当前状态**」；旧同类型材料**保留索引与来源身份**，用于历史分期数据、变化、补充与冲突核对，不得静默丢弃。年报与募集说明书同属用户上传材料但**是不同文档类型**，按具体主题、披露日期与事实期间**共同检索**，不得让一份无条件替代另一份。缺披露日期就标 `unknown`，**PDF 元数据、入库时间、最新财务指标日一律不得冒充披露日期**，也不得回填为发行日期、不得一律伪称「截至某日已披露」。网络事件分别保留事件发生日、来源发布日期与抓取时间。本规则**公司无关、文件名无关、页码无关** |

### 0.3 Baseline Runner 已确认口径

| ID | 已确认口径 | 实施含义 |
|---|---|---|
| B-01 | 主指标使用严格页码命中；相邻 ±1 页只作为诊断指标，不算正式命中 | 防止放宽主指标掩盖页码映射或切块问题 |
| B-02 | 仅含外部来源的题不进入 V1 本地 Retrieval 总分；本地+外部混合题只评价其中本地证据组 | 避免把 V1 Retriever 无法访问的互联网证据错误计为检索失败 |
| B-03 | 总体主分采用 eligible case 等权的 Macro `RequiredPageCoverage@10`，不做人为加权；同时强制展示 P0 `RequiredPageCoverage@10` 独立关键指标 | 总体分反映必需证据的部分覆盖程度，P0 指标防止关键授信问题被普通题高覆盖率掩盖；PageHit 仅表示至少命中一页 |
| B-04 | 全部必需本地证据完成可靠映射后，整题才进入正式分母 | 部分映射题单列诊断与排除原因，不删除缺失部分后计分 |
| B-05 | 当前41问的页码均为“且”，不是“或” | `/`、`+`、跨文档引用及页码范围全部表示必需页；完整覆盖以 `AllGroupHit` 判断 |

### 0.4 2026-09-06 交互与恢复确认（历史基线；当前面试版范围由 §0.7 修订）

- `[历史确认 F-05]` 财务冲突必须选择来源并说明理由，或补充更正材料；自动重算并通过完整回检后方可正式导出，不提供“忽略冲突”放行。现有底层确认能力保留，但当前面试版不交付用户补件、绑定和重算交互，见 §0.7。
- `[历史确认 F-06，当前范围由 UI-01/UI-02 修订]` 原计划以集中待确认面板批量处理问题；当前面试版只读汇总这些问题，不提供批量确认、补件、重算或续跑动作，交互细则见 §4.3.1。
- `[历史确认 H-04]` 达到预算后保留已有结果，原计划由用户点击“继续生成”追加有限预算。当前面试版仍保留有界预算、缺口和 checkpoint，但不交付用户触发的继续生成入口，见 §0.7。

### 0.5 2026-09-12 P3→P4 内容完整性架构修订（现行）

本节是对本文原 Phase 3/4 接口的权威修订。历史 `ResearchOutcome`、评测结果和冻结产物继续保留，但以下规则优先于本文后续仍保留的旧式“单题短答直接进入章节”描述：

| ID | 现行规则 | 实施含义 |
|---|---|---|
| G-01 | `TopicResearchPack` 是公司/行业等开放研究 Topic 从 P3 向 P4 的唯一正式内容交付物 | `ResearchOutcome` 只作为一次原子研究运行记录和兼容评测对象；P4 不得再只遍历 `answer.claims` 生成章节。财务确定性 Workflow 继续交付独立权威的 `FinancialFactPack` |
| G-02 | `required_aspects` 是研究调度和完成判断的最小业务单元 | 一个宽问题必须在内部形成 aspect 待办，不因找到一条相关 Evidence 或写出一句答案而提前结束 |
| G-03 | 命中后按树结构读取，必要时执行受控后备扩读 | 正常路径先定位同文档版本的最小充分 Outline 节点/子树并读取 `OutlineSpan/TableObject`；只有 outline 不可用、低置信、文本截断或跨节点引用时，才按相邻块/页、续表和明确交叉引用有界扩读；禁止无界“往后读” |
| G-04 | 预算按 Topic 复杂度动态分档且始终有硬上限 | 预算不足时保存已取得材料并明确未覆盖 aspect；不得只把统一单题预算调大，也不得无限循环 |
| G-05 | 只有一条正式研究主链 | `SectionContract/Task → Worker编排外壳 → Harness Topic runtime → (Router → ToolRegistry)* → TopicResearchPack → Worker writer`；实验 topic research 可作为算法候选，但不得形成第二套运行时 |
| G-06 | P4 同时保留原子事实和连贯表达 | `SectionClaim` 用于审计，`NarrativeParagraph`/表格用于人读；一个段落可由多条 Claim 支撑，但不得创造 Pack 中不存在的事实或数字 |
| G-07 | 历史冻结结果不可回写 | Phase 2/3 gold、split、历史 run 和验收报告不修改；重整使用新 schema/policy/prompt 版本和新 run_id 独立评测 |
| G-08 | Contract 与写作规格分责 | Contract 决定“研究什么、最低证据和缺口语义”；版本化 `SectionWritingSpec` / `ReportPresentationProfile` 决定“如何把 Topic 组合成小节、段落和表格”。禁止继续让 Prompt 或旧 Markdown 模板充当影子 Contract |
| G-09 | Section 必须消费完整 Pack 集 | 每个 Section 只能消费与 `SectionTask.topic_ids`、任务/公司/时点/依赖指纹完全匹配的一组 Pack；缺失、重复、错配或 stale Pack 必须显式 gap/block，不能挑一个 Pack 写整章 |

当前相近对象必须收敛而不能再新造第四套：Harness 拥有正式 `TopicResearchPack`；`ResearchOutcome` 是其原子输入；既有 `sections.material_bundle.TopicEvidenceBundle` 迁移为 Pack 内部材料视图或兼容适配器；实验 `sections.topic_research.TopicResearchPack` 不直接升格；`AspectCoverageResult` 与 `ExternalFunnelProjection` 只是 Pack 的审计投影，不是调度器或正式交付物。

本轮具体实施、迁移和验收以 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` 为唯一任务书。

### 0.6 2026-09-13 R1-A 冻结资产（已批准、已冻结、未接线）

R1-A 只生成「版本化声明资产 + 只读 schema/loader/validator + 审计导出 + 离线测试」，不接正式运行时。资产已由用户与 Codex 批准并冻结、按职责提交；尚未接线正式运行时：

| 资产 | 载体 | 状态 |
|---|---|---|
| Contract v2（已冻结，尚未接入默认运行时） | `templates/contracts/standard_v3.yaml` | 52 问（28 topic_harness / 13 financial_workflow / 3 phase4_section_derived / 8 phase5_synthesizer）、187 aspect、每 aspect 22 字段、49 evidence 需求 |
| 来源政策 v1 | `templates/policies/source_policy_v1.yaml` | A/B/C/D 分级、关键结论支撑、独立性、时效窗口、行业风险传导四层 |
| WritingSpec v1 | `templates/writing_specs/credit_report_v1.yaml` | 逐字 8/5/9 H2 目录 + 187 primary / 6 secondary_reference，每 aspect 恰一 primary |
| PresentationProfile v1 | `templates/presentation_profiles/interview_demo_v1.yaml` | 只允许 display/folding/screenshots/appendix，禁止 fact/coverage/citation/business_judgment 变更 |
| 审计产物 | `contracts/review/review_52q.json` / `.csv` | 52 问 × 187 aspect × 49 evidence 展平快照 |
| 只读代码 | `contracts/loader_v2.py` `validator_v2.py` `source_policy.py` `sections/writing_spec.py` `presentation_profile.py` `contracts/review/topic_aspect_evidence_review.py` | 纯声明式，不 import Router/Harness/Worker/Writer |
| 离线测试 | `evals/test_contract_v2_assets.py`（已注册 `run_evals`） | 153 项全绿 |

硬边界：`standard_v2.yaml`（v1）未被覆盖（固定 SHA256 `23e1735e3b77e94dacae70be03712ca93c98d8f545cc087f8d8b092ad841ae45`）；Contract v2 在 **R1-A 冻结时点**尚未设为默认、尚未接线 Router/Harness/Worker/Writer；当时未改检索/预算/Prompt/LLM，未迁移/checkpoint/Fact Registry，并已按职责提交（`30dbc83` `884edd4` `4f4b654` `ee51cd8` `b5c6e5b`）。后续阶段事实以 §0.8～§0.10 和 `V2_TODO.md` 为准，不从本段历史快照推断当前进度。R1-A 专项 153 passed / 0 failed、完整 eval 4218 passed / 0 failed / 0 skipped 全绿，但只证明资产自洽，不宣称 P3R/P4R 或 Phase 4 内容关闭。

### 0.7 2026-09-13 面试版交互、状态栏与最终审核边界（现行）

| ID | 已确认决策 | 实施含义 |
|---|---|---|
| UI-01 | 当前面试版不实现报告生成后的用户补件闭环 | 只读展示缺少什么、已查范围、原因、影响及建议材料类型；不提供补充上传、缺口绑定、Evidence 增量更新、用户处理后定向续跑 |
| UI-02 | 保留未来扩展口，不展示尚不可执行的按钮 | Gap 保留稳定身份、影响范围、建议材料类型和未来动作类型；现有 checkpoint/确认底座可保留，但不作为当前 UI 能力承诺 |
| UI-03 | 状态栏是当前版本核心用户能力 | 进度来自持久化任务单元和产物，不由模型估计；分别显示流程是否结束、草稿是否可预览、系统审核是否通过、是否等待人工最终确认 |
| V-04 | 最终审核由受限 `Assurance Controller` 组织，不由生成模型自由自评 | 先运行内容完整性前置门与确定性检查，再运行有证据输入的受限语义审稿；LLM 只能返回结构化 issue/定位/返工目标，不能覆盖硬失败、重写正文或直接放行 |
| V-05 | 系统最高状态是“已通过系统审核，可供人工确认” | 报告版本、输入、Pack、Claim、引用、规则和审核结果必须绑定；报告变化使旧审核失效；主体、重大负面、关键财务冲突和授信方案仍由人工最终确认 |

本次范围修订不删除历史 checkpoint、ResolutionRecord、财务确认或依赖失效代码，也不禁止未来产品版本实现补件闭环；只是将它们从当前面试版 Phase 5/6 出口中移除。当前缺口是只读审计产物，不是待用户在线处理的工作队列。预算耗尽、来源不足和必须人工判断均应形成明确状态，但页面不提供“继续研究”或“补充材料”动作。

### 0.8 2026-09-14 R1-B 关闭与 R2 编码前状态（历史状态；现行见 §0.9～§0.10）

R1-B 已正式关闭：唯一 `TopicResearchPack` schema（v2）+ append-only Pack Store + 追加式 migration 2 + `set_complete` 独立枚举接口（`SetEnumerationVerifier`）+ SourcePolicyRef Pack 内唯一绑定；旧 v1 Pack 的 current/checkpoint/历史默认读一律 fail-closed，历史不 UPDATE/DELETE。完整离线 eval 基线 4467 passed / 0 failed / 0 skipped 全绿；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。

当时正式、版本化、确定性的 `SetEnumerationVerifier` 实现尚待 R2；该句仅记录当时入口状态。后续实现与精确工作区事实见 `V2_TODO.md`。其信任边界仍有效：Store 只能交叉校验身份与结果，不能从任意注入实现的自报证明其内部执行过程。

R2 后续形成的只读访问、Store、哈希、authority、trace、显式引用和 fail-closed 能力继续复用；其旧材料边界假设已由 §0.10 的树结构调整取代。精确工作区 / 测试数字只记录在 `V2_TODO.md`。

### 0.9 2026-09-16 R2 材料能力验收：三轴状态模型与完成定义（现行）

本节是 **R2 完成定义的冻结**，覆盖此前所有以「六类 material 全部 complete/accepted」为目标的表述。

**三轴状态模型（分别建模、禁止互相自动映射）。** 每个材料的验收结果由三条**独立**轴表示，三条轴之间**没有**任何自动映射或等价关系：

| 轴 | 取值 |
|---|---|
| `material_state` | `complete` / `partial` / `boundary_incomplete` / `not_obtained` / `unsupported` / `invalid` |
| `capability_verdict` | `PASS` / `FAIL` / `NOT_TESTED` |
| `report_impact` | `blocking` / `non_blocking` / `audit_only` |

合法组合示例：`boundary_incomplete / PASS / blocking`、`not_obtained / PASS / non_blocking`。`capability_verdict = PASS` 只表示**系统正确、可复核地得出了该材料状态**，**不**表示材料完整，**也不**表示报告可发布。旧调用方使用的 `verdict` 字段只是三轴的确定性兼容视图，权威输出是三轴本身。

**完成定义（R2 不以「全部 material complete/accepted」为关闭条件）。** 诚实的 `boundary_incomplete` / `not_obtained` / `unsupported` 可以是 `capability_verdict = PASS`，并且**不**自动构成代码缺陷；负面材料状态本身不得被当成代码失败（失败门只表达能力门或完整性门失败）。

**R2 职责边界。** R2 只负责：材料构建、受控上下文扩读、边界证明、持久化、材料能力验收。以下属于 **R3 正式事实形成与 Pack 状态**职责，R2 **不做**、也不得代做：授信金额语义模式、币种推断、used/unused 业务对账、multi-source conflict 双轴、授信 `REPORT_BLOCKED` 映射、授信正式 Writer/报告展示。

**授信预览的定位。** 现存 `evaluation/results/r2_credit_dual_axis_*_preview_*` 各轮均保留为 **evaluation diagnostic / R3 candidate**：它们不是 R2 或树结构调整的关闭门，也**不得**被引用来宣称「正式运行链接线完成」。具体轮次与现场状态只记录在 `V2_TODO.md`；历史预览与相关代码不删除、不回滚，只标记定位。

本节不修改冻结的 Contract（`templates/contracts/*.yaml`）、SourcePolicy（`templates/policies/source_policy_v1.yaml`）、WritingSpec（`templates/writing_specs/credit_report_v1.yaml`）与 PresentationProfile（`templates/presentation_profiles/interview_demo_v1.yaml`）；R2 中任何验收都不得通过放宽 authority、`set_complete`、来源边界或 hash 校验来提高完成率。

### 0.10 2026-09-16 树结构调整（已确认，现行）

真实年报和募集说明书审计证明，现有 `EvidenceBlock` 主要由页内双换行、字符上限和兼容标题提示决定：它具有可靠来源身份，却不是可靠的章节、段落、表格或业务对象边界。一个 Evidence 可以跨越多个正文小标题、多个 Contract 主题或表格与表后分析；旧 `section_path` 也可能是表格行、年份或残片。继续围绕相邻块、固定页距和 seed 哨兵修补，无法从根本上保证材料完整性。

以下决定已经用户确认，覆盖本文中把“整块 Evidence + section_path/相邻块”当作正常材料边界的旧表述：

| ID | 已确认决定 | 实施含义 |
|---|---|---|
| T-01 | `EvidenceBlock` 降为不可变来源与引用锚点 | 历史 Evidence ID/Evidence Set 不回写；整块文本不能因为一次命中直接成为语义材料或覆盖证明 |
| T-02 | 每个支持的电子 PDF 生成版本化 `PageLayout` 与只读 `DocumentOutline` | 从原始 PDF 或同一 canonical layout 派生；目录/书签只是候选，必须由正文大小标题、小标题、编号连续性和版式锚点确认 |
| T-03 | `OutlineSpan` 与 `TableObject` 是正式本地消费单位 | 一个 Evidence 可映射多个 span，一个节点可聚合多个 span；表格与叙述文字分读，并保留表题、单位、表头、表体、合计、续表和关联关系 |
| T-04 | RAG、Pack 与 P4 切换为树感知消费 | 先定位候选节点，再加载最小充分节点/子树的 span/table；引用仍回指底层 Evidence 与精确字符/页/坐标定位 |
| T-05 | Contract→标题树相似度只用于导航 | 标题、确定性简介、子标题和表题参与候选排序；aspect covered 仍由合格材料、事实、引用、权威和 Contract completion rule 决定 |
| T-06 | 旧扩读能力降为有界后备 | 相邻块/页、rolling frontier、显式引用用于 outline 不可用、低置信、文本截断或跨节点引用；不再承担普通文档的主要业务边界判断 |
| T-07 | 权威分离保持不变 | `TableObject` 是 Evidence-backed 结构对象，不是新的数字权威；FinancialSnapshot、Evidence 附注事实、普通业务表和 ExternalSnapshot 继续分别校验 |
| T-08 | 树结构调整是 R2→R3 强制门 | 标题层级、跨标题 span、TableObject、树感知检索/材料消费及真实纵向样本通过前，不进入 R3，不继续围绕旧 seed/邻接边界做局部补丁 |

树结构是非破坏性结构层，不要求立刻重写历史 Evidence。若原 Evidence 文本覆盖不足或无法建立精确 span，允许从同一原始 PDF 生成新的 append-only `evidence_set_version`；旧集合只读保留，不 UPDATE、不伪造字符范围。节点 synopsis 只作导航元数据，不能作为 Evidence、Citation 或事实来源。

### 0.11 2026-09-30 面试 Demo Backbone（已确认，现行）

TS5 最新真实运行证明，代码回归与 schema/fail-closed 机制可以全绿，但三份真实文档的表格守恒和人读内容门仍未通过。TS5 因此保持未关闭，失败产物不得重标、改 pin 或通过人工补勾换成通过。与此同时，当前产品目标是面试 Demo，而不是在 9·30 前完成全部业务覆盖。用户已批准以 `2026-09-30_DEMO_BACKBONE_MILESTONE.md` 建立一个时间盒式代表性纵向切片。

该裁决采用三条独立轴：

| 轴 | 含义 |
|---|---|
| `design_surface_coverage` | 核心架构层、角色、输入输出和状态是否均有真实对象/产物可展示 |
| `demo_content_coverage` | 少量代表性公司、财务、行业、事件与缺口内容是否可读、可回查 |
| `formal_phase_closure` | 各正式阶段是否满足原关闭门；不受 Demo 可运行自动改变 |

Demo Backbone 允许在正式树门尚未关闭时，沿**同一生产接口**实现代表性的 Pack、Writer、Section Evaluator、确定性组装、独立只读语义审查、Assurance Controller 与 Streamlit 展示。该时间盒例外不允许第二套 Router/Harness/ToolRegistry/Pack/Writer，不允许未签发 TS5 表格进入正式事实，不允许降低 authority/citation/conservation/set_complete 门，也不允许把 `preview_only` 或 `blocked` 渲染成发布通过。

9·30 的成功标准是“设计理念和整条流程可真实演示、事实与状态诚实、少量内容可读”，不是“完整 52 问/187 aspects、完整 TS5、P3R/P4R 或 Phase 5/6 已关闭”。正式能力轨继续按原门推进；10·1～10·7 优先回到 TS5 与完整树门、泛化、性能和内容覆盖。

**§0.17（2026-09-28）对本节收紧了**演示主题的业务内容门：`company_business` 必须**同时**呈现有实质内容的经营文字与合格的原始业务表格，目标表须**逐对象取得正式资格并经现有图/工具链进入 Pack**；表格确实存在却因识别/续表/资格/工具接线失败而未呈现，算**系统能力缺陷**（该门不得通过），不算来源缺口。本节的「少量内容可读」**不再**包含「目标表只列 typed gap 即可通过」这一旧读法。

### 0.12 2026-09-21 M930-3 双轨材料与事实资格纠偏（已确认，现行）

M930-3 实施过程中确认：`TopicResearchPack` 已经同时保存 `materials` 与 `facts`，完整 `OutlineSpan` 正文也确实存在于 material payload 中、没有丢失；但现行 Writer 只把 materials 用于 ID/`payload_ref` 血缘检查，prompt 只收到 Claim 文本与 gaps，最终事实句逐字复制 `fact.text`/`Claim.text`。因此它目前是一个 Claim 排序器，尚未达到“完整材料 + 原子事实共同驱动人读叙述”的设计目标。同时 M930-2 的 `_SpanRecallResearch` 把首个 span 原文直接包装成 `Claim(kind=fact)` 并自报 SUPPORTED，只能证明树工具能取得真实材料，不能证明事实提取合格或内容可写性。以下裁决为现行口径，细化硬规则见 §5.4.1、§5.5 与 §11.0。

**1. 材料与事实并存，不是替代关系**

- `ResearchMaterial` 与 `SupportedFact` 是两个并列层：材料层回答“来源里客观存在什么”，事实层回答“其中哪一条是可独立判断真假的命题”。**事实候选被拒绝，不得删除、裁碎、改写或降级原材料。**
- 完整 material payload、标题路径、locator、provenance 与父子/续表关系继续原样保留；资格判定只决定“能不能单独晋级为事实”，不决定“材料还在不在”。
- Writer 消费**精确等集**的材料上下文，而不是“看起来相关的材料”：company/industry Writer 的上下文来源是当前已验证的 `VerifiedPackSet`；`VerifiedPackSet.topic_ids` 必须与 `SectionTask.topic_ids` **精确相等**；Writer 的输入材料集合必须**精确等于**该 `VerifiedPackSet` 中所有 current Pack 的 `materials` **并集**（含未晋级为事实的上下文材料）。既不只消费 fact 文本，也不读取整库无关材料，更不得由 Writer/assembler/LLM 自行挑选“方便的”“看起来最相关的”子集再把章节表示为完整。该集合由确定性、版本化的 material-context manifest 固化，机械规则见 §5.4.1。
- **可用集合 ≠ 实际使用集合**：**available material set** 必须精确等于上述并集，且其每个成员都必须进入 manifest；**used material set** 可以是 available 的**子集**——未被采用的成员**不是 gap**，只需带 typed 理由（重复、与本节表达目标无关、候选资格未通过、已被更完整材料覆盖、预算分区后未选中）。**“未使用”本身不是 gap**；只有 payload 缺失、身份错误、必需材料不可得或 Contract 必需事实未取得，才形成 gap/block。四层集合（available / processed / used / not-used）的机械规则见 §5.4.1。
- 材料上下文提供**语境、组织与连贯性**输入，也是 Writer 提出描述性 `ClaimCandidate` 的依据；但材料上下文**不得绕过**高风险事实预验证产生新的数字、实体、期间或结论。正式事实引用（factual support）走两条路径：**路径 A** 落到通过预验证的 fact、财务权威、evidence note 权威或外部权威；**路径 B** 落到 exact material/payload/locator 加 `ClaimBindingDecision` 与独立 `ClaimEntailmentDecision`。**高风险硬事实只允许路径 A**；任何已具有合法预验证事实身份的原子断言可用路径 A，非高风险描述性原子可用路径 B；作者侧 `ClaimCandidate` 在两门通过前**不构成正式事实**（§0.13）。
- 财务与外部来源的权威边界不因本裁决改变：`FinancialFactPack`、Evidence 附注事实与 `ExternalFact` 各自独立校验；`ExternalSnapshot` 只是不变正文与来源载体，必须由 `ExternalFact` 引用，不能单独授权事实，也不得与其他数字权威混成同一权威表。

**2. `SupportedFact` 资格**

`SupportedFact` 必须是**可独立判断真假的完整命题**：主体、关系，以及业务所需的期间、范围、单位、选择状态和 required fields 明确。下列内容**不得单独晋级**为 `SupportedFact` 或 `SectionClaim`：

| 不得晋级的形态 | 说明 |
|---|---|
| 孤立标题、字段标签、表头、单位 | 只有名目，没有断言 |
| 版式残片 | 由分栏、换行、编号或抽取截断产生的碎片 |
| 未解析的“适用/不适用”勾选项 | 选择状态未解析，命题真假不可判 |
| 只有披露规则而没有公司事实的规范性文字 | 规则文本不是被研究主体的事实 |
| 缺少必要表头/表体/单位/期间组合的表格片段 | 结构不完整，无法确定所指 |
| Contract 要求期间或必要字段但尚未闭合的候选 | 资格不完整 |

这些内容仍保留为材料，并且事实候选必须形成 typed、versioned qualification/rejection decision；若该拒绝导致 Contract-required aspect/fact 仍未满足，才**另外**形成 `ContractGap`/Block。两条记录不得互相替代；材料不得静默丢弃，也不得用自由文本“看起来像事实”替代类型化判断。

**3. 两类支撑语义**

```text
factual support 有两条合法路径，都必须显式声明 support_semantics = factual：

  路径 A（预验证权威路径）——任何已具有合法预验证事实身份的原子断言
     SectionClaim → SupportedFact / FinancialFact / Evidence note fact /
                    ExternalFact + ExternalSnapshot source carrier
                  → 该 authority 分支要求的 container + source/provenance + payload/exact locator
     （只有 topic_pack 分支必然绑定 ResearchMaterial；其他分支不得伪造 Topic material。
       必须绑定已通过预验证的事实身份；authorization basis = 该权威本身）

  路径 B（材料派生描述路径）——只用于非高风险的描述性原子断言
     ClaimCandidate → exact ResearchMaterial / payload / exact locator
                    + ClaimBindingDecision（机械门，仅证明绑定成立）
                    + 独立版本化 ClaimEntailmentDecision（语义蕴含）
     （不要求预先存在 SupportedFact；但必须走 factual support，不得伪装成 context，
       不得用于高风险硬事实，且必须绑定 exact material/payload/locator 与独立蕴含决定）

context support:   NarrativeDraftUnit → authority-specific context source
                   （必须显式声明 support_semantics = context；不要求 fact_id，
                     也不要求 ClaimEntailmentDecision；只接受所属 authority 分支规定的
                     container + source/provenance + payload/exact locator。
                     它不得以 ClaimCandidate / SectionClaim 为 target）
```

**两条路径的真值表**

| 断言类型 | 必须绑定的授权依据 | 不得 |
|---|---|---|
| 高风险硬事实：数字、金额、比率、日期、期间、币种、单位、财务数字、授信语义、勾选状态、表行/列关系、法律实体、明确否定等 | **只允许路径 A**：预先存在的预验证权威——研究侧 `SupportedFact`、`FinancialFact`、Evidence note fact，或绑定 `ExternalSnapshot` 来源载体的 `ExternalFact` | 走材料派生捷径；让 snapshot 单独授权；用描述性候选冒充；用 context support 授权 |
| 其他已具有合法预验证事实身份的原子断言（非高风险） | **路径 A**（优先）或路径 B | 用一个原子的授权掩盖另一个原子 |
| 非高风险的描述性断言，由指定材料原子蕴含 | **路径 B**：exact `ResearchMaterial`/payload/locator + 机械 `ClaimBindingDecision` + 独立版本化 `ClaimEntailmentDecision`；**不要求**预先存在 `SupportedFact` | 伪装成 context support；跳过任一决定；由 Writer 自批；用于高风险硬事实 |

**原子性规则**：路径选择是**逐原子**的，因为**一个 `ClaimCandidate` 只能表达一个原子断言**。包含多个断言的混合自然句**必须拆成多个 `ClaimCandidate`**：每个高风险原子独立走路径 A，每个描述性原子独立走其合法路径；`NarrativeSentence` 可以绑定多个已接受 `SectionClaim`。不得在 wire 无 atom ID 的前提下宣称“单个混合 Claim 内逐原子绑定”。**不新增 `ClaimAtom` 体系**，除非只读审计证明现有原子 Claim 约束确实无法表达该规则。

- `factual support` 是唯一能授权事实性断言的支撑；它必须落到 typed **authorization basis**：路径 A 是权威身份（fact/权威对象 ID），路径 B 是 exact material/payload/locator 加机械绑定决定与独立蕴含决定。
- `context support` 只用于恢复语境、组织叙述与解释背景，**永远不能授权事实性断言**。
- 研究侧资格**不是**"一次判定、穷尽全篇"：它是对 Contract 要求高风险硬事实的**非穷尽**预验证。Writer 通过**材料派生路径**提出描述性 `ClaimCandidate`（路径 B），且不得自批、回写历史 Pack、绕过预验证夹带新的硬事实或自评。准确表述是：Writer **不得**捏造材料或权威中不存在的事实，**不得**绕过预验证引入高风险硬事实；但它**允许**提出由指定材料原子蕴含的描述性候选（写作侧候选通道见 §0.13）。
- **路径 A 不是"只允许高风险事实"**：任何已经具有合法预验证事实身份的原子断言（含非高风险原子）都可以走路径 A；**高风险硬事实只能走路径 A**。**路径 B 只用于非高风险的描述性原子断言**，不得用于高风险硬事实，也不得声称描述性断言只能用路径 B。
- **每条 support edge 必须显式、类型化地声明自己是 `factual` 还是 `context`；不得用 `fact_id is None` 反推角色。** 因此**不能把所有 `fact_id=None` 的 material-only support 一律判非法**——判非法的对象是“角色缺失/歧义”与“拿 context 支撑去授权 factual Claim”。
- **路径 A** 的 `factual support` 缺少所属 authority 分支规定的预验证 fact identity 时**必须拒绝**；**路径 B** 允许没有 `fact_id`，但必须以显式 authorization basis 标注为材料派生路径，并绑定 exact `ResearchMaterial`/payload/locator 与独立蕴含决定——既不得与 `context support` 混同，也不得因缺少 `fact_id` 被误判为 context。每条 edge 都必须独立绑定所属分支规定的 container、source/provenance、payload 与精确 locator；只有 `topic_pack` 及“已正式采纳为 Pack material”的例外分支要求 `ResearchMaterial`，不得给财务、note 或 external 分支伪造 material。
- `context support` 允许不带 `fact_id`，**也不要求 `ClaimEntailmentDecision`**（context 不是 factual 授权路径，不产生蕴含决定）；但只能用于**语境、结构、衔接与非事实性表达**：**不得单独授权 factual Claim**，**不得引入**新的数字、实体、期间、因果或结论。
- support role **缺失、歧义，或与 Claim/Narrative 类型不相容**时 **fail-closed**。若当前 wire 无法表达该 typed role，登记进 successor changelist（§5.4.1、实施计划 §6.2.2），**不得用 `fact_id is None` 猜测角色**。
- **不得再写成"所有可写事实进入 `TopicResearchPack`"**：进入 Pack 的是**研究侧预验证事实**及其候选/资格决定；写作侧路径 B 产生的正式 `SectionClaim` 属于 **Writer/Section successor**，不得写回 Pack。

**4. 自然叙述**

- `Claim` 是原子审计单元；`NarrativeSentence`/`NarrativeParagraph` 是人读表达单元，二者不可互相替代。
- 一个事实句可以绑定**多条** Claims；Writer 可以合并、排序和改写为自然表达，但不得增加 Claims/材料中没有的事实、数字、实体、期间或结论。
- Renderer 不得把 Claim 列表机械打印成报告；机械拼接不构成人读内容门通过。
- 自然叙述必须经过确定性血缘/数字门，以及**独立只读章级 role-aware entailment 检查**（防自我证明边界见 §11.2）。检查对象是**角色**而不是“被 Claims/materials 蕴含”这个可能被读成 OR 的表述：每个事实性原子必须由合格 Claim/fact 及其 **factual support edge** 蕴含；**context material 只能**验证背景、结构、衔接与“未新增事实”，**不能补足缺失的合格 fact**。Writer 不得自评放行。

**5. 支撑语义是第三条正交轴，不得覆盖既有 `support_role`**

支撑关系必须建模为至少三条**相互正交**的轴，不得用一个字段兼任：

| 轴 | 取值 | 回答什么 |
|---|---|---|
| `authority_kind` | `topic_pack` / `financial_pack` / `evidence_note` / `external_snapshot` | 这条支撑来自哪一类权威来源 |
| support priority/role（**已有字段名 `support_role`**） | `primary` / `corroborating` | 它是主支撑还是佐证 |
| support semantics（逻辑名 `support_semantics`） | `factual` / `context` | 它授权事实，还是只提供语境 |

现行 `ClaimSupportRef.support_role` 已经表示 `primary` / `corroborating`（`sections/narrative_schema.py` 中的 `SUPPORT_ROLES`），**不得复用或覆盖它**来表示 `factual` / `context`。第三条轴的最终字段名由 Pack successor changelist 决定；本设计只固定其逻辑名与语义，并明确它可与前两条轴独立组合（例如 `primary + factual`、`corroborating + context` 均合法）。

**6. 按 authority kind 建立封闭 tagged union**

不得把所有 factual support 写成同一种字段组合。四类权威的绑定要求不同（逻辑 authority kind 为 `topic_pack` / `financial_pack` / `evidence_note` / `external_snapshot`；新增的 `external_snapshot` 是**逻辑名**，最终字段名由 Pack/Writer successor changelist 决定，本设计只固定其逻辑名与语义）。

**该 union 是封闭的**：四类 kind 各自给出路径 A / 路径 B / context 三种支持形态的**必需字段（required）**、**可选字段（optional）**与**禁止字段（forbidden）**。只写原则句不构成闭合；任何 field 组合不在下表之内即 fail-closed。下表为**语义要求**，最终字段名由 Pack/Writer successor changelist 固定，但不得删减 required 或放宽 forbidden。

**所有合法 edge 的共同 required 字段（对四类 kind 一律适用）**：stage-appropriate identity、target identity、`authority_kind`、authority container identity、source/provenance identity、`support_role`、`support_semantics`、authorization basis、citation identity 与 content/dependency fingerprint。proposal 阶段的 authorization basis 只能声明封闭枚举（`path_a_prevalidated` / `path_b_material_derived` / `context_only`）及其来源字段，**不得**引用未来决定；accepted 阶段才引用决定。缺任一共同字段即 fail-closed。

**先分离两类身份（对四类 kind 一律适用）**：
- **authority container identity** 指承载权威的容器对象身份（Pack / `FinancialFactPack` / `FinancialSnapshot` / Evidence note artifact / `ExternalSnapshot` 记录）；
- **source/provenance identity** 指底层来源的身份（原始文档 / Evidence / 外部 URL 与正文 hash / SourcePolicy 与日期）。
- 二者必须**分别**出现在 identity 与 disposition 中；**不得让一个 `ExternalSnapshot` 同时冒充容器身份与事实身份**。

| authority kind | 路径 A：required / optional / forbidden | 路径 B：required / optional / forbidden | context：required / optional / forbidden |
|---|---|---|---|
| `topic_pack` | **路径 A branch-specific required**：qualified `fact_id`/`SupportedFact`、Pack content fingerprint、`ResearchMaterial`/payload/exact locator。**optional**：corroborating material。**forbidden**：以整块 `Evidence` 或 material ID 冒充 fact；无 locator | **路径 B branch-specific required**：exact `ResearchMaterial`/payload/locator；authorization basis=`path_b_material_derived`。**forbidden**：高风险硬事实；伪造 `fact_id`；伪装成 context | **context branch-specific required**：真实 `ResearchMaterial`/payload/locator；authorization basis=`context_only`。**forbidden**：fact identity、`ClaimEntailmentDecision`、授权 factual 或引入新数字/实体/期间/因果/结论 |
| `financial_pack` | **路径 A branch-specific required**：`financial_fact_id`、`FinancialFactPack`/`FinancialSnapshot` payload/locator、金额/单位/期间/scope 校验。**optional**：corroborating 附注。**forbidden**：伪造 `material_id`；把财务数字挂到 Topic Pack material | **非法**；若文本已正式采纳为 Pack `ResearchMaterial`，改以 `topic_pack` 路径 B 表达并保留财务 provenance | **context branch-specific required**：财务 payload/locator。**forbidden**：`material_id`、fact identity、`ClaimEntailmentDecision`、授权 factual |
| `evidence_note` | **路径 A branch-specific required**：经校验的 `note_fact_id`、note artifact/Evidence payload 与真实 locator。**optional**：同 note 相邻 locator。**forbidden**：虚构 Topic Pack material | **非法**；若文本已正式采纳为 Pack `ResearchMaterial`，改以 `topic_pack` 路径 B 表达并保留 note provenance | **context branch-specific required**：note artifact/Evidence payload 与真实 locator。**forbidden**：Topic Pack material、fact identity、`ClaimEntailmentDecision`、授权 factual |
| `external_snapshot` | **路径 A branch-specific required**：正式 `external_fact_id`/对应资格决定 + `ExternalSnapshot` container ID + URL/body hash/SourcePolicy/发布与检索日期/exact locator。**forbidden**：让 snapshot 单独授权；用 snippet/URL 冒充已抓取正文 | **非法**；若正文已正式采纳为 Pack `ResearchMaterial`，改以 `topic_pack` 路径 B 表达并保留 external provenance | **context branch-specific required**：属于当前允许输入集的 snapshot ID + URL/body hash/SourcePolicy/日期/exact locator。**forbidden**：external fact identity、`ClaimEntailmentDecision`、授权 factual；缺正文或不在允许输入集 |

`ExternalSnapshot` 是**不可变来源载体（source carrier）**，**不是事实权威**：它本身不得单独授权任何原子断言。正式逻辑对象 `ExternalFact` 必须绑定 `external_fact_id`、资格决定、snapshot ID/body hash、SourcePolicy、日期、命题字段与 exact locator，并由 Pack/current authority input 持久化引用。`ExternalFact` 及其 snapshot provenance **必须进入 disposition/输入/输出联合**，不得在具体字段表中消失。

- **`ClaimSupportRef` 是 `ProposedSupportRef | AcceptedSupportBinding` 的兼容逻辑联合，不是第三个可独立实例化的 wire 对象**；两阶段的唯一字段清单以 `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §6.3 为准。proposal 只能引用当前 subject 与来源字段，accepted 才引用决定；不得出现“一条记录同时拥有 proposal/accepted 两种 ID”的混合态。
- `context support` 可以没有 fact identity，也不要求 `ClaimEntailmentDecision`；它必须以 `narrative_draft_unit_id` 为 proposal target，并按所属 authority 分支绑定真实 container、source/provenance、payload 与 locator。它只能支持背景、结构、衔接与非事实性表达，**永远不能**以 `ClaimCandidate`/`SectionClaim` 为 target、授权 factual Claim，或引入新数字、实体、期间、因果、结论。
- **某种 authority kind 不存在合法的 context 定位方式时，该组合显式非法**；不得为了满足字段而伪造 Topic material、伪造 locator 或降级为自由文本理由。

**角色缺失、歧义、字段组合不符合所属 authority kind、authorization basis 缺失，或 context 被用于授权 factual Claim，一律 fail-closed。**

**7. 写作侧材料集合恒等式与两类可持久化 disposition**

```text
available  = VerifiedPackSet 中全部 current Pack.materials 的精确并集
processed  = available                 （成功完成 Writer 输入处理时）
used ∩ not_used = ∅
used ∪ not_used = available
```

- 上述方程只属于 **Writer/Section 侧的 `WriterMaterialProcessingDisposition`**；它不定义研究侧 `ResearchMaterialDisposition`。`used` 可以是 `available` 的**子集**；`processed` 表示 available 已被成功处理，每个成员都必须有确定性处理状态，不得静默遗漏。
- `not_used` **合法，不等于 gap**。
- **gap/block 与材料 disposition 是正交轴**：同一材料可以既是 `not_used`，又因 Contract 必需事实未取得而产生 gap；反过来，材料被采用也不消除 Contract gap。
- 两类 disposition 都必须是**版本化**正式对象，但语义不得混用：研究/Pack 侧 `ResearchMaterialDisposition` 只记录材料准入/保留、source/authority validation、aspect association、provenance 与研究政策证明；它**不**裁决 fact eligibility，也不表达 Writer 的 `used/not_used`。写作侧 `WriterMaterialProcessingDisposition` 才记录 manifest 成员的 processed/used/not_used、factual/context usage、typed not-used reason、proof/policy 与 partition/binding references。二者不得共用含混 identity，也都不是 gap（字段见 §5.4.1）。

**8. role-aware entailment，取代含混的“被 Claims/materials 蕴含”**

“句子被 Claims/materials 蕴含”可能被读成 OR（材料也能证明事实），必须改为按角色判定：

- 每个**事实性原子**必须由合格 Claim/fact 及其 **factual support edge** 蕴含；
- **context material 只能**验证背景、结构、衔接与“未新增事实”；
- **context material 不能补足缺失的合格 fact**；
- 一个自然句可以绑定**多条** Claims；
- 修改 sentence text 而 bindings 不变，或修改 bindings 而 text 不变，都必须改变身份并触发重新核验；
- Writer **不得自评**；由**独立只读**的 Section Evaluator 重新核验事实原子、Claims、support edges 与 context 使用边界（§11.2）。

正反例见 §5.5 与实施计划 §6.7.1。

**9. dependency fingerprint 与 content identity 不得混淆**

- `qualification policy version`、schema/runtime/policy 版本属于 **dependency fingerprint**；
- `FactCandidate`、`FactQualificationDecision`、资格结果与材料 disposition 属于 **Pack/content identity**；
- **不得**写成”资格决定本身进入 dependency fingerprint”——进入指纹的是**资格政策的版本**，不是资格决定本身。
- **dependency/content identity 分三套，不得合并为一个命名空间**。此三套与 §0.13 第 4.1 条的四类持久化边界是**不同轴的划分，不是一一对应关系**：
  1. **Pack qualification**：政策版本、schema/runtime 版本、research prompt 版本 → dependency fingerprint；候选、资格决定、资格结果、`ResearchMaterialDisposition`、research gap/conflict/not_found → content identity。
  2. **Section/Writer/Evaluator**：`SectionWritingSpec`/`ReportPresentationProfile` 版本、Writer/Evaluator prompt 版本、model 版本、entailment rubric 版本、**Claim Binding Gate 规则/政策版本** → dependency fingerprint；material-context manifest、`WriterMaterialProcessingDisposition`、`FactNarrativeDisposition`、`ClaimCandidate`、`NarrativeDraftUnit`、`ProposedSupportRef`（proposed support edges）、`ClaimBindingDecision`、`ClaimEntailmentDecision`、`AcceptedSupportBinding`、`SectionClaim`、Narrative、`Unresolved` → content identity。
  3. **Review/Assurance**：Reviewer prompt/model/rubric 版本与 Controller 规则版本 → dependency fingerprint；绑定 `report_version` 的 `ReviewIssue[]` 与聚合状态 → content identity。
- **`FollowUpNeed` 位于这三套 content identity 之外**：它是独立的运行请求/trace 身份；Harness 执行一个 `FollowUpNeed` 生成的是**新的不可变 Pack**，即以 ① 的新内容身份出现，而不是给 `FollowUpNeed` 本身一个内容身份。
- **政策、prompt、model、rubric、gate 规则任一变化必须使对应那一套的旧决定失效**（其余两套不因此自动失效，也不得被静默沿用）：旧资格决定在新资格政策下不得被静默重解释；旧 `ClaimEntailmentDecision` 在 Writer/Evaluator prompt、model 或 rubric 变化后不得继续放行；旧 `ClaimBindingDecision` 在 Claim Binding Gate 规则/政策版本变化后不得继续作为放行依据；旧 `ReviewIssue[]` 不得放行新 `report_version`。
- **跨集传播（不是跨集删除或改写）**：上游 current identity 变化后，历史对象**不被删除、不被改写**；依赖旧输入的下游对象只是**失去 current eligibility**——它们仍是合法历史记录，但不得被当前链路当作已接受依据继续使用，必须重新取得对应决定。

以上各条只纠偏“材料如何进入事实与叙述”的口径，不改变已冻结的 Contract、SourcePolicy、WritingSpec 与 PresentationProfile，也不改变 §0.11 的三轴分工。

材料保留与事实资格是**两条不同的轴**：材料轴回答“来源里客观存在什么、是否完整可回查”，资格轴回答“哪一条候选可独立判断真假、可否单独晋级为事实”。Pack 必须同时保留完整材料、候选事实、资格裁决与 gap。本节只定义口径；当前 M930-3.2 独立 gate 侧仍有 3 个未关闭旁路（登记见 `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §6.2.2），**本节不构成任何里程碑关闭声明**，TS5 与正式树门状态不因本裁决改变。

**superseded 范围（2026-09-21）**：本节当时记录的口径中，第 3 条“两类支撑语义”内“资格必须在研究阶段**一次判定**、写作阶段只消费判定结果”一句，以及由此推出的“Writer 不得提出任何候选”读法，已被 **§0.13 写作主链裁决**取代——研究侧资格改为**非穷尽**的高风险事实预验证，写作侧新增 `ClaimCandidate` 候选通道。同一轮窄修另已 supersede 本节以下措辞：路径 A 曾写作“高风险硬事实**专用**”（现为“任何具有合法预验证事实身份的原子断言均可用；高风险硬事实只能用路径 A”）；曾要求“单个混合 Claim 内逐原子绑定”（现为“一个 `ClaimCandidate` 只表达一个原子断言，混合句必须拆成多个 `ClaimCandidate`”）；曾写作“四个边界与三套 identity 一一对应”（现为“不同轴的划分，不是一一对应”）；`ClaimEntailment/AcceptanceDecision` 统一改称 `ClaimEntailmentDecision`；`MaterialProcessingDisposition` 拆分为 `ResearchMaterialDisposition` 与 `WriterMaterialProcessingDisposition`。本节其余各条（材料保留与事实资格双轴、正交支撑轴、封闭 authority tagged union、材料集合恒等式与可持久化 disposition、role-aware entailment、fingerprint 与 content identity 分工）继续有效，**不改写本节当时记录**。

### 0.13 2026-09-21 写作链历史裁决（原实现保留；写作侧已由 §0.20 取代）

本节记录此前唯一写作链的历史设计及当前工作区兼容对象；其写作侧在 2026-09-29 被 §0.20 取代，不能再作为新生产主线或 Demo 的必经门。研究侧非穷尽高风险事实预验证继续有效。Demo 轨与正式轨仍共用唯一 Harness/Pack 来源；Demo 只收窄 Contract 覆盖，不使用第二套研究运行时。

**1. 唯一主链**

```text
用户上传电子文档
  → 原始只读版本化存储
  → canonical PageLayout
  → EvidenceBlock（不可变审计/引用锚点）
  → 只读 DocumentOutline
  → OutlineSpan / TableObject 材料对象
  → typed 树/图关系
  → Contract required aspects
  → Worker / Harness 属主的研究
      （本地树内检索；关联表与显式引用；必要时有界 fallback；必要时外部抓取 + 不可变快照）
  → 权威 Pack
      （完整精确 ResearchMaterial；**研究侧预验证事实**进入 Pack；外部/财务权威；
        conflict / gap / not_found / searched scope；ResearchMaterialDisposition）
  → exact Writer material-context manifest
  → SectionDraft（门前对象，绝不引用 SectionResult 或任何未来决定）
      ClaimCandidate[]（每个候选只表达一个原子断言）
      + NarrativeDraftUnit[]（门前叙述单元，稳定 ID）
      + ProposedSupportRef[]（target 为 claim_candidate 或 narrative_draft_unit；
        不得引用尚未产生的 ClaimBindingDecision / ClaimEntailmentDecision）
  → 确定性 Claim Binding Gate（机械门）
      → 每个 binding subject revision 恰好一个聚合 ClaimBindingDecision
        （覆盖该 subject 的完整、有序 proposal set 与逐 edge 结果）
      ├─ factual ClaimCandidate → Section Evaluator（原子蕴含）
      │    → 每个 candidate revision 恰好一个 ClaimEntailmentDecision
      │    → factual AcceptedSupportBinding[] → 已接受 SectionClaim
      └─ context NarrativeDraftUnit → context AcceptedSupportBinding[]
           （不进入 Claim entailment，不产生 ClaimEntailmentDecision）
  → 最终 NarrativeSentence / Paragraph / Table / Unresolved
      （绑定已接受 SectionClaim IDs 与 context AcceptedSupportBinding IDs）
  → SectionResult（门后对象，单向引用 section_draft_id）
  → 确定性组装
  → 隔离只读 Independent Review Agent
  → 确定性 Assurance Controller
  → 独立人工验收
```

**该链是严格无环的**：后一对象可引用前一对象，**前一对象不得引用后一对象**，也**不得把自身或后继对象的 ID 计入自身 content identity**。绑定关系为：

| 对象 | 自身 content identity 的输入 | 不得包含 |
|---|---|---|
| `ClaimCandidate` | schema/version、规范化原子候选文本/类型、task/section/company/`report_as_of`/Contract、draft revision | 任何 support/material/payload/locator/citation、任何决定 ID |
| `NarrativeDraftUnit` | schema/version、门前叙述文本/类型、task/section、draft revision | 最终 Narrative ID、`SectionResult` ID、任何决定 ID |
| `ProposedSupportRef` | `binding_subject_kind + binding_subject_id`、authority kind/container identity、source/provenance identity、分支适用的 fact/material/payload/locator/citation、`support_role`、`support_semantics`、声明式 authorization-path 枚举、manifest/fingerprint | accepted ID、`ClaimBindingDecision` / `ClaimEntailmentDecision`、`SectionClaim`、最终 Narrative ID |
| `ClaimBindingDecision` | `binding_subject_kind + binding_subject_id + draft_revision`、**完整有序 proposal IDs/hashes 集合及 digest**、逐 edge 机械结果、manifest identity、机械门规则版本、聚合结果 | 自身 ID、`ClaimEntailmentDecision`、`AcceptedSupportBinding`、`SectionClaim`、最终 Narrative ID |
| `ClaimEntailmentDecision` | factual candidate ID/revision、**通过的聚合 `ClaimBindingDecision` ID**、相同 support-set digest、指定材料/权威集合、rubric/prompt/model 版本、语义结果 | 自身 ID、`AcceptedSupportBinding`、`SectionClaim`、Narrative |
| `AcceptedSupportBinding` | proposal ID、对应 subject、通过的聚合 binding decision；factual 分支另含 entailment decision，context 分支明确禁止 entailment decision | 自身 ID、未来 `SectionClaim`/Narrative ID |
| `SectionClaim` / final Narrative | `SectionClaim` 引用 factual accepted-binding IDs；final Narrative 引用 accepted SectionClaim IDs 与 context accepted-binding IDs | 反向写入前序对象；自身 ID 进入自身 content identity |

context support **既不要求 fact ID，也不要求 `ClaimEntailmentDecision`**；它的 subject 必须是 `NarrativeDraftUnit`，不得以 `ClaimCandidate`/`SectionClaim` 为 target，也不得作为任何 factual 授权依据。

候选正文文本可以作为**显式标记的草稿/预览**被持久化，但在相应门通过前不得被表述为已审查、可发布或最终接受。

**2. 高风险事实预验证（通道 A）与非穷尽性**

必须在研究侧预先验证的**高风险硬事实**至少包括：金额、比率、日期、期间、币种、单位、财务数字、授信额度金额及其语义类型、勾选状态、表行/列关系、法律实体、明确否定事实，以及其他 Contract 要求的预验证硬事实。研究侧事实资格是**非穷尽**的：它**不负责**预先拆完整篇文档，也不因为某段材料未产生预验证事实就视为材料不可用。

**3. 写作侧候选（通道 B）与四条禁止**

Writer 从**完整材料**提出描述性 `ClaimCandidate` 与独立的 `ProposedSupportRef[]`。`ClaimCandidate` 身份只包含原子命题及 task/section/company/`report_as_of`/Contract/draft revision，**不包含** material/locator；精确 authority/material/payload/locator 只属于 proposal。该候选走 **factual support 的材料派生路径（路径 B）**，**不得伪装成 context**，且必须绑定 exact material/payload/locator、聚合机械 `ClaimBindingDecision` 与独立版本化 `ClaimEntailmentDecision`。Writer **不得**：自行批准候选；回写或污染历史 Pack；绕过预验证引入新的数字、日期、币种、勾选、明确否定等硬事实；自己生成权威；自评通过。

**禁止的准确范围（避免把通道 B 封死）**：禁止的是"**捏造**材料或权威中不存在的事实"与"**绕过预验证引入高风险硬事实**"，**不是**"Writer 不得提出任何新表述/实体/结论"。由指定材料原子蕴含的描述性候选是通道 B 的正当产物；把它写成"Writer 不得新增任何事实"会与本节第 1 条主链自相矛盾。

**原子模型（取代逐原子绑定的旧表述）**：**一个 `ClaimCandidate` 只能表达一个原子断言**。包含多个断言的混合自然句**必须拆成多个 `ClaimCandidate`**，每个高风险原子各自独立走路径 A、每个描述性原子各自独立走其合法路径——**不得**在 wire 无 atom ID 的前提下宣称"单个混合 Claim 内逐原子绑定"（§0.12 真值表与原子性规则）。一个 `NarrativeSentence` 可以绑定**多个**已接受 `SectionClaim`。**不新增 `ClaimAtom` 体系**，除非只读审计证明现有 atomic Claim 约束确实无法表达该规则。

**4. 身份分离**

```text
ResearchMaterial  ≠  FactCandidate / SupportedFact  ≠  ClaimCandidate / SectionClaim
```

研究侧 `FactQualificationDecision` 与写作侧 claim 蕴含/接受决定是**不同身份、不同持久化对象**。纯算法可以复用，**wire 不得混用**。

**4.1 四类持久化边界（不得合并成一个 successor）**

```text
① Research/Pack successor（研究侧，唯一权威 Pack 链）
     ResearchMaterial 与 ResearchMaterialDisposition、FactCandidate、
     FactQualificationDecision、SupportedFact、ExternalFact、外部快照/财务权威、
     research gap / conflict / not_found、来源身份与 provenance

② Writer/Section/Narrative successor（写作侧，独立持久化域）
     exact material-context manifest、WriterMaterialProcessingDisposition、
     FactNarrativeDisposition、ClaimCandidate、NarrativeDraftUnit、
     ProposedSupportRef（proposed support edges）、ClaimBindingDecision、
     ClaimEntailmentDecision、AcceptedSupportBinding、SectionClaim、
     Narrative、Unresolved

③ FollowUpNeed（独立的运行请求/trace 对象）
     不是 Pack 内容，也不是写作侧产物；Harness 执行后生成**新的不可变 Pack**，
     旧 Pack 不回写、不追加、不重标

④ Review/Assurance（报告版本绑定）
     绑定 report_version 的 ReviewIssue[] 与聚合状态
```

- **不得**再写成"同一个 Pack successor 保存 `ClaimCandidate`、`SectionClaim`、`FollowUpNeed`"。Pack successor 只承载 ①。
- **写作侧候选接受结果不得计入 Pack content identity**：`pack_id`/Pack content fingerprint 不因 `ClaimCandidate` 被接受、被拒绝或正文改写而变化；Pack 也不因 Writer 运行而被追加写入。
- ② 可以与 ① 位于同一物理 Store 或同一 repository 的 migration 序列中，但必须是**独立对象、独立版本轴、独立 content identity**；不得共用一个含混的"successor"名称掩盖边界。
- **`ResearchMaterialDisposition`（①）与 `WriterMaterialProcessingDisposition`（②）是两个不同身份**：前者只记录研究侧材料的 admission/retention、source/authority validation、aspect association、provenance 与研究政策证明，**不裁决 fact eligibility，也不表达 used/not_used**；后者才记录同一批 manifest 成员在写作侧的 processed/used/not_used、factual/context usage、typed reason 与证明。`FactNarrativeDisposition` 是第三种写作侧对象，记录预验证 authority fact 是 `claimed`、`supporting_only` 还是 `not_presented_with_reason`。三者不得共用含混 identity，也不得互相冒充。
- ③ 由 Harness 拥有；Writer 只能发出请求，不能读写其持久化结果或据此改写历史 Pack。
- ④ 绑定 `report_version`；新版本使旧 `ReviewIssue[]` 与聚合状态失效（§11.2）。

**4.2 三套 dependency/content identity（与上述四个边界不是一一对应）**

见 §0.12 第 9 条：Pack qualification、Section/Writer/Evaluator、Review/Assurance 三套指纹与内容身份**分开管理**；政策、prompt、model、rubric、**Claim Binding Gate 规则/政策版本**变化只使对应那一套的旧决定失效，不得跨套静默复用，也不得因一套变化就连带作废另一套的合法历史记录。**`FollowUpNeed` 不属于这三套中的任何一套**，它是独立的运行请求/trace 身份；Harness 执行它产生的是一个新 Pack（进入 ① 的内容身份）。**四个持久化边界与三套 identity 是不同轴的划分，不得写成一一对应。**

**5. 确定性 Claim Binding Gate（机械门）**

Binding Gate 不做长文语义判断，**不调用 review LLM**。它核对：schema/version/fingerprint（含**机械门规则/政策版本**）；task/section/company/`report_as_of`/Contract；exact Pack 与 material-context manifest identity；`binding_subject_kind`/subject ID/draft revision；该 subject 的**完整、有序 proposal IDs/hashes 集合及 digest**；`authority_kind` 与 container/source-provenance；`support_role`；`support_semantics`；声明式 authorization path；ID/hash/payload/locator；**每条** citation/support edge 独立绑定；数字/单位/期间/币种；高风险 Claim 是否绑定合法预验证权威；文本或 bindings 任一改变是否产生新身份并触发重核验。缺失、重复或多出的 proposal 均拒绝；任一 edge 失败，聚合决定失败。**不得**用 `fact_id is None` 推断 support semantics。

它对每个 `(binding_subject_kind, binding_subject_id, draft_revision)` **恰好输出一个聚合 `ClaimBindingDecision`**，绑定完整 support-set digest、逐 edge 结果、manifest identity、机械门规则版本与聚合结果（通过/不通过 + 失败码）。同一 subject/revision 多个决定、只覆盖部分 proposal 或额外覆盖未知 proposal 均 fail-closed。

**`ClaimBindingDecision` 只证明机械绑定通过，不能冒充语义蕴含决定。** 它的输出是绑定成立/不成立及失败码；它**不得**包含"材料是否真的支持该断言"的语义判断，也**不得**被下游当作接受依据。语义蕴含由第 6 条的独立版本化 `ClaimEntailmentDecision` 回答；两者是不同身份、不同持久化对象，缺任一个都不得进入正式 `SectionClaim`。

**6. Section Evaluator 是 Claim 级原子语义核验的唯一位置**

不再设重复的 Claim Reviewer Agent。**Evaluator 的输入是 SectionDraft 中的 factual `ClaimCandidate` subjects 及其完整 `ProposedSupportRef[]`，不是 `SectionResult`**——`SectionResult`/`SectionClaim` 只在通过之后才形成。Evaluator 只接收具有**唯一、通过的聚合 `ClaimBindingDecision`**且 support-set digest 完全相同的 candidate revision；context `NarrativeDraftUnit` 不进入 Claim entailment。Evaluator 与 Writer分离调用/上下文，职责为：候选是否被指定权威/材料集合原子蕴含；主体/期间/范围/极性是否误读；是否过度概括或改变条件；Contract 覆盖/必需缺口；结构、重复、信息密度、可读性与有界返工。通过才形成正式 `SectionClaim`；不通过只允许有界返工、降级为非事实过渡、unresolved 或 Contract gap/block。Evaluator 不重复 Binding Gate 的 ID/hash/locator 检查。任何 LLM 辅助蕴含必须隔离、版本化，只读取候选、完整指定材料/权威及 support set，只输出结构化决定，不改写、不宣称全报告通过。

Evaluator 对每个 factual `(claim_candidate_id, draft_revision)` **恰好输出一个独立、版本化的 `ClaimEntailmentDecision`**：它绑定唯一通过的聚合 `ClaimBindingDecision` ID、相同 support-set digest、指定权威/材料集合、rubric/prompt/model 版本与语义结果；它不复用机械决定身份，也不写入 Pack。路径 B 以它作为授权依据；路径 A 仍须经过该语义核验，但事实授权来自预验证权威。通过后，每条 factual proposal 形成一个引用两道决定的 `AcceptedSupportBinding`，随后 `SectionClaim` 引用这些 accepted-binding IDs。context proposal 只形成引用机械决定的 context `AcceptedSupportBinding`，**不得**有 entailment decision，也不得引用未来 Narrative ID。任何 accepted binding/claim 的 content identity 均不得包含自身 ID 或未来 Narrative 身份。

**7. Independent Reviewer 与 Assurance Controller**

Reviewer 在章节定稿与确定性组装之后运行，隔离只读，只输出 `ReviewIssue[]`；检查合并多个正确 Claim 时的含义变化、选择性使用材料、遗漏关键限制、局部到整体外推、相关写成因果、夸大优势/风险、跨 Claim/章级矛盾，以及整体是否可能误导信贷人员。它**不得**改正文、补研究、联网、重复逐条 hash/locator 门、覆盖 hard failure、自行放行。Controller 只做确定性聚合：hard gates → Section/Evaluator 结果 → `ReviewIssue[]` → system-Assurance 状态，并保持流程完成 / 预览可用 / system-Assurance / 人工最终接受 / 正式阶段关闭彼此分离。

**8. 表的三条流向**

见 §5.4.1 与 `AGENTS.md` §4：财务主表/附注数字表/需计算表保留 `TableObject` 读取与检索视图并另走结构化数字权威、Decimal 计算与口径冲突检查（LLM 不算数，`TableObject ≠ FinancialSnapshot`）；业务表作为结构化写作材料、不强制进入计算层；勾选/披露模板/版式表形成 typed 表单/选择状态或 gap，表头/选项/单位/版式残片不得升格为公司事实。表格的读取视图与数字视图共享来源身份与 locator。

**9. 拒绝与缺口解耦**

**拒绝与缺口是两条独立记录，不得二选一。**

1. **记录一（必然产生）**：`FactCandidate` 或 `ClaimCandidate` 被拒绝时，**必然**形成 typed、versioned rejection decision / audit（类型化、版本化、可审计，理由码封闭，绑定被拒候选身份与其原材料身份）。**拒绝决定不得被 gap 取代**，也**不得每拒绝一个候选就自动制造一个 gap**。
2. **记录二（条件产生）**：只有当**因该拒绝导致 Contract 要求的 aspect/fact 仍然未被满足**时，才**另外**产生 ContractGap/Block。它与记录一各自独立、各自绑定合同依据，不是同一条记录的另一种写法。
3. 材料未被采用（`not_used`）本身**不构成 gap**（§0.12 第 7 条）。

拒绝决定永远**不得**删除、裁碎、改写或降级原材料；也不得把拒绝理由写进 `semantic_tags`、gap `detail` 自由文本或泛化 `validator` 字符串。**不得**使用 `rejected → reason/gap/audit`、"qualification decision 或 gap"、"audit/gap 去向"等把二者写成可二选一的措辞。

**10. 本轮性质**

本节只同步文档口径。无环提案→决定→接受链、双通道事实授权真值表、封闭 authority tagged union、四类持久化边界、三套 dependency/content identity、`FollowUpNeed` 独立身份与拒绝/缺口两记录规则**均为已写入文档的规则**；**勘误（2026-09-27）**：高风险事实预验证、Writer `ClaimCandidate`、`ProposedSupportRef`、Claim Binding Gate、`ClaimBindingDecision`、`ClaimEntailmentDecision`、`AcceptedSupportBinding`、Section Evaluator 原子蕴含、`FollowUpNeed`、Narrative/Section successor 与 Reviewer/Controller 接口**在工作区已有实现（未提交、未评审）**，但**真实纵链未复验**——工作区有实现不等于代码已在真实创建式运行里成立。M930-3 未关闭、M930-3.2 三个独立 gate P1 **按当前 wire 逐条仍未修复**（这是「三条旁路仍在」，不是「已全修复」）、TS5 no-go/未 seal/未关闭、M930-4/5 未开始、正式 Phase 5/6 未进入。**写规则进文档不等于代码已实现；工作区已有实现不等于真实纵链已复验，也不等于任何里程碑关闭。**

**11. 三条日期轴与最终句语义门（2026-09-27 定点澄清）**

**(1) 三条日期轴必须分开**（与 §3.1 第 13 条同源，此处给出链上的落点）：

| 轴 | 含义 | 谁能写 | 唯一真值来源 |
|---|---|---|---|
| (a) **事实适用期** | 断言何时成立（`2025年度` / `截至2025年12月31日`） | 路径 A 权威事实自己的文本，或路径 B 候选所绑定材料的**逐字**原文 | 预验证权威事实 / 精确材料正文。**不得**推测、**不得**由任何旁路日期字段回填 |
| (b) **来源归属** | 材料身份 / 版本 / 页码 / **可核实披露日** | **系统**确定性渲染（作者不手写） | 来源清单登记的四轴身份与 `DisclosureDateState`（`verified` 才给日；否则 `unknown`） |
| (c) **报告生成日** | `report_as_of` | 系统时钟 | `harness/report_clock.py` 的单次采样 |

三条轴**互不顶替**：入库时间、PDF 元数据、财务期末一律不得冒充披露日；`report_as_of` 既不是事实适用期也不是披露日。**非数值的一般经营描述**（产品、业务模式、采购/生产/销售方式）**不要求**每句机械重复“2025年度”，但**不得**写成“截至报告生成日仍然如此”或“一直如此”；只由旧材料支持的内容保留历史来源归属或留缺口，新旧材料实质差异不得被相似文本抹平（该轴由 `sections/source_role_scope.py` 的 `srsc-*` 与 O-12 承担）。**新闻/外部事件**优先使用有依据的事件发生/生效日；只有发布日时只能写“某日发布的报道提及……”，不得把发布日冒充事件日。

**(2) 最终句语义门（裁决 = B，2026-09-27）**：定稿前，**每一条承载事实的最终句**（含 `natural` 与 `composed`）必须由一个**独立、版本化**的决定核验：逐**事实原子**记录「句子里的这个原子 ← 哪条已接受 Claim ← 哪些 factual `AcceptedSupportBinding` ← 哪份精确来源」，并给出 `entailed` / `not_entailed` / `atom_not_located`。**不能**因为 Claim 原文逐字在场就放过连接语新增的因果、时间、范围或结论。新增映射、遗漏映射、无支撑、主体或指标替换、期间/否定/范围改变、代理口径丢失一律拒绝。决定按 append-only 迁移、读回可复算、历史只读，且**只引用** `sentence_id`（方向不反转）。**表面比较器 `natfid-1` 不能冒充本门**：它是只读表面读数、不产生任何决定身份、通过结论不落任何对象。

**状态勘误（2026-09-27，与 §12.4.5 同一条纪律）**：本门已按上述裁决**编码**（wire `nsfid-1` / rubric `nsfr-1` / prompt `final_sentence_fidelity_v1@nsfr-1`；缺失 / 重复 / 拒绝 / 过期四档各自阻断，只读展示见 §12.4.4 第 6 步），但**只经离线重放与桩调用验证，尚未在真实 run 上运行过**，因此「B 落地」这句话在真实链上**不成立**。真实 run 的正文在此之前仍只受 `natfid-1` 保护；任何真实-run 授权文本与验收文本都必须写明这一点，不得据离线全绿读成「语义门已覆盖」。

### 0.14 2026-09-24 跨文档联合检索（已确认，现行）

本节确认**源集（source set）是一等身份**，并确认逐来源责任、未命中终态与冲突口径。它是 [TREE_STRUCTURE_IMPLEMENTATION_PLAN.md](TREE_STRUCTURE_IMPLEMENTATION_PLAN.md) §7.2/§7.4 已登记缺口（「一个 aspect 的材料库如何从**多个树、多个文档、多个年份**中形成」「可跨**年报、募集说明书**及其他本地权威文档聚合」）在 Demo 轨 M930-3 链上的落地口径。**本节只同步文档口径，不声称任何阶段关闭**；实现细节见 `CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md`。

**1. 源集与同类判定（取代「单一 primary document」）**

- 同类 = **同一发行主体 + 同一文档系列 + 仅年份/期间不同**；三者缺一即不同类。源集先从**全部 eligible 材料**中按可核实的内容报告期间与现有稳定排序键选**恰一个全局当前兼容锚**，不限文档类型；同系列较旧者保留供历史/变化/冲突核对，其他系列成员按主题参与检索。这个锚只是旧单文档接口的兼容读视图，**不裁定**任一事实的期间或权威。不得仅凭文件名、路径、入库时间或未知披露日期断定材料新旧。
- 不同文档类型的用户上传材料（如募集说明书）是**不同类型但同等检索资格**的源集成员，不得因类型不同落「未选中」；只有一份可核实期间的募集说明书时，它可以是全局当前锚，不能因没有年报而直接阻断。
- 注册类型与内容识别不一致**保留 typed 审计、原样呈现**，**不得**擅改数据库、也不得写成 gap 或塞进自由文本。
- 期间不可核实不能凭猜测定谁新。只有**暂定全局锚自身的同系列组**存在未知期间成员、使该锚无法证明为组内最新者，才记 `ambiguous_current_state` 并不准入；其他系列的未知期间成员**不**清空已可核实的锚。若全源集都无可核实期间，则记 `no_eligible_current`。全局锚恰一，不能每系列各选一个；未准入时不伪造证明域或 partial Pack。
- **无法确定当前来源时，在检索之前就如实停**：准入闸先于任何要求非空源集的会话/上下文构造，发出 typed 阻塞（`ambiguous_current_state` / `empty_source_set` / `no_eligible_current`），零树调用、零 Pack 提交。对**现有源集成员**派生逐 aspect × 逐来源责任；空源集没有这种配对，另记每 aspect 的阻塞与 gap。结果是独立、可读回的 blocked/partial 运行状态，**不是**伪造的 partial Pack；缺正式 Pack 的 Writer 必须 blocked。不得暗中挑一份充当当前来源。

**2. 三个独立判断与检索前定责（口径核心）**

「**纳入有序源集**」「**某栏目必须尝试检索该来源**」「**该来源必须为栏目完整性提供证明**」是**三个不同判断**。每个 aspect 在**检索之前**依 Contract、时间口径、来源角色与 SourcePolicy 定**逐来源责任**并记录 typed 理由；**不得**看见检索结果后倒填责任（责任记录的指纹须可独立复算比对）。

- 当前状态栏目**不**因旧年报仅提供历史片段就自动要求旧年报的全集闭合；**Contract 明确要求跨期集合时**，才对相应期间逐份要求证明。
- **第一约束**：证明责任**先**受该 aspect 自身的集合完整性规则约束——`"set_complete" ∉ aspect.coverage_rules` ⇒ 证明责任**恒为 false**，普通事实栏目**不得**被派集合枚举证明责任。
- **契约能力现状（如实记录，不得推成「已判定为否」）**：`EvidenceAuthorityPolicy` 是封闭结构，能表达来源类/等级/kind 与 `supplemental_only`，**不能**表达文档、期间或「跨期集合」。对**处于该 aspect 必查范围、且非 `supplemental_only` 的非当前状态来源**，证明责任因此无法确定；必查范围外或 `supplemental_only` 的成员仍可明确为 `false`。**不得**把「尚无映射」推成「仅当前来源必然为真」，也不得因源集有多份就把所有非当前成员一律判未决。
- **证明责任是三值（`true` / `false` / `undetermined`），不是布尔**：`undetermined` 是**一等结论**，必须原样表达。**只要该 aspect 存在任一 `undetermined` 边，该 aspect 一律不得 `set_complete`**，如实留 partial 与明确 gap，且理由码必须与「缺证明」**分开记**（前者是契约表达能力缺口，后者是补齐证明即可）。**不得**把 `undetermined` 压成 `false`（那等于用一个不存在的映射支撑通过结论），也**不得**压成 `true`（那会要求无人能验证的证明）。扩展冻结契约超出本项授权。

**3. 未命中终态（不得伪造、不得静默、不得冒充「不存在」）**

对**已尝试但没有相关材料**的来源，新增可审计的「**在声明搜索范围内未命中**」终态：记录声明的搜索范围、真实调用与结果。它**不是**「该文档不存在该事实」的断言，也**不得**伪造非空 `EnumerationBoundaryProof`（该结构在 `violation()` 层已对空种子/空成员拒绝，理由码即「空证明缺口」）。

- 每个 `(aspect, 来源文档)` 恰好落入**四类终态**之一，并在 Pack 顶层有自己的记录：**有材料**（带真实材料 ID/定位）/ **已尝试未命中**（带该份真实调用与范围）/ **无需检索**（带检索前定下的 typed 理由）/ **要求检索但未履行**（带未履行原因，不得伪造调用或未命中）。后两类不得混淆。proof 键在包装记录上，proof 本体不变；**partial 有材料可尚无 proof**，但已有 proof 必须真且归属正确；只有宣称正式 `covered/set_complete` 时，责任要求证明的有材料来源才必须恰有一条 proof。
  - **「无需检索」与「要求检索但未履行」必须分开**：把后者读成前者，等于把**未履行的责任**从读回面上抹掉。
  - **逐份终态必须由该文档自己的证据支撑**：该份自己的成功调用、该次请求声明的搜索范围、该次返回的未读范围、**该次调用自己的**停止原因。**不得**复用整个 aspect 的最后停止原因，**不得**只凭文档身份回声判合格未命中（身份回声只证明**分派到了**，不证明**范围被覆盖**）。
  - **「尝试后无材料的观察」不得冒充合格未命中**：只有条件全齐（含范围已覆盖、无未读残留、未因预算停止）才可投影为「在声明范围内未命中」；否则只能是「尝试过但条件不齐」，读回时不得写成「已确认没有」。
  - **对不含集合完整性规则的普通栏目，未履行的检索同样不得静默略过**：审计、覆盖判断与「已查范围」表述三处都必须体现。
- **proof 若存在 ⇒ 对应有材料且责任明确为 `true`，并始终校验真实性**；正式宣称 `covered/set_complete` 时才增加反向要求（责任域内有材料者不得缺 proof）。无材料或责任未确定的来源不得造 proof；包装键纳入 Pack 内容身份，换键须重新验证，不能静默转移证明。
- **必需来源**未取得材料 ⇒ 留 Contract gap/partial；**补充来源**未命中 ⇒ 记账但**不得**被静默略过，也**不得**自动阻断当前状态的集合完整性。
- 未命中**不得**写成「未披露 / 没有 / 不存在」；与 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md:354` 的四分（已披露发生 / 明确披露未发生 / 在已列范围内未发现 / 来源范围不足）对齐，后两者**不得**写成确定不存在。

**4. 冲突与期间口径**

**不同期间首先就是两个期间的事实，不是冲突。** 2024 与 2025 对同一指标取值不同，两侧各自成立、可以同时为真，**不产生** `conflict`。

期间、口径、主体/事项的可比性分别为**已证可比 / 已证不相容 / 未确定**。三项已证可比、取值互斥且两侧事实资格均成立，才裁定正式 `conflict`；任一项已证不相容，两侧作为不同期间/口径陈述并存；没有已证不相容但有未确定项，写 Pack 顶层版本化 `SourceComparisonAudit.comparability_pending`，不假判冲突或已证并存；一侧资格未成立时的表面差异只写 `SourceComparisonAudit.observed_divergence`，不混进正式冲突。该比较审计必须逐侧可回查原句/值及 hash、来源/locator、三轴状态、待核项、规则版本与 typed 理由，Store 独立复核。**事实期间**未知使期间可比性待定；**披露日期**未知只禁止按披露先后判赢家，不妨碍用独立证据已确立的可比性及互斥值判冲突。入库时间不得冒充披露日期。

**不得**用 `source_role` 裁定。正式 `ResearchConflict.sides` 的每一侧都必须是可解析到本 Pack 合格 fact 的 `QualifiedFactSide`，`fact_ids` 与 sides 一一对应；观察侧不得混入正式冲突。`SourceComparisonAudit` 的观察陈述侧则必须解析到已持久化原句或数值及 payload hash、来源/locator、期间、资格状态和拒绝理由，不能只填一串自报 ID 或藏在自由文本里。旧版缺少这些字段的冲突只作 legacy 审计读回，不伪造新版两侧。`conflict` 仍**不是** gap。旧年报材料对 Writer 可达，但不要求据此编造变化叙述；Contract 要求且证据已确立的变化必须呈现。

**5. 实现约束（不变量）**

- 仍只有**一个** Harness、Router、ToolRegistry 与 `inspect_outline_materials` 工具名（`TOOL_NAMES` 11 个不变）；每份 PDF 各建有身份的树会话，工具按请求中的文档身份**精确分派**，未知/错主体/重复身份 **fail-closed**，**绝不退回默认文档**。
- 一次 topic 研究**共享**预算与 checkpoint；最终只提交**一个**跨源 Pack（`topic_current` 主键不加文档轴）；源集、逐来源尝试/未尝试/未命中、材料去向与跨源引用均进版本化身份与审计。Writer 消费**完整** Pack 材料集合；**不得**在验收 runner 拼接材料，**不得**另造第二套 Router/Pack/运行时。
- **正式 `set_complete` 不得假关闭**：当前运行时不生成正式 `SetCompletenessAssessment`、依赖束未接入生产 verifier，**仅补一个 verifier 参数不足以完成该链**；真实验收对尚无正式证明的栏目**保持 partial 与明确 gap**，**不得**用测试替身签发生产完整性结论。
- **「部分材料可保存」与「正式完整性证明」是两件事，不得互相冒充**：
  - **逐 aspect × 逐来源责任记录与四类终态记录必须独立持久化、可读回**，**不得**只挂在**可为 `None`** 的集合完整性评估对象里——否则一个未宣称完整的 Pack 就存不下真实材料与责任，等于把「形式未完整」误读成「材料不存在」。
- **正式完整性门只在宣称 `set_complete` 时才关**：未宣称完整的 aspect 不因**缺**正式 proof/verifier 而丢掉真实材料与事实；但责任与四臂穷尽、材料身份及**已有 proof 的真实性**在 partial 入库时照样校验。partial Pack 可供 Writer 使用，**不保证**可读预览：仍需新真实运行证明 Writer 未截断、候选获接纳、自然段有正确引用并经过人工内容审查。缺必要事实或 Writer 失败也可能内容 blocked；「材料已保存」「正式完整」「预览可用」分别报告。
  - **不得**为无材料来源造空 proof；键只能加在**包装记录**上，proof 本体与其反伪造校验不变。
  - 尚未取得的必要事实仍必须**另外**形成 gap/block；`not_used` **不是** gap。冲突、未命中、失败、拒绝各有自己的记录，**不混用**。

**6. 本轮性质**

本节为**已确认口径**；跨文档源集、逐来源责任、**四类终态**、逐份证据的持久化形状、typed 冲突两侧与研究前准入闸的代码**已在工作区有实现（未提交、未评审），真实纵链未复验**（结果回显文档身份一项已取消：改为复用树工具既有返回体中的真实身份）。M930-3 仍未关闭；TS5 仍 no-go/未 seal；M930-4/5 未开始；正式 Phase 5/6 未进入。

**真实调用的归属与事前准入（2026-09-24 补充）**：研究与写作共用传输、分两条预算轴。调用必须在**进入可能启动研究的环境之前**就受同一个事前门控制；研究按 topic、写作按节分别计量，每一次 provider 尝试（含显式重试与失败）先归属、计数、有限放行，SDK 不得隐式重试绕账。写作总额只数写作轴，研究另有独立上限；二者不能相互消耗或冒充全链总额。单次同文档广覆盖检索可服务多个 aspect，但每对 aspect/来源仍须有可核的责任、归因与终态；不同文档不能伪装成一次调用。上限只为防循环与可追踪，**不是**节约 LLM 费用或压低质量，未获用户批准前不发真实请求。

**7. 业务纵链的完成判据（2026-09-24 补充）**：源集登记、导航命中、工具调用、Pack 入库、测试全绿分别只证明各自环节；任何一项都不能代替“有用材料到达 Writer 并形成有依据的业务正文”。对本轮代表性经营主题，须沿「上传文档原文与期间 → 标题树候选及实际读取的正文/表格 → `ResearchMaterial` 与拒绝/保留去路 → Pack → exact Writer manifest → 被接受的 Claim/引用 → 可读段落，或明确的 typed 缺口」逐级读回。通用导航应利用冻结 Contract 的主题/问题与 aspect 层级，在子项无独立标题或正文时，沿真实标题树查找适当父节点及其有界子树正文；不得在生产规则中写死“经营模式”等标题词。父节点正文可同时成为多个子 aspect 的候选材料，但每个 aspect 是否 covered 仍须分别由正文、事实、引用和 Contract 规则证明。标题相似度本身不证明覆盖，误命中的披露模板标题不得代替实质正文。对实际存在且与必需 aspect 相关的连续正文，候选事实被拒绝不得使原材料从 Pack 消失；正式使用与合法未使用须在 Writer 侧逐项交代。既有导航规则若需改变，应升其规则/依赖版本并做公司无关反例，不能用个案页码或关键词特判。

Demo 不要求所有 Contract 栏目都产出正文，但至少要有自然、客观、信息充分、逐句可回查的代表性经营正文；不能以孤立标题、碎片材料、候选 Claim、截断输出或“已登记三份文档”冒充成文。当前版本仍须由真实纵链证明跨文档取料、Pack→Writer 和段落内容；这些是 M930-3 待验收项，不改变 TS5 与正式树门状态。具体执行与证据见 `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §6.7.4。**历史 §0.17（2026-09-28）**曾要求目标表逐对象签发并进入 Pack；本次演示原表呈现已由 §0.21 改为人工确认的原 PDF 表区，只读可见而不获得结构化表/数字资格。

### 0.15 2026-09-26 M930-3 旧写作链「逐候选裁出」规则（历史兼容，写作侧已由 §0.20 取代）

本节只补**一项**：它**不改变**「被拒整束的有界定向重提案」与「整束 fail-closed」的既有口径，**不放宽**任何路径 B 授权面，也不改动冻结 Contract、SourcePolicy 或 TS5 拒发结论。

当一束候选提案**同时**满足「有条目被逐候选判据点名（路径 B 高风险表面，或支撑资格不合格）」与「同一次模型输出里仍有完全合格的条目」时，允许**先逐候选裁出**：被点名者各自形成 typed 逐候选拒绝记录，其余条目以**一份全新修订**继续走完整条链。整束拒绝在此时是**过度**的——不合格的是被点名的那几条，被连带作废的却是同一次返回里其余合法的候选。

1. **零新增调用**：裁出不发任何模型调用（`model_calls_added = 0`），它只是把**已经付过费的那一次返回**里不合格的几条逐条拒掉；这一条逐轮可核（裁出轮的 `calls = 0`），不是一句声明。
2. **原束不可变留档**：原束的完整有序候选身份、逐候选 typed 原因、判据逐字表面与代表调用哈希原样保留，`whole_set_rejected = True` 不变；裁出只让那条记录**多一个** `carve_out` 对账字段。
3. **新修订不是旧束的子集冒充**：新修订的候选、支撑提案与材料处理去向（WMPD `support_usages`）必须重新派生内容身份，与原束身份不相交；`carve_out` 逐条给出「原身份 → 被排除原因 / 被排除逐字文本与表面 / 新身份」，对账以**顺序 + 逐字文本**双判据闭合，任一条对不上即 fail-closed。
4. **必须重过全部门**：新修订对每个绑定主体重新执行唯一 aggregate `ClaimBindingDecision`、factual `ClaimEntailmentDecision` 与叙述硬门；裁出轮**不豁免**任何一道判定，两条判据（高风险面 / 支撑资格）各自独立执行且一步都不能少。
5. **互斥与边界**：与「定向重提案」「整束 fail-closed」互斥；一条都没被点名、或没有一条幸存（幸存数为 0 与整束拒绝是同一件事）、或本轮自己就是裁出轮、或已排定定向重提案时**不裁出**，退回既有路径一个字不改。
6. **缺口仍按第 9 条**（§0.13）：裁出本身**不产生** gap；只有 Contract 必需内容因该拒绝**仍然未被满足**时，才另外形成 ContractGap/Block。每个被排除候选都有独立拒绝记录，缺口的产生与它各自独立、各自绑定合同依据。

对象与版本：`CandidateCarveOutDecision` / `CandidateCarveOutTrace`，`CANDIDATE_CARVE_OUT_VERSION = "cco-1"`；逐候选排除原因码取自封闭集合（本出口实际使用 `path_b_high_risk_surface`、`path_b_ineligible_material_scope`）。**本节只登记裁决与边界，不声称任何阶段关闭：M930-3 未关闭、TS5 仍 no-go/未 seal、正式树门与 Phase 5/6 未进入。**

---

### 0.16 2026-09-28 M930-3 旧链 context 裁出与材料四列读数（前者历史兼容；后者继续适用）

本节记录两项边界：旧链的 context 裁出规则只供历史兼容；材料是否对栏目有用的四列分离仍适用于新链。2026-09-29 之后不得把本节理解为恢复 §0.13 旧写作主链，也不改动冻结 Contract、SourcePolicy、WritingSpec 或 TS5 历史拒发结论。

#### 0.16.1 明确否定事实不是背景衔接（`cco-6`）

**明确否定事实**（「控股股东与实际控制人报告期内未发生变更」「报告期内未发生重大诉讼/仲裁」这类）**自带主体与期间**，它是**事实**，不是衔接语。因此：

1. **只有**在该否定已有合法预验证事实身份（路径 A）时，它才可作为 factual 候选**重新提案**。此时主体、期间、否定命题都在预验证身份内，路径 A 是它唯一的合法通道。
2. 没有路径 A 身份时，承载它的 context 单元**及其 context 支撑提案**按**逐项 typed 排除**撤下：每个被排除对象各自产出独立记录，保留原文逐字表面、来源、排除原因与补件诉求供审计，**不进入新草稿、不进入正文**。逐项排除**不得**退化成「整束撤空」：被点名对象数**严格小于**该束对象数才可裁；全部对象都被点名时**不做裁出**，退回既有 fail-closed 路径并在原因里保留门规则 id（「全撤」与整束拒绝是同一件事，§0.15 第 5 条）。
3. **禁止的四种手法**（逐条都是「看起来过了门」的伪解）：
   - 只删「报告期」三个字把期间抹掉——否定命题失去期间就不再是被证明的那件事；
   - 把否定改写成含糊的正面句——改写不产生事实身份，只产生一句无法回查的话；
   - 让 context 单元补足 factual 授权——`context_only` 不承载事实，不得因它「越过」了某道门就获得事实资格；
   - 让这两个坏单元**连坐**销毁同一次返回里其余独立合格的经营描述——不合格的是被点名的那几条。
4. **幸存即新修订**：若裁出后仍有幸存提案集，必须产出**新的修订**与完整提案摘要，重新经过草稿闭合、绑定、蕴含与最终句审核；**旧决定一条都不得复用**。
5. **缺口独立于排除记录**：Contract 必需事实因该排除**仍然未取得**时**另立缺口**；逐项排除记录**不能**代替缺口（§0.13 第 9 条）。已知的待裁决补件（原始 20 条补件与重试轮次的补件）各自有待裁决去向，**不自动执行**。

对象与版本：`ContextUnitCarveOutDestination`（`next_draft_unit_id` 为空 ⟺ 该行是「被排除」那一行）与 `CarveOutContextUnitDecision`；`CANDIDATE_CARVE_OUT_VERSION = "cco-6"`。`cco-5` → `cco-6` 的口径变化只有一条：**判据口径多认一类被裁对象**，`narrative_gate_blocking` 自 `cco-6` 起进入 `CARVE_OUT_ELIGIBLE_KINDS`（= `REPROPOSAL_TRIGGER_KINDS` + `narrative_gate_blocking`），使硬门阻断的 context 单元可以逐项撤下。两条轴**正交且不得合并**：候选轴的原因码取自 `CANDIDATE_CARVE_OUT_REASONS`，context 单元轴取自 `CONTEXT_UNIT_CARVE_OUT_REASONS`（当前唯一成员 `narrative_vague_period`），`carve_out_axis ∈ {"", "candidates", "context_units"}` 逐轴对账；两类对象被撤下的法律后果不同，混成一张表就再也分不清是哪一轴判的。`model_calls_added` 恒为 0——裁出只处置**已经付过费的那一次返回**。

#### 0.16.2 材料对栏目有没有用：四列互不推出（`material-column-audit/3`）

「这一栏有没有材料」不是一个读数。逐栏目表**必须**分开下列四条**互不推出**的轴，任何一列不得顶替另一列：

1. **结构匹配**——只判「所在章节标题的**完整标签段是否整段等于**本栏本层 / 祖先层声明键」（`section_title_label_segment_equality`）。判 `误召` 时**不是**「内容与本栏无关」。
2. **正文相关性**——只读内容形态与原文可回查性（`content_form_only`），闭集为 `narrative_prose` / `disclosure_state_only` / `other_form` / `text_unreadable` / `kind_unreadable` / `no_disposition`。它的**语义那一半恒为 `not_judged_here`**：「形态上是散文」不等于「原文在讲本栏那一件事」，后者只能逐字读原文得出。
3. **事实资格**——Pack 侧处置与内容形态的**合取**（`pack_disposition_x_content_form`），闭集含 `fact_candidate_eligible` 与各类不合格读数。`fact_candidate_eligible` **只**说明取材上可以提候选；绑定与蕴含由 `ClaimBindingDecision` / `ClaimEntailmentDecision` 判，本表一个字都不替它判。
4. **实际写作采用**——只读本节 Writer 精确材料清单的成员资格。`manifest_unreadable` 是**不可判定**（`null` 不是 `false`：本轮可能根本没有可核对的清单），**不是**「没进清单」。

由此推出两条**明确禁止**的读法：

- **「标题不等于『采购模式』⇒ 这一栏没有材料」是错读。** 标题树节点叫「3、经营模式」时，任何子项键都不可能与之整段相等；这是**结构读数的下界**，推不出正文里没有采购遴选、生产安排与自建基地的内容。正文相关性只能逐字读原文。
- **「标题对得上 ⇒ 这句话有用」同样是错读。** 结构匹配命中只说明所在标题段落相等，既不说明形态可入正文，也不说明它被写作采用（第④列可能读作 `manifest_unreadable`）。

因此 `no_on_topic_material` 缺口只是**本层取材为 0 的结构读数**，不得直接读成「这一栏真的没有材料」。若据此需要修改导航或 Pack，**只能修正向取材规则本身**，并配公司无关的正反例；不得靠放大 top-k / 预算、固定页码、D 级来源或撤销现有安全门来换覆盖。

对象与版本：`evaluation/material_column_audit.py`，`COLUMN_AUDIT_SCHEMA_VERSION = "material-column-audit/3"`；四条轴的闭集与判据随载荷一并声明（`body_relevance_values` / `writing_adoption_values` / `structural_match_basis` / `body_relevance_semantic_field`），读的人不必翻源码；逐材料绑定指纹**同时带四列**，避免 diff 看不出「同一块原文的形态 / 资格 / 采用读数变了」。本模块**只读、零模型调用、不判语义**。

**本节只登记裁决与边界，不声称任何阶段关闭：M930-3 未关闭、TS5 仍 no-go/未 seal、正式树门与 Phase 5/6 未进入。**

### 0.17 2026-09-28 M930-3 演示主题的目标表资格门（历史裁决；本次演示由 §0.21 替代）

以下保留 2026-09-28 的当时裁决供历史追溯；**本次 M930-3 演示内容验收以 §0.21 为准**，不能继续把逐表自动签发误读为本次展示的前置，也不能把原 PDF 展示误读为已通过本节所述的结构化资格。原裁决不改写历史 TS5 no-go、信任根快照与既有 run。

#### 0.17.1 业务范围：「完整列出」的确切所指

演示的 `company_business` **必须同时呈现**两样东西：**有实质内容的经营文字**与**合格的原始业务表格**。其中「完整列出」**只**指下列这一件事：

> 对该主题的 **18 个 aspect × 三份上传材料**，逐格列出：相关**正文与业务表候选**、**实际读取**、**资格**、**Pack 去向**、**Writer 去向**与**未取得原因**。

它**不是**要求覆盖全报告的 187 个 aspects，也**不是**把来源原文整段堆进报告正文。清单是**取材对账**，不是正文篇幅。

#### 0.17.2 退出条件：两类未呈现分得开

| 现象 | 判读 | 后果 |
|---|---|---|
| 表格**确实存在于**上传材料，但因识别、续表、资格或工具接线失败而未呈现 | **系统能力缺陷** | 主营业务内容门**不得通过**；必须按对象登记失败环节 |
| 已完成**可核查检索**，确认来源**确实没有**该内容 | **来源缺口** | 按缺口诚实呈现（缺什么、已查范围、影响、建议材料类型） |

「系统没做出来」与「来源里没有」是两组不同的结论，**不得互相顶替**。只有后者才可标成来源缺口。

#### 0.17.3 三条不得越过的边界

1. **诊断不是材料**：诊断平铺文本、`refused`/`partial` 对象**不得**变成正式材料、**不得**充当数字权威，也**不得**进入正文。
2. **预览不替代验收**：目标表未取得时，允许先产出**明确标注「未核验、不可发布」**的文字诊断预览（用途只有一个：检查 Writer 是否按 Pack 原文写作），但它**不能替代最终验收**，也不改变任何门的状态。
3. **不靠放宽换呈现**：不得用统一放大预算/top-k、放宽 fail-closed、D 级来源或 snippet 入正文来把表格「凑」出来。

#### 0.17.4 本次业务验收的读回要求

M930-3 的本次业务验收必须**实际读回合格表格、自然正文及各自的引用**，并由**人**检查内容——不接受「已登记」「已生成候选」「已进 Pack」这类中间态作为通过证据。

#### 0.17.5 工期影响

在**修改 TS5／图表接线代码之前**，必须先完成 `AGENTS.md`、本文件、`V2_IMPLEMENTATION_PLAN.md`、`2026-09-30_DEMO_BACKBONE_MILESTONE.md`、`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` 与相关父级任务书的最小一致性修订（本 §0.17 即其中一件），写明新的业务范围、退出条件与工期影响。第一批研究派发与取材修复不受此序约束，可继续。

真实 LLM 调用与外部检索仍**逐次单独授权**；本批不 stage、不 commit、不 seal。

**本节只登记裁决与边界，不声称任何阶段关闭：M930-3 未关闭、TS5 仍 no-go/未 seal、正式树门与 Phase 5/6 未进入。**

---

### 0.18 2026-09-29 M930 阶段顺序与正式表格单通道裁决

本节的**演示执行顺序**继续有效；其中引用 §0.17/§0.19、要求本次目标表先结构化签发并进入 Pack 的部分已由 §0.21 取代，不改变冻结 Contract 或正式阶段关闭条件。先完成一个新版本 scope 的核心纵向切片：公司节只选 `company_business`，财务节选 `fin_source_scope` 与 `fin_solvency`；主体身份仍须在输入、来源清单和预检中核对，但不把未选的 `company_identity`、`company_legal_risks` 当作已研究。`industry_scale_cycle`、行业外部检索与事件/负面核验在本切片均标为 `out_of_scope/not_demonstrated`，不得标成通过、无缺口或已完成。历史三节 scope、run 和验收结论不改写。

执行顺序为：**M930-3 主营业务正文与合格原始业务表格、财务权威事实及可读分析的同版本真实纵链 → 在该切片上实施 M930-4 独立只读审查 → M930-5 确定性状态与只读 UI → 再扩到行业及外部检索能力展示**。核心切片能证明所选范围的纵向设计，不能冒充原三节 M930 完整验收、TS5 全面关闭或正式 Phase 5/6 关闭。主营业务仍按 §0.17 的 18 aspect × 三份上传材料逐格对账，且目标表缺位时内容门不得通过；财务表的确定性展示也不得冒充从 PDF 签发的原始业务表。

**W8 正式生产路径选单通道替换，而非并行接线。** 当前 `tobj-*` 从 Evidence 文本另行重切并投递表材料，不受图侧守恒签发约束；`gto-*` 已能读图侧资格，但尚无生产调用方。正式目标是：同一份图侧来源 → `gto-*` 按 §0.19 对目标表逐对象严格核验与放行 → **复用现有工具、Pack、Writer 的材料投递接口**。`tobj-*` 不得继续作为另一条可独立签发/投递目标表的生产来源；历史对象保持只读兼容。对象 `partial`、来源/身份不一致或目标表自身账不平不得进入 Pack。表格作为阅读材料与表内数字作为可引用的权威事实仍是两项独立资格；数字未取得合法结构化权威时只停数字发布，不把诊断文本升级为事实。原“文档 refused 即所有表归零”的裁决由 §0.19 对演示目标表作明确替代；正式 TS5 全文档门并未因此通过。

若构造、接口或冻结契约需要变更，先列精确 file-level 影响和版本/迁移方案；未触及冻结业务契约的普通实现可在本裁决范围内继续。真实 LLM、外部检索仍逐次单独授权。本节是**目标与顺序裁决，不是已实现或已验收的声明**。

---

### 0.19 2026-09-29 演示目标表的逐表完整证明（用户已裁决，待实现）

本节保留为**结构化表能力轨的历史设计与后续待办**，不再是 2026-10-01 M930-3 演示原 PDF 区域展示的前置或通过条件；现行演示范围以 §0.21 为准。原裁决曾替代 §0.18 中“整份文档被拒则所有目标表一律归零”的资格粒度。全文档守恒问题仍是正式 TS5 的未解决缺陷，必须保留原始拒发结果、问题清单和信任根；此变更不是对 `partial` 表、诊断平铺文本或未构造表的豁免。

目标表须从**同一图侧来源**逐表证明：表题与所属主体、业务口径及适用期间；完整物理表头、行列标签、单位、合并单元格及续表对应关系；每个拟展示单元格的原始数值/文字与精确 PDF 位置；本表范围内字符/区域的守恒与唯一归属；来源文档、版本、Evidence 和表对象身份一致。不能以任意优先级吞掉重叠、把未知字符标为已消费、用模板表头补空白，或仅凭对象状态 `complete` 放行。证明失败者保持 `partial/refused`，给出表级 typed 缺口；跨表或跨页关系未证明时不得截取一半冒充完整原表。

**阅读资格与数字权威分开。** 完整表对象经现有图→工具→Pack 单通道成为阅读材料，不从 Evidence 文本另建第二条可投递表路径；表内收入、成本、毛利、比例等用于正文断言、计算或财务分析时，还需结构化格级权威：指标/业务行、期间/事件、列、单位、口径、原值、来源位置逐项核对；计算只由 Python/Decimal 完成。可读表不自动授权数字，数字事实也不能反过来伪装成原表。

验收逐张列出“未构造／已构造但不完整／表自身证明通过／已进 Pack／Writer 已收到／读者面已呈现／格级数字已授权”七步及拒绝原因。当前已观察到的四张 `partial` 与两张未构造目标表，**不会因本裁决自动转绿**。M930-3 仍须在同一版本真实纵链同时读到主营业务文字和合格原表；TS5 全文档门、正式树门及历史 run 均不因本局部门通过而关闭。实施前须列出被替代的文档级判断在构造器、图侧放行、工具、Pack、版本/身份和回归中的精确影响，不得以改文档代替编码与真实验收。

### 0.20 2026-09-29 Pack 驱动的带引用写作与独立审阅（用户已裁决，待实现）

本节替代 §0.12–§0.16 **写作侧**“原子候选→聚合绑定→逐候选蕴含→接受绑定→Claim 拼文→最终句门”作为现行生产主线；旧代码、版本、测试与历史产物保留只读兼容，不删除、不改写，也不作为新主线的必经门。研究侧 Evidence、标题树、Pack、来源角色、`FactCandidate/FactQualificationDecision/SupportedFact`、财务权威和冻结 Contract/SourcePolicy/WritingSpec **不变**。实施新主线以前，不能把尚未存在的接口写成已运行。

现行目标顺序：`Contract/WritingSpec → Harness 唯一研究运行时 → 精确且完整的当前 Pack/合格事实清单 → Writer 直接依据原文写带引用自然正文 → 不调用模型的逐句底线核对 → 独立只读语义审阅 → 逐句标记的不可发布预览与独立的系统/人工状态`。Writer 只负责写作和指出所需补件，不再在同一次回复中生成 `ClaimCandidate`、支撑提案或自我批准；Claim 历史账不再是正文素材。**正文每句**标注本次 manifest 内的材料或权威事实短引用；纯标题和独立列出的缺口不是正文句。系统确定性展开到 Pack 身份、文档版本和精确页/片段或表格行列/单元格；只写“见某个 Pack”而无具体成员与位置不合格。Writer 不检索、不联网、不计算、不回写 Pack。

核对器逐句确定性检查：引用确属本次输入、来源身份/定位真实、硬性数字/日期/主体/否定不凭空生成、旧来源不冒充当前、勾选/模板文字不冒充事实、Contract 栏目未覆盖须显式留缺口。一般经营描述可以自然改写，不要求逐字复制 Claim 或每句机械写年份；来源披露日、事实适用期和报告生成日仍各司其职。**财务金额、比率、跨期变化及业务原表数字**不得由普通正文材料或裸 `m*` 授权；必须引用具名、已资格化且与句意匹配的事实，并逐项核对主体、指标/事件、期间、单位、口径和表格行列/格值，LLM 不得自行计算。机械核对仅证明底线，不能宣称“材料语义支持句子”。

独立审阅以整节正文、逐句精确引用及来源全文为输入，只提出 `ReviewIssue`：不支持、夸大/越界、因果误写、局部推整体、选择性取材、矛盾、旧材料当前化和重要限制遗漏等；不改稿、不检索、不联网、不覆盖硬错误，也不自行放行。必要时 Writer 最多有界返修一次，改后重新核对与审阅；仍有问题的句子保留原文及标记，不因一处失败抹掉整节已有合法文字。审阅意见与硬错误分列，不能把“没有发现问题”冒充系统或人工批准。

状态至少分开记录：①正文预览是否可读；②逐句确定性硬核对；③独立审阅意见/完成状态；④人工接受。读者面逐句显示引用、问题与中文缺口，预览明确“不可发布、未经人工接受”；“人工修改”只预留展示入口/状态，不实施编辑、回写或续跑。正式发布须硬错误为零、必需缺口诚实说明、独立审阅完成且 blocking 问题已解决，并走独立的系统放行与人工接受，不能只凭句子数或测试全绿。即便预览可读，**M930-3 本次演示内容门仍要求经营文字与 §0.21 的经人工确认原 PDF 表格区域同时呈现**；财务展示表不是业务原表替代，原 PDF 区域也不是 §0.19 的结构化合格表替代。

本次核心双节的**内容证据**：同一当前版本的真实创建式运行中，主营业务正文目标为 3–5 段，覆盖产品、应用和研发/采购/生产/销售模式；力争不少于 15 个通过硬核对且无 blocking 审阅意见的事实句，并由人工抽查至少 10 句与所引原文的语义一致性。句数是读回量级，**不是代替人工内容判断的放行捷径**。收入构成、成本和毛利的原 PDF 表格区域按 §0.21 经人工确认后与文字同时呈现；区域未能忠实定位/展示仍记系统能力缺陷，本次演示内容门不通过。财务偿债表保留真实权威值、期间、单位和代理口径，业务正文所用原表数字另按 §0.21 格级核对。独立审阅意见随预览可读，未审不得写成通过。

本裁决先改设计，随后再给出新接口、版本/迁移、旧链兼容、定点测试和业务验收的 file-level 实施计划；未经单独授权不发起真实 LLM、外检或 create-only run。M930-3、M930-4/5、TS5 与正式阶段均未因此关闭。

**迁移边界必须显式处理，不得把新句子塞入旧 wire：**现有 exact `VerifiedPackSet`/材料阅读视图可作为输入基础，但现有 Writer 使用账以 proposal ID 记用途，新链必须以句子引用重新版本化；财务事实和附注使用各自权威清单，不伪造 Topic material。现有 ReportVersion/组装接口强绑定旧 Draft/Claim/Binding/Entailment，必须创建新版本的正文身份、引用/核对记录、报告组装和只读预览适配，同时保留旧版解码与历史 run。现有 Assurance `ReviewIssue` 与输入 bundle 尚未具备完整句级定位，须版本化扩展并保证问题绑定真实 `report_version`；不能把当前只有 schema 的 `assurance/` 写成审阅运行时已落地，也不能把旧诊断预览冒充新审阅产物。

### 0.21 2026-10-01 M930-3 演示原表展示与数字授权分离（现行裁决）

用户为完成核心双节纵向演示，决定**不再以六张目标业务表全部自动重建、取得 `TableObject` 资格并经图→工具→Pack→Writer 投递为本次 M930-3 内容门前置**。本节替代 §0.17–§0.19 及 §0.18 中相关的**演示目标表**要求；不修改冻结 Contract、SourcePolicy、WritingSpec、正式 TS5 全文档/结构化表门、历史 run 或失败事实。旧逐表结构化路线转为正式能力轨的后续工作，不能把本次 PDF 展示反记为其通过。

1. **两条可读路径，身份不得混用。**经营正文仍走 `Contract → Harness/树 → Evidence/Pack 与合格事实 → 精确 Writer manifest → 带引用自然句 → 确定性硬核对 → 独立只读审阅 → 不可发布预览`。业务原表另走 `已登记的原始电子 PDF → 精确页/区域定位 → 人工确认 → 来源原件区域只读渲染 → 预览`。后一条只是来源回查视图，不进入 `TopicResearchPack`/Writer 材料身份，不算合格 `TableObject`、结构化复原、表格数字权威、Contract `set_complete` 或系统发布资格；不从截图/OCR 反提取数字。
2. **原件展示须可核查。**每个展示区域记录来源文档身份、版本及原 PDF 字节哈希、页码和区域坐标、表题、适用期间/单位（能从原件确认时）、渲染产物哈希；跨页或续表必须把属于同一目标表的各页区域按顺序列齐，不能截半张当完整原表。人工确认记录确认人、时间、范围与结论，只确认“展示与原件一致、边界完整、文字数字清晰可见”，**不**确认任何格值已获事实资格。区域选择允许落在当前 run 的来源展示记录，不得写成公司名、证券代码或固定页码的生产特例。来源 PDF 不可用、区域无法定位/显示或确认失败时，明确记**系统展示能力缺陷**，不能写成“上传材料没有表”。
3. **本次演示业务范围。**仍对 `company_business` 的 18 个 aspect × 三份上传材料做来源责任、实际读取、Pack/Writer 去向和缺口对账；但“原表展示”的最小必需集合按**内容**而非六张自动对象计数判：最新与比较期年报中与营业收入构成、营业成本及毛利/毛利率有关的原件表区应供人对照，页内多个表区或续页照实列出。募集说明书相关表照实登记、说明相关性与采用/不采用，不为了凑表数强行展示。未覆盖的 Contract 栏目与未取得的可比数据保留 typed 缺口；“本轮未取得”不得写成“来源不存在”。原件区域成功展示只满足**演示可见性**，不自动证明栏目事实已覆盖。
4. **正文数字独立授权。**Writer 不得凭原 PDF 图像、普通 Pack 文本或裸 `m*` 写出收入、成本、毛利、占比或跨期比较。只有正文确实要用的少量格值才单独建立合格事实：逐项核主体、业务行、指标列/格、期间或事件、单位、口径、原值、文档版本及精确来源位置，并在句级引用与机械核对中绑定具名事实。差额、增减幅和占比由 Python/Decimal 以已合格输入计算；证据不足即不写该数字，另列缺口，不用模型猜测。人工确认截图**不能**替代此资格。
5. **财务表格保持原门。**财务指标表继续由 `FinancialFactPack/FinancialSnapshot` 确定性生成；逐格对权威事实的数值、符号、期间、单位、口径与引用，不换成 PDF 截图，也不让 Writer 计算。`diagnostic_only/audit_only` 的代理输入不得混进正文主指标表；允许展示的代理计算指标须保留负值、`PROXY_FINANCE_EXPENSES` 与限制说明，并按 Contract 与 `FORMULA_REVIEW.md` 判展示位置。`fin_source_scope` 不得用 `fin_solvency` 的整套指标表冒充本栏目内容。离线格值对账不等于真实模型审阅或人工接受。
6. **独立验收与状态。**本次 M930-3 演示内容门要求同一当前版本真实纵链有可读、在题、可追溯的主营业务正文，已确认且完整的上述原 PDF 表区，所写数字逐项有合法事实及计算链，财务表逐格权威对账且呈现位置合规，并有句级硬核对、独立审阅意见和人工抽查读数。每环分列“找到/可写/送达/写出/核对/审阅/人确认”；预览、系统放行、人工接受与正式阶段关闭分开。若任何必需表区未能忠实展示、重要事实错误或真实写作/审阅尚未完成，本门不得宣布通过；结构化表资格与 TS5 仍明确为未通过/未关闭。新的验收标准不能倒填历史 r1–r17 或旧 MOCK 的结果。

实现这一路径时优先做最小、可复核的来源展示接口和少量数字事实适配，随后验证 Pack→Writer→正文→核对→审阅的真实业务纵链；不再让六张表的自动表头/全格重建支配本次 Demo 日程。真实 LLM、外部检索与 create-only run 仍须逐次单独授权；本节不批准 stage、commit 或 seal。

### 0.22 2026-10-05 M930 三屏演示：本次上传创建新运行的范围（现行）

**前提纠正。**本节的必要性来自一处前提更正：`AGENTS.md` §7 与 §0.7 禁止的是**生成后补件、缺口绑定与用户续跑**，**不是**「首次上传创建新运行」。因此本节**不**给 `AGENTS.md` 加例外、**不**放宽任何门禁，只把本批演示的范围写清楚。

1. **本次上传建立**一次新的 create-only 运行。第 1 屏上传的三份电子 PDF 的**原始字节**落进一个新建、不可覆盖的运行输入目录（`data/run_inputs/<run_id>/`，整目录不入 git）；链在读 PDF 的三个点上**只**按 `(document_id, sha256)` 命中本 run 的上传对象，命中不了即失败，**一处都不回落**到登记路径或 `data/samples`。运行 id 只在点击「开始生成」时铸一次。
2. ~~**本批只生成公司节（主营业务）。财务节本次未生成、未审核。**原因是本次上传的三份 PDF 与财务节的权威输入（三份 Excel 报表及其背后的财务权威快照）**不是同一组材料**，两组之间没有任何可互相勾稽的内容身份。既有财务 A2 只在明确标注的「历史对照 · 非本次生成」区作为历史记录出现，**不**算作本 run 的产物，也**不**得据此宣布双节验收完成。要本 run 新生成财务节，须另报 XLSX 上传、财务权威快照与调用预算方案并另行获批。~~ **【2026-10-05 由 §0.23 取代】**：本次业务裁决改为同一次上传创建同一个新 run 并**同时产出 `company` 与 `financial`**，首屏增列三份财务 XLSX 的 typed 上传绑定。本条的其余部分（第 1、3、4、5 项）继续有效。
3. **第 2、3 屏只读本 run 自己。**第 2 屏只读本 run 的 `rj-1` 阶段日志（真实时间戳、真实阶段名；无 `checkpoint_id` 即写「不可恢复」）；第 3 屏只读本 run 自己的正文、引用、缺口、机械核对与审阅读数。两屏都不复用 `ndc5` / `dual_v2_r1` 的正文或审阅结论；拿不到本 run 产物时页面如实拒绝，不回落显示历史 run。状态不预设为通过或不可发布。
4. **本批不新增门、不放宽门**：不改冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile，不改 `data/` 下任何共享库，不改历史 run 与冻结产物，不改 M930-3 质量判据，不伪造引用、不放开数字资格。`2026-09-30_DEMO_BACKBONE_MILESTONE.md` 与 `V2_TODO.md` 同步同一句范围声明。
5. 真实 LLM、外部检索与 create-only run 仍须逐次单独授权；本节不批准 stage、commit 或 seal。M930-3/4/5、TS5 与正式阶段均**未**因此关闭。

**一次性运行授权（`cra-2`，2026-10-05 追加；`cra-1` 的字段集由本节扩写）。**真实模式在**第一个模型请求之前**必须持有一份逐字段对上的批准凭据：`run_id`、三份上传 PDF 的 `(document_id, sha256)`、三份上传 XLSX 的 `(source_version, sha256)`、财务快照身份（`snapshot_id`／主体／期间／合并口径／币种／用途）与它完整的 `source_versions`、v2 profile 指纹、双节集合、模型、prompt 版本、每类上限与整轮上限、自动重试次数，任一项不同即拒，且拒在**建立结果目录之前**（不留看似仍在运行的半成品，只留一条可读的拒绝留痕）。凭据由**人**用 `scripts/authorize_cited_run.py` 写，链只读、从不写。它是一次性的：消费用 `O_CREAT|O_EXCL` 原子建标记，重复点击或另一个浏览器会话消费不动同一份。**持久化读回时 `granted_by` 为空即拒**——「谁批准的」这一读数不能因为落在盘上就变成一个可省略的字段。这条门是**加**在既有每-run 预算门之上的前置条件，两者都不能代替对方（预算是上限，本门是许可）；它不构成本阶段之外的权限系统——没有账户、角色或可继承权限，也**没有**改动任何既有门的口径。

### 0.23 2026-10-05 三屏演示改判：同一次上传同时产出公司节与财务节（现行；取代 §0.22 第 2 条）

业务裁决变更：**三屏演示必须由同一次浏览器上传创建同一个新 run，并同时产出 `company` 与 `financial`**。§0.22 第 2 条「本批只生成公司节、财务节未生成」自本节起作废；§0.22 其余各项（本次上传建立 create-only 运行、第 2/3 屏只读本 run、不新增门不放宽门、真实调用逐次授权）继续有效。本节**不**改冻结 Contract／SourcePolicy／WritingSpec／PresentationProfile，**不**写共享财务库，**不**改历史 run，也**不**批准任何真实模型调用。

1. **首屏收六份对象，两条绑定的身份不混用。**三份业务电子 PDF 继续按 Evidence 文档身份与 SHA-256 绑定（`cri-1`，格式与指纹**不动**）；三份财务 XLSX 建立**独立、typed** 的财务输入绑定（`cfi-1`，新增），**不得**把它们伪装成 Evidence PDF，也不得让任一绑定回退读取 `data/samples`。缺件、多件、重复、错主体或哈希不符一律具名拒；文件名**不能**代替内容身份。六份上传对象、同一 run-id 与各自的来源角色均须落盘并在页面可查。
2. **财务权威复用，但先证同字节。**财务节继续使用现有**只读** `financial_v2.db` 的 current、valid 快照，但在**建立结果目录之前、第一个模型请求之前**必须证明：本次上传的三份 XLSX 与该快照的**完整** `source_versions` 逐份**同字节**，且主体、期间、合并口径、币种、用途与快照 ID 全部一致；财务装配时**再核一次**快照未漂移（`current_snapshot` 指针、validity 与 `source_versions` 逐项相等）。任何一项不符 ⇒ **整轮在首个请求之前拒绝**，**不**拿旧快照顶替。本批**不**写共享财务库、**不**重抽取工作簿；对外准确表述为「**本次上传核对并复用同字节的已建立权威快照**」。
3. **一次 run 跑双节，A2 必须是本 run 自己的。**同一新 run 执行 `company,financial`，显式使用含 `fin_balance_structure` 的 v2 profile。公司节走本次上传 PDF 的取材与新 Writer；财务节由已绑定的权威快照在**本 run 中重新装配** `FinancialFactPack`，生成 A1 正文与 A2 确定性资产负债结构。A2 必须落在**本 run 的财务目录**、保持零模型计算与原有数值权威；**不得**复制或链接旧 `dual_v2_r1/financial` 充作新结果。A1 的逐句审阅、A2 的确定性呈现、报告级审阅未运行，三者状态**分开**显示。
4. **授权与预算升版。**一次性凭据（`cra-2`）绑定六份文件哈希、快照身份与口径、v2 profile 指纹、双节集合、模型、prompt 版本与调用上限；补一条「持久化读回时 `granted_by` 为空即拒」的负例。预算升 `cited-budget-3`：两节各 ≤1 次写作 + ≤1 次逐句审阅，**整轮 ≤4**，零自动重试；**返修本次不自动发**（本版类别集不含返修），因此不占额、也不因有硬错而失败。真实调用仍须单独明确授权。
5. **第二、三屏按本 run 的真实读数分列。**第 2 屏只显示这一个 run 的真实阶段事件，且**不得**把阶段日志称作可恢复 checkpoint（`resumable` 恒 false）。第 3 屏并排呈现本 run 的公司正文、财务 A1 与 A2，**分别**列机械核对、审阅、系统放行与人工状态。`run_outcome=completed` 却缺任一节或缺 A2 时**必须显著失败**——不得跳过后显示双节成功。历史产物只出现在明确标注的「历史对照 · 非本次生成」区。
6. 离线替身**不**证明 Writer/Reviewer 质量；「规则已写入文档」**不**等于「代码已实现」，**不**等于 M930-3/4/5、TS5 或任何正式阶段关闭。本节不批准 stage、commit 或 seal。

---

## 1. V2 产品目标

### 1.1 一句话定义

V2 是一个面向客户经理的、以证据驱动信用研究为核心的授信报告生成系统：能够将上传材料和可信外部信息转化为可追溯 Evidence，按报告章节完成结构化研究，生成跨章节一致的授信研判，并通过分层评测和全报告验证保证结果可检查、可回放。

### 1.2 V1 到 V2 的变化

| 维度 | V1 | V2 |
|---|---|---|
| 知识单元 | PDF text chunk | `EvidenceBlock` 来源锚点 + `PageLayout/DocumentOutline` 结构层 + `OutlineSpan/TableObject` 材料单元 |
| 检索 | 固定查询 + Dense top-k + 简单加权 | Information Need Router + 结构化查询 + Hybrid + 深度检索 |
| 章节目标 | Markdown guidance | 可执行的 `SectionContract` |
| Agent 行为 | 固定调用若干 Agent | Workflow 为主，开放研究使用共享 Research Harness |
| Agent 状态 | 隐含在函数和上下文中 | 显式 `ResearchState` + checkpoint + 停止条件 |
| 章节质量 | 主要依赖 Prompt | Rules + 章节 Evaluator + 定向返工 |
| 综合报告 | LLM 根据素材重写全文 | 确定性组装 + 受约束的跨章节综合研判 |
| 回检 | 数值、实体、时效三类规则 | Citation、Numerical、Entity、Temporal、Cross-section、Decision Assurance |
| 评测 | 模块单测/Mock eval 为主 | 数据集驱动的分层离线评测 + 在线运行观测 |
| 可观测性 | Retrieval/LLM 日志 | Trace、Cost、Latency、Audit 贯穿完整任务 |

### 1.3 V2 不追求的事情

- 不以“Agent 数量多”为目标。
- 不让 LLM 计算财务数字或确定性项目测算。
- 不让一个大模型 Prompt 同时负责检索、分析、写作和核验。
- 不把所有章节都改造成无限循环 Agent。
- 不在第一阶段追求生产级多用户、权限、加密和高并发。

### 1.4 V2 启动时的历史 V1 代码基线（非当前实现状态）

- V1 标准模板包含公司主体、财务、行业和综合授信建议，当时尚无项目分析。
- V1 PDF 索引已保留页码、节段标题和文档类型，后来成为 Evidence 迁移起点。
- V1 检索是 Dense Retrieval + 文档类型加权 + 多查询去重，当时尚无 BM25、Router 和标准 EvidencePack；这些能力已在后续 V2 阶段实现。
- V1 公司主体 Agent 使用预设查询，研究目标和循环状态尚未显式化。
- V1 Synthesizer 根据三份素材重写全文；V2 已改为基于权威事实/Claim 的受约束生产方向。
- V1 Verifier 只有数值、实体和时效检查，作为后续 Report Assurance 的迁移起点。
- V1 `evals/` 主要验证代码行为；后续已增加 gold、Router、Harness、章节状态机等评测，但当前仍缺 P3R Topic Pack 与段落内容完整性评测。
- V1 `agents.ingest.run()` 为占位实现；现行 V2 编排不得回退依赖它。
- V1 `financial.db.query_metric()` 缺少来源版本与口径隔离；现行 Financial V2 已改用核准快照，V1 查询只作兼容能力。
- V1 `external.web_search` 使用 DuckDuckGo 摘要；现行外部链已改为博查搜索、正文获取和不可变快照。

以上只解释 V2 为什么这样设计，不描述当前运行状态。当前状态以 `V2_TODO.md` 为准。

---

## 2. 继承的硬约束

以下内容构成 V2 当前硬约束：

1. `[已确认]` 第一阶段仅接受 PDF 和 Excel；财务材料允许 Excel、PDF 或混合上传，Word/PPT/图片列入第二阶段。
2. `[已确认]` LLM 不计算指标、比例、增长率和项目现金流。
3. `[已确认]` 公司信用、财务和项目章节涉及的数字，必须先抽取到结构化记录，再由 Python/SQL 校验或计算后进入 Prompt。行业章节可以引用有来源的外部数字，但任何派生比例、增速或比较仍由 Python 计算。
4. `[继承V1]` 所有 RAG 调用必须通过统一检索接口并落盘日志。
5. `[继承V1]` 所有 LLM Prompt 存放在 `llm/prompts/`，禁止内联。
6. `[继承V1]` 核心模块必须有 CLI，可脱离 Streamlit 独立运行。
7. `[继承V1]` Streamlit 只负责输入、任务调用、状态展示和结果交付。
8. `[已确认]` V2 继续本地部署，GitHub 仅作为代码展示和版本库；结构化数据继续使用 SQLite，Dense Retrieval 先保留 BGE-M3。
9. `[已确认]` 第一阶段只支持 A 股上市公司。
10. `[已确认 O-01]` 第一阶段明确排除扫描 PDF/OCR；低文本质量或扫描件直接提示用户改用电子年报 PDF 或 Excel。

> `AGENTS.md` 已同步电子财务 PDF、Excel 与混合上传约束。V2 接口迁移仍须逐模块明确兼容入口，不能直接把多来源记录交给 V1 求和查询。

---

## 3. V2 总体架构

```text
报告模板与 Section Contracts
                 │
                 ▼
客户经理 → 企业全称 + 授信类型 + 授信方案 → 分类上传材料
                 │
                 ▼
       Parse / Normalize / Quality Check
                 │
          ┌──────┴──────────────────┐
          ▼                         ▼
 Evidence Store                Financial Store
 (immutable provenance)             │
          │                          │
          ▼                          │
 PageLayout → DocumentOutline       │
          │                          │
          ▼                          │
 OutlineSpan / TableObject          │
          └──────────┬───────────────┘
                 ▼
      Report Planner / Section Tasks
                 │
                 ▼
          P4 Worker 编排外壳
                 │
       ┌─────────┴──────────┐
       ▼                    ▼
公司/行业 Harness       财务 Workflow
 Topic Runtime          Python/SQL Rules
       │                    │
       ▼                    │
Topic + Aspect 待办          │
       │                    │
       ▼                    │
(Router → Tool Registry → Evidence/Structured/Web)*
       │                    │
       └─────────┬──────────┘
                 ▼
 TopicResearchPack / FinancialFactPack
                 │
                 ▼
       Worker writer 阶段
  → Claims + NarrativeParagraphs + Tables
                 │
                 ▼
          Section Quality Gates
                 │
                 ▼
  Deterministic Assembly → 综合方案评价
                 │
                 ▼
 Deterministic Report Assurance Controller orchestration
  → hard gates
  → isolated Independent Review Agent（read-only ReviewIssue[]）
  → deterministic version-bound aggregation
                 │
        ┌────────┴─────────┐
        ▼                  ▼
系统审核未通过          系统审核通过
  → 草稿/定向返工          │
                           ▼
                    可供人工最终确认
                           │
                           ▼
                  人工已确认的报告版本
       + 引用 + 风险清单 + 方案评价 + Audit Package

第二阶段条件分支：授信类型为固定资产贷款/项目贷款
  → 项目材料（PDF + 预测 Excel）
  → 项目分析 Workflow（仅用户材料，不联网）
  → IRR/盈亏平衡点/压力情景
  → 并入综合方案评价

横切全流程：Evaluation / Trace / Cost / Latency / Audit
```

### 3.1 核心架构原则

1. **先定义章节，再定义检索。** `SectionContract` 决定 Planner 要拆什么问题。
2. **检索按 Information Need 发生。** 不在任务开始时统一召回一大包上下文。
3. **EvidenceBlock 是统一来源接口，不是统一语义材料。** 内部文档、Web、API 和结构化数据都需要可追溯来源；电子 PDF 的业务结构由版本化 `PageLayout/DocumentOutline` 派生，RAG/Pack/P4 以 `OutlineSpan/TableObject` 消费。
4. **Harness 是共享运行时。** 公司、行业、项目研究使用不同 Policy，不复制三套 Loop。
5. **Evaluator、独立 Reviewer 和 Assurance 分工。** Evaluator 控制章节是否需要返工；独立 Reviewer 只读输出结构化问题；确定性 Assurance Controller 判断当前报告版本是否满足系统门，三者都不代替人工最终确认。
6. **综合不是重写。** Synthesizer 可建立跨章节关系，但不得创造新事实或新数字。
7. **借款主体与实际控制人不得混同。** 借款主体是申请授信的法人；实际控制人用于识别控制权、治理和关联风险，不称为“真正借款人”。
8. **页面状态不暴露模型思维链。** UI 展示阶段、工具、次数、耗时、错误和停止原因；内部 Trace 保存结构化动作和结果，不保存或展示隐藏推理过程。
9. **先完成研究覆盖，再组织文字。** P3 负责把 Topic 的必答 aspect、材料、事实、冲突和缺口归拢成 Pack；P4 不得用写作 Prompt 弥补上游未研究的内容。
10. **原子事实与完整叙述并存。** 小粒度 Claim 保证可验证，多 Claim 段落和表格保证可读性；禁止把“一条 Claim 一句话”的审计结构直接当成最终报告。
11. **宽问题可以内部拆解，但不得产生影子 Contract。** 子 need 必须从正式 question/aspect/evidence requirement 派生并保留 parent identity，不得由样例公司、gold 页或手工 case 表决定。
12. **安全门不等于研究能力。** fail-closed 负责阻止错误内容进入报告，但不能把缺少研究、上下文或来源的状态包装成“系统已完成”；内容完整性必须独立评测。
13. **三条日期轴必须分开，任一条不得顶替另一条**（2026-09-27 定点澄清，见 §0.13 第 11 条）：
    - **（a）事实适用期**（何时成立）：经营流量/事件使用“2025年度”“2025年内”或明确检索截止日；余额使用“截至2025年12月31日/2026年3月末”。它是高风险硬事实，**只能**来自预验证权威事实（路径 A）或精确材料自己的原文，**不得**推测、**不得**由任何旁路日期字段回填；除非章首已定义，不用含义模糊的“报告期内”替代具体期间。
    - **（b）来源归属**（谁在什么时候披露的）：材料身份、版本、页码与**可核实披露日**。它由系统**从已登记的来源身份确定性渲染**，作者不手写；披露日不可核实就标 `unknown`，**入库时间、PDF 元数据、财务期末一律不得冒充**。
    - **（c）`report_as_of`** 是报告生成日，独立于 (a)(b)，既不当事适用期也不当披露日。

    **非数值的一般经营描述**（产品、业务模式、采购/生产/销售方式）**不要求**每一句机械重复“2025年度”，但**不得**写成“截至报告生成日仍然如此”或“一直如此”：只由旧材料支持的内容须保留历史来源归属，或留缺口；新旧材料存在实质差异时不得用相似文本抹平。新闻类外部事件优先使用有依据的**事件发生/生效日**；只有报道发布日时，只能表述为“某日发布的报道提及……”，不得把发布日冒充事件日。
14. **篇幅服从内容，不设 8,000 字符硬上限。** 完整授信报告可按 2～3 万中文字符作为人工参考，但阶段验收看 Contract 覆盖、信息密度、可读性和引用，不靠压缩或凑字数过关。

调用栈上，公司/行业 Worker 是 P4 编排外壳：它先调用 Harness Topic runtime，取得 `TopicResearchPack` 后再进入自身 writer 阶段。数据语义图将 Pack 画在“研究→写作”边界，不表示要新增第二个 Worker，也不允许 writer 绕过 Pack 直接检索、查私有库或联网。

### 3.2 页面输入与章节调度

创建任务时必须输入：

- 企业全称。
- 授信类型：贸易融资、流动资金贷款、固定资产贷款、项目贷款。
- 用户拟定授信方案：金额、期限、增信措施。

材料上传分组：

| 材料组 | 第一阶段格式 | 主要消费者 |
|---|---|---|
| 公司与行业材料 | PDF | 公司信用研究、行业研究 |
| 财务材料 | Excel + PDF，可混合 | 财务数据库、财务分析、数值回检 |
| 项目材料 | PDF + 预测 Excel | 第二阶段项目分析 |

调度规则：

- 所有授信类型运行公司信用、财务、行业和综合方案评价。
- 贸易融资按具体产品强化应收、预付、存货等相关科目。
- 流动资金贷款追加流动资金需求测算。
- 固定资产贷款/项目贷款在第二阶段追加项目分析。
- 公司和行业研究优先使用用户材料，可调用外部公开信息。
- 财务分析以用户材料和结构化数据库为主；若必须使用外部数字，必须单独标识来源和口径。
- 项目分析禁止外部互联网检索。

主体核验在正式研究前执行：比较用户输入企业名称、材料内主体、股票代码和外部企业信息。水滴信用/企查查等商业 MCP 仅作为可选适配器，不构成单点依赖；可用性、授权和降级策略见 O-03。

---

## 4. 报告结构与 Section Contracts

机器可读 `SectionContract` v1 继续作为固定 hash 的历史兼容资产；R1-A 已发布并冻结兼容 Contract v2（52 问 / 187 aspects）及 SourcePolicy/WritingSpec/PresentationProfile。现行实现必须消费这些冻结资产，禁止原地覆盖 v1/v2 或让 Prompt/代码补出影子 Contract。本轮树结构调整只改变本地材料结构与定位，不改变 Contract 业务语义。

### 4.1 通用 Section Contract Schema

```python
@dataclass
class SectionContract:
    contract_version: str
    section_id: str
    title: str
    purpose: str
    required_topics: list[TopicContract]
    output_requirements: list[OutputRequirement]
    completion_rules: list[CompletionRule]
    evaluation_rules: list[EvaluationRule]
    allowed_capabilities: list[str]
    research_policy: str  # workflow | harness | conditional_harness
    missing_policies: list[MissingPolicy]
```

`[已确认]` 第一阶段目录和章节主题按本节执行；SC-01～SC-05 的 blocking 业务语义已固化。Contract v2 已细化 aspect/evidence/source/display/not_found，未重开已经确认的章节范围。

外部能力在冻结 Contract v2 中显式区分：`search_external_sources` 授权候选搜索，`fetch_external_content` 授权模型从允许候选中选择正文获取；fetch 成功后的 `snapshot_external_source` 仍是 Rules-internal 原子步骤，不暴露为模型动作，但必须受 fetch 授权、Registry、预算和审计约束。v1 只列 search，属于历史兼容限制；R4 以 Contract v2/SourcePolicy 为准接线。

### 4.2 公司信用研究

**目的**：确认申请授信的法人主体是否合法存续、控制权是否清晰、经营是否有效，以及其业务和经营能力能否支持还款。最终回答“这是一家什么样的企业、靠什么挣钱、主要信用风险是什么”。实际控制人用于判断控制权和治理风险，不等同于借款主体。

**已确认必含主题**：

1. 企业基本信息与历史沿革。基本信息至少包括成立日期、办公地址、法定代表人、注册资本、实缴资本和经营范围；历史沿革至少包括重大改革、股权变更、法定代表人变更、上市及重大募资事项。
2. 股权结构、控股股东及介绍、实际控制人及介绍、控制链条。控制链条需要生成可视化关系图。
3. 主要子公司、集团结构与重要关联方。
4. 主营业务、收入/成本/毛利构成、技术路线、采购/生产/销售模式、客户与供应商集中度，以及产业链位置和成本、销售、竞争能力。
5. 核心竞争力、研发能力、发展计划和在建工程。
6. 公司治理、管理层稳定性、内部控制和主要管理人员履历。
7. 重大诉讼、违约、处罚、关联交易、股权质押和舆情。
8. 公司层面的核心信用优势、风险及其偿债影响。
9. 债务情况，包括发债、金融机构借款和对外担保，使用结构化表格呈现。
10. 非主营业务和利润质量。若最新年度投资收益、公允价值变动、资产/信用减值、营业外收支等造成重大利润变化，说明金额、原因和可持续性。
11. 重大投资、收并购、资产出售、定向增发等影响经营的事件。
12. 股权激励计划及进展，分析其潜在现金流和治理影响。

**建议执行方式**：`Research Harness`。

**最低完成条件初稿**：

- 主体、股票代码、经营状态、报告时点必须明确，并检查输入企业名称与上传材料主体一致。
- 控股股东/实际控制人必须有证据；“无实际控制人”可以是合法结论，“控制关系无法确认”则转人工确认。
- 主营业务和主要收入来源必须有证据。
- 客户/供应商集中度、关联交易、债务和担保为必答项；未检索到时必须逐项明确写“未在给定材料及已执行来源中检索到”。
- 重大风险检查必须覆盖破产/失信、重大诉讼、逾期/违约、处罚、退市风险以及所属行业是否为淘汰/禁止类；即使无发现也要记录检索范围和截止日期。
- 每个关键事实绑定 Evidence ID；每个风险判断回指支持事实。

`[已确认 C-01/C-02]` 上述十二个主题构成第一版公司信用研究 Contract；客户/供应商集中度、关联交易、债务和担保均为必答。
`[已确认 O-03/O-04]` 企业信息 MCP 不可用时允许降级至交易所、国家企业信用信息公示系统等公开来源并提示；主体或控制关系异常时保留已有结果、阻止正式版并转人工确认。
`[待技术细化]` “未发现重大风险”仍需转化为可执行搜索清单、来源优先级、回溯期限和完成规则，仅列风险名称还不足以证明检索覆盖。

### 4.3 财务分析

**目的**：先判断报表是否可信，再以确定性数据和计算结果评价偿债、盈利、营运、现金流与增长质量，判断其能否支持用户提出的授信方案。

**已确认必含主题**：

1. 数据来源、口径、报告期、合并/母公司范围、单位、审计意见和会计师事务所。非标准无保留意见必须高亮；近三年更换事务所时核实原因。
2. 三张报表及附注的一致性。若同时存在审计报告、客户财务报表和征信报告，核对同一数字及债务余额；收入/成本构成表必须与利润表勾稽。
3. 资产负债结构、重大科目变化和科目组成。科目占资产或负债 15% 以上时强制分析；无论占比如何，至少分析应收账款、其他应收款、固定资产、在建工程、短期借款、长期借款、应付账款和其他应付款。
4. 短期与长期偿债能力、净资产水平和刚性债务结构。
5. 盈利能力和利润质量。比较应收账款与营业收入/总资产的匹配性、应收增速与营收增速，以及应收/应付账龄和坏账计提。
6. 营运效率，包括应收账款、存货和总资产周转。
7. 现金流结构与现金保障程度，判断经营现金流能否覆盖债务和贷款偿付。
8. 增长趋势、异常变动、可能原因和杜邦分析。
9. 非主营损益、减值、受限资产、商誉、开发支出等对利润和资产质量的影响。
10. 财务风险结论及其对当前授信方案的影响。

**执行方式**：`Workflow`，不进入自由研究循环。

**数据流**：

```text
Excel/PDF → 表格与附注抽取 → 标准科目/结构化明细 → SQLite
→ 来源间勾稽与一致性检查 → Python 指标/异常规则 → LLM 解读 → 数值回检
```

**最低完成条件初稿**：

- 所有报告数字必须来自结构化财务数据或 Python 计算结果。
- 每个数据库数字必须保留源文件、页码/Sheet、表格和单元格/行列坐标，支持回查源材料。
- Excel、审计报告 PDF、征信报告和补充表之间必须生成 reconciliation result；差异不能静默覆盖。
- 缺少分母、前期值或关键科目时不得计算对应指标。
- 章节必须声明数据口径和期间。
- 至少覆盖报表可信度、资产负债表、利润表、现金流量表和综合结论。
- 重要异常必须连接到原因证据；无法解释时标注待核实。

**按授信类型追加分析**：

- 流动资金贷款：测算流动资金需求，重点分析收入增长预测、营运资金周转天数、毛利率、存货、应收、预付、预收和应付。
- 贸易融资：按业务类型选择关键科目；例如国内保理重点分析应收账款和销售收入。
- 固定资产贷款/项目贷款：第一阶段仅从公司财务角度分析资本实力和现有项目现金流；第二阶段再运行独立项目分析。

`[已确认 F-01]` 保留 V1 指标，并增加有息负债、EBITDA、自由现金流、盈利质量和杜邦分析；正式实现前需逐项冻结公式与源科目。公式注册表支持某指标或明确返回 unavailable，不等于该指标必须出现在每份正文；required/optional/diagnostic/not_applicable 及展示位置由版本化 Contract/display policy 决定。
`[已确认 F-02]` 统一重大科目阈值由 20% 调整为 15%。
`[已确认 F-03]` 雪人股份样例仅作为分析深度参考，稳定结构为资产负债表、利润表、现金流量表和综合结论。
`[已确认 F-04]` 第一阶段不做完整同行业财务对标。重资产 70%、轻资产 40% 的资产负债率暂作为关注提示，是否为否决线见 O-06。
`[已确认 O-01/O-02]` 第一阶段只处理电子财务 PDF；多来源数字冲突时保留各来源值，生成 reconciliation issue 并转人工确认，不自动猜测口径。

#### 4.3.1 财务冲突的集中处理与最少交互

`[历史目标，当前面试版不交付交互闭环]` 下列 1～6 项保留为未来产品扩展设计和既有底层能力的审计依据。按照 §0.7，当前面试版只读展示冲突、缺项、来源、影响和建议材料类型，不提供选源提交、补充更正材料、确认后重算或用户续跑入口；未解决冲突继续阻止系统审核通过。

1. 自动完成单位标准化、期间/口径分离、重复上传识别和同口径一致性校验。不同合并范围、期间、币种或重述版本不能误当成可合并数据；同口径且校验一致的多份记录只计一次并保留全部来源。非零差异按已版本化的精度/舍入规则处理，未知精度或超出容差必须列为冲突，不能用财务重大性阈值掩盖差异。
2. 持续收集冲突，先完成不依赖这些冲突的解析、公司研究和行业研究。受影响指标不计算，依赖它的 Claim 不生成；预览明确标注待核实及影响范围，正式导出保持阻断。主体错误等影响整个任务的前提问题应立即明确提示，不能为了批量收集而继续使用错误主体。
3. 同一面板按报表、期间和口径分组，展示科目、各来源原值/标准值、文件与页码或单元格、差异及影响的指标/结论。优先展示影响大的组，其余可展开；底层全部冲突均保留。
4. 客户可对明确列出的同组条目批量选择某来源，选择适用的理由或填写说明，也可补充更正材料。批量选择必须逐条验证所选来源存在且口径一致；不适用项继续保留待确认。系统不默认选中冲突来源，不把选择扩展到未展示条目或未来上传文件。
5. 客户一次点击“确认并重新核验”，系统保存本次条目清单、采用/未采用来源、理由、时间和源文件哈希；补充材料路径记录新旧来源及重检结果。自动使受影响计算与下游 Claim 失效、重算和重生成，再运行完整 Assurance。无新问题不再次询问；新出现的冲突仍在同一面板处理。
6. 客户可暂不处理并查看带缺口的预览。确认仅解决来源选择，不等于免除勾稽校验或获得正式导出许可。已确认来源内容/口径变化时确认失效；只改变授信方案时重算相关分析，不要求重做未受影响的来源确认。

未来交互验收（不属于当前面试版门禁）：无冲突时零新增确认；同一批可处理冲突支持一次提交；独立章节可继续；新材料只使受影响确认失效；无法核实的问题明确说明，不能承诺所有任务只需一次交互。当前门禁只要求上述问题在状态栏和缺口面板中完整、可理解、可回查地展示。

### 4.4 行业研究

**目的**：评价行业环境如何影响公司的收入、盈利、现金流和偿债能力，而不是生成泛行业介绍。

**已确认必含主题**：

1. 行业定义、边界与公司所属细分领域。
2. 行业规模、增速和当前周期位置。
3. 供需关系、价格与成本驱动因素。
4. 竞争格局、集中度和主要参与者。
5. 政策、监管、技术替代和外部冲击。
6. 公司行业地位和相对竞争能力。
7. 行业风险向授信主体的传导路径。
8. 行业结论的有效期和监测指标。

**建议执行方式**：受预算限制的 `Research Harness`。

**最低完成条件初稿**：

- 行业边界和数据截止日期明确。
- 关键市场数据必须有来源、发布日期和统计口径。
- 至少完成一次公司与主要同业的相对比较。
- 结论必须落到借款人的收入、成本、资本开支或现金流。
- 事实与分析判断分开表达。

`[已确认 I-01]` 行业研究必须落到最相关的细分行业。多主营企业采用“整体行业 + 核心细分行业”；聚焦型企业以最细分产品为主体，同时保留必要的上位行业背景。
`[已确认 I-02]` 选择 3～5 家可比公司是研究目标而非完成门禁；不足 3 家时须说明限制并使用合理相近样本，无合理可比时明确不可比，禁止为凑数量选择不相关公司。可比口径、规模和竞争数据以核心细分行业为主。
`[已确认 I-03/O-10]` 用户材料优先，其他公开互联网信息可补充；外部来源优先级、关键结论最低来源和强时点日期门按 §0.2 O-09/O-10 与 §19.5 SC-03 执行，R1 将其固化为唯一版本化机器 policy。
`[已确认 I-04/O-05]` 新闻与行业规模数据默认使用近 2 年信息；2 年是默认检索回溯窗口，不是历史事实的硬失效线。

### 4.5 项目分析

`[已确认 D-01]` 本章保留在第二阶段设计中，第一阶段不开发、不进入默认报告。

**目的**：评价具体项目的合规性、建设与经营可行性、资金安排、现金流覆盖和风险缓释能力。

**建议必含主题**：

1. 项目主体、地点、用途和建设内容。
2. 审批、备案、土地、环评等合规状态。
3. 总投资、资本金、融资结构和资金用途。
4. 建设周期、关键节点与当前进度。
5. 收入、成本、产能、价格等核心假设。
6. 项目现金流、偿债来源和覆盖指标。
7. 敏感性分析与压力情景。
8. 完工、市场、运营、合规和融资风险。
9. 担保、抵质押、账户监管等缓释措施。

**已确认执行方式**：确定性 `Workflow` 负责抽取、计算和材料内检索。项目分析禁止互联网搜索，只能使用用户上传的项目材料；证据缺口直接列为待补充，不通过外部研究自动补齐。

**建议最小输入集**：

- 项目名称、项目公司、建设地点、项目类型。
- 总投资、资本金、拟融资额、期限、用途。
- 建设期和运营期关键时间表。
- 收入、成本、产销量、价格等预测 Excel。
- 项目批复/备案/环评等 PDF 材料。
- 还款来源、担保和抵质押信息。

`[已确认 P-01]` 不同项目采用不同材料清单。光伏项目参考材料包括项目公司营业执照、章程、可研报告、备案/批复、内部投委会材料、EPC、采购合同、能源管理合同、电网接入批复、并网确认和购售电合同。  
`[已确认 P-02]` 项目财务预测强制 Excel。  
`[已确认 P-03]` 第二阶段先计算 IRR 和盈亏平衡点，不计算 DSCR/NPV。  
`[已确认 P-04]` 压力情景预设经营现金流下降 20% 和 40%。  
`[已确认 P-05]` 仅在授信类型为固定资产贷款或项目贷款时启动项目分析。

### 4.6 综合方案评价

**目的**：把公司、财务和行业章节连接成完整信用逻辑，并评价客户经理输入的授信方案是否与企业经营、偿债能力和风险相匹配。第一阶段不由系统主动设计新的授信额度、期限或增信方案。

**已确认必含内容**：

1. 授信主体、授信类型和用户输入方案概览。
2. 支持该方案的核心优势。
3. 该方案面临的核心风险及风险传导。
4. 第一还款来源与现有增信措施的有效性。
5. 授信方案的优点、缺点和综合评价。
6. 若方案明显不合理，指出具体不匹配项和依据；否则不主动改写额度、期限和增信措施。
7. 仍未解决的信息缺口和需人工确认事项。
8. 明确声明“本评价由 AI 生成，仅供参考”。

**硬约束**：

- 只能使用已经通过章节质量门的 Claim。
- 每个综合判断必须回指一个或多个章节 Claim。
- 不得生成用户未提供的新授信方案。
- 不设置自创风险评级或评分模型。
- 涉及用户输入方案中的金额、期限、增信措施时，必须逐项引用输入或结构化事实。

`[已确认 S-01/S-02/S-03]` 第一阶段仅评价用户方案，不主动给出单一值/区间，不自创评级；除明显不合理外，输出方案优缺点和综合结论。

---

## 5. 核心数据模型

### 5.1 ReportJob

```python
@dataclass
class ReportJob:
    job_id: str
    company_id: str
    company_name: str
    credit_type: str       # trade_finance | working_capital | fixed_asset | project_loan
    proposed_scheme: CreditScheme
    template_id: str
    report_as_of: str
    status: str
    input_documents: list[DocumentInput]
    enabled_sections: list[str]
    created_at: datetime

@dataclass
class CreditScheme:
    amount: float
    currency: str
    term_months: int
    enhancement_measures: list[str]

@dataclass
class DocumentInput:
    document_id: str
    material_group: str   # company_industry | financial | project
    file_type: str        # pdf | xlsx
    declared_company_name: str | None
```

`[已确认]` 企业全称、授信类型和授信方案为创建任务时的必填输入。授信方案至少包含金额、期限和增信措施。系统必须核对输入主体、材料主体和公开企业信息是否一致。

### 5.2 EvidenceBlock

```python
@dataclass
class EvidenceBlock:
    evidence_id: str
    company_id: str
    document_id: str
    source_name: str
    source_type: str
    source_uri: str | None
    page_number: int | None
    section_path: list[str]
    evidence_type: str       # paragraph | table | table_row | heading | web
    text: str
    structured_payload: dict | None
    report_period: str | None
    published_at: str | None
    retrieved_at: str | None
    entities: list[str]
    quality_flags: list[str]
    content_hash: str
```

**Evidence ID 建议**：基于 `document_id + page + block_index + content_hash` 生成稳定 ID；文件重新解析但内容未变化时尽量保持稳定。

`EvidenceBlock` 的正式语义限于来源身份、原文内容、版本、页码/定位和内容哈希。旧集合中的 `section_path`、`evidence_type` 与切块边界是兼容元数据/弱提示，不能证明一个块只属于一个标题、一个表格或一个 Contract aspect，也不能直接作为 `covered`/`set_complete` 的依据。历史 Evidence 保持只读；若 canonical PageLayout 发现旧集合漏页、误跳页或无法精确映射，发布新的 append-only `evidence_set_version`，不得覆写旧块。

`[已确认 E-01]` PDF 表格保留表格坐标、行列头、原始单元格、单位和页码。  
`[已确认 E-02]` 外部网页至少保存支持 Claim 的正文快照、URL、标题、发布日期和抓取时间。  
`[已确认 E-03/O-07]` Evidence 按公司在本地独立资源库长期保留并保留多个版本；允许用户主动按公司或任务删除，但删除前必须检查最终报告引用关系。

### 5.2.1 PageLayout、DocumentOutline 与正式材料单元

```python
@dataclass(frozen=True)
class PageLayout:
    layout_id: str
    document_id: str
    document_version: str
    parser_version: str
    pages: tuple[LayoutPage, ...]       # 行、阅读顺序、bbox、字体/字号/粗体、表格区域
    content_fingerprint: str

@dataclass(frozen=True)
class DocumentOutline:
    outline_id: str
    document_id: str
    document_version: str
    layout_id: str
    outline_version: str
    root_node_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    unassigned_span_ids: tuple[str, ...]
    dependency_fingerprint: str

@dataclass(frozen=True)
class OutlineNode:
    node_id: str
    outline_id: str
    parent_node_id: str | None
    level: int
    title: str
    normalized_title: str
    ordinal_path: tuple[str, ...]
    child_node_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    table_object_ids: tuple[str, ...]
    heading_anchor: SourceLocator
    navigation_synopsis: "NavigationSynopsis"
    confidence: str
    quality_flags: tuple[str, ...]

@dataclass(frozen=True)
class EvidenceSpanRef:
    evidence_id: str
    char_start: int
    char_end: int
    alignment_record_id: str
    normalization_version: str

@dataclass(frozen=True)
class OutlineSpan:
    span_id: str
    outline_id: str
    node_id: str | None                  # None 仅用于显式 unassigned
    evidence_refs: tuple[EvidenceSpanRef, ...]
    page_start: int
    page_end: int
    content_hash: str
    role: str                         # heading | narrative | list_item | caption | footnote

@dataclass(frozen=True)
class TableObject:
    table_object_id: str
    outline_id: str
    node_id: str
    component_span_ids: tuple[str, ...]
    title: str | None
    unit: str | None
    header: tuple[tuple[str, ...], ...]
    rows: tuple[tuple[str, ...], ...]
    totals: tuple[tuple[str, ...], ...]
    continuation_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    content_hash: str
    quality_flags: tuple[str, ...]

@dataclass(frozen=True)
class NavigationSynopsis:
    status: str                         # available | unavailable
    text: str
    source_span_ids: tuple[str, ...]    # 抽取式简介的原文范围
    algorithm_version: str
    reason_codes: tuple[str, ...]
    content_hash: str

@dataclass(frozen=True)
class AspectNavigationProfile:
    profile_id: str
    contract_fingerprint: str
    aspect_id: str
    query_terms: tuple[str, ...]
    source_intents: tuple[str, ...]
    algorithm_version: str
    vocabulary_version: str
    content_fingerprint: str
```

以上是稳定业务语义，具体字段可在树结构任务书的获批实现计划中版本化细化，但不得缩减以下不变量：

- `PageLayout` 从原始电子 PDF 或同一 canonical layout 源构建，不从旧 `section_path` 反推；无文本层/低质量文档继续 fail fast，不启用 OCR。
- `DocumentOutline` 是只读、版本化派生物。PDF bookmark/目录只产生候选；正文全页大小标题、小标题、编号连续性、字体、缩进和坐标负责确认。一个节点默认延伸至下一个同级或更高层标题。
- 同名标题按完整路径和来源坐标区分；低置信或无法归属的正文进入显式 `unassigned`，不得静默丢弃。
- 一个 Evidence 可以被多个不重叠 `OutlineSpan` 引用，一个 span 也可以按源顺序引用多个 Evidence ranges；一个节点可以聚合多个 span。span 必须无损回指 Evidence 内容和字符范围，禁止越界、自报或重写原文。旧 Evidence 未覆盖的原文必须先进入新的 append-only evidence set，不能伪造 offsets。
- PageLayout 原始文本与 Evidence 规范化文本之间必须保存版本化 alignment 记录；alignment 不能唯一确认时 fail-closed 或发布新的 append-only evidence set，禁止猜测字符偏移。
- `TableObject` 将表题、单位、物理表头、表体、合计、续表与文字说明分开；通过 `introduces`、`explains`、`continued_by`、`footnote_of`、`references`、`reconciles_with` 等 typed relation 组合。
- 每个可导航节点必须有确定性、抽取式且可回溯的 `NavigationSynopsis`，例如由节点标题、子标题及有界原文句生成；无法生成时必须记录 `status=unavailable` 与原因。简介只能用于候选导航，不能成为 Citation、SupportedFact、coverage 或 set_complete 依据。
- `AspectNavigationProfile` 只能由冻结 Contract 字段和公司无关、版本化的通用词汇规则确定性派生；不得内置公司名、固定页码、答案关键词或另造业务要求。profile、词汇和排序算法版本必须进入依赖指纹并写入检索审计。
- layout/outline/span/table/parser/index 版本进入依赖指纹，任一变化使相应索引与 Pack stale，不得静默复用。

### 5.3 InformationNeed 与 RouteDecision

```python
@dataclass
class InformationNeed:
    need_id: str
    section_id: str
    question: str
    required_evidence_types: list[str]
    required_source_types: list[str]
    time_scope: str | None
    priority: str
    depends_on: list[str]

@dataclass
class RouteDecision:
    need_id: str
    route: str  # db_lookup | direct_evidence | standard_rag | deep_retrieval | external_research
    reason_code: str
    filters: dict
    budget: dict
    fallback_routes: list[str]
```

`[已确认]` Router 第一阶段优先使用规则分类；只有规则无法判定时才调用轻量 LLM Router。Router 必须返回结构化结果和 reason code。规则判定范围见 §7.1。

### 5.4 EvidencePack

```python
@dataclass
class EvidencePack:
    need_id: str
    route_decision: RouteDecision
    evidence: list[EvidenceRef]
    unresolved_conflicts: list[str]
    missing_requirements: list[str]
    retrieval_trace_id: str
```

### 5.4.1 TopicResearchPack（P3→P4 正式交付）

`EvidencePack` 回答“一次 InformationNeed 找到了什么”；`TopicResearchPack` 回答“一个正式 Topic 为写成完整章节已经研究了什么、还缺什么”。它由 Harness 所有并持久化，是 P4 公司/行业 Worker 的正式内容输入。

> 以下 dataclass 为**历史示意字段名**：稳定不变量见本小节下方硬规则；唯一强类型 schema 以 `R1B_IMPLEMENTATION_PLAN.md` §1（落点 `harness/topic_schema.py`）为唯一规范，本文不维护第二份完整 schema。本处已把历史松散字段（裸 `locator` / `content_or_payload_ref` / `authority_status` / `source_authority` / `value_identity` / `external_funnel` / `budget_policy` / `cumulative_usage`，原为 `dict`/`str` 形态）替换为强类型引用，并对 Pack 补上 process/coverage 双轴状态，避免与 R1-B 唯一 schema 冲突。

```python
@dataclass
class AspectResearchResult:
    aspect_id: str
    question_ids: list[str]
    requirement_text: str
    priority: str
    evidence_requirements: list[str]
    status: str                 # covered | partial | not_found | blocked | not_applicable
    supported_fact_ids: list[str]
    material_ids: list[str]
    attempted_need_ids: list[str]
    unresolved_ids: list[str]
    not_found_audit_id: str | None

@dataclass
class ResearchMaterial:
    material_id: str
    material_type: str          # outline_span | table_object | structured | external_snapshot
    source_identity: str
    locator: MaterialLocator                       # 按 material_type 区分的严格联合类型（见 R1-B §1）
    payload_ref: MaterialPayloadRef                # 不可变解析引用（替代裸 content_or_payload_ref）
    context_parent_id: str | None
    content_hash: str
    authority_assessment: AuthorityAssessment      # 三类来源权威联合类型（替代裸 authority_status）

@dataclass
class SupportedFact:
    fact_id: str
    text: str
    fact_type: str
    aspect_ids: list[str]
    citation_refs: list[CitationRef]
    source_authority: AuthorityAssessment   # 三类来源权威联合类型（替代裸 source_authority）
    value_identity: ValueIdentity | None    # 规范化数字语义（value_kind/metric/unit/period/scope/amount_canonical）
    semantic_tags: list[str]
    period: str | None
    scope: str | None
    confidence: str

@dataclass
class ResearchConflict:
    conflict_id: str
    fact_ids: list[str]
    category: str
    detail: str
    status: str

@dataclass
class NotFoundAudit:
    audit_id: str
    aspect_ids: list[str]
    policy_version: str
    required_source_scope: list[str]
    attempted_source_types: list[str]
    valid_attempt_count: int
    searched_need_ids: list[str]
    context_expansion_attempted: bool
    alternative_candidate_ids: list[str]
    alternative_sources_attempted: list[str]
    time_window: dict
    unattempted_candidate_ids: list[str]
    budget_exhausted: bool
    qualification_reasons: list[str]
    qualified: bool

@dataclass
class ResearchGap:
    unresolved_id: str
    aspect_ids: list[str]
    reason_code: str
    detail: str
    attempted_need_ids: list[str]
    blocking: list[str]
    impact: str
    not_found_audit_id: str | None

@dataclass
class TopicResearchPack:
    schema_version: str
    pack_id: str
    run_id: str
    task_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: list[str]
    aspect_results: list[AspectResearchResult]
    materials: list[ResearchMaterial]
    facts: list[SupportedFact]
    outcome_refs: list[str]
    external_funnel: ExternalFunnelSnapshot | None
    conflicts: list[ResearchConflict]
    not_found_audits: list[NotFoundAudit]
    unresolved: list[ResearchGap]
    usage: TopicUsageSnapshot                     # budget_policy + cumulative_usage + stop_reason（typed）
    process_status: PackProcessStatus             # pending|running|finished|stopped_by_budget|blocked|failed
    coverage_status: PackCoverageStatus           # complete|complete_with_gaps|insufficient|unavailable
    status_derivation: StatusDerivation
    dependency_fingerprint: str
```

硬规则：

- `pack_id` 由规范化业务内容和依赖指纹派生，不含时间戳、call_id 或模型隐藏推理；同输入同内容幂等复用，内容或依赖变化产生新版本。
- `schema_version/company_id/report_as_of/contract_version/contract_fingerprint/source_policy_version/task_id/topic_id` 是显式身份，不得只藏在不透明的 dependency hash 中；P4 必须逐字段校验后再消费。
- 搜索结果的 title/URL/snippet 仅是候选导航；只有经过 inspect 或 fetch→snapshot、并通过权威校验的正文/结构化记录才能进入 `materials` 和 `facts`。
- 一个材料可以支持多个 aspect，一个 aspect 也可以由多个材料共同支持；不得把“一条命中”机械等同于整个 aspect covered。
- 本地文本一律以 `outline_id/node_id/span_id/table_object_id` 定位最小充分节点/子树。即使进入相邻块、同页/跨页或明确引用 fallback，结果也必须形成带精确来源范围的 `OutlineSpan`（低置信或无标题内容进入 `unassigned`），不得把整个 `EvidenceBlock` 提升为正式材料类型。fallback 原因、范围、未读结构和 outline 置信状态必须进入审计字段；fallback/unassigned 材料可以支撑候选事实，但不得单独使集合型 aspect 达到 `set_complete`，缺少已验证 outline/table 边界时保持 `boundary_incomplete` 或 `partial`。
- 财务 `FinancialFactPack` 保持独立权威来源，但对 P4 暴露与 `TopicResearchPack` 可组合的只读事实视图；PDF 附注 Evidence 事实不得伪装成 FinancialSnapshot 事实。
- 所有可能进入报告的数字统一投影为可查询的 `SupportedFact.value_identity`/财务事实只读视图，供各 Topic 复用；这是一层统一 Fact Registry 读模型，不是把 FinancialSnapshot、Evidence 附注、授信/担保/研发和外部数据强行写进同一权威表。来源类型、原始定位、期间、单位、scope 与语义类别必须保留，LLM 不能把不同权威或口径的同值互换。
- `AspectCoverageResult` 和 `ExternalFunnelProjection` 可以从 Pack 派生或作为其审计字段，但不能替代 Pack 的材料、事实、预算和未解决项。
- aspect 级 `not_found` 只有在对应 `NotFoundAudit.qualified=true` 时成立，并向历史 KeyQuestion 状态投影为 `NOT_FOUND_AFTER_SEARCH`；不得机械继承原子 outcome。预算耗尽、存在未合理尝试候选或未达到来源/扩读/替代策略时只能是 `partial + ResearchGap`。
- `TopicResearchPack` 的**研究流程状态**（`process_status`）与**内容覆盖状态**（`coverage_status`）是两个正交维度，不得用一个含混 `status` 同时表达。一个原子 `ResearchOutcome=COMPLETED` 只能结束当前 need，不能结束 Topic；只有完整 required aspect 集合都进入按 Contract 允许的合法终态后流程才可 `finished`，内容是否完整/是否有缺口由 coverage 轴单独表达（`not_found` 是 aspect 层证据结果，不是 Pack 流程状态）。
- 所有嵌套对象 unknown-field fail-closed；Writer 只能消费与 `SectionTask.topic_ids` 完全匹配、身份逐字段一致的 Pack 集，缺 Pack 必须显式 gap/block。
- `ResearchMaterial` 与 `SupportedFact` 是**并存双层**（§0.12）：材料保留来源里客观存在的内容，事实只承载可独立判断真假的完整命题；拒绝一个事实候选**不得**删除、裁碎、改写或降级其原材料，完整 payload、标题路径、locator 与 provenance 一律保留。
- 从材料到**高风险事实**必须经 versioned `FactQualificationDecision`（`ResearchMaterial` → `FactCandidate` → `decision{eligible → SupportedFact | rejected → typed rejection decision / audit}`）；拒绝决定必须类型化、版本化、可审计，理由码封闭，**不得**写进 `semantic_tags`、`detail` 自由文本或泛化 `validator` 字符串。**拒绝必然产生 typed rejection decision/audit，并且不自动产生 gap**；只有因该拒绝导致 Contract 必需 aspect/fact 仍未满足时，才**另外**形成 gap/block（§0.13 第 9 条）。这是**通道 A**，且是**非穷尽**的：它只覆盖金额、比率、日期、期间、币种、单位、财务数字、授信额度金额与语义类型、勾选状态、表行/列关系、法律实体、明确否定等 Contract 高风险硬事实，**不负责**预先拆完整篇文档（§0.13）。
- **通道 B（写作侧候选）**：Writer 从完整材料提出描述性 `ClaimCandidate`（候选叙述 + 原子候选 + 拟用 support edges + 精确 material/locator），但**不得**自批、**不得**回写历史 Pack、**不得**绕过通道 A 夹带新的硬事实、**不得**自评。`ClaimCandidate` 经确定性 **Claim Binding Gate** 与 **Section Evaluator** 的原子蕴含核验后才转为正式 `SectionClaim`；`ResearchMaterial`、`FactCandidate`/`SupportedFact`、`ClaimCandidate`/`SectionClaim` 是三种不同身份与不同持久化对象，纯算法可复用但 **wire 不得混用**（§0.13）。
- 若 current Pack wire **无法完整持久化** `FactCandidate → FactQualificationDecision → qualification result（eligible → SupportedFact；rejected → typed rejection decision/audit）`（现行 wire 正是如此，逐字段论证见实施计划 §6.2.1），则以下三项**已经确定**：需要 **append-only Pack successor**；需要 schema version 升级与显式的迁移、历史读回规则；**qualification policy version 必须进入 Pack 的 dependency fingerprint**，以确保旧资格结果不能被新政策静默解释。`ContractGap` 是基于 required aspect/fact 是否仍未满足而条件产生的独立记录，不是 qualification result 的联合分支。
- **尚待编码前裁决（本节不预先裁定）**：具体采用「新增独立 dependency key」还是「复用经批准的既有 schema/runtime version axis」，**必须**在 file-level successor changelist 与**全仓影响清点**之后裁决。本节**不**预先授权 exact-set dependency keys 扩展，也**不**假设“增加一个键没有迁移影响”。无论采用哪种方式，都**不得**把决定塞进 `semantic_tags`、gap 的 `detail` 自由文本或泛化 `validator` 字符串，且必须按实施计划 §10.3 提交 before/after wire、迁移范围、历史读回、版本真值表与回滚影响。
- 下列形态不得单独晋级为 `SupportedFact`/`SectionClaim`：孤立标题、字段标签、表头、单位；版式残片；未解析的“适用/不适用”勾选项；只有披露规则而无公司事实的规范性文字；缺必要表头/表体/单位/期间的表格片段；Contract 要求期间或必要字段但尚未闭合的候选（完整清单与说明见 §0.12）。
- Writer 的 material context 由**精确集合**定义，不由相关性或相似度判断（§0.12）：company/industry Writer 的材料集合必须**精确等于** `VerifiedPackSet` 中所有 current Pack 的 `materials` 并集，且 `VerifiedPackSet.topic_ids` 与 `SectionTask.topic_ids` 精确相等；缺件、多件、旧版、错公司或错时点任一即 block。
- 该集合必须固化为确定性、版本化的 **material-context manifest**，至少记录：task/report/company/Contract 身份；Pack 身份与版本；`material_id`；payload/content fingerprint；source/provenance locator；排序规则；集合指纹。排序稳定为 `topic_id → pack_id → material_id`，**不得依赖数据库返回顺序**。
- Writer、assembler 或 LLM **不得**自行挑选“方便的”“看起来最相关的”材料再把章节表示为完整。
- **材料集合分四层，禁止把“未使用”误判为 gap**：① **available material set（可用集合）** 必须精确等于 `VerifiedPackSet` 中所有 current Pack 的 `materials` 并集，每个成员都必须进入 material-context manifest；② **processed material set（已处理集合）**：每个 manifest 成员都必须有确定性的处理状态，**不得静默遗漏**，任何成员**不得从 manifest 中消失**；③ **used material set（实际使用集合）** 可以是可用集合的**子集**，但每个被使用的成员必须被标注为 `factual support` 或 `context support`；④ **not-used material（未使用材料）** 是**允许**的，但必须带 typed 理由：重复 / 与本节表达目标无关 / 候选资格未通过 / 已被更完整材料覆盖 / 预算分区后未选中。**“未使用”本身不是 gap**；只有 payload 缺失、身份错误、必需材料不可得，或 Contract 必需事实未取得，才形成 gap/block。
- 若未来为上下文预算做分区：必须先对完整集合生成 manifest，使用确定性、版本化的分区计划。每个 manifest 成员必须有可复核 `WriterMaterialProcessingDisposition` 并回指其 Pack 侧 `ResearchMaterialDisposition`；预验证事实是否进入叙述另由 `FactNarrativeDisposition` 记录。三者不得混用，任何材料不得从 manifest 静默消失。
- 材料上下文提供语境、组织与连贯性输入，也是 Writer 提出 `ClaimCandidate` 的依据。**factual support 有两条合法路径**（§0.12 真值表）：路径 A 落到预验证权威（合格 fact、财务权威、evidence note 权威或外部权威），**任何已具有合法预验证事实身份的原子断言都可用**；路径 B 落到 exact material/payload/locator 加 `ClaimBindingDecision` 与独立 `ClaimEntailmentDecision`，**只用于非高风险的描述性原子断言**。**高风险硬事实只允许路径 A**；材料上下文不得绕过预验证产生新的数字、实体、期间或结论（§0.13）。
- **Writer 不检索、不联网**：Harness 唯一拥有 `InformationNeed → Router → ToolRegistry`、研究调度、预算、checkpoint、外部漏斗与权威 Pack。Writer 需要更多时只能发出结构化 `FollowUpNeed`，由 Harness 判定 Contract/budget/SourcePolicy、执行追加研究或网络调用、形成新 Pack，Writer 再做有界重写。外部事实仍需 fetch、非空正文、不可变快照、SourcePolicy、日期与 locator；"网络"是一种来源类型，不是"非事实"。
- **支撑边按 authority kind / semantics / role / authorization path 四条正交轴校验**。所有变体都有 stage/target、container、source/provenance、citation/fingerprint；authority-specific payload/locator 按 §0.12 第 6 条的 required/optional/forbidden 表逐项校验。Topic 可走预验证 fact 或 exact material path B；financial/note/external 禁止伪造 Topic material。外部路径 A 必须绑定 formal `ExternalFact`（资格决定、snapshot/body hash、SourcePolicy、日期、命题、locator），snapshot 单独不授权。`ClaimSupportRef` 只是 `ProposedSupportRef | AcceptedSupportBinding` 的兼容 union，不是第三种 wire；两阶段唯一字段表见 Demo 实施计划 §6.3。
- **材料 disposition 必须持久化为两个版本化正式对象，字段集合不同**：研究/Pack 侧 `ResearchMaterialDisposition` 绑定 material/Pack/aspect identity、research admission/retention、source/authority validation、provenance、typed research reason、proof、policy/schema/fingerprint，并且**禁止** Writer 的 used/not_used、factual/context usage 与 section partition；写作侧 `WriterMaterialProcessingDisposition` 绑定 task/section/manifest/material identity、processed/used/not_used、used 时的 factual/context usage、not_used 时的 typed reason+proof、适用的 partition/binding/alternative-material refs、policy/version/fingerprint，并且**禁止**改写研究资格或 Pack。二者不得共用含混 disposition identity；每个写作侧记录须回指匹配的研究侧记录。
- **not-used 理由不得由 Writer 自报即成立**：`irrelevant_to_section_goal`、`covered_by_more_complete_material`、`not_selected_after_budget_partition` 三类理由**必须**由版本化策略确定性重算，或被独立 gate 核验其证明；无 proof/policy binding 的 not-used reason 一律拒绝。预算分区不得让材料从 manifest 静默消失。
- **required fact 未取得时不得只写 not-used**：Contract 必需的 fact candidate 资格失败或未取得时必须形成 gap/block；把它记成普通 `not_used` 属于伪造完整性，一律拒绝（反例见实施计划 §6.7.1）。
- **gap 与 disposition 正交**：材料可以既标 `not_used` 又因 required fact 未取得而产生 Contract gap；两者不得互相抵消。

### 5.5 Claim 与 Citation

```python
@dataclass
class Claim:
    claim_id: str
    section_id: str
    text: str
    claim_type: str          # fact | calculation | inference | recommendation
    evidence_ids: list[str]
    derived_from_claim_ids: list[str]
    confidence: str          # high | medium | low | unresolved
    as_of_date: str | None
```

```python
@dataclass
class Citation:
    evidence_id: str
    source_name: str
    page_number: int | None
    snippet: str
```

```python
@dataclass
class NarrativeParagraph:
    paragraph_id: str
    section_id: str
    topic_id: str
    paragraph_role: str        # overview | fact_pattern | analysis | risk_implication | limitation
    text: str
    supporting_claim_ids: list[str]
    citation_ids: list[str]
```

`Claim` 是最小可审计断言，`NarrativeParagraph` 是面向客户经理的表达单元。段落可合并多条已支持 Claim 并增加不创造事实的衔接与分析，但每个事实句和数字仍必须能回指 Claim/Citation；Renderer 不得直接把 Claim 列表逐条打印成报告。

Writer 产出的**不是**已接受 Claim，而是相互分离的 `ClaimCandidate[]`、`NarrativeDraftUnit[]` 与 `ProposedSupportRef[]`。一个候选只表达一个原子断言；候选 identity 不含 material/locator，所有拟用 authority/material/payload/locator 都在 proposal 中。三者共同构成门前 `SectionDraft`，它是 gate/Evaluator 输入，**不是 `SectionResult`，也不得引用 future result/decision/claim IDs**。Claim Binding Gate 对每个 subject revision 的完整有序 proposal set 产出恰好一个聚合 `ClaimBindingDecision`；factual candidate 再由 Section Evaluator 产出恰好一个绑定同一 set digest 的 `ClaimEntailmentDecision`。每条通过的 factual proposal 形成 `AcceptedSupportBinding`，随后 `SectionClaim` 引用 accepted-binding IDs；context proposal 以 `NarrativeDraftUnit` 为 target，只经过机械门，形成无 entailment decision 的 context accepted binding。最终 Narrative 才引用 accepted claims/context bindings，门后 `SectionResult` 单向引用 `section_draft_id`。所有对象严格无环，任何对象不得把自身或后继 ID 计入自身 content identity（§0.13）。

支撑语义分两类，不得混用（§0.12）：

```text
factual support（路径 A，预验证权威）
    任何已具有合法预验证事实身份的原子断言；高风险硬事实的唯一合法路径
    SectionClaim → SupportedFact / FinancialFact / Evidence note fact /
                   ExternalFact + ExternalSnapshot source carrier
                 → authority-specific container + source/provenance + payload/exact locator
    （只有 topic_pack 分支要求 ResearchMaterial；不得给其他 authority 伪造 material）
factual support（路径 B，材料派生描述断言；只用于非高风险描述性原子；不要求预存 SupportedFact）
    ClaimCandidate → exact ResearchMaterial / payload / exact locator
                   + ClaimBindingDecision（机械） + ClaimEntailmentDecision（语义）
context support: NarrativeDraftUnit → authority-specific context source
    （不要求 fact_id，也不要求 ClaimEntailmentDecision；不得 target Claim；永远不能授权 factual Claim）
```

`factual support` 是唯一能授权事实性断言的支撑（两条路径都必须显式声明 `support_semantics = factual`）；`context support` 只用于恢复语境和组织叙述，**永远不能授权 factual Claim**，也不得在写作阶段被临时升级成事实。Topic Pack context 只能引用 exact material manifest 中的材料；其他 authority 的 context 必须符合 §0.12 封闭表规定的 container/provenance/payload/locator，不能为统一字段伪造 Topic material。

**每条 support edge 必须显式声明 typed role（`factual` / `context`）**：`fact_id=None` 对**已声明为 `context`** 的 edge 合法；对**路径 B 的 factual edge** 也合法，但必须以显式 authorization basis 标注为材料派生路径，并绑定 exact material/payload/locator 与两道独立决定；对**路径 A 的 factual edge** 一律拒绝。角色缺失、歧义或与 Claim/Narrative 类型不相容时 fail-closed。合法 `context support` **不得**因“没有 `fact_id`”被误杀——它只被禁止引入新的数字、实体、期间、因果与结论；路径 B 同样不得因缺少 `fact_id` 被误判为 context 或被一律拒绝。

人读叙述的最小持久化单元是 `NarrativeSentence`：它必须保存最终 sentence text、所绑定的 `claim_ids`、`citation_ids`、**每条支撑边的 `authority_kind` / `support_role` / `support_semantics`**，以及本句使用的 context material IDs，且一句话可以绑定多条 Claims。Writer 可以合并、排序与改写，但不得增加 Claims/材料中没有的事实、数字、实体或期间。

该对象必须由确定性门检查身份、数字、期间、材料与引用，并由**独立**章级 Evaluator 做 **role-aware entailment** 检查。检查对象**不是**“句子是否被 Claims/materials 蕴含”——该表述可能被读成 OR（材料也能证明事实），必须替换为：

- 每个**事实性原子**必须由**合格 Claim/fact 及其 factual support edge**蕴含；材料派生的描述性断言必须由**路径 B**（指定材料 + 机械门 + 独立蕴含决定）授权，且**只限非高风险描述性原子**；**高风险硬事实只允许路径 A**，其原子必须**各自**绑定预验证权威——一个 `ClaimCandidate` 仍只能表达一个原子断言，混合自然句必须拆成多个候选（§0.12 原子性规则）；
- **context material 只能**验证背景、结构、衔接与“未新增事实”；
- **context material 不能补足缺失的合格 fact**；
- 修改 sentence text 而 bindings 不变，或修改 bindings 而 text 不变，都必须**改变身份**并触发重新核验；
- Writer **不得自评**通过。

**Section Evaluator 是 Claim 级原子语义核验的唯一位置**：不再设重复的 Claim Reviewer Agent。Evaluator 与 Writer 分离调用/上下文，除上述蕴含判定外，还覆盖主体/期间/范围/极性误读、材料中不存在的过度概括与条件改变、Contract 覆盖与必需缺口、重复/结构/信息密度/可读性，并输出有界返工目标；通过才把 `ClaimCandidate` 转为正式 `SectionClaim`，不通过只允许有界返工、降级为非事实过渡、unresolved 或 Contract gap/block。它**不重复** Claim Binding Gate 的 ID/hash/locator 检查，也不宣称全报告通过。任何 LLM 辅助蕴含必须与 Writer 隔离、版本化、读取候选 + 对应完整材料 + support edges、只输出结构化决定、不改写。

| 例 | 构造 | 判定 |
|---|---|---|
| 反例 | 句子的事实性原子只被 context material 支持，没有 factual support（路径 A 或路径 B 都没有） | **拒绝** |
| 反例 | 高风险硬事实原子（数字/日期/期间/币种/勾选/明确否定等）只有路径 B 的材料派生绑定，没有预验证权威 | **拒绝** |
| 正例（路径 A） | 全部事实性原子均由合格 facts/权威及其 factual support edge 覆盖，context 仅辅助衔接 | **通过** |
| 正例（路径 A） | 非高风险原子断言绑定已通过预验证的 fact 身份 | **通过**（路径 A 不限于高风险事实） |
| 正例（路径 B） | 描述性断言由 exact material/payload/locator + 机械 `ClaimBindingDecision` + 独立 `ClaimEntailmentDecision` 支持，且不引入任何高风险硬事实 | **通过**（不得因缺少 `fact_id` 被误杀） |

把 Claim 列表逐条机械打印既不是人读表达单元，也不得作为内容门通过证据。

研究 Topic 是调度与审计单元，不等于最终报告小节。P4 必须增加版本化、公司无关的写作与展示规格：

```python
@dataclass(frozen=True)
class SectionWritingSpec:
    spec_version: str
    section_kind: str
    subsection_specs: tuple[SubsectionWritingSpec, ...]
    display_policy_version: str
    period_language_policy: dict
    citation_style: str
    soft_length_guidance: dict

@dataclass(frozen=True)
class SubsectionWritingSpec:
    subsection_id: str
    title: str
    topic_ids: tuple[str, ...]
    paragraph_roles: tuple[str, ...]
    table_specs: tuple[str, ...]
    required_content_roles: tuple[str, ...]
    optional_content_roles: tuple[str, ...]

@dataclass(frozen=True)
class ReportPresentationProfile:
    profile_version: str
    section_order: tuple[str, ...]
    section_writing_spec_versions: dict[str, str]
    front_matter_policy: dict
    reference_policy: dict
    appendix_policy: dict
```

- Contract 决定 Topic/aspect、证据、计算和缺口语义；WritingSpec 决定多个 Topic 如何合并成业务所需的小节（不与 Topic 数机械一一对应）、每个小节采用何种段落/表格和哪些内容必须展示；PresentationProfile 决定整份报告的章节顺序、前言、引用与附录。
- WritingSpec/PresentationProfile 必须版本化并进入 Section/report dependency fingerprint；不得包含公司名称、证券代码、固定事实、固定页码或 gold。
- P4 对一个 Section 的输入是与 `SectionTask.topic_ids` 完全匹配的一组 current Pack，而不是任意一个 Pack。缺少、重复、stale 或任务/公司/时点/Contract 指纹错配的 Pack 必须显式 `gap/block`。
- Prompt 只能执行已冻结的 WritingSpec，不能自行发明目录；旧 `templates/standard.md`、`templates/simple.md` 和 `publication_editor.txt` 不得成为 V2 影子 Contract/写作规格。
- 软篇幅用于控制信息密度，不作为截断或通过门；不得为了达成字数删除 required aspect 或关键风险。

`[已确认 C-04]` 关键主张强制引用；背景性描述允许段落级引用。

### 5.6 ResearchState

```python
@dataclass
class ResearchState:
    run_id: str
    section_task: SectionTask
    current_need_id: str | None
    completed_needs: list[str]
    unresolved_needs: list[str]
    evidence_packs: dict[str, EvidencePack]
    claims: list[Claim]
    tool_history: list[ToolCallRecord]
    errors: list[ResearchError]
    iteration: int
    token_used: int
    elapsed_ms: int
    stop_reason: str | None
    checkpoint_version: int
```

`ResearchState` 继续作为单个原子 ResearchOutcome 的兼容状态，但正式 Topic 研究必须增加由同一 Harness 管理的 `TopicResearchState`：保存正式 aspect 待办队列、已取得材料/事实、子 need 关系、每 aspect 尝试、Topic 级累计预算与 Pack checkpoint。它不是新的 Agent，也不得绕过现有 Router、ToolRegistry、Retriever、外部快照或引用权威校验。单题结束不代表 Topic 结束；只有全部必需 aspect 达到 `covered/not_found/blocked/not_applicable` 等可解释终态，或 Topic 硬预算用尽，才可提交 `TopicResearchPack`。

### 5.7 ProgressEvent 与 Checkpoint

```python
@dataclass
class ProgressEvent:
    event_id: str
    run_id: str
    stage_id: str
    section_id: str | None
    status: str                 # queued | running | retrying | waiting_user | paused | degraded | completed | failed
    message_code: str
    completed_units: int | None
    total_units: int | None
    tool_call_count: int
    retry_count: int
    elapsed_ms: int
    checkpoint_id: str | None
    recoverable: bool
    error_code: str | None
    created_at: str


@dataclass
class Checkpoint:
    checkpoint_id: str
    run_id: str
    stage_id: str
    state_version: int
    artifact_refs: list[str]
    input_hashes: dict[str, str]
    dependency_versions: dict[str, str]  # contract/schema/prompt/model/rules/index/financial_snapshot
    resolution_refs: list[str]           # 绑定来源版本的人工处理记录
    completed_unit_ids: list[str]
    created_at: str
```

`ProgressEvent` 服务于用户状态展示和运行监控；`Checkpoint` 只在对应产物已持久化且可复用后创建。页面百分比由真实完成单元计算，不由模型估计。

### 5.8 VerificationIssue

```python
@dataclass
class VerificationIssue:
    issue_id: str
    category: str
    severity: str
    claim_id: str | None
    location: str
    detail: str
    evidence_ids: list[str]
    repair_target: str       # assembly | section | synthesis | human
    blocking: bool
```

### 5.9 Schema 的决策归属

| Schema | 技术方可以决定 | 必须由你确认/补齐 |
|---|---|---|
| `SectionContract` | 字段命名、序列化格式、校验代码 | 章节目的、必答主题、最低证据、完成标准 |
| `EvidenceBlock` | ID 算法、存储结构、索引字段 | 需要保留的来源粒度、表格结构、网页快照要求 |
| `InformationNeed` | 内部 ID、依赖表示、优先级实现 | 从真实报告要求拆出的标准问题集 |
| `RouteDecision` | 路由字段、reason code、fallback 实现 | 通常不需要逐字段确认；只需确认外部研究边界和成本限制 |
| `EvidencePack` | 排序、去重、压缩和 trace 字段 | 关键结论所需的最低来源数量/类型 |
| `TopicResearchPack` | 稳定 ID、材料/事实结构、持久化、预算和审计字段 | required aspect 的业务含义、最低证据与可接受缺口 |
| `Claim/Citation` | ID、图谱关系和渲染方式 | 哪些陈述强制引用、引用显示粒度 |
| `NarrativeParagraph` | Claim 映射、段落身份和渲染实现 | 章节表达深度、哪些风险判断必须显式呈现 |
| `ResearchState` | 状态字段、checkpoint 和恢复机制 | 最大研究轮数、预算、是否允许动态追加问题 |
| `ProgressEvent/Checkpoint` | 状态枚举、事件存储、恢复和幂等实现 | 用户可见阶段名称、哪些异常必须等待人工处理 |
| `ToolResult` | 错误码、状态值和通用返回封装 | 通常无需确认；工具可访问的外部数据边界需确认 |
| `EvalCase` | 文件格式、runner 和指标实现 | gold Evidence、必含/禁止结论、人工 rubric |
| `VerificationIssue` | 内部结构和修复路由 | 哪些问题属于 blocking、何时必须人工确认 |

结论是：你不需要亲自设计每个 Python 字段；你需要定义并确认这些字段背后的**业务语义、合格标准和责任边界**。

---

## 6. Evidence Architecture

### 6.1 处理流程

```text
RawDocument
  → 文件识别与归属校验
  → canonical PageLayout（全页行、坐标、样式、阅读顺序、表格区域）
  → bookmark/目录候选 + 正文全量大小标题/小标题 + 编号/版式对齐
  → versioned read-only DocumentOutline
  → EvidenceBlock 来源锚点 + OutlineSpan / TableObject 派生
  → 实体、期间、来源元数据与对象关系绑定
  → 质量检测
  → append-only Structure Store + outline-aware 检索索引
```

构建器不得因为某行包含“目录”两个字就跳过整页；目录识别必须使用页面结构，并保留目录页作为候选来源。正文页始终参加标题锚点与内容覆盖检查。每个非空正文范围必须落入可信 Outline 节点、`TableObject` 或显式 `unassigned`，并输出未映射原因；不得以标题识别失败为由静默删除内容。

财务材料使用额外分支：

```text
Excel / 电子 PDF / 审计报告附注 / 征信报告
  → 表格与字段抽取
  → SourceFinancialRecord（保留原值、单位、口径、坐标）
  → 标准科目映射
  → 同源勾稽 + 跨源 reconciliation
  → 通过校验的 Financial Store
  → 冲突项进入人工确认或 Report Assurance
```

第一阶段不以“多种 LLM 对同一数字投票”作为主要保障。优先顺序为：确定性表格抽取与公式校验、原表勾稽、跨来源对账、源坐标回查；LLM 只用于表头/科目语义映射或低置信度辅助，并且其结果必须通过规则或人工确认后才能成为报告数字。

财务存储必须分为不可覆盖的原始来源记录、对账/人工处理记录、获准计算的 FinancialSnapshot 三层。快照绑定公司、期间、合并范围、币种、来源及重述版本；相同科目不同来源不是可相加的明细。指标接口必须显式绑定快照，禁止沿用 V1 对所有来源求和的查询作为 V2 计算依据。计算结果保留公式版本、输入记录引用和缺失值原因。

电子 PDF 表格抽取需要独立的表格/单元格坐标输出契约；现有仅含文本、页码和节段的 TextChunk 不能补出丢失坐标。先验证三张报表和必要附注的抽取、勾稽与重复上传，再扩充指标；无法可靠抽取时提示补充 Excel，不退回普通 RAG 取数。

### 6.2 V1 兼容策略

- 保留 `parsers.pdf_parser.parse()` 作为底层文本解析入口。
- 历史 `TextChunk → EvidenceBlock`、Evidence ID、Evidence Set、旧索引和既有运行产物只读保留；旧 `section_path` 不升级为正式标题树。
- 新增 PageLayout/DocumentOutline/OutlineSpan/TableObject 派生层与版本化索引。正常本地检索不再把整块 Evidence 直接作为写作材料。
- 若旧 Evidence 未覆盖 canonical PageLayout 中的正文，生成新的 append-only `evidence_set_version`；新旧集合严格隔离，历史引用继续可读。
- V1 `RetrievedChunk` 在迁移期可由 `EvidenceBlock` 适配生成。
- 不直接删除现有 collection；新旧索引使用 schema version 区分。
- 财务 PDF 新增独立抽取/校验路径，不复用普通段落 RAG 直接生成财务数字。

### 6.3 Evidence 质量规则

- 来源文件、公司归属、页码或网页来源不能为空。
- 低质量页必须携带 `quality_flags`。
- 表格行不得丢失列头和单位。
- 时间相关事实尽量提取 `published_at/report_period`。`published_at` 必须是**可核实的披露/发行日期**并登记其**提取依据与置信状态**（O-12）：优先取公告/文档正文；**PDF 元数据、入库时间、最新财务指标日不得冒充**；不可核实即标 `unknown`，可另列明确标注的「数据截至日/参考期间」，**不得回填为发行日期**。`report_period` 与 `retrieved_at` 是另外两个独立字段，三者不得互相顶替。
- 相同内容多次出现时保留来源关系，但检索结果可去重。
- Web Evidence 必须记录 URL、标题、发布日期（如可得）和抓取时间。
- 财务 Evidence 必须记录表名、行名、列名、单位、合并/母公司口径和原始坐标。
- Evidence 按公司单独保存并支持版本化；被最终报告引用的版本不可因清理运行缓存而删除。
- Outline 必须覆盖正文一级标题、二/三级标题和项目级小标题；目录与正文锚点无法一致时显式标记冲突或低置信，不得任选其一静默通过。
- 跨标题 Evidence 必须拆为字符范围不重叠、可无损回查的 spans；任何 span 越界、重叠冲突、内容哈希不符或跨 document version 均 fail-closed。
- TableObject 必须能回查 component span/cell、表题、单位、表头、表体、合计与续表关系；结构不足时标记 partial/unsupported，不得把摊平文本伪装为完整结构化表。
- 每份文档必须报告未映射正文、`unassigned`、低置信节点、目录—正文不一致和表格结构缺口。

---

## 7. Router 与 Retrieval 2.0

### 7.1 五条路由与规则优先判定

| Route | 用途 | 典型例子 |
|---|---|---|
| `DB_LOOKUP` | 已进入数据库的标准字段、财务数字和 Python 指标 | 2025 年资产负债率、营业收入 |
| `DIRECT_EVIDENCE` | 文档中有明确字段/表格答案，但尚未标准化入库 | 成立日期、董事人数、折旧年限 |
| `STANDARD_RAG` | 单一专题，需要若干相关 Evidence 归纳 | 公司主营业务、核心竞争力、行业定义 |
| `DEEP_RETRIEVAL` | 跨文件、多跳、冲突信息 | 实际控制人变化及其时间线 |
| `EXTERNAL_RESEARCH` | 新近事件、行业、政策和外部验证 | 近期处罚、行业价格变化 |

规则优先判定顺序：

1. 问题是否对应已注册的数据库字段或计算指标；是则 `DB_LOOKUP`。
2. 是否要求从上传文档查一个精确字段/表格单元；是则 `DIRECT_EVIDENCE`。
3. 是否明确要求最新外部状态、新闻、政策或行业数据；是则 `EXTERNAL_RESEARCH`。
4. 是否需要跨页、跨文件、冲突消解或多跳关系；是则 `DEEP_RETRIEVAL`。
5. 其余章节内主题归纳走 `STANDARD_RAG`。
6. 多条规则同时命中、问题表达模糊或无法确定时，才调用轻量 LLM Router；输出仍必须经过 schema 校验。

项目分析第二阶段有额外硬规则：无论问题内容如何，不得路由到 `EXTERNAL_RESEARCH`。

### 7.2 Hybrid Retrieval 流程

```text
InformationNeed
  → Contract aspect / source / time metadata filter
  → OutlineNode candidate retrieval（title/path/synopsis/child titles/table titles）
  → Span/Table Sparse 与 Dense 并行召回
  → rank fusion
  → reranker
  → node/subtree 结构约束、去重与来源多样性控制
  → OutlineSpan/TableObject context assembly
  → EvidencePack
```

检索分为“导航候选”和“正式内容”两层：节点标题、路径和 synopsis 用于定位，正式上下文只来自可回查的 span/table payload。缺少高置信标题命中时，先检索全文、祖先/子节点、`unassigned`、同公司其他文档、表格对象和结构化数据；不能直接以“无标题命中”触发外部搜索。只有本地要求仍未满足且 SourcePolicy/Contract 允许时，才进入外部漏斗。

### 7.3 统一接口

```python
def retrieve(
    need: InformationNeed,
    company_id: str,
    policy: RetrievalPolicy,
) -> EvidencePack: ...
```

该接口继续承担强制日志职责，禁止 Worker 绕过接口直接访问 ChromaDB。树结构调整后，接口的本地结果必须携带 outline/node/span/table identity 与底层 Evidence locator；旧整块 Evidence 返回只能使用明确的兼容/fallback 状态。

### 7.4 Reranker 与 Fusion

`[已确认 R-01]` 第一阶段先保留 BGE-M3 Dense，新增本地 BM25，使用 Reciprocal Rank Fusion；Cross-Encoder/Reranker 是否加入由评测结果决定。  
`[已确认 R-02]` 可以接受额外本地模型的内存和启动成本，但必须记录加载时间、检索延迟、总运行时间和资源占用，作为是否启用的依据。

### 7.5 Top-k 策略

V2 不使用一个全局固定 top-k，也不把 top-k 调大视为默认优化。拆成三个参数：

- `candidate_k`：Sparse/Dense 各自初召回的候选数。
- `rerank_k`：融合后进入重排的候选数。
- `context_k`：最终进入 EvidencePack/模型上下文的 span/table 材料数；不得用整块 Evidence 数量掩盖节点内信息密度。

参数按 Route、章节和证据类型配置。例如精确字段通常需要较小 `context_k`，跨文件问题需要更大的候选池但仍限制最终上下文。首轮使用 PageHit@K、AllGroupHit、MRR、页级精度代理和延迟；真正的 Context Precision 与生成 token 成本待相应标注/生成评测具备后启用。

### 7.6 41 问路由标签迁移

已提交的 baseline 使用 `STRUCTURED / TOPIC / MULTI_HOP / EXTERNAL`。V2 不直接覆盖原始标签，而是增加派生字段 `expected_route_v2`：

| Baseline 标签 | V2 映射原则 |
|---|---|
| `STRUCTURED` | 已入库数字映射为 `DB_LOOKUP`；PDF 中精确字段映射为 `DIRECT_EVIDENCE` |
| `TOPIC` | 默认 `STANDARD_RAG`，若要求跨源冲突消解则改为 `DEEP_RETRIEVAL` |
| `MULTI_HOP` | `DEEP_RETRIEVAL` |
| `EXTERNAL` | 只有确实需要外部时效信息时映射为 `EXTERNAL_RESEARCH`；若年报已足够，则改为内部路径 |

当前数据中存在需校正示例：`COMP-S3` 标为 `EXTERNAL`，但 gold Evidence 是年报 P97；该题不应仅凭标签强制联网。路由评测前先完成人工/规则复核。

---

## 8. Tool Layer

### 8.1 Agent 可见工具

```text
search_evidence()
search_tables()
lookup_financial_metric()
lookup_company_field()
inspect_evidence()
compare_evidence()
search_external_sources()
verify_claim()
```

### 8.2 Tool Contract

每个工具必须声明：

- 工具用途与不适用场景。
- 结构化输入 Schema。
- 结构化输出 Schema。
- 可返回的错误类型。
- 最大结果数、超时和成本属性。
- 数据来源与审计字段。
- 是否允许重试、何时降级。

```python
@dataclass
class ToolResult:
    call_id: str
    status: str             # success | partial | empty | retryable_error | fatal_error
    data: dict
    evidence_ids: list[str]
    error_code: str | None
    message: str | None
    latency_ms: int
    cost: float
```

`[建议默认]` 工具返回完整结构化结果给 Harness；给 LLM 的上下文只放必要摘要和 Evidence 引用，避免把原始长结果全部塞回 Prompt。

### 8.3 外部来源适配的实施边界

独立定义搜索、正文获取、快照保存三步接口，返回 URL、标题、正文片段、发布日期（未知时显式为空）、抓取时间、内容哈希和工具状态。指定本地应用实际可调用的提供方与配置，不能把开发环境中可用的搜索能力视作 Streamlit 已接入能力。先完成真实适配器的 CLI 和来源快照验收，再接入 Harness。

网络失败、访问受限、空结果分别记录；降级缓存注明截止日期。“未检索到风险”必须带已执行来源和范围，不能由访问失败推导。材料正文属于证据数据，不能作为修改工具权限或研究边界的指令。

---

## 9. Research Harness

### 9.1 职责

Harness 是模型运行环境，不只是 guardrails。它负责：

1. 装配 SectionTask、当前 State 和允许使用的工具。
2. 接收模型的下一步动作。
3. 校验工具参数并执行工具。
4. 将结构化工具结果写入 State。
5. 管理迭代、token、时间和外部搜索预算。
6. 分类错误、重试、降级和失败终止。
7. 保存 checkpoint，支持从最近状态恢复。
8. 执行 Topic completion 与 Pack quality gate；章节正文生成后的 Section Evaluator 属于 P4。
9. 保存完整 trace 与 stop reason。

### 9.2 Loop

```text
初始化 TopicResearchState 与正式 aspect 待办
      ↓
选择仍未终态的最高优先级 aspect
      ↓
检查已验证材料/事实是否满足该 aspect 的证据要求
      ├─ 满足 → 标记 covered，保留该 aspect 已验证的全部材料与原子事实（不按相关性筛选）
      └─ 不满足 → 从正式要求派生 InformationNeed
                       ↓
              Router → ToolRegistry 执行
                       ↓
          命中后定位 Outline 节点/子树并读取 span/table
             （结构不可用或跨节点引用时才有界后备扩读）
                       ↓
            抽取并校验事实，更新覆盖与预算
                       ↓
              Topic Completion 是否满足？
              ├─ 否 → 下一未覆盖 aspect 或补检
              └─ 是/硬预算停止 → 提交 TopicResearchPack
                                      ↓
                          P4 Section Worker / Evaluator
```

宽问题的初始查询可以同时覆盖多个 aspect；系统应把一次结果映射回所有被支持的 aspect，而不是机械地“每个 aspect 必搜一次”。只有未覆盖 aspect 才触发定向查询。命中后，Harness 先读取候选节点的完整标题路径、相关子节点、`OutlineSpan` 和 `TableObject`，以恢复定义、列表、业务过程、原因、表头/单位和续表。只有 outline 缺失、低置信、内容截断或明确跨节点引用时才启用旧扩读能力；它不是新建平行检索器，仍经既有 Evidence/工具接口并落 Trace。

外部研究按“查询意图 → 候选排序 → fetch → snapshot → 事实采纳”执行。候选是否值得抓取按来源等级、日期、域名独立性和目标 aspect 判断；低价值未抓候选不得永久阻断为另一未覆盖 aspect 发起新查询。单一 URL、snippet 或 D 级来源不能让 aspect 完成。

### 9.3 停止条件

至少包括：

- 所有 required aspect 已进入有证据支持的 `covered`，或进入可解释的 `partial/not_found/blocked/not_applicable` 终态；完成一个 Information Need 不能代替 Topic 完成。
- 关键 Claim 达到最低证据数量和来源要求。
- 无新的高价值检索动作。
- 达到最大轮数、token、时间或外部搜索预算。
- 连续两轮无新增合格材料或事实；同一混合 Evidence 的重复返回不算新增。
- 出现不可恢复错误或必须人工确认事项。

`[已确认 H-01，2026-09-12 修订]` 历史单题公司/行业 6 轮作为 Phase 3 frozen 评测基线保留；正式内容生产改为版本化的 Topic 复杂度预算。简单字段题可沿用小预算，多 aspect 本地题、混合结构化题和外部研究题分别提高上限，但每档必须同时限制 rounds、tool calls、local/external searches、fetch/snapshot、tokens 和 elapsed time。预算由 aspect 数、来源类型和未覆盖缺口确定，不由公司名称、case_id 或 gold 决定。
`[已确认 H-02]` 模型可以追加 Information Need，但必须受章节边界、允许工具和预算约束。  
`[历史确认 H-03，当前范围由 UI-01/UI-02 修订]` checkpoint 与中间产物保留用于崩溃恢复、审计和未来扩展；当前面试版 UI 不提供“继续生成”按钮。未完成任务的中间产物最长保留 5 天；报告导出后及时清理可再生的运行中间态。最终报告、版本、Evidence 和引用长期保留。

`[建议默认]` 一轮定义为一次规划动作及其有上限的工具执行批次，可以覆盖多个 Need，不等于完成一个主题。每批 policy 必须冻结 max_iterations、max_tool_calls、max_tokens、max_elapsed_ms、max_external_calls、max_retries 和 max_repair_rounds；重试、定向返工和 Evaluator 消耗均计入预算。正式运行不接受无限值；具体数值由小规模运行校准后版本化。

`[已确认 H-04，2026-09-13 范围修订]` 达到任一预算上限即保存 checkpoint，状态为 paused/partial，不能标记为质量通过；当前面试版到此交付带缺口草稿，不向用户提供追加预算或继续生成入口。内部批次、累计用量和未解决 Need 仍须完整记录，为崩溃恢复、复现实验和未来扩展保留稳定接口。

以下情形一律不能视为研究充分：任意相关 Evidence 命中、任意一个 Claim 生成、任意一条外部搜索结果返回、或模型主动选择 ANSWER。完成判断必须逐 required aspect 使用已验证事实与引用；预算耗尽时可以交付 `PARTIAL` Pack，但必须保留已取得材料，并列明具体缺口、已查范围、未读范围和下一步建议。

`not_found` 不是“没看到结果”的默认状态。只有执行了 Contract 规定的来源范围、最低有效尝试、必要的上下文扩读与替代来源/候选策略后，才允许标为 `not_found`；检索尚未真正执行、候选尚未合理尝试、fetch 全被低价值候选挤占或仅因预算耗尽时，必须标为 `partial` 并记录具体 gap，不能提前关门。

当前面试版必须只读展示：当前缺什么、已查哪些材料/来源、为何停止、影响哪些结论或审核状态、以及未来如扩展时建议补充的材料类型。不得展示不可执行的“处理待确认事项”“补充材料”或“继续生成”按钮，不实施缺口与新材料绑定、Evidence 增量更新或用户触发续跑。独立章节仍按各自状态完成并可预览；缺口不能因缺少交互入口而被隐藏或改写为“不存在”。

### 9.4 Evaluator 使用边界

- 财务计算、格式检查、字段完整性优先使用 Rules。
- 公司、行业、项目开放研究在章节结束时使用一次 LLM Evaluator。
- Evaluator 只能指出具体缺口和证据问题，不负责重写章节。
- 最多触发有限次定向返工，禁止 evaluator-optimizer 无限循环。
- Evaluator 的输入、输出、模型和评分必须进入 trace。
- Section Evaluator 只负责章节质量，不是最终报告放行者；不得把 Writer 的自报覆盖或单次 LLM 评分当作通过证明。

### 9.5 状态栏与内部 Trace

`[已确认]` V2 需要同时定义机器状态和用户可见状态栏，二者不能只靠日志文本临时拼装。

#### 9.5.1 状态不是装饰性进度条

页面状态、后台任务状态和 checkpoint 共用同一套结构化事件。每次阶段变化先持久化 `ProgressEvent`；只有阶段产物完整提交后，才写入 `Checkpoint` 并将阶段标记为 `completed`。因此“已完成”代表该阶段可审计、可复用，而不是仅代表函数运行过，也不等于内容完整、系统审核通过或人工接受。

状态栏必须分离四个维度，禁止继续以单一 `success` 代替：

1. `process_completed`：工作流是否正常结束；
2. `preview_available`：是否存在可读草稿；
3. `assurance_status`：当前报告版本是否通过系统审核；
4. `human_acceptance_status`：是否已经人工最终确认。

用户可见最高自动状态为“已通过系统审核，可供人工确认”，系统不得把自己的审核结果表述为人工批准或正式授信决定。

任务有一个父级 `JobState`，公司研究、财务分析和行业研究分别拥有子级 `StageState`。并行运行时页面分别展示三个章节的进度，不能用一个虚假的线性百分比掩盖慢任务。

#### 9.5.2 用户可见阶段

| 页面显示 | 后台阶段 | 可展示的真实进度依据 | 完成后 Checkpoint |
|---|---|---|---|
| 校验上传材料 | `VALIDATING_INPUT` | 已校验文件数 / 总文件数 | 文件清单、哈希、主体与类型归属 |
| 读取 PDF | `PARSING_DOCUMENTS` | 已解析页数或文件数 / 总数 | 每份文档的解析结果与质量报告 |
| 提取并核对财务数据 | `EXTRACTING_FINANCIALS` | 已处理报表/期间数 | 原始财务记录、标准科目、勾稽与冲突结果 |
| 构建证据链 | `BUILDING_EVIDENCE` | 已生成 Evidence 数、待处理页面数 | EvidenceBlock 批次及来源坐标 |
| 建立检索索引 | `INDEXING_EVIDENCE` | 已索引 Evidence 数 / 总数 | 可查询的 Sparse/Dense 索引版本 |
| 规划报告研究任务 | `PLANNING` | 已生成 SectionTask 和 Information Need 数 | 冻结的 ReportPlan |
| 公司信用研究 | `COMPANY_RESEARCH` | 已完成 Need 数 / 计划数 | EvidencePack、Claim 和章节草稿 |
| 财务分析 | `FINANCIAL_ANALYSIS` | 已完成指标组/主题数 | 指标表、异常项、Claim 和章节草稿 |
| 行业研究 | `INDUSTRY_RESEARCH` | 已完成 Need 数 / 计划数 | 外部快照、EvidencePack、Claim 和章节草稿 |
| 章节质量检查 | `SECTION_EVALUATION` | 已通过章节数 / 应完成章节数 | Evaluator 结果及返工记录 |
| 综合整理报告 | `ASSEMBLING` / `SYNTHESIZING` | 已组装章节数与跨章冲突数 | 完整报告草稿和 Claim 关系 |
| 整体自检 | `VERIFYING` | 内容完整性前置门及已运行 Assurance 类别数 / 总数 | 绑定当前报告版本的 Assurance 结果及人工复核状态 |
| 生成交付文件 | `EXPORTING` | 已生成目标格式数 / 总数 | 当前报告版本与草稿导出；人工最终确认状态独立保存 |

当总量暂时未知时，页面显示阶段动画和当前动作，不伪造百分比；一旦得到页数、文件数或 Need 数，再切换为确定进度。

#### 9.5.3 用户状态信息

UI 至少展示：

- 当前阶段、并行章节及简短动作，例如“正在读取第 3/8 份 PDF”。
- 已完成/总任务数、未解决 Information Need 和需要人工确认的事项。
- 当前阶段耗时、任务总耗时；工具调用、重试和外部检索次数可折叠展示。
- `retrying`、`degraded`、`partial`、`review_required`、`paused`、`failed` 等明确状态，而不是长期停在“处理中”；兼容层内部 `waiting_user` 在当前 UI 映射为“存在信息缺口/需人工复核”，不形成在线处理入口。
- 最近 checkpoint 的时间和已保留结果；当前面试版不展示“继续生成”或补件按钮。
- 可选的 token/成本，但不展示模型隐藏思维链。

#### 9.5.4 Checkpoint 与断点恢复

默认在以下边界创建耐久 checkpoint：

1. 上传材料校验完成并冻结 manifest。
2. 每份 PDF 解析和质量检测完成。
3. 每批 Evidence 写入并完成索引。
4. 财务抽取、标准化、勾稽和冲突记录提交完成。
5. `ReportPlan` 冻结。
6. 每个 Information Need 的 EvidencePack 完成，以及每个章节通过质量门。
7. 报告组装完成、整体 Assurance 完成和交付文件生成完成。

恢复时读取最近一个有效 checkpoint，校验输入哈希与产物引用；已完成单元不重复执行，checkpoint 之后未完整提交的单元以相同幂等键安全重跑。当前面试版只要求系统崩溃/重启恢复和产物复现，不交付用户替换材料后的在线依赖失效与续跑；相应身份、依赖和失效字段继续保留为未来扩展口。

恢复还需核对 contract、schema、提示词、模型、规则、索引和财务快照版本，以及人工确认的来源绑定；版本不兼容时明确说明需重跑哪些单元。幂等保证本地产物不会重复提交，不保证崩溃前未记录响应的外部调用不会再次计费；此类不确定调用必须记录并纳入预算，不能宣称外部调用恰好执行一次。

#### 9.5.5 与 Trace 的关系

状态事件回答“现在做到哪里、能否继续”；Trace 回答“用了什么输入、调用了什么工具、为何得到该产物”。一次阶段迁移应同时写入状态事件和对应 trace span，但二者的数据粒度不同。UI 只展示可理解的状态、错误和产物摘要，不展示模型思维链。

---

## 10. 报告组装与综合研判

### 10.1 两阶段设计

**阶段 A：确定性组装**

- 按模板放置章节。
- 统一公司名称、股票代码、报告期、单位和标题。
- 汇总 Claim、Citation、RiskFinding 和 UnresolvedIssue。
- 对重复事实做结构化去重，不改写其含义。

**阶段 B：综合方案评价**

- 建立公司、行业、财务和项目之间的影响关系。
- 识别优势与风险的相互抵消或放大。
- 形成第一还款来源和现有增信措施有效性判断。
- 将重大风险映射到用户已提交方案的金额、期限或增信措施。
- 形成方案优缺点和综合结论，但不主动生成新额度、期限、增信措施，不引入新事实和新数字。

### 10.2 综合 Claim 的来源

综合结论必须使用 `derived_from_claim_ids` 指向章节 Claim；章节 Claim 再经受支持事实或材料对象追溯到来源锚点。这样形成：

```text
综合方案评价
  → 综合判断
    → 章节 Claim
      → SupportedFact / ResearchMaterial
        → OutlineSpan / TableObject / FinancialFact / ExternalFact
          → EvidenceBlock + PageLayout source coordinates / FinancialSnapshot / ExternalSnapshot
            → 原始文件、页码或外部来源
```

`DocumentOutline` 的标题、路径和导航简介只帮助定位，不能作为 Claim 的直接证据。P4 引用必须落到 `OutlineSpan`、`TableObject` 的 component provenance、结构化财务事实或外部快照正文；最终仍可回查原始文件与精确位置。

---

## 11. Report Assurance

**现行设计覆盖（2026-09-29）：**本章凡要求 `SectionClaim`、聚合绑定或逐候选蕴含为正文/审阅的前置门者，均是旧链历史接口，不再适用于 §0.20 新链。新链的当前版本报告由精确 Pack/合格事实、逐句引用正文、逐句确定性硬核对记录、独立只读 `ReviewIssue[]`、Contract 缺口及表格资格共同构成审查输入。Reviewer 按句子和精确来源定位语义问题，Controller 独立聚合硬错误/审阅意见；旧版对象只读兼容，不得混入新版本身份。预览可读、硬核对、独立审阅、系统放行和人工接受分别报告；旧状态不自动迁移为新状态。下列 Claim 图谱细节留作历史对照。

V1 `agents.verifier` 保留为迁移起点，但 V2 将回检扩展为全报告质量保障。

### 11.0 内容完整性前置门

六类 Assurance 运行前，必须先确定性核对 `Contract required_aspects → TopicResearchPack/FinancialFactPack → SectionClaim → NarrativeParagraph/Table` 的保留关系：每个 required aspect 有独立终态，covered 必须有合格事实与引用，高优先级已支持事实不得无理由在 Writer 边界丢失。该门负责回答“应写的内容是否系统性漏掉”，不能由引用存在性或 LLM 自评替代；失败时报告仍可作为带缺口草稿预览，但不得获得系统审核通过状态。

该门还必须覆盖 §0.12/§0.13 的下列各轴，并与覆盖率检查分开报告：

- **候选接受轴线**：正文事实必须来自**已接受**的 `SectionClaim`，即已通过确定性 Claim Binding Gate 与 Section Evaluator 原子蕴含的候选；`ClaimCandidate`、Writer 提案或草稿预览**不得**被当作已接受事实，也不得在相应门通过前把正文表述为已审查或可发布（§0.13）；
- **资格轴线**：`covered` 引用的必须是**通过资格判定**的事实（`eligible → SupportedFact`），而不是 wire 层被采纳的候选；候选被拒绝必须留下 typed rejection decision/audit，且原材料仍在 Pack 中。**拒绝本身不产生 gap**；只有 Contract 必需 aspect/fact 因该拒绝仍未满足时才另行形成 gap/block（§0.13 第 9 条）；
- **材料 disposition 轴线**：每个 manifest 成员必须具有可复核的 `WriterMaterialProcessingDisposition` 并回指匹配的研究侧 `ResearchMaterialDisposition`；前者表达 processed/used/not_used，后者只表达 research admission/retention/source validation，二者不得共用含糊身份或越权裁决 fact eligibility。Contract 必需的 fact candidate 资格失败或未取得时**必须形成 gap/block**，不得只记成普通 `not_used`；反向地，材料未被采用不构成 gap；
- **支撑权威轴线**：每条支撑边必须显式声明 `authority_kind`（`topic_pack` / `financial_pack` / `evidence_note` / `external_snapshot`）、`support_role`、`support_semantics` 与 authorization basis；字段组合必须符合所属 authority kind，无合法 context 定位方式的组合显式非法，不得为凑字段伪造 Topic material（§0.12、§5.4.1）；
- **role-aware 轴线**：NarrativeSentence 的每个事实性原子必须由 factual support 支撑——路径 A（预验证权威，任何已具有合法预验证事实身份的原子均可用）或路径 B（exact 材料 + 机械门 + 独立蕴含决定，**仅限非高风险描述性断言**）；**高风险硬事实只允许路径 A**；context material 不得补足缺失的合格 fact（§5.5、§11.2）。
- **原子性轴线**：一个 `ClaimCandidate` 只能表达一个原子断言；混合自然句必须拆成多个候选，每个高风险原子独立绑定其预验证权威。不得以“单个混合 Claim 内逐原子绑定”替代拆分（§0.12）。

### 11.1 六类检查

| 类别 | 核心问题 | 首选方法 |
|---|---|---|
| Citation Integrity | Evidence 是否存在且真正支持 Claim | 规则 + NLI/LLM 判断 |
| Numerical Consistency | 数字、单位、期间和计算是否一致 | Python/SQL Rules |
| Entity Consistency | 公司、股东、子公司、项目是否混淆 | 实体表 + Rules |
| Temporal Consistency | 是否混用过期或不同时间口径 | 日期 Rules + 来源元数据 |
| Cross-section Consistency | 不同章节事实和判断是否矛盾 | Claim 图谱 + Phase 5 受限独立语义审稿器（见 §11.2） |
| Decision Adequacy | 风险是否落实到对用户授信方案的评价 | Rules + Phase 5 受限独立语义审稿器（见 §11.2） |

### 11.2 Independent Review Agent、Assurance Controller 与防自我证明边界

最终审核不是第二个自由写作 Agent，也不是 Writer 与 Reviewer 的多轮辩论。语义审查角色正式命名为 `Independent Review Agent`：它在章节定稿与确定性组装**之后**运行，与 Writer 使用独立调用、独立 Prompt、独立证据上下文且不读取 Writer 的自评或历史对话；输入由权威产物构造，至少包含绑定 `report_version` 的 assembled draft、`NarrativeParagraph/Table`、Claims、Citations、最小证据原文、材料索引、gaps 与 Contract/WritingSpec rubric；接口只允许输出版本化、定位明确的 `ReviewIssue[]`。

它检查的是**整体风险**而非逐条重核：合并多个各自正确的 Claim 时是否产生含义变化；是否选择性使用材料；是否遗漏关键限制；是否把局部结论外推到整体；是否把相关写成因果；是否夸大优势或风险；是否存在跨 Claim/跨章矛盾；以及整体是否可能误导信贷人员。它只读、不改正文、不补事实、不触发研究、不联网、不决定发布，**不得重复**确定性 hard gate 或 Claim Binding Gate 的逐条 hash/locator 检查（Claim 级原子语义核验的唯一位置是 Section Evaluator，§0.13），**不得覆盖** hard failure，也不得自行放行。

`Assurance Controller` 只编排以下单向质量门：

1. 冻结并校验当前 `report_version`、Contract、Pack、Snapshot、Claim、Citation、规则和 Prompt 身份；
2. 先执行内容完整性前置门与可确定计算的硬规则，任一 blocking 不得被 LLM 覆盖；
3. 独立审稿输入由权威 Store 独立构造为“绑定 `report_version` 的 assembled draft + `NarrativeParagraph/Table` + Claim/Citation + 最小证据原文 + 精确定位 + gaps + Contract/WritingSpec rubric”，不读取 Writer 的自评、隐藏推理或历史对话；
4. `Independent Review Agent` 只返回结构化 `supported/contradicted/insufficient/missing_content` issue、位置和建议返工目标，不得重写正文、创造事实、直接决定发布或与 Writer 反复协商，也不得重复逐条 hash/locator 门、覆盖 hard failure 或自行放行；
5. 最终状态由确定性聚合器计算，并绑定当前报告版本；任何自动修正或章节返工生成新版本后，旧 Assurance 立即失效并重新运行；
6. 主体异常、重大负面事项、关键财务冲突及用户授信方案仍保留人工最终确认。使用不同审核模型是可选增强；当前 Demo 允许复用同一底层模型，但必须独立调用、独立 Prompt、独立证据上下文且无共享生成历史。

报告版本状态至少区分“流程完成”“草稿可预览”“系统审核未通过”“系统审核通过、可供人工确认”和“人工未复核/已复核”；这些状态绑定当前 `report_version`。正式阶段关闭是来自治理文档/验收记录的独立只读快照，不由 Controller 计算，也不随报告版本失效。

`Review/Assurance` 是**第三套** dependency/content identity（§0.12 第 9 条）：Reviewer/Controller 的 prompt、model、rubric 与规则版本属于 dependency fingerprint；绑定 `report_version` 的 `ReviewIssue[]` 与聚合状态属于 content identity。这些对象**不写入 Pack**，也不与 Pack qualification、Section/Writer/Evaluator 两套身份混装。**这三套与 §0.13 第 4.1 条的四类持久化边界是不同轴的划分，不是一一对应关系**（`FollowUpNeed` 位于三套 content identity 之外，是独立运行请求/trace 身份）。Reviewer 输入中的 `ClaimBindingDecision` 与 `ClaimEntailmentDecision` 只作只读输入，不得被 Reviewer 改写或重新判定。LLM 不输出最终布尔绿灯；形式化放行条件为：当前版本硬规则通过、blocking 为 0、必需语义审核结果完整且仍有效。面试 Demo 允许 `流程完成 + 草稿可预览 + 系统审核未通过 + 人工未复核`，但页面和产物不得把它缩写为单一 `success`。

### 11.3 质量门与回流

| 问题类型 | 默认动作 |
|---|---|
| 格式、名称、单位等确定性错误 | 自动返回组装层修正 |
| 证据不足或引用不支持 | 在本次有界运行内返回具体 SectionTask 定向补查；运行结束后仍不足则只读展示缺口 |
| 跨章节判断冲突 | 返回综合研判层 |
| 财务数字不一致 | 阻止正式版导出，重新读取结构化结果 |
| 重大事实无法确认 | 标记 blocking/review_required，草稿列明影响，当前面试版不提供在线补件或确认闭环 |

`[已确认 V-01]` 数值重大错误、主体错误、无证据的核心结论和方案评价自相矛盾为 blocking，阻止正式版导出。  
`[已确认 V-02]` 可延续黄/红/橙的用户提示思路，但 V2 不受 V1 颜色绑定限制；内部 `category` 与 `severity` 分开建模。  
`[已确认 V-03]` 自动修正后必须重新运行完整 Assurance。
`[已确认 V-04]` 内容完整性前置门、确定性硬规则和版本绑定由 Controller 计算；LLM 只做有证据输入的结构化语义审稿，不能自我证明或直接放行。
`[已确认 V-05]` 当前面试版最高自动状态为“已通过系统审核，可供人工确认”；不实现用户补件/绑定/续跑，也不把系统审核表述为人工授信批准。

---

## 12. Evaluation Framework

评测不是最终报告的一次总分，而是沿数据流分层定位问题。

### 12.1 七层离线评测

| 层 | 主要指标 | 基准数据需要什么 |
|---|---|---|
| Evidence 构建 | 页码准确率、结构类型准确率、表格完整率、来源完整率 | 文档页面与人工标注 Evidence |
| 文档结构 | 目录/正文标题对齐率、大小标题层级准确率、正文归属率、跨标题切片准确率、TableObject 完整率 | 真实 PDF 的标题树、正文区间、表格与未归属内容人工标注 |
| Router | Route Accuracy、严重误路由率、fallback 成功率 | Information Need + 人工路由标签 |
| Retrieval | Recall@K、MRR、nDCG、Context Precision、跨标题污染率、材料多样性 | Query + gold node/span/table IDs + 底层 Evidence IDs |
| Research Harness | 必答项完成率、有效工具调用率、无效循环率、恢复成功率 | Section Task + gold requirements |
| Section | Coverage、Faithfulness、Citation Correctness、信用相关性 | 章节 rubric + 参考证据 |
| Full Report | 数值准确、实体准确、时效、跨章节一致、决策充分性 | 报告级 case + 专家 rubric |

P3R/P4R 必须在原六层之间增加可定位的内容吞吐指标，而不是只看最终 FULL 或 `eval 0 failed`：

- **研究完整性**：required aspect 终态率、supported aspect coverage、Pack 事实保留率、命中后上下文扩读有效率、材料跨来源多样性。
- **结构保真度**：正文小标题召回率、父子/同级关系准确率、跨标题旧块正确拆分率、正文未归属率、TableObject 标题/单位/表头/表体/合计/续表完整率。
- **树感知检索质量**：候选节点命中率、span/table 返回率、整块混合 Evidence 直接进入上下文的比例、导航简介被误当证据的次数（必须为 0）。
- **外部研究价值**：每 aspect 候选/fetch/snapshot/adopt 数、A/B/C/D 分布、日期合格率、失败发生在 query/provider/fetch/snapshot/policy 的具体层。
- **章节表达**：Pack fact→Claim 保留率、Claim→NarrativeParagraph 覆盖率、表文一致率、宽 Topic 的结构完整性和人工可读性 rubric。
- **安全正确性**：错误事实、无来源数字、引用不可回查、期间/单位/主体错配进入正式正文必须为 0；安全正确性和研究完整性分别报告，不能互相替代。

### 12.2 在线运行指标

- 每阶段 latency 和总 latency。
- 每模型调用 token、成本和失败率。
- 每工具调用成功、空结果、重试和降级次数。
- 每章节迭代轮数和 stop reason。
- Evidence 数量、引用覆盖率和 unresolved 数量。
- Topic 的 aspect covered/partial/not_found 分布、Pack material/fact 数、上下文扩读范围及预算利用率。
- 外部漏斗的候选、抓取、快照、采纳和拒绝原因分布。
- Pack→Claim→Paragraph 各层保留率；高优先级事实被 Writer 丢弃须形成 issue。
- Evaluator 返工率、返工后改善率。
- Assurance 问题数量、blocking 数量和人工确认数量。

### 12.3 EvalCase Schema

```python
@dataclass
class EvalCase:
    case_id: str
    company_id: str
    input_fixture: str
    section_id: str | None
    information_need: str | None
    expected_route_raw: str | None
    expected_route_v2: str | None
    gold_evidence_ids: list[str]
    gold_page_refs: list[str]
    gold_answer: dict | str | None
    required_claims: list[str]
    forbidden_claims: list[str]
    rubric: dict
```

### 12.4 V2 Baseline 的最低数据集

`[已确认 EV-01/EV-02]` 已提交宁德时代 41 问数据集：公司信用 20、财务 13、行业 8；路由分布为 STRUCTURED 14、EXTERNAL 7、TOPIC 11、MULTI_HOP 9。41 条均包含 `gold_answer` 和非空 `gold_evidence.page`。

第一阶段 baseline 范围：

- 主指标为 `PageHit@K`：返回 Evidence 的来源页是否命中 gold 页码集合。
- 同时记录 `MRR`：第一个正确页码在结果中的排名。
- 对多页 gold，采用“至少命中一个”和“全部关键页命中”两个指标。
- 记录页级 `GoldPageResultPrecision@K`、检索耗时及返回文本字符数；如记录 tokenizer 估算 token 数须注明 tokenizer 版本。这不是生成模型实际输入 token，也不是真正的 Context Precision。
- 先跑 V1 baseline，再确定 V2 通过阈值；当前不凭空设置 90% 等绝对门槛。
- 暂不要求章节级和全文级人工 gold 报告，也不安排第二位人工评审。
- 保留已有 `gold_answer`，但答案准确率作为后续阶段，不阻塞第一轮 Retrieval 改造。

`[已确认 O-08]` 第一阶段接受“先评正确页码命中，答案质量评价后置”的范围。  
`[待技术处理]` 当前路由标签需要按 §7.6 迁移并复核；页码字符串还需规范化为文档 ID + PDF 页码/印刷页码，避免“年报 P97”和“PDF 第 99 页”混淆。

### 12.5 Baseline Runner 契约

#### 12.5.1 目标与冻结对象

Runner 的目标是冻结“V1 检索器在不修改查询、不引入 Router、不做查询扩展时，能否从当前本地语料中召回正确证据页”的基准。一次 run 必须同时冻结：

- 数据集文件哈希。
- Corpus manifest、各 PDF 文件哈希和 Chroma collection 标识。
- Retriever 代码版本或 Git commit；工作区非 clean 时记录 dirty 状态。
- Embedding 模型、`k`、文档优先级参数和运行时间。
- 逐题原始返回结果、检索日志引用、耗时和异常。

同时冻结实际索引库存：按稳定顺序记录 collection 中的记录 ID、来源、PDF 页码、chunk、文本哈希及 metadata，生成库存指纹并核对 manifest 中的文件。记录 Embedding 本地模型版本/权重标识、精度、依赖版本、设备和 Retriever 相关代码文件哈希；仅 Git commit 加 dirty 标记不能标识未提交代码。初始加载耗时单列，逐题耗时保留实际观测，不能把首题冷启动误作全部查询的稳定延迟。

允许 Runner 通过只读适配器读取 collection 元数据/记录用于库存核验和失败诊断，不允许执行额外向量查询、修改索引或自动重建。已知缺文档按参评表处理；实际额外文档、文件版本无法核对或运行前后库存变化须明确报告为不可比较，不生成可用正式基线。索引本身不足以证明来源 PDF 哈希时，需先在独立的数据准备步骤建立可信来源清单，Runner 不猜测对应关系。

Baseline Runner 只能调用现有 `retrieval.retriever.retrieve()`，不得绕过统一接口直接查询 ChromaDB。每道题只使用数据集中的原始 `question`，不得加入同义词、答案关键词或人工 query expansion，否则不再是 V1 原始基线。

#### 12.5.2 输入文件

```text
evaluation/datasets/v1_baseline.jsonl   # 规范化后的41问
evaluation/datasets/corpus_manifest.json # 文档别名、实际文件、页码体系和索引信息
data/chroma/                              # 已构建的V1索引
```

规范化后的 case 至少包含：

```python
@dataclass
class RetrievalEvalCase:
    case_id: str
    company_id: str
    section_id: str
    question: str
    expected_route_raw: str
    expected_route_v2: str
    priority: str
    time_scope: str | None
    gold_evidence_raw: dict   # 原始页码表达、来源和备注保留供审计
    notes: str
    gold_answer: dict | str | None
    gold_evidence_groups: list[GoldEvidenceGroup]


@dataclass
class GoldEvidenceGroup:
    group_id: str
    requirement: str          # 当前41问固定 all，不支持把页码改成 any
    channel: str              # local | external | structured_db
    targets: list[GoldEvidenceTarget]


@dataclass
class GoldEvidenceTarget:
    document_id: str | None
    printed_page: int | None  # 每个 target 对应一个必需页面
    pdf_page: int | None      # PDF 1-based；未映射不得猜测
    page_mapping_id: str | None
    source_note: str
```

`[已确认 B-05]` 当前41问全部采用且关系：`/`、`+`、跨文档页码均为必需证据，范围如 P35-40 展开为35至40每个页面，每页单独一个 target。相同 document_id + pdf_page 去重并保留原始引用关系；不得自动改成“任选一页”或缩小范围。证据组按问题子要求/channel 组织，所有本地组均必需；外部组仅标为本轮未评估。当前数据加载器拒绝 any；未来若新增或修正标注，必须发布独立数据集版本，不回改已冻结基线。

#### 12.5.3 Corpus Manifest 与页码映射

```python
@dataclass
class CorpusDocument:
    document_id: str
    aliases: list[str]
    file_path: str
    source_type: str
    sha256: str
    page_count: int
    page_mappings: list[PageMapping]

@dataclass
class PageMapping:
    mapping_id: str
    printed_page: int | None
    pdf_page: int | None
    page_system: str          # printed | pdf
    status: str               # verified | inferred | missing
    verification_note: str    # 页脚/目录/文本锚点、校验方法及范围
```

命中必须同时满足 `document_id` 和标准化后的 `pdf_page`，不能只比较页码数字。每条映射记录所属文档、原始页码体系和验证状态，不允许用整份文档一个状态代替逐页状态，不允许对不同文档套统一 offset。抽样可定位页码关系，但未验证的引用页仍为 inferred；正式参评的每个本地 target 必须 verified，且在实际 PDF 页数内。原文明确写 PDF 页的引用不再套印刷页偏移，仍需核对页界及锚点。无法可靠映射的题整题排除，并报告已确认/未确认 target，不删除未确认页后重新计分。

`[已确认 B-01]` 正式主指标采用严格页码相等；±1 页命中仅输出 `AdjacentPageHit@K` 供定位跨页切块问题。

#### 12.5.4 参评资格

每个 case 运行前确定 `eligibility`：

| 状态 | 含义 | 是否进入本地 Retrieval 总分 |
|---|---|---|
| `ELIGIBLE_LOCAL` | 至少一个本地组，且全部必需本地 target 均 verified、所有必需文档在语料中 | 是 |
| `EXTERNAL_ONLY` | gold 仅来自外部网页或行情 | 否，单列为未来 External Research baseline |
| `STRUCTURED_DB_ONLY` | gold 只应来自结构化数据库 | 否，单列为 DB lookup baseline |
| `NON_LOCAL_MIXED` | 同时包含 external 和 structured_db，且无本地 target | 否，单列为本轮未评估 |
| `INVALID_GOLD_MAPPING` | 文档或页码尚不能可靠映射 | 否，视为数据集错误 |
| `MISSING_CORPUS_DOCUMENT` | gold 文档未进入当前语料 | 否，但必须作为 corpus 缺口报告 |

判定顺序：无本地 target 时，仅 external 为 EXTERNAL_ONLY，仅 structured_db 为 STRUCTURED_DB_ONLY，两者都有则为 NON_LOCAL_MIXED（单列排除）；存在本地组时，先检查别名与全部页码映射，任一无效则 INVALID_GOLD_MAPPING，再检查必需文档是否进入语料，缺任一份则 MISSING_CORPUS_DOCUMENT，否则 ELIGIBLE_LOCAL。所有同时存在的问题保留辅助标签，主状态互斥。文档已入库但特定 gold 页无有效 chunk 或索引缺页时，仍是 eligible，计为能力失败，不能通过排除页级缺口提高分数。

正式运行只查询 eligible 题；部分映射题展示映射诊断，不用其已确认子集计算正式分数。验证模式不加载 Embedding，但需只读盘点既有 collection 才能确认完整 eligibility；缺 collection 时非零退出，不自动创建。

混合题只评价其本地证据组；外部部分标记 `NOT_EVALUATED_IN_THIS_RUN`，不能视作已经完成。`STRUCTURED` 原标签不自动排除：只要 gold 位于当前 PDF 语料，仍可作为 V1 Retriever 能力基线运行。

`[已确认 B-02]` `EXTERNAL_ONLY` 不进入 V1 本地检索总分，混合题只评价本地证据组。

#### 12.5.5 命中与指标公式

对单题 `c` 和截断位置 `K`：

```text
target_hit(t, K) = Top-K 中存在 document_id 与 pdf_page 均匹配 t 的结果
group_hit(g, K)  = 本地组中全部target命中（当前41问只允许all）
AnyPageHit(c, K) = 任一必需本地target命中（部分召回信号，不代表答题完整）
AllGroupHit(c,K) = 所有必需本地证据组均命中
RR(c)            = 1 / 第一个正确 target 的排名；无命中为0
```

汇总指标：

- `PageHit@1/5/10`：eligible case 的 `AnyPageHit` 平均值。
- `AllGroupHit@5/10`：全部 eligible case 的 AllGroupHit 平均值；另列拥有两个及以上唯一必需本地页面的 multi_page 切片及分母。
- `MRR@10`：eligible case 的 `RR` 平均值，只考察前10名。
- `AdjacentPageHit@5/10`：允许同文档 ±1 PDF 页的诊断值，不作为正式主分。
- 按 `section_id`、`expected_route_raw`、`expected_route_v2` 和 `priority` 分组报告相同指标；P0 `PageHit@10` 必须与总体主分同时出现在报告首屏。
- 平均、P50、P95 latency；空召回、异常和缺文档数量。

当前41问只有页级 gold，不能可靠计算真正的 Context Precision。首轮只输出 `GoldPageResultPrecision@K` 作为诊断代理，并在报告中明确它不是语义层 Context Precision；后者需补充 chunk/Evidence 级相关性标注后再启用。

代理精度定义为前 K 个实际返回 chunk 中落在 gold 页的 chunk 数 / 实际返回 chunk 数，空召回或异常为0；重复页仍占原始排名位置，不能先去重页码再截取 Top-K。相邻诊断采用同文档绝对页差≤1，包含严格命中，另列仅相邻而非严格命中的题数。

正式指标统一使用运行前冻结的 eligible 分母；空结果、异常均计0，不事后移出分母。切片无参评题时输出 null/“不适用”及 n=0。`ks` 必须包含1、5、10且全部为正整数，去重排序，当前 V1 fetch 上限为20，拒绝 K>20。每题仅调用一次 k=max(ks)，所有 K 指标是该次返回的前缀统计，不宣称等同于分别原生调用 k=1/5/10；跨运行比较必须保持相同 max(ks)。

PageHit 和 MRR 保留“是否找到了至少一页”的既有用途，不代表全部证据充分；B-05 的“且”通过 AllGroupHit 验收。首屏在既有总体/P0 PageHit@10 之外同时显示 AllGroupHit@10，避免将部分召回描述为完成。报告列出必需唯一页数>K的题数：V1 单 chunk 属于单页，此类题的 AllGroupHit@K 无法达到1，仍保留分母并注明预算限制，不擅自缩减 gold。

`[已确认 B-03]` 总体 headline metric 使用 eligible case 等权的 Macro `RequiredPageCoverage@10`，不计算人为加权总分；同时将 P0 `RequiredPageCoverage@10` 作为独立关键指标。`PageHit@10` 只表示是否至少命中一页，不能替代部分覆盖主分。

#### 12.5.6 失败分类

每道未命中题必须且只能有一个主失败原因，同时允许多个辅助标签：

| 主失败原因 | 判定方式 |
|---|---|
| `DATASET_MAPPING_ERROR` | gold 文档别名或印刷页码无法规范化 |
| `CORPUS_MISSING` | gold 文档未被索引 |
| `PARSE_PAGE_EMPTY` | gold PDF 页为空、被跳过或未生成 chunk |
| `INDEX_MISSING` | gold 页有 chunk，但 collection 中没有对应记录 |
| `INDEXED_NOT_RETURNED_TOP_K` | 缺失的必需 gold 页已入索引，但未进入本次返回；不能推断其确切排名 |
| `EMPTY_RETRIEVAL` | Retriever 返回空结果 |
| `RETRIEVAL_ERROR` | 模型、Chroma 或运行异常 |
| `NOT_APPLICABLE_LOCAL` | external-only 或 DB-only，不属于本次失败 |
| `DIAGNOSTIC_UNAVAILABLE` | 无可靠解析/索引记录区分缺页或召回原因，明确诊断证据不足 |

按 AllGroupHit@max(K) 未完成的题分类，PageHit 成功但缺少其他必需页也属于部分召回。主原因按 DATASET_MAPPING_ERROR、CORPUS_MISSING、NOT_APPLICABLE_LOCAL（仅排除题）、RETRIEVAL_ERROR、EMPTY_RETRIEVAL、PARSE_PAGE_EMPTY、INDEX_MISSING、INDEXED_NOT_RETURNED_TOP_K 的适用优先级确定；页面诊断只检查本次缺失的必需页面。解析/索引证据不足以区分时记录 DIAGNOSTIC_UNAVAILABLE，不猜测。可加 CHUNK_BOUNDARY、NEEDS_MULTI_HOP 等有依据的辅助标签；词汇不匹配、文档加权压制等未验证解释只能标为假设，首轮不额外查询或使用 LLM 猜主因。

#### 12.5.7 输出契约

```text
evaluation/results/<run_id>/
├── run_manifest.json        # 输入、版本、参数、语料与环境快照
├── case_results.jsonl       # 逐题排名、命中、耗时、错误和Top-K结果
├── metrics.json             # 总体及各切片机器可读指标
├── data_quality.json        # 页码映射、缺文档和无效case
└── report.md                # 人可读摘要与失败题清单
```

run_manifest 引用并哈希运行冻结的 dataset、corpus manifest 和索引库存快照，这些快照随结果保存到 inputs/ 以便离线复算；以上五类文件仍是必需交付物。case_results 保存每个排除题的全部原因，以及每个 eligible 题的原始返回、rank、完整文本、score、逐target/组命中和耗时；未知来源不得仅凭同页码判中。

Runner 为每次检索生成唯一 call_id，在 logs/retrieval/baseline/<run_id>/ 下先持久化 started，再追加 succeeded/failed 事件，包含原始 query、K、完整结果/异常和时间，并将路径与哈希写入结果。现有 Retriever 日志存在时精确关联并随运行归档，无法唯一关联时明确记录 legacy_log_missing/ambiguous，不能用猜测路径冒充。Runner 审计为强制日志，失败则 run 标记不可用；不改 V1 排序、查询或日志实现。未闭合 started 在中断诊断中保留，不自动重试题目。

Runner 遇到单题检索异常时记录失败并继续其他 case；但数据集 JSON 无法解析、case_id 重复、collection 不存在或没有任何 eligible case 时，应以非零退出码终止。输出采用临时目录写入，全部成功后原子改名，避免把中断结果误认为完整 baseline。

“全部成功”指评测与审计产物完整提交，不要求全部题命中；完整批次可为 completed_with_case_errors，异常题仍计0。共享依赖启动失败、审计落盘失败或语料冻结失败属于系统错误，写入明确的 failed 诊断，非零退出且不生成完整运行目录。临时目录必须与最终目录同文件系统、使用唯一 run_id，不覆盖已有结果。

### 12.6 Baseline Runner 的验收规则

- `--validate-only` 可在不加载 Embedding 模型的情况下完成数据集、manifest、页码和参评资格检查。
- 相同数据集、语料、参数和模型重复运行，case 数、分母和命中排名应一致。
- 至少用合成数据覆盖：单页命中、范围逐页 all、多组 all、部分映射、相邻页、外部-only、缺文档、无效页码、空召回和 Retriever 异常；当前41问输入 any 必须校验失败。
- Runner 自身测试使用 mock retriever，不加载 BGE-M3；真实 CLI 集成测试才使用当前 Chroma collection。
- `case_results.jsonl` 的每次本地检索均能关联 `logs/retrieval/baseline/<run_id>/` 中闭合的调用审计；缺失的 V1 旧日志单列而不伪造。
- baseline 只记录结果，不修改索引、不自动优化 query、不改变 V1 Retriever 参数。

### 12.7 自动化评测原则

- 能用确定性规则的，不使用 LLM Judge。
- LLM Judge 必须使用明确 rubric 和结构化输出。
- Judge 不得看到被评模型名称，避免偏差。
- 保存 Judge 输入、输出、版本和理由。
- 关键通过门槛不能只由单次 LLM 评分决定。

### 12.8 已提交 41 问的覆盖审计

41 问已经足够用于第一轮 Retrieval baseline，但不能直接视为完整 Section Contract 的全部验收题。

已覆盖较好的部分：

- 公司：成立与上市、实际控制人及链条、股权质押、历史沿革、主要子公司、主营构成、核心竞争力、研发、股权激励、关联采购、定增、授信与担保。
- 财务：合并/母公司口径、三年利润趋势、经营现金流、偿债指标、三类现金流、事务所稳定性、折旧、受限资金、部分账龄/存货/固定资产/净利率。
- 行业：细分行业定义、规模与周期、供需和成本、竞争地位、风险传导和监测指标。

仍需后续增加的 Contract 测试题：

- 公司：注册/实缴资本、经营状态、办公地址、法定代表人、供应商集中度、治理/内控、系统性诉讼/违约/退市检查、完整债务结构、非主营损益、重大投资与资产处置。
- 财务：审计意见类型、财务来源间勾稽、应收账款而非其他应收款的账龄/坏账、应付账款账龄、商誉/减值/开发支出、杜邦分析、按授信类型触发的流贷/贸易融资分析。
- 行业：3～5 家可比公司的结构化比较及可比选择理由。

这些新增题不阻塞先跑 41 问 baseline；它们用于后续验证 Section Contract 是否完整。

---

## 13. Trace、Cost、Latency 与 Audit

### 13.1 Trace Event

```python
@dataclass
class TraceEvent:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    job_id: str
    stage: str
    event_type: str
    input_refs: list[str]
    output_refs: list[str]
    status: str
    latency_ms: int
    token_usage: dict
    cost: float
    model_version: str | None
    prompt_version: str | None
    created_at: datetime
```

### 13.2 每轮 Harness 至少保存

- 当前 SectionTask 和 Information Need。
- State 摘要及版本。
- 模型收到的工具声明版本。
- 模型选择的动作与参数。
- ToolResult 状态、证据引用和错误码。
- 新增/删除的 Evidence、Claim 和 unresolved 项。
- token、成本、耗时、重试次数。
- 是否继续及 stop reason。
- checkpoint 路径。

### 13.3 Audit Package

最终任务至少能够回放：

- 使用了哪些原始文件及其哈希。
- 使用了哪个 Evidence schema、索引和检索策略版本。
- 使用了哪些 Prompt、模型、工具和规则版本。
- 每个关键 Claim 来自哪些 Evidence。
- 哪些问题经过自动修正、返工或人工确认。
- 最终导出报告对应的任务版本。

---

## 14. 错误模型与恢复

### 14.1 标准错误类型

```text
InputValidationError
ParseQualityError
SchemaMappingError
EvidenceStoreError
RetrievalEmptyError
RetrievalConflictError
ExternalSourceUnavailable
ToolTimeoutError
ToolContractError
BudgetExceededError
EvaluatorError
VerificationBlockingError
HumanConfirmationRequired
```

### 14.2 处理原则

- 错误必须进入 State 和 Trace，禁止 `except: pass`。
- 可重试错误使用有限次数和退避策略。
- 空检索结果是合法结果，不等同于系统异常。
- 外部网络失败允许降级到缓存，但报告必须标记信息截止日期。
- checkpoint 只在状态成功持久化后推进版本。
- 恢复任务时不得重复写入相同 Evidence 或 Claim。

---

## 15. 建议目录结构

这是 V2 目标结构，采用渐进新增，暂不移动 V1 已工作模块。

```text
credit-report-demo/
├── DESIGN.md                     # V1 设计
├── DESIGN_V2.md                  # 本文
├── contracts/
│   ├── schema.py                 # SectionContract / InformationNeed
│   └── loader.py                 # 从配置加载并校验章节契约
├── evidence/
│   ├── schema.py                 # EvidenceBlock / EvidenceRef
│   ├── builder.py                # parser 输出 → Evidence
│   └── store.py                  # Evidence CRUD
├── document_structure/
│   ├── schema.py                 # PageLayout / DocumentOutline / OutlineSpan / TableObject
│   ├── layout_builder.py         # 原始电子 PDF → 版本化页面布局
│   ├── outline_builder.py        # 目录候选 + 正文标题 → 标题树
│   ├── table_builder.py          # 表格对象与跨页续表关系
│   └── store.py                  # 只读派生结构的版本化持久化
├── planning/
│   └── report_planner.py         # Contract → SectionTask / InformationNeed
├── routing/
│   └── router.py                 # Need → RouteDecision
├── retrieval/
│   ├── sparse.py                 # BM25
│   ├── hybrid.py                 # fusion
│   ├── reranker.py               # 可选 rerank
│   ├── outline_indexer.py        # 标题路径、简介、子标题和表题索引
│   └── retriever_v2.py           # 统一入口 + 强制日志
├── tools/
│   ├── contracts.py              # Tool schema / ToolResult
│   ├── registry.py               # Agent 可见工具注册表
│   └── adapters.py               # 现有 DB/RAG/Web 能力适配
├── harness/
│   ├── state.py                  # ResearchState
│   ├── runtime.py                # Loop
│   ├── policies.py               # 公司/行业/项目 Policy
│   └── checkpoint.py             # 保存与恢复
├── agents/
│   ├── company_subject.py        # 逐步迁移为 Worker
│   ├── industry.py               # 逐步迁移为 Worker
│   ├── project.py                # 产品第二阶段新增
│   ├── synthesizer.py            # 改为受约束综合
│   └── verifier.py               # V1 兼容入口
├── assurance/
│   ├── citations.py
│   ├── numerical.py
│   ├── entities.py
│   ├── temporal.py
│   ├── consistency.py
│   └── decision.py
├── evaluation/
│   ├── schema.py
│   ├── datasets/
│   ├── metrics/
│   └── run_baseline.py
├── observability/
│   ├── trace.py
│   ├── cost.py
│   └── audit.py
└── llm/prompts/
    ├── router.txt
    ├── research_planner.txt
    ├── section_evaluator.txt
    ├── cross_section_synthesis.txt
    └── report_assurance.txt
```

`[已确认 A-01]` 接受新增上述一级目录，保持 Contract、Planning、Routing、Harness 和 Assurance 职责分离。

---

## 16. 核心模块接口与 CLI 契约

### 16.1 `contracts.loader`

```python
def load_contracts(path: str) -> list[SectionContract]: ...
```

CLI：

```bash
# V1 固定哈希兼容示例；不是当前 Contract v2 默认接线声明
python -m contracts.loader templates/contracts/standard_v2.yaml
# 冻结 Contract v2 的只读校验入口
python -m contracts.loader_v2 --validate templates/contracts/standard_v3.yaml
```

依赖：仅 schema 和配置文件。

### 16.2 `evidence.builder`

```python
def build(document: ParsedDocument, context: DocumentContext) -> list[EvidenceBlock]: ...
```

CLI：

```bash
python -m evidence.builder data/samples/300750/announcements/example.pdf --company 300750
```

依赖：现有 `parsers.pdf_parser`，不直接依赖 ChromaDB。

### 16.2.1 `document_structure`

```python
def build_page_layout(pdf_path: str, context: DocumentContext) -> PageLayout: ...
def build_document_outline(layout: PageLayout) -> DocumentOutline: ...
def build_outline_spans(
    outline: DocumentOutline,
    evidence_set: list[EvidenceBlock],
) -> list[OutlineSpan]: ...
def build_table_objects(
    layout: PageLayout,
    outline: DocumentOutline,
    spans: list[OutlineSpan],
) -> list[TableObject]: ...
```

CLI（名称可在实施计划中确定，但职责不得合并为第二套研究运行时）：

```bash
python -m document_structure.build_outline <electronic.pdf> --company <company_id> --validate-only
python -m document_structure.inspect_outline --document-id <id> --version <version>
```

依赖与边界：直接读取不可变电子 PDF 和当前文档身份；不依赖 LLM、Router 或业务 Contract 才能形成基础标题树。目录、书签和版式仅提供候选，正文标题、小标题和源坐标负责确认。若旧 Evidence 未覆盖原文，允许为同一文档版本追加新的 `evidence_set_version`，禁止覆盖历史 Evidence。

### 16.3 `planning.report_planner`

```python
def plan(job: ReportJob, contracts: list[SectionContract]) -> ReportPlan: ...
```

CLI：

```bash
# 当前命令仍是 V1 兼容入口；树结构任务不得借此把 V1 重新定义为当前业务权威
python -m planning.report_planner --job data/cache/job.json --contracts templates/contracts/standard_v2.yaml
```

### 16.4 `routing.router`

```python
def route(need: InformationNeed, context: RouteContext) -> RouteDecision: ...
```

CLI：

```bash
python -m routing.router --case evaluation/datasets/router/sample.json
```

### 16.5 `retrieval.retriever_v2`

```python
def retrieve(need: InformationNeed, company_id: str, policy: RetrievalPolicy) -> EvidencePack: ...
```

CLI：

```bash
python -m retrieval.retriever_v2 --company 300750 --need evaluation/datasets/retrieval/sample.json
```

### 16.6 `harness.runtime` 与 `harness.topic_runtime`

```python
# harness.runtime：历史原子执行与兼容评测入口
def run_question(need: InformationNeed, policy: ResearchPolicy) -> ResearchOutcome: ...

# harness.topic_runtime：P3R 正式生产入口
def run_topic(task: SectionTask, topic_id: str, policy: TopicResearchPolicy) -> TopicResearchPack: ...
def resume_topic(pack_run_id: str) -> TopicResearchPack: ...
```

`harness.runtime.run_question` 是历史评测和原子动作兼容入口；`harness.topic_runtime.run_topic` 是 P3R 正式生产入口。Topic runtime 可以调用原子执行器，但两者必须复用同一 Router、ToolRegistry、动作执行、证据权威校验和 Trace，不允许 `sections.topic_research` 再实现平行搜索循环。`resume_topic` 只处理仍未终态的 aspect，并保留累计预算；该 API/CLI 仅用于系统故障恢复、管理员诊断和自动化测试，不得成为当前面试版的用户继续生成入口。若为兼容性在 `harness.runtime` re-export Topic API，必须只做薄转发且由测试证明不存在第二份实现。

CLI：

```bash
python -m harness.runtime --need data/cache/needs/company_subject.json
python -m harness.topic_runtime --task data/cache/tasks/company.json --topic company_business --out data/cache/packs/
python -m harness.topic_runtime --resume <pack_run_id>
```

### 16.7 `assurance`

```python
def verify(report: ReportArtifact, context: AssuranceContext) -> AssuranceResult: ...
```

CLI：

```bash
python -m assurance.run --report data/cache/report.md --company 300750
```

### 16.8 `evaluation.run_baseline`

```python
def run_baseline(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    collection: str,
    ks: list[int],
    db_path: str = "data/chroma",
    output_root: str = "evaluation/results",
) -> BaselineRunResult: ...
```

CLI：

```bash
python -m evaluation.run_baseline \
  --dataset evaluation/datasets/v1_baseline.jsonl \
  --corpus-manifest evaluation/datasets/corpus_manifest.json \
  --company 300750 \
  --collection company_docs \
  --k 1 5 10

python -m evaluation.run_baseline \
  --dataset evaluation/datasets/v1_baseline.jsonl \
  --corpus-manifest evaluation/datasets/corpus_manifest.json \
  --company 300750 \
  --collection company_docs \
  --validate-only
```

---

## 17. V1 → V2 渐进迁移计划

### Phase 0：冻结报告契约与跑 V1 Baseline

产出：

- 标准报告 Section Contracts v1。
- 已提交的宁德时代 41 条 Information Need 基准集。
- V1 Retrieval 页级 baseline、数据质量、运行审计和检索延迟。章节/Verifier/生成成本 baseline 后置到对应评测阶段，不阻塞本阶段。

退出条件：完成 route v2 映射、页码规范化，并按 O-08 跑通第一阶段页码命中评测。

### Phase 1：Evidence Architecture

产出：

- EvidenceBlock schema。
- Chunk → Evidence 适配器。
- Evidence Store 与稳定 ID。
- Evidence 构建 eval。

退出条件：现有公司主体 Agent 可通过适配器继续工作，引用可追溯到 Evidence ID。

### Phase 1F：财务来源、对账与集中确认

在 Phase 1 文档定位能力基础上实施，必须先于财务 Worker 接入；不塞入 Baseline Runner 变更。

产出：电子 PDF 表格/附注坐标契约、SourceFinancialRecord、reconciliation/人工处理记录、FinancialSnapshot 查询与 V1 兼容适配、版本化公式及缺失值规则、集中待确认面板。

退出条件（未来完整财务交互版）：PDF/Excel 同值混合输入与单来源得到相同指标；重复上传不重复计数；母公司/合并、期间、币种、重述版本隔离；冲突值不进入计算；一次批量选择自动重算并完整复检；替换材料仅使受影响确认失效。无冲突路径零额外交互，未解决冲突阻止正式导出。当前面试版只对已经冻结的输入执行最终完整性复核并只读展示缺口，不提供材料替换、用户确认或交互式重算，边界以 §0.7 UI-01/UI-02 为准。

### Phase 2：Router + Hybrid Retrieval

产出：

- InformationNeed 和 RouteDecision。
- BM25 + Dense + Fusion。
- 统一 EvidencePack。
- Router/Retrieval eval。

退出条件：在 baseline 数据集上优于或至少不低于 V1，且延迟/成本可接受。

### Phase 3：Tool Layer + Research Harness

产出：

- Tool Registry 与结构化 ToolResult。
- ResearchState、Loop、预算、错误和 checkpoint。
- 公司与行业 Policy。
- 可真实调用的外部搜索/正文/快照适配器，包含访问失败与空结果区分。
- 单题 ResearchOutcome 兼容评测与 TopicResearchPack 正式交付。
- 由 required aspects 驱动的缺口调度、受控上下文扩读、Topic 级动态有界预算和材料/事实持久化。
- Harness eval。

历史退出条件（固定预算停止、恢复、trace、安全门）继续有效。生产内容能力追加退出条件：至少用本地叙述、本地表格/附注、结构化财务、外部时效、事件/负面核验五类 Topic 验证 Pack；命中后扩读、跨 Evidence 归拢、逐 aspect 覆盖和缺口均可审计；P4 不再依赖单题简短答案补全内容。

### P3R/P4R 前置门：树结构调整

该门位于 R2 既有只读、权威、哈希、Store 与审计能力之后、R3 aspect 调度接线之前。它不是重做 R1/R2，而是替换已经证明不可靠的“整块 Evidence + 相邻块猜边界”材料主路径。

产出：

- 每份受支持电子 PDF 的版本化只读 `PageLayout` 与 `DocumentOutline`；
- 目录/书签候选与正文一级至小标题的确定性对齐，以及显式 `unassigned` 内容；
- 可把跨多个标题的旧 Evidence 精确映射为多个 `OutlineSpan`；
- 将表题、单位、物理表头、表体、合计、续表及 component provenance 独立表达的 `TableObject`；
- 返回 node/span/table 的树感知本地检索；
- 与 TopicResearchPack/P4 引用、dependency fingerprint、stale 和旧 schema 兼容的版本化接线。

退出条件：三份真实文档和一个非 300750 fixture 证明正文不因目录误判丢失、小标题层级可用、跨标题块被正确拆分、所有正文被归属或显式标记未归属、真实表格结构可回查、检索不再把混合整块 Evidence 直接作为正式材料；主营业务、核心竞争力、主要子公司与财务附注样本的重复和跨标题污染显著下降。通过前不得进入 R3。

树子任务内部的批次口径与事实快照见 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` §9（TS0 / TS1 公共 schema·types·invariants / TS2 PageLayout / TS3 DocumentOutline + 正式 aligner / TS4 OutlineSpan + synopsis / TS5 TableObject / TS6 AspectNavigationProfile / TS7A 跨树聚合与材料库样板 / TS7B Retriever·ToolRegistry·Store·Pack·P4 provenance 正式接线），但该表不得覆盖本设计或路线图的上位语义。**整体树门的关闭同时要求 TS7A 与 TS7B 完成**；TS0～TS4 已关闭。TS5 已执行编码与真实运行，但 `tree_table_ts5_20260919T195556Z` 机器门未通过、三份真实文档被拒绝、固定样本 9/10 失败，故 **TS5 未关闭**；TS6/TS7A/TS7B 与正式 R3 尚未开始。失败产物原样保留，不能因 9·30 Demo 里程碑而改写。

### 2026-09-30 Demo Backbone：时间盒式纵向演示通道

在不改变上述正式树门的前提下，`2026-09-30_DEMO_BACKBONE_MILESTONE.md` 授权一个代表性纵向演示通道：选择少量冻结 Contract topics/aspects，经同一 Evidence/结构层、Router/ToolRegistry/Harness、Pack、按 §0.20 逐句引用写作与核对、独立 Review Agent、确定性 Assurance Controller 和 Streamlit 产出可回查预览。Demo 与正式轨共用唯一当前生产来源和新版本接口，不另建研究运行时；Demo 只收窄 Contract 覆盖。未覆盖范围进入 scope/gap/blocked 状态；该通道可预览不等于任何正式阶段关闭。

**未签发/未获资格的表对象**仍不得作为正式事实或数字权威（§0.17 第 3 条）。但自 §0.17（2026-09-28）起，**演示主题的目标表**另有要求：它们必须**逐对象取得正式资格并经现有图/工具链进入 Pack**——这是「本演示主题所需的那几张表」的取材门，**不是**「所有类型的 TS5 表格能力已正式关闭」的声明。TS5 仍 no-go/未 seal/未关闭，历史 run 与信任根不改写。

### Phase 4：第一阶段章节契约化

产出：

- 公司、财务、行业 Worker 按 Contract 消费 TopicResearchPack/FinancialFactPack 与精确 material context，直接输出逐句引用的自然正文和必要的结构化 `FollowUpNeed`；财务权威表由确定性渲染生成，合格原始业务表经 §0.19 图侧单通道进入 Pack 和读者面。
- 逐句确定性硬核对与独立只读语义审阅分开留账；旧 `ClaimCandidate`/Binding/Entailment/`SectionClaim` 写作链保留历史读回，不再是新正文的成立条件。Writer 不检索、不联网、不计算、不自批（§0.20）。
- Section Evaluator 和质量门。

章节关闭必须同时满足安全正确性与内容完整性：必需 aspect 有明确覆盖或缺口；正文不是 Q&A/Claim 清单；主营业务、行业情况、重大事项等宽主题应体现 Pack 中已验证的构成、过程、变化、原因和风险传导。不能用“没有错误事实”替代“完成了该主题研究”。

项目分析作为产品第二阶段单独排期，在固定资产贷款/项目贷款分支中实施，不阻塞 V2 第一阶段。

### Phase 5：Synthesis + Report Assurance

产出：

- 确定性组装。
- Claim 驱动的跨章节综合。
- 隔离只读 `Independent Review Agent`（整体风险、只输出 `ReviewIssue[]`、不重复逐条 hash/locator 门、不放行）+ 内容完整性前置门 + 六类 Assurance，由受限 Controller 确定性聚合。
- 报告版本绑定的系统审核门；流程完成 / 预览可用 / system-Assurance / 人工最终接受 / 正式阶段关闭彼此分离，最高自动状态为“可供人工确认”。

### Phase 6：UI 与演示打磨

产出：

- Streamlit 展示真实阶段状态、流程完成/预览/系统审核/人工确认四类状态、证据来源、未解决问题和质量门结果。
- 缺口面板当前只读展示，不实现用户补件、绑定、Evidence 更新或继续生成；保留未来扩展字段。
- 保留一键 Demo。
- V1/V2 切换或回退开关。

9·30 Demo Backbone 可以先交付上述 UI 的代表性只读切片：默认加载持久化真实 run，展示来源、材料、代表性正文/财务/行业内容、缺口、独立审查和系统状态。它不要求完整四章或正式 Phase 6 关闭，也不提供补件、续跑或人工批准按钮。

---

## 18. V2 第一阶段验收标准

以下是建议验收标准，具体数值应在 V1 baseline 后冻结：

1. 同一输入可以生成可重复的 ReportPlan 和 SectionTask。
2. 每个关键 Claim 可经 `OutlineSpan`/`TableObject` component provenance 追溯到 Evidence ID，或追溯到结构化财务/外部快照权威结果；导航标题和简介不得充当证据。
3. 任何 RAG/Web 查询均有完整日志和 trace。
4. 财务章节不存在由 LLM 新计算的数值。
5. Research Harness 在预算内停止，并记录明确 stop reason。
6. 章节缺失证据时明确输出 unresolved，不编造补齐。
7. Synthesizer 不产生输入 Claim 中不存在的新事实。
8. Assurance 能识别预置的数值、实体、时效、引用和跨章节错误。
9. 任务失败后可从最近 checkpoint 恢复，或明确重新开始的原因。
10. 完整任务可统计各阶段 latency、token、成本和错误。

---

## 19. 本轮决策清单

### 19.1 已经完成确认

- [x] D-01～D-08：阶段范围、报告目录、综合评价边界、输入类型和导出门禁。
- [x] C-01/C-02/C-04：公司研究主题、必答项和引用粒度。
- [x] F-01～F-04：财务结构、指标扩充、15% 重大性阈值、暂不做完整同业对标。
- [x] I-01～I-04：细分行业深度、可比公司目标与不足时降级、公开来源和 2 年时效设置。
- [x] P-01～P-05：第二阶段项目材料、预测 Excel、IRR/盈亏平衡点、压力情景和触发条件。
- [x] S-01～S-03：只评价用户方案，不主动设计新方案，不自创评级。
- [x] E-01～E-03：表格结构、网页快照和 Evidence 本地长期版本化保存。
- [x] R-01/R-02：先 RRF，额外 reranker 由效果/资源评测决定。
- [x] H-01～H-04：研究轮数、动态 Need、有界预算和 checkpoint；H-03/H-04 的用户继续生成入口已由 UI-01/UI-02 修订为当前面试版不交付。
- [x] F-05/F-06：财务冲突确认与补件闭环作为历史底座/未来扩展保留；当前面试版仅只读展示冲突和缺口。
- [x] V-01～V-05：blocking、视觉提示、修复后复检、受限 Assurance Controller、防自我证明和人工最终确认。
- [x] EV-01/EV-02/EV-04：已提供 41 问及页码，不安排第二人工评审。
- [x] A-01：接受新增 V2 一级目录。
- [x] O-01～O-10：电子 PDF 边界、冲突处理、外部核验降级、异常门禁、时效窗口、风险阈值、Evidence 删除、首轮 Retrieval 评测范围、正式结论截止日及外部来源充分性。
- [x] SC-01～SC-05：Section Contract 阻断边界、财务最低分析基础、行业来源/代理/可比公司、综合影响范围、other 授信类型处理，全部正式确认（规则与确认状态固化于 `contracts/sc_decisions.yaml`，见 §19.5）。

### 19.2 Baseline 已确认事项与后续 Contract 复核

- [x] B-01：正式命中严格页码相等，±1 页仅作为诊断。
- [x] B-02：external-only 排除出本地 Retrieval 总分，混合题只评价本地部分。
- [x] B-03：`Macro RequiredPageCoverage@10` 等权，不按 P0/P1 人为加权；同时强制展示 P0 `RequiredPageCoverage@10` 独立关键指标。
- [x] B-04/B-05：全部必需本地页可靠映射后整题参评；41问全部页码为且关系。
- [x] Phase 0B 的 `SectionContract` v1 及 SC-01～SC-05 已完成业务复核；P3R R1-A 已完成全部 52 问的 aspect/evidence/source/display/not_found 审计并冻结兼容 Contract v2，不覆盖 v1 或历史指纹。

### 19.3 历史技术拆分（已落地，不是当前待办）

- 公司/行业主题已拆为 KeyQuestion 和 CompletionRule；P3R R1 只做粒度与来源充分性审计，不重新创建一套 Contract 系统。
- Financial V2 的公式、科目依赖、来源、对账、核准快照和缺失值规则已落地；现行口径继续以 `FORMULA_REVIEW.md` 为准。
- 41 问页码、route 标签、Router、Evidence、工具错误码、状态事件和 checkpoint 已形成历史冻结基线；P3R 在其上扩展 Topic 级调度，不回写 frozen 结果。
- 当前尚未落地的对象、顺序和验收只看 §20、`V2_IMPLEMENTATION_PLAN.md`、`V2_TODO.md` 与 P3R/P4R 权威任务书。

### 19.4 技术实现自由度（受当前任务书与冻结边界约束）

- dataclass 的字段拆分和内部命名。
- BM25 的本地实现方式。
- 新增算法参数可通过独立 eval 决定；已经冻结的 RRF/Router 参数不得借本条重新调优。
- Trace 文件格式和 span ID 生成方式。
- checkpoint 的序列化实现。
- V1 兼容适配器的内部组织，但不得让 V1 路径重新成为 V2 正式内容主链。

### 19.5 SC-01～SC-05 最终规则（Phase 0B 固化）

阻断范围与问题状态正交（状态说明“缺什么”，阻断等级说明“后果多大”）：
`JOB_BLOCKED`=基础前提错误，整个任务暂停并保留 Checkpoint；`SECTION_BLOCKED`=其他章节继续，但当前章节无法形成有效结论；`REPORT_BLOCKED`=继续生成带问题预览，但禁止正式导出；`NONE`=不阻断。`WAITING_HUMAN` / `CONFLICT` 不直接等于固定阻断等级。复合阻断以后果集合表达（如 `SECTION_BLOCKED + REPORT_BLOCKED`）。

- **SC-01 公司信用**：主体/股票代码/材料主体无法一致确认 → `JOB_BLOCKED`；主营业务完全无法确认 → `SECTION_BLOCKED`；控股股东或实际控制关系无法确认、重大债务/金融机构借款/对外担保因材料明显缺失无法核实 → `REPORT_BLOCKED`；合法无实际控制人 → `SATISFIED`+`NONE`；已执行检索未发现 → `NOT_FOUND_AFTER_SEARCH`+`NONE`（记录检索范围/来源/截止日期，不得写“确定不存在”）；客户/供应商名称依法未披露但集中度已披露 → `SATISFIED`+`NONE`；股权激励不适用 → `NOT_APPLICABLE`+`NONE`；研发/新业务/管理层履历等非核心不足 → 缺口预览不阻断。
- **SC-02 财务**：最低正式分析基础 = 最新完整年度三张主表 + 审计意见；趋势分析原则上覆盖近三年；最新季度/半年可用则纳入，否则披露缺口、不一刀切；不要求三份独立审计报告（可从历年年报/最新年报比较披露取得）。缺最新完整年度任一主表、或报告期间/金额单位/合并或母公司口径无法确认 → `SECTION_BLOCKED` + `REPORT_BLOCKED`（复合）；关键数字未解决冲突 → 暂停受影响计算与 Claim，同时 `REPORT_BLOCKED`；个别历史期间/附注明细/非关键字段缺失 → 缺口预览；缺分母不得计算、不得 LLM 补算。
- **SC-03 行业**：来源 A/B/C/D 四级（A=监管/政府/交易所，B=行业协会/研究机构/公司公告，C=券商/财经媒体/头部披露，D=来源不明/聚合转载）。A/B 级来源可单独支持一般事实性结论；关键负面结论、主体重大变化、重大风险、关键行业规模/份额结论，至少需要“1 个直接支持的 A/B 级来源”或“2 个相互独立、内容一致的 C 级来源”。单一 C 级只作线索或带限制的非关键说明；D 级不得作为关键结论唯一依据；发布日期未知的内容不得支持强时点结论。来源不足时写“在明确列示的检索范围内未发现相关事项”或“未能核实”，形成显式 gap 并列出未来建议材料类型，不得写成“不存在”或把“待补充”表现为当前可执行动作；是否阻断由该 gap 的 `impact_scope` 和 Contract blocking 规则决定，不能以“来源等级低”一刀切。代理指标记录六项：原目标指标/实际替代指标/替代理由/来源日期/口径/局限性。只有完成 Contract 规定的来源范围、最小尝试及替代来源策略后，才可使用 `NOT_FOUND_AFTER_SEARCH`；预算耗尽但有效尝试不足只能是 partial + explicit gap。仅核心内容整体不足（无法确定所属行业/无法形成基本供需竞争政策判断/无法说明风险传导/检索后无合格替代分析）才 `SECTION_BLOCKED`。可比公司 3~5 家是目标不是门禁（1~2 家说明限制、无直接可比用相近、无合理可比说明不可比；不因数量不足自动 `REPORT_BLOCKED`、不强行选不可比公司）。
- **SC-04 综合**：上游仅非核心 `NOT_PROVIDED`/`NOT_FOUND_AFTER_SEARCH` → 带缺口预览；上游影响主体/偿债/关键数字/授信方案的问题 → 不得生成受影响结论；任一上游 `SECTION_BLOCKED` → 综合只能说明无法完成对应判断；存在相关 `REPORT_BLOCKED` → 允许预览、禁止导出；不因任意 `WAITING_HUMAN` 停止全部。通过结构化 `impact_scope`（subject/solvency/key_financial/credit_scheme）判断影响面，不得由 LLM 临时决定。
- **SC-05 other**：先跑通用契约、不自动启动专项分析。当前面试版若初始输入未给出具体业务类型，只读展示该缺口、影响和建议未来提供的业务类型，仍允许通用预览，不在同一任务内补件或续跑；综合必须提示“尚未按具体授信业务类型追加专项分析”。未来版本可在新任务或获批交互扩展中启用对应专项分析。

### 19.6 E1-01～E1-05 最终规则（Phase 1 编码前冻结）

- **E1-01 表格能力边界**：Phase 1 冻结完整表格 Evidence schema，并先验证电子 PDF 表格坐标抽取可行性；现有纯文本 `TextChunk` 只能生成 paragraph/heading，不得伪装成 table/table_row。可靠抽取成功后才生成结构化表格 Evidence；失败时标记 `TABLE_STRUCTURE_UNAVAILABLE`。财务表格的完整抽取、勾稽和对账仍属于 Phase 1F。
- **E1-02 Evidence Store**：使用项目现有 SQLite 保存权威 Evidence、文档版本和引用关系，结构化 payload 可使用 JSON 字段；不新增数据库服务。ChromaDB 仅作为可以重建的检索索引，不是 Evidence 的唯一权威存储。
- **E1-03 文档身份与版本**：首次上传由任务/ingest 登记或生成稳定 `document_id`，后续同一业务文档复用；文件名只作来源名称。文件内容哈希决定 `document_version`。无法可靠判断是否同一业务文档时不得擅自合并。
- **E1-04 删除与保留**：Phase 1 不提供物理删除，只提供可删除性检查和标记停用；被 Claim/报告引用的版本拒绝删除。完整删除和级联规则在后续引用关系及 UI/Assurance 接入后实现。
- **E1-05 状态栏范围**：Phase 1 实现真实 `ProgressEvent`、checkpoint、CLI 状态和现有 Streamlit 动线中的简单只读展示；不建设完整任务中心、暂停控制、人工确认 UI、多用户队列或通用调度平台。

### 19.7 FA-01～FA-06 最终规则（Phase 1F-A 编码前冻结）

- **FA-01 来源范围**：首版支持上市公司年度/中期/季度报告中的三张主表及必要附注电子PDF，以及用户上传的 `.xlsx` 财务报表；征信报告先登记来源并预留债务对账接口，不承诺自动解析所有征信格式。
- **FA-02 差异容差**：同口径标准值只有在差异不超过来源展示精度造成的舍入上限时才算一致；明确单位/小数位时按半个最小展示单位计算，未知精度或单位时非零差异均为冲突。15%重大科目阈值不得用于掩盖对账差异。
- **FA-03 单来源准入**：单一合格来源在主体、期间、币种、scope、单位明确且同源勾稽通过时可以进入快照并标记 `SINGLE_SOURCE`，不强制等待第二来源；新增同口径来源发生冲突时转人工确认。
- **FA-04 LLM映射边界**：规则唯一匹配可自动批准；LLM只提供科目映射候选和置信信息，必须经过确定性校验或集中人工确认后才能进入快照，不以多个模型投票替代确认。
- **FA-05 选择理由**：首版理由为 `AUDITED_SOURCE`、`LATEST_RESTATEMENT`、`SCOPE_MATCH`、`PERIOD_MATCH`、`CORRECTED_MATERIAL`、`OTHER_WITH_NOTE`，最后一项必须填写说明。理由只记录人工选择依据，系统不得据此自动选择来源；不同期间、scope、币种或重述版本不属于同组冲突。
- **FA-06 公式确认门**：技术方先生成 `FORMULA_REVIEW.md`，业务方只复核有歧义的科目、平均值、利息、EBITDA、自由现金流和专项公式口径；确认后再实现 Formula Registry。A1～A5不受阻，A6必须等待公式复核，不得由开发代理自行越过。

---

## 20. 当前建议的下一步

推荐按以下顺序推进：

1. 保留 R0、R1-A、R1-B 与 R2 已完成的 Contract、Pack schema/Store、只读访问、权威、双哈希、trace、显式引用和 fail-closed 基础，不再围绕个别 seed、页码或相邻块继续局部打补丁。
2. 原样保留 TS5 no-go 真实运行及所有失败门；9·30 前不再以完整 TS5 关闭作为交付主线，未签发表格不得进入正式事实。
3. 按 `2026-09-30_DEMO_BACKBONE_MILESTONE.md` 推进代表性纵向链：M930-0/1/2 已完成并工程封存；M930-3 **未关闭**。当前先对账取材与 Pack，按 §0.19 修目标原表的逐表完整证明及图/工具/Pack 单通道，再按 §0.20 实施 Pack 直接写带引用正文、逐句硬核对与独立审阅。修改代码前完成上位文档一致性和 file-level 影响/迁移方案；文字与合格原表必须在同一版本真实产物共同可读。
4. Demo Backbone 必须沿同一生产来源，以少量真实内容展示结构材料、Pack、逐句引用正文、**合格业务原表**/缺口、独立 Review Agent、确定性 Assurance 与只读 UI；不完整内容保持 `preview_only/blocked/not_reviewed`。旧 Claim 账可查但不是新正文的必经接口。
5. 正式能力轨保持：完成 TS5 → TS6 → TS7A → TS7B 后再正式进入 R3，对 115 个 `topic_harness` aspects 执行 Contract 驱动调度；随后按 R4/R5/R6/R7 与 Phase 5/6 的原关闭门推进。
6. 10·1～10·7 优先回到 TS5 实质性 residual、完整树门、跨公司泛化、性能与内容覆盖；不得用已完成的 Demo Backbone 替代这些工作。

目前不需要业务方逐章手写所有表达。业务方只需复核正式 Contract 的业务语义、来源门槛和真实纵向切片是否达到授信报告深度；技术字段、调度器和材料包内部结构由任务书约束下的实现负责。

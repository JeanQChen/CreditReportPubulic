"""Eval 跑分入口。

用法: python -m evals.run_evals [--real-llm]
"""

import importlib
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL_MODULES = [
    "evals.test_schema",
    "evals.test_template",
    "evals.test_metrics_pure",
    "evals.test_db",
    "evals.test_excel_parser",
    "evals.test_metrics_db",
    "evals.test_pipeline",
    "evals.test_schema_mapper_llm",
    "evals.test_analyzer",
    "evals.test_assembler",
    "evals.test_pdf_parser",
    "evals.test_retrieval",
    "evals.test_company_subject",
    "evals.test_industry",
    "evals.test_synthesizer",
    "evals.test_verifier",
    "evals.test_word_exporter",
    "evals.test_baseline_runner",
    "evals.test_contracts",
    "evals.test_contract_v2_assets",
    "evals.test_evidence",
    "evals.test_financial_v2_schema",
    "evals.test_financial_v2_snapshot_schema",
    "evals.test_financial_v2_store",
    "evals.test_financial_v2_source_registry",
    "evals.test_financial_v2_migration",
    "evals.test_financial_v2_decimal",
    "evals.test_financial_v2_metadata_confirmation",
    "evals.test_financial_v2_extractors",
    "evals.test_financial_v2_pdf_extractor",
    "evals.test_financial_v2_mapping",
    "evals.test_financial_v2_normalization",
    "evals.test_financial_v2_checks",
    "evals.test_financial_v2_reconciliation",
    "evals.test_financial_v2_resolutions",
    "evals.test_financial_v2_resolutions_ui",
    "evals.test_financial_v2_snapshot_store",
    "evals.test_financial_v2_snapshots",
    "evals.test_financial_v2_formulas",
    "evals.test_financial_v2_metrics",
    "evals.test_financial_v2_invalidation",
    "evals.test_financial_v2_adapters",
    "evals.test_financial_number_identity",
    "evals.test_financial_note_detail",
    "evals.test_evidence_facts",
    "evals.test_financial_v2_progress",
    "evals.test_financial_v2_a7_integration",
    "evals.test_financial_v2_snapshot_admission",
    "evals.test_financial_v2_snapshot_admission_p0",
    "evals.test_routing_schema",
    "evals.test_db_targets",
    "evals.test_router",
    "evals.test_periods",
    "evals.test_router_audit",
    "evals.test_context",
    "evals.test_indexer_v2",
    "evals.test_sparse",
    "evals.test_fusion",
    "evals.test_retriever_v2",
    "evals.test_router_eval",
    "evals.test_retrieval_v2_runner",
    "evals.test_perf_compare",
    "evals.test_tool_contracts",
    "evals.test_tool_registry",
    "evals.test_tool_adapters",
    "evals.test_external_v2",
    "evals.test_external_v2_store",
    "evals.test_llm_client",
    "evals.test_llm_call_budget",
    "evals.test_m930_3_batch_a_sources",
    # 跨文档联合检索 §L1.5：**逐 aspect × 逐来源责任模型**（`asr-1`）。责任只能在检索前由
    # 输入轴定死；`proof_required` 是三值，`undetermined` 是一等结论（不得压成 true/false）。
    "evals.test_aspect_source_responsibility",
    "evals.test_m930_3_rejection_retention",
    # M930-3 §三/3：**本轮未检索外部来源**的诚实记录。冻结 Contract 声明某些 aspect 的来源类
    # 只能是 `external`，`acc-30` 起正式 `InformationNeed` **真的**带出了这条要求（注入换成生产
    # 实现，用同一个权威派生器 `TS.derive_support_eligibility`）。原因码是封闭集：开关为关
    # （外部动作执行前被拒）；或注入的构造器又是已知结构替身（历史原因）。反例面：把「未检索」
    # 写成「已证明没有」或「已检索未取得」、把三侧合并成一条结论、让原因/终态字段变成常量。
    "evals.test_m930_3_external_retrieval_honesty",
    # M930-3 步骤 ③：冻结 aspect → 正式 `InformationNeed` 的**生产接线** + 外部授权的**执行前门**。
    # 正面：冻结 `external` ⇒ need 带 `external` ⇒ 真实路由落 `EXTERNAL_RESEARCH`（同一句问题由
    # 旧结构件投出则不落，证明差异来自来源类）。唯一真值：与派生器逐 aspect 相等（含 authority
    # 分支，`supplemental_only` 不得抬成 required）。具名限制：不反推 `evidence_kind`、窗口 token
    # 不当日期（并证明这条限制是活的）。授权门覆盖的动作集必须与 `ACTION_ROUTES` 恰好相等。
    "evals.test_m930_3_aspect_need_builder",
    # M930-3 批次 B §二 2.3：候选的内容资格与三条互斥去路（材料 / 结构 gap / 内容处置）。
    "evals.test_m930_3_material_qualification",
    # 第二批（表格的正式图返回路径）三条。它们**必须**在这里：本批在磁盘上建了模块却没登记，
    # 于是「回归从不执行」——`evals.test_eval_suite_shape` 正是为这件事立的（它当场点名了这三条）。
    # 三条各自钉住的：`tobj-1` 放行通道（三条续接门 + 两条正交身份 + 页号不进身份）、
    # `tom-1` 材料通道（阅读材料身份 ≠ 数字权威，且两者都进 payload 哈希）、
    # 以及树工具 v6 在**真实链**上把每个定位块的合格表对象同时交出一份正式 Pack 材料。
    "evals.test_m930_3_table_object_release",
    "evals.test_m930_3_table_object_materials",
    "evals.test_m930_3_tree_table_branch",
    # 第三批（图侧已构建对象的**逐格放行**通道）。与上面两条平行、身份不得互认：`gto-1`
    # 只把**已由 `live_table_source` 签发**的表对象逐格复核来源后放行，不重切、不重解析。
    # 与上面第二条同一教训：本模块在磁盘上存在却没有导入方、也没有登记——不登记就等于
    # 「回归从不执行」，因此这里必须显式在册。
    "evals.test_graph_table_release",
    # 第四批（§0.18 W8 单通道的材料化端）：`gto-3` 逐表证明过的放行记录 → `gtm-1` 正式
    # Pack 材料（逐格读视图 / 声明读数 / 内容寻址身份 / 依赖指纹 / 十条 typed 拒绝 /
    # 独立重切解析器）。它此前只在**工具面**被间接走到；模块自身的判据一条都没被单独钉住。
    "evals.test_m930_3_graph_table_materials",
    # M930-3 返修 P0：事实期间链（所引原文 → 候选 → 唯一构造器 → 结果 → 写作侧门）。
    "evals.test_m930_3_period_chain",
    # M930-3 返修 P1：写作「被提供面」（请求面 == 门迭代面）+ 逐轮修订 + 排除原因封闭词表。
    "evals.test_m930_3_writer_face",
    # M930-3 返修 P2：写作收口——边界感知的实体表面判定（不跨接缝合成 token，但衔接区自造
    # 主体名/数字/含糊期间照旧被拒）+「一句就是一个句子」（`A。同时B。` 不算多 Claim 自然句）。
    # 必须排在 writer_face 之后：它复核的是同一批写作产物在**门后**的两条判据。
    "evals.test_m930_3_writing_closure",
    # M930-3 返修 P3：财务**读者可见面**——期间口径（end 时点 / flow 期间）只能由权威自己的
    # `statement_type` / `period_requirement` 决定；列名与正文期间写期间表达而不是裸期间末日；
    # 代理口径（`CALCULATED_PROXY`）的口径限定语必须落在读者读数字的那一格（表格单元格），
    # 只藏在 Claim 文本或 claim_id 链里不算披露。反例按 §5 第 12/13 行各配正反两面。
    "evals.test_m930_3_financial_face",
    # M930-3 返修 P4：**主体与报告日**——主体由报告输入声明（`subj-1`）后按该主体核对快照与
    # current Evidence Set（不再取库里排序第一条），报告参考日与快照选择日版本化分开
    # （`rc-rd-1`）。反例自带「旧查询会返回另一个主体」的对照面，证明判据不是空断言。
    "evals.test_m930_3_subject_and_report_date",
    # M930-3 返修 P5：**失败也可审计**——新增只读诊断产物 `failure_diagnostics.json`。没有
    # `SectionDraft` 的节逐 aspect 写「本 run 未能读回验收」（**不**写成「已证明没有材料」），
    # 读不回的层是 `None` 而不是 0/`[]`。判据自带对照面：同字段在可读回时必须是 0/`[]`/dict。
    "evals.test_m930_3_failure_diagnostics",
    # M930-3 §四 读者面（`pwr-4`）：人读正文的**单位 / 主体 / 代理口径 / 缺口**四件事。
    # 单位按封闭表译成中文且与单元格显示同向（元数据 `unit` 一字不改）；主体写成
    # 「可核实名称（主体标识）」、没有名称时退回标识而不发明；代理口径的权威限定语逐字留在
    # 单元格里、**另加**一行中文口径说明；缺口文案标识保留、`key=` 字段语法不留，而类型化
    # 的 `state` / `reason_code` / `blocking_effects`（含 `SECTION_BLOCKED`）一字不改。
    "evals.test_m930_3_reader_face",
    # M930-3 指令 E 第 3 项：门前**自然草稿层**（`narr-8`：材料出处轴 `source_member_refs`
    # 与权威事实出处轴 `source_fact_refs` **互斥**、恰好一条非空）+ 门后**最终句保真核对**
    # （`natfid-1` / `ng-14` / `nrules-15`）。本门第一次在某一句类上把「逐字等于 Claim 文本」
    # 换成保真边界，因此这里同时钉住：新判据的七轴内容、唯一放宽处（单字阈值）、草稿层非空的
    # `SectionDraft` **必须构造得出来**（本批实测到的阻断性不动点缺陷的回归）、`natural` 在冻结门
    # 里是**新增一支**而不是删掉第 8 条、两条出处轴各带正反例（错轴 / 双轴 / 空轴 / 越界键），
    # 以及本核验**没**覆盖什么（主体泛指等三处边界）。
    "evals.test_m930_3_sentence_fidelity",
    # M930-3 指令 D 第四项：**最终句语义门 B**（`nsfid-1` / `nsfr-1`）与 `natfid-1` **并存**。
    # 这里钉住的是「表面比较器放行、语义门拒绝」那三处盲区的**可执行**读数（`变动为` → `变动约为`、
    # `资产负债率` → `有息负债率`、丢掉 `代理口径（PROXY_FINANCE_EXPENSES）`），以及在 `nsfr-1`
    # 里判定器看不到这两类的原因（词表就是答案关键词，因此只能由判定面补出）、三档 verdict 的
    # 逐字段约束、聚合由逐原子推出的唯一口径、决定身份与三种过期锚点、以及**没**覆盖什么
    # （表格单元格没有 `sentence_id`，是一条已登记的缺口）。
    # `nsfr-2`（r9 后的定点返修）：输入面改成**逐句预填槽位**（判定字段一律留空，「留空不是通过」），
    # 冻结的 `final_sentence_fidelity_v1` 与其指纹仍在册。§12/§13 钉的是：槽位逐行作答即通过，
    # 把两个年份合并成一条更宽的表述、或漏答其中一个年份，都必须因「已定位表面无人认领」
    # fail-closed（r9 的真实返回正是这一条），改指标与丢代理口径限定语各有一条同面孔的反例。
    "evals.test_m930_3_final_sentence_fidelity",
    # M930-3 §12.4.4 **第 6 步**（只读展示）：人读产物里逐句显示最终句语义决定的状态。
    # 这里钉的是「只读」这件事本身——展示函数的引用面（不碰库/网络/文件/模型/判据入口、
    # `NS.` 上只碰三个名字）、不自行发明档位（源码里没有那四个档位的字面常量）、
    # 「没有决定」与「没有对象可核」都**不**读成「通过」，以及覆盖面边界（表格行不带
    # 句子身份）如实写在产物里。第 6 步的另一半（**不**实现用户补件 / 缺口绑定 / 证据更新 /
    # 用户触发继续生成）由展示函数**没有**任何写入口来体现，同样在本模块里复核。
    "evals.test_m930_3_final_sentence_display",
    "evals.test_harness_schema",
    "evals.test_harness_budget",
    "evals.test_harness_state",
    "evals.test_harness_aspects",
    "evals.test_harness_entailment",
    "evals.test_harness_structured_needs",
    "evals.test_harness_structured_provenance",
    "evals.test_harness_snapshot_lock",
    "evals.test_harness_checkpoint",
    "evals.test_topic_pack_store",
    "evals.test_topic_pack_contract_reachability",
    "evals.test_evidence_reader",
    "evals.test_context_expansion",
    "evals.test_topic_boundary",
    "evals.test_r2_boundary_gate",
    "evals.test_r2_rolling_closure",
    "evals.test_topic_pack_material_payload",
    "evals.test_topic_materials",
    "evals.test_set_enumeration",
    "evals.test_source_object_inventory",
    "evals.test_r2_dependencies",
    "evals.test_material_slice_runner",
    "evals.test_r2_boundary_semantics",
    "evals.test_r2_six_state_acceptance",
    "evals.test_r2_boundary_aggregation",
    "evals.test_r2_explicit_reference_audit",
    "evals.test_r2_reference_binding",
    "evals.test_r2_reference_occurrence",
    "evals.test_r2_v14_closure",
    "evals.test_r2_table_continuation",
    "evals.test_credit_semantics",
    "evals.test_credit_fact_extraction",
    "evals.test_credit_authority",
    "evals.test_six_category_acceptance",
    "evals.test_harness_runtime",
    "evals.test_demo_preflight",
    "evals.test_actual_path_41",
    "evals.test_research_preview",
    "evals.test_split_manifest",
    "evals.test_report_planner",
    "evals.test_planner_readonly",
    "evals.test_topic_research",
    "evals.test_topic_research_sections",
    "evals.test_chapter_writer",
    "evals.test_phase4_vertical_slice",
    "evals.test_phase4_guardfix",
    "evals.test_phase4_contract_slice",
    "evals.test_phase4_formal_chain",
    "evals.test_phase4_service_formal_chain",
    "evals.test_section_schema",
    "evals.test_section_material_bundle",
    "evals.test_section_store",
    "evals.test_section_financial_worker",
    "evals.test_section_research_workers",
    "evals.test_section_evaluator",
    "evals.test_section_llm_evaluator",
    "evals.test_section_rework",
    "evals.test_section_audit_opinion",
    "evals.test_section_service",
    "evals.test_section_streamlit_ui",
    "evals.test_section_artifact_loader",
    "evals.test_intermediate_preview",
    "evals.test_section_eval_runner",
    "evals.test_phase4_pipeline_integration",
    "evals.test_phase4_demo",
    "evals.test_tree_structure_focused",
    "evals.test_tree_structure_schema",
    "evals.test_tree_structure_adversarial",
    "evals.test_tree_page_layout",
    "evals.test_tree_aligner",
    "evals.test_tree_outline_builder",
    "evals.test_tree_outline_baseline",
    "evals.test_tree_outline_hierarchy",
    "evals.test_tree_heading_qualification",
    "evals.test_tree_alignment_partition",
    "evals.test_tree_structure_artifacts",
    # TS4-A（§18.13）：记录层 8 个版本化顶层对象、唯一正式组合根与正式复核层。
    # 顺序即依赖顺序：类型/输入/坐标 → 构建 → 复核 → 简介 → 三层守恒 → 产物 →
    # 非 300750 fixture → 完成判定 A/B 真值表 → 正式链路（live 正向）。
    "evals.test_tree_span_schema",
    "evals.test_tree_span_input",
    "evals.test_tree_span_coords",
    "evals.test_tree_span_builder",
    # TS4-A P1-A 反例（表格 provisional 邻接闭包）：必须在构建器之后运行——它独立
    # 重算同一批真实冻结版式上的闭包成员资格与边界。
    "evals.test_tree_span_table_adjacency",
    # W1（`sb-7 → sb-8`）：跨页 `table_inside` 在构造期按页分段。紧跟在正文范围切分
    # 与邻接闭包之后运行——它直接在这些函数产出的 `_LineFact` 序列上断言分段行为。
    "evals.test_tree_span_cross_page_table",
    "evals.test_tree_span_verifier",
    "evals.test_tree_synopsis",
    "evals.test_tree_span_conservation",
    "evals.test_tree_span_artifacts",
    "evals.test_tree_span_fixture",
    "evals.test_tree_span_completion",
    "evals.test_tree_span_formal_chain",
    # TS4-B（§18.3.5 / §18.14）：资格策略冻结（approval / frozen 资产、A/B 身份分离、
    # 历史 A 读回）。必须排在 TS4-A 各模块之后——它只读已封存的 A 产物与版本化策略目录。
    "evals.test_tree_span_policy_b",
    # TS5（§19）：TableObject 材料/导航对象、cell 来源与可引用性、typed relations、
    # 持续续表、四层守恒、权威隔离、final span / 快照 / 终端能力、正式链路与产物。
    # 顺序即依赖顺序：wire/schema 身份 → 几何 → 构建 → cell 来源 → 关系 → 续表 →
    # 守恒 → 权威隔离 → 正式链路（live 正向）→ 独立复核器与产物。
    "evals.test_tree_table_schema",
    "evals.test_tree_table_geometry",
    "evals.test_tree_table_builder",
    "evals.test_tree_table_cells",
    "evals.test_tree_table_relations",
    "evals.test_tree_table_continuation",
    "evals.test_tree_table_conservation",
    "evals.test_tree_table_authority",
    "evals.test_tree_final_material",
    "evals.test_tree_table_formal_chain",
    "evals.test_tree_table_artifacts",
    # M930-3 写作主链 + 跨文档联合检索：**这批模块一直存在于磁盘上，却从未登记进本清单**
    # （2026-09-25 全仓核对：43 个模块只在手跑时被执行）。后果是「完整 eval 全绿」并不覆盖
    # 主链本身——其中包括 Claim Binding Gate / Entailment Gate / FollowUpNeed / 自然
    # Narrative / 跨文档分派 / 导航层级回读 / 验收门这些本里程碑的核心判据。
    # 逐个手跑（2026-09-25，`DEEPSEEK_API_KEY=` 空值，禁真实调用）后全部通过：合计 6040 项
    # 通过、4 项 typed skip（skip 详情在各模块内说明其在本仓不可达），其中
    # `evals.test_demo_topic_store_migration` 暴露出一条**未被任何既有运行覆盖过的**陈旧版本
    # 针（store 迁移台账 4→5 那次前进留下的 `_versions(db) == ["1","2","3","5"]`），
    # 已改为从台账派生（`[v for v in known if v != "4"]`），不再写死版本列表。
    # 顺序即依赖顺序：wire/schema 与 store → 身份/树/材料/事实 → 决定与绑定 → narrative →
    # section 链与 follow-up → Writer 各相 → Pack/组装 → 跨源分派与导航 → 验收门与产物。
    "evals.test_demo_backbone_schema",
    "evals.test_demo_backbone_artifacts",
    "evals.test_demo_scope",
    "evals.test_demo_source_policy_resolver",
    "evals.test_demo_topic_store_migration",
    "evals.test_demo_section_store_migration",
    "evals.test_demo_section_store_readback",
    "evals.test_demo_topic_runtime",
    "evals.test_demo_live_span_source",
    "evals.test_demo_financial_pack",
    "evals.test_demo_material_dispositions",
    "evals.test_demo_claim_binding_gate",
    "evals.test_demo_claim_entailment_gate",
    "evals.test_demo_accepted_binding",
    "evals.test_demo_claim_legacy_readback",
    "evals.test_demo_narrative_schema",
    "evals.test_demo_narrative_organizer",
    "evals.test_demo_narrative_legacy_readback",
    "evals.test_demo_section_result_legacy_readback",
    "evals.test_demo_section_chain_persistence",
    "evals.test_demo_followup_need",
    "evals.test_demo_followup_store",
    "evals.test_demo_contract_gap",
    "evals.test_demo_external_fact",
    "evals.test_demo_fact_narrative_disposition",
    "evals.test_demo_writer_proposals",
    "evals.test_demo_writer_batching",
    "evals.test_demo_writer_support_alias",
    "evals.test_demo_writer_prompt_assets",
    "evals.test_demo_backbone_writer_phase",
    "evals.test_demo_writer_formal_chain",
    "evals.test_demo_pack_writer",
    "evals.test_demo_pack_set",
    "evals.test_demo_tree_materials",
    "evals.test_demo_report_assembler",
    # M930-4 独立审查与确定性 Assurance（`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §7.3）：
    # 第三套 identity 的线格式。本模块只钉线格式本身——封闭 category 词表（`unsupported`/
    # `clarity` 被拒且给出可执行替代）、`supported` 是正向结果而非 PASS、Reviewer 的
    # pass/decision/正文改写字段在字段层即被拒、`HardGateIssue` 两个布尔正交、
    # `AssuranceResult` 没有 `formal_closure`/`run_id` 的位置、四状态轴独立且放行结论
    # 不随人工轴变化、乱序输入得同一 id。硬门/Reviewer/Controller 各另有模块。
    "evals.test_demo_review_schema",
    "evals.test_cross_document_dispatch",
    "evals.test_m930_3_navigation_mechanics",
    # M930-3 目标一**读回页**：逐 (栏目, 文档) 的七态判定按**章**分轴——「已定位但未读取」
    # 只由本栏所在那一章的未读带料位置触发；年报同批探针词在财务报告等**别章**命中的
    # 「24、收入」「（二）收入确认」逐条列出、不藏，但不顶掉本章的结论（不新增第 8 态）。
    # 另钉住：章节判不出时按最保守口径计数（实料没读不得从头条消失）、定位位置必须带
    # 「命中哪个申报字段词」（客户/供应商两栏的强定位全部由「关联方」命中关联方交易，
    # 真正的披露「主要销售客户和主要供应商情况」那一刻是弱定位且未读）、以及两份年报上
    # 「主要业务」**主体正文逐字在读集里**＋收入/成本/毛利四栏**仍如实记表格拒发**。
    "evals.test_m930_3_business_readback",
    # M930-3 双节演示入口：节序**只由所选 demo scope profile 派生**（`selected_section_ids`），
    # 且承重不变式是「绑定后的节序逐字等于冻结 Contract 投影出的 `report_plan.section_tasks`
    # 节序」。另钉住 acc-36 实测出的缺陷面：主题研究节的分派**只按节身份查表**，不写死
    # 「公司 + 行业」这一对——双节 profile（公司 + 财务）下 industry 是**缺席**，未注册的节
    # 一律 `AcceptanceRefusal`（既不静默跳过、也不拿别的 worker 顶替）。
    "evals.test_m930_3_dual_section_entry",
    "evals.test_m930_3_acceptance_gates",
    # M930-3 批次四 §1：**r5 真实失败响应的离线复演**（replay ≠ pass）。读 r5 的原始 narration
    # 日志与三份记录（`logs/llm` + `evaluation/results/m930_3_acceptance_crossdoc_real_r5/`），
    # 在当前代码上重放同一批字节：候选 24/1/2 逐字保留、财务读视图的投影面为空、两条真实诉求
    # 变成逐条 typed 拒绝，而旧的全有或全无入口抛出的消息与 r5 记录**逐字相同**。数据不在版本
    # 控制里，缺失时 **typed skip**（不计通过），不静默绿。
    "evals.test_m930_3_r5_financial_replay",
    # M930-3 定点批退出条件 ①②：用 r7b 真实留存的材料、manifest 与原始模型返回做零网络
    # 离线同链重放——公司节一段逐句可回查的经营模式正文（原束 95 → 裁出 31 → 幸存 64 →
    # 64 Claim → 16 句正文），财务节一段由 `ddf-1` Δpp 派生事实（Path A）承载的分析正文 +
    # 4 张与记录逐格相同的表。**不是**新的真实验收；`SECTION_BLOCKED` 与缺口如实保留。
    # 同一组数据不在版本控制里，缺失时 **typed skip**（不计通过），不静默绿。
    "evals.test_m930_3_prewrite_offline_replay",
    # M930-3 指令 E 第 4 项：**坏候选四条出口**在 r7b 两束留存原始返回上的离线重放（零网络、
    # 零新调用）。① `primary` 边序错误（真实批 3/4：整束 fail-closed 逐字点名 c9，逐候选裁出
    # 恰好拒 c9/c16、守恒 19+2=21 且幸存者逐字未改写）；② 非法 JSON（真实纠正返回：字符串内
    # 一处裸换行，严格解析逐字复现记录里的错误位置；容错**只**放行已证明的三类字符，NUL 与
    # 字符串外控制字符照旧拒，结构判定一条不放）；③ 高风险候选（逐批复算 0/40、18/44、21/21、
    # 8/35；判据排在成员解析之前）；④ 重复候选 + 聚合绑定的四条集合级结构码（空集/缺身份/
    # 重复/非 canonical 顺序，在 `authority=None` 时即抛出 ⇒ 读内容之前 fail-closed）。
    # 同时复核有界（每批 1 次、与截断缩小共用 2 次额度）与带痕（说明只带被交来的那一批坐标）。
    # 数据同样不在版本控制里，缺失时 **typed skip**（不计通过），不静默绿。
    "evals.test_m930_3_r7b_replay",
    # M930-3 四条**候选级**判定轴的定点专测。它们此前不在全量清单里：模块是绿的、也被手工跑过，
    # 但全量回归从不执行它们——「判据自己有一份专测」与「这份专测会被跑到」是两件事，
    # 只有登记进来才成立。四份都不依赖版本控制外的数据。
    #   * `derived_delta_fact`：`ddf-1` Δpp 派生事实（单期冒充双期 / 缺前期 / 错范围口径 / 已舍入输入）；
    #   * `candidate_carve_out`：`cco-3` 逐候选裁出（幸存者逐字照原样、被点名者逐条带证据退出）；
    #   * `selection_scope`：`selappl-1` 勾选行允许用途（所问事项之内 / 不得从尾段取原文）；
    #   * `risk_surface_semantics`：路径 B 高风险表面（误伤词与真实风险词的**反例组**）；
    #   * `source_role_scope`：`srsc-1` / `srsc-2` 期间位次与独立支撑结论（O-12）。
    "evals.test_m930_3_derived_delta_fact",
    "evals.test_m930_3_candidate_carve_out",
    "evals.test_m930_3_selection_scope",
    "evals.test_m930_3_risk_surface_semantics",
    "evals.test_m930_3_source_role_scope",
    # M930-3 指令 E 第 3 项（门后那一半）：`natural` 句类的**句级来源闭合**。门前草稿进组织器
    # 输入面的是「草稿文本 + 逐原子 kept/dropped 台账 + 只读的`材料正文`行（后者见下一条）」，
    # 句类由系统从「有没有声明 `prose_unit_id`」判定，`claim_ids` 必须是那一段 `kept` 原子的
    # 子集；未在本轮输入里的出处、借草稿之名声明别的 Claim、系统补救句挂出处、模型自报句类
    # 一律拒。保真判据本身归 `sentence_fidelity`，这里只证明「它真的在门内跑」与「逐字判据
    # 没有被删掉」。
    "evals.test_m930_3_natural_prose_closure",
    # M930-3 返修 P1-d：门后组织器的**只读材料正文依据面**（`norg-8` / `orgmc-1`）。此前组织器
    # 手里只有草稿文本、逐原子台账和材料 ID，**没有材料正文**——「照材料原文的用词与语序改写」
    # 在模型那一侧不可执行，只能退回去把 Claim 一条条拼一遍。本批给它点名过的成员的材料正文
    # （身份 + exact 定位 + 正文 + 形态），并**解码真发出去的那次请求**逐条钉住边界：只投点名过
    # 的成员（有界）；`member_ref` 相同而正文被换过的材料必须靠**读视图指纹**当场拒（只比
    # `member_ref` 或只比内容指纹都拦不住自洽重算的伪造）；纯 `FinancialFactPack` 的财务节点名
    # 数为 0 ⇒ 合法空表而不是伪造材料；**只读不是授权**可执行——照材料写进一个声明 Claim 里
    # 没有的期间表面必须被门拒，而删掉那个表面后同一份计划必须被接受。**§7**（r9 后返修 B）：
    # `npr-1` 允许同一条候选在两段草稿里各出现一次之后，组织器仍要收到**两行**台账（共享的
    # `claim_id` 在两行的 `kept` 原子里）与**两行**材料正文——组织器本身**不重建**，这一节全是
    # 输入面读数。
    "evals.test_m930_3_organizer_material_context",
    "evals.test_m930_3_real_assembly_smoke",
    # M930-3 r9 后返修 **B**：门前草稿「一条原子、多处表达」的归属政策（`npr-1`）在**真实写入链**
    # 上的正反例。r9 保存字节死在 `SectionDraft` 构造，现场就是「同一段话在两份年报里都出现」：
    # 候选按原子合并成一条，草稿段却按出处各自成段。修法是版本化地允许一个候选在多段草稿里各出现
    # 一次，代价是每一次出现都要与**该候选自己的**支撑提案逐项核对材料身份。这里走 `write_section`
    # （不是判据的逐格用例——那些在 `test_m930_3_sentence_fidelity` §6b）：正向证明两处表达真的
    # 成稿、两段正文与出处原样保留、两处都能被 `natural_prose_occurrences_for_candidate` 读回；
    # 反例三条（错来源 / 漏候选 / 段内重复）逐条断言类型化原因，并钉住「被拒不是静默 fail-closed」
    # ——门前草稿与补件诉求留在 `pgr-1` 留存面（标「未核验、不可发布」）里，不产生 `SectionResult`。
    "evals.test_m930_3_prose_occurrence",
    # M930-3 r8 后的业务纵链收口：用 r8 **自己留存的字节**（6 份 `pack_section_writer_proposals_
    # v9@proposals-13` 原始返回）做零网络离线同链重放——A 段原样重放必须在公司节第 4/4 批被判死
    # （反例 A：走通才是异常信号），B 段换成「前三批最终返回 + 离线构造的合法空第四批」后必须真的
    # 产出 Claim/段落/句子与一个 `SectionResult`（判「公司正文有/无、为何」）。同时钉住组织器输入
    # 面（材料驱动草稿 + 只读原文语境 + 两种句类）与 `natfid-1` 的三条已实测盲区、财务与行业分报。
    # **最重要的一条**：它同时登记了那批新增判据（`add_batch_no_witness_means_empty` / `bcnw-1`）
    # 的反例面——只登记判据、不登记专测，等于回归从不执行它。
    # 数据同样不在版本控制里，缺失时 **typed skip**（不计通过），不静默绿。
    "evals.test_m930_3_r8_offline_replay",
    # M930-3 r9 后的返修 A 段：用 r9 **自己留存的字节**（4 份 `pack_section_writer_proposals_
    # v11@proposals-15` 原始返回 + 1 份财务最终句返回）做零网络离线同链定点复现。r9 与 r8 不是
    # 同一条故障：它越过了批内形状校验与合并（40 候选 / 19 草稿段 / 10 单元 / 20 补件成形），死在
    # `SectionDraft` 构造——**同一段话在两份年报里都出现**，候选按原子命题合并成一条，草稿段却按
    # 出处各自成段，于是同一候选被两段声明而被唯一性判据拒。本模块读出的不是错误串的转述，而是
    # 「哪两组候选、declared by 哪四段草稿、每段各自独有的原子与各自的文档/角色出处」的现场对象，
    # 并把财务最终句那一次返回钉住（机械定位出三条表面、模型把期间**合成**成一条原子，因此两条年份
    # 表面无人认领、本门未能形成决定——与产物里该决定读回行数为 0 是同一件事的两面）。
    # **最重要的一条**：B 段修好后，这里的第一格由 `_check_reproduction` 从「必须失败」翻成
    # 「必须走通且共享候选的两条出处、四段草稿的独有原子一条不少」——判据不是被删掉，是换向。
    # 数据同样不在版本控制里，缺失时 **typed skip**（不计通过），不静默绿。
    "evals.test_m930_3_r9_saved_bytes_replay",
    "evals.test_material_column_audit",
    "evals.test_r2_source_object_closure",
    "evals.test_external_adapters",
    "evals.test_llm_call_attribution",
    "evals.test_section_publishable_report",
    # 指令 D §二「三条日期轴」的 (b) 来源归属轴（`srattr-1`）：归属语只由**系统**从已登记身份
    # 渲染（材料名 + 登记 id@版本 + 精确页码 + 可核实披露日），写者一个字也不能写。它钉住三件
    # 容易互相顶替的事：披露日只在 `verified` 时才有值且必须是日粒度；不可核实就渲染成「披露日
    # 未知」，绝不用入库时间／PDF 元数据／上传时间／财务期末冒充；月粒度线索不得升格。兼容面同样
    # 逐字节钉：不传归属语时 `render_citation` 与旧版渲染完全相同（旧调用方零影响）。
    "evals.test_source_attribution",
    # 上一条测归属语本身；这一条测它在 **M930-3 验收运行**里的读者面：逐材料归属语表 +
    # 「逐句 → Claim → 采信边 → 材料 → 归属语」的对账，以及三种「没渲染出来」的 typed 原因
    # （登记表没有 / 登记数据不合规 / 没有精确页码）分得开。它同时钉住**不改正文**这条不变量
    # （runner 不重建正文来源索引、不调正文渲染器、句文本逐字未改）。
    "evals.test_m930_3_source_attribution",
    # 指令 D §三：整束被拒时**门前草稿的只读留存**（`pgr-1`）与失败侧诊断（`pre_gate_draft.*`）。
    # 真实 run r8 的 company 节里，模型分四批被问、前三批各写出了内容，第四批的返回结构不合法，
    # 而整轮是全有或全无的——前三批的正文随异常蒸发，产物里连「这一节写出过什么」都读不出来
    # （LLM 账本只有元数据，事后**不可恢复**）。这一条钉住修法的三面：逐批留存（含坐标、正文与
    # 出处轴）、`none` 与「有留存」不得互相冒充、以及诊断**不喂正文**（只有一处调用点、不进
    # `SectionResult` / 预览 / 报告，也不放宽任何门）。
    "evals.test_m930_3_pre_gate_draft",
    # 受阻章节的**不可发布预览**（`blocked-section-preview/2`）。与上一条是两件事：`pre_gate_draft`
    # 只覆盖「本节根本没成形」，本件覆盖真实 run r9 暴露的第三条现场——本节**已经**有
    # `SectionDraft` / 门侧决定 / `SectionResult` / 逐条引用，而整本报告因**别节**没产出被拒，
    # 于是 `report_preview.md` 一个字都没写。「别节失败、本节内容也丢了」由此有了只读出口。
    # 这一条钉住：三栏（已过门内容 / 被拒门前草稿 / 替身组织器产出）不得混成一栏、正式组装器对
    # `BLOCKED` 的拒绝一字不动、没有内容不得造空壳正文、库不可读与链读不回来**都**不得退化成
    # 「没有内容」、重放与历史档不得标成当前真实成稿，以及没有正式 `report_version` 时不得
    # 声称 M930-4 已做独立审查。
    # `/2` 只**加**两个面：引用的人读行（中文来源 vs 生产渲染器有意不印的复核面记号 vs
    # 解析不出来也不丢）与逐节**补件去向**（待裁决诉求 / 不成立的原始诉求 / 已写出内容里自己
    # 不成立的申请，三类分列，且**不是** gap、**未执行**）。三栏名与四档出口状态一字未动。
    "evals.test_m930_3_blocked_section_preview",
    # 离线替身**读请求面**的回归：写入侧把返修/栏目定向/批内形状纠正说明逐字追加在完整原请求
    # 之后（`PW._append_note`，唯一追加点 `PW._messages_for`），替身用裸 `json.loads` 读整段
    # 就会在「本批被形状纠正后重问」的那一次抛 `Extra data`，于是**整节没有产出**（现场：
    # company / financial 两节各一批，离线验收 A1/A2 因此红）。这一条钉住三面：领头的完整值 +
    # 换行分隔的说明必须读得出来、坏请求（含「两个 JSON 值首尾相接」那种同样报 `Extra data` 的
    # 形态）一律不许被圆过去、以及请求面不得借用模型返回侧的裸控制字符容错。
    "evals.test_m930_3_offline_request_face",
    # 离线替身的**组织句**：接缝里的中性并列连接语不得与被接的那句话自己的开头重复
    # （材料原文常自己写着 `此外，`，替身的接缝再补一个就读成 `此外，此外，`）。这一条是
    # 人读面（离线预览）的读得通纪律，不是门：避不开时照常写出正文，任何判据都不放宽。
    "evals.test_m930_3_offline_organizer_sentence",
    # 逐条 typed「栏目未达」原因从运行现场 → 只读诊断 → 人读页的搬运链：五条原因彼此不可
    # 互推，**不得**合并成一句「覆盖门未通过」，更不得写成「语料里没有」；本节取不到运行结果
    # 时是「不可判定」而不是空的 entries；闭集只从 runtime 取，诊断侧不复制一份。
    "evals.test_m930_3_column_unmet_readback",
    # 材料包读回里**表对象信道**的形态读法：表对象信封按设计不带 §二 2.3 的
    # `content_qualification`，过去一律落 `None` ⇒ 已准入、已保留、已进 Writer 清单的表对象
    # 被印成「内容形态：读不出」并落进 `unavailable`。这一条钉住：表对象另成一族、读同信封的
    # `reading_policy`（允许用途 + 逐条排除项），两条正交声明（能读 / 数字权威）各自成键，
    # 而真正的「读不出形态」仍是 `None`、仍印「读不出」——两种结论不得再合并。
    "evals.test_m930_3_table_object_readback",
    # §0.20 第一步：新的 Pack → 逐句引用自然正文接口（`cwm-2`/`cw-1`/`cwp-1`）。钉的是接口
    # 边界而不是措辞：输入面逐条等于精确材料清单、引用键命名空间由本清单独占、财务事实不伪装
    # 成 Pack 材料、小节一一对应、采用去向按句引用记、一处无引用不清零整节、身份往返可重算。
    "evals.test_m930_3_cited_writer",
    # §0.20 主营业务写作组织面 `co-3`（`cp-22` 立）/`co-4`（`cp-23` 纠两条过度推断）/
    # `co-5`（`cp-24` 再登记 `production_mode`/`revenue_breakdown` 两栏）/
    # `co-6`（`cp-25` 把 `revenue_breakdown` 的**金额**与**占比**的授权面分列；`ndc-4`）与
    # 「已送达未写出」的**读时派生**读数（`munr-1`）。
    # 钉的是本批非冻结改动：`sales_mode` 栏的**内容目标**（逐点列该栏要答什么，写不出的点按
    # 第 7 条记缺口）与**引用机会**（当期/历史来源分开读）只由冻结 Contract 的**栏位身份后缀**派生、
    # 只组织写作不加门，且**不动**分步/`CitedSubsectionSpec` 身份体/`MaterialAdoptionRecord` wire；
    # 以及回读里 `delivered_not_used` 三列**现算**（逐字重复对象 + 按句计独有句 + 说明）而不落盘
    # ——判重只认逐字（归一空白），同义换字不判重，已引用/不适用的行一律补空值。
    "evals.test_m930_3_sales_mode_objective",
    # §0.20 数字**逐值**链条里「材料原句 → 候选」那一段（`M930_3_QREWORK_PART1_NUMERIC_CHANNEL_PLAN`）。
    # 钉两件独立的形状：`INSPECT_EVIDENCE` 的动作层与工具契约层**统一到单数 `evidence_id`**
    # （工具层本就是等号形状 `additionalProperties: False`、`max_results=1`；复数此前在动作层被放行、
    # 在工具层恒 `FATAL_TOOL_ERROR`）——统一方向不改变调用记账，一次动作仍是**一次** tool call；
    # 以及本批**不动**判据本体（期间抽取版本串、Writer 侧排除原因码封闭词表、工具契约层逐字未变）。
    "evals.test_m930_3_numeric_channel",
    # 缺口**理由**与补件**来源类**这两条业务行为（`cw-3`）。缘起是真实 r2 的现场：7 条缺口**全部**
    # 自述「清单里没有来源」，可其中 5 栏（各业务成本毛利、成本结构、三个客户栏目）的材料登记里
    # 明明有该栏材料，只有 2 个供应商栏目确为零材料；4 条补件又把来源类写成 `financial_pack`——那
    # 是**权威类型**（由谁背书），不是**来源类**（去哪里检索），两根轴。钉的是行为不是措辞：
    # 改判**只**发生在「自述『清单里没有来源』而该栏登记数 > 0」这一种情形且单向，其余自述一律
    # 原样保留并如实标 `writer_declared`，规格取不到标 `uncheckable_subsection_not_in_manifest`；
    # 补件的期望来源类由该栏 Contract 允许来源**推导**（并集保序、空取值只在「量不出来」那一档、
    # 构造期 fail-closed），生成器自述的那一个原样留在审计字段、**不进**身份体也不被下游当指令读。
    # §2/§3 把 r2 的**真实回复字节**重走一遍真实解析口逐条读回（容器侧身份是夹具值，请求面不落
    # 它们；材料登记与正文逐字取自真实请求面）；只读 `logs/llm` 与冻结 Contract，不调 LLM、
    # 不联网、不写库，日志缺失时 typed skip。
    "evals.test_m930_3_gap_reason_and_source_class",
    # §0.20 第二步（前半）：逐句机械底线核对（`sc-1`/`scp-1`）。钉判据本身：四类高风险表面各自
    # 有合法来源、两套授权池（报告框架能授权时点/主体，不授权数字/否定）、旧材料不得当前化、
    # 勾选/模板文字不得冒充事实、表数字必须逐格可核（缺格即 typed 拒收，不是恒假门）、
    # 一处错误不清零整节、机械核对不得宣称语义已被支持。
    "evals.test_m930_3_sentence_check",
    # §0.20 数字**逐值**链条台账（`cvt-1`）。`sc-*` 的判定是**逐句**的，说不出「同一句里
    # 四个数字各自卡在哪一步」；本模块把链条摊平到**值**上：写了不等于授权到了（只引普通材料
    # 原文 ⇒ `material_surface_only`）、材料里有这个数字**不等于**有一条被拒的候选
    # （`no_candidate` / `candidate_rejected` / `absent_from_material_spans` 三档两两不同）、
    # 已送达未写与已合格未送达各是一档（无 typed 排除记录时明说「无记录」而不是「没被排除」）。
    # 上游对象不在产物里时整份标 `not_observable`——**不**把「读不到」写成「没有」。
    "evals.test_m930_3_value_trace",
    # §0.20 分业务营收纵链的**确定性候选入口**（`nd-2`）与句子—事实**语义配对**（`scp-12` → `scp-13`）。
    # 此前研究侧唯一的候选构造器的 `statement` 恒取模型命题文本，材料已把分业务营收原文送进
    # Pack 而候选数为 0。本模块钉住：并列三年数字按**位置配对**拆成单值命题（整句期间抽取对
    # 同一句只答一个期间，是错的**值级**期间），对不上/无开标记/单位不符/无分母/增速动词一律
    # typed 跳过且不铸候选；冲突值**两条**都铸候选都判拒；数值披露与模型 claim 共用**同一个**
    # 资格门（三个 digest 可被 Pack 门复算，重构后模型那条路身份逐字未变）；`nd-2` 的逐值身份
    # 带目标值自身与四轴身份（期间/业务作用域/指标/单位）且与候选期间逐字对齐，值级期间对不上
    # 就**整条**不铸候选，同材料去重/冲突键含业务作用域（异业务同年不误判冲突、作用域读不出则
    # 保守跳过）；`scp-12` 下同数字不同年 / 不同业务 / 金额当占比必须判错，任一侧给不出完整绑定
    # 则回落旧判据；`scp-13` 再收两格——**声明了**身份的事实不再享有那条回落（句子侧读不出
    # 就是「核不了」，不得读成放行），且**已声明的占比事实**在分母无轴可核之前一律不授权
    # （「同为百分比」不能代替分母核验）；`ndc-4` 再钉请求面与逐句核对读**同一处**判定——
    # `fact_numeric_writability` 一个函数两侧共用，请求面据此把「Pack 侧登记（`citable_fact_keys`）」
    # 与「本版可写（`writable_fact_keys` / `numeric_authorization`）」分成两根轴，登记语义一字未动；
    # 这样一条事实必须真的出现在 `scan_topic_pack` 与写作输入清单的 `f*` 行里。
    "evals.test_m930_3_numeric_disclosure",
    # §0.20 合格事实 → **唯一材料** 的消歧（`mbind-1`，`ndc-2` 批）。此前采纳侧按「本 aspect 的
    # 材料集」解析、写作侧 `scan_topic_pack` 按「整 Pack 的 material_index」解析：同一份 Pack 在
    # 采纳时唯一、到写作时变成同来源身份同页多候选 ⇒ 整节在扫描里 fail-closed，一个字写不出。
    # 本模块钉住补上的「唯一收窄」：同一当前 Pack 内逐环重算 `SupportedFact → FactCandidate →
    # eligible 决定` 取该事实**已核验的输入材料**，**单元素**时才与引用的类型化来源身份候选求交；
    # 交集为空、多候选、错页、跨 Pack、身份不一致、决定与候选不一致、非 eligible、digest 重算不符
    # 一律不绑定（该拒的仍拒，不按顺序任选，`for_support_edge` 不是开关）；`scan_topic_pack` 与
    # 独立支撑门 `_authority_material_expectations` 必须给出**同一个**绑定。方案 A **不改**
    # `CitationRef` wire。
    "evals.test_m930_3_material_disambiguation",
    # §0.20 写作链里**没有 Pack 材料边界**的那一支（财务 artifact + 已验附注事实）。钉的是
    # 分支边界而不是正文质量：材料边界指纹分两支且 Pack 支逐字节不动、空材料上下文只对非 Pack
    # 权威开放、非 Pack 权威夹带 Pack 材料一律拒、`sc-1` 轴 2 按各权威自己的坐标判且默认那一支
    # 未被放宽、离线事实替身不越权（没事实就如实报缺）、缺省 topic 只能从单 topic 任务派生。
    "evals.test_m930_3_cited_financial_branch",
    # §0.19/§0.20 财务节的**确定性**「指标 × 期间」表（`cmt-4`/`cmtr-4`）：候选集是**进入本节
    # 事实清单的合格财务事实**，**不再由写作结果决定**（草稿已不是该构造器的输入）。钉判据本身：
    # 合法缺席各有 typed 记录且与表格互斥、七类数据伤一律 fail-closed、单元格逐字取权威字段且不
    # 含任何算术、逐格核对的基准是该权威事实自己的字段、双期派生事实不进期间轴、表身份含
    # `rule_version`、缺值的格留空而不是被回填、必需指标 × 期间覆盖三档（`displayed` /
    # `available_not_displayed` / `missing`）逐条可查且不给「覆盖通过」结论。
    "evals.test_m930_3_cited_financial_table",
    # §0.20/§0.21 财务**呈现层**三件事：指标→栏目路由声明（`fpr-1`，含「栏目缺口由冻结 Contract
    # 减法得出」「未路由事实逐条可查」「来源标记不得换成权威登记」与「路由输入必须带指标 code，
    # 拿扫描读视图充数会让 `fact_columns` 恒为空」）、确定性来源/口径呈现（`css-1`：十条要求逐条
    # 出现、零模型调用进身份体、缺口不带呈现句、不复制偿债表）与真实模式的门（`cited-budget-2`：
    # 离线替身一次都不记账；真实模式拒绝时请求没发出去且拒绝记在写作类别；`--model` 换模型在
    # 第一请求之前被拒；不在受管节集合里的节也在第一请求之前被拒）。模块本身不发任何模型请求。
    "evals.test_m930_3_financial_presentation",
    # §0.21 财务节的**第二个**确定性呈现栏（`cbs-2`／`cbsp-2`／`bsr-1`）：资产负债三栏
    # （结构／重大科目变化／15% 强筛）逐条读本节权威的**合格事实**。钉的是会读错数字的那几件
    # 事：资产负债率**取**权威自己的 `SOLV_DEBT_RATIO` 而**不**自行相除（指标命名空间按**本节
    # range**收，落到本节内另一栏也要读到、落到本节之外才落缺口）、合计行不充当被筛科目、
    # 快照缺的独立科目落 `no_registered_fact_for_item` 而不拿合并科目顶替、15%（Contract 判据）
    # 与 20%（本批展示筛）分别计算分别记录、两条筛**逐期**陈述而不是只讲本期末、每条变动读数
    # 带算式（「算式 / 事实」列不得整列空白）、季度末对上年末**只能**写「较上年末」而不是同比、
    # 未登记的读法与错产出者身份在构造期拒绝。模块本身不发任何模型请求、不写库。
    "evals.test_m930_3_balance_structure_presentation",
    # 财务离线正文**错栏**定点修：权威事实按 `presentation_routing.fact_columns` 落栏，未路由
    # 的事实不得被轮转塞进任何一栏。缘起是 r27 现场：25 条财务事实 `aspect_ids` 全为空，替身
    # 于是退回按清单顺序轮转，18 句里 17 句被 `presentation_column_attribution`（`scp-4`）判为
    # 错栏。模块钉住的是**选材规则**与它的边界：按路由选事实（正例）、不把别栏事实挪来充数
    # （反例）、没有路由的事实留 typed 去向（`FACT_PLACEMENT_DISPOSITIONS` 闭集，四种「没写出去」
    # 的情形分开记）、两条轴都没有时的轮转回退**仍在**、零路由栏如实报缺口并把路由自己登记的
    # typed 理由带出来。**错栏硬核对一个字没放宽**——模块专门用反例证明这条轴仍是 `applicable=True`
    # 的判过的轴，把事实挪到别的栏照样 `hard_error`。夹具是 r27 那次离线运行自己落下的产物，
    # 目录不在时 typed skip；只读产物，不调 LLM、不联网、不写库。
    "evals.test_m930_3_routing_fact_placement",
    # §0.20 第二步（后半）：独立只读审阅（`rvi-2`/`rib-2`/`crv-2`）与新的报告版本身份
    # （`crpv-2`/`crpp-2`）。钉判据本身：旧 `rvi-1`/`rib-1` 逐字段兼容且 `create()` 默认仍停
    # 旧版、句级扩展只在 `rvi-2` 上、粗类与九类句义不得互相矛盾、审阅不放行不改稿（信封与
    # 意见两层各有一份**指名**禁列）、逐句覆盖等式、意见必须挂在该句自己的引用上、审阅不得
    # 覆盖机械硬错误、请求面只含被引来源且缺锚即 fail-closed、版本锚只由写作侧输入派生
    # （故可被意见引用而不成环）、一处错误只标记那一句而其余照旧可读、预览恒定「不可发布」。
    # `crv-2`/`crpv-2` 追加两根轴：意见的**生产者**进身份并决定 ③ 轴的天花板（离线回声无论有无
    # blocking 都停在 `system_review_not_run`，读者面永不出现 `passed_awaiting_human`）；确定性
    # 指标表进 `record_id` 身份体（表一变记录就变，版本锚不变），预览里的表格与它声称的那一版
    # **当场**对账（表不属本节、格引用键越出本节清单即拒）。
    "evals.test_m930_3_cited_review",
    # 真实 cited 链的**失败路径**（2026-10-01 r1：公司节首次写作调用 `max_tokens`、
    # 8192 输出、可见正文 0 字）。钉的是失败路径本身而不是根因：本链两个客户端**显式**关推理
    # 而 `llm.client` 的共享缺省不动（判据是最终发往 `messages.create` 的 kwargs）、
    # `text` 取**全部**可见块按序相接（thinking / 未知块不进正文、缺 `content` 不炸）、
    # 截断日志带响应**形态**（块类型/块数/各块长度/可见总长/stop_reason/usage，**只有长度
    # 没有内容**）且未截断调用不带它、截断既抛也记（客户端失败流水含 call_id、无 text/
    # response_hash）、脚本在节循环抛错时仍**原子**写出 `cited_call_ledger.json`（标 failed、
    # 带失败原因与 call_id、含已占次数）且不产出任何章节成功产物。模块不发真实请求，
    # 日志目录被指向临时目录。
    "evals.test_m930_3_cited_failure_path",
    # 审阅失败在**运行入口**上的复验（本批两处窄项修复：截断没在审阅边界被接住、
    # `response_not_json` 没被映射到）。两条都是**边界接线**缺陷——单测函数组合各测各的都全绿，
    # 因此这里让**同一条 `run()`** 先真的取得 Writer 草稿、再触发审阅失败，然后读**落盘产物**与
    # **退出码**：草稿/逐句机械核对/缺口/`cited_preview.md`/失败账本仍在，预览标明审阅未完成
    # 且不可发布，系统不放行，`run()` 返回非零；正例（审阅正常完成）证明正常路径没被改坏；
    # 「这次调用已占额」由真实客户端＋真实 `cited-budget-2` 门＋真实 `chat_with_usage` 的分层
    # 复现给出（离线模式没有真实账本，这一条不能拿离线运行冒充）。穿进审阅边界的失败对象取自
    # 上述真实复现，类型清单不在测试里重抄。**注意成本**：它读真实只读库与真实 PDF，本机实测
    # 约 17 分钟（装配 525s ＋ 表矩阵 391s ＋ live 快照 101s，三个 case 共用一次装配），
    # 不加这 17 分钟就验不到运行入口。不发真实请求、socket 层断网、日志目录指向临时目录。
    "evals.test_m930_3_cited_review_failure_entry",
    # 真实 r2 返回的**纯结构归一**（`crn-1`）。钉的是边界不是措辞：小节内那份缺口**只有**与
    # 顶层同小节的缺口逐字段完全相同（按多重集，含条数）才被删，`detail` 差一字、少抄、多抄、
    # 「有键但是空表而顶层有」四种不一致全部 fail-closed；小节里**没有** `gaps` 键是正常形状，
    # 不得被读成缺陷；`sentence_id` 由解析边界按「小节→段落→句子」稳定顺序取全节唯一值（模型
    # 原 ID / 结构位置 / 新 ID 三者在映射里同时在场），空 ID 仍然拒收（归一只重命名、不补编号）；
    # 解析边界再断一次唯一，使绕过归一化的路径在同一条线上失败；归一不动正文、输入对象不被改动、
    # 且**幂等**。合成 wire 夹具，不调 LLM、不联网、不写库。
    "evals.test_m930_3_cited_reply_normalize",
    # §0.20 第三步的**有界局部返修**（`cwr-2`/`cwrp-3`，prompt `cited_prose_rework_v1@crw-2`）。
    # 钉的是接口边界，不是措辞、也不是产出质量：只收点名句（其余句正文与引用**多重集**原样，
    # 改一个标点即整次作废且**不留新稿**）；换引用必须逐条声明且声明必须与该句在稿里的真实引用
    # 逐字相符（未声明 / 不符 / 指向不存在的句子三条各自给反例，清单外的键由写作侧同一套
    # `validate_citations` 更早关掉）；`rounds` 只有 1 这一个合法值，没有循环与重试入口；
    # 没有点名句时**一个调用都不发**；草稿/清单、报告/草稿两条事前等式对不上时在装配请求面
    # 就拒。真实基线（只读 `m930_3_cited_real_company_cp21_r1`）上离线替身重放证明：65 份材料
    # 一份未丢、合格句逐字未被擅改、4 条 `numeric_qualification` 硬错是**撤下**而不是放行、
    # 单句返修不会清零整节（28 句→21 句）。`cwr-2` 另钉**去向台账**：点名句的 id 来自初稿、
    # 新稿的 id 已被 `crn-1` 重编，对这两个编号空间求交集恒错；台账改按归一化编号台账逐句
    # 对应，并把「拆句重号 / 同文换号 / 合句吞并」三种不可唯一对应的情形记为 `ambiguous`。
    # `cwrp-3` 另钉**数字授权投影**：返修请求面开始带 `writable_fact_keys` 与顶层
    # `numeric_authorization`，两者都是初稿请求面那份派生读视图的逐字投影（12 条事实 → 9 可写 /
    # 3 撤回）；合成夹具与真实离线面（只读 `m930_3_cited_offline_ndc4_company_r1`）各自复核两个
    # 请求面逐栏逐字相同，并验证「金额保留、占比撤下」与「占比原样留着 ⇒ 读数仍报那条硬错」。
    # **测试全绿只证明机制接得上，不宣称真实 Writer 或 Reviewer 已改善**；返修在
    # `cited-budget-2` 下已是**自己的类别**（公司节 ≤1），真实调用仍逐次授权。
    "evals.test_m930_3_cited_rework",
    # 写作调用的**失败前留存**（`ccj-1`）：输入、可见回复、解析状态按发生顺序各自落盘，前一步
    # 不被后一步顶掉，解析失败时回复仍在盘上；不留隐藏推理（文件级与回复级两处恒 false，且序列
    # 化体里没有隐藏推理字段）；恒不可发布；**留存写失败从不抛**（只记 `write_errors`，不把「这一
    # 轮为什么失败」换成「写文件失败了」）；请求面留**原文**而不是摘要、超限如实记截断且哈希按原
    # 文算；形状自查只查账目自洽。夹具是临时目录，不发真实请求。
    "evals.test_m930_3_cited_call_journal",
    # 留存回复的**只读业务诊断**（`crrb-2`）：能在现有证据下判的轴就判（复用 `sc-5` 的原语，
    # 不另立词表），每根轴都配正反例。数字分四种情形——来源里逐字有（不报）／只差来源文本自身的
    # 排版空格（待审，且 `detail` 明写「不是模型编数字」）／来源里根本没有（硬错）／金额比率的
    # 资格**逐 token** 看有没有落在本句所引权威事实里（整句引了事实不等于这笔金额被授权）。跨小节
    # 重复分逐字重复、「整句包含」（无阈值，方向分写）与「换说法达阈值」，阈值以下不报。缺口里对
    # **来源文档**的断言单独标出（「相关披露范围」不标、否定式披露才标），原文一字不改。人工判断
    # 与机械判据分栏，人工条目的字段与归类封闭（写错就拒）。判不了的 12 根轴逐条写明依据，不得静默
    # 略过。本模块**不与清单对账**，因此它只能叫离线派生诊断，不能倒写成那一轮的正式通过。
    "evals.test_m930_3_cited_reply_readback",
    # 运行目录的**离线四段对账**（`ccrb-1`）：清单 → 句子引用 → 核对 → 审阅输入，逐环穿过现有
    # 读回口（`from_dict` 重算身份），逐段要求「绑的是不是同一件事」（清单/草稿 id 逐个对齐、
    # 核对覆盖的句子集合与草稿相等、审阅的句子集合相等、并用现有构建口**重建**审阅请求来核
    # 「审阅会看到什么」而不是信文件里的一句话），再给逐句四段对照表。留存簿在场时把留存的可见
    # 回复**重放**一遍真实解析口，要求得到同一个 draft_id。**缺件就是缺件**：任何一环没落盘一律
    # 记「本轮不可判」并列出缺哪个文件、不拿别轮产物顶替、不按请求面拼清单（真实 r2 正是这种
    # 形状）；目录不存在**不**与「跑过但没落字段」混为一谈。三种篡改（换草稿/换清单/换审阅句子
    # 集合）各配反例。夹具复用写作模块的真 Pack 与真解析口，审阅走回声替身（产出者身份如实标
    # `offline_diagnostic_echo`），不调 LLM、不联网、不写库。
    "evals.test_m930_3_cited_chain_readback",
    # 真实 r28 失败轮的**留存字节离线回放** + 本批四类修复的判据（与上一条是两件事：上一条
    # 对账的是**结构**，这一条钉的是**三个失败门与它们的边界**）。r28 的现场：29 份材料已送达
    # Writer、23 句返回、独立 Reviewer 也回了 23 行，整轮却死在 `ReviewIssue.reason 不得为空
    # 字符串`，于是人读出口整节消失。这里先从**三份互不相干的留存**（写作留存簿、审阅调用日志、
    # 失败账本）交叉印证同一轮事实，再逐层重放那三个挡门（① 22/23 行空 `reason`；② 7 行空
    # `citation_id`——无引用句给不出 `rvi-2` 要的审阅单元，因而**结构上不可审**；③ 22 行判
    # `supported`），然后钉住本批修法的**正反两面**：
    #   * `supported` 也必须有非空理由，而正向意见**记一条分歧**、不使解析失败、也**绝不**覆盖
    #     机械硬错误（③ 轴不得越过 ②：本节有硬错误时 `passed_awaiting_human` 当场拒）；
    #   * 无引用句被**具名排除并声明**在请求面（`uncited_sentence_ids` 恒在场），而不是放宽
    #     `rvi-2`；把无声明的范围报上来仍 fail-closed 并逐句点名；
    #   * 纯缺口形态：材料登记在册却无合格事实时，该栏可以**没有段落**，但缺口写进 `gaps`
    #     （改判留 `claimed_reason` 原话），**不得**写成零引用的「本次未取得……」正文句——旧的
    #     零引用写法今天照旧是硬错误；
    #   * 审阅失败仍留得下**可读草稿**：23 句逐句在预览里、逐行标「未完成」且 `ok` 为假、
    #     明写「独立审阅未完成／降级诊断预览」、恒不可发布、永不出现「系统审阅已通过」；
    #     而财务呈现层是另一条轴——审阅失败**不**顺带丢掉权威表。
    # 它的**具名限制**：r28 的输入清单没有留存，因此模块**不**重算那一轮的机械硬错误条数
    # （重算就等于编造输入），只证留存字节支持的结论；另，财务节错栏的修法在本模块里是离线
    # 验证，真实财务节尚未跑到。数据不在版本控制里，缺失时 **typed skip**（不计通过），不静默绿。
    "evals.test_m930_3_review_robustness",
    # §0.21 路径 (b)：**原 PDF 表区的只读展示**（`std-1`/`stdp-1`）。这条路径与写作链
    # **身份不可互换**（并列的正文版本号不进展示集身份；模块不 import 写作链）。钉的是边界而
    # 不是措辞：实测文件哈希与登记一致才可展示、不一致**不渲染**；区域越界/退化/页号越界、
    # 表题锚点不在该页、关键行不在该区域、续表前驱缺席或落在更后面的页，一律 typed 缺陷且
    # 区域记 `defect` ——「确有表格却未呈现」是**系统展示能力缺陷**，绝不被读成「材料里没有」；
    # 渲染产出真 PNG（字节与渲染哈希逐字相等）；人工确认只有人能签（初值恒 pending、缺陷区域
    # 不接受签署、不就地改写）；候选表区登记「采用须有区域、不采用须有理由」且每个展示区域必须
    # 被一条采用登记认领。全场本地，不联网、不读库、不碰历史 run。
    "evals.test_m930_3_source_table_display",
    # 写作**小节轴**那一条投影（`cwm-6`，`R1` 结构缺陷的源头）：这条链以前按 **Contract
    # aspect** 造写作小节，而冻结 WritingSpec 里 `company_business*` 的 18 个栏目**全部**归属
    # 同一个 `co-h4`、`fin_solvency.*` 全部归属 `fin-h2`——「一个小节一条」因此被放大成十八个
    # 各写一句的短栏，同一份材料被反复拿去填不同小节。那是**小节轴造得比冻结规范更细**，不是
    # 取材不足；当成材料不够去扩大检索只会更歪。本模块只用**真实冻结投影 + 真实 WritingSpec
    # 文件**回答四件事：轴来自那一份（id/标题/次序等于该节 TOC，标题断言写成「等于 TOC 里那
    # 一条」而不抄字面）、要求文本逐行不删不改、`declared_aspect_ids` 与 Contract 栏目有序集
    # 逐条相等且每个栏目**恰好出现一次**、`allowed_source_classes` 是该组来源类的并集且**非
    # 空**（空集在这里是红旗：冻结 Contract 原始的 `evidence_requirement_ids` 是 `list[str]`，
    # 对字符串取 `.source_classes` 会得到空元组而**不报错**，那条断言就是这道静默降级的守卫）。
    # 另用**反序输入**证明小节轴是「这份资产 × 这组栏目」的纯函数（节间取前缀表次序、节内取
    # 该节 TOC），并逐条钉住三处 fail-closed（指纹：声明值/重算值各一例；栏目无归属小节；TOC
    # 缺条目）——替身本身另配一个合法输入，确保那几条不是「不管喂什么都停」。不调 LLM、不联网、
    # 不写库；无公司代号、页码、表号或答案关键词；不钉提示词措辞。
    "evals.test_m930_3_cited_spec_axis",
    # 本批读回新增的**两份材料去向账**（`wmdl-1` / `sarm-1`）。之所以必须钉住：两张账都是
    # `getattr(obj, 名字, 默认值)` 从产物上读数——**字段名写错不会报错，只会印出一片「—」**，
    # 而那片「—」在人读时正好会被读成「这条内容没有」。模块钉四件事：材料粒度四态互斥穷尽
    # 且各有判据次序（未准入 / 未送达 / Writer 判未采用 / 采用）、缺陷态（清单里有而 Pack 里
    # 没有、进了清单却没有 Writer 处置）**不是**一种去向而应报缺陷、「引用它的句数」与「其中
    # 带硬错」分两列且一句引多条材料时硬错记到每条上、无 Pack 材料边界的一支返回 `None`
    # （**不适用**，不是全零）。两个粒度的分离用**取值域不相交**钉住：材料去向态里不得出现
    # 任何投影终态的名字——否则「这一栏这次没查到」会被印成「这份来源里没有该内容」，那正是
    # 本批要堵掉的那句话。§6 逐类断言账读的每个字段名都在真实类型上（这条当场抓到一个真
    # 缺陷：出处身份被从 `ResearchMaterial` 上取，而它没有这个字段），并配一个反例证明该断言
    # 抓得住「字段名不存在」。§3b 是同类的第三种：栏目 × 材料矩阵里「算出数／不算数／本轴没判」
    # 三列的**主语是小节**（`aspect_attribution` 的判据是「所引来源登记在本小节声明的**某一栏**
    # 里」，它判不出「这一句服务的是哪一栏」），而表头一度写成「（本栏目）」——同一个小节里
    # 十几行拿到同一个数，读者会把「小节级读数」读成「本栏被 N 句覆盖」。断言把粒度差钉死：
    # 同小节两栏的三桶值必须相同、换小节必须各归各、非本轴的记录不得串入，而栏目级的分辨力
    # 只在 `cw-4` 段级声明轴那两列上。夹具是替身，只钉判定与词表；不证明材料真的取到了、
    # 正文达到人读内容门，也不宣称 M930-3 / TS5 或任何正式阶段关闭。
    "evals.test_m930_3_cited_destination_ledger",
    # 第二批的**真工具人口**回归：表对象的人口必须是"本次派发**已定位**的宿主块"，不是
    # "本次产出过 `role=\"body\"` 候选的块"。这一条只打真入口（`TreeInspectionSession.inspect`
    # ＋ `located_table_hosts`），逐面覆盖「零正文候选的已选中宿主里表仍返回」「未选中的
    # 相邻宿主不得混入」「不可归属块永不进人口」「span 上界只限正文通道」以及 table-only
    # 返回如实化；样本与否定例都在真文档上**动态发现**，缺样本时如实 skip，不伪造通过。
    "evals.test_m930_3_table_host_population",
    # 取材**消费侧**的两件机制（`rpo-1` 读取计划 + `tim-2` 有界续读）。之所以必须钉住：
    # 读集的**集合**由导航决定、本层一字不改，因此这一层一旦出错，错法全是**静默**的——
    # 顺序错了只是「先读别的」，复用键少了轴只是「拿另一批 span 冒充本批结论」，续读位
    # 不前进只是「原地打转」。三件事都不会抛异常，只会让并列业务正文悄悄地进不了 Pack。
    # 合成树段断言：排序依据只有三个既有结构量（自有准入正文字符数 / Contract 完整标签段
    # 命中数 / 文档序），**不因标题措辞降级**（标题写「概述」而自有正文 145 字的节点仍是
    # 实质档；同簇另放一个「业务味」标题、自有正文 4 字的节点作反例证明判据是按量），
    # 产物与入参**同集合**，缺正文量时**不重排**只记状态，且 `ReadPlan` 上**没有任何
    # 支持 / 充分性字段**（否则「先读谁」会被读成「这一栏已获支持」）；复用键必须区分
    # 「同一集合、不同顺序」，四轴各变一次各不复用；续读读数从截断条目逐字读出、形状不对
    # 退回零值，截断条目**不带具名候选**故不得被冒充成「预算未读的候选」。工具段只在真
    # 文档上跑：首轮入参与 `v7` 逐字相同（无 `span_cursor` 键）、游标指向读集外 / 越界 /
    # 与 `evidence_ids` 同用三种 fail-closed、两轮并集不重不漏且起点严格前进。样本或
    # Evidence 库缺失时如实 skip，不伪造通过。不调 LLM、不联网、不写任何库。
    "evals.test_m930_3_read_plan",
    # M930-4 只读确定性 Assurance：复核真实持久化的逐句硬门/既有审阅，
    # 财务 A2 未审与跨节未审保持显式不放行；不发新模型调用或改历史 run。
    "evals.test_m930_4_cited_controller",
    # M930-4 报告级审阅**输入**：为读者能看到、A1 从未表态的三块内容（财务 A2 / 两节合读 /
    # 未用材料的选择性遗漏）备好隔离、内容寻址、版本可失效的输入面；A2 有独立内容身份，
    # 「正文改一字」与「A2 改一字」是两条互不牵连的失效轴。刻意不产生任何意见：
    # 没有调用路径，state 恒为 review_not_run。不调 LLM、不联网、不写库、不改历史 run。
    "evals.test_m930_4_report_review",
    # M930-4 报告级审阅**链**：可执行但一次调用都不发。分批覆盖/容量预检/覆盖等式/
    # 信封层（含把冻结 wire 的 schema 错误归一到本链错误，否则失败留存接不住）/单元归属/
    # 替身端到端/身份 round-trip/陈旧读数不聚合/**硬错误不被全 supported 覆盖**/
    # prompt 资产加载即对账/事前预算门装不上就发不出/源 run 逐字节不变。
    "evals.test_m930_4_report_review_chain",
    # M930-4 人读读回的两支措辞：**没有**绑定记录时保留「停在这里等授权」，**有**记录时
    # 换成逐 scope 实际读数（跑没跑/几批成几批/放进多少/回来多少/提了几条），并点出没进过
    # 请求的成员。走真实入口写进临时目录再读回；另有一条盯着本机那份交付读数与现行生成器
    # 逐字节一致，免得生成器改了、产物停在旧措辞上。不调 LLM、不联网、不写库、不改源 run。
    "evals.test_m930_4_assurance_readback",
    # M930-5 只读演示入口：真实持久化双节 run、版本/文件哈希、12 区去重、
    # 篡改/缺失/越界反例及断网不写的聚焦回归；不把 UI 当成发布或人工确认入口。
    # 另含本批新接的**真实报告级审阅读数**：显式/环境变量选择能读到新侧车、不设环境变量时
    # 不因新增产物冒出「两份当前读数」、身份不匹配仍拒，以及整体判 failed 的 scope
    # 仍要显示它的 7 条原始意见与 50 个未覆盖成员。
    "evals.test_m930_5_cited_demo_ui",
    # M930-4/5 三屏改版的**只读展示绑定**（`cdb-1`）：两个**不同 run** 的已存产物
    # （单节扁平 `ndc5` 公司正文 + `dual_v2_r1/financial` 的 A2）各自逐字节钉指纹、逐身份
    # 交叉核对（正文版本绑定的是**有效稿**、审阅调用必须落在本 run 成功的账本里、A2 必须
    # 仍是零模型调用的确定性呈现）。反例必须是**拒**而不是降级：任一产物改一字节、缺一个
    # 冻结产物、A2 自称发过模型调用 ⇒ `DisplayBindingError`。来源材料只在内存里算 SHA-256，
    # 三份全中才对上「可查看运行回放」，缺一份或混入别的文件都具名拒绝。第三屏右栏恒读
    # 「并列演示基线 · 不可发布 · 未经人工接受」，A2 能否单列靠**重算内容版本**而非栏目同名；
    # 旧双节 run 的侧车与意见只出现在明确标注「原 run 历史审计」的区域，绝不套到 ndc5 正文上。
    # 全程不调 LLM、不联网、不写库、不改历史 run、不写回人工确认。
    "evals.test_m930_5_display_binding",
    # 「上传三份 PDF → **本次运行**」这条新纵链的接线与反例（`cri-1` 运行输入 + `rj-1` 阶段
    # 日志 + 第 1～3 屏）：落盘的必须是本次上传的字节、解析只按 `(document_id, sha256)` 命中、
    # 点击只铸一次 run-id 且模式按参数走（**不**写死 `--mode offline`，获批后同一入口走真实路径）。
    # 反例全部要求**拒**而不是降级：对象移走/改一字节/多出第四个文件/同名冒充/重复上传/
    # 清单与当前登记不是同一组 ⇒ 具名拒；链在**建立结果目录之前**停下，绝不回退 `data/samples`
    # 或登记路径；拿历史 run（`ndc5`、`dual_v2_r1`）当本次 run 读 ⇒ 拒，且不碰任何历史读入口；
    # 深链 `?view=2/3` 未伴随本次上传 ⇒ 回第 1 屏并说明，不去读历史绑定。`rj-1` 侧另钉
    # 「无 `checkpoint_id` 即写不可恢复」与未登记阶段名/状态具名拒。
    # 全程不调 LLM、不联网、不写 `data/`、不跑链、不改历史 run。
    "evals.test_m930_5_upload_run",
    # 真实模式的一次性运行授权（`cra-1`）：把「谁批准了这一次运行」做成盘上一条逐字段对上的
    # 凭据，而不是页面提示、单选框或每-run 预算——三者都由浏览器侧决定，脚本照抄即可。
    # 正向：一份对上的凭据放行，且**只消费一次**（八线程并发消费断言恰好一个成功，证明的是
    # `O_CREAT|O_EXCL` 的原子性而不是「先查后写」）。反向逐字段各一条：run-id／模式／主体／
    # 节集合／模型／三条 prompt 版本／三份材料哈希／每类上限／整轮上限／重试次数，任一项不同
    # 即拒；身份体封闭（未知键、缺键、版本不符）也拒。链上层钉的是**停止位置**：真实模式
    # 无可用授权时在**建立结果目录之前** `SystemExit`，结果目录一个都没建（不留看似仍在运行
    # 的半成品），并在运行输入目录留下可读的拒绝留痕。全程不发模型请求、不联网、不写
    # `data/` 库、不改历史 run；离线模式不走这道门（离线不发请求），由离线纵链覆盖。
    "evals.test_m930_5_run_authorization",
    "evals.test_section_validator",
    # 套件**自身**的形状回归：登记面（磁盘 `test_*.py` 与 `EVAL_MODULES` 互为子集）、形状面
    # （每个登记模块有顶层同步 `main()`，且 `sys.exit`／`os._exit`／`raise SystemExit`／
    # `exit()` 全在 `__main__` 守卫内）、运行器面（`SystemExit` 必须被接住并降级成点名 CRASH）。
    # 立这条是因为本批两个新模块各犯过一次：模块级 `sys.exit` 在导入期执行，`SystemExit` 是
    # `BaseException`，`except Exception` 接不住，套件在 240/244 处**不打印 TOTAL** 地静默收尾、
    # 退出码 0，最后四个模块（含 `test_m930_3_pre_gate_draft`、`test_section_validator`）
    # 从来没跑过。形状缺陷不刷红，只刷「少」，所以必须有专门一条回归盯着它。
    "evals.test_eval_suite_shape",
    # 离线只读评测引擎（`evaluation/rubric_eval`）：24 个指标落到真实 run 的已落盘产物上。
    # 这组回归盯的是**评测自己会不会撒谎**——缺件报 0、上游未跑报 0/0、读另一个 run 的产物、
    # 把离线回声的 `issues[]` 算成独立审阅覆盖，都不会刷红，只会让 readback 更好看。
    # 主干因此是反例：缺文件（回 None 不抛）、错哈希（改一字节即掉分）、跨 run 拼接（证据
    # 路径必须落在本 run 目录内）、零分母（回 None，绝不 100%）、上游失败（报未运行不报 0）、
    # 离线回声冒充真实审阅（fail-closed）。另配两组正向对照防止"恒拒"蒙混：合规合成 run
    # 必须真测出 MEASURED，四件套必须真落盘、每样本恰好 24 行、且只创建不覆盖。
    # 全程不联网、不发模型请求、不建 run、不写 `data/` 库、不改 `evaluation/results` 的字节。
    "evals.test_rubric_eval",
]


def main() -> None:
    real_llm = "--real-llm" in sys.argv

    if not real_llm:
        os.environ["EVAL_MOCK_LLM"] = "true"

    print(f"\n{'=' * 64}")
    print(f"  Eval Suite  {'(MOCK LLM)' if not real_llm else '(REAL LLM)'}")
    print(f"{'=' * 64}\n")

    all_results: dict[str, dict] = {}
    t0 = time.time()

    for module_name in EVAL_MODULES:
        short_name = module_name.replace("evals.", "")
        try:
            mod = importlib.import_module(module_name)
            result = mod.main()
            all_results[module_name] = result
        # **必须连 `SystemExit` 一起接**：模块若在**导入期**（而非 `__main__` 守卫里）留下
        # `sys.exit(...)`，`SystemExit` 是 `BaseException`，只写 `except Exception` 接不住——
        # 进程会以那个退出码结束，**TOTAL 不打印**，它之后还没跑的模块静默消失，而日志看起来
        # 仍像「套件基本跑完」。这不是假想：本批两个新模块各犯过一次，套件在 240/244 处静默收尾
        # 且退出码 0。接住之后同一种缺陷变成一条**点名**的 fail-closed CRASH 记录，套件照跑到底、
        # TOTAL 照打、红项照记。`KeyboardInterrupt` 不在其列，Ctrl-C 仍可中止套件。
        except (SystemExit, Exception) as e:
            import traceback
            all_results[module_name] = {
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "details": [f"CRASH: {type(e).__name__}: {e}\n{traceback.format_exc()}"],
            }

        r = all_results[module_name]
        # 汇总形状也是契约（`passed` / `failed` / `skipped` / `details`）。少一个键或类型不对时
        # **不能**让套件在下面那行抛 `KeyError`：那会让它之后的模块**静默不跑**、TOTAL 也不打印，
        # 而日志看起来仍然像是「套件基本跑完」。这里改成一条 fail-closed 的 CRASH 记录。
        _bad = [k for k in ("passed", "failed", "skipped") if not isinstance(r.get(k), int)]
        if _bad:
            r = {"passed": 0, "failed": 1, "skipped": 0,
                 "details": [f"CRASH: 模块汇总形状不合契约（缺/非整数键 {_bad}；"
                             f"实得键 {sorted(r)}）"]}
            all_results[module_name] = r
        if r["failed"] == 0 and r["passed"] > 0:
            status = " PASS"
        elif r["skipped"] > 0 and r["failed"] == 0:
            status = " SKIP"
        else:
            status = " FAIL"
        print(f"  [{status}] {short_name}  "
              f"(+{r['passed']} / -{r['failed']} / ~{r['skipped']})")

    elapsed = time.time() - t0

    total_passed = sum(r["passed"] for r in all_results.values())
    total_failed = sum(r["failed"] for r in all_results.values())
    total_skipped = sum(r["skipped"] for r in all_results.values())
    total_tests = total_passed + total_failed + total_skipped

    print(f"\n{'=' * 64}")
    print(f"  TOTAL: {total_passed} passed, {total_failed} failed, "
          f"{total_skipped} skipped  ({elapsed:.1f}s)")
    print(f"{'=' * 64}\n")

    # Print failures
    if total_failed > 0:
        print("FAILURES:\n")
        for name, r in all_results.items():
            for d in r.get("details", []):
                if d.startswith("FAIL") or d.startswith("CRASH"):
                    print(f"  [{name}] {d}")
        print()

    sys.exit(0 if total_failed == 0 else 1)


if __name__ == "__main__":
    main()

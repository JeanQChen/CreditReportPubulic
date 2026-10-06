"""M930-3 narrative wire（计划 §6.3）：Claim→段落/表格 的**可回查**正文承载对象。

本模块只提供**类型 + 身份 + 确定性校验**，不做研究、不检索、不写 Prompt、不碰 Store。

为什么需要独立的 narrative wire，而不是重用 `chapter_writer.ParagraphDraft`：
后者是「句子文本 + `{{fact:id}}` marker」的**文本型**草稿，marker 由渲染器事后解析成引用。
那条路无法证明「这句话由哪条 Claim 支撑」，也无法区分「渲染器找到了 marker」与
「作者真的声明了这条 Claim」。因此计划 §6.3 明确要求：旧 `ParagraphDraft` / `TableDraft`
不得原样当最终 wire，也不得改写旧 `SectionResult` wire 语义来混装新对象。

本模块的血缘立场：
- Pack/Fact/Material → Claim 的**唯一边**是 `ClaimSupportRef`，且只在 **typed ID 图**上比对，
  绝不按文本相似度推断（§6.3「不得按文本相似度推断血缘」）。
- 每条被选中的权威 fact 必须有**恰一条** `FactNarrativeDisposition`，缺口不得静默消失。
- 句子里的每个数字都必须能落回它所绑 Claim 的权威事实（`scan_numeric_tokens` +
  `authorized_numeric_tokens`），因此「裸数字」在类型层就不可表达。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from financial_v2 import period_basis as _FPB
from sections import backbone_schema as _BACKBONE

__all__ = [
    "NARRATIVE_SCHEMA_VERSION",
    "LEGACY_NARRATIVE_SCHEMA_VERSIONS",
    "LEGACY_NARRATIVE_SCHEMA_NARR3",
    "LEGACY_NARRATIVE_SCHEMA_NARR4",
    "LEGACY_NARRATIVE_SCHEMA_NARR5",
    "LEGACY_NARRATIVE_SCHEMA_NARR6",
    "NARRATIVE_RULES_VERSION",
    "NARRATIVE_GATE_VERSION",
    "CLAIM_BINDING_GATE_VERSION",
    "CLAIM_ENTAILMENT_RULES_VERSION",
    "MATERIAL_PROCESSING_RULES_VERSION",
    "MATERIAL_MANIFEST_SCHEMA_VERSION",
    "LEGACY_MATERIAL_MANIFEST_SCHEMA_VERSIONS",
    "LegacyWriterMaterialManifestV1View",
    "load_legacy_wmm1_for_audit",
    "MATERIAL_USAGE_STATES",
    "MATERIAL_NOT_USED_REASONS",
    "MATERIAL_PROOF_POLICY_KEYS",
    "MATERIAL_NOT_USED_PROOF_KEYS",
    "REPORT_SCHEMA_VERSION",
    "CONNECTOR_VERSION",
    "CONNECTORS",
    "RELATION_CONNECTOR_VERSION",
    "RELATION_ASSERTING_CONNECTORS",
    "NEUTRAL_CONNECTORS",
    "verify_connector_relation_partition",
    "relation_connector_hits",
    "VAGUE_PERIOD_PHRASES",
    "SYSTEM_PROVENANCE_PHRASES",
    "system_provenance_hits",
    "ISSUER_SELF_DESCRIPTION_PHRASES",
    "ATTRIBUTION_MARKERS",
    "issuer_self_description_hits",
    "attribution_hits",
    "FACTUAL_CLAIM_SEPARATOR",
    "NON_COVERED_ASPECT_STATUSES",
    "render_factual_text",
    "amount_sentence",
    "PROXY_STATUS",
    "PROXY_MARKER_PHRASE",
    "is_proxy_fact",
    "proxy_qualifier",
    "financial_period_text",
    # `pwr-5`：读者面**来源**说明（渲染层从既有 `CitationRef` 确定性重建，零 Narrative wire 改动）。
    "citation_ref_source",
    "citation_source_index",
    "citation_source_notes",
    "citation_source_coverage",
    "is_derived_two_period_fact",
    "financial_table_cell",
    "FinancialCellComponents",
    "financial_cell_components",
    "authoritative_fact_surface",
    "citation_from_mapping",
    "note_citation",
    "citation_source_identity",
    "MaterialCandidate",
    "MaterialBindingAmbiguityError",
    "material_index",
    "material_binding_for_citation",
    "authoritative_citation_refs",
    "authority_fact_entries",
    "material_object",
    "material_citation_ref",
    "render_final_narrative_markdown",
    "build_section_narrative",
    "FINANCIAL_TABLE_MIN_PERIODS",
    "FINANCIAL_TABLE_CAPTION",
    "FINANCIAL_BASIS_TEXTS",
    "financial_table_caption",
    "reader_unit_short_text",
    "FINANCIAL_TABLE_EMPTY_CELL",
    "FINANCIAL_TABLE_LABEL_HEADER",
    "tabled_claim_ids",
    "build_metric_period_tables",
    "CLAIM_TYPE_BY_FACT_TYPE",
    "claim_type_for_fact_type",
    "claim_citation_refs",
    "finalize_section_claims",
    "derive_section_narrative_id",
    "SectionNarrative",
    "authoritative_locator",
    "claim_numeric_tokens",
    "numeric_token_in_claims",
    "vague_period_hits",
    "HIGH_RISK_SURFACE_MARKERS",
    "MARKER_SUBSTRING_EXEMPTIONS",
    "ENTITY_SUFFIXES",
    "marker_hits",
    "entity_name_tokens",
    "high_risk_surface_tokens",
    "unauthorized_surfaces",
    "unauthorized_surfaces_within_claims",
    "SENTENCE_TERMINATORS",
    "SEAM_LEAD_PUNCTUATION",
    "JOIN_RESIDUE_PUNCTUATION",
    "is_punctuation_only_join",
    "has_internal_sentence_terminator",
    "unseparated_seam_positions",
    "COMPOSED_SENTENCE_DEFECTS",
    "COMPOSED_DEFECT_NOT_PRESENT_IN_ORDER",
    "COMPOSED_DEFECT_MECHANICAL_JOIN",
    "COMPOSED_DEFECT_INTERNAL_TERMINATOR",
    "COMPOSED_DEFECT_UNSEPARATED_SEAM",
    "COMPOSED_DEFECT_OVER_CLAIM_BOUND",
    "composed_organization_defect",
    "split_composed_sentence_at_terminators",
    "claim_citation_ids",
    "claim_topic_for_conservation",
    "current_wire_collection",
    "has_registered_gaps",
    "CLAIM_NARRATIVE_DISPOSITIONS",
    "CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION",
    "CLAIM_OMISSION_REASONS",
    "REDUNDANCY_SCREEN_VERSION",
    "REDUNDANCY_SCREEN_THRESHOLD",
    "redundancy_candidate_pairs",
    "unsupported_relation_connectors",
    "unattributed_self_description",
    "ClaimNarrativeDisposition",
    "derive_claim_narrative_disposition_id",
    "verify_claim_narrative_dispositions",
    "verify_section_narrative",
    "verify_section_narrative_claims_selected",
    "render_section_markdown",
    "render_body_and_appendix",
    "canonical_unresolved_projections",
    "render_paragraphs_markdown",
    "render_unresolved_appendix",
    "UNRESOLVED_APPENDIX_HEADING",
    "body_fingerprint_of",
    "AUTHORITY_KINDS",
    "SUPPORT_ROLES",
    "SUPPORT_SEMANTICS",
    "AUTHORIZATION_PATHS",
    "BINDING_SUBJECT_KINDS",
    "BINDING_DECISION_RESULTS",
    "BINDING_EDGE_RESULTS",
    "BINDING_EDGE_REASON_CODES",
    "BINDING_STRUCTURAL_REASONS",
    "ENTAILMENT_VERDICTS",
    "ENTAILMENT_REJECTION_REASONS",
    "CLAIM_SUPPORT_REF_UNION_KINDS",
    "DRAFT_UNIT_KINDS",
    "EXTERNAL_SNAPSHOT_REF_KEYS",
    "FACT_FIELD_BY_AUTHORITY_KIND",
    "ALL_FACT_FIELDS",
    "validate_support_edge_shape",
    "is_claim_support_ref_union_member",
    "AUTHORITY_STATES",
    "SENTENCE_KINDS",
    "NARRATIVE_DISPOSITIONS",
    "DISPOSITION_REASON_CODES",
    "NON_FACTUAL_ROW_REASONS",
    "GATE_SEVERITIES",
    "POST_GATE_ADJUDICATED_GATE_RULES",
    "NarrativeSchemaError",
    "scan_numeric_tokens",
    "authorized_numeric_tokens",
    "canonical_json",
    "content_id",
    "ClaimSupportRef",
    "ClaimCandidate",
    "NarrativeDraftUnit",
    "NaturalProseDraftUnit",
    "derive_natural_prose_unit_id",
    "validate_natural_prose_mapping",
    "natural_prose_draft_digest",
    "natural_prose_spec_digest",
    "verify_sentence_fidelity",
    "SentenceFidelityReport",
    "sentence_fidelity_digest",
    "assertion_critical_surfaces",
    "entity_head_nouns",
    "ENTITY_HEAD_NOUNS",
    "FINAL_SENTENCE_FIDELITY_VERSION",
    "SENTENCE_FIDELITY_DEFECTS",
    "CAUSAL_ASSERTION_MARKERS",
    "SCOPE_QUALIFIER_MARKERS",
    "CONCLUSION_STRENGTH_MARKERS",
    "FIDELITY_SINGLE_CHAR_MARKERS",
    "FINAL_SENTENCE_DECISION_SCHEMA_VERSION",
    "FINAL_SENTENCE_RULES_VERSION",
    "FINAL_SENTENCE_DECISION_VERDICTS",
    "FINAL_SENTENCE_ATOM_VERDICTS",
    "FINAL_SENTENCE_ATOM_KINDS",
    "FINAL_SENTENCE_ATOM_REASON_CODES",
    "FINAL_SENTENCE_DECISION_REJECTION_REASONS",
    "FINAL_SENTENCE_BLOCK_REASONS",
    "SentenceAtomReading",
    "FinalSentenceFidelityDecision",
    "derive_final_sentence_fidelity_decision_id",
    "final_sentence_gate_state",
    "sentence_support_set_digest",
    "fact_bearing_sentence_ids",
    "ProposedSupportRef",
    "WriterMaterialManifestEntry",
    "WriterMaterialManifest",
    "WriterMaterialProcessingDisposition",
    "manifest_member_ref",
    "fact_provenance_ref",
    "provenance_axis_of",
    "MATERIAL_PROVENANCE_PREFIX",
    "FACT_PROVENANCE_PREFIX",
    "PROSE_PROVENANCE_POLICY_VERSION",
    "PROSE_OCCURRENCE_POLICY_VERSION",
    "material_reading_view",
    "derive_manifest_id",
    "derive_manifest_fingerprint",
    "derive_wmpd_id",
    "validate_material_processing",
    "derive_draft_revision",
    "derive_draft_revision_with_natural_prose",
    "BindingEdgeResult",
    "ClaimBindingDecision",
    "ClaimEntailmentDecision",
    "AcceptedSupportBinding",
    "Narr3SectionDraftView",
    "load_legacy_narrative_narr3_for_audit",
    "Narr4SectionDraftView",
    "load_legacy_narrative_narr4_for_audit",
    "Narr5SectionDraftView",
    "load_legacy_narrative_narr5_for_audit",
    "Narr6SectionDraftView",
    "load_legacy_narrative_narr6_for_audit",
    "Narr7SectionDraftView",
    "load_legacy_narrative_narr7_for_audit",
    "LOCATOR_SCHEMA_VERSION",
    "LEGACY_LOCATOR_SCHEMA_VERSION",
    "LOCATOR_KINDS",
    "LOCATOR_VARIANT_FIELDS",
    "validate_locator",
    "locator_sort_key",
    "load_legacy_locator_for_audit",
    "block_range_locator",
    "char_range_locator",
    "table_cell_locator",
    "whole_payload_locator",
    "derive_claim_candidate_id",
    "derive_draft_unit_id",
    "derive_proposed_support_id",
    "derive_binding_decision_id",
    "derive_entailment_decision_id",
    "derive_accepted_support_binding_id",
    "FactNarrativeDisposition",
    "NarrativeSentence",
    "NarrativeParagraph",
    "NarrativeTableRow",
    "NarrativeTable",
    "SectionDraft",
    "NarrativeGateIssue",
    "NarrativeGateResult",
    "NarrativeEvaluationBinding",
    "AssembledSection",
    "AssembledReport",
    "required_fact_ids",
    "note_container_id",
    "support_fact_id",
    "proposal_fact_key",
    "binding_fact_key",
    "external_authority_container_id",
    "authority_numeric_texts",
    "gate_draft",
    "derive_support_ref_id",
    "derive_disposition_id",
    "derive_sentence_id",
    "derive_paragraph_id",
    "derive_table_row_id",
    "derive_table_id",
    "derive_draft_id",
    "derive_gate_result_id",
    "derive_binding_id",
    "derive_report_version",
    "derive_report_id",
]

#: narrative wire 结构版本。字段增删/语义变化必须递增。
#: `narr-4`（M930-3A）：`ClaimCandidate` / `NarrativeDraftUnit` / `ProposedSupportRef` /
#: `ClaimBindingDecision` / `ClaimEntailmentDecision` / `AcceptedSupportBinding` 六个 successor
#: 类型进入 wire；`SectionDraft` 去掉 `section_result_id`（Result→Draft 单向）；narr-3 载荷只能
#: 经 `load_legacy_narrative_narr3_for_audit` 只读回放，**narr-4 current reader 拒绝 narr-3**。
#:
#: `narr-5`（M930-3 §三 C）：final Narrative 的**自然组织** successor 进入 wire —
#: `NarrativeSentence` 新增 `context_binding_ids`，并新增第三种 `sentence_kind="composed"`
#: （一句由**多条**已接受 Claim 组织而成，可同时携带 context accepted binding）。句子的
#: 文本与它声明的绑定集合**共用一个内容身份**：任一改动都会改 `sentence_id`，因此
#: 「文本换了但绑定没换」或反之都无法通过。narr-3 / narr-4 载荷各经自己的 legacy reader
#: 只读回放，current reader 一律拒绝。
#: `narr-6`（M930-3.2 §四）：`locator_ref` 从裸三元组改为**版本化 tagged union**（`loc-1`：
#: `char_range` / `block_range` / `table_cell` / `whole_payload`）。原因不是审美：旧的裸三元组
#: 同时承载「半开字符区间」与「闭块区间」两种互不相容的语义，`(owner, 0, 0)`（合法单块材料）
#: 在 `start < end` 的校验下必然被拒，而放行它就要放宽同一个字段——两类区间从此无法区分。
#: 因此**必须升版**：`loc-0`（裸三元组）只能经 `load_legacy_locator_for_audit` 只读读取，
#: 且读出来的对象带 `loc-0` 标签、进不了 current wire（禁止静默重解释）。
#: `narr-7`（M930-3 指令 E 第 3 项）：**门前自然草稿**进入 wire —
#: `SectionDraft` 新增 `natural_prose_draft`（`NaturalProseDraftUnit`：一段可保留的自然正文 +
#: 「表达的是哪些 manifest 成员」+「这段正文里的事实原子各自提交了哪个候选」）；
#: `SENTENCE_KINDS` 新增第四种 `sentence_kind="natural"`（以草稿为表达基础的改写句，其第 8 条
#: 判据由「逐字等于 Claim 文本」换成 `verify_sentence_fidelity` 保真核对）。
#: 为什么**必须升版**（不能靠「新字段有缺省值所以不算改 wire」）：新字段改变了 `draft_revision`
#: 的输入集——草稿非空时它进修订。于是同一套 task/manifest 输入可以产出**两个**不同的修订，
#: 而载荷里的 marker 若仍是 narr-6，读的人无从判断手上这一束到底带不带草稿层、以及那个
#: `draft_revision` 是按哪一套输入算出来的。这与 `writer_attempt` 当时的情形逐字相同（纯追加、
#: 缺省与加入前逐字节等价，但仍然升版）：**形状可变而 marker 不变**就是让 reader 去猜。
#: 兼容口径与既往各版一致：`narr-6` 载荷一律经 `load_legacy_narrative_narr6_for_audit` 只读回放
#: （含既有全部冻结 run 工件），**narr-7 current reader 拒绝 narr-6**，缺该键的旧载荷在 legacy
#: 路径下读回来与加入该字段之前逐字节等价。
#:
#: `narr-8`（M930-3 定点返修 P1-B）：**草稿出处的第二条轴**进入 wire ——
#: `NaturalProseDraftUnit` 新增 `source_fact_refs`（该段正文的出处是**权威事实行**），与既有的
#: `source_member_refs`（出处是 manifest 材料行）**互斥**：恰有一个非空。
#: 为什么**必须升版**：财务节的精确材料清单**合法地为空**（`FinancialFactPack` 的事实不经过
#: Pack 材料），因此「草稿的出处只能是材料」这条纪律在那一节里没有任何可写的合法值——要么
#: 伪造一条 `ResearchMaterial`（明令禁止），要么这一段正文根本写不出来（`pw-15` 下财务节就是
#: 后者）。加一条正交的出处轴改的是**单元的形状**：同一个 `prose_unit_id` 在新旧两版下含义不同
#: （旧版「出处=材料集」、新版「出处=材料集 **或** 事实集」），marker 不变就是让 reader 去猜。
#: 兼容口径与既往各版一致：`narr-7` 载荷一律经 `load_legacy_narrative_narr7_for_audit` 只读回放，
#: **narr-8 current reader 拒绝 narr-7**；不带 `source_fact_refs` 的旧载荷在 legacy 路径下读回来
#: 与加入该字段之前逐字节等价。
NARRATIVE_SCHEMA_VERSION = "narr-8"
#: 每个 legacy 版本**各自**的标记常量。legacy reader 必须按**自己那一版**精确比对，不能写成
#: 「在 legacy 集里」：legacy 集是会增长的（narr-3 → narr-3+narr-4），一旦写成集合成员判定，
#: 新增一版 legacy 就会让**旧 reader 静默多吞一版载荷**（narr-3 reader 开始接受 narr-4），
#: 而两侧载荷形状明确不同。单版本 reader = 单版本常量。
LEGACY_NARRATIVE_SCHEMA_NARR3 = "narr-3"
LEGACY_NARRATIVE_SCHEMA_NARR4 = "narr-4"
#: `narr-5`：locator 仍是裸三元组（`loc-0`）的最后一版。它的载荷只能经
#: `load_legacy_narrative_narr5_for_audit` 只读读取（locator 字段按 `loc-0` 读，不升级）。
LEGACY_NARRATIVE_SCHEMA_NARR5 = "narr-5"
#: `narr-6`：`locator_ref` 已是 `loc-1` tagged union、尚无门前自然草稿的最后一版。它的载荷只能经
#: `load_legacy_narrative_narr6_for_audit` 只读读取（既有的全部冻结 run 工件都是这一版）。
LEGACY_NARRATIVE_SCHEMA_NARR6 = "narr-6"
#: `narr-7`：门前自然草稿已是 `narr-7` 的形状（`SectionDraft.natural_prose_draft`、
#: `sentence_kind="natural"`）、但草稿出处**只有材料一条轴**的最后一版。它的载荷只能经
#: `load_legacy_narrative_narr7_for_audit` 只读读取（不升级为 narr-8）。
LEGACY_NARRATIVE_SCHEMA_NARR7 = "narr-7"
#: 登记过的 **legacy** narrative 载荷版本。只有经 `load_legacy_narrative_narr3_for_audit`
#: / `..._narr4_for_audit` / `..._narr5_for_audit` / `..._narr6_for_audit`
#: / `..._narr7_for_audit` 才能读回，且**不得**升级为 narr-8；current reader 必须拒绝它们。
#: 本元组服务于「登记面」判据（legacy 面非空、与 current 不重叠、preflight 比对），**不是**
#: reader 的准入判据——准入一律用上面的单版本常量精确比对。
LEGACY_NARRATIVE_SCHEMA_VERSIONS = (
    LEGACY_NARRATIVE_SCHEMA_NARR3, LEGACY_NARRATIVE_SCHEMA_NARR4,
    LEGACY_NARRATIVE_SCHEMA_NARR5, LEGACY_NARRATIVE_SCHEMA_NARR6,
    LEGACY_NARRATIVE_SCHEMA_NARR7)
#: 本节写作**规则**版本（不属于本模块执行，供 draft/evaluation 记录）。
#: `nrules-5`（§三 C）：规则集随 narr-5 前进——章级规则现在消费**定稿 Narrative**
#: （`SectionNarrative`）而不是门前 `SectionDraft`，并新增「composed 句不得新增高风险表面」
#: 与「Claim selected/omitted 去向必须完备」两条。
#: `nrules-6`（§三 G）：规则集新增两条守恒/限定判断——句级「引用必须逐条由本句声明的 Claim
#: 派生（selected-material limitation）」与段级「段落 topic 归属必须等于所引用 Claim 的派生
#: 序列（cross-topic 守恒）」。wire 形状未变（故 `narr-5` 不动），变的是**判定集**。
#: `nrules-7`（M930-3.2 §七 1）：规则集新增「composed 句必须确实被组织过」——声明的 Claim 文本
#: 必须按序出现在正文里、剔除后剩下至少一个汉字组织成分、一句最多 4 条 Claim。wire 形状未变
#: （`narr-6` 不动：没有新增/删除任何字段，`sentence_kind` 的取值集也没变），变的是**判定集**：
#: 同一份正文在 nrules-6 与 nrules-7 下可能一个通过、一个被拒。
#: `nrules-8`（M930-3 返修 P2 §三 2.4）：判定集再前进两条——(i) 组合句的高风险表面改为**边界
#: 感知**核验：表面只在「组织段」内合成，不跨 Claim 段与组织段的接缝（消掉 `…年` + `同时` +
#: `公司…` 这类接缝上凭空合成的假主体，判据本身的严格度不变，只是不再量错对象）；(ii) 组合句
#: 不得含**句中**句末标点——`A。同时B。` 是两句首尾相接，不是一条多 Claim 自然句。wire 形状
#: 未变（`narr-6` 不动），变的是**判定集**：同一份正文在 nrules-7 与 nrules-8 下可能一个通过、
#: 一个被拒。
#: `nrules-9`（M930-3 r4 后定点返修 ⑥）：判定集再前进一条——第 9 条「正文不得自称系统的检索 /
#: 核验行为」。年报转述的行业数据（以及任何来自文档材料的 Claim）都**不是**系统独立联网核验
#: 的结果，因此一句话里出现 `SYSTEM_PROVENANCE_PHRASES` 的任何一个短语时，该短语必须能在它
#: 自己声明的 Claim 文本里**逐字**找到；否则即拒。「年报里写着、系统照样转述」不受影响
#: （Claim 文本逐字在场），被拒的只是组织者/写作器**替系统自报**一个它没有做过的动作。
#: wire 形状未变（`narr-6` 不动），变的是**判定集**。
#: `nrules-10`（M930-3「先证明能成稿」批 §一.1）：判定集新增两条**机械**修正，三处使用点
#: （写入侧候选授权面 / `cbg-2` 支撑边授权面 / composed 句表面守恒）全部经同一实现，因此同进：
#: (i) **标记词子串豁免**（`MARKER_SUBSTRING_EXEMPTIONS`）——`说明` 作为**文书名**的一部分
#: （`募集说明书`）出现时不再算命中；(ii) **主体名匹配集卫生**——内嵌后缀标签（`有限公司`
#: ⊂ `股份有限公司`）、紧邻后缀标签（`证券` + `交易所`）、回扫上界截断造成的不存在主体名、
#: 以及通用前缀词（`根据` / `年度报告披露` / `发行人` / `母公司` / `下属` / `境内`）一律不再
#: 产出 token。**没有任何风险词被删除**，也没有任何主体后缀被删除：被剔除的是「同一份文本里
#: 由机械回扫**造出来**的字面成分」。判定集变了：同一份文本在 nrules-9 与 nrules-10 下可能一个
#: 命中、一个不命中，故不得共用版本号。
#: `nrules-11`（M930-3 写作主链定点批 §一）：判定集新增**五组同类的硬事实封闭标记**——
#: 会计口径（`会计期间` / `会计年度` / `公历年度` / `记账本位币` / `本位币`）、法人身份角色
#: （`法定代表人` / `法人代表` / `实际控制人` / `控股股东` / `董事长`）、状态评价（`正常` /
#: `良好` / `稳定`）、交叉引用（`详见` / `参见`）、证券上市状态（`挂牌` / `上市` / `上市交易`），
#: 并为 `上市` 登记了文书名豁免（`上市公告说明书`）。动因是 r7b 现场的**实测缺陷**：六条
#: **不带任何数字、也不带任何既有标记**的 path-B 候选（`公司会计期间采用公历年度` /
#: `本公司及境内子公司以人民币为记账本位币` / `公司法定代表人为曾毓群` /
#: `境外客户回款情况正常` / `公司作为整体…详见年度报告财务报告部分` /
#: `公司发行H股股份在香港联交所主板挂牌并上市交易`）因此整句进入了正文——其中
#: 「法定代表人」一条还与材料侧 `blocked` 缺口**同时可见**。
#: **不删任何既有词**：新词表与 r7b 两份真实语料（公司 64 候选 + 14 草稿单元，财务 24 候选，
#: 共 102 条）逐条对账，命中的**恰好**是上述目标候选（另有两个草稿单元命中 `上市`/`挂牌`，
#: 而草稿单元从不进 path-B 扫描、其文本也从不进入正文），财务语料零命中。
#: 判定集变了：同一份文本在 nrules-10 与 nrules-11 下可能一个命中、一个不命中，故不得共用版本号。
#: `nrules-12`（M930-3 写作主链定点批 §二）：第 8 条新增判据 e「组合句的接缝必须有分隔标点
#: 起头」（`SEAM_LEAD_PUNCTUATION`）。动因同样是 r7b 现场的**实测缺陷**：组织器把两条 Claim
#: 用句首连接语直接黏在一起（`…研发、生产、销售` + `此外，` + `公司产品可应用于…`），
#: 输出给读者的是「…研发、生产、销售此外，公司产品…」这样的病句，而 a/b/d/c 四条既有判据
#: 在这个文本上全部通过（Claim 按序在场、有汉字组织成分、无句中句末标点、未超条数上界）。
#: **不放宽任何既有判据**：新增的是第 8 条的第五条，判据 a/b/c/d 一字未改。
#: `nrules-13`（M930-3 读者面正文批 §三）：新增两组**封闭**短语集，都只在**正文**这一面生效、
#: 都不进 `HIGH_RISK_SURFACE_MARKERS`：`ISSUER_SELF_DESCRIPTION_PHRASES`（发行人自述评价语，
#: 配 `ATTRIBUTION_MARKERS`）与连接语的**关系语义**分组（`RELATION_ASSERTING_CONNECTORS` /
#: `NEUTRAL_CONNECTORS`）。动因是 r7b 现场的**实测缺陷**：年报「经营模式」一节里的
#: 「拥有核心技术优势」「提供**一流的**动力电池」被原样转述成没有归属的分析结论；组织器用
#: `另一方面` / `在此基础上` 把互不构成对照或递进的事项强行连接（本次实测 12 处接缝）。
#: 判定集变了：同一份正文在 nrules-12 下通过、nrules-13 下可能被拒，故不得共用版本号。
#: **不放宽任何既有判据**：nrules-12 的标记集与判据 a–e 一字未改。
#: `nrules-14`（M930-3 返修 ④：O-12 的期间纪律在**组织**侧落地）：新增一组**封闭**短语集
#: `CURRENT_STATE_FRAMING_MARKERS`（当前式措辞），只在**正文**这一面生效、**不进**
#: `HIGH_RISK_SURFACE_MARKERS`。动因是「同类较旧材料不得被静默写成当前状态」这条 O-12 纪律：
#: 在组织侧，越权不表现为新增事实表面（那是第 2/3 条），而表现为**换时点**——`目前` / `仍` /
#: `持续` 不含任何数字与期间，第 2/3 条必然放行，可它把断言从材料说的那个时点搬到了「现在」。
#: 判定集变了：同一份正文在 nrules-13 下通过、nrules-14 下可能被拒，故不得共用版本号。
#: **不放宽任何既有判据**：nrules-13 的标记集与判据 1–11 一字未改。
#: `nrules-15`（M930-3 指令 E 第 3 项）：判定集新增第四种句子类别的第 8 条判据——
#: `sentence_kind="natural"` 的**保真核对**（`verify_sentence_fidelity`，七轴：主体 / 数量 /
#: 期间 / 否定 / 范围 / 因果 / 结论 + 引用；见 `assertion_critical_surfaces` 与
#: `SENTENCE_FIDELITY_DEFECTS`）。判定集变了：同一份正文在 nrules-14 下没有「保真」这一条，
#: 在 nrules-15 下可能被拒，故不得共用版本号。
#: **这是本规则集第一次「换判据」而非「只新增」**，必须说准它换掉的是什么：只换掉`natural`
#: 类别下的「逐字等于 Claim 文本」这一条机械要求，`factual` / `composed` 两条老路径的第 8 条
#: 一字未改；第 2/3 条（不得新增高风险表面）对三个类别一律原样适用，**没有**任何放宽。
#: `nrules-16`（M930-3 定点业务闭环批 §二 2）：判定集**只扩当前式措辞闭集**——第 12 条的
#: 判据实现一字未改，`CURRENT_STATE_FRAMING_MARKERS` 新增普遍化组（`一直`/`始终`/`历来`/
#: `向来`/`一向`/`一贯`/`素来`/`从来`）与否定式普遍化组（`从未`/`未曾`）。动因是上位澄清：
#: 非数值的一般经营描述不要求每句机械重复期间，但**必须**挡住同一件事的另一种越权写法——
#: 把「某份材料披露时的情形」写成「一向如此」。判定集变了：同一份正文在 nrules-15 下通过、
#: nrules-16 下被拒（`A此外公司一直<B>` 里的「一直」材料没写过），故不得共用版本号。
#: **不放宽任何既有判据**：第 1–11 条与第 12 条的判据本身一字未改，只换判定集。
#: `nrules-17`（M930-3 主营业务质量返修批 §四）：第 2/3 条的**判据实现一字未改**，但
#: **判定集**随 `_entity_name_run` 的剥前缀改为不动点而变（`ENTITY_SUFFIXES` 与
#: `_ENTITY_GENERIC_PREFIXES` 两个封闭集都一字未改）。这是一种**收窄**：以前会产出、
#: 现在不再产出的，只有那些**剥一趟不够、剥到不动点才落空**的伪主体名——实测
#: `entity_name_tokens("截至报告期末公司…")` 由 `('报告期末公司',)` 变为 `()`。
#: 判定集变了就必须前进：同一份正文在 nrules-16 下被拒、nrules-17 下通过，二者不得共用
#: 一个版本号。**不放宽任何既有判据**：真正错误的主体名（来源里没有的公司名）照旧命中，
#: 反例见 `evals/test_m930_3_subject_and_report_date.py`。
#: `nrules-18`（同批，`scp-9`）：`nrules-17` 的定点修**没修干净**——同一条 `_entity_name_run`
#: 里，「本公司」被后缀匹配**切开**的那一格仍然产出伪主体名。`报告期末本公司应收账款…` 的
#: `公司` 命中在 `本` 之后，回扫得 `报告期末本`，剥一趟前缀得 `末本`，最终 token 是
#: `末本公司`——这个词在任何来源里都不存在，于是凭空吃一条 `unsourced_subject_surface`
#: （正反例见 `evals/test_m930_3_subject_and_report_date.py` §E）。修法是在剥前缀**之前**
#: 多判一步「去掉残留的 `本` 之后是不是整条时间状语」。同样是**收窄**：`ENTITY_SUFFIXES`
#: 与 `_ENTITY_GENERIC_PREFIXES` 两个封闭集一字未改，只多剥掉一个字；判定集变了，故与
#: `nrules-17` 不得共用版本号。**不放宽任何既有判据**：真正错误的主体名照旧命中。
NARRATIVE_RULES_VERSION = "nrules-18"
#: 确定性 narrative 门版本（`gate_draft` 的断言只按此版本复现）。
#: `ng-3`：规则 7 的**五项**展示字段（caption/header[*]/entity_scope/unit/period）一律改为
#: 逐字回查授权文本池，并修掉「待查值混进授权池」导致检查恒真的缺陷；`报告期` 改为按词根判；
#: 表格渲染把主体/期间/单位显式写进正文。
#: `ng-4`（M930-3A）：门输入与判定轴随 narr-4 successor 前进；authority 四元闭合。
#: `ng-5`（§三 C）：门随 narr-5 前进——composed 句的高风险表面必须逐字落在**它自己声明的
#: Claim 文本**里（context 绑定不授权任何表面），且 context 绑定必须真实存在于本节已接受的
#: context binding 集内。
#: `ng-6`（§三 G）：门后核验从五条判据扩到七条——第 6 条句级引用限定（`citation_ids` 必须
#: 逐条由本句声明的 Claim 派生）与第 7 条段级 cross-topic 守恒（段落 `topic_ids` 必须等于
#: 所引用 Claim 的 topic 首次出现序列）。门版本必须随判定集前进：同一份正文在 ng-5 与 ng-6
#: 下可能一个通过、一个被拒，二者不得共用一个版本号。
#: `ng-7`（M930-3.2 §七 1）：第 8 条「composed 句确实被组织过」进核验——声明的 Claim 文本必须
#: 按序逐字出现在句子文本里，剔除后必须有汉字组织成分（否则是 `A；B。` 式机械拼接），且一句
#: 最多 `MAX_COMPOSED_CLAIMS_PER_SENTENCE` 条 Claim。理由同前一版：判定集变了，「只检查
#: `len(claim_ids) >= 2`」的那个门与它判的不是同一件事，不得共用版本号。
#: `ng-8`（M930-3 返修 P2 §三 2.4）：第 2/3 条对组合句改用边界感知核验（`unauthorized_surfaces_
#: within_claims`），并在第 8 条新增判据 d「不得含句中句末标点」。判定集变了：同一份正文在
#: ng-7 与 ng-8 下可能一个通过、一个被拒（`A。同时B。` 在 ng-7 通过、ng-8 拒绝；接缝假主体
#: 在 ng-7 被拒、ng-8 通过），故不得共用版本号。
#: `ng-9`（M930-3 r4 后定点返修 ⑥）：第 9 条「正文不得自称系统的检索 / 核验行为」进核验
#: （判据见 `SYSTEM_PROVENANCE_PHRASES`）。判定集变了：同一份正文在 ng-8 下通过、ng-9 下可能
#: 被拒（正文里多了一句「系统已联网核验」而没有任何 Claim 说过它），故不得共用版本号。
#: **第 8 条一字未改**：拒绝「仅用标点拼接 Claim」的判据原样保留，本轮新增的是它的**有界
#: 定向补救**（`sections/narrative_organizer`，判据仍由本模块唯一给出）。
#: `ng-10`（M930-3 写作主链定点批 §一）：第 2/3 条的判据实现一字未改，但**判据所用的封闭标记
#: 集**随 `nrules-11` 前进，因此同一份正文在 ng-9 与 ng-10 下可能一个通过、一个被拒（典型：
#: 组织段里自造一个「详见」或「上市」，前一版不报、本版报）。门版本必须随判定集前进。
#: `ng-11`（M930-3 写作主链定点批 §二）：第 8 条新增判据 e「接缝必须有分隔标点起头」
#: （`COMPOSED_DEFECT_UNSEPARATED_SEAM`，随 `nrules-12` 前进）。判定集变了：同一份正文在
#: ng-10 下通过（`A此外，B。`）、ng-11 下被拒，二者不得共用一个版本号。第 2/3 条与 a/b/c/d
#: 四条判据一字未改。
#: `ng-12`（M930-3 读者面正文批 §三）：新增第 10 条「关系性连接语必须有材料支持」与第 11 条
#: 「发行人自述必须有归属」，两条都随 `nrules-13` 前进。判定集变了：同一份正文在 ng-11 下通过、
#: ng-12 下被拒（`A，另一方面，B` 里材料没说过这个对照；「公司拥有核心技术优势」没有归属），
#: 二者不得共用一个版本号。第 1–9 条与 a–e 六条判据一字未改——**只新增，不放宽**。
#: `ng-13`（M930-3 返修 ④）：新增第 12 条「组织语不得把断言挪到当前时点」
#: （`CURRENT_STATE_FRAMING_MARKERS`，随 `nrules-14` 前进）。判定集变了：同一份正文在 ng-12 下
#: 通过、ng-13 下被拒（`A，此外，公司目前<B>` 里的「目前」材料没写过），二者不得共用一个版本号。
#: 第 1–11 条与 a–e 六条判据一字未改——**只新增，不放宽**。
#: `ng-14`（M930-3 指令 E 第 3 项）：第 8 条在 `sentence_kind="natural"` 模式下**由逐字判据换成
#: 保真核对**（`verify_sentence_fidelity`，随 `nrules-15` 前进）。判定集变了，而且是本门**第一次
#: 放宽**——必须把话说准：`factual` / `composed` 两条老路径的第 8 条一字未改（它们的「逐字」
#: 要求原样保留），放宽只作用于**新模式**；自然改写模式**不会**因此少掉「不得新增」这一侧
#: （第 2/3 条原样适用），它换来的只是「不必逐字」，同时新增了「不得丢」（数字 / 期间 / 否定 /
#: 因果 / 实体头部名词 / 范围与强度语必须仍在声明 Claim 里）。因此同一份正文在 ng-13 下的
#: 结论**不能**移植到 ng-14：ng-13 只可能在 natural 模式上**更严**（因为它要求逐字），
#: 二者不得共用一个版本号。
#: `ng-15`（M930-3 定点业务闭环批 §二 2）：第 12 条的**判据实现一字未改**，但**判据所用的封闭
#: 标记集**随 `nrules-16` 前进（当前式措辞新增普遍化组与否定式普遍化组）。判定集变了：同一份
#: 正文在 ng-14 下通过（`A此外公司一直<B>`）、ng-15 下被拒，二者不得共用一个版本号。
#: 第 1–11 条与 a–e 六条判据一字未改——**只换判定集，不放宽**。
#: `ng-16`（M930-3 主营业务质量返修批 §四）：第 2/3 条的**判据实现一字未改**，但**判据所用的
#: 主体名抽取**随 `nrules-17` 前进（`_entity_name_run` 剥前缀改为不动点）。判定集**收窄**：
#: 同一份正文在 ng-15 下被拒（`截至报告期末公司` 被当成未授权主体名）、ng-16 下通过，
#: 二者不得共用一个版本号。真正错误的主体名照旧命中，**不放宽**。
#: `ng-17`（同批，随 `nrules-18`）：判据实现仍一字未改，判定集再收窄一格——`报告期末本公司…`
#: 这一形状不再产出伪主体名 `末本公司`（成因与正反例见 `nrules-18` 的说明）。同一份正文在
#: ng-16 下被拒、ng-17 下通过，二者不得共用一个版本号；真正错误的主体名照旧命中，**不放宽**。
#: `ng-18`（M930-3 `ndc-2` 批：引用→material 绑定的唯一收窄）：判据实现除了绑定那一步之外
#: 一字未改，但**门读到的绑定集合**变了——事实的已核验输入材料唯一时，同来源身份下的多个
#: material 候选先被它收窄（`mbind-1`）。同一份正文在 ng-17 下命中 `support_material_ambiguous`
#: （整节阻断）、在 ng-18 下得到一条具体绑定，判定集变了，二者不得共用一个版本号。
#: 收窄**不放宽**任何一条门要求：交集为空／多项／错页／跨 Pack／身份不一致仍 fail-closed。
NARRATIVE_GATE_VERSION = "ng-18"
#: **门前只出提示、门后必须由 FND/Result 判定**的 gate 规则（封闭集；「半条规则」）。
#:
#: 门只读门前候选束，看不到门后的 `FactNarrativeDisposition`，因此这些规则在门前**不可裁定**：
#: 它们只出 rework 级提示，说明该事实在门前无法呈现。真正的判定发生在门后——该事实必须在
#: FND 里 `claimed`，或（required 时）绑定一条**已存在**的显式 unresolved/block。
#:
#: 集合成员**不是**「可忽略的提示」：下游（组装器）必须逐一验证该 issue 对应的门后判定已经
#: 成立，否则仍然拒绝。把某个规则加进这里，等于把它从「组装器一律拒绝的 rework」改成
#: 「组装器逐条复核门后判定的 rework」——覆盖面不缩，只换裁定者。
POST_GATE_ADJUDICATED_GATE_RULES = ("required_fact_not_proposed",)
#: 确定性 aggregate Claim Binding Gate（P8）的规则版本。门前 proposal 与门后 accepted binding
#: 都记录它；**不得**把语义判断塞进这个机械门（§16.7.1 P8）。
#: `cbg-2`（§二 / P0）：路径 B 的授权面收紧为「非高风险描述性原子」——逐边新增一条拒绝码
#: `path_b_high_risk_surface`，判据是「候选文本里出现任何高风险表面」，与「该字面成分是否
#: 在材料正文里逐字存在」**无关**。同一份 Draft 在 cbg-1 与 cbg-2 下可能一通过一被拒，
#: 因此版本必须前进，二者不得共用一个版本号。
#: `cbg-3`（M930-3 写作主链定点批 §一）：`cbg-2` 的判据实现一字未改，但**判据所读的封闭标
#: 记集**随 `nrules-11` 前进（同一处 scanner，三个使用点共用）。实测：r7b 里 6 条候选
#: （法定代表人 / 记账本位币 / 会计期间 / 境外回款正常 / 详见 / 上市）在 cbg-2 下逐边 `pass`、
#: 在 cbg-3 下逐边 `path_b_high_risk_surface` 拒绝，同一份 Draft 的 aggregate 判定因此从 pass
#: 变 fail。判定集变了就必须换版本号，二者不得共用。
CLAIM_BINDING_GATE_VERSION = "cbg-3"
#: factual Claim 级语义核验（P9 `sections/claim_entailment_evaluator.py`）的 rubric 版本。
#: 它是 M930 current 链上 Claim 级语义核验的**唯一**所有者。
#: `cer-2`（§三）：rubric 新增「原子性先于蕴含」——候选文本含两个可分别判断真假的断言时，
#: 必须输出 `rejected` + `non_atomic_claim`，不得替它做蕴含判断。判定集变了，版本必须前进。
#: `cer-3`（M930-3 返修 ⑤）：rubric 的原子性判据拆成两支——一般语义判据不变；候选逐字镜像
#: **恰好一条**权威事实自身的文本（`authority_fact_mirror.match_count == 1`，含该事实自己
#: 的口径限定语）时不判 `non_atomic_claim`。判定集变了（同一份候选在 `cer-2` 下可能被拒、
#: 在 `cer-3` 下通过），因此版本必须前进：`ClaimEntailmentDecision.rubric_version` 参与身份，
#: 两种判定集不得共用一个版本号。
#: `cer-4`（M930-3 定点返修 r6 后 ①）：镜像读数（`authority_fact_mirror`）的比较口径从
#: **逐字相等**改成**标点归一读视图上的内容相等**（`mir-2`）。真实 run r6 的财务节 24 条
#: 候选 `match_count` 全为 `0`（写作侧契约要求候选不带句末标点），`cer-3` 的豁免一次都没
#: 生效，同一形状被判出相反结论，必需事实无 Claim 可引、整节中止。读数口径变了，同一份候选
#: 在 `cer-3` 与 `cer-4` 下会配到不同的镜像读数，因此版本必须前进，不得共用版本号。
CLAIM_ENTAILMENT_RULES_VERSION = "cer-4"
#: Writer 侧**材料处理去向**（`WriterMaterialProcessingDisposition`，P6）的规则版本。
#: 它记录「available → processed → used / not_used」三层等式所依据的规则集；Writer 自报
#: 不足以建立去向（§16.7.1 P6），故版本必须随 WMPD 一起留存并参与身份。
MATERIAL_PROCESSING_RULES_VERSION = "wmpd-1"
#: exact material manifest 的 wire 结构版本（与 narrative schema 版本解耦：manifest 是
#: Writer 消费边界自身的身份，不是 narrative 文本的版本）。
#:
#: `wmm-1`（M930-3A/3B/3C）只有身份侧：`material_id` + `content_hash`。它能证明「有这样一条
#: 材料」，**不能**证明「正文在场」——于是 P6 只到接口骨架（LLM 手里是 ID 列表）。
#: `wmm-2`（M930-3 真实写作主链收口批）把**真实 payload 引用 + exact locator + payload 哈希 +
#: 读视图指纹**钉进成员身份：没有解析并校验过正文，就构造不出合法成员。旧 `wmm-1` 对象不得被
#: `from_dict` 静默重解释（字段集不同 ⇒ 直接拒绝），只经 `load_legacy_wmm1_for_audit` 只读回放。
MATERIAL_MANIFEST_SCHEMA_VERSION = "wmm-2"
#: 只能只读回放的旧 manifest 版本（不得进入当前链，不得原位重写）。
LEGACY_MATERIAL_MANIFEST_SCHEMA_VERSIONS = ("wmm-1",)
#: §十一：表格**展示标签**（表题/列名）的封闭词表。这些词只描述版面，**不承载任何事实**：
#: 不含数字，也不含主体/期间/单位/口径/结论。表题与列名要么逐字取自授权文本池，要么逐字
#: 取自本词表；此外一律拒——写不出合规表头就不要出表格（见写作器 prompt 的硬性要求）。
#: 注意：`unit` / `period` / `entity_scope` **不适用**本词表，它们是对读者的事实声明，
#: 必须逐字落在授权文本池内。
TABLE_DISPLAY_LABELS = ("项目", "指标", "说明", "数值", "内容", "名称", "单位", "期间", "主体")
#: 组装**产物** wire 结构版本（本模块对象的字段形状）。它**不是**报告版本身份：
#: 报告版本只有唯一权威根 `sections.backbone_schema.ReportVersionIdentity`（M930-1 冻结），
#: `AssembledReport.report_version` 只能从该身份的 `content_fingerprint` 读出。
#:
#: `abr-2 → abr-3`（M930-3 任务二）：产物字段形状未变，但**校验边界**变了——读回时必须
#: 独立重算"排除自身版本字段后的规范载荷"指纹（见 `assembled_payload_body`），并核对
#: 各节 SectionDraft 身份与身份对象一致。同一份 wire 在 abr-2 / abr-3 下可接受结论不同，
#: 因此必须标明是哪个边界读出的。
REPORT_SCHEMA_VERSION = "abr-3"
#: 报告版本身份的 schema 版本（本模块**只读复用**冻结接口，不另立第二套）。
REPORT_VERSION_SCHEMA_VERSION = _BACKBONE.REPORT_VERSION_SCHEMA_VERSION

#: 内容连接语（**封闭词表**，版本化）。它们不承载任何事实、数字、实体、期间或结论，
#: 只表示行文关系；由生成器选择、由写作者按本表逐字渲染。词表之外的自由文本一律不可
#: 作为连接语（§十：LLM 只能组织，不能创造事实表面）。
CONNECTOR_VERSION = "conn-1"
CONNECTORS = (
    "此外，",
    "同时，",
    "其中，",
    "另一方面，",
    "在此基础上，",
    "综上，",
)

#: 连接语的**行文关系**分组（封闭，`conn-rel-1`）。`CONNECTORS` 是**词表**，本分组是同一个
#: 词表上的**关系语义**划分——两者不是同一件事，因此各有各的版本字面量。
#:
#: 为什么需要它：`nrules-12` 只判「接缝有没有分隔标点」，于是 `A，另一方面，B` 与
#: `A，此外，B` 在门下一模一样。但读者看到的不是同一件事——`此外` / `同时` 是**中性的并列**
#: （「还有这一条」），而 `另一方面` 断言**对照**、`在此基础上` 断言**递进/承接**、`其中` 断言
#: **从属**、`综上` 断言**由前文归纳**。四种关系都是**关于两条断言之间关系**的断言，材料里若没有
#: 说出这个关系，正文就是在替材料下一个它没有下的判断（与「因此」「说明」同属把并列升级为推理）。
#:
#: 因此分组是**封闭**的，且必须与 `CONNECTORS` 精确划分（由
#: `verify_connector_relation_partition` 在测试里复算，不靠人工同步）。
RELATION_CONNECTOR_VERSION = "conn-rel-1"
#: 断言「两条断言之间有关系」的连接语：**没有材料支持就不得使用**。
RELATION_ASSERTING_CONNECTORS = (
    "其中，",
    "另一方面，",
    "在此基础上，",
    "综上，",
)
#: 中性并列连接语：只说「还有这一条」，不宣称两条断言之间存在任何特定关系。
NEUTRAL_CONNECTORS = (
    "此外，",
    "同时，",
)


def verify_connector_relation_partition() -> None:
    """封闭分组必须与 `CONNECTORS` **精确划分**（无遗漏、无重复、无表外词）。

    唯一实现在这里，测试直接调用它：分组与词表是两份字面量，分开写必然漂移，而漂移的后果是
    「某个关系性连接语因为忘了归组而永远不被核验」——那正好是这条判据要防的事。
    """
    grouped = tuple(RELATION_ASSERTING_CONNECTORS) + tuple(NEUTRAL_CONNECTORS)
    if len(set(grouped)) != len(grouped):
        raise NarrativeSchemaError(
            f"连接语关系分组内部有重复：{sorted(grouped)}")
    if set(grouped) != set(CONNECTORS):
        raise NarrativeSchemaError(
            "连接语关系分组没有精确划分 CONNECTORS："
            f"未归组 {sorted(set(CONNECTORS) - set(grouped))}，"
            f"表外词 {sorted(set(grouped) - set(CONNECTORS))}")


def relation_connector_hits(text: Any) -> "tuple[tuple[int, str], ...]":
    """返回文本里命中的**关系性**连接语及其位置（空 tuple 表示没有）。

    与 `vague_period_hits` 同形：只做字面命中，不做语义判断。「这个关系在材料里成不成立」由
    `verify_section_narrative` 第 10 条按**声明的 Claim 文本**逐字核验，不在本函数里判断。
    """
    if not isinstance(text, str):
        return ()
    out: list[tuple[int, str]] = []
    for connector in RELATION_ASSERTING_CONNECTORS:
        start = text.find(connector)
        while start >= 0:
            out.append((start, connector))
            start = text.find(connector, start + 1)
    return tuple(sorted(out))
#: 正文不得出现的「未定义报告期」措辞（§十二 4）：硬门按**词根**判（子串命中即拒），
#: 因此裸根 `报告期` 已经覆盖 `报告期内`/`公司报告期`/`本报告期`/`报告期初`/`报告期末`
#: 全部变体，不必逐条枚举（逐条枚举反而会漏掉未枚举的写法）。
#: 这些词既未绑定期间也未绑定主体，是「用未定义指代顶替权威期间」的唯一入口。
VAGUE_PERIOD_PHRASES = ("报告期",)

#: 「**系统自己做过检索 / 核验**」这一类自述的封闭短语集（M930-3 返修 ⑥，判定集 nrules-9）。
#:
#: 为什么它必须是**封闭列举**而不是一句「不得自夸」：这句话的危险恰恰在于它**不引入任何数字、
#: 主体、期间或结论**，所以上面那几组高风险表面判据一个都抓不住它；而它对读者的作用却是把
#: 「年报里转述的一段话」升级成「系统独立核验过的事实」——读者据此会跳过回查。
#:
#: 判据与第 2/3 条同形（**逐字在场**），因此它拦的不是「转述」，而是**替系统自报**：
#: 一句话里出现下表任何一个短语时，该短语必须能在**它自己声明的 Claim 文本**里逐字找到。
#: 年报（或其他文档材料）自己写着「互联网」「核验」时，Claim 文本逐字带着它，正文照转不误；
#: 材料的 Claim 文本没写，正文却写「本系统已独立联网核验」——即拒。这与「路径 B 只授权非高风险
#: 描述性原子」不冲突：本集合**不进** `HIGH_RISK_SURFACE_MARKERS`，因此不参与写入侧的候选
#: 授权面（材料原文里的这些词不在候选阶段被判高风险），只在**正文**这一面生效。
SYSTEM_PROVENANCE_PHRASES = (
    "互联网", "联网", "检索", "抓取", "爬取", "核验", "本系统",
)


def system_provenance_hits(text: str) -> tuple[str, ...]:
    """返回文本里命中的「系统自述检索 / 核验」短语（空 tuple 表示没有）。

    与 `vague_period_hits` 同形：只做字面命中，不做语义判断。短语集与判据的唯一实现都在这里，
    正文核验（第 9 条）与验收侧独立复算各自取用不同的一份常量。
    """
    if not isinstance(text, str):
        return ()
    return tuple(p for p in SYSTEM_PROVENANCE_PHRASES if p in text)


#: **发行人自述评价语**的封闭短语集（`issuer-praise-1`，判定集 nrules-13）。
#:
#: 与 `SYSTEM_PROVENANCE_PHRASES` 同一类问题、相反方向：这些短语**逐字就在 Claim 文本里**
#: （材料原文自己写着「一流」「核心技术优势」），所以第 2/3 条的「高风险表面必须逐字来自声明的
#: Claim」对它**必然通过**——它拦不住。可它对读者的作用是把**发行人对自己的一句评价**读成
#: **授信分析者的结论**：「公司拥有核心技术优势」出自年报的「经营模式」一节，是发行人自述；
#: 正文照转而不标明出处，读者会以为这是分析判断。
#:
#: 判据因此不是「禁止这些词」（那会连材料原文一起禁掉），而是**必须有归属**：一句正文里出现
#: 下表的短语时，该短语所在的**组织段**里必须出现 `ATTRIBUTION_MARKERS` 里的至少一个短语。
#: 「据公司自身表述，公司拥有核心技术优势」合规；「公司拥有核心技术优势」即拒。
#: 与第 9 条一样**不进** `HIGH_RISK_SURFACE_MARKERS`：它不参与写入侧的候选授权面，只在正文
#: 这一面生效，因此材料原文里的这些词在候选阶段不被判高风险。
ISSUER_SELF_DESCRIPTION_PHRASES = (
    "一流", "核心优势", "核心技术优势", "前瞻性", "领先", "卓越", "优质",
)
#: 归属语的封闭集（`issuer-praise-1`）：它们只表明「这句话是发行人自己说的」，不引入任何数字、
#: 期间、主体或结论，因此组织器可以自行写出（与 `CONNECTORS` 同权：只表示行文与出处关系）。
ATTRIBUTION_MARKERS = (
    "据公司自身表述", "据公司披露", "公司自述", "公司披露", "公司称",
    "年度报告披露", "年报披露",
)


def issuer_self_description_hits(text: Any) -> tuple[str, ...]:
    """返回文本里命中的**发行人自述评价语**（空 tuple 表示没有）。只做字面命中。"""
    if not isinstance(text, str):
        return ()
    return tuple(p for p in ISSUER_SELF_DESCRIPTION_PHRASES if p in text)


def attribution_hits(text: Any) -> tuple[str, ...]:
    """返回文本里命中的**归属语**（空 tuple 表示没有）。只做字面命中。"""
    if not isinstance(text, str):
        return ()
    return tuple(p for p in ATTRIBUTION_MARKERS if p in text)


#: **当前式措辞**的封闭短语集（`curstate-1`，判定集 nrules-14）：把断言**挪到当前时点**的词。
#:
#: 与 `ISSUER_SELF_DESCRIPTION_PHRASES` 同一类：它们逐字可能是材料自己的话，但**能不能这样写**
#: 取决于这条断言由什么材料支撑（O-12：「同类材料按『较新且可核实者优先表达当前状态』」）。
#: 一条只由同类较旧材料支撑的断言，被组织语加上「目前」「仍」「持续」之后，读者读到的是
#: 「截至现在仍然如此」——那是材料没说过的一件事。判据因此与第 9/10 条同形：**组织段里**出现
#: 下表的短语时，该短语必须能在**本句自己声明的 Claim 文本**里逐字找到（材料原文自己写着
#: 「持续」照转不误；材料没写而组织语写了即拒）。
#:
#: 三条边界（与 `sections/source_role_scope.py` 的分工写在这里，避免两处各判一半）：
#:
#:   * 只扫**组织段**（`_residue_segments`）：Claim 段逐字就是 Claim 文本，那里的时点措辞来自
#:     材料自己，不由本条代言；
#:   * 与 `SRS.PERIOD_QUALIFICATION_MARKERS` **方向相反、用途互补**：那边问「候选有没有把断言
#:     锚到某个期间」（有锚才可能留下），这边问「组织语有没有把断言挪到当前」（挪了即拒）。
#:     同一批词在下表里出现、在那边**刻意**不出现，理由只有一个：它们是当前式措辞，不是期间限定；
#:   * **不进** `HIGH_RISK_SURFACE_MARKERS`：不参与写入侧的候选授权面，只在**正文**这一面生效，
#:     因此材料原文里的这些词在候选阶段不被判高风险。
CURRENT_STATE_FRAMING_MARKERS = (
    "目前", "当前", "如今", "现今", "至今", "迄今", "眼下",
    "仍然", "仍", "依旧", "持续", "现有",
    # `curstate-2`（M930-3 定点业务闭环批 §二 2）：**普遍化**措辞组。初版只收「把断言挪到
    # 当前时点」的词（目前/仍/持续），漏掉了同一件事的**另一种写法**——不是挪到「现在」，
    # 而是抹掉起点、把断言说成**从来如此**（`一直` / `始终` / `历来` / `向来` / `一向` /
    # `一贯` / `素来` / `从来`）。两者对读者的作用相同：材料说的是「某份材料披露时的情形」，
    # 读者读到的是「一向如此」。CLAUDE.md 明令的点名反例「不得写成『一直如此』」正落在这一组。
    # 判据本身一字未改（第 12 条，逐字在场），只换判定集 ⇒ 判定集变了，版本必须前进。
    # 三条边界与初版相同：只扫组织段；材料自己写着这些词时逐字豁免；**不进**
    # `HIGH_RISK_SURFACE_MARKERS`（不参与写入侧的候选授权面，只在正文这一面生效）。
    "一直", "始终", "历来", "向来", "一向", "一贯", "素来", "从来",
    # `curstate-2` 的第三条：**否定式普遍化**（`从未` / `未曾`）。它们比肯定式更危险——把一个
    # 「未发生」的观察写成「从未发生」，同时扩大了否定本身的范围，而第 2/3 条只按字面看否定词
    # （`未发生` 在 `从未发生过` 里逐字在场，因此那一侧必然通过）。
    "从未", "未曾",
)


def current_state_framing_hits(text: Any) -> tuple[str, ...]:
    """返回文本里命中的**当前式措辞**（空 tuple 表示没有）。只做字面命中，不做语义判断。"""
    if not isinstance(text, str):
        return ()
    return tuple(p for p in CURRENT_STATE_FRAMING_MARKERS if p in text)


def unqualified_current_state_framing(text: str, claim_texts: "Sequence[str]"
                                      ) -> "tuple[tuple[str, str], ...]":
    """第 12 条的**唯一**定位实现：返回组织段里**没有材料支持**的当前式措辞 `(措辞, 组织段)`。

    「有材料支持」的口径与第 9/10 条同形、同理由——**逐字在场**：该措辞必须能在本句自己声明的
    Claim 文本里逐字找到（材料自己把这件事写成了当前状态，照转不误）。材料没写而组织语写了，
    等于替这条断言换了一个时点——O-12 要的「旧材料不得被静默写成当前状态」在**组织**这一侧就落
    在这里：换时点与加事实表面是同一类越权，只是它更隐蔽（不动一个字面事实）。

    跨度定位不成立时返回空（那种句子已在判据 a 被拒）。与第 10 条同一分段口径。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (claim_texts or ()))
    spans = _claim_spans(body, texts)
    if spans is None:
        return ()
    supported = "\n".join(texts)
    out: list[tuple[str, str]] = []
    for segment in _residue_segments(body, spans):
        for marker in current_state_framing_hits(segment):
            if marker not in supported and (marker, segment) not in out:
                out.append((marker, segment))
    return tuple(out)


#: ---------------------------------------------------------------------------
#: 最终句保真（`natfid-1`，判定集 `ng-14`；M930-3 指令 E 第 3 项）
#: ---------------------------------------------------------------------------
#:
#: **为什么需要它。** 自然改写模式（`sentence_kind="natural"`）下，句子不再逐字等于它声明的
#: Claim 文本，于是「Claim 逐字出现」这条机械判据（第 8 条）在这一模式下**不可用**；而它原本
#: 免费提供的保障——「读者读到的每个断言都在已审 Claim 的射程内」——必须由别的东西接管。
#:
#: **它接管什么、不接管什么（不许含糊）。** 它只在**正文**这一侧工作：读最终句文本与它声明的
#: 已接受 Claim 集（外加合法引用与已接受 context 绑定 id），核对**改写**有没有越过七条轴
#: （主体 / 数量 / 期间 / 否定 / 范围 / 因果 / 结论），然后给出一份**只读** typed 报告。
#: 它**不**判断「这条 Claim 是不是蕴含材料」——那是 Claim 级蕴含门（`cer-*`）的职责，
#: 它**不**读材料正文，**不**写任何对象，**不**产生任何决定身份。
#:
#: **两个方向，强度不对称（这是设计，不是疏漏）。**
#:
#:   * **不得新增**：句子里出现的高风险表面必须逐字来自它自己声明的 Claim 文本——这一条
#:     **复用**既有的 `unauthorized_surfaces`（第 2/3 条判据），一个字都没有重写。
#:   * **不得丢**：声明 Claim 里**断言关键**的表面（数字 / 含糊期间 / 否定与状态标记 /
#:     因果关系 / 实体头部名词，以及下面两张补语表里的范围与强度语）必须**逐字仍在**句子里。
#:     逐字事实句与组合句**不需要**这一条（它们的文本逐字包含 Claim 文本，丢字在结构上不可能），
#:     因此它是自然改写模式**独有**的新判据——「把 `未发生` 写成 `发生`」「把 `129,641,258 千元`
#:     写成 `12.96 亿元`」这类改写，第 2/3 条只拦得住**添**，拦不住**减**。
#:
#: **为什么补两张表。** 因果词在 `HIGH_RISK_SURFACE_MARKERS` 里**只覆盖了一部分**
#: （`因此`/`因而`/`导致`/`从而`/`说明`/`表明`/`反映` 在表内，`使得`/`由于`/`因为`/`可见`/
#: `意味着`/`证明`/`体现` 不在），范围与结论强度语**一条都没有**。自然改写恰恰最容易在这三处
#: 升级：把并列写成因果、把「部分」写成「全部」、把「下降」写成「显著下降」。因此补语是**新增
#: 的封闭表**，不是「放松」：它们与既有表面判据**同向**（都必须逐字来自声明 Claim）。
CAUSAL_ASSERTION_MARKERS = (
    "因此", "因而", "故", "于是", "导致", "使得", "从而", "由于", "因为",
    "说明", "表明", "可见", "意味着", "证明", "印证", "反映", "体现",
)
SCOPE_QUALIFIER_MARKERS = (
    "全部", "所有", "任何", "之一", "部分", "多数", "少数", "主要", "均为", "均", "各", "仅",
)
CONCLUSION_STRENGTH_MARKERS = (
    "显著", "明显", "大幅", "进一步", "完全", "唯一", "首次", "充分", "极大", "根本", "必然",
)
#: 三张补语表的**内容指纹口径**版本。表变了（增删任何一个词）即必须换号：同一份正文在两版
#: 词表下可能一个通过、一个被拒。
FINAL_SENTENCE_FIDELITY_VERSION = "natfid-1"
#: 最终句保真的**封闭缺陷码**（typed；一条判据一个码，不得塞进泛化字符串）。
SENTENCE_FIDELITY_DEFECTS = (
    # 句子里有、声明 Claim 里没有的高风险表面（复用第 2/3 条判据）。
    "unauthorized_surface",
    # 声明 Claim 里有、句子里没有的断言关键表面（本判据**独有**的方向）。
    "dropped_assertion_surface",
    # 句子里的范围 / 结论强度 / 因果补语不在任何声明 Claim 文本里。
    "added_scope_or_strength",
    # 句子声明的引用不是它自己声明 Claim 派生出来的。
    "stray_citation",
)
#: 只参与「不得丢」方向、**不**参与「不得新增」方向的成员长度阈值。
#:
#: 单字成员（`故` / `均` / `各` / `仅`）作为**子串**会大量误伤：`平均` / `不仅` / `故事` /
#: `各地` 都不是范围或因果断言，却都含这些字。它们在「不得新增」一侧的收益（挡住一个单字副词）
#: 远小于代价（把合规的自然改写判成越权，正好毁掉本模式存在的意义）。而「不得丢」一侧不受影响：
#: 那些字是**从声明 Claim 文本里读出来的**，位置由材料决定，不是系统猜的。
FIDELITY_SINGLE_CHAR_MARKERS = ("故", "均", "各", "仅")


def _fidelity_norm(text: Any) -> str:
    """保真比对的归一：**只**去空白（含全角空格与制表符/换行），不动标点、不做同义改写。

    「只去空白」是一条硬边界：再多一步（同义、繁简、标点镜像）就会把「改写」判成「一致」，
    而本判据的全部价值就在于它**只**认逐字在场。
    """
    if not isinstance(text, str):
        return ""
    out: list[str] = []
    for ch in text:
        if ch.isspace() or ch == "　":
            continue
        out.append(ch)
    return "".join(out)


def _fidelity_marker_hits(text: Any, markers: "Sequence[str]") -> tuple[str, ...]:
    return tuple(m for m in markers if m in str(text or ""))


#: 主体轴的**可核验替身**：实体表面里**必须仍在句中出现**的头部名词（**封闭表**）。
#:
#: **为什么不直接要求整条实体表面逐字留。** `entity_name_tokens` 会把跨词边界的片段粘成一条
#: 「实体」——实测 `entity_name_tokens("2024年度公司主营业务收入…")` 得 `('年度公司',)`，把尾字
#: `年` 与 `度公司` 粘在了一起。在**授权**方向（第 2/3 条）这种过度包含是保守的：它只多要求一份
#: 授权，最坏结果是把合法句子判得**更严**。在**保真**方向它却是反向的致命伤：`年度公司` 会被
#: 当成「丢了的断言表面」，于是 `公司2024年度主营业务收入…` 这种完全合规的自然改写被判越权——
#: 正好毁掉本模式存在的意义。因此这里改核**头部名词**：主体轴真正要守的是「这句话还在说一家
#: 公司/集团/银行」，而不是「字的先后次序一个不差」。
#:
#: **这条替身覆盖不到什么（不许含糊）。** 它拦不住「具体主体 → 泛指」的概化（含全称的法人名写成
#: `该公司`）。**「新增」方向也拦不住它**——实测（`natfid-1` 批次夹具，公司无关的通用反例）：
#: 声明 Claim 为 `示例新能源科技股份有限公司2024年营业收入为100亿元。`、句子写成
#: `该公司2024年营业收入为100亿元。` 时，`verify_sentence_fidelity` 的 `ok` 仍为真、四组读数全空。
#: 原因在 `entity_name_tokens` 自己登记的边界：指代式主体（`该公司`）的字号不足 2 个汉字，回扫
#: 自然落空，于是它根本不进高风险表面集，既不会被判「添」也不会被判「丢」。
#: 这条边界由 **Claim 级蕴含门**（`cer-*`：改写后的句子文本要重新过蕴含）与**人工逐句读回**承担，
#: 本核验**不**声称覆盖它，也不得据此称「主体轴已完整」。
ENTITY_HEAD_NOUNS = (
    "公司", "集团", "股份", "银行", "研究院", "研究所", "事业部", "中心", "工厂", "分行", "支行", "厂",
)


def entity_head_nouns(text: Any) -> tuple[str, ...]:
    """`text` 里实体表面所带的**头部名词**（按实体切分再取头）。

    切分用的是**不裁剪时间状语字号**的那一版（`_entity_tokens(drop_temporal_runs=False)`）：
    本函数的用途是保真轴的**保守替身**，多认一个 token 只会让它更保守，而少认一个会少一格读数。
    因此它**不**继承 `entity_name_tokens` 的授权方向裁剪——两件事各自读各自的。
    """
    out: list[str] = []
    for token in _entity_tokens(str(text or ""), drop_temporal_runs=False):
        head = ""
        for noun in ENTITY_HEAD_NOUNS:
            if token.endswith(noun) and len(noun) > len(head):
                head = noun
        if head and head not in out:
            out.append(head)
    return tuple(out)


def assertion_critical_surfaces(claim_text: Any) -> tuple[str, ...]:
    """一条 Claim 文本里**断言关键**的表面（**唯一**口径，按首次出现序去重）。

    = 数字 / 否定与状态标记 / 含糊期间（`high_risk_surface_tokens` 的三条，**不含**实体整串）
      ∪ 实体头部名词（`entity_head_nouns`，见上面的表说明）
      ∪ 范围补语 ∪ 结论强度补语。

    自然改写后这些表面**必须逐字仍在**句子里；少一个就换了一次断言（期间、口径、范围或强度）。
    实体整串**故意不在此列**：主体轴的「不得概化」一侧不由本核验承担（见 `ENTITY_HEAD_NOUNS`）。
    """
    body = str(claim_text or "")
    surfaces: list[str] = list(scan_numeric_tokens(body))
    surfaces += list(marker_hits(body))
    surfaces += list(vague_period_hits(body))
    surfaces += list(entity_head_nouns(body))
    surfaces += list(_fidelity_marker_hits(body, SCOPE_QUALIFIER_MARKERS))
    surfaces += list(_fidelity_marker_hits(body, CONCLUSION_STRENGTH_MARKERS))
    out: list[str] = []
    for token in surfaces:
        if token and token not in out:
            out.append(token)
    return tuple(out)


def sentence_fidelity_digest(text: Any, claim_ids: "Sequence[str]" = (),
                             claim_texts: "Sequence[str]" = (),
                             accepted_binding_ids: "Sequence[str]" = ()) -> str:
    """保真结论的**内容指纹**：句子文本 + 声明的 Claim 身份与文本 + 已接受绑定 id。

    §三 C 的硬要求「句子文本与绑定共用一个内容身份，任一改动都强制重新校验」在这里的具体做法：
    指纹只由这四样东西算出，因此**改了句文本、换了绑定集合、或换了某条 Claim 的文本**，
    算出来的指纹都会变，任何**旧**的保真结论都对不上新指纹——「结论过期」在结构上无从伪装成
    「结论仍然有效」。
    """
    body = {
        "schema_version": FINAL_SENTENCE_FIDELITY_VERSION,
        "text": _fidelity_norm(text),
        "claim_ids": [str(x) for x in (claim_ids or ())],
        "claim_texts": [_fidelity_norm(t) for t in (claim_texts or ())],
        "accepted_binding_ids": [str(x) for x in (accepted_binding_ids or ())],
    }
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SentenceFidelityReport:
    """最终句保真的**只读** typed 结果（`natfid-1`）。它不是一个决定，也不参与任何身份。

    调用方（门 / 组装器 / 读回核对）只读 `ok` / `defects()` / 四个明细字段，并且**必须**用
    `fidelity_digest` 判断这份结论是不是还对得上当前的正文与绑定。
    """

    sentence_text_sha256: str
    claim_ids: tuple[str, ...]
    claim_texts_sha256: str
    accepted_binding_ids: tuple[str, ...]
    added_surfaces: tuple[str, ...]
    dropped_surfaces: tuple[tuple[str, str], ...]
    added_scope_or_strength: tuple[tuple[str, str], ...]
    stray_citations: tuple[str, ...]
    fidelity_digest: str

    @property
    def ok(self) -> bool:
        return not (self.added_surfaces or self.dropped_surfaces
                    or self.added_scope_or_strength or self.stray_citations)

    def defects(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.added_surfaces:
            out.append("unauthorized_surface")
        if self.dropped_surfaces:
            out.append("dropped_assertion_surface")
        if self.added_scope_or_strength:
            out.append("added_scope_or_strength")
        if self.stray_citations:
            out.append("stray_citation")
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "schema_version": FINAL_SENTENCE_FIDELITY_VERSION,
            "sentence_text_sha256": self.sentence_text_sha256,
            "claim_ids": list(self.claim_ids),
            "claim_texts_sha256": self.claim_texts_sha256,
            "accepted_binding_ids": list(self.accepted_binding_ids),
            "added_surfaces": list(self.added_surfaces),
            "dropped_surfaces": [list(x) for x in self.dropped_surfaces],
            "added_scope_or_strength": [list(x) for x in self.added_scope_or_strength],
            "stray_citations": list(self.stray_citations),
            "fidelity_digest": self.fidelity_digest,
            "ok": self.ok,
            "defects": list(self.defects()),
        }


def verify_sentence_fidelity(*, text: Any, claims: Any, citation_ids: "Sequence[str]" = (),
                             claim_citations: Any = None,
                             accepted_binding_ids: "Sequence[str]" = (),
                             ) -> SentenceFidelityReport:
    """**只读**核对「一条最终句是怎么把已审原子写进句子的」（`natfid-1`，`ng-14`）。

    七条轴逐条落到下面的四组读数上（对应关系写在每组上，不另立说法）：

    | 轴 | 读数 | 方向 |
    |---|---|---|
    | 主体 / 数量 / 期间 / 否定 / 表格关系 / 因果 | `added_surfaces`（复用第 2/3 条） | 不得**添** |
    | 主体 / 数量 / 期间 / 否定 / 表格关系 / 因果 | `dropped_surfaces` | 不得**丢** |
    | 范围 / 结论 | `added_scope_or_strength`（含因果补语） | 不得**添** |
    | 范围 / 结论 | `dropped_surfaces`（Claim 自己的范围/强度语必须仍在） | 不得**丢** |
    | 引用 | `stray_citations` | 只能来自声明 Claim |

    `claims` 可以是「有 `claim_id` / `text` 属性的对象序列」，也可以是 `claim_id -> text` 的映射
    （与 `verify_section_narrative` 同一收法：两处对「什么算声明 Claim」的口径不允许分叉）。

    `claim_citations` 是引用轴的**唯一**输入，两种收法（都走 `claim_citation_ids` 这一份实现）：
    映射（`claim_id -> citation_ids`）或「有 `citation_refs` 的 Claim 对象序列」。**不传时这一轴
    不核**，`stray_citations` 恒为空——两个理由，都不是省事：映射形的 `claims` 没有可派生的对象；
    而门内那条唯一的调用路径（`_verify_natural_sentence_fidelity`）刻意不重复第 6 条，
    那里也不要这一轴。因此「不传」是**没做这一轴**，不是「这一轴通过了」——按 `stray_citations`
    为空就断言引用合法会读错这份报告，请用 `ok` 与 `defects()` 判断。

    本函数**不抛**：它只返回报告。要不要因此拒绝，是调用方的决定（门侧 fail-closed）。
    """
    body = str(text or "")
    pairs: list[tuple[str, str]] = []
    if isinstance(claims, Mapping):
        pairs = [(str(k), str(v)) for k, v in claims.items()]
    else:
        for claim in (claims or ()):
            cid = str(getattr(claim, "claim_id", "") or "")
            pairs.append((cid, str(getattr(claim, "text", "") or "")))
    claim_ids = tuple(cid for cid, _ in pairs)
    claim_texts = tuple(t for _, t in pairs)
    supported = "\n".join(claim_texts)

    added = unauthorized_surfaces(body, claim_texts)

    dropped: list[tuple[str, str]] = []
    for cid, ctext in pairs:
        for surface in assertion_critical_surfaces(ctext):
            if _fidelity_norm(surface) not in _fidelity_norm(body):
                dropped.append((cid, surface))

    normalized_support = _fidelity_norm(supported)
    extra_markers: list[tuple[str, str]] = []
    for markers in (CAUSAL_ASSERTION_MARKERS, SCOPE_QUALIFIER_MARKERS,
                    CONCLUSION_STRENGTH_MARKERS):
        for marker in _fidelity_marker_hits(body, markers):
            if len(marker) < 2 and marker in FIDELITY_SINGLE_CHAR_MARKERS:
                continue
            if _fidelity_norm(marker) in normalized_support:
                continue
            extra_markers.append((marker, body))

    stray: list[str] = []
    if claim_citations is not None:
        derivation = claim_citations
        if isinstance(derivation, Mapping):
            allowed: set[str] = set()
            for cid in claim_ids:
                allowed.update(str(x) for x in (derivation.get(cid) or ()))
        else:
            allowed = set()
            for claim in derivation:
                allowed.update(str(x) for x in claim_citation_ids(claim))
        stray = sorted({str(x) for x in (citation_ids or ())} - allowed)

    return SentenceFidelityReport(
        sentence_text_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        claim_ids=claim_ids,
        claim_texts_sha256=hashlib.sha256(supported.encode("utf-8")).hexdigest(),
        accepted_binding_ids=tuple(str(x) for x in (accepted_binding_ids or ())),
        added_surfaces=tuple(added),
        dropped_surfaces=tuple(dropped),
        added_scope_or_strength=tuple(extra_markers),
        stray_citations=tuple(stray),
        fidelity_digest=sentence_fidelity_digest(body, claim_ids, claim_texts,
                                                 accepted_binding_ids))


#: ---------------------------------------------------------------------------
#: 最终句语义门 B（`nsfid-1`）：逐**事实原子**的版本化核验决定
#: ---------------------------------------------------------------------------
#:
#: **为什么需要它（与 `natfid-1` 的分工，不许含糊）。** `natfid-1` 是**表面**比较器：它把最终句
#: 与声明的 Claim 文本做逐字对照，只回答「有没有多字、有没有少字」。它对三类越权**两方向都
#: 没有读数**（实测放行，缺陷码 `[]`）：把「变动为」写成「变动**约**为」（范围）、把「资产
#: 负债率」写成「**有息**负债率」（主体/指标）、把「代理口径（PROXY_FINANCE_EXPENSES）」整段
#: 删掉（限定）。这三处都不是「多字/少字」，而是**同一个位置换了一个断言**。本门因此不看表面
#: 是否在场，而是逐**事实原子**记录「读者读到的这条断言，由哪条已接受 Claim 声明、由哪条
#: factual 支撑边承载」。
#:
#: **范围（2026-09-27 勘误，指令 D 第四项）。** 本门覆盖**所有承载事实的最终句**，`natural` 与
#: `composed` **一视同仁**。`composed` 句的「文本恒等」只回答「声明的 Claim 文本是否按序逐字
#: 在句子里」，它**不回答**连接语是否新增了因果、时间、范围或结论——那正是本门要挡的越权。
#: 因此本门**不得**因 Claim 原文逐字在场而放行 `composed`。
#:
#: **方向。** 决定**只引用** `sentence_id`（`sentence_ids` 是覆盖面的内容身份）；
#: `NarrativeSentence` / `SectionNarrative` **不得**引用决定（§七：草稿不得靠引用未来的决定
#: 证明自己合法）。反向边在本模块**无处可写**。
#:
#: **cardinality。** 每个 draft revision 的最终句集**恰好一个** aggregate 决定（与
#: `ClaimBindingDecision` 同一纪律）：缺失 / `rejected` / 身份过期三态各自阻断定稿。
FINAL_SENTENCE_DECISION_SCHEMA_VERSION = "nsfid-1"
#: 规则版本（`rubric_version` 的唯一缺省值）。判定面是它的规则所有者，只 re-export，避免两处漂移。
FINAL_SENTENCE_RULES_VERSION = "nsfr-1"
#: aggregate 决定的封闭结果集。
FINAL_SENTENCE_DECISION_VERDICTS = ("entailed", "rejected")
#: 逐原子核验的封闭结果集。`atom_not_located` 的分母不是「模型没找到」，而是「这条表面在
#: 已接受 Claim 集里**没有位置**」——即读者读到了一条没有任何已接受 Claim 声明的断言成分。
FINAL_SENTENCE_ATOM_VERDICTS = ("entailed", "not_entailed", "atom_not_located")
#: 事实原子的**封闭类别**（与 §11.5.4 的七条轴一一对应，不另立说法）。
FINAL_SENTENCE_ATOM_KINDS = (
    "numeric",            # 数值（金额、比率、计数）
    "period",             # 期间（年份 / 期末 / 时点 / 含糊期间）
    "negation_or_state",  # 否定与状态（未发生 / 尚未 / 已终止）
    "entity_head",        # 实体头部（公司 / 集团 / 银行…）
    # 主体或指标名（`资产负债率` → `有息负债率`、`营业收入` → `主营业务收入`）。
    # **只有判定面会产出这一类**，机械定位器**不产**：认指标名需要一份指标词表，而词表化指标名
    # 正是「写死答案关键词」，明令禁止（见 `sections/final_sentence_fidelity.py` 的定位器说明）。
    # 它必须留在封闭类别里，否则「主体或指标被换掉」这条最隐蔽的越权在逐原子读数里无处可写：
    # 换掉指标名既不改变数字、也不改变期间、也没有任何否定或范围词。
    "subject_or_metric",
    "scope_or_strength",  # 范围与结论强度（部分 / 全部 / 显著 / 唯一）
    "table_relation",     # 表格关系（行头—列头—数值的对应关系）
)
#: 逐原子失败 / 无法定位的**封闭**原因码（typed；一条判据一个码，不得塞进泛化字符串）。
FINAL_SENTENCE_ATOM_REASON_CODES = (
    # `atom_not_located` 唯一允许的原因码：句子里有这条表面，已接受 Claim 集里没有它。
    "atom_absent_from_claims",
    # 以下七个只允许出现在 `not_entailed` 上。
    "subject_or_metric_replaced",       # 主体或指标被替换（资产负债率 → 有息负债率）
    "period_changed",                   # 期间被换（2024 → 2025、期末 → 时点）
    "negation_or_state_changed",        # 否定或状态被换（未发生 → 发生）
    "scope_or_strength_changed",        # 范围或结论强度被换（部分 → 全部、下降 → 显著下降）
    "proxy_qualifier_dropped",          # 代理口径限定语被丢（PROXY_* 标注）
    "unsupported_by_accepted_bindings", # Claim 声明了，但它自己的 factual 支撑边不承载
    "support_binding_missing",          # 声明该原子的 Claim 在本节没有 factual 支撑边
)
#: aggregate `rejected` 的封闭原因码（逐原子码已在 `atoms[]` 里逐条给出，这里只给聚合档）。
FINAL_SENTENCE_DECISION_REJECTION_REASONS = ("atom_not_entailed", "atom_not_located")

#: **定稿路径**上的 typed block 原因码（封闭集合，§12.4.4 第 4 步）。
#:
#: 它们**不是**模型的判决：模型的判决只有 `FINAL_SENTENCE_DECISION_VERDICTS` 两个取值。这里回答
#: 的是另一个问题——「本节的正文有没有**一份有效的**最终句决定」。两者分开记账：把「没有决定」
#: 写成一条 `rejected`（或反过来把 `rejected` 写成「漏审」）都是伪造证据，所以它们是各自独立的码。
#:
#: 这四个码只出现在**门后**产生的 `SectionUnresolved` 上（`blocking_effects=("SECTION_BLOCKED",)`），
#: 与权威派生的缺口（`unresolved_projections`）分属不同来源：后者**先于** Draft 形成、必须逐条在
#: Draft 里登记；本组 block **后于** Draft 形成（它核验的是最终句，而最终句在两道门之后才成形），
#: 因此 Draft 里不可能有它的投影。任何「Resolved 缺口集 == Draft 缺口集」的判据都必须按这一条
#: 收窄为「Result 缺口 = Draft 缺口 ∪ 至多一条由 `final_sentence_gate_state` 重算出的 block」。
FINAL_SENTENCE_BLOCK_REASONS = (
    # 有承载事实的最终句，却没有（有效的）决定——含调用失败 / 输出不可解析而**未形成**决定的情形。
    # 这一档**不**冒充语义判决：它只说「本节的正文未经最终句语义核验」。
    "final_sentence_decision_missing",
    # 同一 draft revision 多于一条决定（cardinality 被破坏）。两个决定并存时无法判断「以哪一份为准」，
    # 取其一都是把一份决定捧成有效结论，因此整档阻断。
    "final_sentence_decision_duplicate",
    # 决定存在、身份未过期，但聚合 verdict=`rejected`（逐原子原因码在 `atoms[]` 里逐条给出）。
    "final_sentence_decision_rejected",
    # 决定的锚点（draft_id / section_id / draft_revision / narrative_id）、覆盖面（`sentence_ids`）
    # 或支撑集指纹与**当前**正文不符：改句、改 Claim、换绑定都会落进这一档。
    "final_sentence_decision_stale",
)


def fact_bearing_sentence_ids(narrative: "SectionNarrative") -> tuple[str, ...]:
    """**承载事实**的最终句（有序、去重）：声明了至少一条 Claim 的句子。

    `transition` 句不承载事实（构造期已禁止它携带 Claim / 引用 / context 绑定），因此不在
    覆盖范围内；除此之外**一律**在内——`composed` 与 `natural` 都不得因「Claim 逐字在场」被划出
    覆盖范围（指令 D 第四项的范围勘误）。

    覆盖面按**集合相等**判（决定声明的 `sentence_ids` 必须逐位等于本函数的结果），所以「漏审
    一句」在结构上不是「少一条读数」，而是身份对不上。

    **这条覆盖到哪儿为止（如实写，不含糊）。** 覆盖面是**段落里的最终句**——`NarrativeSentence`
    只存在于段落中。**表格行不带句子身份**（`NarrativeTableRow` 没有 `sentence_id`，它的身份是
    行内容寻址的 `row_id`），因此表格单元格里的数字**不**经本门逐原子核验；它们仍受
    `natfid-1` 的表面比对、表格行自身「非空单元格与 Claim 逐位同数」的构造约束与 Claim 级蕴含门
    保护。把行 id 塞进 `sentence_ids` 会把两种身份混成一个集合，本门**不做**——这条边界因此
    作为**已知缺口**如实报告，不当作已覆盖。
    """
    out: list[str] = []
    for unit in narrative.paragraphs:
        for sentence in unit.sentences:
            if not getattr(sentence, "claim_ids", ()):
                continue
            sid = str(getattr(sentence, "sentence_id", "") or "")
            if sid and sid not in out:
                out.append(sid)
    return tuple(out)


def gate_claim_surface(narrative: "SectionNarrative",
                       claims: "Sequence[Any]") -> tuple[Any, ...]:
    """最终句语义门的**授权面**：承载事实的最终句实际声明的那些已定稿 Claim（有序、去重）。

    **「所有已接受 Claim 都必须出现在正文」不是本门的要求**（指令 D 第三项：门后正文以草稿
    与材料语境为底稿，只保留已获授权的事实，未被写进正文的 Claim 由 `FactNarrativeDisposition`
    的去向账负责）。门后协调器拿到的 `claims` 是**本节全部**已定稿 Claim，而本门核的是**正文**：
    两者不是同一个集合，所以授权面必须在这里收窄一次，而不是让每个调用方各自记得先筛一遍。

    收窄**只朝一个方向**：句子声明的 Claim 若不在给定的 `claims` 里，这里**跳过**、不悄悄补上
    ——「正文引用了一条本束没有的 Claim」由 `SentenceFidelityBundle._assert_claim_coverage` 的
    缺项方向照旧拒（fail-closed）。顺序取**首次出现**序：它只由正文决定，因此三处调用方（定稿
    协调器 / 组装器 / Store 读回）在同一份正文上必然算出同一个面，与传入 `claims` 的排列无关。
    """
    declared = {str(claim.claim_id): claim for claim in (claims or ())}
    out: list[Any] = []
    seen: set[str] = set()
    for unit in narrative.paragraphs:
        for sentence in unit.sentences:
            for claim_id in getattr(sentence, "claim_ids", ()) or ():
                cid = str(claim_id)
                if cid in seen:
                    continue
                seen.add(cid)
                claim = declared.get(cid)
                if claim is not None:
                    out.append(claim)
    return tuple(out)


def sentence_support_set_digest(*, draft_id: str, draft_revision: str, narrative_id: str,
                                sentence_ids: "Sequence[str]",
                                accepted_binding_ids: "Sequence[str]") -> str:
    """本门 aggregate 决定的**内容指纹**：其唯一输入就是它覆盖的正文与支撑边。

    与 `ClaimBindingDecision.support_set_digest` 同一纪律（内容寻址、可确定性重算）：只由
    「哪一份正文（draft/revision/narrative）+ 哪些句子 + 哪些已接受 factual 支撑边」算出。
    因此换句子、换绑定、或换一份正文，指纹都变——旧决定对不上新指纹，「结论过期」在结构上
    无从伪装成「仍然有效」。
    """
    body = {
        "schema_version": FINAL_SENTENCE_DECISION_SCHEMA_VERSION,
        "draft_id": str(draft_id or ""), "draft_revision": str(draft_revision or ""),
        "narrative_id": str(narrative_id or ""),
        "sentence_ids": [str(x) for x in (sentence_ids or ())],
        "accepted_binding_ids": sorted(str(x) for x in (accepted_binding_ids or ())),
    }
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SentenceAtomReading:
    """最终句里**一个事实原子**的核验读数（`nsfid-1` 的逐行载荷）。

    一行必须同时给出三样东西，缺一就不是可回查的核验：它在句子里长什么样（`atom_surface`）、
    它由哪条**已接受 Claim** 声明（`claim_id`）、那条 Claim 的 **factual** 支撑边
    （`accepted_binding_ids`）。`atom_not_located` 是唯一允许 `claim_id` 为空的档——它的含义
    恰恰是「没有一条已接受 Claim 声明这条表面」。

    `accepted_binding_ids` 只收 **factual** 边：context 边不授权事实，把它写进原子行会让
    「这条断言由材料承载」这句话失去含义（与 `ClaimEntailmentDecision` 同一口径）。

    **`atom_surface` 的来源按 verdict 分两种**（两个方向都必须能被写下，否则「丢」的那一侧
    在 wire 层无处表达）：

    * 原子**在最终句里**（`entailed`；`atom_not_located`；`not_entailed` 里被**换掉/升级**的
      那些）——写它在**最终句**里逐字出现的那几个字。
    * 原子被**丢掉**（`not_entailed` 且原因是口径限定语被删、否定被抹、期间被省）——写它在
      **声明 Claim** 里的原样字。那时最终句里本来就没有这几个字，硬要写「句子里出现的字」
      只能写成别的东西，等于把「丢了」伪装成「换了」。
    """

    sentence_id: str
    atom_kind: str
    atom_surface: str
    claim_id: str | None
    accepted_binding_ids: tuple[str, ...]
    verdict: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.sentence_id, "SentenceAtomReading.sentence_id")
        _require_enum(self.atom_kind, FINAL_SENTENCE_ATOM_KINDS, "SentenceAtomReading.atom_kind")
        _require_nonempty(self.atom_surface, "SentenceAtomReading.atom_surface")
        _require_enum(self.verdict, FINAL_SENTENCE_ATOM_VERDICTS, "SentenceAtomReading.verdict")
        object.__setattr__(self, "accepted_binding_ids", _str_tuple(
            self.accepted_binding_ids, "SentenceAtomReading.accepted_binding_ids"))
        if len(set(self.accepted_binding_ids)) != len(self.accepted_binding_ids):
            raise NarrativeSchemaError(
                "SentenceAtomReading.accepted_binding_ids 含重复（支撑集是集合）")
        if self.verdict == "entailed":
            _require_nonempty(self.claim_id, "SentenceAtomReading.claim_id")
            if not self.accepted_binding_ids:
                raise NarrativeSchemaError(
                    f"原子 {self.atom_surface!r} 判为 entailed，却没有引任何 factual 支撑边："
                    "没有支撑边的原子不构成已核验的映射")
            if self.reason_code is not None:
                raise NarrativeSchemaError("verdict=entailed 不得携带 reason_code")
        elif self.verdict == "not_entailed":
            _require_nonempty(self.claim_id, "SentenceAtomReading.claim_id")
            _require_enum(self.reason_code, FINAL_SENTENCE_ATOM_REASON_CODES,
                          "SentenceAtomReading.reason_code")
            if self.reason_code == "atom_absent_from_claims":
                raise NarrativeSchemaError(
                    "reason_code=atom_absent_from_claims 只属于 atom_not_located："
                    "既然声明了 claim_id，这条表面就不是「没有位置」")
        else:
            if self.claim_id is not None:
                raise NarrativeSchemaError(
                    "verdict=atom_not_located 不得携带 claim_id：这一档的含义正是"
                    "「没有一条已接受 Claim 声明这条表面」")
            if self.accepted_binding_ids:
                raise NarrativeSchemaError(
                    "verdict=atom_not_located 不得引支撑边：没有声明它的 Claim，就没有承载它的边")
            if self.reason_code != "atom_absent_from_claims":
                raise NarrativeSchemaError(
                    "verdict=atom_not_located 的 reason_code 必须是 "
                    "'atom_absent_from_claims'（这一档只有这一个原因）")

    def to_dict(self) -> dict:
        return {
            "sentence_id": self.sentence_id, "atom_kind": self.atom_kind,
            "atom_surface": self.atom_surface, "claim_id": self.claim_id,
            "accepted_binding_ids": list(self.accepted_binding_ids),
            "verdict": self.verdict, "reason_code": self.reason_code,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SentenceAtomReading":
        d = _reject_unknown(d, {"sentence_id", "atom_kind", "atom_surface", "claim_id",
                                "accepted_binding_ids", "verdict", "reason_code"},
                            "SentenceAtomReading")
        return cls(
            sentence_id=_require_nonempty(d.get("sentence_id"), "SentenceAtomReading.sentence_id"),
            atom_kind=_require_nonempty(d.get("atom_kind"), "SentenceAtomReading.atom_kind"),
            atom_surface=_require_nonempty(d.get("atom_surface"),
                                           "SentenceAtomReading.atom_surface"),
            claim_id=d.get("claim_id"),
            accepted_binding_ids=_str_tuple(d.get("accepted_binding_ids") or (),
                                            "SentenceAtomReading.accepted_binding_ids"),
            verdict=_require_nonempty(d.get("verdict"), "SentenceAtomReading.verdict"),
            reason_code=d.get("reason_code"))


@dataclass(frozen=True)
class FinalSentenceFidelityDecision:
    """一个 draft revision 的最终句集**唯一**的逐原子语义决定（`nsfid-1`）。

    与 `ClaimEntailmentDecision` 同族（同版本化 + 同 `call_id` 记账 + 同 typed 拒绝码纪律），
    但**对象不是 Claim 而是句子**：它绑定的覆盖面是 `sentence_ids`（最终句的**有序**集合），
    过期锚点是 `draft_id` / `section_id` / `draft_revision` / `narrative_id`。

    本类**没有**任何字段能指回 Claim / Draft 之外的对象：方向是「决定 → 句子」，因此
    §七「草稿不得靠引用未来的决定证明自己合法」在类型层就不可违反。
    """

    final_sentence_fidelity_decision_id: str
    schema_version: str
    task_id: str
    section_id: str
    draft_id: str
    draft_revision: str
    narrative_id: str
    sentence_ids: tuple[str, ...]
    atoms: tuple[SentenceAtomReading, ...]
    support_set_digest: str
    rubric_version: str
    prompt_version: str
    model_policy: str
    verdict: str
    call_id: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != FINAL_SENTENCE_DECISION_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                "FinalSentenceFidelityDecision.schema_version 必须为 "
                f"{FINAL_SENTENCE_DECISION_SCHEMA_VERSION!r}")
        for name in ("task_id", "section_id", "draft_id", "draft_revision", "narrative_id",
                     "support_set_digest", "rubric_version", "prompt_version", "model_policy",
                     "call_id"):
            _require_nonempty(getattr(self, name),
                              f"FinalSentenceFidelityDecision.{name}")
        sentence_ids = _str_tuple(self.sentence_ids,
                                  "FinalSentenceFidelityDecision.sentence_ids")
        if len(set(sentence_ids)) != len(sentence_ids):
            raise NarrativeSchemaError(
                "FinalSentenceFidelityDecision.sentence_ids 含重复（覆盖面是集合，顺序即正文序）")
        object.__setattr__(self, "sentence_ids", sentence_ids)
        atoms = tuple(self.atoms or ())
        for atom in atoms:
            if not isinstance(atom, SentenceAtomReading):
                raise NarrativeSchemaError(
                    "FinalSentenceFidelityDecision.atoms 只接受 SentenceAtomReading，"
                    f"得到 {type(atom).__name__}")
            if atom.sentence_id not in sentence_ids:
                raise NarrativeSchemaError(
                    f"原子行挂在未被本决定覆盖的句子 {atom.sentence_id!r} 上："
                    "决定只能对它所覆盖的句子集下判断")
        object.__setattr__(self, "atoms", atoms)
        _require_enum(self.verdict, FINAL_SENTENCE_DECISION_VERDICTS,
                      "FinalSentenceFidelityDecision.verdict")
        bad = tuple(a for a in atoms if a.verdict != "entailed")
        if self.verdict == "entailed":
            if self.reason_code is not None:
                raise NarrativeSchemaError("verdict=entailed 不得携带 reason_code")
            if bad:
                raise NarrativeSchemaError(
                    f"aggregate 判为 entailed，却有 {len(bad)} 条原子未通过："
                    "聚合结论必须由逐原子结果推出，不得与它相反")
        else:
            _require_enum(self.reason_code, FINAL_SENTENCE_DECISION_REJECTION_REASONS,
                          "FinalSentenceFidelityDecision.reason_code")
            if not bad:
                raise NarrativeSchemaError(
                    "aggregate 判为 rejected 却没有任何未通过的原子："
                    "拒绝必须由一条typed 的逐原子结果承载")
            located = tuple(a for a in bad if a.verdict == "atom_not_located")
            expected = "atom_not_located" if located and len(located) == len(bad) \
                else "atom_not_entailed"
            if self.reason_code != expected:
                raise NarrativeSchemaError(
                    f"aggregate reason_code={self.reason_code!r} 与逐原子结果不符："
                    f"{len(bad)} 条未通过原子中 {len(located)} 条无位置 ⇒ 应为 {expected!r}")
        expected_id = derive_final_sentence_fidelity_decision_id(self)
        if self.final_sentence_fidelity_decision_id != expected_id:
            raise NarrativeSchemaError(
                "FinalSentenceFidelityDecision.final_sentence_fidelity_decision_id 与内容不符："
                f"声明 {self.final_sentence_fidelity_decision_id!r}，应为 {expected_id!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version, "task_id": self.task_id,
            "section_id": self.section_id, "draft_id": self.draft_id,
            "draft_revision": self.draft_revision, "narrative_id": self.narrative_id,
            "sentence_ids": list(self.sentence_ids),
            "atoms": [a.to_dict() for a in self.atoms],
            "support_set_digest": self.support_set_digest,
            "rubric_version": self.rubric_version, "prompt_version": self.prompt_version,
            "model_policy": self.model_policy, "verdict": self.verdict,
            "reason_code": self.reason_code, "call_id": self.call_id,
        }

    def to_dict(self) -> dict:
        return {"final_sentence_fidelity_decision_id":
                self.final_sentence_fidelity_decision_id, **self.identity_body()}

    def atom_verdicts(self) -> tuple[tuple[str, str, str, str, str | None], ...]:
        """逐原子的 `(sentence_id, atom_kind, atom_surface, verdict, reason_code)` 读视图。"""
        return tuple((a.sentence_id, a.atom_kind, a.atom_surface, a.verdict, a.reason_code)
                     for a in self.atoms)

    def is_stale_for(self, *, draft_id: str, section_id: str, draft_revision: str,
                     narrative_id: str, sentence_ids: "Sequence[str]") -> bool:
        """本决定是否还对得上**当前**的正文（过期锚点 + 覆盖面，逐项比）。

        「过期」是一个**读数**，不是一个可以靠调用方自律的口头约定：改句、改 Claim、换绑定都会改
        `sentence_id`（它本身已是 `text + claim_ids + citation_ids + context_binding_ids` 的内容
        身份），因此覆盖面一比就对不上——不需要新哈希。
        """
        return (str(self.draft_id) != str(draft_id)
                or str(self.section_id) != str(section_id)
                or str(self.draft_revision) != str(draft_revision)
                or str(self.narrative_id) != str(narrative_id)
                or tuple(self.sentence_ids) != tuple(str(x) for x in sentence_ids))

    @classmethod
    def create(cls, **kwargs: Any) -> "FinalSentenceFidelityDecision":
        # 原子行先归一到**对象**：身份体（`identity_body()`）按 wire 形态给出原子行，而构造
        # 只接受 `SentenceAtomReading`；两者混用会让 `create` 永远构造不出一份自洽的决定
        # （拿 dict 当身份体又拿 dict 当成员）。入参允许是对象，也允许是它的 `to_dict()` 形态
        # ——与 `SectionNarrative.create` 同一手法。
        atoms = tuple(a if isinstance(a, SentenceAtomReading) else SentenceAtomReading.from_dict(a)
                      for a in (kwargs.get("atoms") or ()))
        body = {
            "schema_version": FINAL_SENTENCE_DECISION_SCHEMA_VERSION,
            "task_id": kwargs.get("task_id"), "section_id": kwargs.get("section_id"),
            "draft_id": kwargs.get("draft_id"), "draft_revision": kwargs.get("draft_revision"),
            "narrative_id": kwargs.get("narrative_id"),
            "sentence_ids": tuple(kwargs.get("sentence_ids") or ()),
            "atoms": [a.to_dict() for a in atoms],
            "support_set_digest": kwargs.get("support_set_digest"),
            "rubric_version": kwargs.get("rubric_version") or FINAL_SENTENCE_RULES_VERSION,
            "prompt_version": kwargs.get("prompt_version") or "",
            "model_policy": kwargs.get("model_policy") or "",
            "verdict": kwargs.get("verdict"), "reason_code": kwargs.get("reason_code"),
            "call_id": kwargs.get("call_id") or "",
        }
        body["final_sentence_fidelity_decision_id"] = \
            derive_final_sentence_fidelity_decision_id(_WireView(body))
        body["atoms"] = atoms
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "FinalSentenceFidelityDecision":
        allowed = {"final_sentence_fidelity_decision_id", "schema_version", "task_id",
                   "section_id", "draft_id", "draft_revision", "narrative_id", "sentence_ids",
                   "atoms", "support_set_digest", "rubric_version", "prompt_version",
                   "model_policy", "verdict", "reason_code", "call_id"}
        d = _reject_unknown(d, allowed, "FinalSentenceFidelityDecision")
        return cls(
            final_sentence_fidelity_decision_id=_require_nonempty(
                d.get("final_sentence_fidelity_decision_id"),
                "FinalSentenceFidelityDecision.final_sentence_fidelity_decision_id"),
            schema_version=_require_nonempty(
                d.get("schema_version"), "FinalSentenceFidelityDecision.schema_version"),
            task_id=_require_nonempty(d.get("task_id"), "FinalSentenceFidelityDecision.task_id"),
            section_id=_require_nonempty(d.get("section_id"),
                                         "FinalSentenceFidelityDecision.section_id"),
            draft_id=_require_nonempty(d.get("draft_id"),
                                       "FinalSentenceFidelityDecision.draft_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "FinalSentenceFidelityDecision.draft_revision"),
            narrative_id=_require_nonempty(d.get("narrative_id"),
                                           "FinalSentenceFidelityDecision.narrative_id"),
            sentence_ids=_str_tuple(d.get("sentence_ids") or (),
                                    "FinalSentenceFidelityDecision.sentence_ids"),
            atoms=tuple(SentenceAtomReading.from_dict(a) for a in (d.get("atoms") or ())),
            support_set_digest=_require_nonempty(
                d.get("support_set_digest"), "FinalSentenceFidelityDecision.support_set_digest"),
            rubric_version=_require_nonempty(
                d.get("rubric_version"), "FinalSentenceFidelityDecision.rubric_version"),
            prompt_version=_require_nonempty(
                d.get("prompt_version"), "FinalSentenceFidelityDecision.prompt_version"),
            model_policy=_require_nonempty(
                d.get("model_policy"), "FinalSentenceFidelityDecision.model_policy"),
            verdict=_require_nonempty(d.get("verdict"), "FinalSentenceFidelityDecision.verdict"),
            reason_code=d.get("reason_code"),
            call_id=_require_nonempty(d.get("call_id"), "FinalSentenceFidelityDecision.call_id"))


def derive_final_sentence_fidelity_decision_id(decision: Any) -> str:
    body = (decision.identity_body()
            if isinstance(decision, FinalSentenceFidelityDecision) else decision.body)
    return content_id("nsfid_", body)


def final_sentence_gate_state(
        *, narrative: "SectionNarrative", decisions: "Sequence[Any]",
        claims: "Sequence[Any]", accepted_bindings: "Sequence[Any]",
        ) -> tuple[str | None, str]:
    """定稿路径上**唯一**的最终句门状态派生（纯函数、只读、不调用任何模型）。

    返回 `(reason_code, detail)`：`(None, "")` 表示本节的最终句集有**一份有效决定**；否则
    `reason_code` ∈ `FINAL_SENTENCE_BLOCK_REASONS`，`detail` 是**确定性**的人读说明（它与
    `reason_code` 一起进入 block 的 `unresolved_id`，因此两侧重算必须逐字相同）。

    `accepted_bindings` 是**本节**的已接受支撑边（context 边的过滤在本函数内完成）：四样输入
    （正文 / 决定 / 定稿 Claim / 已接受支撑边）都能从已封存的门后束重算，**没有第五样只在现场
    存在的东西**——这正是三处调用方共用它的前提。

    调用方（门后协调器 / 组装器 / Store 读回）**共用这一份实现**：三处各自重算、结论必须一致，
    任何一处自己写一套「算不算通过」都会让同一个缺口集合读出两种状态。

    检查顺序与理由（前一条不成立才看后一条，因此 detail 是单因的、可诊断的）：

    1. **无承载事实的句子**：本门无对象可核 ⇒ 没有决定也不阻断（合法性由 Draft 的缺口登记与
       组装器裁决，不由本门代替）。但此时**带着**决定反而是身份错位：决定的覆盖面不可能为空
       （判定面拒绝为空的句子集产出决定），故落在 `stale`。
    2. **没有决定**：`missing`。它**不**冒充语义判决——「调用失败 / 输出不可解析 ⇒ 未形成决定」
       也走这一档，detail 里如实写明该原因，而不是伪造一条 `rejected`。
    3. **多于一条决定**：`duplicate`。两个决定并存时无法判断以哪一份为准。
    4. **锚点或覆盖面过期**（`is_stale_for`）：`stale`。
    5. **支撑集指纹不符**：`stale`。覆盖面逐位相同**不等于**授权面相同——换掉一条支撑边
       （改 Claim 的 accepted set）时句子身份可能不变，但这条决定已经不是对当前授权面下的判断。
    6. **逐原子引用面过期**：`stale`。原子声明的 Claim 必须仍是本节已定稿 Claim、必须是**该句**
       声明过的 Claim，且它引的支撑边必须是那条 Claim **当前**的 factual 边。
    7. **聚合 verdict 非 `entailed`**：`rejected`（逐原子原因码在 `atoms[]` 里逐条给出）。

    **`detail` 逐字不带数字**（与写入侧缺口文案同一条纪律）：缺口文案里的数字不得与权威数据
    混淆，所以这里只写「有没有 / 是哪一档」，条数与 id 留在决定对象自己身上。唯一例外是
    `nsfid-1` 这类**版本标识**，调用方在建 block 时把它作为身份标识传入。
    """
    expected = fact_bearing_sentence_ids(narrative)
    rows = tuple(decisions or ())
    if not expected:
        if rows:
            return ("final_sentence_decision_stale",
                    "本节没有任何承载事实的最终句，却带着最终句决定：决定的覆盖面是它声明的"
                    "句子集，空集不是合法覆盖面，本节无从适用该决定")
        return (None, "")
    if not rows:
        return ("final_sentence_decision_missing",
                f"本节有承载事实的最终句，却没有形成最终句语义决定"
                f"（{FINAL_SENTENCE_DECISION_SCHEMA_VERSION}）：正文未经逐原子语义核验，"
                "本节不得按「已核验」定稿")
    if len(rows) > 1:
        return ("final_sentence_decision_duplicate",
                "本节同一个 draft revision 配到多于一条最终句决定，而每个 draft revision "
                "恰好一条：并存的决定无法判断以哪一份为准")
    decision = rows[0]
    draft_id = str(narrative.section_draft_id)
    revision = str(narrative.draft_revision)
    narrative_id = str(narrative.narrative_id)
    if decision.is_stale_for(draft_id=draft_id, section_id=str(narrative.section_id),
                             draft_revision=revision, narrative_id=narrative_id,
                             sentence_ids=expected):
        return ("final_sentence_decision_stale",
                "最终句决定的锚点或覆盖面与当前正文不符：改句、改 Claim、换绑定都会改句子身份，"
                "过期结论不得当作有效")
    # 授权面 = 正文**实际声明**的那些 Claim（`gate_claim_surface`），不是调用方手里的全部
    # 已定稿 Claim：三处调用方各自持有的 `claims` 集合可能不同（组装器持整节、定稿协调器持
    # 当轮），而「本节到底核了哪些 Claim」必须只有一份答案——否则同一个缺口集合会读出两种
    # 状态。收窄同时把「未被写进正文的 Claim」挡在授权面外（指令 D 第三项）。
    claims = gate_claim_surface(narrative, claims)
    declared = {str(claim.claim_id): claim for claim in claims}
    used_binding_ids: list[str] = []
    for claim in claims:
        for binding_id in claim.accepted_binding_ids:
            if str(binding_id) not in used_binding_ids:
                used_binding_ids.append(str(binding_id))
    # 授权面只收 **factual** 支撑边（context 边不授权事实），过滤**在本函数里做**：三处调用方
    # 各自传「本节的 accepted bindings」即可得到同一结论——各传各的、各自过滤一次，就会有人
    # 忘了过滤，而「同一个缺口集合读出两种状态」正是这条纪律要避免的。
    available = {str(binding.accepted_support_binding_id)
                 for binding in (accepted_bindings or ())
                 if str(getattr(binding, "support_semantics", "")) == "factual"}
    if set(used_binding_ids) - available:
        return ("final_sentence_decision_stale",
                "本节已定稿 Claim 引用的 factual 支撑边不在本次核验的授权面里："
                "授权面不完整时，决定的结论无从对回支撑边")
    digest = sentence_support_set_digest(
        draft_id=draft_id, draft_revision=revision, narrative_id=narrative_id,
        sentence_ids=expected, accepted_binding_ids=used_binding_ids)
    if str(decision.support_set_digest) != digest:
        return ("final_sentence_decision_stale",
                "最终句决定的支撑集指纹与当前授权面不符：换掉一条支撑边即换了一次判断，"
                "覆盖面逐位相同不足以让旧结论继续成立")
    sentence_claims = {str(sentence.sentence_id): tuple(str(c) for c in sentence.claim_ids)
                       for unit in narrative.paragraphs for sentence in unit.sentences}
    for atom in decision.atoms:
        if atom.claim_id is None:
            continue
        claim_id = str(atom.claim_id)
        if claim_id not in declared:
            return ("final_sentence_decision_stale",
                    "最终句决定里的原子声明的 Claim 不是本节当前已定稿 Claim："
                    "原子必须落在当前授权面上")
        if claim_id not in sentence_claims.get(str(atom.sentence_id), ()):
            return ("final_sentence_decision_stale",
                    "最终句决定里的原子声明的 Claim 不是它所在句子声明过的："
                    "原子与句子的对应关系在重算时对不上")
        allowed = {str(b) for b in declared[claim_id].accepted_binding_ids}
        if set(str(b) for b in atom.accepted_binding_ids) - allowed:
            return ("final_sentence_decision_stale",
                    "最终句决定里的原子引用的支撑边不是声明它的 Claim 当前的 factual 边："
                    "换绑定后旧读数不再成立")
    if decision.verdict != "entailed":
        return ("final_sentence_decision_rejected",
                f"最终句语义决定判为 rejected（聚合原因码 {decision.reason_code}）："
                "存在未通过的事实原子（含在已接受 Claim 集里没有位置的原子），"
                "逐条读数见决定的 atoms[]；未通过原子的正文不得按已核验定稿")
    return (None, "")


#: `ClaimSupportRef` / `FactNarrativeDisposition` 的权威容器类别（**封闭四元**）。
#: `external_snapshot` 是**逻辑名**：它承载的是 Pack 中 formal `ExternalFact` 的 authority
#: input，`ExternalSnapshot` 只是不变来源载体，**不得**被当作事实权威。
AUTHORITY_KINDS = ("topic_pack", "financial_pack", "evidence_note", "external_snapshot")
#: 支撑边的角色。`primary` 只允许落在 authoritative 权威上。
SUPPORT_ROLES = ("primary", "corroborating")
#: 支撑边的**语义轴**（与 `authority_kind` / `support_role` 三条轴相互正交，§6.2.3）：
#: `factual` 授权事实；`context` 只验证背景/结构/衔接，**不得**授权数字/主体/期间/因果/结论，
#: 也不产生 `ClaimEntailmentDecision`。**不得**用 `fact_id is None` 反推本轴。
SUPPORT_SEMANTICS = ("factual", "context")
#: proposal 阶段**必须**声明的封闭授权路径（只声明路径，不引用决定）。
AUTHORIZATION_PATHS = ("path_a_prevalidated", "path_b_material_derived", "context_only")
#: 绑定主体类别。factual 只允许 `claim_candidate`，context 只允许 `narrative_draft_unit`。
BINDING_SUBJECT_KINDS = ("claim_candidate", "narrative_draft_unit")
#: aggregate `ClaimBindingDecision` 的聚合结果（每个 subject revision **恰一个**）。
BINDING_DECISION_RESULTS = ("pass", "fail")
#: 逐边机械结果。
BINDING_EDGE_RESULTS = ("pass", "fail")
#: 逐边失败的**封闭**原因码（typed audit；不得塞进泛化字符串）。
BINDING_EDGE_REASON_CODES = (
    "proposal_missing",
    "proposal_unknown",
    "proposal_duplicate",
    "proposal_order_drift",
    "manifest_identity_mismatch",
    "authority_kind_mismatch",
    "authority_container_mismatch",
    "source_identity_mismatch",
    "support_role_mismatch",
    "support_semantics_mismatch",
    "authorization_path_mismatch",
    "missing_authority_fact",
    "missing_material_or_locator",
    "forbidden_field_present",
    "subject_mismatch",
    "fingerprint_mismatch",
    # §三 A.7：manifest 成员**存在**不等于它的正文被解析过。路径 B / context 的机械门必须
    # 自己回查该成员是否携带真实 payload/locator 解析结果；只凭 material ID 一律拒绝。
    "material_payload_unresolved",
    # §二（P0）：路径 B 的授权面是**非高风险描述性原子**。候选文本里只要出现任何高风险表面
    # （数字/金额/比例/日期/期间/币种、显式否定、勾选与适用状态、法人主体身份、表格行列与
    # 合计/占比关系、因果或结论连接），该边就**不能**用材料派生路径授权——**无论**这些字面
    # 成分是否在材料正文里逐字存在。原文逐字存在**不等于**已经资格化：高风险硬事实只有
    # authoritative fact identity（路径 A 预验证）一条正式入口。本码是这条规则的唯一拒绝码。
    "path_b_high_risk_surface",
)
#: 聚合集合结构性失败（proposal 集不完整/含未知/顺序漂移/重复/为空）的封闭原因码。
BINDING_STRUCTURAL_REASONS = (
    "proposal_set_empty",
    "proposal_set_incomplete",
    "proposal_set_has_unknown",
    "proposal_order_drift",
    "proposal_duplicate",
)
#: `ClaimEntailmentDecision` 的语义结论。`rejected` 同样是**一条 typed 决定**，不是缺记录。
ENTAILMENT_VERDICTS = ("entailed", "rejected")
#: `ClaimEntailmentDecision` 判 rejected 时的封闭原因码。
ENTAILMENT_REJECTION_REASONS = (
    # §三（P0）：候选**不是原子命题**（含两个或以上可分别判断真假的断言）。原子性是蕴含的
    # **前置**：一条候选里塞两个断言时，蕴含判断本身没有意义（一个成立、另一个不成立时，
    # `entailed` 与 `rejected` 都在说错话），因此它必须是**独立的** typed 拒绝码。
    # 该判断是**语义**判断（由 P9 核验器做），不是字符串捷径：出现逗号不是拒绝理由。
    "non_atomic_claim",
    "not_entailed_by_bound_authority",
    "unsupported_specificity",
    "unauthorized_number_or_period",
    "unauthorized_subject_or_scope",
    "causal_or_conclusion_overreach",
    "authority_conflict_unresolved",
)
#: `ClaimSupportRef` 的**兼容 union** 说明：narr-4 的合法支撑边只有 `ProposedSupportRef`
#: （门前）与 `AcceptedSupportBinding`（门后）两种 stage identity。`ClaimSupportRef` 是二者的
#: 兼容 union **名称**，**不是第三种可独立实例化的 wire**；narr-3 载荷里它是具体类型，只经
#: `load_legacy_narrative_narr3_for_audit` 只读回放。
CLAIM_SUPPORT_REF_UNION_KINDS = ("proposed_support_ref", "accepted_support_binding")
#: `NarrativeDraftUnit` 的单元类别（封闭）。
DRAFT_UNIT_KINDS = ("paragraph", "table")
#: WMPD 的 used / not_used 分区（**每个** manifest 成员恰属其一，P6 三层等式）。
MATERIAL_USAGE_STATES = ("used", "not_used")
#: `not_used` 的封闭理由码（§6.4.1 材料集合第四层）。Writer 不得自造自由文本理由：未使用的
#: 理由必须落在这张表里，且**不是** gap —— 「本轮没用到」与「Contract 必需事实未取得」是
#: 两条不同的轴。
MATERIAL_NOT_USED_REASONS = (
    "duplicate",
    "irrelevant_to_section_goal",
    "candidate_qualification_failed",
    "covered_by_more_complete_material",
    "not_selected_after_budget_partition",
)
#: 任何 `not_used` 理由都必须携带的**证明政策**绑定（§6.4.1：无 proof/policy binding 的
#: not-used reason 一律拒绝）。`policy_fingerprint` 是版本化策略字节的 sha256。
MATERIAL_PROOF_POLICY_KEYS = ("policy_version", "policy_fingerprint")
#: 逐理由码必须携带的 typed proof 字段（键集**精确相等**：少一个即无证明，多一个即逃逸字段）。
MATERIAL_NOT_USED_PROOF_KEYS = {
    "duplicate": ("duplicate_of_member_ref",),
    "irrelevant_to_section_goal": ("relevance_decision_ref",),
    "candidate_qualification_failed": ("fact_candidate_id", "qualification_decision_id"),
    "covered_by_more_complete_material": ("other_pack_id", "other_material_id",
                                          "coverage_relation_ref"),
    "not_selected_after_budget_partition": ("partition_plan_id", "partition_plan_version",
                                            "budget_tier"),
}
#: 权威状态。`recorded_only` 表示「如实记录但不得作为正文金额权威」（非权威/漂移快照）。
AUTHORITY_STATES = ("authoritative", "supplemental_only", "recorded_only", "rejected")
#: 句子类别。
#: * `factual`：逐字取自**一条** Claim 的事实句（`render_factual_text`，字符级零新增）；
#: * `transition`：封闭词表内的连接语，不承载任何事实；
#: * `composed`（narr-5，§三 C）：由**多条**已接受 Claim 组织而成的自然句。它允许改动语序、
#:   添加不含事实的表面连接成分，但**不得**新增任何高风险表面（数字/主体/期间/否定/状态/
#:   表格关系）：句子里每个这样的 token 必须在它自己声明的 Claim 文本里逐字存在。
#: * `natural`（narr-7，M930-3 指令 E 第 3 项）：以**门前自然草稿**（`NaturalProseDraftUnit`）
#:   为表达基础的改写句。它与 `composed` 的结构判据相同（同一套高风险表面检查、同一套连接语
#:   与归属判据），但**第 8 条由「逐字等于 Claim 文本」换成「`verify_sentence_fidelity` 保真核对」**：
#:   句子的数字 / 期间 / 否定 / 因果 / 实体头部名词 / 范围与强度语必须逐字仍在它声明的 Claim 里，
#:   同时不得出现声明 Claim 之外的高风险表面。这是**唯一的**放宽点，且只放宽「逐字」，
#:   不放宽「不得新增」；`factual` / `composed` 两条老路径一字未动。
SENTENCE_KINDS = ("factual", "transition", "composed", "natural")
#: 权威 fact 在正文中的去向（恰一条，无默认、无省略）。
NARRATIVE_DISPOSITIONS = ("claimed", "supporting_only", "not_presented_with_reason")
#: `not_presented_with_reason` / `supporting_only` 的封闭原因集合。
DISPOSITION_REASON_CODES = (
    "covered_by_other_fact",
    "duplicate_of_claimed_fact",
    "not_required_for_selected_aspects",
    "superseded_by_stronger_authority",
    "blocked_by_authority_state",
    "outside_narrative_scope",
)
#: `NarrativeTableRow` 显式非事实显示的封闭原因（只用于表头/单位/来源注记等展示行）。
NON_FACTUAL_ROW_REASONS = (
    "display_label_only",
    "period_header",
    "unit_declaration",
    "source_note",
)
#: narrative 门的严重度（与 `sections.rules_evaluator` 的三档一致）。
GATE_SEVERITIES = ("blocking", "rework", "warning")


class NarrativeSchemaError(Exception):
    """narrative wire 构造/校验失败（fail-closed）。"""


class MaterialBindingAmbiguityError(NarrativeSchemaError):
    """引用锚点在容器内对应**多于一个** material 候选，且类型化定位无法消歧（fail-closed）。

    §四：候选歧义时不得任选第一条/最后一条/最长/最高分。任选一个都会把「这条 Claim 出自
    哪份材料」从可回查事实降级成实现细节，且随材料顺序漂移——那正是本轮要修掉的缺陷。
    """


# ---------------------------------------------------------------------------
# 确定性工具
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """稳定 JSON 序列化（排序键 + 紧凑分隔符 + 转义非 ASCII），供内容寻址使用。"""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def content_id(prefix: str, body: Any) -> str:
    """内容寻址 id：同内容必得同 id，改一个字节必得新 id。"""
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return prefix + digest[:24]


def _is_sha256_hex(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def _reject_unknown(d: Any, allowed: Iterable[str], what: str) -> dict:
    if not isinstance(d, dict):
        raise NarrativeSchemaError(f"{what} 必须是对象")
    unknown = sorted(set(d) - set(allowed))
    if unknown:
        raise NarrativeSchemaError(f"{what} 含未登记字段 {unknown}")
    return d


def _req(d: Mapping[str, Any], key: str, what: str, *, allow_none: bool = False) -> Any:
    if key not in d:
        raise NarrativeSchemaError(f"{what} 缺字段 {key!r}")
    value = d[key]
    if value is None and not allow_none:
        raise NarrativeSchemaError(f"{what}.{key} 不得为 null")
    return value


def _str_tuple(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise NarrativeSchemaError(f"{what} 必须是字符串列表")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or item == "":
            raise NarrativeSchemaError(f"{what} 含非字符串/空元素: {item!r}")
        out.append(item)
    return tuple(out)


def _cells_tuple(value: Any, what: str) -> tuple[str, ...]:
    """表格**单元格**列表的归一：允许空串（`FINANCIAL_TABLE_EMPTY_CELL`），其余同 `_str_tuple`。

    `_str_tuple` 拒空元素，对 id/标签类字段是对的；对 `NarrativeTableRow.cells` 则与 wire 自己的
    规则冲突：单元格的空串**就是**一条显式事实（「本行这一列没有已接受的权威事实」），门后构造器
    （`build_metric_period_tables`）正是靠它把缺值显式留空。若这里拒空串，「缺值留空」就只能在
    构造期 Hard fail —— 而「缺值」恰恰是必须能被如实表达的东西。非字符串/`None` 仍然拒。
    """
    if not isinstance(value, (list, tuple)):
        raise NarrativeSchemaError(f"{what} 必须是字符串列表")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise NarrativeSchemaError(f"{what} 含非字符串元素: {item!r}")
        out.append(item)
    return tuple(out)


def _require_nonempty(value: Any, what: str) -> str:
    if not isinstance(value, str) or value == "":
        raise NarrativeSchemaError(f"{what} 必须为非空字符串")
    return value


def _canon_jsonable(obj: Any) -> Any:
    """把投影内容递归归一成规范 JSON 形状（键排序），用于**内容寻址**而非展示。"""
    if isinstance(obj, Mapping):
        return {str(k): _canon_jsonable(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canon_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise NarrativeSchemaError(
        f"投影内容含不可规范化的类型 {type(obj).__name__}：投影必须是可以内容寻址的 JSON")


def _projection_list(value: Any, what: str) -> tuple[dict, ...]:
    """一组投影对象：逐项规范化 + 按规范序列化排序（顺序不参与身份，内容全参与）。"""
    if value is None:
        return ()
    if isinstance(value, Mapping) or not isinstance(value, (list, tuple)):
        raise NarrativeSchemaError(f"{what} 必须是对象列表")
    out: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise NarrativeSchemaError(f"{what} 含非对象项：{item!r}")
        canonical = _canon_jsonable(item)
        key = canonical_json(canonical)
        if key in seen:
            continue
        seen.add(key)
        out.append(canonical)
    out.sort(key=canonical_json)
    return tuple(out)


def _require_enum(value: str, allowed: Sequence[str], what: str) -> str:
    if value not in allowed:
        raise NarrativeSchemaError(f"{what} 非法: {value!r}（允许 {tuple(allowed)}）")
    return value


# ---------------------------------------------------------------------------
# 数字扫描（§6.4：正文数字必须由权威事实绑定，不得出现裸数字）
#
# 扫描是**确定性**的：同一段文本永远得到同一组 token。构造 NarrativeSentence 时声明
# 的 token 必须与扫描结果逐字相同（所以无法「声明少一点」来藏数字），随后由 writer 用
# `authorized_numeric_tokens` 逐 token 比对它所属 Claim 的权威事实。
# ---------------------------------------------------------------------------

#: 数字 token：可选负号 + 千分位数字 + 可选小数 + 可选单位/百分号后缀。
#: 前视/后视排除紧邻字母数字（避免把 `A1`、`v2`、`abc123` 里的数字当金额）。
_NUMERIC_UNITS = ("个百分点", "亿元", "万元", "个月", "%", "‰", "亿", "万", "千", "百",
                  "元", "倍", "股", "家", "人", "项", "天", "年")
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z_.])"
    r"-?[0-9][0-9,]*(?:\.[0-9]+)?"
    r"(?:\s?(?:" + "|".join(_NUMERIC_UNITS) + r"))?"
)


def scan_numeric_tokens(text: str) -> tuple[str, ...]:
    """按出现顺序返回文本中的数字 token（去重保留首次出现；空格归一化）。"""
    if not isinstance(text, str):
        raise NarrativeSchemaError("scan_numeric_tokens 需要字符串")
    seen: list[str] = []
    for match in _NUMERIC_TOKEN_RE.finditer(text):
        token = " ".join(match.group(0).split())
        if token not in seen:
            seen.append(token)
    return tuple(seen)


def authorized_numeric_tokens(texts: Iterable[str]) -> frozenset[str]:
    """把权威事实自带的文本/规范值扫描成一个「允许出现的数字」集合。

    只接受**权威侧**字符串（fact.text / value 规范串 / display / period / citation period
    等）。Writer 绝不用正文自身的文本构建这个集合——那样任何数字都会自证合法。

    §十一：本集合只做**逐字**归一（千分位与空白的排版差异），不派生任何等价写法。
    """
    allowed: set[str] = set()
    for text in texts:
        if not isinstance(text, str) or text == "":
            continue
        for token in scan_numeric_tokens(text):
            allowed.add(_literal_token(token))
    return frozenset(allowed)


def _literal_token(token: str) -> str:
    """token 的排版归一：去掉千分位分隔符，**并删除全部空白**（不改数值、符号、单位、尾零）。

    M930-3 定点纠正（2026-10-02）：PDF 文字层常把「数字 + 单位」写成**带一个空格**
    （`24 家`、`1,700 万`、`129,641,258 千`），而正文正常写作写「`24家`」「`1,700万`」。
    原先这里只**折叠**空白（`" ".join(split())`）不**删除**，两者归一后分别是 `24 家` 与
    `24家`，永不相等 ⇒ 一句**数字确实在来源里**的话被判 `unsourced_number_surface` 硬错。

    等价面是**有界**的：token 由 :data:`_NUMERIC_TOKEN_RE` 扫出，形状只能是
    `<数字><至多一个空白><可选单位>`（见 :data:`_NUMERIC_UNITS`），因此删除空白**只**可能
    把「数字」与它的「单位」之间那个排版空格消掉——它不会跨两个数字、不会派生单位、不会
    改符号、不会补尾零。`-5` 与 `5`、`1.6` 与 `1.60`、`129,641,258千` 与 `129,641,258千元`
    在本函数下**依旧不相等**（后者差的不是空格，是单位）。
    """
    return "".join(token.replace(",", "").split())


def numeric_token_authorized(token: str, allowed: Iterable[str]) -> bool:
    """正文 token 是否被权威侧文本**逐字**覆盖（只做千分位/空白归一）。

    §十一：`-5` 与 `5` 是两个不同的 token，`1.6` 与 `1.60` 也是；单位必须一起出现。
    本函数不做符号取反、不做尾零补齐、不做单位剥离——那三类派生都会让「未被权威事实
    绑定的数字」凭排版等价自证合法。
    """
    return _literal_token(token) in {_literal_token(a) for a in allowed}


def claim_numeric_tokens(claim_texts: Iterable[str]) -> tuple[str, ...]:
    """一组 Claim 文本**逐字**携带的数字 token（§十一 2：正文数字的唯一授权根）。

    正文里出现的每个数字必须能在某条被绑定 Claim 的文本中原样找到；Claim 文本本身
    由权威事实逐字派生（`pack_writer._financial_fact_text` / `fact.text`），因此这条链
    的根仍然是权威侧，而不是正文自己。
    """
    out: list[str] = []
    for text in claim_texts:
        if not isinstance(text, str) or text == "":
            continue
        for token in scan_numeric_tokens(text):
            if token not in out:
                out.append(token)
    return tuple(out)


def numeric_token_in_claims(token: str, claim_tokens: Iterable[str]) -> bool:
    """token 是否在某条被绑定 Claim 的文本里原样出现（千分位/空白归一后逐字比较）。"""
    return _literal_token(token) in {_literal_token(t) for t in claim_tokens}


#: 正文不得出现的「无期间/无主体绑定的措辞」（§十二 4）。这些词看起来像期间，却既没有
#: 绑定任何显式期间也没有绑定任何主体，是「报告期」这类未定义指代进入正文的唯一入口。
def vague_period_hits(text: str) -> tuple[str, ...]:
    """返回文本中命中的含糊期间措辞（空 tuple 表示没有）。"""
    if not isinstance(text, str):
        return ()
    return tuple(p for p in VAGUE_PERIOD_PHRASES if p in text)


# ---------------------------------------------------------------------------
# 高风险表面（**唯一**实现）
# ---------------------------------------------------------------------------
#
# 这里的三个封闭集合是「一段文本里哪些成分属于**必须被授权**的高风险硬事实」的**唯一**
# 判据来源。写入侧（`sections/pack_writer.py` 的路径 B 预验证）与门后侧（composed 句的
# 表面守恒）必须用**同一份**集合：两处各维护一份等价词表，等于允许两套口径漂移——而漂移的
# 方向恰恰总是「更松的那一套先被用上」。因此 pack_writer 只保留别名，不保留副本。

#: 表格行列与列表关系这一组的**具名切片**（`HIGH_RISK_SURFACE_MARKERS` 的成员）。
#:
#: 为什么具名：最终句语义门（`sections/final_sentence_fidelity.py`，`nsfid-1`）的原子定位器要把
#: 机械命中的标记按 `atom_kind` 分流，其中 `table_relation` 与 `negation_or_state` 同源于
#: `marker_hits`。分流若在本表**之外**再抄一份表格词组，就是第二份词表——正是本文件开头那段
#: 「两处各维护一份等价词表，等于允许两套口径漂移」明令禁止的。子集判据必须挂在**同一份**集合上。
#: 内容与顺序与本组此前逐字相同（本切片只是把它取了个名字，一个字未增未删）。
TABLE_RELATION_SURFACE_MARKERS = (
    "合计", "小计", "总计", "占比", "其中", "同比", "环比", "较上年", "较上期",
    "增减", "差额", "平均值", "本表", "下表", "上表",
)

#: 显式否定 / 勾选与适用状态 / 表格与列表关系 / 推理与结论连接 / 趋势与比较结论的**封闭**标记集合。
#: 同一份集合，两种判据（§二 裁决后按**边**分开，不再共用一套口径）：
#:   * **路径 B 授权面**（写入侧 `pack_writer` 与门后 `claim_binding_gate`）：**命中即拒**。
#:     路径 B 只授权非高风险描述性原子，因此只要候选文本里出现本集合的任何成分就拒绝；
#:     「材料正文里逐字存在」**不是**资格证明——逐字在场只说明它是一段原文，不说明它已被资格化。
#:   * **composed 句表面守恒**（`unauthorized_surfaces`）：**逐字在场**。一个多 Claim 组织成的
#:     句子，其高风险表面必须能在它自己声明的那些 Claim 文本里原样找到——这里挡的是组织者
#:     自行新增，而不是 Claim 自己写着的内容。
#: 两处判据不同是刻意的：把「逐字在场」用在路径 B 上，正是 §二 要废掉的那个错误语义。
HIGH_RISK_SURFACE_MARKERS = (
    # 显式否定（把「有」写成「没有」是最高风险的一类改写）
    "不存在", "未披露", "未发生", "未取得", "未达到", "未包含", "未出现", "未被",
    "不符合", "不适用", "不属于", "不构成", "不确定", "不满足", "不再", "不予",
    "尚未", "并未", "并非", "没有", "无重大", "无任何", "以上均无",
    # 勾选 / 状态 / 适用性
    "勾选", "已选", "未选", "选中", "打勾", "适用", "不适用", "有效", "失效",
    "是/否",
    # 表格行列与列表关系（具名切片，定义见上：本组即 `TABLE_RELATION_SURFACE_MARKERS`）
    *TABLE_RELATION_SURFACE_MARKERS,
    # 推理 / 结论连接（§三 C：组织者不得新增因果、推断或结论）。
    # 这类词与前面各组是同一个集合、同一条纪律：它不引入任何数字，却把**并列**升级为
    # **因果或判断**，即改写整句的语义强度——比多写一个数字更危险，因为它不留字面痕迹。
    # 两种判据见上方注释：路径 B 命中即拒；composed 句里 Claim 自己写着的因果表述照样可以
    # 组织进正文，被拒的只是「Claim 里没有这个连接词，正文却自行添加」。
    "因此", "因而", "故此", "从而", "导致", "说明", "表明", "反映",
    "由此可见", "可以看出", "这意味着", "主要是因为", "究其原因", "综上",
    # 趋势 / 比较结论（§七 2：文字只承担已被权威事实支持的必要说明）。
    # 「指标变好还是变坏」不是材料里的一句原文，而是一条**需要判断的比较结论**：它没有预验证的
    # 趋势/比较事实就写不出来。方向词与上方的因果连接词同属 §三 C 那一类——不引入数字，却把
    # 「本期数」升级成「相对判断」。这里是冻结的封闭列举：其它比较写法（`同比` / `环比` /
    # `较上年` / `增减` / `差额`）已经在上方表格关系组里，不在此重复。
    "上升", "下降", "改善", "恶化",
    # 会计口径（期间与记账币种）。`nrules-11` 新增：r7b 现场有两条 path-B 候选
    # （`公司会计期间采用公历年度` / `本公司及境内子公司以人民币为记账本位币`）**不带任何数字**
    # 地进了正文，其中「会计期间」一条还与 `company_identity_basic.legal_rep` 那条被拒身份事实
    # 同源。会计政策是**公司自己声明**的口径事实（期间怎么算、拿什么记账），与主体身份、数字
    # 同级：材料正文里逐字存在**不是**资格证明。判据不新造：仍是「路径 B 命中即拒 / composed 句
    # 逐字在场」。
    "会计期间", "会计年度", "公历年度", "记账本位币", "本位币",
    # 法人身份角色（自然人身份 / 治理角色）。`nrules-11` 新增：既有封闭集只用 `ENTITY_SUFFIXES`
    # 认**法人主体名**，认不出「某某人为公司法定代表人」这种**点名的自然人身份**——于是
    # `公司法定代表人为曾毓群` 成了正文里唯一一条既被写成已授权事实、又在材料侧挂着 blocked
    # 缺口的候选（读者会同时读到「是」与「缺」）。本组是主体身份那组的自然人对应物，同样只做
    # 字面命中，不做语义判断：材料里写着某人是什么角色，不等于该事实已被资格化。
    "法定代表人", "法人代表", "实际控制人", "控股股东", "董事长",
    # 状态评价（无期间绑定的现状判断）。`nrules-11` 新增：`境外客户回款情况正常` 由一条
    # 「报告期内…」材料写成，Claim 文本把材料自己的**期间限定语丢掉了**，于是同一句话从
    # 「某段期间内的观察」升级成一条**无期间**的确定判断——读者会把它读成「生成时点的现状」。
    # 「好 / 正常 / 稳定」这类评价本身不是材料里的一个事实原子，而是一条需要预验证的结论。
    "正常", "良好", "稳定",
    # 文档内 / 跨文档交叉引用关系。`nrules-11` 新增：`公司作为整体，产品和服务的对外交易收入
    # 情况详见年度报告财务报告部分` 是**文档版式指路**，不是关于公司经营的描述性原子；把它当
    # 业务正文写出来，读者拿到的是「请看别处」而不是内容。
    "详见", "参见",
    # 证券上市与挂牌状态（证券身份事实）。`nrules-11` 新增：上市地 / 板块 / 挂牌状态与主体身份、
    # 数字同级，属必须预验证的硬事实。`上市` 在文书名（`上市公告说明书`）里不承担这个角色，
    # 该次出现由 `MARKER_SUBSTRING_EXEMPTIONS` 逐次覆盖。
    "挂牌", "上市", "上市交易",
)
#: 法人主体身份的**封闭后缀**集合（主体身份与数字同级：属高风险硬事实）。
ENTITY_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "集团公司", "集团", "公司", "银行",
    "证券", "基金", "保险", "研究院", "研究所", "大学", "交易所", "事务所",
)
#: 主体名回扫时**必须停下**的连接词/虚词：它们属于句子结构，不属于主体名的一部分。
#:
#: **已知边界（本批未修，待裁决）**：表内只列了 `为`，没有列同类的**系词** `是`。
#: `X是<名词短语>` 与 `X为<名词短语>` 是同一类结构，但回扫在 `是` 处**不停**，于是
#: `entity_name_tokens("公司是一家零碳新能源科技公司")` 会把主语与谓语粘成一条**来源里根本
#: 不存在的**短语（实测得 `('公司是一家零碳新能源科技公司',)`，而来源里只有
#: `零碳新能源科技公司`）。这会凭空吃一条 `unsourced_subject_surface`。
#: 修法是一行（把 `是` 并进本集合，与既有的 `为` 同类，判据仍是纯字面回扫），但它是
#: **主体名匹配集**的**判定集**变化——按 `nrules-10` 立下的规矩（「判定集变了……故不得共用
#: 版本号」），必须与 `NARRATIVE_RULES_VERSION` / `NARRATIVE_GATE_VERSION` /
#: `SENTENCE_CHECK_POLICY_VERSION` 三处版本前进**一起**落地，并重核钉住这些版本的离线重放。
#: 那是另一件有自身回归面的事，不在本批的「最小返修」范围内，故此处只登记，不实施。
_ENTITY_RUN_STOP = set("的与和及等为在对从因由则即而并或其该这那此将已被把向以也所")
#: 回扫上界（汉字数）。旧值 10 会把长名**截断**成不存在的主体（`宁德时代新能源科技股份有限
#: 公司` → `德时代新能源科技股份有限公司`），因此上界必须大于最长真实法人名。24 个汉字覆盖
#: 中文法人名的实际长度（含行政区划 + 字号 + 行业 + 组织形式），同时仍是一个**有界**预算。
#: 它只放宽**上界**：真正防越界的是停止字符集与前缀词裁剪，二者都没有放宽。
_ENTITY_MAX_BACKSCAN = 24
#: 主体名**前缀词**（封闭集合，按长度降序匹配）：它们是句子结构或通用角色/文书指代，不是字号。
#: 回扫越过它们时逐次剥掉，使 `根据中国人民银行` → `中国人民银行`、`发行人母公司` → 落空。
_ENTITY_GENERIC_PREFIXES = (
    "年度报告披露", "年度报告同时披露", "年度报告", "招股说明书", "募集说明书", "说明书",
    "报告期内", "报告期", "披露", "根据", "依据", "发行人", "本公司", "母公司", "子公司",
    "下属", "境内", "境外", "关于", "截至", "经",
)
#: 只剩组织形式、不构成字号的**通用片段**（封闭集合）：`有限` + `公司`、`集团` + `公司`
#: 这类组合不是主体身份，不得单独成为一个 token。
_ENTITY_GENERIC_RUNS = frozenset({
    "有限", "责任", "股份", "股份有限", "集团", "控股", "公司", "企业", "投资", "实业", "国际",
})
#: 回扫所得的「字号」如果**整个就是一条时间状语**，那它不是字号（`nrules-17` 新增，封闭集合）。
#:
#: 这是 `_ENTITY_RUN_STOP` 那条「回扫是字符级」边界的第二类落点：回扫在数字处会停
#: （`2024年末公司` 的 `4` 不是汉字），于是 `年末` 整段被当成字号，产出 `年末公司`；
#: `报告期末公司`、`截至报告期末公司`、`本报告期末公司` 同理。它们**都不是主体名**，
#: 却会各吃一条 `unsourced_subject_surface`。判据刻意**不做**任何语义判断，只问
#: 「剥前缀之后剩下的这一串，是不是恰好等于集合里的某一条时间状语」——是则不产出 token。
#: 它**只删**这些**整串即状语**的情形，不裁剪长字号的一部分（`宁德时代…` 一字不动），
#: 因此不会缩短任何真实法人名。**已知残余边界**：`报告期末本公司` 回扫得 `末本`
#: （`本` 属于后缀 `公司` 那一侧，不在本集合），本批不处理——它既不在这批语料里，
#: 也不能靠加 `本` 解决（那会吃掉 `日本…` 这类真实字号）。
_ENTITY_TEMPORAL_RUNS = frozenset({
    "年初", "年末", "年度", "期末", "期内", "季度末", "月末", "月初", "月底", "度末",
    "报告期内", "报告期末", "本报告期末", "本年末", "本年度", "本期", "当期",
})
#: 时间状语前可能残留的**量词**（`2024年期末` 的 `4` 叫停回扫，`年` 就留在字号里）。
#: `_is_temporal_run` 允许先剥掉**一个**这样的前导量词再查集合：`年期末` → `期末`（命中）、
#: `年年初` → `年初`（命中）、`年度末` → `度末`（命中）。它**不**放宽集合本身——剥完之后
#: 仍必须整串等于集合里的某一条；真实字号（`宁德时代…`、`中国石油…`）剥不到任何一条。
_ENTITY_TEMPORAL_LEAD_QUANTIFIERS = frozenset({"年", "月", "季"})


def _is_temporal_run(run: str) -> bool:
    """`run` 是不是**整条**时间状语（`_ENTITY_TEMPORAL_RUNS` 的查表，允许剥一个前导量词）。

    两问，都只查封闭集合、都不做语义判断：

    1. `run` 本身就在集合里；
    2. `run` 去掉**首字量词**之后在集合里（首字必须属于 `_ENTITY_TEMPORAL_LEAD_QUANTIFIERS`）。

    第 2 条是实测补上的：`2024年期末公司…` 回扫得 `年期末`，光查第 1 条会漏，仍产出
    `年期末公司` 这条来源里不存在的主体名。补它不会吃掉任何真实字号——要求「剥掉一个
    `年/月/季` 之后**整串恰好**是 `年度`/`期末`/`年初`…」。`run` 为空或只剩一个字时
    一律不是（那连主体名都不够格，另有 `len(run) < 2` 兜底）。
    """
    if run in _ENTITY_TEMPORAL_RUNS:
        return True
    return (
        len(run) > 1
        and run[0] in _ENTITY_TEMPORAL_LEAD_QUANTIFIERS
        and run[1:] in _ENTITY_TEMPORAL_RUNS
    )
#: 标记词的**子串豁免**（封闭集合）：这些标记在某几个更长的词里**不承担**它被登记的那个
#: 风险角色，因此那一次字面出现不算命中。判据刻意是「**每一次**出现都被豁免形覆盖」才对
#: 该标记整体免判——只要有一处是独立的，标记照旧命中。它不是「删掉一个风险词」：
#: `说明` 在「募集说明书」里是**文书名**（名词），在「…说明公司未发生…」里仍然是结论连接词。
MARKER_SUBSTRING_EXEMPTIONS = {
    "说明": ("募集说明书", "招股说明书", "上市公告说明书", "说明书"),
    # `nrules-11`：`上市` 在**文书名**里是名词的一部分（这份文书叫「上市公告说明书」），
    # 不承担「证券已上市」这个状态角色。豁免逐次判定：同一段里只要另有一处独立的 `上市`，
    # 标记照旧命中。
    "上市": ("上市公告说明书",),
}
#: 汉字区间（用转义常量而不是字面字符：源码里一眼可读，不依赖编辑器编码）。
_CJK_START, _CJK_END = "一", "鿿"

#: 一句话里最多允许承载几条已定稿 Claim（§七 1「一个超长句塞入大量互不组织的 Claim」）。
#: 它是一个**上界**，不是文风偏好：超过它，句子就不再是「组织」，而是把一节的事实倒进一句话。
#: 组织器要覆盖更多 Claim 时，正确动作是**多写一句**（或让表格承载），不是把句子写长。
MAX_COMPOSED_CLAIMS_PER_SENTENCE = 4

#: 句末标点（**封闭**集合）。一句话里至多允许出现**一个**，且只能在末尾：句中标点会把一句变成
#: 两句，「一句 = 一个句子」这条在文本层就没了判据。它与 `render_factual_text` 归一化时去掉的
#: 那一组同源（那里去掉的是 `。；;`，本集合再含 `！？!?`，是**更严**的一层，不改变那条既有
#: 渲染口径——事实句的渲染一个字都不动）。
SENTENCE_TERMINATORS = "。；;！？!?"

#: 组合句**接缝**的起头标点（**封闭**集合，`nrules-12`）。两条 Claim 文本之间必须先落一个
#: 分隔标点，组织语（连接词）只能落在它**后面**。
#:
#: 为什么必须是一条判据、而不是一句「写得通顺些」：`A此外，B` 与 `A，此外，B` 只差一个字符，
#: 而第 8 条现有的四条判据在这两个文本上给出**完全相同**的答案——声明的 Claim 都按序在场、
#: 组织成分都含汉字、都没有句中句末标点、都没超过条数上界。于是最容易被写出的那类病句
#: （把**句首**连接语塞进两个子句之间，读者看到的是「…研发、生产、销售此外，公司产品…」）
#: 在门下一路绿灯，而它恰恰是「正文不像正文」的主要来源。判据只能是**接缝的第一个字符**：
#: 判「有没有连接语」拦不住相邻 Claim 直接黏连，判「连接语在不在封闭词表里」拦不住位置。
#:
#: 刻意**不**把 `；` 收进来：句末标点已经由 `SENTENCE_TERMINATORS` 在别处拦住（用 `；` 接缝的
#: 句子在判据 d 就被拒），两条判据各自只有一个所有者，缺陷码也才不会互相盖住。
SEAM_LEAD_PUNCTUATION = "，,、：:"

#: 纯标点接缝里允许出现的字符（**封闭**集合，只有标点与空白）：分隔符、括号、引号、破折号。
#: 它定义的是「正文 = 若干 Claim 文本 + 分隔标点」这一整类句子：模型在这句话里**没有**贡献任何
#: 组织成分——既没有组织语（汉字），也没有自造表面（数字 / 字母 / 百分号）。
JOIN_RESIDUE_PUNCTUATION = "，,、；;：:。！？!?…—－-~～()（）[]【】「」『』“”‘’\"'、《》<>/ \t"


def is_punctuation_only_join(text: str, claim_texts: "Sequence[str]") -> bool:
    """`text` 是否是「若干 Claim 文本 + 纯标点接缝」（唯一的判据实现）。

    只有这种句子才允许走「每条 Claim 各自成句」的补救（`sections.narrative_organizer` 的
    第二级）：那一级会**丢掉**接缝上的字符，因此接缝上只允许有标点。接缝上写着自造数字
    （`A，42%，B`）或模型自己写的组织语（`A，同时，B`）时**不**满足它——那种句子里有模型贡献的
    字，补救就等于替模型改写正文，而它正确的去向是 fail-closed（判据拒了就是拒了，不得洗成合法）。

    与第一级（按句中句末标点**逐字**切段）无关：那一级一个字都不丢（切完拼回去必须等于原文），
    所以不受本判据限制。
    """
    residue = _organizing_residue(
        str(text or ""), tuple(str(t or "") for t in (claim_texts or ())))
    if residue is None:
        return False
    return all(ch in JOIN_RESIDUE_PUNCTUATION for ch in residue)


def _claim_spans(text: str, claim_texts: "Sequence[str]"
                 ) -> "list[tuple[int, int]] | None":
    """按**声明顺序**定位每条 Claim 文本在 `text` 里的字符跨度（唯一的定位实现）。

    返回 `None` 表示有一条声明的 Claim 文本**没有**按序出现在这段文本里——那种句子声称支撑
    来自它其实没有呈现的 Claim，属绑定与正文不一致，调用方据此 fail-closed。

    左侧起点是贪心的（`find(..., cursor)`），因此第 i 条的跨度只可能从第 i-1 条结束之后开始：
    跨度之间不会互相包含，把它们从文本里挖掉之后剩下的就是严格的「组织成分」。
    """
    cursor = 0
    spans: list[tuple[int, int]] = []
    for raw in claim_texts:
        needle = str(raw or "")
        if not needle:
            return None
        index = text.find(needle, cursor)
        if index < 0:
            return None
        spans.append((index, index + len(needle)))
        cursor = index + len(needle)
    return spans


def _organizing_residue(text: str, claim_texts: "Sequence[str]") -> str | None:
    """把句子声明的 Claim 文本按**声明顺序**逐条剔除后剩下的组织成分（§七 1）。

    返回 `None` 表示有一条声明的 Claim 文本**没有**按序出现在句子里——那种句子声称支撑来自
    它其实没有呈现的 Claim，属绑定与正文不一致，调用方据此 fail-closed。返回空串/纯标点表示
    该句是 Claim 文本的**机械拼接**（`A；B。` / `A。B。`），组织器一个字都没有贡献。
    """
    body = str(text or "")
    spans = _claim_spans(body, claim_texts)
    if spans is None:
        return None
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(body[cursor:start])
        cursor = end
    parts.append(body[cursor:])
    return "".join(parts)


def _residue_segments(text: str, spans: "Sequence[tuple[int, int]]") -> tuple[str, ...]:
    """Claim 跨度**之外**的连续文本段（句首、两两之间、句尾）——组织器自己写的那些字。

    与 `_organizing_residue` 返回的拼接串不同，这里**保持分段**：把几段组织语首尾相接再扫一遍
    表面，会让扫描器在段与段的接缝上合成一个两段都不存在的 token，而那正是本节要消掉的那类
    假阳性（只是换了个接缝）。
    """
    body = str(text or "")
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(body[cursor:start])
        cursor = end
    out.append(body[cursor:])
    return tuple(out)


def unauthorized_surfaces_within_claims(text: str, claim_texts: "Sequence[str]"
                                        ) -> tuple[str, ...]:
    """组合句的**边界感知**高风险表面核验（§三 2.4）：表面按**文本分段**核验，不跨接缝合成。

    为什么需要它：`unauthorized_surfaces` 对整句一次扫描，而它的若干判据是**合成式**的——
    主体名是「后缀 + 向前回扫汉字」拼出来的。组织器把两条 Claim 用衔接语串起来时，回扫会
    跨过接缝：`"…2011年" + "同时" + "公司于…"` 里，第二个「公司」的回扫会一路吃掉「年同时」，
    产出一个两个声明 Claim 里都不存在的 token「年同时公司」，于是**同一份合规的正文有时过、
    有时被拒**（取决于接缝恰好落在哪）。这不是「门太严」，是判据在接缝上量错了对象。

    口径（**边界感知，不是白名单**）：把句子按声明 Claim 的跨度切成「Claim 段」与「组织段」，
    表面只在**单个组织段内**合成。Claim 段无需扫描——它逐字就是该 Claim 文本，落进去的表面
    必然已在授权池里；组织段里合成出来的表面仍按**原判据**核验（必须逐字出现在某条声明
    Claim 文本里），因此组织器在衔接区自行写出一个完整主体名、一个数字或一个「因此」照样被拒。
    法人主体门、数字门、否定门、状态门、表格关系门、因果门一个都没删，只是**不再跨接缝发明
    token**。

    定位不成立（有条 Claim 文本没按序出现）时退回整句扫描：那种句子本来就 fail-closed，
    这里不替它挑一个更松的判据。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (claim_texts or ()))
    spans = _claim_spans(body, texts)
    if spans is None:
        return unauthorized_surfaces(body, texts)
    supported = "\n".join(texts)
    out: list[str] = []
    for segment in _residue_segments(body, spans):
        for token in high_risk_surface_tokens(segment):
            if token not in supported and token not in out:
                out.append(token)
    return tuple(out)


def has_internal_sentence_terminator(text: str) -> bool:
    """句子文本里有没有**句中**句末标点（末尾的一串是允许的，它只是句读）。

    `A。同时B。` 这种「双句容器」在文本层就是两个句子首尾相接：它既不是「一条自然句」，
    也不能因为它声明了两条 Claim 就被算成多 Claim 自然句（§七 1）。
    """
    body = str(text or "").strip()
    while body and body[-1] in SENTENCE_TERMINATORS:
        body = body[:-1].rstrip()
    return any(ch in SENTENCE_TERMINATORS for ch in body)


def _entity_suffix_matches(text: str) -> list[tuple[int, int]]:
    """text 里每个主体后缀的每次出现，做完**匹配集卫生**后的 `(start, end)` 列表。

    两个纯机械的规则（都不做语义判断，也都**不删词**）：

    * **包含**：一个匹配的跨度整体落在另一个**更长**匹配内时丢弃。`有限公司` 是
      `股份有限公司` 的内嵌标签，`公司` 又是 `有限公司` 的内嵌标签——三个都留下就会把
      同一个名字报成三种身份，而且较短的两次回扫会各自造出**不存在**的主体名。
    * **紧邻**：一个匹配的终点正好是另一个匹配的起点时丢弃较短的那个。`证券` + `交易所`
      里前者的「证券」是后者的一部分（`深圳证券交易所` 是一个主体，不是「深圳证券」+「交易所」
      两个）。
    """
    raw: list[tuple[int, int]] = []
    for suffix in ENTITY_SUFFIXES:
        start = 0
        while True:
            index = text.find(suffix, start)
            if index < 0:
                break
            start = index + 1
            raw.append((index, index + len(suffix)))
    kept: list[tuple[int, int]] = []
    for span in raw:
        length = span[1] - span[0]
        contained = any(other is not span and other[0] <= span[0] and span[1] <= other[1]
                        and (other[1] - other[0]) > length for other in raw)
        if contained:
            continue
        adjacent = any(other is not span and other[0] == span[1]
                       and (other[1] - other[0]) >= length for other in raw)
        if adjacent:
            continue
        kept.append(span)
    return sorted(set(kept))


def _entity_name_run(text: str, index: int) -> str:
    """从 `index`（后缀起点）向前回扫出的**字号**部分（可能为空串）。

    回扫在非汉字或 `_ENTITY_RUN_STOP` 字符处停下，上限 `_ENTITY_MAX_BACKSCAN`；随后由左向右
    反复剥掉 `_ENTITY_GENERIC_PREFIXES` 里的前缀词。剥前缀是**逐次**做的：
    `年度报告同时披露公司` 要连剥两刀才落空，`根据中国人民银行` 剥一刀就得到真正的主体名。
    """
    back = index
    while (back > 0 and index - back < _ENTITY_MAX_BACKSCAN
           and _CJK_START <= text[back - 1] <= _CJK_END
           and text[back - 1] not in _ENTITY_RUN_STOP):
        back -= 1
    run = text[back:index]
    #: 剥前缀必须剥到**不动点**，不是「按长度降序走一趟」。一趟的写法有一个可复算的漏：
    #: 一趟里 `报告期` 排在 `截至` **之前**被检查，当时 `run` 还是 `截至报告期末`，不匹配；
    #: 等 `截至` 把它剥成 `报告期末` 时，`报告期` 这一趟**已经过去了**，`末` 这个 1 字残余
    #: 就被留成了字号，造出一条**来源里根本不存在**的「主体名」——实测
    #: `entity_name_tokens("截至报告期末公司…")` 曾得 `('报告期末公司',)`，凭空吃一条
    #: `unsourced_subject_surface`（`scp-8` 修）。本函数的 docstring 一直写的是「反复剥掉」，
    #: 这里改成与那句话一致的**不动点**循环：每轮剥掉当前**最长**的可匹配前缀，直到没有
    #: 前缀还能匹配。它只多剥掉**本来就在封闭前缀集里**的词，不放宽任何判据。
    prefixes = sorted(_ENTITY_GENERIC_PREFIXES, key=len, reverse=True)
    while True:
        #: 剥到「整串已经**就是**一条时间状语」就停手（`_is_temporal_run`）：再往下剥只会把
        #: `报告期末` 削成 1 字残余 `末`，两个方向都要为此付代价——授权方向固然不再产出伪
        #: token（那正是本批要的），但保真方向也跟着丢掉 `报告期末公司` 的头名词 `公司`，
        #: 那是把授权轴的修复代价转嫁给保真轴。停在这里，两个方向的分歧交回 `_entity_tokens`：
        #: 授权方向由 `_is_temporal_run` 整个不产出，保真方向照旧取头名词。
        if _is_temporal_run(run):
            return run
        #: 回扫 + 后缀会把「本公司」**切开**：`报告期末本公司` 里 `公司` 命中在 `本` 之后，
        #: 回扫因此得到 `报告期末本`。若去掉那个残留的 `本` 之后剩下的**是一条时间状语**，
        #: 整串就是「时间状语 + 自称」，不是主体名——`nrules-18` 修。它只多剥掉一个字，且
        #: **必须**在剥前缀之前判定：一旦 `报告期` 被剥走，剩下 `末本` 就再也认不出原形，
        #: 那正是 `scp-8` 没修干净、`scp-9` 补上的那一格（`报告期末本公司…` 曾产出
        #: `末本公司` 这样来源里根本不存在的伪主体名）。
        if run.endswith("本") and _is_temporal_run(run[:-1]):
            return run[:-1]
        for prefix in prefixes:
            if run.startswith(prefix):
                run = run[len(prefix):]
                break
        else:
            return run


def _entity_tokens(text: str, *, drop_temporal_runs: bool) -> tuple[str, ...]:
    """`entity_name_tokens` / `entity_head_nouns` 共用的抽取体。

    `drop_temporal_runs` 分出两个**方向不同**的读法，二者都对，但服务的是两件事：

    * **True（授权方向）**——`entity_name_tokens` 用它。整串就是一条时间状语的「字号」
      （`年末`/`年度`/`报告期末`…）**不是主体名**，产出它只会在授权轴上凭空吃一条
      `unsourced_subject_surface`（`scp-8` 修的就是这条）。这里**不产出**。
    * **False（保真方向）**——`entity_head_nouns` 用它。保真轴要的是「这句话还在说一家
      公司/集团/银行」，取的是**头部名词**；`年度公司` 的头是 `公司`，正是保真轴要守的那一格。
      在这个方向上多认一个 token 是**保守**的（见 `ENTITY_HEAD_NOUNS` 的说明），因此不裁剪。
      若这里也裁掉，保真轴会**少一格读数**——那是把授权轴的修复代价转嫁给保真轴，不允许。
    """
    if not isinstance(text, str):
        return ()
    out: list[str] = []
    for start, end in _entity_suffix_matches(text):
        suffix = text[start:end]
        run = _entity_name_run(text, start)
        if len(run) < 2 or run in _ENTITY_GENERIC_RUNS:
            continue
        if drop_temporal_runs and _is_temporal_run(run):
            continue
        token = run + suffix
        if token not in out:
            out.append(token)
    return tuple(out)


def entity_name_tokens(text: str) -> tuple[str, ...]:
    """从文本里抽出「主体名 + 后缀」的**逐字**片段（封闭规则，不做任何语义判断）。

    规则：找出全部主体后缀匹配并做匹配集卫生（见 `_entity_suffix_matches`），逐个向前回扫出
    字号（见 `_entity_name_run`）；字号长度 >= 2、且不是 `_ENTITY_GENERIC_RUNS` 里的纯组织形式
    片段，才算一个主体身份 token（少于 2 个汉字不足以构成主体名，例如「该公司」式的指代会被
    连接词截断成「」而自然落空）。返回值按出现顺序去重。

    **整串即时间状语的字号不产出**（`nrules-17` / `nrules-18`／`_is_temporal_run`）：这是
    **授权方向**的口径——`报告期末公司`、`2024年末公司`、`2024年期末公司` 这类 token 不是
    主体名，产出它们只会凭空吃硬错。`nrules-18` 又补上「时间状语 + 自称」那一格
    （`报告期末本公司` 的 `本公司` 会被后缀切开，`nrules-17` 下仍产出伪 token `末本公司`）。
    保真方向另有一份不裁剪的读法，见 `_entity_tokens`。

    **已知边界（本批不改，如实登记）**：回扫仍是**字符级**停止，因此一个不含连接词的长动宾短语
    紧贴主体名时仍可能被一起扫进来（`详细介绍公司`）。要修它需要名号级词典或统计判据，那是另一
    条口径，不在本批授权范围内；本批只修「内嵌标签、紧邻标签、上界截断、通用前缀」四类**可复算**
    的机械缺陷。
    """
    return _entity_tokens(text, drop_temporal_runs=True)


def marker_hits(text: str) -> tuple[str, ...]:
    """文本命中的**风险标记词**（`HIGH_RISK_SURFACE_MARKERS` 减去子串豁免后的那一层）。

    一个字面命中被剔除，当且仅当该标记在文本里的**每一次**出现都落在它的某个豁免形之内
    （`MARKER_SUBSTRING_EXEMPTIONS`；逐次删除豁免形后标记不再出现）。这是「按词算」而不是
    「按位置算」：判据只看这份文本自己，可复算，也不需要任何语义判断。
    """
    out: list[str] = []
    for marker in HIGH_RISK_SURFACE_MARKERS:
        if marker not in text:
            continue
        residue = text
        for exempt in MARKER_SUBSTRING_EXEMPTIONS.get(marker, ()):
            residue = residue.replace(exempt, "")
        if marker in residue:
            out.append(marker)
    return tuple(out)


def high_risk_surface_tokens(text: str) -> tuple[str, ...]:
    """一段文本里的**全部高风险表面**：数字 / 显式否定与状态标记 / 含糊期间 / 主体身份。

    这是「这段文本里有哪些字面成分必须被授权」的**唯一**答案；调用方不得各自挑一部分。
    """
    if not isinstance(text, str):
        return ()
    surfaces: list[str] = list(scan_numeric_tokens(text))
    surfaces += list(marker_hits(text))
    surfaces += list(vague_period_hits(text))
    surfaces += list(entity_name_tokens(text))
    out: list[str] = []
    for token in surfaces:
        if token not in out:
            out.append(token)
    return tuple(out)


def unauthorized_surfaces(text: str, authorized_texts: Iterable[str]) -> tuple[str, ...]:
    """`text` 里**未**在授权文本池中逐字出现的高风险表面（空 tuple 表示全部被授权）。

    判据刻意是「逐字在场」而不是「语义相近」：本层不做语义判断，只做字面核验。
    """
    supported = "\n".join(str(t) for t in (authorized_texts or ()))
    return tuple(t for t in high_risk_surface_tokens(text) if t not in supported)


# ---------------------------------------------------------------------------
# ClaimSupportRef
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClaimSupportRef:
    """Pack/Fact/Material → Claim 的唯一血缘边。

    **narr-4 定位**：本类只是 `ProposedSupportRef | AcceptedSupportBinding` 的**兼容 union
    名称**（§6.3），**不是第三种可独立实例化的 wire**；narr-4 的 current 链只构造那两个
    successor 类型。本类保留下来，仅供 narr-3 载荷经 `load_legacy_narrative_narr3_for_audit`
    只读回放（`ClaimSupportRef` 在过去是具体类型）。因此它的形状规则**不是**succeed 真值表：
    四元 authority union 的完整 required/forbidden 见 §6.2.3，实现落在
    `ProposedSupportRef` / `AcceptedSupportBinding`。**不得**把它当作完整真值表复用。

    authority-kind 封闭的 id 形状（混装即 fail-closed）：
    - `topic_pack`：`fact_id` 与/或 `material_id`（至少其一）；`payload_ref` 仅在
      `material_id` 存在时允许；`locator_ref` 必须为空。
    - `financial_pack`：必须且只能有 `financial_fact_id`。
    - `evidence_note`：必须有 `fact_id` + `locator_ref`（evidence_id + char range + span_id）。
    - `external_snapshot`：**本类不承载**（narr-3 时代不存在该 kind）。narr-4 的外部事实边
      必须用 `ProposedSupportRef` / `AcceptedSupportBinding` 并绑定正式 `external_fact_id`
      + 资格决定 + snapshot container/URL/body hash/SourcePolicy/日期/locator；在这里放行
      只会造出一条既非 proposal 又非 accepted 的第三种 wire，故显式拒绝。
    """

    support_ref_id: str
    claim_id: str
    authority_kind: str
    authority_container_id: str
    citation_id: str
    support_role: str
    authority_state: str
    fact_id: str | None = None
    financial_fact_id: str | None = None
    material_id: str | None = None
    payload_ref: dict | None = None
    #: **只读 legacy**：`loc-0` 审计 locator（由 `load_legacy_locator_for_audit` 产出）。
    #: 本类从不承载 current `loc-1` locator——它是 narr-3 时代的裸三元组。
    locator_ref: dict | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.claim_id, "ClaimSupportRef.claim_id")
        _require_enum(self.authority_kind, AUTHORITY_KINDS, "ClaimSupportRef.authority_kind")
        _require_nonempty(self.authority_container_id, "ClaimSupportRef.authority_container_id")
        _require_nonempty(self.citation_id, "ClaimSupportRef.citation_id")
        _require_enum(self.support_role, SUPPORT_ROLES, "ClaimSupportRef.support_role")
        _require_enum(self.authority_state, AUTHORITY_STATES, "ClaimSupportRef.authority_state")

        if self.authority_kind == "topic_pack":
            if self.financial_fact_id is not None:
                raise NarrativeSchemaError(
                    "topic_pack 支撑边不得携带 financial_fact_id（authority kind 混装）")
            if self.locator_ref is not None:
                raise NarrativeSchemaError("topic_pack 支撑边不得携带 locator_ref")
            if self.fact_id is None and self.material_id is None:
                raise NarrativeSchemaError(
                    "topic_pack 支撑边必须至少绑定 fact_id 或 material_id")
            if self.payload_ref is not None and self.material_id is None:
                raise NarrativeSchemaError(
                    "payload_ref 只在绑定 material_id 时允许（否则引用无载体可解析）")
        elif self.authority_kind == "financial_pack":
            if self.financial_fact_id is None:
                raise NarrativeSchemaError("financial_pack 支撑边必须绑定 financial_fact_id")
            for name in ("fact_id", "material_id", "payload_ref", "locator_ref"):
                if getattr(self, name) is not None:
                    raise NarrativeSchemaError(
                        f"financial_pack 支撑边不得携带 {name}（authority kind 混装）")
        elif self.authority_kind == "external_snapshot":
            raise NarrativeSchemaError(
                "claim-1/narr-3 的 ClaimSupportRef 不承载 external_snapshot 边："
                "外部事实必须走 ProposalSupportRef/AcceptedSupportBinding 并绑定正式 "
                "external_fact_id + 资格决定 + snapshot container/URL/body hash/SourcePolicy/"
                "日期/locator（不得凭 snapshot 单独授权事实）")
        else:  # evidence_note
            if self.fact_id is None:
                raise NarrativeSchemaError("evidence_note 支撑边必须绑定附注 fact_id")
            if self.locator_ref is None:
                raise NarrativeSchemaError(
                    "evidence_note 支撑边必须绑定 locator_ref（evidence_id+char range+span_id）")
            for name in ("financial_fact_id", "material_id", "payload_ref"):
                if getattr(self, name) is not None:
                    raise NarrativeSchemaError(
                        f"evidence_note 支撑边不得携带 {name}（authority kind 混装）")
            legacy = load_legacy_locator_for_audit(self.locator_ref)
            if not legacy["owner"]:
                raise NarrativeSchemaError("locator_ref 的 evidence_id 不得为空")
            object.__setattr__(self, "locator_ref", legacy)

        if self.support_role == "primary" and self.authority_state != "authoritative":
            raise NarrativeSchemaError(
                f"primary 支撑边只能落在 authoritative 权威上；当前 authority_state="
                f"{self.authority_state!r}（非权威来源不得作为正文主支撑）")

        expected = derive_support_ref_id(self)
        if self.support_ref_id != expected:
            raise NarrativeSchemaError(
                f"ClaimSupportRef.support_ref_id 与内容不符：声明 {self.support_ref_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "authority_kind": self.authority_kind,
            "authority_container_id": self.authority_container_id,
            "citation_id": self.citation_id,
            "support_role": self.support_role,
            "authority_state": self.authority_state,
            "fact_id": self.fact_id,
            "financial_fact_id": self.financial_fact_id,
            "material_id": self.material_id,
            "payload_ref": self.payload_ref,
            "locator_ref": dict(self.locator_ref) if self.locator_ref else None,
        }

    def to_dict(self) -> dict:
        return {"support_ref_id": self.support_ref_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ClaimSupportRef":
        body = {k: kwargs.get(k) for k in (
            "claim_id", "authority_kind", "authority_container_id", "citation_id",
            "support_role", "authority_state", "fact_id", "financial_fact_id",
            "material_id", "payload_ref", "locator_ref")}
        locator = body.get("locator_ref")
        if locator is not None:
            # narr-3 的 locator 是裸三元组 `(evidence_id, char_start, char_end)`（`loc-0`）。
            # 这里显式经 legacy reader 读取：既不猜它是闭块区间，也不让 `loc-0` 载荷悄悄
            # 冒充 current `loc-1` locator。
            body["locator_ref"] = load_legacy_locator_for_audit(locator)
        body["support_ref_id"] = derive_support_ref_id(_SupportRefView(body))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "ClaimSupportRef":
        d = _reject_unknown(d, {"support_ref_id", "claim_id", "authority_kind",
                                "authority_container_id", "citation_id", "support_role",
                                "authority_state", "fact_id", "financial_fact_id",
                                "material_id", "payload_ref", "locator_ref"}, "ClaimSupportRef")
        locator = d.get("locator_ref")
        return cls(
            support_ref_id=_require_nonempty(d.get("support_ref_id"), "ClaimSupportRef.support_ref_id"),
            claim_id=_require_nonempty(d.get("claim_id"), "ClaimSupportRef.claim_id"),
            authority_kind=_require_nonempty(d.get("authority_kind"), "ClaimSupportRef.authority_kind"),
            authority_container_id=_require_nonempty(
                d.get("authority_container_id"), "ClaimSupportRef.authority_container_id"),
            citation_id=_require_nonempty(d.get("citation_id"), "ClaimSupportRef.citation_id"),
            support_role=_require_nonempty(d.get("support_role"), "ClaimSupportRef.support_role"),
            authority_state=_require_nonempty(d.get("authority_state"), "ClaimSupportRef.authority_state"),
            fact_id=d.get("fact_id"), financial_fact_id=d.get("financial_fact_id"),
            material_id=d.get("material_id"), payload_ref=d.get("payload_ref"),
            locator_ref=load_legacy_locator_for_audit(locator) if locator else None)


def derive_support_ref_id(ref: Any) -> str:
    """`ClaimSupportRef` 的内容寻址 id（对 `identity_body` 求 hash）。"""
    if isinstance(ref, ClaimSupportRef):
        body = ref.identity_body()
    elif isinstance(ref, dict):
        body = dict(ref)
    else:
        body = ref.body
    return content_id("csr_", body)


class _SupportRefView:
    """`create()` 的中间视图：只提供 `body`，让 derive 与真对象共用同一函数。"""

    __slots__ = ("body",)

    def __init__(self, body: dict) -> None:
        self.body = body


class _WireView:
    """successor 类型 `create()` 的中间视图（与 `_SupportRefView` 同一手法）。"""

    __slots__ = ("body",)

    def __init__(self, body: dict) -> None:
        self.body = body


# ---------------------------------------------------------------------------
# narr-4 successor：ClaimCandidate / NarrativeDraftUnit / ProposedSupportRef
#                  ClaimBindingDecision / ClaimEntailmentDecision / AcceptedSupportBinding
#
# 参考 DAG（§6.2.1 / §16.7.1 P5，**单向**）：
#   SectionDraft(claim_candidates, narrative_draft_units, proposed_support_refs)
#     → 每个 subject revision 恰一个 aggregate ClaimBindingDecision
#     → factual candidate 恰一个 ClaimEntailmentDecision；context 为 0 条
#     → 每个通过 proposal 各一条 AcceptedSupportBinding
#     → 之后才形成 SectionClaim / final Narrative → SectionResult（单向引用 draft）
#
# 方向规则：**后形成的对象持有前者的 ID**。proposal 不得引用任何决定；accepted binding
# 不得引用 SectionClaim / final Narrative / SectionResult（这些字段在本模块根本不存在，
# 即「在类型层不可表达」）。
# ---------------------------------------------------------------------------

#: `external_snapshot` 边的 snapshot ref 必须携带的字段（§6.2.3：snapshot ID + URL/body hash
#: + SourcePolicy + 日期）。`ExternalSnapshot` 只是**来源载体**，不能单独授权事实。
EXTERNAL_SNAPSHOT_REF_KEYS = ("snapshot_id", "canonical_url", "body_hash",
                              "source_policy_version", "as_of_date")

#: 逐 authority kind 的「authority-specific fact 字段名」。
FACT_FIELD_BY_AUTHORITY_KIND = {
    "topic_pack": "fact_id",
    "financial_pack": "financial_fact_id",
    "evidence_note": "note_fact_id",
    "external_snapshot": "external_fact_id",
}
ALL_FACT_FIELDS = ("fact_id", "financial_fact_id", "note_fact_id", "external_fact_id")


def external_authority_container_id(external_fact: Any) -> str:
    """formal `ExternalFact` 的**权威容器 id**（§6.2.3：容器身份与事实身份必须分开）。

    承载外部权威的容器是 snapshot **容器记录**（`source_snapshot_id`），事实身份是
    `external_fact_id`；二者由两个字段分别表达，任何一个都不得单独授权原子事实。
    前缀与 `note_container_id` 同一约定：容器 id 由容器自己派生，不从事实 id 借名。
    """
    snapshot_id = str(getattr(external_fact, "source_snapshot_id", "") or "")
    if not snapshot_id:
        raise NarrativeSchemaError(
            "external fact 缺 source_snapshot_id，无法确定权威容器身份"
            "（snapshot 只是来源载体，必须与 external_fact_id 分别在场）")
    return "external_snapshot:" + snapshot_id


def authority_numeric_texts(kind: str, fact: Any) -> tuple[str, ...]:
    """该权威事实**自己的**可用于数字授权的文本（§十一 2：数字的唯一授权根是权威侧字段）。

    逐 kind 只取承载**命题与规范值**的字段：

    - `topic_pack`：`text` / `period` / `scope` 与 `value_identity` 的规范分量；
    - `financial_pack`：科目名/编码/期间记号/期间表达/权威渲染串/单位/附注；
    - `evidence_note`：附注名/渲染串/数值/附注/期间；
    - `external_snapshot`：命题 `statement` 与 `as_of_date`。

    **本池必须覆盖 `authoritative_fact_surface` 拼句时用到的每一个字段**：表面是逐字交给
    Writer、也逐字出现在正文里的那句；池子少了其中任何一个，照抄该表面的候选就会被本门判成
    「未授权数字」——那是门自己在拒绝自己的权威表面，不是候选写错了。给表面加字段时，
    这里必须同一批登记（`financial_pack` 的 `period_label` 正是这么漏过一次）。

    **刻意排除** URL、body hash、页码与 citation 元数据：它们是定位/载体字段，不是正文命题。
    把 hash 或页码放进授权池，等于让正文从一串十六进制或版面数字里「借」到未受权的数字。
    """
    values: list[Any] = []
    if kind == "topic_pack":
        values = [getattr(fact, "text", ""), getattr(fact, "period", ""),
                  getattr(fact, "scope", "")]
        value_identity = getattr(fact, "value_identity", None)
        if value_identity is not None:
            values.extend([getattr(value_identity, "amount_canonical", ""),
                           getattr(value_identity, "metric", ""),
                           getattr(value_identity, "unit", ""),
                           getattr(value_identity, "period", ""),
                           getattr(value_identity, "scope", "")])
    elif kind == "financial_pack":
        # `period_label` 必须在池里：**权威表面**（`authoritative_fact_surface`）拼句时用的就是
        # `financial_period_text(fact)`，即 `period_label`（`pb-1` 之后期间表达与期间记号已分家：
        # `period` 是身份/分组/列对齐用的记号 `2023-12-31`，`period_label` 是给读者看、也逐字写进
        # 正文的 `2023年末`）。少了它，读视图交给 Writer（以及送给模型）的那句
        # `2023年末的有息负债为…` 里的 `2023年` 就没有授权来源——候选**逐字照抄自己的权威表面**
        # 也会被判未授权数字，财务节必然 fail-closed，而「唯一授权根是权威侧字段」这条纪律本身是
        # 满足的：漏的是**这个字段**，不是这条纪律。池子与表面必须同源，新增表面字段要同步在此登记。
        values = [getattr(fact, "label", ""), getattr(fact, "code", ""),
                  getattr(fact, "period", ""), getattr(fact, "period_label", ""),
                  getattr(fact, "display", ""), getattr(fact, "value_text", ""),
                  getattr(fact, "unit", ""), getattr(fact, "note", "")]
    elif kind == "evidence_note":
        values = [getattr(fact, "label", ""), getattr(fact, "display", ""),
                  getattr(fact, "value_text", ""), getattr(fact, "note", ""),
                  getattr(fact, "period", "")]
    elif kind == "external_snapshot":
        values = [getattr(fact, "statement", ""), getattr(fact, "as_of_date", "")]
    else:
        raise NarrativeSchemaError(f"authority_numeric_texts 不认识 authority_kind={kind!r}")
    return tuple(str(v) for v in values if isinstance(v, str) and v)


def _validate_snapshot_ref(payload_ref: Any, what: str) -> dict:
    if not isinstance(payload_ref, Mapping):
        raise NarrativeSchemaError(
            f"{what} 的 external_snapshot 边必须绑定 snapshot payload_ref（对象），"
            f"得到 {type(payload_ref).__name__}")
    missing = [k for k in EXTERNAL_SNAPSHOT_REF_KEYS if not payload_ref.get(k)]
    if missing:
        raise NarrativeSchemaError(
            f"{what} 的 snapshot ref 缺字段 {missing}：snapshot 只是来源载体，"
            "必须与 external_fact_id/资格决定 + URL/body hash/SourcePolicy/日期/locator 同时在场")
    return dict(payload_ref)


def _validate_locator(locator_ref: Any, what: str) -> dict | None:
    """`validate_locator` 的旧名字（本模块内部沿用）。语义**不是**旧三元组，见下。"""
    return validate_locator(locator_ref, what)


# ---------------------------------------------------------------------------
# exact locator 的**版本化 tagged union**（§四.5）
# ---------------------------------------------------------------------------
#
# 缺陷（`loc-0`，`narr-5` 及以前）：`locator_ref` 是裸三元组 `(owner, start, end)`，却被两个
# **互不相容**的区间语义共用：
#   * 权威字符区间（`evidence_note` 的 `evidence_char_range`、snapshot 载体的字符定位）是
#     **半开**区间 `start < end`；
#   * Topic material 的 `block_range` 是**闭**区间 `first <= last`，单块材料因此合法地是
#     `(0, 0)`。
# 于是「单块材料」的合法 locator 一旦按三元组校验就必然被拒（`end > start` 不成立），而放行
# 它就必须放宽同一个字段——两类区间从此无法再区分。**猜类型**（看 owner 后缀、看 `start==end`
# 就当成闭区间）是被明确禁止的：它把 wire 语义交给调用方推测。
#
# 因此 current wire 改为**带标签的联合**：每个 locator 自己声明 `locator_kind`，区间语义由
# 标签唯一确定，字段名也随之区分（`start/end` vs `first/last`）。旧形态只能经
# `load_legacy_locator_for_audit` **显式**读取，且读出来的对象带 `loc-0` 标签、**不能**进入
# 任何 current wire（`validate_locator` 拒收）——禁止静默重解释。

#: current locator wire 版本。
LOCATOR_SCHEMA_VERSION = "loc-1"
#: 被取代的 locator wire：裸三元组 `(owner, start, end)`（半开字符区间）。**只读**。
LEGACY_LOCATOR_SCHEMA_VERSION = "loc-0"
#: current 允许的 locator 变体（每个变体有自己的字段集与区间语义）。
LOCATOR_KINDS = ("char_range", "block_range", "table_cell", "whole_payload")
#: 变体 → 该变体**必须**携带的字段（除 `locator_schema` / `locator_kind` 外**恰是**这些）。
LOCATOR_VARIANT_FIELDS: dict[str, tuple[str, ...]] = {
    # 字符区间：半开 `0 <= start < end`（原文里的字符位置）。
    "char_range": ("owner", "start", "end"),
    # 块区间：**闭** `0 <= first <= last`（`(0, 0)` 是合法的单块材料）。
    "block_range": ("owner", "first", "last"),
    # 表格单元：表引用 + 行列下标（不含区间语义，不假装是字符位置）。
    "table_cell": ("owner", "table_ref", "row_index", "column_index"),
    # 整个 payload：只指向载体身份，**不声称**原文里的任何位置。
    "whole_payload": ("owner",),
}


def _locator_int(value: Any, what: str, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise NarrativeSchemaError(f"{what}.locator_ref.{field} 必须是 >=0 整数，得到 {value!r}")
    return int(value)


def _require_locator_owner(owner: Any, what: str) -> str:
    if not isinstance(owner, str) or not owner.strip():
        raise NarrativeSchemaError(
            f"{what}.locator_ref.owner 必须是非空字符串（locator 必须有归属载体）")
    return owner


def block_range_locator(owner: Any, first: Any, last: Any) -> dict:
    """闭区间块的 locator（`first <= last`；单块 `(0, 0)` 合法）。"""
    return {"locator_schema": LOCATOR_SCHEMA_VERSION, "locator_kind": "block_range",
            "owner": str(owner), "first": int(first), "last": int(last)}


def char_range_locator(owner: Any, start: Any, end: Any) -> dict:
    """半开字符区间的 locator（`start < end`）。"""
    return {"locator_schema": LOCATOR_SCHEMA_VERSION, "locator_kind": "char_range",
            "owner": str(owner), "start": int(start), "end": int(end)}


def table_cell_locator(owner: Any, *, table_ref: Any, row_index: Any, column_index: Any) -> dict:
    """表格单元的 locator（表引用 + 行列下标）。"""
    return {"locator_schema": LOCATOR_SCHEMA_VERSION, "locator_kind": "table_cell",
            "owner": str(owner), "table_ref": str(table_ref),
            "row_index": int(row_index), "column_index": int(column_index)}


def whole_payload_locator(owner: Any) -> dict:
    """整份 payload 的 locator（只指向载体身份，不声称字符位置）。"""
    return {"locator_schema": LOCATOR_SCHEMA_VERSION, "locator_kind": "whole_payload",
            "owner": str(owner)}


def validate_locator(locator_ref: Any, what: str) -> dict | None:
    """current wire 的 locator 校验（**唯一**入口）：只接受带标签的 `loc-1` 联合。

    拒绝：裸三元组（`loc-0` legacy wire）、`locator_schema` 不是 `loc-1` 的载荷、未知变体、
    变体字段缺失/多余、区间语义不成立。legacy 载荷必须经 `load_legacy_locator_for_audit`
    读取——那是**另一个**函数，读出来的对象也进不了 current wire。
    """
    if locator_ref is None:
        return None
    if not isinstance(locator_ref, Mapping):
        raise NarrativeSchemaError(
            f"{what}.locator_ref 必须是带标签的 locator 对象（`loc-1`）；裸三元组是 `loc-0` "
            f"legacy wire，只能经 load_legacy_locator_for_audit 读取，不得静默重解释，"
            f"得到 {type(locator_ref).__name__}")
    fields = set(locator_ref)
    schema = locator_ref.get("locator_schema")
    if schema == LEGACY_LOCATOR_SCHEMA_VERSION:
        raise NarrativeSchemaError(
            f"{what}.locator_ref 是 `loc-0` legacy locator：它只能用于只读审计，"
            "不得进入任何 current 决定/正文")
    if schema != LOCATOR_SCHEMA_VERSION:
        raise NarrativeSchemaError(
            f"{what}.locator_ref.locator_schema 必须是 {LOCATOR_SCHEMA_VERSION!r}，"
            f"得到 {schema!r}")
    kind = locator_ref.get("locator_kind")
    if kind not in LOCATOR_VARIANT_FIELDS:
        raise NarrativeSchemaError(
            f"{what}.locator_ref.locator_kind 必须是 {LOCATOR_KINDS} 之一，得到 {kind!r}")
    expected = {"locator_schema", "locator_kind", *LOCATOR_VARIANT_FIELDS[kind]}
    if fields != expected:
        raise NarrativeSchemaError(
            f"{what}.locator_ref[{kind}] 的字段必须恰为 {sorted(expected)}，"
            f"得到 {sorted(fields)}")
    owner = _require_locator_owner(locator_ref.get("owner"), what)
    if kind == "char_range":
        start = _locator_int(locator_ref.get("start"), what, "start")
        end = _locator_int(locator_ref.get("end"), what, "end")
        if start >= end:
            raise NarrativeSchemaError(
                f"{what}.locator_ref[char_range] 必须为 0 <= start < end 的半开字符区间，"
                f"得到 {(start, end)!r}")
        return char_range_locator(owner, start, end)
    if kind == "block_range":
        first = _locator_int(locator_ref.get("first"), what, "first")
        last = _locator_int(locator_ref.get("last"), what, "last")
        if first > last:
            raise NarrativeSchemaError(
                f"{what}.locator_ref[block_range] 必须为 0 <= first <= last 的**闭**块区间"
                f"（单块 first == last 合法），得到 {(first, last)!r}")
        return block_range_locator(owner, first, last)
    if kind == "table_cell":
        table_ref = locator_ref.get("table_ref")
        if not isinstance(table_ref, str) or not table_ref.strip():
            raise NarrativeSchemaError(
                f"{what}.locator_ref[table_cell].table_ref 必须是非空字符串")
        return table_cell_locator(
            owner, table_ref=table_ref,
            row_index=_locator_int(locator_ref.get("row_index"), what, "row_index"),
            column_index=_locator_int(locator_ref.get("column_index"), what, "column_index"))
    return whole_payload_locator(owner)


def locator_sort_key(locator_ref: Any) -> tuple:
    """locator 的规范比较键（逐字段，含变体标签）。

    比较 locator 一律经它：`loc-1` 的两个变体即使 owner 相同也不是同一个定位，直接比较
    dict 的顺序/缺字段会得到不稳定结果。
    """
    if locator_ref is None:
        return ()
    if not isinstance(locator_ref, Mapping):
        raise NarrativeSchemaError(
            "locator_sort_key 只接受带标签的 current locator（legacy 载荷请先经 "
            "load_legacy_locator_for_audit，且不得用于比较 current 语义）")
    kind = str(locator_ref.get("locator_kind") or "")
    fields = LOCATOR_VARIANT_FIELDS.get(kind)
    if fields is None:
        raise NarrativeSchemaError(f"locator_sort_key 不认识 locator_kind={kind!r}")
    return (str(locator_ref.get("locator_schema") or ""), kind,
            tuple(str(locator_ref.get(f)) for f in fields))


def load_legacy_locator_for_audit(value: Any) -> dict:
    """读取 `loc-0`（裸三元组 `(owner, start, end)`）**只读审计视图**。

    两件事刻意分开：
    1. legacy 三元组**只**有过一种语义——半开字符区间（当时的校验是 `0 <= start < end`）；
       因此这里把它读成 `char_range`，**绝不**推断成闭块区间。「`(0,0)` 是不是单块」在
       legacy wire 上不可判定，猜它就是被禁止的静默重解释。
    2. 读出来的对象带 `locator_schema="loc-0"`，因此 `validate_locator` 会拒收它——审计视图
       不能回流进任何 current 决定、正文或指纹。
    """
    if isinstance(value, Mapping):
        if value.get("locator_schema") != LEGACY_LOCATOR_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                "load_legacy_locator_for_audit 只接受 `loc-0` 载荷")
        values = (value.get("owner"), value.get("start"), value.get("end"))
    else:
        values = tuple(value or ())
    if len(values) != 3:
        raise NarrativeSchemaError(
            f"legacy locator 必须是 (owner, char_start, char_end) 三元组，得到 {value!r}")
    owner = _require_locator_owner(values[0], "legacy locator")
    start = _locator_int(values[1], "legacy locator", "start")
    end = _locator_int(values[2], "legacy locator", "end")
    if start >= end:
        raise NarrativeSchemaError(
            f"legacy locator 是半开字符区间（0 <= start < end），得到 {(start, end)!r}："
            "闭块区间从未存在于 legacy wire，不得在此推断")
    return {"locator_schema": LEGACY_LOCATOR_SCHEMA_VERSION, "locator_kind": "char_range",
            "owner": owner, "start": start, "end": end}


def validate_support_edge_shape(*, authority_kind: str, support_semantics: str,
                                authorization_path: str, fact_fields: Mapping[str, Any],
                                material_id: Any, payload_ref: Any, locator_ref: Any,
                                what: str) -> tuple[dict | None, Any]:
    """四元 authority union 的**唯一**边形状校验（proposal 与 accepted binding 共用）。

    返回 `(规范化后的 locator_ref, 规范化后的 payload_ref)`。三条轴相互正交，逐分支的
    required/forbidden 以 §6.2.3 的表为准；这里的每一条拒绝都是 fail-closed。
    """
    _require_enum(authority_kind, AUTHORITY_KINDS, f"{what}.authority_kind")
    _require_enum(support_semantics, SUPPORT_SEMANTICS, f"{what}.support_semantics")
    _require_enum(authorization_path, AUTHORIZATION_PATHS, f"{what}.authorization_path")

    # 轴 2（support_semantics）与声明路径必须自洽：context 只能声明 context_only；
    # factual 不得声明 context_only。三个角色不构成第四种组合。
    if support_semantics == "context":
        if authorization_path != "context_only":
            raise NarrativeSchemaError(
                f"{what}: support_semantics=context 只允许 authorization_path=context_only，"
                f"得到 {authorization_path!r}（context 不得声明事实授权路径）")
    elif authorization_path == "context_only":
        raise NarrativeSchemaError(
            f"{what}: support_semantics={support_semantics!r} 不得声明 authorization_path="
            "context_only（context_only 只属于 context 边）")

    # 路径 B 只存在于 topic_pack（§6.2.3）：其余 authority 的正式采纳路径是「先成为 Pack
    # material，再按 topic_pack 路径 B 记」，不得就地伪装成别的 kind 的路径 B。
    if authorization_path == "path_b_material_derived" and authority_kind != "topic_pack":
        raise NarrativeSchemaError(
            f"{what}: authority_kind={authority_kind!r} 不存在 path_b_material_derived；"
            "正式采纳为 Pack material 后必须改记 topic_pack 路径 B")

    present_facts = {name for name in ALL_FACT_FIELDS if fact_fields.get(name)}
    expected_fact_field = FACT_FIELD_BY_AUTHORITY_KIND[authority_kind]
    material = material_id if material_id else None
    payload = payload_ref if payload_ref else None
    locator = _validate_locator(locator_ref, what)
    # payload_ref 只在有 material 载体时才能解析（与 `ClaimSupportRef` 同一口径）。
    if authority_kind == "topic_pack" and payload is not None and material is None:
        raise NarrativeSchemaError(
            f"{what}: payload_ref 只在绑定 material_id 时允许（否则引用无载体可解析）")

    if authorization_path == "path_a_prevalidated":
        if not fact_fields.get(expected_fact_field):
            raise NarrativeSchemaError(
                f"{what}: 路径 A 必须绑定 {expected_fact_field}（该 authority kind 的"
                "预验证事实身份）")
        foreign = sorted(present_facts - {expected_fact_field})
        if foreign:
            raise NarrativeSchemaError(
                f"{what}: 路径 A 的 {authority_kind} 边不得携带 {foreign}（authority kind 混装）")
        if authority_kind == "financial_pack" and material is not None:
            raise NarrativeSchemaError(f"{what}: financial_pack 边不得携带 material_id")
        if authority_kind in ("evidence_note", "external_snapshot") and material is not None:
            raise NarrativeSchemaError(
                f"{what}: {authority_kind} 边不得携带 material_id（不得虚构 Topic material）")
        if authority_kind == "external_snapshot":
            if not locator:
                raise NarrativeSchemaError(
                    f"{what}: external_snapshot 路径 A 必须绑定 exact locator")
            payload = _validate_snapshot_ref(payload, what)
        elif authority_kind == "evidence_note":
            if not locator:
                raise NarrativeSchemaError(
                    f"{what}: evidence_note 路径 A 必须绑定 exact locator（note artifact 定位）")
        return locator, payload

    if authorization_path == "path_b_material_derived":
        if present_facts:
            raise NarrativeSchemaError(
                f"{what}: 路径 B 不得声称已有预验证事实身份（{sorted(present_facts)} 非空）；"
                "它只授权非高风险的描述性原子断言")
        if material is None:
            raise NarrativeSchemaError(
                f"{what}: 路径 B 必须绑定 exact ResearchMaterial（material_id）")
        # §四.1：路径 B 的支撑边必须是**闭合**的——载体身份、payload 引用与精确 locator 与
        # material 身份同在一条边上。「只给 material_id、其余回查 manifest」把「这条边绑定了
        # 什么」变成下游重新拼接的结果：边上少一个字段，谁都看不出来，验收也无法逐边复核。
        if payload is None:
            raise NarrativeSchemaError(
                f"{what}: 路径 B 必须绑定该 material 自己的 payload_ref（载体身份不是可选注解）")
        if not locator:
            raise NarrativeSchemaError(
                f"{what}: 路径 B 必须绑定该 material 的 exact locator（`loc-1` tagged union）")
        return locator, payload

    # context_only：不允许任何 fact identity（四条 fact 字段全禁），并按键要求 container/
    # provenance/payload/locator 的合法定位方式。
    if present_facts:
        raise NarrativeSchemaError(
            f"{what}: context 边不得携带任何 fact identity（{sorted(present_facts)}）；"
            "context 只验证背景/结构/衔接，不授权事实")
    if authority_kind == "topic_pack":
        if material is None:
            raise NarrativeSchemaError(
                f"{what}: topic_pack context 边必须绑定真实 ResearchMaterial（material_id）")
        # §四.2：context 边与路径 B 一样必须是闭合的（同样的载体身份 + payload + locator），
        # 区别只在它**没有**事实身份、也不产生蕴含决定。
        if payload is None:
            raise NarrativeSchemaError(
                f"{what}: topic_pack context 边必须绑定该 material 自己的 payload_ref")
        if not locator:
            raise NarrativeSchemaError(
                f"{what}: topic_pack context 边必须绑定该 material 的 exact locator")
    elif authority_kind == "financial_pack":
        if material is not None:
            raise NarrativeSchemaError(f"{what}: financial_pack context 边不得携带 material_id")
        if payload is None:
            raise NarrativeSchemaError(
                f"{what}: financial_pack context 边必须绑定 financial payload_ref")
    elif authority_kind == "evidence_note":
        if material is not None:
            raise NarrativeSchemaError(f"{what}: evidence_note context 边不得携带 material_id")
        if not locator:
            raise NarrativeSchemaError(
                f"{what}: evidence_note context 边必须绑定 exact locator")
    else:  # external_snapshot
        if material is not None:
            raise NarrativeSchemaError(f"{what}: external_snapshot context 边不得携带 material_id")
        if not locator:
            raise NarrativeSchemaError(
                f"{what}: external_snapshot context 边必须绑定 exact locator")
        payload = _validate_snapshot_ref(payload, what)
    return locator, payload


def _subject_matches_semantics(binding_subject_kind: str, support_semantics: str) -> bool:
    return (support_semantics == "factual" and binding_subject_kind == "claim_candidate") or (
        support_semantics == "context" and binding_subject_kind == "narrative_draft_unit")


@dataclass(frozen=True)
class ClaimCandidate:
    """门前**原子命题候选**（§6.3）。

    identity 只含 schema/version、规范化候选文本与类型、task/section/company/`report_as_of`/
    Contract 与 draft revision。**不得**含 support/material/payload/locator/citation 或任何
    决定 ID —— 本类根本没有这些字段，因此「候选携带未来身份」在类型层不可表达。
    """

    candidate_id: str
    schema_version: str
    draft_revision: str
    task_id: str
    section_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    claim_text: str
    fact_type: str

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"ClaimCandidate.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        for name in ("draft_revision", "task_id", "section_id", "company_id", "report_as_of",
                     "contract_version", "contract_fingerprint", "claim_text", "fact_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise NarrativeSchemaError(f"ClaimCandidate.{name} 不得为空")
        expected = derive_claim_candidate_id(self)
        if self.candidate_id != expected:
            raise NarrativeSchemaError(
                f"ClaimCandidate.candidate_id 与内容不符：声明 {self.candidate_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "draft_revision": self.draft_revision,
            "task_id": self.task_id,
            "section_id": self.section_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "claim_text": self.claim_text,
            "fact_type": self.fact_type,
        }

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"candidate_id": self.candidate_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ClaimCandidate":
        fields = ("draft_revision", "task_id", "section_id", "company_id", "report_as_of",
                  "contract_version", "contract_fingerprint", "claim_text", "fact_type")
        body = {k: kwargs.get(k) or "" for k in fields}
        body["schema_version"] = NARRATIVE_SCHEMA_VERSION
        body["candidate_id"] = derive_claim_candidate_id(_WireView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "ClaimCandidate":
        d = _reject_unknown(d, {"candidate_id", "schema_version", "draft_revision", "task_id",
                                "section_id", "company_id", "report_as_of", "contract_version",
                                "contract_fingerprint", "claim_text", "fact_type"},
                            "ClaimCandidate")
        return cls(
            candidate_id=_require_nonempty(d.get("candidate_id"), "ClaimCandidate.candidate_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "ClaimCandidate.schema_version"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                            "ClaimCandidate.draft_revision"),
            task_id=d.get("task_id") or "", section_id=d.get("section_id") or "",
            company_id=d.get("company_id") or "", report_as_of=d.get("report_as_of") or "",
            contract_version=d.get("contract_version") or "",
            contract_fingerprint=d.get("contract_fingerprint") or "",
            claim_text=d.get("claim_text") or "", fact_type=d.get("fact_type") or "")


def derive_claim_candidate_id(candidate: Any) -> str:
    body = (candidate.identity_body() if isinstance(candidate, ClaimCandidate)
            else candidate.body)
    return content_id("ccand_", body)


@dataclass(frozen=True)
class NarrativeDraftUnit:
    """门前**叙述单元**（§6.3）：context support 的**唯一**合法 target。

    它不是最终 Narrative，也不承载任何事实身份：本类没有 Claim ID、没有 fact ID、没有
    accepted binding ID 字段，因此「context 单元携带事实身份」在类型层不可表达。
    """

    draft_unit_id: str
    schema_version: str
    draft_revision: str
    section_id: str
    index: int
    unit_kind: str
    text: str

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"NarrativeDraftUnit.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        _require_nonempty(self.draft_revision, "NarrativeDraftUnit.draft_revision")
        _require_nonempty(self.section_id, "NarrativeDraftUnit.section_id")
        _require_enum(self.unit_kind, DRAFT_UNIT_KINDS, "NarrativeDraftUnit.unit_kind")
        if not isinstance(self.index, int) or self.index < 0:
            raise NarrativeSchemaError("NarrativeDraftUnit.index 必须是 >= 0 的整数")
        if not isinstance(self.text, str) or not self.text.strip():
            raise NarrativeSchemaError("NarrativeDraftUnit.text 不得为空")
        expected = derive_draft_unit_id(self)
        if self.draft_unit_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeDraftUnit.draft_unit_id 与内容不符：声明 {self.draft_unit_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {"schema_version": self.schema_version, "draft_revision": self.draft_revision,
                "section_id": self.section_id, "index": self.index,
                "unit_kind": self.unit_kind, "text": self.text}

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"draft_unit_id": self.draft_unit_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "NarrativeDraftUnit":
        body = {"draft_revision": kwargs.get("draft_revision") or "",
                "section_id": kwargs.get("section_id") or "",
                "index": int(kwargs.get("index") if kwargs.get("index") is not None else 0),
                "unit_kind": kwargs.get("unit_kind") or "",
                "text": kwargs.get("text") or "",
                "schema_version": NARRATIVE_SCHEMA_VERSION}
        body["draft_unit_id"] = derive_draft_unit_id(_WireView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeDraftUnit":
        d = _reject_unknown(d, {"draft_unit_id", "schema_version", "draft_revision", "section_id",
                                "index", "unit_kind", "text"}, "NarrativeDraftUnit")
        return cls(
            draft_unit_id=_require_nonempty(d.get("draft_unit_id"),
                                            "NarrativeDraftUnit.draft_unit_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "NarrativeDraftUnit.schema_version"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "NarrativeDraftUnit.draft_revision"),
            section_id=_require_nonempty(d.get("section_id"), "NarrativeDraftUnit.section_id"),
            index=int(d.get("index") if d.get("index") is not None else -1),
            unit_kind=_require_nonempty(d.get("unit_kind"), "NarrativeDraftUnit.unit_kind"),
            text=d.get("text") or "")


def derive_draft_unit_id(unit: Any) -> str:
    body = unit.identity_body() if isinstance(unit, NarrativeDraftUnit) else unit.body
    return content_id("ndu_", body)


@dataclass(frozen=True)
class NaturalProseDraftUnit:
    """门前**自然草稿单元**（narr-8，M930-3 指令 E 第 3 项）：材料驱动的可保留散文。

    **它是什么。** Writer 在进入任何 gate **之前**写下的自然散文单位：一段连续正文，外加两样
    可追溯登记——这段正文**是从哪儿长出来的**（出处，见下），以及这段正文里的事实原子**各自**
    提交的候选（`atom_candidate_ids`，有序）。门后组织器以**它**作为表达基础，而不是以一串
    Claim 文本作为表达基础。

    **出处是两条**互斥**的轴（`narr-8` / `pprov-1`），恰有一个非空：**

      * `source_member_refs`：出处是 manifest 的**材料行**（`manifest_member_ref`，Pack 材料）；
      * `source_fact_refs`：出处是**权威事实行**（`fact_provenance_ref`）。

    两条轴为什么必须分开、且必须互斥：公司/行业节的正文从 Pack 材料原文里长出来（此时「出处=材料」
    是唯一正确的答案，事实通过候选与支撑边进入，不经这一格）；而**财务节的精确材料清单合法地为
    空**（`FinancialFactPack` 的事实不经过 Pack 材料），那一节里「出处=材料」没有任何合法取值。
    只留一条材料轴，财务节要么伪造一条 `ResearchMaterial`（明令禁止），要么这一段正文根本写不
    出来——`pw-15` 下就是后者。两轴**同时**非空同样不允许：那样这一格会同时表达两种身份，
    读者再也分不清这段话是从材料正文概括来的、还是从一条预验证事实派生的。两轴命名空间
    （`wmmref_` / `fprov_`）**不相交**由 `provenance_axis_of` 一处判定。

    **出处不是授权。** 无论哪一条轴，它只回答「这段话从哪儿来」：正文里每一处事实性内容仍必须
    由它声明的候选（及其路径 A / 路径 B 支撑边）承载，出处本身不给任何数字、期间或结论背书；
    事实侧的出处更不构成路径 A——授权的唯一入口仍是候选的支撑边。

    **为什么必须是单独的类，不能在 `NarrativeDraftUnit` 上加字段。** 那个类的定义性约束是
    「context support 的**唯一**合法 target，且不承载任何事实身份」（它连 Claim ID 字段都没有）。
    自然草稿恰恰相反：它存在的理由就是携带事实原子。若把 `atom_candidate_ids` 放进
    `NarrativeDraftUnit`，context 单元就同时具备了声明事实原子的能力，而 context 绑定**不经过
    蕴含门**——那等于凭空开出一条「用 context 绑定授权事实」的通道。两个身份必须分开。

    **它不是第三种 binding subject。** `subject_keys` 仍然只出 `claim_candidate` 与
    `narrative_draft_unit` 两类键；本单元**不**出现在 proposal 的 target 里，也**不**参与
    aggregate binding decision 的基数。它只是「候选从哪段材料、哪句话里长出来」的登记层，
    因此 aggregate 基数、proposal 集合等式与 `cer-*` 蕴含门的输入一律不变。

    **为什么不带这些字段（类型层不可表达）：** `binding_decision_id`、`entailment_decision_id`、
    `accepted_support_binding_id`、`section_claim_id`、最终 Narrative ID、`section_result_id`、
    `sentence_id`。草稿不得靠引用未来的决定或产物来证明自己合法——方向只能从草稿指向候选。
    """

    prose_unit_id: str
    schema_version: str
    draft_revision: str
    section_id: str
    index: int
    text: str
    #: 出处轴一：这段正文所表达的 manifest 成员（`manifest_member_ref` 键，有序、去重）。
    source_member_refs: tuple[str, ...]
    #: 出处轴二（`narr-8`）：这段正文所表达的**权威事实行**（`fact_provenance_ref` 键，有序、
    #: 去重）。与 `source_member_refs` **恰有一个非空**——见类 docstring。
    source_fact_refs: tuple[str, ...]
    #: 这段正文里的事实原子各自提交的候选 ID（有序、去重、**不得为空**）。空表示这段正文不含
    #: 任何已审原子——那是未审文本，不得进入草稿。原子顺序即 prose 内的出现顺序。
    atom_candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"NaturalProseDraftUnit.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        _require_nonempty(self.draft_revision, "NaturalProseDraftUnit.draft_revision")
        _require_nonempty(self.section_id, "NaturalProseDraftUnit.section_id")
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise NarrativeSchemaError("NaturalProseDraftUnit.index 必须是 >= 0 的整数")
        if not isinstance(self.text, str) or not self.text.strip():
            raise NarrativeSchemaError("NaturalProseDraftUnit.text 不得为空")
        for name in ("source_member_refs", "source_fact_refs", "atom_candidate_ids"):
            values = _str_tuple(getattr(self, name), f"NaturalProseDraftUnit.{name}")
            if len(set(values)) != len(values):
                raise NarrativeSchemaError(f"NaturalProseDraftUnit.{name} 含重复元素")
            object.__setattr__(self, name, values)
        # 两轴互斥（`pprov-1`）：恰有一个非空。空两轴 = 没有出处的散句（凭空生成）；两轴都非空 =
        # 这一格同时表达两种身份，「这段正文从哪儿长出来」不再有唯一答案。
        if not self.source_member_refs and not self.source_fact_refs:
            raise NarrativeSchemaError(
                "NaturalProseDraftUnit 必须恰有一条出处轴非空（source_member_refs 或 "
                "source_fact_refs）：空两轴 = 无出处的散句，它不是材料驱动写作")
        if self.source_member_refs and self.source_fact_refs:
            raise NarrativeSchemaError(
                "NaturalProseDraftUnit 的两条出处轴不得同时非空（pprov-1：出处只能落在材料行或"
                "权威事实行之一，两种身份不得混在同一格）")
        if not self.atom_candidate_ids:
            raise NarrativeSchemaError(
                "NaturalProseDraftUnit.atom_candidate_ids 不得为空（空集 = 无原子的散文，不得进入草稿）")
        # 前缀 ⇔ 轴：轴上每一条键必须真的落在该轴的命名空间里（判据只此一处，
        # `provenance_axis_of`）。这一条让「把事实键写进材料轴」这种混用在类型层就不可表达。
        for name, axis in (("source_member_refs", "material"), ("source_fact_refs", "fact")):
            for ref in getattr(self, name):
                if provenance_axis_of(ref) != axis:
                    raise NarrativeSchemaError(
                        f"NaturalProseDraftUnit.{name} 里的 {ref!r} 不属于 {axis!r} 轴"
                        "（两轴的命名空间不相交，键写进哪一格就只能是哪一格）")
        expected = derive_natural_prose_unit_id(self)
        if self.prose_unit_id != expected:
            raise NarrativeSchemaError(
                f"NaturalProseDraftUnit.prose_unit_id 与内容不符：声明 {self.prose_unit_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {"schema_version": self.schema_version, "draft_revision": self.draft_revision,
                "section_id": self.section_id, "index": self.index, "text": self.text,
                "source_member_refs": list(self.source_member_refs),
                "source_fact_refs": list(self.source_fact_refs),
                "atom_candidate_ids": list(self.atom_candidate_ids)}

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"prose_unit_id": self.prose_unit_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "NaturalProseDraftUnit":
        body = {"draft_revision": kwargs.get("draft_revision") or "",
                "section_id": kwargs.get("section_id") or "",
                "index": int(kwargs.get("index") if kwargs.get("index") is not None else 0),
                "text": kwargs.get("text") or "",
                "source_member_refs": list(kwargs.get("source_member_refs") or ()),
                "source_fact_refs": list(kwargs.get("source_fact_refs") or ()),
                "atom_candidate_ids": list(kwargs.get("atom_candidate_ids") or ()),
                "schema_version": NARRATIVE_SCHEMA_VERSION}
        body["prose_unit_id"] = derive_natural_prose_unit_id(_WireView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "NaturalProseDraftUnit":
        d = _reject_unknown(d, {"prose_unit_id", "schema_version", "draft_revision", "section_id",
                                "index", "text", "source_member_refs", "source_fact_refs",
                                "atom_candidate_ids"},
                            "NaturalProseDraftUnit")
        return cls(
            prose_unit_id=_require_nonempty(d.get("prose_unit_id"),
                                            "NaturalProseDraftUnit.prose_unit_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "NaturalProseDraftUnit.schema_version"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "NaturalProseDraftUnit.draft_revision"),
            section_id=_require_nonempty(d.get("section_id"), "NaturalProseDraftUnit.section_id"),
            index=int(d.get("index") if d.get("index") is not None else -1),
            text=d.get("text") or "",
            source_member_refs=tuple(d.get("source_member_refs") or ()),
            source_fact_refs=tuple(d.get("source_fact_refs") or ()),
            atom_candidate_ids=tuple(d.get("atom_candidate_ids") or ()))


def derive_natural_prose_unit_id(unit: Any) -> str:
    body = (unit.identity_body() if isinstance(unit, NaturalProseDraftUnit) else unit.body)
    return content_id("npdu_", body)


def validate_natural_prose_mapping(*, natural_prose_draft: "Sequence[NaturalProseDraftUnit]",
                                   claim_candidates: "Sequence[ClaimCandidate]",
                                   member_refs: "Sequence[str]",
                                   section_id: str, draft_revision: str,
                                   proposed_support_refs: "Sequence[ProposedSupportRef] | None"
                                   = None,
                                   material_manifest: "WriterMaterialManifest | None" = None
                                   ) -> tuple[str, ...]:
    """自然草稿与其候选集、manifest 成员集的**闭合核对**（**唯一**实现，返回 typed 问题列表）。

    四条等式/包含关系，缺一条「材料驱动写作」就退化回「Claim 文本拼接」：

    1. **原子双向闭合**：草稿里声明的原子集**恰好等于**候选集。多一个（声明了不存在的候选）
       是伪造映射；少一个（有候选却不在任何草稿单元里）说明该候选不是从正文长出来的，
       而是旁路塞进来的。
    2. **成员隶属（材料侧）**：落在材料轴上的单元，每个 `source_member_refs` 必须属于本 draft 的
       exact manifest。材料身份不得跨清单借用（同 ID 错 Evidence Set 正是靠这一条在最上游就被挡住）。
       **事实侧（`narr-8`）不在本层核对，如实声明**：本层手上只有 manifest 的成员集，没有**权威
       事实登记面**，因此对事实轴只能核到「键属于事实轴命名空间」（`NaturalProseDraftUnit.
       __post_init__` 已按 `provenance_axis_of` 逐条核）与「两轴互斥」。事实侧的**隶属**核对在两处
       fail-closed 落地：`pack_writer._parse_one_prose_unit`（短别名 → 身份，只认本次请求声明的
       事实行）与 `pack_writer._build_natural_prose_units`（构造单元时再回查权威事实表）。
       在这里凭本地没有的登记面「顺手放过」是 fail-open 的另一种写法，因此宁可如实说「本层不核」。
    3. **归属（occurrence，`npr-1`）与顺序**：每个单元的 `draft_revision`/`section_id` 必须与本节
       一致，`index` 必须等于其序号（不得乱序拼装）；**同一候选可以在多个草稿单元里各出现一次**
       （`PROSE_OCCURRENCE_POLICY_VERSION`），但**每一次出现**都必须在该候选**自己的** factual
       提案里，找到一条落在**同一份来源**上的边（成员的 `(pack_id, material_id)` 两把键 + 声明的
       `document_id` / `document_version`，或事实轴上的权威事实行）。核不上的那一次出现即拒；
       **同一单元内**重复声明同一条候选仍拒（那不是「两个版本各写了一遍」，是同一格把一件事说了
       两遍）——但那一条在**类型层**就已强制（`NaturalProseDraftUnit` 是 frozen 的，
       `atom_candidate_ids` 在 `__post_init__` 去重），本层不再重复判一次，见下。
    4. **缺席的核对面不得换成放宽**：调用方没给 `proposed_support_refs` / `material_manifest` 时，
       本层手上就没有「这条候选的支撑边落在哪份来源上」这张表，于是本层只能退到**唯一性**
       （同一候选被多个单元声明即拒）。生产路径（`SectionDraft.__post_init__`）**一律**提供两份
       输入，因此 `npr-1` 的逐 occurrence 核对在正式链上总是生效；缺输入时保持 `npr-0` 的严格度，
       而不是把「核不了」读成「核过了」。

    **来源角色（`source_role`）怎么核，如实说明**：本层**不比角色字符串**，比的是角色的**决定项**
    ——`document_id` / `document_version`。理由是角色按构造是 `document_id` 的单值函数：
    `SRS.document_roles` 在「同一 `document_id` 在本节不同 Pack 上角色不一致」时当场抛，
    因此「文档同一」蕴涵「角色同一」、「文档不同」蕴涵「角色可能不同（且正是 `current_state_source`
    / `history_and_conflict_source` 分开的那一格）」。拿同一份台账同时算出两侧角色再比，是一个
    **恒真**的比较（`srsc` 模块头把这种检查叫「假门」，并写明假门比没有门更坏）；故本层不写它。
    两侧的文档轴各自独立读出：单元侧取自 manifest 成员的 `locator_ref`，边侧取自**边自己**的
    `locator_ref`（§四.1：路径 B 的边必须闭合，逐字带上该成员的 payload 与 exact locator）。

    空草稿返回空列表：`narr-7` 及更早（无草稿层的形态）的草稿没有这一层，那条路径的行为
    **一字未变**（唯一性/归属判据只在草稿非空时生效；`candidates ≠ ∅ ∧ 草稿 = ∅` 在**当前提案线
    格式**下已由 `pack_writer` 的 typed 失败在更上游挡住，见 `PROPOSAL_WIRE_CURRENT`）。
    """
    problems: list[str] = []
    units = tuple(natural_prose_draft or ())
    if not units:
        return ()
    manifest_refs = set(member_refs or ())
    candidate_ids = [c.candidate_id for c in (claim_candidates or ())]
    candidate_set = set(candidate_ids)
    declared: list[str] = []
    for pos, unit in enumerate(units):
        if unit.draft_revision != draft_revision or unit.section_id != section_id:
            problems.append(
                f"自然草稿单元 {unit.prose_unit_id} 的 draft_revision/section_id 与本节不符")
        if unit.index != pos:
            problems.append(f"自然草稿单元 {unit.prose_unit_id} 的 index 与顺序不符（不得乱序拼装）")
        # 材料轴的隶属在这一层核；事实轴只核到「键确实落在事实轴上」（两轴互斥与命名空间由
        # `NaturalProseDraftUnit.__post_init__` 逐条核），它的**隶属**由写入侧对着权威事实表核
        # ——见本函数 docstring 第 2 条，那里写明了「本层不核」的理由与本层手上没有的东西。
        for ref in unit.source_member_refs:
            if ref not in manifest_refs:
                problems.append(
                    f"自然草稿单元 {unit.prose_unit_id} 引用了本节 manifest 之外的成员 {ref!r}"
                    "（材料身份不得跨清单借用）")
        for ref in unit.source_fact_refs:
            if provenance_axis_of(ref) != "fact":
                problems.append(
                    f"自然草稿单元 {unit.prose_unit_id} 的事实侧出处 {ref!r} 不在事实轴命名空间里")
        declared.extend(unit.atom_candidate_ids)
    # ---- 归属：单元**内部**的重复（见 docstring 第 3 条末句） -------------------------
    # 这一条**不在这里**判：`NaturalProseDraftUnit` 是 frozen 的，`atom_candidate_ids` 的去重在
    # 类型层就已强制（`__post_init__` 的 `含重复元素`），因此「同一段里把一件事说了两遍」根本
    # 构造不出来。本层重复判一次只会得到一条永远走不到的分支，还会让读的人以为守卫在这里——
    # 全仓只有一处判它，就是那个构造器（`evals/test_m930_3_sentence_fidelity.py` §6b 钉住）。
    unknown = sorted(set(declared) - candidate_set)
    if unknown:
        problems.append(f"自然草稿声明了本节不存在的候选：{unknown}")
    uncovered = sorted(candidate_set - set(declared))
    if uncovered:
        problems.append(
            f"候选未被任何自然草稿单元声明：{uncovered}"
            "（候选必须从草稿正文长出，不得旁路塞入）")
    # ---- 归属：逐 occurrence 的支撑核对（`npr-1`） ----------------------------------
    checkable = proposed_support_refs is not None and material_manifest is not None
    if not checkable:
        # 核对面缺席：退到唯一性，不把「核不了」读成「核过了」。理由见 docstring 第 4 条。
        duplicated = sorted({cid for cid in declared if declared.count(cid) > 1})
        if duplicated:
            problems.append(
                f"同一候选被多个自然草稿单元声明：{duplicated}（本层没有收到支撑提案面/manifest，"
                f"因此不得按 `{PROSE_OCCURRENCE_POLICY_VERSION}` 逐 occurrence 核对——"
                "缺核对面时原子归属仍必须唯一）")
        return tuple(problems)
    edges = _factual_edges_by_candidate(proposed_support_refs or ())
    for unit in units:
        for candidate_id in dict.fromkeys(unit.atom_candidate_ids):
            if candidate_id not in candidate_set:
                continue  # 未知候选已由上一条统一报出，不重复报
            coordinates, broken = _prose_occurrence_coordinates(unit, material_manifest)
            if broken:
                # 读不出声明身份时**不**当成「没有声明」（那会把一条本来会判 mismatch 的边读成
                # match），因此在这里 fail-closed 而不是跳过这次核对。
                problems.append(
                    f"自然草稿单元 {unit.prose_unit_id} 的出处读不出声明身份：{broken}")
                continue
            mine = edges.get(candidate_id, ())
            if not any(_occurrence_matches_edge(coordinates, edge) for edge in mine):
                problems.append(
                    f"自然草稿单元 {unit.prose_unit_id}（第 {unit.index + 1} 段）声明了候选 "
                    f"{candidate_id}，但这条候选**自己**的支撑提案里没有一条落在本段的来源上："
                    f"本段出处 = {_coordinates_text(coordinates)}；该候选有 {len(mine)} 条 factual "
                    f"提案，出处分别是 {_edges_text(mine)}。同一条原子由**另一份材料/另一个版本**"
                    "写出来是**另一段草稿**（它的出处是这句话的一部分），不得拿一份材料的文字声明"
                    "一个只在另一份材料上取得过支撑的原子")
    return tuple(problems)


# ---------------------------------------------------------------------------
# `npr-1`：草稿原子归属（occurrence）的核对面
#
# 为什么要把这套东西单独写出来：归属核对要回答的是「**这一次**表达是从哪一行长出来的」，
# 而单元与支撑边各自只说自己那一半——单元说「我引用了这些成员」，边说「我绑定了这份材料」。
# 把两侧各自投影成**同一种**坐标（材料轴 / 事实轴各一项），核对就退化成「这两张表有没有交集」。
# 两侧的坐标都来自**它们自己声明的字段**，没有一处取自对方的结论：单元侧取自 manifest 成员的
# exact locator，边侧取自边自己的 locator（§四.1 要求路径 B 的边闭合，正是为了这一处能核）。
# ---------------------------------------------------------------------------

def _material_axis_coordinate(container_identity: Any, material_id: Any,
                              locator_ref: Any) -> dict:
    """材料轴坐标：`(容器, material_id)` + 该 locator **声明**的文档两轴（读不出即 `None`，不猜）。

    为什么容器也在坐标里：`manifest_member_ref` 把成员键定义成 `(pack_id, material_id)`，理由
    正是「裸 `material_id` 跨容器去重会把两个 Pack 里同名的 material 折叠成一条」。归属核对若
    只比 material_id，就在这一层重新制造了那道已被挡住的折叠——同 ID 错 Evidence Set 的正文会
    被读成「同一份材料」（`validate_natural_prose_mapping` docstring 第 2 条点名的正是这件事）。
    两侧的容器各取自**自己声明的字段**：单元侧取 manifest 成员的 `pack_id`，边侧取边的
    `authority_container_id`（路径 B 的边本来就逐字带上它所属的 Pack）。
    """
    from sections import source_role_scope as _SRS

    axes = _SRS.declared_axes_from_locator(locator_ref)
    return {"axis": "material", "container_identity": str(container_identity or ""),
            "material_id": str(material_id or ""),
            "document_id": axes[0] if axes else None,
            "document_version": axes[1] if axes else None}


def _fact_axis_coordinate(authority_kind: Any, container_identity: Any,
                          fact_id: Any) -> dict:
    """事实轴坐标：权威事实行的**内容指纹**（与单元侧 `source_fact_refs` 同一编码）。"""
    return {"axis": "fact",
            "ref": fact_provenance_ref(str(authority_kind), str(container_identity),
                                       str(fact_id))}


def _prose_occurrence_coordinates(unit: NaturalProseDraftUnit,
                                  material_manifest: "WriterMaterialManifest"
                                  ) -> tuple[tuple[dict, ...], str]:
    """一段草稿在**这次归属核对**里的出处坐标：`(坐标, 读不出的原因)`，恰有一个非空。

    单元的两条出处轴互斥（`pprov-1`，由 `__post_init__` 保证），因此本函数只投影非空的那一条：

      * 材料轴：每个成员引用回查 manifest 成员，取它的 `(material_id, locator 声明的文档两轴)`。
        成员不在清单里时**照样投影**（清单隶属已由调用方逐条报出，这里再抛会让同一件事出现两个
        原因），只是它的文档两轴为空洞；
      * 事实轴：键本身就是坐标（`fprov_*` 即 `(kind, container, fact)` 的内容指纹）。

    读不出**声明身份**（`SRS.declared_axes_from_locator` 对 evidence 容器拆不出两轴时抛）时返回
    原因串：那说明这一侧的 locator 被改过或被拼过，「读不出声明」与「声明为空」是两件事，
    静默退化成后者等于把一条本来会判 mismatch 的边读成 match。
    """
    from sections import source_role_scope as _SRS

    if unit.source_fact_refs:
        # 键本身就是坐标（`fprov_*` = `(kind, container, fact)` 的内容指纹），不重算一遍：
        # 语义相同的三元组必然给出同一个键，重算只会让「哪一处是权威编码」多一个答案。
        return (tuple({"axis": "fact", "ref": str(ref)}
                      for ref in unit.source_fact_refs), "")
    coordinates: list[dict] = []
    for ref in unit.source_member_refs:
        entry = material_manifest.entry_for(str(ref))
        try:
            coordinates.append(_material_axis_coordinate(
                getattr(entry, "pack_id", None), getattr(entry, "material_id", None),
                getattr(entry, "locator_ref", None)))
        except _SRS.SourceRoleScopeError as exc:
            return ((), str(exc))
    return (tuple(coordinates), "")


def _factual_edges_by_candidate(proposals: "Sequence[ProposedSupportRef]"
                                ) -> "dict[str, tuple[ProposedSupportRef, ...]]":
    """`candidate_id → 该候选的 factual 提案`（**只**取 factual：context 不授权事实）。"""
    index: dict[str, list[ProposedSupportRef]] = {}
    for proposal in proposals:
        if proposal.support_semantics != "factual":
            continue
        index.setdefault(str(proposal.binding_subject_id), []).append(proposal)
    return {key: tuple(value) for key, value in index.items()}


def _edge_coordinates(edge: ProposedSupportRef) -> tuple[dict, ...]:
    """一条 factual 提案 → 它在核对面上的坐标（材料轴 / 事实轴，恰取其一）。

    路径 B（`topic_pack` 材料派生）与 topic path A（Pack 预验证事实）都落在**材料轴**上：
    路径 A 的 material 锚点由该事实**自己的引用**派生（`_factual_proposal`），因此它同样是
    「这条边的正文从哪份材料长出来」。其余三种 authority 没有材料载体（校验器禁止它们带
    `material_id`），坐标落在事实轴上。
    """
    material_id = str(edge.material_id or "")
    if material_id:
        return (_material_axis_coordinate(edge.authority_container_id, material_id,
                                         edge.locator_ref),)
    field_name = FACT_FIELD_BY_AUTHORITY_KIND.get(str(edge.authority_kind))
    fact_id = str(getattr(edge, str(field_name), "") or "") if field_name else ""
    if not fact_id:
        return ()
    return (_fact_axis_coordinate(edge.authority_kind, edge.authority_container_id, fact_id),)


def _occurrence_matches_edge(coordinates: "Sequence[dict]", edge: ProposedSupportRef) -> bool:
    """这次出现是否落在该边**自己的**来源上（材料 ID + 文档版本，或同一条权威事实行）。

    材料轴的比对是**三步**，每一步都不是可省的一步：

      * `container_identity`（`pack_id`）必须逐字相同——它与 `material_id` 一起才构成
        `manifest_member_ref` 那把成员键（理由见 `_material_axis_coordinate`）；
      * `material_id` 必须逐字相同——它是跨容器去重的那把键，也是「同 ID 错 Evidence Set」
        最上游的挡板；
      * 两侧**都**读得出声明的文档两轴时再逐轴比一次（`document_id` / `document_version`）。
        材料身份按构造已蕴含文档版本（`harness.tree_materials` 的 material 身份含
        `document_version`），因此这一步是**冗余但独立**的复核：冗余可接受，恒真不可接受。
        读不出的那一侧（财务 / 外部快照的 locator 不是 evidence 容器）**不**当成「没有版本」
        ——它本来就没有这条轴，材料 ID 的相等就是这次比对的全部内容。
    """
    for mine in _edge_coordinates(edge):
        for theirs in coordinates:
            if mine["axis"] != theirs["axis"]:
                continue
            if mine["axis"] == "fact":
                if mine["ref"] == theirs["ref"]:
                    return True
                continue
            if not mine["material_id"] or mine["material_id"] != theirs["material_id"]:
                continue
            if (mine["container_identity"] != theirs["container_identity"]):
                continue
            if mine["document_id"] and theirs["document_id"]:
                if (mine["document_id"], mine["document_version"]) != (
                        theirs["document_id"], theirs["document_version"]):
                    continue
            return True
    return False


def _coordinates_text(coordinates: "Sequence[dict]") -> str:
    """坐标的可读投影（进拒绝原因，逐条列出这次出现到底落在哪一行上）。"""
    if not coordinates:
        return "（无）"
    return "、".join(
        (f"{item['ref']}" if item["axis"] == "fact"
         else (f"容器 {item['container_identity']} 的材料 {item['material_id']}"
               + (f"（文档 {item['document_id']}@{item['document_version']}）"
                  if item["document_id"] else "（无文档声明轴）")))
        for item in coordinates)


def _edges_text(edges: "Sequence[ProposedSupportRef]") -> str:
    """该候选自己的支撑边出处（进拒绝原因：读的人要能自己看出「有没有一条本该在此」）。"""
    if not edges:
        return "（无）"
    return "、".join(_coordinates_text(_edge_coordinates(edge)) or
                     f"（{edge.authority_kind} 边没有可核的来源坐标）"
                     for edge in edges)


def natural_prose_draft_digest(units: "Sequence[NaturalProseDraftUnit]") -> str:
    """自然草稿集的**单一**内容摘要（有序，**唯一**口径）。

    空集返回**空串**：`derive_draft_revision` 把空串当作「本节没有草稿层」而不进身份体，
    于是 `narr-6` 及更早的草稿修订取值一字不变。

    **为什么只摘「prose 文本 + 出处 + 顺序」，不摘 `identity_body()`。** 本摘要的**唯一**
    用途是进 `derive_draft_revision`，而 `draft_revision` 反过来是每个单元自己的字段——若摘要
    取 `identity_body()`（内含 `draft_revision`），就得到 `R = f(g(R))` 这个**不动点方程**，它一般
    无解：草稿层非空的 `SectionDraft` 将**根本无法构造**。这不是理论顾虑，是实测出来的：先按
    临时修订算出摘要 D1、推出 R、再用 R 重建单元得 D2，D1 ≠ D2 且新修订也不等于 R。

    与候选 id 的关系同理由排除：`atom_candidate_ids` 里的候选 id 同样含 `draft_revision`。
    因此本摘要**只**由不依赖本修订的三样东西构成：单元顺序、prose 文本、出处（`narr-8` 起是
    **两条轴**：材料侧与事实侧各自成一项——出处换了就是另一版正文，两轴都在摘要里）。

    **这样摘会漏掉什么（如实声明）。** 「prose 文本与出处都不变、只有原子→候选映射变了」时
    修订**不变**。但那种改动仍会换掉 `draft_id`：映射变了意味着某条候选的文本变了，而候选身份
    在 `SectionDraft.identity_body()` 里（`claim_candidate_ids`），于是「同一 `draft_id` 两版草稿」
    依旧不可表达——只是拦住它的那条线从「修订」换到了「draft 身份」。闭合核对
    （`validate_natural_prose_mapping`）在两条线上都逐单元生效。
    """
    return natural_prose_spec_digest(
        ({"index": u.index, "text": u.text, "source_member_refs": u.source_member_refs,
          "source_fact_refs": u.source_fact_refs}
         for u in (units or ())))


def natural_prose_spec_digest(specs: "Iterable[Mapping[str, Any]]") -> str:
    """`(index, text, source_member_refs, source_fact_refs)` 四元组序列的摘要——与
    `natural_prose_draft_digest` **同一实现**（后者只是把单元投影成这四项再调本函数）。

    存在的**唯一**理由：候选身份（`ccand_*`）反过来依赖 `draft_revision`，因此写入侧必须在
    「候选还不存在」的那一刻先定出修订。上面已经说明草稿摘要**只**由这四项构成（不含
    `draft_revision`、不含候选 id），所以「先按四元组算」与「再按单元算」不可能给出两个答案
    ——这一点由本函数是**唯一**实现保证：两条口径各写一份，正是最容易漂移、且漂移后表现为
    「草稿层非空时构造不出来」的那类缺陷（见上一段的不动点）。

    `narr-8` 把出处拆成两条轴，投影因此是**四**项而不是三项：只摘材料侧会让「同一段正文、出处
    从材料行换成权威事实行」摘出同一个摘要——那是**另一版**正文，必须换一个修订。两轴互斥
    （`pprov-1`）保证恰有一项非空，因此多出来的这一项不是噪声。

    空集返回空串（与 `natural_prose_draft_digest` 同一条口径：空串 = 本节没有草稿层）。
    """
    rows = [{"index": int(spec["index"]), "text": str(spec["text"]),
             "source_member_refs": [str(r) for r in (spec["source_member_refs"] or ())],
             "source_fact_refs": [str(r) for r in (spec.get("source_fact_refs") or ())]}
            for spec in (specs or ())]
    if not rows:
        return ""
    return content_id("npdd_", {"units": rows})


def derive_draft_revision_with_natural_prose(*, natural_prose_draft: "Sequence[NaturalProseDraftUnit]",
                                             **revision_kwargs: Any) -> str:
    """草稿层在场时 `draft_revision` 的**唯一**推导入口（含必要的**两遍**顺序）。

    为什么需要两遍：单元的 `draft_revision` 字段就是本函数的返回值，而摘要只取 prose 内容
    （见 `natural_prose_draft_digest`）。因此顺序是**先**用任意修订构造单元、由**内容**算摘要、
    推出修订，**再**用真正的修订重建单元。少了第二步，构造出的 `SectionDraft` 会因为单元携带
    临时修订而被 `__post_init__` 拒。

    `**revision_kwargs` 与 `derive_draft_revision` 逐项相同（不含 `natural_prose_digest`——
    它由本函数从草稿算出）。
    """
    digest = natural_prose_draft_digest(natural_prose_draft)
    return derive_draft_revision(natural_prose_digest=digest, **revision_kwargs)


@dataclass(frozen=True)
class ProposedSupportRef:
    """门前**支撑提案**（§6.3.1）：只声明封闭授权路径与来源字段。

    **禁止字段**（本类不存在，即类型层不可表达）：`accepted_support_binding_id`、
    `binding_decision_id`、`entailment_decision_id`、`section_claim_id`、最终 Narrative ID、
    `section_result_id`。proposal 不得靠引用未来决定来证明自己合法。
    """

    proposed_support_id: str
    schema_version: str
    binding_subject_kind: str
    binding_subject_id: str
    draft_revision: str
    manifest_id: str
    manifest_fingerprint: str
    authority_kind: str
    authority_container_id: str
    source_identity: str
    provenance_identity: str
    support_role: str
    support_semantics: str
    authorization_path: str
    content_fingerprint: str
    dependency_fingerprint: str
    fact_id: str | None = None
    financial_fact_id: str | None = None
    note_fact_id: str | None = None
    external_fact_id: str | None = None
    material_id: str | None = None
    payload_ref: dict | None = None
    locator_ref: dict | None = None

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"ProposedSupportRef.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        for name in ("binding_subject_id", "draft_revision", "manifest_id",
                     "manifest_fingerprint", "authority_container_id", "source_identity",
                     "provenance_identity", "content_fingerprint", "dependency_fingerprint"):
            _require_nonempty(getattr(self, name), f"ProposedSupportRef.{name}")
        _require_enum(self.binding_subject_kind, BINDING_SUBJECT_KINDS,
                      "ProposedSupportRef.binding_subject_kind")
        _require_enum(self.support_role, SUPPORT_ROLES, "ProposedSupportRef.support_role")
        if not _subject_matches_semantics(self.binding_subject_kind, self.support_semantics):
            raise NarrativeSchemaError(
                f"ProposedSupportRef: support_semantics={self.support_semantics!r} 的 binding "
                f"subject 只能是 "
                f"{'claim_candidate' if self.support_semantics == 'factual' else 'narrative_draft_unit'}"
                f"，得到 {self.binding_subject_kind!r}（factual 不得挂草稿单元，context 不得挂候选）")
        locator, payload = validate_support_edge_shape(
            authority_kind=self.authority_kind, support_semantics=self.support_semantics,
            authorization_path=self.authorization_path,
            fact_fields={"fact_id": self.fact_id, "financial_fact_id": self.financial_fact_id,
                         "note_fact_id": self.note_fact_id,
                         "external_fact_id": self.external_fact_id},
            material_id=self.material_id, payload_ref=self.payload_ref,
            locator_ref=self.locator_ref, what="ProposedSupportRef")
        object.__setattr__(self, "locator_ref", locator)
        object.__setattr__(self, "payload_ref", payload)
        expected = derive_proposed_support_id(self)
        if self.proposed_support_id != expected:
            raise NarrativeSchemaError(
                f"ProposedSupportRef.proposed_support_id 与内容不符：声明 "
                f"{self.proposed_support_id!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "binding_subject_kind": self.binding_subject_kind,
            "binding_subject_id": self.binding_subject_id,
            "draft_revision": self.draft_revision,
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "authority_kind": self.authority_kind,
            "authority_container_id": self.authority_container_id,
            "source_identity": self.source_identity,
            "provenance_identity": self.provenance_identity,
            "support_role": self.support_role,
            "support_semantics": self.support_semantics,
            "authorization_path": self.authorization_path,
            "content_fingerprint": self.content_fingerprint,
            "dependency_fingerprint": self.dependency_fingerprint,
            "fact_id": self.fact_id,
            "financial_fact_id": self.financial_fact_id,
            "note_fact_id": self.note_fact_id,
            "external_fact_id": self.external_fact_id,
            "material_id": self.material_id,
            "payload_ref": dict(self.payload_ref) if self.payload_ref else None,
            "locator_ref": dict(self.locator_ref) if self.locator_ref else None,
        }

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"proposed_support_id": self.proposed_support_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ProposedSupportRef":
        fields = ("binding_subject_kind", "binding_subject_id", "draft_revision", "manifest_id",
                  "manifest_fingerprint", "authority_kind", "authority_container_id",
                  "source_identity", "provenance_identity", "support_role", "support_semantics",
                  "authorization_path", "content_fingerprint", "dependency_fingerprint",
                  "fact_id", "financial_fact_id", "note_fact_id", "external_fact_id",
                  "material_id", "payload_ref", "locator_ref")
        body = {k: kwargs.get(k) for k in fields}
        body["locator_ref"] = _validate_locator(body.get("locator_ref"), "ProposedSupportRef")
        body["schema_version"] = NARRATIVE_SCHEMA_VERSION
        body["proposed_support_id"] = derive_proposed_support_id(_WireView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "ProposedSupportRef":
        allowed = {"proposed_support_id", "schema_version", "binding_subject_kind",
                   "binding_subject_id", "draft_revision", "manifest_id",
                   "manifest_fingerprint", "authority_kind", "authority_container_id",
                   "source_identity", "provenance_identity", "support_role",
                   "support_semantics", "authorization_path", "content_fingerprint",
                   "dependency_fingerprint", "fact_id", "financial_fact_id", "note_fact_id",
                   "external_fact_id", "material_id", "payload_ref", "locator_ref"}
        d = _reject_unknown(d, allowed, "ProposedSupportRef")
        locator = d.get("locator_ref")
        return cls(
            proposed_support_id=_require_nonempty(d.get("proposed_support_id"),
                                                 "ProposedSupportRef.proposed_support_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "ProposedSupportRef.schema_version"),
            binding_subject_kind=_require_nonempty(d.get("binding_subject_kind"),
                                                   "ProposedSupportRef.binding_subject_kind"),
            binding_subject_id=_require_nonempty(d.get("binding_subject_id"),
                                                 "ProposedSupportRef.binding_subject_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "ProposedSupportRef.draft_revision"),
            manifest_id=_require_nonempty(d.get("manifest_id"), "ProposedSupportRef.manifest_id"),
            manifest_fingerprint=_require_nonempty(d.get("manifest_fingerprint"),
                                                   "ProposedSupportRef.manifest_fingerprint"),
            authority_kind=_require_nonempty(d.get("authority_kind"),
                                             "ProposedSupportRef.authority_kind"),
            authority_container_id=_require_nonempty(
                d.get("authority_container_id"), "ProposedSupportRef.authority_container_id"),
            source_identity=_require_nonempty(d.get("source_identity"),
                                              "ProposedSupportRef.source_identity"),
            provenance_identity=_require_nonempty(d.get("provenance_identity"),
                                                  "ProposedSupportRef.provenance_identity"),
            support_role=_require_nonempty(d.get("support_role"), "ProposedSupportRef.support_role"),
            support_semantics=_require_nonempty(d.get("support_semantics"),
                                                "ProposedSupportRef.support_semantics"),
            authorization_path=_require_nonempty(d.get("authorization_path"),
                                                 "ProposedSupportRef.authorization_path"),
            content_fingerprint=_require_nonempty(d.get("content_fingerprint"),
                                                  "ProposedSupportRef.content_fingerprint"),
            dependency_fingerprint=_require_nonempty(d.get("dependency_fingerprint"),
                                                     "ProposedSupportRef.dependency_fingerprint"),
            fact_id=d.get("fact_id"), financial_fact_id=d.get("financial_fact_id"),
            note_fact_id=d.get("note_fact_id"), external_fact_id=d.get("external_fact_id"),
            material_id=d.get("material_id"),
            payload_ref=dict(d["payload_ref"]) if d.get("payload_ref") else None,
            locator_ref=locator if locator else None)


def derive_proposed_support_id(ref: Any) -> str:
    body = ref.identity_body() if isinstance(ref, ProposedSupportRef) else ref.body
    return content_id("psr_", body)


# ---------------------------------------------------------------------------
# exact material manifest + WriterMaterialProcessingDisposition（3B：P5/P6）
#
# 三个身份**不得混用**（§0.13）：
#   * `ResearchMaterialDisposition`（RMD，研究侧，`harness.topic_schema`）＝ 材料**进入 Pack
#     时的处置**，不含 used/not_used；
#   * `WriterMaterialManifest`（本模块）＝ 本次 Writer 消费的**精确材料清单**（available）；
#   * `WriterMaterialProcessingDisposition`（本模块）＝ 每个 manifest 成员在**本次写作**中的
#     处理去向（processed / used / not_used + 支撑用途）。
# 因此 WMPD 必须回指 matching RMD（`research_material_disposition_id`）与同一条 material 的
# 内容指纹：Writer 自报不能单独建立去向（§16.7.1 P6）。
# ---------------------------------------------------------------------------

def manifest_member_ref(pack_id: str, material_id: str) -> str:
    """manifest 成员的**唯一键**：`(container, material)` 二元组的确定性编码。

    §P1-8 同类纪律：裸 `material_id` 跨容器去重会把两个 Pack 里同名的 material 折叠成一条，
    于是「每个 manifest 成员恰有一条处理去向」被静默满足。成员键必须带容器身份。
    """
    _require_nonempty(pack_id, "manifest_member_ref.pack_id")
    _require_nonempty(material_id, "manifest_member_ref.material_id")
    return content_id("wmmref_", {"pack_id": str(pack_id), "material_id": str(material_id)})


def fact_provenance_ref(authority_kind: str, container_identity: str, fact_id: str) -> str:
    """门前自然草稿**事实侧出处**的唯一键（`narr-8` / `pprov-1`）：权威事实行的确定性编码。

    它回答的问题与 `manifest_member_ref` 是**同一个**（「这段正文是从哪儿长出来的」），
    只是答案落在另一条身份轴上：财务节的精确材料清单合法地为空（`FinancialFactPack` 的事实
    不经过 Pack 材料），此时草稿的出处只能是**权威事实行**——把事实路径塞进材料键里（或反过来
    伪造一条 `ResearchMaterial`）会让「出处」这一格同时表达两种身份，读者再也分不清这段话是从
    材料正文概括来的、还是从一条预验证事实派生的。

    两条轴**命名空间不相交**是可核验的，不是约定：本函数返回 `fprov_<hex>`，材料侧返回
    `wmmref_<hex>`，两者是**不同前缀**的内容 id，任何一侧都不可能产出另一侧的取值。
    """
    _require_nonempty(authority_kind, "fact_provenance_ref.authority_kind")
    _require_nonempty(container_identity, "fact_provenance_ref.container_identity")
    _require_nonempty(fact_id, "fact_provenance_ref.fact_id")
    return content_id("fprov_", {"authority_kind": str(authority_kind),
                                 "container_identity": str(container_identity),
                                 "fact_id": str(fact_id)})


#: 草稿出处两轴的命名空间前缀（`pprov-1`）。它们的**不相交**是「两轴互斥」可核验的前提：
#: 任何一个草稿出处键必然**只**属于其中一侧。
MATERIAL_PROVENANCE_PREFIX = "wmmref_"
FACT_PROVENANCE_PREFIX = "fprov_"
#: 草稿出处政策的版本号：两轴互斥（恰有一个非空）+ 事实侧键的编码口径。
PROSE_PROVENANCE_POLICY_VERSION = "pprov-1"
#: 草稿**原子归属（occurrence）政策**的版本号（`npr-1`）：一条候选可以在几段草稿里各出现一次。
#:
#: 它之前的形态（`npr-0`，无版本号的隐式规则）是「同一候选被多个草稿单元声明即拒」。那条规则
#: 把**两条不同的业务关系**当成了一件事：
#:
#:   * 同一句话在两段草稿里各写一遍、**两段都指得出自己的支撑**——两个版本的材料（年报 2025 /
#:     年报 2024）各自写出同一句原子，两次表达各有各的出处。这是**材料驱动写作的真实产物**：
#:     合并层按（`claim_text`, 事实类型）判候选同一性，于是同一句原子在合并后**必然**只有一条
#:     候选，而它声明的两段草稿却指向两份不同的材料。旧规则在这里把正确的链判成缺陷。
#:   * 同一句话写了两遍、其中一遍**指不出**支撑（错版本 / 错来源 / 无匹配支撑），或**同一段**
#:     里把一条候选说两遍——那是「这段正文不是从这场材料长出来的」，必须继续拒。
#:
#: `npr-1` 把这两件事分开：允许出现多次，但**每一次出现**都必须在该候选**自己的** factual
#: 提案里找到一条落在同一份来源上的边（材料 ID + 文档两轴，或同一条权威事实行）；单元内部重复、
#: 无匹配支撑、错版本、漏候选照旧拒绝。**不放宽任何一条既有的拒绝**：被拿掉的只有「跨单元
#: 唯一」这一条，而它换来的不是「不核了」，是「逐 occurrence 各核一次」。
#:
#: 门后由组织器在合格表达里选（避免读者面重复），因此允许 occurrence **不等于**允许正文重复。
PROSE_OCCURRENCE_POLICY_VERSION = "npr-1"


def provenance_axis_of(ref: str) -> str:
    """一个草稿出处键落在**哪一条轴**上（`material` / `fact`）；前缀对不上即 fail-closed。

    判据只有这一处：互斥核对、闭合核对与读者面都读它，不各自再写一遍「startswith」。
    """
    key = str(ref or "")
    if key.startswith(MATERIAL_PROVENANCE_PREFIX):
        return "material"
    if key.startswith(FACT_PROVENANCE_PREFIX):
        return "fact"
    raise NarrativeSchemaError(
        f"草稿出处 {key!r} 不属于任何一条登记过的出处轴"
        f"（材料侧 {MATERIAL_PROVENANCE_PREFIX!r} / 事实侧 {FACT_PROVENANCE_PREFIX!r}）")


@dataclass(frozen=True)
class WriterMaterialManifestEntry:
    """manifest 的一个成员：**一份真实 Pack material** + 其 matching RMD 的引用（`wmm-2`）。

    `authority_kind` 固定为 `topic_pack`：exact `ResearchMaterial` 只存在于 Pack；财务口径 /
    附注 / external snapshot 各有自己的 payload/locator，**不得**伪造一条 material 混进来。

    `wmm-2` 相对 `wmm-1` 新增的是**正文侧**（§三 A.3）：`payload_ref`（规范
    `MaterialPayloadRef` dict）、`locator_ref`（exact locator）、`payload_hash`（载体层身份）、
    `reading_view_fingerprint`（模型实际看到的读视图指纹）。它们全部由
    `sections.material_context` 解析真实 payload 字节后确定性派生，**不是**调用方自报字段：
    因此「成员存在」与「正文已解析并校验」在 `wmm-2` 里是同一件事。
    """

    member_ref: str
    pack_id: str
    material_id: str
    research_material_disposition_id: str
    source_identity: str
    provenance_identity: str
    material_content_fingerprint: str
    topic_id: str
    material_type: str
    payload_ref: dict
    locator_ref: dict
    payload_hash: str
    reading_view_fingerprint: str
    authority_kind: str = "topic_pack"

    def __post_init__(self) -> None:
        if self.authority_kind != "topic_pack":
            raise NarrativeSchemaError(
                "WriterMaterialManifestEntry.authority_kind 只能是 'topic_pack'："
                "exact ResearchMaterial 是 Pack 侧的正式材料边界，"
                "不得给非 Pack 来源伪造一条 material")
        for name in ("pack_id", "material_id", "research_material_disposition_id",
                     "source_identity", "provenance_identity", "topic_id", "material_type"):
            _require_nonempty(getattr(self, name), f"WriterMaterialManifestEntry.{name}")
        for name in ("material_content_fingerprint", "payload_hash", "reading_view_fingerprint"):
            if not _is_sha256_hex(getattr(self, name)):
                raise NarrativeSchemaError(
                    f"WriterMaterialManifestEntry.{name} 必须是 64 位 sha256 hex")
        if not isinstance(self.payload_ref, Mapping) or not self.payload_ref:
            raise NarrativeSchemaError(
                "WriterMaterialManifestEntry.payload_ref 必须是规范 `MaterialPayloadRef` dict"
                "（wmm-2 成员身份必须包含可解析的真实引用，不得只有 material ID）")
        locator = validate_locator(self.locator_ref, "WriterMaterialManifestEntry")
        if locator is None:
            raise NarrativeSchemaError(
                "WriterMaterialManifestEntry.locator_ref 不得为空（成员身份必须包含 exact "
                "locator：`loc-1` tagged union，单块材料用 block_range 的 first == last）")
        object.__setattr__(self, "payload_ref", dict(self.payload_ref))
        object.__setattr__(self, "locator_ref", locator)
        expected = manifest_member_ref(self.pack_id, self.material_id)
        if self.member_ref != expected:
            raise NarrativeSchemaError(
                f"WriterMaterialManifestEntry.member_ref 与容器/材料身份不符：声明 "
                f"{self.member_ref!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "member_ref": self.member_ref,
            "pack_id": self.pack_id,
            "material_id": self.material_id,
            "research_material_disposition_id": self.research_material_disposition_id,
            "source_identity": self.source_identity,
            "provenance_identity": self.provenance_identity,
            "material_content_fingerprint": self.material_content_fingerprint,
            "topic_id": self.topic_id,
            "material_type": self.material_type,
            "payload_ref": dict(self.payload_ref),
            "locator_ref": dict(self.locator_ref),
            "payload_hash": self.payload_hash,
            "reading_view_fingerprint": self.reading_view_fingerprint,
            "authority_kind": self.authority_kind,
        }

    def to_dict(self) -> dict:
        return dict(self.identity_body())

    @classmethod
    def create(cls, **kwargs: Any) -> "WriterMaterialManifestEntry":
        body = {k: kwargs.get(k) for k in (
            "pack_id", "material_id", "research_material_disposition_id", "source_identity",
            "provenance_identity", "material_content_fingerprint", "topic_id", "material_type",
            "payload_ref", "locator_ref", "payload_hash", "reading_view_fingerprint",
            "authority_kind")}
        body["authority_kind"] = body["authority_kind"] or "topic_pack"
        body["member_ref"] = manifest_member_ref(str(body["pack_id"] or ""),
                                                str(body["material_id"] or ""))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "WriterMaterialManifestEntry":
        d = _reject_unknown(d, {"member_ref", "pack_id", "material_id",
                                "research_material_disposition_id", "source_identity",
                                "provenance_identity", "material_content_fingerprint",
                                "topic_id", "material_type", "payload_ref", "locator_ref",
                                "payload_hash", "reading_view_fingerprint",
                                "authority_kind"}, "WriterMaterialManifestEntry")
        return cls(
            member_ref=_require_nonempty(d.get("member_ref"),
                                         "WriterMaterialManifestEntry.member_ref"),
            pack_id=_require_nonempty(d.get("pack_id"), "WriterMaterialManifestEntry.pack_id"),
            material_id=_require_nonempty(d.get("material_id"),
                                          "WriterMaterialManifestEntry.material_id"),
            research_material_disposition_id=_require_nonempty(
                d.get("research_material_disposition_id"),
                "WriterMaterialManifestEntry.research_material_disposition_id"),
            source_identity=_require_nonempty(
                d.get("source_identity"), "WriterMaterialManifestEntry.source_identity"),
            provenance_identity=_require_nonempty(
                d.get("provenance_identity"), "WriterMaterialManifestEntry.provenance_identity"),
            material_content_fingerprint=_require_nonempty(
                d.get("material_content_fingerprint"),
                "WriterMaterialManifestEntry.material_content_fingerprint"),
            topic_id=_require_nonempty(d.get("topic_id"),
                                       "WriterMaterialManifestEntry.topic_id"),
            material_type=_require_nonempty(d.get("material_type"),
                                            "WriterMaterialManifestEntry.material_type"),
            payload_ref=_req(d, "payload_ref", "WriterMaterialManifestEntry"),
            locator_ref=_req(d, "locator_ref", "WriterMaterialManifestEntry"),
            payload_hash=_require_nonempty(d.get("payload_hash"),
                                           "WriterMaterialManifestEntry.payload_hash"),
            reading_view_fingerprint=_require_nonempty(
                d.get("reading_view_fingerprint"),
                "WriterMaterialManifestEntry.reading_view_fingerprint"),
            authority_kind=d.get("authority_kind") or "topic_pack")

    @classmethod
    def from_material_context(cls, material: Any) -> "WriterMaterialManifestEntry":
        """`ResolvedWriterMaterial` → 成员（**唯一**构造路径：已解析正文才能成为成员）。

        延迟导入 `sections.material_context`：manifest wire 不反向依赖上下文模块，但两者
        的成员身份必须是同一份（成员键、payload 哈希、读视图指纹逐字相同）。
        """
        from sections import material_context as _MC

        if not isinstance(material, _MC.ResolvedWriterMaterial):
            raise NarrativeSchemaError(
                "WriterMaterialManifestEntry.from_material_context 只接受已解析正文的 "
                f"ResolvedWriterMaterial，得到 {type(material).__name__}："
                "「只有 material ID」的成员在 wmm-2 里不可构造")
        return cls.create(
            pack_id=material.pack_id, material_id=material.material_id,
            research_material_disposition_id=material.research_material_disposition_id,
            source_identity=material.source_identity,
            provenance_identity=material.provenance_identity,
            material_content_fingerprint=material.material_content_fingerprint,
            topic_id=material.topic_id, material_type=material.material_type,
            payload_ref=dict(material.payload_ref), locator_ref=dict(material.locator_ref),
            payload_hash=material.payload_hash,
            reading_view_fingerprint=material.reading_view_fingerprint)


def material_reading_view(material: Any) -> tuple[str, dict | None, dict | None]:
    """`ResolvedWriterMaterial` → `(正文读视图, 表格读视图, 内容形态读法)`（**唯一**实现）。

    为什么要有这一处：**两个**调用方都要把「模型实际看到的材料正文」投出去——门前写作器的
    请求面（`pack_writer._reading_views_for_manifest`）与门后组织器的改写依据
    （`narrative_organizer`）。两处各自写一份字段读取，正是最容易漂移、且漂移后表现为
    「同一份材料在两个调用里读到的不是同一段字」的那类缺陷。这里只做**投影**：正文既不是从
    material ID 猜的，也不是调用方另带的一份；第三项（内容形态）随正文一起走，因为它在材料
    构建期就进了 payload 哈希（§二 2.3），不是任何调用方另算的。
    """
    return (str(getattr(material, "reading_view", "") or ""),
            getattr(material, "structured_view", None),
            getattr(material, "content_qualification", None))


@dataclass(frozen=True)
class LegacyWriterMaterialManifestV1View:
    """`wmm-1` manifest 的**只读**回放视图（审计用，不得进入当前链）。

    旧成员没有 payload 正文引用，因此它**不能**被当作「材料正文在场」的证据：
    当前链只认 `wmm-2`，旧对象只能按原字节回放、逐字段读出并与历史记录比对。
    """

    manifest_id: str
    schema_version: str
    members: tuple[dict, ...]
    manifest_fingerprint: str

    def member_refs(self) -> tuple[str, ...]:
        return tuple(str(m.get("member_ref") or "") for m in self.members)


def load_legacy_wmm1_for_audit(payload: Any) -> LegacyWriterMaterialManifestV1View:
    """回放 `wmm-1` manifest（只读）。当前 reader **拒绝**它，本函数是唯一的旧对象入口。"""
    d = _reject_unknown(payload, {"manifest_id", "manifest_fingerprint", "schema_version",
                                  "members"}, "LegacyWriterMaterialManifestV1View")
    version = _require_nonempty(d.get("schema_version"),
                                "LegacyWriterMaterialManifestV1View.schema_version")
    if version not in LEGACY_MATERIAL_MANIFEST_SCHEMA_VERSIONS:
        raise NarrativeSchemaError(
            f"load_legacy_wmm1_for_audit 只回放 {list(LEGACY_MATERIAL_MANIFEST_SCHEMA_VERSIONS)}，"
            f"得到 {version!r}（当前版本请走正式 reader）")
    allowed = {"member_ref", "pack_id", "material_id", "research_material_disposition_id",
               "source_identity", "provenance_identity", "material_content_fingerprint",
               "authority_kind"}
    members: list[dict] = []
    for raw in _req(d, "members", "LegacyWriterMaterialManifestV1View"):
        member = _reject_unknown(raw, allowed, "LegacyWriterMaterialManifestV1View.member")
        expected = manifest_member_ref(_require_nonempty(member.get("pack_id"), "legacy.pack_id"),
                                       _require_nonempty(member.get("material_id"),
                                                         "legacy.material_id"))
        if str(member.get("member_ref") or "") != expected:
            raise NarrativeSchemaError(
                "legacy manifest 成员的 member_ref 与容器/材料身份不符（旧对象未改写）")
        members.append(dict(member))
    body = {"schema_version": version, "members": members}
    declared_id = _require_nonempty(d.get("manifest_id"), "LegacyWriterMaterialManifestV1View.manifest_id")
    declared_fp = _require_nonempty(d.get("manifest_fingerprint"),
                                    "LegacyWriterMaterialManifestV1View.manifest_fingerprint")
    if declared_id != content_id("wmm_", body):
        raise NarrativeSchemaError(
            "legacy manifest 的 manifest_id 与成员集不符（历史记录被改写）")
    if declared_fp != hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest():
        raise NarrativeSchemaError(
            "legacy manifest 的 manifest_fingerprint 与成员集不符（历史记录被改写）")
    return LegacyWriterMaterialManifestV1View(manifest_id=declared_id, schema_version=version,
                                              members=tuple(members),
                                              manifest_fingerprint=declared_fp)


@dataclass(frozen=True)
class WriterMaterialManifest:
    """本次写作消费的**精确**材料清单（available set），成员逐一回指真实 material + RMD。

    空 manifest 是合法状态（本节确实没有任何 Pack material）；**不是**「无材料也照样写」的
    许可：它只说明 available 为空，processed 也必须为空。
    """

    manifest_id: str
    schema_version: str
    entries: tuple[WriterMaterialManifestEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != MATERIAL_MANIFEST_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"WriterMaterialManifest.schema_version 必须为 "
                f"{MATERIAL_MANIFEST_SCHEMA_VERSION!r}")
        entries = tuple(self.entries)
        for entry in entries:
            if not isinstance(entry, WriterMaterialManifestEntry):
                raise NarrativeSchemaError(
                    "WriterMaterialManifest.entries 只能由 WriterMaterialManifestEntry 构成，"
                    f"得到 {type(entry).__name__}")
        object.__setattr__(self, "entries", entries)
        refs = [e.member_ref for e in entries]
        if len(set(refs)) != len(refs):
            raise NarrativeSchemaError(
                "WriterMaterialManifest 含重复成员（按 container+material 身份去重，"
                "不得用裸 material_id 折叠）")
        expected = derive_manifest_id(self)
        if self.manifest_id != expected:
            raise NarrativeSchemaError(
                f"WriterMaterialManifest.manifest_id 与成员集不符：声明 {self.manifest_id!r}，"
                f"应为 {expected!r}")

    def member_refs(self) -> tuple[str, ...]:
        return tuple(e.member_ref for e in self.entries)

    def entry_for(self, member_ref: str) -> WriterMaterialManifestEntry | None:
        for entry in self.entries:
            if entry.member_ref == member_ref:
                return entry
        return None

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "members": [e.identity_body() for e in self.entries],
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.fingerprint(),
            "schema_version": self.schema_version,
            "members": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def create(cls, **kwargs: Any) -> "WriterMaterialManifest":
        raw = kwargs.get("members")
        if raw is None:
            raw = kwargs.get("entries")
        entries = tuple(WriterMaterialManifestEntry.from_dict(x)
                        if isinstance(x, Mapping) else x for x in (raw or ()))
        body = {"schema_version": MATERIAL_MANIFEST_SCHEMA_VERSION,
                "members": [e.identity_body() for e in entries]}
        return cls(manifest_id=derive_manifest_id(_WireView(body)),
                   schema_version=MATERIAL_MANIFEST_SCHEMA_VERSION, entries=entries)

    @classmethod
    def from_dict(cls, d: Any) -> "WriterMaterialManifest":
        d = _reject_unknown(d, {"manifest_id", "manifest_fingerprint", "schema_version",
                                "members", "entries"}, "WriterMaterialManifest")
        raw = d.get("members")
        if raw is None:
            raw = d.get("entries")
        manifest = cls(
            manifest_id=_require_nonempty(d.get("manifest_id"),
                                          "WriterMaterialManifest.manifest_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "WriterMaterialManifest.schema_version"),
            entries=tuple(WriterMaterialManifestEntry.from_dict(x) for x in (raw or ())))
        declared = d.get("manifest_fingerprint")
        if declared is not None and str(declared) != manifest.fingerprint():
            raise NarrativeSchemaError(
                f"WriterMaterialManifest.manifest_fingerprint 与成员集不符：声明 {declared!r}，"
                f"实际 {manifest.fingerprint()!r}")
        return manifest


def derive_manifest_id(manifest: Any) -> str:
    body = manifest.identity_body() if isinstance(manifest, WriterMaterialManifest) else manifest.body
    return content_id("wmm_", body)


def derive_manifest_fingerprint(manifest: Any) -> str:
    if isinstance(manifest, WriterMaterialManifest):
        return manifest.fingerprint()
    return hashlib.sha256(
        canonical_json(manifest.body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WriterMaterialProcessingDisposition:
    """一个 manifest 成员在**本次写作**中的处理去向（WMPD，§16.7.1 P6）。

    三层等式（由 `SectionDraft` 的 exact-set 校验 `validate_material_processing` 强制）：

    * `processed = available`：每个 manifest 成员都被真的处理过；
    * `used ∩ not_used = ∅`：每个成员恰属一个分区；
    * `used ∪ not_used = available`：没有成员静默消失。

    `usage="used"` 必须给出**有序**的 `support_usages`（实际引用该材料的 proposal IDs）；
    `usage="not_used"` 必须给出封闭 `reason_code` 与 typed `reason_proof`（版本化策略绑定 +
    该理由专属证明字段），且**不是** gap：「本轮没用到材料」与「Contract 必需事实未取得」是
    两条不同的轴（§0.13）。

    「被使用」的 role（`factual` / `context`）**不**在本类重述：它由 `support_usages` 指向的
    proposal 自身声明，避免同一事实有两个真值来源（§6.4.1 第 3 层）。
    """

    wmpd_id: str
    schema_version: str
    manifest_id: str
    manifest_fingerprint: str
    member_ref: str
    pack_id: str
    material_id: str
    research_material_disposition_id: str
    material_content_fingerprint: str
    processed: bool
    usage: str
    support_usages: tuple[str, ...] = ()
    reason_code: str | None = None
    reason_proof: dict | None = None
    writer_policy_version: str = ""
    rules_version: str = MATERIAL_PROCESSING_RULES_VERSION
    content_fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"WriterMaterialProcessingDisposition.schema_version 必须为 "
                f"{NARRATIVE_SCHEMA_VERSION!r}")
        for name in ("manifest_id", "manifest_fingerprint", "member_ref", "pack_id",
                     "material_id", "research_material_disposition_id", "writer_policy_version",
                     "rules_version"):
            _require_nonempty(getattr(self, name),
                              f"WriterMaterialProcessingDisposition.{name}")
        if not _is_sha256_hex(self.manifest_fingerprint):
            raise NarrativeSchemaError(
                "WriterMaterialProcessingDisposition.manifest_fingerprint 必须是 64 位 sha256 hex")
        if not _is_sha256_hex(self.material_content_fingerprint):
            raise NarrativeSchemaError(
                "WriterMaterialProcessingDisposition.material_content_fingerprint 必须是 "
                "64 位 sha256 hex")
        if not isinstance(self.processed, bool):
            raise NarrativeSchemaError(
                "WriterMaterialProcessingDisposition.processed 必须是布尔")
        _require_enum(self.usage, MATERIAL_USAGE_STATES,
                      "WriterMaterialProcessingDisposition.usage")
        usages = _str_tuple(self.support_usages,
                            "WriterMaterialProcessingDisposition.support_usages")
        object.__setattr__(self, "support_usages", usages)
        if len(set(usages)) != len(usages):
            raise NarrativeSchemaError(
                "WriterMaterialProcessingDisposition.support_usages 含重复 proposal 引用"
                "（支撑用途是精确有序集合，不得重复计数）")
        expected_ref = manifest_member_ref(self.pack_id, self.material_id)
        if self.member_ref != expected_ref:
            raise NarrativeSchemaError(
                f"WriterMaterialProcessingDisposition.member_ref 与容器/材料身份不符：声明 "
                f"{self.member_ref!r}，应为 {expected_ref!r}")

        if self.usage == "used":
            if not usages:
                raise NarrativeSchemaError(
                    "WMPD usage='used' 必须给出实际引用该材料的 support_usages"
                    "（「用了」不能自报）")
            if self.reason_code is not None or self.reason_proof is not None:
                raise NarrativeSchemaError(
                    "WMPD usage='used' 不得携带 not_used 的理由码/证明")
        else:
            if usages:
                raise NarrativeSchemaError(
                    "WMPD usage='not_used' 不得声明 support_usages："
                    "未被使用的材料不可能有支撑用途（used/not_used 必须互斥）")
            if self.reason_code is None:
                raise NarrativeSchemaError("WMPD usage='not_used' 必须给出封闭 reason_code")
            _require_enum(self.reason_code, MATERIAL_NOT_USED_REASONS,
                          "WriterMaterialProcessingDisposition.reason_code")
            proof = self.reason_proof
            if not isinstance(proof, Mapping):
                raise NarrativeSchemaError(
                    "WMPD usage='not_used' 必须给出 typed reason_proof（对象）："
                    "无 proof/policy binding 的自报理由一律拒绝")
            proof = _canon_jsonable(dict(proof))
            object.__setattr__(self, "reason_proof", proof)
            required = tuple(MATERIAL_PROOF_POLICY_KEYS) + tuple(
                MATERIAL_NOT_USED_PROOF_KEYS[self.reason_code])
            missing = sorted(set(required) - set(proof))
            extra = sorted(set(proof) - set(required))
            if missing:
                raise NarrativeSchemaError(
                    f"WMPD reason_proof 对理由 {self.reason_code!r} 缺证明字段 {missing}"
                    "（不得用自由文本/泛化字符串代替 typed 证明）")
            if extra:
                raise NarrativeSchemaError(
                    f"WMPD reason_proof 含未登记字段 {extra}（不得开辟逃逸字段）")
            for key in required:
                _require_nonempty(proof.get(key), f"WMPD reason_proof.{key}")
            if not _is_sha256_hex(proof["policy_fingerprint"]):
                raise NarrativeSchemaError(
                    "WMPD reason_proof.policy_fingerprint 必须是 64 位 sha256 hex"
                    "（证明必须绑定版本化策略字节）")
        if not self.processed and self.reason_code is None:
            raise NarrativeSchemaError(
                "WMPD processed=False 必须给出封闭 reason_code（未处理的成员也要有去向）")

        expected = derive_wmpd_id(self)
        if self.wmpd_id != expected:
            raise NarrativeSchemaError(
                f"WriterMaterialProcessingDisposition.wmpd_id 与内容不符：声明 "
                f"{self.wmpd_id!r}，应为 {expected!r}")
        if self.content_fingerprint != self.content_hash():
            raise NarrativeSchemaError(
                "WriterMaterialProcessingDisposition.content_fingerprint 与内容不符"
                "（必须是本对象 identity_body 的 sha256）")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "member_ref": self.member_ref,
            "pack_id": self.pack_id,
            "material_id": self.material_id,
            "research_material_disposition_id": self.research_material_disposition_id,
            "material_content_fingerprint": self.material_content_fingerprint,
            "processed": self.processed,
            "usage": self.usage,
            "support_usages": list(self.support_usages),
            "reason_code": self.reason_code,
            "reason_proof": dict(self.reason_proof) if isinstance(self.reason_proof, Mapping)
            else None,
            "writer_policy_version": self.writer_policy_version,
            "rules_version": self.rules_version,
        }

    def content_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"wmpd_id": self.wmpd_id, "content_fingerprint": self.content_fingerprint,
                **self.identity_body()}

    def proof_policy(self) -> tuple[str, str] | None:
        """`not_used` 的证明政策绑定 `(policy_version, policy_fingerprint)`；used 为 None。"""
        if not isinstance(self.reason_proof, Mapping):
            return None
        return (str(self.reason_proof.get("policy_version", "")),
                str(self.reason_proof.get("policy_fingerprint", "")))

    @classmethod
    def create(cls, **kwargs: Any) -> "WriterMaterialProcessingDisposition":
        fields = ("manifest_id", "manifest_fingerprint", "member_ref", "pack_id", "material_id",
                  "research_material_disposition_id", "material_content_fingerprint", "processed",
                  "usage", "support_usages", "reason_code", "reason_proof",
                  "writer_policy_version", "rules_version")
        values = {k: kwargs.get(k) for k in fields}
        values["support_usages"] = list(values["support_usages"] or ())
        values["reason_proof"] = (dict(values["reason_proof"])
                                  if isinstance(values["reason_proof"], Mapping) else None)
        values["rules_version"] = values["rules_version"] or MATERIAL_PROCESSING_RULES_VERSION
        if not values.get("member_ref"):
            values["member_ref"] = manifest_member_ref(str(values.get("pack_id") or ""),
                                                      str(values.get("material_id") or ""))
        body = {"schema_version": NARRATIVE_SCHEMA_VERSION, **values}
        values["schema_version"] = NARRATIVE_SCHEMA_VERSION
        values["wmpd_id"] = content_id("wmpd_", body)
        values["content_fingerprint"] = hashlib.sha256(
            canonical_json(body).encode("utf-8")).hexdigest()
        return cls(**values)

    @classmethod
    def from_dict(cls, d: Any) -> "WriterMaterialProcessingDisposition":
        d = _reject_unknown(d, {"wmpd_id", "content_fingerprint", "schema_version",
                               "manifest_id", "manifest_fingerprint", "member_ref", "pack_id",
                               "material_id", "research_material_disposition_id",
                               "material_content_fingerprint", "processed", "usage",
                               "support_usages", "reason_code", "reason_proof",
                               "writer_policy_version", "rules_version"},
                            "WriterMaterialProcessingDisposition")
        return cls(
            wmpd_id=_require_nonempty(d.get("wmpd_id"),
                                      "WriterMaterialProcessingDisposition.wmpd_id"),
            schema_version=_require_nonempty(
                d.get("schema_version"), "WriterMaterialProcessingDisposition.schema_version"),
            manifest_id=_require_nonempty(
                d.get("manifest_id"), "WriterMaterialProcessingDisposition.manifest_id"),
            manifest_fingerprint=_require_nonempty(
                d.get("manifest_fingerprint"),
                "WriterMaterialProcessingDisposition.manifest_fingerprint"),
            member_ref=_require_nonempty(
                d.get("member_ref"), "WriterMaterialProcessingDisposition.member_ref"),
            pack_id=_require_nonempty(d.get("pack_id"),
                                      "WriterMaterialProcessingDisposition.pack_id"),
            material_id=_require_nonempty(d.get("material_id"),
                                          "WriterMaterialProcessingDisposition.material_id"),
            research_material_disposition_id=_require_nonempty(
                d.get("research_material_disposition_id"),
                "WriterMaterialProcessingDisposition.research_material_disposition_id"),
            material_content_fingerprint=_require_nonempty(
                d.get("material_content_fingerprint"),
                "WriterMaterialProcessingDisposition.material_content_fingerprint"),
            processed=bool(d.get("processed")),
            usage=_require_nonempty(d.get("usage"),
                                    "WriterMaterialProcessingDisposition.usage"),
            support_usages=_str_tuple(d.get("support_usages") or (),
                                      "WriterMaterialProcessingDisposition.support_usages"),
            reason_code=d.get("reason_code"),
            reason_proof=(dict(d["reason_proof"]) if d.get("reason_proof") else None),
            writer_policy_version=_require_nonempty(
                d.get("writer_policy_version"),
                "WriterMaterialProcessingDisposition.writer_policy_version"),
            rules_version=_require_nonempty(
                d.get("rules_version"), "WriterMaterialProcessingDisposition.rules_version"),
            content_fingerprint=_require_nonempty(
                d.get("content_fingerprint"),
                "WriterMaterialProcessingDisposition.content_fingerprint"))


def derive_wmpd_id(disp: Any) -> str:
    body = (disp.identity_body() if isinstance(disp, WriterMaterialProcessingDisposition)
            else getattr(disp, "body", None))
    if isinstance(body, Mapping):
        body = {k: v for k, v in body.items()
                if k not in ("wmpd_id", "content_fingerprint")}
    return content_id("wmpd_", body)


def validate_material_processing(
        manifest: WriterMaterialManifest,
        dispositions: Sequence[WriterMaterialProcessingDisposition],
        proposals: Sequence[ProposedSupportRef]) -> tuple[str, ...]:
    """三层集合等式 + 回指关系的**唯一**确定性重算入口（3B P6 / P4 共用）。

    返回有序违规说明；空元组即通过。它只做机械重算，**不接受 Writer 自报**：

    * `available` = manifest 成员集；每个成员**恰一条** WMPD，且 `processed=True`
      （`processed = available`），任何成员不得从清单里消失；
    * `used ∩ not_used = ∅`、`used ∪ not_used = available`：每个成员恰属一个分区
      （由「每成员恰一行 + 行内 usage 唯一」强制）；
    * `used` 行的 `support_usages` 必须逐条解析到**本 draft 的真实 proposal**，且该
      proposal 指向的材料就是该成员（不得借别的材料凑用途）；
    * 反过来，任何 `topic_pack` authority 且带 `material_id` 的 proposal，其成员必须在
      manifest 中且被判为 `used` 并在 `support_usages` 里列出它 —— 否则「实际引用的材料
      被报成没用到」或「材料被选择性漏掉」；
    * `not_used` 的证明若指认了另一份材料（`duplicate` / `covered_by_more_complete_material`），
      被指认者必须是本 manifest 的成员且已被 `used`。
    """
    problems: list[str] = []
    entries = tuple(manifest.entries)
    by_ref = {e.member_ref: e for e in entries}
    rows: dict[str, WriterMaterialProcessingDisposition] = {}
    for row in dispositions:
        if row.member_ref in rows:
            problems.append(f"manifest 成员 {row.member_ref} 有多条处理去向"
                            "（每个成员恰一条）")
            continue
        rows[row.member_ref] = row
    missing = sorted(set(by_ref) - set(rows))
    if missing:
        problems.append(f"manifest 成员没有处理去向：{missing}")
    extra = sorted(set(rows) - set(by_ref))
    if extra:
        problems.append(f"处理去向指向本 manifest 之外的成员：{extra}")

    proposal_by_id = {p.proposed_support_id: p for p in proposals}
    used_claims: dict[str, list[str]] = {}

    for ref in sorted(set(by_ref) & set(rows)):
        entry, row = by_ref[ref], rows[ref]
        if row.manifest_id != manifest.manifest_id or \
                row.manifest_fingerprint != manifest.fingerprint():
            problems.append(f"处理去向 {row.wmpd_id} 未绑定本 manifest identity")
        for name in ("pack_id", "material_id", "research_material_disposition_id",
                     "material_content_fingerprint"):
            if getattr(row, name) != getattr(entry, name):
                problems.append(
                    f"处理去向 {row.wmpd_id} 的 {name} 与 manifest 成员不一致"
                    "（member_ref 相同不等于内容相同）")
        if not row.processed:
            problems.append(f"manifest 成员 {ref} 未被处理（processed=False）")
        if row.usage == "used":
            for pid in row.support_usages:
                proposal = proposal_by_id.get(pid)
                if proposal is None:
                    problems.append(
                        f"处理去向 {row.wmpd_id} 的 support_usages 含本 draft 之外的 "
                        f"proposal {pid}")
                    continue
                if proposal.material_id != entry.material_id or \
                        proposal.authority_container_id != entry.pack_id:
                    problems.append(
                        f"proposal {pid} 并不指向材料 {entry.material_id}"
                        f"@{entry.pack_id}（用途不得借别的材料凑）")
                used_claims.setdefault(ref, []).append(pid)
        else:
            proof = row.reason_proof if isinstance(row.reason_proof, Mapping) else {}
            if row.reason_code in ("duplicate", "covered_by_more_complete_material"):
                other = (str(proof.get("duplicate_of_member_ref", ""))
                         if row.reason_code == "duplicate" else "")
                if row.reason_code == "covered_by_more_complete_material":
                    other = manifest_member_ref(str(proof.get("other_pack_id", "")),
                                                str(proof.get("other_material_id", "")))
                if other not in by_ref:
                    problems.append(
                        f"处理去向 {row.wmpd_id} 指认的替代材料不在本 manifest 中")
                elif rows.get(other) is None or rows[other].usage != "used":
                    problems.append(
                        f"处理去向 {row.wmpd_id} 指认的替代材料未被使用"
                        "（「有更完整的材料」必须以该材料真的被使用为前提）")

    for proposal in proposals:
        if proposal.authority_kind != "topic_pack" or not proposal.material_id:
            continue
        ref = manifest_member_ref(proposal.authority_container_id, proposal.material_id)
        if ref not in by_ref:
            problems.append(
                f"proposal {proposal.proposed_support_id} 引用的材料不在 manifest 中"
                "（材料被选择性漏掉）")
            continue
        if proposal.proposed_support_id not in used_claims.get(ref, []):
            problems.append(
                f"proposal {proposal.proposed_support_id} 引用的材料 {proposal.material_id}"
                " 未被记为 used 并列出该用途（引用过的材料不得报成未使用）")
    return tuple(problems)


def derive_draft_revision(*, task_id: str, section_id: str, company_id: str, report_as_of: str,
                          contract_version: str, contract_fingerprint: str,
                          writer_policy_version: str, prompt_version: str, model_policy: str,
                          manifest_id: str, manifest_fingerprint: str,
                          attempt: int = 1, natural_prose_digest: str = "") -> str:
    """本候选束的 **draft revision**：Writer 输入（含 manifest identity）+ 第几次生成。

    它**不**包含任何候选/单元/proposal 的 ID：否则 `ClaimCandidate.draft_revision` 与被 draft
    引用的 candidate ID 互指成环（§0.13：前一对象不得把后继身份计入自身 content identity）。
    它**也不**包含模型输出内容——`attempt` 是**调用**的序号，不是输出的内容：一次重写与它重写
    掉的那一束因此互为不同修订，而「同一束被重放」仍然得到同一个修订。

    为什么必须区分：被拒的那一束与重写后的那一束若共享同一个 `draft_revision`，产物里就会出现
    「同一个修订既被拒又被采信」，读的人无从判断某个 candidate_id 属于哪一束；而重写的正当做法
    是**整束重出**（新修订 + 完整有序 proposal 集），不是把违规的那几条原地删掉后拿同一修订放行。

    `attempt == 1`（首轮）与加入本次参数**之前**的取值逐字节相同：历史 draft 载荷与既有夹具
    在没有该字段时读回来的仍是同一个修订，历史读回不受影响。

    `natural_prose_digest`（narr-7）是门前自然草稿的内容摘要，**只在草稿非空时**进身份体。
    它与 `writer_attempt` 同一条纪律：空串（= 没有草稿，`narr-6` 及更早）不进身份体，因此
    **全部**历史载荷的 `draft_revision` 一字不变；草稿非空时进身份体，于是「同一批输入但草稿
    换了一版」必然是**另一个修订**，不会与旧修订共享身份（否则同一修订会同时背负两版正文）。

    该摘要**只由不依赖本修订的内容**构成（prose 文本 / 材料出处 / 顺序），否则本函数与它自己
    的返回值互指成环、草稿层非空的载荷根本无法构造——详见 `natural_prose_draft_digest` 与
    `derive_draft_revision_with_natural_prose`（后者是草稿层在场时的**唯一**推导入口）。
    """
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise NarrativeSchemaError(f"derive_draft_revision.attempt 必须是 >= 1 的整数，实为 {attempt!r}")
    body = {
        "task_id": task_id, "section_id": section_id, "company_id": company_id,
        "report_as_of": report_as_of, "contract_version": contract_version,
        "contract_fingerprint": contract_fingerprint,
        "writer_policy_version": writer_policy_version, "prompt_version": prompt_version,
        "model_policy": model_policy,
        "manifest_id": manifest_id, "manifest_fingerprint": manifest_fingerprint,
    }
    if attempt > 1:
        # 首轮不进身份体：`{"writer_attempt": 1}` 加进去会改掉**每一份**历史载荷的修订取值，
        # 而那不带来任何区分力（首轮本来就只有一种）。
        body["writer_attempt"] = attempt
    if natural_prose_digest:
        body["natural_prose_digest"] = str(natural_prose_digest)
    return content_id("sdrev_", body)


@dataclass(frozen=True)
class BindingEdgeResult:
    """aggregate `ClaimBindingDecision` 里的一条**逐边机械结果**（typed audit）。"""

    proposed_support_id: str
    proposal_content_hash: str
    result: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.proposed_support_id, "BindingEdgeResult.proposed_support_id")
        _require_nonempty(self.proposal_content_hash, "BindingEdgeResult.proposal_content_hash")
        _require_enum(self.result, BINDING_EDGE_RESULTS, "BindingEdgeResult.result")
        if self.result == "pass":
            if self.reason_code is not None:
                raise NarrativeSchemaError("逐边 pass 不得携带 reason_code")
        else:
            _require_enum(self.reason_code, BINDING_EDGE_REASON_CODES,
                          "BindingEdgeResult.reason_code")

    def to_dict(self) -> dict:
        return {"proposed_support_id": self.proposed_support_id,
                "proposal_content_hash": self.proposal_content_hash,
                "result": self.result, "reason_code": self.reason_code}

    @classmethod
    def from_dict(cls, d: Any) -> "BindingEdgeResult":
        d = _reject_unknown(d, {"proposed_support_id", "proposal_content_hash", "result",
                                "reason_code"}, "BindingEdgeResult")
        return cls(
            proposed_support_id=_require_nonempty(d.get("proposed_support_id"),
                                                 "BindingEdgeResult.proposed_support_id"),
            proposal_content_hash=_require_nonempty(d.get("proposal_content_hash"),
                                                    "BindingEdgeResult.proposal_content_hash"),
            result=_require_nonempty(d.get("result"), "BindingEdgeResult.result"),
            reason_code=d.get("reason_code"))


@dataclass(frozen=True)
class ClaimBindingDecision:
    """**aggregate** 机械门决定（§6.3.2）：每个 `(subject_kind, subject_id, draft_revision)`
    **恰好一条**。

    它绑定**完整、有序**的 proposal IDs/hashes、support-set digest、manifest identity 与
    规则版本，并保存逐边机械结果。少一条、多一条、重复一条或存在未知 proposal 均为 fail；
    任一逐边失败则聚合失败。它**不做**长文语义判断，也不得冒充蕴含决定 —— 本类没有
    `entailment_decision_id`、`section_claim_id`、`section_result_id` 字段。
    """

    binding_decision_id: str
    schema_version: str
    subject_kind: str
    subject_id: str
    draft_revision: str
    authority_kind: str
    manifest_id: str
    manifest_fingerprint: str
    support_set_digest: str
    proposal_ids: tuple[str, ...]
    proposal_hashes: tuple[str, ...]
    edge_results: tuple[BindingEdgeResult, ...]
    result: str
    rules_version: str
    structural_reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"ClaimBindingDecision.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        _require_enum(self.subject_kind, BINDING_SUBJECT_KINDS,
                      "ClaimBindingDecision.subject_kind")
        _require_nonempty(self.subject_id, "ClaimBindingDecision.subject_id")
        _require_nonempty(self.draft_revision, "ClaimBindingDecision.draft_revision")
        _require_enum(self.authority_kind, AUTHORITY_KINDS, "ClaimBindingDecision.authority_kind")
        for name in ("manifest_id", "manifest_fingerprint", "support_set_digest"):
            _require_nonempty(getattr(self, name), f"ClaimBindingDecision.{name}")
        _require_enum(self.result, BINDING_DECISION_RESULTS, "ClaimBindingDecision.result")
        _require_nonempty(self.rules_version, "ClaimBindingDecision.rules_version")

        object.__setattr__(self, "proposal_ids",
                           _str_tuple(self.proposal_ids, "ClaimBindingDecision.proposal_ids"))
        object.__setattr__(self, "proposal_hashes", _str_tuple(
            self.proposal_hashes, "ClaimBindingDecision.proposal_hashes"))
        object.__setattr__(self, "edge_results",
                           tuple(self.edge_results or ()))
        if not self.proposal_ids:
            raise NarrativeSchemaError(
                "ClaimBindingDecision.proposal_ids 不得为空（空 proposal 集不是合法聚合）")
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise NarrativeSchemaError("ClaimBindingDecision.proposal_ids 含重复（聚合 cardinality 失效）")
        if len(self.proposal_hashes) != len(self.proposal_ids):
            raise NarrativeSchemaError(
                "ClaimBindingDecision.proposal_hashes 必须与 proposal_ids 一一对应（同长度）")
        if len(set(self.proposal_hashes)) != len(self.proposal_hashes):
            raise NarrativeSchemaError("ClaimBindingDecision.proposal_hashes 含重复")

        known = set(self.proposal_ids)
        edge_ids = [e.proposed_support_id for e in self.edge_results]
        if len(set(edge_ids)) != len(edge_ids):
            raise NarrativeSchemaError("ClaimBindingDecision.edge_results 含重复 proposal")
        unknown = sorted(set(edge_ids) - known)
        if unknown:
            raise NarrativeSchemaError(
                f"ClaimBindingDecision.edge_results 指向未声明的 proposal：{unknown}")
        complete = (list(edge_ids) == list(self.proposal_ids))
        all_pass = all(e.result == "pass" for e in self.edge_results)
        if self.result == "pass":
            if not complete:
                raise NarrativeSchemaError(
                    "aggregate=pass 必须覆盖**完整、有序**的 proposal 集（缺失/多出/顺序漂移皆拒）")
            if not all_pass:
                raise NarrativeSchemaError("任一逐边失败则 aggregate 必须 fail")
            if self.structural_reason_code is not None:
                raise NarrativeSchemaError("aggregate=pass 不得携带 structural_reason_code")
        else:
            if complete and all_pass:
                raise NarrativeSchemaError(
                    "逐边全 pass 且集合完整时 aggregate 不得为 fail")
            if self.structural_reason_code is not None:
                _require_enum(self.structural_reason_code, BINDING_STRUCTURAL_REASONS,
                              "ClaimBindingDecision.structural_reason_code")
            elif all_pass:
                raise NarrativeSchemaError(
                    "aggregate=fail 且逐边全 pass 时必须给出 structural_reason_code")
            elif not self.edge_results:
                raise NarrativeSchemaError(
                    "aggregate=fail 时既无逐边结果也无 structural_reason_code（无 typed audit）")
        expected = derive_binding_decision_id(self)
        if self.binding_decision_id != expected:
            raise NarrativeSchemaError(
                f"ClaimBindingDecision.binding_decision_id 与内容不符：声明 "
                f"{self.binding_decision_id!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "draft_revision": self.draft_revision,
            "authority_kind": self.authority_kind,
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "support_set_digest": self.support_set_digest,
            "proposal_ids": list(self.proposal_ids),
            "proposal_hashes": list(self.proposal_hashes),
            "edge_results": [e.to_dict() for e in self.edge_results],
            "result": self.result,
            "rules_version": self.rules_version,
            "structural_reason_code": self.structural_reason_code,
        }

    def to_dict(self) -> dict:
        return {"binding_decision_id": self.binding_decision_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ClaimBindingDecision":
        # 逐边结果可以以 wire dict 或 `BindingEdgeResult` 传入（与 `WriterMaterialManifest.create`
        # 对成员的处理同一手法）；**id 派生只看 wire 形状**，因此先归一到 `to_dict()`，再让
        # dataclass 持有对象。否则 `edge_results` 不是 JSON 可序列化的，content id 无法派生
        # （即身份不可算）。
        edges = tuple(BindingEdgeResult.from_dict(x) if isinstance(x, Mapping) else x
                      for x in (kwargs.get("edge_results") or ()))
        body = {"subject_kind": kwargs.get("subject_kind"), "subject_id": kwargs.get("subject_id"),
                "draft_revision": kwargs.get("draft_revision"),
                "authority_kind": kwargs.get("authority_kind"),
                "manifest_id": kwargs.get("manifest_id"),
                "manifest_fingerprint": kwargs.get("manifest_fingerprint"),
                "support_set_digest": kwargs.get("support_set_digest"),
                "proposal_ids": tuple(kwargs.get("proposal_ids") or ()),
                "proposal_hashes": tuple(kwargs.get("proposal_hashes") or ()),
                "edge_results": [e.to_dict() for e in edges],
                "result": kwargs.get("result"),
                "rules_version": kwargs.get("rules_version") or CLAIM_BINDING_GATE_VERSION,
                "structural_reason_code": kwargs.get("structural_reason_code"),
                "schema_version": NARRATIVE_SCHEMA_VERSION}
        body["binding_decision_id"] = derive_binding_decision_id(_WireView(dict(body)))
        return cls(**{**body, "edge_results": edges})

    @classmethod
    def from_dict(cls, d: Any) -> "ClaimBindingDecision":
        allowed = {"binding_decision_id", "schema_version", "subject_kind", "subject_id",
                   "draft_revision", "authority_kind", "manifest_id", "manifest_fingerprint",
                   "support_set_digest", "proposal_ids", "proposal_hashes", "edge_results",
                   "result", "rules_version", "structural_reason_code"}
        d = _reject_unknown(d, allowed, "ClaimBindingDecision")
        return cls(
            binding_decision_id=_require_nonempty(d.get("binding_decision_id"),
                                                 "ClaimBindingDecision.binding_decision_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "ClaimBindingDecision.schema_version"),
            subject_kind=_require_nonempty(d.get("subject_kind"),
                                           "ClaimBindingDecision.subject_kind"),
            subject_id=_require_nonempty(d.get("subject_id"), "ClaimBindingDecision.subject_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "ClaimBindingDecision.draft_revision"),
            authority_kind=_require_nonempty(d.get("authority_kind"),
                                             "ClaimBindingDecision.authority_kind"),
            manifest_id=_require_nonempty(d.get("manifest_id"), "ClaimBindingDecision.manifest_id"),
            manifest_fingerprint=_require_nonempty(d.get("manifest_fingerprint"),
                                                   "ClaimBindingDecision.manifest_fingerprint"),
            support_set_digest=_require_nonempty(d.get("support_set_digest"),
                                                 "ClaimBindingDecision.support_set_digest"),
            proposal_ids=_str_tuple(d.get("proposal_ids") or (),
                                    "ClaimBindingDecision.proposal_ids"),
            proposal_hashes=_str_tuple(d.get("proposal_hashes") or (),
                                       "ClaimBindingDecision.proposal_hashes"),
            edge_results=tuple(BindingEdgeResult.from_dict(x)
                               for x in d.get("edge_results") or ()),
            result=_require_nonempty(d.get("result"), "ClaimBindingDecision.result"),
            rules_version=_require_nonempty(d.get("rules_version"),
                                            "ClaimBindingDecision.rules_version"),
            structural_reason_code=d.get("structural_reason_code"))


def derive_binding_decision_id(decision: Any) -> str:
    body = (decision.identity_body() if isinstance(decision, ClaimBindingDecision)
            else decision.body)
    return content_id("cbd_", body)


@dataclass(frozen=True)
class ClaimEntailmentDecision:
    """factual candidate revision 的**唯一**语义决定（§6.3.2，P9 所有者）。

    `verdict="rejected"` 同样是一条 typed 决定，**不是**缺记录；aggregate=fail 的 factual
    candidate 与任何 context subject 都是 **0 条**（本类不存在 context 分支字段）。
    """

    entailment_decision_id: str
    schema_version: str
    claim_candidate_id: str
    draft_revision: str
    binding_decision_id: str
    support_set_digest: str
    authorization_path: str
    authority_fact_keys: tuple[str, ...]
    rubric_version: str
    prompt_version: str
    model_policy: str
    verdict: str
    call_id: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"ClaimEntailmentDecision.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        for name in ("claim_candidate_id", "draft_revision", "binding_decision_id",
                     "support_set_digest", "rubric_version", "prompt_version", "model_policy",
                     "call_id"):
            _require_nonempty(getattr(self, name), f"ClaimEntailmentDecision.{name}")
        _require_enum(self.verdict, ENTAILMENT_VERDICTS, "ClaimEntailmentDecision.verdict")
        if self.authorization_path == "context_only":
            raise NarrativeSchemaError(
                "ClaimEntailmentDecision 不接受 context_only 路径：context 不进入语义门")
        _require_enum(self.authorization_path, AUTHORIZATION_PATHS,
                      "ClaimEntailmentDecision.authorization_path")
        object.__setattr__(self, "authority_fact_keys", _str_tuple(
            self.authority_fact_keys, "ClaimEntailmentDecision.authority_fact_keys"))
        if len(set(self.authority_fact_keys)) != len(self.authority_fact_keys):
            raise NarrativeSchemaError(
                "ClaimEntailmentDecision.authority_fact_keys 含重复（集合身份失效）")
        if self.verdict == "entailed":
            if self.reason_code is not None:
                raise NarrativeSchemaError("verdict=entailed 不得携带 reason_code")
        else:
            _require_enum(self.reason_code, ENTAILMENT_REJECTION_REASONS,
                          "ClaimEntailmentDecision.reason_code")
        expected = derive_entailment_decision_id(self)
        if self.entailment_decision_id != expected:
            raise NarrativeSchemaError(
                f"ClaimEntailmentDecision.entailment_decision_id 与内容不符：声明 "
                f"{self.entailment_decision_id!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "claim_candidate_id": self.claim_candidate_id,
            "draft_revision": self.draft_revision,
            "binding_decision_id": self.binding_decision_id,
            "support_set_digest": self.support_set_digest,
            "authorization_path": self.authorization_path,
            "authority_fact_keys": list(self.authority_fact_keys),
            "rubric_version": self.rubric_version,
            "prompt_version": self.prompt_version,
            "model_policy": self.model_policy,
            "verdict": self.verdict,
            "reason_code": self.reason_code,
            "call_id": self.call_id,
        }

    def to_dict(self) -> dict:
        return {"entailment_decision_id": self.entailment_decision_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ClaimEntailmentDecision":
        body = {"claim_candidate_id": kwargs.get("claim_candidate_id"),
                "draft_revision": kwargs.get("draft_revision"),
                "binding_decision_id": kwargs.get("binding_decision_id"),
                "support_set_digest": kwargs.get("support_set_digest"),
                "authorization_path": kwargs.get("authorization_path"),
                "authority_fact_keys": tuple(kwargs.get("authority_fact_keys") or ()),
                "rubric_version": kwargs.get("rubric_version") or CLAIM_ENTAILMENT_RULES_VERSION,
                "prompt_version": kwargs.get("prompt_version") or "",
                "model_policy": kwargs.get("model_policy") or "",
                "verdict": kwargs.get("verdict"), "reason_code": kwargs.get("reason_code"),
                "call_id": kwargs.get("call_id") or "",
                "schema_version": NARRATIVE_SCHEMA_VERSION}
        body["entailment_decision_id"] = derive_entailment_decision_id(_WireView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "ClaimEntailmentDecision":
        allowed = {"entailment_decision_id", "schema_version", "claim_candidate_id",
                   "draft_revision", "binding_decision_id", "support_set_digest",
                   "authorization_path", "authority_fact_keys", "rubric_version",
                   "prompt_version", "model_policy", "verdict", "reason_code", "call_id"}
        d = _reject_unknown(d, allowed, "ClaimEntailmentDecision")
        return cls(
            entailment_decision_id=_require_nonempty(
                d.get("entailment_decision_id"), "ClaimEntailmentDecision.entailment_decision_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "ClaimEntailmentDecision.schema_version"),
            claim_candidate_id=_require_nonempty(d.get("claim_candidate_id"),
                                                 "ClaimEntailmentDecision.claim_candidate_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "ClaimEntailmentDecision.draft_revision"),
            binding_decision_id=_require_nonempty(d.get("binding_decision_id"),
                                                  "ClaimEntailmentDecision.binding_decision_id"),
            support_set_digest=_require_nonempty(d.get("support_set_digest"),
                                                 "ClaimEntailmentDecision.support_set_digest"),
            authorization_path=_require_nonempty(d.get("authorization_path"),
                                                 "ClaimEntailmentDecision.authorization_path"),
            authority_fact_keys=_str_tuple(d.get("authority_fact_keys") or (),
                                           "ClaimEntailmentDecision.authority_fact_keys"),
            rubric_version=_require_nonempty(d.get("rubric_version"),
                                             "ClaimEntailmentDecision.rubric_version"),
            prompt_version=_require_nonempty(d.get("prompt_version"),
                                             "ClaimEntailmentDecision.prompt_version"),
            model_policy=_require_nonempty(d.get("model_policy"),
                                           "ClaimEntailmentDecision.model_policy"),
            verdict=_require_nonempty(d.get("verdict"), "ClaimEntailmentDecision.verdict"),
            reason_code=d.get("reason_code"),
            call_id=_require_nonempty(d.get("call_id"), "ClaimEntailmentDecision.call_id"))


def derive_entailment_decision_id(decision: Any) -> str:
    body = (decision.identity_body() if isinstance(decision, ClaimEntailmentDecision)
            else decision.body)
    return content_id("ced_", body)


@dataclass(frozen=True)
class AcceptedSupportBinding:
    """门后 **accepted 支撑边**（§6.3.3）：每个通过的 proposal 各一条。

    factual 变体必须携带 `entailment_decision_id`；context 变体**禁止**（且禁止任何 fact
    identity，在类型层与校验层同时不可表达）。**禁止**字段：`section_claim_id`、最终
    Narrative ID、`section_result_id` —— accepted binding 不得引用尚未形成的对象。
    """

    accepted_support_binding_id: str
    schema_version: str
    proposed_support_id: str
    proposal_content_hash: str
    binding_subject_kind: str
    binding_subject_id: str
    draft_revision: str
    binding_decision_id: str
    support_set_digest: str
    authority_kind: str
    authority_container_id: str
    source_identity: str
    provenance_identity: str
    support_role: str
    support_semantics: str
    authorization_path: str
    content_fingerprint: str
    entailment_decision_id: str | None = None
    fact_id: str | None = None
    financial_fact_id: str | None = None
    note_fact_id: str | None = None
    external_fact_id: str | None = None
    material_id: str | None = None
    payload_ref: dict | None = None
    locator_ref: dict | None = None

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"AcceptedSupportBinding.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        for name in ("proposed_support_id", "proposal_content_hash", "binding_subject_id",
                     "draft_revision", "binding_decision_id", "support_set_digest",
                     "authority_container_id", "source_identity", "provenance_identity",
                     "content_fingerprint"):
            _require_nonempty(getattr(self, name), f"AcceptedSupportBinding.{name}")
        _require_enum(self.binding_subject_kind, BINDING_SUBJECT_KINDS,
                      "AcceptedSupportBinding.binding_subject_kind")
        _require_enum(self.support_role, SUPPORT_ROLES, "AcceptedSupportBinding.support_role")
        if not _subject_matches_semantics(self.binding_subject_kind, self.support_semantics):
            raise NarrativeSchemaError(
                f"AcceptedSupportBinding: support_semantics={self.support_semantics!r} 与 "
                f"binding subject {self.binding_subject_kind!r} 不符")
        if self.support_semantics == "factual":
            _require_nonempty(self.entailment_decision_id,
                              "AcceptedSupportBinding.entailment_decision_id")
        elif self.entailment_decision_id is not None:
            raise NarrativeSchemaError(
                "context accepted binding 禁止携带 entailment_decision_id（context 不进入语义门）")
        locator, payload = validate_support_edge_shape(
            authority_kind=self.authority_kind, support_semantics=self.support_semantics,
            authorization_path=self.authorization_path,
            fact_fields={"fact_id": self.fact_id, "financial_fact_id": self.financial_fact_id,
                         "note_fact_id": self.note_fact_id,
                         "external_fact_id": self.external_fact_id},
            material_id=self.material_id, payload_ref=self.payload_ref,
            locator_ref=self.locator_ref, what="AcceptedSupportBinding")
        object.__setattr__(self, "locator_ref", locator)
        object.__setattr__(self, "payload_ref", payload)
        expected = derive_accepted_support_binding_id(self)
        if self.accepted_support_binding_id != expected:
            raise NarrativeSchemaError(
                f"AcceptedSupportBinding.accepted_support_binding_id 与内容不符：声明 "
                f"{self.accepted_support_binding_id!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "proposed_support_id": self.proposed_support_id,
            "proposal_content_hash": self.proposal_content_hash,
            "binding_subject_kind": self.binding_subject_kind,
            "binding_subject_id": self.binding_subject_id,
            "draft_revision": self.draft_revision,
            "binding_decision_id": self.binding_decision_id,
            "support_set_digest": self.support_set_digest,
            "authority_kind": self.authority_kind,
            "authority_container_id": self.authority_container_id,
            "source_identity": self.source_identity,
            "provenance_identity": self.provenance_identity,
            "support_role": self.support_role,
            "support_semantics": self.support_semantics,
            "authorization_path": self.authorization_path,
            "content_fingerprint": self.content_fingerprint,
            "entailment_decision_id": self.entailment_decision_id,
            "fact_id": self.fact_id,
            "financial_fact_id": self.financial_fact_id,
            "note_fact_id": self.note_fact_id,
            "external_fact_id": self.external_fact_id,
            "material_id": self.material_id,
            "payload_ref": dict(self.payload_ref) if self.payload_ref else None,
            "locator_ref": dict(self.locator_ref) if self.locator_ref else None,
        }

    def to_dict(self) -> dict:
        return {"accepted_support_binding_id": self.accepted_support_binding_id,
                **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "AcceptedSupportBinding":
        fields = ("proposed_support_id", "proposal_content_hash", "binding_subject_kind",
                  "binding_subject_id", "draft_revision", "binding_decision_id",
                  "support_set_digest", "authority_kind", "authority_container_id",
                  "source_identity", "provenance_identity", "support_role", "support_semantics",
                  "authorization_path", "content_fingerprint", "entailment_decision_id",
                  "fact_id", "financial_fact_id", "note_fact_id", "external_fact_id",
                  "material_id", "payload_ref", "locator_ref")
        # P1-1 方向：accepted binding **不得**引用尚未形成的对象。这些字段在本类里本来就不存在，
        # 只靠「字段不存在」拒是不够的——调用方传进来会被静默丢掉，于是「企图引用未来身份」既没
        # 成形也没有报错。多传未知键一律拒绝（不是忽略）。
        unknown = sorted(set(kwargs) - set(fields))
        if unknown:
            raise NarrativeSchemaError(
                f"AcceptedSupportBinding.create 收到未知字段 {unknown}：accepted binding 不得引用"
                "尚未形成的 SectionClaim / final Narrative / SectionResult（反向引用只能由后继侧持有）")
        body = {k: kwargs.get(k) for k in fields}
        body["locator_ref"] = _validate_locator(body.get("locator_ref"), "AcceptedSupportBinding")
        body["schema_version"] = NARRATIVE_SCHEMA_VERSION
        body["accepted_support_binding_id"] = derive_accepted_support_binding_id(
            _WireView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "AcceptedSupportBinding":
        allowed = {"accepted_support_binding_id", "schema_version", "proposed_support_id",
                   "proposal_content_hash", "binding_subject_kind", "binding_subject_id",
                   "draft_revision", "binding_decision_id", "support_set_digest",
                   "authority_kind", "authority_container_id", "source_identity",
                   "provenance_identity", "support_role", "support_semantics",
                   "authorization_path", "content_fingerprint", "entailment_decision_id",
                   "fact_id", "financial_fact_id", "note_fact_id", "external_fact_id",
                   "material_id", "payload_ref", "locator_ref"}
        d = _reject_unknown(d, allowed, "AcceptedSupportBinding")
        locator = d.get("locator_ref")
        return cls(
            accepted_support_binding_id=_require_nonempty(
                d.get("accepted_support_binding_id"),
                "AcceptedSupportBinding.accepted_support_binding_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "AcceptedSupportBinding.schema_version"),
            proposed_support_id=_require_nonempty(d.get("proposed_support_id"),
                                                  "AcceptedSupportBinding.proposed_support_id"),
            proposal_content_hash=_require_nonempty(
                d.get("proposal_content_hash"), "AcceptedSupportBinding.proposal_content_hash"),
            binding_subject_kind=_require_nonempty(
                d.get("binding_subject_kind"), "AcceptedSupportBinding.binding_subject_kind"),
            binding_subject_id=_require_nonempty(d.get("binding_subject_id"),
                                                 "AcceptedSupportBinding.binding_subject_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "AcceptedSupportBinding.draft_revision"),
            binding_decision_id=_require_nonempty(d.get("binding_decision_id"),
                                                  "AcceptedSupportBinding.binding_decision_id"),
            support_set_digest=_require_nonempty(d.get("support_set_digest"),
                                                 "AcceptedSupportBinding.support_set_digest"),
            authority_kind=_require_nonempty(d.get("authority_kind"),
                                             "AcceptedSupportBinding.authority_kind"),
            authority_container_id=_require_nonempty(
                d.get("authority_container_id"), "AcceptedSupportBinding.authority_container_id"),
            source_identity=_require_nonempty(d.get("source_identity"),
                                              "AcceptedSupportBinding.source_identity"),
            provenance_identity=_require_nonempty(
                d.get("provenance_identity"), "AcceptedSupportBinding.provenance_identity"),
            support_role=_require_nonempty(d.get("support_role"),
                                           "AcceptedSupportBinding.support_role"),
            support_semantics=_require_nonempty(d.get("support_semantics"),
                                                "AcceptedSupportBinding.support_semantics"),
            authorization_path=_require_nonempty(d.get("authorization_path"),
                                                 "AcceptedSupportBinding.authorization_path"),
            content_fingerprint=_require_nonempty(d.get("content_fingerprint"),
                                                  "AcceptedSupportBinding.content_fingerprint"),
            entailment_decision_id=d.get("entailment_decision_id"),
            fact_id=d.get("fact_id"), financial_fact_id=d.get("financial_fact_id"),
            note_fact_id=d.get("note_fact_id"), external_fact_id=d.get("external_fact_id"),
            material_id=d.get("material_id"),
            payload_ref=dict(d["payload_ref"]) if d.get("payload_ref") else None,
            locator_ref=locator if locator else None)


def derive_accepted_support_binding_id(binding: Any) -> str:
    body = (binding.identity_body() if isinstance(binding, AcceptedSupportBinding)
            else binding.body)
    return content_id("asb_", body)


def is_claim_support_ref_union_member(value: Any) -> bool:
    """`ClaimSupportRef` 只是**兼容 union 名称**：合法成员只有 proposal 与 accepted 两种。"""
    return isinstance(value, (ProposedSupportRef, AcceptedSupportBinding))


# ---------------------------------------------------------------------------
# legacy narrative 只读回放：narr-3 / narr-4 **各一个单版本 reader**（均**不得**升级为 narr-5）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Narr3SectionDraftView:
    """narr-3 `SectionDraft` 载荷的**只读** legacy 视图（migration·readback）。

    这是唯一允许读 narr-3 draft 载荷的入口。它**不**升级为 narr-4/narr-5，也不得冒充 current
    `SectionDraft`：视图类型不同，任何把它传进 current 链的调用都会在类型/字段层失败。
    """

    schema_version: str
    draft_id: str
    section_result_id: str
    payload: dict

    def __post_init__(self) -> None:
        if self.schema_version != LEGACY_NARRATIVE_SCHEMA_NARR3:
            raise NarrativeSchemaError(
                f"Narr3SectionDraftView 只承载 "
                f"{LEGACY_NARRATIVE_SCHEMA_NARR3!r} 载荷，得到 {self.schema_version!r}")
        _require_nonempty(self.draft_id, "Narr3SectionDraftView.draft_id")
        if not isinstance(self.payload, Mapping):
            raise NarrativeSchemaError("Narr3SectionDraftView.payload 必须是对象")
        if self.payload.get("schema_version") != self.schema_version:
            raise NarrativeSchemaError(
                "Narr3SectionDraftView.payload 的 schema_version 与视图不符")
        # narr-3 的 draft 携带 section_result_id；narr-4 的 draft **不得**有该字段。两者形状不同，
        # 因此这里必须显式要求它是非空字符串，不能靠默认值糊过去。
        if not isinstance(self.section_result_id, str) or not self.section_result_id:
            raise NarrativeSchemaError("Narr3SectionDraftView.section_result_id 不得为空")

    def to_dict(self) -> dict:
        return dict(self.payload)


def load_legacy_narrative_narr3_for_audit(payload: Any) -> Narr3SectionDraftView:
    """把 narr-3 commit 载荷**只读**还原为 narr-3 视图（不得升级为 narr-4）。

    fail-closed：payload 必须是对象、`schema_version` 必须**恰是** `"narr-3"`、且必须带 narr-3 的
    `section_result_id`。narr-4 载荷（**即使它也是登记的 legacy 版本**）、narr-5 current 载荷、
    或任何缺失 marker 的载荷一律拒——本 reader 是**单版本** reader。
    """
    if not isinstance(payload, Mapping):
        raise NarrativeSchemaError("narr-3 legacy 载荷必须是 JSON 对象")
    version = payload.get("schema_version")
    if version != LEGACY_NARRATIVE_SCHEMA_NARR3:
        raise NarrativeSchemaError(
            f"不是登记的 narr-3 legacy 载荷：schema_version={version!r}（不得以 legacy reader "
            "宽松读取 current 载荷或另一个 legacy 版本）")
    if "section_result_id" not in payload:
        raise NarrativeSchemaError(
            "narr-3 legacy 载荷必须携带 section_result_id（narr-4 draft 不得有该字段）")
    return Narr3SectionDraftView(
        schema_version=str(version),
        draft_id=str(payload.get("draft_id") or ""),
        section_result_id=str(payload.get("section_result_id") or ""),
        payload=dict(payload))


@dataclass(frozen=True)
class Narr4SectionDraftView:
    """narr-4 `SectionDraft` 载荷的**只读** legacy 视图（migration·readback）。

    §三 C 把 final Narrative 升到 narr-5（composed 句 + `context_binding_ids`）之后，磁盘上
    M930-3A/3B/3C 期间的 narr-4 记录仍然是**历史事实**：它们不得被原位重写，也不得被 current
    reader 静默重解释成 narr-5。本视图是唯一允许读 narr-4 draft 载荷的入口。
    """

    schema_version: str
    draft_id: str
    payload: dict

    def __post_init__(self) -> None:
        if self.schema_version != LEGACY_NARRATIVE_SCHEMA_NARR4:
            raise NarrativeSchemaError(
                f"Narr4SectionDraftView 只承载 {LEGACY_NARRATIVE_SCHEMA_NARR4!r} 载荷，"
                f"得到 {self.schema_version!r}")
        _require_nonempty(self.draft_id, "Narr4SectionDraftView.draft_id")
        if not isinstance(self.payload, Mapping):
            raise NarrativeSchemaError("Narr4SectionDraftView.payload 必须是对象")
        if self.payload.get("schema_version") != self.schema_version:
            raise NarrativeSchemaError(
                "Narr4SectionDraftView.payload 的 schema_version 与视图不符")
        # narr-4 相对 narr-3 的**形状差异**：draft 不再携带 `section_result_id`（Result→Draft 单向）。
        # 少了这条负向断言，narr-3 载荷就会被 narr-4 reader 宽松吞下。
        if "section_result_id" in self.payload:
            raise NarrativeSchemaError(
                "narr-4 legacy 载荷**不得**携带 section_result_id（那是 narr-3 的形状）")

    def to_dict(self) -> dict:
        return dict(self.payload)


def load_legacy_narrative_narr4_for_audit(payload: Any) -> Narr4SectionDraftView:
    """把 narr-4 commit 载荷**只读**还原为 narr-4 视图（不得升级为 narr-5）。

    fail-closed：payload 必须是对象、`schema_version` 必须**恰是** `"narr-4"`、且**不得**带 narr-3
    的 `section_result_id`。narr-5 current 载荷、narr-3 载荷、或任何缺失 marker 的载荷一律拒——
    与 narr-3 reader 对称，本 reader 也是**单版本** reader。
    """
    if not isinstance(payload, Mapping):
        raise NarrativeSchemaError("narr-4 legacy 载荷必须是 JSON 对象")
    version = payload.get("schema_version")
    if version != LEGACY_NARRATIVE_SCHEMA_NARR4:
        raise NarrativeSchemaError(
            f"不是登记的 narr-4 legacy 载荷：schema_version={version!r}（不得以 legacy reader "
            "宽松读取 current 载荷或另一个 legacy 版本）")
    if "section_result_id" in payload:
        raise NarrativeSchemaError(
            "narr-4 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
    return Narr4SectionDraftView(
        schema_version=str(version),
        draft_id=str(payload.get("draft_id") or ""),
        payload=dict(payload))


#: narr-5 载荷里**承载 legacy locator** 的字段名（`narr-6` 起改为 tagged union）。
_LEGACY_LOCATOR_FIELDS = ("locator_ref",)


def _legacy_locator_audit_payload(value: Any) -> Any:
    """把载荷里每个 `locator_ref` 换成 `loc-0` 审计 locator（其余字段逐字不动）。

    只读转换，**不**做任何升级：`(owner, start, end)` 在 legacy wire 上只可能是半开字符区间，
    这里按此读出并打上 `loc-0` 标签；读不出来的值原样保留（审计视图的职责是如实回放，
    不是替历史载荷「修正」）。
    """
    if isinstance(value, Mapping):
        out: dict = {}
        for key, item in value.items():
            if key in _LEGACY_LOCATOR_FIELDS and item is not None:
                try:
                    out[key] = load_legacy_locator_for_audit(item)
                except NarrativeSchemaError:
                    out[key] = item
            else:
                out[key] = _legacy_locator_audit_payload(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_legacy_locator_audit_payload(item) for item in value]
    return value


@dataclass(frozen=True)
class Narr5SectionDraftView:
    """`narr-5` `SectionDraft` 载荷的**只读** legacy 视图（migration·readback）。

    `narr-6` 把 `locator_ref` 升为 `loc-1` tagged union 之后，磁盘上 M930-3 §三 C 期间的 narr-5
    记录仍是历史事实：它们不得被原位重写，也不得被 current reader 静默重解释成 narr-6。
    本视图是唯一允许读 narr-5 draft 载荷的入口；`payload` 里的 locator 一律以 `loc-0`
    审计形态呈现（见 `_legacy_locator_audit_payload`），因此它进不了任何 current 决定。
    """

    schema_version: str
    draft_id: str
    payload: dict

    def __post_init__(self) -> None:
        if self.schema_version != LEGACY_NARRATIVE_SCHEMA_NARR5:
            raise NarrativeSchemaError(
                f"Narr5SectionDraftView 只承载 {LEGACY_NARRATIVE_SCHEMA_NARR5!r} 载荷，"
                f"得到 {self.schema_version!r}")
        _require_nonempty(self.draft_id, "Narr5SectionDraftView.draft_id")
        if not isinstance(self.payload, Mapping):
            raise NarrativeSchemaError("Narr5SectionDraftView.payload 必须是对象")
        if self.payload.get("schema_version") != self.schema_version:
            raise NarrativeSchemaError(
                "Narr5SectionDraftView.payload 的 schema_version 与视图不符")
        if "section_result_id" in self.payload:
            raise NarrativeSchemaError(
                "narr-5 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
        # narr-5 的形状标记：successor 六类对象都在（narr-4 也有；这里用来把 narr-5 与
        # 更早/更晚的载荷区分开——`section_result_id` 判据在上面已经排除了 narr-3）。
        if not self.payload.get("proposed_support_refs"):
            raise NarrativeSchemaError(
                "narr-5 legacy 载荷必须携带 proposed_support_refs（否则无法与 narr-4 区分）")

    def to_dict(self) -> dict:
        return dict(self.payload)


def load_legacy_narrative_narr5_for_audit(payload: Any) -> Narr5SectionDraftView:
    """把 narr-5 commit 载荷**只读**还原为 narr-5 视图（不得升级为 narr-6）。

    fail-closed：payload 必须是对象、`schema_version` 必须**恰是** `"narr-5"`、不得带 narr-3 的
    `section_result_id`、且必须携带 successor 形状字段。narr-6 current 载荷、其他 legacy 载荷、
    或任何缺失 marker 的载荷一律拒——本 reader 与 narr-3/4 reader 一样是**单版本** reader。
    """
    if not isinstance(payload, Mapping):
        raise NarrativeSchemaError("narr-5 legacy 载荷必须是 JSON 对象")
    version = payload.get("schema_version")
    if version != LEGACY_NARRATIVE_SCHEMA_NARR5:
        raise NarrativeSchemaError(
            f"不是登记的 narr-5 legacy 载荷：schema_version={version!r}（不得以 legacy reader "
            "宽松读取 current 载荷或另一个 legacy 版本）")
    if "section_result_id" in payload:
        raise NarrativeSchemaError(
            "narr-5 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
    return Narr5SectionDraftView(
        schema_version=str(version),
        draft_id=str(payload.get("draft_id") or ""),
        payload=_legacy_locator_audit_payload(dict(payload)))


@dataclass(frozen=True)
class Narr6SectionDraftView:
    """`narr-6` `SectionDraft` 载荷的**只读** legacy 视图（migration·readback）。

    `narr-7` 引入门前自然草稿之后，磁盘上**全部**冻结 run 工件（含 M930-3 的 r7b 真跑记录与
    `evaluation/results/**` 下的历史验收产物）都还是这一版。它们是历史事实：不得原位重写，
    也不得被 current reader 静默重解释成 narr-7。本视图是唯一允许读 narr-6 draft 载荷的入口。

    与 narr-5 视图的区别（也是两个 reader 各自的判据）：narr-6 的 `locator_ref` 已是 `loc-1`
    tagged union，因此载荷**逐字**回放，不做任何 locator 转换（`loc-0` 是 narr-5 的事）。
    """

    schema_version: str
    draft_id: str
    payload: dict

    def __post_init__(self) -> None:
        if self.schema_version != LEGACY_NARRATIVE_SCHEMA_NARR6:
            raise NarrativeSchemaError(
                f"Narr6SectionDraftView 只承载 {LEGACY_NARRATIVE_SCHEMA_NARR6!r} 载荷，"
                f"得到 {self.schema_version!r}")
        _require_nonempty(self.draft_id, "Narr6SectionDraftView.draft_id")
        if not isinstance(self.payload, Mapping):
            raise NarrativeSchemaError("Narr6SectionDraftView.payload 必须是对象")
        if self.payload.get("schema_version") != self.schema_version:
            raise NarrativeSchemaError(
                "Narr6SectionDraftView.payload 的 schema_version 与视图不符")
        if "section_result_id" in self.payload:
            raise NarrativeSchemaError(
                "narr-6 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
        # narr-7 的形状标记：门前自然草稿。它在场就说明这份载荷不是 narr-6 —— 必须由 narr-7
        # current reader 读，绝不能被本 reader「忽略未知字段」地吞下（那正是静默重解释）。
        if self.payload.get("natural_prose_draft"):
            raise NarrativeSchemaError(
                "narr-6 legacy 载荷不得携带 natural_prose_draft（那是 narr-7 的形状）："
                "带草稿层的载荷必须由 current reader 读，legacy reader 不得忽略它")
        # successor 形状标记：四组里至少一组非空（与 current `SectionDraft.__post_init__` 同一条
        # 不变量），因此「只有 marker 没有内容」的载荷不会经本 reader 通过。
        #
        # **为什么不要求 `proposed_support_refs` 非空（narr-5 reader 那样）**：这是被真实字节纠正的
        # 一处假设。r7b 冻结工件里 industry 那一束的 proposal 数为 **0** —— 它在提案形成之前就
        # 按设计停下、只留下 `unresolved_ids`（4 条）。那恰恰是必须能如实读回的历史事实；若这里
        # 要求 proposal 非空，唯一能读回它的路径就断了，而「读不回真实失败工件」比「少一条判别式」
        # 危险得多。
        if not any(self.payload.get(f) for f in ("claim_candidates", "narrative_draft_units",
                                                "proposed_support_refs", "unresolved_ids")):
            raise NarrativeSchemaError(
                "narr-6 legacy 载荷既无候选/单元/proposal 又无 unresolved_ids："
                "空载荷不是本节记录（不得以 marker 充当内容）")
        # locator 形状：有 proposal 时才可判（无 proposal 的载荷里根本没有 locator 可判——
        # 这一点如实承认，不用编出来的判别式假装 narr-6 与 narr-4 在那种载荷上可分；
        # 真正把它们分开的是上面那条**单版本** marker 精确比对）。
        self._locators_are_locator_1()

    def _locators_are_locator_1(self) -> None:
        """载荷里每个 `locator_ref` 必须已经是 `loc-1`（bare 三元组 = narr-5，一律拒）。"""
        for proposal in self.payload.get("proposed_support_refs") or ():
            if not isinstance(proposal, Mapping):
                continue
            try:
                validate_locator(proposal.get("locator_ref"),
                                 f"narr-6 legacy proposal "
                                 f"{proposal.get('proposed_support_id') or '<无 id>'}")
            except NarrativeSchemaError as exc:
                raise NarrativeSchemaError(
                    f"narr-6 legacy 载荷的 locator 不是 `loc-1`：{exc}") from exc

    def to_dict(self) -> dict:
        return dict(self.payload)


def load_legacy_narrative_narr6_for_audit(payload: Any) -> Narr6SectionDraftView:
    """把 narr-6 commit 载荷**只读**还原为 narr-6 视图（不得升级为 narr-7）。

    fail-closed：payload 必须是对象、`schema_version` 必须**恰是** `"narr-6"`、不得带 narr-3 的
    `section_result_id`、不得带 narr-7 的 `natural_prose_draft`、必须至少有一组 successor 内容
    （候选/单元/proposal/unresolved）、且**已携带的** `locator_ref` 必须已是 `loc-1`。narr-7
    current 载荷、其他 legacy 载荷、或任何缺失 marker 的载荷一律拒——本 reader 与 narr-3/4/5
    reader 一样是**单版本** reader。

    注意这里**不**要求 proposal 非空：r7b 的 industry 束就是 0 proposal + 4 unresolved 的真实
    记录（提案形成前按设计停下），必须能如实读回。
    """
    if not isinstance(payload, Mapping):
        raise NarrativeSchemaError("narr-6 legacy 载荷必须是 JSON 对象")
    version = payload.get("schema_version")
    if version != LEGACY_NARRATIVE_SCHEMA_NARR6:
        raise NarrativeSchemaError(
            f"不是登记的 narr-6 legacy 载荷：schema_version={version!r}（不得以 legacy reader "
            "宽松读取 current 载荷或另一个 legacy 版本）")
    if "section_result_id" in payload:
        raise NarrativeSchemaError(
            "narr-6 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
    return Narr6SectionDraftView(
        schema_version=str(version),
        draft_id=str(payload.get("draft_id") or ""),
        payload=dict(payload))


@dataclass(frozen=True)
class Narr7SectionDraftView:
    """`narr-7` `SectionDraft` 载荷的**只读** legacy 视图（migration·readback）。

    `narr-8` 把草稿的出处拆成两条互斥的轴（材料行 / 权威事实行）之后，`narr-7` 的草稿单元
    （出处**只有材料一条轴**）只能经本视图只读回放：它们是**另一个形状**的单元，不得被 current
    reader 静默重解释成 narr-8。

    与 narr-6 视图的区别（也是两个 reader 各自的判据）：narr-7 的草稿层**在场**（`SectionDraft.
    natural_prose_draft` 是它的形状标记），因此本视图**接受**它、并逐单元读出；而 narr-8 的形状
    标记（单元上的 `source_fact_refs`）一旦出现，本 reader 即拒——那说明这份载荷该由 current
    reader 读。locator 与 narr-6 同口径：已是 `loc-1` tagged union，逐字回放，不做任何转换。
    """

    schema_version: str
    draft_id: str
    payload: dict

    def __post_init__(self) -> None:
        if self.schema_version != LEGACY_NARRATIVE_SCHEMA_NARR7:
            raise NarrativeSchemaError(
                f"Narr7SectionDraftView 只承载 {LEGACY_NARRATIVE_SCHEMA_NARR7!r} 载荷，"
                f"得到 {self.schema_version!r}")
        _require_nonempty(self.draft_id, "Narr7SectionDraftView.draft_id")
        if not isinstance(self.payload, Mapping):
            raise NarrativeSchemaError("Narr7SectionDraftView.payload 必须是对象")
        if self.payload.get("schema_version") != self.schema_version:
            raise NarrativeSchemaError(
                "Narr7SectionDraftView.payload 的 schema_version 与视图不符")
        if "section_result_id" in self.payload:
            raise NarrativeSchemaError(
                "narr-7 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
        # narr-8 的形状标记：草稿单元的**事实侧出处**。它在场就说明这份载荷不是 narr-7 ——
        # 必须由 current reader 读，绝不能被本 reader「忽略未知字段」地吞下（那正是静默重解释）。
        self._drafts_are_material_only()
        if not any(self.payload.get(f) for f in ("claim_candidates", "narrative_draft_units",
                                                "proposed_support_refs", "unresolved_ids",
                                                "natural_prose_draft")):
            raise NarrativeSchemaError(
                "narr-7 legacy 载荷既无候选/单元/proposal/unresolved_ids 又无草稿层："
                "空载荷不是本节记录（不得以 marker 充当内容）")
        self._locators_are_locator_1()

    def _drafts_are_material_only(self) -> None:
        """每个草稿单元必须是**材料轴**形状（`source_member_refs` 非空、无 `source_fact_refs`）。"""
        for unit in self.payload.get("natural_prose_draft") or ():
            if not isinstance(unit, Mapping):
                raise NarrativeSchemaError("narr-7 legacy 载荷的草稿单元必须是对象")
            if "source_fact_refs" in unit:
                raise NarrativeSchemaError(
                    "narr-7 legacy 载荷的草稿单元不得携带 source_fact_refs（那是 narr-8 的形状）："
                    "带事实侧出处的载荷必须由 current reader 读，legacy reader 不得忽略它")
            if not unit.get("source_member_refs"):
                raise NarrativeSchemaError(
                    "narr-7 legacy 载荷的草稿单元必须有非空 source_member_refs"
                    "（narr-7 里出处只有材料一条轴）")

    def _locators_are_locator_1(self) -> None:
        """载荷里每个 `locator_ref` 必须已经是 `loc-1`（bare 三元组 = narr-5，一律拒）。"""
        for proposal in self.payload.get("proposed_support_refs") or ():
            if not isinstance(proposal, Mapping):
                continue
            try:
                validate_locator(proposal.get("locator_ref"),
                                 f"narr-7 legacy proposal "
                                 f"{proposal.get('proposed_support_id') or '<无 id>'}")
            except NarrativeSchemaError as exc:
                raise NarrativeSchemaError(
                    f"narr-7 legacy 载荷的 locator 不是 `loc-1`：{exc}") from exc

    def to_dict(self) -> dict:
        return dict(self.payload)


def load_legacy_narrative_narr7_for_audit(payload: Any) -> Narr7SectionDraftView:
    """把 narr-7 载荷**只读**还原为 narr-7 视图（不得升级为 narr-8）。

    fail-closed：payload 必须是对象、`schema_version` 必须**恰是** `"narr-7"`、不得带 narr-3 的
    `section_result_id`、不得带 narr-8 的 `source_fact_refs`、必须至少有一组内容（候选/单元/
    proposal/unresolved/草稿层）、且**已携带的** `locator_ref` 必须已是 `loc-1`。narr-8 current
    载荷、其他 legacy 载荷、或任何缺失 marker 的载荷一律拒——本 reader 与 narr-3/4/5/6 reader
    一样是**单版本** reader。
    """
    if not isinstance(payload, Mapping):
        raise NarrativeSchemaError("narr-7 legacy 载荷必须是 JSON 对象")
    version = payload.get("schema_version")
    if version != LEGACY_NARRATIVE_SCHEMA_NARR7:
        raise NarrativeSchemaError(
            f"不是登记的 narr-7 legacy 载荷：schema_version={version!r}（不得以 legacy reader "
            "宽松读取 current 载荷或另一个 legacy 版本）")
    if "section_result_id" in payload:
        raise NarrativeSchemaError(
            "narr-7 legacy 载荷不得携带 section_result_id（那是 narr-3 的形状）")
    return Narr7SectionDraftView(
        schema_version=str(version),
        draft_id=str(payload.get("draft_id") or ""),
        payload=dict(payload))


# ---------------------------------------------------------------------------
# FactNarrativeDisposition（successor / fnd-2）
# ---------------------------------------------------------------------------

#: FND **successor** 的 wire 版本（§16.7.1 P5 / P1-8，§16.10 #26、#39）。
#: 旧 `fnd-1`（只有三态 + `claim_ids`，无 source/provenance identity、无内容指纹、无
#: authority 专属 fact/decision 字段）**不**经本 reader 读回，也不得与 successor 混用。
FND_SCHEMA_VERSION = "fnd-2"
#: 逐 authority kind 的「qualification / validation authority ref」字段名：**恰好一个**在场。
#: topic_pack / external_snapshot 用 `fact_qualification_decision_id`（资格决定是该事实的权威
#: 来源）；financial_pack 用产出 FactPack 的**选材规则版本**；evidence_note 用附注集合的
#: **核验规则版本**。三者都是**权威自己的**版本字段——本层不得替它们发明第二套版本号。
FND_AUTHORITY_REF_BY_KIND = {
    "topic_pack": "fact_qualification_decision_id",
    "external_snapshot": "fact_qualification_decision_id",
    "financial_pack": "financial_selection_rule_version",
    "evidence_note": "note_validation_rule_version",
}
FND_AUTHORITY_REF_FIELDS = ("fact_qualification_decision_id",
                            "financial_selection_rule_version",
                            "note_validation_rule_version")


def _sorted_unique_ids(values: Any, what: str) -> tuple[str, ...]:
    """引用集合的**规范序**：去重后按字典序升序。非串/空串一律拒。"""
    items = tuple(str(v) for v in (values or ()))
    if not all(items):
        raise NarrativeSchemaError(f"{what} 不得含空 id")
    duplicates = sorted({i for i in items if items.count(i) > 1})
    if duplicates:
        raise NarrativeSchemaError(f"{what} 含重复 id：{duplicates}")
    return tuple(sorted(items))


def _require_fnd_keys(values: Any, what: str) -> tuple[tuple[str, str, str], ...]:
    """FND 集合键必须是 `(authority_kind, container_identity, authority_specific_fact_id)` 三元组。

    裸 `fact_id` 一律拒：不同 container / 不同 authority kind 下的同名 fact id 会被错误地
    当成同一条事实，从而让「集合相等」在跨容器时假通过（§16.7.1 P5：不得用裸 fact ID 去重）。
    """
    out: list[tuple[str, str, str]] = []
    for item in values or ():
        if isinstance(item, str):
            raise NarrativeSchemaError(
                f"{what} 的集合键不得使用裸 fact ID（{item!r}）：必须是 "
                "(authority_kind, container_identity, authority_specific_fact_id) 三元组，"
                "否则跨 container / 跨 kind 的同名 fact 会被当成同一条事实")
        parts = tuple(item)
        if len(parts) != 3 or not all(str(part or "") for part in parts):
            raise NarrativeSchemaError(
                f"{what} 的集合键必须是三格非空三元组，得到 {item!r}")
        out.append((str(parts[0]), str(parts[1]), str(parts[2])))
    duplicates = sorted({k for k in out if out.count(k) > 1})
    if duplicates:
        raise NarrativeSchemaError(f"{what} 的集合键重复：{duplicates}")
    return tuple(out)


@dataclass(frozen=True)
class FactNarrativeDisposition:
    """一条**被选中的预验证权威事实**在正文里的**去向**（§6.3：恰一条，不得静默省略）。

    **successor 定位（P1-8）**：只记录「该预验证权威事实在本次叙述中的去向」。它**不是**
    材料审计总账：不得替代 `ResearchMaterialDisposition`（研究侧材料去向）、
    `WriterMaterialProcessingDisposition`（Writer 侧材料处理）、拒绝决定或 research
    `ContractGap`。三类 disposition 身份互不继承、字段不相交、持久化 family 也各不相同。

    authority 维度是**封闭 tagged union**（§6.2.3 的四条路径 A 表；逐 kind 的
    required/forbidden 由 `validate_support_edge_shape` 唯一裁决，本类不另立第二套）：

    | `authority_kind` | 容器 identity | authority 专属 fact 字段 | qualification/validation ref |
    |---|---|---|---|
    | `topic_pack` | Pack `pack_id` | `fact_id`（`SupportedFact`） | `fact_qualification_decision_id` |
    | `financial_pack` | FactPack `artifact_id` | `financial_fact_id` | `financial_selection_rule_version` |
    | `evidence_note` | note 容器 id | `note_fact_id` | `note_validation_rule_version` |
    | `external_snapshot` | snapshot 记录 id | `external_fact_id` | `fact_qualification_decision_id` |

    跨 kind 的 fact 字段（如 topic_pack 携带 `external_fact_id`）一律拒——混装即拒，没有
    「泛化的 qualification_ref / authority ref」逃逸字段可绕过。
    """

    disposition_id: str
    schema_version: str
    authority_kind: str
    authority_container_id: str
    disposition: str
    required: bool
    source_identity: str
    provenance_identity: str
    content_fingerprint: str
    accepted_binding_ids: tuple[str, ...] = ()
    section_claim_ids: tuple[str, ...] = ()
    reason_code: str | None = None
    unresolved_id: str | None = None
    pack_id: str | None = None
    fact_id: str | None = None
    financial_fact_id: str | None = None
    note_fact_id: str | None = None
    external_fact_id: str | None = None
    fact_qualification_decision_id: str | None = None
    financial_selection_rule_version: str | None = None
    note_validation_rule_version: str | None = None
    material_id: str | None = None
    payload_ref: dict | None = None
    locator_ref: dict | None = None

    def __post_init__(self) -> None:
        if self.schema_version != FND_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"FactNarrativeDisposition.schema_version 必须为 {FND_SCHEMA_VERSION!r}"
                f"（得到 {self.schema_version!r}：fnd-1 载荷不得冒充 successor）")
        _require_enum(self.authority_kind, AUTHORITY_KINDS, "FactNarrativeDisposition.authority_kind")
        _require_nonempty(self.authority_container_id,
                          "FactNarrativeDisposition.authority_container_id")
        _require_enum(self.disposition, NARRATIVE_DISPOSITIONS, "FactNarrativeDisposition.disposition")
        if not isinstance(self.required, bool):
            raise NarrativeSchemaError("FactNarrativeDisposition.required 必须是布尔")
        for name in ("source_identity", "provenance_identity", "content_fingerprint"):
            _require_nonempty(getattr(self, name), f"FactNarrativeDisposition.{name}")
        object.__setattr__(self, "accepted_binding_ids",
                           _str_tuple(self.accepted_binding_ids,
                                      "FactNarrativeDisposition.accepted_binding_ids"))
        object.__setattr__(self, "section_claim_ids",
                           _str_tuple(self.section_claim_ids,
                                      "FactNarrativeDisposition.section_claim_ids"))
        for name in ("accepted_binding_ids", "section_claim_ids"):
            values = getattr(self, name)
            if tuple(sorted(set(values))) != values:
                raise NarrativeSchemaError(
                    f"FactNarrativeDisposition.{name} 必须是**确定性排序、无重复**的 id 元组"
                    f"（得到 {list(values)}）：顺序漂移会让「完整有序精确集合」失去意义")

        # -- authority tagged union：形状由唯一的边校验器裁决（不另立第二套真值表） --
        locator, payload = validate_support_edge_shape(
            authority_kind=self.authority_kind, support_semantics="factual",
            authorization_path="path_a_prevalidated",
            fact_fields={name: getattr(self, name) for name in ALL_FACT_FIELDS},
            material_id=self.material_id, payload_ref=self.payload_ref,
            locator_ref=self.locator_ref, what="FactNarrativeDisposition")
        object.__setattr__(self, "locator_ref", locator)
        object.__setattr__(self, "payload_ref", payload)
        if self.authority_kind == "topic_pack":
            _require_nonempty(self.pack_id, "FactNarrativeDisposition.pack_id")
            if self.pack_id != self.authority_container_id:
                raise NarrativeSchemaError(
                    f"FactNarrativeDisposition: topic_pack 的容器就是 Pack 自己（pack_id="
                    f"{self.pack_id!r}，authority_container_id={self.authority_container_id!r}）")
            if self.locator_ref is not None:
                raise NarrativeSchemaError(
                    "FactNarrativeDisposition: topic_pack 事实的定位由 material payload 承担，"
                    "不得携带 locator_ref")
            # `payload_ref` 只在有 material 载体时能解析（由边形状校验器拒），但**反过来不成立**：
            # `_resolve_material_binding` 的返回类型是 `(material_id, payload_ref | None)`，
            # 上游 material 绑定本身就可能不带 payload。因此这里不要求「material 必带 payload」
            # ——那会把写入侧合法产物判成非法；「上游没有 material」同样不是漏绑
            # （`(None, None)` 是合法结果）。
        elif self.authority_kind == "external_snapshot":
            _require_nonempty(self.pack_id, "FactNarrativeDisposition.pack_id")
            if self.pack_id == self.authority_container_id:
                raise NarrativeSchemaError(
                    "FactNarrativeDisposition: external_snapshot 的容器是 snapshot 记录身份，"
                    "必须与承载该 `ExternalFact` 的 current Pack 分开表达")
        ref_field = FND_AUTHORITY_REF_BY_KIND[self.authority_kind]
        _require_nonempty(getattr(self, ref_field),
                          f"FactNarrativeDisposition.{ref_field}")
        foreign_refs = sorted(name for name in FND_AUTHORITY_REF_FIELDS
                              if name != ref_field and getattr(self, name) is not None)
        if foreign_refs:
            raise NarrativeSchemaError(
                f"FactNarrativeDisposition: {self.authority_kind} 不得携带 {foreign_refs}"
                "（authority kind 混装）")

        # -- 三态语义（P1-8）：声称与引用必须同时成立 --
        if self.disposition == "claimed":
            if not self.accepted_binding_ids:
                raise NarrativeSchemaError(
                    "disposition=claimed 必须绑定至少一条 factual accepted binding"
                    "（没有接受的支撑边就不是「已写入正文」）")
            if not self.section_claim_ids:
                raise NarrativeSchemaError(
                    "disposition=claimed 必须绑定至少一条 SectionClaim")
            if self.reason_code is not None:
                raise NarrativeSchemaError("disposition=claimed 不得携带 reason_code")
        elif self.disposition == "supporting_only":
            if not self.accepted_binding_ids:
                raise NarrativeSchemaError(
                    "disposition=supporting_only 必须绑定至少一条 accepted binding")
            if self.section_claim_ids:
                raise NarrativeSchemaError(
                    "disposition=supporting_only 不得绑定 SectionClaim：该事实不得作为任何"
                    " Claim 的 primary authority，也不得伪造 body claim")
            if self.reason_code is None:
                raise NarrativeSchemaError(
                    "disposition=supporting_only 必须给出登记过的 reason_code")
            _require_enum(self.reason_code, DISPOSITION_REASON_CODES,
                          "FactNarrativeDisposition.reason_code")
        else:
            if self.accepted_binding_ids or self.section_claim_ids:
                raise NarrativeSchemaError(
                    "disposition=not_presented_with_reason 不得引用任何 accepted binding / "
                    "SectionClaim（未呈现的事实不该有正文引用）")
            if self.reason_code is None:
                raise NarrativeSchemaError(
                    "disposition=not_presented_with_reason 必须给出登记过的 reason_code")
            _require_enum(self.reason_code, DISPOSITION_REASON_CODES,
                          "FactNarrativeDisposition.reason_code")

        if self.required and self.disposition != "claimed" and self.unresolved_id is None:
            raise NarrativeSchemaError(
                f"required fact {self.authority_specific_fact_id!r} 的去向是 "
                f"{self.disposition!r}：必须绑定显式 SectionUnresolved/block projection"
                "（required 事实不得静默不呈现）")
        if self.unresolved_id is not None and self.disposition == "claimed":
            raise NarrativeSchemaError("已 claim 的事实不得同时绑定 unresolved_id")

        expected = derive_disposition_id(self)
        if self.disposition_id != expected:
            raise NarrativeSchemaError(
                f"FactNarrativeDisposition.disposition_id 与内容不符：声明 "
                f"{self.disposition_id!r}，应为 {expected!r}")

    # -- 集合键 / 容器别名（与 P17 v2 family 的唯一键同形）------------------------

    @property
    def authority_specific_fact_id(self) -> str:
        """该 authority kind 自己的 fact 身份（由 `FACT_FIELD_BY_AUTHORITY_KIND` 唯一映射）。"""
        field = FACT_FIELD_BY_AUTHORITY_KIND[self.authority_kind]
        return str(getattr(self, field) or "")

    @property
    def container_identity(self) -> str:
        """authority 容器身份（`authority_container_id` 的只读别名，与 v2 表列同名）。"""
        return self.authority_container_id

    @property
    def disposition_key(self) -> tuple[str, str, str]:
        """FND 的**唯一集合键** `(authority_kind, container_identity, fact id)`（§16.7.1 P5）。"""
        return (self.authority_kind, self.container_identity,
                self.authority_specific_fact_id)

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "authority_kind": self.authority_kind,
            "authority_container_id": self.authority_container_id,
            "disposition": self.disposition,
            "required": self.required,
            "source_identity": self.source_identity,
            "provenance_identity": self.provenance_identity,
            "content_fingerprint": self.content_fingerprint,
            "accepted_binding_ids": list(self.accepted_binding_ids),
            "section_claim_ids": list(self.section_claim_ids),
            "reason_code": self.reason_code,
            "unresolved_id": self.unresolved_id,
            "pack_id": self.pack_id,
            "fact_id": self.fact_id,
            "financial_fact_id": self.financial_fact_id,
            "note_fact_id": self.note_fact_id,
            "external_fact_id": self.external_fact_id,
            "fact_qualification_decision_id": self.fact_qualification_decision_id,
            "financial_selection_rule_version": self.financial_selection_rule_version,
            "note_validation_rule_version": self.note_validation_rule_version,
            "material_id": self.material_id,
            "payload_ref": dict(self.payload_ref) if self.payload_ref else None,
            "locator_ref": dict(self.locator_ref) if self.locator_ref else None,
        }

    def to_dict(self) -> dict:
        return {"disposition_id": self.disposition_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "FactNarrativeDisposition":
        fields = ("authority_kind", "authority_container_id", "disposition", "required",
                  "source_identity", "provenance_identity", "content_fingerprint",
                  "accepted_binding_ids", "section_claim_ids", "reason_code", "unresolved_id",
                  "pack_id", "fact_id", "financial_fact_id", "note_fact_id",
                  "external_fact_id", "fact_qualification_decision_id",
                  "financial_selection_rule_version", "note_validation_rule_version",
                  "material_id", "payload_ref", "locator_ref")
        body = {k: kwargs.get(k) for k in fields}
        body["accepted_binding_ids"] = _sorted_unique_ids(
            body.get("accepted_binding_ids"), "FactNarrativeDisposition.accepted_binding_ids")
        body["section_claim_ids"] = _sorted_unique_ids(
            body.get("section_claim_ids"), "FactNarrativeDisposition.section_claim_ids")
        body["locator_ref"] = _validate_locator(body.get("locator_ref"),
                                                "FactNarrativeDisposition")
        body["schema_version"] = FND_SCHEMA_VERSION
        body["disposition_id"] = derive_disposition_id(_DispositionView(dict(body)))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "FactNarrativeDisposition":
        allowed = {"disposition_id", "schema_version", "authority_kind",
                   "authority_container_id", "disposition", "required", "source_identity",
                   "provenance_identity", "content_fingerprint", "accepted_binding_ids",
                   "section_claim_ids", "reason_code", "unresolved_id", "pack_id", "fact_id",
                   "financial_fact_id", "note_fact_id", "external_fact_id",
                   "fact_qualification_decision_id", "financial_selection_rule_version",
                   "note_validation_rule_version", "material_id", "payload_ref", "locator_ref"}
        d = _reject_unknown(d, allowed, "FactNarrativeDisposition")
        locator = d.get("locator_ref")
        return cls(
            disposition_id=_require_nonempty(d.get("disposition_id"),
                                             "FactNarrativeDisposition.disposition_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "FactNarrativeDisposition.schema_version"),
            authority_kind=_require_nonempty(d.get("authority_kind"),
                                             "FactNarrativeDisposition.authority_kind"),
            authority_container_id=_require_nonempty(
                d.get("authority_container_id"), "FactNarrativeDisposition.authority_container_id"),
            disposition=_require_nonempty(d.get("disposition"),
                                          "FactNarrativeDisposition.disposition"),
            required=bool(d.get("required")),
            source_identity=_require_nonempty(d.get("source_identity"),
                                              "FactNarrativeDisposition.source_identity"),
            provenance_identity=_require_nonempty(
                d.get("provenance_identity"), "FactNarrativeDisposition.provenance_identity"),
            content_fingerprint=_require_nonempty(
                d.get("content_fingerprint"), "FactNarrativeDisposition.content_fingerprint"),
            accepted_binding_ids=_str_tuple(
                d.get("accepted_binding_ids") or (), "FactNarrativeDisposition.accepted_binding_ids"),
            section_claim_ids=_str_tuple(
                d.get("section_claim_ids") or (), "FactNarrativeDisposition.section_claim_ids"),
            reason_code=d.get("reason_code"), unresolved_id=d.get("unresolved_id"),
            pack_id=d.get("pack_id"), fact_id=d.get("fact_id"),
            financial_fact_id=d.get("financial_fact_id"), note_fact_id=d.get("note_fact_id"),
            external_fact_id=d.get("external_fact_id"),
            fact_qualification_decision_id=d.get("fact_qualification_decision_id"),
            financial_selection_rule_version=d.get("financial_selection_rule_version"),
            note_validation_rule_version=d.get("note_validation_rule_version"),
            material_id=d.get("material_id"),
            payload_ref=dict(d["payload_ref"]) if d.get("payload_ref") else None,
            locator_ref=locator if locator else None)


def verify_fnd_key_set(selected_keys: Any, dispositions: Any,
                       *, what: str = "FND") -> tuple[tuple[str, str, str], ...]:
    """FND 集合键必须与「本次选中的预验证权威事实键集」**精确相等**（§16.7.1 P5）。

    不得缺、不得多、不得重；两侧都按 `(authority_kind, container_identity,
    authority_specific_fact_id)` 三元组比较（裸 fact id 直接拒）。返回声明的键序（升序）。
    """
    selected = _require_fnd_keys(selected_keys, f"{what}.selected_keys")
    declared = _require_fnd_keys([d.disposition_key for d in (dispositions or ())],
                                 f"{what}.disposition_keys")
    missing = sorted(set(selected) - set(declared))
    extra = sorted(set(declared) - set(selected))
    if missing or extra:
        raise NarrativeSchemaError(
            f"{what} 集合键与选中的权威事实不相等：缺 {missing[:6]}，多 {extra[:6]}")
    return declared


def verify_fnd_reference_sets(disp: "FactNarrativeDisposition", *,
                              accepted_binding_ids: Any, section_claim_ids: Any,
                              claim_binding_ids: Mapping[str, Sequence[str]] | None = None,
                              binding_roles: Mapping[str, str] | None = None) -> None:
    """按「**完整、有序、精确集合**」重算一条 FND 的 binding / Claim 引用（P1-8）。

    - `accepted_binding_ids` / `section_claim_ids`：该 fact 在本次叙述里**实际参与**的完整
      有序集合。声明必须与之**逐项相等**——只证明「至少一条」不算（§16.10 #39）。
    - `claim_binding_ids`：Claim → 它引用的 factual accepted binding 集合。用于 `claimed` 的
      双向闭合：每个声明的 binding 都必须能解析到所列 Claim，且所列 Claim 的 binding 集不得
      出现未声明的项（反向无遗漏）。
    - `binding_roles`：binding → `support_role`。`supporting_only` 必须全为 `corroborating`：
      它不得作为任何 Claim 的 primary authority。
    """
    if not isinstance(disp, FactNarrativeDisposition):
        raise NarrativeSchemaError("verify_fnd_reference_sets 只接受 FactNarrativeDisposition")
    actual_bindings = tuple(str(v) for v in (accepted_binding_ids or ()))
    actual_claims = tuple(str(v) for v in (section_claim_ids or ()))
    if disp.accepted_binding_ids != actual_bindings:
        raise NarrativeSchemaError(
            f"FND {disp.disposition_key} 的 accepted_binding_ids 不是完整有序精确集合：声明 "
            f"{list(disp.accepted_binding_ids)}，实际 {list(actual_bindings)}")
    if disp.section_claim_ids != actual_claims:
        raise NarrativeSchemaError(
            f"FND {disp.disposition_key} 的 section_claim_ids 不是完整有序精确集合：声明 "
            f"{list(disp.section_claim_ids)}，实际 {list(actual_claims)}")

    if disp.disposition == "supporting_only":
        if actual_claims:
            raise NarrativeSchemaError(
                "supporting_only 的事实不得参与任何 SectionClaim 引用")
        roles = dict(binding_roles or {})
        primary = sorted(b for b in disp.accepted_binding_ids
                         if roles.get(b) not in (None, "corroborating"))
        if primary:
            raise NarrativeSchemaError(
                f"supporting_only 的 accepted binding 必须全为 corroborating，实测 {primary}"
                " 不是：它不得作为任何 Claim 的 primary authority")
        return
    if disp.disposition != "claimed":
        return

    mapping = {str(k): tuple(str(v) for v in vals)
               for k, vals in dict(claim_binding_ids or {}).items()}
    unknown_claims = sorted(set(actual_claims) - set(mapping))
    if unknown_claims:
        raise NarrativeSchemaError(
            f"claimed 的 FND 引用了不存在的 SectionClaim：{unknown_claims[:6]}")
    declared = set(disp.accepted_binding_ids)
    for claim_id in actual_claims:
        for binding_id in mapping[claim_id]:
            if binding_id not in declared:
                raise NarrativeSchemaError(
                    f"claimed 的 FND 漏声明了 Claim {claim_id!r} 参与的 binding "
                    f"{binding_id!r}（反向无遗漏）")
    allowed = {b for claim_id in actual_claims for b in mapping[claim_id]}
    dangling = sorted(declared - allowed)
    if dangling:
        raise NarrativeSchemaError(
            f"claimed 的 FND 声明的 binding 无法解析到所列 Claim：{dangling[:6]}")


def derive_disposition_id(disp: Any) -> str:
    body = disp.identity_body() if isinstance(disp, FactNarrativeDisposition) else disp.body
    return content_id("fnd_", body)


class _DispositionView:
    __slots__ = ("body",)

    def __init__(self, body: dict) -> None:
        self.body = body


# ---------------------------------------------------------------------------
# NarrativeSentence / NarrativeParagraph
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeSentence:
    """一个句子 + 它声明的 Claim 支撑（factual）或显式的非事实过渡（transition）。"""

    sentence_id: str
    paragraph_id: str
    index: int
    text: str
    sentence_kind: str
    claim_ids: tuple[str, ...] = ()
    citation_ids: tuple[str, ...] = ()
    numeric_tokens: tuple[str, ...] = ()
    #: 过渡句携带的连接语，必须**逐字**取自封闭词表 `CONNECTORS`（§十：模型只能组织，
    #: 不能创造事实表面）。事实句恒为 None——事实表面的全部字符都来自 Claim 文本。
    connector: str | None = None
    #: 本句**实际使用**的 context accepted binding（narr-5，§三 C）。context 只承担背景/组织/
    #: 衔接，不授权任何事实：因此它既不能免掉 `composed` 句的高风险表面核验，也不能让一句
    #: 不带 Claim 的句子取得事实地位。
    context_binding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.paragraph_id, "NarrativeSentence.paragraph_id")
        _require_nonempty(self.text, "NarrativeSentence.text")
        _require_enum(self.sentence_kind, SENTENCE_KINDS, "NarrativeSentence.sentence_kind")
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise NarrativeSchemaError("NarrativeSentence.index 必须为 >=0 的整数")
        object.__setattr__(self, "claim_ids", _str_tuple(self.claim_ids, "NarrativeSentence.claim_ids"))
        object.__setattr__(self, "citation_ids",
                           _str_tuple(self.citation_ids, "NarrativeSentence.citation_ids"))
        object.__setattr__(self, "context_binding_ids",
                           _str_tuple(self.context_binding_ids,
                                      "NarrativeSentence.context_binding_ids"))
        scanned = scan_numeric_tokens(self.text)
        declared = _str_tuple(self.numeric_tokens, "NarrativeSentence.numeric_tokens")
        if declared != scanned:
            raise NarrativeSchemaError(
                f"句子 {self.sentence_id or '<new>'} 声明的 numeric_tokens 与文本扫描结果不符："
                f"声明 {declared}，扫描 {scanned}（不得隐藏正文里的数字）")

        if self.sentence_kind == "factual":
            if self.connector is not None:
                raise NarrativeSchemaError(
                    "factual 句子不得携带 connector（事实句的每个字符都必须来自 Claim 文本）")
            if self.context_binding_ids:
                raise NarrativeSchemaError(
                    "factual 句子不得携带 context_binding_ids：逐字事实句的支撑只有它引用的"
                    "Claim，混入 context 绑定会让「谁授权了这句话」变得不可判定")
            if not self.claim_ids:
                raise NarrativeSchemaError(
                    "factual 句子必须绑定至少一条 claim_id（否则就是无据陈述）")
            if not self.citation_ids:
                raise NarrativeSchemaError("factual 句子必须绑定至少一条 citation_id")
        elif self.sentence_kind in ("composed", "natural"):
            # `natural`（narr-7）与 `composed` 的**构造期**约束逐条相同：连接语写在 text 里、
            # 至少一条 Claim、至少一条引用、可携带 context 绑定。两者唯一的差别在**核验期**的
            # 第 8 条（逐字 vs 保真），那一处在 `verify_section_narrative` 里分派，不在这里——
            # 若在这里也分叉一次，两条路径的构造约束就会各自漂移，而「同一份句子在两个句类下
            # 构造结果不同」是最难发现的一类分叉。
            if self.connector is not None:
                raise NarrativeSchemaError(
                    f"{self.sentence_kind} 句子不得携带 connector：连接语是封闭词表的独立句类，"
                    f"{self.sentence_kind} 句的自由成分必须写在 text 里并接受高风险表面核验")
            if not self.claim_ids:
                raise NarrativeSchemaError(
                    f"{self.sentence_kind} 句子必须绑定至少一条已接受 claim_id（context 不授权"
                    f"事实，因此没有 Claim 的 {self.sentence_kind} 句就是无据陈述）")
            if not self.citation_ids:
                raise NarrativeSchemaError(
                    f"{self.sentence_kind} 句子必须绑定至少一条 citation_id"
                    "（引用只能由它声明的 Claim 派生）")
        else:
            if self.claim_ids:
                raise NarrativeSchemaError("transition 句子不得携带 claim_id（过渡语不得断言事实）")
            if self.citation_ids:
                raise NarrativeSchemaError("transition 句子不得携带 citation_id")
            if self.context_binding_ids:
                raise NarrativeSchemaError(
                    "transition 句子不得携带 context_binding_ids（连接语本身不引用任何材料）")
            if self.connector not in CONNECTORS:
                raise NarrativeSchemaError(
                    f"transition 句子的 connector={self.connector!r} 不在封闭词表内："
                    f"{list(CONNECTORS)}（连接语不得自造）")
            if self.text != self.connector:
                raise NarrativeSchemaError(
                    f"transition 句子文本必须逐字等于 connector（{self.connector!r}），"
                    f"实为 {self.text!r}（连接语之外的自由文本不可进入正文）")
            if scanned:
                raise NarrativeSchemaError(
                    f"transition 句子不得包含数字：{list(scanned)}（数字必须由权威事实支撑）")

        expected = derive_sentence_id(self)
        if self.sentence_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeSentence.sentence_id 与内容不符：声明 {self.sentence_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        """句子的**内容身份**：文本与它声明的绑定集合**必须同源**。

        §三 C 的硬要求是「句子文本与绑定共用一个内容身份，任一改动都强制重新校验」。做法就是
        把 `text` 与 `claim_ids` / `citation_ids` / `context_binding_ids` 一起放进被哈希的 body：
        改文本会改 `sentence_id`，改绑定也会改 `sentence_id`，两者都逃不过下游的重算比对。
        """
        return {
            "paragraph_id": self.paragraph_id, "index": self.index, "text": self.text,
            "sentence_kind": self.sentence_kind, "claim_ids": list(self.claim_ids),
            "citation_ids": list(self.citation_ids), "numeric_tokens": list(self.numeric_tokens),
            "connector": self.connector,
            "context_binding_ids": list(self.context_binding_ids),
        }

    def to_dict(self) -> dict:
        return {"sentence_id": self.sentence_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "NarrativeSentence":
        text = kwargs.get("text", "")
        body = {
            "paragraph_id": kwargs.get("paragraph_id", ""), "index": kwargs.get("index", 0),
            "text": text, "sentence_kind": kwargs.get("sentence_kind", "factual"),
            "claim_ids": tuple(kwargs.get("claim_ids") or ()),
            "citation_ids": tuple(kwargs.get("citation_ids") or ()),
            "numeric_tokens": scan_numeric_tokens(text),
            "connector": kwargs.get("connector"),
            "context_binding_ids": tuple(kwargs.get("context_binding_ids") or ()),
        }
        body["sentence_id"] = content_id("nsent_", body)
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeSentence":
        d = _reject_unknown(d, {"sentence_id", "paragraph_id", "index", "text", "sentence_kind",
                                "claim_ids", "citation_ids", "numeric_tokens", "connector",
                                "context_binding_ids"},
                            "NarrativeSentence")
        return cls(
            sentence_id=_require_nonempty(d.get("sentence_id"), "NarrativeSentence.sentence_id"),
            paragraph_id=_require_nonempty(d.get("paragraph_id"), "NarrativeSentence.paragraph_id"),
            index=_req(d, "index", "NarrativeSentence"),
            text=_require_nonempty(d.get("text"), "NarrativeSentence.text"),
            sentence_kind=_require_nonempty(d.get("sentence_kind"), "NarrativeSentence.sentence_kind"),
            claim_ids=_str_tuple(d.get("claim_ids") or (), "NarrativeSentence.claim_ids"),
            citation_ids=_str_tuple(d.get("citation_ids") or (), "NarrativeSentence.citation_ids"),
            numeric_tokens=_str_tuple(d.get("numeric_tokens") or (), "NarrativeSentence.numeric_tokens"),
            connector=d.get("connector"),
            context_binding_ids=_str_tuple(d.get("context_binding_ids") or (),
                                           "NarrativeSentence.context_binding_ids"))

#: 事实句里多条 Claim 并置时的分隔符（**唯一**允许出现在 Claim 文本之间的字符）。
FACTUAL_CLAIM_SEPARATOR = "；"


def render_factual_text(claim_texts: Sequence[str]) -> str:
    """把一条或多条 Claim 文本**逐字**渲染成句（§十：正文不得新增事实表面）。

    规则只做排版归一，不新增任何实词：
    1. 每条 Claim 去掉尾部已有的句末标点（`。` / `；`）；
    2. 多条 Claim 之间用 `；` 连接（唯一的连接字符）；
    3. 末尾补一个 `。`。

    因此渲染结果里出现的每一个数字、实体、期间都**逐字**来自 Claim 文本本身。
    """
    parts: list[str] = []
    for raw in claim_texts:
        text = str(raw or "").strip()
        while text and text[-1] in "。；;":
            text = text[:-1].strip()
        if text:
            parts.append(text)
    if not parts:
        raise NarrativeSchemaError("事实句没有任何 Claim 文本可渲染（不得凭空空写）")
    return FACTUAL_CLAIM_SEPARATOR.join(parts) + "。"


#: 「事实未覆盖」的权威 aspect 状态（§七 P0-4 2：这类状态必须留下缺口，不得消失）。
#: 唯一一份实现：写入侧（`pack_writer`）与章节级规则（`rules_evaluator`）都取这里。
NON_COVERED_ASPECT_STATUSES = ("partial", "blocked", "not_found")


def amount_sentence(label: str, period: str, value: str) -> str:
    """权威字段 → 可读断言句：`[期间]的[科目/指标]为[权威数值渲染]。`

    §六：这是**唯一**的权威事实表面拼装实现 —— 写入侧（`pack_writer` 构造 Claim 文本）与
    硬门（`authoritative_fact_surface` 复核 Claim 是否属于该 fact）必须共用它；两套口径会让
    「这条 Claim 属于哪条权威事实」被各自解释。本函数不做任何计算、换算、四舍五入或补位，
    也**不**追加 `unit`（权威 `display` 已带单位，再拼一次会造出不存在的量纲）。
    """
    head = f"{period}的" if period else ""
    core = f"{label}为{value}" if label else value
    return f"{head}{core}。"


#: 财务权威自己声明的**代理口径状态**（`financial_v2.schema` 的封闭取值之一）。它只表示
#: 「这个数值是用代理输入算出来的」，**不是**模型的判断，也不是本层的判断。
PROXY_STATUS = "CALCULATED_PROXY"
#: 代理口径的**显式标记词**（唯一一份）：权威自己的 `note` 以它开头
#: （`financial_worker`：`f"代理口径（{reason_code}）"`），硬门也用它判定「这条断言有没有
#: 把口径写出来」。标记词取自权威自己的措辞，不是本层新造的术语。
PROXY_MARKER_PHRASE = "代理口径"


def is_proxy_fact(fact: Any) -> bool:
    """该财务事实是否**由权威自己**标为代理口径（读 `status`，不做任何推断）。"""
    return str(getattr(fact, "status", "") or "").strip() == PROXY_STATUS


def proxy_qualifier(fact: Any) -> str:
    """代理口径事实的**显式限定语**：逐字取权威自己的 `note`，缺失时退到标记词本身。

    非代理事实返回空串（不得给精确口径的事实加任何口径限定）。限定语是**权威命题的一部分**
    （它说明这个数值是怎么来的），因此它必须随事实表面一起出现在正文里 —— 少了它，读者会把
    一个代理口径的数值读成受审的精确值。
    """
    if not is_proxy_fact(fact):
        return ""
    return str(getattr(fact, "note", "") or "").strip() or PROXY_MARKER_PHRASE


def is_derived_two_period_fact(fact: Any) -> bool:
    """该财务事实是否是**双期派生**事实（它声明了两期输入血缘）。

    判据只看事实**自己声明的字段**（`input_periods` 的长度）：不看 `code`（公式增删会让写死的
    代码串漂移），也不看 `period` 的形状（那是排版巧合，不是身份）。这类事实说的是「两期之间
    的变化」，它的 `period` 是复合记号（`本期|上期`），**不属于任何单一期间** —— 放进「指标 ×
    期间」表格就会凭空造出一列不存在的期间，因此它永不成为表格行（见
    `build_metric_period_tables` 的同批规则）。
    """
    return len(tuple(getattr(fact, "input_periods", ()) or ())) == 2


def financial_period_text(fact: Any) -> str:
    """财务事实在**给读者看的文本**里的期间说法（唯一实现）。

    优先权威自己声明的**期间表达**（`period_label`，如 `2025年度` / `2025年末`，
    由 `financial_v2.period_basis` 从权威期间记号与口径确定性派生）；权威没有给出表达时，
    逐字退回它的期间记号（`period`）——**不发明措辞**。

    为什么不能一律用 `period`：`SOLV_INTEREST_COVER` 这类流量指标的期间要求是 `flow`，
    它的 `period` 是期间末日（`2025-12-31`）。把「本报告期的利息保障倍数」写成
    「2025-12-31的利息保障倍数」，读者会把一个期间量读成时点量 —— 那一天并没有这个数。
    """
    label = str(getattr(fact, "period_label", "") or "").strip()
    if label:
        return label
    return str(getattr(fact, "period", "") or "").strip()


def financial_table_cell(fact: Any) -> str:
    """财务事实在**表格单元格**里的可见文本（唯一实现）：数值渲染 + （代理时）口径限定语。

    `FORMULA_REVIEW` §0 要求代理值与缺失值在结果里**显式披露**：口径说明只写在正文 Claim 或
    `claim_id` 链里不算披露 —— 读者在表格里看到的仍是一个光秃秃的倍数，会把代理口径当成受审的
    精确值。因此限定语必须出现在读者真正读数字的那一格。

    两段都**逐字**取自该事实自己的权威表面（`authoritative_fact_surface` 的 `sentence` 与
    `qualifier`），中间的分隔符就是权威表面自己的标点，所以本函数的结果仍是权威表面的一段
    连续子串 —— 因此表里不可能出现权威没说过的数值或披露。

    **门的判据不是「本函数的结果是 Claim 的连续子串」（C4 修）**：那样会把「值与限定语之间
    有一个句号」变成一条额外要求，而写作提示词要求 `claim_text` 中间不得有句末标点 ——
    两条要求数学上互斥，实测把 24 条正确权威事实一并以一个标点差异作废。门改为按
    `financial_cell_components` 的**分量**逐项核对（数值 / 期间表达 / 完整限定语各自逐字出现）。
    """
    value = (str(getattr(fact, "display", "") or "").strip()
             or str(getattr(fact, "value_text", "") or "").strip())
    qualifier = proxy_qualifier(fact)
    return f"{value}。{qualifier}" if qualifier else value


@dataclass(frozen=True)
class FinancialCellComponents:
    """一格财务表格单元格的**可分量**（C4）：门与渲染**共用**的唯一分解。

    为什么要把一格拆开：一格读者可见的文本由三件事组成 —— **数值**（`display`）、**期间表达**
    （`financial_period_text`）、（代理时）**完整口径限定语**（`proxy_qualifier`）。旧门要求
    「把这三段用权威自己的标点拼起来的那一整串」逐字出现在 Claim 文本里，于是**标点**成了
    第四条隐含要求；而写作侧被明令禁止在 `claim_text` 中间写句末标点。两条要求互斥，正确事实
    被整体作废。拆开之后，每一条要求都只针对一个语义分量，标点不再承担任何判据。

    `cell` 是**渲染**结果（`financial_table_cell`）—— 它保留权威自己的标点、因而是权威表面的
    连续子串；但**门不再检查 `cell` 的连续性**，只逐项检查下面三个分量。
    """

    value: str
    period_text: str
    qualifier: str
    cell: str

    def __post_init__(self) -> None:
        if not self.value:
            raise NarrativeSchemaError("财务单元格没有数值分量（不得凭空空写一格）")

    @property
    def is_proxy(self) -> bool:
        return bool(self.qualifier)

    def required_texts(self) -> tuple[tuple[str, str], ...]:
        """Claim 文本里**必须**逐字出现的分量：(判据名, 文本)。判据名用于错误信息。

        三个分量缺一不可：少了数值 → 这一格无值可陈；少了期间表达 → 读者不知道这是哪一期的
        数（同一指标跨期成行，期间正是**行身份**的一部分）；少了限定语 → 代理口径被读成受审的
        精确值。限定语**不因「写在 citation 链里」而免除**：引用链不进表格正文，读者看不到。
        """
        parts = [("数值", self.value), ("期间表达", self.period_text)]
        if self.qualifier:
            parts.append(("代理口径的限定语", self.qualifier))
        return tuple(parts)


def financial_cell_components(fact: Any) -> FinancialCellComponents:
    """一格财务表格单元格 → 分量化（**唯一**分解实现，门与渲染共用）。"""
    return FinancialCellComponents(
        value=(str(getattr(fact, "display", "") or "").strip()
               or str(getattr(fact, "value_text", "") or "").strip()),
        period_text=financial_period_text(fact),
        qualifier=proxy_qualifier(fact),
        cell=financial_table_cell(fact),
    )


# ---------------------------------------------------------------------------
# 读者面（`pwr-4`）：单位说法与代理口径的中文说明
# ---------------------------------------------------------------------------
#
# 这两件事都**只在渲染层**发生：`NarrativeTable.unit` 是权威自己的量纲记号（`yuan` /
# `percent` / `ratio`），它照原样留在元数据里参与身份与核对；读者看到的那一行要写成中文，
# 且必须与同一张表里**单元格的显示**一致 —— 单元格按各自量级渲染（`1,251.59亿元`），
# 只写 `单位：yuan` 会让读者读到两个互相打架的单位说法。
#
#: 读者面的单位说法（封闭表）。未登记的记号**原样呈现**：不发明单位，也不猜它的量纲。
READER_UNIT_TEXTS = {
    "yuan": "元（权威归一化口径；单元格按各自数值量级显示为亿元 / 万元 / 元）",
    "percent": "百分比（单元格显示为 %）",
    "ratio": "无量纲（倍数）",
}


def reader_unit_text(unit: Any) -> str:
    """表格**读者面**的单位说法（唯一实现）。

    只翻译已知量纲记号；未知记号逐字返回原记号 —— 渲染层不得替权威补一个它没声明的单位。
    """
    token = str(unit or "").strip()
    return READER_UNIT_TEXTS.get(token, token)


def reader_unit_short_text(unit: Any) -> str:
    """**表题**里的短单位说法：`reader_unit_text` 那句话去掉括号里的量级说明后的头部。

    它**不是**第二张单位表 —— 逐字取自同一句话（`元（权威归一化口径；…）` → `元`），因此不可能
    出现「表题说一个单位、下一行说明书说另一个单位」这种自相矛盾；未登记的记号与
    `reader_unit_text` 走同一条规则（原样呈现，不发明、不猜量纲）。
    """
    return reader_unit_text(unit).split("（", 1)[0]


#: 代理口径码在读者可见单元格里的写法：权威自己的标记词 + 括号里的**原因码**
#: （`financial_worker`：`f"代理口径（{reason_code}）"`）。只抽原因码，不改写任何单元格文本。
_PROXY_CALIBER_CODE_RE = re.compile(re.escape(PROXY_MARKER_PHRASE) + r"（([A-Za-z0-9_]+)）")


def proxy_caliber_code(text: Any) -> str:
    """读者可见文本里的**代理口径原因码**（没有则空串）。"""
    match = _PROXY_CALIBER_CODE_RE.search(str(text or ""))
    return match.group(1) if match else ""


def proxy_caliber_explanation(code: str) -> str:
    """代理口径码 → **中文口径说明**（唯一实现）。

    措辞**不发明**：公式名与「谁替代谁」都逐字取自权威自己的公式登记表
    （`financial_v2.formulas.build_registry()` 的 `name` 与 `proxy_rule`），科目名取自
    唯一的科目中文名单（`sections.common.item_label_map()`）。登记表里查不到这条代理规则时，
    只陈述 `CALCULATED_PROXY` 这个状态**本身**的含义（它以代理输入算出，不是原口径的精确值），
    并且**原样保留原因码** —— 读者至少要能拿这个码回查权威，而不是只看到一句「代理口径」。
    """
    token = str(code or "").strip()
    if not token:
        return ""
    name = ""
    proxy_input = ""
    replaced = ""
    try:
        from financial_v2 import formulas as _FFORMULAS
        for formula in _FFORMULAS.build_registry().values():
            rule = dict(getattr(formula, "proxy_rule", {}) or {})
            if str(rule.get("reason_code", "")) == token:
                name = str(getattr(formula, "name", "") or "")
                proxy_input = str(rule.get("proxy_input", "") or "")
                replaced = str(rule.get("replaces", "") or "")
                break
    except Exception:  # noqa: BLE001 —— 登记表读不到时退到「只陈述状态」的措辞
        name = ""
    if name and proxy_input and replaced:
        try:
            from sections import common as _SC
            labels = _SC.item_label_map()
        except Exception:  # noqa: BLE001
            labels = {}
        return (f"{name}：「{labels.get(proxy_input, proxy_input)}」替代"
                f"「{labels.get(replaced, replaced)}」计算（代理口径，{token}）")
    return f"该数值由权威以代理输入计算，不等于原口径的精确值（代理口径，{token}）"


def proxy_caliber_notes(cell_texts: Iterable[Any]) -> tuple[str, ...]:
    """一组单元格里出现的代理口径码 → 去重、保序的中文口径说明（唯一实现）。

    顺序按**首次出现**，因此同一批单元格永远渲染出同一段文字（正文指纹可复算）。
    """
    codes: list[str] = []
    for text in cell_texts or ():
        code = proxy_caliber_code(text)
        if code and code not in codes:
            codes.append(code)
    return tuple(proxy_caliber_explanation(code) for code in codes)


# ---------------------------------------------------------------------------
# 读者面（`pwr-5`）：表格与正文句的**来源**说明
# ---------------------------------------------------------------------------
#
# 正式 Markdown 读回要能让读者看到**中文期间、单位、来源**与代理口径说明。期间、单位与代理
# 口径已在 `pwr-4` 落地（表头下的 `主体 / 期间 / 单位` 行与表下的 `口径说明` 行）；缺的是
# **来源**。承载它的两样东西都是**既有的**：一是 `SectionClaim.citation_refs` 里的
# `CitationRef`（它自己就带 `formula_id` / `formula_version` / `period`，且本来就是这个 Claim
# 被授权引用的那一条），二是权威自己的公式登记表（`financial_v2.formulas.build_registry()`）
# 与期间口径（`financial_v2.period_basis`）。因此**不需要**扩 `NarrativeSentence` /
# `NarrativeTable` 的 wire：来源说明是**渲染层**从既有引用对象确定性拼出来的，权威没给的名字
# 一个都不造。
#
# 为什么必须把 Claim 交给渲染器：`NarrativeParagraph.citation_ids` 里存的是
# `sections.schema.derive_citation_id` 的**单向散列**（`cite_…`），身份串**不可反解**——所以
# 渲染器只能拿到与正文构造同一个 `derive_citation_id` 派生出的键值对，索引因而必须由 Claim 集
# 现场重建（见 `citation_source_index`），既不能从 citation_id 里「读」出来，也不能由调用方
# 自带一段文字。索引对**正文引用全集**的覆盖由渲染器逐条检查，缺一条即 fail-closed。
#
# 印不出中文来源的那些引用（`evidence` / `external` 没有中文来源名，登记表里查不到的
# `formula_id` 同样没有，值为 `None`）**一律不印**：宁可这一处少一行来源，也不把
# `evidence:84605fcd` 这类内部记号印给读者 —— 那正是 `pwr-4` 已认定的一类缺陷（读者面不得再
# 印内部记号）。**「不印」与「漏给」必须分得开**：所以未登记的引用仍然进索引（值为 `None`），
# 覆盖检查才有意义。


def _reader_period_text(period: Any, formula: Any) -> str:
    """期间记号 → 读者面**中文期间表达**；口径判不出时逐字退回期间记号。

    与 `financial_period_text` 同一条规则：优先权威自己的期间表达，取不到就原样呈现期间记号，
    **不发明**措辞（也就不用猜「这一天是不是一个期间量」）。
    """
    token = str(period or "").strip()
    if not token:
        return ""
    try:
        basis = _FPB.basis_from_period_requirement(
            str(getattr(formula, "period_requirement", "") or ""))
        return _FPB.period_expression(token, basis)
    except Exception:  # noqa: BLE001 —— 口径判不出时退回期间记号，不猜一个口径
        return token


def citation_ref_source(ref: Any) -> "tuple[str, str] | None":
    """一条 `CitationRef` → `(中文口径, 中文期间)`；印不出中文来源时返回 `None`。

    `中文口径` = `公式中文名@公式版本`（登记表逐字给出，本函数不造名）；`中文期间` 见
    `_reader_period_text`，可为空串（引用自身没带期间）。
    """
    if str(getattr(ref, "ref_type", "") or "") != "structured":
        return None
    formula_id = str(getattr(ref, "formula_id", "") or "")
    if not formula_id:
        return None
    try:
        from financial_v2 import formulas as _FFORMULAS
        formula = _FFORMULAS.build_registry().get(formula_id)
    except Exception:  # noqa: BLE001 —— 登记表读不到时不印来源（不得自造一个公式名）
        return None
    if formula is None:
        return None
    name = str(getattr(formula, "name", "") or "")
    if not name:
        return None
    version = str(getattr(ref, "formula_version", "") or "")
    caliber = f"{name}@{version}" if version else name
    return caliber, _reader_period_text(getattr(ref, "period", None), formula)


def citation_source_index(claims: Any) -> "dict[str, tuple[str, str] | None]":
    """本节定稿 Claim 集 → `citation_id -> 来源` 索引（唯一实现）。

    键用与正文构造**同一个** `derive_citation_id` 派生，因此键就是正文里那批 `cite_…`；
    未登记（印不出中文来源）的引用同样进索引、值为 `None`（理由见本节开头）。
    """
    import sections.schema as _SS  # 延迟导入：取 citation ID 的**唯一**派生实现

    out: "dict[str, tuple[str, str] | None]" = {}
    for claim in claims or ():
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id:
            continue
        for ref in (getattr(claim, "citation_refs", ()) or ()):
            out.setdefault(_SS.derive_citation_id(claim_id, ref), citation_ref_source(ref))
    return out


def citation_source_notes(citation_ids: Iterable[Any],
                          sources: "Mapping[str, Any] | None") -> tuple[str, ...]:
    """一组 `citation_id` + 来源索引 → 去重、保序的**中文来源说明**。

    按 `(中文口径)` 归并，期间按**首次出现**顺序并列 —— 同一批引用永远渲染出同一段文字，
    因此正文指纹可复算；顺序即引用顺序，不排序（排序会让来源行与它上面那张表的列序脱钩）。
    """
    groups: list[tuple[str, list[str]]] = []
    for raw in citation_ids or ():
        parsed = (sources or {}).get(str(raw))
        if not parsed:
            continue
        caliber, period = parsed
        group = next((g for g in groups if g[0] == caliber), None)
        if group is None:
            group = (caliber, [])
            groups.append(group)
        if period and period not in group[1]:
            group[1].append(period)
    return tuple(f"{caliber}（期间 {'、'.join(periods)}）" if periods else caliber
                 for caliber, periods in groups)


def _citation_source_line(citation_ids: Iterable[Any],
                          sources: "Mapping[str, Any] | None") -> str:
    """渲染器专用：一组引用 → **恰好一行** `- 来源：…`；印不出来时返回空串。

    合并成一行（而不是一项一行）是为了让「这一张表 / 这一段正文的来源」在读者眼里是**一条**
    信息；组内用 `；` 并列，组间顺序即引用顺序（见 `citation_source_notes`）。
    """
    notes = citation_source_notes(citation_ids, sources)
    return f"- 来源：{'；'.join(notes)}" if notes else ""


def authoritative_fact_surface(kind: str, fact: Any) -> str:
    """权威 fact 自己的**权威表面文本**（§六：Claim 必须确实属于该权威 fact）。

    - ``topic_pack``：正文表面就是 `fact.text` 本身（写入侧逐字照抄，不得改写）；
    - ``financial_pack`` / ``evidence_note``：由权威字段确定性拼装（期间/科目/权威数值
      渲染），与写入侧使用同一套字段、同一顺序。财务事实若由权威自己标为
      `CALCULATED_PROXY`，其口径限定语（`proxy_qualifier`）**附在断言句之后**，使「这个数值是
      代理口径算出来的」成为该权威表面自己的一部分：写入侧看到的行与硬门复算的表面是同一份
      文本，模型不得把它省略，门也不必另行推断口径。

    返回空串表示该 fact 没有可写的权威表面（调用方据此 fail-closed）。
    """
    if kind == "topic_pack":
        return str(getattr(fact, "text", "") or "")
    value = (str(getattr(fact, "display", "") or "").strip()
             or str(getattr(fact, "value_text", "") or "").strip())
    if kind == "financial_pack":
        if not value:
            return ""
        # 科目名优先 `label`，缺失时退到权威自己的 `code`（不发明新名称）。
        label = str(getattr(fact, "label", "") or getattr(fact, "code", "") or "").strip()
        sentence = amount_sentence(label, financial_period_text(fact), value)
        qualifier = proxy_qualifier(fact)
        # 限定语自成一句（`amount_sentence` 已自带句号），因此不追加任何连接词或修饰。
        return f"{sentence}{qualifier}。" if qualifier else sentence
    if kind == "evidence_note":
        # 附注事实没有 `period` 字段（期间由 span locator 指向的原文承担），不编造期间；
        # 没有任何数值的附注事实只陈述其科目名，绝不补一个数字上去。
        label = str(getattr(fact, "label", "") or "").strip()
        if not value:
            return label
        return amount_sentence(label, "", value)
    return ""


def citation_from_mapping(payload: Any) -> Any:
    """财务 fact 的 `citation` 映射 → `CitationRef`（**唯一**实现，写入侧只做错误类型转换）。"""
    from harness.schema import CitationRef

    if not isinstance(payload, Mapping):
        raise NarrativeSchemaError("财务 fact 的 citation 必须是 mapping（否则无法建立 locator）")
    allowed = set(CitationRef.__dataclass_fields__)
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise NarrativeSchemaError(f"财务 citation 含未登记字段 {unknown}（不得臆造 locator）")
    if not payload.get("ref_type"):
        raise NarrativeSchemaError("财务 citation 缺 ref_type（不得臆造 locator）")
    return CitationRef(**{k: payload[k] for k in payload})


def note_citation(fact: Any) -> Any:
    """附注事实的引用锚点：evidence 类型 + 该附注所在 evidence 的 id（**唯一**实现）。"""
    from harness.schema import CitationRef

    evidence_id = str(getattr(fact, "evidence_id", "") or "")
    if not evidence_id:
        raise NarrativeSchemaError(f"附注 fact {getattr(fact, 'fact_id', '?')!r} 缺 evidence_id")
    return CitationRef(ref_type="evidence", evidence_id=evidence_id)


def authoritative_citation_refs(kind: str, fact: Any) -> tuple[Any, ...] | None:
    """§六：该权威 fact **自己**给出的引用锚点（Claim 的引用必须与它逐条相同）。

    返回 `None` 表示该 kind 无法从 fact 派生引用 —— 调用方此刻不得声称核验过引用归属
    （宁可显式承认「无法核验」，也不要拿 Claim 自己声明的引用来自证）。
    """
    if kind == "topic_pack":
        refs = tuple(getattr(fact, "citation_refs", ()) or ())
        return refs or None
    if kind == "financial_pack":
        # 一条财务事实的数值可以是**多条**权威记录的确定性函数（Δpp 派生事实 = 两期输入的
        # 指标结果，`FORMULA_REVIEW` §5.1）。此时它必须把**完整**的两期引用一起交出来：
        # 引用集少了任何一期，「这个差额是从哪两期算出来的」就无法回查，而单引用会被读者
        # 当成「这条事实就是那一期的数」。`citations` 为空 = 就是 `citation` 那一条（不是没有）。
        refs = tuple(citation_from_mapping(c)
                     for c in (getattr(fact, "citations", ()) or ()))
        if refs:
            return refs
        return (citation_from_mapping(getattr(fact, "citation", None)),)
    if kind == "evidence_note":
        return (note_citation(fact),)
    if kind == "external_snapshot":
        # 外部事实的引用锚点是它的**来源载体**（snapshot 身份）；URL/body hash/SourcePolicy/
        # 日期由该事实自己的资格决定承担，引用本身不重复它们。
        snapshot_id = str(getattr(fact, "source_snapshot_id", "") or "")
        if not snapshot_id:
            return None
        from harness.schema import CitationRef

        return (CitationRef(ref_type="external", source_snapshot_id=snapshot_id),)
    return None


def authority_fact_entries(authority_input: Any) -> dict[tuple[str, str, str], Any]:
    """权威输入 → `{(authority_kind, container_id, fact_id): 该权威 fact 对象}`。

    这是**门后**定稿侧把 accepted binding 的权威坐标解回「事实对象」的唯一遍历实现：定稿
    `SectionClaim` 的引用必须逐条等于权威 fact 自己给出的引用（`authoritative_citation_refs`），
    所以坐标必须能解回**权威持有的事实对象**，而不是解回一个只带若干字符串的摘要。

    四类 authority 各按自己的容器/身份字段取值，与 `_authority_fact_periods` /
    `_authority_fact_index` 同一坐标系：`external_snapshot` 的 fact 身份是
    `external_fact_id`、容器是 snapshot 容器身份（容器 ≠ 事实身份）。
    """
    out: dict[tuple[str, str, str], Any] = {}
    kind = getattr(authority_input, "producer_kind", None)

    def _put(container: str, fact_id: str, authority_kind: str, fact: Any) -> None:
        key = (authority_kind, container, fact_id)
        if key in out and out[key] is not fact:
            raise NarrativeSchemaError(
                f"权威事实坐标重复：{key}（同一 (kind, container, fact) 只能有一条）")
        out[key] = fact

    if kind == "topic_harness":
        for pack in authority_input.pack_set.packs:
            container = str(pack.pack_id)
            for fact in pack.facts:
                _put(container, str(fact.fact_id), "topic_pack", fact)
            for external in tuple(getattr(pack, "external_facts", ()) or ()):
                _put(external_authority_container_id(external),
                     str(external.external_fact_id), "external_snapshot", external)
    elif kind == "financial_workflow":
        artifact = authority_input.artifact
        for fact in artifact.facts:
            _put(str(artifact.artifact_id), str(fact.fact_id), "financial_pack", fact)
        note_set = getattr(authority_input, "note_facts", None)
        if note_set is not None:
            container = note_container_id(note_set)
            for fact in note_set.facts:
                _put(container, str(fact.fact_id), "evidence_note", fact)
    return out


def authoritative_locator(kind: str, fact: Any) -> dict | None:
    """§六：该权威 fact 的**权威 locator**（`loc-1` `char_range`：evidence id + 半开字符区间）。

    附注事实的定位本来就是**半开字符区间**，因此这里产出 `char_range` 而不是块区间；无区间
    时返回 `None`（不猜、不用别的字段顶替）。
    """
    if kind != "evidence_note":
        return None
    evidence_id = str(getattr(fact, "evidence_id", "") or "")
    char_range = getattr(fact, "evidence_char_range", None)
    if not evidence_id or not char_range:
        return None
    try:
        start, end = int(char_range[0]), int(char_range[1])
    except (TypeError, ValueError, IndexError):
        return None
    if start < 0 or start >= end:
        return None
    return char_range_locator(evidence_id, start, end)



def derive_sentence_id(sentence: Any) -> str:
    body = (sentence.identity_body() if isinstance(sentence, NarrativeSentence) else sentence.body)
    return content_id("nsent_", body)


@dataclass(frozen=True)
class NarrativeParagraph:
    """由多句组成、可跨多个 Claim 的段落（§6.4：Writer 可以合并与组织，但不得新增事实）。"""

    paragraph_id: str
    section_id: str
    topic_ids: tuple[str, ...]
    index: int
    sentences: tuple[NarrativeSentence, ...]
    claim_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.section_id, "NarrativeParagraph.section_id")
        topics = _str_tuple(self.topic_ids, "NarrativeParagraph.topic_ids")
        if not topics:
            raise NarrativeSchemaError("NarrativeParagraph.topic_ids 不得为空（段落必须归属 topic）")
        object.__setattr__(self, "topic_ids", topics)
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise NarrativeSchemaError("NarrativeParagraph.index 必须为 >=0 的整数")
        if not self.sentences:
            raise NarrativeSchemaError("NarrativeParagraph.sentences 不得为空")
        for pos, sentence in enumerate(self.sentences):
            if not isinstance(sentence, NarrativeSentence):
                raise NarrativeSchemaError("NarrativeParagraph.sentences 必须是 NarrativeSentence")
            if sentence.paragraph_id != self.paragraph_id:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 的 paragraph_id 与本段落不符（跨段落拼装已拒）")
            if sentence.index != pos:
                raise NarrativeSchemaError(
                    f"段落 {self.paragraph_id} 的句子下标不连续：位置 {pos} 上是 index="
                    f"{sentence.index}（句序不得空洞或重号）")
        declared_claims = _str_tuple(self.claim_ids, "NarrativeParagraph.claim_ids")
        declared_citations = _str_tuple(self.citation_ids, "NarrativeParagraph.citation_ids")
        actual_claims = tuple(dict.fromkeys(
            cid for s in self.sentences for cid in s.claim_ids))
        actual_citations = tuple(dict.fromkeys(
            cid for s in self.sentences for cid in s.citation_ids))
        if declared_claims != actual_claims:
            raise NarrativeSchemaError(
                f"段落 claim_ids 与句子并集不符：声明 {list(declared_claims)}，"
                f"实际 {list(actual_claims)}")
        if declared_citations != actual_citations:
            raise NarrativeSchemaError(
                f"段落 citation_ids 与句子并集不符：声明 {list(declared_citations)}，"
                f"实际 {list(actual_citations)}")
        object.__setattr__(self, "claim_ids", declared_claims)
        object.__setattr__(self, "citation_ids", declared_citations)

        expected = derive_paragraph_id(self)
        if self.paragraph_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeParagraph.paragraph_id 与内容不符：声明 {self.paragraph_id!r}，"
                f"应为 {expected!r}")

    @staticmethod
    def _sentence_content_body(sentence: NarrativeSentence) -> dict:
        """句子的**内容**身份（去掉依赖 `paragraph_id` 的 `sentence_id`）。

        段落 id 只能由句子内容决定：句子的 `sentence_id` 反过来包含 `paragraph_id`，
        若用 `sentence_id` 参与段落寻址就会形成循环，任何构造都无法自洽。
        """
        body = sentence.identity_body()
        body.pop("paragraph_id", None)
        return body

    def identity_body(self) -> dict:
        return {
            "section_id": self.section_id, "topic_ids": list(self.topic_ids),
            "index": self.index,
            "sentences": [self._sentence_content_body(s) for s in self.sentences],
            "claim_ids": list(self.claim_ids),
            "citation_ids": list(self.citation_ids),
        }

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.sentences)

    @property
    def context_binding_ids(self) -> tuple[str, ...]:
        """本段落各句**实际使用**的 context accepted binding 并集（有序、去重）。

        它是**派生**属性而不是字段：句级绑定已经在句子内容身份里，段落再存一份可被自报的口径
        只会制造两个真值。`SectionNarrative` 用它核对「段落声明 ⊂ 句子声明」。
        """
        return tuple(dict.fromkeys(
            cid for s in self.sentences for cid in s.context_binding_ids))

    def to_dict(self) -> dict:
        return {"paragraph_id": self.paragraph_id, "section_id": self.section_id,
                "topic_ids": list(self.topic_ids), "index": self.index,
                "claim_ids": list(self.claim_ids), "citation_ids": list(self.citation_ids),
                "sentences": [s.to_dict() for s in self.sentences]}

    @classmethod
    def create(cls, *, section_id: str, topic_ids: Sequence[str], index: int,
               sentence_specs: Sequence[Mapping[str, Any]]) -> "NarrativeParagraph":
        """按 `sentence_specs`（text/sentence_kind/claim_ids/citation_ids/connector/
        context_binding_ids）建段落并派生 id。"""
        for spec in sentence_specs:
            unknown = sorted(set(spec) - {"text", "sentence_kind", "claim_ids", "citation_ids",
                                          "connector", "context_binding_ids"})
            if unknown:
                raise NarrativeSchemaError(f"句规格含未登记字段 {unknown}")
        placeholder = content_id("npar_", {"pending": True})
        sentences = tuple(
            NarrativeSentence.create(paragraph_id=placeholder, index=pos, **spec)
            for pos, spec in enumerate(sentence_specs))
        body = {
            "section_id": section_id, "topic_ids": list(topic_ids), "index": index,
            "sentences": [cls._sentence_content_body(s) for s in sentences],
            "claim_ids": list(dict.fromkeys(c for s in sentences for c in s.claim_ids)),
            "citation_ids": list(dict.fromkeys(c for s in sentences for c in s.citation_ids)),
        }
        paragraph_id = content_id("npar_", body)
        sentences = tuple(
            NarrativeSentence.create(paragraph_id=paragraph_id,
                                     index=s.index, text=s.text,
                                     sentence_kind=s.sentence_kind,
                                     claim_ids=s.claim_ids, citation_ids=s.citation_ids,
                                     connector=s.connector,
                                     context_binding_ids=s.context_binding_ids)
            for s in sentences)
        return cls(paragraph_id=paragraph_id, section_id=section_id,
                   topic_ids=tuple(topic_ids), index=index, sentences=sentences,
                   claim_ids=tuple(body["claim_ids"]), citation_ids=tuple(body["citation_ids"]))

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeParagraph":
        d = _reject_unknown(d, {"paragraph_id", "section_id", "topic_ids", "index", "sentences",
                                "claim_ids", "citation_ids"}, "NarrativeParagraph")
        return cls(
            paragraph_id=_require_nonempty(d.get("paragraph_id"), "NarrativeParagraph.paragraph_id"),
            section_id=_require_nonempty(d.get("section_id"), "NarrativeParagraph.section_id"),
            topic_ids=_str_tuple(d.get("topic_ids") or (), "NarrativeParagraph.topic_ids"),
            index=_req(d, "index", "NarrativeParagraph"),
            sentences=tuple(NarrativeSentence.from_dict(x)
                            for x in _req(d, "sentences", "NarrativeParagraph")),
            claim_ids=_str_tuple(d.get("claim_ids") or (), "NarrativeParagraph.claim_ids"),
            citation_ids=_str_tuple(d.get("citation_ids") or (), "NarrativeParagraph.citation_ids"))


def derive_paragraph_id(paragraph: Any) -> str:
    body = (paragraph.identity_body() if isinstance(paragraph, NarrativeParagraph)
            else paragraph.body)
    return content_id("npar_", body)


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeTableRow:
    """表格行：要么绑定 Claim/Citation，要么显式声明为**非事实**展示行（二者恰一）。

    §七 2：`cells` 与 `claim_ids` 的对应是**按非空单元格顺序**的逐位对应 —— 第 i 个非空单元格
    属于第 i 条 Claim。**空串单元格不是占位符**：它显式表达「本行指标在这一列期间**没有**已接受
    的权威事实」，因此它**不**携带 Claim，也**不得**被任何内容填补（不推算、不按公式回填、
    不借相邻期间的值）。把空串当成 `""` 这样一条「空 Claim」来凑位，等于让「缺值」在 wire 层
    消失；把非空单元格数与 Claim 数脱钩，等于让表格里的数字失去归属。
    """

    row_id: str
    table_id: str
    index: int
    label: str
    cells: tuple[str, ...]
    claim_ids: tuple[str, ...] = ()
    citation_ids: tuple[str, ...] = ()
    non_factual_reason: str | None = None
    unit: str = ""
    period: str = ""

    def __post_init__(self) -> None:
        _require_nonempty(self.table_id, "NarrativeTableRow.table_id")
        _require_nonempty(self.label, "NarrativeTableRow.label")
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise NarrativeSchemaError("NarrativeTableRow.index 必须为 >=0 的整数")
        cells = _cells_tuple(self.cells, "NarrativeTableRow.cells")
        object.__setattr__(self, "cells", cells)
        claims = _str_tuple(self.claim_ids, "NarrativeTableRow.claim_ids")
        citations = _str_tuple(self.citation_ids, "NarrativeTableRow.citation_ids")

        has_claims = bool(claims)
        has_non_factual = self.non_factual_reason is not None
        if has_claims == has_non_factual:
            raise NarrativeSchemaError(
                f"表格行 {self.label!r} 必须**恰有其一**：绑定 Claim/Citation，或显式声明非事实"
                f"展示行；当前 claim_ids={list(claims)}，non_factual_reason="
                f"{self.non_factual_reason!r}")
        if has_claims and not citations:
            raise NarrativeSchemaError(
                f"绑定 Claim 的表格行 {self.label!r} 必须同时给出 citation_id")
        if has_claims:
            # §七 2：非空单元格与 Claim 必须**逐位同数**（第 i 个非空单元格属于第 i 条 Claim）。
            # 空串是「本列期间没有已接受的权威事实」的显式表达，不是可以凑位的占位符。
            filled = tuple(cell for cell in cells if str(cell).strip())
            if len(filled) != len(claims):
                raise NarrativeSchemaError(
                    f"表格行 {self.label!r} 的非空单元格数 {len(filled)} 与 Claim 数 "
                    f"{len(claims)} 不一致：单元格与 Claim 按非空顺序逐位对应，"
                    "空单元格表达「本列没有已接受的权威事实」且不携带 Claim")
        if has_non_factual:
            _require_enum(self.non_factual_reason, NON_FACTUAL_ROW_REASONS,
                          "NarrativeTableRow.non_factual_reason")

        body_text = " ".join((self.label,) + cells)
        numeric = scan_numeric_tokens(body_text)
        if numeric and not has_non_factual:
            if not self.unit:
                raise NarrativeSchemaError(
                    f"含数字的表格行 {self.label!r} 必须声明 unit（单位不得由 Writer 推定）")
            if not self.period:
                raise NarrativeSchemaError(
                    f"含数字的表格行 {self.label!r} 必须声明 period（期间不得由 Writer 推定）")
        object.__setattr__(self, "claim_ids", claims)
        object.__setattr__(self, "citation_ids", citations)

        expected = derive_table_row_id(self)
        if self.row_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeTableRow.row_id 与内容不符：声明 {self.row_id!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {"table_id": self.table_id, "index": self.index, "label": self.label,
                "cells": list(self.cells), "claim_ids": list(self.claim_ids),
                "citation_ids": list(self.citation_ids),
                "non_factual_reason": self.non_factual_reason,
                "unit": self.unit, "period": self.period}

    def to_dict(self) -> dict:
        return {"row_id": self.row_id, **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "NarrativeTableRow":
        body = {
            "table_id": kwargs.get("table_id", ""), "index": kwargs.get("index", 0),
            "label": kwargs.get("label", ""), "cells": tuple(kwargs.get("cells") or ()),
            "claim_ids": tuple(kwargs.get("claim_ids") or ()),
            "citation_ids": tuple(kwargs.get("citation_ids") or ()),
            "non_factual_reason": kwargs.get("non_factual_reason"),
            "unit": kwargs.get("unit", ""), "period": kwargs.get("period", ""),
        }
        body["row_id"] = content_id("ntrow_", body)
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeTableRow":
        d = _reject_unknown(d, {"row_id", "table_id", "index", "label", "cells", "claim_ids",
                                "citation_ids", "non_factual_reason", "unit", "period"},
                            "NarrativeTableRow")
        return cls(
            row_id=_require_nonempty(d.get("row_id"), "NarrativeTableRow.row_id"),
            table_id=_require_nonempty(d.get("table_id"), "NarrativeTableRow.table_id"),
            index=_req(d, "index", "NarrativeTableRow"),
            label=_require_nonempty(d.get("label"), "NarrativeTableRow.label"),
            cells=_cells_tuple(d.get("cells") or (), "NarrativeTableRow.cells"),
            claim_ids=_str_tuple(d.get("claim_ids") or (), "NarrativeTableRow.claim_ids"),
            citation_ids=_str_tuple(d.get("citation_ids") or (), "NarrativeTableRow.citation_ids"),
            non_factual_reason=d.get("non_factual_reason"),
            unit=d.get("unit") or "", period=d.get("period") or "")


def derive_table_row_id(row: Any) -> str:
    body = (row.identity_body() if isinstance(row, NarrativeTableRow) else row.body)
    return content_id("ntrow_", body)


@dataclass(frozen=True)
class NarrativeTable:
    """叙述型表格：表级 unit/period/entity_scope 必须显式，Writer 不得求和/换算。"""

    table_id: str
    section_id: str
    topic_ids: tuple[str, ...]
    index: int
    caption: str
    header: tuple[str, ...]
    rows: tuple[NarrativeTableRow, ...]
    claim_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    entity_scope: str
    unit: str
    period: str

    def __post_init__(self) -> None:
        _require_nonempty(self.section_id, "NarrativeTable.section_id")
        _require_nonempty(self.caption, "NarrativeTable.caption")
        topics = _str_tuple(self.topic_ids, "NarrativeTable.topic_ids")
        if not topics:
            raise NarrativeSchemaError("NarrativeTable.topic_ids 不得为空")
        object.__setattr__(self, "topic_ids", topics)
        header = _str_tuple(self.header, "NarrativeTable.header")
        if not header:
            raise NarrativeSchemaError("NarrativeTable.header 不得为空")
        object.__setattr__(self, "header", header)
        if not self.rows:
            raise NarrativeSchemaError("NarrativeTable.rows 不得为空")
        for pos, row in enumerate(self.rows):
            if not isinstance(row, NarrativeTableRow):
                raise NarrativeSchemaError("NarrativeTable.rows 必须是 NarrativeTableRow")
            if row.table_id != self.table_id:
                raise NarrativeSchemaError(
                    f"行 {row.row_id} 的 table_id 与本表不符（跨表拼装已拒）")
            if row.index != pos:
                raise NarrativeSchemaError(
                    f"表 {self.table_id} 行下标不连续：位置 {pos} 上是 index={row.index}")
            if len(row.cells) != len(header) - 1 and row.cells:
                # 允许 label + cells 的形态：cells 覆盖表头除首列外的列
                raise NarrativeSchemaError(
                    f"行 {row.row_id} 的 cells 数 {len(row.cells)} 与表头列数 "
                    f"{len(header)} 不匹配（不得缺口填充/错位）")
        claims = _str_tuple(self.claim_ids, "NarrativeTable.claim_ids")
        citations = _str_tuple(self.citation_ids, "NarrativeTable.citation_ids")
        actual_claims = tuple(dict.fromkeys(c for r in self.rows for c in r.claim_ids))
        actual_citations = tuple(dict.fromkeys(c for r in self.rows for c in r.citation_ids))
        if claims != actual_claims:
            raise NarrativeSchemaError(
                f"表 claim_ids 与行并集不符：声明 {list(claims)}，实际 {list(actual_claims)}")
        if citations != actual_citations:
            raise NarrativeSchemaError(
                f"表 citation_ids 与行并集不符：声明 {list(citations)}，"
                f"实际 {list(actual_citations)}")
        object.__setattr__(self, "claim_ids", claims)
        object.__setattr__(self, "citation_ids", citations)
        if not self.entity_scope:
            raise NarrativeSchemaError("NarrativeTable.entity_scope 不得为空（主体范围必须显式）")

        expected = derive_table_id(self)
        if self.table_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeTable.table_id 与内容不符：声明 {self.table_id!r}，应为 {expected!r}")

    @staticmethod
    def _row_content_body(row: NarrativeTableRow) -> dict:
        """行的**内容**身份（去掉依赖 `table_id` 的 `row_id`），理由同段落。"""
        body = row.identity_body()
        body.pop("table_id", None)
        return body

    def identity_body(self) -> dict:
        return {"section_id": self.section_id, "topic_ids": list(self.topic_ids),
                "index": self.index, "caption": self.caption, "header": list(self.header),
                "rows": [self._row_content_body(r) for r in self.rows],
                "claim_ids": list(self.claim_ids),
                "citation_ids": list(self.citation_ids), "entity_scope": self.entity_scope,
                "unit": self.unit, "period": self.period}

    def to_dict(self) -> dict:
        return {"table_id": self.table_id, "section_id": self.section_id,
                "topic_ids": list(self.topic_ids), "index": self.index,
                "caption": self.caption, "header": list(self.header),
                "claim_ids": list(self.claim_ids), "citation_ids": list(self.citation_ids),
                "entity_scope": self.entity_scope, "unit": self.unit, "period": self.period,
                "rows": [r.to_dict() for r in self.rows]}

    @classmethod
    def create(cls, *, section_id: str, topic_ids: Sequence[str], index: int, caption: str,
               header: Sequence[str], row_specs: Sequence[Mapping[str, Any]], entity_scope: str,
               unit: str, period: str) -> "NarrativeTable":
        placeholder = content_id("ntab_", {"pending": True})
        rows = tuple(NarrativeTableRow.create(table_id=placeholder, index=pos, **spec)
                     for pos, spec in enumerate(row_specs))
        body = {
            "section_id": section_id, "topic_ids": list(topic_ids), "index": index,
            "caption": caption, "header": list(header),
            "rows": [cls._row_content_body(r) for r in rows],
            "claim_ids": list(dict.fromkeys(c for r in rows for c in r.claim_ids)),
            "citation_ids": list(dict.fromkeys(c for r in rows for c in r.citation_ids)),
            "entity_scope": entity_scope, "unit": unit, "period": period,
        }
        table_id = content_id("ntab_", body)
        rows = tuple(NarrativeTableRow.create(
            table_id=table_id, index=r.index, label=r.label, cells=r.cells,
            claim_ids=r.claim_ids, citation_ids=r.citation_ids,
            non_factual_reason=r.non_factual_reason, unit=r.unit, period=r.period)
            for r in rows)
        return cls(table_id=table_id, section_id=section_id, topic_ids=tuple(topic_ids),
                   index=index, caption=caption, header=tuple(header), rows=rows,
                   claim_ids=tuple(body["claim_ids"]), citation_ids=tuple(body["citation_ids"]),
                   entity_scope=entity_scope, unit=unit, period=period)

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeTable":
        d = _reject_unknown(d, {"table_id", "section_id", "topic_ids", "index", "caption",
                                "header", "rows", "claim_ids", "citation_ids", "entity_scope",
                                "unit", "period"}, "NarrativeTable")
        return cls(
            table_id=_require_nonempty(d.get("table_id"), "NarrativeTable.table_id"),
            section_id=_require_nonempty(d.get("section_id"), "NarrativeTable.section_id"),
            topic_ids=_str_tuple(d.get("topic_ids") or (), "NarrativeTable.topic_ids"),
            index=_req(d, "index", "NarrativeTable"),
            caption=_require_nonempty(d.get("caption"), "NarrativeTable.caption"),
            header=_str_tuple(d.get("header") or (), "NarrativeTable.header"),
            rows=tuple(NarrativeTableRow.from_dict(x) for x in _req(d, "rows", "NarrativeTable")),
            claim_ids=_str_tuple(d.get("claim_ids") or (), "NarrativeTable.claim_ids"),
            citation_ids=_str_tuple(d.get("citation_ids") or (), "NarrativeTable.citation_ids"),
            entity_scope=d.get("entity_scope") or "", unit=d.get("unit") or "",
            period=d.get("period") or "")


def derive_table_id(table: Any) -> str:
    body = table.identity_body() if isinstance(table, NarrativeTable) else table.body
    return content_id("ntab_", body)


# ---------------------------------------------------------------------------
# final Narrative（门**后**；3D 定稿侧）
# ---------------------------------------------------------------------------

def derive_section_narrative_id(narrative: Any) -> str:
    body = narrative.identity_body() if isinstance(narrative, SectionNarrative) else narrative.body
    return content_id("nar_", body)


@dataclass(frozen=True)
class SectionNarrative:
    """门**后** final Narrative：把已定稿的 Claim / citation 组织成段落与表格（3D 定稿侧）。

    方向（§0.13 / P1-1）：本对象**后于** `SectionClaim` 与 accepted binding 形成，因此它引用
    定稿 Claim ID 与 context accepted-binding ID；反向边在类型层不可表达 —— `SectionDraft`
    没有任何字段能指回本对象，`AcceptedSupportBinding` 也没有 `narrative_id` 字段。

    `claim_ids` 承载**事实性**内容的支撑（每条必须能被组装器解析到本节 `SectionClaim`）；
    `context_binding_ids` 只承载背景/结构/衔接用的 context 支撑，**不授权事实**。两者都按
    **集合**语义（有序、去重），否则「同一处正文两种写法」会变成两个身份。

    本类**没有**任何回指 Draft 之外的字段：`section_draft_id` 是**单向**引用，Draft 侧不反向
    持有 narrative ID，所以 Draft↔（Claim/Result/Narrative）成环在类型层不可构造。
    """

    narrative_id: str
    schema_version: str
    task_id: str
    section_id: str
    #: 单向引用门前 draft：final Narrative 只在某一 draft revision 之后成立。
    section_draft_id: str
    draft_revision: str
    paragraphs: tuple[NarrativeParagraph, ...]
    tables: tuple[NarrativeTable, ...]
    context_binding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"SectionNarrative.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}，得到 "
                f"{self.schema_version!r}")
        for name in ("task_id", "section_id", "section_draft_id", "draft_revision"):
            _require_nonempty(getattr(self, name), f"SectionNarrative.{name}")
        paragraphs = tuple(self.paragraphs or ())
        tables = tuple(self.tables or ())
        # 「零段落零表格」可以是**合法**的：本节没有任何可定稿的事实（真实 Pack 里既无可用
        # 事实也无材料），正文就只有「标题 + 缺口附录」——这正是「如实交代本节没有正文」，
        # 而不是「凭空产生正文」。缺了它，「无内容但有缺口」的章节在定稿处无法表达，缺口只能
        # 靠 fail-closed 传达，而 fail-closed 不是人读的缺口显示（§五 3：有合法材料就写，
        # 否则 typed 缺口）。
        #
        # 这条判据因此**不在 wire 层**：wire 看不到「本节是否登记了缺口」（那是 draft 的
        # 字段）。唯一所有者是定稿构造器 `build_section_narrative`：它只在 draft 明确登记了
        # `unresolved_ids` 且权威投影在场时才允许空正文，其余情形照旧 fail-closed。汇编侧
        # 还有第二道：`report_assembler._verify_scope` 要求每个 topic 要么有正文要么有显式
        # 缺口，因此「空正文且无缺口」在组装处仍然不可能通过。
        # 这条移动**不放宽**任何事实判据：空正文里一个字符的模型文本都没有，事实表面守恒、
        # 引用限定、跨主题守恒三条判据在零句子上恒真，不存在被「空集合自证」放过的伪造。
        for name, values, cls in (("paragraphs", paragraphs, NarrativeParagraph),
                                  ("tables", tables, NarrativeTable)):
            for item in values:
                if not isinstance(item, cls):
                    raise NarrativeSchemaError(
                        f"SectionNarrative.{name} 只接受 {cls.__name__}，"
                        f"得到 {type(item).__name__}")
                if item.section_id != self.section_id:
                    raise NarrativeSchemaError(
                        f"SectionNarrative.{name} 中的 {item.section_id!r} 不属于本节 "
                        f"{self.section_id!r}（跨节拼装已拒）")
            # 下标必须**整体连续且按位置递增**：顺序本身是正文的一部分，乱序/空洞不得被静默接受。
            if tuple(v.index for v in values) != tuple(range(len(values))):
                raise NarrativeSchemaError(
                    f"SectionNarrative.{name} 的 index 必须按位置严格取自 0..n-1，"
                    f"实际 {[v.index for v in values]}（顺序即正文身份，不得空洞或重号）")
        object.__setattr__(self, "paragraphs", paragraphs)
        object.__setattr__(self, "tables", tables)
        context = _str_tuple(self.context_binding_ids, "SectionNarrative.context_binding_ids")
        if len(set(context)) != len(context):
            raise NarrativeSchemaError(
                "SectionNarrative.context_binding_ids 含重复（context 引用必须是集合）")
        object.__setattr__(self, "context_binding_ids", context)
        # 节级声明（本节**已接受**的 context binding 全集）与句级声明（某句**实际使用**的
        # context binding）是两个不同的量：句级是节级的**子集**，反过来不成立。这里只钉方向
        # ——句子不得使用一个本节没有接受的 context 绑定（凭空引用比漏引更危险：它会让一句
        # 话看起来有据，而那依据根本不在本节的已接受集合里）。「已接受但未被任何句子使用」
        # 的完备性由 organizer 的 selected/omitted 去向裁决，不在 wire 层越权判定。
        used_context = tuple(dict.fromkeys(
            cid for paragraph in paragraphs for cid in paragraph.context_binding_ids))
        stray = sorted(set(used_context) - set(context))
        if stray:
            raise NarrativeSchemaError(
                f"句级 context_binding_ids 引用了本节未接受的绑定：{stray}"
                f"（节级已接受 {list(context)}）：context 只承载背景与衔接，"
                "不得为一句正文临时发明依据")
        expected = derive_section_narrative_id(self)
        if self.narrative_id != expected:
            raise NarrativeSchemaError(
                f"SectionNarrative.narrative_id 与内容不符：声明 {self.narrative_id!r}，"
                f"应为 {expected!r}")

    @property
    def paragraph_ids(self) -> tuple[str, ...]:
        return tuple(p.paragraph_id for p in self.paragraphs)

    @property
    def table_ids(self) -> tuple[str, ...]:
        return tuple(t.table_id for t in self.tables)

    @property
    def claim_ids(self) -> tuple[str, ...]:
        """段落与表格引用的定稿 Claim ID 并集（有序、去重）。"""
        return tuple(dict.fromkeys(
            cid for unit in (*self.paragraphs, *self.tables) for cid in unit.claim_ids))

    @property
    def citation_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            cid for unit in (*self.paragraphs, *self.tables) for cid in unit.citation_ids))

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version, "task_id": self.task_id,
            "section_id": self.section_id, "section_draft_id": self.section_draft_id,
            "draft_revision": self.draft_revision,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "tables": [t.to_dict() for t in self.tables],
            "context_binding_ids": list(self.context_binding_ids),
        }

    def to_dict(self) -> dict:
        return {"narrative_id": self.narrative_id, **self.identity_body()}

    @classmethod
    def create(cls, *, task_id: str, section_id: str, section_draft_id: str,
               draft_revision: str, paragraphs: tuple, tables: tuple,
               context_binding_ids: tuple = ()) -> "SectionNarrative":
        # 段落/表格先归一到**对象**：构造函数只接受 `NarrativeParagraph` / `NarrativeTable`，
        # 而 `narrative_id` 必须按 wire 形态（`identity_body()` 的同一口径）计算。两者混用
        # 会让 `create` 永远构造不出一份自洽的 final Narrative（拿 dict 当身份体又拿 dict 当
        # 成员）。入参允许是对象，也允许是它的 `to_dict()` 形态。
        paragraphs = tuple(p if isinstance(p, NarrativeParagraph)
                           else NarrativeParagraph.from_dict(p) for p in paragraphs)
        tables = tuple(t if isinstance(t, NarrativeTable)
                       else NarrativeTable.from_dict(t) for t in tables)
        declared_context = _str_tuple(context_binding_ids or (),
                                      "SectionNarrative.context_binding_ids")
        body = {
            "schema_version": NARRATIVE_SCHEMA_VERSION, "task_id": task_id,
            "section_id": section_id, "section_draft_id": section_draft_id,
            "draft_revision": draft_revision,
            "paragraphs": [p.to_dict() for p in paragraphs],
            "tables": [t.to_dict() for t in tables],
            "context_binding_ids": list(declared_context),
        }
        return cls(narrative_id=content_id("nar_", body), schema_version=body["schema_version"],
                   task_id=task_id, section_id=section_id, section_draft_id=section_draft_id,
                   draft_revision=draft_revision, paragraphs=paragraphs, tables=tables,
                   context_binding_ids=declared_context)

    @classmethod
    def from_dict(cls, d: Any) -> "SectionNarrative":
        d = _reject_unknown(d, {"narrative_id", "schema_version", "task_id", "section_id",
                                "section_draft_id", "draft_revision", "paragraphs", "tables",
                                "context_binding_ids"}, "SectionNarrative")
        return cls(
            narrative_id=_require_nonempty(d.get("narrative_id"), "SectionNarrative.narrative_id"),
            schema_version=d.get("schema_version"),
            task_id=_require_nonempty(d.get("task_id"), "SectionNarrative.task_id"),
            section_id=_require_nonempty(d.get("section_id"), "SectionNarrative.section_id"),
            section_draft_id=_require_nonempty(d.get("section_draft_id"),
                                               "SectionNarrative.section_draft_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "SectionNarrative.draft_revision"),
            paragraphs=tuple(NarrativeParagraph.from_dict(x)
                             for x in _req(d, "paragraphs", "SectionNarrative")),
            tables=tuple(NarrativeTable.from_dict(x)
                         for x in _req(d, "tables", "SectionNarrative")),
            context_binding_ids=_str_tuple(d.get("context_binding_ids") or (),
                                           "SectionNarrative.context_binding_ids"))


def build_section_narrative(*, draft: SectionDraft, claims: Sequence[Any],
                            context_binding_ids: Sequence[str] = (),
                            tables: Sequence[Any] = ()) -> SectionNarrative:
    """门**后** final Narrative 的**唯一**确定性构造器（§0.13：定稿在两道门之后）。

    这是「组装器与协调器共用同一实现」的那一份：P15 用它定稿，组装器用**同一函数**在当前
    Claim 集上重算并要求逐字段一致 —— 否则调用方可以自带一份正文段落，把未经 Claim 授权的
    文本混进正文（§五 4：正文只能由唯一渲染器从已定稿 Claim 派生）。

    规则（只有分组与排版，不新增任何事实表面）：
    1. 每条定稿 `SectionClaim` 贡献**恰好一个**事实句；句子文本 `render_factual_text` 逐字
       来自该 Claim 的 `text`，citation 取该 Claim 自己 `citation_refs` 的**派生** citation ID；
    2. 段落按 `topic_id` **首次出现顺序**分组，一个 topic 一个段落；组内顺序即 Claim 顺序；
    3. 过渡句数量恒为 0：门前 `narrative_draft_units` 只是 context 定位锚点（**不是**正文），
       当前链没有任何合法的连接句来源，因此不产生、也不伪造连接句。
    """
    if not isinstance(draft, SectionDraft):
        raise NarrativeSchemaError("build_section_narrative 的 draft 必须是门前 SectionDraft")
    import sections.schema as _SS  # 延迟导入：取 citation ID 的**唯一**派生实现

    ordered = tuple(claims or ())
    if not ordered and not tuple(tables or ()):
        # 「零 Claim 且零表格」有两种**截然不同**的情形，必须分开判，不能合并成一条拒绝：
        #  (1) 缺口**已如实登记**（draft 明确列出 `unresolved_ids`，且它们各自的权威投影在场）：
        #      本节正文就是缺口附录本身，事实句数为 0。这不是「凭空产生正文」，而是
        #      「如实交代本节没有正文」——行业节在真实 Pack 既无可用事实也无材料时就是这一种。
        #      缺了它，「无内容但有缺口」的章节在定稿处根本无法表达，缺口只能靠 fail-closed
        #      传达，而 fail-closed 不是人读的缺口显示。
        #  (2) 什么都没登记：照旧 fail-closed —— 那才是静默的空章节。
        # 判据是 `has_registered_gaps`（与 `sections.store` 提交前的门后束检查**同一个**实现）。
        if not has_registered_gaps(draft):
            raise NarrativeSchemaError(
                "build_section_narrative 既没有 Claim 也没有表格，draft 也没有登记缺口"
                "（unresolved_ids/权威投影）：定稿正文不得凭空产生"
                "（没有正文不是放行理由，缺口走 Unresolved）")
    grouped: dict[str, list[Any]] = {}
    for claim in ordered:
        topic_id = str(getattr(claim, "topic_id", "") or "")
        claim_id = str(getattr(claim, "claim_id", "") or "")
        text = str(getattr(claim, "text", "") or "")
        if not (topic_id and claim_id and text):
            raise NarrativeSchemaError(
                "build_section_narrative 的 Claim 缺 topic_id/claim_id/text 之一："
                "未定稿的 Claim 不得进入 final Narrative")
        grouped.setdefault(topic_id, []).append(claim)
    paragraphs: list[NarrativeParagraph] = []
    for index, topic_id in enumerate(grouped):
        specs = []
        for claim in grouped[topic_id]:
            # `derive_citation_id` 的输入是**引用对象**（它自己内部才取 `citation_identity`）；
            # 这里多取一次身份字符串会把「引用身份」再当引用解一次，是判据失明不是去重。
            citation_ids = tuple(
                dict.fromkeys(_SS.derive_citation_id(claim.claim_id, ref)
                              for ref in (getattr(claim, "citation_refs", ()) or ())))
            specs.append({
                "text": render_factual_text([claim.text]),
                "sentence_kind": "factual",
                "claim_ids": (claim.claim_id,),
                "citation_ids": citation_ids,
            })
        paragraphs.append(NarrativeParagraph.create(
            section_id=draft.section_id, topic_ids=(topic_id,), index=index,
            sentence_specs=specs))
    return SectionNarrative.create(
        task_id=draft.task_id, section_id=draft.section_id,
        section_draft_id=draft.draft_id, draft_revision=draft.draft_revision,
        paragraphs=tuple(paragraphs), tables=tuple(tables or ()),
        context_binding_ids=tuple(context_binding_ids or ()))


#: §七 2：财务「指标 × 期间」表格成立的下界 —— 同一指标至少在**两个**期间上被接受成 Claim，
#: 才叫「重复的指标 × 期间 事实」。只有一个期间时它就是一句话，表格化会把单点事实包装成矩阵。
FINANCIAL_TABLE_MIN_PERIODS = 2
#: 表格标题的**固定部分**（不含任何事实表面、不来自模型），因此不可能替 Writer 发明结论。
#: 表题**完整**形式由 `financial_table_caption` 给出：固定部分 + 这张表自己的单位与口径。
FINANCIAL_TABLE_CAPTION = "主要财务事实（指标 × 期间）"
#: 期间口径的**读者面**说法（封闭表，取值集 = `financial_v2.period_basis.PERIOD_BASES`）。
#: 与 `READER_UNIT_TEXTS` 同一条纪律：只翻译已登记的口径记号，未登记的记号**原样呈现** ——
#: 渲染层不得替权威补一个它没声明的口径（那是 `build_metric_period_tables` 第 5 条 fail-closed
#: 管的事，不是这里该猜的）。`期间量` / `时点量` 正是「同一列之下流量与时点不可混用」这件事在
#: 读者面的说法。
FINANCIAL_BASIS_TEXTS = {_FPB.BASIS_END: "时点量", _FPB.BASIS_FLOW: "期间量"}


def financial_table_caption(*, unit: Any, basis: Any) -> str:
    """一张展示表的**表题**：固定文案 + 本表自己的单位与期间口径（唯一实现）。

    为什么表题必须带这两个限定：表按 `(unit, period_basis)` 成组，四张表此前**表题逐字相同**，
    读者眼睛先落在的那一行对四张表说同一句话 —— 真实 run 的四张表里有两张（`ratio` 的时点组与
    期间组）连单位都一样，只能靠下一行的期间列名区分。这是读者面的**同一性缺陷**：表题不承担
    身份，身份就只剩下一行小字。

    两个限定语都只做**查表**，不新增任何事实表面：单位走 `reader_unit_text`（与紧随其后的
    「主体 / 期间 / 单位」那一行**逐字同一句**，因此不可能互相矛盾），口径走
    `FINANCIAL_BASIS_TEXTS`。表题里仍然没有数字、没有主体名、没有结论，
    因此「表级文字必须能由权威事实文本逐字授权」这条判据在**零表面**上恒成立 —— 表题不是
    一处可以夹带事实的地方，本函数进不去 `UnauthorizedSurface` 的扫描面。

    单位用的是**完整**说法而不是它的短头部：`yuan` 的短说法是「元」，而格子里逐字取自权威的
    渲染是「1,251.59亿元」—— 读者眼睛先落在表题那一行，只写「单位：元」就等于让表题和它下面
    的第一格说两件看起来不同的事。完整说法自带「单元格按各自数值量级显示为亿元 / 万元 / 元」
    这句限定，读者不必读第二行才知道「元」是权威的归一化口径而不是格子里的字面量。
    """
    parts = [FINANCIAL_TABLE_CAPTION]
    unit_text = reader_unit_text(unit)
    if unit_text:
        parts.append(f"单位：{unit_text}")
    token = str(basis or "").strip()
    caliber = FINANCIAL_BASIS_TEXTS.get(token, token)
    if caliber:
        parts.append(f"口径：{caliber}")
    return " · ".join(parts)
#: 空白单元格：显式表达「本行指标在这一列期间没有已接受的权威事实」。它不是占位符，
#: 不得被填补、推算或按公式回填（缺值显式留空，见 `NarrativeTableRow` 的对应规则）。
FINANCIAL_TABLE_EMPTY_CELL = ""
#: 「指标 × 期间」表格的**列轴**表头（第一列是行标签，其余列是期间）。
FINANCIAL_TABLE_LABEL_HEADER = "指标"


def tabled_claim_ids(tables: Sequence[Any]) -> tuple[str, ...]:
    """被表格行承载的 Claim ID（有序去重）——「哪些 Claim 由表格呈现」的**唯一**读取入口。

    表格与正文的分工必须由一个函数给出：组织器决定「不把这些 Claim 写成句子」、组装器复算
    「这些 Claim 只该出现在表格里」，两处各写一遍「谁在表格里」就会各说各话。
    """
    out: list[str] = []
    for table in tuple(tables or ()):
        for row in tuple(getattr(table, "rows", ()) or ()):
            for cid in tuple(getattr(row, "claim_ids", ()) or ()):
                if cid not in out:
                    out.append(cid)
    return tuple(out)


def build_metric_period_tables(*, section_id: str, claims: Sequence[Any], authority: Any,
                               acceptance: Any) -> tuple[NarrativeTable, ...]:
    """门后**确定性**构造「指标 × 期间」表格（§七 2）：不发起任何模型调用、不做任何计算。

    规则（全部可复算，输入只有已定稿 Claim 与权威输入）：

    1. **表格候选**：一条 Claim 恰好由**一条** `financial_pack` 权威事实支撑时才是候选。
       一条 Claim 对应多条事实、或支撑边不指向财务事实时，它的呈现位置是它自己的事实句
       —— 表格不得替它挑一条事实来陈列；
    2. **成行下界**：同一 `(kind, code)` 的候选按期间成行，且至少
       `FINANCIAL_TABLE_MIN_PERIODS` 个期间才成表；不足下界的指标留在正文里当事实句；
    2b. **双期派生事实不是行**：声明了两期输入血缘的事实（`is_derived_two_period_fact`，
       如 `FORMULA_REVIEW` §5.1 的 Δpp）说的是「两期之间的变化」，它的期间是复合记号，
       不属于任何单一期间。它承载**正文**，不进期间轴表格 —— 否则表格必须给这个差额一个
       列名，而任何单一列名都在说一件不成立的事；
    3. **单元格**：`financial_table_cell` —— 逐字取权威自己的数值渲染（`display`，缺失时退到
       `value_text`，与 `authoritative_fact_surface` **同一表达式**），代理口径的数值**带口径
       限定语**（读者在表格里看到的那一格必须自己写出口径）。门按 `financial_cell_components`
       的**分量**核对：数值、期间表达、（代理时的）完整口径限定语必须**各自逐字**出现在该 Claim
       的文本里 —— 表格里因此不可能出现 Claim 没有承担的数值、期间或披露；某个期间没有事实时
       该格**留空**（`FINANCIAL_TABLE_EMPTY_CELL`），绝不推算/补齐/按公式回填；
    4. **列轴**：列 = 该表涉及期间的并集，顺序取权威 artifact 自己声明的 `periods`
       （不自创期间顺序）；行内出现 artifact 未声明的期间 → fail-closed。列名与行期间写的是
       **期间表达**（`financial_period_text`，如 `2025年度` / `2025年末`）而不是裸的期间末日：
       一列之下既有流量指标又有余额指标时，期间末日会把流量的量写成时点的量；
    5. **口径分组**：表按 `(unit, period_basis)` 成组 —— 同一张表的每一行必须是同一口径，否则
       列名只能对着其中一半行说真话。事实**声明了**口径却不在封闭取值内、或期间记号是权威日期
       形状却**没有**声明口径 → fail-closed（不凭期间记号的外观替它挑一个口径）。
       分组键就是表题的**判别依据**：表题由 `financial_table_caption(unit, basis)` 给出，读者在
       表题那一行就能读出这张表的单位与口径，不必先读列名再回推；
    6. **一致性**：同一指标跨期间的 `label`/`unit` 不一致、同一格归属两条 Claim、或同一列出现
       两种期间表达 → fail-closed：这类不一致是数据问题，不得被表格排版抹平。

    返回表格元组（可为空：没有可成表的「重复指标 × 期间」事实时，本节不产生表格）。
    """
    ordered = tuple(claims or ())
    if not ordered:
        return ()
    import sections.schema as _SS

    fact_objects = authority_fact_entries(authority)
    bindings = {str(getattr(b, "accepted_support_binding_id", "") or ""): b
                for b in tuple(getattr(acceptance, "accepted_bindings", ()) or ())}
    groups: dict[tuple[str, str, str], dict[str, tuple[Any, Any]]] = {}
    order: list[tuple[str, str, str]] = []
    for claim in ordered:
        claim_id = str(getattr(claim, "claim_id", "") or "")
        coords: list[tuple[str, str, str]] = []
        for binding_id in tuple(getattr(claim, "accepted_binding_ids", ()) or ()):
            binding = bindings.get(str(binding_id))
            if binding is None:
                raise NarrativeSchemaError(
                    f"Claim {claim_id!r} 声明的 accepted binding {binding_id!r} 不在本次 accepted "
                    "集内：表格不得据未知支撑边构造")
            if str(getattr(binding, "support_semantics", "")) != "factual":
                continue
            coord = binding_fact_key(binding)
            if coord is not None:
                coords.append(coord)
        if len(coords) != 1 or coords[0][0] != "financial_pack":
            continue
        fact = fact_objects.get(coords[0])
        if fact is None:
            raise NarrativeSchemaError(
                f"Claim {claim_id!r} 的财务事实坐标 {coords[0]} 不在本 authority 输入内："
                "表格行不得指向别的章节的事实")
        # 2b. **双期派生事实永不进期间轴表格**（`FORMULA_REVIEW` §5.1 的 Δpp）：它的 `period`
        #     是复合记号（`本期|上期`），不是任何单一期间。它一旦成为一行，表格就必须给它一个
        #     列名，而任何单一列名都在说一件不成立的事（这个差额不属于 2025-12-31 那一刻，
        #     也不属于 2025 那一整年）。它承载的是**正文**，表格保留原有各期事实。
        #     判据是事实自己声明的字段（`input_periods`），不是 `code` 字符串，也不是期间形状；
        #     下游的「列轴只能由 artifact 声明期间组成」那条仍然保留，作为纵深防线。
        if is_derived_two_period_fact(fact):
            continue
        code = str(getattr(fact, "code", "") or "").strip()
        period = str(getattr(fact, "period", "") or "").strip()
        value = (str(getattr(fact, "display", "") or "").strip()
                 or str(getattr(fact, "value_text", "") or "").strip())
        if not (code and period and value):
            # 缺 code/期间/数值渲染的财务事实没有可陈列的单元格（它是缺口，不是表格行）。
            continue
        # 口径必须由权威自己声明（`financial_v2.period_basis`）。声明了却不在封闭取值内、或
        # 期间记号本身就是权威日期形状却没有声明口径 —— 两种都不许"按外观补一个口径"：补错
        # 方向恰好是把期间量写成时点量。
        basis = str(getattr(fact, "period_basis", "") or "").strip()
        if basis and basis not in _FPB.PERIOD_BASES:
            raise NarrativeSchemaError(
                f"财务事实 {getattr(fact, 'fact_id', '?')!r} 的期间口径 {basis!r} 不在"
                f" {list(_FPB.PERIOD_BASES)} 内（口径是封闭取值，不得自造）")
        if not basis and _FPB.is_authority_period_token(period):
            raise NarrativeSchemaError(
                f"财务事实 {getattr(fact, 'fact_id', '?')!r} 的期间 {period!r} 是权威日期形状，"
                "却没有声明期间口径（`period_basis`）：无法判断它是时点量还是期间量，"
                "表格不得凭期间记号的外观替它挑一个")
        # C4：**分量**逐项核对，不核对拼起来之后的连续性。旧口径要求整格（`值。限定语`）是
        # Claim 的连续子串，于是那个内部句号成了隐含要求，而写作侧被禁止在 `claim_text` 中间写
        # 句末标点——两条要求互斥。拆开之后：数值 / 期间表达 / 完整限定语各自逐字出现即通过；
        # 标点不再承担任何判据（权威自己的标点仍照原样渲染进 `cell`）。
        # 方向是**收紧**：旧口径**没有**核对期间表达，新口径把它补上了；三个分量各自都不得省略。
        claim_text = str(getattr(claim, "text", "") or "")
        components = financial_cell_components(fact)
        for label, required in components.required_texts():
            if required not in claim_text:
                raise NarrativeSchemaError(
                    f"Claim {claim_id!r} 的文本里没有该权威事实的{label} {required!r}"
                    f"（该格渲染为 {components.cell!r}）：表格单元格只能逐字复用 Claim 已经承担的"
                    "权威表面分量——数值、期间表达与完整口径限定语三者缺一不可"
                    + ("（代理口径的限定语只写在引用链里不算披露：引用链不进表格正文，"
                       "读者在那一格里看到的仍是光秃秃的数值）" if components.is_proxy else ""))
        key = (str(getattr(fact, "kind", "") or ""), code, basis)
        bucket = groups.setdefault(key, {})
        if period in bucket:
            raise NarrativeSchemaError(
                f"财务事实 {key} 在期间 {period!r} 上有不止一条 Claim："
                "同一格不得归属两条断言")
        bucket[period] = (fact, claim)
        if key not in order:
            order.append(key)
    if not order:
        return ()

    declared_periods = tuple(
        str(p) for p in (getattr(getattr(authority, "artifact", None), "periods", ()) or ()))
    company_id = str(getattr(authority, "company_id", "") or "").strip()
    if not company_id:
        raise NarrativeSchemaError(
            "财务表格的 entity_scope 必须来自权威主体身份（company_id 为空，不得留空）")
    # 读者面（`pwr-4`）：主体必须写成**可核实的公司名称**，并保留可回查的主体标识。名称来自
    # 权威输入自己的 `company_name`（由本次报告输入声明带入，见 `run_m930_3_acceptance`）。
    # 权威没有名称时**不发明一个** —— 退到原来的主体标识，读者看到的仍是一个真实字段。
    company_name = str(getattr(authority, "company_name", "") or "").strip()
    entity_scope = f"{company_name}（{company_id}）" if company_name else company_id

    # 表按 `(unit, period_basis)` 成组：同一张表的每一行必须同口径，否则列名只能对着其中一半
    # 行说真话（`2025年末` 对着利润表流量就是错的，`2025年度` 对着资产负债表余额也是错的）。
    by_table: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    table_order: list[tuple[str, str]] = []
    for key in order:
        bucket = groups[key]
        if len(bucket) < FINANCIAL_TABLE_MIN_PERIODS:
            continue
        labels = {str(getattr(f, "label", "") or "").strip() for f, _ in bucket.values()}
        units = {str(getattr(f, "unit", "") or "").strip() for f, _ in bucket.values()}
        if len(labels) != 1 or len(units) != 1:
            raise NarrativeSchemaError(
                f"财务事实 {key} 跨期间的 label/unit 不一致（label={sorted(labels)}，"
                f"unit={sorted(units)}）：同一个指标不得在表格里有两套名字或两个量纲")
        label = labels.pop()
        unit = units.pop()
        if not label or not unit:
            raise NarrativeSchemaError(
                f"财务事实 {key} 缺 label/unit：含数字的表格行必须有指标名与单位，"
                "不得由 Writer 推定")
        table_key = (unit, key[2])
        by_table.setdefault(table_key, []).append(key)
        if table_key not in table_order:
            table_order.append(table_key)
    if not by_table:
        return ()

    tables: list[NarrativeTable] = []
    for unit, basis in table_order:
        keys = by_table[(unit, basis)]
        used = {period for key in keys for period in groups[key]}
        if not declared_periods:
            raise NarrativeSchemaError(
                "权威 artifact 没有声明任何期间顺序：财务表格的列轴不得自创顺序")
        columns = [period for period in declared_periods if period in used]
        if set(columns) != used:
            raise NarrativeSchemaError(
                f"财务表格出现 artifact 未声明的期间 {sorted(used - set(declared_periods))}："
                "列轴只能由权威声明的期间组成")
        # 列名写**期间表达**而不是裸的期间末日（`financial_period_text`）。同一列只能有一种表达
        # ——期间表达是权威事实自己带的字段，同一期间上出现两种说法是数据冲突，不得由排版抹平。
        column_texts: list[str] = []
        for period in columns:
            texts: set[str] = set()
            for key in keys:
                entry = groups[key].get(period)
                if entry is not None:
                    texts.add(financial_period_text(entry[0]))
            if len(texts) != 1:
                raise NarrativeSchemaError(
                    f"财务表格的期间 {period!r} 在列名上有不止一种期间表达 {sorted(texts)}："
                    "同一列不得有两种期间说法")
            column_texts.append(texts.pop())
        if len(set(column_texts)) != len(column_texts):
            raise NarrativeSchemaError(
                f"财务表格出现重名的期间表达 {column_texts}："
                "两列同名时读者无法判断哪一列是哪个期间")
        row_specs: list[dict] = []
        topics: list[str] = []
        for key in keys:
            bucket = groups[key]
            label = str(getattr(next(iter(bucket.values()))[0], "label", "") or "").strip()
            cells: list[str] = []
            claim_ids: list[str] = []
            citation_ids: list[str] = []
            row_periods: list[str] = []
            for period, column_text in zip(columns, column_texts):
                entry = bucket.get(period)
                if entry is None:
                    # 缺值显式留空：这一格没有已接受的权威事实，不得推算/补齐/回填。
                    cells.append(FINANCIAL_TABLE_EMPTY_CELL)
                    continue
                fact, claim = entry
                cells.append(financial_table_cell(fact))
                row_periods.append(column_text)
                claim_ids.append(str(claim.claim_id))
                citation_ids.extend(
                    _SS.derive_citation_id(claim.claim_id, ref)
                    for ref in tuple(getattr(claim, "citation_refs", ()) or ()))
                topic = claim_topic_for_conservation(claim)
                if topic not in topics:
                    topics.append(topic)
            row_specs.append({
                "label": label, "cells": tuple(cells), "claim_ids": tuple(claim_ids),
                "citation_ids": tuple(dict.fromkeys(citation_ids)),
                "unit": unit, "period": "、".join(row_periods),
            })
        tables.append(NarrativeTable.create(
            section_id=section_id, topic_ids=tuple(topics), index=len(tables),
            caption=financial_table_caption(unit=unit, basis=basis),
            header=(FINANCIAL_TABLE_LABEL_HEADER, *column_texts),
            row_specs=tuple(row_specs), entity_scope=entity_scope, unit=unit,
            period="、".join(column_texts)))
    return tuple(tables)


# ---------------------------------------------------------------------------
# 门后**确定性**核验（narr-5；§三 C）
# ---------------------------------------------------------------------------
#
# 自然组织把「正文文本」从确定性派生变成了模型产物，于是正文侧必须补一道**确定性**核验：
# 文本不再是可复算的，但「它引用了谁」「它的高风险表面来自哪里」「哪些 Claim 没被用上」
# 这三件事仍然可以逐条机械判定。本函数是这道核验的**唯一**实现：
#   * 自然组织器在**发出前**用它自检（organizer 不得批准自己的输出，但可以是第一个撞上它的人）；
#   * 组装器在**读回后**用它复算（读回来的正文必须与已定稿 Claim 集自洽，否则拒绝组装）。
#
# 判据刻意只有这七条，且每条都是机械的：
#   1. 引用的 Claim 必须**存在且当前**：每个 `claim_ids` 的元素都必须在本次定稿的 Claim 集里，
#      且句子文本所属的段落/节级引用集合与之一致（不得引用上一状态或被替换掉的 Claim）；
#   2. 事实表面必须可追溯到**它自己声明的** Claim：数字 / 主体身份 / 含糊期间 / 否定与状态
#      标记 / 推理与结论连接一律要 `unauthorized_surfaces` 为空（context 绑定不参与授权）；
#   3. 不得出现新的高风险 token：由第 2 条统一覆盖（扫描面就是 `high_risk_surface_tokens`），
#      因此这里不另立第二套「不该出现的词」清单；
#   4. context 只有背景/衔接权：`context_binding_ids` 必须在已接受的 context binding 集内，
#      且**不得**出现在 factual 句上；
#   5. selected/omitted 去向必须**完备**：每条本次定稿的 Claim 恰好被处置一次；
#   6. §三 G selected-material limitation（句级）：`citation_ids` 必须逐条由本句声明的 Claim
#      派生——引用不得事后补、不得指向本节未选中的材料；
#   7. §三 G cross-topic 守恒（段级）：段落 `topic_ids` 必须等于它真正引用的 Claim 的 topic
#      首次出现序列——不得改挂主题，也不得声明没用到过的主题。

#: 一条定稿 Claim 在 final Narrative 里的去向（§三 C.5）。`omitted` 必须给出**封闭**理由码：
#: 自由文本理由会让「为什么这条事实没写进去」重新变成不可判定的措辞。
CLAIM_NARRATIVE_DISPOSITIONS = ("selected", "omitted")
#: `ClaimNarrativeDisposition` 的**自有** wire marker（§三 E：每个 current wire 对象都要有
#: schema marker、主键与内容身份，数据库 migration 序号不替代 wire discriminator）。
#: 它**不**等于 `NARRATIVE_SCHEMA_VERSION`：本类是否被持久化与 narr 主 wire 版本是两件事，
#: 混用一个值会让「narr-5 载荷」与「去向载荷」在 v2 family 里无法区分。
CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION = "cnd-1"
CLAIM_OMISSION_REASONS = (
    # 该 Claim 属于本节之外的主题（例如另一财务章节的指标），本节不呈现。
    "outside_section_topic",
    # 该 Claim 与已选入的另一条 Claim 表达同一原子断言，取其一即可（同一事实不得重复陈列）。
    "redundant_with_selected_claim",
    # 该 Claim 由节级表格承载，不另设事实句（表格行本身就是它的呈现位置）。
    "presented_as_table_row",
)

#: 近义重复**筛查**的版本（`redundancy-screen-1`）。
REDUNDANCY_SCREEN_VERSION = "redundancy-screen-1"
#: 筛查阈值。实测依据（本次真实公司节，1651 对互不逐字包含的 Claim）：真近义对全部落在
#: `D ≥ 0.800`，而最像的**真不同**对是 `0.675`（EnerC… vs EnerOne… 两种不同产品）——两侧
#: 之间有 `0.125` 的裕度，阈值取在裕度中间。
#:
#: **但阈值不是判据，只是候选生成器**（§0.12：相似度只生成候选，判定仍由规则/模型给出）。
#: 反例就在同一批里：`0.885` 的那一对（「…以及交流侧系统等储能产品解决方案」vs
#: 「…以及系统集成等储能解决方案」）内容**不同**，按 organizer 规则「只要有一项内容不同，
#: 两条都必须保留」，它**必须**两条都留。任何把它直接判成重复的阈值都是错的——这正是
#: 「筛查只出候选、判定归宣称方」的原因。
REDUNDANCY_SCREEN_THRESHOLD = 0.80


def redundancy_candidate_pairs(claims: "Sequence[Any]", *,
                               threshold: float = REDUNDANCY_SCREEN_THRESHOLD
                               ) -> "tuple[tuple[str, str, float], ...]":
    """**候选**级的近义重复筛查：返回 `(claim_id_a, claim_id_b, 相似度)`，按相似度降序。

    它**不做判定**，因此不得被当作「这两条是重复的」的结论使用——它只回答「这几对值得看」。
    判定归组织器（`redundant_with_selected_claim` 的语义是「表达同一原子断言」，那是语义判断，
    不是字面判断）。这个分工与 §0.12 对 Contract/标题相似度定的口径一致：相似度**只生成候选**。

    逐字包含的对**不**入选：那是另一种情形（短断言被长断言逐字涵盖），由调用方按包含关系处理，
    路径与本函数的近似匹配不同。空文本与自比一律跳过。
    """
    import difflib
    rows: list[tuple[str, str, float]] = []
    items = [(str(getattr(c, "claim_id", "") or ""), str(getattr(c, "text", "") or ""))
             for c in (claims or ())]
    items = [(cid, text) for cid, text in items if cid and text]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (cid_a, text_a), (cid_b, text_b) = items[i], items[j]
            if text_a in text_b or text_b in text_a:
                continue
            score = difflib.SequenceMatcher(None, text_a, text_b).ratio()
            if score >= threshold:
                rows.append((cid_a, cid_b, round(score, 4)))
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return tuple(rows)


@dataclass(frozen=True)
class ClaimNarrativeDisposition:
    """一条定稿 Claim 在 final Narrative 里的**去向**（narr-5；§三 C 第 5 条）。

    它与 `FactNarrativeDisposition` 是**两个不同的身份轴**，不得互填：FND 描述「一份权威事实
    有没有被写进正文」，本类描述「一条**已定稿 Claim**有没有被选进 final Narrative」。
    一条 FND 可以对应零条 Claim（事实没被接受成 Claim），一条 Claim 必然对应一份已接受支撑。
    """

    claim_narrative_disposition_id: str
    schema_version: str
    section_id: str
    draft_revision: str
    claim_id: str
    disposition: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                "ClaimNarrativeDisposition.schema_version 必须为 "
                f"{CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION!r}（得到 {self.schema_version!r}）")
        _require_nonempty(self.claim_narrative_disposition_id,
                          "ClaimNarrativeDisposition.claim_narrative_disposition_id")
        for name in ("section_id", "draft_revision", "claim_id"):
            _require_nonempty(getattr(self, name), f"ClaimNarrativeDisposition.{name}")
        _require_enum(self.disposition, CLAIM_NARRATIVE_DISPOSITIONS,
                      "ClaimNarrativeDisposition.disposition")
        if self.disposition == "omitted":
            if not self.reason_code:
                raise NarrativeSchemaError(
                    "ClaimNarrativeDisposition 的 omitted 必须给出封闭理由码："
                    "「没写进去」没有理由就不是去向记录")
            _require_enum(self.reason_code, CLAIM_OMISSION_REASONS,
                          "ClaimNarrativeDisposition.reason_code")
        elif self.reason_code is not None:
            raise NarrativeSchemaError(
                "ClaimNarrativeDisposition 的 selected 不得携带 reason_code"
                "（被选中的 Claim 不需要缺席理由）")
        expected = derive_claim_narrative_disposition_id(self)
        if self.claim_narrative_disposition_id != expected:
            raise NarrativeSchemaError(
                f"ClaimNarrativeDisposition 身份与内容不符：声明 "
                f"{self.claim_narrative_disposition_id!r}，应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "section_id": self.section_id, "draft_revision": self.draft_revision,
            "claim_id": self.claim_id, "disposition": self.disposition,
            "reason_code": self.reason_code,
        }

    def to_dict(self) -> dict:
        return {"claim_narrative_disposition_id": self.claim_narrative_disposition_id,
                **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ClaimNarrativeDisposition":
        body = {
            "schema_version": str(kwargs.get("schema_version")
                                  or CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION),
            "section_id": str(kwargs.get("section_id") or ""),
            "draft_revision": str(kwargs.get("draft_revision") or ""),
            "claim_id": str(kwargs.get("claim_id") or ""),
            "disposition": str(kwargs.get("disposition") or ""),
            "reason_code": kwargs.get("reason_code"),
        }
        return cls(claim_narrative_disposition_id=content_id("cnd_", body), **body)

    @classmethod
    def from_dict(cls, d: Any) -> "ClaimNarrativeDisposition":
        d = _reject_unknown(d, {"claim_narrative_disposition_id", "schema_version", "section_id",
                                "draft_revision", "claim_id", "disposition", "reason_code"},
                            "ClaimNarrativeDisposition")
        return cls(
            claim_narrative_disposition_id=_require_nonempty(
                d.get("claim_narrative_disposition_id"),
                "ClaimNarrativeDisposition.claim_narrative_disposition_id"),
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "ClaimNarrativeDisposition.schema_version"),
            section_id=_require_nonempty(d.get("section_id"),
                                         "ClaimNarrativeDisposition.section_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "ClaimNarrativeDisposition.draft_revision"),
            claim_id=_require_nonempty(d.get("claim_id"), "ClaimNarrativeDisposition.claim_id"),
            disposition=_require_nonempty(d.get("disposition"),
                                          "ClaimNarrativeDisposition.disposition"),
            reason_code=d.get("reason_code"))


def derive_claim_narrative_disposition_id(disp: Any) -> str:
    body = (disp.identity_body() if isinstance(disp, ClaimNarrativeDisposition) else disp.body)
    return content_id("cnd_", body)


def verify_claim_narrative_dispositions(
        claims: Sequence[Any], dispositions: Sequence[ClaimNarrativeDisposition],
        *, section_id: str, draft_revision: str) -> tuple[ClaimNarrativeDisposition, ...]:
    """去向必须**恰好一次覆盖每条定稿 Claim**（缺失/重复/额外一律拒）。

    与 `verify_fnd_key_set` 同一纪律：只判「集合是否精确相等」，不判文本好坏。
    """
    expected = tuple(str(getattr(c, "claim_id", "") or "") for c in (claims or ()))
    if any(not cid for cid in expected):
        raise NarrativeSchemaError(
            "verify_claim_narrative_dispositions 的 Claim 集含空 claim_id（未定稿对象不得进入核验）")
    declared: list[str] = []
    for disp in dispositions or ():
        if not isinstance(disp, ClaimNarrativeDisposition):
            raise NarrativeSchemaError(
                "verify_claim_narrative_dispositions 只接受 ClaimNarrativeDisposition，"
                f"得到 {type(disp).__name__}")
        if disp.section_id != section_id or disp.draft_revision != draft_revision:
            raise NarrativeSchemaError(
                f"去向 {disp.claim_narrative_disposition_id} 不属于本次定稿"
                f"（{disp.section_id!r}/{disp.draft_revision!r} ≠ {section_id!r}/{draft_revision!r}）")
        declared.append(disp.claim_id)
    duplicates = sorted({c for c in declared if declared.count(c) > 1})
    if duplicates:
        raise NarrativeSchemaError(
            f"同一条 Claim 出现多条去向记录：{duplicates}（去向必须恰好一次）")
    missing = sorted(set(expected) - set(declared))
    extra = sorted(set(declared) - set(expected))
    if missing or extra:
        raise NarrativeSchemaError(
            f"Claim 去向不精确相等：缺 {missing[:6]}，多 {extra[:6]}"
            f"（每条定稿 Claim 恰好一条去向：不得静默丢弃，也不得给不存在者编造去向）")
    return tuple(dispositions)


def claim_citation_ids(claim: Any) -> tuple[str, ...]:
    """一条 current Claim 自己的引用 ID（**唯一**派生口径，§三 G「selected-material limitation」）。

    引用只能由**这条 Claim 自己的来源**派生（`derive_citation_id` 的输入是引用**对象**，多取一次
    身份串会把引用身份再当引用解一次）。组织器与门后核验必须共用这一个口径，否则「系统派生的
    引用」与「核验承认的引用」会变成两套集合，而两句之差正好是伪造引用的容身处。

    缺 `citation_refs` 的替身对象**不给默认值**：那会把「这条 Claim 的引用来源无从核验」变成
    「它没有引用」，于是伪造引用反而更容易通过。缺字段一律 typed fail-closed。
    """
    import sections.schema as _SS
    claim_id = str(getattr(claim, "claim_id", "") or "")
    refs = getattr(claim, "citation_refs", None)
    if refs is None:
        raise NarrativeSchemaError(
            f"Claim {claim_id or '<无 id>'} 缺 citation_refs（{type(claim).__name__}）："
            "门后核验需要 current Claim wire，不得用缺字段的替身对象跳过引用限定")
    return tuple(dict.fromkeys(_SS.derive_citation_id(claim_id, ref) for ref in refs))


def claim_topic_for_conservation(claim: Any) -> str:
    """一条被正文引用的 Claim 的 topic 归属；缺字段同样 typed fail-closed（理由同上）。

    它是「段落 topic 归属只能由所引用 Claim 派生」这条判据的**唯一**实现：组织器用它决定
    段落 topic，门后核验用它复算段落 topic，两边不允许各自解释一遍。
    """
    topic = getattr(claim, "topic_id", None)
    if not isinstance(topic, str) or not topic.strip():
        raise NarrativeSchemaError(
            f"Claim {str(getattr(claim, 'claim_id', '') or '<无 id>')} 缺 topic_id"
            f"（{type(claim).__name__}）：段落 topic 归属只能由所引用 Claim 派生，"
            "缺字段不得被当成「无 topic」而跳过跨主题守恒核验")
    return topic


#: 第 8 条（「composed 句确实被组织过」）的**封闭缺陷码**。它们只描述**文本层**的机械事实，
#: 不描述文风、不做任何语义判断——因此可以被「有界定向重组织」（`sections/narrative_organizer`）
#: 直接消费：补救方按**同一份判据**知道「这一句到底哪里不合法」，不必去解析错误文本。
#:
#: **不含**第 2/3 条（高风险表面）：重组织只修**组织形态**，永不放行表面。衔接区里自造的主体名 /
#: 数字 / 期间 / 因果，以及第 9 条的系统自述措辞，都由整份核验原样拦住（见
#: `evals/test_m930_3_writing_closure` 的反例组）。
COMPOSED_DEFECT_NOT_PRESENT_IN_ORDER = "claims_not_present_in_order"
COMPOSED_DEFECT_MECHANICAL_JOIN = "mechanical_join"
COMPOSED_DEFECT_INTERNAL_TERMINATOR = "internal_sentence_terminator"
COMPOSED_DEFECT_UNSEPARATED_SEAM = "unseparated_seam"
COMPOSED_DEFECT_OVER_CLAIM_BOUND = "over_claim_bound"
COMPOSED_SENTENCE_DEFECTS = (
    COMPOSED_DEFECT_NOT_PRESENT_IN_ORDER,
    COMPOSED_DEFECT_MECHANICAL_JOIN,
    COMPOSED_DEFECT_INTERNAL_TERMINATOR,
    COMPOSED_DEFECT_UNSEPARATED_SEAM,
    COMPOSED_DEFECT_OVER_CLAIM_BOUND,
)


def unseparated_seam_positions(text: str, claim_texts: Sequence[str]) -> tuple[int, ...]:
    """判据 e 的**唯一**实现：返回**没有分隔标点起头**的接缝（`claim_texts` 的下标），合规为空。

    接缝 = 相邻两条 Claim 文本之间的那段组织成分。它的第一个字符必须是
    `SEAM_LEAD_PUNCTUATION` 里的分隔标点：连接语（`此外，`/`同时，`…）本身自带一个**尾部**
    逗号，写在接缝开头时读者看到的是「上一条断言 + 连接语」黏在一起（`…生产、销售此外，公司…`）。
    判据只看第一个字符，不看连接语是什么——把「连接语是否在封闭词表里」当判据会漏掉相邻
    Claim 直接黏连，也会把「不许写某个词」当成正文纪律，而那不是本判据要管的事。

    定位用 `_claim_spans`（与判据 a 同一实现）：跨度取不到时返回空——那种句子已经在判据 a 被拒，
    本判据不必替它下第二个结论（同一份文本只报**第一个**不合法之处）。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (claim_texts or ()))
    if len(texts) < 2:
        return ()
    spans = _claim_spans(body, texts)
    if spans is None:
        return ()
    bad: list[int] = []
    for pos in range(len(spans) - 1):
        seam_start = spans[pos][1]
        if seam_start >= spans[pos + 1][0] or body[seam_start] not in SEAM_LEAD_PUNCTUATION:
            bad.append(pos)
    return tuple(bad)


def composed_organization_defect(*, text: str,
                                 authorized_texts: Sequence[str]) -> str | None:
    """第 8 条的**唯一**判据实现：返回封闭缺陷码，合规返回 `None`。

    输入刻意是**纯文本**（句子文本 + 它声明的 Claim 文本），不是句子对象：判据既要在门前
    （句子还没建出来）被补救方按计划文本询问，也要在门后被核验方按已建成的句子询问。两处若
    各自实现一遍「什么算机械拼接」，那句「这一句只是标点拼接」就会有两个口径。

    判据顺序与错误文本的呈现顺序一致（a → b → d → e → c）：先判「声明的 Claim 有没有真在
    句子里」，再判「有没有组织成分」，再判「是不是一句」，再判「接缝有没有分隔标点」，
    最后判「有没有塞太多」。

    顺序里 d 在 e 之前是**刻意的**：`A。同时，B。` 同时违反两条，而它应当报的是 d（两个句子
    首尾相接）——接缝判据（e）不该盖住那个更本质的缺陷码，否则补救方会以为「给它补个逗号」
    就够了（那正是把断句洗成自然句）。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (authorized_texts or ()))
    residue = _organizing_residue(body, texts)
    if residue is None:
        return COMPOSED_DEFECT_NOT_PRESENT_IN_ORDER
    if len(texts) >= 2 and not any(_CJK_START <= ch <= _CJK_END for ch in residue):
        return COMPOSED_DEFECT_MECHANICAL_JOIN
    if has_internal_sentence_terminator(body):
        return COMPOSED_DEFECT_INTERNAL_TERMINATOR
    if unseparated_seam_positions(body, texts):
        return COMPOSED_DEFECT_UNSEPARATED_SEAM
    if len(texts) > MAX_COMPOSED_CLAIMS_PER_SENTENCE:
        return COMPOSED_DEFECT_OVER_CLAIM_BOUND
    return None


def split_composed_sentence_at_terminators(*, text: str, authorized_texts: Sequence[str]
                                           ) -> "tuple[tuple[str, tuple[int, ...]], ...] | None":
    """把一条 composed 句按**它自己的**句中句末标点切成若干段，并逐段重绑 Claim。

    这是 ⑥ 的**有界定向重组织**的第一级：`A。同时B。` 这种「双句容器」在文本层就是两个句子，
    切点由模型自己的标点给出，因此切出来的每一段都**逐字是模型写的原文**——不新增一个字、
    不删一个字（`"".join(part_texts) == text` 恒成立），模型的衔接语（如 `同时`）跟着它原本
    相邻的那一段留下。它与「用固定连接语词表替模型补组织语」是两回事：后者是系统替模型写正文。

    返回 `tuple[(段文本, 该段的 Claim 位置)]`（位置是 `authorized_texts` 的下标，按声明序），
    或者 `None` 表示**切不出全部合法的段**——调用方据此落到下一级补救（每条 Claim 各自成句）。
    `None` 的三种情形，每一种都是「切了也不合法」，不是「懒得切」：

    * 声明的 Claim 文本没有按序出现（绑定与正文不一致，切分不能把没出现的 Claim 变出来）；
    * 某条 Claim 的跨度**跨过**切点（那条 Claim 自己就含句末标点：既切不开，也不能指望它在
      任何一段里逐字完整）；
    * 切出来的某一段自己没有 Claim，或切完仍有段不合法（同一判据）。

    切点只取**句中**的句末标点：末尾那一串只是句读（`has_internal_sentence_terminator` 同口径）。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (authorized_texts or ()))
    spans = _claim_spans(body, texts)
    if spans is None:
        return None
    stripped_end = len(body)
    while stripped_end > 0 and body[stripped_end - 1] in SENTENCE_TERMINATORS:
        stripped_end -= 1
    cuts = [index + 1 for index in range(stripped_end)
            if body[index] in SENTENCE_TERMINATORS]
    if not cuts:
        return None
    bounds: list[tuple[int, int]] = []
    start = 0
    for cut in (*cuts, len(body)):
        bounds.append((start, cut))
        start = cut
    bounds = [(lo, hi) for lo, hi in bounds if hi > lo]
    parts: list[tuple[str, tuple[int, ...]]] = []
    for lo, hi in bounds:
        inside = tuple(
            pos for pos, (span_lo, span_hi) in enumerate(spans)
            if lo <= span_lo and span_hi <= hi)
        if not inside:
            return None
        for pos, (span_lo, span_hi) in enumerate(spans):
            if pos not in inside and span_lo < hi and lo < span_hi:
                return None
        parts.append((body[lo:hi], inside))
    if "".join(part for part, _ in parts) != body:
        return None
    covered = [pos for _part, inside in parts for pos in inside]
    if covered != list(range(len(texts))):
        return None
    for part_text, inside in parts:
        if composed_organization_defect(
                text=part_text, authorized_texts=[texts[pos] for pos in inside]) is not None:
            return None
    return tuple(parts)


def unsupported_relation_connectors(text: str, claim_texts: "Sequence[str]"
                                    ) -> "tuple[tuple[str, str], ...]":
    """第 10 条的**唯一**定位实现：返回接缝里**没有材料支持**的关系性连接语 `(连接语, 组织段)`。

    「有材料支持」的口径与第 9 条同形、同理由——**逐字在场**：该连接语必须能在本句自己声明的
    Claim 文本里逐字找到，即**材料自己说了这个关系**。材料没写这个关系而正文写了
    `A，另一方面，B`，等于替材料下一个它没有下的对照判断（与「因此」「说明」同属把并列升级为
    推理）。合规的替代写法是中性并列（`此外，` / `同时，`）——只表示「还有这一条」，不宣称关系。

    只扫**组织段**（`_residue_segments`，与第 2/3 条同一分段口径）：Claim 段逐字就是 Claim 文本，
    那里的「连接语」本来就来自材料，无需再判。跨度定位不成立时返回空——那种句子已在判据 a 被拒。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (claim_texts or ()))
    spans = _claim_spans(body, texts)
    if spans is None:
        return ()
    supported = "\n".join(texts)
    out: list[tuple[str, str]] = []
    for segment in _residue_segments(body, spans):
        for _pos, connector in relation_connector_hits(segment):
            if connector not in supported and (connector, segment) not in out:
                out.append((connector, segment))
    return tuple(out)


def unattributed_self_description(text: str, claim_texts: "Sequence[str]",
                                  claim_ids: "Sequence[str]"
                                  ) -> "tuple[tuple[str, str], ...]":
    """第 11 条的**唯一**定位实现：返回未带归属的发行人自述 `(claim_id, 短语)`。

    判据：某条声明 Claim 的文本里出现 `ISSUER_SELF_DESCRIPTION_PHRASES` 的短语时，该 Claim 在句子里
    **紧前的那一个组织段**（`_residue_segments` 的第 i 段，i = 该 Claim 在声明序里的下标）必须含
    `ATTRIBUTION_MARKERS` 里的至少一个短语。归属语可以由组织器自行写出——它只表明「这句话是发行
    人自己说的」，不引入任何数字、期间、主体或结论。

    「紧前那一段」而不是「整句任意位置」：归所属的是**它自己**那条断言，附着在别处的归属语并不
    说明这句话的出处。跨度定位不成立时返回空（同第 10 条）。
    """
    body = str(text or "")
    texts = tuple(str(t or "") for t in (claim_texts or ()))
    ids = tuple(str(c or "") for c in (claim_ids or ()))
    if len(ids) != len(texts):
        return ()
    spans = _claim_spans(body, texts)
    if spans is None:
        return ()
    segments = _residue_segments(body, spans)
    out: list[tuple[str, str]] = []
    for index, (claim_id, claim_text) in enumerate(zip(ids, texts)):
        for phrase in issuer_self_description_hits(claim_text):
            segment = segments[index] if index < len(segments) else ""
            if not attribution_hits(segment) and (claim_id, phrase) not in out:
                out.append((claim_id, phrase))
    return tuple(out)


def _verify_composed_sentence_organization(*, sentence: Any,
                                           authorized: Sequence[str]) -> None:
    """第 8 条：composed 句必须**真被组织过**，且声明的 Claim 必须真的在句子里（§七 1）。

    五条机械判据（判据的**唯一**实现在 `composed_organization_defect`，本条只负责把它返回的
    封闭缺陷码翻译成 fail-closed 的错误文本）：

    a. **绑定一致**：句子声明的每条 Claim 文本必须按声明顺序在句子文本里逐字出现。声明的
       Claim 不在正文里 = 句子把支撑挂在它没有呈现的事实上（反之，正文里的高风险表面必须来自
       这同一批 Claim，那由第 2 条判）。
    b. **确实组织过**（只在声明**多条** Claim 时才判）：把这几条 Claim 文本剔除后，剩下的组织
       成分必须至少含一个汉字。剔除后只剩标点/空白，说明这句话就是 Claim 文本的拼接
       （`A；B。`、`A。B。`、`A，B，C。`）——组织器一个字都没贡献。把 N 条 Claim 首尾相接
       **不是**「组织成一个自然段」，它只是把机械渲染换了个句式，却让机器门看起来「有多 Claim
       自然句」。
    c. **不承载大量互不组织的 Claim**：一句最多 `MAX_COMPOSED_CLAIMS_PER_SENTENCE` 条。
    d. **一句就是一个句子**：句末标点至多出现在末尾，句中不得再有（`SENTENCE_TERMINATORS`）。
       把两条各自带句号的 Claim 首尾相接（`A。同时B。`）在文本层就是**两个句子**；它只是把
       `A；B。` 这种机械拼接换成了「像自然句」的接缝，却能被「句子里有几条 Claim」这类计数
       当成一条多 Claim 自然句，于是机器门看到的是「组织过了」，读者看到的是两个断句。
    e. **接缝必须有分隔标点**（`nrules-12`）：相邻两条 Claim 文本之间那段组织成分的第一个字符
       必须是 `SEAM_LEAD_PUNCTUATION` 里的标点，连接语只能落在它**后面**。`A此外，B` 与
       `A，此外，B` 只差一个字符，但读者看到的是一个病句与一个正常句；而 a/b/d/c 四条判据在
       这两个文本上给出**同一个**答案（Claim 都在场、都有组织成分、都没有句中句末标点、都没
       超过条数上界）。这一条判的是**接缝的位置**，不是「用了什么词」：把连接语限制在词表里
       不能拦住把句首连接语塞进句中这个动作，而那个动作正是「正文不像正文」的主要来源。

    判据 b/d/e 的前提不同：b 说的是「组织成分不能为零」，d 说的是「句子不能被内部句末标点切成
    两句」，e 说的是「接缝上有分隔标点」。判据 b/e 的前提都是「有东西可组织」：只声明**一条**
    Claim 的句子没有「拼接」这个动作——它要么逐字呈现了那条 Claim（判据 a），要么没有，不存在
    「贡献了多少组织语」「接缝落在哪」这回事。把 b/e 套到单 Claim 句上，等于要求组织器为一条
    Claim 硬凑连接语，那不是本条要拦的对象。
    **一节是否真的被组织起来**由验收门（「至少一个多 Claim 自然段」）判定，不在这里替它下结论。

    **本判据一字未改**（⑥ 只加了补救，没有放宽判据）：`ng-9` 与 `ng-8` 在「什么样的 composed
    句通过」上完全一致——同一份正文在两侧得到同一个答案。有界定向重组织发生在**门前之外，
    但门内之前**：它产出的是新的正文，那份新正文仍要整份过本条。
    """
    count = len(tuple(authorized))
    text = str(sentence.text or "")
    defect = composed_organization_defect(text=text, authorized_texts=authorized)
    if defect is None:
        return
    if defect == COMPOSED_DEFECT_NOT_PRESENT_IN_ORDER:
        raise NarrativeSchemaError(
            f"composed 句子 {sentence.sentence_id} 声明的 Claim 文本没有按序出现在正文里："
            f"声明的 Claim 共 {count} 条"
            "（句子必须真的呈现它声明支撑的事实；绑定与正文不得各说各话）")
    if defect == COMPOSED_DEFECT_MECHANICAL_JOIN:
        residue = _organizing_residue(text, tuple(authorized))
        raise NarrativeSchemaError(
            f"composed 句子 {sentence.sentence_id} 只是它声明 Claim 文本的机械拼接："
            f"剔除 Claim 文本后不含任何组织成分（剩下 {residue!r}）"
            f"——把多条 Claim 用标点首尾相接不构成「多 Claim 自然句」")
    if defect == COMPOSED_DEFECT_INTERNAL_TERMINATOR:
        raise NarrativeSchemaError(
            f"composed 句子 {sentence.sentence_id} 含**句中**句末标点，实际是两个句子首尾相接"
            f"（{text!r}）：句末标点至多出现在末尾；"
            "把两条各自的完整句子用一个衔接语接起来不构成「多 Claim 自然句」，"
            "而是把断句伪装成了自然句（要覆盖更多 Claim 就多写一句，或让表格承载）")
    if defect == COMPOSED_DEFECT_UNSEPARATED_SEAM:
        texts = tuple(authorized)
        spans = _claim_spans(text, texts)
        seams = unseparated_seam_positions(text, texts)
        shown = [text[spans[pos][1]:spans[pos + 1][0]][:12] for pos in seams]
        raise NarrativeSchemaError(
            f"composed 句子 {sentence.sentence_id} 的接缝没有分隔标点起头（第 "
            f"{[pos + 1 for pos in seams]} 个接缝，实测 {shown}）：两条 Claim 文本之间必须先落"
            f"一个 {SEAM_LEAD_PUNCTUATION!r} 里的标点，连接语只能落在它后面。"
            "把句首连接语（「此外，」「同时，」…）塞在两个子句中间，读者读到的是一句病句"
            "（「…研发、生产、销售此外，公司产品…」）——它既不是机械拼接，也不是双句相接，"
            "因此前面几条判据都拦不住它")
    raise NarrativeSchemaError(
        f"composed 句子 {sentence.sentence_id} 承载了 {len(tuple(sentence.claim_ids))} 条 "
        f"Claim，超过一句最多 {MAX_COMPOSED_CLAIMS_PER_SENTENCE} 条的上界：一句塞入大量"
        "互不组织的 Claim 不是组织（要多覆盖几条，就多写一句，或让表格承载）")


def _verify_natural_sentence_fidelity(*, sentence: Any,
                                      claims: Sequence[Any]) -> None:
    """第 8 条（`natural` 模式）：**唯一**的机制判据是保真核对（`ng-14`，`nrules-15`）。

    这是本门**第一次**在某个句子类别上把「逐字等于 Claim 文本」换成别的判据，因此必须把边界
    写清楚，不允许含糊：

    * **换掉的是什么**：`natural` 句的文本不再等于它的 Claim 文本，句子可以换语序、合并同义
      衔接、把两条 Claim 写成一句——这正是「自然改写」本来的意思，也是本模式存在的理由。
    * **没换的是什么**：第 2/3 条（不得出现声明 Claim 之外的高风险表面）对 `natural` 原样适用；
      第 1、6、7、9、10、11、12 条同样原样适用。**没有**任何一处 fail-closed 被放宽。
    * **新增的是什么**：`verify_sentence_fidelity` 的「不得丢」方向——声明的 Claim 里凡属
      **断言关键**的表面（数字 / 含糊期间 / 否定与状态标记 / 因果关系 / 实体头部名词 / 范围与
      结论强度语）必须逐字仍在句子里。少了就是换了一次断言：把 `129,641,258 千元` 写成
      `12.96 亿元`、把 `未发生` 写成 `发生`、把 `下降` 写成 `下降但主要由于` 都会在这一条上被拒，
      而第 2/3 条只拦得住**添**、拦不住**减**。
    * **本函数不做、也不声称做的事**：它不判断 «这条 Claim 是否蕴含材料»（那是 Claim 级蕴含门
      `cer-*` 的事）；它不读材料正文；它不写任何对象、不产生任何决定身份；它**不**核对引用
      （引用轴在门内由第 6 条承担，本条刻意不重复一遍）。`verify_sentence_fidelity` 自己具备引用
      核对能力，那是给「最终句读回」这类**没有**跑过第 6 条的调用方用的。
    """
    report = verify_sentence_fidelity(text=sentence.text, claims=tuple(claims or ()))
    if report.ok:
        return
    detail: list[str] = []
    if report.added_surfaces:
        detail.append(f"句子含声明 Claim 里没有的高风险表面 {list(report.added_surfaces)}")
    if report.dropped_surfaces:
        detail.append("声明 Claim 的断言关键表面在句子里不见了 "
                      f"{[(cid, s) for cid, s in report.dropped_surfaces][:6]}")
    if report.added_scope_or_strength:
        detail.append("句子的范围 / 强度 / 因果语不在任何声明 Claim 文本里 "
                      f"{[(m, seg[:12]) for m, seg in report.added_scope_or_strength][:6]}")
    if report.stray_citations:
        detail.append(f"句子声明了不由本句 Claim 派生的引用 {list(report.stray_citations)[:6]}")
    raise NarrativeSchemaError(
        f"natural 句子 {sentence.sentence_id} 的改写越过了保真边界（缺陷码 "
        f"{list(report.defects())}）：" + "；".join(detail)
        + f"。保真结论指纹 {report.fidelity_digest[:12]}…（正文或绑定任一改动都会换指纹，"
          "因此这条结论不能移植到改写后的句子）。自然改写可以换语序、可以合并句子，但不得改变"
          "主体 / 数量 / 期间 / 否定 / 范围 / 因果 / 结论，也不得新增声明 Claim 之外的硬事实")


def verify_section_narrative(*, narrative: SectionNarrative, claims: Sequence[Any],
                             accepted_context_binding_ids: Iterable[str] = (),
                             dispositions: Sequence[ClaimNarrativeDisposition] = ()) -> None:
    """门后 final Narrative 的**确定性**核验（narr-6；九条判据的唯一实现）。

    §三 G 把两条**守恒/限定**判据加进同一个实现（调用方：自然组织器发出前自检、组装器读回后
    复算、章节评估器的规则侧、Store 读回——四处共用，口径不允许分叉）：

    6. **selected-material limitation**（句级）：句子声明的 `citation_ids` 必须**逐条**能由它
       自己声明的 Claim 派生（`claim_citation_ids`）。正文引用一个本节 Claim 从未给出的来源，
       等于让读者以为这段话有据可查，而那条依据根本不在本节的选中材料集里。
    7. **cross-topic/段落守恒**：段落 `topic_ids` 必须等于它真正引用的 Claim 的 topic 首次出现
       序列。段落不得把另一个 topic 的事实表面挂在本 topic 名下，也不得声明一个它没用的 topic。
    8. **句子确实被组织过**——按句类分派，这是**唯一**按句类分叉的一条判据（`ng-14`）：
       * `composed`（§七 1）：句子声明的 Claim 文本必须按序逐字出现在句子文本里；声明**多条**
         Claim 时，剔除这些 Claim 文本后剩下的组织成分必须至少含一个汉字（只剩标点 = 机械拼接）；
         句子不得含**句中**句末标点（`A。同时B。` 是两句相接）；相邻 Claim 之间的接缝必须以
         `SEAM_LEAD_PUNCTUATION` 里的分隔标点起头（`A此外，B` 是病句、`A，此外，B` 才是句子）；
         一句最多承载 `MAX_COMPOSED_CLAIMS_PER_SENTENCE` 条 Claim。五条都是机械判据
         （见 `_verify_composed_sentence_organization`）：`ng-6` 只看「句子声明了几条 Claim」，
         于是「用 `；` 把 Claim 首尾相接」与「真的组织成一句话」在门下一模一样；`ng-10` 之前的
         四条判据在「连接语写在接缝哪个位置」上也一模一样。
       * `natural`（M930-3 指令 E 第 3 项）：**不要求**逐字——要求的是**保真**（见
         `_verify_natural_sentence_fidelity` 与 `verify_sentence_fidelity`）：数字 / 含糊期间 /
         否定与状态标记 / 因果关系 / 实体头部名词 / 范围与结论强度语必须逐字仍在它声明的 Claim
         文本里。**注意方向**：这一支**不放宽**第 2/3 条，因此「不得新增」照旧；它换来的是
         「不必逐字」，同时**新增**了「不得丢」——那一侧是 `composed` 路径本来就免费拥有的
         （它的正文逐字包含 Claim 文本，丢字在结构上不可能）。
    9. **不得自称系统的检索 / 核验**（⑥，`ng-9`）：句子文本里出现 `SYSTEM_PROVENANCE_PHRASES`
       的任何一个短语时，该短语必须能在**它自己声明的 Claim 文本**里逐字找到。年报转述的行业
       数据（以及任何来自文档材料的 Claim）都不是系统独立联网核验的结果：材料写着的照转不误
       （Claim 文本逐字在场），材料没写而正文写了「本系统已独立联网核验」即拒。**不进**
       `HIGH_RISK_SURFACE_MARKERS`：它不参与写入侧的候选授权面（材料原文里的这些词不在候选
       阶段被判高风险），只在**正文**这一面生效。
    10. **关系性连接语必须有材料支持**（`ng-12`，`nrules-13`）：句子文本的**组织段**里出现
       `RELATION_ASSERTING_CONNECTORS`（`其中` / `另一方面` / `在此基础上` / `综上`）之一时，该
       连接语必须能在**它自己声明的 Claim 文本**里逐字找到——即材料自己说了这个关系。判据与第 9
       条同形、同理由：这四种连接语都断言「两条断言之间存在某种关系」（从属 / 对照 / 递进 /
       归纳），材料没说的关系由组织语补上，就是把并列升级成推理。合规替代写法是中性并列
       （`此外，` / `同时，`）。
    11. **发行人自述必须有归属**（`ng-12`，`nrules-13`）：某条声明 Claim 的文本里含
       `ISSUER_SELF_DESCRIPTION_PHRASES`（`一流` / `核心优势` / `前瞻性` …）时，该 Claim 在句子里
       **紧前的那一个组织段**必须含 `ATTRIBUTION_MARKERS` 之一。这些评价语逐字就在材料里，第 2/3
       条必然放行；但它们是**发行人对自己**的评价，照转而不标明出处，读者会把发行人自述读成授信
       分析者的结论。**不进** `HIGH_RISK_SURFACE_MARKERS`（同第 9 条的理由）。

    第 10/11 条都**不进** `COMPOSED_SENTENCE_DEFECTS`：那份封闭缺陷码集是给「有界定向重组织」
    消费的，而重组织只修**组织形态**、绝不改写正文文本；关系词与归属语只能由组织器自己写对。

    第 2/3 条对 composed 句走 `unauthorized_surfaces_within_claims`（边界感知）：判据不变，
    只是表面改为在单个**组织段**内合成，不再跨接缝凭空造出不存在的 token。

    **范围边界**（如实声明，不含糊）：`tables` 不参与第 7 条——表格行的 `citation_ids` 语义
    （非事实行/单位/期间行）尚未在 wire 层收敛到「必须由该行 Claim 派生」，此刻强行套用会与
    既有 wire 注释冲突。因此本函数对表格只做既有的行/表并集一致性（构造期）与单元格表面核验，
    不主张已实现表格级守恒。

    任一条不成立即抛 `NarrativeSchemaError`（fail-closed，不做部分接受）。
    """
    if not isinstance(narrative, SectionNarrative):
        raise NarrativeSchemaError(
            f"verify_section_narrative 只接受 SectionNarrative，得到 {type(narrative).__name__}")
    claim_by_id: dict[str, Any] = {}
    for c in (claims or ()):
        cid = str(getattr(c, "claim_id", "") or "")
        if not cid:
            raise NarrativeSchemaError("verify_section_narrative 的 Claim 集含空 claim_id")
        claim_by_id[cid] = c
    claim_text = {cid: str(getattr(c, "text", "") or "") for cid, c in claim_by_id.items()}
    section_id = narrative.section_id
    accepted_context = set(str(x) for x in (accepted_context_binding_ids or ()))
    selected: list[str] = []
    for paragraph in narrative.paragraphs:
        used_topics: list[str] = []
        for sentence in paragraph.sentences:
            if sentence.sentence_kind not in ("factual", "composed", "natural"):
                continue
            # 1) 引用的 Claim 必须存在且当前
            unknown = [cid for cid in sentence.claim_ids if cid not in claim_text]
            if unknown:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 引用了本节未定稿的 Claim：{unknown[:6]}"
                    "（引用必须落在**本次**定稿集上；引用上一状态或已被替换的 Claim 一律拒）")
            selected.extend(sentence.claim_ids)
            # 2+3) 高风险表面必须逐字来自它自己声明的 Claim 文本；context 不参与授权
            authorized = [claim_text[cid] for cid in sentence.claim_ids]
            if sentence.sentence_kind == "composed":
                # 组合句按**文本分段**核验：整句一次扫描会在「Claim 段 | 组织段」的接缝上
                # 合成出两个声明 Claim 里都不存在的表面（主体名是「后缀 + 向前回扫」拼出来的），
                # 让同一份合规正文时过时不过。分段之后口径不变：组织段里合成出的表面仍必须
                # 逐字出现在某条声明 Claim 文本里，数字/主体/否定/状态/表格关系/因果门一个不少。
                unauthorized = unauthorized_surfaces_within_claims(sentence.text, authorized)
            else:
                # `factual` 与 `natural` 走这一支：两者的句子里都**没有**可用来分段的 Claim 逐字
                # 文本（`natural` 是改写句，Claim 文本根本不逐字在场），因此「按 Claim 文本切段、
                # 在段内合成表面」这套切法在它们身上没有意义，只能整句扫描。
                unauthorized = unauthorized_surfaces(sentence.text, authorized)
            if unauthorized:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id}（{sentence.sentence_kind}）含它声明的 Claim "
                    f"文本里没有的高风险表面：{list(unauthorized)}"
                    "（数字/主体/期间/否定/状态/表格关系都属高风险硬事实，"
                    "context 绑定不授权任何事实表面）")
            # 4) context 只承载背景与衔接，且必须是本节**已接受**的绑定
            if sentence.context_binding_ids:
                if sentence.sentence_kind == "factual":
                    raise NarrativeSchemaError(
                        f"factual 句子 {sentence.sentence_id} 携带 context 绑定："
                        "逐字事实句的支撑只能来自它引用的 Claim")
                stray = sorted(set(sentence.context_binding_ids) - accepted_context)
                if stray:
                    raise NarrativeSchemaError(
                        f"句子 {sentence.sentence_id} 使用了未接受的 context 绑定 {stray}"
                        "（context 依据必须来自本节的已接受集合）")
            # 6) selected-material limitation：本句声明的引用必须逐条由它自己声明的 Claim 派生
            authorized_citations: set[str] = set()
            for cid in sentence.claim_ids:
                authorized_citations.update(claim_citation_ids(claim_by_id[cid]))
            stray_citations = sorted(set(sentence.citation_ids) - authorized_citations)
            if stray_citations:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 声明的引用 {stray_citations[:6]} 不是它绑定的 "
                    f"Claim 给出的引用（本句 Claim 授权的引用共 {len(authorized_citations)} 条）"
                    "：引用只能来自这条支撑边自己的来源，不得事后补引用、也不得引用本节未选中的材料")
            # 7) cross-topic 守恒：本段用到的 topic 由它真正引用的 Claim 派生（首次出现序）
            for cid in sentence.claim_ids:
                topic = claim_topic_for_conservation(claim_by_id[cid])
                if topic not in used_topics:
                    used_topics.append(topic)
            # 8) composed 句必须真的被组织过（§七 1「多 Claim 自然句」不是拼接句）；
            #    natural 句（`ng-14`）走**保真核对**——见 `_verify_natural_sentence_fidelity`。
            if sentence.sentence_kind == "composed":
                _verify_composed_sentence_organization(
                    sentence=sentence, authorized=authorized)
            elif sentence.sentence_kind == "natural":
                _verify_natural_sentence_fidelity(
                    sentence=sentence,
                    claims=[claim_by_id[cid] for cid in sentence.claim_ids])
            # 9) 正文不得**替系统自报**一次它没有做过的检索 / 核验（⑥）：短语必须逐字来自本句
            #    声明的 Claim 文本（年报自己写着「核验」照转不误；材料没写而正文写了即拒）。
            #    与第 2/3 条同形、同理由：这不是文风，而是「这句话的依据是什么」。
            provenance = [hit for hit in system_provenance_hits(sentence.text)
                          if not any(hit in text for text in authorized)]
            if provenance:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 自称本系统做过检索 / 核验：{provenance}"
                    "，而它声明的 Claim 文本里没有这些字面（年报转述的数据不得写成"
                    "「系统已独立联网核验」；材料原文写着的照转不误）")
            # 10) 关系性连接语必须有材料支持（`ng-12`）：`另一方面` 断言对照、`在此基础上` 断言
            #     递进、`其中` 断言从属、`综上` 断言归纳——四种都是「两条断言之间有关系」的断言。
            #     材料自己没写这个关系，正文就是在替材料下判断。与第 9 条同形：连接语必须逐字
            #     出现在本句声明的 Claim 文本里；否则改用中性并列（`此外，` / `同时，`）。
            unsupported = unsupported_relation_connectors(sentence.text, authorized)
            if unsupported:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 用了没有材料支持的关系性连接语："
                    f"{[(c, seg[:12]) for c, seg in unsupported]}"
                    "——该连接语必须逐字出现在本句声明的 Claim 文本里（材料自己说了这个关系）；"
                    "材料没说的关系不得由组织语断言，请改用中性并列（`此外，` / `同时，`）")
            # 11) 发行人自述必须有归属（`ng-12`）：`一流` / `核心优势` / `前瞻性` 这类评价语
            #     逐字就在材料里（所以第 2/3 条必然放行），但它是**发行人对自己**的评价。照转而
            #     不标明出处，读者会把发行人自述读成授信分析者的结论。归属语不出现在该 Claim
            #     紧前的组织段里即拒。与第 9 条一样**不进** `HIGH_RISK_SURFACE_MARKERS`。
            unattributed = unattributed_self_description(
                sentence.text, authorized, sentence.claim_ids)
            if unattributed:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 把发行人自述写成了没有归属的结论："
                    f"{unattributed[:4]}"
                    "——这些评价语出自发行人自己的披露，正文必须在其紧前给出归属"
                    f"（{list(ATTRIBUTION_MARKERS)} 之一），否则读者会把它读成分析结论")
            # 12) 组织语不得把断言挪到当前时点（`ng-13`，O-12 在**组织**侧的落点）：`目前` /
            #     `仍` / `持续` 这类词不含任何数字与期间，因此第 2/3 条拦不住它——可它把一条断言
            #     从「材料说的那个时点」搬到了「现在」。只由同类较旧材料支撑的断言被这样一写，
            #     读者读到的是材料没说过的一件事。判据与第 9/10 条同形：措辞必须逐字出现在本句
            #     声明的 Claim 文本里；否则改用中性并列（`此外，` / `同时，`）。
            framing = unqualified_current_state_framing(sentence.text, authorized)
            if framing:
                raise NarrativeSchemaError(
                    f"句子 {sentence.sentence_id} 的组织语把断言挪到了当前时点："
                    f"{[(m, seg[:12]) for m, seg in framing]}"
                    "——这些当前式措辞必须逐字出现在本句声明的 Claim 文本里（材料自己就是这样"
                    "写的）；材料没写的时点不得由组织语改写。O-12：同类较旧材料只用于历史、变化"
                    "与冲突核对，正文不得把它静默写成当前状态")
        declared_topics = tuple(paragraph.topic_ids)
        if tuple(used_topics) != declared_topics:
            raise NarrativeSchemaError(
                f"段落 {paragraph.paragraph_id} 的 topic 归属与它真正引用的 Claim 不符："
                f"声明 {list(declared_topics)}，由 Claim 派生 {used_topics}"
                "（段落不得把另一 topic 的事实表面挂在本 topic 名下，也不得声明没有用到的 topic）")
    if dispositions:
        verify_claim_narrative_dispositions(
            claims, dispositions, section_id=section_id,
            draft_revision=narrative.draft_revision)
        omitted = {d.claim_id for d in dispositions if d.disposition == "omitted"}
        selected_set = set(selected)
        # 5) 去向与实际正文必须一致：声明 omitted 的不得真出现在正文里，反之亦然。
        contradiction = sorted(omitted & selected_set)
        if contradiction:
            raise NarrativeSchemaError(
                f"这些 Claim 的记号是 omitted，正文里却真的用到了：{contradiction[:6]}"
                "（去向是核验依据，不是可以自报的注释）")


def verify_section_narrative_claims_selected(*, narrative: SectionNarrative,
                                             claims: Sequence[Any],
                                             dispositions: Sequence[Any]) -> None:
    """`selected` 记号必须与正文真实引用一致（`verify_section_narrative` 的补充一侧）。

    `selected` 的 Claim 必须在正文里至少被引用一次：否则「记号说选进去了」与「正文里找不到」
    会同时成立，而下游只读记号、不读正文。
    """
    used = set()
    for paragraph in narrative.paragraphs:
        for sentence in paragraph.sentences:
            used |= set(sentence.claim_ids)
    claimed_selected = {str(d.claim_id) for d in (dispositions or ())
                        if str(getattr(d, "disposition", "")) == "selected"}
    missing = sorted(claimed_selected - used)
    if missing:
        raise NarrativeSchemaError(
            f"这些 Claim 的记号是 selected，正文里却一次都没被引用：{missing[:6]}")


#: 候选 `fact_type` → 定稿 `SectionClaim.claim_type` 的**封闭**映射（**唯一**实现）。
#: `fact`/`inference` 逐字保留；路径 B 材料派生的描述性原子没有权威事实类型，定稿为 `fact`
#: （它仍是被接受的**事实性**支撑，不是新类型，也不放宽任何必需事实/缺口规则）。
CLAIM_TYPE_BY_FACT_TYPE = {"fact": "fact", "inference": "inference", "descriptive": "fact"}


def claim_type_for_fact_type(fact_type: Any, what: str) -> str:
    """候选事实类型 → Claim 类型的**唯一**确定性映射；未知值 fail-closed（不猜通用值）。"""
    key = str(fact_type or "")
    if key not in CLAIM_TYPE_BY_FACT_TYPE:
        raise NarrativeSchemaError(
            f"{what} 的 fact_type={key!r} 没有到 claim_type 的确定性映射"
            f"（只认 {sorted(CLAIM_TYPE_BY_FACT_TYPE)}）；不得猜一个通用 Claim 类型")
    return CLAIM_TYPE_BY_FACT_TYPE[key]


def claim_citation_refs(authority_input: Any, bindings: Sequence[Any],
                        *, what: str) -> tuple[Any, ...]:
    """一组 factual accepted binding → 该 Claim 的权威 citation 引用（**唯一**派生实现）。

    路径 A：引用逐字来自 binding 指向的**权威事实自己**给出的引用
    （`authoritative_citation_refs`）；路径 B：引用来自该 exact material 自己
    （`material_citation_ref`，含 payload/locator）。两类都拒绝**事后补引用** —— 引用只能是
    这条支撑边自己的来源给的。

    返回按引用身份去重（保序：绑定顺序 → 权威引用顺序）的元组；同一身份出现两次只保留一条。
    """
    import sections.schema as _SS

    entries = authority_fact_entries(authority_input)
    out: list[Any] = []
    seen: set[str] = set()

    def _add(ref: Any) -> None:
        identity = _SS.citation_identity(ref)
        if identity not in seen:
            seen.add(identity)
            out.append(ref)

    for binding in tuple(bindings or ()):
        coord = binding_fact_key(binding)
        if coord:
            key = coord[2]
            fact = entries.get(coord)
            if fact is None:
                raise NarrativeSchemaError(
                    f"{what} 的 accepted binding "
                    f"{getattr(binding, 'accepted_support_binding_id', '?')!r} 指向的权威事实 "
                    f"{coord} 不在本 authority 输入内（支撑边不得指向别的章节的事实）")
            refs = authoritative_citation_refs(str(binding.authority_kind), fact)
            if not refs:
                raise NarrativeSchemaError(
                    f"{what} 的权威事实 {key} 没有可派生的 citation_refs："
                    "该支撑边无法回查来源，不得成为定稿 Claim 的来源")
            for ref in refs:
                _add(ref)
            continue
        material_id = str(getattr(binding, "material_id", "") or "")
        if not material_id:
            raise NarrativeSchemaError(
                f"{what} 的 accepted binding "
                f"{getattr(binding, 'accepted_support_binding_id', '?')!r} 既没有权威 fact 身份"
                "也没有 material：支撑边无法回查任何来源")
        for ref in material_citation_ref(authority_input, str(binding.authority_container_id),
                                        material_id):
            _add(ref)
    if not out:
        raise NarrativeSchemaError(
            f"{what} 的 factual accepted 集没有产生任何 citation：无来源的断言不得定稿")
    return tuple(out)


def _claim_topic_id(bindings: Sequence[Any], *, fact_table: Mapping[Any, Any],
                    pack_topics: Mapping[str, str], what: str) -> str:
    """一条 Claim 的 topic 归属：只从**权威侧**取，归属不唯一即 fail-closed。

    路径 A 的事实主题来自权威事实条目（`AuthorityFactEntry.topic_id`，财务/附注/外部事实同表
    同口径）；路径 B 的材料主题来自它所属 Pack 自己的 `topic_id`。同一候选的权威事实分属不同
    topic 时**不猜**第一个：一条 Claim 只能有一个 topic 归属。
    """
    topics: set[str] = set()
    for binding in tuple(bindings or ()):
        coord = binding_fact_key(binding)
        key = coord[2] if coord else ""
        if coord:
            entry = fact_table.get(coord)
            if entry is None:
                raise NarrativeSchemaError(
                    f"{what} 的 accepted binding 指向的权威事实坐标 {coord} 不在本 authority "
                    "事实目录内：不得据未知坐标归属 topic")
            topic = str(getattr(entry, "topic_id", "") or "")
        else:
            container = str(getattr(binding, "authority_container_id", "") or "")
            topic = str(pack_topics.get(container, "") or "")
            if not topic:
                raise NarrativeSchemaError(
                    f"{what} 的材料支撑边容器 {container!r} 没有对应 Pack topic："
                    "路径 B 的归属只能来自它所属 Pack")
        if not topic:
            raise NarrativeSchemaError(f"{what} 的权威事实 {key} 没有 topic 归属")
        topics.add(topic)
    if len(topics) != 1:
        raise NarrativeSchemaError(
            f"{what} 的权威支撑跨了多个 topic {sorted(topics)}："
            "一条 Claim 只能有一个 topic 归属（不得挑一个当归属）")
    return topics.pop()


def finalize_section_claims(*, task: Any, authority: Any, draft: SectionDraft,
                            acceptance: Any) -> tuple[Any, ...]:
    """门**后** Claim 定稿的**唯一**确定性构造器（§0.13：Claim 在两道门之后形成）。

    P15 用它定稿；组装器用**同一实现**在当前 draft + accepted 集上重算并要求逐字段一致，
    因此调用方既不能凭空加一条没有支撑的 Claim，也不能把被拒候选写进正文。

    规则（身份全部来自已有对象，不新增任何来源）：
    1. 只有**被接受的 factual 候选**产生 Claim：`support_semantics="factual"` 的 accepted
       binding 才计入；context（门前叙述单元）永不产生 Claim，也不进 entailment；
    2. 定稿**不改写命题**：`text` 逐字等于候选，`claim_type` 由候选 `fact_type` 按
       `CLAIM_TYPE_BY_FACT_TYPE` 得到，`claim_candidate_id` / `claim_candidate_revision`
       单向回指该 candidate revision；
    3. `accepted_binding_ids` 是该 candidate revision 的**完整** factual accepted 集
       （不是其中一条，也不含别的候选的边）；
    4. `topic_id` / `question_ids` 只从权威侧与任务侧取（`claim_topic_id` +
       `pack_writer._question_ids_for_topic` 的唯一口径）；
    5. 输出顺序 = `draft.claim_candidates` 的顺序（定稿顺序不另起一套排序）。
    """
    from sections import pack_writer as _PW
    import sections.schema as _SS

    if not isinstance(draft, SectionDraft):
        raise NarrativeSchemaError("finalize_section_claims 的 draft 必须是门前 SectionDraft")
    ordered_ids = [str(c.candidate_id) for c in draft.claim_candidates]
    factual: dict[str, list[Any]] = {}
    for binding in tuple(getattr(acceptance, "accepted_bindings", ()) or ()):
        if str(getattr(binding, "support_semantics", "")) != "factual":
            continue
        subject_kind = str(getattr(binding, "binding_subject_kind", ""))
        if subject_kind != "claim_candidate":
            raise NarrativeSchemaError(
                f"factual accepted binding 的 subject 只能是 claim_candidate，得到 "
                f"{subject_kind!r}（context 支撑不得被定稿成 Claim）")
        factual.setdefault(str(binding.binding_subject_id), []).append(binding)
    unknown = sorted(set(factual) - set(ordered_ids))
    if unknown:
        raise NarrativeSchemaError(
            f"accepted factual subject {unknown[:4]} 不在本 draft 候选束内："
            "不得跳过候选直接定稿")

    scan = _PW.scan_authority(authority, task)
    fact_table = _PW._authority_fact_table(scan.facts)
    # 路径 B 的 topic 归属 = 该材料所属 Pack 自己的 topic（容器身份 → topic）。
    pack_topics = {str(pack.pack_id): str(pack.topic_id)
                   for pack in tuple(getattr(getattr(authority, "pack_set", None), "packs", ())
                                     or ())}

    claims: list[Any] = []
    for candidate_id in ordered_ids:
        mine = factual.get(candidate_id)
        if not mine:
            continue
        candidate = draft.candidate_for(candidate_id)
        what = f"候选 {candidate_id}"
        if candidate is None:
            raise NarrativeSchemaError(f"{what} 不在本 draft 候选束内（候选束身份漂移）")
        topic_id = _claim_topic_id(mine, fact_table=fact_table, pack_topics=pack_topics,
                                   what=what)
        question_ids = tuple(_PW._question_ids_for_topic(task, topic_id, what))
        refs = claim_citation_refs(authority, mine, what=what)
        binding_ids = tuple(sorted(str(b.accepted_support_binding_id) for b in mine))
        claim_type = claim_type_for_fact_type(candidate.fact_type, what)
        claim_id = _SS.derive_claim_id(
            claim_type, topic_id, question_ids, candidate.claim_text, refs,
            candidate_id, draft.draft_revision, binding_ids)
        claims.append(_SS.SectionClaim(
            claim_id=claim_id, schema_version=_SS.CLAIM_SCHEMA_VERSION,
            section_id=draft.section_id, topic_id=topic_id, question_ids=question_ids,
            text=candidate.claim_text, claim_type=claim_type, citation_refs=refs,
            claim_candidate_id=candidate_id, claim_candidate_revision=draft.draft_revision,
            accepted_binding_ids=binding_ids,
            as_of_date=str(getattr(authority, "report_as_of", "") or "") or None))
    return tuple(claims)


# ---------------------------------------------------------------------------
# SectionDraft
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionDraft:
    """门**前**候选束（§0.13 / §6.3）：current 的 `SectionDraft`（`narr-5`；§三 C 起
    final Narrative 的 wire 前进，本候选束的**字段集未变**，marker 随 narrative wire family
    一起前进，narr-4 记录只经各自的 legacy reader 只读回放）。

    它只承载「候选 / 草稿单元 / proposal / 材料处理去向是怎么组织的」，**不**承载任何门后
    身份。字段命名是**显式 stage-specific** 的：

    * `claim_candidates`（对象元组）＋ `claim_candidate_ids`（派生视图）；
    * `narrative_draft_units`（门前叙述单元）；
    * `proposed_support_refs`（门前 proposal）；
    * `material_manifest` / `material_dispositions`（exact manifest 与其处理去向）。

    本类**没有**（即类型层不可表达）：`section_result_id`、任何 `SectionClaim` ID、
    accepted-binding ID、任何决定 ID、最终 Narrative ID、`FollowUpNeed` 内容身份。因此
    「Draft 反向引用 Result / 引用未来决定 / 把 final Claim 当候选」都不可表达。

    旧名 `claim_ids` 承载**已定稿 Claim** 的语义**不得沿用**：narr-3 载荷只经
    `load_legacy_narrative_narr3_for_audit` 只读回放，current reader 一律拒绝。
    """

    schema_version: str
    draft_id: str
    #: 本候选束的 revision：`derive_draft_revision(...)` 的确定性输出。候选/单元/proposal
    #: 的 `draft_revision` 必须与它逐字相同（binding subject 的基数键就是它）。
    draft_revision: str
    task_id: str
    section_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    producer_kind: str
    writer_policy_version: str
    prompt_version: str
    model_policy: str
    authority_container_ids: tuple[str, ...]
    #: exact material-context manifest：本次写作消费的**精确**材料清单（available）。
    material_manifest: WriterMaterialManifest
    #: 每个 manifest 成员恰一条处理去向（processed/used/not_used）。
    material_dispositions: tuple[WriterMaterialProcessingDisposition, ...]
    claim_candidates: tuple[ClaimCandidate, ...]
    narrative_draft_units: tuple[NarrativeDraftUnit, ...]
    proposed_support_refs: tuple[ProposedSupportRef, ...]
    unresolved_ids: tuple[str, ...]
    unresolved_projections: tuple[dict, ...]
    coverage_summary: dict
    conflict_projections: tuple[dict, ...]
    not_found_projections: tuple[dict, ...]
    dependency_fingerprint: str
    created_at: str = ""
    #: 本节标题（§五：规范 Markdown 只在**唯一**渲染器里生成，标题由 scope 决定）。
    title: str = ""
    #: `section_version` 的两个**写入侧**输入（渲染器 / 规则版本）。它们必须随 draft 一起
    #: 留存：否则组装器无法从产物本身重算 `section_version`，只能去猜一个版本常量 —— 那
    #: 恰好就是「第二套口径」。
    writer_rules_version: str = ""
    writer_renderer_version: str = ""
    #: 这一束是**第几次**生成（1 = 首轮；被拒后重写的那一束是 2、3…）。它进入
    #: `derive_draft_revision(attempt=…)`，因此重写必是**新修订**：被拒的那一束与重写后的那一束
    #: 不可能共享修订。它不进 `identity_body`——`draft_revision` 已在其中且已含它，
    #: 而多写一个键会改掉**全部**历史 draft 的 `draft_id`（历史载荷只读回放会因此失配）。
    writer_attempt: int = 1
    #: 门前**自然草稿**（narr-7，§三 C 第 3 项）：材料驱动的可保留散文，逐单元登记「表达的是
    #: 哪些 manifest 成员」与「这段正文里的事实原子各自提交了哪个候选」。门后组织器以**它**
    #: 作为表达基础，而不是以一串 Claim 文本作为表达基础。
    #:
    #: **空元组** = 本节未走草稿路径（`narr-6` 及更早的载荷、组合句路径），此时本字段对身份与
    #: 校验都没有影响，老行为逐字节不变。非空时它进 `draft_revision`（见
    #: `derive_draft_revision(natural_prose_digest=…)`）也进闭合核对，因此「换了草稿」不是
    #: 「同一修订的两版正文」，而是**新修订**。
    #:
    #: 它**不进** `identity_body`，理由与 `writer_attempt` 逐字相同：多写一个键会改掉**全部**历史
    #: draft 的 `draft_id`。二者不同的是可核验性来源——`writer_attempt` 靠 `identity_body` 之外的
    #: 显式载荷留存，本字段的内容则由每个 `NaturalProseDraftUnit` 自己的内容寻址 id 保证（草稿
    #: 内容改一个字节，单元 id 就变，而单元 id 在 `claim_candidate`/`narrative_draft_unit` 之外
    #: 由闭合核对强制与候选对齐）。因此「载荷里的草稿」与「draft_id 所指的那一束」不会脱钩。
    natural_prose_draft: tuple[NaturalProseDraftUnit, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"SectionDraft.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        if (not isinstance(self.writer_attempt, int) or isinstance(self.writer_attempt, bool)
                or self.writer_attempt < 1):
            raise NarrativeSchemaError(
                f"SectionDraft.writer_attempt 必须是 >= 1 的整数，实为 {self.writer_attempt!r}")
        for name in ("task_id", "section_id", "producer_kind",
                     "writer_policy_version", "prompt_version", "model_policy"):
            _require_nonempty(getattr(self, name), f"SectionDraft.{name}")
        for name in ("authority_container_ids", "unresolved_ids"):
            object.__setattr__(self, name,
                               _str_tuple(getattr(self, name), f"SectionDraft.{name}"))
        object.__setattr__(self, "title", self.title if isinstance(self.title, str) else "")
        for name in ("claim_candidates", "narrative_draft_units", "proposed_support_refs",
                     "material_dispositions", "natural_prose_draft"):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        if not isinstance(self.material_manifest, WriterMaterialManifest):
            raise NarrativeSchemaError(
                "SectionDraft.material_manifest 必须是 WriterMaterialManifest："
                "候选束必须携带 exact material manifest（缺清单即不得写）")
        # §七 4：缺口/覆盖/冲突四类投影是**权威根出的状态**，「正文没变但缺口变了」同样是
        # 内容变化，因此必须进入 draft 身份（不得只放在 to_dict 的展示载荷里）。
        for name in ("unresolved_projections", "conflict_projections", "not_found_projections"):
            object.__setattr__(self, name, _projection_list(getattr(self, name),
                                                            f"SectionDraft.{name}"))
        if self.coverage_summary is None:
            object.__setattr__(self, "coverage_summary", {})
        if not isinstance(self.coverage_summary, dict):
            raise NarrativeSchemaError("SectionDraft.coverage_summary 必须是对象")
        object.__setattr__(self, "coverage_summary", _canon_jsonable(self.coverage_summary))
        expected_revision = derive_draft_revision(
            task_id=self.task_id, section_id=self.section_id, company_id=self.company_id,
            report_as_of=self.report_as_of, contract_version=self.contract_version,
            contract_fingerprint=self.contract_fingerprint,
            writer_policy_version=self.writer_policy_version,
            prompt_version=self.prompt_version, model_policy=self.model_policy,
            manifest_id=self.material_manifest.manifest_id,
            manifest_fingerprint=self.material_manifest.fingerprint(),
            attempt=self.writer_attempt,
            natural_prose_digest=natural_prose_draft_digest(self.natural_prose_draft))
        if self.draft_revision != expected_revision:
            raise NarrativeSchemaError(
                f"SectionDraft.draft_revision 与 Writer 输入/manifest 不符：声明 "
                f"{self.draft_revision!r}，应为 {expected_revision!r}")

        candidates = self.claim_candidates
        units = self.narrative_draft_units
        proposals = self.proposed_support_refs
        candidate_ids = [c.candidate_id for c in candidates]
        unit_ids = [u.draft_unit_id for u in units]
        proposal_ids = [p.proposed_support_id for p in proposals]
        if not candidate_ids and not unit_ids and not proposals and not self.unresolved_ids:
            raise NarrativeSchemaError(
                "SectionDraft 既无候选/门前叙述单元/proposal 又无 unresolved_ids："
                "空 draft 不得静默通过")
        for label, ids in (("claim_candidates", candidate_ids),
                           ("narrative_draft_units", unit_ids),
                           ("proposed_support_refs", proposal_ids)):
            if len(set(ids)) != len(ids):
                raise NarrativeSchemaError(f"SectionDraft.{label} 含重复身份")
        if set(candidate_ids) & set(unit_ids):
            raise NarrativeSchemaError(
                "候选与门前叙述单元共用身份命名空间（两者是不同的 binding subject kind）")

        # subject 归属：每个候选/单元都必须属于本节、属于同一 draft revision。
        for candidate in candidates:
            if candidate.draft_revision != self.draft_revision \
                    or candidate.section_id != self.section_id:
                raise NarrativeSchemaError(
                    f"候选 {candidate.candidate_id} 的 draft_revision/section_id 与本节不符"
                    "（不得跨 revision 拼装候选）")
            if (candidate.task_id, candidate.company_id, candidate.report_as_of,
                    candidate.contract_version, candidate.contract_fingerprint) != (
                    self.task_id, self.company_id, self.report_as_of,
                    self.contract_version, self.contract_fingerprint):
                raise NarrativeSchemaError(
                    f"候选 {candidate.candidate_id} 的 task/company/时点/Contract 与本节不符")
        for pos, unit in enumerate(units):
            if unit.draft_revision != self.draft_revision or unit.section_id != self.section_id:
                raise NarrativeSchemaError(
                    f"门前叙述单元 {unit.draft_unit_id} 的 draft_revision/section_id 与本节不符")
            if unit.index != pos:
                raise NarrativeSchemaError(
                    f"门前叙述单元 {unit.draft_unit_id} 的 index 与顺序不符（不得乱序拼装）")

        # 自然草稿（narr-7）的闭合核对：只在草稿非空时生效，`narr-6` 及更早一字不变。
        for prose in self.natural_prose_draft:
            if not isinstance(prose, NaturalProseDraftUnit):
                raise NarrativeSchemaError(
                    "SectionDraft.natural_prose_draft 只能由 NaturalProseDraftUnit 构成，"
                    f"得到 {type(prose).__name__}")
        prose_ids = [u.prose_unit_id for u in self.natural_prose_draft]
        if len(set(prose_ids)) != len(prose_ids):
            raise NarrativeSchemaError("SectionDraft.natural_prose_draft 含重复身份")
        if set(prose_ids) & set(candidate_ids + unit_ids):
            raise NarrativeSchemaError(
                "自然草稿单元与候选/门前叙述单元共用身份命名空间")
        # `npr-1`：归属核对要逐 occurrence 对着**该候选自己的支撑提案**核（材料 ID / 文档两轴），
        # 因此这里把 proposal 面与 manifest 一并交出去——两者都是本对象已有的字段，不新造核对面。
        prose_problems = validate_natural_prose_mapping(
            natural_prose_draft=self.natural_prose_draft, claim_candidates=candidates,
            member_refs=self.material_manifest.member_refs(), section_id=self.section_id,
            draft_revision=self.draft_revision,
            proposed_support_refs=proposals, material_manifest=self.material_manifest)
        if prose_problems:
            raise NarrativeSchemaError(
                "SectionDraft 自然草稿不闭合：" + "；".join(prose_problems))

        subject_kind = {cid: "claim_candidate" for cid in candidate_ids}
        subject_kind.update({uid: "narrative_draft_unit" for uid in unit_ids})
        for proposal in proposals:
            if proposal.draft_revision != self.draft_revision:
                raise NarrativeSchemaError(
                    f"proposal {proposal.proposed_support_id} 的 draft_revision 与本节不符")
            if proposal.manifest_id != self.material_manifest.manifest_id \
                    or proposal.manifest_fingerprint != self.material_manifest.fingerprint():
                raise NarrativeSchemaError(
                    f"proposal {proposal.proposed_support_id} 未绑定本 draft 的 manifest "
                    "identity（不得引用别的清单）")
            if proposal.binding_subject_id not in subject_kind:
                raise NarrativeSchemaError(
                    f"proposal {proposal.proposed_support_id} 指向本节之外的 subject "
                    f"{proposal.binding_subject_id!r}（伪 proposal 已拒）")
            if proposal.binding_subject_kind != subject_kind[proposal.binding_subject_id]:
                raise NarrativeSchemaError(
                    f"proposal {proposal.proposed_support_id} 的 binding_subject_kind 与所指向"
                    "对象不符（候选与草稿单元不得互换）")

        # 无据陈述不可表达：每个候选至少一条 factual proposal（context 不得给候选撑事实）。
        factual_subjects = {p.binding_subject_id for p in proposals
                            if p.support_semantics == "factual"}
        ungrounded = sorted(set(candidate_ids) - factual_subjects)
        if ungrounded:
            raise NarrativeSchemaError(
                f"ClaimCandidate 没有任何 factual proposal：{ungrounded}"
                "（无据陈述不得进入 narrative wire）")

        containers = {p.authority_container_id for p in proposals}
        undeclared = sorted(containers - set(self.authority_container_ids))
        if undeclared:
            raise NarrativeSchemaError(f"proposal 引用了未声明的权威容器：{undeclared}")

        # exact manifest 的三层集合等式 + 回指关系（唯一重算入口，不接受 Writer 自报）。
        processing = validate_material_processing(
            self.material_manifest, self.material_dispositions, proposals)
        if processing:
            raise NarrativeSchemaError(
                "SectionDraft 材料处理不闭合：" + "；".join(processing))

        expected = derive_draft_id(self)
        if self.draft_id != expected:
            raise NarrativeSchemaError(
                f"SectionDraft.draft_id 与内容不符：声明 {self.draft_id!r}，应为 {expected!r}")

    # ------------------------------------------------------------------
    # 派生视图（**不是**字段）：身份只由对象本身决定，视图不得成为第二个真值来源。
    # ------------------------------------------------------------------

    @property
    def claim_candidate_ids(self) -> tuple[str, ...]:
        return tuple(c.candidate_id for c in self.claim_candidates)

    @property
    def narrative_draft_unit_ids(self) -> tuple[str, ...]:
        return tuple(u.draft_unit_id for u in self.narrative_draft_units)

    @property
    def natural_prose_unit_ids(self) -> tuple[str, ...]:
        return tuple(u.prose_unit_id for u in self.natural_prose_draft)

    def natural_prose_for_candidate(self, candidate_id: str) -> NaturalProseDraftUnit | None:
        """某个候选**第一次**出现在哪一段自然草稿正文里（**唯一**反查入口，npr-1 起是多值的一支）。

        正文保真核对要拿「这条原子写在句子里的原文长什么样」，靠的就是这条反查；不得各自再扫一遍
        （两份口径迟早会分叉，而分叉处正好是「草稿说的是 A、核验用的是 B」的容身处）。

        `npr-1` 起一条候选可以在**多段**草稿里各出现一次（每段各有自己的材料/版本出处，见
        `PROSE_OCCURRENCE_POLICY_VERSION`）。本方法按草稿顺序返回**第一段**——它仍然是确定的，
        但**读的人不得据此以为「这段文字只有一处表达」**：需要看全的调用方用
        :meth:`natural_prose_occurrences_for_candidate`（本条现在是它的一元特例）。
        """
        occurrences = self.natural_prose_occurrences_for_candidate(candidate_id)
        return occurrences[0] if occurrences else None

    def natural_prose_occurrences_for_candidate(self, candidate_id: str
                                                ) -> tuple[NaturalProseDraftUnit, ...]:
        """某个候选在自然草稿里的**全部**表达（按草稿顺序，**唯一**反查入口）。

        为什么必须有这一条：`npr-1` 允许同一条候选出现在多段草稿里之后，「这条原子写在句子里的
        原文」不再是一个值——不同版本的材料写出的同一句原子，措辞可以不同（正是本批要保留的那种
        表达）。只留一个「取第一段」的反查，会让下游在**不知道存在第二段**的情况下拿第一段去核，
        而失败时看起来像「材料没写过这句话」。返回空元组 = 这条候选没有草稿表达（此时它进不了
        `SectionDraft`：闭合核对要求每个候选至少被一段声明）。
        """
        return tuple(unit for unit in self.natural_prose_draft
                     if candidate_id in unit.atom_candidate_ids)

    @property
    def proposed_support_ids(self) -> tuple[str, ...]:
        return tuple(p.proposed_support_id for p in self.proposed_support_refs)

    @property
    def material_disposition_ids(self) -> tuple[str, ...]:
        return tuple(d.wmpd_id for d in self.material_dispositions)

    @property
    def subject_keys(self) -> tuple[tuple[str, str], ...]:
        """本 draft 的 **exact binding subject 集**：`(subject_kind, subject_id)` 有序元组。

        Binding Gate 必须对这张表确定性 map 并断言输出 key 集与它完全相等（§6.3.2）。
        """
        keys = [("claim_candidate", c.candidate_id) for c in self.claim_candidates]
        keys += [("narrative_draft_unit", u.draft_unit_id) for u in self.narrative_draft_units]
        return tuple(keys)

    def candidate_for(self, candidate_id: str) -> ClaimCandidate | None:
        for candidate in self.claim_candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def draft_unit_for(self, unit_id: str) -> NarrativeDraftUnit | None:
        for unit in self.narrative_draft_units:
            if unit.draft_unit_id == unit_id:
                return unit
        return None

    def proposals_for_subject(self, subject_kind: str, subject_id: str
                              ) -> tuple[ProposedSupportRef, ...]:
        """该 subject 的**完整、有序** proposal 集（构造顺序即 ID/hash 集合顺序）。"""
        return tuple(p for p in self.proposed_support_refs
                     if p.binding_subject_kind == subject_kind
                     and p.binding_subject_id == subject_id)

    def material_disposition_for(self, member_ref: str
                                 ) -> WriterMaterialProcessingDisposition | None:
        for row in self.material_dispositions:
            if row.member_ref == member_ref:
                return row
        return None

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "draft_revision": self.draft_revision,
            "task_id": self.task_id, "section_id": self.section_id,
            "company_id": self.company_id, "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "producer_kind": self.producer_kind,
            "writer_policy_version": self.writer_policy_version,
            "writer_rules_version": self.writer_rules_version,
            "writer_renderer_version": self.writer_renderer_version,
            "prompt_version": self.prompt_version, "model_policy": self.model_policy,
            "authority_container_ids": list(self.authority_container_ids),
            # manifest identity 与成员集：available 集合的**精确**表述。
            "manifest_id": self.material_manifest.manifest_id,
            "manifest_fingerprint": self.material_manifest.fingerprint(),
            "material_member_refs": list(self.material_manifest.member_refs()),
            "material_disposition_ids": list(self.material_disposition_ids),
            "claim_candidate_ids": list(self.claim_candidate_ids),
            "narrative_draft_unit_ids": list(self.narrative_draft_unit_ids),
            "proposed_support_ids": list(self.proposed_support_ids),
            "unresolved_ids": list(self.unresolved_ids),
            # 四类投影 + 覆盖摘要：权威根出的状态必须参与身份（§七 4）。
            "unresolved_projections": [dict(x) for x in self.unresolved_projections],
            "coverage_summary": dict(self.coverage_summary),
            "conflict_projections": [dict(x) for x in self.conflict_projections],
            "not_found_projections": [dict(x) for x in self.not_found_projections],
            "title": self.title,
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    def to_dict(self) -> dict:
        return {
            **{k: v for k, v in self.identity_body().items()},
            "draft_id": self.draft_id,
            "material_manifest": self.material_manifest.to_dict(),
            "material_dispositions": [d.to_dict() for d in self.material_dispositions],
            "claim_candidates": [c.to_dict() for c in self.claim_candidates],
            "narrative_draft_units": [u.to_dict() for u in self.narrative_draft_units],
            "proposed_support_refs": [p.to_dict() for p in self.proposed_support_refs],
            "created_at": self.created_at,
            # `identity_body` 之外但必须随载荷留存：`create(**to_dict())` 是合法重建路径，
            # 丢掉它会让重建出的修订退回首轮取值（见 `writer_attempt` 字段说明）。
            "writer_attempt": self.writer_attempt,
            # 同上：自然草稿不进 `identity_body`，但必须随载荷留存，否则重建会丢掉草稿层，
            # `draft_revision` 随之算错（草稿非空时它进修订）。
            "natural_prose_draft": [u.to_dict() for u in self.natural_prose_draft],
        }

    @classmethod
    def create(cls, **kwargs: Any) -> "SectionDraft":
        fields = ("task_id", "section_id", "company_id", "report_as_of",
                  "contract_version", "contract_fingerprint", "producer_kind",
                  "writer_policy_version", "prompt_version", "model_policy",
                  "authority_container_ids", "material_manifest", "material_dispositions",
                  "claim_candidates", "narrative_draft_units", "proposed_support_refs",
                  "unresolved_ids", "unresolved_projections", "coverage_summary",
                  "conflict_projections", "not_found_projections",
                  "dependency_fingerprint", "created_at", "title",
                  "writer_rules_version", "writer_renderer_version", "writer_attempt",
                  "natural_prose_draft")
        body = {k: kwargs.get(k) for k in fields}
        if body["writer_attempt"] is None:
            body["writer_attempt"] = 1
        for key in ("authority_container_ids", "unresolved_ids"):
            body[key] = tuple(body[key] or ())
        for key in ("material_dispositions", "claim_candidates", "narrative_draft_units",
                    "proposed_support_refs", "natural_prose_draft"):
            body[key] = tuple(body[key] or ())
        for key in ("unresolved_projections", "conflict_projections", "not_found_projections"):
            body[key] = _projection_list(body[key], f"SectionDraft.{key}")
        # `create(**draft.to_dict())` 是内容寻址对象的合法重建路径（装配器重建报告就走这条），
        # 因此嵌套对象既接受实例也接受其 canonical dict 形式；dict 一律走 `from_dict`，与
        # 显式声明的 id 逐项校验，不让「内容变了但 id 还是旧的」从重建路径溜进来。
        if isinstance(body["material_manifest"], Mapping):
            body["material_manifest"] = WriterMaterialManifest.from_dict(
                dict(body["material_manifest"]))
        for key, coercer in (("material_dispositions",
                              WriterMaterialProcessingDisposition.from_dict),
                             ("claim_candidates", ClaimCandidate.from_dict),
                             ("narrative_draft_units", NarrativeDraftUnit.from_dict),
                             ("proposed_support_refs", ProposedSupportRef.from_dict),
                             ("natural_prose_draft", NaturalProseDraftUnit.from_dict)):
            body[key] = tuple(coercer(x) if isinstance(x, Mapping) else x for x in body[key])
        body["coverage_summary"] = _canon_jsonable(dict(body["coverage_summary"] or {}))
        body["title"] = body["title"] if isinstance(body["title"], str) else ""
        for key in ("company_id", "report_as_of", "contract_version", "contract_fingerprint",
                    "dependency_fingerprint", "created_at", "writer_rules_version",
                    "writer_renderer_version"):
            body[key] = body[key] or ""
        manifest = body["material_manifest"]
        if not isinstance(manifest, WriterMaterialManifest):
            raise NarrativeSchemaError(
                "SectionDraft.create 必须给出 material_manifest（对象或 canonical dict）")
        body["draft_revision"] = kwargs.get("draft_revision") or derive_draft_revision(
            task_id=body["task_id"], section_id=body["section_id"],
            company_id=body["company_id"], report_as_of=body["report_as_of"],
            contract_version=body["contract_version"],
            contract_fingerprint=body["contract_fingerprint"],
            writer_policy_version=body["writer_policy_version"],
            prompt_version=body["prompt_version"], model_policy=body["model_policy"],
            manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint(),
            attempt=body["writer_attempt"],
            natural_prose_digest=natural_prose_draft_digest(body["natural_prose_draft"]))
        body["schema_version"] = NARRATIVE_SCHEMA_VERSION
        # identity 只由 `identity_body` 的形状决定：这里用同一形状算 id，避免两套输入集。
        probe = cls.__new__(cls)
        for key, value in body.items():
            object.__setattr__(probe, key, value)
        object.__setattr__(probe, "material_manifest", manifest)
        body["draft_id"] = derive_draft_id(probe)
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "SectionDraft":
        allowed = {"schema_version", "draft_id", "draft_revision", "task_id", "section_id",
                   "company_id", "report_as_of", "contract_version", "contract_fingerprint",
                   "producer_kind", "writer_policy_version", "prompt_version", "model_policy",
                   "authority_container_ids", "material_manifest", "material_dispositions",
                   "claim_candidates", "narrative_draft_units", "proposed_support_refs",
                   "unresolved_ids", "unresolved_projections", "coverage_summary",
                   "conflict_projections", "not_found_projections",
                   "dependency_fingerprint", "created_at", "title",
                   "writer_rules_version", "writer_renderer_version", "writer_attempt",
                   # `narr-7` 起：门前自然草稿。没有草稿层的载荷缺这个键（缺省空元组），读回来与
                   # 加入该字段之前逐字节等价。
                   "natural_prose_draft",
                   # `to_dict` 里的冗余 id 投影：读回时逐项与对象重算结果比对，不作为身份输入。
                   "manifest_id", "manifest_fingerprint", "material_member_refs",
                   "material_disposition_ids", "claim_candidate_ids",
                   "narrative_draft_unit_ids", "proposed_support_ids"}
        d = _reject_unknown(d, allowed, "SectionDraft")
        draft = cls(
            schema_version=_require_nonempty(d.get("schema_version"), "SectionDraft.schema_version"),
            draft_id=_require_nonempty(d.get("draft_id"), "SectionDraft.draft_id"),
            draft_revision=_require_nonempty(d.get("draft_revision"),
                                             "SectionDraft.draft_revision"),
            task_id=_require_nonempty(d.get("task_id"), "SectionDraft.task_id"),
            section_id=_require_nonempty(d.get("section_id"), "SectionDraft.section_id"),
            company_id=d.get("company_id") or "",
            report_as_of=d.get("report_as_of") or "",
            contract_version=d.get("contract_version") or "",
            contract_fingerprint=d.get("contract_fingerprint") or "",
            producer_kind=_require_nonempty(d.get("producer_kind"), "SectionDraft.producer_kind"),
            writer_policy_version=_require_nonempty(d.get("writer_policy_version"),
                                                    "SectionDraft.writer_policy_version"),
            prompt_version=_require_nonempty(d.get("prompt_version"), "SectionDraft.prompt_version"),
            model_policy=_require_nonempty(d.get("model_policy"), "SectionDraft.model_policy"),
            authority_container_ids=_str_tuple(d.get("authority_container_ids") or (),
                                               "SectionDraft.authority_container_ids"),
            material_manifest=WriterMaterialManifest.from_dict(
                _req(d, "material_manifest", "SectionDraft")),
            material_dispositions=tuple(
                WriterMaterialProcessingDisposition.from_dict(x)
                for x in d.get("material_dispositions") or ()),
            claim_candidates=tuple(ClaimCandidate.from_dict(x)
                                   for x in d.get("claim_candidates") or ()),
            narrative_draft_units=tuple(NarrativeDraftUnit.from_dict(x)
                                        for x in d.get("narrative_draft_units") or ()),
            proposed_support_refs=tuple(ProposedSupportRef.from_dict(x)
                                        for x in d.get("proposed_support_refs") or ()),
            unresolved_ids=_str_tuple(d.get("unresolved_ids") or (), "SectionDraft.unresolved_ids"),
            unresolved_projections=_projection_list(d.get("unresolved_projections"),
                                                    "SectionDraft.unresolved_projections"),
            coverage_summary=dict(d.get("coverage_summary") or {}),
            conflict_projections=_projection_list(d.get("conflict_projections"),
                                                  "SectionDraft.conflict_projections"),
            not_found_projections=_projection_list(d.get("not_found_projections"),
                                                   "SectionDraft.not_found_projections"),
            dependency_fingerprint=d.get("dependency_fingerprint") or "",
            created_at=d.get("created_at") or "",
            title=d.get("title") or "",
            writer_rules_version=d.get("writer_rules_version") or "",
            writer_renderer_version=d.get("writer_renderer_version") or "",
            # 旧载荷没有这个键（首轮语义），缺省 1 与加入该字段之前逐字节等价。
            writer_attempt=(1 if d.get("writer_attempt") is None else d.get("writer_attempt")),
            # `narr-7` 起：没有草稿层的载荷缺这个键，缺省空元组与加入该字段之前逐字节等价。
            # 带草稿层而 schema_version 是 narr-7/更早的载荷**读不进来**（单元类型要求 narr-8）：
            # 那条路只能经 `load_legacy_narrative_narr7_for_audit` 只读回放。
            natural_prose_draft=tuple(NaturalProseDraftUnit.from_dict(x)
                                      for x in d.get("natural_prose_draft") or ()))
        derived_views = {
            "manifest_id": [draft.material_manifest.manifest_id],
            "manifest_fingerprint": [draft.material_manifest.fingerprint()],
            "material_member_refs": list(draft.material_manifest.member_refs()),
            "material_disposition_ids": list(draft.material_disposition_ids),
            "claim_candidate_ids": list(draft.claim_candidate_ids),
            "narrative_draft_unit_ids": list(draft.narrative_draft_unit_ids),
            "proposed_support_ids": list(draft.proposed_support_ids),
        }
        for key, actual in derived_views.items():
            declared = d.get(key)
            if declared is None:
                continue
            declared_list = declared if isinstance(declared, (list, tuple)) else [declared]
            if list(declared_list) != actual:
                raise NarrativeSchemaError(
                    f"SectionDraft.{key} 与对象重算结果不符：声明 {list(declared_list)}，"
                    f"实际 {actual}")
        return draft


def derive_draft_id(draft: Any) -> str:
    body = draft.identity_body() if isinstance(draft, SectionDraft) else draft.body
    return content_id("sdraft_", body)


def render_section_markdown(title: str, draft: Any, *,
                            citation_sources: "Mapping[str, Any] | None" = None) -> str:
    """**唯一**的章节 Markdown 渲染器（§五 4）：只读 draft 的段落/表格/缺口，确定性、可复算。

    组成分两部分，都只从 draft 自身派生：
    1. 正文：段落与表格（事实表面的唯一来源是 Claim）；
    2. 缺口附录：`draft.unresolved_projections` 里的权威原始状态（§十三 人读 gap）。

    调用方（写作器、组装器、只读预览）都必须经过这里，不得各自拼正文：
    组装器会重新渲染本函数并与 `SectionResult.markdown` 逐字节比对，不一致即拒。

    **门前候选束（`SectionDraft` successor）没有段落/表格**：它的表达单元是
    `NarrativeDraftUnit`（门前草稿，供 context 定位），final Narrative 只在两道门之后形成。
    因此对门前束调用本函数不是「渲染空正文」，而是**用错了对象**：显式 fail-closed，
    不得静默返回一份没有事实的正文（§0.13）。门后渲染见 3D 的 final-Narrative 渲染入口。
    """
    if not hasattr(draft, "paragraphs") or not hasattr(draft, "tables"):
        raise NarrativeSchemaError(
            "render_section_markdown 只渲染含 final Narrative（paragraphs/tables）的章节对象；"
            "门前 SectionDraft 只有 NarrativeDraftUnit，final Narrative 必须在两道门之后形成"
            "（§6.5：候选正文在门通过前不得被表述为已审查/可发布的正文）")
    return render_body_and_appendix(
        title, draft.paragraphs, draft.tables,
        # 缺 `unresolved_projections` 的替身对象不得被当成「本节没有缺口」：缺口附录是门后正文的
        # 一部分，缺字段渲染出的正文会**少掉**缺口，而正文正是人读判断的唯一入口。
        tuple(current_wire_collection(
            draft, "unresolved_projections", "render_section_markdown 的 draft") or ()),
        citation_sources=citation_sources)


def render_final_narrative_markdown(title: str, narrative: Any,
                                    unresolved_projections: Sequence[Mapping[str, Any]] = (), *,
                                    citation_sources: "Mapping[str, Any] | None") -> str:
    """门**后**正文渲染入口（3D）：与 `render_section_markdown` 共用同一个拼装实现。

    正文来自 final Narrative（段落/表格），缺口附录仍只从 draft 留存的**权威投影**渲染 ——
    门后不重新判定缺口，只把门前已登记的权威状态原样带进正文；两者共用
    `render_body_and_appendix`，因此「同一份正文两种写法」不可能出现。
    传错对象（门前束或别的 wire）即 fail-closed，不静默返回一份没有事实的正文。

    `citation_sources` **没有缺省值**（`pwr-5`）：正式章节正文是唯一对人呈现的产物，来源必须
    可见；留一个缺省值等于给正式链留一条「忘了给来源、读者看不到出处」的静默走法。值只能是
    `citation_source_index(claims)` 的产物，覆盖不全即 fail-closed。
    """
    if not isinstance(narrative, SectionNarrative):
        raise NarrativeSchemaError(
            f"render_final_narrative_markdown 只接受 final Narrative（SectionNarrative），"
            f"得到 {type(narrative).__name__}（门前草稿单元不是正文）")
    if citation_sources is None:
        raise NarrativeSchemaError(
            "render_final_narrative_markdown 必须给出来源索引（`citation_source_index(claims)`）："
            "本章正文是唯一对人呈现的产物，来源是它的一部分；没有索引就没有来源行，"
            "而「忘了给」与「本节确实没有来源」必须分得开（后者传空映射 `{}`）。")
    return render_body_and_appendix(title, narrative.paragraphs, narrative.tables,
                                    tuple(unresolved_projections or ()),
                                    citation_sources=citation_sources)


def render_body_and_appendix(title: str, paragraphs: Sequence[Any], tables: Sequence[Any],
                             unresolved_projections: Sequence[Mapping[str, Any]], *,
                             citation_sources: "Mapping[str, Any] | None" = None) -> str:
    """`render_section_markdown` 的**同一实现**，供写作器在 draft 定稿前调用。

    §五 4 要求「正文只能由唯一渲染器从 draft 生成」，而写作器必须在 `section_version`
    （内含正文指纹）之前拿到正文，因此渲染器必须能在 draft 之外被调用。这里把「正文 + 缺口
    附录」的拼接收成一个实现：`render_section_markdown` 只是它的一个薄包装，两者不可能漂移。
    """
    body = render_paragraphs_markdown(title, paragraphs, tables,
                                      citation_sources=citation_sources)
    appendix = _unresolved_appendix_from_projections(unresolved_projections)
    return body + "\n\n" + appendix if appendix else body


def canonical_unresolved_projections(items: Any) -> tuple[dict, ...]:
    """缺口投影的**规范形态**（含规范排序）：写作器必须用它构造 draft。

    `SectionDraft` 会对投影做规范化排序（顺序不参与身份、内容全参与）。若写作器用一份
    顺序不同的投影渲染正文、而组装器用 draft 里的规范投影重算，两者会逐字节不一致——
    正文就与 draft 脱钩了。因此写作器与渲染器必须共用这一个规范化入口。
    """
    return _projection_list(items, "unresolved_projections")


def citation_source_coverage(paragraphs: Sequence[Any], tables: Sequence[Any],
                             sources: "Mapping[str, Any] | None") -> "Mapping[str, Any]":
    """来源索引必须覆盖正文引用的**全集**；缺一条即 fail-closed（`pwr-5`）。

    两条被明确拒绝的走法：索引**部分**覆盖（会让一部分引用的来源被静默省略，读者看到的来源
    行就成了「本节全部来源」的假称），以及调用方**自带**一段来源文字（会让未经 Claim 授权的
    文字进入正文）。因此这里只接受由 `citation_source_index` 现场重建的索引，并且要求它对
    `paragraphs` / `tables` 里出现过的每一个 `citation_id` 都有键（值可以是 `None`，表示
    「这条引用没有中文来源」——那与「漏给」是两件事）。
    """
    if sources is None:
        # `None` = 本次调用**没有**引用载荷可用（门前草稿与单测走的原语路径），不是「索引不全」：
        # 它渲染出的正文不含 `- 来源：` 行。正式章节正文入口不接受 `None`（见
        # `render_final_narrative_markdown`），因此正式链上不存在这条静默走法。
        return {}
    used = tuple(dict.fromkeys(
        str(cid) for unit in (*paragraphs, *tables)
        for cid in (getattr(unit, "citation_ids", ()) or ())))
    if not used:
        return sources
    missing = sorted(set(used) - set(sources))
    if missing:
        raise NarrativeSchemaError(
            f"来源索引未覆盖正文引用的全集：正文共 {len(used)} 条引用，索引缺 "
            f"{missing[:6]}{'…' if len(missing) > 6 else ''}（共缺 {len(missing)} 条）。"
            "来源索引只能由本节定稿 Claim 集现场重建（`citation_source_index(claims)`）："
            "部分覆盖会让一部分引用的来源被静默省略，自带文字会让未经授权的内容进入正文。")
    return sources or {}


def render_paragraphs_markdown(title: str, paragraphs: Sequence[Any],
                               tables: Sequence[Any], *,
                               citation_sources: "Mapping[str, Any] | None" = None) -> str:
    """同一个渲染器的直接入口（写作器在 SectionDraft 定稿前就要拿到正文）。

    `citation_sources` 是 `citation_source_index(claims)` 的产物（`pwr-5`）。不给（`None`）时
    正文里不出现 `- 来源：` 行 —— 这是**门前草稿**与单测用的原语入口；章节正文入口
    `render_final_narrative_markdown` 不设缺省值，正式链上不存在「忘了给来源」的写法。
    """
    sources = citation_source_coverage(paragraphs, tables, citation_sources)
    lines = [f"### {title}"]
    for para in paragraphs:
        lines.append(para.text)
        # `pwr-5`：正文句的来源也必须**可见**（同表格那一行）。`NarrativeParagraph.citation_ids`
        # 是 `derive_citation_id` 的单向散列、不可反解，因此来源从 Claim 的引用对象现场重建；
        # 印不出中文来源的引用（如 `evidence`）整条不印，不落内部记号。
        source_line = _citation_source_line(getattr(para, "citation_ids", ()), sources)
        if source_line:
            lines.append(source_line)
    for table in tables:
        lines.append(f"**{table.caption}**")
        # §十一：主体/期间/单位必须**可见地**出现在正文里，不得只藏在 JSON 元数据中。
        # 这一行由 draft 的表格元数据确定性渲染，因此元数据一改，正文指纹即改。
        # `pwr-4`：单位按 `reader_unit_text` 写成中文、且与单元格自己的显示一致（单元格按各自
        # 量级渲染）；主体由 `entity_scope` 带出（可核实名称 + 主体标识）。两处都只改**渲染**，
        # 表格元数据里的 `unit` / `entity_scope` 一字不动。
        scope_line = " / ".join(
            f"{label}：{value}" for label, value in
            (("主体", table.entity_scope), ("期间", table.period),
             ("单位", reader_unit_text(table.unit)))
            if value)
        if scope_line:
            lines.append(scope_line)
        lines.append("| " + " | ".join(table.header) + " |")
        lines.append("|" + "---|" * len(table.header))
        for row in table.rows:
            lines.append("| " + " | ".join([row.label, *row.cells]) + " |")
        # 读者面（`pwr-4`）：代理口径的数值旁边要有一句**中文**口径说明。权威自己的限定语是
        # `代理口径（CODE）`（它必须逐字留在单元格与 Claim 里，一个字都不改），只印一个内部
        # 原因码，读者读不出「这个数是怎么算出来的」。说明逐字取自权威自己的公式登记表。
        for note in proxy_caliber_notes(
                cell for row in table.rows for cell in row.cells):
            lines.append(f"- 口径说明：{note}")
        # `pwr-5`：来源与口径说明并列（同一行式），表与正文句都印。
        source_line = _citation_source_line(table.citation_ids, sources)
        if source_line:
            lines.append(source_line)
    return "\n".join(lines)


#: 缺口附录的标题行（正文与只读视图共用同一份措辞，避免出现"两份缺口文本"）。
UNRESOLVED_APPENDIX_HEADING = "**未覆盖与缺口（权威状态原样保留）**"


def _gap_line(state: str, reason_code: str, detail: str, raw: str) -> str:
    """一条人读缺口：`[权威原始状态/状态/原因码]` + 权威侧的缺口说明。

    状态与原因码一律**逐字**来自上游权威，不改写、不美化（§七 4）；`raw` 是权威侧的原始
    覆盖状态（partial / blocked / …），缺失时如实省略，不用占位符假装有值。
    """
    tag = f"{raw}/{state}/{reason_code}" if raw else f"{state}/{reason_code}"
    return f"- [{tag}] {detail}"


def render_unresolved_appendix(unresolved: Sequence[Any],
                               authority_status: Mapping[str, str]) -> str:
    """缺口附录（人读 gap）：权威原始状态原样保留，不改写、不美化。

    缺口正文与权威标签都来自上游权威对象，因此它是「按权威状态展示缺口」而不是「写作」。
    写作器用本函数从 `SectionUnresolved` 对象直接渲染；组装器另走
    `_unresolved_appendix_from_projections`（draft 里留存的同一批字段），两者共用
    `_gap_line`，并由组装器的逐字节比对保证不漂移。
    """
    if not unresolved:
        return ""
    lines = [UNRESOLVED_APPENDIX_HEADING]
    for item in unresolved:
        lines.append(_gap_line(item.state, item.reason_code, item.detail,
                               authority_status.get(item.unresolved_id, "")))
    return "\n".join(lines)


def _unresolved_appendix_from_projections(projections: Sequence[Mapping[str, Any]]) -> str:
    """从 draft 留存的缺口投影渲染同一份人读 gap（字段与 `SectionUnresolved` 同名）。"""
    if not projections:
        return ""
    lines = [UNRESOLVED_APPENDIX_HEADING]
    for item in projections:
        lines.append(_gap_line(str(item.get("state") or ""), str(item.get("reason_code") or ""),
                               str(item.get("detail") or ""),
                               str(item.get("authority_status") or "")))
    return "\n".join(lines)


def body_fingerprint_of(markdown: str) -> str:
    """正文指纹（送进冻结的 `ReportVersionIdentity.body_fingerprint`，64 位小写 sha256）。"""
    if not isinstance(markdown, str):
        raise NarrativeSchemaError("body_fingerprint_of 只接受字符串正文")
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 确定性 narrative 门
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeGateIssue:
    """一条 narrative 门问题（只描述，不改正文、不放行）。"""

    rule_id: str
    severity: str
    location: str
    detail: str
    rework_target_ref: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.rule_id, "NarrativeGateIssue.rule_id")
        _require_enum(self.severity, GATE_SEVERITIES, "NarrativeGateIssue.severity")
        _require_nonempty(self.location, "NarrativeGateIssue.location")

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "severity": self.severity, "location": self.location,
                "detail": self.detail, "rework_target_ref": self.rework_target_ref}


@dataclass(frozen=True)
class NarrativeGateResult:
    """确定性 narrative 规则的门结果（§6.5 第 1 项）。不含任何 LLM 判断。"""

    gate_result_id: str
    gate_version: str
    section_draft_id: str
    rules_passed: bool
    blocking: bool
    issues: tuple[NarrativeGateIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.gate_version != NARRATIVE_GATE_VERSION:
            raise NarrativeSchemaError(
                f"NarrativeGateResult.gate_version 必须为 {NARRATIVE_GATE_VERSION!r}")
        _require_nonempty(self.section_draft_id, "NarrativeGateResult.section_draft_id")
        expected = derive_gate_result_id(self)
        if self.gate_result_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeGateResult.gate_result_id 与内容不符：声明 {self.gate_result_id!r}，"
                f"应为 {expected!r}")

    def issues_of(self, severity: str) -> tuple[NarrativeGateIssue, ...]:
        return tuple(i for i in self.issues if i.severity == severity)

    def to_dict(self) -> dict:
        return {"gate_result_id": self.gate_result_id, "gate_version": self.gate_version,
                "section_draft_id": self.section_draft_id, "rules_passed": self.rules_passed,
                "blocking": self.blocking, "issues": [i.to_dict() for i in self.issues]}

    @classmethod
    def build(cls, *, section_draft_id: str,
              issues: Sequence[NarrativeGateIssue]) -> "NarrativeGateResult":
        items = tuple(issues)
        blocking = any(i.severity == "blocking" for i in items)
        rework = any(i.severity == "rework" for i in items)
        body = {"gate_version": NARRATIVE_GATE_VERSION, "section_draft_id": section_draft_id,
                "issues": [i.to_dict() for i in items]}
        return cls(gate_result_id=content_id("ngr_", body), gate_version=NARRATIVE_GATE_VERSION,
                   section_draft_id=section_draft_id,
                   rules_passed=(not blocking and not rework), blocking=blocking, issues=items)

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeGateResult":
        d = _reject_unknown(d, {"gate_result_id", "gate_version", "section_draft_id",
                                "rules_passed", "blocking", "issues"}, "NarrativeGateResult")
        return cls(
            gate_result_id=_require_nonempty(d.get("gate_result_id"),
                                             "NarrativeGateResult.gate_result_id"),
            gate_version=_require_nonempty(d.get("gate_version"),
                                           "NarrativeGateResult.gate_version"),
            section_draft_id=_require_nonempty(d.get("section_draft_id"),
                                               "NarrativeGateResult.section_draft_id"),
            rules_passed=bool(d.get("rules_passed")), blocking=bool(d.get("blocking")),
            issues=tuple(NarrativeGateIssue(
                rule_id=x["rule_id"], severity=x["severity"], location=x["location"],
                detail=x.get("detail", ""), rework_target_ref=x.get("rework_target_ref"))
                for x in d.get("issues") or ()))


def derive_gate_result_id(gate: Any) -> str:
    body = {"gate_version": gate.gate_version, "section_draft_id": gate.section_draft_id,
            "issues": [i.to_dict() for i in gate.issues]}
    return content_id("ngr_", body)


# ---------------------------------------------------------------------------
# NarrativeEvaluationBinding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeEvaluationBinding:
    """`section_result_id + section_draft_id + evaluation_id + narrative_gate_result_id` 的绑定。

    §6.5：组装器必须拒绝任一身份不一致的组合，防止把旧 `SectionEvaluation` 套到新 Draft 上。
    """

    schema_version: str
    binding_id: str
    section_result_id: str
    section_draft_id: str
    evaluation_id: str
    narrative_gate_result_id: str
    rules_version: str
    gate_version: str

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"NarrativeEvaluationBinding.schema_version 必须为 {NARRATIVE_SCHEMA_VERSION!r}")
        for name in ("section_result_id", "section_draft_id", "evaluation_id",
                     "narrative_gate_result_id", "rules_version", "gate_version"):
            _require_nonempty(getattr(self, name), f"NarrativeEvaluationBinding.{name}")
        expected = derive_binding_id(self)
        if self.binding_id != expected:
            raise NarrativeSchemaError(
                f"NarrativeEvaluationBinding.binding_id 与内容不符：声明 {self.binding_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {"schema_version": self.schema_version,
                "section_result_id": self.section_result_id,
                "section_draft_id": self.section_draft_id,
                "evaluation_id": self.evaluation_id,
                "narrative_gate_result_id": self.narrative_gate_result_id,
                "rules_version": self.rules_version, "gate_version": self.gate_version}

    def to_dict(self) -> dict:
        return {"binding_id": self.binding_id, **self.identity_body()}

    @classmethod
    def create(cls, *, section_result_id: str, section_draft_id: str, evaluation_id: str,
               narrative_gate_result_id: str, rules_version: str,
               gate_version: str) -> "NarrativeEvaluationBinding":
        body = {"schema_version": NARRATIVE_SCHEMA_VERSION,
                "section_result_id": section_result_id, "section_draft_id": section_draft_id,
                "evaluation_id": evaluation_id,
                "narrative_gate_result_id": narrative_gate_result_id,
                "rules_version": rules_version, "gate_version": gate_version}
        return cls(binding_id=content_id("neb_", body), **body)

    @classmethod
    def from_dict(cls, d: Any) -> "NarrativeEvaluationBinding":
        d = _reject_unknown(d, {"binding_id", "schema_version", "section_result_id",
                                "section_draft_id", "evaluation_id",
                                "narrative_gate_result_id", "rules_version", "gate_version"},
                            "NarrativeEvaluationBinding")
        return cls(
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "NarrativeEvaluationBinding.schema_version"),
            binding_id=_require_nonempty(d.get("binding_id"),
                                         "NarrativeEvaluationBinding.binding_id"),
            section_result_id=_require_nonempty(d.get("section_result_id"),
                                                "NarrativeEvaluationBinding.section_result_id"),
            section_draft_id=_require_nonempty(d.get("section_draft_id"),
                                               "NarrativeEvaluationBinding.section_draft_id"),
            evaluation_id=_require_nonempty(d.get("evaluation_id"),
                                            "NarrativeEvaluationBinding.evaluation_id"),
            narrative_gate_result_id=_require_nonempty(
                d.get("narrative_gate_result_id"),
                "NarrativeEvaluationBinding.narrative_gate_result_id"),
            rules_version=_require_nonempty(d.get("rules_version"),
                                            "NarrativeEvaluationBinding.rules_version"),
            gate_version=_require_nonempty(d.get("gate_version"),
                                           "NarrativeEvaluationBinding.gate_version"))


def derive_binding_id(binding: Any) -> str:
    body = (binding.identity_body() if isinstance(binding, NarrativeEvaluationBinding)
            else binding.body)
    return content_id("neb_", body)


# ---------------------------------------------------------------------------
# AssembledReport
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssembledSection:
    """组装产物里一个 section 的只读切片（正文 + gap + 全部身份引用）。"""

    section_id: str
    title: str
    section_result_id: str
    section_draft_id: str
    evaluation_id: str
    narrative_gate_result_id: str
    binding_id: str
    decision: str
    coverage_summary: dict
    claim_ids: tuple[str, ...]
    paragraph_ids: tuple[str, ...]
    table_ids: tuple[str, ...]
    unresolved_ids: tuple[str, ...]
    markdown: str

    def __post_init__(self) -> None:
        for name in ("section_id", "title", "section_result_id", "section_draft_id",
                     "evaluation_id", "narrative_gate_result_id", "binding_id", "decision"):
            _require_nonempty(getattr(self, name), f"AssembledSection.{name}")

    def to_dict(self) -> dict:
        return {"section_id": self.section_id, "title": self.title,
                "section_result_id": self.section_result_id,
                "section_draft_id": self.section_draft_id,
                "evaluation_id": self.evaluation_id,
                "narrative_gate_result_id": self.narrative_gate_result_id,
                "binding_id": self.binding_id, "decision": self.decision,
                "coverage_summary": dict(self.coverage_summary),
                "claim_ids": list(self.claim_ids),
                "paragraph_ids": list(self.paragraph_ids),
                "table_ids": list(self.table_ids),
                "unresolved_ids": list(self.unresolved_ids),
                "markdown": self.markdown}


@dataclass(frozen=True)
class AssembledReport:
    """确定性组装产物（§6.6）。

    §四：报告版本**只有唯一权威根** —— M930-1 冻结的 `ReportVersionIdentity`。本对象把它
    整只带在身上（`version_identity`），`report_version` 只能从它的 `content_fingerprint`
    读出；本模块不再有自己的 `derive_report_version`，也不再造第二套 `abr_` 身份。
    `report_id` 是**内容句柄**（由同一 `content_fingerprint` 派生），不含时间、attempt 或路径。
    """

    schema_version: str
    report_id: str
    report_version: str
    version_identity: "_BACKBONE.ReportVersionIdentity"
    job_id: str
    company_id: str
    report_as_of: str
    profile_fingerprint: str
    projection_id: str
    contract_version: str
    contract_fingerprint: str
    assembler_version: str
    sections: tuple[AssembledSection, ...]
    scope_coverage: tuple[dict, ...]
    disposition_index: tuple[dict, ...]
    support_ref_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    gap_index: tuple[dict, ...]
    conflict_index: tuple[dict, ...]
    authority_container_ids: tuple[str, ...]
    retention: dict
    markdown: str
    dependency_fingerprint: str
    generated_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != REPORT_SCHEMA_VERSION:
            raise NarrativeSchemaError(
                f"AssembledReport.schema_version 必须为 {REPORT_SCHEMA_VERSION!r}")
        if not isinstance(self.version_identity, _BACKBONE.ReportVersionIdentity):
            raise NarrativeSchemaError(
                "AssembledReport.version_identity 必须是冻结的 "
                "sections.backbone_schema.ReportVersionIdentity（报告版本的唯一权威根）")
        for name in ("job_id", "company_id", "profile_fingerprint", "projection_id",
                     "assembler_version"):
            _require_nonempty(getattr(self, name), f"AssembledReport.{name}")
        if not self.sections:
            raise NarrativeSchemaError("AssembledReport.sections 不得为空")
        section_ids = [s.section_id for s in self.sections]
        if len(set(section_ids)) != len(section_ids):
            raise NarrativeSchemaError("AssembledReport.sections 含重复 section_id")
        # §4.6 之一：报告身份必须绑定各节 SectionDraft 身份。改 Draft ID（重跑、重写、
        # 换 revision）而正文恰好不变时，报告版本也必须换——因此这里既查节内唯一，也查
        # 与身份对象声明值的**逐字**一致（旧身份对象不得静默当 current）。
        draft_ids = [s.section_draft_id for s in self.sections]
        if len(set(draft_ids)) != len(draft_ids):
            raise NarrativeSchemaError("AssembledReport.sections 含重复 section_draft_id")
        declared_drafts = tuple(self.version_identity.section_draft_ids)
        if tuple(sorted(draft_ids)) != declared_drafts:
            raise NarrativeSchemaError(
                f"AssembledReport 的各节 SectionDraft 身份与 version_identity 不符："
                f"产物含 {sorted(draft_ids)}，身份声明 {list(declared_drafts)}"
                "（Draft 身份变化必须换出新的 report_version）")
        # §4.6 之二：读回时**独立重算**规范载荷指纹（排除 report_version / report_id /
        # version_identity / generated_at）。身份声明值与重算值不一致即拒——「载荷变了、
        # 版本没变」在这里暴露；旧身份对象只能经显式历史审计入口读回。
        actual_payload = assembled_payload_fingerprint_of(self)
        if self.version_identity.assembled_payload_fingerprint != actual_payload:
            raise NarrativeSchemaError(
                "AssembledReport 的规范载荷与 version_identity.assembled_payload_fingerprint "
                f"不符：身份声明 {self.version_identity.assembled_payload_fingerprint!r}，"
                f"实际重算 {actual_payload!r}（报告内容变化必须换出新的 report_version）")
        expected = derive_report_version(self)
        if self.report_version != expected:
            raise NarrativeSchemaError(
                f"AssembledReport.report_version 与权威身份不符：声明 {self.report_version!r}，"
                f"应为 {expected!r}（正文或任一内容依赖变化必须改变 report_version）")
        expected_id = derive_report_id(self)
        if self.report_id != expected_id:
            raise NarrativeSchemaError(
                f"AssembledReport.report_id 与内容身份不符：声明 {self.report_id!r}，"
                f"应为 {expected_id!r}")
        # 交叉核对：报告级字段必须与权威身份一致（不得出现「报告说自己属于 A、身份写 B」）。
        for name in ("job_id", "company_id", "report_as_of", "profile_fingerprint",
                     "projection_id", "contract_fingerprint", "dependency_fingerprint"):
            actual = getattr(self, name)
            declared = getattr(self.version_identity, name, None)
            if declared is not None and actual != declared:
                raise NarrativeSchemaError(
                    f"AssembledReport.{name}={actual!r} 与 version_identity 的 "
                    f"{declared!r} 不一致（报告身份不得自证）")
        # §五：正文是身份的一部分。`report_version` 只从权威身份派生，因此必须显式核对
        # `markdown` 与 `version_identity.body_fingerprint` —— 否则「改正文、留身份」会让
        # 正文与 report_version 脱钩，等于又把第二套版本放回来。
        body_fingerprint = body_fingerprint_of(self.markdown)
        if self.version_identity.body_fingerprint != body_fingerprint:
            raise NarrativeSchemaError(
                f"AssembledReport.markdown 与 version_identity.body_fingerprint 不符："
                f"身份声明 {self.version_identity.body_fingerprint!r}，正文实际 "
                f"{body_fingerprint!r}（正文变化必须换出新的 report_version，正文与报告身份"
                "不得脱钩）")

    def payload_kwargs(self) -> dict:
        """规范载荷的入参（**按纳入字段闭集**取；读回复算走同一入口 `report_payload_kwargs`）。"""
        return report_payload_kwargs(self)

    def identity_body(self) -> dict:
        """`report_version` 覆盖的全部字段（**不含** `generated_at` / `report_id`）。

        报告版本来自 `version_identity.content_fingerprint`，正文其余结构只作为
        「身份必须与实际产物一致」的复核依据。除自身身份对象外，其余字段逐字来自
        `assembled_payload_body`——规范载荷只有一套口径，不存在第二个投影。
        """
        return {"version_identity": self.version_identity.to_dict(),
                **assembled_payload_body(**self.payload_kwargs())}

    def section(self, section_id: str) -> AssembledSection:
        for item in self.sections:
            if item.section_id == section_id:
                return item
        raise NarrativeSchemaError(f"AssembledReport 不含 section {section_id!r}")

    def to_dict(self) -> dict:
        return {"report_id": self.report_id, "report_version": self.report_version,
                **{k: v for k, v in self.identity_body().items()},
                "generated_at": self.generated_at}

    @classmethod
    def create(cls, **kwargs: Any) -> "AssembledReport":
        fields = ("job_id", "company_id", "report_as_of", "profile_fingerprint",
                  "projection_id", "contract_version", "contract_fingerprint",
                  "assembler_version", "sections", "scope_coverage", "disposition_index",
                  "support_ref_ids", "claim_ids", "gap_index", "conflict_index",
                  "authority_container_ids", "retention", "markdown",
                  "dependency_fingerprint", "generated_at")
        body = {k: kwargs.get(k) for k in fields}
        for key in ("sections", "scope_coverage", "disposition_index", "support_ref_ids",
                    "claim_ids", "gap_index", "conflict_index", "authority_container_ids"):
            body[key] = tuple(body[key] or ())
        # sections 必须归一成 AssembledSection 对象：identity_body() 产出的是 dict 投影，
        # 而 create(**identity_body()) 是「按内容重建」的正式路径。只接受对象会让这条路径
        # 直接崩掉（AttributeError），因此这里两种形态都收。
        body["sections"] = tuple(
            s if isinstance(s, AssembledSection) else _section_from_dict(s)
            for s in body["sections"])
        body["retention"] = dict(body["retention"] or {})
        body["report_as_of"] = body["report_as_of"] or ""
        body["contract_version"] = body["contract_version"] or ""
        body["contract_fingerprint"] = body["contract_fingerprint"] or ""
        body["markdown"] = body["markdown"] or ""
        body["dependency_fingerprint"] = body["dependency_fingerprint"] or ""
        body["generated_at"] = body["generated_at"] or ""
        identity = _coerce_version_identity(kwargs.get("version_identity"))
        report_version = _BACKBONE.derive_report_version(identity.content_fingerprint)
        return cls(schema_version=REPORT_SCHEMA_VERSION, version_identity=identity,
                   report_version=report_version,
                   report_id=derive_report_id_for(identity.content_fingerprint), **body)

    @classmethod
    def from_dict(cls, d: Any) -> "AssembledReport":
        allowed = {"report_id", "report_version", "schema_version", "job_id", "company_id",
                   "report_as_of", "profile_fingerprint", "projection_id", "contract_version",
                   "contract_fingerprint", "assembler_version", "sections", "scope_coverage",
                   "disposition_index", "support_ref_ids", "claim_ids", "gap_index",
                   "conflict_index", "authority_container_ids", "retention", "markdown",
                   "dependency_fingerprint", "generated_at", "version_identity"}
        d = _reject_unknown(d, allowed, "AssembledReport")
        if "version_identity" not in d:
            raise NarrativeSchemaError(
                "AssembledReport 缺 version_identity：报告版本只能来自冻结的 "
                "ReportVersionIdentity（无身份的组装产物一律拒收）")
        return cls(
            schema_version=_require_nonempty(d.get("schema_version"),
                                             "AssembledReport.schema_version"),
            report_id=_require_nonempty(d.get("report_id"), "AssembledReport.report_id"),
            report_version=_require_nonempty(d.get("report_version"),
                                             "AssembledReport.report_version"),
            version_identity=_coerce_version_identity(d.get("version_identity")),
            job_id=_require_nonempty(d.get("job_id"), "AssembledReport.job_id"),
            company_id=_require_nonempty(d.get("company_id"), "AssembledReport.company_id"),
            report_as_of=d.get("report_as_of") or "",
            profile_fingerprint=_require_nonempty(d.get("profile_fingerprint"),
                                                  "AssembledReport.profile_fingerprint"),
            projection_id=_require_nonempty(d.get("projection_id"),
                                            "AssembledReport.projection_id"),
            contract_version=d.get("contract_version") or "",
            contract_fingerprint=d.get("contract_fingerprint") or "",
            assembler_version=_require_nonempty(d.get("assembler_version"),
                                                "AssembledReport.assembler_version"),
            sections=tuple(_section_from_dict(x) for x in d.get("sections") or ()),
            scope_coverage=tuple(dict(x) for x in d.get("scope_coverage") or ()),
            disposition_index=tuple(dict(x) for x in d.get("disposition_index") or ()),
            support_ref_ids=_str_tuple(d.get("support_ref_ids") or (),
                                       "AssembledReport.support_ref_ids"),
            claim_ids=_str_tuple(d.get("claim_ids") or (), "AssembledReport.claim_ids"),
            gap_index=tuple(dict(x) for x in d.get("gap_index") or ()),
            conflict_index=tuple(dict(x) for x in d.get("conflict_index") or ()),
            authority_container_ids=_str_tuple(d.get("authority_container_ids") or (),
                                               "AssembledReport.authority_container_ids"),
            retention=dict(d.get("retention") or {}),
            markdown=d.get("markdown") or "",
            dependency_fingerprint=d.get("dependency_fingerprint") or "",
            generated_at=d.get("generated_at") or "")


# ---------------------------------------------------------------------------
# §4.6 规范载荷：报告身份覆盖的**组装内容**（无环内容寻址）
# ---------------------------------------------------------------------------
#
# 现行实施计划 §4.6 要求 `report_version` 同时绑定两件事：各节 SectionDraft 身份，以及
# 「排除自身版本字段后的 assembled canonical payload」。此前身份只绑定 `body_fingerprint`
# （报告级 markdown），因此「改 Draft ID 而正文不变」与「改未呈现在 Markdown 中但属于报告
# 身份的内容」都不会换版本 —— 身份漏绑定。
#
# 这里给出**唯一**的规范载荷口径：纳入字段与排除字段是两个显式闭集，二者之并必须恰好等于
# `AssembledReport` 的字段全集（模块末尾有闭包断言）。读回时由 `AssembledReport.__post_init__`
# 用同一入口独立重算，声明值与重算值不一致即拒。
#
# 排除的四项各有理由：
#   * `report_version` / `report_id`：自身版本字段，纳入即自引用成环；
#   * `version_identity`：身份对象本身（它的内容已经由 `version_identity` 那一层指纹覆盖）；
#   * `generated_at`：生成时间、运行环境，不是内容身份（§4.6 明确排除生成时间与运行 ID）。

#: 规范载荷的**纳入字段**（顺序固定，供确定性指纹使用）。
ASSEMBLED_PAYLOAD_INCLUDED_FIELDS = (
    "schema_version",
    "job_id",
    "company_id",
    "report_as_of",
    "profile_fingerprint",
    "projection_id",
    "contract_version",
    "contract_fingerprint",
    "assembler_version",
    "sections",
    "scope_coverage",
    "disposition_index",
    "support_ref_ids",
    "claim_ids",
    "gap_index",
    "conflict_index",
    "authority_container_ids",
    "retention",
    "markdown",
    "dependency_fingerprint",
)

#: 规范载荷的**排除字段**：自身版本字段、身份对象本身、生成时间与运行 ID。
ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS = (
    "report_id",
    "report_version",
    "version_identity",
    "generated_at",
)


def _section_payload(section: Any) -> dict:
    """单个 section 的规范投影（对象与 canonical dict 两种形态都收，输出一致）。"""
    def g(name: str) -> Any:
        if isinstance(section, Mapping):
            return section[name]
        return getattr(section, name)

    return {
        "section_id": g("section_id"), "title": g("title"),
        "section_result_id": g("section_result_id"),
        "section_draft_id": g("section_draft_id"),
        "evaluation_id": g("evaluation_id"),
        "narrative_gate_result_id": g("narrative_gate_result_id"),
        "binding_id": g("binding_id"), "decision": g("decision"),
        "coverage_summary": dict(g("coverage_summary") or {}),
        "claim_ids": list(g("claim_ids") or ()),
        "paragraph_ids": list(g("paragraph_ids") or ()),
        "table_ids": list(g("table_ids") or ()),
        "unresolved_ids": list(g("unresolved_ids") or ()),
        "markdown": g("markdown") or "",
    }


def _canonical_index(entries: Any) -> list:
    """**索引**集合的规范序（§4.6）。

    `disposition_index` / `gap_index` 是「一条记录一项」的**目录**，其先后不是内容：写回时
    由本节对象的产出顺序决定，读回时由存储行序（`ORDER BY ordinal`）决定，二者只保证同一
    **集合**。顺序若原样进入规范载荷，「内容相同、行序不同」的两次组装就会换出不同
    `report_version` —— 把行序这种实现细节混进版本身份。故按内容排序（重复项无害）。
    只有这两条**目录**走规范序；`sections[*].claim_ids` / `paragraph_ids` / `unresolved_ids`
    与 `scope_coverage` 的先后是阅读序/声明序，属内容，保持原序。
    """
    return sorted((dict(x) for x in (entries or ())), key=canonical_json)


def assembled_payload_body(**kwargs: Any) -> dict:
    """规范载荷体（§4.6）。入参必须是**恰好**纳入字段闭集，缺一个或多一个都拒绝。"""
    missing = sorted(set(ASSEMBLED_PAYLOAD_INCLUDED_FIELDS) - set(kwargs))
    extra = sorted(set(kwargs) - set(ASSEMBLED_PAYLOAD_INCLUDED_FIELDS))
    if missing or extra:
        raise NarrativeSchemaError(
            f"assembled canonical payload 必须恰好提供纳入字段闭集：缺 {missing}、"
            f"多 {extra}（被排除字段 {list(ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS)} 不得进入载荷）")
    try:
        sections = [_section_payload(s) for s in kwargs["sections"]]
    except (KeyError, AttributeError, TypeError) as exc:
        raise NarrativeSchemaError(f"assembled canonical payload 的 sections 不可投影：{exc!r}")
    return {
        "schema_version": kwargs["schema_version"],
        "job_id": kwargs["job_id"],
        "company_id": kwargs["company_id"],
        "report_as_of": kwargs["report_as_of"],
        "profile_fingerprint": kwargs["profile_fingerprint"],
        "projection_id": kwargs["projection_id"],
        "contract_version": kwargs["contract_version"],
        "contract_fingerprint": kwargs["contract_fingerprint"],
        "assembler_version": kwargs["assembler_version"],
        "sections": sections,
        "scope_coverage": [dict(x) for x in (kwargs["scope_coverage"] or ())],
        "disposition_index": _canonical_index(kwargs["disposition_index"]),
        "support_ref_ids": list(kwargs["support_ref_ids"] or ()),
        "claim_ids": list(kwargs["claim_ids"] or ()),
        "gap_index": _canonical_index(kwargs["gap_index"]),
        "conflict_index": [dict(x) for x in (kwargs["conflict_index"] or ())],
        "authority_container_ids": list(kwargs["authority_container_ids"] or ()),
        "retention": dict(kwargs["retention"] or {}),
        "markdown": kwargs["markdown"] or "",
        "dependency_fingerprint": kwargs["dependency_fingerprint"] or "",
    }


def assembled_payload_fingerprint(**kwargs: Any) -> str:
    """规范载荷的 sha256（读回复算与写入时**共用**的唯一入口）。"""
    body = assembled_payload_body(**kwargs)
    leak = sorted(set(body) & set(ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS))
    if leak:
        raise NarrativeSchemaError(f"assembled canonical payload 含被排除字段: {leak}")
    return _BACKBONE.sha256_canonical(body)


def report_payload_kwargs(source: Any) -> dict:
    """按纳入字段闭集取入参；`AssembledReport` 对象与 `identity_body()` 形态都收。"""
    if isinstance(source, Mapping):
        return {k: source[k] for k in ASSEMBLED_PAYLOAD_INCLUDED_FIELDS}
    return {k: getattr(source, k) for k in ASSEMBLED_PAYLOAD_INCLUDED_FIELDS}


def assembled_payload_fingerprint_of(report: Any) -> str:
    """读回时**独立重算**的载荷指纹；与身份声明值不一致即拒（不得静默当 current）。"""
    return assembled_payload_fingerprint(**report_payload_kwargs(report))


def _assert_assembled_payload_closure() -> None:
    """纳入 ∪ 排除必须恰好等于 `AssembledReport` 的字段全集（新字段不得悄悄落在缝里）。"""
    fields = set(AssembledReport.__dataclass_fields__)
    covered = set(ASSEMBLED_PAYLOAD_INCLUDED_FIELDS) | set(ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS)
    if covered != fields:
        raise NarrativeSchemaError(
            "assembled canonical payload 的纳入/排除字段闭集与 AssembledReport 字段不一致："
            f"未覆盖 {sorted(fields - covered)}、多余 {sorted(covered - fields)}")
    if len(set(ASSEMBLED_PAYLOAD_INCLUDED_FIELDS)) != len(ASSEMBLED_PAYLOAD_INCLUDED_FIELDS):
        raise NarrativeSchemaError("assembled canonical payload 纳入字段含重复项")


_assert_assembled_payload_closure()


def _coerce_version_identity(value: Any) -> "_BACKBONE.ReportVersionIdentity":
    """只接受冻结接口本身（或它的 canonical dict）；任何「长得像」的替代类型一律拒收。"""
    if isinstance(value, _BACKBONE.ReportVersionIdentity):
        return value
    if isinstance(value, Mapping):
        return _BACKBONE.ReportVersionIdentity.from_dict(dict(value))
    raise NarrativeSchemaError(
        "version_identity 必须是 sections.backbone_schema.ReportVersionIdentity"
        "（不得用第二套报告版本类型顶替）")


def _section_from_dict(d: Any) -> AssembledSection:
    d = _reject_unknown(d, {"section_id", "title", "section_result_id", "section_draft_id",
                            "evaluation_id", "narrative_gate_result_id", "binding_id",
                            "decision", "coverage_summary", "claim_ids", "paragraph_ids",
                            "table_ids", "unresolved_ids", "markdown"}, "AssembledSection")
    return AssembledSection(
        section_id=_require_nonempty(d.get("section_id"), "AssembledSection.section_id"),
        title=_require_nonempty(d.get("title"), "AssembledSection.title"),
        section_result_id=_require_nonempty(d.get("section_result_id"),
                                            "AssembledSection.section_result_id"),
        section_draft_id=_require_nonempty(d.get("section_draft_id"),
                                           "AssembledSection.section_draft_id"),
        evaluation_id=_require_nonempty(d.get("evaluation_id"), "AssembledSection.evaluation_id"),
        narrative_gate_result_id=_require_nonempty(
            d.get("narrative_gate_result_id"), "AssembledSection.narrative_gate_result_id"),
        binding_id=_require_nonempty(d.get("binding_id"), "AssembledSection.binding_id"),
        decision=_require_nonempty(d.get("decision"), "AssembledSection.decision"),
        coverage_summary=dict(d.get("coverage_summary") or {}),
        claim_ids=_str_tuple(d.get("claim_ids") or (), "AssembledSection.claim_ids"),
        paragraph_ids=_str_tuple(d.get("paragraph_ids") or (), "AssembledSection.paragraph_ids"),
        table_ids=_str_tuple(d.get("table_ids") or (), "AssembledSection.table_ids"),
        unresolved_ids=_str_tuple(d.get("unresolved_ids") or (), "AssembledSection.unresolved_ids"),
        markdown=d.get("markdown") or "")


def derive_report_version(report: Any) -> str:
    """报告版本**只**从冻结的 `ReportVersionIdentity` 读出（§四：唯一权威根）。

    本函数是「报告自己声称的 report_version」与「权威身份算出的 report_version」之间
    的唯一桥梁：两者不一致即拒，任何第二套派生都会在这里暴露。
    """
    identity = getattr(report, "version_identity", None)
    if not isinstance(identity, _BACKBONE.ReportVersionIdentity):
        raise NarrativeSchemaError(
            "report_version 只能由 sections.backbone_schema.ReportVersionIdentity 派生")
    return _BACKBONE.derive_report_version(identity.content_fingerprint)


def derive_report_id(report: Any) -> str:
    """报告内容句柄：只由权威身份的 `content_fingerprint` 派生（不含时间/路径/attempt）。"""
    identity = getattr(report, "version_identity", None)
    if not isinstance(identity, _BACKBONE.ReportVersionIdentity):
        raise NarrativeSchemaError(
            "report_id 只能由 sections.backbone_schema.ReportVersionIdentity 派生")
    return derive_report_id_for(identity.content_fingerprint)


def derive_report_id_for(content_fingerprint: str) -> str:
    _require_nonempty(content_fingerprint, "derive_report_id_for(content_fingerprint)")
    return "rpt_" + content_fingerprint[:24]


# ---------------------------------------------------------------------------
# required fact 判定（确定性，只依据权威输入自身的类型化字段）
# ---------------------------------------------------------------------------

def current_wire_collection(obj: Any, name: str, what: str) -> Any:
    """读一个**当前 wire 必须携带**的集合字段；字段不存在即 typed fail-closed（§三 G）。

    `getattr(obj, name, ())` 会把「这个对象根本没有这个字段」读成「这个字段是空的」：在必需事实 /
    缺口投影这类集合字段上，那正好等价于把「无从判定」判成「没有必需事实 / 没有缺口」，**缺字段
    反而更容易通过**。空集合是一个结论，不是一个兜底值，两者必须分辨。
    """
    if not hasattr(obj, name):
        raise NarrativeSchemaError(
            f"{what} 缺字段 {name!r}（{type(obj).__name__}）：当前 wire 的集合字段不得用默认空集"
            "兜底（空集是判定结论，不得由缺字段冒充；§三 G）")
    return getattr(obj, name)


def has_registered_gaps(draft: SectionDraft) -> bool:
    """draft 是否**如实登记**了缺口（缺口 id 与它们的权威投影**成对在场**）。

    「本节没有任何正文」有两种截然不同的形态，判据只有这一条：

    * 登记过缺口 —— 本节正文就是缺口附录本身。这不是「凭空产生正文」，而是「如实交代本节
      没有正文」；行业节在真实 Pack 既无可用事实也无材料时就是这一种。缺了它，「无内容但有
      缺口」的章节在定稿与封口处根本无法表达，缺口只能靠 fail-closed 传达，而 fail-closed
      不是人读的缺口显示；
    * 什么都没登记 —— 那才是静默的空章节，照旧 fail-closed。

    判据只读 draft 自己的两个集合字段（缺字段由 `current_wire_collection` typed 拒绝，不用
    默认空集兜底），不引入任何新的「允许空正文」开关，也不放宽任何事实判据：零事实句时
    守恒判据在零句子上是空真，事实侧判定一个字都没有变。

    定稿构造器（`build_section_narrative`）与提交前的门后束检查（`sections.store`）用的是
    **同一个**实现 —— 两处各写一份「缺口已登记」的宽松判据，迟早会漂成两套口径。
    """
    if not isinstance(draft, SectionDraft):
        raise NarrativeSchemaError("has_registered_gaps 的 draft 必须是门前 SectionDraft")
    gap_ids = tuple(current_wire_collection(
        draft, "unresolved_ids", "has_registered_gaps 的 draft") or ())
    gap_projections = tuple(current_wire_collection(
        draft, "unresolved_projections", "has_registered_gaps 的 draft") or ())
    return bool(gap_ids) and bool(gap_projections)


def required_fact_ids(authority_input: Any) -> frozenset[str]:
    """从一份 `WorkerAuthorityInput` 求出「**本节**必须被 claim 或被显式 unresolved」的 fact 集合。

    判据只取权威输入自己的类型化字段，不引入第二套语义：
    - `topic_pack`：fact 支撑的 aspect 中任一 requirement 快照的 `blocking_policy` 非空
      （该 aspect 可 block ⇒ 它的事实不得静默不呈现）。
    - `financial_pack`：artifact 中带数值、且权威侧把归属**放在本节主题内**的事实（本节金额
      权威，丢了就是静默损失）。归属由权威自己的 `topic_for_fact` 给出：它明示归到本节之外的
      fact 是**别的章节**的材料，不是本节的 required —— 把它算进本节，会逼出一个「本节之外」
      内容的缺口，而那个缺口的内容单位不属于任何 DemoScope topic（§七 P0-4 2：内容单位守恒）。
      这些 fact 依然逐条留下去向（`FactNarrativeDisposition`），不会被静默省略。
    - `evidence_note`：全部附注事实。
    """
    kind = getattr(authority_input, "producer_kind", None)
    if kind == "topic_harness":
        required: set[str] = set()
        for pack in authority_input.pack_set.packs:
            blocking_aspects = {
                aspect.aspect_id
                for req in authority_input.pack_set.requirements
                for aspect in getattr(req, "aspects", ())
                if aspect.topic_id == pack.topic_id and aspect.blocking_policy
            }
            for fact in pack.facts:
                if blocking_aspects & set(fact.aspect_ids):
                    required.add(fact.fact_id)
            # 外部事实同样是预验证 authority fact：它归属的 aspect 可 block ⇒ 不得静默不呈现。
            for external in getattr(pack, "external_facts", ()) or ():
                if blocking_aspects & set(getattr(external, "aspect_ids", ()) or ()):
                    required.add(str(external.external_fact_id))
        return frozenset(required)
    if kind == "financial_workflow":
        own_topics = {str(topic_id) for topic_id in authority_input.topic_ids}
        return frozenset(
            str(f.fact_id) for f in authority_input.artifact.facts
            if f.value_text is not None
            and authority_input.topic_for_fact(str(f.fact_id)) in own_topics)
    if kind == "derived_section":
        return frozenset(current_wire_collection(
            authority_input, "required_fact_ids", "derived_section 权威输入") or ())
    raise NarrativeSchemaError(f"required_fact_ids 不认识 producer_kind={kind!r}")


def note_container_id(note_set: Any) -> str:
    """附注事实集的容器 id（与 `ClaimSupportRef.authority_container_id` 同一约定）。"""
    return "note_set:" + note_set.task_id


def support_fact_id(ref: Any) -> str:
    """支撑边绑定的**权威 fact 身份**，按其自身的 authority kind 取值（唯一实现，§六）。

    `topic_pack` / `evidence_note` 的语言是 `fact_id`；`financial_pack` 的语言是
    `financial_fact_id`（schema 层禁止混装）。任何按 `(container, fact)` 坐标查权威事实的
    判据都必须经过这里：若一律读 `fact_id`，财务支撑边会拿到空 key，于是「有事实、有期间」
    的 Claim 被静默判成「没有权威事实」——这是判据失明，不是缺口。
    """
    if str(getattr(ref, "authority_kind", "")) == "financial_pack":
        return str(getattr(ref, "financial_fact_id", None) or "")
    return str(getattr(ref, "fact_id", None) or "")


def proposal_fact_key(prop: Any) -> str:
    """proposal 的 **branch-specific 权威事实身份**（按 authority_kind 取唯一对应字段）。

    唯一入口：任何按 `(container, fact)` 坐标回查权威事实的判据都必须经过它。若一律读
    `fact_id`，财务/附注/外部支撑边会拿到空 key，于是「有事实」的 proposal 被静默判成
    「没有权威事实」——那是判据失明，不是缺口（与 `support_fact_id` 同一纪律）。
    路径 B / context 边本就**不得**携带任何 fact identity，返回空串即表示「无事实身份」。
    """
    field = FACT_FIELD_BY_AUTHORITY_KIND.get(str(getattr(prop, "authority_kind", "")), "")
    return str(getattr(prop, field, None) or "") if field else ""


def binding_fact_key(binding: Any) -> tuple[str, str, str] | None:
    """accepted binding → 权威事实**坐标** `(authority_kind, container, fact_id)`；无事实即 `None`。

    唯一入口：任何按坐标回查权威事实表（`authority_fact_entries` / `scan.facts`）的判据都必须
    经过它。`proposal_fact_key` 只给出该 authority kind **自己的**事实身份字段（一个裸 id），
    不是坐标——裸 id 在两张不同的容器下会撞成同一条，于是「有事实」被静默判成「无事实」或
    「指向别处」。路径 B / context 边本就不得携带任何事实身份，返回 `None`。
    """
    fact_id = proposal_fact_key(binding)
    if not fact_id:
        return None
    return (str(getattr(binding, "authority_kind", "") or ""),
            str(getattr(binding, "authority_container_id", "") or ""), fact_id)


def gate_draft(bundle: SectionDraft, authority_input: Any) -> NarrativeGateResult:
    """确定性 narrative 门（§6.5 第 1 项）：只读**门前候选束**与权威输入，不检索、不生成。

    分工（§16.9）：本门是**门前**的机械门，判定「这份候选束能否进入两道门」。它
    **不**产生 `ClaimBindingDecision`（aggregate cardinality / 完整有序 proposal 集 /
    同一 digest 与逐边 accepted-rejected 记录属 P8，3C）、**不**调用 review LLM、
    **不**做长文语义判断（原子蕴含是 P9 的**唯一**位置）。最终 Narrative（句子/段落/表格
    层）与 `FactNarrativeDisposition` 的 required-fact 闭环发生在门后（3D）。

    检查项（全部落在 typed ID 图上，绝不按文本相似度推断）：
    1. 结构方程：`SectionDraft` 构造期已强制 exact manifest/WMPD 三层集合等式与「候选必须有
       事实性 proposal」；本门只补**需要权威输入才能判定**的那半条——束声明的权威容器必须
       真的在当前 `WorkerAuthorityInput` 内；
    2. 逐 proposal：容器/fact/material/locator 必须能在权威输入里解析。material 期望唯一时
       必须绑定它，且 `payload_ref` 逐字等于该 material 自己的 payload_ref；歧义时阻断（不得
       任选其一）。外部支撑边的 snapshot 载体字段必须与该 `ExternalFact` 自己的载体一致；
    3. 数字权威（§6.2.1 通道 A / §十一）：factual 候选文本里的每个数字 token 必须被**它自己
       路径 A 权威事实**的文本逐字授权。只有路径 B / context 的候选**不得携带任何数字**
       ——数字/日期是高风险硬事实，只能走路径 A 预验证；描述性原子不含数字。
    4. 代理口径（§三 3.6）：绑定自报 `CALCULATED_PROXY` 财务事实的候选必须逐字写出该事实
       自己的口径限定语（`narrative_proxy_fact_unmarked`）；措辞来自权威 `note`，不自造。
    5. 含糊期间（§十二 4）：候选与草稿单元文本不得出现未绑定权威期间的措辞。
    6. required fact 提示：Contract 必需事实若没有任何 factual proposal 引用，出 rework 级
       提示（本门看不到门后对象，该规则属于 `POST_GATE_ADJUDICATED_GATE_RULES`：门后由
       FND/Result 判定它是否已 claim 或已成为显式 unresolved/block，组装器逐条复核该判定）。
    """
    issues: list[NarrativeGateIssue] = []

    def add(rule_id: str, severity: str, location: str, detail: str,
            rework_ref: str | None = None) -> None:
        issues.append(NarrativeGateIssue(rule_id=rule_id, severity=severity,
                                         location=location, detail=detail,
                                         rework_target_ref=rework_ref))

    facts_by_container = _authority_fact_index(authority_input)
    fact_objects = _authority_fact_objects(authority_input)
    materials_by_container = _authority_material_ids(authority_input)
    material_expectations, material_ambiguous = _authority_material_expectations(authority_input)
    missing_containers = sorted(set(bundle.authority_container_ids) - set(facts_by_container))
    if missing_containers:
        add("narrative_container_unknown", "blocking", ",".join(missing_containers[:4]),
            "draft 声明的权威容器不在当前 WorkerAuthorityInput 内")

    # 2. 逐 proposal 落地（容器 / fact / material / payload / locator / snapshot 载体）
    proposals_by_subject: dict[tuple[str, str], list[Any]] = {}
    for prop in bundle.proposed_support_refs:
        subject = (prop.binding_subject_kind, prop.binding_subject_id)
        proposals_by_subject.setdefault(subject, []).append(prop)
        container = str(prop.authority_container_id)
        facts = facts_by_container.get(container)
        if facts is None:
            add("support_container_not_in_authority", "blocking", prop.proposed_support_id,
                f"proposal 的容器 {container!r} 不在当前 WorkerAuthorityInput 内"
                "（权威容器必须来自本次输入，不得自报）")
            continue
        fact_key = proposal_fact_key(prop)
        fact_obj = None
        if fact_key:
            if fact_key not in facts:
                add("support_fact_unknown", "blocking", prop.proposed_support_id,
                    f"proposal 指向容器内不存在的 {FACT_FIELD_BY_AUTHORITY_KIND[prop.authority_kind]}"
                    f"={fact_key!r}（路径 A 必须绑定该 kind 自己的预验证事实身份）")
            else:
                fact_obj = fact_objects.get(container, {}).get(fact_key)
        material_id = prop.material_id
        known_materials = materials_by_container.get(container)
        if material_id and known_materials is not None and material_id not in known_materials:
            add("support_material_unknown", "blocking", prop.proposed_support_id,
                f"proposal 指向的 material {material_id!r} 不在权威容器 {container!r} 内"
                "（material 必须确实存在）")
        # §四：权威事实的引用**确实**能落到本容器内一个 material 时，proposal 必须绑定它，
        # 并逐字绑定该 material 自己的 payload reference —— material 存在却漏绑，等于把
        # 「材料载荷可解析」这一环在血缘里抹掉。
        if (container, fact_key) in material_ambiguous:
            add("support_material_ambiguous", "blocking", prop.proposed_support_id,
                f"fact {fact_key!r} 的引用来源身份在容器 {container!r} 内对应多个 material "
                "候选，且 citation 定位无法唯一确定：不得任选其一，必须收窄引用到具体 material")
        expected = material_expectations.get((container, fact_key))
        if expected is not None and fact_key:
            expected_material_id, expected_payload = expected
            if material_id != expected_material_id:
                add("support_material_unbound", "blocking", prop.proposed_support_id,
                    f"fact {fact_key!r} 的引用来源身份在当前容器内对应 material "
                    f"{expected_material_id!r}，proposal 却绑定 {material_id!r}"
                    "（material 必须绑定到它自己的来源身份上）")
            elif expected_payload is not None and \
                    canonical_json(prop.payload_ref) != canonical_json(expected_payload):
                add("support_payload_mismatch", "blocking", prop.proposed_support_id,
                    f"proposal 的 payload_ref={prop.payload_ref!r} 与权威 material "
                    f"{expected_material_id!r} 自己的 payload_ref={expected_payload!r} 不一致"
                    "（payload 锚点必须逐字回查材料，不得自证）")
        if fact_obj is None:
            continue
        # §六：proposal 的**身份**必须回原始 authority 核验 —— 容器与 fact 都存在还不够，
        # 该 fact 自己的 locator / 引用 / snapshot 载体必须与 proposal 声明的逐字一致，
        # 否则支撑边可以绑一条无关权威事实而不被发现。
        expected_locator = authoritative_locator(prop.authority_kind, fact_obj)
        if expected_locator is not None:
            actual_locator = dict(prop.locator_ref) if prop.locator_ref else None
            if locator_sort_key(actual_locator) != locator_sort_key(expected_locator):
                add("support_locator_mismatch", "blocking", prop.proposed_support_id,
                    f"proposal 的 locator_ref={actual_locator!r} 与权威 fact {fact_key!r} 的 "
                    f"{expected_locator!r} 不一致（locator 必须精确落在权威记录上；"
                    "变体标签与区间语义都要一致）")
        if prop.authority_kind == "external_snapshot":
            expected_carrier = {
                "snapshot_id": str(getattr(fact_obj, "source_snapshot_id", "") or ""),
                "canonical_url": str(getattr(fact_obj, "canonical_url", "") or ""),
                "body_hash": str(getattr(fact_obj, "body_hash", "") or ""),
                "source_policy_version": str(getattr(fact_obj, "source_policy_version", "") or ""),
                "as_of_date": str(getattr(fact_obj, "as_of_date", "") or ""),
            }
            payload = prop.payload_ref if isinstance(prop.payload_ref, Mapping) else {}
            actual_carrier = {key: str(payload.get(key) or "") for key in expected_carrier}
            if actual_carrier != expected_carrier:
                add("support_snapshot_ref_mismatch", "blocking", prop.proposed_support_id,
                    f"proposal 的 snapshot 载体 {actual_carrier!r} 与权威 ExternalFact "
                    f"{fact_key!r} 自己的载体 {expected_carrier!r} 不一致（snapshot 只是来源"
                    "载体：URL/body hash/SourcePolicy/日期必须逐字来自该事实，不得自报）")

    # 3. required fact 提示（门前的半条规则）
    #    Contract 必需事实若没有任何 factual proposal 引用，本束在门前**不可能**呈现它；
    #    门后必须由 `FactNarrativeDisposition` 判定它是 `claimed` 还是已成为显式
    #    unresolved/block（FND 由门后 coordinator 确定性派生，3D）。本门只出 rework 级提示：
    #    门前既不能宣布「已合格呈现」，也不得在看不到权威侧 gap 记录时冒充 blocking 缺口判定
    #    ——「未呈现」本身不是 gap，缺口必须带 Contract 依据（§6.4.1）。
    required = required_fact_ids(authority_input)
    referenced_fact_keys = {
        (str(prop.authority_container_id), proposal_fact_key(prop))
        for prop in bundle.proposed_support_refs if proposal_fact_key(prop)}
    provided_keys = _writer_provided_fact_keys(authority_input)
    for container_id, fact_id in sorted(_authority_selected_facts(authority_input)):
        if str(fact_id) not in required:
            continue
        key = (str(container_id), str(fact_id))
        if key in referenced_fact_keys:
            continue
        # 只对**本次确实交给 Writer、可写**的必需事实出这条返修提示。没交给它的必需事实
        # （aspect 不在本节 / 期间门排除）绝不能变成返修指令：模型看不到它，无论怎么重写都
        # 满足不了，于是整节被反复打回直到 fail-closed —— 那是把「权威侧排除了它」伪装成
        # 「模型没写它」。这类事实的去向仍分别保留：Pack 侧逐条期间拒绝决定
        # （`AuthorityScan.period_exclusions`）+ Contract gap/FND，与被提供面各记各的。
        if provided_keys is not None and key not in provided_keys:
            continue
        add("required_fact_not_proposed", "rework", f"{container_id}:{fact_id}",
            "Contract 必需事实没有任何 factual proposal 引用它：本节在门前无法呈现该事实；"
            "门后必须由 FactNarrativeDisposition 判定它已 claim 或已成为显式 unresolved/block",
            rework_ref=bundle.draft_id)

    # 4. 数字权威（§6.2.1 通道 A / §十一）：门前候选文本里的每个数字 token 必须被**它自己
    #    路径 A 权威事实**的文本逐字授权；只有路径 B / context 支撑的候选**不得携带任何
    #    数字**——数字/日期/币种是高风险硬事实，只能走路径 A 预验证，不得由材料派生绕过。
    #    授权根是权威侧字段（`authority_numeric_texts`），**不是**候选自己的文本（那样任何数字
    #    都会自证合法）。路径 B 的授权面另有两条**独立执行**的网（§二 / P0）：写入侧的
    #    `pack_writer._path_b_high_risk_surfaces`（组装 Draft 之前）与机械门的
    #    `path_b_high_risk_surface`（`cbg-2`，对绕过 Writer 手工构造的 Draft 同样拒绝）。
    #    两者的判据都是「候选文本里出现任何高风险表面即不可由材料派生授权」，**与**该字面成分
    #    是否在材料正文里逐字存在**无关**：原文逐字存在不等于已经资格化。本门这一条只管数字，
    #    与本门、与那两条网都不是包含关系——它们是同一纪律的多道网，不是多套口径。
    for candidate in bundle.claim_candidates:
        tokens = scan_numeric_tokens(candidate.claim_text)
        if not tokens:
            continue
        own = proposals_by_subject.get(("claim_candidate", candidate.candidate_id), ())
        path_a_keys = {(str(p.authority_container_id), proposal_fact_key(p))
                       for p in own
                       if p.authorization_path == "path_a_prevalidated" and proposal_fact_key(p)}
        if not path_a_keys:
            add("narrative_number_unauthorized", "blocking", candidate.candidate_id,
                f"候选文本含数字 {list(tokens)}，但它没有任何路径 A 权威事实支撑："
                "数字/日期/币种属高风险硬事实，只能由预验证权威事实授权（路径 B 只授权"
                "非高风险描述性原子，不得携带数字）",
                rework_ref=bundle.draft_id)
            continue
        allowed: set[str] = set()
        for container_id, fact_key in sorted(path_a_keys):
            fact_obj = fact_objects.get(container_id, {}).get(fact_key)
            if fact_obj is None:
                continue
            kind = next((p.authority_kind for p in own
                         if str(p.authority_container_id) == container_id
                         and proposal_fact_key(p) == fact_key), "topic_pack")
            allowed |= authorized_numeric_tokens(authority_numeric_texts(kind, fact_obj))
        unauthorized = [t for t in tokens if not numeric_token_authorized(t, allowed)]
        if unauthorized:
            add("narrative_number_unauthorized", "blocking", candidate.candidate_id,
                f"候选文本含未被其路径 A 权威事实逐字授权的数字 {unauthorized}"
                "（数字必须逐字出现在权威事实自己的文本/规范值里，不得自造）",
                rework_ref=bundle.draft_id)

    # 4b. 代理口径必须**显式标记**（M930-3 §三 3.6）：财务权威事实若自报口径为
    #     `CALCULATED_PROXY`，绑定它的候选文本必须逐字带上**该事实自己给出的**口径限定语
    #     （`proxy_qualifier`，即权威 `note` 的原话）。缺的**不是数字授权** ——
    #     `authority_numeric_texts` 早已把 `note` 纳入数字授权面，所以「3.2」本来就合法；
    #     缺的是那个限定语本身。少写它，读者会把用代理输入算出来的数值读成受审的精确值，
    #     这是对数值**口径**的误述，与「数字未授权」「期间用含糊措辞顶替」同一族（文本必须
    #     逐字回指权威），故同为 blocking：写作侧仍有 `max_llm_retries` 次按问题文本重写的机会，
    #     额度用尽才 fail-closed，绝不把未标记的代理口径放行（`pack_writer` 的返修/拒绝路径）。
    #     措辞只有一处来源（`proxy_qualifier`）：本门不自造同义词，也不把这条判定塞进
    #     泛化 validator 字符串或别的规则 id。
    for candidate in bundle.claim_candidates:
        own = proposals_by_subject.get(("claim_candidate", candidate.candidate_id), ())
        for prop in own:
            if str(getattr(prop, "authority_kind", "")) != "financial_pack":
                continue
            fact_key = proposal_fact_key(prop)
            if not fact_key:
                continue
            fact_obj = fact_objects.get(str(prop.authority_container_id), {}).get(fact_key)
            if fact_obj is None or not is_proxy_fact(fact_obj):
                continue
            qualifier = proxy_qualifier(fact_obj)
            if qualifier and qualifier not in candidate.claim_text:
                add("narrative_proxy_fact_unmarked", "blocking", candidate.candidate_id,
                    f"候选绑定的财务事实 {fact_key} 自报口径为 {PROXY_STATUS}，但文本没有逐字"
                    f"写出该事实自己的口径限定语「{qualifier}」：读者会把代理口径的数值读成"
                    "受审的精确值（限定语必须逐字来自该事实，不得省略或改写成「精确/准确」）",
                    rework_ref=bundle.draft_id)

    # 5. 含糊期间（§十二 4）：门前候选与草稿单元文本不得出现「报告期」这类无期间措辞。
    #    门前的机械形态只有这半条；期间是否**逐字**来自权威事实、以及表题/列名/单位/主体范围
    #    的逐字回查发生在门后（最终 Narrative 与 answerable 的元数据在门后才形成，3D）。
    for candidate in bundle.claim_candidates:
        hits = vague_period_hits(candidate.claim_text)
        if hits:
            add("narrative_vague_period", "blocking", candidate.candidate_id,
                f"候选文本含未绑定权威期间的含糊措辞 {list(hits)}"
                "（期间必须逐字来自权威事实，不得用「报告期内」顶替）",
                rework_ref=bundle.draft_id)
    for unit in bundle.narrative_draft_units:
        hits = vague_period_hits(unit.text)
        if hits:
            add("narrative_vague_period", "blocking", unit.draft_unit_id,
                f"草稿单元文本含未绑定权威期间的含糊措辞 {list(hits)}",
                rework_ref=bundle.draft_id)

    # 6. 跨 topic 组织（§十四）与事实表面守恒（§十）发生在**门后**：它们要看到最终 Narrative
    #    的句子/表格行与其 Claim 绑定，而门前的候选束还没有最终表达单元（`NarrativeDraftUnit`
    #    只是供 context 定位的草稿单元，不是 final Narrative）。**本门不实现这两条**，它们由
    #    门后的确定性核验承担，且只有一处实现：
    #      * `verify_section_narrative` —— 引用当前性 / 事实表面（数字、主体、期间、否定、
    #        推理连接）逐字可追溯到**本句声明的 Claim** / context 不授权事实 / 处置完备；
    #      * `report_assembler._numeric_assertions` + `_cross_section_conflicts` —— 把**段落
    #        自然组织句与表格行**一起放进跨章节事实表面守恒面（§三 G）。
    #    组织器、章级评估与组装器都调用**同一个**核验函数，因此「谁认为合规」不可能是两套口径。
    return NarrativeGateResult.build(section_draft_id=bundle.draft_id, issues=issues)


def _claim_texts(claim_ids: Sequence[str], draft: SectionDraft,
                 claims_by_id: Mapping[str, Any]) -> tuple[str, ...]:
    """一组 Claim 的**规范文本**（正文事实表面的唯一授权根）。

    只认 canonical `SectionResult.claims` 里的文本：draft 的 support edge 只用来证明
    「这条 Claim 有据」，不能用来改写 Claim 的文本。
    """
    out: list[str] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        text = str(getattr(claim, "claim", "") or getattr(claim, "text", "") or "")
        if text:
            out.append(text)
    return tuple(out)


def _authority_identity_texts(authority_input: Any) -> set[str]:
    """权威**自报的主体身份**文本（§十一：`entity_scope` 只能来自这里或事实自身的 `scope`）。

    只取上游权威自己声明的字段：公司身份，以及财务 artifact 快照声明的口径 / 币种。**绝不**
    放 `report_as_of` 进来——那会把「本期换成上期」这类期间伪造在池内合法化（§十二 1）。
    """
    texts: set[str] = set()
    company_id = getattr(authority_input, "company_id", "")
    if isinstance(company_id, str) and company_id:
        texts.add(company_id)
    if getattr(authority_input, "producer_kind", None) == "financial_workflow":
        snapshot = getattr(getattr(authority_input, "artifact", None), "snapshot", None)
        for value in (getattr(snapshot, "scope", ""), getattr(snapshot, "currency", "")):
            if isinstance(value, str) and value:
                texts.add(value)
    return texts


def _authority_fact_objects(authority_input: Any) -> dict[str, dict[str, Any]]:
    """容器 id → {fact_id: 权威 fact **对象**}（§六：Claim↔fact 的核验必须回原始 authority）。

    与 `_authority_fact_index`（只保留文本）分开：这里保留对象本身，供
    `authoritative_fact_surface` 按 kind 复算权威表面。未知 producer_kind 返回空映射，
    门会因此只做「存在性」核验（不会凭空发明表面）。
    """
    kind = getattr(authority_input, "producer_kind", None)
    out: dict[str, dict[str, Any]] = {}
    if kind == "topic_harness":
        for pack in getattr(getattr(authority_input, "pack_set", None), "packs", ()) or ():
            out[str(pack.pack_id)] = {str(f.fact_id): f
                                      for f in getattr(pack, "facts", ()) or ()}
            # 外部事实的容器是 snapshot 记录（`external_authority_container_id`），与 Pack
            # 容器分开登记：同一本字典里两套 id 空间不得互相冒充。
            for external in getattr(pack, "external_facts", ()) or ():
                out.setdefault(external_authority_container_id(external), {})[
                    str(external.external_fact_id)] = external
    elif kind == "financial_workflow":
        artifact = getattr(authority_input, "artifact", None)
        out[str(artifact.artifact_id)] = {str(f.fact_id): f
                                         for f in getattr(artifact, "facts", ()) or ()}
        note_set = getattr(authority_input, "note_facts", None)
        if note_set is not None:
            out[note_container_id(note_set)] = {str(f.fact_id): f
                                                for f in getattr(note_set, "facts", ()) or ()}
    return out


def _authority_material_ids(authority_input: Any) -> dict[str, set[str]]:
    """容器 id → 该容器内**确实存在**的 material_id 集合（§六：material 必须存在）。

    只登记权威输入里**有**材料的容器；`topic_pack` 的每个 pack 都会登记（哪怕为空集），
    因此「指向本 pack 内不存在的 material」一定被发现。
    """
    kind = getattr(authority_input, "producer_kind", None)
    out: dict[str, set[str]] = {}
    if kind == "topic_harness":
        for pack in getattr(getattr(authority_input, "pack_set", None), "packs", ()) or ():
            out[str(pack.pack_id)] = {str(getattr(m, "material_id", ""))
                                      for m in getattr(pack, "materials", ()) or ()
                                      if getattr(m, "material_id", None)}
    return out


def citation_source_identity(citation: Any) -> str:
    """引用锚点 → 来源身份（**唯一入口**：`harness.topic_schema.citation_source_identity`）。

    §四：material 的身份域与 `CitationRef` 的身份域必须由**同一个类型化函数**打通。任何一处
    手写拼接都会静默失配——material 侧的身份是 `evidence:<id>`，而 `CitationRef.evidence_id`
    是裸 id；拿裸 id 去比 `material.source_identity` 永远不相等，于是「material 确实存在」被
    误判成「不存在」，support edge 静默丢掉 material 绑定与 payload 锚点。
    """
    from harness.topic_schema import citation_source_identity as _impl

    try:
        return _impl(citation)
    except Exception as exc:  # noqa: BLE001 — 未知 ref_type 一律 fail-closed（不猜前缀）
        raise NarrativeSchemaError(f"无法从引用锚点派生来源身份：{exc}") from exc


#: 引用锚点 → material 绑定的**消歧规则集**版本。绑定的结果是事实行自己的
#: `material_id` / `payload_ref`，它会进写作输入清单身份；因此「同一条引用在哪份材料上落地」
#: 这条判据一变，就必须换版本号。
#:
#: `mbind-1`（M930-3 `ndc-2` 批）：判别集**只收窄一次**——当该事实**已核验的输入材料**是
#: 单元素集合时，先用它把同来源身份下的候选收窄，再走原来的按页规则。同一份 Pack 在旧规则下
#: 抛歧义（整节 fail-closed）、在本版下唯一绑定，二者不得共用一个版本号。判据本身**不**放宽：
#: 交集为空、多项、错页、跨 Pack、身份不一致一律 fail-closed，也不按顺序任选。
MATERIAL_BINDING_POLICY_VERSION = "mbind-1"


@dataclass(frozen=True)
class MaterialCandidate:
    """同一来源身份下的**一个** material 候选（§四：候选必须全部保留，不得最后写入者胜出）。

    `page` 只来自该 material 自己的 locator，`payload_ref` 只来自它自己的 payload reference：
    绑定结果必须是同一个真实 material 的两半，不得跨候选拼装。
    """

    material_id: str
    page: int | None = None
    payload_ref: dict | None = None


def material_index(authority_input: Any) -> dict[str, dict[str, tuple[MaterialCandidate, ...]]]:
    """容器 id → {来源身份: (候选 material, …)}（§四 唯一实现）。

    只有 `topic_pack` 容器持有 material；财务 artifact / 附注集没有 material 层，返回空映射
    （调用方不得因此发明一个 material）。

    **同一来源身份下的全部候选都必须保留**：一个 Evidence 完全可能对应多个 OutlineSpan
    （同页多段、跨页续写）。把它折叠成单值就是「最后写入者胜出」——一次静默覆盖会同时伪造
    血缘与 payload 锚点，而且结果取决于 `pack.materials` 的顺序。消歧不在本函数做，由
    `material_binding_for_citation` 按类型化 citation 的定位确定性裁决。
    """
    if getattr(authority_input, "producer_kind", None) != "topic_harness":
        return {}
    out: dict[str, dict[str, tuple[MaterialCandidate, ...]]] = {}
    for pack in getattr(getattr(authority_input, "pack_set", None), "packs", ()) or ():
        entry: dict[str, list[MaterialCandidate]] = {}
        by_material_id: dict[str, MaterialCandidate] = {}
        for material in tuple(getattr(pack, "materials", ()) or ()):
            source_identity = str(getattr(material, "source_identity", "") or "")
            if not source_identity:
                continue
            material_id = str(getattr(material, "material_id", "") or "")
            if not material_id:
                continue
            payload = getattr(material, "payload_ref", None)
            candidate = MaterialCandidate(
                material_id=material_id, page=_material_locator_page(material),
                payload_ref=payload.to_dict() if hasattr(payload, "to_dict") else None)
            prior = by_material_id.get(material_id)
            if prior is not None:
                # 同一 material_id 出现两次且自报不同 → 身份本身就不唯一，绝不静默取其一。
                if prior != candidate:
                    raise NarrativeSchemaError(
                        f"material {material_id!r} 在同一容器内出现多次且定位/payload 不一致："
                        "material 身份必须唯一（不得静默取最后一条）")
                continue
            by_material_id[material_id] = candidate
            entry.setdefault(source_identity, []).append(candidate)
        out[str(getattr(pack, "pack_id", ""))] = {
            identity: tuple(sorted(candidates, key=lambda c: c.material_id))
            for identity, candidates in entry.items()}
    return out


def _material_locator_page(material: Any) -> int | None:
    """material **自己**的定位页号（只读它自己的 locator，不向别处借定位）。

    非 evidence 定位（financial/external）没有页号 → `None`；此时它只能靠「候选唯一」被选中，
    有页号的 citation 不会匹配到它。
    """
    page = getattr(getattr(material, "locator", None), "page", None)
    return int(page) if isinstance(page, int) and not isinstance(page, bool) else None


def fact_verified_input_material_ids(pack: Any, fact: Any) -> frozenset[str] | None:
    """这条 topic_pack 事实**已核验**的输入材料 id 集合；无法核验 ⇒ `None`。

    「已核验」不是自报：它把 `SupportedFact → FactCandidate → eligible
    FactQualificationDecision` 这条资格链**逐环**重算一遍，口径与
    `sections/pack_set.py::_check_decision_inputs` **逐字相同**（三个 digest 按决定自己声明的
    `input_material_ids` 规范序复算）。任一环不成立就说明这条事实的「它出自哪些材料」在本
    Pack 里读不出来 —— 返回 `None`，交由调用方回落到按 citation 定位消歧的旧规则。

    **唯一例外**：候选指针在场、而资格决定**不在本 Pack 内**时**抛**
    `NarrativeSchemaError`。有候选却没有决定是资格链断了，不是「读不出」：静默回落会让绑定
    去猜一条断链事实的出处。
    """
    candidate_id = str(getattr(fact, "candidate_id", "") or "")
    decision_id = str(getattr(fact, "qualification_decision_id", "") or "")
    if not candidate_id and not decision_id:
        return None
    candidates = [c for c in (getattr(pack, "fact_candidates", ()) or ())
                  if str(getattr(c, "candidate_id", "") or "") == candidate_id]
    if len(candidates) != 1:
        #: 候选不在本 Pack 内（或同一 id 出现两次）：链的第一环就不成立 ⇒ 无收窄集。
        return None
    candidate = candidates[0]
    if not decision_id:
        return None
    decisions = [d for d in (getattr(pack, "fact_qualification_decisions", ()) or ())
                 if str(getattr(d, "decision_id", "") or "") == decision_id]
    if len(decisions) != 1:
        raise NarrativeSchemaError(
            f"事实 {getattr(fact, 'fact_id', '?')!r} 回指的资格决定 "
            f"{decision_id!r} 不在本 Pack 内（资格链断，不得静默回落到按引用定位消歧）")
    decision = decisions[0]
    if str(getattr(decision, "candidate_id", "") or "") != candidate_id or \
            str(getattr(decision, "candidate_revision", "") or "") != str(
                getattr(candidate, "candidate_revision", "") or ""):
        return None
    if str(getattr(decision, "candidate_source_kind", "") or "") != "topic_material" or \
            str(getattr(candidate, "candidate_source_kind", "") or "") != "topic_material":
        return None
    if str(getattr(decision, "verdict", "") or "") != "eligible":
        return None
    declared = tuple(str(x) for x in (getattr(decision, "input_material_ids", ()) or ()))
    claimed = tuple(str(x) for x in (getattr(candidate, "material_ids", ()) or ()))
    if len(set(declared)) != len(declared) or sorted(set(declared)) != sorted(set(claimed)):
        return None
    if not declared:
        return None
    material_by_id = {str(m.material_id): m for m in (getattr(pack, "materials", ()) or ())}
    if any(mid not in material_by_id for mid in declared):
        return None
    from harness import topic_schema as _TS

    inputs = [material_by_id[mid] for mid in sorted(declared)]
    locator_digest = _TS.sha256_canonical(
        {"locators": [m.locator.to_dict() for m in inputs]})
    payload_digest = _TS.sha256_canonical(
        {"payloads": [m.payload_ref.to_dict() for m in inputs]})
    if locator_digest != str(getattr(decision, "input_locator_digest", "") or "") or \
            payload_digest != str(getattr(decision, "input_payload_digest", "") or ""):
        return None
    identity_digest = _TS.sha256_canonical({
        "candidate_id": candidate_id,
        "candidate_revision": str(getattr(candidate, "candidate_revision", "") or ""),
        "material_ids": list(sorted(declared)),
        "material_content_hashes": sorted({str(m.content_hash) for m in inputs}),
        "source_identity": str(getattr(decision, "input_source_identity", "") or ""),
        "locator_digest": locator_digest,
        "payload_digest": payload_digest,
    })
    if identity_digest != str(getattr(decision, "input_identity_digest", "") or ""):
        return None
    source_identity = str(getattr(decision, "input_source_identity", "") or "")
    if not source_identity:
        return None
    try:
        fact_source = _TS.authority_source_identity(getattr(fact, "source_authority", None))
    except Exception:  # noqa: BLE001 — 来源身份派生失败 ⇒ 无收窄集（不猜）
        return None
    if fact_source != source_identity:
        return None
    for material in inputs:
        if _TS.authority_source_identity(material.authority_assessment) != source_identity:
            return None
    return frozenset(declared)


def _resolve_material_binding(candidates: Sequence[MaterialCandidate], citation: Any,
                              container_id: str,
                              restrict: frozenset[str] | None = None
                              ) -> tuple[str, dict | None] | None:
    """候选集合 + 类型化 citation → **恰好一个**绑定；0 个取不到，多个 fail-closed。

    消歧只有两步，且**都不猜**：

    1. `restrict`（`mbind-1`，§「唯一收窄」）：调用方已经把该事实**已核验的输入材料**取出来
       （`fact_verified_input_material_ids`），且它是**单元素**集合时，先与候选求交。「事实的
       唯一输入材料」本身就是类型化定位，比页号更精确；交集为空 ⇒ 引用与事实说的不是同一份
       材料（错页／跨 Pack／身份不一致）⇒ fail-closed，绝不退回去任选一个。
    2. citation 自己的定位：有页号时只接受同页 candidate；其余情况要求候选唯一。

    候选集合已按 material_id 规范化排序，因此调用方传入顺序不影响结果。
    """
    if not candidates:
        return None
    if restrict is not None and len(restrict) == 1:
        narrowed = tuple(c for c in candidates if c.material_id in restrict)
        if not narrowed:
            raise MaterialBindingAmbiguityError(
                f"事实的已核验输入材料 {sorted(restrict)} 不在引用锚点在容器 {container_id!r} 内"
                "对应的 material 候选集里（错页／跨容器／身份不一致）：不得退回去任选其一")
        candidates = narrowed
    page = getattr(citation, "page_number", None)
    if page is not None:
        same_page = tuple(c for c in candidates if c.page == page)
        if not same_page:
            return None
        candidates = same_page
    if len(candidates) > 1:
        raise MaterialBindingAmbiguityError(
            f"引用锚点在容器 {container_id!r} 内对应 {len(candidates)} 个 material 候选"
            f"（{', '.join(c.material_id for c in candidates)}）"
            + (f"，且同页 {page} 仍无法消歧" if page is not None
               else "，citation 未提供可消歧的页号")
            + "：不得任选其一，必须收窄引用定位")
    only = candidates[0]
    return (only.material_id, only.payload_ref)


def material_binding_for_citation(authority_input: Any, container_id: str,
                                  citation: Any, *, fact: Any = None, pack: Any = None
                                  ) -> tuple[str, dict | None] | None:
    """引用锚点 → 该容器内**确实存在**的 material 绑定 `(material_id, payload_ref dict)`。

    找不到（容器内没有该来源身份的 material、或页号与任何候选都不符）返回 `None`：本函数不
    发明 material，也不把裸 id 当作来源身份。候选多于一个且无法用**类型化定位**消歧时抛
    `MaterialBindingAmbiguityError`（fail-closed），绝不替调用方挑一个。

    给了 `fact`（连同它所在的 `pack`）时，先取该事实**已核验的输入材料**（
    `fact_verified_input_material_ids`）；取到且唯一才多这一道收窄（见
    :func:`_resolve_material_binding`）。取不到就完全按旧规则办 —— 它不是开关，也不放宽。
    """
    entry = material_index(authority_input).get(str(container_id))
    if not entry:
        return None
    candidates = tuple(entry.get(citation_source_identity(citation)) or ())
    restrict = (fact_verified_input_material_ids(pack, fact)
                if fact is not None and pack is not None else None)
    return _resolve_material_binding(candidates, citation, str(container_id), restrict)


def material_object(authority_input: Any, container_id: str, material_id: str) -> Any:
    """`(容器, material_id)` → 该容器内**确实存在**的 material 对象（找不到即拒）。

    方向与 `material_index` 相反：这里从已确定的 material 身份回到**载体对象**，供路径 B
    （材料派生描述性原子）取该材料自己的权威定位字段。容器/member 缺失或同一 id 出现多次
    即 fail-closed —— 路径 B 的授权基础就是「exact 材料」，身份不唯一时不得任选其一。
    """
    if getattr(authority_input, "producer_kind", None) != "topic_harness":
        raise NarrativeSchemaError(
            "只有 topic_harness 权威输入持有 material：路径 B 不存在于其他 producer_kind")
    packs = tuple(getattr(getattr(authority_input, "pack_set", None), "packs", ()) or ())
    containers = [p for p in packs if str(getattr(p, "pack_id", "")) == str(container_id)]
    if not containers:
        raise NarrativeSchemaError(f"材料容器 {container_id!r} 不在权威输入的 Pack set 内")
    if len(containers) > 1:
        raise NarrativeSchemaError(f"材料容器 {container_id!r} 在 Pack set 内出现多次")
    found = [m for m in tuple(getattr(containers[0], "materials", ()) or ())
             if str(getattr(m, "material_id", "")) == str(material_id)]
    if not found:
        raise NarrativeSchemaError(
            f"材料 {material_id!r} 不在容器 {container_id!r} 内（不得引用不存在的 material）")
    if len(found) > 1:
        raise NarrativeSchemaError(
            f"材料 {material_id!r} 在容器 {container_id!r} 内出现多次（身份必须唯一）")
    return found[0]


def material_citation_ref(authority_input: Any, container_id: str,
                          material_id: str) -> tuple[Any, ...]:
    """路径 B 材料的**权威引用锚点**（material → `CitationRef` 的唯一实现）。

    路径 B 的描述性原子没有预验证 fact 身份，它的授权基础是 exact material 本身，因此引用
    必须由该材料**自己的** authority assessment / locator 派生：evidence 材料取它自己的
    `evidence_id` 与页号，external 材料取它自己的 `source_snapshot_id`；财务快照材料取
    snapshot id。三类身份都由**类型化**判别取出（不切 `source_identity` 字符串），随后必须
    与 `citation_source_identity` 逐字回环一致 —— 否则「材料的来源身份」与「引用的来源身份」
    被两套口径各说一次，静默失配。
    """
    material = material_object(authority_input, container_id, material_id)
    authority = getattr(material, "authority_assessment", None)
    locator = getattr(material, "locator", None)
    from harness.schema import CitationRef
    from harness.topic_schema import (EvidenceAuthorityAssessment,
                                      ExternalSnapshotAuthorityAssessment,
                                      FinancialSnapshotAuthorityAssessment)

    if isinstance(authority, EvidenceAuthorityAssessment):
        page = getattr(locator, "page", None)
        refs = (CitationRef(ref_type="evidence", evidence_id=str(authority.evidence_id),
                            page_number=page if isinstance(page, int) else None),)
    elif isinstance(authority, FinancialSnapshotAuthorityAssessment):
        refs = (CitationRef(ref_type="structured",
                            snapshot_id=str(authority.snapshot_id)),)
    elif isinstance(authority, ExternalSnapshotAuthorityAssessment):
        refs = (CitationRef(ref_type="external",
                            source_snapshot_id=str(authority.source_snapshot_id)),)
    else:
        raise NarrativeSchemaError(
            f"材料 {material_id!r} 的 authority assessment 类型未知："
            f"{type(authority).__name__}（不得臆造引用锚点）")
    source_identity = str(getattr(material, "source_identity", "") or "")
    for ref in refs:
        if citation_source_identity(ref) != source_identity:
            raise NarrativeSchemaError(
                f"材料 {material_id!r} 派生的引用来源身份 {citation_source_identity(ref)!r} 与"
                f"材料自己的 source_identity {source_identity!r} 不一致")
    return refs


def _authority_material_expectations(
        authority_input: Any
) -> tuple[dict[tuple[str, str], tuple[str, dict | None]], set[tuple[str, str]]]:
    """(container, fact_id) → 该**权威 fact 自己的引用**所对应的 material 绑定；外加歧义集合。

    这是「material 存在」的权威判据：由 fact 的 `citation_refs` 派生来源身份，再回到同一
    容器里找 material。找不到就不产生期望（此时不要求绑定），因此不会把「上游没有 material」
    误判成「support edge 漏绑」。

    候选多于一个且无法按 citation 的定位消歧时，这个 fact **没有**可绑定的 material：不猜、
    不任选，改为登记进第二个返回值，由调用方转成阻断门（`support_material_ambiguous`）。
    消歧规则本身只在 `_resolve_material_binding` 里实现一次。
    """
    out: dict[tuple[str, str], tuple[str, dict | None]] = {}
    ambiguous: set[tuple[str, str]] = set()
    if getattr(authority_input, "producer_kind", None) != "topic_harness":
        return out, ambiguous
    index = material_index(authority_input)
    for pack in getattr(getattr(authority_input, "pack_set", None), "packs", ()) or ():
        pack_id = str(getattr(pack, "pack_id", ""))
        entry = index.get(pack_id, {})
        for fact in tuple(getattr(pack, "facts", ()) or ()):
            refs = tuple(getattr(fact, "citation_refs", ()) or ())
            if not refs:
                continue
            try:
                identity = citation_source_identity(refs[0])
            except NarrativeSchemaError:
                continue
            candidates = tuple(entry.get(identity) or ())
            if not candidates:
                continue
            key = (pack_id, str(fact.fact_id))
            try:
                binding = _resolve_material_binding(
                    candidates, refs[0], pack_id, fact_verified_input_material_ids(pack, fact))
            except MaterialBindingAmbiguityError:
                ambiguous.add(key)
                continue
            if binding is not None:
                out[key] = binding
    return out, ambiguous


def _authority_fact_index(authority_input: Any) -> dict[str, dict[str, list[str]]]:
    """容器 id → {fact_id/material_id: 权威侧文本列表}（数字权威比对只用这些文本）。

    fact 一律经 `authority_numeric_texts` 取文本（逐 kind 的命题/规范值字段），**不**把 URL、
    body hash 或页码混进授权池；`external_snapshot` 容器的 id 由
    `external_authority_container_id` 从 snapshot 身份派生（容器 ≠ 事实身份）。
    """
    kind = getattr(authority_input, "producer_kind", None)
    index: dict[str, dict[str, list[str]]] = {}
    if kind == "topic_harness":
        for pack in authority_input.pack_set.packs:
            entry: dict[str, list[str]] = {}
            for fact in pack.facts:
                entry[str(fact.fact_id)] = list(authority_numeric_texts("topic_pack", fact))
            for material in pack.materials:
                entry.setdefault(material.material_id, [material.source_identity])
            index[pack.pack_id] = entry
            # §6.4 第 1 项：TopicPackAuthorityInput 必须携带由 current Pack 持久化引用的
            # formal `ExternalFact`（external_fact_id + 资格决定 + snapshot refs）。它们进入
            # 本索引，路径 A 才可达（否则外部事实即使已进 Pack 也无法被支撑边解析）。
            for external in getattr(pack, "external_facts", ()) or ():
                container = external_authority_container_id(external)
                index.setdefault(container, {})[
                    str(external.external_fact_id)] = list(
                        authority_numeric_texts("external_snapshot", external))
    elif kind == "financial_workflow":
        artifact = authority_input.artifact
        entry = {}
        for fact in artifact.facts:
            entry[str(fact.fact_id)] = list(authority_numeric_texts("financial_pack", fact))
        index[artifact.artifact_id] = entry
        note_set = getattr(authority_input, "note_facts", None)
        if note_set is not None:
            index[note_container_id(note_set)] = {
                str(fact.fact_id): list(authority_numeric_texts("evidence_note", fact))
                for fact in note_set.facts}
    return index


def _authority_fact_periods(authority_input: Any) -> dict[tuple[str, str], str]:
    """权威事实自己的**显式期间**（§十二 1：期间只能来自事实自身，不得由基准期顶替）。

    返回 {(container_id, fact_id): period}；空串表示该事实没有可核验的显式期间（它在写作侧
    不会产出 Claim，只会留下 `period_unresolved` 缺口）。
    """
    kind = getattr(authority_input, "producer_kind", None)
    out: dict[tuple[str, str], str] = {}
    if kind == "topic_harness":
        for pack in authority_input.pack_set.packs:
            for fact in pack.facts:
                out[(pack.pack_id, str(fact.fact_id))] = str(getattr(fact, "period", "") or "")
            # 外部事实没有 `period`：它的显式时点是 `as_of_date`（SourcePolicy 日期），
            # 不得用 benchmark 基准期顶替（§十二 1）。
            for external in getattr(pack, "external_facts", ()) or ():
                out[(external_authority_container_id(external),
                     str(external.external_fact_id))] = str(
                         getattr(external, "as_of_date", "") or "")
    elif kind == "financial_workflow":
        artifact = authority_input.artifact
        for fact in artifact.facts:
            out[(artifact.artifact_id, str(fact.fact_id))] = str(
                getattr(fact, "period", "") or "")
        note_set = getattr(authority_input, "note_facts", None)
        if note_set is not None:
            container = note_container_id(note_set)
            for fact in note_set.facts:
                out[(container, str(fact.fact_id))] = str(
                    getattr(fact, "period", "") or "")
    return out


def _writer_provided_fact_keys(authority_input: Any) -> frozenset[tuple[str, str]] | None:
    """本轮**确实交给 Writer 的**权威事实键 `{(container, fact)}`；无法判定时返回 `None`。

    唯一来源是权威输入自己派生出的**被提供面**（`TopicPackAuthorityInput.writer_face`，由
    `sections.pack_writer.topic_writer_face` 在同一份扫描上派生，与 `authority_facts` 载荷
    逐行一一对应）。本模块**不重算**它：重算就是第二份扫描实现，两份一旦漂移，「门看到的
    面」与「模型看到的面」就又不一致了 —— 而门与被提供面不一致，正是本规则此前把「权威侧
    排除的事实」当成「模型漏写的事实」的成因。

    返回 `None`（调用方退回全量目录）只应出现在没有独立提供面概念的权威上：财务工件、
    `evidence_note`、`derived_section`（其 `selected_facts` 本身就是显式提供面）。生产链上的
    topic 权威一律经 `TopicPackAuthorityInput.create()` 构造，`__post_init__` 必然派生该面。
    """
    face = getattr(authority_input, "writer_face", None)
    if face is None:
        return None
    return frozenset((str(container), str(fact_id)) for container, fact_id in face.provided)


def _authority_selected_facts(authority_input: Any) -> set[tuple[str, str]]:
    """返回 {(container_id, fact_id)} 形式的本轮**被选中**权威事实。"""
    kind = getattr(authority_input, "producer_kind", None)
    out: set[tuple[str, str]] = set()
    if kind == "topic_harness":
        for pack in authority_input.pack_set.packs:
            for fact in pack.facts:
                out.add((pack.pack_id, fact.fact_id))
            for external in getattr(pack, "external_facts", ()) or ():
                out.add((external_authority_container_id(external),
                         str(external.external_fact_id)))
    elif kind == "financial_workflow":
        artifact = authority_input.artifact
        for fact in artifact.facts:
            out.add((artifact.artifact_id, fact.fact_id))
        note_set = getattr(authority_input, "note_facts", None)
        if note_set is not None:
            for fact in note_set.facts:
                out.add((note_container_id(note_set), fact.fact_id))
    elif kind == "derived_section":
        for container_id, fact_id in getattr(authority_input, "selected_facts", ()) or ():
            out.add((container_id, fact_id))
    return out

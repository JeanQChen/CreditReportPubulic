# -*- coding: utf-8 -*-
"""TS5（`TREE_STRUCTURE_IMPLEMENTATION_PLAN.md` §十九）的 **current wire 层**。

本模块只做三件事，且**只**做这三件事：

1. 声明 TS5 的 current wire 类型（`TableObjectV4` / `FinalOutlineSpan` /
   `TableRangeDecision` / `TableRelation` / `FinalComponentBinding` /
   `TableCitableCoverage` / `TableStructureGap` / `FinalMaterialConservation` /
   `FinalMaterialStructureSnapshot` 及其封闭子结构）；
2. 给出它们的**确定性身份**（内容寻址 locator + revision identity）与严格
   `to_dict` / `from_dict` / `create`；
3. 机器强制 **身份有向无环图**：每个对象只能引用严格更早层的对象，任何回指、
   循环指纹或"引用尚未生成的下游对象"在构造期即 fail-closed。

它**不做**任何 I/O、几何、分类或聚合：没有 PDF 读取、没有 pdfplumber、没有
outline/evidence 访问、没有 run 目录、没有 prompt。具体算法分属
`table_geometry` / `table_classification` / `table_builder` /
`final_material_builder` / `final_verifier`。

## 与 legacy `to-3` 的关系（§19.4.2 / §19.15.1）

`document_structure.schema.TableObject` 是**历史兼容对象**（`to-3` + `tb-1`），
本模块不原位改写它，也不允许把 `to-3` 载荷静默解释成 `to-4`。两条路径互不引用：

- legacy：`document_structure.schema.TableObject`（`to-3`，只读历史）；
- current：`document_structure.table_schema.TableObjectV4`（`to-4`，正式消费单位）。

## 身份 DAG（§19.4.4，机器强制）

```text
upstream_dependency_fingerprint
  → TableObjectV4 + FinalOutlineSpan
  → TableRangeDecision
  → FinalComponentBinding + TableCitableCoverage
  → TableRelation
  → FinalMaterialConservation + TableStructureGap
  → FinalMaterialStructureSnapshot
  → VerifiedFinalMaterialStructureSnapshot（运行时能力，见 final_verifier）
```

因此：

- `TableObjectV4` / `FinalOutlineSpan` **不得**携带 decision / binding / coverage /
  relation / conservation / gap / snapshot 的任何 ID 或指纹；
- `TableRangeDecision` 只单向引用已完成的 table / final span；
- `TableRelation` 的端点只能引用已完成对象；
- `FinalMaterialStructureSnapshot` 是唯一终端聚合节点，其身份**不得**反向进入任何
  成员身份或上游依赖束。

`IDENTITY_DAG_EDGES` / `assert_identity_dag_acyclic()` 把这张图写成机器可读数据，
并由 `self_check()` 逐条检验；`evals.test_tree_table_schema` 用它做环检测反例。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    identity,
    locator,
    sha256_canonical,
)
from document_structure.schema import (
    _check_bbox,
    _err,
    _need_bbox,
    _need_bool,
    _need_children,
    _need_enum,
    _need_int,
    _need_int_pair,
    _need_num,
    _need_ordered_pairs,
    _need_schema_version,
    _need_sha256,
    _need_str,
    _need_str_tuple,
    _reject_unknown,
    quantize,
)
from document_structure.span_schema import (
    COMPONENT_LANDINGS,
    COMPONENT_VERDICTS,
    BoundaryFactor,
    _check_own_schema_version,
    _check_registered_constant,
)

__all__ = [
    # 版本常量引用（值本身只在 `versions.py` 里）
    "TABLE_SCHEMA_VERSION", "TABLE_BUILDER_VERSION", "TABLE_CELL_SCHEMA_VERSION",
    "FINAL_SPAN_SCHEMA_VERSION", "TABLE_RANGE_DECISION_SCHEMA_VERSION",
    "TABLE_RELATION_SCHEMA_VERSION", "TABLE_RELATION_BUILDER_VERSION",
    "FINAL_COMPONENT_BINDING_SCHEMA_VERSION",
    "TABLE_CITABLE_COVERAGE_SCHEMA_VERSION",
    "TABLE_STRUCTURE_GAP_SCHEMA_VERSION",
    "FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION",
    "FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION",
    "TABLE_GEOMETRY_VERSION", "TABLE_CLASSIFICATION_PROFILE_VERSION",
    "TABLE_CELL_BLOCK_PROFILE_VERSION", "TS5_FINAL_SPAN_BUILDER_VERSION",
    # 封闭词表
    "STRUCTURE_KINDS", "TABLES_STRUCTURE_CLASSES", "STRUCTURE_STATES",
    "TABLE_ROW_ROLES", "ROW_ROLE_ORDER", "CELL_BLOCK_ROLES",
    "CELL_BLOCK_SEPARATOR", "HEADER_ABSENCE_REASONS",
    "MISSING_FIELD_CODES", "TABLE_RANGE_KINDS", "TABLE_DECISION_KINDS",
    "TABLE_DECISION_TARGETS", "DECISION_TARGET_OF",
    "DECISION_RATIONALE_CODES",
    "TABLE_RELATION_KINDS", "RESOLVABLE_RELATION_KINDS",
    "FUTURE_ONLY_RELATION_KINDS", "RELATION_ENDPOINT_TYPES",
    "TABLE_ENDPOINT_KINDS", "COMPONENT_ENDPOINT_ROLES",
    "TERMINAL_KINDS", "CITABLE_REASONS", "CITABLE_REASON_TRUE",
    "BINDING_ADMISSIONS", "BINDING_REASONS",
    "FINAL_SPAN_ADMISSION_REASONS", "TABLE_ADMISSION_REASONS",
    "PENDING_ADMISSION_REASONS", "REJECTED_ADMISSION_REASONS",
    "COMPONENT_LANDING_ADMISSIONS", "COMPONENT_LANDING_PENDING_REASONS",
    "COMPONENT_LANDING_REJECTED_REASONS", "DOCUMENT_BLOCKING_REASONS",
    "TABLE_GAP_KINDS", "GAP_BLOCKING_KINDS", "CANDIDATE_AUDIT_OUTCOMES",
    "CANDIDATE_AUDIT_UNRESOLVED_REASONS", "CANDIDATE_ADMISSION_BASES",
    "CONSERVATION_LAYER_KINDS", "CONSERVATION_TERM_KINDS",
    "OWNER_KINDS", "UNASSIGNED_REF_KINDS",
    "CONTINUATION_PROOF_KINDS",
    # 子结构
    "TableSourceInterval", "TableCellSourceRef", "TableCellBlock",
    "TableOwnerRef", "TableCellV4", "TableRowV4",
    "TableEndpointRef", "FinalSpanEndpointRef", "ComponentEndpointRef",
    "ReferenceOccurrenceEndpointRef",
    "TableGapEntry", "TableCandidateAuditRow",
    "CitableInterval", "ConservationLayer", "ConservationTerm",
    # 公共 wire 类型
    "TableObjectV4", "FinalOutlineSpan", "TableRangeDecision", "TableRelation",
    "FinalComponentBinding", "TableCitableCoverage", "TableStructureGap",
    "FinalMaterialConservation", "FinalMaterialStructureSnapshot",
    "TableObject",
    # 身份 DAG
    "IDENTITY_DAG_NODES", "IDENTITY_DAG_EDGES", "assert_identity_dag_acyclic",
    "upstream_dependency_fingerprint", "table_source_order_key",
    # 自检
    "TABLE_RECORD_TYPES", "SUBSTRUCTURE_REGISTRY", "self_check", "_main",
]

# ---------------------------------------------------------------------------
# 0. 版本常量别名（**值**只在 `versions.py` 里；这里只做可读的局部名）
# ---------------------------------------------------------------------------

TABLE_SCHEMA_VERSION = V.TABLE_SCHEMA_VERSION
TABLE_BUILDER_VERSION = V.TABLE_BUILDER_VERSION
TABLE_CELL_SCHEMA_VERSION = V.TABLE_CELL_SCHEMA_VERSION
FINAL_SPAN_SCHEMA_VERSION = V.FINAL_SPAN_SCHEMA_VERSION
TABLE_RANGE_DECISION_SCHEMA_VERSION = V.TABLE_RANGE_DECISION_SCHEMA_VERSION
TABLE_RELATION_SCHEMA_VERSION = V.TABLE_RELATION_SCHEMA_VERSION
TABLE_RELATION_BUILDER_VERSION = V.TABLE_RELATION_BUILDER_VERSION
FINAL_COMPONENT_BINDING_SCHEMA_VERSION = V.FINAL_COMPONENT_BINDING_SCHEMA_VERSION
TABLE_CITABLE_COVERAGE_SCHEMA_VERSION = V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION
TABLE_STRUCTURE_GAP_SCHEMA_VERSION = V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION
FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION = \
    V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION
FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION = V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION
TABLE_GEOMETRY_VERSION = V.TABLE_GEOMETRY_VERSION
TABLE_CLASSIFICATION_PROFILE_VERSION = V.TABLE_CLASSIFICATION_PROFILE_VERSION
TABLE_CELL_BLOCK_PROFILE_VERSION = V.TABLE_CELL_BLOCK_PROFILE_VERSION
TS5_FINAL_SPAN_BUILDER_VERSION = V.TS5_FINAL_SPAN_BUILDER_VERSION


# ---------------------------------------------------------------------------
# 1. 封闭词表（§19.4.2 / §19.6 / §19.7 / §19.8 / §19.9）
# ---------------------------------------------------------------------------

#: §19.4.2 / §19.6.1：表结构类型。未通过结构证明的对象**不得**提升为完整表。
STRUCTURE_KINDS: tuple[str, ...] = (
    "headered_grid", "key_value_form", "headerless_grid",
)

#: §19.6.3：结构分类。仍然**只是**结构标签，永不授予数字 authority。
TABLES_STRUCTURE_CLASSES: tuple[str, ...] = (
    "financial_main_statement", "note_table", "ordinary_business_table",
    "unclassified",
)

#: §19.6.1：完成状态。`headerless_grid` 只能 `partial`。
STRUCTURE_STATES: tuple[str, ...] = ("complete", "partial")

#: §19.6.1：行角色。表头 / 表体 / 小计 / 合计分开保留。
TABLE_ROW_ROLES: tuple[str, ...] = ("header", "body", "subtotal", "total")

#: 行角色的**物理顺序**：header 前缀 → body/subtotal 交替 → total 后缀。
ROW_ROLE_ORDER: tuple[str, ...] = ("header", "body", "subtotal", "total")

#: §19.6.2：cell 内部块角色。只表示 cell 内部呈现结构，**不**进入标题树。
CELL_BLOCK_ROLES: tuple[str, ...] = (
    "heading", "paragraph", "list_item", "line", "unclassified",
)

#: §19.6.2：cell canonical text 由 blocks 按此**冻结分隔符**重构。
CELL_BLOCK_SEPARATOR = "\n"

#: §19.6.1：`headered_grid` / `key_value_form` 无表头时的显式原因（不得静默缺省）。
HEADER_ABSENCE_REASONS: tuple[str, ...] = (
    "no_header_evidence",
    "key_value_form_without_header",
)

#: §19.4.2 `missing_or_uncertain_fields` 的封闭字段码（不得自由字符串）。
MISSING_FIELD_CODES: tuple[str, ...] = (
    "title", "unit", "header", "subtotal", "total", "column_semantics",
    "row_boundary", "column_boundary", "continuation", "cell_interior",
)

#: §19.7.1：decision 只覆盖 TS4 的**表 provisional 范围**两种。
TABLE_RANGE_KINDS: tuple[str, ...] = ("table_inside", "table_adjacency")

#: §19.7.1：一条 provisional range 恰好得到其中**之一**。
TABLE_DECISION_KINDS: tuple[str, ...] = (
    "absorbed_as_caption", "absorbed_as_unit", "absorbed_as_note",
    "absorbed_as_table_body", "kept_as_paragraph", "absorbed_into_body",
    "unsupported_table_structure", "unresolved_geometry",
)

#: decision → target 的**唯一**真值表（§19.7.1）。
TABLE_DECISION_TARGETS: dict[str, str] = {
    "absorbed_as_caption": "table",
    "absorbed_as_unit": "table",
    "absorbed_as_note": "table",
    "absorbed_as_table_body": "table",
    "kept_as_paragraph": "final_span",
    "absorbed_into_body": "final_span",
    "unsupported_table_structure": "none",
    "unresolved_geometry": "none",
}

#: 逆查视图（真值表的另一方向）。
DECISION_TARGET_OF: dict[str, tuple[str, ...]] = {
    "table": ("absorbed_as_caption", "absorbed_as_unit", "absorbed_as_note",
              "absorbed_as_table_body"),
    "final_span": ("kept_as_paragraph", "absorbed_into_body"),
    "none": ("unsupported_table_structure", "unresolved_geometry"),
}

#: decision 的依据码（封闭）。它**不是**自由说明文字：只登记"凭什么这样裁决"。
DECISION_RATIONALE_CODES: tuple[str, ...] = (
    "geometry_and_source_order",
    "caption_position_and_text",
    "unit_binding_and_source_order",
    "note_marker_and_geometry",
    "cell_grid_membership",
    "independent_body_boundary",
    "recomputed_merge_boundary",
    "insufficient_structure_proof",
    "geometry_not_uniquely_resolved",
)

#: §19.8.1：关系种类。`reconciles_with` **只**保留 schema 扩展位。
TABLE_RELATION_KINDS: tuple[str, ...] = (
    "introduces", "caption_of", "unit_of", "explains", "footnote_of",
    "continued_by", "references", "reconciles_with",
)

#: 本批**允许构造**的关系种类（§19.8.1）。
RESOLVABLE_RELATION_KINDS: tuple[str, ...] = (
    "introduces", "caption_of", "unit_of", "explains", "footnote_of",
    "continued_by", "references",
)

#: 本批**不得构造**、仅保留 enum 的种类（§19.8.1）。
FUTURE_ONLY_RELATION_KINDS: tuple[str, ...] = ("reconciles_with",)

#: §19.8.1：四种 typed endpoint 的**严格**类型对（不得用 `source_id: str` 旁路）。
RELATION_ENDPOINT_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "introduces": ("FinalSpanEndpointRef", ("TableEndpointRef",)),
    "caption_of": ("ComponentEndpointRef", ("TableEndpointRef",)),
    "unit_of": ("ComponentEndpointRef", ("TableEndpointRef",)),
    "explains": ("TableEndpointRef", ("FinalSpanEndpointRef",)),
    "footnote_of": ("ComponentEndpointRef", ("TableEndpointRef",)),
    "continued_by": ("TableEndpointRef", ("TableEndpointRef",)),
    "references": ("ReferenceOccurrenceEndpointRef", ("TableEndpointRef",)),
}

#: §19.8.1：component endpoint 的 role 词表。
COMPONENT_ENDPOINT_ROLES: tuple[str, ...] = ("caption", "unit", "note")

#: §19.4.2：terminal 沿用 `SpanEvidenceComponent` 的既有封闭真值表。
TERMINAL_KINDS: tuple[str, ...] = ("alignment", "refusal")

#: §19.9.2：逐 cell source ref 的**可引用理由**（封闭）。
CITABLE_REASONS: tuple[str, ...] = (
    "aligned_fragment_closed",
    "verdict_not_aligned",
    "refusal_record",
    "offset_unverifiable",
    "residue_unmapped",
    "fragment_not_closed",
    "component_not_admitted",
)

#: `citable=True` 当且仅当理由恰为这一项（与 TS4 的 `admitted` 同一模式）。
CITABLE_REASON_TRUE = "aligned_fragment_closed"
CITABLE_REASON_FALSE: tuple[str, ...] = tuple(
    r for r in CITABLE_REASONS if r != CITABLE_REASON_TRUE)

#: §19.9.1：component 的四种终态。
BINDING_ADMISSIONS: tuple[str, ...] = (
    "final_span", "table_object", "pending", "rejected",
)

#: §19.9.1：admission 理由（封闭）。
BINDING_REASONS: tuple[str, ...] = (
    "absorbed_into_table",
    "kept_as_final_paragraph",
    "rebuilt_as_final_span",
    "unsupported_table_structure",
    "unresolved_geometry",
    "upstream_table_scope_miss",
    "provenance_incomplete",
    "empty_source_text",
    "structural_heading_only",
    "non_content_region",
    "outside_formal_body",
    "root_identity_mismatch",
)

FINAL_SPAN_ADMISSION_REASONS: tuple[str, ...] = (
    "kept_as_final_paragraph", "rebuilt_as_final_span",
)
TABLE_ADMISSION_REASONS: tuple[str, ...] = ("absorbed_into_table",)
PENDING_ADMISSION_REASONS: tuple[str, ...] = (
    "unsupported_table_structure", "unresolved_geometry",
    "upstream_table_scope_miss", "provenance_incomplete",
    "root_identity_mismatch",
)
REJECTED_ADMISSION_REASONS: tuple[str, ...] = (
    "empty_source_text", "structural_heading_only", "non_content_region",
    "outside_formal_body",
)

#: §19.9.1 的 11 类 `COMPONENT_LANDINGS` → 唯一允许终态。**这是 TS5 的唯一映射函数**：
#: builder / verifier / runner 都不得各自再解释一遍。
COMPONENT_LANDING_ADMISSIONS: dict[str, tuple[str, ...]] = {
    "body_span": ("final_span", "pending"),
    "table_inside": ("table_object", "final_span", "pending"),
    "table_adjacency": ("table_object", "final_span", "pending"),
    "body_unassigned": ("table_object", "pending"),
    "body_empty": ("rejected", "pending"),
    "heading_node": ("rejected", "pending"),
    "formal_unassigned": ("table_object", "pending"),
    "non_content": ("rejected", "pending"),
    "outside_body": ("rejected", "pending"),
    "alignment_offset_unverifiable": ("pending",),
    "alignment_residue_unmapped": ("pending",),
}

#: landing → 允许的 pending 理由（封闭）。`pending` 不得配任意理由。
#:
#: §19.9.1 末段（决定性）：一个 component 若**跨越**表体与正文、跨**两个** table
#: object、在本 landings 上留有未归属残余区间，或无法唯一划分成表/span 片段，
#: 则它必须落 `pending(provenance_incomplete)` —— 因此 `table_inside` /
#: `table_adjacency` 除"结构不可表达 / 几何未唯一解析"外，还必须承认这一条。
#: 这是**收紧**（多了一个诚实的 pending 出口），不构成任何新的准入路径。
COMPONENT_LANDING_PENDING_REASONS: dict[str, tuple[str, ...]] = {
    "body_span": ("upstream_table_scope_miss",),
    "table_inside": ("unsupported_table_structure", "unresolved_geometry",
                     "provenance_incomplete"),
    "table_adjacency": ("unsupported_table_structure", "unresolved_geometry",
                        "provenance_incomplete"),
    "body_unassigned": ("provenance_incomplete",),
    "body_empty": ("root_identity_mismatch",),
    "heading_node": ("upstream_table_scope_miss",),
    "formal_unassigned": ("provenance_incomplete",),
    "non_content": ("upstream_table_scope_miss",),
    "outside_body": ("upstream_table_scope_miss",),
    "alignment_offset_unverifiable": ("provenance_incomplete",),
    "alignment_residue_unmapped": ("provenance_incomplete",),
}

#: landing → 允许的 rejected 理由（封闭）。
COMPONENT_LANDING_REJECTED_REASONS: dict[str, tuple[str, ...]] = {
    "body_empty": ("empty_source_text",),
    "heading_node": ("structural_heading_only",),
    "non_content": ("non_content_region",),
    "outside_body": ("outside_formal_body",),
}

#: §19.9.1：必须**阻断该文档 final capability 与 TS5 关闭**的理由。
#: 这些不是"可保留的 P2"。
DOCUMENT_BLOCKING_REASONS: tuple[str, ...] = (
    "upstream_table_scope_miss", "root_identity_mismatch",
)

#: §19.4.3：结构缺口种类（封闭）。
TABLE_GAP_KINDS: tuple[str, ...] = (
    "unsupported_table_structure", "unresolved_geometry",
    "upstream_table_scope_miss", "visual_object_not_table",
    "ambiguous_continuation", "provenance_incomplete",
    "root_identity_mismatch",
)

#: 其中必须阻断 final capability 的缺口（与 `DOCUMENT_BLOCKING_REASONS` 同一语义）。
GAP_BLOCKING_KINDS: tuple[str, ...] = (
    "upstream_table_scope_miss", "root_identity_mismatch",
)

#: §19.14.1 候选审计的三态结局（封闭、互斥、穷尽）。
CANDIDATE_AUDIT_OUTCOMES: tuple[str, ...] = (
    "accepted", "rejected", "unresolved",
)

#: `outcome == "unresolved"` 的全部合法理由码：这些都是"几何无法裁决"，必须显式
#: 保留为缺口，不得按"取第一个/取最大"强行收敛（§19.5.3 / §19.12.2-6）。
CANDIDATE_AUDIT_UNRESOLVED_REASONS: tuple[str, ...] = (
    "candidate_straddles_frozen_range",
    "candidate_not_uniquely_mapped",
    "geometry_overlaps_frozen_regular_span",
)

#: §19.5.1 统一硬门的**准入依据**码（封闭、互斥、穷尽）。它与
#: `CANDIDATE_AUDIT_UNRESOLVED_REASONS` 是两条**互不重叠**的轴：
#:
#: - 非 `accepted` 的候选只带 `reason`（拒绝／未决理由码），`admission_basis` 必须为
#:   `None`；
#: - `accepted` 的候选只带 `admission_basis`，`reason` 必须为 `None`。
#:
#: 这样"这张表凭哪一条判据准入"是可逐行回查的**独立类型化字段**，不需要把它塞进
#: `reason`（`accepted` 行禁止携带，见 `TableCandidateAuditRow.__post_init__`）、
#: `semantic_tags` 或缺口 `detail`。
CANDIDATE_ADMISSION_BASES: tuple[str, ...] = (
    # 候选**完全**落入其唯一冻结表范围（`frozen_overlap_ratio >= 0.95` 的既有通道）。
    "frozen_range_contained",
    # 候选自带网格骨架、且**恰好包含**其唯一冻结 `table_inside`／`table_adjacency`
    # 范围；与同页冻结 `regular` 正文无重叠（§19.5.1 相邻冻结范围裁定）。
    "contained_frozen_owner",
    # 候选是**一串连续相邻冻结表范围的并集**本身（`tb-4`）：矩形逐分量等于该串
    # 真实矩形的并集、来源通道为 `frozen_range`、可回查成员范围 id 全部在册。它只
    # 说明"凭哪一条判据准入"，形状门与其余判据照旧（§19.5.1 相邻冻结范围裁定）。
    "contiguous_frozen_union",
    # 候选自带网格骨架，且同页被它**≥0.95 包含**的冻结表范围**全部**恰好构成某一条
    # 连续相邻串里的**一段连续子串**（≥2 段），而候选与其余同页冻结表范围**互不相交**
    # （既不吞下第二张表，也不把任何一段切成两半）；与同页冻结 `regular` 正文无重叠。
    # 这就是"上游把**一张**物理表按文字行段切成了多段"的完整证明
    # （§19.5.1 相邻冻结范围裁定；乙：一张物理表对应一个有界候选）。
    "contained_frozen_run",
)

#: §19.9.3：四层守恒（分别验证、**不得**跨层相加）。
CONSERVATION_LAYER_KINDS: tuple[str, ...] = (
    "disposition", "component", "layout_text", "evidence_interval",
)

#: 每一层守恒的既定分项（封闭；顺序固定，便于逐层比对）。
#:
#: `layout_text` 层的 `registered_deferred`（`fmc-2` 新增）：落在表相关文本域内、
#: **已被正式延期**的字符。它与 `residual_gap` 的区别是**可复核的资格**而不是口径：
#: 一段字符只有在"恰好被一个 component 的 layout hit 覆盖、该 component 的终态是
#: `pending`/`rejected`、且缺口台账里有与该 component 精确对应的 typed gap"时才计入
#: 本项；任何一条不成立都必须留在 `residual_gap` 里并阻止本层 balanced。
#: 它不是兜底项：它**不允许**收留"没有 component"或"覆盖不唯一"的字符。
#:
#: `layout_text` 层的 `non_semantic_whitespace`（同一轮 `fmc-2` 新增）：落在表相关
#: 文本域内、**整段恰为空白**（`text[a:b].strip() == ""`）的字符。它是"非语义空白"
#: 的**精确范围**记账，不是兜底项，判据只有一条且可逐字符复核：
#:
#: - 进入条件：该区间的原文切片 `text[a:b].strip() == ""`（空白之外的一个字符都不进）。
#: - **不得**收留：任何非空白正文（那是 `residual_gap`）、任何重复认领 / 越界 / 两类
#:   同时主张的区间（那走各自的问题码），以及任何"没有 typed gap 的**内容**"——空白
#:   不是内容，但"因为没找到 gap 就把它算成空白"是禁止的，因此判据只看原文，不看
#:   gap、不看 component、不看表格。
#: - 因此它**不**放宽质量门：`residual_gap` 仍必须为零、层内 `problems` 仍必须为空，
#:   有凭据的延期仍优先计入 `registered_deferred`（它比"非语义空白"信息量更高）。
CONSERVATION_LAYER_TERMS: dict[str, tuple[str, ...]] = {
    "disposition": ("absorbed_to_table", "final_paragraph",
                    "pending_or_unsupported"),
    "component": ("final_span", "table_object", "pending", "rejected"),
    "layout_text": ("cell_source_ref", "caption_unit_note_ref",
                    "final_paragraph_ref", "registered_deferred",
                    "non_semantic_whitespace", "residual_gap"),
    "evidence_interval": ("preserved", "missing", "duplicated"),
}

#: `fmc-1` 的历史分项表（冻结副本）。schema 升到 `fmc-2` 之后，current reader 一律
#: 拒绝 `fmc-1`；历史读回必须走 `read_legacy_conservation`，它按**当时的**分项表
#: 校验载荷形状。保留这张表是为了让"读回历史产物"与"静默按新语义解释"在代码层
#: 就是两件事，而不是靠调用方自觉。
LEGACY_CONSERVATION_LAYER_TERMS: dict[str, tuple[str, ...]] = {
    "disposition": ("absorbed_to_table", "final_paragraph",
                    "pending_or_unsupported"),
    "component": ("final_span", "table_object", "pending", "rejected"),
    "layout_text": ("cell_source_ref", "caption_unit_note_ref",
                    "final_paragraph_ref", "residual_gap"),
    "evidence_interval": ("preserved", "missing", "duplicated"),
}

#: 守恒分项的语义类别（封闭）。
CONSERVATION_TERM_KINDS: tuple[str, ...] = ("count", "interval", "character")

#: 每一层的**合计**分项名（§19.9.3）。它不属于任何一层的分项表：一层的"合计"
#: 与"分项"是同一层内的两个角色，合计的语义类别由它所属的层决定（见
#: `CONSERVATION_LAYER_TERMS`）。因此它是**唯一**一个不参与
#: `CONSERVATION_TERM_KIND_OF` 查表的名字，也是唯一允许在 `ConservationTerm`
#: 构造期跳过"分项 → 类别"校验的名字。除它之外，任何未登记名字一律 fail-closed：
#: 借这个口子塞进一个"自由分项"是不可能的（它仍必须匹配所在层的类别）。
CONSERVATION_TOTAL_TERM = "total"

#: 每层的分项 → 语义类别（封闭）。
CONSERVATION_TERM_KIND_OF: dict[str, str] = {
    "absorbed_to_table": "count", "final_paragraph": "count",
    "pending_or_unsupported": "count",
    "final_span": "count", "table_object": "count", "pending": "count",
    "rejected": "count",
    "cell_source_ref": "character", "caption_unit_note_ref": "character",
    "final_paragraph_ref": "character", "registered_deferred": "character",
    "non_semantic_whitespace": "character",
    "residual_gap": "character",
    "preserved": "interval", "missing": "interval", "duplicated": "interval",
}

#: §19.9.3：**必须为零**才允许所在层 `balanced=true` 的分项。
#:
#: 这一项把"未解释残余为 0"从一句人工承诺变成构造期断言：只要 `layout_text` 的
#: `residual_gap` 不为 0，该层就不能 balanced，文档级 `balanced` 随之不能成立，
#: 复核层拒发 capability、验收机的守恒门失败。它**不**通过把残余改名 / 排除 /
#: 归入兜底项来实现，而是要求残余真的被解释掉。
#:
#: `non_semantic_whitespace` **不**在这一项里：非语义空白是有原凭据（原文逐字符
#: 为空白）的正当记账，它不该阻止签发。但"正当"不等于"免检"——它仍受算术守恒约束
#: （分项之和必须恰等于合计），且它与 `residual_gap` 是**两个**分项：把非空白正文
#: 划进空白项会让 `residual_gap` 变小而合计不变，从而在 `sum == total` 上暴露不出来，
#: 所以判定空白与否只读原文切片，不读任何 gap / component / 表格状态。
CONSERVATION_ZERO_REQUIRED_TERMS: tuple[str, ...] = ("residual_gap",)

#: 可以作为 `registered_deferred` 依据的 typed gap（**封闭**）：已正式登记的
#: 延期 / 未归属终态。
#:
#: **不含**阻断性缺口：`upstream_table_scope_miss` / `root_identity_mismatch` 表示
#: 上游冻结范围与已验证几何互相矛盾——那是**尚未解释的冲突**，不是一次正当延期，
#: 必须原样留在 `residual_gap` 里并阻止 `balanced` 与 `issued`。这样，"文档被阻断"
#: 与"残余已解释"就不会互相冒充。
CONSERVATION_DEFERRED_GAP_KINDS: tuple[str, ...] = tuple(
    kind for kind in TABLE_GAP_KINDS if kind not in GAP_BLOCKING_KINDS)


def is_non_semantic_whitespace(slice_text: Any) -> bool:
    """`non_semantic_whitespace` 的**唯一**判据：区间原文切片是否整段恰为空白。

    只读一个东西：原文切片的 `.strip()` 是否为空串。判据**刻意**不看 gap、不看
    component、不看 binding、不看表格状态、不看坐标是否在表相关区域内——因为一旦
    "没有 typed gap" 之类的信息能影响这个判定，`residual_gap` 就可以被有选择地
    搬进空白项而合计不变（`sum == total` 查不出来），质量门就被绕过了。

    builder 与独立 verifier 都调用本函数：两份实现不可能各自漂移。非字符串输入一律
    返回 `False`（fail-closed：宁可留在 `residual_gap`，也不放行）。
    """
    return isinstance(slice_text, str) and slice_text.strip() == ""


def conservation_layer_unit(layer_kind: str) -> str:
    """守恒层的坐标单位（由该层第一个分项的语义类别决定；同层分项同类）。"""
    terms = CONSERVATION_LAYER_TERMS.get(layer_kind)
    if not terms:
        raise SchemaValidationError(f"未登记的守恒层 {layer_kind!r}")
    return CONSERVATION_TERM_KIND_OF[terms[0]]


def conservation_term_value(term: Any) -> int:
    """一个 `ConservationTerm` 在**它所属层**坐标系下的标量值。

    `count` / `character` / `interval` 三选一，与 `term_kind` 一一对应；`total` 的
    `term_kind` 就是所在层的单位，因此同一函数对分项与合计都成立。
    """
    kind = getattr(term, "term_kind", None)
    if kind == "count":
        return int(term.count)
    if kind == "character":
        return int(term.char_count)
    if kind == "interval":
        return int(term.interval_length)
    raise SchemaValidationError(f"未登记的守恒分项语义类别 {kind!r}")


def conservation_layer_eligible(layer_kind: str, values: Mapping[str, int],
                                total: int, problems: Sequence[str]) -> bool:
    """§19.9.3 的**唯一**分层守恒资格函数（builder / schema / verifier / runner 共用）。

    `balanced` 不是"算术和相等"这一个条件。它**同时**要求：

    1. 该层坐标系下的分项之和恰等于合计（算术守恒）；
    2. 该层 `problems` 为空——重复、越界、负长度、身份冲突、区间不齐等都必须
       以问题码的形式留在这里，而不是被算术平均掉；
    3. `CONSERVATION_ZERO_REQUIRED_TERMS` 里的分项为 0（未解释残余必须为零）。

    三者缺一即资格不成立。因此"`problems` 非空却 `balanced=true`"在本函数下不可能
    出现，而且这是**唯一**的实现：构造期、反序列化、独立复核与验收机的判定都调用它，
    不存在第二套口径。

    `values` 里出现的未登记名字被忽略（判定只看本层的登记分项）；缺项按 0 计。
    """
    terms = CONSERVATION_LAYER_TERMS.get(layer_kind)
    if not terms:
        raise SchemaValidationError(f"未登记的守恒层 {layer_kind!r}")
    got = {name: int(values.get(name, 0)) for name in terms}
    if sum(got.values()) != int(total):
        return False
    for name in CONSERVATION_ZERO_REQUIRED_TERMS:
        if got.get(name, 0) != 0:
            return False
    return not tuple(problems)


def conservation_layer_values(layer: Any) -> dict:
    """一层的分项名 → 本层坐标系下的标量值（供资格函数与重算路径共用）。"""
    return {term.term: conservation_term_value(term) for term in layer.terms}


def conservation_eligible(conservation: Any) -> bool:
    """文档级最终守恒资格（§19.9.3）：四层各自成立**且**文档级 `problems` 为空。

    文档级 `problems` 是四层问题码的并集与文档级问题的合并；它与层内 `problems`
    都必须为空。因此"层算术凑平、层问题为空、文档级却记着问题"不构成合格。
    """
    if tuple(conservation.problems):
        return False
    for layer in conservation.layers:
        if not conservation_layer_eligible(
                layer.layer_kind, conservation_layer_values(layer),
                conservation_term_value(layer.total), layer.problems):
            return False
    return True


#: `fmc-2` 相对 `fmc-1` 新增的分项名（冻结清单）。历史读回按名字把这两项从 current
#: 词表里剔除，从而得到"当时的"分项表——**不是**手工再抄一份，避免两份词表各自漂移。
FMC2_ADDED_TERMS: tuple[str, ...] = (
    "registered_deferred", "non_semantic_whitespace")

#: `fmc-1` 的分项 → 语义类别（冻结副本，供历史读回按**当时的**口径校验）。
LEGACY_CONSERVATION_TERM_KIND_OF: dict[str, str] = {
    name: kind for name, kind in CONSERVATION_TERM_KIND_OF.items()
    if name not in FMC2_ADDED_TERMS}

#: 各 legacy 守恒版本的"当时的"分项表与类别表。
#:
#: `fmc-2 → fmc-3` **没有**增删分项名（升版原因是 `registered_deferred` 的**资格**
#: 收紧了：`fmc-2` 只承认 component 终态 + 台账 typed gap，`fmc-3` 还承认被缺口
#: `source_intervals` 精确覆盖的区间），所以 `fmc-2` 的分项表与 current 完全一致，
#: 但它的**结论**（同一份载荷里哪些字符算 residual）与 `fmc-3` 不同，因此仍然只能
#: 走本历史入口，不得被 current reader 静默接受。
LEGACY_CONSERVATION_TERMS_BY_VERSION: dict[str, dict[str, tuple[str, ...]]] = {
    "fmc-1": LEGACY_CONSERVATION_LAYER_TERMS,
    "fmc-2": {k: tuple(v) for k, v in CONSERVATION_LAYER_TERMS.items()},
}

#: 同上：各 legacy 版本的分项 → 语义类别。
LEGACY_CONSERVATION_TERM_KIND_BY_VERSION: dict[str, dict[str, str]] = {
    "fmc-1": LEGACY_CONSERVATION_TERM_KIND_OF,
    "fmc-2": dict(CONSERVATION_TERM_KIND_OF),
}

#: 各 legacy 守恒版本的读回附注（说明"为什么这份载荷的 `balanced` 不能当 current 结论"）。
LEGACY_CONSERVATION_NOTES: dict[str, str] = {
    "fmc-1": ("`fmc-1` 的历史读回：`residual_gap` 同时表示『已被 typed gap 延期』、"
              "『整段恰为空白』与『真正无任何归属』三者，因此其 `balanced` **不得**"
              "被当作 current 结论；current 结论只能由当前版本重算得出。"),
    "fmc-2": ("`fmc-2` 的历史读回：分项名与 current 一致，但 `registered_deferred` 的"
              "资格较窄（只承认 component 终态 + 台账 typed gap），同一份载荷在 "
              "`fmc-3` 下会把一部分 `residual_gap` 字符算作已正式延期。因此其 "
              "`balanced` **不得**被当作 current 结论；current 结论只能由当前版本"
              "重算得出。"),
}


def read_legacy_conservation(payload: Any) -> dict:
    """legacy 守恒载荷的**显式历史入口**（只读、不可当作 current）。

    current reader（`FinalMaterialConservation.from_dict`）对所有登记在册的 legacy
    版本一律 fail-closed 拒绝：`fmc-1` 的 `residual_gap` 把"已被 typed gap 延期"、
    "整段恰为空白"与"真正未解释"三者混在一起；`fmc-2` 虽然分项名相同，但
    `registered_deferred` 的资格比 current 窄，同一份载荷的含义不同。两者都不能按
    current 语义读。需要审计历史产物时走本函数：它按**该版本当时的**分项表与类别表
    校验载荷形状、独立重算每层的算术，并把结果标成 `historical_only`。它**不**返回
    current 对象，也**不**改写任何历史产物。
    """
    t = "read_legacy_conservation"
    if not isinstance(payload, Mapping):
        _err(t, "载荷必须为映射")
    version = payload.get("schema_version")
    if version not in LEGACY_CONSERVATION_TERMS_BY_VERSION:
        _err(t, f"本入口只受理 {tuple(LEGACY_CONSERVATION_TERMS_BY_VERSION)}，"
                f"得到 {version!r}")
    expected_terms = LEGACY_CONSERVATION_TERMS_BY_VERSION[version]
    kind_of = LEGACY_CONSERVATION_TERM_KIND_BY_VERSION[version]
    layers = payload.get("layers")
    if not isinstance(layers, list) or not layers:
        _err(t, "layers 必须为非空数组")
    kinds = tuple(layer.get("layer_kind") for layer in layers)
    if kinds != CONSERVATION_LAYER_KINDS:
        _err(t, f"四层必须恰为 {CONSERVATION_LAYER_KINDS}（固定顺序），得到 {kinds}")
    rows: list = []
    for layer in layers:
        layer_kind = layer["layer_kind"]
        terms = layer.get("terms")
        if not isinstance(terms, list):
            _err(t, f"{layer_kind} 的 terms 必须为数组")
        got = tuple(term.get("term") for term in terms)
        expected = expected_terms[layer_kind]
        if got != expected:
            _err(t, f"{layer_kind} 的历史分项必须恰为 {expected}，得到 {got}")
        values: dict = {}
        for term in terms:
            name = term["term"]
            kind = term.get("term_kind")
            if kind_of.get(name) != kind:
                _err(t, f"{layer_kind} 的分项 {name!r} 类别不符：{kind!r}")
            values[name] = {"count": int(term.get("count", 0)),
                            "character": int(term.get("char_count", 0)),
                            "interval": int(term.get("interval_length", 0))}[kind]
        total_term = layer.get("total")
        unit = kind_of[expected[0]]
        total = {"count": int(total_term.get("count", 0)),
                 "character": int(total_term.get("char_count", 0)),
                 "interval": int(total_term.get("interval_length", 0))}[unit]
        rows.append({
            "layer_kind": layer_kind,
            "values": values,
            "total": total,
            "arithmetic_balanced": sum(values.values()) == total,
            "recorded_balanced": bool(layer.get("balanced")),
            "problem_count": len(layer.get("problems") or ()),
        })
    return {
        "schema_version": version,
        "status": "historical_only",
        "layers": rows,
        "problems": list(payload.get("problems") or ()),
        # 附注 = 该版本自身的语义差异 + 一条**在读取时求值**的后缀（点名当次
        # current 版本）。后缀不写死在 `LEGACY_CONSERVATION_NOTES` 里，否则下一次
        # 升版会让历史读回的说明指向一个已经不是 current 的版本号。
        "note": LEGACY_CONSERVATION_NOTES[version] + (
            f"（本次读回发生于 current 版本 "
            f"{FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION} 下：current 结论只能由 "
            f"{FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION} 重算得出。）"),
    }


def read_legacy_structure_gap(payload: Any) -> dict:
    """`tsg-1` 缺口台账的**显式历史入口**（只读、不可当作 current）。

    `tsg-1` 的 `TableGapEntry` 没有 `source_intervals`：它只能说"这里有一个
    `unsupported_table_structure` 缺口"，不能说"它覆盖了哪些真实字符区间"。因此在
    `tsg-1` 载荷上，"某段字符已被正式延期"**不可判定**——按 `tsg-2` 语义读会把
    "无人认领"错读成"已延期"。current reader（`TableStructureGap.from_dict`）因此对
    `tsg-1` 一律 fail-closed 拒绝；需要审计历史产物时走本函数，它按 `tsg-1` 的字段集
    校验形状、核对缺口码与阻断位、统计已知缺口，并把结果标成 `historical_only` 且
    显式声明 `precise_source_intervals = False`。
    """
    t = "read_legacy_structure_gap"
    if not isinstance(payload, Mapping):
        _err(t, "载荷必须为映射")
    version = payload.get("schema_version")
    if version != "tsg-1":
        _err(t, f"本入口只受理 `tsg-1`，得到 {version!r}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        _err(t, "entries 必须为数组")
    rows: list = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            _err(t, f"entries[{i}] 必须为映射")
        gap_kind = entry.get("gap_kind")
        if gap_kind not in TABLE_GAP_KINDS:
            _err(t, f"entries[{i}].gap_kind 必须属于 {TABLE_GAP_KINDS}，"
                    f"得到 {gap_kind!r}")
        blocks = entry.get("blocks_document_capability")
        if blocks != (gap_kind in GAP_BLOCKING_KINDS):
            _err(t, f"entries[{i}].blocks_document_capability 与 gap_kind 不符")
        if "source_intervals" in entry:
            _err(t, f"entries[{i}] 携带 `source_intervals`，这属于 `tsg-2`；"
                    "`tsg-1` 载荷不得混入后继字段")
        rows.append({
            "gap_kind": gap_kind,
            "detail_code": entry.get("detail_code"),
            "page_number": entry.get("page_number"),
            "table_id": entry.get("table_id"),
            "component_id": entry.get("component_id"),
            "disposition_id": entry.get("disposition_id"),
            "blocks_document_capability": blocks,
        })
    return {
        "schema_version": version,
        "status": "historical_only",
        "precise_source_intervals": False,
        "entry_count": len(rows),
        "entries": rows,
        "note": ("`tsg-1` 的历史读回：该版本的缺口**没有**精确源区间，因此"
                 "『已正式延期』与『无人认领』在该载荷上不可区分。其 `balanced` /"
                 "`issued` 结论不得被当作 current 结论。"),
    }

#: §19.4.2：owner 的两条互斥分支。
OWNER_KINDS: tuple[str, ...] = ("outline_node", "unassigned_boundary")

#: unassigned owner 的边界来源种类（封闭）。
UNASSIGNED_REF_KINDS: tuple[str, ...] = (
    "ts3_unassigned_span", "ts4_disposition",
)

#: `TableOwnerRef` 的 source boundary 种类（封闭）。
#:
#: TS5 **只**承认 TS4 已冻结、可直接回查的边界。boundary 不是"附近有个标题"的
#: 推测，而是一个有 id/locator/页范围的真实对象：
#:
#: - `ts4_body_disposition`：TS4 `BodyRangeDisposition`（`table_inside` /
#:   `table_adjacency` / `unassigned`）。TS5 的表格**只能**在这类 disposition 的
#:   范围内成立，因此它同时给出 owner 与边界，二者不可能分叉。
#: - `ts3_unassigned_span`：TS3 的显式 unassigned `OutlineSpan`。
SOURCE_BOUNDARY_KINDS: tuple[str, ...] = (
    "ts4_body_disposition", "ts3_unassigned_span",
)

#: §19.8.2 的 continuation 门的编号（6 条，必须**全部**满足）。
CONTINUATION_PROOF_KINDS: tuple[str, ...] = (
    "same_document_identity",
    "adjacent_page_or_explicit_occurrence",
    "compatible_column_header_unit",
    "source_order_closure",
    "no_intervening_structure",
    "bidirectional_acyclic_single_successor",
)


# ---------------------------------------------------------------------------
# 2. 小工具
# ---------------------------------------------------------------------------

def _need_pair(d: dict, key: str, typename: str, *, lo: int = 0,
               strict: bool = False) -> tuple[int, int]:
    return _need_int_pair(d, key, typename, lo=lo, strict=strict)


def _need_interval_list(d: dict, key: str, typename: str,
                        decoder: Callable[[Any], Any]) -> tuple:
    return _need_children(d, key, typename, decoder)


def _check_fingerprint(typename: str, field: str, value: Any, payload: Any) -> None:
    if not isinstance(value, str) or len(value) != 64:
        _err(typename, f"{field} 必须为 64 位小写十六进制 sha256，得到 {value!r}")
    expected = sha256_canonical(payload)
    if value != expected:
        _err(typename, f"{field} 与载荷重算不一致：{value!r} != {expected!r}")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 \
        and all(c in "0123456789abcdef" for c in value)


def _source_ref_sort_key(ref: "TableCellSourceRef") -> tuple:
    """来源片段的**源序键**：物理定位完全由 `interval` 承载，不另立字段。"""
    return ref.interval.sort_key()


def table_source_order_key(*, page_number: int, bbox: Sequence[float]) -> tuple:
    """页内**源序键**：`(页, 量化 bbox 的阅读顺序)`。

    用于 §19.4.4 的 `table_locator`：它只绑定物理位置与源序，**不**绑定
    classification/profile，因此 profile 升版只让 revision/member 失效，
    绝不伪造"物理位置改变"。
    """
    x0, y0, x1, y1 = (quantize(float(v)) for v in bbox)
    return (int(page_number), y0, x0, y1, x1)


def _assemble(cls: type, fields: dict, *,
              locator_attr: str | None, locator_kind: str, locator_method: str,
              fingerprint_attrs: tuple[tuple[str, str], ...] = (),
              identity_attr: str | None, identity_kind: str,
              identity_method: str) -> Any:
    """由"非派生字段"两阶段装配一个内容寻址对象。

    阶段 1 在**未验证**的临时实例上复用该类型自己的载荷函数算 locator / 指纹 /
    identity；阶段 2 用全部字段走真正的构造器，因此 `__post_init__` 的完整校验
    （含身份复算）**一定**会跑。派生逻辑因此只有一份，不可能与校验分叉。
    """
    tmp = object.__new__(cls)
    for k, v in fields.items():
        object.__setattr__(tmp, k, v)
    if locator_attr is not None:
        object.__setattr__(tmp, locator_attr,
                           locator(locator_kind, getattr(tmp, locator_method)()))
    for attr, method in fingerprint_attrs:
        object.__setattr__(tmp, attr,
                           sha256_canonical(getattr(tmp, method)()))
    if identity_attr is not None:
        object.__setattr__(tmp, identity_attr,
                           identity(identity_kind,
                                    getattr(tmp, identity_method)()))
    return cls(**{f.name: getattr(tmp, f.name)
                  for f in _dataclass_fields(cls)})


def _dataclass_fields(cls: type) -> tuple:
    from dataclasses import fields as _fields
    return tuple(_fields(cls))


def _owner_is_boundary_closed(owner: "TableOwnerRef", *,
                              page_number: int) -> bool:
    """owner 的 source boundary 必须能覆盖该表的物理页（跨文档/跨树一律拒绝）。

    这里**没有**任何"边界种类可以豁免页范围检查"的分支：boundary 是真实对象的
    页范围，表格落在其外就意味着归属无法回查。
    """
    if not isinstance(owner, TableOwnerRef):
        return False
    return owner.source_boundary_end_page >= page_number >= \
        owner.source_boundary_start_page


# ---------------------------------------------------------------------------
# 3. 子结构（无独立 schema version；随宿主对象的版本轴）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableSourceInterval:
    """一条**真实 LayoutSpan 片段**（域：`(页, 行, 片段索引, 片段内字符区间)`）。

    它是 cell / caption / unit / note 来源的**最小可回查单位**，也是
    §19.9.3 第 3 层 Layout 文本守恒的分区元。
    """

    page_number: int
    line_index: int
    span_index: int
    span_char_range: tuple[int, int]
    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        t = "TableSourceInterval"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        _need_int({"v": self.line_index}, "v", t, lo=0)
        _need_int({"v": self.span_index}, "v", t, lo=0)
        if not isinstance(self.span_char_range, tuple) or \
                len(self.span_char_range) != 2:
            _err(t, "span_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.span_char_range)]}, "_", t)
        if self.span_char_range[1] <= self.span_char_range[0]:
            _err(t, f"span_char_range 必须为正长度，得到 {self.span_char_range!r}")
        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            _err(t, "bbox 必须为 (x0,y0,x1,y1)")
        object.__setattr__(self, "bbox", tuple(quantize(x) for x in self.bbox))
        _check_bbox(self.bbox, f"{t}.bbox")

    @property
    def char_length(self) -> int:
        return self.span_char_range[1] - self.span_char_range[0]

    def sort_key(self) -> tuple:
        return (self.page_number, self.line_index, self.span_index,
                self.span_char_range[0])

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableSourceInterval",
            "page_number": self.page_number,
            "line_index": self.line_index,
            "span_index": self.span_index,
            "span_char_range": list(self.span_char_range),
            "bbox": list(self.bbox),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableSourceInterval":
        t = "TableSourceInterval"
        d = _reject_unknown(d, {
            "schema_type", "page_number", "line_index", "span_index",
            "span_char_range", "bbox"}, t)
        _need_enum(d, "schema_type", t, ("TableSourceInterval",))
        return cls(
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            span_index=_need_int(d, "span_index", t, lo=0),
            span_char_range=_need_int_pair(d, "span_char_range", t, strict=True),
            bbox=_need_bbox(d, "bbox", t),
        )


@dataclass(frozen=True)
class TableCellSourceRef:
    """cell 内容的一**段**真实来源（§19.4.2）。

    它必须落到**真实 terminal source**：明确 `terminal_kind`、terminal identity、
    精确 page/line/span/char/bbox 定位，并**逐段**判定可引用性。

    两条硬约束：

    - 只有 `terminal_kind="alignment"` 才可派生 `alignment_id = terminal_id`；
      refusal 分支**不得**伪造 alignment ID；
    - 整段 `EvidenceBlock` **不能**替代 cell 来源——这里的区间是 Evidence 字符域上的
      **精确子区间**，且必须与真实 Layout 片段闭合。
    """

    source_ref_id: str
    interval: TableSourceInterval
    component_id: str
    component_locator: str
    evidence_block_id: str
    evidence_char_range: tuple[int, int]
    terminal_kind: str
    terminal_id: str
    terminal_locator: str
    terminal_schema_version: str
    alignment_id: str | None
    verdict: str
    refusal_reason: str | None
    citable: bool
    citable_reason: str

    def __post_init__(self) -> None:
        t = "TableCellSourceRef"
        if not isinstance(self.interval, TableSourceInterval):
            _err(t, "interval 必须为 TableSourceInterval")
        for name in ("source_ref_id", "component_id", "component_locator",
                     "evidence_block_id", "terminal_id", "terminal_locator",
                     "terminal_schema_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.terminal_kind not in TERMINAL_KINDS:
            _err(t, f"terminal_kind 必须属于 {TERMINAL_KINDS}，"
                    f"得到 {self.terminal_kind!r}")
        if not isinstance(self.evidence_char_range, tuple) or \
                len(self.evidence_char_range) != 2:
            _err(t, "evidence_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.evidence_char_range)]}, "_", t)
        if self.evidence_char_range[1] <= self.evidence_char_range[0]:
            _err(t, "evidence_char_range 必须为正长度")
        # terminal 真值表（与 `SpanEvidenceComponent` 的既有规则**完全一致**，
        # 再叠加"逐段可引用性"，不另立一套）。
        if self.verdict not in COMPONENT_VERDICTS:
            _err(t, f"verdict 必须属于 {COMPONENT_VERDICTS}，得到 {self.verdict!r}")
        if self.verdict == "refused":
            if not isinstance(self.refusal_reason, str) or self.refusal_reason == "":
                _err(t, "verdict='refused' 必须给出 refusal_reason")
        elif self.refusal_reason is not None:
            _err(t, "非拒绝对齐记录不得携带 refusal_reason")
        if self.terminal_kind == "alignment":
            if self.alignment_id != self.terminal_id:
                _err(t, "alignment 终态必须派生 alignment_id == terminal_id，"
                        f"得到 {self.alignment_id!r} != {self.terminal_id!r}")
        elif self.alignment_id is not None:
            _err(t, "refusal 终态**不得**伪造 alignment ID")
        if not isinstance(self.citable, bool):
            _err(t, "citable 必须为 bool")
        if self.citable_reason not in CITABLE_REASONS:
            _err(t, f"citable_reason 必须属于 {CITABLE_REASONS}，"
                    f"得到 {self.citable_reason!r}")
        if self.citable != (self.citable_reason == CITABLE_REASON_TRUE):
            _err(t, f"citable=True 当且仅当 citable_reason=={CITABLE_REASON_TRUE!r}")
        # 逐段可引用性必须与终态自洽：只有 aligned 段落可引用。
        if self.citable and self.verdict != "aligned":
            _err(t, f"只有 aligned 终态的片段可引用，得到 verdict={self.verdict!r}")
        if not self.citable and self.verdict == "aligned" \
                and self.citable_reason in ("verdict_not_aligned", "refusal_record"):
            _err(t, "aligned 终态不得以'非对齐/拒绝'为不可引用理由")
        expected = identity("tcsr", self._identity_payload())
        if self.source_ref_id != expected:
            _err(t, "source_ref_id 与派生身份不一致："
                    f"{self.source_ref_id!r} != {expected!r}")

    def _identity_payload(self) -> dict:
        return {
            "interval": self.interval.to_dict(),
            "component_id": self.component_id,
            "component_locator": self.component_locator,
            "evidence_block_id": self.evidence_block_id,
            "evidence_char_range": list(self.evidence_char_range),
            "terminal_kind": self.terminal_kind,
            "terminal_id": self.terminal_id,
            "terminal_locator": self.terminal_locator,
            "terminal_schema_version": self.terminal_schema_version,
            "alignment_id": self.alignment_id,
            "verdict": self.verdict,
            "refusal_reason": self.refusal_reason,
            "citable": self.citable,
            "citable_reason": self.citable_reason,
        }

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "TableCellSourceRef"
        payload["source_ref_id"] = self.source_ref_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableCellSourceRef":
        t = "TableCellSourceRef"
        d = _reject_unknown(d, {
            "schema_type", "source_ref_id", "interval", "component_id",
            "component_locator", "evidence_block_id", "evidence_char_range",
            "terminal_kind", "terminal_id", "terminal_locator",
            "terminal_schema_version", "alignment_id", "verdict",
            "refusal_reason", "citable", "citable_reason"}, t)
        _need_enum(d, "schema_type", t, ("TableCellSourceRef",))
        return cls(
            source_ref_id=_need_str(d, "source_ref_id", t),
            interval=_need_child(d, "interval", t, TableSourceInterval.from_dict),
            component_id=_need_str(d, "component_id", t),
            component_locator=_need_str(d, "component_locator", t),
            evidence_block_id=_need_str(d, "evidence_block_id", t),
            evidence_char_range=_need_int_pair(d, "evidence_char_range", t,
                                               strict=True),
            terminal_kind=_need_enum(d, "terminal_kind", t, TERMINAL_KINDS),
            terminal_id=_need_str(d, "terminal_id", t),
            terminal_locator=_need_str(d, "terminal_locator", t),
            terminal_schema_version=_need_str(d, "terminal_schema_version", t),
            alignment_id=_need_str(d, "alignment_id", t, none_ok=True),
            verdict=_need_enum(d, "verdict", t, COMPONENT_VERDICTS),
            refusal_reason=_need_str(d, "refusal_reason", t, none_ok=True),
            citable=_need_bool(d, "citable", t),
            citable_reason=_need_enum(d, "citable_reason", t, CITABLE_REASONS),
        )

    @classmethod
    def create(cls, *, interval: TableSourceInterval, component_id: str,
               component_locator: str, evidence_block_id: str,
               evidence_char_range: tuple[int, int], terminal_kind: str,
               terminal_id: str, terminal_locator: str,
               terminal_schema_version: str, verdict: str,
               refusal_reason: str | None = None) -> "TableCellSourceRef":
        """由真实 terminal source 派生一段 cell 来源（可引用性**逐段**判定）。

        `alignment_id` **只**在 `terminal_kind="alignment"` 时由 terminal 派生；
        refusal 分支一律 `None`，因此不存在"伪造 Alignment ID"的构造路径。
        """
        if terminal_kind not in TERMINAL_KINDS:
            _err(cls.__name__, f"terminal_kind 必须属于 {TERMINAL_KINDS}，"
                               f"得到 {terminal_kind!r}")
        alignment_id = terminal_id if terminal_kind == "alignment" else None
        citable = (terminal_kind == "alignment" and verdict == "aligned")
        if citable:
            citable_reason = CITABLE_REASON_TRUE
        elif terminal_kind == "refusal" or verdict == "refused":
            citable_reason = "refusal_record"
        else:
            citable_reason = "verdict_not_aligned"
        payload = {
            "interval": interval.to_dict(),
            "component_id": component_id,
            "component_locator": component_locator,
            "evidence_block_id": evidence_block_id,
            "evidence_char_range": list(evidence_char_range),
            "terminal_kind": terminal_kind,
            "terminal_id": terminal_id,
            "terminal_locator": terminal_locator,
            "terminal_schema_version": terminal_schema_version,
            "alignment_id": alignment_id,
            "verdict": verdict,
            "refusal_reason": refusal_reason,
            "citable": citable,
            "citable_reason": citable_reason,
        }
        return cls(
            source_ref_id=identity("tcsr", payload),
            interval=interval,
            component_id=component_id,
            component_locator=component_locator,
            evidence_block_id=evidence_block_id,
            evidence_char_range=evidence_char_range,
            terminal_kind=terminal_kind,
            terminal_id=terminal_id,
            terminal_locator=terminal_locator,
            terminal_schema_version=terminal_schema_version,
            alignment_id=alignment_id,
            verdict=verdict,
            refusal_reason=refusal_reason,
            citable=citable,
            citable_reason=citable_reason,
        )


@dataclass(frozen=True)
class TableCellBlock:
    """cell 内部的一段呈现结构（§19.6.2）。

    `role` 只表示 **cell 内部**呈现结构，**不**进入标题树：两个表内小标题作为
    `role=heading` 的 cell block 保留，但**不**成为 `OutlineNode`。

    `text` 必须逐字符来自 `source_ref_ids` 指向的真实片段：片段按源序拼接，
    相邻的**不同 `(页, 行)` 组**之间插入一个换行。因此
    `len(text) == Σ片段长度 + (组数 - 1)` 可在构造期精确复核。
    """

    block_index: int
    role: str
    text: str
    source_ref_ids: tuple[str, ...]
    content_fingerprint: str

    def __post_init__(self) -> None:
        t = "TableCellBlock"
        _need_int({"v": self.block_index}, "v", t, lo=0)
        if self.role not in CELL_BLOCK_ROLES:
            _err(t, f"role 必须属于 {CELL_BLOCK_ROLES}，得到 {self.role!r}")
        if not isinstance(self.text, str):
            _err(t, "text 必须为字符串")
        if not isinstance(self.source_ref_ids, tuple):
            _err(t, "source_ref_ids 必须为元组")
        if not self.source_ref_ids:
            _err(t, "source_ref_ids 不得为空（块必须有真实来源）")
        for i, rid in enumerate(self.source_ref_ids):
            if not isinstance(rid, str) or rid == "":
                _err(t, f"source_ref_ids[{i}] 必须为非空字符串")
        if len(set(self.source_ref_ids)) != len(self.source_ref_ids):
            _err(t, "source_ref_ids 不得重复")
        _check_fingerprint(t, "content_fingerprint", self.content_fingerprint,
                           self._fingerprint_payload())

    def _fingerprint_payload(self) -> dict:
        return {
            "role": self.role,
            "text": self.text,
            "source_ref_ids": list(self.source_ref_ids),
        }

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableCellBlock",
            "block_index": self.block_index,
            "role": self.role,
            "text": self.text,
            "source_ref_ids": list(self.source_ref_ids),
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableCellBlock":
        t = "TableCellBlock"
        d = _reject_unknown(d, {
            "schema_type", "block_index", "role", "text", "source_ref_ids",
            "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("TableCellBlock",))
        return cls(
            block_index=_need_int(d, "block_index", t, lo=0),
            role=_need_enum(d, "role", t, CELL_BLOCK_ROLES),
            text=_need_str(d, "text", t, empty_ok=True),
            source_ref_ids=_need_str_tuple(d, "source_ref_ids", t),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, *, block_index: int, role: str, text: str,
               source_ref_ids: Sequence[str]) -> "TableCellBlock":
        payload = {"role": role, "text": text,
                   "source_ref_ids": list(source_ref_ids)}
        return cls(block_index=block_index, role=role, text=text,
                   source_ref_ids=tuple(source_ref_ids),
                   content_fingerprint=sha256_canonical(payload))


@dataclass(frozen=True)
class TableOwnerRef:
    """`TableObjectV4` 的**唯一**归属引用（§19.4.2）。

    每个表必须**恰好**有一个可独立回查的 owner：已验证的 outline node，或已验证的
    unassigned boundary。不允许 orphan table，也不允许仅凭相邻标题字符串自报归属。

    两条分支**互斥**且**恰有一个成立**：

    - `owner_kind="outline_node"`：必须带 verified `node_id` + `node_locator` +
      source boundary，且**不得**携带 unassigned ref；
    - `owner_kind="unassigned_boundary"`：`node_id/node_locator` 为 `None`，
      必须带已验证的 unassigned 引用（TS3 unassigned span 或 TS4 body /
      formal-unassigned disposition）与 source boundary。
    """

    owner_kind: str
    node_id: str | None
    outline_locator: str | None
    source_boundary_kind: str
    source_boundary_locator: str
    source_boundary_id: str
    source_boundary_start_page: int
    source_boundary_end_page: int
    unassigned_ref_kind: str | None
    unassigned_ref_locator: str | None
    unassigned_ref_id: str | None

    def __post_init__(self) -> None:
        t = "TableOwnerRef"
        if self.owner_kind not in OWNER_KINDS:
            _err(t, f"owner_kind 必须属于 {OWNER_KINDS}，得到 {self.owner_kind!r}")
        for name in ("source_boundary_kind", "source_boundary_locator",
                     "source_boundary_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.source_boundary_kind not in SOURCE_BOUNDARY_KINDS:
            _err(t, f"source_boundary_kind 必须属于 {SOURCE_BOUNDARY_KINDS}，"
                    f"得到 {self.source_boundary_kind!r}")
        _need_int({"v": self.source_boundary_start_page}, "v", t, lo=1)
        _need_int({"v": self.source_boundary_end_page}, "v", t, lo=1)
        if self.source_boundary_end_page < self.source_boundary_start_page:
            _err(t, "source boundary 的结束页不得早于起始页")
        if self.owner_kind == "outline_node":
            for name in ("node_id", "outline_locator"):
                v = getattr(self, name)
                if not isinstance(v, str) or v == "":
                    _err(t, f"outline_node 分支必须给出 {name}")
            for name in ("unassigned_ref_kind", "unassigned_ref_locator",
                         "unassigned_ref_id"):
                if getattr(self, name) is not None:
                    _err(t, f"outline_node 分支不得携带 {name}")
        else:
            if self.node_id is not None or self.outline_locator is not None:
                _err(t, "unassigned_boundary 分支的 node_id/outline_locator "
                        "必须为 None")
            if self.unassigned_ref_kind not in UNASSIGNED_REF_KINDS:
                _err(t, f"unassigned_boundary 分支必须给出已登记的 "
                        f"unassigned_ref_kind，得到 {self.unassigned_ref_kind!r}")
            for name in ("unassigned_ref_locator", "unassigned_ref_id"):
                v = getattr(self, name)
                if not isinstance(v, str) or v == "":
                    _err(t, f"unassigned_boundary 分支必须给出 {name}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableOwnerRef",
            "owner_kind": self.owner_kind,
            "node_id": self.node_id,
            "outline_locator": self.outline_locator,
            "source_boundary_kind": self.source_boundary_kind,
            "source_boundary_locator": self.source_boundary_locator,
            "source_boundary_id": self.source_boundary_id,
            "source_boundary_start_page": self.source_boundary_start_page,
            "source_boundary_end_page": self.source_boundary_end_page,
            "unassigned_ref_kind": self.unassigned_ref_kind,
            "unassigned_ref_locator": self.unassigned_ref_locator,
            "unassigned_ref_id": self.unassigned_ref_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableOwnerRef":
        t = "TableOwnerRef"
        d = _reject_unknown(d, {
            "schema_type", "owner_kind", "node_id", "outline_locator",
            "source_boundary_kind", "source_boundary_locator",
            "source_boundary_id", "source_boundary_start_page",
            "source_boundary_end_page", "unassigned_ref_kind",
            "unassigned_ref_locator", "unassigned_ref_id"}, t)
        _need_enum(d, "schema_type", t, ("TableOwnerRef",))
        return cls(
            owner_kind=_need_enum(d, "owner_kind", t, OWNER_KINDS),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            outline_locator=_need_str(d, "outline_locator", t, none_ok=True),
            source_boundary_kind=_need_str(d, "source_boundary_kind", t),
            source_boundary_locator=_need_str(d, "source_boundary_locator", t),
            source_boundary_id=_need_str(d, "source_boundary_id", t),
            source_boundary_start_page=_need_int(
                d, "source_boundary_start_page", t, lo=1),
            source_boundary_end_page=_need_int(
                d, "source_boundary_end_page", t, lo=1),
            unassigned_ref_kind=_need_str(d, "unassigned_ref_kind", t,
                                          none_ok=True),
            unassigned_ref_locator=_need_str(d, "unassigned_ref_locator", t,
                                             none_ok=True),
            unassigned_ref_id=_need_str(d, "unassigned_ref_id", t, none_ok=True),
        )


@dataclass(frozen=True)
class TableCellV4:
    """`to-4` 的一个 cell（§19.4.2）。

    它不再只有"单行 text + 一个首 locator"：`source_fragments`（即 `source_refs`）
    是逐段真实来源，`blocks` 是有序内部结构。

    `text` 必须**恰好**等于 `blocks` 按 `CELL_BLOCK_SEPARATOR` 的重构结果：cell
    canonical text 因此是可复核的派生量，而不是自报字符串。

    `tc-3` 起，cell 可以**带 typed 缺口**：`unproven_fragments` 逐条记录"落在本 cell
    几何范围内、但无法闭合到真实 terminal source 的 `(页, 行, 片段索引)`"。空 refs /
    空 blocks **仅在**该元组非空时合法 —— 一个既无可引用来源、又不声明缺口的 cell 仍然
    不可能存在（`tc-2` 的 fail-closed 语义只被"有缺口可报"这一条豁免，没有被撤销）。
    """

    row: int
    column: int
    rowspan: int
    colspan: int
    bbox: tuple[float, float, float, float]
    text: str
    blocks: tuple[TableCellBlock, ...]
    source_refs: tuple[TableCellSourceRef, ...]
    unproven_fragments: tuple[tuple[int, int, int], ...]
    schema_version: str

    SCHEMA_CONSTANT = "TABLE_CELL_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "TableCellV4"
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        for name in ("row", "column"):
            _need_int({"v": getattr(self, name)}, "v", t, lo=0)
        for name in ("rowspan", "colspan"):
            _need_int({"v": getattr(self, name)}, "v", t, lo=1)
        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            _err(t, "bbox 必须为 (x0,y0,x1,y1)")
        object.__setattr__(self, "bbox", tuple(quantize(x) for x in self.bbox))
        _check_bbox(self.bbox, f"{t}.bbox")
        if not isinstance(self.text, str):
            _err(t, "text 必须为字符串")
        if not isinstance(self.source_refs, tuple):
            _err(t, "source_refs 必须为元组")
        if not isinstance(self.blocks, tuple):
            _err(t, "blocks 必须为元组")
        if not isinstance(self.unproven_fragments, tuple):
            _err(t, "unproven_fragments 必须为元组")
        keys_seen: set = set()
        for i, item in enumerate(self.unproven_fragments):
            ok = (isinstance(item, tuple) and len(item) == 3
                  and all(isinstance(x, int) and not isinstance(x, bool)
                          for x in item)
                  and item[0] >= 1 and item[1] >= 0 and item[2] >= 0)
            if not ok:
                _err(t, f"unproven_fragments[{i}] 必须为 (页, 行, 片段索引) 三元组")
            if item in keys_seen:
                _err(t, f"unproven_fragments[{i}] 重复：{item!r}")
            keys_seen.add(item)
        if sorted(self.unproven_fragments) != list(self.unproven_fragments):
            _err(t, "unproven_fragments 必须按 (页, 行, 片段索引) 升序")
        if not self.source_refs or not self.blocks:
            # 缺口必须**逐条在册**才允许空 cell：反过来，任何空 cell 都必须指明它缺的是
            # 哪些真实片段，因此这里不可能出现"没有来源也没有缺口"的静默 cell。
            if not self.unproven_fragments:
                _err(t, "非空 cell 必须有真实来源片段（不得自报文本）")
        if not self.blocks and self.text != "":
            _err(t, "没有内部块的 cell 必须为空文本")
        ids = []
        for i, ref in enumerate(self.source_refs):
            if not isinstance(ref, TableCellSourceRef):
                _err(t, f"source_refs[{i}] 必须为 TableCellSourceRef")
            ids.append(ref.source_ref_id)
        if len(set(ids)) != len(ids):
            _err(t, "同一 cell 内 source_ref_id 不得重复")
        keys = [_source_ref_sort_key(r) for r in self.source_refs]
        if keys != sorted(keys):
            _err(t, "source_refs 必须按 (页, 行, 片段索引, 片段内起点) 升序")
        # 逐段互斥：同一 LayoutSpan 上的片段区间不得重叠（可以相邻）。
        by_span: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
        for r in self.source_refs:
            by_span.setdefault(
                (r.interval.page_number, r.interval.line_index,
                 r.interval.span_index), []).append(r.interval.span_char_range)
        for key, ranges in by_span.items():
            ranges = sorted(ranges)
            for a, b in zip(ranges, ranges[1:]):
                if b[0] < a[1]:
                    _err(t, f"同一 LayoutSpan {key} 上的来源片段重叠：{a} / {b}")
        pool = set(ids)
        block_ids: list[str] = []
        for i, blk in enumerate(self.blocks):
            if not isinstance(blk, TableCellBlock):
                _err(t, f"blocks[{i}] 必须为 TableCellBlock")
            if blk.block_index != i:
                _err(t, f"blocks[{i}].block_index 必须等于其序号 {i}")
            for rid in blk.source_ref_ids:
                if rid not in pool:
                    _err(t, f"blocks[{i}] 引用了未知 source_ref_id {rid!r}")
            block_ids.extend(blk.source_ref_ids)
        if len(set(block_ids)) != len(block_ids):
            _err(t, "同一 source ref 不得被同一 cell 的两个块重复消费")
        if set(block_ids) != pool:
            missing = sorted(pool - set(block_ids))
            _err(t, f"每个来源片段必须恰好被一个块消费，未消费: {missing}")
        expected_text = CELL_BLOCK_SEPARATOR.join(b.text for b in self.blocks)
        if self.text != expected_text:
            _err(t, "cell text 必须由 blocks 以冻结分隔规则重构："
                    f"{self.text!r} != {expected_text!r}")

    def source_ref_by_id(self, source_ref_id: str) -> TableCellSourceRef | None:
        for r in self.source_refs:
            if r.source_ref_id == source_ref_id:
                return r
        return None

    @property
    def citable_ref_count(self) -> int:
        return sum(1 for r in self.source_refs if r.citable)

    @property
    def non_citable_ref_count(self) -> int:
        return len(self.source_refs) - self.citable_ref_count

    @property
    def all_refs_citable(self) -> bool:
        return self.non_citable_ref_count == 0

    def sort_key(self) -> tuple:
        return (self.row, self.column)

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableCellV4",
            "row": self.row,
            "column": self.column,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "bbox": list(self.bbox),
            "text": self.text,
            "blocks": [b.to_dict() for b in self.blocks],
            "source_refs": [r.to_dict() for r in self.source_refs],
            "unproven_fragments": [list(x) for x in self.unproven_fragments],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableCellV4":
        t = "TableCellV4"
        d = _reject_unknown(d, {
            "schema_type", "row", "column", "rowspan", "colspan", "bbox", "text",
            "blocks", "source_refs", "unproven_fragments", "schema_version"}, t)
        _need_enum(d, "schema_type", t, ("TableCellV4",))
        raw_gap = d.get("unproven_fragments", [])
        if not isinstance(raw_gap, (list, tuple)):
            _err(t, "unproven_fragments 必须为数组")
        gap: list[tuple[int, int, int]] = []
        for i, item in enumerate(raw_gap):
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                _err(t, f"unproven_fragments[{i}] 必须为 (页, 行, 片段索引) 三元组")
            gap.append(tuple(_need_int({"v": x}, "v", t, lo=0) for x in item))
        return cls(
            row=_need_int(d, "row", t, lo=0),
            column=_need_int(d, "column", t, lo=0),
            rowspan=_need_int(d, "rowspan", t, lo=1),
            colspan=_need_int(d, "colspan", t, lo=1),
            bbox=_need_bbox(d, "bbox", t),
            text=_need_str(d, "text", t, empty_ok=True),
            blocks=_need_children(d, "blocks", t, TableCellBlock.from_dict),
            source_refs=_need_children(d, "source_refs", t,
                                       TableCellSourceRef.from_dict),
            unproven_fragments=tuple(gap),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
        )

    @classmethod
    def create(cls, *, row: int, column: int, rowspan: int, colspan: int,
               bbox: tuple[float, float, float, float],
               blocks: Sequence[TableCellBlock],
               source_refs: Sequence[TableCellSourceRef],
               unproven_fragments: Sequence[tuple[int, int, int]] = (),
               schema_version: str | None = None) -> "TableCellV4":
        """cell canonical text 由 blocks **重构**，不由调用方自报。"""
        return cls(
            row=row, column=column, rowspan=rowspan, colspan=colspan,
            bbox=bbox,
            text=CELL_BLOCK_SEPARATOR.join(b.text for b in blocks),
            blocks=tuple(blocks),
            source_refs=tuple(source_refs),
            unproven_fragments=tuple(sorted(tuple(x) for x in unproven_fragments)),
            schema_version=(schema_version or TABLE_CELL_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class TableRowV4:
    """`to-4` 的一行（§19.6.1）。表头 / 表体 / 小计 / 合计分开保留。"""

    row_index: int
    role: str
    label: str | None
    is_repeated_header: bool
    cells: tuple[TableCellV4, ...]

    def __post_init__(self) -> None:
        t = "TableRowV4"
        _need_int({"v": self.row_index}, "v", t, lo=0)
        if self.role not in TABLE_ROW_ROLES:
            _err(t, f"role 必须属于 {TABLE_ROW_ROLES}，得到 {self.role!r}")
        if self.label is not None and (not isinstance(self.label, str)
                                       or self.label == ""):
            _err(t, "label 必须为非空字符串或 None")
        if not isinstance(self.is_repeated_header, bool):
            _err(t, "is_repeated_header 必须为 bool")
        if self.is_repeated_header and self.role != "header":
            _err(t, "只有 header 行可以是重复表头（`repeated_header` 只是逻辑展示标记，"
                    "物理来源必须保留）")
        if not isinstance(self.cells, tuple):
            _err(t, "cells 必须为元组")
        if not self.cells:
            _err(t, "每个物理行必须至少有一个起始 cell")
        cols = [c.column for c in self.cells]
        if cols != sorted(cols) or len(set(cols)) != len(cols):
            _err(t, "行内 cells 必须按 column 升序且不得重复")

    def cell_at(self, column: int) -> TableCellV4 | None:
        for c in self.cells:
            if c.column == column:
                return c
        return None

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableRowV4",
            "row_index": self.row_index,
            "role": self.role,
            "label": self.label,
            "is_repeated_header": self.is_repeated_header,
            "cells": [c.to_dict() for c in self.cells],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableRowV4":
        t = "TableRowV4"
        d = _reject_unknown(d, {
            "schema_type", "row_index", "role", "label", "is_repeated_header",
            "cells"}, t)
        _need_enum(d, "schema_type", t, ("TableRowV4",))
        return cls(
            row_index=_need_int(d, "row_index", t, lo=0),
            role=_need_enum(d, "role", t, TABLE_ROW_ROLES),
            label=_need_str(d, "label", t, none_ok=True),
            is_repeated_header=_need_bool(d, "is_repeated_header", t),
            cells=_need_children(d, "cells", t, TableCellV4.from_dict),
        )


# ---- typed relation endpoints (§19.8.1) -----------------------------------

@dataclass(frozen=True)
class TableEndpointRef:
    """`TableRelation` 的 table 端点。"""

    table_locator: str
    table_id: str
    document_id: str
    page_layout_id: str
    outline_id: str
    page_number: int

    endpoint_kind = "table"

    def __post_init__(self) -> None:
        t = "TableEndpointRef"
        for name in ("table_locator", "table_id", "document_id", "page_layout_id",
                     "outline_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _need_int({"v": self.page_number}, "v", t, lo=1)

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableEndpointRef",
            "endpoint_kind": self.endpoint_kind,
            "table_locator": self.table_locator,
            "table_id": self.table_id,
            "document_id": self.document_id,
            "page_layout_id": self.page_layout_id,
            "outline_id": self.outline_id,
            "page_number": self.page_number,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableEndpointRef":
        t = "TableEndpointRef"
        d = _reject_unknown(d, {
            "schema_type", "endpoint_kind", "table_locator", "table_id",
            "document_id", "page_layout_id", "outline_id", "page_number"}, t)
        _need_enum(d, "schema_type", t, ("TableEndpointRef",))
        _need_enum(d, "endpoint_kind", t, ("table",))
        return cls(
            table_locator=_need_str(d, "table_locator", t),
            table_id=_need_str(d, "table_id", t),
            document_id=_need_str(d, "document_id", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            outline_id=_need_str(d, "outline_id", t),
            page_number=_need_int(d, "page_number", t, lo=1),
        )


@dataclass(frozen=True)
class FinalSpanEndpointRef:
    """`TableRelation` 的 final span 端点。"""

    span_locator: str
    span_id: str
    node_id: str | None
    document_id: str
    outline_id: str
    source_boundary_id: str

    endpoint_kind = "final_span"

    def __post_init__(self) -> None:
        t = "FinalSpanEndpointRef"
        for name in ("span_locator", "span_id", "document_id", "outline_id",
                     "source_boundary_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.node_id is not None and (not isinstance(self.node_id, str)
                                         or self.node_id == ""):
            _err(t, "node_id 必须为非空字符串或 None")

    def to_dict(self) -> dict:
        return {
            "schema_type": "FinalSpanEndpointRef",
            "endpoint_kind": self.endpoint_kind,
            "span_locator": self.span_locator,
            "span_id": self.span_id,
            "node_id": self.node_id,
            "document_id": self.document_id,
            "outline_id": self.outline_id,
            "source_boundary_id": self.source_boundary_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinalSpanEndpointRef":
        t = "FinalSpanEndpointRef"
        d = _reject_unknown(d, {
            "schema_type", "endpoint_kind", "span_locator", "span_id", "node_id",
            "document_id", "outline_id", "source_boundary_id"}, t)
        _need_enum(d, "schema_type", t, ("FinalSpanEndpointRef",))
        _need_enum(d, "endpoint_kind", t, ("final_span",))
        return cls(
            span_locator=_need_str(d, "span_locator", t),
            span_id=_need_str(d, "span_id", t),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            document_id=_need_str(d, "document_id", t),
            outline_id=_need_str(d, "outline_id", t),
            source_boundary_id=_need_str(d, "source_boundary_id", t),
        )


@dataclass(frozen=True)
class ComponentEndpointRef:
    """`TableRelation` 的 component 端点（caption / unit / note）。"""

    component_id: str
    component_locator: str
    evidence_block_id: str
    evidence_char_range: tuple[int, int]
    terminal_kind: str
    terminal_id: str
    role: str
    table_id: str

    endpoint_kind = "component"

    def __post_init__(self) -> None:
        t = "ComponentEndpointRef"
        for name in ("component_id", "component_locator", "evidence_block_id",
                     "terminal_id", "table_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.terminal_kind not in TERMINAL_KINDS:
            _err(t, f"terminal_kind 必须属于 {TERMINAL_KINDS}，"
                    f"得到 {self.terminal_kind!r}")
        if self.role not in COMPONENT_ENDPOINT_ROLES:
            _err(t, f"role 必须属于 {COMPONENT_ENDPOINT_ROLES}，得到 {self.role!r}")
        if not isinstance(self.evidence_char_range, tuple) or \
                len(self.evidence_char_range) != 2:
            _err(t, "evidence_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.evidence_char_range)]}, "_", t)
        if self.evidence_char_range[1] <= self.evidence_char_range[0]:
            _err(t, "evidence_char_range 必须为正长度")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ComponentEndpointRef",
            "endpoint_kind": self.endpoint_kind,
            "component_id": self.component_id,
            "component_locator": self.component_locator,
            "evidence_block_id": self.evidence_block_id,
            "evidence_char_range": list(self.evidence_char_range),
            "terminal_kind": self.terminal_kind,
            "terminal_id": self.terminal_id,
            "role": self.role,
            "table_id": self.table_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ComponentEndpointRef":
        t = "ComponentEndpointRef"
        d = _reject_unknown(d, {
            "schema_type", "endpoint_kind", "component_id", "component_locator",
            "evidence_block_id", "evidence_char_range", "terminal_kind",
            "terminal_id", "role", "table_id"}, t)
        _need_enum(d, "schema_type", t, ("ComponentEndpointRef",))
        _need_enum(d, "endpoint_kind", t, ("component",))
        return cls(
            component_id=_need_str(d, "component_id", t),
            component_locator=_need_str(d, "component_locator", t),
            evidence_block_id=_need_str(d, "evidence_block_id", t),
            evidence_char_range=_need_int_pair(d, "evidence_char_range", t,
                                               strict=True),
            terminal_kind=_need_enum(d, "terminal_kind", t, TERMINAL_KINDS),
            terminal_id=_need_str(d, "terminal_id", t),
            role=_need_enum(d, "role", t, COMPONENT_ENDPOINT_ROLES),
            table_id=_need_str(d, "table_id", t),
        )


@dataclass(frozen=True)
class ReferenceOccurrenceEndpointRef:
    """`TableRelation` 的 reference occurrence 端点（§19.8.1 末段）。

    TS3 允许"source occurrence 已对象级验证、但 target 尚未解析"的边；TS5 **不**
    要求上游预先虚构 resolved table target，而是必须用当前 verified source
    occurrence + 当前 `TableObject` 的 geometry/identity/target proof 独立完成目标
    落地。因此这里保留的是**已验证的来源位置**，目标由 `TableRelation` 证明。
    """

    occurrence_locator: str
    occurrence_id: str
    owning_source_ref: str
    page_number: int
    line_index: int
    occurrence_char_range: tuple[int, int]
    edge_locator: str
    edge_id: str
    reference_marker: str

    endpoint_kind = "reference_occurrence"

    def __post_init__(self) -> None:
        t = "ReferenceOccurrenceEndpointRef"
        for name in ("occurrence_locator", "occurrence_id", "owning_source_ref",
                     "edge_locator", "edge_id", "reference_marker"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _need_int({"v": self.page_number}, "v", t, lo=1)
        _need_int({"v": self.line_index}, "v", t, lo=0)
        if not isinstance(self.occurrence_char_range, tuple) or \
                len(self.occurrence_char_range) != 2:
            _err(t, "occurrence_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.occurrence_char_range)]}, "_", t)
        if self.occurrence_char_range[1] <= self.occurrence_char_range[0]:
            _err(t, "occurrence_char_range 必须为正长度")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ReferenceOccurrenceEndpointRef",
            "endpoint_kind": self.endpoint_kind,
            "occurrence_locator": self.occurrence_locator,
            "occurrence_id": self.occurrence_id,
            "owning_source_ref": self.owning_source_ref,
            "page_number": self.page_number,
            "line_index": self.line_index,
            "occurrence_char_range": list(self.occurrence_char_range),
            "edge_locator": self.edge_locator,
            "edge_id": self.edge_id,
            "reference_marker": self.reference_marker,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ReferenceOccurrenceEndpointRef":
        t = "ReferenceOccurrenceEndpointRef"
        d = _reject_unknown(d, {
            "schema_type", "endpoint_kind", "occurrence_locator", "occurrence_id",
            "owning_source_ref", "page_number", "line_index",
            "occurrence_char_range", "edge_locator", "edge_id",
            "reference_marker"}, t)
        _need_enum(d, "schema_type", t, ("ReferenceOccurrenceEndpointRef",))
        _need_enum(d, "endpoint_kind", t, ("reference_occurrence",))
        return cls(
            occurrence_locator=_need_str(d, "occurrence_locator", t),
            occurrence_id=_need_str(d, "occurrence_id", t),
            owning_source_ref=_need_str(d, "owning_source_ref", t),
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            occurrence_char_range=_need_int_pair(d, "occurrence_char_range", t,
                                                 strict=True),
            edge_locator=_need_str(d, "edge_locator", t),
            edge_id=_need_str(d, "edge_id", t),
            reference_marker=_need_str(d, "reference_marker", t),
        )


#: 四者组成的**严格** discriminated union 注册表。
ENDPOINT_REF_TYPES: tuple[type, ...] = (
    TableEndpointRef, FinalSpanEndpointRef, ComponentEndpointRef,
    ReferenceOccurrenceEndpointRef,
)
ENDPOINT_REF_BY_NAME: dict[str, type] = {t.__name__: t for t in ENDPOINT_REF_TYPES}
ENDPOINT_REF_BY_KIND: dict[str, type] = {t.endpoint_kind: t
                                         for t in ENDPOINT_REF_TYPES}


# ---- coverage / gap / conservation 子结构 ---------------------------------

@dataclass(frozen=True)
class CitableInterval:
    """一个可引用区间 + 其理由（§19.9.2）。`citable=False` 的区间同样**保留**。"""

    citable: bool
    reason: str
    char_range: tuple[int, int]
    cell_ref: str | None

    def __post_init__(self) -> None:
        t = "CitableInterval"
        if not isinstance(self.citable, bool):
            _err(t, "citable 必须为 bool")
        if self.reason not in CITABLE_REASONS:
            _err(t, f"reason 必须属于 {CITABLE_REASONS}，得到 {self.reason!r}")
        if self.citable != (self.reason == CITABLE_REASON_TRUE):
            _err(t, f"citable=True 当且仅当 reason=={CITABLE_REASON_TRUE!r}")
        if not isinstance(self.char_range, tuple) or len(self.char_range) != 2:
            _err(t, "char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.char_range)]}, "_", t)
        if self.char_range[1] <= self.char_range[0]:
            _err(t, "char_range 必须为正长度")
        if self.cell_ref is not None and (not isinstance(self.cell_ref, str)
                                          or self.cell_ref == ""):
            _err(t, "cell_ref 必须为非空字符串或 None")
        if self.citable and self.cell_ref is None:
            _err(t, "可引用区间必须绑定到具体 cell source ref（下游 citation 不得只引整表）")

    def to_dict(self) -> dict:
        return {
            "schema_type": "CitableInterval",
            "citable": self.citable,
            "reason": self.reason,
            "char_range": list(self.char_range),
            "cell_ref": self.cell_ref,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "CitableInterval":
        t = "CitableInterval"
        d = _reject_unknown(d, {
            "schema_type", "citable", "reason", "char_range", "cell_ref"}, t)
        _need_enum(d, "schema_type", t, ("CitableInterval",))
        return cls(
            citable=_need_bool(d, "citable", t),
            reason=_need_enum(d, "reason", t, CITABLE_REASONS),
            char_range=_need_int_pair(d, "char_range", t, strict=True),
            cell_ref=_need_str(d, "cell_ref", t, none_ok=True),
        )


@dataclass(frozen=True)
class TableGapSourceInterval:
    """一条 typed gap **精确覆盖**的真实 LayoutSpan 字符区间（`tsg-2` / §六）。

    域与 `TableSourceInterval` **完全一致**：`(页, 行, 片段索引, 片段内字符区间)`，
    因此它可以与 cell / 表题 / 段落引用直接比较，不存在任何偏移换算，也不可能出现
    "整页框"或"整 bbox 框"这种粗粒度主张——它的粒度就是真实排版片段的字符区间。

    它**没有**自己的哈希，也不接受调用方自报坐标：可回查性来自两处正式来源：

    - 它随宿主 `TableStructureGap.upstream_dependency_fingerprint`（文档 / 文档版本 /
      PageLayout / 上游依赖束）一起被签名；
    - 读回时由独立 verifier 对着**当次** PageLayout 重算（片段存在、区间落在片段内、
      区间为正长度、不与其它 typed gap 在同一片段上重叠）。
    """

    page_number: int
    line_index: int
    span_index: int
    span_char_range: tuple[int, int]

    def __post_init__(self) -> None:
        t = "TableGapSourceInterval"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        _need_int({"v": self.line_index}, "v", t, lo=0)
        _need_int({"v": self.span_index}, "v", t, lo=0)
        if not isinstance(self.span_char_range, tuple) or \
                len(self.span_char_range) != 2:
            _err(t, "span_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.span_char_range)]}, "_", t)
        if self.span_char_range[1] <= self.span_char_range[0]:
            _err(t, "span_char_range 必须为正长度，"
                    f"得到 {self.span_char_range!r}")

    @property
    def fragment_key(self) -> tuple:
        return (self.page_number, self.line_index, self.span_index)

    def sort_key(self) -> tuple:
        return (self.page_number, self.line_index, self.span_index,
                self.span_char_range[0], self.span_char_range[1])

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableGapSourceInterval",
            "page_number": self.page_number,
            "line_index": self.line_index,
            "span_index": self.span_index,
            "span_char_range": list(self.span_char_range),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableGapSourceInterval":
        t = "TableGapSourceInterval"
        d = _reject_unknown(d, {
            "schema_type", "page_number", "line_index", "span_index",
            "span_char_range"}, t)
        _need_enum(d, "schema_type", t, ("TableGapSourceInterval",))
        return cls(
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            span_index=_need_int(d, "span_index", t, lo=0),
            span_char_range=_need_int_pair(d, "span_char_range", t,
                                           strict=True),
        )


@dataclass(frozen=True)
class TableGapEntry:
    """一条结构缺口（§19.4.3）。缺口是**诚实未决**，不是静默补丁。

    `tsg-2` 新增 `source_intervals`：tyed gap 必须能说出它**精确覆盖了哪些真实
    LayoutSpan 字符**，否则"这个字符已被正式延期"和"这个字符谁都没管"在载荷上
    无法区分。空元组的语义是"该缺口不对应任何具体字符区间"（例如整页几何不可裁决、
    table 级 provenance 缺口），**不是**"覆盖全区"。
    """

    gap_kind: str
    detail_code: str
    page_number: int | None
    table_id: str | None
    component_id: str | None
    disposition_id: str | None
    blocks_document_capability: bool
    source_intervals: tuple[TableGapSourceInterval, ...] = ()

    def __post_init__(self) -> None:
        t = "TableGapEntry"
        if self.gap_kind not in TABLE_GAP_KINDS:
            _err(t, f"gap_kind 必须属于 {TABLE_GAP_KINDS}，得到 {self.gap_kind!r}")
        if not isinstance(self.detail_code, str) or self.detail_code == "":
            _err(t, "detail_code 必须为非空字符串（封闭码，不是自由说明）")
        if self.page_number is not None:
            _need_int({"v": self.page_number}, "v", t, lo=1)
        for name in ("table_id", "component_id", "disposition_id"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, str) or v == ""):
                _err(t, f"{name} 必须为非空字符串或 None")
        if not isinstance(self.blocks_document_capability, bool):
            _err(t, "blocks_document_capability 必须为 bool")
        if self.blocks_document_capability != \
                (self.gap_kind in GAP_BLOCKING_KINDS):
            _err(t, "blocks_document_capability 必须由 gap_kind 决定"
                    f"（阻断集合 {GAP_BLOCKING_KINDS}）")
        if not isinstance(self.source_intervals, tuple):
            _err(t, "source_intervals 必须为元组")
        keys = []
        for i, iv in enumerate(self.source_intervals):
            if not isinstance(iv, TableGapSourceInterval):
                _err(t, f"source_intervals[{i}] 必须为 TableGapSourceInterval")
            if self.page_number is not None and \
                    iv.page_number != self.page_number:
                _err(t, "source_intervals 不得越出本条缺口的页号")
            keys.append(iv.sort_key())
        if keys != sorted(keys):
            _err(t, "source_intervals 必须按 (页, 行, 片段, 起, 止) 升序")
        if len(set(keys)) != len(keys):
            _err(t, "source_intervals 不得重复")
        # 同一片段内的区间不得互相重叠：一个字符只能被同一缺口覆盖一次。
        by_fragment: dict = {}
        for iv in self.source_intervals:
            by_fragment.setdefault(iv.fragment_key, []).append(
                iv.span_char_range)
        for fkey, ranges in by_fragment.items():
            ordered = sorted(ranges)
            for (a, b), (c, d) in zip(ordered, ordered[1:]):
                if c < b:
                    _err(t, f"source_intervals 在同一片段 {fkey!r} 上重叠："
                            f"{(a, b)!r} 与 {(c, d)!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableGapEntry",
            "gap_kind": self.gap_kind,
            "detail_code": self.detail_code,
            "page_number": self.page_number,
            "table_id": self.table_id,
            "component_id": self.component_id,
            "disposition_id": self.disposition_id,
            "blocks_document_capability": self.blocks_document_capability,
            "source_intervals": [iv.to_dict() for iv in self.source_intervals],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableGapEntry":
        t = "TableGapEntry"
        d = _reject_unknown(d, {
            "schema_type", "gap_kind", "detail_code", "page_number", "table_id",
            "component_id", "disposition_id",
            "blocks_document_capability", "source_intervals"}, t)
        _need_enum(d, "schema_type", t, ("TableGapEntry",))
        pg = d.get("page_number")
        if pg is not None:
            pg = _need_int(d, "page_number", t, lo=1)
        return cls(
            gap_kind=_need_enum(d, "gap_kind", t, TABLE_GAP_KINDS),
            detail_code=_need_str(d, "detail_code", t),
            page_number=pg,
            table_id=_need_str(d, "table_id", t, none_ok=True),
            component_id=_need_str(d, "component_id", t, none_ok=True),
            disposition_id=_need_str(d, "disposition_id", t, none_ok=True),
            blocks_document_capability=_need_bool(
                d, "blocks_document_capability", t),
            source_intervals=_need_children(
                d, "source_intervals", t, TableGapSourceInterval.from_dict),
        )


@dataclass(frozen=True)
class TableCandidateAuditRow:
    """§19.14.1 `table_candidate_audit.jsonl` 的一行：一个候选的裁决**记录**。

    它是**审计记录**，不是身份对象：不进入任何 content fingerprint，也不被任何
    下游对象回指。`upstream_dependency_fingerprint` 由父容器携带（整份 audit 文件
    的头部），因此本行自身不带 `schema_version`——与其余子结构同规
    （`TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES`）。

    `outcome` 三态是**互斥且穷尽**的：

    - `accepted`：该候选最终成为一张 `TableObject`（`table_id` 必填）；
    - `rejected`：已有定论不是表（纯文本伪表、网格不可重建、列/行数不足、散文门、
      非表格视觉对象等），`reason` 给出封闭理由码；
    - `unresolved`：几何**无法裁决**（部分落入冻结范围、同时被多个冻结范围覆盖、
      或几何命中冻结 `regular` body span），必须显式保留为缺口，不得强行选"第一个"。
    """

    candidate_source: str
    strategy: str
    page_number: int
    bbox: tuple[float, float, float, float]
    outcome: str
    reason: str | None
    blocks_document: bool
    frozen_disposition_id: str | None
    closed_grid: bool
    edge_count: int
    intersection_count: int
    row_count: int
    column_count: int
    cell_count: int
    table_id: str | None
    #: §19.5.1 准入依据码（`CANDIDATE_ADMISSION_BASES`）。与 `reason` 互斥：只有
    #: `accepted` 行才非 `None`。**它不进入任何 content fingerprint**（本行是审计
    #: 记录，不是身份对象），但它是"这张表凭哪一条判据准入"的唯一回查点。
    admission_basis: str | None

    def __post_init__(self) -> None:
        t = "TableCandidateAuditRow"
        if not isinstance(self.candidate_source, str) or self.candidate_source == "":
            _err(t, "candidate_source 必须为非空字符串")
        if not isinstance(self.strategy, str) or self.strategy == "":
            _err(t, "strategy 必须为非空字符串")
        _need_int({"v": self.page_number}, "v", t, lo=1)
        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            _err(t, "bbox 必须为四元组")
        for v in self.bbox:
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                _err(t, "bbox 的分量必须为数字")
        if self.outcome not in CANDIDATE_AUDIT_OUTCOMES:
            _err(t, f"outcome 必须属于 {CANDIDATE_AUDIT_OUTCOMES}，"
                    f"得到 {self.outcome!r}")
        if not isinstance(self.blocks_document, bool):
            _err(t, "blocks_document 必须为 bool")
        for name in ("closed_grid",):
            if not isinstance(getattr(self, name), bool):
                _err(t, f"{name} 必须为 bool")
        for name in ("edge_count", "intersection_count", "row_count",
                     "column_count", "cell_count"):
            _need_int({"v": getattr(self, name)}, "v", t, lo=0)
        for name in ("reason", "frozen_disposition_id", "table_id",
                     "admission_basis"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, str) or v == ""):
                _err(t, f"{name} 必须为非空字符串或 None")
        # 三态与 reason / table_id 必须自洽：不得用 "accepted" 掩盖无表的候选，
        # 也不得让被拒候选缺少理由（拒绝理由必须可回查）。
        if self.outcome == "accepted" and self.table_id is None:
            _err(t, "outcome='accepted' 必须绑定 table_id")
        if self.outcome != "accepted" and self.table_id is not None:
            _err(t, "只有 accepted 候选才允许携带 table_id")
        if self.outcome == "accepted" and self.reason is not None:
            _err(t, "accepted 候选不得携带拒绝理由")
        if self.outcome != "accepted" and self.reason is None:
            _err(t, "非 accepted 候选必须给出理由码")
        # 准入依据与拒绝理由**互斥**：二者都不可缺，也都不得越界携带。
        if self.outcome == "accepted" and self.admission_basis is None:
            _err(t, "accepted 候选必须给出准入依据码")
        if (self.admission_basis is not None
                and self.admission_basis not in CANDIDATE_ADMISSION_BASES):
            _err(t, f"admission_basis 必须属于 {CANDIDATE_ADMISSION_BASES}，"
                    f"得到 {self.admission_basis!r}")
        if self.outcome != "accepted" and self.admission_basis is not None:
            _err(t, "非 accepted 候选不得携带准入依据")
        if self.blocks_document and not self.reason:
            _err(t, "阻断性候选必须说明理由")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableCandidateAuditRow",
            "candidate_source": self.candidate_source,
            "strategy": self.strategy,
            "page_number": self.page_number,
            "bbox": [self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3]],
            "outcome": self.outcome,
            "reason": self.reason,
            "blocks_document": self.blocks_document,
            "frozen_disposition_id": self.frozen_disposition_id,
            "closed_grid": self.closed_grid,
            "edge_count": self.edge_count,
            "intersection_count": self.intersection_count,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "cell_count": self.cell_count,
            "table_id": self.table_id,
            "admission_basis": self.admission_basis,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableCandidateAuditRow":
        t = "TableCandidateAuditRow"
        fields = {"schema_type", "candidate_source", "strategy", "page_number",
                  "bbox", "outcome", "reason", "blocks_document",
                  "frozen_disposition_id", "closed_grid", "edge_count",
                  "intersection_count", "row_count", "column_count", "cell_count",
                  "table_id", "admission_basis"}
        d = _reject_unknown(d, fields, t)
        _need_enum(d, "schema_type", t, ("TableCandidateAuditRow",))
        bbox = d.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            _err(t, "bbox 必须为四元数组")
        return cls(
            candidate_source=_need_str(d, "candidate_source", t),
            strategy=_need_str(d, "strategy", t),
            page_number=_need_int(d, "page_number", t, lo=1),
            bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
            outcome=_need_enum(d, "outcome", t, CANDIDATE_AUDIT_OUTCOMES),
            reason=_need_str(d, "reason", t, none_ok=True),
            blocks_document=_need_bool(d, "blocks_document", t),
            frozen_disposition_id=_need_str(d, "frozen_disposition_id", t,
                                            none_ok=True),
            closed_grid=_need_bool(d, "closed_grid", t),
            edge_count=_need_int(d, "edge_count", t, lo=0),
            intersection_count=_need_int(d, "intersection_count", t, lo=0),
            row_count=_need_int(d, "row_count", t, lo=0),
            column_count=_need_int(d, "column_count", t, lo=0),
            cell_count=_need_int(d, "cell_count", t, lo=0),
            table_id=_need_str(d, "table_id", t, none_ok=True),
            # 取值域由 `__post_init__` 对 `CANDIDATE_ADMISSION_BASES` 的闭合校验兜底
            # （与 `outcome` 同规：这里先做形状校验，语义校验在建对象时统一执行）。
            admission_basis=_need_str(d, "admission_basis", t, none_ok=True),
        )


@dataclass(frozen=True)
class ConservationTerm:
    """一层守恒里的一个分项（§19.9.3）。"""

    term: str
    term_kind: str
    count: int
    char_count: int
    interval_length: int

    def __post_init__(self) -> None:
        t = "ConservationTerm"
        if self.term_kind not in CONSERVATION_TERM_KINDS:
            _err(t, f"term_kind 必须属于 {CONSERVATION_TERM_KINDS}，"
                    f"得到 {self.term_kind!r}")
        if not isinstance(self.term, str) or self.term == "":
            _err(t, "term 必须为非空字符串")
        if self.term != CONSERVATION_TOTAL_TERM and \
                CONSERVATION_TERM_KIND_OF.get(self.term) != self.term_kind:
            _err(t, f"term {self.term!r} 的语义类别必须为 "
                    f"{CONSERVATION_TERM_KIND_OF.get(self.term)!r}，"
                    f"得到 {self.term_kind!r}")
        _need_int({"v": self.count}, "v", t, lo=0)
        _need_int({"v": self.char_count}, "v", t, lo=0)
        _need_int({"v": self.interval_length}, "v", t, lo=0)
        # 每个分项只填它自己那一类量，其余必须为 0（不得"三个都填上"掩盖口径）。
        nonzero = [self.count > 0, self.char_count > 0, self.interval_length > 0]
        expected = {"count": 0, "character": 1, "interval": 2}[self.term_kind]
        for i, flag in enumerate(nonzero):
            if flag and i != expected:
                _err(t, f"term_kind={self.term_kind!r} 的分项只能填对应的量")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ConservationTerm",
            "term": self.term,
            "term_kind": self.term_kind,
            "count": self.count,
            "char_count": self.char_count,
            "interval_length": self.interval_length,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConservationTerm":
        t = "ConservationTerm"
        d = _reject_unknown(d, {
            "schema_type", "term", "term_kind", "count", "char_count",
            "interval_length"}, t)
        _need_enum(d, "schema_type", t, ("ConservationTerm",))
        return cls(
            term=_need_str(d, "term", t),
            term_kind=_need_enum(d, "term_kind", t, CONSERVATION_TERM_KINDS),
            count=_need_int(d, "count", t, lo=0),
            char_count=_need_int(d, "char_count", t, lo=0),
            interval_length=_need_int(d, "interval_length", t, lo=0),
        )


@dataclass(frozen=True)
class ConservationLayer:
    """一层守恒（§19.9.3）。四层**分别验证**，不得跨层相加。"""

    layer_kind: str
    terms: tuple[ConservationTerm, ...]
    total: ConservationTerm
    balanced: bool
    problems: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "ConservationLayer"
        if self.layer_kind not in CONSERVATION_LAYER_KINDS:
            _err(t, f"layer_kind 必须属于 {CONSERVATION_LAYER_KINDS}，"
                    f"得到 {self.layer_kind!r}")
        if not isinstance(self.terms, tuple):
            _err(t, "terms 必须为元组")
        expected = CONSERVATION_LAYER_TERMS[self.layer_kind]
        got = tuple(x.term for x in self.terms)
        if got != expected:
            _err(t, f"第 {self.layer_kind} 层分项必须恰为 {expected}（固定顺序），"
                    f"得到 {got}")
        for i, term in enumerate(self.terms):
            if not isinstance(term, ConservationTerm):
                _err(t, f"terms[{i}] 必须为 ConservationTerm")
            if term.term_kind != CONSERVATION_TERM_KIND_OF[term.term]:
                _err(t, f"terms[{i}] 的语义类别与分项不符")
        if not isinstance(self.total, ConservationTerm):
            _err(t, "total 必须为 ConservationTerm")
        if not isinstance(self.balanced, bool):
            _err(t, "balanced 必须为 bool")
        if not isinstance(self.problems, tuple):
            _err(t, "problems 必须为元组")
        for p in self.problems:
            if not isinstance(p, str) or p == "":
                _err(t, "problems 必须为非空字符串元组")
        total_kind = self.total.term_kind
        if total_kind != conservation_layer_unit(self.layer_kind):
            _err(t, f"第 {self.layer_kind} 层的 total 语义类别必须为 "
                    f"{conservation_layer_unit(self.layer_kind)!r}，"
                    f"得到 {total_kind!r}")
        # 平衡判据只有一套实现：`conservation_layer_eligible`。它要求"分项之和恰等于
        # total"**且**"本层 problems 为空"**且**"必须为零的分项为 0"三者同时成立。
        # 因此 `problems` 非空而 `balanced=true` 在本构造器下无法成立，且这一条与
        # 复核层、验收机调用的是同一个函数，不存在第二套口径。
        if self.balanced != conservation_layer_eligible(
                self.layer_kind, conservation_layer_values(self),
                conservation_term_value(self.total), self.problems):
            _err(t, "balanced 必须等于分层守恒资格函数的判定结果"
                    "（算术守恒 + 本层 problems 为空 + 必须为零的分项为 0）")
        if not self.balanced and not self.problems:
            _err(t, "不平衡必须留下问题码（不得静默）")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ConservationLayer",
            "layer_kind": self.layer_kind,
            "terms": [x.to_dict() for x in self.terms],
            "total": self.total.to_dict(),
            "balanced": self.balanced,
            "problems": list(self.problems),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConservationLayer":
        t = "ConservationLayer"
        d = _reject_unknown(d, {
            "schema_type", "layer_kind", "terms", "total", "balanced",
            "problems"}, t)
        _need_enum(d, "schema_type", t, ("ConservationLayer",))
        return cls(
            layer_kind=_need_enum(d, "layer_kind", t, CONSERVATION_LAYER_KINDS),
            terms=_need_children(d, "terms", t, ConservationTerm.from_dict),
            total=_need_child(d, "total", t, ConservationTerm.from_dict),
            balanced=_need_bool(d, "balanced", t),
            problems=_need_str_tuple(d, "problems", t),
        )


def _need_child(d: dict, key: str, typename: str, decoder: Callable[[Any], Any]) -> Any:
    v = d.get(key)
    if v is None:
        _err(typename, f"缺必填字段: {key}")
    try:
        return decoder(v)
    except SchemaValidationError as e:
        _err(typename, f"{key} 解码失败：{e}")


# ---------------------------------------------------------------------------
# 4. 上游依赖指纹（DAG 的**唯一**根）
# ---------------------------------------------------------------------------

#: `upstream_dependency_fingerprint` 覆盖的字段名（封闭、有序）。
UPSTREAM_DEPENDENCY_FIELDS: tuple[str, ...] = (
    "document_id", "document_version", "evidence_set_version",
    "page_layout_id", "outline_id", "alignment_id",
    "verified_span_snapshot_id", "verified_span_input_fingerprint",
    "verified_span_content_fingerprint", "verified_span_verification_fingerprint",
    "page_layout_schema_version", "outline_schema_version",
    "span_schema_version", "span_build_snapshot_schema_version",
    "span_builder_version", "handoff_identity",
    # TS5 自身版本束（profile bump 必须改变它）
    "table_schema_version", "table_builder_version", "table_cell_schema_version",
    "final_span_schema_version", "table_relation_schema_version",
    "table_relation_builder_version", "table_range_decision_schema_version",
    "final_component_binding_schema_version",
    "table_citable_coverage_schema_version",
    "table_structure_gap_schema_version",
    "final_material_conservation_schema_version",
    "final_material_structure_schema_version",
    "table_geometry_version", "table_classification_profile_version",
    "table_cell_block_profile_version", "ts5_final_span_builder_version",
    # profile / settings 资产身份（file SHA256）
    "classification_profile_file_fingerprint",
    "classification_profile_content_fingerprint",
    "cell_block_profile_file_fingerprint",
    "cell_block_profile_content_fingerprint",
    "geometry_settings_fingerprint",
    "qualification_policy_key", "qualification_policy_fingerprint",
)


def upstream_dependency_fingerprint(payload: dict) -> str:
    """从**唯一受信根**派生 TS5 的上游依赖指纹。

    这里**只**接受 `UPSTREAM_DEPENDENCY_FIELDS` 恰好齐全的载荷：缺字段、多字段一律
    拒绝。这样"少放一个 profile hash 就悄悄换了依赖束"在构造期就不可能发生。
    """
    if not isinstance(payload, dict):
        raise SchemaValidationError(
            "upstream_dependency_fingerprint 需要 dict 载荷")
    missing = [k for k in UPSTREAM_DEPENDENCY_FIELDS if k not in payload]
    extra = sorted(set(payload) - set(UPSTREAM_DEPENDENCY_FIELDS))
    if missing:
        raise SchemaValidationError(
            f"上游依赖束缺字段: {missing}（少一项即换依赖束，必须显式登记）")
    if extra:
        raise SchemaValidationError(f"上游依赖束含未登记字段: {extra}")
    return sha256_canonical({k: payload[k] for k in UPSTREAM_DEPENDENCY_FIELDS})


# ---------------------------------------------------------------------------
# 5. `TableObjectV4`（`to-4` current wire）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableObjectV4:
    """`to-4`：TS5 的**正式表格对象**（Evidence-backed material / navigation object）。

    它不是 `FinancialSnapshot`，也不得取得任何财务数字权威：
    `is_financial_authority()` **恒**为 `False`，且不可覆盖。

    身份两层（§19.4.4）：

    - `table_locator`：只绑定 raw PDF/document version、page、量化物理 bbox、
      页内源序与 geometry version。**classification/profile 变化不得改变它**；
    - `table_id`：在 locator 之上再绑定规范化 cell/content/provenance、
      `structure_class` 与 `upstream_dependency_fingerprint`。
    """

    table_locator: str
    table_id: str
    schema_version: str
    table_builder_version: str
    cell_schema_version: str
    geometry_version: str
    geometry_settings_fingerprint: str
    classification_profile_version: str
    classification_profile_file_fingerprint: str
    classification_profile_content_fingerprint: str
    cell_block_profile_version: str
    cell_block_profile_file_fingerprint: str
    cell_block_profile_content_fingerprint: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    verified_span_snapshot_id: str
    upstream_dependency_fingerprint: str
    page_number: int
    page_bbox: tuple[float, float, float, float]
    source_order_index: int
    owner: TableOwnerRef
    structure_kind: str
    structure_class: str
    structure_state: str
    structure_state_reason: str | None
    header_absence_reason: str | None
    missing_or_uncertain_fields: tuple[str, ...]
    column_count: int
    title_blocks: tuple[TableCellBlock, ...]
    unit_text: str | None
    unit_blocks: tuple[TableCellBlock, ...]
    note_blocks: tuple[TableCellBlock, ...]
    rows: tuple[TableRowV4, ...]
    source_refs: tuple[TableCellSourceRef, ...]
    continuation_anchor_locator: str | None
    continuation_candidate_locators: tuple[str, ...]
    content_fingerprint: str
    structure_fingerprint: str
    provenance_fingerprint: str

    SCHEMA_CONSTANT = "TABLE_SCHEMA_VERSION"

    # --- 构造期不变量 -----------------------------------------------------

    def __post_init__(self) -> None:
        t = "TableObjectV4"
        for name in ("table_locator", "table_id", "document_id", "document_version",
                     "evidence_set_version", "page_layout_id", "outline_id",
                     "verified_span_snapshot_id", "upstream_dependency_fingerprint",
                     "geometry_settings_fingerprint",
                     "classification_profile_file_fingerprint",
                     "classification_profile_content_fingerprint",
                     "cell_block_profile_file_fingerprint",
                     "cell_block_profile_content_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        for field, constant in (
                ("table_builder_version", "TABLE_BUILDER_VERSION"),
                ("cell_schema_version", "TABLE_CELL_SCHEMA_VERSION"),
                ("geometry_version", "TABLE_GEOMETRY_VERSION"),
                ("classification_profile_version",
                 "TABLE_CLASSIFICATION_PROFILE_VERSION"),
                ("cell_block_profile_version",
                 "TABLE_CELL_BLOCK_PROFILE_VERSION")):
            _check_registered_constant(constant)
            expected = V.VERSION_CONSTANTS[constant]
            if getattr(self, field) != expected:
                _err(t, f"{field} 必须为当前版本 {expected!r}，"
                        f"得到 {getattr(self, field)!r}")
        for field in ("geometry_settings_fingerprint",
                      "classification_profile_file_fingerprint",
                      "classification_profile_content_fingerprint",
                      "cell_block_profile_file_fingerprint",
                      "cell_block_profile_content_fingerprint",
                      "upstream_dependency_fingerprint"):
            if not _is_sha256(getattr(self, field)):
                _err(t, f"{field} 必须为 64 位小写十六进制 sha256")
        _need_int({"v": self.page_number}, "v", t, lo=1)
        _need_int({"v": self.source_order_index}, "v", t, lo=0)
        if not isinstance(self.page_bbox, tuple) or len(self.page_bbox) != 4:
            _err(t, "page_bbox 必须为 (x0,y0,x1,y1)")
        object.__setattr__(self, "page_bbox",
                           tuple(quantize(x) for x in self.page_bbox))
        _check_bbox(self.page_bbox, f"{t}.page_bbox")
        if not isinstance(self.owner, TableOwnerRef):
            _err(t, "owner 必须为 TableOwnerRef（不得 orphan）")
        if self.structure_kind not in STRUCTURE_KINDS:
            _err(t, f"structure_kind 必须属于 {STRUCTURE_KINDS}，"
                    f"得到 {self.structure_kind!r}")
        if self.structure_class not in TABLES_STRUCTURE_CLASSES:
            _err(t, f"structure_class 必须属于 {TABLES_STRUCTURE_CLASSES}，"
                    f"得到 {self.structure_class!r}")
        if self.structure_state not in STRUCTURE_STATES:
            _err(t, f"structure_state 必须属于 {STRUCTURE_STATES}，"
                    f"得到 {self.structure_state!r}")
        # §19.6.3：unassigned owner 的分类**默认** unclassified，不得因表内文字猜。
        if self.owner.owner_kind == "unassigned_boundary" and \
                self.structure_class != "unclassified":
            _err(t, "unassigned_boundary owner 的 structure_class 必须为 "
                    "'unclassified'（不得凭表内文字猜成财务/附注/业务表）")
        if not _owner_is_boundary_closed(self.owner, page_number=self.page_number):
            _err(t, "owner 的 source boundary 必须包含该表的物理页"
                    "（跨 document/outline/页一律拒绝）")
        # §19.6.1：headerless_grid 只能 partial，且 header 缺省原因固定。
        if self.structure_kind == "headerless_grid":
            if self.structure_state != "partial":
                _err(t, "headerless_grid 只能是 partial（不得单独授权集合完备或列语义）")
            if self.header_absence_reason is None:
                _err(t, "headerless_grid 必须给出 header_absence_reason")
        if self.header_absence_reason is not None and \
                self.header_absence_reason not in HEADER_ABSENCE_REASONS:
            _err(t, f"header_absence_reason 必须属于 {HEADER_ABSENCE_REASONS}，"
                    f"得到 {self.header_absence_reason!r}")
        if self.structure_state == "partial" and self.structure_state_reason is None:
            _err(t, "partial 必须给出 structure_state_reason")
        if self.structure_state == "complete" and self.structure_state_reason is not None:
            _err(t, "complete 不得携带 structure_state_reason")
        for code in self.missing_or_uncertain_fields:
            if code not in MISSING_FIELD_CODES:
                _err(t, f"missing_or_uncertain_fields 必须属于 {MISSING_FIELD_CODES}，"
                        f"得到 {code!r}")
        if len(set(self.missing_or_uncertain_fields)) != \
                len(self.missing_or_uncertain_fields):
            _err(t, "missing_or_uncertain_fields 不得重复")
        if self.missing_or_uncertain_fields != \
                tuple(sorted(self.missing_or_uncertain_fields)):
            _err(t, "missing_or_uncertain_fields 必须按字典序（固定顺序）")
        self._check_rows(t)
        self._check_source_refs(t)
        self._check_blocks(t)
        if self.continuation_anchor_locator is not None and (
                not isinstance(self.continuation_anchor_locator, str)
                or self.continuation_anchor_locator == ""):
            _err(t, "continuation_anchor_locator 必须为非空字符串或 None")
        if self.continuation_anchor_locator == self.table_locator:
            _err(t, "续表锚点不得是自身")
        locs = list(self.continuation_candidate_locators)
        if locs != sorted(locs) or len(set(locs)) != len(locs):
            _err(t, "continuation_candidate_locators 必须去重且按字典序")
        if self.table_locator in locs:
            _err(t, "续表候选不得包含自身")
        # 身份复算（locator 与 revision 两套都必须可外部复算）。
        expected_loc = locator("to4", self.locator_payload())
        if self.table_locator != expected_loc:
            _err(t, "table_locator 与派生定位不一致："
                    f"{self.table_locator!r} != {expected_loc!r}")
        expected_id = identity("to4", self.identity_payload())
        if self.table_id != expected_id:
            _err(t, "table_id 与派生身份不一致："
                    f"{self.table_id!r} != {expected_id!r}")
        for field, payload in (("content_fingerprint", self.content_payload()),
                               ("structure_fingerprint", self.structure_payload()),
                               ("provenance_fingerprint",
                                self.provenance_payload())):
            _check_fingerprint(t, field, getattr(self, field), payload)

    # --- 内部校验 ---------------------------------------------------------

    def _check_rows(self, t: str) -> None:
        if not isinstance(self.rows, tuple) or not self.rows:
            _err(t, "表格必须至少一行（仅标题 / 仅表头 / 纯装饰线不得成表）")
        for i, row in enumerate(self.rows):
            if not isinstance(row, TableRowV4):
                _err(t, f"rows[{i}] 必须为 TableRowV4")
            if row.row_index != i:
                _err(t, f"rows[{i}].row_index 必须等于其序号 {i}")
        _need_int({"v": self.column_count}, "v", t, lo=2)
        roles = [r.role for r in self.rows]
        header_count = sum(1 for r in roles if r == "header")
        if roles[:header_count] != ["header"] * header_count:
            _err(t, "header 行必须构成连续前缀（不得把第一条数据行吞成 header）")
        body_like = sum(1 for r in roles if r != "header")
        if body_like < 1:
            _err(t, "表格必须至少一行表体（仅表头不得成表）")
        if self.structure_kind == "key_value_form" and body_like < 2:
            _err(t, "key_value_form 必须有至少两行真实内容")
        total_count = 0
        for r in reversed(self.rows):
            if r.role == "total":
                total_count += 1
            else:
                break
        if total_count > 0 and roles[len(roles) - total_count:] != ["total"] * total_count:
            _err(t, "total 行必须构成连续后缀")
        self._check_grid(t)

    def _check_grid(self, t: str) -> None:
        row_count = len(self.rows)
        covered: dict[tuple[int, int], int] = {}
        for row in self.rows:
            for cell in row.cells:
                if cell.row != row.row_index:
                    _err(t, f"cell({cell.row},{cell.column}) 出现在 row_index="
                            f"{row.row_index} 的行里")
                for dr in range(cell.rowspan):
                    for dc in range(cell.colspan):
                        key = (cell.row + dr, cell.column + dc)
                        if key in covered:
                            _err(t, f"cell 覆盖重叠于 {key}")
                        covered[key] = 1
        for (r, c) in covered:
            if r >= row_count:
                _err(t, f"cell 越出行数范围: {key_text(r, c)}")
            if c >= self.column_count:
                _err(t, f"cell 越出列数范围: {key_text(r, c)}")
        holes = [(r, c) for r in range(row_count) for c in range(self.column_count)
                 if (r, c) not in covered]
        if holes:
            _err(t, f"cell 网格存在未解释空洞: {holes[:8]}")

    def _check_source_refs(self, t: str) -> None:
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            _err(t, "表格必须至少一条真实来源片段")
        ids: list[str] = []
        keys: list[tuple] = []
        for i, ref in enumerate(self.source_refs):
            if not isinstance(ref, TableCellSourceRef):
                _err(t, f"source_refs[{i}] 必须为 TableCellSourceRef")
            ids.append(ref.source_ref_id)
            keys.append(_source_ref_sort_key(ref))
        if len(set(ids)) != len(ids):
            _err(t, "表格级 source_ref_id 不得重复")
        if keys != sorted(keys):
            _err(t, "source_refs 必须按 (页, 行, 片段索引, 片段内起点) 升序")
        by_span: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
        for ref in self.source_refs:
            by_span.setdefault(
                (ref.interval.page_number, ref.interval.line_index,
                 ref.interval.span_index), []).append(ref.interval.span_char_range)
        for key, ranges in by_span.items():
            ranges = sorted(ranges)
            for a, b in zip(ranges, ranges[1:]):
                if b[0] < a[1]:
                    _err(t, f"同一 LayoutSpan {key} 上的来源片段重叠：{a} / {b}")

    def _check_blocks(self, t: str) -> None:
        if not isinstance(self.title_blocks, tuple):
            _err(t, "title_blocks 必须为元组")
        for name in ("unit_blocks", "note_blocks"):
            if not isinstance(getattr(self, name), tuple):
                _err(t, f"{name} 必须为元组")
        for name in ("title_blocks", "unit_blocks", "note_blocks"):
            for i, blk in enumerate(getattr(self, name)):
                if not isinstance(blk, TableCellBlock):
                    _err(t, f"{name}[{i}] 必须为 TableCellBlock")
                if blk.block_index != i:
                    _err(t, f"{name}[{i}].block_index 必须等于其序号 {i}")
        if self.unit_text is not None and (not isinstance(self.unit_text, str)
                                           or self.unit_text == ""):
            _err(t, "unit_text 必须为非空字符串或 None")
        if (self.unit_text is None) != (len(self.unit_blocks) == 0):
            _err(t, "unit_text 与 unit_blocks 必须同时存在或同时缺省")
        expected_unit = CELL_BLOCK_SEPARATOR.join(b.text for b in self.unit_blocks)
        if self.unit_text is not None and self.unit_text != expected_unit:
            _err(t, "unit_text 必须由 unit_blocks 以冻结分隔规则重构")
        # 表级 `source_refs` 必须是**全部被消费片段**的并集，且每个片段只被消费一次：
        # 这样 §19.9.3 第 3 层 Layout 文本守恒才有唯一分区。
        pool = {r.source_ref_id for r in self.source_refs}
        cell_ids = {x.source_ref_id for c in self.all_cells for x in c.source_refs}
        outer_ids: list[str] = []
        for name in ("title_blocks", "unit_blocks", "note_blocks"):
            for blk in getattr(self, name):
                for rid in blk.source_ref_ids:
                    if rid not in pool:
                        _err(t, f"{name} 引用了未知 source_ref_id {rid!r}")
                    outer_ids.append(rid)
        if len(set(outer_ids)) != len(outer_ids):
            _err(t, "同一来源片段不得被两个表级块（标题/单位/注释）重复消费")
        overlap = sorted(set(outer_ids) & cell_ids)
        if overlap:
            _err(t, f"同一来源片段不得同时被 cell 与表级块消费：{overlap}")
        if set(outer_ids) | cell_ids != pool:
            _err(t, "表级 source_refs 必须是全部被消费片段的并集（不得有游离片段）")

    # --- 身份载荷 ---------------------------------------------------------

    def locator_payload(self) -> dict:
        """**只**含物理位置与源序：profile / classification 变化不得改变它。"""
        return {
            "document_id": self.document_id,
            "document_version": self.document_version,
            "page_layout_id": self.page_layout_id,
            "page_number": self.page_number,
            "page_bbox": list(self.page_bbox),
            "source_order_index": self.source_order_index,
            "geometry_version": self.geometry_version,
        }

    def content_payload(self) -> dict:
        return {
            "structure_kind": self.structure_kind,
            "column_count": self.column_count,
            "rows": [r.to_dict() for r in self.rows],
            "title_blocks": [b.to_dict() for b in self.title_blocks],
            "unit_blocks": [b.to_dict() for b in self.unit_blocks],
            "unit_text": self.unit_text,
            "note_blocks": [b.to_dict() for b in self.note_blocks],
        }

    def structure_payload(self) -> dict:
        return {
            "structure_class": self.structure_class,
            "structure_state": self.structure_state,
            "structure_state_reason": self.structure_state_reason,
            "header_absence_reason": self.header_absence_reason,
            "missing_or_uncertain_fields": list(self.missing_or_uncertain_fields),
            "classification_profile_version":
                self.classification_profile_version,
            "classification_profile_file_fingerprint":
                self.classification_profile_file_fingerprint,
            "classification_profile_content_fingerprint":
                self.classification_profile_content_fingerprint,
            "cell_block_profile_version": self.cell_block_profile_version,
            "cell_block_profile_file_fingerprint":
                self.cell_block_profile_file_fingerprint,
            "cell_block_profile_content_fingerprint":
                self.cell_block_profile_content_fingerprint,
        }

    def provenance_payload(self) -> dict:
        return {
            "owner": self.owner.to_dict(),
            "source_refs": [r.to_dict() for r in self.source_refs],
            "continuation_anchor_locator": self.continuation_anchor_locator,
            "continuation_candidate_locators":
                list(self.continuation_candidate_locators),
        }

    def identity_payload(self) -> dict:
        return {
            "table_locator": self.table_locator,
            "schema_version": self.schema_version,
            "table_builder_version": self.table_builder_version,
            "cell_schema_version": self.cell_schema_version,
            "geometry_version": self.geometry_version,
            "geometry_settings_fingerprint": self.geometry_settings_fingerprint,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_layout_id": self.page_layout_id,
            "outline_id": self.outline_id,
            "verified_span_snapshot_id": self.verified_span_snapshot_id,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "content_fingerprint": self.content_fingerprint,
            "structure_fingerprint": self.structure_fingerprint,
            "provenance_fingerprint": self.provenance_fingerprint,
        }

    # --- 权威边界 ---------------------------------------------------------

    def is_financial_authority(self) -> bool:
        """**恒** `False`：TableObject 是 material/navigation 对象，不是财务权威。"""
        return False

    def is_evidence_backed(self) -> bool:
        """至少一条来源片段可引用 ⇒ Evidence-backed（与 TS4 同一口径）。"""
        return self.citable_source_ref_count > 0

    @property
    def citable_source_ref_count(self) -> int:
        return sum(1 for r in self.source_refs if r.citable)

    @property
    def all_source_refs_citable(self) -> bool:
        return self.citable_source_ref_count == len(self.source_refs)

    @property
    def header_rows(self) -> tuple[TableRowV4, ...]:
        return tuple(r for r in self.rows if r.role == "header")

    @property
    def body_rows(self) -> tuple[TableRowV4, ...]:
        return tuple(r for r in self.rows if r.role == "body")

    @property
    def subtotal_rows(self) -> tuple[TableRowV4, ...]:
        return tuple(r for r in self.rows if r.role == "subtotal")

    @property
    def total_rows(self) -> tuple[TableRowV4, ...]:
        return tuple(r for r in self.rows if r.role == "total")

    @property
    def all_cells(self) -> tuple[TableCellV4, ...]:
        return tuple(c for r in self.rows for c in r.cells)

    def source_ref_by_id(self, source_ref_id: str) -> TableCellSourceRef | None:
        for r in self.source_refs:
            if r.source_ref_id == source_ref_id:
                return r
        return None

    def cell_id(self, row: int, column: int) -> str:
        """cell 的**可回查句柄**：`<table_id>#r<row>c<column>`。

        它不是独立身份轴，只是 table 身份下的稳定寻址串（cell 的内容身份由
        `content_fingerprint` 覆盖）。
        """
        return f"{self.table_id}#r{row}c{column}"

    # --- wire ------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableObjectV4",
            "table_locator": self.table_locator,
            "table_id": self.table_id,
            "schema_version": self.schema_version,
            "table_builder_version": self.table_builder_version,
            "cell_schema_version": self.cell_schema_version,
            "geometry_version": self.geometry_version,
            "geometry_settings_fingerprint": self.geometry_settings_fingerprint,
            "classification_profile_version": self.classification_profile_version,
            "classification_profile_file_fingerprint":
                self.classification_profile_file_fingerprint,
            "classification_profile_content_fingerprint":
                self.classification_profile_content_fingerprint,
            "cell_block_profile_version": self.cell_block_profile_version,
            "cell_block_profile_file_fingerprint":
                self.cell_block_profile_file_fingerprint,
            "cell_block_profile_content_fingerprint":
                self.cell_block_profile_content_fingerprint,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_layout_id": self.page_layout_id,
            "outline_id": self.outline_id,
            "verified_span_snapshot_id": self.verified_span_snapshot_id,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "page_number": self.page_number,
            "page_bbox": list(self.page_bbox),
            "source_order_index": self.source_order_index,
            "owner": self.owner.to_dict(),
            "structure_kind": self.structure_kind,
            "structure_class": self.structure_class,
            "structure_state": self.structure_state,
            "structure_state_reason": self.structure_state_reason,
            "header_absence_reason": self.header_absence_reason,
            "missing_or_uncertain_fields": list(self.missing_or_uncertain_fields),
            "column_count": self.column_count,
            "title_blocks": [b.to_dict() for b in self.title_blocks],
            "unit_text": self.unit_text,
            "unit_blocks": [b.to_dict() for b in self.unit_blocks],
            "note_blocks": [b.to_dict() for b in self.note_blocks],
            "rows": [r.to_dict() for r in self.rows],
            "source_refs": [r.to_dict() for r in self.source_refs],
            "continuation_anchor_locator": self.continuation_anchor_locator,
            "continuation_candidate_locators":
                list(self.continuation_candidate_locators),
            "content_fingerprint": self.content_fingerprint,
            "structure_fingerprint": self.structure_fingerprint,
            "provenance_fingerprint": self.provenance_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableObjectV4":
        t = "TableObjectV4"
        d = _reject_unknown(d, {
            "schema_type", "table_locator", "table_id", "schema_version",
            "table_builder_version", "cell_schema_version", "geometry_version",
            "geometry_settings_fingerprint", "classification_profile_version",
            "classification_profile_file_fingerprint",
            "classification_profile_content_fingerprint",
            "cell_block_profile_version", "cell_block_profile_file_fingerprint",
            "cell_block_profile_content_fingerprint", "document_id",
            "document_version", "evidence_set_version", "page_layout_id",
            "outline_id", "verified_span_snapshot_id",
            "upstream_dependency_fingerprint", "page_number", "page_bbox",
            "source_order_index", "owner", "structure_kind", "structure_class",
            "structure_state", "structure_state_reason", "header_absence_reason",
            "missing_or_uncertain_fields", "column_count", "title_blocks",
            "unit_text", "unit_blocks", "note_blocks", "rows", "source_refs",
            "continuation_anchor_locator", "continuation_candidate_locators",
            "content_fingerprint", "structure_fingerprint",
            "provenance_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("TableObjectV4",))
        return cls(
            table_locator=_need_str(d, "table_locator", t),
            table_id=_need_str(d, "table_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            table_builder_version=_need_str(d, "table_builder_version", t),
            cell_schema_version=_need_str(d, "cell_schema_version", t),
            geometry_version=_need_str(d, "geometry_version", t),
            geometry_settings_fingerprint=_need_sha256(
                d, "geometry_settings_fingerprint", t),
            classification_profile_version=_need_str(
                d, "classification_profile_version", t),
            classification_profile_file_fingerprint=_need_sha256(
                d, "classification_profile_file_fingerprint", t),
            classification_profile_content_fingerprint=_need_sha256(
                d, "classification_profile_content_fingerprint", t),
            cell_block_profile_version=_need_str(
                d, "cell_block_profile_version", t),
            cell_block_profile_file_fingerprint=_need_sha256(
                d, "cell_block_profile_file_fingerprint", t),
            cell_block_profile_content_fingerprint=_need_sha256(
                d, "cell_block_profile_content_fingerprint", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            outline_id=_need_str(d, "outline_id", t),
            verified_span_snapshot_id=_need_str(d, "verified_span_snapshot_id", t),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            page_number=_need_int(d, "page_number", t, lo=1),
            page_bbox=_need_bbox(d, "page_bbox", t),
            source_order_index=_need_int(d, "source_order_index", t, lo=0),
            owner=_need_child(d, "owner", t, TableOwnerRef.from_dict),
            structure_kind=_need_enum(d, "structure_kind", t, STRUCTURE_KINDS),
            structure_class=_need_enum(d, "structure_class", t,
                                       TABLES_STRUCTURE_CLASSES),
            structure_state=_need_enum(d, "structure_state", t, STRUCTURE_STATES),
            structure_state_reason=_need_str(d, "structure_state_reason", t,
                                             none_ok=True),
            header_absence_reason=_need_str(d, "header_absence_reason", t,
                                            none_ok=True),
            missing_or_uncertain_fields=_need_str_tuple(
                d, "missing_or_uncertain_fields", t),
            column_count=_need_int(d, "column_count", t, lo=2),
            title_blocks=_need_children(d, "title_blocks", t,
                                        TableCellBlock.from_dict),
            unit_text=_need_str(d, "unit_text", t, none_ok=True),
            unit_blocks=_need_children(d, "unit_blocks", t,
                                       TableCellBlock.from_dict),
            note_blocks=_need_children(d, "note_blocks", t,
                                       TableCellBlock.from_dict),
            rows=_need_children(d, "rows", t, TableRowV4.from_dict),
            source_refs=_need_children(d, "source_refs", t,
                                       TableCellSourceRef.from_dict),
            continuation_anchor_locator=_need_str(
                d, "continuation_anchor_locator", t, none_ok=True),
            continuation_candidate_locators=_need_str_tuple(
                d, "continuation_candidate_locators", t),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
            structure_fingerprint=_need_sha256(d, "structure_fingerprint", t),
            provenance_fingerprint=_need_sha256(d, "provenance_fingerprint", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "TableObjectV4":
        """由非派生字段两阶段装配（locator / 三指纹 / revision id 全部本类派生）。"""
        return _assemble(
            cls, fields,
            locator_attr="table_locator", locator_kind="to4",
            locator_method="locator_payload",
            fingerprint_attrs=(
                ("content_fingerprint", "content_payload"),
                ("structure_fingerprint", "structure_payload"),
                ("provenance_fingerprint", "provenance_payload"),
            ),
            identity_attr="table_id", identity_kind="to4",
            identity_method="identity_payload")


def key_text(row: int, column: int) -> str:
    """网格坐标的可读文本（仅用于诊断，不进入身份）。"""
    return f"(r{row},c{column})"


#: current 别名（§19.4.2）：`TableObject` 指向 to-4。
TableObject = TableObjectV4


# ---------------------------------------------------------------------------
# 6. `FinalOutlineSpan`（`fos-1` / `sbf-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinalOutlineSpan:
    """§19.7.2：由 `sbf-1` **重新生成身份**的 final paragraph span。

    它**不是** TS4 的 `OutlineSpan`（`os-4` + `sb-7`），也不复用其身份：decision
    不构成 citable 资格，构建器必须重新计算冻结 TS4-B 的 12 个边界因子与 0.85
    必要阈值。`boundary_factors` 因此**原样保存**那 12 项（可由策略逐项复算），
    `confidence` 必须是左因子 × 右因子。

    `citable_intervals` 从真实 TS4 components 重建，不允许仅复制旧 projection。
    """

    span_locator: str
    span_id: str
    schema_version: str
    span_builder_version: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    upstream_dependency_fingerprint: str
    node_id: str | None
    source_boundary_id: str
    ts4_source_span_locator: str | None
    ts4_source_disposition_id: str | None
    start_page: int
    start_line: int
    end_page: int
    end_line: int
    component_ids: tuple[str, ...]
    evidence_char_ranges: tuple[tuple[str, int, int], ...]
    boundary_factors: tuple[BoundaryFactor, ...]
    confidence: float
    confidence_min: float
    normalized_text: str
    citable_intervals: tuple[CitableInterval, ...]
    content_fingerprint: str

    SCHEMA_CONSTANT = "FINAL_SPAN_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "FinalOutlineSpan"
        for name in ("span_locator", "span_id", "document_id", "document_version",
                     "evidence_set_version", "page_layout_id", "outline_id",
                     "upstream_dependency_fingerprint", "source_boundary_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if self.span_builder_version != V.TS5_FINAL_SPAN_BUILDER_VERSION:
            _err(t, f"span_builder_version 必须为 {V.TS5_FINAL_SPAN_BUILDER_VERSION!r}"
                    f"（final span 必须是 sbf-1 新身份，不得复用 TS4 sb-7），"
                    f"得到 {self.span_builder_version!r}")
        if self.node_id is None and self.ts4_source_disposition_id is None:
            _err(t, "final span 必须绑定 node_id 或 TS4 disposition（不得无来源）")
        for name in ("ts4_source_span_locator", "ts4_source_disposition_id"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, str) or v == ""):
                _err(t, f"{name} 必须为非空字符串或 None")
        _need_int({"v": self.start_page}, "v", t, lo=1)
        _need_int({"v": self.start_line}, "v", t, lo=0)
        _need_int({"v": self.end_page}, "v", t, lo=1)
        _need_int({"v": self.end_line}, "v", t, lo=0)
        if (self.end_page, self.end_line) < (self.start_page, self.start_line):
            _err(t, "结束位置不得早于起始位置")
        if not isinstance(self.component_ids, tuple) or not self.component_ids:
            _err(t, "final span 必须绑定真实 TS4 components")
        if len(set(self.component_ids)) != len(self.component_ids):
            _err(t, "component_ids 不得重复")
        if tuple(sorted(self.component_ids)) != self.component_ids:
            _err(t, "component_ids 必须按字典序（固定顺序）")
        for i, item in enumerate(self.evidence_char_ranges):
            if not isinstance(item, tuple) or len(item) != 3:
                _err(t, f"evidence_char_ranges[{i}] 必须为 (evidence_block_id, start, end)")
            block_id, lo, hi = item
            if not isinstance(block_id, str) or block_id == "":
                _err(t, f"evidence_char_ranges[{i}] 的 evidence_block_id 必须为非空字符串")
            _need_int({"v": lo}, "v", t, lo=0)
            _need_int({"v": hi}, "v", t, lo=0)
            if hi <= lo:
                _err(t, f"evidence_char_ranges[{i}] 必须为正长度")
        keys = [(b, lo) for b, lo, _hi in self.evidence_char_ranges]
        if keys != sorted(keys):
            _err(t, "evidence_char_ranges 必须按 (evidence_block_id, start) 升序")
        if not isinstance(self.boundary_factors, tuple) or \
                len(self.boundary_factors) != 12:
            _err(t, "boundary_factors 必须恰为 12 项（冻结 TS4-B 因子表）")
        sides = {}
        for i, f in enumerate(self.boundary_factors):
            if not isinstance(f, BoundaryFactor):
                _err(t, f"boundary_factors[{i}] 必须为 BoundaryFactor")
            sides.setdefault((f.side, f.cause), []).append(f.factor)
        for key, vals in sides.items():
            if len(vals) != 1:
                _err(t, f"boundary_factors 的 {key} 重复")
        _need_num({"v": self.confidence}, "v", t, lo=0.0, hi=1.0)
        object.__setattr__(self, "confidence", quantize(self.confidence))
        _need_num({"v": self.confidence_min}, "v", t, lo=0.0, hi=1.0)
        if not isinstance(self.normalized_text, str) or self.normalized_text == "":
            _err(t, "normalized_text 必须为非空字符串")
        if not isinstance(self.citable_intervals, tuple):
            _err(t, "citable_intervals 必须为元组")
        self._check_citable_partition(t)
        expected_loc = locator("fos", self.locator_payload())
        if self.span_locator != expected_loc:
            _err(t, "span_locator 与派生定位不一致："
                    f"{self.span_locator!r} != {expected_loc!r}")
        expected_id = identity("fos", self.identity_payload())
        if self.span_id != expected_id:
            _err(t, "span_id 与派生身份不一致："
                    f"{self.span_id!r} != {expected_id!r}")
        _check_fingerprint(t, "content_fingerprint", self.content_fingerprint,
                           self.content_payload())

    def content_payload(self) -> dict:
        return {"normalized_text": self.normalized_text}

    def _check_citable_partition(self, t: str) -> None:
        for i, iv in enumerate(self.citable_intervals):
            if not isinstance(iv, CitableInterval):
                _err(t, f"citable_intervals[{i}] 必须为 CitableInterval")
        keys = [iv.char_range for iv in self.citable_intervals]
        if keys != sorted(keys):
            _err(t, "citable_intervals 必须按 char_range 升序")
        for a, b in zip(keys, keys[1:]):
            if b[0] < a[1]:
                _err(t, f"citable_intervals 之间不得重叠：{a} / {b}")

    @property
    def citable_char_count(self) -> int:
        return sum(iv.char_range[1] - iv.char_range[0]
                   for iv in self.citable_intervals if iv.citable)

    @property
    def non_citable_char_count(self) -> int:
        return sum(iv.char_range[1] - iv.char_range[0]
                   for iv in self.citable_intervals if not iv.citable)

    def is_citable(self) -> bool:
        """decision 不构成 citable 资格：只有真实可引用区间才成立。"""
        return self.citable_char_count > 0

    def locator_payload(self) -> dict:
        return {
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_layout_id": self.page_layout_id,
            "outline_id": self.outline_id,
            "node_id": self.node_id,
            "source_boundary_id": self.source_boundary_id,
            "start_page": self.start_page,
            "start_line": self.start_line,
            "end_page": self.end_page,
            "end_line": self.end_line,
        }

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "span_locator": self.span_locator,
            "schema_version": self.schema_version,
            "span_builder_version": self.span_builder_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "ts4_source_span_locator": self.ts4_source_span_locator,
            "ts4_source_disposition_id": self.ts4_source_disposition_id,
            "component_ids": list(self.component_ids),
            "evidence_char_ranges": [list(x) for x in self.evidence_char_ranges],
            "boundary_factors": [f.to_dict() for f in self.boundary_factors],
            "confidence": self.confidence,
            "confidence_min": self.confidence_min,
            "citable_intervals": [iv.to_dict() for iv in self.citable_intervals],
            "content_fingerprint": self.content_fingerprint,
        })
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "FinalOutlineSpan"
        payload["span_id"] = self.span_id
        payload["normalized_text"] = self.normalized_text
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "FinalOutlineSpan":
        t = "FinalOutlineSpan"
        d = _reject_unknown(d, {
            "schema_type", "span_locator", "span_id", "schema_version",
            "span_builder_version", "document_id", "document_version",
            "evidence_set_version", "page_layout_id", "outline_id",
            "upstream_dependency_fingerprint", "node_id", "source_boundary_id",
            "ts4_source_span_locator", "ts4_source_disposition_id", "start_page",
            "start_line", "end_page", "end_line", "component_ids",
            "evidence_char_ranges", "boundary_factors", "confidence",
            "confidence_min", "normalized_text", "citable_intervals",
            "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("FinalOutlineSpan",))

        def _triple(v: Any) -> tuple[str, int, int]:
            if not isinstance(v, list) or len(v) != 3:
                _err(t, "evidence_char_ranges 元素必须为 [evidence_block_id, start, end]")
            if not isinstance(v[0], str) or v[0] == "":
                _err(t, "evidence_char_ranges 元素的 evidence_block_id 必须为非空字符串")
            for x in v[1:]:
                if not isinstance(x, int) or isinstance(x, bool):
                    _err(t, "evidence_char_ranges 元素的区间必须为 int")
            return (v[0], v[1], v[2])

        return cls(
            span_locator=_need_str(d, "span_locator", t),
            span_id=_need_str(d, "span_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            span_builder_version=_need_str(d, "span_builder_version", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            outline_id=_need_str(d, "outline_id", t),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            source_boundary_id=_need_str(d, "source_boundary_id", t),
            ts4_source_span_locator=_need_str(d, "ts4_source_span_locator", t,
                                              none_ok=True),
            ts4_source_disposition_id=_need_str(d, "ts4_source_disposition_id", t,
                                                none_ok=True),
            start_page=_need_int(d, "start_page", t, lo=1),
            start_line=_need_int(d, "start_line", t, lo=0),
            end_page=_need_int(d, "end_page", t, lo=1),
            end_line=_need_int(d, "end_line", t, lo=0),
            component_ids=_need_str_tuple(d, "component_ids", t),
            evidence_char_ranges=_need_children(
                d, "evidence_char_ranges", t, _triple),
            boundary_factors=_need_children(d, "boundary_factors", t,
                                            BoundaryFactor.from_dict),
            confidence=_need_num(d, "confidence", t, lo=0.0, hi=1.0),
            confidence_min=_need_num(d, "confidence_min", t, lo=0.0, hi=1.0),
            normalized_text=_need_str(d, "normalized_text", t),
            citable_intervals=_need_children(d, "citable_intervals", t,
                                             CitableInterval.from_dict),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "FinalOutlineSpan":
        """`sbf-1`：final span 身份由本类派生，**不**复用 TS4 的 `os-4` / `sb-7`。"""
        return _assemble(
            cls, fields,
            locator_attr="span_locator", locator_kind="fos",
            locator_method="locator_payload",
            fingerprint_attrs=(("content_fingerprint", "content_payload"),),
            identity_attr="span_id", identity_kind="fos",
            identity_method="identity_payload")


# ---------------------------------------------------------------------------
# 7. `TableRangeDecision`（`trd-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableRangeDecision:
    """§19.7.1：一条 TS4 表 provisional range 的**唯一**裁决。

    它只**单向**引用已完成的 table / final span：`table_id` / `final_span_id`
    是引用，不是被引用；table 与 final span 都**不得**携带 decision 的 ID 或指纹。
    """

    decision_locator: str
    decision_id: str
    schema_version: str
    upstream_dependency_fingerprint: str
    disposition_id: str
    disposition_locator: str
    range_kind: str
    decision: str
    target_kind: str
    table_id: str | None
    final_span_id: str | None
    evidence_char_range: tuple[int, int]
    source_ref_ids: tuple[str, ...]
    rationale_code: str

    SCHEMA_CONSTANT = "TABLE_RANGE_DECISION_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "TableRangeDecision"
        for name in ("decision_locator", "decision_id", "disposition_id",
                     "disposition_locator", "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        if self.range_kind not in TABLE_RANGE_KINDS:
            _err(t, f"range_kind 必须属于 {TABLE_RANGE_KINDS}，"
                    f"得到 {self.range_kind!r}")
        if self.decision not in TABLE_DECISION_KINDS:
            _err(t, f"decision 必须属于 {TABLE_DECISION_KINDS}，"
                    f"得到 {self.decision!r}")
        expected_target = TABLE_DECISION_TARGETS[self.decision]
        if self.target_kind != expected_target:
            _err(t, f"decision={self.decision!r} 的 target 必须为 "
                    f"{expected_target!r}，得到 {self.target_kind!r}")
        if self.decision in DECISION_TARGET_OF["table"]:
            if not isinstance(self.table_id, str) or self.table_id == "":
                _err(t, "被表格吸收的 decision 必须给出 table_id")
            if self.final_span_id is not None:
                _err(t, "被表格吸收的 decision 不得携带 final_span_id")
        elif self.decision in DECISION_TARGET_OF["final_span"]:
            if not isinstance(self.final_span_id, str) or self.final_span_id == "":
                _err(t, "保留为正文的 decision 必须给出 final_span_id")
            if self.table_id is not None:
                _err(t, "保留为正文的 decision 不得携带 table_id")
        else:
            if self.table_id is not None or self.final_span_id is not None:
                _err(t, "unsupported/unresolved decision 不得绑定任何目标对象")
        if self.rationale_code not in DECISION_RATIONALE_CODES:
            _err(t, f"rationale_code 必须属于 {DECISION_RATIONALE_CODES}，"
                    f"得到 {self.rationale_code!r}")
        # 一条 provisional range **可以**是空区间（零长度也要如实登记），
        # 因此这里只要求 0 <= start <= end，不用 `_need_ordered_pairs`（它要求严格正长）。
        if not isinstance(self.evidence_char_range, tuple) or \
                len(self.evidence_char_range) != 2:
            _err(t, "evidence_char_range 必须为二元组")
        for x in self.evidence_char_range:
            _need_int({"v": x}, "v", t, lo=0)
        if self.evidence_char_range[1] < self.evidence_char_range[0]:
            _err(t, "evidence_char_range 必须满足 0 <= start <= end")
        if len(set(self.source_ref_ids)) != len(self.source_ref_ids):
            _err(t, "source_ref_ids 不得重复")
        if tuple(sorted(self.source_ref_ids)) != self.source_ref_ids:
            _err(t, "source_ref_ids 必须按字典序")
        if self.decision == "absorbed_as_table_body" and \
                self.evidence_char_range[1] > self.evidence_char_range[0] \
                and not self.source_ref_ids:
            _err(t, "absorbed_as_table_body 的非空范围必须给出真实 cell source ref")
        expected_loc = locator("trd", self.locator_payload())
        if self.decision_locator != expected_loc:
            _err(t, "decision_locator 与派生定位不一致："
                    f"{self.decision_locator!r} != {expected_loc!r}")
        expected_id = identity("trd", self.identity_payload())
        if self.decision_id != expected_id:
            _err(t, "decision_id 与派生身份不一致："
                    f"{self.decision_id!r} != {expected_id!r}")

    def locator_payload(self) -> dict:
        return {"disposition_locator": self.disposition_locator,
                "range_kind": self.range_kind}

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "decision_locator": self.decision_locator,
            "schema_version": self.schema_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "disposition_id": self.disposition_id,
            "decision": self.decision,
            "target_kind": self.target_kind,
            "table_id": self.table_id,
            "final_span_id": self.final_span_id,
            "evidence_char_range": list(self.evidence_char_range),
            "source_ref_ids": list(self.source_ref_ids),
            "rationale_code": self.rationale_code,
        })
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "TableRangeDecision"
        payload["decision_id"] = self.decision_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableRangeDecision":
        t = "TableRangeDecision"
        d = _reject_unknown(d, {
            "schema_type", "decision_locator", "decision_id", "schema_version",
            "upstream_dependency_fingerprint", "disposition_id",
            "disposition_locator", "range_kind", "decision", "target_kind",
            "table_id", "final_span_id", "evidence_char_range", "source_ref_ids",
            "rationale_code"}, t)
        _need_enum(d, "schema_type", t, ("TableRangeDecision",))
        return cls(
            decision_locator=_need_str(d, "decision_locator", t),
            decision_id=_need_str(d, "decision_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            disposition_id=_need_str(d, "disposition_id", t),
            disposition_locator=_need_str(d, "disposition_locator", t),
            range_kind=_need_enum(d, "range_kind", t, TABLE_RANGE_KINDS),
            decision=_need_enum(d, "decision", t, TABLE_DECISION_KINDS),
            target_kind=_need_enum(d, "target_kind", t, ("table", "final_span",
                                                         "none")),
            table_id=_need_str(d, "table_id", t, none_ok=True),
            final_span_id=_need_str(d, "final_span_id", t, none_ok=True),
            evidence_char_range=_need_int_pair(d, "evidence_char_range", t),
            source_ref_ids=_need_str_tuple(d, "source_ref_ids", t),
            rationale_code=_need_enum(d, "rationale_code", t,
                                      DECISION_RATIONALE_CODES),
        )

    @classmethod
    def create(cls, **fields: Any) -> "TableRangeDecision":
        return _assemble(cls, fields,
                         locator_attr="decision_locator", locator_kind="trd",
                         locator_method="locator_payload",
                         identity_attr="decision_id", identity_kind="trd",
                         identity_method="identity_payload")


# ---------------------------------------------------------------------------
# 8. `TableRelation`（`trl-1` / `trb-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableRelation:
    """§19.8.1：final snapshot overlay 上的 typed 关系。

    它**不**回写 `DocumentOutline` / `ReferenceEdge`，**不**回写端点身份。
    端点必须是**真实的、对象级解析过的** typed endpoint；通用
    `source_id/target_id: str`、仅凭字符串存在、调用方自报 endpoint kind 或跨文档
    拼接一律拒绝（由 `RELATION_ENDPOINT_TYPES` 的严格类型对强制）。
    """

    relation_locator: str
    relation_id: str
    schema_version: str
    relation_builder_version: str
    upstream_dependency_fingerprint: str
    relation_kind: str
    source_endpoint: Any
    target_endpoint: Any
    relation_proof_ids: tuple[str, ...]
    content_fingerprint: str

    SCHEMA_CONSTANT = "TABLE_RELATION_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "TableRelation"
        for name in ("relation_locator", "relation_id",
                     "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _check_registered_constant("TABLE_RELATION_BUILDER_VERSION")
        expected_builder = V.VERSION_CONSTANTS["TABLE_RELATION_BUILDER_VERSION"]
        if self.relation_builder_version != expected_builder:
            _err(t, f"relation_builder_version 必须为 {expected_builder!r}，"
                    f"得到 {self.relation_builder_version!r}")
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        if self.relation_kind not in TABLE_RELATION_KINDS:
            _err(t, f"relation_kind 必须属于 {TABLE_RELATION_KINDS}，"
                    f"得到 {self.relation_kind!r}")
        if self.relation_kind in FUTURE_ONLY_RELATION_KINDS:
            _err(t, f"{self.relation_kind!r} 只保留 schema 扩展位；"
                    f"TS5 构造任何 resolved 关系均拒绝")
        expected_src, expected_tgt = RELATION_ENDPOINT_TYPES[self.relation_kind]
        for role, endpoint, want in (("source", self.source_endpoint, expected_src),
                                     ("target", self.target_endpoint,
                                      expected_tgt)):
            if not isinstance(endpoint, ENDPOINT_REF_TYPES):
                _err(t, f"{role}_endpoint 必须属于 "
                        f"{tuple(x.__name__ for x in ENDPOINT_REF_TYPES)}，"
                        f"得到 {type(endpoint).__name__}")
            if type(endpoint).__name__ not in want:
                _err(t, f"relation_kind={self.relation_kind!r} 的 {role} 端点必须为 "
                        f"{want}，得到 {type(endpoint).__name__}")
        if not isinstance(self.relation_proof_ids, tuple) or \
                not self.relation_proof_ids:
            _err(t, "每条 resolved relation 必须携带关系证明引用")
        if len(set(self.relation_proof_ids)) != len(self.relation_proof_ids):
            _err(t, "relation_proof_ids 不得重复")
        if tuple(sorted(self.relation_proof_ids)) != self.relation_proof_ids:
            _err(t, "relation_proof_ids 必须按字典序")
        if self.relation_kind == "continued_by":
            self._check_continuation(t)
        if self.relation_kind == "introduces" and \
                isinstance(self.source_endpoint, FinalSpanEndpointRef) and \
                isinstance(self.target_endpoint, TableEndpointRef):
            if self.source_endpoint.document_id != self.target_endpoint.document_id:
                _err(t, "introduces 两端必须同 document")
        if self.relation_kind == "footnote_of":
            role = getattr(self.source_endpoint, "role", None)
            if isinstance(self.source_endpoint, ComponentEndpointRef) and \
                    role != "note":
                _err(t, "footnote_of 的 component 端点 role 必须为 'note'")
        if self.relation_kind == "caption_of":
            if self.source_endpoint.role != "caption":
                _err(t, "caption_of 的 component 端点 role 必须为 'caption'")
        if self.relation_kind == "unit_of":
            if self.source_endpoint.role != "unit":
                _err(t, "unit_of 的 component 端点 role 必须为 'unit'")
        expected_loc = locator("trl", self.locator_payload())
        if self.relation_locator != expected_loc:
            _err(t, "relation_locator 与派生定位不一致："
                    f"{self.relation_locator!r} != {expected_loc!r}")
        expected_id = identity("trl", self.identity_payload())
        if self.relation_id != expected_id:
            _err(t, "relation_id 与派生身份不一致："
                    f"{self.relation_id!r} != {expected_id!r}")
        _check_fingerprint(t, "content_fingerprint", self.content_fingerprint,
                           self.content_payload())

    def _check_continuation(self, t: str) -> None:
        src, tgt = self.source_endpoint, self.target_endpoint
        if not isinstance(src, TableEndpointRef) or \
                not isinstance(tgt, TableEndpointRef):
            _err(t, "continued_by 两端必须都是 TableEndpointRef")
        if src.table_locator == tgt.table_locator:
            _err(t, "continued_by 的端点不得是自身")
        if src.document_id != tgt.document_id:
            _err(t, "continued_by 必须 same document（TS5 不跨文档合成逻辑大表）")
        if src.page_layout_id != tgt.page_layout_id or \
                src.outline_id != tgt.outline_id:
            _err(t, "continued_by 必须同 layout/outline 版本")

    def locator_payload(self) -> dict:
        return {
            "relation_kind": self.relation_kind,
            "source": self.source_endpoint.to_dict(),
            "target": self.target_endpoint.to_dict(),
        }

    def content_payload(self) -> dict:
        return {
            "relation_proof_ids": list(self.relation_proof_ids),
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
        }

    def identity_payload(self) -> dict:
        return {
            "relation_locator": self.relation_locator,
            "schema_version": self.schema_version,
            "relation_builder_version": self.relation_builder_version,
            "relation_kind": self.relation_kind,
            "source": self.source_endpoint.to_dict(),
            "target": self.target_endpoint.to_dict(),
            "relation_proof_ids": list(self.relation_proof_ids),
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "content_fingerprint": self.content_fingerprint,
        }

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "TableRelation"
        payload["relation_id"] = self.relation_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableRelation":
        t = "TableRelation"
        d = _reject_unknown(d, {
            "schema_type", "relation_locator", "relation_id", "schema_version",
            "relation_builder_version", "upstream_dependency_fingerprint",
            "relation_kind", "source", "target", "relation_proof_ids",
            "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("TableRelation",))
        return cls(
            relation_locator=_need_str(d, "relation_locator", t),
            relation_id=_need_str(d, "relation_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            relation_builder_version=_need_str(d, "relation_builder_version", t),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            relation_kind=_need_enum(d, "relation_kind", t, TABLE_RELATION_KINDS),
            source_endpoint=_decode_endpoint(d, "source", t),
            target_endpoint=_decode_endpoint(d, "target", t),
            relation_proof_ids=_need_str_tuple(d, "relation_proof_ids", t),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "TableRelation":
        return _assemble(
            cls, fields,
            locator_attr="relation_locator", locator_kind="trl",
            locator_method="locator_payload",
            fingerprint_attrs=(("content_fingerprint", "content_payload"),),
            identity_attr="relation_id", identity_kind="trl",
            identity_method="identity_payload")


def _decode_endpoint(d: dict, key: str, typename: str) -> Any:
    """严格 discriminated union 解码：以 `endpoint_kind` 选分支，且拒绝跨分支字段。"""
    raw = d.get(key)
    if not isinstance(raw, dict):
        _err(typename, f"{key} 必须为端点对象，得到 {type(raw).__name__}")
    kind = raw.get("endpoint_kind")
    decoder = ENDPOINT_REF_BY_KIND.get(kind)
    if decoder is None:
        _err(typename, f"{key}.endpoint_kind 必须属于 "
                       f"{tuple(ENDPOINT_REF_BY_KIND)}，得到 {kind!r}")
    try:
        return decoder.from_dict(raw)
    except SchemaValidationError as e:
        _err(typename, f"{key} 解码失败：{e}")


# ---------------------------------------------------------------------------
# 9. `FinalComponentBinding`（`fcb-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinalComponentBinding:
    """§19.9.1：一个 TS4 component 的**唯一**最终去路。

    `component_id` 集合必须与 verified TS4 snapshot 的**全部 components 精确等集**；
    一个 component 恰好落入 `final_span` / `table_object` / `pending` / `rejected`
    之一。`rejected` 是带完整 component identity、区间与 reason 的**终端账本项**，
    不是删除来源。
    """

    binding_locator: str
    binding_id: str
    schema_version: str
    upstream_dependency_fingerprint: str
    component_id: str
    component_locator: str
    component_landing: str
    admission: str
    admission_reason: str
    table_id: str | None
    final_span_id: str | None
    cell_source_ref_ids: tuple[str, ...]
    disposition_id: str | None
    evidence_char_range: tuple[int, int]
    gap_codes: tuple[str, ...]

    SCHEMA_CONSTANT = "FINAL_COMPONENT_BINDING_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "FinalComponentBinding"
        for name in ("binding_locator", "binding_id", "component_id",
                     "component_locator", "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        if self.component_landing not in COMPONENT_LANDINGS:
            _err(t, f"component_landing 必须属于 {COMPONENT_LANDINGS}，"
                    f"得到 {self.component_landing!r}")
        if self.admission not in BINDING_ADMISSIONS:
            _err(t, f"admission 必须属于 {BINDING_ADMISSIONS}，"
                    f"得到 {self.admission!r}")
        allowed = COMPONENT_LANDING_ADMISSIONS[self.component_landing]
        if self.admission not in allowed:
            _err(t, f"landing={self.component_landing!r} 只允许终态 {allowed}，"
                    f"得到 {self.admission!r}")
        if self.admission_reason not in BINDING_REASONS:
            _err(t, f"admission_reason 必须属于 {BINDING_REASONS}，"
                    f"得到 {self.admission_reason!r}")
        self._check_admission_truth_table(t)
        for i, code in enumerate(self.gap_codes):
            if code not in TABLE_GAP_KINDS:
                _err(t, f"gap_codes[{i}] 必须属于 {TABLE_GAP_KINDS}，得到 {code!r}")
        if len(set(self.gap_codes)) != len(self.gap_codes):
            _err(t, "gap_codes 不得重复")
        if tuple(sorted(self.gap_codes)) != self.gap_codes:
            _err(t, "gap_codes 必须按字典序")
        ids = list(self.cell_source_ref_ids)
        if len(set(ids)) != len(ids):
            _err(t, "cell_source_ref_ids 不得重复")
        if ids != sorted(ids):
            _err(t, "cell_source_ref_ids 必须按字典序")
        if not isinstance(self.evidence_char_range, tuple) or \
                len(self.evidence_char_range) != 2:
            _err(t, "evidence_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.evidence_char_range)]}, "_", t)
        if self.evidence_char_range[1] <= self.evidence_char_range[0]:
            _err(t, "evidence_char_range 必须为正长度")
        expected_loc = locator("fcb", self.locator_payload())
        if self.binding_locator != expected_loc:
            _err(t, "binding_locator 与派生定位不一致："
                    f"{self.binding_locator!r} != {expected_loc!r}")
        expected_id = identity("fcb", self.identity_payload())
        if self.binding_id != expected_id:
            _err(t, "binding_id 与派生身份不一致："
                    f"{self.binding_id!r} != {expected_id!r}")

    def _check_admission_truth_table(self, t: str) -> None:
        if self.admission == "table_object":
            if self.admission_reason not in TABLE_ADMISSION_REASONS:
                _err(t, f"table_object 的理由必须属于 {TABLE_ADMISSION_REASONS}，"
                        f"得到 {self.admission_reason!r}")
            if not isinstance(self.table_id, str) or self.table_id == "":
                _err(t, "table_object 终态必须给出 table_id")
            if self.final_span_id is not None:
                _err(t, "table_object 终态不得携带 final_span_id")
            if not self.cell_source_ref_ids:
                _err(t, "table_object 终态必须给出真实 cell source refs")
            if self.component_landing in ("alignment_offset_unverifiable",
                                          "alignment_residue_unmapped"):
                _err(t, "无可核验 layout fragment 的 landing 不得进入 table_object")
        elif self.admission == "final_span":
            if self.admission_reason not in FINAL_SPAN_ADMISSION_REASONS:
                _err(t, f"final_span 的理由必须属于 {FINAL_SPAN_ADMISSION_REASONS}，"
                        f"得到 {self.admission_reason!r}")
            if not isinstance(self.final_span_id, str) or self.final_span_id == "":
                _err(t, "final_span 终态必须给出 final_span_id")
            if self.table_id is not None:
                _err(t, "final_span 终态不得携带 table_id")
            if self.cell_source_ref_ids:
                _err(t, "final_span 终态不得同时声明 cell source refs"
                        "（一个 component 只能投一处）")
        elif self.admission == "pending":
            if self.admission_reason not in PENDING_ADMISSION_REASONS:
                _err(t, f"pending 的理由必须属于 {PENDING_ADMISSION_REASONS}，"
                        f"得到 {self.admission_reason!r}")
            allowed = COMPONENT_LANDING_PENDING_REASONS[self.component_landing]
            if self.admission_reason not in allowed:
                _err(t, f"landing={self.component_landing!r} 的 pending 理由必须属于 "
                        f"{allowed}，得到 {self.admission_reason!r}")
            if self.table_id is not None or self.final_span_id is not None:
                _err(t, "pending 不得绑定任何目标对象")
            if self.cell_source_ref_ids:
                _err(t, "pending 不得声明已消费的 cell source refs")
            if not self.gap_codes:
                _err(t, "pending 必须留下至少一个结构缺口码（诚实未决）")
        else:
            if self.admission_reason not in REJECTED_ADMISSION_REASONS:
                _err(t, f"rejected 的理由必须属于 {REJECTED_ADMISSION_REASONS}，"
                        f"得到 {self.admission_reason!r}")
            allowed = COMPONENT_LANDING_REJECTED_REASONS.get(
                self.component_landing, ())
            if self.admission_reason not in allowed:
                _err(t, f"landing={self.component_landing!r} 的 rejected 理由必须属于 "
                        f"{allowed}，得到 {self.admission_reason!r}")
            if self.table_id is not None or self.final_span_id is not None:
                _err(t, "rejected 不得绑定任何目标对象")
            if self.cell_source_ref_ids:
                _err(t, "rejected 不得声明已消费的 cell source refs")

    @property
    def blocks_document_capability(self) -> bool:
        """该终态是否必须阻断本文档的 final capability 与 TS5 关闭。"""
        return self.admission_reason in DOCUMENT_BLOCKING_REASONS

    def locator_payload(self) -> dict:
        return {"component_locator": self.component_locator}

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "binding_locator": self.binding_locator,
            "schema_version": self.schema_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "component_id": self.component_id,
            "component_landing": self.component_landing,
            "admission": self.admission,
            "admission_reason": self.admission_reason,
            "table_id": self.table_id,
            "final_span_id": self.final_span_id,
            "cell_source_ref_ids": list(self.cell_source_ref_ids),
            "disposition_id": self.disposition_id,
            "evidence_char_range": list(self.evidence_char_range),
            "gap_codes": list(self.gap_codes),
        })
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "FinalComponentBinding"
        payload["binding_id"] = self.binding_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "FinalComponentBinding":
        t = "FinalComponentBinding"
        d = _reject_unknown(d, {
            "schema_type", "binding_locator", "binding_id", "schema_version",
            "upstream_dependency_fingerprint", "component_id",
            "component_locator", "component_landing", "admission",
            "admission_reason", "table_id", "final_span_id",
            "cell_source_ref_ids", "disposition_id", "evidence_char_range",
            "gap_codes"}, t)
        _need_enum(d, "schema_type", t, ("FinalComponentBinding",))
        return cls(
            binding_locator=_need_str(d, "binding_locator", t),
            binding_id=_need_str(d, "binding_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            component_id=_need_str(d, "component_id", t),
            component_locator=_need_str(d, "component_locator", t),
            component_landing=_need_enum(d, "component_landing", t,
                                         COMPONENT_LANDINGS),
            admission=_need_enum(d, "admission", t, BINDING_ADMISSIONS),
            admission_reason=_need_enum(d, "admission_reason", t, BINDING_REASONS),
            table_id=_need_str(d, "table_id", t, none_ok=True),
            final_span_id=_need_str(d, "final_span_id", t, none_ok=True),
            cell_source_ref_ids=_need_str_tuple(d, "cell_source_ref_ids", t),
            disposition_id=_need_str(d, "disposition_id", t, none_ok=True),
            evidence_char_range=_need_int_pair(d, "evidence_char_range", t,
                                               strict=True),
            gap_codes=_need_str_tuple(d, "gap_codes", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "FinalComponentBinding":
        return _assemble(cls, fields,
                         locator_attr="binding_locator", locator_kind="fcb",
                         locator_method="locator_payload",
                         identity_attr="binding_id", identity_kind="fcb",
                         identity_method="identity_payload")


# ---------------------------------------------------------------------------
# 10. `TableCitableCoverage`（`tcc-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableCitableCoverage:
    """§19.9.2：**逐 cell source ref** 的可引用覆盖。

    不得以整表 bool 取代：table structure 可以 complete，但
    `all_cells_citable=False`。下游 citation 必须落到单一 component/Evidence range，
    不能只引用整张表或页码。
    """

    coverage_locator: str
    coverage_id: str
    schema_version: str
    upstream_dependency_fingerprint: str
    table_id: str
    table_locator: str
    cell_refs: tuple[str, ...]
    intervals: tuple[CitableInterval, ...]
    evidence_char_range: tuple[int, int]
    non_citable_reasons: tuple[str, ...]

    SCHEMA_CONSTANT = "TABLE_CITABLE_COVERAGE_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "TableCitableCoverage"
        for name in ("coverage_locator", "coverage_id", "table_id",
                     "table_locator", "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        if not isinstance(self.cell_refs, tuple) or not self.cell_refs:
            _err(t, "coverage 必须逐 cell source ref 记录（不得只给整表 bool）")
        if len(set(self.cell_refs)) != len(self.cell_refs):
            _err(t, "cell_refs 不得重复")
        if tuple(sorted(self.cell_refs)) != self.cell_refs:
            _err(t, "cell_refs 必须按字典序")
        for i, iv in enumerate(self.intervals):
            if not isinstance(iv, CitableInterval):
                _err(t, f"intervals[{i}] 必须为 CitableInterval")
            if iv.cell_ref is not None and iv.cell_ref not in set(self.cell_refs):
                _err(t, f"intervals[{i}].cell_ref 不在本文 coverage 的 cell_refs 中")
        keys = [iv.char_range for iv in self.intervals]
        if keys != sorted(keys):
            _err(t, "intervals 必须按 char_range 升序")
        for a, b in zip(keys, keys[1:]):
            if b[0] < a[1]:
                _err(t, f"intervals 之间不得重叠：{a} / {b}")
        for i, r in enumerate(self.non_citable_reasons):
            if r not in CITABLE_REASONS:
                _err(t, f"non_citable_reasons[{i}] 必须属于 {CITABLE_REASONS}")
        if self.non_citable_reasons != tuple(sorted(set(self.non_citable_reasons))):
            _err(t, "non_citable_reasons 必须去重且按字典序")
        if not isinstance(self.evidence_char_range, tuple) or \
                len(self.evidence_char_range) != 2:
            _err(t, "evidence_char_range 必须为二元组")
        for x in self.evidence_char_range:
            _need_int({"v": x}, "v", t, lo=0)
        if self.evidence_char_range[1] < self.evidence_char_range[0]:
            _err(t, "evidence_char_range 必须满足 0 <= start <= end")
        # 理由集必须与实测区间一致（不得自报一个与内容矛盾的理由集）。
        measured = tuple(sorted({iv.reason for iv in self.intervals
                                 if not iv.citable}))
        if measured != self.non_citable_reasons:
            _err(t, "non_citable_reasons 必须等于实测非可引用区间的理由集："
                    f"{measured} != {self.non_citable_reasons}")
        expected_loc = locator("tcc", self.locator_payload())
        if self.coverage_locator != expected_loc:
            _err(t, "coverage_locator 与派生定位不一致："
                    f"{self.coverage_locator!r} != {expected_loc!r}")
        expected_id = identity("tcc", self.identity_payload())
        if self.coverage_id != expected_id:
            _err(t, "coverage_id 与派生身份不一致："
                    f"{self.coverage_id!r} != {expected_id!r}")

    @property
    def all_cells_citable(self) -> bool:
        return all(iv.citable for iv in self.intervals)

    @property
    def citable_char_count(self) -> int:
        return sum(iv.char_range[1] - iv.char_range[0]
                   for iv in self.intervals if iv.citable)

    def locator_payload(self) -> dict:
        return {"table_locator": self.table_locator}

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "coverage_locator": self.coverage_locator,
            "schema_version": self.schema_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "table_id": self.table_id,
            "cell_refs": list(self.cell_refs),
            "intervals": [iv.to_dict() for iv in self.intervals],
            "evidence_char_range": list(self.evidence_char_range),
            "non_citable_reasons": list(self.non_citable_reasons),
        })
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "TableCitableCoverage"
        payload["coverage_id"] = self.coverage_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableCitableCoverage":
        t = "TableCitableCoverage"
        d = _reject_unknown(d, {
            "schema_type", "coverage_locator", "coverage_id", "schema_version",
            "upstream_dependency_fingerprint", "table_id", "table_locator",
            "cell_refs", "intervals", "evidence_char_range",
            "non_citable_reasons"}, t)
        _need_enum(d, "schema_type", t, ("TableCitableCoverage",))
        return cls(
            coverage_locator=_need_str(d, "coverage_locator", t),
            coverage_id=_need_str(d, "coverage_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            table_id=_need_str(d, "table_id", t),
            table_locator=_need_str(d, "table_locator", t),
            cell_refs=_need_str_tuple(d, "cell_refs", t),
            intervals=_need_children(d, "intervals", t, CitableInterval.from_dict),
            evidence_char_range=_need_int_pair(d, "evidence_char_range", t),
            non_citable_reasons=_need_str_tuple(d, "non_citable_reasons", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "TableCitableCoverage":
        return _assemble(cls, fields,
                         locator_attr="coverage_locator", locator_kind="tcc",
                         locator_method="locator_payload",
                         identity_attr="coverage_id", identity_kind="tcc",
                         identity_method="identity_payload")


# ---------------------------------------------------------------------------
# 11. `TableStructureGap`（`tsg-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableStructureGap:
    """§19.4.3：结构缺口的**独立记录**（不塞进 table 对象，避免回指）。"""

    gap_locator: str
    gap_id: str
    schema_version: str
    upstream_dependency_fingerprint: str
    entries: tuple[TableGapEntry, ...]
    blocking_entry_count: int

    SCHEMA_CONSTANT = "TABLE_STRUCTURE_GAP_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "TableStructureGap"
        for name in ("gap_locator", "gap_id", "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        if not isinstance(self.entries, tuple):
            _err(t, "entries 必须为元组")
        keys = []
        for i, e in enumerate(self.entries):
            if not isinstance(e, TableGapEntry):
                _err(t, f"entries[{i}] 必须为 TableGapEntry")
            keys.append((e.gap_kind, e.detail_code, e.page_number or 0,
                         e.table_id or "", e.component_id or "",
                         e.disposition_id or ""))
        if keys != sorted(keys):
            _err(t, "缺口条目必须按固定顺序（gap_kind, detail_code, page, ids）")
        if len(set(keys)) != len(keys):
            _err(t, "缺口条目不得重复")
        _need_int({"v": self.blocking_entry_count}, "v", t, lo=0)
        measured = sum(1 for e in self.entries if e.blocks_document_capability)
        if measured != self.blocking_entry_count:
            _err(t, "blocking_entry_count 必须等于实测阻断条目数："
                    f"{measured} != {self.blocking_entry_count}")
        expected_loc = locator("tsg", self.locator_payload())
        if self.gap_locator != expected_loc:
            _err(t, "gap_locator 与派生定位不一致："
                    f"{self.gap_locator!r} != {expected_loc!r}")
        expected_id = identity("tsg", self.identity_payload())
        if self.gap_id != expected_id:
            _err(t, "gap_id 与派生身份不一致："
                    f"{self.gap_id!r} != {expected_id!r}")

    @property
    def blocks_document_capability(self) -> bool:
        return self.blocking_entry_count > 0

    def locator_payload(self) -> dict:
        return {"scope": "document"}

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "gap_locator": self.gap_locator,
            "schema_version": self.schema_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "entries": [e.to_dict() for e in self.entries],
            "blocking_entry_count": self.blocking_entry_count,
        })
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "TableStructureGap"
        payload["gap_id"] = self.gap_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TableStructureGap":
        t = "TableStructureGap"
        d = _reject_unknown(d, {
            "schema_type", "scope", "gap_locator", "gap_id", "schema_version",
            "upstream_dependency_fingerprint", "entries",
            "blocking_entry_count"}, t)
        _need_enum(d, "schema_type", t, ("TableStructureGap",))
        # `scope` 是 locator payload 的组成项（`to_dict` 会写出），round-trip 必须接受。
        _need_enum(d, "scope", t, ("document",))
        return cls(
            gap_locator=_need_str(d, "gap_locator", t),
            gap_id=_need_str(d, "gap_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            entries=_need_children(d, "entries", t, TableGapEntry.from_dict),
            blocking_entry_count=_need_int(d, "blocking_entry_count", t, lo=0),
        )

    @classmethod
    def create(cls, **fields: Any) -> "TableStructureGap":
        return _assemble(cls, fields,
                         locator_attr="gap_locator", locator_kind="tsg",
                         locator_method="locator_payload",
                         identity_attr="gap_id", identity_kind="tsg",
                         identity_method="identity_payload")


# ---------------------------------------------------------------------------
# 12. `FinalMaterialConservation`（`fmc-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinalMaterialConservation:
    """§19.9.3：四层守恒**分别**验证，不得跨层相加。

    任何差额、重复、越界或"为凑平而自动修正"都是 P1：因此本对象**没有**任何
    "自动修正后仍然 balanced"的构造路径；不平衡必须原样记录并留下问题码。
    """

    conservation_locator: str
    conservation_id: str
    schema_version: str
    upstream_dependency_fingerprint: str
    layers: tuple[ConservationLayer, ...]
    problems: tuple[str, ...]

    SCHEMA_CONSTANT = "FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "FinalMaterialConservation"
        for name in ("conservation_locator", "conservation_id",
                     "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        got = tuple(x.layer_kind for x in self.layers)
        if got != CONSERVATION_LAYER_KINDS:
            _err(t, f"四层守恒必须恰为 {CONSERVATION_LAYER_KINDS}（固定顺序），"
                    f"得到 {got}")
        for i, layer in enumerate(self.layers):
            if not isinstance(layer, ConservationLayer):
                _err(t, f"layers[{i}] 必须为 ConservationLayer")
        if not isinstance(self.problems, tuple):
            _err(t, "problems 必须为元组")
        for p in self.problems:
            if not isinstance(p, str) or p == "":
                _err(t, "problems 必须为非空字符串元组")
        # 文档级 `problems` 必须是各层问题码的**并集**：层内记了问题、文档级却不认，
        # 会让"文档级 problems 为空"变成一个可以说谎的字段。这里把它变成构造期断言。
        layer_problems = tuple(sorted({p for x in self.layers for p in x.problems}))
        missing = tuple(p for p in layer_problems if p not in self.problems)
        if missing:
            _err(t, f"层内问题码必须并入文档级 problems，缺少 {missing}")
        unbalanced = [x.layer_kind for x in self.layers if not x.balanced]
        if unbalanced and not self.problems:
            _err(t, f"存在不平衡层 {unbalanced}，必须留下问题码（不得静默）")
        expected_loc = locator("fmc", self.locator_payload())
        if self.conservation_locator != expected_loc:
            _err(t, "conservation_locator 与派生定位不一致："
                    f"{self.conservation_locator!r} != {expected_loc!r}")
        expected_id = identity("fmc", self.identity_payload())
        if self.conservation_id != expected_id:
            _err(t, "conservation_id 与派生身份不一致："
                    f"{self.conservation_id!r} != {expected_id!r}")

    @property
    def balanced(self) -> bool:
        """文档级守恒资格（§19.9.3）。**不是**"各层算术凑平"。

        它由 `conservation_eligible` 唯一判定：文档级 `problems` 为空，且四层各自
        满足"算术守恒 + 本层 problems 为空 + 必须为零的分项为 0"。因此
        "`problems` 非空却 `balanced=true`"在读取侧同样不可能出现——凡是反序列化
        出来的对象都重新走这一条，而不是相信载荷里写的那个布尔值。
        """
        return conservation_eligible(self)

    def layer(self, layer_kind: str) -> ConservationLayer:
        for x in self.layers:
            if x.layer_kind == layer_kind:
                return x
        raise SchemaValidationError(f"未登记的守恒层 {layer_kind!r}")

    def locator_payload(self) -> dict:
        return {"scope": "document"}

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "conservation_locator": self.conservation_locator,
            "schema_version": self.schema_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "layers": [x.to_dict() for x in self.layers],
            "problems": list(self.problems),
        })
        return payload

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "FinalMaterialConservation"
        payload["conservation_id"] = self.conservation_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "FinalMaterialConservation":
        t = "FinalMaterialConservation"
        d = _reject_unknown(d, {
            "schema_type", "scope", "conservation_locator", "conservation_id",
            "schema_version", "upstream_dependency_fingerprint", "layers",
            "problems"}, t)
        _need_enum(d, "schema_type", t, ("FinalMaterialConservation",))
        # 同 `TableStructureGap`：`scope` 属于 locator payload。
        _need_enum(d, "scope", t, ("document",))
        return cls(
            conservation_locator=_need_str(d, "conservation_locator", t),
            conservation_id=_need_str(d, "conservation_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            layers=_need_children(d, "layers", t, ConservationLayer.from_dict),
            problems=_need_str_tuple(d, "problems", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "FinalMaterialConservation":
        return _assemble(cls, fields,
                         locator_attr="conservation_locator", locator_kind="fmc",
                         locator_method="locator_payload",
                         identity_attr="conservation_id", identity_kind="fmc",
                         identity_method="identity_payload")


# ---------------------------------------------------------------------------
# 13. `FinalMaterialStructureSnapshot`（`fms-1`，唯一终端聚合节点）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinalMaterialStructureSnapshot:
    """§19.4.3：唯一终端聚合节点。

    `content_fingerprint` 最后由
    `upstream_dependency_fingerprint + canonical(sorted member IDs/fingerprints)
    + final versions` 派生。snapshot identity **不得**反向进入任何 member identity
    或上游依赖束；任何 Table→Snapshot→Table 循环都由构造期与
    `assert_identity_dag_acyclic()` 拒绝。
    """

    snapshot_locator: str
    snapshot_id: str
    schema_version: str
    builder_version: str
    final_span_schema_version: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    verified_span_snapshot_id: str
    upstream_dependency_fingerprint: str
    tables: tuple[TableObjectV4, ...]
    final_spans: tuple[FinalOutlineSpan, ...]
    decisions: tuple[TableRangeDecision, ...]
    relations: tuple[TableRelation, ...]
    bindings: tuple[FinalComponentBinding, ...]
    coverages: tuple[TableCitableCoverage, ...]
    gaps: TableStructureGap
    conservation: FinalMaterialConservation
    synopses: tuple[FinalNavigationSynopsis, ...]
    component_count: int
    table_count: int
    final_span_count: int
    blocking_gap_count: int
    content_fingerprint: str

    SCHEMA_CONSTANT = "FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "FinalMaterialStructureSnapshot"
        for name in ("snapshot_locator", "snapshot_id", "document_id",
                     "document_version", "evidence_set_version", "page_layout_id",
                     "outline_id", "verified_span_snapshot_id",
                     "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        for field, constant in (("builder_version", "FINAL_MATERIAL_BUILDER_VERSION"),
                                ("final_span_schema_version",
                                 "FINAL_SPAN_SCHEMA_VERSION")):
            _check_registered_constant(constant)
            expected = V.VERSION_CONSTANTS[constant]
            if getattr(self, field) != expected:
                _err(t, f"{field} 必须为当前版本 {expected!r}，"
                        f"得到 {getattr(self, field)!r}")
        if not _is_sha256(self.upstream_dependency_fingerprint):
            _err(t, "upstream_dependency_fingerprint 必须为 sha256")
        self._check_member_identity(t)
        for name, seq, kind in (("tables", self.tables, TableObjectV4),
                                ("final_spans", self.final_spans,
                                 FinalOutlineSpan),
                                ("decisions", self.decisions,
                                 TableRangeDecision),
                                ("relations", self.relations, TableRelation),
                                ("bindings", self.bindings,
                                 FinalComponentBinding),
                                ("coverages", self.coverages,
                                 TableCitableCoverage)):
            if not isinstance(seq, tuple):
                _err(t, f"{name} 必须为元组")
            for i, x in enumerate(seq):
                if not isinstance(x, kind):
                    _err(t, f"{name}[{i}] 必须为 {kind.__name__}")
        for name in ("tables", "final_spans", "decisions", "relations",
                     "bindings", "coverages"):
            keys = [_member_sort_key(x) for x in getattr(self, name)]
            if keys != sorted(keys):
                _err(t, f"{name} 必须按固定顺序排列")
            if len(set(keys)) != len(keys):
                _err(t, f"{name} 不得重复")
        if not isinstance(self.gaps, TableStructureGap):
            _err(t, "gaps 必须为 TableStructureGap")
        if not isinstance(self.conservation, FinalMaterialConservation):
            _err(t, "conservation 必须为 FinalMaterialConservation")
        if not isinstance(self.synopses, tuple):
            _err(t, "synopses 必须为元组")
        for i, s in enumerate(self.synopses):
            # 类型本身即版本轴断言：TS4 `NavigationSynopsis`（`nss-2`/`ns-2`）
            # 在这里被拒，无法"混装"进 final snapshot。
            if not isinstance(s, FinalNavigationSynopsis):
                _err(t, f"synopses[{i}] 必须为 FinalNavigationSynopsis，"
                        f"得到 {type(s).__name__}"
                        f"（TS4 NavigationSynopsis 不得进入 fms-1）")
        syn_keys = [_member_sort_key(s) for s in self.synopses]
        if syn_keys != sorted(syn_keys):
            _err(t, "synopses 必须按 (node_id, synopsis_locator) 升序")
        if len(set(syn_keys)) != len(syn_keys):
            _err(t, "同一节点不得出现两条 final synopsis")
        _need_int({"v": self.component_count}, "v", t, lo=0)
        _need_int({"v": self.table_count}, "v", t, lo=0)
        _need_int({"v": self.final_span_count}, "v", t, lo=0)
        _need_int({"v": self.blocking_gap_count}, "v", t, lo=0)
        if self.table_count != len(self.tables):
            _err(t, "table_count 必须等于 tables 长度")
        if self.final_span_count != len(self.final_spans):
            _err(t, "final_span_count 必须等于 final_spans 长度")
        if self.blocking_gap_count != self.gaps.blocking_entry_count:
            _err(t, "blocking_gap_count 必须等于 gaps 的阻断条目数")
        if self.component_count != len(self.bindings):
            _err(t, "component_count 必须等于 bindings 长度（component 精确等集）")
        # 成员的一致性：所有成员必须绑定同一 document / layout / outline / 依赖束。
        for name, seq in (("tables", self.tables), ("final_spans", self.final_spans),
                          ("decisions", self.decisions),
                          ("relations", self.relations),
                          ("bindings", self.bindings),
                          ("coverages", self.coverages)):
            for i, x in enumerate(seq):
                if getattr(x, "upstream_dependency_fingerprint", None) != \
                        self.upstream_dependency_fingerprint:
                    _err(t, f"{name}[{i}] 的 upstream_dependency_fingerprint "
                            f"与 snapshot 不一致（不得混合两次构建的成员）")
        for i, tbl in enumerate(self.tables):
            if tbl.document_id != self.document_id or \
                    tbl.page_layout_id != self.page_layout_id or \
                    tbl.outline_id != self.outline_id:
                _err(t, f"tables[{i}] 不属于本文档（跨树拼接拒绝）")
            if tbl.verified_span_snapshot_id != self.verified_span_snapshot_id:
                _err(t, f"tables[{i}] 绑定的 verified span snapshot 与本文档不一致")
        for i, sp in enumerate(self.final_spans):
            if sp.document_id != self.document_id or \
                    sp.page_layout_id != self.page_layout_id or \
                    sp.outline_id != self.outline_id:
                _err(t, f"final_spans[{i}] 不属于本文档（跨树拼接拒绝）")
        # final synopsis 的**成员闭合**：来源必须解析到本 snapshot 内的 final span，
        # 且必须是同节点。这保证"简介引用了谁"是 snapshot 内部可复核的，
        # 而不是一个悬空 ID。
        span_registry = {sp.span_id: sp for sp in self.final_spans}
        for i, s in enumerate(self.synopses):
            for j, sid in enumerate(s.source_final_span_ids):
                target = span_registry.get(sid)
                if target is None:
                    _err(t, f"synopses[{i}].source_final_span_ids[{j}]="
                            f"{sid!r} 不在本 snapshot 的 final_spans 内"
                            f"（final 简介来源必须成员闭合）")
                if target.node_id != s.node_id:
                    _err(t, f"synopses[{i}] 归属节点 {s.node_id!r}，"
                            f"却引用节点 {target.node_id!r} 的 final span {sid!r}")
        expected_loc = locator("fms", self.locator_payload())
        if self.snapshot_locator != expected_loc:
            _err(t, "snapshot_locator 与派生定位不一致："
                    f"{self.snapshot_locator!r} != {expected_loc!r}")
        expected_id = identity("fms", self.identity_payload())
        if self.snapshot_id != expected_id:
            _err(t, "snapshot_id 与派生身份不一致："
                    f"{self.snapshot_id!r} != {expected_id!r}")
        _check_fingerprint(t, "content_fingerprint", self.content_fingerprint,
                           self._content_payload())

    def _check_member_identity(self, t: str) -> None:
        """终端节点**不得**被任何成员反向引用（无环的第二道闸）。"""
        forbidden = {self.snapshot_locator, self.snapshot_id,
                     self.content_fingerprint}
        blob_parts = []
        for seq in (self.tables, self.final_spans, self.decisions,
                    self.relations, self.bindings, self.coverages):
            for x in seq:
                blob_parts.append(canonical_json(x.to_dict()))
        blob = "\n".join(blob_parts)
        for token in forbidden:
            if token and token in blob:
                _err(t, f"成员对象反向引用了 final snapshot 的 {token!r}"
                        f"（身份 DAG 不得回指终端节点）")

    def locator_payload(self) -> dict:
        return {
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_layout_id": self.page_layout_id,
            "outline_id": self.outline_id,
            "verified_span_snapshot_id": self.verified_span_snapshot_id,
        }

    def _content_payload(self) -> dict:
        return {
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "member_ids": sorted(self.member_ids()),
            "final_versions": {
                "schema_version": self.schema_version,
                "builder_version": self.builder_version,
                "final_span_schema_version": self.final_span_schema_version,
            },
        }

    def member_ids(self) -> list[str]:
        """全部成员的 ID / locator 集合（canonical 排序后进入 snapshot 指纹）。

        `synopses` **必须**在内：`content_fingerprint` 只由本方法的返回值派生，漏掉
        一个成员维度会让"改一处即改身份"在该维度上失效——一条 final synopsis 被改写
        而 `snapshot_id` / `content_fingerprint` 逐字节不变，任何比较指纹而不是重新
        复核的消费者都看不见。收齐后该性质对每个成员维度都成立。
        """
        out: list[str] = []
        for x in self.tables:
            out.extend([x.table_id, x.table_locator])
        for x in self.final_spans:
            out.extend([x.span_id, x.span_locator])
        for x in self.decisions:
            out.extend([x.decision_id, x.decision_locator])
        for x in self.relations:
            out.extend([x.relation_id, x.relation_locator])
        for x in self.bindings:
            out.extend([x.binding_id, x.binding_locator])
        for x in self.coverages:
            out.extend([x.coverage_id, x.coverage_locator])
        for x in self.synopses:
            out.extend([x.synopsis_id, x.synopsis_locator])
        out.extend([self.gaps.gap_id, self.gaps.gap_locator])
        out.extend([self.conservation.conservation_id,
                    self.conservation.conservation_locator])
        return sorted(out)

    def identity_payload(self) -> dict:
        payload = self.locator_payload()
        payload.update({
            "snapshot_locator": self.snapshot_locator,
            "schema_version": self.schema_version,
            "builder_version": self.builder_version,
            "final_span_schema_version": self.final_span_schema_version,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "content_fingerprint": self.content_fingerprint,
        })
        return payload

    def is_financial_authority(self) -> bool:
        return False

    def table_by_id(self, table_id: str) -> TableObjectV4 | None:
        for x in self.tables:
            if x.table_id == table_id:
                return x
        return None

    def final_span_by_id(self, span_id: str) -> FinalOutlineSpan | None:
        for x in self.final_spans:
            if x.span_id == span_id:
                return x
        return None

    def binding_by_component_id(self, component_id: str) -> \
            FinalComponentBinding | None:
        for x in self.bindings:
            if x.component_id == component_id:
                return x
        return None

    def synopsis_by_node_id(self, node_id: str) -> Any:
        for s in self.synopses:
            if getattr(s, "node_id", None) == node_id:
                return s
        return None

    def to_dict(self) -> dict:
        payload = {
            "schema_type": "FinalMaterialStructureSnapshot",
            "snapshot_locator": self.snapshot_locator,
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "builder_version": self.builder_version,
            "final_span_schema_version": self.final_span_schema_version,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_layout_id": self.page_layout_id,
            "outline_id": self.outline_id,
            "verified_span_snapshot_id": self.verified_span_snapshot_id,
            "upstream_dependency_fingerprint":
                self.upstream_dependency_fingerprint,
            "tables": [x.to_dict() for x in self.tables],
            "final_spans": [x.to_dict() for x in self.final_spans],
            "decisions": [x.to_dict() for x in self.decisions],
            "relations": [x.to_dict() for x in self.relations],
            "bindings": [x.to_dict() for x in self.bindings],
            "coverages": [x.to_dict() for x in self.coverages],
            "gaps": self.gaps.to_dict(),
            "conservation": self.conservation.to_dict(),
            "synopses": [_jsonify_synopsis(s) for s in self.synopses],
            "component_count": self.component_count,
            "table_count": self.table_count,
            "final_span_count": self.final_span_count,
            "blocking_gap_count": self.blocking_gap_count,
            "content_fingerprint": self.content_fingerprint,
        }
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "FinalMaterialStructureSnapshot":
        t = "FinalMaterialStructureSnapshot"
        d = _reject_unknown(d, {
            "schema_type", "snapshot_locator", "snapshot_id", "schema_version",
            "builder_version", "final_span_schema_version", "document_id",
            "document_version", "evidence_set_version", "page_layout_id",
            "outline_id", "verified_span_snapshot_id",
            "upstream_dependency_fingerprint", "tables", "final_spans",
            "decisions", "relations", "bindings", "coverages", "gaps",
            "conservation", "synopses", "component_count", "table_count",
            "final_span_count", "blocking_gap_count",
            "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("FinalMaterialStructureSnapshot",))
        return cls(
            snapshot_locator=_need_str(d, "snapshot_locator", t),
            snapshot_id=_need_str(d, "snapshot_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            builder_version=_need_str(d, "builder_version", t),
            final_span_schema_version=_need_str(d, "final_span_schema_version", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            outline_id=_need_str(d, "outline_id", t),
            verified_span_snapshot_id=_need_str(d, "verified_span_snapshot_id", t),
            upstream_dependency_fingerprint=_need_sha256(
                d, "upstream_dependency_fingerprint", t),
            tables=_need_children(d, "tables", t, TableObjectV4.from_dict),
            final_spans=_need_children(d, "final_spans", t,
                                       FinalOutlineSpan.from_dict),
            decisions=_need_children(d, "decisions", t,
                                     TableRangeDecision.from_dict),
            relations=_need_children(d, "relations", t, TableRelation.from_dict),
            bindings=_need_children(d, "bindings", t,
                                    FinalComponentBinding.from_dict),
            coverages=_need_children(d, "coverages", t,
                                     TableCitableCoverage.from_dict),
            gaps=TableStructureGap.from_dict(_need_child_dict(d, "gaps", t)),
            conservation=FinalMaterialConservation.from_dict(
                _need_child_dict(d, "conservation", t)),
            synopses=_need_children(d, "synopses", t,
                                    _decode_final_synopsis),
            component_count=_need_int(d, "component_count", t, lo=0),
            table_count=_need_int(d, "table_count", t, lo=0),
            final_span_count=_need_int(d, "final_span_count", t, lo=0),
            blocking_gap_count=_need_int(d, "blocking_gap_count", t, lo=0),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "FinalMaterialStructureSnapshot":
        """终端节点：**唯一**允许聚合全部上游层；下游不得引用其 identity。"""
        return _assemble(
            cls, fields,
            locator_attr="snapshot_locator", locator_kind="fms",
            locator_method="locator_payload",
            fingerprint_attrs=(("content_fingerprint", "_content_payload"),),
            identity_attr="snapshot_id", identity_kind="fms",
            identity_method="identity_payload")


#: 每类成员对象**自己**的稳定排序键。
#:
#: 不得写成"按属性名逐个 try"：`TableCitableCoverage` 同时带 `table_locator` 与
#: `coverage_locator`，属性探测会静默选中**别人的** locator，使"固定顺序"取决于
#: 属性名拼写而不是对象身份。逐类显式登记后，这个歧义在结构上不存在。
_MEMBER_SORT_ATTR = {
    "TableObjectV4": "table_locator",
    "FinalOutlineSpan": "span_locator",
    "TableRangeDecision": "decision_locator",
    "TableRelation": "relation_locator",
    "FinalComponentBinding": "binding_locator",
    "TableCitableCoverage": "coverage_locator",
}


def _member_sort_key(x: Any) -> tuple:
    # `FinalNavigationSynopsis` 的排序键**不是**单个属性：一个节点允许有多条
    # unavailable 简介（`fms-1` 只禁止同节点重复），只按 `synopsis_locator` 排会让
    # "同节点内的顺序"重新取决于插入顺序。因此它的键是
    # `(node_id, synopsis_locator)`，与 `fms-1` 构造期强制的顺序**同一口径**——
    # 两处若各写一份，排序与校验就会分叉。
    #
    # 它也不进 `_MEMBER_SORT_ATTR`：那张表按**类型名**登记单属性键，登记 TS4
    # `NavigationSynopsis` 会顺带允许它作为成员被排序，等于给"混装进 fms-1"留门。
    if isinstance(x, FinalNavigationSynopsis):
        return ("node_id", x.node_id, x.synopsis_locator)
    attr = _MEMBER_SORT_ATTR.get(type(x).__name__)
    if attr is None:
        raise SchemaValidationError(
            f"成员对象 {type(x).__name__} 未登记稳定排序键（不得靠插入顺序）")
    value = getattr(x, attr, None)
    if not isinstance(value, str) or value == "":
        raise SchemaValidationError(
            f"成员对象 {type(x).__name__} 的 {attr} 必须为非空字符串")
    return (attr, value)


def _need_child_dict(d: dict, key: str, typename: str) -> Any:
    v = d.get(key)
    if not isinstance(v, dict):
        _err(typename, f"{key} 必须为对象，得到 {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# 13bis. `FinalNavigationSynopsis`（`nss-3` / `ns-3`，§19.0.1 方案 C / §19.10）
# ---------------------------------------------------------------------------
#
# 与 TS4 的 `document_structure.schema.NavigationSynopsis`（`nss-2` / `ns-2`）是
# **两个 public wire type**。它们在五处不同：
#
# | | TS4 `NavigationSynopsis` | TS5 `FinalNavigationSynopsis` |
# |---|---|---|
# | `schema_type` | `NavigationSynopsis` | `FinalNavigationSynopsis` |
# | schema 轴 | `nss-2`（冻结） | `nss-3`（本批新增） |
# | 算法轴 | `ns-2`（冻结） | `ns-3`（本批新增） |
# | reason 词表 | 含 `table_only_pending_ts5` | 含 `table_material_available_no_text_synopsis` |
# | 允许来源 | TS4 `OutlineSpan os-*` | `FinalOutlineSpan fos-*` |
#
# 两侧各有一个**对方不允许出现**的 reason，来源身份前缀也不同。这三重差异必须由
# **类型本身**承载，不能靠"同一类 + 一个布尔开关 + 一套查表"实现：后者会让 aggregate
# 只能按 child 自报的版本猜 reader，而这正是 §19.4.1 禁止的。
#
# 因此本类**不继承** `NavigationSynopsis`、不 alias 它、不与其共用常量对象。

#: final 简介的状态集合。与 TS4 同名同义，但**刻意**是独立常量：
#: 若将来只有一个类型需要新状态，共用常量会迫使另一个类型也接受它。
FINAL_SYNOPSIS_STATUSES = ("available", "synopsis_unavailable")

#: final 简介的封闭 reason 词表（§19.10），**五个值，与 TS4 词表逐字对齐前四项**。
#:
#: 唯一差异是最后一格。TS4 的 `table_only_pending_ts5` 表示"内容还在等 TS5 决议"——
#: TS5 之后不得再产出；本表的 `table_material_available_no_text_synopsis` 表示
#: "表格已经是正式 material，但该节点没有 final 可引用 paragraph 可供抽取"。
#:
#: 两者不可互换：把前者放进本表就是"TS5 之后还在等 TS5"，把后者放进 TS4 词表就是
#: "TS4 提前知道了 final 的结论"。`self_check()` 逐项断言两个集合的专属值互不出现。
FINAL_SYNOPSIS_REASON_CODES = ("no_span", "empty_text", "length_exceeded",
                               "alignment_failed",
                               "table_material_available_no_text_synopsis")

#: final span 的身份前缀。它把"来源必须是 final span"从一条约定变成一次前缀断言：
#: TS4 的 `os-*` 与任何表格/cell ID 都不可能通过。
FINAL_SPAN_ID_PREFIX = "fos-"


@dataclass(frozen=True)
class FinalSynopsisSnippet:
    """final 简介片段：精确绑定**单一** `FinalOutlineSpan` 的连续字符区间。

    与 TS4 `SynopsisSnippet` 的差别不只是字段名：这里同时携带来源 span 的
    **locator** 与 **schema 版本**，因此单看一个 snippet 就能回查"它引用的是哪一份
    final span、按哪版 wire 解释"，不需要再去别处取上下文。

    TS4 的 `SynopsisSnippet.span_id` 因此**无法**被直接搬进来：字段名不同、且
    `final_span_id` 有 `fos-` 前缀断言。
    """

    final_span_id: str
    final_span_locator: str
    final_span_schema_version: str
    snippet_index: int
    char_start: int
    char_end: int
    text: str

    def __post_init__(self) -> None:
        t = "FinalSynopsisSnippet"
        for name in ("final_span_id", "final_span_locator",
                     "final_span_schema_version", "text"):
            v = getattr(self, name)
            if not isinstance(v, str):
                _err(t, f"{name} 必须为字符串，得到 {type(v).__name__}")
        if not self.final_span_id.startswith(FINAL_SPAN_ID_PREFIX):
            _err(t, f"final_span_id 必须为 final span 身份（{FINAL_SPAN_ID_PREFIX} "
                    f"前缀），得到 {self.final_span_id!r}；TS4 `os-*` span、"
                    f"TableObject/cell ID 一律不得冒充 final 简介来源")
        if self.final_span_schema_version != V.FINAL_SPAN_SCHEMA_VERSION:
            _err(t, f"final_span_schema_version 必须为 "
                    f"{V.FINAL_SPAN_SCHEMA_VERSION!r}，"
                    f"得到 {self.final_span_schema_version!r}")
        _need_int({"v": self.snippet_index}, "v", t, lo=0)
        _need_int({"v": self.char_start}, "v", t, lo=0)
        _need_int({"v": self.char_end}, "v", t, lo=0)
        if self.char_end <= self.char_start:
            _err(t, "char_end 必须严格大于 char_start")
        if self.text == "":
            _err(t, "text 不得为空字符串")

    def to_dict(self) -> dict:
        return {
            "final_span_id": self.final_span_id,
            "final_span_locator": self.final_span_locator,
            "final_span_schema_version": self.final_span_schema_version,
            "snippet_index": self.snippet_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinalSynopsisSnippet":
        t = "FinalSynopsisSnippet"
        d = _reject_unknown(d, {
            "final_span_id", "final_span_locator", "final_span_schema_version",
            "snippet_index", "char_start", "char_end", "text"}, t)
        return cls(
            final_span_id=_need_str(d, "final_span_id", t),
            final_span_locator=_need_str(d, "final_span_locator", t),
            final_span_schema_version=_need_str(
                d, "final_span_schema_version", t),
            snippet_index=_need_int(d, "snippet_index", t, lo=0),
            char_start=_need_int(d, "char_start", t, lo=0),
            char_end=_need_int(d, "char_end", t, lo=0),
            text=_need_str(d, "text", t),
        )


@dataclass(frozen=True)
class FinalNavigationSynopsis:
    """TS5 final 节点的抽取式导航简介（`nss-3` / `ns-3`）。

    - **稳定定位身份** `synopsis_locator`：`loc-fns-<h(node_id, ns-3, 来源 final span
      locator 集)>`。"是谁 / 在哪"变了才换 locator。
    - **不可变 revision 身份** `synopsis_id`：在 locator 之上绑定 schema 版本、status、
      reason 与全部 snippets——因此"来源不变但摘录被改写 / 状态翻转"是另一个身份。

    与 TS4 简介一样，本对象**只作候选导航**：它不是事实、不是引用、不参与
    `set_complete`，也不构成任何 citable 资格。表格本体的导航元数据（表题、表头、
    路径）由 TS6 直接索引 `TableObject`，**不**经过本对象（§19.10）。
    """

    synopsis_locator: str
    synopsis_id: str
    node_id: str
    schema_version: str
    synopsis_version: str
    status: str
    reason_code: str | None
    snippets: tuple[FinalSynopsisSnippet, ...]
    source_final_span_ids: tuple[str, ...]

    SCHEMA_CONSTANT = "FINAL_SYNOPSIS_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "FinalNavigationSynopsis"
        for name in ("synopsis_locator", "synopsis_id", "node_id"):
            if not isinstance(getattr(self, name), str) or getattr(self, name) == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        if self.synopsis_version != V.FINAL_SYNOPSIS_VERSION:
            _err(t, f"synopsis_version 必须为 {V.FINAL_SYNOPSIS_VERSION!r}"
                    f"（TS4 的 {V.SYNOPSIS_VERSION!r} 属于另一个类型），"
                    f"得到 {self.synopsis_version!r}")
        if self.status not in FINAL_SYNOPSIS_STATUSES:
            _err(t, f"status 必须属于 {FINAL_SYNOPSIS_STATUSES}，"
                    f"得到 {self.status!r}")
        if not isinstance(self.snippets, tuple):
            _err(t, "snippets 必须为元组")
        if not isinstance(self.source_final_span_ids, tuple):
            _err(t, "source_final_span_ids 必须为元组")
        for i, sid in enumerate(self.source_final_span_ids):
            if not isinstance(sid, str) or sid == "":
                _err(t, f"source_final_span_ids[{i}] 必须为非空字符串")
            if not sid.startswith(FINAL_SPAN_ID_PREFIX):
                _err(t, f"source_final_span_ids[{i}] 必须为 final span 身份"
                        f"（{FINAL_SPAN_ID_PREFIX} 前缀），得到 {sid!r}")
        if len(set(self.source_final_span_ids)) != len(self.source_final_span_ids):
            _err(t, "source_final_span_ids 不得重复（去重后按源顺序）")

        if self.status == "available":
            if not self.snippets:
                _err(t, "status='available' 时 snippets 不得为空")
            if self.reason_code is not None:
                _err(t, "status='available' 时 reason_code 必须为 None")
            ordered: list[str] = []
            for i, sn in enumerate(self.snippets):
                if not isinstance(sn, FinalSynopsisSnippet):
                    _err(t, f"snippets[{i}] 必须为 FinalSynopsisSnippet，"
                            f"得到 {type(sn).__name__}")
                if sn.snippet_index != i:
                    _err(t, f"snippets[{i}].snippet_index 必须等于其位置 {i}")
                if sn.final_span_id not in ordered:
                    ordered.append(sn.final_span_id)
            if tuple(ordered) != self.source_final_span_ids:
                _err(t, "source_final_span_ids 必须等于 snippets 的 final_span_id "
                        f"按源顺序去重（{tuple(ordered)} vs "
                        f"{self.source_final_span_ids}）")
        else:
            if self.snippets:
                _err(t, "status='synopsis_unavailable' 时 snippets 必须为空")
            if self.reason_code not in FINAL_SYNOPSIS_REASON_CODES:
                _err(t, f"status='synopsis_unavailable' 时 reason_code 必须属于 "
                        f"{FINAL_SYNOPSIS_REASON_CODES}，得到 {self.reason_code!r}")
            # §19.10：那两个"无文本可摘"的理由**不得**声明任何来源——否则就是把
            # 表格材料伪装成 final span 简介。
            if self.reason_code in ("no_span",
                                    "table_material_available_no_text_synopsis") \
                    and self.source_final_span_ids:
                _err(t, f"reason_code={self.reason_code!r} 时不得声明 "
                        f"source_final_span_ids（该理由表示没有可引用 paragraph）")

        expected_loc = self.derive_locator()
        if self.synopsis_locator != expected_loc:
            _err(t, "synopsis_locator 与派生定位身份不一致："
                    f"{self.synopsis_locator!r} != {expected_loc!r}")
        expected_id = self.derive_id()
        if self.synopsis_id != expected_id:
            _err(t, f"synopsis_id 与派生身份不一致："
                    f"{self.synopsis_id!r} != {expected_id!r}")

    def locator_payload(self) -> dict:
        return {
            "node_id": self.node_id,
            "synopsis_version": self.synopsis_version,
            "source_final_span_ids": list(self.source_final_span_ids),
        }

    def identity_payload(self) -> dict:
        return {
            "synopsis_locator": self.synopsis_locator,
            "node_id": self.node_id,
            "schema_version": self.schema_version,
            "synopsis_version": self.synopsis_version,
            "status": self.status,
            "reason_code": self.reason_code,
            "snippets": [sn.to_dict() for sn in self.snippets],
            "source_final_span_ids": list(self.source_final_span_ids),
        }

    def derive_locator(self) -> str:
        return locator("fns", self.locator_payload())

    def derive_id(self) -> str:
        return identity("fns", self.identity_payload())

    def is_navigation_only(self) -> bool:
        """恒为 True：本对象的唯一合法用途是候选导航（不得充当证据）。"""
        return True

    def to_dict(self) -> dict:
        payload = self.identity_payload()
        payload["schema_type"] = "FinalNavigationSynopsis"
        payload["synopsis_id"] = self.synopsis_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "FinalNavigationSynopsis":
        t = "FinalNavigationSynopsis"
        d = _reject_unknown(d, {
            "schema_type", "synopsis_locator", "synopsis_id", "node_id",
            "schema_version", "synopsis_version", "status", "reason_code",
            "snippets", "source_final_span_ids"}, t)
        # aggregate 由**本类**决定合法 leaf：TS4 的 `NavigationSynopsis` 在这里因为
        # `schema_type` 不同而被拒，反之亦然。两边都不"按 child 猜 reader"。
        _need_enum(d, "schema_type", t, ("FinalNavigationSynopsis",))
        return cls(
            synopsis_locator=_need_str(d, "synopsis_locator", t),
            synopsis_id=_need_str(d, "synopsis_id", t),
            node_id=_need_str(d, "node_id", t),
            schema_version=_need_str(d, "schema_version", t),
            synopsis_version=_need_str(d, "synopsis_version", t),
            status=_need_enum(d, "status", t, FINAL_SYNOPSIS_STATUSES),
            reason_code=_need_str(d, "reason_code", t, none_ok=True),
            snippets=_need_children(d, "snippets", t,
                                    FinalSynopsisSnippet.from_dict),
            source_final_span_ids=_need_str_tuple(
                d, "source_final_span_ids", t),
        )

    @classmethod
    def create(cls, **fields: Any) -> "FinalNavigationSynopsis":
        """由非派生字段两阶段装配（locator / revision id 全部本类派生）。

        与其它 TS5 wire 类型同一套 `_assemble` 约定：派生逻辑只有 `locator_payload`
        / `identity_payload` 一处，校验不会与派生分叉。
        """
        return _assemble(cls, fields,
                         locator_attr="synopsis_locator", locator_kind="fns",
                         locator_method="locator_payload",
                         identity_attr="synopsis_id", identity_kind="fns",
                         identity_method="identity_payload")

    @classmethod
    def available(cls, *, node_id: str,
                  snippets: tuple[FinalSynopsisSnippet, ...]
                  ) -> "FinalNavigationSynopsis":
        """可用的 final 简介：来源序由 `snippets` 的去重顺序**派生**。"""
        ordered: list[str] = []
        for sn in snippets:
            if sn.final_span_id not in ordered:
                ordered.append(sn.final_span_id)
        return cls._build(node_id=node_id, status="available", reason_code=None,
                          snippets=snippets,
                          source_final_span_ids=tuple(ordered))

    @classmethod
    def unavailable(cls, *, node_id: str, reason_code: str,
                    source_final_span_ids: tuple[str, ...] = ()
                    ) -> "FinalNavigationSynopsis":
        """诚实的不可用简介。`reason_code` 必须属于 **final** 词表。"""
        return cls._build(node_id=node_id, status="synopsis_unavailable",
                          reason_code=reason_code, snippets=(),
                          source_final_span_ids=source_final_span_ids)

    @classmethod
    def _build(cls, *, node_id: str, status: str, reason_code: str | None,
               snippets: tuple[FinalSynopsisSnippet, ...],
               source_final_span_ids: tuple[str, ...]
               ) -> "FinalNavigationSynopsis":
        return cls.create(
            node_id=node_id,
            schema_version=V.FINAL_SYNOPSIS_SCHEMA_VERSION,
            synopsis_version=V.FINAL_SYNOPSIS_VERSION,
            status=status, reason_code=reason_code, snippets=snippets,
            source_final_span_ids=source_final_span_ids)


def final_synopsis_fingerprint(synopsis: FinalNavigationSynopsis) -> str:
    """final 简介的**独立**内容指纹：只含本类型自己的字段与版本轴。

    刻意不复用 TS4 `synopsis_fingerprint`：那一个绑定 `V.SYNOPSIS_VERSION`，共用
    会让两代简介的指纹落在同一个命名空间里，从而无法在产物里区分"这是哪一代"。
    """
    return sha256_canonical({
        "schema_type": "FinalNavigationSynopsis",
        "schema_version": synopsis.schema_version,
        "synopsis_version": synopsis.synopsis_version,
        "synopsis_id": synopsis.synopsis_id,
        "synopsis_locator": synopsis.synopsis_locator,
        "node_id": synopsis.node_id,
        "status": synopsis.status,
        "reason_code": synopsis.reason_code,
        "snippets": [sn.to_dict() for sn in synopsis.snippets],
        "source_final_span_ids": list(synopsis.source_final_span_ids),
    })


# ---------------------------------------------------------------------------
# 14. final synopsis 的 wire 适配
# ---------------------------------------------------------------------------
#
# §19.10：TS5 不把表题 / 表头 / cell 伪装成 `SynopsisSnippet(span_id=...)`。
# final synopsis 是**独立类型** `FinalNavigationSynopsis`（`nss-3` / `ns-3`），
# 来源只允许 `FinalOutlineSpan fos-*`，且只能进入 `fms-1`。
#
# 这两个适配函数是"成员维度"的唯一出入口：`to_dict` / `from_dict` 两条路径都要经过
# 它们，因此不可能出现"写入时是 final 类型、读回时变成 TS4 类型"的错配。

def _jsonify_synopsis(obj: Any) -> dict:
    if not isinstance(obj, FinalNavigationSynopsis):
        raise SchemaValidationError(
            f"final snapshot 的 synopses 必须为 FinalNavigationSynopsis，"
            f"得到 {type(obj).__name__}"
            f"（TS4 `NavigationSynopsis` 不得进入 fms-1）")
    payload = obj.to_dict()
    if payload.get("schema_version") != V.FINAL_SYNOPSIS_SCHEMA_VERSION:
        raise SchemaValidationError(
            f"final synopsis 的 schema_version 必须为 "
            f"{V.FINAL_SYNOPSIS_SCHEMA_VERSION!r}，"
            f"得到 {payload.get('schema_version')!r}")
    return payload


def _decode_final_synopsis(payload: Any) -> FinalNavigationSynopsis:
    """final snapshot 的 synopsis reader：**类型 + 版本 + 来源**三处同时收紧。"""
    if not isinstance(payload, dict):
        raise SchemaValidationError(
            f"final synopsis 必须为对象，得到 {type(payload).__name__}")
    schema_type = payload.get("schema_type")
    if schema_type != "FinalNavigationSynopsis":
        raise SchemaValidationError(
            f"final snapshot 只接受 `FinalNavigationSynopsis`，"
            f"得到 schema_type={schema_type!r}"
            f"（TS4 `NavigationSynopsis` 不得进入 fms-1；两代 synopsis 不得混装）")
    obj = FinalNavigationSynopsis.from_dict(payload)
    if obj.schema_version != V.FINAL_SYNOPSIS_SCHEMA_VERSION:
        raise SchemaValidationError(
            f"final synopsis 必须为 {V.FINAL_SYNOPSIS_SCHEMA_VERSION!r}")
    return obj


# ---------------------------------------------------------------------------
# 15. 身份有向无环图（§19.4.4，机器强制）
# ---------------------------------------------------------------------------

#: DAG 的层序（严格：每层只能引用比它更早的层）。
IDENTITY_DAG_NODES: tuple[str, ...] = (
    "upstream_dependency", "table_object", "final_span", "range_decision",
    "component_binding", "citable_coverage", "relation", "gap", "conservation",
    "final_snapshot",
)

#: 每层可以引用的**更早**层（`IDENTITY_DAG_EDGES[层] = 允许引用的层集合`）。
IDENTITY_DAG_EDGES: dict[str, tuple[str, ...]] = {
    "upstream_dependency": (),
    "table_object": ("upstream_dependency",),
    "final_span": ("upstream_dependency",),
    "range_decision": ("upstream_dependency", "table_object", "final_span"),
    "component_binding": ("upstream_dependency", "table_object", "final_span",
                          "range_decision"),
    "citable_coverage": ("upstream_dependency", "table_object", "final_span"),
    "relation": ("upstream_dependency", "table_object", "final_span",
                 "component_binding"),
    "gap": ("upstream_dependency", "table_object", "final_span",
            "range_decision", "component_binding", "citable_coverage",
            "relation"),
    "conservation": ("upstream_dependency", "table_object", "final_span",
                     "range_decision", "component_binding", "citable_coverage",
                     "relation"),
    "final_snapshot": ("upstream_dependency", "table_object", "final_span",
                       "range_decision", "component_binding",
                       "citable_coverage", "relation", "gap", "conservation"),
}


def identity_dag_problems() -> list[str]:
    """逐条检验 DAG：只允许指向严格更早层，且不得遗漏任何层。"""
    problems: list[str] = []
    index = {name: i for i, name in enumerate(IDENTITY_DAG_NODES)}
    if set(IDENTITY_DAG_EDGES) != set(IDENTITY_DAG_NODES):
        problems.append("IDENTITY_DAG_EDGES 的层集合与 IDENTITY_DAG_NODES 不一致")
    for layer, refs in IDENTITY_DAG_EDGES.items():
        if layer not in index:
            problems.append(f"未登记的层 {layer!r}")
            continue
        for ref in refs:
            if ref not in index:
                problems.append(f"{layer} 引用了未登记的层 {ref!r}")
                continue
            if index[ref] >= index[layer]:
                problems.append(
                    f"{layer} 引用了不比它更早的层 {ref!r}"
                    f"（禁止回指 / 自我证明）")
        if len(set(refs)) != len(refs):
            problems.append(f"{layer} 的引用层重复")
    return problems


def assert_identity_dag_acyclic() -> None:
    """DAG 有环 / 回指即 fail-closed（供 builder 与测试共用）。"""
    problems = identity_dag_problems()
    if problems:
        raise SchemaValidationError(
            "identity DAG 非法：" + "；".join(problems))


def identity_dag_topological_order() -> tuple[str, ...]:
    """Kahn 拓扑序；有环则抛错（环检测的**独立**实现，不依赖层号比较）。"""
    incoming = {n: set() for n in IDENTITY_DAG_NODES}
    for layer, refs in IDENTITY_DAG_EDGES.items():
        for ref in refs:
            if ref not in incoming:
                raise SchemaValidationError(f"{layer} 引用了未登记的层 {ref!r}")
            incoming[layer].add(ref)
    order: list[str] = []
    ready = sorted(n for n in IDENTITY_DAG_NODES if not incoming[n])
    pending = {n: set(v) for n, v in incoming.items()}
    while ready:
        node = ready.pop(0)
        order.append(node)
        for other, deps in pending.items():
            if node in deps:
                deps.discard(node)
                if not deps and other not in order and other not in ready:
                    ready.append(other)
        ready.sort()
    if len(order) != len(IDENTITY_DAG_NODES):
        cycle = sorted(set(IDENTITY_DAG_NODES) - set(order))
        raise SchemaValidationError(f"identity DAG 存在环：{cycle}")
    return tuple(order)


# ---------------------------------------------------------------------------
# 16. 注册表与自检
# ---------------------------------------------------------------------------

#: TS5 的 11 个 wire 类型（键名与 `versions.TABLE_RECORD_PUBLIC_TYPES` 一致）。
#:
#: §19.0.1 方案 C：`FinalNavigationSynopsis` 是本表的**第 11 项**，与 TS4 的
#: `schema.NavigationSynopsis` 是两个类型。它登记在 TS5 表里、走
#: `FINAL_SYNOPSIS_SCHEMA_VERSION` 轴，因此"两代简介"在登记层面就不会被并成一项。
TABLE_RECORD_TYPES: dict[str, Any] = {
    "TableObjectV4": TableObjectV4,
    "TableCellV4": TableCellV4,
    "FinalOutlineSpan": FinalOutlineSpan,
    "TableRangeDecision": TableRangeDecision,
    "TableRelation": TableRelation,
    "FinalComponentBinding": FinalComponentBinding,
    "TableCitableCoverage": TableCitableCoverage,
    "TableStructureGap": TableStructureGap,
    "FinalMaterialConservation": FinalMaterialConservation,
    "FinalMaterialStructureSnapshot": FinalMaterialStructureSnapshot,
    "FinalNavigationSynopsis": FinalNavigationSynopsis,
}

#: 14 个子结构（无独立 schema version）。
SUBSTRUCTURE_REGISTRY: dict[str, Any] = {
    "TableRowV4": TableRowV4,
    "TableOwnerRef": TableOwnerRef,
    "TableCellSourceRef": TableCellSourceRef,
    "TableCellBlock": TableCellBlock,
    "TableSourceInterval": TableSourceInterval,
    "TableEndpointRef": TableEndpointRef,
    "FinalSpanEndpointRef": FinalSpanEndpointRef,
    "ComponentEndpointRef": ComponentEndpointRef,
    "ReferenceOccurrenceEndpointRef": ReferenceOccurrenceEndpointRef,
    "TableGapEntry": TableGapEntry,
    "TableCandidateAuditRow": TableCandidateAuditRow,
    "CitableInterval": CitableInterval,
    "ConservationLayer": ConservationLayer,
    "ConservationTerm": ConservationTerm,
}


def _mapping_problems() -> list[str]:
    problems: list[str] = []
    if set(COMPONENT_LANDING_ADMISSIONS) != set(COMPONENT_LANDINGS):
        problems.append("COMPONENT_LANDING_ADMISSIONS 的键集必须与 "
                        "COMPONENT_LANDINGS 精确相等")
    for landing, admissions in COMPONENT_LANDING_ADMISSIONS.items():
        if not admissions:
            problems.append(f"{landing} 的允许终态不得为空")
        if len(set(admissions)) != len(admissions):
            problems.append(f"{landing} 的允许终态重复")
        for a in admissions:
            if a not in BINDING_ADMISSIONS:
                problems.append(f"{landing} 引用了未登记的终态 {a!r}")
    if set(COMPONENT_LANDING_PENDING_REASONS) != set(COMPONENT_LANDINGS):
        problems.append("COMPONENT_LANDING_PENDING_REASONS 必须覆盖全部 landing")
    if set(COMPONENT_LANDING_REJECTED_REASONS) - set(COMPONENT_LANDINGS):
        problems.append("COMPONENT_LANDING_REJECTED_REASONS 含未登记的 landing")
    for landing, reasons in COMPONENT_LANDING_PENDING_REASONS.items():
        for r in reasons:
            if r not in PENDING_ADMISSION_REASONS:
                problems.append(f"{landing} 的 pending 理由 {r!r} 未登记")
        if "pending" not in COMPONENT_LANDING_ADMISSIONS[landing]:
            problems.append(f"{landing} 不允许 pending，却登记了 pending 理由")
    for landing, reasons in COMPONENT_LANDING_REJECTED_REASONS.items():
        for r in reasons:
            if r not in REJECTED_ADMISSION_REASONS:
                problems.append(f"{landing} 的 rejected 理由 {r!r} 未登记")
        if "rejected" not in COMPONENT_LANDING_ADMISSIONS[landing]:
            problems.append(f"{landing} 不允许 rejected，却登记了 rejected 理由")
    for landing in COMPONENT_LANDINGS:
        a = COMPONENT_LANDING_ADMISSIONS[landing]
        if "pending" in a and not COMPONENT_LANDING_PENDING_REASONS[landing]:
            problems.append(f"{landing} 允许 pending 但未登记任何 pending 理由")
        if "rejected" in a and not COMPONENT_LANDING_REJECTED_REASONS.get(landing):
            problems.append(f"{landing} 允许 rejected 但未登记任何 rejected 理由")
    # 集合闭合：四种终态的覆盖并集必须恰为全部 landing 的允许集合。
    union: set[str] = set()
    for a in COMPONENT_LANDING_ADMISSIONS.values():
        union |= set(a)
    if union != set(BINDING_ADMISSIONS):
        problems.append(f"landing→终态映射的并集 {sorted(union)} 不等于 "
                        f"{sorted(BINDING_ADMISSIONS)}（未闭合）")
    return problems


def _decision_table_problems() -> list[str]:
    problems: list[str] = []
    if set(TABLE_DECISION_TARGETS) != set(TABLE_DECISION_KINDS):
        problems.append("TABLE_DECISION_TARGETS 必须覆盖全部 decision")
    for k, tgt in TABLE_DECISION_TARGETS.items():
        if tgt not in ("table", "final_span", "none"):
            problems.append(f"{k} 的 target {tgt!r} 未登记")
    rebuilt: dict[str, list[str]] = {"table": [], "final_span": [], "none": []}
    for k, tgt in TABLE_DECISION_TARGETS.items():
        rebuilt[tgt].append(k)
    for tgt, keys in DECISION_TARGET_OF.items():
        if tuple(sorted(keys)) != tuple(sorted(rebuilt[tgt])):
            problems.append(f"DECISION_TARGET_OF[{tgt!r}] 与真值表不一致")
    return problems


def _relation_table_problems() -> list[str]:
    problems: list[str] = []
    for kind in RESOLVABLE_RELATION_KINDS:
        if kind not in RELATION_ENDPOINT_TYPES:
            problems.append(f"可解析关系 {kind!r} 未登记端点类型对")
    for kind in FUTURE_ONLY_RELATION_KINDS:
        if kind in RELATION_ENDPOINT_TYPES:
            problems.append(f"仅保留扩展位的关系 {kind!r} 不得登记端点类型对")
    if set(RESOLVABLE_RELATION_KINDS) | set(FUTURE_ONLY_RELATION_KINDS) != \
            set(TABLE_RELATION_KINDS):
        problems.append("关系种类词表未被 可解析 ∪ 未来 恰好覆盖")
    for kind, (src, tgts) in RELATION_ENDPOINT_TYPES.items():
        if src not in ENDPOINT_REF_BY_NAME:
            problems.append(f"{kind} 的来源端点类型 {src!r} 未登记")
        for t in tgts:
            if t not in ENDPOINT_REF_BY_NAME:
                problems.append(f"{kind} 的目标端点类型 {t!r} 未登记")
    if set(ENDPOINT_REF_BY_KIND) != {t.endpoint_kind for t in ENDPOINT_REF_TYPES}:
        problems.append("ENDPOINT_REF_BY_KIND 与端点类型集合不一致")
    if len(ENDPOINT_REF_TYPES) != 4:
        problems.append("typed endpoint 必须恰为 4 种")
    return problems


def _conservation_problems() -> list[str]:
    problems: list[str] = []
    if set(CONSERVATION_LAYER_TERMS) != set(CONSERVATION_LAYER_KINDS):
        problems.append("CONSERVATION_LAYER_TERMS 必须覆盖全部守恒层")
    if CONSERVATION_TOTAL_TERM in CONSERVATION_TERM_KIND_OF:
        problems.append(
            f"合计名 {CONSERVATION_TOTAL_TERM!r} 不得登记为分项（合计不是分项）")
    for terms in CONSERVATION_LAYER_TERMS.values():
        if CONSERVATION_TOTAL_TERM in terms:
            problems.append(
                f"合计名 {CONSERVATION_TOTAL_TERM!r} 不得混入任何一层的分项表")
    seen: dict[str, str] = {}
    for layer, terms in CONSERVATION_LAYER_TERMS.items():
        if len(set(terms)) != len(terms):
            problems.append(f"第 {layer} 层的分项重复")
        for t in terms:
            if t not in CONSERVATION_TERM_KIND_OF:
                problems.append(f"分项 {t!r} 未登记语义类别")
            elif t in seen:
                problems.append(f"分项 {t!r} 同时出现在 {seen[t]!r} 与 {layer!r} 层")
            else:
                seen[t] = layer
    return problems


def self_check() -> dict:
    """本模块的机械自检（供 `evals.test_tree_table_schema` 与 verifier 调用）。"""
    problems: list[str] = []
    if len(TABLE_RECORD_TYPES) != 11:
        problems.append("TS5 wire 类型必须恰为 11 个（含独立的 "
                        "FinalNavigationSynopsis）")
    if len(SUBSTRUCTURE_REGISTRY) != 14:
        problems.append("TS5 子结构必须恰为 14 个")
    if len(UPSTREAM_DEPENDENCY_FIELDS) != len(set(UPSTREAM_DEPENDENCY_FIELDS)):
        problems.append("UPSTREAM_DEPENDENCY_FIELDS 含重复字段")
    if set(TABLE_RECORD_TYPES) != set(V.TABLE_RECORD_PUBLIC_TYPES):
        problems.append("TS5 wire 类型清单必须与 versions.TABLE_RECORD_PUBLIC_TYPES "
                        "精确一致")
    if set(SUBSTRUCTURE_REGISTRY) != set(V.TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES):
        problems.append("TS5 子结构清单必须与 "
                        "versions.TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES 精确一致")
    for name, t in TABLE_RECORD_TYPES.items():
        if not hasattr(t, "SCHEMA_CONSTANT"):
            problems.append(f"{name} 缺 SCHEMA_CONSTANT")
        if not hasattr(t, "from_dict") or not hasattr(t, "to_dict"):
            problems.append(f"{name} 必须严格 to_dict/from_dict")
        fields = getattr(t, "__dataclass_fields__", None)
        if fields is None or "schema_version" not in fields:
            problems.append(f"{name} 必须有 schema_version 字段")
    for name, t in SUBSTRUCTURE_REGISTRY.items():
        fields = getattr(t, "__dataclass_fields__", None)
        if fields is None:
            problems.append(f"{name} 必须为 dataclass")
        elif "schema_version" in fields:
            problems.append(f"{name} 不得自带 schema_version（随宿主版本轴）")
    problems.extend(identity_dag_problems())
    problems.extend(_mapping_problems())
    problems.extend(_decision_table_problems())
    problems.extend(_relation_table_problems())
    problems.extend(_conservation_problems())
    try:
        order = identity_dag_topological_order()
        if set(order) != set(IDENTITY_DAG_NODES):
            problems.append("拓扑序未覆盖全部层")
    except SchemaValidationError as e:
        problems.append(str(e))
    for code in DOCUMENT_BLOCKING_REASONS:
        if code not in PENDING_ADMISSION_REASONS:
            problems.append(f"阻断理由 {code!r} 不在 pending 理由集合内")
    return {
        "wire_type_count": len(TABLE_RECORD_TYPES),
        "substructure_type_count": len(SUBSTRUCTURE_REGISTRY),
        "component_landing_count": len(COMPONENT_LANDING_ADMISSIONS),
        "relation_kind_count": len(TABLE_RELATION_KINDS),
        "endpoint_kind_count": len(ENDPOINT_REF_TYPES),
        "conservation_layer_count": len(CONSERVATION_LAYER_KINDS),
        "gap_kind_count": len(TABLE_GAP_KINDS),
        "upstream_dependency_field_count": len(UPSTREAM_DEPENDENCY_FIELDS),
        "identity_dag_nodes": list(IDENTITY_DAG_NODES),
        "identity_dag_order": list(identity_dag_topological_order()),
        "table_object_is_financial_authority": False,
        "problems": problems,
    }


def _main(argv: list[str] | None = None) -> int:
    import json
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--self-check",):
        print(f"未知参数: {args[0]}", file=sys.stderr)
        return 2
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report["problems"] else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))

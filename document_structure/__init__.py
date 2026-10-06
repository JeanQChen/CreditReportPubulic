"""`document_structure`：目标链 `电子 PDF → PageLayout → DocumentOutline →
OutlineSpan/TableObject → Retriever/ToolRegistry → Harness Topic runtime →
TopicResearchPack` 的**类型与身份层**。

TS1 只交付公共类型、版本常量、内容寻址身份、严格序列化/反序列化、构造期不变量与
确定性 fail-closed 验证。**不含**任何构建算法（PDF 读取、版式构建、标题识别、对齐、
span 切分、表格识别、profile 派生、跨树聚合分属 TS2–TS7B），也不接 Store / Pack /
Retriever / ToolRegistry。

导入顺序刻意固定：先 `canonical`（最低层原语，仅标准库），再 `versions`（唯一版本
字面量来源），最后 `schema`（引用前二者）。因此本包不存在"部分初始化"顺序依赖：
任何模块都可以安全地 `from document_structure import versions as V` 或
`from document_structure.schema import OutlineSpan`。

本包位于目标链最底层，**不**依赖 `harness`（Topic runtime）或任何上层包；
`SchemaValidationError` 因此来自 `document_structure.canonical`，而不是
`harness.topic_schema`（后者会构成层级倒置，TS7B 接线后成为真实循环导入）。
"""

from __future__ import annotations

from document_structure import canonical
from document_structure import versions
from document_structure import schema
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    canonical_text,
    identity,
    is_finite,
    locator,
    sha256_canonical,
    to_json_value,
)
from document_structure.versions import (
    ALIGN_MIN,
    FLOAT_PRECISION,
    HEADING_QUALIFICATION_PROFILE_VERSION,
    SPAN_CONFIDENCE_MIN,
    TABLE_REGION_QUALIFICATION_VERSION,
    VERSION_CONSTANTS,
    VERSIONED_OBJECT_SCHEMA_FIELDS,
    all_version_literals,
    classify_schema_version,
    legacy_versions,
)
from document_structure.schema import (
    ALIGNMENT_REFUSAL_REASONS,
    ALIGNMENT_VERDICTS,
    DEFERRED_TO_TS7A,
    EDGE_ENDPOINT_KINDS,
    EDGE_KINDS,
    LOCATOR_PREFIXES,
    OBJECT_BACKED_REF_KINDS,
    OCCURRENCE_REQUIRED_EDGE_KINDS,
    OUTLINE_OWNED_REF_KINDS,
    PUBLIC_TYPE_NAMES,
    PUBLIC_TYPES,
    RESOLVABLE_EDGE_KINDS,
    TABLE_LOCATOR_PREFIX,
    TEXT_SOURCE_REF_KINDS,
    TOC_SOURCE_LOCATOR_PREFIX,
    UNASSIGNED_REASONS,
    UNRESOLVABLE_REF_KINDS,
    UNRESOLVED_EDGE_REASONS,
    PageLayout,
    DocumentOutline,
    OutlineNode,
    OutlineSpan,
    LayoutLine,
    LayoutPage,
    LayoutSpan,
    NavigationSynopsis,
    SynopsisSnippet,
    AspectNavigationEntry,
    AspectNavigationProfile,
    ReferenceEdge,
    ReferenceOccurrence,
    TocSource,
    ReferenceValidationContext,
    VerifiedReferences,
    TableCell,
    TableRow,
    TextAlignmentRecord,
    AlignmentRefusalRecord,
    alignment_closure_errors,
    compute_alignment_coverage,
    compute_alignment_residue_class,
    compute_alignment_verdict,
    compute_alignment_verdict_exact,
    validate_alignment_partition,
)

# TS3 生产构建核心：对齐（`aligner`）与标题树（`outline_builder`）。两者都建立在
# TS1 公共类型之上，且彼此单向依赖（`outline_builder` → `aligner` → `schema`），
# 因此在本模块末尾导入不存在部分初始化问题。
from document_structure import aligner, outline_builder  # noqa: E402

# TS3 收口轮（§八 / P1-F）：正式集合入口的**权威全集身份**类型。这三个类型与
# `EvidenceBlockInput` 同属"集合级输入契约"，因此按同一条版本轴登记
# （`EVIDENCE_SET_SNAPSHOT_VERSION` / `EVIDENCE_SET_GATEWAY_VERSION`），而不是
# wire-object 轴（本包只有 schema 层能定义 wire 对象）。
from document_structure.aligner import (  # noqa: E402
    EVIDENCE_SET_STATUSES,
    EVIDENCE_TYPE_CLOSED_SET,
    AlignmentError,
    EvidenceSetGateway,
    EvidenceSetMember,
    EvidenceSetSnapshot,
    align_evidence_set,
    assert_members_exact,
    snapshot_fingerprint,
    snapshot_from_gateway,
)

# TS4-A（§18.11）：记录层 8 个版本化顶层对象 + 唯一的正式组合根与正式复核层。
#
# 这里**只**导出公共 wire 类型与两个公共入口。**不**导出 provider / gateway /
# issuer / `_issue_pinned_*` / `_testing_*`：受信边界不是扩展点，把它们挂在包级
# API 上等于把"换一个 provider"变成一件看起来正常的事。
from document_structure import span_schema  # noqa: E402
from document_structure.span_schema import (  # noqa: E402
    BodyRangeDisposition,
    OutlineStructureSnapshot,
    SpanBuildSnapshot,
    SpanCitableCoverage,
    SpanConservation,
    SpanEvidenceComponent,
    SpanQualificationPolicy,
    SynopsisSourceValidation,
)
from document_structure import (  # noqa: E402
    evidence_gateway,
    layout_builder,
    span_builder,
    span_policy,
    span_verifier,
    synopsis,
)
from document_structure.span_builder import (  # noqa: E402
    SpanBuildError,
    build_span_snapshot,
    issue_live_ts3_handoff,
)
from document_structure.span_verifier import (  # noqa: E402
    SpanVerificationError,
    VerifiedSpanSnapshot,
    is_completion_eligible,
    verify_outline_structure_snapshot,
    verify_span_snapshot,
)

# TS5（§19.11.2）：current 导出指向 `to-4`。本包的 `TableObject` 名字**改指**
# `table_schema.TableObjectV4`（`table_schema` 里的 `TableObject` 就是它）。
#
# **legacy v3 入口**是显式的、只能显式拿：`document_structure.schema.TableObject`
# （`to-3` / `tb-1`，被类内固定常量校验）。这里再给一个直白别名
# `LegacyTableObject`，让"我想读历史 to-3"在一行里就看得出来；它**不得**进入
# final snapshot / capability，也不得被当作 current 解释（`classify_schema_version`
# 对 `to-3` 给出"已识别旧版、须重建"的专门结论）。
from document_structure import table_schema  # noqa: E402
from document_structure.table_schema import (  # noqa: E402
    FinalComponentBinding,
    FinalMaterialConservation,
    FinalMaterialStructureSnapshot,
    FinalOutlineSpan,
    TableCitableCoverage,
    TableObject,
    TableObjectV4,
    TableRangeDecision,
    TableRelation,
    TableStructureGap,
    upstream_dependency_fingerprint,
)
from document_structure import table_geometry, table_classification  # noqa: E402
from document_structure import table_builder  # noqa: E402
from document_structure import final_material_builder  # noqa: E402
from document_structure import final_verifier  # noqa: E402
from document_structure.final_material_builder import (  # noqa: E402
    build_final_material_snapshot,
)
from document_structure.final_verifier import (  # noqa: E402
    FinalVerificationError,
    VerifiedFinalMaterialStructureSnapshot,
    assert_final_material_capability,
    verify_final_material_snapshot,
)

#: `to-3` / `tb-1` 历史 reader：**只**供旧产物审计读回。
LegacyTableObject = schema.TableObject
LegacyTableRow = schema.TableRow
LegacyTableCell = schema.TableCell

__all__ = [
    "canonical",
    "versions",
    "schema",
    "aligner",
    "outline_builder",
    "span_schema",
    "span_policy",
    "span_builder",
    "span_verifier",
    "evidence_gateway",
    "layout_builder",
    "synopsis",
    "SchemaValidationError",
    "canonical_json",
    "canonical_text",
    "identity",
    "is_finite",
    "locator",
    "sha256_canonical",
    "to_json_value",
    "ALIGN_MIN",
    "FLOAT_PRECISION",
    "HEADING_QUALIFICATION_PROFILE_VERSION",
    "SPAN_CONFIDENCE_MIN",
    "TABLE_REGION_QUALIFICATION_VERSION",
    "VERSION_CONSTANTS",
    "VERSIONED_OBJECT_SCHEMA_FIELDS",
    "all_version_literals",
    "classify_schema_version",
    "legacy_versions",
    "ALIGNMENT_VERDICTS",
    "DEFERRED_TO_TS7A",
    "EDGE_ENDPOINT_KINDS",
    "EDGE_KINDS",
    "LOCATOR_PREFIXES",
    "OCCURRENCE_REQUIRED_EDGE_KINDS",
    "OUTLINE_OWNED_REF_KINDS",
    "OBJECT_BACKED_REF_KINDS",
    "RESOLVABLE_EDGE_KINDS",
    "TEXT_SOURCE_REF_KINDS",
    "TOC_SOURCE_LOCATOR_PREFIX",
    "UNRESOLVABLE_REF_KINDS",
    "PUBLIC_TYPE_NAMES",
    "PUBLIC_TYPES",
    "TABLE_LOCATOR_PREFIX",
    "UNASSIGNED_REASONS",
    "UNRESOLVED_EDGE_REASONS",
    "LayoutSpan",
    "LayoutLine",
    "LayoutPage",
    "PageLayout",
    "OutlineNode",
    "DocumentOutline",
    "SynopsisSnippet",
    "NavigationSynopsis",
    "AspectNavigationEntry",
    "AspectNavigationProfile",
    "TextAlignmentRecord",
    "AlignmentRefusalRecord",
    "OutlineSpan",
    "TableRow",
    "TableCell",
    "ReferenceEdge",
    "ReferenceOccurrence",
    "TocSource",
    "ReferenceValidationContext",
    "VerifiedReferences",
    "alignment_closure_errors",
    "compute_alignment_coverage",
    "compute_alignment_residue_class",
    "compute_alignment_verdict",
    "compute_alignment_verdict_exact",
    "validate_alignment_partition",
    "ALIGNMENT_REFUSAL_REASONS",
    "AlignmentError",
    "align_evidence_set",
    "assert_members_exact",
    "EVIDENCE_SET_STATUSES",
    "EVIDENCE_TYPE_CLOSED_SET",
    "EvidenceSetGateway",
    "EvidenceSetMember",
    "EvidenceSetSnapshot",
    "snapshot_fingerprint",
    "snapshot_from_gateway",
    "OutlineStructureSnapshot",
    "SpanQualificationPolicy",
    "BodyRangeDisposition",
    "SpanEvidenceComponent",
    "SpanCitableCoverage",
    "SpanConservation",
    "SpanBuildSnapshot",
    "SynopsisSourceValidation",
    "SpanBuildError",
    "SpanVerificationError",
    "VerifiedSpanSnapshot",
    "build_span_snapshot",
    "verify_span_snapshot",
    "verify_outline_structure_snapshot",
    "is_completion_eligible",
    "issue_live_ts3_handoff",
    "table_schema",
    "table_geometry",
    "table_classification",
    "table_builder",
    "final_material_builder",
    "final_verifier",
    "TableObject",
    "TableObjectV4",
    "FinalOutlineSpan",
    "TableRangeDecision",
    "TableRelation",
    "FinalComponentBinding",
    "TableCitableCoverage",
    "TableStructureGap",
    "FinalMaterialConservation",
    "FinalMaterialStructureSnapshot",
    "upstream_dependency_fingerprint",
    "build_final_material_snapshot",
    "verify_final_material_snapshot",
    "VerifiedFinalMaterialStructureSnapshot",
    "assert_final_material_capability",
    "FinalVerificationError",
    "LegacyTableObject",
    "LegacyTableRow",
    "LegacyTableCell",
]

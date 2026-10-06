"""TS4 span 记录层（`document_structure.span_schema`）的 T1–T4 确定性验收。

全部为纯离线检查：无网络 / 无数据库 / 无 LLM / 无 PDF 引擎 / 不依赖执行顺序。
本文件只读取被测对象的**公开行为**（是否抛错、身份是否变化、能否逐字节往返），
不采信实现自报的任何"结论字段"。

覆盖（计划 §18.4.3 / §18.4.4 / §18.13）：

T1 类型与版本登记
    1. 8 个新顶层类型与 `SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS` 严格双射，
       且每个类型的 `SCHEMA_CONSTANT` 与登记表逐项一致；
    2. 8 个 schema 版本常量的**精确名字**与**精确字面值**（`obs-1` … `nsv-1`），
       以及 21 个算法常量（`sb-8` / `sqpr-1` / `osp-1` … `vss-2`）逐个比对；
    3. 与既有 9 类型、以及 TS5 的 11 张表格记录类型的**并集**无重名、无漏项；
       `versions.self_check()` 与 `span_schema.self_check()` 的并集自检必须同时
       覆盖三张表；
    4. `current` / `legacy` / `unknown` 三分类均有反例：已登记旧值必须是
       `legacy`，形状非法或未登记的字面量**不得**被分类为 `current`；
    5. 冻结不变量：`SPAN_SCHEMA_VERSION == "os-4"`、`SPAN_BUILDER_VERSION == "sb-1"`、
       `TS4_BODY_SPAN_BUILDER_VERSION == "sb-8"`，且 `sb-2` / `sb-3` / `sb-4` /
       `sb-5` / `sb-6` / `sb-7` 已登记为 legacy；
    5b. §19.0.1 方案 C **两代简介、两条独立版本轴**：TS4
       `SYNOPSIS_SCHEMA_VERSION == "nss-2"` / `SYNOPSIS_VERSION == "ns-2"` 保持
       **current**（不是 legacy），而 TS5
       `FINAL_SYNOPSIS_SCHEMA_VERSION == "nss-3"` / `FINAL_SYNOPSIS_VERSION == "ns-3"`
       是另一条轴；两条轴的字面量不得互相被分类为 `current`；
    6. 运行时类型 / 子结构不得混入 wire 顶层登记表。

T2 `to_dict` / `from_dict` 严格性
    7. 8 个类型逐一：未知字段拒绝、跨类型字典混淆拒绝、非 dict 输入拒绝；
    8. 缺字段：**非空值**字段必须拒绝；**空值 / None** 字段允许"缺省 ≡ 空值"，
       但读回必须与显式空值完全等价（不得静默替换成语义不同的默认值）；
    9. 类型不符拒绝（每个类型至少一项）；
   10. 全部浮点叶子上出现 `NaN` / `+Inf` / `-Inf` 一律拒绝（含构造器入口）；
   11. `from_dict(to_dict(x))` 往返必须与原始规范形逐字节相等。

T3 身份确定性
   12. 同一输入重复派生 → 同一 `*_locator` / `*_id`（无时间 / 随机成分）；
   13. 任一已规范化字段变化 → revision 身份必变；
   14. 定位身份只随"是谁 / 在哪"变化（内容修订不改 locator）。

T4 词表
   15. **两套闭合词表**：TS4 `SYNOPSIS_REASON_CODES` 恰为 5 值（含
       `table_only_pending_ts5`），TS5 `FINAL_SYNOPSIS_REASON_CODES` 恰为 5 值
       （含 `table_material_available_no_text_synopsis`）；每套的值都必须能被
       **它自己那个类型**接受，且任一套都不得接受对方专属值；
   15b. **两条来源轴**：TS4 简介的 snippet 改成 final 身份（`fos-*`）后，来源复核
       必须拒绝（以真 `os-*` 来源作正例基线），与 final 侧「`os-*` 必须被拒」
       互为镜像；
   16. `SPAN_ROLES` / `UNASSIGNED_REASONS` / `ALIGNMENT_VERDICTS` / `TABLE_SCOPES`
       的值集与**本文件内冻结**的 TS1 / TS3 集合逐值相等（含顺序）；
   17. TS4 新增词表与冻结词表不得互相污染；A/B 阶段必须由
       `versions.SPAN_CONFIDENCE_MIN` 唯一派生。
"""

from __future__ import annotations

import copy
import hashlib
import re

from document_structure import outline_builder as OB
from document_structure import schema as S
from document_structure import span_schema as SS
from document_structure import synopsis as SY
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, canonical_json

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:
        text = str(e)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


# ---------------------------------------------------------------------------
# 0. 冻结期望值（全部定义在本文件内，不读取生产代码提供的任何"期望清单"）
# ---------------------------------------------------------------------------

#: §18.12.4：TS4 的 8 个 schema 版本常量（精确名字 → 精确字面值）。
_FROZEN_SPAN_SCHEMA_CONSTANTS = (
    ("OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION", "obs-1"),
    ("SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION", "sqp-1"),
    ("BODY_RANGE_DISPOSITION_SCHEMA_VERSION", "sps-1"),
    ("SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION", "spc-1"),
    ("SPAN_CITABLE_COVERAGE_SCHEMA_VERSION", "spv-1"),
    ("SPAN_CONSERVATION_SCHEMA_VERSION", "spr-1"),
    ("SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION", "spn-1"),
    ("SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION", "nsv-1"),
)

#: §18.12.4：TS4 的 21 个算法 / issuer 版本常量（精确名字 → 精确字面值）。
_FROZEN_SPAN_ALGORITHM_CONSTANTS = (
    ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-8"),
    ("OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION", "sb-1"),
    ("SPAN_QUALIFICATION_POLICY_VERSION", "sqpr-1"),
    ("OUTLINE_STRUCTURE_PROVIDER_VERSION", "osp-1"),
    ("EVIDENCE_GATEWAY_PROVIDER_VERSION", "egp-1"),
    ("ALIGNMENT_TERMINAL_PROVIDER_VERSION", "atp-1"),
    ("QUALIFICATION_POLICY_PROVIDER_VERSION", "qpp-1"),
    ("VERIFIED_PAGE_LAYOUT_ISSUER_VERSION", "vpli-1"),
    ("VERIFIED_ALIGNMENT_ISSUER_VERSION", "vai-1"),
    ("VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION", "vea-1"),
    ("VERIFIED_TS3_HANDOFF_VERSION", "vth-1"),
    ("PINNED_PAGE_LAYOUT_ISSUER_VERSION", "vplip-1"),
    ("PINNED_ALIGNMENT_ISSUER_VERSION", "vaip-1"),
    ("PINNED_EVIDENCE_AUTHORITY_VERSION", "veap-1"),
    ("PINNED_TS3_HANDOFF_VERSION", "vthp-1"),
    ("FIXTURE_PAGE_LAYOUT_ISSUER_VERSION", "vplif-1"),
    ("FIXTURE_ALIGNMENT_ISSUER_VERSION", "vaif-1"),
    ("FIXTURE_EVIDENCE_AUTHORITY_VERSION", "veaf-1"),
    ("FIXTURE_TS3_HANDOFF_VERSION", "vthf-1"),
    ("TESTING_TS3_HANDOFF_VERSION", "vtht-1"),
    ("VERIFIED_SPAN_SNAPSHOT_VERSION", "vss-2"),
)

#: §18.4.3：8 个新顶层类型名（顺序即声明顺序）。
_FROZEN_SPAN_RECORD_TYPES = (
    "OutlineStructureSnapshot",
    "SpanQualificationPolicy",
    "BodyRangeDisposition",
    "SpanEvidenceComponent",
    "SpanCitableCoverage",
    "SpanConservation",
    "SpanBuildSnapshot",
    "SynopsisSourceValidation",
)

#: 既有 9 项 wire 契约类型名（TS1 / TS3 冻结集）。
_FROZEN_LEGACY_RECORD_TYPES = (
    "PageLayout", "DocumentOutline", "OutlineSpan", "TableObject",
    "NavigationSynopsis", "TextAlignmentRecord", "AlignmentRefusalRecord",
    "AspectNavigationProfile", "ReferenceEdge",
)

#: §18.4.4 / TS1 / TS3 冻结值集：**不得**随 TS4 变化。
_FROZEN_SPAN_ROLES = ("body", "list", "table_caption", "table_note", "unassigned")
_FROZEN_UNASSIGNED_REASONS = (
    "toc_only_candidate", "no_heading_context", "boundary_ambiguous",
    "below_last_heading", "cross_heading_orphan", "outside_any_outline_node",
    "insufficient_heading_evidence", "numbered_list_ambiguity", "toc_unmatched",
    "bookmark_unmatched", "hierarchy_conflict",
)
_FROZEN_ALIGNMENT_VERDICTS = ("aligned", "partially_aligned", "unaligned")
_FROZEN_TABLE_SCOPES = ("inside_table", "adjacent_to_table", "none")
#: §18.4.4 / §18.12.3：TS4 `NavigationSynopsis`（`nss-2` / `ns-2`）的冻结词表。
#: §19.0.1 方案 C：**保持 5 值**，不因 TS5 落地而扩张。
_FROZEN_SYNOPSIS_REASON_CODES = (
    "no_span", "empty_text", "length_exceeded", "alignment_failed",
    "table_only_pending_ts5",
)

#: §19.10：TS5 `FinalNavigationSynopsis`（`nss-3` / `ns-3`）的独立冻结词表。
#:
#: 唯一与 TS4 不同的是最后一格：TS5 之后表格已是正式材料，"还在等 TS5"不再成立，
#: 取而代之的是"有正式表格材料，但该节点没有可引用段落能支撑正文简介"。
#: 两表各含一个对方**不允许**的值——这正是两个类型不能合并的证据。
_FROZEN_FINAL_SYNOPSIS_REASON_CODES = (
    "no_span", "empty_text", "length_exceeded", "alignment_failed",
    "table_material_available_no_text_synopsis",
)

#: "缺必填字段"必须被拒绝的**载荷关键字段**（定位 / 位置 / 成员 / 指纹）。
#: 其余字段由 `_test_t2_missing_fields` 的通用不变量逐键覆盖。
_MUST_REJECT_MISSING = {
    "OutlineStructureSnapshot": ("line_states", "counts", "document_id",
                                 "content_fingerprint", "outline_locator"),
    "SpanQualificationPolicy": ("factor_entries", "policy_fingerprint", "stage",
                                "policy_version"),
    "BodyRangeDisposition": ("span_id", "confidence", "left_boundary_cause",
                             "right_boundary_cause", "range_kind", "node_id"),
    "SpanEvidenceComponent": ("evidence_char_range", "admitted", "landing",
                              "span_id"),
    "SpanEvidenceComponent(refusal)": ("refusal_reason", "residue_class",
                                       "evidence_char_range", "landing"),
    "SpanCitableCoverage": ("citable_source_intervals", "covering_component_ids",
                            "span_local_length"),
    "SpanConservation": ("document_layer", "body_layer", "evidence_layer"),
    "SpanBuildSnapshot": ("spans", "coverages", "components", "trusted_input",
                          "terminal_count", "conservation"),
    "SynopsisSourceValidation": ("snippet_checks", "ok", "synopsis_id"),
}


# ---------------------------------------------------------------------------
# 1. 固定装置（全部满足不变量；不使用任何公司特例字段 / 固定业务页码或表号）
# ---------------------------------------------------------------------------

_DOC_ID = "doc-0001"
_DOC_SHA = hashlib.sha256("ts4-fixture-document".encode("utf-8")).hexdigest()
_DOC_VERSION = "sha256-" + _DOC_SHA[:16]
_PAGE_LAYOUT_ID = "pl-0123456789abcdef"
_OUTLINE_LOCATOR = "loc-do-0123456789abcdef"
_OUTLINE_ID = "do-0123456789abcdef"
_STRUCTURE_ID = "obs-0123456789abcdef"
_EVIDENCE_SET_VERSION = "set-0123456789ab"
_ALIGNMENT_ID = "al-0123456789abcdef"
_REFUSAL_ID = "alr-0123456789abcdef"
_TERMINAL_LOCATOR = "loc-al-0123456789abcdef"
_BLOCK_ID = "ev-block-0001"
_NODE_ID = "on-0123456789abcdef"
#: 方案 C 反例用的 **final span 身份**（`fos-`）。与 `test_tree_final_material`
#: 里那句「final 片段不得引用 `os-*`」逐字镜像：那边拿 `os-0123456789abcdef`，
#: 这边拿 `fos-0123456789abcdef`。本文件内冻结，不读生产代码的期望清单。
_FORGED_FINAL_SPAN_ID = "fos-0123456789abcdef"
_SPAN_TEXT = "营业收入本年为人民币一亿元"
_N = len(_SPAN_TEXT)
_BBOX = (50.0, 100.0, 150.0, 112.0)
_ANCHOR = (1, 2, _BBOX)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iv(start: int, end: int) -> SS.ClosedInterval:
    return SS.ClosedInterval(start=start, end=end)


def _make_span(span_builder_version: str | None = None):
    """TS4 正文 span（`sb-7`）：最小但完整的一条已归属正文材料。"""
    return S.OutlineSpan.create(
        document_outline_locator=_OUTLINE_LOCATOR, node_id=_NODE_ID,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_ANCHOR, end_anchor=_ANCHOR, normalized_text=_SPAN_TEXT,
        layout_line_refs=((1, 2),), confidence=1.0,
        span_builder_version=span_builder_version or V.TS4_BODY_SPAN_BUILDER_VERSION)


def _policy_stage() -> str:
    return ("distribution_only" if V.SPAN_CONFIDENCE_MIN is None
            else "threshold_enabled")


def _policy_factor_entries():
    """12 个 `(side, cause)` 边界因子；穷尽性与顺序由策略自身强制。"""
    entries = [SS.BoundaryFactor(side="left", cause=cause, factor=0.25)
               for cause in SS.BOUNDARY_CAUSES_LEFT]
    entries += [SS.BoundaryFactor(side="right", cause=cause, factor=0.5)
                for cause in SS.BOUNDARY_CAUSES_RIGHT]
    return tuple(entries)


def _policy_kwargs(**overrides):
    """合格策略的构造器入参（阶段与 completion 一律由 `SPAN_CONFIDENCE_MIN` 派生）。"""
    stage = _policy_stage()
    kwargs = dict(
        policy_key="span-qualification-ts4a", stage=stage,
        span_confidence_min=V.SPAN_CONFIDENCE_MIN,
        factor_entries=_policy_factor_entries(),
        completion_enabled=(stage != "distribution_only"),
        set_complete_supported=(stage != "distribution_only"),
        max_snippets_per_node=3, max_snippet_chars=200, min_snippet_chars=20,
        max_total_snippet_chars=300, sentence_terminators=("。", "；"),
        closing_quotes=("”",))
    kwargs.update(overrides)
    return kwargs


def _make_policy(**overrides):
    return SS.SpanQualificationPolicy.create(**_policy_kwargs(**overrides))


def _make_line_state(**overrides):
    kwargs = dict(
        page_number=1, line_index=2, state="heading_node", node_id=_NODE_ID,
        body_attachment=None, reason_code="heading_numbering")
    kwargs.update(overrides)
    kwargs.setdefault("line_identity", SS.derive_line_identity(
        document_version=_DOC_VERSION, page_number=kwargs["page_number"],
        line_index=kwargs["line_index"], text=_SPAN_TEXT, bbox=_BBOX))
    return SS.LineStructureState(**kwargs)


def _make_structure_snapshot(**overrides):
    kwargs = dict(
        outline_algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        heading_profile_version=V.HEADING_QUALIFICATION_PROFILE_VERSION,
        table_region_version=V.TABLE_REGION_QUALIFICATION_VERSION,
        toc_reconciliation_version=V.TOC_BODY_RECONCILIATION_VERSION,
        normalization_version=V.NORMALIZATION_VERSION,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        page_layout_id=_PAGE_LAYOUT_ID, outline_locator=_OUTLINE_LOCATOR,
        outline_id=_OUTLINE_ID, line_states=(_make_line_state(),))
    kwargs.update(overrides)
    return SS.OutlineStructureSnapshot.create(**kwargs)


def _disposition_kwargs(span_id, **overrides):
    """`regular` 极大正文范围：边界成因、confidence 与 span 绑定齐全。"""
    kwargs = dict(
        range_kind="regular", node_id=_NODE_ID, unassigned_reason=None,
        table_scope=None, table_reason=None, start_page=1, start_line=2,
        end_page=1, end_line=2, line_count=1, tight_char_count=_N,
        left_boundary_cause="preceding_heading", right_boundary_cause="document_end",
        confidence=0.9, span_id=span_id)
    kwargs.update(overrides)
    return kwargs


def _component_kwargs(span_id, **overrides):
    """准入的正文落点组件：`admitted=True` 当且仅当 `aligned_projected`。

    按 §18.13 的"最小合法实例"要求，此处 `layout_hits` 取空。逐段落点坐标的
    生产行为不在本文件（T1–T4）的范围内，由 `test_tree_span_coords.py` 覆盖。
    """
    kwargs = dict(
        evidence_block_id=_BLOCK_ID, terminal_kind="alignment",
        terminal_id=_ALIGNMENT_ID, verdict="aligned",
        evidence_char_range=(0, _N), landing="body_span", admitted=True,
        admission_reason="aligned_projected", span_local_char_range=(0, _N),
        node_id=_NODE_ID, span_id=span_id, layout_hits=())
    kwargs.update(overrides)
    return kwargs


def _refusal_component_kwargs(**overrides):
    """拒绝终态 + residue 落点：同时覆盖两条真值表分支。"""
    kwargs = dict(
        evidence_block_id=_BLOCK_ID, terminal_kind="refusal",
        terminal_id=_REFUSAL_ID, verdict="refused",
        refusal_reason="quantization_boundary_refused", residue_class="unexplained",
        evidence_char_range=(0, 1), landing="alignment_residue_unmapped",
        admitted=False, admission_reason="refusal_record")
    kwargs.update(overrides)
    return kwargs


def _coverage_kwargs(span_id, component_ids, **overrides):
    kwargs = dict(
        span_id=span_id, span_local_length=_N, normalization_only_intervals=(),
        citable_source_intervals=((0, _N),), non_citable_source_intervals=(),
        uncovered_source_intervals=(), covering_component_ids=component_ids)
    kwargs.update(overrides)
    return kwargs


def _evidence_row(split=False):
    """单条 Evidence 在 `[0, block_char_length)` 上的三分法（必须 tile 全域）。"""
    if split:
        mapped = (_iv(0, _N - 1),)
        unverifiable = (_iv(_N - 1, _N),)
        landing_chars = _N - 1
    else:
        mapped, unverifiable, landing_chars = (_iv(0, _N),), (), _N
    return SS.EvidenceConservationRow(
        evidence_id=_BLOCK_ID, block_char_length=_N, mapped_intervals=mapped,
        unverifiable_intervals=unverifiable, residue_intervals=(),
        landing_counts=(SS.LandingCount(landing="body_span", segment_count=1,
                                        char_count=landing_chars),),
        conserved=True, problems=())


def _make_conservation(split=False):
    """三层守恒：文档层四桶 / 正文层五桶 / Evidence 层逐条。"""
    document_layer = (
        SS.LayerBucket(bucket="heading", line_count=0, tight_char_count=0),
        SS.LayerBucket(bucket="formal_unassigned", line_count=0, tight_char_count=0),
        SS.LayerBucket(bucket="non_content", line_count=0, tight_char_count=0),
        SS.LayerBucket(bucket="body", line_count=1, tight_char_count=_N),
    )
    body_layer = (
        SS.LayerBucket(bucket="regular", line_count=1, tight_char_count=_N),
        SS.LayerBucket(bucket="table_adjacency_provisional", line_count=0,
                       tight_char_count=0),
        SS.LayerBucket(bucket="table_inside", line_count=0, tight_char_count=0),
        SS.LayerBucket(bucket="unassigned", line_count=0, tight_char_count=0),
        SS.LayerBucket(bucket="empty", line_count=0, tight_char_count=0),
    )
    return SS.SpanConservation.create(
        document_layer=document_layer, body_layer=body_layer,
        evidence_layer=(_evidence_row(split=split),))


def _make_synopsis(span_id):
    return S.NavigationSynopsis.available(node_id=_NODE_ID, snippets=(
        S.SynopsisSnippet(span_id=span_id, snippet_index=0, char_start=0,
                          char_end=_N, text=_SPAN_TEXT),))


def _make_validation(span_id, **overrides):
    kwargs = dict(
        snippet_index=0, span_id=span_id, snippet_char_range=(0, _N),
        snippet_source_hash=_sha(_SPAN_TEXT), covering_effective_interval=(0, _N),
        ok=True, problems=())
    kwargs.update(overrides)
    return SS.SynopsisSourceValidation.create(
        node_id=_NODE_ID, synopsis_id=_make_synopsis(span_id).synopsis_id,
        snippet_checks=(SS.SnippetCheck(**kwargs),))


def _make_trusted_input(policy):
    """§18.3.5 的封闭输入载荷：字段数与顺序由规格钉死，任何偏离都在构造期拒绝。"""
    factors = tuple(x for bf in policy.factor_entries
                    for x in (bf.side, bf.cause, bf.factor))
    return SS.TrustedBuildInput(
        page_layout=(_PAGE_LAYOUT_ID, _DOC_ID, _DOC_VERSION, V.LAYOUT_ENGINE,
                     V.LAYOUT_ENGINE_VERSION, V.LAYOUT_SCHEMA_VERSION),
        outline=(_OUTLINE_LOCATOR, _OUTLINE_ID, _DOC_ID, _DOC_VERSION,
                 V.OUTLINE_SCHEMA_VERSION, V.OUTLINE_ALGORITHM_VERSION,
                 V.HEADING_QUALIFICATION_PROFILE_VERSION, 1),
        structure=(_STRUCTURE_ID, V.OUTLINE_ALGORITHM_VERSION,
                   V.HEADING_QUALIFICATION_PROFILE_VERSION,
                   V.TABLE_REGION_QUALIFICATION_VERSION,
                   V.NORMALIZATION_VERSION),
        evidence=(_BLOCK_ID, _N, _DOC_SHA, _EVIDENCE_SET_VERSION,
                  _EVIDENCE_SET_VERSION, 1, ()),
        terminals=((1, _ALIGNMENT_ID, _TERMINAL_LOCATOR, V.ALIGN_SCHEMA_VERSION,
                    "aligned", 1.0, 0, _N),),
        qualification=(policy.policy_key, policy.policy_id, policy.policy_version,
                       policy.stage, policy.span_confidence_min, factors,
                       policy.completion_enabled, policy.set_complete_supported,
                       policy.max_snippets_per_node, policy.max_snippet_chars,
                       policy.min_snippet_chars, policy.max_total_snippet_chars,
                       policy.policy_fingerprint),
        rules=(V.SPAN_QUALIFICATION_POLICY_VERSION, V.OUTLINE_ALGORITHM_VERSION,
               V.HEADING_QUALIFICATION_PROFILE_VERSION,
               V.TABLE_REGION_QUALIFICATION_VERSION, V.NORMALIZATION_VERSION),
        handoff=(V.VERIFIED_TS3_HANDOFF_VERSION, "handoff-0001", _DOC_ID,
                 _DOC_VERSION, _PAGE_LAYOUT_ID, _OUTLINE_ID, _BLOCK_ID,
                 _ALIGNMENT_ID, 1, 1))


def _make_snapshot(**overrides):
    policy = overrides.pop("qualification_policy", None) or _make_policy()
    span = overrides.pop("span", None) or _make_span()
    component = overrides.pop("component", None) or SS.SpanEvidenceComponent.create(
        **_component_kwargs(span.span_id))
    coverages = overrides.pop("coverages", None)
    if coverages is None:
        coverages = (SS.SpanCitableCoverage.create(
            **_coverage_kwargs(span.span_id, (component.component_id,))),)
    kwargs = dict(
        qualification_policy=policy, document_id=_DOC_ID,
        document_version=_DOC_VERSION, page_layout_id=_PAGE_LAYOUT_ID,
        outline_id=_OUTLINE_ID, alignment_schema_version=V.ALIGN_SCHEMA_VERSION,
        alignment_id=_ALIGNMENT_ID, structure_snapshot_id=_STRUCTURE_ID,
        trusted_input=_make_trusted_input(policy), dispositions=(
            SS.BodyRangeDisposition.create(**_disposition_kwargs(span.span_id)),),
        spans=(span,), inherited_unassigned_span_ids=("os-0123456789abcdef",),
        components=(component,), coverages=coverages,
        conservation=_make_conservation(),
        synopses=(_make_synopsis(span.span_id),), terminal_count=1)
    kwargs.update(overrides)
    return SS.SpanBuildSnapshot.create(**kwargs)


_FIXTURE_CACHE: dict = {}


def _fixtures():
    """8 个顶层类型的**最小但非平凡**合法实例（模块级缓存，只构造一次）。"""
    if not _FIXTURE_CACHE:
        span = _make_span()
        admitted = SS.SpanEvidenceComponent.create(**_component_kwargs(span.span_id))
        refusal = SS.SpanEvidenceComponent.create(**_refusal_component_kwargs())
        _FIXTURE_CACHE.update({
            "OutlineStructureSnapshot": _make_structure_snapshot(),
            "SpanQualificationPolicy": _make_policy(),
            "BodyRangeDisposition": SS.BodyRangeDisposition.create(
                **_disposition_kwargs(span.span_id)),
            "SpanEvidenceComponent": admitted,
            "SpanEvidenceComponent(refusal)": refusal,
            "SpanCitableCoverage": SS.SpanCitableCoverage.create(
                **_coverage_kwargs(span.span_id, (admitted.component_id,))),
            "SpanConservation": _make_conservation(),
            "SpanBuildSnapshot": _make_snapshot(),
            "SynopsisSourceValidation": _make_validation(span.span_id),
        })
    return _FIXTURE_CACHE


def _without(d: dict, key: str) -> dict:
    d2 = copy.deepcopy(d)
    d2.pop(key, None)
    return d2


def _is_empty_value(v) -> bool:
    """`None` 或空容器 —— 只有这类值才可能"缺省 ≡ 显式空值"。"""
    return v is None or (isinstance(v, (list, tuple, dict)) and len(v) == 0)


def _float_paths(node, prefix=()):
    """规范形里所有浮点叶子的键路径（用于 NaN / ±Inf 注入）。"""
    out = []
    if isinstance(node, dict):
        for k in sorted(node):
            out.extend(_float_paths(node[k], prefix + (k,)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_float_paths(v, prefix + (i,)))
    elif isinstance(node, float):
        out.append(prefix)
    return out


def _set_path(node, path, value):
    cur = node
    for step in path[:-1]:
        cur = cur[step]
    cur[path[-1]] = value


# ---------------------------------------------------------------------------
# T1 类型与版本登记
# ---------------------------------------------------------------------------


def _test_t1_registry_bijection():
    check(tuple(SS.SPAN_RECORD_TYPES) == _FROZEN_SPAN_RECORD_TYPES,
          f"8 个 TS4 顶层类型必须恰为冻结名字集，得到 {tuple(SS.SPAN_RECORD_TYPES)}")
    check(len(SS.SPAN_RECORD_TYPES) == 8,
          f"TS4 顶层类型必须恰为 8 个，得到 {len(SS.SPAN_RECORD_TYPES)}")
    check(tuple(V.SPAN_RECORD_PUBLIC_TYPES) == _FROZEN_SPAN_RECORD_TYPES,
          f"versions.SPAN_RECORD_PUBLIC_TYPES 必须与冻结类型集一致，"
          f"得到 {tuple(V.SPAN_RECORD_PUBLIC_TYPES)}")

    table = V.SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS
    check(set(table) == set(_FROZEN_SPAN_RECORD_TYPES),
          f"登记表的类型集必须恰为 8 个冻结名字，得到 {sorted(table)}")
    declared_constants = set()
    for type_name in _FROZEN_SPAN_RECORD_TYPES:
        cls = SS.SPAN_RECORD_TYPES[type_name]
        pair = table.get(type_name)
        if not check(pair == ("schema_version", cls.SCHEMA_CONSTANT),
                     f"{type_name} 在登记表中必须为 ('schema_version', "
                     f"{cls.SCHEMA_CONSTANT!r})，得到 {pair!r}"):
            continue
        check("schema_version" in cls.__dataclass_fields__,
              f"{type_name} 必须声明 schema_version 字段")
        check(all(callable(getattr(cls, meth, None))
                  for meth in ("to_dict", "from_dict", "create")),
              f"{type_name} 必须同时提供 to_dict/from_dict/create")
        declared_constants.add(pair[1])
    check(len(declared_constants) == 8,
          f"8 个类型的 schema 版本常量必须两两不同，得到 {len(declared_constants)}")
    check(sorted(declared_constants)
          == sorted(c for c, _v in _FROZEN_SPAN_SCHEMA_CONSTANTS),
          "类型登记的 8 个常量名必须与冻结清单逐名相等")

    # 精确常量名 ↔ 精确字面值。
    for constant_name, literal in _FROZEN_SPAN_SCHEMA_CONSTANTS:
        check(getattr(V, constant_name, None) == literal,
              f"versions.{constant_name} 必须恰为 {literal!r}，"
              f"得到 {getattr(V, constant_name, None)!r}")
        check(V.VERSION_CONSTANTS.get(constant_name) == literal,
              f"VERSION_CONSTANTS[{constant_name}] 必须恰为 {literal!r}")
    frozen_alg_names = tuple(n for n, _v in _FROZEN_SPAN_ALGORITHM_CONSTANTS)
    check(tuple(V.SPAN_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES) == frozen_alg_names,
          "SPAN_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES 必须恰为冻结的 21 个名字且按序")
    for constant_name, literal in _FROZEN_SPAN_ALGORITHM_CONSTANTS:
        check(getattr(V, constant_name, None) == literal,
              f"versions.{constant_name} 必须恰为 {literal!r}，"
              f"得到 {getattr(V, constant_name, None)!r}")
        check(V.VERSION_CONSTANTS.get(constant_name) == literal,
              f"VERSION_CONSTANTS[{constant_name}] 必须恰为 {literal!r}")
        check(constant_name in V.SPAN_RECORD_MANDATED_VERSION_CONSTANT_NAMES,
              f"{constant_name} 必须进入 TS4 强制登记清单")
    check(set(frozen_alg_names) | {c for c, _v in _FROZEN_SPAN_SCHEMA_CONSTANTS}
          <= set(V.SPAN_RECORD_MANDATED_VERSION_CONSTANT_NAMES),
          "TS4 的 schema + 算法常量必须全部出现在强制清单里")

    # 运行时能力对象 / 子结构不得混入 wire 顶层登记表。
    for type_name in SS.RUNTIME_ONLY_TYPE_NAMES:
        check(type_name not in SS.SPAN_RECORD_TYPES,
              f"运行时类型 {type_name} 不得登记为 wire 顶层类型")
        check(type_name not in SS.SUBSTRUCTURE_REGISTRY,
              f"运行时类型 {type_name} 不得登记为子结构")
        cls = getattr(SS, type_name, None)
        if cls is not None and hasattr(cls, "__dataclass_fields__"):
            check("schema_version" not in cls.__dataclass_fields__,
                  f"运行时类型 {type_name} 不得自带 schema_version")
    for type_name in V.SPAN_RECORD_SUBSTRUCTURE_TYPE_NAMES:
        check(type_name in SS.SUBSTRUCTURE_TYPES,
              f"子结构 {type_name} 必须在 span_schema.SUBSTRUCTURE_TYPES 内")
        check(type_name not in SS.SPAN_RECORD_TYPES,
              f"子结构 {type_name} 不得被登记为 wire 顶层类型")
    check(set(SS.SUBSTRUCTURE_REGISTRY) == set(SS.SUBSTRUCTURE_TYPES),
          "SUBSTRUCTURE_REGISTRY 的键必须恰为 SUBSTRUCTURE_TYPES")
    for type_name in SS.SUBSTRUCTURE_TYPES:
        check("schema_version" not in getattr(
            SS.SUBSTRUCTURE_REGISTRY[type_name], "__dataclass_fields__", {}),
            f"子结构 {type_name} 不得自带 schema_version（版本由父对象携带）")


def _test_t1_union_coverage():
    legacy = V.VERSIONED_OBJECT_SCHEMA_FIELDS
    ts4 = V.SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS
    ts5 = V.TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS
    union = V.ALL_VERSIONED_OBJECT_SCHEMA_FIELDS
    tables = (legacy, ts4, ts5)

    check(tuple(legacy) == _FROZEN_LEGACY_RECORD_TYPES,
          f"既有 9 项 wire 契约表不得增删换序，得到 {tuple(legacy)}")
    check(set(legacy) & set(ts4) == set(),
          f"两张登记表不得重名，交集为 {sorted(set(legacy) & set(ts4))}")
    check(not (set(legacy) & set(ts5)) and not (set(ts4) & set(ts5)),
          "TS5 表不得与既有两张表重名，交集为 "
          f"{sorted((set(legacy) | set(ts4)) & set(ts5))}")
    check(len(union) == len(legacy) + len(ts4) + len(ts5),
          f"并集必须无重名合并"
          f"（{len(union)} != {len(legacy)}+{len(ts4)}+{len(ts5)}）")
    check(set(union) == set(legacy) | set(ts4) | set(ts5),
          "并集不得漏项：必须恰为三张表的名字并集")
    check(len(union) == 28, f"项目级 wire 类型并集必须为 28 项，得到 {len(union)}")
    for type_name, pair in union.items():
        check(any(pair == table.get(type_name) for table in tables),
              f"{type_name} 在并集中的 (字段, 常量) 必须来自其原表")
    # §19.0.1 方案 C：TS4 简介与 final 简介必须是并集里的**两个**类型，
    # 而不是一个类型 + 一条历史通道。
    check("NavigationSynopsis" in legacy
          and legacy["NavigationSynopsis"][1] == "SYNOPSIS_SCHEMA_VERSION",
          "TS4 NavigationSynopsis 必须继续登记在既有契约表上，并走 "
          "SYNOPSIS_SCHEMA_VERSION 轴")
    check("FinalNavigationSynopsis" in ts5
          and ts5["FinalNavigationSynopsis"][1] == "FINAL_SYNOPSIS_SCHEMA_VERSION",
          "FinalNavigationSynopsis 必须登记在 TS5 表上，并走 "
          "FINAL_SYNOPSIS_SCHEMA_VERSION 轴（两个类型、两条独立版本轴）")

    all_schema_names = V.ALL_SCHEMA_VERSION_CONSTANT_NAMES
    # 27 而非 28：`TABLE_SCHEMA_VERSION` 同时是 TS1 的 `TableObject`（历史 to-3
    # reader）与 TS5 的 `TableObjectV4`（当前 to-4）的版本常量名。同一个版本轴由
    # 两个 wire 类型共享，是设计而非漏登记——它保证 to-3 与 to-4 不会各持一套版本。
    check(len(all_schema_names) == 27,
          f"项目级 schema 版本常量名必须为 27 项，得到 {len(all_schema_names)}")
    check(len(set(all_schema_names)) == len(all_schema_names),
          "项目级 schema 版本常量名不得重复")
    check(set(all_schema_names)
          == {name for name, _lit in _FROZEN_SPAN_SCHEMA_CONSTANTS}
          | {legacy[t][1] for t in legacy}
          | {ts5[t][1] for t in ts5},
          "项目级 schema 版本常量名必须恰为既有 9 + TS4 8 + TS5 11（去重后 27）")
    # 两条简介版本轴必须是两条**独立**常量，且都进入项目级登记。
    for _name, _lit in (("SYNOPSIS_SCHEMA_VERSION", V.SYNOPSIS_SCHEMA_VERSION),
                        ("FINAL_SYNOPSIS_SCHEMA_VERSION",
                         V.FINAL_SYNOPSIS_SCHEMA_VERSION)):
        check(_name in all_schema_names and _lit in V.VERSION_CONSTANTS.values(),
              f"{_name}={_lit!r} 必须进入项目级 schema 版本常量登记")

    # 并集自检必须真的同时覆盖三张表。
    check(V.check_version_registry(tuple(union), union) == (),
          "versions.check_version_registry 对并集必须无问题")
    vs = V.self_check()
    check(vs["registry_problems"] == [],
          f"versions.self_check 的并集自检必须无问题，得到 {vs['registry_problems']}")
    check(vs["span_record_public_type_count"] == 8
          and vs["table_record_public_type_count"] == 11
          and vs["all_versioned_object_count"] == 28,
          "versions.self_check 必须自报并集覆盖（TS4 8 / TS5 11 / 总 28），"
          f"得到 {vs['span_record_public_type_count']} / "
          f"{vs['table_record_public_type_count']} / "
          f"{vs['all_versioned_object_count']}")
    check(vs["all_schema_version_constant_count"] == 27,
          f"versions.self_check 必须自报 27 个项目级 schema 版本常量，"
          f"得到 {vs.get('all_schema_version_constant_count')}")
    check(vs["missing_mandated"] == [] and vs["malformed_literals"] == [],
          f"并集强制清单必须完整且字面量形状合法，"
          f"缺失={vs['missing_mandated']}，非法={vs['malformed_literals']}")
    check(vs["all_mandated_count"]
          == len(V.MANDATED_VERSION_CONSTANT_NAMES) + 29 + 19,
          "TS4 在既有 12 项之外再强制登记 8 + 21 = 29 项，TS5 再追加 19 项"
          "（11 个 wire + 8 个算法），"
          f"得到 {vs['all_mandated_count']} vs "
          f"{len(V.MANDATED_VERSION_CONSTANT_NAMES)} + 29 + 19")
    check(len(V.TABLE_RECORD_MANDATED_VERSION_CONSTANT_NAMES) == 19
          and set(V.TABLE_RECORD_MANDATED_VERSION_CONSTANT_NAMES)
          <= set(V.ALL_MANDATED_VERSION_CONSTANT_NAMES),
          "TS5 的 19 项强制常量必须全部进入项目级强制清单")

    sc = SS.self_check()
    check(sc["problems"] == [], f"span_schema 自检必须无问题，得到 {sc['problems']}")
    check(sc["span_record_type_count"] == 8,
          f"自检必须自报 8 个顶层类型，得到 {sc['span_record_type_count']}")
    check(sc["boundary_cause_count"] == 12,
          f"边界成因必须恰为 12 项（5 左 + 7 右），得到 {sc['boundary_cause_count']}")
    check(sc["component_landing_count"] == 11,
          f"组件落点必须恰为 11 项，得到 {sc['component_landing_count']}")


def _test_t1_version_classification():
    for constant_name, literal in _FROZEN_SPAN_SCHEMA_CONSTANTS:
        check(V.classify_schema_version(constant_name, literal) == "current",
              f"{constant_name}={literal} 必须分类为 current")
    for constant_name, literal in _FROZEN_SPAN_ALGORITHM_CONSTANTS:
        check(V.classify_schema_version(constant_name, literal) == "current",
              f"{constant_name}={literal} 必须分类为 current")

    # 冻结不变量。
    check(V.SPAN_SCHEMA_VERSION == "os-4",
          f"SPAN_SCHEMA_VERSION 必须冻结为 os-4，得到 {V.SPAN_SCHEMA_VERSION!r}")
    check(V.SPAN_BUILDER_VERSION == "sb-1",
          f"SPAN_BUILDER_VERSION 必须保持 sb-1，得到 {V.SPAN_BUILDER_VERSION!r}")
    check(V.TS4_BODY_SPAN_BUILDER_VERSION == "sb-8",
          "TS4 正文算法版本必须是独立的 sb-8（跨页 `table_inside` 在构造期按页分段成"
          "逐页的单页 `BodyRangeDisposition`；`frozen_range_bbox` 的单页不变式未放宽）")
    check(V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS
          == ("sb-2", "sb-3", "sb-4", "sb-5", "sb-6", "sb-7"),
          f"sb-2 / sb-3 / sb-4 / sb-5 / sb-6 / sb-7 必须登记为 TS4 正文算法的 legacy 值，"
          f"得到 {V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS!r}")
    check(V.legacy_versions("TS4_BODY_SPAN_BUILDER_VERSION")
          == ("sb-2", "sb-3", "sb-4", "sb-5", "sb-6", "sb-7"),
          f"sb-2 / sb-3 / sb-4 / sb-5 / sb-6 / sb-7 必须由 legacy_versions 报告为历史值，"
          f"得到 {V.legacy_versions('TS4_BODY_SPAN_BUILDER_VERSION')}")
    check(V.VERIFIED_SPAN_SNAPSHOT_VERSION == "vss-2",
          f"完成判据语义变更必须由复核层版本承载，"
          f"得到 {V.VERIFIED_SPAN_SNAPSHOT_VERSION!r}")
    check(V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION == V.SPAN_BUILDER_VERSION,
          "TS3 unassigned 与历史树重建必须继续使用 sb-1")
    # §19.0.1 方案 C：`nss-2` / `ns-2` 是 TS4 的 **current** 版本轴，**不升版**。
    # TS5 的 final 简介是另一个类型，走另一条**独立**版本轴 `nss-3` / `ns-3`；
    # 因此 `nss-2` / `ns-2` 既不是 TS4 的历史值，也不是 TS5 的 current 值。
    check(V.SYNOPSIS_SCHEMA_VERSION == "nss-2",
          f"SYNOPSIS_SCHEMA_VERSION 必须保持冻结的 nss-2（TS4 的 current 轴），"
          f"得到 {V.SYNOPSIS_SCHEMA_VERSION!r}")
    check(V.SYNOPSIS_VERSION == "ns-2",
          f"SYNOPSIS_VERSION 必须保持冻结的 ns-2（TS4 的 current 轴），"
          f"得到 {V.SYNOPSIS_VERSION!r}")
    check(V.legacy_versions("SYNOPSIS_VERSION") == ("ns-1",),
          f"只有 ns-1 是 TS4 简介算法轴的历史值（ns-2 是 current），"
          f"得到 {V.legacy_versions('SYNOPSIS_VERSION')}")
    check(V.legacy_versions("SYNOPSIS_SCHEMA_VERSION") == ("nss-1",),
          f"只有 nss-1 是 TS4 简介 wire 轴的历史值（nss-2 是 current），"
          f"得到 {V.legacy_versions('SYNOPSIS_SCHEMA_VERSION')}")
    # TS5 final 轴：独立常量、独立字面量、且与 TS4 轴不相交。
    check(V.FINAL_SYNOPSIS_SCHEMA_VERSION == "nss-3",
          f"FINAL_SYNOPSIS_SCHEMA_VERSION 必须为 nss-3，"
          f"得到 {V.FINAL_SYNOPSIS_SCHEMA_VERSION!r}")
    check(V.FINAL_SYNOPSIS_VERSION == "ns-3",
          f"FINAL_SYNOPSIS_VERSION 必须为 ns-3，"
          f"得到 {V.FINAL_SYNOPSIS_VERSION!r}")
    check(V.FINAL_SYNOPSIS_SCHEMA_VERSION != V.SYNOPSIS_SCHEMA_VERSION,
          "TS4 与 final 的 wire 轴不得共用同一个字面量")
    check(V.FINAL_SYNOPSIS_VERSION != V.SYNOPSIS_VERSION,
          "TS4 与 final 的算法轴不得共用同一个字面量")
    check(V.classify_schema_version("FINAL_SYNOPSIS_SCHEMA_VERSION", "nss-2")
          != "current",
          "TS4 的 nss-2 不得被分类为 final wire 轴的 current")
    check(V.classify_schema_version("FINAL_SYNOPSIS_VERSION", "ns-2")
          != "current",
          "TS4 的 ns-2 不得被分类为 final 算法轴的 current")
    check(V.classify_schema_version("SYNOPSIS_SCHEMA_VERSION", "nss-3")
          != "current",
          "final 的 nss-3 不得被分类为 TS4 wire 轴的 current")
    check(V.classify_schema_version("SYNOPSIS_VERSION", "ns-3") != "current",
          "final 的 ns-3 不得被分类为 TS4 算法轴的 current")
    check(not hasattr(V, "LEGACY_SYNOPSIS_SCHEMA_VERSION")
          and not hasattr(V, "LEGACY_SYNOPSIS_VERSION"),
          "不得存在 LEGACY_SYNOPSIS_* 常量：nss-2/ns-2 是 current 而非历史")
    check("sb-1" not in V.legacy_versions("SPAN_BUILDER_VERSION"),
          "sb-1 不是失效 legacy（TS3 unassigned 仍以它为准）")

    # legacy 反例：已登记旧值必须是 legacy，且绝不与 current 共用取值。
    legacy_cases = (
        ("SYNOPSIS_VERSION", "ns-1"),
        ("SYNOPSIS_SCHEMA_VERSION", "nss-1"),
        ("SPAN_SCHEMA_VERSION", "os-1"),
        ("ALIGNER_VERSION", "al-1"),
        ("LAYOUT_SCHEMA_VERSION", "pl-1"),
        ("OUTLINE_ALGORITHM_VERSION", "oa-2"),
        ("HEADING_QUALIFICATION_PROFILE_VERSION", "hq-3"),
        ("TABLE_REGION_QUALIFICATION_VERSION", "trg-1"),
        ("TOC_BODY_RECONCILIATION_VERSION", "tocr-1"),
        ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-2"),
        ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-3"),
        ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-4"),
        ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-5"),
        ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-6"),
        ("TS4_BODY_SPAN_BUILDER_VERSION", "sb-7"),
    )
    for constant_name, value in legacy_cases:
        check(V.classify_schema_version(constant_name, value) == "legacy",
              f"{constant_name}={value} 必须被识别为 legacy，"
              f"得到 {V.classify_schema_version(constant_name, value)!r}")
        check(value in V.legacy_versions(constant_name),
              f"{constant_name} 的历史清单必须包含 {value}")

    # 形状非法 / 未登记字面量：绝不允许被当成 current。
    bad_literals = ("", "os", "os-", "-1", "OS-4", "os-1234", "nope",
                    "ns-1 ", " ns-1", "obs-99", None, 1, 1.0, True)
    for constant_name, _literal in _FROZEN_SPAN_SCHEMA_CONSTANTS:
        for bad in bad_literals:
            kind = V.classify_schema_version(constant_name, bad)
            check(kind != "current",
                  f"{constant_name} 的非法/未登记值 {bad!r} 不得被分类为 current"
                  f"（得到 {kind!r}）")
    for bad in bad_literals:
        check(V.classify_schema_version("SPAN_SCHEMA_VERSION", bad) != "current",
              f"SPAN_SCHEMA_VERSION 的非法值 {bad!r} 不得被分类为 current")
    check(V.classify_schema_version("SPAN_SCHEMA_VERSION", "os-99") == "unknown",
          "形状合法但未登记的字面量必须分类为 unknown")

    # 字面量形状约束本身必须真的会拒绝非法形状。
    pattern = re.compile(V.VERSION_LITERAL_PATTERN)
    for literal in ("obs-1", "nsv-1", "vtht-1", "sb-2", "qpp-1", "os-4"):
        check(pattern.match(literal) is not None,
              f"合法版本字面量 {literal!r} 必须匹配冻结形状")
    for literal in ("os", "os-", "-1", "os-1234", "ob-", "abcdefghijklm-1",
                    "OS-4", "os-0000"):
        check(pattern.match(literal) is None,
              f"非法形状 {literal!r} 不得匹配版本字面量形状")


def _test_t1_payload_versions_rejected():
    # 未知 wire 版本：8 个类型逐一必须给出"未知版本"诊断。
    for type_name in _FROZEN_SPAN_RECORD_TYPES:
        obj = _fixtures()[type_name]
        d = obj.to_dict()
        d["schema_version"] = str(d["schema_version"]).split("-")[0] + "-99"
        raises(lambda cls=type(obj), dd=d: cls.from_dict(dd),
               SchemaValidationError, "未知版本",
               f"{type_name} 的未知 schema 版本必须被拒绝")
        d2 = _without(obj.to_dict(), "schema_version")
        raises(lambda cls=type(obj), dd=d2: cls.from_dict(dd),
               SchemaValidationError, "缺必填字段",
               f"{type_name} 缺 schema_version 必须被拒绝")

    # 8 个类型各自绑定自己的 schema 常量（不得借用别的常量名）。
    for type_name, cls in SS.SPAN_RECORD_TYPES.items():
        constant = cls.SCHEMA_CONSTANT
        check(V.VERSION_CONSTANTS.get(constant) is not None,
              f"{type_name}.SCHEMA_CONSTANT={constant} 必须已在 versions 登记")
        check(V.classify_schema_version(constant, V.VERSION_CONSTANTS[constant])
              == "current",
              f"{type_name} 的自有 schema 常量必须分类为 current")

    # 算法版本字段：legacy → "已退役的旧规则版本"；未登记 → "必须为当前版本"。
    snapshot = _make_snapshot()
    nsd = snapshot.to_dict()
    raises(lambda: SS.SpanBuildSnapshot.from_dict(
        {**nsd, "synopsis_version": "ns-1"}),
        SchemaValidationError, "已退役的旧规则版本",
        "ns-1 载荷必须给出显式退役错误，不得静默按 ns-2 解释")
    raises(lambda: SS.SpanBuildSnapshot.from_dict(
        {**nsd, "qualification_policy_version": "sqpr-99"}),
        SchemaValidationError, "必须为当前版本",
        "未登记的资格策略算法版本必须被拒绝")
    raises(lambda: SS.SpanBuildSnapshot.from_dict(
        {**nsd, "span_builder_version": "sb-1"}),
        SchemaValidationError, "必须为 TS4 正文算法",
        "TS4 快照不得回落到 TS3 的 sb-1 正文算法")
    policy = _make_policy()
    pd = policy.to_dict()
    raises(lambda: SS.SpanQualificationPolicy.from_dict(
        {**pd, "policy_version": "sqpr-99"}),
        SchemaValidationError, "必须为当前版本",
        "未登记的资格策略版本必须被拒绝")
    raises(lambda: SS.SpanQualificationPolicy.from_dict(
        {**pd, "schema_version": "sqp-2"}),
        SchemaValidationError, "未知版本",
        "未登记的资格策略 schema 版本必须被拒绝")

    # synopsis 的 `nss-1` / `ns-1` 必须显式拒绝，`nss-2` / `ns-2` 正常读写。
    span = _make_span()
    syn = _make_synopsis(span.span_id)
    yd = syn.to_dict()
    raises(lambda: S.NavigationSynopsis.from_dict({**yd, "schema_version": "nss-1"}),
           SchemaValidationError, "旧 wire format",
           "nss-1 的 synopsis 载荷必须被显式识别为旧 wire format 并拒绝")
    raises(lambda: S.NavigationSynopsis.from_dict({**yd, "synopsis_version": "ns-1"}),
           SchemaValidationError, "已退役的旧规则版本",
           "ns-1 的 synopsis 载荷必须给出显式退役错误")
    raises(lambda: S.NavigationSynopsis.from_dict({**yd, "schema_version": "nss-99"}),
           SchemaValidationError, "未知版本", "未知 synopsis wire 版本必须被拒绝")
    check(S.NavigationSynopsis.from_dict(copy.deepcopy(yd)).to_dict() == yd,
          "nss-2 / ns-2 的 synopsis 必须可正常读回")
    check(V.classify_schema_version("SYNOPSIS_VERSION", syn.synopsis_version)
          == "current", "synopsis 载荷携带的算法版本必须分类为 current")

    # span 构建算法版本是**多值接受集**（sb-1 与当前 TS4 正文算法同时合法），但取值
    # 进入身份。被替换掉的 `sb-2` / `sb-3` / `sb-4` **也**必须在兼容读层合法：历史载荷必须仍可
    # 逐字节读回（否则冻结产物无法重建）。可读回 ≠ 可被当作当前产物消费：TS4 正式记录
    # 仍只接受当前算法（`span_schema._bind_span_builder_version`）。
    legacy_span = _make_span(span_builder_version="sb-1")
    check(legacy_span.span_builder_version == "sb-1",
          "sb-1 必须仍是合法的 span 构建算法版本（TS3 unassigned 仍在用）")
    check(S.OutlineSpan.from_dict(legacy_span.to_dict()) == legacy_span,
          "sb-1 的 span 必须可逐字节往返")
    check(legacy_span.span_id != span.span_id,
          "换构建算法版本必须换 span_id（算法版本进入身份）")
    for retired in V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS:
        old_span = _make_span(span_builder_version=retired)
        check(S.OutlineSpan.from_dict(old_span.to_dict()) == old_span,
              f"{retired} 的历史 span 载荷必须仍可逐字节读回")
        check(old_span.span_id != span.span_id,
              f"{retired} 与当前 TS4 正文算法必须是不同身份")
        check(old_span.span_builder_version in S.SPAN_BUILDER_VERSIONS,
              f"{retired} 必须仍在兼容读层接受集里")
    raises(lambda: _make_span(span_builder_version="sb-99"),
           SchemaValidationError, "必须为当前版本",
           "未登记的 span 构建算法版本必须被拒绝")


# ---------------------------------------------------------------------------
# T2 to_dict / from_dict 严格性
# ---------------------------------------------------------------------------


def _test_t2_unknown_and_cross_type():
    fixtures = _fixtures()
    for type_name, obj in fixtures.items():
        cls = type(obj)
        raises(lambda c=cls, dd={**obj.to_dict(), "未知字段": 1}: c.from_dict(dd),
               SchemaValidationError, "未知字段",
               f"{type_name}: 未知字段必须被拒绝")
        other = ("SpanBuildSnapshot" if type_name != "SpanBuildSnapshot"
                 else "SpanConservation")
        raises(lambda c=cls, dd=fixtures[other].to_dict():
               c.from_dict(copy.deepcopy(dd)),
               SchemaValidationError, "跨类型字典混淆",
               f"{type_name}: 跨类型字典混淆必须被拒绝")
        raises(lambda c=cls: c.from_dict([1, 2, 3]),
               SchemaValidationError, "需要 dict",
               f"{type_name}: 非 dict 输入必须被拒绝")


def _test_t2_missing_fields():
    for type_name, obj in _fixtures().items():
        cls = type(obj)
        d = obj.to_dict()
        for key in sorted(d):
            if key == "schema_type":
                continue
            value = d[key]
            if _is_empty_value(value):
                # 空值键：允许"缺省 ≡ 空值"，但读回必须完全等价 ——
                # 既不得静默换成语义不同的默认值，也不得变成半接受的畸形对象。
                accepted = None
                try:
                    accepted = cls.from_dict(_without(d, key))
                except SchemaValidationError:
                    pass
                check(accepted is None or accepted == obj,
                      f"{type_name}: 缺空值字段 {key} 必须要么被拒绝、要么读回与显式"
                      f"空值完全等价（值={value!r}，"
                      f"accepted={accepted is not None}，"
                      f"equal={accepted == obj if accepted is not None else 'N/A'}）")
            else:
                raises(lambda c=cls, dd=_without(d, key): c.from_dict(dd),
                       SchemaValidationError, "",
                       f"{type_name}: 缺非空字段 {key} 必须被拒绝")
        for key in _MUST_REJECT_MISSING[type_name]:
            check(not _is_empty_value(d[key]),
                  f"{type_name}: 载荷关键字段 {key} 的夹具值必须非空，"
                  f"否则该必填断言会空转（值={d[key]!r}）")


def _test_t2_wrong_type():
    cases = (
        ("OutlineStructureSnapshot", "document_id", 5),
        ("OutlineStructureSnapshot", "line_states", "not-a-list"),
        ("SpanQualificationPolicy", "max_snippet_chars", "200"),
        ("SpanQualificationPolicy", "completion_enabled", "no"),
        ("BodyRangeDisposition", "line_count", [1]),
        ("BodyRangeDisposition", "range_kind", 3),
        ("SpanEvidenceComponent", "admitted", "yes"),
        ("SpanEvidenceComponent", "landing", 7),
        ("SpanCitableCoverage", "span_local_length", "14"),
        ("SpanCitableCoverage", "citable_source_intervals", "x"),
        ("SpanConservation", "conserved", "true"),
        ("SpanConservation", "document_layer", {}),
        ("SpanBuildSnapshot", "terminal_count", 1.0),
        ("SpanBuildSnapshot", "inherited_unassigned_span_ids", 123),
        ("SynopsisSourceValidation", "ok", 1),
        ("SynopsisSourceValidation", "snippet_checks", {}),
    )
    fixtures = _fixtures()
    for type_name, key, bad in cases:
        obj = fixtures[type_name]
        cls = type(obj)
        d = copy.deepcopy(obj.to_dict())
        d[key] = bad
        raises(lambda c=cls, dd=d: c.from_dict(dd),
               SchemaValidationError, "",
               f"{type_name}: {key} 类型不符（{bad!r}）必须被拒绝")


def _test_t2_nan_and_inf():
    specials = (float("nan"), float("inf"), float("-inf"))
    for type_name, obj in _fixtures().items():
        cls = type(obj)
        d = obj.to_dict()
        for path in _float_paths(d):
            label = ".".join(str(p) for p in path)
            for special in specials:
                mutated = copy.deepcopy(d)
                _set_path(mutated, path, special)
                raises(lambda c=cls, dd=mutated: c.from_dict(dd),
                       SchemaValidationError, "",
                       f"{type_name}: 浮点字段 {label} 为 {special!r} 时必须被拒绝")
    # 12 个边界因子必须全部作为浮点叶子出现在规范形里。`span_confidence_min` 在
    # `distribution_only` 阶段为 None、在 `threshold_enabled` 阶段为实数，因此策略
    # 规范形的浮点叶子总数随阶段而变——这里按阶段派生期望集合，而不是写死 12。
    policy_leaves = _float_paths(_fixtures()["SpanQualificationPolicy"].to_dict())
    factor_leaves = [p for p in policy_leaves if p[:1] == ("factor_entries",)]
    expected_leaves = {("factor_entries", i, "factor") for i in range(12)}
    if V.SPAN_CONFIDENCE_MIN is not None:
        expected_leaves.add(("span_confidence_min",))
    check(set(policy_leaves) == expected_leaves,
          "策略规范形的浮点叶子必须恰为 12 个边界因子"
          + ("与已启用的阶段阈值" if V.SPAN_CONFIDENCE_MIN is not None else "")
          + f"，得到 {sorted(policy_leaves)}")
    check(len(factor_leaves) == 12,
          f"策略的 12 个边界因子必须都是规范形里的浮点叶子，得到 {len(factor_leaves)} 个")
    check(len(_float_paths(_fixtures()["SpanBuildSnapshot"].to_dict())) >= 17,
          "快照的规范形必须同时暴露策略因子、终态与落点坐标等浮点叶子，"
          f"得到 {len(_float_paths(_fixtures()['SpanBuildSnapshot'].to_dict()))}")

    # 构造器入口（不经 from_dict）同样 fail-closed。
    raises(lambda: SS.BoundaryFactor(side="left", cause=SS.BOUNDARY_CAUSES_LEFT[0],
                                     factor=float("nan")),
           SchemaValidationError, "有限实数",
           "BoundaryFactor 构造期必须拒绝 NaN 因子")
    raises(lambda: SS.LayoutHit(page_number=1, line_index=0, layout_span_index=0,
                                span_char_range=(0, 1), line_char_range=(0, 1),
                                bbox=(float("inf"), 0.0, 1.0, 1.0)),
           SchemaValidationError, "有限实数",
           "LayoutHit 构造期必须拒绝 Inf 坐标")
    raises(lambda: SS.ClosedInterval(start=0, end=float("nan")),
           SchemaValidationError, "必须为 int",
           "ClosedInterval 构造期必须拒绝非 int 边界")
    raises(lambda: SS.ClosedInterval(start=2, end=2),
           SchemaValidationError, "正长度",
           "ClosedInterval 必须拒绝零长度区间")
    raises(lambda: SS.BodyRangeDisposition.create(
        **_disposition_kwargs("os-0123456789abcdef", confidence=float("nan"))),
        SchemaValidationError, "有限实数",
        "BodyRangeDisposition 构造期必须拒绝 NaN confidence")
    raises(lambda: SS.SpanQualificationPolicy.create(
        **_policy_kwargs(factor_entries=tuple(
            SS.BoundaryFactor(side=e.side, cause=e.cause,
                              factor=(float("nan") if i == 0 else e.factor))
            for i, e in enumerate(_policy_factor_entries())))),
        SchemaValidationError, "有限实数",
        "SpanQualificationPolicy 构造期必须拒绝 NaN 边界因子")


def _test_t2_round_trip():
    for type_name, obj in _fixtures().items():
        cls = type(obj)
        d = obj.to_dict()
        check(isinstance(d, dict), f"{type_name}.to_dict 必须返回 dict")
        check(d.get("schema_type") == type_name.split("(")[0],
              f"{type_name}.to_dict 必须带正确的 schema_type，"
              f"得到 {d.get('schema_type')!r}")
        back = cls.from_dict(copy.deepcopy(d))
        check(canonical_json(back.to_dict()) == canonical_json(d),
              f"{type_name}: from_dict → to_dict 必须与原始规范形逐字节相等")
        check(back == obj, f"{type_name}: 往返对象必须相等")
        check(back.to_dict() == d,
              f"{type_name}: 往返后的规范形必须与原始规范形完全相同")


# ---------------------------------------------------------------------------
# T3 身份确定性
# ---------------------------------------------------------------------------

_ID_PREFIX = {
    "OutlineStructureSnapshot": "obs-",
    "SpanQualificationPolicy": "sqp-",
    "BodyRangeDisposition": "dr-",
    "SpanEvidenceComponent": "sc-",
    "SpanCitableCoverage": "cv-",
    "SpanConservation": "cs-",
    "SpanBuildSnapshot": "sbs-",
    "SynopsisSourceValidation": "sv-",
}


def _test_t3_identity_determinism():
    fixtures = _fixtures()
    pairs = (
        ("OutlineStructureSnapshot", "structure_snapshot_id",
         "structure_snapshot_locator", lambda: _make_structure_snapshot()),
        ("SpanQualificationPolicy", "policy_id", "policy_locator",
         lambda: _make_policy()),
        ("BodyRangeDisposition", "disposition_id", "disposition_locator",
         lambda: SS.BodyRangeDisposition.create(
             **_disposition_kwargs(_make_span().span_id))),
        ("SpanEvidenceComponent", "component_id", "component_locator",
         lambda: SS.SpanEvidenceComponent.create(
             **_component_kwargs(_make_span().span_id))),
        ("SpanCitableCoverage", "coverage_id", "coverage_locator",
         lambda: SS.SpanCitableCoverage.create(**_coverage_kwargs(
             _make_span().span_id,
             fixtures["SpanCitableCoverage"].covering_component_ids))),
        ("SpanConservation", "conservation_id", "conservation_locator",
         lambda: _make_conservation()),
        ("SpanBuildSnapshot", "snapshot_id", "snapshot_locator",
         lambda: _make_snapshot()),
        ("SynopsisSourceValidation", "validation_id", "validation_locator",
         lambda: _make_validation(_make_span().span_id)),
    )
    for type_name, id_field, locator_field, make in pairs:
        base = fixtures[type_name]
        first, second = make(), make()
        check(getattr(base, id_field).startswith(_ID_PREFIX[type_name]),
              f"{type_name}.{id_field} 必须带 {_ID_PREFIX[type_name]} 前缀，"
              f"得到 {getattr(base, id_field)!r}")
        check(getattr(base, locator_field).startswith("loc-"),
              f"{type_name}.{locator_field} 必须是定位身份（loc- 前缀），"
              f"得到 {getattr(base, locator_field)!r}")
        check(getattr(base, id_field) != getattr(base, locator_field),
              f"{type_name}: revision 身份与定位身份必须不同")
        check(getattr(first, id_field) == getattr(second, id_field)
              == getattr(base, id_field),
              f"{type_name}: 同一输入必须产生同一 {id_field}"
              f"（{getattr(first, id_field)!r} / {getattr(second, id_field)!r}）")
        check(getattr(first, locator_field) == getattr(second, locator_field)
              == getattr(base, locator_field),
              f"{type_name}: 同一输入必须产生同一 {locator_field}")

    structure = fixtures["OutlineStructureSnapshot"]
    other_state = _make_structure_snapshot(
        line_states=(_make_line_state(reason_code="layout_style"),))
    check(other_state.structure_snapshot_id != structure.structure_snapshot_id,
          "OutlineStructureSnapshot: line_states 变化必须改变 structure_snapshot_id")
    check(other_state.structure_snapshot_locator
          == structure.structure_snapshot_locator,
          "OutlineStructureSnapshot: 内容修订不得改变定位身份")
    check(other_state.content_fingerprint != structure.content_fingerprint,
          "OutlineStructureSnapshot: 内容指纹必须随行级事实变化")

    policy = fixtures["SpanQualificationPolicy"]
    other_policy = _make_policy(max_snippets_per_node=4)
    check(other_policy.policy_id != policy.policy_id,
          "SpanQualificationPolicy: 规范化字段变化必须改变 policy_id")
    check(other_policy.policy_locator == policy.policy_locator,
          "SpanQualificationPolicy: 内容修订不得改变定位身份")
    check(other_policy.policy_fingerprint != policy.policy_fingerprint,
          "SpanQualificationPolicy: 指纹必须随策略内容变化")
    check(policy.factor_for("left", SS.BOUNDARY_CAUSES_LEFT[0]) == 0.25,
          "SpanQualificationPolicy: 边界因子必须可按 (side, cause) 取回")

    span = _make_span()
    disposition = fixtures["BodyRangeDisposition"]
    other_disposition = SS.BodyRangeDisposition.create(
        **_disposition_kwargs(span.span_id, tight_char_count=_N + 1))
    check(other_disposition.disposition_id != disposition.disposition_id,
          "BodyRangeDisposition: tight_char_count 变化必须改变 disposition_id")
    check(other_disposition.disposition_locator == disposition.disposition_locator,
          "BodyRangeDisposition: 内容修订不得改变定位身份（位置相同）")
    moved = SS.BodyRangeDisposition.create(
        **_disposition_kwargs(span.span_id, start_line=3, end_line=3))
    check(moved.disposition_locator != disposition.disposition_locator,
          "BodyRangeDisposition: 位置变化必须改变定位身份")

    component = fixtures["SpanEvidenceComponent"]
    other_component = SS.SpanEvidenceComponent.create(
        **_component_kwargs(span.span_id, node_id="on-ffffffffffffffff"))
    check(other_component.component_id != component.component_id,
          "SpanEvidenceComponent: node_id 变化必须改变 component_id")
    check(other_component.component_locator == component.component_locator,
          "SpanEvidenceComponent: 内容修订不得改变定位身份")
    shifted = SS.SpanEvidenceComponent.create(
        **_component_kwargs(span.span_id, evidence_char_range=(0, _N - 1)))
    check(shifted.component_locator != component.component_locator,
          "SpanEvidenceComponent: 证据字符区间变化必须改变定位身份")

    coverage = fixtures["SpanCitableCoverage"]
    other_coverage = SS.SpanCitableCoverage.create(
        **_coverage_kwargs(span.span_id, (component.component_id,),
                           normalization_only_intervals=((_N - 1, _N),),
                           citable_source_intervals=((0, _N - 1),)))
    check(other_coverage.coverage_id != coverage.coverage_id,
          "SpanCitableCoverage: 四分类变化必须改变 coverage_id")
    check(other_coverage.coverage_locator == coverage.coverage_locator,
          "SpanCitableCoverage: 内容修订不得改变定位身份")
    narrower = SS.SpanCitableCoverage.create(
        **_coverage_kwargs(span.span_id, (component.component_id,),
                           span_local_length=_N + 1,
                           normalization_only_intervals=(),
                           citable_source_intervals=((0, _N + 1),)))
    check(narrower.coverage_locator != coverage.coverage_locator,
          "SpanCitableCoverage: span_local_length 变化必须改变定位身份")

    conservation = fixtures["SpanConservation"]
    other_conservation = _make_conservation(split=True)
    check(other_conservation.conservation_id != conservation.conservation_id,
          "SpanConservation: Evidence 分区变化必须改变 conservation_id")
    check(other_conservation.conservation_locator
          == conservation.conservation_locator,
          "SpanConservation: 层外内容修订不得改变定位身份")
    check(other_conservation.conserved is True and conservation.conserved is True,
          "两层夹具都必须自报守恒（否则守恒判定未被真正覆盖）")

    snapshot = fixtures["SpanBuildSnapshot"]
    other_snapshot = _make_snapshot(terminal_count=2)
    check(other_snapshot.snapshot_id != snapshot.snapshot_id,
          "SpanBuildSnapshot: terminal_count 变化必须改变 snapshot_id")
    check(other_snapshot.snapshot_locator == snapshot.snapshot_locator,
          "SpanBuildSnapshot: 内容修订不得改变定位身份")
    check(other_snapshot.content_fingerprint != snapshot.content_fingerprint,
          "SpanBuildSnapshot: 内容指纹必须随业务内容变化")
    check(other_snapshot.input_fingerprint == snapshot.input_fingerprint,
          "SpanBuildSnapshot: 受信输入未变时 input_fingerprint 不得变化")
    other_policy_snapshot = _make_snapshot(qualification_policy=_make_policy(
        policy_key="span-qualification-ts4a-alt"))
    check(other_policy_snapshot.snapshot_locator != snapshot.snapshot_locator,
          "SpanBuildSnapshot: 嵌入策略身份变化必须改变 snapshot 定位身份")

    validation = fixtures["SynopsisSourceValidation"]
    other_validation = _make_validation(span.span_id, snippet_char_range=(0, _N - 1))
    check(other_validation.validation_id != validation.validation_id,
          "SynopsisSourceValidation: snippet 区间变化必须改变 validation_id")
    check(other_validation.validation_locator == validation.validation_locator,
          "SynopsisSourceValidation: 内容修订不得改变定位身份")


# ---------------------------------------------------------------------------
# T4 词表
# ---------------------------------------------------------------------------


def _test_t4_vocabularies():
    codes = tuple(S.SYNOPSIS_REASON_CODES)
    check(len(codes) == 5,
          f"SYNOPSIS_REASON_CODES 必须恰为 5 值（TS4 冻结词表），得到 {len(codes)}")
    check(codes == _FROZEN_SYNOPSIS_REASON_CODES,
          f"SYNOPSIS_REASON_CODES 必须恰为冻结的 5 值且按序，得到 {codes}")
    check("table_only_pending_ts5" in codes,
          "SYNOPSIS_REASON_CODES 必须包含 table_only_pending_ts5")
    check(len(set(codes)) == len(codes), "SYNOPSIS_REASON_CODES 不得有重复项")
    for code in codes:
        syn = S.NavigationSynopsis.unavailable(node_id=_NODE_ID, reason_code=code)
        check(syn.reason_code == code and syn.snippets == (),
              f"reason_code={code!r} 必须能被 synopsis 真正接受并读回")
    raises(lambda: S.NavigationSynopsis.unavailable(node_id=_NODE_ID,
                                                    reason_code="table_only"),
           SchemaValidationError, "reason_code",
           "未登记的 synopsis 原因码必须被拒绝")

    # §19.0.1 方案 C：final 词表是**另一份**闭合词表。两边各含一个对方不允许的值，
    # 因此"一个开关 + 一张合并表"的实现无法通过下面的三组反例。
    final_codes = tuple(TS.FINAL_SYNOPSIS_REASON_CODES)
    check(final_codes == _FROZEN_FINAL_SYNOPSIS_REASON_CODES,
          f"FINAL_SYNOPSIS_REASON_CODES 必须恰为冻结的 5 值且按序，得到 {final_codes}")
    check("table_material_available_no_text_synopsis" in final_codes,
          "final 词表必须包含 table_material_available_no_text_synopsis")
    check("table_only_pending_ts5" not in final_codes,
          "final 词表不得包含 TS4 专属的 table_only_pending_ts5")
    check("table_material_available_no_text_synopsis" not in codes,
          "TS4 词表不得包含 final 专属的 "
          "table_material_available_no_text_synopsis")
    check(set(codes) - set(final_codes) == {"table_only_pending_ts5"},
          f"两词表的差集必须恰为 TS4 专属值，得到 "
          f"{set(codes) - set(final_codes)}")
    check(set(final_codes) - set(codes)
          == {"table_material_available_no_text_synopsis"},
          f"两词表的差集必须恰为 final 专属值，得到 "
          f"{set(final_codes) - set(codes)}")
    for code in final_codes:
        syn = TS.FinalNavigationSynopsis.unavailable(node_id=_NODE_ID,
                                                     reason_code=code)
        check(syn.reason_code == code and syn.snippets == (),
              f"final reason_code={code!r} 必须能被 final synopsis 接受并读回")
    raises(lambda: TS.FinalNavigationSynopsis.unavailable(
               node_id=_NODE_ID, reason_code="table_only_pending_ts5"),
           SchemaValidationError, "reason_code",
           "TS4 专属理由不得被 final synopsis 接受")
    raises(lambda: S.NavigationSynopsis.unavailable(
               node_id=_NODE_ID,
               reason_code="table_material_available_no_text_synopsis"),
           SchemaValidationError, "reason_code",
           "final 专属理由不得被 TS4 synopsis 接受")

    # §19.0.1 方案 C 的**来源轴**隔离，与 final 侧的 §19.12.4-7
    # （「final 片段引用 `os-*` 必须被拒」）互为镜像。
    #
    # 构造期**不**拦：`SynopsisSnippet.span_id` 的冻结契约只要求"非空字符串"
    # （TS4 期没有 `fos-`，为它加一条前缀规则等于改写冻结 wire 契约）。因此真正的
    # 门在**来源复核**：TS4 的 registry 以 `os-*` 为键，`fos-*` 在其中永远解析不到
    # 对象。若把两代 span 身份合成一套，这条反例会立刻失效——这正是方案 C 要求
    # "两个类型、两条来源轴"的理由之一。
    _span = _make_span()
    _policy = _make_policy()
    check(_span.span_id.startswith("os-")
          and not _span.span_id.startswith("fos-"),
          f"TS4 span 身份必须以 `os-` 开头（得到 {_span.span_id!r}），"
          "因此 final 身份不可能在 TS4 registry 里被解析到")
    _registry = {_span.span_id: _span}
    _real = _make_synopsis(_span.span_id)
    _val = SY.verify_synopsis_sources(_real, span_registry=_registry,
                                      coverage_registry={},
                                      verified_policy=_policy)
    check(_val.node_id == _NODE_ID
          and tuple(c.span_id for c in _val.snippet_checks) == (_span.span_id,),
          "正例基线：真 `os-*` 来源在 TS4 registry 里解析得到，"
          "来源复核返回逐片段结论（不触发结构性拒绝）")
    _forged = _make_synopsis(_FORGED_FINAL_SPAN_ID)
    check(_forged.status == "available"
          and _forged.source_span_ids == (_FORGED_FINAL_SPAN_ID,),
          "反例载体有效：TS4 简介确实收下了 `fos-*` 来源，"
          "所以下面拒绝的确实是复核阶段而不是构造阶段")
    raises(lambda f=_forged: SY.verify_synopsis_sources(
               f, span_registry=_registry, coverage_registry={},
               verified_policy=_policy),
           SY.SynopsisError, "引用了未知 span",
           "TS4 简介的 snippet 改成 `fos-*`（final 身份）后来源复核必须拒绝")

    check(tuple(S.SPAN_ROLES) == _FROZEN_SPAN_ROLES,
          f"SPAN_ROLES 值集不得随 TS4 变化，得到 {tuple(S.SPAN_ROLES)}")
    check(tuple(S.UNASSIGNED_REASONS) == _FROZEN_UNASSIGNED_REASONS,
          f"UNASSIGNED_REASONS 值集不得随 TS4 变化，"
          f"得到 {tuple(S.UNASSIGNED_REASONS)}")
    check(tuple(S.ALIGNMENT_VERDICTS) == _FROZEN_ALIGNMENT_VERDICTS,
          f"ALIGNMENT_VERDICTS 值集不得随 TS4 变化，"
          f"得到 {tuple(S.ALIGNMENT_VERDICTS)}")
    check(tuple(OB.TABLE_SCOPES) == _FROZEN_TABLE_SCOPES,
          f"TABLE_SCOPES 值集不得随 TS4 变化，得到 {tuple(OB.TABLE_SCOPES)}")
    check(tuple(SS._outline_vocab()["TABLE_SCOPES"]) == _FROZEN_TABLE_SCOPES,
          "span_schema 必须复用 outline_builder 的**唯一一份**表格范围词表")

    # TS4 新增词表不得与冻结词表互相污染。
    check("table_only_pending_ts5" not in S.UNASSIGNED_REASONS,
          "table_only_pending_ts5 是 synopsis 原因码，不是 unassigned 原因")
    check("table_only_pending_ts5" not in SS.BODY_RANGE_KINDS,
          "table_only_pending_ts5 不得混入正文范围五分类")
    check(set(SS.BODY_RANGE_KINDS) == {
        "regular", "table_adjacency", "table_inside", "unassigned", "empty"},
        f"BODY_RANGE_KINDS 必须恰为五分类，得到 {SS.BODY_RANGE_KINDS}")
    check(tuple(SS.DOCUMENT_LAYER_BUCKETS) == (
        "heading", "formal_unassigned", "non_content", "body"),
        f"文档层桶名必须封闭且按序，得到 {SS.DOCUMENT_LAYER_BUCKETS}")
    check(tuple(SS.BODY_LAYER_BUCKETS) == (
        "regular", "table_adjacency_provisional", "table_inside", "unassigned",
        "empty"), f"正文层桶名必须封闭且按序，得到 {SS.BODY_LAYER_BUCKETS}")
    check(set(SS.COMPONENT_ADMISSION_REASONS) == {
        "aligned_projected", "verdict_not_aligned", "refusal_record",
        "offset_unverifiable", "boundary_inexact", "landing_not_body_span",
        "residue_unmapped"},
        "组件准入原因必须是 7 值封闭集（含唯一放行码 aligned_projected），"
        f"得到 {SS.COMPONENT_ADMISSION_REASONS}")
    check(len(set(SS.COMPONENT_LANDINGS)) == 11,
          f"组件落点必须恰为 11 个互不重复的封闭值，得到 {SS.COMPONENT_LANDINGS}")
    check(tuple(SS.STRUCTURE_SNAPSHOT_COUNT_KEYS) == (
        "heading_node", "formal_unassigned", "body_under_node", "non_content"),
        f"结构快照计数键集必须封闭且与四态一一对应，"
        f"得到 {SS.STRUCTURE_SNAPSHOT_COUNT_KEYS}")
    check(set(SS.POLICY_STAGES) == {"distribution_only", "threshold_enabled"},
          f"资格策略阶段必须是两值封闭集合，得到 {SS.POLICY_STAGES}")
    check(set(SS.COMPONENT_VERDICTS) == set(S.ALIGNMENT_VERDICTS) | {"refused"},
          f"组件 verdict 词表必须恰为对齐终态词表 + refused，"
          f"得到 {SS.COMPONENT_VERDICTS}")

    # A/B 阶段必须由 SPAN_CONFIDENCE_MIN 唯一派生。
    expected = "distribution_only" if V.SPAN_CONFIDENCE_MIN is None \
        else "threshold_enabled"
    check(V.span_confidence_stage()
          == ("TS4-A" if V.SPAN_CONFIDENCE_MIN is None else "TS4-B"),
          f"span_confidence_stage 必须由 SPAN_CONFIDENCE_MIN="
          f"{V.SPAN_CONFIDENCE_MIN!r} 派生，得到 {V.span_confidence_stage()!r}")
    check(SS.self_check()["policy_stage"] == expected,
          f"span_schema 自报阶段必须为 {expected!r}，"
          f"得到 {SS.self_check()['policy_stage']!r}")
    check(SS.self_check()["span_confidence_stage"] == V.span_confidence_stage(),
          "自检阶段与版本层阶段必须一致")
    policy = _make_policy()
    check(policy.stage == expected,
          f"策略对象的阶段必须为 {expected!r}，得到 {policy.stage!r}")
    check(policy.span_confidence_min == V.SPAN_CONFIDENCE_MIN,
          "策略的 span_confidence_min 必须恒等于 versions.SPAN_CONFIDENCE_MIN")
    if V.SPAN_CONFIDENCE_MIN is None:
        other_stage, other_completion = "threshold_enabled", True
    else:
        other_stage, other_completion = "distribution_only", False
    raises(lambda: _make_policy(stage=other_stage),
           SchemaValidationError, "stage 必须为",
           f"阶段必须由 SPAN_CONFIDENCE_MIN 唯一派生：不得自报 {other_stage!r}")
    raises(lambda: _make_policy(completion_enabled=other_completion,
                                set_complete_supported=other_completion),
           SchemaValidationError, "",
           f"阶段={expected!r} 时不得声明 completion_enabled="
           f"{other_completion!r}")
    check(policy.completion_enabled is (expected != "distribution_only")
          and policy.set_complete_supported is (expected != "distribution_only"),
          f"阶段={expected!r} 的 completion / set_complete 必须与之匹配")
    if V.SPAN_CONFIDENCE_MIN is None:
        check(policy.completion_enabled is False
              and policy.set_complete_supported is False,
              "distribution_only 阶段不得开启 completion / set_complete")
        raises(lambda: _make_policy(completion_enabled=True),
               SchemaValidationError, "不得开启 completion",
               "distribution_only 阶段不得开启 completion")


# ---------------------------------------------------------------------------


def main() -> dict:
    _test_t1_registry_bijection()
    _test_t1_union_coverage()
    _test_t1_version_classification()
    _test_t1_payload_versions_rejected()
    _test_t2_unknown_and_cross_type()
    _test_t2_missing_fields()
    _test_t2_wrong_type()
    _test_t2_nan_and_inf()
    _test_t2_round_trip()
    _test_t3_identity_determinism()
    _test_t4_vocabularies()
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

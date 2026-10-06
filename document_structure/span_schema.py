"""TS4 span 记录层：8 个 versioned 顶层对象及其 frozen 子结构（计划 §18.4.3/§18.4.4）。

本模块**只**定义 wire 类型与严格编解码，不产生任何业务判断、不读数据库、不接收
provider/gateway/handoff。所有身份（`*_locator` / `*_id`）与指纹都在 `__post_init__`
里按载荷**重算并逐字比对**，因此"仿造字段再同步重算全部 ID"只能得到另一个**自洽但
不同**的对象——它仍然过不了 `span_verifier` 的受信 handoff 绑定。

三条硬约束（§18.4.3）：

1. **版本字面量只在 `versions` 里**。本模块不得内联出现任何版本字符串（`obs-1` 等）。
2. **未知字段拒绝、缺字段拒绝、类型不符拒绝、NaN/±Inf 拒绝**；不猜、不补默认值、不降级。
3. **区间一律半开 `[start, end)`、正长度、升序、两两不交且相邻必须已合并**；凡声明
   "划分"的字段组（覆盖四分类、三层守恒）必须两两不交且并集恰为声明域长度。
"""

from __future__ import annotations

import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Sequence

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    identity,
    locator,
    sha256_canonical,
    to_json_value,
)
from document_structure.normalization import tight
from document_structure.schema import (
    NavigationSynopsis,
    OutlineSpan,
    RESIDUE_CLASS_SEVERITY,
    UNASSIGNED_REASONS,
    _check_version,
    _err,
    _need_alignment_schema_version,
    _need_bool,
    _need_children,
    _need_enum,
    _need_int,
    _need_num,
    _need_ordered_pairs,
    _need_schema_version,
    _need_str,
    _need_str_tuple,
    _reject_unknown,
)

__all__ = [
    # 词表
    "BODY_ATTACHMENTS", "BODY_RANGE_KINDS", "COMPONENT_LANDINGS", "COMPONENT_VERDICTS",
    "COMPONENT_ADMISSION_REASONS", "POLICY_STAGES", "BOUNDARY_CAUSES_LEFT",
    "BOUNDARY_CAUSES_RIGHT", "CONSERVATION_LAYERS", "CONSERVATION_GAP_REASONS",
    "COVERAGE_PROBLEMS", "DISPOSITION_PROBLEMS", "EVIDENCE_ROW_PROBLEMS",
    "SYNOPSIS_CHECK_PROBLEMS", "STRUCTURE_SNAPSHOT_COUNT_KEYS",
    "DOCUMENT_LAYER_BUCKETS", "BODY_LAYER_BUCKETS", "SPAN_RECORD_TYPES",
    # 子结构
    "ClosedInterval", "LineStructureState", "BoundaryFactor", "LayoutHit",
    "EvidenceConservationRow", "ConservationGap", "SnippetCheck", "StateCount",
    "LayerBucket", "LandingCount",
    # 顶层对象
    "OutlineStructureSnapshot", "SpanQualificationPolicy", "BodyRangeDisposition",
    "SpanEvidenceComponent", "SpanCitableCoverage", "SpanConservation",
    "SpanBuildSnapshot", "SynopsisSourceValidation",
    # 区间代数
    "merge_intervals", "invert_intervals", "intersect_intervals",
    "subtract_intervals", "interval_length", "check_interval_partition",
    "partition_problems", "effective_citable_intervals",
    "derive_line_identity", "tight_char_count",
    # 运行时能力登记表（反自证边界）
    "CAPABILITY_KINDS", "ISSUER_SCOPES", "CapabilityError",
    "issued_capability", "capability_scope", "issued_capability_count",
    # 类型登记
    "SUBSTRUCTURE_TYPES", "SUBSTRUCTURE_REGISTRY", "RUNTIME_ONLY_TYPE_NAMES",
    # 自检
    "self_check",
]


# ---------------------------------------------------------------------------
# 0. 上游词表的**延迟**接入
# ---------------------------------------------------------------------------
#
# `outline_builder` 依赖 `aligner`（`NUMERIC_LIKE_CHARS`），而 `aligner` 必须能导入
# 本模块：本模块持有 TS4 的**运行时能力登记表**（`VerifiedPageLayout` /
# `VerifiedEvidenceSetAlignment` / `VerifiedTS3Handoff` … 的签发登记），而
# `align_evidence_set_verified` / `build_verified_page_layout` 都要用它来拒绝
# "调用者 `object.__new__` 出来的同形对象"。
#
# 因此若在本模块**顶层**导入 `outline_builder`，就会形成
# `span_schema → outline_builder → aligner → span_schema` 的循环导入。
# 折中办法只有两个：把词表抄一份（**禁止**：会出现两套四态定义），或者延迟导入。
# 这里选延迟导入，并把它收敛到一个函数里，词表仍然**只有一份**真值来源。

_OUTLINE_VOCAB: dict = {}


def _outline_vocab() -> dict:
    """返回 `outline_builder` 的权威词表（首次调用时导入并缓存）。"""
    if not _OUTLINE_VOCAB:
        from document_structure import outline_builder as OB
        _OUTLINE_VOCAB.update({
            "LINE_STRUCTURE_STATES": tuple(OB.LINE_STRUCTURE_STATES),
            "TABLE_SCOPES": tuple(OB.TABLE_SCOPES),
            "TABLE_REGION_REASONS": tuple(OB.TABLE_REGION_REASONS),
            "TABLE_REGION_INSIDE_REASONS": tuple(OB.TABLE_REGION_INSIDE_REASONS),
            "TABLE_REGION_ADJACENT_REASON": OB.TABLE_REGION_ADJACENT_REASON,
        })
    return _OUTLINE_VOCAB


# ---------------------------------------------------------------------------
# 1. 词表（封闭集合；只增不改）
# ---------------------------------------------------------------------------

#: 正文行的归属方式（`outline_builder` 的 `body_attachment`，§18.6.1）。
BODY_ATTACHMENTS: tuple[str, ...] = (
    "preceding_heading",
    "after_formal_unassigned_boundary",
    "before_first_heading",
)

#: 正文域五分类（§18.9.2）。`B_body = S_regular ⊎ A_adjacent_provisional
#: ⊎ T_inside_table ⊎ U_ts4_unassigned ⊎ E_empty`。
BODY_RANGE_KINDS: tuple[str, ...] = (
    "regular", "table_adjacency", "table_inside", "unassigned", "empty",
)

#: 组件落点（§18.8.1）。前 9 个是"文档/正文域落点"，后 2 个是"Evidence 层不可用落点"。
COMPONENT_LANDINGS: tuple[str, ...] = (
    "body_span", "table_inside", "table_adjacency", "body_unassigned", "body_empty",
    "heading_node", "formal_unassigned", "non_content", "outside_body",
    "alignment_offset_unverifiable", "alignment_residue_unmapped",
)

#: 组件 verdict：**直接复用** `TextAlignmentRecord.verdict` 的既有词表加上拒绝对齐记录。
COMPONENT_VERDICTS: tuple[str, ...] = (
    "aligned", "partially_aligned", "unaligned", "refused",
)

#: 组件准入原因（`admitted` 的**唯一**依据链，§18.8.1）。`admitted=True` 当且仅当
#: `admission_reason == "aligned_projected"`。
COMPONENT_ADMISSION_REASONS: tuple[str, ...] = (
    "aligned_projected",
    "verdict_not_aligned",
    "refusal_record",
    "offset_unverifiable",
    "boundary_inexact",
    "landing_not_body_span",
    "residue_unmapped",
)

#: 资格策略阶段（§18.14.4）。TS4-A 恒为 `distribution_only`；`threshold_enabled`
#: 只有在 `versions.SPAN_CONFIDENCE_MIN` 已裁决时才可能成立。
POLICY_STAGES: tuple[str, ...] = ("distribution_only", "threshold_enabled")

#: 左边界成因（§18.8.5）。取值必须与 run 切断原因形成**穷尽真值表**。
BOUNDARY_CAUSES_LEFT: tuple[str, ...] = (
    "preceding_heading",
    "resume_after_empty",
    "resume_after_table_inside",
    "resume_after_table_adjacency",
    "resume_after_non_content",
)

#: 右边界成因（§18.8.5）。穷尽性由 §18.6.1 的切断条件保证：run 末行之后的**第一条
#: 非家具行**只可能是 heading / formal_unassigned / non_content / 另一类正文范围，
#: 或者不存在（文档结束）。
BOUNDARY_CAUSES_RIGHT: tuple[str, ...] = (
    "next_heading",
    "document_end",
    "empty",
    "table_inside",
    "table_adjacency",
    "formal_unassigned",
    "non_content",
)

#: 三层守恒的层名（§18.9）。
CONSERVATION_LAYERS: tuple[str, ...] = ("document", "body", "evidence")

#: 文档层桶名：`D_nonfurniture = H_heading ⊎ F_formal_unassigned ⊎ N_non_content ⊎ B_body`。
DOCUMENT_LAYER_BUCKETS: tuple[str, ...] = (
    "heading", "formal_unassigned", "non_content", "body",
)

#: 正文层桶名（与 `BODY_RANGE_KINDS` 一一对应，顺序固定为 §18.9.2 的书写顺序）。
BODY_LAYER_BUCKETS: tuple[str, ...] = (
    "regular", "table_adjacency_provisional", "table_inside", "unassigned", "empty",
)

#: `OutlineStructureSnapshot.counts` 的**封闭**键集。
STRUCTURE_SNAPSHOT_COUNT_KEYS: tuple[str, ...] = (
    "heading_node", "formal_unassigned", "body_under_node", "non_content",
)

#: 守恒缺口原因码。
CONSERVATION_GAP_REASONS: tuple[str, ...] = (
    "layer_bucket_key_unknown",
    "layer_sum_mismatch",
    "layer_sign_mismatch",
    "evidence_partition_gap",
    "evidence_partition_overlap",
    "evidence_length_mismatch",
    "landing_count_mismatch",
    "component_target_missing",
    "span_without_regular_disposition",
    "regular_disposition_without_span",
    "text_char_mismatch",
)

#: 覆盖记录的自证问题码。
COVERAGE_PROBLEMS: tuple[str, ...] = (
    "coverage_partition_invalid",
    "coverage_domain_mismatch",
    "effective_intervals_inconsistent",
    "covering_component_unknown",
    "covering_component_not_admitted",
    "coverage_without_regular_disposition",
)

#: 范围处置记录的自证问题码。
DISPOSITION_PROBLEMS: tuple[str, ...] = (
    "boundary_cause_missing",
    "boundary_cause_forbidden",
    "confidence_inconsistent",
    "table_reason_inconsistent",
    "unassigned_reason_inconsistent",
    "span_missing",
    "span_forbidden",
    "range_kind_unknown",
)

#: Evidence 守恒行的自证问题码。
EVIDENCE_ROW_PROBLEMS: tuple[str, ...] = (
    "partition_invalid",
    "mapped_without_admitted_component",
    "landing_count_mismatch",
    "residue_class_missing",
)

#: 简介来源校验的问题码。
SYNOPSIS_CHECK_PROBLEMS: tuple[str, ...] = (
    "snippet_outside_effective_intervals",
    "snippet_hash_mismatch",
    "snippet_not_extractive",
    "snippet_index_mismatch",
    "snippet_span_unknown",
    "snippet_over_span_length",
    "snippet_longer_than_limit",
)


# ---------------------------------------------------------------------------
# 2. 区间代数（半开 [start,end)，正长度，升序、不交、相邻已合并）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClosedInterval:
    """半开区间 `[start, end)`；正长度。**不变量**：`0 <= start < end`。"""

    start: int
    end: int

    def __post_init__(self) -> None:
        t = "ClosedInterval"
        for name in ("start", "end"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                _err(t, f"{name} 必须为 int，得到 {v!r}")
        if self.start < 0:
            _err(t, f"start 不得小于 0，得到 {self.start}")
        if self.end <= self.start:
            _err(t, f"必须满足 end > start（正长度），得到 {(self.start, self.end)}")

    def length(self) -> int:
        return self.end - self.start

    def to_dict(self) -> list:
        return [self.start, self.end]

    @classmethod
    def from_dict(cls, d: Any) -> "ClosedInterval":
        t = "ClosedInterval"
        if not isinstance(d, (list, tuple)) or len(d) != 2:
            _err(t, f"必须为长度 2 的 int 数组，得到 {d!r}")
        return cls(start=_need_int({"_": d[0]}, "_", t), end=_need_int({"_": d[1]}, "_", t))


def _iv_sort_key(iv: ClosedInterval) -> tuple[int, int]:
    return (iv.start, iv.end)


def _coerce_pairs(intervals: Sequence[Any]) -> tuple[tuple[int, int], ...]:
    """把 `ClosedInterval` / `(a,b)` / `[a,b]` 统一成有序 `(a,b)` 元组序列。"""
    out: list[tuple[int, int]] = []
    for iv in intervals:
        if isinstance(iv, ClosedInterval):
            out.append((iv.start, iv.end))
        elif isinstance(iv, (list, tuple)) and len(iv) == 2:
            out.append((int(iv[0]), int(iv[1])))
        else:
            raise SchemaValidationError(f"区间必须为 ClosedInterval 或长度 2 的序列，得到 {iv!r}")
    return tuple(out)


def _wrap(pairs: Sequence[tuple[int, int]]) -> tuple[ClosedInterval, ...]:
    return tuple(ClosedInterval(start=a, end=b) for (a, b) in pairs)


def merge_intervals(intervals: Sequence[Any]) -> tuple[ClosedInterval, ...]:
    """升序化并合并**重叠或相邻**的同类区间（相邻同类必须合并成一段）。"""
    pairs = sorted(_coerce_pairs(intervals))
    out: list[tuple[int, int]] = []
    for a, b in pairs:
        if a >= b:
            raise SchemaValidationError(f"区间必须满足 end > start，得到 {(a, b)}")
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return _wrap(out)


def invert_intervals(intervals: Sequence[Any], domain_length: int) -> tuple[ClosedInterval, ...]:
    """`[0, domain_length)` 内不属于 `intervals` 的部分（输入须已合并）。"""
    if not isinstance(domain_length, int) or isinstance(domain_length, bool):
        raise SchemaValidationError("domain_length 必须为 int")
    if domain_length < 0:
        raise SchemaValidationError("domain_length 不得为负")
    out: list[tuple[int, int]] = []
    pos = 0
    for a, b in _coerce_pairs(intervals):
        if a < pos:
            raise SchemaValidationError(f"输入区间必须已合并且升序，{(a, b)} 处重叠")
        if b > domain_length:
            raise SchemaValidationError(f"区间 {(a, b)} 越出域长度 {domain_length}")
        if a > pos:
            out.append((pos, a))
        pos = b
    if pos < domain_length:
        out.append((pos, domain_length))
    return _wrap(out)


def intersect_intervals(a: Sequence[Any], b: Sequence[Any]) -> tuple[ClosedInterval, ...]:
    pa, pb = sorted(_coerce_pairs(a)), sorted(_coerce_pairs(b))
    out: list[tuple[int, int]] = []
    i = j = 0
    while i < len(pa) and j < len(pb):
        lo = max(pa[i][0], pb[j][0])
        hi = min(pa[i][1], pb[j][1])
        if lo < hi:
            out.append((lo, hi))
        if pa[i][1] <= pb[j][1]:
            i += 1
        else:
            j += 1
    return merge_intervals(out)


def subtract_intervals(a: Sequence[Any], b: Sequence[Any]) -> tuple[ClosedInterval, ...]:
    pa, pb = sorted(_coerce_pairs(a)), sorted(_coerce_pairs(b))
    out: list[tuple[int, int]] = []
    for (lo, hi) in pa:
        pos = lo
        for (blo, bhi) in pb:
            if bhi <= pos:
                continue
            if blo >= hi:
                break
            if blo > pos:
                out.append((pos, blo))
            pos = max(pos, bhi)
            if pos >= hi:
                break
        if pos < hi:
            out.append((pos, hi))
    return merge_intervals(out)


def interval_length(intervals: Sequence[Any]) -> int:
    return sum(b - a for (a, b) in _coerce_pairs(intervals))


def partition_problems(lists: Sequence[tuple[str, Sequence[Any]]],
                       domain_length: int) -> tuple[str, ...]:
    """校验若干"类别 → 区间列表"**两两不交且并集恰为** `[0, domain_length)`。

    返回问题字符串元组（空元组即合法）。这是覆盖四分类与 Evidence 三分法的**唯一**
    判定实现：`__post_init__` 与正式 verifier 共用它，避免两套划分规则。
    """
    problems: list[str] = []
    events: list[tuple[int, int, str]] = []
    for kind, ivs in lists:
        for (a, b) in _coerce_pairs(ivs):
            if a < 0 or b > domain_length:
                problems.append(f"{kind} 区间 {(a, b)} 越出域 [0,{domain_length})")
                continue
            events.append((a, b, kind))
    if problems:
        return tuple(problems)
    events.sort()
    pos = 0
    for (a, b, kind) in events:
        if a < pos:
            problems.append(f"{kind} 区间 {(a, b)} 与前段重叠（pos={pos}）")
            continue
        if a > pos:
            problems.append(f"[{pos},{a}) 未被任何类别覆盖")
        pos = b
    if pos != domain_length:
        problems.append(f"并集终点 {pos} != 域长度 {domain_length}")
    return tuple(problems)


def check_interval_partition(lists: Sequence[tuple[str, Sequence[Any]]],
                             domain_length: int, where: str) -> tuple[tuple[int, int, str], ...]:
    """`partition_problems` 的 fail-closed 包装：任一问题即抛 `SchemaValidationError`。

    合法时返回按位置排序的 `(start, end, kind)` 三元组序列（已 tile 整个域）。
    """
    problems = partition_problems(lists, domain_length)
    if problems:
        _err(where, "区间划分非法：" + "；".join(problems))
    events: list[tuple[int, int, str]] = []
    for kind, ivs in lists:
        for (a, b) in _coerce_pairs(ivs):
            events.append((a, b, kind))
    events.sort()
    return tuple(events)


def effective_citable_intervals(normalization_only: Sequence[Any],
                                citable: Sequence[Any],
                                non_citable: Sequence[Any],
                                uncovered: Sequence[Any],
                                domain_length: int) -> tuple[ClosedInterval, ...]:
    """**有效可引用区间**（§18.8.3）：只被 normalization-only 分隔的可引用内容要跨过
    折叠空格**桥接**成一段。

    定义：先按四分类 tile 整个域；再取"不含 `non_citable` / `uncovered` 的极大连续段"，
    每段内若有 `citable` 位置，则取**首个 citable 起点**到**末个 citable 终点**为该段的
    有效区间（首尾若有 normalization-only 则裁掉）。
    """
    events = check_interval_partition(
        (("normalization_only", normalization_only), ("citable_source", citable),
         ("non_citable_source", non_citable), ("uncovered_source", uncovered)),
        domain_length, "effective_citable_intervals")
    out: list[tuple[int, int]] = []
    i = 0
    n = len(events)
    while i < n:
        if events[i][2] in ("non_citable_source", "uncovered_source"):
            i += 1
            continue
        j = i
        first: int | None = None
        last: int | None = None
        while j < n and events[j][2] in ("citable_source", "normalization_only"):
            if events[j][2] == "citable_source":
                if first is None:
                    first = events[j][0]
                last = events[j][1]
            j += 1
        if first is not None and last is not None:
            out.append((first, last))
        i = j
    return _wrap(out)


def _need_intervals(d: dict, key: str, typename: str) -> tuple[ClosedInterval, ...]:
    """必填的规范化区间列表；**缺字段直接拒绝**（`_need_ordered_pairs` 缺字段返回空，
    因此这里必须先显式确认字段存在，否则"漏写 attrubute"会被静默解释成"该类别为空"）。
    """
    if key not in d:
        _err(typename, f"缺必填字段: {key}")
    if d[key] is None:
        _err(typename, f"{key} 不得为 null（无内容请写 []）")
    pairs = _need_ordered_pairs(d, key, typename)
    out: list[ClosedInterval] = []
    prev_end = -1
    for (a, b) in pairs:
        if a == prev_end:
            _err(typename, f"{key} 相邻同类区间必须已合并，{(a, b)} 处相邻未合并")
        prev_end = b
        out.append(ClosedInterval(start=a, end=b))
    return tuple(out)


def _need_exactly_one_interval(d: dict, key: str, typename: str) -> tuple[int, int]:
    """长度恰为 1 段的区间（用于 `LayoutHit` 的逐段落点）。"""
    ivs = _need_intervals(d, key, typename)
    if len(ivs) != 1:
        _err(typename, f"{key} 必须恰为 1 段，得到 {len(ivs)} 段")
    return (ivs[0].start, ivs[0].end)


def _need_residue_intervals(d: dict, key: str, typename: str) -> tuple[tuple[int, int, str], ...]:
    v = d.get(key)
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list，得到 {type(v).__name__}")
    out: list[tuple[int, int, str]] = []
    prev_end = -1
    for i, item in enumerate(v):
        if not isinstance(item, list) or len(item) != 3:
            _err(typename, f"{key}[{i}] 必须为 [start, end, residue_class]，得到 {item!r}")
        a, b, cls = item
        for x in (a, b):
            if not isinstance(x, int) or isinstance(x, bool):
                _err(typename, f"{key}[{i}] 的区间元素必须为 int，得到 {x!r}")
        if a < 0 or b <= a:
            _err(typename, f"{key}[{i}] 必须满足 0 <= start < end，得到 {(a, b)}")
        if a < prev_end:
            _err(typename, f"{key} 必须升序且不重叠，{i} 处得到 {(a, b)}")
        if not isinstance(cls, str) or cls == "":
            _err(typename, f"{key}[{i}] residue_class 必须为非空字符串")
        prev_end = b
        out.append((a, b, cls))
    return tuple(out)


# ---------------------------------------------------------------------------
# 3. 确定性身份派生
# ---------------------------------------------------------------------------

def derive_line_identity(*, document_version: str, page_number: int, line_index: int,
                         text: str, bbox: Sequence[float]) -> str:
    """`ln-<sha256[:16]>`：真实 `LayoutLine` 的行身份（**仅**由真实上游字段派生）。"""
    return identity("ln", {
        "document_version": document_version,
        "page_number": page_number,
        "line_index": line_index,
        "text_sha256": sha256_canonical(text),
        "bbox": list(bbox),
    })


# ---------------------------------------------------------------------------
# 4. frozen 子结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineStructureState:
    """逐非家具行的四态记录（§18.3.3/§18.6.1）。

    空值真值表封闭：
    - `heading_node`：`node_id` 非空、`body_attachment is None`；
    - `formal_unassigned` / `non_content`：`node_id is None`、`body_attachment is None`；
    - `body_under_node`：`body_attachment` 非空，`node_id` **可**为 None（文档开头 /
      形式未归属边界之后）。
    """

    page_number: int
    line_index: int
    line_identity: str
    state: str
    node_id: str | None
    body_attachment: str | None
    reason_code: str

    def __post_init__(self) -> None:
        t = "LineStructureState"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        _need_int({"v": self.line_index}, "v", t, lo=0)
        for name in ("line_identity", "reason_code"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        states = _outline_vocab()["LINE_STRUCTURE_STATES"]
        if self.state not in states:
            _err(t, f"state 必须属于 {states}，得到 {self.state!r}")
        if self.node_id is not None and (not isinstance(self.node_id, str)
                                         or self.node_id == ""):
            _err(t, "node_id 必须为非空字符串或 None")
        if self.body_attachment is not None and self.body_attachment not in BODY_ATTACHMENTS:
            _err(t, f"body_attachment 必须属于 {BODY_ATTACHMENTS} 或 None，"
                    f"得到 {self.body_attachment!r}")
        if self.state == "body_under_node":
            if self.body_attachment is None:
                _err(t, "body_under_node 必须给出 body_attachment")
        else:
            if self.body_attachment is not None:
                _err(t, f"{self.state} 的 body_attachment 必须为 None")
        if self.state in ("formal_unassigned", "non_content") and self.node_id is not None:
            _err(t, f"{self.state} 的 node_id 必须为 None")
        if self.state == "heading_node" and self.node_id is None:
            _err(t, "heading_node 必须给出 node_id")

    def to_dict(self) -> dict:
        return {
            "schema_type": "LineStructureState",
            "page_number": self.page_number,
            "line_index": self.line_index,
            "line_identity": self.line_identity,
            "state": self.state,
            "node_id": self.node_id,
            "body_attachment": self.body_attachment,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "LineStructureState":
        t = "LineStructureState"
        d = _reject_unknown(d, {
            "schema_type", "page_number", "line_index", "line_identity", "state",
            "node_id", "body_attachment", "reason_code"}, t)
        _need_enum(d, "schema_type", t, ("LineStructureState",))
        return cls(
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            line_identity=_need_str(d, "line_identity", t),
            state=_need_enum(d, "state", t, _outline_vocab()["LINE_STRUCTURE_STATES"]),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            body_attachment=_need_str(d, "body_attachment", t, none_ok=True),
            reason_code=_need_str(d, "reason_code", t),
        )


@dataclass(frozen=True)
class StateCount:
    """`(state, line_count)` 精确计数项；键集由所属对象强制封闭。"""

    key: str
    line_count: int

    def __post_init__(self) -> None:
        t = "StateCount"
        if not isinstance(self.key, str) or self.key == "":
            _err(t, "key 必须为非空字符串")
        _need_int({"v": self.line_count}, "v", t, lo=0)

    def to_dict(self) -> dict:
        return {"schema_type": "StateCount", "key": self.key,
                "line_count": self.line_count}

    @classmethod
    def from_dict(cls, d: Any) -> "StateCount":
        t = "StateCount"
        d = _reject_unknown(d, {"schema_type", "key", "line_count"}, t)
        _need_enum(d, "schema_type", t, ("StateCount",))
        return cls(key=_need_str(d, "key", t),
                   line_count=_need_int(d, "line_count", t, lo=0))


@dataclass(frozen=True)
class LayerBucket:
    """某一层的一个桶：桶名 + 行数 + **tight 字符数**（空格的差异不参与守恒）。"""

    bucket: str
    line_count: int
    tight_char_count: int

    def __post_init__(self) -> None:
        t = "LayerBucket"
        if not isinstance(self.bucket, str) or self.bucket == "":
            _err(t, "bucket 必须为非空字符串")
        _need_int({"v": self.line_count}, "v", t, lo=0)
        _need_int({"v": self.tight_char_count}, "v", t, lo=0)

    def to_dict(self) -> dict:
        return {"schema_type": "LayerBucket", "bucket": self.bucket,
                "line_count": self.line_count,
                "tight_char_count": self.tight_char_count}

    @classmethod
    def from_dict(cls, d: Any) -> "LayerBucket":
        t = "LayerBucket"
        d = _reject_unknown(d, {"schema_type", "bucket", "line_count",
                                "tight_char_count"}, t)
        _need_enum(d, "schema_type", t, ("LayerBucket",))
        return cls(bucket=_need_str(d, "bucket", t),
                   line_count=_need_int(d, "line_count", t, lo=0),
                   tight_char_count=_need_int(d, "tight_char_count", t, lo=0))


@dataclass(frozen=True)
class LandingCount:
    """某一落点的 char_map 段数与覆盖字符数（Evidence 守恒行的构成明细）。"""

    landing: str
    segment_count: int
    char_count: int

    def __post_init__(self) -> None:
        t = "LandingCount"
        if self.landing not in COMPONENT_LANDINGS:
            _err(t, f"landing 必须属于 {COMPONENT_LANDINGS}，得到 {self.landing!r}")
        _need_int({"v": self.segment_count}, "v", t, lo=0)
        _need_int({"v": self.char_count}, "v", t, lo=0)

    def to_dict(self) -> dict:
        return {"schema_type": "LandingCount", "landing": self.landing,
                "segment_count": self.segment_count, "char_count": self.char_count}

    @classmethod
    def from_dict(cls, d: Any) -> "LandingCount":
        t = "LandingCount"
        d = _reject_unknown(d, {"schema_type", "landing", "segment_count",
                                "char_count"}, t)
        _need_enum(d, "schema_type", t, ("LandingCount",))
        return cls(landing=_need_enum(d, "landing", t, COMPONENT_LANDINGS),
                   segment_count=_need_int(d, "segment_count", t, lo=0),
                   char_count=_need_int(d, "char_count", t, lo=0))


@dataclass(frozen=True)
class BoundaryFactor:
    """边界成因 → 因子（§18.8.5）。`side` 与 `cause` 组合在策略内唯一。"""

    side: str
    cause: str
    factor: float

    def __post_init__(self) -> None:
        t = "BoundaryFactor"
        if self.side not in ("left", "right"):
            _err(t, f"side 必须为 'left'/'right'，得到 {self.side!r}")
        allowed = BOUNDARY_CAUSES_LEFT if self.side == "left" else BOUNDARY_CAUSES_RIGHT
        if self.cause not in allowed:
            _err(t, f"{self.side} 侧 cause 必须属于 {allowed}，得到 {self.cause!r}")
        if not isinstance(self.factor, (int, float)) or isinstance(self.factor, bool):
            _err(t, f"factor 必须为实数，得到 {self.factor!r}")
        _need_num({"v": self.factor}, "v", t, lo=0.0, hi=1.0)

    def to_dict(self) -> dict:
        return {"schema_type": "BoundaryFactor", "side": self.side,
                "cause": self.cause, "factor": self.factor}

    @classmethod
    def from_dict(cls, d: Any) -> "BoundaryFactor":
        t = "BoundaryFactor"
        d = _reject_unknown(d, {"schema_type", "side", "cause", "factor"}, t)
        _need_enum(d, "schema_type", t, ("BoundaryFactor",))
        return cls(side=_need_enum(d, "side", t, ("left", "right")),
                   cause=_need_str(d, "cause", t),
                   factor=_need_num(d, "factor", t, lo=0.0, hi=1.0))


@dataclass(frozen=True)
class LayoutHit:
    """一段 Evidence 字符在真实布局上的落点（§18.5.2 走查的**逐段**结果）。

    - `span_char_range`：`LayoutSpan.text` 内的 `[char_start+char_offset,
      char_start+span_stop)`（域 `L_s`）；
    - `line_char_range`：`LayoutLine.text` 内的 `[line_start, line_stop)`（域 `L_l`）。
    """

    page_number: int
    line_index: int
    layout_span_index: int
    span_char_range: tuple[int, int]
    line_char_range: tuple[int, int]
    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        t = "LayoutHit"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        _need_int({"v": self.line_index}, "v", t, lo=0)
        _need_int({"v": self.layout_span_index}, "v", t, lo=0)
        for name in ("span_char_range", "line_char_range"):
            v = getattr(self, name)
            if not isinstance(v, tuple) or len(v) != 2:
                _err(t, f"{name} 必须为 (start, end) 二元组，得到 {v!r}")
            _need_ordered_pairs({"_": [list(v)]}, "_", t)
            if v[1] <= v[0]:
                _err(t, f"{name} 必须为正长度，得到 {v!r}")
        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            _err(t, "bbox 必须为 (x0,y0,x1,y1)")
        for x in self.bbox:
            _need_num({"v": x}, "v", t)
        if self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]:
            _err(t, f"bbox 必须非退化，得到 {self.bbox!r}")

    def to_dict(self) -> dict:
        # 两个区间字段的 wire 形态是**区间列表**（与 `from_dict` 的
        # `_need_exactly_one_interval` 互逆）。写成扁平 `[start, end]` 会让
        # `LayoutHit` 无法往返，进而使任何带 `layout_hits` 的组件、以及整份
        # `SpanBuildSnapshot` 都无法从 canonical JSON 读回。
        return {
            "schema_type": "LayoutHit",
            "page_number": self.page_number,
            "line_index": self.line_index,
            "layout_span_index": self.layout_span_index,
            "span_char_range": [list(self.span_char_range)],
            "line_char_range": [list(self.line_char_range)],
            "bbox": list(self.bbox),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "LayoutHit":
        t = "LayoutHit"
        d = _reject_unknown(d, {
            "schema_type", "page_number", "line_index", "layout_span_index",
            "span_char_range", "line_char_range", "bbox"}, t)
        _need_enum(d, "schema_type", t, ("LayoutHit",))
        bbox = d.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            _err(t, f"bbox 必须为长度 4 的数组，得到 {bbox!r}")
        for x in bbox:
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                _err(t, f"bbox 元素必须为实数，得到 {x!r}")
        return cls(
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            layout_span_index=_need_int(d, "layout_span_index", t, lo=0),
            span_char_range=_need_exactly_one_interval(d, "span_char_range", t),
            line_char_range=_need_exactly_one_interval(d, "line_char_range", t),
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        )


@dataclass(frozen=True)
class EvidenceConservationRow:
    """单条 Evidence 在 `[0, block_char_length)` 上的三分法（§18.9.3）。

    `mapped_char_map_landings ⊎ alignment_offset_unverifiable ⊎ residue_unmapped`
    必须两两不交且并集恰为 `[0, block_char_length)`。
    """

    evidence_id: str
    block_char_length: int
    mapped_intervals: tuple[ClosedInterval, ...]
    unverifiable_intervals: tuple[ClosedInterval, ...]
    residue_intervals: tuple[tuple[int, int, str], ...]
    landing_counts: tuple[LandingCount, ...]
    conserved: bool
    problems: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "EvidenceConservationRow"
        if not isinstance(self.evidence_id, str) or self.evidence_id == "":
            _err(t, "evidence_id 必须为非空字符串")
        _need_int({"v": self.block_char_length}, "v", t, lo=0)
        for name in ("mapped_intervals", "unverifiable_intervals"):
            if not isinstance(getattr(self, name), tuple):
                _err(t, f"{name} 必须为元组")
        if not isinstance(self.residue_intervals, tuple):
            _err(t, "residue_intervals 必须为元组")
        for i, seg in enumerate(self.residue_intervals):
            if not isinstance(seg, tuple) or len(seg) != 3:
                _err(t, f"residue_intervals[{i}] 必须为 (start, end, class)")
            if seg[2] not in _RESIDUE_CLASSES:
                _err(t, f"residue_intervals[{i}] 的 class 必须属于 "
                        f"{sorted(_RESIDUE_CLASSES)}，得到 {seg[2]!r}")
        if not isinstance(self.landing_counts, tuple):
            _err(t, "landing_counts 必须为元组")
        for i, lc in enumerate(self.landing_counts):
            if not isinstance(lc, LandingCount):
                _err(t, f"landing_counts[{i}] 必须为 LandingCount")
        if not isinstance(self.conserved, bool):
            _err(t, "conserved 必须为 bool")
        for p in self.problems:
            if p not in EVIDENCE_ROW_PROBLEMS:
                _err(t, f"problems 必须属于 {EVIDENCE_ROW_PROBLEMS}，得到 {p!r}")
        key_order = {k: i for i, k in enumerate(COMPONENT_LANDINGS)}
        seen = [lc.landing for lc in self.landing_counts]
        if len(set(seen)) != len(seen):
            _err(t, "landing_counts 的 landing 不得重复")
        if seen != sorted(seen, key=lambda k: key_order[k]):
            _err(t, f"landing_counts 必须按固定词表顺序 {COMPONENT_LANDINGS}")
        # 三分法必须 tile 整个块域。
        check_interval_partition(
            (("mapped_char_map_landings", self.mapped_intervals),
             ("alignment_offset_unverifiable", self.unverifiable_intervals),
             ("residue_unmapped", [(a, b) for (a, b, _c) in self.residue_intervals])),
            self.block_char_length, "EvidenceConservationRow")
        residue_chars = sum(b - a for (a, b, _c) in self.residue_intervals)
        if sum(lc.char_count for lc in self.landing_counts) != (
                interval_length(self.mapped_intervals) + residue_chars):
            _err(t, "landing_counts 的 char_count 之和必须等于映射段与残差段字符数之和")

    def to_dict(self) -> dict:
        return {
            "schema_type": "EvidenceConservationRow",
            "evidence_id": self.evidence_id,
            "block_char_length": self.block_char_length,
            "mapped_intervals": [iv.to_dict() for iv in self.mapped_intervals],
            "unverifiable_intervals": [iv.to_dict() for iv in self.unverifiable_intervals],
            "residue_intervals": [[a, b, c] for (a, b, c) in self.residue_intervals],
            "landing_counts": [lc.to_dict() for lc in self.landing_counts],
            "conserved": self.conserved,
            "problems": list(self.problems),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceConservationRow":
        t = "EvidenceConservationRow"
        d = _reject_unknown(d, {
            "schema_type", "evidence_id", "block_char_length", "mapped_intervals",
            "unverifiable_intervals", "residue_intervals", "landing_counts",
            "conserved", "problems"}, t)
        _need_enum(d, "schema_type", t, ("EvidenceConservationRow",))
        return cls(
            evidence_id=_need_str(d, "evidence_id", t),
            block_char_length=_need_int(d, "block_char_length", t, lo=0),
            mapped_intervals=_need_intervals(d, "mapped_intervals", t),
            unverifiable_intervals=_need_intervals(d, "unverifiable_intervals", t),
            residue_intervals=_need_residue_intervals(d, "residue_intervals", t),
            landing_counts=_need_children(d, "landing_counts", t, LandingCount.from_dict),
            conserved=_need_bool(d, "conserved", t),
            problems=_need_str_tuple(d, "problems", t),
        )


@dataclass(frozen=True)
class ConservationGap:
    """一处守恒缺口：**必须**给出层、原因、来源身份与位置（不得只记"不一致"）。"""

    gap_id: str
    layer: str
    reason_code: str
    source_identity: str
    interval: tuple[int, int] | None
    line_ref: tuple[int, int] | None
    detail: str

    def __post_init__(self) -> None:
        t = "ConservationGap"
        for name in ("gap_id", "source_identity", "detail"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.layer not in CONSERVATION_LAYERS:
            _err(t, f"layer 必须属于 {CONSERVATION_LAYERS}，得到 {self.layer!r}")
        if self.reason_code not in CONSERVATION_GAP_REASONS:
            _err(t, f"reason_code 必须属于 {CONSERVATION_GAP_REASONS}，"
                    f"得到 {self.reason_code!r}")
        for name in ("interval", "line_ref"):
            v = getattr(self, name)
            if v is None:
                continue
            if not isinstance(v, tuple) or len(v) != 2:
                _err(t, f"{name} 必须为二元组或 None，得到 {v!r}")
            for x in v:
                if not isinstance(x, int) or isinstance(x, bool):
                    _err(t, f"{name} 元素必须为 int，得到 {v!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ConservationGap",
            "gap_id": self.gap_id,
            "layer": self.layer,
            "reason_code": self.reason_code,
            "source_identity": self.source_identity,
            "interval": list(self.interval) if self.interval is not None else None,
            "line_ref": list(self.line_ref) if self.line_ref is not None else None,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConservationGap":
        t = "ConservationGap"
        d = _reject_unknown(d, {
            "schema_type", "gap_id", "layer", "reason_code", "source_identity",
            "interval", "line_ref", "detail"}, t)
        _need_enum(d, "schema_type", t, ("ConservationGap",))

        def _pair(key: str) -> tuple[int, int] | None:
            v = d.get(key)
            if v is None:
                return None
            if not isinstance(v, list) or len(v) != 2:
                _err(t, f"{key} 必须为长度 2 的 int 数组或 null，得到 {v!r}")
            for x in v:
                if not isinstance(x, int) or isinstance(x, bool):
                    _err(t, f"{key} 元素必须为 int，得到 {x!r}")
            return (v[0], v[1])

        return cls(
            gap_id=_need_str(d, "gap_id", t),
            layer=_need_enum(d, "layer", t, CONSERVATION_LAYERS),
            reason_code=_need_enum(d, "reason_code", t, CONSERVATION_GAP_REASONS),
            source_identity=_need_str(d, "source_identity", t),
            interval=_pair("interval"),
            line_ref=_pair("line_ref"),
            detail=_need_str(d, "detail", t),
        )


@dataclass(frozen=True)
class SnippetCheck:
    """单条 snippet 的**逐字符回溯校验**结果（§18.8.6）。

    `covering_effective_interval` 必须是覆盖 `snippet_char_range` 的那段有效可引用
    区间——简介只导航，本校验只证明"摘录确为原文"，**不**赋予任何证据资格。
    """

    snippet_index: int
    span_id: str
    snippet_char_range: tuple[int, int]
    snippet_source_hash: str
    covering_effective_interval: tuple[int, int] | None
    ok: bool
    problems: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "SnippetCheck"
        _need_int({"v": self.snippet_index}, "v", t, lo=0)
        if not isinstance(self.span_id, str) or self.span_id == "":
            _err(t, "span_id 必须为非空字符串")
        if not isinstance(self.snippet_char_range, tuple) or len(self.snippet_char_range) != 2:
            _err(t, "snippet_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.snippet_char_range)]}, "_", t)
        if self.covering_effective_interval is not None:
            if (not isinstance(self.covering_effective_interval, tuple)
                    or len(self.covering_effective_interval) != 2):
                _err(t, "covering_effective_interval 必须为二元组或 None")
            _need_ordered_pairs({"_": [list(self.covering_effective_interval)]}, "_", t)
        if not isinstance(self.snippet_source_hash, str) or len(self.snippet_source_hash) != 64:
            _err(t, "snippet_source_hash 必须为 64 位小写十六进制 sha256")
        if not isinstance(self.ok, bool):
            _err(t, "ok 必须为 bool")
        for p in self.problems:
            if p not in SYNOPSIS_CHECK_PROBLEMS:
                _err(t, f"problems 必须属于 {SYNOPSIS_CHECK_PROBLEMS}，得到 {p!r}")
        if self.ok == bool(self.problems):
            _err(t, "ok 当且仅当 problems 为空")

    def to_dict(self) -> dict:
        return {
            "schema_type": "SnippetCheck",
            "snippet_index": self.snippet_index,
            "span_id": self.span_id,
            "snippet_char_range": list(self.snippet_char_range),
            "snippet_source_hash": self.snippet_source_hash,
            "covering_effective_interval": (
                list(self.covering_effective_interval)
                if self.covering_effective_interval is not None else None),
            "ok": self.ok,
            "problems": list(self.problems),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SnippetCheck":
        t = "SnippetCheck"
        d = _reject_unknown(d, {
            "schema_type", "snippet_index", "span_id", "snippet_char_range",
            "snippet_source_hash", "covering_effective_interval", "ok",
            "problems"}, t)
        _need_enum(d, "schema_type", t, ("SnippetCheck",))

        def _pair(key: str) -> tuple[int, int] | None:
            v = d.get(key)
            if v is None:
                return None
            pr = _need_ordered_pairs({key: [v]}, key, t)
            return pr[0]

        return cls(
            snippet_index=_need_int(d, "snippet_index", t, lo=0),
            span_id=_need_str(d, "span_id", t),
            snippet_char_range=_pair("snippet_char_range"),
            snippet_source_hash=_need_str(d, "snippet_source_hash", t),
            covering_effective_interval=_pair("covering_effective_interval"),
            ok=_need_bool(d, "ok", t),
            problems=_need_str_tuple(d, "problems", t),
        )


# 残差分类词表：直接复用 `schema.RESIDUE_CLASS_SEVERITY`（**不得**另造同义名）。
_RESIDUE_CLASSES: tuple[str, ...] = RESIDUE_CLASS_SEVERITY


# ---------------------------------------------------------------------------
# 5. 顶层 versioned 对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutlineStructureSnapshot:
    """`obs-1`：把 TS3 产物的**四态行级事实**冻结成 TS4 的唯一结构输入（§18.3.3）。

    快照**只**承载"每个非家具行的状态、归属与正文附着方式"，不承载任何边界判断：
    `line_structure_diagnostic` 的启发式字段（`body_attachment` 以外的推断）不得被
    TS4 当成边界权威（§18.3.4）。
    """

    structure_snapshot_locator: str
    structure_snapshot_id: str
    schema_version: str
    outline_algorithm_version: str
    heading_profile_version: str
    table_region_version: str
    toc_reconciliation_version: str
    normalization_version: str
    document_id: str
    document_version: str
    page_layout_id: str
    outline_locator: str
    outline_id: str
    line_states: tuple[LineStructureState, ...]
    counts: tuple[StateCount, ...]
    content_fingerprint: str

    SCHEMA_CONSTANT = "OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "OutlineStructureSnapshot"
        for name in ("structure_snapshot_locator", "structure_snapshot_id",
                     "document_id", "document_version", "page_layout_id",
                     "outline_locator", "outline_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_algorithm_version(t, "outline_algorithm_version",
                                self.outline_algorithm_version, "OUTLINE_ALGORITHM_VERSION")
        _bind_algorithm_version(t, "heading_profile_version",
                                self.heading_profile_version,
                                "HEADING_QUALIFICATION_PROFILE_VERSION")
        _bind_algorithm_version(t, "table_region_version", self.table_region_version,
                                "TABLE_REGION_QUALIFICATION_VERSION")
        _bind_algorithm_version(t, "toc_reconciliation_version",
                                self.toc_reconciliation_version,
                                "TOC_BODY_RECONCILIATION_VERSION")
        _bind_algorithm_version(t, "normalization_version", self.normalization_version,
                                "NORMALIZATION_VERSION")
        for i, ls in enumerate(self.line_states):
            if not isinstance(ls, LineStructureState):
                _err(t, f"line_states[{i}] 必须为 LineStructureState")
        keys = [(ls.page_number, ls.line_index) for ls in self.line_states]
        if keys != sorted(keys):
            _err(t, "line_states 必须按 (page_number, line_index) 升序")
        if len(set(keys)) != len(keys):
            _err(t, "line_states 的 (page_number, line_index) 不得重复")
        if not isinstance(self.counts, tuple):
            _err(t, "counts 必须为元组")
        if tuple(c.key for c in self.counts) != STRUCTURE_SNAPSHOT_COUNT_KEYS:
            _err(t, f"counts 的键必须恰为 {STRUCTURE_SNAPSHOT_COUNT_KEYS} 且按序，"
                    f"得到 {tuple(c.key for c in self.counts)}")
        recomputed = {k: 0 for k in STRUCTURE_SNAPSHOT_COUNT_KEYS}
        for ls in self.line_states:
            recomputed[ls.state] += 1
        for c in self.counts:
            if c.line_count != recomputed[c.key]:
                _err(t, f"counts[{c.key}]={c.line_count} 与 line_states 重算 "
                        f"{recomputed[c.key]} 不一致")
        _check_fingerprint(t, self.content_fingerprint, self._payload_for_fingerprint())
        expected = identity("obs", self._identity_payload())
        if self.structure_snapshot_id != expected:
            _err(t, "structure_snapshot_id 与派生身份不一致："
                    f"{self.structure_snapshot_id!r} != {expected!r}")
        expected_loc = locator("obs", {
            "outline_locator": self.outline_locator,
            "outline_id": self.outline_id,
            "document_version": self.document_version,
        })
        if self.structure_snapshot_locator != expected_loc:
            _err(t, "structure_snapshot_locator 与派生定位不一致："
                    f"{self.structure_snapshot_locator!r} != {expected_loc!r}")

    def _payload_for_fingerprint(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "outline_algorithm_version": self.outline_algorithm_version,
            "heading_profile_version": self.heading_profile_version,
            "table_region_version": self.table_region_version,
            "toc_reconciliation_version": self.toc_reconciliation_version,
            "normalization_version": self.normalization_version,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "page_layout_id": self.page_layout_id,
            "outline_locator": self.outline_locator,
            "outline_id": self.outline_id,
            "line_states": [ls.to_dict() for ls in self.line_states],
        }

    def _identity_payload(self) -> dict:
        payload = self._payload_for_fingerprint()
        payload["structure_snapshot_locator"] = self.structure_snapshot_locator
        payload["counts"] = [c.to_dict() for c in self.counts]
        payload["content_fingerprint"] = self.content_fingerprint
        return payload

    def to_dict(self) -> dict:
        return {
            "schema_type": "OutlineStructureSnapshot",
            "structure_snapshot_locator": self.structure_snapshot_locator,
            "structure_snapshot_id": self.structure_snapshot_id,
            "schema_version": self.schema_version,
            "outline_algorithm_version": self.outline_algorithm_version,
            "heading_profile_version": self.heading_profile_version,
            "table_region_version": self.table_region_version,
            "toc_reconciliation_version": self.toc_reconciliation_version,
            "normalization_version": self.normalization_version,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "page_layout_id": self.page_layout_id,
            "outline_locator": self.outline_locator,
            "outline_id": self.outline_id,
            "line_states": [ls.to_dict() for ls in self.line_states],
            "counts": [c.to_dict() for c in self.counts],
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "OutlineStructureSnapshot":
        t = "OutlineStructureSnapshot"
        d = _reject_unknown(d, {
            "schema_type", "structure_snapshot_locator", "structure_snapshot_id",
            "schema_version", "outline_algorithm_version", "heading_profile_version",
            "table_region_version", "toc_reconciliation_version",
            "normalization_version", "document_id", "document_version",
            "page_layout_id", "outline_locator", "outline_id", "line_states",
            "counts", "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("OutlineStructureSnapshot",))
        return cls(
            structure_snapshot_locator=_need_str(d, "structure_snapshot_locator", t),
            structure_snapshot_id=_need_str(d, "structure_snapshot_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            outline_algorithm_version=_need_str(d, "outline_algorithm_version", t),
            heading_profile_version=_need_str(d, "heading_profile_version", t),
            table_region_version=_need_str(d, "table_region_version", t),
            toc_reconciliation_version=_need_str(d, "toc_reconciliation_version", t),
            normalization_version=_need_str(d, "normalization_version", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            outline_locator=_need_str(d, "outline_locator", t),
            outline_id=_need_str(d, "outline_id", t),
            line_states=_need_children(d, "line_states", t, LineStructureState.from_dict),
            counts=_need_children(d, "counts", t, StateCount.from_dict),
            content_fingerprint=_need_str(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, *, outline_algorithm_version: str, heading_profile_version: str,
               table_region_version: str, toc_reconciliation_version: str,
               normalization_version: str, document_id: str, document_version: str,
               page_layout_id: str, outline_locator: str, outline_id: str,
               line_states: tuple[LineStructureState, ...]) -> "OutlineStructureSnapshot":
        payload = {
            "schema_version": V.OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
            "outline_algorithm_version": outline_algorithm_version,
            "heading_profile_version": heading_profile_version,
            "table_region_version": table_region_version,
            "toc_reconciliation_version": toc_reconciliation_version,
            "normalization_version": normalization_version,
            "document_id": document_id,
            "document_version": document_version,
            "page_layout_id": page_layout_id,
            "outline_locator": outline_locator,
            "outline_id": outline_id,
            "line_states": [ls.to_dict() for ls in line_states],
        }
        fp = sha256_canonical(payload)
        counts = {k: 0 for k in STRUCTURE_SNAPSHOT_COUNT_KEYS}
        for ls in line_states:
            counts[ls.state] += 1
        loc = locator("obs", {
            "outline_locator": outline_locator,
            "outline_id": outline_id,
            "document_version": document_version,
        })
        ident_payload = dict(payload)
        ident_payload["structure_snapshot_locator"] = loc
        ident_payload["counts"] = [
            {"schema_type": "StateCount", "key": k, "line_count": counts[k]}
            for k in STRUCTURE_SNAPSHOT_COUNT_KEYS]
        ident_payload["content_fingerprint"] = fp
        return cls(
            structure_snapshot_locator=loc,
            structure_snapshot_id=identity("obs", ident_payload),
            schema_version=V.OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
            outline_algorithm_version=outline_algorithm_version,
            heading_profile_version=heading_profile_version,
            table_region_version=table_region_version,
            toc_reconciliation_version=toc_reconciliation_version,
            normalization_version=normalization_version,
            document_id=document_id, document_version=document_version,
            page_layout_id=page_layout_id, outline_locator=outline_locator,
            outline_id=outline_id, line_states=tuple(line_states),
            counts=tuple(StateCount(key=k, line_count=counts[k])
                         for k in STRUCTURE_SNAPSHOT_COUNT_KEYS),
            content_fingerprint=fp,
        )


@dataclass(frozen=True)
class SpanQualificationPolicy:
    """`sqp-1`：TS4 的资格策略记录（**阶段自洽**的版本化策略）。

    本对象只对**记录自身**的阶段自洽性负责（与当前处于 A 还是 B 无关）：

    - `distribution_only` ⇔ `span_confidence_min is None` ⇔ `completion_enabled is False`
      ⇔ `set_complete_supported is False`；
    - `threshold_enabled` ⇔ 有限阈值（`[0,1]`）⇔ 同时开启 `completion_enabled` 与
      `set_complete_supported`。

    **当前**阶段（A/B）与该记录是否一致、以及 `span_confidence_min` 是否等于
    `versions.SPAN_CONFIDENCE_MIN`，由 provider 在**新建**入口单点强制
    （`span_policy.assert_current_policy`，计划 §18.3.2(9)）。把全局常量写进记录身份
    会把"当前轮次"混进"历史产物"：历史 A 记录必须在 TS4-B 发布后仍能按其内嵌 policy
    identity **读回、重建和验证**，但永远拿不到完成资格。本对象因此**不**读取
    `versions.SPAN_CONFIDENCE_MIN`。
    """

    policy_locator: str
    policy_id: str
    schema_version: str
    policy_version: str
    policy_key: str
    stage: str
    span_confidence_min: float | None
    factor_entries: tuple[BoundaryFactor, ...]
    completion_enabled: bool
    set_complete_supported: bool
    max_snippets_per_node: int
    max_snippet_chars: int
    min_snippet_chars: int
    max_total_snippet_chars: int
    sentence_terminators: tuple[str, ...]
    closing_quotes: tuple[str, ...]
    policy_fingerprint: str

    SCHEMA_CONSTANT = "SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "SpanQualificationPolicy"
        for name in ("policy_locator", "policy_id", "policy_key"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_algorithm_version(t, "policy_version", self.policy_version,
                                "SPAN_QUALIFICATION_POLICY_VERSION")
        if self.stage not in POLICY_STAGES:
            _err(t, f"stage 必须属于 {POLICY_STAGES}，得到 {self.stage!r}")
        if self.span_confidence_min is not None:
            _need_num({"v": self.span_confidence_min}, "v", t, lo=0.0, hi=1.0)
        if not isinstance(self.completion_enabled, bool):
            _err(t, "completion_enabled 必须为 bool")
        if not isinstance(self.set_complete_supported, bool):
            _err(t, "set_complete_supported 必须为 bool")
        # 阶段 ⇔ 阈值 ⇔ completion 的**自洽性**是本对象的不变式。它与"当前处于 A 还是 B"
        # 无关，因此历史读回 / 复核路径同样成立（见类文档）。
        if self.stage == "distribution_only":
            if self.span_confidence_min is not None:
                _err(t, "stage 必须为 'distribution_only'：分布口径不得携带 "
                        f"span_confidence_min={self.span_confidence_min!r}")
            if self.completion_enabled or self.set_complete_supported:
                _err(t, "distribution_only 阶段不得开启 completion 或 set_complete")
        else:
            if self.span_confidence_min is None:
                _err(t, "stage 必须为 'threshold_enabled'：已裁决阶段必须携带有限阈值"
                        "（threshold_enabled 不得没有 threshold）")
            if not self.completion_enabled or not self.set_complete_supported:
                _err(t, "threshold_enabled 阶段必须同时开启 completion 与 "
                        "set_complete_supported")
        if not isinstance(self.factor_entries, tuple):
            _err(t, "factor_entries 必须为元组")
        seen: list[tuple[str, str]] = []
        for i, bf in enumerate(self.factor_entries):
            if not isinstance(bf, BoundaryFactor):
                _err(t, f"factor_entries[{i}] 必须为 BoundaryFactor")
            seen.append((bf.side, bf.cause))
        expected_keys = ([("left", c) for c in BOUNDARY_CAUSES_LEFT]
                         + [("right", c) for c in BOUNDARY_CAUSES_RIGHT])
        if seen != expected_keys:
            _err(t, "factor_entries 必须**穷尽**且按固定词表顺序给出全部边界成因："
                    f"{expected_keys}，得到 {seen}")
        for name in ("max_snippets_per_node", "max_snippet_chars", "min_snippet_chars",
                     "max_total_snippet_chars"):
            _need_int({name: getattr(self, name)}, name, t, lo=1)
        if self.min_snippet_chars > self.max_snippet_chars:
            _err(t, "min_snippet_chars 不得大于 max_snippet_chars")
        if self.max_snippet_chars > self.max_total_snippet_chars:
            _err(t, "max_snippet_chars 不得大于 max_total_snippet_chars")
        if len(self.sentence_terminators) < 1:
            _err(t, "sentence_terminators 不得为空")
        for x in self.sentence_terminators:
            if not isinstance(x, str) or x == "":
                _err(t, "sentence_terminators 元素必须为非空字符串")
        for x in self.closing_quotes:
            if not isinstance(x, str) or x == "":
                _err(t, "closing_quotes 元素必须为非空字符串")
        _check_fingerprint(t, self.policy_fingerprint, self._fingerprint_payload())
        expected_id = identity("sqp", self._identity_payload())
        if self.policy_id != expected_id:
            _err(t, "policy_id 与派生身份不一致："
                    f"{self.policy_id!r} != {expected_id!r}")
        expected_loc = locator("sqp", {"policy_key": self.policy_key,
                                       "policy_version": self.policy_version})
        if self.policy_locator != expected_loc:
            _err(t, "policy_locator 与派生定位不一致："
                    f"{self.policy_locator!r} != {expected_loc!r}")

    def _fingerprint_payload(self) -> dict:
        return {
            "policy_key": self.policy_key,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "stage": self.stage,
            "span_confidence_min": self.span_confidence_min,
            "factor_entries": [bf.to_dict() for bf in self.factor_entries],
            "completion_enabled": self.completion_enabled,
            "set_complete_supported": self.set_complete_supported,
            "max_snippets_per_node": self.max_snippets_per_node,
            "max_snippet_chars": self.max_snippet_chars,
            "min_snippet_chars": self.min_snippet_chars,
            "max_total_snippet_chars": self.max_total_snippet_chars,
            "sentence_terminators": list(self.sentence_terminators),
            "closing_quotes": list(self.closing_quotes),
        }

    def _identity_payload(self) -> dict:
        payload = self._fingerprint_payload()
        payload["policy_locator"] = self.policy_locator
        payload["policy_fingerprint"] = self.policy_fingerprint
        return payload

    def factor_for(self, side: str, cause: str) -> float:
        for bf in self.factor_entries:
            if bf.side == side and bf.cause == cause:
                return bf.factor
        _err("SpanQualificationPolicy", f"未登记的边界成因 ({side},{cause})")

    def to_dict(self) -> dict:
        return {
            "schema_type": "SpanQualificationPolicy",
            "policy_locator": self.policy_locator,
            "policy_id": self.policy_id,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "policy_key": self.policy_key,
            "stage": self.stage,
            "span_confidence_min": self.span_confidence_min,
            "factor_entries": [bf.to_dict() for bf in self.factor_entries],
            "completion_enabled": self.completion_enabled,
            "set_complete_supported": self.set_complete_supported,
            "max_snippets_per_node": self.max_snippets_per_node,
            "max_snippet_chars": self.max_snippet_chars,
            "min_snippet_chars": self.min_snippet_chars,
            "max_total_snippet_chars": self.max_total_snippet_chars,
            "sentence_terminators": list(self.sentence_terminators),
            "closing_quotes": list(self.closing_quotes),
            "policy_fingerprint": self.policy_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SpanQualificationPolicy":
        t = "SpanQualificationPolicy"
        d = _reject_unknown(d, {
            "schema_type", "policy_locator", "policy_id", "schema_version",
            "policy_version", "policy_key", "stage", "span_confidence_min",
            "factor_entries", "completion_enabled", "set_complete_supported",
            "max_snippets_per_node", "max_snippet_chars", "min_snippet_chars",
            "max_total_snippet_chars", "sentence_terminators", "closing_quotes",
            "policy_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("SpanQualificationPolicy",))
        raw_min = d.get("span_confidence_min")
        if raw_min is not None:
            raw_min = _need_num({"v": raw_min}, "v", t, lo=0.0, hi=1.0)
        return cls(
            policy_locator=_need_str(d, "policy_locator", t),
            policy_id=_need_str(d, "policy_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            policy_version=_need_str(d, "policy_version", t),
            policy_key=_need_str(d, "policy_key", t),
            stage=_need_enum(d, "stage", t, POLICY_STAGES),
            span_confidence_min=raw_min,
            factor_entries=_need_children(d, "factor_entries", t,
                                          BoundaryFactor.from_dict),
            completion_enabled=_need_bool(d, "completion_enabled", t),
            set_complete_supported=_need_bool(d, "set_complete_supported", t),
            max_snippets_per_node=_need_int(d, "max_snippets_per_node", t, lo=1),
            max_snippet_chars=_need_int(d, "max_snippet_chars", t, lo=1),
            min_snippet_chars=_need_int(d, "min_snippet_chars", t, lo=1),
            max_total_snippet_chars=_need_int(d, "max_total_snippet_chars", t, lo=1),
            sentence_terminators=_need_str_tuple(d, "sentence_terminators", t),
            closing_quotes=_need_str_tuple(d, "closing_quotes", t),
            policy_fingerprint=_need_str(d, "policy_fingerprint", t),
        )

    @classmethod
    def create(cls, *, policy_key: str, stage: str, span_confidence_min: float | None,
               factor_entries: tuple[BoundaryFactor, ...], completion_enabled: bool,
               set_complete_supported: bool, max_snippets_per_node: int,
               max_snippet_chars: int, min_snippet_chars: int,
               max_total_snippet_chars: int, sentence_terminators: tuple[str, ...],
               closing_quotes: tuple[str, ...]) -> "SpanQualificationPolicy":
        payload = {
            "policy_key": policy_key,
            "schema_version": V.SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION,
            "policy_version": V.SPAN_QUALIFICATION_POLICY_VERSION,
            "stage": stage,
            "span_confidence_min": span_confidence_min,
            "factor_entries": [bf.to_dict() for bf in factor_entries],
            "completion_enabled": completion_enabled,
            "set_complete_supported": set_complete_supported,
            "max_snippets_per_node": max_snippets_per_node,
            "max_snippet_chars": max_snippet_chars,
            "min_snippet_chars": min_snippet_chars,
            "max_total_snippet_chars": max_total_snippet_chars,
            "sentence_terminators": list(sentence_terminators),
            "closing_quotes": list(closing_quotes),
        }
        fp = sha256_canonical(payload)
        loc = locator("sqp", {"policy_key": policy_key,
                              "policy_version": V.SPAN_QUALIFICATION_POLICY_VERSION})
        ident_payload = dict(payload)
        ident_payload["policy_locator"] = loc
        ident_payload["policy_fingerprint"] = fp
        return cls(
            policy_locator=loc,
            policy_id=identity("sqp", ident_payload),
            schema_version=V.SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION,
            policy_version=V.SPAN_QUALIFICATION_POLICY_VERSION,
            policy_key=policy_key, stage=stage,
            span_confidence_min=span_confidence_min,
            factor_entries=tuple(factor_entries),
            completion_enabled=completion_enabled,
            set_complete_supported=set_complete_supported,
            max_snippets_per_node=max_snippets_per_node,
            max_snippet_chars=max_snippet_chars,
            min_snippet_chars=min_snippet_chars,
            max_total_snippet_chars=max_total_snippet_chars,
            sentence_terminators=tuple(sentence_terminators),
            closing_quotes=tuple(closing_quotes),
            policy_fingerprint=fp,
        )


@dataclass(frozen=True)
class BodyRangeDisposition:
    """`sps-1`：一个**极大正文范围**的处置（§18.7/§18.9.2）。

    合并键固定为"源序连续 + 同 `range_kind` / `node_id` / `unassigned_reason` /
    `table_scope` / `table_reason`"，任一键变化即切断。`regular` 范围必须持久化
    `left/right_boundary_cause` 与由二者决定的 `confidence`。
    """

    disposition_locator: str
    disposition_id: str
    schema_version: str
    span_builder_version: str
    range_kind: str
    node_id: str | None
    unassigned_reason: str | None
    table_scope: str | None
    table_reason: str | None
    start_page: int
    start_line: int
    end_page: int
    end_line: int
    line_count: int
    tight_char_count: int
    left_boundary_cause: str | None
    right_boundary_cause: str | None
    confidence: float | None
    span_id: str | None
    problems: tuple[str, ...]

    SCHEMA_CONSTANT = "BODY_RANGE_DISPOSITION_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "BodyRangeDisposition"
        for name in ("disposition_locator", "disposition_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_span_builder_version(t, self.span_builder_version)
        if self.range_kind not in BODY_RANGE_KINDS:
            _err(t, f"range_kind 必须属于 {BODY_RANGE_KINDS}，得到 {self.range_kind!r}")
        _need_int({"v": self.start_page}, "v", t, lo=1)
        _need_int({"v": self.start_line}, "v", t, lo=0)
        _need_int({"v": self.end_page}, "v", t, lo=1)
        _need_int({"v": self.end_line}, "v", t, lo=0)
        _need_int({"v": self.line_count}, "v", t, lo=1)
        _need_int({"v": self.tight_char_count}, "v", t, lo=0)
        if (self.end_page, self.end_line) < (self.start_page, self.start_line):
            _err(t, "结束位置不得早于起始位置")
        for name in ("node_id", "unassigned_reason", "table_scope", "table_reason",
                     "span_id"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, str) or v == ""):
                _err(t, f"{name} 必须为非空字符串或 None")
        for p in self.problems:
            if p not in DISPOSITION_PROBLEMS:
                _err(t, f"problems 必须属于 {DISPOSITION_PROBLEMS}，得到 {p!r}")

        # 空值真值表（封闭）。
        if self.range_kind == "unassigned":
            if self.node_id is not None:
                _err(t, "unassigned 范围的 node_id 必须为 None")
            if self.unassigned_reason not in UNASSIGNED_REASONS:
                _err(t, f"unassigned 范围必须给出登记原因，得到 {self.unassigned_reason!r}")
        else:
            if self.unassigned_reason is not None:
                _err(t, "非 unassigned 范围不得携带 unassigned_reason")
        if self.range_kind in ("table_inside", "table_adjacency"):
            vocab = _outline_vocab()
            if self.table_scope not in vocab["TABLE_SCOPES"]:
                _err(t, f"表范围必须给出 table_scope，得到 {self.table_scope!r}")
            if self.table_reason not in vocab["TABLE_REGION_REASONS"]:
                _err(t, f"表范围必须给出登记的 table_reason，得到 {self.table_reason!r}")
            if self.range_kind == "table_adjacency" and (
                    self.table_reason != vocab["TABLE_REGION_ADJACENT_REASON"]):
                _err(t, "table_adjacency 的 table_reason 必须为 "
                        f"{vocab['TABLE_REGION_ADJACENT_REASON']!r}")
        else:
            if self.table_scope is not None or self.table_reason is not None:
                _err(t, f"{self.range_kind} 不得携带 table_scope/table_reason")

        if self.range_kind == "regular":
            if self.node_id is None:
                _err(t, "regular 范围必须有 node_id")
            if self.left_boundary_cause not in BOUNDARY_CAUSES_LEFT:
                _err(t, f"regular 范围必须给出左边界成因，得到 {self.left_boundary_cause!r}")
            if self.right_boundary_cause not in BOUNDARY_CAUSES_RIGHT:
                _err(t, f"regular 范围必须给出右边界成因，得到 {self.right_boundary_cause!r}")
            if self.confidence is None:
                _err(t, "regular 范围必须给出 confidence")
            _need_num({"v": self.confidence}, "v", t, lo=0.0, hi=1.0)
            if self.span_id is None:
                _err(t, "regular 范围必须绑定 span_id")
        else:
            if self.left_boundary_cause is not None or self.right_boundary_cause is not None:
                _err(t, f"{self.range_kind} 不得携带边界成因（只有 regular run 有）")
            if self.confidence is not None:
                _err(t, f"{self.range_kind} 不得携带 confidence")
            if self.span_id is not None:
                _err(t, f"{self.range_kind} 不得绑定 span_id")

        expected_loc = locator("dr", self._position_payload())
        if self.disposition_locator != expected_loc:
            _err(t, "disposition_locator 与派生定位不一致："
                    f"{self.disposition_locator!r} != {expected_loc!r}")
        expected_id = identity("dr", self._identity_payload())
        if self.disposition_id != expected_id:
            _err(t, "disposition_id 与派生身份不一致："
                    f"{self.disposition_id!r} != {expected_id!r}")

    def _position_payload(self) -> dict:
        return {
            "start_page": self.start_page, "start_line": self.start_line,
            "end_page": self.end_page, "end_line": self.end_line,
            "range_kind": self.range_kind,
        }

    def _identity_payload(self) -> dict:
        payload = self._position_payload()
        payload.update({
            "disposition_locator": self.disposition_locator,
            "schema_version": self.schema_version,
            "span_builder_version": self.span_builder_version,
            "node_id": self.node_id,
            "unassigned_reason": self.unassigned_reason,
            "table_scope": self.table_scope,
            "table_reason": self.table_reason,
            "line_count": self.line_count,
            "tight_char_count": self.tight_char_count,
            "left_boundary_cause": self.left_boundary_cause,
            "right_boundary_cause": self.right_boundary_cause,
            "confidence": self.confidence,
            "span_id": self.span_id,
        })
        return payload

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "BodyRangeDisposition"
        payload["disposition_id"] = self.disposition_id
        payload["problems"] = list(self.problems)
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "BodyRangeDisposition":
        t = "BodyRangeDisposition"
        d = _reject_unknown(d, {
            "schema_type", "disposition_locator", "disposition_id", "schema_version",
            "span_builder_version", "range_kind", "node_id", "unassigned_reason",
            "table_scope", "table_reason", "start_page", "start_line", "end_page",
            "end_line", "line_count", "tight_char_count", "left_boundary_cause",
            "right_boundary_cause", "confidence", "span_id", "problems"}, t)
        _need_enum(d, "schema_type", t, ("BodyRangeDisposition",))
        raw_conf = d.get("confidence")
        if raw_conf is not None:
            raw_conf = _need_num({"v": raw_conf}, "v", t, lo=0.0, hi=1.0)
        return cls(
            disposition_locator=_need_str(d, "disposition_locator", t),
            disposition_id=_need_str(d, "disposition_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            span_builder_version=_need_str(d, "span_builder_version", t),
            range_kind=_need_enum(d, "range_kind", t, BODY_RANGE_KINDS),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            unassigned_reason=_need_str(d, "unassigned_reason", t, none_ok=True),
            table_scope=_need_str(d, "table_scope", t, none_ok=True),
            table_reason=_need_str(d, "table_reason", t, none_ok=True),
            start_page=_need_int(d, "start_page", t, lo=1),
            start_line=_need_int(d, "start_line", t, lo=0),
            end_page=_need_int(d, "end_page", t, lo=1),
            end_line=_need_int(d, "end_line", t, lo=0),
            line_count=_need_int(d, "line_count", t, lo=1),
            tight_char_count=_need_int(d, "tight_char_count", t, lo=0),
            left_boundary_cause=_need_str(d, "left_boundary_cause", t, none_ok=True),
            right_boundary_cause=_need_str(d, "right_boundary_cause", t, none_ok=True),
            confidence=raw_conf,
            span_id=_need_str(d, "span_id", t, none_ok=True),
            problems=_need_str_tuple(d, "problems", t),
        )

    @classmethod
    def create(cls, *, range_kind: str, node_id: str | None, unassigned_reason: str | None,
               table_scope: str | None, table_reason: str | None, start_page: int,
               start_line: int, end_page: int, end_line: int, line_count: int,
               tight_char_count: int, left_boundary_cause: str | None = None,
               right_boundary_cause: str | None = None,
               confidence: float | None = None, span_id: str | None = None,
               problems: tuple[str, ...] = ()) -> "BodyRangeDisposition":
        loc = locator("dr", {
            "start_page": start_page, "start_line": start_line,
            "end_page": end_page, "end_line": end_line, "range_kind": range_kind})
        payload = {
            "start_page": start_page, "start_line": start_line,
            "end_page": end_page, "end_line": end_line, "range_kind": range_kind,
            "disposition_locator": loc,
            "schema_version": V.BODY_RANGE_DISPOSITION_SCHEMA_VERSION,
            "span_builder_version": V.TS4_BODY_SPAN_BUILDER_VERSION,
            "node_id": node_id, "unassigned_reason": unassigned_reason,
            "table_scope": table_scope, "table_reason": table_reason,
            "line_count": line_count, "tight_char_count": tight_char_count,
            "left_boundary_cause": left_boundary_cause,
            "right_boundary_cause": right_boundary_cause,
            "confidence": confidence, "span_id": span_id,
        }
        return cls(
            disposition_locator=loc, disposition_id=identity("dr", payload),
            schema_version=V.BODY_RANGE_DISPOSITION_SCHEMA_VERSION,
            span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION,
            range_kind=range_kind, node_id=node_id,
            unassigned_reason=unassigned_reason, table_scope=table_scope,
            table_reason=table_reason, start_page=start_page, start_line=start_line,
            end_page=end_page, end_line=end_line, line_count=line_count,
            tight_char_count=tight_char_count,
            left_boundary_cause=left_boundary_cause,
            right_boundary_cause=right_boundary_cause, confidence=confidence,
            span_id=span_id, problems=tuple(problems),
        )


@dataclass(frozen=True)
class SpanEvidenceComponent:
    """`spc-1`：**一条把 Evidence 与某个范围关联起来的事实**（§18.8.1）。

    每条 component 都必须能回答"这条 Evidence 的**哪一段**落在**哪里**、为什么准入 /
    不准入"。`admitted=True` 当且仅当 `admission_reason == "aligned_projected"`。
    """

    component_locator: str
    component_id: str
    schema_version: str
    span_builder_version: str
    evidence_block_id: str
    terminal_kind: str
    terminal_id: str
    verdict: str
    refusal_reason: str | None
    residue_class: str | None
    evidence_char_range: tuple[int, int]
    span_local_char_range: tuple[int, int] | None
    node_id: str | None
    span_id: str | None
    disposition_id: str | None
    layout_hits: tuple[LayoutHit, ...]
    admitted: bool
    admission_reason: str
    landing: str

    SCHEMA_CONSTANT = "SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "SpanEvidenceComponent"
        for name in ("component_locator", "component_id", "evidence_block_id",
                     "terminal_kind", "terminal_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_span_builder_version(t, self.span_builder_version)
        if self.verdict not in COMPONENT_VERDICTS:
            _err(t, f"verdict 必须属于 {COMPONENT_VERDICTS}，得到 {self.verdict!r}")
        if self.landing not in COMPONENT_LANDINGS:
            _err(t, f"landing 必须属于 {COMPONENT_LANDINGS}，得到 {self.landing!r}")
        if self.admission_reason not in COMPONENT_ADMISSION_REASONS:
            _err(t, f"admission_reason 必须属于 {COMPONENT_ADMISSION_REASONS}，"
                    f"得到 {self.admission_reason!r}")
        if not isinstance(self.admitted, bool):
            _err(t, "admitted 必须为 bool")
        if self.admitted != (self.admission_reason == "aligned_projected"):
            _err(t, "admitted=True 当且仅当 admission_reason=='aligned_projected'")
        if not isinstance(self.evidence_char_range, tuple) or len(self.evidence_char_range) != 2:
            _err(t, "evidence_char_range 必须为二元组")
        _need_ordered_pairs({"_": [list(self.evidence_char_range)]}, "_", t)
        if self.span_local_char_range is not None:
            if (not isinstance(self.span_local_char_range, tuple)
                    or len(self.span_local_char_range) != 2):
                _err(t, "span_local_char_range 必须为二元组或 None")
            _need_ordered_pairs({"_": [list(self.span_local_char_range)]}, "_", t)
        if self.verdict == "refused":
            if not isinstance(self.refusal_reason, str) or self.refusal_reason == "":
                _err(t, "verdict='refused' 必须给出 refusal_reason")
        elif self.refusal_reason is not None:
            _err(t, "非拒绝对齐记录不得携带 refusal_reason")
        if self.landing == "alignment_residue_unmapped":
            if not isinstance(self.residue_class, str) or self.residue_class == "":
                _err(t, "residue 落点必须给出 residue_class")
        elif self.residue_class is not None:
            _err(t, "非 residue 落点不得携带 residue_class")
        for i, h in enumerate(self.layout_hits):
            if not isinstance(h, LayoutHit):
                _err(t, f"layout_hits[{i}] 必须为 LayoutHit")
        hits = [(h.page_number, h.line_index, h.layout_span_index,
                 h.span_char_range[0]) for h in self.layout_hits]
        if hits != sorted(hits):
            _err(t, "layout_hits 必须按 (page, line, span, span_char_start) 升序")
        # 落点真值表：只有 body_span 能携带 span_id；只有四种正文范围能携带 disposition_id。
        if self.landing == "body_span":
            if self.span_id is None or self.disposition_id is not None:
                _err(t, "body_span 落点必须有 span_id 且不得有 disposition_id")
        elif self.landing == "formal_unassigned":
            if self.span_id is None or self.disposition_id is not None:
                _err(t, "formal_unassigned 落点必须绑定 TS3 unassigned span_id")
        elif self.landing in ("table_inside", "table_adjacency", "body_unassigned",
                              "body_empty"):
            if self.disposition_id is None or self.span_id is not None:
                _err(t, f"{self.landing} 落点必须有 disposition_id 且不得有 span_id")
        else:
            if self.span_id is not None or self.disposition_id is not None:
                _err(t, f"{self.landing} 落点不得携带 span_id/disposition_id")
        if self.admitted and self.landing != "body_span":
            _err(t, "只有 body_span 落点可以被准入")
        expected_loc = locator("sc", {
            "evidence_block_id": self.evidence_block_id,
            "terminal_kind": self.terminal_kind, "terminal_id": self.terminal_id,
            "evidence_char_range": list(self.evidence_char_range),
            "landing": self.landing,
        })
        if self.component_locator != expected_loc:
            _err(t, "component_locator 与派生定位不一致："
                    f"{self.component_locator!r} != {expected_loc!r}")
        expected_id = identity("sc", self._identity_payload())
        if self.component_id != expected_id:
            _err(t, "component_id 与派生身份不一致："
                    f"{self.component_id!r} != {expected_id!r}")

    def _identity_payload(self) -> dict:
        return {
            "component_locator": self.component_locator,
            "schema_version": self.schema_version,
            "span_builder_version": self.span_builder_version,
            "evidence_block_id": self.evidence_block_id,
            "terminal_kind": self.terminal_kind,
            "terminal_id": self.terminal_id,
            "verdict": self.verdict,
            "refusal_reason": self.refusal_reason,
            "residue_class": self.residue_class,
            "evidence_char_range": list(self.evidence_char_range),
            "span_local_char_range": (list(self.span_local_char_range)
                                      if self.span_local_char_range is not None else None),
            "node_id": self.node_id,
            "span_id": self.span_id,
            "disposition_id": self.disposition_id,
            "layout_hits": [h.to_dict() for h in self.layout_hits],
            "admitted": self.admitted,
            "admission_reason": self.admission_reason,
            "landing": self.landing,
        }

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "SpanEvidenceComponent"
        payload["component_id"] = self.component_id
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "SpanEvidenceComponent":
        t = "SpanEvidenceComponent"
        d = _reject_unknown(d, {
            "schema_type", "component_locator", "component_id", "schema_version",
            "span_builder_version", "evidence_block_id", "terminal_kind",
            "terminal_id", "verdict", "refusal_reason", "residue_class",
            "evidence_char_range", "span_local_char_range", "node_id", "span_id",
            "disposition_id", "layout_hits", "admitted", "admission_reason",
            "landing"}, t)
        _need_enum(d, "schema_type", t, ("SpanEvidenceComponent",))

        def _pair(key: str) -> tuple[int, int] | None:
            v = d.get(key)
            if v is None:
                return None
            return _need_ordered_pairs({key: [v]}, key, t)[0]

        return cls(
            component_locator=_need_str(d, "component_locator", t),
            component_id=_need_str(d, "component_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            span_builder_version=_need_str(d, "span_builder_version", t),
            evidence_block_id=_need_str(d, "evidence_block_id", t),
            terminal_kind=_need_str(d, "terminal_kind", t),
            terminal_id=_need_str(d, "terminal_id", t),
            verdict=_need_enum(d, "verdict", t, COMPONENT_VERDICTS),
            refusal_reason=_need_str(d, "refusal_reason", t, none_ok=True),
            residue_class=_need_str(d, "residue_class", t, none_ok=True),
            evidence_char_range=_pair("evidence_char_range"),
            span_local_char_range=_pair("span_local_char_range"),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            span_id=_need_str(d, "span_id", t, none_ok=True),
            disposition_id=_need_str(d, "disposition_id", t, none_ok=True),
            layout_hits=_need_children(d, "layout_hits", t, LayoutHit.from_dict),
            admitted=_need_bool(d, "admitted", t),
            admission_reason=_need_enum(d, "admission_reason", t,
                                        COMPONENT_ADMISSION_REASONS),
            landing=_need_enum(d, "landing", t, COMPONENT_LANDINGS),
        )

    @classmethod
    def create(cls, *, evidence_block_id: str, terminal_kind: str, terminal_id: str,
               verdict: str, evidence_char_range: tuple[int, int],
               landing: str, admitted: bool, admission_reason: str,
               refusal_reason: str | None = None, residue_class: str | None = None,
               span_local_char_range: tuple[int, int] | None = None,
               node_id: str | None = None, span_id: str | None = None,
               disposition_id: str | None = None,
               layout_hits: tuple[LayoutHit, ...] = ()) -> "SpanEvidenceComponent":
        loc = locator("sc", {
            "evidence_block_id": evidence_block_id, "terminal_kind": terminal_kind,
            "terminal_id": terminal_id,
            "evidence_char_range": list(evidence_char_range), "landing": landing})
        payload = {
            "component_locator": loc,
            "schema_version": V.SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION,
            "span_builder_version": V.TS4_BODY_SPAN_BUILDER_VERSION,
            "evidence_block_id": evidence_block_id, "terminal_kind": terminal_kind,
            "terminal_id": terminal_id, "verdict": verdict,
            "refusal_reason": refusal_reason, "residue_class": residue_class,
            "evidence_char_range": list(evidence_char_range),
            "span_local_char_range": (list(span_local_char_range)
                                      if span_local_char_range is not None else None),
            "node_id": node_id, "span_id": span_id,
            "disposition_id": disposition_id,
            "layout_hits": [h.to_dict() for h in layout_hits],
            "admitted": admitted, "admission_reason": admission_reason,
            "landing": landing,
        }
        return cls(
            component_locator=loc, component_id=identity("sc", payload),
            schema_version=V.SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION,
            span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION,
            evidence_block_id=evidence_block_id, terminal_kind=terminal_kind,
            terminal_id=terminal_id, verdict=verdict, refusal_reason=refusal_reason,
            residue_class=residue_class, evidence_char_range=evidence_char_range,
            span_local_char_range=span_local_char_range, node_id=node_id,
            span_id=span_id, disposition_id=disposition_id,
            layout_hits=tuple(layout_hits), admitted=admitted,
            admission_reason=admission_reason, landing=landing,
        )


@dataclass(frozen=True)
class SpanCitableCoverage:
    """`spv-1`：单个 span 的**可引用覆盖**（§18.8.3）。

    **闭环不变量**（构建与验证共用同一实现，见 `effective_citable_intervals`）：四分类
    两两不交且并集恰为 `[0, span_local_length)`；`effective_citable_intervals` 必须等于
    由四分类重算的结果。禁止跨 Evidence 求和、禁止 `min(1.0, ...)`、禁止"一条对齐
    Evidence ⇒ 整个 span 可引用"。
    """

    coverage_locator: str
    coverage_id: str
    schema_version: str
    span_builder_version: str
    span_id: str
    span_local_length: int
    normalization_only_intervals: tuple[ClosedInterval, ...]
    required_content_intervals: tuple[ClosedInterval, ...]
    citable_source_intervals: tuple[ClosedInterval, ...]
    non_citable_source_intervals: tuple[ClosedInterval, ...]
    uncovered_source_intervals: tuple[ClosedInterval, ...]
    effective_citable_intervals: tuple[ClosedInterval, ...]
    normalization_only_chars: int
    required_content_chars: int
    citable_source_chars: int
    non_citable_source_chars: int
    uncovered_source_chars: int
    effective_citable_chars: int
    covering_component_ids: tuple[str, ...]
    problems: tuple[str, ...]

    SCHEMA_CONSTANT = "SPAN_CITABLE_COVERAGE_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "SpanCitableCoverage"
        for name in ("coverage_locator", "coverage_id", "span_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_span_builder_version(t, self.span_builder_version)
        _need_int({"v": self.span_local_length}, "v", t, lo=0)
        for name in ("normalization_only_intervals", "required_content_intervals",
                     "citable_source_intervals", "non_citable_source_intervals",
                     "uncovered_source_intervals", "effective_citable_intervals"):
            if not isinstance(getattr(self, name), tuple):
                _err(t, f"{name} 必须为元组")
        # 归一化空白与"必需内容"必须 tile 整个 span 局部域；随后**内容三分类**
        # （可引用 / 不可引用 / 未覆盖）只在 `required_content` 上 tile —— 两者合并
        # 等价于"四分类 tile 全域"，因此这里直接按四分类做**唯一一次**判定。
        check_interval_partition(
            (("normalization_only", self.normalization_only_intervals),
             ("required_content", self.required_content_intervals)),
            self.span_local_length, t)
        check_interval_partition(
            (("normalization_only", self.normalization_only_intervals),
             ("citable_source", self.citable_source_intervals),
             ("non_citable_source", self.non_citable_source_intervals),
             ("uncovered_source", self.uncovered_source_intervals)),
            self.span_local_length, t)
        for name in ("normalization_only_chars", "required_content_chars",
                     "citable_source_chars", "non_citable_source_chars",
                     "uncovered_source_chars", "effective_citable_chars"):
            _need_int({name: getattr(self, name)}, name, t, lo=0)
        pairs = (
            ("normalization_only_chars", self.normalization_only_intervals),
            ("required_content_chars", self.required_content_intervals),
            ("citable_source_chars", self.citable_source_intervals),
            ("non_citable_source_chars", self.non_citable_source_intervals),
            ("uncovered_source_chars", self.uncovered_source_intervals),
            ("effective_citable_chars", self.effective_citable_intervals),
        )
        for name, ivs in pairs:
            if getattr(self, name) != interval_length(ivs):
                _err(t, f"{name}={getattr(self, name)} 与区间重算长度 "
                        f"{interval_length(ivs)} 不一致")
        expected_eff = effective_citable_intervals(
            self.normalization_only_intervals, self.citable_source_intervals,
            self.non_citable_source_intervals, self.uncovered_source_intervals,
            self.span_local_length)
        if tuple(self.effective_citable_intervals) != tuple(expected_eff):
            _err(t, "effective_citable_intervals 必须等于由四分类重算的结果")
        if len(set(self.covering_component_ids)) != len(self.covering_component_ids):
            _err(t, "covering_component_ids 不得重复")
        if tuple(sorted(self.covering_component_ids)) != self.covering_component_ids:
            _err(t, "covering_component_ids 必须按字典序升序")
        for p in self.problems:
            if p not in COVERAGE_PROBLEMS:
                _err(t, f"problems 必须属于 {COVERAGE_PROBLEMS}，得到 {p!r}")
        expected_loc = locator("cv", {"span_id": self.span_id,
                                      "span_local_length": self.span_local_length})
        if self.coverage_locator != expected_loc:
            _err(t, "coverage_locator 与派生定位不一致："
                    f"{self.coverage_locator!r} != {expected_loc!r}")
        expected_id = identity("cv", self._identity_payload())
        if self.coverage_id != expected_id:
            _err(t, "coverage_id 与派生身份不一致："
                    f"{self.coverage_id!r} != {expected_id!r}")

    def _identity_payload(self) -> dict:
        return {
            "coverage_locator": self.coverage_locator,
            "schema_version": self.schema_version,
            "span_builder_version": self.span_builder_version,
            "span_id": self.span_id,
            "span_local_length": self.span_local_length,
            "normalization_only_intervals": [iv.to_dict()
                                             for iv in self.normalization_only_intervals],
            "required_content_intervals": [iv.to_dict()
                                           for iv in self.required_content_intervals],
            "citable_source_intervals": [iv.to_dict()
                                         for iv in self.citable_source_intervals],
            "non_citable_source_intervals": [iv.to_dict()
                                             for iv in self.non_citable_source_intervals],
            "uncovered_source_intervals": [iv.to_dict()
                                           for iv in self.uncovered_source_intervals],
            "effective_citable_intervals": [iv.to_dict()
                                            for iv in self.effective_citable_intervals],
            "covering_component_ids": list(self.covering_component_ids),
        }

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "SpanCitableCoverage"
        payload["coverage_id"] = self.coverage_id
        payload["normalization_only_chars"] = self.normalization_only_chars
        payload["required_content_chars"] = self.required_content_chars
        payload["citable_source_chars"] = self.citable_source_chars
        payload["non_citable_source_chars"] = self.non_citable_source_chars
        payload["uncovered_source_chars"] = self.uncovered_source_chars
        payload["effective_citable_chars"] = self.effective_citable_chars
        payload["problems"] = list(self.problems)
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "SpanCitableCoverage":
        t = "SpanCitableCoverage"
        d = _reject_unknown(d, {
            "schema_type", "coverage_locator", "coverage_id", "schema_version",
            "span_builder_version", "span_id", "span_local_length",
            "normalization_only_intervals", "required_content_intervals",
            "citable_source_intervals", "non_citable_source_intervals",
            "uncovered_source_intervals", "effective_citable_intervals",
            "normalization_only_chars", "required_content_chars",
            "citable_source_chars", "non_citable_source_chars",
            "uncovered_source_chars", "effective_citable_chars",
            "covering_component_ids", "problems"}, t)
        _need_enum(d, "schema_type", t, ("SpanCitableCoverage",))
        return cls(
            coverage_locator=_need_str(d, "coverage_locator", t),
            coverage_id=_need_str(d, "coverage_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            span_builder_version=_need_str(d, "span_builder_version", t),
            span_id=_need_str(d, "span_id", t),
            span_local_length=_need_int(d, "span_local_length", t, lo=0),
            normalization_only_intervals=_need_intervals(
                d, "normalization_only_intervals", t),
            required_content_intervals=_need_intervals(
                d, "required_content_intervals", t),
            citable_source_intervals=_need_intervals(d, "citable_source_intervals", t),
            non_citable_source_intervals=_need_intervals(
                d, "non_citable_source_intervals", t),
            uncovered_source_intervals=_need_intervals(
                d, "uncovered_source_intervals", t),
            effective_citable_intervals=_need_intervals(
                d, "effective_citable_intervals", t),
            normalization_only_chars=_need_int(d, "normalization_only_chars", t, lo=0),
            required_content_chars=_need_int(d, "required_content_chars", t, lo=0),
            citable_source_chars=_need_int(d, "citable_source_chars", t, lo=0),
            non_citable_source_chars=_need_int(d, "non_citable_source_chars", t, lo=0),
            uncovered_source_chars=_need_int(d, "uncovered_source_chars", t, lo=0),
            effective_citable_chars=_need_int(d, "effective_citable_chars", t, lo=0),
            covering_component_ids=_need_str_tuple(d, "covering_component_ids", t),
            problems=_need_str_tuple(d, "problems", t),
        )

    @classmethod
    def create(cls, *, span_id: str, span_local_length: int,
               normalization_only_intervals: Sequence[Any],
               citable_source_intervals: Sequence[Any],
               non_citable_source_intervals: Sequence[Any],
               uncovered_source_intervals: Sequence[Any],
               covering_component_ids: tuple[str, ...],
               problems: tuple[str, ...] = ()) -> "SpanCitableCoverage":
        ccids = tuple(sorted(set(covering_component_ids)))
        norm = merge_intervals(normalization_only_intervals)
        citable = merge_intervals(citable_source_intervals)
        non_citable = merge_intervals(non_citable_source_intervals)
        uncovered = merge_intervals(uncovered_source_intervals)
        required = invert_intervals(norm, span_local_length)
        effective = effective_citable_intervals(norm, citable, non_citable, uncovered,
                                                span_local_length)
        loc = locator("cv", {"span_id": span_id,
                             "span_local_length": span_local_length})
        payload = {
            "coverage_locator": loc,
            "schema_version": V.SPAN_CITABLE_COVERAGE_SCHEMA_VERSION,
            "span_builder_version": V.TS4_BODY_SPAN_BUILDER_VERSION,
            "span_id": span_id, "span_local_length": span_local_length,
            "normalization_only_intervals": [iv.to_dict() for iv in norm],
            "required_content_intervals": [iv.to_dict() for iv in required],
            "citable_source_intervals": [iv.to_dict() for iv in citable],
            "non_citable_source_intervals": [iv.to_dict() for iv in non_citable],
            "uncovered_source_intervals": [iv.to_dict() for iv in uncovered],
            "effective_citable_intervals": [iv.to_dict() for iv in effective],
            "covering_component_ids": list(ccids),
        }
        return cls(
            coverage_locator=loc, coverage_id=identity("cv", payload),
            schema_version=V.SPAN_CITABLE_COVERAGE_SCHEMA_VERSION,
            span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION,
            span_id=span_id, span_local_length=span_local_length,
            normalization_only_intervals=norm, required_content_intervals=required,
            citable_source_intervals=citable,
            non_citable_source_intervals=non_citable,
            uncovered_source_intervals=uncovered,
            effective_citable_intervals=effective,
            normalization_only_chars=interval_length(norm),
            required_content_chars=interval_length(required),
            citable_source_chars=interval_length(citable),
            non_citable_source_chars=interval_length(non_citable),
            uncovered_source_chars=interval_length(uncovered),
            effective_citable_chars=interval_length(effective),
            covering_component_ids=tuple(sorted(set(covering_component_ids))),
            problems=tuple(problems),
        )


@dataclass(frozen=True)
class SpanConservation:
    """`spr-1`：三层守恒（§18.9）。

    - 文档层：`D_nonfurniture = H_heading ⊎ F_formal_unassigned ⊎ N_non_content ⊎ B_body`
    - 正文层：`B_body = S_regular ⊎ A_adjacent_provisional ⊎ T_inside_table
      ⊎ U_ts4_unassigned ⊎ E_empty`
    - Evidence 层：逐条 Evidence `[0, block_char_length)` 的三分法
    """

    conservation_locator: str
    conservation_id: str
    schema_version: str
    span_builder_version: str
    document_layer: tuple[LayerBucket, ...]
    body_layer: tuple[LayerBucket, ...]
    evidence_layer: tuple[EvidenceConservationRow, ...]
    conserved: bool
    gaps: tuple[ConservationGap, ...]

    SCHEMA_CONSTANT = "SPAN_CONSERVATION_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "SpanConservation"
        for name in ("conservation_locator", "conservation_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_span_builder_version(t, self.span_builder_version)
        if tuple(b.bucket for b in self.document_layer) != DOCUMENT_LAYER_BUCKETS:
            _err(t, f"document_layer 的桶必须恰为 {DOCUMENT_LAYER_BUCKETS} 且按序，"
                    f"得到 {tuple(b.bucket for b in self.document_layer)}")
        if tuple(b.bucket for b in self.body_layer) != BODY_LAYER_BUCKETS:
            _err(t, f"body_layer 的桶必须恰为 {BODY_LAYER_BUCKETS} 且按序，"
                    f"得到 {tuple(b.bucket for b in self.body_layer)}")
        for i, b in enumerate(self.document_layer):
            if not isinstance(b, LayerBucket):
                _err(t, f"document_layer[{i}] 必须为 LayerBucket")
        for i, b in enumerate(self.body_layer):
            if not isinstance(b, LayerBucket):
                _err(t, f"body_layer[{i}] 必须为 LayerBucket")
        if not isinstance(self.evidence_layer, tuple):
            _err(t, "evidence_layer 必须为元组")
        eids = [row.evidence_id for row in self.evidence_layer]
        if eids != sorted(eids):
            _err(t, "evidence_layer 必须按 evidence_id 升序")
        if len(set(eids)) != len(eids):
            _err(t, "evidence_layer 的 evidence_id 不得重复")
        if not isinstance(self.conserved, bool):
            _err(t, "conserved 必须为 bool")
        for g in self.gaps:
            if not isinstance(g, ConservationGap):
                _err(t, "gaps 元素必须为 ConservationGap")
        # `conserved` 必须同时为真于"未声明任何缺口"与"机械重算未发现不一致"。
        # 只查其中一个都会让"声明空 gaps 却掩盖数值不一致"或反之静默通过。
        expected_conserved = not self.gaps and not self._gaps_recomputed()
        if self.conserved != expected_conserved:
            _err(t, f"conserved 必须由重算决定（应为 {expected_conserved}）")
        expected_loc = locator("cs", {
            "document_layer": [b.to_dict() for b in self.document_layer],
            "body_layer": [b.to_dict() for b in self.body_layer],
        })
        if self.conservation_locator != expected_loc:
            _err(t, "conservation_locator 与派生定位不一致："
                    f"{self.conservation_locator!r} != {expected_loc!r}")
        expected_id = identity("cs", self._identity_payload())
        if self.conservation_id != expected_id:
            _err(t, "conservation_id 与派生身份不一致："
                    f"{self.conservation_id!r} != {expected_id!r}")

    def _gaps_recomputed(self) -> tuple[str, ...]:
        """独立重算守恒缺口（**不**采信调用方写入的 `gaps`）。"""
        out: list[str] = []
        dmap = {b.bucket: b for b in self.document_layer}
        body = dmap["body"]
        doc_lines = sum(b.line_count for b in self.document_layer)
        doc_chars = sum(b.tight_char_count for b in self.document_layer)
        if body.line_count != sum(b.line_count for b in self.body_layer):
            out.append(f"body 行数 {body.line_count} != 正文层之和 "
                       f"{sum(b.line_count for b in self.body_layer)}")
        if body.tight_char_count != sum(b.tight_char_count for b in self.body_layer):
            out.append(f"body 字符 {body.tight_char_count} != 正文层之和 "
                       f"{sum(b.tight_char_count for b in self.body_layer)}")
        for b in self.document_layer:
            if b.line_count < 0 or b.tight_char_count < 0:
                out.append(f"document_layer[{b.bucket}] 出现负值")
        for row in self.evidence_layer:
            if not row.conserved:
                out.append(f"evidence {row.evidence_id} 自身不守恒")
            for p in row.problems:
                out.append(f"evidence {row.evidence_id} 问题 {p}")
        return tuple(out)

    def _identity_payload(self) -> dict:
        return {
            "conservation_locator": self.conservation_locator,
            "schema_version": self.schema_version,
            "span_builder_version": self.span_builder_version,
            "document_layer": [b.to_dict() for b in self.document_layer],
            "body_layer": [b.to_dict() for b in self.body_layer],
            "evidence_layer": [row.to_dict() for row in self.evidence_layer],
            "gaps": [g.to_dict() for g in self.gaps],
        }

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "SpanConservation"
        payload["conservation_id"] = self.conservation_id
        payload["conserved"] = self.conserved
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "SpanConservation":
        t = "SpanConservation"
        d = _reject_unknown(d, {
            "schema_type", "conservation_locator", "conservation_id",
            "schema_version", "span_builder_version", "document_layer", "body_layer",
            "evidence_layer", "conserved", "gaps"}, t)
        _need_enum(d, "schema_type", t, ("SpanConservation",))
        return cls(
            conservation_locator=_need_str(d, "conservation_locator", t),
            conservation_id=_need_str(d, "conservation_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            span_builder_version=_need_str(d, "span_builder_version", t),
            document_layer=_need_children(d, "document_layer", t, LayerBucket.from_dict),
            body_layer=_need_children(d, "body_layer", t, LayerBucket.from_dict),
            evidence_layer=_need_children(d, "evidence_layer", t,
                                          EvidenceConservationRow.from_dict),
            conserved=_need_bool(d, "conserved", t),
            gaps=_need_children(d, "gaps", t, ConservationGap.from_dict),
        )

    @classmethod
    def create(cls, *, document_layer: tuple[LayerBucket, ...],
               body_layer: tuple[LayerBucket, ...],
               evidence_layer: tuple[EvidenceConservationRow, ...],
               gaps: tuple[ConservationGap, ...] = ()) -> "SpanConservation":
        loc = locator("cs", {
            "document_layer": [b.to_dict() for b in document_layer],
            "body_layer": [b.to_dict() for b in body_layer],
        })
        payload = {
            "conservation_locator": loc,
            "schema_version": V.SPAN_CONSERVATION_SCHEMA_VERSION,
            "span_builder_version": V.TS4_BODY_SPAN_BUILDER_VERSION,
            "document_layer": [b.to_dict() for b in document_layer],
            "body_layer": [b.to_dict() for b in body_layer],
            "evidence_layer": [row.to_dict() for row in evidence_layer],
            "gaps": [g.to_dict() for g in gaps],
        }
        obj = cls(
            conservation_locator=loc, conservation_id=identity("cs", payload),
            schema_version=V.SPAN_CONSERVATION_SCHEMA_VERSION,
            span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION,
            document_layer=tuple(document_layer), body_layer=tuple(body_layer),
            evidence_layer=tuple(evidence_layer),
            conserved=not gaps, gaps=tuple(gaps),
        )
        return obj


@dataclass(frozen=True)
class SynopsisSourceValidation:
    """`nsv-1`：逐节点逐 snippet 的抽取式来源校验（§18.8.6）。

    简介只导航：本对象证明"片段确为 span 原文的连续切片且落在有效可引用区间内"，
    **不**把简介变成证据，也**不**参与 `set_complete`。
    """

    validation_locator: str
    validation_id: str
    schema_version: str
    synopsis_version: str
    node_id: str
    synopsis_id: str
    snippet_checks: tuple[SnippetCheck, ...]
    ok: bool
    problems: tuple[str, ...]

    SCHEMA_CONSTANT = "SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "SynopsisSourceValidation"
        for name in ("validation_locator", "validation_id", "node_id", "synopsis_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_ts4_synopsis_version(t, self.synopsis_version)
        for i, sc in enumerate(self.snippet_checks):
            if not isinstance(sc, SnippetCheck):
                _err(t, f"snippet_checks[{i}] 必须为 SnippetCheck")
            if sc.snippet_index != i:
                _err(t, f"snippet_checks[{i}].snippet_index 必须等于其位置 {i}")
        if not isinstance(self.ok, bool):
            _err(t, "ok 必须为 bool")
        for p in self.problems:
            if p not in SYNOPSIS_CHECK_PROBLEMS:
                _err(t, f"problems 必须属于 {SYNOPSIS_CHECK_PROBLEMS}，得到 {p!r}")
        recomputed = tuple(p for sc in self.snippet_checks for p in sc.problems)
        if self.problems != recomputed:
            _err(t, "problems 必须等于各 snippet check 的问题按序拼接")
        if self.ok != (not self.problems and bool(self.snippet_checks)):
            _err(t, "ok 当且仅当 snippet_checks 非空且 problems 为空")
        expected_loc = locator("sv", {"node_id": self.node_id,
                                      "synopsis_id": self.synopsis_id})
        if self.validation_locator != expected_loc:
            _err(t, "validation_locator 与派生定位不一致："
                    f"{self.validation_locator!r} != {expected_loc!r}")
        expected_id = identity("sv", self._identity_payload())
        if self.validation_id != expected_id:
            _err(t, "validation_id 与派生身份不一致："
                    f"{self.validation_id!r} != {expected_id!r}")

    def _identity_payload(self) -> dict:
        return {
            "validation_locator": self.validation_locator,
            "schema_version": self.schema_version,
            "synopsis_version": self.synopsis_version,
            "node_id": self.node_id,
            "synopsis_id": self.synopsis_id,
            "snippet_checks": [sc.to_dict() for sc in self.snippet_checks],
            "problems": list(self.problems),
        }

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "SynopsisSourceValidation"
        payload["validation_id"] = self.validation_id
        payload["ok"] = self.ok
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "SynopsisSourceValidation":
        t = "SynopsisSourceValidation"
        d = _reject_unknown(d, {
            "schema_type", "validation_locator", "validation_id", "schema_version",
            "synopsis_version", "node_id", "synopsis_id", "snippet_checks", "ok",
            "problems"}, t)
        _need_enum(d, "schema_type", t, ("SynopsisSourceValidation",))
        return cls(
            validation_locator=_need_str(d, "validation_locator", t),
            validation_id=_need_str(d, "validation_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            synopsis_version=_need_str(d, "synopsis_version", t),
            node_id=_need_str(d, "node_id", t),
            synopsis_id=_need_str(d, "synopsis_id", t),
            snippet_checks=_need_children(d, "snippet_checks", t, SnippetCheck.from_dict),
            ok=_need_bool(d, "ok", t),
            problems=_need_str_tuple(d, "problems", t),
        )

    @classmethod
    def create(cls, *, node_id: str, synopsis_id: str,
               snippet_checks: tuple[SnippetCheck, ...]) -> "SynopsisSourceValidation":
        checks = tuple(snippet_checks)
        for i, sc in enumerate(checks):
            if sc.snippet_index != i:
                _err("SynopsisSourceValidation",
                     f"snippet_checks[{i}].snippet_index 必须等于其位置 {i}")
        problems = tuple(p for sc in checks for p in sc.problems)
        loc = locator("sv", {"node_id": node_id, "synopsis_id": synopsis_id})
        payload = {
            "validation_locator": loc,
            "schema_version": V.SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION,
            "synopsis_version": V.SYNOPSIS_VERSION,
            "node_id": node_id, "synopsis_id": synopsis_id,
            "snippet_checks": [sc.to_dict() for sc in checks],
            "problems": list(problems),
        }
        return cls(
            validation_locator=loc, validation_id=identity("sv", payload),
            schema_version=V.SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION,
            synopsis_version=V.SYNOPSIS_VERSION, node_id=node_id,
            synopsis_id=synopsis_id, snippet_checks=checks,
            ok=bool(checks) and not problems, problems=problems,
        )


#: §18.3.5 的封闭输入载荷分组：**字段数与顺序由规格钉死**（不得增删换序）。
#: 每个分组是一个扁平 tuple（标量元素），`evidence` 的第 7 位与 `terminals` 整体
#: 是"tuple 的 tuple"。任何长度、顺序或元素类型的偏离都在 `__post_init__` 里拒绝。
TRUSTED_INPUT_GROUPS: tuple[tuple[str, int], ...] = (
    ("page_layout", 6),
    ("outline", 8),
    ("structure", 5),
    ("evidence", 7),
    ("qualification", 13),
    ("rules", 5),
    ("handoff", 10),
)

#: `terminals` 每一项的字段数（§18.3.5 的终态元组）。
TRUSTED_INPUT_TERMINAL_WIDTH: int = 8

_TRUSTED_SCALARS = (str, int, float, bool, type(None))

#: 各组里**规格要求**的嵌套位置（值仍是 tuple，不是标量）。仅这两处：
#: `evidence[6]` = 逐成员身份元组，`qualification[5]` = 展平的 12×3 边界因子。
#: 其余位置一律必须为标量，因此"顺手塞一个 dict/list 进去"仍然被拒。
_TRUSTED_NESTED_INDEXES: dict[str, tuple[int, ...]] = {
    "evidence": (6,),
    "qualification": (5,),
}


def _jsonify(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_jsonify(v) for v in value]
    return value


def _detuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_detuple(v) for v in value)
    return value


def _need_scalar_seq(value: Any, where: str, typename: str) -> None:
    if not isinstance(value, tuple):
        _err(typename, f"{where} 必须为 tuple，得到 {type(value).__name__}")
    for j, item in enumerate(value):
        if not isinstance(item, _TRUSTED_SCALARS):
            _err(typename, f"{where}[{j}] 必须为标量（str/int/float/bool/None），"
                           f"得到 {type(item).__name__}")


@dataclass(frozen=True)
class TrustedBuildInput:
    """§18.3.5 的**封闭 typed 输入载荷**：`SpanBuildSnapshot.input_fingerprint` 的唯一来源。

    它不是"随便一组身份的快照"，而是把**上游每个受信根对象**的身份按规格固定的字段
    与顺序冻结下来：版式、大纲、结构快照、Evidence（含逐成员身份）、对齐终态、资格策略、
    规则版本、以及 TS3 handoff 自身的身份。因此"改一个上游字段再同步重算其他哈希"
    必然改变 `input_fingerprint`，验证器由此能独立重算并比对。

    字段**不得增删或换序**（§18.3.5 明示）；新增上游身份只能通过升版规格实现。
    """

    page_layout: tuple
    outline: tuple
    structure: tuple
    evidence: tuple
    terminals: tuple
    qualification: tuple
    rules: tuple
    handoff: tuple

    def __post_init__(self) -> None:
        t = "TrustedBuildInput"
        for name, length in TRUSTED_INPUT_GROUPS:
            group = getattr(self, name)
            if not isinstance(group, tuple):
                _err(t, f"{name} 必须为 tuple，得到 {type(group).__name__}")
            if len(group) != length:
                _err(t, f"{name} 必须恰为 {length} 元组，得到 {len(group)}")
            # 两处**规格要求**的嵌套元素（不是标量）：`evidence[6]` 是逐成员身份元组，
            # `qualification[5]` 是展平后的 12×3 边界因子。它们各自的形状由下面的
            # 专门校验（以及 §18.3.5 的固定顺序）负责，其余位置仍必须为标量。
            nested = _TRUSTED_NESTED_INDEXES.get(name, ())
            for j, item in enumerate(group):
                if j in nested:
                    if not isinstance(item, tuple):
                        _err(t, f"{name}[{j}] 必须为 tuple（规格要求的嵌套分组），"
                                f"得到 {type(item).__name__}")
                    continue
                if not isinstance(item, _TRUSTED_SCALARS):
                    _err(t, f"{name}[{j}] 必须为标量（str/int/float/bool/None），"
                            f"得到 {type(item).__name__}")
        members = self.evidence[6]
        if not isinstance(members, tuple):
            _err(t, "evidence[6] 必须为成员身份 tuple")
        for i, m in enumerate(members):
            _need_scalar_seq(m, f"evidence[6][{i}]", t)
        # 边界因子：12 个 `(side, cause, factor)` 展平后必须恰为 36 个标量。
        factors = self.qualification[5]
        _need_scalar_seq(factors, "qualification[5]", t)
        if len(factors) % 3 != 0:
            _err(t, f"qualification[5] 必须是 3 的整数倍（(side, cause, factor) 展平），"
                    f"得到 {len(factors)}")
        for i in range(0, len(factors), 3):
            if factors[i] not in ("left", "right"):
                _err(t, f"qualification[5][{i}] 必须为 'left'/'right'，"
                        f"得到 {factors[i]!r}")
        for i, term in enumerate(self.terminals):
            _need_scalar_seq(term, f"terminals[{i}]", t)
            if len(term) != TRUSTED_INPUT_TERMINAL_WIDTH:
                _err(t, f"terminals[{i}] 必须恰为 {TRUSTED_INPUT_TERMINAL_WIDTH} 元组，"
                        f"得到 {len(term)}")

    def to_dict(self) -> dict:
        payload: dict = {"schema_type": "TrustedBuildInput"}
        for name, _length in TRUSTED_INPUT_GROUPS:
            payload[name] = _jsonify(getattr(self, name))
        payload["terminals"] = _jsonify(self.terminals)
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "TrustedBuildInput":
        t = "TrustedBuildInput"
        allowed = {"schema_type"} | {name for name, _l in TRUSTED_INPUT_GROUPS} | {"terminals"}
        d = _reject_unknown(d, allowed, t)
        _need_enum(d, "schema_type", t, ("TrustedBuildInput",))
        kwargs = {}
        for name, length in TRUSTED_INPUT_GROUPS:
            v = d.get(name)
            if v is None:
                _err(t, f"缺必填字段: {name}")
            if not isinstance(v, list):
                _err(t, f"{name} 必须为数组")
            kwargs[name] = _detuple(v)
        if d.get("terminals") is None:
            _err(t, "缺必填字段: terminals")
        return cls(terminals=_detuple(d["terminals"]), **kwargs)


@dataclass(frozen=True)
class SpanBuildSnapshot:
    """`spn-1`：一次 span 构建的**完整不可变快照**（TS4-A 的唯一业务产物）。

    快照内**嵌入**所用的资格策略全文（自描述）；`input_fingerprint` 绑定受信
    TS3 handoff，`content_fingerprint` 绑定业务内容。两者都与受信 handoff 在
    `span_verifier` 中独立重算比对。
    """

    snapshot_locator: str
    snapshot_id: str
    schema_version: str
    span_builder_version: str
    synopsis_version: str
    qualification_policy_version: str
    document_id: str
    document_version: str
    page_layout_id: str
    outline_id: str
    alignment_schema_version: str
    alignment_id: str
    structure_snapshot_id: str
    trusted_input: TrustedBuildInput
    qualification_policy: SpanQualificationPolicy
    dispositions: tuple[BodyRangeDisposition, ...]
    spans: tuple[OutlineSpan, ...]
    inherited_unassigned_span_ids: tuple[str, ...]
    components: tuple[SpanEvidenceComponent, ...]
    coverages: tuple[SpanCitableCoverage, ...]
    conservation: SpanConservation
    synopses: tuple[NavigationSynopsis, ...]
    terminal_count: int
    input_fingerprint: str
    content_fingerprint: str

    SCHEMA_CONSTANT = "SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION"

    def __post_init__(self) -> None:
        t = "SpanBuildSnapshot"
        for name in ("snapshot_locator", "snapshot_id", "document_id",
                     "document_version", "page_layout_id", "outline_id",
                     "alignment_id", "structure_snapshot_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_own_schema_version(t, self.schema_version, self.SCHEMA_CONSTANT)
        _bind_span_builder_version(t, self.span_builder_version)
        _bind_ts4_synopsis_version(t, self.synopsis_version)
        _bind_algorithm_version(t, "qualification_policy_version",
                                self.qualification_policy_version,
                                "SPAN_QUALIFICATION_POLICY_VERSION")
        # 对齐终态的 wire 版本：当前或**已登记**旧版可读，未知版本 fail-closed。
        _need_alignment_schema_version(
            {"alignment_schema_version": self.alignment_schema_version},
            "alignment_schema_version", t)
        if not isinstance(self.qualification_policy, SpanQualificationPolicy):
            _err(t, "qualification_policy 必须为 SpanQualificationPolicy")
        # §18.3.5：`input_fingerprint` 的载荷就是 `trusted_input` 全文；顶层便利字段
        # 必须与它逐项一致，否则"顶层写 A、指纹绑 B"会成为一个合法但自相矛盾的快照。
        if not isinstance(self.trusted_input, TrustedBuildInput):
            _err(t, "trusted_input 必须为 TrustedBuildInput")
        for field_name, group, index in (("page_layout_id", "page_layout", 0),
                                         ("outline_id", "outline", 1),
                                         ("structure_snapshot_id", "structure", 0)):
            group_value = getattr(self.trusted_input, group)
            if group_value[index] != getattr(self, field_name):
                _err(t, f"{field_name} 必须等于 trusted_input.{group}[{index}]："
                        f"{getattr(self, field_name)!r} != {group_value[index]!r}")
        keys = [(d.start_page, d.start_line) for d in self.dispositions]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            _err(t, "dispositions 必须按 (start_page, start_line) 严格升序且不重复")
        for i, d in enumerate(self.dispositions):
            if not isinstance(d, BodyRangeDisposition):
                _err(t, f"dispositions[{i}] 必须为 BodyRangeDisposition")
        span_keys: list[tuple[int, int]] = []
        for i, sp in enumerate(self.spans):
            if not isinstance(sp, OutlineSpan):
                _err(t, f"spans[{i}] 必须为 OutlineSpan")
            # 与**容器自己**声明的算法比较，而不是与当前常量比较：容器的
            # `span_builder_version` 已在 `__post_init__` 开头经 `_bind_span_builder_version`
            # 钉死（生产路径唯一可能的值就是当前算法，只读 legacy 窗口内才是登记旧版）。
            # 因此这两句在生产下逐字等价，而在 legacy 读回时问的是正确的问题：
            # "这些 span 与它们所在的容器是同一套算法吗"。直接比当前常量会让每一份
            # 历史快照在容器层被拒——那正是"历史产物不得被当前全局常量重新解释"
            # 要防的事，而不是它的实现方式。
            if sp.span_builder_version != self.span_builder_version:
                _err(t, f"spans[{i}] 必须使用 TS4 正文算法 "
                        f"{self.span_builder_version!r}（与本快照相同），"
                        f"得到 {sp.span_builder_version!r}")
            if sp.role != "body":
                _err(t, f"spans[{i}].role 必须为 'body'，得到 {sp.role!r}")
            if sp.is_fallback or sp.is_cross_heading:
                _err(t, f"spans[{i}] 不得是 fallback 或 cross-heading")
            if sp.char_range != (0, len(sp.normalized_text)):
                _err(t, f"spans[{i}].char_range 必须为 (0, len(normalized_text))")
            span_keys.append((sp.start_anchor[0], sp.start_anchor[1]))
        if span_keys != sorted(span_keys) or len(set(span_keys)) != len(span_keys):
            _err(t, "spans 必须按起始锚点严格升序且不重复")
        if len(set(self.inherited_unassigned_span_ids)) != len(
                self.inherited_unassigned_span_ids):
            _err(t, "inherited_unassigned_span_ids 不得重复")
        if tuple(sorted(self.inherited_unassigned_span_ids)) != self.inherited_unassigned_span_ids:
            _err(t, "inherited_unassigned_span_ids 必须按字典序升序")
        ckeys = [(c.evidence_block_id, c.evidence_char_range[0],
                  c.evidence_char_range[1], c.landing) for c in self.components]
        if ckeys != sorted(ckeys) or len(set(ckeys)) != len(ckeys):
            _err(t, "components 必须按 (block, char_range, landing) 严格升序且不重复")
        for i, c in enumerate(self.components):
            if not isinstance(c, SpanEvidenceComponent):
                _err(t, f"components[{i}] 必须为 SpanEvidenceComponent")
        cov_ids = [c.span_id for c in self.coverages]
        if cov_ids != sorted(cov_ids) or len(set(cov_ids)) != len(cov_ids):
            _err(t, "coverages 必须按 span_id 严格升序且不重复")
        for i, c in enumerate(self.coverages):
            if not isinstance(c, SpanCitableCoverage):
                _err(t, f"coverages[{i}] 必须为 SpanCitableCoverage")
        if {c.span_id for c in self.coverages} != {s.span_id for s in self.spans}:
            _err(t, "coverages 的 span_id 集合必须恰好等于 spans 的 span_id 集合")
        if not isinstance(self.conservation, SpanConservation):
            _err(t, "conservation 必须为 SpanConservation")
        syn_keys = [n.node_id for n in self.synopses]
        if syn_keys != sorted(syn_keys) or len(set(syn_keys)) != len(syn_keys):
            _err(t, "synopses 必须按 node_id 严格升序且不重复")
        for i, n in enumerate(self.synopses):
            if not isinstance(n, NavigationSynopsis):
                _err(t, f"synopses[{i}] 必须为 NavigationSynopsis")
        _need_int({"v": self.terminal_count}, "v", t, lo=0)
        _check_fingerprint(t, self.input_fingerprint, self._input_payload())
        _check_fingerprint(t, self.content_fingerprint, self._content_payload())
        expected_loc = locator("sbs", {
            "document_version": self.document_version,
            "page_layout_id": self.page_layout_id, "outline_id": self.outline_id,
            "alignment_id": self.alignment_id,
            "span_builder_version": self.span_builder_version,
            "policy_id": self.qualification_policy.policy_id,
        })
        if self.snapshot_locator != expected_loc:
            _err(t, "snapshot_locator 与派生定位不一致："
                    f"{self.snapshot_locator!r} != {expected_loc!r}")
        expected_id = identity("sbs", self._identity_payload())
        if self.snapshot_id != expected_id:
            _err(t, "snapshot_id 与派生身份不一致："
                    f"{self.snapshot_id!r} != {expected_id!r}")

    def _input_payload(self) -> dict:
        return self.trusted_input.to_dict()

    def _content_payload(self) -> dict:
        return {
            "qualification_policy": self.qualification_policy.to_dict(),
            "dispositions": [d.to_dict() for d in self.dispositions],
            "spans": [s.to_dict() for s in self.spans],
            "inherited_unassigned_span_ids": list(self.inherited_unassigned_span_ids),
            "components": [c.to_dict() for c in self.components],
            "coverages": [c.to_dict() for c in self.coverages],
            "conservation": self.conservation.to_dict(),
            "synopses": [n.to_dict() for n in self.synopses],
            "terminal_count": self.terminal_count,
        }

    def _identity_payload(self) -> dict:
        payload = {
            "document_id": self.document_id, "document_version": self.document_version,
            "page_layout_id": self.page_layout_id, "outline_id": self.outline_id,
            "alignment_id": self.alignment_id,
            "alignment_schema_version": self.alignment_schema_version,
            "structure_snapshot_id": self.structure_snapshot_id,
            "span_builder_version": self.span_builder_version,
        }
        payload["snapshot_locator"] = self.snapshot_locator
        payload["schema_version"] = self.schema_version
        payload["synopsis_version"] = self.synopsis_version
        payload["qualification_policy_version"] = self.qualification_policy_version
        payload["input_fingerprint"] = self.input_fingerprint
        payload["content_fingerprint"] = self.content_fingerprint
        return payload

    def to_dict(self) -> dict:
        payload = self._identity_payload()
        payload["schema_type"] = "SpanBuildSnapshot"
        payload["snapshot_id"] = self.snapshot_id
        payload["trusted_input"] = self.trusted_input.to_dict()
        payload["qualification_policy"] = self.qualification_policy.to_dict()
        payload["dispositions"] = [d.to_dict() for d in self.dispositions]
        payload["spans"] = [s.to_dict() for s in self.spans]
        payload["inherited_unassigned_span_ids"] = list(self.inherited_unassigned_span_ids)
        payload["components"] = [c.to_dict() for c in self.components]
        payload["coverages"] = [c.to_dict() for c in self.coverages]
        payload["conservation"] = self.conservation.to_dict()
        payload["synopses"] = [n.to_dict() for n in self.synopses]
        payload["terminal_count"] = self.terminal_count
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "SpanBuildSnapshot":
        t = "SpanBuildSnapshot"
        d = _reject_unknown(d, {
            "schema_type", "snapshot_locator", "snapshot_id", "schema_version",
            "span_builder_version", "synopsis_version", "qualification_policy_version",
            "document_id", "document_version", "page_layout_id", "outline_id",
            "alignment_schema_version", "alignment_id", "structure_snapshot_id",
            "trusted_input", "qualification_policy", "dispositions", "spans",
            "inherited_unassigned_span_ids", "components", "coverages",
            "conservation", "synopses", "terminal_count", "input_fingerprint",
            "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("SpanBuildSnapshot",))
        return cls(
            snapshot_locator=_need_str(d, "snapshot_locator", t),
            snapshot_id=_need_str(d, "snapshot_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                cls.SCHEMA_CONSTANT),
            span_builder_version=_need_str(d, "span_builder_version", t),
            synopsis_version=_need_str(d, "synopsis_version", t),
            qualification_policy_version=_need_str(d, "qualification_policy_version", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            outline_id=_need_str(d, "outline_id", t),
            alignment_schema_version=_need_str(d, "alignment_schema_version", t),
            alignment_id=_need_str(d, "alignment_id", t),
            structure_snapshot_id=_need_str(d, "structure_snapshot_id", t),
            trusted_input=_need_one(d, "trusted_input", t, TrustedBuildInput.from_dict),
            qualification_policy=_need_one(d, "qualification_policy", t,
                                           SpanQualificationPolicy.from_dict),
            dispositions=_need_children(d, "dispositions", t,
                                        BodyRangeDisposition.from_dict),
            spans=_need_children(d, "spans", t, OutlineSpan.from_dict),
            inherited_unassigned_span_ids=_need_str_tuple(
                d, "inherited_unassigned_span_ids", t),
            components=_need_children(d, "components", t,
                                      SpanEvidenceComponent.from_dict),
            coverages=_need_children(d, "coverages", t, SpanCitableCoverage.from_dict),
            conservation=_need_one(d, "conservation", t, SpanConservation.from_dict),
            # §19.0.1 方案 C：**aggregate 决定合法 leaf**。这里点名 TS4 的
            # `NavigationSynopsis` reader（`nss-2` / `ns-2`），而不是"按 child 的版本
            # 猜一个 reader"。TS5 的 `FinalNavigationSynopsis`（`nss-3` / `ns-3`）在
            # `schema_type` 与版本两处都会被这个 reader 拒绝，因此两代 synopsis 不可能
            # 混装进同一份 `spn-1`。
            synopses=_need_children(d, "synopses", t, NavigationSynopsis.from_dict),
            terminal_count=_need_int(d, "terminal_count", t, lo=0),
            input_fingerprint=_need_str(d, "input_fingerprint", t),
            content_fingerprint=_need_str(d, "content_fingerprint", t),
        )

    @classmethod
    def from_dict_legacy(cls, d: Any, *, declared_version: str
                         ) -> "SpanBuildSnapshot":
        """**只读**解码历史快照（`sb-8` 之前的正文算法），不得用于生产构造。

        为什么需要这条门：`sb-7` → `sb-8` 改变了跨页 `table_inside` 的处置构成
        （`sb-7` 把整条跨页 run 建成一条处置，`sb-8` 逐页分段），因此历史封存 run 的
        容器版本与每个 `dispositions[*].span_builder_version` 都是 `sb-7`。`from_dict`
        会把它们按"已退役旧版本"拒绝，于是**连读回都做不到**——而"历史产物读回"本身是
        一条独立的、必须成立的能力（历史 A 产物不得被当前全局常量重新解释）。

        三条边界，都可执行验证：

        1. **只接受已登记的 legacy 版本**。`declared_version` 必须分类为 `legacy`；
           传当前版本会被要求走 `from_dict`（不代劳），传未登记值 fail-closed（不猜）。
           这与 `versions.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS` 是同一份登记表，
           因此"新增可读旧版"必须改版本登记，而不是在本函数里加分支。
        2. **版本身份必须自洽**。载荷自带声明的 `span_builder_version` 必须逐字等于
           `declared_version`；否则调用方是在拿另一个版本的门票读这份载荷。
        3. **身份按该版本重算**。`snapshot_locator` / `snapshot_id` / `input_fingerprint`
           / `content_fingerprint` 以及每个叶子的 `*_id` 全部以**对象自己声明的**版本为
           载荷重算（见各 `_identity_payload`），所以身份这一关没有被放宽。

        与生产资格的分离：本入口**不**签发 `VerifiedSpanSnapshot` 能力，legacy 对象因
        而不可能被任何要求 `live` / `pinned_acceptance` 资格的消费点接受；生产消费者另有
        `span_builder_version == TS4_BODY_SPAN_BUILDER_VERSION` 的硬比较。
        """
        t = "SpanBuildSnapshot"
        if not isinstance(declared_version, str) or declared_version == "":
            _err(t, f"declared_version 必须为非空字符串，得到 {declared_version!r}")
        kind = V.classify_schema_version("TS4_BODY_SPAN_BUILDER_VERSION",
                                         declared_version)
        if kind == "current":
            _err(t, f"declared_version={declared_version!r} 是当前版本；"
                    "当前版本必须走 `from_dict`，legacy 门不代劳")
        if kind != "legacy":
            _err(t, f"declared_version={declared_version!r} 不是已登记的旧版本"
                    f"（{V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS}），fail-closed")
        if not isinstance(d, dict):
            _err(t, "legacy 读回的载荷必须为对象")
        found = d.get("span_builder_version")
        if found != declared_version:
            _err(t, f"载荷声明的 span_builder_version={found!r} 与 "
                    f"declared_version={declared_version!r} 不符："
                    "不得用另一个版本的门票读这份载荷")
        token = _LEGACY_READBACK_VERSION.set(declared_version)
        try:
            return cls.from_dict(d)
        finally:
            _LEGACY_READBACK_VERSION.reset(token)

    @classmethod
    def create(cls, *, qualification_policy: SpanQualificationPolicy,
               document_id: str, document_version: str, page_layout_id: str,
               outline_id: str, alignment_schema_version: str, alignment_id: str,
               structure_snapshot_id: str,
               trusted_input: TrustedBuildInput,
               dispositions: tuple[BodyRangeDisposition, ...],
               spans: tuple[OutlineSpan, ...],
               inherited_unassigned_span_ids: tuple[str, ...],
               components: tuple[SpanEvidenceComponent, ...],
               coverages: tuple[SpanCitableCoverage, ...],
               conservation: SpanConservation,
               synopses: tuple[NavigationSynopsis, ...],
               terminal_count: int) -> "SpanBuildSnapshot":
        sbv = V.TS4_BODY_SPAN_BUILDER_VERSION
        inp = {
            "document_id": document_id, "document_version": document_version,
            "page_layout_id": page_layout_id, "outline_id": outline_id,
            "alignment_id": alignment_id,
            "alignment_schema_version": alignment_schema_version,
            "structure_snapshot_id": structure_snapshot_id,
            "span_builder_version": sbv,
        }
        infp = sha256_canonical(trusted_input.to_dict())
        content = {
            "qualification_policy": qualification_policy.to_dict(),
            "dispositions": [x.to_dict() for x in dispositions],
            "spans": [x.to_dict() for x in spans],
            "inherited_unassigned_span_ids": list(inherited_unassigned_span_ids),
            "components": [x.to_dict() for x in components],
            "coverages": [x.to_dict() for x in coverages],
            "conservation": conservation.to_dict(),
            "synopses": [x.to_dict() for x in synopses],
            "terminal_count": terminal_count,
        }
        cfp = sha256_canonical(content)
        loc = locator("sbs", {
            "document_version": document_version, "page_layout_id": page_layout_id,
            "outline_id": outline_id, "alignment_id": alignment_id,
            "span_builder_version": sbv,
            "policy_id": qualification_policy.policy_id,
        })
        ident = dict(inp)
        ident.update({
            "snapshot_locator": loc,
            "schema_version": V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION,
            "synopsis_version": V.SYNOPSIS_VERSION,
            "qualification_policy_version": V.SPAN_QUALIFICATION_POLICY_VERSION,
            "input_fingerprint": infp,
            "content_fingerprint": cfp,
        })
        return cls(
            snapshot_locator=loc, snapshot_id=identity("sbs", ident),
            schema_version=V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION,
            span_builder_version=sbv, synopsis_version=V.SYNOPSIS_VERSION,
            qualification_policy_version=V.SPAN_QUALIFICATION_POLICY_VERSION,
            document_id=document_id, document_version=document_version,
            page_layout_id=page_layout_id, outline_id=outline_id,
            alignment_schema_version=alignment_schema_version,
            alignment_id=alignment_id, structure_snapshot_id=structure_snapshot_id,
            trusted_input=trusted_input, qualification_policy=qualification_policy,
            dispositions=tuple(dispositions), spans=tuple(spans),
            inherited_unassigned_span_ids=tuple(inherited_unassigned_span_ids),
            components=tuple(components), coverages=tuple(coverages),
            conservation=conservation, synopses=tuple(synopses),
            terminal_count=terminal_count, input_fingerprint=infp,
            content_fingerprint=cfp,
        )


# ---------------------------------------------------------------------------
# 6. 运行时能力登记表（反自证边界的**唯一**实现）
# ---------------------------------------------------------------------------
#
# 正式链路上的每一步都要问同一个问题：**"这个对象真的是本进程由正式签发路径产生的
# 那一个吗？"** 只靠字段自洽无法回答 —— `object.__new__`、`copy` / `deepcopy` /
# `pickle`、手写同形对象、甚至"把全部 ID / hash 同步重算一遍"都能造出一个字段完全
# 自洽的仿冒品。因此资格**不来自字段**，而来自"本进程签发登记表里有它"。
#
# 登记表用弱引用：对象一旦不再被持有即自动失效，且不会因为"登记过"而永远活着。
# 跨 scope 伪造（例如把 `testing` 签发的对象当 `live` 用）在此被拒。

CAPABILITY_KINDS: tuple[str, ...] = (
    "VerifiedCurrentEvidenceAuthority",
    "VerifiedPageLayout",
    "VerifiedEvidenceSetAlignment",
    "VerifiedTS3Handoff",
    "VerifiedSpanSnapshot",
    "VerifiedFinalMaterialStructureSnapshot",
)

#: 三个签发域**严格隔离**：`live` 是生产链路，`pinned_acceptance` 供版本化夹具走
#: 与生产同构的受信链路，`testing` 只服务反例测试。任何一步都可以要求"只接受某几个
#: scope"，因此"拿 testing 冒充 live / pinned_acceptance"不可能通过。
ISSUER_SCOPES: tuple[str, ...] = ("live", "pinned_acceptance", "testing")

_ISSUED_CAPABILITIES: dict[int, tuple[Any, str, str]] = {}
"""`id(obj) -> (weakref.ref(obj), kind, scope)`；弱引用保证对象不再被持有即失效。"""


class CapabilityError(SchemaValidationError):
    """能力对象不合格（未签发 / 跨 scope / 非本进程实例）。"""


def _issue_capability(obj: Any, kind: str, scope: str) -> Any:
    """**唯一**签发入口（仅由 `layout_builder` / `aligner` / `span_builder` /
    TS5 的 `final_verifier` 调用）。

    `final_verifier` 是 TS5 终端快照的**独立**签发者：它不得调用 `table_builder`
    或 `final_material_builder` 的任何 public 构建入口，只能复用本模块的低层叶子
    原语。签发登记表按对象身份（`id`）登记 kind 与 scope，因此"自行构造一个同形的
    `VerifiedFinalMaterialStructureSnapshot`"拿不到资格。
    """
    if kind not in CAPABILITY_KINDS:
        _err("_issue_capability", f"未登记的能力种类 {kind!r}（{CAPABILITY_KINDS}）")
    if scope not in ISSUER_SCOPES:
        _err("_issue_capability", f"未登记的签发域 {scope!r}（{ISSUER_SCOPES}）")
    _reap_issued()
    _ISSUED_CAPABILITIES[id(obj)] = (weakref.ref(obj), kind, scope)
    return obj


def _reap_issued() -> None:
    for key in [k for k, (ref, _kind, _scope) in _ISSUED_CAPABILITIES.items()
                if ref() is None]:
        del _ISSUED_CAPABILITIES[key]


def issued_capability(obj: Any, kind: str,
                      scopes: Sequence[str] | None = None) -> Any:
    """要求 `obj` 是**本进程签发的**、种类与签发域都合格的能力对象；否则 fail-closed。

    `scopes=None` 表示"任一合法签发域均可"。正式链路按步骤传入允许的域集合。
    """
    if kind not in CAPABILITY_KINDS:
        _err("issued_capability", f"未登记的能力种类 {kind!r}")
    if scopes is not None:
        allowed = tuple(scopes)
        for s in allowed:
            if s not in ISSUER_SCOPES:
                _err("issued_capability", f"允许集合含未登记签发域 {s!r}")
    else:
        allowed = ISSUER_SCOPES
    entry = _ISSUED_CAPABILITIES.get(id(obj))
    if entry is None or entry[0]() is not obj:
        raise CapabilityError(
            f"{kind}: 对象不是本进程由正式签发路径产生的实例；"
            f"`object.__new__` / copy / deepcopy / pickle / 字段仿造 /"
            f"同步重算全部 ID 与 hash 均不取得正式资格（fail-closed）")
    if entry[1] != kind:
        raise CapabilityError(
            f"{kind}: 对象登记的种类为 {entry[1]!r}，不得当作 {kind!r} 使用"
            f"（能力种类不可混用，fail-closed）")
    if entry[2] not in allowed:
        raise CapabilityError(
            f"{kind}: 对象的签发域为 {entry[2]!r}，不在允许集合 {allowed} 中；"
            f"live / pinned_acceptance / testing 严格隔离（fail-closed）")
    return obj


def capability_scope(obj: Any) -> str | None:
    """已签发对象的签发域；未签发返回 None（只读诊断，不构成资格）。"""
    entry = _ISSUED_CAPABILITIES.get(id(obj))
    if entry is None or entry[0]() is not obj:
        return None
    return entry[2]


def issued_capability_count() -> int:
    _reap_issued()
    return len(_ISSUED_CAPABILITIES)


# ---------------------------------------------------------------------------
# 7. 共享严格助手
# ---------------------------------------------------------------------------

def _need_one(d: dict, key: str, typename: str, decoder) -> Any:
    v = d.get(key)
    if v is None:
        _err(typename, f"缺必填字段: {key}")
    try:
        return decoder(v)
    except SchemaValidationError as e:
        _err(typename, f"{key} 解码失败：{e}")


def _check_own_schema_version(typename: str, value: Any, constant_name: str) -> None:
    """自有 schema 常量：**必须**恰等于 `versions.VERSION_CONSTANTS[constant_name]`。"""
    _check_registered_constant(constant_name)
    d = {"schema_version": value}
    _need_schema_version(d, "schema_version", typename, constant_name)


def _bind_algorithm_version(typename: str, field: str, value: Any,
                            constant_name: str) -> None:
    _check_registered_constant(constant_name)
    _check_version(typename, field, value, V.VERSION_CONSTANTS[constant_name],
                   constant_name)


def _bind_ts4_synopsis_version(typename: str, value: Any) -> None:
    """把 TS4 快照的 `synopsis_version` **精确钉到** TS4 轴（§19.0.1 方案 C）。

    本模块的 `SpanBuildSnapshot spn-1` 与 `SynopsisSourceValidation nsv-1` 是 TS4 的
    容器，只能承载 TS4 `NavigationSynopsis`（`nss-2` / `ns-2`）。TS5 的
    `FinalNavigationSynopsis` 走**另一条**版本轴（`nss-3` / `ns-3`），且**只**能进入
    `FinalMaterialStructureSnapshot fms-1`。

    为什么不用通用的 `_check_version` 了事：`ns-3` 在那里只会得到"必须为当前版本
    `ns-2`"，读起来像是"本容器的一个未来版本还没支持"，而不是"你拿的是另一个类型的
    版本"。二者对调用方的下一步动作完全不同——前者会诱导去加 reader（即把两个类型合回
    一个），后者才会去用正确的入口。因此这里单独给出方向性错误。

    只拒绝**精确**的 final 版本：其余非法值仍走 `_check_version` 的常规 fail-closed
    路径（已登记 legacy → "须显式迁移"；未知 → "必须为当前版本"）。
    """
    if value == V.FINAL_SYNOPSIS_VERSION:
        _err(typename,
             f"synopsis_version={value!r} 是 TS5 `FinalNavigationSynopsis` 的版本轴，"
             f"不得进入 TS4 容器（本容器只承载 TS4 `NavigationSynopsis`，"
             f"current 为 {V.SYNOPSIS_VERSION!r}）。final 简介只能进入 "
             f"`FinalMaterialStructureSnapshot fms-1`；两代 synopsis 不得混装、"
             f"不得按 child 猜 reader")
    _bind_algorithm_version(typename, "synopsis_version", value,
                            "SYNOPSIS_VERSION")


#: 只读 legacy 解码的**唯一**入场券（历史封存产物读回，见
#: `SpanBuildSnapshot.from_dict_legacy`）。默认 `None` ⇒ 生产路径完全不受影响。
#:
#: 为什么用 `ContextVar` 而不是参数：`span_builder_version` 是**叶子**字段
#: （`BodyRangeDisposition` / `OutlineSpan` / … 各自带一份），它们由 `_need_children`
#: 在容器 `from_dict` 内部构造，签名里没有、也不该有版本参数——给每个叶子加一个
#: "允许的旧版本"参数，等于把这条只读门复制到每一个 wire 类型上，那时它就不再是
#: "一条显式门"了。`ContextVar` 让允许值只在一个 `try/finally` 包住的解码窗口内生效，
#: 且天然并发安全（不共享可变全局）。
_LEGACY_READBACK_VERSION: ContextVar[str | None] = ContextVar(
    "span_schema_legacy_readback_version", default=None)


def _bind_span_builder_version(typename: str, value: Any) -> None:
    """TS4 正式记录的 span builder 版本：**只**接受 TS4 正文算法（不接受 `sb-1`）。

    唯一的放宽口是只读 legacy 解码窗口：窗口内、且值**确为已登记的旧版本**时才放行。
    放宽只针对"这个字符串是不是本轴的旧值"，**不**放宽任何身份校验——`snapshot_id` /
    `disposition_id` / 各指纹仍按对象自己声明的版本重算并逐字比对，所以"把版本改成
    `sb-7`、身份留在 `sb-8`"必然对不上（见 `from_dict_legacy` 的反例）。
    """
    if value == V.TS4_BODY_SPAN_BUILDER_VERSION:
        return
    allowed = _LEGACY_READBACK_VERSION.get()
    if allowed is not None and value == allowed and V.classify_schema_version(
            "TS4_BODY_SPAN_BUILDER_VERSION", value) == "legacy":
        return
    _err(typename, f"span_builder_version 必须为 TS4 正文算法 "
                   f"{V.TS4_BODY_SPAN_BUILDER_VERSION!r}，得到 {value!r}")


def _check_registered_constant(constant_name: str) -> None:
    if constant_name not in V.VERSION_CONSTANTS:
        raise SchemaValidationError(f"versions 未登记版本常量 {constant_name!r}")


def _check_fingerprint(typename: str, value: Any, payload: dict) -> None:
    if not isinstance(value, str) or len(value) != 64:
        _err(typename, "指纹必须为 64 位小写十六进制 sha256")
    expected = sha256_canonical(payload)
    if value != expected:
        _err(typename, f"指纹与载荷重算不一致：{value!r} != {expected!r}")


def tight_char_count(text: str) -> int:
    """`tight` 口径的字符数（守恒**只**按 tight 计数，空格差异不参与）。"""
    return len(tight(text))


# ---------------------------------------------------------------------------
# 8. 自检
# ---------------------------------------------------------------------------

#: §18.4.3 的子结构表：前 7 项为计划**点名**的类型，后 3 项为同一冻结口径下
#: 必要的精确计数/分桶子结构（计划允许"其余均采用 frozen 子结构"）。
SUBSTRUCTURE_TYPES: tuple[str, ...] = (
    "ClosedInterval", "LineStructureState", "BoundaryFactor", "LayoutHit",
    "EvidenceConservationRow", "ConservationGap", "SnippetCheck",
    "StateCount", "LayerBucket", "LandingCount", "TrustedBuildInput",
)

#: 仅运行时存在、**不得**序列化的能力对象（由 `verified` 层与 builder 内部持有）。
RUNTIME_ONLY_TYPE_NAMES: tuple[str, ...] = (
    "VerifiedPageLayout", "VerifiedCurrentEvidenceAuthority",
    "VerifiedEvidenceSetAlignment", "VerifiedTS3Handoff", "VerifiedSpanSnapshot",
    "SpanBuildInput",
)

SPAN_RECORD_TYPES: dict[str, Any] = {
    "OutlineStructureSnapshot": OutlineStructureSnapshot,
    "SpanQualificationPolicy": SpanQualificationPolicy,
    "BodyRangeDisposition": BodyRangeDisposition,
    "SpanEvidenceComponent": SpanEvidenceComponent,
    "SpanCitableCoverage": SpanCitableCoverage,
    "SpanConservation": SpanConservation,
    "SpanBuildSnapshot": SpanBuildSnapshot,
    "SynopsisSourceValidation": SynopsisSourceValidation,
}

SUBSTRUCTURE_REGISTRY: dict[str, Any] = {
    "ClosedInterval": ClosedInterval,
    "LineStructureState": LineStructureState,
    "BoundaryFactor": BoundaryFactor,
    "LayoutHit": LayoutHit,
    "EvidenceConservationRow": EvidenceConservationRow,
    "ConservationGap": ConservationGap,
    "SnippetCheck": SnippetCheck,
    "StateCount": StateCount,
    "LayerBucket": LayerBucket,
    "LandingCount": LandingCount,
    "TrustedBuildInput": TrustedBuildInput,
}


def self_check() -> dict:
    """本模块的机械自检（供 `evals.test_tree_span_schema` 与正式 verifier 调用）。"""
    problems: list[str] = []

    if set(SPAN_RECORD_TYPES) != set(V.SPAN_RECORD_PUBLIC_TYPES):
        problems.append("SPAN_RECORD_TYPES 与 versions.SPAN_RECORD_PUBLIC_TYPES 不一致："
                        f"{sorted(SPAN_RECORD_TYPES)} vs "
                        f"{sorted(V.SPAN_RECORD_PUBLIC_TYPES)}")
    for name, cls in SPAN_RECORD_TYPES.items():
        if cls.SCHEMA_CONSTANT not in V.VERSION_CONSTANTS:
            problems.append(f"{name}.SCHEMA_CONSTANT={cls.SCHEMA_CONSTANT!r} 未在 versions 登记")
            continue
        registered = V.SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS.get(name)
        if registered is None:
            problems.append(f"{name} 未登记进 SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS")
        elif registered != ("schema_version", cls.SCHEMA_CONSTANT):
            problems.append(f"{name} 的 (字段, 常量) 应为 "
                            f"{('schema_version', cls.SCHEMA_CONSTANT)!r}，"
                            f"注册表为 {registered!r}")
        for meth in ("to_dict", "from_dict", "create"):
            if not callable(getattr(cls, meth, None)):
                problems.append(f"{name} 缺 {meth}")
    for name in SUBSTRUCTURE_TYPES:
        if name not in SUBSTRUCTURE_REGISTRY:
            problems.append(f"子结构 {name} 未在本模块定义")
            continue
        cls = SUBSTRUCTURE_REGISTRY[name]
        for meth in ("to_dict", "from_dict"):
            if not callable(getattr(cls, meth, None)):
                problems.append(f"子结构 {name} 缺 {meth}")

    problems.extend(V.check_version_registry(
        V.SPAN_RECORD_PUBLIC_TYPES, V.SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS,
        substructure_names=SUBSTRUCTURE_TYPES,
        runtime_only_names=RUNTIME_ONLY_TYPE_NAMES))

    # 词表封闭性：不得重复、不得为空。
    for name, vocab in (
            ("BODY_ATTACHMENTS", BODY_ATTACHMENTS),
            ("BODY_RANGE_KINDS", BODY_RANGE_KINDS),
            ("COMPONENT_LANDINGS", COMPONENT_LANDINGS),
            ("COMPONENT_VERDICTS", COMPONENT_VERDICTS),
            ("COMPONENT_ADMISSION_REASONS", COMPONENT_ADMISSION_REASONS),
            ("POLICY_STAGES", POLICY_STAGES),
            ("BOUNDARY_CAUSES_LEFT", BOUNDARY_CAUSES_LEFT),
            ("BOUNDARY_CAUSES_RIGHT", BOUNDARY_CAUSES_RIGHT),
            ("CONSERVATION_LAYERS", CONSERVATION_LAYERS),
            ("CONSERVATION_GAP_REASONS", CONSERVATION_GAP_REASONS),
            ("COVERAGE_PROBLEMS", COVERAGE_PROBLEMS),
            ("DISPOSITION_PROBLEMS", DISPOSITION_PROBLEMS),
            ("EVIDENCE_ROW_PROBLEMS", EVIDENCE_ROW_PROBLEMS),
            ("SYNOPSIS_CHECK_PROBLEMS", SYNOPSIS_CHECK_PROBLEMS),
            ("DOCUMENT_LAYER_BUCKETS", DOCUMENT_LAYER_BUCKETS),
            ("BODY_LAYER_BUCKETS", BODY_LAYER_BUCKETS),
            ("STRUCTURE_SNAPSHOT_COUNT_KEYS", STRUCTURE_SNAPSHOT_COUNT_KEYS)):
        if not vocab:
            problems.append(f"词表 {name} 不得为空")
        if len(set(vocab)) != len(vocab):
            problems.append(f"词表 {name} 含重复项：{vocab}")

    vocab = _outline_vocab()
    if set(vocab["LINE_STRUCTURE_STATES"]) != {
            "heading_node", "formal_unassigned", "body_under_node", "non_content"}:
        problems.append(f"上游四态词表已变更：{vocab['LINE_STRUCTURE_STATES']}")
    if set(vocab["TABLE_SCOPES"]) != {"inside_table", "adjacent_to_table", "none"}:
        problems.append(f"上游表格范围词表已变更：{vocab['TABLE_SCOPES']}")
    if set(vocab["TABLE_REGION_REASONS"]) != set(
            vocab["TABLE_REGION_INSIDE_REASONS"]) | {vocab["TABLE_REGION_ADJACENT_REASON"]}:
        problems.append("上游表格区域原因码并集不一致")
    if set(BODY_ATTACHMENTS) != {
            "preceding_heading", "after_formal_unassigned_boundary",
            "before_first_heading"}:
        problems.append("BODY_ATTACHMENTS 含未登记值")
    if set(BODY_RANGE_KINDS) != {
            "regular", "table_adjacency", "table_inside", "unassigned", "empty"}:
        problems.append(f"BODY_RANGE_KINDS 必须恰为五分类，得到 {BODY_RANGE_KINDS}")
    if set(BODY_LAYER_BUCKETS) != {
            "regular", "table_adjacency_provisional", "table_inside", "unassigned",
            "empty"}:
        problems.append(f"BODY_LAYER_BUCKETS 必须恰为五分类，得到 {BODY_LAYER_BUCKETS}")
    if set(COMPONENT_LANDINGS) & set(BODY_RANGE_KINDS) - {"table_inside",
                                                          "table_adjacency"}:
        problems.append("落点词表与范围分类词表出现未登记的交叉项")

    # A/B 真值表：`versions` 是唯一权威；本模块只**报告**当前阶段，不再用它去
    # 约束任何策略记录（记录只对自身阶段自洽负责，当前阶段由 provider 在新建入口强制）。
    stage_map = {"TS4-A": "distribution_only", "TS4-B": "threshold_enabled"}
    expected_stage = stage_map.get(V.span_confidence_stage())
    if expected_stage is None:
        problems.append(f"未知 span confidence 阶段：{V.span_confidence_stage()!r}")
    if (V.SPAN_CONFIDENCE_MIN is None) != (expected_stage == "distribution_only"):
        problems.append("SPAN_CONFIDENCE_MIN 与阶段判定不自洽")

    if len(SPAN_RECORD_TYPES) != 8:
        problems.append(f"TS4 顶层对象必须恰为 8 个，得到 {len(SPAN_RECORD_TYPES)}")

    return {
        "span_record_type_count": len(SPAN_RECORD_TYPES),
        "substructure_type_count": len(SUBSTRUCTURE_TYPES),
        "runtime_only_type_count": len(RUNTIME_ONLY_TYPE_NAMES),
        "boundary_cause_count": len(BOUNDARY_CAUSES_LEFT) + len(BOUNDARY_CAUSES_RIGHT),
        "component_landing_count": len(COMPONENT_LANDINGS),
        "policy_stage": expected_stage,
        "span_confidence_stage": V.span_confidence_stage(),
        "problems": problems,
    }


def _main(argv: list[str]) -> int:
    import json
    if "--validate-only" not in argv:
        print("用法：python -m document_structure.span_schema --validate-only")
        return 2
    result = self_check()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result["problems"] else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))

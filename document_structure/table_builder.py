# -*- coding: utf-8 -*-
"""TS5 §19.5–§19.8：候选裁决、cell 组装、owner、decision 与 relation 的**私有纯核**。

本模块**不暴露 raw-root 入口**。唯一正式输入是 `TableBuildContext`：它的每个字段
都必须来自已验证的 `VerifiedSpanSnapshot` / `VerifiedTS3Handoff`，由
`final_material_builder` 在完成 §19.3.2 全部交叉核对后构造。本模块**不接受**
JSON、目录路径、裸 dict、自报字段，也不接受调用方拼装的 PageLayout /
DocumentOutline / Evidence / terminal 集合。

职责（§19.11.1）：

- candidates 枚举、去重与 §19.5.1 统一硬门；
- 行 / cell / 片段组装、cell provenance 与**逐段**可引用性；
- caption / unit / note 归属（**只按几何位置**，不读业务文字）；
- continuation（§19.8.2 六条证明）与 typed relations（§19.8.1）；
- §19.7.1 的 disposition 裁决计划。

**不在这里**：final span、component binding、citable coverage、四层守恒、
synopsis 与最终快照（见 `final_material_builder`）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import versions as V
from .aligner import AlignmentTerminal
from .canonical import SchemaValidationError
from .table_classification import (
    TableClassificationInputs,
    TableProfileBundle,
    classify_cell_block_role,
    classify_structure_class,
    evaluate_cell_block_signals,
    load_table_profile_bundle,
)
from .table_geometry import (
    BORDERLESS_CLUSTER_PARAMS,
    CANDIDATE_SOURCE_PRIORITY,
    GEOMETRY_PAGE_SOURCE,
    TABLE_GEOMETRY_DEFAULT_TOLERANCE,
    CellGridPlan,
    GeometryCandidate,
    TableGeometryReport,
    derive_cell_grid,
)
from .table_schema import (
    CANDIDATE_AUDIT_UNRESOLVED_REASONS,
    CELL_BLOCK_SEPARATOR,
    TableCandidateAuditRow,
    CONTINUATION_PROOF_KINDS,
    DECISION_RATIONALE_CODES,
    HEADER_ABSENCE_REASONS,
    MISSING_FIELD_CODES,
    RESOLVABLE_RELATION_KINDS,
    TABLE_DECISION_KINDS,
    TABLE_DECISION_TARGETS,
    TABLE_RELATION_BUILDER_VERSION,
    TABLE_SCHEMA_VERSION,
)

BUILDER_VERSION = V.TABLE_BUILDER_VERSION

#: 表格对象**只能**在 TS4 这两类 provisional 范围里成立（§19.5.1 / §19.7.1）。
TABLE_DISPOSITION_KINDS: tuple[str, ...] = ("table_inside", "table_adjacency")

#: 候选矩形必须**唯一**落入同一页 PageLayout 几何（§19.5.1 统一硬门第 1 条）。
CANDIDATE_CONTAINED_RATIO = 0.95

#: 表题 / 单位 / 表注与表体之间的**几何**邻近半径（PDF point，固定，不随调用方放大）。
PROXIMITY_PT = 12.0

#: 连续相邻冻结表范围的**相邻判据**上界（PDF point，固定，不随调用方放大）：两个
#: 同页冻结表范围的真实矩形之间垂直间隙 <= 此值、且横向有重叠，才可能属于同一张
#: 物理表被上游切成的相邻段（`tb-4` 的并集通道）。
#:
#: 取值依据是**实测分布**（三份上传材料的目标页，见 `§18.8.8`）：同一张表被按行段
#: 切开时，相邻两段的间隙 = 行间余量，实测 2.04–8.04 pt；而两个**不同**冻结块之间
#: 的间隙下界实测为 9.84 pt（≥ 一个真实行高 10.0 pt）。取 9.0 pt 落在两簇之间，且
#: 该值仍是**几何**量纲，与文档、公司、页码或表号无关。
UNION_ADJACENCY_GAP_PT = 9.0

#: 单位行判定：必须与表题处在**同一行带**内（垂直重叠 >= 此比例）且位于其右侧。
UNIT_SAME_BAND_MIN_OVERLAP = 0.5

#: 表题游程阈值（§二.1 / §二.2）：真实行内相邻两个非空片段之间的水平间隙若超过
#: `CAPTION_RUN_GAP_RATIO × 两片段中较小的字号`，它们不构成一条连续文本游程。
#: 量纲是**字符高度**（排版单位），不是字符数、关键词或页码，因此与语言无关；
#: 一个全角空格的间隙仍在这条游程内，一处真正的列间隙则不在。
CAPTION_RUN_GAP_RATIO = 1.2

#: 表题孤立性阈值（§二.3）：同栏相邻真实行与本行的**左边界**之差不超过此值即视为
#: 同一段折行正文（PDF point，固定，不随调用方放大）。
CAPTION_FLOW_LEFT_TOLERANCE = 2.0

#: 一个候选矩形覆盖页面多大比例以上就算"整页回退框"（§四.4/§四.5）。
#: 整页文本回退与真实图元拓扑同时出现时无法可靠区分，只能记未决。
PAGE_SPANNING_RATIO = 0.9

#: 普通段落 / 列表 / 数字散文反例门（§19.5.1 统一硬门最后一条）。
#:
#: 这些是**内容形状**阈值（字符数），不是公司 / 表号 / 关键词规则：散文被误检成表
#: 时，"单元格"会变成整段文字，表现为单元格文本异常长。与语言无关。
PROSE_CELL_CHAR_MIN = 60
PROSE_CELL_RATIO_MAX = 0.5
MAX_CELL_CHARS = 400
MAX_CELL_MEAN_CHARS = 120

#: §19.5.1 第 2、3 条通道在 TS5 内的诚实可用性登记（不可用必须显式记账，不得静默）。
CHANNEL_LIMITATIONS: tuple[str, ...] = (
    "explicit_caption_marker_geometry_only",
    "text_start_flag_not_geometry_evidence",
)


class TableBuildError(SchemaValidationError):
    """TS5 构建期 fail-closed 错误。"""


class UnsupportedStructureError(TableBuildError):
    """候选**诚实不可表达**为 `to-4`（例如整行被 rowspan 吞掉的网格）。

    它不是缺陷：这一条只把该候选降级为 `unsupported_table_structure` 并留下缺口，
    而不是中断整轮构建。除本类以外的 `TableBuildError` 一律向上抛（真缺陷）。
    """


def _err(msg: str) -> None:
    raise TableBuildError(msg)


def _overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """两个 bbox 的相交面积（无交集为 0）。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0.0 or h <= 0.0:
        return 0.0
    return w * h


def _area(b: Sequence[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _contains_box(outer: Sequence[float], inner: Sequence[float]) -> bool:
    """`inner` 是否被 `outer` **完全包含**（容差 = 几何默认容差）。

    容差只用几何轴的既有常量，不引入新的可调量：真实 PDF 的同一张表在不同通道
    下会被四舍五入到不同的末位，逐分量零容差比较会把"完全包含"误判为"越界"。
    """
    tol = TABLE_GEOMETRY_DEFAULT_TOLERANCE
    return (outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol
            and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol)


# ---------------------------------------------------------------------------
# 1. 构建上下文（唯一正式输入）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableBuildContext:
    """从**已验证能力**投影出来的只读上下文。

    构造它的唯一合法方式是 `final_material_builder` 在 §19.3.2 全部交叉核对通过后
    组装。本模块不提供任何"从 JSON / 路径 / 自报字段"构造它的入口。
    """

    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    outline_locator: str
    verified_span_snapshot_id: str
    source_file_sha256: str
    page_layout: Any
    outline: Any
    evidence_blocks: tuple
    terminals: tuple
    components: tuple
    dispositions: tuple
    spans: tuple
    qualification_policy: Any
    profiles: TableProfileBundle
    geometry: TableGeometryReport
    upstream_dependency_fingerprint: str
    _index: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        t = "TableBuildContext"
        for name in ("document_id", "document_version", "evidence_set_version",
                     "page_layout_id", "outline_id", "outline_locator",
                     "verified_span_snapshot_id", "source_file_sha256",
                     "upstream_dependency_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(f"{t}.{name} 必须为非空字符串")
        for name in ("source_file_sha256", "upstream_dependency_fingerprint"):
            if len(getattr(self, name)) != 64:
                _err(f"{t}.{name} 必须为 sha256")
        if self.page_layout.page_layout_id != self.page_layout_id:
            _err(f"{t}.page_layout 对象与 page_layout_id 不一致")
        if self.page_layout.document_version != self.document_version:
            _err(f"{t}.page_layout.document_version 与 document_version 不一致")
        if self.page_layout.source_file_sha256 != self.source_file_sha256:
            _err(f"{t}.page_layout 必须绑定同一份源 PDF 字节")
        if self.outline.outline_locator != self.outline_locator:
            _err(f"{t}.outline 对象与 outline_locator 不一致")
        if self.outline.outline_id != self.outline_id:
            _err(f"{t}.outline 对象与 outline_id 不一致")
        if self.outline.page_layout_id != self.page_layout_id:
            _err(f"{t}.outline 必须绑定同一 page_layout_id")
        if self.geometry.page_layout_id != self.page_layout_id:
            _err(f"{t}.几何报告必须绑定同一 page_layout_id")
        if self.geometry.source_file_sha256 != self.source_file_sha256:
            _err(f"{t}.几何报告必须绑定同一份源 PDF 字节")
        if self.geometry.settings.geometry_version != V.TABLE_GEOMETRY_VERSION:
            _err(f"{t}.几何报告版本不是当前登记版本")
        if self.profiles.classification.profile_version != \
                V.TABLE_CLASSIFICATION_PROFILE_VERSION:
            _err(f"{t}.classification profile 版本漂移")
        if self.profiles.cell_block.profile_version != \
                V.TABLE_CELL_BLOCK_PROFILE_VERSION:
            _err(f"{t}.cell_block profile 版本漂移")
        for name in ("terminals", "components", "dispositions", "spans",
                     "evidence_blocks"):
            if not isinstance(getattr(self, name), tuple):
                _err(f"{t}.{name} 必须为元组")

    # -- 派生索引（首次访问时构建，只读） ---------------------------------

    @property
    def _idx(self) -> dict:
        if not self._index:
            lines: dict = {}
            for page in self.page_layout.pages:
                for ln in page.lines:
                    lines[(page.page_number, ln.line_index)] = ln
            terms: dict = {}
            for term in self.terminals:
                if not isinstance(term, AlignmentTerminal):
                    _err("context.terminals 成员必须为 AlignmentTerminal")
                terms[term.evidence_block_id] = term
            comps: dict = {}
            for comp in self.components:
                comps[comp.component_id] = comp
            hits: dict = {}
            for comp in self.components:
                for hit in comp.layout_hits:
                    hits.setdefault(
                        (hit.page_number, hit.line_index,
                         hit.layout_span_index), []).append(comp)
            for key in hits:
                hits[key].sort(key=lambda c: (c.evidence_char_range[0],
                                              c.component_id))
            disps: dict = {}
            for d in self.dispositions:
                disps[d.disposition_id] = d
            pages: dict = {}
            for page in self.page_layout.pages:
                pages[page.page_number] = page
            object.__setattr__(self, "_index", {
                "lines": lines, "terminals": terms, "components": comps,
                "hits": hits, "dispositions": disps, "pages": pages,
            })
        return self._index

    def line_at(self, page_number: int, line_index: int) -> Any | None:
        return self._idx["lines"].get((page_number, line_index))

    def layout_span(self, page_number: int, line_index: int,
                    span_index: int) -> Any | None:
        ln = self.line_at(page_number, line_index)
        if ln is None or span_index >= len(ln.spans):
            return None
        return ln.spans[span_index]

    def page(self, page_number: int) -> Any | None:
        return self._idx["pages"].get(page_number)

    def terminal_by_block(self, evidence_block_id: str) -> Any | None:
        return self._idx["terminals"].get(evidence_block_id)

    def terminal_for(self, component: Any) -> AlignmentTerminal:
        term = self.terminal_by_block(component.evidence_block_id)
        if term is None:
            _err(f"组件 {component.component_id} 找不到对应终态（fail-closed）")
        return term

    def disposition_by_id(self, disposition_id: str) -> Any | None:
        return self._idx["dispositions"].get(disposition_id)

    def components_for_fragment(self, page_number: int, line_index: int,
                                span_index: int) -> list:
        return self._idx["hits"].get((page_number, line_index, span_index), [])

    def outline_node(self, node_id: str) -> Any | None:
        for node in self.outline.nodes:
            if node.node_id == node_id:
                return node
        return None

    def frame_fingerprint(self, page_number: int) -> str:
        for fr in self.geometry.frames:
            if fr.page_number == page_number:
                return fr.frame_fingerprint
        return ""


# ---------------------------------------------------------------------------
# 2. 候选枚举（§19.5.1 通道）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuilderCandidate:
    """一个候选：几何矩形 + 通道 + 它落在哪个 TS4 冻结范围里。它**仍然不是表**。"""

    candidate_source: str
    strategy: str
    page_number: int
    bbox: tuple
    geometry: GeometryCandidate
    frozen_disposition_id: str | None
    frozen_disposition_locator: str | None
    frozen_overlap_ratio: float
    competing_disposition_ids: tuple = ()
    #: 本候选是**连续相邻冻结表范围的并集**时，按上边界升序列出全部成员范围 id；
    #: 逐范围候选与几何候选一律为 `()`。它**只**决定准入依据码的具名
    #: （`contiguous_frozen_union`），不改变任何几何 / 形状 / 守恒语义。
    union_disposition_ids: tuple = ()

    def stable_key(self) -> tuple:
        return (self.page_number, tuple(self.bbox), self.candidate_source,
                self.strategy)


def frozen_range_bbox(ctx: TableBuildContext, disposition: Any) -> tuple | None:
    """冻结范围的**真实**物理矩形（由真实 LayoutLine bbox 求并，不是自报坐标）。

    只接受**单页**范围：跨页范围没有唯一物理矩形，且 §19.4.2 的表格对象是单页对象。
    """
    if disposition.start_page != disposition.end_page:
        return None
    xs0: list[float] = []
    ys0: list[float] = []
    xs1: list[float] = []
    ys1: list[float] = []
    for li in range(disposition.start_line, disposition.end_line + 1):
        ln = ctx.line_at(disposition.start_page, li)
        if ln is None or ln.is_furniture:
            continue
        xs0.append(ln.bbox[0])
        ys0.append(ln.bbox[1])
        xs1.append(ln.bbox[2])
        ys1.append(ln.bbox[3])
    if not xs0:
        return None
    box = (min(xs0), min(ys0), max(xs1), max(ys1))
    if _area(box) <= 0.0:
        return None
    return box


def _fragment_count(ctx: TableBuildContext, bbox: Sequence[float]) -> int:
    count = 0
    for ln in ctx._idx["lines"].values():
        if ln.is_furniture or _overlap(ln.bbox, bbox) <= 0.0:
            continue
        for sp in ln.spans:
            if _overlap(sp.bbox, bbox) > 0.0:
                count += 1
    return count


def _frozen_candidate(ctx: TableBuildContext, d: Any) -> BuilderCandidate | None:
    box = frozen_range_bbox(ctx, d)
    if box is None:
        return None
    frags = _fragment_count(ctx, box)
    geom = GeometryCandidate(
        candidate_source="frozen_range", strategy="lines",
        page_number=int(d.start_page), bbox=tuple(box),
        frame_fingerprint=ctx.frame_fingerprint(int(d.start_page)),
        row_count=0, column_count=0, cell_count=0,
        merged_cells=(), grid_cells=(), closed_grid=False,
        # 冻结范围通道**不带任何网格证据**（无边、无交点、无骨架），所以"闭合"
        # 从来未被证明：至少记 1 个结构缺口，fragment 为空时同样记 1。
        structural_gap_count=1,
        edge_count=0, intersection_count=0,
        source_fragment_count=frags, unresolved_reason=None)
    return BuilderCandidate(
        candidate_source="frozen_range", strategy="lines",
        page_number=int(d.start_page), bbox=tuple(box), geometry=geom,
        frozen_disposition_id=d.disposition_id,
        frozen_disposition_locator=d.disposition_locator,
        frozen_overlap_ratio=1.0, competing_disposition_ids=())


def _channel_rank(source: str) -> int:
    if source == "frozen_range":
        return 0
    if source in CANDIDATE_SOURCE_PRIORITY:
        return 1 + CANDIDATE_SOURCE_PRIORITY.index(source)
    return len(CANDIDATE_SOURCE_PRIORITY) + 1


def _frozen_owner(ctx: TableBuildContext, cand: GeometryCandidate) -> tuple:
    hits: list[tuple[str, float]] = []
    area = _area(cand.bbox)
    if area <= 0.0:
        return None, 0.0, ()
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != cand.page_number or d.start_page != d.end_page:
            continue
        box = frozen_range_bbox(ctx, d)
        if box is None:
            continue
        inter = _overlap(box, cand.bbox)
        if inter <= 0.0:
            continue
        hits.append((d.disposition_id, inter / area))
    if not hits:
        return None, 0.0, ()
    hits.sort(key=lambda x: (-x[1], x[0]))
    return hits[0][0], hits[0][1], tuple(h[0] for h in hits[1:])


def _union_extends(head_node_id: Any, prev_box: Sequence[float], cur_d: Any,
                   cur_box: Sequence[float], union_box: Sequence[float],
                   regular: Sequence[Sequence[float]]) -> bool:
    """串内**下一段**能否接上：枚举期与准入期共用的**唯一**判据（②③④）。

    ①（同页、真实矩形可回查）由调用方取矩形时给出；本函数只看"接得上"的三条：与串内
    上一段垂直相邻且横向有重叠、与串首同属一个已验证标题节点、接上后的并集不碰同页
    冻结 `regular` 正文。`union_box` 必须是**接上之后**的累积并集矩形：判据 ④ 按累积
    并集复核，因此同一串在枚举期与准入期得到同一结论（累积并集单调扩张，某一段接上时
    就已含该段，故逐段复核与整体复核等价）。

    判据 ③ 要的是**真实节点身份**：两端都非 `None` 且相等。两端同为 `None` 时本函数
    返回 `False`（分段），因为"都不知道"不是"同一个"的证据——否则同页相邻的**两张
    不同表**会仅因都没有已验证标题节点而被并成一串（乙：一张物理表对应一个有界候选）。
    """
    gap = cur_box[1] - prev_box[3]
    hov = min(cur_box[2], prev_box[2]) - max(cur_box[0], prev_box[0])
    if not (0.0 <= gap <= UNION_ADJACENCY_GAP_PT and hov > 0.0):
        return False
    # ③ "同属一个已验证标题节点"必须由**真实节点身份**证明：两端都非 `None` 且相等。
    #    两端同为 `None` **不是**证据——那只是"两段都没有已验证标题节点"，据此把两段
    #    并成一串等于用"都不知道"冒充"同一个"，同页相邻的**两张不同表**会因此被并成
    #    一个范围。证明不了就分段（宁可两段各自成候选，也不合并出一张不存在的表）。
    if head_node_id is None or cur_d.node_id is None:
        return False
    if cur_d.node_id != head_node_id:
        return False
    return not any(_overlap(b, union_box) > 0.0 for b in regular)


def _frozen_runs(ctx: TableBuildContext) -> tuple:
    """把冻结表范围按页聚成**连续相邻串**（`tb-4` 并集通道的输入）。

    这是"一张物理表被上游按行段切成多段"的**来源依据**：只有真实几何能证明相邻。
    串的延伸判据四条，缺一不可（全部只看真实几何与已验证结构，不读任何业务文字）：

    ① 同页、真实矩形可回查（`frozen_range_bbox` 非 `None`；不可回查的范围**断开**
       串 —— 不能证明"两段相邻"时宁可分段，且它在串序里占一个确定位置，见下）；
    ② 与串内上一段：垂直间隙 ∈ [0, `UNION_ADJACENCY_GAP_PT`] 且横向重叠 > 0；
    ③ 与串内首段同属一个 `node_id`，且两端该字段都**非 `None`** —— 一个表对象不得
       跨越两个已验证标题节点；两端同为 `None` 不构成同节点证据（见 `_union_extends`
       判据 ③），此时分段而不是合并；
    ④ 延伸后的并集矩形与同页冻结 `regular` 正文范围**无**真实重叠 —— 隔着正文的
       两段不是同一张表的相邻段。

    返回 `((disposition, ...), ...)`；长度 1 的串退化为"该范围自己"，与旧版逐范围
    候选逐字节一致。

    串序一律取**文档顺序**（`start_line`），不取 y 坐标：同一页可能有分栏，y 序与阅读
    序不一致；而"真实矩形不可回查"的范围只有在文档序里才有确定位置，取 y 序会把它们
    一律排到页尾，于是它们**断开不了**自己所在的那一串。准入期复核（`_contiguous_
    frozen_union`）按同一个键重排，两侧因此看同一条串。

    判据 ②③④ 的唯一实现在 `_union_extends`（准入期复核同一串时调用同一个函数），
    因此"枚举期怎么聚的串"与"准入期凭什么裁定"不会各写一套。
    """
    by_page: dict = {}
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != d.end_page:
            continue
        by_page.setdefault(int(d.start_page), []).append(d)
    runs: list[list] = []
    for page_number in sorted(by_page):
        entries: list[tuple] = []
        for d in by_page[page_number]:
            box = frozen_range_bbox(ctx, d)
            # 不可回查：它既不能入串，也不能被跨过 —— 在串序里占位以**断开**串。
            entries.append((d, box))
        entries.sort(key=lambda e: (e[0].start_line, e[0].disposition_id))
        regular = [b for b in (
            frozen_range_bbox(ctx, d) for d in ctx.dispositions
            if d.range_kind == "regular" and d.start_page == page_number) if b]
        for d, box in entries:
            if box is None:
                runs.append([])
                continue
            if not runs or not runs[-1]:
                runs.append([(d, box)])
                continue
            prev = runs[-1][-1][1]
            boxes = [b for _d, b in runs[-1]] + [box]
            nxt = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))
            if _union_extends(runs[-1][0][0].node_id, prev, d, box, nxt,
                              regular):
                runs[-1].append((d, box))
            else:
                runs.append([(d, box)])
    return tuple(tuple(ds for ds, _b in run) for run in runs if run)


def _frozen_union_candidate(ctx: TableBuildContext, ds: Sequence[Any],
                            box: Sequence[float]) -> BuilderCandidate:
    """由一串连续相邻冻结表范围构造**并集候选**（`tb-4`）。

    它**没有**网格骨架（与逐范围候选一样）：形状必须由 `derive_cell_grid` 从真实
    行列片段重建，因此"并集框里有网格"这件事不是自报的。owner 取**串首**（文档序
    第一段；串内全部成员同属一个已验证 `node_id`，见 `_frozen_runs` 判据 ③，因此
    owner 的选择不改变归属语义，但 `TableOwnerRef` 只回指这一段边界 —— 见 changelist
    的并集通道条目）。

    `frozen_overlap_ratio` 与 `competing_disposition_ids` 按 `_frozen_owner` 的口径
    **如实**填：并集比串内任何一段都大，所以 owner 的覆盖比必然 < 1、其余成员必然是
    竞争命中。它们的准入由 `_contiguous_frozen_union` 单独裁定，**不**靠把比值写成 1
    来绕开交占比门。
    """
    owner_box = frozen_range_bbox(ctx, ds[0])
    frags = _fragment_count(ctx, box)
    geom = GeometryCandidate(
        candidate_source="frozen_range", strategy="lines",
        page_number=int(ds[0].start_page), bbox=tuple(float(v) for v in box),
        frame_fingerprint=ctx.frame_fingerprint(int(ds[0].start_page)),
        row_count=0, column_count=0, cell_count=0,
        merged_cells=(), grid_cells=(), closed_grid=False,
        # 与 `_frozen_candidate` 同规：本通道**不带任何网格证据**，"闭合"从未被
        # 证明，至少记 1 个结构缺口。
        structural_gap_count=1,
        edge_count=0, intersection_count=0,
        source_fragment_count=frags, unresolved_reason=None)
    return BuilderCandidate(
        candidate_source="frozen_range", strategy="lines",
        page_number=int(ds[0].start_page), bbox=tuple(float(v) for v in box),
        geometry=geom,
        frozen_disposition_id=ds[0].disposition_id,
        frozen_disposition_locator=ds[0].disposition_locator,
        frozen_overlap_ratio=(_area(owner_box) / _area(box)) if owner_box
        else 0.0,
        competing_disposition_ids=tuple(d.disposition_id for d in ds[1:]),
        union_disposition_ids=tuple(d.disposition_id for d in ds))


def enumerate_candidates(ctx: TableBuildContext) -> tuple:
    """枚举全部通道的候选，并按 §19.5.1 硬门第 1 条标注冻结范围归属。

    通道顺序：TS4 冻结范围（逐范围 + `tb-4` 的连续相邻并集）→ 几何层候选
    （pdfplumber 线 / 文本 / borderless 聚类）。
    同 `(页, 矩形)` 只保留一个：优先级 = 通道顺序，再按"网格证据更强"决胜。
    """
    pool: list[BuilderCandidate] = []
    for run in _frozen_runs(ctx):
        # 逐范围候选**一律**出（`tb-3` 行为，含多成员串的每个成员）：并集候选是**另**
        # 一条形态，不是替代。串内成员若因此与并集候选同矩形，由下面的偏好决胜按
        # "片段的更强者优先"保留逐范围候选（并集框的片段数不少于任何成员）。
        for d in run:
            cand = _frozen_candidate(ctx, d)
            if cand is not None:
                pool.append(cand)
        if len(run) < 2:
            continue
        box = (min(frozen_range_bbox(ctx, d)[0] for d in run),
               min(frozen_range_bbox(ctx, d)[1] for d in run),
               max(frozen_range_bbox(ctx, d)[2] for d in run),
               max(frozen_range_bbox(ctx, d)[3] for d in run))
        pool.append(_frozen_union_candidate(ctx, run, box))
    for geom_cand in ctx.geometry.candidates:
        owner_id, ratio, others = _frozen_owner(ctx, geom_cand)
        pool.append(BuilderCandidate(
            candidate_source=geom_cand.candidate_source,
            strategy=geom_cand.strategy,
            page_number=geom_cand.page_number,
            bbox=tuple(geom_cand.bbox), geometry=geom_cand,
            frozen_disposition_id=owner_id,
            frozen_disposition_locator=(
                ctx.disposition_by_id(owner_id).disposition_locator
                if owner_id else None),
            frozen_overlap_ratio=ratio, competing_disposition_ids=others))
    best: dict = {}
    for cand in pool:
        key = (cand.page_number, tuple(cand.bbox))
        cur = best.get(key)
        if cur is None or _preference(cand) < _preference(cur):
            best[key] = cand
    return tuple(sorted(best.values(), key=lambda c: c.stable_key()))


def _preference(cand: BuilderCandidate) -> tuple:
    g = cand.geometry
    return (_channel_rank(cand.candidate_source),
            0 if g.closed_grid else 1,
            g.structural_gap_count,
            -g.source_fragment_count)


# ---------------------------------------------------------------------------
# 3. 统一硬门（§19.5.1）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    admitted: bool
    reason: str | None
    blocks_document: bool
    grid: CellGridPlan | None
    frozen_disposition_id: str | None
    #: 准入依据码（`CANDIDATE_ADMISSION_BASES`）。与 `reason` 互斥：`admitted` 才非
    #: `None`。它只说明"凭哪一条判据准入"，不改变任何下游语义，也不进 content
    #: fingerprint；持久化的唯一落点是 `TableCandidateAuditRow.admission_basis`。
    admission_basis: str | None = None


#: 不需要线条/交点自证即可作出 scope miss 断言的通道：`frozen_range` 由冻结范围本身
#: 证明归属；`layout_borderless_cluster` 由"多行多列内容闭合"证明，仍需
#: `derive_cell_grid` 复核（§19.12.2-5）。二者都**不**因此获得准入豁免。
SCOPE_CLASSIFICATION_EXEMPT_SOURCES: tuple[str, ...] = (
    "frozen_range", "layout_borderless_cluster",
)


def _has_geometric_grid_evidence(cand: BuilderCandidate) -> bool:
    """候选是否携带**独立于 pdfplumber 网格骨架的线条/交点证据**。

    注意这**不是**准入条件，而是 §19.5.3 `upstream_table_scope_miss` 的**断言资格**：
    该缺口等于"此处存在一张 TS4 冻结范围漏掉的表"。只有真实边、交点或闭合网格才能
    主张"此处存在表格几何"。

    `pdfplumber_text` 找不到表时会退化成**整页文本框**，其 `grid_cells` 因而非空
    （真实样本上出现 `rows=57 cols=1`、`rows=88 cols=3`、覆盖整页的矩形），但
    `edge_count` / `intersection_count` 全为 0。这样的候选若仍按 scope miss 判定，
    一个整页文本框就能把整份文档判为被阻断——那不是上游错了，是候选没有形状。

    反之，`pdfplumber_lines` 也可能给出 `edge_count == 0` 而确有 5×7 / 31 cell 的
    真实网格（真实样本 p8），因此本函数**只**用于阻断分类，**绝不**用于准入：否则
    会以"候选没有形状"为名静默缩小正式材料。
    """
    if cand.candidate_source in SCOPE_CLASSIFICATION_EXEMPT_SOURCES:
        return True
    g = cand.geometry
    return bool(g.closed_grid or g.edge_count > 0 or g.intersection_count > 0)


def _shape_viability(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> tuple[str | None, CellGridPlan | None]:
    """候选是否能重建为一张**形状合格**的表（§19.5.1 第 2–3 条）。

    返回 `(None, grid)` 表示形状合格；否则返回 `(拒绝理由, None)`。把这几条抽出来
    是因为它们既决定**准入**，也决定 §19.5.3 scope miss 的**断言资格**：一张 1 列
    13 行的竖排文字（pdfplumber `lines` 策略在正文上找到一条竖线就会给出这种
    "网格"）永远不可能被准入，因此它也无权主张"此处有一张 TS4 漏掉的表"。
    """
    page = ctx.page(cand.page_number)
    if page is None or ctx.frame_fingerprint(cand.page_number) == "":
        return "candidate_not_uniquely_mapped", None
    tol = TABLE_GEOMETRY_DEFAULT_TOLERANCE
    b = cand.bbox
    if (b[0] < -tol or b[1] < -tol
            or b[2] > page.width + tol or b[3] > page.height + tol):
        return "candidate_not_uniquely_mapped", None
    grid = derive_cell_grid(page, candidate=cand.geometry)
    if not grid.cells:
        return "grid_not_reconstructible", None
    if not grid.closed:
        return "grid_not_closed", None
    if grid.column_count < 2:
        return "column_count_below_min", None
    if grid.row_count < 2:
        return "row_count_below_min", None
    return None, grid


def _asserts_missing_table(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> bool:
    """候选是否有资格作出 `upstream_table_scope_miss` 断言（§19.5.3）。

    该缺口等于"此处存在一张 TS4 冻结范围漏掉的表"，因此断言方必须**同时**：
    携带线条/交点/闭合网格证据，且形状合格到本可成为一张表。两者缺一，这个候选
    就不是"漏掉的表"，只是"浮在正文上的伪表候选"——拒绝它是正确结果，不阻断文档。
    """
    if not _has_geometric_grid_evidence(cand):
        return False
    return _shape_viability(ctx, cand)[0] is None


def _overlaps_frozen_regular(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> bool:
    """候选矩形是否与**已冻结的 `regular` body span** 在同一页真实几何上重叠。

    §19.5.3 只在这一种情形下说"geometry 命中冻结 regular span"，也只有这一种情形
    属于必须交由上游 successor 修复的 scope miss。它与"候选浮在普通正文上、不属于
    任何冻结结构"必须分开——后者是 §19.5.1 第 1 条要拒绝的伪表候选，拒绝本身就是
    正确结果（§19.12.2-3：普通数字段落、编号列表、双栏排版不得成表），不阻断文档。
    """
    for d in ctx.dispositions:
        if d.range_kind != "regular":
            continue
        if d.start_page != cand.page_number:
            continue
        box = frozen_range_bbox(ctx, d)
        if box is None:
            continue
        if _overlap(box, cand.bbox) > 0.0:
            return True
    return False


def _contained_frozen_owner(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> bool:
    """§19.5.1 相邻冻结范围裁定：候选是否**有来源依据地**放宽"交占比 ≥ 0.95"。

    起因是一条可复现的几何情形（与文档、公司、页码无关）：候选从 pdfplumber 拿到
    **完整网格骨架**，`derive_cell_grid` 逐格闭合且 `problems` 为空；与此同时，TS4
    冻结范围只覆盖这张表的**主体**，候选比它多出来的只是同侧边框／行间余量，于是
    交占比落在 `[CANDIDATE_CONTAINED_RATIO, 1.0)` 之下。此时按"部分落入冻结范围"
    记 `candidate_straddles_frozen_range`，等于把一张**已自证网格**的表判成几何
    不可裁决——而这不是几何不可裁决，是交占比阈值对"带余量的同一张表"不适用。

    四条判据缺一不可，全部只看真实几何与候选自带证据，**不看**候选的申请理由：

    ① 候选自带 `grid_cells` 非空（不是靠几何猜出来的框；骨架由
       `derive_cell_grid` 的骨架路径消费）；
    ② owner 的真实矩形可回查（`frozen_range_bbox` 非 `None`）且被候选矩形**完全
       包含**（容差 = `TABLE_GEOMETRY_DEFAULT_TOLERANCE`）；
    ③ 同页 `table_inside`／`table_adjacency` 范围中，真实矩形被候选包含的**恰好
       一个**（即候选没有一口吞下第二张表；含 `None` 矩形的范围一律计入"不可回查"
       因而判否）；
    ④ 候选与同页冻结 `regular` body span 无真实重叠（多出来的部分不是无关正文）。

    它**只**替代交占比那一条：形状门（可重建／闭合／≥2 列 ≥2 行）照旧由
    `_shape_viability` 执行，因此"网格不闭合""列数不足"仍然如实拒绝。
    """
    g = cand.geometry
    if not g.grid_cells:
        return False
    owner = (ctx.disposition_by_id(cand.frozen_disposition_id)
             if cand.frozen_disposition_id else None)
    if owner is None:
        return False
    owner_box = frozen_range_bbox(ctx, owner)
    if owner_box is None or not _contains_box(cand.bbox, owner_box):
        return False
    contained: list = []
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != cand.page_number or d.end_page != cand.page_number:
            continue
        box = frozen_range_bbox(ctx, d)
        if box is None:
            # 该范围在候选同一页却无法回查真实矩形 ⇒ 不能证明"候选没有吞下它"。
            return False
        if _contains_box(cand.bbox, box):
            contained.append(d.disposition_id)
    if contained != [owner.disposition_id]:
        return False
    if _overlaps_frozen_regular(ctx, cand):
        return False
    return True


def _contained_frozen_run(ctx: TableBuildContext, cand: BuilderCandidate) -> bool:
    """§19.5.1 相邻冻结范围裁定（乙）：候选是**一整张物理表**，而上游把这张表按文字行段
    切成了多个相邻冻结范围。

    与 `_contained_frozen_owner`（"恰好一个" owner）的区别只有一条：这里允许候选包含
    **一段连续子串**（≥2 段）。起因同样是可复现的几何情形（与文档、公司、页码无关）：
    pdfplumber 的规则格架给出的候选矩形是**整张物理表**（表题行、完整表头、期间列、合计
    行、分块行与主体行都在一个闭合网格里），而 TS4 冻结范围只把这张表的**文字行**切成
    若干段，于是交占比远低于 `CANDIDATE_CONTAINED_RATIO`，按"部分落入冻结范围"记
    `candidate_straddles_frozen_range` 等于把一张**已自证网格**的完整原表判成几何不可
    裁决。它不是"几何不可裁决"，是"一张表的多段"——但**只有**真实几何能证明这一点。

    六条判据缺一不可，全部只看真实几何，**不看**候选的申请理由：

    ① 候选自带 `grid_cells` 非空（骨架来自真实规线，不是靠几何猜的框）；
    ② 同页 `table_inside`／`table_adjacency` 范围**全部**可回查真实矩形（任何一段不可
       回查 ⇒ 判否：证明不了候选没有吞下或切到它）；
    ③ 该页此类范围 ≥2 段，其中被候选**完全**包含（自身面积占比 ≥
       `CANDIDATE_CONTAINED_RATIO`）的那些构成"被包含集合"，其余**必须与候选零重叠**：
       任何"只落进来一部分"的范围都使判否 —— 那正是把一张表截取一半（§0.19 明令禁止）；
    ④ 被包含集合**恰好**是某一条 `_frozen_runs` 连续相邻串里的一段连续子串（≥2 段）：
       同页相邻**两张不同表**不会因此被并成一张——串本身要求各段同属一个**非空**且相等
       的已验证标题节点（见 `_union_extends` 判据 ③），且段间真实相邻、不跨冻结正文；
    ⑤ 候选与同页冻结 `regular` body span 无真实重叠（多出来的部分不是无关正文）；
    ⑥ 形状门（可重建／闭合／≥2 列 ≥2 行）照旧由 `_shape_viability` 执行，因此"网格不
       闭合""列数不足"仍然如实拒绝。

    它**只**替代交占比那一条，与 `contained_frozen_owner` 并列且互斥（后者要求恰好一段）。
    """
    g = cand.geometry
    if not g.grid_cells:
        return False
    page = int(cand.page_number)
    boxes: dict[str, tuple] = {}
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != page or d.end_page != page:
            continue
        box = frozen_range_bbox(ctx, d)
        if box is None:
            return False
        boxes[d.disposition_id] = box
    if len(boxes) < 2:
        return False
    contained: list[str] = []
    for did, box in boxes.items():
        area = _area(box)
        if area <= 0.0:
            return False
        inter = _overlap(box, cand.bbox)
        if inter / area >= CANDIDATE_CONTAINED_RATIO:
            contained.append(did)
        elif inter > 0.0:
            # 只落进来一部分 ⇒ 候选把这张表切开了（截取一半），判否。
            return False
    if len(contained) < 2:
        return False
    want = frozenset(contained)
    for run in _frozen_runs(ctx):
        if len(run) < 2 or int(run[0].start_page) != page:
            continue
        ids = [d.disposition_id for d in run]
        for i in range(len(ids) - 1):
            for j in range(i + 2, len(ids) + 1):
                if frozenset(ids[i:j]) == want:
                    return not _overlaps_frozen_regular(ctx, cand)
    return False


def evaluate_candidate_gate(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> GateResult:
    """§19.5.1 的统一硬门。**所有**通道都必须过同一道门。"""
    # 1. 只能落在 TS4 已冻结的表范围里。三种落空情形语义不同，**不得**合并成一个
    #    阻断理由——把它们混在一起会让"拒绝一张浮在正文上的伪表候选"和"几何与
    #    冻结正文冲突"都变成阻断性缺口，于是每份真实文档都会被判为被阻断。
    if cand.frozen_disposition_id is None:
        if _overlaps_frozen_regular(ctx, cand):
            if _asserts_missing_table(ctx, cand):
                # 几何覆盖已冻结 regular body span，且候选本可成为一张表：
                # 不得静默改成表格（T9 / §19.5.3），必须交上游 successor 修复。
                return GateResult(False, "geometry_overlaps_frozen_regular_span",
                                  True, None, None)
            # 无断言资格：按普通伪表候选拒绝，不阻断文档。理由取形状检查的结论，
            # 使拒绝理由如实反映"它为什么不是表"。
            shape_reason = _shape_viability(ctx, cand)[0]
            return GateResult(False, shape_reason or "no_geometric_grid_evidence",
                              False, None, None)
        # 不属于任何冻结结构：普通正文 / 视觉对象上的伪表候选，拒绝即正确结果。
        return GateResult(False, "outside_frozen_table_scope", False, None, None)
    if cand.union_disposition_ids:
        # 并集通道（`tb-4`）：候选矩形**就是**一串连续相邻冻结表范围的并集。它的
        # `frozen_overlap_ratio < 1` 是"上游把同一张表按行段切开"的必然结果，不是
        # "几何无法唯一落入冻结范围"，因此与逐范围候选分开裁定（§19.5.1 相邻冻结范围
        # 裁定的第二种）。裁定否 ⇒ 与逐范围候选遇交占比不足时**同一个**理由码。
        if not _contiguous_frozen_union(ctx, cand):
            return GateResult(False, "candidate_straddles_frozen_range", False,
                              None, cand.frozen_disposition_id)
        shape_reason, grid = _shape_viability(ctx, cand)
        if shape_reason is not None:
            return GateResult(False, shape_reason, False, None,
                              cand.frozen_disposition_id)
        return GateResult(True, None, False, grid, cand.frozen_disposition_id,
                          "contiguous_frozen_union")
    if cand.frozen_overlap_ratio < CANDIDATE_CONTAINED_RATIO:
        # 相邻冻结范围裁定（A1）：候选自带网格骨架且**恰好包含**其唯一冻结表范围、
        # 且多出来的部分不碰冻结正文 ⇒ 这是"同一张表的边框余量"，不是"几何无法唯一
        # 落入冻结范围"。裁定通过后仍必须过形状门。
        if _contained_frozen_owner(ctx, cand):
            shape_reason, grid = _shape_viability(ctx, cand)
            if shape_reason is not None:
                return GateResult(False, shape_reason, False, None,
                                  cand.frozen_disposition_id)
            return GateResult(True, None, False, grid,
                              cand.frozen_disposition_id,
                              "contained_frozen_owner")
        # 相邻冻结范围裁定（乙）：候选是**一整张物理表**，冻结范围只把它按文字行切成
        # 多段。判据见 `_contained_frozen_run`；它同样**只**替代交占比那一条。
        if _contained_frozen_run(ctx, cand):
            shape_reason, grid = _shape_viability(ctx, cand)
            if shape_reason is not None:
                return GateResult(False, shape_reason, False, None,
                                  cand.frozen_disposition_id)
            return GateResult(True, None, False, grid,
                              cand.frozen_disposition_id,
                              "contained_frozen_run")
        # 与冻结表范围**部分**重叠：几何无法唯一落入冻结范围，按 §19.5.3 的裁决顺序
        # 只能是 `unresolved_geometry`，不得强行选"第一个"。
        return GateResult(False, "candidate_straddles_frozen_range", False, None,
                          cand.frozen_disposition_id)
    # 同时被多个冻结范围覆盖、且谁也不完全包含 ⇒ 几何不可唯一裁决。
    if cand.competing_disposition_ids and cand.frozen_overlap_ratio < 1.0:
        return GateResult(False, "candidate_not_uniquely_mapped", False, None,
                          cand.frozen_disposition_id)
    # 2–3. 候选矩形唯一映射到真实 PageLayout 几何，且 cell 网格可重建、闭合、
    #      形状达标（≥2 列 ≥2 行）。
    shape_reason, grid = _shape_viability(ctx, cand)
    if shape_reason is not None:
        return GateResult(False, shape_reason, False, None,
                          cand.frozen_disposition_id)
    return GateResult(True, None, False, grid, cand.frozen_disposition_id,
                      "frozen_range_contained")


def _contiguous_frozen_union(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> bool:
    """并集通道的准入裁定（`tb-4`，§19.5.1 相邻冻结范围裁定的第二种）。

    候选的 `frozen_overlap_ratio < 1` 不是"几何无法唯一落入冻结范围"，而是**上游把
    同一张表按行段切成了多个相邻范围**的必然结果：候选矩形就是这一串范围真实矩形的
    并集，因而比其中任何一段都大。本裁定把它与"真正跨越两张表的候选"分开。四条判据
    缺一不可，全部只看真实几何，且**按 id 回查**、不采信候选自带的矩形与成员表：

    ① 成员数 ≥ 2，且每个成员都仍是同页冻结表范围、真实矩形可回查；
    ② 候选矩形逐分量等于成员真实矩形的并集（容差 = 几何默认容差）——候选不得比它
       声称的那一串**多出**任何面积；
    ③ 成员按**文档顺序**（`start_line`，与枚举期同一个键）逐对满足
       `_union_extends`（与枚举期同一判据，含"不碰同页冻结 `regular` 正文"）；
    ④ 候选矩形本身与同页冻结 `regular` 正文无真实重叠。

    它**只**替代交占比那一条：形状门（可重建／闭合／≥2 列 ≥2 行）照旧由
    `_shape_viability` 执行，因此"并集框里网格不闭合"仍然如实拒绝。
    """
    ids = tuple(cand.union_disposition_ids)
    if len(ids) < 2:
        return False
    members: list = []
    for did in ids:
        d = ctx.disposition_by_id(did)
        if d is None or d.range_kind not in TABLE_DISPOSITION_KINDS:
            return False
        if d.start_page != cand.page_number or d.end_page != d.start_page:
            return False
        box = frozen_range_bbox(ctx, d)
        if box is None:
            return False
        members.append((d, box))
    regular = [b for b in (frozen_range_bbox(ctx, d) for d in ctx.dispositions
                           if d.range_kind == "regular"
                           and d.start_page == cand.page_number) if b]
    if any(_overlap(b, cand.bbox) > 0.0 for b in regular):
        return False
    ordered = sorted(members, key=lambda m: (m[0].start_line,
                                             m[0].disposition_id))
    acc = ordered[0][1]
    for (_pd, prev), (cur_d, cur_box) in zip(ordered, ordered[1:]):
        union = (min(acc[0], cur_box[0]), min(acc[1], cur_box[1]),
                 max(acc[2], cur_box[2]), max(acc[3], cur_box[3]))
        if not _union_extends(ordered[0][0].node_id, prev, cur_d, cur_box,
                              union, regular):
            return False
        acc = union
    tol = TABLE_GEOMETRY_DEFAULT_TOLERANCE
    return all(abs(a - b) <= tol for a, b in zip(acc, cand.bbox))


def prose_negative_gate(cell_texts: Sequence[str]) -> str | None:
    """普通段落 / 列表 / 数字散文反例门（§19.5.1 最后一条）。

    判据只看**内容形状**：散文被误检成表时，"单元格"会变成整段文字。
    """
    lengths = [len(t) for t in cell_texts]
    if not lengths:
        return "prose_negative_gate"
    if max(lengths) > MAX_CELL_CHARS:
        return "cell_too_long"
    if sum(lengths) / len(lengths) > MAX_CELL_MEAN_CHARS:
        return "prose_negative_gate"
    if sum(1 for n in lengths if n >= PROSE_CELL_CHAR_MIN) / len(lengths) > \
            PROSE_CELL_RATIO_MAX:
        return "prose_negative_gate"
    return None


# ---------------------------------------------------------------------------
# 4. cell 组装与 provenance
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellAssembly:
    cell: Any
    #: `component_id -> tuple[(evidence_start, evidence_end), ...]`（源序）。
    consumed: dict
    #: 无法闭合到真实 terminal source 的片段键（诚实缺口，不静默丢弃）。
    unclosed: tuple


def _evidence_segments(ctx: TableBuildContext, comp: Any) -> list:
    """`[(span_off_start, span_off_end, page, line, span_index, ev_start, ev_end)]`。

    由**终态** `char_map` 得到：每段是"Evidence 块坐标 ↔ 真实布局位置"的 1:1 映射，
    因此没有任何字符偏移是猜出来的。

    每段再按**本组件自己**的 `evidence_char_range` 裁剪：同一个证据块里的一个真实
    片段可以同时被两个组件声明（各自的证据区间相邻，如 `(588,590)` 与 `(590,625)`），
    而 `char_map` 是**块级**的。不裁剪就会让一个组件引用到它自己区间之外的证据字符
    ——那正是独立复核器 `_partition_problems` 的 `range_out_of_component`。裁剪是
    精确的：一段 `char_map` 内证据偏移与片段内偏移之间是仿射的（同一 `offset` 基准），
    两端同裁不引入任何猜测。裁剪后本组件覆盖不到的字符就是**未覆盖**，由调用方
    按缺口处理，不得近似。
    """
    term = ctx.terminal_by_block(comp.evidence_block_id)
    if term is None:
        return []
    lo, hi = (int(v) for v in comp.evidence_char_range)
    out = []
    for seg in term.char_map:
        bs, be, page, line, span_index, offset = seg
        a = max(int(bs), lo)
        b = min(int(be), hi)
        if b <= a:
            continue
        s0 = int(offset) + (a - int(bs))
        out.append((s0, s0 + (b - a), int(page), int(line), int(span_index),
                    a, b))
    return out


def map_span_range_to_evidence(ctx: TableBuildContext, comp: Any,
                               page_number: int, line_index: int,
                               span_index: int, c0: int, c1: int) -> tuple | None:
    """把一段 span 内字符区间映射到 Evidence 字符区间；**不完全闭合**则返回 `None`。

    坐标域：`c0` / `c1` 与终态 `char_map` 的第 6 元一样，都是**片段内（span-local）**
    偏移（`_locate_tight_offset` 的返回口径），不是行内偏移。调用方必须先把
    `LayoutSpan.char_start/char_end`（行内域）换算过来。

    要求 `[c0, c1)` 被终态映射段**无缝覆盖**：任何未映射的字符都使这段来源不可
    引用（不得夹取、不得近似）。
    """
    pieces: list[tuple[int, int, int]] = []
    for (s0, s1, p, li, si, e0, _e1) in _evidence_segments(ctx, comp):
        if (p, li, si) != (page_number, line_index, span_index):
            continue
        a = max(s0, c0)
        b = min(s1, c1)
        if b <= a:
            continue
        pieces.append((a, b, e0 + (a - s0)))
    if not pieces:
        return None
    pieces.sort()
    cursor = c0
    start_ev: int | None = None
    end_ev: int | None = None
    for (a, b, ev) in pieces:
        if a != cursor:
            return None
        if start_ev is None:
            start_ev = ev
        end_ev = ev + (b - a)
        cursor = b
    if cursor != c1:
        return None
    if start_ev is None or end_ev is None:
        return None
    return (start_ev, end_ev)


def _mapped_extent(ctx: TableBuildContext, comp: Any, page_number: int,
                   line_index: int, span_index: int) -> tuple | None:
    """本组件在本片段上**被终态映射覆盖**的最大区间（片段内偏移）。

    `char_map` 是**块级**的，一段映射可以只覆盖一个布局片段的一部分：版式片段常带
    右对齐补白、缩排空白或行末折行空隙，这些字符在 Evidence 文本里**根本不存在**，
    因此它们既不是来源也不是缺口。取本片段所有映射段的并集外包；中间若仍留空隙，
    由 `map_span_range_to_evidence` 的无缝覆盖判定拒绝（不得夹取、不得近似）。
    """
    lo: int | None = None
    hi: int | None = None
    for (s0, s1, p, li, si, _e0, _e1) in _evidence_segments(ctx, comp):
        if (p, li, si) != (page_number, line_index, span_index):
            continue
        lo = s0 if lo is None else min(lo, s0)
        hi = s1 if hi is None else max(hi, s1)
    if lo is None or hi is None:
        return None
    return (lo, hi)


def make_source_ref(ctx: TableBuildContext, *, page_number: int,
                    line_index: int, span_index: int, span: Any, comp: Any) -> Any:
    """由真实 terminal source 派生一段来源（可引用性**逐段**判定）。"""
    from .table_schema import (TableCellSourceRef, TableSourceInterval,
                               is_non_semantic_whitespace)
    # `LayoutSpan.char_start/char_end` 是**行内**偏移，`char_map` 与
    # `TableSourceInterval.span_char_range` 都是**片段内**偏移：这里做唯一一次换算。
    full = span.char_end - span.char_start
    covered = _mapped_extent(ctx, comp, page_number, line_index, span_index)
    if covered is None:
        # 该片段不在任何终态 `char_map` 里（residue / offset 不可验证）：按
        # §19.12.2A(7) 它**不得进入 cell source ref**，只以 pending binding 保留。
        return None
    local0, local1 = covered
    if local0 < 0 or local1 > full or local1 <= local0:
        return None
    # 覆盖区间之外**必须**只是排版空白。判据与守恒层**同一词表**
    # （`is_non_semantic_whitespace`）：不得让"没有 typed gap"之类的信息影响这里的
    # 判定。只要有一个非空白字符落在覆盖区间外，本片段的来源就不完整——按
    # §19.12.2A(7) 拒绝，不得用「近似」补上。
    if not is_non_semantic_whitespace(span.text[:local0]) \
            or not is_non_semantic_whitespace(span.text[local1:]):
        return None
    ev = map_span_range_to_evidence(ctx, comp, page_number, line_index,
                                    span_index, local0, local1)
    if ev is None:
        return None
    term = ctx.terminal_for(comp)
    # 与 TS4 同一口径：refusal 终态不携带 verdict，其正式 verdict 恒为 `refused`。
    verdict = term.verdict if term.verdict is not None else "refused"
    refusal_reason = term.refusal_reason if verdict == "refused" else None
    interval = TableSourceInterval(
        page_number=page_number, line_index=line_index, span_index=span_index,
        span_char_range=(local0, local1), bbox=tuple(span.bbox))
    return TableCellSourceRef.create(
        interval=interval, component_id=comp.component_id,
        component_locator=comp.component_locator,
        evidence_block_id=comp.evidence_block_id,
        evidence_char_range=ev, terminal_kind=term.terminal_kind,
        terminal_id=term.terminal_id, terminal_locator=term.terminal_locator,
        terminal_schema_version=term.terminal_schema_version,
        verdict=verdict, refusal_reason=refusal_reason)


def _fragment_has_content(span: Any) -> bool:
    """片段是否携带**非空 tight 文本**。

    §19.9.3 第 3 层的分区元是"全部 **non-empty** LayoutSpan fragments"：纯空白
    片段（tight 为空）不属于该分区，因此既不进 cell，也不是缺口。
    """
    from .normalization import tight
    return bool(tight(span.text))


def assemble_cell(ctx: TableBuildContext, plan_cell: Any, *, page_number: int,
                  row: int) -> CellAssembly:
    """由一个 `GridCellPlan` 组装 cell（逐段真实来源 + 有序内部块）。"""
    refs: list = []
    unclosed: list = []
    for (line_index, span_start, span_end) in plan_cell.fragment_keys:
        for span_index in range(span_start, span_end + 1):
            span = ctx.layout_span(page_number, line_index, span_index)
            if span is None:
                unclosed.append((line_index, span_index))
                continue
            if not _fragment_has_content(span):
                continue
            ref = None
            for comp in ctx.components_for_fragment(page_number, line_index,
                                                    span_index):
                ref = make_source_ref(ctx, page_number=page_number,
                                      line_index=line_index,
                                      span_index=span_index, span=span, comp=comp)
                if ref is not None:
                    break
            if ref is None:
                unclosed.append((line_index, span_index))
                continue
            refs.append(ref)
    refs.sort(key=lambda r: (r.interval.page_number, r.interval.line_index,
                             r.interval.span_index,
                             r.interval.span_char_range[0]))
    consumed: dict = {}
    for ref in refs:
        consumed.setdefault(ref.component_id, []).append(ref.evidence_char_range)
    blocks = _blocks_from_refs(ctx, refs, unclosed=unclosed,
                               cell_width=float(plan_cell.bbox[2]
                                                - plan_cell.bbox[0]))
    return CellAssembly(
        cell=_make_cell(plan_cell, blocks=blocks, refs=refs, row=row,
                        unproven=tuple((page_number, li, si)
                                       for (li, si) in unclosed)),
        consumed={k: tuple(sorted(v)) for k, v in consumed.items()},
        unclosed=tuple(unclosed))


def _make_cell(plan_cell: Any, *, blocks, refs, row: int,
               unproven: Sequence[tuple[int, int, int]] = ()) -> Any:
    """组装 cell；**带 typed 缺口**时不再丢弃整张表（`tc-3`，§18.1 A3／T3）。

    `tc-2` 的语义是"无可回查来源 ⇒ 抛 `UnsupportedStructureError`"；实测（§18.8）表明
    这会把**整张表**连带丢弃：主营业务表的行标签列有一部分字符只以 `column_reorder`
    残差存在（`TREE_STRUCTURE_IMPLEMENTATION_PLAN.md:73`：该类**不单独决定引用资格**，
    表格重排由 `TableObject` 路径承接），而数字列是完整可回查的。因此这里改为记录
    `unproven_fragments`：每个落在本 cell 内、却无法闭合到真实 terminal source 的
    `(页, 行, 片段索引)` 逐条在册。cell 的 `text` **只**包含可回查字符，缺口不猜、不补。
    空 refs／空 blocks 仅当缺口非空时合法，因此"静默空 cell"仍不可能；表侧一定随之
    降为 `structure_state=partial`（`_missing_fields` → `cell_interior`）。

    §19.12.2A(7) 仍成立：未映射字符**不得进入 cell source ref**——它们只进缺口账。
    """
    from .table_schema import TableCellV4
    return TableCellV4.create(
        row=row, column=plan_cell.column, rowspan=plan_cell.rowspan,
        colspan=plan_cell.colspan, bbox=tuple(plan_cell.bbox),
        blocks=blocks, source_refs=refs, unproven_fragments=unproven)


def _blocks_from_refs(ctx: TableBuildContext, refs: Sequence[Any], *,
                      unclosed: Sequence[Any],
                      cell_width: float = 0.0) -> tuple:
    """按**真实行游程**把来源片段切成有序内部块，并逐块判角色（§19.6.2）。

    分组规则来自 `tcbp-1` 的 `line_group_rule`（同一 cell 内连续的 `(页, 行)` 游程，
    允许不超过阈值的小间隙）。表内小标题因此作为 `role="heading"` 的 cell block
    **保留**在 cell 内，而**不**成为 `OutlineNode`。
    """
    from .table_schema import TableCellBlock
    if not refs:
        return ()
    gap_limit = int(ctx.profiles.cell_block.line_group_rule
                    .max_intra_group_line_gap)
    groups: list[list] = []
    for ref in refs:
        if groups and _same_group(groups[-1][-1], ref, gap_limit):
            groups[-1].append(ref)
        else:
            groups.append([ref])
    blocks: list = []
    for i, group in enumerate(groups):
        text = group_text(ctx, group)
        signals = _cell_block_signals(
            ctx, group, group_count=len(groups), index=i,
            unclosed_inputs=bool(unclosed), cell_width=cell_width)
        role, _rationale = classify_cell_block_role(ctx.profiles.cell_block,
                                                    signals)
        blocks.append(TableCellBlock.create(
            block_index=i, role=role, text=text,
            source_ref_ids=tuple(r.source_ref_id for r in group)))
    return tuple(blocks)


def _same_group(a: Any, b: Any, gap_limit: int) -> bool:
    if a.interval.page_number != b.interval.page_number:
        return False
    gap = b.interval.line_index - a.interval.line_index
    return 0 <= gap <= gap_limit


def group_text(ctx: TableBuildContext, group: Sequence[Any]) -> str:
    """块文本必须**逐字符**取自真实 `LayoutSpan.text`（不允许改写或拼接来源）。

    §19.6.2 的冻结拼接规则：同一 `(页, 行)` 的片段直接相接，**相邻的不同
    `(页, 行)` 之间**插入一个 `CELL_BLOCK_SEPARATOR`。因此
    `len(text) == Σ片段长度 + (不同 (页,行) 组数 - 1)` 可被独立复核，块文本不是自报量。
    """
    parts: list[str] = []
    prev_key: tuple | None = None
    for ref in group:
        span = ctx.layout_span(ref.interval.page_number, ref.interval.line_index,
                               ref.interval.span_index)
        if span is None:
            _err("块文本无法从真实 LayoutSpan 取回（fail-closed）")
        key = (ref.interval.page_number, ref.interval.line_index)
        if prev_key is not None and key != prev_key:
            parts.append(CELL_BLOCK_SEPARATOR)
        a, b = ref.interval.span_char_range
        # `span_char_range` 已是**片段内**偏移，直接切 `LayoutSpan.text`。
        parts.append(span.text[a:b])
        prev_key = key
    return "".join(parts)


def _cell_block_signals(ctx: TableBuildContext, group: Sequence[Any], *,
                        group_count: int, index: int, unclosed_inputs: bool,
                        cell_width: float) -> Any:
    """求值 `tcbp-1` 允许的**全部**信号。

    这里只读字体 / 编号 / 换行 / 相对几何，不读任何业务文字。
    """
    text = group_text(ctx, group)
    sizes: list[float] = []
    bold_chars = 0
    total_chars = 0
    last_width = 0.0
    for ref in group:
        span = ctx.layout_span(ref.interval.page_number, ref.interval.line_index,
                               ref.interval.span_index)
        if span is None:
            continue
        n = ref.interval.span_char_range[1] - ref.interval.span_char_range[0]
        sizes.append(float(span.size))
        total_chars += n
        if span.is_bold:
            bold_chars += n
        ln = ctx.line_at(ref.interval.page_number, ref.interval.line_index)
        if ln is not None:
            last_width = max(last_width, ln.bbox[2] - ln.bbox[0])
    sizes.sort()
    median = sizes[len(sizes) // 2] if sizes else None
    cell_median = _cell_median_size(ctx, group)
    size_ratio = None
    if median is not None and cell_median:
        size_ratio = median / cell_median
    bold_ratio = (bold_chars / total_chars) if total_chars else 0.0
    return evaluate_cell_block_signals(
        text=text, line_group_count=group_count, own_line_group=True,
        followed_by_other_block_in_same_cell=index < group_count - 1,
        size_ratio=size_ratio, bold_ratio=bold_ratio,
        unclosed_inputs=unclosed_inputs, profile=ctx.profiles.cell_block)


def _cell_median_size(ctx: TableBuildContext, group: Sequence[Any]) -> float | None:
    sizes: list[float] = []
    for ref in group:
        span = ctx.layout_span(ref.interval.page_number, ref.interval.line_index,
                               ref.interval.span_index)
        if span is not None:
            sizes.append(float(span.size))
    if not sizes:
        return None
    sizes.sort()
    return sizes[len(sizes) // 2]


# ---------------------------------------------------------------------------
# 5. 行角色与表结构类型（§19.6.1）
# ---------------------------------------------------------------------------

def _row_is_header(cells: Sequence[Any]) -> bool:
    """表头判据**不是**"第一行不是数字"，而是"这一行的每个 cell 都被结构性判为 heading"。"""
    if not cells:
        return False
    return all(any(b.role == "heading" for b in c.blocks) for c in cells)


def assign_rows(cells_by_row: dict) -> tuple:
    """分配行角色。**不**凭文字猜小计 / 合计：没有真实行分节证据就不声称。"""
    from .table_schema import TableRowV4
    rows = sorted(cells_by_row)
    header = rows[0] if rows and _row_is_header(cells_by_row[rows[0]]) else None
    out: list = []
    for i, r in enumerate(rows):
        cells = tuple(sorted(cells_by_row[r], key=lambda c: c.column))
        out.append(TableRowV4(
            row_index=i, role=("header" if r == header else "body"),
            label=None, is_repeated_header=False, cells=cells))
    return tuple(out)


def determine_structure_kind(*, rows: Sequence[Any], geometry_backed: bool,
                             grid: CellGridPlan) -> tuple:
    """§19.6.1 真值表：`headered_grid` / `key_value_form` / `headerless_grid`。"""
    has_header = any(r.role == "header" for r in rows)
    body_rows = sum(1 for r in rows if r.role != "header")
    if has_header and geometry_backed and grid.column_count >= 2:
        return "headered_grid", None
    if not has_header and body_rows >= 2 and grid.column_count >= 2:
        if _looks_like_key_value(rows):
            return "key_value_form", "key_value_form_without_header"
        return "headerless_grid", "no_header_evidence"
    return "headerless_grid", "no_header_evidence"


def _looks_like_key_value(rows: Sequence[Any]) -> bool:
    """键值表判据：每行首列都是**短标签**，每行至少两个真实 cell。与语言无关。"""
    if len(rows) < 2:
        return False
    for row in rows:
        if len(row.cells) < 2:
            return False
        first = row.cells[0].text.strip()
        if not first or len(first) > 40 or CELL_BLOCK_SEPARATOR in first:
            return False
    return True


# ---------------------------------------------------------------------------
# 6. caption / unit / note（只按几何位置）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableAdornments:
    title_blocks: tuple
    unit_blocks: tuple
    unit_text: str | None
    note_blocks: tuple
    title_disposition_ids: tuple
    unit_disposition_ids: tuple
    note_disposition_ids: tuple
    source_refs: tuple


def disposition_band(ctx: TableBuildContext, d: Any) -> tuple | None:
    """冻结范围在**真实行 bbox**上的垂直行带 `(top, bottom)`。"""
    tops: list[float] = []
    bottoms: list[float] = []
    for li in range(d.start_line, d.end_line + 1):
        ln = ctx.line_at(d.start_page, li)
        if ln is None or ln.is_furniture:
            continue
        tops.append(ln.bbox[1])
        bottoms.append(ln.bbox[3])
    if not tops:
        return None
    return (min(tops), max(bottoms))


def _disposition_horizontal(ctx: TableBuildContext, d: Any) -> tuple:
    xs0: list[float] = []
    xs1: list[float] = []
    for li in range(d.start_line, d.end_line + 1):
        ln = ctx.line_at(d.start_page, li)
        if ln is None or ln.is_furniture:
            continue
        xs0.append(ln.bbox[0])
        xs1.append(ln.bbox[2])
    if not xs0:
        return (0.0, 0.0)
    return (min(xs0), max(xs1))


def _band_overlap(a: Sequence[float], b: Sequence[float]) -> float:
    height = min(a[1] - a[0], b[1] - b[0])
    if height <= 0:
        return 0.0
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / height


def caption_region_line_count(ctx: TableBuildContext, d: Any) -> int:
    """冻结范围里参与行带的**真实物理行数**（排除页眉页脚与空行）。"""
    count = 0
    for li in range(d.start_line, d.end_line + 1):
        ln = ctx.line_at(d.start_page, li)
        if ln is None or ln.is_furniture:
            continue
        if not any(_fragment_has_content(span) for span in ln.spans):
            continue
        count += 1
    return count


def _caption_region_line(ctx: TableBuildContext, d: Any) -> tuple:
    """冻结范围里**唯一**那条带内容的真实行 ⇒ `(line, page)`；不唯一则 `(None, None)`。"""
    found = None
    count = 0
    for li in range(d.start_line, d.end_line + 1):
        ln = ctx.line_at(d.start_page, li)
        if ln is None or ln.is_furniture:
            continue
        if not any(_fragment_has_content(span) for span in ln.spans):
            continue
        count += 1
        found = ln
    if count != 1:
        return None, None
    return found, ctx.page(d.start_page)


def _line_content_extent(line: Any) -> tuple | None:
    """真实行内**非空**片段在 x 上的并集 `(x0, x1)`；没有非空片段时为 `None`。"""
    spans = [sp for sp in line.spans if _fragment_has_content(sp)]
    if not spans:
        return None
    return (min(float(sp.bbox[0]) for sp in spans),
            max(float(sp.bbox[2]) for sp in spans))


def _caption_region_single_run(line: Any) -> bool:
    """该行是否构成**一条连续文本游程**（§二.1 / §二.2）。

    判据只看**排版量纲**：相邻两个非空片段之间的水平间隙若超过
    `CAPTION_RUN_GAP_RATIO × 两片段中较小的字号`，它们就不是同一段文字流。
    字号是 PDF 的真实版式属性，阈值因此与语言、公司、页码、表号无关：一段明显
    呈列状的行（多个相距甚远的片段）在同一行上就会在这里被否掉。
    """
    spans = [sp for sp in line.spans if _fragment_has_content(sp)]
    for a, b in zip(spans, spans[1:]):
        gap = float(b.bbox[0]) - float(a.bbox[2])
        scale = min(float(a.size), float(b.size))
        if gap > CAPTION_RUN_GAP_RATIO * scale:
            return False
    return True


def _caption_region_isolated(line: Any, page: Any) -> bool:
    """该行是否是**孤立**的文本行（§二.3）。

    "折行正文的一行"与"表题"在几何上唯一的区别是：前者一定与同栏内相邻真实行
    **共享左边界**（它是同一段文字的续行），后者不会。只要在同一页上找得到另一条
    真实行，其 x 区间与本行相交、左边界又与本行对齐（容差 `CAPTION_FLOW_LEFT_TOLERANCE`），
    本行就是正文流的一部分，不得当表题。这是版式不变量，不读任何业务文字。
    """
    own = _line_content_extent(line)
    if own is None:
        return False
    for other in page.lines:
        if other.line_index == line.line_index or other.is_furniture:
            continue
        ext = _line_content_extent(other)
        if ext is None:
            continue
        if ext[1] <= own[0] or ext[0] >= own[1]:
            continue
        if abs(ext[0] - own[0]) <= CAPTION_FLOW_LEFT_TOLERANCE:
            return False
    return True


def caption_region_proven(ctx: TableBuildContext, d: Any) -> bool:
    """表题/前导区域是否被**文本结构**共同证明，而不只是位置相邻（§19.7 / §二）。

    位置规则（矩形贴着表体上方、间隙 <= `PROXIMITY_PT`）只能说明"它挨着表"，
    说明不了"它是表题"。共证取三条与语言无关的版式不变量，**全部**成立才算证明：

    1. **唯一物理行**：一条占据多行的冻结范围是正文段落的一部分；
    2. **一条连续文本游程**：行内非空片段之间不得出现超过一个字号的水平间隙，
       明显呈列状的行或含多个相距甚远片段的行不是表题；
    3. **孤立行**：同栏内不得存在与本行左边界对齐且 x 区间相交的相邻真实行——
       那说明本行是折行正文的一行。

    该判据被 `build_adornments` 与 `absorbed_role` **共用**，因此"进了 title_blocks"
    与"裁决记成 `absorbed_as_caption`"不可能各自成立。不成立时调用方必须走既有的
    `insufficient_structure_proof` 分支（`unsupported_table_structure`），不得新增
    兜底词条；被否掉的前导文字因此落入缺口台账而不是被吞进表题（§二.6）。
    """
    line, page = _caption_region_line(ctx, d)
    if line is None or page is None:
        return False
    if not _caption_region_single_run(line):
        return False
    return _caption_region_isolated(line, page)


def refs_in_disposition(ctx: TableBuildContext, d: Any) -> tuple:
    """一个冻结范围里全部可回查的真实来源片段（源序）。"""
    refs: list = []
    for li in range(d.start_line, d.end_line + 1):
        ln = ctx.line_at(d.start_page, li)
        if ln is None or ln.is_furniture:
            continue
        for si, span in enumerate(ln.spans):
            if not _fragment_has_content(span):
                continue
            for comp in ctx.components_for_fragment(d.start_page, li, si):
                ref = make_source_ref(ctx, page_number=d.start_page,
                                      line_index=li, span_index=si, span=span,
                                      comp=comp)
                if ref is not None:
                    refs.append(ref)
                    break
    refs.sort(key=lambda r: (r.interval.page_number, r.interval.line_index,
                             r.interval.span_index,
                             r.interval.span_char_range[0]))
    return tuple(refs)


def _reindex(blocks: Sequence[Any]) -> tuple:
    from .table_schema import TableCellBlock
    return tuple(TableCellBlock.create(
        block_index=i, role=b.role, text=b.text, source_ref_ids=b.source_ref_ids)
        for i, b in enumerate(blocks))


def _dedup_refs(refs: Sequence[Any]) -> tuple:
    pool = {r.source_ref_id: r for r in refs}
    return tuple(sorted(pool.values(), key=lambda r: (
        r.interval.page_number, r.interval.line_index, r.interval.span_index,
        r.interval.span_char_range[0])))


def build_adornments(ctx: TableBuildContext, *, table_bbox: Sequence[float],
                     page_number: int, dispositions: Sequence[Any]) \
        -> TableAdornments:
    """按**几何**把相邻冻结范围归入表题 / 单位 / 表注。

    全部是位置规则（不读任何业务文字）：

    - 范围完全落在表体矩形**上方**、垂直间隙 <= `PROXIMITY_PT`，且被
      `caption_region_proven` 共同证明为**前导区域** ⇒ 表题候选；
    - 其中与表题处在**同一行带**、且整体位于表题右侧的范围 ⇒ 单位；
    - 范围完全落在表体矩形**下方**、垂直间隙 <= `PROXIMITY_PT` ⇒ 表注。

    "位置相邻"**不**足以成为表题的证明：多行的冻结范围是正文段落的一部分，不吸收
    为表题（§19.7 的"标题只能来自被验证的表题/前导区域"）。
    """
    title_refs: list = []
    unit_refs: list = []
    note_refs: list = []
    title_ids: list[str] = []
    unit_ids: list[str] = []
    note_ids: list[str] = []
    caption_band: tuple | None = None
    caption_right = 0.0
    above: list[tuple] = []
    for d in dispositions:
        if d.start_page != page_number:
            continue
        band = disposition_band(ctx, d)
        if band is None:
            continue
        gap_above = table_bbox[1] - band[1]
        gap_below = band[0] - table_bbox[3]
        if 0.0 <= gap_above <= PROXIMITY_PT and caption_region_proven(ctx, d):
            above.append((band[0], d.disposition_id, d))
        elif 0.0 <= gap_below <= PROXIMITY_PT:
            refs = refs_in_disposition(ctx, d)
            if refs:
                note_refs.extend(refs)
                note_ids.append(d.disposition_id)
    above.sort(key=lambda x: (x[0], x[1]))
    for _top, _did, d in above:
        refs = refs_in_disposition(ctx, d)
        if not refs:
            continue
        band = disposition_band(ctx, d)
        x0, x1 = _disposition_horizontal(ctx, d)
        is_unit = (caption_band is not None and band is not None
                   and _band_overlap(caption_band, band)
                   >= UNIT_SAME_BAND_MIN_OVERLAP
                   and x0 >= caption_right - PROXIMITY_PT)
        if is_unit:
            unit_refs.extend(refs)
            unit_ids.append(d.disposition_id)
            caption_right = max(caption_right, x1)
        else:
            title_refs.extend(refs)
            title_ids.append(d.disposition_id)
            if caption_band is None and band is not None:
                caption_band = band
                caption_right = x1
    title_blocks = _reindex(_blocks_from_refs(ctx, title_refs, unclosed=()))
    unit_blocks = _reindex(_blocks_from_refs(ctx, unit_refs, unclosed=()))
    note_blocks = _reindex(_blocks_from_refs(ctx, note_refs, unclosed=()))
    unit_text = (CELL_BLOCK_SEPARATOR.join(b.text for b in unit_blocks)
                 if unit_blocks else None)
    return TableAdornments(
        title_blocks=title_blocks, unit_blocks=unit_blocks, unit_text=unit_text,
        note_blocks=note_blocks, title_disposition_ids=tuple(title_ids),
        unit_disposition_ids=tuple(unit_ids),
        note_disposition_ids=tuple(note_ids),
        source_refs=_dedup_refs(title_refs + unit_refs + note_refs))


# ---------------------------------------------------------------------------
# 7. 表格构建
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuiltTable:
    table: Any
    grid: CellGridPlan
    disposition_ids: tuple
    consumed: dict
    unclosed: tuple
    adornments: TableAdornments


def build_table(ctx: TableBuildContext, cand: BuilderCandidate, gate: GateResult,
                *, source_order_index: int) -> BuiltTable:
    """由已过门的候选构建一个 `TableObjectV4`。"""
    from .table_schema import TableObjectV4
    grid = gate.grid
    if grid is None:
        _err("过门的候选必须携带闭合网格")
    cells_by_row: dict = {}
    consumed: dict = {}
    unclosed: list = []
    for plan_cell in grid.cells:
        asm = assemble_cell(ctx, plan_cell, page_number=grid.page_number,
                            row=plan_cell.row)
        cells_by_row.setdefault(plan_cell.row, []).append(asm.cell)
        for cid, ranges in asm.consumed.items():
            consumed.setdefault(cid, []).extend(ranges)
        unclosed.extend(asm.unclosed)
    # §19.6.1 + `TableRowV4`：每个物理行都必须至少有一个**起始** cell。整行被上方
    # cell 的 rowspan 完全吞掉的网格无法用 `to-4` 诚实表达 ⇒ 不提升为完整表。
    for r in range(grid.row_count):
        if not cells_by_row.get(r):
            raise UnsupportedStructureError(
                "网格存在没有起始 cell 的物理行：不得提升为完整表（fail-closed）")
    # `to-4` 的**表级**来源是全部被消费片段的并集：一个 cell 都没闭合到真实 terminal
    # source 的候选，整张表就没有任何可回查来源（`TableObjectV4.create` 会当场拒绝）。
    # 在这里 fail-closed 成一条 typed 拒绝，而不是让它撞上 schema 错——两者结论相同
    # （该候选不成表），但只有前者能被 `build_tables` 记进逐候选审计。
    if not any(cell.source_refs for cells in cells_by_row.values()
               for cell in cells):
        raise UnsupportedStructureError(
            "整表没有任何可回查的真实来源片段：不得提升为完整表（fail-closed）")
    rows = assign_rows(cells_by_row)
    kind, header_absence = determine_structure_kind(
        rows=rows, geometry_backed=grid.geometry_backed, grid=grid)
    owner = resolve_owner(ctx, gate)
    table_class, _rationale = classify_structure_class(
        ctx.profiles.classification, _classification_inputs(ctx, owner))
    adorn = build_adornments(
        ctx, table_bbox=cand.bbox, page_number=cand.page_number,
        dispositions=_adornment_dispositions(ctx, cand))
    missing = _missing_fields(rows, kind, table_class, grid, unclosed,
                              title_block_count=len(adorn.title_blocks))
    state, state_reason = _structure_state(kind, missing, grid)
    profile = ctx.profiles
    table = TableObjectV4.create(
        schema_version=TABLE_SCHEMA_VERSION,
        table_builder_version=BUILDER_VERSION,
        cell_schema_version=V.TABLE_CELL_SCHEMA_VERSION,
        geometry_version=V.TABLE_GEOMETRY_VERSION,
        geometry_settings_fingerprint=ctx.geometry.settings.settings_fingerprint,
        classification_profile_version=profile.classification.profile_version,
        classification_profile_file_fingerprint=(
            profile.classification_file_sha256),
        classification_profile_content_fingerprint=(
            profile.classification.classification_profile_fingerprint),
        cell_block_profile_version=profile.cell_block.profile_version,
        cell_block_profile_file_fingerprint=profile.cell_block_file_sha256,
        cell_block_profile_content_fingerprint=(
            profile.cell_block.cell_block_profile_fingerprint),
        document_id=ctx.document_id,
        document_version=ctx.document_version,
        evidence_set_version=ctx.evidence_set_version,
        page_layout_id=ctx.page_layout_id,
        outline_id=ctx.outline_id,
        verified_span_snapshot_id=ctx.verified_span_snapshot_id,
        upstream_dependency_fingerprint=ctx.upstream_dependency_fingerprint,
        page_number=cand.page_number,
        page_bbox=tuple(float(x) for x in cand.bbox),
        source_order_index=source_order_index,
        owner=owner,
        structure_kind=kind,
        structure_class=table_class,
        structure_state=state,
        structure_state_reason=state_reason,
        header_absence_reason=header_absence,
        missing_or_uncertain_fields=missing,
        column_count=grid.column_count,
        title_blocks=adorn.title_blocks,
        unit_text=adorn.unit_text,
        unit_blocks=adorn.unit_blocks,
        note_blocks=adorn.note_blocks,
        rows=rows,
        source_refs=_table_refs(rows, adorn),
        continuation_anchor_locator=None,
        continuation_candidate_locators=())
    merged = {cid: tuple(sorted(set(v))) for cid, v in consumed.items()}
    return BuiltTable(
        table=table, grid=grid,
        disposition_ids=((gate.frozen_disposition_id,)
                         if gate.frozen_disposition_id else ()),
        consumed=merged, unclosed=tuple(unclosed), adornments=adorn)


def _table_refs(rows: Sequence[Any], adorn: TableAdornments) -> tuple:
    """表级 `source_refs` = **全部被消费片段**的并集（cell + 标题/单位/注释）。"""
    pool: dict = {}
    cell_ids: set = set()
    for row in rows:
        for cell in row.cells:
            for ref in cell.source_refs:
                pool[ref.source_ref_id] = ref
                cell_ids.add(ref.source_ref_id)
    outer: list[str] = []
    for blocks in (adorn.title_blocks, adorn.unit_blocks, adorn.note_blocks):
        for blk in blocks:
            outer.extend(blk.source_ref_ids)
    if len(set(outer)) != len(outer):
        raise UnsupportedStructureError(
            "同一来源片段被两个表级块重复消费：不得提升为完整表（fail-closed）")
    if set(outer) & cell_ids:
        raise UnsupportedStructureError(
            "同一来源片段同时被 cell 与表级块消费：不得提升为完整表（fail-closed）")
    for ref in adorn.source_refs:
        pool[ref.source_ref_id] = ref
    if set(outer) | cell_ids != set(pool):
        raise UnsupportedStructureError(
            "表级 source_refs 不是全部被消费片段的并集（fail-closed）")
    if len(pool) != len(cell_ids) + len(set(outer)):
        raise UnsupportedStructureError(
            "同一 LayoutSpan 片段被两个 cell 重复消费：cell 网格不是互斥分区"
            "（fail-closed）")
    return tuple(sorted(pool.values(), key=lambda r: (
        r.interval.page_number, r.interval.line_index, r.interval.span_index,
        r.interval.span_char_range[0])))


def _adornment_dispositions(ctx: TableBuildContext, cand: BuilderCandidate) \
        -> tuple:
    """表体之外的相邻同页冻结范围（`table_inside` / `table_adjacency`）。"""
    out: list = []
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != cand.page_number or d.end_page != d.start_page:
            continue
        band = disposition_band(ctx, d)
        if band is None:
            continue
        vertical = min(band[1], cand.bbox[3]) - max(band[0], cand.bbox[1])
        if vertical > 0.0:
            # 与表体在垂直方向相交：不属于"表体之外"，交由 cell 网格裁决。
            continue
        gap_above = cand.bbox[1] - band[1]
        gap_below = band[0] - cand.bbox[3]
        if (0.0 <= gap_above <= PROXIMITY_PT) or \
                (0.0 <= gap_below <= PROXIMITY_PT):
            out.append(d)
    out.sort(key=lambda d: (d.start_page, d.start_line, d.disposition_id))
    return tuple(out)


def resolve_owner(ctx: TableBuildContext, gate: GateResult) -> Any:
    """**恰好一个**可独立回查的 owner（§19.4.2）。孤儿表与字符串自报归属一律拒绝。

    并集通道（`admission_basis == "contiguous_frozen_union"`）由**串首**成员担任
    owner：串内成员同属一个已验证 `node_id`（见 `_contiguous_frozen_union` 判据 ③，
    归属因此不因选谁而变），且 `TableOwnerRef` 只有一个源边界字段。于是 owner 的
    `source_boundary_*` 只回指该成员**这一段**，不列举整串 —— 这是有意的保守记法，
    不是表的来源被截断：表的真实来源逐格由 cell 片段证明。
    """
    from .table_schema import TableOwnerRef
    d = ctx.disposition_by_id(gate.frozen_disposition_id or "")
    if d is None:
        _err("表格候选没有对应的 TS4 冻结范围：所有权无法回查（fail-closed）")
    if d.node_id is not None:
        if ctx.outline_node(d.node_id) is None:
            _err(f"冻结范围给出的 node_id {d.node_id!r} 不在已验证标题树里"
                 f"（fail-closed）")
        return TableOwnerRef(
            owner_kind="outline_node", node_id=d.node_id,
            outline_locator=ctx.outline_locator,
            source_boundary_kind="ts4_body_disposition",
            source_boundary_locator=d.disposition_locator,
            source_boundary_id=d.disposition_id,
            source_boundary_start_page=int(d.start_page),
            source_boundary_end_page=int(d.end_page),
            unassigned_ref_kind=None, unassigned_ref_locator=None,
            unassigned_ref_id=None)
    return TableOwnerRef(
        owner_kind="unassigned_boundary", node_id=None, outline_locator=None,
        source_boundary_kind="ts4_body_disposition",
        source_boundary_locator=d.disposition_locator,
        source_boundary_id=d.disposition_id,
        source_boundary_start_page=int(d.start_page),
        source_boundary_end_page=int(d.end_page),
        unassigned_ref_kind="ts4_disposition",
        unassigned_ref_locator=d.disposition_locator,
        unassigned_ref_id=d.disposition_id)


def _classification_inputs(ctx: TableBuildContext, owner: Any) \
        -> TableClassificationInputs:
    """分类器的**允许输入**只有：已验证节点路径与源边界（§19.6.4）。

    `unassigned_boundary` owner 没有已验证的**节点**，因此 `owner_resolved=False`
    ⇒ 行 1 ⇒ `unclassified`：这与 `TableObjectV4` 的"未归属 ⇒ unclassified"
    约束是同一条规则的两种表述，不存在让表内文字影响分类的路径。
    """
    if owner.owner_kind != "outline_node":
        return TableClassificationInputs(
            owner_kind="unassigned_boundary", owner_resolved=False,
            ancestor_titles=(), inside_verified_body=False, path_available=False)
    node = ctx.outline_node(owner.node_id or "")
    if node is None:
        return TableClassificationInputs(
            owner_kind="outline_node", owner_resolved=False, ancestor_titles=(),
            inside_verified_body=False, path_available=False)
    return TableClassificationInputs(
        owner_kind="outline_node", owner_resolved=True,
        ancestor_titles=tuple(node.structural_path),
        inside_verified_body=True, path_available=True)


def _closure_problem_fields(grid: CellGridPlan) -> set:
    """网格**闭合性**问题 ⇒ 对应的诚信字段码（§19.5.2 / §19.6.3）。

    - 有真实行跨越候选矩形边界（`straddling_lines`）：被排除的那些行可能是被**截断**
      的行，因此行边界不成立；
    - `unexplained_hole`：网格位置没有任何真实片段解释 ⇒ cell 内部不可回查；
    - `fragment_not_column_aligned`：片段横跨列带却无法唯一归属 ⇒ 列边界不成立。

    这三条都来自**真实 PageLayout** 的重建结果，不是候选自报的几何字段。
    """
    codes: set = set()
    if grid.straddling_lines:
        codes.add("row_boundary")
    for problem in grid.problems:
        if problem == "unexplained_hole":
            codes.add("cell_interior")
        elif problem in ("fragment_not_column_aligned",
                         "column_boundary_cuts_fragment",
                         "cell_coverage_overlap"):
            codes.add("column_boundary")
        elif problem in ("line_straddles_candidate_boundary",
                         "fragment_consumed_twice",
                         "column_band_count_below_min"):
            codes.add("row_boundary")
    return codes


def _missing_fields(rows: Sequence[Any], kind: str, table_class: str,
                    grid: CellGridPlan, unclosed: Sequence[Any],
                    *, title_block_count: int) -> tuple:
    codes: set = set()
    if title_block_count == 0:
        # 没有**被验证的**表题/前导区域就必须如实声明，不能默默当作有表题。
        codes.add("title")
    if kind == "headerless_grid" or not any(r.role == "header" for r in rows):
        codes.add("header")
    if table_class == "financial_main_statement":
        # TS5 **不**凭空声称会计小计 / 合计：那需要真实行分节证据，本批没有。
        codes.update({"subtotal", "total", "column_semantics"})
    if not grid.geometry_backed:
        codes.add("row_boundary")
        codes.add("column_boundary")
    if unclosed:
        # 有片段无法闭合到真实 terminal source ⇒ cell 内部并非全部可回查。
        codes.add("cell_interior")
    # 闭合性证据：截断的行、无法解释的空位、无法归属的片段都必须落成在册缺口，
    # 否则"正文未闭合 / 结构不确定"会被当成 `complete`。
    codes |= _closure_problem_fields(grid)
    if len(codes - set(MISSING_FIELD_CODES)):
        _err("missing_or_uncertain_fields 出现未登记字段码（fail-closed）")
    return tuple(sorted(codes))


def _structure_state(kind: str, missing: Sequence[str],
                     grid: CellGridPlan) -> tuple:
    """完成状态**不**由调用方决定：只要有一条在册缺口就一定 `partial`。

    原因按下述固定优先级取，避免把 "provenance 不完整" 说成 "会计分节未证明"。
    `complete` 还额外要求网格闭合性没有留下任何问题——"正文是否闭合"必须由真实
    重建结果回答，不能因为缺口清单恰好为空就默认闭合。
    """
    if kind == "headerless_grid":
        return "partial", "header_not_proven"
    if "cell_interior" in missing:
        return "partial", "cell_provenance_incomplete"
    if grid.straddling_lines:
        return "partial", "row_boundary_truncated"
    if grid.problems:
        return "partial", "grid_closure_not_proven"
    if missing:
        return "partial", "accounting_sections_not_proven"
    return "complete", None


# ---------------------------------------------------------------------------
# 8. continuation 与 relations（§19.8.2 / §19.8.1）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContinuationOutcome:
    relations: tuple
    ambiguous_pairs: tuple
    anchors: dict


def build_continuations(ctx: TableBuildContext, tables: Sequence[BuiltTable]) \
        -> ContinuationOutcome:
    """§19.8.2 六条证明**全部**满足才建立 `continued_by`；否则各自保留 + 缺口。"""
    ordered = sorted(tables, key=lambda t: (t.table.page_number,
                                            t.table.page_bbox[1],
                                            t.table.table_id))
    succ: dict = {}
    pred: dict = {}
    ambiguous: list = []
    for a, b in zip(ordered, ordered[1:]):
        if not _continuation_proofs(a, b, ordered):
            ambiguous.append((a.table.table_id, b.table.table_id))
            continue
        succ.setdefault(a.table.table_id, []).append(b.table.table_id)
        pred.setdefault(b.table.table_id, []).append(a.table.table_id)
    relations: list = []
    for a_id in sorted(succ):
        targets = succ[a_id]
        if len(targets) != 1 or len(pred.get(targets[0], [])) != 1:
            continue
        a = next(t for t in ordered if t.table.table_id == a_id)
        b = next(t for t in ordered if t.table.table_id == targets[0])
        if _has_cycle(a_id, succ):
            continue
        from .table_schema import TableRelation
        relations.append(TableRelation.create(
            schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
            relation_builder_version=TABLE_RELATION_BUILDER_VERSION,
            upstream_dependency_fingerprint=ctx.upstream_dependency_fingerprint,
            relation_kind="continued_by",
            source_endpoint=_table_endpoint(a.table),
            target_endpoint=_table_endpoint(b.table),
            relation_proof_ids=_proof_ids(a.table, b.table)))
    anchors = {r.source_endpoint.table_id: r.target_endpoint.table_locator
               for r in relations}
    return ContinuationOutcome(tuple(relations), tuple(ambiguous), anchors)


def _continuation_proofs(a: BuiltTable, b: BuiltTable,
                         ordered: Sequence[BuiltTable]) -> bool:
    """六条证明（`CONTINUATION_PROOF_KINDS`）逐条成立才允许 `continued_by`。"""
    ta, tb = a.table, b.table
    # 1. same_document_identity
    if (ta.document_id, ta.document_version, ta.evidence_set_version,
            ta.page_layout_id, ta.outline_id) != \
            (tb.document_id, tb.document_version, tb.evidence_set_version,
             tb.page_layout_id, tb.outline_id):
        return False
    # 2. adjacent_page_or_explicit_occurrence
    if tb.page_number != ta.page_number + 1:
        return False
    # 3. compatible_column_header_unit
    if ta.column_count != tb.column_count or ta.unit_text != tb.unit_text:
        return False
    if _header_signature(ta) != _header_signature(tb):
        return False
    if not _column_bands_compatible(a, b):
        return False
    # 4. source_order_closure
    if (tb.page_number, tuple(tb.page_bbox)) <= (ta.page_number,
                                                 tuple(ta.page_bbox)):
        return False
    # 5. no_intervening_structure
    for other in ordered:
        if other.table.table_id in (ta.table_id, tb.table_id):
            continue
        if ta.page_number < other.table.page_number < tb.page_number:
            return False
    # 6. bidirectional_acyclic_single_successor 由 `build_continuations` 统一验证。
    return True


def _header_signature(table: Any) -> tuple:
    header = [r for r in table.rows if r.role == "header"]
    if not header:
        return ()
    return tuple(c.text for c in header[0].cells)


def _column_starts(bt: BuiltTable) -> tuple:
    """每个列索引的**列带起点**：该列全部 cell 里最小的左边界。

    不能用"每个 cell 的 bbox 左边界"当列带：borderless（行带/列带）路径里 cell 的
    bbox 是该行**真实片段**的矩形，逐行不同（首行缩进、右对齐数字都会移动左边界），
    于是同一张表的上下续表永远判成不兼容，`continued_by` 恒为空。
    """
    starts: dict = {}
    for c in bt.grid.cells:
        starts[c.column] = min(starts.get(c.column, c.bbox[0]), c.bbox[0])
    return tuple(sorted(starts.items()))


def _column_bands_compatible(a: BuiltTable, b: BuiltTable) -> bool:
    """§19.8.2 证明 3：两条续表必须落在**同一组列带**上。

    比较的是列带起点（含几何容差），不是逐 cell 的矩形是否逐位相等。
    """
    sa, sb = _column_starts(a), _column_starts(b)
    if tuple(c for c, _ in sa) != tuple(c for c, _ in sb):
        return False
    if not sa:
        return False
    tol = BORDERLESS_CLUSTER_PARAMS["column_tolerance"]
    return all(abs(x - y) <= tol for (_, x), (_, y) in zip(sa, sb))


def _has_cycle(start: str, succ: dict) -> bool:
    seen = {start}
    cur = start
    while True:
        nxt = succ.get(cur, [])
        if not nxt:
            return False
        cur = nxt[0]
        if cur in seen:
            return True
        seen.add(cur)


def _proof_ids(a: Any, b: Any) -> tuple:
    ids = {a.table_locator, b.table_locator, a.table_id, b.table_id}
    ids.update(CONTINUATION_PROOF_KINDS)
    return tuple(sorted(ids))


def _table_endpoint(table: Any) -> Any:
    from .table_schema import TableEndpointRef
    return TableEndpointRef(
        table_locator=table.table_locator, table_id=table.table_id,
        document_id=table.document_id, page_layout_id=table.page_layout_id,
        outline_id=table.outline_id, page_number=table.page_number)


def build_adornment_relations(ctx: TableBuildContext, bt: BuiltTable) -> tuple:
    """表题 / 单位 / 表注的 typed 关系（component 端点 → table 端点）。"""
    from .table_schema import ComponentEndpointRef, TableRelation
    out: list = []
    seen: set = set()
    for kind, role, blocks in (("caption_of", "caption", bt.table.title_blocks),
                               ("unit_of", "unit", bt.table.unit_blocks),
                               ("footnote_of", "note", bt.table.note_blocks)):
        if kind not in RESOLVABLE_RELATION_KINDS:
            continue
        for blk in blocks:
            for rid in blk.source_ref_ids:
                ref = bt.table.source_ref_by_id(rid)
                if ref is None:
                    _err(f"表级块引用了未知来源片段 {rid!r}（fail-closed）")
                if (kind, rid) in seen:
                    continue
                seen.add((kind, rid))
                endpoint = ComponentEndpointRef(
                    component_id=ref.component_id,
                    component_locator=ref.component_locator,
                    evidence_block_id=ref.evidence_block_id,
                    evidence_char_range=ref.evidence_char_range,
                    terminal_kind=ref.terminal_kind, terminal_id=ref.terminal_id,
                    role=role, table_id=bt.table.table_id)
                out.append(TableRelation.create(
                    schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
                    relation_builder_version=TABLE_RELATION_BUILDER_VERSION,
                    upstream_dependency_fingerprint=(
                        ctx.upstream_dependency_fingerprint),
                    relation_kind=kind, source_endpoint=endpoint,
                    target_endpoint=_table_endpoint(bt.table),
                    relation_proof_ids=tuple(sorted(
                        {ref.source_ref_id, ref.component_id,
                         bt.table.table_locator, bt.table.table_id}))))
    out.sort(key=lambda r: (r.relation_kind, r.relation_id))
    return tuple(out)


# ---------------------------------------------------------------------------
# 9. disposition 裁决计划（§19.7.1）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionDraft:
    """一条 provisional range 的**唯一**裁决草稿。

    `final_span_id` 暂为 `None`：`TableRangeDecision` 在 §19.4.4 的 DAG 里位于
    final span **下游**，因此由 `final_material_builder` 在 final span 完成后补齐。
    """

    disposition_id: str
    disposition_locator: str
    range_kind: str
    decision: str
    target_kind: str
    table_id: str | None
    final_span_id: str | None
    evidence_char_range: tuple
    source_ref_ids: tuple
    rationale_code: str


_ROLE_RATIONALE = {
    "absorbed_as_caption": "caption_position_and_text",
    "absorbed_as_unit": "unit_binding_and_source_order",
    "absorbed_as_note": "note_marker_and_geometry",
    "absorbed_as_table_body": "cell_grid_membership",
}


def plan_decisions(ctx: TableBuildContext, tables: Sequence[BuiltTable],
                   gates: dict) -> tuple:
    """每个 `table_inside` / `table_adjacency` disposition **恰好**一条裁决。"""
    drafts: list = []
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        bt = _table_for_disposition(tables, d.disposition_id)
        if bt is not None:
            role = absorbed_role(ctx, bt, d)
            if role is None:
                drafts.append(_draft(d, "unsupported_table_structure", None,
                                     "insufficient_structure_proof"))
            else:
                drafts.append(_draft(d, role, bt.table.table_id,
                                     _ROLE_RATIONALE[role]))
            continue
        gate = gates.get(d.disposition_id)
        reason = gate.reason if gate is not None else None
        if reason == "candidate_not_uniquely_mapped":
            drafts.append(_draft(d, "unresolved_geometry", None,
                                 "geometry_not_uniquely_resolved"))
        else:
            drafts.append(_draft(d, "unsupported_table_structure", None,
                                 "insufficient_structure_proof"))
    return tuple(drafts)


def _draft(d: Any, decision: str, table_id: str | None,
           rationale: str) -> DecisionDraft:
    if decision not in TABLE_DECISION_KINDS:
        _err(f"未登记的裁决 {decision!r}")
    if rationale not in DECISION_RATIONALE_CODES:
        _err(f"未登记的依据码 {rationale!r}")
    return DecisionDraft(
        disposition_id=d.disposition_id,
        disposition_locator=d.disposition_locator, range_kind=d.range_kind,
        decision=decision, target_kind=TABLE_DECISION_TARGETS[decision],
        table_id=table_id, final_span_id=None, evidence_char_range=(0, 0),
        source_ref_ids=(), rationale_code=rationale)


def _table_for_disposition(tables: Sequence[BuiltTable], disposition_id: str) \
        -> BuiltTable | None:
    for bt in tables:
        if disposition_id in bt.disposition_ids:
            return bt
    return None


def absorbed_role(ctx: TableBuildContext, bt: BuiltTable, d: Any) -> str | None:
    """范围相对表体矩形的位置 ⇒ 吸收形态（纯几何，不读文字）。

    唯独"表题"这一形态**不能**只凭位置：上方范围必须同时被
    `caption_region_proven` 证明为前导区域。不成立时返回 `None`，由调用方按既有的
    `insufficient_structure_proof` 分支记 `unsupported_table_structure`——这等于
    诚实地说"此处结构未被证明"，而不是把位置相邻当成表题证据。
    """
    band = disposition_band(ctx, d)
    if band is None:
        return None
    bbox = bt.table.page_bbox
    if band[1] <= bbox[1]:
        if not caption_region_proven(ctx, d):
            return None
        return "absorbed_as_caption"
    if band[0] >= bbox[3]:
        return "absorbed_as_note"
    if band[0] >= bbox[1] and band[1] <= bbox[3]:
        return "absorbed_as_table_body"
    return None


def absorbed_roles(ctx: TableBuildContext, tables: Sequence[BuiltTable]) -> dict:
    """`disposition_id -> (吸收形态, table_id)`（供上层守恒分区使用）。"""
    out: dict = {}
    for d in ctx.dispositions:
        if d.range_kind not in TABLE_DISPOSITION_KINDS:
            continue
        bt = _table_for_disposition(tables, d.disposition_id)
        if bt is None:
            continue
        role = absorbed_role(ctx, bt, d)
        if role is not None:
            out[d.disposition_id] = (role, bt.table.table_id)
    return out


# ---------------------------------------------------------------------------
# 10. 顶层：一次构建
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableBuildResult:
    tables: tuple
    decisions: tuple
    relations: tuple
    ambiguous_continuations: tuple
    gate_results: dict
    rejected: tuple
    channel_limitations: tuple
    absorbed_roles: dict
    audit: tuple = ()


def _audit_row(cand: BuilderCandidate, *, outcome: str, reason: str | None,
               blocks_document: bool, table_id: str | None,
               admission_basis: str | None = None) \
        -> TableCandidateAuditRow:
    """把一次候选裁决记成审计行（§19.14.1）。`unresolved` 的判据是理由码本身。"""
    if outcome == "rejected" and reason in CANDIDATE_AUDIT_UNRESOLVED_REASONS:
        # 几何无法裁决：不得记成"已定论不是表"，必须显式保留为未决。
        outcome = "unresolved"
    g = cand.geometry
    return TableCandidateAuditRow(
        candidate_source=cand.candidate_source, strategy=cand.strategy,
        page_number=cand.page_number, bbox=tuple(float(v) for v in cand.bbox),
        outcome=outcome, reason=reason, blocks_document=blocks_document,
        frozen_disposition_id=cand.frozen_disposition_id,
        closed_grid=bool(g.closed_grid), edge_count=int(g.edge_count),
        intersection_count=int(g.intersection_count),
        row_count=int(g.row_count), column_count=int(g.column_count),
        cell_count=int(g.cell_count), table_id=table_id,
        admission_basis=admission_basis)


def _page_audit_rows(ctx: TableBuildContext) -> list:
    """整页几何不可裁决的行（`unresolved_pages`）。

    这不是"某个候选"，而是"这一页根本没有可用几何"：它必须出现在审计里，否则
    "没找到候选"和"候选都没通过"会被混为一谈。`candidate_source` 记为
    `GEOMETRY_PAGE_SOURCE`，`bbox` 取该页真实 PageLayout 页框（无页框则为零框）。
    """
    rows: list = []
    for page_number in ctx.geometry.unresolved_pages:
        page = ctx.page(page_number.page_number)
        bbox = ((0.0, 0.0, float(page.width), float(page.height))
                if page is not None else (0.0, 0.0, 0.0, 0.0))
        rows.append(TableCandidateAuditRow(
            candidate_source=GEOMETRY_PAGE_SOURCE, strategy="page_frame",
            page_number=page_number.page_number, bbox=bbox, outcome="unresolved",
            reason=page_number.reason, blocks_document=False,
            frozen_disposition_id=None, closed_grid=False, edge_count=0,
            intersection_count=0, row_count=0, column_count=0, cell_count=0,
            table_id=None, admission_basis=None))
    return rows


def _claimed_fragment_keys(bt: BuiltTable) -> set:
    """该表主张的全部真实 LayoutSpan 片段键 `(页, 行, 片段索引)`。

    cell 来源与表题/单位/表注来源一起算：二者都是"这段真实文字已被本表消费"。
    """
    keys: set = set()
    for row in bt.table.rows:
        for cell in row.cells:
            for ref in cell.source_refs:
                keys.add((ref.interval.page_number, ref.interval.line_index,
                          ref.interval.span_index))
    for blocks in (bt.table.title_blocks, bt.table.unit_blocks,
                   bt.table.note_blocks):
        for blk in blocks:
            for rid in blk.source_ref_ids:
                ref = bt.table.source_ref_by_id(rid)
                if ref is None:
                    _err(f"表级块引用了未知来源片段 {rid!r}（fail-closed）")
                keys.add((ref.interval.page_number, ref.interval.line_index,
                          ref.interval.span_index))
    return keys


def build_tables(ctx: TableBuildContext) -> TableBuildResult:
    """一次完整构建。**没有**任何 raw-root 入口：输入只有 context。"""
    candidates = enumerate_candidates(ctx)
    gate_results: dict = {}
    rejected: list = []
    admitted: list = []
    audit: list = []
    for cand in candidates:
        gate = evaluate_candidate_gate(ctx, cand)
        if cand.frozen_disposition_id is not None:
            prev = gate_results.get(cand.frozen_disposition_id)
            if prev is None or (gate.admitted and not prev.admitted):
                gate_results[cand.frozen_disposition_id] = gate
        if gate.admitted:
            admitted.append((cand, gate))
        else:
            rejected.append((cand.page_number, tuple(cand.bbox), gate.reason,
                             gate.blocks_document))
            audit.append(_audit_row(cand, outcome="rejected", reason=gate.reason,
                                    blocks_document=gate.blocks_document,
                                    table_id=None))
    admitted.sort(key=lambda x: (x[0].page_number, tuple(x[0].bbox),
                                 x[0].candidate_source, x[0].strategy))
    candidates_built: list = []
    for i, (cand, gate) in enumerate(admitted):
        try:
            built = build_table(ctx, cand, gate, source_order_index=i)
        except UnsupportedStructureError:
            rejected.append((cand.page_number, tuple(cand.bbox),
                             "unsupported_table_structure", False))
            audit.append(_audit_row(cand, outcome="rejected",
                                    reason="unsupported_table_structure",
                                    blocks_document=False, table_id=None))
            continue
        verdict = prose_negative_gate(
            [c.text for r in built.table.rows for c in r.cells])
        if verdict is not None:
            rejected.append((cand.page_number, tuple(cand.bbox), verdict, False))
            audit.append(_audit_row(cand, outcome="rejected", reason=verdict,
                                    blocks_document=False, table_id=None))
            continue
        candidates_built.append((cand, built, gate))
    tables: list = []
    # 同一处文字只能被**一个**表消费（§六）：两个已过门的候选若主张同一批真实
    # LayoutSpan 片段，同一个字符就会被两张表同时消费（守恒层会记
    # `layout_text_duplicate_claim`）。判胜负只用几何——**面积大者胜**（它包含另一
    # 者或覆盖更多真实文字），面积相同则按稳定候选键，因此结果与枚举顺序无关。
    # 落败的候选按既有的 `insufficient_structure_proof` 记缺口，不阻断文档。
    ordered_by_priority = sorted(
        candidates_built,
        key=lambda x: (-_area(x[0].bbox), x[0].stable_key()))
    kept: list = []
    kept_claims: list = []
    for cand, built, gate in ordered_by_priority:
        claims = _claimed_fragment_keys(built)
        if any(claims & other for other in kept_claims):
            rejected.append((cand.page_number, tuple(cand.bbox),
                             "candidate_overlaps_accepted_table", False))
            audit.append(_audit_row(cand, outcome="rejected",
                                    reason="candidate_overlaps_accepted_table",
                                    blocks_document=False, table_id=None))
            continue
        kept.append((cand, built, gate))
        kept_claims.append(claims)
    for cand, built, gate in sorted(kept,
                                    key=lambda x: x[1].table.source_order_index):
        tables.append(built)
        audit.append(_audit_row(cand, outcome="accepted", reason=None,
                                blocks_document=False,
                                table_id=built.table.table_id,
                                admission_basis=gate.admission_basis))
    audit.extend(_page_audit_rows(ctx))
    cont = build_continuations(ctx, tables)
    relations = list(cont.relations)
    for bt in tables:
        relations.extend(build_adornment_relations(ctx, bt))
    relations.sort(key=lambda r: (r.relation_kind, r.relation_id))
    # 审计行按**稳定键**排序：候选顺序、seed 顺序或运行顺序变化都不得改变它。
    audit.sort(key=lambda r: (r.page_number, r.bbox, r.candidate_source,
                              r.strategy, r.outcome, r.table_id or ""))
    return TableBuildResult(
        tables=tuple(tables),
        decisions=plan_decisions(ctx, tables, gate_results),
        relations=tuple(relations),
        ambiguous_continuations=cont.ambiguous_pairs,
        gate_results=gate_results, rejected=tuple(rejected),
        channel_limitations=CHANNEL_LIMITATIONS,
        absorbed_roles=absorbed_roles(ctx, tables), audit=tuple(audit))


# ---------------------------------------------------------------------------
# 11. provenance 完备性（供上层守恒使用）
# ---------------------------------------------------------------------------

def component_partition_problems(ctx: TableBuildContext,
                                 consumption: dict) -> list:
    """检查"一个 component 被切成互斥、无洞、源序、且并集等于全区间"的片段。

    返回问题列表；非空时该 component 只能 `pending(provenance_incomplete)`。
    """
    problems: list[str] = []
    comps = {c.component_id: c for c in ctx.components}
    for cid, ranges in sorted(consumption.items()):
        comp = comps.get(cid)
        if comp is None:
            problems.append(f"{cid}:unknown_component")
            continue
        ordered = sorted(set(ranges))
        lo, hi = comp.evidence_char_range
        if ordered[0][0] != lo or ordered[-1][1] != hi:
            problems.append(f"{cid}:range_not_exact")
            continue
        for (a, b), (c, _d) in zip(ordered, ordered[1:]):
            if c != b:
                problems.append(f"{cid}:gap_or_overlap")
                break
    return problems


# ---------------------------------------------------------------------------
# 12. self-check（不接触任何仓库资产；合成反例 + 真实 profile 装配）
# ---------------------------------------------------------------------------

def self_check(profiles_dir: str | None = None) -> dict:
    problems: list[str] = []
    from .table_schema import TableSourceInterval

    bundle = load_table_profile_bundle(profiles_dir)
    if bundle.settings_fingerprint() == "":
        problems.append("profile bundle 指纹为空")
    if bundle.classification.profile_version != \
            V.TABLE_CLASSIFICATION_PROFILE_VERSION:
        problems.append("classification profile 版本与登记不一致")
    if bundle.cell_block.profile_version != V.TABLE_CELL_BLOCK_PROFILE_VERSION:
        problems.append("cell block profile 版本与登记不一致")
    if TABLE_DISPOSITION_KINDS != ("table_inside", "table_adjacency"):
        problems.append("表范围种类必须恰为 table_inside/table_adjacency")
    if len(TABLE_DECISION_KINDS) != 8:
        problems.append("裁决种类必须恰为 8 种（§19.7.1）")
    if HEADER_ABSENCE_REASONS != ("no_header_evidence",
                                  "key_value_form_without_header"):
        problems.append("表头缺省原因与 schema 不一致")
    for kind in ("absorbed_as_caption", "absorbed_as_unit",
                 "absorbed_as_note", "absorbed_as_table_body"):
        if TABLE_DECISION_TARGETS[kind] != "table":
            problems.append(f"{kind} 的 target 必须为 table")
    for kind in ("kept_as_paragraph", "absorbed_into_body"):
        if TABLE_DECISION_TARGETS[kind] != "final_span":
            problems.append(f"{kind} 的 target 必须为 final_span")
    # 反例门（内容形状，不看关键词）。
    prose = ["本项目公司债券募集资金扣除发行费用后拟用于补充流动资金及偿还"
             "有息债务，具体用途由发行人根据实际经营情况确定。" * 2]
    if prose_negative_gate(prose) is None:
        problems.append("散文形状未被反例门拒绝")
    if prose_negative_gate(["货币资金", "1,234.56"]) is not None:
        problems.append("正常单元格被反例门误拒")
    if prose_negative_gate(["x" * (MAX_CELL_CHARS + 1)]) != "cell_too_long":
        problems.append("超长单元格未被拒绝")
    if prose_negative_gate([]) is None:
        problems.append("空输入必须被反例门拒绝")
    # 键值表判据。
    if _looks_like_key_value([]) is not False:
        problems.append("空表不得被判为键值表")
    # 通道优先级：冻结范围必须优先于任何几何通道。
    if _channel_rank("frozen_range") >= _channel_rank("pdfplumber_lines"):
        problems.append("冻结范围通道未取得最高优先级")
    if _channel_rank("pdfplumber_text") >= \
            _channel_rank("layout_borderless_cluster"):
        problems.append("通道优先级与 §19.5.1 枚举顺序不一致")
    # 片段区间必须是正长度（cell 来源不得是零长度区间）。
    try:
        TableSourceInterval(page_number=1, line_index=0, span_index=0,
                            span_char_range=(3, 3), bbox=(0.0, 0.0, 1.0, 1.0))
        problems.append("零长度片段区间未被拒绝")
    except SchemaValidationError:
        pass
    # 表结构类型的真值表：表头缺失只能是 headerless/partial。
    clear = CellGridPlan(page_number=1, bbox=(0.0, 0.0, 1.0, 1.0),
                         row_count=2, column_count=2, cells=(),
                         assigned_lines=(0, 1), straddling_lines=(),
                         geometry_backed=True, problems=())
    truncated = CellGridPlan(page_number=1, bbox=(0.0, 0.0, 1.0, 1.0),
                             row_count=2, column_count=2, cells=(),
                             assigned_lines=(0,), straddling_lines=(1,),
                             geometry_backed=True, problems=())
    holed = CellGridPlan(page_number=1, bbox=(0.0, 0.0, 1.0, 1.0),
                         row_count=2, column_count=2, cells=(),
                         assigned_lines=(0, 1), straddling_lines=(),
                         geometry_backed=True, problems=("unexplained_hole",))
    if _structure_state("headerless_grid", (), clear) != ("partial",
                                                          "header_not_proven"):
        problems.append("headerless_grid 必须是 partial")
    if _structure_state("headered_grid", (), clear) != ("complete", None):
        problems.append("无缺口的 headered_grid 必须是 complete")
    # 网格闭合性证据必须由**真实重建结果**回答：行被截断或网格留空位时，
    # 即使缺口清单恰好为空也不得判 complete。
    if _structure_state("headered_grid", (), truncated) != (
            "partial", "row_boundary_truncated"):
        problems.append("存在跨边界行时必须判 row_boundary_truncated")
    if _structure_state("headered_grid", (), holed) != (
            "partial", "grid_closure_not_proven"):
        problems.append("网格留有未解释空位时必须判 grid_closure_not_proven")
    return {
        "builder_version": BUILDER_VERSION,
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "relation_builder_version": TABLE_RELATION_BUILDER_VERSION,
        "classification_profile_version": bundle.classification.profile_version,
        "cell_block_profile_version": bundle.cell_block.profile_version,
        "profile_settings_fingerprint": bundle.settings_fingerprint(),
        "decision_kind_count": len(TABLE_DECISION_KINDS),
        "continuation_proof_count": len(CONTINUATION_PROOF_KINDS),
        "proximity_pt": PROXIMITY_PT,
        "channel_limitations": list(CHANNEL_LIMITATIONS),
        "problems": problems,
    }


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] != "--self-check":
        print(f"未知参数: {args[0]}", file=sys.stderr)
        return 2
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report["problems"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

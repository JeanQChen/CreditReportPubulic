# -*- coding: utf-8 -*-
"""TS5 的**同源几何层**（计划 §19.5.1 / §19.5.2 / §19.5.3）。

本模块只回答两个问题：

1. "这一页的 PDF 几何里，哪些矩形**看起来**是表？"（候选，不是表）；
2. "某个候选 bbox 能不能**唯一**映射回当前 `PageLayout` 的同页几何？"。

它**没有任何业务权威**：

- 不判定表、不分类、不生成 `TableObject`、不写入任何 ID；
- 不读 V2 TODO / gold / 公司名 / 证券代码 / 固定页码 / 表号；
- `pdfplumber` 的 extracted text **只作诊断**，绝不进入任何正式 cell 文本
  （正式 cell 文本必须从同一 `PageLayout` 的真实 source fragments 重建）。

三条硬边界（§19.5.2）：

1. 只能用 `VerifiedPageLayout.source_bytes` 打开 pdfplumber —— 几何与版式来自
   **同一份字节**，排除"先构建版式、后重新读盘"的窗口；
2. 坐标变换必须绑定页尺寸、CropBox/MediaBox、rotation、量化容差、固定 strategy
   settings 与引擎版本，且写成显式公式；
3. 页尺寸或旋转**无法唯一对齐**时整页候选为 `unresolved_geometry`，**不得**靠放大
   容差强行匹配。

候选只决定"检查哪里"（§19.5.1）：正式资格由 `table_builder` 的统一硬门判定。
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from typing import Any, Sequence

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    sha256_canonical,
)
from document_structure.schema import (
    _err,
    _need_bool,
    _need_children,
    _need_enum,
    _need_int,
    _need_num,
    _need_str,
    _need_str_tuple,
    _reject_unknown,
    quantize,
)

__all__ = [
    "TableGeometryError", "GEOMETRY_STRATEGIES", "CANDIDATE_SOURCES",
    "UNRESOLVED_GEOMETRY_REASONS", "GEOMETRY_UNRESOLVED_PAGE_REASON",
    "RULED_TABLE_SETTINGS", "TEXT_TABLE_SETTINGS", "TABLE_SETTINGS_BY_STRATEGY",
    "RULE_STROKE_MAX_THICKNESS_PT",
    "AXIS_ORDERS", "TABLE_GEOMETRY_DEFAULT_TOLERANCE",
    "TableGeometrySettings", "PageGeometryFrame", "GeometryCandidate",
    "GeometryUnresolvedPage", "TableGeometryReport",
    "pdfplumber_engine_versions", "geometry_settings",
    "align_page_frame", "extract_table_geometry", "verify_geometry_mapping",
    "candidate_order_key", "sort_candidates", "cluster_borderless_rows",
    "self_check", "_main",
]


class TableGeometryError(SchemaValidationError):
    """几何层失败（字节不符 / 引擎缺失 / 版式未签发 / 参数越界）。"""


#: 固定 strategy（封闭）。新增 strategy 必须同时登记 settings 与版本轴。
GEOMETRY_STRATEGIES: tuple[str, ...] = ("lines", "text")

#: 候选来源（封闭）。与 §19.5.1 的五条候选通道一一对应。
CANDIDATE_SOURCES: tuple[str, ...] = (
    "frozen_range",
    "explicit_caption_marker",
    "text_start_flag",
    "pdfplumber_lines",
    "pdfplumber_text",
    "layout_borderless_cluster",
)

#: 候选通道优先级（§19.5.1 的枚举顺序，越小越优先）。仅用于**同 bbox 去重**：
#: 它不参与资格判定，也不改变"多通道都发现"这件事本身。
CANDIDATE_SOURCE_PRIORITY: tuple[str, ...] = CANDIDATE_SOURCES

#: 整页级 `unresolved_geometry` 的原因（封闭）。
GEOMETRY_UNRESOLVED_PAGE_REASON = "page_frame_not_uniquely_aligned"

#: 候选审计里**整页级**行的来源标记（不属于 `CANDIDATE_SOURCES`：它不是一个候选通道，
#: 而是"这一页根本没有可用几何"这件事本身）。缺了它，"没找到候选"与"候选都没通过"
#: 会在 `table_candidate_audit.jsonl` 里被混为一谈。
GEOMETRY_PAGE_SOURCE = "geometry_page"

#: 候选级不可裁决原因（封闭）。
UNRESOLVED_GEOMETRY_REASONS: tuple[str, ...] = (
    "page_frame_not_uniquely_aligned",
    "overlapping_tie",
    "no_grid_structure",
)

#: 轴序（pdfplumber 页尺寸 ↔ PageLayout 页尺寸的对应关系）。
AXIS_ORDERS: tuple[str, ...] = ("direct", "swapped")

#: 页尺寸对齐容差（**固定**，不随调用方放大；单位 = PDF point）。
TABLE_GEOMETRY_DEFAULT_TOLERANCE = 0.5

#: 固定 pdfplumber 表检测 settings。必须**整体**进入 settings 指纹：
#: 任何一项变化都会改变候选集合，因此必须改变几何版本绑定。
RULED_TABLE_SETTINGS: dict[str, Any] = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "snap_x_tolerance": 3,
    "snap_y_tolerance": 3,
    "join_tolerance": 3,
    "join_x_tolerance": 3,
    "join_y_tolerance": 3,
    "edge_min_length": 3,
    "intersection_tolerance": 3,
    "intersection_x_tolerance": 3,
    "intersection_y_tolerance": 3,
}

#: 本表**规线**描边的厚度上界（PDF point，**固定**，不随调用方放大）。
#:
#: `lines` 通道此前把页面上的**全部**描边都当行/列边界。真实电子 PDF 里除细规线外
#: 还有两类"看起来像边"的描边：把某个词包起来的**文字框**（宽高都远大于厚度），和
#: **合并表头单元格的内缩框**（比真规线内缩几个 point）。它们的边落在真规线之间，
#: 被 pdfplumber 当成额外列。
#:
#: 实测 2025 年报 p24：真规线是 0.3 pt 的**细长填充矩形**（x = 56.6 / 132.7 / 233.6
#: / 309.8 / 386.0 / 462.2 / 538.3，逐行分段，且在两张跨列行上整段缺失），而文字框
#: （62.3..127.6 包住"项目"）与内缩框（138.4 / 304.6 / 315.4 / 457.1）的边都在其
#: 之间 ⇒ 该页被判成 10 行 × 17 列、102 个 `None`，骨架因此被丢弃，**整张物理表一张
#: 也建不出来**（应得 10 × 6、46 个真实 cell，含 4 个跨行/跨列合并）。
#:
#: 判据：一条描边只要有一维 <= 本上界、另一维 > 本上界，才是**规线**候选。它是**几何
#: 量纲**阈值（本页描边厚度分布），与文档、公司、语言、页码、表号无关；宽高都大的框
#: （单元格框、图片框、文字框）两个维度都远大于本上界，因此不会被误当规线。分段描边
#: **原样逐段**传入（不合并成贯穿整表的长线），所以"缺一条内线"的合并格依旧成立。
RULE_STROKE_MAX_THICKNESS_PT = 0.5

TEXT_TABLE_SETTINGS: dict[str, Any] = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "snap_x_tolerance": 3,
    "snap_y_tolerance": 3,
    "join_tolerance": 3,
    "join_x_tolerance": 3,
    "join_y_tolerance": 3,
    "edge_min_length": 3,
    "intersection_tolerance": 3,
    "intersection_x_tolerance": 3,
    "intersection_y_tolerance": 3,
}

TABLE_SETTINGS_BY_STRATEGY: dict[str, dict[str, Any]] = {
    "lines": RULED_TABLE_SETTINGS,
    "text": TEXT_TABLE_SETTINGS,
}

#: borderless 聚类的固定参数（进入 settings 指纹）。
BORDERLESS_CLUSTER_PARAMS: dict[str, Any] = {
    "row_overlap_min_ratio": 0.5,
    "band_gap_tolerance": 2.0,
    "column_tolerance": 3.0,
    "min_rows": 3,
    "min_columns": 2,
    "min_row_span_columns": 2,
}

#: cell 网格重建的问题码（封闭）。它**不是**自由文本：builder 只能按码判定
#: "该候选能否成为闭合网格"，不得按字符串内容猜。
GRID_PROBLEM_CODES: tuple[str, ...] = (
    # 行文本越出候选矩形（不夹取，如实记账）
    "line_straddles_candidate_boundary",
    # 片段横跨两个列带但无法归到唯一列（列边界不可判定）
    "fragment_not_column_aligned",
    # 内部列边界**切开**了同一个真实 LayoutSpan 片段（TS5-P1-2）：无骨架路径
    # 证明不了"这里真的有列分界"，把切开的部分当成跨列合并 cell 等于凭空造合并。
    "column_boundary_cuts_fragment",
    # 片段被两个 cell 同时覆盖（重叠消费）
    "fragment_consumed_twice",
    # 网格里存在没有任何真实片段解释的空位（既不是合并延续，也不是空 cell）
    "unexplained_hole",
    # 候选矩形内没有任何真实 PageLayout 片段
    "no_source_fragments",
    # 稳定列带数不足（borderless 路径无法证明">= 2 列"）
    "column_band_count_below_min",
    # 相邻两条列带**从未在同一个行带里同时出现** ⇒ 这两列不是同一张表的列，
    # 而是两处互不相干的 x 位置（按位置相邻硬切出来的"列"）
    "column_boundary_not_co_witnessed",
    # 合并锚点与其覆盖位不自洽
    "merged_anchor_inconsistent",
    # 两个 cell 的占地**重叠**（或越出网格行列范围）⇒ 网格不是真分区；`to-4` 无法
    # 诚实表达（`TableObjectV4._check_grid` 会当场拒绝）。这是 `CellGridPlan` 自己
    # 声明的闭合条件之一（"覆盖不重叠、不越界"），此前未被实现。
    "cell_coverage_overlap",
)

#: 合并单元格的 rowspan 只有在**真实几何**上成立才被接受：
#: 被合并掉的位必须确实落在锚点 cell 的 bbox 纵向范围内（含容差）。
GRID_ROWSPAN_COVER_MIN_RATIO = 0.5


def pdfplumber_engine_versions() -> dict:
    """引擎版本三元组。缺失引擎即 fail-closed（不得静默降级）。"""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - 依赖缺失时的明确失败
        raise TableGeometryError(f"pdfplumber 不可用：{e}") from e
    versions = {"pdfplumber": getattr(pdfplumber, "__version__", None)}
    try:
        import pymupdf
        versions["pymupdf"] = getattr(pymupdf, "__doc__", "") or ""
    except ImportError:  # pragma: no cover
        try:
            import fitz
            versions["pymupdf"] = getattr(fitz, "__doc__", "") or ""
        except ImportError:
            versions["pymupdf"] = ""
    if not versions["pdfplumber"]:
        raise TableGeometryError("pdfplumber 版本无法读取（fail-closed）")
    return versions


def _settings_payload() -> dict:
    return {
        "geometry_version": V.TABLE_GEOMETRY_VERSION,
        "strategies": list(GEOMETRY_STRATEGIES),
        "ruled_settings": RULED_TABLE_SETTINGS,
        "text_settings": TEXT_TABLE_SETTINGS,
        "borderless_cluster_params": BORDERLESS_CLUSTER_PARAMS,
        "default_tolerance": TABLE_GEOMETRY_DEFAULT_TOLERANCE,
        "rule_stroke_max_thickness": RULE_STROKE_MAX_THICKNESS_PT,
        "engine_versions": pdfplumber_engine_versions(),
    }


@dataclass(frozen=True)
class TableGeometrySettings:
    """几何 settings 的**不可变**快照（版本 + 引擎 + 全部固定参数）。"""

    geometry_version: str
    strategy: str
    tolerance: float
    table_settings: dict
    #: 规线厚度上界（见 `RULE_STROKE_MAX_THICKNESS_PT`）。`lines` 通道据此**额外**
    #: 跑一路"只用细长描边"的检测；`text` 通道不使用它，但同样登记进指纹，使
    #: "哪一版规则产出了这批候选"在任何策略下都可复核。
    rule_stroke_max_thickness: float
    borderless_params: dict
    engine_versions: dict
    settings_fingerprint: str

    def __post_init__(self) -> None:
        t = "TableGeometrySettings"
        if self.geometry_version != V.TABLE_GEOMETRY_VERSION:
            _err(t, f"geometry_version 必须为 {V.TABLE_GEOMETRY_VERSION!r}，"
                    f"得到 {self.geometry_version!r}")
        if self.strategy not in GEOMETRY_STRATEGIES:
            _err(t, f"strategy 必须属于 {GEOMETRY_STRATEGIES}，"
                    f"得到 {self.strategy!r}")
        if quantize(self.tolerance) != quantize(TABLE_GEOMETRY_DEFAULT_TOLERANCE):
            _err(t, "tolerance 是固定值，不得放大或缩小："
                    f"{self.tolerance!r}")
        if not isinstance(self.table_settings, dict) or not self.table_settings:
            _err(t, "table_settings 必须为非空对象")
        if quantize(self.rule_stroke_max_thickness) != quantize(
                RULE_STROKE_MAX_THICKNESS_PT):
            _err(t, "rule_stroke_max_thickness 是固定值，不得放大或缩小："
                    f"{self.rule_stroke_max_thickness!r}")
        if not isinstance(self.borderless_params, dict) or \
                not self.borderless_params:
            _err(t, "borderless_params 必须为非空对象")
        for name in ("settings_fingerprint",):
            v = getattr(self, name)
            if not isinstance(v, str) or len(v) != 64:
                _err(t, f"{name} 必须为 sha256，得到 {v!r}")
        expected = sha256_canonical(self.fingerprint_payload())
        if self.settings_fingerprint != expected:
            _err(t, "settings_fingerprint 与载荷重算不一致："
                    f"{self.settings_fingerprint!r} != {expected!r}")

    def fingerprint_payload(self) -> dict:
        return {
            "geometry_version": self.geometry_version,
            "strategy": self.strategy,
            "tolerance": quantize(self.tolerance),
            "table_settings": self.table_settings,
            "rule_stroke_max_thickness": quantize(self.rule_stroke_max_thickness),
            "borderless_params": self.borderless_params,
            "engine_versions": self.engine_versions,
        }

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableGeometrySettings",
            "geometry_version": self.geometry_version,
            "strategy": self.strategy,
            "tolerance": quantize(self.tolerance),
            "table_settings": self.table_settings,
            "rule_stroke_max_thickness": quantize(self.rule_stroke_max_thickness),
            "borderless_params": self.borderless_params,
            "engine_versions": self.engine_versions,
            "settings_fingerprint": self.settings_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableGeometrySettings":
        t = "TableGeometrySettings"
        d = _reject_unknown(d, {
            "schema_type", "geometry_version", "strategy", "tolerance",
            "table_settings", "rule_stroke_max_thickness", "borderless_params",
            "engine_versions", "settings_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("TableGeometrySettings",))
        payload = {
            "geometry_version": _need_str(d, "geometry_version", t),
            "strategy": _need_enum(d, "strategy", t, GEOMETRY_STRATEGIES),
            "tolerance": _need_num(d, "tolerance", t, lo=0.0),
            "table_settings": d.get("table_settings"),
            "rule_stroke_max_thickness": _need_num(
                d, "rule_stroke_max_thickness", t, lo=0.0),
            "borderless_params": d.get("borderless_params"),
            "engine_versions": d.get("engine_versions"),
        }
        payload["settings_fingerprint"] = sha256_canonical(
            {k: (quantize(v) if k == "tolerance" else v)
             for k, v in payload.items()})
        return cls(**payload)


def geometry_settings(strategy: str = "lines") -> TableGeometrySettings:
    """构造固定 settings（唯一入口）。`strategy` 之外的参数不可由调用方更改。"""
    if strategy not in GEOMETRY_STRATEGIES:
        raise TableGeometryError(f"未知 strategy {strategy!r}（已登记 "
                                 f"{GEOMETRY_STRATEGIES}）")
    payload = _settings_payload()
    body = {
        "geometry_version": payload["geometry_version"],
        "strategy": strategy,
        "tolerance": TABLE_GEOMETRY_DEFAULT_TOLERANCE,
        "table_settings": payload["ruled_settings"] if strategy == "lines"
        else payload["text_settings"],
        "rule_stroke_max_thickness": payload["rule_stroke_max_thickness"],
        "borderless_params": payload["borderless_cluster_params"],
        "engine_versions": payload["engine_versions"],
    }
    fp = sha256_canonical(
        {k: (quantize(v) if k == "tolerance" else v) for k, v in body.items()})
    return TableGeometrySettings(settings_fingerprint=fp, **body)


# ---------------------------------------------------------------------------
# 坐标变换
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageGeometryFrame:
    """一页的坐标变换（pdfplumber top-left ↔ PageLayout bbox）。

    两者**都以左上角为原点**，因此变换只有"轴序 + 缩放 + 平移"：

    ``layout_x = (pdf_x - pdf_x0) * scale_x + layout_x0``
    ``layout_y = (pdf_y - pdf_y0) * scale_y + layout_y0``

    `scale` 由"两套页尺寸的比值"给出，**不是**可调参数：当两套尺寸在
    `tolerance` 内相等时 `scale == 1.0`、偏移为 0；否则该页判为
    `unresolved_geometry`（本函数不会有非 1 的 scale 通过 `align_page_frame`）。
    """

    page_number: int
    axis_order: str
    rotation: int
    pdf_width: float
    pdf_height: float
    layout_width: float
    layout_height: float
    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    crop_box: tuple[float, float, float, float]
    media_box: tuple[float, float, float, float]
    tolerance: float
    frame_fingerprint: str

    def __post_init__(self) -> None:
        t = "PageGeometryFrame"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        if self.axis_order not in AXIS_ORDERS:
            _err(t, f"axis_order 必须属于 {AXIS_ORDERS}，得到 {self.axis_order!r}")
        if self.rotation not in (0, 90, 180, 270):
            _err(t, f"rotation 必须属于 (0,90,180,270)，得到 {self.rotation!r}")
        for name in ("pdf_width", "pdf_height", "layout_width", "layout_height"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) \
                    or not math.isfinite(float(v)) or float(v) <= 0:
                _err(t, f"{name} 必须为正的有限实数，得到 {v!r}")
        if self.axis_order == "direct":
            want = (self.pdf_width, self.pdf_height)
        else:
            want = (self.pdf_height, self.pdf_width)
        if abs(want[0] - self.layout_width) > self.tolerance or \
                abs(want[1] - self.layout_height) > self.tolerance:
            _err(t, "页尺寸超出容差即不得建立变换（必须 unresolved_geometry）")
        for name in ("scale_x", "scale_y"):
            v = float(getattr(self, name))
            if abs(v - 1.0) > 1e-9:
                _err(t, f"{name} 必须为 1.0（不做非等比缩放），得到 {v!r}")
        for name in ("offset_x", "offset_y"):
            if abs(float(getattr(self, name))) > 1e-9:
                _err(t, f"{name} 必须为 0.0，得到 {getattr(self, name)!r}")
        for name in ("crop_box", "media_box"):
            box = getattr(self, name)
            if not isinstance(box, tuple) or len(box) != 4:
                _err(t, f"{name} 必须为长度 4 的元组")
        expected = sha256_canonical(self.fingerprint_payload())
        if self.frame_fingerprint != expected:
            _err(t, "frame_fingerprint 与载荷重算不一致")

    def fingerprint_payload(self) -> dict:
        return {
            "page_number": self.page_number,
            "axis_order": self.axis_order,
            "rotation": self.rotation,
            "pdf_width": quantize(self.pdf_width),
            "pdf_height": quantize(self.pdf_height),
            "layout_width": quantize(self.layout_width),
            "layout_height": quantize(self.layout_height),
            "scale_x": quantize(self.scale_x),
            "scale_y": quantize(self.scale_y),
            "offset_x": quantize(self.offset_x),
            "offset_y": quantize(self.offset_y),
            "crop_box": [quantize(v) for v in self.crop_box],
            "media_box": [quantize(v) for v in self.media_box],
            "tolerance": quantize(self.tolerance),
        }

    def to_dict(self) -> dict:
        payload = self.fingerprint_payload()
        payload["schema_type"] = "PageGeometryFrame"
        payload["frame_fingerprint"] = self.frame_fingerprint
        return payload

    @classmethod
    def from_dict(cls, d: Any) -> "PageGeometryFrame":
        t = "PageGeometryFrame"
        d = _reject_unknown(d, {
            "schema_type", "page_number", "axis_order", "rotation",
            "pdf_width", "pdf_height", "layout_width", "layout_height",
            "scale_x", "scale_y", "offset_x", "offset_y", "crop_box",
            "media_box", "tolerance", "frame_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("PageGeometryFrame",))
        boxes = {}
        for name in ("crop_box", "media_box"):
            v = d.get(name)
            if not isinstance(v, list) or len(v) != 4:
                _err(t, f"{name} 必须为长度 4 的数组")
            for i, x in enumerate(v):
                if not isinstance(x, (int, float)) or isinstance(x, bool):
                    _err(t, f"{name}[{i}] 必须为实数")
            boxes[name] = tuple(float(x) for x in v)
        body = {
            "page_number": _need_int(d, "page_number", t, lo=1),
            "axis_order": _need_enum(d, "axis_order", t, AXIS_ORDERS),
            "rotation": _need_int(d, "rotation", t),
            "pdf_width": _need_num(d, "pdf_width", t, lo=0.0),
            "pdf_height": _need_num(d, "pdf_height", t, lo=0.0),
            "layout_width": _need_num(d, "layout_width", t, lo=0.0),
            "layout_height": _need_num(d, "layout_height", t, lo=0.0),
            "scale_x": _need_num(d, "scale_x", t, lo=0.0),
            "scale_y": _need_num(d, "scale_y", t, lo=0.0),
            "offset_x": _need_num(d, "offset_x", t),
            "offset_y": _need_num(d, "offset_y", t),
            "crop_box": boxes["crop_box"],
            "media_box": boxes["media_box"],
            "tolerance": _need_num(d, "tolerance", t, lo=0.0),
            "frame_fingerprint": _need_str(d, "frame_fingerprint", t),
        }
        return cls(**body)

    # -- 变换（唯一公式） --------------------------------------------------

    def pdf_bbox_to_layout(self, bbox: Sequence[float]) -> tuple[float, ...]:
        """把 pdfplumber 的 `(x0, top, x1, bottom)` 变成 PageLayout 坐标（量化）。"""
        if len(bbox) != 4:
            _err("PageGeometryFrame", "bbox 必须为长度 4 的序列")
        x0, top, x1, bottom = (float(v) for v in bbox)
        if self.axis_order == "direct":
            ax0, atop, ax1, abottom = x0, top, x1, bottom
        else:
            ax0, atop = top, x0
            ax1, abottom = bottom, x1
        lx0 = (ax0 - 0.0) * self.scale_x + self.offset_x
        ltop = (atop - 0.0) * self.scale_y + self.offset_y
        lx1 = (ax1 - 0.0) * self.scale_x + self.offset_x
        lbottom = (abottom - 0.0) * self.scale_y + self.offset_y
        lo_x, hi_x = min(lx0, lx1), max(lx0, lx1)
        lo_y, hi_y = min(ltop, lbottom), max(ltop, lbottom)
        if lo_x < -self.tolerance or lo_y < -self.tolerance:
            _err("PageGeometryFrame",
                 f"变换后 bbox 落在页面外：{(lo_x, lo_y, hi_x, hi_y)}")
        if hi_x > self.layout_width + self.tolerance or \
                hi_y > self.layout_height + self.tolerance:
            _err("PageGeometryFrame",
                 f"变换后 bbox 越过页面边界：{(lo_x, lo_y, hi_x, hi_y)}")
        return (quantize(lo_x), quantize(lo_y), quantize(hi_x), quantize(hi_y))


def align_page_frame(*, layout_page: Any, pdf_page: Any, tolerance: float =
                     TABLE_GEOMETRY_DEFAULT_TOLERANCE) -> PageGeometryFrame | None:
    """建立该页的坐标变换；**无法唯一对齐时返回 `None`**。

    唯一性条件（缺一不可）：

    - `rotation` 两套一致；
    - 轴序只有两种候选（`direct` / `swapped`），且**恰好一种**在容差内成立。

    两种都成立（正方形页）时不算唯一 —— 返回 `None`，不允许"任选一种"。
    """
    if tolerance != TABLE_GEOMETRY_DEFAULT_TOLERANCE:
        raise TableGeometryError("容差是固定值，不得放大或缩小")
    if layout_page.rotation != int(round(float(pdf_page.rotation or 0))):
        return None
    pdf_w = float(pdf_page.width)
    pdf_h = float(pdf_page.height)
    lay_w = float(layout_page.width)
    lay_h = float(layout_page.height)
    direct = abs(pdf_w - lay_w) <= tolerance and abs(pdf_h - lay_h) <= tolerance
    swapped = abs(pdf_h - lay_w) <= tolerance and abs(pdf_w - lay_h) <= tolerance
    if direct and swapped and abs(pdf_w - pdf_h) <= tolerance:
        return None
    if direct:
        axis_order = "direct"
    elif swapped:
        axis_order = "swapped"
    else:
        return None
    crop = getattr(pdf_page, "cropbox", None)
    media = getattr(pdf_page, "mediabox", None)
    crop_box = (float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3])) \
        if crop is not None else (0.0, 0.0, pdf_w, pdf_h)
    media_box = (float(media[0]), float(media[1]), float(media[2]),
                 float(media[3])) if media is not None else (0.0, 0.0, pdf_w, pdf_h)
    body = {
        "page_number": int(layout_page.page_number),
        "axis_order": axis_order,
        "rotation": int(layout_page.rotation),
        "pdf_width": quantize(pdf_w),
        "pdf_height": quantize(pdf_h),
        "layout_width": quantize(lay_w),
        "layout_height": quantize(lay_h),
        "scale_x": 1.0,
        "scale_y": 1.0,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "crop_box": tuple(quantize(v) for v in crop_box),
        "media_box": tuple(quantize(v) for v in media_box),
        "tolerance": quantize(tolerance),
    }
    fp = sha256_canonical(
        {k: ([quantize(x) for x in v] if isinstance(v, tuple) else
             (quantize(v) if isinstance(v, float) else v))
         for k, v in body.items()})
    return PageGeometryFrame(frame_fingerprint=fp, **body)


# ---------------------------------------------------------------------------
# 候选
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeometryCandidate:
    """一个**候选**矩形。它不是表；正式资格由 builder 的统一硬门判定。"""

    candidate_source: str
    strategy: str
    page_number: int
    bbox: tuple[float, float, float, float]
    frame_fingerprint: str
    row_count: int
    column_count: int
    #: 行×列网格里**实有** cell 数（pdfplumber 会把合并单元格的延续位记为 None）。
    cell_count: int
    #: 合并单元格证据：`(row_index, column_index, rowspan, colspan)`。
    merged_cells: tuple[tuple[int, int, int, int], ...]
    #: **真实网格骨架**：`(row_index, column_index, rowspan, colspan, bbox)`，
    #: bbox 已经在 **PageLayout 坐标系**里。仅当通道自带网格（pdfplumber 的
    #: `table.cells`）时非空。空元组表示"本通道没有网格骨架"——那时 cell 网格
    #: 必须由 `derive_cell_grid` 从真实 PageLayout 行/片段重建，且**不可能**
    #: 证明合并单元格，因此只能是 `partial`。
    grid_cells: tuple[tuple[int, int, int, int, tuple[float, float, float, float]],
                      ...]
    #: 是否闭合网格（外框 + 全部内线都由真实边给出）。
    closed_grid: bool
    #: 结构缺口计数（缺失的内线 / 未解释空洞），越小越好。
    structural_gap_count: int
    edge_count: int
    intersection_count: int
    #: 该候选消费的 PageLayout source fragment 数（供 builder 做覆盖比较）。
    source_fragment_count: int
    #: 不可裁决时的原因（`None` = 可裁决）。
    unresolved_reason: str | None

    def __post_init__(self) -> None:
        t = "GeometryCandidate"
        if self.candidate_source not in CANDIDATE_SOURCES:
            _err(t, f"candidate_source 必须属于 {CANDIDATE_SOURCES}，"
                    f"得到 {self.candidate_source!r}")
        if self.strategy not in GEOMETRY_STRATEGIES:
            _err(t, f"strategy 必须属于 {GEOMETRY_STRATEGIES}，"
                    f"得到 {self.strategy!r}")
        _need_int({"v": self.page_number}, "v", t, lo=1)
        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            _err(t, "bbox 必须为长度 4 的元组")
        for i, v in enumerate(self.bbox):
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                _err(t, f"bbox[{i}] 必须为实数")
        if self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]:
            _err(t, "bbox 必须满足 x1 > x0 且 y1 > y0（正面积）")
        for name in ("row_count", "column_count", "cell_count",
                     "structural_gap_count", "edge_count",
                     "intersection_count", "source_fragment_count"):
            _need_int({"v": getattr(self, name)}, "v", t, lo=0)
        if not isinstance(self.closed_grid, bool):
            _err(t, "closed_grid 必须为 bool")
        if self.closed_grid and self.structural_gap_count != 0:
            _err(t, "闭合网格不得携带结构缺口")
        if self.unresolved_reason is not None and \
                self.unresolved_reason not in UNRESOLVED_GEOMETRY_REASONS:
            _err(t, f"unresolved_reason 必须属于 {UNRESOLVED_GEOMETRY_REASONS}，"
                    f"得到 {self.unresolved_reason!r}")
        if self.strategy == "lines" and not self.closed_grid:
            # 线策略下未闭合不是错误，但必须**显式记账**，不得沉默。
            if self.structural_gap_count == 0:
                _err(t, "线策略下未闭合网格必须给出 structural_gap_count > 0")
        seen: set[tuple[int, int]] = set()
        for span in self.merged_cells:
            if not isinstance(span, tuple) or len(span) != 4:
                _err(t, "merged_cells 每项必须为 (r, c, rowspan, colspan)")
            r, c, rs, cs = span
            for v in span:
                _need_int({"v": v}, "v", t, lo=0)
            if rs < 1 or cs < 1:
                _err(t, "merged cell 的 rowspan/colspan 必须 >= 1")
            if (r, c) in seen:
                _err(t, f"merged_cells 有重复锚点 {(r, c)}")
            seen.add((r, c))
            if r + rs > self.row_count or c + cs > self.column_count:
                _err(t, f"merged cell {(r, c, rs, cs)} 越过网格范围")
        if not isinstance(self.grid_cells, tuple):
            _err(t, "grid_cells 必须为元组")
        anchors: set[tuple[int, int]] = set()
        occupied: set[tuple[int, int]] = set()
        boxes: list[tuple[float, float, float, float]] = []
        for i, item in enumerate(self.grid_cells):
            if not isinstance(item, tuple) or len(item) != 5:
                _err(t, f"grid_cells[{i}] 必须为 (r, c, rowspan, colspan, bbox)")
            r, c, rs, cs, box = item
            for v in (r, c, rs, cs):
                _need_int({"v": v}, "v", t, lo=0)
            if rs < 1 or cs < 1:
                _err(t, f"grid_cells[{i}] 的 rowspan/colspan 必须 >= 1")
            if r + rs > self.row_count or c + cs > self.column_count:
                _err(t, f"grid_cells[{i}] 越过网格范围")
            if (r, c) in anchors:
                _err(t, f"grid_cells 锚点重复：{(r, c)}")
            anchors.add((r, c))
            for dr in range(rs):
                for dc in range(cs):
                    key = (r + dr, c + dc)
                    if key in occupied:
                        _err(t, f"grid_cells 覆盖重叠于 {key}")
                    occupied.add(key)
            if (not isinstance(box, tuple) or len(box) != 4
                    or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                           for v in box)):
                _err(t, f"grid_cells[{i}].bbox 必须为长度 4 的实数元组")
            q = tuple(quantize(float(v)) for v in box)
            if q[2] <= q[0] or q[3] <= q[1]:
                _err(t, f"grid_cells[{i}].bbox 必须为正面积矩形")
            tol = TABLE_GEOMETRY_DEFAULT_TOLERANCE
            if (q[0] < self.bbox[0] - tol or q[1] < self.bbox[1] - tol
                    or q[2] > self.bbox[2] + tol or q[3] > self.bbox[3] + tol):
                _err(t, f"grid_cells[{i}].bbox 越出候选矩形")
            boxes.append(q)
        if self.grid_cells:
            if (self.row_count, self.column_count) == (0, 0):
                _err(t, "带网格骨架的候选必须有非零行/列数")
            if len(occupied) != self.row_count * self.column_count:
                _err(t, "网格骨架必须铺满 row_count × column_count（不得有空洞）")
            spans = {(r, c, rs, cs) for (r, c, rs, cs, _b) in self.grid_cells
                     if rs > 1 or cs > 1}
            if spans != set(self.merged_cells):
                _err(t, "merged_cells 必须与网格骨架导出的合并锚点一致")

    # -- 排序（§19.5.3） ---------------------------------------------------

    def geometry_rank_key(self) -> tuple:
        """**仅几何**可裁决部分的裁决键（越小越优先）。

        顺序：闭合网格 > 结构缺口更少 > bbox 更小（面积）。

        这里**不含**页码/坐标/来源这类"必然可区分"的字段：§19.5.3 明确承认
        "仍并列"这一结局，因此裁决键必须在语义上真的可能相等；确定性排序由
        `sort_candidates` 单独负责，两者不得混用。
        """
        return (0 if self.closed_grid else 1, self.structural_gap_count,
                quantize((self.bbox[2] - self.bbox[0])
                         * (self.bbox[3] - self.bbox[1])))

    def to_dict(self) -> dict:
        return {
            "schema_type": "GeometryCandidate",
            "candidate_source": self.candidate_source,
            "strategy": self.strategy,
            "page_number": self.page_number,
            "bbox": [quantize(v) for v in self.bbox],
            "frame_fingerprint": self.frame_fingerprint,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "cell_count": self.cell_count,
            "merged_cells": [list(s) for s in self.merged_cells],
            "grid_cells": [[r, c, rs, cs, [quantize(v) for v in box]]
                           for (r, c, rs, cs, box) in self.grid_cells],
            "closed_grid": self.closed_grid,
            "structural_gap_count": self.structural_gap_count,
            "edge_count": self.edge_count,
            "intersection_count": self.intersection_count,
            "source_fragment_count": self.source_fragment_count,
            "unresolved_reason": self.unresolved_reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "GeometryCandidate":
        t = "GeometryCandidate"
        d = _reject_unknown(d, {
            "schema_type", "candidate_source", "strategy", "page_number", "bbox",
            "frame_fingerprint", "row_count", "column_count", "cell_count",
            "merged_cells", "grid_cells", "closed_grid",
            "structural_gap_count", "edge_count", "intersection_count",
            "source_fragment_count", "unresolved_reason"}, t)
        _need_enum(d, "schema_type", t, ("GeometryCandidate",))
        bbox = d.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            _err(t, "bbox 必须为长度 4 的数组")
        merged = d.get("merged_cells")
        if not isinstance(merged, list):
            _err(t, "merged_cells 必须为数组")
        spans: list[tuple[int, int, int, int]] = []
        for i, s in enumerate(merged):
            if not isinstance(s, list) or len(s) != 4:
                _err(t, f"merged_cells[{i}] 必须为长度 4 的数组")
            spans.append(tuple(int(x) for x in s))
        raw_cells = d.get("grid_cells")
        if not isinstance(raw_cells, list):
            _err(t, "grid_cells 必须为数组")
        cells: list[tuple[int, int, int, int, tuple[float, float, float, float]]] = []
        for i, item in enumerate(raw_cells):
            if not isinstance(item, list) or len(item) != 5:
                _err(t, f"grid_cells[{i}] 必须为长度 5 的数组")
            box = item[4]
            if not isinstance(box, list) or len(box) != 4:
                _err(t, f"grid_cells[{i}][4] 必须为长度 4 的数组")
            cells.append((int(item[0]), int(item[1]), int(item[2]), int(item[3]),
                          tuple(quantize(float(v)) for v in box)))
        return cls(
            candidate_source=_need_enum(d, "candidate_source", t,
                                        CANDIDATE_SOURCES),
            strategy=_need_enum(d, "strategy", t, GEOMETRY_STRATEGIES),
            page_number=_need_int(d, "page_number", t, lo=1),
            bbox=tuple(quantize(float(v)) for v in bbox),
            frame_fingerprint=_need_str(d, "frame_fingerprint", t),
            row_count=_need_int(d, "row_count", t, lo=0),
            column_count=_need_int(d, "column_count", t, lo=0),
            cell_count=_need_int(d, "cell_count", t, lo=0),
            merged_cells=tuple(spans),
            grid_cells=tuple(cells),
            closed_grid=_need_bool(d, "closed_grid", t),
            structural_gap_count=_need_int(d, "structural_gap_count", t, lo=0),
            edge_count=_need_int(d, "edge_count", t, lo=0),
            intersection_count=_need_int(d, "intersection_count", t, lo=0),
            source_fragment_count=_need_int(d, "source_fragment_count", t, lo=0),
            unresolved_reason=_need_str(d, "unresolved_reason", t, none_ok=True),
        )


def _bbox_overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """相交面积（0 = 不相交）。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return float(w) * float(h)


def candidate_order_key(candidate: GeometryCandidate,
                        coverage_hint: dict | None = None) -> tuple:
    """完整**裁决**键（§19.5.3）：闭合 > 覆盖更全 > 缺口更少 > bbox 更小。

    `coverage_hint` 由 builder 提供（候选 bbox 内已被 TS4 component/evidence 覆盖的
    片段数）；缺失时该维度整体取出，几何层**不猜**，也因此不会因缺数据而伪胜出。

    本键**刻意**不含页码/坐标：语义上真的可能相等的键才允许产生"仍并列"。
    """
    closed_rank, gap, area = candidate.geometry_rank_key()
    if coverage_hint is None:
        return (closed_rank, gap, area)
    coverage = int(coverage_hint.get((candidate.page_number, candidate.bbox), 0))
    return (closed_rank, -coverage, gap, area)


def _stable_sort_key(candidate: GeometryCandidate) -> tuple:
    """**排序用**（非裁决用）的稳定键：保证输出与输入顺序无关。"""
    return (candidate.page_number, candidate.bbox, candidate.candidate_source,
            candidate.strategy)


def sort_candidates(candidates: Sequence[GeometryCandidate],
                    coverage_hint: dict | None = None) \
        -> tuple[GeometryCandidate, ...]:
    """确定性排序：先按裁决键，再按与输入顺序无关的稳定键。"""
    return tuple(sorted(candidates,
                        key=lambda c: (candidate_order_key(c, coverage_hint),
                                       _stable_sort_key(c))))


def resolve_overlapping_candidates(
        candidates: Sequence[GeometryCandidate],
        coverage_hint: dict | None = None) \
        -> tuple[tuple[GeometryCandidate, ...], tuple[GeometryCandidate, ...]]:
    """同页重叠候选的**唯一**裁决：返回 `(胜出, 仍不可裁决)`。

    规则（§19.5.3）：

    - 无重叠的候选直接胜出；
    - 有重叠时取裁决键最小者；若**最小键被多个候选同时取得**，则这些候选整体进
      `unresolved_geometry`（不允许"取第一张"）；键更大的落选者被淘汰出该组；
    - 因此同一页重叠组要么给出唯一赢家，要么整组不可裁决，不存在第三种结果。
    """
    items = list(candidates)
    winners: list[GeometryCandidate] = []
    unresolved: list[GeometryCandidate] = []
    for cand in items:
        if any(_bbox_overlap(cand.bbox, other.bbox) > 0.0
               and other.page_number == cand.page_number
               for other in items if other is not cand):
            continue
        winners.append(cand)
    clashed = [c for c in items if c not in winners]
    while clashed:
        seed = min(clashed, key=lambda c: (_stable_sort_key(c),
                                           candidate_order_key(c, coverage_hint)))
        group = [seed]
        for other in clashed:
            if other is seed:
                continue
            if any(_bbox_overlap(other.bbox, member.bbox) > 0.0
                   for member in group):
                group.append(other)
        best = min(candidate_order_key(c, coverage_hint) for c in group)
        at_best = [c for c in group if candidate_order_key(c, coverage_hint) == best]
        if len(at_best) == 1:
            winners.append(at_best[0])
        else:
            unresolved.extend(at_best)
        for member in group:
            clashed.remove(member)
    dedup: dict[tuple, GeometryCandidate] = {}
    for cand in unresolved:
        dedup.setdefault((cand.page_number, cand.bbox), cand)
    return tuple(winners), tuple(dedup.values())


# ---------------------------------------------------------------------------
# borderless 聚类（§19.5.1 第 5 条：仅作第二佐证）
# ---------------------------------------------------------------------------

def cluster_borderless_rows(layout_page: Any, *, tolerance: float =
                            BORDERLESS_CLUSTER_PARAMS["band_gap_tolerance"]) \
        -> tuple[tuple[float, float, float, float], int, int] | None:
    """用**真实 LayoutLine bbox** 聚类出 borderless 行带与稳定 x 列。

    返回 `(bbox, row_count, column_count)`；不成立时返回 `None`。

    本函数只吃 `LayoutLine.bbox` / `is_furniture`，不吃任何文本内容，因此与公司、
    语言和表号无关。它只是**第二佐证**：正式资格仍由 builder 的统一硬门判定。
    """
    lines = [ln for ln in layout_page.lines if not ln.is_furniture]
    if not lines:
        return None
    bands: list[list[Any]] = []
    for ln in sorted(lines, key=lambda x: (x.bbox[1], x.bbox[0], x.line_index)):
        if bands:
            prev_top = min(x.bbox[1] for x in bands[-1])
            prev_bottom = max(x.bbox[3] for x in bands[-1])
            overlap = min(prev_bottom, ln.bbox[3]) - max(prev_top, ln.bbox[1])
            height = min(prev_bottom - prev_top, ln.bbox[3] - ln.bbox[1])
            if height > 0 and overlap / height >= \
                    BORDERLESS_CLUSTER_PARAMS["row_overlap_min_ratio"]:
                bands[-1].append(ln)
                continue
            if abs(ln.bbox[1] - prev_bottom) <= tolerance and \
                    abs(ln.bbox[3] - prev_top) <= tolerance:
                bands[-1].append(ln)
                continue
        bands.append([ln])
    min_rows = BORDERLESS_CLUSTER_PARAMS["min_rows"]
    min_cols = BORDERLESS_CLUSTER_PARAMS["min_columns"]
    min_span_cols = BORDERLESS_CLUSTER_PARAMS["min_row_span_columns"]
    col_tol = BORDERLESS_CLUSTER_PARAMS["column_tolerance"]
    # 每个行带内的 span 左边界构成该行的列锚点。
    anchors: list[list[float]] = []
    for band in bands:
        starts: list[float] = []
        for ln in sorted(band, key=lambda x: x.bbox[0]):
            for i, sp in enumerate(ln.spans):
                x0 = sp.bbox[0]
                seg = (i == 0) or (sp.bbox[0] - ln.spans[i - 1].bbox[2] > col_tol)
                if seg:
                    starts.append(x0)
        anchors.append(sorted(starts))
    best: tuple[int, tuple[float, ...]] | None = None
    for row in range(len(bands) - min_rows + 1):
        window = anchors[row:row + min_rows]
        if any(len(a) < min_span_cols for a in window):
            continue
        # 稳定列 = 窗口内每个行带都能在 col_tol 内找到匹配的锚点。
        base = window[0]
        stable: list[float] = []
        for x in base:
            if all(any(abs(y - x) <= col_tol for y in w) for w in window):
                stable.append(x)
        if len(stable) < min_cols:
            continue
        if best is None or len(stable) > len(best[1]):
            best = (row, tuple(stable))
    if best is None:
        return None
    row, stable = best
    span_bands = bands[row:row + min_rows]
    for extra in range(row + min_rows, len(bands)):
        w = anchors[extra]
        if all(any(abs(y - x) <= col_tol for y in w) for x in stable):
            span_bands = span_bands + [bands[extra]]
        else:
            break
    top = min(x.bbox[1] for band in span_bands for x in band)
    bottom = max(x.bbox[3] for band in span_bands for x in band)
    left = min(x.bbox[0] for band in span_bands for x in band)
    right = max(x.bbox[2] for band in span_bands for x in band)
    if right <= left or bottom <= top:
        return None
    return ((quantize(left), quantize(top), quantize(right), quantize(bottom)),
            len(span_bands), len(stable))


# ---------------------------------------------------------------------------
# cell 网格重建（§19.5.1 "cell 覆盖不重叠、不越界、无未解释空洞"）
# ---------------------------------------------------------------------------

def _fragments_in_line(line: Any, *, column_tolerance: float) -> list[tuple[int, int]]:
    """把一行内的 span 按"x 间隙 <= column_tolerance"合成视觉片段。

    返回 `(span_start, span_end)` 闭区间列表（升序、互不重叠）。本函数**只看
    bbox 与 span 顺序**，不看文本，因此与语言/公司/表号无关。
    """
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(line.spans)):
        gap = line.spans[i].bbox[0] - line.spans[i - 1].bbox[2]
        if gap > column_tolerance:
            out.append((start, i - 1))
            start = i
    out.append((start, len(line.spans) - 1))
    return out


def _fragment_bbox(line: Any, frag: tuple[int, int]) -> tuple[float, ...]:
    a, b = frag
    xs0 = min(line.spans[i].bbox[0] for i in range(a, b + 1))
    ys0 = min(line.spans[i].bbox[1] for i in range(a, b + 1))
    xs1 = max(line.spans[i].bbox[2] for i in range(a, b + 1))
    ys1 = max(line.spans[i].bbox[3] for i in range(a, b + 1))
    return (quantize(xs0), quantize(ys0), quantize(xs1), quantize(ys1))


@dataclass(frozen=True)
class GridCellPlan:
    """一个 cell 的**几何计划**：位置、跨度和它实际消费的真实片段。

    它**不含**文本与角色：文本重建、块角色、来源引用与可引用性都属于 builder 的
    组装职责。本类型只是"哪些 span 落在哪个 cell 里"的机械结论。
    """

    row: int
    column: int
    rowspan: int
    colspan: int
    bbox: tuple[float, float, float, float]
    #: 消费的真实片段，按 (行号, span 起始, span 结束) 升序；闭区间。
    fragment_keys: tuple[tuple[int, int, int], ...]

    def to_dict(self) -> dict:
        return {
            "schema_type": "GridCellPlan",
            "row": self.row, "column": self.column,
            "rowspan": self.rowspan, "colspan": self.colspan,
            "bbox": [quantize(v) for v in self.bbox],
            "fragment_keys": [list(k) for k in self.fragment_keys],
        }


@dataclass(frozen=True)
class CellGridPlan:
    """候选矩形内的 cell 网格重建结果。

    `problems` 为空**当且仅当**网格闭合：覆盖不重叠、不越界、无未解释空洞。
    非空时该候选**不得**成为完整表（只能 `partial` 或直接不入选）。
    """

    page_number: int
    bbox: tuple[float, float, float, float]
    row_count: int
    column_count: int
    cells: tuple[GridCellPlan, ...]
    #: 被本网格消费的行号（真实 LayoutLine 的 line_index，升序）。
    assigned_lines: tuple[int, ...]
    #: 越出候选矩形的行号：诚实记账，不夹取、不消费。
    straddling_lines: tuple[int, ...]
    #: 网格骨架是否有真实几何来源（pdfplumber 的 ruled/text grid）。
    geometry_backed: bool
    problems: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "CellGridPlan"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        for name in ("row_count", "column_count"):
            _need_int({"v": getattr(self, name)}, "v", t, lo=0)
        for pr in self.problems:
            if pr not in GRID_PROBLEM_CODES:
                _err(t, f"问题码必须属于 {GRID_PROBLEM_CODES}，得到 {pr!r}")
        if self.problems != tuple(sorted(set(self.problems))):
            _err(t, "problems 必须去重且按字典序")

    @property
    def closed(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {
            "schema_type": "CellGridPlan",
            "page_number": self.page_number,
            "bbox": [quantize(v) for v in self.bbox],
            "row_count": self.row_count,
            "column_count": self.column_count,
            "cells": [c.to_dict() for c in self.cells],
            "assigned_lines": list(self.assigned_lines),
            "straddling_lines": list(self.straddling_lines),
            "geometry_backed": self.geometry_backed,
            "problems": list(self.problems),
        }


def _coverage_problems(cells: Sequence[GridCellPlan], rows: int,
                       cols: int) -> list[str]:
    """cell 占地必须是网格的**真分区**：既不重叠，也不越出 `rows × cols`。

    这是 `CellGridPlan` 自己声明的闭合条件（见该类型 docstring）里唯一此前**没有**
    实现的一条：`_grid_from_skeleton` / `_grid_from_bands` 各自只报自己那一路的问题，
    谁都不检查"两格是否占了同一格位"。列带路径在多列表头聚成若干个**各自跨列**的
    cell 时就会产生这种网格：每个 cell 的 `colspan` 单独看都通过了共同见证判据，
    合起来却互相重叠，而 `problems` 为空 —— 于是本不闭合的网格被当成闭合候选准入，
    直到建 `TableObjectV4` 时才因 cell 覆盖重叠抛 schema 错。这里只报**几何事实**，
    不修网格：重叠处的列带归属无法由真实片段唯一判定，猜一个就是造合并。
    """
    seen: dict[tuple[int, int], int] = {}
    overlapped = False
    out_of_range = False
    for i, c in enumerate(cells):
        for dr in range(c.rowspan):
            for dc in range(c.colspan):
                key = (c.row + dr, c.column + dc)
                if key[0] >= rows or key[1] >= cols:
                    out_of_range = True
                    continue
                if key in seen:
                    overlapped = True
                seen[key] = i
    if overlapped or out_of_range:
        return ["cell_coverage_overlap"]
    return []


def derive_cell_grid(layout_page: Any, *, candidate: GeometryCandidate,
                     tolerance: float = BORDERLESS_CLUSTER_PARAMS[
                         "band_gap_tolerance"],
                     contained_ratio: float = 0.95) -> CellGridPlan:
    """由**真实 PageLayout 片段**在候选矩形内重建 cell 网格（§19.5.1）。

    两条路径，**不混用**：

    - 候选自带网格骨架（pdfplumber 的 ruled/text grid）⇒ 直接采用其锚点矩形，
      真实片段按"片段中心落在锚点矩形内"归属。骨架是**真实几何**，因此
      rowspan/colspan 有据可依。
    - 候选没有骨架（frozen 范围 / 显式标记 / 文本旗标 / borderless 聚类）⇒
      行带由真实行 bbox 聚类、列带由跨行稳定 x 锚点聚类，片段按 bbox 重叠归属。
      这条路径**不产生任何 rowspan**：没有真实网格就证明不了纵向合并，宁可
      诚实报 `unexplained_hole`，也不把空位一律当成合并。

    本函数是**低层叶子原语**（只读坐标 + 原始版式），正式 builder 与独立
    verifier 都可以复用它；但"哪些 cell 成为什么角色/什么引用"不在这里。
    """
    boxes: list[tuple[float, float, float, float]] = []
    problems: list[str] = []
    assigned: list[Any] = []
    straddling: list[int] = []
    cb = tuple(quantize(v) for v in candidate.bbox)
    for ln in layout_page.lines:
        if ln.is_furniture:
            continue
        inter = _bbox_overlap(ln.bbox, cb)
        if inter <= 0.0:
            continue
        area = (ln.bbox[2] - ln.bbox[0]) * (ln.bbox[3] - ln.bbox[1])
        if area > 0 and inter / area >= contained_ratio:
            assigned.append(ln)
        else:
            straddling.append(int(ln.line_index))
    assigned.sort(key=lambda x: (x.bbox[1], x.bbox[0], x.line_index))
    if not assigned:
        problems.append("no_source_fragments")
        return CellGridPlan(
            page_number=int(layout_page.page_number), bbox=cb,
            row_count=0, column_count=0, cells=(),
            assigned_lines=(), straddling_lines=tuple(sorted(straddling)),
            geometry_backed=bool(candidate.grid_cells),
            problems=tuple(sorted(set(problems))))

    if candidate.grid_cells:
        cells, extra = _grid_from_skeleton(assigned, candidate)
    else:
        cells, extra = _grid_from_bands(assigned, cb, tolerance)
    problems.extend(extra)
    rows = max((c.row + c.rowspan for c in cells), default=0)
    cols = max((c.column + c.colspan for c in cells), default=0)
    problems.extend(_coverage_problems(cells, rows, cols))
    return CellGridPlan(
        page_number=int(layout_page.page_number), bbox=cb,
        row_count=rows, column_count=cols, cells=tuple(cells),
        assigned_lines=tuple(sorted(int(ln.line_index) for ln in assigned)),
        straddling_lines=tuple(sorted(straddling)),
        geometry_backed=bool(candidate.grid_cells),
        problems=tuple(sorted(set(problems))))


def _grid_from_skeleton(assigned: Sequence[Any], candidate: GeometryCandidate) \
        -> tuple[list[GridCellPlan], list[str]]:
    """骨架路径：真实网格矩形 + 片段按中心归属。"""
    tol = TABLE_GEOMETRY_DEFAULT_TOLERANCE
    cells: list[GridCellPlan] = []
    problems: list[str] = []
    filled: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    stray = 0
    for ln in assigned:
        for (a, b) in _fragments_in_line(
                ln, column_tolerance=BORDERLESS_CLUSTER_PARAMS["column_tolerance"]):
            fb = _fragment_bbox(ln, (a, b))
            cx = (fb[0] + fb[2]) / 2.0
            cy = (fb[1] + fb[3]) / 2.0
            hit: tuple[int, int] | None = None
            for (r, c, _rs, _cs, box) in candidate.grid_cells:
                if (box[0] - tol <= cx <= box[2] + tol
                        and box[1] - tol <= cy <= box[3] + tol):
                    hit = (r, c)
                    break
            if hit is None:
                stray += 1
                continue
            filled.setdefault(hit, []).append(
                (int(ln.line_index), a, b))
    if stray:
        problems.append("fragment_not_column_aligned")
    for (r, c, rs, cs, box) in candidate.grid_cells:
        keys = tuple(sorted(filled.get((r, c), ())))
        if not keys:
            problems.append("unexplained_hole")
            continue
        cells.append(GridCellPlan(row=r, column=c, rowspan=rs, colspan=cs,
                                  bbox=tuple(box), fragment_keys=keys))
    return cells, problems


def _grid_from_bands(assigned: Sequence[Any], cb: Sequence[float],
                     tolerance: float) -> tuple[list[GridCellPlan], list[str]]:
    """行带/列带路径：**不产生 rowspan**（没有真实网格就证明不了纵向合并）。"""
    col_tol = BORDERLESS_CLUSTER_PARAMS["column_tolerance"]
    overlap_ratio = BORDERLESS_CLUSTER_PARAMS["row_overlap_min_ratio"]
    problems: list[str] = []
    bands: list[list[Any]] = []
    for ln in assigned:
        if bands:
            prev = bands[-1]
            top = min(x.bbox[1] for x in prev)
            bottom = max(x.bbox[3] for x in prev)
            overlap = min(bottom, ln.bbox[3]) - max(top, ln.bbox[1])
            height = min(bottom - top, ln.bbox[3] - ln.bbox[1])
            if height > 0 and overlap / height >= overlap_ratio:
                prev.append(ln)
                continue
            if (abs(ln.bbox[1] - bottom) <= tolerance
                    and abs(ln.bbox[3] - top) <= tolerance):
                prev.append(ln)
                continue
        bands.append([ln])
    # 每个行带的片段（按 x 升序）。
    band_frags: list[list[tuple[Any, int, int, tuple]]] = []
    for band in bands:
        frs: list[tuple[Any, int, int, tuple]] = []
        for ln in band:
            for (a, b) in _fragments_in_line(ln, column_tolerance=col_tol):
                frs.append((ln, a, b, _fragment_bbox(ln, (a, b))))
        frs.sort(key=lambda x: (x[3][0], x[3][1], int(x[0].line_index)))
        band_frags.append(frs)
    # 列：以**片段左边界**做一维聚类（同一列的左边界在真实表格里对齐），
    # 并且必须出现在 >= 2 个行带里才是稳定列。用"与簇最小值之差"而非区间重叠，
    # 避免一个跨列片段把相邻两列桥接成同一簇。
    values = sorted({f[3][0] for frs in band_frags for f in frs})
    clusters: list[list[float]] = []
    for v in values:
        if clusters and v - clusters[-1][0] <= col_tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    members: list[list[tuple]] = [[] for _ in clusters]
    for bi, frs in enumerate(band_frags):
        for f in frs:
            for ci, cl in enumerate(clusters):
                if abs(f[3][0] - cl[0]) <= col_tol:
                    members[ci].append((bi, f))
                    break
    # 列锚**与对齐方向无关**：同一列可能左对齐（标签列）也可能右对齐（数字列）。
    # 右对齐的数字列里，各行数字位数不同，**左边界**就会相差几个字符宽（实测
    # 192.3 / 187.8），只按左边界聚类会把它拆成两个各自只出现在 1 个行带里的簇；
    # 两个簇都不满足"至少 2 个行带"，于是整列凭空消失，两个片段随后落到同一个
    # `(band, col)` 上、以 `fragment_consumed_twice` 被静默吞掉。这里把**同一条
    # 真实列**的两半并回来，三条同时成立才并：
    #   (a) 两个簇的行带集合不相交——它们从不在同一行上同时出现，因而不是同一行
    #       里两个相邻的列；
    #   (b) 两个簇各自的**右边界**都已经收敛（簇内右边界极差在容差内，即该列是
    #       右对齐列），且两簇右边界代表值相差在容差内（右对齐列的真实锚）；
    #   (c) 左边界之差不超过**较窄簇**内最大片段的宽度——漂移必须小于一格本身的
    #       宽度，否则一个横跨全表的表头片段会把首列与末列并成同一簇。
    col_bands: list[set[int]] = [{b for b, _f in m} for m in members]
    parent = list(range(len(clusters)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _spread_right(ci: int) -> float:
        rs = [f[3][2] for _b, f in members[ci]]
        return (max(rs) - min(rs)) if rs else float("inf")

    def _right_rep(ci: int) -> float:
        rs = sorted(f[3][2] for _b, f in members[ci])
        return rs[len(rs) // 2] if rs else float("inf")

    def _max_width(ci: int) -> float:
        return max((f[3][2] - f[3][0] for _b, f in members[ci]), default=0.0)

    for i in range(len(clusters)):
        if not members[i]:
            continue
        for j in range(i + 1, len(clusters)):
            if not members[j] or col_bands[i] & col_bands[j]:
                continue
            if _spread_right(i) > col_tol or _spread_right(j) > col_tol:
                continue
            if abs(_right_rep(i) - _right_rep(j)) > col_tol:
                continue
            if abs(clusters[i][0] - clusters[j][0]) > min(_max_width(i),
                                                          _max_width(j)):
                continue
            pi, pj = _find(i), _find(j)
            if pi != pj:
                parent[pj] = pi
    groups: dict[int, list[int]] = {}
    for ci in range(len(clusters)):
        groups.setdefault(_find(ci), []).append(ci)
    ordered = sorted(groups.values(),
                     key=lambda g: min(clusters[c][0] for c in g))
    kept = [i for i, g in enumerate(ordered)
            if len(set().union(*(col_bands[c] for c in g))) >= 2]
    if len(kept) < 2:
        problems.append("column_band_count_below_min")
        return [], problems
    edges = [min(clusters[c][0] for c in ordered[i]) for i in kept] \
        + [quantize(float(cb[2]))]
    # 列边界必须在**真实片段间隙**里被共同见证：对每一条内部列边界，至少要有一个
    # 行带在它**两侧都各有片段**（即这一行上确实存在两段被空白分开的文字，边界落在
    # 那段空白里）。若某条边界在所有行带里都只有一侧有片段，那这两列从未同时出现过，
    # 它们不是同一张表的列，而是两处互不相干的 x 位置——按位置相邻硬切出来的"列"不算
    # 结构证据（否则一段折行正文也能被切成多列表）。
    def _witness_bands(k: int, skip_band: int | None = None) -> int:
        """有多少个**行带**在这条内部列边界两侧都各有片段（即这一行上确实存在
        两段被空白分开的文字，边界落在那段空白里）。

        `skip_band` 用于问"除去某个行带之后还剩几个见证"——判断一个横跨边界的长
        片段能否算跨列合并 cell 时需要它。
        """
        edge = edges[k]
        n = 0
        for bi, frs in enumerate(band_frags):
            if bi == skip_band:
                continue
            left = any(f[3][2] <= edge for f in frs)
            right = any(f[3][0] >= edge for f in frs)
            if left and right:
                n += 1
        return n

    # 同一条内部列边界必须在**多个**行带上被共同见证（§三.3/§三.4）：只有一行偶然
    # 断开不足以证明"这里是列分界"，否则一段折行正文也能被切成多列表。
    min_witness = min(2, len(band_frags))
    for k in range(1, len(kept)):
        if _witness_bands(k) < min_witness:
            problems.append("column_boundary_not_co_witnessed")
            return [], problems
    # 一个片段**横跨**内部列边界（表头 / 全宽标题行）时，"它确实是一格跨列合并"
    # 这件事不能由它自己那一行来证明（§三.1/§三.2）：那条边界必须在该行带**之外**
    # 仍有独立见证——即别的行上真切地存在两段被空白分开的文字。否则这条边界就是
    # 在被切开的那一行上自证的，只能如实记问题码，不得把它降级成跨列合并 cell。
    self_witness_min = min(2, max(0, len(band_frags) - 1))
    cells: list[GridCellPlan] = []
    seen_pos: set[tuple[int, int]] = set()
    for bi, frs in enumerate(band_frags):
        for (ln, a, b, fb) in frs:
            cols = [k for k in range(len(kept))
                    if fb[2] > edges[k] and fb[0] < edges[k + 1]]
            if not cols:
                problems.append("fragment_not_column_aligned")
                continue
            if len(cols) > 1 and any(
                    _witness_bands(k, skip_band=bi) < self_witness_min
                    for k in range(cols[0] + 1, cols[-1] + 1)):
                problems.append("column_boundary_cuts_fragment")
                continue
            pos = (bi, cols[0])
            if pos in seen_pos:
                problems.append("fragment_consumed_twice")
                continue
            seen_pos.add(pos)
            cells.append(GridCellPlan(
                row=bi, column=cols[0], rowspan=1,
                colspan=cols[-1] - cols[0] + 1, bbox=fb,
                fragment_keys=((int(ln.line_index), a, b),)))
    if not cells:
        problems.append("no_source_fragments")
        return [], problems
    col_count = len(kept)
    covered = {(c.row, c.column + dc) for c in cells
               for dc in range(c.colspan)}
    if any((r, cc) not in covered
           for r in range(len(bands)) for cc in range(col_count)):
        problems.append("unexplained_hole")
    return sorted(cells, key=lambda x: (x.row, x.column)), problems


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeometryUnresolvedPage:
    """整页级不可裁决（页尺寸/旋转无法唯一对齐）。"""

    page_number: int
    reason: str
    detail: str

    def __post_init__(self) -> None:
        t = "GeometryUnresolvedPage"
        _need_int({"v": self.page_number}, "v", t, lo=1)
        if self.reason not in UNRESOLVED_GEOMETRY_REASONS:
            _err(t, f"reason 必须属于 {UNRESOLVED_GEOMETRY_REASONS}，"
                    f"得到 {self.reason!r}")
        if not isinstance(self.detail, str) or self.detail == "":
            _err(t, "detail 必须为非空字符串")

    def to_dict(self) -> dict:
        return {"schema_type": "GeometryUnresolvedPage",
                "page_number": self.page_number, "reason": self.reason,
                "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Any) -> "GeometryUnresolvedPage":
        t = "GeometryUnresolvedPage"
        d = _reject_unknown(d, {"schema_type", "page_number", "reason", "detail"},
                            t)
        _need_enum(d, "schema_type", t, ("GeometryUnresolvedPage",))
        return cls(page_number=_need_int(d, "page_number", t, lo=1),
                   reason=_need_enum(d, "reason", t, UNRESOLVED_GEOMETRY_REASONS),
                   detail=_need_str(d, "detail", t))


@dataclass(frozen=True)
class TableGeometryReport:
    """一次几何抽取的**诊断**结果。

    它绑定：源 PDF 字节身份、版式身份、settings 指纹、引擎版本与全部候选。
    它不是权威对象，也不进入任何身份 DAG 的"证明"位置——它只被 builder 消费，
    并把 settings/版本指纹带进 `TableObjectV4` 的上游依赖束。
    """

    source_file_sha256: str
    page_layout_id: str
    settings: TableGeometrySettings
    frames: tuple[PageGeometryFrame, ...]
    candidates: tuple[GeometryCandidate, ...]
    unresolved_pages: tuple[GeometryUnresolvedPage, ...]

    def __post_init__(self) -> None:
        t = "TableGeometryReport"
        v = self.source_file_sha256
        if not isinstance(v, str) or len(v) != 64:
            _err(t, "source_file_sha256 必须为 sha256")
        if not isinstance(self.page_layout_id, str) or self.page_layout_id == "":
            _err(t, "page_layout_id 必须为非空字符串")
        if not isinstance(self.settings, TableGeometrySettings):
            _err(t, "settings 必须为 TableGeometrySettings")
        for name in ("frames", "candidates", "unresolved_pages"):
            if not isinstance(getattr(self, name), tuple):
                _err(t, f"{name} 必须为元组")
        seen: set[int] = set()
        for fr in self.frames:
            if not isinstance(fr, PageGeometryFrame):
                _err(t, "frames 成员必须为 PageGeometryFrame")
            if fr.page_number in seen:
                _err(t, f"frames 页号重复：{fr.page_number}")
            seen.add(fr.page_number)
        pages = {fr.page_number for fr in self.frames}
        for cand in self.candidates:
            if not isinstance(cand, GeometryCandidate):
                _err(t, "candidates 成员必须为 GeometryCandidate")
            if cand.page_number not in pages:
                _err(t, f"候选页 {cand.page_number} 没有对应的坐标变换")
            frame = next(f for f in self.frames if f.page_number == cand.page_number)
            if cand.frame_fingerprint != frame.frame_fingerprint:
                _err(t, "候选的 frame_fingerprint 与同页变换不一致")
        bad = sorted(set(pages) & {u.page_number for u in self.unresolved_pages})
        if bad:
            _err(t, f"同一页不得既有变换又不可裁决：{bad}")
        for u in self.unresolved_pages:
            if not isinstance(u, GeometryUnresolvedPage):
                _err(t, "unresolved_pages 成员必须为 GeometryUnresolvedPage")

    def pages_without_candidates(self) -> tuple[int, ...]:
        """已对齐但**没有任何候选**的页（诚实缺口，不是失败）。"""
        used = {c.page_number for c in self.candidates}
        return tuple(sorted(f.page_number for f in self.frames
                            if f.page_number not in used))

    def all_pages(self) -> tuple[int, ...]:
        return tuple(sorted({f.page_number for f in self.frames}
                            | {u.page_number for u in self.unresolved_pages}))

    def report_fingerprint(self) -> str:
        return sha256_canonical({
            "source_file_sha256": self.source_file_sha256,
            "page_layout_id": self.page_layout_id,
            "settings_fingerprint": self.settings.settings_fingerprint,
            "frames": [f.to_dict() for f in self.frames],
            "candidates": [c.to_dict() for c in self.candidates],
            "unresolved_pages": [u.to_dict() for u in self.unresolved_pages],
        })

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableGeometryReport",
            "source_file_sha256": self.source_file_sha256,
            "page_layout_id": self.page_layout_id,
            "settings": self.settings.to_dict(),
            "frames": [f.to_dict() for f in self.frames],
            "candidates": [c.to_dict() for c in self.candidates],
            "unresolved_pages": [u.to_dict() for u in self.unresolved_pages],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableGeometryReport":
        t = "TableGeometryReport"
        d = _reject_unknown(d, {
            "schema_type", "source_file_sha256", "page_layout_id", "settings",
            "frames", "candidates", "unresolved_pages"}, t)
        _need_enum(d, "schema_type", t, ("TableGeometryReport",))
        if not isinstance(d.get("settings"), dict):
            _err(t, "settings 必须为对象")
        return cls(
            source_file_sha256=_need_str(d, "source_file_sha256", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            settings=TableGeometrySettings.from_dict(d["settings"]),
            frames=_need_children(d, "frames", t, PageGeometryFrame.from_dict),
            candidates=_need_children(d, "candidates", t,
                                      GeometryCandidate.from_dict),
            unresolved_pages=_need_children(d, "unresolved_pages", t,
                                            GeometryUnresolvedPage.from_dict),
        )


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------

def _page_source_fragment_count(layout_page: Any, bbox: Sequence[float]) -> int:
    """候选 bbox 内**真实** PageLayout span 数（正式文本来源，不是 pdfplumber 文本）。"""
    count = 0
    for ln in layout_page.lines:
        if _bbox_overlap(ln.bbox, bbox) <= 0.0:
            continue
        for sp in ln.spans:
            if _bbox_overlap(sp.bbox, bbox) > 0.0:
                count += 1
    return count


def _grid_cells_from_grid(grid: Sequence[Sequence[Any]]) -> \
        tuple[tuple[int, int, int, int, tuple[float, float, float, float]], ...] | None:
    """由 pdfplumber 的行×列 cell 网格推出**带真实 bbox 的**网格骨架。

    pdfplumber 把合并单元格的**延续位**记为 `None`，因此锚点 = 非 None 且其右侧/
    下方的 None 连成一片。锚点 cell 自己的 bbox 就是它的**物理范围**：这就是
    "合并单元格有明确 rowspan/colspan"的机械证据，也是 rowspan 能被真实几何
    复核（而不是"空位一律当成合并"）的原因。

    **要么精确切分，要么没有骨架**：`colspan` / `rowspan` 的贪心延伸都**不得**越过
    已被别的锚点占用的位置（否则同一位置会被两个锚点同时主张——真实 PDF 的合并
    单元格经常让行向与列向的延伸互相冲突）。切分完成后逐位置核对"恰好被主张一次"；
    只要有一个位置不是恰好一次（重叠或空洞），本函数返回 `None`，调用方据此**丢弃
    骨架**并记一条 `no_grid_structure` 缺口。宁可诚实地说"这个候选没有可复核的
    网格"，也不产出一个内部自相矛盾的网格。
    """
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if rows == 0 or cols == 0:
        return None
    claims: list[list[int]] = [[0] * cols for _ in range(rows)]
    out: list[tuple[int, int, int, int, tuple[float, float, float, float]]] = []
    for r in range(rows):
        for c in range(cols):
            if c >= len(grid[r]) or grid[r][c] is None or claims[r][c] != 0:
                continue
            box = grid[r][c]
            if not isinstance(box, (tuple, list)) or len(box) != 4:
                return None
            colspan = 1
            while (c + colspan < len(grid[r])
                   and grid[r][c + colspan] is None
                   and claims[r][c + colspan] == 0):
                colspan += 1
            rowspan = 1
            while r + rowspan < rows:
                row = grid[r + rowspan]
                if len(row) < c + colspan:
                    break
                if any(row[j] is not None for j in range(c, c + colspan)):
                    break
                if any(claims[r + rowspan][j] for j in range(c, c + colspan)):
                    break
                rowspan += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    claims[r + dr][c + dc] += 1
            out.append((r, c, rowspan, colspan,
                        tuple(quantize(float(v)) for v in box)))
    for r in range(rows):
        for c in range(cols):
            if claims[r][c] != 1:
                return None
    return tuple(sorted(out, key=lambda x: (x[0], x[1])))


def _rule_stroke_lines(pdf_page: Any) -> tuple[list, list]:
    """挑出页面上**规线候选**描边，原样返回（含 `object_type`）。

    判据只有一条几何量纲：一条描边**有一维 <= `RULE_STROKE_MAX_THICKNESS_PT`、
    另一维 > 该上界**，它才是规线的一段；宽高都大的矩形（单元格框、文字框、内缩
    合并框、图片框）两个维度都远大于上界，因此它们的边**不会**成为行列边界。逐段
    原样保留（不合并成贯穿线），所以"某处缺一段内线"的合并格依旧表达为缺线。

    返回 `(竖直描边, 水平描边)`，各自按 (主坐标, 次坐标) 升序排列以固定顺序；两者
    都只由**本页真实对象**决定，与文档、公司、语言、页码、表号无关。
    """
    bound = RULE_STROKE_MAX_THICKNESS_PT
    vert: list = []
    horz: list = []
    for kind in ("rects", "lines", "curves"):
        for obj in (getattr(pdf_page, kind, None) or []):
            try:
                x0, x1, top, bottom = (obj["x0"], obj["x1"], obj["top"],
                                       obj["bottom"])
            except (TypeError, KeyError):
                continue
            if abs(x1 - x0) <= bound and abs(bottom - top) > bound:
                vert.append(obj)
            elif abs(bottom - top) <= bound and abs(x1 - x0) > bound:
                horz.append(obj)
    vert.sort(key=lambda o: (o["x0"], o["top"], o["bottom"]))
    horz.sort(key=lambda o: (o["top"], o["x0"], o["x1"]))
    return vert, horz


def _ruled_lattice_settings(pdf_page: Any,
                            settings_obj: TableGeometrySettings) -> dict | None:
    """把**规线**当显式行列边界（`lines` 通道的第二路检测）。

    除 `vertical_strategy` / `horizontal_strategy` 换成 `explicit` 并给出本页规线外，
    其余固定参数（`snap_*` / `join_*` / `edge_min_length` / `intersection_*`）与
    `lines` 通道**完全相同**：本路不是另一套规则，只是把"哪些描边是边界"从"全部
    描边"收紧成"细长描边"。任一向无规线时返回 `None`（不做这一路，绝不靠模板或
    文字位置补出行列边界）。
    """
    vert, horz = _rule_stroke_lines(pdf_page)
    if not vert or not horz:
        return None
    ts = dict(settings_obj.table_settings)
    ts["vertical_strategy"] = "explicit"
    ts["horizontal_strategy"] = "explicit"
    ts["explicit_vertical_lines"] = vert
    ts["explicit_horizontal_lines"] = horz
    return ts


def _candidate_from_pdfplumber_table(table: Any, *, source: str, strategy: str,
                                     frame: PageGeometryFrame,
                                     layout_page: Any, page: Any) -> \
        GeometryCandidate | None:
    raw_bbox = table.bbox
    try:
        bbox = frame.pdf_bbox_to_layout(raw_bbox)
    except SchemaValidationError:
        # 表格越过变换后的页面边界：诚实丢弃，不夹取。
        return None
    # 行列网格**只能**从 `table.rows` 取：`Table.cells` 是一维 bbox 列表，
    # 不是二维网格；而 `Row.cells` 按全部列 x0 对齐，合并位的延续处为 None。
    # 这正是 rowspan/colspan 的机械证据来源。
    table_rows = getattr(table, "rows", None) or []
    grid = [list(row.cells) for row in table_rows]
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if rows == 0 or cols == 0:
        return None
    cell_count = sum(1 for r in grid for c in r if c is not None)
    raw_cells = _grid_cells_from_grid(grid)
    # 网格骨架的 bbox 必须与候选本体走**同一个**坐标变换：任何越界单元都诚实丢弃
    # 整个骨架（保留候选，但不保留一个无法复核的网格）。切分不精确（`None`）时
    # 同样丢弃骨架，并显式记一条 `no_grid_structure` 原因。
    skeleton_dropped = raw_cells is None
    grid_cells: list[tuple[int, int, int, int,
                           tuple[float, float, float, float]]] = []
    if raw_cells is not None:
        for (r, c, rs, cs, box) in raw_cells:
            try:
                mapped = tuple(frame.pdf_bbox_to_layout(box))
            except SchemaValidationError:
                grid_cells = []
                skeleton_dropped = True
                break
            grid_cells.append((r, c, rs, cs, mapped))
    spans = tuple(sorted((r, c, rs, cs) for (r, c, rs, cs, _b) in grid_cells
                         if rs > 1 or cs > 1))
    edges = getattr(page, "edges", []) or []
    edge_count = 0
    for e in edges:
        eb = (e["x0"], e["top"], e["x1"], e["bottom"])
        if _bbox_overlap(eb, raw_bbox) > 0.0:
            edge_count += 1
    intersections = getattr(page, "intersections", None)
    intersection_count = len(intersections) if intersections is not None else 0
    # 闭合性：线策略下"网格满格（无 None 洞）+ 有真实边"即视为闭合；
    # 文本策略**永不**产生闭合网格（它的内线是排版推断，不是真实边）。
    holes = sum(1 for r in grid for c in r if c is None)
    closed = bool(strategy == "lines" and holes == 0 and edge_count > 0
                  and not skeleton_dropped)
    gap_count = 0 if closed else (holes + (0 if edge_count > 0 else 1))
    if skeleton_dropped:
        gap_count += 1
    if strategy == "lines" and not closed and gap_count == 0:
        gap_count = 1
    unresolved_reason = "no_grid_structure" if skeleton_dropped else None
    return GeometryCandidate(
        candidate_source=source,
        strategy=strategy,
        page_number=int(layout_page.page_number),
        bbox=tuple(bbox),
        frame_fingerprint=frame.frame_fingerprint,
        row_count=rows,
        column_count=cols,
        cell_count=cell_count,
        merged_cells=spans,
        grid_cells=tuple(grid_cells),
        closed_grid=closed,
        structural_gap_count=gap_count,
        edge_count=edge_count,
        intersection_count=intersection_count,
        source_fragment_count=_page_source_fragment_count(layout_page, bbox),
        unresolved_reason=unresolved_reason,
    )


def extract_table_geometry(layout_capability: Any, *, pages: Sequence[int] | None
                           = None) -> TableGeometryReport:
    """从 `VerifiedPageLayout` 的**同源字节**抽取几何候选（§19.5.1/§19.5.2）。

    只接受已签发的 `VerifiedPageLayout`：字节来自签发时保留的那一份。任何
    `PageLayout` 直传、JSON 直传或自报字段旁路都在这里被拒绝。
    """
    if layout_capability is None or not hasattr(layout_capability, "source_bytes"):
        raise TableGeometryError(
            "extract_table_geometry 只接受已签发的 VerifiedPageLayout（含 source_bytes）")
    source_bytes = layout_capability.source_bytes
    if not isinstance(source_bytes, (bytes, bytearray)) or not source_bytes:
        raise TableGeometryError("VerifiedPageLayout.source_bytes 必须为非空字节")
    layout = layout_capability.layout
    settings = geometry_settings("lines")
    text_settings = geometry_settings("text")
    wanted = None if pages is None else {int(p) for p in pages}
    frames: list[PageGeometryFrame] = []
    candidates: list[GeometryCandidate] = []
    unresolved: list[GeometryUnresolvedPage] = []
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise TableGeometryError(f"pdfplumber 不可用：{e}") from e
    with pdfplumber.open(io.BytesIO(bytes(source_bytes))) as pdf:
        if len(pdf.pages) != layout.page_count:
            raise TableGeometryError(
                f"PDF 页数 {len(pdf.pages)} 与 PageLayout.page_count "
                f"{layout.page_count} 不一致（fail-closed）")
        for layout_page in layout.pages:
            if wanted is not None and layout_page.page_number not in wanted:
                continue
            pdf_page = pdf.pages[layout_page.page_number - 1]
            frame = align_page_frame(layout_page=layout_page, pdf_page=pdf_page)
            if frame is None:
                unresolved.append(GeometryUnresolvedPage(
                    page_number=int(layout_page.page_number),
                    reason=GEOMETRY_UNRESOLVED_PAGE_REASON,
                    detail=(f"pdfplumber {quantize(float(pdf_page.width))}x"
                            f"{quantize(float(pdf_page.height))} rot="
                            f"{int(pdf_page.rotation or 0)} vs layout "
                            f"{layout_page.width}x{layout_page.height} rot="
                            f"{layout_page.rotation}")))
                continue
            found: list[GeometryCandidate] = []
            for strategy, settings_obj in (("lines", settings), ("text", text_settings)):
                source = ("pdfplumber_lines" if strategy == "lines"
                          else "pdfplumber_text")
                # `lines` 通道跑两路：① 原样（页面**全部**描边为边界）；② 规线格架
                # （只把**细长描边**当边界，见 `RULE_STROKE_MAX_THICKNESS_PT`）。
                # ② 只是同一通道内"哪些描边算边界"的收紧，不是新通道：两路产出的
                # 候选都走同一个 `source`、同一道硬门、同一套去重与身份。
                passes = [settings_obj.table_settings]
                if strategy == "lines":
                    lattice = _ruled_lattice_settings(pdf_page, settings_obj)
                    if lattice is not None:
                        passes.append(lattice)
                for table_settings in passes:
                    try:
                        tables = pdf_page.find_tables(
                            table_settings=table_settings)
                    except Exception:
                        # pdfplumber 内部失败 ⇒ 该路诚实记为"无候选"。
                        # 不得退回另一套自研规则来"补上"它。
                        tables = []
                    for table in tables:
                        cand = _candidate_from_pdfplumber_table(
                            table, source=source, strategy=strategy, frame=frame,
                            layout_page=layout_page, page=pdf_page)
                        if cand is not None:
                            found.append(cand)
            cluster = cluster_borderless_rows(layout_page)
            if cluster is not None:
                cbbox, crow, ccol = cluster
                found.append(GeometryCandidate(
                    candidate_source="layout_borderless_cluster",
                    strategy="lines",
                    page_number=int(layout_page.page_number),
                    bbox=tuple(cbbox),
                    frame_fingerprint=frame.frame_fingerprint,
                    row_count=crow,
                    column_count=ccol,
                    cell_count=crow * ccol,
                    merged_cells=(),
                    grid_cells=(),
                    closed_grid=False,
                    structural_gap_count=1,
                    edge_count=0,
                    intersection_count=0,
                    source_fragment_count=_page_source_fragment_count(
                        layout_page, cbbox),
                    unresolved_reason=None,
                ))
            frames.append(frame)
            candidates.extend(_dedup_candidates(found))
    return TableGeometryReport(
        source_file_sha256=layout.source_file_sha256,
        page_layout_id=layout.page_layout_id,
        settings=settings,
        frames=tuple(frames),
        candidates=tuple(sorted(candidates,
                                key=lambda c: (c.page_number, c.bbox,
                                               c.candidate_source))),
        unresolved_pages=tuple(unresolved),
    )


def _dedup_candidates(candidates: Sequence[GeometryCandidate]) \
        -> list[GeometryCandidate]:
    """同页**同 bbox** 的候选去重（§19.5.1"生成并去重"）。

    同一矩形被两条通道同时发现，是"同一处被检查了两遍"，不是两个互相竞争的候选；
    把它当竞争会人为制造"并列 ⇒ 整页 unresolved"，从而把真实表格判成不可裁决。
    去重保留证据最强的那个：闭合网格 > 网格信息量更大 > 候选通道优先级更高。
    """
    best: dict[tuple, GeometryCandidate] = {}
    for cand in candidates:
        key = (cand.page_number, cand.bbox)
        prev = best.get(key)
        if prev is None or _dedup_rank(cand) < _dedup_rank(prev):
            best[key] = cand
    return list(best.values())


def _dedup_rank(candidate: GeometryCandidate) -> tuple:
    return (0 if candidate.closed_grid else 1,
            -(candidate.row_count * candidate.column_count),
            -candidate.source_fragment_count,
            CANDIDATE_SOURCE_PRIORITY.index(candidate.candidate_source))


# ---------------------------------------------------------------------------
# 映射核验
# ---------------------------------------------------------------------------

def verify_geometry_mapping(report: TableGeometryReport, *, page_number: int,
                            bbox: Sequence[float]) -> dict:
    """核验一个 bbox 能否**唯一**映射回当前 PageLayout 的同页几何。

    返回的字典**只**包含可复核事实：同页变换、量化后的 bbox、真实 source fragment
    数、是否越界、以及最终判决。它**不**判定"这是不是一张表"。
    """
    if not isinstance(report, TableGeometryReport):
        raise TableGeometryError("report 必须为 TableGeometryReport")
    page = int(page_number)
    frame = next((f for f in report.frames if f.page_number == page), None)
    if frame is None:
        reason = next((u.reason for u in report.unresolved_pages
                       if u.page_number == page), "page_frame_not_uniquely_aligned")
        return {"page_number": page, "mapped": False, "reason": reason,
                "frame_fingerprint": None, "bbox": None,
                "within_page": False, "source_fragment_count": 0}
    if len(bbox) != 4:
        raise TableGeometryError("bbox 必须为长度 4 的序列")
    lo = (quantize(float(bbox[0])), quantize(float(bbox[1])))
    hi = (quantize(float(bbox[2])), quantize(float(bbox[3])))
    within = (lo[0] >= -frame.tolerance and lo[1] >= -frame.tolerance
              and hi[0] <= frame.layout_width + frame.tolerance
              and hi[1] <= frame.layout_height + frame.tolerance
              and hi[0] > lo[0] and hi[1] > lo[1])
    # 注意：本函数**不**返回 source fragment 数。片段计数必须以真实 PageLayout
    # 为准，而 `TableGeometryReport` 不携带版式行，因此这里不提供该字段比"给一个
    # 看起来像但实际没算过的数"更诚实 —— 计数由持有版式的 builder 完成。
    return {
        "page_number": page,
        "mapped": bool(within),
        "reason": None if within else "bbox_outside_page_frame",
        "frame_fingerprint": frame.frame_fingerprint,
        "bbox": (lo[0], lo[1], hi[0], hi[1]),
        "within_page": bool(within),
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """机械自检：settings 闭合、版本闭合、真值表可判、无公司/页码硬编码。"""
    problems: list[str] = []
    try:
        versions = pdfplumber_engine_versions()
    except TableGeometryError as e:
        versions = {}
        problems.append(f"引擎不可用：{e}")
    for strategy in GEOMETRY_STRATEGIES:
        try:
            s = geometry_settings(strategy)
        except SchemaValidationError as e:
            problems.append(f"strategy {strategy} settings 不合法：{e}")
            continue
        if s.strategy != strategy:
            problems.append(f"strategy 往返不一致：{s.strategy!r} != {strategy!r}")
        rt = TableGeometrySettings.from_dict(s.to_dict())
        if rt.to_dict() != s.to_dict():
            problems.append(f"strategy {strategy} settings 往返不等")
        if rt.settings_fingerprint != s.settings_fingerprint:
            problems.append(f"strategy {strategy} settings 指纹往返不等")
    # 大容差必须被拒绝。
    try:
        TableGeometrySettings(
            geometry_version=V.TABLE_GEOMETRY_VERSION, strategy="lines",
            tolerance=TABLE_GEOMETRY_DEFAULT_TOLERANCE * 8,
            table_settings=RULED_TABLE_SETTINGS,
            rule_stroke_max_thickness=RULE_STROKE_MAX_THICKNESS_PT,
            borderless_params=BORDERLESS_CLUSTER_PARAMS,
            engine_versions=versions, settings_fingerprint="0" * 64)
        problems.append("放大容差的 settings 未被拒绝")
    except SchemaValidationError:
        pass
    # candidate 排序不得依赖输入顺序。
    def _cand(**kw):
        base = {"candidate_source": "pdfplumber_lines", "strategy": "lines",
                "page_number": 1, "bbox": (0.0, 0.0, 10.0, 10.0),
                "frame_fingerprint": "f" * 8, "row_count": 2,
                "column_count": 2, "cell_count": 4, "merged_cells": (),
                "grid_cells": (),
                "closed_grid": True, "structural_gap_count": 0, "edge_count": 4,
                "intersection_count": 4, "source_fragment_count": 1,
                "unresolved_reason": None}
        base.update(kw)
        return GeometryCandidate(**base)

    a = _cand(bbox=(0.0, 0.0, 10.0, 10.0))
    b = _cand(bbox=(0.0, 0.0, 20.0, 20.0), closed_grid=False,
              structural_gap_count=2)
    fwd = [c.bbox for c in sort_candidates((a, b))]
    rev = [c.bbox for c in sort_candidates((b, a))]
    if fwd != rev:
        problems.append("sort_candidates 依赖输入顺序（必须确定性）")
    winners, unresolved = resolve_overlapping_candidates((a, b))
    if [c.bbox for c in winners] != [(0.0, 0.0, 10.0, 10.0)] or unresolved:
        problems.append("重叠裁决未按闭合/缺口/面积给出唯一胜出")
    tie_a = _cand(bbox=(0.0, 0.0, 10.0, 10.0))
    tie_b = _cand(bbox=(2.0, 2.0, 12.0, 12.0))
    w2, u2 = resolve_overlapping_candidates((tie_a, tie_b))
    if w2 or len(u2) != 2:
        problems.append("完全并列的重叠候选未整体判为 unresolved")

    # --- cell 网格重建（§19.5.1） -----------------------------------------
    class _Span:
        __slots__ = ("bbox",)

        def __init__(self, x0, y0, x1, y1):
            self.bbox = (quantize(x0), quantize(y0), quantize(x1), quantize(y1))

    class _Line:
        __slots__ = ("line_index", "bbox", "spans", "is_furniture")

        def __init__(self, idx, spans, furniture=False):
            self.line_index = idx
            self.spans = tuple(spans)
            self.bbox = (min(s.bbox[0] for s in spans),
                         min(s.bbox[1] for s in spans),
                         max(s.bbox[2] for s in spans),
                         max(s.bbox[3] for s in spans))
            self.is_furniture = furniture

    class _Page:
        __slots__ = ("page_number", "lines")

        def __init__(self, lines):
            self.page_number = 1
            self.lines = tuple(lines)

    def _row(idx, y, cells_x):
        return _Line(idx, [_Span(x0, y, x1, y + 10.0) for (x0, x1) in cells_x])

    # 2×2 骨架：每格都真有片段 => 闭合。
    skel = ((0, 0, 1, 1, (0.0, 0.0, 50.0, 20.0)),
            (0, 1, 1, 1, (50.0, 0.0, 100.0, 20.0)),
            (1, 0, 1, 1, (0.0, 20.0, 50.0, 40.0)),
            (1, 1, 1, 1, (50.0, 20.0, 100.0, 40.0)))
    skel_cand = _cand(bbox=(0.0, 0.0, 100.0, 40.0), row_count=2,
                      column_count=2, cell_count=4, grid_cells=skel)
    page = _Page([_row(0, 2.0, [(2.0, 48.0), (52.0, 98.0)]),
                  _row(1, 22.0, [(2.0, 48.0), (52.0, 98.0)])])
    plan = derive_cell_grid(page, candidate=skel_cand)
    if not plan.closed or len(plan.cells) != 4 or not plan.geometry_backed:
        problems.append(f"骨架网格未闭合：{plan.problems}")

    # 骨架里有一格没文本 => 未解释空洞（不得静默）。
    page_hole = _Page([_row(0, 2.0, [(2.0, 48.0), (52.0, 98.0)]),
                       _row(1, 22.0, [(2.0, 48.0)])])
    plan_hole = derive_cell_grid(page_hole, candidate=skel_cand)
    if plan_hole.closed or "unexplained_hole" not in plan_hole.problems:
        problems.append("骨架空位未被判为未解释空洞")

    # 无骨架路径：真实行带/列带 => 闭合，且**绝不**产生 rowspan。
    band_cand = _cand(bbox=(0.0, 0.0, 100.0, 40.0), row_count=0,
                      column_count=0, cell_count=0, closed_grid=False,
                      structural_gap_count=1)
    plan_band = derive_cell_grid(page, candidate=band_cand)
    if (not plan_band.closed or plan_band.row_count != 2
            or plan_band.column_count != 2
            or any(c.rowspan != 1 for c in plan_band.cells)):
        problems.append(f"行带/列带网格未闭合或产生了无据 rowspan："
                        f"{plan_band.problems}")

    # 无骨架路径下缺一格 => 未解释空洞（没有真实网格就不得声称纵向合并）。
    plan_band_hole = derive_cell_grid(page_hole, candidate=band_cand)
    if plan_band_hole.closed:
        problems.append("行带网格把空缺当成合并（必须 fail-closed）")

    # 越界行：如实记账且不消费。
    page_straddle = _Page([_row(0, 2.0, [(2.0, 48.0), (52.0, 98.0)]),
                           _row(1, 22.0, [(2.0, 48.0), (52.0, 98.0)]),
                           _Line(2, [_Span(-40.0, 2.0, 30.0, 12.0)])])
    plan_s = derive_cell_grid(page_straddle, candidate=skel_cand)
    if (2 not in plan_s.straddling_lines) or (2 in plan_s.assigned_lines):
        problems.append("越界行未被如实记账")

    # 跨两列的片段 => colspan=2（真实几何结论，不是猜测）。
    page_wide = _Page([_row(0, 2.0, [(2.0, 98.0)]),
                       _row(1, 22.0, [(2.0, 48.0), (52.0, 98.0)]),
                       _row(2, 42.0, [(2.0, 48.0), (52.0, 98.0)])])
    plan_w = derive_cell_grid(
        page_wide, candidate=_cand(bbox=(0.0, 0.0, 100.0, 60.0), row_count=0,
                                   column_count=0, cell_count=0,
                                   closed_grid=False, structural_gap_count=1))
    if not any(c.colspan == 2 for c in plan_w.cells):
        problems.append("跨列片段未被判为 colspan=2")
    return {
        "geometry_version": V.TABLE_GEOMETRY_VERSION,
        "strategy_count": len(GEOMETRY_STRATEGIES),
        "candidate_source_count": len(CANDIDATE_SOURCES),
        "engine_versions": versions,
        "settings_fingerprint_lines": (
            geometry_settings("lines").settings_fingerprint
            if versions else None),
        "settings_fingerprint_text": (
            geometry_settings("text").settings_fingerprint
            if versions else None),
        "problems": problems,
    }


def _main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] != "--self-check":
        print(f"未知参数: {args[0]}", file=sys.stderr)
        return 2
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report["problems"] else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))

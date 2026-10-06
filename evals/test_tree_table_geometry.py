# -*- coding: utf-8 -*-
"""TS5 G1–G7：候选通道、统一硬门、重叠裁决与普通段落负门（计划 §19.12.2）。

覆盖（全部离线：只读本地 PDF 与**只读** `data/evidence.db`；无网络、无 LLM、无写入）：

- **G1** §19.12.2-1：显式表号表 / 无表号 generic 表 / 续表三种形状**各自**都能成为候选并
  通过统一硬门；三者互换表题文本后候选与门的结论逐项相同（通道不读业务文字）；
  现场文档的冻结范围通道与"单页 `table_inside`/`table_adjacency` 范围"的 `(页, 矩形)`
  **精确等集**；
- **G2** §19.12.2-2：`detect_table_start_flags` 一类旧 harness 文本旗标在 TS5 生产模块里
  既不被导入也不被引用（AST 守卫）；文本通道只能"指出检查哪里"（`CHANNEL_LIMITATIONS`），
  形状资格完全由真实几何网格决定：携带完整几何网格的显式表号候选仍可验证，只有文本
  旗标、没有线条/交点/闭合网格的候选既不能准入也不能主张 scope miss；
- **G3** §19.12.2-3 + 普通段落负门：只有表题、普通数字段落、编号列表、双栏排版、表字
  标题、组织结构图**一律不得成表**（门级拒绝 + `build_tables` 产出零表）；
  `prose_negative_gate` 的三条阈值（400 / 120 / 60·0.5）逐条按边界钉死；
- **G4** §19.12.2-4：只有 `adjacent_to_table` 不得自动成表——位置相邻本身不给资格，
  `table_adjacency` 与同几何的 `table_inside` 走**同一道门**且结论逐项相同；现场文档
  `table_adjacency` 裁决的表归属与"是否真被某张表消费"一致；
- **G5** §19.12.2-5：无线框表只有在 row/column/content 多重证据闭合时才可恢复
  （`cluster_borderless_rows` 的 `min_rows`/`min_columns`/`min_row_span_columns` 三重下限；
  行带路径**绝不**产生无据 rowspan）；缺一格即 `unexplained_hole`，只报 unsupported；
- **G6** §19.12.2-6：两个重叠候选无法唯一裁决时整体 `unresolved`，**不选"第一个"**
  （几何裁决键与输入顺序无关，且刻意不含页码/坐标；`plan_decisions` 把
  `candidate_not_uniquely_mapped` 的范围判为 `unresolved_geometry`）；现场审计三态与
  理由码逐项自洽；
- **G7** §19.12.2-7：geometry 命中冻结 `regular` body span 时输出
  `upstream_table_scope_miss` 且**阻断**，既不静默重分类成表、也不给该 regular 范围
  任何吸收裁决；没有几何证据的同形候选不阻断（不过度阻断）；
- **G8** TS5-P1-2（§三.1/§三.3）：无骨架路径里一个片段横跨内部列边界时，这条边界必须
  在该行带**之外**仍有 ≥2 个独立见证才允许读成跨列合并 cell；只有被切开的那一行
  自证时如实记 `column_boundary_cuts_fragment` 并整张不闭合；
- **G9** §0.19（乙）`lines` 通道**第二路**：只有"一维 ≤ `RULE_STROKE_MAX_THICKNESS_PT`、
  另一维 > 该上界"的**细长描边**才被当成行列边界（单元格框／文字框／内缩合并框／图片框
  一律不是规线，因此不得凭空造出表头或行列）；任一向无规线则**放弃**这一路；除四个被
  收紧的键，其余固定参数与 `lines` 通道逐项相同；真实现场上第二路**只增不改**（关掉它
  只会让候选变少或不变），且至少一页确实因它多出候选。

**只读与性能**：`TG.extract_table_geometry` 在本次进程内按 `(layout capability, pages)`
记忆化（`_install_geometry_memo`）。它是对**同一** capability 的纯函数，被测代码读到的
仍是同一份报告，因此不削弱任何断言，只是把重复的 pdfplumber 扫描降为一次。

**合成夹具**：反例需要"表题无网格 / 双栏排版 / 组织结构图"这类现场文档里不存在的形状，
因此 G1–G7 用 `_Site` 构造**内部自洽**的合成版式与真实几何报告（`PageGeometryFrame`
由生产 `align_page_frame` 生成、真实 profile 资产、真实 `derive_cell_grid` 与统一硬门）。
合成输入只经由生产类型 `TG.TableGeometryReport` / `TB.TableBuildContext` 进入被测代码，
没有旁路入口：几何候选加入报告后由**真实** `TB.enumerate_candidates` 重新枚举，
因此断言覆盖的是生产调用路径本身，而不是被绕过的单函数。
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import time
from pathlib import Path

from evidence import store as _estore

from document_structure import aligner as AL
from document_structure import final_material_builder as FMB
from document_structure import layout_builder as LB
from document_structure import span_builder as SB
from document_structure import span_verifier as SV
from document_structure import table_builder as TB
from document_structure import table_classification as TC
from document_structure import table_geometry as TG
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.evidence_gateway import bind_current_evidence_authority
from document_structure.schema import quantize
from evals import tree_stage_env as STAGE

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
        if substr in str(e):
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{str(e)[:300]!r}")
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
# 0. 真实现场（只读）与进程内几何记忆
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_DB_PATH = _REPO / "data" / "evidence.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
_DOC_KEY = "NDSD_2024_year"

_CTX: dict = {}
_PROFILES = None
#: `_install_geometry_memo` 装上的记忆化包装**背后**的原函数。G9 需要"同一个
#: capability、同一批页、但第二路被关掉"的第二份报告，记忆化键撞车时仍要真算。
_ORIGINAL_EXTRACT = None


def _db_identity(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _install_geometry_memo():
    """把 `TG.extract_table_geometry` 换成本进程内的记忆化包装；返回原函数。"""
    global _ORIGINAL_EXTRACT
    original = TG.extract_table_geometry
    _ORIGINAL_EXTRACT = original
    memo: dict = {}

    def _memo(layout_capability, *, pages=None):
        key = (id(layout_capability), None if pages is None else tuple(pages))
        if key not in memo:
            memo[key] = original(layout_capability, pages=pages)
        return memo[key]

    TG.extract_table_geometry = _memo
    return original


def _profiles():
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = TC.load_table_profile_bundle()
    return _PROFILES


def _live() -> dict:
    """跑一次真实链路到 TS5 构建上下文（**不**建 final 快照）并缓存。"""
    if "built" in _CTX or "error" in _CTX:
        return _CTX
    try:
        doc = json.loads(_TRUST.read_text(encoding="utf-8"))["documents"][_DOC_KEY]
        ident = doc["expected_identity"]
        pdf = _REPO / doc["source_pdf_relpath"]
        authority = bind_current_evidence_authority()
        layout = LB.build_verified_page_layout(
            pdf, company_id=ident["company_id"],
            document_id=ident["document_id"])
        alignment = AL.align_evidence_set_verified(layout, authority)
        handoff = SB.issue_live_ts3_handoff(layout, alignment, authority)
        snap = SB.build_span_snapshot(handoff, stage=STAGE.current_stage())
        verified = SV.verify_span_snapshot(snap, handoff)
        root = FMB._read_roots(verified)
        ctx = FMB._build_context(root)
        cands = TB.enumerate_candidates(ctx)
        gates = tuple((c, TB.evaluate_candidate_gate(ctx, c)) for c in cands)
        built = TB.build_tables(ctx)
        _CTX.update({"verified": verified, "root": root, "ctx": ctx,
                     "cands": cands, "gates": gates, "built": built,
                     "report": ctx.geometry, "handoff": handoff})
    except Exception as error:  # noqa: BLE001
        _CTX["error"] = f"{type(error).__name__}: {error}"
    return _CTX


def _gate(ctx, msg="真实链路必须成立"):
    if ctx.get("error"):
        check(False, f"{msg} —— 现场链路失败：{ctx['error']}")
        return False
    if "built" not in ctx:
        check(False, f"{msg} —— 现场链路未产出构建结果")
        return False
    return True


# ---------------------------------------------------------------------------
# 1. 合成版式夹具（内部自洽；只读，不接触仓库资产）
# ---------------------------------------------------------------------------

_PAGE_W = 600.0
_PAGE_H = 800.0
_GRID_XS = ((20.0, 50.0), (60.0, 90.0))
_GRID_TOPS = (20.0, 33.0, 46.0, 59.0)
_ROW_H = 10.0


class _Span:
    __slots__ = ("bbox", "text", "char_start", "char_end", "size", "is_bold")

    def __init__(self, x0, y0, x1, y1, text="f", size=10.0, bold=False):
        self.bbox = (quantize(x0), quantize(y0), quantize(x1), quantize(y1))
        self.text = text
        self.char_start = 0
        self.char_end = len(text)
        self.size = float(size)
        self.is_bold = bool(bold)


class _Line:
    __slots__ = ("line_index", "spans", "bbox", "is_furniture", "text")

    def __init__(self, idx, spans, furniture=False):
        spans = tuple(spans)
        if not spans:
            raise ValueError("合成行必须有片段")
        self.line_index = int(idx)
        self.spans = spans
        self.bbox = (min(s.bbox[0] for s in spans),
                     min(s.bbox[1] for s in spans),
                     max(s.bbox[2] for s in spans),
                     max(s.bbox[3] for s in spans))
        self.is_furniture = bool(furniture)
        self.text = "".join(s.text for s in spans)


class _Page:
    __slots__ = ("page_number", "lines", "width", "height", "rotation")

    def __init__(self, page_number, lines):
        self.page_number = int(page_number)
        self.lines = tuple(lines)
        self.width = _PAGE_W
        self.height = _PAGE_H
        self.rotation = 0


class _PdfPage:
    """与合成 `_Page` 尺寸/旋转一致的 pdfplumber 侧视图（供 `align_page_frame`）。"""

    __slots__ = ("width", "height", "rotation", "cropbox", "mediabox")

    def __init__(self, page):
        self.width = float(page.width)
        self.height = float(page.height)
        self.rotation = int(page.rotation)
        self.cropbox = (0.0, 0.0, float(page.width), float(page.height))
        self.mediabox = tuple(self.cropbox)


class _Layout:
    __slots__ = ("pages", "page_count", "page_layout_id", "document_version",
                 "document_id", "source_file_sha256")

    def __init__(self, pages):
        self.pages = tuple(pages)
        self.page_count = len(self.pages)
        self.page_layout_id = "pl-syn-1"
        self.document_version = "dv-syn-1"
        self.document_id = "doc-syn-1"
        self.source_file_sha256 = "a" * 64


class _Node:
    __slots__ = ("node_id", "structural_path")

    def __init__(self, node_id, structural_path=("正文",)):
        self.node_id = node_id
        self.structural_path = tuple(structural_path)


class _Outline:
    __slots__ = ("outline_locator", "outline_id", "page_layout_id", "nodes",
                 "document_id", "document_version")

    def __init__(self, page_layout_id, nodes=()):
        self.outline_locator = "ol-syn-1"
        self.outline_id = "oi-syn-1"
        self.page_layout_id = page_layout_id
        self.nodes = tuple(nodes)
        self.document_id = "doc-syn-1"
        self.document_version = "dv-syn-1"


@dataclasses.dataclass(frozen=True)
class _Disposition:
    """只含被测代码真正读取的字段的合成冻结范围。"""

    disposition_id: str
    range_kind: str
    node_id: str | None
    start_page: int
    end_page: int
    start_line: int
    end_line: int
    disposition_locator: str = ""
    unassigned_reason: str | None = None
    table_scope: str | None = None
    table_reason: str | None = None
    line_count: int = 0
    tight_char_count: int = 0
    span_id: str | None = None

    def __post_init__(self):
        if not self.disposition_locator:
            object.__setattr__(self, "disposition_locator",
                               "loc-" + self.disposition_id)


def _union(lines) -> tuple:
    """独立手算的真实物理矩形（**不**调用被测的 `frozen_range_bbox`）。"""
    spans = [s for ln in lines for s in ln.spans]
    return (min(s.bbox[0] for s in spans), min(s.bbox[1] for s in spans),
            max(s.bbox[2] for s in spans), max(s.bbox[3] for s in spans))


def _two_col_rows(*, tops, missing=(), texts=None, index_base=0):
    """两列真实行：`(行, 列)` 一个片段；`missing` 内的位置被挖空。"""
    lines = []
    for i, top in enumerate(tops):
        spans = [_Span(x0, top, x1, top + _ROW_H,
                       text=(texts or {}).get((i, j), f"C{i}{j}"))
                 for j, (x0, x1) in enumerate(_GRID_XS) if (i, j) not in missing]
        if spans:
            lines.append(_Line(index_base + i, spans))
    return lines


def _grid_page(page_number=1, *, caption=None, tops=_GRID_TOPS, missing=(),
               texts=None):
    """两列规则行（+ 可选表题行）的合成页；行带路径可闭合为 2 列网格。"""
    lines = []
    if caption is not None:
        lines.append(_Line(0, [_Span(20.0, 2.0, 90.0, 12.0, text=caption)]))
    lines.extend(_two_col_rows(tops=tops, missing=missing, texts=texts,
                               index_base=len(lines)))
    return _Page(page_number, lines)


def _single_col_page(page_number=1, *, x0=20.0, x1=120.0, count=4, texts=None):
    """单列多行（编号列表 / 数字段落 / 只有表题的形状）：稳定列数恒为 1。"""
    lines = [_Line(i, [_Span(x0, 20.0 + i * 13.0, x1,
                             20.0 + i * 13.0 + _ROW_H,
                             text=(texts or {}).get(i, f"P{i}"))])
             for i in range(count)]
    return _Page(page_number, lines)


def _org_chart_page(page_number=1):
    """组织结构图：行带不等宽、列锚点在行带间不稳定，且存在未解释空洞。"""
    lines = [
        _Line(0, [_Span(40.0, 20.0, 60.0, 30.0)]),
        _Line(1, [_Span(20.0, 40.0, 40.0, 50.0), _Span(70.0, 40.0, 90.0, 50.0)]),
        _Line(2, [_Span(40.0, 60.0, 60.0, 70.0)]),
        _Line(3, [_Span(10.0, 80.0, 30.0, 90.0), _Span(40.0, 80.0, 60.0, 90.0),
                  _Span(70.0, 80.0, 90.0, 90.0)]),
    ]
    return _Page(page_number, lines)


def _skeleton(bbox, rows=2, cols=2):
    """把矩形均分为 rows×cols 的真实网格骨架（`grid_cells`）。"""
    x0, y0, x1, y1 = bbox
    xs = [x0 + (x1 - x0) * i / cols for i in range(cols + 1)]
    ys = [y0 + (y1 - y0) * i / rows for i in range(rows + 1)]
    return tuple((r, c, 1, 1, (quantize(xs[c]), quantize(ys[r]),
                               quantize(xs[c + 1]), quantize(ys[r + 1])))
                 for r in range(rows) for c in range(cols))


class _Site:
    """合成版式 + 与之自洽的真实几何报告（frame 由生产 `align_page_frame` 生成）。"""

    def __init__(self, pages, *, nodes=()):
        self.layout = _Layout(pages)
        self.outline = _Outline(self.layout.page_layout_id, nodes=nodes)
        self.frames = {}
        for page in self.layout.pages:
            frame = TG.align_page_frame(layout_page=page, pdf_page=_PdfPage(page))
            if frame is None:
                raise AssertionError("合成页必须能唯一对齐")
            self.frames[page.page_number] = frame

    def frame(self, page_number=1) -> TG.PageGeometryFrame:
        return self.frames[page_number]

    def cand(self, *, page_number=1, bbox, source="pdfplumber_lines",
             strategy="lines", closed=False, gaps=1, edges=0, intersections=0,
             grid_cells=(), row_count=0, column_count=0, cell_count=0,
             merged=()) -> TG.GeometryCandidate:
        return TG.GeometryCandidate(
            candidate_source=source, strategy=strategy, page_number=page_number,
            bbox=tuple(bbox),
            frame_fingerprint=self.frame(page_number).frame_fingerprint,
            row_count=row_count, column_count=column_count,
            cell_count=cell_count, merged_cells=tuple(merged),
            grid_cells=tuple(grid_cells), closed_grid=bool(closed),
            structural_gap_count=int(gaps), edge_count=int(edges),
            intersection_count=int(intersections),
            source_fragment_count=0, unresolved_reason=None)

    def context(self, *, dispositions=(), candidates=(), unresolved=()):
        report = TG.TableGeometryReport(
            source_file_sha256=self.layout.source_file_sha256,
            page_layout_id=self.layout.page_layout_id,
            settings=TG.geometry_settings("lines"),
            frames=tuple(self.frames[p.page_number]
                         for p in self.layout.pages),
            candidates=tuple(candidates), unresolved_pages=tuple(unresolved))
        return TB.TableBuildContext(
            document_id=self.layout.document_id,
            document_version=self.layout.document_version,
            evidence_set_version="esv-syn-1",
            page_layout_id=self.layout.page_layout_id,
            outline_id=self.outline.outline_id,
            outline_locator=self.outline.outline_locator,
            verified_span_snapshot_id="vss-syn-1",
            source_file_sha256=self.layout.source_file_sha256,
            page_layout=self.layout, outline=self.outline, evidence_blocks=(),
            terminals=(), components=(), dispositions=tuple(dispositions),
            spans=(), qualification_policy=None, profiles=_profiles(),
            geometry=report, upstream_dependency_fingerprint="b" * 64)


def _owning_site(page, *, range_kind="table_inside", node_id="n1",
                 candidates=()):
    """一页 + 一个覆盖整页行范围的冻结表范围（默认 `table_inside`）。"""
    site = _Site([page], nodes=[_Node("n1"), _Node("n2")])
    d = _Disposition("d-1", range_kind, node_id, page.page_number,
                     page.page_number, 0, len(page.lines) - 1)
    return site, d, site.context(dispositions=[d], candidates=candidates), \
        _union(page.lines)


# ---------------------------------------------------------------------------
# G1 §19.12.2-1：三类形状都能成为候选（含表题文本无关性）
# ---------------------------------------------------------------------------

#: 三种形状只用**表题文字**区分：显式表号 / 无表号 generic / 续表。
_SHAPES = (
    ("显式表号", "表 4-1 主要财务指标", 1),
    ("无表号 generic", "主要财务指标", 1),
    ("续表", "续表", 2),
)


def _g1_shape(label, caption, page_number):
    page = _grid_page(page_number, caption=caption)
    _site, d, ctx, bbox = _owning_site(page)
    cands = TB.enumerate_candidates(ctx)
    ok = len(cands) == 1 and cands[0].candidate_source == "frozen_range"
    check(ok, f"G1 {label}：冻结范围通道给出**一个**候选（得到 "
              f"{[(c.candidate_source, c.bbox) for c in cands]}）")
    if not ok:
        return
    cand = cands[0]
    check(tuple(cand.bbox) == bbox,
          f"G1 {label}：候选矩形等于真实 LayoutLine bbox 的并集"
          f"（{tuple(cand.bbox)} vs {bbox}）")
    check(cand.frozen_disposition_id == d.disposition_id
          and cand.frozen_disposition_locator == d.disposition_locator
          and cand.frozen_overlap_ratio == 1.0,
          f"G1 {label}：候选的唯一归属冻结范围对象级回查正确")
    gate = TB.evaluate_candidate_gate(ctx, cand)
    grid = gate.grid
    check(gate.admitted and grid is not None and grid.closed
          and grid.column_count == 2 and grid.row_count == 5
          and grid.assigned_lines == tuple(range(len(page.lines))),
          f"G1 {label}：候选过统一硬门并可重建闭合 2 列 5 行网格（得到 "
          f"admitted={gate.admitted} reason={gate.reason} "
          f"grid={None if grid is None else (grid.row_count, grid.column_count, grid.problems)}）")


def _g1_text_blindness():
    """表题文本互换后，候选与门的结论必须逐项相同（通道不读业务文字）。"""
    signatures = []
    for text in ("表 4-1 主要财务指标", "主要财务指标", "续表"):
        page = _grid_page(1, caption=text)
        _site, _d, ctx, _bbox = _owning_site(page)
        cands = TB.enumerate_candidates(ctx)
        grid = TB.evaluate_candidate_gate(ctx, cands[0]).grid if cands else None
        signatures.append((
            tuple(tuple(c.bbox) for c in cands),
            tuple(c.candidate_source for c in cands),
            None if grid is None else (grid.row_count, grid.column_count,
                                       tuple(grid.problems),
                                       tuple(c.colspan for c in grid.cells))))
    check(signatures[0] == signatures[1] == signatures[2],
          f"G1 表题文本（显式表号 / 无表号 / 续表）不得改变候选或网格结论："
          f"{signatures}")


def _g1_live(ctx):
    cands = ctx["cands"]
    live_ctx = ctx["ctx"]
    frozen = [c for c in cands if c.candidate_source == "frozen_range"]
    expected = set()
    for d in live_ctx.dispositions:
        if d.range_kind not in TB.TABLE_DISPOSITION_KINDS:
            continue
        box = TB.frozen_range_bbox(live_ctx, d)
        if box is not None:
            expected.add((int(d.start_page), tuple(box)))
    actual = {(c.page_number, tuple(c.bbox)) for c in frozen}
    by_box = {(c.page_number, tuple(c.bbox)): c for c in frozen}
    check(expected - set(by_box) == set(),
          f"G1 现场：每个单页表范围都必须出一个逐范围候选（缺 "
          f"{sorted(expected - set(by_box))[:3]}）")
    # `tb-4` 起冻结通道**另**出一类候选：一串连续相邻范围的并集。它必然让
    # "(页, 矩形)" 集合比逐范围集合大，因此这里不能再用等集断言——改成"多出来的每一个
    # 都必须是并集候选，且成员真实矩形的并集逐分量等于它"，等集断言只对逐范围部分成立。
    extra = set(by_box) - expected
    bad_extra = []
    for key in sorted(extra):
        c = by_box[key]
        members = [live_ctx.disposition_by_id(d)
                   for d in c.union_disposition_ids]
        boxes = [TB.frozen_range_bbox(live_ctx, d) for d in members]
        if (len(c.union_disposition_ids) < 2 or any(b is None for b in boxes)
                or any(d.start_page != c.page_number
                       or d.end_page != d.start_page for d in members)
                or c.frozen_disposition_id != c.union_disposition_ids[0]):
            bad_extra.append(key)
            continue
        union_box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes))
        if any(abs(a - b) > 1e-9 for a, b in zip(c.bbox, union_box)):
            bad_extra.append(key)
    check(bad_extra == [],
          f"G1 现场：冻结通道里多出来的每个 (页, 矩形) 都必须是一条并集候选，且其"
          f"矩形逐分量等于成员真实矩形的并集、owner 为文档序首段（反例 "
          f"{bad_extra[:2]}）")
    unions = [c for c in frozen if c.union_disposition_ids]
    if unions:
        _results["details"].append(
            f"INFO 真实现场冻结通道另有 {len(unions)} 条并集候选"
            f"（逐范围 {len(frozen) - len(unions)} 条）")
    solo = [c for c in frozen if not c.union_disposition_ids]
    check(all(c.frozen_overlap_ratio == 1.0 for c in solo)
          and all(c.frozen_disposition_locator for c in frozen),
          "G1 现场：逐范围冻结候选的归属比例恒为 1.0 且 locator 非空")
    # 并集候选的交占比必须**如实**等于"owner 真实矩形占并集矩形的比"（严格 < 1）：
    # 写 1.0 会绕过交占比门，那是自报而不是裁定。
    bad_ratio = []
    for c in unions:
        members = [live_ctx.disposition_by_id(d)
                   for d in c.union_disposition_ids]
        owner_box = TB.frozen_range_bbox(live_ctx, members[0])
        boxes = [TB.frozen_range_bbox(live_ctx, d) for d in members]
        union_box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes))
        want = (TB._area(owner_box) / TB._area(union_box))
        if not (0.0 < c.frozen_overlap_ratio < 1.0
                and abs(c.frozen_overlap_ratio - want) < 1e-9):
            bad_ratio.append((c.page_number, c.frozen_disposition_id,
                              c.frozen_overlap_ratio, want))
    check(bad_ratio == [],
          f"G1 现场：并集候选的归属比例必须如实等于串首真实矩形占并集矩形的比"
          f"（反例 {bad_ratio[:2]}）")
    by_id = {d.disposition_id: d for d in live_ctx.dispositions}
    bad = [c.frozen_disposition_id for c in frozen
           if c.frozen_disposition_id not in by_id
           or by_id[c.frozen_disposition_id].disposition_locator
           != c.frozen_disposition_locator]
    check(bad == [],
          f"G1 现场：每个冻结候选的 disposition_id/locator 都能对象级回查"
          f"（反例 {bad[:2]}）")
    check(all(c.geometry.row_count == 0 and c.geometry.column_count == 0
              and not c.geometry.grid_cells and not c.geometry.closed_grid
              and c.geometry.structural_gap_count == 1
              and c.geometry.edge_count == 0
              and c.geometry.intersection_count == 0 for c in frozen),
          "G1 现场：冻结范围通道**不带**任何几何网格证据（不靠自报闭合）")
    check(len(cands) >= len(actual),
          f"G1 现场：候选总数不得少于冻结范围通道数（{len(cands)}）")


def _test_g1(ctx):
    for label, caption, page_number in _SHAPES:
        _g1_shape(label, caption, page_number)
    _g1_text_blindness()
    if _gate(ctx):
        _g1_live(ctx)


# ---------------------------------------------------------------------------
# G2 §19.12.2-2：文本旗标关闭不影响几何网格的验证
# ---------------------------------------------------------------------------

_LEGACY_FLAG_NAMES = frozenset({
    "detect_table_start_flags", "detect_table_start_flags_across",
    "table_start_flags"})

_PRODUCTION_MODULES = (
    "document_structure/table_geometry.py",
    "document_structure/table_builder.py",
    "document_structure/table_classification.py",
    "document_structure/final_material_builder.py",
    "document_structure/final_verifier.py",
)


def _legacy_flag_uses() -> list:
    """生产模块里对旧 harness 文本旗标的 import / 名字引用（必须为空）。"""
    hits: list = []
    for rel in _PRODUCTION_MODULES:
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "harness":
                        hits.append((rel, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "harness":
                    hits.append((rel, node.lineno, node.module))
            elif isinstance(node, ast.Name) and node.id in _LEGACY_FLAG_NAMES:
                hits.append((rel, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in _LEGACY_FLAG_NAMES:
                hits.append((rel, node.lineno, node.attr))
    return hits


def _flag_candidate(site, disposition, bbox, source, **kw):
    """按通道构造生产类型的候选（`geometry` 与 `candidate_source` 同步）。"""
    strategy = kw.pop("strategy", "text" if source != "frozen_range" else "lines")
    return TB.BuilderCandidate(
        candidate_source=source, strategy=strategy, page_number=1, bbox=bbox,
        geometry=site.cand(bbox=bbox, source=source, strategy=strategy, **kw),
        frozen_disposition_id=disposition.disposition_id,
        frozen_disposition_locator=disposition.disposition_locator,
        frozen_overlap_ratio=1.0, competing_disposition_ids=())


def _test_g2(live):
    ctx = live
    check(_legacy_flag_uses() == [],
          f"G2 生产模块不得 import harness 或引用 detect_table_start_flags / "
          f"table_start_flags（命中 {_legacy_flag_uses()[:3]}）")
    check(TB.CHANNEL_LIMITATIONS == (
              "explicit_caption_marker_geometry_only",
              "text_start_flag_not_geometry_evidence"),
          "G2 文本通道只作定位线索、不作几何证据的登记必须逐字在册："
          f"{TB.CHANNEL_LIMITATIONS}")
    check(TB.SCOPE_CLASSIFICATION_EXEMPT_SOURCES ==
          ("frozen_range", "layout_borderless_cluster"),
          "G2 免于'线条/交点自证'的通道必须恰为冻结范围与无框聚类："
          f"{TB.SCOPE_CLASSIFICATION_EXEMPT_SOURCES}")
    check("explicit_caption_marker" in TG.CANDIDATE_SOURCES
          and "text_start_flag" in TG.CANDIDATE_SOURCES,
          "G2 两条文本通道必须登记在候选来源里（作为**定位线索**）")

    page = _grid_page()
    bbox = _union(page.lines)
    site, d, s_ctx, _b = _owning_site(page)
    # (a) 形状合格的**显式表号**候选，自带完整真实几何网格 ⇒ 仍可验证。
    cand = _flag_candidate(site, d, bbox, "explicit_caption_marker",
                           grid_cells=_skeleton(bbox), row_count=2,
                           column_count=2, cell_count=4)
    gate = TB.evaluate_candidate_gate(s_ctx, cand)
    grid = gate.grid
    check(gate.admitted and grid is not None and grid.closed
          and grid.geometry_backed and len(grid.cells) == 4
          and all(cell.rowspan == 1 and cell.colspan == 1
                  for cell in grid.cells),
          f"G2 显式表号候选只要携带完整几何网格就仍可验证（admitted="
          f"{gate.admitted} reason={gate.reason} "
          f"cells={0 if grid is None else len(grid.cells)}）")
    # (b) 文本旗标通道**本身**不是拒因：同一页上传真行带网格足够闭合时，
    #     文本通道候选照样过门（资格由真实几何决定，不由通道决定）。
    flag_ok = _flag_candidate(site, d, bbox, "text_start_flag")
    gate_flag = TB.evaluate_candidate_gate(s_ctx, flag_ok)
    check(gate_flag.admitted and gate_flag.grid is not None
          and gate_flag.grid.closed and not gate_flag.grid.geometry_backed
          and TB._has_geometric_grid_evidence(flag_ok) is False,
          f"G2 文本通道不是拒因：真实行带网格闭合时文本候选也可验证"
          f"（admitted={gate_flag.admitted} reason={gate_flag.reason}）")

    # (c) 只有文本旗标、且真实几何不足 ⇒ 既不准入，也无权主张 scope miss。
    plain = _single_col_page(texts={0: "表 4-1 主要财务指标"})
    site_b = _Site([plain], nodes=[_Node("n1")])
    d_b = _Disposition("d-1b", "table_inside", "n1", 1, 1, 0, len(plain.lines) - 1)
    s_ctx_b = site_b.context(dispositions=[d_b])
    bbox_b = _union(plain.lines)
    bare = _flag_candidate(site_b, d_b, bbox_b, "text_start_flag")
    gate = TB.evaluate_candidate_gate(s_ctx_b, bare)
    check(not gate.admitted and gate.grid is None
          and gate.reason == "grid_not_reconstructible"
          and gate.blocks_document is False,
          f"G2 只有文本旗标且真实几何不足的候选必须被硬门拒绝且不阻断"
          f"（reason={gate.reason} blocks={gate.blocks_document}）")
    check(TB._has_geometric_grid_evidence(bare) is False
          and TB._asserts_missing_table(s_ctx_b, bare) is False,
          "G2 无线条/交点/闭合网格的候选不得主张'此处存在表格几何'，"
          "也不得作出 scope miss 断言")
    check(TB._has_geometric_grid_evidence(
              _flag_candidate(site_b, d_b, bbox_b, "pdfplumber_text",
                              intersections=1)) is True
          and TB._has_geometric_grid_evidence(
              _flag_candidate(site_b, d_b, bbox_b, "pdfplumber_text",
                              edges=2)) is True
          and TB._has_geometric_grid_evidence(
              _flag_candidate(site_b, d_b, bbox_b, "pdfplumber_text",
                              closed=True, gaps=0)) is True
          and TB._has_geometric_grid_evidence(
              _flag_candidate(site_b, d_b, bbox_b, "pdfplumber_text")) is False,
          "G2 交点/真实边/闭合网格任一存在即取得几何证据，三者皆无则不得主张"
          "（只用于阻断分类，不用于准入）")
    check(TB._has_geometric_grid_evidence(
              _flag_candidate(site_b, d_b, bbox_b, "frozen_range")) is True
          and TB._has_geometric_grid_evidence(
              _flag_candidate(site_b, d_b, bbox_b,
                              "layout_borderless_cluster")) is True,
          "G2 冻结范围与无框聚类由自身结构证明归属（登记的两个豁免通道）")

    if not _gate(ctx):
        return
    report = ctx["report"]
    sources = {c.candidate_source for c in report.candidates}
    enum_sources = {c.candidate_source for c in ctx["cands"]}
    check(sources <= set(TG.CANDIDATE_SOURCES)
          and enum_sources <= set(TG.CANDIDATE_SOURCES),
          f"G2 现场候选来源必须全部落在封闭登记表内（几何 {sorted(sources)} / "
          f"枚举 {sorted(enum_sources)}）")
    check("explicit_caption_marker" not in enum_sources
          and "text_start_flag" not in enum_sources,
          "G2 现场链路的候选**没有**任何一条来自文本旗标通道"
          f"（枚举来源 {sorted(enum_sources)}）")
    check("frozen_range" in enum_sources and "pdfplumber_lines" in enum_sources,
          f"G2 现场候选来自冻结范围与真实线条几何（{sorted(enum_sources)}）")


# ---------------------------------------------------------------------------
# G3 §19.12.2-3：六类伪表形状 + 普通段落负门
# ---------------------------------------------------------------------------

def _shape_case(label, *, page, mode="owned", source="pdfplumber_text",
                edges=0, intersections=0, expected=None,
                expected_problem=None):
    """伪表形状负例：不得准入、不得阻断、不得产出任何表。"""
    site = _Site([page], nodes=[_Node("n1")])
    bbox = _union(page.lines)
    disps = []
    if mode == "owned":
        disps.append(_Disposition("d-1", "table_inside", "n1", page.page_number,
                                  page.page_number, 0, len(page.lines) - 1))
    elif mode == "over_regular":
        disps.append(_Disposition("d-reg", "regular", "n1", page.page_number,
                                  page.page_number, 0, len(page.lines) - 1))
    geom = site.cand(bbox=bbox, source=source, closed=False, gaps=1,
                     edges=edges, intersections=intersections)
    ctx = site.context(dispositions=tuple(disps), candidates=(geom,))
    cands = TB.enumerate_candidates(ctx)
    ok = len(cands) == 1
    check(ok, f"G3 {label}：候选必须被枚举（得到 {len(cands)} 个）")
    if not ok:
        return
    cand = cands[0]
    if mode == "owned":
        check(cand.frozen_disposition_id == "d-1",
              f"G3 {label}：候选归属冻结表范围 d-1")
    else:
        check(cand.frozen_disposition_id is None,
              f"G3 {label}：候选不属于任何冻结表范围（owner=None）")
    gate = TB.evaluate_candidate_gate(ctx, cand)
    check(not gate.admitted and gate.grid is None
          and gate.blocks_document is False and gate.reason == expected,
          f"G3 {label}：不得成表（admitted={gate.admitted} "
          f"reason={gate.reason} 期望={expected} "
          f"blocks={gate.blocks_document}）")
    if expected_problem is not None:
        grid = TG.derive_cell_grid(page, candidate=geom)
        check(expected_problem in grid.problems and not grid.closed,
              f"G3 {label}：网格重建必须如实报 {expected_problem!r}"
              f"（problems={grid.problems}）")
    built = TB.build_tables(ctx)
    check(built.tables == () and built.rejected != (),
          f"G3 {label}：`build_tables` 产出零张表且拒绝被显式记账"
          f"（tables={len(built.tables)} rejected={len(built.rejected)}）")
    check(all(not blocks for (_p, _b, _r, blocks) in built.rejected)
          and all(row.reason == expected for row in built.audit),
          f"G3 {label}：伪表候选的拒绝一律不阻断且审计理由一致"
          f"（audit={[(r.outcome, r.reason) for r in built.audit]}）")
    outcomes = {row.outcome for row in built.audit}
    check("accepted" not in outcomes
          and outcomes <= set(TS.CANDIDATE_AUDIT_OUTCOMES),
          f"G3 {label}：审计里没有任何 accepted 行（{sorted(outcomes)}）")


def _test_g3(ctx):
    # (i) 只有表题、无网格。
    _shape_case("只有表题", page=_single_col_page(
        texts={0: "表 4-1 主要财务指标"}), expected="grid_not_reconstructible")
    # (ii) 普通数字段落。
    _shape_case("普通数字段落", page=_single_col_page(
        x0=20.0, x1=400.0, texts={
            0: "1,234.56 2,345.67 3,456.78 4,567.89 5,678.90 6,789.01"}),
        expected="grid_not_reconstructible")
    # (iii) 编号列表。
    _shape_case("编号列表", page=_single_col_page(
        x0=20.0, x1=200.0, texts={0: "1. 主要风险", 1: "2. 应对措施",
                                  2: "3. 其他事项", 3: "4. 补充说明"}),
        expected="grid_not_reconstructible")
    # (iv) 双栏排版：形状**看起来**闭合，但不属于任何冻结结构 ⇒ 不给资格。
    _shape_case("双栏排版（不属于任何冻结结构）", page=_grid_page(),
                mode="unowned", expected="outside_frozen_table_scope")
    # (iv-b) 双栏排版浮在冻结 regular 正文上：无几何证据 ⇒ 不阻断。
    _shape_case("双栏排版（压在冻结 regular 正文上）", page=_grid_page(),
                mode="over_regular", expected="no_geometric_grid_evidence")
    # (v) 表字标题（内容含"表"但无网格）。
    _shape_case("表字标题", page=_single_col_page(
        texts={0: "表 5-1 组织结构"}), expected="grid_not_reconstructible")
    # (vi) 组织结构图。
    _shape_case("组织结构图", page=_org_chart_page(), expected="grid_not_closed",
                expected_problem="unexplained_hole")

    # 内容无关性：同一几何、不同文字 ⇒ 门的结论逐项相同。
    signature = []
    for text in ("表 5-1 组织结构", "组织结构图", "2024 年度"):
        _site, _d, c, _b = _owning_site(_single_col_page(texts={0: text}))
        cand = TB.enumerate_candidates(c)[0]
        g = TB.evaluate_candidate_gate(c, cand)
        signature.append((g.admitted, g.reason, g.blocks_document,
                          tuple(cand.bbox)))
    check(signature[0] == signature[1] == signature[2],
          f"G3 表字/普通文字不得改变硬门结论：{signature}")

    # 普通段落负门：三条阈值按边界钉死。
    check(TB.prose_negative_gate([]) == "prose_negative_gate",
          "G3 空输入必须被反例门拒绝（不得静默通过）")
    check(TB.prose_negative_gate(["a" * 60, "b" * 60, "c", "d"]) is None,
          "G3 长单元格占比恰为 0.5（不 > 0.5）时不得误拒")
    check(TB.prose_negative_gate(["a" * 60, "b" * 60, "c" * 60, "d"]) ==
          "prose_negative_gate",
          "G3 长单元格占比 0.75 > 0.5 时必须拒绝")
    check(TB.prose_negative_gate(["a" * 400, "b", "c", "d"]) is None,
          "G3 单元格长度恰为 400（不 > 400）且均值达标时不得误拒")
    check(TB.prose_negative_gate(["a" * 401, "b", "c", "d"]) == "cell_too_long",
          "G3 单元格长度 401 > 400 必须以 cell_too_long 拒绝")
    check(TB.prose_negative_gate(["a" * 239, "b" * 239, "c", "d"]) is None,
          "G3 平均长度 120.0（不 > 120）时不得误拒")
    check(TB.prose_negative_gate(["a" * 241, "b" * 241, "c", "d"]) ==
          "prose_negative_gate",
          "G3 平均长度 121 > 120 时必须拒绝")
    check(TB.prose_negative_gate(["货币资金", "1,234.56"]) is None,
          "G3 正常单元格不得被反例门误拒")
    check(TB.MAX_CELL_CHARS == 400 and TB.MAX_CELL_MEAN_CHARS == 120
          and TB.PROSE_CELL_CHAR_MIN == 60 and TB.PROSE_CELL_RATIO_MAX == 0.5,
          f"G3 反例门阈值必须为登记值（{TB.MAX_CELL_CHARS}/"
          f"{TB.MAX_CELL_MEAN_CHARS}/{TB.PROSE_CELL_CHAR_MIN}/"
          f"{TB.PROSE_CELL_RATIO_MAX}）")
    for reason in ("prose_negative_gate", "cell_too_long"):
        check(reason in FMB.CANDIDATE_REJECTION_GAP_KINDS,
              f"G3 反例门理由 {reason!r} 必须在缺口映射的全函数里（不得静默丢弃）")

    if not _gate(ctx):
        return
    built = ctx["built"]
    check(all(not blocks for (_p, _b, _r, blocks) in built.rejected),
          "G3 现场：没有任何候选拒绝是阻断性的（不得把伪表候选说成上游缺失）")
    check(built.tables != ()
          and all(t.table.structure_class in TS.TABLES_STRUCTURE_CLASSES
                  for t in built.tables),
          f"G3 现场：真表仍然建了出来（{len(built.tables)} 张）且分类合法")
    check({r[2] for r in built.rejected} <= set(FMB.CANDIDATE_REJECTION_GAP_KINDS),
          "G3 现场：每个拒绝理由都必须能映射到缺口种类（全函数）")


# ---------------------------------------------------------------------------
# G4 §19.12.2-4：只有 adjacent_to_table 不得自动成表
# ---------------------------------------------------------------------------

def _test_g4(ctx):
    # 位置相邻本身不给资格：table_adjacency 范围照样要过同一道门。
    page = _single_col_page(texts={0: "承接上表"})
    _site, _d, c, _b = _owning_site(page, range_kind="table_adjacency")
    cands = TB.enumerate_candidates(c)
    check(len(cands) == 1 and cands[0].candidate_source == "frozen_range",
          f"G4 `table_adjacency` 范围仍被枚举并检查（{len(cands)} 个候选）")
    if not cands:
        return
    gate = TB.evaluate_candidate_gate(c, cands[0])
    check(not gate.admitted and gate.blocks_document is False
          and gate.reason == "grid_not_reconstructible",
          f"G4 仅因相邻不得成表（admitted={gate.admitted} "
          f"reason={gate.reason} blocks={gate.blocks_document}）")
    built = TB.build_tables(c)
    check(built.tables == () and len(built.decisions) == 1
          and built.decisions[0].table_id is None
          and built.decisions[0].decision == "unsupported_table_structure"
          and built.decisions[0].rationale_code
          == "insufficient_structure_proof",
          f"G4 相邻范围必须留下显式裁决而不是被静默丢弃"
          f"（decisions={[(x.decision, x.table_id) for x in built.decisions]}）")
    check(built.absorbed_roles == {} and built.relations == (),
          "G4 没有表时不得产生吸收角色或关系")

    # 同一几何：table_inside 与 table_adjacency 的结论必须逐项相同。
    verdicts = []
    for kind in ("table_inside", "table_adjacency"):
        _s2, _d2, c2, _b2 = _owning_site(_grid_page(), range_kind=kind)
        cand = TB.enumerate_candidates(c2)[0]
        g = TB.evaluate_candidate_gate(c2, cand)
        verdicts.append((g.admitted, g.reason, g.blocks_document,
                         g.grid.row_count, g.grid.column_count,
                         tuple(x.colspan for x in g.grid.cells)))
    check(verdicts[0] == verdicts[1],
          f"G4 `table_inside` 与 `table_adjacency` 必须走同一道门、给出同一结论："
          f"{verdicts}")

    if not _gate(ctx):
        return
    built = ctx["built"]
    live_ctx = ctx["ctx"]
    adj_ids = {d.disposition_id for d in live_ctx.dispositions
               if d.range_kind == "table_adjacency"}
    check(bool(adj_ids),
          f"G4 现场必须存在 `table_adjacency` 范围（{len(adj_ids)} 个）")
    adj_decisions = [x for x in built.decisions
                     if x.range_kind == "table_adjacency"]
    check(len(adj_decisions) == len(adj_ids),
          f"G4 现场每个 `table_adjacency` 范围恰好一条裁决"
          f"（{len(adj_decisions)} vs {len(adj_ids)}）")
    absorbed = [x for x in adj_decisions
                if x.decision == "absorbed_as_table_body"]
    table_ids = {t.table.table_id for t in built.tables}
    owned = {cid for t in built.tables for cid in t.disposition_ids}
    check(all(x.table_id in table_ids and x.disposition_id in owned
              for x in absorbed),
          f"G4 现场：只有真的被某张表消费的相邻范围才可判 absorbed_as_table_body"
          f"（反例 {[x.disposition_id for x in absorbed][:2]}）")
    check(all(x.table_id is None for x in adj_decisions
              if x.decision != "absorbed_as_table_body"),
          "G4 现场：非吸收裁决的相邻范围不得指向任何表")
    check(all(x.decision in TS.TABLE_DECISION_KINDS for x in adj_decisions),
          "G4 现场相邻范围裁决的种类必须全部登记")
    check(all(role == "body" for cid, role in built.absorbed_roles.items()
              if cid in adj_ids),
          f"G4 现场：相邻范围若被吸收只能是表体角色"
          f"（{list(built.absorbed_roles.items())[:2]}）")


# ---------------------------------------------------------------------------
# G5 §19.12.2-5：无线框表只在多重证据闭合时可恢复
# ---------------------------------------------------------------------------

def _stable_then_short_page(page_number=1):
    """3 个两列稳定行 + 1 个只占左列的短行：稳定聚类恰好是前 3 行。"""
    lines = _two_col_rows(tops=(20.0, 33.0, 46.0))
    lines.append(_Line(3, [_Span(20.0, 59.0, 50.0, 69.0)]))
    return _Page(page_number, lines)


def _test_g5(ctx):
    # 正例：3 行 × 2 稳定列 ⇒ 无框聚类成立，行带网格闭合。
    page = _stable_then_short_page()
    cluster = TG.cluster_borderless_rows(page)
    check(cluster is not None and cluster[1] == 3 and cluster[2] == 2
          and tuple(cluster[0]) == (20.0, 20.0, 90.0, 56.0),
          f"G5 无框聚类必须只取稳定行带并给出 (bbox, 3 行, 2 稳定列)：{cluster}")
    site = _Site([page], nodes=[_Node("n1")])
    d = _Disposition("d-1", "table_inside", "n1", 1, 1, 0, len(page.lines) - 1)
    geom = site.cand(bbox=cluster[0], source="layout_borderless_cluster",
                     closed=False, gaps=1)
    ctx2 = site.context(dispositions=[d], candidates=(geom,))
    cands = TB.enumerate_candidates(ctx2)
    check(len(cands) == 2
          and {c.bbox for c in cands} == {cluster[0], _union(page.lines)},
          f"G5 无框候选必须与冻结范围候选并存（{[(c.candidate_source, c.bbox) for c in cands]}）")
    cand = next((c for c in cands
                 if c.candidate_source == "layout_borderless_cluster"), None)
    check(cand is not None and cand.frozen_disposition_id == "d-1"
          and cand.frozen_overlap_ratio == 1.0,
          f"G5 无框候选必须落在冻结表范围内（{None if cand is None else (cand.frozen_disposition_id, cand.frozen_overlap_ratio)}）")
    if cand is None:
        return
    check(dataclasses.replace(cand).geometry.grid_cells == ()
          and not cand.geometry.closed_grid
          and cand.geometry.structural_gap_count == 1,
          "G5 无框通道自身不带网格骨架（闭合只能来自行带重建）")
    gate = TB.evaluate_candidate_gate(ctx2, cand)
    grid = gate.grid
    check(gate.admitted and grid is not None and grid.closed
          and grid.row_count == 3 and grid.column_count == 2
          and not grid.geometry_backed
          and all(cell.rowspan == 1 and cell.colspan == 1
                  for cell in grid.cells),
          f"G5 多重证据闭合的无框表可恢复，且**绝不**产生无据 rowspan"
          f"（admitted={gate.admitted} reason={gate.reason} "
          f"grid={None if grid is None else (grid.row_count, grid.column_count, [x.rowspan for x in grid.cells])}）")
    check(gate.grid.problems == ()
          and len(gate.grid.cells) == 6
          and gate.grid.assigned_lines == (0, 1, 2),
          "G5 闭合无框网格必须覆盖全部 3×2 个 cell 并如实记账被消费的行")

    # 行/列证据下限：2 行不足（min_rows=3）。
    check(TG.cluster_borderless_rows(_grid_page(tops=(20.0, 33.0))) is None,
          "G5 只有 2 行不得聚成无框表（min_rows=3）")
    # 列证据下限：单列不成表。
    check(TG.cluster_borderless_rows(_single_col_page(count=4)) is None,
          "G5 只有 1 个稳定列不得聚成无框表（min_columns=2）")
    # 行带内列锚点不足（min_row_span_columns）。
    ragged = _Page(1, _two_col_rows(tops=(20.0, 33.0, 46.0),
                                    missing=((1, 1),)))
    check(TG.cluster_borderless_rows(ragged) is None,
          "G5 任一行带锚点不足 2 个时不得聚成无框表（min_row_span_columns=2）")
    check(TG.BORDERLESS_CLUSTER_PARAMS == {
              "row_overlap_min_ratio": 0.5, "band_gap_tolerance": 2.0,
              "column_tolerance": 3.0, "min_rows": 3, "min_columns": 2,
              "min_row_span_columns": 2},
          f"G5 无框聚类参数必须为登记值：{TG.BORDERLESS_CLUSTER_PARAMS}")

    # 缺一格 ⇒ 未解释空洞，如实报 unsupported，不当作合并。
    holed = _grid_page(missing=((1, 1),))
    site3, d3, _c3, bbox3 = _owning_site(holed)
    geom3 = site3.cand(bbox=bbox3, source="layout_borderless_cluster",
                       closed=False, gaps=1)
    ctx3 = site3.context(dispositions=[d3], candidates=(geom3,))
    grid3 = TG.derive_cell_grid(holed, candidate=geom3)
    check("unexplained_hole" in grid3.problems and not grid3.closed
          and all(cell.rowspan == 1 for cell in grid3.cells),
          f"G5 缺一格必须报 unexplained_hole 且不得当成纵向合并"
          f"（problems={grid3.problems} spanning={TG.cluster_borderless_rows(holed)}）")
    flagged3 = _flag_candidate(site3, d3, bbox3, "layout_borderless_cluster",
                               closed=False, gaps=1)
    gate3 = TB.evaluate_candidate_gate(ctx3, flagged3)
    check(not gate3.admitted and gate3.reason == "grid_not_closed"
          and gate3.blocks_document is False and gate3.grid is None,
          f"G5 未闭合的无框候选只报 unsupported（reason={gate3.reason}）")
    cand3 = TB.enumerate_candidates(ctx3)[0]
    gate3b = TB.evaluate_candidate_gate(ctx3, cand3)
    check(cand3.frozen_disposition_id == d3.disposition_id
          and gate3b.reason == gate3.reason
          and gate3b.blocks_document is gate3.blocks_document,
          "G5 同一几何经冻结范围通道与无框聚类通道必须得到同一裁决"
          f"（{cand3.candidate_source}:{gate3b.reason} vs "
          f"layout_borderless_cluster:{gate3.reason}）")
    built3 = TB.build_tables(ctx3)
    check(built3.tables == ()
          and [r[2] for r in built3.rejected] == ["grid_not_closed"],
          f"G5 `build_tables` 不得把未闭合的无框候选变成表（tables="
          f"{len(built3.tables)} rejected={[r[2] for r in built3.rejected]}）")

    if not _gate(ctx):
        return
    report = ctx["report"]
    borderless = [c for c in report.candidates
                  if c.candidate_source == "layout_borderless_cluster"]
    check(bool(borderless),
          f"G5 现场必须存在无框聚类候选（{len(borderless)} 个）")
    check(all(not c.grid_cells and not c.closed_grid
              and c.structural_gap_count == 1 and c.edge_count == 0
              and c.intersection_count == 0 for c in borderless),
          "G5 现场无框通道自己**从不**声称闭合网格（闭合由行带网格重建裁定）")
    built = ctx["built"]
    non_backed = [t for t in built.tables if not t.grid.geometry_backed]
    check(all(cell.rowspan == 1 for t in non_backed for cell in t.grid.cells),
          f"G5 现场非骨架网格的表里没有任何 rowspan（{len(non_backed)} 张）")
    check(all(t.grid.closed for t in built.tables),
          "G5 现场每张表的网格都必须闭合（未闭合不得提升为完整表）")
    check(all(t.grid.column_count >= 2 and t.grid.row_count >= 2
              for t in built.tables),
          "G5 现场每张表都必须满足 >=2 列 >=2 行的形状下限")


# ---------------------------------------------------------------------------
# G6 §19.12.2-6：重叠候选无法唯一裁决时 unresolved
# ---------------------------------------------------------------------------

def _two_disposition_page():
    """两个**单行**表范围 + 一个跨两者的几何候选（三者都真在版式里）。"""
    return _Page(1, [
        _Line(0, [_Span(20.0, 18.0, 50.0, 78.0),
                  _Span(60.0, 18.0, 90.0, 78.0)]),
        _Line(1, [_Span(20.0, 70.0, 50.0, 90.0),
                  _Span(60.0, 70.0, 90.0, 90.0)]),
    ])


def _test_g6(ctx):
    # 裁决键：闭合 > 缺口更少 > 面积更小，且**刻意**不含页码/坐标。
    page = _grid_page()
    site = _Site([page], nodes=[_Node("n1")])
    bbox = _union(page.lines)
    strong = site.cand(bbox=bbox, closed=True, gaps=0, edges=4)
    weak = site.cand(bbox=(10.0, 18.0, 95.0, 71.0), closed=False, gaps=2)
    check(TG.candidate_order_key(strong) < TG.candidate_order_key(weak),
          "G6 裁决键必须让'闭合且缺口更少'的候选胜出")
    check(TG.candidate_order_key(
              site.cand(bbox=(20.0, 20.0, 90.0, 69.0), closed=True, gaps=0))
          == TG.candidate_order_key(
              site.cand(bbox=(30.0, 30.0, 100.0, 79.0), closed=True, gaps=0))
          != TG.candidate_order_key(
              site.cand(bbox=(20.0, 20.0, 90.0, 79.0), closed=True, gaps=0)),
          "G6 裁决键必须**不含**页码/坐标（同面积不同位置即并列；面积不同则不并列）")
    winners, unresolved = TG.resolve_overlapping_candidates((strong, weak))
    check([c.bbox for c in winners] == [bbox] and unresolved == (),
          f"G6 强弱可分时必须给出唯一胜出（winners={[c.bbox for c in winners]}）")
    rev_winners, rev_unresolved = TG.resolve_overlapping_candidates((weak, strong))
    check([c.bbox for c in rev_winners] == [c.bbox for c in winners]
          and rev_unresolved == unresolved,
          "G6 重叠裁决必须与输入顺序无关")

    # 完全并列的重叠候选：整组 unresolved，不得"取第一个"。
    twin_a = site.cand(bbox=(20.0, 20.0, 90.0, 69.0), closed=False, gaps=3)
    twin_b = site.cand(bbox=(25.0, 25.0, 95.0, 74.0), closed=False, gaps=3)
    check(TG.candidate_order_key(twin_a) == TG.candidate_order_key(twin_b),
          "G6 前置：两个候选的裁决键必须真的完全相等")
    w2, u2 = TG.resolve_overlapping_candidates((twin_a, twin_b))
    check(w2 == () and {c.bbox for c in u2} == {twin_a.bbox, twin_b.bbox}
          and all(c.unresolved_reason is None
                  or c.unresolved_reason in TG.UNRESOLVED_GEOMETRY_REASONS
                  for c in u2),
          f"G6 并列重叠必须整组 unresolved 且理由码登记"
          f"（winners={len(w2)} unresolved={len(u2)}）")
    w3, u3 = TG.resolve_overlapping_candidates((twin_b, twin_a))
    check(w3 == () and {c.bbox for c in u3} == {c.bbox for c in u2},
          "G6 并列重叠的 unresolved 结论必须与输入顺序无关")
    check([tuple(c.bbox) for c in TG.sort_candidates((twin_a, twin_b))]
          == [tuple(c.bbox) for c in TG.sort_candidates((twin_b, twin_a))],
          "G6 并列候选的排序必须与输入顺序无关（稳定键兜底）")

    # 生产路径：几何候选跨两个冻结表范围、且无人完全包含 ⇒ 不可唯一裁决。
    page2 = _two_disposition_page()
    site2 = _Site([page2], nodes=[_Node("n1")])
    d_a = _Disposition("d-a", "table_inside", "n1", 1, 1, 0, 0)
    d_b = _Disposition("d-b", "table_inside", "n1", 1, 1, 1, 1)
    box_a = TB.frozen_range_bbox(site2.context(dispositions=[d_a]), d_a)
    box_b = TB.frozen_range_bbox(site2.context(dispositions=[d_b]), d_b)
    check(box_a == (20.0, 18.0, 90.0, 78.0)
          and box_b == (20.0, 70.0, 90.0, 90.0),
          f"G6 前置：两个冻结范围的真实矩形为手算值（{box_a} / {box_b}）")
    ambig = site2.cand(bbox=(20.0, 20.0, 90.0, 80.0), closed=True, gaps=0,
                       edges=8, intersections=8)
    ctx2 = site2.context(dispositions=[d_a, d_b], candidates=(ambig,))
    cand2 = next(c for c in TB.enumerate_candidates(ctx2)
                 if tuple(c.bbox) == (20.0, 20.0, 90.0, 80.0))
    check(cand2.competing_disposition_ids == ("d-b",)
          and 0.95 <= cand2.frozen_overlap_ratio < 1.0,
          f"G6 前置：候选真的被两个范围同时覆盖且无人完全包含"
          f"（owner={cand2.frozen_disposition_id} "
          f"ratio={round(cand2.frozen_overlap_ratio, 4)} "
          f"competing={cand2.competing_disposition_ids}）")
    gate2 = TB.evaluate_candidate_gate(ctx2, cand2)
    check(not gate2.admitted and gate2.reason == "candidate_not_uniquely_mapped"
          and gate2.blocks_document is False and gate2.grid is None
          and gate2.frozen_disposition_id == "d-a",
          f"G6 几何无法唯一落入冻结范围 ⇒ candidate_not_uniquely_mapped"
          f"（reason={gate2.reason} blocks={gate2.blocks_document}）")
    check("candidate_not_uniquely_mapped" in TS.CANDIDATE_AUDIT_UNRESOLVED_REASONS,
          "G6 不可唯一裁决的理由必须在'必须保留为未决'的登记里")
    built2 = TB.build_tables(ctx2)
    rows = [r for r in built2.audit if r.reason == "candidate_not_uniquely_mapped"]
    check(len(rows) == 1 and rows[0].outcome == "unresolved"
          and rows[0].blocks_document is False,
          f"G6 不可唯一裁决必须记为 unresolved（不得记成'已定论不是表'）"
          f"（{[(r.outcome, r.reason) for r in built2.audit]}）")
    check(len(built2.decisions) == 2
          and {x.disposition_id for x in built2.decisions} == {"d-a", "d-b"}
          and all(x.decision in TS.TABLE_DECISION_KINDS
                  for x in built2.decisions),
          "G6 两个范围各留下**恰好**一条登记的裁决")

    # 部分重叠（无人包含）⇒ candidate_straddles_frozen_range。
    strad = site2.cand(bbox=(20.0, 38.0, 90.0, 90.0), closed=True, gaps=0,
                       edges=6)
    ctx3 = site2.context(dispositions=[d_a, d_b], candidates=(strad,))
    cand3 = next(c for c in TB.enumerate_candidates(ctx3)
                 if tuple(c.bbox) == (20.0, 38.0, 90.0, 90.0))
    gate3 = TB.evaluate_candidate_gate(ctx3, cand3)
    check(not gate3.admitted
          and gate3.reason == "candidate_straddles_frozen_range"
          and gate3.blocks_document is False and gate3.grid is None,
          f"G6 与冻结范围部分重叠 ⇒ candidate_straddles_frozen_range"
          f"（reason={gate3.reason}）")
    check(TB.CANDIDATE_CONTAINED_RATIO == 0.95
          and FMB.CANDIDATE_REJECTION_GAP_KINDS[
              "candidate_straddles_frozen_range"] == "unresolved_geometry",
          "G6 唯一包含比例 0.95 与'部分重叠→unresolved_geometry'映射必须登记")

    # 裁决计划：门给出不可唯一裁决 ⇒ 该范围判 unresolved_geometry。
    _s4, d4, c4, _b4 = _owning_site(_grid_page())
    gates = {d4.disposition_id: TB.GateResult(
        admitted=False, reason="candidate_not_uniquely_mapped",
        blocks_document=False, grid=None,
        frozen_disposition_id=d4.disposition_id)}
    drafts = TB.plan_decisions(c4, (), gates)
    check(len(drafts) == 1 and drafts[0].decision == "unresolved_geometry"
          and drafts[0].rationale_code == "geometry_not_uniquely_resolved"
          and drafts[0].target_kind
          == TS.TABLE_DECISION_TARGETS["unresolved_geometry"] == "none"
          and drafts[0].table_id is None and drafts[0].final_span_id is None
          and drafts[0].disposition_locator == d4.disposition_locator,
          f"G6 几何不可唯一裁决必须判 unresolved_geometry（得到 "
          f"{[(x.decision, x.rationale_code, x.target_kind) for x in drafts]}）")
    check("geometry_not_uniquely_resolved" in TS.DECISION_RATIONALE_CODES
          and TS.TABLE_DECISION_TARGETS["unsupported_table_structure"] == "none",
          "G6 依据码与 target 真值表必须登记")
    other = TB.plan_decisions(c4, (), {d4.disposition_id: TB.GateResult(
        admitted=False, reason="grid_not_closed", blocks_document=False,
        grid=None, frozen_disposition_id=d4.disposition_id)})
    check(other[0].decision == "unsupported_table_structure"
          and other[0].rationale_code == "insufficient_structure_proof",
          "G6 其它门理由一律判 unsupported_table_structure（不得借 unresolved "
          "无限期悬置）")

    if not _gate(ctx):
        return
    built = ctx["built"]
    check(all(c.blocks_document is False for _c, c in ctx["gates"]
              if not c.admitted),
          "G6 现场：没有任何候选拒绝是阻断性的")
    unresolved_rows = [r for r in built.audit if r.outcome == "unresolved"]
    rejected_rows = [r for r in built.audit if r.outcome == "rejected"]
    check(all(r.reason in TS.CANDIDATE_AUDIT_UNRESOLVED_REASONS
              for r in unresolved_rows),
          f"G6 现场 unresolved 审计行的理由码必须全部登记"
          f"（{len(unresolved_rows)} 行）")
    check(all(r.reason not in TS.CANDIDATE_AUDIT_UNRESOLVED_REASONS
              for r in rejected_rows),
          "G6 现场 rejected 审计行不得使用'未决'理由码")
    check(len(built.audit)
          == len(ctx["cands"]) + len(ctx["report"].unresolved_pages),
          f"G6 现场每个候选恰有一条审计行（{len(built.audit)} vs "
          f"{len(ctx['cands'])} + {len(ctx['report'].unresolved_pages)}）")
    check(len({r.table_id for r in built.audit if r.outcome == "accepted"})
          == len(built.tables),
          "G6 现场 accepted 审计行与建成的表一一对应")
    ids = [x.disposition_id for x in built.decisions]
    check(len(ids) == len(set(ids)),
          f"G6 现场每个 disposition 恰有一条裁决（重复 {len(ids) - len(set(ids))}）")
    check({x.disposition_id for x in built.decisions}
          == {d.disposition_id for d in ctx["ctx"].dispositions
              if d.range_kind in TB.TABLE_DISPOSITION_KINDS},
          "G6 现场裁决集合与表范围集合精确等集")
    check(all(x.decision in TS.TABLE_DECISION_KINDS
              and x.target_kind == TS.TABLE_DECISION_TARGETS[x.decision]
              and x.rationale_code in TS.DECISION_RATIONALE_CODES
              for x in built.decisions),
          "G6 现场裁决的种类/目标/依据码必须全部落在封闭登记表内")
    check(all(row.outcome in TS.CANDIDATE_AUDIT_OUTCOMES
              and row.candidate_source in TG.CANDIDATE_SOURCES
              for row in built.audit),
          "G6 现场审计行的三态与来源必须全部落在封闭登记表内")


# ---------------------------------------------------------------------------
# G7 §19.12.2-7：geometry 命中冻结 regular span ⇒ upstream_table_scope_miss
# ---------------------------------------------------------------------------

def _test_g7(ctx):
    page = _grid_page()
    site = _Site([page], nodes=[_Node("n1")])
    bbox = _union(page.lines)
    regular = _Disposition("d-reg", "regular", "n1", 1, 1, 0, len(page.lines) - 1)
    scope_geom = site.cand(bbox=bbox, source="pdfplumber_lines", closed=True,
                           gaps=0, edges=6, intersections=6)
    c = site.context(dispositions=[regular], candidates=(scope_geom,))
    cands = TB.enumerate_candidates(c)
    check(len(cands) == 1 and cands[0].frozen_disposition_id is None
          and cands[0].candidate_source == "pdfplumber_lines",
          f"G7 前置：regular 正文不产生冻结表范围，几何候选无归属"
          f"（{[(x.candidate_source, x.frozen_disposition_id) for x in cands]}）")
    if not cands:
        return
    cand = cands[0]
    check(TB._overlaps_frozen_regular(c, cand) is True,
          "G7 前置：候选矩形确实与冻结 regular body span 在同一页相交")
    check(TB._asserts_missing_table(c, cand) is True,
          "G7 前置：候选携带几何网格且形状合格，有资格主张'此处有一张漏掉的表'")
    gate = TB.evaluate_candidate_gate(c, cand)
    check(not gate.admitted
          and gate.reason == "geometry_overlaps_frozen_regular_span"
          and gate.blocks_document is True and gate.grid is None,
          f"G7 geometry 命中冻结 regular span ⇒ "
          f"geometry_overlaps_frozen_regular_span 且阻断（得到 {gate.reason} "
          f"blocks={gate.blocks_document}）")
    check("geometry_overlaps_frozen_regular_span"
          in TS.CANDIDATE_AUDIT_UNRESOLVED_REASONS,
          "G7 scope miss 的候选必须是'未决'而不是'已定论不是表'")
    check(FMB.CANDIDATE_REJECTION_GAP_KINDS[
              "geometry_overlaps_frozen_regular_span"]
          == "upstream_table_scope_miss",
          "G7 scope miss 理由必须映射为 upstream_table_scope_miss")
    check("upstream_table_scope_miss" in TS.GAP_BLOCKING_KINDS
          and "upstream_table_scope_miss" in TS.DOCUMENT_BLOCKING_REASONS,
          "G7 upstream_table_scope_miss 必须在阻断登记里")

    built = TB.build_tables(c)
    check(built.tables == ()
          and [(r[2], r[3]) for r in built.rejected]
          == [("geometry_overlaps_frozen_regular_span", True)],
          f"G7 scope miss 不得静默重分类成表（tables={len(built.tables)} "
          f"rejected={[(r[2], r[3]) for r in built.rejected]}）")
    check([r.outcome for r in built.audit] == ["unresolved"],
          f"G7 scope miss 的审计行必须是 unresolved"
          f"（{[(r.outcome, r.reason) for r in built.audit]}）")
    check(built.decisions == () and built.absorbed_roles == {},
          "G7 regular 正文范围不得得到任何表格吸收裁决（它是正文，不是表内范围）")
    entries = FMB._build_gap_entries(None, c, built, (), (), (), (), (), 0)
    entry = entries[0] if entries else None
    check(len(entries) == 1 and entry is not None
          and entry.gap_kind == "upstream_table_scope_miss"
          and entry.detail_code
          == "candidate_rejected:geometry_overlaps_frozen_regular_span"
          and entry.blocks_document_capability is True
          and entry.page_number == 1 and entry.disposition_id is None,
          f"G7 缺口出口必须产出 upstream_table_scope_miss"
          f"（得到 {[(e.gap_kind, e.detail_code) for e in entries]}）")
    raises(lambda: FMB._build_gap_entries(
               None, c,
               dataclasses.replace(built, rejected=(
                   (1, bbox, "geometry_overlaps_frozen_regular_span", False),)),
               (), (), (), (), (), 0),
           FMB.FinalMaterialBuildError, "blocks_document 与缺口种类",
           "G7 阻断性必须由缺口种类单点决定（自报不阻断即拒绝）")

    # 反向控制：同样压在 regular 上但**没有**几何证据 ⇒ 不阻断、不主张 scope miss。
    quiet_geom = site.cand(bbox=bbox, source="pdfplumber_text", strategy="text",
                           closed=False, gaps=1)
    c2 = site.context(dispositions=[regular], candidates=(quiet_geom,))
    quiet = TB.enumerate_candidates(c2)[0]
    gate_q = TB.evaluate_candidate_gate(c2, quiet)
    check(not gate_q.admitted and gate_q.blocks_document is False
          and gate_q.reason == "no_geometric_grid_evidence",
          f"G7 无几何证据的同形候选不得阻断文档（reason={gate_q.reason} "
          f"blocks={gate_q.blocks_document}）")
    built_q = TB.build_tables(c2)
    entries_q = FMB._build_gap_entries(None, c2, built_q, (), (), (), (), (), 0)
    check(len(entries_q) == 1
          and entries_q[0].blocks_document_capability is False,
          f"G7 不阻断的拒绝只能产出非阻断缺口"
          f"（{[(e.gap_kind, e.blocks_document_capability) for e in entries_q]}）")

    # 不属于任何冻结结构（未覆盖 regular）⇒ 同样不阻断。
    page_far = _Page(1, [_Line(0, [_Span(20.0, 200.0, 90.0, 210.0)]),
                         _Line(1, [_Span(20.0, 213.0, 90.0, 223.0)]),
                         _Line(2, [_Span(20.0, 226.0, 90.0, 236.0)])])
    site_far = _Site([page_far], nodes=[_Node("n1")])
    far_geom = site_far.cand(bbox=_union(page_far.lines),
                             source="pdfplumber_lines", closed=True, gaps=0,
                             edges=4)
    c3 = site_far.context(candidates=(far_geom,))
    far = TB.enumerate_candidates(c3)[0]
    gate_f = TB.evaluate_candidate_gate(c3, far)
    check(gate_f.reason == "outside_frozen_table_scope"
          and gate_f.blocks_document is False
          and TB._overlaps_frozen_regular(c3, far) is False,
          f"G7 不属于任何冻结结构的候选只报 outside_frozen_table_scope"
          f"（reason={gate_f.reason}）")

    if not _gate(ctx):
        return
    built_live = ctx["built"]
    reasons = {r[2] for r in built_live.rejected}
    check("geometry_overlaps_frozen_regular_span" not in reasons,
          f"G7 现场：没有任何候选触发 scope miss（现场理由 {sorted(reasons)}）")
    check(all(r[3] is False for r in built_live.rejected),
          "G7 现场：没有任何候选拒绝是阻断性的（不得过度阻断整份文档）")
    live_ctx = ctx["ctx"]
    regular_ids = {d.disposition_id for d in live_ctx.dispositions
                   if d.range_kind == "regular"}
    decided = {x.disposition_id for x in built_live.decisions}
    check(bool(regular_ids) and regular_ids & decided == set(),
          f"G7 现场：regular 正文范围一律不进入表格吸收裁决"
          f"（{len(regular_ids)} 个，交集 {sorted(regular_ids & decided)[:2]}）")
    check(reasons <= set(FMB.CANDIDATE_REJECTION_GAP_KINDS),
          f"G7 缺口映射必须是全函数（未登记理由 "
          f"{sorted(reasons - set(FMB.CANDIDATE_REJECTION_GAP_KINDS))}）")
    for reason in sorted(reasons):
        kind = FMB.CANDIDATE_REJECTION_GAP_KINDS[reason]
        if kind is None:
            continue
        check(all(r[3] == (kind in TS.GAP_BLOCKING_KINDS)
                  for r in built_live.rejected if r[2] == reason),
              f"G7 现场理由 {reason!r} 的阻断性与缺口种类 {kind!r} 一致")


# ---------------------------------------------------------------------------
# G8 §三.1/§三.3：列边界只允许落在真实片段之间的空隙里
# ---------------------------------------------------------------------------

#: G8 的候选矩形：四列（列带起点 20 / 102 / 170 / 185）三行带的边框less 网格。
_G8_BBOX = (0.0, 0.0, 220.0, 60.0)
_G8_LEAD_ROWS = (
    ((20.0, 50.0), (102.0, 180.0), (185.0, 210.0)),
    ((20.0, 50.0), (102.0, 140.0), (170.0, 180.0), (185.0, 210.0)),
)


def _g8_page(second_band):
    """三行带四列的网格，**唯一**变量是第 2 行带的形状。

    第 0 行带上有一个横跨 `edge=170` 的长片段（102–180）。它能否被读成"跨列合并
    cell"，取决于这条边界在**该行带之外**还有几个独立见证（真实存在两段被空白分开
    的文字）：见证 ≥2 ⇒ 允许；只有 1 ⇒ 这条边界只在被切开的那一行上自证 ⇒
    如实记 `column_boundary_cuts_fragment`，整张候选不闭合。
    """
    rows = _G8_LEAD_ROWS + (second_band,)
    lines = []
    for i, row in enumerate(rows):
        top = 20.0 + i * 13.0
        lines.append(_Line(i, [
            _Span(x0, top, x1, top + _ROW_H, text=f"c{i}-{j}")
            for j, (x0, x1) in enumerate(row)]))
    return _Page(1, lines)


def _g8_grid(second_band):
    page = _g8_page(second_band)
    site = _Site([page], nodes=[_Node("n1")])
    geom = site.cand(bbox=_G8_BBOX, source="pdfplumber_lines", closed=True,
                     gaps=0, edges=0)
    return page, geom, TG.derive_cell_grid(page, candidate=geom)


def _test_g8(_ctx):
    # 正例（§八(4)）：第 2 行带在三列上都有真实片段 ⇒ 边界 170 有第 1、第 2 两个
    # 独立见证 ⇒ 第 0 行带上的长片段合法地成为 colspan=2 的跨列合并 cell。
    _, _, ok = _g8_grid(((20.0, 50.0), (102.0, 140.0), (170.0, 180.0),
                         (185.0, 210.0)))
    merged = [c for c in ok.cells if c.colspan > 1]
    check(ok.closed and ok.problems == ()
          and ok.row_count == 3 and ok.column_count == 4,
          f"G8 正例：多行稳定列间隙的真实网格必须闭合"
          f"（problems={ok.problems} rows={ok.row_count} cols={ok.column_count}）")
    check(len(merged) == 1 and merged[0].row == 0 and merged[0].column == 1
          and merged[0].colspan == 2
          and merged[0].bbox[:2] == (102.0, 20.0),
          f"G8 正例：跨列合并 cell 只允许落在被独立见证的那条边界上"
          f"（{[(c.row, c.column, c.colspan) for c in ok.cells]}）")
    check(all(c.colspan == 1 for c in ok.cells if c.row != 0),
          "G8 正例：其余行带逐格单列，不得凭空造出第二个横跨格")

    # 负例（§八(3)）：同一条边界在第 2 行带上如果只有**一侧**有片段，它就没有第二个
    # 独立见证 —— 这条边界只是被切开那一行的自证，候选必须被拒。
    _, _, bad = _g8_grid(((170.0, 210.0),))
    check("column_boundary_cuts_fragment" in bad.problems and not bad.closed,
          f"G8 负例：只能被被切开的那一行自证的列边界不得降级成跨列合并"
          f"（problems={bad.problems}）")
    check(not any(c.colspan > 1 and c.row == 0 for c in bad.cells),
          f"G8 负例：被拒的横跨片段不得留下 colspan>1 的 cell"
          f"（{[(c.row, c.column, c.colspan) for c in bad.cells]}）")
    check(bad.cells != () and not any(c.colspan > 1 and c.row == 0
                                      for c in bad.cells),
          "G8 负例：网格仍如实给出其余 cell（不是整体消失）")

    # 反向控制：第 2 行带里没有 170 / 102 这两个列锚点 ⇒ 这条边界根本不会被建出来，
    # 于是拒绝理由必须换成别的码，说明上面那条拒绝确实由"边界被切开"造成，
    # 不是任何形状都会报它。
    _, _, no_cols = _g8_grid(((20.0, 50.0), (185.0, 210.0)))
    check("column_boundary_cuts_fragment" not in no_cols.problems
          and not no_cols.closed,
          f"G8 反向控制：第 2 行带没有 170/102 列锚点时，拒绝理由不得冒充"
          f"cut_fragment（problems={no_cols.problems}）")


# ---------------------------------------------------------------------------
# G9 §0.19（乙）`lines` 通道第二路：只有**细长描边**才是行列边界
# ---------------------------------------------------------------------------

#: G9 合成 pdfplumber 侧视图：只有 `rects`/`lines`/`curves` 三种真实对象表，
#: 以及 `align_page_frame` 需要的最小尺寸字段。
class _Stroke:
    """一条真实描边；只暴露 `_rule_stroke_lines` 真正读取的四个坐标与 `object_type`。"""

    __slots__ = ("x0", "x1", "top", "bottom", "object_type")

    def __init__(self, x0, top, x1, bottom, kind="rect"):
        self.x0 = float(x0)
        self.x1 = float(x1)
        self.top = float(top)
        self.bottom = float(bottom)
        self.object_type = kind

    def __getitem__(self, key):
        return getattr(self, key)


class _StrokePage:
    """只带真实描边对象的 pdfplumber 侧视图（**没有** find_tables，本组不跑它）。"""

    __slots__ = ("rects", "lines", "curves", "width", "height", "rotation")

    def __init__(self, *, rects=(), lines=(), curves=()):
        self.rects = list(rects)
        self.lines = list(lines)
        self.curves = list(curves)
        self.width = _PAGE_W
        self.height = _PAGE_H
        self.rotation = 0


def _test_g9(_ctx):
    """乙的几何侧：规线判据、第二路设置、以及"第二路只增不改"的真实现场性质。

    正例：细长描边（一维 ≤ 上界、另一维 > 上界）是规线，三个对象表都扫；
    反例：单元格框 / 文字框 / 内缩合并框 / 图片框（两维都远大于上界）**都不是**规线，
    因此它们的边不会变成行列边界，也**不能**靠模板补出缺失表头。
    """
    bound = TG.RULE_STROKE_MAX_THICKNESS_PT
    check(bound == 0.5, f"G9 规线上界必须是登记常量（实测 {bound}）")

    # (1) 判据只有量纲一条：一维 ≤ 上界、另一维 > 上界。
    thin_v = _Stroke(56.6, 100.0, 56.6 + bound, 400.0, "rect")
    thin_h = _Stroke(56.6, 100.0, 400.0, 100.0 + bound, "line")
    cell_frame = _Stroke(56.6, 100.0, 400.0, 400.0, "rect")
    word_box = _Stroke(60.0, 100.0, 100.0, 110.0, "rect")
    inset_merged = _Stroke(200.0, 100.0, 260.0, 112.0, "rect")
    image_frame = _Stroke(56.6, 100.0, 538.0, 700.0, "rect")
    dot = _Stroke(300.0, 300.0, 300.0 + bound, 300.0 + bound, "rect")
    pair = (thin_v, cell_frame, word_box, inset_merged, image_frame, dot, thin_h)
    page = _StrokePage(rects=pair)
    vert, horz = TG._rule_stroke_lines(page)
    check([id(o) for o in vert] == [id(thin_v)],
          f"G9 正例：只有细长**竖直**描边是竖直规线（实测 {len(vert)} 条）")
    check([id(o) for o in horz] == [id(thin_h)],
          f"G9 正例：只有细长**水平**描边是水平规线（实测 {len(horz)} 条）")
    not_rules = {id(cell_frame), id(word_box), id(inset_merged), id(image_frame)}
    check(not [o for o in vert + horz if id(o) in not_rules],
          f"G9 反例：单元格框／文字框／内缩合并框／图片框**一律**不得成为规线"
          f"（混入 {len([o for o in vert + horz if id(o) in not_rules])} 条）")
    check(dot not in vert and dot not in horz,
          "G9 反例：两维都不超过上界的“点”两个方向都不得成为规线")

    # 边界：恰好等于上界（`<= bound`）且另一维更大 ⇒ 是规线；超过一点即不再是。
    edge = _Stroke(420.0, 100.0, 420.0 + bound, 400.0, "rect")
    over = _Stroke(500.0, 100.0, 500.0 + bound + 0.001, 400.0, "rect")
    vert2, _h2 = TG._rule_stroke_lines(_StrokePage(rects=(edge, over)))
    check([id(o) for o in vert2] == [id(edge)],
          f"G9 边界：厚度恰等于上界的是规线、超过上界的不是"
          f"（实测 {len(vert2)} 条）")

    # (2) 三个对象表都扫；顺序按 (主坐标, 次坐标) 升序固定；对象**原样**返回。
    v_far = _Stroke(300.0, 20.0, 300.0 + bound, 60.0, "curves")
    h_low = _Stroke(20.0, 500.0, 90.0, 500.0 + bound, "rect")
    h_high = _Stroke(40.0, 200.0, 80.0, 200.0 + bound, "curves")
    shuffled = _StrokePage(rects=(h_low, thin_v), lines=(thin_h,),
                           curves=(v_far, h_high))
    vert3, horz3 = TG._rule_stroke_lines(shuffled)
    check([(o["x0"], o["top"]) for o in vert3]
          == sorted((o["x0"], o["top"]) for o in vert3)
          and sorted(o["x0"] for o in vert3) == [56.6, 300.0],
          f"G9 顺序：竖直规线按 (x0, top, bottom) 升序且跨对象表"
          f"（{[ (o['x0'], o['top']) for o in vert3 ]}）")
    check([(o["top"], o["x0"]) for o in horz3]
          == sorted((o["top"], o["x0"]) for o in horz3)
          and len(horz3) == 3,
          f"G9 顺序：水平规线按 (top, x0, x1) 升序且跨对象表"
          f"（{[ (o['top'], o['x0']) for o in horz3 ]}）")
    check(all(isinstance(o, _Stroke) for o in vert3 + horz3),
          "G9 原样返回：规线必须是页面**真实对象本身**，不得复制或改写坐标")
    check(all(hasattr(o, "object_type") for o in vert3 + horz3),
          "G9 原样返回：`object_type` 必须随对象保留（`obj_to_edges` 需要它）")
    check(TG._rule_stroke_lines(shuffled)[0][0] is TG._rule_stroke_lines(shuffled)[0][0],
          "G9 纯函数：同一页两次调用给出同一批对象")

    # (3) 第二路设置：任一向无规线 ⇒ 不做这一路（**不**用模板补表头/行列）。
    lines_settings = TG.geometry_settings("lines")
    check(TG._ruled_lattice_settings(_StrokePage(rects=(cell_frame,)),
                                     lines_settings) is None,
          "G9 反例：整页只有单元格框 ⇒ 第二路必须**放弃**（不靠模板补行列边界）")
    only_v = _StrokePage(rects=(thin_v,))
    only_h = _StrokePage(rects=(thin_h,))
    check(TG._ruled_lattice_settings(only_v, lines_settings) is None
          and TG._ruled_lattice_settings(only_h, lines_settings) is None,
          "G9 反例：只有竖向或只有横向规线时第二路都不得开跑")

    before = dict(lines_settings.table_settings)
    ts = TG._ruled_lattice_settings(shuffled, lines_settings)
    check(ts is not None and ts["vertical_strategy"] == "explicit"
          and ts["horizontal_strategy"] == "explicit",
          "G9 正例：第二路必须显式给出行列边界策略")
    check([id(o) for o in ts["explicit_vertical_lines"]]
          == [id(o) for o in vert3]
          and [id(o) for o in ts["explicit_horizontal_lines"]]
          == [id(o) for o in horz3],
          "G9 正例：显式边界必须就是本页真实规线对象（不得凭 bbox 造字符/行标签）")
    check(set(ts) == set(before) | {"explicit_vertical_lines",
                                    "explicit_horizontal_lines"},
          f"G9 正例：第二路只允许**新增**两条显式边界键，其余键一个不得增删"
          f"（多 {sorted(set(ts) - set(before) - {'explicit_vertical_lines', 'explicit_horizontal_lines'})}／"
          f"少 {sorted(set(before) - set(ts))}）")
    check(all(ts[k] == v for k, v in before.items()
              if k not in ("vertical_strategy", "horizontal_strategy",
                           "explicit_vertical_lines", "explicit_horizontal_lines")),
          "G9 正例：除四个被收紧的键，其余固定参数必须与 `lines` 通道逐项相同"
          "（第二路不是另一套规则）")
    check(lines_settings.table_settings == before,
          "G9 正例：第二路不得就地改写传入的 `lines` settings")

    # (4) 真实现场：第二路**只增不改**——关掉它，每页候选的 (矩形) 集合只会变小或不变；
    #     且至少有一页确实变小（第二路是本演示目标表的唯一构造来源）。新增的候选必须
    #     仍是同一条 `pdfplumber_lines` 通道、带本页真实 frame 指纹。
    if _gate(_ctx):
        handoff = _ctx["handoff"]
        full = _ctx["report"]
        original_fn = TG._ruled_lattice_settings
        TG._ruled_lattice_settings = lambda pdf_page, settings_obj: None
        try:
            legacy = _ORIGINAL_EXTRACT(handoff.layout_capability, pages=None)
        finally:
            TG._ruled_lattice_settings = original_fn

        def _keys(report):
            out: dict = {}
            for c in report.candidates:
                out.setdefault(c.page_number, set()).add(tuple(c.bbox))
            return out

        now = _keys(full)
        was = _keys(legacy)
        grew = {p: now[p] - was.get(p, set()) for p in now}
        grew = {p: ks for p, ks in grew.items() if ks}
        lost = {p: was[p] - now[p] for p in was if was[p] - now.get(p, set())}
        check(not lost,
              f"G9 现场：第二路只增不改——关掉它不得让任何页少掉候选"
              f"（反例 {sorted(lost)[:3]}）")
        check(bool(grew),
              "G9 现场：第二路必须是**载荷路径**（至少一页因它多出候选），"
              "否则本组没有覆盖真实构造来源")
        frame_of = {f.page_number: f.frame_fingerprint for f in full.frames}
        added = [c for c in full.candidates
                 if tuple(c.bbox) in grew.get(c.page_number, set())]
        check(bool(added) and all(
            c.candidate_source == "pdfplumber_lines" and c.strategy == "lines"
            and c.frame_fingerprint == frame_of.get(c.page_number)
            and c.candidate_source in TG.CANDIDATE_SOURCES
            for c in added),
            "G9 现场：第二路新增的候选仍是同一条 `pdfplumber_lines` 通道、"
            "带本页真实 frame 指纹、且来源码仍在封闭六元组内")
        check(len(TG.CANDIDATE_SOURCES) == 6,
              "G9 现场：第二路不得扩大候选来源封闭集（仍为 6 个）")


# ---------------------------------------------------------------------------
# 附加：几何层与构建层的机械自检
# ---------------------------------------------------------------------------

def _test_self_check():
    geo = TG.self_check()
    check(geo["problems"] == [], f"几何层自检必须无问题：{geo['problems']}")
    check(geo["strategy_count"] == len(TG.GEOMETRY_STRATEGIES) == 2
          and geo["candidate_source_count"] == len(TG.CANDIDATE_SOURCES) == 6,
          "几何层自检必须覆盖全部 strategy 与候选来源")
    check(geo["geometry_version"] == V.TABLE_GEOMETRY_VERSION
          and geo["settings_fingerprint_lines"]
          != geo["settings_fingerprint_text"],
          "两种 strategy 的 settings 指纹必须各自绑定且互不相同")
    build = TB.self_check()
    check(build["problems"] == [], f"构建层自检必须无问题：{build['problems']}")
    check(build["decision_kind_count"] == len(TS.TABLE_DECISION_KINDS) == 8
          and build["continuation_proof_count"]
          == len(TS.CONTINUATION_PROOF_KINDS),
          "构建层自检必须覆盖全部裁决种类与 continuation 证明")
    check(build["proximity_pt"] == TB.PROXIMITY_PT == 12.0
          and list(build["channel_limitations"]) == list(TB.CHANNEL_LIMITATIONS),
          f"几何邻近半径与通道限制登记必须一致（{build['proximity_pt']}）")
    schema_problems = TS.self_check()["problems"]
    check(schema_problems == [], f"schema 自检必须无问题：{schema_problems}")
    tc_problems = TC.self_check()["problems"]
    check(tc_problems == [],
          f"分类/单元 profile 资产自检必须无问题：{tc_problems}")


# ---------------------------------------------------------------------------
# 分组执行
# ---------------------------------------------------------------------------

def _run_group(name, fn, ctx):
    started = time.time()
    try:
        fn(ctx)
    except Exception as exc:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 分组异常：{type(exc).__name__}: {exc}")
    _results["details"].append(f"__{name}__ {time.time() - started:.1f}s")


def main() -> dict:
    started = time.time()
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _db_identity(_DB_PATH) if _DB_PATH.exists() else None
    original_extract = _install_geometry_memo()
    try:
        _estore._db_path = _DB_PATH
        ctx = _live()
        _run_group("G1", _test_g1, ctx)
        _run_group("G2", _test_g2, ctx)
        _run_group("G3", _test_g3, ctx)
        _run_group("G4", _test_g4, ctx)
        _run_group("G5", _test_g5, ctx)
        _run_group("G6", _test_g6, ctx)
        _run_group("G7", _test_g7, ctx)
        _run_group("G8", _test_g8, ctx)
        _run_group("G9", _test_g9, ctx)
        _run_group("SC", lambda _c: _test_self_check(), ctx)
    finally:
        TG.extract_table_geometry = original_extract
        _estore._db_path = previous_db_path
        after = _db_identity(_DB_PATH) if _DB_PATH.exists() else None
        check(before is not None and before == after,
              "只读前置：`data/evidence.db` 在测试前后必须逐项不变"
              f"（size/mtime_ns/sha256）：{before} vs {after}")
    _results["seconds"] = round(time.time() - started, 1)
    _results["failures"] = [d for d in _results["details"]
                            if d.startswith("FAIL")]
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

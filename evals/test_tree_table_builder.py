# -*- coding: utf-8 -*-
"""TS5 §19.12.3（cell 与网格 1–7）与 §19.12.2A（component 终态映射 1–2）。

覆盖：

- **G1** §19.12.3-1：简单 grid / key-value form / headerless partial 的行角色、结构类型、
  表头缺省原因、缺口字段与完成状态（含"无骨架 ⇒ 行/列边界不确定"）；
- **G2** §19.12.3-2：空 cell、换行/折行 cell、多段 cell、列表、cell 内小标题——cell
  文本必须由**真实 LayoutSpan 片段逐字符重构**（本模块独立复算，不读自报值）；
- **G3** §19.12.3-3：rowspan / colspan / 二者组合 / 多层物理 header；
- **G4** §19.12.3-4：网格空洞、重叠消费、bbox 越界、来源片段缺失/重复/跨 cell；
- **G5** §19.12.3-5：cell text 与来源不一致、来源序错、char range 非法；
- **G6** §19.12.3-6：第一条数据行不得误作 header；subtotal 与 total 分开，且 TS5 **不**
  凭空声称会计小计 / 合计；
- **G7** §19.12.3-7：改企业名/比例/单位/表头/合计/任一 fragment ⇒ 身份必须变化，或
  （自报身份与重算身份不一致时）被拒；
- **G8** §二（表题资格必须由**连续文本游程**证明）：含多个列片段的单一物理行、折行
  正文的一行、占据多条真实行的冻结范围都不得被当作表题；单条连续且孤立的标题段是
  正例，且换掉全部文字后结论不变（判据不读业务文字）；
- **G9** §四（视觉对象分类不得按"有没有文字"二分）：带文字但只有图元拓扑且不是整页
  回退框 ⇒ `visual_object_not_table`；图元拓扑与整页回退框重合 ⇒ `unresolved_geometry`
  （不猜）；有真实表状网格时网格优先；无文字无网格无拓扑仍是视觉对象；只有文字时是
  `unsupported_table_structure`；候选拒绝理由经生产入口必须落到同一个分类器；
- **A1** §19.12.2A-1：`FinalComponentBinding.component_id` 与 verified TS4 components
  **精确等集**、每个只出现一次；
- **A2** §19.12.2A-2：table dispositions 的 decision→admission/target 真值表逐项成立，
  含"裁决说被吸收但没有任何真实消费"必须 pending 而不得凭裁决绑定；
- **A3** §19.5.1（`tb-4`）：一串连续相邻冻结表范围必须另出一个并集候选并**只凭并集
  本身**准入（九条正反例，含"候选矩形比成员并集多出面积"必须拒绝）；
- **A4** §0.19（乙，`tb-6`）：一张物理表对应**一个有界候选**——`_contained_frozen_run`
  六条判据逐条正反例（整表含连续两段范围 ⇒ 准入；有范围只落进来一部分 ⇒ 拒绝；
  被包含集合跨两条串 ⇒ 拒绝；只包含一段 ⇒ 乙判否而既有单 owner 裁定照旧；
  无 `grid_cells` ⇒ 拒绝；多出来的部分碰到同页冻结 `regular` ⇒ 拒绝；同页范围不可
  回查 ⇒ 拒绝），`_union_extends` 判据 ③ 另钉"两端同为 `None` 不构成同节点证据"
  （同页相邻两张表因此保持分离），并在真实现场确认乙确实准入、单 owner 路径仍在、
  且同页两张已准入表**零重叠**（跨表格值不混入）。

**只读与性能**：真实链只跑**一次**并缓存（`_live`），且只走到
`_read_roots → _build_context → build_tables → _build_final_spans → _build_decisions
→ _measure_consumption → _build_bindings`（不建 final 快照、不跑守恒/verifier）。
`TG.extract_table_geometry` 在本进程内按 `(layout capability, pages)` 记忆化：它是对
同一 capability 的纯函数，被测代码读到的仍是同一份报告，断言强度不变。

**合成夹具**：反例形状（空洞、重叠消费、越界、非法 char range、多层 header、合并锚点）
在现场文档里不一定存在，因此用合成版式与合成 cell/row 构造，但**只**经生产类型与生产
函数进入（`TableCellV4.create` / `TableRowV4` / `assign_rows` / `determine_structure_kind`
/ `derive_cell_grid` / `evaluate_candidate_gate` / `build_tables` / `_table_refs` /
`component_partition_problems` / `_binding_for`），没有旁路入口。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections import Counter
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

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": [],
            "failures": []}


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

_LIVE: dict = {}
_PROFILES = None
_SEP = TS.CELL_BLOCK_SEPARATOR


def _db_identity(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _install_geometry_memo():
    """把 `TG.extract_table_geometry` 换成本进程内的记忆化包装；返回原函数。"""
    original = TG.extract_table_geometry
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
    """跑一次真实链路到 cell / 行 / binding 层并缓存（**不**建 final 快照）。"""
    if "bindings" in _LIVE or "error" in _LIVE:
        return _LIVE
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
        built = TB.build_tables(ctx)
        span_build = FMB._build_final_spans(root)
        decisions = FMB._build_decisions(root, ctx, built, span_build.spans)
        consumption = FMB._measure_consumption(ctx, tuple(built.tables))
        bindings = FMB._build_bindings(
            root, consumption, decisions, span_build.span_of_component)
        _LIVE.update({
            "verified": verified, "root": root, "ctx": ctx, "built": built,
            "tables": tuple(bt.table for bt in built.tables),
            "grids": tuple(bt.grid for bt in built.tables),
            "decisions": decisions, "consumption": consumption,
            "bindings": bindings, "final_spans": span_build.spans,
            "span_of_component": span_build.span_of_component,
            "profiles": ctx.profiles, "handoff": handoff})
    except Exception as error:  # noqa: BLE001
        _LIVE["error"] = f"{type(error).__name__}: {error}"
    return _LIVE


def _gate(live, msg="真实链路必须成立"):
    if live.get("error"):
        check(False, f"{msg} —— 现场链路失败：{live['error']}")
        return False
    if "bindings" not in live:
        check(False, f"{msg} —— 现场链路未产出构建结果")
        return False
    return True


# ---------------------------------------------------------------------------
# 1. 合成版式夹具（内部自洽；只读，不接触仓库资产）
# ---------------------------------------------------------------------------

_PAGE_W = 600.0
_PAGE_H = 800.0
_GRID_XS = ((20.0, 50.0), (60.0, 90.0))
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
        self.bbox = (min(s.bbox[0] for s in spans), min(s.bbox[1] for s in spans),
                     max(s.bbox[2] for s in spans), max(s.bbox[3] for s in spans))
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
    """两列真实行：一行两个片段；`missing` 内的位置被挖空。"""
    lines = []
    for i, top in enumerate(tops):
        spans = [_Span(x0, top, x1, top + _ROW_H,
                       text=(texts or {}).get((i, j), f"C{i}{j}"))
                 for j, (x0, x1) in enumerate(_GRID_XS) if (i, j) not in missing]
        if spans:
            lines.append(_Line(index_base + i, spans))
    return lines


def _grid_page(page_number=1, *, tops=(20.0, 33.0, 46.0, 59.0), missing=(),
               texts=None):
    """两列规则行的合成页；行带路径可闭合为 2 列多行网格。"""
    return _Page(page_number,
                 _two_col_rows(tops=tops, missing=missing, texts=texts))


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

    def frame(self, page_number=1):
        return self.frames[page_number]

    def cand(self, *, page_number=1, bbox, source="pdfplumber_lines",
             strategy="lines", closed=False, gaps=1, edges=0, intersections=0,
             grid_cells=(), row_count=0, column_count=0, cell_count=0,
             merged=()):
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


def _owning_site(page, *, range_kind="table_inside", node_id="n1"):
    """一页 + 一个覆盖整页行范围的冻结表范围。"""
    site = _Site([page], nodes=[_Node(node_id)])
    d = _Disposition("d-1", range_kind, node_id, page.page_number,
                     page.page_number, 0, len(page.lines) - 1)
    return site, d, site.context(dispositions=[d]), _union(page.lines)


def _flag_candidate(site, disposition, bbox, source, **kw):
    """按通道构造生产类型的候选（`geometry` 与 `candidate_source` 同步）。"""
    strategy = kw.pop("strategy", "lines")
    return TB.BuilderCandidate(
        candidate_source=source, strategy=strategy, page_number=1, bbox=bbox,
        geometry=site.cand(bbox=bbox, source=source, strategy=strategy, **kw),
        frozen_disposition_id=disposition.disposition_id,
        frozen_disposition_locator=disposition.disposition_locator,
        frozen_overlap_ratio=1.0, competing_disposition_ids=())


def _skeleton(bbox, rows=2, cols=2):
    """把矩形均分为 rows×cols 的真实网格骨架（`grid_cells`）。"""
    x0, y0, x1, y1 = bbox
    xs = [x0 + (x1 - x0) * i / cols for i in range(cols + 1)]
    ys = [y0 + (y1 - y0) * i / rows for i in range(rows + 1)]
    return tuple((r, c, 1, 1, (quantize(xs[c]), quantize(ys[r]),
                               quantize(xs[c + 1]), quantize(ys[r + 1])))
                 for r in range(rows) for c in range(cols))


# ---------------------------------------------------------------------------
# 2. 合成 cell / 行 / 网格夹具（只经生产类型构造）
# ---------------------------------------------------------------------------

#: 合成来源一律用登记的对齐 schema 版本（不得自造版本字符串）。
_ALS = V.ALIGN_SCHEMA_VERSION


def _ref(comp, *, page=1, line=0, span=0, c0=0, c1=5, ev0=0,
         kind="alignment", verdict=None):
    """一段真实形状的 cell 来源（真实 `TableCellSourceRef.create` 派生身份）。"""
    if verdict is None:
        verdict = "refused" if kind == "refusal" else "aligned"
    rejected = (kind == "refusal") or (verdict == "refused")
    interval = TS.TableSourceInterval(
        page_number=page, line_index=line, span_index=span,
        span_char_range=(c0, c1),
        bbox=(20.0, 20.0 + line * 10.0, 90.0, 28.0 + line * 10.0))
    return TS.TableCellSourceRef.create(
        interval=interval, component_id=comp, component_locator="loc-" + comp,
        evidence_block_id="evb-" + comp,
        evidence_char_range=(ev0, ev0 + (c1 - c0)),
        terminal_kind=kind,
        terminal_id=("rf-" if kind == "refusal" else "al-") + comp,
        terminal_locator="loc-t-" + comp, terminal_schema_version=_ALS,
        verdict=verdict,
        refusal_reason=("recorded_refusal" if rejected else None))


def _blk(idx, role, text, refs):
    return TS.TableCellBlock.create(
        block_index=idx, role=role, text=text,
        source_ref_ids=tuple(r.source_ref_id for r in refs))


def _cell(row, col, parts, *, rowspan=1, colspan=1, bbox=None):
    """`parts = [(role, text, ref), ...]` 逐块给出文本；每块恰消费一个片段。"""
    blocks = tuple(_blk(i, role, text, (ref,))
                   for i, (role, text, ref) in enumerate(parts))
    refs = tuple(ref for (_r, _t, ref) in parts)
    return TS.TableCellV4.create(
        row=row, column=col, rowspan=rowspan, colspan=colspan,
        bbox=bbox or (20.0 + col * 40.0, 20.0 + row * 10.0,
                      60.0 + col * 40.0, 28.0 + row * 10.0),
        blocks=blocks, source_refs=refs)


def _plain_cell(row, col, text, *, role="line", ev0=None):
    """单块 cell：文本长度与它唯一的片段区间长度一致（证据链自洽）。"""
    ref = _ref(f"sc-{row}-{col}", line=row, span=col, c0=0, c1=len(text),
               ev0=(100 * row + col if ev0 is None else ev0))
    return _cell(row, col, [(role, text, ref)])


def _row(idx, role, cells, *, repeated=False, label=None):
    return TS.TableRowV4(row_index=idx, role=role, label=label,
                         is_repeated_header=repeated, cells=tuple(cells))


def _grid(*, page=1, rows=2, cols=2, bbox=(20.0, 20.0, 100.0, 60.0),
          geometry_backed=False, cells=(), problems=(), straddling=()):
    """合成 `CellGridPlan`（真实类型；驱动结构类型与缺口真值表）。"""
    return TG.CellGridPlan(
        page_number=page, bbox=tuple(bbox), row_count=rows, column_count=cols,
        cells=tuple(cells), assigned_lines=tuple(range(rows)),
        straddling_lines=tuple(straddling), geometry_backed=bool(geometry_backed),
        problems=tuple(problems))


def _adorn(**kw):
    """空的（或按参数注入的）表级装饰块集合。"""
    base = dict(title_blocks=(), unit_blocks=(), unit_text=None, note_blocks=(),
                title_disposition_ids=(), unit_disposition_ids=(),
                note_disposition_ids=(), source_refs=())
    base.update(kw)
    return TB.TableAdornments(**base)


# ---------------------------------------------------------------------------
# 3. 真实表的身份手术（改字段后必须重算派生身份）
# ---------------------------------------------------------------------------

#: 表对象的**派生**字段（由 `create()` 自算，不得由调用方提供）。
_TABLE_DERIVED = ("table_locator", "table_id", "content_fingerprint",
                  "structure_fingerprint", "provenance_fingerprint")


def _recreate_table(table, **override):
    """用**非派生**字段重跑 `create()`：定位/三指纹/身份因此全部自洽。"""
    payload = {f.name: getattr(table, f.name)
               for f in dataclasses.fields(table) if f.name not in _TABLE_DERIVED}
    payload.update(override)
    return TS.TableObjectV4.create(**payload)


def _retitle_last_block(cell, new_text):
    """改掉 cell 最后一个块的文本（结构与来源不变；文本随之重算）。"""
    blocks = list(cell.blocks)
    last = blocks[-1]
    blocks[-1] = TS.TableCellBlock.create(
        block_index=last.block_index, role=last.role, text=new_text,
        source_ref_ids=last.source_ref_ids)
    return dataclasses.replace(
        cell, blocks=tuple(blocks),
        text=_SEP.join(b.text for b in blocks))


def _with_cell(row, cell):
    return dataclasses.replace(
        row, cells=tuple(cell if c.column == cell.column else c
                         for c in row.cells))


def _with_row(table, row):
    return _recreate_table(
        table, rows=tuple(row if r.row_index == row.row_index else r
                          for r in table.rows))


def _swap_ref_in_table(table):
    """把某个"单块单来源"cell 的片段区间整体延长 1 字符（身份必须随之变化）。

    返回 `(old_ref, new_ref, new_table)`；找不到这样的 cell 时返回 `None`。
    """
    for row in table.rows:
        for cell in row.cells:
            if len(cell.blocks) != 1 or len(cell.source_refs) != 1:
                continue
            old = cell.source_refs[0]
            a, b = old.interval.span_char_range
            new_interval = TS.TableSourceInterval(
                page_number=old.interval.page_number,
                line_index=old.interval.line_index,
                span_index=old.interval.span_index,
                span_char_range=(a, b + 1), bbox=old.interval.bbox)
            new_ref = TS.TableCellSourceRef.create(
                interval=new_interval, component_id=old.component_id,
                component_locator=old.component_locator,
                evidence_block_id=old.evidence_block_id,
                evidence_char_range=(old.evidence_char_range[0],
                                     old.evidence_char_range[1] + 1),
                terminal_kind=old.terminal_kind, terminal_id=old.terminal_id,
                terminal_locator=old.terminal_locator,
                terminal_schema_version=old.terminal_schema_version,
                verdict=old.verdict, refusal_reason=old.refusal_reason)
            new_block = TS.TableCellBlock.create(
                block_index=cell.blocks[0].block_index,
                role=cell.blocks[0].role, text=cell.blocks[0].text,
                source_ref_ids=(new_ref.source_ref_id,))
            new_cell = TS.TableCellV4.create(
                row=cell.row, column=cell.column, rowspan=cell.rowspan,
                colspan=cell.colspan, bbox=cell.bbox, blocks=(new_block,),
                source_refs=(new_ref,))
            new_rows = tuple(_with_cell(r, new_cell)
                             if r.row_index == row.row_index else r
                             for r in table.rows)
            new_refs = tuple(new_ref if r.source_ref_id == old.source_ref_id
                             else r for r in table.source_refs)
            return old, new_ref, _recreate_table(
                table, rows=new_rows, source_refs=new_refs)
    return None


# ---------------------------------------------------------------------------
# G1 §19.12.3-1：简单 grid / key-value form / headerless partial
# ---------------------------------------------------------------------------

def _test_g1(live):
    h_cells = [_plain_cell(0, 0, "项目", role="heading"),
               _plain_cell(0, 1, "金额", role="heading")]
    mixed = [h_cells[0], _plain_cell(0, 1, "金额")]
    check(TB._row_is_header(h_cells) and not TB._row_is_header(mixed)
          and not TB._row_is_header([]),
          "G1 表头判据必须是'该行每个 cell 都有结构性 heading 块'："
          "一行里只有一个 heading 不算表头，空行也不算")
    data = [_plain_cell(1, 0, "营业收入"), _plain_cell(1, 1, "100")]
    rows = TB.assign_rows({0: h_cells, 1: data,
                           2: [_plain_cell(2, 0, "营业成本"),
                               _plain_cell(2, 1, "80")]})
    check([r.role for r in rows] == ["header", "body", "body"]
          and [r.row_index for r in rows] == [0, 1, 2],
          f"G1 表头只作为连续前缀（{[r.role for r in rows]}）")
    check(all(r.is_repeated_header is False and r.label is None
              for r in rows),
          "G1 `assign_rows` 不得自行声称重复表头或行标签（没有真实分节证据）")
    check([r.role for r in TB.assign_rows({0: data, 1: data})] == ["body", "body"],
          "G1 无 heading 证据时第一行也不得成为表头")

    grid2 = _grid(rows=3, cols=2, geometry_backed=True)
    check(TB.determine_structure_kind(rows=rows, geometry_backed=True,
                                      grid=grid2) == ("headered_grid", None),
          "G1 表头 + 真实骨架 + >=2 列 ⇒ headered_grid")
    kv_rows = TB.assign_rows({i: [_plain_cell(i, 0, f"项目{i}"),
                                  _plain_cell(i, 1, "100")] for i in range(3)})
    check(all(r.role == "body" for r in kv_rows),
          "G1 无 heading 块时不得有任何 header 行")
    check(TB.determine_structure_kind(
              rows=kv_rows, geometry_backed=False, grid=_grid(rows=3, cols=2))
          == ("key_value_form", "key_value_form_without_header"),
          "G1 无表头 + >=2 行 + >=2 列 + 首列短标签 ⇒ key_value_form")
    long_rows = TB.assign_rows({i: [_plain_cell(i, 0, "说明" * 30),
                                    _plain_cell(i, 1, "100")] for i in range(3)})
    check(TB.determine_structure_kind(
              rows=long_rows, geometry_backed=False, grid=_grid(rows=3, cols=2))
          == ("headerless_grid", "no_header_evidence"),
          "G1 首列不是短标签 ⇒ headerless_grid / no_header_evidence")
    check(TB._looks_like_key_value(long_rows) is False
          and TB._looks_like_key_value(kv_rows) is True
          and TB._looks_like_key_value(kv_rows[:1]) is False,
          "G1 key-value 判据必须读**真实首列文本形状**且至少两行（与语言无关）")
    check(TB.determine_structure_kind(
              rows=kv_rows[:1], geometry_backed=False, grid=_grid(rows=1, cols=2))
          == ("headerless_grid", "no_header_evidence"),
          "G1 只有一行时不得判成 key_value_form")
    check(TB.determine_structure_kind(
              rows=rows, geometry_backed=False, grid=_grid(rows=3, cols=1))
          == ("headerless_grid", "no_header_evidence"),
          "G1 只有一列时不得判成 headered_grid")
    check(TB.determine_structure_kind(
              rows=rows, geometry_backed=False, grid=grid2)
          == ("headerless_grid", "no_header_evidence"),
          "G1 有表头但没有真实骨架时结构类型降级为 headerless_grid"
          "（并因此给出 no_header_evidence —— 与该表确实有 header 行的事实并存，"
          "见最终报告的说明）")

    partial = TB._missing_fields(long_rows, "headerless_grid",
                                 "ordinary_business_table",
                                 _grid(rows=3, cols=2), (),
                                 title_block_count=1)
    check(set(partial) == {"header", "row_boundary", "column_boundary"},
          f"G1 headerless 且无骨架的缺口码必须恰为 header/row/column（{partial}）")
    check(TB._missing_fields(rows, "headered_grid", "ordinary_business_table",
                             grid2, ((1, 0),), title_block_count=1)
          == ("cell_interior",),
          "G1 有未闭合片段时缺口必须是 cell_interior")
    check(set(TB._missing_fields(rows, "headered_grid",
                                 "ordinary_business_table",
                                 _grid(rows=3, cols=2, geometry_backed=False),
                                 (), title_block_count=1))
          == {"row_boundary", "column_boundary"},
          "G1 无真实骨架时必须登记 row/column boundary 不确定")
    check(set(TB._missing_fields(rows, "headered_grid",
                                 "financial_main_statement", grid2, (),
                                 title_block_count=1))
          == {"subtotal", "total", "column_semantics"},
          "G1 财务主表必须**显式**声称会计分节/列语义未证明")
    check(set(TB._missing_fields(rows, "headered_grid",
                                 "ordinary_business_table", grid2, (),
                                 title_block_count=1)) == set(),
          "G1 普通业务表不得被追加上会计分节缺口")
    # §19.5.2 表题只能来自**被验证的**表题/前导区域：没有验证过的表题块就必须
    # 如实登记 title 缺口，不得默默当作有表题。
    check(TB._missing_fields(rows, "headered_grid", "ordinary_business_table",
                             grid2, (), title_block_count=0) == ("title",),
          "G1 没有已验证表题块时必须登记 title 缺口")
    check(TB._missing_fields(
              rows, "headered_grid", "ordinary_business_table",
              _grid(rows=3, cols=2, geometry_backed=True,
                    problems=("unexplained_hole",)), (),
              title_block_count=1) == ("cell_interior",)
          and TB._missing_fields(
              rows, "headered_grid", "ordinary_business_table",
              _grid(rows=3, cols=2, geometry_backed=True, straddling=(7,)), (),
              title_block_count=1) == ("row_boundary",),
          "G1 网格闭合性问题（未解释空位/跨边界行）必须落成在册缺口")
    check(set(TS.MISSING_FIELD_CODES) >= {"header", "subtotal", "total",
                                          "column_semantics", "row_boundary",
                                          "column_boundary", "cell_interior"},
          "G1 缺口字段码必须是封闭登记表")

    clean = _grid(rows=3, cols=2, geometry_backed=True)
    check(TB._structure_state("headerless_grid", (), clean) ==
          ("partial", "header_not_proven")
          and TB._structure_state("headerless_grid", ("header",), clean) ==
          ("partial", "header_not_proven"),
          "G1 headerless_grid 永远是 partial/header_not_proven（无表头证据）")
    check(TB._structure_state("headered_grid", ("cell_interior", "total"),
                              clean) == ("partial", "cell_provenance_incomplete"),
          "G1 cell_interior 的优先级高于会计分节（不得把来源不完整说成会计未证明）")
    check(TB._structure_state("headered_grid", ("row_boundary",), clean) ==
          ("partial", "accounting_sections_not_proven")
          and TB._structure_state("headered_grid", (), clean)
          == ("complete", None),
          "G1 有缺口必 partial、无缺口才 complete")
    # §19.6.3：完成状态还必须回答"正文是否闭合"，它只能由**真实重建结果**决定：
    # 行被截断 / 网格留有未解释空位时，缺口清单恰好为空也不得判 complete。
    check(TB._structure_state("headered_grid", (),
                              _grid(rows=3, cols=2, straddling=(7,))) ==
          ("partial", "row_boundary_truncated")
          and TB._structure_state(
              "headered_grid", (),
              _grid(rows=3, cols=2, problems=("unexplained_hole",))) ==
          ("partial", "grid_closure_not_proven"),
          "G1 截断行 / 网格未闭合时不得判 complete")

    if not _gate(live):
        return
    tables, grids = live["tables"], live["grids"]
    check(len(tables) == len(grids) and len(tables) > 0,
          f"G1 现场必须构建出表（{len(tables)} 张）")
    kinds = Counter((t.structure_kind, t.header_absence_reason,
                     t.structure_state, t.structure_state_reason)
                    for t in tables)
    check(set(k[0] for k in kinds) <= set(TS.STRUCTURE_KINDS),
          f"G1 现场结构类型必须登记在册：{sorted({k[0] for k in kinds})}")
    check(all((k[0] == "headered_grid") == (k[1] is None) for k in kinds),
          f"G1 只有 headered_grid 才允许没有 header_absence_reason：{sorted(kinds)}")
    check(all(k[1] is None or k[1] in TS.HEADER_ABSENCE_REASONS for k in kinds),
          "G1 header_absence_reason 必须属于封闭登记表")
    bad_state = []
    for t, g in zip(tables, grids):
        missing = tuple(t.missing_or_uncertain_fields)
        want = TB._structure_state(t.structure_kind, missing, g)
        if (t.structure_state, t.structure_state_reason) != want:
            bad_state.append((t.page_number, t.structure_state,
                              t.structure_state_reason, want))
        if set(missing) - set(TS.MISSING_FIELD_CODES):
            bad_state.append((t.page_number, "unregistered", missing))
    check(not bad_state,
          f"G1 现场每张表的 (structure_state, reason) 必须由自己的缺口单点决定"
          f"（反例 {bad_state[:2]}）")
    check(all(t.column_count == g.column_count and len(t.rows) == g.row_count
              and g.closed for t, g in zip(tables, grids)),
          "G1 现场每张表的行列数必须等于它自己已闭合的网格")
    check(all(not any(r.role == "header" for r in t.rows) for t in tables)
          and all("header" in t.missing_or_uncertain_fields for t in tables),
          f"G1 现场文档没有任何可证成的表头行，因此每张表都必须显式登记 header 缺口"
          f"（{len(tables)} 张表的类型分布 "
          f"{sorted(set(t.structure_kind for t in tables))}）")


# ---------------------------------------------------------------------------
# G2 §19.12.3-2：空 cell / 换行折行 / 多段 / 列表 / cell 内小标题
# ---------------------------------------------------------------------------

def _block_signals(**kw):
    """按**显式信号**求值角色真值表（profile 只提供"什么算列表标记/句末标点"）。"""
    base = dict(text="", line_group_count=1, own_line_group=True,
                followed_by_other_block_in_same_cell=False, size_ratio=None,
                bold_ratio=None, unclosed_inputs=False,
                profile=_profiles().cell_block)
    base.update(kw)
    return TC.evaluate_cell_block_signals(**base)


def _role(**kw):
    bundle = _profiles().cell_block
    return TC.classify_cell_block_role(bundle, _block_signals(**kw))


def _recomputed_block_text(ctx, refs):
    """**独立**由真实 LayoutSpan 复算块文本（不读 `block.text`）。"""
    parts: list = []
    prev = None
    for ref in refs:
        span = ctx.layout_span(ref.interval.page_number, ref.interval.line_index,
                               ref.interval.span_index)
        if span is None:
            return None
        key = (ref.interval.page_number, ref.interval.line_index)
        if prev is not None and key != prev:
            parts.append(_SEP)
        a, b = ref.interval.span_char_range
        if not (0 <= a < b <= len(span.text)):
            return None
        parts.append(span.text[a:b])
        prev = key
    return "".join(parts)


def _test_g2(live):
    # 角色真值表：编号/列表标记 ⇒ list_item；多层条件不足 ⇒ 更弱角色。
    check(_role(text="1、营业收入")[0] == "list_item"
          and _role(text="- 列表项")[0] == "list_item"
          and _role(text="（1）其他")[0] == "list_item",
          "G2 编号前缀/列表标记必须判为 list_item（只读标记，不读业务含义）")
    check(_role(text="主要产品", size_ratio=2.0,
                followed_by_other_block_in_same_cell=True) ==
          ("heading", "rule_heading"),
          "G2 字号比值达标且后随同 cell 其他块时必须判为 heading"
          f"（得到 {_role(text='主要产品', size_ratio=2.0, followed_by_other_block_in_same_cell=True)}）")
    check(_role(text="主要产品", size_ratio=2.0)[0] != "heading",
          "G2 单独一块（后面没有同 cell 其他块）不得判为 heading："
          "heading 只存在于多块 cell 内（这是现场没有任何 heading 块的原因）")
    check(_role(text="公司主营业务收入构成情况如下。", line_group_count=2) ==
          ("paragraph", "rule_paragraph"),
          "G2 多行组且带句末标点必须判为 paragraph")
    check(_role(text="公司主营业务构成如下", line_group_count=2)[0] != "paragraph",
          "G2 多行组但既无句末标点也无折行续写时不得判为 paragraph"
          "（`wrap_continuation` 恒为 False ⇒ 不得靠猜升级角色）")
    check(_role(text="逐行短标签") == ("line", "rule_line"),
          "G2 单行组且无标记必须判为 line")
    check(_role(text="任意文字", unclosed_inputs=True) ==
          ("unclassified", "unclosed_inputs"),
          "G2 输入不闭合时不得靠猜角色提高命中率")
    check(_role(text="完全无信号的长文字" * 3,
                line_group_count=1)[0] in TS.CELL_BLOCK_ROLES,
          "G2 未命中任何规则的块必须由兜底规则接住且角色登记在册")
    orders = [r.order for r in _profiles().cell_block.rules]
    check(len(set(orders)) == len(orders),
          "G2 profile 规则 order 必须唯一，否则 fail-closed 冲突分支无法区分"
          f"（{orders}）")

    # 空 cell：TS5 没有"空 cell"表示——空白片段既不进 cell，也不是缺口。
    check(TB._fragment_has_content(_Span(20.0, 20.0, 90.0, 30.0, text="x"))
          and not TB._fragment_has_content(_Span(20.0, 20.0, 90.0, 30.0,
                                                 text="   "))
          and not TB._fragment_has_content(_Span(20.0, 20.0, 90.0, 30.0,
                                                 text="　")),
          "G2 纯空白片段（含全角空格）不得进入 cell，也不构成缺口")

    # 多段 / 折行 cell：块内同 (页,行) 直接相接，跨行插一个冻结分隔符。
    r_a = _ref("sc-m1", line=0, span=0, c0=0, c1=4)
    r_b = _ref("sc-m2", line=0, span=0, c0=4, c1=8)
    r_c = _ref("sc-m3", line=1, span=0, c0=0, c1=6)
    wrapped = _cell(0, 0, [("paragraph", "AAAABBBB", r_a)],
                    bbox=(20.0, 20.0, 60.0, 28.0))
    check(wrapped.source_refs == (r_a,) and wrapped.text == "AAAABBBB",
          "G2 折行 cell 的块文本必须逐字对齐来源片段（不得凭空插字）")
    multi = TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAABBBB", (r_a, r_b)),
                _blk(1, "line", "CCCCCC", (r_c,))),
        source_refs=(r_a, r_b, r_c))
    check(multi.text == "AAAABBBB" + _SEP + "CCCCCC" and len(multi.blocks) == 2,
          f"G2 多段 cell 的文本必须是 (块内相接 + 块间分隔符) 的重构结果"
          f"（得到 {multi.text!r}）")
    check(len(multi.text) == sum(
        x.interval.span_char_range[1] - x.interval.span_char_range[0]
        for x in (r_a, r_b, r_c)) + 1,
          "G2 多段 cell 文本长度必须等于 Σ片段长度 + 跨行分隔符数（可独立复算）")
    reordered = TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "BBBBA", (r_b, r_a)),),
        source_refs=(r_a, r_b))
    check(reordered.text == "BBBBA"
          and reordered.blocks[0].source_ref_ids ==
          (r_b.source_ref_id, r_a.source_ref_id),
          "G2 schema **不**重排块内片段（块文本逐块拼接，读序即块序）；"
          "顺序的唯一守卫在 cell 级 `source_refs` 上")
    raises(lambda: TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAABBBB", (r_a, r_b)),),
        source_refs=(r_b, r_a)),
        Exception, "升序",
        "G2 cell 级来源乱序必须被拒绝（唯一顺序守卫，同一规则见 G5）")
    gap_limit = int(_profiles().cell_block.line_group_rule.max_intra_group_line_gap)
    check(TB._same_group(r_a, r_c, 1) and not TB._same_group(r_a, r_c, 0)
          and not TB._same_group(r_a, _ref("sc-m4", page=2, line=1), gap_limit),
          f"G2 行游程分组只允许同页且行号差不超过 profile 阈值"
          f"（gap_limit={gap_limit}）")

    if not _gate(live):
        return
    ctx = live["ctx"]
    cells = [c for t in live["tables"] for r in t.rows for c in r.cells]
    check(bool(cells), f"G2 现场必须有 cell（{len(cells)} 个）")
    mismatched: list = []
    for cell in cells:
        by_id = {r.source_ref_id: r for r in cell.source_refs}
        block_texts = []
        broken = False
        for blk in cell.blocks:
            refs = [by_id.get(i) for i in blk.source_ref_ids]
            if any(r is None for r in refs):
                mismatched.append((cell.row, cell.column, "unknown_ref"))
                broken = True
                break
            text = _recomputed_block_text(ctx, refs)
            if text != blk.text:
                mismatched.append((cell.row, cell.column, blk.block_index))
                broken = True
                break
            block_texts.append(text)
        if broken:
            continue
        if _SEP.join(block_texts) != cell.text:
            mismatched.append((cell.row, cell.column, "cell_text"))
            continue
        changes = 0
        prev = None
        total = 0
        ok = True
        for ref in cell.source_refs:
            span = ctx.layout_span(ref.interval.page_number,
                                   ref.interval.line_index,
                                   ref.interval.span_index)
            key = (ref.interval.page_number, ref.interval.line_index)
            if prev is not None and key != prev:
                changes += 1
            prev = key
            a, b = ref.interval.span_char_range
            total += (b - a)
            if span is None or not (0 <= a < b <= len(span.text)):
                mismatched.append((cell.row, cell.column, "range"))
                ok = False
                break
        if ok and len(cell.text) != total + changes:
            mismatched.append((cell.row, cell.column, "length_identity"))
    check(not mismatched,
          f"G2 现场每个 cell 的文本都必须能由真实 LayoutSpan 片段逐字符复算，"
          f"且满足长度恒等式（不符 {mismatched[:4]} / {len(cells)} 个 cell）")
    check(all(b.source_ref_ids for c in cells for b in c.blocks)
          and all(c.blocks or c.unproven_fragments for c in cells),
          "G2 每个 cell 要么有至少一个块（且每块都有真实来源），要么逐条声明 "
          "unproven_fragments（`tc-3` 的 typed 缺口；两者皆无的静默空 cell 不可产出）")
    gap_cells = [c for c in cells if not c.blocks]
    check(all(c.text == "" and c.unproven_fragments for c in gap_cells),
          f"G2 缺口 cell 必须是空文本且缺口非空（{len(gap_cells)} 个缺口 cell）")
    check(all(t.structure_state != "complete"
              for t in live["tables"]
              if any(not c.blocks or c.unproven_fragments
                     for r in t.rows for c in r.cells)),
          "G2 只要有一个 cell 带缺口，该表就**不得**是 complete（fail-closed 不因 "
          "允许带缺口产出而失效）")
    check(all(tuple(b.block_index for b in c.blocks) ==
              tuple(range(len(c.blocks))) for c in cells),
          "G2 cell 内部块必须按真实行游程有序编号")
    check(all(set(r.source_ref_id for r in c.source_refs) ==
              set(i for b in c.blocks for i in b.source_ref_ids)
              and len(set(i for b in c.blocks for i in b.source_ref_ids)) ==
              len(c.source_refs) for c in cells),
          "G2 现场每个 cell 的块分区与来源片段必须双射（每个片段恰被一个块消费）")
    bad_group: list = []
    for cell in cells:
        by_id = {r.source_ref_id: r for r in cell.source_refs}
        for blk in cell.blocks:
            refs = [by_id[i] for i in blk.source_ref_ids]
            for x, y in zip(refs, refs[1:]):
                if not TB._same_group(x, y, gap_limit):
                    bad_group.append((cell.row, cell.column, blk.block_index))
    check(not bad_group,
          f"G2 现场 cell 内部块的真实行游程必须连续且同页（反例 {bad_group[:3]}）")


# ---------------------------------------------------------------------------
# G3 §19.12.3-3：rowspan / colspan / 组合 / 多层物理 header
# ---------------------------------------------------------------------------

#: 骨架/合并反例的候选矩形：必须同时包含全部骨架锚点（构造期校验）。
_SPAN_BBOX = (20.0, 20.0, 100.0, 60.0)


def _spanning_page(page_number=1):
    """两块纵向合并：左列跨两行，右列上下各一格。"""
    return _Page(page_number, [
        _Line(0, [_Span(20.0, 22.0, 55.0, 38.0), _Span(62.0, 22.0, 98.0, 38.0)]),
        _Line(1, [_Span(20.0, 42.0, 55.0, 58.0), _Span(62.0, 42.0, 98.0, 58.0)]),
    ])


def _span_skeleton():
    """2×2 网格骨架：`(0,0)` 是 rowspan=2 的合并锚点。"""
    return ((0, 0, 2, 1, (20.0, 20.0, 60.0, 60.0)),
            (0, 1, 1, 1, (60.0, 20.0, 100.0, 40.0)),
            (1, 1, 1, 1, (60.0, 40.0, 100.0, 60.0)))


def _test_g3(live):
    site, d, s_ctx, _bbox = _owning_site(_spanning_page())
    cand = site.cand(bbox=_SPAN_BBOX, closed=True, gaps=0, edges=4,
                     intersections=4, row_count=2, column_count=2, cell_count=3,
                     merged=((0, 0, 2, 1),), grid_cells=_span_skeleton())
    grid = TG.derive_cell_grid(site.layout.pages[0], candidate=cand)
    check(grid.closed and grid.geometry_backed and grid.row_count == 2
          and grid.column_count == 2,
          f"G3 骨架路径的合并网格必须闭合（problems={grid.problems}）")
    spans = {(c.row, c.column): (c.rowspan, c.colspan) for c in grid.cells}
    check(spans == {(0, 0): (2, 1), (0, 1): (1, 1), (1, 1): (1, 1)},
          f"G3 rowspan 必须由真实骨架锚点给出（得到 {spans}）")
    anchor = next(c for c in grid.cells if (c.row, c.column) == (0, 0))
    check(len(anchor.fragment_keys) == 2
          and [k[0] for k in anchor.fragment_keys] == [0, 1],
          f"G3 rowspan cell 必须消费两行的真实片段（{anchor.fragment_keys}）")
    gate = TB.evaluate_candidate_gate(
        s_ctx, _flag_candidate(site, d, _SPAN_BBOX, "frozen_range",
                               closed=True, gaps=0, row_count=2, column_count=2,
                               cell_count=3, merged=((0, 0, 2, 1),),
                               grid_cells=_span_skeleton()))
    check(gate.admitted and gate.grid is not None
          and gate.grid.row_count == 2 and gate.grid.geometry_backed
          and any(c.rowspan > 1 for c in gate.grid.cells),
          f"G3 带合并的骨架候选必须过门且保留合并（reason={gate.reason}）")

    # 组合：rowspan 与 colspan 同时出现在一个锚点上。
    combo = ((0, 0, 2, 2, (20.0, 20.0, 100.0, 60.0)),)
    cand2 = site.cand(bbox=_SPAN_BBOX, closed=True, gaps=0, edges=4,
                      intersections=4, row_count=2, column_count=2,
                      cell_count=1, merged=((0, 0, 2, 2),), grid_cells=combo)
    grid_c2 = TG.derive_cell_grid(site.layout.pages[0], candidate=cand2)
    check(grid_c2.closed and len(grid_c2.cells) == 1
          and (grid_c2.cells[0].rowspan, grid_c2.cells[0].colspan) == (2, 2)
          and len(grid_c2.cells[0].fragment_keys) == 4,
          f"G3 rowspan+colspan 组合必须如实记账并消费 4 个真实片段"
          f"（cells={len(grid_c2.cells)} problems={grid_c2.problems}）")

    # 骨架与 `merged_cells` 不自洽 ⇒ 候选构造期即拒绝（不得靠后续静默修好）。
    raises(lambda: site.cand(bbox=_SPAN_BBOX, closed=True, gaps=0, row_count=2,
                             column_count=2, cell_count=3, merged=(),
                             grid_cells=_span_skeleton()),
           Exception, "merged_cells 必须与网格骨架导出的合并锚点一致",
           "G3 自报 `merged_cells` 与骨架不一致必须在候选构造期被拒绝")
    raises(lambda: site.cand(bbox=_SPAN_BBOX, closed=True, gaps=0, row_count=2,
                             column_count=2, cell_count=2,
                             merged=((0, 0, 2, 1),),
                             grid_cells=_span_skeleton()[:2]),
           Exception, "网格骨架必须铺满",
           "G3 骨架不得留下空洞（候选构造期即拒绝）")
    raises(lambda: site.cand(bbox=(20.0, 40.0, 100.0, 60.0), closed=True, gaps=0,
                             row_count=2, column_count=2, cell_count=1,
                             merged=((0, 0, 2, 2),),
                             grid_cells=((0, 0, 2, 2,
                                          (20.0, 20.0, 100.0, 60.0)),)),
           Exception, "越出候选矩形",
           "G3 骨架锚点越出候选矩形必须被拒绝（不得静默夹取）")

    # 行带路径**绝不**产生 rowspan（没有真实骨架就没有纵向合并的证据）。
    band_cand = site.cand(bbox=_SPAN_BBOX, closed=True, gaps=0)
    band_grid = TG.derive_cell_grid(site.layout.pages[0], candidate=band_cand)
    check(bool(band_grid.cells)
          and all(c.rowspan == 1 and c.colspan == 1 for c in band_grid.cells)
          and band_grid.geometry_backed is False and band_grid.closed,
          f"G3 行带路径不得产生任何 rowspan/colspan，且必须自报 "
          f"geometry_backed=False（{[(c.rowspan, c.colspan) for c in band_grid.cells]}"
          f" problems={band_grid.problems}）")

    # 多层物理 header：两行 header 必须都保留；重复表头只能是 header 行。
    h0 = [_plain_cell(0, 0, "项目", role="heading"),
          _plain_cell(0, 1, "2024年度", role="heading")]
    h1 = [_plain_cell(1, 0, "项目", role="heading"),
          _plain_cell(1, 1, "金额", role="heading")]
    body = [_plain_cell(2, 0, "营业收入"), _plain_cell(2, 1, "100")]
    rows = TB.assign_rows({0: h0, 1: h1, 2: body})
    check([r.role for r in rows] == ["header", "body", "body"],
          f"G3 `assign_rows` 只把**首行**认作表头，因此两层物理表头的第二层降级为 "
          f"body（{[r.role for r in rows]}）——多层物理表头目前无法表示，"
          f"属未覆盖缺口，见最终报告")
    check(TB.determine_structure_kind(
              rows=rows, geometry_backed=True,
              grid=_grid(rows=3, cols=2, geometry_backed=True))
          == ("headered_grid", None),
          "G3 两层表头 + 真实骨架仍是 headered_grid")
    raises(lambda: _row(1, "body", body, repeated=True),
           Exception, "只有 header 行可以是重复表头",
           "G3 非 header 行不得标记为重复表头")
    check(_row(0, "header", h0, repeated=True).is_repeated_header is True,
          "G3 header 行允许被标记为重复表头（只是逻辑展示标记）")

    if not _gate(live):
        return
    tables, grids = live["tables"], live["grids"]
    check(all(t.column_count >= 2 for t in tables),
          "G3 现场每张表都必须至少两列")
    merged_cells = [(t.page_number, c.row, c.column)
                    for t in tables for r in t.rows for c in r.cells
                    if c.rowspan > 1 or c.colspan > 1]
    # `colspan` 有**两条**合法来源：① 骨架网格自带的矩形；② 行带路径里跨列片段，
    # 且该片段跨越的**每条**内部列边界都在本行带之外的其它行带上被共同见证
    # （`_grid_from_bands` 的 `_witness_bands` 证明；反例码是
    # `column_boundary_cuts_fragment`）。`rowspan` 则**只**允许来自骨架——行带路径
    # 声明"没有真实网格就证明不了纵向合并"。
    band_merged = [(t.page_number, c.row, c.column)
                   for t, g in zip(tables, grids) if not g.geometry_backed
                   for r in t.rows for c in r.cells
                   if c.rowspan > 1 or c.colspan > 1]
    check(all(g.geometry_backed for t, g in zip(tables, grids)
              if any(c.rowspan > 1 for r in t.rows for c in r.cells)),
          f"G3 现场 `rowspan>1` 必须只出现在 geometry_backed 的骨架网格里"
          f"（合并 {len(merged_cells)} 个）")
    check(all("column_boundary_cuts_fragment" not in g.problems
              for t, g in zip(tables, grids) if not g.geometry_backed),
          f"G3 行带路径的跨列合并必须逐条有共同见证，未见证的边界不得造合并"
          f"（行带合并 {band_merged[:4]}）")
    # 覆盖必须是**铺满且互斥**的真分区——这条对**全部**网格成立：`tgeo-2` 起
    # `cell_coverage_overlap` 把重叠覆盖挡在准入之前，因此建出来的表不可能不是分区。
    coverage_ok = True
    for t, g in zip(tables, grids):
        occupied = [(c.row + dr, c.column + dc)
                    for r in t.rows for c in r.cells
                    for dr in range(c.rowspan) for dc in range(c.colspan)]
        if len(occupied) != len(set(occupied)) or \
                len(set(occupied)) != len(t.rows) * t.column_count:
            coverage_ok = False
            check(False, f"G3 现场 p{t.page_number} 的合并网格不是铺满且互斥的分区")
            break
    if coverage_ok:
        check(True, f"G3 现场全部 {len(tables)} 张表的 cell 覆盖必须铺满且互斥"
                    "（骨架矩形或已见证的跨列合并）")
    roles_ok = True
    for t in tables:
        roles = [r.role for r in t.rows]
        n = sum(1 for r in roles if r == "header")
        if roles[:n] != ["header"] * n:
            roles_ok = False
            check(False, f"G3 现场 p{t.page_number} 的 header 行不是连续前缀：{roles}")
            break
    if roles_ok:
        check(True, f"G3 现场表头行一律构成连续前缀（{len(tables)} 张）")


# ---------------------------------------------------------------------------
# G4 §19.12.3-4：网格空洞 / 重叠 / bbox 越界 / 来源片段缺失重复跨 cell
# ---------------------------------------------------------------------------

def _test_g4(live):
    # (a) 未解释空洞：如实报 `unexplained_hole`，不得当成合并。
    holed = _grid_page(missing=((1, 1),))
    site, d, s_ctx, bbox = _owning_site(holed)
    band = site.cand(bbox=bbox, closed=False, gaps=1)
    grid = TG.derive_cell_grid(holed, candidate=band)
    check("unexplained_hole" in grid.problems and not grid.closed
          and all(c.rowspan == 1 for c in grid.cells),
          f"G4 挖空一格必须报 unexplained_hole 且不得当成纵向合并"
          f"（problems={grid.problems}）")
    gate = TB.evaluate_candidate_gate(
        s_ctx, _flag_candidate(site, d, bbox, "frozen_range", closed=False, gaps=1))
    check(not gate.admitted and gate.reason == "grid_not_closed"
          and gate.blocks_document is False and gate.grid is None,
          f"G4 有空洞的候选只报 unsupported（reason={gate.reason}）")
    built = TB.build_tables(s_ctx)
    check(built.tables == () and bool(built.rejected)
          and all(row[2] == "grid_not_closed" for row in built.rejected),
          f"G4 `build_tables` 不得把有空洞的候选变成表："
          f"{[row[2] for row in built.rejected]}")

    # (b) 重叠消费：同一行带内两个真实行片段争同一格位。
    dup_page = _Page(1, [
        _Line(0, [_Span(20.0, 20.0, 50.0, 30.0), _Span(60.0, 20.0, 90.0, 30.0)]),
        _Line(1, [_Span(20.0, 21.0, 50.0, 31.0)]),
        _Line(2, [_Span(20.0, 46.0, 50.0, 56.0), _Span(60.0, 46.0, 90.0, 56.0)]),
    ])
    site2, d2, s_ctx2, bbox2 = _owning_site(dup_page)
    band2 = site2.cand(bbox=bbox2, closed=False, gaps=1)
    grid2 = TG.derive_cell_grid(dup_page, candidate=band2)
    check("fragment_consumed_twice" in grid2.problems and not grid2.closed,
          f"G4 同一行带内两个片段争同一格位必须报 fragment_consumed_twice"
          f"（problems={grid2.problems}）")
    check(TB.evaluate_candidate_gate(
              s_ctx2, _flag_candidate(site2, d2, bbox2, "frozen_range",
                                      closed=False, gaps=1)).reason
          == "grid_not_closed",
          "G4 重叠消费的候选不得过门")

    # (c) 骨架里存在没有任何真实片段解释的锚点 ⇒ `unexplained_hole`
    #     （自报 closed=True 不足为凭）。
    hole_page = _Page(1, [
        _Line(0, [_Span(20.0, 20.0, 50.0, 30.0), _Span(60.0, 20.0, 90.0, 30.0)]),
        _Line(1, [_Span(20.0, 31.0, 50.0, 41.0)]),
    ])
    site3, d3, s_ctx3, _bbox3 = _owning_site(hole_page)
    hole_bbox = (20.0, 20.0, 90.0, 42.0)
    skeleton3 = ((0, 0, 1, 1, (20.0, 20.0, 55.0, 31.0)),
                 (0, 1, 1, 1, (55.0, 20.0, 90.0, 31.0)),
                 (1, 0, 1, 1, (20.0, 31.0, 55.0, 42.0)),
                 (1, 1, 1, 1, (55.0, 31.0, 90.0, 42.0)))
    cand3 = site3.cand(bbox=hole_bbox, closed=True, gaps=0, edges=4,
                       intersections=4, row_count=2, column_count=2,
                       cell_count=4, grid_cells=skeleton3)
    grid3 = TG.derive_cell_grid(hole_page, candidate=cand3)
    check("unexplained_hole" in grid3.problems,
          f"G4 骨架锚点内没有真实片段必须报 unexplained_hole（{grid3.problems}）")
    check(TB.evaluate_candidate_gate(
              s_ctx3, _flag_candidate(site3, d3, hole_bbox, "pdfplumber_lines",
                                      closed=True, gaps=0, edges=4,
                                      intersections=4, row_count=2,
                                      column_count=2, cell_count=4,
                                      grid_cells=skeleton3)
          ).reason == "grid_not_closed",
          "G4 自报闭合的骨架若真实缺少片段，仍必须被硬门拒绝")

    # (d) bbox 越界 / 页内无片段：两类不同结果不得混同。
    site4, d4, s_ctx4, _b4 = _owning_site(_grid_page())
    oob = _flag_candidate(site4, d4, (20.0, 20.0, _PAGE_W + 200.0, 60.0),
                          "frozen_range", closed=True, gaps=0)
    oob_gate = TB.evaluate_candidate_gate(s_ctx4, oob)
    check(oob_gate.admitted is False
          and oob_gate.reason == "candidate_not_uniquely_mapped"
          and oob_gate.blocks_document is False,
          f"G4 候选矩形越出页面必须拒绝为不可唯一映射（reason={oob_gate.reason}）")
    far = site4.cand(bbox=(300.0, 600.0, 500.0, 700.0))
    far_grid = TG.derive_cell_grid(site4.layout.pages[0], candidate=far)
    check(far_grid.problems == ("no_source_fragments",)
          and far_grid.cells == () and far_grid.assigned_lines == (),
          f"G4 页面内没有任何真实片段的矩形必须报 no_source_fragments"
          f"（problems={far_grid.problems}）")
    check(TB.evaluate_candidate_gate(
              s_ctx4, _flag_candidate(site4, d4, (300.0, 600.0, 500.0, 700.0),
                                      "frozen_range", closed=True, gaps=0)
          ).reason == "grid_not_reconstructible",
          "G4 无任何片段的候选必须报网格不可重建（与越界分开）")

    # (e) 表级来源并集：只有三条 fail-closed 分支可达（顺序即生产顺序）。
    r_a = _ref("sc-a", line=0, span=0, c0=0, c1=4)
    r_b = _ref("sc-b", line=0, span=1, c0=0, c1=4)
    r_c = _ref("sc-c", line=9, span=0, c0=0, c1=4)
    solo_a = _cell(0, 0, [("line", "AAAA", r_a)])
    solo_b = _cell(0, 1, [("line", "BBBB", r_b)])
    rows_e = (_row(0, "body", [solo_a, solo_b]),)
    refs = TB._table_refs(rows_e, _adorn())
    check(len(refs) == 2 and {r.source_ref_id for r in refs} ==
          {r_a.source_ref_id, r_b.source_ref_id},
          f"G4 互斥分区的表级来源必须是全部被消费片段的并集（{len(refs)}）")
    full = TB._table_refs(
        rows_e, _adorn(title_blocks=(_blk(0, "line", "CCCC", (r_c,)),),
                       source_refs=(r_a, r_b, r_c)))
    check(len(full) == 3 and {r.source_ref_id for r in full} ==
          {r_a.source_ref_id, r_b.source_ref_id, r_c.source_ref_id},
          "G4 cell + 表级装饰块的并集必须完整进入表级 source_refs（3 条）")
    raises(lambda: TB._table_refs(
        rows_e, _adorn(title_blocks=(_blk(0, "line", "AAAA", (r_a,)),
                                     _blk(1, "line", "AAAA", (r_a,)),),
                       source_refs=(r_a, r_b))),
        TB.UnsupportedStructureError, "两个表级块重复消费",
        "G4 同一片段被两个表级块消费必须 fail-closed")
    raises(lambda: TB._table_refs(
        rows_e, _adorn(title_blocks=(_blk(0, "line", "AAAA", (r_a,)),),
                       source_refs=(r_a, r_b))),
        TB.UnsupportedStructureError, "同时被 cell 与表级块消费",
        "G4 同一片段同时进 cell 与表级块必须 fail-closed")
    raises(lambda: TB._table_refs(
        rows_e, _adorn(title_blocks=(_blk(0, "line", "CCCC", (r_c,)),),
                       source_refs=(r_a, r_b))),
        TB.UnsupportedStructureError, "不是全部被消费片段的并集",
        "G4 表级 source_refs 漏记装饰块片段必须 fail-closed")

    # (f) component 分区（缺失/越界/空洞/重叠）逐条由真实函数抓出。
    if not _gate(live):
        return
    ctx, cons = live["ctx"], live["consumption"]
    check(len(ctx.components) > 0 and len(cons.tables) > 0,
          f"G4 现场必须有 component 台账与被表消费的 component"
          f"（{len(ctx.components)} / {len(cons.tables)}）")
    by_id = {c.component_id: c for c in ctx.components}
    codes_of: dict = {}
    bad: list = []
    for cid, claims in cons.tables.items():
        comp = by_id.get(cid)
        if comp is None:
            bad.append((cid, "unknown_component"))
            continue
        ranges = tuple(sorted({r for (_tid, rs) in claims for r in rs}))
        if not ranges:
            bad.append((cid, "no_ranges"))
            continue
        codes = tuple(sorted({p.partition(":")[2] for p in
                              TB.component_partition_problems(ctx, {cid: ranges})}))
        codes_of[cid] = codes
        if codes:
            continue
        lo, hi = comp.evidence_char_range
        if ranges[0][0] != lo or ranges[-1][1] != hi or \
                any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
            bad.append((cid, "self_check"))
    check(not bad,
          f"G4 现场每个被消费 component 的子区间必须由真实台账闭合"
          f"（反例 {bad[:3]} / {len(cons.tables)}）")
    check({cid: codes for cid, codes in codes_of.items() if codes}
          == {k: tuple(sorted(v)) for k, v in cons.problems.items()},
          f"G4 分区的重算诊断必须与上游记账逐项一致（重算 {len(codes_of)} / "
          f"记账 {len(cons.problems)}）")
    clean = sorted(cid for cid, codes in codes_of.items() if not codes)
    check(bool(clean),
          f"G4 现场必须有分区干净的 component 作为反例基线"
          f"（干净 {len(clean)} / {len(codes_of)}）")
    if clean:
        cid = clean[0]
        ranges = tuple(sorted({r for (_tid, rs) in cons.tables[cid] for r in rs}))
        lo, hi = by_id[cid].evidence_char_range
        narrowed = ((ranges[0][0] + 1, ranges[0][1]),) + tuple(ranges[1:])
        check({p.partition(":")[2] for p in
               TB.component_partition_problems(ctx, {cid: narrowed})}
              == {"range_not_exact"},
              "G4 起点右移（不再精确覆盖全区间）必须报 range_not_exact")
        widened = ((ranges[0][0], ranges[0][1] + 1),) + tuple(ranges[1:])
        want_widened = ({"range_not_exact"} if len(ranges) == 1
                        else {"gap_or_overlap"})
        check({p.partition(":")[2] for p in
               TB.component_partition_problems(ctx, {cid: widened})}
              == want_widened,
              f"G4 首个子区间右移 1（{len(ranges)} 段）必须报 {sorted(want_widened)}")
        if len(ranges) >= 2:
            check({p.partition(":")[2] for p in
                   TB.component_partition_problems(ctx, {cid: ranges[:-1]})}
                  == {"range_not_exact"},
                  "G4 丢掉末段子区间必须报 range_not_exact")
    unknown = TB.component_partition_problems(
        ctx, {"sc-not-a-component": ((0, 1),)})
    check(unknown == ["sc-not-a-component:unknown_component"],
          f"G4 未登记的 component 分区必须被抓出（得到 {unknown}）")


# ---------------------------------------------------------------------------
# G5 §19.12.3-5：cell text 与来源不一致 / 来源序错 / char range 非法
# ---------------------------------------------------------------------------

def _test_g5(live):
    r1 = _ref("sc-g5-1", line=0, span=0, c0=0, c1=4)
    r2 = _ref("sc-g5-2", line=1, span=0, c0=0, c1=4, ev0=50)
    c_ok = _cell(0, 0, [("line", "AAAA", r1), ("line", "BBBB", r2)])
    check(c_ok.text == "AAAA" + _SEP + "BBBB" and len(c_ok.blocks) == 2,
          f"G5 cell 文本必须由 blocks 以冻结分隔规则重构（得到 {c_ok.text!r}）")
    raises(lambda: dataclasses.replace(c_ok, text="AAAA"),
           Exception, "cell text 必须由 blocks 以冻结分隔规则重构",
           "G5 自报 cell 文本与块不一致必须被拒绝")
    raises(lambda: _cell(0, 0, [("line", "AAAA", r1)],
                         bbox=(60.0, 20.0, 20.0, 28.0)),
           Exception, "bbox",
           "G5 非法 cell bbox（x1<=x0）必须被拒绝")
    raises(lambda: _cell(0, 0, [("line", "AAAA", r1), ("line", "BBBB", r2)],
                         rowspan=0),
           Exception, "不得小于 1",
           "G5 非法 rowspan 必须被拒绝")

    # 来源序错：片段必须按 (页, 行, 片段索引, 片段内起点) 升序。
    raises(lambda: TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAA" + _SEP + "BBBB", (r2, r1)),),
        source_refs=(r2, r1)),
        Exception, "必须按 (页, 行, 片段索引, 片段内起点) 升序",
        "G5 来源序错必须被拒绝")
    check(TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAA" + _SEP + "BBBB", (r1, r2)),),
        source_refs=(r1, r2)).text == "AAAA" + _SEP + "BBBB",
        "G5 源序正确的同一 cell 必须被接受")

    # 同一片段上的两个子区间重叠。
    body_a = _ref("sc-g5-3", line=0, span=0, c0=0, c1=6, ev0=0)
    body_b = _ref("sc-g5-3", line=0, span=0, c0=4, c1=8, ev0=4)
    raises(lambda: TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAAAA", (body_a,)),
                _blk(1, "line", "BBBB", (body_b,))),
        source_refs=(body_a, body_b)),
        Exception, "来源片段重叠",
        "G5 同一 LayoutSpan 上的子区间重叠必须被拒绝")

    # 块引用未知来源 / 片段未被消费 / 片段被两个块消费。
    raises(lambda: TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAA", (r2,)),), source_refs=(r1,)),
        Exception, "引用了未知 source_ref_id",
        "G5 块引用未知来源必须被拒绝")
    raises(lambda: TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAA" + _SEP + "BBBB", (r1,)),),
        source_refs=(r1, r2)),
        Exception, "每个来源片段必须恰好被一个块消费",
        "G5 有片段未被任何块消费必须被拒绝")
    raises(lambda: TS.TableCellV4.create(
        row=0, column=0, rowspan=1, colspan=1, bbox=(20.0, 20.0, 60.0, 28.0),
        blocks=(_blk(0, "line", "AAAA", (r1,)), _blk(1, "line", "AAAA", (r1,))),
        source_refs=(r1,)),
        Exception, "不得被同一 cell 的两个块重复消费",
        "G5 同一片段被两个块消费必须被拒绝")

    # char range 非法：长度必须为正、不得负起点、不得端点倒置。
    raises(lambda: _ref("sc-g5-4", c0=4, c1=4),
           Exception, "0 <= start < end",
           "G5 零长度 char range 必须被拒绝")
    raises(lambda: _ref("sc-g5-5", c0=-2, c1=4),
           Exception, "0 <= start < end",
           "G5 负起点 char range 必须被拒绝")
    raises(lambda: _ref("sc-g5-6", c0=9, c1=3),
           Exception, "0 <= start < end",
           "G5 端点倒置的 char range 必须被拒绝")
    raises(lambda: TS.TableSourceInterval(page_number=0, line_index=0,
                                          span_index=0, span_char_range=(0, 1),
                                          bbox=(0.0, 0.0, 1.0, 1.0)),
           Exception, "不得小于 1",
           "G5 非法页码必须被拒绝")

    # 非对齐终态的片段不得可引用（否则整表可引用性会被一句 verdict 洗白）。
    refusal_ref = _ref("sc-g5-7", kind="refusal")
    check(refusal_ref.citable is False
          and refusal_ref.citable_reason == "refusal_record"
          and refusal_ref.alignment_id is None
          and refusal_ref.verdict == "refused",
          "G5 refusal 终态片段必须不可引用且不得伪造 alignment ID")
    check(_ref("sc-g5-8", verdict="refused").citable is False
          and _ref("sc-g5-9", verdict="unaligned").citable is False
          and _ref("sc-g5-9", verdict="unaligned").citable_reason
          == "verdict_not_aligned",
          "G5 非 aligned 终态一律不可引用，且理由必须如实")
    good = _ref("sc-g5-10")
    check(good.citable is True
          and good.citable_reason == TS.CITABLE_REASON_TRUE
          and good.alignment_id == "al-sc-g5-10",
          "G5 aligned 终态片段必须可引用且 alignment_id 指向真实终态")

    if not _gate(live):
        return
    ctx = live["ctx"]
    cells = [c for t in live["tables"] for r in t.rows for c in r.cells]
    bad: list = []
    for cell in cells:
        keys = [(r.interval.page_number, r.interval.line_index,
                 r.interval.span_index, r.interval.span_char_range[0])
                for r in cell.source_refs]
        if keys != sorted(keys):
            bad.append((cell.row, cell.column, "order"))
            continue
        for ref in cell.source_refs:
            span = ctx.layout_span(ref.interval.page_number,
                                   ref.interval.line_index,
                                   ref.interval.span_index)
            a, b = ref.interval.span_char_range
            if span is None or a != 0 or b <= 0 or b > len(span.text):
                bad.append((cell.row, cell.column, "range", (a, b)))
                break
            if ref.terminal_kind == "refusal" and ref.alignment_id is not None:
                bad.append((cell.row, cell.column, "refusal_alignment_id"))
                break
            if ref.citable and ref.verdict != "aligned":
                bad.append((cell.row, cell.column, "citable_not_aligned"))
                break
    check(not bad,
          f"G5 现场所有 cell 来源必须按源序、区间落在真实片段内、且 refusal 终态"
          f"不得携带 alignment ID（反例 {bad[:3]} / {len(cells)} 个 cell）")


# ---------------------------------------------------------------------------
# G6 §19.12.3-6：第一条数据行不误作 header；subtotal 与 total 分开保留
# ---------------------------------------------------------------------------

def _test_g6(live):
    first_data = [_plain_cell(0, 0, "营业收入"), _plain_cell(0, 1, "100")]
    check(TB._row_is_header(first_data) is False
          and TB._row_is_header([_plain_cell(0, 0, "项目", role="heading")]) is True,
          "G6 第一条数据行不得因'位于第一行'而变成表头")
    rows = TB.assign_rows({0: first_data,
                           1: [_plain_cell(1, 0, "营业成本"),
                               _plain_cell(1, 1, "80")]})
    check([r.role for r in rows] == ["body", "body"],
          f"G6 无 heading 证据时全部行都是 body（{[r.role for r in rows]}）")
    check(TB.assign_rows({0: first_data})[0].role == "body",
          "G6 单行同样不得自报表头")
    check(TB.determine_structure_kind(rows=rows, geometry_backed=False,
                                      grid=_grid(rows=2, cols=2))
          == ("key_value_form", "key_value_form_without_header"),
          "G6 无表头时必须显式给出 header_absence_reason（不得静默缺省）")

    check(TS.TABLE_ROW_ROLES == ("header", "body", "subtotal", "total")
          and TS.ROW_ROLE_ORDER == ("header", "body", "subtotal", "total"),
          f"G6 行角色登记必须逐字在册：{TS.TABLE_ROW_ROLES}")
    kv_rows = [_row(0, "body", [_plain_cell(0, 0, "营业收入"),
                                _plain_cell(0, 1, "100")]),
               _row(1, "subtotal", [_plain_cell(1, 0, "小计"),
                                    _plain_cell(1, 1, "100")]),
               _row(2, "body", [_plain_cell(2, 0, "其他"),
                                _plain_cell(2, 1, "5")]),
               _row(3, "total", [_plain_cell(3, 0, "合计"),
                                 _plain_cell(3, 1, "105")])]
    check([r.role for r in kv_rows] == ["body", "subtotal", "body", "total"]
          and "subtotal" != "total",
          "G6 subtotal 与 total 是**分开**的行角色（不得合并成一个）")
    check(TB._missing_fields(kv_rows, "headered_grid", "ordinary_business_table",
                             _grid(rows=4, cols=2, geometry_backed=True), (),
                             title_block_count=1)
          == ("header",),
          "G6 普通业务表即使有 subtotal/total 行，缺口也只能是 header"
          "（不得因为出现小计/合计文字就追加上会计分节缺口）")
    check(not any(r.role in ("subtotal", "total") for r in TB.assign_rows(
        {i: [_plain_cell(i, 0, f"项目{i}"), _plain_cell(i, 1, "1")]
         for i in range(3)})),
          "G6 `assign_rows` 不得从文字猜出 subtotal/total（没有真实行分节证据）")

    if not _gate(live):
        return
    tables = live["tables"]
    live_roles = Counter(r.role for t in tables for r in t.rows)
    check(set(live_roles) <= set(TS.TABLE_ROW_ROLES),
          f"G6 现场行角色必须全部登记在册：{sorted(live_roles)}")
    check(live_roles["subtotal"] == 0 and live_roles["total"] == 0,
          f"G6 现场不得凭空产出 subtotal/total 行：{dict(live_roles)}")
    no_header = [t for t in tables if not any(r.role == "header" for r in t.rows)]
    check(all("header" in t.missing_or_uncertain_fields for t in no_header),
          f"G6 现场每张没有表头行的表都必须显式登记 header 缺口"
          f"（{len(no_header)} / {len(tables)} 张）")
    check(all(t.structure_kind != "key_value_form" or
              "header" in t.missing_or_uncertain_fields for t in tables),
          "G6 key_value_form 的缺省原因必须与 header 缺口一致")


# ---------------------------------------------------------------------------
# G7 §19.12.3-7：改文字/单位/表头/合计/fragment ⇒ 身份变化或被拒
# ---------------------------------------------------------------------------

def _test_g7(live):
    if not _gate(live):
        return
    tables = live["tables"]
    check(bool(tables) and bool(tables[0].source_refs),
          "G7 现场必须有带真实来源的表")
    table = max(tables, key=lambda t: (len(t.rows), len(t.source_refs)))
    base_id = table.table_id
    base_content = table.content_fingerprint
    base_struct = table.structure_fingerprint
    base_prov = table.provenance_fingerprint

    # (1) 改 cell 文本（企业名 / 比例 / 数字都是 cell 文本）。
    row0 = table.rows[0]
    cell0 = row0.cells[-1]
    edited = _with_row(table, _with_cell(
        row0, _retitle_last_block(cell0, cell0.text + "0")))
    check(edited.table_id != base_id and edited.content_fingerprint != base_content,
          "G7 改 cell 文本必须改变表身份与内容指纹")
    check(edited.structure_fingerprint == base_struct
          and edited.provenance_fingerprint == base_prov,
          "G7 只改文本不得改变结构/来源指纹（内容与结构两条轴分开）")

    # (2) 改单位：unit_text 与 unit_blocks 必须同时给出（生产约束），
    #     新片段落在新的真实行号上，因此表级 source_refs 仍按源序且自洽。
    last_page, last_line = max((r.interval.page_number, r.interval.line_index)
                               for r in table.source_refs)
    unit_ref = _ref("sc-unit", page=last_page, line=last_line + 1, span=0,
                    c0=0, c1=4, ev0=10 ** 6)
    unit_blocks = (TS.TableCellBlock.create(
        block_index=0, role="line", text="单位：万元",
        source_ref_ids=(unit_ref.source_ref_id,)),)
    unit_edited = _recreate_table(
        table, unit_blocks=unit_blocks, unit_text="单位：万元",
        source_refs=table.source_refs + (unit_ref,))
    check(unit_edited.table_id != base_id
          and unit_edited.content_fingerprint != base_content,
          "G7 改单位文本必须改变表身份与内容指纹")
    check(unit_edited.structure_fingerprint == base_struct,
          "G7 改单位不得改变结构指纹")
    check(unit_edited.provenance_fingerprint != base_prov,
          "G7 单位片段进入表级来源必须改变来源指纹")

    # (3) 改表头 cell 文本。
    header_row = next((r for r in table.rows if r.role == "header"), table.rows[0])
    h_cell = header_row.cells[0]
    h_edited = _with_row(table, _with_cell(
        header_row, _retitle_last_block(h_cell, h_cell.text + "X")))
    check(h_edited.table_id != base_id,
          "G7 改表头文本必须改变表身份")

    # (4) 改行角色（合计行）：身份必须变化；非法角色序列必须被拒绝。
    last = table.rows[-1]
    total_rows = tuple(dataclasses.replace(r, role="total")
                       if r.row_index == last.row_index else r
                       for r in table.rows)
    total_edited = _recreate_table(table, rows=total_rows)
    check(total_edited.table_id != base_id
          and total_edited.content_fingerprint != base_content,
          "G7 把末行改成 total 必须改变表身份与内容指纹")
    if len(table.rows) >= 3:
        middle = next(r for r in table.rows if r.row_index == 1)
        bad_rows = tuple(dataclasses.replace(r, role="total")
                         if r.row_index == middle.row_index else r
                         for r in table.rows)
        accepted = True
        try:
            _recreate_table(table, rows=bad_rows)
        except Exception:  # noqa: BLE001
            accepted = False
        check(accepted,
              "G7 生产当前**接受**非后缀的 total 行（中间行标 total、其后仍有 "
              "body）：`TableObjectV4._check_rows` 先把 total_count 算成尾随游程、"
              "再拿它与自身比较，条件恒假 ⇒ 该后缀判据是死分支（见最终报告）")
    raises(lambda: _recreate_table(
        table, rows=tuple(dataclasses.replace(r, role="header")
                          for r in table.rows)),
        Exception, "表体",
        "G7 仅表头不得成表")

    # (5) 改任一 fragment 的区间：来源身份与表身份都必须变化。
    swapped = _swap_ref_in_table(table)
    check(swapped is not None,
          "G7 现场必须存在'单块单来源'的 cell 以做片段手术")
    if swapped is not None:
        old_ref, new_ref, ref_edited = swapped
        check(old_ref.source_ref_id != new_ref.source_ref_id,
              "G7 片段区间变化必须改变片段自身的内容寻址身份")
        check(ref_edited.table_id != base_id
              and ref_edited.provenance_fingerprint != base_prov,
              "G7 改任一片段必须改变表身份与来源指纹")
        check(ref_edited.content_fingerprint != base_content
              and ref_edited.structure_fingerprint == base_struct,
              "G7 改片段区间必须改变内容指纹（cell 来源进入 content 载荷），"
              "但不得改变结构指纹")

    # (6) 自报字段与派生身份不一致 ⇒ 拒绝（三套身份各有一条 fail-closed 分支）。
    stale_order = table.to_dict()
    stale_order["source_order_index"] = stale_order["source_order_index"] + 1
    raises(lambda: TS.TableObjectV4.from_dict(stale_order),
           Exception, "table_locator 与派生定位不一致",
           "G7 篡改页内源序必须被拒绝（定位是物理位置的派生量）")
    stale_id = table.to_dict()
    stale_id["table_id"] = (table.table_id[:-1]
                            + ("0" if table.table_id[-1] != "0" else "1"))
    raises(lambda: TS.TableObjectV4.from_dict(stale_id),
           Exception, "table_id 与派生身份不一致",
           "G7 篡改 table_id 必须被拒绝")
    stale_cont = table.to_dict()
    stale_cont["continuation_candidate_locators"] = sorted(
        set(stale_cont["continuation_candidate_locators"]) | {"zzzz-continuation"})
    raises(lambda: TS.TableObjectV4.from_dict(stale_cont),
           Exception, "provenance_fingerprint 与载荷重算不一致",
           "G7 篡改续表候选却沿用旧来源指纹必须被拒绝")
    unit_only = table.to_dict()
    unit_only["unit_text"] = "单位：万元"
    unit_msg = ("unit_text 与 unit_blocks 必须同时存在或同时缺省"
                if not table.unit_blocks
                else "unit_text 必须由 unit_blocks 以冻结分隔规则重构")
    raises(lambda: TS.TableObjectV4.from_dict(unit_only),
           Exception, unit_msg,
           "G7 单位文本必须与单位块同生共死（不得自报）")
    if len(table.rows) >= 3:
        stale3 = table.to_dict()
        row_dicts = [dict(r) for r in stale3["rows"]]
        row_dicts[1]["role"] = "total"
        stale3["rows"] = row_dicts
        raises(lambda: TS.TableObjectV4.from_dict(stale3),
               Exception, "与载荷重算不一致",
               "G7 篡改行角色后沿用旧指纹必须被拒绝（载荷指纹抓住行结构篡改；"
               "后缀判据本身是死分支，见上一条）")

    # (7) 现场：所有表的身份都是内容寻址且互相不同。
    ids = [t.table_id for t in tables]
    check(len(set(ids)) == len(ids) and all(t.table_id.startswith("to4-")
                                            for t in tables),
          f"G7 现场表身份必须唯一且为登记前缀（{len(ids)} 张）")
    check(all(t.content_fingerprint and t.structure_fingerprint
              and t.provenance_fingerprint for t in tables),
          "G7 现场每张表都必须携带三条独立指纹")


# ---------------------------------------------------------------------------
# A1 §19.12.2A-1：binding 与 verified TS4 components 精确等集
# ---------------------------------------------------------------------------

def _test_a1(live):
    if not _gate(live):
        return
    root, bindings = live["root"], live["bindings"]
    comp_ids = [c.component_id for c in root.components]
    bind_ids = [b.component_id for b in bindings]
    check(len(bind_ids) == len(set(bind_ids)),
          f"A1 每个 component 只允许一条 binding"
          f"（重复 {len(bind_ids) - len(set(bind_ids))}）")
    check(set(bind_ids) == set(comp_ids),
          f"A1 binding 的 component 集合必须与 verified TS4 components 精确等集"
          f"（缺 {sorted(set(comp_ids) - set(bind_ids))[:3]} / "
          f"多 {sorted(set(bind_ids) - set(comp_ids))[:3]}）")
    check(len(bindings) == len(root.components),
          f"A1 binding 条数必须等于 component 条数"
          f"（{len(bindings)} vs {len(root.components)}）")
    by_id = {c.component_id: c for c in root.components}
    check(all(b.component_locator == by_id[b.component_id].component_locator
              and b.component_landing == by_id[b.component_id].landing
              and b.evidence_char_range ==
              by_id[b.component_id].evidence_char_range
              for b in bindings),
          "A1 每条 binding 的 locator/landing/区间必须对象级回查命中同一 component")
    check([b.binding_locator for b in bindings] ==
          sorted(b.binding_locator for b in bindings),
          "A1 binding 必须按 locator 稳定排序")
    check(len({b.binding_id for b in bindings}) == len(bindings),
          "A1 binding 身份必须唯一")
    landings = Counter((b.component_landing, b.admission) for b in bindings)
    check(all(l in TS.COMPONENT_LANDINGS for (l, _a) in landings),
          f"A1 现场必须出现已登记的 landing：{sorted({l for (l, _a) in landings})}")
    check(all(a in TS.BINDING_ADMISSIONS for (_l, a) in landings),
          f"A1 现场终态必须属于封闭登记：{sorted({a for (_l, a) in landings})}")
    check(all(a in TS.COMPONENT_LANDING_ADMISSIONS[l] for (l, a) in landings),
          f"A1 landing→终态的允许集合必须逐项成立（反例 "
          f"{[k for k in landings if k[1] not in TS.COMPONENT_LANDING_ADMISSIONS[k[0]]][:3]}）")
    check(all(b.admission_reason in TS.BINDING_REASONS for b in bindings),
          "A1 终态理由必须逐条登记在册")
    check(all(b.admission_reason in
              TS.COMPONENT_LANDING_PENDING_REASONS[b.component_landing]
              for b in bindings if b.admission == "pending"),
          "A1 pending 理由必须落在该 landing 的封闭理由集内")
    check(all(b.admission_reason in
              TS.COMPONENT_LANDING_REJECTED_REASONS[b.component_landing]
              for b in bindings if b.admission == "rejected"),
          "A1 rejected 理由必须落在该 landing 的封闭理由集内")
    check(all(b.component_id and b.component_locator and b.binding_locator
              for b in bindings),
          "A1 binding 的 component 定位不得为空")


# ---------------------------------------------------------------------------
# A2 §19.12.2A-2：decision→admission/target 真值表
# ---------------------------------------------------------------------------

def _test_a2(live):
    check(set(TS.TABLE_DECISION_TARGETS) == set(TS.TABLE_DECISION_KINDS)
          and len(TS.TABLE_DECISION_TARGETS) == 8,
          f"A2 decision→target 真值表必须恰覆盖 8 类裁决："
          f"{sorted(TS.TABLE_DECISION_TARGETS)}")
    inverted: dict = {}
    for dec, target in TS.TABLE_DECISION_TARGETS.items():
        inverted.setdefault(target, []).append(dec)
    check(all(set(inverted[t]) == set(TS.DECISION_TARGET_OF[t])
              and len(inverted[t]) == len(TS.DECISION_TARGET_OF[t])
              for t in inverted)
          and set(TS.DECISION_TARGET_OF) == set(inverted),
          f"A2 逆查视图必须是同一真值表的另一方向：{TS.DECISION_TARGET_OF}")
    thirds = (set(FMB.ABSORBED_DECISIONS), set(FMB.KEPT_DECISIONS),
              set(FMB.STRUCTURAL_DECISIONS))
    check(thirds[0] | thirds[1] | thirds[2] == set(TS.TABLE_DECISION_KINDS)
          and not (thirds[0] & thirds[1]) and not (thirds[1] & thirds[2])
          and not (thirds[0] & thirds[2]),
          "A2 三分组必须穷尽且互斥全部 8 类裁决")
    check(set(FMB.ABSORBED_DECISIONS) ==
          {"absorbed_as_caption", "absorbed_as_unit", "absorbed_as_note",
           "absorbed_as_table_body"}
          and set(FMB.KEPT_DECISIONS) == {"kept_as_paragraph",
                                          "absorbed_into_body"},
          f"A2 三分组内容必须逐字在册：{FMB.ABSORBED_DECISIONS} / "
          f"{FMB.KEPT_DECISIONS}")
    check(set(FMB.ABSORBED_DECISIONS) == set(TS.DECISION_TARGET_OF["table"])
          and set(FMB.KEPT_DECISIONS) == set(TS.DECISION_TARGET_OF["final_span"])
          and set(FMB.STRUCTURAL_DECISIONS) ==
          set(TS.DECISION_TARGET_OF["none"]),
          "A2 三分组必须与 decision→target 真值表逐项一致（target=table/"
          "final_span/none）")

    if not _gate(live):
        return
    ctx = live["ctx"]
    decisions = live["decisions"]
    decision_of = {d.disposition_id: d for d in decisions}
    check(len(decision_of) == len(decisions),
          f"A2 每个 provisional disposition 只允许一条裁决"
          f"（{len(decisions)} 条 / {len(decision_of)} 个 id）")
    check(all(d.range_kind in TS.TABLE_RANGE_KINDS for d in decisions),
          f"A2 裁决只覆盖表 provisional 范围："
          f"{sorted({d.range_kind for d in decisions})}")
    check(all(d.decision in TS.TABLE_DECISION_KINDS for d in decisions),
          "A2 裁决种类必须登记在册")
    check(all(d.target_kind == TS.TABLE_DECISION_TARGETS[d.decision]
              for d in decisions),
          "A2 每条裁决的 target_kind 必须等于真值表规定的目标")
    check(all((d.decision in FMB.ABSORBED_DECISIONS) ==
              (d.table_id is not None) for d in decisions)
          and all((d.decision in FMB.KEPT_DECISIONS) ==
                  (d.final_span_id is not None) for d in decisions)
          and all(d.decision in FMB.ABSORBED_DECISIONS
                  or d.decision in FMB.KEPT_DECISIONS
                  or (d.table_id is None and d.final_span_id is None)
                  for d in decisions),
          "A2 裁决与它声称的目标对象必须同时成立或缺省（不得挂空指）")
    comp_by_id = {c.component_id: c for c in ctx.components}
    table_ids = {t.table_id for t in live["tables"]}
    span_ids = {s.span_id for s in live["final_spans"]}
    problems: list = []
    cons = live["consumption"]
    span_of_component = live["span_of_component"]
    for b in live["bindings"]:
        comp = comp_by_id[b.component_id]
        if comp.landing not in TB.TABLE_DISPOSITION_KINDS:
            continue
        if b.disposition_id not in decision_of:
            problems.append((b.component_id, "no_decision"))
            continue
        dec = decision_of[b.disposition_id]
        # 真值表的**优先序**（由消费台账独立复算，不调用被测函数）：
        # 单表干净消费 ▸ 有消费但脏 ▸ 无消费时按裁决目标。
        claims = cons.tables.get(b.component_id, ())
        probs = cons.problems.get(b.component_id, ())
        clean_id = None
        if len(claims) == 1 and not probs:
            tid = claims[0][0]
            if cons.ref_ids.get((tid, b.component_id), ()):
                clean_id = tid
        if clean_id is not None:
            if (b.admission != "table_object"
                    or b.admission_reason != "absorbed_into_table"
                    or b.table_id != clean_id
                    or b.cell_source_ref_ids
                    != cons.ref_ids[(clean_id, b.component_id)]):
                problems.append((b.component_id, "clean_table_not_bound"))
        elif claims or probs:
            if (b.admission != "pending"
                    or b.admission_reason != "provenance_incomplete"):
                problems.append((b.component_id, "dirty_not_pending"))
        elif dec.decision in FMB.ABSORBED_DECISIONS:
            if (b.admission != "pending"
                    or b.admission_reason != "provenance_incomplete"):
                problems.append((b.component_id, "absorbed_without_consumption"))
        elif dec.decision in FMB.KEPT_DECISIONS:
            if b.admission == "final_span":
                if (b.admission_reason != "kept_as_final_paragraph"
                        or b.final_span_id
                        != span_of_component.get(b.component_id)):
                    problems.append((b.component_id, "kept_reason"))
            elif b.admission == "pending":
                if b.admission_reason != "provenance_incomplete":
                    problems.append((b.component_id, "kept_pending"))
            else:
                problems.append((b.component_id, "kept_wrong_admission"))
        elif dec.decision == "unresolved_geometry":
            if (b.admission != "pending"
                    or b.admission_reason != "unresolved_geometry"):
                problems.append((b.component_id, "unresolved_not_pending"))
        else:
            if (b.admission != "pending"
                    or b.admission_reason != "unsupported_table_structure"):
                problems.append((b.component_id, "structural_not_pending"))
    check(not problems,
          f"A2 每条表范围 binding 的终态必须由『单表干净消费 ▸ 脏消费 ▸ 裁决"
          f"目标』的固定优先序单点决定（反例 {problems[:4]}）")
    check(all(b.table_id is None or b.table_id in table_ids
              for b in live["bindings"]),
          "A2 任何 table_object 终态都必须指向现场真实表")
    check(all(b.final_span_id is None or b.final_span_id in span_ids
              for b in live["bindings"]),
          "A2 任何 final_span 终态都必须指向现场真实 final span")
    check(all(b.table_id is None or b.final_span_id is None
              for b in live["bindings"]),
          "A2 一条 binding 不得同时指向表与 final span")
    check(all(len(set(b.cell_source_ref_ids)) == len(b.cell_source_ref_ids)
              for b in live["bindings"]),
          "A2 binding 的 cell source refs 不得重复")
    check(all((len(b.cell_source_ref_ids) > 0) ==
              (b.admission == "table_object")
              for b in live["bindings"]),
          "A2 只有 table_object 终态才允许携带被消费的 cell source refs")
    check(all(bool(b.gap_codes) == (b.admission == "pending")
              for b in live["bindings"]),
          "A2 pending 必须留下缺口码，非 pending 不得携带缺口码")
    check(all(set(b.gap_codes) <= set(TS.TABLE_GAP_KINDS)
              for b in live["bindings"]),
          "A2 缺口码必须登记在册")

    # `_binding_for` 真值表：裁决说"被吸收"但没有任何真实消费 ⇒ 必须 pending。
    consumed = FMB._Consumption(tables={}, ref_ids={}, problems={})
    dispos_of = {c.component_id: c.disposition_id for c in ctx.components}
    absorbed_comp = next(
        (c for c in ctx.components
         if c.landing in TB.TABLE_DISPOSITION_KINDS
         and dispos_of.get(c.component_id) in decision_of
         and decision_of[dispos_of[c.component_id]].decision
         in FMB.ABSORBED_DECISIONS), None)
    if absorbed_comp is None:
        check(False, "A2 现场缺少可用于真值表探针的已吸收 component")
    else:
        plan = FMB._binding_for(consumed, absorbed_comp, decision_of, {})
        check(plan["admission"] == "pending"
              and plan["admission_reason"] == "provenance_incomplete"
              and plan["table_id"] is None
              and plan["gap_codes"] == ("provenance_incomplete",),
              f"A2 裁决说被吸收但无真实消费时必须 pending(provenance_incomplete)"
              f"（得到 {plan['admission']}/{plan['admission_reason']}）")
        structural_comp = next(
            (c for c in ctx.components
             if c.landing in TB.TABLE_DISPOSITION_KINDS
             and dispos_of.get(c.component_id) in decision_of
             and decision_of[dispos_of[c.component_id]].decision
             in FMB.STRUCTURAL_DECISIONS), None)
        if structural_comp is not None:
            dec = decision_of[dispos_of[structural_comp.component_id]]
            plan2 = FMB._binding_for(consumed, structural_comp, decision_of, {})
            want = ("unresolved_geometry" if dec.decision == "unresolved_geometry"
                    else "unsupported_table_structure")
            check(plan2["admission"] == "pending"
                  and plan2["admission_reason"] == want
                  and plan2["cell_source_ref_ids"] == (),
                  f"A2 {dec.decision} 必须映射为 pending({want})"
                  f"（得到 {plan2['admission']}/{plan2['admission_reason']}）")
    body_comp = next((c for c in ctx.components if c.landing == "body_span"), None)
    if body_comp is None:
        check(False, "A2 现场缺少可用于真值表探针的 body_span component")
    else:
        plan3 = FMB._binding_for(consumed, body_comp, decision_of, {})
        check(plan3["admission"] == "pending"
              and plan3["admission_reason"] == "upstream_table_scope_miss",
              f"A2 body_span 没有真实 final span 时必须 pending"
              f"(upstream_table_scope_miss)"
              f"（得到 {plan3['admission']}/{plan3['admission_reason']}）")
        plan4 = FMB._binding_for(
            consumed, body_comp, decision_of,
            {body_comp.component_id: "fs-synthetic-1"})
        check(plan4["admission"] == "final_span"
              and plan4["admission_reason"] == "rebuilt_as_final_span"
              and plan4["final_span_id"] == "fs-synthetic-1",
              f"A2 body_span 有真实 final span 时必须重建为 final span 并携带"
              f"其 span_id（得到 {plan4['admission']}/"
              f"{plan4['admission_reason']}/{plan4['final_span_id']}）")
    rejected_landing = FMB._REJECTED_REASON_OF_LANDING
    check(all(v in TS.BINDING_REASONS for v in rejected_landing.values())
          and set(rejected_landing) <= set(TS.COMPONENT_LANDINGS),
          f"A2 rejected 理由映射必须逐键登记：{rejected_landing}")
    check(all(v == TS.COMPONENT_LANDING_REJECTED_REASONS[k][0]
              for k, v in rejected_landing.items()),
          "A2 rejected 映射必须与 `COMPONENT_LANDING_REJECTED_REASONS` 逐项一致")
    check(set(FMB._NO_FRAGMENT_LANDINGS) <= set(TS.COMPONENT_LANDINGS)
          and all(TS.COMPONENT_LANDING_ADMISSIONS[l] == ("pending",)
                  for l in FMB._NO_FRAGMENT_LANDINGS),
          "A2 无可核验 fragment 的 landing 只能 pending")


# ---------------------------------------------------------------------------
# G8. §二 表题资格：必须由**连续文本游程**证明，而不是"挨着表"
# ---------------------------------------------------------------------------

def _g8_site(pages_lines):
    """一页 + 覆盖指定行的冻结表范围 + 该范围的上下文。"""
    page = _Page(1, pages_lines)
    site = _Site([page], nodes=[_Node("n1")])
    return site


def _g8_proven(lines, *, start_line, end_line=None):
    """按给定真实行构造上下文并问生产判据：这个范围能不能算表题。"""
    site = _g8_site(lines)
    d = _Disposition("d-cap", "table_above", "n1", 1, 1, start_line,
                     start_line if end_line is None else end_line)
    return site.context(dispositions=[d]), d


def _test_g8(live):
    """§八(1)(2)：列状行不得当表题；单条连续标题段是正例。

    三条判据都是**与语言无关的版式不变量**（连续游程 / 唯一物理行 / 孤立行），
    因此本组只用合成几何构造正反例，不读任何业务文字，也不断言任何关键词。
    """
    tail = _Line(9, [_Span(20.0, 60.0, 90.0, 70.0, text="表体")])

    # (1) 负例：单一物理行里灌了两个相距甚远的片段 —— 它是**列**，不是一段文字流。
    col_line = _Line(0, [_Span(20.0, 20.0, 50.0, 30.0, text="项目"),
                         _Span(185.0, 20.0, 215.0, 30.0, text="金额")])
    ctx, d = _g8_proven([col_line, tail], start_line=0)
    check(not TB._caption_region_single_run(col_line),
          f"G8 列状行不构成连续文本游程（片段间隙 "
          f"{185.0 - 50.0} > {TB.CAPTION_RUN_GAP_RATIO}×字号）")
    check(TB.caption_region_proven(ctx, d) is False,
          "G8 含多个列片段的单一物理行不得被当作表题（§八(1)）")

    # (2) 正例：一条连续文本游程的行；下方另一行左边界**不**与本行对齐，
    #     因此本行不是折行正文的一行 ⇒ 孤立 ⇒ 表题成立。
    run_line = _Line(0, [_Span(120.0, 20.0, 240.0, 30.0, text="公司债券募集资金运用情况表"),
                         _Span(241.0, 20.0, 300.0, 30.0, text="（续）")])
    other = _Line(1, [_Span(130.0, 40.0, 300.0, 50.0, text="下方另一栏文字")])
    ctx, d = _g8_proven([run_line, other], start_line=0)
    check(TB._caption_region_single_run(run_line)
          and abs(130.0 - 120.0) > TB.CAPTION_FLOW_LEFT_TOLERANCE,
          "G8 正例前置：本行连续、且下方行左边界与本行不对齐"
          "（因此不构成正文续行）")
    check(TB.caption_region_proven(ctx, d) is True,
          "G8 单条连续标题段作为正例必须被判为表题（§八(2)）")

    # (3) 反向控制：同一行文字，只要下方存在**左边界对齐且 x 相交**的相邻真实行，
    #     它就是折行正文的一行，不得当表题 —— 证明"孤立行"判据不是空转。
    flow = _Line(1, [_Span(120.0, 40.0, 300.0, 50.0, text="同一段正文的续行")])
    ctx, d = _g8_proven([run_line, flow], start_line=0)
    check(not TB._caption_region_isolated(run_line, ctx.page(1)),
          "G8 同栏内左边界对齐且 x 相交的相邻行使其不再是孤立行")
    check(TB.caption_region_proven(ctx, d) is False,
          "G8 折行正文的一行不得被当作表题（反向控制）")

    # (4) 负例：一条占多行的冻结范围是正文段落的一部分，不得当表题（§二.1）。
    second = _Line(1, [_Span(120.0, 33.0, 300.0, 43.0, text="第二条真实行")])
    ctx, d = _g8_proven([run_line, second, tail], start_line=0, end_line=1)
    check(TB.caption_region_line_count(ctx, d) == 2
          and TB.caption_region_proven(ctx, d) is False,
          "G8 占据两条真实行的冻结范围不得被当作表题（§二.1 唯一物理行）")

    # (5) 与语言无关：把三个正例的文字换成完全不同的字符，结论必须逐项不变。
    swapped = _Line(0, [_Span(120.0, 20.0, 240.0, 30.0, text="AAAA BBBB CCCC"),
                        _Span(241.0, 20.0, 300.0, 30.0, text="DDDD")])
    ctx, d = _g8_proven([swapped, other], start_line=0)
    check(TB.caption_region_proven(ctx, d) is True,
          "G8 表题判据不读业务文字：换字符后结论不变")


# ---------------------------------------------------------------------------
# G9. §四 视觉对象分类：不得按"有没有文字"二分
# ---------------------------------------------------------------------------

_G9_BOX = (20.0, 20.0, 200.0, 120.0)
_G9_FULL = (0.0, 0.0, 600.0, 800.0)


def _g9_ctx(text_line=True, **cands):
    """一页（可含一条真实文字行）+ 自带几何证据的候选。"""
    lines = [_Line(0, [_Span(30.0, 30.0, 90.0, 40.0, text="节点甲")])] \
        if text_line else [_Line(0, [_Span(30.0, 30.0, 90.0, 40.0, text=" ")])]
    site = _Site([_Page(1, lines)])
    return site.context(candidates=[site.cand(**kw) for kw in cands.values()])


def _g9_kind(ctx, bbox):
    """按生产入口问分类结果；`root` 形参在生产实现里未被读取，故显式传 `None`。"""
    return FMB._candidate_gap_kind(None, ctx, 1, bbox)


def _test_g9(live):
    """§八(5)(6)：有文字不等于表；分不清就必须留 `unresolved_geometry`。"""
    # (5) 带文本但只有图元拓扑、且不是整页回退框 ⇒ 可证是**非表格视觉对象**。
    ctx = _g9_ctx(topology=dict(bbox=_G9_BOX, edges=4, intersections=2))
    check(FMB._candidate_has_text(None, ctx, 1, _G9_BOX) is True,
          "G9 前置：该矩形内确实有真实文字（旧实现会据此判成表结构）")
    check(_g9_kind(ctx, _G9_BOX) == "visual_object_not_table",
          "G9 带文本的图元拓扑对象必须被判为 visual_object_not_table（§八(5)）")

    # (6) 同一条矩形同时是整页回退框 ⇒ 图元与整页文本混在一起，不得猜。
    ctx = _g9_ctx(topology=dict(bbox=_G9_FULL, edges=4, intersections=2))
    check(_g9_kind(ctx, _G9_FULL) == "unresolved_geometry",
          "G9 图元拓扑与整页回退框重合时必须留 unresolved_geometry（§八(6)）")

    # 对照 A：有真实表状网格（≥2 列 ≥2 行且有 cell）时网格优先，拒绝理由另有原因。
    ctx = _g9_ctx(topology=dict(bbox=_G9_BOX, edges=4, intersections=2,
                                row_count=3, column_count=2, cell_count=6))
    check(_g9_kind(ctx, _G9_BOX) == "unsupported_table_structure",
          "G9 有可闭合表状网格时不得改判成视觉对象（分类顺序不得被绕过）")

    # 对照 B：无文字、无网格、无拓扑 ⇒ 纯图元，同样是非表格视觉对象。
    ctx = _g9_ctx(text_line=False, bare=dict(bbox=_G9_BOX))
    check(_g9_kind(ctx, _G9_BOX) == "visual_object_not_table",
          "G9 无文字无网格无拓扑的矩形仍是视觉对象，而不是被当成表")

    # 对照 C：有真实文字、无网格、无拓扑 ⇒ "有范围但不足以建表"，不得冒充视觉对象。
    ctx = _g9_ctx(bare=dict(bbox=_G9_BOX))
    check(_g9_kind(ctx, _G9_BOX) == "unsupported_table_structure",
          "G9 只有文字时是 unsupported_table_structure，"
          "不得因为'没网格'就被当成视觉对象")

    # 对照 D：集成路径 —— `outside_frozen_table_scope` 必须走到同一个分类器，
    # 不得另起一套"有没有文字"的判断。
    ctx = _g9_ctx(topology=dict(bbox=_G9_BOX, edges=4, intersections=2))
    check(FMB._rejection_gap_kind(None, ctx, "outside_frozen_table_scope",
                                  1, _G9_BOX) == "visual_object_not_table",
          "G9 候选拒绝理由经生产入口必须落到同一个结构证据分类器")


# ---------------------------------------------------------------------------
# A3 §19.5.1 相邻冻结范围裁定（第二种）：连续相邻冻结表范围的**并集**通道
# ---------------------------------------------------------------------------

def _split_site(page, groups, *, node_ids=None, kinds=None, regular=()):
    """把一页真实行按行区间切成多个冻结表范围（`tb-4` 并集通道的输入）。

    `groups` = `((start_line, end_line), ...)`；`node_ids` / `kinds` 逐段给出（缺省
    全同）。`regular` = 同页 `regular` 正文段，用于判据 ④ 的反例。
    """
    if node_ids is None:
        node_ids = ("n1",) * len(groups)
    if kinds is None:
        kinds = ("table_inside",) * len(groups)
    nodes: list = []
    for nid in node_ids:
        if nid is not None and nid not in [n.node_id for n in nodes]:
            nodes.append(_Node(nid))
    site = _Site([page], nodes=nodes)
    ds = [_Disposition(f"d-{i + 1}", kind, nid, page.page_number,
                       page.page_number, s, e)
          for i, ((s, e), nid, kind) in enumerate(zip(groups, node_ids, kinds))]
    ds += [_Disposition(f"r-{j + 1}", "regular", None, page.page_number,
                        page.page_number, s, e)
           for j, (s, e) in enumerate(regular)]
    return site, tuple(ds), site.context(dispositions=ds)


def _union_cands(ctx):
    return [c for c in TB.enumerate_candidates(ctx) if c.union_disposition_ids]


def _ratio(x0, y0, x1, y1):
    return (x1 - x0) * (y1 - y0)


def _test_a3(live):
    """`tb-4`：一串连续相邻冻结表范围的并集**另出一个候选**，且只凭并集本身准入。

    正例一条（并集网格闭合 ⇒ 准入，依据码单独具名）、反例五条（间隙超上界 / 节点不同 /
    并集碰冻结正文 / 成员矩形不可回查 / 候选矩形比成员并集多出面积）。并集候选的
    `frozen_overlap_ratio` 必须**如实**小于 1（不得靠写 1 绕开交占比门）。
    """
    check(TB.UNION_ADJACENCY_GAP_PT == 9.0
          and TB.UNION_ADJACENCY_GAP_PT < TB.PROXIMITY_PT,
          f"A3 相邻判据上界必须是登记值且窄于既有邻近判据"
          f"（{TB.UNION_ADJACENCY_GAP_PT} vs {TB.PROXIMITY_PT}）")
    check("contiguous_frozen_union" in TS.CANDIDATE_ADMISSION_BASES,
          "A3 并集准入依据码必须登记在封闭依据码集内")

    # (a) 正例：一页 4 行被切成 2+2，两段垂直间隙 3.0 pt、横向完全重叠、同一节点。
    page = _grid_page()
    site, ds, ctx = _split_site(page, ((0, 1), (2, 3)))
    runs = TB._frozen_runs(ctx)
    check([len(r) for r in runs] == [2]
          and [d.disposition_id for d in runs[0]] == ["d-1", "d-2"],
          f"A3 连续相邻的两段必须聚成一串（{[[d.disposition_id for d in r] for r in runs]}）")
    cands = _union_cands(ctx)
    check(len(cands) == 1 and len(cands[0].union_disposition_ids) == 2,
          f"A3 一串 n>=2 的范围必须另出一个并集候选"
          f"（{[c.union_disposition_ids for c in cands]}）")
    cand = cands[0]
    owner_box = _union(page.lines[0:2])
    union_box = _union(page.lines[0:4])
    want = (_ratio(*owner_box) / _ratio(*union_box))
    check(abs(cand.frozen_overlap_ratio - want) < 1e-9
          and 0.0 < cand.frozen_overlap_ratio < 1.0,
          f"A3 并集候选的交占比必须**如实**等于 owner 真实矩形占并集矩形的比"
          f"（实测 {cand.frozen_overlap_ratio} vs 手算 {want}）")
    check(cand.competing_disposition_ids == ("d-2",)
          and cand.frozen_disposition_id == "d-1",
          f"A3 并集候选的其余成员必须如实登记为竞争命中"
          f"（{cand.competing_disposition_ids} / {cand.frozen_disposition_id}）")
    check(all(abs(a - b) < 1e-9 for a, b in zip(cand.bbox, union_box)),
          f"A3 并集候选矩形必须等于成员真实矩形的并集（{cand.bbox} vs {union_box}）")
    gate = TB.evaluate_candidate_gate(ctx, cand)
    check(gate.admitted is True and gate.reason is None
          and gate.blocks_document is False
          and gate.admission_basis == "contiguous_frozen_union",
          f"A3 并集网格闭合时必须准入且依据码单独具名"
          f"（admitted={gate.admitted} reason={gate.reason} "
          f"basis={gate.admission_basis}）")
    check(gate.grid is not None and gate.grid.row_count == 4
          and gate.grid.column_count == 2,
          f"A3 准入后必须是并集框上的真实网格（"
          f"{None if gate.grid is None else (gate.grid.row_count, gate.grid.column_count)}）")
    built = TB.build_tables(ctx)
    rows = [r for r in built.audit if r.page_number == 1
            and all(abs(a - b) < 1e-9 for a, b in zip(r.bbox, union_box))]
    check(built.tables == () and len(rows) == 1
          and rows[0].outcome == "rejected"
          and rows[0].reason == "unsupported_table_structure"
          and rows[0].admission_basis is None,
          f"A3 合成站点没有来源证据 ⇒ 过了门也必须在建表阶段 fail-closed，且审计行"
          f"不得留下'已准入'的依据码（"
          f"{[(r.outcome, r.reason, r.admission_basis) for r in rows]}）")

    # (b) 对照：同一个 4 行范围若**不被切**，准入依据码仍是逐范围的 `frozen_range_contained`
    #     （并集具名不得外溢到逐范围通道）。
    _s2, _d2, ctx2, _b2 = _owning_site(_grid_page())
    g2 = TB.evaluate_candidate_gate(
        ctx2, TB.enumerate_candidates(ctx2)[0])
    check(g2.admitted is True
          and g2.admission_basis == "frozen_range_contained"
          and not _union_cands(ctx2),
          f"A3 未被切开的单范围候选不得改记为并集依据"
          f"（basis={g2.admission_basis} unions={len(_union_cands(ctx2))}）")

    # (c) 反例①：两段间隙超过相邻上界（15.0 pt）⇒ 不聚串。
    page_c = _Page(1, _two_col_rows(tops=(20.0, 45.0)))
    _sc, _dc, ctx_c = _split_site(page_c, ((0, 0), (1, 1)))
    check(TB._frozen_runs(ctx_c) and all(len(r) == 1
                                        for r in TB._frozen_runs(ctx_c))
          and not _union_cands(ctx_c),
          f"A3 间隙 15.0 pt（> {TB.UNION_ADJACENCY_GAP_PT}）的两段不得聚串")

    # (d) 反例②：横向**不**重叠的两段（上下相邻但左右错开）⇒ 不聚串。
    page_d = _Page(1, [_Line(0, [_Span(20.0, 20.0, 50.0, 30.0)]),
                       _Line(1, [_Span(60.0, 32.0, 90.0, 42.0)])])
    _sd, _dd, ctx_d = _split_site(page_d, ((0, 0), (1, 1)))
    check(not _union_cands(ctx_d),
          "A3 横向无重叠的两段不得聚成一张表（横向判据与纵向判据并列）")

    # (e) 反例③：同属一页但分属两个已验证标题节点 ⇒ 不聚串（表对象不得跨标题节点）。
    page_e = _grid_page()
    _se, _de, ctx_e = _split_site(page_e, ((0, 1), (2, 3)),
                                  node_ids=("n1", "n2"))
    check(all(len(r) == 1 for r in TB._frozen_runs(ctx_e))
          and not _union_cands(ctx_e),
          "A3 分属两个标题节点的相邻段不得聚成一串")

    # (f) 反例④：两段之间隔着同页冻结 `regular` 正文 ⇒ 不聚串。
    page_f = _Page(1, [_Line(0, [_Span(20.0, 20.0, 50.0, 30.0),
                                _Span(60.0, 20.0, 90.0, 30.0)]),
                       _Line(1, [_Span(20.0, 31.0, 90.0, 35.0, text="正文")]),
                       _Line(2, [_Span(20.0, 36.0, 50.0, 46.0),
                                 _Span(60.0, 36.0, 90.0, 46.0)])])
    _sf, _df, ctx_f = _split_site(page_f, ((0, 0), (2, 2)),
                                  regular=((1, 1),))
    check(all(len(r) == 1 for r in TB._frozen_runs(ctx_f))
          and not _union_cands(ctx_f),
          "A3 隔着同页冻结正文的两段不得聚成一串（判据 ④）")

    # (g) 反例⑤：成员矩形不可回查（整段都是页眉页脚）⇒ 断开串，不得跨过它。
    page_g = _Page(1, [_Line(0, [_Span(20.0, 20.0, 50.0, 30.0),
                                _Span(60.0, 20.0, 90.0, 30.0)]),
                       _Line(1, [_Span(20.0, 31.0, 90.0, 35.0, text="页脚"),
                                 ], furniture=True),
                       _Line(2, [_Span(20.0, 36.0, 50.0, 46.0),
                                 _Span(60.0, 36.0, 90.0, 46.0)])])
    _sg, _dg, ctx_g = _split_site(page_g, ((0, 0), (1, 1), (2, 2)))
    check([len(r) for r in TB._frozen_runs(ctx_g)] == [1, 1]
          and not _union_cands(ctx_g),
          f"A3 不可回查真实矩形的范围必须断开串（"
          f"{[len(r) for r in TB._frozen_runs(ctx_g)]}）")

    # (h) 反例⑥：并集候选矩形比成员并集**多出**面积 ⇒ 裁定否，且理由与逐范围候选
    #     交占比不足时**同一个**理由码。
    page_h = _grid_page()
    site_h, ds_h, ctx_h = _split_site(page_h, ((0, 1), (2, 3)))
    real = _union(page_h.lines[0:4])
    forged = (real[0] - 1.0, real[1] - 1.0, real[2] + 1.0, real[3] + 1.0)
    fake = TB.BuilderCandidate(
        candidate_source="frozen_range", strategy="lines", page_number=1,
        bbox=forged,
        geometry=site_h.cand(bbox=forged, source="frozen_range"),
        frozen_disposition_id="d-1", frozen_disposition_locator="loc-d-1",
        frozen_overlap_ratio=0.5,
        competing_disposition_ids=("d-2",),
        union_disposition_ids=("d-1", "d-2"))
    gate_h = TB.evaluate_candidate_gate(ctx_h, fake)
    check(gate_h.admitted is False
          and gate_h.reason == "candidate_straddles_frozen_range"
          and gate_h.blocks_document is False,
          f"A3 候选矩形比成员并集多出面积时不得按并集准入"
          f"（reason={gate_h.reason}）")
    # 同一串成员、矩形有据：反过来证明上一条否的是"矩形不符"而不是"并集一律否"。
    ok = TB.BuilderCandidate(
        candidate_source="frozen_range", strategy="lines", page_number=1,
        bbox=real, geometry=site_h.cand(bbox=real, source="frozen_range"),
        frozen_disposition_id="d-1", frozen_disposition_locator="loc-d-1",
        frozen_overlap_ratio=0.5, competing_disposition_ids=("d-2",),
        union_disposition_ids=("d-1", "d-2"))
    check(TB.evaluate_candidate_gate(ctx_h, ok).admission_basis
          == "contiguous_frozen_union",
          "A3 同一串成员、矩形等于并集时必须准入（否则上一条否错了对象）")
    # 单成员冒充并集：`union_disposition_ids` 长度 1 ⇒ 不得走并集通道。
    solo = TB.BuilderCandidate(
        candidate_source="frozen_range", strategy="lines", page_number=1,
        bbox=real, geometry=site_h.cand(bbox=real, source="frozen_range"),
        frozen_disposition_id="d-1", frozen_disposition_locator="loc-d-1",
        frozen_overlap_ratio=0.5, competing_disposition_ids=(),
        union_disposition_ids=("d-1",))
    check(TB.evaluate_candidate_gate(ctx_h, solo).admitted is False,
          "A3 只有一个成员时不得走并集通道")

    # (i) 反例⑦：并集框里网格不闭合 ⇒ 如实拒绝（与逐范围候选同一形状门）。
    page_i = _grid_page(missing=((2, 1),))
    _si, _di, ctx_i = _split_site(page_i, ((0, 1), (2, 3)))
    cands_i = _union_cands(ctx_i)
    check(len(cands_i) == 1, "A3 前置：带空洞的页仍应产出并集候选")
    gate_i = TB.evaluate_candidate_gate(ctx_i, cands_i[0])
    check(gate_i.admitted is False and gate_i.reason == "grid_not_closed"
          and gate_i.blocks_document is False,
          f"A3 并集框内网格不闭合必须如实拒绝（reason={gate_i.reason}）")


def _whole_table_site(*, tops=(20.0, 33.0, 46.0, 59.0, 72.0), groups=((0, 1),
                                                                      (2, 3)),
                      node_ids=None, regular=(), cand_hi=82.0):
    """一张**整张物理表**的合成页：表体被切成多段冻结范围，候选比它们更大。

    候选多出来的部分是同一张表的最后一行（合计行）：它在真实几何里有片段，
    但不在任何冻结表范围里 —— 正是"整张物理表 vs 被切开的文字行段"的形状。
    """
    page = _grid_page(tops=tops)
    site, ds, _ = _split_site(page, groups, node_ids=node_ids, regular=regular)
    bbox = (_union(page.lines)[0], _union(page.lines)[1],
            _union(page.lines)[2], cand_hi)
    rows = len(tops)
    return page, site, ds, bbox, site.cand(
        bbox=bbox, source="pdfplumber_lines", closed=True, gaps=0,
        grid_cells=_skeleton(bbox, rows=rows, cols=2), row_count=rows,
        column_count=2, cell_count=rows * 2)


def _geom_cands(ctx):
    return [c for c in TB.enumerate_candidates(ctx)
            if c.candidate_source != "frozen_range"]


def _test_a4(live):
    """乙的构建侧：一张物理表对应**一个有界候选**，且不得把相邻两表并成一个。

    `_contained_frozen_run` 六条判据逐条正反例：正例是"整张物理表包含一段连续
    冻结范围子串"；反例覆盖"第三段只落进来一部分"（＝截取一半）、"被包含集合不是
    某条串的连续子串"、"只包含一段"（乙不得吞掉既有的单 owner 裁定）、
    "候选无网格骨架"、"多出来的部分碰到同页冻结 `regular`"、"同页范围不可回查"。
    `_union_extends` 判据 ③ 另钉"两端同为 `None` 不构成同节点证据"。
    """
    check("contained_frozen_run" in TS.CANDIDATE_ADMISSION_BASES,
          "A4 依据码必须登记在封闭依据码集内")

    # (a) 正例：候选是整张物理表（多出表底合计行），包含一段 ≥2 的连续范围子串。
    page, site, ds, bbox, geom = _whole_table_site()
    ctx = site.context(dispositions=ds, candidates=(geom,))
    check([[d.disposition_id for d in r] for r in TB._frozen_runs(ctx)]
          == [["d-1", "d-2"]],
          "A4 前置：两段同节点相邻范围必须聚成一条串")
    cands = _geom_cands(ctx)
    check(len(cands) == 1, f"A4 前置：本页应只有一个几何候选（{len(cands)}）")
    cand = cands[0]
    check(cand.frozen_disposition_id == "d-1"
          and cand.competing_disposition_ids == ("d-2",)
          and 0.0 < cand.frozen_overlap_ratio < TB.CANDIDATE_CONTAINED_RATIO,
          f"A4 前置：整表候选的交占比必须**如实**低于包含阈值"
          f"（{cand.frozen_disposition_id} / {cand.competing_disposition_ids} / "
          f"{cand.frozen_overlap_ratio}）")
    check(TB._contained_frozen_owner(ctx, cand) is False,
          "A4 前置：两段都被包含时 `contained_frozen_owner` 必须判否"
          "（它要求恰好一个 owner）")
    check(TB._contained_frozen_run(ctx, cand) is True,
          "A4 正例：整张物理表包含连续两段冻结范围时必须裁定通过")
    gate = TB.evaluate_candidate_gate(ctx, cand)
    check(gate.admitted is True and gate.reason is None
          and gate.blocks_document is False
          and gate.admission_basis == "contained_frozen_run",
          f"A4 正例：准入依据码必须单独具名（admitted={gate.admitted} "
          f"reason={gate.reason} basis={gate.admission_basis}）")
    check(gate.grid is not None and gate.grid.row_count == 5
          and gate.grid.column_count == 2,
          f"A4 正例：准入后必须是整表框上的真实网格（"
          f"{None if gate.grid is None else (gate.grid.row_count, gate.grid.column_count)}）")
    # 反例对照：同一候选若无网格骨架，判据 ① 必须挡住它。
    no_grid = TB.BuilderCandidate(
        candidate_source="pdfplumber_lines", strategy="lines", page_number=1,
        bbox=bbox, geometry=site.cand(bbox=bbox, source="pdfplumber_lines",
                                      closed=False, grid_cells=()),
        frozen_disposition_id="d-1", frozen_disposition_locator="loc-d-1",
        frozen_overlap_ratio=cand.frozen_overlap_ratio,
        competing_disposition_ids=("d-2",))
    check(TB._contained_frozen_run(ctx, no_grid) is False,
          "A4 反例①：候选自带 `grid_cells` 为空时不得走乙通道")

    # (b) 反例③：同页还有第三段，只落进来一部分 ⇒ 判否（§0.19 不得截取一半）。
    page_b = _grid_page(tops=(20.0, 33.0, 46.0, 59.0, 72.0, 85.0))
    site_b, ds_b, _ = _split_site(page_b, ((0, 1), (2, 3), (4, 5)))
    cut = (_union(page_b.lines)[0], _union(page_b.lines)[1],
           _union(page_b.lines)[2], 78.0)
    geom_b = site_b.cand(bbox=cut, source="pdfplumber_lines", closed=True,
                         gaps=0, grid_cells=_skeleton(cut, rows=5, cols=2),
                         row_count=5, column_count=2, cell_count=10)
    ctx_b = site_b.context(dispositions=ds_b, candidates=(geom_b,))
    check([[d.disposition_id for d in r] for r in TB._frozen_runs(ctx_b)]
          == [["d-1", "d-2", "d-3"]],
          "A4 前置(b)：三段同节点相邻范围必须聚成一条串")
    check(all(TB._contained_frozen_run(ctx_b, c) is False
              for c in _geom_cands(ctx_b)),
          "A4 反例③：有范围只落进来一部分时必须判否（不得把一张表截取一半）")
    gate_b = TB.evaluate_candidate_gate(ctx_b, _geom_cands(ctx_b)[0])
    check(gate_b.admitted is False
          and gate_b.reason == "candidate_straddles_frozen_range"
          and gate_b.blocks_document is False,
          f"A4 反例③：门理由必须仍是交占比不足那一条（reason={gate_b.reason}）")

    # (c) 反例④：被包含集合**不是**某条串的连续子串（中间换了已验证节点）。
    site_c, ds_c, _ = _split_site(page_b, ((0, 1), (2, 3), (4, 5)),
                                  node_ids=("n1", "n2", "n2"))
    full = (_union(page_b.lines)[0], _union(page_b.lines)[1],
            _union(page_b.lines)[2], 97.0)
    geom_c = site_c.cand(bbox=full, source="pdfplumber_lines", closed=True,
                         gaps=0, grid_cells=_skeleton(full, rows=6, cols=2),
                         row_count=6, column_count=2, cell_count=12)
    ctx_c = site_c.context(dispositions=ds_c, candidates=(geom_c,))
    check([[d.disposition_id for d in r] for r in TB._frozen_runs(ctx_c)]
          == [["d-1"], ["d-2", "d-3"]],
          "A4 前置(c)：换节点处必须断开串")
    check(all(TB._contained_frozen_run(ctx_c, c) is False
              for c in _geom_cands(ctx_c)),
          "A4 反例④：被包含集合跨两条串时不得裁定通过"
          "（否则相邻两张不同表会被并成一个候选）")

    # (d) 反例下限：只包含**一段**时乙必须判否；此时既有的单 owner 裁定照旧生效，
    #     证明乙只覆盖"≥2 段"，没有顺手放宽单段情形。
    page_d = _grid_page(tops=(20.0, 33.0, 46.0, 59.0, 72.0, 85.0))
    site_d, ds_d, _ = _split_site(page_d, ((0, 1), (3, 4)))
    one = (_union(page_d.lines)[0], _union(page_d.lines)[1],
           _union(page_d.lines)[2], 58.0)
    geom_d = site_d.cand(bbox=one, source="pdfplumber_lines", closed=True,
                         gaps=0, grid_cells=_skeleton(one, rows=3, cols=2),
                         row_count=3, column_count=2, cell_count=6)
    ctx_d = site_d.context(dispositions=ds_d, candidates=(geom_d,))
    check(all(TB._contained_frozen_run(ctx_d, c) is False
              for c in _geom_cands(ctx_d)),
          "A4 反例⑤：只包含一段时乙不得裁定通过")
    check(all(TB._contained_frozen_owner(ctx_d, c) is True
              for c in _geom_cands(ctx_d)),
          "A4 对照⑤：只包含一段时既有的单 owner 裁定照旧成立"
          "（乙不是它的替代）")

    # (e) 反例⑥：候选多出来的部分碰到同页冻结 `regular` body span ⇒ 判否。
    site_e, ds_e, _ = _split_site(page, ((0, 1), (2, 3)), regular=((4, 4),))
    geom_e = site_e.cand(bbox=bbox, source="pdfplumber_lines", closed=True,
                         gaps=0, grid_cells=_skeleton(bbox, rows=5, cols=2),
                         row_count=5, column_count=2, cell_count=10)
    ctx_e = site_e.context(dispositions=ds_e, candidates=(geom_e,))
    check([[d.disposition_id for d in r] for r in TB._frozen_runs(ctx_e)]
          == [["d-1", "d-2"]],
          "A4 前置(e)：表体两段仍成串（`regular` 不在并集框内）")
    check(all(TB._overlaps_frozen_regular(ctx_e, c) for c in _geom_cands(ctx_e))
          and all(TB._contained_frozen_run(ctx_e, c) is False
                  for c in _geom_cands(ctx_e)),
          "A4 反例⑥：多出来的部分碰到冻结 `regular` 正文时必须判否")

    # (f) 反例②：同页同类范围里有一段真实矩形**不可回查** ⇒ 判否（证明不了没吞下它）。
    ds_f = list(ds) + [_Disposition("d-9", "table_inside", "n1", 1, 1, 40, 41)]
    ctx_f = site.context(dispositions=ds_f, candidates=(geom,))
    check(all(TB._contained_frozen_run(ctx_f, c) is False
              for c in _geom_cands(ctx_f)),
          "A4 反例②：同页有范围不可回查真实矩形时不得裁定通过")

    # (g) 判据 ③ 的唯一实现：`_union_extends` 只在两端**都非 `None` 且相等**时成立。
    for node_ids, want_runs, label in (
            (("n1", "n1"), [["d-1", "d-2"]], "同节点 ⇒ 成串"),
            ((None, None), [["d-1"], ["d-2"]], "两端同为 None ⇒ 必须分段"),
            (("n1", "n2"), [["d-1"], ["d-2"]], "不同节点 ⇒ 必须分段")):
        site_g, ds_g, _ = _split_site(page, ((0, 1), (2, 3)),
                                      node_ids=node_ids)
        ctx_g = site_g.context(dispositions=ds_g)
        got = [[d.disposition_id for d in r] for r in TB._frozen_runs(ctx_g)]
        check(got == want_runs,
              f"A4 判据③：{label}（实测 {got}）")
        check(bool(_union_cands(ctx_g)) == (len(want_runs) == 1),
              f"A4 判据③：{label} 时并集候选的有无必须与串数一致"
              f"（{[c.union_disposition_ids for c in _union_cands(ctx_g)]}）")
    d1 = ds[0]
    d2 = ds[1]
    box1 = TB.frozen_range_bbox(ctx, d1)
    box2 = TB.frozen_range_bbox(ctx, d2)
    union_box = (min(box1[0], box2[0]), min(box1[1], box2[1]),
                 max(box1[2], box2[2]), max(box1[3], box2[3]))
    check(TB._union_extends(None, box1, d2, box2, union_box, ()) is False
          and TB._union_extends("n1", box1, d2, box2, union_box, ()) is True,
          "A4 判据③：`_union_extends` 本体的两端非 None 判据必须与串一致")

    # (h) 真实现场：乙必须**确实**在这份文档上生效，且不得让同页两张表互相重叠
    #     （跨表格值不混入）。两者都只看生产读数，不依赖页码/表名。
    if not _gate(live):
        return
    audit = live["built"].audit
    run_rows = [r for r in audit if r.admission_basis == "contained_frozen_run"]
    check(bool(run_rows),
          "A4 现场：乙必须在本份文档上至少准入一张表（否则本组没有覆盖真实路径）")
    check(all(r.outcome == "accepted" and r.reason is None for r in run_rows),
          "A4 现场：`contained_frozen_run` 只允许出现在 accepted 行上")
    check(all(r.admission_basis is None
              or r.admission_basis in TS.CANDIDATE_ADMISSION_BASES
              for r in audit),
          "A4 现场：每个非空依据码都必须在封闭依据码集内")
    owner_rows = [r for r in audit
                  if r.admission_basis == "contained_frozen_owner"]
    check(bool(owner_rows),
          "A4 现场：单 owner 路径必须仍在生产（乙不是它的替代）")
    by_page: dict = {}
    for t in live["built"].tables:
        by_page.setdefault(t.table.page_number, []).append(t.table.page_bbox)
    clashes = []
    for pn, boxes in sorted(by_page.items()):
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                over = ((min(a[2], b[2]) - max(a[0], b[0]))
                        * (min(a[3], b[3]) - max(a[1], b[1])))
                if over > 0.0:
                    clashes.append((pn, a, b, round(over, 3)))
    check(not clashes,
          f"A4 现场：同页两张已准入表不得互相重叠（跨表格值不混入），"
          f"反例 {clashes[:2]}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

_GROUPS = (("G1", _test_g1), ("G2", _test_g2), ("G3", _test_g3),
           ("G4", _test_g4), ("G5", _test_g5), ("G6", _test_g6),
           ("G7", _test_g7), ("G8", _test_g8), ("G9", _test_g9),
           ("A1", _test_a1), ("A2", _test_a2), ("A3", _test_a3),
           ("A4", _test_a4))


def _run_group(name, fn, live):
    started = time.time()
    before = _results["passed"] + _results["failed"]
    try:
        fn(live)
    except Exception as error:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(f"FAIL {name} 未捕获异常："
                                   f"{type(error).__name__}: {error}")
    finally:
        _results["details"].append(
            f"__{name}__ {time.time() - started:.1f}s "
            f"(checks={_results['passed'] + _results['failed'] - before})")


def main() -> dict:
    started = time.time()
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _db_identity(_DB_PATH) if _DB_PATH.exists() else None
    original_extract = _install_geometry_memo()
    try:
        _estore._db_path = _DB_PATH
        live = _live()
        for name, fn in _GROUPS:
            _run_group(name, fn, live)
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


if __name__ == "__main__":  # pragma: no cover
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

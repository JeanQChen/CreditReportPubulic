# -*- coding: utf-8 -*-
"""TS5 §19.12.3-8 与 §19.12.2A-3/4/5/6/8 与 §19.12.6-1/2：cell 来源、可引用性与账本。

覆盖：

- **C1** §19.12.3-8：两个**表内小标题** fixture（一个靠字体信号、一个靠通用编号前缀）
  的全文（cell text 与逐块 text 必须由真实 `LayoutSpan` 逐字符重算得到）、blocks
  （有序、角色、每个来源片段恰被一个块消费）、locator（cell bbox 与每个来源片段的
  物理位置/派生身份可独立重算）与 component 回查（component → Evidence 区间 →
  alignment 终态）完整；且生产代码零硬编码（机械扫源码 + 两个 self-check + 角色必须
  能由登记 profile 的真值表在**真实信号**上复算）。
- **C2** §19.12.2A-3：`body_unassigned` / `formal_unassigned` 只有 **exact**
  geometry/provenance 闭合才进入 table；片段级"未闭合"绝不进 cell（只留在 unclosed，
  且强制全部块角色 fail-closed 成 `unclassified`）。
- **C3** §19.12.2A-4：`heading_node` / `non_content` / `outside_body` 与 table 冲突时
  只能是 rejected 或 pending(+upstream gap)，**不得被静默吸收**成 table_object。
- **C4** §19.12.2A-5：`alignment_offset_unverifiable` / `alignment_residue_unmapped`
  永远 pending 且逐段不可引用；empty 只作为带理由的 rejected 账本，绝不进 cell。
- **C5** §19.12.2A-6：同一 component 跨同一表多个 cell 时，Evidence 子区间**有序、
  互斥、无洞且并集相等**；跨 table / 跨 terminal 时 fail-closed 成 pending，
  且**不复制 binding**（每个 component 恰一条 binding）。
- **C6** §19.12.2A-8（账本层半边）：删除一个 component、重复消费、端点改变、制造
  洞/重叠 ⇒ 账本检测器必须抓出；rejected 来源的终态由 landing 封闭映射决定，
  账本无法把它洗成 table。
- **C7** §19.12.6-1：每个 cell source ref 逐跳回查
  `LayoutSpan → TS4 component → Evidence range → alignment`，含身份重算。
- **C8** §19.12.6-2：aligned / non-citable 混合表**不得整表授权**——可引用性逐 cell
  source ref 记录，且把非可引用区间改写成"可引用"必被实测理由集抓住。

**只读与性能**：真实链只跑**一次**并缓存（`_live`），且只走到
`_read_roots → _build_context → build_tables → _build_final_spans → _build_decisions
→ _measure_consumption → _build_bindings → _build_coverages`（不建 final 快照、
不跑守恒 / verifier）。`TG.extract_table_geometry` 在本进程内按
`(layout capability, pages)` 记忆化：它是对同一 capability 的纯函数，被测代码读到的
仍是同一份报告，断言强度不变。

**合成夹具**：两个表内小标题与"部分闭合片段"等反例形状在现场文档里不一定存在，
因此用合成版式 + 合成 terminal/component 构造，但**只**经生产类型与生产函数进入
（`AlignmentTerminal` / `TableBuildContext` / `derive_cell_grid` /
`evaluate_candidate_gate` / `build_table` / `assemble_cell` /
`TableCellSourceRef.create` / `TableCitableCoverage.create` /
`component_partition_problems` / `_measure_consumption` / `_binding_for` /
`_build_bindings`），没有旁路入口。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from evidence import store as _estore

from document_structure import aligner as AL
from document_structure import final_material_builder as FMB
from document_structure import layout_builder as LB
from document_structure import span_builder as SB
from document_structure import span_schema as SS
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
    """跑一次真实链路到 cell / coverage 层并缓存（**不**建 final 快照）。"""
    if "coverages" in _LIVE or "error" in _LIVE:
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
        tables = tuple(sorted((bt.table for bt in built.tables),
                              key=lambda t: t.table_locator))
        consumption = FMB._measure_consumption(ctx, tuple(built.tables))
        bindings = FMB._build_bindings(
            root, consumption, decisions, span_build.span_of_component)
        coverages = FMB._build_coverages(root, ctx, tables)
        _LIVE.update({
            "verified": verified, "root": root, "ctx": ctx, "built": built,
            "tables": tables,
            "grids": tuple(bt.grid for bt in built.tables),
            "decisions": decisions, "consumption": consumption,
            "bindings": bindings, "final_spans": span_build.spans,
            "span_of_component": span_build.span_of_component,
            "coverages": coverages, "profiles": ctx.profiles, "handoff": handoff})
    except Exception as error:  # noqa: BLE001
        _LIVE["error"] = f"{type(error).__name__}: {error}"
    return _LIVE


def _gate(live, msg="真实链路必须成立"):
    if live.get("error"):
        check(False, f"{msg} —— 现场链路失败：{live['error']}")
        return False
    if "coverages" not in live:
        check(False, f"{msg} —— 现场链路未产出 cell / coverage 结果")
        return False
    return True


# ---------------------------------------------------------------------------
# 1. 合成版式夹具（内部自洽；只读，不接触仓库资产）
# ---------------------------------------------------------------------------

_PAGE_W = 600.0
_PAGE_H = 800.0

#: 表内小标题 fixture 的候选矩形与 2×2 骨架（`(0,0)` 为 rowspan=2 的合并锚点）。
#:
#: 左列的两个片段必须落在**行号相差 2**的两行上：`tcbp-1` 的
#: `max_intra_group_line_gap=1` 会把相邻行并成**同一**块，那样表内小标题就不会
#: 单独成块。因此第 1 行只有右列片段。
_CAP_BBOX = (20.0, 20.0, 100.0, 72.0)
_CAP_SKELETON = ((0, 0, 2, 1, (20.0, 20.0, 60.0, 72.0)),
                 (0, 1, 1, 1, (60.0, 20.0, 100.0, 46.0)),
                 (1, 1, 1, 1, (60.0, 46.0, 100.0, 72.0)))
_CAP_MERGED = ((0, 0, 2, 1),)

#: 两个 fixture 的**通用**文本（不含任何公司 / 代码 / 页码 / 表号形状）。
_CAP_BOLD = "小标题"
_CAP_NUMBERED = "（1）项目"
_CAP_HEAD = "表头"
_CAP_BODY = "明细内容。"
_CAP_VALUE = "值甲"

#: §19.12.3-8 的"零硬编码"机械反例：fixture 专属字面量与案例标识都不得出现在生产
#: 模块里。这里**不**放"小标题 / 表头"这类领域通用词——它们本来就属于生产注释用词。
_FORBIDDEN_LITERALS = ("NDSD", "宁德", "300750", "CATL", "（1）项目", "明细内容。",
                       "值甲")
_COMPANY_LITERALS = ("NDSD", "宁德", "300750", "CATL")
_PRODUCTION_MODULES = ("table_builder.py", "table_classification.py",
                       "table_schema.py", "table_geometry.py",
                       "final_material_builder.py", "final_verifier.py")


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

    def cand(self, *, page_number=1, bbox, source="frozen_range",
             strategy="lines", closed=False, gaps=1, edges=0,
             intersections=0, grid_cells=(), row_count=0, column_count=0,
             cell_count=0, merged=()):
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

    def context(self, *, dispositions=(), components=(), terminals=()):
        report = TG.TableGeometryReport(
            source_file_sha256=self.layout.source_file_sha256,
            page_layout_id=self.layout.page_layout_id,
            settings=TG.geometry_settings("lines"),
            frames=tuple(self.frames[p.page_number]
                         for p in self.layout.pages),
            candidates=(), unresolved_pages=())
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
            terminals=tuple(terminals), components=tuple(components),
            dispositions=tuple(dispositions), spans=(),
            qualification_policy=None, profiles=_profiles(),
            geometry=report, upstream_dependency_fingerprint="b" * 64)


def _flag_candidate(site, disposition, bbox, source, **kw):
    """按通道构造生产类型的候选（`geometry` 与 `candidate_source` 同步）。"""
    strategy = kw.pop("strategy", "lines")
    return TB.BuilderCandidate(
        candidate_source=source, strategy=strategy, page_number=1, bbox=bbox,
        geometry=site.cand(bbox=bbox, source=source, strategy=strategy, **kw),
        frozen_disposition_id=disposition.disposition_id,
        frozen_disposition_locator=disposition.disposition_locator,
        frozen_overlap_ratio=1.0, competing_disposition_ids=())


# ---------------------------------------------------------------------------
# 2. 合成 terminal / component（只含被测代码读取的字段）
# ---------------------------------------------------------------------------

class _Hit:
    __slots__ = ("page_number", "line_index", "layout_span_index")

    def __init__(self, page_number, line_index, layout_span_index):
        self.page_number = int(page_number)
        self.line_index = int(line_index)
        self.layout_span_index = int(layout_span_index)


def _terminal(*, evidence_block_id, terminal_id, page, line, span, length, ev0,
              kind="alignment", verdict="aligned", refusal_reason=None):
    """真实 `AlignmentTerminal`：`char_map` 把片段内偏移 1:1 映射到 Evidence 区间。"""
    if kind == "refusal":
        verdict = None
        refusal_reason = refusal_reason or "recorded_refusal"
    # `_evidence_segments` 读的 6 元组是 `(evidence_start, evidence_end, 页, 行,
    # 片段索引, 片段内起点)`：这里让片段 [0, length) 恰好映射到 [ev0, ev0+length)。
    seg = (int(ev0), int(ev0) + int(length), int(page), int(line), int(span), 0)
    return AL.AlignmentTerminal(
        page_number=int(page), block_index=int(line),
        evidence_block_id=evidence_block_id, evidence_set_version="esv-syn-1",
        terminal_kind=kind, terminal_id=terminal_id,
        terminal_locator="loc-" + terminal_id,
        terminal_schema_version=V.ALIGN_SCHEMA_VERSION,
        aligner_version=V.ALIGNER_VERSION,
        partition_validator_version=V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        verdict=verdict, refusal_reason=refusal_reason, residue_class=None,
        block_char_length=int(length), matched_chars=int(length),
        exact_coverage=1.0, char_map=(seg,), residue=())


def _component(cid, *, page, line, span, length, ev0, landing="body_span",
               disposition_id="d-1", span_id="sp-syn-1", admitted=True,
               admission_reason="aligned_projected", kind="alignment",
               verdict="aligned", partial=None):
    """合成 component + 与它同源的真实 terminal（两者共用同一 `char_map` 口径）。"""
    term = _terminal(evidence_block_id="evb-" + cid, terminal_id="al-" + cid,
                     page=page, line=line, span=span, length=length, ev0=ev0,
                     kind=kind, verdict=verdict)
    if partial is not None:
        # 只覆盖片段前 `partial` 个字符：整段映射无法闭合。
        seg = (int(ev0), int(ev0) + int(partial), int(page), int(line),
               int(span), 0)
        term = dataclasses.replace(term, char_map=(seg,))
    comp = SimpleNamespace(
        component_id=cid, component_locator="loc-" + cid,
        evidence_block_id="evb-" + cid,
        evidence_char_range=(int(ev0), int(ev0) + int(length)),
        layout_hits=(_Hit(page, line, span),), landing=landing,
        disposition_id=disposition_id, admitted=bool(admitted),
        admission_reason=admission_reason, span_id=span_id)
    return comp, term


def _caption_fixture(caption, *, bold):
    """2×2 骨架版式：`(0,0)` 是 rowspan=2 的 cell，内含表内小标题（行 0）+ 正文（行 2）。"""
    page = _Page(1, [
        _Line(0, [_Span(20.0, 22.0, 55.0, 34.0, text=caption,
                        size=(12.0 if bold else 10.0), bold=bold),
                  _Span(62.0, 22.0, 98.0, 34.0, text=_CAP_HEAD)]),
        _Line(1, [_Span(62.0, 38.0, 98.0, 50.0, text=_CAP_HEAD)]),
        _Line(2, [_Span(20.0, 54.0, 55.0, 68.0, text=_CAP_BODY),
                  _Span(62.0, 54.0, 98.0, 68.0, text=_CAP_VALUE)])])
    comps, terms = [], []
    layout = (("c-0-0", 0, 0, caption), ("c-0-1", 0, 1, _CAP_HEAD),
              ("c-1-1", 1, 1, _CAP_HEAD), ("c-2-0", 2, 0, _CAP_BODY),
              ("c-2-1", 2, 1, _CAP_VALUE))
    for i, (cid, line, span, text) in enumerate(layout):
        comp, term = _component(cid, page=1, line=line, span=span,
                                length=len(text), ev0=100 + 10 * i)
        comps.append(comp)
        terms.append(term)
    return page, comps, terms


def _recomputed_block_text(ctx, refs):
    """**独立**由真实 LayoutSpan 复算块文本（不读 `block.text`）。"""
    parts: list = []
    prev = None
    for ref in sorted(refs, key=lambda r: (r.interval.page_number,
                                           r.interval.line_index,
                                           r.interval.span_index,
                                           r.interval.span_char_range[0])):
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


# ---------------------------------------------------------------------------
# C1 §19.12.3-8：两个表内小标题 fixture
# ---------------------------------------------------------------------------

def _caption_case(label, caption, *, bold):
    page, comps, terms = _caption_fixture(caption, bold=bold)
    site = _Site([page], nodes=[_Node("n1")])
    d = _Disposition("d-1", "table_inside", "n1", 1, 1, 0, len(page.lines) - 1)
    ctx = site.context(dispositions=[d], components=comps, terminals=terms)

    cand = site.cand(bbox=_CAP_BBOX, closed=True, gaps=0, edges=4,
                     intersections=4, row_count=2, column_count=2, cell_count=3,
                     merged=_CAP_MERGED, grid_cells=_CAP_SKELETON)
    grid = TG.derive_cell_grid(page, candidate=cand)
    gate = TB.evaluate_candidate_gate(
        ctx, _flag_candidate(site, d, _CAP_BBOX, "frozen_range", closed=True,
                             gaps=0, edges=4, intersections=4, row_count=2,
                             column_count=2, cell_count=3, merged=_CAP_MERGED,
                             grid_cells=_CAP_SKELETON))

    check(grid.closed and grid.geometry_backed
          and [(c.row, c.column) for c in grid.cells]
          == [(0, 0), (0, 1), (1, 1)],
          f"C1/{label} 骨架网格必须闭合且保留合并锚点（problems={grid.problems}）")
    anchor = next(c for c in grid.cells if (c.row, c.column) == (0, 0))
    check(anchor.fragment_keys == ((0, 0, 0), (2, 0, 0)),
          f"C1/{label} 表内小标题所在 cell 必须消费两行（行 0 与行 2）的真实片段，"
          f"得到 {anchor.fragment_keys}")
    check(gate.admitted and gate.grid is not None,
          f"C1/{label} 带真实合并骨架的候选必须过门（reason={gate.reason}）")

    asm = TB.assemble_cell(ctx, anchor, page_number=1, row=0)
    cell = asm.cell
    want_roles = ["heading", "paragraph"]
    want_texts = [caption, _CAP_BODY]

    check([b.role for b in cell.blocks] == want_roles,
          f"C1/{label} 表内小标题必须作为 role=heading 的 cell block **保留**在 cell 内"
          f"（得到 {[b.role for b in cell.blocks]}）")
    check([b.block_index for b in cell.blocks] == list(range(len(cell.blocks))),
          f"C1/{label} 块序号必须从 0 连续递增")
    check([b.text for b in cell.blocks] == want_texts,
          f"C1/{label} 逐块文本必须是原文（得到 {[b.text for b in cell.blocks]}）")
    check(cell.text == caption + _SEP + _CAP_BODY,
          f"C1/{label} cell 全文必须由 blocks 按冻结分隔规则重构"
          f"（得到 {cell.text!r}）")

    recomputed = []
    for b in cell.blocks:
        own = [r for r in cell.source_refs
               if r.source_ref_id in set(b.source_ref_ids)]
        recomputed.append(_recomputed_block_text(ctx, own))
    check(recomputed == want_texts,
          f"C1/{label} 全文必须能由真实 LayoutSpan 逐字符重算（{recomputed}）")
    check(all(len(b.text) == sum(r.interval.span_char_range[1]
                                 - r.interval.span_char_range[0]
                                 for r in cell.source_refs
                                 if r.source_ref_id in set(b.source_ref_ids))
              for b in cell.blocks),
          f"C1/{label} 块文本长度必须等于其来源片段长度之和（无自报字符）")

    ids = [rid for b in cell.blocks for rid in b.source_ref_ids]
    check(sorted(ids) == sorted(r.source_ref_id for r in cell.source_refs)
          and len(set(ids)) == len(ids),
          f"C1/{label} blocks 必须**完整**覆盖 cell 的每个来源片段且不得重复消费")
    consumed_ok = set(asm.consumed) == {"c-0-0", "c-2-0"}
    for cid, ranges in asm.consumed.items():
        comp = next((c for c in ctx.components if c.component_id == cid), None)
        consumed_ok = consumed_ok and comp is not None \
            and tuple(ranges) == (comp.evidence_char_range,)
    check(consumed_ok,
          f"C1/{label} cell 消费账本必须逐 component 精确等于其 Evidence 全区间"
          f"（{asm.consumed}）")
    check(tuple(asm.unclosed) == (),
          f"C1/{label} 该 cell 不得有未闭合片段（{asm.unclosed}）")

    # locator：物理位置与派生身份都必须可独立重算。
    check(tuple(cell.bbox) == (20.0, 20.0, 60.0, 72.0),
          f"C1/{label} cell bbox 必须等于其真实骨架锚点（{cell.bbox}）")
    loc_ok, identity_ok, comp_ok = True, True, True
    for r in cell.source_refs:
        span = ctx.layout_span(r.interval.page_number, r.interval.line_index,
                               r.interval.span_index)
        if span is None or tuple(span.bbox) != tuple(r.interval.bbox) \
                or (r.interval.span_char_range[1]
                    - r.interval.span_char_range[0]) \
                != span.char_end - span.char_start \
                or r.interval.span_char_range != (0, len(span.text)):
            loc_ok = False
        rebuilt = TS.TableCellSourceRef.create(
            interval=r.interval, component_id=r.component_id,
            component_locator=r.component_locator,
            evidence_block_id=r.evidence_block_id,
            evidence_char_range=r.evidence_char_range,
            terminal_kind=r.terminal_kind, terminal_id=r.terminal_id,
            terminal_locator=r.terminal_locator,
            terminal_schema_version=r.terminal_schema_version,
            verdict=r.verdict, refusal_reason=r.refusal_reason)
        if rebuilt.source_ref_id != r.source_ref_id or \
                TS.TableCellSourceRef.from_dict(r.to_dict()).source_ref_id \
                != r.source_ref_id:
            identity_ok = False
        comp = next((c for c in ctx.components
                     if c.component_id == r.component_id), None)
        if comp is None or r.component_locator != comp.component_locator \
                or r.evidence_block_id != comp.evidence_block_id \
                or comp.layout_hits[0].line_index != r.interval.line_index \
                or comp.layout_hits[0].layout_span_index != r.interval.span_index:
            comp_ok = False
            continue
        if TB.map_span_range_to_evidence(
                ctx, comp, r.interval.page_number, r.interval.line_index,
                r.interval.span_index, r.interval.span_char_range[0],
                r.interval.span_char_range[1]) != r.evidence_char_range:
            comp_ok = False
        term = ctx.terminal_for(comp)
        if r.terminal_id != term.terminal_id \
                or r.terminal_locator != term.terminal_locator \
                or r.terminal_schema_version != term.terminal_schema_version \
                or r.citable is not True \
                or r.citable_reason != TS.CITABLE_REASON_TRUE \
                or r.alignment_id != r.terminal_id:
            comp_ok = False
    check(loc_ok,
          f"C1/{label} 每个来源片段必须定位到真实 LayoutSpan（bbox + 整段区间）")
    check(identity_ok,
          f"C1/{label} 来源身份必须是 interval+component+terminal 的派生量"
          f"（可重算、可回读）")
    check(comp_ok,
          f"C1/{label} component 回查必须完整：component → Evidence 区间 → "
          f"alignment 终态（含逐段可引用性）")

    # 角色必须能由登记 profile 的真值表在**真实信号**上复算（不是写死的角色）。
    bundle = _profiles().cell_block
    role_ok = True
    for b in cell.blocks:
        group = [r for r in cell.source_refs
                 if r.source_ref_id in set(b.source_ref_ids)]
        signals = TB._cell_block_signals(
            ctx, group, group_count=len(cell.blocks), index=b.block_index,
            unclosed_inputs=False,
            cell_width=float(cell.bbox[2] - cell.bbox[0]))
        role, code = TC.classify_cell_block_role(bundle, signals)
        role_ok = role_ok and role == b.role and code == "rule_" + b.role
    check(role_ok,
          f"C1/{label} 每个块角色都必须等于「真实信号 + 登记真值表」的复算结果")

    # 表内小标题**不**进入标题树。
    check(len(ctx.outline.nodes) == 1
          and all(caption not in "".join(n.structural_path)
                  for n in ctx.outline.nodes),
          f"C1/{label} 表内小标题只作为 cell block 保留，**不得**成为 OutlineNode")

    # 篡改：块载荷指纹必须与块内容自洽；cell 文本必须由 blocks 重构。
    raises(lambda: dataclasses.replace(cell.blocks[0], role="paragraph"),
           Exception, "与载荷重算不一致",
           f"C1/{label} 改写块角色却沿用旧载荷指纹必须被拒绝")
    raises(lambda: dataclasses.replace(cell.blocks[1], text="AAAA"),
           Exception, "与载荷重算不一致",
           f"C1/{label} 改写块文本却沿用旧载荷指纹必须被拒绝")
    raises(lambda: dataclasses.replace(cell, text="拼凑的全文"),
           Exception, "必须由 blocks 以冻结分隔规则重构",
           f"C1/{label} 自报 cell 全文必须被拒绝")

    table = None
    try:
        built = TB.build_table(ctx, cand, gate, source_order_index=0)
        table = built.table
    except Exception as error:  # noqa: BLE001
        check(False, f"C1/{label} 该合成表必须能装配成功，实际 "
                     f"{type(error).__name__}: {error}")
    if table is not None:
        check(any([b.role for b in c.blocks] == want_roles
                  and c.text == caption + _SEP + _CAP_BODY
                  for rw in table.rows for c in rw.cells),
              f"C1/{label} 表内小标题必须作为表内 cell 的 heading 块出现在表里")
        check(all(b.text.strip() != "" for rw in table.rows for c in rw.cells
                  for b in c.blocks),
              f"C1/{label} 表内不得出现空块（empty 不进 cell）")
        check(len(ctx.outline.nodes) == 1,
              f"C1/{label} 装配整表也不得新增 OutlineNode")
    return site, ctx, cell, table


def _deny_list_source_lines(path):
    """已登记反硬编码 deny-list 常量的源码文本（唯一允许案例字面量的地方）。"""
    out, depth, started = [], 0, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not started:
            if not line.startswith("_FORBIDDEN_PROFILE_TOKEN_PATTERNS"):
                continue
            started = True
        depth += line.count("(") - line.count(")")
        out.append(line)
        if depth == 0:
            break
    return set(out)


def _test_c1(live):
    _caption_case("A 字体信号", _CAP_BOLD, bold=True)
    _caption_case("B 通用编号前缀", _CAP_NUMBERED, bold=False)

    # 生产代码零硬编码：fixture 字面量与案例标识都不得出现在生产模块里。
    # 唯一豁免是**已登记的反硬编码 deny-list 本身**（`_FORBIDDEN_PROFILE_TOKEN_PATTERNS`
    # 必须列出这些形状，否则这道机械门不存在）；豁免只覆盖该常量的源码文本。
    base = _REPO / "document_structure"
    exempt = _deny_list_source_lines(base / "table_classification.py")
    hits = []
    for name in _PRODUCTION_MODULES:
        path = base / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line in exempt:
                continue
            for token in _FORBIDDEN_LITERALS:
                if token in line:
                    hits.append((name, token, line.strip()[:60]))
    check(not hits,
          f"C1 生产模块（deny-list 常量除外）不得含 fixture 字面量 / 案例标识：{hits}")
    check(len(exempt) >= 4 and "300750" in "\n".join(exempt),
          "C1 反硬编码 deny-list 必须仍然登记案例形态（豁免不是空集）")

    policy_hits = []
    for path in sorted((base / "policies").glob("table_*.json")):
        text = path.read_text(encoding="utf-8")
        for token in _COMPANY_LITERALS:
            if token in text:
                policy_hits.append((path.name, token))
    check(not policy_hits,
          f"C1 `table_*.json` 策略文件不得含公司 / 代码标识：{policy_hits}")

    # 角色词表与规则只能来自**已登记**的 profile 文件（不是代码里的分支表）。
    bundle = _profiles().cell_block
    check(tuple(r.role for r in bundle.rules) == ("heading", "list_item",
                                                  "paragraph", "line",
                                                  "unclassified")
          and [r.order for r in bundle.rules] == [1, 2, 3, 4, 5]
          and set(TS.CELL_BLOCK_ROLES) == {r.role for r in bundle.rules},
          "C1 块角色词表必须恰由登记 profile 的规则给出（代码里没有第二套分支）")
    check(bundle.profile_version == V.TABLE_CELL_BLOCK_PROFILE_VERSION
          and bundle.conflict_rule == "fail_closed_unclassified"
          and bundle.unknown_signal_rule == "unclassified",
          "C1 冲突与未知信号必须 fail-closed 由 profile 声明")
    tb_report = TB.self_check()
    tc_report = TC.self_check()
    check("problems" in tb_report and not tb_report["problems"],
          f"C1 `table_builder.self_check` 必须无问题："
          f"{tb_report.get('problems')}")
    check("problems" in tc_report and not tc_report["problems"],
          f"C1 `table_classification.self_check` 必须无问题："
          f"{tc_report.get('problems')}")


# ---------------------------------------------------------------------------
# C2 §19.12.2A-3：unassigned 只有 exact closure 才进 table
# ---------------------------------------------------------------------------

def _consumed(tables=None, ref_ids=None, problems=None):
    return FMB._Consumption(tables=tables or {}, ref_ids=ref_ids or {},
                            problems=problems or {})


def _binding(consumed, cid, landing, *, disposition_id="d-1"):
    comp = SimpleNamespace(component_id=cid, landing=landing,
                           disposition_id=disposition_id)
    return FMB._binding_for(consumed, comp, {}, {})


def _test_c2(live):
    clean = _consumed(tables={"c-1": (("t-1", ((0, 2),)),)},
                      ref_ids={("t-1", "c-1"): ("tcsr-a",)})
    for landing in ("body_unassigned", "formal_unassigned"):
        got = _binding(clean, "c-1", landing)
        check(got["admission"] == "table_object"
              and got["table_id"] == "t-1"
              and got["cell_source_ref_ids"] == ("tcsr-a",),
              f"C2 {landing} 有**唯一**干净 table 消费时必须吸收进表，"
              f"且必须带真实 cell source ref（得到 {got}）")
        two = _consumed(tables={"c-1": (("t-1", ((0, 2),)),
                                        ("t-2", ((0, 2),)))})
        got = _binding(two, "c-1", landing)
        check(got["admission"] == "pending"
              and got["admission_reason"] == "provenance_incomplete"
              and not got["cell_source_ref_ids"],
              f"C2 {landing} 跨两张表消费必须 fail-closed 成 "
              f"pending(provenance_incomplete)（得到 {got}）")
        dirty = _consumed(tables={"c-1": (("t-1", ((0, 1),)),)},
                          ref_ids={("t-1", "c-1"): ("tcsr-a",)},
                          problems={"c-1": ("range_not_exact",)})
        check(_binding(dirty, "c-1", landing)["admission"] == "pending"
              and not _binding(dirty, "c-1", landing)["cell_source_ref_ids"],
              f"C2 {landing} 子区间不精确时必须 pending（不得进表）")
        empty = _consumed(tables={"c-1": (("t-1", ((0, 2),)),)})
        check(_binding(empty, "c-1", landing)["admission"] == "pending",
              f"C2 {landing} 没有真实 cell source ref 时必须 pending"
              f"（不得凭表绑定）")

    # 片段级：只有被终态 char_map **无缝覆盖**的片段才进 cell。
    page, comps, terms = _caption_fixture(_CAP_BOLD, bold=True)
    comps = [c for c in comps if c.component_id != "c-2-0"]
    terms = [t for t in terms if t.terminal_id != "al-c-2-0"]
    new_c, new_t = _component("c-2-0", page=1, line=2, span=0,
                              length=len(_CAP_BODY), ev0=130, partial=2)
    comps.append(new_c)
    terms.append(new_t)
    site = _Site([page], nodes=[_Node("n1")])
    d = _Disposition("d-1", "table_inside", "n1", 1, 1, 0, len(page.lines) - 1)
    ctx = site.context(dispositions=[d], components=comps, terminals=terms)
    plan_cell = TG.GridCellPlan(row=0, column=0, rowspan=1, colspan=1,
                                bbox=(20.0, 20.0, 60.0, 72.0),
                                fragment_keys=((0, 0, 0), (2, 0, 0)))
    asm = TB.assemble_cell(ctx, plan_cell, page_number=1, row=0)
    check(tuple(asm.unclosed) == ((2, 0),),
          f"C2 只被部分覆盖的片段必须登记为 unclosed，得到 {asm.unclosed}")
    check([r.interval.line_index for r in asm.cell.source_refs] == [0],
          f"C2 未闭合的片段**不得**进入 cell 来源，得到 "
          f"{[r.interval.line_index for r in asm.cell.source_refs]}")
    check([b.role for b in asm.cell.blocks] == ["unclassified"],
          f"C2 有未闭合输入时全部块角色必须 fail-closed 成 unclassified"
          f"（得到 {[b.role for b in asm.cell.blocks]}）")
    rows = TB.assign_rows({0: [asm.cell]})
    grid = TG.CellGridPlan(
        page_number=1, bbox=_CAP_BBOX, row_count=1, column_count=1,
        cells=(plan_cell,), assigned_lines=(0, 1), straddling_lines=(),
        geometry_backed=True, problems=())
    missing = TB._missing_fields(rows, "headered_grid",
                                 "ordinary_business_table", grid, asm.unclosed,
                                 title_block_count=1)
    check("cell_interior" in missing
          and TB._structure_state("headered_grid", missing, grid)
          == ("partial", "cell_provenance_incomplete"),
          f"C2 未闭合片段必须成为显式缺口 cell_interior / partial，得到 {missing}")

    if not _gate(live):
        return
    ctx_l, cons, bindings = live["ctx"], live["consumption"], live["bindings"]
    by_id = {b.component_id: b for b in bindings}
    unassigned = [c for c in ctx_l.components
                  if c.landing in ("body_unassigned", "formal_unassigned")]
    bad = []
    for comp in unassigned:
        b = by_id[comp.component_id]
        if b.admission == "table_object":
            claims = cons.tables.get(comp.component_id, ())
            if not (len(claims) == 1 and not cons.problems.get(comp.component_id)
                    and b.cell_source_ref_ids
                    and TB.component_partition_problems(
                        ctx_l, {comp.component_id: claims[0][1]}) == []):
                bad.append((comp.component_id, claims, b.cell_source_ref_ids))
        elif b.admission == "pending":
            if b.admission_reason != "provenance_incomplete" \
                    or b.cell_source_ref_ids:
                bad.append((comp.component_id, "pending",
                            b.admission_reason, b.cell_source_ref_ids))
        else:
            bad.append((comp.component_id, "unexpected", b.admission))
    check(not bad,
          f"C2 现场 {len(unassigned)} 个 unassigned component 必须"
          f"「exact closure 才进表 / 否则 pending 且无 ref」（反例 {bad[:2]}）")
    bad2 = []
    for b in bindings:
        if b.admission != "table_object":
            continue
        claims = cons.tables.get(b.component_id, ())
        if not b.cell_source_ref_ids or len(claims) != 1 \
                or cons.problems.get(b.component_id) \
                or TB.component_partition_problems(
                    ctx_l, {b.component_id: claims[0][1]}):
            bad2.append((b.component_id, claims, b.cell_source_ref_ids))
    check(not bad2,
          f"C2 现场任何 table_object binding 都必须有唯一、干净、带真实 ref 的消费"
          f"（反例 {bad2[:2]}，共 {len(bad2)} 个）")
    print(f"[c2] unassigned={len(unassigned)} "
          f"table_object={sum(1 for b in bindings if b.admission == 'table_object')}")


# ---------------------------------------------------------------------------
# C3 §19.12.2A-4：结构性 landing 与 table 冲突不得被静默吸收
# ---------------------------------------------------------------------------

def _test_c3(live):
    clean = _consumed(tables={"c-1": (("t-1", ((0, 2),)),)},
                      ref_ids={("t-1", "c-1"): ("tcsr-a",)})
    structural = ("heading_node", "non_content", "outside_body")
    for landing in structural:
        check(TS.COMPONENT_LANDING_ADMISSIONS[landing]
              == ("rejected", "pending"),
              f"C3 {landing} 只允许 rejected/pending，得到 "
              f"{TS.COMPONENT_LANDING_ADMISSIONS[landing]}")
        check(TS.COMPONENT_LANDING_PENDING_REASONS[landing]
              == ("upstream_table_scope_miss",),
              f"C3 {landing} 的 pending 理由必须恰为 upstream 缺口，得到 "
              f"{TS.COMPONENT_LANDING_PENDING_REASONS[landing]}")
        want = {"heading_node": "structural_heading_only",
                "non_content": "non_content_region",
                "outside_body": "outside_formal_body"}[landing]
        check(TS.COMPONENT_LANDING_REJECTED_REASONS[landing] == (want,),
              f"C3 {landing} 的 rejected 理由必须恰为 {want!r}")
        got = _binding(clean, "c-1", landing)
        check(got["admission"] == "rejected"
              and got["admission_reason"] == want
              and got["table_id"] is None
              and not got["cell_source_ref_ids"]
              and got["gap_codes"] == (),
              f"C3 即便账本里有一张**干净**的表消费了它，{landing} 也不得被吸收"
              f"（得到 {got}）")
    # empty 是另一条腿：同样只允许 rejected，但理由与 pending 出口都必须分开登记。
    check(TS.COMPONENT_LANDING_ADMISSIONS["body_empty"] == ("rejected", "pending")
          and TS.COMPONENT_LANDING_REJECTED_REASONS["body_empty"]
          == ("empty_source_text",)
          and TS.COMPONENT_LANDING_PENDING_REASONS["body_empty"]
          == ("root_identity_mismatch",),
          "C3 body_empty 的必要原因必须与结构性 landing 分开登记")
    got = _binding(clean, "c-1", "body_empty")
    check(got["admission"] == "rejected"
          and got["admission_reason"] == "empty_source_text"
          and not got["cell_source_ref_ids"] and got["gap_codes"] == (),
          f"C3 body_empty 不得被吸收（得到 {got['admission']}）")

    if not _gate(live):
        return
    bindings = live["bindings"]
    bad = [(b.component_id, b.component_landing, b.admission)
           for b in bindings
           if b.admission
           not in TS.COMPONENT_LANDING_ADMISSIONS[b.component_landing]]
    check(not bad,
          f"C3 现场 {len(bindings)} 条 binding 的终态必须属于其 landing 的允许集"
          f"（反例 {bad[:2]}）")
    live_struct = [b for b in bindings
                   if b.component_landing
                   in TS.COMPONENT_LANDING_REJECTED_REASONS]
    bad2 = [(b.component_id, b.component_landing, b.admission,
             b.admission_reason)
            for b in live_struct
            if b.admission != "rejected"
            or b.admission_reason
            not in TS.COMPONENT_LANDING_REJECTED_REASONS[b.component_landing]
            or b.table_id is not None or b.final_span_id is not None
            or b.cell_source_ref_ids or b.gap_codes]
    check(not bad2,
          f"C3 现场 {len(live_struct)} 个结构性 landing 必须全部是带理由的 "
          f"rejected 账本项（反例 {bad2[:2]}）")
    check(all(b.component_locator and isinstance(b.evidence_char_range, tuple)
              for b in live_struct),
          "C3 rejected 账本项必须保留完整 component identity 与区间（不是删除来源）")
    print(f"[c3] 结构性 landing "
          f"{sorted({b.component_landing for b in live_struct})} 共 {len(live_struct)}")


# ---------------------------------------------------------------------------
# C4 §19.12.2A-5：unverifiable / residue 永远 pending 且不可引用
# ---------------------------------------------------------------------------

def _test_c4(live):
    clean = _consumed(tables={"c-1": (("t-1", ((0, 2),)),)},
                      ref_ids={("t-1", "c-1"): ("tcsr-a",)})
    for landing in ("alignment_offset_unverifiable",
                    "alignment_residue_unmapped"):
        check(TS.COMPONENT_LANDING_ADMISSIONS[landing] == ("pending",),
              f"C4 {landing} 只允许 pending，得到 "
              f"{TS.COMPONENT_LANDING_ADMISSIONS[landing]}")
        got = _binding(clean, "c-1", landing)
        check(got["admission"] == "pending"
              and got["admission_reason"] == "provenance_incomplete"
              and got["table_id"] is None and not got["cell_source_ref_ids"]
              and got["gap_codes"] == ("provenance_incomplete",),
              f"C4 {landing} 即便账本里有干净表也必须 pending 且无 ref"
              f"（得到 {got}）")

    # 可引用性是**全函数**：每个未准入理由都映射到一个封闭的不可引用理由，
    # 且只有 `aligned_projected` 一项映射到"可引用"。
    check(set(FMB._CITABLE_REASON_OF_ADMISSION)
          == set(SS.COMPONENT_ADMISSION_REASONS) - {"aligned_projected"},
          "C4 `admission_reason → citable_reason` 映射必须覆盖除准入项外的全集")
    for reason, want in (("offset_unverifiable", "offset_unverifiable"),
                         ("residue_unmapped", "residue_unmapped"),
                         ("boundary_inexact", "fragment_not_closed"),
                         ("landing_not_body_span", "component_not_admitted"),
                         ("verdict_not_aligned", "verdict_not_aligned"),
                         ("refusal_record", "refusal_record")):
        got = FMB._CITABLE_REASON_OF_ADMISSION[reason]
        check(got == want and got != TS.CITABLE_REASON_TRUE,
              f"C4 未准入理由 {reason!r} 必须映射到不可引用理由 {want!r}，"
              f"得到 {got!r}")
    not_admitted = SimpleNamespace(component_id="c-9", admitted=False,
                                   admission_reason="offset_unverifiable")
    check(FMB._citable_of_component(not_admitted)
          == (False, "offset_unverifiable", None),
          "C4 offset 不可验证的 component 必须逐段不可引用且不指向任何 cell ref")
    admitted = SimpleNamespace(component_id="c-9", admitted=True,
                               admission_reason="aligned_projected")
    check(FMB._citable_of_component(admitted)
          == (True, TS.CITABLE_REASON_TRUE, "c-9"),
          "C4 已准入 component 的可引用区间必须绑定到具体 cell source ref")
    raises(lambda: TS.CitableInterval(citable=True,
                                      reason=TS.CITABLE_REASON_TRUE,
                                      char_range=(0, 3), cell_ref=None),
           Exception, "必须绑定到具体 cell source ref",
           "C4 可引用区间不得脱离具体 cell source ref（不得整表授权）")
    check(len(set(TS.CITABLE_REASONS)) == len(TS.CITABLE_REASONS)
          and set(TS.CITABLE_REASON_FALSE)
          == set(TS.CITABLE_REASONS) - {TS.CITABLE_REASON_TRUE},
          "C4 可引用理由必须是封闭词表，且只有一项可引用")

    # 逐段可引用性由**真实 terminal** 派生，不得自报。
    page = _Page(1, [_Line(0, [_Span(20.0, 20.0, 60.0, 30.0, text="xy")])])
    site = _Site([page], nodes=[_Node("n1")])
    d = _Disposition("d-1", "table_inside", "n1", 1, 1, 0, 0)
    pairs = [_component("c-ok", page=1, line=0, span=0, length=2, ev0=220),
             _component("c-al", page=1, line=0, span=0, length=2, ev0=200,
                        verdict="unaligned"),
             _component("c-rf", page=1, line=0, span=0, length=2, ev0=210,
                        kind="refusal")]
    ctx = site.context(dispositions=[d],
                       components=[c for c, _t in pairs],
                       terminals=[t for _c, t in pairs])
    interval = TS.TableSourceInterval(page_number=1, line_index=0, span_index=0,
                                      span_char_range=(0, 2),
                                      bbox=(20.0, 20.0, 60.0, 30.0))
    for comp, _term in pairs:
        term = ctx.terminal_for(comp)
        r = TS.TableCellSourceRef.create(
            interval=interval, component_id=comp.component_id,
            component_locator=comp.component_locator,
            evidence_block_id=comp.evidence_block_id,
            evidence_char_range=comp.evidence_char_range,
            terminal_kind=term.terminal_kind, terminal_id=term.terminal_id,
            terminal_locator=term.terminal_locator,
            terminal_schema_version=term.terminal_schema_version,
            verdict=(term.verdict if term.verdict is not None else "refused"),
            refusal_reason=term.refusal_reason)
        if comp.component_id == "c-ok":
            check(r.citable is True and r.citable_reason == TS.CITABLE_REASON_TRUE
                  and r.alignment_id == r.terminal_id,
                  "C4 只有 aligned 终态的片段可引用（alignment_id 必须派生）")
        elif comp.component_id == "c-rf":
            check(r.citable is False and r.citable_reason == "refusal_record"
                  and r.alignment_id is None and r.terminal_kind == "refusal",
                  f"C4 refusal 终态必须不可引用且**不得**伪造 alignment ID"
                  f"（{r.citable_reason}/{r.alignment_id}）")
        else:
            check(r.citable is False
                  and r.citable_reason == "verdict_not_aligned"
                  and r.alignment_id == r.terminal_id,
                  f"C4 非对齐终态必须不可引用（{r.citable_reason}）")
    raises(lambda: TS.TableCellSourceRef.create(
        interval=interval, component_id="c-x", component_locator="loc-c-x",
        evidence_block_id="evb-c-x", evidence_char_range=(0, 2),
        terminal_kind="refusal", terminal_id="rf-c-x",
        terminal_locator="loc-rf-c-x",
        terminal_schema_version=V.ALIGN_SCHEMA_VERSION, verdict="aligned"),
        Exception, "aligned 终态不得以",
        "C4 refusal 终态不得以'非对齐/拒绝'为不可引用理由（aligned 自报被拒）")
    raises(lambda: TS.CitableInterval(citable=True, reason="refusal_record",
                                      char_range=(0, 2), cell_ref="tcsr-x"),
           Exception, "当且仅当",
           "C4 可引用标记与理由必须互相蕴含（不得两处自相矛盾）")

    if not _gate(live):
        return
    ctx_l, built = live["ctx"], live["built"]
    empties = [c for c in ctx_l.components if c.landing == "body_empty"]
    by_id = {b.component_id: b for b in live["bindings"]}
    bad = [(c.component_id, by_id[c.component_id].admission,
            by_id[c.component_id].admission_reason)
           for c in empties
           if by_id[c.component_id].admission != "rejected"
           or by_id[c.component_id].admission_reason != "empty_source_text"]
    check(not bad,
          f"C4 现场 {len(empties)} 个 empty landing 必须全部是 "
          f"rejected(empty_source_text)（反例 {bad[:2]}）")
    zero = [(t.page_number, r.source_ref_id) for t in live["tables"]
            for rw in t.rows for c in rw.cells for r in c.source_refs
            if r.evidence_char_range[1] <= r.evidence_char_range[0]
            or r.interval.span_char_range[1] <= r.interval.span_char_range[0]]
    check(not zero,
          f"C4 现场不得有任何零长度/反向的 cell 来源区间（{zero[:2]}）")
    blank = [(t.page_number, rw.row_index) for t in live["tables"]
             for rw in t.rows for c in rw.cells for b in c.blocks
             if b.text.strip() == ""]
    check(not blank,
          f"C4 现场表内不得出现空块（empty 来源不进 cell，{blank[:2]}）")
    reasons = sorted({row[2] for row in built.rejected})
    check(bool(reasons) and all(isinstance(x, str) and x != ""
                                for x in reasons),
          f"C4 rejected 账本必须逐行带封闭理由码（现场 {len(reasons)} 种：{reasons}）")
    print(f"[c4] empty landing={len(empties)} rejected 行={len(built.rejected)} "
          f"理由={reasons}")


# ---------------------------------------------------------------------------
# C5 §19.12.2A-6：同一 component 跨 cell 的子区间与跨表 fail-closed
# ---------------------------------------------------------------------------

def _test_c5(live):
    if not _gate(live):
        return
    ctx, cons = live["ctx"], live["consumption"]
    comps = {c.component_id: c for c in ctx.components}

    # (1) 跨表消费 ⇒ 该 component 必须 fail-closed 成 pending 且不持 ref；
    #     并且**任何** component 都不得在两个 table_object 里各留一份 binding。
    by_id = {b.component_id: b for b in live["bindings"]}
    cross = {cid: claims for cid, claims in cons.tables.items()
             if len(claims) > 1}
    bad_cross = [(cid, by_id[cid].admission, by_id[cid].admission_reason,
                  by_id[cid].cell_source_ref_ids, claims[:2])
                 for cid, claims in cross.items()
                 if by_id[cid].admission != "pending"
                 or by_id[cid].admission_reason != "provenance_incomplete"
                 or by_id[cid].cell_source_ref_ids]
    check(not bad_cross,
          f"C5 现场 {len(cross)} 个跨表 component 必须 pending(provenance_incomplete) "
          f"且无 ref（反例 {bad_cross[:2]}）")
    check(len(live["bindings"]) == len(ctx.components)
          and len({b.component_id for b in live["bindings"]})
          == len(live["bindings"])
          and {b.component_id for b in live["bindings"]}
          == {c.component_id for c in ctx.components},
          f"C5 binding 必须与 components 精确等集且每个 component 恰一条"
          f"（{len(live['bindings'])} / {len(ctx.components)}）")
    print(f"[c5] 跨表消费 component={len(cross)}")

    # (2) 逐表：同一 component 的子区间必须有序、互斥、无洞、并集相等。
    bad = []
    multi = 0
    for bt in live["built"].tables:
        cid_ranges: dict = {}
        for rw in bt.table.rows:
            for c in rw.cells:
                for r in c.source_refs:
                    cid_ranges.setdefault(r.component_id, []).append(
                        r.evidence_char_range)
        for cid, ranges in cid_ranges.items():
            multi += 1 if len(ranges) > 1 else 0
            comp = comps.get(cid)
            ordered = sorted(ranges)
            ok = comp is not None
            if ok:
                lo, hi = comp.evidence_char_range
                ok = ordered[0][0] == lo and ordered[-1][1] == hi
                cursor = lo
                for (a, b) in ordered:
                    ok = ok and a == cursor and b > a
                    cursor = b
                ok = ok and cursor == hi and len(ordered) == len(set(ordered))
            if not ok:
                bad.append((bt.table.page_number, cid, ordered))
    check(not bad,
          f"C5 现场每张表内同一 component 的 Evidence 子区间必须有序/互斥/无洞/"
          f"并集相等（反例 {bad[:2]}）")
    check(all(TB.component_partition_problems(ctx, bt.consumed) == []
              for bt in live["built"].tables),
          "C5 生产分区检测器在每张现场表上都必须无问题")

    # (3) 跨 terminal：同一 component 的所有片段只能来自它**唯一**的终态。
    cross_term = []
    total_refs = 0
    for t in live["tables"]:
        for r in t.source_refs:
            total_refs += 1
            comp = comps[r.component_id]
            term = ctx.terminal_for(comp)
            if r.terminal_id != term.terminal_id \
                    or r.terminal_locator != term.terminal_locator:
                cross_term.append((r.source_ref_id, r.terminal_id,
                                   term.terminal_id))
    check(total_refs > 0 and not cross_term,
          f"C5 同一 component 不得跨 terminal 拼来源（{total_refs} 段，"
          f"反例 {cross_term[:2]}）")

    # (4) 跨表消费 ⇒ pending，且 binding 不复制；单表精确消费仍被吸收（成对）。
    cid = live["tables"][0].source_refs[0].component_id
    rng = comps[cid].evidence_char_range
    dup = _consumed(tables={cid: (("t-1", (rng,)), ("t-2", (rng,)))},
                    ref_ids={("t-1", cid): ("tcsr-a",)})
    got = _binding(dup, cid, "body_unassigned")
    check(got["admission"] == "pending"
          and got["admission_reason"] == "provenance_incomplete"
          and not got["cell_source_ref_ids"],
          f"C5 跨表消费必须 pending(provenance_incomplete) 且不复制 binding"
          f"（得到 {got['admission']}/{got['admission_reason']}）")
    once = _consumed(tables={cid: (("t-1", (rng,)),)},
                     ref_ids={("t-1", cid): ("tcsr-a",)})
    check(_binding(once, cid, "body_unassigned")["admission"] == "table_object",
          "C5 单表精确消费仍然必须被吸收（反例必须与正例成对出现）")

    # (5) 合法切分 vs 洞/重叠：分区检测器的正负例必须成对。
    target = next((c for c in ctx.components
                   if c.evidence_char_range[1] - c.evidence_char_range[0] >= 4),
                  None)
    check(target is not None, "C5 现场必须能找到长度 >= 4 的 component 做端点手术")
    if target is not None:
        lo, hi = target.evidence_char_range
        mid = lo + (hi - lo) // 2
        check(TB.component_partition_problems(
                  ctx, {target.component_id: ((lo, mid), (mid, hi))}) == [],
              "C5 恰好切成两段且无缝的合法分割不得被误报")
        check(TB.component_partition_problems(
                  ctx, {target.component_id: ((lo, mid - 1), (mid, hi))})
              == [f"{target.component_id}:gap_or_overlap"],
              "C5 有**重叠**的分割必须报 gap_or_overlap")
        check(TB.component_partition_problems(
                  ctx, {target.component_id: ((lo, mid), (mid + 1, hi))})
              == [f"{target.component_id}:gap_or_overlap"],
              "C5 有**洞**的分割必须报 gap_or_overlap")
        check(TB.component_partition_problems(
                  ctx, {target.component_id: ((lo, hi - 1),)})
              == [f"{target.component_id}:range_not_exact"],
              "C5 端点被削去的分割必须报 range_not_exact")
    print(f"[c5] cell source ref {total_refs} 段，跨 cell 消费的 component {multi} 个")


# ---------------------------------------------------------------------------
# C6 §19.12.2A-8（账本层）：删除 / 重复 / 端点改变 / rejected 不可洗白
# ---------------------------------------------------------------------------

def _test_c6(live):
    if not _gate(live):
        return
    ctx, cons, bindings = live["ctx"], live["consumption"], live["bindings"]
    comps = {c.component_id: c for c in ctx.components}

    unknown = TB.component_partition_problems(ctx,
                                              {"cid-syn-not-real": ((0, 1),)})
    check(unknown == ["cid-syn-not-real:unknown_component"],
          f"C6 账本里出现未知 component 必须报 unknown_component（得到 {unknown}）")

    absorbed_ids = {d.disposition_id for d in live["decisions"]
                    if d.decision in FMB.ABSORBED_DECISIONS}
    deletable = [b for b in bindings
                 if b.admission == "table_object"
                 and comps[b.component_id].disposition_id in absorbed_ids]
    check(bool(deletable),
          f"C6 现场必须存在「被表吸收且裁决也是吸收」的 component 供删除手术"
          f"（{sum(1 for b in bindings if b.admission == 'table_object')} 个被吸收，"
          f"{len(absorbed_ids)} 条吸收裁决）")
    if not deletable:
        return
    deleted = deletable[0]
    other = next((b for b in bindings
                  if b.admission == "table_object"
                  and b.component_id != deleted.component_id), None)
    check(other is not None, "C6 现场必须存在第二个被表吸收的 component 供重复手术")
    if other is None:
        return

    tampered_tables = dict(cons.tables)
    tampered_tables.pop(deleted.component_id, None)
    dup_cid = other.component_id
    tampered_tables[dup_cid] = tuple(tampered_tables[dup_cid]) + (
        ("t-syn-dup", tuple(tampered_tables[dup_cid][0][1])),)
    tampered = _consumed(tables=tampered_tables, ref_ids=cons.ref_ids,
                         problems=cons.problems)
    rebuilt = FMB._build_bindings(live["root"], tampered, live["decisions"],
                                  live["span_of_component"])
    check(len(rebuilt) == len(live["root"].components)
          and len({b.component_id for b in rebuilt}) == len(rebuilt)
          and {b.component_id for b in rebuilt}
          == {c.component_id for c in live["root"].components},
          f"C6 账本手术后 binding 仍然必须与 components 精确等集且不复制"
          f"（{len(rebuilt)} 条 / {len(live['root'].components)} 个 component）")
    before = {b.component_id: (b.admission, b.admission_reason)
              for b in bindings}
    after = {b.component_id: (b.admission, b.admission_reason)
             for b in rebuilt}
    by_id = {b.component_id: b for b in rebuilt}
    check(after[deleted.component_id] == ("pending", "provenance_incomplete")
          and not by_id[deleted.component_id].cell_source_ref_ids,
          f"C6 删除账本里的 component 后必须降级为 pending 且不再持有 ref"
          f"（得到 {after[deleted.component_id]}）")
    check(after[dup_cid] == ("pending", "provenance_incomplete")
          and not by_id[dup_cid].cell_source_ref_ids,
          f"C6 重复账本条目后必须 pending 且不复制 binding"
          f"（得到 {after[dup_cid]}）")
    changed = {cid for cid in after if after[cid] != before[cid]}
    check(changed == {deleted.component_id, dup_cid},
          f"C6 账本手术的影响必须**只**落在被改的两条 component 上"
          f"（实际受影响 {len(changed)} 条：{sorted(changed)[:4]}）")

    # rejected 来源的终态由 landing 封闭映射决定：账本无法把它洗成 table。
    for landing, want in (("body_empty", "empty_source_text"),
                          ("heading_node", "structural_heading_only"),
                          ("non_content", "non_content_region"),
                          ("outside_body", "outside_formal_body")):
        cid2 = f"c-syn-{landing}"
        dirty = _consumed(tables={cid2: (("t-1", ((0, 2),)),)},
                          ref_ids={("t-1", cid2): ("tcsr-a",)})
        got = _binding(dirty, cid2, landing)
        check(got["admission"] == "rejected"
              and got["admission_reason"] == want
              and not got["cell_source_ref_ids"],
              f"C6 账本里「有干净表消费」也不得把 {landing} 洗成 table"
              f"（得到 {got['admission']}/{got['admission_reason']}）")
    check(set(TS.COMPONENT_LANDING_REJECTED_REASONS)
          == set(FMB._REJECTED_REASON_OF_LANDING)
          and all(FMB._REJECTED_REASON_OF_LANDING[k]
                  in TS.COMPONENT_LANDING_REJECTED_REASONS[k]
                  for k in FMB._REJECTED_REASON_OF_LANDING),
          "C6 rejected 映射必须与 wire 层封闭集合精确一致（无第二套理由表）")
    check(set(FMB._NO_FRAGMENT_LANDINGS)
          == {"alignment_offset_unverifiable", "alignment_residue_unmapped"}
          and all(TS.COMPONENT_LANDING_ADMISSIONS[k] == ("pending",)
                  for k in FMB._NO_FRAGMENT_LANDINGS),
          "C6 无可核验片段的 landing 必须恰为登记的两项且只能 pending")


# ---------------------------------------------------------------------------
# C7 §19.12.6-1：逐 cell source ref 的四跳回查
# ---------------------------------------------------------------------------

def _test_c7(live):
    if not _gate(live):
        return
    ctx, root = live["ctx"], live["root"]
    comps = {c.component_id: c for c in ctx.components}
    blocks = root.blocks_by_id
    hops = {"span": 0, "component": 0, "evidence": 0, "alignment": 0}
    bad = {"span": [], "component": [], "evidence": [], "alignment": []}
    total = 0
    for t in live["tables"]:
        for r in t.source_refs:
            total += 1
            # 第 1 跳：LayoutSpan（物理位置 + 整段区间）。
            span = ctx.layout_span(r.interval.page_number,
                                   r.interval.line_index,
                                   r.interval.span_index)
            if span is None \
                    or tuple(span.bbox) != tuple(r.interval.bbox) \
                    or (r.interval.span_char_range[1]
                        - r.interval.span_char_range[0]) \
                    != span.char_end - span.char_start:
                bad["span"].append(r.source_ref_id)
            else:
                hops["span"] += 1
            # 第 2 跳：TS4 component（身份 + 它自己声明的 layout hit）。
            comp = comps.get(r.component_id)
            if comp is None \
                    or r.component_locator != comp.component_locator \
                    or r.evidence_block_id != comp.evidence_block_id \
                    or not any(
                        h.page_number == r.interval.page_number
                        and h.line_index == r.interval.line_index
                        and h.layout_span_index == r.interval.span_index
                        for h in comp.layout_hits):
                bad["component"].append(r.source_ref_id)
            else:
                hops["component"] += 1
            if comp is None:
                continue
            # 第 3 跳：Evidence range（由真实 char_map 重算 + 落在块文本内）。
            mapped = TB.map_span_range_to_evidence(
                ctx, comp, r.interval.page_number, r.interval.line_index,
                r.interval.span_index, r.interval.span_char_range[0],
                r.interval.span_char_range[1])
            blk = blocks.get(r.evidence_block_id)
            text_len = len(FMB._tight_text(blk)) if blk is not None else -1
            if mapped != r.evidence_char_range \
                    or r.evidence_char_range[1] > text_len:
                bad["evidence"].append(
                    (r.source_ref_id, mapped, r.evidence_char_range,
                     text_len))
            else:
                hops["evidence"] += 1
            # 第 4 跳：alignment 终态（kind / id / verdict / 可引用性）。
            term = ctx.terminal_for(comp)
            verdict = term.verdict if term.verdict is not None \
                else "refused"
            ok = (r.terminal_kind == term.terminal_kind
                  and r.terminal_id == term.terminal_id
                  and r.terminal_locator == term.terminal_locator
                  and r.terminal_schema_version
                  == term.terminal_schema_version
                  and r.verdict == verdict
                  and r.refusal_reason
                  == (term.refusal_reason if verdict == "refused"
                      else None)
                  and r.alignment_id == (r.terminal_id
                                         if r.terminal_kind
                                         == "alignment" else None)
                  and r.citable == (r.terminal_kind == "alignment"
                                    and r.verdict == "aligned")
                  and r.citable_reason
                  == (TS.CITABLE_REASON_TRUE if r.citable
                      else r.citable_reason))
            if not ok:
                bad["alignment"].append(r.source_ref_id)
            else:
                hops["alignment"] += 1
    check(total > 0, f"C7 现场必须有 cell source ref 可供回查（{total}）")
    for name, label in (("span", "LayoutSpan"), ("component", "TS4 component"),
                        ("evidence", "Evidence range"),
                        ("alignment", "alignment 终态")):
        check(not bad[name],
              f"C7 第 {label} 跳必须逐段成立（{hops[name]}/{total} 通过，"
              f"反例 {bad[name][:2]}）")
    print(f"[c7] 逐跳回查 {total} 段：{hops}")


# ---------------------------------------------------------------------------
# C8 §19.12.6-2：aligned / non-citable 混合表不得整表授权
# ---------------------------------------------------------------------------

def _test_c8(live):
    fields = [f.name for f in dataclasses.fields(TS.TableCitableCoverage)]
    check(fields == ["coverage_locator", "coverage_id", "schema_version",
                     "upstream_dependency_fingerprint", "table_id",
                     "table_locator", "cell_refs", "intervals",
                     "evidence_char_range", "non_citable_reasons"],
          f"C8 coverage 必须逐 cell source ref 记录且**没有**整表授权字段"
          f"（字段 {fields}）")
    check(isinstance(getattr(TS.TableCitableCoverage, "all_cells_citable"),
                     property)
          and isinstance(getattr(TS.TableCitableCoverage, "citable_char_count"),
                         property)
          and "all_cells_citable" not in fields
          and "citable_char_count" not in fields,
          "C8 逐 cell 结论必须是派生 property，不得是可自报的存储字段")

    # 合成混合 coverage：一个 aligned 区间 + 一个 refusal 区间。
    r_ok = TS.TableCellSourceRef.create(
        interval=TS.TableSourceInterval(page_number=1, line_index=0,
                                        span_index=0, span_char_range=(0, 2),
                                        bbox=(20.0, 22.0, 55.0, 38.0)),
        component_id="c-ok", component_locator="loc-c-ok",
        evidence_block_id="evb-c-ok", evidence_char_range=(300, 302),
        terminal_kind="alignment", terminal_id="al-c-ok",
        terminal_locator="loc-al-c-ok",
        terminal_schema_version=V.ALIGN_SCHEMA_VERSION, verdict="aligned")
    r_rf = TS.TableCellSourceRef.create(
        interval=TS.TableSourceInterval(page_number=1, line_index=0,
                                        span_index=0, span_char_range=(2, 4),
                                        bbox=(20.0, 22.0, 55.0, 38.0)),
        component_id="c-rf", component_locator="loc-c-rf",
        evidence_block_id="evb-c-rf", evidence_char_range=(302, 304),
        terminal_kind="refusal", terminal_id="rf-c-rf",
        terminal_locator="loc-rf-c-rf",
        terminal_schema_version=V.ALIGN_SCHEMA_VERSION, verdict="refused",
        refusal_reason="recorded_refusal")
    cell_refs = tuple(sorted({r_ok.source_ref_id, r_rf.source_ref_id}))

    def _cov(intervals, reasons):
        return TS.TableCitableCoverage.create(
            schema_version=V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION,
            upstream_dependency_fingerprint="a" * 64,
            table_id="to-syn-1", table_locator="loc-to-syn-1",
            cell_refs=cell_refs, intervals=tuple(intervals),
            evidence_char_range=(300, 304),
            non_citable_reasons=tuple(sorted(set(reasons))))

    def _iv(citable, reason, a, b, ref):
        return TS.CitableInterval(citable=citable, reason=reason,
                                  char_range=(a, b), cell_ref=ref)

    good = _cov([_iv(True, TS.CITABLE_REASON_TRUE, 0, 2, r_ok.source_ref_id),
                 _iv(False, "refusal_record", 2, 4, r_rf.source_ref_id)],
                ["refusal_record"])
    check(good.all_cells_citable is False
          and good.citable_char_count == 2
          and good.non_citable_reasons == ("refusal_record",)
          and good.cell_refs == cell_refs,
          "C8 混合表必须自报 all_cells_citable=False 且逐段可引用性可分别读取")
    check(sorted(good.identity_payload()["cell_refs"]) == list(cell_refs)
          and len(good.identity_payload()["intervals"]) == 2,
          "C8 coverage 身份必须绑定到具体 cell source ref（不得只绑整表）")

    # 自洽的篡改（重算派生身份）仍是一条「整表可引用」的谎言：它必须能被
    # 「逐 ref 真实可引用性 + 实测理由集」抓住，而不是靠对象自洽性拦住。
    tampered = _cov([_iv(True, TS.CITABLE_REASON_TRUE, 0, 2,
                         r_ok.source_ref_id),
                     _iv(True, TS.CITABLE_REASON_TRUE, 2, 4,
                         r_rf.source_ref_id)], [])
    check(tampered.coverage_id != good.coverage_id
          and tampered.coverage_locator == good.coverage_locator
          and tampered.all_cells_citable is True,
          "C8 篡改后的 coverage 只能是自洽重算身份的**谎言**对象"
          "（本反例因此有效）")
    check(tampered.all_cells_citable
          and not all(r.citable for r in (r_ok, r_rf)),
          "C8 该谎言与本文真实 cell source ref 的可引用性直接矛盾 ⇒ 可被抓住")
    raises(lambda: _cov([_iv(True, TS.CITABLE_REASON_TRUE, 0, 2,
                             r_ok.source_ref_id),
                         _iv(True, TS.CITABLE_REASON_TRUE, 2, 4,
                             r_rf.source_ref_id)],
                        ["refusal_record"]),
           Exception, "必须等于实测非可引用区间的理由集",
           "C8 把 refusal 段改写成可引用却沿用旧理由集必须被拒绝")
    raises(lambda: _cov([_iv(True, TS.CITABLE_REASON_TRUE, 0, 2,
                             "tcsr-not-in-coverage")], []),
           Exception, "不在本文 coverage 的 cell_refs 中",
           "C8 区间不得绑定到本文 coverage 之外的 cell source ref")
    raises(lambda: _cov([_iv(True, TS.CITABLE_REASON_TRUE, 0, 4,
                             r_ok.source_ref_id),
                         _iv(True, TS.CITABLE_REASON_TRUE, 2, 6,
                             r_rf.source_ref_id)], []),
           Exception, "不得重叠",
           "C8 可引用区间不得重叠（不得把一段授权两遍）")
    raises(lambda: _cov([_iv(False, "made_up_reason", 0, 2,
                             r_rf.source_ref_id)], []),
           Exception, "reason 必须属于",
           "C8 不可引用理由必须是登记封闭词表（不得自造理由掩盖）")

    if not _gate(live):
        return
    tables, coverages = live["tables"], live["coverages"]
    check(len(coverages) == len(tables) and len(tables) > 0,
          f"C8 每张表必须恰有一份 coverage（{len(coverages)}/{len(tables)}）")
    cov_by_table = {c.table_id: c for c in coverages}
    bad = []
    for t in tables:
        cov = cov_by_table.get(t.table_id)
        # coverage 必须覆盖表的**全部** source ref（含表题/单位/注装饰 ref），
        # 与 _build_coverages 的口径一致；顺序即 table.source_refs 的声明顺序。
        refs = list(t.source_refs)
        if cov is None:
            bad.append((t.page_number, "missing coverage"))
            continue
        if cov.cell_refs != tuple(sorted({r.source_ref_id for r in refs})):
            bad.append((t.page_number, "cell_refs mismatch"))
            continue
        if len(cov.intervals) != len(refs) \
                or [iv.cell_ref for iv in cov.intervals] \
                != [r.source_ref_id for r in refs]:
            bad.append((t.page_number, "intervals not per cell source ref"))
            continue
        if [(iv.citable, iv.reason) for iv in cov.intervals] \
                != [(r.citable, r.citable_reason) for r in refs]:
            bad.append((t.page_number, "citable flags/reasons mismatch"))
            continue
        cursor = 0
        cursor_ok = True
        for ref, iv in zip(refs, cov.intervals):
            length = (ref.interval.span_char_range[1]
                      - ref.interval.span_char_range[0])
            if iv.char_range != (cursor, cursor + length):
                cursor_ok = False
                break
            cursor += length
        if not cursor_ok:
            bad.append((t.page_number, "interval cursor mismatch"))
            continue
        if cov.all_cells_citable != all(r.citable for r in refs):
            bad.append((t.page_number, "all_cells_citable mismatch"))
            continue
        if cov.citable_char_count != sum(
                iv.char_range[1] - iv.char_range[0]
                for iv in cov.intervals if iv.citable):
            bad.append((t.page_number, "citable_char_count mismatch"))
            continue
        want_reasons = tuple(sorted({r.citable_reason for r in refs
                                     if not r.citable}))
        if cov.non_citable_reasons != want_reasons:
            bad.append((t.page_number, "non_citable_reasons mismatch"))
            continue
        blocks = {r.evidence_block_id for r in refs}
        if len(blocks) == 1:
            want_env = (min(r.evidence_char_range[0] for r in refs),
                        max(r.evidence_char_range[1] for r in refs))
        else:
            # 跨 Evidence block 不得拼出"看起来合法"的区间。
            want_env = (0, 0)
        if tuple(cov.evidence_char_range) != tuple(want_env):
            bad.append((t.page_number, "evidence_char_range mismatch"))
    check(not bad,
          f"C8 现场 coverage 必须逐 cell source ref 与真实 refs 精确对齐"
          f"（反例 {bad[:2]}）")
    mixed = [c for c in coverages if not c.all_cells_citable]
    check(all(c.citable_char_count
              < sum(iv.char_range[1] - iv.char_range[0] for iv in c.intervals)
              for c in mixed),
          f"C8 混合表的可引用字符数必须严格小于区间总长"
          f"（现场混合表 {len(mixed)} 张）")
    aligned_tables = [c for c in coverages if c.all_cells_citable]
    check(all(c.non_citable_reasons == () for c in aligned_tables),
          f"C8 全可引用表的非可引用理由集必须为空"
          f"（{len(aligned_tables)} 张全可引用）")
    print(f"[c8] 现场表 {len(tables)} 张，非整表可引用 {len(mixed)} 张")


# ---------------------------------------------------------------------------
# 组驱动
# ---------------------------------------------------------------------------

_GROUPS = (("C1", _test_c1), ("C2", _test_c2), ("C3", _test_c3),
           ("C4", _test_c4), ("C5", _test_c5), ("C6", _test_c6),
           ("C7", _test_c7), ("C8", _test_c8))


def _run_group(name, fn, live):
    started = time.time()
    before = _results["passed"] + _results["failed"]
    try:
        fn(live)
    except Exception as error:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 未捕获异常：{type(error).__name__}: {error}")
    finally:
        _results["details"].append(
            f"__{name}__ {time.time() - started:.1f}s "
            f"(checks={_results['passed'] + _results['failed'] - before})")


def main() -> dict:
    started = time.time()
    before = _db_identity(_DB_PATH) if _DB_PATH.exists() else None
    previous_db_path = getattr(_estore, "_db_path", None)
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

"""W1：跨页 `table_inside` 在**构造期**按页分段（`sb-7 → sb-8`；表格构造侧本轮为
`tb-4 → tb-5`，其后 §0.19（乙）再升到 `tb-5 → tb-6`，本模块的版本断言随现行值走）。

## 这一轮修的是什么

`_split_runs` 的合并键对表格类别只取 `(kind, table_scope, table_reason)`，**页码不在
键里**（`span_builder._run_merge_key`），且家具行不参与事实序列 —— 所以一条
`table_inside` 正文范围**可以跨页**（该函数自己的文档字符串就写明"故跨页 run 允许"）。
`sb-7` 把这样一条跨页 run 建成**一条跨页 `BodyRangeDisposition`**。而 §19.4.2 的表格
对象是**单页**对象，`table_builder.frozen_range_bbox` 对跨页范围按设计返回 `None`
（"跨页范围没有唯一物理矩形"），于是 §19.5.1 冻结范围通道整段看不到它；下游文档级
守恒层同样对跨页处置 `continue`（`final_material_builder._layout_text_scan` /
`_plan_gap_intervals`），这些字符既不建表也不进表相关文本域，只能落 `residual_gap`
⇒ 该层 `balanced=false` ⇒ 文档 `refused` ⇒ 零放行。

`sb-8` 的做法是**在构造期按页展开**：一条跨页 `table_inside` run 变成本页各自的单页
处置记录。这**不是**放宽 `frozen_range_bbox` 的单页不变式 —— 本模块的反例 C 正是
钉住"该函数对任何跨页范围仍然返回 `None`"。

## 本模块只做三件事

1. **正例**：跨页 `table_inside` run 展开成逐页单页处置，且身份由内容派生（不写死）；
2. **底线回归**：单页 run 的输出与展开前**逐字段一致**（分段不得改变既有行为）；
3. **反例**：`frozen_range_bbox` 单页不变式未放宽、正文不得被误判成表、跨页
   `table_adjacency` 不在本步覆盖范围内（如实记录而不是假装已修）、非单调页序
   fail-closed。

守恒口径（行数 / tight 字符数）在展开前后**必须逐项相等**：`_build_conservation`
的正文域层按 `range_kind` 分桶累加 `line_count` 与 `tight_char_count`，所以"分项之
和恰等于合计"这一条正是靠"分段只是重新分账、不增不减"来维持的。本模块直接断言
这条加法律，而不重跑整条守恒链。

本模块只读仓库内代码与自身合成夹具；不连网络、不写数据库、不写生成结果、不发
真实 LLM。
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from document_structure import span_builder as SB  # noqa: E402
from document_structure import table_builder as TB  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.span_schema import (  # noqa: E402
    BodyRangeDisposition,
    SchemaValidationError,
)

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def skip(msg):
    _results["skipped"] += 1
    _results["details"].append("SKIP " + msg)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as error:
        text = str(error)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as error:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(error).__name__} 而非 {exc.__name__}：{error}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


# ---------------------------------------------------------------------------
# 1. 只读投影：合成版式 / 结构终态
# ---------------------------------------------------------------------------
#
# `_collect_line_facts` 与 `outline_builder.table_region_scopes` 只读下面这些字段；
# 本模块用**只读投影**把它们凑出来（与 `test_tree_span_table_adjacency._FactsView`、
# `test_tree_span_builder._FactsView` 同一先例）。不构造任何生产 capability，不新增
# 旁路；分段函数本身不接触任何 capability。

class _Span:
    __slots__ = ("text", "size", "is_bold")

    def __init__(self, text: str, size: float, is_bold: bool) -> None:
        self.text = text
        self.size = size
        self.is_bold = is_bold


class _Line:
    __slots__ = ("line_index", "bbox", "spans", "is_furniture", "text")

    def __init__(self, line_index, bbox, text, size, is_bold, is_furniture):
        self.line_index = line_index
        self.bbox = bbox
        self.text = text
        self.spans = (_Span(text, size, is_bold),)
        self.is_furniture = is_furniture


class _Page:
    __slots__ = ("page_number", "lines")

    def __init__(self, page_number, lines):
        self.page_number = page_number
        self.lines = tuple(lines)


class _Layout:
    __slots__ = ("pages",)

    def __init__(self, pages):
        self.pages = tuple(pages)


class _State:
    __slots__ = ("page_number", "line_index", "state", "body_attachment",
                 "node_id")

    def __init__(self, *, page_number, line_index, state, body_attachment,
                 node_id):
        self.page_number = page_number
        self.line_index = line_index
        self.state = state
        self.body_attachment = body_attachment
        self.node_id = node_id


class _Structure:
    __slots__ = ("line_states",)

    def __init__(self, states):
        self.line_states = states


class _FactsView:
    """`_collect_line_facts` 只读的两个字段（测试专用只读投影）。"""

    __slots__ = ("page_layout", "structure_snapshot")

    def __init__(self, page_layout, structure_snapshot) -> None:
        self.page_layout = page_layout
        self.structure_snapshot = structure_snapshot


#: 合成版式的几何常量（pt），与 `test_tree_span_table_adjacency` 同值：正文行贴文档
#: 左边界，表格两列（同一条水平带上的两格 = 多字段行 ⇒ `trg-3` 判据 A）。
PROSE_LEFT = 56.64
PROSE_RIGHT = 500.0
BODY_SIZE = 12.0
CELL_SIZE = 9.0
TABLE_LEFT = 100.0
TABLE_COL2 = 340.0
CELL_WIDTH = 200.0
ROW_PITCH = 14.0
ROW_HEIGHT = 10.0


class _Doc:
    """合成文档构造器：按**阅读顺序**追加行（line_index 递增，y 递减）。

    `line_index` 是**全文档**递增计数（与既有夹具同一先例）；页是独立坐标域，故
    第 2 页可以复用同一组 y 坐标。
    """

    def __init__(self) -> None:
        self._rows: list = []

    def line(self, *, x0, x1, y0, y1, text, size=BODY_SIZE, bold=False,
             node_id="n1", state="body_under_node",
             attachment="preceding_heading", page=1, furniture=False) -> int:
        index = len(self._rows)
        self._rows.append({
            "page": page, "line_index": index,
            "bbox": (float(x0), float(y0), float(x1), float(y1)),
            "text": text, "size": float(size), "bold": bool(bold),
            "node_id": node_id, "state": state, "attachment": attachment,
            "furniture": bool(furniture)})
        return index

    def prose(self, *, y0, text="公司经营情况说明正文", node_id="n1", page=1):
        return self.line(x0=PROSE_LEFT, x1=PROSE_RIGHT, y0=y0,
                         y1=y0 + ROW_HEIGHT, text=text, size=BODY_SIZE,
                         node_id=node_id, page=page)

    def table(self, *, top_y=200.0, rows=2, node_id="n1", page=1,
              attachment="preceding_heading") -> None:
        """两列 `inside_table` 单元格；每行是一条水平带（`trg-3` 判据 A）。"""
        for step in range(rows):
            y = top_y - step * ROW_PITCH
            self.line(x0=TABLE_LEFT, x1=TABLE_LEFT + CELL_WIDTH, y0=y,
                      y1=y + ROW_HEIGHT, text=f"项目{step}", size=CELL_SIZE,
                      node_id=node_id, attachment=attachment, page=page)
            self.line(x0=TABLE_COL2, x1=TABLE_COL2 + CELL_WIDTH, y0=y,
                      y1=y + ROW_HEIGHT, text=f"数值{step}", size=CELL_SIZE,
                      node_id=node_id, attachment=attachment, page=page)

    def near_table(self, *, top_y, node_id="n1", page=1):
        """表格区域附近、但**不属于**成员资格判据的普通正文行。

        与既有夹具 `_Doc.after` 同一构造理由：起点相对 `TABLE_LEFT` 偏移 7pt
        （躲开判据 B 的列锚点容差）、右端越过第二列起点（躲开判据 C 的标签列片段），
        与表格带保持 8pt 以上的垂直距离（否则会落成 `adjacent_to_table`）。
        """
        y = top_y - 12.0 * ROW_PITCH
        return self.line(x0=TABLE_LEFT + 7.0, x1=TABLE_COL2 + 60.0, y0=y,
                         y1=y + ROW_HEIGHT, text="表后说明文字", size=CELL_SIZE,
                         node_id=node_id, page=page)

    def view(self) -> _FactsView:
        pages: dict = {}
        for row in self._rows:
            pages.setdefault(row["page"], []).append(row)
        layout_pages = []
        states = []
        for page_number in sorted(pages):
            lines = []
            for row in sorted(pages[page_number], key=lambda r: r["line_index"]):
                lines.append(_Line(row["line_index"], row["bbox"], row["text"],
                                   row["size"], row["bold"], row["furniture"]))
                states.append(_State(
                    page_number=page_number, line_index=row["line_index"],
                    state=row["state"], body_attachment=row["attachment"],
                    node_id=row["node_id"]))
            layout_pages.append(_Page(page_number, lines))
        return _FactsView(_Layout(layout_pages), _Structure(tuple(states)))

    def facts(self) -> tuple:
        return SB._collect_line_facts(self.view())


def _runs_of_kind(facts: tuple, kind: str) -> list:
    return [run for run in SB._split_runs(facts) if run[0].kind == kind]


def _page_span(disposition) -> tuple:
    return (int(disposition.start_page), int(disposition.end_page))


# ---------------------------------------------------------------------------
# 2. 正例：跨页 `table_inside` 在构造期按页分段
# ---------------------------------------------------------------------------

def _cross_page_inside_doc() -> _Doc:
    """第 1 页末尾两行表体 + 第 2 页开头两行表体，中间没有别的正文行。

    夹在两段正文之间（前有正文、后有正文），因此这是一条**真实的**跨页
    `table_inside` run，而不是人为拼出来的行序列。
    """
    doc = _Doc()
    doc.prose(y0=260.0)
    doc.table(top_y=200.0, rows=2, page=1)
    doc.table(top_y=200.0, rows=2, page=2)
    doc.prose(y0=120.0, page=2)
    return doc


def _test_positive_cross_page_split():
    doc = _cross_page_inside_doc()
    facts = doc.facts()

    runs = _runs_of_kind(facts, SB._KIND_TABLE_INSIDE)
    if not check(len(runs) == 1,
                 f"夹具必须先造出**恰好一条** `table_inside` 正文范围，得到 {len(runs)} 条"):
        return
    run = runs[0]
    if not check(run[0].key[0] == 1 and run[-1].key[0] == 2,
                 f"该范围必须跨页（第 1 页 → 第 2 页），得到 "
                 f"{run[0].key[0]} → {run[-1].key[0]}"):
        return

    out = SB._build_non_regular_disposition(run)

    check(isinstance(out, list) and len(out) == 2,
          f"跨页 `table_inside` 必须展开成**逐页两条**处置记录，得到 "
          f"{len(out) if isinstance(out, list) else type(out).__name__} 条")
    if not (isinstance(out, list) and len(out) == 2):
        return

    for index, disposition in enumerate(out):
        check(disposition.range_kind == "table_inside",
              f"第 {index + 1} 条必须是 `table_inside`，得到 "
              f"{disposition.range_kind!r}")
        check(disposition.start_page == disposition.end_page,
              f"第 {index + 1} 条必须是**单页**范围（start_page == end_page），得到 "
              f"{_page_span(disposition)}")
    check([_page_span(d)[0] for d in out] == [1, 2],
          f"页序必须与 run 序一致（1, 2），得到 "
          f"{[d.start_page for d in out]}")

    # 逐页行区间必须互不重叠且都在本页内。
    check(out[0].start_line <= out[0].end_line
          and out[1].start_line <= out[1].end_line,
          "每一段的页内行区间必须非空（start_line <= end_line）")
    check(out[0].end_line < out[1].start_line or out[0].start_page < out[1].start_page,
          f"两段不得在页内行区间上重叠：{_page_span(out[0])} 行 "
          f"[{out[0].start_line}, {out[0].end_line}] / {_page_span(out[1])} 行 "
          f"[{out[1].start_line}, {out[1].end_line}]")

    # 守恒口径：分段只重新分账，不增不减。`_build_conservation` 的正文域层按
    # `range_kind` 分桶累加这两项，故"分项之和恰等于合计"正是靠这条加法律维持。
    check(sum(d.line_count for d in out) == len(run),
          f"分段后行数之和必须等于原范围行数 {len(run)}，得到 "
          f"{sum(d.line_count for d in out)}")
    check(sum(d.tight_char_count for d in out)
          == sum(SB.tight_char_count_of(f.line.text) for f in run),
          "分段后 tight 字符数之和必须等于原范围字符数")

    # 归属信息取自各段自己的首帧（`table_scope` / `table_reason` / `node_id`）。
    for index, disposition in enumerate(out):
        head = [f for f in run if f.key[0] == disposition.start_page][0]
        check(disposition.node_id == head.node_id
              and disposition.table_scope == head.table_scope
              and disposition.table_reason == head.table_reason,
              f"第 {index + 1} 条的 node_id / table_scope / table_reason 必须取自"
              f"该段首帧")

    # 身份必须**由内容派生**（位置 + 范围 + 版本一起进身份），不得写死。
    check(len({d.disposition_id for d in out}) == len(out),
          "两段的 disposition_id 必须互不相同（位置进身份）")
    for disposition in out:
        rebuilt = BodyRangeDisposition.create(
            range_kind=disposition.range_kind, node_id=disposition.node_id,
            unassigned_reason=disposition.unassigned_reason,
            table_scope=disposition.table_scope,
            table_reason=disposition.table_reason,
            start_page=disposition.start_page, start_line=disposition.start_line,
            end_page=disposition.end_page, end_line=disposition.end_line,
            line_count=disposition.line_count,
            tight_char_count=disposition.tight_char_count)
        check(rebuilt.disposition_id == disposition.disposition_id,
              "disposition_id 必须由内容身份派生（同参数重算必须相等）")
        check(disposition.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION,
              f"处置记录必须携带当前 TS4 正文算法版本 "
              f"{V.TS4_BODY_SPAN_BUILDER_VERSION!r}，得到 "
              f"{disposition.span_builder_version!r}")
        check(disposition.range_kind not in TB.TABLE_DISPOSITION_KINDS
              or disposition.start_page == disposition.end_page,
              "凡进入 §19.5.1 冻结范围通道的处置记录都必须是单页的")


# ---------------------------------------------------------------------------
# 3. 底线回归：单页 run 的输出与展开前逐字段一致
# ---------------------------------------------------------------------------

def _single_page_inside_doc() -> _Doc:
    doc = _Doc()
    doc.prose(y0=260.0)
    doc.table(top_y=200.0, rows=2, page=1)
    doc.prose(y0=120.0)
    return doc


def _test_baseline_single_page_unchanged():
    facts = _single_page_inside_doc().facts()
    runs = _runs_of_kind(facts, SB._KIND_TABLE_INSIDE)
    if not check(len(runs) == 1, "夹具必须先造出恰好一条单页 `table_inside` 范围"):
        return
    run = runs[0]
    check(run[0].key[0] == run[-1].key[0], "该范围必须落在同一页内")

    out = SB._build_non_regular_disposition(run)
    if not check(len(out) == 1,
                 f"单页 `table_inside` 必须仍是**恰好一条**处置记录，得到 {len(out)}"):
        return

    # 与"整条 run 一个处置"的旧口径逐字段一致：单页路径不得因分段而改变任何字段。
    legacy = BodyRangeDisposition.create(
        range_kind="table_inside", node_id=run[0].node_id, unassigned_reason=None,
        table_scope=run[0].table_scope, table_reason=run[0].table_reason,
        start_page=run[0].key[0], start_line=run[0].key[1],
        end_page=run[-1].key[0], end_line=run[-1].key[1],
        line_count=len(run),
        tight_char_count=sum(SB.tight_char_count_of(f.line.text) for f in run))
    check(out[0].to_dict() == legacy.to_dict(),
          "单页 `table_inside` 的输出必须与分段前**逐字段一致**（分段不得改变既有行为）")

    # 单页 `table_adjacency` 同样只产出一条（本步不覆盖该 kind，不得顺手改它）。
    adjacent = _single_page_adjacent_doc().facts()
    adj_runs = _runs_of_kind(adjacent, SB._KIND_TABLE_ADJACENT)
    if check(len(adj_runs) >= 1, "夹具必须先造出至少一条 `table_adjacency` 范围"):
        adj_out = SB._build_non_regular_disposition(adj_runs[0])
        check(len(adj_out) == 1 and adj_out[0].range_kind == "table_adjacency",
              f"单页 `table_adjacency` 必须仍是一条 `table_adjacency`，得到 "
              f"{[d.range_kind for d in adj_out]}")


# ---------------------------------------------------------------------------
# 4. 反例 A：`frozen_range_bbox` 的单页不变式**未**放宽
# ---------------------------------------------------------------------------

def _cross_page_disposition() -> BodyRangeDisposition:
    return BodyRangeDisposition.create(
        range_kind="table_inside", node_id="n1", unassigned_reason=None,
        table_scope="inside_table", table_reason="table_row_own",
        start_page=1, start_line=0, end_page=2, end_line=3,
        line_count=4, tight_char_count=16)


def _test_negative_single_page_invariant_kept():
    cross = _cross_page_disposition()
    check(cross.start_page != cross.end_page,
          "本反例的前提是构造出一条**跨页**处置记录")

    # `frozen_range_bbox` 在读取任何几何之前就按页判据返回 None —— 传 `ctx=None`
    # 仍必须返回 `None`，这正好证明它是**构造性拒绝**，而不是"几何算不出来"。
    check(TB.frozen_range_bbox(None, cross) is None,
          "跨页范围必须仍被 `frozen_range_bbox` 拒（返回 None），且拒绝发生在读几何之前")

    # 反面对照：单页范围不会被这条 early-return 短路，因此上面的 `None` 不是
    # "该函数永远返回 None"。
    single = BodyRangeDisposition.create(
        range_kind="table_inside", node_id="n1", unassigned_reason=None,
        table_scope="inside_table", table_reason="table_row_own",
        start_page=1, start_line=0, end_page=1, end_line=3,
        line_count=4, tight_char_count=16)
    try:
        TB.frozen_range_bbox(None, single)
    except AttributeError:
        check(True, "单页范围不会被这条 early-return 短路（`None` 只来自跨页判据）")
    except Exception as error:  # noqa: BLE001
        check(False, f"单页范围在无 ctx 时应走到几何读取，得到 {type(error).__name__}")
    else:
        check(False, "单页范围在无 ctx 时不应能算出矩形")

    # 分段之后，进入冻结范围通道的处置记录必须**全部**通过这条不变式。
    facts = _cross_page_inside_doc().facts()
    for run in _runs_of_kind(facts, SB._KIND_TABLE_INSIDE):
        for disposition in SB._build_non_regular_disposition(run):
            if disposition.range_kind in TB.TABLE_DISPOSITION_KINDS:
                check(disposition.start_page == disposition.end_page,
                      "分段后的处置记录必须条条单页，才能通过 §19.5.1 的冻结范围通道")


# ---------------------------------------------------------------------------
# 5. 反例 B：正文不得被误判成表；跨页 `regular` 不切分
# ---------------------------------------------------------------------------

def _test_negative_prose_not_reclassified():
    doc = _cross_page_inside_doc()
    facts = doc.facts()

    # 正文行**不得**被分段成表处置：断言它们一条都不是 `table_inside`（分段只作用于
    # 该 kind），并进一步断言两段单页处置的行区间**只落在表体行上**、不覆盖任何正文行。
    prose = [f for f in facts if "说明正文" in f.line.text]
    check(len(prose) == 2 and not any(f.kind == SB._KIND_TABLE_INSIDE
                                      for f in prose),
          f"正文行不得被分段误判成 `table_inside`，得到 "
          f"{[(f.line.text, f.kind) for f in prose]}")

    cell_keys = {f.key for f in facts if f.kind == SB._KIND_TABLE_INSIDE}
    prose_keys = {f.key for f in prose}
    for run in _runs_of_kind(facts, SB._KIND_TABLE_INSIDE):
        for disposition in SB._build_non_regular_disposition(run):
            covered = {(disposition.start_page, li)
                       for li in range(disposition.start_line,
                                       disposition.end_line + 1)}
            check(covered and covered <= cell_keys,
                  f"单页处置的行区间必须只覆盖表体行 {sorted(cell_keys)}，得到 "
                  f"{sorted(covered)}")
            check(not (covered & prose_keys),
                  "单页处置不得覆盖任何正文行的位置")

    # 跨页 `regular` 范围根本不进分段函数：对**每一条** regular 范围直接调用都必须
    # fail-closed，因此分段不可能越界到正文，也不可能凭空多出表处置。
    regular_runs = _runs_of_kind(facts, SB._KIND_REGULAR)
    if check(len(regular_runs) >= 1, "夹具必须先造出至少一条 `regular` 正文范围"):
        for index, run in enumerate(regular_runs):
            raises(lambda r=run: SB._build_non_regular_disposition(r),
                   SB.SpanBuildError, "非 regular 正文范围不得由",
                   f"第 {index + 1} 条常规正文范围不得走非 regular 处置构造")


def _test_negative_table_adjacency_not_split():
    """本步**不**覆盖跨页 `table_adjacency`：如实钉住当前行为，不假装已修。"""
    doc = _Doc()
    doc.prose(y0=260.0)
    doc.table(top_y=200.0, rows=2, page=1)
    doc.near_table(top_y=200.0, page=1)
    doc.near_table(top_y=200.0, page=2)
    doc.prose(y0=120.0, page=2)
    facts = doc.facts()
    runs = _runs_of_kind(facts, SB._KIND_TABLE_ADJACENT)
    if not runs:
        skip("合成夹具未产出 `table_adjacency` 范围；跨页邻近闭包由既有模块覆盖")
        return
    out = SB._build_non_regular_disposition(runs[0])
    check(len(out) == 1,
          f"跨页 `table_adjacency` 在本步**不**分段（仍是一条），得到 {len(out)} 条")
    check(out[0].range_kind == "table_adjacency",
          "跨页 `table_adjacency` 的 range_kind 不得被改写成 `table_inside`")


def _single_page_adjacent_doc() -> _Doc:
    doc = _Doc()
    doc.prose(y0=260.0)
    doc.table(top_y=200.0, rows=2, page=1)
    doc.near_table(top_y=200.0, page=1)
    return doc


# ---------------------------------------------------------------------------
# 6. 反例 C：非单调页序 fail-closed
# ---------------------------------------------------------------------------

class _Frame:
    __slots__ = ("key",)

    def __init__(self, key) -> None:
        self.key = key


def _test_negative_non_monotone_pages():
    # 同一页在一条 run 里出现两次时，"按页分段"会把同一页的字符拆进两条处置记录。
    raises(lambda: SB._page_segments((_Frame((1, 0)), _Frame((2, 0)),
                                      _Frame((1, 5)))),
           SB.SpanBuildError, "同一页出现两次",
           "一条范围内同一页出现两次时必须 fail-closed（不得悄悄拆成两条）")

    # 正面对照：单调页序（含同一页多行）正常分段。
    segs = SB._page_segments((_Frame((1, 0)), _Frame((1, 1)), _Frame((2, 0))))
    check([len(seg) for seg in segs] == [2, 1],
          f"单调页序必须切成 [2, 1] 两段，得到 {[len(s) for s in segs]}")
    check(len(SB._page_segments((_Frame((1, 0)),))) == 1,
          "单帧范围必须退化为一段")


# ---------------------------------------------------------------------------
# 7. 版本与迁移登记（W1 的 `sb-7 → sb-8`；表格构造 `tb-5 → tb-6`、几何 `tgeo-4`）
# ---------------------------------------------------------------------------

def _test_version_registration():
    check(V.TS4_BODY_SPAN_BUILDER_VERSION == "sb-8",
          f"TS4 正文算法当前版本必须是 `sb-8`，得到 "
          f"{V.TS4_BODY_SPAN_BUILDER_VERSION!r}")
    check("sb-7" in V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS,
          f"`sb-7` 必须登记为 TS4 正文算法的 legacy 值，得到 "
          f"{V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS!r}")
    check(V.classify_schema_version("TS4_BODY_SPAN_BUILDER_VERSION", "sb-8")
          == "current",
          "`sb-8` 必须被分类为 current")
    check(V.classify_schema_version("TS4_BODY_SPAN_BUILDER_VERSION", "sb-7")
          == "legacy",
          "`sb-7` 必须被分类为 legacy（不是 unknown）")

    check(V.TABLE_BUILDER_VERSION == "tb-6",
          f"表格构造当前版本必须是 `tb-6`，得到 {V.TABLE_BUILDER_VERSION!r}")
    check("tb-5" in V.legacy_versions("TABLE_BUILDER_VERSION"),
          f"`tb-5` 必须登记为表格构造的 legacy 值，得到 "
          f"{V.legacy_versions('TABLE_BUILDER_VERSION')}")
    check(V.classify_schema_version("TABLE_BUILDER_VERSION", "tb-6") == "current",
          "`tb-6` 必须被分类为 current")
    check(V.classify_schema_version("TABLE_BUILDER_VERSION", "tb-5") == "legacy",
          "`tb-5` 必须被分类为 legacy（不是 unknown）")
    check(V.classify_schema_version("TABLE_BUILDER_VERSION", "tb-4") == "legacy",
          "`tb-4` 必须被分类为 legacy（不是 unknown）")

    # §0.19（乙）的几何侧：`lines` 通道多加了一路（只把细长描边当边界），同一份
    # PageLayout 在 `tgeo-3` 与 `tgeo-4` 下可能得到**不同**网格，故按同一政策升版且
    # `tgeo-3` 登记为 legacy——不得静默按 `tgeo-4` 解释历史网格。
    check(V.TABLE_GEOMETRY_VERSION == "tgeo-4",
          f"表几何当前版本必须是 `tgeo-4`，得到 {V.TABLE_GEOMETRY_VERSION!r}")
    check("tgeo-3" in V.legacy_versions("TABLE_GEOMETRY_VERSION"),
          f"`tgeo-3` 必须登记为表几何的 legacy 值，得到 "
          f"{V.legacy_versions('TABLE_GEOMETRY_VERSION')}")
    check(V.classify_schema_version("TABLE_GEOMETRY_VERSION", "tgeo-4")
          == "current"
          and V.classify_schema_version("TABLE_GEOMETRY_VERSION", "tgeo-3")
          == "legacy",
          "`tgeo-4` 必须是 current、`tgeo-3` 必须是 legacy（不是 unknown）")

    # 算法版本进身份：同一份位置/范围在两个算法版本下必须是**不同**的处置记录身份，
    # 因此历史 `sb-7` 产物不可能被静默当作 `sb-8` 产物复用。
    payload = dict(
        range_kind="table_inside", node_id="n1", unassigned_reason=None,
        table_scope="inside_table", table_reason="table_row_own",
        start_page=1, start_line=0, end_page=1, end_line=1,
        line_count=2, tight_char_count=8)
    current = BodyRangeDisposition.create(**payload)
    check(current.span_builder_version == "sb-8",
          "新构造的处置记录必须携带 `sb-8`")
    # 版本进身份：把当前载荷的版本字段手工改成 `sb-7` 后，`disposition_id` 不再等于
    # 由载荷派生的身份，读回必须 fail-closed。
    tampered = {**current.to_dict(), "span_builder_version": "sb-7"}
    raises(lambda: BodyRangeDisposition.from_dict(tampered),
           SchemaValidationError, "必须为 TS4 正文算法",
           "把处置记录的算法版本改成 `sb-7` 必须被拒绝（版本进身份、旧版本读回受限）")


def main() -> dict:
    _test_positive_cross_page_split()
    _test_baseline_single_page_unchanged()
    _test_negative_single_page_invariant_kept()
    _test_negative_prose_not_reclassified()
    _test_negative_table_adjacency_not_split()
    _test_negative_non_monotone_pages()
    _test_version_registration()
    return _results


if __name__ == "__main__":
    import json
    out = main()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out["failed"] == 0 else 1)

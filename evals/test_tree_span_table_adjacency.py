# -*- coding: utf-8 -*-
"""TS4-A P1 反例：表格 provisional 邻接闭包（表题 / 单位行 / 表后表注不得成为正文材料）。

被测对象是 `span_builder._collect_line_facts` 里的**业务处置**（表格**前导**闭包与
表格**尾部**闭包）以及它的下游 `_split_runs` / `_build_non_regular_disposition`。
本模块锁定：

1. **闭包形态**：从已经证明的 `inside_table` / `adjacent_to_table` 区域出发，向紧邻的
   **前后两侧**建立**有限、可复核**的 provisional 闭包：
   * **前导（向上）**覆盖"表题 → 单位行 → 表体"；
   * **尾部（向下）**覆盖"表体 → 表后表注 / 表后残段"，即**表后相邻内容进入 TS5 合法
     裁决入口**的那一段（TS4 不裁决"这是不是表注"，只把它做成 bounded provisional）。
   两个方向被吸收的行一律落成同**一个**签名 `range_kind="table_adjacency"` +
   `table_scope="none"` + `table_reason="table_row_adjacent"`：**不产生 span、
   不进简介、不计入可引用覆盖**，原文 / 页码 / 行号 / 几何 / 节点 / 状态全部保留；
2. **边界**：不得跨越 heading、节点边界、空白断点、formal unassigned；不得无界扩展。
   前导"连续前导区域"只由**几何**与源序判定，逐行有四类终止条件，且**只有这四类**：

   * **结构边界**：不同节点 / 非 `regular` 类别（含 heading / 未归属 / 空白）；
   * **满栏正文**：贴正文栏左边界（`x0 ≤ doc_left + 容差`），或宽度达到**声明栏宽上界**
     的 `0.7`（后者同时盖住"缩进的正文续行"与"居中的满栏行"—— 单一版式特征不够，
     宽栏本身就是负证明）；
   * **字号偏大**：大于正文基准字号；
   * **折行续行**：候选的**上一条**（源序相邻、非家具行）是同节点、同左边界、且
     **自身对该区域没有任何锚定证据**的 `regular` 行 ⇒ 候选只是它的续行。折行残段
     很窄，窄表格区域的水平中心又极易与它对齐，"居中"这一条单一版式特征不足以证明
     它是表格前导。

   行**左边界与上一行相同**（表题行与单位行同左边界正是最常见的真实形态）**不是**
   终止条件：真实的多行前导允许连续同左边界，只有"上一行**自己**没有资格被当成前导"
   才切断。宽度装不下 `_TABLE_LEADING_MIN_EM` 个字宽的**退化碎片**（页码 / 装饰线 /
   零散短标签）既不是正文也不是前导，**随所在连续区域一起进入 provisional**，不切断上溯。
   这些全部是几何量，不依赖任何文字形态（"以表字开头"这类文字过滤在本模块里只用于
   **诊断统计**，绝不参与判定，见 `_caption_form` / `_unit_form`）。

   尾部（向下）走查的判据是**物理邻接**，同样只有几何 / 结构量：

   * **起始资格（全部满足才可能进入 provisional）**：区域含至少一条真实
     `inside_table`；与区域**同节点**；与区域尾行**同一物理页**；与区域水平跨度
     **交叠**（这就是"同一正文栏"）；与上一行在真实源序上**直接相邻**（中间不夹家具
     行）；与区域尾行的垂直间距 ≤ `TRAILING_PITCH_FACTOR` × 本页本栏**局部行距**
     （由真实 `bbox` 与类别快照确定性派生）；
   * **后续连续行**：同页 / 同节点 / 同栏交叠 / 源序连续 / 间距仍在阈值内 / 总数不超过
     固定上限 `TRAILING_CAP`；
   * **任何**停止（文档末 / 新表格类别段 / 非 `regular` 类别 / 别的节点 / 夹着家具行 /
     跨页 / 栏不交叠 / 间距超阈值 / 达到上限）都**只**保留已经通过物理邻接检查的
     **staged prefix**，绝不越过停止行继续下探。这里**没有**"命中某种内容形态即整段
     丢弃"的语义。

   满栏与不满栏在起始资格与续行判据上**完全同权**：尾部闭包**不要求**任何几何标记，
   真实表注与正文段落的字号 / 左边界 / 行距都可以**完全一致**（本模块的 `_pitch` 级
   诊断已证垂直行距不可分），因此满栏**不是**排除理由。TS4 **不判断"这是表注"**，
   也不判断"这是不是普通正文"：它只交付有限 provisional 段，由 TS5 用不可变
   `TableRangeDecision` 裁决为 `absorbed_as_note` / `kept_as_paragraph` /
   `absorbed_into_body` / `unresolved_geometry`。普通正文被暂时暂存**不是内容丢失**
   —— 原文与精确定位完整保留、范围有界、TS5 能确定性恢复为正文段；反过来，把"避免
   暂存普通正文"当理由，就会让真实表注永久留在 TS4 正式正文层（进 span、进简介）；
3. **诚实性**：只有 `adjacent_to_table` 而没有已证明 `inside_table` 的区域**不得**
   声称绑定到某个表格对象，保留 `table_scope="adjacent_to_table"`；
4. **真实文档**：三份真实冻结文档与非 300750 夹具走**同一条规则**，且生产代码里
   没有任何公司名 / 固定页码 / 表号 / evidence_id 之类的专用字面量；独立验收指定的
   **四组真实表后表注**（`_REQUIRED_TRAILING_SAMPLES`）必须不再是正式 `body` span；
5. **守恒与可读性**：行数 / tight 字符数守恒（吸收是**改桶**而不是删文本）；TS5
   仍能从处置记录里读到全文与精确位置（`kept_as_paragraph` / `absorbed_into_body`
   都建立在"原文与定位完整保留"之上）。

静态检查（`_case_no_case_literals`）**不按名字列举帮助函数**：它从两个闭包出发做
**模块内可达函数的传递闭包**（`ast` 扫描被调用的模块内函数），因此把判定拆进新 helper
不能绕过禁字 / 禁文字判据的检查（P2）。

本模块只读仓库内冻结产物与夹具成员文件；不连网络、不写数据库、不写生成结果。
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import re
import sys
import textwrap

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import tree_stage_env as STAGE  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_policy as SP  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure.evidence_gateway import (  # noqa: E402
    fixture_root_dir,
    load_fixture_root,
)
from document_structure.schema import PageLayout  # noqa: E402

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

#: 冻结的 TS3 标题树产物（**只读**；本模块不重跑、不改写它）。
_FROZEN_TS3_RUN = (REPO / "evaluation" / "results"
                   / "tree_structure_ts3_outline_ts3_outline_tocr2_closure"
                     "_p2final_20260918T130000Z")
_REAL_DOCUMENTS = ("NDSD_KCZ_2026", "NDSD_2024_year", "NDSD_2025_year")
_FIXTURE_DOCUMENT = "FIXTURE_BOND_2026"

#: 闭包产物的**唯一**签名（schema 层只接受这一组：`table_adjacency` 的
#: `table_reason` 必须恰为 `table_row_adjacent`）。
CLOSURE_SCOPE = "none"
CLOSURE_REASON = "table_row_adjacent"
TABLE_KINDS = ("table_inside", "table_adjacency")


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


def _one_disposition(run):
    """单条正文范围的处置记录（W1 后 `_build_non_regular_disposition` 返回**列表**）。

    本模块的全部夹具与真实样本都建立在"这一段恰好一条处置记录"之上：它们要么是单页
    范围，要么是不参与按页分段的 `table_adjacency`（尾部闭包按设计在本页内停止）。
    跨页 `table_inside` 会展开成**多条**单页记录，那种行为由
    `evals.test_tree_span_cross_page_table` 单独覆盖；这里若出现多条，说明本模块的
    夹具前提已失效，必须显式报错，而不是静默取第一条。
    """
    out = SB._build_non_regular_disposition(run)
    if len(out) != 1:
        raise AssertionError(
            f"本模块的夹具必须产生恰好一条处置记录，得到 {len(out)} 条"
            f"（范围 {run[0].key} → {run[-1].key}）")
    return out[0]


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
# `_collect_line_facts` 与 `table_region_scopes` 只读下面这些字段；本模块用**只读
# 投影**把它们凑出来（与 `test_tree_span_builder._FactsView` 同一先例），不构造任何
# 生产 capability，也不新增任何旁路。

class _Span:
    __slots__ = ("text", "size", "is_bold")

    def __init__(self, text: str, size: float, is_bold: bool) -> None:
        self.text = text
        self.size = size
        self.is_bold = is_bold


class _Line:
    __slots__ = ("line_index", "bbox", "spans", "is_furniture", "text")

    def __init__(self, line_index: int, bbox: tuple, text: str, size: float,
                 is_bold: bool, is_furniture: bool) -> None:
        self.line_index = line_index
        self.bbox = bbox
        self.text = text
        self.spans = (_Span(text, size, is_bold),)
        self.is_furniture = is_furniture


class _Page:
    __slots__ = ("page_number", "lines")

    def __init__(self, page_number: int, lines: list) -> None:
        self.page_number = page_number
        self.lines = tuple(lines)


class _Layout:
    __slots__ = ("pages",)

    def __init__(self, pages: list) -> None:
        self.pages = tuple(pages)


class _State:
    __slots__ = ("page_number", "line_index", "state", "body_attachment", "node_id")

    def __init__(self, *, page_number: int, line_index: int, state: str,
                 body_attachment: str | None, node_id: str | None) -> None:
        self.page_number = page_number
        self.line_index = line_index
        self.state = state
        self.body_attachment = body_attachment
        self.node_id = node_id


class _Structure:
    __slots__ = ("line_states",)

    def __init__(self, states: tuple) -> None:
        self.line_states = states


class _FactsView:
    """`_collect_line_facts` 只读的两个字段（测试专用只读投影）。"""

    __slots__ = ("page_layout", "structure_snapshot")

    def __init__(self, page_layout, structure_snapshot) -> None:
        self.page_layout = page_layout
        self.structure_snapshot = structure_snapshot


#: 合成版式的几何常量（pt）。正文行贴文档左边界，表格两列，表题居中偏左。
PROSE_LEFT = 56.64
PROSE_RIGHT = 500.0
BODY_SIZE = 12.0
CELL_SIZE = 9.0
TABLE_LEFT = 100.0
TABLE_COL2 = 340.0
CELL_WIDTH = 200.0
UNIT_LEFT = 460.0
UNIT_WIDTH = 60.0
CAPTION_LEFT = 180.0
CAPTION_RIGHT = 420.0
#: "同左边界连续前导"案例：表题行与单位行共用同一条左边界（真实年报里最常见的形态），
#: 两者都落在表格水平中心（320.0）右侧、宽度远小于栏宽。
LEADING_LEFT = 430.0
LEADING_RIGHT = 520.0
#: 退化碎片案例：宽度装不下 `FRAGMENT_MIN_EM` 个字宽（9pt × 3 = 27pt）。
FRAGMENT_LEFT = 470.0
FRAGMENT_WIDTH = 16.0
#: 独立重算口径的"退化碎片"字宽下限（与构建器 `_TABLE_LEADING_MIN_EM` 同义）。
FRAGMENT_MIN_EM = 3.0
#: 独立重算口径的"满栏 / 近满栏正文"宽度比例（相对**声明栏宽上界**）。
PROSE_WIDTH_FRAC = 0.7
#: 独立重算口径的尾部闭包上限（与构建器 `_TABLE_TRAILING_CAP` 同义）。
TRAILING_CAP = 3
#: 独立重算口径的尾部垂直间距倍数（与构建器 `_TABLE_TRAILING_PITCH_FACTOR` 同义）：
#: 候选与区域尾行（或上一候选）的垂直间距至多这么多个**本页本栏局部行距**。
TRAILING_PITCH_FACTOR = 2.0
#: 独立验收指定的**四组真实表后表注**（验收锚点：只用于测试核对，生产判定里不得
#: 出现这些坐标 —— `_case_no_case_literals` 会拒绝任何固定页码 / 行号判据）。
_REQUIRED_TRAILING_SAMPLES = (
    ("NDSD_2024_year", ((163, 40), (163, 41))),
    ("NDSD_2025_year", ((27, 95), (160, 29), (222, 25), (222, 26))),
)


class _Doc:
    """合成文档构造器：按**阅读顺序**追加行（line_index 递增，y 递减）。"""

    def __init__(self) -> None:
        self._rows: list = []

    def line(self, *, x0: float, x1: float, y0: float, y1: float, text: str,
             size: float = BODY_SIZE, bold: bool = False, node_id: str = "n1",
             state: str = "body_under_node",
             attachment: str | None = "preceding_heading",
             page: int = 1, furniture: bool = False) -> int:
        index = len(self._rows)
        self._rows.append({
            "page": page, "line_index": index,
            "bbox": (float(x0), float(y0), float(x1), float(y1)),
            "text": text, "size": float(size), "bold": bool(bold),
            "node_id": node_id, "state": state, "attachment": attachment,
            "furniture": bool(furniture)})
        return index

    # -- 常用骨架 ---------------------------------------------------------
    def prose(self, *, y0: float, text: str = "公司经营情况说明正文", x0: float = PROSE_LEFT,
              x1: float = PROSE_RIGHT, size: float = BODY_SIZE,
              node_id: str = "n1", page: int = 1) -> int:
        return self.line(x0=x0, x1=x1, y0=y0, y1=y0 + 10.0, text=text, size=size,
                         node_id=node_id, page=page)

    def table(self, *, top_y: float = 100.0, node_id: str = "n1",
              rows: int = 2, page: int = 1, attachment: str = "preceding_heading"
              ) -> None:
        """两列 `inside_table` 单元格；每行是一条水平带（判据 A）。"""
        for step in range(rows):
            y = top_y - step * 14.0
            self.line(x0=TABLE_LEFT, x1=TABLE_LEFT + CELL_WIDTH, y0=y, y1=y + 10.0,
                      text=f"项目{step}", size=CELL_SIZE, node_id=node_id,
                      attachment=attachment, page=page)
            self.line(x0=TABLE_COL2, x1=TABLE_COL2 + CELL_WIDTH, y0=y, y1=y + 10.0,
                      text=f"数值{step}", size=CELL_SIZE, node_id=node_id,
                      attachment=attachment, page=page)

    def unit(self, *, y0: float = 112.0, text: str = "单位：万元",
             node_id: str = "n1", page: int = 1) -> int:
        return self.line(x0=UNIT_LEFT, x1=UNIT_LEFT + UNIT_WIDTH, y0=y0, y1=y0 + 10.0,
                         text=text, size=CELL_SIZE, node_id=node_id, page=page)

    def caption(self, *, y0: float = 124.0, text: str = "表4-1 主营业务构成表",
                x0: float = CAPTION_LEFT, x1: float = CAPTION_RIGHT,
                size: float = BODY_SIZE, bold: bool = True,
                node_id: str = "n1", page: int = 1) -> int:
        return self.line(x0=x0, x1=x1, y0=y0, y1=y0 + 10.0, text=text, size=size,
                         bold=bold, node_id=node_id, page=page)

    def after(self, *, y0: float, text: str = "表后说明文字",
              x0: float = TABLE_LEFT + 7.0,
              x1: float = TABLE_LEFT + CELL_WIDTH + 60.0, size: float = CELL_SIZE,
              node_id: str = "n1", page: int = 1,
              state: str = "body_under_node",
              attachment: str | None = "preceding_heading",
              furniture: bool = False) -> int:
        """表后残段候选行：**未被冻结 `trg-3` 判进任何表格范围**的普通正文行。

        默认几何是刻意选的 —— 这一行必须同时躲开 `trg-3` 的两条成员资格判据，否则测的
        就不是本轮的尾部闭包，而是冻结版式判定本身：

        * 判据 B（列锚点成员）：起点落在多字段行的列锚点上（容差 3pt）**且**字号等于
          该行某格字号时，垂直可达 **30pt** —— 所以 `x0` 相对 `TABLE_LEFT` 偏移 7pt，
          也所以调用方不能靠"离得比 8pt 远"来躲开它；
        * 判据 C（固定宽度标签列片段）：两条非结构行构成片段（间距 ≤ 4pt、起点相差
          ≤ 12pt）且右端止于第二列起点前 —— 所以默认 `x1` 越过第二列起点。

        距离 ≤ `TABLE_REGION_PROXIMITY_PT` 会落成 `adjacent_to_table`：调用方仍须自行
        保证与表格区域的距离 > 8pt。
        """
        return self.line(x0=x0, x1=x1, y0=y0, y1=y0 + 10.0, text=text, size=size,
                         node_id=node_id, page=page, state=state,
                         attachment=attachment, furniture=furniture)

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


# ---------------------------------------------------------------------------
# 2. 事实序列上的常用视图与独立重算
# ---------------------------------------------------------------------------

def _tight(text: str) -> str:
    return "".join(text.split())


def _line_size(line) -> float:
    sizes = [span.size for span in line.spans if _tight(span.text) != ""]
    return max(sizes) if sizes else 0.0


def _x0(fact) -> float:
    return float(fact.line.bbox[0])


def _x2(fact) -> float:
    return float(fact.line.bbox[2])


def _width(fact) -> float:
    return _x2(fact) - _x0(fact)


def _bold(fact) -> bool:
    return any(span.is_bold for span in fact.line.spans)


def _center(fact) -> float:
    return (_x0(fact) + _x2(fact)) / 2.0


def _kinds(facts: tuple) -> dict:
    return {fact.key: fact.kind for fact in facts}


def _absorbed(facts: tuple) -> tuple:
    """闭包产物：`table_adjacency` 且自报 `table_scope == "none"` 的行。"""
    return tuple(f for f in facts
                 if f.kind == SB._KIND_TABLE_ADJACENT
                 and f.table_scope == CLOSURE_SCOPE)


def _real_adjacent(facts: tuple) -> tuple:
    """版式本来就判定的邻近行（`table_scope == "adjacent_to_table"`）。"""
    return tuple(f for f in facts
                 if f.kind == SB._KIND_TABLE_ADJACENT
                 and f.table_scope == "adjacent_to_table")


def _regions(facts: tuple) -> list:
    """表格区域的极大连续段（`inside_table` / `adjacent_to_table`）。"""
    regions: list = []
    current: list = []
    for fact in facts:
        if fact.kind in TABLE_KINDS:
            current.append(fact)
        else:
            if current:
                regions.append(current)
            current = []
    if current:
        regions.append(current)
    return regions


def _region_after(facts: tuple, position: int) -> list:
    """`position` **之后**紧邻的表格类别段（含已被闭包吸收的同签名行）。"""
    step = position + 1
    region: list = []
    while step < len(facts) and facts[step].kind in TABLE_KINDS:
        region.append(facts[step])
        step += 1
    return region


def _region_before(facts: tuple, position: int) -> list:
    """`position` **之前**紧邻的表格类别段（含已被闭包吸收的同签名行）。"""
    step = position - 1
    region: list = []
    while step >= 0 and facts[step].kind in TABLE_KINDS:
        region.insert(0, facts[step])
        step -= 1
    return region


def _table_band(region: list) -> tuple:
    """**独立重算**的区域水平跨度：只由 `inside_table` 成员给出 `(x0, x2)`。

    `table_adjacency` 成员（冻结的邻近行、闭包暂存行）只是"邻近"，**不扩张**表格的
    水平证据范围 —— 与构建器 `_table_closure_band` 同一口径。
    """
    inside = [f for f in region if f.kind == SB._KIND_TABLE_INSIDE]
    return (min(_x0(f) for f in inside), max(_x2(f) for f in inside))


def _band_overlap(fact, band: tuple) -> float:
    """候选与区域水平跨度的**交叠宽度**（≤ 0 即"不在同一正文栏"）。"""
    return min(_x2(fact), band[1]) - max(_x0(fact), band[0])


def _vertical_gap(previous, fact) -> float:
    """两行盒沿**阅读方向**的垂直间距（垂直重叠记 `0`，与 y 轴朝向无关）。

    取两个方向的盒间距的**较大者**：真实 `PageLayout` 与合成版式夹具的 y 轴朝向相反，
    两者必须得到同一个几何量，因此重算不得假定"y 越大越靠下"。
    """
    return max(0.0,
               float(fact.line.bbox[1]) - float(previous.line.bbox[3]),
               float(previous.line.bbox[1]) - float(fact.line.bbox[3]))


def _local_pitch(order, order_index, kinds, *, page: int, band: tuple,
                 tail) -> float:
    """**独立重算**的本页本栏**局部行距**：同页相邻行起点间距的中位数。

    样本优先级与构建器 `_table_closure_local_pitch` 一致：同页且与 `band` 交叠的
    改桶前正文行 → 同页全部非家具行 → 区域尾行的行高。全部是 `bbox` / 页码 /
    类别快照的确定性函数，不含任何固定页码 / 公司 / 表号 / 坐标常量。
    """
    def _median(rows: list):
        rows = sorted(rows, key=lambda fact: order_index[fact.key])
        deltas = sorted(
            abs(float(second.line.bbox[1]) - float(first.line.bbox[1]))
            for first, second in zip(rows, rows[1:])
            if int(second.state.page_number) == int(first.state.page_number))
        deltas = [delta for delta in deltas if delta > 0.0]
        return deltas[len(deltas) // 2] if deltas else None

    same_page = [fact for fact in order
                 if int(fact.state.page_number) == page]
    body = [fact for fact in same_page
            if kinds[fact.key] == SB._KIND_REGULAR
            and _band_overlap(fact, band) > 0.0]
    pitch = _median(body)
    if pitch is None:
        pitch = _median(same_page)
    if pitch is not None:
        return pitch
    return abs(float(tail.line.bbox[3]) - float(tail.line.bbox[1]))


def _prose_width(facts: tuple) -> float:
    """**声明栏宽上界**：全部非家具行的最大水平跨度（与构建器同一口径）。"""
    widths = [_width(f) for f in facts if not f.line.is_furniture]
    return max(widths) if widths else 0.0


def _prose_shape(facts: tuple) -> tuple:
    """**独立重算**的"满栏正文形态"：贴正文左边界、或宽达声明栏宽上界的 0.7。
    这样的行在闭包里没有任何可用证据 —— 因此绝不能被吸收。"""
    regular = [f for f in facts if f.kind == SB._KIND_REGULAR]
    if not regular:
        return ()
    doc_left = min(_x0(f) for f in regular)
    limit = PROSE_WIDTH_FRAC * _prose_width(facts)
    out = []
    for fact in regular:
        if _x0(fact) <= doc_left + 3.0 or _width(fact) >= limit:
            out.append(fact)
    return tuple(out)


def _fragment(fact) -> bool:
    """**独立重算**的"退化碎片"：宽度装不下 `FRAGMENT_MIN_EM` 个字宽。"""
    return _width(fact) < FRAGMENT_MIN_EM * _line_size(fact.line)


def _marks(fact, region_top, inside, prose_width: float) -> tuple:
    """**独立重算**闭包标记（几何量，不看任何文字）：
    `(居中, 加粗, 靠右窄排, 区域右侧窄排)`。"""
    in_x0 = min(_x0(f) for f in inside)
    in_x2 = max(_x2(f) for f in inside)
    center = (in_x0 + in_x2) / 2.0
    width = _width(fact)
    size = _line_size(fact.line)
    centered = abs(_center(fact) - center) <= 3.0
    bold = _bold(fact)
    narrow = width >= FRAGMENT_MIN_EM * size
    right_side = _x0(fact) >= center and narrow and width <= 0.35 * (in_x2 - in_x0)
    beyond_right = (_x0(fact) >= in_x2 and narrow
                    and width <= 0.35 * prose_width)
    return (centered, bold, right_side, beyond_right)


def _anchored(fact, region, inside, prose_width: float) -> bool:
    """**独立重算**的"锚定到表格区域"：退化碎片或至少一条几何标记。

    与 `_leading_stop_reason` 的可吸收判据是**同一个** —— 折行续行守卫用
    "上一行自己有没有资格被当成前导"来判定。"""
    return _fragment(fact) or any(_marks(fact, region[0], inside, prose_width))


def _previous_in_order(facts: tuple, position: int):
    """源序上一条**非家具**行（与构建器 `order` 同一口径；`facts` 含家具行）。"""
    step = position - 1
    while step >= 0 and facts[step].line.is_furniture:
        step -= 1
    return facts[step] if step >= 0 else None


def _prose_wrap_continuation(fact, previous, region, inside,
                             prose_width: float) -> bool:
    """**独立重算**折行续行守卫：本行是否只是上面那行**普通正文**的续行。

    成立条件（全部是几何 + 源序 + 已证明的区域归属，**不看文字**）：上一行存在、
    是 `regular`、同节点、与本行左边界相同（≤ 3.0pt），且上一行**自身**对该区域
    没有任何锚定证据。
    """
    if previous is None or previous.kind != SB._KIND_REGULAR:
        return False
    if previous.node_id != fact.node_id:
        return False
    if abs(_x0(previous) - _x0(fact)) > 3.0:
        return False
    return not _anchored(previous, region, inside, prose_width)


def _caption_form(text: str) -> bool:
    """表题 / 单位行的**文字形态**（只作诊断统计，**绝不**作判定输入）。"""
    return bool(re.match(r"^(表\s*[0-9０-９]|单位\s*[:：]|注\s*[:：]|资料来源)",
                         _tight(text)))


def _unit_form(text: str) -> bool:
    """单位行的**文字形态**（只作诊断统计，**绝不**作判定输入）。"""
    return bool(re.match(r"^单位\s*[:：]", _tight(text)))


def _note_form(text: str) -> bool:
    """**表注 / 表后说明**的文字形态（只作诊断统计，**绝不**作判定输入）。

    生产代码里没有、也不得有这条判据：它只在本模块充当"第 13 类反例"的抽样探针，
    以及真实验收报告里的人工核对清单。
    """
    return bool(re.match(r"^(注[:：]|说明[:：]|备注[:：]|资料来源|数据来源)",
                         _tight(text)))


def _leading_stop_reason(fact, region, inside, *, node_id, document_left,
                         body_size, prose_limit, prose_width, previous=None,
                         kind=None):
    """**独立重算**：这一行在闭包里会不会被吸收；不吸收则返回停止原因。

    条件顺序与生产实现一致，但**只**用几何与结构事实：类别 / 节点 → 贴正文栏左边界
    → 字号 → 满栏（近满栏）正文形态 → **折行续行**（上一行是同左边界、无区域证据的
    普通正文）→ 退化碎片（可直接吸收）→ 几何标记。返回 `None` 表示"可被吸收"。

    `kind` 可显式传入**改桶之前**的类别（`_pre_closure_kinds`），使本判据在闭包产物上
    也能重放。
    """
    if (fact.kind if kind is None else kind) != SB._KIND_REGULAR:
        return f"类别是 {fact.kind}"
    if fact.node_id != node_id:
        return "跨节点"
    if _x0(fact) <= document_left + 3.0:
        return "贴正文栏左边界"
    if body_size is not None and _line_size(fact.line) > body_size + 0.5:
        return "字号偏大"
    if _width(fact) >= prose_limit:
        return "满栏 / 近满栏正文形态"
    if _prose_wrap_continuation(fact, previous, region, inside, prose_width):
        return "上一行是同左边界的普通正文 ⇒ 本行是它的折行续行"
    if _fragment(fact):
        return None
    if not any(_marks(fact, region[0], inside, prose_width)):
        return "一条几何标记都不满足"
    return None


def _leading_reason(facts, index, fact, prose, prose_width):
    """**独立重算**：这一**行**作为"表格前导"会不会被吸收；不会被吸收则返回原因。

    一行只有同时满足下面几条，才算"已被证明的表格前导"：

      (a) 它后面（中间不夹别的行）就是一段含 `inside_table` 的已证明表格区域；
      (b) 它与该区域**同节点**；
      (c) 它不是满栏正文形态（见 `_prose_shape`），且 **要么**对该区域至少满足一条
          **几何**标记（加粗 / 与表格水平中心对齐 / 落在表格右半且窄排 / 整行落在
          区域右侧的窄排行），**要么**它本身就是退化碎片（宽度装不下 3 个字宽）；
      (d) 它**不是**上面那行普通正文的**折行续行**（见 `_prose_wrap_continuation`）。
    """
    if fact.key in prose:
        return "满栏正文形态的行不能成为前导"
    position = index[fact.key]
    step = position + 1
    region: list = []
    while step < len(facts) and facts[step].kind in TABLE_KINDS:
        region.append(facts[step])
        step += 1
    inside = [f for f in region if f.kind == SB._KIND_TABLE_INSIDE]
    if not inside:
        return "后方没有已证明的 inside_table 区域"
    if any(f.node_id != fact.node_id for f in region):
        return "与表格区域跨节点"
    if not _anchored(fact, region, inside, prose_width):
        return "对本区域一条几何标记都不满足，也不是退化碎片"
    if _prose_wrap_continuation(
            fact, _previous_in_order(facts, position), region, inside, prose_width):
        return "上一行是同左边界的普通正文 ⇒ 本行只是它的折行续行"
    return None


def _closure_candidate(fact) -> bool:
    """这一行在**闭包改桶之前**是否是普通正文行（`regular`）。

    独立重算跑在**闭包产物**上，此时被认领的行已经是 `table_adjacency`。这里只读两处
    闭包**不会**改动的证据：结构终态（`body_under_node` + 前置标题）与表的来源范围
    （`None` = 冻结版式没把它判进任何表格范围；闭包标记 = 闭包改过桶）。构建器走的是
    前导还是尾部分支**不影响**这个判据。
    """
    if fact.state.state != "body_under_node" \
            or fact.state.body_attachment != "preceding_heading":
        return False
    return fact.table_scope in (None, CLOSURE_SCOPE)


def _trailing_run(order, order_index, raw_index, kinds, claimed, tail_key,
                  node_id, *, page: int, band: tuple, limit: float):
    """**独立重算**：从 `tail_key`（区域尾行）之后沿源序向下走查**物理邻接**范围。

    返回 `(keys, stop)`：`keys` 是**已经通过物理邻接检查的 staged prefix**，
    `stop` 是让它停下来的那一条判据（`"document_end"` / `"kind"` / `"claimed"` /
    `"node"` / `"page"` / `"furniture"` / `"column"` / `"gap"` / `"cap"`）。

    **任何** `stop` 都保留 `keys` —— 这里没有"命中某种形态即整段丢弃"的语义：
    `sb-6` 的"满栏正文 ⇒ 整段不暂存"正是本轮要消除的失效模式（它会把一条满栏表注
    连同它前面已经成立的候选一起退回正式正文）。全部判据都是几何与结构量
    （同页 / 同节点 / 同栏交叠 / 源序相邻 / 垂直间距 / 上限），**不看文字**。
    """
    keys: list = []
    previous = order[order_index[tail_key]]
    step = order_index[tail_key] + 1
    while step < len(order) and len(keys) < TRAILING_CAP:
        fact = order[step]
        if kinds[fact.key] != SB._KIND_REGULAR:
            return tuple(keys), "kind"           # 非正文行 / 新的表格段
        if fact.key in claimed:
            return tuple(keys), "claimed"        # 已被前导闭包认领（两方向不重复认领）
        if fact.node_id != node_id:
            return tuple(keys), "node"
        if int(fact.state.page_number) != page:
            return tuple(keys), "page"           # 跨页：无 typed continuation ⇒ 停止
        if raw_index[fact.key] - raw_index[previous.key] != 1:
            return tuple(keys), "furniture"      # 中间夹着家具行（页码 / 页眉页脚）
        if _band_overlap(fact, band) <= 0.0:
            return tuple(keys), "column"         # 不在同一正文栏
        if _vertical_gap(previous, fact) > limit:
            return tuple(keys), "gap"            # 超过本页本栏局部行距阈值
        keys.append(fact.key)
        previous = fact
        step += 1
    return tuple(keys), ("cap" if len(keys) >= TRAILING_CAP else "document_end")


def _pre_closure_kinds(facts: tuple) -> dict:
    """闭包**改桶之前**的类别快照（§8.1 的全部判据都以它为准）。

    闭包只把 `regular` 改成 `table_adjacency`；这里用 `_closure_candidate`（结构终态
    + 冻结 `trg-3` 范围）独立还原，**不采信**构建器的自报分类。
    """
    out: dict = {}
    for fact in facts:
        if fact.kind == SB._KIND_TABLE_ADJACENT and _closure_candidate(fact):
            out[fact.key] = SB._KIND_REGULAR
        else:
            out[fact.key] = fact.kind
    return out


def _regions_of(order: list, kinds: dict) -> list:
    """**独立重算**的已证明表格区域：极大连续表格类别段，且至少含一行
    `inside_table`、区域有明确的节点归属（与构建器 `_table_closure_regions` 同义）。"""
    runs: list = []
    current: list = []
    for fact in order:
        if kinds[fact.key] in TABLE_KINDS:
            current.append(fact)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [run for run in runs
            if run[0].node_id is not None
            and any(kinds[f.key] == SB._KIND_TABLE_INSIDE for f in run)]


def _leading_keys(order, order_index, regions, kinds, prose_width) -> set:
    """**独立重算**前导闭包成员：沿每个已证明区域向上走查到的那一段。

    与构建器 `_absorb_table_leading` 同条件、同上限（单区域 3 行、连续上溯、一旦某行
    不合格就停在那里）：这是**方向归属**的独立重算，用来把"已被前导闭包认领的行"从
    尾部走查里排除 —— 两个方向不得重复认领同一行，两个表格区域也不得并成一段。
    """
    regular = [fact for fact in order if kinds[fact.key] == SB._KIND_REGULAR]
    if not regular:
        return set()
    document_left = min(_x0(fact) for fact in regular)
    sizes: dict = {}
    for fact in regular:
        size = round(_line_size(fact.line), 1)
        sizes[size] = sizes.get(size, 0) + 1
    body_size = sorted(sizes.items(), key=lambda item: (-item[1], item[0]))[0][0]
    prose_limit = PROSE_WIDTH_FRAC * prose_width
    out: set = set()
    for region in regions:
        inside = [f for f in region if kinds[f.key] == SB._KIND_TABLE_INSIDE]
        node_id = region[0].node_id
        step = order_index[region[0].key] - 1
        taken = 0
        while step >= 0 and taken < TRAILING_CAP:   # 前导上限与尾部上限同值（3 行）
            fact = order[step]
            previous = order[step - 1] if step >= 1 else None
            if _leading_stop_reason(
                    fact, region, inside, node_id=node_id,
                    document_left=document_left, body_size=body_size,
                    prose_limit=prose_limit, prose_width=prose_width,
                    previous=previous, kind=kinds[fact.key]) is not None:
                break
            out.add(fact.key)
            taken += 1
            step -= 1
    return out


def _trailing_keys(order, order_index, raw_index, regions, kinds, claimed) -> dict:
    """逐区域独立重算尾部物理邻接范围：`{区域尾行 key: (keys, stop)}`。"""
    out: dict = {}
    for region in regions:
        tail = region[-1]
        band = _table_band(region)
        page = int(tail.state.page_number)
        limit = TRAILING_PITCH_FACTOR * _local_pitch(
            order, order_index, kinds, page=page, band=band, tail=tail)
        out[tail.key] = _trailing_run(
            order, order_index, raw_index, kinds, claimed, tail.key, tail.node_id,
            page=page, band=band, limit=limit)
    return out


def _trailing_reason(fact, baseline):
    """**独立重算**：这一**行**作为"表区域尾部物理邻接行"会不会被吸收。

    成立条件**只有物理邻接**（§四.1）：上方有含 `inside_table` 的同节点已证明区域、
    同物理页、与区域水平跨度交叠、源序直接相邻、垂直间距不超过本页本栏局部行距的
    `TRAILING_PITCH_FACTOR` 倍。**满栏与不满栏同权** —— "这一行是满栏正文"不再是
    越界证据，它只说明 TS5 应当把它判回正文段。
    """
    order, order_index, kinds, claimed, regions, runs = baseline
    above = [region for region in regions
             if order_index[region[-1].key] < order_index[fact.key]]
    if not above:
        return "上方没有已证明的表格区域"
    region = above[-1]
    node_id = region[0].node_id
    if any(f.node_id != node_id for f in region):
        return "上方区域没有明确的节点归属"
    if fact.node_id != node_id:
        return "与上方表格区域跨节点"
    tail = region[-1]
    if int(fact.state.page_number) != int(tail.state.page_number):
        return "与上方表格区域不同物理页（跨页无 typed continuation ⇒ 停止）"
    if _band_overlap(fact, _table_band(region)) <= 0.0:
        return "与上方表格区域水平范围不交叠（不在同一正文栏）"
    keys, stop = runs[tail.key]
    if fact.key not in keys:
        return f"不在本区域尾部物理邻接范围内（停止原因 {stop}）"
    return None


def verified_closure(facts: tuple) -> tuple:
    """**独立重算**两个方向闭包的成员资格（几何 + 源序，不看构建器的分类）。

    一行只要**独立重算**认定它是"表格前导"（见 `_leading_reason`）**或**"表区域尾部
    物理邻接行"（见 `_trailing_reason`）之一，就是合法的闭包产物 —— 这正是"表后相邻
    内容进入 TS5 合法裁决入口"的那一步：TS4 只交付有限 provisional 段，不判断它是不是
    表注，也不因为它是满栏正文而排除它。

    `test_tree_span_builder._test_t16`（右边界成因模型）与
    `test_tree_span_conservation._t29_layers`（表范围口径）共用这一个重算，
    以免出现第二套判定。

    返回 `(verified_keys, over_absorbed)`：后者是构建器声称吸收、但两个方向的重算都
    不成立的行——出现即失败（这正是"普通正文被吞"的检出点）。
    """
    index = {fact.key: position for position, fact in enumerate(facts)}
    prose = {fact.key for fact in _prose_shape(facts)}
    prose_width = _prose_width(facts)
    order = [fact for fact in facts if not fact.line.is_furniture]
    order_index = {fact.key: position for position, fact in enumerate(order)}
    kinds = _pre_closure_kinds(facts)
    regions = _regions_of(order, kinds)
    absorbed = _absorbed(facts)
    claimed = _leading_keys(order, order_index, regions, kinds, prose_width)
    runs = _trailing_keys(order, order_index, index, regions, kinds, claimed)
    verified: set = set(claimed)
    for keys, _stop in runs.values():
        verified.update(keys)
    baseline = (order, order_index, kinds, claimed, regions, runs)
    over: list = []
    for fact in absorbed:
        if fact.key in verified:
            continue
        leading = None if fact.key in claimed else _leading_reason(
            facts, index, fact, prose, prose_width)
        trailing = _trailing_reason(fact, baseline)
        over.append((fact.key, f"前导不成立：{leading}；尾部也不成立：{trailing}"))
    return verified, over


# ---------------------------------------------------------------------------
# 3. 合成反例（1）～（9）、（13）、（14）
# ---------------------------------------------------------------------------

def _case_caption_unit_body():
    """（1）表题 + 单位行 + 表体：表题与单位行都必须是 provisional，且都不产生 span。"""
    doc = _Doc()
    doc.prose(y0=200.0)
    doc.caption(y0=124.0)
    doc.unit(y0=112.0)
    doc.table(top_y=100.0)
    facts = doc.facts()
    kinds = _kinds(facts)
    absorbed = _absorbed(facts)
    check(len(absorbed) == 1, f"T41 表题必须被闭包吸收，得到 {len(absorbed)} 行")
    if absorbed:
        fact = absorbed[0]
        check(_tight(fact.line.text) == "表4-1主营业务构成表",
              f"T41 被吸收的行必须就是表题行，得到 {_tight(fact.line.text)!r}")
        check(fact.table_reason == CLOSURE_REASON,
              f"T41 闭包行的 table_reason 必须为 {CLOSURE_REASON!r}，"
              f"得到 {fact.table_reason!r}")
        check(fact.key == (1, 1), f"T41 表题行位置必须是 (1, 1)，得到 {fact.key}")
    unit = _real_adjacent(facts)
    check(len(unit) == 1 and unit[0].key == (1, 2),
          f"T41 单位行必须保持版式判定的 adjacent_to_table，得到 "
          f"{[(f.key, f.table_scope) for f in unit]}")
    cells = [f for f in facts if f.kind == SB._KIND_TABLE_INSIDE]
    check(len(cells) == 4, f"T41 表体两行四格必须全是 inside_table，得到 {len(cells)}")
    check(set(kinds.values()) == {SB._KIND_REGULAR, SB._KIND_TABLE_INSIDE,
                                  SB._KIND_TABLE_ADJACENT},
          f"T41 合成文档不应出现其它行类别，得到 {sorted(set(kinds.values()))}")
    regular = [f for f in facts if f.kind == SB._KIND_REGULAR]
    check([f.key for f in regular] == [(1, 0)],
          f"T41 只有正文行保持 regular，得到 {[f.key for f in regular]}")
    # 处置记录：闭包行与单位行各自成段，都不带 span / confidence。
    runs = SB._split_runs(facts)
    closures = [r for r in runs if r[0].kind == SB._KIND_TABLE_ADJACENT]
    disps = [_one_disposition(r) for r in closures]
    kinds_of = sorted({d.range_kind for d in disps})
    check(kinds_of == ["table_adjacency"], f"T41 段处置必须都是 table_adjacency，"
                                          f"得到 {kinds_of}")
    for disp in disps:
        check(disp.span_id is None and disp.confidence is None,
              f"T41 {disp.range_kind} 处置不得携带 span_id / confidence")
    scopes = sorted(d.table_scope for d in disps)
    check(scopes == sorted([CLOSURE_SCOPE, "adjacent_to_table"]),
          f"T41 表题段与单位段的 table_scope 必须分别是闭包签名与版式邻近值，"
          f"得到 {scopes}")


def _case_caption_only():
    """（2）表题直接压在表体上（没有单位行）：仍必须被吸收。"""
    doc = _Doc()
    doc.prose(y0=200.0)
    # 表题与表格顶行相距 12pt > `TABLE_REGION_PROXIMITY_PT`(8pt)：版式**不**把它
    # 判成 `adjacent_to_table`，因此它确实是"闭包外、待吸收"的普通行。
    doc.caption(y0=122.0)
    doc.table(top_y=100.0)
    facts = doc.facts()
    absorbed = _absorbed(facts)
    check(len(absorbed) == 1 and absorbed[0].key == (1, 1),
          f"T41 无单位行时表题仍必须被吸收，得到 {[f.key for f in absorbed]}")
    check(all(f.kind != SB._KIND_TABLE_INSIDE for f in absorbed),
          "T41 表题不得被改写成 table_inside")


def _case_two_line_caption():
    """（3）双行表题：两行都在上限内被吸收，顺序与位置保持。"""
    doc = _Doc()
    doc.prose(y0=220.0)
    doc.caption(y0=152.0, text="表4-2 主营业务分产品情况", bold=True)
    doc.caption(y0=138.0, text="（续）", x0=190.0, x1=450.0, bold=False)
    doc.unit(y0=126.0)
    doc.table(top_y=114.0)
    facts = doc.facts()
    absorbed = _absorbed(facts)
    keys = sorted(f.key for f in absorbed)
    check(keys == [(1, 1), (1, 2)],
          f"T41 双行表题必须两行都被吸收，得到 {keys}")
    check(all(f.table_reason == CLOSURE_REASON for f in absorbed),
          "T41 双行表题的两行都必须携带闭包原因码")


def _case_prose_not_swallowed():
    """（4）正文 + 表题 + 单位 + 表体：正文必须保持 regular，闭包不得无界上溯。"""
    doc = _Doc()
    doc.prose(y0=240.0, text="第一段正文")
    doc.prose(y0=214.0, text="第二段正文", x0=120.0)
    doc.caption(y0=124.0)
    doc.unit(y0=112.0)
    doc.table(top_y=100.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    regular = [f.key for f in facts if f.kind == SB._KIND_REGULAR]
    check(absorbed == {(1, 2)},
          f"T41 上溯到表题就必须停止，得到被吸收 {sorted(absorbed)}")
    check(regular == [(1, 0), (1, 1)],
          f"T41 两行正文必须全部保持 regular，得到 {regular}")
    check(all(f.table_scope is None for f in facts if f.kind == SB._KIND_REGULAR),
          "T41 regular 行不得携带任何表格范围字段")


def _case_stop_conditions():
    """（4′）停止条件逐条独立成立：贴左边界 / 无任何标记 / 字号偏大 / 续行。"""
    doc = _Doc()
    doc.prose(y0=300.0, text="顶部正文")
    doc.prose(y0=276.0, text="贴左边界正文")          # bodystart 停止
    doc.prose(y0=252.0, text="宽栏正文行", x0=120.0)  # 无标记停止
    # 与下表题同左边界会先触发"续行"停止，掩盖"字号"停止；这里错开 20pt。
    doc.line(x0=200.0, x1=440.0, y0=236.0, y1=246.0,  # 字号停止
             text="大字号前导", size=14.0)
    doc.caption(y0=210.0, text="表4-3 前导闭包")
    doc.unit(y0=198.0)
    doc.table(top_y=186.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, 4)},
          f"T41 只有紧邻表体的那一行表题可被吸收，得到 {sorted(absorbed)}")
    regular = [f.key for f in facts if f.kind == SB._KIND_REGULAR]
    check(regular == [(1, 0), (1, 1), (1, 2), (1, 3)],
          f"T41 四种停止条件下四行都必须保持 regular，得到 {regular}")

    # 续行停止：两行正文同左边界（错开正文栏左边界，避免"贴左边界"抢先命中）。
    # 切断它的**不是**"与上一行同左边界"这条机械规则，而是**宽度**：两行都宽达声明
    # 栏宽上界的 0.7，宽度本身就是"这是正文"的负证明。
    doc2 = _Doc()
    doc2.prose(y0=252.0, text="段落首行", x0=150.0, x1=500.0)
    doc2.prose(y0=232.0, text="段落续行", x0=150.0, x1=500.0)
    doc2.caption(y0=206.0, text="续行后的表题")
    doc2.unit(y0=194.0)
    doc2.table(top_y=182.0)
    facts2 = doc2.facts()
    absorbed2 = {f.key for f in _absorbed(facts2)}
    check(absorbed2 == {(1, 2)},
          f"T41 宽栏续行必须切断闭包，得到 {sorted(absorbed2)}")
    regular2 = [f.key for f in facts2 if f.kind == SB._KIND_REGULAR]
    check(regular2 == [(1, 0), (1, 1)],
          f"T41 续行与被续行都必须是普通正文，得到 {regular2}")


def _narrow_table(doc, *, top_y: float = 100.0, node_id: str = "n1") -> None:
    """两列**窄**表格：列宽 60pt、列起点相距 70pt（判据 A 成立），
    区域整体宽度只有 130pt —— 远小于正文栏宽，也没有任何一格延伸到页面右侧。"""
    for step in range(2):
        y = top_y - step * 14.0
        doc.line(x0=60.0, x1=120.0, y0=y, y1=y + 10.0, text=f"标签{step}",
                 size=CELL_SIZE, node_id=node_id)
        doc.line(x0=130.0, x1=190.0, y0=y, y1=y + 10.0, text=f"取值{step}",
                 size=CELL_SIZE, node_id=node_id)


def _case_same_left_leading():
    """（1）表题行与单位行**共用同一条左边界**：两行都必须 provisional。

    真实年报里"表题 / 单位行"几乎总左对齐在同一条缩进边界上。若闭包因为
    "与上一行同左边界"就机械停止，这类前导会整段留在正式正文里 —— 这正是本轮
    残余污染的成因之一。**同左边界不是停止条件**；停止只由结构边界、满栏正文形态
    与字号决定。
    """
    doc = _Doc()
    doc.prose(y0=320.0, text="表前正文段落")
    caption_index = doc.caption(y0=134.0, text="表4-15 主要财务指标",
                                x0=LEADING_LEFT, x1=LEADING_RIGHT, bold=False)
    unit_index = doc.line(x0=LEADING_LEFT, x1=LEADING_LEFT + 48.0, y0=120.0,
                          y1=130.0, text="单位：千元", size=CELL_SIZE)
    doc.table(top_y=100.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, caption_index), (1, unit_index)},
          f"T41 同左边界的两行前导必须都被吸收，得到 {sorted(absorbed)}")
    regular = [f.key for f in facts if f.kind == SB._KIND_REGULAR]
    check(regular == [(1, 0)],
          f"T41 表前正文必须保持 regular，得到 {regular}")
    for fact in facts:
        if fact.key in absorbed:
            check(fact.table_scope == CLOSURE_SCOPE
                  and fact.table_reason == CLOSURE_REASON,
                  f"T41 同左边界前导 {fact.key} 必须落在 provisional 表格范围里")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 同左边界前导必须通过独立重算，得到 verified={sorted(verified)} "
          f"over={over}")


def _case_three_line_leading():
    """（2）三行连续前导（表题 / 单位 / 表注）：三行都 provisional；上限仍然生效。"""
    doc = _Doc()
    doc.prose(y0=360.0, text="表前正文段落第一行")
    doc.prose(y0=340.0, text="表前正文段落第二行")
    lines = [
        doc.caption(y0=184.0, text="表4-16 主营业务分产品情况",
                    x0=LEADING_LEFT, x1=LEADING_RIGHT, bold=False),
        doc.line(x0=LEADING_LEFT, x1=LEADING_LEFT + 70.0, y0=170.0, y1=180.0,
                 text="单位：万元", size=CELL_SIZE),
        doc.line(x0=LEADING_LEFT, x1=LEADING_LEFT + 66.0, y0=156.0, y1=166.0,
                 text="注：数据来源于年报", size=CELL_SIZE),
    ]
    doc.table(top_y=100.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    expected = {(1, index) for index in lines}
    check(absorbed == expected,
          f"T41 三行连续前导必须全部被吸收，得到 {sorted(absorbed)}")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 三行前导必须通过独立重算，得到 verified={sorted(verified)} "
          f"over={over}")

    # 上限：四行连续前导里只有最靠表格的三行可以 provisional，最上面一行必须留在
    # 正式正文 —— 不得用"扩大区域"的方式换取零污染。
    doc2 = _Doc()
    doc2.prose(y0=360.0, text="表前正文段落")
    tops = [
        doc2.caption(y0=210.0, text="表4-17 最上面一行前导",
                     x0=LEADING_LEFT, x1=LEADING_RIGHT, bold=False),
        doc2.caption(y0=196.0, text="表4-18 第二行前导",
                     x0=LEADING_LEFT, x1=LEADING_RIGHT, bold=False),
        doc2.line(x0=LEADING_LEFT, x1=LEADING_LEFT + 70.0, y0=182.0, y1=192.0,
                  text="单位：万元", size=CELL_SIZE),
        doc2.line(x0=LEADING_LEFT, x1=LEADING_LEFT + 66.0, y0=168.0, y1=178.0,
                  text="注：数据来源于年报", size=CELL_SIZE),
    ]
    doc2.table(top_y=100.0)
    facts2 = doc2.facts()
    absorbed2 = {f.key for f in _absorbed(facts2)}
    check(absorbed2 == {(1, index) for index in tops[1:]},
          f"T41 前导上限必须仍然是 3 行，得到 {sorted(absorbed2)}")
    regular2 = [f.key for f in facts2 if f.kind == SB._KIND_REGULAR]
    check((1, tops[0]) in regular2,
          f"T41 超出上限的那一行必须保持正式正文，得到 {regular2}")


def _case_prose_continuation_not_swallowed():
    """（3）两行普通正文左边界相同：不得因为"同左边界"或"看起来像前导"被吞。"""
    doc = _Doc()
    doc.prose(y0=300.0, text="段落首行", x0=150.0, x1=500.0)
    first = doc.prose(y0=276.0, text="段落续行", x0=150.0, x1=500.0)
    doc.unit(y0=120.0)
    doc.table(top_y=100.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, 2)},
          f"T41 满栏正文续行必须切断闭包，得到 {sorted(absorbed)}")
    regular = [f.key for f in facts if f.kind == SB._KIND_REGULAR]
    check(regular == [(1, 0), (1, first)],
          f"T41 两行正文都必须保持 regular，得到 {regular}")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 正文续行不得被吸收，得到 verified={sorted(verified)} over={over}")


def _case_prose_wrap_above_region_not_swallowed():
    """（3′）**窄折行**紧跟在一行正文之后、又紧邻表格区域上方：不得被吸收。

    真实文档里最容易被误吞的形态：正文段落的**折行残段**很窄，而它上方的窄表格区域
    水平中心又恰好与它对齐（窄区域中心极易撞上任何一行）—— 于是"居中"这**单一版式
    特征**就把它当成表格前导吞掉。它实际是**上一行正文的续行**：上一行与它同节点、
    同左边界，并且上一行自身对该区域**没有任何锚定证据**（既不是退化碎片，也不满足
    任何一条几何标记 —— 也就是说，上一行自己绝不可能被当成表格前导）。
    """
    doc = _Doc()
    doc.prose(y0=320.0, text="表前正文段落")
    doc.prose(y0=300.0, text="折行段落的前一行", x0=100.0, x1=520.0)
    wrap = doc.line(x0=100.0, x1=150.0, y0=276.0, y1=286.0, text="折行残段")
    _narrow_table(doc, top_y=100.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == set(),
          f"T41 紧跟正文行、同左边界的窄折行不得被吸收，得到 {sorted(absorbed)}")
    regular = [f.key for f in facts if f.kind == SB._KIND_REGULAR]
    check((1, wrap) in regular,
          f"T41 窄折行必须保持普通正文，得到 {regular}")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 窄折行不得被吸收，得到 verified={sorted(verified)} over={over}")

    # 对照：同样"与上一行同左边界"的两行前导，但**两行自身**都满足区域标记（加粗）——
    # 禁止的只是"上一行是没有任何区域证据的普通正文"，不是"同左边界"。
    doc2 = _Doc()
    doc2.prose(y0=320.0, text="表前正文段落")
    upper = doc2.line(x0=100.0, x1=150.0, y0=300.0, y1=310.0, text="表题上段",
                      bold=True)
    lower = doc2.line(x0=100.0, x1=150.0, y0=276.0, y1=286.0, text="表题下段",
                      bold=True)
    _narrow_table(doc2, top_y=100.0)
    facts2 = doc2.facts()
    absorbed2 = {f.key for f in _absorbed(facts2)}
    check(absorbed2 == {(1, upper), (1, lower)},
          f"T41 两行自身都有区域标记的同左边界前导必须都被吸收，得到 "
          f"{sorted(absorbed2)}")
    verified2, over2 = verified_closure(facts2)
    check(absorbed2 == verified2 and not over2,
          f"T41 同左边界前导必须通过独立重算，得到 verified={sorted(verified2)} "
          f"over={over2}")


def _case_wide_prose_with_marks():
    """（4）居中 / 加粗的**满栏正文**不得只凭单一版式特征被吞。

    这两行的水平中心恰好等于表格区域中心、或带粗体 —— 二者都**只是版式特征**，
    不足以证明"这是表格前导"；宽度本身就是"这是正文"的负证明。
    """
    doc = _Doc()
    doc.prose(y0=300.0, text="正文首行")
    centered = doc.line(x0=100.0, x1=540.0, y0=276.0, y1=286.0,
                        text="水平中心恰与表格中心重合的满栏正文")
    doc.unit(y0=120.0)
    doc.table(top_y=100.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, 2)},
          f"T41 居中的满栏正文不得被吸收，得到 {sorted(absorbed)}")
    regular = [f.key for f in facts if f.kind == SB._KIND_REGULAR]
    check((1, centered) in regular,
          f"T41 居中的满栏正文必须保持 regular，得到 {regular}")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 居中满栏正文不得被吸收，得到 verified={sorted(verified)} over={over}")

    doc2 = _Doc()
    doc2.prose(y0=300.0, text="正文首行")
    bold_line = doc2.line(x0=100.0, x1=540.0, y0=276.0, y1=286.0,
                          text="加粗的满栏正文行", bold=True)
    doc2.unit(y0=120.0)
    doc2.table(top_y=100.0)
    facts2 = doc2.facts()
    absorbed2 = {f.key for f in _absorbed(facts2)}
    check(absorbed2 == {(1, 2)},
          f"T41 加粗的满栏正文不得被吸收，得到 {sorted(absorbed2)}")
    regular2 = [f.key for f in facts2 if f.kind == SB._KIND_REGULAR]
    check((1, bold_line) in regular2,
          f"T41 加粗的满栏正文必须保持 regular，得到 {regular2}")


def _case_degenerate_fragment_transit():
    """（1′）（5′）退化碎片（宽度装不下 3 个字宽）随连续前导区域一并 provisional；
    但它既不得击穿"贴左边界即正文"的负证明，也不得跨过字号 / 节点边界生效。"""
    doc = _Doc()
    doc.prose(y0=340.0, text="表前正文段落")
    fragment = doc.line(x0=FRAGMENT_LEFT, x1=FRAGMENT_LEFT + FRAGMENT_WIDTH,
                        y0=140.0, y1=150.0, text="- 1 -", size=CELL_SIZE)
    unit = doc.unit(y0=126.0)
    doc.table(top_y=106.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, fragment), (1, unit)},
          f"T41 退化碎片必须随连续区域一并 provisional，得到 {sorted(absorbed)}")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 退化碎片必须通过独立重算，得到 verified={sorted(verified)} "
          f"over={over}")

    # 反例 a：贴在正文栏左边界上的碎片 —— "贴左边界"优先，碎片规则不得击穿它。
    # 结果是闭包停在这个碎片上：紧邻表体的那行仍被吸收，碎片及其上方保持正式正文。
    doc_a = _Doc()
    doc_a.prose(y0=340.0, text="表前正文段落")
    fragment_a = doc_a.line(x0=PROSE_LEFT, x1=PROSE_LEFT + FRAGMENT_WIDTH,
                            y0=140.0, y1=150.0, text="- 2 -", size=CELL_SIZE)
    unit_a = doc_a.unit(y0=126.0)
    doc_a.table(top_y=106.0)
    facts_a = doc_a.facts()
    check({f.key for f in _absorbed(facts_a)} == {(1, unit_a)},
          f"T41 贴左边界碎片必须切断闭包，得到 "
          f"{[f.key for f in _absorbed(facts_a)]}")
    regular_a = [f.key for f in facts_a if f.kind == SB._KIND_REGULAR]
    check(regular_a == [(1, 0), (1, fragment_a)],
          f"T41 贴左边界碎片必须保持 regular（负证明不被击穿），得到 {regular_a}")

    # 反例 b：字号偏大的碎片 —— 字号守卫必须先于碎片规则生效。
    doc_b = _Doc()
    doc_b.prose(y0=340.0, text="表前正文段落")
    fragment_b = doc_b.line(x0=FRAGMENT_LEFT, x1=FRAGMENT_LEFT + FRAGMENT_WIDTH,
                            y0=140.0, y1=150.0, text="- 3 -", size=20.0)
    unit_b = doc_b.unit(y0=126.0)
    doc_b.table(top_y=106.0)
    facts_b = doc_b.facts()
    check({f.key for f in _absorbed(facts_b)} == {(1, unit_b)},
          f"T41 大字号碎片必须切断闭包，得到 "
          f"{[f.key for f in _absorbed(facts_b)]}")
    regular_b = [f.key for f in facts_b if f.kind == SB._KIND_REGULAR]
    check((1, fragment_b) in regular_b,
          f"T41 大字号碎片必须保持 regular，得到 {regular_b}")

    # 反例 c：跨节点的碎片 —— 节点边界必须先于碎片规则生效。
    doc_c = _Doc()
    doc_c.prose(y0=340.0, text="表前正文段落")
    fragment_c = doc_c.line(x0=FRAGMENT_LEFT, x1=FRAGMENT_LEFT + FRAGMENT_WIDTH,
                            y0=140.0, y1=150.0, text="- 4 -", size=CELL_SIZE,
                            node_id="n2")
    unit_c = doc_c.unit(y0=126.0)
    doc_c.table(top_y=106.0)
    facts_c = doc_c.facts()
    check({f.key for f in _absorbed(facts_c)} == {(1, unit_c)},
          f"T41 跨节点碎片必须切断闭包，得到 "
          f"{[f.key for f in _absorbed(facts_c)]}")
    regular_c = [f.key for f in facts_c if f.kind == SB._KIND_REGULAR]
    check((1, fragment_c) in regular_c,
          f"T41 跨节点碎片必须保持 regular，得到 {regular_c}")


def _case_narrow_region_right_side():
    """（1″）窄表格区域：整行落在区域**右侧**的窄排行仍必须 provisional；
    落在区域右侧的宽行不得只凭"在右边"被吞。"""
    doc = _Doc()
    doc.prose(y0=300.0, text="表前正文段落")
    unit = doc.unit(y0=140.0)
    _narrow_table(doc, top_y=100.0)
    facts = doc.facts()
    inside = [f for f in facts if f.kind == SB._KIND_TABLE_INSIDE]
    check(len(inside) == 4,
          f"T41 窄表格必须由判据 A 判成 inside_table，得到 {len(inside)}")
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, unit)},
          f"T41 窄区域右侧的窄排行必须被吸收，得到 {sorted(absorbed)}")
    verified, over = verified_closure(facts)
    check(absorbed == verified and not over,
          f"T41 窄区域右侧窄排行必须通过独立重算，得到 verified={sorted(verified)} "
          f"over={over}")

    # 反例：同样落在窄区域右侧、但**不窄**的普通行 —— 没有任何标记，必须保持正文。
    doc2 = _Doc()
    doc2.prose(y0=300.0, text="表前正文段落")
    wide = doc2.line(x0=200.0, x1=400.0, y0=140.0, y1=150.0,
                     text="窄表格右侧的普通正文行", size=CELL_SIZE)
    _narrow_table(doc2, top_y=100.0)
    facts2 = doc2.facts()
    check(not _absorbed(facts2),
          f"T41 窄区域右侧的普通宽行不得被吸收，得到 "
          f"{[f.key for f in _absorbed(facts2)]}")
    regular2 = [f.key for f in facts2 if f.kind == SB._KIND_REGULAR]
    check((1, wide) in regular2,
          f"T41 窄区域右侧的普通宽行必须保持 regular，得到 {regular2}")


def _case_leading_disposition_and_conservation():
    """（9）（10）（11）新的连续前导区域：全文 / 定位 / 守恒 / 不产生 span。"""
    doc = _Doc()
    doc.prose(y0=320.0, text="表前正文段落")
    caption = doc.caption(y0=134.0, text="表4-19 主要财务指标",
                          x0=LEADING_LEFT, x1=LEADING_RIGHT, bold=False)
    unit = doc.line(x0=LEADING_LEFT, x1=LEADING_LEFT + 48.0, y0=120.0,
                    y1=130.0, text="单位：千元", size=CELL_SIZE)
    doc.table(top_y=100.0)
    view = doc.view()
    facts = SB._collect_line_facts(view)
    runs = SB._split_runs(facts)
    check(sum(len(run) for run in runs) == len(facts),
          "T41 切分后行数必须守恒（闭包只改桶，不删行）")
    check(sum(SB.tight_char_count_of(f.line.text) for run in runs for f in run)
          == sum(SB.tight_char_count_of(f.line.text) for f in facts),
          "T41 切分后 tight 字符数必须守恒")
    all_lines = {line.line_index: line for page in view.page_layout.pages
                 for line in page.lines}
    for fact in facts:
        check(fact.line is all_lines[fact.key[1]],
              f"T41 被吸收的行必须保留**原来那一行对象**（原文 / 页码 / 几何不变）："
              f"{fact.key}")
    closure_runs = [r for r in runs if r[0].kind == SB._KIND_TABLE_ADJACENT
                    and r[0].table_scope == CLOSURE_SCOPE]
    check(len(closure_runs) == 1,
          f"T41 连续前导必须合成**一段**范围，得到 {len(closure_runs)}")
    if not closure_runs:
        return
    run = closure_runs[0]
    disp = _one_disposition(run)
    check(disp.range_kind == "table_adjacency" and disp.span_id is None
          and disp.confidence is None,
          f"T41 前导范围不得产生 span / confidence，得到 "
          f"({disp.range_kind}, {disp.span_id}, {disp.confidence})")
    check((disp.start_page, disp.start_line) == (1, caption)
          and (disp.end_page, disp.end_line) == (1, unit),
          f"T41 处置记录必须给出精确起止位置，得到 "
          f"({disp.start_page},{disp.start_line})-({disp.end_page},{disp.end_line})")
    check(disp.line_count == 2
          and disp.tight_char_count
          == SB.tight_char_count_of("表4-19 主要财务指标")
          + SB.tight_char_count_of("单位：千元"),
          f"T41 处置记录必须给出真实行数与 tight 字符数，得到 "
          f"{disp.line_count} / {disp.tight_char_count}")


def _case_heading_boundary():
    """（5）标题紧接表格：闭包不得跨过标题。"""
    doc = _Doc()
    doc.prose(y0=240.0, text="正文段落")            # 正文栏左边界基准
    doc.caption(y0=200.0, text="表4-4 上游遗留的前导行")
    doc.line(x0=56.64, x1=300.0, y0=160.0, y1=170.0, text="（三）主营业务情况",
             size=13.0, bold=True, state="heading_node", attachment=None)
    doc.caption(y0=130.0, text="表4-5 标题下的表")
    doc.unit(y0=118.0)
    doc.table(top_y=106.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, 3)},
          f"T41 表题必须被吸收、标题行不得被吸收也不得被跨越，得到 {sorted(absorbed)}")
    heading = [f for f in facts if f.kind == SB._KIND_HEADING]
    check(len(heading) == 1 and heading[0].key == (1, 2),
          "T41 标题行的类别必须原样保留")
    upstream = [f for f in facts if f.key == (1, 1)]
    check(upstream and upstream[0].kind == SB._KIND_REGULAR,
          "T41 标题之上的行必须保持 regular（闭包不得越过标题上溯）")


def _case_node_boundary():
    """（6）跨节点相邻：闭包不得跨节点边界。"""
    doc = _Doc()
    doc.prose(y0=200.0, text="上一节点的正文", node_id="n1")
    doc.caption(y0=170.0, text="表4-6 上一节点的表题", x0=170.0, x1=410.0,
                node_id="n1")
    doc.caption(y0=130.0, text="表4-7 本节点表题", node_id="n2")
    doc.unit(y0=118.0, node_id="n2")
    doc.table(top_y=106.0, node_id="n2")
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, 2)},
          f"T41 闭包必须停在节点边界，得到 {sorted(absorbed)}")


def _case_blank_and_formal_unassigned():
    """（7）空白断点 / formal unassigned 必须切断闭包。"""
    doc = _Doc()
    doc.prose(y0=220.0)
    doc.caption(y0=200.0, text="表4-8 空白断点之上的表题")
    doc.line(x0=56.64, x1=60.0, y0=170.0, y1=180.0, text="   ", size=BODY_SIZE)
    doc.caption(y0=140.0, text="表4-9 空白之下的表题")
    doc.unit(y0=128.0)
    doc.table(top_y=116.0)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, 3)},
          f"T41 空白行必须切断闭包，得到 {sorted(absorbed)}")
    empty = [f for f in facts if f.kind == SB._KIND_EMPTY]
    check(len(empty) == 1 and empty[0].key == (1, 2),
          "T41 空白行必须保持 empty 类别")

    doc2 = _Doc()
    doc2.prose(y0=220.0)
    doc2.caption(y0=200.0, text="表4-10 unassigned 之上的表题")
    doc2.line(x0=56.64, x1=500.0, y0=170.0, y1=180.0, text="无归属正文",
              size=BODY_SIZE, state="formal_unassigned", attachment=None, node_id=None)
    doc2.caption(y0=140.0, text="表4-11 unassigned 之下的表题")
    doc2.unit(y0=128.0)
    doc2.table(top_y=116.0)
    facts2 = doc2.facts()
    absorbed2 = {f.key for f in _absorbed(facts2)}
    check(absorbed2 == {(1, 3)},
          f"T41 formal_unassigned 必须切断闭包，得到 {sorted(absorbed2)}")


def _case_adjacent_without_inside():
    """（8）只有 adjacent_to_table 而没有已证明 inside_table：不得声称绑定表格对象。"""
    doc = _Doc()
    doc.prose(y0=200.0)
    # 多字段行的两格是 `before_first_heading` → 属于 unassigned，**不是** T_inside。
    doc.table(top_y=100.0, attachment="before_first_heading")
    doc.unit(y0=112.0)          # 版式判为 adjacent_to_table
    caption_index = doc.caption(y0=124.0, text="表4-12 无 T_inside 的表题")
    facts = doc.facts()
    caption = next(f for f in facts if f.key == (1, caption_index))
    inside = [f for f in facts if f.kind == SB._KIND_TABLE_INSIDE]
    check(not inside,
          f"T41 该合成文档不得存在 inside_table 事实，得到 {len(inside)}")
    adjacent = [f for f in facts if f.kind == SB._KIND_TABLE_ADJACENT]
    check(bool(adjacent), "T41 该合成文档必须存在 adjacent_to_table 事实")
    check(all(f.table_scope == "adjacent_to_table" for f in adjacent),
          f"T41 没有 inside_table 时邻近行必须保留真实 table_scope，得到 "
          f"{[(f.key, f.table_scope) for f in adjacent]}")
    check(all(f.table_scope != CLOSURE_SCOPE for f in facts),
          "T41 没有已证明的表格区域时不得产出任何闭包行")
    check(caption.kind == SB._KIND_REGULAR,
          f"T41 邻近行之上的表题必须保持 regular（诚实 provisional），"
          f"得到 {caption.kind!r}")


def _case_text_after_table():
    """（9）表后**跨页**的满栏分析正文必须保持正文（无 typed continuation ⇒ 停止）。

    这一条说的是**边界**，不是**形态**：表后同页同栏的候选一律进入 provisional
    （哪怕它是满栏正文，见 `_case_trailing_physical_adjacency`），但正文跨到下一页时，
    没有已批准的 typed continuation 关系就必须停在页边界，不得猜测续接。所以本案例把
    表尾放在第 1 页、分析正文放在第 2 页：表前表题仍被吸收，跨页正文保持 regular。
    """
    doc = _Doc()
    doc.prose(y0=200.0)
    caption_index = doc.caption(y0=124.0, text="表4-13 表前表题")
    doc.unit(y0=112.0)
    doc.table(top_y=100.0)
    after_index = doc.prose(y0=60.0, text="表后分析文字第一行", page=2)
    doc.prose(y0=40.0, text="表后分析文字第二行", page=2)
    facts = doc.facts()
    absorbed = {f.key for f in _absorbed(facts)}
    check(absorbed == {(1, caption_index)},
          f"T41 跨页表后正文不得进入 provisional，只有表前表题被吸收，"
          f"得到 {sorted(absorbed)}")
    after = [f for f in facts if f.key[1] >= after_index]
    check(len(after) == 2 and all(f.kind == SB._KIND_REGULAR for f in after),
          f"T41 跨页表后文字必须保持 regular，得到 {[(f.key, f.kind) for f in after]}")
    verified, over = verified_closure(facts)
    check(not over, f"T41 跨页表后正文不得被判成越界吸收，得到 {over}")
    check(verified == {(1, caption_index)},
          f"T41 独立重算必须只认下表前表题，得到 {sorted(verified)}")


def _case_conservation_and_location():
    """（13）（14）守恒（改桶不删文本）与"TS5 仍能读到全文与精确位置"。"""
    doc = _Doc()
    doc.prose(y0=200.0)
    doc.caption(y0=124.0, text="表4-14 主营业务分产品构成表")
    doc.unit(y0=112.0)
    doc.table(top_y=100.0)
    view = doc.view()
    facts = SB._collect_line_facts(view)
    runs = SB._split_runs(facts)
    check(sum(len(run) for run in runs) == len(facts),
          "T41 切分后行数必须守恒（闭包只改桶，不删行）")
    check(sum(SB.tight_char_count_of(f.line.text) for run in runs for f in run)
          == sum(SB.tight_char_count_of(f.line.text) for f in facts),
          "T41 切分后 tight 字符数必须守恒")
    all_lines = {line.line_index: line for page in view.page_layout.pages
                 for line in page.lines}
    for fact in facts:
        original = all_lines[fact.key[1]]
        check(fact.line is original,
              f"T41 被吸收的行必须保留**原来那一行对象**（原文 / 页码 / 几何不变）："
              f"{fact.key}")
    # 位置与全文：由处置记录即可回到原文。
    closure_runs = [r for r in runs if r[0].kind == SB._KIND_TABLE_ADJACENT
                    and r[0].table_scope == CLOSURE_SCOPE]
    check(len(closure_runs) == 1, "T41 必须存在恰好一段闭包范围")
    if closure_runs:
        disp = _one_disposition(closure_runs[0])
        check((disp.start_page, disp.start_line) == (1, 1)
              and (disp.end_page, disp.end_line) == (1, 1),
              f"T41 处置记录必须给出精确起止位置，得到 "
              f"({disp.start_page},{disp.start_line})-({disp.end_page},{disp.end_line})")
        check(disp.line_count == 1 and disp.tight_char_count == len("表4-14主营业务分产品构成表"),
              f"T41 处置记录必须给出真实行数与 tight 字符数，得到 "
              f"{disp.line_count} / {disp.tight_char_count}")
        texts = "".join(_tight(all_lines[line_index].text)
                        for line_index in range(disp.start_line, disp.end_line + 1))
        check(texts == "表4-14主营业务分产品构成表",
              f"T41 处置记录的位置必须能读回表题全文，得到 {texts!r}")


# ---------------------------------------------------------------------------
# 3b. 尾部 provisional 闭包：表后相邻内容必须进入 TS5 的合法裁决入口
# ---------------------------------------------------------------------------
#
# 这一族反例对应"表后表注仍是正式 `regular/body OutlineSpan`（其中一部分还进了简介）"
# 这个 P1 缺陷：TS4 交付的必须是**有限、被结构边界终止的 provisional 段**，由 TS5 用
# 不可变 `TableRangeDecision` 裁决 `absorbed_as_note` / `kept_as_paragraph` /
# `absorbed_into_body` / `unresolved_geometry`。TS4 **不判断"这是表注"**：本族反例里
# 表后行与正文行在字号 / 左边界 / 宽度上都可以**完全一致**，判定只靠**物理邻接**
# （同一已证明区域 + 同节点 + 同物理页 + 同栏交叠 + 源序直接相邻 + 局部垂直间距）与
# 有界性。
#
# **内容形态不是判据**：`sb-6` 曾用"满栏正文负证明成立 ⇒ 整段不暂存"作为停止条件，
# 于是一条满栏表注（"说明：…"）会让它**前面**已经成立的候选一起退回正式正文。本族反例
# 显式要求"满栏与不满栏同权"：满栏的第一条候选照样进入 provisional，任何停止都只保留
# 已经通过物理邻接检查的 staged prefix。普通正文因此被暂时暂存**不是内容丢失** ——
# 原文与定位完整保留、范围有界、TS5 能确定性恢复为正文段。

#: 骨架里**表题**行的 line_index（`_with_table()` 的行序固定：正文在前）。
SKELETON_CAPTION = 5


def _with_table() -> _Doc:
    """标准骨架：正文行（正文栏基准） → 表题 → 单位行 → 两行表体。

    `doc.facts()` 的行序固定为：0–4 正文、5 表题、6 单位行、7–10 表体（2 行 × 2 格），
    表体末格是区域尾行；`_absorbed` 里来自本骨架的前导成员恰为 `{(1, SKELETON_CAPTION)}`。

    正文行不是装饰：`body_size` 是逐行正文样本的**众数**（构建器 `_table_closure_baseline`），
    骨架必须给出真实的正文基准字号，否则表后候选自己较小的字号会把众数拉走，判据就变成
    自指的。因此正文行数为 5，始终多于尾部候选的最大行数（`TRAILING_CAP`）。
    """
    doc = _Doc()
    for step in range(5):
        doc.prose(y0=260.0 - step * 15.0)
    doc.caption(y0=124.0)
    doc.unit(y0=112.0)
    doc.table(top_y=100.0)
    return doc


def _lines_of(view):
    return {line.line_index: line for page in view.page_layout.pages
            for line in page.lines}


def _staged_runs(facts: tuple, *, from_line: int | None = None) -> list:
    """闭包产物切分出的 run（可按起始行号过滤，用来单独看尾部那一段）。"""
    return [run for run in SB._split_runs(facts)
            if run[0].kind == SB._KIND_TABLE_ADJACENT
            and run[0].table_scope == CLOSURE_SCOPE
            and (from_line is None or run[0].key[1] == from_line)]


def _case_trailing_single_line():
    """T42（1）（14）（15）表后单行：provisional 邻接、原文与定位完整、无 span / 简介。"""
    doc = _with_table()
    tail = doc.after(y0=60.0, text="注：本表数据经审计。")
    view = doc.view()
    facts = SB._collect_line_facts(view)
    absorbed = {fact.key for fact in _absorbed(facts)}
    check(absorbed == {(1, SKELETON_CAPTION), (1, tail)},
          f"T42 表后单行必须进入 provisional 邻接闭包，得到 {sorted(absorbed)}")
    staged = [fact for fact in facts if fact.key == (1, tail)]
    check(len(staged) == 1
          and staged[0].kind == SB._KIND_TABLE_ADJACENT
          and staged[0].table_scope == CLOSURE_SCOPE
          and staged[0].table_reason == CLOSURE_REASON,
          f"T42 表后行必须落成既有 provisional 签名，得到 "
          f"{[(f.kind, f.table_scope, f.table_reason) for f in staged]}")
    check(staged and staged[0].node_id == "n1",
          "T42 表后行必须保留节点归属（TS5 据此判定它属于哪一段材料）")
    lines = _lines_of(view)
    check(staged and staged[0].line is lines[tail],
          "T42 表后行必须保留**原来那一行对象**（原文 / 页码 / 几何不变）")
    runs = _staged_runs(facts, from_line=tail)
    check(len(runs) == 1, f"T42 表后单行必须自成一段范围，得到 {len(runs)}")
    if not runs:
        return
    disposition = _one_disposition(runs[0])
    check(disposition.range_kind == "table_adjacency"
          and disposition.span_id is None and disposition.confidence is None,
          f"T42 表后范围不得产生 span / confidence，得到 "
          f"({disposition.range_kind}, {disposition.span_id}, {disposition.confidence})")
    check(disposition.line_count == 1
          and (disposition.start_page, disposition.start_line) == (1, tail)
          and (disposition.end_page, disposition.end_line) == (1, tail),
          f"T42 处置记录必须给出精确起止位置，得到 "
          f"({disposition.start_page},{disposition.start_line})-"
          f"({disposition.end_page},{disposition.end_line})")
    text = "".join(_tight(lines[index].text) for index
                   in range(disposition.start_line, disposition.end_line + 1))
    check(text == "注：本表数据经审计。",
          f"T42 处置记录必须能读回表后原文全文，得到 {text!r}")


def _case_trailing_two_lines():
    """T42（2）（13）表后两行连续：合成一段、逐位守恒、不得删文本。"""
    doc = _with_table()
    first = doc.after(y0=60.0, text="资料来源：公司公告。")
    second = doc.after(y0=46.0, text="注：口径与上年一致。")
    view = doc.view()
    facts = SB._collect_line_facts(view)
    absorbed = {fact.key for fact in _absorbed(facts)}
    check(absorbed == {(1, SKELETON_CAPTION), (1, first), (1, second)},
          f"T42 表后连续两行必须被同一段闭包覆盖，得到 {sorted(absorbed)}")
    runs = _staged_runs(facts, from_line=first)
    check(len(runs) == 1 and len(runs[0]) == 2,
          f"T42 表后连续两行必须合成**一段**范围，得到 "
          f"{[(r[0].key, len(r)) for r in runs]}")
    # 守恒：行数与 tight 字符数都不因改桶而变化（吸收是改桶，不是删文本）。
    check(sum(len(run) for run in SB._split_runs(facts)) == len(facts),
          "T42 切分后行数必须守恒（闭包只改桶，不删行）")
    check(sum(SB.tight_char_count_of(f.line.text)
              for run in SB._split_runs(facts) for f in run)
          == sum(SB.tight_char_count_of(f.line.text) for f in facts),
          "T42 切分后 tight 字符数必须守恒")
    lines = _lines_of(view)
    for fact in facts:
        check(fact.line is lines[fact.key[1]],
              f"T42 闭包不得替换任何行对象（原文 / 页码 / 几何不变）：{fact.key}")
    if runs:
        disposition = _one_disposition(runs[0])
        check(disposition.line_count == 2
              and disposition.tight_char_count
              == SB.tight_char_count_of("资料来源：公司公告。")
              + SB.tight_char_count_of("注：口径与上年一致。"),
              f"T42 处置记录必须给出真实行数与 tight 字符数，得到 "
              f"{disposition.line_count} / {disposition.tight_char_count}")


def _case_trailing_between_tables():
    """T42（3）（10）两表之间的说明行：暂存为一段，且两个表格区域不得并成一段。"""
    doc = _with_table()
    first = doc.after(y0=60.0, text="说明：上表为合并口径。")
    second = doc.after(y0=46.0, text="注：单位为千元。")
    doc.table(top_y=20.0)          # 第二张表：与 46 行相距 16pt（> 8pt 邻近阈值）
    facts = doc.facts()
    absorbed = {fact.key for fact in _absorbed(facts)}
    check(absorbed == {(1, SKELETON_CAPTION), (1, first), (1, second)},
          f"T42 两表之间的说明行必须被暂存，得到 {sorted(absorbed)}")
    runs = SB._split_runs(facts)
    # 只看**含这两行说明**的那一段 provisional 范围：前导闭包的表题行另成一段
    # （中间隔着冻结的 `adjacent_to_table` 单位行），那是前导闭包的合法产物，
    # 与本用例要验的"两表之间"无关。
    staging = [run for run in runs if run[0].kind == SB._KIND_TABLE_ADJACENT
               and run[0].table_scope == CLOSURE_SCOPE
               and any(row.key == (1, first) for row in run)]
    inner = [run for run in runs if run[0].kind == SB._KIND_TABLE_INSIDE]
    check(len(staging) == 1 and len(inner) == 2,
          f"T42 两个表格区域必须各自成段（不得与前一个区域合并），得到 "
          f"staging={len(staging)} inside={len(inner)}")
    check(len(staging) == 1
          and [row.key for row in staging[0]] == [(1, first), (1, second)],
          f"T42 两表之间的说明行必须合成**一段**，得到 "
          f"{[(r[0].key, len(r)) for r in staging]}")
    if not (staging and len(inner) == 2):
        return
    check(SB._run_merge_key(staging[0][0]) != SB._run_merge_key(inner[0][0]),
          "T42 provisional 邻接段与表格内部段的合并键必须不同")
    left = _one_disposition(staging[0])
    right = _one_disposition(inner[-1])
    check((left.end_page, left.end_line) < (right.start_page, right.start_line),
          f"T42 两段范围不得互相覆盖，得到 "
          f"({left.start_page},{left.start_line})-({left.end_page},{left.end_line}) 与 "
          f"({right.start_page},{right.start_line})-({right.end_page},{right.end_line})")


def _case_trailing_heading_boundary():
    """T42（4）表后紧接 heading：不得吸收 heading、也不得越过它。"""
    doc = _with_table()
    first = doc.after(y0=60.0, text="注：上表含少数股东权益。")
    heading = doc.line(x0=56.64, x1=300.0, y0=40.0, y1=50.0, text="（三）下一小节",
                       size=13.0, bold=True, state="heading_node", attachment=None)
    below = doc.after(y0=20.0, text="标题之下的表后行")
    facts = doc.facts()
    absorbed = {fact.key for fact in _absorbed(facts)}
    check(absorbed == {(1, SKELETON_CAPTION), (1, first)},
          f"T42 尾部闭包必须停在 heading 之前，得到 {sorted(absorbed)}")
    kinds = _kinds(facts)
    check(kinds.get((1, heading)) == SB._KIND_HEADING,
          f"T42 标题行的类别必须原样保留，得到 {kinds.get((1, heading))!r}")
    check(kinds.get((1, below)) == SB._KIND_REGULAR,
          f"T42 标题之下的行不得被吸收（闭包不得越过标题），得到 "
          f"{kinds.get((1, below))!r}")


def _case_trailing_node_boundary():
    """T42（5）表后跨节点：残段不得越过节点边界。"""
    doc = _with_table()
    first = doc.after(y0=60.0, text="注：本期无重大变化。")
    other = doc.after(y0=46.0, text="另一节点的正文", node_id="n2")
    facts = doc.facts()
    absorbed = {fact.key for fact in _absorbed(facts)}
    check(absorbed == {(1, SKELETON_CAPTION), (1, first)},
          f"T42 尾部闭包必须停在节点边界，得到 {sorted(absorbed)}")
    kinds = _kinds(facts)
    check(kinds.get((1, other)) == SB._KIND_REGULAR,
          f"T42 另一节点的行不得被吸收，得到 {kinds.get((1, other))!r}")


def _case_trailing_blank_and_formal_unassigned():
    """T42（6）空白断点 / formal unassigned：既是结构封口，也不得被吸收。"""
    # (a) 断点紧贴区域尾行 ⇒ 一段都不暂存
    doc = _with_table()
    blank = doc.line(x0=56.64, x1=60.0, y0=60.0, y1=70.0, text="   ", size=BODY_SIZE)
    below = doc.after(y0=40.0, text="空白之下的表后行")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)} == {(1, SKELETON_CAPTION)},
          f"T42 空白行封口时不得暂存任何行，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    kinds = _kinds(facts)
    check(kinds.get((1, blank)) == SB._KIND_EMPTY
          and kinds.get((1, below)) == SB._KIND_REGULAR,
          f"T42 空白行与空白之下的行类别都必须原样保留，得到 "
          f"{kinds.get((1, blank))!r} / {kinds.get((1, below))!r}")
    # (b) 表后行 + 空白断点 + 更下面的行 ⇒ 只暂存空白之上的那一段
    doc2 = _with_table()
    first = doc2.after(y0=60.0, text="说明：本表口径与上年一致。")
    doc2.line(x0=56.64, x1=60.0, y0=44.0, y1=54.0, text="   ", size=BODY_SIZE)
    below2 = doc2.after(y0=24.0, text="断点之下的行")
    facts2 = doc2.facts()
    check({fact.key for fact in _absorbed(facts2)} == {(1, SKELETON_CAPTION), (1, first)},
          f"T42 空白断点必须封口尾部残段，得到 "
          f"{sorted(f.key for f in _absorbed(facts2))}")
    check(_kinds(facts2).get((1, below2)) == SB._KIND_REGULAR,
          "T42 断点之下的行必须保持 regular（闭包不得越过断点）")
    # (c) formal unassigned 同样封口、同样不被吸收
    doc3 = _with_table()
    first3 = doc3.after(y0=60.0, text="说明：同上。")
    formal = doc3.line(x0=56.64, x1=500.0, y0=44.0, y1=54.0, text="无归属正文",
                       size=BODY_SIZE, state="formal_unassigned", attachment=None,
                       node_id=None)
    below3 = doc3.after(y0=24.0, text="未归属之下的行")
    facts3 = doc3.facts()
    check({fact.key for fact in _absorbed(facts3)} == {(1, SKELETON_CAPTION), (1, first3)},
          f"T42 formal_unassigned 必须封口尾部残段，得到 "
          f"{sorted(f.key for f in _absorbed(facts3))}")
    kinds3 = _kinds(facts3)
    check(kinds3.get((1, formal)) == SB._KIND_FORMAL
          and kinds3.get((1, below3)) == SB._KIND_REGULAR,
          f"T42 未归属行与其下的行类别都必须原样保留，得到 "
          f"{kinds3.get((1, formal))!r} / {kinds3.get((1, below3))!r}")


def _case_trailing_physical_adjacency():
    """T42（1）（3）（4）（5）（6）（7）（11）物理邻接驱动的尾部 provisional 入口。

    全部反例只靠几何（同页 / 同栏交叠 / 源序相邻 / 局部垂直间距）与结构边界成立，
    与这一行的**内容形态无关**。
    """
    # (a) 满栏**单行**表注：必须进入 provisional。`sb-6` 的"满栏正文负证明成立 ⇒
    #     整段不暂存"会让它永久留在正式正文层（进而进 span / 简介）—— 这正是本轮要
    #     消除的失效模式。"满栏"在这里不再是排除理由。
    doc = _with_table()
    note = doc.after(y0=60.0, x0=PROSE_LEFT, x1=PROSE_RIGHT, size=BODY_SIZE,
                     text="说明：上表中募集资金已使用金额为累计数。")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)} == {(1, SKELETON_CAPTION), (1, note)},
          f"T42 满栏单行表注必须进入 provisional（不得因满栏而排除），得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    staged = [fact for fact in facts if fact.key == (1, note)]
    check(len(staged) == 1
          and staged[0].table_scope == CLOSURE_SCOPE
          and staged[0].table_reason == CLOSURE_REASON
          and staged[0].node_id == "n1"
          and staged[0].line is not None,
          f"T42 满栏单行表注必须落成既有 provisional 签名、保留节点归属与原文，得到 "
          f"{[(f.table_scope, f.table_reason, f.node_id) for f in staged]}")
    verified, over = verified_closure(facts)
    check(not over and (1, note) in verified,
          f"T42 满栏单行表注必须经独立重算成立（满栏不是排除理由）：{over}")

    # (b) staged prefix 遇到不满足物理邻接的行：**prefix 保留**、当前行不被吞。
    #     `sb-6` 语义下这里会整段回滚（满栏 ⇒ 全部退回正文），是本轮反例的直接靶子。
    doc = _with_table()
    first = doc.after(y0=60.0, x0=PROSE_LEFT, x1=PROSE_RIGHT, size=BODY_SIZE,
                      text="说明：上表数据经审计。")
    far = doc.after(y0=10.0, text="间距过大的表后行")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)} == {(1, SKELETON_CAPTION), (1, first)},
          f"T42 staged prefix 必须在停止行之前保留（不得整段回滚），得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    check(_kinds(facts).get((1, far)) == SB._KIND_REGULAR,
          "T42 不满足物理邻接的行不得被吞入 provisional")
    runs = _staged_runs(facts, from_line=first)
    check(len(runs) == 1 and [row.key for row in runs[0]] == [(1, first)],
          f"T42 prefix 必须自成一段且不含停止行，得到 "
          f"{[(r[0].key, [row.key for row in r]) for r in runs]}")

    # (c) 表后普通正文作为**第一行**：范围有限、TS5 可确定性恢复为正文段。
    doc = _with_table()
    rows = [doc.prose(y0=60.0 - step * 14.0, text=f"表后正文第{step + 1}行")
            for step in range(2)]
    view = doc.view()
    facts = SB._collect_line_facts(view)
    check({fact.key for fact in _absorbed(facts)}
          == {(1, SKELETON_CAPTION), (1, rows[0]), (1, rows[1])},
          f"T42 表后普通正文必须有限地进入 provisional，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    staged = [fact for fact in facts if fact.key in {(1, rows[0]), (1, rows[1])}]
    check(len(staged) == 2
          and all(fact.node_id == "n1"
                  and fact.state.state == "body_under_node"
                  and fact.state.body_attachment == "preceding_heading"
                  for fact in staged),
          f"T42 暂存的普通正文必须原样保留节点 / 结构状态 / 附着方式"
          f"（TS5 判回正文段时身份不变），得到 "
          f"{[(f.node_id, f.state.state, f.state.body_attachment) for f in staged]}")
    lines = _lines_of(view)
    check(all(fact.line is lines[fact.key[1]] for fact in staged),
          "T42 暂存的普通正文必须保留**原来那一行对象**（原文 / 页码 / 几何不变）")
    runs = _staged_runs(facts, from_line=rows[0])
    check(len(runs) == 1 and len(runs[0]) == 2 and len(runs[0]) <= TRAILING_CAP,
          f"T42 表后普通正文必须合成**有限**的一段（≤ 上限），得到 "
          f"{[(r[0].key, len(r)) for r in runs]}")
    if runs:
        disposition = _one_disposition(runs[0])
        check(disposition.range_kind == "table_adjacency"
              and disposition.span_id is None and disposition.confidence is None
              and disposition.left_boundary_cause is None
              and disposition.right_boundary_cause is None,
              f"T42 provisional 段不得携带 span / confidence / 边界成因，得到 "
              f"({disposition.range_kind}, {disposition.span_id}, "
              f"{disposition.confidence}, {disposition.left_boundary_cause}, "
              f"{disposition.right_boundary_cause})")
        text = "".join(_tight(lines[index].text) for index
                       in range(disposition.start_line, disposition.end_line + 1))
        check(text == "表后正文第1行表后正文第2行",
              f"T42 处置记录的位置必须能逐字读回表后正文"
              f"（`kept_as_paragraph` / `absorbed_into_body` 的依据），得到 {text!r}")
    check(not [run for run in SB._split_runs(facts)
               if run[0].kind == SB._KIND_REGULAR
               and any(row.key in {(1, rows[0]), (1, rows[1])} for row in run)],
          "T42 暂存的普通正文不得同时留在任何 regular 段里（否则会重复成 span）")

    # (d) 窄标签行 + 满栏续行：整体进入**同一段** provisional（不得在续行处断开）。
    doc = _with_table()
    label = doc.after(y0=60.0, text="注：")
    cont = doc.prose(y0=46.0, text="本公司视其日常资金管理的需要，将该等资金计入相关科目。")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)}
          == {(1, SKELETON_CAPTION), (1, label), (1, cont)},
          f"T42 窄标签行与其满栏续行必须整体进入 provisional，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    runs = _staged_runs(facts, from_line=label)
    check(len(runs) == 1 and [row.key for row in runs[0]] == [(1, label), (1, cont)],
          f"T42 窄标签行与满栏续行必须合成**同一段**，得到 "
          f"{[(r[0].key, [row.key for row in r]) for r in runs]}")

    # (e) 垂直间距超过本页本栏局部阈值：不建立邻接。
    doc = _with_table()
    far = doc.after(y0=10.0, text="间距过大的表后行")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)} == {(1, SKELETON_CAPTION)},
          f"T42 垂直间距超过局部阈值时不得建立邻接，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    check(_kinds(facts).get((1, far)) == SB._KIND_REGULAR,
          "T42 间距过大的表后行必须保持 regular")

    # (f) 同页但**不同栏**：水平范围与已证明区域不交叠 ⇒ 不建立邻接。
    doc = _with_table()
    other = doc.after(y0=60.0, x0=10.0, x1=90.0, text="另一栏的正文")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)} == {(1, SKELETON_CAPTION)},
          f"T42 同一页但不同正文栏时不得建立邻接，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    check(_kinds(facts).get((1, other)) == SB._KIND_REGULAR,
          "T42 另一栏的行必须保持 regular")

    # (g) 固定上限：连续 4 行都合格时只暂存前 3 行，第 4 行留在正文。
    doc = _with_table()
    rows = [doc.after(y0=60.0 - step * 14.0, text=f"表后残段第{step + 1}行")
            for step in range(4)]
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)}
          == {(1, SKELETON_CAPTION), (1, rows[0]), (1, rows[1]), (1, rows[2])},
          f"T42 尾部 provisional 必须受固定上限约束，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    check(_kinds(facts).get((1, rows[3])) == SB._KIND_REGULAR,
          "T42 超出上限的那一行必须保持 regular（不得越过上限继续下探）")
    runs = _staged_runs(facts, from_line=rows[0])
    check(len(runs) == 1 and len(runs[0]) == TRAILING_CAP,
          f"T42 上限内的连续行必须合成一段，得到 "
          f"{[(r[0].key, len(r)) for r in runs]}")


def _case_trailing_furniture():
    """T42（11）家具行（页码 / 页眉页脚）：不得参与，且构成结构封口。"""
    # (a) 家具行夹在区域与候选之间 ⇒ 一段都不暂存
    doc = _with_table()
    doc.line(x0=270.0, x1=290.0, y0=60.0, y1=70.0, text="12", size=9.0,
             state="non_content", attachment=None, furniture=True)
    below = doc.after(y0=40.0, text="家具行之下的表后行")
    facts = doc.facts()
    check({fact.key for fact in _absorbed(facts)} == {(1, SKELETON_CAPTION)},
          f"T42 家具行封口时不得暂存任何行，得到 "
          f"{sorted(f.key for f in _absorbed(facts))}")
    check(_kinds(facts).get((1, below)) == SB._KIND_REGULAR,
          "T42 家具行之下的行不得被吸收")
    # (b) 表后行 + 家具行 + 更下面的行 ⇒ 只暂存家具行之上的那一段
    doc2 = _with_table()
    first = doc2.after(y0=60.0, text="注：本表含关联方交易。")
    doc2.line(x0=270.0, x1=290.0, y0=44.0, y1=54.0, text="12", size=9.0,
              state="non_content", attachment=None, furniture=True)
    below2 = doc2.after(y0=24.0, text="家具行之下的行")
    facts2 = doc2.facts()
    check({fact.key for fact in _absorbed(facts2)} == {(1, SKELETON_CAPTION), (1, first)},
          f"T42 家具行必须封口尾部残段，得到 "
          f"{sorted(f.key for f in _absorbed(facts2))}")
    check(_kinds(facts2).get((1, below2)) == SB._KIND_REGULAR,
          "T42 家具行之下的行必须保持 regular（闭包不得越过家具行）")
    check(all(not fact.line.is_furniture for fact in _absorbed(facts2)),
          "T42 家具行自身永远不得进入任何闭包段")


def _case_trailing_returns_to_body():
    """T42（8）（9）（15）表后正文先 provisional 后，TS5 仍能裁决回正文：

    * 原文 / 页码 / 行号 / 几何 / 节点 / 状态 / 附着方式全部完整保留 ⇒
      `kept_as_paragraph`（原样成为正文段）与 `absorbed_into_body`（并入相邻正文段）
      都有可用的定位与身份；
    * 处置记录不带 span_id / confidence / 左右边界成因 ⇒ 它现在**不是**材料，
      也没有对任何 span 或简介作出声明（TS5 裁决前不得被消费）。
    """
    doc = _with_table()
    first = doc.after(y0=60.0, text="本期营业收入同比增长。")
    second = doc.after(y0=46.0, text="毛利率保持稳定。")
    view = doc.view()
    facts = SB._collect_line_facts(view)
    lines = _lines_of(view)
    staged = [fact for fact in facts if fact.key in {(1, first), (1, second)}]
    check(len(staged) == 2
          and all(fact.kind == SB._KIND_TABLE_ADJACENT for fact in staged),
          f"T42 表后正文行必须先落成 provisional，得到 "
          f"{[(fact.key, fact.kind) for fact in staged]}")
    for fact in staged:
        check(fact.line is lines[fact.key[1]],
              f"T42 原文对象必须逐行保留：{fact.key}")
        check(fact.node_id == "n1"
              and fact.state.state == "body_under_node"
              and fact.state.body_attachment == "preceding_heading",
              f"T42 节点 / 结构状态 / 附着方式必须原样保留（TS5 判回正文段时身份不变）："
              f"{fact.key}")
    runs = _staged_runs(facts, from_line=first)
    check(len(runs) == 1 and len(runs[0]) == 2,
          f"T42 表后正文必须合成一段，得到 {[(r[0].key, len(r)) for r in runs]}")
    if not runs:
        return
    disposition = _one_disposition(runs[0])
    check(disposition.span_id is None and disposition.confidence is None
          and disposition.left_boundary_cause is None
          and disposition.right_boundary_cause is None,
          f"T42 provisional 段不得携带 span / confidence / 边界成因，得到 "
          f"({disposition.span_id}, {disposition.confidence}, "
          f"{disposition.left_boundary_cause}, {disposition.right_boundary_cause})")
    text = "".join(_tight(lines[index].text) for index
                   in range(disposition.start_line, disposition.end_line + 1))
    check(text == "本期营业收入同比增长。毛利率保持稳定。",
          f"T42 处置记录的位置必须能逐字读回表后正文（kept_as_paragraph 的依据），"
          f"得到 {text!r}")
    body_runs = [run for run in SB._split_runs(facts)
                 if run[0].kind == SB._KIND_REGULAR]
    check(bool(body_runs),
          "T42 表后残段暂存不得删掉任何正文 run（absorbed_into_body 的落点仍在）")
    check(all(run[0].kind != SB._KIND_REGULAR for run in runs),
          "T42 非 regular 范围不进入 span 构建路径（`_build_snapshot` 只为 regular "
          "run 建 span / 简介），因此 provisional 段既无 span 也不进简介")


def _case_trailing_conservation():
    """T42（13）（14）尾部闭包的守恒与"全文 / 精确位置可读回"。"""
    doc = _with_table()
    first = doc.after(y0=60.0, text="注：本表口径与上年一致。")
    view = doc.view()
    facts = SB._collect_line_facts(view)
    runs = SB._split_runs(facts)
    check(sum(len(run) for run in runs) == len(facts),
          "T42 切分后行数必须守恒（尾部闭包只改桶，不删行）")
    check(sum(SB.tight_char_count_of(f.line.text) for run in runs for f in run)
          == sum(SB.tight_char_count_of(f.line.text) for f in facts),
          "T42 切分后 tight 字符数必须守恒")
    check(len(runs) == len({run[0].key for run in runs}),
          "T42 切分必须无重叠、无重复段")
    lines = _lines_of(view)
    check(all(fact.line is lines[fact.key[1]] for fact in facts),
          "T42 闭包不得替换任何行对象")
    # 独立重算：构建器的闭包产物必须逐行成立，且本模块不得漏判
    verified, over = verified_closure(facts)
    absorbed = {fact.key for fact in _absorbed(facts)}
    check(not over, f"T42 被吸收的行必须经独立重算成立（前导或尾部之一）：{over}")
    check(verified == absorbed,
          f"T42 独立重算必须与构建器的闭包产物逐行一致，差集 "
          f"verified−absorbed={sorted(verified - absorbed)}，"
          f"absorbed−verified={sorted(absorbed - verified)}")
    check((1, first) in verified, "T42 独立重算必须认定表后残段成立")


def _case_trailing_keeps_leading():
    """T42（12）尾部闭包不得改变前导闭包的结果（同一骨架加尾部行前后逐键相等）。"""
    bare = _with_table()
    bare_facts = bare.facts()
    bare_keys = {fact.key for fact in bare_facts}
    bare_absorbed = {fact.key for fact in _absorbed(bare_facts)}
    doc = _with_table()
    doc.after(y0=60.0, text="注：表后说明。")
    facts = doc.facts()
    now_leading = {fact.key for fact in _absorbed(facts) if fact.key in bare_keys}
    check(now_leading == bare_absorbed,
          f"T42 加入表后行不得改变前导闭包的产物，得到 {sorted(now_leading)} != "
          f"{sorted(bare_absorbed)}")
    for label, sample in (("无表后行", bare_facts), ("有表后行", facts)):
        verified, over = verified_closure(sample)
        check(not over,
              f"T42 {label}时被吸收的行必须经独立重算成立：{over}")
        check(verified == {fact.key for fact in _absorbed(sample)},
              f"T42 {label}时独立重算必须与构建器逐行一致，差集 "
              f"{sorted(verified ^ {f.key for f in _absorbed(sample)})}")


# ---------------------------------------------------------------------------
# 4. 真实冻结文档（三份真实 + 夹具文档）：同一条规则
# ---------------------------------------------------------------------------

def _frozen_facts(document_id: str):
    """只读冻结 TS3 版式与结构终态，投影成事实序列（不重跑、不改写）。"""
    doc_dir = _FROZEN_TS3_RUN / document_id
    layout = PageLayout.from_dict(json.loads(
        (doc_dir / "page_layout.json").read_text(encoding="utf-8")))
    rows = json.loads((doc_dir / "line_structure_assignment.json").read_text(
        encoding="utf-8"))["lines"]
    states = tuple(
        _State(page_number=int(r["page_number"]), line_index=int(r["line_index"]),
               state=r["structure_state"], body_attachment=r["body_attachment"],
               node_id=r["node_id"])
        for r in rows)
    return SB._collect_line_facts(_FactsView(layout, _Structure(states)))


def _case_real_documents():
    """（11）（12）（13）真实文档：闭包形态 / 边界 / 正文负证明。"""
    if not _FROZEN_TS3_RUN.is_dir():
        skip(f"T41 冻结 TS3 产物目录不存在：{_FROZEN_TS3_RUN}")
        return
    summary: dict = {}
    required_hits: dict = {}
    for document_id in _REAL_DOCUMENTS + (_FIXTURE_DOCUMENT,):
        facts = _frozen_facts(document_id)
        absorbed = _absorbed(facts)
        regular = [fact for fact in facts if fact.kind == SB._KIND_REGULAR]
        absorbed_keys = {fact.key for fact in absorbed}
        index = {fact.key: position for position, fact in enumerate(facts)}
        prose = {fact.key for fact in _prose_shape(facts)}
        prose_width = _prose_width(facts)
        # 方向归类由**独立重算**给出（不看构建器走了哪条分支）：前导成立即前导，否则尾部。
        leading = {fact.key for fact in absorbed
                   if _leading_reason(facts, index, fact, prose, prose_width) is None}
        trailing = absorbed_keys - leading
        summary[document_id] = {
            "facts": len(facts), "absorbed": len(absorbed),
            "leading": len(leading), "trailing": len(trailing),
            "regular": len(regular),
            "caption_form_regular": sum(1 for fact in regular
                                        if _caption_form(fact.line.text)),
        }
        # 12-a 同一条规则：两个方向共用**同一个** provisional 签名与原因码。
        check(all(fact.table_reason == CLOSURE_REASON for fact in absorbed),
              f"T41 {document_id} 闭包行的原因码必须一致")
        check(all(fact.node_id is not None for fact in absorbed),
              f"T41 {document_id} 闭包行必须落在某个节点内")
        check(all(fact.table_scope == CLOSURE_SCOPE for fact in absorbed),
              f"T41 {document_id} 闭包行的 table_scope 必须统一为 {CLOSURE_SCOPE!r}")
        # 12-h 独立重算：每一行都必须由"前导"或"尾部"之一成立，构建器不得漏判。
        verified, over = verified_closure(facts)
        check(not over,
              f"T41 {document_id} 被吸收的行必须经独立重算成立（前导或尾部之一）："
              f"{over[:3]}")
        summary[document_id]["verified"] = len(verified)
        check(absorbed_keys <= verified,
              f"T41 {document_id} 构建器声称吸收的行都必须在独立重算里成立，得到 "
              f"{sorted(absorbed_keys - verified)[:5]}")
        # 12-b 每个闭包行都必须紧贴一段**已证明的**表格区域（同一节点、中间不夹别的
        #      行）：前导向前看、尾部向后看 —— 方向由独立重算认定。
        for key in sorted(absorbed_keys):
            fact = facts[index[key]]
            region = (_region_after(facts, index[key]) if key in leading
                      else _region_before(facts, index[key]))
            inside = [f for f in region if f.kind == SB._KIND_TABLE_INSIDE]
            check(bool(inside)
                  and all(f.node_id == fact.node_id for f in region),
                  f"T41 {document_id} 闭包行 {key} 必须紧贴同一节点的 inside_table "
                  f"区域，得到 region={[f.key for f in region][:6]}")
        # 12-c 上限：逐区域、**两个方向**都不得超过 3 行。
        for region in _regions(facts):
            inside = [f for f in region if f.kind == SB._KIND_TABLE_INSIDE]
            if not inside:
                continue
            step = index[region[0].key] - 1
            count = 0
            while step >= 0 and facts[step].key in leading:
                count += 1
                step -= 1
            check(count <= 3,
                  f"T41 {document_id} 单区域前导闭包不得超过 3 行，得到 {count}")
            step = index[region[-1].key] + 1
            count = 0
            while step < len(facts) and facts[step].key in trailing:
                count += 1
                step += 1
            check(count <= 3,
                  f"T41 {document_id} 单区域尾部闭包不得超过 3 行，得到 {count}")
        # 12-d 前导闭包的标记独立重算：每个**前导**闭包行要么至少满足一条几何标记，
        #      要么是退化碎片。尾部闭包**不要求**标记 —— 真实表注与正文段落在字号 /
        #      左边界 / 宽度上可以完全一致（见第 3b 节）；这里的计数进 summary，供
        #      人工审计"暂存了多少条没有标记的行"。
        unmarked = []
        for key in sorted(leading):
            fact = facts[index[key]]
            region = _region_after(facts, index[key])
            inside = [f for f in region if f.kind == SB._KIND_TABLE_INSIDE]
            if not inside:
                continue
            marks = _marks(fact, region[0], inside, prose_width)
            if not (any(marks) or _fragment(fact)):
                unmarked.append((key, marks, _tight(fact.line.text)[:24]))
        check(not unmarked,
              f"T41 {document_id} 前导闭包行必须满足至少一条几何标记"
              f"（居中/加粗/靠右窄排/区域右侧窄排）或是退化碎片，得到 {unmarked[:3]}")
        summary[document_id]["trailing_prose_shape"] = len(trailing & prose)
        # 12-e 正文负证明：**前导**方向仍是"满栏正文形态的行绝不能被吸收"（前导行必须
        #      靠几何标记自证身份，满栏正文一条标记都拿不到）。**尾部**方向不再是负
        #      证明 —— 尾部的判据是**物理邻接**，满栏与不满栏同权：满栏的表注必须进
        #      provisional，满栏的普通正文也只是**暂时**进 provisional（TS5 判回正文）。
        #      因此这里改为"尾部闭包行必须逐行满足物理邻接"（由 12-h 的独立重算覆盖），
        #      并统计其中满栏正文形态的行数 —— 那正是 TS5 应当判回正文段的部分。
        swallowed_leading = sorted(leading & prose)
        check(not swallowed_leading,
              f"T41 {document_id} 满栏正文形态的行不得被前导闭包吸收，得到 "
              f"{swallowed_leading[:5]}")
        check(len(prose) > 0,
              f"T41 {document_id} 必须存在满栏正文行（否则前导负证明是空的）")
        # 12-e' §六（13）：表注**文字形态**的行若满足 §四.1 的物理邻接起始资格，就
        #      必须已经取得合法 provisional 入口。文字形态只用于**离线诊断**，不是
        #      判定输入；这里把它当成"抽样探针"：它挑出的行若仍留在正式正文层，唯一
        #      可接受的解释是它不满足物理邻接（跨页 / 不交叠 / 间距超阈值 / 被上限或
        #      前面的行挡住），逐行记录原因供人工复核。
        pre_kinds = _pre_closure_kinds(facts)
        order = [fact for fact in facts if not fact.line.is_furniture]
        order_index = {fact.key: position for position, fact in enumerate(order)}
        regions = _regions_of(order, pre_kinds)
        runs = _trailing_keys(order, order_index, index, regions, pre_kinds, leading)
        baseline = (order, order_index, pre_kinds, leading, regions, runs)
        note_residual = []
        for fact in regular:
            if not _note_form(fact.line.text):
                continue
            reason = _trailing_reason(fact, baseline)
            note_residual.append((fact.key, reason, _tight(fact.line.text)[:28]))
            check(reason is not None,
                  f"T41 {document_id} 表注文字形态行 {fact.key} 满足 §四.1 物理邻接"
                  f"起始资格，却仍留在正式正文层（必须取得合法 provisional 入口）")
        summary[document_id]["note_form_residual"] = len(note_residual)
        _results.setdefault("note_form_residual", {})[document_id] = [
            (list(key), reason, text) for key, reason, text in note_residual]
        # 12-f 残留的表题形态行：要么结构上够不着（前方没有已证明区域 / 中间夹着
        #      非正文行 / 跨节点 / 超过上溯上限 / 命中贴左边界、字号或满栏正文停止
        #      条件），要么**几何上根本不是表格前导**（一条标记都不满足、也不是退化
        #      碎片）。两者都不是"闭包漏走"；只有"几何上明明是表格前导、也够得着、
        #      却仍留在 regular"才算漏。
        residuals = [f for f in regular if _caption_form(f.line.text)]
        document_left = min(_x0(f) for f in regular) if regular else 0.0
        prose_width = _prose_width(facts)
        prose_limit = PROSE_WIDTH_FRAC * prose_width
        sizes: dict = {}
        for fact in regular:
            size = round(_line_size(fact.line), 1)
            sizes[size] = sizes.get(size, 0) + 1
        body_size = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] \
            if sizes else None
        unexplained = []
        geometry_only = 0
        for fact in residuals:
            j = index[fact.key]
            k = j + 1
            while k < len(facts) and facts[k].kind == SB._KIND_REGULAR:
                k += 1
            if k >= len(facts) or facts[k].kind not in TABLE_KINDS:
                continue                          # 前方没有已证明的表格区域
            region_start = k
            inside = []
            while k < len(facts) and facts[k].kind in TABLE_KINDS:
                if facts[k].kind == SB._KIND_TABLE_INSIDE:
                    inside.append(facts[k])
                k += 1
            if not inside:
                continue                          # 没有已证明的 inside_table
            if region_start - j > 3:
                continue                          # 超出上溯上限
            region = facts[region_start:k]
            if _leading_stop_reason(
                    fact, region, inside, node_id=region[0].node_id,
                    document_left=document_left, body_size=body_size,
                    prose_limit=prose_limit, prose_width=prose_width,
                    previous=_previous_in_order(facts, j)) is not None:
                geometry_only += 1
                continue                          # 几何/版式上够不着表格
            # 闭包是**连续上溯**：一旦更靠近表格的那一行不合格，闭包就停在它那里，
            # 不会再越过它去吸收上面的行。中间任何一行不成立 ⇒ 这一行本来就够不着。
            blocked = None
            for i in range(j + 1, region_start):
                blocked = _leading_stop_reason(
                    facts[i], region, inside, node_id=region[0].node_id,
                    document_left=document_left, body_size=body_size,
                    prose_limit=prose_limit, prose_width=prose_width,
                    previous=_previous_in_order(facts, i))
                if blocked is not None:
                    break
            if blocked is not None:
                geometry_only += 1
                continue
            unexplained.append((fact.key, _tight(fact.line.text)[:28]))
        summary[document_id]["residual_caption_form"] = len(residuals)
        summary[document_id]["residual_geometry_only"] = geometry_only
        check(not unexplained,
              f"T41 {document_id} 够得着、又是几何表格前导的表题形态行不得留在 regular，"
              f"得到 {unexplained}")
        # 12-g 诊断意义上的**单位行**：三份真实文档 + 夹具文档里都必须为 0 ——
        #      "单位行仍是正式 body span"就是本轮的缺陷本身。
        residual_units = [(f.key, _tight(f.line.text)[:24]) for f in regular
                          if _unit_form(f.line.text)]
        summary[document_id]["residual_unit_form"] = len(residual_units)
        check(not residual_units,
              f"T41 {document_id} 诊断意义上的单位行不得仍是正式 body span，得到 "
              f"{residual_units}")
        # 12-i 独立验收指定的**四组真实表后表注**：必须不再是正式 body span、必须
        #      进入 `table_adjacency` provisional、原文与定位完整、且**没有**被永久
        #      定性为表注（处置记录只给 provisional 位置，不给 span_id）。
        for key in dict(_REQUIRED_TRAILING_SAMPLES).get(document_id, ()):
            required_hits.setdefault(document_id, []).append(key)
            check(key in index,
                  f"T41 {document_id} 指定样本 {key} 必须存在于冻结事实序列里")
            if key not in index:
                continue
            fact = facts[index[key]]
            check(fact.kind == SB._KIND_TABLE_ADJACENT
                  and fact.table_scope == CLOSURE_SCOPE
                  and fact.table_reason == CLOSURE_REASON,
                  f"T41 {document_id} 指定样本 {key} 必须落成 provisional 签名"
                  f"（不得再是 regular / body span），得到 "
                  f"{(fact.kind, fact.table_scope, fact.table_reason)}")
            check(key in trailing,
                  f"T41 {document_id} 指定样本 {key} 必须由**尾部**闭包覆盖"
                  f"（已证明表格区域在它的上方）")
            runs = [run for run in SB._split_runs(facts)
                    if any(row.key == key for row in run)]
            if not check(len(runs) == 1
                         and runs[0][0].kind == SB._KIND_TABLE_ADJACENT,
                         f"T41 {document_id} 指定样本 {key} 必须落在唯一一段 provisional "
                         f"范围里，得到 {[(r[0].key, len(r), r[0].kind) for r in runs]}"):
                # 未暂存的行仍属于 `regular` run：此时构造非 regular 处置会 fail-closed
                # 抛错，掩盖本用例真正要报的失败。这里显式跳过，把失败留在上面的 check。
                continue
            run = runs[0]
            disposition = _one_disposition(run)
            check(disposition.span_id is None and disposition.confidence is None
                  and (disposition.start_page, disposition.start_line) <= key
                  <= (disposition.end_page, disposition.end_line),
                  f"T41 {document_id} 指定样本 {key} 必须被处置记录精确定位、"
                  f"且不带 span / confidence，得到 "
                  f"({disposition.start_page},{disposition.start_line})-"
                  f"({disposition.end_page},{disposition.end_line}) / "
                  f"{disposition.span_id}")
            texts = "".join(_tight(row.line.text) for row in run)
            check(texts != "",
                  f"T41 {document_id} 指定样本 {key} 所在范围必须保留原文")
    for document_id, keys in _REQUIRED_TRAILING_SAMPLES:
        hit = required_hits.get(document_id, [])
        check(sorted(hit) == sorted(keys),
              f"T41 {document_id} 指定的真实表后表注必须逐条核验到，得到 "
              f"{sorted(hit)} != {sorted(keys)}")
    _results["required_trailing_samples"] = {
        document_id: [list(key) for key in keys]
        for document_id, keys in _REQUIRED_TRAILING_SAMPLES}
    _results["real_documents"] = summary
    kcz = summary["NDSD_KCZ_2026"]
    check(kcz["absorbed"] > 0,
          f"T41 发生缺陷的真实文档必须至少吸收一行，得到 {kcz['absorbed']}")


#: 两个闭包**必须存在**的判定入口。静态检查不是"按名字列举再逐个扫"：它从这两个入口
#: 出发做"本模块内可达函数"的**传递闭包**（见 `_closure_reachable_source`），所以把
#: 判定拆进**新** helper 不能绕过禁字 / 禁文字判据的检查；而改名 / 删函数会在这里失败。
_CLOSURE_REQUIRED = ("_apply_table_leading_closure", "_apply_table_trailing_closure",
                     "_table_leading_marks", "_table_leading_fragment",
                     "_table_leading_anchored")

_SOURCE_FORBIDDEN = (
    (r"300750|宁德时代|CATL", "公司名"),
    (r"表\s*\d+\s*[-－–]\s*\d+", "固定表号"),
    (r"\bos-[0-9a-f]{8,}", "具体 span_id"),
    (r"\bev-[0-9a-f]{8,}", "具体 evidence_id"),
    (r"NDSD|KCZ|FIXTURE_BOND", "文档 / 案例标识"),
    (r"\(\s*(?:1[0-9]{2}|[1-9][0-9])\s*,\s*[0-9]{1,3}\s*\)", "固定页码 / 行号坐标"),
)

_SOURCE_FORBIDDEN_TEXT = (
    (r"startswith\([^)]*表", "'以表字开头'文字判定"),
    (r"startswith\([^)]*单位", "'以单位开头'文字判定"),
    (r"startswith\([^)]*注", "'以注字开头'文字判定"),
    (r"line\.text", "读取行文字"),
    (r"re\.(match|search|compile)", "正则文字过滤"),
    (r"\.text\s*(==|in|!=)", "文字相等 / 包含判定"),
)


def _closure_reachable_source(*entries: str) -> dict:
    """从给定函数出发，取"它调用的**本模块内**函数"的传递闭包及其源码。

    `None` 表示入口函数不存在（构建器没有实现该判定）——调用方据此判定失败。
    """
    pending: list = []
    for name in entries:
        function = getattr(SB, name, None)
        if not callable(function):
            return {}
        pending.append(function)
    sources: dict = {}
    while pending:
        function = pending.pop()
        if function.__name__ in sources:
            continue
        source = inspect.getsource(function)
        sources[function.__name__] = source
        for node in ast.walk(ast.parse(textwrap.dedent(source))):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            callee = getattr(SB, node.id, None)
            if inspect.isfunction(callee) and callee.__module__ == SB.__name__:
                pending.append(callee)
    return sources


#: 公司字面量的**精确**口径：只允许作为 §18.14.4 人工验收清单项 7 的**封存标识符**
#: 出现（`non_300750_positive_fixture_and_legacy_negative`）。那是一份由用户 + Codex
#: 批准、并经 sealed attestation 封存的 check ID，不是公司专用生产规则；改它即等于改写
#: 已批准的验收事实，因此只能"精确放行这一个字面量"，其余任何出现仍然 fail-closed。
_SEALED_CHECK_LITERALS = frozenset(SP.APPROVAL_REVIEW_CHECK_IDS)


def _fold_str(node) -> str | None:
    """把 `"a" + "b"` 这类**纯常量加法**静态折叠成一个串；不可折叠时返回 `None`。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold_str(node.left), _fold_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _static_strings(tree) -> list:
    """AST 里全部**可静态求值**的字符串（含常量加法折叠的结果）。"""
    values: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            folded = _fold_str(node)
            if folded is not None:
                values.append(folded)
    return values


def _company_literal_violations(source: str) -> list:
    """返回源码里**违反**"不得含公司专用字面量"的逐条问题（纯函数，便于反例自证）。

    判定分三步，任何一步不成立都算违规：

    1. 逐字面量核验：含 token 的静态串必须**整条**属于 §18.14.4 封存 check ID
       （`"300" + "750"` 会被常量折叠后在这里被抓到）；
    2. 残余核验：把源码里**每个字符串常量值**的文本逐一抠掉后，token 必须一次都不剩
       ——注释与标识符里的写法会在这里被抓到；
    3. 重复核验：同一个封存 check ID 在同模块里最多出现一次（不得复制 / 散落多处）。
    """
    tree = ast.parse(source)
    sealed = _SEALED_CHECK_LITERALS
    values = _static_strings(tree)
    problems: list = []
    for token in ("300750", "宁德时代", "CATL"):
        hits = {value for value in values if token in value}
        problems.extend(
            f"非 §18.14.4 封存 check ID 的字符串含公司字面量 {token!r}：{value!r}"
            for value in sorted(hits - sealed))
    residue = source
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            residue = residue.replace(node.value, "")
    for token in ("300750", "宁德时代", "CATL"):
        if token in residue:
            problems.append(
                f"{token!r} 出现在字符串常量之外（注释 / 标识符 / 其它字面量形态）")
    for value in sorted(sealed):
        if sum(1 for v in values if v == value) > 1:
            problems.append(f"封存 check ID {value!r} 被复制 / 散落多处")
    return problems


def _case_no_case_literals():
    """（12）（17）（18）生产代码不得含公司名 / 表号 / 具体 ID；闭包不得看文字。

    公司字面量按**字面量**逐个核验（不是全文子串扫描），唯一放行的是 §18.14.4 封存
    的 check ID 本身。先证伪扫描器本身（planted 反例必须被判违规），再扫描真实模块。
    """
    # 先证伪：以下写法**必须**被判违规，否则本节的"没有违规"是空的。
    planted = {
        "裸字符串": 'x = "300750"',
        "拼接串": 'x = "300" + "750"',
        "注释": "# 见 300750 的年报表\nx = 1",
        "标识符": "def f300750(): pass",
        "公司名": 'name = "宁德时代"',
        "英文简称": 'code = "CATL"',
        "封存 ID 被改写": 'ids = ("non_300750_positive_fixture_and_legacy_negative_x",)',
        "封存 ID 复制两份": ('ids = ("non_300750_positive_fixture_and_legacy_negative",'
                            ' "non_300750_positive_fixture_and_legacy_negative")'),
    }
    for label, source in planted.items():
        problems = _company_literal_violations(source)
        check(bool(problems),
              f"T41 反例自证失败：planted 写法「{label}」必须被判违规，实际判为合规"
              f"（扫描器被放宽过度）")
    # 再证真：**原样**保留封存 check ID 的写法必须被判合规（否则门会永远关着）。
    benign = ('ids = ("non_300750_positive_fixture_and_legacy_negative",)')
    check(not _company_literal_violations(benign),
          "T41 反例自证失败：原样列出的 §18.14.4 封存 check ID 不得被判违规")

    for name in ("span_builder.py", "span_verifier.py", "span_schema.py",
                 "span_policy.py", "synopsis.py", "versions.py"):
        source = (REPO / "document_structure" / name).read_text(encoding="utf-8")
        problems = _company_literal_violations(source)
        check(not problems,
              f"T41 生产模块 {name} 不得含公司专用字面量（唯一放行项为 §18.14.4 封存 "
              f"check ID）：{problems}")
    missing = [name for name in _CLOSURE_REQUIRED if not callable(getattr(SB, name, None))]
    if not check(not missing,
                 "T41 表格邻接闭包（前导与尾部）必须由 span_builder 的专用函数实现"
                 f"（`_apply_table_leading_closure` / `_apply_table_trailing_closure` "
                 f"及其判定 helper），缺少 {missing}"):
        return
    sources = _closure_reachable_source(*_CLOSURE_REQUIRED)
    check(len(sources) >= len(_CLOSURE_REQUIRED),
          f"T41 静态检查必须覆盖闭包可达的**全部**模块内函数，得到 {sorted(sources)}")
    closure_src = "\n".join(sources[name] for name in sorted(sources))
    for pattern, what in _SOURCE_FORBIDDEN:
        check(re.search(pattern, closure_src) is None,
              f"T41 闭包及其可达函数源码不得含{what}（正则 {pattern}）")
    for pattern, what in _SOURCE_FORBIDDEN_TEXT:
        check(re.search(pattern, closure_src) is None,
              f"T41 闭包及其可达函数不得使用{what}（正则 {pattern}）")
    for name in ("_apply_table_leading_closure", "_apply_table_trailing_closure"):
        check(re.search(r"line\.text", sources.get(name, "")) is None,
              f"T41 {name} 不得读取行文字（判定只允许用几何 / 源序 / 类别）")


# ---------------------------------------------------------------------------
# 5. 正式链集成：非 300750 夹具
# ---------------------------------------------------------------------------

def _case_fixture_chain():
    """（10）（13）（14）夹具正式链：表格范围不产生 span、不进简介、守恒不破。"""
    fixture_root = load_fixture_root()
    root_dir = fixture_root_dir()
    layout = PageLayout.from_dict(json.loads(
        (root_dir / "page_layout.json").read_text(encoding="utf-8")))
    # 本节基线是 **TS4-A 版**夹具产物（含 A 版交接）：进入 TS4-B 之后必须显式把阶段拨回
    # A（`evals.tree_stage_env`，退出即逐字还原），而不是绕过生产代码的阶段门。
    with STAGE.simulated_a_environment():
        handoff = SB._issue_fixture_ts3_handoff(
            raw_pdf=(REPO / fixture_root["source_pdf"]["relpath"]).read_bytes(),
            expected_layout=layout, company_id=fixture_root["company_id"],
            document_id=fixture_root["document_id"], fixture_root=fixture_root)
        snapshot = SB._build_from_pinned_handoff(handoff, stage="distribution_only")
    verified = SV.verify_span_snapshot(snapshot, handoff)
    check(verified.snapshot is snapshot, "T41 夹具正向链必须先通过复核")

    table_disps = [d for d in snapshot.dispositions
                   if d.range_kind in ("table_inside", "table_adjacency")]
    check(bool(table_disps),
          f"T41 夹具必须存在表格范围处置记录（否则本节断言是空的），得到 "
          f"{len(table_disps)}")
    for disp in table_disps:
        check(disp.span_id is None and disp.confidence is None
              and disp.left_boundary_cause is None
              and disp.right_boundary_cause is None,
              f"T41 表格范围处置不得携带 span / confidence / 边界成因：{disp.range_kind}")
    closed_keys: set = set()
    for disp in table_disps:
        for page in layout.pages:
            for line in page.lines:
                key = (page.page_number, line.line_index)
                if (disp.start_page, disp.start_line) <= key \
                        <= (disp.end_page, disp.end_line):
                    closed_keys.add(key)
    check(bool(closed_keys), "T41 表格范围必须覆盖到真实版式上的若干行")
    for span in snapshot.spans:
        clash = sorted(closed_keys & set(span.layout_line_refs))
        check(not clash,
              f"T41 span {span.span_id} 不得引用任何表格范围行，得到 {clash}")
        check(span.role == "body" and not span.is_fallback
              and not span.is_cross_heading,
              "T41 正式 span 必须是普通正文材料")
    for synopsis in snapshot.synopses:
        for snippet in synopsis.snippets:
            span = next((s for s in snapshot.spans
                         if s.span_id == snippet.span_id), None)
            check(span is not None,
                  "T41 简介片段必须引用存在的 span")
            if span is None:
                continue
            clash = sorted(closed_keys & set(span.layout_line_refs))
            check(not clash,
                  f"T41 简介片段不得来自表格范围行，得到 {clash}")
    check(snapshot.conservation.conserved is True
          and not snapshot.conservation.gaps,
          f"T41 夹具守恒必须仍然闭合，得到 "
          f"{[g.gap_id for g in snapshot.conservation.gaps]}")
    _results["fixture_counts"] = {
        "spans": len(snapshot.spans), "dispositions": len(snapshot.dispositions),
        "table_dispositions": len(table_disps),
        "table_adjacency_dispositions": sum(
            1 for d in snapshot.dispositions if d.range_kind == "table_adjacency"),
        "synopses": len(snapshot.synopses),
        "table_lines": len(closed_keys),
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> dict:
    self_check = SB.self_check()
    check(self_check["problems"] == [],
          f"T41 构建层自检必须无问题：{self_check['problems']}")
    _case_caption_unit_body()
    _case_caption_only()
    _case_two_line_caption()
    _case_prose_not_swallowed()
    _case_stop_conditions()
    _case_same_left_leading()
    _case_three_line_leading()
    _case_prose_continuation_not_swallowed()
    _case_prose_wrap_above_region_not_swallowed()
    _case_wide_prose_with_marks()
    _case_degenerate_fragment_transit()
    _case_narrow_region_right_side()
    _case_leading_disposition_and_conservation()
    _case_heading_boundary()
    _case_node_boundary()
    _case_blank_and_formal_unassigned()
    _case_adjacent_without_inside()
    _case_text_after_table()
    _case_trailing_single_line()
    _case_trailing_two_lines()
    _case_trailing_between_tables()
    _case_trailing_heading_boundary()
    _case_trailing_node_boundary()
    _case_trailing_blank_and_formal_unassigned()
    _case_trailing_physical_adjacency()
    _case_trailing_furniture()
    _case_trailing_returns_to_body()
    _case_trailing_conservation()
    _case_trailing_keeps_leading()
    _case_conservation_and_location()
    _case_real_documents()
    _case_no_case_literals()
    _case_fixture_chain()
    return _results


def _report(result: dict, argv: list) -> None:
    """默认只打印失败项与计数（真实文档会话会产生上千条明细）；`--all` 全打。"""
    if "--all" in argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    shown = [d for d in result["details"] if not d.startswith("PASS ")]
    for line in shown:
        print(line)
    payload = {k: v for k, v in result.items() if k != "details"}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    outcome = main()
    _report(outcome, sys.argv[1:])
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

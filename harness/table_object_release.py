"""合格表对象的**版本化放行**（tobj-1）：把「树/图谱定位到的表」变成可读材料。

背景（本模块存在的唯一理由）
--------------------------------------------------------------------------
树的材料人口 (`TreeMaterialPayloadResolver` / `inspect_outline_materials`) 只收 ``body``
正文 span，表内内容天然不进人口（``harness/topic_runtime.py`` 的
``tree_table_material_unavailable``）。真实纵链里因此出现一个系统性缺口：**营业收入构成、
营业成本构成、毛利率这类「只以表格存在」的栏目永远拿不到材料**，即使同一块原文里就有
一张结构完整的表。

本模块补的**不是**「重新解析 PDF」，而是「在已经定位到的块原文上，把既有的表结构资格
判据按真实排版本形补齐，产出一个可审计、可定位、只作**阅读材料**的合格表对象」。它不
改写 ``harness/table_structure.py`` 里那套 ``REFERENCE_TARGET_* = "3"`` 的 R2 冻结线上
语义，而是**平行**给出一条带版本号的放行通道（``tobj-1``）。

三类真实版式缺陷（在真实三份文档上逐条复现过）
--------------------------------------------------------------------------
F1 **竖跨首列**：真实年报的表格首列（``项目`` / ``行业分类`` / ``公司名称``）是**跨行合并**
   的行标识列；父表头层带这一列、子列层只覆盖其后各列。于是「表体列数 = 最宽表头层列数
   + 1」是**合法**结构，而不是「列结构无法解释」。旧判据直接拿最宽层与表体比，把这类
   表整批拒掉（真实现场：``头3列/体6列``、``头5列/体6列``、``头4列/体7列``、
   ``头6列/体7列``）。
F2 **折行表头片段**：PDF 抽取会把一行的表头标签切成两行（``营业收入比上`` + ``年同期增减``；
   ``毛利`` + ``率``）。片段行「全是短标签」且「下方确有真实数据行」，旧判据把它当成
   **第二层表头**或**首个数据行**，两种误判都会污染表头或吃掉表体。
F3 **子列层比父层更宽**：分组父列（``2025年 2024年度 2023年度``）配有更宽的子列层
   （``项目 金额 占比 毛利 …``）时，旧判据第 3 条要求「下方存在更宽的结构行」，方向正好
   相反 —— 该表体比子列层**更窄**，于是子列层被降级成首个数据行，表体随之被拒。
F4 **表题与表体跨 Evidence 块**：``表 5-11发行人主营业务成本构成表`` 这样的表题落在块末，
   表体在下一块。旧路径单块求解，得到一个「结构行=1」的空候选。跨块判据必须按**块末截断**
   来判（区域末行之后到块末只剩空行/页码行），不能按「区域末行 == 块末行」—— 真实 PDF 在
   块末带页码行（2024 年报 p19 ``1）营业收入及营业成本整体情况`` 的表头在本块、表体在 p20）。
F5 **跨块对象被包装成单块来源**（`tobj-1` 的真实缺陷，`tobj-2` 修掉）：`tobj-1` 的跨块对象
   只声明**一个** ``evidence_block_id``，却把 ``char_range[1]`` 写成**拼接后**文本的偏移。
   于是在「宿主块坐标」下它声明了一段**不存在**的字符。三份真实文档上实测 **15/15 全部
   越界**（2024 年报 4 个、2025 年报 5 个、募集说明书 6 个，越界 50–680 字）：这不是边缘
   情形，是跨块通道**全体**如此。`tobj-2` 的修法是**逐来源块**声明精确区间
   （``target_source_spans``，每块一条、按该块自身长度截断），locator 只描述**宿主块**，
   跨块对象另带 ``source_blocks``，出现位置身份含**全部**来源块。构造上无法再越界；
   来源无法按块表达时落 typed 拒发 ``source_span_unresolved``，不退回「像单块」的残段。

放行的**四条**硬条件（缺一不放行，全部结构性、零公司/页码/关键词）
--------------------------------------------------------------------------
G1 结构区非空且起点可验证（表题行 / 首行即列行）；通用表题（无 ``表 N``）仍须
   :func:`harness.table_structure.generic_structure_sufficient` 证明结构充分。
G2 表头区存在且可复核：至少一层「全标签」的物理表头行（标签 vs 数字是唯一的角色判据）。
G3 表体是**数字表**：含数字单元的多列表体行占全部多列表体行的比例 ≥
   ``_NUMERIC_ROW_MIN_SHARE``，**且**这样的行至少 ``_MIN_NUMERIC_BODY_ROWS`` 条（一行
   不成表，是残句），数字单元总数 ≥ ``_MIN_NUMERIC_CELLS``。释义表、任职表这类全标签表
   **不**走本通道（放行它们只会给 Writer 一堆无语义的结构），但仍产出 typed refusal 记录，
   绝不静默丢弃。
G4 表体**列宽不散**：数字表体行的列数极差 ≤ ``_MAX_COLUMN_SPREAD``。真实 PDF 会把若干列
   数字挤进同一个空白分隔的单元（``7,544,197.2`` ``67.8`` 挤成一格），列数因此**天然**
   逐行抖动（真实现场：KCZ 表5-12 的体行列数是 3/3/6/6/4 —— 极差 3，但它每行都是同一组
   9 个数字）。所以这里只能给一个**有界**的粘连余量，绝不用「列数众数」把这种真实表判死
   （那正是 tok-1 首版踩过的坑）。「多张表被吞进同一结构区」由 G3 的**数字行占比**挡
   （被吞进来的第二张表在多列行里是非数字行），「折行散文里恰好有几个数字」由 G3 的
   **行数下限 + 占比**挡。

列数关系（G2 × G4 之间）按 ``_lead_column_evidence`` 给 0/1 的竖跨首列余量，并且
**仅在表头区含折行片段行时**额外给「片段行自身的单元数」作为上界（片段确实携带表头单元，
只是落点结构上二义，故只能作**上界**、且该对象一律 ``partial``）：

``max(数字表体行列数) <= widest_header_layer_cols + lead_span + wrap_cells``

已知的**有界残余风险**（如实记录，不靠放大判据掩盖）：若同一个结构区里恰好并排着**两张
列宽相近、且都含数字**的表，G4 的极差判据可能让它们合并成一个对象。缓解手段是审计面而非
放宽面 —— 对象逐字携带全部行、``target_structure_rows`` 与精确字符区间都落盘，审阅者与
Writer 都能看到边界；且对象**不带任何数字权威**（见下）。

竖跨首列余量**不是**无条件放宽 —— 它只在「存在两层表头且两层首格互不相同且其中一个是
非数字标签」时成立（即竖跨列被真实观察到）。

区内的**非数字多列行**（勾选项残句、表后残段）与**非多列行**（``分行业``/``分产品``
这类整行分组合并标签）都不进表体身份，而是逐字落在 ``target_residue_lines`` /
``target_other_region_lines`` 里作**上下文**：它们可以被人读、被 Writer 读，但既不是表体
行，也不构成任何事实或数字权威 —— 这正是「不得把勾选项或表后残句当事实」在表对象里的
落点。

读取材料身份 ≠ 数字权威（硬边界）
--------------------------------------------------------------------------
放行对象的 payload 只承载：定位（locator）、**原文逐字**表头行/单位行/表体行/合计行、
结构状态、放行规则版本、身份摘要。它**显式声明** ``financial_authority_claimed: False`` /
``numeric_authority: False``，并且**不出现**任何金额字段名。需要计算或引用的数字仍必须走
``FinancialSnapshot`` / ``FinancialFactPack`` 与 Python/Decimal 结构校验；本模块的
``column_count`` / ``numeric_cell_count`` 只是**可读材料的形状**，不是可引用的数。

表头一律**逐字保留、不做位置重建**：折行片段行被识别并标注（``header_wrap_lines``），但
绝不猜它在逻辑表头里的落点 —— 真实抽取里同一批片段既可能是「尾部延伸」
（``营业收入比上`` + ``年同期增减 ×3``），也可能是「等分续行」（``毛利`` + ``率`` 每隔三列
一次），结构上二义。因此含折行片段的表对象标 ``structure_state = partial``，而**不含**
折行片段的标 ``complete``；两者都可作阅读材料，都不带数字权威。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

from . import table_structure as TBL

# 放行规则版本 / payload schema 版本（两者同步升版；旧版本记录一律 fail-closed）。
#
# `tobj-1` → `tobj-2`（2026-09-28）：**来源真实性**。`tobj-1` 的跨块对象只声明一个
# `evidence_block_id`，却把 `char_range[1]` 写成**拼接后**文本的偏移，于是「跨块读出来的
# 表」被包装成「单块来源」。真实三份文档上实测：**全部 15 个跨块对象 100% 越界**
# （2024 年报 4 个 / 2025 年报 5 个 / 募集说明书 6 个，越界 50–680 字）。`tobj-2` 改为
# **逐来源块**声明精确区间（`target_source_spans`），locator 只描述**宿主块**，跨块对象的
# 身份含全部来源块。旧 `tobj-1` 记录一律 fail-closed（版本比对不通过）。
RELEASE_RULE_VERSION = "tobj-2"
RELEASE_SCHEMA_VERSION = "tobj-2"

# 来源块状态（**读法**的一部分，进 payload 哈希）：单块 = 表完全在一个 Evidence 块内；
# 多块 = 表题/表头在本块、表体在紧邻的下一块（F4 续表，有界一块）。多块对象**不是**
# 单块来源，消费方必须看得到这一条，不得按单块材料理解。
SOURCE_SPAN_STATE_SINGLE = "single_block"
SOURCE_SPAN_STATE_MULTI = "multi_block"

# 结构状态：complete = 表头区无折行片段，列结构可解释到形状级；partial = 表头区含折行片段
# （逐字保留但**不做**位置重建）。两者都只是「可读材料的形状」，不是权威等级。
STRUCTURE_STATE_COMPLETE = "complete"
STRUCTURE_STATE_PARTIAL = "partial"

# 放行 / 拒绝理由码（闭集；拒绝也要留 typed 记录，绝不静默丢弃）。
RELEASE_REASON_RELEASED = "released"
RELEASE_REASON_NO_STRUCTURE_START = "no_structure_start"
RELEASE_REASON_EMPTY_REGION = "empty_structure_region"
RELEASE_REASON_INSUFFICIENT_GENERIC = "insufficient_generic_structure"
RELEASE_REASON_NO_HEADER = "no_physical_header"
RELEASE_REASON_NO_DATA_ROW = "no_body_row"
RELEASE_REASON_BODY_ROW_FLOOR = "body_row_count_below_floor"
RELEASE_REASON_BODY_INCONSISTENT = "body_numeric_structure_inconsistent"
RELEASE_REASON_BODY_WIDER = "body_wider_than_header_and_lead_column"
RELEASE_REASON_NOT_NUMERIC = "no_numeric_body_row"
# 来源区间无法按**逐块**精确表达（`tobj-2` 新增）：宁可保守拒发，也不把跨块文本
# 包装成单块来源。这是 fail-closed 的兜底，不是「表不合格」。
RELEASE_REASON_SOURCE_SPAN_UNRESOLVED = "source_span_unresolved"

RELEASE_REASONS = (
    RELEASE_REASON_RELEASED,
    RELEASE_REASON_NO_STRUCTURE_START,
    RELEASE_REASON_EMPTY_REGION,
    RELEASE_REASON_INSUFFICIENT_GENERIC,
    RELEASE_REASON_NO_HEADER,
    RELEASE_REASON_NO_DATA_ROW,
    RELEASE_REASON_BODY_ROW_FLOOR,
    RELEASE_REASON_BODY_INCONSISTENT,
    RELEASE_REASON_BODY_WIDER,
    RELEASE_REASON_NOT_NUMERIC,
    RELEASE_REASON_SOURCE_SPAN_UNRESOLVED,
)

# 表题形态（**事实记录**，不是权威等级）：显式表题 / 首行即多列表头 / 首行是标签行 /
# 退化表题（单位行、勾选残句、页码碎片、期间或金额碎片）。消费方据此区分
# 「这是一张有名字的表」与「这是一张可读但没有可引用表题的残段」——后者仍可作阅读材料
# （逐字保留、无数字权威），但**不得**被当成某个栏目的具名表材料。
TITLE_KIND_EXPLICIT = "explicit_table_title"
TITLE_KIND_HEADER_LINE = "header_line_as_title"
TITLE_KIND_LABEL_LINE = "label_line_as_title"
TITLE_KIND_DEGRADED = "degraded_title"

TITLE_KINDS = (TITLE_KIND_EXPLICIT, TITLE_KIND_HEADER_LINE,
               TITLE_KIND_LABEL_LINE, TITLE_KIND_DEGRADED)

# 勾选残句（表单控件符号 / 「适用 □不适用」形态）—— 通用判据，不绑定任何具体措辞。
_SELECTION_FORM_MARKERS = ("□", "☑", "☒", "√")
_SELECTION_FORM_RE = re.compile(r"适用\s*不适用")

# 期间/日期碎片（``2024年`` / ``17日`` / ``5202年12月 31日``）：去掉数字与日期单位后不剩
# 任何标签字 —— 这类首行不构成可复核表题（通用判据，不绑定任何具体年份/页码）。
_DATE_FRAGMENT_RE = re.compile(r"^[\d\s,\.\-—–/年月日期末]+$")

# 表头层数上限（父列层 + 子列层）；折行片段行不占层。
_MAX_HEADER_LAYERS = 2
# 折行片段行的单元最长字符数（真实片段：``年同期增减`` / ``率`` / ``本比重``）。
_WRAP_FRAGMENT_MAX_CHARS = 6
# 表体**数字结构**判据：含数字表体行占全部多列表体行的最低占比；含数字表体行的条数下限
# （一行不成表，是残句）；数字表体行列数的最大极差（PDF 粘连的有界余量）。
_NUMERIC_ROW_MIN_SHARE = 0.5
_MIN_NUMERIC_BODY_ROWS = 2
_MAX_COLUMN_SPREAD = 3
# 数字单元总数下限（单个数字的表不是「数字表」，多半是误切的行）。
_MIN_NUMERIC_CELLS = 2


# ---------------------------------------------------------------------------
# 行/单元格几何（**全部**复用 ``harness.table_structure`` 的原语，绝不另写一套口径）
# ---------------------------------------------------------------------------


def _cells(s: str) -> list[str]:
    """保列规范单元格（与 ``table_object_id`` 同一口径）。"""
    return TBL.canonical_cells(s)


def _ncols(s: str) -> int:
    return len(_cells(s))


def _all_label(cells: Sequence[str]) -> bool:
    """全部是标签单元（不含数字/金额/比例/期间值）—— 表头角色的唯一判据。"""
    return all(not TBL.is_numeric_cell(c) for c in cells)


def _is_real_data_row(s: str) -> bool:
    """真实数据行：多列、非合计、且至少含一个数字单元。"""
    if not TBL.is_columnar_row(s) or TBL.is_closure_row(s):
        return False
    return any(TBL.is_numeric_cell(c) for c in _cells(s))


def _has_real_data_row_below(rows: Sequence[str]) -> bool:
    return any(_is_real_data_row(t) for t in rows)


def _has_wider_columnar_row(cells: Sequence[str], rows: Sequence[str]) -> bool:
    width = len(cells)
    return any(_ncols(t) > width for t in rows if TBL.is_columnar_row(t))


# ---------------------------------------------------------------------------
# 表头层裁定（tobj-1）
# ---------------------------------------------------------------------------


def _is_wrap_fragment_line(prev_layer: str, cand: str,
                          remaining: Sequence[str]) -> bool:
    """``cand`` 是否为 ``prev_layer`` 的**折行片段行**（F2）。

    结构性判据（四条同时成立；任一条不成立 ⇒ 不是片段行）：

    1. 片段行本身是多列行，且列数**严格少于**上一表头层（片段是上一层的**一部分**；
       等宽/更宽的行不可能是一行的折行产物）；
    2. 片段行的**每个**单元都是短标签片段（≤ ``_WRAP_FRAGMENT_MAX_CHARS`` 字、非数字、
       非合计标记）—— 折行产物总是被截断的标签残段；
    3. 上一层被覆盖的**尾部各单元**同样全是非数字标签（片段只可能延伸标签，不可能延伸数字）；
    4. 片段行**下方确有真实数据行** —— 即它仍在表头区里，没有被读到表体里去。

    真假反例（回归测试钉住）：``毛利`` + ``率 率 率`` 是片段行；``甲有限公司 北京市 5000万``
    这类**等宽**首个数据行不是（条件 1 挡掉）；``是 否 否`` 这类无数字的窄行若下方没有
    真实数据行也不是（条件 4 挡掉）。

    **只识别、不重建**：片段的逻辑落点在结构上是二义的（尾部延伸 vs 等分续行，见模块
    docstring），因此调用方只标注 + 降级为 ``partial``，绝不猜位置。
    """
    if not TBL.is_columnar_row(cand):
        return False
    cand_cells = _cells(cand)
    prev_cells = _cells(prev_layer)
    k = len(cand_cells)
    if k < 2 or k >= len(prev_cells):
        return False
    if not _all_label(cand_cells):
        return False
    if any(len(c) > _WRAP_FRAGMENT_MAX_CHARS for c in cand_cells):
        return False
    if any(TBL.is_closure_row(c) for c in cand_cells):
        return False
    if not _all_label(prev_cells[-k:]):
        return False
    return _has_real_data_row_below(remaining)


def _is_subcolumn_layer(prev_layer: str, cand: str,
                        remaining: Sequence[str]) -> bool:
    """``cand`` 是否为 ``prev_layer`` 的**第二层物理表头（子列层）**（F3 修正版）。

    与冻结原语 ``table_structure.second_header_layer_cells`` 的差别**只有第 3 条**：

    - 冻结晶口径：下方存在**比子列层更宽**的结构行；
    - 本口径：下方存在更宽的结构行 **或** 存在一条**真实数据行**（多列、非合计、含数字）。

    为什么必须放宽：分组父列（``2025年 2024年度 2023年度``，3 列）配更宽的子列层
    （``项目 金额 占比 毛利 …``，10 列）时，表体（10 列）**不比子列层更宽**，冻结晶口径
    因此把子列层降级成首个数据行，整张表被「表体比表头宽」拒掉。而第 2 条（子列层**全为
    标签单元**）才是「表头 vs 首个数据行」的真正判据：任何含数字的行都会在第 2 条被挡掉，
    所以放宽第 3 条**不会**把首个数据行吞成表头；第 3 条真正防的是「全标签的首个数据行」
    （如 ``甲公司 北京``）—— 那种表下方**没有**数字行，两个口径都会挡住它。
    """
    if not (TBL.is_columnar_row(prev_layer) and TBL.is_columnar_row(cand)):
        return False
    cand_cells = _cells(cand)
    if len(cand_cells) < 2:
        return False
    if not _all_label(cand_cells):
        return False
    if _has_wider_columnar_row(cand_cells, remaining):
        return True
    return _has_real_data_row_below(remaining)


def assign_header_layers(rows: Sequence[str]) -> tuple[list[str], list[str], list[str], str]:
    """把结构行切成 ``(表头层, 折行片段行, 其余行, 裁定码)``。

    首行是**多列行**时必为第一层表头；其后每行按**结构判据**归入三类之一：

    - 折行片段行（``_is_wrap_fragment_line``）→ 逐字保留在 ``wrap_lines``，**不进** 表头层，
      也**不进** 表体（它不是数据行）；
    - 第二层表头（``_is_subcolumn_layer``）→ 进 ``layers``（最多 ``_MAX_HEADER_LAYERS`` 层）；
    - 其余 → 表体（首个真实数据行起，后续行一律归表体，绝不再回吞为表头）。

    首行不是多列行 ⇒ 无物理表头（``HEADER_DECISION_NONE``）。**首行还必须全部是标签单元**
    —— G2 的「标签 vs 数字」是表头角色的唯一判据：起点行自身带数字（真实现场：
    ``电池矿产资源  5,978,096  1.41%  5,493,003  1.52%  8.83%`` 这样的**表体中间行**被
    ``table_start_indices`` 当成结构起点）说明这个起点**不是表的开头**，它没有可复核的
    物理表头，按 ``HEADER_DECISION_NONE`` 拒绝 —— 绝不拿一行数据冒充表头。裁定码直接用
    ``table_structure`` 的四个码（``single_layer_header`` / ``second_layer_subcolumn_header``
    / ``second_row_is_first_body_row`` / ``no_physical_header_row``）并补一个
    ``header_wrap_fragment_present`` —— 语义与冻结侧同名码**不重叠**（本函数是版本化新
    通道，不参与 R2 线上绑定）。
    """
    r = [row for row in (rows or ())]
    if not r or not TBL.is_columnar_row(r[0]):
        return [], [], r, TBL.HEADER_DECISION_NONE
    if not _all_label(_cells(r[0])):
        return [], [], r, TBL.HEADER_DECISION_NONE
    layers = [r.pop(0)]
    wraps: list[str] = []
    decision = TBL.HEADER_DECISION_SINGLE
    while r:
        if _is_wrap_fragment_line(layers[-1], r[0], r[1:]):
            wraps.append(r.pop(0))
            decision = "header_wrap_fragment_present"
            continue
        if len(layers) >= _MAX_HEADER_LAYERS:
            break
        if not _is_subcolumn_layer(layers[-1], r[0], r[1:]):
            break
        layers.append(r.pop(0))
        decision = TBL.HEADER_DECISION_SUBCOLUMN
    return layers, wraps, r, decision


def _lead_column_evidence(layers: Sequence[str]) -> bool:
    """是否观察到**竖跨首列**（F1）：两层表头、首格互不相同、且其中至少一个是非数字标签。

    真实年报里 ``项目`` / ``行业分类`` 是跨行的行标识列：父层带它、子层不带，于是表体比
    最宽表头层多一列。这个余量**不是**无条件放宽 —— 它只在竖跨列被真实观察到（两层首格
    确实不同）时成立；单层表头一律不给余量。
    """
    if len(layers) < 2:
        return False
    widest = max(layers, key=_ncols)
    widest_first = _cells(widest)[0] if _cells(widest) else ""
    for layer in layers:
        if layer is widest:
            continue
        cells = _cells(layer)
        if len(cells) < 2:
            continue
        if cells[0] != widest_first and not TBL.is_numeric_cell(cells[0]):
            return True
    return False


# ---------------------------------------------------------------------------
# 表体结构（G3 / G4）
# ---------------------------------------------------------------------------


def body_shape_verdict(region_rows: Sequence[str]) -> tuple[str, dict, dict]:
    """表体裁定：``(理由码, 证据, 切分)``。

    输入是结构区里**表头之后**的全部行（含非多列的分组标签行、勾选残句），输出把行按
    **确定性结构**切成三份：

    - ``body_rows``：含数字的多列非合计行 —— 表体身份**只认这一份**；
    - ``residue_rows``：多列、非合计、但**无数字**的行（勾选项残句、表后残段）；
    - ``other_rows``：非多列行（``分行业``/``分产品`` 这类整行分组合并标签、单位行残段）。

    后两份逐字保留作上下文，**不**进表体身份、不构成事实。判据（全部结构性）：

    - 表体行至少 ``_MIN_NUMERIC_BODY_ROWS`` 条（一行不成表，是残句）；
    - 表体行占全部多列非合计行的比例 ≥ ``_NUMERIC_ROW_MIN_SHARE``
      —— 这一条挡「多张表被吞进同一结构区」（被吞的第二张表在多列行里是非数字行）与
      「折行散文里恰好有几个数字」（占比被摊薄）；
    - 表体行列数极差 ≤ ``_MAX_COLUMN_SPREAD`` —— 只给**有界**的 PDF 粘连余量，绝不用
      列数众数把真实表判死（见模块 docstring G4 的说明与已知残余风险）。
    """
    columnar = [t for t in region_rows if TBL.is_columnar_row(t)
                and not TBL.is_closure_row(t)]
    body_rows = [t for t in columnar if any(TBL.is_numeric_cell(c) for c in _cells(t))]
    residue_rows = [t for t in columnar if t not in body_rows]
    other_rows = [t for t in region_rows
                  if t not in columnar and not TBL.is_unit_line(t)]
    body_cells = [_cells(t) for t in body_rows]
    widths = [len(c) for c in body_cells]
    evidence = {
        "columnar_row_count": len(columnar),
        "body_row_count": len(body_rows),
        "residue_row_count": len(residue_rows),
        "other_row_count": len(other_rows),
        "body_column_counts": widths,
        "body_column_spread": (max(widths) - min(widths)) if widths else 0,
        "body_numeric_counts": [sum(1 for c in row if TBL.is_numeric_cell(c))
                                for row in body_cells],
        "numeric_cell_count": sum(1 for row in body_cells
                                  for c in row if TBL.is_numeric_cell(c)),
    }
    split = {"body_rows": body_rows, "residue_rows": residue_rows,
             "other_rows": other_rows}
    if not columnar:
        # 一条多列表体行都没有：表题在块末／表体在下一块 —— 唯一该走续表读下一块的情形。
        return RELEASE_REASON_NO_DATA_ROW, evidence, split
    if not body_rows:
        # 有表体形状、但一条数字行都没有：释义表/任职表这类全标签表。**不是**续表情形，
        # 而是「本通道只放行数字表」的如实拒绝。
        return RELEASE_REASON_NOT_NUMERIC, evidence, split
    if len(body_rows) < _MIN_NUMERIC_BODY_ROWS:
        return RELEASE_REASON_BODY_ROW_FLOOR, evidence, split
    if len(body_rows) / len(columnar) < _NUMERIC_ROW_MIN_SHARE:
        return RELEASE_REASON_NOT_NUMERIC, evidence, split
    if evidence["numeric_cell_count"] < _MIN_NUMERIC_CELLS:
        return RELEASE_REASON_NOT_NUMERIC, evidence, split
    if evidence["body_column_spread"] > _MAX_COLUMN_SPREAD:
        return RELEASE_REASON_BODY_INCONSISTENT, evidence, split
    return RELEASE_REASON_RELEASED, evidence, split


# ---------------------------------------------------------------------------
# 身份（两条正交身份：内容身份 vs 出现位置身份）
# ---------------------------------------------------------------------------


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def table_content_payload(*, title: str, unit: str = "",
                          header_lines: Sequence[str] = (),
                          wrap_lines: Sequence[str] = (),
                          body_row_texts: Sequence[str] = (),
                          closure_row: str = "") -> dict:
    """表对象的**内容身份** canonical payload（保列规范形，见 ``canonical_cells``）。

    与冻结侧 ``table_object_payload`` 同口径但**不同 schema 版本**（``tobj-1``）：
    两者身份**不得混用**（``R2`` 线上绑定对象与本通道放行对象是不同身份）。
    """
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "title": TBL._collapse(title or ""),
        "unit": TBL._collapse(unit or ""),
        "header_lines": [_cells(h) for h in (header_lines or ())],
        "wrap_lines": [_cells(h) for h in (wrap_lines or ())],
        "body_rows": [_cells(r) for r in (body_row_texts or ())],
        "closure_row": _cells(closure_row or ""),
    }


def table_content_id(*, title: str, unit: str = "",
                     header_lines: Sequence[str] = (),
                     wrap_lines: Sequence[str] = (),
                     body_row_texts: Sequence[str] = (),
                     closure_row: str = "") -> str:
    """内容寻址身份：同一张表在两份文档/两处出现时**同**此 ID。"""
    return _digest(table_content_payload(
        title=title, unit=unit, header_lines=header_lines, wrap_lines=wrap_lines,
        body_row_texts=body_row_texts, closure_row=closure_row))


def locator_source_blocks(source_locator: Mapping) -> list[dict]:
    """locator 的**来源块轴**（有序）：跨块对象带 ``source_blocks``，单块对象退回单条。

    单块对象刻意**不**写 ``source_blocks`` 键：这样它的 locator 与 `tobj-1` 形态逐字同形
    （只有 ``evidence_block_id`` + ``char_range``），审计面上「单块来源」与「多块来源」
    一眼可分，不需要读区间长度去猜。
    """
    blocks = source_locator.get("source_blocks")
    if blocks:
        return [{"evidence_block_id": str(b.get("evidence_block_id") or ""),
                 "char_range": [int((b.get("char_range") or (0, 0))[0]),
                                int((b.get("char_range") or (0, 0))[1])]}
                for b in blocks]
    return [{"evidence_block_id": str(source_locator.get("evidence_block_id") or ""),
             "char_range": [int(source_locator.get("char_range", (0, 0))[0]),
                            int(source_locator.get("char_range", (0, 0))[1])]}]


def released_object_id(*, content_id: str, source_locator: dict) -> str:
    """**出现位置**身份：内容身份 + locator 的对象轴（文档 / **全部来源块** / 字符区间）。

    与内容身份**正交**：内容相同、出现位置不同 ⇒ 内容身份相同而对象身份不同（同一张表
    在两处出现是可审计的两条记录，不是覆盖）。locator 的**页码不进身份** —— 页码只是
    阅读坐标，绝不成为规则的一部分。

    `tobj-2`：跨块对象的身份含**每一个**来源块的 id 与精确区间，不再只含宿主块 ——
    否则「同一段文本被切成不同的块」会得到同一个身份，而它们**不是**同一次出现。
    """
    return _digest({
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_rule_version": RELEASE_RULE_VERSION,
        "content_id": content_id,
        "document_id": str(source_locator.get("document_id") or ""),
        "source_blocks": locator_source_blocks(source_locator),
    })


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SourcePart:
    """一个来源 Evidence 块在**拼接原文**里的一段。

    ``start`` 是该块首字符在拼接原文里的全局偏移，``length`` 是**该块自身**的文本长度
    （不含块间分隔符）。逐块声明精确区间的全部几何都由这三个数推出来，因此「某个区间
    越出了它所属的块」在构造上就不可能发生（`_source_spans` 一律按 ``length`` 截断）。
    """

    index: int
    block_id: str
    text: str
    start: int
    length: int


def _measure_raw(raw: str) -> tuple[list[str], list[int], list[int]]:
    """``raw``（**已经** NFC 规范化过的拼接原文）→ ``(规范化行, 行起偏移, 行末偏移)``。"""
    norm: list[str] = []
    offsets: list[int] = []
    ends: list[int] = []
    pos = 0
    for line in raw.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        norm.append(TBL.normalize_line(body))
        offsets.append(pos)
        ends.append(pos + len(body))
        pos += len(line)
    if pos < len(raw):
        body = raw[pos:]
        norm.append(TBL.normalize_line(body))
        offsets.append(pos)
        ends.append(pos + len(body))
    return norm, offsets, ends


def _measure(text: str) -> tuple[list[str], list[int], list[int]]:
    """单段文本的 ``(规范化行, 行起偏移, 行末偏移)``（与冻结侧同口径）。

    与 :func:`_measure_raw` 的差别只在「先做 NFC 规范化」。按块求解一律走
    :func:`_source_parts` ＋ :func:`_measure_raw`（拼接原文只规范化**一次**，否则块的
    全局起点会算错）。
    """
    return _measure_raw(unicodedata.normalize("NFC", text or ""))


def _source_parts(sources: Sequence[tuple[str, str]]) -> list[_SourcePart]:
    """``[(块 id, 块原文)]`` → 拼接几何（块序即给定顺序）。

    分隔符固定为一个 ``"\\n"``。NFC 规范化与拼接**可交换**在这里成立：分隔符是换行，
    不可能与任何组合字符成对，所以 ``NFC(a + "\\n" + b) == NFC(a) + "\\n" + NFC(b)``，
    每块的全局起点因此可以精确复算 —— 这是「逐块区间」可信的前提。
    """
    parts: list[_SourcePart] = []
    pos = 0
    for index, (block_id, text) in enumerate(sources):
        norm_text = unicodedata.normalize("NFC", text or "")
        parts.append(_SourcePart(index=index, block_id=str(block_id or ""),
                                 text=norm_text, start=pos, length=len(norm_text)))
        pos += len(norm_text) + 1
    return parts


def _joined_raw(parts: Sequence[_SourcePart]) -> str:
    return "\n".join(p.text for p in parts)


def _owners(offsets: Sequence[int], parts: Sequence[_SourcePart]) -> list[int]:
    """逐行归属的**来源块序号**（行的起偏移落在哪个块里）。

    块间那个分隔换行行归属**前一块**（它的起点恰好等于前一块的末界）；归属只用于
    把区间切回各块，越界由 ``_source_spans`` 的截断兜底，不依赖这里的边界解释。
    """
    owner: list[int] = []
    for off in offsets:
        index = 0
        for part in parts:
            if part.start <= off:
                index = part.index
            else:
                break
        owner.append(index)
    return owner


def _source_spans(region_idx: Sequence[int], offsets: Sequence[int],
                  ends: Sequence[int], parts: Sequence[_SourcePart],
                  owner: Sequence[int]) -> list[dict]:
    """结构区 → **逐来源块**的精确区间（按块序，无则省略该块）。

    每块的区间 = 该块内被区域覆盖的行的 ``[min 行起, max 行末]``，并**按该块自身长度截断**
    —— 越界在构造上不可能。``block_text_length`` 一并落盘，使审计者能一眼看出这段区间
    在该块的什么位置、是否触到块末。
    """
    spans: list[dict] = []
    for part in parts:
        idxs = [i for i in region_idx if 0 <= i < len(owner) and owner[i] == part.index]
        if not idxs:
            continue
        lo = min(int(offsets[i]) for i in idxs) - part.start
        hi = max(int(ends[i]) for i in idxs) - part.start
        lo = max(0, min(lo, part.length))
        hi = max(lo, min(hi, part.length))
        spans.append({
            "evidence_block_id": part.block_id,
            "char_range": [int(lo), int(hi)],
            "block_text_length": int(part.length),
        })
    return spans


def _host_local_range(obj: Mapping, host_block_id: str) -> list[int]:
    """对象在**宿主块**坐标下的精确区间（跨块对象的 locator 只描述宿主块那一段）。

    宿主块 = 区域起点所在的块。声明里没有该块 ⇒ ``[0, 0]``（调用方据
    ``target_source_spans`` 的非空性判 fail-closed，不在这里猜）。
    """
    for span in (obj.get("target_source_spans") or ()):
        if str(span.get("evidence_block_id") or "") == str(host_block_id or ""):
            rng = span.get("char_range") or (0, 0)
            return [int(rng[0]), int(rng[1])]
    return [0, 0]


def _candidate_starts(norm: Sequence[str], offsets: Sequence[int],
                      from_offset: int) -> list[int]:
    """结构起点候选（升序）：显式 ``表 N`` / 结构表题形态 + 表题缺失时的块首结构行回退。"""
    cand = [i for i in TBL.table_start_indices(norm) if offsets[i] >= from_offset]
    lead = TBL._line_index(norm, offsets, from_offset)
    if lead is not None and lead not in cand:
        cand.append(lead)
        cand.sort()
    return cand


# 页码行（**通用**判据，不绑定任何固定页码）：纯数字 / ``第 N 页`` / ``- N -`` 形态。
_PAGE_NUMBER_LINE_RE = re.compile(
    r"^(?:第\s*\d{1,4}\s*页(?:共\s*\d{1,4}\s*页)?|[-—–]\s*\d{1,4}\s*[-—–]|\d{1,4})$")


def _is_page_number_line(line: str) -> bool:
    """空行或**页码行**：结构上不携带表内容，不构成结构区的边界证据。"""
    t = (line or "").strip()
    if not t:
        return True
    return bool(_PAGE_NUMBER_LINE_RE.match(t))


def _is_selection_form_line(line: str) -> bool:
    """勾选残句（``适用 □不适用`` / ``▇是 □否``）—— 表后或块首的表单控件行。"""
    t = line or ""
    if any(m in t for m in _SELECTION_FORM_MARKERS):
        return True
    return bool(_SELECTION_FORM_RE.search(t))


def _is_date_fragment_line(line: str) -> bool:
    """期间/日期碎片行（``2024年`` / ``17日`` / ``5202年12月 31日``）。"""
    t = (line or "").strip()
    return bool(t) and bool(_DATE_FRAGMENT_RE.match(t))


def _title_kind(title: str) -> str:
    """表题形态（见 ``TITLE_KIND_*``）。判据全部结构性、零公司/页码/关键词。"""
    t = title or ""
    if TBL.is_explicit_table_title(t):
        return TITLE_KIND_EXPLICIT
    if (TBL.is_unit_line(t) or _is_page_number_line(t)
            or _is_selection_form_line(t) or _is_date_fragment_line(t)):
        return TITLE_KIND_DEGRADED
    cells = _cells(t)
    if TBL.is_columnar_row(t) and len(cells) >= 2:
        return (TITLE_KIND_HEADER_LINE if _all_label(cells)
                else TITLE_KIND_DEGRADED)
    # 单格（或解析不出多列）的首行：仍是**标签**首行时可作弱表题（``教育程度``）；
    # 期间/金额碎片（``2024年`` / ``,496,520 日-2023``）如实降级。
    if any(TBL.is_numeric_cell(c) for c in (cells or [t])):
        return TITLE_KIND_DEGRADED
    return TITLE_KIND_LABEL_LINE


def _region_reaches_block_end(norm: Sequence[str], region_idx: Sequence[int],
                              start: int, terminator: str,
                              host_line_count: int) -> bool:
    """结构区是否被**块末截断**（= 本块已无结构行可读，表可能续到下一块）。

    两条同时成立：终止边界是 ``block_end``（而非正文行 / 另一张表题 / 续表标记），且区域
    最后一行之后到块末**只剩空行或页码行**。真实年报在块末总带页码行（真实现场：2024 年报
    p19 的 ``1）营业收入及营业成本整体情况`` 表头在本块、表体在 p20，区域末行之后正是 ``''``
    与 ``19``）；若把「区域末行 == 块末行」当唯一判据，这类续表永远接不上下一块的表体，
    表就只剩一层表头而被拒。反之，终止边界若是正文行/另一张表题，说明表在本块内已经读完，
    **不接**下一块（否则会把下一张表吞成这张表的表体）。
    """
    if terminator != "block_end":
        return False
    last = region_idx[-1] if region_idx else start
    return all(_is_page_number_line(norm[i])
               for i in range(last + 1, host_line_count))


def _has_body_row_in_next_block(res: dict, next_block_text: str) -> bool:
    """放行对象的表体里**确有**下一块的行 —— 跨块不是「接上就放行」的许可证。"""
    next_lines = {TBL.normalize_line(line)
                  for line in (next_block_text or "").splitlines()}
    return any(text in next_lines
               for text in (res.get("target_body_row_texts") or ()))


def _release_at(norm: Sequence[str], offsets: Sequence[int], ends: Sequence[int],
                start: int, *, host_line_count: int, parts: Sequence[_SourcePart],
                owner: Sequence[int]) -> dict:
    """自 ``start`` 起求解一个（放行或拒绝）结果。

    ``host_line_count`` 是**宿主块**在拼接定位里占的行数（= 第二块的起始行号；单块时就是
    总行数）。区域跨过它就是「表题在本块、表体在下一块」的续表场景（F4），调用方据此决定
    是否把下一块接进来重解。

    ``target_start`` / ``target_end`` 是**拼接定位**里的坐标（只在一次求解内部比较用）；
    对外可引用的来源真值一律是 ``target_source_spans`` —— **逐块**的精确区间。
    """
    title = norm[start]
    explicit = TBL.is_explicit_table_title(title)
    if TBL.has_prose_punct(title) and not TBL.is_unit_line(title):
        region_idx, closed, terminator = TBL._region_indices(norm, start + 1)
    else:
        region_idx, closed, terminator = TBL._region_indices(norm, start)
    spans = _source_spans(region_idx, offsets, ends, parts, owner)
    base = {
        "release_rule_version": RELEASE_RULE_VERSION,
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "target_start": int(offsets[start]),
        "target_end": (int(ends[region_idx[-1]]) if region_idx else int(ends[start])),
        "target_table_title": title,
        "target_title_kind": _title_kind(title),
        "target_end_boundary": terminator,
        "target_closed": bool(closed),
        # **来源真值**：逐块的精确区间（有序）。单块对象恰一条，跨块对象两条及以上。
        "target_source_spans": spans,
        "target_source_span_state": (SOURCE_SPAN_STATE_MULTI if len(spans) > 1
                                     else SOURCE_SPAN_STATE_SINGLE),
        # 结构区是否被块末截断（**单块**视角：表头在本块、表体可能在下块，见 F4）。
        "target_open_at_block_end": _region_reaches_block_end(
            norm, region_idx, start, terminator, host_line_count),
        # 结构区是否**真的越过**块末（只在把下一块接进来重解后才有意义）。两者语义不同：
        # 前者是「可以试着接下一块」的前提，后者是「接上之后确实读到了下一块」的证据。
        "target_spans_block_boundary": bool(region_idx) and region_idx[-1] >= host_line_count,
    }
    if not region_idx:
        return {**base, "released": False, "reason": RELEASE_REASON_EMPTY_REGION}
    region_lines = [norm[i] for i in region_idx]
    # 起点行**自身就是表头行**（无表题的表：``项目 2025年 2024年度 2023年度`` 后面直接是
    # 子列层）时，它必须作为**父层**参与表头层裁定，否则子列层会失去父层、竖跨首列余量
    # 随之丢失（真实现场：募集说明书续表只有 ``金额 占比 …`` 一层，表体 7 列被误判成
    # 「比表头宽」）。起点行是表题（显式 ``表 N`` 或非多列行）时它不进表头。
    start_is_header = TBL.is_columnar_row(title) and not explicit
    after_title = list(region_lines)
    if after_title and TBL._collapse(after_title[0]) == TBL._collapse(title):
        after_title = after_title[1:]
    structure_rows = [title] + after_title if start_is_header else after_title
    if not explicit and not TBL.generic_structure_sufficient(
            TBL.structural_evidence(structure_rows)):
        return {**base, "reason": RELEASE_REASON_INSUFFICIENT_GENERIC, "released": False}
    unit = (after_title[0] if after_title and TBL.is_unit_line(after_title[0]) else "")
    tail = after_title[1:] if unit else after_title
    rest = ([title] + tail) if start_is_header else tail
    layers, wraps, remainder, decision = assign_header_layers(rest)
    if not layers:
        return {**base, "reason": RELEASE_REASON_NO_HEADER, "released": False}
    closure_texts = [t for t in remainder if TBL.is_closure_row(t)]
    reason, shape, split = body_shape_verdict(remainder)
    body_rows = list(split["body_rows"])
    base["body_evidence"] = shape
    base["target_header_decision"] = decision
    base["target_header_layers"] = list(layers)
    base["target_header_wrap_lines"] = list(wraps)
    if reason != RELEASE_REASON_RELEASED:
        return {**base, "reason": reason, "released": False}
    widest = max(_ncols(h) for h in layers)
    lead = _lead_column_evidence(layers)
    wrap_cells = sum(_ncols(w) for w in wraps)
    bound = widest + (1 if lead else 0) + wrap_cells
    if max(shape["body_column_counts"]) > bound:
        return {**base, "reason": RELEASE_REASON_BODY_WIDER, "released": False}
    closure_row = closure_texts[0] if closure_texts else ""
    content_id = table_content_id(
        title=title, unit=unit, header_lines=layers, wrap_lines=wraps,
        body_row_texts=body_rows, closure_row=closure_row)
    return {
        **base,
        "reason": RELEASE_REASON_RELEASED,
        "released": True,
        "structure_state": (STRUCTURE_STATE_PARTIAL if wraps
                            else STRUCTURE_STATE_COMPLETE),
        "target_unit": unit,
        # 表题形态可复核性：只有显式表题 / 首行即列头 / 标签首行才算「有可复核表题」。
        # 退化表题（单位行、勾选残句、页码或期间碎片）仍可读，但**不得**被当成具名表材料。
        "target_title_verifiable": _title_kind(title) != TITLE_KIND_DEGRADED,
        "target_header_lines": list(layers),
        "target_header_wrap_lines": list(wraps),
        "target_header_layer_count": len(layers),
        "target_column_count": max(shape["body_column_counts"]),
        "target_column_upper_bound": bound,
        "lead_column_labelled": bool(lead),
        "target_body_row_texts": body_rows,
        "target_body_rows": len(body_rows),
        "target_body_numeric_counts": shape["body_numeric_counts"],
        "target_numeric_cell_count": shape["numeric_cell_count"],
        # 区内**非表体**行：逐字保留作上下文，不构成事实、不构成数字权威。
        "target_residue_lines": list(split["residue_rows"]),
        "target_other_region_lines": list(split["other_rows"]),
        "target_closure_row": closure_row,
        "target_structure_rows": len(structure_rows),
        "content_id": content_id,
        # 阅读材料 = True；数字权威**显式否决**（需要数字仍走 FinancialSnapshot /
        # FinancialFactPack 与 Python/Decimal 结构校验）。
        "reading_material": True,
        "numeric_authority": False,
        "financial_authority_claimed": False,
        "authority_note": ("本对象只作阅读材料：表头/表体原文逐字，不含任何金额权威；"
                           "需要计算或引用的数字必须走 FinancialSnapshot / "
                           "FinancialFactPack 与 Python/Decimal 结构校验。"),
    }


def release_table_objects(text: str | None, *, from_offset: int = 0,
                          next_block_text: str | None = None,
                          block_id: str = "",
                          next_block_id: str = "") -> list[dict]:
    """在块原文上求解**全部**放行对象与拒绝记录（按源顺序）。

    ``next_block_text`` 是紧随其后**一个** Evidence 块的原文（有界：只接一块，不链式扩读），
    用于 F4「表题/表头在本块、表体在下一块」的续表场景。跨块只在**四条**同时成立时启用：
    ①单块视角下该候选的结构区被**块末**截断（``target_open_at_block_end``：终止边界是
    ``block_end``，且区域末行之后到块末只剩空行/页码行）；②接上下一块重解后**确实放行**，
    且结构区真的越过块末（``target_spans_block_boundary``）；③重解出的表体里**确有下一块
    的行**；④几何**按块**表达成立（``target_source_spans`` 至少两条，且每条都不越出它自己
    那一块）。结果里标 ``continuation_block_read`` 与 ``continuation_into_block_id``，使
    「这是一条跨块读出来的对象」在审计上留下痕迹，绝不伪装成单块对象。

    每个结果（放行或拒绝）都带 ``target_source_spans``：**逐来源块**的精确区间，按块序。
    单块对象恰一条；跨块对象两条及以上，且 ``target_source_span_state == "multi_block"``。
    """
    host = _source_parts([(block_id, text or "")])
    single_norm, single_offsets, single_ends = _measure_raw(host[0].text)
    if not single_norm:
        return []
    out: list[dict] = []
    for i in _candidate_starts(single_norm, single_offsets, from_offset):
        res = _release_at(single_norm, single_offsets, single_ends, i,
                          host_line_count=len(single_norm), parts=host,
                          owner=[0] * len(single_norm))
        if (not res.get("released") and next_block_text
                and res.get("reason") in (RELEASE_REASON_EMPTY_REGION,
                                          RELEASE_REASON_NO_HEADER,
                                          RELEASE_REASON_NO_DATA_ROW,
                                          RELEASE_REASON_BODY_ROW_FLOOR,
                                          RELEASE_REASON_NOT_NUMERIC,
                                          RELEASE_REASON_INSUFFICIENT_GENERIC)
                and res.get("target_open_at_block_end")):
            # 续表（F4）：表题/表头在本块、表体在下一块。**只接一块**，且接上之后必须
            # ①结构区确实越过块末、②表体里确有下一块的行 —— 否则说明该候选在本块内就已
            # 读完（接下一块会把另一张表吞成这张表的表体），或表体全在本块（跨块无意义）。
            joined_parts = _source_parts([(block_id, text or ""),
                                          (next_block_id, next_block_text or "")])
            joined_raw = _joined_raw(joined_parts)
            j_norm, j_offsets, j_ends = _measure_raw(joined_raw)
            j_owner = _owners(j_offsets, joined_parts)
            # 宿主块在拼接定位里占的行数 = 第二块的起始行号。**不能**用「单块行数」：
            # 块原文自带结尾换行时，拼接会多出一个块间空行，用单块行数会把那个空行误判成
            # 「已越过块末」。
            host_line_count = sum(1 for o in j_owner if o == 0)
            res2 = _release_at(j_norm, j_offsets, j_ends, i,
                               host_line_count=host_line_count,
                               parts=joined_parts, owner=j_owner)
            if (res2.get("released") and res2.get("target_spans_block_boundary")
                    and _has_body_row_in_next_block(res2, next_block_text)
                    and len(res2.get("target_source_spans") or ()) > 1
                    and _spans_within_blocks(res2, joined_parts)):
                res2["continuation_block_read"] = True
                res2["continuation_into_block_id"] = str(next_block_id or "")
                out.append(res2)
                continue
        if not _spans_within_blocks(res, host):
            res = {**res, "released": False,
                   "reason": RELEASE_REASON_SOURCE_SPAN_UNRESOLVED}
        out.append(res)
    return _drop_contained_objects(out)


def _spans_within_blocks(res: Mapping, parts: Sequence[_SourcePart]) -> bool:
    """逐块区间是否**声明齐全且不越界**（`tobj-2` 的来源真实性复核）。

    三条同时成立：①每个区域非空的结果都带 ``target_source_spans``；②每条 span 的块 id
    在本批来源里、且是该批的一个**真实块**；③``0 <= lo <= hi <= 该块文本长度``。
    单块结果的区间按构造就不会越界，这条判据在它身上恒真 —— 它挡的是「跨块几何没有
    按块表达」的那一类结果。
    """
    spans = res.get("target_source_spans")
    if not res.get("target_start") and not spans:
        return True
    if not spans:
        # 有区域却没有逐块区间 ⇒ 来源无法按块表达。
        return int(res.get("target_end") or 0) <= int(res.get("target_start") or 0)
    by_id = {p.block_id: p for p in parts}
    for span in spans:
        part = by_id.get(str(span.get("evidence_block_id") or ""))
        if part is None:
            return False
        lo, hi = (span.get("char_range") or (0, 0))[:2]
        if not (0 <= int(lo) <= int(hi) <= part.length):
            return False
    return True


def _drop_contained_objects(results: Sequence[dict]) -> list[dict]:
    """丢掉**起点落在已放行对象区间内部**的放行记录（表内伪起点去重）。

    ``table_start_indices`` 会在一张真实表的中间行上判定「结构表题形态」（真实现场：
    ``电池矿产资源  5,978,096  1.41% …`` 被当成另一个起点）。一张表不可能**从另一张表
    已声明的区间内部**开始，因此这类记录按起点包含关系确定性地去掉 —— 与候选顺序无关，
    也不依赖公司/页码/关键词。被丢掉的记录**不**进入放行清单（它们不是独立材料）。
    """
    released = [r for r in results if r.get("released")]
    keep: list[dict] = []
    for r in results:
        if not r.get("released"):
            keep.append(r)
            continue
        start = int(r.get("target_start") or 0)
        contained = any(
            int(o.get("target_start") or 0) < start
            and start < int(o.get("target_end") or 0)
            for o in released)
        if not contained:
            keep.append(r)
    return keep


def release_table_object(text: str | None, *, from_offset: int = 0,
                         next_block_text: str | None = None) -> dict | None:
    """首个放行对象（按源顺序）；无 → None。"""
    for res in release_table_objects(text, from_offset=from_offset,
                                     next_block_text=next_block_text):
        if res.get("released"):
            return res
    return None


def suppress_continued_duplicates(objects: Sequence[dict]) -> list[dict]:
    """跨块去重：被续表读出来的对象**抑制**它续进来的那块里的同体对象。

    真实现场（募集说明书 p50）：``表 5-11发行人主营业务成本构成表`` 的表题在块0、表体在块1。
    按块独立求解会得到**两条**记录：块0 的「表题 + 跨块表体」（有真表题、``continuation_
    block_read = True``）与块1 的「无表题单块对象」（表题退化成 ``项目 2025年 …``）。二者
    表体逐行相同、只是表题可信度不同 —— 保留有真表题的那条，丢掉另一条，避免同一张表在
    Pack 里变成两份材料。

    判据是**三条同时成立**：被抑制对象的块 id 恰是某个跨块对象的续读目标块、其表体行集合
    ⊆ 该跨块对象的表体行集合、且它不是跨块对象本身。确定性、与输入顺序无关。**调用方必须
    只传同一份文档的对象**（块 id 只在文档内唯一）。
    """
    continued = [o for o in (objects or ())
                 if o.get("released") and o.get("continuation_block_read")]
    if not continued:
        return list(objects or ())
    dropped: set[int] = set()
    for idx, obj in enumerate(objects or ()):
        if not obj.get("released") or obj.get("continuation_block_read"):
            continue
        # 被抑制对象的宿主块：locator 还没接上时取**逐块来源**的第一条（宿主块就是区域
        # 起点所在的块，`_source_spans` 恒按块序给出它）。
        spans = obj.get("target_source_spans") or ()
        block = str((obj.get("source_locator") or {}).get("evidence_block_id")
                    or (spans[0].get("evidence_block_id") if spans else "")
                    or (obj.get("continuation_into_block_id") or ""))
        body = set(obj.get("target_body_row_texts") or ())
        if not body or not block:
            continue
        for big in continued:
            if big is obj:
                continue
            if str(big.get("continuation_into_block_id") or "") != block:
                continue
            if body <= set(big.get("target_body_row_texts") or ()):
                dropped.add(idx)
                break
    return [o for i, o in enumerate(objects or ()) if i not in dropped]


def attach_locator(obj: dict, *, document_id: str, evidence_block_id: str,
                   page_number: int | None = None) -> dict:
    """把文档/块/页坐标接到对象上并算出**出现位置身份**（工具层唯一入口）。

    三条来源真实性口径（`tobj-2`）：

    1. ``char_range`` 是**宿主块**坐标下的区间（取自 ``target_source_spans`` 里宿主块
       那一条），**绝不**是拼接坐标 —— `tobj-1` 把拼接偏移写进单块 locator，于是跨块对象
       声明了宿主块里不存在的字符（真实三份文档 15/15 全部如此）。
    2. 跨块对象另带 ``source_blocks``（**有序**逐块精确区间）—— 来源是「一块还是两块」
       在 locator 上直接读得出来，不需要看区间长度猜。
    3. 页码只落在 locator 里作阅读坐标，**不进**任何规则的判据，也不进身份摘要。

    ``evidence_block_id`` 是**宿主块**（区域起点所在块）。它必须在 ``target_source_spans``
    里出现；不在（几何被改写过）就落 ``[0, 0]``，由材料侧的 fail-closed 复核挡下。
    """
    host_range = _host_local_range(obj, evidence_block_id)
    locator = {
        "document_id": str(document_id),
        "evidence_block_id": str(evidence_block_id),
        "char_range": host_range,
    }
    if page_number is not None:
        locator["page_number"] = int(page_number)
    spans = [s for s in (obj.get("target_source_spans") or ())]
    if len(spans) > 1:
        locator["source_blocks"] = [
            # 逐块区间**原样**取自 `_source_spans`（已按该块长度截断），不在这里重算。
            {"evidence_block_id": str(s.get("evidence_block_id") or ""),
             "char_range": [int((s.get("char_range") or (0, 0))[0]),
                            int((s.get("char_range") or (0, 0))[1])]}
            for s in spans]
    finalized = {k: v for k, v in obj.items() if k != "_locator_template"}
    finalized["source_locator"] = locator
    finalized["released_object_id"] = released_object_id(
        content_id=str(obj.get("content_id") or ""), source_locator=locator)
    return finalized

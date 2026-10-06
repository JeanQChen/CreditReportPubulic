"""Eval: M930-3 目标二 —— 合格表对象的版本化放行（`harness/table_object_release.py`，tobj-1）。

用法: python -X utf8 -m evals.test_m930_3_table_object_release

只测**机制**：放行判据、三类真实版式缺陷（竖跨首列 / 折行表头片段 / 子列层更宽）、
续表跨块、身份正交、以及「放行 = 阅读材料 ≠ 数字权威」这条硬边界。合成夹具一律
公司无关：不出现样本代号、固定页码、答案关键词。

覆盖：

1. **G1 结构起点 + G2 全标签表头**：起点行自身带数字（表体中间行被当成结构起点）⇒ 拒，
   绝不拿一行数据冒充表头；**冻结线上判据在同一文本上会接受**（本条同时钉住「tobj-1 是
   严格收紧而非放宽」）。
2. **F1 竖跨首列**：父层带行标识列、子层不带 ⇒ 表体 = 最宽表头层 + 1 是**合法**结构。
3. **F3 子列层比父层更宽**：分组父列 + 更宽子列层 + 等宽数字表体 ⇒ 放行（冻结晶口径
   因为要求「下方有更宽行」而误拒）。
4. **F2 折行表头片段**：片段行只被**标注**（`target_header_wrap_lines`），表头层**逐字
   保留不重建**；该对象一律 `partial`。
5. **F4 续表跨块**：表题在块末、表体在下一块 ⇒ 跨块对象带真表题；下一块那份同体对象
   被 `suppress_continued_duplicates` 抑制，同一张表不变成两份材料。
6. **拒绝面**：全标签表（释义表）不冒数字表；单行残句不成表；列宽极差超界不并表；
   每条拒绝都有闭集内的 typed reason（绝不静默丢弃）。
7. **身份正交**：内容身份对列间距不敏感、对任一数字/列序敏感；对象身份随 locator 变化，
   而**页码不进身份**。
8. **材料/权威分离**：放行对象的 payload 显式声明 `numeric_authority=False` /
   `financial_authority_claimed=False`，且不含任何金额字段名。
9. **真实文档**：三份样本上，营业收入及营业成本整体情况（含毛利率）、续表成本构成表、
   毛利润及毛利率构成表真的被放行 —— 表题/续表情形逐条断言。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import table_object_release as REL  # noqa: E402
from harness import table_structure as TBL       # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "data/samples/300750/announcements"


# ---------------------------------------------------------------------------
# 0. 合成夹具（公司无关；列间 ≥2 空格即列边界）
# ---------------------------------------------------------------------------

def _simple() -> str:
    return ("项目  2025年  2024年\n"
            "甲类  1,234  2,345\n"
            "乙类  3,456  4,567\n"
            "丙类  5,678  6,789\n")


def _fixture_lead_column() -> str:
    """F1：父层首格是行标识列（竖跨），子层只覆盖其后各列。"""
    return ("项目  2025年  2024年\n"
            "金额  占比  金额  占比  同比\n"
            "甲类  1,234  10.0%  2,345  20.0%  3.0%\n"
            "乙类  3,456  30.0%  4,567  40.0%  4.0%\n"
            "丙类  5,678  50.0%  6,789  60.0%  5.0%\n")


def _fixture_wide_subcolumn() -> str:
    """F3：显式表题 + 分组父列（3 列）+ 更宽子列层（10 列）+ 等宽数字表体。"""
    return ("表 1-1某构成表\n"
            "2025年  2024年  2023年\n"
            "项目  金额  占比  毛利  金额  占比  毛利  金额  占比  毛利\n"
            "甲类  1    2    3    4    5    6    7    8    9\n"
            "乙类  10    11    12    13    14    15    16    17    18\n"
            "丙类  19    20    21    22    23    24    25    26    27\n")


def _fixture_wrap_fragment() -> str:
    """F2：表头标签被 PDF 折成两行（尾部延伸型片段）。"""
    return ("项目  营业收入  营业成本  毛利率  营业收入比上  营业成本比上  毛利率比上年\n"
            "年同期增减  年同期增减  同期增减\n"
            "甲类  1,234  2,345  3.0%  4  5  6\n"
            "乙类  2,345  3,456  4.0%  5  6  7\n"
            "丙类  3,456  4,567  5.0%  6  7  8\n")


def _caption_only() -> str:
    return "表 1-1某业务成本构成表\n"


def _caption_body() -> str:
    return ("项目  2025年  2024年\n"
            "金额  占比  金额  占比\n"
            "甲类  1,234  10.0%  2,345  20.0%\n"
            "乙类  3,456  30.0%  4,567  40.0%\n")


def _block_end_paged() -> str:
    """F4 真实形态：表头在本块、块末**带页码行**（真实年报块末总带页码）。"""
    return ("1）营业收入及营业成本整体情况\n"
            "单位：千元\n"
            "  项目  营业收入  营业成本  毛利率  营业收入比上  营业成本比上  毛利率比上年\n"
            "  年同期增减  年同期增减  同期增减\n"
            " 分业务\n"
            "\n"
            "19\n")


def _block_end_body() -> str:
    """下一块：直接就是数字表体行（表头留在上一块）。"""
    return ("电气机械及器材  356,519,551  268,494,348  24.69%  -9.32%  -15.51%  5.51%\n"
            "采选冶炼行业  5,493,003  5,024,611  8.53%  -28.98%  -18.93%  -11.33%\n"
            "电池矿产资源  5,978,096  5,493,003  8.83%  -1.41%  -1.52%  -0.11%\n")


def _block_end_prose() -> str:
    """同前，但结构区在本块内**被正文行终止** ⇒ 表已在本块读完，不得再接下一块。"""
    return ("1）营业收入及营业成本整体情况\n"
            "单位：千元\n"
            "  项目  营业收入  营业成本  毛利率  营业收入比上  营业成本比上  毛利率比上年\n"
            "  年同期增减  年同期增减  同期增减\n"
            "本表数据来源于公司年度报告。\n")


def _other_table_block() -> str:
    """下一块是**另一张显式表题起的表**（不是本表的续表）。"""
    return ("表 9-9另一张表\n"
            "项目  金额  占比\n"
            "甲类  1,234  10.0%\n"
            "乙类  2,345  20.0%\n")


# ---------------------------------------------------------------------------
# 表题形态（事实记录轴；消费方据此区分「具名表材料」与「可读残段」）
# ---------------------------------------------------------------------------

_TITLE_FORM_CASES = (
    ("表 1-1某构成表\n" + "项目  金额  占比\n" + "甲类  1,234  10.0%\n"
     + "乙类  2,345  20.0%\n", REL.TITLE_KIND_EXPLICIT, True),
    ("项目  金额  占比\n" + "甲类  1,234  10.0%\n" + "乙类  2,345  20.0%\n",
     REL.TITLE_KIND_HEADER_LINE, True),
    ("教育程度\n" + "项目  人数  占比\n" + "甲类  1,234  10.0%\n"
     + "乙类  2,345  20.0%\n", REL.TITLE_KIND_LABEL_LINE, True),
    ("单位：千元\n" + "项目  金额  占比\n" + "甲类  1,234  10.0%\n"
     + "乙类  2,345  20.0%\n", REL.TITLE_KIND_DEGRADED, False),
    ("适用 □不适用\n" + "项目  金额  占比\n" + "甲类  1,234  10.0%\n"
     + "乙类  2,345  20.0%\n", REL.TITLE_KIND_DEGRADED, False),
    ("2024年\n" + "项目  金额  占比\n" + "甲类  1,234  10.0%\n"
     + "乙类  2,345  20.0%\n", REL.TITLE_KIND_DEGRADED, False),
)


def _label_only_table() -> str:
    """释义表形态：多列、非合计，但一条数字行都没有。"""
    return ("项目  含义\n"
            "甲  指  某个简称\n"
            "乙  指  另一个简称\n"
            "丙  指  第三个简称\n")


def _single_row_fragment() -> str:
    """表后残段：只有一条数字行。"""
    return ("类别  项目  本期金额  占比  上期金额  占比  增减\n"
            "甲行业  直接材料  2,202  71.7%  2,026  76.4%  -4.6%\n")


def _data_row_as_header() -> str:
    """表体中间行被当成结构起点：起点行自身带数字。"""
    return ("甲类  1,234  2,345  3,456\n"
            "乙类  3,456  4,567  5,678\n"
            "丙类  5,678  6,789  7,890\n"
            "丁类  7,890  8,901  9,012\n")


def _spread_out_of_bound() -> str:
    """同区并排两张列宽相差过大的表（极差超界 ⇒ 不合并放行）。"""
    return ("项目  2025年  2024年  同比  备注  来源  口径  说明\n"
            "甲类  1,234  2,345  3.0%  甲  乙  丙  丁\n"
            "乙类  3,456  4,567  4.0%  甲  乙  丙  丁\n"
            "丙  1  2\n"
            "丁  3  4\n")


# ---------------------------------------------------------------------------
# 1. 放行判据
# ---------------------------------------------------------------------------

def _check_release_basics(check, details) -> None:
    objs = REL.release_table_objects(_simple())
    rel = [o for o in objs if o.get("released")]
    check(len(rel) == 1, "单层表头 + 3 条数字表体行 ⇒ 恰放行 1 个对象")
    obj = rel[0]
    check(obj["structure_state"] == REL.STRUCTURE_STATE_COMPLETE,
          "无折行片段的表对象标 complete")
    check(obj["target_body_row_texts"] == ["甲类  1,234  2,345",
                                           "乙类  3,456  4,567",
                                           "丙类  5,678  6,789"],
          "表体行**逐字**保留（列间距、原文一字不改）")
    check(obj["lead_column_labelled"] is False and obj["target_column_count"] == 3,
          "单层表头不给竖跨首列余量（列数 = 最宽表头层 = 3）")
    check(obj["target_header_lines"] == ["项目  2025年  2024年"],
          "表头层逐字保留")
    check(obj["release_rule_version"] == REL.RELEASE_RULE_VERSION
          and obj["release_schema_version"] == REL.RELEASE_SCHEMA_VERSION
          and obj["release_rule_version"] == "tobj-2",
          "放行记录带规则版本与 schema 版本（可复核、可升版；当前 tobj-2）")


def _check_lead_column(check, details) -> None:
    obj = REL.release_table_object(_fixture_lead_column())
    check(obj is not None, "F1 竖跨首列：父层带行标识列 ⇒ 放行")
    if obj is None:
        return
    check(obj["lead_column_labelled"] is True,
          "F1 竖跨首列被识别（两层首格不同、且都是非数字标签）")
    check(obj["target_column_count"] == 6
          and any(len(TBL.canonical_cells(t)) == 6
                  for t in obj["target_body_row_texts"]),
          "F1 表体 6 列 = 最宽表头层 5 列 + 竖跨首列 1 —— 不再被误判成「列结构无法解释」")
    frozen = TBL.reference_target_table_objects(_fixture_lead_column(), 0)
    check(frozen == [],
          "同一文本在**冻结线上判据**上仍被拒（tobj-1 是补通道，不改冻结语义）")


def _check_wide_subcolumn(check, details) -> None:
    obj = REL.release_table_object(_fixture_wide_subcolumn())
    check(obj is not None, "F3 子列层比父层更宽 ⇒ 放行")
    if obj is None:
        return
    check(obj["target_header_layer_count"] == 2
          and obj["target_header_lines"][1].startswith("项目"),
          "F3 更宽的标签行被裁定为**第二层表头**，而不是首个数据行")
    check(len(obj["target_body_row_texts"]) == 3
          and all(len(TBL.canonical_cells(t)) == 10
                  for t in obj["target_body_row_texts"]),
          "F3 三条真实数据行全部进表体（没有被表头吞掉）")
    # 冻结侧失败点：子列层（10 列）不比表体更宽，冻结晶判据第 3 条（「下方存在比子列行更宽的
    # 结构行」）因此不成立 ⇒ 子列层被降级为首个数据行 ⇒ 表体（10 列）比父层表头（3 列）宽
    # ⇒ 整张表被拒。这里既钉住**机制**（原语返回 False）也钉住**结果**（对象数为 0）。
    parent = TBL.canonical_cells("2025年  2024年  2023年")
    child = TBL.canonical_cells(
        "项目  金额  占比  毛利  金额  占比  毛利  金额  占比  毛利")
    rows = [TBL.canonical_cells(t) for t in
            ("甲类  1    2    3    4    5    6    7    8    9",
             "乙类  10    11    12    13    14    15    16    17    18")]
    check(TBL.second_header_layer_cells(parent, child, rows) is False,
          "F3 冻结晶判据第 3 条要求「下方有更宽的结构行」⇒ 更宽的子列层被降级为首个数据行")
    frozen = TBL.reference_target_table_objects(_fixture_wide_subcolumn(), 0)
    check(frozen == [], "F3 同一文本在冻结线上判据上被拒（子列层被降级 ⇒ 表体比表头宽）")


def _check_wrap_fragment(check, details) -> None:
    obj = REL.release_table_object(_fixture_wrap_fragment())
    check(obj is not None, "F2 折行表头片段 ⇒ 仍放行（片段不是数据行）")
    if obj is None:
        return
    check(obj["structure_state"] == REL.STRUCTURE_STATE_PARTIAL,
          "F2 含折行片段的对象标 partial（片段落点在结构上二义）")
    check(obj["target_header_wrap_lines"] == ["年同期增减  年同期增减  同期增减"],
          "F2 片段行被**标注**且逐字保留")
    check(obj["target_header_lines"] == [
        "项目  营业收入  营业成本  毛利率  营业收入比上  营业成本比上  毛利率比上年"],
        "F2 表头层**不做位置重建**（绝不把片段猜进某个单元格）")
    check(len(obj["target_body_row_texts"]) == 3,
          "F2 折行片段没有被当成首个数据行（表体仍是 3 行）")


def _check_continuation(check, details) -> None:
    solo = REL.release_table_objects(_caption_body(), block_id="blk_b")
    check(any(o.get("released") for o in solo),
          "续表目标块单独求解：表体自身可放行（表题退化成首行）")
    got = REL.release_table_objects(_caption_only(), block_id="blk_a",
                                    next_block_text=_caption_body(),
                                    next_block_id="blk_b")
    rel = [o for o in got if o.get("released")]
    check(len(rel) == 1, "F4 表题在块末、表体在下一块 ⇒ 跨块放行一条")
    if not rel:
        return
    obj = rel[0]
    check(obj.get("continuation_block_read") is True
          and obj.get("continuation_into_block_id") == "blk_b",
          "F4 跨块读数被标记（不伪装成单块对象）")
    check(obj["target_table_title"] == "表 1-1某业务成本构成表",
          "F4 跨块对象带的是**真表题**（不是退化的首行）")
    check(len(obj["target_body_row_texts"]) == 2,
          "F4 表体来自下一块且逐字保留")
    kept = REL.suppress_continued_duplicates(rel + solo)
    released = [o for o in kept if o.get("released")]
    check(len(released) == 1 and released[0].get("continuation_block_read") is True,
          "F4 续读目标块里的同体对象被抑制（同一张表不变成两份材料）")
    capped = REL.release_table_objects(_caption_only(), block_id="blk_a",
                                       next_block_text=_caption_only(),
                                       next_block_id="blk_b")
    check(not any(o.get("released") for o in capped),
          "F4 只接**一块**：下一块也没有表体 ⇒ 不放行（不链式扩读）")

    # 块末截断的续表（真实形态：区域末行之后只剩页码行）。单块视角必须先给出**带
    # typed reason 的拒绝记录**并把「本块已无可读结构行」记下来，跨块读数才有据可查。
    solo_paged = REL.release_table_objects(_block_end_paged(), block_id="blk_a")
    check(not any(o.get("released") for o in solo_paged)
          and all(o.get("target_open_at_block_end") is True for o in solo_paged),
          "块末带页码行的候选：单块视角不放行，但留下 target_open_at_block_end 记录")
    paged = REL.release_table_objects(_block_end_paged(), block_id="blk_a",
                                      next_block_text=_block_end_body(),
                                      next_block_id="blk_b")
    rel_paged = [o for o in paged if o.get("released")]
    check(len(rel_paged) == 1
          and rel_paged[0].get("continuation_block_read") is True,
          "F4 页码行不阻断续表：表头在本块、表体在下一块 ⇒ 跨块放行一条")
    if rel_paged:
        check(len(rel_paged[0]["target_body_row_texts"]) == 3
              and all(t in _block_end_body()
                      for t in rel_paged[0]["target_body_row_texts"]),
              "F4 跨块对象的表体**确有**下一块的行")
        check(rel_paged[0]["target_header_wrap_lines"] ==
              ["年同期增减  年同期增减  同期增减"],
              "F4 跨块后折行表头片段才可判定（片段下方确有数据行）")
        check("19" in rel_paged[0]["target_other_region_lines"],
              "F4 页码行如实落在 other_region（逐字保留，不构成事实）")

    # 反例一：结构区被**正文行**终止 ⇒ 表已在本块读完，接下一块会把别的表吞成其表体。
    prose = REL.release_table_objects(_block_end_prose(), block_id="blk_a",
                                      next_block_text=_block_end_body(),
                                      next_block_id="blk_b")
    check(not any(o.get("released") for o in prose),
          "正文行终止的结构区不跨块（接上会吞掉下一块的表）")
    # 反例二：下一块是另一张显式表题起的表 ⇒ 结构区没有越过块末，不接。
    other = REL.release_table_objects(_block_end_paged(), block_id="blk_a",
                                      next_block_text=_other_table_block(),
                                      next_block_id="blk_b")
    check(not any(o.get("released") for o in other),
          "下一块另起显式表题 ⇒ 不成续表（跨块对象数 0）")
    # 反例三：本块自身已放行的完整表不得把下一块的行吸进来。
    complete = REL.release_table_objects(_simple(), block_id="blk_a",
                                         next_block_text=_block_end_body(),
                                         next_block_id="blk_b")
    rel_complete = [o for o in complete if o.get("released")]
    check(len(rel_complete) == 1
          and len(rel_complete[0]["target_body_row_texts"]) == 3
          and not rel_complete[0].get("continuation_block_read"),
          "本块内已放行的完整表不吸下一块的行（续表只在被块末截断时启用）")


# ---------------------------------------------------------------------------
# 2. 拒绝面（typed reason，绝不静默丢弃）
# ---------------------------------------------------------------------------

def _check_refusals(check, details) -> None:
    obj = REL.release_table_object(_label_only_table())
    check(obj is None, "全标签多列表（释义表形态）不放行")
    res = REL.release_table_objects(_label_only_table())
    check(len(res) == 1 and res[0]["reason"] == REL.RELEASE_REASON_NOT_NUMERIC,
          "全标签表给出 typed reason = no_numeric_body_row（有表体形状、无数字行）")
    check(res[0]["body_evidence"]["residue_row_count"] == 3,
          "全标签表的行落在 residue（逐字保留作上下文，不构成事实）")

    res = REL.release_table_objects(_single_row_fragment())
    check(all(o["reason"] != REL.RELEASE_REASON_RELEASED for o in res),
          "只有一条数字行的残段不放行")
    check(any(o["reason"] == REL.RELEASE_REASON_BODY_ROW_FLOOR for o in res),
          "残段给出 typed reason = body_row_count_below_floor")

    res = REL.release_table_objects(_spread_out_of_bound())
    check(any(o["reason"] == REL.RELEASE_REASON_BODY_INCONSISTENT for o in res)
          or not any(o.get("released") for o in res),
          "列宽极差超界 ⇒ 不合并放行（或按其它 typed reason 拒绝）")

    res = REL.release_table_objects(_data_row_as_header())
    check(any(o["reason"] == REL.RELEASE_REASON_NO_HEADER for o in res),
          "G2 起点行自身带数字 ⇒ typed reason = no_physical_header（不拿数据行冒充表头）")
    check(not any(o.get("released") for o in res),
          "G2 该文本在 tobj-1 上完全不放行")
    frozen = TBL.reference_target_table_objects(_data_row_as_header(), 0)
    check(len(frozen) == 1 and frozen[0]["target_body_rows"] >= 1,
          "同一文本在**冻结线上判据**上却被接受（tobj-1 在此处是收紧）")

    res = REL.release_table_objects("")
    check(res == [], "空原文不放行任何对象")
    reasons = {o["reason"] for o in res}
    check(reasons <= set(REL.RELEASE_REASONS),
          "全部理由码都在闭集内（拒绝不是自由文本）")


# ---------------------------------------------------------------------------
# 3. 身份正交 + 材料/权威分离
# ---------------------------------------------------------------------------

def _check_identity(check, details) -> None:
    wide = _simple().replace("  1,234", "      1,234")
    a = REL.release_table_object(_simple())
    b = REL.release_table_object(wide)
    check(a is not None and b is not None, "列间距变体两版都放行")
    if a and b:
        check(a["content_id"] == b["content_id"],
              "内容身份对**列间距**不敏感（同一张表不因排版空白变成两张）")
        check(a["target_body_row_texts"] != b["target_body_row_texts"],
              "但表体原文各自逐字保留（身份归一不吞原文差异）")
    mutated = _simple().replace("1,234", "1,235")
    c = REL.release_table_object(mutated)
    check(a is not None and c is not None and a["content_id"] != c["content_id"],
          "内容身份对任一数字变化敏感（1,234 → 1,235 必换身份）")
    if not (a and c):
        return
    loc1 = REL.attach_locator(a, document_id="doc_x", evidence_block_id="blk_1",
                              page_number=10)
    loc2 = REL.attach_locator(a, document_id="doc_x", evidence_block_id="blk_2",
                              page_number=10)
    loc3 = REL.attach_locator(a, document_id="doc_x", evidence_block_id="blk_1",
                              page_number=99)
    check(loc1["released_object_id"] != loc2["released_object_id"],
          "对象身份随 locator（块）变化 —— 同一内容在两处是可审计的两条记录")
    check(loc1["released_object_id"] == loc3["released_object_id"],
          "**页码不进身份**（页码只是阅读坐标，绝不成为规则的一部分）")
    check(loc1["source_locator"]["page_number"] == 10
          and "_locator_template" not in loc1,
          "locator 落在 payload 的 source_locator 里，内部模板不落盘")


def _check_title_kind(check, details) -> None:
    for text, kind, verifiable in _TITLE_FORM_CASES:
        obj = REL.release_table_object(text)
        check(obj is not None and obj.get("target_title_kind") == kind
              and obj.get("target_title_verifiable") is verifiable,
              f"表题形态：{kind}（verifiable={verifiable}）")


def _check_material_not_authority(check, details) -> None:
    obj = REL.release_table_object(_fixture_lead_column())
    check(obj is not None, "材料/权威分离：先取一个放行对象")
    if obj is None:
        return
    check(obj["reading_material"] is True, "放行对象可作阅读材料")
    check(obj["numeric_authority"] is False
          and obj["financial_authority_claimed"] is False,
          "放行对象**显式否决**数字/财务权威")
    forbidden = ("amount", "amount_cny", "fact_id", "financial_fact",
                 "authoritative_amount")
    payload_keys = set(obj)
    check(not (payload_keys & set(forbidden)),
          "payload 里不出现任何金额权威字段名")
    check(isinstance(obj["target_body_row_texts"], list)
          and all(isinstance(t, str) for t in obj["target_body_row_texts"]),
          "表体是**原文行**而不是解析后的数字（解析/计算不在本通道）")


# ---------------------------------------------------------------------------
# 4. 真实文档（三份样本；文件缺失则跳过）
# ---------------------------------------------------------------------------

def _check_source_spans(check, details) -> None:
    """`tobj-2` 的来源真实性：**逐块**精确区间（`tobj-1` 的跨块对象 100% 越界）。

    正反例：①合法跨块 ⇒ 逐块区间各自落在自己块内、续段在下一块坐标下从 0 起；
    ②单块对象 ⇒ 恰一条区间、locator 与它是同一套几何、且不写 source_blocks；
    ③身份轴 ⇒ 单块对象换块即换身份，跨块对象**漏掉续块**即换身份（残缺来源不是同一次出现）。
    """
    host = _block_end_paged()
    nxt = _block_end_body()
    rel = REL.release_table_objects(host, block_id="blk_h", next_block_text=nxt,
                                    next_block_id="blk_n")
    obj = next((o for o in rel if o.get("released")), None)
    check(obj is not None, "来源真实性：跨块对象可放行（正例存在，否则下列断言无意义）")
    if obj is None:
        return
    spans = [dict(s) for s in (obj.get("target_source_spans") or ())]
    check(len(spans) == 2
          and obj.get("target_source_span_state") == REL.SOURCE_SPAN_STATE_MULTI,
          "跨块对象带**两条**逐块区间且状态为 multi_block（不伪装成单块来源）")
    host_len, nxt_len = len(host), len(nxt)
    check(spans and spans[0]["evidence_block_id"] == "blk_h"
          and spans[0]["char_range"][0] >= 0
          and 0 < spans[0]["char_range"][1] <= host_len
          and spans[0]["block_text_length"] == host_len,
          "宿主段**不超过宿主块自身长度**（不再声明宿主块里不存在的字符）")
    check(len(spans) == 2 and spans[1]["evidence_block_id"] == "blk_n"
          and spans[1]["char_range"][0] == 0
          and 0 < spans[1]["char_range"][1] <= nxt_len
          and spans[1]["block_text_length"] == nxt_len,
          "续段在**下一块坐标**下从 0 起、且不超过该块长度")
    check(all(0 <= s["char_range"][0] <= s["char_range"][1] <= len(t)
              for s, t in ((spans[0], host), (spans[1], nxt))),
          "每条区间都落在**它自己那一块**的文本之内（构造性保证，非事后校验）")

    locator = REL.attach_locator(obj, document_id="doc_x", evidence_block_id="blk_h",
                                 page_number=19)
    check(locator["source_locator"]["char_range"] == list(spans[0]["char_range"])
          and locator["source_locator"]["char_range"][1] <= host_len,
          "locator 的 char_range 是**宿主块坐标**下的区间（不是拼接坐标）")
    check(len(locator["source_locator"].get("source_blocks") or ()) == 2,
          "跨块 locator 另带 source_blocks（一块还是两块，在 locator 上直接读得出）")

    # 身份轴①：跨块对象的身份含**全部**来源块 ⇒ 漏掉续块的残缺声明**不是**同一次出现。
    truncated = REL.attach_locator(
        {**obj, "target_source_spans": [spans[0]]},
        document_id="doc_x", evidence_block_id="blk_h")
    check(truncated["released_object_id"] != locator["released_object_id"],
          "漏掉续块 ⇒ 出现位置身份改变（残缺来源不冒充同一次出现）")

    solo = REL.release_table_objects(_simple(), block_id="blk_1")
    solo_obj = next((o for o in solo if o.get("released")), None)
    solo_spans = list((solo_obj or {}).get("target_source_spans") or ())
    check(solo_obj is not None and len(solo_spans) == 1
          and solo_obj.get("target_source_span_state") == REL.SOURCE_SPAN_STATE_SINGLE,
          "单块对象恰一条逐块区间、状态 single_block")
    if solo_obj is not None:
        solo_final = REL.attach_locator(solo_obj, document_id="doc_x",
                                        evidence_block_id="blk_1")
        check("source_blocks" not in solo_final["source_locator"],
              "单块 locator **不**写 source_blocks（旧形态逐字保留，单块/多块一眼可分）")
        check(solo_final["source_locator"]["char_range"] == solo_spans[0]["char_range"],
              "单块 locator 的 char_range 与逐块区间是同一个数（不是两套几何）")
        # 身份轴②：单块对象换块 ⇒ 换身份（同一内容在两处是可审计的两条记录）。
        moved = REL.attach_locator(solo_obj, document_id="doc_x",
                                   evidence_block_id="blk_2")
        check(moved["released_object_id"] != solo_final["released_object_id"],
              "同一内容换个来源块 ⇒ 出现位置身份随之改变（不是同一次出现）")

    # 反例：续块是**无关的相邻块**（另一张显式表题）⇒ 逐块区间不得把它算进来。
    other = REL.release_table_objects(host, block_id="blk_h",
                                      next_block_text=_other_table_block(),
                                      next_block_id="blk_o")
    touched = [s for o in other for s in (o.get("target_source_spans") or ())
               if str(s.get("evidence_block_id")) == "blk_o"]
    check(touched == [],
          "相邻无关块不进入任何逐块区间（跨块不是「接上就放行」）")
    # 反例：**错版本**。逐块区间记录的是「本块文本长度」，块文本一变（换版本/换 set）
    # 就与存量声明对不上 —— 材料侧以 source_binding_mismatch 拒收，此处钉住放行侧
    # 的确写下了可被**逐块**比对的长度（而不是拼接长度）。
    check(all(s.get("block_text_length") == len(t)
              for s, t in ((spans[0], host), (spans[1], nxt))),
          "逐块区间各带**自己那一块**的文本长度（换版本时逐块可比对）")


def _check_real_documents(check, details) -> int:
    try:
        from evaluation import business_material_readback as RB
    except Exception as exc:                      # pragma: no cover
        details.append(f"SKIP: 真实文档读回模块不可用（{exc}）")
        return 1
    docs = ("NDSD_2025_year", "NDSD_2024_year", "NDSD_KCZ_2026")
    if not all((SAMPLES / f"{d}.pdf").exists() for d in docs):
        details.append("SKIP: 样本 PDF 不在工作区（真实文档断言未执行）")
        return 1
    released: dict[str, list[dict]] = {}
    block_len: dict[str, dict[str, int]] = {}
    for document_id in docs:
        pdf = SAMPLES / f"{document_id}.pdf"
        _index, _snapshot, _outline, blocks, _live = RB.build_index(REPO, pdf)
        ordered = sorted(blocks.items(),
                         key=lambda kv: (kv[1].page_number, kv[1].block_index))
        block_len[document_id] = {str(k): len(str(getattr(v, "text", "") or ""))
                                  for k, v in ordered}
        collected: list[dict] = []
        for k, (block_id, block) in enumerate(ordered):
            nxt = ordered[k + 1] if k + 1 < len(ordered) else None
            collected.extend(REL.release_table_objects(
                block.text, block_id=block_id,
                next_block_text=(nxt[1].text if nxt else None),
                next_block_id=(nxt[0] if nxt else "")))
        released[document_id] = [o for o in REL.suppress_continued_duplicates(collected)
                                 if o.get("released")]

    # 来源真实性（真实文档）：`tobj-1` 在这里 15/15 全越界 —— 跨块对象把**拼接**偏移写进
    # 单块 locator。以下三条逐对象复核：区间不得越出**它自己那一块**、跨块对象必须逐块
    # 声明齐全、声明长度必须与库里那一块的实际长度一致。
    overrun: list[str] = []
    incomplete: list[str] = []
    mismatched: list[str] = []
    for document_id in docs:
        lens = block_len[document_id]
        for obj in released[document_id]:
            spans = [s for s in (obj.get("target_source_spans") or ())]
            state = str(obj.get("target_source_span_state") or "")
            tag = f"{document_id} p{obj.get('source_locator', {}).get('page_number', '?')}"
            for span in spans:
                bid = str(span.get("evidence_block_id") or "")
                lo, hi = (span.get("char_range") or (0, 0))[:2]
                real = lens.get(bid)
                if real is None:
                    mismatched.append(f"{tag}: 声明块 {bid!r} 不在该文档块集里")
                elif int(span.get("block_text_length") or -1) != real:
                    mismatched.append(f"{tag}: {bid!r} 声明长度与实块不符")
                elif not (0 <= int(lo) <= int(hi) <= real):
                    overrun.append(f"{tag}: {bid!r} 区间 [{lo},{hi}] 越出 {real}")
            if obj.get("continuation_block_read") is True:
                into = str(obj.get("continuation_into_block_id") or "")
                if state != REL.SOURCE_SPAN_STATE_MULTI or len(spans) < 2 \
                        or into not in {str(s.get("evidence_block_id") or "") for s in spans}:
                    incomplete.append(f"{tag}: 跨块声明不齐（state={state}, n={len(spans)}）")
    check(overrun == [],
          "真实三份文档：放行对象的逐块区间**无一越出**自己那一块"
          + (f"（越界 {len(overrun)} 条：{overrun[:3]}）" if overrun else ""))
    check(mismatched == [],
          "真实三份文档：逐块声明长度与库中该块实际长度逐条一致（换版本可比对）"
          + (f"（不符 {len(mismatched)} 条：{mismatched[:3]}）" if mismatched else ""))
    check(incomplete == [],
          "真实三份文档：跨块对象的逐块声明齐全（含宿主块与续块）"
          + (f"（不齐 {len(incomplete)} 条：{incomplete[:3]}）" if incomplete else ""))

    y25 = released["NDSD_2025_year"]
    whole = [o for o in y25 if any("毛利率" in h for h in o["target_header_lines"])]
    check(len(whole) == 1,
          "2025 年报：营业收入/营业成本/毛利率三栏同表被放行恰一条")
    if whole:
        check(whole[0]["structure_state"] == REL.STRUCTURE_STATE_PARTIAL
              and whole[0]["target_header_wrap_lines"],
              "2025 三栏表带折行表头片段 ⇒ partial 且片段被标注")
        check(len(whole[0]["target_body_row_texts"]) >= 8,
              "2025 三栏表的表体行数 ≥ 8（分业务/分产品/分地区逐行保留）")
        check(any("营业成本" in h for h in whole[0]["target_header_lines"]),
              "2025 三栏表头逐字含营业成本列名")

    y24 = released["NDSD_2024_year"]
    cont = [o for o in y24 if o["target_table_title"].startswith("1）营业收入及营业成本整体情况")]
    check(len(cont) == 1 and cont[0].get("continuation_block_read") is True,
          "2024 年报：营业收入及营业成本整体情况的表体跨页 ⇒ 只有续表通道能拿到")
    if cont:
        check(len(cont[0]["target_body_row_texts"]) >= 8,
              "2024 续表对象的表体行数 ≥ 8")
        check(cont[0]["target_title_verifiable"] is True
              and cont[0]["target_title_kind"] != REL.TITLE_KIND_DEGRADED,
              "2024 续表对象有可复核表题（营业收入及营业成本整体情况）")

    kcz = released["NDSD_KCZ_2026"]

    # 表题形态轴：真实文档里**退化表题**必须只落在残句式首行上（单位行、勾选残句、
    # 期间碎片）—— 这些对象仍可读，但不得被当成具名表材料。
    degraded = [o["target_table_title"] for o in y25 + y24 + kcz
                if not o["target_title_verifiable"]]
    check(degraded and all(
        TBL.is_unit_line(t) or REL._is_selection_form_line(t)
        or REL._is_date_fragment_line(t) for t in degraded),
        "退化表题只出现在单位行/勾选残句/期间碎片上（不误伤真实表题）")
    check(all(o["target_title_kind"] in REL.TITLE_KINDS
              for o in y25 + y24 + kcz),
          "表题形态取值都在闭集内")
    titles = [o["target_table_title"] for o in kcz]
    for needle in ("主营业务收入构成表", "主营业务成本构成表", "毛利润及毛利率构成表"):
        check(any(needle in t for t in titles),
              f"2026 募集说明书：{needle} 被放行")
    for needle in ("主营业务收入构成表", "主营业务成本构成表", "毛利润及毛利率构成表"):
        hit = [o for o in kcz if needle in o["target_table_title"]]
        check(hit and all(o["target_title_kind"] == REL.TITLE_KIND_EXPLICIT
                          and o["target_title_verifiable"] is True for o in hit),
              f"2026 {needle}：表题是显式表题（具名表材料）")
    cost = [o for o in kcz if "主营业务成本构成表" in o["target_table_title"]]
    check(len(cost) == 1 and cost[0].get("continuation_block_read") is True,
          "2026 成本构成表：表题在块末、表体在下一块 ⇒ 跨块对象带真表题")
    if cost:
        check(len(cost[0]["target_body_row_texts"]) == 5,
              "2026 成本构成表表体 5 行逐字保留")
    check(all(o["numeric_authority"] is False
              and o["financial_authority_claimed"] is False
              for o in y25 + y24 + kcz),
          "三份文档的全部放行对象都不带数字/财务权威")
    return 0


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    _check_release_basics(check, details)
    _check_lead_column(check, details)
    _check_wide_subcolumn(check, details)
    _check_wrap_fragment(check, details)
    _check_continuation(check, details)
    _check_refusals(check, details)
    _check_identity(check, details)
    _check_title_kind(check, details)
    _check_material_not_authority(check, details)
    _check_source_spans(check, details)
    skipped += _check_real_documents(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

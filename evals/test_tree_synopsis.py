# -*- coding: utf-8 -*-
"""TS4-A 节点导航简介（`document_structure.synopsis`）验收：T23–T28（计划 §18.13）。

覆盖（全部为纯离线"构造 + 断言"：无网络、无数据库写入、无 LLM、无 PDF 引擎、
不依赖执行顺序）：

T23 **头部未覆盖、尾部覆盖**：span 开头没有来源覆盖、只有尾部被覆盖时，**禁止**从
    `cs = 0` 出片段；片段起点必须等于可引用区间的起点。
T24 **多行 span 仅部分行可引用**：未覆盖来源行不得进入任何片段；`normalization-only`
    造成的行间合成空格可被**透明跨越**，但 `non_citable` / `uncovered` 的真实来源
    空洞一律不可跨。
T25 **多条重叠 Evidence 不重复计数**：覆盖计数等于区间**并集**长度并严格小于逐条
    求和；同一条 Evidence 被重复引用不得重复计数；`confidence` 与覆盖度无关。
T26 **多行完整来源覆盖正向**：两行、且同一行上有多个 `LayoutSpan`，全部 required
    content 可引用、只有合成空格属于 `normalization-only` → 必须桥接出跨空格的
    effective 区间且 coverage 完整；TS4-A 下 eligibility / completion 仍为 False，
    B 阶段分支只在已裁决阈值时另行断言；删掉一个真实来源字符即失败。
T27 **`MIN_SNIPPET_CHARS` 一致性**：可引用区间短于 20 字 → 无片段且
    `reason_code == "length_exceeded"`；**不得**出现短于 20 字的片段（唯一口径）；
    过长且窗口内无句末标点同样只能诚实放弃，不得硬截断。
T27b **多 span 节点级配额**：同一节点 ≥2 个 span 时按源锚点稳定排序；3 条 / 300 字
    配额是**节点级**的，只初始化一次、不得按 span 重置；输入 span 顺序打乱后简介
    字节相同。
T28 **表格 provisional 不进 Synopsis**：`table_inside` / `table_adjacency` 均不产生
    片段；每条范围仍有 disposition、原文、定位器与全量 component；模拟未来不可变
    decision overlay 后原 disposition（及其简介）一个字节都不变。

判定口径（§18.8.3 / §18.8.6）：片段 `[cs, ce)` 必须落在**单个** effective 可引用区间
内、逐字取自 `span.normalized_text`；`length_exceeded` 的唯一定义是"长度约束无法满足"
（既含过短也含"过长且无句末标点"）。全部断言只读被测对象的公开行为与 typed 记录，
不读取任何"自报结论"字段作为通过依据。

本文件的断言都构造了**对照支**（同一文本在另一种覆盖下必须给出相反结果），因此
删掉对应的生产逻辑（不从 0 起始、跨空洞拦截、并集合并、配额不重置、表格不产片段）
必然使本文件失败，而不是恒真。
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
from unittest import mock

import document_structure

from document_structure import span_policy as sp
from document_structure import span_schema as SC
from document_structure import synopsis as SY
from document_structure import versions as V
from document_structure.canonical import canonical_json
from document_structure.normalization import tight
from document_structure.schema import (
    LayoutLine,
    LayoutPage,
    LayoutSpan,
    OutlineSpan,
    PageLayout,
)

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent


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
        text = str(e)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
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
# 固定装置（公司无关；不含任何真实公司 / case / 固定业务页码 / 答案关键词）
# ---------------------------------------------------------------------------

_NODE_A = "nd-0001"
_NODE_B = "nd-0002"
_OUTLINE_LOCATOR = "loc-do-fixture-0001"
_DOC_ID = "doc-fixture-0001"
_DOC_SHA = hashlib.sha256(b"ts4-synopsis-fixture").hexdigest()
_DOC_VERSION = "sha256-" + _DOC_SHA[:16]
_COMPANY_ID = "company-fixture"
_ESV = "set-fixture-0001"
_BBOX = (0.0, 0.0, 10.0, 10.0)
_PAGE_W = 600.0
_PAGE_H = 800.0
_SPAN_BUILDER = V.TS4_BODY_SPAN_BUILDER_VERSION

_POLICY = None

#: 全量片段样本（T27 的"不得短于 20 字"要在**所有**路径上成立，因此逐份收集）。
_SNIPPET_SAMPLES: list[str] = []
#: 全量节点配额样本 `(node_id, 总字符数, 片段条数)`（T27b）。
_NODE_TOTALS: list[tuple[str, int, int]] = []


def _policy():
    """解析并钉住 TS4-A 的分布口径策略（只读固定目录内的版本化 JSON）。"""
    global _POLICY
    if _POLICY is None:
        _POLICY = sp.resolve_qualification_policy()
    return _POLICY


def _make_span(*, text, lines, node_id=_NODE_A, role="body", confidence=0.75,
               component_evidence_refs=(), alignment_ids=()):
    """构造一个导航可采信的 `OutlineSpan`（`char_range` 覆盖全文）。"""
    return OutlineSpan.create(
        document_outline_locator=_OUTLINE_LOCATOR, node_id=node_id,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_ESV, role=role,
        start_anchor=(lines[0][0], lines[0][1], _BBOX),
        end_anchor=(lines[-1][0], lines[-1][1], _BBOX),
        normalized_text=text, layout_line_refs=tuple(lines),
        component_evidence_refs=tuple(component_evidence_refs),
        alignment_ids=tuple(alignment_ids), confidence=confidence,
        span_builder_version=_SPAN_BUILDER)


def _coverage(span, *, citable=(), norm=(), non_citable=(), uncovered=(),
              components=()):
    """按四分类构造 `SpanCitableCoverage`（`create` 内部做并集合并与重算）。"""
    return SC.SpanCitableCoverage.create(
        span_id=span.span_id, span_local_length=len(span.normalized_text),
        normalization_only_intervals=norm,
        citable_source_intervals=citable,
        non_citable_source_intervals=non_citable,
        uncovered_source_intervals=uncovered,
        covering_component_ids=tuple(components))


def _pairs(intervals):
    return [(iv.start, iv.end) for iv in intervals]


def _ranges(ranges_):
    return [r.as_tuple() for r in ranges_]


def _sample(syn):
    """登记一份简介的片段与节点级配额统计（供 T27 / T27b 的全量口径断言）。"""
    quoted = False
    for sn in syn.snippets:
        _SNIPPET_SAMPLES.append(sn.text)
        quoted = True
    if syn.status == "available":
        _NODE_TOTALS.append((syn.node_id,
                             sum(len(sn.text) for sn in syn.snippets),
                             len(syn.snippets)))
    return quoted


def _node_synopsis(*, node_id, spans, coverages, kind_counts):
    syn = SY.build_node_synopsis(
        node_id=node_id, spans=spans, coverages=coverages,
        kind_counts=kind_counts, policy=_policy())
    _sample(syn)
    return syn


# ---------------------------------------------------------------------------
# 版式装置（T26 的"同一行多个 LayoutSpan"与 T28 的"原文仍可定位"）
# ---------------------------------------------------------------------------

def _line_bbox(index: int):
    return (40.0, 8.0 + 20.0 * index, 560.0, 24.0 + 20.0 * index)


def _split_layout_spans(text: str, line_index: int, parts: int) -> tuple:
    """把一行文本切成 `parts` 个 `LayoutSpan`（每个片段与其行内切片逐字相等）。"""
    step = max(1, (len(text) + parts - 1) // parts)
    out = []
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + step)
        y0 = 8.0 + 20.0 * line_index
        out.append(LayoutSpan(
            text=text[pos:end],
            bbox=(40.0 + 4.0 * pos, y0, 40.0 + 4.0 * end, y0 + 16.0),
            font="F1", size=10.0, is_bold=False,
            char_start=pos, char_end=end))
        pos = end
    return tuple(out)


def _make_layout(line_texts, *, parts=1) -> PageLayout:
    lines = tuple(
        LayoutLine(line_index=i, bbox=_line_bbox(i),
                  spans=_split_layout_spans(t, i, parts), text=t,
                  is_furniture=False, furniture_kind=None,
                  reading_order=i, column_index=0)
        for i, t in enumerate(line_texts))
    page = LayoutPage(page_number=1, width=_PAGE_W, height=_PAGE_H, rotation=0,
                      lines=lines, has_text_layer=True)
    return PageLayout.create(document_id=_DOC_ID, document_version=_DOC_VERSION,
                             company_id=_COMPANY_ID, source_file_sha256=_DOC_SHA,
                             pages=(page,))


# ---------------------------------------------------------------------------
# 文本装置（长度一律由 len() 派生，避免手数字符）
# ---------------------------------------------------------------------------

#: 纯中文、**不含任何句末标点**的可引用正文块（5 字），用于"整段取用 / 长度上界"。
_NO_PUNCT = "无标点正文"

_T23_HEAD = "开头这段没有任何来源覆盖。"
_T23_TAIL = "尾部这一段确有证据支撑，可以摘出连续原文。"

_T24_L1 = "第一行有来源覆盖，这一段文字足够长以便形成片段。"
_T24_L2 = "第二行没有来源覆盖。"
_T24_L3 = "第二行内容虽有来源记录但判为拒绝对齐，不得引用。"
_T24_L4 = "第二行同样全部有来源覆盖，只有行间合成空格是折叠出来的。"

_T27_SHORT = "短句有句末标点但不足二十字。"
_T27_EXACT20 = _NO_PUNCT * 4          # 恰好 20 字，无句末标点
_T27_LONG200 = _NO_PUNCT * 40         # 200 字，无句末标点（禁止硬截断）

_T28_LINE_TEXTS = (
    "表一项目行甲字段数值",
    "表一项目行乙字段数值",
    "表一项目行丙字段数值",
    "表一项目行丁字段数值",
    "表一相邻行戊字段数值",
    "表一相邻行己字段数值",
)
_T28_LINE_CHARS = 10
_T28_INSIDE_TIGHT = _T28_LINE_CHARS * 3      # 行 1..3
_T28_ADJACENT_TIGHT = _T28_LINE_CHARS * 2    # 行 4..5


# ---------------------------------------------------------------------------
# T23 —— 头部未覆盖、尾部覆盖
# ---------------------------------------------------------------------------

def _test_t23_head_uncovered_tail_covered():
    head_len = len(_T23_HEAD)
    text = _T23_HEAD + _T23_TAIL
    span = _make_span(text=text, lines=((1, 10),))
    cov = _coverage(span, citable=[(head_len, len(text))],
                    uncovered=[(0, head_len)])

    check(_pairs(cov.effective_citable_intervals) == [(head_len, len(text))],
          "T23 前置：有效可引用区间只有一个且起点 > 0")
    check(cov.uncovered_source_chars == head_len
          and cov.citable_source_chars == len(_T23_TAIL),
          "T23 前置：头部计入未覆盖、尾部计入可引用")

    syn = _node_synopsis(node_id=_NODE_A, spans=[span],
                         coverages={span.span_id: cov},
                         kind_counts={"regular": 1})
    check(syn.status == "available", "T23 尾部可引用且长度达标时简介必须可用")
    check(len(syn.snippets) == 1, "T23 恰产出一条片段")
    sn = syn.snippets[0] if syn.snippets else None
    check(sn is not None and sn.char_start == head_len,
          "T23 片段起点必须等于最大（唯一）可引用区间的起点，不得从 cs=0 出片段")
    check(sn is not None and sn.char_start > 0, "T23 禁止以 cs=0 起始的片段")
    check(sn is not None and sn.text == _T23_TAIL,
          "T23 片段必须是尾部原文的逐字切片（抽取式回指）")
    check(sn is not None and _T23_HEAD[:4] not in sn.text,
          "T23 未覆盖的头部不得进入片段")
    for i, s in enumerate(syn.snippets):
        check(s.char_start >= head_len,
              f"T23 片段[{i}] 必须完全落在可引用区间内（起点不早于 {head_len}）")

    # 对照支：同一文本在**全文可引用**时片段必须从 0 起 —— 证明上一条断言并非恒真。
    cov_full = _coverage(span, citable=[(0, len(text))])
    control = _node_synopsis(node_id=_NODE_A, spans=[span],
                             coverages={span.span_id: cov_full},
                             kind_counts={"regular": 1})
    cs = control.snippets[0].char_start if control.snippets else None
    check(cs == 0 and control.snippets[0].text == text,
          "T23 对照：全文可引用时片段起点为 0（覆盖变化确实改变片段起点）")

    val = SY.verify_synopsis_sources(
        syn, span_registry={span.span_id: span},
        coverage_registry={span.span_id: cov}, verified_policy=_policy())
    check(val.ok is True and val.problems == (),
          "T23 逐片段来源复核通过（落在单个有效可引用区间内）")
    check(all(sc.covering_effective_interval == (head_len, len(text))
              for sc in val.snippet_checks),
          "T23 复核结论必须指出片段所属的那个有效可引用区间")


# ---------------------------------------------------------------------------
# T24 —— 多行 span 仅部分行可引用
# ---------------------------------------------------------------------------

def _test_t24_partial_lines():
    l1 = _T24_L1
    l1_len = len(l1)

    # (a) 未覆盖来源行（第 2 行没有来源）不得进入片段。
    text_a = l1 + " " + _T24_L2
    span_a = _make_span(text=text_a, lines=((1, 0), (1, 1)))
    cov_a = _coverage(span_a, citable=[(0, l1_len)],
                      norm=[(l1_len, l1_len + 1)],
                      uncovered=[(l1_len + 1, len(text_a))])
    check(_pairs(cov_a.effective_citable_intervals) == [(0, l1_len)],
          "T24 未覆盖行不得被桥接进有效可引用区间")
    syn_a = _node_synopsis(node_id=_NODE_A, spans=[span_a],
                           coverages={span_a.span_id: cov_a},
                           kind_counts={"regular": 1})
    sna = syn_a.snippets[0] if syn_a.snippets else None
    check(sna is not None and sna.text == l1,
          "T24 未覆盖来源行不得进入片段（片段必须只含第 1 行）")
    check(sna is not None and sna.char_end == l1_len,
          "T24 片段终点必须停在可引用区间末端，不得跨过未覆盖来源空洞")
    check(sna is not None and _T24_L2[:3] not in sna.text,
          "T24 未覆盖行的原文不得以任何形式出现在片段里")

    # (b) non-citable 真实来源字符（拒绝对齐）同样不可跨。
    hole = l1_len
    text_b = l1 + "密" + _T24_L3
    span_b = _make_span(text=text_b, lines=((1, 2), (1, 3)))
    cov_b = _coverage(span_b, citable=[(0, hole), (hole + 1, len(text_b))],
                      non_citable=[(hole, hole + 1)])
    check(_pairs(cov_b.effective_citable_intervals)
          == [(0, hole), (hole + 1, len(text_b))],
          "T24 non-citable 来源字符必须把有效可引用区间切成两段")
    syn_b = _node_synopsis(node_id=_NODE_A, spans=[span_b],
                           coverages={span_b.span_id: cov_b},
                           kind_counts={"regular": 1})
    check(_ranges(SY.select_snippet_ranges(
        span_b, cov_b, policy=_policy(), budget=[3, 300]))
        == [(span_b.span_id, 0, hole),
            (span_b.span_id, hole + 1, len(text_b))],
        "T24 两段有效区间各出一条片段，且都不跨 non-citable 字符")
    check(all(not (s.char_start <= hole < s.char_end) for s in syn_b.snippets),
          "T24 不可引用的真实来源字符不得进入任何片段")
    check(cov_b.non_citable_source_chars == 1
          and cov_b.citable_source_chars == len(text_b) - 1,
          "T24 前置：恰有一个真实来源字符不可引用")

    # (c) normalization-only（行间合成空格）可被透明跨越。
    text_c = l1 + " " + _T24_L4
    span_c = _make_span(text=text_c, lines=((1, 4), (1, 5)))
    cov_c = _coverage(span_c, citable=[(0, l1_len), (l1_len + 1, len(text_c))],
                      norm=[(l1_len, l1_len + 1)])
    check(_pairs(cov_c.effective_citable_intervals) == [(0, len(text_c))],
          "T24 normalization-only 分隔符必须被桥接成一个有效可引用区间")
    syn_c = _node_synopsis(node_id=_NODE_A, spans=[span_c],
                           coverages={span_c.span_id: cov_c},
                           kind_counts={"regular": 1})
    snc = syn_c.snippets[0] if syn_c.snippets else None
    check(snc is not None and snc.text == text_c,
          "T24 合成空格可透明跨越：片段覆盖两行全文")
    check(snc is not None and snc.char_start <= l1_len < snc.char_end,
          "T24 跨空格的片段必须真实包含那个折叠出来的空格位置")
    check(cov_c.normalization_only_chars == 1
          and cov_c.required_content_chars == len(text_c) - 1,
          "T24 归一化空白只占 1 字且不计入 required content")

    # 对照：同一份覆盖做一次来源复核，(b) 的两条片段都必须通过。
    val_b = SY.verify_synopsis_sources(
        syn_b, span_registry={span_b.span_id: span_b},
        coverage_registry={span_b.span_id: cov_b}, verified_policy=_policy())
    check(val_b.ok is True and val_b.problems == (),
          "T24 逐片段来源复核通过（每条片段落在单个有效区间内）")


# ---------------------------------------------------------------------------
# T25 —— 多条重叠 Evidence 不重复计数
# ---------------------------------------------------------------------------

def _test_t25_overlapping_evidence():
    check(SC.interval_length([(0, 30), (10, 40)]) == 60,
          "T25 前置：逐条求和的字数为 60")
    merged = SC.merge_intervals([(0, 30), (10, 40)])
    check(_pairs(merged) == [(0, 40)],
          "T25 重叠区间必须合并成并集（(0,30) ∪ (10,40) = (0,40)）")
    check(SC.interval_length(merged) == 40
          and SC.interval_length(merged) < SC.interval_length([(0, 30), (10, 40)]),
          "T25 覆盖计数等于并集长度（40），严格小于逐条求和（60）")

    text = _NO_PUNCT * 8                      # 40 字
    span = _make_span(text=text, lines=((1, 11),))
    cov_overlap = _coverage(span, citable=[(0, 30), (10, 40)])
    cov_merged = _coverage(span, citable=[(0, 40)])

    check(cov_overlap.citable_source_chars == 40,
          "T25 覆盖计数必须等于并集长度，不得按 Evidence 条数累加")
    check(cov_overlap.citable_source_chars < 30 + 30,
          "T25 覆盖计数严格小于逐条求和（不得重复计数重叠段）")
    check(cov_overlap.effective_citable_chars == 40,
          "T25 有效可引用字符数同样只算并集一次")
    check(cov_overlap.citable_source_chars <= cov_overlap.span_local_length,
          "T25 覆盖计数不得越出 span 局部长度（禁止 min(1.0, ...) 式掩盖）")
    check(cov_overlap.coverage_id == cov_merged.coverage_id,
          "T25 叠放区间与预合并区间必须是同一条覆盖记录（唯一合并口径）")

    pol = _policy()
    r_overlap = SY.select_snippet_ranges(span, cov_overlap, policy=pol,
                                         budget=[3, 300])
    r_merged = SY.select_snippet_ranges(span, cov_merged, policy=pol,
                                        budget=[3, 300])
    check(_ranges(r_overlap) == _ranges(r_merged)
          and _ranges(r_overlap) == [(span.span_id, 0, 40)],
          "T25 重叠证据不得改变片段选取结果（仍然整段取用 40 字）")

    cov_ids = _coverage(span, citable=[(0, 40)],
                        components=("sc-2", "sc-1", "sc-1"))
    check(cov_ids.covering_component_ids == ("sc-1", "sc-2"),
          "T25 同一条 Evidence 被重复引用必须去重且按字典序升序")
    check(cov_ids.citable_source_chars == 40,
          "T25 重复引用不得抬高覆盖计数（admitted component 计数与可引用字数无关）")

    # confidence 只表达结构边界 / 节点归属，永不随 Evidence 条数或覆盖度变化。
    s_plain = _make_span(text=text, lines=((1, 11),), confidence=0.75)
    s_ev = _make_span(text=text, lines=((1, 11),), confidence=0.75,
                      component_evidence_refs=(("ev-a", 0, 5), ("ev-b", 5, 9)),
                      alignment_ids=("ta-1", "ta-2"))
    check(s_plain.confidence == 0.75 and s_ev.confidence == 0.75,
          "T25 confidence 不得由 Evidence 条数 / 引用字符数计算")
    check(s_plain.span_id != s_ev.span_id,
          "T25 对照：component Evidence 引用进入 span 身份（故上一条比较的是两个不同 span）")
    zero = _coverage(span, uncovered=[(0, 40)])
    check(zero.effective_citable_chars == 0
          and zero.coverage_id != cov_merged.coverage_id,
          "T25 前置：零覆盖与满覆盖是两条不同的覆盖记录")
    check(s_plain.confidence == s_ev.confidence == 0.75,
          "T25 覆盖度从 0 到满覆盖都不改变 confidence（两条口径不得合并）")

    syn = _node_synopsis(node_id=_NODE_A, spans=[span],
                         coverages={span.span_id: cov_overlap},
                         kind_counts={"regular": 1})
    check(len(syn.snippets) == 1,
          "T25 重叠证据只产出一条片段（不得按 Evidence 条数复制）")


# ---------------------------------------------------------------------------
# T26 —— 多行完整来源覆盖正向（含 A/B 门与"删一个真实来源字符即失败"）
# ---------------------------------------------------------------------------

def _test_t26_full_multiline_positive():
    l1 = _T24_L1
    l1_len = len(l1)
    text = l1 + " " + _T24_L4
    span = _make_span(text=text, lines=((1, 0), (1, 1)))
    cov = _coverage(span, citable=[(0, l1_len), (l1_len + 1, len(text))],
                    norm=[(l1_len, l1_len + 1)])

    check(_pairs(cov.effective_citable_intervals) == [(0, len(text))],
          "T26 合成空格必须被桥接：两行全部 required content 形成单个 effective 区间")
    check(cov.normalization_only_chars == 1,
          "T26 只有行间合成空格属于 normalization-only")
    check(cov.uncovered_source_chars == 0 and cov.non_citable_source_chars == 0,
          "T26 coverage 完整：既无未覆盖来源也无不可引用来源")
    check(cov.required_content_chars == cov.citable_source_chars == len(text) - 1,
          "T26 coverage 完整：required content 与 citable source 逐字相等")

    syn = _node_synopsis(node_id=_NODE_A, spans=[span],
                         coverages={span.span_id: cov},
                         kind_counts={"regular": 1})
    check(syn.status == "available", "T26 完整覆盖必须产出可用简介")
    sn = syn.snippets[0] if syn.snippets else None
    check(sn is not None and sn.char_start == 0 and sn.char_end == len(text),
          "T26 跨空格片段必须覆盖整段（两行 + 折叠空格）")

    layout = _make_layout((l1, _T24_L4), parts=3)
    check(len(layout.pages[0].lines[0].spans) >= 2,
          "T26 前置：同一行上确有多条 LayoutSpan（不是每行一段）")
    check(layout.line_at(1, 0).text == l1 and layout.line_at(1, 1).text == _T24_L4,
          "T26 前置：版式层的两行原文与 span 规范化文本一致")

    val = SY.verify_synopsis_sources(
        syn, span_registry={span.span_id: span},
        coverage_registry={span.span_id: cov}, verified_policy=_policy(),
        page_layout=layout, records_by_id={})
    check(val.ok is True and val.problems == (),
          "T26 逐片段来源复核通过（含真实 PageLayout 上下文）")
    check([sc.covering_effective_interval for sc in val.snippet_checks]
          == [(0, len(text))],
          "T26 复核必须确认片段落在那个跨空格的单个有效区间内")

    # A/B 真值表：完成资格只由阈值裁决派生。
    gate = sp.ab_gate_truth_table()
    check(gate["completion_enabled"] is gate["threshold_decided"]
          and gate["set_complete_supported"] is gate["threshold_decided"],
          "T26 A/B 真值表自洽（四项判定同源于 SPAN_CONFIDENCE_MIN）")
    pol = _policy()
    if not gate["threshold_decided"]:
        # TS4-A（本轮实际执行的分支）
        check(pol.stage == "distribution_only"
              and pol.span_confidence_min is None
              and pol.completion_enabled is False
              and pol.set_complete_supported is False,
              "T26 A 阶段策略必须是分布口径且不得开启 completion / set_complete")
        check(V.SPAN_CONFIDENCE_MIN is None,
              "T26 A 阶段 SPAN_CONFIDENCE_MIN 必须仍未裁决")
        check(span.is_structurally_eligible() is False,
              "T26 A 阶段即使 coverage 完整也拿不到结构资格（fail-closed）")
        check(span.can_support_set_complete(alignment_records=(),
                                            boundary_verified=True) is False,
              "T26 A 阶段完整覆盖的 span 不得支撑 set_complete")
    else:
        check(pol.stage == "threshold_enabled" and pol.completion_enabled is True
              and pol.set_complete_supported is True
              and pol.span_confidence_min == V.SPAN_CONFIDENCE_MIN,
              "T26 B 阶段（frozen policy）才允许断言 completion 可用，"
              "且阈值必须与全局常量同步")
        # 阈值是**必要条件**，不是"覆盖闭合即完成"：本条 fixture 的 span 置信度低于
        # 已裁决阈值，因此即使 coverage 完整也不得取得任何资格。
        check(span.confidence < pol.span_confidence_min,
              f"T26 反例前置：本 fixture span 置信度 {span.confidence} 必须低于已裁决"
              f"阈值 {pol.span_confidence_min}（否则下两条断言是空的）")
        check(span.is_structurally_eligible() is False,
              "T26 B 阶段：置信度低于阈值的 span 即使 coverage 完整也不得取得结构资格")
        check(span.can_support_set_complete(alignment_records=(),
                                            boundary_verified=True) is False,
              "T26 B 阶段：置信度低于阈值的 span 不得支撑 set_complete")
        # 对照：把同一个 span 的置信度抬到阈值即取得结构资格（证明上两条确实因阈值
        # 而失败，而不是因为 fixture 本身坏了）。
        lifted = _make_span(text=text, lines=((1, 0), (1, 1)),
                            confidence=pol.span_confidence_min)
        check(lifted.is_structurally_eligible() is True,
              f"T26 对照：置信度抬到 {pol.span_confidence_min} 时结构资格必须成立"
              "（否则上两条不是因为阈值而失败）")

    # 负例：删掉一个真实来源字符（该字符没有任何来源覆盖）即失败。
    hole = 10
    cov_bad = _coverage(span,
                        citable=[(0, hole), (hole + 1, l1_len),
                                 (l1_len + 1, len(text))],
                        norm=[(l1_len, l1_len + 1)],
                        uncovered=[(hole, hole + 1)])
    eff_bad = _pairs(cov_bad.effective_citable_intervals)
    check(eff_bad == [(0, hole), (hole + 1, len(text))]
          and (0, len(text)) not in eff_bad,
          "T26 删除一个真实来源字符后不得再出现跨全文的 effective 区间")
    check(cov_bad.uncovered_source_chars == 1
          and cov_bad.required_content_chars != cov_bad.citable_source_chars,
          "T26 真实来源空洞必须使 coverage 不再完整")
    bad = _node_synopsis(node_id=_NODE_A, spans=[span],
                         coverages={span.span_id: cov_bad},
                         kind_counts={"regular": 1})
    check(all(not (s.char_start <= hole < s.char_end) for s in bad.snippets),
          "T26 空洞处的真实来源字符不得进入任何片段")
    check(len(bad.snippets) == 1 and bad.snippets[0].char_start == hole + 1,
          "T26 片段必须从空洞之后的第一个可引用字符起（跳过真实来源空洞）")
    check(all(s.text != text for s in bad.snippets),
          "T26 对照：完整覆盖时存在的整段片段在空洞出现后不得再出现")


# ---------------------------------------------------------------------------
# T27 —— MIN_SNIPPET_CHARS 一致性（唯一口径）
# ---------------------------------------------------------------------------

def _test_t27_min_snippet_consistency():
    pol = _policy()
    check(SY.MIN_SNIPPET_CHARS == 20 and pol.min_snippet_chars == SY.MIN_SNIPPET_CHARS,
          "T27 代码常量与策略记录里的 min_snippet_chars 必须是同一个 20")
    check(SY.MAX_SNIPPET_CHARS == pol.max_snippet_chars == 120,
          "T27 max_snippet_chars 常量与策略记录必须同为 120")
    check(SY.MAX_SNIPPETS_PER_NODE == pol.max_snippets_per_node == 3,
          "T27 max_snippets_per_node 常量与策略记录必须同为 3")
    check(SY.MAX_TOTAL_SNIPPET_CHARS == pol.max_total_snippet_chars == 300,
          "T27 max_total_snippet_chars 常量与策略记录必须同为 300")
    check(tuple(pol.sentence_terminators) == tuple(SY.SENTENCE_TERMINATORS)
          and tuple(pol.closing_quotes) == tuple(SY.CLOSING_QUOTES),
          "T27 句末标点与闭引号必须是同一份规格")

    # (a) 可引用区间短于 20 字：即便有句末标点也不得产出片段。
    check(len(_T27_SHORT) < SY.MIN_SNIPPET_CHARS,
          "T27 前置：短区间样本短于 20 字且带句末标点")
    short = _make_span(text=_T27_SHORT, lines=((1, 12),))
    cov_short = _coverage(short, citable=[(0, len(_T27_SHORT))])
    check(SY.select_snippet_ranges(short, cov_short, policy=pol,
                                   budget=[3, 300]) == (),
          "T27 短于 20 字的可引用区间不得产出任何片段（含句末标点也不行）")
    syn_short = _node_synopsis(node_id=_NODE_A, spans=[short],
                               coverages={short.span_id: cov_short},
                               kind_counts={"regular": 1})
    check(syn_short.status == "synopsis_unavailable"
          and syn_short.reason_code == "length_exceeded",
          "T27 短区间必须诚实判 length_exceeded")
    check(syn_short.snippets == ()
          and syn_short.source_span_ids == (short.span_id,),
          "T27 情形 5 仍须给出可采信 span 的来源序 id（缺口可审计）")

    # (b) 恰好 20 字：下界是闭的，"整段取用"分支同样受它约束。
    check(len(_T27_EXACT20) == SY.MIN_SNIPPET_CHARS,
          "T27 前置：边界样本恰为 20 字且无句末标点")
    exact = _make_span(text=_T27_EXACT20, lines=((1, 13),))
    cov_exact = _coverage(exact, citable=[(0, len(_T27_EXACT20))])
    rng_exact = SY.select_snippet_ranges(exact, cov_exact, policy=pol,
                                         budget=[3, 300])
    check(len(rng_exact) == 1 and rng_exact[0].length == SY.MIN_SNIPPET_CHARS,
          "T27 恰好 20 字的区间整段取用必须产出一条恰 20 字的片段")
    syn_exact = _node_synopsis(node_id=_NODE_A, spans=[exact],
                               coverages={exact.span_id: cov_exact},
                               kind_counts={"regular": 1})
    check(syn_exact.status == "available"
          and len(syn_exact.snippets[0].text) == SY.MIN_SNIPPET_CHARS,
          "T27 下界为闭：20 字片段可用且不得被裁短")

    # (c) 过长且窗口内无句末标点：禁止硬截断，同样只能判 length_exceeded。
    check(len(_T27_LONG200) > SY.MAX_SNIPPET_CHARS,
          "T27 前置：长区间样本超过 120 字且无句末标点")
    long_span = _make_span(text=_T27_LONG200, lines=((1, 14),))
    cov_long = _coverage(long_span, citable=[(0, len(_T27_LONG200))])
    check(SY.select_snippet_ranges(long_span, cov_long, policy=pol,
                                   budget=[3, 300]) == (),
          "T27 过长且窗口内无句末标点时必须放弃，禁止硬截断到 120 字")
    syn_long = _node_synopsis(node_id=_NODE_A, spans=[long_span],
                              coverages={long_span.span_id: cov_long},
                              kind_counts={"regular": 1})
    check(syn_long.status == "synopsis_unavailable"
          and syn_long.reason_code == "length_exceeded",
          "T27 length_exceeded 是唯一定义（过短与过长且无标点同码）")

    # reason_code 判定必须是全函数、互斥、首个命中（§18.8.6 的六条）。
    def _reason(kind_counts, admissible, citable, count):
        return SY.unavailable_reason(
            node_id=_NODE_A, kind_counts=kind_counts,
            has_admissible_span=admissible, has_citable_coverage=citable,
            snippet_count=count)

    check(_reason({}, False, False, 0) == "no_span",
          "T27 情形 1：无 regular span 且无任何处置范围 → no_span")
    check(_reason({"table_inside": 1}, False, False, 0)
          == "table_only_pending_ts5",
          "T27 情形 2：只有表格范围 → table_only_pending_ts5")
    check(_reason({"table_adjacency": 1, "empty": 2}, False, False, 0)
          == "table_only_pending_ts5",
          "T27 情形 2：表格范围与 empty 同时存在仍取表格原因（首个命中）")
    check(_reason({"empty": 1}, False, False, 0) == "empty_text",
          "T27 情形 3：只有 empty → empty_text")
    check(_reason({"regular": 1}, False, True, 0) == "no_span",
          "T27 有 regular span 但无任何可采信来源 → no_span")
    check(_reason({"regular": 1}, True, False, 0) == "alignment_failed",
          "T27 情形 4：可引用并集为空 → alignment_failed")
    check(_reason({"regular": 1}, True, True, 0) == "length_exceeded",
          "T27 情形 5：有可引用覆盖但无片段达标 → length_exceeded")
    check(_reason({"regular": 1}, True, True, 1) is None,
          "T27 情形 6：已产出至少一条合法片段才为 available")
    raises(lambda: _reason({"bogus": 1}, True, True, 1), SY.SynopsisError,
           "未登记", "T27 未登记的 range_kind 必须 fail-closed")
    raises(lambda: _reason({"regular": -1}, True, True, 1), SY.SynopsisError,
           "非负", "T27 负数计数必须 fail-closed")


# ---------------------------------------------------------------------------
# T27b —— 多 span 节点级配额
# ---------------------------------------------------------------------------

def _quota_text(mark: str) -> str:
    """3 段各 110 字的可引用块，段间各夹一个未覆盖来源字符。"""
    chunk = mark * 110
    return chunk + "隔" + chunk + "隔" + chunk


def _test_t27b_node_budget_and_order():
    pol = _policy()
    text_a = _quota_text("甲")
    text_b = _quota_text("乙")
    span_a = _make_span(text=text_a, lines=((1, 20),))
    span_b = _make_span(text=text_b, lines=((1, 30),))
    cov_a = _coverage(span_a, citable=[(0, 110), (111, 221), (222, 332)],
                      uncovered=[(110, 111), (221, 222)])
    cov_b = _coverage(span_b, citable=[(0, 110), (111, 221), (222, 332)],
                      uncovered=[(110, 111), (221, 222)])
    covers = {span_a.span_id: cov_a, span_b.span_id: cov_b}

    check(span_a.span_id != span_b.span_id
          and span_a.start_anchor[1] < span_b.start_anchor[1],
          "T27b 前置：两个 span 属于同一节点但源锚点不同")

    # (1) 稳定排序：与输入顺序无关。
    check(SY.admissible_source_spans([span_a, span_b], node_id=_NODE_A)
          == (span_a, span_b),
          "T27b 两个 span 必须按源锚点稳定升序排列")
    check(SY.admissible_source_spans([span_b, span_a], node_id=_NODE_A)
          == (span_a, span_b),
          "T27b 输入顺序打乱后排序结果必须相同（稳定排序）")

    # (2) 节点级配额只初始化一次，不得按 span 重置。
    syn_fwd = _node_synopsis(node_id=_NODE_A, spans=[span_a, span_b],
                             coverages=covers, kind_counts={"regular": 2})
    syn_rev = _node_synopsis(node_id=_NODE_A, spans=[span_b, span_a],
                             coverages=covers, kind_counts={"regular": 2})
    check(canonical_json(syn_fwd.to_dict()) == canonical_json(syn_rev.to_dict())
          and syn_fwd.synopsis_id == syn_rev.synopsis_id,
          "T27b 输入 span 顺序打乱后 synopsis 必须逐字节相同")
    check(syn_fwd.snippets[0].span_id == span_a.span_id,
          "T27b 首个片段必须来自源序在前的 span")

    per_span = []
    for sp_, cv_ in ((span_a, cov_a), (span_b, cov_b)):
        per_span.extend(SY.select_snippet_ranges(sp_, cv_, policy=pol,
                                                 budget=[pol.max_snippets_per_node,
                                                         pol.max_total_snippet_chars]))
    check(len(syn_fwd.snippets) == 2,
          "T27b 配额不得按 span 重置：两个 span 共享同一份配额后只剩 2 条")
    check(len(per_span) == 4 and len(per_span) > pol.max_snippets_per_node,
          "T27b 对照：按 span 各自重置会得到 4 条（超出节点级 3 条上限）")
    total_chars = sum(len(s.text) for s in syn_fwd.snippets)
    check(total_chars == 220 and total_chars <= pol.max_total_snippet_chars,
          "T27b 节点级总字符配额必须跨 span 累计（220 <= 300）")
    check(len(syn_fwd.snippets) <= pol.max_snippets_per_node,
          "T27b 节点级片段条数不得超过 3")
    check(all(len(s.text) >= pol.min_snippet_chars for s in syn_fwd.snippets),
          "T27b 受配额截断时不得留下半个片段（仍须满足长度下界）")

    # (3) 总字符配额本身必须生效：3×110 字 > 300。
    check(len(cov_a.effective_citable_intervals) == 3,
          "T27b 前置：单个 span 有 3 段各 110 字的有效可引用区间")
    one_span = SY.select_snippets([span_a], covers, policy=pol)
    check(len(one_span) == 2
          and sum(r.length for r in one_span) == 220,
          "T27b 300 字总配额必须生效（3×110 只能取 2 条，且不产出半个片段）")
    check(3 * 110 > pol.max_total_snippet_chars,
          "T27b 前置：3×110 字确实超过 300 字上限")

    # (4) 来源去重与索引：同一 span 多条片段只出一次 span_id；配额耗尽后后面的
    #     span 不再贡献片段，因此来源序里也不出现它。
    check(syn_fwd.source_span_ids == (span_a.span_id,)
          and [s.snippet_index for s in syn_fwd.snippets] == [0, 1],
          "T27b source_span_ids 必须是来源序去重、snippet_index 必须是节点级追加序")
    check(all(s.span_id == span_a.span_id for s in syn_fwd.snippets),
          "T27b 配额耗尽后排在后面的 span 不得再贡献片段")

    registry = {span_a.span_id: span_a, span_b.span_id: span_b}
    val = SY.verify_synopsis_sources(syn_fwd, span_registry=registry,
                                     coverage_registry=covers,
                                     verified_policy=pol)
    check(val.ok is True and val.problems == (),
          "T27b 逐片段来源复核通过（多 span 节点）")


# ---------------------------------------------------------------------------
# T28 —— 表格 provisional 不进 Synopsis
# ---------------------------------------------------------------------------

def _table_disposition(*, kind, start_line, end_line, line_count, tight_chars):
    if kind == "table_inside":
        scope, reason = "inside_table", "table_row_own"
    else:
        scope, reason = "adjacent_to_table", "table_row_adjacent"
    return SC.BodyRangeDisposition.create(
        range_kind=kind, node_id=_NODE_A, unassigned_reason=None,
        table_scope=scope, table_reason=reason, start_page=1,
        start_line=start_line, end_page=1, end_line=end_line,
        line_count=line_count, tight_char_count=tight_chars)


def _table_component(disposition, landing, block_id, block_len):
    return SC.SpanEvidenceComponent.create(
        evidence_block_id=block_id, terminal_kind="evidence_block",
        terminal_id=block_id, verdict="aligned",
        evidence_char_range=(0, block_len), landing=landing,
        admitted=False, admission_reason="landing_not_body_span",
        span_local_char_range=(0, block_len), node_id=_NODE_A,
        disposition_id=disposition.disposition_id)


@dataclasses.dataclass(frozen=True)
class _SimulatedTableRangeDecision:
    """（模拟）TS5 未来的不可变 decision overlay：只按 `disposition_id` 追加决定。"""
    disposition_id: str
    decision: str
    target_kind: str
    target_ref: str


def _test_t28_table_provisional():
    pol = _policy()
    check(all(len(t) == _T28_LINE_CHARS for t in _T28_LINE_TEXTS),
          "T28 前置：版式行样本长度一致（无空白字符，tight 长度等于原长度）")
    check(sum(len(t) for t in _T28_LINE_TEXTS[1:4]) == _T28_INSIDE_TIGHT
          and sum(len(t) for t in _T28_LINE_TEXTS[4:6]) == _T28_ADJACENT_TIGHT,
          "T28 前置：范围内原文的 tight 字数与硬编码期望一致")

    d_in = _table_disposition(kind="table_inside", start_line=1, end_line=3,
                             line_count=3, tight_chars=_T28_INSIDE_TIGHT)
    d_adj = _table_disposition(kind="table_adjacency", start_line=4, end_line=5,
                              line_count=2, tight_chars=_T28_ADJACENT_TIGHT)
    check(d_in.disposition_id != d_adj.disposition_id
          and d_in.span_id is None and d_adj.span_id is None,
          "T28 表格范围不得绑定 span_id（provisional：TS5 之前不产生 OutlineSpan）")
    check(d_in.disposition_locator and d_adj.disposition_locator
          and d_in.disposition_locator.startswith("loc-dr-")
          and d_in.disposition_id.startswith("dr-"),
          "T28 每条范围仍必须携带自己的定位器与不可变身份")
    check(SC.BodyRangeDisposition.from_dict(d_in.to_dict()) == d_in,
          "T28 范围记录必须可无损往返（原文与定位器不依赖外部状态）")

    layout = _make_layout(_T28_LINE_TEXTS, parts=1)
    for d, expected in ((d_in, _T28_INSIDE_TIGHT),
                        (d_adj, _T28_ADJACENT_TIGHT)):
        lines_ok = True
        for k in range(d.line_count):
            line = layout.line_at(d.start_page, d.start_line + k)
            if line is None or line.is_furniture:
                lines_ok = False
        check(lines_ok,
              f"T28 {d.range_kind} 的范围必须仍精确定位到真实正文行（原文保留）")
        actual = sum(len(tight(layout.line_at(1, d.start_line + k).text))
                     for k in range(d.line_count))
        check(d.tight_char_count == actual == expected,
              f"T28 {d.range_kind} 的 tight_char_count 必须等于范围内真实原文的 tight 字数")

    c_in = _table_component(d_in, "table_inside", "ev-tb-inside",
                            _T28_INSIDE_TIGHT)
    c_adj = _table_component(d_adj, "table_adjacency", "ev-tb-adjacent",
                             _T28_ADJACENT_TIGHT)
    for comp, disp, landing in ((c_in, d_in, "table_inside"),
                                (c_adj, d_adj, "table_adjacency")):
        check(comp.disposition_id == disp.disposition_id
              and comp.landing == landing and comp.span_id is None,
              f"T28 {landing} 必须有全量 component（绑定到范围而非 span）")
        check(comp.admitted is False
              and comp.admission_reason == "landing_not_body_span",
              f"T28 {landing} 的 Evidence 永不得被准入为可引用")

    caption = _make_span(text=_T28_LINE_TEXTS[0], lines=((1, 0),),
                         node_id=_NODE_A, role="table_caption")
    check(SY.navigation_admissible(caption) is False,
          "T28 表格题注角色不得成为导航简介来源")
    check(SY.admissible_source_spans([caption], node_id=_NODE_A) == (),
          "T28 表格题注不得进入可采信来源序")

    syn = SY.build_navigation_synopses(
        node_ids=[_NODE_A], spans=[caption], coverages=(),
        dispositions=(d_in, d_adj), policy=pol)[0]
    _sample(syn)
    check(syn.status == "synopsis_unavailable"
          and syn.reason_code == "table_only_pending_ts5",
          "T28 只有表格范围的节点必须判 table_only_pending_ts5")
    check(syn.snippets == () and syn.source_span_ids == (),
          "T28 table_inside / table_adjacency 均不得产生片段（情形 2 的来源序为空）")
    check(syn.is_navigation_only() is True,
          "T28 简介恒为只导航对象（不得充当证据）")

    caption_b = _make_span(text=_T28_LINE_TEXTS[0], lines=((1, 0),),
                           node_id=_NODE_B, role="table_caption")
    syn_b = _node_synopsis(node_id=_NODE_B, spans=[caption_b], coverages={},
                           kind_counts={})
    check(syn_b.status == "synopsis_unavailable"
          and syn_b.reason_code == "no_span" and syn_b.snippets == (),
          "T28 只有题注（无任何 regular / 表格范围）的节点判 no_span，不得凭空造片段")

    # 模拟 TS5 的不可变 decision overlay：追加决定，不得回写原记录。
    before = [canonical_json(d.to_dict()) for d in (d_in, d_adj)]
    syn_before = canonical_json(syn.to_dict())
    decisions = tuple(
        _SimulatedTableRangeDecision(
            disposition_id=d.disposition_id,
            decision=("inside_table" if d.range_kind == "table_inside"
                      else "adjacent_to_table"),
            target_kind="table_object", target_ref="tb-fixture-0001")
        for d in (d_in, d_adj))
    check(len({x.disposition_id for x in decisions}) == len(decisions),
          "T28 overlay 对每条 disposition 恰有一条决定（互斥覆盖）")
    by_id = {d.disposition_id: d for d in (d_in, d_adj)}
    check(all(x.disposition_id in by_id for x in decisions),
          "T28 overlay 只能指向已登记的 disposition_id（不得悬空）")

    after = [canonical_json(by_id[x.disposition_id].to_dict())
             for x in decisions]
    check(before == after,
          "T28 不可变 overlay 落地后原 disposition 必须逐字节不变")
    syn_after = SY.build_node_synopsis(
        node_id=_NODE_A, spans=[caption], coverages={},
        kind_counts={"table_inside": 1, "table_adjacency": 1}, policy=pol)
    check(canonical_json(syn_after.to_dict()) == syn_before,
          "T28 overlay 落地前简介不得改变（TS5 之前表格内容不进 Synopsis）")
    d_in_again = _table_disposition(kind="table_inside", start_line=1, end_line=3,
                                   line_count=3, tight_chars=_T28_INSIDE_TIGHT)
    check(d_in_again == d_in
          and d_in_again.disposition_id == d_in.disposition_id,
          "T28 overlay 之后重算的范围必须逐字节相同（决定不得进入 disposition 身份）")
    raises(lambda: setattr(d_in, "range_kind", "regular"),
           dataclasses.FrozenInstanceError, "cannot assign",
           "T28 disposition 必须不可原地改写（只有追加式 overlay）")


# ---------------------------------------------------------------------------
# 全量口径清扫与卫生（T27 / T27b 的"唯一口径"必须在所有路径上成立）
# ---------------------------------------------------------------------------

def _sweep_battery():
    """独立构造一批简介，保证"唯一口径"清扫断言不因执行顺序而空真。"""
    pol = _policy()

    head_len = len(_T23_HEAD)
    t23_text = _T23_HEAD + _T23_TAIL
    span = _make_span(text=t23_text, lines=((1, 10),))
    fixtures = [(span, _coverage(span, citable=[(head_len, len(t23_text))],
                                 uncovered=[(0, head_len)]))]

    l1 = _T24_L1
    t24_text = l1 + " " + _T24_L4
    span = _make_span(text=t24_text, lines=((1, 40), (1, 41)))
    fixtures.append((span, _coverage(
        span, citable=[(0, len(l1)), (len(l1) + 1, len(t24_text))],
        norm=[(len(l1), len(l1) + 1)])))

    span = _make_span(text=_NO_PUNCT * 8, lines=((1, 42),))
    fixtures.append((span, _coverage(span, citable=[(0, 30), (10, 40)])))

    span = _make_span(text=_T27_EXACT20, lines=((1, 43),))
    fixtures.append((span, _coverage(span, citable=[(0, len(_T27_EXACT20))])))

    span = _make_span(text=_T27_LONG200, lines=((1, 44),))
    fixtures.append((span, _coverage(span, citable=[(0, len(_T27_LONG200))])))

    for sp_, cv_ in fixtures:
        _sample(SY.build_node_synopsis(
            node_id=_NODE_A, spans=[sp_], coverages={sp_.span_id: cv_},
            kind_counts={"regular": 1}, policy=pol))


def _test_t27_sweep_and_hygiene():
    pol = _policy()
    _sweep_battery()
    check(len(_SNIPPET_SAMPLES) >= 5 and len(_NODE_TOTALS) >= 5,
          "T27 全量片段样本必须非空（否则下一条断言是空真）")
    short = [t for t in _SNIPPET_SAMPLES if len(t) < SY.MIN_SNIPPET_CHARS]
    long_ = [t for t in _SNIPPET_SAMPLES if len(t) > SY.MAX_SNIPPET_CHARS]
    check(not short,
          f"T27 任何路径都不得出现短于 {SY.MIN_SNIPPET_CHARS} 字的片段：{short[:2]}")
    check(not long_,
          f"T27 任何路径都不得出现长于 {SY.MAX_SNIPPET_CHARS} 字的片段：{long_[:2]}")

    check(len(_NODE_TOTALS) >= 4, "T27b 节点级配额样本必须非空")
    over_count = [x for x in _NODE_TOTALS if x[2] > pol.max_snippets_per_node]
    over_chars = [x for x in _NODE_TOTALS if x[1] > pol.max_total_snippet_chars]
    check(not over_count,
          f"T27b 任何节点都不得超过 {pol.max_snippets_per_node} 条片段：{over_count}")
    check(not over_chars,
          f"T27b 任何节点都不得超过 {pol.max_total_snippet_chars} 字总配额：{over_chars}")

    # 卫生：简介模块不得出现公司 / 答案专用 token，也不得引入 I/O、网络、LLM。
    src = (_PKG_DIR / "synopsis.py").read_text(encoding="utf-8")
    for token in ("300750", "宁德时代", "CATL", "case_id", "300750.SZ"):
        check(token not in src, f"synopsis.py 不得出现公司/答案专用 token {token!r}")
    for mod in ("sqlite3", "requests", "openai", "subprocess", "pdfplumber", "fitz",
                "httpx"):
        check(f"import {mod}" not in src and f"from {mod}" not in src,
              f"synopsis.py 不得 import {mod}（简介层无 I/O、网络、LLM）")
    check(SY.self_check()["problems"] == [],
          "T27 synopsis.self_check 不得报告任何问题")


# ---------------------------------------------------------------------------
# 变异检查：把对应的生产逻辑换成"去掉该规则"的版本，本文件的相关断言必须失败，
# 以此证明它们不是恒真断言。（只在进程内替换模块函数，不改动任何生产文件。）
# ---------------------------------------------------------------------------

def _test_assertion_falsifiability():
    pol = _policy()

    span_a = _make_span(text=_quota_text("甲"), lines=((1, 20),))
    span_b = _make_span(text=_quota_text("乙"), lines=((1, 30),))
    cov_a = _coverage(span_a, citable=[(0, 110), (111, 221), (222, 332)],
                      uncovered=[(110, 111), (221, 222)])
    cov_b = _coverage(span_b, citable=[(0, 110), (111, 221), (222, 332)],
                      uncovered=[(110, 111), (221, 222)])
    covers = {span_a.span_id: cov_a, span_b.span_id: cov_b}
    base = SY.build_node_synopsis(node_id=_NODE_A, spans=[span_a, span_b],
                                 coverages=covers, kind_counts={"regular": 2},
                                 policy=pol)
    check(len(base.snippets) == 2 and base.snippets[0].span_id == span_a.span_id,
          "变异检查前置：受配额约束的基准简介为 2 条且首条来自源序在前的 span")

    # (1) 去掉源锚点稳定排序 → "打乱顺序字节相同"必须失败。
    def _no_sort(spans, *, node_id=None):
        out = [s for s in spans if SY.navigation_admissible(s)]
        if node_id is not None:
            out = [s for s in out if s.node_id == node_id]
        return tuple(out)

    with mock.patch.object(SY, "admissible_source_spans", _no_sort):
        mutated = SY.build_node_synopsis(node_id=_NODE_A, spans=[span_b, span_a],
                                        coverages=covers,
                                        kind_counts={"regular": 2}, policy=pol)
    check(canonical_json(mutated.to_dict()) != canonical_json(base.to_dict())
          and mutated.snippets[0].span_id == span_b.span_id,
          "T27b 变异检查：去掉源锚点排序后'打乱顺序字节相同'必然失败")

    # (2) 配额按 span 重置 → 节点级 ≤3 条 / 总字符断言必须失败。
    def _per_span_budget(spans, coverages, *, policy):
        out = []
        for s in spans:
            out.extend(SY.select_snippet_ranges(
                s, coverages[s.span_id], policy=policy,
                budget=[policy.max_snippets_per_node,
                        policy.max_total_snippet_chars]))
        return tuple(out)

    with mock.patch.object(SY, "select_snippets", _per_span_budget):
        mutated_budget = SY.build_node_synopsis(
            node_id=_NODE_A, spans=[span_a, span_b], coverages=covers,
            kind_counts={"regular": 2}, policy=pol)
    check(len(mutated_budget.snippets) == 4
          and len(mutated_budget.snippets) > pol.max_snippets_per_node,
          "T27b 变异检查：配额按 span 重置会得到 4 条（节点级配额断言确实可失败）")

    # (3) 允许从 cs=0 出片段 → "不得从 0 起"断言必须失败。
    head_len = len(_T23_HEAD)
    t23_text = _T23_HEAD + _T23_TAIL
    span_t23 = _make_span(text=t23_text, lines=((1, 10),))
    cov_t23 = _coverage(span_t23, citable=[(head_len, len(t23_text))],
                        uncovered=[(0, head_len)])

    def _zero_start(span, coverage, *, policy, budget):
        if len(span.normalized_text) < policy.min_snippet_chars:
            return ()
        return (SY.SnippetRange(span.span_id, 0, len(span.normalized_text)),)

    with mock.patch.object(SY, "select_snippet_ranges", _zero_start):
        mutated_zero = SY.build_node_synopsis(
            node_id=_NODE_A, spans=[span_t23],
            coverages={span_t23.span_id: cov_t23},
            kind_counts={"regular": 1}, policy=pol)
    check(bool(mutated_zero.snippets)
          and mutated_zero.snippets[0].char_start == 0,
          "T23 变异检查：允许从 cs=0 出片段时该断言确实会失败")

    # (4) 放宽导航角色过滤 → 表格题注会被当成正文来源，no_span 断言必须失败。
    def _any_node_span(span):
        return (span.node_id is not None
                and span.char_range == (0, len(span.normalized_text)))

    caption = _make_span(text=_T24_L1, lines=((1, 0),), node_id=_NODE_B,
                         role="table_caption")
    caption_cov = _coverage(caption, citable=[(0, len(_T24_L1))])
    with mock.patch.object(SY, "navigation_admissible", _any_node_span):
        mutated_caption = SY.build_node_synopsis(
            node_id=_NODE_B, spans=[caption],
            coverages={caption.span_id: caption_cov},
            kind_counts={"regular": 1}, policy=pol)
    check(mutated_caption.status == "available" and mutated_caption.snippets,
          "T28 变异检查：放宽角色过滤后表格题注会被当成正文来源并产出片段")


# ---------------------------------------------------------------------------

def main() -> dict:
    _test_t23_head_uncovered_tail_covered()
    _test_t24_partial_lines()
    _test_t25_overlapping_evidence()
    _test_t26_full_multiline_positive()
    _test_t27_min_snippet_consistency()
    _test_t27b_node_budget_and_order()
    _test_t28_table_provisional()
    _test_t27_sweep_and_hygiene()
    _test_assertion_falsifiability()
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

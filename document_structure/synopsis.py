"""TS4 节点导航简介（计划 §18.8.3 / §18.8.4 / §18.8.6）。

**简介只导航，不作证据。** 本模块的全部产物都只回答一个问题："该节点的正文里，
哪一段**确有来源支撑**的连续原文可以摘出来让人快速认出这一节讲什么？"它**不**参与
可引用覆盖、**不**抬高 `confidence`、**不**参与 `set_complete`。

四条硬约束（缺一条即 fail-closed）：

1. **抽取式回指**：片段文本一律是 `span.normalized_text[cs:ce]` 的逐字切片，禁止
   生成、改写或拼接；
2. **单个有效可引用区间**：`[cs, ce)` 必须落在**同一个**
   `SpanCitableCoverage.effective_citable_intervals` 元素内（§18.8.3-1），因此
   "开头未覆盖只有尾部覆盖"时片段起点**不得**取 `0`，跨真实来源空洞也不可能成立；
3. **长度硬下界**：任何路径（含"整段取用"分支）都不得产出短于
   `min_snippet_chars` 的片段；长度约束无法满足即诚实判 `length_exceeded`；
4. **节点级全局配额**：`max_snippets_per_node` 与 `max_total_snippet_chars` 是**节点
   级**配额，多个 span 共享同一份，不得按 span 各自重置。

`navigation_admissible` 与 completion eligibility **是两件事**：TS4-A 的
`threshold=None` 下没有 completion 资格，但导航简介仍必须可用（§18.8.6）。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    sha256_canonical,
)
from document_structure.schema import (
    SYNOPSIS_REASON_CODES,
    NavigationSynopsis,
    OutlineSpan,
    SynopsisSnippet,
)
from document_structure.span_schema import (
    SnippetCheck,
    SpanCitableCoverage,
    SpanQualificationPolicy,
    SynopsisSourceValidation,
)
from document_structure.table_schema import (
    FINAL_SYNOPSIS_REASON_CODES,
    FinalNavigationSynopsis,
    FinalOutlineSpan,
    FinalSynopsisSnippet,
)

__all__ = [
    "SynopsisError", "MAX_SNIPPETS_PER_NODE", "MAX_SNIPPET_CHARS",
    "MIN_SNIPPET_CHARS", "MAX_TOTAL_SNIPPET_CHARS", "SENTENCE_TERMINATORS",
    "CLOSING_QUOTES", "SnippetRange", "navigation_admissible",
    "admissible_source_spans", "select_snippet_ranges", "select_snippets",
    "unavailable_reason", "build_node_synopsis", "build_navigation_synopses",
    "build_navigation_body_chars", "verify_synopsis_sources",
    # TS5 §19.10：final synopsis（与上面共用同一批叶子与策略）
    "final_navigation_admissible", "admissible_final_spans",
    "select_final_snippet_ranges", "select_final_snippets",
    "build_final_node_synopsis", "build_final_navigation_synopses",
    "final_synopsis_problems",
    "self_check",
]


class SynopsisError(SchemaValidationError):
    """简介构建 / 校验失败（越界、非抽取、跨区间、长度越界）。"""


# ---------------------------------------------------------------------------
# 1. 冻结常数（§18.8.6；与 `span_policy.TS4_A_SNIPPET_DEFAULTS` 同源同值）
# ---------------------------------------------------------------------------
#
# 这些常数有两份表达：本模块的模块级常量（供纯函数与 self_check 使用）与 TS4 策略
# 记录里的字段（进入 snapshot 身份与 input fingerprint）。两者**必须**逐项相等；策略
# 解析层（`span_policy.resolve_qualification_policy`）负责把文件里的值与代码中的已
# 冻结规格逐项对回，因此"改一处"必然被发现。本模块的 `self_check()` 也再对一次。

MAX_SNIPPETS_PER_NODE: int = 3
MAX_SNIPPET_CHARS: int = 120
MIN_SNIPPET_CHARS: int = 20
MAX_TOTAL_SNIPPET_CHARS: int = 300

#: 句末标点。**最长优先**匹配（`……` 必须先于 `。` 尝试），否则两字省略号会被切成
#: 两个单字省略号，片段会在半个标点处结束。实现使用普通引号字面量，不用正则。
SENTENCE_TERMINATORS: tuple[str, ...] = (
    "……", "。", "！", "？", ".", "!", "?", ";", "；",
)

#: 句末 token 之后允许紧随并**一并计入片段**的闭引号（§18.8.6）。单个省略号字符或
#: 普通逗号都不算句末。
CLOSING_QUOTES: tuple[str, ...] = ("”", "’", "\"", "'")


def _terminators_longest_first() -> tuple[str, ...]:
    """按 token 长度降序（等长时保持声明序，保证确定性）。"""
    return tuple(sorted(SENTENCE_TERMINATORS, key=lambda t: -len(t)))


# ---------------------------------------------------------------------------
# 2. 导航可采信性（**不**复用 completion eligibility）
# ---------------------------------------------------------------------------

def navigation_admissible(span: OutlineSpan) -> bool:
    """该 span 能否**作为导航简介的来源**（§18.8.6 的 `navigation_admissible`）。

    条件：已归属真实节点、非 fallback、非跨标题、角色为正文、`char_range` 覆盖全文。
    这里**故意不**要求 completion eligibility —— TS4-A 没有阈值，若复用它会让全部
    导航简介退化为不可用。
    """
    if not isinstance(span, OutlineSpan):
        raise SynopsisError("navigation_admissible 只接受 OutlineSpan")
    if span.role != "body":
        return False
    if span.node_id is None:
        return False
    if span.is_fallback or span.is_cross_heading:
        return False
    if span.char_range != (0, len(span.normalized_text)):
        return False
    return True


def admissible_source_spans(spans: Sequence[OutlineSpan], *,
                            node_id: str | None = None) -> tuple[OutlineSpan, ...]:
    """可采信来源 span，按 `(start_page, start_line, end_page, end_line, span_id)` 排序。

    给定 `node_id` 时只保留该节点自己的 span —— 简介**不得**引用别的节点的正文。
    """
    out = [s for s in spans if navigation_admissible(s)]
    if node_id is not None:
        out = [s for s in out if s.node_id == node_id]
    out.sort(key=lambda s: (s.start_anchor[0], s.start_anchor[1],
                            s.end_anchor[0], s.end_anchor[1], s.span_id))
    return tuple(out)


# ---------------------------------------------------------------------------
# 3. 片段区间选取（§18.8.4；确定性，不依赖任何公司专用规则）
# ---------------------------------------------------------------------------

class SnippetRange:
    """一个待产出的片段区间（`S` 域）及其来源 span。"""

    __slots__ = ("span_id", "char_start", "char_end")

    def __init__(self, span_id: str, char_start: int, char_end: int) -> None:
        self.span_id = span_id
        self.char_start = char_start
        self.char_end = char_end

    @property
    def length(self) -> int:
        return self.char_end - self.char_start

    def as_tuple(self) -> tuple[str, int, int]:
        return (self.span_id, self.char_start, self.char_end)


def _terminator_end(text: str, lo: int, window_end: int) -> int | None:
    """`[lo, window_end)` 内最靠后的句末 token 结束位置（不含闭引号），无则 `None`。

    只用**最长优先**的普通子串匹配；找不到即返回 `None`，调用方据此走"整段取用"或
    诚实放弃，**绝不**硬截断。
    """
    best: int | None = None
    for pos in range(lo, window_end):
        for token in _terminators_longest_first():
            if text.startswith(token, pos):
                end = pos + len(token)
                if end <= window_end:
                    if best is None or end > best:
                        best = end
                break
    return best


def _extend_closing_quotes(text: str, end: int, hard_end: int) -> int:
    """句末 token 之后紧随的闭引号一并计入片段。"""
    while end < hard_end and text[end] in CLOSING_QUOTES:
        end += 1
    return end


def select_snippet_ranges(span: OutlineSpan, coverage: SpanCitableCoverage, *,
                          policy: SpanQualificationPolicy,
                          budget: list[int]) -> tuple[SnippetRange, ...]:
    """对**单个 span** 按其 `effective_citable_intervals` 顺序产出片段。

    `budget` 是**节点级**的两元素可变配额 `[剩余条数, 剩余总字符数]`，由调用方在节点
    内跨 span 共享（因此不得在 span 之间重置）。返回的区间一律满足
    `MIN_SNIPPET_CHARS <= length <= MAX_SNIPPET_CHARS`，且落在**单个**有效区间内。
    """
    if span.span_id != coverage.span_id:
        raise SynopsisError(
            f"coverage.span_id={coverage.span_id!r} 与 span.span_id={span.span_id!r} "
            f"不一致（fail-closed）")
    if coverage.span_local_length != len(span.normalized_text):
        raise SynopsisError(
            f"coverage.span_local_length={coverage.span_local_length} 与 span 文本长度 "
            f"{len(span.normalized_text)} 不一致（fail-closed）")
    text = span.normalized_text
    out: list[SnippetRange] = []
    for interval in coverage.effective_citable_intervals:
        if budget[0] <= 0:
            break
        lo, hi = interval.start, interval.end
        if hi - lo < policy.min_snippet_chars:
            continue
        window_end = min(hi, lo + policy.max_snippet_chars)
        # 句末 token 之后必须至少达到硬下界，且不越过窗口。
        p_end = _terminator_end(text, lo, window_end)
        if p_end is not None and lo + policy.min_snippet_chars <= p_end:
            cs = lo
            ce = _extend_closing_quotes(text, p_end, window_end)
            if ce - cs < policy.min_snippet_chars:
                continue
        elif hi - lo <= policy.max_snippet_chars:
            cs, ce = lo, hi
        else:
            # 过长且在窗口内找不到句末标点：**禁止**硬截断。
            continue
        if ce - cs > policy.max_snippet_chars:
            continue
        if ce - cs > budget[1]:
            # 总字符配额不足：本 span 到此为止，不产出半个片段。
            break
        out.append(SnippetRange(span.span_id, cs, ce))
        budget[0] -= 1
        budget[1] -= ce - cs
    return tuple(out)


def select_snippets(spans: Sequence[OutlineSpan],
                    coverages: dict[str, SpanCitableCoverage], *,
                    policy: SpanQualificationPolicy) -> tuple[SnippetRange, ...]:
    """对**有序**的一组可采信 span 产出片段（节点级配额只初始化一次）。"""
    budget = [policy.max_snippets_per_node, policy.max_total_snippet_chars]
    out: list[SnippetRange] = []
    for span in spans:
        coverage = coverages.get(span.span_id)
        if coverage is None:
            raise SynopsisError(f"span {span.span_id!r} 缺 coverage（fail-closed）")
        out.extend(select_snippet_ranges(span, coverage, policy=policy,
                                         budget=budget))
        if budget[0] <= 0:
            break
    return tuple(out)


# ---------------------------------------------------------------------------
# 4. 不可用原因（全函数、互斥、首个命中）
# ---------------------------------------------------------------------------

def unavailable_reason(*, node_id: str, kind_counts: dict[str, int],
                       has_admissible_span: bool, has_citable_coverage: bool,
                       snippet_count: int,
                       final_material: bool = False) -> str | None:
    """按 §18.8.6 的固定顺序给出 `reason_code`（`None` 表示可 `available`）。

    `kind_counts`：该节点全部 `BodyRangeDisposition.range_kind` 的计数。
    判定是全函数且互斥，取**首个**命中；不采信调用方的任何"已算好的结论"。

    `final_material`（§19.10 / `nss-2 → nss-3`）只改**一个**格：TS5 之后，"内容
    全部落在正式 `TableObject` 上"的节点不再处于"等 TS5 决议"状态，因此它得到的
    是 `table_material_available_no_text_synopsis`，而不是 `table_only_pending_ts5`。
    其余分支、"首个命中"顺序与全部非 `table_only_pending_ts5` 的取值逐字不变，
    因此 TS4 路径（`final_material=False`，默认值）保持逐字节不变。
    """
    if not isinstance(node_id, str) or node_id == "":
        raise SynopsisError("node_id 必须为非空字符串")
    unknown = set(kind_counts) - set(_OUTLINE_KIND_NAMES)
    if unknown:
        raise SynopsisError(f"kind_counts 含未登记的 range_kind：{sorted(unknown)}")
    for name, value in kind_counts.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SynopsisError(f"kind_counts[{name!r}] 必须为非负 int，得到 {value!r}")

    regular = kind_counts.get("regular", 0)
    table = kind_counts.get("table_inside", 0) + kind_counts.get("table_adjacency", 0)
    empty = kind_counts.get("empty", 0)
    unassigned = kind_counts.get("unassigned", 0)

    if regular == 0:
        # 1. 无 regular span 且无任何处置范围，或只有 unassigned 范围。
        if table == 0 and empty == 0:
            return "no_span"
        # 2. 无 regular span 且含任意表格范围（可同时含 empty）。
        if table > 0:
            # TS5 之后表格已成为正式材料：不得再说"等 TS5"。
            return ("table_material_available_no_text_synopsis" if final_material
                    else "table_only_pending_ts5")
        # 3. 无 regular span 且只含 empty。
        return "empty_text"
    if not has_admissible_span:
        return "no_span"
    if not has_citable_coverage:
        # 4. 有 regular span 但所有 span 的可引用并集为空。
        return "alignment_failed"
    if snippet_count == 0:
        # 5. 有可引用覆盖但无片段满足长度约束。
        return "length_exceeded"
    if unassigned:  # pragma: no cover - regular>0 时 unassigned 不影响原因
        pass
    # 6. 已实际生成至少一条合法片段。
    return None


_OUTLINE_KIND_NAMES: tuple[str, ...] = (
    "regular", "table_adjacency", "table_inside", "unassigned", "empty",
)


# ---------------------------------------------------------------------------
# 5. 节点简介构建
# ---------------------------------------------------------------------------

def build_node_synopsis(*, node_id: str, spans: Sequence[OutlineSpan],
                        coverages: dict[str, SpanCitableCoverage],
                        kind_counts: dict[str, int],
                        policy: SpanQualificationPolicy) -> NavigationSynopsis:
    """构建一个节点的简介（可用或**诚实不可用**）。

    截断到 `page_layout` 没有用处：本函数只看 span 自己的规范化文本与其 typed 覆盖，
    因此同一份输入必然得到同一份输出（确定性、无外部状态）。
    """
    sources = admissible_source_spans(spans, node_id=node_id)
    source_ids = tuple(s.span_id for s in sources)
    citable = any(coverages[s.span_id].effective_citable_chars > 0 for s in sources
                  if s.span_id in coverages)
    missing = [s.span_id for s in sources if s.span_id not in coverages]
    if missing:
        raise SynopsisError(f"节点 {node_id!r} 的 span 缺 coverage：{missing}")

    ranges = select_snippets(sources, coverages, policy=policy)
    reason = unavailable_reason(
        node_id=node_id, kind_counts=kind_counts,
        has_admissible_span=bool(sources), has_citable_coverage=citable,
        snippet_count=len(ranges))
    if reason is not None:
        return NavigationSynopsis.unavailable(
            node_id=node_id, reason_code=reason,
            source_span_ids=(() if reason in ("no_span", "table_only_pending_ts5")
                             else source_ids))
    by_id = {s.span_id: s for s in sources}
    snippets = []
    for index, rng in enumerate(ranges):
        span = by_id[rng.span_id]
        text = span.normalized_text[rng.char_start:rng.char_end]
        snippets.append(SynopsisSnippet(
            span_id=rng.span_id, snippet_index=index,
            char_start=rng.char_start, char_end=rng.char_end, text=text))
    return NavigationSynopsis.available(node_id=node_id, snippets=tuple(snippets))


def build_navigation_synopses(*, node_ids: Sequence[str],
                              spans: Sequence[OutlineSpan],
                              coverages: Sequence[SpanCitableCoverage],
                              dispositions: Sequence[Any],
                              policy: SpanQualificationPolicy
                              ) -> tuple[NavigationSynopsis, ...]:
    """为**每一个真实 outline 节点**恰产出一份简介（按 `node_id` 升序）。

    节点集合由调用方按真实 `DocumentOutline.nodes` 给出；这里既不补造节点，也不跳过
    没有正文的节点 —— "没有正文"必须表达为带 `reason_code` 的不可用简介。
    """
    cov = {c.span_id: c for c in coverages}
    counts: dict[str, dict[str, int]] = {nid: {} for nid in node_ids}
    for d in dispositions:
        if d.node_id is None or d.node_id not in counts:
            continue
        bucket = counts[d.node_id]
        bucket[d.range_kind] = bucket.get(d.range_kind, 0) + 1
    out = []
    for node_id in node_ids:
        if not isinstance(node_id, str) or node_id == "":
            raise SynopsisError("node_ids 必须全为非空字符串")
        out.append(build_node_synopsis(
            node_id=node_id, spans=spans, coverages=cov,
            kind_counts=counts[node_id], policy=policy))
    keys = [n.node_id for n in out]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise SynopsisError("简介必须按 node_id 严格升序且每个节点恰一份")
    return tuple(out)


def build_navigation_body_chars(spans: Sequence[OutlineSpan]) -> dict[str, int]:
    """逐节点**自有**导航可采信正文字符数（`anp-7` 主体补读的输入之一）。

    与简介同源（都用 `navigation_admissible`，因此都是已归属真实节点、非 fallback、
    非跨标题、`role == "body"` 且 `char_range` 覆盖全文的 span），差别只在**统计方式**：
    简介是**抽取式**摘要（每节点至多 `MAX_SNIPPETS_PER_NODE × MAX_SNIPPET_CHARS` 字，
    且只取带句末的一小段），因此"简介长度"**不是**"这一节有多少正文"的代理。真实文档上
    两者会分叉：必须过的那一节 `1、主要业务` 自有正文 441 字、简介只有 78 字——按简介量
    卡"有没有实质正文"会把该读的那一节挡在外面。

    返回只含**计数**（无任何正文文本），因此它进 `NavigationIndex` 之后索引仍然拿不到
    证据内容。没有可采信正文的节点**不出现**在结果里（调用方按 0 处理）。
    """
    out: dict[str, int] = {}
    for span in spans:
        if not navigation_admissible(span):
            continue
        out[span.node_id] = out.get(span.node_id, 0) + len(span.normalized_text)
    return out


# ---------------------------------------------------------------------------
# 6. 来源复核（§18.4.2-3）
# ---------------------------------------------------------------------------

def _check_one_snippet(synopsis: NavigationSynopsis, index: int, snippet: Any,
                       *, span_registry: dict[str, OutlineSpan],
                       coverage_registry: dict[str, SpanCitableCoverage]
                       ) -> SnippetCheck:
    problems: list[str] = []
    span = span_registry.get(snippet.span_id)
    covering: tuple[int, int] | None = None
    if span is None:
        problems.append("snippet_span_unknown")
        return SnippetCheck(
            snippet_index=index, span_id=snippet.span_id,
            snippet_char_range=(snippet.char_start, snippet.char_end),
            snippet_source_hash=sha256_canonical(snippet.text),
            covering_effective_interval=None, ok=False,
            problems=tuple(sorted(set(problems))))
    if snippet.char_end > len(span.normalized_text) or snippet.char_start < 0:
        problems.append("snippet_over_span_length")
    else:
        sliced = span.normalized_text[snippet.char_start:snippet.char_end]
        if sliced != snippet.text:
            problems.append("snippet_not_extractive")
        if sha256_canonical(sliced) != sha256_canonical(snippet.text):
            problems.append("snippet_hash_mismatch")
    coverage = coverage_registry.get(snippet.span_id)
    if coverage is None:
        problems.append("snippet_span_unknown")
    else:
        for iv in coverage.effective_citable_intervals:
            if iv.start <= snippet.char_start < snippet.char_end <= iv.end:
                covering = (iv.start, iv.end)
                break
        if covering is None:
            problems.append("snippet_outside_effective_intervals")
    if snippet.char_end - snippet.char_start > MAX_SNIPPET_CHARS:
        problems.append("snippet_longer_than_limit")
    if snippet.snippet_index != index:
        problems.append("snippet_index_mismatch")
    # 来源 span 必须归属**本节点**且导航可采信。
    if span.node_id != synopsis.node_id or not navigation_admissible(span):
        problems.append("snippet_span_unknown")
    return SnippetCheck(
        snippet_index=index, span_id=snippet.span_id,
        snippet_char_range=(snippet.char_start, snippet.char_end),
        snippet_source_hash=sha256_canonical(snippet.text),
        covering_effective_interval=covering, ok=not problems,
        problems=tuple(sorted(set(problems))))


def verify_synopsis_sources(synopsis: NavigationSynopsis, *,
                            span_registry: dict[str, OutlineSpan],
                            coverage_registry: dict[str, SpanCitableCoverage],
                            verified_policy: SpanQualificationPolicy,
                            page_layout: Any = None,
                            records_by_id: dict[str, Any] | None = None
                            ) -> SynopsisSourceValidation:
    """独立重算一份简介的**逐片段来源**并返回 typed 校验结论。

    它**不**信任简介自己写的任何字段：片段区间是否落在单个有效可引用区间内、切片是否
    逐字相等、来源 span 是否属于本节点且导航可采信、`source_span_ids` 是否为来源序的
    去重结果、`synopsis_locator` / `synopsis_id` 是否等于重算值 —— 全部重算。

    参数 `page_layout` 与 `records_by_id` 保留为签名的一部分（§18.4.2-3），当前实现
    的可引用性判据**只**来自 typed coverage，不需要回读 Layout 与终态；它们为将来的
    几何复核保留，且**不**影响结论。
    """
    del page_layout, records_by_id  # 见 docstring：当前判据只来自 typed coverage
    if not isinstance(verified_policy, SpanQualificationPolicy):
        raise SynopsisError("verified_policy 必须为 SpanQualificationPolicy")
    # 结构性复核：**表达不成片段检查**的问题一律直接拒绝，不得压进某个片段的问题码，
    # 也不得伪造一个 check 来承载它。
    for sid in synopsis.source_span_ids:
        span = span_registry.get(sid)
        if span is None:
            raise SynopsisError(f"简介 {synopsis.synopsis_id!r} 引用了未知 span {sid!r}")
        if span.node_id != synopsis.node_id:
            raise SynopsisError(
                f"简介 {synopsis.synopsis_id!r} 引用了别的节点的 span {sid!r}")
        if not navigation_admissible(span):
            raise SynopsisError(
                f"简介 {synopsis.synopsis_id!r} 引用了不可采信 span {sid!r}")
    if synopsis.status == "available":
        if not synopsis.snippets:
            raise SynopsisError("available 简介必须至少有一条片段")
        ordered: list[str] = []
        for sn in synopsis.snippets:
            if sn.span_id not in ordered:
                ordered.append(sn.span_id)
        if tuple(ordered) != synopsis.source_span_ids:
            raise SynopsisError(
                f"source_span_ids 不是片段来源序的去重结果：{synopsis.source_span_ids} "
                f"!= {tuple(ordered)}")
    elif synopsis.snippets:
        raise SynopsisError("synopsis_unavailable 不得携带片段")
    return SynopsisSourceValidation.create(
        node_id=synopsis.node_id, synopsis_id=synopsis.synopsis_id,
        snippet_checks=tuple(
            _check_one_snippet(synopsis, i, sn, span_registry=span_registry,
                               coverage_registry=coverage_registry)
            for i, sn in enumerate(synopsis.snippets)))


# ---------------------------------------------------------------------------
# 6bis. TS5 §19.10：final synopsis（`ns-3` / `nss-3`）
# ---------------------------------------------------------------------------
#
# 与上面 TS4 路径**只共用低层叶子**（`SnippetRange`、`_terminator_end`、
# `_extend_closing_quotes`、`unavailable_reason`）与同一份 `SpanQualificationPolicy`
# 配额，因此不存在"第二套简介策略"。**两个简介类型本身各自独立**：TS4 是
# `NavigationSynopsis`（`nss-2` / `ns-2`），这里产出 `FinalNavigationSynopsis`
# （`nss-3` / `ns-3`），两者没有继承、别名、强制转换，也**不**共用构造器——
# 共用的只是文本切片这类与"是哪一代简介"无关的纯函数（§19.0.1）。
#
# 三条 TS5 专有约束：
#
# 1. `source_span_ids` **只**允许指向 final span（`fos-` 身份）。表格材料（表题 /
#    表头 / cell）不得伪装成 `SynopsisSnippet(span_id=...)`；
# 2. 可引用区间来自 `FinalOutlineSpan.citable_intervals` 中 `citable=True` 的那些，
#    而不是 TS4 的 `SpanCitableCoverage`；
# 3. 节点带表格但**没有**可引用 paragraph span 时，理由码只能是
#    `table_only_pending_ts5` / `table_material_available_no_text_synopsis` ——
#    不得伪造一条表格简介。

def select_final_snippet_ranges(span: Any, *, policy: SpanQualificationPolicy,
                                budget: list[int]) -> tuple[SnippetRange, ...]:
    """对**单个** `FinalOutlineSpan` 按其可引用区间顺序产出片段。

    与 `select_snippet_ranges` 同一套窗口 / 下界 / 配额规则；这里**不做任何**表格
    特例：表格材料既然没有 final span，就不可能有 final 片段。
    """
    if not isinstance(span, FinalOutlineSpan):
        raise SynopsisError(f"需要 FinalOutlineSpan，得到 {type(span).__name__}")
    text = span.normalized_text
    out: list[SnippetRange] = []
    for interval in span.citable_intervals:
        if not interval.citable:
            continue
        if budget[0] <= 0:
            break
        lo, hi = interval.char_range
        if hi - lo < policy.min_snippet_chars:
            continue
        window_end = min(hi, lo + policy.max_snippet_chars)
        p_end = _terminator_end(text, lo, window_end)
        if p_end is not None and lo + policy.min_snippet_chars <= p_end:
            cs = lo
            ce = _extend_closing_quotes(text, p_end, window_end)
            if ce - cs < policy.min_snippet_chars:
                continue
        elif hi - lo <= policy.max_snippet_chars:
            cs, ce = lo, hi
        else:
            # 过长且在窗口内找不到句末标点：**禁止**硬截断。
            continue
        if ce - cs > policy.max_snippet_chars:
            continue
        if ce - cs > budget[1]:
            break
        out.append(SnippetRange(span.span_id, cs, ce))
        budget[0] -= 1
        budget[1] -= ce - cs
    return tuple(out)


def select_final_snippets(spans: Sequence[Any], *,
                          policy: SpanQualificationPolicy
                          ) -> tuple[SnippetRange, ...]:
    """对**有序**的一组 final span 产出片段（节点级配额只初始化一次）。"""
    budget = [policy.max_snippets_per_node, policy.max_total_snippet_chars]
    out: list[SnippetRange] = []
    for span in spans:
        out.extend(select_final_snippet_ranges(span, policy=policy,
                                               budget=budget))
        if budget[0] <= 0:
            break
    return tuple(out)


def final_navigation_admissible(span: Any) -> bool:
    """final span 是否**导航可采信**：有 node 归属、有文本、且存在可引用区间。

    与 completion eligibility **无关**（decision 不构成 citable 资格）。
    """
    if not isinstance(span, FinalOutlineSpan):
        return False
    if span.node_id is None:
        return False
    if not isinstance(span.normalized_text, str) or span.normalized_text == "":
        return False
    return span.is_citable()


def admissible_final_spans(spans: Sequence[Any], *, node_id: str) -> tuple:
    """按**源序**（页, 行）取出本节点全部可采信 final span。"""
    picked = [s for s in spans
              if isinstance(s, FinalOutlineSpan) and s.node_id == node_id
              and final_navigation_admissible(s)]
    picked.sort(key=lambda s: (s.start_page, s.start_line, s.span_locator))
    return tuple(picked)


def build_final_node_synopsis(*, node_id: str, final_spans: Sequence[Any],
                              kind_counts: dict[str, int],
                              policy: SpanQualificationPolicy
                              ) -> FinalNavigationSynopsis:
    """为**一个**真实 outline 节点产出 final 简介（可用或诚实不可用）。

    返回的是 **TS5 自己的类型** `FinalNavigationSynopsis`（`nss-3` / `ns-3`），不是
    TS4 的 `NavigationSynopsis`：节点简介跨代复用同一个类，会让 aggregate 只能按
    child 自报的版本猜 reader，这正是 §19.4.1 禁止的。
    """
    sources = admissible_final_spans(final_spans, node_id=node_id)
    source_ids = tuple(s.span_id for s in sources)
    ranges = select_final_snippets(sources, policy=policy)
    reason = unavailable_reason(
        node_id=node_id, kind_counts=kind_counts,
        has_admissible_span=bool(sources),
        has_citable_coverage=any(s.is_citable() for s in sources),
        snippet_count=len(ranges), final_material=True)
    if reason is None and not sources:
        reason = "no_span"
    if reason is not None:
        # 表格材料不是 paragraph span：这两种理由都**不得**声明任何来源
        # （否则就是"把表格伪装成 final span"）。本类型的 `__post_init__` 会独立
        # 再拒一次，这里不做它的替代。
        return FinalNavigationSynopsis.unavailable(
            node_id=node_id, reason_code=reason,
            source_final_span_ids=(
                () if reason in ("no_span",
                                 "table_material_available_no_text_synopsis")
                else source_ids))
    by_id = {s.span_id: s for s in sources}
    snippets = []
    for index, rng in enumerate(ranges):
        span = by_id[rng.span_id]
        snippets.append(FinalSynopsisSnippet(
            final_span_id=rng.span_id,
            final_span_locator=span.span_locator,
            final_span_schema_version=span.schema_version,
            snippet_index=index,
            char_start=rng.char_start, char_end=rng.char_end,
            text=span.normalized_text[rng.char_start:rng.char_end]))
    return FinalNavigationSynopsis.available(node_id=node_id,
                                             snippets=tuple(snippets))


def build_final_navigation_synopses(*, node_ids: Sequence[str],
                                    final_spans: Sequence[Any],
                                    dispositions: Sequence[Any],
                                    policy: SpanQualificationPolicy
                                    ) -> tuple[FinalNavigationSynopsis, ...]:
    """为**每一个**真实 outline 节点恰产出一份 final 简介（按 `node_id` 升序）。"""
    counts: dict[str, dict[str, int]] = {nid: {} for nid in node_ids}
    for d in dispositions:
        node_id = getattr(d, "node_id", None)
        if node_id is None or node_id not in counts:
            continue
        bucket = counts[node_id]
        bucket[d.range_kind] = bucket.get(d.range_kind, 0) + 1
    out = []
    for node_id in node_ids:
        if not isinstance(node_id, str) or node_id == "":
            raise SynopsisError("node_ids 必须全为非空字符串")
        out.append(build_final_node_synopsis(
            node_id=node_id, final_spans=final_spans,
            kind_counts=counts[node_id], policy=policy))
    keys = [n.node_id for n in out]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise SynopsisError("final 简介必须按 node_id 严格升序且每个节点恰一份")
    return tuple(out)


def final_synopsis_problems(synopsis: Any,
                            final_spans: Sequence[Any]) -> list[str]:
    """独立复核一份 final 简介：类型、身份重算、来源与本节点闭合、摘录可抽回。

    与 TS4 的 `verify_synopsis_sources` 是**两条独立**通道：这里不读取任何 TS4
    对象、不接受 `os-*` 身份，也不复用 TS4 的字段名。传入 TS4 对象会直接报类型问题，
    而不是被"尽力解释"。
    """
    problems: list[str] = []
    if not isinstance(synopsis, FinalNavigationSynopsis):
        return [f"not_final_navigation_synopsis:{type(synopsis).__name__}"]
    if synopsis.schema_version != V.FINAL_SYNOPSIS_SCHEMA_VERSION:
        problems.append(f"schema_version_not_final:{synopsis.schema_version}")
    if synopsis.synopsis_version != V.FINAL_SYNOPSIS_VERSION:
        problems.append(f"synopsis_version_not_final:{synopsis.synopsis_version}")
    if synopsis.synopsis_locator != synopsis.derive_locator():
        problems.append("synopsis_locator_not_derived")
    if synopsis.synopsis_id != synopsis.derive_id():
        problems.append("synopsis_id_not_derived")
    registry = {s.span_id: s for s in final_spans
                if isinstance(s, FinalOutlineSpan)}
    for sid in synopsis.source_final_span_ids:
        if not sid.startswith("fos-"):
            problems.append(f"source_span_not_final_span:{sid}")
            continue
        span = registry.get(sid)
        if span is None:
            problems.append(f"source_span_unknown:{sid}")
            continue
        if span.node_id != synopsis.node_id:
            problems.append(f"source_span_other_node:{sid}")
        if not final_navigation_admissible(span):
            problems.append(f"source_span_not_admissible:{sid}")
    if synopsis.status == "available":
        for sn in synopsis.snippets:
            span = registry.get(sn.final_span_id)
            if span is None:
                problems.append(f"snippet_span_unknown:{sn.final_span_id}")
                continue
            if sn.final_span_locator != span.span_locator:
                problems.append(
                    f"snippet_locator_mismatch:{sn.snippet_index}")
            if sn.final_span_schema_version != span.schema_version:
                problems.append(
                    f"snippet_schema_version_mismatch:{sn.snippet_index}")
            if sn.char_end > len(span.normalized_text):
                problems.append(
                    f"snippet_range_out_of_bounds:{sn.snippet_index}")
                continue
            sliced = span.normalized_text[sn.char_start:sn.char_end]
            if sliced != sn.text:
                problems.append(f"snippet_not_extractive:{sn.snippet_index}")
            if not any(iv.citable and iv.char_range[0] <= sn.char_start
                       and sn.char_end <= iv.char_range[1]
                       for iv in span.citable_intervals):
                problems.append(
                    f"snippet_outside_citable_interval:{sn.snippet_index}")
    elif synopsis.status == "synopsis_unavailable":
        if synopsis.reason_code not in FINAL_SYNOPSIS_REASON_CODES:
            problems.append(f"reason_not_final:{synopsis.reason_code}")
    return problems


# ---------------------------------------------------------------------------
# 7. 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    problems: list[str] = []
    if (MAX_SNIPPETS_PER_NODE, MAX_SNIPPET_CHARS, MIN_SNIPPET_CHARS,
            MAX_TOTAL_SNIPPET_CHARS) != (3, 120, 20, 300):
        problems.append("snippet 常数偏离 §18.8.6 冻结值")
    if MIN_SNIPPET_CHARS > MAX_SNIPPET_CHARS:
        problems.append("min_snippet_chars 不得大于 max_snippet_chars")
    if MAX_SNIPPETS_PER_NODE * MAX_SNIPPET_CHARS < MAX_TOTAL_SNIPPET_CHARS:
        problems.append("节点级配额自相矛盾（条数上限 × 单条上限 < 总上限）")
    if _terminators_longest_first() != tuple(
            sorted(SENTENCE_TERMINATORS, key=lambda t: -len(t))):
        problems.append("sentence_terminators 未按长度降序")
    if "……" not in SENTENCE_TERMINATORS:
        problems.append("sentence_terminators 必须含两字省略号")
    if set(CLOSING_QUOTES) - {"”", "’", "\"", "'"}:
        problems.append("closing_quotes 含未登记字符")
    # §19.0.1 方案 C：**两套** reason 词表各自的成员必须被冻结在各自的类型上。
    # TS4 的词表不得含 final 专属值，final 的词表不得含 TS4 专属值——
    # 否则"两代简介"就会退化成"一套词表 + 一个开关"。
    _ts4_reasons = {"no_span", "empty_text", "length_exceeded",
                    "alignment_failed", "table_only_pending_ts5"}
    _final_reasons = {"no_span", "empty_text", "length_exceeded",
                      "alignment_failed",
                      "table_material_available_no_text_synopsis"}
    if set(SYNOPSIS_REASON_CODES) != _ts4_reasons:
        problems.append(f"TS4 reason 词表必须恰为 {sorted(_ts4_reasons)}，"
                        f"得到 {sorted(SYNOPSIS_REASON_CODES)}")
    if set(FINAL_SYNOPSIS_REASON_CODES) != _final_reasons:
        problems.append(f"final reason 词表必须恰为 {sorted(_final_reasons)}，"
                        f"得到 {sorted(FINAL_SYNOPSIS_REASON_CODES)}")
    if "table_only_pending_ts5" in FINAL_SYNOPSIS_REASON_CODES:
        problems.append("final 词表不得含 TS4 专属的 table_only_pending_ts5")
    if "table_material_available_no_text_synopsis" in SYNOPSIS_REASON_CODES:
        problems.append("TS4 词表不得含 final 专属的 "
                        "table_material_available_no_text_synopsis")
    # `final_material` 只允许改"表格材料"这一格；两条分支都必须可复算。
    _ts4_reason = unavailable_reason(
        node_id="n", kind_counts={"table_inside": 1}, has_admissible_span=False,
        has_citable_coverage=False, snippet_count=0, final_material=False)
    _ts5_reason = unavailable_reason(
        node_id="n", kind_counts={"table_inside": 1}, has_admissible_span=False,
        has_citable_coverage=False, snippet_count=0, final_material=True)
    if _ts4_reason != "table_only_pending_ts5":
        problems.append(f"TS4 表格-only 节点理由码必须是 table_only_pending_ts5，"
                        f"得到 {_ts4_reason!r}")
    if _ts5_reason != "table_material_available_no_text_synopsis":
        problems.append(f"TS5 表格-only 节点理由码必须是 "
                        f"table_material_available_no_text_synopsis，"
                        f"得到 {_ts5_reason!r}")
    for _kinds in ({"regular": 1, "table_inside": 1}, {"empty": 1},
                   {"unassigned": 1}):
        if unavailable_reason(
                node_id="n", kind_counts=_kinds, has_admissible_span=False,
                has_citable_coverage=False, snippet_count=0,
                final_material=True) != unavailable_reason(
                node_id="n", kind_counts=_kinds, has_admissible_span=False,
                has_citable_coverage=False, snippet_count=0,
                final_material=False):
            problems.append(f"final_material 只能改表格-only 一格，"
                            f"却在 {sorted(_kinds)} 上产生了差异")
    try:
        from document_structure.span_policy import TS4_A_SNIPPET_DEFAULTS
        if TS4_A_SNIPPET_DEFAULTS["max_snippets_per_node"] != MAX_SNIPPETS_PER_NODE:
            problems.append("策略默认值与模块常量不一致（max_snippets_per_node）")
        if TS4_A_SNIPPET_DEFAULTS["max_snippet_chars"] != MAX_SNIPPET_CHARS:
            problems.append("策略默认值与模块常量不一致（max_snippet_chars）")
        if TS4_A_SNIPPET_DEFAULTS["min_snippet_chars"] != MIN_SNIPPET_CHARS:
            problems.append("策略默认值与模块常量不一致（min_snippet_chars）")
        if (TS4_A_SNIPPET_DEFAULTS["max_total_snippet_chars"]
                != MAX_TOTAL_SNIPPET_CHARS):
            problems.append("策略默认值与模块常量不一致（max_total_snippet_chars）")
        if tuple(TS4_A_SNIPPET_DEFAULTS["sentence_terminators"]) != SENTENCE_TERMINATORS:
            problems.append("策略默认值与模块常量不一致（sentence_terminators）")
        if tuple(TS4_A_SNIPPET_DEFAULTS["closing_quotes"]) != CLOSING_QUOTES:
            problems.append("策略默认值与模块常量不一致（closing_quotes）")
    except ImportError as e:  # pragma: no cover
        problems.append(f"无法导入 span_policy 做一致性自检：{e}")
    return {
        "snippet_limits": {
            "max_snippets_per_node": MAX_SNIPPETS_PER_NODE,
            "max_snippet_chars": MAX_SNIPPET_CHARS,
            "min_snippet_chars": MIN_SNIPPET_CHARS,
            "max_total_snippet_chars": MAX_TOTAL_SNIPPET_CHARS,
        },
        "terminator_count": len(SENTENCE_TERMINATORS),
        "closing_quote_count": len(CLOSING_QUOTES),
        "reason_code_count": len(SYNOPSIS_REASON_CODES),
        "navigation_only": True,
        "problems": problems,
    }


def _main(argv: list[str]) -> int:
    if "--validate-only" not in argv:
        print("用法：python -m document_structure.synopsis --validate-only")
        return 2
    result = self_check()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result["problems"] else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))

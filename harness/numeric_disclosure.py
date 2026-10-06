"""确定性数值披露抽取（`nd-1`，2026-10-04）。

**这一层解决什么。** 研究侧此前**唯一**的候选构造器是 `topic_runtime._adopt_facts`，它的
`statement` 恒取模型命题文本（`claim.text`）。于是「材料已经把分业务营收原文送到 Pack 了，
却一条合格事实也没有」这类缺口，**不是** Contract、不是权威门、不是 Writer 面的问题，
而是**候选生成之前**没有任何确定性入口。本模块补的就是这个入口：把**已准入 Pack 的
精确定位正文**按句拆成**单值命题**，交给**既有**的资格门（`build_fact_candidate` →
`build_qualification_decision` → `build_supported_fact`），**不**建立第二套事实通道。

**边界（每条都要能反查）：**

1. **纯函数、零 LLM、零网络、零 DB**：输入是文本与冻结 Contract 字段标签，输出是逐值身份与
   逐值 typed 跳过；不读文件名、不读页号、不读报告生成日、不读答案关键词。
2. **公司无关**：本模块里**没有**任何主体代码、公司名、固定页或答案数字。覆盖面对外声明在
   `ASPECT_COVERAGE` 里，键是**冻结 Contract 的 aspect_id 与字段标签**——任何主体的同一栏目
   都走同一条规则；拓宽覆盖面 = 往那张表加一行（升版 + 测试），不是写特例。
3. **并列三年数字必须拆成单值命题**：`2023-2025 年 … 分别为 A、B、C` 里第 i 个值绑第 i 个
   年份，**位置配对**。年份个数与值个数对不上、开标记缺失、单位不一致、占比无分母、指标窗里
   出现增速动词——一律 **typed 跳过**，一个都不猜。`4.4%`／`9.0%` 这类同比值**不得**铸成水平值。
4. **期间口径 ≠ 本模块口径**：本模块回答的是「**这个值**属于区间里的哪一年」，不是
   `harness/period_extraction.py` 回答的「这句话披露了哪一个期间」。两者都在本仓、都确定性，
   但**不是**同一个问题：对 `2023-2025 年` 后者只会答 `2025年`——那是错的**值级**期间。
   因此本模块**不**调用 `extract_explicit_period`，`period_key` 只来自位置配对。
5. **可定位**：`statement` 是归一正文里的**逐字连续切片**（年份头起、到本值单位止），
   因此逐字存在于 Writer 读到的 `reading_view` 里；`value_span` 给出它的字符区间。
6. **被挡下的值不是候选**：`SkippedValue` 不进 `FactCandidate`，因此**不**产生资格决定。
   `DESIGN_V2.md` §0.12「每个被拒绝候选恰有一条 typed audit」说的是**候选**，本模块不冒充；
   Contract 必需事实仍未取得时，仍由既有 §7.5 另外形成 gap／block。
7. **冲突值两处都铸候选、都判拒**：同一 `(期间, 字段, 单位, 业务作用域)` 下出现**互不相同**
   的原值 ⇒ 两条都是**候选**（进 `conflicts`，由调用方各判一条 `rejected` 决定），不是静默
   取一个。作用域是这条键的一部分：**不同业务**同年的收入是两条各自成立的命题，不是冲突。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

#: 抽取规则版本。任何一条闭集（`METRIC_LEXICON` / `SERIES_OPENERS` / `DELTA_MARKERS` /
#: `ASPECT_COVERAGE` / 归一化方式 / 字段归属选法）发生变化都必须升版，并带版本化正反例。
#:
#: `nd-2`（M930-3 `ndc-2` 批）与 `nd-1` 的三处差别，**都只收紧**：
#:   ① 同一材料内的去重／冲突键补上**业务作用域**（`nd-1` 是 `(期间,字段,单位)`）——不同业务
#:     同年的收入不再互判冲突；同业务同年不同值照旧两条都铸候选、都判拒。
#:   ② 作用域读不出（`scope_key` 为空）的并列序列**整条跳过**，新码 `scope_unresolved`：
#:      `nd-1` 会把 `分别占…` 里的 `分别` 当成业务名，铸出一条作用域为空、无法与句子配对的事实。
#:   ③ 新增 `binding_from_declared`：句子侧可改用**事实自己声明的**逐值身份（`SupportedFact.
#:      value_identity`）而不是从 `text` 反推，后者会被「从年份头起到本值的逐字前缀」带偏。
#: 判定集变了 ⇒ 版本必须前进，`nd-1` 与 `nd-2` 不得共用一个版本号。
NUMERIC_DISCLOSURE_VERSION = "nd-2"

#: 冻结 Contract 字段标签 → `(单位类, 指标词素闭集)`。
#:
#: **只**由冻结字段标签派生：`各业务收入` 是 `templates/contracts/standard_v3.yaml` 里
#: `company_business_main.revenue_breakdown.required_fields` 的**逐字**标签，不是近义名。
#: 词素闭集是**声明**：窗里出现词素才算命中，命中不了就跳过并记账，不做模糊匹配。
METRIC_LEXICON: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "各业务收入": ("amount", ("收入",)),
    "收入占比": ("ratio", ("占比", "比重")),
}

#: 本版**声明覆盖**的 `aspect_id → 字段标签`。覆盖面是**说出来的**，不是靠启发式蹭出来的：
#: 栏目不在表里 ⇒ `covered_fields` 返回空集 ⇒ `extract_disclosures` **在扫值之前**就返回
#: `(), (), ()`（连值是哪个都还不知道，编不出逐值跳过码），该栏目的缺口仍由既有的 aspect
#: 级判定记账，不在这里重复计一遍。
#:
#: 本版只覆盖一个栏目：`company_business_main.revenue_breakdown`。选它的理由是**纵链已证**：
#: 该栏目的材料确实把分业务营收原文送进了 Pack，而候选数为 0
#: （`M930_3_QREWORK2_DESTINATION_TABLE.md` §1.1-§1.3）。**不是**因为它属于某个主体。
ASPECT_COVERAGE: Mapping[str, tuple[str, ...]] = {
    "company_business_main.revenue_breakdown": ("各业务收入", "收入占比"),
}

#: 序列表述开标记（**闭集**）：必须整词贴在指标窗末尾、紧邻第一个数值。
SERIES_OPENERS: tuple[str, ...] = ("分别为", "依次为", "达到", "为", "达")

#: 增速／差额动词（**闭集**）：出现在指标窗里 ⇒ 该值不是水平值，跳过。
#: `减少4.4%` / `同比增长9.0%` 正是这一类。
DELTA_MARKERS: tuple[str, ...] = (
    "增长", "减少", "下降", "上升", "同比", "环比", "增幅", "增速", "下滑", "提高", "降低",
)

#: 占比必须自带分母：窗里要有「占」+ 至少两个字的分母宾语。
_DENOMINATOR_RE = re.compile(r"占\s*[^，,。；;：:]{2,}")

#: 句边界（与 `harness/period_extraction.py::split_sentence_spans` **同一批**边界字符，
#: 边界字符归前句）。本模块不另立一套切句口径。
_SENTENCE_BOUNDARIES = "。；！？!?;\n"

#: 数值：可选负号 + 千分位数字 + 可选小数。前后不得紧邻字母数字（避免 `A1`、`12C` 里的数字）。
_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z_.])-?[0-9][0-9,]*(?:\.[0-9]+)?(?![0-9])")

#: 单位：紧跟数值之后的**闭集**后缀（顺序敏感，长的在前）。
_UNIT_ALTERNATIVES: tuple[str, ...] = (
    "个百分点", "亿元", "万元", "千元", "百元", "元", "‰", "%",
)

#: 金额类单位（`unit_kind == "amount"`）。裸缩放词（`亿`/`万`）**不算**单位：它要跟「元」才成
#: 金额，而那一步与 `sentence_check.is_qualified_numeric_surface` 是同一判据口径。
_AMOUNT_UNITS: frozenset[str] = frozenset({"亿元", "万元", "千元", "百元", "元"})

#: 比率类单位。
_RATIO_UNITS: frozenset[str] = frozenset({"%", "‰", "个百分点"})

#: 年份头：区间（`2023-2025 年`）或单年（`2025 年`）。
_YEAR_RANGE_RE = re.compile(r"(?<![0-9])((?:19|20)\d{2})\s*[-—－~～至]\s*((?:19|20)\d{2})\s*年")
_YEAR_SINGLE_RE = re.compile(r"(?<![0-9])((?:19|20)\d{2})\s*年")

#: 值之间的分隔符（**闭集**）。
#:
#: **不带 `^`**：判据一律用 `_SEPARATOR_RE.match(text, pos)` 从任意位置起匹配，而 `^` 在
#: `match(string, pos)` 里只在**串首**成立——带上它会让「第二个并列值」永远收不进来。
_SEPARATOR_RE = re.compile(r"\s*[、，,和及\-]\s*")

#: 收集并列值时的上界（只用于「找出这个数字属于哪个序列」，不用于判定年份个数）。
_SERIES_SCAN_CAP = 24

#: `SkippedValue.reason_code` 闭集（本版）。
#:
#: 「本栏目不在 `ASPECT_COVERAGE` 里」**不**在其中：那一步在扫值之前就返回空集（连值是哪个
#: 都不知道，逐值跳过无从谈起），由 `covered_fields` 的空集表达，缺口仍走既有的 aspect 级判定。
#:
#: `metric_field_ambiguous` 在 `nd-1` 的词素表下**不可达**（`各业务收入` 的词素 `收入` 与
#: `收入占比` 的两个词素，都不可能落在同一结束位）。留着它是**给下一版词素表预留**：覆盖声明
#: 一变它就可能被走到，届时不必再动码表。本版由测试钉死「其余八个码都各有可达输入」。
SKIP_REASONS: tuple[str, ...] = (
    "period_value_count_mismatch",
    "period_range_unparsable",
    "no_enumeration_marker",
    "growth_rate_or_delta_not_a_level",
    "metric_not_in_aspect_fields",
    "metric_field_ambiguous",
    "unit_kind_mismatch",
    "ratio_denominator_unstated",
    "duplicate_same_value",
    #: `nd-2` 新增：指标窗给出的业务作用域读不出（归一后短于 `_SCOPE_MIN_CHARS`，或只剩
    #: 「分别」这类承接词）。作用域是本模块说清「哪个业务」的唯一载体，空作用域的事实无法与
    #: 句子四轴配对（`scope_compatible` 会一律放行），因此**整条并列序列跳过**，不铸候选。
    "scope_unresolved",
)

#: `conflicting_value_for_same_identity` 由调用方在 `conflicts` 上判，不在这里。
CONFLICT_REASON = "conflicting_value_for_same_identity"

#: 比较用的 scope 归一：删空白与「的」这类不区分业务的成分。
_SCOPE_NOISE_RE = re.compile(r"[\s的]")
#: 比较用的 scope 至少要这么长，低于它视为「给不出绑定」（回落旧判据，不新造硬错）。
_SCOPE_MIN_CHARS = 4


class NumericDisclosureError(Exception):
    """闭集／配置违规（fail-closed，不返回半个结果）。"""


def normalize_text(text: object) -> str:
    """payload 正文 → 读视图：只做换行归一并去首尾空白，不增删任何实词。

    与 `sections/material_context.normalize_reading_view` **逐字同口径**（测试对账）。
    本模块在归一后的文本上工作，因此 `statement` 切片**必然**逐字存在于 Writer 读到的
    `reading_view` 里。
    """
    return str(text if text is not None else "").replace("\r\n", "\n").replace("\r", "\n").strip()


@dataclass(frozen=True)
class ValueIdentity:
    """一个**单值命题**的身份。放不进 wire（`FactCandidate` 无值身份字段），只在本模块内
    构造与审计；真正进 Pack 的只有 `statement` / `period` / `material_ids`。"""

    period_key: str
    period_display: str
    field_label: str
    unit: str
    unit_kind: str
    raw_number: str
    scope_text: str
    ordinal: int
    value_span: tuple[int, int]
    statement_span: tuple[int, int]
    statement: str

    @property
    def scope_key(self) -> str:
        """比较用的 scope 键（删空白与「的」）。"""
        return _scope_noise(self.scope_text)


@dataclass(frozen=True)
class SkippedValue:
    """被挡下的值：typed、可审计。**不**是 `FactCandidate`，因此不产生资格决定。"""

    value_span: tuple[int, int]
    raw_number: str
    reason_code: str
    detail: str


@dataclass(frozen=True)
class NumberBinding:
    """句子侧的数字绑定（item 3 的配对判据用；与 :func:`extract_disclosures` **同一**窗口
    规则、同一归一化）。任一分量为空 ⇒ 这一侧「给不出绑定」，调用方**回落**旧判据。"""

    raw_number: str
    period_key: str
    scope_key: str
    metric_field: str
    unit_kind: str


@dataclass(frozen=True)
class _Value:
    raw: str
    unit: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# 覆盖面
# ---------------------------------------------------------------------------

def covered_fields(aspect_id: str, required_fields: Sequence[str]
                   ) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """本栏目在 `nd-1` 下**声明覆盖**的字段：`((标签, 单位类, 词素), ...)`。

    `ASPECT_COVERAGE` 里声明了、但冻结 `required_fields` 里**没有**的标签一律剔除
    （冻结 Contract 与我们的声明分叉时说不上话：宁可少覆盖，不可自造栏目）。表外栏目 ⇒ `()`。
    """
    declared = tuple(ASPECT_COVERAGE.get(str(aspect_id), ()))
    if not declared:
        return ()
    present = {str(f) for f in (required_fields or ())}
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for label in declared:
        if label not in present:
            continue
        entry = METRIC_LEXICON.get(label)
        if entry is None:
            raise NumericDisclosureError(
                f"ASPECT_COVERAGE 声明了字段标签 {label!r}，但 METRIC_LEXICON 里没有它"
                f"（覆盖声明与词素表必须同版本一致，fail-closed）")
        kind, lexemes = entry
        out.append((label, kind, tuple(lexemes)))
    return tuple(out)


# ---------------------------------------------------------------------------
# 底层扫描
# ---------------------------------------------------------------------------

def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """句区间（含边界字符，边界归前句）——与 `split_sentence_spans` 同口径。"""
    spans: list[tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _SENTENCE_BOUNDARIES:
            spans.append((start, i + 1))
            start = i + 1
    if start < len(text):
        spans.append((start, len(text)))
    return tuple(spans)


def _sentence_start(text: str, pos: int) -> int:
    for a, b in _sentence_spans(text):
        if a <= pos < b:
            return a
    return 0


def _number_at(text: str, pos: int) -> _Value | None:
    """`pos` 处（允许前置空白）是否有一个「数值 [+ 单位]」。

    紧邻 `年` 的数字（年份头）**不算**值——这是把 `2023-2025 年` 与 `28,525,291.7 万元`
    分开的唯一边界。
    """
    i = pos
    while i < len(text) and text[i] in " \t":
        i += 1
    m = _NUMBER_RE.match(text, i)
    if m is None:
        return None
    k = m.end()
    while k < len(text) and text[k] in " \t":
        k += 1
    if k < len(text) and text[k] == "年":
        #: `2024 年` 里的 `2024` **不是**值。中间那一个空格必须跳过：PDF 抽取出来的正文
        #: 到处都有这种排版空白，只认紧邻的 `年` 会把年份头当成数值收进并列序列。
        return None
    end = m.end()
    j = end
    if j < len(text) and text[j] in " \t":
        j += 1
    unit = ""
    for candidate in _UNIT_ALTERNATIVES:
        if text.startswith(candidate, j):
            unit = candidate
            end = j + len(candidate)
            break
    return _Value(raw=m.group(0), unit=unit, start=i, end=end)


def _unit_kind(unit: str) -> str:
    if unit in _RATIO_UNITS:
        return "ratio"
    if unit in _AMOUNT_UNITS:
        return "amount"
    return ""


def _year_headers(text: str) -> tuple[tuple[tuple[str, ...], int, int], ...]:
    """全文的年份头，按起始位置升序：`((有序年份, 起, 止), ...)`。

    区间展开成**有序**年份；单年一个。区间与落在它里面的单年各自是**一条**记录，取「离得最近
    的那一条」由调用方按位置决定——不在这里做覆盖消解，避免两处口径分叉。
    """
    out: list[tuple[tuple[str, ...], int, int]] = []
    for m in _YEAR_RANGE_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if b < a:
            continue
        out.append((tuple(str(y) for y in range(a, b + 1)), m.start(), m.end()))
    for m in _YEAR_SINGLE_RE.finditer(text):
        if _in_year_range(text, m.start()):
            continue
        out.append(((m.group(1),), m.start(), m.end()))
    return tuple(sorted(out, key=lambda h: h[1]))


def _year_range_spans(text: str) -> tuple[tuple[int, int], ...]:
    return tuple((m.start(), m.end()) for m in _YEAR_RANGE_RE.finditer(text))


def _in_year_range(text: str, pos: int) -> bool:
    return any(a <= pos < b for a, b in _year_range_spans(text))


def _year_header_before(text: str, pos: int,
                        headers: tuple[tuple[tuple[str, ...], int, int], ...]
                        ) -> tuple[tuple[str, ...], int, int] | None:
    """`pos` 之前**同一句内**最近的年份头；不跨句追认。"""
    lo = _sentence_start(text, pos)
    best: tuple[tuple[str, ...], int, int] | None = None
    for header in headers:
        if header[2] <= pos and header[1] >= lo:
            best = header
    return best


def _series_values(text: str, opener_end: int, cap: int) -> tuple[_Value, ...]:
    """从 `opener_end` 起按分隔符连续收集数值（最多 `cap` 个）。"""
    values: list[_Value] = []
    pos = opener_end
    while len(values) < cap:
        found = _number_at(text, pos)
        if found is None:
            break
        values.append(found)
        m = _SEPARATOR_RE.match(text, found.end)
        if m is None:
            break
        pos = m.end()
    return tuple(values)


def _opener_ends(text: str) -> tuple[int, ...]:
    """所有「整词开标记 + 紧跟数值」的开标记**结束位置**（升序、去重）。

    取最长匹配，保证 `分别为` 不被当成 `为`（两个位置都返回，由调用方按窗口判据去重解析）。
    """
    out: set[int] = set()
    for opener in SERIES_OPENERS:
        start = 0
        while True:
            i = text.find(opener, start)
            if i < 0:
                break
            start = i + 1
            end = i + len(opener)
            if _number_at(text, end) is not None:
                out.add(end)
    return tuple(sorted(out))


def _series_containing(text: str, pos: int) -> tuple[int, tuple[_Value, ...]] | None:
    """覆盖 `pos` 的**最近**一个并列序列：`(开标记结束位, 全部值)`。给不出 ⇒ `None`。"""
    best: tuple[int, tuple[_Value, ...]] | None = None
    for end in _opener_ends(text):
        if end > pos:
            break
        values = _series_values(text, end, _SERIES_SCAN_CAP)
        if any(v.start <= pos < v.end for v in values):
            best = (end, values)
    return best


def _match_field(window: str, fields: Iterable[tuple[str, str, tuple[str, ...]]]
                 ) -> tuple[tuple[str, str, tuple[str, ...]] | None, str]:
    """指标窗 → 命中的字段。返回 `(字段, 失败原因)`。

    归属判据是**位置**而不是出现与否：取窗里**离本值最近**的那个命中词素（结束位置最大者）
    所属的字段。这一条是必需的——`占营业收入的比重` 里同时有 `收入` 与 `比重`，按「命中即
    计入」会两栏都算、只能判歧义，而离值最近的那个词素恰好就是真正的指标。两个**不同**字段的
    词素在同一位置结束才判 `metric_field_ambiguous`（不按长度任选，那是在猜）。
    """
    best_end = -1
    best_field: tuple[str, str, tuple[str, ...]] | None = None
    ambiguous = False
    for field in fields:
        for lexeme in field[2]:
            idx = window.rfind(lexeme)
            if idx < 0:
                continue
            end = idx + len(lexeme)
            if end > best_end:
                best_end, best_field, ambiguous = end, field, False
            elif end == best_end and field is not best_field and field != best_field:
                ambiguous = True
    if best_field is None:
        return None, "metric_not_in_aspect_fields"
    if ambiguous:
        return None, "metric_field_ambiguous"
    return best_field, ""


def _scope_from_window(window: str, field: tuple[str, str, tuple[str, ...]] | None,
                       kind: str) -> str:
    """指标窗 → 业务／主体片段。

    占比那一支在**分母的「占」处切开**：`电池材料及回收业务占公司全年营业收入比重` 的 scope
    是 `电池材料及回收业务`，而不是把分母口径也算进业务名。金额那一支去掉开标记与命中的指标
    词素。
    """
    text = window
    for opener in SERIES_OPENERS:
        if text.endswith(opener):
            text = text[: -len(opener)]
            break
    if kind == "ratio":
        cut = text.rfind("占")
        if cut >= 0:
            text = text[:cut]
    if field is not None:
        for lexeme in field[2]:
            text = text.replace(lexeme, "")
    return text.strip("，,、：:；;。 \t")


def _scope_noise(scope: str) -> str:
    key = _SCOPE_NOISE_RE.sub("", str(scope or ""))
    return key if len(key) >= _SCOPE_MIN_CHARS else ""


def _prev_number_end(text: str, pos: int) -> int:
    """同一句内 `pos` 之前最后一个**带单位**数值的结束位；没有则句首。

    必须把单位算进去：只退回数字本体时，`…31,650,636.9 万元，分别占…` 的窗会从
    `万元` 起算，指标窗里于是多出一个「万元」。
    """
    lo = _sentence_start(text, pos)
    last = lo
    for m in _NUMBER_RE.finditer(text, lo, pos):
        found = _number_at(text, m.start())
        last = found.end if found is not None else m.end()
    return last


def _metric_window(text: str, opener_end: int,
                   header: tuple[tuple[str, ...], int, int] | None) -> str:
    """指标窗：**起点取「年份头尾」与「上一个数值尾」中更靠后的那个**，终点是开标记尾。

    取更靠后者是必需的，两个方向各有反例：

    * 只用年份头 ⇒ 同一句里先有一段金额披露、后接一段占比披露时，占比那一支的窗里会拖进
      前一段的数值与业务名（scope 于是不可比）；
    * 只用上一个数值 ⇒ 并列序列里第二、三个值的窗里只剩一个顿号（指标无从判定）。
    """
    start = _sentence_start(text, opener_end)
    if header is not None:
        start = max(start, header[2])
    return text[max(start, _prev_number_end(text, opener_end)):opener_end]


# ---------------------------------------------------------------------------
# 主入口：确定性抽取
# ---------------------------------------------------------------------------

def extract_disclosures(text: str, *, aspect_id: str, required_fields: Sequence[str]
                        ) -> tuple[tuple[ValueIdentity, ...], tuple[ValueIdentity, ...],
                                   tuple[SkippedValue, ...]]:
    """一段**已准入 Pack** 的材料正文 → `(identities, conflicts, skips)`。

    * `identities`：可铸候选的单值命题；
    * `conflicts`：同一 `(期间, 字段, 单位, 业务作用域)`（`nd-2` 起含作用域）下出现**互不相同**
      原值的那些值——调用方**每条**都要铸候选并各判一条 `rejected` 决定
      （`conflicting_value_for_same_identity`）；
    * `skips`：其余被挡下的值（typed、可审计）。
    """
    body = normalize_text(text)
    fields = covered_fields(aspect_id, required_fields)
    if not fields:
        return (), (), ()

    headers = _year_headers(body)
    identities: list[ValueIdentity] = []
    conflicts: list[ValueIdentity] = []
    skips: list[SkippedValue] = []
    consumed: list[tuple[int, int]] = []
    seen: dict[tuple[str, str, str, str], tuple[str, int, ValueIdentity]] = {}
    conflict_keys: set[tuple[str, str, str, str]] = set()

    for opener_end in _opener_ends(body):
        if any(a <= opener_end < b for a, b in consumed):
            continue
        values = _series_values(body, opener_end, _SERIES_SCAN_CAP)
        if not values:
            continue
        first = values[0]
        header = _year_header_before(body, opener_end, headers)
        window = _metric_window(body, opener_end, header)
        if any(marker in window for marker in DELTA_MARKERS):
            skips.append(SkippedValue(
                (first.start, first.end), first.raw, "growth_rate_or_delta_not_a_level",
                "指标窗里出现增速／差额动词，该值不是水平值"))
            consumed.append((first.start, first.end))
            continue
        field, reason = _match_field(window, fields)
        if field is None:
            #: 开标记与年份头都在，只是指标不在本栏目声明的字段里（或两栏同近）：
            #: 按原因记账并**消费掉**这一段，免得后面的兜底再给它安一个错的原因。
            detail = ("指标窗里找不到本栏目声明的字段词素" if reason == "metric_not_in_aspect_fields"
                      else "指标窗里两个字段的词素同近，归属无从判定")
            for value in values:
                skips.append(SkippedValue((value.start, value.end), value.raw, reason, detail))
            consumed.append((values[0].start, values[-1].end))
            continue
        n_years = len(header[0]) if header is not None else 0
        if n_years == 0 or len(values) != n_years:
            code = "period_range_unparsable" if header is None else "period_value_count_mismatch"
            detail = ("指标窗前没有可解析的年份头" if header is None else
                      f"年份个数 {n_years} 与并列值个数 {len(values)} 不一致")
            for value in values:
                skips.append(SkippedValue((value.start, value.end), value.raw, code, detail))
            consumed.append((values[0].start, values[-1].end))
            continue
        units = {v.unit for v in values}
        kinds = {_unit_kind(u) for u in units}
        if len(units) != 1 or len(kinds) != 1 or kinds != {field[1]}:
            detail = (f"并列值单位不唯一或不匹配字段 {field[0]!r} 声明的单位类 {field[1]!r}")
            for value in values:
                skips.append(SkippedValue((value.start, value.end), value.raw,
                                          "unit_kind_mismatch", detail))
            consumed.append((values[0].start, values[-1].end))
            continue
        if field[1] == "ratio" and _DENOMINATOR_RE.search(window) is None:
            for value in values:
                skips.append(SkippedValue(
                    (value.start, value.end), value.raw, "ratio_denominator_unstated",
                    "占比没有可读的分母口径（窗里没有非空的「占…」宾语）"))
            consumed.append((values[0].start, values[-1].end))
            continue

        unit = next(iter(units))
        kind = next(iter(kinds))
        scope = _scope_from_window(window, field, kind)
        #: `nd-2`：作用域是本模块唯一能说清「哪个业务」的载体。读不出就**整条序列跳过**——
        #: `nd-1` 会把 `…，分别占营业收入的比重为 71.2%…` 里的承接词 `分别` 当成业务名，铸出
        #: 一条作用域为空、四轴配对时 `scope_compatible` 一律放行的事实（等于没有业务轴）。
        if not _scope_noise(scope):
            for value in values:
                skips.append(SkippedValue(
                    (value.start, value.end), value.raw, "scope_unresolved",
                    "指标窗给出的业务作用域读不出（空、过短或只是承接词）：无从确定这个值是哪个"
                    "业务的，不铸候选"))
            consumed.append((values[0].start, values[-1].end))
            continue
        #: 命题切片**从年份头起**（而不是从年份头尾起）：年份必须逐字留在命题里，
        #: 否则 `bind_numbers(statement)` 反过来会给不出期间，item 3 的配对判据就永远
        #: 落不到自家事实上。
        statement_start = header[1]
        for ordinal, value in enumerate(values):
            identity = ValueIdentity(
                period_key=header[0][ordinal], period_display=f"{header[0][ordinal]}年",
                field_label=field[0], unit=unit, unit_kind=kind, raw_number=value.raw,
                scope_text=scope, ordinal=ordinal,
                value_span=(value.start, value.end),
                statement_span=(statement_start, value.end),
                statement=body[statement_start:value.end])
            #: `nd-2`：键里补上**业务作用域**。少了它，同一材料里甲、乙两个业务同年的收入会被
            #: 判成「同一身份下的两个不同值」= 冲突，两条都铸成 `rejected`——那是把**两条本来
            #: 都成立的命题**错判成互相打架。同业务同年的不同值仍然照旧进冲突。
            key = (identity.period_key, identity.field_label, identity.unit, identity.scope_key)
            if key in conflict_keys:
                #: 已经判成冲突的那一组：后面再出现的同键值一并进冲突，不与先出现的那条比
                #: （否则第三条会绕回「新增一条候选」的老路，冲突组被悄悄拆开）。
                conflicts.append(identity)
                continue
            prior = seen.get(key)
            if prior is None:
                seen[key] = (value.raw, value.start, identity)
                identities.append(identity)
            elif prior[0] == value.raw:
                skips.append(SkippedValue((value.start, value.end), value.raw,
                                          "duplicate_same_value",
                                          f"与本材料内 {prior[1]} 处的同键同值重复"))
            else:
                #: 同一 (期间, 字段, 单位) 下两个**不同**原值：**两条**都不静默丢掉，
                #: 都进 `conflicts`，由调用方各判一条 rejected 决定。先出现的那条必须从
                #: 候选位**换到**冲突位（只从 `identities` 里删掉就等于把它丢了）。
                prior_raw, prior_start, prior_identity = prior
                del prior_raw, prior_start
                identities[:] = [i for i in identities if i is not prior_identity]
                seen.pop(key, None)
                conflict_keys.add(key)
                conflicts.extend((prior_identity, identity))
        consumed.append((values[0].start, values[-1].end))

    #: 未被任何序列消费掉的数值：一律 typed 跳过（**不**做第二套启发式去猜年份）。
    for m in _NUMBER_RE.finditer(body):
        if any(a <= m.start() < b for a, b in consumed) or _in_year_range(body, m.start()):
            continue
        found = _number_at(body, m.start())
        if found is None:
            continue
        window = body[_prev_number_end(body, found.start):found.start]
        if any(marker in window for marker in DELTA_MARKERS):
            reason, detail = ("growth_rate_or_delta_not_a_level",
                              "指标窗里出现增速／差额动词，该值不是水平值")
        else:
            reason, detail = ("no_enumeration_marker",
                              "该值不在任何「…分别为／为」并列序列里，年份无从位置配对")
        skips.append(SkippedValue((found.start, found.end), found.raw, reason, detail))
    return tuple(identities), tuple(conflicts), tuple(skips)


# ---------------------------------------------------------------------------
# 句子侧绑定（item 3）
# ---------------------------------------------------------------------------

def bind_numbers(text: str) -> tuple[NumberBinding, ...]:
    """句子 → 逐数字绑定；供 `sentence_check.numeric_qualification` 做**配对**判据。

    与 :func:`extract_disclosures` **同一**窗口规则、同一归一化：年份取该值**自己**那一段的
    年份头（并列序列按位置配对，单年直取），scope 取年份头与指标词素之间的片段，指标取窗里
    离本值最近的命中词素，单位类取单位后缀。

    任一分量为空 ⇒ 该数字「给不出绑定」，调用方**回落**旧判据（只可能收紧，不会放宽）。
    """
    body = normalize_text(text)
    fields = tuple((label, kind, lexemes)
                   for label, (kind, lexemes) in METRIC_LEXICON.items())
    headers = _year_headers(body)
    out: list[NumberBinding] = []
    for m in _NUMBER_RE.finditer(body):
        #: 年份头里的数字**不是**值：`2023-2025 年` 的 `2023` 后面跟的是 `-` 而不是 `年`，
        #: 光靠 `_number_at` 的 `年` 边界拦不住它，必须按年份头区间排除。
        if _in_year_range(body, m.start()):
            continue
        found = _number_at(body, m.start())
        if found is None:
            continue
        kind = _unit_kind(found.unit)
        series = _series_containing(body, found.start)
        if series is not None:
            opener_end, values = series
            header = _year_header_before(body, opener_end, headers)
            window = _metric_window(body, opener_end, header)
            ordinal = next(i for i, v in enumerate(values) if v.start == found.start)
        else:
            header = _year_header_before(body, found.start, headers)
            window = _metric_window(body, found.start, header)
            ordinal = 0
        for opener in SERIES_OPENERS:
            if window.endswith(opener):
                window = window[: -len(opener)]
                break
        field, _reason = _match_field(window, fields)
        period_key = ""
        if header is not None and len(header[0]) > ordinal:
            period_key = header[0][ordinal]
        out.append(NumberBinding(
            raw_number=found.raw, period_key=period_key,
            scope_key=_scope_noise(_scope_from_window(window, field, kind)),
            metric_field=(field[0] if field is not None else ""),
            unit_kind=kind))
    return tuple(out)


def period_key_of(display: str) -> str:
    """期间**表达** → 期间**键**（`2025年` → `2025`）。读不出 ⇒ `""`。

    只认「四位数字 + 可选空白 + `年`」这一种形状（本模块自己产出的 `period_display` 就是它）。
    别的写法（`2025-12-31`、`2025年度`、`报告期内`）一律返回 `""`：**不**做等价写法派生。
    """
    m = re.fullmatch(r"\s*((?:19|20)\d{2})\s*年\s*", str(display or ""))
    return m.group(1) if m else ""


def binding_from_declared(declared: Mapping[str, Any] | None, fact_period: str = ""
                          ) -> NumberBinding | None:
    """事实**自己声明的**逐值身份（`SupportedFact.value_identity` 的 dict）→ 句子侧绑定。

    这是 item 3 的第二条腿：**事实说清楚它授权的是哪个值**，句子侧不比对它那一段文本，而是
    比对这条声明。原因见 `nd-2` 说明③——`SupportedFact.text` 是从年份头到本值的**逐字前缀**，
    2025 那条事实的文本里因此带着 2023/2024 的值，从文本反推绑定必然把前值也读成本事实的。

    六轴里参与配对的是 `period`（经 `period_key_of` 换成键）、`metric`、`value_kind`（单位类）、
    `scope`（经 `_scope_noise` 换成键）、`amount_canonical`（本值）。**任一轴读不出、或声明的
    期间与事实自己的 `period` 不一致、或 `value_kind` 与 `unit` 相互矛盾** ⇒ 返回 `None`：
    调用方必须把它当「这条事实对任何数字都不授权」，**不得**退回从文本反推。
    """
    if declared is None:
        return None
    period = str(declared.get("period") or "")
    if fact_period and period != str(fact_period):
        return None
    period_key = period_key_of(period)
    kind = str(declared.get("value_kind") or "")
    unit = str(declared.get("unit") or "")
    declared_unit_kind = _unit_kind(unit)
    if declared_unit_kind and declared_unit_kind != kind:
        return None
    scope_key = _scope_noise(str(declared.get("scope") or ""))
    binding = NumberBinding(
        raw_number=str(declared.get("amount_canonical") or ""), period_key=period_key,
        scope_key=scope_key, metric_field=str(declared.get("metric") or ""), unit_kind=kind)
    return binding if complete(binding) else None


def scope_compatible(fact_scope_key: str, sentence_scope_key: str) -> bool:
    """两个 scope 键是否兼容：**互相包含**即算对上（不要求逐字相等）。

    放宽到包含是为了不误伤正常改写（`公司储能电池系统` vs `储能电池系统`），同时仍然挡住
    「同数字不同业务」（`储能电池系统` 与 `动力电池系统` 互不包含）。空键 ⇒ 兼容（那一侧给不出
    绑定，本就不该收紧）。
    """
    if not fact_scope_key or not sentence_scope_key:
        return True
    return fact_scope_key in sentence_scope_key or sentence_scope_key in fact_scope_key


def complete(binding: NumberBinding) -> bool:
    """这一侧的绑定是不是**完整**（四个分量都读得出）。

    任一分量为空 ⇒ 该侧「给不出绑定」，调用方**回落**旧判据。空值一律**不**当作「不匹配」：
    「读不出」与「读出来不一样」在判据上一字不同（前者不判，后者判错）。
    """
    return bool(binding and binding.period_key and binding.scope_key
                and binding.metric_field and binding.unit_kind)


def token_matches_binding(token: str, binding: NumberBinding) -> bool:
    """`token` 是不是就是指 `binding` 的那个原值（删千分位与空白后的前缀比对）。"""
    want = _literal(token)
    head = _literal(binding.raw_number)
    return bool(want and head and want.startswith(head))


def bindings_for(bindings: Sequence[NumberBinding],
                 token: str) -> tuple[NumberBinding, ...]:
    """该 token 的**全部**绑定（同一原值在一段里出现两次、角色不同时不止一条）。

    取全部而不是第一条：配对判据问的是「**存在**一条与句意相符的合格事实」，只取首条会把
    「另有正确的一条」误判成不匹配。
    """
    return tuple(b for b in bindings if token_matches_binding(token, b))


def binding_for(bindings: Sequence[NumberBinding], token: str) -> NumberBinding | None:
    """数字 token（`scan_numeric_tokens` 的形状）→ 该 token 的**第一条**绑定。

    token 形如 `<数字><至多一个空白><可选单位>`；按**删千分位与空白**后的前缀比对，与
    `narrative_schema._literal_token` 同一归一化口径（不派生任何等价写法）。
    """
    matches = bindings_for(bindings, token)
    return matches[0] if matches else None


def binding_compatible(sentence_binding: NumberBinding, fact_binding: NumberBinding) -> bool:
    """句子侧绑定与事实侧绑定是否**指向同一个数字命题**（item 3 的配对判据）。

    四轴全部要对上：`period_key`（同数字不同年 ⇒ 拒）、`metric_field`（把收入金额当收入
    占比 ⇒ 拒）、`unit_kind`（万元当亿元 ⇒ 拒）、`scope_key`（同数字不同业务 ⇒ 拒，用
    :func:`scope_compatible` 的互相包含，允许 `公司储能电池系统` 与 `储能电池系统` 这类
    正常改写）。调用方必须先确认两侧绑定都 :func:`complete`，否则「读不出」会被当成「不一样」。
    """
    return (sentence_binding.period_key == fact_binding.period_key
            and sentence_binding.metric_field == fact_binding.metric_field
            and sentence_binding.unit_kind == fact_binding.unit_kind
            and scope_compatible(fact_binding.scope_key, sentence_binding.scope_key))


def _literal(token: str) -> str:
    """删千分位与**全部**空白（与 `narrative_schema._literal_token` 同口径）。"""
    return re.sub(r"[\s,]", "", str(token or ""))

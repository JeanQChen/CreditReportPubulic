"""期间抽取（确定性、版本化）：从**所引原文**里核验明确期间。

规则（用户 2026-09-24 指令 二）：
- 期间只能来自**被引用材料自己的原文**（`payload_text.envelope_text` 解出的 `content.text`，
  即该 OutlineSpan 的精确定位原文）；
- **不得**用报告生成日（`report_as_of`）、财务快照期末、PDF 文件名、入库时间、相邻未核验
  标题或任何旁路字段补期间；
- 同一段原文出现**多个互不包含**的期间 → `ambiguous`，调用方**不得任选一条**；
- 抽取不到 → `none`，调用方不得据此编造，只能让事实走期间缺口路径；
- **期间必须落在「值自己那句话」里**（`period-extract-2`）：一段长 span 里前句写「报告期内」、
  后句另写「2025 年」时，**后文的年份不得追认前句**。生产调用点用
  :func:`extract_explicit_period_in_sentence`（按命题文本定位到唯一那句再抽）；
  :func:`extract_explicit_period` 保留为「整段扫描」的原义，只作对照与既有测试用。

本模块零 LLM、零网络、零 DB 写入；纯函数，同一输入恒得同一输出。
"""

from __future__ import annotations

import dataclasses
import re

#: 抽取口径版本。正则集或归一化口径变化必须改这里（结果会进入事实候选身份的下游）。
#:
#: `period-extract-2`（2026-10-04）：新增 :func:`extract_explicit_period_in_sentence`——
#: 「期间必须落在**值自己的那句话**里」的确定性口径。整段扫描（`period-extract-1` 的语义）
#: 保留为 :func:`extract_explicit_period` 的原义，但**生产调用点不再用它**：长 span 里
#: 前句写「报告期内」（无显式期间）、后句另写「2025 年」时，整段扫描会拿后文年份**追认前句**。
PERIOD_EXTRACTION_VERSION = "period-extract-2"

#: 抽取状态（封闭集合）。
PERIOD_EXTRACTED = "extracted"
PERIOD_ABSENT = "absent"
PERIOD_AMBIGUOUS = "ambiguous"

#: 期间类型（封闭集合），决定期间表达**不是**裸时点。
PERIOD_KIND_RANGE = "range"          # 2025年1月1日至2025年12月31日
PERIOD_KIND_POINT = "point"          # 截至2025年12月31日 / 2025年12月31日
PERIOD_KIND_YEAR = "year"            # 2025年度 / 2025年
PERIOD_KIND_HALF = "half"            # 2025年上半年 / 2025年1-6月
PERIOD_KIND_QUARTER = "quarter"      # 2025年第一季度
PERIOD_KIND_MONTH = "month"          # 2025年3月

_CJK = r"一-鿿"
_SEP = r"(?:至|到|—|–|~|～|-|－)"

#: 有序（长的优先）正则表：`(kind, pattern)`。每项必须**整体**匹配才能给出期间，
#: 因此 `2025年1月1日至2025年12月31日` 不会被拆成两个更短的时点。
_PERIOD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (PERIOD_KIND_RANGE, re.compile(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*" + _SEP + r"\s*"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    (PERIOD_KIND_HALF, re.compile(r"(\d{4})\s*年\s*(?:上半年|1\s*[-—～~]\s*6\s*月)")),
    (PERIOD_KIND_HALF, re.compile(r"(\d{4})\s*年\s*下半年")),
    (PERIOD_KIND_QUARTER, re.compile(
        r"(\d{4})\s*年\s*(?:第)?\s*([一二三四1-4])\s*季度")),
    (PERIOD_KIND_POINT, re.compile(r"截至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    (PERIOD_KIND_POINT, re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    (PERIOD_KIND_YEAR, re.compile(r"(\d{4})\s*年\s*度")),
    (PERIOD_KIND_MONTH, re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?!\s*\d)")),
    (PERIOD_KIND_YEAR, re.compile(r"(\d{4})\s*年(?!\s*[\d一二三四])")),
)

_QUARTER_DIGITS = {"一": "1", "二": "2", "三": "3", "四": "4"}

#: 月份/日期的合法域（越界即不是期间，按「抽不到」处理，不猜）。
_MONTH_MIN, _MONTH_MAX = 1, 12
_DAY_MIN, _DAY_MAX = 1, 31
_MIN_YEAR, _MAX_YEAR = 1900, 2199


@dataclasses.dataclass(frozen=True)
class PeriodExtraction:
    """一次期间抽取的结果（**声明式**，不含任何外部状态）。"""

    status: str
    kind: str | None = None
    #: 原文逐字切片（可回查：`text[span[0]:span[1]]` 必须逐字等于它）。
    literal: str = ""
    #: 规范化期间表达（进入 `FactCandidate.period` 的就是它）。
    display: str = ""
    #: 在原文中的精确位置。
    span: tuple[int, int] | None = None
    #: `ambiguous` 时给出全部互不包含的候选表达（升序），供审计；**不**表示可选。
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "status": self.status, "kind": self.kind, "literal": self.literal,
            "display": self.display,
            "span": list(self.span) if self.span else None,
            "candidates": list(self.candidates),
            "extraction_version": PERIOD_EXTRACTION_VERSION,
        }


def _display_of(kind: str, groups: tuple[str, ...]) -> str | None:
    """匹配分组 → 规范化期间表达；域越界或语义不成立 → ``None``。"""
    try:
        if kind == PERIOD_KIND_RANGE:
            y1, m1, d1, y2, m2, d2 = (int(g) for g in groups[:6])
            if not (_valid_date(y1, m1, d1) and _valid_date(y2, m2, d2)):
                return None
            if (y1, m1, d1) > (y2, m2, d2):
                return None
            return (f"{y1:04d}-{m1:02d}-{d1:02d}..{y2:04d}-{m2:02d}-{d2:02d}")
        if kind == PERIOD_KIND_HALF:
            year = int(groups[0])
            if not _valid_year(year):
                return None
            return f"{year:04d}年上半年" if len(groups) == 1 else f"{year:04d}年下半年"
        if kind == PERIOD_KIND_QUARTER:
            year = int(groups[0])
            digit = _QUARTER_DIGITS.get(groups[1], groups[1])
            if not _valid_year(year) or digit not in "1234":
                return None
            return f"{year:04d}年Q{digit}"
        if kind == PERIOD_KIND_POINT:
            year, month, day = (int(g) for g in groups[:3])
            if not _valid_date(year, month, day):
                return None
            return f"{year:04d}-{month:02d}-{day:02d}"
        if kind == PERIOD_KIND_YEAR:
            year = int(groups[0])
            if not _valid_year(year):
                return None
            return f"{year:04d}年度"
        if kind == PERIOD_KIND_MONTH:
            year, month = int(groups[0]), int(groups[1])
            if not _valid_year(year) or not (_MONTH_MIN <= month <= _MONTH_MAX):
                return None
            return f"{year:04d}-{month:02d}"
    except (TypeError, ValueError):
        return None
    return None


def _valid_year(year: int) -> bool:
    return _MIN_YEAR <= year <= _MAX_YEAR


def _valid_date(year: int, month: int, day: int) -> bool:
    if not _valid_year(year) or not (_MONTH_MIN <= month <= _MONTH_MAX):
        return False
    return _DAY_MIN <= day <= _DAY_MAX


def extract_explicit_period(text: str) -> PeriodExtraction:
    """从一段原文里抽取**唯一且明确**的期间。

    判定顺序：先按模式表找出所有匹配（长模式优先，短模式**不得**覆盖长模式已占用的区间），
    再按规范化表达去重：
    - 恰好一种表达 → `extracted`（返回逐字切片与精确 span）；
    - 多种表达 → `ambiguous`（返回全部候选，**不选**）；
    - 无匹配 → `absent`。
    """
    source = str(text or "")
    if not source:
        return PeriodExtraction(status=PERIOD_ABSENT)
    hits: list[tuple[int, int, str, str, tuple[str, ...]]] = []
    occupied: list[tuple[int, int]] = []
    for kind, pattern in _PERIOD_PATTERNS:
        for match in pattern.finditer(source):
            start, end = match.span()
            # 长模式优先：与已占用区间重叠的短匹配是被吞掉的片段，不是第二个期间。
            if any(start < o_end and o_start < end for o_start, o_end in occupied):
                continue
            display = _display_of(kind, match.groups())
            if display is None:
                continue
            occupied.append((start, end))
            hits.append((start, end, kind, display, tuple(str(g) for g in match.groups())))
    if not hits:
        return PeriodExtraction(status=PERIOD_ABSENT)
    hits.sort(key=lambda h: h[0])
    displays = tuple(sorted({h[3] for h in hits}))
    if len(displays) > 1:
        return PeriodExtraction(status=PERIOD_AMBIGUOUS, candidates=displays)
    start, end, kind, display, _groups = hits[0]
    return PeriodExtraction(
        status=PERIOD_EXTRACTED, kind=kind, literal=source[start:end], display=display,
        span=(start, end))


#: 句边界（**封闭集合**）：中文句末标点 + 换行。不引入空格（空格不是句子边界，句内的
#: 千分位/单位空格必须留在同一句里）。
_SENTENCE_BOUNDARIES = "。；！？!?;\n"
_SENTENCE_BOUNDARY_SET = frozenset(_SENTENCE_BOUNDARIES)


def split_sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """把一段原文按**确定性句边界**切成交错、连续、覆盖全文的半点区间。

    边界字符归**前一句**（`。` 留在句尾）。空句（连续边界之间没有非空字符）也被切出来，
    但它们不含任何字符，不会影响「值落在哪一句」的判定。纯函数，无正则、无语言模型。
    """
    source = str(text or "")
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(source):
        if char in _SENTENCE_BOUNDARY_SET:
            spans.append((start, index + 1))
            start = index + 1
    if start < len(source):
        spans.append((start, len(source)))
    return tuple(spans)


def _squash_whitespace(text: str) -> str:
    """去掉**全部**空白后的匹配串（仅供定位，不改变任何被返回的逐字切片）。"""
    return "".join(ch for ch in str(text or "") if not ch.isspace())


def locate_sentence_span(text: str, *, anchor: str) -> tuple[int, int] | None:
    """定位 `anchor`（值的命题文本）落在原文的**哪一句**；不唯一即返回 ``None``。

    判据：把原文与 anchor 各自去掉空白后求「哪一句包含 anchor」。**恰好一句**才返回该句
    区间；0 句（措辞不是逐字，无法定位）或 ≥2 句（同句措辞重复出现）一律 fail-closed 返回
    ``None``——「定位不了」不得退化成「扫全段」。
    """
    key = _squash_whitespace(anchor)
    if not key:
        return None
    matches = [span for span in split_sentence_spans(text)
               if key in _squash_whitespace(text[span[0]:span[1]])]
    if len(matches) != 1:
        return None
    return matches[0]


def extract_explicit_period_in_sentence(
        text: str, *, anchor: str) -> PeriodExtraction:
    """只在**值自己那句话**里抽期间（`period-extract-2` 的生产口径）。

    与 :func:`extract_explicit_period` 的差别只有一个：候选期间先被限定到
    :func:`locate_sentence_span` 定位到的那一句，**后文句子里的年份不得追认前句**。

    三种结果：
      * 定位到该句、句内有唯一显式期间 → ``extracted``（`span` 已换算回全文坐标）；
      * 定位到该句、句内没有显式期间 → ``absent``（调用方据此走「本轮未取得显式期间」，**不**猜）；
      * 定位不到该句（anchor 不是逐字、或重复出现）→ ``absent``（fail-closed，**不**退到整段）。
    """
    span = locate_sentence_span(text, anchor=anchor)
    if span is None:
        return PeriodExtraction(status=PERIOD_ABSENT)
    source = str(text or "")
    local = extract_explicit_period(source[span[0]:span[1]])
    if local.status != PERIOD_EXTRACTED or local.span is None:
        return local
    rebased = (span[0] + local.span[0], span[0] + local.span[1])
    return PeriodExtraction(
        status=local.status, kind=local.kind, literal=local.literal, display=local.display,
        span=rebased, candidates=local.candidates)

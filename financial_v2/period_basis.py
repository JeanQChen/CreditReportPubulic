"""财务事实的**期间口径**（`end` 时点 / `flow` 期间）与**期间表达**（纯函数，无 I/O）。

M930-3 返修 P3：权威事实必须自己携带「这个数值说的是一个**时点**还是一个**期间**」，
以及读者可读的**期间表达**。两者都是权威命题的一部分：

* `SOLV_INTEREST_COVER`（利息保障倍数）的期间要求是 `flow` —— 它说的是**本报告期**的
  保障倍数。把这样一个数值挂在列名 `2025-12-31` 下，读者会把它读成「2025-12-31 这一时点
  的利息保障倍数」，而那一刻并没有这个量。因此表格列轴与正文期间表达都必须写成
  `2025年度`，而不是裸的期间末日。
* `TOTAL_ASSETS` 这类资产负债表科目是 `end`（时点余额），其表达是 `2025年末`。

本模块是这两件事的**唯一**实现：`financial_worker`（构造事实）与下游只有这一处口径。
它刻意**不**读任何公司名、年份、页码或 fixture 专用词表：口径只由权威自己的
`period_requirement`（公式定义）与 `statement_type`（快照条目）决定，表达只由权威自己的
期间记号（`YYYY-MM-DD`）与口径决定。

**取不到日期时逐字保留权威自己的期间记号**（`period_expression` 的 fallback）：这不是
"补一个更漂亮的表达"，而是拒绝在被授权的记号之外发明任何期间措辞——权威没给出可解析的
日期时，本模块不会替它写成 `2025年度`。
"""

from __future__ import annotations

#: 期间口径规则的版本（供 `FinancialFactPack` / artifact 记录）。
#:
#: `pb-1`（M930-3 返修 P3）：首次建立时点/期间两态与期间表达的唯一实现。
PERIOD_BASIS_VERSION = "pb-1"

#: 时点量（期末余额）。
BASIS_END = "end"
#: 期间量（本报告期流量；比率类指标的分子是流量，或指标表达的是期间内的变化）。
BASIS_FLOW = "flow"
PERIOD_BASES = (BASIS_END, BASIS_FLOW)

#: 公式定义的期间要求取值（`financial_v2.formulas`）→ 口径。**只有 `end` 是时点量**：
#: `flow` / `flow/end` / `flow/avg` 的分子都是流量，`yoy_flow` / `yoy_end` 表达的是**期间内**
#: 的变化，因此它们的呈现期间一律是「期间」，不是「时点」。
_END_REQUIREMENTS = frozenset({BASIS_END})

#: 三大报表类型 → 口径（`balance_sheet` 是时点余额，利润表/现金流量表是期间流量）。
_STATEMENT_BASIS = {
    "balance_sheet": BASIS_END,
    "income_statement": BASIS_FLOW,
    "cash_flow": BASIS_FLOW,
}

#: 期间类型（快照条目）→ 是否年度。仅用于「非 12 月期末」的兜底表达判定，不参与口径判定。
PERIOD_TYPES = ("annual", "quarterly", "interim")

#: 12 个自然月 → 季度序号（期间表达的兜底用），以及季度序号的汉字写法。
_QUARTER_BY_MONTH = {
    1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3, 8: 3, 9: 3, 10: 4, 11: 4, 12: 4,
}
_QUARTER_CN = {1: "一", 2: "二", 3: "三", 4: "四"}


class UnsupportedStatementType(ValueError):
    """无法判定口径的报表类型（fail-closed：宁可拒，不猜一个口径）。"""


def basis_from_period_requirement(period_requirement: str) -> str:
    """公式定义的期间要求 → 口径（`end` → 时点，其余 → 期间）。"""
    text = str(period_requirement or "").strip()
    if not text:
        raise ValueError("period_requirement 不得为空（口径不得由调用方猜）")
    return BASIS_END if text in _END_REQUIREMENTS else BASIS_FLOW


def basis_from_statement_type(statement_type: str) -> str:
    """快照条目的报表类型 → 口径（未知类型 fail-closed，不默认成时点或期间）。"""
    text = str(statement_type or "").strip()
    basis = _STATEMENT_BASIS.get(text)
    if basis is None:
        raise UnsupportedStatementType(
            f"未登记的报表类型 {statement_type!r}：无法判定它是时点量还是期间量"
            f"（已登记：{sorted(_STATEMENT_BASIS)}）")
    return basis


def _parse_year_month(period: str) -> tuple[int, int] | None:
    """解析权威期间记号里的 `YYYY-MM-DD` 年月；不是这个形状就返回 `None`。"""
    text = str(period or "").strip()
    parts = text.split("-")
    if len(parts) != 3:
        return None
    year, month, day = parts
    if not (len(year) == 4 and len(month) == 2 and len(day) == 2):
        return None
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    month_i = int(month)
    if not 1 <= month_i <= 12:
        return None
    return int(year), month_i


def is_authority_period_token(period: str) -> bool:
    """该期间记号是否是**权威的日期形状**（`YYYY-MM-DD`）。

    用途只有一个：区分「这是一个真实的报告期记号，因此它必须有口径」与「这不是日期形状，
    本模块不该替它发明表达」。判据与 `period_expression` 的解析**同一处**（不许两套）。
    """
    return _parse_year_month(period) is not None


def year_month_of(period: str) -> tuple[int, int] | None:
    """权威期间记号的 `(年, 月)`；不是 `YYYY-MM-DD` 形状时返回 `None`。

    这是上面那次解析的**公开读口**：调用方（如 `financial_v2.derived_facts` 判定「完整年度
    期末」）需要年月，但不得自己再写一份解析——本模块仍是期间记号的唯一解析实现。
    它不改变任何既有语义，因此 `PERIOD_BASIS_VERSION` 保持 `pb-1`。
    """
    return _parse_year_month(period)


def period_expression(period: str, basis: str) -> str:
    """权威期间记号 + 口径 → 读者可读的**期间表达**。

    规则只由「期间末日所在的年/月」与口径决定，因此不存在按公司/年份/页码特判的余地：

    | 口径 | 3 月 | 6 月 | 9 月 | 12 月 | 其它 |
    |---|---|---|---|---|---|
    | `end`（时点） | `Y年一季度末` | `Y年半年末` | `Y年三季度末` | `Y年末` | `Y年M月末` |
    | `flow`（期间） | `Y年一季度` | `Y年半年度` | `Y年前三季度` | `Y年度` | `Y年1-M月` |

    `Y` 是期间末日所在年（`2025-12-31` → `2025年度`），不是报告生成日，也不是文件名里的年份。
    `period` 不是 `YYYY-MM-DD` 形状时**逐字返回原记号**：本模块不替权威发明期间措辞。
    """
    if basis not in PERIOD_BASES:
        raise ValueError(f"未知的期间口径 {basis!r}（封闭取值：{list(PERIOD_BASES)}）")
    ymd = _parse_year_month(period)
    if ymd is None:
        return str(period or "")
    year, month = ymd
    if basis == BASIS_END:
        if month == 12:
            return f"{year}年末"
        if month == 6:
            return f"{year}年半年末"
        if month in (3, 9):
            return f"{year}年{_QUARTER_CN[_QUARTER_BY_MONTH[month]]}季度末"
        return f"{year}年{month}月末"
    if month == 12:
        return f"{year}年度"
    if month == 9:
        return f"{year}年前三季度"
    if month == 6:
        return f"{year}年半年度"
    if month == 3:
        return f"{year}年一季度"
    return f"{year}年1-{month}月"


__all__ = [
    "PERIOD_BASIS_VERSION", "BASIS_END", "BASIS_FLOW", "PERIOD_BASES", "PERIOD_TYPES",
    "UnsupportedStatementType", "basis_from_period_requirement",
    "basis_from_statement_type", "is_authority_period_token", "year_month_of",
    "period_expression",
]

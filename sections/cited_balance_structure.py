"""`fin_balance_structure` 的**确定性**呈现（`cbs-2`）。

与 [sections/cited_source_scope.py](cited_source_scope.py)（`css-2`）是**同一套做法的第二个实例**，
不是它的复制：那一栏问的是「报表本身齐不齐、什么口径」（答案在 `FinancialPackArtifact` 的字段里），
本栏问的是「资产负债的结构、重大科目变化、15% 以上科目」——答案在**本节权威自己的合格事实**里：
`item_*` 科目金额事实（`FinancialFact`，`code` 是 `standard_item_code`）与 `SOLV_DEBT_RATIO`
这类**已由权威算好**的指标事实。

## 为什么这一栏不交给模型写

三栏 Contract 要求（`fin_balance_structure.balance_structure` / `.major_account_changes` /
`.fifteen_pct_forced_analysis`）的每一个取值都能在权威里**逐条指认**：哪条事实、哪个期间、
哪条引用。让模型把「总资产 8,000 亿元」这类数字从材料里再抄一遍，不会让这个数字更可信，
只会把一个可以直接回查的权威值变成一句需要逐句复核的生成文本——而本链为此付出的代价
（一份清单、一次写作调用、一次审阅、一套逐句机械核对）买不到任何新增的信息。

因此本模块**零模型调用**：`model_calls_issued` 恒为 0，且进身份体。

## 本模块的算术边界（哪几件数是我们算的，哪几件不是）

* **取用**：金额一律逐字取权威事实自己的 `display`（`sections.common.format_yuan_amount` 的产物），
  本模块不自己渲染金额。单位、期间、口径同样取事实自己的字段（`unit` / `period_label` /
  `period_basis`），不从期间记号的**外观**补一个。
* **在算的**：只有**两个已登记合格事实之间的确定性换算**——结构占比（`部分 / 合计`）、
  15% 筛选、逐期变动幅度。它们全部用 `Decimal` 在**未舍入原值**上算，舍入只发生在渲染那一步，
  且每条读数都把两个输入事实的 id 与算式一起记下（`*_fact_id` / `formula`）。
* **不在算的**：**资产负债率**。权威自己产出过 `SOLV_DEBT_RATIO` 指标事实（有自己的公式与
  provenance），本模块**取它**；取不到的期间落 typed 缺口，**不**用「负债总额 ÷ 资产总额」
  自己补一个——那会造出一个权威从未产出过的数，并让读者以为它和别的指标同源。

## 阈值出处（两个数不是同一类东西，不得混说）

* **15%** 是**冻结 Contract 的判据**：`fin_balance_structure.fifteen_pct_forced_analysis` 的
  `complete_set_rule` 逐字写着「占资产或负债 15% 以上的科目强制分析」。构造期在 Contract 投影上
  逐字见证这一点（见 `_contract_threshold_rule`），Contract 改了数而本模块没改即当场拒绝。
* **20%** **不是** Contract 的判据，它是**本批（M930-3 指令 II.2）规定的展示筛选**：
  「变动达到 20% 的重点科目」。因此它只用来**挑选要展开讲哪几条**，逐条试算的读数
  （含未命中的）全部在册——「未列出」不等于「未试算」，更不等于「Contract 说它不重要」。

## 本栏**不**做的事（每一条都对应一种会读错的写法）

* **不造「变化原因」。** `major_account_changes.required_fields` 含「变化原因」，而本节权威
  **没有承载原因的字段**。原因逐条留 typed 缺口，绝不用模型推断或常识补足。
* **不把合计行当被筛科目。** `TOTAL_ASSETS` / `TOTAL_LIABILITIES` / `TOTAL_EQUITY` /
  `TOTAL_LIABILITIES_AND_EQUITY` 是总量轴本身，占自己的比例恒为 100%：拿它们去比任何一侧
  的总量都会得到一个看起来合理、含义错误的百分比。本栏对它们逐条落
  `no_authority_field_for_required_item`，不替它们挑一个分母。
* **不把合并科目当独立科目。** 快照里没有 `ACCOUNTS_RECEIVABLE` / `FIXED_ASSETS` 的条目
  （`_m930_3_probe/balance_item_statement_probe.py` 只读核对过），本栏因此对这两个 code
  逐条落 `no_registered_fact_for_item`——**不**拿任何合并口径的科目顶替它们。
* **不出应收账款明细表。** 那一项（指令 II.3）需要先审计 `EvidenceNoteFact` 接口与 PDF 单元格
  定位，触及冻结权威口径，属**单独子项裁决**；本栏把它记成 typed 注记，不预先产出任何数字。
* **不复制偿债指标表。** 那是 `fin_solvency` 那一栏的表（由 `cited_financial_table` 确定性成表），
  本栏只记一条注记指向它。

本模块纯确定性：不发起任何模型调用、不读库、不检索、不联网。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

__all__ = [
    "CITED_BALANCE_STRUCTURE_SCHEMA_VERSION",
    "CITED_BALANCE_STRUCTURE_POLICY_VERSION",
    "BALANCE_STRUCTURE_ROUTING_VERSION",
    "BALANCE_STRUCTURE_PRODUCER_KIND",
    "BALANCE_STRUCTURE_ITEM_STATES",
    "BALANCE_STRUCTURE_GAP_REASONS",
    "BALANCE_STRUCTURE_ASPECT_ROUTING",
    "FIFTEEN_PCT_THRESHOLD",
    "MAJOR_CHANGE_THRESHOLD",
    "CitedBalanceStructureError",
    "BalanceValue",
    "BalanceRatio",
    "BalanceShare",
    "BalanceChange",
    "BalanceStructureAspectItem",
    "BalanceStructureNote",
    "CitedBalanceStructurePresentation",
    "build_cited_balance_structure",
    "render_cited_balance_structure_markdown",
]

#: 产物 schema 版本。条目字段集变化即升版。
#: `cbs-1 → cbs-2`：`BalanceChange` 条目新增 `formula` 键（与 `ratio`／`share` 同构）。
CITED_BALANCE_STRUCTURE_SCHEMA_VERSION = "cbs-2"
#: 呈现政策版本（判据集合、缺口理由集合、阈值、读哪些字段任一变化即升版）。
#: `cbsp-1 → cbsp-2`：读法三处变化——(1) 新增**指标命名空间**（资产负债率改从本节范围的
#: `kind="calculation"` 事实取，不再按本 topic 过筛而读空）；(2) 15%／20% 两段话改为**逐期**
#: 陈述（`time_scope=THREE_YEARS_PLUS_LATEST`，只讲最后一期会咽掉其余期间的命中）。
CITED_BALANCE_STRUCTURE_POLICY_VERSION = "cbsp-2"
#: 「哪一条 Contract 要求由哪一族读数回答」这张表的版本。
BALANCE_STRUCTURE_ROUTING_VERSION = "bsr-1"

#: 产出者身份。它**不是**任何一个模型：这一栏没有生成调用，产物里不许出现模型名或 prompt 版本。
BALANCE_STRUCTURE_PRODUCER_KIND = "deterministic_presentation"

#: 条目状态，封闭两档。
BALANCE_STRUCTURE_ITEM_STATES = ("determined", "gap")

#: 缺口原因，封闭集合。名称直指「取不到」的那一层，不含任何程度的判断。
BALANCE_STRUCTURE_GAP_REASONS = (
    #: 本节权威里**没有**这个 code 的合格事实（不是「找不到」，是「没提供」）。
    "no_registered_fact_for_item",
    #: 该 code 在权威里**有**事实，但那条事实没有可用的权威值（`value_text` 为空）。
    #: 它与上一条是两件事：上一条是「权威里没有这一项」，这一条是「有这一项但没有值」。
    "fact_not_displayable",
    #: 分母为 0：占比在数学上不成立，不得用一个「0%」冒充已算出。
    "comparison_base_zero",
    #: 冻结 Contract 要求、而本节权威**没有承载字段**（例：变化原因）——
    #: 不是「检索没找到」，是「权威里没有这个字段」。
    "no_authority_field_for_required_item",
    #: 需要单独子项裁决才能产出（例：应收账款明细，指令 II.3）。
    "requires_separate_adjudication",
)

#: 冻结 Contract `fifteen_pct_forced_analysis.complete_set_rule` 里的阈值，逐字复述。
FIFTEEN_PCT_THRESHOLD = Decimal("0.15")
#: 本批（M930-3 指令 II.2）规定的**展示**筛选阈值。**不是**冻结 Contract 判据，见模块 docstring。
MAJOR_CHANGE_THRESHOLD = Decimal("0.20")
#: 在 Contract 的 `complete_set_rule` 里逐字见证 15% 用的词。
_CONTRACT_THRESHOLD_WORD = "15%"

#: 只在**渲染**时量化；`value` 本身保持未舍入。
_TWO_DP = Decimal("0.01")
_HUNDRED = Decimal("100")
#: 科目事实（`kind="fact"`）的 `code` 是 `standard_item_code`；指标事实是 formula_id。两个命名空间
#: 分开查，免得某天一个 code 重名时表现为一条静默的错取值。
_KIND_ITEM = "fact"
#: 资产负债率的**唯一**取值来源：权威自己的指标事实。**不**在此处相除（见模块 docstring）。
_DEBT_RATIO_FORMULA = "SOLV_DEBT_RATIO"

#: 总量轴：它们**是**分母，不是被筛的科目。
_TOTAL_CODES = ("TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY")
#: 合计行：占自己的比例恒为 100%，拿它比任何一侧总量都是错栏。逐条落 typed 缺口。
_SUMMARY_CODES = ("TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY",
                  "TOTAL_LIABILITIES_AND_EQUITY")
#: 结构轴：`(科目, 分母科目)`。资产侧比资产总额、负债侧比负债总额。
_STRUCTURE_CODES = (
    ("CURRENT_ASSETS", "TOTAL_ASSETS"), ("NON_CURRENT_ASSETS", "TOTAL_ASSETS"),
    ("CURRENT_LIABILITIES", "TOTAL_LIABILITIES"),
    ("NON_CURRENT_LIABILITIES", "TOTAL_LIABILITIES"),
)
#: 15% 强筛的**分母轴**：每一侧一根，逐字对应 Contract 的「占资产或负债」。
_SHARE_DENOMINATOR_BY_SIDE = {"assets": "TOTAL_ASSETS", "liabilities": "TOTAL_LIABILITIES"}
#: 「每一个已登记科目落在哪一侧」——**显式表**，不按 code 前缀猜。
#:
#: 按前缀猜（`CASH_`/`INVENTORY`/… 属于资产侧）在这张表上是会错的：`BONDS_PAYABLE` 与
#: `LONG_TERM_BORROWINGS` 的名字里没有任何负债侧的记号。导入期当场核对这张表与
#: `financial_worker._BALANCE_SHEET_ITEMS` 互补——少一个 code 即 `CitedBalanceStructureError`，
#: 而不是让那条科目悄悄不参与筛选。
_BALANCE_SHEET_SIDE: dict[str, str] = {
    "CURRENT_ASSETS": "assets", "NON_CURRENT_ASSETS": "assets",
    "CASH_AND_EQUIVALENTS": "assets", "INVENTORY": "assets",
    "ACCOUNTS_RECEIVABLE": "assets", "FIXED_ASSETS": "assets",
    "GOODWILL": "assets", "INTANGIBLE_ASSETS": "assets",
    "CURRENT_LIABILITIES": "liabilities", "NON_CURRENT_LIABILITIES": "liabilities",
    "SHORT_TERM_BORROWINGS": "liabilities", "LONG_TERM_BORROWINGS": "liabilities",
    "BONDS_PAYABLE": "liabilities",
}


class CitedBalanceStructureError(Exception):
    """确定性呈现的构造期拒绝。**不是**一次运行结果。"""


def balance_sheet_items() -> frozenset[str]:
    """本节认定属于资产负债表的科目集合（`financial_worker` 的只读读口）。

    经函数取而不是在导入期 `from ... import`：本模块与 `financial_worker` 之间不建立导入期
    依赖，免得两个模块在（未来的）互相引用上打结。
    """
    from sections import financial_worker as FW

    return frozenset(FW._BALANCE_SHEET_ITEMS)


def _assert_side_table_covers_balance_sheet() -> None:
    """导入期核对：`_BALANCE_SHEET_SIDE ∪ _SUMMARY_CODES` 恰好等于资产负债表科目集。

    这张表漏一个 code 的后果是具体的：那条科目**不会**出现在 15% 筛选里，而读者面上看不出
    「少了一条」——这正是本栏最不该有的失败形状。因此不等到运行期，导入即判。
    """
    covered = set(_BALANCE_SHEET_SIDE) | set(_SUMMARY_CODES)
    items = set(balance_sheet_items())
    if covered != items:
        raise CitedBalanceStructureError(
            "`_BALANCE_SHEET_SIDE` 与 `_SUMMARY_CODES` 合起来必须恰好覆盖本节认定属于资产负债表的"
            f"科目：多 {sorted(covered - items)}、少 {sorted(items - covered)}"
            "（少一条的表现是「那条科目悄悄不参与筛选」，不得靠人记得补）")
    for code, side in _BALANCE_SHEET_SIDE.items():
        if side not in _SHARE_DENOMINATOR_BY_SIDE:
            raise CitedBalanceStructureError(
                f"科目 {code!r} 被归到未登记的一侧 {side!r}"
                f"（封闭取值 {sorted(_SHARE_DENOMINATOR_BY_SIDE)}）")


# ---------------------------------------------------------------------------
# 取值
# ---------------------------------------------------------------------------

def _pct_text(ratio: Decimal) -> str:
    """比率 → 读者串（`0.4321` → `43.21%`）。**只在展示时舍入**。"""
    if not isinstance(ratio, Decimal):
        raise CitedBalanceStructureError(f"比率必须是 Decimal，得到 {ratio!r}")
    quantized = (ratio * _HUNDRED).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        text = "0"
    return f"{text}%"


def _money_text(fact: Any) -> str:
    """金额渲染串：**逐字**取权威自己的 `display`，本模块不重新渲染金额。

    权威没有给出渲染串时返回空串（调用方把这条事实记成不可陈列），而不是自己拼一个——
    「1 元」与「1 亿元」的差别不是渲染细节，是数量级。
    """
    return str(getattr(fact, "display", "") or "").strip()


def _raw_value(fact: Any) -> Decimal | None:
    """事实的权威值。

    本节权威给出的事实是 `FinancialFactProjection`（**只读投影**，`value_text` 是 Decimal 的
    规范字符串，`__post_init__` 已逐条核过它可精确往返），不是 `FinancialFact`。因此这里先读
    投影的读口，再退回 `FinancialFact` 自己的 `value`——两个形状都支持，但**不**对字符串做
    任何浮点化：`Decimal(value_text)` 是精确的，`float()` 不是。
    """
    reader = getattr(fact, "value_decimal", None)
    if callable(reader):
        value = reader()
        return value if isinstance(value, Decimal) else None
    value = getattr(fact, "value", None)
    if isinstance(value, Decimal):
        return value
    text = getattr(fact, "value_text", None)
    return Decimal(str(text)) if text not in (None, "") else None


def _citation_identity(fact: Any) -> str:
    """事实自己的引用身份。**取事实的引用**，不从清单里另配一个键：本栏的事实不进写作清单
    （它们属于确定性呈现的 topic），因此这里不可能有 `fNN` 键——编一个才是错。

    投影把引用带成**字典**，而引用身份的**唯一**实现是 `sections.schema.citation_identity`
    （它收 `CitationRef`）。这里把字典物化回 `CitationRef` 再交给它，而不是另写一遍格式化——
    两处各写一遍的下场是某天一处补了新字段、另一处没有，而两条引用读起来仍然一样。
    字典里出现未登记字段即构造期拒绝：那是「引用形状变了而本模块没跟上」，不得静默丢弃。
    """
    from harness.schema import CitationRef

    from sections import schema as SC

    citation = getattr(fact, "citation", None)
    if isinstance(citation, CitationRef):
        return SC.citation_identity(citation)
    if isinstance(citation, dict) and citation:
        fields = set(CitationRef.__dataclass_fields__)
        unknown = sorted(set(citation) - fields)
        if unknown:
            raise CitedBalanceStructureError(
                f"事实 {getattr(fact, 'fact_id', '')!r} 的引用里有未登记字段 {unknown}："
                "引用形状变了而本模块没跟上，不得静默丢弃")
        return SC.citation_identity(CitationRef(**citation))
    raise CitedBalanceStructureError(
        f"事实 {getattr(fact, 'fact_id', '')!r} 没有引用：本栏的每一个数都必须能回查")


@dataclass(frozen=True)
class _FactIndex:
    """本节权威的合格事实索引，**两个命名空间**（`cbsp-2` 起）。

    * `by_code_period` —— **科目金额事实**（`kind="fact"`，`code` 是 `standard_item_code`）：
      只收**路由到本 topic** 的那些。它们是本栏的陈述对象（总量轴、结构比、15% 强筛、
      20% 变动），别的 topic 的科目事实不归本栏讲。
    * `metrics_by_code_period` —— **指标事实**（`kind="calculation"`，`code` 是 formula_id）：
      收**本节范围内**的全部 topic。这一支是为资产负债率建的：权威自己的 `SOLV_DEBT_RATIO`
      按 `fpr-2` 的路由落在 `fin_solvency` 那一栏——那一栏**属于本节**（它是本节的偿债指标栏），
      不是本节之外。只按本 topic 过滤会把这条事实整批滤掉，于是正文会在权威**明明产出过**
      这个数时写下「权威从未产出过」：那是一条读者面上的**假陈述**，比少一个数更糟
      （`cbsp-1` 的实测读数 `evaluation/results/m930_3_cited_offline_dual_v2_r1/financial/`）。

    两个命名空间**分开**，是因为两侧 `code` 的来路不同（科目 code 来自
    `standard_item_code`，指标 code 是 formula_id）：合成一张匿名大表会把「某天一个 code
    重名」表现为一条静默的错取值。
    """

    periods: tuple[str, ...]
    by_code_period: dict[tuple[str, str], Any]
    metrics_by_code_period: dict[tuple[str, str], Any]

    @classmethod
    def build(cls, *, authority: Any, topic_id: str) -> "_FactIndex":
        artifact = getattr(authority, "artifact", None)
        if artifact is None:
            raise CitedBalanceStructureError(
                f"节权威没有财务 artifact，topic {topic_id!r} 的每一栏都要逐条读它："
                "没有权威事实就不得用别的来源凑")
        periods = tuple(str(p) for p in (getattr(artifact, "periods", ()) or ()))
        if not periods:
            raise CitedBalanceStructureError(
                "财务 artifact 没有声明任何期间：没有期间轴的资产负债读数不得合成一栏")
        if len(set(periods)) != len(periods):
            raise CitedBalanceStructureError(f"财务 artifact 的期间轴有重复项：{list(periods)}")
        #: 本节范围**逐字取自权威自己的声明**，不另写一张名单：指标事实「属不属于本节」这件事
        #: 只能有一个出处，否则本栏的读法与 `scan_financial` 的取法会各自漂移。
        section_topics = tuple(str(t) for t in (getattr(authority, "topic_ids", ()) or ()))
        if not section_topics:
            raise CitedBalanceStructureError(
                "节权威没有声明本节 topic 集合：没有范围就无从判定「这条指标事实属于本节」，"
                "不得用「除了本节之外都算」这类兜底口径")
        items: dict[tuple[str, str], Any] = {}
        metrics: dict[tuple[str, str], Any] = {}
        for fact in tuple(getattr(artifact, "facts", ()) or ()):
            code = str(getattr(fact, "code", "") or "").strip()
            period = str(getattr(fact, "period", "") or "").strip()
            if not code or not period:
                raise CitedBalanceStructureError(
                    f"事实 {getattr(fact, 'fact_id', '')!r} 缺 code 或期间："
                    "不可陈列的事实不得进本栏索引")
            topic = str(authority.topic_for_fact(str(getattr(fact, "fact_id", ""))))
            if str(getattr(fact, "kind", "") or "") == _KIND_ITEM:
                if topic != topic_id:
                    continue
                target, noun = items, "科目"
            else:
                #: 范围外的指标事实（`OUTSIDE_SECTION_TOPIC`）**不收**：那是「本节看不到」，
                #: 与「本节看得到而本栏不引」是两件事，不得共用一个措辞。
                if topic not in section_topics:
                    continue
                target, noun = metrics, "指标"
            key = (code, period)
            if key in target:
                raise CitedBalanceStructureError(
                    f"{noun} {code!r} 在期间 {period!r} 上有两条事实"
                    f"（{target[key].fact_id!r} 与 {fact.fact_id!r}）：同一格不得归属两条断言")
            target[key] = fact
        return cls(periods=periods, by_code_period=items,
                   metrics_by_code_period=metrics)

    def fact(self, code: str, period: str) -> Any | None:
        """本 topic 的一条**科目**事实。"""
        return self.by_code_period.get((str(code), str(period)))

    def metric(self, code: str, period: str) -> Any | None:
        """本节范围内的一条**指标**事实。**不**按本 topic 过滤——理由见类 docstring。"""
        return self.metrics_by_code_period.get((str(code), str(period)))

    def periods_with(self, code: str) -> tuple[str, ...]:
        return tuple(p for p in self.periods if (str(code), p) in self.by_code_period)

    def period_label(self, period: str) -> str:
        """期间表达逐条取事实自己的 `period_label`；该期一条事实都没有时退回期间记号本身。"""
        for (code, _p), fact in self.by_code_period.items():
            if _p != period:
                continue
            label = str(getattr(fact, "period_label", "") or "")
            if label:
                return label
        return str(period)


# ---------------------------------------------------------------------------
# 读数类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BalanceValue:
    """一个权威数字本身。`value_text` 是未舍入原值的规范串（供复算），不是给读者看的。"""

    label: str
    code: str
    fact_id: str
    period: str
    period_label: str
    period_basis: str
    unit: str
    display: str
    value_text: str
    citation_identity: str

    kind = "value"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "code": self.code,
                "fact_id": self.fact_id, "period": self.period,
                "period_label": self.period_label, "period_basis": self.period_basis,
                "unit": self.unit, "display": self.display,
                "value_text": self.value_text, "citation_identity": self.citation_identity}


@dataclass(frozen=True)
class BalanceRatio:
    """两个**已登记合格事实**之间的确定性换算（结构占比）。**不是**独立权威指标。"""

    label: str
    period: str
    period_label: str
    numerator_fact_id: str
    denominator_fact_id: str
    numerator_code: str
    denominator_code: str
    ratio_text: str
    value_text: str

    kind = "ratio"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "period": self.period,
                "period_label": self.period_label,
                "numerator_fact_id": self.numerator_fact_id,
                "denominator_fact_id": self.denominator_fact_id,
                "numerator_code": self.numerator_code,
                "denominator_code": self.denominator_code,
                "formula": f"{self.numerator_code} / {self.denominator_code}",
                "ratio_text": self.ratio_text, "value_text": self.value_text}


@dataclass(frozen=True)
class BalanceShare:
    """15% 强筛的一条逐条读数：`part / whole`，带分母是哪一根总量轴。"""

    label: str
    code: str
    fact_id: str
    period: str
    period_label: str
    denominator_code: str
    denominator_fact_id: str
    share_text: str
    value_text: str
    selected: bool

    kind = "share"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "code": self.code,
                "fact_id": self.fact_id, "period": self.period,
                "period_label": self.period_label,
                "denominator_code": self.denominator_code,
                "denominator_fact_id": self.denominator_fact_id,
                "formula": f"{self.code} / {self.denominator_code}",
                "share_text": self.share_text, "value_text": self.value_text,
                "threshold_text": _pct_text(FIFTEEN_PCT_THRESHOLD),
                "selected": self.selected}


@dataclass(frozen=True)
class BalanceChange:
    """逐期变动幅度的一条逐条读数。`comparison_label` 说明**跟哪一期比**。"""

    label: str
    code: str
    current_fact_id: str
    prior_fact_id: str
    current_period: str
    prior_period: str
    comparison_label: str
    is_year_on_year: bool
    direction: str
    change_text: str
    value_text: str
    selected: bool

    kind = "change"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "code": self.code,
                "current_fact_id": self.current_fact_id, "prior_fact_id": self.prior_fact_id,
                "current_period": self.current_period, "prior_period": self.prior_period,
                "comparison_label": self.comparison_label,
                "is_year_on_year": self.is_year_on_year, "direction": self.direction,
                #: 与 `ratio`/`share` 两支同构：**逐条读数都要带算式**。少了这一项，读者面
                #: 表格的「算式/事实」列会整列空白（`cbs-1` 实测），而空白列既不能核对
                #: 「跟哪一期比」，也不能核对「比的是哪两条事实」。
                "formula": f"{self.prior_fact_id} → {self.current_fact_id}",
                "change_text": self.change_text, "value_text": self.value_text,
                "threshold_text": _pct_text(MAJOR_CHANGE_THRESHOLD),
                "selected": self.selected}


# ---------------------------------------------------------------------------
# 期间比较的**唯一**命名处
# ---------------------------------------------------------------------------

def _ymd(period: str) -> tuple[int, int] | None:
    """权威期间记号的 `(年, 月)`。解析交给 `financial_v2.period_basis`（期间记号的唯一实现）。"""
    from financial_v2 import period_basis as FPB

    return FPB.year_month_of(str(period))


def _comparison_label(*, current_period: str, prior_period: str,
                      prior_label: str) -> tuple[str, bool]:
    """`(读者可见的比较说法, 是否真为年度同比)`。

    **这是本模块唯一一处决定「跟哪一期比」措辞的地方。** 它存在的理由是一个具体的错法：
    把「2026 年一季度末」与「2025 年末」的差额写成**同比**。季度末 → 上年末不是同比——
    它比的是**上年末**，跨了一个季度而不是一年。

    规则只由两期的**年/月**决定，因此不存在按公司、年份或页码特判的余地：

    * 当期是 3/6/9 月末、前期是**上一年 12 月**末 ⇒ `较上年末（<前期表达>）`，**不是**同比；
    * 两期都是 12 月末、年份相邻 ⇒ `同比（<前期表达>）`，这才是年度同比；
    * 其余 ⇒ `较<前期表达>`，并如实标为**不是**年度同比。

    第三种情形**不**给出习惯说法：本模块不肯为一个自己没判过的比较对安一个同比/环比的名号——
    那正是「期间口径」最容易出错的地方。
    """
    current, prior = _ymd(current_period), _ymd(prior_period)
    if current is None or prior is None:
        return (f"较{prior_label}", False)
    cy, cm = current
    py, pm = prior
    if cm in (3, 6, 9) and pm == 12 and py == cy - 1:
        return (f"较上年末（{prior_label}）", False)
    if cm == 12 and pm == 12 and py == cy - 1:
        return (f"同比（{prior_label}）", True)
    return (f"较{prior_label}", False)


def _direction(change: Decimal) -> str:
    return "上升" if change > 0 else ("下降" if change < 0 else "持平")


# ---------------------------------------------------------------------------
# 逐栏读数
# ---------------------------------------------------------------------------

def _read_totals(index: _FactIndex) -> tuple[tuple[BalanceValue, ...], tuple[dict, ...],
                                             tuple[dict, ...]]:
    """总量轴：逐期取总资产／总负债／所有者权益；再逐期取权威自己的资产负债率事实。

    返回 `(逐条读数, 总量轴缺口, 资产负债率逐期读数)`——最后一项里同时含已取到与留缺口的期间，
    因为「某期的资产负债率取不到」这件事本身是读者要看见的读数。
    """
    values: list[BalanceValue] = []
    gaps: list[dict] = []
    for code in _TOTAL_CODES:
        got = present = 0
        for period in index.periods:
            fact = index.fact(code, period)
            if fact is not None:
                present += 1
            reading = _value_of(fact) if fact is not None else None
            if reading is None:
                continue
            values.append(reading)
            got += 1
        if got == 0:
            gaps.append({"code": code, "aspect": "balance_structure",
                         "reason": ("no_registered_fact_for_item" if present == 0
                                    else "fact_not_displayable"),
                         "detail": (f"本节权威在本报告的 {len(index.periods)} 个期间上都没有 "
                                    f"{code} 的合格科目事实" if present == 0 else
                                    f"本节权威有 {present} 条 {code} 事实，但没有一条带有可用的"
                                    "权威值（`value_text` 为空）")})
    ratio_rows: list[dict] = []
    for period in index.periods:
        #: 指标命名空间，**不是**科目命名空间：`SOLV_DEBT_RATIO` 按路由落在本节的偿债指标栏，
        #: 若按本 topic 过筛就会在这里整批读空，正文随即写下「权威从未产出过」的假陈述。
        fact = index.metric(_DEBT_RATIO_FORMULA, period)
        value = _raw_value(fact) if fact is not None else None
        display = str(getattr(fact, "display", "") or "").strip() if fact is not None else ""
        if fact is None or value is None or not display:
            reason = ("no_registered_fact_for_item" if fact is None else "fact_not_displayable")
            ratio_rows.append({
                "period": period, "state": "gap", "reason": reason,
                "detail": (f"本节权威在 {period} 上没有 {_DEBT_RATIO_FORMULA} 的合格指标事实；"
                           "本栏不自行「负债 ÷ 资产」补一个权威没产出过的数" if fact is None else
                           f"本节权威在 {period} 的 {_DEBT_RATIO_FORMULA} 没有可用的权威值；"
                           "本栏不自行「负债 ÷ 资产」补一个权威没产出过的数")})
            continue
        ratio_rows.append({"period": period,
                           "period_label": str(getattr(fact, "period_label", "") or ""),
                           "state": "determined", "display": display,
                           "fact_id": str(getattr(fact, "fact_id", "") or ""),
                           "citation_identity": _citation_identity(fact),
                           "formula_id": _DEBT_RATIO_FORMULA})
    return tuple(values), tuple(gaps), tuple(ratio_rows)


def _value_of(fact: Any) -> BalanceValue | None:
    """一条合格事实 → 一条读者可见读数；不可陈列（缺渲染串或非 Decimal 值）返回 `None`。"""
    value = _raw_value(fact)
    display = _money_text(fact)
    if value is None or not display:
        return None
    return BalanceValue(
        label=str(getattr(fact, "label", "") or ""),
        code=str(getattr(fact, "code", "") or ""),
        fact_id=str(getattr(fact, "fact_id", "") or ""),
        period=str(getattr(fact, "period", "") or ""),
        period_label=str(getattr(fact, "period_label", "") or ""),
        period_basis=str(getattr(fact, "period_basis", "") or ""),
        unit=str(getattr(fact, "unit", "") or ""),
        display=display, value_text=format(value, "f"),
        citation_identity=_citation_identity(fact))


def _read_structure(index: _FactIndex) -> tuple[tuple[BalanceRatio, ...], tuple[dict, ...]]:
    """结构轴：逐期算流动/非流动占各自总量轴的比例（两个合格事实之间的确定性换算）。"""
    ratios: list[BalanceRatio] = []
    gaps: list[dict] = []
    for code, denominator in _STRUCTURE_CODES:
        for period in index.periods:
            part, whole = index.fact(code, period), index.fact(denominator, period)
            if part is None or whole is None:
                missing = code if part is None else denominator
                gaps.append({"code": code, "period": period, "aspect": "balance_structure",
                             "reason": "no_registered_fact_for_item",
                             "detail": f"{period} 缺 {missing} 的合格事实：占比的分子或分母不在"
                                       "权威里，不得只写一半"})
                continue
            part_value, whole_value = _raw_value(part), _raw_value(whole)
            if part_value is None or whole_value is None:
                gaps.append({"code": code, "period": period, "aspect": "balance_structure",
                             "reason": "fact_not_displayable",
                             "detail": f"{period} 的 {code} 或 {denominator} 有事实但没有可用的"
                                       "权威值"})
                continue
            if whole_value == 0:
                gaps.append({"code": code, "period": period, "aspect": "balance_structure",
                             "reason": "comparison_base_zero",
                             "detail": f"{period} 的 {denominator} 为 0：占比在数学上不成立"})
                continue
            ratio = part_value / whole_value
            ratios.append(BalanceRatio(
                label=str(getattr(part, "label", "") or ""), period=period,
                period_label=str(getattr(part, "period_label", "") or ""),
                numerator_fact_id=str(part.fact_id), denominator_fact_id=str(whole.fact_id),
                numerator_code=code, denominator_code=denominator,
                ratio_text=_pct_text(ratio), value_text=format(ratio, "f")))
    return tuple(ratios), tuple(gaps)


def _read_shares(index: _FactIndex) -> tuple[tuple[BalanceShare, ...], tuple[dict, ...]]:
    """15% 强制分析：对**每一个可被筛的科目 × 每一个期间**逐条试算占该侧总量轴的比例。

    逐条（含未命中）都在册：「未列出」不等于「未试算」。这条读数是本栏对 `complete_set_rule`
    的回答方式——**筛选逐条施加、逐条记录**，而不是挑几条讲。

    合计行（`_SUMMARY_CODES`）不参与试算，逐条落 `no_authority_field_for_required_item`：
    它们的分母是它们自己，任何比例都是 100%，那不是「占资产或负债 15% 以上」这句话的意思。
    """
    shares: list[BalanceShare] = []
    gaps: list[dict] = []
    for code in sorted(balance_sheet_items()):
        for period in index.periods:
            if code in _SUMMARY_CODES:
                gaps.append({"code": code, "period": period,
                             "aspect": "fifteen_pct_forced_analysis",
                             "reason": "no_authority_field_for_required_item",
                             "detail": f"{code} 是总量轴或合计行本身，不是被筛的科目："
                                       "本栏不替它挑一个分母"})
                continue
            fact = index.fact(code, period)
            if fact is None:
                gaps.append({"code": code, "period": period,
                             "aspect": "fifteen_pct_forced_analysis",
                             "reason": "no_registered_fact_for_item",
                             "detail": f"本节权威在 {period} 上没有 {code} 的合格科目事实"})
                continue
            side = _BALANCE_SHEET_SIDE[code]
            denominator = _SHARE_DENOMINATOR_BY_SIDE[side]
            whole = index.fact(denominator, period)
            part_value = _raw_value(fact)
            whole_value = _raw_value(whole) if whole is not None else None
            if part_value is None or whole_value is None:
                gaps.append({"code": code, "period": period,
                             "aspect": "fifteen_pct_forced_analysis",
                             "reason": "fact_not_displayable",
                             "detail": f"{period} 的 {code} 或 {denominator} 有事实但没有可用的"
                                       "权威值"})
                continue
            if whole_value == 0:
                gaps.append({"code": code, "period": period,
                             "aspect": "fifteen_pct_forced_analysis",
                             "reason": "comparison_base_zero",
                             "detail": f"{period} 的 {denominator} 为 0：占比不成立"})
                continue
            share = part_value / whole_value
            shares.append(BalanceShare(
                label=str(getattr(fact, "label", "") or ""), code=code,
                fact_id=str(fact.fact_id), period=period,
                period_label=str(getattr(fact, "period_label", "") or ""),
                denominator_code=denominator, denominator_fact_id=str(whole.fact_id),
                share_text=_pct_text(share), value_text=format(share, "f"),
                selected=share >= FIFTEEN_PCT_THRESHOLD))
    return tuple(shares), tuple(gaps)


def _read_changes(index: _FactIndex) -> tuple[tuple[BalanceChange, ...], tuple[dict, ...]]:
    """重大科目变化：对**每一个已登记科目 × 每一对相邻期间**逐条试算变动幅度。"""
    changes: list[BalanceChange] = []
    gaps: list[dict] = []
    for code in sorted(balance_sheet_items()):
        periods = index.periods_with(code)
        if not periods:
            continue
        for prior_period, current_period in zip(periods, periods[1:]):
            prior, current = index.fact(code, prior_period), index.fact(code, current_period)
            prior_value, current_value = _raw_value(prior), _raw_value(current)
            if prior_value is None or current_value is None:
                gaps.append({"code": code, "period": current_period,
                             "aspect": "major_account_changes",
                             "reason": "fact_not_displayable",
                             "detail": f"{code} 在 {prior_period} 或 {current_period} 上"
                                       "有事实但没有可用的权威值：变动幅度不得只算一半"})
                continue
            if prior_value == 0:
                gaps.append({"code": code, "period": current_period,
                             "aspect": "major_account_changes",
                             "reason": "comparison_base_zero",
                             "detail": f"{code} 在 {prior_period} 的值为 0：变动幅度不成立"})
                continue
            change = (current_value - prior_value) / abs(prior_value)
            prior_label = str(getattr(prior, "period_label", "") or prior_period)
            comparison, is_yoy = _comparison_label(
                current_period=current_period, prior_period=prior_period, prior_label=prior_label)
            changes.append(BalanceChange(
                label=str(getattr(current, "label", "") or ""), code=code,
                current_fact_id=str(current.fact_id), prior_fact_id=str(prior.fact_id),
                current_period=current_period, prior_period=prior_period,
                comparison_label=comparison, is_year_on_year=is_yoy,
                direction=_direction(change), change_text=_pct_text(change),
                value_text=format(change, "f"),
                selected=abs(change) >= MAJOR_CHANGE_THRESHOLD))
    return tuple(changes), tuple(gaps)


# ---------------------------------------------------------------------------
# 一栏一个条目
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BalanceStructureAspectItem:
    """一条 Contract 要求的确定性答案（或一条 typed 缺口）。

    `readings` 是该栏的**全部**读数（逐条试算，含未命中筛选的）；`statement` 是读者面那一段话。
    两者都进身份体：只留 `statement` 会让「这一栏到底试算了几条」无法回读。
    """

    aspect_id: str
    requirement_text: str
    label: str
    state: str
    statement: str
    readings: tuple[Any, ...] = ()
    gaps: tuple[dict, ...] = ()
    reason: str = ""
    authority_field: str = ""

    def __post_init__(self) -> None:
        if self.state not in BALANCE_STRUCTURE_ITEM_STATES:
            raise CitedBalanceStructureError(
                f"未登记的条目状态 {self.state!r}（封闭取值 "
                f"{list(BALANCE_STRUCTURE_ITEM_STATES)}）")
        if self.state == "determined":
            if not self.statement:
                raise CitedBalanceStructureError(
                    f"{self.aspect_id}: state='determined' 必须有一段实际呈现，不得为空")
            if self.reason:
                raise CitedBalanceStructureError(
                    f"{self.aspect_id}: state='determined' 不得带缺口原因"
                    "（原因码只属于缺口）")
        else:
            if self.reason not in BALANCE_STRUCTURE_GAP_REASONS:
                raise CitedBalanceStructureError(
                    f"{self.aspect_id}: 未登记的缺口原因 {self.reason!r}（封闭取值 "
                    f"{list(BALANCE_STRUCTURE_GAP_REASONS)}）")
            if self.statement:
                raise CitedBalanceStructureError(
                    f"{self.aspect_id}: state='gap' 不得带呈现段——缺口不许用一段话把"
                    "「没取到」写成「已经写了」")
        for gap in self.gaps:
            reason = str(gap.get("reason") or "")
            if reason not in BALANCE_STRUCTURE_GAP_REASONS:
                raise CitedBalanceStructureError(
                    f"{self.aspect_id}: 逐条缺口里出现未登记的原因码 {reason!r}"
                    f"（封闭取值 {list(BALANCE_STRUCTURE_GAP_REASONS)}）")

    def to_dict(self) -> dict:
        return {"aspect_id": self.aspect_id, "requirement_text": self.requirement_text,
                "label": self.label, "state": self.state, "statement": self.statement,
                "readings": [r.to_dict() for r in self.readings],
                "gaps": [dict(g) for g in self.gaps], "reason": self.reason,
                "authority_field": self.authority_field}


@dataclass(frozen=True)
class BalanceStructureNote:
    """一条**不由 Contract 栏目承载**的注记：说清本栏刻意没做什么、以及为什么。

    `reason` 走 `BALANCE_STRUCTURE_GAP_REASONS` 的同一个封闭集——「这一项要单独裁决」与
    「这一项权威里没有」是两种不同的「没做」，不得共用一个措辞。
    """

    note_id: str
    label: str
    state: str
    reason: str
    detail: str

    def __post_init__(self) -> None:
        if self.state not in BALANCE_STRUCTURE_ITEM_STATES:
            raise CitedBalanceStructureError(
                f"注记 {self.note_id!r} 的状态 {self.state!r} 不在封闭取值 "
                f"{list(BALANCE_STRUCTURE_ITEM_STATES)} 内")
        if self.state == "gap" and self.reason not in BALANCE_STRUCTURE_GAP_REASONS:
            raise CitedBalanceStructureError(
                f"注记 {self.note_id!r} 的原因 {self.reason!r} 不在封闭取值 "
                f"{list(BALANCE_STRUCTURE_GAP_REASONS)} 内")

    def to_dict(self) -> dict:
        return {"note_id": self.note_id, "label": self.label, "state": self.state,
                "reason": self.reason, "detail": self.detail}


# ---------------------------------------------------------------------------
# 读者面那三段话（纯函数，输入全是上面那些逐条读数）
# ---------------------------------------------------------------------------

def _totals_statement(*, index: _FactIndex, values: tuple[BalanceValue, ...],
                      ratio_rows: tuple[dict, ...]) -> str:
    by_code: dict[str, dict[str, BalanceValue]] = {}
    for value in values:
        by_code.setdefault(value.code, {})[value.period] = value
    parts: list[str] = []
    for period in index.periods:
        pieces: list[str] = []
        assets = by_code.get("TOTAL_ASSETS", {}).get(period)
        debts = by_code.get("TOTAL_LIABILITIES", {}).get(period)
        own = by_code.get("TOTAL_EQUITY", {}).get(period)
        if assets is not None:
            pieces.append(f"资产总额{assets.display}")
        if debts is not None:
            pieces.append(f"负债总额{debts.display}")
        if own is not None:
            pieces.append(f"所有者权益合计{own.display}")
        if pieces:
            parts.append(f"{index.period_label(period)}{'，'.join(pieces)}")
    lines: list[str] = []
    if parts:
        lines.append("报告期各期末" + "；".join(parts) + "。")
    determined = [r for r in ratio_rows if r.get("state") == "determined"]
    missing = [r for r in ratio_rows if r.get("state") != "determined"]
    if determined:
        #: 期间表达与数值之间**必须有分隔**：`2025年末65%` 这种连写会被读成一个数。
        rendered = "、".join(f"{r.get('period_label') or r['period']}为{r['display']}"
                            for r in determined)
        lines.append("资产负债率（取值来自本节权威自己的指标事实"
                     f"`{_DEBT_RATIO_FORMULA}`，本栏不自行相除）：{rendered}。")
    if missing:
        lines.append("其中 " + "、".join(str(r["period"]) for r in missing)
                     + " 没有权威产出的资产负债率事实：这一栏留 typed 缺口，"
                       "不以「负债总额 ÷ 资产总额」补一个权威从未产出过的数。")
    return "".join(lines)


def _structure_statement(*, index: _FactIndex, ratios: tuple[BalanceRatio, ...]) -> str:
    by_period: dict[str, list[BalanceRatio]] = {}
    for ratio in ratios:
        by_period.setdefault(ratio.period, []).append(ratio)
    sentences: list[str] = []
    for period in index.periods:
        entries = by_period.get(period)
        if not entries:
            continue
        pieces: list[str] = []
        for denominator, total_cn in (("TOTAL_ASSETS", "资产总额"),
                                      ("TOTAL_LIABILITIES", "负债总额")):
            group = [r for r in entries if r.denominator_code == denominator]
            if not group:
                continue
            pieces.append(f"占{total_cn}的比重分别为"
                          + "、".join(f"{r.label}{r.ratio_text}" for r in group))
        if pieces:
            sentences.append(f"{index.period_label(period)}{'；'.join(pieces)}。")
    return "".join(sentences)


def _shares_statement(*, index: _FactIndex, shares: tuple[BalanceShare, ...],
                      gaps: tuple[dict, ...]) -> str:
    #: **逐期**陈述，不只看最后一期：Contract 的三栏 `time_scope` 都是
    #: `THREE_YEARS_PLUS_LATEST`，只讲本期等于把三个年末的命中整批咽掉
    #: （`cbsp-1` 实测：15 条命中有 9 条落在 2025 年末，正文里一条也没有）。
    if not shares:
        return ""
    by_period: dict[str, list[BalanceShare]] = {}
    for share in shares:
        by_period.setdefault(share.period, []).append(share)
    sentences: list[str] = []
    for period in index.periods:
        eligible = by_period.get(period)
        if not eligible:
            continue
        hit = [s for s in eligible if s.selected]
        if hit:
            clauses: list[str] = []
            for denominator, total_cn in (("TOTAL_ASSETS", "资产总额"),
                                          ("TOTAL_LIABILITIES", "负债总额")):
                group = [s for s in hit if s.denominator_code == denominator]
                if group:
                    clauses.append(f"占{total_cn} 15% 以上的科目有 "
                                   + "、".join(f"{s.label}{s.share_text}" for s in group))
            sentences.append(f"{index.period_label(period)}，" + "；".join(clauses) + "。")
        else:
            sentences.append(f"{index.period_label(period)}没有任何已登记科目"
                             "占该侧总量轴 15% 以上。")
    return ("".join(sentences)
            + "该筛选对**每一个可被筛的科目 × 每一个期间**逐条试算"
              f"（全部期间合计 {len(shares)} 条，未命中的也逐条在册）："
              "「未列出」不等于「未试算」。"
            + "合计行（资产总额／负债总额／所有者权益合计／负债和所有者权益总计）"
              "**不参与**试算：它们的分母是它们自己，任何比例都是 100%，"
              "那不是 Contract「占资产或负债 15% 以上」的意思。"
            + (f"另有 {len(gaps)} 条无法试算（逐条带封闭原因码）。" if gaps else ""))


def _changes_statement(*, index: _FactIndex, changes: tuple[BalanceChange, ...]) -> str:
    #: 同上，**逐期**陈述。比较说法写进每一句自身：各期的比较对**不同**
    #: （2024/2025 两个年末是同比，2026 年一季度末是「较上年末」），
    #: 用一个统称盖住全部期间正是「期间口径」最容易被读错的地方。
    if not changes:
        return ""
    by_period: dict[str, list[BalanceChange]] = {}
    for change in changes:
        by_period.setdefault(change.current_period, []).append(change)
    sentences: list[str] = []
    for period in index.periods:
        recent = by_period.get(period)
        if not recent:
            continue
        head_label = recent[0].comparison_label
        selected_recent = [c for c in recent if c.selected]
        if selected_recent:
            rendered = "、".join(f"{c.label}{c.change_text}（{c.direction}）"
                                for c in selected_recent)
            sentences.append(f"{index.period_label(period)}{head_label}，"
                             f"变动达到 20% 的重点科目为 {rendered}。")
        else:
            sentences.append(f"{index.period_label(period)}{head_label}，"
                             "没有科目的变动达到 20%。")
    selected_all = [c for c in changes if c.selected]
    return ("".join(sentences)
            + "20% 是本批的**展示筛选**（不是冻结 Contract 的判据），用它挑选要展开讲的科目；"
              f"相邻期间逐条试算共 {len(changes)} 条（命中 {len(selected_all)} 条），"
              "未命中的同样在册。"
              "跟哪一期比由两期的年月决定：季度末对上年末的差额标为「较上年末」"
              "（**不是**同比），只有相邻两个年末之间才写「同比」。"
            + "至于**变化原因**：冻结 Contract 的 `required_fields` 要求「变化原因」，"
              "而本节权威没有承载原因的字段——该项留 typed 缺口，不以模型推断补足。")


#: 「哪一条 Contract 要求由哪一族读数回答」——本模块的**唯一**路由表。
#:
#: 三栏各自读**不同**的一族：`balance_structure` 读总量轴与结构比、`major_account_changes` 读
#: 逐期变动、`fifteen_pct_forced_analysis` 读结构占比筛选。按 aspect 名字猜读法的写法在这里会
#: 立刻出错——`fifteen_pct_forced_analysis` 的名字里没有「这是对同一组科目施加的筛选」这层含义，
#: 而它恰恰**不是**一组独立指标（见模块 docstring 的阈值出处一节）。
BALANCE_STRUCTURE_ASPECT_ROUTING: tuple[tuple[str, str], ...] = (
    ("fin_balance_structure.balance_structure", "资产负债结构"),
    ("fin_balance_structure.major_account_changes", "重大科目变化"),
    ("fin_balance_structure.fifteen_pct_forced_analysis", "15%以上科目强制分析"),
)


def _contract_threshold_rule(aspects: tuple[Any, ...]) -> str:
    """Contract 判据的**逐字**见证：必须有一条 `complete_set_rule` 里出现 `15%`。

    这不是防呆，是防**漂移**：15% 这个数写在本模块的常量里，而它的出处是冻结 Contract 的
    `complete_set_rule`。哪天 Contract 改了数而本模块没改，这里当场拒绝，而不是让读者面上出现
    一个与 Contract 不符的阈值。命中时把 Contract 的原话**原样**带进产物。
    """
    rules = [str(getattr(a, "complete_set_rule", "") or "") for a in aspects]
    hits = [r for r in rules if _CONTRACT_THRESHOLD_WORD in r]
    if not hits:
        raise CitedBalanceStructureError(
            f"冻结 Contract 本 topic 的 `complete_set_rule` 里没有 {_CONTRACT_THRESHOLD_WORD!r}"
            f"（实得 {rules!r}）：本模块的 15% 阈值与 Contract 判据不同源了，不得继续呈现")
    return hits[0]


def _fact_scope(authority: Any) -> str:
    """本节权威自己的口径声明：范围 / 币种 / 期间。**取字段**，不由本模块推断。"""
    artifact = getattr(authority, "artifact", None)
    snapshot = getattr(artifact, "snapshot", None)
    scope = str(getattr(snapshot, "scope", "") or "").strip()
    currency = str(getattr(snapshot, "currency", "") or "").strip()
    periods = tuple(str(p) for p in (getattr(artifact, "periods", ()) or ()))
    return (f"本次快照口径 `{scope or '（未声明）'}`，币种 `{currency or '（未声明）'}`，"
            f"期间轴 {list(periods)}；科目金额以元登记、按权威自己的渲染串展示"
            "（≥1 亿 → 亿元）。")


def _notes() -> tuple[BalanceStructureNote, ...]:
    """本栏刻意**没有**做的两件事，逐条成文（各自不同的封闭原因码）。

    两条注记都不是「待办清单」，而是**读者面的读数**：它们说明这一栏的边界在哪、
    以及边界之外的那一项为什么不能由本栏顺手补上。
    """
    return (
        BalanceStructureNote(
            note_id="accounts_receivable_case",
            label="应收账款案例（指令 II.3）",
            state="gap", reason="requires_separate_adjudication",
            detail=("本栏**不**产出应收账款明细表，也**不**给出任何应收账款的账面余额、坏账准备"
                    "或账面价值。理由不是「没有材料」，而是这一项要先把附注抽取的资格路径"
                    "（`EvidenceNoteFact` 接口 + 精确 PDF 单元格定位）审计清楚，而那触及冻结"
                    "权威口径，属**单独子项裁决**：在裁决之前产出的每一个数都会是一条绕过资格"
                    "决定的数字。因此本栏只声明边界。另外，本次快照里**没有** "
                    "`ACCOUNTS_RECEIVABLE` 与 `FIXED_ASSETS` 的条目，本栏据此对这两个 code 逐条落 "
                    "`no_registered_fact_for_item`——**不**拿任何合并口径的科目顶替独立科目。")),
        BalanceStructureNote(
            note_id="existing_solvency_table",
            label="已有偿债指标表",
            state="determined", reason="",
            detail=("本节最后那张「指标 × 期间」偿债表由 `cited_financial_table` 按 `fin_solvency` "
                    "那一栏确定性成表，**不在本栏复制**；本栏只声明它在哪里、以及两栏不是同一"
                    "件事：那一栏回答偿债能力的指标值，本栏回答资产负债的结构、重大科目变动与 "
                    "15% 强筛。两栏的栏目数各自独立，不得互相顶替。")),
    )


# ---------------------------------------------------------------------------
# 呈现
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedBalanceStructurePresentation:
    """`fin_balance_structure` 一栏的完整确定性呈现：版本化路由表 + 逐条答案与缺口 + 边界注记。"""

    section_id: str
    topic_id: str
    schema_version: str
    policy_version: str
    routing_version: str
    producer_kind: str
    contract_threshold_rule: str
    contract_topic_aspects: tuple[str, ...]
    items: tuple[BalanceStructureAspectItem, ...]
    notes: tuple[BalanceStructureNote, ...] = ()
    fact_scope: str = ""
    model_calls_issued: int = 0

    def __post_init__(self) -> None:
        if self.producer_kind != BALANCE_STRUCTURE_PRODUCER_KIND:
            raise CitedBalanceStructureError(
                f"产出者身份只能是 {BALANCE_STRUCTURE_PRODUCER_KIND!r}，实得 {self.producer_kind!r}")
        if self.model_calls_issued != 0:
            raise CitedBalanceStructureError(
                f"本栏的确定性呈现不得发起模型调用，实得 {self.model_calls_issued} 次")
        seen = [item.aspect_id for item in self.items]
        if len(set(seen)) != len(seen):
            raise CitedBalanceStructureError(f"同一栏出现重复条目：{seen}")
        missing = [a for a in self.contract_topic_aspects if a not in set(seen)]
        if missing:
            raise CitedBalanceStructureError(
                f"Contract 要求 {missing} 在呈现里没有条目：未取到的项目也必须**逐条**出现"
                "（留白会让「没问」看起来像「问过了」）")

    @property
    def determined(self) -> tuple[BalanceStructureAspectItem, ...]:
        return tuple(i for i in self.items if i.state == "determined")

    @property
    def gaps(self) -> tuple[BalanceStructureAspectItem, ...]:
        return tuple(i for i in self.items if i.state == "gap")

    def all_readings(self) -> tuple[Any, ...]:
        return tuple(r for item in self.items for r in item.readings)

    def identity_body(self) -> dict:
        #: `model_calls_issued` 进身份体：它恒为 0，而「这一栏有没有花过模型额度」是这条产物
        #: 最容易被误读的一个读数，因此让它在身份体里恒等于 0，而不是靠读者去别处推断。
        return {"section_id": self.section_id, "topic_id": self.topic_id,
                "schema_version": self.schema_version,
                "policy_version": self.policy_version,
                "routing_version": self.routing_version,
                "producer_kind": self.producer_kind,
                "contract_threshold_rule": self.contract_threshold_rule,
                "contract_topic_aspects": list(self.contract_topic_aspects),
                "fact_scope": self.fact_scope,
                "items": [i.to_dict() for i in self.items],
                "notes": [n.to_dict() for n in self.notes],
                "model_calls_issued": self.model_calls_issued}

    def fingerprint(self) -> str:
        import json
        body = json.dumps(self.identity_body(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {**self.identity_body(), "fingerprint": self.fingerprint()}


def build_cited_balance_structure(*, section_id: str, topic_id: str, requirement: Any,
                                  authority: Any) -> CitedBalanceStructurePresentation:
    """按 Contract 的 aspect 次序逐条读本节财务权威，产出确定性呈现。

    Contract 是**驱动方**：遍历的是 `requirement.aspects` 的次序，而不是路由表的次序。
    路由表里没有登记的 aspect 是一条构造期拒绝——「Contract 要、而本模块不知道怎么读」这件事
    必须在这里停住，不能悄悄少呈现一条。
    """
    _assert_side_table_covers_balance_sheet()
    aspects = tuple(getattr(requirement, "aspects", ()) or ())
    if not aspects:
        raise CitedBalanceStructureError(
            f"topic {topic_id!r} 在冻结 Contract 投影里没有任何 aspect：没有要求就没有呈现")
    labels: Mapping[str, str] = {a: label for a, label in BALANCE_STRUCTURE_ASPECT_ROUTING}
    threshold_rule = _contract_threshold_rule(aspects)

    index = _FactIndex.build(authority=authority, topic_id=topic_id)
    values, total_gaps, ratio_rows = _read_totals(index)
    ratios, structure_gaps = _read_structure(index)
    shares, share_gaps = _read_shares(index)
    changes, change_gaps = _read_changes(index)

    readings_by_aspect: dict[str, tuple[Any, ...]] = {
        "fin_balance_structure.balance_structure": values + ratios,
        "fin_balance_structure.major_account_changes": changes,
        "fin_balance_structure.fifteen_pct_forced_analysis": shares,
    }
    gaps_by_aspect: dict[str, tuple[dict, ...]] = {
        "fin_balance_structure.balance_structure": total_gaps + structure_gaps,
        # 变化原因是本 aspect 的 `required_fields` 之一而权威没有承载字段：这条缺口属于**整栏**，
        # 不属于某一个科目，因此不逐科目重复，只记一条并说明它乘的是哪一栏的要求。
        "fin_balance_structure.major_account_changes": change_gaps + ({
            "code": "", "period": "", "aspect": "major_account_changes",
            "reason": "no_authority_field_for_required_item",
            "detail": "冻结 Contract 的 `required_fields` 含「变化原因」，而本节权威只承载金额、"
                      "期间、单位与口径，没有承载原因的字段：该要求逐条留 typed 缺口，"
                      "不以模型推断或行业常识补足"},),
        "fin_balance_structure.fifteen_pct_forced_analysis": share_gaps,
    }
    statements: dict[str, str] = {
        "fin_balance_structure.balance_structure":
            _totals_statement(index=index, values=values, ratio_rows=ratio_rows)
            + _structure_statement(index=index, ratios=ratios),
        "fin_balance_structure.major_account_changes":
            _changes_statement(index=index, changes=changes),
        "fin_balance_structure.fifteen_pct_forced_analysis":
            _shares_statement(index=index, shares=shares, gaps=share_gaps),
    }

    items: list[BalanceStructureAspectItem] = []
    aspect_ids: list[str] = []
    for aspect in aspects:
        aspect_id = str(getattr(aspect, "aspect_id", "") or "")
        requirement_text = str(getattr(aspect, "requirement_text", "") or "")
        if not aspect_id:
            raise CitedBalanceStructureError("冻结 Contract 投影里有一条第 aspect_id 为空的栏目")
        if aspect_id not in labels:
            raise CitedBalanceStructureError(
                f"Contract 栏目 {aspect_id!r} 在本模块的路由表里没有登记读法（版本 "
                f"{BALANCE_STRUCTURE_ROUTING_VERSION}）：宁可在构造期停住，也不得少呈现一条已"
                "要求的内容")
        aspect_ids.append(aspect_id)
        statement = statements[aspect_id]
        items.append(BalanceStructureAspectItem(
            aspect_id=aspect_id, requirement_text=requirement_text, label=labels[aspect_id],
            state="determined" if statement else "gap", statement=statement,
            readings=readings_by_aspect[aspect_id], gaps=gaps_by_aspect[aspect_id],
            reason="" if statement else "no_registered_fact_for_item",
            authority_field="authority.artifact.facts（`FinancialFact`）"))

    return CitedBalanceStructurePresentation(
        section_id=section_id, topic_id=topic_id,
        schema_version=CITED_BALANCE_STRUCTURE_SCHEMA_VERSION,
        policy_version=CITED_BALANCE_STRUCTURE_POLICY_VERSION,
        routing_version=BALANCE_STRUCTURE_ROUTING_VERSION,
        producer_kind=BALANCE_STRUCTURE_PRODUCER_KIND,
        contract_threshold_rule=threshold_rule,
        contract_topic_aspects=tuple(aspect_ids), items=tuple(items),
        notes=_notes(), fact_scope=_fact_scope(authority))


# ---------------------------------------------------------------------------
# 读者面
# ---------------------------------------------------------------------------

#: 阅读次序（指令 II.2 规定的正文次序）与 Contract 栏目次序是**两个不同的轴**：
#: 前者是读者理解轴（总量 → 结构 → 15% → 20%），后者是要求枚举轴（逐栏覆盖按它核）。
#: 把两者并成一句会让「覆盖核过了」看起来像「读起来成篇了」，反过来也一样。
BALANCE_STRUCTURE_READING_ORDER: tuple[tuple[str, str], ...] = (
    ("fin_balance_structure.balance_structure", "资产负债总量与结构"),
    ("fin_balance_structure.fifteen_pct_forced_analysis", "占资产或负债 15% 以上的科目"),
    ("fin_balance_structure.major_account_changes", "变动达到 20% 的重点科目"),
)


def render_cited_balance_structure_markdown(
        presentation: CitedBalanceStructurePresentation, *,
        requirement_text_by_aspect: Mapping[str, str] | None = None) -> str:
    """一栏的读者面：正文段（按阅读次序）+ 逐栏覆盖（按 Contract 次序）+ 逐条读数与缺口。"""
    by_aspect = {item.aspect_id: item for item in presentation.items}
    lines: list[str] = []
    lines.append(f"## {presentation.topic_id}（确定性呈现，"
                 f"`{presentation.producer_kind}`，模型调用 {presentation.model_calls_issued} 次）")
    lines.append("")
    lines.append("> 本栏**不经过模型**：逐条取值来自本节财务权威自己的合格事实，"
                 "逐条标出取的是哪一条事实、哪个期间、哪条引用。"
                 "缺口的措辞与取值分列，「没取到」不得被读成「已核对通过」。")
    lines.append("")
    lines.append(f"- 路由表版本：`{presentation.routing_version}`"
                 f"　产物政策：`{presentation.policy_version}`　"
                 f"schema：`{presentation.schema_version}`")
    lines.append(f"- 口径：{presentation.fact_scope}")
    lines.append(f"- 15% 判据的出处（冻结 Contract `complete_set_rule` 原话）："
                 f"「{presentation.contract_threshold_rule}」")
    lines.append(f"- 已呈现 **{len(presentation.determined)}** 栏，"
                 f"typed 缺口 **{len(presentation.gaps)}** 栏，"
                 f"共 **{len(presentation.items)}** 栏"
                 "（= 冻结 Contract 本 topic 的 aspect 数）；"
                 f"逐条读数 **{len(presentation.all_readings())}** 条（含未命中筛选的）")
    lines.append("")
    lines.append("### 正文（按阅读次序；次序与 Contract 的栏目次序是两个不同的轴）")
    lines.append("")
    for aspect_id, title in BALANCE_STRUCTURE_READING_ORDER:
        item = by_aspect.get(aspect_id)
        if item is None:
            continue
        lines.append(f"**{title}**")
        lines.append("")
        lines.append(item.statement or
                     f"**缺口**（`{item.reason}`）：本栏目在本次登记来源里没有可取的项")
        lines.append("")
    lines.append("### 逐栏覆盖（按冻结 Contract 的 aspect 次序）")
    lines.append("")
    lines.append("| # | Contract 要求 | 状态 | 逐条读数 | 逐条缺口 |")
    lines.append("|---|---|---|---|---|")
    for index, item in enumerate(presentation.items, start=1):
        state = "已呈现" if item.state == "determined" else f"**缺口**（`{item.reason}`）"
        text = (requirement_text_by_aspect or {}).get(item.aspect_id) or item.requirement_text
        lines.append(f"| {index} | {text} | {state} | {len(item.readings)} 条 "
                     f"| {len(item.gaps)} 条 |")
    lines.append("")
    for item in presentation.items:
        if not item.readings:
            continue
        lines.append(f"#### `{item.aspect_id}` 逐条读数（{len(item.readings)} 条，"
                     "含未命中筛选的）")
        lines.append("")
        lines.append("| 读数 | 科目/标签 | 期间 | 取值 | 算式 / 事实 |")
        lines.append("|---|---|---|---|---|")
        for reading in item.readings:
            detail = reading.to_dict()
            subject = detail.get("label") or detail.get("code", "")
            period = detail.get("period") or detail.get("current_period", "")
            shown = (detail.get("display") or detail.get("ratio_text")
                     or detail.get("share_text") or detail.get("change_text") or "")
            mark = ""
            if detail.get("kind") in ("share", "change"):
                mark = "（命中筛选）" if detail.get("selected") else "（未命中筛选）"
            lines.append(f"| `{detail.get('kind')}` | {subject} | `{period}` | {shown} {mark} "
                         f"| `{detail.get('formula') or detail.get('fact_id', '')}` |")
        lines.append("")
        if item.gaps:
            lines.append(f"#### `{item.aspect_id}` 逐条缺口（{len(item.gaps)} 条，"
                         "逐条带封闭原因码）")
            lines.append("")
            for gap in item.gaps:
                where = f"（{gap.get('code')} / {gap.get('period')}）" if gap.get("code") else ""
                lines.append(f"- `{gap.get('reason')}`{where}：{gap.get('detail', '')}")
            lines.append("")
    if presentation.notes:
        lines.append("### 本栏边界注记（**不是**待办清单，是读者面的读数）")
        lines.append("")
        for note in presentation.notes:
            state = "已呈现" if note.state == "determined" else f"缺口（`{note.reason}`）"
            lines.append(f"- **{note.label}**（{state}）：{note.detail}")
        lines.append("")
    return "\n".join(lines)

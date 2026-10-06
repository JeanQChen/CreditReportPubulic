"""相邻完整年度期末的**资产负债率变动**（Δpp）——派生展示事实（`FORMULA_REVIEW` §5.1，`ddf-1`）。

M930-3「先证明能成稿」定点批 §二只批准**一种**派生事实，本模块是它的唯一实现：

    Δpp = (本期 raw_value − 上期 raw_value) × 100

批准边界（逐条对应 `FORMULA_REVIEW` §5.1，实现不得超出）：

* **两期输入**必须是同一主体/合并范围/指标公式与口径下的**权威**原始 `MetricResult.raw_value`
  —— 只读权威自己的原值，**绝不**拿表格里 `69.34%` 这类**已舍入的展示值**相减（那样差异里
  混进了两次舍入误差，且读者无法区分「算出 3.30」与「两个四位小数相减得 3.30」）；
* **相邻的两个完整年度期末**：两个 `annual` 且 12 月的期间末日，年份相差 1。季度、中报、
  累计、单季、同比、环比、任意趋势判断一律**不在**批准范围内；
* 两期必须同主体、**同 scope（均 `consolidated`）**、同指标公式与口径版本。§5.1 的异常规则
  逐字登记：**缺前期 / 两期 scope 或指标公式版本不一致 / 任一期为 missing 或代理口径 →
  不生成**，并留下**类型化**原因（`DERIVED_FACT_REFUSAL_REASONS`，不是散文式借口）。
  「代理口径」在这里是**不生成**的理由，不是「生成后附一句代理说明」的理由——§5.1 把
  代理输入与 missing 并列在同一条异常规则里，本模块照此办理；
* 方向只由符号决定，负值逐字保留；不写「改善/恶化」这类判断词，**不参与评分**；
* 独立身份（`fact_id`/`kind`/`code`）+ 公式版本（`DELTA_FACT_FORMULA_VERSION`）+ **两期**
  输入与来源引用 + 可读回的血缘（`inputs`：每期的期间、原值、状态、输入事实 id、引用键）；
* 展示时才舍入：2 位小数 `ROUND_HALF_UP`，单位「个百分点」，`value` 保留未舍入值；
* 它不是「指标 × 期间」表格的一行（期间是复合记号，不是任何单期），见
  `sections.narrative_schema.build_metric_period_tables` 的同批登记。

本模块是**纯函数**：不读库、不联网、不做 I/O，也不 import `sections.*`（层序上
`financial_v2` 在 `sections` 上游）。展示渲染与 `FORMULA_REVIEW` §0 的舍入规则一致
（`ROUND_HALF_UP`、2 位小数、去尾部零），但它**不**按 `formula_id` 查冻结公式注册表 ——
Δpp 刻意**不**进注册表：注册表是「受审的评分指标」的封闭集合，而 §5.1 明确本项是
**非评分、展示型**派生事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence

from financial_v2 import formulas as fformulas
from financial_v2 import period_basis as fpb

#: 本项派生公式的版本（`FORMULA_REVIEW` §5.1 登记）。它随事实一起过界，读者据此判断
#: 「这个数值是按哪一版公式从两期原值算出来的」——**不**随注册表版本漂移。
DELTA_FACT_FORMULA_VERSION = "ddf-1"

#: 唯一的输入指标：`SOLV_DEBT_RATIO`（资产负债率，`end` 口径）。
DELTA_SOURCE_FORMULA_ID = "SOLV_DEBT_RATIO"
#: 派生事实自己的标识与身份（`kind` 刻意**不是** `calculation`：它不是公式注册表里的受审指标）。
DELTA_FACT_CODE = "SOLV_DEBT_RATIO_DELTA_PP"
DELTA_FACT_KIND = "derived"
DELTA_FACT_LABEL = "资产负债率变动"
DELTA_FACT_UNIT = "个百分点"
#: 未生成时登记在 excluded 里的状态（与 `CALCULATED_*` 同一封闭风格，明确「这一项没算出来」）。
DELTA_FACT_NOT_GENERATED_STATUS = "NOT_GENERATED"

_EXACT_STATUS = "CALCULATED_EXACT"
#: 输入**唯一**可用的状态（§5.1：「任一期为 missing 或代理口径 → 不生成」）。
#: `CALCULATED_PROXY` 刻意**不**在可用状态内：代理输入算出来的差额是另一个量，
#: 它既不是两期精确值之差，也无法与精确差额并列比较，因此按 §5.1 直接不生成。
_AVAILABLE_STATUSES = (_EXACT_STATUS,)

#: 不生成派生事实的**封闭**原因码（`FORMULA_REVIEW` §5.1：「缺前期、冲突或口径不可比就不生成
#: 该事实，并留下原因」）。每一条都指名一个具体的、可复算的拒绝条件，不是泛化理由。
DERIVED_FACT_REFUSAL_REASONS = (
    # 完整年度期末不足两个（缺前期；或快照里根本没有 12 月的 annual 期间末日）。
    "insufficient_complete_annual_periods",
    # 有两个以上完整年度期末，但最近的两个不相邻（年份差 ≠ 1）：中间缺了一年，不可比。
    "complete_annual_periods_not_adjacent",
    # 两期中至少有一期在指标结果表里找不到（未计算）。
    "input_metric_result_missing",
    # 两期中至少有一期的状态不是 CALCULATED_EXACT（§5.1：「任一期为 missing 或代理口径 →
    # 不生成」；代理口径与 missing 同列一条异常规则），或 raw_value 为空。
    "input_metric_unavailable",
    # 两期的 snapshot/公式/公式版本不一致（主体、合并范围或口径不可比）。
    "input_identity_mismatch",
    # 输入指标的期间口径无法判定（不在封闭取值内）：宁可拒，不给一个口径。
    "input_period_basis_unavailable",
)

_TWO_DP = Decimal("0.01")
_HUNDRED = Decimal(100)


class DerivedFactError(ValueError):
    """派生事实的构造错误（输入形状不合法；**不是**业务上的「不生成」）。"""


# ---------------------------------------------------------------------------
# 纯计算与展示（§5.1 的公式行）
# ---------------------------------------------------------------------------

def delta_pp(prior_raw: Decimal, current_raw: Decimal) -> Decimal:
    """`Δpp = (本期 raw_value − 上期 raw_value) × 100`（**未舍入**）。

    入参只接受权威原始的 `MetricResult.raw_value`。两个 `Decimal` 之差是精确的：
    不引入任何浮点、不预先舍入、不做单位换算之外的处理。
    """
    if not isinstance(prior_raw, Decimal) or not isinstance(current_raw, Decimal):
        raise DerivedFactError("Δpp 的两期输入必须是 Decimal（权威原始 raw_value）")
    return (current_raw - prior_raw) * _HUNDRED


def render_delta_pp(value: Decimal) -> str:
    """`value` → 读者可见的渲染串：2 位小数 `ROUND_HALF_UP` + 「个百分点」。

    **只在展示时舍入**：`value` 本身保持未舍入，血缘里回读到的就是算出来的那个数。
    负值逐字保留（`-3.3` → `-3.3个百分点`），不换成正数再补方向词。
    """
    if not isinstance(value, Decimal):
        raise DerivedFactError("render_delta_pp 需要 Decimal")
    quantized = value.quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{DELTA_FACT_UNIT}"


def input_citation_payload(identity: "DeltaInputIdentity", period: str) -> dict:
    """一期的**权威引用键**（`harness.schema.CitationRef` 的字段子集）。

    它指向真实存在的指标结果（`snapshot_id + formula_id + formula_version + period`）：
    Δpp 自己**没有**持久化的指标结果，因此它的两期引用必须逐条指向真实可回查的输入记录，
    绝不发明一条查不到来源的引用。本函数只声明引用键，`CitationRef` 由 `sections` 侧物化。
    """
    return {"ref_type": "structured", "snapshot_id": identity.snapshot_id,
            "formula_id": identity.formula_id,
            "formula_version": identity.formula_version, "period": period}


# ---------------------------------------------------------------------------
# 输入与结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeltaInputIdentity:
    """两期输入**共同**的身份与口径（「同主体/合并范围/指标公式与口径」的那一份声明）。

    这份身份不是从两期各自拼出来的：它是调用方给出的**本轮权威身份**，两期结果都必须与它
    逐项相符，否则就是拿两组不可比的数据相减。任何一项不符 → 不生成事实。
    """

    snapshot_id: str
    scope: str
    currency: str
    formula_id: str
    formula_version: str
    period_basis: str

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "scope", "currency", "formula_id", "formula_version"):
            if not str(getattr(self, name) or ""):
                raise DerivedFactError(f"DeltaInputIdentity.{name} 不得为空")
        if self.period_basis not in fpb.PERIOD_BASES:
            raise DerivedFactError(
                f"DeltaInputIdentity.period_basis={self.period_basis!r} 不在"
                f" {list(fpb.PERIOD_BASES)} 内（口径是封闭取值）")


@dataclass(frozen=True)
class DeltaInputPeriod:
    """一期的可读回输入：期间、权威原值、状态，以及它在 FactPack 里的输入事实 id 与引用键。"""

    period: str
    raw_value: Decimal
    status: str
    fact_id: str
    citation: dict

    def __post_init__(self) -> None:
        if not self.period or not self.fact_id:
            raise DerivedFactError("DeltaInputPeriod 的 period / fact_id 不得为空")
        if self.status not in _AVAILABLE_STATUSES:
            raise DerivedFactError(
                f"DeltaInputPeriod.status={self.status!r} 不是可用状态 {list(_AVAILABLE_STATUSES)}"
                "（代理口径与 missing 一样按 §5.1 不进入派生计算）")


@dataclass(frozen=True)
class DeltaFact:
    """Δpp 派生展示事实（身份、两期输入、公式版本与可读回血缘齐备）。"""

    fact_id: str
    kind: str
    code: str
    label: str
    period: str
    period_label: str
    value: Decimal
    display: str
    unit: str
    status: str
    reason_code: str | None
    note: str
    period_basis: str
    formula_version: str
    identity: DeltaInputIdentity
    inputs: tuple[DeltaInputPeriod, DeltaInputPeriod]  # (上期, 本期)

    def __post_init__(self) -> None:
        prior, current = self.inputs
        if len(self.inputs) != 2:
            raise DerivedFactError("DeltaFact 必须恰有两期输入（上期, 本期）")
        if prior.period >= current.period:
            raise DerivedFactError(
                f"DeltaFact 的输入必须按 (上期, 本期) 升序排列，得到 "
                f"{(prior.period, current.period)}")
        expected_id = f"derived_{DELTA_FACT_CODE}_{current.period}_{prior.period}"
        if self.fact_id != expected_id:
            raise DerivedFactError(
                f"DeltaFact.fact_id 必须是确定性身份 {expected_id!r}，得到 {self.fact_id!r}")
        if self.period != f"{current.period}|{prior.period}":
            raise DerivedFactError(
                f"DeltaFact.period 必须是复合期间记号 {current.period}|{prior.period}，"
                f"得到 {self.period!r}（它**不是**任何单一期间：单期间字段不得冒充双期间）")
        if self.kind != DELTA_FACT_KIND or self.code != DELTA_FACT_CODE:
            raise DerivedFactError(
                f"DeltaFact 的 kind/code 必须是 {DELTA_FACT_KIND!r}/{DELTA_FACT_CODE!r}")
        if self.unit != DELTA_FACT_UNIT:
            raise DerivedFactError(f"DeltaFact.unit 必须是 {DELTA_FACT_UNIT!r}")
        if self.formula_version != DELTA_FACT_FORMULA_VERSION:
            raise DerivedFactError(
                f"DeltaFact.formula_version 必须是 {DELTA_FACT_FORMULA_VERSION!r}")
        if self.period_basis not in fpb.PERIOD_BASES:
            raise DerivedFactError(
                f"DeltaFact.period_basis={self.period_basis!r} 不在 {list(fpb.PERIOD_BASES)} 内")
        if self.status not in _AVAILABLE_STATUSES:
            raise DerivedFactError(f"DeltaFact.status={self.status!r} 不是可用状态")
        expected_value = delta_pp(prior.raw_value, current.raw_value)
        if self.value != expected_value:
            raise DerivedFactError(
                f"DeltaFact.value={self.value} 与两期原值复算的 {expected_value} 不符"
                "（展示值不得回流成计算输入）")
        if self.display != render_delta_pp(self.value):
            raise DerivedFactError(
                f"DeltaFact.display={self.display!r} 与 value 的渲染 "
                f"{render_delta_pp(self.value)!r} 不符")
        expected_label = (
            f"{fpb.period_expression(current.period, self.identity.period_basis)}较"
            f"{fpb.period_expression(prior.period, self.identity.period_basis)}")
        if self.period_label != expected_label:
            raise DerivedFactError(
                f"DeltaFact.period_label 必须是两期权威期间表达的组合 {expected_label!r}，"
                f"得到 {self.period_label!r}")
        if self.status != _EXACT_STATUS:
            raise DerivedFactError(
                f"DeltaFact.status 必须是 {_EXACT_STATUS!r}，得到 {self.status!r}"
                "（§5.1：任一期为代理口径即不生成该事实，因此不存在代理口径的 Δpp）")
        if self.note or self.reason_code:
            raise DerivedFactError(
                "Δpp 的两期输入都是精确口径，它自己不得携带任何口径限定语或原因码"
                "（§5.1：代理输入不生成事实，没有「代理口径的 Δpp」这种身份可标）")

    # -- 血缘（过界时随事实一起走） ----------------------------------------

    @property
    def derived_from(self) -> tuple[str, str]:
        """两期**输入事实**在本 FactPack 里的 id（上期, 本期）——血缘的左半边。"""
        return (self.inputs[0].fact_id, self.inputs[1].fact_id)

    @property
    def input_periods(self) -> tuple[str, str]:
        """两期的权威期间记号（上期, 本期）——血缘的右半边（引用键用同一对期间）。"""
        return (self.inputs[0].period, self.inputs[1].period)

    @property
    def input_citation_payloads(self) -> tuple[dict, dict]:
        """两期输入的权威引用键（上期, 本期），逐条指向真实可回查的输入指标结果。"""
        return (self.inputs[0].citation, self.inputs[1].citation)

    @property
    def input_raw_texts(self) -> tuple[str, str]:
        """两期**未舍入**原值的规范字符串（上期, 本期）：血缘里回读到的计算输入。"""
        return (str(self.inputs[0].raw_value), str(self.inputs[1].raw_value))


@dataclass(frozen=True)
class DeltaRefusal:
    """**不生成**派生事实的类型化记录（原因码来自封闭取值，detail 只作人读说明）。"""

    reason_code: str
    fact_id: str
    period: str
    detail: str
    formula_id: str = DELTA_FACT_CODE
    label: str = DELTA_FACT_LABEL
    kind: str = DELTA_FACT_KIND
    status: str = DELTA_FACT_NOT_GENERATED_STATUS

    def __post_init__(self) -> None:
        if self.reason_code not in DERIVED_FACT_REFUSAL_REASONS:
            raise DerivedFactError(
                f"不生成派生事实的原因码 {self.reason_code!r} 不在封闭取值 "
                f"{list(DERIVED_FACT_REFUSAL_REASONS)} 内（不得自造理由）")
        if not self.fact_id:
            raise DerivedFactError("DeltaRefusal.fact_id 不得为空（缺口必须可回指身份）")
        if not self.detail:
            raise DerivedFactError("DeltaRefusal.detail 不得为空（原因要对读者可读）")

    def as_gap(self) -> dict:
        """→ FactPack 的缺口条目（`reason_code` 是类型化判据，`detail` 只是人读说明）。"""
        return {"fact_id": self.fact_id, "formula_id": self.formula_id, "label": self.label,
                "kind": self.kind, "period": self.period, "status": self.status,
                "reason_code": self.reason_code, "detail": self.detail}


# ---------------------------------------------------------------------------
# 期间选择与构造
# ---------------------------------------------------------------------------

def _is_complete_annual_end(period: str) -> bool:
    """是否是**完整年度期末**：权威日期形状且落在 12 月（`YYYY-12-DD`）。"""
    ymd = fpb.year_month_of(period)
    return ymd is not None and ymd[1] == 12


def select_delta_periods(annual_periods: Sequence[str]) -> tuple[tuple[str, str] | None, str]:
    """从权威自己声明的年度期间里选出 (上期, 本期)；选不出时返回类型化原因码。

    只认 12 月的年度期末（半年报/季报的 `annual` 标记不算「完整年度」）；最近的两个必须
    年份相邻（差 1）——中间缺一年就不是「相邻且可比」。
    """
    complete = sorted({str(p) for p in (annual_periods or ()) if _is_complete_annual_end(p)})
    if len(complete) < 2:
        return None, "insufficient_complete_annual_periods"
    prior, current = complete[-2], complete[-1]
    prior_year = fpb.year_month_of(prior)[0]
    current_year = fpb.year_month_of(current)[0]
    if current_year - prior_year != 1:
        return None, "complete_annual_periods_not_adjacent"
    return (prior, current), ""


def _input_of(result: Any, *, identity: DeltaInputIdentity, period: str,
              ) -> tuple[DeltaInputPeriod | None, str]:
    """一条指标结果 → 一期输入（含全部可复算的核对）；不合格时返回类型化原因码。"""
    if str(getattr(result, "period", "") or "") != period:
        return None, "input_metric_result_missing"
    if str(getattr(result, "snapshot_id", "") or "") != identity.snapshot_id:
        return None, "input_identity_mismatch"
    if str(getattr(result, "formula_id", "") or "") != identity.formula_id:
        return None, "input_identity_mismatch"
    if str(getattr(result, "formula_version", "") or "") != identity.formula_version:
        return None, "input_identity_mismatch"
    status = str(getattr(result, "status", "") or "")
    raw = getattr(result, "raw_value", None)
    # §5.1：「任一期为 missing 或代理口径 → 不生成」。这里**不**接受代理口径的输入：
    # 代理输入算出的差额与精确差额不是同一个量，按注册口径直接不生成（不是附一句说明后照发）。
    if status not in _AVAILABLE_STATUSES or not isinstance(raw, Decimal):
        return None, "input_metric_unavailable"
    return DeltaInputPeriod(
        period=period, raw_value=raw, status=status,
        fact_id=f"metric_{identity.formula_id}_{period}",
        citation=input_citation_payload(identity, period)), ""


def _refuse(reason_code: str, detail: str, *, period: str = "") -> DeltaRefusal:
    fact_id = f"derived_{DELTA_FACT_CODE}" + (f"_{period}" if period else "")
    return DeltaRefusal(reason_code=reason_code, fact_id=fact_id, period=period, detail=detail)


def build_delta_fact(*, results: Sequence[Any], annual_periods: Sequence[str],
                     snapshot_id: str, scope: str, currency: str,
                     input_period_basis: str,
                     source_formula_id: str = DELTA_SOURCE_FORMULA_ID,
                     ) -> DeltaFact | DeltaRefusal:
    """构造相邻两个完整年度期末的 Δpp，或返回**类型化**不生成记录（绝不抛业务例外）。

    输入是权威自己给的：`results` 为快照的指标结果表，`annual_periods` 为快照自己声明的年度
    期间末日，`snapshot_id/scope/currency` 为快照身份，`input_period_basis` 为输入指标
    **公式定义自己声明**的期间口径（`financial_v2.period_basis`，调用方不得猜）。
    """
    for value, name in ((snapshot_id, "snapshot_id"), (scope, "scope"), (currency, "currency"),
                        (source_formula_id, "source_formula_id")):
        if not str(value or ""):
            raise DerivedFactError(f"build_delta_fact 的 {name} 不得为空")
    # §5.1：「两期必须同主体、同 scope（均 `consolidated`）」。两期结果同属本快照（下面逐期核对
    # `snapshot_id`），因此 scope 只需核对本快照自己声明的那一个 —— 母公司口径与合并口径相减
    # 得到的是一个没有含义的数。要求值取权威自己的 `formulas.SCOPE_REQUIREMENT`，不在这里
    # 另写一份字面量。
    if str(scope) != fformulas.SCOPE_REQUIREMENT:
        return _refuse(
            "input_identity_mismatch",
            f"快照 scope={scope!r} 不是本指标要求的 {fformulas.SCOPE_REQUIREMENT!r}"
            "（§5.1：两期均须为合并口径）：口径不可比，不生成该事实")
    if input_period_basis not in fpb.PERIOD_BASES:
        return _refuse(
            "input_period_basis_unavailable",
            f"输入指标 {source_formula_id} 的期间口径 {input_period_basis!r} 无法判定，"
            f"不得凭期间记号的外观替它挑一个（封闭取值 {list(fpb.PERIOD_BASES)}）")

    pair, reason = select_delta_periods(annual_periods)
    if pair is None:
        detail = {
            "insufficient_complete_annual_periods":
                "快照里不足两个完整年度期末（12 月的年度期间末日），无法计算相邻年度变动："
                "缺前期时不生成该事实",
            "complete_annual_periods_not_adjacent":
                f"快照里最近的完整年度期末 {list(annual_periods)} 不相邻（年份差不为 1）："
                "中间缺一年，两期不可比，不生成该事实",
        }[reason]
        return _refuse(reason, detail)

    prior_period, current_period = pair
    # 公式版本来自**权威结果自己**（同一公式在同一个快照里必须只有一个版本）；先取本期，
    # 再要求上期与它一致 —— 两期版本不同意味着指标定义变过，相减无意义。
    current_result = next((r for r in results
                           if str(getattr(r, "formula_id", "") or "") == source_formula_id
                           and str(getattr(r, "period", "") or "") == current_period), None)
    if current_result is None:
        return _refuse("input_metric_result_missing",
                       f"本期 {current_period} 没有 {source_formula_id} 的指标结果："
                       "两期输入不齐，不生成该事实",
                       period=f"{current_period}_{prior_period}")
    formula_version = str(getattr(current_result, "formula_version", "") or "")
    if not formula_version:
        return _refuse("input_identity_mismatch",
                       f"本期 {current_period} 的 {source_formula_id} 结果没有公式版本："
                       "无法确认两期用同一个公式定义，不生成该事实",
                       period=f"{current_period}_{prior_period}")
    identity = DeltaInputIdentity(
        snapshot_id=str(snapshot_id), scope=str(scope), currency=str(currency),
        formula_id=str(source_formula_id), formula_version=formula_version,
        period_basis=input_period_basis)

    prior_result = next((r for r in results
                         if str(getattr(r, "formula_id", "") or "") == source_formula_id
                         and str(getattr(r, "period", "") or "") == prior_period), None)
    if prior_result is None:
        return _refuse("input_metric_result_missing",
                       f"上期 {prior_period} 没有 {source_formula_id} 的指标结果："
                       "缺前期，不生成该事实",
                       period=f"{current_period}_{prior_period}")

    detail_period = f"{current_period}_{prior_period}"
    inputs: list[DeltaInputPeriod] = []
    for result, period in ((prior_result, prior_period), (current_result, current_period)):
        item, reason = _input_of(result, identity=identity, period=period)
        if item is None:
            message = {
                "input_metric_result_missing":
                    f"{period} 的 {source_formula_id} 指标结果与期间身份不符，不生成该事实",
                "input_identity_mismatch":
                    f"{period} 的 {source_formula_id} 结果与本期权威身份"
                    f"（快照 {identity.snapshot_id} / 公式版本 {identity.formula_version}）不一致："
                    "两期不可比，不生成该事实",
                "input_metric_unavailable":
                    f"{period} 的 {source_formula_id} 状态是"
                    f"{getattr(result, 'status', '')!r}、原始值为"
                    f"{getattr(result, 'raw_value', None)!r}：该期输入不可用（不是精确口径），"
                    "按 §5.1「任一期为 missing 或代理口径 → 不生成」不生成该事实",
            }[reason]
            return _refuse(reason, message, period=detail_period)
        inputs.append(item)

    prior_input, current_input = inputs[0], inputs[1]
    value = delta_pp(prior_input.raw_value, current_input.raw_value)
    return DeltaFact(
        fact_id=f"derived_{DELTA_FACT_CODE}_{current_period}_{prior_period}",
        kind=DELTA_FACT_KIND, code=DELTA_FACT_CODE, label=DELTA_FACT_LABEL,
        period=f"{current_period}|{prior_period}",
        period_label=(f"{fpb.period_expression(current_period, input_period_basis)}较"
                      f"{fpb.period_expression(prior_period, input_period_basis)}"),
        value=value, display=render_delta_pp(value), unit=DELTA_FACT_UNIT,
        status=_EXACT_STATUS, reason_code=None, note="",
        # 期间口径：Δpp 说的是**两个时点之间**的变化，按 `period_basis` 模块对 `yoy_*` 的
        # 同一规则，它是期间量而不是时点量（时点量没有「变化」）。
        period_basis=fpb.BASIS_FLOW,
        formula_version=DELTA_FACT_FORMULA_VERSION,
        identity=identity, inputs=(prior_input, current_input))


__all__ = [
    "DELTA_FACT_FORMULA_VERSION", "DELTA_SOURCE_FORMULA_ID", "DELTA_FACT_CODE",
    "DELTA_FACT_KIND", "DELTA_FACT_LABEL", "DELTA_FACT_UNIT",
    "DELTA_FACT_NOT_GENERATED_STATUS", "DERIVED_FACT_REFUSAL_REASONS",
    "DerivedFactError", "DeltaInputIdentity", "DeltaInputPeriod", "DeltaFact", "DeltaRefusal",
    "delta_pp", "render_delta_pp", "input_citation_payload", "select_delta_periods",
    "build_delta_fact",
]

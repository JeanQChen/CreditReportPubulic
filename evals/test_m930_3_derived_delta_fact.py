"""M930-3 §二 定点批：**唯一**获批的派生展示事实 —— 资产负债率变动 Δpp（`ddf-1`）。

用法: python -X utf8 -m evals.test_m930_3_derived_delta_fact

本模块只验证 `FORMULA_REVIEW` §5.1 定点登记的那一项，以及它过界后的四件事：

1. **公式与量纲**：`Δpp = (本期 raw_value − 上期 raw_value) × 100`，两个 `Decimal` 原值相减，
   展示时才 2 位小数 `ROUND_HALF_UP`，负值逐字保留；**绝不**用表格里已舍入的 `69.34%` 这类
   显示值相减（本模块用一组**能区分两种算法**的原值给出反例，而不是口头声明）；
2. **§5.1 的异常规则逐字落地**：「缺前期 / 两期 scope 或指标公式版本不一致 / 任一期为 missing
   或代理口径 → **不生成**，并留下原因」。代理口径是**不生成**的理由，不是「生成后附一句代理
   说明」的理由 —— 本模块给出反例证明代理输入确实被拒；
3. **两期身份与过界**：完整的两期引用（不是一条捏造的引用）、两期未舍入原值血缘、以及
   「两期原值**不进**数字授权面」；单期字段不得冒充双期（构造层 / Worker 层 / 投影层各有见证）；
4. **不污染期间轴表格**：双期派生事实永不成为「指标 × 期间」的一行，并由两个反例把这条排除钉在
   **两条独立防线**上（声明字段 + 列轴），而不是「期间数不足」的偶然结果。

本模块**不**包含任何公司、股票代码、页码或答案关键词专用判据：所有输入都是自造的 Decimal
与自造的期间记号，断言只针对公式与规则本身。
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import derived_facts as DF
from financial_v2 import formulas as FFORMULAS
from financial_v2 import period_basis as PB
from harness import schema as HS
from sections import financial_pack_artifact as FPA
from sections import financial_worker as FW
from sections import narrative_schema as NS
from sections import schema as SS

TOPIC = "financial_metrics"
ENTITY = "co-1"
ARTIFACT_ID = "ffpa_fixture"
SNAPSHOT_ID = "snap-1"
#: 夹具 artifact 声明的期间顺序：三个完整年度期末 + 一个季度期末（真实现场的形状）。
DECLARED_PERIODS = ("2023-12-31", "2024-12-31", "2025-12-31", "2026-03-31")
ANNUAL_PERIODS = ("2023-12-31", "2024-12-31", "2025-12-31")
DERIVED_FACT_ID = "derived_SOLV_DEBT_RATIO_DELTA_PP_2025-12-31_2024-12-31"


class _NS:
    """只读命名空间替身（判据与渲染器只按属性名读取产物容器）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

#: 一组**能区分「原值相减」与「显示值相减」**的两期原值（夹具自造，非真实公司数据）：
#: 原值精确相减（×100）得 `-3.30071`；若先把两期各自舍入到 2 位小数再相减，会得 `-3.00`。
PRIOR_RAW = Decimal("0.6523912")
CURRENT_RAW = Decimal("0.6193841")
#: 已舍入的显示值（= 表格单元格里会出现的那两个数）。
PRIOR_DISPLAY = Decimal("0.65")
CURRENT_DISPLAY = Decimal("0.62")


def _result(period: str, raw, *, status: str = "CALCULATED_EXACT",
            formula_id: str = DF.DELTA_SOURCE_FORMULA_ID, formula_version: str = "fv-1",
            snapshot_id: str = SNAPSHOT_ID) -> _NS:
    """一条指标结果替身（形状对齐 `financial_v2.schema.MetricResult` 的读口）。"""
    return _NS(period=period, raw_value=raw, display_value=raw, status=status,
               formula_id=formula_id, formula_version=formula_version,
               snapshot_id=snapshot_id, reason_code=None, unit="percent")


RESULTS = [_result("2023-12-31", Decimal("0.6934")),
           _result("2024-12-31", PRIOR_RAW),
           _result("2025-12-31", CURRENT_RAW)]


def _build(**kw):
    args = dict(results=RESULTS, annual_periods=ANNUAL_PERIODS, snapshot_id=SNAPSHOT_ID,
                scope="consolidated", currency="CNY", input_period_basis=PB.BASIS_END)
    args.update(kw)
    return DF.build_delta_fact(**args)


def _fin_fact(fact_id: str, *, code: str, label: str, period: str, display: str,
              kind: str = "calculation", unit: str = "percent",
              period_basis: str = PB.BASIS_END, **extra) -> _NS:
    """一条权威财务事实替身（形状对齐 `ffpa-3` 投影：事实自己带口径与期间表达）。"""
    return _NS(fact_id=fact_id, kind=kind, code=code, label=label, period=period,
               period_basis=period_basis,
               period_label=PB.period_expression(period, period_basis) if period_basis else "",
               display=display, value_text=display, unit=unit, status="CALCULATED_EXACT",
               note="", reason_code=None, text="",
               citation={"ref_type": "structured", "snapshot_id": SNAPSHOT_ID, "period": period},
               **extra)


def _claim(text: str, *, binding_id: str) -> SS.SectionClaim:
    refs = (HS.CitationRef(ref_type="evidence", evidence_id="ev1"),)
    bindings = (binding_id,)
    claim_id = SS.derive_claim_id("fact", TOPIC, ("q1",), text, refs,
                                  f"ccand_{binding_id}", "dr-1", bindings)
    return SS.SectionClaim(claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION,
                           section_id="financial", topic_id=TOPIC, question_ids=("q1",),
                           text=text, claim_type="fact", citation_refs=refs,
                           claim_candidate_id=f"ccand_{binding_id}",
                           claim_candidate_revision="dr-1", accepted_binding_ids=bindings)


def _binding(binding_id: str, fact_id: str) -> _NS:
    return _NS(accepted_support_binding_id=binding_id, authority_kind="financial_pack",
               authority_container_id=ARTIFACT_ID, material_id=None,
               support_semantics="factual", authorization_path="path_a_prevalidated",
               source_identity="src1", payload_ref=None, locator_ref=None, fact_id=None,
               financial_fact_id=fact_id, note_fact_id=None, external_fact_id=None,
               binding_decision_id="cbd-1", entailment_decision_id="ced-1")


def _authority(facts) -> _NS:
    return _NS(producer_kind="financial_workflow", company_id=ENTITY,
               artifact=_NS(artifact_id=ARTIFACT_ID, periods=DECLARED_PERIODS,
                            facts=tuple(facts)))


def _level_bundle():
    """三个期间的资产负债率（层级事实）→ (facts, claims, bindings)，Claim 文本即权威表面。"""
    facts, claims, bindings = [], [], []
    for period, display in (("2023-12-31", "69.34%"), ("2024-12-31", "65.24%"),
                            ("2025-12-31", "61.94%")):
        fid = f"metric_SOLV_DEBT_RATIO_{period}"
        fact = _fin_fact(fid, code="SOLV_DEBT_RATIO", label="资产负债率", period=period,
                         display=display)
        bid = f"asb_{period}"
        claim = _claim(NS.authoritative_fact_surface("financial_pack", fact),
                       binding_id=bid)
        facts.append(fact)
        claims.append(claim)
        bindings.append(_binding(bid, fid))
    return facts, claims, bindings


# ---------------------------------------------------------------------------
# 主用例
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}（异常文本须含 {needle!r}；实际 {str(e)[:200]!r}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}（异常类型应为 {exc.__name__}；实际 "
                           f"{type(e).__name__}: {str(e)[:200]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}（没有抛异常）")

    # ==================================================================
    # 1. 公式：只用两期 raw_value 的 Decimal 相减，展示时才舍入
    # ==================================================================
    check(DF.DELTA_FACT_FORMULA_VERSION == "ddf-1",
          f"版本：公式版本是 ddf-1（实际 {DF.DELTA_FACT_FORMULA_VERSION}）")
    check(DF.DELTA_SOURCE_FORMULA_ID == "SOLV_DEBT_RATIO",
          "输入：唯一的输入指标是资产负债率")
    check(DF.DELTA_FACT_CODE not in FFORMULAS.build_registry(),
          "身份：Δpp **不在**冻结公式注册表里（§5.1 明确它是非评分、展示型派生事实，"
          "注册表是受审指标的封闭集合）")

    exact = DF.delta_pp(PRIOR_RAW, CURRENT_RAW)
    check(exact == Decimal("-3.30071"),
          f"公式：(0.6193841 − 0.6523912) × 100 = -3.30071（实际 {exact}）")
    check(DF.delta_pp(Decimal("0.4"), Decimal("0.5")) == Decimal("10"),
          "公式：方向由符号决定（上升 → 正值）")
    check(DF.delta_pp(Decimal("0.5"), Decimal("0.4")) == Decimal("-10"),
          "公式：方向由符号决定（下降 → 负值，逐字保留）")
    check(DF.delta_pp(Decimal("0.5"), Decimal("0.5")) == Decimal("0"),
          "公式：持平 → 0（不编方向词，也不写「改善」）")
    check(DF.delta_pp(Decimal("0.6523912"), Decimal("0.6523912")) == Decimal("0"),
          "公式：两期同值 → 0（Decimal 精确为零，不出现 -0.0 这类浮点尾巴）")

    # 反例：把**已舍入的显示值**相减会得到另一个数 —— 这正是 §5.1 明令禁止的输入。
    rounded_inputs = DF.delta_pp(PRIOR_DISPLAY, CURRENT_DISPLAY)
    check(rounded_inputs == Decimal("-3.00"),
          f"反例：已舍入显示值相减得 {rounded_inputs}（两个 2 位小数之差）")
    check(rounded_inputs != exact,
          f"反例：两种算法结果必须不同（显示值相减 {rounded_inputs} ≠ 原值相减 {exact}）"
          "—— 若公式改用显示值，这条与上一条会同时成立，本组断言就会抓住它")

    check(DF.render_delta_pp(exact) == "-3.3个百分点",
          f"展示：2 位小数 + 去尾部零（与 `sections.common.format_metric_display` 同一展示"
          f"约定；实际 {DF.render_delta_pp(exact)!r}）")
    check(DF.render_delta_pp(Decimal("-10")) == "-10个百分点",
          "展示：负值原样保留负号（不取绝对值再补方向词）")
    check(DF.render_delta_pp(Decimal("-3.305")) == "-3.31个百分点",
          "展示：2 位小数 ROUND_HALF_UP（-3.305 → -3.31，不是截断）")
    check(DF.render_delta_pp(Decimal("-3.304")) == "-3.3个百分点",
          "展示：2 位小数 ROUND_HALF_UP（-3.304 → -3.3）")
    check(DF.render_delta_pp(Decimal("-3.3")) == "-3.3个百分点",
          "展示：去尾部零（-3.3 不是 -3.30）")
    check(DF.render_delta_pp(Decimal("0")) == "0个百分点",
          "展示：零值也是「0个百分点」，不是空串")
    check(DF.render_delta_pp(Decimal("1")) == "1个百分点" and DF.DELTA_FACT_UNIT == "个百分点",
          "展示：单位是「个百分点」（Δpp 的量纲，不是 % 也不是倍）")
    # 公式与展示都拒绝非 Decimal（float 会把 -3.30071 这类差值变成不可控的二进制近似）。
    expect_error(lambda: DF.delta_pp(0.6523912, 0.6193841), DF.DerivedFactError,
                 "公式：float 输入必须被拒（Decimal 是唯一被授权的数值类型）",
                 needle="Decimal")
    expect_error(lambda: DF.render_delta_pp(1.5), DF.DerivedFactError,
                 "展示：float 输入必须被拒", needle="Decimal")

    # ==================================================================
    # 2. 期间选择：相邻的两个**完整年度期末**
    # ==================================================================
    pair, reason = DF.select_delta_periods(ANNUAL_PERIODS)
    check(pair == ("2024-12-31", "2025-12-31") and reason == "",
          f"期间：取最近的两个完整年度期末（实际 {pair} / {reason!r}）")
    pair, reason = DF.select_delta_periods(("2024-12-31", "2025-12-31", "2026-03-31"))
    check(pair == ("2024-12-31", "2025-12-31"),
          "期间：季度期间末日不参与（2026-03-31 不是完整年度期末）")
    pair, reason = DF.select_delta_periods(("2024-06-30", "2024-12-31", "2025-12-31"))
    check(pair == ("2024-12-31", "2025-12-31"),
          "期间：半年末（6 月）即使被标成年度期间也不算「完整年度期末」")
    pair, reason = DF.select_delta_periods(("2025-12-31",))
    check(pair is None and reason == "insufficient_complete_annual_periods",
          f"期间：只有一个年度期末 → 缺前期（实际 {pair} / {reason!r}）")
    pair, reason = DF.select_delta_periods(())
    check(pair is None and reason == "insufficient_complete_annual_periods",
          "期间：没有任何年度期末 → 缺前期")
    pair, reason = DF.select_delta_periods(("2022-12-31", "2025-12-31"))
    check(pair is None and reason == "complete_annual_periods_not_adjacent",
          f"期间：中间缺一年 → 不相邻即不可比（实际 {pair} / {reason!r}）")
    pair, reason = DF.select_delta_periods(("2024-12-31", "2026-12-31"))
    check(pair is None and reason == "complete_annual_periods_not_adjacent",
          "期间：跨两年同样不相邻")
    pair, reason = DF.select_delta_periods(("2025-12-31", "2023-12-31", "2024-12-31"))
    check(pair == ("2024-12-31", "2025-12-31"),
          "期间：输入乱序时也取最近一对（排序后再选，不依赖调用方给顺序）")

    # ==================================================================
    # 3. 构造：正例（两期引用、原值血缘、口径、期间表达）
    # ==================================================================
    fact = _build()
    check(isinstance(fact, DF.DeltaFact), f"构造：正例必须产出 DeltaFact（实际 {type(fact)}）")
    check(fact.fact_id == DERIVED_FACT_ID,
          f"身份：fact_id 是确定性的双期标识（实际 {fact.fact_id}）")
    check(fact.kind == DF.DELTA_FACT_KIND == "derived" and fact.code == DF.DELTA_FACT_CODE,
          "身份：kind 是 derived（不是 calculation —— 它不在受审指标表里）")
    check(fact.period == "2025-12-31|2024-12-31",
          f"身份：period 是复合记号（本期|上期），不是任何单一期间（实际 {fact.period}）")
    check(fact.value == Decimal("-3.30071") and fact.display == "-3.3个百分点",
          f"数值：value 保留未舍入值、display 是展示渲染（实际 {fact.value} / {fact.display}）")
    check(DF.render_delta_pp(fact.value) == fact.display,
          "自洽：display 就是 value 的渲染（读者看到的数与算出来的数同源）")
    check(fact.unit == "个百分点" and fact.formula_version == "ddf-1",
          "血缘：单位与公式版本随事实一起过界")
    check(fact.status == "CALCULATED_EXACT" and fact.note == "" and fact.reason_code is None,
          "状态：两期都是精确口径时不携带任何口径限定语")
    check(fact.period_label == "2025年末较2024年末",
          f"期间表达：两期**输入指标自己的口径**（`end`）派生的组合，读者据此知道差的是两个"
          f"年末（实际 {fact.period_label!r}）")
    check(fact.period_basis == PB.BASIS_FLOW,
          "口径：Δpp 说的是两期之间的变化，因此它是期间量（`flow`）而不是时点量 —— "
          "时点量没有「变化」可言（与 `yoy_*` 同一规则）")
    check(fact.derived_from == ("metric_SOLV_DEBT_RATIO_2024-12-31",
                               "metric_SOLV_DEBT_RATIO_2025-12-31"),
          f"血缘：回指两期**输入事实**（实际 {fact.derived_from}）")
    check(fact.input_periods == ("2024-12-31", "2025-12-31"),
          "血缘：两期输入的权威期间记号")
    check(fact.input_raw_texts == ("0.6523912", "0.6193841"),
          f"血缘：两期**未舍入**原值（可据此复算 Δpp，实际 {fact.input_raw_texts}）")
    check(fact.identity.snapshot_id == SNAPSHOT_ID and
          fact.identity.scope == "consolidated" and fact.identity.formula_version == "fv-1",
          "血缘：两期输入共同的身份（快照 / 合并口径 / 公式版本）随事实过界")
    payloads = fact.input_citation_payloads
    check(len(payloads) == 2 and all(p["ref_type"] == "structured" for p in payloads),
          "引用：完整引用集是**两条**结构化引用")
    check([p["period"] for p in payloads] == ["2024-12-31", "2025-12-31"],
          f"引用：逐条指向真实的一期输入（实际 {[p['period'] for p in payloads]}）")
    check(all(p["formula_id"] == "SOLV_DEBT_RATIO" and p["formula_version"] == "fv-1"
              and p["snapshot_id"] == SNAPSHOT_ID for p in payloads),
          "引用：两条引用都指向输入指标自己的权威记录（Δpp 没有自己的指标结果，"
          "不得发明一条查不到来源的引用）")

    # ==================================================================
    # 4. 构造：§5.1 的异常规则逐条到达（不生成 + 类型化原因）
    # ==================================================================
    r = _build(annual_periods=("2025-12-31",))
    check(isinstance(r, DF.DeltaRefusal) and
          r.reason_code == "insufficient_complete_annual_periods",
          f"拒绝：缺前期 → insufficient_complete_annual_periods（实际 {r!r}）")
    r = _build(annual_periods=("2022-12-31", "2025-12-31"))
    check(isinstance(r, DF.DeltaRefusal) and
          r.reason_code == "complete_annual_periods_not_adjacent",
          "拒绝：年度期末不相邻 → complete_annual_periods_not_adjacent")
    r = _build(results=[RESULTS[2]])
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_metric_result_missing",
          "拒绝：上期没有指标结果 → input_metric_result_missing")
    r = _build(results=[_result("2024-12-31", PRIOR_RAW, formula_id="SOLV_CURRENT_RATIO"),
                        RESULTS[2]])
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_metric_result_missing",
          "拒绝：上期是别的指标 → input_metric_result_missing（不跨指标相减）")
    # §5.1：「任一期为 missing 或代理口径 → 不生成」——两种状态走**同一条**异常规则。
    for bad_status in ("missing", "CALCULATED_PROXY"):
        r = _build(results=[RESULTS[1],
                            _result("2025-12-31", CURRENT_RAW, status=bad_status)])
        check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_metric_unavailable",
              f"拒绝：本期状态 {bad_status!r} → input_metric_unavailable"
              f"（§5.1 把 missing 与代理口径并列在同一条异常规则里；实际 {r!r}）")
    check("CALCULATED_PROXY" not in DF._AVAILABLE_STATUSES,
          "拒绝：代理口径**不在**可用状态内 —— 代理输入算出的差额是另一个量（不是两期精确"
          "值之差），按注册口径直接不生成，而不是附一句代理说明后照发")
    r = _build(results=[RESULTS[1], _result("2025-12-31", None)])
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_metric_unavailable",
          "拒绝：本期 raw_value 为空 → input_metric_unavailable（不得拿 display 顶替）")
    r = _build(results=[RESULTS[1],
                        _result("2025-12-31", CURRENT_RAW, formula_version="fv-2")])
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_identity_mismatch",
          "拒绝：两期公式版本不一致 → input_identity_mismatch（指标定义变过，相减无意义）")
    r = _build(results=[RESULTS[1],
                        _result("2025-12-31", CURRENT_RAW, snapshot_id="snap-2")])
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_identity_mismatch",
          "拒绝：某期结果不属于本快照 → input_identity_mismatch（主体/合并范围不可比）")
    # §5.1：「两期必须同主体、同 scope（均 `consolidated`）」。母公司口径与合并口径相减
    # 得到的是一个没有含义的数。
    r = _build(scope="parent")
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_identity_mismatch",
          f"拒绝：快照不是合并口径 → input_identity_mismatch（实际 {r!r}）")
    check(FFORMULAS.SCOPE_REQUIREMENT == "consolidated" and
          "SCOPE_REQUIREMENT" in inspect.getsource(DF),
          "要求值：口径要求取自权威自己的 `formulas.SCOPE_REQUIREMENT`，"
          "不在派生模块里另写一份字面量")
    r = _build(input_period_basis="")
    check(isinstance(r, DF.DeltaRefusal) and r.reason_code == "input_period_basis_unavailable",
          "拒绝：输入指标口径不可判定 → input_period_basis_unavailable（不凭外观挑一个）")

    # 拒绝记录本身：封闭取值 + 可回指身份 + 人读说明 + 明确状态。
    refusal = _build(annual_periods=("2025-12-31",))
    gap = refusal.as_gap()
    check(gap["reason_code"] == refusal.reason_code and gap["fact_id"] and gap["detail"],
          "拒绝记录：带 fact_id 与人读 detail（缺口不得无名无因）")
    check(gap["status"] == DF.DELTA_FACT_NOT_GENERATED_STATUS == "NOT_GENERATED" and
          gap["kind"] == "derived" and gap["label"] == DF.DELTA_FACT_LABEL,
          "拒绝记录：状态是 NOT_GENERATED（不是 0，也不是静默省略）")
    expect_error(lambda: DF.DeltaRefusal(reason_code="因为不太好", fact_id="x", period="",
                                        detail="y"), DF.DerivedFactError,
                 "拒绝记录：封闭取值之外的原因码必须被拒", needle="封闭取值")
    expect_error(lambda: DF.DeltaRefusal(reason_code="input_metric_unavailable", fact_id="",
                                        period="", detail="y"), DF.DerivedFactError,
                 "拒绝记录：缺口必须可回指身份（fact_id 不得为空）", needle="回指身份")
    expect_error(lambda: DF.DeltaRefusal(reason_code="input_metric_unavailable", fact_id="x",
                                        period="", detail=""), DF.DerivedFactError,
                 "拒绝记录：原因要对读者可读（detail 不得为空）", needle="可读")
    check(len(DF.DERIVED_FACT_REFUSAL_REASONS) ==
          len(set(DF.DERIVED_FACT_REFUSAL_REASONS)) == 6,
          "拒绝记录：原因码封闭且无重复（六条，与获批边界的六种拒绝条件一一对应）")

    # ==================================================================
    # 5. 构造层不变量：单期身份不得被单期字段冒充
    # ==================================================================
    expect_error(lambda: dataclasses.replace(fact, period="2025-12-31"), DF.DerivedFactError,
                 "不变量：把 period 改回单期记号必须被拒（单期间字段不得冒充双期间）",
                 needle="单期间字段不得冒充双期间")
    expect_error(lambda: dataclasses.replace(fact, period="2024-12-31|2025-12-31"),
                 DF.DerivedFactError,
                 "不变量：复合记号必须写成（本期|上期）", needle="复合期间记号")
    expect_error(lambda: dataclasses.replace(fact, value=Decimal("-3.3")), DF.DerivedFactError,
                 "不变量：value 与两期原值复算不符必须被拒（展示值不得回流成计算输入）",
                 needle="复算")
    expect_error(lambda: dataclasses.replace(fact, display="-3.30个百分点"),
                 DF.DerivedFactError, "不变量：display 必须是 value 的渲染", needle="渲染")
    expect_error(lambda: dataclasses.replace(fact, unit="%"), DF.DerivedFactError,
                 "不变量：单位只能是「个百分点」", needle="个百分点")
    expect_error(lambda: dataclasses.replace(fact, formula_version="ddf-2"),
                 DF.DerivedFactError, "不变量：公式版本只能是 ddf-1", needle="ddf-1")
    expect_error(lambda: dataclasses.replace(fact, period_label="2025年度"), DF.DerivedFactError,
                 "不变量：期间表达必须是两期的组合（不得退化成单期）", needle="期间表达")
    expect_error(lambda: dataclasses.replace(fact, note="口径：精确"), DF.DerivedFactError,
                 "不变量：精确口径的事实不得携带口径限定语", needle="限定语")
    expect_error(lambda: dataclasses.replace(fact, status="CALCULATED_PROXY"),
                 DF.DerivedFactError,
                 "不变量：不存在代理口径的 Δpp（§5.1 代理输入即不生成）",
                 needle="不是可用状态")
    expect_error(lambda: DF.DeltaInputPeriod(
        period="2024-12-31", raw_value=PRIOR_RAW, status="CALCULATED_PROXY",
        fact_id="metric_SOLV_DEBT_RATIO_2024-12-31",
        citation={"ref_type": "structured"}), DF.DerivedFactError,
        "不变量：代理口径的输入不得进入派生计算", needle="§5.1")
    expect_error(lambda: DF.DeltaInputIdentity(
        snapshot_id="s", scope="consolidated", currency="CNY", formula_id="SOLV_DEBT_RATIO",
        formula_version="fv-1", period_basis="guess"), DF.DerivedFactError,
        "不变量：口径是封闭取值（不得自造）", needle="封闭取值")
    expect_error(lambda: DF.DeltaInputIdentity(
        snapshot_id="s", scope="", currency="CNY", formula_id="SOLV_DEBT_RATIO",
        formula_version="fv-1", period_basis=PB.BASIS_END), DF.DerivedFactError,
        "不变量：两期共同身份的任何一项都不得为空", needle="scope")
    inputs = fact.inputs
    expect_error(lambda: dataclasses.replace(fact, inputs=(inputs[1], inputs[0])),
                 DF.DerivedFactError,
                 "不变量：两期输入必须按（上期, 本期）升序存放（顺序颠倒会改掉符号的含义）",
                 needle="升序")

    # ==================================================================
    # 6. 过界：Worker 的 FinancialFact / artifact 投影 / 下游引用派生
    # ==================================================================
    built = FW._derived_fact_of(
        _NS(snapshot_id=SNAPSHOT_ID, scope="consolidated", currency="CNY"),
        _NS(results=tuple(RESULTS)), {"annual_periods_available": list(ANNUAL_PERIODS)})
    check(isinstance(built, tuple) and len(built) == 2 and built[1] is None and
          built[0] is not None, f"Worker：正例产出 (事实, None)（实际 {built!r}）")
    worker_fact = built[0]
    check(isinstance(worker_fact, FW.FinancialFact), "Worker：产出的是 FinancialFact")
    check(worker_fact.fact_id == DERIVED_FACT_ID and worker_fact.kind == "derived" and
          worker_fact.code == DF.DELTA_FACT_CODE and worker_fact.label == DF.DELTA_FACT_LABEL,
          "Worker：身份逐字过界（id / kind / code / label）")
    check(worker_fact.formula_version == "ddf-1" and
          worker_fact.derived_from == ("metric_SOLV_DEBT_RATIO_2024-12-31",
                                       "metric_SOLV_DEBT_RATIO_2025-12-31"),
          "Worker：公式版本与两期血缘随事实过界")
    check(worker_fact.input_periods == ("2024-12-31", "2025-12-31") and
          worker_fact.input_raw_texts == ("0.6523912", "0.6193841"),
          "Worker：两期期间与**未舍入**原值随事实过界")
    check(len(worker_fact.citations) == 2 and worker_fact.citation == worker_fact.citations[1],
          "Worker：主引用是完整引用集里的**本期**那一条（它是集合成员，不是另立一条）")
    check(worker_fact.period_basis == PB.BASIS_FLOW and
          worker_fact.period_label == "2025年末较2024年末",
          "Worker：口径与期间表达随事实过界")
    check(NS.is_derived_two_period_fact(worker_fact) is True,
          "判据：双期派生由事实**自己声明的 `input_periods`** 判定")
    check(NS.is_proxy_fact(worker_fact) is False and NS.proxy_qualifier(worker_fact) == "",
          "口径：Δpp 不是代理事实，也**不得**被加上任何口径限定语")
    # 拒绝路径：Worker 把类型化拒绝转成缺口，而不是抛一个没有原因码的例外。
    refused, gap = FW._derived_fact_of(
        _NS(snapshot_id=SNAPSHOT_ID, scope="consolidated", currency="CNY"),
        _NS(results=tuple(RESULTS)), {"annual_periods_available": ["2025-12-31"]})
    check(refused is None and isinstance(gap, dict) and
          gap["reason_code"] == "insufficient_complete_annual_periods",
          f"Worker：拒绝时产出 (None, 类型化缺口)（实际 {refused!r} / {gap!r}）")

    single = _fin_fact("metric_SOLV_DEBT_RATIO_2025-12-31", code="SOLV_DEBT_RATIO",
                       label="资产负债率", period="2025-12-31", display="61.94%")

    # 数字授权面：读者可见的数值只有 display / value_text，两期**原值**不得进池。
    pool_worker = NS.authority_numeric_texts("financial_pack", worker_fact)
    check("0.6523912" not in pool_worker and "0.6193841" not in pool_worker,
          f"授权面：两期未舍入原值不进数字授权池（血缘用来回读，不是正文数字的来源；"
          f"实际 {pool_worker}）")
    check("-3.3个百分点" in pool_worker and "2025年末较2024年末" in pool_worker,
          f"授权面：display 与期间表达都在池里（正文里这两个可被逐字授权；"
          f"实际 {pool_worker}）")
    check("-3.30071" not in pool_worker,
          "授权面：Worker 事实没有 `value_text`，因此未舍入值不进池（读者看到的是 -3.3）")

    # 投影：新字段过界 + 无损往返。
    proj = FPA._projection_of(worker_fact)
    check(isinstance(proj, FPA.FinancialFactProjection), "投影：产出 FinancialFactProjection")
    check(len(proj.citations) == 2 and proj.derived_from == worker_fact.derived_from and
          proj.input_periods == worker_fact.input_periods and
          proj.input_raw_texts == worker_fact.input_raw_texts and
          proj.formula_version == "ddf-1",
          f"投影：完整引用集与两期血缘原样过界（实际 value_text={proj.value_text!r}）")
    # `value_text` 是 `Decimal` 的规范字符串，它保留**乘法之后的标度**：两个 7 位小数相减再 ×100
    # 得到标度 -5 的值 `-3.3007100`。它与 `-3.30071` 是同一个 `Decimal`（`==` 为真），
    # 且渲染出来的读者文本仍是 `-3.3个百分点`（先量化再舍入，标度不影响结果）。
    check(proj.value_text == "-3.3007100" and
          Decimal(proj.value_text) == worker_fact.value == exact and
          DF.render_delta_pp(Decimal(proj.value_text)) == proj.display == "-3.3个百分点",
          f"投影：value_text 是精确往返的规范字符串（标度随乘法走，数值与舍入结果都不变；"
          f"实际 {proj.value_text!r}）")
    check(FPA.FinancialFactProjection.from_dict(proj.to_dict()) == proj,
          "投影：to_dict/from_dict 无损往返（五个新字段一个不丢）")
    pool_proj = NS.authority_numeric_texts("financial_pack", proj)
    check("-3.3007100" in pool_proj and "-3.3个百分点" in pool_proj and
          "2025年末较2024年末" in pool_proj,
          f"授权面：投影的 value_text / display / 期间表达都在池里（实际 {pool_proj}）")
    check("-3.30071" not in pool_proj,
          "授权面：池里只有**规范字符串**那一种写法（同一数值不出现两种可授权的写法）")

    # 引用派生是**读视图（wire）侧**的实现：`citation_from_mapping` 只接受 mapping，而
    # Worker 的 `FinancialFact.citations` 持有的是 `CitationRef` **对象**。真实调用点
    # （`narrative_schema` 门后核验）解的是 `authority_fact_entries` 里的 artifact 投影，
    # 因此传投影才是它的正用法。把进程内对象喂给它必须 fail-closed（宁可抛错，也不返回
    # 一组来路不明的引用）—— 这条断言的用途是把这个 wire/对象 边界**钉死**，免得以后有人
    # 以为它对两种输入都成立。
    refs = NS.authoritative_citation_refs("financial_pack", proj)
    check(refs is not None and len(refs) == 2 and
          {r.period for r in refs} == {"2024-12-31", "2025-12-31"},
          f"引用派生：两期引用必须**两条**都被交出（否则「这个差额从哪两期算出来」无法回查；"
          f"实际 {refs}）")
    check(NS.authoritative_citation_refs("financial_pack", single) ==
          (NS.citation_from_mapping(single.citation),),
          "引用派生：单引用事实仍只交出一条（空 citations = 就是 citation 那一条）")
    expect_error(lambda: NS.authoritative_citation_refs("financial_pack", worker_fact),
                 NS.NarrativeSchemaError,
                 "引用派生：进程内对象不是 wire 载荷 —— 传 Worker 事实必须 fail-closed"
                 "（真实调用点解的是 artifact 投影）", needle="必须是 mapping")
    check("0.6523912" not in pool_proj and "0.6193841" not in pool_proj,
          "授权面：投影的两期原值同样不进池（血缘字段刻意不参与授权）")
    expect_error(lambda: dataclasses.replace(proj, citations=(proj.citations[0],)),
                 FPA.FinancialArtifactError,
                 "投影：只留一条引用的多引用集必须被拒（引用集不得残缺）",
                 needle="多引用集只用于")
    expect_error(lambda: dataclasses.replace(proj, input_periods=()),
                 FPA.FinancialArtifactError,
                 "投影：血缘三列不得被部分抹掉（只删其中一列必须先撞上长度一致性）",
                 needle="派生血缘长度不一致")
    expect_error(lambda: dataclasses.replace(proj, period="2024-12-31|2025-12-31"),
                 FPA.FinancialArtifactError,
                 "投影：复合记号必须写成（本期|上期）", needle="本期|上期")
    expect_error(lambda: dataclasses.replace(proj, period="2025-12-31"),
                 FPA.FinancialArtifactError,
                 "投影：双期血缘 + 单期 period 必须被拒", needle="单期间字段不得冒充双期间")
    expect_error(lambda: dataclasses.replace(proj, period_basis="guess"),
                 FPA.FinancialArtifactError,
                 "投影：口径是封闭取值（不得自造）", needle="封闭取值")

    def _worker_reject(**over) -> FW.FinancialFact:
        kw = dict(fact_id="derived_X", kind=DF.DELTA_FACT_KIND, label="x", code="x",
                  period="2025-12-31|2024-12-31", value=Decimal("0"), display="0个百分点",
                  unit="个百分点", status="CALCULATED_EXACT", reason_code=None, note="",
                  citation=worker_fact.citations[1], citations=worker_fact.citations,
                  derived_from=("a", "b"), input_periods=("2024-12-31", "2025-12-31"),
                  input_raw_texts=("0.1", "0.2"))
        kw.update(over)
        return FW.FinancialFact(**kw)

    check(_worker_reject().period == "2025-12-31|2024-12-31",
          "Worker：两期血缘 + 复合记号是唯一被接受的形态（阳性对照）")
    expect_error(lambda: _worker_reject(citations=(worker_fact.citations[1],)),
                 FW.FinancialWorkerError,
                 "Worker：只带一条引用的多引用集必须被拒", needle="多引用集只用于")
    expect_error(lambda: _worker_reject(period="2025-12-31"), FW.FinancialWorkerError,
                 "Worker：双期血缘 + 单期 period 必须被拒",
                 needle="单期间字段不得冒充双期间")
    expect_error(lambda: _worker_reject(derived_from=("a",)), FW.FinancialWorkerError,
                 "Worker：血缘长度不一致必须被拒", needle="派生血缘长度不一致")
    expect_error(lambda: _worker_reject(derived_from=("a", "b", "c"),
                                        input_periods=("2023-12-31", "2024-12-31",
                                                       "2025-12-31"),
                                        input_raw_texts=("0.1", "0.2", "0.3")),
                 FW.FinancialWorkerError,
                 "Worker：多期血缘不得冒充（本批只批准两期）", needle="两期输入")

    # ==================================================================
    # 7. 期间轴表格：双期派生事实**永不**成为一行
    # ==================================================================
    check(NS.is_derived_two_period_fact(worker_fact) is True and
          NS.is_derived_two_period_fact(single) is False,
          "判据：只有声明了两期血缘的事实才被判为双期派生（普通指标事实不受影响）")
    check(NS.is_derived_two_period_fact(
        _fin_fact("metric_Z_2025-12-31", code="Z_OTHER", label="z", period="2025-12-31",
                  display="1")) is False,
          "判据：不看 `code`（换一个编码的普通事实不会被误判成双期派生）")

    level_facts, level_claims, level_bindings = _level_bundle()
    derived_claim = _claim("2025年末较2024年末的资产负债率变动为-3.3个百分点",
                           binding_id="asb_delta")
    all_facts = tuple(level_facts) + (proj,)
    all_claims = tuple(level_claims) + (derived_claim,)
    all_bindings = tuple(level_bindings) + (_binding("asb_delta", proj.fact_id),)

    kind_checks = tuple(str(f.kind) for f in all_facts)
    check(kind_checks[:3] == ("calculation",) * 3 and kind_checks[3] == "derived",
          "前置：层级事实与派生事实的 `kind` 不同（同一 code 不会被混进同一桶）")

    tables = NS.build_metric_period_tables(
        section_id="financial", claims=all_claims, authority=_authority(all_facts),
        acceptance=_NS(accepted_bindings=all_bindings))
    check(len(tables) == 1, f"表格：只为三个期间的资产负债率成一张表（实际 {len(tables)} 张）")
    table = tables[0] if tables else None
    if table is not None:
        check(tuple(str(h) for h in table.header) ==
              (NS.FINANCIAL_TABLE_LABEL_HEADER, "2023年末", "2024年末", "2025年末"),
              f"表格：列名是三个完整年度期末的**期间表达**（实际 "
              f"{tuple(str(h) for h in table.header)}）")
        check(len(table.rows) == 1,
              f"表格：一行（同一指标 × 三期间，实际 {len(table.rows)} 行）")
        row = table.rows[0]
        check(tuple(str(c) for c in row.cells) == ("69.34%", "65.24%", "61.94%"),
              f"表格：三格逐字是权威自己的数值渲染（实际 {tuple(str(c) for c in row.cells)}）")
        row_claim_ids = tuple(str(c) for c in row.claim_ids)
        check(str(derived_claim.claim_id) not in row_claim_ids,
              "表格：派生事实的 Claim **没有**变成表格行（它承载正文）")
        check(row_claim_ids == tuple(str(c.claim_id) for c in level_claims),
              f"表格：三个期间的层级 Claim 各占一格、顺序即列序（实际 {row_claim_ids}）")
        check(all("|" not in str(h) for h in table.header) and
              proj.period not in tuple(str(h) for h in table.header),
              "表格：复合记号没有成为一列（否则表格必须给这个差额一个不存在的期间名）")
        check(str(table.unit) == "percent" and str(table.entity_scope) == ENTITY,
              "表格：单位与主体身份来自权威字段（不由 Writer 推定）")
    check(NS.tabled_claim_ids(tables) == tuple(str(c.claim_id) for c in level_claims),
          "表格：被表格呈现的 Claim 恰好是三个层级 Claim（派生 Claim 不在其列）")

    # **两条独立防线**（这是「永不成为一行」不是偶然结果的证据）：
    # (a) 声明字段这条被抹掉时，单个双期事实仍成不了行 —— 但那是 `FINANCIAL_TABLE_MIN_PERIODS`
    #     这条**通用**下界在挡，不是 2b；因此 (b) 才是 2b 真正承重的地方。
    unstamped_a = _NS(fact_id="derived_SOLV_DEBT_RATIO_DELTA_PP_2025-12-31_2024-12-31",
                      kind=proj.kind, code=proj.code, label=proj.label,
                      period="2025-12-31|2024-12-31", period_label="2025年末较2024年末",
                      display="-3.3个百分点", value_text="-3.30071", unit=proj.unit,
                      status=proj.status, note="", reason_code=None, period_basis="flow",
                      citation=proj.citation, input_periods=(), derived_from=(),
                      input_raw_texts=())
    unstamped_b = _NS(fact_id="derived_SOLV_DEBT_RATIO_DELTA_PP_2024-12-31_2023-12-31",
                      kind=proj.kind, code=proj.code, label=proj.label,
                      period="2024-12-31|2023-12-31", period_label="2024年末较2023年末",
                      display="-4.1个百分点", value_text="-4.1", unit=proj.unit,
                      status=proj.status, note="", reason_code=None, period_basis="flow",
                      citation=proj.citation, input_periods=(), derived_from=(),
                      input_raw_texts=())
    check(NS.is_derived_two_period_fact(unstamped_a) is False and
          NS.is_derived_two_period_fact(unstamped_b) is False,
          "前提：抹掉血缘声明后它们就不再被判为双期派生（判据只看声明字段）")

    strip_claims = (tuple(level_claims) +
                    (_claim("2025年末较2024年末的资产负债率变动为-3.3个百分点",
                            binding_id="asb_sa"),
                     _claim("2024年末较2023年末的资产负债率变动为-4.1个百分点",
                            binding_id="asb_sb")))
    strip_bindings = (tuple(level_bindings) + (_binding("asb_sa", unstamped_a.fact_id),
                                               _binding("asb_sb", unstamped_b.fact_id)))
    strip_auth = _authority(tuple(level_facts) + (unstamped_a, unstamped_b))
    solo_auth = _authority(tuple(level_facts) + (unstamped_a,))
    solo_bindings = tuple(level_bindings) + (_binding("asb_sa", unstamped_a.fact_id),)
    solo_claims = tuple(level_claims) + (strip_claims[3],)

    solo_tables = NS.build_metric_period_tables(
        section_id="financial", claims=solo_claims, authority=solo_auth,
        acceptance=_NS(accepted_bindings=solo_bindings))
    check(len(solo_tables) == 1 and
          str(strip_claims[3].claim_id) not in NS.tabled_claim_ids(solo_tables),
          "纵深防线(a)：即使 2b 不存在，单个双期事实也成不了行（通用下界 "
          "FINANCIAL_TABLE_MIN_PERIODS 在挡）——因此 2b 的价值不在这一格")
    expect_error(lambda: NS.build_metric_period_tables(
        section_id="financial", claims=strip_claims, authority=strip_auth,
        acceptance=_NS(accepted_bindings=strip_bindings)), NS.NarrativeSchemaError,
        "纵深防线(b)：两个复合记号一旦被当成普通「期间」，列轴就必须给它们列名 —— "
        "而任何单一列名都不在权威声明的期间里，于是 fail-closed（不得自创一列）",
        needle="列轴只能由权威声明的期间组成")

    # ==================================================================
    # 8. 接线审计：本项只接进了这几个点，且不改动获批边界
    # ==================================================================
    worker_src = inspect.getsource(FW)
    check("fderived.build_delta_fact(" in worker_src and "_derived_fact_of(" in worker_src,
          "接线：Worker 经 `_derived_fact_of` 调用 `derived_facts`（唯一构造口）")
    check(FW._FORMULA_TO_TOPIC.get(DF.DELTA_FACT_CODE) ==
          FW._FORMULA_TO_TOPIC[DF.DELTA_SOURCE_FORMULA_ID] == "fin_solvency",
          "接线：派生事实与输入指标同主题（不新造主题，也不落进「本节之外」）")
    check(FW.FINANCIAL_FACT_SELECTION_RULE_VERSION == "fw-build_fact_pack-v3",
          f"版本：选材规则版本已递增（实际 {FW.FINANCIAL_FACT_SELECTION_RULE_VERSION}）")
    check(FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION == "ffpa-3",
          f"版本：artifact 结构版本已递增（实际 {FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION}）")
    check(FPA.FINANCIAL_PACK_PROJECTION_VERSION == "ffp-proj-3",
          f"版本：投影规则版本已递增（实际 {FPA.FINANCIAL_PACK_PROJECTION_VERSION}）")
    check(PB.PERIOD_BASIS_VERSION == "pb-1",
          "版本：口径模块未升版（新增的 `year_month_of` 只是同一次解析的公开读口，语义未变）")
    check(PB.year_month_of("2025-12-31") == (2025, 12) and
          PB.year_month_of("2025年度") is None and
          PB.is_authority_period_token("2025-12-31|2024-12-31") is False and
          PB.period_expression("2025-12-31|2024-12-31", PB.BASIS_END) ==
          "2025-12-31|2024-12-31",
          "口径：复合记号不是权威日期形状（口径模块逐字返回原记号，不替它发明表达）")
    check(DF.DERIVED_FACT_REFUSAL_REASONS == (
        "insufficient_complete_annual_periods", "complete_annual_periods_not_adjacent",
        "input_metric_result_missing", "input_metric_unavailable",
        "input_identity_mismatch", "input_period_basis_unavailable"),
        "边界：不生成的原因码集合与获批范围一致（没有偷偷放宽）")
    check(inspect.getsource(DF).count("quantize(") == 1,
          "边界：全模块只在一处舍入（`render_delta_pp`）——计算路径上没有任何预先舍入")
    check(tuple(DF.__all__) == (
        "DELTA_FACT_FORMULA_VERSION", "DELTA_SOURCE_FORMULA_ID", "DELTA_FACT_CODE",
        "DELTA_FACT_KIND", "DELTA_FACT_LABEL", "DELTA_FACT_UNIT",
        "DELTA_FACT_NOT_GENERATED_STATUS", "DERIVED_FACT_REFUSAL_REASONS",
        "DerivedFactError", "DeltaInputIdentity", "DeltaInputPeriod", "DeltaFact",
        "DeltaRefusal", "delta_pp", "render_delta_pp", "input_citation_payload",
        "select_delta_periods", "build_delta_fact"),
        "边界：模块对外只暴露登记过的那一批名字（没有偷偷加第二条派生路径）")
    check(not any(name in dir(DF) for name in
                  ("quarter_delta", "yoy_delta", "trend_delta", "regression", "score_delta")),
          "边界：没有季度/同比/趋势/回归/评分形态的派生入口（§5.1 越界即停）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)

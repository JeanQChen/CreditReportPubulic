"""Eval: `fin_balance_structure` 的**确定性**呈现（`cbs-2`／`cbsp-2`／`bsr-1`）。

用法: python -m evals.test_m930_3_balance_structure_presentation

本模块钉的是本批（M930-3 指令 II.1／II.2）把**已有**资产负债合格事实接到读者面时，最容易被
写错的几件事。每一条都对应一个具体的、会读错产物或读错数字的错法：

1. **资产负债率不得自己相除**。权威自己产出过 `SOLV_DEBT_RATIO` 指标事实（有它自己的公式与
   provenance）；本栏**取它**，取不到的期间落 typed 缺口，**不**用「负债总额 ÷ 资产总额」
   补一个权威从未产出过的数——那会造出一个和别的指标不同源、却在读者面上长得一样的数。
2. **合计行不得拿去筛**。「占资产或负债 15% 以上」这句话的分母是总量轴本身；拿
   `TOTAL_ASSETS` 这类合计行去比它自己，得到的恒是 100%，看着合理、含义全错。
3. **合并科目不得冒充独立科目**。快照里没有 `ACCOUNTS_RECEIVABLE` / `FIXED_ASSETS` 的条目，
   本栏对这两个 code 落 `no_registered_fact_for_item`，**不**拿任何合并口径的科目顶替。
4. **季度末对上年末不是同比**。「2026 年一季度末」与「2025 年末」的差额只能写成
   「较上年末」；只有相邻两个年末之间才写「同比」。措辞由两期的年月决定，没有特判余地。
5. **15% 与 20% 是两个不同类的数**。15% 是冻结 Contract `complete_set_rule` 的判据（构造期
   逐字见证）；20% 是本批规定的**展示**筛选。两个阈值分别计算、分别记录，不合并。
6. **没登记的读法要停住**。Contract 出现一条路由表没登记的 aspect ⇒ 构造期拒绝，
   不许少呈现一条已要求的内容。

夹具走 `evals.test_demo_pack_writer` 的真实 `FinancialPackArtifact` / `FinancialAuthorityInput`；
冻结 Contract 那一侧读**真实投影**（`planning.demo_scope` + `contracts.loader_v2`），不手搭
假 aspect 表。模块无公司代号、证券代码、页码、表号或固定答案关键词。不调 LLM、不联网、
不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.loader_v2 import load_contract_v2                      # noqa: E402
from evals import test_demo_pack_writer as T                          # noqa: E402
from planning import demo_scope as DS                                 # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN                   # noqa: E402
from sections import cited_balance_structure as CBS                   # noqa: E402
from sections import financial_worker as FW                           # noqa: E402

_FIN_SECTION = "financial"
_FIN_TASK = "task-fin-balance-structure"
_BS_TOPIC = CHAIN.BALANCE_STRUCTURE_TOPIC
_CO = "示例股份"
_UNIT = "yuan"

_P23, _P24, _P25, _P26 = "2023-12-31", "2024-12-31", "2025-12-31", "2026-03-31"
_L23, _L24, _L25, _L26 = "2023年末", "2024年末", "2025年末", "2026年一季度末"

_A_BS = "fin_balance_structure.balance_structure"
_A_CHG = "fin_balance_structure.major_account_changes"
_A_PCT = "fin_balance_structure.fifteen_pct_forced_analysis"

#: 冻结 Contract `fifteen_pct_forced_analysis.complete_set_rule` 的逐字要求（测试侧规格；
#: §0 与真实投影逐条核对）。构造期 `_contract_threshold_rule` 会在真实投影上再见证一次。
_FIFTEEN_RULE = "占资产或负债15%以上的科目强制分析；重点科目无论占比均分析"

_BS_ASPECTS = (
    (_A_BS, "required_body", "资产负债结构", ""),
    (_A_CHG, "required_body", "重大科目变化", ""),
    (_A_PCT, "required_body", "15%以上科目强制分析", _FIFTEEN_RULE),
)


@dataclasses.dataclass(frozen=True)
class _FF:
    """财务事实规格（`FinancialFactProjection` 的测试侧规格）。

    比 `test_demo_pack_writer._FinFact` 多带**期间口径两件套**：`period_basis` 决定这一格是
    时点量还是期间量，`period_label` 是给读者看的期间说法（本栏的比较措辞由它与期间记号共同
    决定）。少了它们，测出来的不会是「读者面怎么写期间」。
    """

    fact_id: str
    label: str
    display: str
    period: str
    unit: str = _UNIT
    value_text: str | None = None
    citation: dict | None = None
    kind: str = "fact"
    code: str = ""
    status: str = "ok"
    reason_code: str | None = None
    note: str = ""
    period_basis: str = "end"
    period_label: str = ""


def _ff(*, code, period, period_label, label, value, display) -> _FF:
    """一条科目事实。`value` 是**元**的整数金额，`value_text` 是它的规范十进制串。

    引用锚点带 `ref_type`：财务 citation 缺它会在扫描期被拒（`scan_financial` 不得臆造
    locator），因此夹具必须给出一条**形状合法**的引用。
    """
    return _FF(fact_id=f"item_{code}_{period}", label=label, display=display, period=period,
               code=code, value_text=format(Decimal(int(value)), "f"),
               period_label=period_label, citation={"ref_type": "structured"})


def _metric(*, code, period, period_label, label, value, display, kind="ratio") -> _FF:
    """一条**指标**事实（`kind != "fact"`，`code` 是 formula_id 命名空间）。"""
    return _FF(fact_id=f"{code.lower()}-{period}", label=label, display=display, period=period,
               code=code, kind=kind, unit="ratio",
               value_text=format(Decimal(str(value)), "f"),
               period_label=period_label, citation={"ref_type": "structured"})


#: 夹具的**设计数**。每一个断言都用它们复算，而不是抄模块自己算出来的串。
#:
#: 选这些值的理由：`INVENTORY` 2025 占资产 16%（≥15% 命中）而 2024→2025 变动 +60%（≥20% 命中），
#: 同一条事实同时喂两根轴；`SHORT_TERM_BORROWINGS` 2025→2026Q1 变动 +50%（命中）且跨越的是
#: 「季度末 → 上年末」这一对，用来钉「较上年末，不是同比」。
_ITEM_VALUES: dict[str, dict[str, str]] = {
    # code: {period: 金额（元）}
    "TOTAL_ASSETS": {
        _P23: "800000000000", _P24: "900000000000",
        _P25: "1000000000000", _P26: "1100000000000"},
    "TOTAL_LIABILITIES": {
        _P23: "560000000000", _P24: "600000000000",
        _P25: "650000000000", _P26: "720000000000"},
    "TOTAL_EQUITY": {
        _P23: "240000000000", _P24: "300000000000",
        _P25: "350000000000", _P26: "380000000000"},
    "CURRENT_ASSETS": {_P25: "450000000000", _P26: "500000000000"},
    "NON_CURRENT_ASSETS": {_P25: "550000000000"},
    "CURRENT_LIABILITIES": {_P25: "400000000000"},
    "NON_CURRENT_LIABILITIES": {_P25: "250000000000"},
    "CASH_AND_EQUIVALENTS": {_P25: "120000000000"},
    "INVENTORY": {_P24: "100000000000", _P25: "160000000000"},
    "GOODWILL": {_P25: "5000000000"},
    "SHORT_TERM_BORROWINGS": {_P25: "80000000000", _P26: "120000000000"},
}

#: 权威自己的资产负债率事实：只有三期（**缺** 2026-03-31），用来钉「取权威、不自己相除」。
#: `(value_text, display)` 两件都显式给出——渲染串是**权威的**，不由本模块或夹具的算术生成。
_DEBT_RATIO: dict[str, tuple[str, str]] = {
    _P23: ("0.70", "70%"), _P24: ("0.6667", "66.67%"), _P25: ("0.65", "65%")}

_LABELS = {
    "TOTAL_ASSETS": "资产总额", "TOTAL_LIABILITIES": "负债总额", "TOTAL_EQUITY": "所有者权益合计",
    "CURRENT_ASSETS": "流动资产合计", "NON_CURRENT_ASSETS": "非流动资产合计",
    "CURRENT_LIABILITIES": "流动负债合计", "NON_CURRENT_LIABILITIES": "非流动负债合计",
    "CASH_AND_EQUIVALENTS": "货币资金", "INVENTORY": "存货", "GOODWILL": "商誉",
    "SHORT_TERM_BORROWINGS": "短期借款",
}

_PERIOD_LABELS = {_P23: _L23, _P24: _L24, _P25: _L25, _P26: _L26}

def _money_display(amount: str) -> str:
    """照 `format_yuan_amount` 的口径给一条渲染串（元 → ≥1 亿写作亿元）。夹具自用。"""
    value = Decimal(amount)
    yi = Decimal("100000000")
    if abs(value) >= yi:
        return f"{value / yi:f} 亿元"
    return f"{value:f} 元"


def _facts() -> tuple[_FF, ...]:
    specs: list[_FF] = []
    for code, by_period in _ITEM_VALUES.items():
        for period, amount in by_period.items():
            specs.append(_ff(code=code, period=period, period_label=_PERIOD_LABELS[period],
                             label=_LABELS[code], value=amount, display=_money_display(amount)))
    for period, (value_text, display) in _DEBT_RATIO.items():
        specs.append(_metric(code="SOLV_DEBT_RATIO", period=period,
                             period_label=_PERIOD_LABELS[period], label="资产负债率",
                             value=value_text, display=display))
    return tuple(specs)


def _requirement():
    """`fin_balance_structure` 的 Contract 投影（测试侧规格；§0 与真实投影逐条核对）。"""
    return T._Req(_BS_TOPIC, tuple(
        T._aspect(aspect_id, _BS_TOPIC, f"q-{aspect_id}", display_tier=tier,
                  requirement_text=text, complete_set_rule=rule)
        for aspect_id, tier, text, rule in _BS_ASPECTS))


def _authority(*, facts=None, periods=(_P23, _P24, _P25, _P26),
               topic_ids=(_BS_TOPIC,), topic_map=None):
    """只选 `fin_balance_structure` 的财务任务 ⇒ `create` **必须**显式给出 `fact_topic_map`。

    事实一律归到该 topic（未列出的 code 在本夹具里根本不存在），因此
    「权威里没有这一项」这件事是被**构造**出来的，不是被读出来的。

    `topic_ids` / `topic_map` 用来构造**跨 topic 的边界**：真实财务节同时声明
    `fin_source_scope` / `fin_balance_structure` / `fin_solvency`，而资产负债率那条指标事实
    按路由落在 `fin_solvency`。「本节范围内的另一栏」与「本节之外」是**两件事**，夹具必须
    能把两者分别造出来（见 §2b）。
    """
    specs = _facts() if facts is None else tuple(facts)
    task = T._task(_FIN_SECTION, tuple(topic_ids), task_id=_FIN_TASK, title="财务信息")
    artifact = T._Artifact(task_id=_FIN_TASK, facts=specs, periods=tuple(periods),
                           statements_available=("balance_sheet",))
    mapping = {(spec.fact_id, _BS_TOPIC) for spec in specs}
    if topic_map:
        mapping = {(spec.fact_id, topic_map.get(spec.fact_id, _BS_TOPIC)) for spec in specs}
    authority = T._financial_authority(
        task, artifact, note_gap=T._NoteGap(task_id=task.task_id), company_name=_CO,
        fact_topic_map=tuple(sorted(mapping)))
    return task, authority


def _presentation(*, facts=None, periods=(_P23, _P24, _P25, _P26),
                  topic_ids=(_BS_TOPIC,), topic_map=None):
    _task_obj, authority = _authority(facts=facts, periods=periods,
                                      topic_ids=topic_ids, topic_map=topic_map)
    return CBS.build_cited_balance_structure(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC, requirement=_requirement(),
        authority=authority), authority


def _expect_error(fn, exc_type, *, token: str = ""):
    try:
        fn()
    except exc_type as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(
                f"异常消息里没有 {token!r}（无法据此定位）：{message}") from None
        return exc
    except Exception as exc:  # noqa: BLE001 —— 类型不对本身就是失败，不得被吞成「通过了」
        raise AssertionError(
            f"期望 {exc_type.__name__}，实际抛出 {type(exc).__name__}: {exc}") from None
    raise AssertionError(f"这一路必须 fail-closed，但它通过了（期望含 {token!r}）")


def _gaps_of(item) -> list[dict]:
    return [dict(g) for g in item.gaps]


def _readings_of(item, kind: str) -> list[dict]:
    return [r.to_dict() for r in item.readings if r.kind == kind]


# ---------------------------------------------------------------------------
# 主流程
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
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    # ============================================ §0 冻结 Contract 与真实投影
    details.append("## §0 v2 profile 真的把 `fin_balance_structure` 纳入范围；三栏与真实投影逐条相等")
    v2 = DS.load_demo_scope_profile(CHAIN.BALANCE_STRUCTURE_PROFILE)
    check(_BS_TOPIC in tuple(v2.selected_topic_ids),
          f"v2 profile 的 `selected_topics` 含 `{_BS_TOPIC}`（实测 "
          f"{sorted(v2.selected_topic_ids)}）：本栏的内容之所以能被呈现，先决条件是它在范围内——"
          "范围外的事实会在 `scan_financial` 处被静默丢弃")
    check(CHAIN.DEFAULT_PROFILE != CHAIN.BALANCE_STRUCTURE_PROFILE,
          "v1 缺省 profile 与 v2 是**两个**声明源：新范围用新版本表达，旧版本逐字不动")

    contract = load_contract_v2(str(DS.REPO_ROOT / v2.contract_asset))
    real = {a.aspect_id: a for sec in contract.sections for a in sec.all_aspects()
            if str(getattr(a, "topic_id", "")) == _BS_TOPIC}
    check(set(real) == {a for a, _t, _x, _r in _BS_ASPECTS} and len(real) == 3,
          f"测试侧那三栏与冻结 Contract 逐条相等：夹具 {sorted(a for a, *_ in _BS_ASPECTS)} "
          f"⇔ Contract {sorted(real)}")
    check(tuple(a.aspect_id for a in real.values()) and
          all(str(getattr(a, "complete_set_rule", "") or "") == _FIFTEEN_RULE
              for a in real.values()
              if a.aspect_id == _A_PCT),
          "15% 那条判据的**原话**来自冻结 Contract 的 `complete_set_rule`，"
          "不是本模块发明的一句口号")
    check(tuple(a for a, _l in CBS.BALANCE_STRUCTURE_ASPECT_ROUTING) ==
          tuple(a.aspect_id for a in real.values()),
          "`bsr-1` 路由表的条目次序与冻结 Contract 的 aspect 次序**逐条同序同名**"
          "（次序不同会掩盖「少了一栏」）")
    check(set(CBS.balance_sheet_items())
          == set(CBS._BALANCE_SHEET_SIDE) | set(CBS._SUMMARY_CODES) == set(FW._BALANCE_SHEET_ITEMS),
          f"`_BALANCE_SHEET_SIDE ∪ _SUMMARY_CODES` 恰好覆盖 `financial_worker._BALANCE_SHEET_ITEMS`"
          f"（{len(FW._BALANCE_SHEET_ITEMS)} 个 code）：少一个的表现是「那条科目悄悄不参与筛选」，"
          "导入期即判，不靠人记得补")
    check("ACCOUNTS_RECEIVABLE" in CBS._BALANCE_SHEET_SIDE
          and "FIXED_ASSETS" in CBS._BALANCE_SHEET_SIDE,
          "被快照遗漏的那两个 code **在**识别表里：它们是「权威里没有这一项」，"
          "不是「本模块不认识它」——两种「没有」不能共用一个措辞")
    note("§0 只证明**路由表与冻结 Contract 对得上**；它不证明内容已写好，"
         "也不宣称任何内容门通过。")

    # ============================================ §1 逐条呈现、零模型调用
    details.append("## §1 逐条呈现、零模型调用、Contract 次序驱动")
    presentation, authority = _presentation()
    check(tuple(i.aspect_id for i in presentation.items)
          == tuple(a.aspect_id for a in _requirement().aspects),
          "逐条呈现，且次序**逐字等于**冻结 Contract 的 aspect 次序（不由路由表次序决定）")
    check(presentation.model_calls_issued == 0
          and presentation.producer_kind == "deterministic_presentation"
          and presentation.identity_body()["model_calls_issued"] == 0,
          "零模型调用进身份体：这一栏花过的模型额度恒等于 0，且是**产物上的读数字段**")
    check(presentation.schema_version == "cbs-2" and presentation.policy_version == "cbsp-2"
          and presentation.routing_version == "bsr-1",
          "三个版本各自在册（schema／政策／路由表）；`cbs-2`／`cbsp-2` 是本批读法三处变化的"
          "版本印记，老读数不得与新读数共用一个版本号")
    check(presentation.contract_threshold_rule == _FIFTEEN_RULE,
          f"15% 的出处被**原样**带进产物（实测 {presentation.contract_threshold_rule!r}）："
          "读者不必去翻 Contract 才知道这个数从哪来")
    check(set(presentation.to_dict()) >= {"fingerprint", "model_calls_issued", "items", "notes"},
          "wire 上带指纹与逐条条目（产物可回查，不是一个摘要字符串）")
    check(presentation.fingerprint() != _presentation(facts=_facts()[:4])[0].fingerprint(),
          "事实集合一变，指纹就变（本栏进本节清单的身份体，浅拷贝换不得）")

    # ============================================ §2 总量与结构：取用、复算、不自己相除
    details.append("## §2 总量／结构：金额逐字取权威，比例用 Decimal 复算，资产负债率取权威指标")
    bs_item = {i.aspect_id: i for i in presentation.items}[_A_BS]
    values = _readings_of(bs_item, "value")
    by_key = {(v["code"], v["period"]): v for v in values}
    ta25 = by_key.get(("TOTAL_ASSETS", _P25))
    check(ta25 is not None and ta25["display"] == _money_display("1000000000000")
          and ta25["value_text"] == "1000000000000" and ta25["unit"] == "yuan"
          and ta25["period_label"] == _L25 and ta25["period_basis"] == "end",
          f"金额**逐字**取权威的 `display`／`unit`／`period_label`（实测 {ta25 and ta25['display']!r}）："
          "本栏不自己渲染金额——「元」与「亿元」的差别是数量级，不是渲染细节")
    check(all(v["citation_identity"] for v in values),
          f"每一条读数都带自己的引用身份（{len(values)} 条全部非空）：能回查才叫合格来源")
    check(len({(v["code"], v["period"]) for v in values}) == len(values),
          "同一格只有一条读数（重复会被索引构造期拒，这里再核一次读者面）")
    check({v["period"] for v in values} == {_P23, _P24, _P25, _P26},
          "期间轴**逐字取自 artifact 声明的期间集合**（近三年及最近一期四期都在）")

    ratios = {(r["formula"], r["period"]): r for r in _readings_of(bs_item, "ratio")}
    ca = ratios.get(("CURRENT_ASSETS / TOTAL_ASSETS", _P25))
    check(ca is not None and ca["value_text"] == format(Decimal(450) / Decimal(1000), "f")
          and ca["ratio_text"] == "45%",
          f"结构比用 Decimal 在**未舍入原值**上算（实测 {ca and ca['ratio_text']!r}）："
          "450,000,000,000 / 1,000,000,000,000 = 45%")
    ca26 = ratios.get(("CURRENT_ASSETS / TOTAL_ASSETS", _P26))
    check(ca26 is not None and ca26["ratio_text"] == "45.45%"
          and ca26["value_text"] == format(Decimal(500) / Decimal(1100), "f"),
          f"同一科目不同期间各自成条、各自算（2026Q1 实测 {ca26 and ca26['ratio_text']!r}）："
          "按公式聚成一条会把期间静默丢掉")
    check(ca is not None and ca["numerator_fact_id"].startswith("item_CURRENT_ASSETS_")
          and ca["denominator_fact_id"].startswith("item_TOTAL_ASSETS_"),
          "每一条比例都记下**分子与分母各自的 fact_id**：读者能回查它是由哪两条事实算出来的，"
          "而不是一个来路不明的百分比")
    nl = ratios.get(("NON_CURRENT_LIABILITIES / TOTAL_LIABILITIES", _P25))
    check(nl is not None and nl["ratio_text"] == "38.46%",
          f"负债侧比的是负债总额这一根轴（实测 {nl and nl['ratio_text']!r}）："
          "250,000,000,000 / 650,000,000,000；两侧各有一根分母轴，不得串用")

    check(all(r["kind"] != "value" or r["code"] != "SOLV_DEBT_RATIO" for r in values),
          "资产负债率**不**作为「科目金额读数」混进 `_TOTAL_CODES` 那一族："
          "它是指标事实，不是资产负债表的科目")
    statement = bs_item.statement
    check("资产负债率" in statement and "0.65" not in statement
          and f"{_L25}为65%" in statement and _P25 not in statement,
          f"资产负债率的渲染串**逐字取权威**（含 {_L25}为65%），而不是把 value_text 印成 0.65、"
          f"也不是把期间记号与数值连写成 `2025-12-3165%`：{statement!r}")
    check("本栏不自行相除" in statement,
          "取值来源在读者面上写明（「取值来自本节权威自己的指标事实，本栏不自行相除」）："
          "来源在产物里，不只在代码注释里")
    check(f"{_P26} 没有权威产出的资产负债率事实" in statement
          and "资产负债率" in statement,
          f"缺 2026-03-31 的资产负债率 ⇒ 该期落 typed 缺口并逐字说明（实测 {statement!r}）："
          "「取不到」不得被一段看似完整的正文盖过去")
    check("55%" in statement and "45%" in statement,
          "结构与结构比进了同一段读者面正文（流动／非流动占各自总量轴的比重）")

    # 反例 2b：资产负债率那条**指标**事实按路由落在**本节内的另一栏**（`fin_solvency`）——
    # 本节看得到它，因此本栏必须读到它，而不是在权威明明产出过时写下「权威从未产出过」。
    # 真实读数见 `evaluation/results/m930_3_cited_offline_dual_v2_r1/financial/`（`cbsp-1`：
    # 四个期间逐期落假缺口，而 `_m930_3_probe/_out_bs_facts.txt` 里四期都在）。
    section_topics = ("fin_source_scope", _BS_TOPIC, "fin_solvency")
    ratio_map = {spec.fact_id: "fin_solvency" for spec in _facts()
                 if spec.code == "SOLV_DEBT_RATIO"}
    pres_sib, _ = _presentation(topic_ids=section_topics, topic_map=ratio_map)
    sib_statement = {i.aspect_id: i.statement for i in pres_sib.items}[_A_BS]
    check("2023年末为70%" in sib_statement and "2024年末为66.67%" in sib_statement
          and "2025年末为65%" in sib_statement
          and f"{_P26} 没有权威产出的资产负债率事实" in sib_statement,
          "反例 2b：资产负债率事实归**本节内另一栏**（`fin_solvency`）时本栏照样读到它"
          "（三期逐字取到，只有夹具真正没造的那一期落缺口）：只按本 topic 过筛会把这条事实"
          "整批滤掉，读者面上就成了一句「权威从未产出过」的**假陈述**")
    check("70%" in sib_statement and "66.67%" in sib_statement and "65%" in sib_statement,
          "反例 2b-2：读到 ≠ 改口径——取值仍是权威 `display` 的逐字串，"
          "期间仍用 `period_label` 表达，不因换了归属就换一套写法")

    # 反例 2c：归到**本节之外**的指标事实必须仍读不到（「本节看不到」与「本节看得到而不引」
    # 是两件事，不得共用一个措辞），且缺的是**那一个期间**、不是整条路。
    outside_map = dict(ratio_map)
    outside_map[[s.fact_id for s in _facts() if s.code == "SOLV_DEBT_RATIO"][0]] = \
        T.PW.OUTSIDE_SECTION_TOPIC
    pres_out, _ = _presentation(topic_ids=section_topics, topic_map=outside_map)
    out_statement = {i.aspect_id: i.statement for i in pres_out.items}[_A_BS]
    check("2023-12-31" in out_statement and "2023年末为" not in out_statement
          and "2024年末为66.67%" in out_statement and "2025年末为65%" in out_statement,
          "反例 2c：归到**本节之外**的 `SOLV_DEBT_RATIO` 仍进不了本栏——该期按 typed 缺口"
          "逐字说明，同一条事实在**本节内别的栏**时却能读到："
          "「本节之外」与「本节内别的栏」不得共用一条读法")

    # 反例 2a：权威没有 SOLV_DEBT_RATIO ⇒ 不自行相除，只留缺口。
    no_ratio = tuple(s for s in _facts() if s.code != "SOLV_DEBT_RATIO")
    _, authority_no_ratio = _authority(facts=no_ratio)
    pres_no_ratio, _ = _presentation(facts=no_ratio)
    text_no_ratio = {i.aspect_id: i.statement for i in pres_no_ratio.items}[_A_BS]
    check("资产负债率" in text_no_ratio
          and all(p in text_no_ratio for p in (_P23, _P24, _P25, _P26))
          and "没有权威产出的资产负债率事实" in text_no_ratio,
          "反例 2a：权威一条 `SOLV_DEBT_RATIO` 都没有 ⇒ 四个期间**逐期**落 typed 缺口，"
          "且正文里**不出现**任何由「负债 ÷ 资产」算出来的百分比")
    self_divided = ("70%", "66.67%", "65%", "65.45%")
    check("本栏不自行相除" not in text_no_ratio
          and all(x not in text_no_ratio for x in self_divided),
          f"反例 2a-2：正文里没有 {list(self_divided)} 这类**本模块自己算**的资产负债率"
          "（负债 ÷ 资产在四个期间分别得 70%／66.67%／65%／65.45%）："
          "真是权威给的才会出现，而权威一个都没给")

    # ============================================ §3 15% 强筛
    details.append("## §3 15% 强筛：逐条试算（含未命中）、合计行不参与、合并科目不冒充")
    pct_item = {i.aspect_id: i for i in presentation.items}[_A_PCT]
    shares = _readings_of(pct_item, "share")
    hit = {(s["code"], s["period"]) for s in shares if s["selected"]}
    miss = {(s["code"], s["period"]) for s in shares if not s["selected"]}
    check(("INVENTORY", _P25) in hit,
          f"正例：存货 2025 占资产总额 16% ⇒ 命中 15% 筛（实测命中 {sorted(hit)}）")
    check(("CASH_AND_EQUIVALENTS", _P25) in miss and ("GOODWILL", _P25) in miss,
          "未命中的科目**同样在册**：「未列出」不等于「未试算」")
    inv = next(s for s in shares if (s["code"], s["period"]) == ("INVENTORY", _P25))
    check(inv["value_text"] == format(Decimal(160) / Decimal(1000), "f")
          and inv["share_text"] == "16%" and inv["denominator_code"] == "TOTAL_ASSETS"
          and inv["denominator_fact_id"].startswith("item_TOTAL_ASSETS_"),
          f"命中读数带分母轴与分母事实 id（实测 {inv['share_text']} / "
          f"{inv['denominator_code']}）：命中的理由与未命中的理由由同一个算式给出")
    check(inv["threshold_text"] == "15%" and inv["selected"] is True,
          "`selected` 由 `share >= 0.15` 决定，阈值原样记在读数上（不是事后补一个标记）")
    check(not any(s["code"] in CBS._SUMMARY_CODES for s in shares),
          "合计行**不**出现在试算结果里：它们的分母是它们自己，任何比例都是 100%")
    gap_keys = {(g.get("code"), g.get("period"), g.get("reason")) for g in pct_item.gaps}
    check(("TOTAL_ASSETS", _P25, "no_authority_field_for_required_item") in gap_keys
          and ("TOTAL_LIABILITIES_AND_EQUITY", _P23,
               "no_authority_field_for_required_item") in gap_keys,
          "合计行**逐条**落 `no_authority_field_for_required_item`："
          "「本栏不替它挑一个分母」这句话是有记录的，不是没提")
    check(("ACCOUNTS_RECEIVABLE", _P25, "no_registered_fact_for_item") in gap_keys
          and ("FIXED_ASSETS", _P25, "no_registered_fact_for_item") in gap_keys,
          "反例 3a：快照里没有的独立科目**逐条**落 `no_registered_fact_for_item`——"
          "**不**拿任何合并口径的科目顶替独立科目")
    check(all(s["code"] != "TOTAL_ASSETS" for s in shares)
          and all("应收账款" not in (s["label"] or "") for s in shares),
          "反例 3a-2：试算结果里既没有合计行充当被筛科目，也没有把合并科目改名后混进来")
    check(all(s["share_text"].endswith("%") for s in shares)
          and all(Decimal(s["value_text"]) >= 0 for s in shares),
          "每一条试算都是比值（Decimal），不是拼出来的字符串")
    check(f"全部期间合计 {len(shares)} 条" in pct_item.statement
          and f"{_L25}，" in pct_item.statement
          and f"{_L24}没有任何已登记科目" in pct_item.statement,
          f"读者面**逐期**陈述 15% 筛选（2025 年末有命中、2024 年末无命中，各成一句），"
          "并写明全部期间的试算总条数：「筛选逐条施加、逐条记录」是这一栏对 "
          "`complete_set_rule` 的回答方式，而 `time_scope=THREE_YEARS_PLUS_LATEST` "
          "要求这句话覆盖**每一个期间**，不是只讲本期末")

    # ============================================ §4 20% 变动与期间措辞
    details.append("## §4 重大科目变化：20% 是展示筛，「较上年末」与「同比」由年月决定")
    chg_item = {i.aspect_id: i for i in presentation.items}[_A_CHG]
    changes = _readings_of(chg_item, "change")
    by_cp = {(c["code"], c["current_period"]): c for c in changes}
    stb = by_cp.get(("SHORT_TERM_BORROWINGS", _P26))
    check(stb is not None and stb["comparison_label"] == f"较上年末（{_L25}）"
          and stb["is_year_on_year"] is False and stb["direction"] == "上升"
          and stb["change_text"] == "50%" and stb["selected"] is True,
          f"正例：短期借款 2026-03-31 较 2025 年末 +50%，标为**较上年末**且 "
          f"`is_year_on_year=False`（实测 {stb and stb['comparison_label']!r}）："
          "季度末对上年末**不是**同比")
    inv_chg = by_cp.get(("INVENTORY", _P25))
    check(inv_chg is not None and inv_chg["comparison_label"] == f"同比（{_L24}）"
          and inv_chg["is_year_on_year"] is True and inv_chg["change_text"] == "60%"
          and inv_chg["selected"] is True,
          f"正例：存货 2024→2025 是两个相邻年末，+60%，标为**同比**（实测 "
          f"{inv_chg and inv_chg['comparison_label']!r}）：只有这一对才写「同比」")
    check(all(c["threshold_text"] == "20%" for c in changes),
          "20% 逐条记录在读数上；它是**展示**筛，不冒充 Contract 判据")
    check(any(not c["selected"] for c in changes),
          "未达 20% 的相邻期间同样在册（试算不等于命中，命中不等于重要）")
    check(any(g.get("reason") == "no_authority_field_for_required_item"
              and "变化原因" in g.get("detail", "") for g in chg_item.gaps),
          "反例 4a：Contract 的 `required_fields` 要「变化原因」而权威没有承载字段 ⇒ "
          "**整栏**落一条 typed 缺口，不用模型推断或常识补足")
    check(len(chg_item.readings) == len(changes) and "20%" in chg_item.statement
          and "较上年末" in chg_item.statement and "同比" in chg_item.statement,
          "读者面把两根阈值轴与期间措辞规则都写清（含「20% 是本批的展示筛选」）")
    check(Decimal(stb["value_text"]) == Decimal("0.5")
          and Decimal(inv_chg["value_text"]) == Decimal("0.6"),
          "变动幅度用 Decimal 在**未舍入原值**上算（0.5 / 0.6），渲染才舍入")
    check(all(str(c.get("formula") or "") for c in changes),
          "每一条变动读数都带**逐条算式**（前一条事实 → 后一条事实）：`ratio`／`share` 两支"
          "早有这一项，`change` 支曾经漏掉——表现是读者面表格的「算式/事实」列**整列空白**，"
          "既不能核对「跟哪一期比」，也不能核对「比的是哪两条事实」")
    check(all(c["formula"] == f"{c['prior_fact_id']} → {c['current_fact_id']}"
              for c in changes if c.get("prior_fact_id") and c.get("current_fact_id")),
          "算式写的是**事实 id**（不是科目名或期间），读者能据它回查那两条权威事实")
    check(all(c["change_text"] and c["value_text"] for c in changes),
          "算式的补齐没有替换掉数值本身：逐条读数仍是数值 + 算式两件都在")

    # ============================================ §5 边界注记与读者面
    details.append("## §5 边界注记：应收案例单独裁决、已有偿债表不复制")
    notes = {n.note_id: n for n in presentation.notes}
    ar = notes.get("accounts_receivable_case")
    check(ar is not None and ar.state == "gap"
          and ar.reason == "requires_separate_adjudication",
          f"应收案例记成 `requires_separate_adjudication`（实测 {ar and ar.reason!r}）："
          "它要先把附注抽取的资格路径审计清楚，触及冻结权威口径——"
          "在裁决之前产出的每一个数都会是一条绕过资格决定的数字")
    check(ar is not None and "ACCOUNTS_RECEIVABLE" in ar.detail
          and "账面余额" in ar.detail and "坏账准备" in ar.detail,
          "注记写明本栏**不**给出账面余额／坏账准备／账面价值，并点出快照缺哪两个 code")
    sv = notes.get("existing_solvency_table")
    check(sv is not None and sv.state == "determined"
          and "cited_financial_table" in sv.detail,
          "已有偿债指标表只以注记指向 `cited_financial_table`，**不**在本栏复制："
          "两栏回答的是不同的问题，栏目数各自独立")

    md = CBS.render_cited_balance_structure_markdown(presentation)
    check("本栏**不经过模型**" in md and presentation.topic_id in md
          and all(i.requirement_text in md for i in presentation.items),
          "读者面写明「本栏不经过模型」，并逐条给出 Contract 原话与逐栏状态")
    check("命中筛选" in md and "未命中筛选" in md,
          "逐条读数表把命中与未命中分列（筛选结果与试算记录是两件事）")
    check(all(c["formula"] in md for c in changes),
          "读者面表格的「算式 / 事实」列**逐行有值**（每一条变动读数都能看到"
          "它是由哪两条事实算出来的）：空列会让这一栏的逐条可回查性只剩一半")
    change_rows = [ln for ln in md.splitlines() if ln.startswith("| `change` |")]
    check(len(change_rows) == len(changes)
          and all(ln.rstrip().endswith("|") and "|  |" not in ln for ln in change_rows),
          f"变动读数在表里逐行成行（{len(change_rows)} 行 = 读数 {len(changes)} 条），"
          "且没有整列为空的格子")
    check("流动比率" not in md and "利息保障倍数" not in md,
          "反例 5a：本栏**不**复制 `fin_solvency` 的偿债指标表充数——"
          "那一栏回答偿债指标值，本栏回答资产负债的结构与变动")
    check("按阅读次序" in md and "按冻结 Contract 的 aspect 次序" in md,
          "阅读次序轴与 Contract 栏目次序轴在读者面上**分开**写明"
          "（并成一句会让「覆盖核过了」看起来像「读起来成篇了」）")

    # ============================================ §6 构造期 fail-closed
    details.append("## §6 构造期 fail-closed：没登记的读法、错身份、越界状态一律拒绝")
    _expect_error(lambda: CBS.build_cited_balance_structure(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC,
        requirement=T._Req(_BS_TOPIC, (
            T._aspect("fin_balance_structure.unregistered", _BS_TOPIC, "q-x",
                      requirement_text="未登记的要求", complete_set_rule=_FIFTEEN_RULE),)),
        authority=authority), CBS.CitedBalanceStructureError, token="没有登记读法")
    note("反例 6a：Contract 出现一条路由表没登记的要求 ⇒ 构造期停住"
         "（宁可不跑，也不许少呈现一条已要求的内容）。")
    _expect_error(lambda: CBS.build_cited_balance_structure(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC,
        requirement=T._Req(_BS_TOPIC, tuple(
            T._aspect(a, _BS_TOPIC, f"q-{a}", requirement_text=t)
            for a, _tier, t, _r in _BS_ASPECTS)),
        authority=authority), CBS.CitedBalanceStructureError, token="15%")
    note("反例 6b：Contract 的 `complete_set_rule` 里没有 15% ⇒ 构造期停住"
         "（阈值漂移不得表现为「读者面上换了个数」）。")
    _expect_error(lambda: CBS.build_cited_balance_structure(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC,
        requirement=T._Req(_BS_TOPIC, ()), authority=authority),
        CBS.CitedBalanceStructureError, token="没有任何 aspect")
    _expect_error(lambda: CBS.CitedBalanceStructurePresentation(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC,
        schema_version=CBS.CITED_BALANCE_STRUCTURE_SCHEMA_VERSION,
        policy_version=CBS.CITED_BALANCE_STRUCTURE_POLICY_VERSION,
        routing_version=CBS.BALANCE_STRUCTURE_ROUTING_VERSION,
        producer_kind="model_written", contract_threshold_rule=_FIFTEEN_RULE,
        contract_topic_aspects=(), items=()),
        CBS.CitedBalanceStructureError, token="产出者身份")
    note("反例 6c：把产出者标成「模型写的」⇒ 构造期拒绝"
         "（这一栏的读数只能来自确定性呈现）。")
    _expect_error(lambda: CBS.CitedBalanceStructurePresentation(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC,
        schema_version=CBS.CITED_BALANCE_STRUCTURE_SCHEMA_VERSION,
        policy_version=CBS.CITED_BALANCE_STRUCTURE_POLICY_VERSION,
        routing_version=CBS.BALANCE_STRUCTURE_ROUTING_VERSION,
        producer_kind=CBS.BALANCE_STRUCTURE_PRODUCER_KIND,
        contract_threshold_rule=_FIFTEEN_RULE, contract_topic_aspects=(), items=(),
        model_calls_issued=1), CBS.CitedBalanceStructureError, token="不得发起模型调用")
    _expect_error(lambda: CBS.CitedBalanceStructurePresentation(
        section_id=_FIN_SECTION, topic_id=_BS_TOPIC,
        schema_version=CBS.CITED_BALANCE_STRUCTURE_SCHEMA_VERSION,
        policy_version=CBS.CITED_BALANCE_STRUCTURE_POLICY_VERSION,
        routing_version=CBS.BALANCE_STRUCTURE_ROUTING_VERSION,
        producer_kind=CBS.BALANCE_STRUCTURE_PRODUCER_KIND,
        contract_threshold_rule=_FIFTEEN_RULE, contract_topic_aspects=(_A_BS,), items=()),
        CBS.CitedBalanceStructureError, token="没有条目")
    note("反例 6d：Contract 要求的栏目在呈现里没有条目 ⇒ 构造期拒绝"
         "（留白会让「没问」看起来像「问过了」）。")
    _expect_error(lambda: CBS.BalanceStructureAspectItem(
        aspect_id="x", requirement_text="x", label="x", state="unregistered",
        statement="x"), CBS.CitedBalanceStructureError, token="未登记的条目状态")
    _expect_error(lambda: CBS.BalanceStructureAspectItem(
        aspect_id="x", requirement_text="x", label="x", state="determined",
        statement=""), CBS.CitedBalanceStructureError, token="必须有一段实际呈现")
    _expect_error(lambda: CBS.BalanceStructureAspectItem(
        aspect_id="x", requirement_text="x", label="x", state="gap",
        statement="这一项已经写出来了。", reason="no_registered_fact_for_item"),
        CBS.CitedBalanceStructureError, token="不得带呈现段")
    note("反例 6e：`determined` 不带呈现段、`gap` 带呈现段 ⇒ 两条都在构造期被拒"
         "（两种状态各有一条不许越界的边）。")
    _expect_error(lambda: CBS.BalanceStructureAspectItem(
        aspect_id="x", requirement_text="x", label="x", state="gap", statement="",
        reason="looks_fine_to_me"),
        CBS.CitedBalanceStructureError, token="未登记的缺口原因")
    note("反例 6f：缺口原因必须是封闭集合里的一个（自由文本会把「取不到」写成判断）。")

    # ============================================ §7 运行脚本的登记与派发
    details.append("## §7 运行脚本：登记表派发、不占写作名额、未登记即 fail-closed")
    check(CHAIN.DETERMINISTIC_TOPIC_PRESENTERS.get(_BS_TOPIC) == "cited_balance_structure",
          f"`{_BS_TOPIC}` 在 `DETERMINISTIC_TOPIC_PRESENTERS` 里登记为 "
          f"`cited_balance_structure`（实测 {CHAIN.DETERMINISTIC_TOPIC_PRESENTERS.get(_BS_TOPIC)!r}）")
    check(CHAIN._deterministic_presenter_name(topic_id=_BS_TOPIC, section_id=_FIN_SECTION)
          == "cited_balance_structure",
          "名字解析走**同一张**登记表（不是第二处硬编码）")
    cited, deterministic = CHAIN._split_cited_and_deterministic(
        section_id=_FIN_SECTION,
        topic_ids=("fin_source_scope", _BS_TOPIC, "fin_solvency"))
    check(cited == ("fin_solvency",)
          and deterministic == ("fin_source_scope", _BS_TOPIC),
          f"三个财务 topic 各有去处：写作支 {list(cited)}、确定性呈现支 {list(deterministic)}"
          "——`cited` 仍只有一个，**一节一个 Writer topic** 的上限没有被放宽")
    check(CHAIN.MAX_CITED_TOPICS_PER_SECTION == 1,
          "上限常量本身没有被动过")
    _expect_error(lambda: CHAIN._deterministic_presenter_name(
        topic_id="fin_not_registered", section_id=_FIN_SECTION),
        SystemExit, token="没有任何登记的产出者")
    note("反例 7a：一个没登记的 topic 被归入确定性呈现 ⇒ 解析期 `SystemExit`"
         "（不知道用哪套读法就必须停住，不得跳过这一栏）。")
    check(len(CHAIN._DETERMINISTIC_PRESENTERS) == len(CHAIN._DETERMINISTIC_RENDERERS)
          == len({v for v in CHAIN.DETERMINISTIC_TOPIC_PRESENTERS.values()}),
          "构造表、渲染表与 topic 登记表的**取值集合**三者一一对应："
          "只登记一半的名字会在运行期才炸，而不是在导入期")

    note("本模块证明的只有**确定性呈现的取值规则、阈值出处与 fail-closed 边界**。"
         "它不产生正式产物、不宣称财务内容门通过、不宣称 M930-3／TS5 或任何正式阶段关闭；"
         "业务内容由离线读回与真实 run 后的人工读回判定。"
         "整个模块**没有发出任何模型请求**，也没有写库。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

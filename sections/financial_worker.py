"""Phase 4 Batch B — 财务章节 Worker（Workflow，非 Harness）。

确定性流程（任务书 §10）：
    校验 Snapshot 权威（current/valid/非 blocked/非 quarantine/维度一致）
    → 选期间 → 读 SnapshotItem/Exception → metrics.compute_all(persist=False)
    → 构造 FinancialFactPack（fact=科目金额 / calculation=指标，稳定 fact_id + 精确 display + CitationRef）
    → LLM 仅解读（数字一律 [[fact_id]] 占位，禁止手写数字）
    → 解析 + 占位替换 + 确定性复核（裸数字 fail-closed）+ 派生 Claim/Citation/Unresolved
    → Contract 覆盖复核（必答 topic/question 必须被有效 claim 覆盖或转为 SectionUnresolved）
    → 渲染 Markdown → 结构校验 → （可选）原子 commit + current_section 切换。

硬约束：
- 所有金额/比率/增长/趋势来自 financial_v2 Python（SnapshotItem.amount 已归一化为元，
  指标来自 MetricResult.display_value）；LLM 只解读，绝不计算。
- 引用一律 structured CitationRef，可溯源 snapshot_id/period/item_code/formula_id/formula_version。
- 缺失输入 → SectionUnresolved（诚实状态），绝不写「没有/不存在」，绝不编数字。
- 快照非 current / 有效性非 valid / report_blocked / quarantined / 维度不一致 / 与任务锁定
  不一致 → fail-closed 抛错（不产出章节、不切换 current）。
- 依赖指纹（snapshot + required_formula_versions + 契约/任务依赖 + prompt/renderer/rules/worker
  版本）进入 section_version 派生；同一输入幂等复用，任何依赖变化 → 新版本。

CLI:
    python -m sections.financial_worker --task <task.json> --snapshot <id> \
        --company <stock> [--company-name <name>] --fin-db <fin.db> --section-db <sections.db> \
        [--validate-only] [--store] [--out <markdown>]
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from financial_v2 import derived_facts as fderived
from financial_v2 import formulas as fformulas
from financial_v2 import metrics as fmetrics
from financial_v2 import period_basis as fpb
from financial_v2 import schema as FS
from financial_v2 import snapshots as fsnapshots
from financial_v2 import store as fstore
from harness.schema import CitationRef
from llm import client as llm
from planning import schema as PS
from sections import common as SC
from sections import financial_pack_artifact as FPA
from sections import schema as SS
from sections import validator as svalidator

logger = logging.getLogger("sections.financial_worker")


class FinancialWorkerError(RuntimeError):
    """财务 Worker fail-closed 错误（不产出章节，不切换 current）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# 结构/规模关键科目（除公式输入外，额外纳入「财务规模与结构」）。
_STRUCTURE_ITEMS = frozenset({
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY", "TOTAL_LIABILITIES_AND_EQUITY",
    "CURRENT_ASSETS", "NON_CURRENT_ASSETS", "CURRENT_LIABILITIES", "NON_CURRENT_LIABILITIES",
    "TOTAL_REVENUE", "OPERATING_REVENUE", "NET_PROFIT", "NET_PROFIT_PARENT",
    "OPERATING_CASH_FLOW", "INVESTING_CASH_FLOW", "FINANCING_CASH_FLOW",
    "NET_CASH_INCREASE", "CASH_AND_EQUIVALENTS", "INVENTORY", "ACCOUNTS_RECEIVABLE",
    "FIXED_ASSETS", "GOODWILL", "INTANGIBLE_ASSETS", "SHORT_TERM_BORROWINGS",
    "LONG_TERM_BORROWINGS", "BONDS_PAYABLE", "TOTAL_PROFIT",
})

# 三大主表 statement_type（fin_statements_availability 确定性覆盖用）。
_STATEMENT_TYPES = ("balance_sheet", "income_statement", "cash_flow")

# topic_id → 中文标题（renderer 用）。
_TOPIC_LABELS: dict[str, str] = {
    "fin_source_scope": "数据来源与口径",
    "fin_consistency": "财务数据一致性",
    "fin_balance_structure": "财务规模与结构",
    "fin_solvency": "偿债能力",
    "fin_profitability": "盈利能力",
    "fin_operating": "营运能力",
    "fin_cashflow": "现金流",
    "fin_growth": "成长能力",
    "fin_asset_quality": "资产质量",
    "fin_risk_summary": "主要变化与风险提示",
    "fin_working_capital_needs": "流动资金需求",
    "fin_trade_finance_focus": "贸易融资关注",
    "fin_capital_capacity": "资本承受能力",
}

# 契约 calc_* 计算要求 → formula_id（确定性；calc_working_capital_needs 无注册公式）。
_CALC_REQUIREMENT_FORMULAS: dict[str, tuple[str, ...]] = {
    "calc_current_ratio": ("SOLV_CURRENT_RATIO",),
    "calc_quick_ratio": ("SOLV_QUICK_RATIO",),
    "calc_debt_ratio": ("SOLV_DEBT_RATIO",),
    "calc_interest_coverage": ("SOLV_INTEREST_COVER",),
    "calc_ebitda_interest_coverage": ("EBITDA", "SOLV_INTEREST_COVER"),
    "calc_gross_margin": ("PROF_GROSS_MARGIN",),
    "calc_net_margin": ("PROF_NET_MARGIN",),
    "calc_roe": ("PROF_ROE",),
    "calc_roa": ("PROF_ROA",),
    "calc_receivable_turnover": ("OPER_AR_TURNOVER",),
    "calc_inventory_turnover": ("OPER_INV_TURNOVER",),
    "calc_asset_turnover": ("OPER_ASSET_TURNOVER",),
    "calc_ocf_to_net_profit": ("CASH_OCF_TO_NP",),
    "calc_fcf": ("FREE_CASH_FLOW",),
    "calc_revenue_growth": ("GROWTH_REVENUE",),
    "calc_net_profit_growth": ("GROWTH_NET_PROFIT",),
    "calc_dupont": ("PROF_ROE", "PROF_NET_MARGIN", "OPER_ASSET_TURNOVER", "SOLV_EQUITY_MULT"),
    "calc_working_capital_needs": (),
}

# formula_id → topic_id（精确映射，不再用前缀推断）。impact_scope 由所属 question 决定。
_FORMULA_TO_TOPIC: dict[str, str] = {
    "SOLV_CURRENT_RATIO": "fin_solvency",
    "SOLV_QUICK_RATIO": "fin_solvency",
    "SOLV_DEBT_RATIO": "fin_solvency",
    "SOLV_INTEREST_COVER": "fin_solvency",
    "SOLV_EQUITY_MULT": "fin_solvency",
    "PROF_GROSS_MARGIN": "fin_profitability",
    "PROF_NET_MARGIN": "fin_profitability",
    "PROF_ROE": "fin_profitability",
    "PROF_ROA": "fin_profitability",
    "PROF_OPER_MARGIN": "fin_profitability",
    "OPER_ASSET_TURNOVER": "fin_operating",
    "OPER_INV_TURNOVER": "fin_operating",
    "OPER_AR_TURNOVER": "fin_operating",
    "CASH_OCF_TO_NP": "fin_cashflow",
    "CASH_OCF_TO_ASSET": "fin_cashflow",
    "CASH_OCF_TO_REV": "fin_cashflow",
    "EXP_PERIOD_RATE": "fin_profitability",
    "GROWTH_REVENUE": "fin_growth",
    "GROWTH_NET_PROFIT": "fin_growth",
    "GROWTH_ASSET": "fin_growth",
    "GROWTH_LIABILITY": "fin_growth",
    "GROWTH_EQUITY": "fin_growth",
    "GROWTH_OCF": "fin_growth",
    "GROWTH_ICF": "fin_growth",
    "GROWTH_FINANCING_CASH_FLOW": "fin_growth",
    "EBITDA": "fin_solvency",
    "INTEREST_BEARING_DEBT": "fin_solvency",
    "FREE_CASH_FLOW": "fin_cashflow",
    # 派生展示事实（`financial_v2.derived_facts`，`FORMULA_REVIEW` §5.1）的归属：它与输入指标
    # 同主题（资产负债率的变动就是偿债能力这一主题下的东西），不是新主题。它**不在**冻结公式
    # 注册表里，因此上面第 2 步的指标循环取不到它（`registry` 未命中 → 不产生任何缺口记录），
    # 它由第 3 步单独产出；本字典在此只承担「它属于本节哪个 topic」这一件事。
    fderived.DELTA_FACT_CODE: "fin_solvency",
}

#: `_STRUCTURE_ITEMS` 里**属于资产负债表**的那一部分。科目金额事实（`kind="fact"`）的 `code`
#: 是 `standard_item_code`（不是 formula_id），因此**不在** `_FORMULA_TO_TOPIC` 上；这张表是
#: 它们的唯一归属来源。
#:
#: 这份子集不是按名字猜的，也不是从某一家的报表反推的：
#: `_m930_3_probe/balance_item_statement_probe.py` 以**只读**方式读真实财务库的
#: `snapshot_item.standard_item_code × statement_type`，逐条核对得到——
#: `_STRUCTURE_ITEMS` 与 `balance_sheet` 的交集恰为下面 15 条，且没有任何一条同时出现在两个
#: `statement_type` 下；余下两条（`ACCOUNTS_RECEIVABLE`、`FIXED_ASSETS`）在该快照里没有条目，
#: 但 `financial/analyzer.py` 的平衡表科目表同样把它们列为资产负债表科目（应收账款、固定资产），
#: 故此显式列入——**列入的理由是它们在报表上的位置，不是它们在这份快照里出现过**。
#:
#: 利润表 / 现金流量表科目（营业收入、净利润、经营现金流、……）**不在**这张表上：它们属于本阶段
#: 未选中的 topic，按 `pack_writer.OUTSIDE_SECTION_TOPIC` 如实排除，**不得**塞进资产负债结构
#: 充数——把「营业收入」写进「资产结构」正是这条链要防的那类错栏。
_BALANCE_SHEET_ITEMS: frozenset[str] = frozenset({
    "TOTAL_ASSETS", "CURRENT_ASSETS", "NON_CURRENT_ASSETS",
    "TOTAL_LIABILITIES", "CURRENT_LIABILITIES", "NON_CURRENT_LIABILITIES",
    "TOTAL_EQUITY", "TOTAL_LIABILITIES_AND_EQUITY",
    "CASH_AND_EQUIVALENTS", "INVENTORY", "ACCOUNTS_RECEIVABLE", "FIXED_ASSETS",
    "GOODWILL", "INTANGIBLE_ASSETS",
    "SHORT_TERM_BORROWINGS", "LONG_TERM_BORROWINGS", "BONDS_PAYABLE",
})

#: `standard_item_code` → topic_id（**精确映射**，与 `_FORMULA_TO_TOPIC` 并列的第二张表）。
#:
#: 与 `_FORMULA_TO_TOPIC` 分开写而不是合并，是因为两者读的是事实的**不同字段**：指标事实的
#: `code` 是 formula_id，科目事实的 `code` 是 `standard_item_code`。合成一张表会把这个区别藏
#: 起来，日后某条 formula_id 与某条 item code 重名时，冲突只会表现为一条静默的错归属。
_STRUCTURE_ITEM_TO_TOPIC: dict[str, str] = {
    code: "fin_balance_structure" for code in sorted(_BALANCE_SHEET_ITEMS)}


def structure_item_topic(item_code: str) -> str | None:
    """科目 `code` → topic 的**唯一**读入口（未知 code 返回 `None`，调用方负责如实排除）。"""
    return _STRUCTURE_ITEM_TO_TOPIC.get(str(item_code or "").strip())


# missing_policy → QUESTION_STATES（覆盖缺口诚实状态）。
_MISSING_POLICY_STATE: dict[str, str] = {
    "write_not_found": "NOT_FOUND_AFTER_SEARCH",
    "write_not_found_disclose": "NOT_FOUND_AFTER_SEARCH",
    "missing_material": "NOT_PROVIDED",
    "conflict_pause": "CONFLICT",
    "transfer_human": "WAITING_HUMAN",
    "not_applicable_no_plan": "NOT_APPLICABLE",
    "valid_no_controller": "SATISFIED",
    "proxy_allowed": "NOT_PROVIDED",
}

_AVAILABLE_METRIC_STATUSES = frozenset({"CALCULATED_EXACT", "CALCULATED_PROXY"})


# ---------------------------------------------------------------------------
# 财务事实包
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinancialFact:
    fact_id: str
    kind: str                    # fact（科目金额）| calculation（指标）
    label: str
    code: str                    # standard_item_code 或 formula_id
    period: str                  # 权威期间记号（快照自己的 `YYYY-MM-DD`，身份用，不改写）
    value: Decimal | None        # 权威值（item=元；metric=display_value）
    display: str
    unit: str
    status: str
    reason_code: str | None
    note: str
    citation: CitationRef
    #: 期间口径（`end` 时点 / `flow` 期间）。它是**权威命题的一部分**：同一串数字在两种口径下
    #: 说的不是同一件事。来源只有两处——快照条目的 `statement_type`（科目）与公式定义的
    #: `period_requirement`（指标），两者都是权威自己的字段。
    period_basis: str = ""
    #: 该事实的**期间表达**（读者可读，如 `2025年度` / `2025年末`）。它与 `period` 并存而不是
    #: 取代它：`period` 是身份（引用、分组、列对齐都用它），`period_label` 是给读者看的说法。
    period_label: str = ""
    #: 产出该事实的**公式版本**（指标事实来自权威结果自己的 `formula_version`；派生事实来自
    #: `financial_v2.derived_facts.DELTA_FACT_FORMULA_VERSION`）。空串表示该事实不由公式产出。
    formula_version: str = ""
    #: **完整**的权威引用集：只有当一条事实的数值是**多条**权威记录的确定性函数时才有值
    #: （`Δpp` 派生事实 = 两期输入指标结果）。它必须包含 `citation` 本身，且每条都能回查。
    #: 空元组表示「就是 `citation` 那一条」——不是「没有引用」。
    citations: tuple[CitationRef, ...] = ()
    #: 派生事实的**计算输入**血缘：两期输入事实在本 FactPack 里的 id（`derived_from`）、
    #: 两期的权威期间记号（`input_periods`）、两期**未舍入**原值的规范字符串
    #: （`input_raw_texts`）。三者都是血缘，**不进**数字授权面（`authority_numeric_texts`）：
    #: 读者可见的数值只能是 `display`，血缘用来回读「这个差额是怎么算出来的」。
    derived_from: tuple[str, ...] = ()
    input_periods: tuple[str, ...] = ()
    input_raw_texts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.citations:
            if len(self.citations) < 2:
                raise FinancialWorkerError(
                    f"fact {self.fact_id!r} 的 citations 只有 {len(self.citations)} 条："
                    "多引用集只用于「数值是多条权威记录的函数」的派生事实，"
                    "单引用事实必须留空（空 = 就是 citation 那一条）")
            if self.citation not in self.citations:
                raise FinancialWorkerError(
                    f"fact {self.fact_id!r} 的 citation 不在它自己的 citations 集内："
                    "主引用必须是被声明的完整引用集的一员，不得另立一条")
        if self.derived_from or self.input_periods or self.input_raw_texts:
            if not (len(self.derived_from) == len(self.input_periods) == len(self.input_raw_texts)):
                raise FinancialWorkerError(
                    f"fact {self.fact_id!r} 的派生血缘长度不一致："
                    f"derived_from={len(self.derived_from)} "
                    f"input_periods={len(self.input_periods)} "
                    f"input_raw_texts={len(self.input_raw_texts)}")
            # 本批只批准**一阶、两期**的相邻年度变动（`financial_v2.derived_facts` 那一侧
            # 才是这条规则的owner）；这里只做线上见证：两期、且 `period` 正是这两期的复合记号。
            if len(self.input_periods) != 2:
                raise FinancialWorkerError(
                    f"fact {self.fact_id!r} 声明了 {len(self.input_periods)} 期输入血缘："
                    "本批只批准两期输入，多期血缘不得冒充")
            if self.period != f"{self.input_periods[1]}|{self.input_periods[0]}":
                raise FinancialWorkerError(
                    f"fact {self.fact_id!r} 声明了双期血缘，但它的 period={self.period!r} "
                    "不是这两期的复合记号（本期|上期）：单期间字段不得冒充双期间")


@dataclass(frozen=True)
class FinancialFactPack:
    snapshot_id: str
    company_id: str
    company_name: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    periods: list[str]
    facts: tuple[FinancialFact, ...]
    gaps: tuple[dict, ...]           # required 指标缺口（→ SectionUnresolved）
    diagnostic_gaps: tuple[dict, ...]  # relevant 但非 required 指标缺口（仅诊断，不影响状态）
    statements_available: tuple[str, ...]  # 主表 statement_type 齐备情况
    period_note: dict = field(default_factory=dict)  # 年度主线覆盖范围说明（不足三年诚实声明）
    by_id: dict[str, FinancialFact] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerResult:
    section_result: SS.SectionResult
    fact_count: int
    claim_count: int
    unresolved_count: int
    reused: bool
    committed: bool
    verification: dict


# ---------------------------------------------------------------------------
# Snapshot 权威校验（fail-closed）
# ---------------------------------------------------------------------------

def _resolve_snapshot(snapshot_id: str | None, *, company_id: str, scope: str,
                      currency: str, as_of_date: str | None, purpose: str,
                      fin_db: str, task_locked_id: str = "") -> FS.FinancialSnapshot:
    if not Path(fin_db).exists():
        raise FileNotFoundError(f"financial_v2 库不存在: {fin_db}")
    fstore.init_db(fin_db)

    if snapshot_id and task_locked_id and snapshot_id != task_locked_id:
        raise FinancialWorkerError(
            f"快照与任务锁定不一致: {snapshot_id} != {task_locked_id}（fail-closed）")

    sid = snapshot_id or task_locked_id or None
    if sid:
        snap = fstore.get_snapshot(sid)
        if snap is None:
            raise FinancialWorkerError(f"snapshot 不存在: {sid}")
        if snap.company_id != company_id:
            raise FinancialWorkerError(
                f"snapshot 公司归属不符: {snap.company_id} != {company_id}")
    else:
        if not as_of_date:
            raise FinancialWorkerError("未提供 snapshot 且无 as_of_date，无法解析 current 快照")
        snap = fsnapshots.current_snapshot(company_id, scope, currency, as_of_date, purpose)
        if snap is None:
            raise FinancialWorkerError("无可用 current 财务快照（健康门 fail-closed）")
        sid = snap.snapshot_id

    # 权威性复合校验（req 1）：任一失败 fail-closed，不产出、不切 current。
    # 1) 运行维度严格一致。
    if snap.scope != scope:
        raise FinancialWorkerError(f"快照 scope 不符: {snap.scope!r} != {scope!r}（fail-closed）")
    if snap.currency != currency:
        raise FinancialWorkerError(
            f"快照 currency 不符: {snap.currency!r} != {currency!r}（fail-closed）")
    if snap.purpose != purpose:
        raise FinancialWorkerError(
            f"快照 purpose 不符: {snap.purpose!r} != {purpose!r}（fail-closed）")
    if as_of_date and snap.as_of_date != as_of_date:
        raise FinancialWorkerError(
            f"快照 as_of_date 不符: {snap.as_of_date!r} != {as_of_date!r}（fail-closed）")
    # 2) 报告阻断。
    if snap.report_blocked:
        raise FinancialWorkerError(f"快照 report_blocked，禁止生成财务章节: {sid}")
    # 3) 有效性必须精确等于 valid（拒绝 None/stale/superseded/unknown）。
    validity = fstore.latest_snapshot_validity(sid)
    if validity != "valid":
        raise FinancialWorkerError(
            f"快照有效性非 valid（{validity!r}），禁止作为当前报告输入: {sid}")
    # 4) 未隔离。
    if fstore.is_quarantined("financial_snapshot", sid):
        raise FinancialWorkerError(f"快照已隔离，禁止使用: {sid}")
    # 5) 必须是 (company, scope, currency, as_of_date, purpose) 下的 current 指针。
    cur = fstore.get_current_snapshot(company_id, scope, currency, snap.as_of_date, purpose)
    if cur is None or cur.snapshot_id != sid:
        raise FinancialWorkerError(
            f"快照非 current 指针指向（current={cur.snapshot_id if cur else None!r} != {sid!r}，fail-closed）")
    return snap


# ---------------------------------------------------------------------------
# 事实包构建
# ---------------------------------------------------------------------------

def _item_codes_to_include() -> set[str]:
    codes = set(_STRUCTURE_ITEMS)
    for fd in fformulas.build_registry().values():
        codes.update(fd.input_item_codes)
    return codes


def _select_focus_periods(scoped: list[FS.SnapshotItem], *,
                          as_of_date: str) -> tuple[list[str], dict]:
    """纯函数：从同口径条目计算焦点期间（§12 P1 修复）。

    年度主线采用已取得的近三年（annual 最后三个）；最新季度/中报独立补充（单独列，
    不与年度做同比趋势比较）；主报告期 as_of_date 始终纳入。不足三年 → note 诚实说明
    实际年度范围（绝不编数）。
    """
    periods = sorted({it.report_period for it in scoped})
    if not periods:
        raise FinancialWorkerError("快照无任何条目（无法确定焦点期间）")
    annual = sorted({it.report_period for it in scoped if it.period_type == "annual"})
    sub_annual = sorted({it.report_period for it in scoped
                         if it.period_type in ("quarterly", "interim")})
    main = as_of_date if as_of_date in periods else periods[-1]
    annual_mainline = annual[-3:] if len(annual) >= 3 else annual
    focus = set(annual_mainline) | {main}
    supplement = sub_annual[-1] if sub_annual else None
    if supplement:
        focus.add(supplement)
    note = {
        "annual_periods_available": annual,
        "annual_years": len(annual),
        "full_three_years": len(annual) >= 3,
        "sub_annual_supplement": supplement,
    }
    return sorted(focus), note


def _focus_periods(snapshot: FS.FinancialSnapshot) -> tuple[list[str], dict]:
    items = fstore.list_snapshot_items(snapshot.snapshot_id)
    scoped = [it for it in items
              if it.statement_scope == snapshot.scope and it.currency == snapshot.currency]
    return _select_focus_periods(scoped, as_of_date=snapshot.as_of_date)


def _required_formula_ids(task: PS.SectionTask, snapshot: FS.FinancialSnapshot) -> set[str]:
    """必需公式 = 快照锁定的 required_formula_versions ∪ 任务计算要求映射的公式。"""
    fids = set(snapshot.required_formula_versions.keys())
    for q in task.questions:
        for creq in q.calculation_requirements:
            fids.update(_CALC_REQUIREMENT_FORMULAS.get(creq, ()))
    return fids


def _relevant_formula_ids(task: PS.SectionTask, snapshot: FS.FinancialSnapshot) -> set[str]:
    """相关公式 = 必需公式 ∪ 主题命中的注册公式（用于事实/诊断构建，非必需缺口不影响状态）。"""
    fids = set(_required_formula_ids(task, snapshot))
    topic_ids = set(task.topic_ids)
    for fid, topic in _FORMULA_TO_TOPIC.items():
        if topic in topic_ids:
            fids.add(fid)
    return fids


def _formula_to_question_map(task: PS.SectionTask) -> dict[str, str]:
    """formula_id → question_id（来自任务 calculation_requirements；首个命中优先）。"""
    m: dict[str, str] = {}
    for q in task.questions:
        for creq in q.calculation_requirements:
            for fid in _CALC_REQUIREMENT_FORMULAS.get(creq, ()):
                m.setdefault(fid, q.question_id)
    return m


def _first_question_in_topic(task: PS.SectionTask, topic_id: str) -> str | None:
    for q in task.questions:
        if q.topic_id == topic_id:
            return q.question_id
    return None


def _derived_fact_of(snapshot: FS.FinancialSnapshot, table: Any, period_note: dict,
                     ) -> tuple[FinancialFact | None, dict | None]:
    """Δpp 派生展示事实（`financial_v2.derived_facts`，`FORMULA_REVIEW` §5.1）。

    返回 `(事实, None)` 或 `(None, 类型化缺口)`，两者恰有其一。这里**只做转换**：期间选择、
    两期可比性、原值计算与拒绝原因全部在 `derived_facts` 里（那边是这条规则的唯一实现），
    Worker 不重复判断，也不给它补一个「差不多」的理由。
    """
    registry = fformulas.build_registry()
    source = fderived.DELTA_SOURCE_FORMULA_ID
    # 输入指标的口径来自**权威自己的公式定义**（`period_requirement`），不由名称或科目猜。
    # 取不到（公式不在注册表 / 期间要求为空）时留空，让 `derived_facts` 以**类型化**原因拒绝，
    # 而不是在这里抛一个没有原因码的例外。
    basis = ""
    if source in registry:
        try:
            basis = fpb.basis_from_period_requirement(str(registry[source].period_requirement))
        except ValueError:
            basis = ""
    outcome = fderived.build_delta_fact(
        results=table.results,
        annual_periods=tuple(period_note.get("annual_periods_available") or ()),
        snapshot_id=snapshot.snapshot_id, scope=snapshot.scope, currency=snapshot.currency,
        input_period_basis=basis, source_formula_id=source)
    if isinstance(outcome, fderived.DeltaRefusal):
        return None, outcome.as_gap()

    citations = tuple(CitationRef(**dict(p)) for p in outcome.input_citation_payloads)
    return FinancialFact(
        fact_id=outcome.fact_id, kind=outcome.kind, label=outcome.label, code=outcome.code,
        period=outcome.period, value=outcome.value, display=outcome.display, unit=outcome.unit,
        status=outcome.status, reason_code=outcome.reason_code, note=outcome.note,
        # 主引用 = **本期输入**的权威引用（它是完整引用集的成员，且真实可回查）；完整的两期
        # 引用在 `citations` 里，一起过界、一起进 Claim 的引用链。
        citation=citations[1], citations=citations,
        period_basis=outcome.period_basis, period_label=outcome.period_label,
        formula_version=outcome.formula_version, derived_from=outcome.derived_from,
        input_periods=outcome.input_periods, input_raw_texts=outcome.input_raw_texts), None


def build_fact_pack(snapshot: FS.FinancialSnapshot, *, company_name: str,
                    required_formula_ids: set[str],
                    relevant_formula_ids: set[str]) -> FinancialFactPack:
    """读 SnapshotItem + compute_all(persist=False) → 事实包（无 LLM）。

    - required 指标不可得 → gaps（→ SectionUnresolved）；
    - relevant 但非 required 指标不可得 → diagnostic_gaps（仅诊断，不影响状态）。
    """
    items = fstore.list_snapshot_items(snapshot.snapshot_id)
    periods, period_note = _focus_periods(snapshot)
    item_labels = SC.item_label_map()
    formula_names = SC.formula_name_map()
    include_codes = _item_codes_to_include()

    facts: list[FinancialFact] = []
    gaps: list[dict] = []
    diagnostic_gaps: list[dict] = []

    # 1) 科目金额事实（仅 focus 期间 + 需纳入的科目）。
    statements: set[str] = set()
    for it in items:
        if it.statement_scope != snapshot.scope or it.currency != snapshot.currency:
            continue
        if it.report_period in periods and it.statement_type in _STATEMENT_TYPES:
            statements.add(it.statement_type)
        if it.report_period not in periods:
            continue
        if it.standard_item_code not in include_codes:
            continue
        if it.amount is None:
            continue
        # 口径只由权威自己的 `statement_type` 决定（资产负债表=时点，利润表/现金流量表=期间）；
        # 未登记的类型 fail-closed，不给它默认一个口径。
        basis = fpb.basis_from_statement_type(it.statement_type)
        fid = f"item_{it.standard_item_code}_{it.report_period}"
        facts.append(FinancialFact(
            fact_id=fid, kind="fact",
            label=item_labels.get(it.standard_item_code, it.standard_item_code),
            code=it.standard_item_code, period=it.report_period, value=it.amount,
            display=SC.format_yuan_amount(it.amount), unit="yuan", status="available",
            reason_code=None, note="",
            citation=CitationRef(ref_type="structured", snapshot_id=snapshot.snapshot_id,
                                 item_code=it.standard_item_code, period=it.report_period),
            period_basis=basis,
            period_label=fpb.period_expression(it.report_period, basis)))

    # 2) 指标事实（仅 relevant 公式；required 缺失 → gap，其余缺失 → diagnostic）。
    table = fmetrics.compute_all(snapshot.snapshot_id, periods=periods, persist=False)
    by_key = {(r.formula_id, r.period): r for r in table.results}
    registry = fformulas.build_registry()
    for fid in sorted(relevant_formula_ids):
        name = formula_names.get(fid, fid)
        # 指标口径来自**权威自己的**公式定义（`period_requirement`），不来自名称或科目猜测。
        requirement = str(registry[fid].period_requirement) if fid in registry else ""
        for period in periods:
            mr = by_key.get((fid, period))
            if mr is None:
                continue
            status = mr.status
            fact_id = f"metric_{fid}_{period}"
            if status in _AVAILABLE_METRIC_STATUSES and mr.display_value is not None:
                note = ""
                if status == "CALCULATED_PROXY":
                    note = f"代理口径（{mr.reason_code or 'proxy'}）"
                basis = fpb.basis_from_period_requirement(requirement)
                facts.append(FinancialFact(
                    fact_id=fact_id, kind="calculation", label=name, code=fid,
                    period=period, value=mr.display_value,
                    display=SC.format_metric_display(fid, mr.display_value),
                    unit=fformulas.unit_for(fid), status=status, reason_code=mr.reason_code,
                    note=note,
                    citation=CitationRef(ref_type="structured",
                                         snapshot_id=snapshot.snapshot_id,
                                         formula_id=fid,
                                         formula_version=mr.formula_version, period=period),
                    period_basis=basis, period_label=fpb.period_expression(period, basis)))
            else:
                gap = {"fact_id": fact_id, "formula_id": fid, "label": name,
                       "kind": "calculation", "period": period, "status": status,
                       "reason_code": mr.reason_code}
                if fid in required_formula_ids:
                    gaps.append(gap)
                else:
                    diagnostic_gaps.append(gap)

    # 3) 派生展示事实（`FORMULA_REVIEW` §5.1 定点登记的**唯一**一项，`financial_v2.derived_facts`）：
    #    相邻两个完整年度期末的资产负债率变动 Δpp。它是**正式**财务事实（Path A），与指标事实
    #    同主题、同权威面、同引用链，因此要走同一条 Draft→绑定→评价→Claim→Narrative；它**不是**
    #    「指标 × 期间」表格的一行（期间是复合记号，不是任何单期）。
    #    不生成时留**类型化**原因（→ diagnostic_gaps，不阻断本节状态：它是展示型派生事实，
    #    未取得不等于合同必需内容缺失）；只有真正算出来的才算事实。
    derived, derived_gap = _derived_fact_of(snapshot, table, period_note)
    if derived is not None:
        facts.append(derived)
    if derived_gap is not None:
        diagnostic_gaps.append(derived_gap)

    return FinancialFactPack(
        snapshot_id=snapshot.snapshot_id, company_id=snapshot.company_id,
        company_name=company_name, as_of_date=snapshot.as_of_date,
        scope=snapshot.scope, currency=snapshot.currency, purpose=snapshot.purpose,
        periods=periods, facts=tuple(facts), gaps=tuple(gaps),
        diagnostic_gaps=tuple(diagnostic_gaps),
        statements_available=tuple(sorted(statements)),
        period_note=period_note,
        by_id={f.fact_id: f for f in facts})


def build_fact_pack_for_task(task: PS.SectionTask, *, company_id: str, company_name: str = "",
                             snapshot_id: str | None = None,
                             fin_db: str = "data/financial_v2.db",
                             scope: str = "consolidated", currency: str = "CNY",
                             purpose: str = "credit_analysis",
                             as_of_date: str | None = None) -> FinancialFactPack:
    """最小公共只读接口：为 Rules Evaluator / 返工最终检查重建事实包（不重跑 LLM）。

    复用 ``_resolve_snapshot``（权威性 fail-closed）+ ``_required/_relevant_formula_ids`` +
    ``build_fact_pack``（无 LLM）。供 service 层在评估财务章节时注入 fact_pack（规则 6
    财务数值复核复用 Structured Citation → FinancialFact → display 安全链），不经
    Financial Worker 内部，不重构 Worker（任务书三/实现时必须坚持第 10 条）。
    """
    task_locked = (task.dependency_versions or {}).get("financial_snapshot_id") or ""
    snap = _resolve_snapshot(snapshot_id, company_id=company_id, scope=scope,
                             currency=currency, as_of_date=as_of_date, purpose=purpose,
                             fin_db=fin_db, task_locked_id=task_locked)
    return build_fact_pack(snap, company_name=company_name,
                           required_formula_ids=_required_formula_ids(task, snap),
                           relevant_formula_ids=_relevant_formula_ids(task, snap))


# ---------------------------------------------------------------------------
# M930-2 Backbone worker phase 入口（financial_workflow 路径）
#
# 财务侧不走 topic runtime：产物是只读 `FinancialPackArtifact`（§5.10），把 FactPack +
# 快照权威事实投影成可复核 artifact。入口由财务 Worker 拥有（`sections.service` 不得
# 直接调用 `sections.financial_pack_artifact`）。
# ---------------------------------------------------------------------------

BACKBONE_FINANCIAL_PHASE_VERSION = "m930-2-financial-phase-v1"

#: 本 phase 服务的**section 级**研究政策字面量（DESIGN_V2 §「research_policy: workflow |
#: harness | conditional_harness」）。它**不是** topic 的 `producer_kind`：冻结 Contract v2 里
#: 财务 section 的政策字面量是 `workflow`，而 `financial_workflow` 是 topic/aspect 的生产者
#: 类别。混用会让真实投影的财务 task 永远进不来（本 phase 不可达），故按各自语义分别校验。
SECTION_RESEARCH_POLICY = "workflow"

# `build_fact_pack` 是**本模块自己的**确定性选材算法，其版本号必须由产出方声明，
# 不得由调用方代填（§5.10：artifact 要绑定「确定性选材规则版本」）。
#: `fw-build_fact_pack-v2`（M930-3 返修 P3）：每条事实现在**自己携带**期间口径（`end`/`flow`）
#: 与期间表达（`period_basis.py`，`pb-1`）——同一批快照条目在 v1 与 v2 下产出的 `period_label`
#: 不同（`2025-12-31` vs `2025年末`/`2025年度`），因此不得共用一个版本号。
#: 选材规则版本。`-v3`（M930-3 §二获批的派生展示事实）：`build_fact_pack` 新增第 3 步 ——
#: 相邻完整年度期末的资产负债率变动 Δpp（`financial_v2.derived_facts`，`ddf-1`，
#: `FORMULA_REVIEW` §5.1）。它改变了 FactPack 的**内容**（多一条事实、可能多一条类型化缺口），
#: 因此产出 FactPack 的代码版本必须跟着走：artifact 的 `fact_selection_rule_version` 与
#: 内容指纹都以它为准，旧版 artifact 不会被这一版代码重新算出同一个指纹。
FINANCIAL_FACT_SELECTION_RULE_VERSION = "fw-build_fact_pack-v3"

#: §十 只读副本策略版本（副本路径与临时目录名**绝不进**内容身份）。
FINANCIAL_READONLY_COPY_POLICY_VERSION = "fro-copy-1"

#: §十 观测的真实财务库文件（主库 + WAL/SHM 边车文件）。
FINANCIAL_DB_OBSERVED_FILES = ("", "-wal", "-shm")

#: §十一 附注输入状态的核验规则版本。
EVIDENCE_NOTE_VALIDATION_RULE_VERSION = "evn-validate-1"


class FinancialReadOnlyViolation(FinancialWorkerError):
    """真实财务库在本 phase 调用前后发生了任何变化（fail-closed，绝不静默继续）。"""


def financial_db_state(db_path: str | Path) -> dict:
    """观测真实财务库的文件状态：主库 sha256/size/mtime_ns + WAL/SHM 是否存在及各自 sha256。

    只读操作：`stat` 与 `read_bytes` 都不改变被观测文件。
    """
    path = Path(db_path)
    state: dict = {}
    for suffix in FINANCIAL_DB_OBSERVED_FILES:
        target = Path(str(path) + suffix)
        key = suffix.lstrip("-") or "main"
        if not target.exists():
            state[key] = None
            continue
        stat = target.stat()
        state[key] = {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return state


def financial_db_drift(before: dict, after: dict) -> tuple[str, ...]:
    """逐项比对两组观测；返回发生变化的项名（空 = 逐项不变）。"""
    keys = sorted(set(before) | set(after))
    return tuple(k for k in keys if before.get(k) != after.get(k))


@contextlib.contextmanager
def readonly_financial_copy(fin_db: str | Path):
    """把真实财务库复制到临时目录，**只在副本上**运行会 init/迁移的旧构建逻辑。

    生产入口必须走这里：`financial_v2.store.init_db()` 会对传入路径建表/迁移，直接对真实库
    调用就是"读一次写一次"。副本用完即删；临时路径不参与任何内容身份（artifact 只绑**原库**
    的快照身份，`snapshot_id` 来自库自身）。

    **进程级绑定的恢复**：旧 builder 经 `init_db(copy)` 会把 `fstore._db_path` 指到临时副本，
    临时目录随后被删除——若不恢复，进程级 `_db_path` 就悬空指向一个不存在的路径，污染后续
    所有按模块级绑定读库的调用。因此进入前先记下原绑定，**成功与异常路径都在 `finally` 里
    逐字恢复**。
    """
    src = Path(fin_db)
    if not src.exists():
        raise FileNotFoundError(f"financial_v2 库不存在: {fin_db}")
    from financial_v2 import store as fstore
    saved_binding = fstore._db_path
    try:
        with tempfile.TemporaryDirectory(prefix="fin-ro-") as td:
            dst = Path(td) / src.name
            shutil.copy2(src, dst)
            for suffix in ("-wal", "-shm"):
                side = Path(str(src) + suffix)
                if side.exists():
                    shutil.copy2(side, Path(str(dst) + suffix))
            yield dst
    finally:
        fstore._db_path = saved_binding


@dataclass(frozen=True)
class FinancialPhaseOutcome:
    """§十一 财务 backbone phase 的正式结果。

    一个结果里同时给出：只读 artifact、独立复核结论、**恰一个**附注输入状态
    （`ValidatedEvidenceNoteFactSet` 或 `EvidenceNoteGap`）、以及真实库只读性的前后观测。
    附注状态绝不并入 artifact（来源权威不同），也绝不用普通财务缺口冒充。
    """

    phase_version: str
    artifact: FPA.FinancialPackArtifact
    replay: FPA.ArtifactReplayReport
    evidence_note_facts: FPA.ValidatedEvidenceNoteFactSet | None
    evidence_note_gap: FPA.EvidenceNoteGap | None
    readonly_copy_policy_version: str
    db_state_before: dict
    db_state_after: dict

    def __post_init__(self) -> None:
        if self.phase_version != BACKBONE_FINANCIAL_PHASE_VERSION:
            raise FinancialWorkerError(
                f"FinancialPhaseOutcome.phase_version 必须为 {BACKBONE_FINANCIAL_PHASE_VERSION!r}")
        if (self.evidence_note_facts is None) == (self.evidence_note_gap is None):
            raise FinancialWorkerError(
                "财务 phase 结果必须**恰有其一**：ValidatedEvidenceNoteFactSet 或 EvidenceNoteGap")
        drift = financial_db_drift(self.db_state_before, self.db_state_after)
        if drift:
            raise FinancialReadOnlyViolation(
                f"真实财务库在 phase 前后发生漂移：{list(drift)}")

    def to_dict(self) -> dict:
        return {
            "phase_version": self.phase_version,
            "artifact_id": self.artifact.artifact_id,
            "replay": self.replay.to_dict(),
            "evidence_note_facts": (self.evidence_note_facts.to_dict()
                                    if self.evidence_note_facts else None),
            "evidence_note_gap": (self.evidence_note_gap.to_dict()
                                  if self.evidence_note_gap else None),
            "readonly_copy_policy_version": self.readonly_copy_policy_version,
            "db_readonly": {"drift": list(financial_db_drift(self.db_state_before,
                                                            self.db_state_after))},
        }


class FinancialPhaseBlocked(FinancialWorkerError):
    """财务 backbone phase 的显式 block（携带 artifact 与 replay 报告，供只读 UI 如实显示）。

    非权威/漂移快照**不是**拒绝投影的理由（隐藏缺口比显示缺口更糟），而是本 phase 不得
    放行的理由：artifact 照常产出并如实记录，block 携带它一起抛出。
    """

    def __init__(self, reason: str, *, artifact: FPA.FinancialPackArtifact,
                 report: FPA.ArtifactReplayReport,
                 note_gap: FPA.EvidenceNoteGap | None = None,
                 db_state_before: dict | None = None,
                 db_state_after: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.artifact = artifact
        self.report = report
        # 缺口不被吞：block 也必须带着附注输入状态与真实库只读观测一起抛出。
        self.note_gap = note_gap
        self.db_state_before = dict(db_state_before or {})
        self.db_state_after = dict(db_state_after or {})

    def to_dict(self) -> dict:
        return {"phase_version": BACKBONE_FINANCIAL_PHASE_VERSION, "reason": self.reason,
                "artifact_id": self.artifact.artifact_id,
                "replay": self.report.to_dict(),
                "evidence_note_gap": self.note_gap.to_dict() if self.note_gap else None,
                "db_readonly": {"drift": list(financial_db_drift(
                    self.db_state_before, self.db_state_after))}}


def evidence_note_state(task: PS.SectionTask, *, company_id: str, report_as_of: str,
                        contract_version: str, contract_fingerprint: str) -> FPA.EvidenceNoteGap:
    """本轮的财务附注输入状态：**独立**、task-bound 的 `EvidenceNoteGap`。

    本批未实现附注抽取，因此如实登记 `note_extraction_not_implemented`，而不是造一个空
    附注集（空集会被读成"查过且确无附注"），也绝不用普通财务缺口冒充。
    `searched_scope` 记录本轮**已纳入的检索范围**：原因为"未实现抽取"时它是任务声明的能力
    范围，`searched_need_ids` 因此为空——不谎称查过。
    """
    return FPA.EvidenceNoteGap(
        schema_version=FPA.EVIDENCE_NOTE_SCHEMA_VERSION,
        task_id=task.task_id, company_id=company_id, report_as_of=report_as_of or "",
        contract_version=contract_version, contract_fingerprint=contract_fingerprint,
        searched_scope=tuple(task.allowed_capabilities), searched_need_ids=(),
        reason_codes=("note_extraction_not_implemented",),
        detail=("本轮已纳入范围内未取得财务附注：附注抽取尚未实现，故附注数字不参与本次"
                "财务正文，也不以普通财务缺口代替。"))


def run_backbone_financial_phase(
        task: PS.SectionTask, *, company_id: str, projection_id: str,
        contract_version: str, contract_fingerprint: str,
        company_name: str = "", fin_db: str = "data/financial_v2.db",
        scope: str = "consolidated", currency: str = "CNY",
        purpose: str = "credit_analysis", as_of_date: str | None = None,
        snapshot_id: str | None = None,
        authority_source: FPA.FinancialSnapshotAuthoritySource | None = None,
        ) -> FinancialPhaseOutcome:
    """财务 section 的 backbone phase 入口：产出并**独立复核**只读 Financial Pack artifact。

    复核是 live 的（重新独立查询快照权威，要求仍 current+健康且五字段无漂移）；未通过即
    `FinancialPhaseBlocked` fail-closed，绝不返回一个「大概没问题」的 artifact。

    **只读保证（§十）**：会建表/迁移的旧构建逻辑只在真实库的**临时副本**上运行（副本用完
    即删，并在 `finally` 里恢复 `fstore._db_path`）；真实库本身只被 `stat`/`read_bytes`
    （观测）与**连接级只读** SQL（`mode=ro` + `PRAGMA query_only=ON`，绝不 init / 绝不
    `_get_conn` / 绝不改模块级绑定）触碰，调用前后逐项比对 sha256 / size / mtime_ns /
    WAL / SHM，任一项变化即 `FinancialReadOnlyViolation`。

    **附注输入（§十一）**：结果里除 artifact 与 replay 外，必须带**恰一个**附注输入状态
    （本批未实现抽取 → `EvidenceNoteGap`），不得用普通财务缺口冒充，也不并入 artifact。

    `authority_source` 仅供组合/测试注入**独立**查询路径（默认即真库只读查询）；不得传入
    任何自报布尔——artifact 构建本身就拒绝非 `SnapshotAuthority` 对象。
    """
    if task.section_id != "financial":
        raise FinancialWorkerError(
            f"财务 backbone phase 只服务 section_id='financial'，当前 {task.section_id!r}")
    if task.research_policy != SECTION_RESEARCH_POLICY:
        raise FinancialWorkerError(
            f"财务 backbone phase 只服务 section 级 research_policy="
            f"{SECTION_RESEARCH_POLICY!r}，当前 {task.research_policy!r}"
            f"（topic 章节走 run_backbone_topic_phase）")
    for name, value in (("projection_id", projection_id),
                        ("contract_version", contract_version),
                        ("contract_fingerprint", contract_fingerprint)):
        if not isinstance(value, str) or value == "":
            raise FinancialWorkerError(f"财务 backbone phase 的 {name} 必须为非空字符串")

    before = financial_db_state(fin_db)
    # 旧构建逻辑（含 `init_db` 建表/迁移）只在临时副本上跑：真实库不被建表、不被迁移。
    with readonly_financial_copy(fin_db) as read_copy:
        pack = build_fact_pack_for_task(
            task, company_id=company_id, company_name=company_name, snapshot_id=snapshot_id,
            fin_db=str(read_copy), scope=scope, currency=currency, purpose=purpose,
            as_of_date=as_of_date)

    # 快照权威一律独立查询**原库**（只读，不 init）：构建前查一次给 artifact 记录，
    # 复核时由 replay 再查一次比对漂移。artifact 自身不得自证权威。
    source = authority_source or FPA.FinancialDbAuthoritySource(fin_db)
    artifact = FPA.build_financial_pack_artifact(
        pack, task_id=task.task_id, projection_id=projection_id,
        contract_version=contract_version, contract_fingerprint=contract_fingerprint,
        fact_selection_rule_version=FINANCIAL_FACT_SELECTION_RULE_VERSION,
        authority=source.authority_for(pack))

    report = FPA.verify_financial_pack_artifact(artifact, authority_source=source)
    note_gap = evidence_note_state(
        task, company_id=company_id, report_as_of=pack.as_of_date,
        contract_version=contract_version, contract_fingerprint=contract_fingerprint)

    after = financial_db_state(fin_db)
    drift = financial_db_drift(before, after)
    if drift:
        # 真实库被改动过 ⇒ 本 phase 的只读前提已破，不得返回任何"看起来没问题"的结果。
        raise FinancialReadOnlyViolation(
            f"真实财务库在 phase 调用前后发生漂移（{list(drift)}）："
            f"策略={FINANCIAL_READONLY_COPY_POLICY_VERSION}；本 phase 未保持只读，fail-closed")

    if not report.passed():
        raise FinancialPhaseBlocked(
            "财务 artifact 独立复核未通过："
            f"mode={report.mode}，snapshot_authoritative={report.snapshot_authoritative}，"
            + "；".join(report.notes), artifact=artifact, report=report, note_gap=note_gap,
            db_state_before=before, db_state_after=after)
    return FinancialPhaseOutcome(
        phase_version=BACKBONE_FINANCIAL_PHASE_VERSION, artifact=artifact, replay=report,
        evidence_note_facts=None, evidence_note_gap=note_gap,
        readonly_copy_policy_version=FINANCIAL_READONLY_COPY_POLICY_VERSION,
        db_state_before=before, db_state_after=after)


# ---------------------------------------------------------------------------
# LLM 解读
# ---------------------------------------------------------------------------

def _default_llm_generate(messages: list[dict], system: str) -> str:
    resp = llm.chat_with_usage(messages, system=system, prompt_version=SC.PROMPT_VERSION,
                               thinking={"type": "disabled"})
    return resp.text


def _facts_json(pack: FinancialFactPack) -> str:
    return json.dumps([
        {"fact_id": f.fact_id, "kind": f.kind, "label": f.label, "period": f.period,
         "display": f.display, "note": f.note}
        for f in sorted(pack.facts, key=lambda f: (f.period, f.fact_id))
    ], ensure_ascii=False, indent=2)


def _gaps_json(pack: FinancialFactPack) -> str:
    all_gaps = [dict(g, required=True) for g in pack.gaps] + \
               [dict(g, required=False) for g in pack.diagnostic_gaps]
    return json.dumps([
        {"label": g["label"], "period": g["period"], "status": g["status"],
         "reason_code": g["reason_code"], "required": g.get("required")}
        for g in sorted(all_gaps, key=lambda g: (g["period"], g["label"]))
    ], ensure_ascii=False, indent=2)


def _topics_json(task: PS.SectionTask) -> str:
    out = []
    for tid in task.topic_ids:
        qs = [{"question_id": q.question_id, "question": q.question}
              for q in task.questions if q.topic_id == tid]
        out.append({"topic_id": tid, "label": _TOPIC_LABELS.get(tid, tid),
                    "questions": qs})
    return json.dumps(out, ensure_ascii=False, indent=2)


def _build_prompt(pack: FinancialFactPack, task: PS.SectionTask) -> tuple[str, str]:
    template = llm.load_prompt("section_financial")
    company = json.dumps({
        "company_name": pack.company_name, "company_id": pack.company_id,
        "snapshot_id": pack.snapshot_id, "as_of_date": pack.as_of_date,
        "scope": pack.scope, "currency": pack.currency, "purpose": pack.purpose,
        "periods": pack.periods,
        "statements_available": list(pack.statements_available),
    }, ensure_ascii=False, indent=2)
    system = ("你是授信报告财务章节撰写助手，严格遵守 prompt 中的铁律，"
              "只输出 JSON，绝不手写数字。")
    user = (template.replace("<<COMPANY>>", company)
                    .replace("<<TOPICS>>", _topics_json(task))
                    .replace("<<FACTS>>", _facts_json(pack))
                    .replace("<<GAPS>>", _gaps_json(pack)))
    return system, user


def _strip_markdown_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s


def _extract_outer_json(s: str) -> str:
    """提取最外层 JSON object（首个 ``{`` 到末个 ``}``），失败 fail-closed。"""
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise FinancialWorkerError("LLM 输出不含 JSON 对象（fail-closed）")
    return s[start:end + 1]


def _repair_trailing_commas(text: str) -> tuple[str, int]:
    """确定性删除「字符串外、紧邻 ``}`` / ``]`` 之前的逗号」，返回 (修复后文本, 删除条数)。

    有限、可审计的状态机：跟踪 JSON 字符串与转义 —— 字符串内的逗号 / ``}`` / ``]`` /
    转义引号一律原样保留；仅当逗号位于字符串外、其后（忽略空白）紧跟 ``}`` 或 ``]``
    时才删除。不修改 key / value / 数字 / marker / 中文正文 / 数组内容（唯一改动是被删
    的尾逗号）。
    """
    out: list[str] = []
    repaired = 0
    in_string = False
    escaped = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                repaired += 1
                i += 1
                continue  # 丢弃尾逗号
        out.append(ch)
        i += 1
    return "".join(out), repaired


def _parse_llm_json(text: str) -> tuple[dict, dict]:
    """解析 LLM JSON 输出：严格 ``json.loads`` → 一次确定性尾逗号规范化 → 严格。

    返回 ``(payload, audit)``；audit 记录 ``parse_mode``（strict / trailing_comma_repaired）、
    ``repaired_trailing_comma_count``、``first_error``（首次严格解析错误类型）。
    仍无法解析 → fail-closed。严禁宽松 json_repair / eval / 单引号转换 / 自动补 key/value /
    截断补全（这些会猜测语义，破坏 fail-closed 保证）。
    """
    s = _strip_markdown_fence(text)
    outer = _extract_outer_json(s)
    audit: dict = {"parse_mode": "strict", "repaired_trailing_comma_count": 0,
                   "first_error": None}
    try:
        return json.loads(outer), audit
    except json.JSONDecodeError as e:
        audit["first_error"] = f"{type(e).__name__}: {e}"

    repaired_text, n = _repair_trailing_commas(outer)
    if n > 0:
        audit["parse_mode"] = "trailing_comma_repaired"
        audit["repaired_trailing_comma_count"] = n
        try:
            return json.loads(repaired_text), audit
        except json.JSONDecodeError as e:
            raise FinancialWorkerError(
                f"LLM 输出 JSON 解析失败（尾逗号规范化后仍失败）: {e}") from e
    raise FinancialWorkerError(
        f"LLM 输出 JSON 解析失败（无尾逗号可修复）: {audit['first_error']}")


def _generate_and_parse(generate, messages, system) -> tuple[dict, dict]:
    """LLM 生成 + 解析（同一 prompt），至多两次生成尝试，每次走「严格 → 尾逗号规范化」。

    返回 ``(payload, audit)``。audit 记录 ``generation_attempts`` / ``parse_mode`` /
    ``repaired_trailing_comma_count`` / ``first_parse_error`` / ``regenerated``。
    两次都失败 → fail-closed 抛 ``FinancialWorkerError``（不产出、不落 Store）。
    """
    audit: dict = {"generation_attempts": 0, "parse_mode": None,
                   "repaired_trailing_comma_count": 0,
                   "first_parse_error": None, "regenerated": False}
    last_error: FinancialWorkerError | None = None
    for attempt in (1, 2):
        audit["generation_attempts"] = attempt
        raw_text = generate(messages, system)
        try:
            payload, p_audit = _parse_llm_json(raw_text)
        except FinancialWorkerError as e:
            last_error = e
            if attempt == 1:
                audit["first_parse_error"] = str(e)
            continue
        if attempt == 1:
            audit["first_parse_error"] = p_audit["first_error"]
        audit["parse_mode"] = p_audit["parse_mode"]
        audit["repaired_trailing_comma_count"] = p_audit["repaired_trailing_comma_count"]
        audit["regenerated"] = attempt == 2
        return payload, audit
    raise FinancialWorkerError(
        f"LLM 输出 JSON 两次生成均无法解析（fail-closed）: {last_error}")


# ---------------------------------------------------------------------------
# claim 解析 + 确定性复核
# ---------------------------------------------------------------------------

def _question_map(task: PS.SectionTask) -> dict[str, PS.PlannedQuestion]:
    return {q.question_id: q for q in task.questions}


def _resolve_claims(task: PS.SectionTask, pack: FinancialFactPack,
                    raw_claims: list[dict]) -> tuple[tuple[SS.SectionClaim, ...], dict]:
    qmap = _question_map(task)
    valid_topic_ids = set(task.topic_ids)
    display_map = {fid: f.display for fid, f in pack.by_id.items()}
    # 受信任 meta 占位：公司标识（证券代码）值来自受信任输入 pack.company_id，非 LLM 生成。
    # 它可被 resolve_markers 替换，但不作为 FinancialFact（不参与 citation 派生）。
    display_map[SC.META_COMPANY_ID] = pack.company_id
    claims: list[SS.SectionClaim] = []
    verification: dict = {"bare_number_violations": [], "rejected": [],
                          "parsed": len(raw_claims)}

    for i, raw in enumerate(raw_claims):
        topic_id = raw.get("topic_id", "")
        claim_type = raw.get("claim_type", "inference")
        text = raw.get("text", "")
        qids = tuple(sorted(set(raw.get("question_ids") or [])))

        if topic_id not in valid_topic_ids:
            verification["rejected"].append(f"[{i}] 非法 topic_id: {topic_id!r}")
            continue
        if claim_type not in SS.CLAIM_TYPES:
            verification["rejected"].append(f"[{i}] 非法 claim_type: {claim_type!r}")
            continue
        if not text.strip():
            verification["rejected"].append(f"[{i}] text 为空")
            continue
        if not qids:
            verification["rejected"].append(f"[{i}] question_ids 为空")
            continue

        # 确定性 Contract 覆盖（req 3）：question 必须存在且归属于本 claim 的 topic。
        unknown_qids = [q for q in qids if q not in qmap]
        if unknown_qids:
            verification["rejected"].append(f"[{i}] 伪造 question_id: {unknown_qids}")
            continue
        cross_topic = [q for q in qids if qmap[q].topic_id != topic_id]
        if cross_topic:
            verification["rejected"].append(
                f"[{i}] question_id 跨 topic: {cross_topic}（claim topic={topic_id!r}）")
            continue

        # 确定性复核：LLM 原文禁止出现裸数字（policy 常量除外）→ 整节 fail-closed。
        bare = SC.bare_number_tokens(text)
        if bare:
            verification["bare_number_violations"].append(
                f"[{i}] topic={topic_id} 裸数字: {bare}（LLM 不得手写数字）")
            continue

        # marker 是唯一引用来源（req 5）：只认正文 [[fact_id]]，不认额外 fact_ids。
        # [[meta_company_id]] 是公司标识占位，不算财务 fact —— 不满足「至少一个 [[fact_id]]」
        # 的引用要求，也不参与 citation 派生（否则会把公司代码混进 fact 引用）。
        marker_fids = re.findall(r"\[\[([A-Za-z0-9_.\-]+)\]\]", text)
        fact_fids = [f for f in marker_fids if f != SC.META_COMPANY_ID]
        if not fact_fids:
            verification["rejected"].append(f"[{i}] 无 [[fact_id]] 引用")
            continue
        resolved_text, marker_errors = SC.resolve_markers(text, display_map)
        if marker_errors:
            verification["rejected"].append(f"[{i}] 未知/未解析 fact_id: {marker_errors}")
            continue

        uniq_fids = sorted(set(fact_fids))
        citation_refs = tuple(pack.by_id[f].citation for f in uniq_fids)

        impact_scope: tuple[str, ...] = ()
        for qid in qids:
            q = qmap[qid]
            impact_scope = tuple(sorted(set(impact_scope) | set(q.impact_scope)))

        claim_id = SS.derive_claim_id(claim_type, topic_id, qids, resolved_text, citation_refs)
        conf = raw.get("confidence") if raw.get("confidence") in SS.CONFIDENCE_LEVELS else "low"
        claims.append(SS.LegacySectionClaimV1(
            claim_id=claim_id, section_id=task.section_id, topic_id=topic_id,
            question_ids=qids, text=resolved_text, claim_type=claim_type,
            citation_refs=citation_refs, confidence=conf, impact_scope=impact_scope))

    return tuple(claims), verification


# ---------------------------------------------------------------------------
# 未解决项（必需指标缺口 + Contract 覆盖缺口 → SectionUnresolved）
# ---------------------------------------------------------------------------

def _gap_state(status: str) -> str:
    if status == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if status == "BLOCKED_BY_SNAPSHOT":
        return "NOT_FOUND_AFTER_SEARCH"
    if status == "ZERO_DENOMINATOR":
        return "NOT_APPLICABLE"
    return "NOT_PROVIDED"


def _missing_policy_state(policy: str) -> str:
    return _MISSING_POLICY_STATE.get(policy, "NOT_FOUND_AFTER_SEARCH")


def _question_blocking(task: PS.SectionTask, question_id: str | None) -> tuple[str, ...]:
    if not question_id:
        return ()
    for q in task.questions:
        if q.question_id == question_id:
            return tuple(q.blocking_policy)
    return ()


def _build_unresolved(task: PS.SectionTask, pack: FinancialFactPack,
                      claims: tuple[SS.SectionClaim, ...],
                      required_formula_ids: set[str],
                      formula_to_question: dict[str, str]) -> tuple[SS.SectionUnresolved, ...]:
    unresolved: list[SS.SectionUnresolved] = []
    seen: set[str] = set()
    qmap = {q.question_id: q for q in task.questions}

    # 1) 必需指标缺口（req 4）：required 且不可得 → 对应 question 的 SectionUnresolved。
    for g in pack.gaps:
        fid = g.get("formula_id", "")
        topic_id = _FORMULA_TO_TOPIC.get(fid, "fin_growth")
        qid = formula_to_question.get(fid) or _first_question_in_topic(task, topic_id)
        q = qmap.get(qid) if qid else None
        impact_scope = tuple(q.impact_scope) if q else ()
        state = _gap_state(g["status"])
        detail = (f"指标「{g['label']}」期间 {g['period']} 未取得："
                  f"status={g['status']}"
                  + (f"，reason={g['reason_code']}" if g.get("reason_code") else ""))
        uid = "ur_" + SS.sha256_json([task.section_id, topic_id, fid,
                                      g["period"], g["status"]])[:24]
        if uid in seen:
            continue
        seen.add(uid)
        blocking = _question_blocking(task, qid)
        unresolved.append(SS.SectionUnresolved(
            unresolved_id=uid, section_id=task.section_id, topic_id=topic_id,
            question_id=qid, state=state, reason_code=g["reason_code"] or g["status"],
            detail=detail, impact_scope=impact_scope, blocking_effects=blocking))

    # 2) Contract 覆盖缺口（req 3）：未被有效 claim 覆盖的必答 question → SectionUnresolved。
    covered: set[str] = set()
    for c in claims:
        covered.update(c.question_ids)
    for q in task.questions:
        if q.question_id in covered:
            continue
        if any(u.question_id == q.question_id for u in unresolved):
            continue  # 已有 required 缺口覆盖该问题，不重复报覆盖缺口
        uid = "ur_" + SS.sha256_json([task.section_id, q.topic_id, q.question_id,
                                      "coverage"])[:24]
        if uid in seen:
            continue
        seen.add(uid)
        state = _missing_policy_state(q.missing_policy)
        blocking = tuple(q.blocking_policy)
        unresolved.append(SS.SectionUnresolved(
            unresolved_id=uid, section_id=task.section_id, topic_id=q.topic_id,
            question_id=q.question_id, state=state,
            reason_code=q.missing_policy or "uncovered",
            detail=f"问题「{q.question}」未形成有效 claim 覆盖（或覆盖 claim 被拒绝）",
            impact_scope=tuple(q.impact_scope), blocking_effects=blocking))
    return tuple(unresolved)


def _derive_status(unresolved: tuple[SS.SectionUnresolved, ...]) -> str:
    if not unresolved:
        return "COMPLETED"
    for u in unresolved:
        if "SECTION_BLOCKED" in u.blocking_effects or "JOB_BLOCKED" in u.blocking_effects:
            return "SECTION_BLOCKED"
    return "COMPLETED_WITH_GAPS"


# ---------------------------------------------------------------------------
# 渲染（确定性 Markdown）
# ---------------------------------------------------------------------------

def _render_markdown(task: PS.SectionTask, pack: FinancialFactPack,
                     claims: tuple[SS.SectionClaim, ...],
                     unresolved: tuple[SS.SectionUnresolved, ...],
                     snapshot: FS.FinancialSnapshot) -> str:
    lines: list[str] = []
    title = task.title or "财务分析"
    lines.append(f"# {title}")
    lines.append("")
    lines.append("## 数据来源与口径")
    lines.append("")
    lines.append(f"- 公司：{pack.company_name}（{pack.company_id}）")
    lines.append(f"- 财务快照：`{pack.snapshot_id}`")
    lines.append(f"- 报告期：{pack.as_of_date}；覆盖期间：{', '.join(pack.periods)}")
    if pack.period_note:
        pn = pack.period_note
        annual_avail = pn.get("annual_periods_available", [])
        if pn.get("full_three_years"):
            lines.append(f"- 年度主线：近三年（{', '.join(annual_avail[-3:])}）")
        else:
            lines.append(f"- 年度主线：已取得 {pn.get('annual_years', 0)} 个年度"
                         + (f"（{', '.join(annual_avail)}）" if annual_avail else "")
                         + "，不足三年，未取得部分不编数")
        if pn.get("sub_annual_supplement"):
            lines.append(f"- 季度/中报补充：{pn['sub_annual_supplement']}"
                         "（独立展示，不与年度同比）")
    lines.append(f"- 口径：{pack.scope} / {pack.currency} / {pack.purpose}")
    lines.append(f"- 主表齐备：{', '.join(pack.statements_available) or '（未取得主表）'}")
    lines.append(f"- 金额单位：元（科目金额已归一化）；指标为 Python 计算值")
    lines.append("")

    # 关键指标表（仅可得项）。期间列写**期间表达**（`2025年度` / `2025年末`）：流量指标的期间
    # 不是一个时点，写成裸期间末日会让读者把「本报告期的量」读成「那一天的量」。代理口径的
    # 原因码也写在**同一条可见行**里（读者要看到的披露不能只藏在事实对象内部）。
    metric_facts = [f for f in pack.facts if f.kind == "calculation"]
    if metric_facts:
        lines.append("## 关键财务指标")
        lines.append("")
        lines.append("| 指标 | 期间 | 值 | 口径与状态 |")
        lines.append("|------|------|-----|------------|")
        for f in sorted(metric_facts, key=lambda f: (f.period, f.fact_id)):
            period_text = f.period_label or f.period
            if f.status == "CALCULATED_PROXY":
                status_cn = f"代理口径（{f.reason_code or 'proxy'}）"
            else:
                status_cn = "精确口径"
            lines.append(f"| {f.label} | {period_text} | {f.display} | {status_cn} |")
        lines.append("")

    # 主题分组 prose
    lines.append("## 分析")
    lines.append("")
    by_topic: dict[str, list[SS.SectionClaim]] = {}
    for c in claims:
        by_topic.setdefault(c.topic_id, []).append(c)
    for tid in task.topic_ids:
        if tid not in by_topic:
            continue
        lines.append(f"### {_TOPIC_LABELS.get(tid, tid)}")
        lines.append("")
        for c in by_topic[tid]:
            lines.append(c.text)
            lines.append("")
        lines.append("")

    # 未解决项
    if unresolved:
        lines.append("## 未取得 / 未解决项")
        lines.append("")
        for u in unresolved:
            lines.append(f"- [{u.state}] {u.detail}")
        lines.append("")

    # 诊断性未取得（非必需，不影响章节状态）
    if pack.diagnostic_gaps:
        lines.append("## 诊断性未取得（非本任务必需，不影响章节状态）")
        lines.append("")
        for g in pack.diagnostic_gaps:
            lines.append(f"- 指标「{g['label']}」期间 {g['period']}：status={g['status']}"
                         + (f"，reason={g['reason_code']}" if g.get("reason_code") else ""))
        lines.append("")

    # 引用与数字来源
    lines.append("## 引用与数字来源")
    lines.append("")
    distinct: dict[str, CitationRef] = {}
    for c in claims:
        for ref in c.citation_refs:
            distinct[SS.citation_identity(ref)] = ref
    for idx, ref in enumerate(sorted(distinct.values(), key=SS.citation_identity), start=1):
        if ref.formula_id:
            lines.append(f"{idx}. `{ref.formula_id}` v{ref.formula_version} 期间 {ref.period} "
                         f"（快照 {ref.snapshot_id}）")
        else:
            lines.append(f"{idx}. 科目 `{ref.item_code}` 期间 {ref.period} "
                         f"（快照 {ref.snapshot_id}）")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def _dependency_fingerprint(snapshot: FS.FinancialSnapshot, task: PS.SectionTask) -> str:
    """依赖指纹（req 2）：编码 snapshot 身份 + 公式版本 + 契约/任务依赖 + 版本常量。

    进入 section_version 派生 —— 任一依赖（prompt/rules/worker/snapshot/公式/任务依赖）
    变化 → 新版本；完全相同输入 → 严格复用。
    """
    return SS.sha256_json([
        snapshot.snapshot_id,
        sorted(snapshot.required_formula_versions.items()),
        sorted((task.dependency_versions or {}).items()),
        SC.PROMPT_VERSION, SC.RENDERER_VERSION, SC.RULES_VERSION, SC.WORKER_VERSION,
    ])


def run_task(task: PS.SectionTask, *, company_id: str, company_name: str = "",
             snapshot_id: str | None = None, fin_db: str = "data/financial_v2.db",
             scope: str = "consolidated", currency: str = "CNY",
             purpose: str = "credit_analysis", as_of_date: str | None = None,
             llm_generate: Callable[[list[dict], str], str] | None = None) -> WorkerResult:
    """执行财务章节 Worker（完整确定性流程 + 可选 LLM 解读）。"""
    if task.section_id != "financial":
        raise FinancialWorkerError(f"本 Worker 仅处理 financial 章节，收到 {task.section_id!r}")
    generate = llm_generate or _default_llm_generate
    task_locked = (task.dependency_versions or {}).get("financial_snapshot_id") or ""

    snap = _resolve_snapshot(snapshot_id, company_id=company_id, scope=scope,
                             currency=currency, as_of_date=as_of_date, purpose=purpose,
                             fin_db=fin_db, task_locked_id=task_locked)

    required_fids = _required_formula_ids(task, snap)
    relevant_fids = _relevant_formula_ids(task, snap)
    formula_to_question = _formula_to_question_map(task)

    pack = build_fact_pack(snap, company_name=company_name,
                           required_formula_ids=required_fids,
                           relevant_formula_ids=relevant_fids)

    system, user = _build_prompt(pack, task)
    messages = [{"role": "user", "content": user}]
    payload, generation_audit = _generate_and_parse(generate, messages, system)
    raw_claims = payload.get("claims", [])
    if not isinstance(raw_claims, list):
        raise FinancialWorkerError("LLM 输出 claims 不是数组（fail-closed）")

    claims, verification = _resolve_claims(task, pack, raw_claims)
    if verification["bare_number_violations"]:
        raise FinancialWorkerError(
            "财务章节确定性复核失败（LLM 手写数字，fail-closed，不产出章节）："
            + json.dumps(verification["bare_number_violations"], ensure_ascii=False))
    # 注意：claim 全被拒/为空 → 不抛错，诚实降级为「全量未覆盖」（req 3），
    # 由 _build_unresolved 转成覆盖缺口 + 状态派生，绝不静默标 COMPLETED。

    unresolved = _build_unresolved(task, pack, claims, required_fids, formula_to_question)
    status = _derive_status(unresolved)

    fingerprint = _dependency_fingerprint(snap, task)
    section_version = SS.derive_legacy_section_version(
        task.task_id, claims, unresolved,
        renderer_version=SC.RENDERER_VERSION, rules_version=SC.RULES_VERSION,
        dependency_fingerprint=fingerprint)
    section_result_id = SS.derive_section_result_id(section_version)
    markdown = _render_markdown(task, pack, claims, unresolved, snap)

    result = SS.LegacySectionResultV1(
        section_result_id=section_result_id, section_version=section_version,
        task_id=task.task_id, section_id=task.section_id, status=status,
        claims=claims, unresolved=unresolved, markdown=markdown, evaluation=None,
        source_run_ids=(), source_question_ids=tuple(task.question_ids()),
        dependency_fingerprint=fingerprint, created_at=_utcnow())

    errors = svalidator.validate_legacy_section_result(result)
    if errors:
        raise FinancialWorkerError("章节结构校验失败:\n" + "\n".join(f"  - {e}" for e in errors))

    covered = sorted({qid for c in claims for qid in c.question_ids})
    verification["claims_resolved"] = len(claims)
    verification["unresolved"] = len(unresolved)
    verification["facts"] = len(pack.facts)
    verification["generation"] = generation_audit
    verification["required_gaps"] = len(pack.gaps)
    verification["diagnostic_gaps"] = len(pack.diagnostic_gaps)
    verification["required_formula_ids"] = sorted(required_fids)
    verification["statements_available"] = list(pack.statements_available)
    verification["coverage"] = {
        "covered_questions": covered,
        "uncovered_questions": [q.question_id for q in task.questions
                                if q.question_id not in covered],
    }
    return WorkerResult(section_result=result, fact_count=len(pack.facts),
                        claim_count=len(claims), unresolved_count=len(unresolved),
                        reused=False, committed=False, verification=verification)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.financial_worker", description="Phase 4 财务章节 Worker")
    parser.add_argument("--task", required=True, help="SectionTask JSON 文件（section_task_to_dict 产物）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--company-name", default="")
    parser.add_argument("--snapshot", default=None, dest="snapshot_id")
    parser.add_argument("--fin-db", default="data/financial_v2.db")
    parser.add_argument("--section-db", default="data/sections.db")
    parser.add_argument("--scope", default="consolidated")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--purpose", default="credit_analysis")
    parser.add_argument("--as-of", default=None, dest="as_of_date")
    parser.add_argument("--validate-only", action="store_true", help="只生成不写 Store")
    parser.add_argument("--store", action="store_true", help="原子写入 section Store")
    parser.add_argument("--out", default=None, help="Markdown 输出路径（可选）")
    args = parser.parse_args(argv)

    task_dict = json.loads(Path(args.task).read_text(encoding="utf-8"))
    task = PS.section_task_from_dict(task_dict)

    try:
        wr = run_task(task, company_id=args.company_id, company_name=args.company_name,
                      snapshot_id=args.snapshot_id, fin_db=args.fin_db,
                      scope=args.scope, currency=args.currency, purpose=args.purpose,
                      as_of_date=args.as_of_date)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("财务章节 Worker 失败")
        print(f"财务章节 Worker 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    summary = {
        "section_result_id": wr.section_result.section_result_id,
        "section_version": wr.section_result.section_version,
        "status": wr.section_result.status,
        "facts": wr.fact_count, "claims": wr.claim_count,
        "unresolved": wr.unresolved_count,
        "verification": wr.verification,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.store:
        from sections import store as sstore
        sstore.init_db(args.section_db)
        run_id = f"run_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
        commit = sstore.commit_section_result(wr.section_result, run_id=run_id)
        print(f"\n[store] {json.dumps({
            'reused': commit.reused, 'current_switched': commit.current_switched,
            'claim_count': commit.claim_count, 'unresolved_count': commit.unresolved_count},
            ensure_ascii=False)}")

    if args.out:
        Path(args.out).write_text(wr.section_result.markdown, encoding="utf-8")
        print(f"\nMarkdown 已写入: {args.out}")

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())

"""Phase 2 RouteContext 构造器（只读 I/O 层）。

把 company_id 折叠为 Router 所需的静态能力清单（RouteContext）：

- supported_db_fields / supported_metric_ids —— 静态能力，来自注册表，恒非空，
  决定 route（契约修正 A：只看能力是否支持）；
- available_db_fields / available_metric_ids —— 当前快照真实可用值，决定 DB executor
  返回 DB_RESULT_AVAILABLE 还是 DB_FIELD_UNAVAILABLE；
- available_document_ids / available_source_types —— 当前 evidence 可检索文档；
- report_as_of —— **报告参考日**（调用方按本次报告输入声明）；未声明时不冒充：
  回落为快照选择日并把 `report_as_of_source` 标成 `snapshot_as_of_date_fallback`；
- snapshot_as_of_date —— **快照选择日**（选中 current snapshot 的 as_of_date）；
- external_research_enabled —— 外部检索开关（默认开：当前为 Claude built-in web search）。

`rc-rd-1`（M930-3 返修 P4）：上面两个日期曾经是**一个**字段——`report_as_of` 取的就是
`FinancialSnapshot.as_of_date`，即财务期末冒充报告生成日。现在它们是两个正交轴：报告日由
调用方声明（`report_as_of=`），选择日由只读快照查询决定；`report_as_of_source` 让回落可审计。

只读、不写任何库、不执行检索。无财务数据是合法空态（available_* 为空、snapshot_as_of_date
与 report_as_of 为 None），不抛错；库未初始化等真实错误 fail-closed 抛错。

CLI: python -m routing.context --company 300750 [--report-as-of 2026-09-24]
"""

from __future__ import annotations

import logging

from evidence import store as estore
from financial_v2 import progress
from financial_v2 import snapshots
from financial_v2 import store as fstore
from routing import db_targets
from routing import schema as S

logger = logging.getLogger(__name__)

# 指标「可用」仅两种计算态（有真实值）；其余 MISSING_INPUT / PARTIAL_INPUT /
# ZERO_DENOMINATOR / NOT_APPLICABLE / BLOCKED_BY_SNAPSHOT 一律视为不可用。
_AVAILABLE_METRIC_STATUSES = ("CALCULATED_EXACT", "CALCULATED_PROXY")

# 外部检索默认开启：当前互联网检索走 Claude built-in web search（CLAUDE.md 技术栈）。
_DEFAULT_EXTERNAL_RESEARCH = True


# ---------------------------------------------------------------------------
# 纯派生函数（可独立测试，无 I/O）
# ---------------------------------------------------------------------------

def _available_field_codes(items) -> set[str]:
    """快照条目中有真实金额的 standard_item_code 集合。"""
    return {it.standard_item_code for it in items if it.amount is not None}


def _available_metric_ids(metric_results) -> set[str]:
    """已计算出真实值的 formula_id 集合（CALCULATED_EXACT/PROXY）。"""
    return {mr.formula_id for mr in metric_results if mr.status in _AVAILABLE_METRIC_STATUSES}


def _extract_current_documents(records) -> tuple[list[str], list[str]]:
    """由 DocumentRecord 列表派生 (available_document_ids, available_source_types)。

    status=current 的文档才可检索；按 document_id 去重（一份文档仅一个 current 版本）。
    """
    seen: dict[str, str] = {}
    for d in records:
        if d.status != "current":
            continue
        seen[d.document_id] = d.source_type
    ids = list(seen.keys())
    return ids, [seen[i] for i in ids]


# ---------------------------------------------------------------------------
# 只读数据源
# ---------------------------------------------------------------------------

def _current_document_records(company_id: str) -> list:
    """当前可检索文档记录：status=current 且有 current evidence set。"""
    out = []
    for d in estore.list_documents(company_id):
        if d.status != "current":
            continue
        if estore.current_document_version(company_id, d.document_id) is None:
            continue
        out.append(d)
    return out


def _resolve_snapshot(company_id: str, scope: str, currency: str,
                      as_of_date: str | None, purpose: str):
    """定位当前快照；无财务数据是合法空态（返回 None）。"""
    if as_of_date is not None:
        return snapshots.current_snapshot(company_id, scope, currency, as_of_date, purpose)
    try:
        req = progress.build_request_for_company(
            company_id, scope=scope, currency=currency, purpose=purpose)
    except ValueError as e:  # 无 current Record Set（合法空态，非错误）
        logger.info("公司 %s 无 current Record Set，快照置空: %s", company_id, e)
        return None
    return snapshots.current_snapshot(company_id, scope, currency, req.as_of_date, purpose)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_route_context(company_id: str, *, scope: str = "consolidated",
                        currency: str = "CNY", as_of_date: str | None = None,
                        report_as_of: str | None = None,
                        purpose: str = "credit_analysis",
                        external_research_enabled: bool | None = None,
                        ) -> S.RouteContext:
    """把 company_id 折叠为 Router 所需 RouteContext（只读）。

    * `as_of_date` —— **快照选择日**（旧名保留兼容：它的作用一直是「限定选哪个 current
      snapshot」）。选中后同一值出现在 `snapshot_as_of_date` 上。
    * `report_as_of` —— **报告参考日**，由调用方按本次报告输入声明（例如报告生成日）。
      不声明时**不冒充**：取快照选择日，并把 `report_as_of_source` 标成回落。
    """
    docs = _current_document_records(company_id)
    doc_ids, source_types = _extract_current_documents(docs)

    snap = _resolve_snapshot(company_id, scope, currency, as_of_date, purpose)
    snapshot_as_of_date = snap.as_of_date if snap is not None else None
    if report_as_of is not None:
        declared_report_as_of: str | None = report_as_of
        report_as_of_source = S.REPORT_DATE_DECLARED
    elif snapshot_as_of_date is not None:
        declared_report_as_of = snapshot_as_of_date
        report_as_of_source = S.REPORT_DATE_SNAPSHOT_FALLBACK
    else:
        declared_report_as_of = None
        report_as_of_source = S.REPORT_DATE_UNRESOLVED
    # 健康门：report_blocked 或 quarantine 的快照不得贡献 available_*（DB executor 会返回
    # DB_FIELD_UNAVAILABLE，而非把被阻断/隔离快照的值当作可计算值）；仅 healthy 才锁定
    # snapshot_id 并填充 available_periods（fail-closed）。
    healthy = (snap is not None and not snap.report_blocked
               and not fstore.is_quarantined("financial_snapshot", snap.snapshot_id))
    snapshot_id: str | None = None
    available_periods: list[str] = []
    if not healthy:
        avail_fields: list[str] = []
        avail_metrics: list[str] = []
    else:
        items = fstore.list_snapshot_items(snap.snapshot_id)
        avail_fields = sorted(_available_field_codes(items))
        avail_metrics = sorted(
            _available_metric_ids(fstore.list_metric_results(snap.snapshot_id)))
        snapshot_id = snap.snapshot_id
        available_periods = sorted({it.report_period for it in items if it.report_period})

    return S.RouteContext(
        company_id=company_id,
        report_as_of=declared_report_as_of,
        report_as_of_source=report_as_of_source,
        snapshot_as_of_date=snapshot_as_of_date,
        available_document_ids=doc_ids,
        available_source_types=source_types,
        supported_db_fields=db_targets.supported_db_fields(),
        supported_metric_ids=db_targets.supported_metric_ids(),
        available_db_fields=avail_fields,
        available_metric_ids=avail_metrics,
        external_research_enabled=(
            _DEFAULT_EXTERNAL_RESEARCH if external_research_enabled is None
            else external_research_enabled),
        scope=scope,
        currency=currency,
        purpose=purpose,
        available_periods=available_periods,
        snapshot_id=snapshot_id,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m routing.context",
        description="构建 RouteContext（只读，不执行检索）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--scope", default="consolidated")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--as-of-date", dest="as_of_date", default=None,
                        help="快照选择日（限定选哪个 current snapshot）")
    parser.add_argument("--report-as-of", dest="report_as_of", default=None,
                        help="报告参考日（声明值）；不给则回落为快照选择日并如实标注来源")
    parser.add_argument("--purpose", default="credit_analysis")
    parser.add_argument("--fin-db", default=str(fstore.DEFAULT_DB_PATH),
                        help="financial_v2 SQLite 库路径（dev/test 注入临时库）")
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH),
                        help="evidence SQLite 库路径（dev/test 注入临时库）")
    args = parser.parse_args(argv)

    fstore.init_db(args.fin_db)
    estore.init_db(args.ev_db)

    ctx = build_route_context(args.company_id, scope=args.scope, currency=args.currency,
                              as_of_date=args.as_of_date, report_as_of=args.report_as_of,
                              purpose=args.purpose)
    print(json.dumps({
        "company_id": ctx.company_id,
        "report_as_of": ctx.report_as_of,
        "report_as_of_source": ctx.report_as_of_source,
        "snapshot_as_of_date": ctx.snapshot_as_of_date,
        "available_document_ids": ctx.available_document_ids,
        "available_source_types": ctx.available_source_types,
        "supported_db_fields": ctx.supported_db_fields,
        "supported_metric_ids": ctx.supported_metric_ids,
        "available_db_fields": ctx.available_db_fields,
        "available_metric_ids": ctx.available_metric_ids,
        "external_research_enabled": ctx.external_research_enabled,
        "available_periods": ctx.available_periods,
        "snapshot_id": ctx.snapshot_id,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))

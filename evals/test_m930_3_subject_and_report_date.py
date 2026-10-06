"""Eval: M930-3 返修 P4 —— **主体、主体名称与报告日**（`subj-2` / `rc-rd-1`）。

用法: python -m evals.test_m930_3_subject_and_report_date

本条对应三处必须分开的缺陷（任务书 §二、返修计划 §4 P4、定点批 §一.2 读者面）：

1. **主体不得由库的排序决定。** 改前验收 runner 用
   `SELECT … FROM current_snapshot ORDER BY company_id, as_of_date, scope LIMIT 1`
   取主体：库里排序后第一条快照属于谁，这份报告就写给谁。那是把**存量库的排序**当成用户
   输入。现在主体来自 `DeclaredReportInput`（调用参数），库只被用来**核对**它。
2. **报告参考日不得被财务期末冒充。** 改前 `RouteContext.report_as_of` 取的就是
   `FinancialSnapshot.as_of_date`，一个字段同时充当「报告参考日」与「快照选择器」。现在两者
   是两个正交字段（`report_as_of` / `snapshot_as_of_date`），`report_as_of_source` 让回落
   可审计。
3. **主体名称不得由证券代码冒充，也不得由库替声明挑一个。** 改前 `ReportJobInput.company_name`
   直接填成 `company`（代码），于是章节与正文里凡是引用公司名的地方都印出代码。现在名称由
   声明给出（`--subject-name`），并与权威自己的来源文档登记
   （`financial_source_document.declared_company_name`，`subject_match_status='matched'`）
   **逐字**核对；登记缺失、多名字冲突、或与声明不一致都拒绝——**不**回落去写库里的名字。

每条判据都配一个反面对照：喂进**旧行为**，判据必须变红；否则「修好了」只是文字上的。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: §E 接受的核对器政策串：`scp-9`（本组钉住的那一版）**及其之后**的每一版。
#: 登记在这里是为了「升版必须显式到此加一笔」，而不是靠 `!=` 让钉子静默失效。
#: `scp-11`（数值支的事实授权改成**语义配对**）、`scp-12`（同一支再加「声明了逐值身份的事实
#: 只按声明配对、不得回落文本反推」）、`scp-13`（声明了身份的事实不再享有「读不出即放行」的
#: 回落；已声明的占比事实在分母可核之前一律不授权）：与本组钉的主体抽取那一格**无关**，但按
#: 上面那条规矩仍必须到此各登记一笔——它们改的是判据，不是这一格的钉子。
_SUBJECT_PINNED_CHECK_POLICIES = ("scp-9", "scp-10", "scp-11", "scp-12", "scp-13")

from evidence import store as estore
from evaluation import run_m930_3_acceptance as ACC
from financial_v2 import progress, snapshots
from financial_v2 import store as fstore
from harness import source_manifest as SM
from routing import context as RContext
from routing import router as RR
from routing import schema as RS
from sections import narrative_schema as NS
from sections import sentence_check as SC

from evals.test_context import _cleanup_db, _seed_records, _specs, _tmp_db

REPO = Path(__file__).resolve().parent.parent

#: 受验 runner 的源码（静态守卫用；不执行它）。
_ACCEPTANCE_SRC = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")

#: 「库里排序后第一条」的旧查询（本模块要证明它**不再**是主体的来源）。
_OLD_SUBJECT_QUERY = ("ORDER BY company_id, as_of_date, scope LIMIT 1")


def _build_snapshot(company: str, ext_id: str, as_of: str, *, purpose: str,
                    scope: str = "consolidated", currency: str = "CNY",
                    run_id: str) -> str:
    """为 `company` 建一个 current 快照，返回 record_set_version。"""
    rs = _seed_records(company, ext_id, _specs({
        "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
        "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
        "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
        "NET_PROFIT": Decimal("120"),
    }, as_of))
    req = snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date=as_of, scope=scope, currency=currency,
        purpose=purpose, record_set_ids=[rs], reconciliation_run_id=None,
        required_formula_ids=["SOLV_CURRENT_RATIO"], restatement_selection={},
        policy_adjustments={}, run_id=run_id)
    res = progress.run_pipeline(req)
    if res.final_state != "completed":
        raise AssertionError(f"夹具前提失败：run_pipeline → {res.final_state}")
    return rs


def _set_declared_name(db: Path, company: str, name: str | None, *,
                       status: str = "matched") -> None:
    """**夹具**：把 `company` 的**全部**来源文档行改写成指定的主体名称登记。

    `subj-2` 起主体名称是「声明 + 与权威登记**逐字**核对」，因此凡是走 `_financial_dims` 的
    夹具都必须在库里有一条**权威自己登记过**的名称，否则核对无从发生。`_seed_records` 建库时
    按既有约定把 `declared_company_name` 写成 company_id，本夹具在它**之后**把登记改写成给定的
    名称——这样「可核实的公司名」与「证券代码」在本组夹具里是两个不同的值，否则「拿代码当
    名字」这条缺陷无从区分。`name=None` 表示**没有**登记名称（不是登记了一个空名字）。

    这里直接写文档头表，是因为 `register_source_atomic` 还要求一份内容版本的文件事实
    （sha / 大小 / 类型），与「核对名称」这条判据无关；本夹具只造表头这一份最小前提，
    不冒充生产写入路径。
    """
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "UPDATE financial_source_document SET declared_company_name=?, "
            "subject_match_status=? WHERE company_id=?", (name, status, company))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO financial_source_document (source_document_id, company_id, "
                "source_name, source_class, declared_company_name, detected_company_name, "
                "subject_match_status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"src-{company}", company, "夹具来源", "annual_report", name, name,
                 status, "2026-01-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def _add_extra_declared_row(db: Path, company: str, name: str, doc_id: str) -> None:
    """**夹具**：再加一条登记行——用于「权威登记了多个**互相冲突**的名称」这一反面现场。"""
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO financial_source_document (source_document_id, company_id, source_name, "
            "source_class, declared_company_name, detected_company_name, subject_match_status, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, company, "夹具来源二号", "annual_report", name, name, "matched",
             "2026-01-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def _old_query_subject(db: Path) -> str | None:
    """**旧行为**：库里排序后第一条 current 快照属于谁。判据的对照面，不是生产路径。"""
    import sqlite3

    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT company_id FROM current_snapshot "
            f"{_OLD_SUBJECT_QUERY}").fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def _refusal(fn, exc_type=ACC.AcceptanceRefusal) -> str:
    """执行 `fn`，返回 `exc_type` 的消息（没有抛错则返回空串）。"""
    try:
        fn()
    except exc_type as exc:
        return str(exc)
    return ""


def _current_snapshot_subjects(db: Path) -> list[str]:
    """只读列出财务库里**有哪些主体**有 current 快照（供真实装配 smoke 挑一个声明主体）。

    这不是生产路径的选主体逻辑（那条已删）；它只是让 smoke 能挑一个**库里真实存在**的主体
    去声明，从而不必在测试里写死公司名。
    """
    import sqlite3

    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT company_id FROM current_snapshot ORDER BY company_id").fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ==================================================================
    # A. 主体由**声明**决定，库只被用来核对
    # ==================================================================
    # 夹具刻意让两者不一致：字典序第一的主体（`AAA_OTHER`）在库里排在最前，而本次报告输入的
    # 声明主体是 `SUBJ`。旧查询会返回 AAA_OTHER，新接口必须返回 SUBJ——这一组因此**能区分**。
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        fstore.init_db(fin_db)
        estore.init_db(ev_db)
        path = Path(fin_db)
        _build_snapshot("AAA_OTHER", "decoy", "2024-12-31", purpose="credit_analysis",
                        run_id="run-decoy")
        _build_snapshot("SUBJ", "subj", "2025-12-31", purpose="credit_analysis",
                        run_id="run-subj")
        # 建库之后把登记改成**可核实的名称**（夹具里刻意不是证券代码）：声明里用的就是它。
        _set_declared_name(path, "AAA_OTHER", "样本另一家公司")
        _set_declared_name(path, "SUBJ", "样本主体")

        check(_old_query_subject(path) == "AAA_OTHER",
              "对照面（非生产）：库里排序后第一条快照属于 AAA_OTHER —— 因此下一组的判据"
              "确实能区分「按排序取」与「按声明取」，不是空断言")

        declared = ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="样本主体", report_as_of="2026-09-24")
        dims = ACC._financial_dims(path, declared)
        check(dims is not None and dims["company_id"] == "SUBJ",
              f"主体：声明为 SUBJ 时必须核对到 SUBJ 的快照，而不是库里排序第一条"
              f"（实际 {dims and dims['company_id']!r}）")
        check(dims is not None and dims["as_of_date"] == "2025-12-31",
              f"主体：核对的是**该主体**那一条快照的口径（实际 {dims and dims['as_of_date']!r}）")
        check(dims is not None and dims.get("subject_id_declared") == "SUBJ"
              and dims.get("subject_declaration_version") == ACC.SUBJECT_DECLARATION_VERSION,
              "主体：声明本身随 dims 带出去（谁声明的、按哪个版本，可审计）")

        # ---- 主体名称：声明 + 与权威登记逐字核对（`subj-2`） ----
        check(dims is not None and dims.get("company_name") == "样本主体",
              f"名称：读者面的名称必须是**核对到**的权威登记名，不是证券代码"
              f"（实际 {dims and dims.get('company_name')!r}）")
        check(dims is not None and dims.get("company_name") != dims.get("company_id"),
              "名称对照：本组的名与代码**不同**——否则「拿代码当名字」这条缺陷无从区分")
        check(dims is not None and dims.get("subject_name_declared") == "样本主体",
              "名称：声明值也随 dims 带出去（与核对值各自可读，不是一个字段两用）")

        # 声明的名称与权威登记不一致 → 拒绝，**不**改口去写库里的名字。
        mismatch = _refusal(lambda: ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="库里没有这个名字", report_as_of="2026-09-24")))
        check("不一致" in mismatch and "不得改口" in mismatch,
              f"名称：声明与权威登记不一致必须拒绝且不改口（实际 {mismatch!r}）")

        # 库里**没有**登记名称 → 拒绝（不得由 runner 编一个，也不得回落成代码）。
        _build_snapshot("NO_NAME_CO", "noname", "2025-12-31", purpose="credit_analysis",
                        run_id="run-noname")
        _set_declared_name(path, "NO_NAME_CO", None)
        no_name = _refusal(lambda: ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="NO_NAME_CO", subject_name="随便什么名字", report_as_of="2026-09-24")))
        check("没有任何来源文档登记过主体名称" in no_name,
              f"名称：库里没有登记名称必须拒绝（不得回落到代码）（实际 {no_name!r}）")

        # 库里登记了**互相冲突**的多个名称 → 拒绝（来源侧自身矛盾，读者面不得任选一个）。
        _build_snapshot("TWO_NAMES", "twonames", "2025-12-31", purpose="credit_analysis",
                        run_id="run-twonames")
        _add_extra_declared_row(path, "TWO_NAMES", "样本甲名", "src-two-1")
        _add_extra_declared_row(path, "TWO_NAMES", "样本乙名", "src-two-2")
        two_names = _refusal(lambda: ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="TWO_NAMES", subject_name="样本甲名", report_as_of="2026-09-24")))
        check("不止一个主体名称" in two_names,
              f"名称：权威登记多个冲突名称必须拒绝（实际 {two_names!r}）")

        # 未被 `matched` 的登记**不算**权威登记过（那正是「尚未认定主体」的状态）。
        _build_snapshot("UNVERIFIED_CO", "unv", "2025-12-31", purpose="credit_analysis",
                        run_id="run-unverified")
        _set_declared_name(path, "UNVERIFIED_CO", "样本待认名", status="unverified")
        unverified = _refusal(lambda: ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="UNVERIFIED_CO", subject_name="样本待认名", report_as_of="2026-09-24")))
        check("没有任何来源文档登记过主体名称" in unverified,
              f"名称：非 matched 的登记不得当作权威登记过名称（实际 {unverified!r}）")

        # 声明的主体在库里没有 current 快照 → 拒绝，**不**回落去写别人。
        missing = _refusal(lambda: ACC._financial_dims(
            path, ACC.DeclaredReportInput(subject_id="NO_SUCH_SUBJECT",
                                          subject_name="随便什么名字",
                                          report_as_of="2026-09-24")))
        check("没有 current 快照" in missing and "不由库决定" in missing,
              f"主体：声明主体在库里没有快照必须拒绝且不回落（实际 {missing!r}）")

        # 声明缺少主体 / 缺少主体名称 / 缺少报告参考日 → 拒绝（都是输入，不是缺省值）。
        check("缺少主体" in _refusal(lambda: ACC.DeclaredReportInput(
            subject_id="   ", subject_name="样本主体", report_as_of="2026-09-24")),
              "主体：声明缺主体必须拒绝（不得由库或目录名补一个）")
        check("缺少主体名称" in _refusal(lambda: ACC.DeclaredReportInput(
            subject_id="SUBJ", report_as_of="2026-09-24")),
              "名称：声明缺主体名称必须拒绝（不得只印代码，也不得由库补一个名字）")
        check("缺少报告参考日" in _refusal(lambda: ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="样本主体", report_as_of="")),
              "声明：缺报告参考日必须拒绝（不得拿快照期末顶替）")

        # ---- 同主体多条 current 快照：必须由声明收窄，不得任选一条 ----
        _build_snapshot("SUBJ", "subj_q1", "2026-03-31", purpose="credit_analysis",
                        run_id="run-subj-q1")
        # 第二次 `_build_snapshot` 会**再登记**一条以代码为名称的文档头（`_seed_records` 的既有
        # 约定），因此名称夹具在建库之后要重新统一——否则「库里登记名 = 代码」与夹具给的可核实
        # 名称并存，本组会先在「登记了不止一个名称」上拒绝，测不到多快照那条判据。
        _set_declared_name(path, "SUBJ", "样本主体")
        ambiguous = _refusal(lambda: ACC._financial_dims(path, declared))
        check("条 current 快照" in ambiguous and "排序第一" in ambiguous,
              f"多快照：同主体两条 current 快照且声明没收窄 → 必须拒绝（实际 {ambiguous!r}）")

        # 收窄到唯一一期 → 成功，且选中的就是声明的那一期（不是排序第一的那一期）。
        narrowed = ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="样本主体", report_as_of="2026-09-24",
            purpose="credit_analysis",
            scope="consolidated", currency="CNY", snapshot_as_of="2025-12-31"))
        check(narrowed is not None and narrowed["snapshot_id"] == dims["snapshot_id"],
              f"多快照：声明收窄到某一期后必须唯一，且必须是**声明的**那一期"
              f"（实际 {narrowed and narrowed['snapshot_id']!r} / 期望 {dims['snapshot_id']!r}）")
        check(narrowed is not None and narrowed["as_of_date"] == "2025-12-31",
              "多快照：收窄后的口径就是声明的那一期（不是库里排序第一的那一期）")
        # 收窄到库里**不存在**的口径 → 拒绝（不是悄悄放宽回全部）。
        narrowed_away = _refusal(lambda: ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="样本主体", report_as_of="2026-09-24",
            scope="parent_only")))
        check("都不满足声明的收窄条件" in narrowed_away,
              f"多快照：收窄条件无匹配必须拒绝，不得放宽回全部（实际 {narrowed_away!r}）")
        # 报告参考日与快照选择日在声明里是**两个**字段：声明报告日为 2026-09-24 不会让
        # 收窄变成「找 2026-09-24 那一期」（库里没有那一期）。
        check(ACC._financial_dims(path, ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="样本主体", report_as_of="2026-09-24",
            scope="consolidated",
            currency="CNY", purpose="credit_analysis", snapshot_as_of="2025-12-31")) is not None,
              "声明：报告参考日不参与快照选择（否则上面这条会因为库里没有 2026-09-24 期而拒绝）")

        # ---- B. 报告参考日与快照选择日是**两个**轴 ----
        # 真实集成：声明的报告参考日（2026-09-24）与快照期末（2025-12-31）不同。
        declared_ctx = RContext.build_route_context(
            "SUBJ", scope="consolidated", currency="CNY", purpose="credit_analysis",
            as_of_date="2025-12-31", report_as_of="2026-09-24")
        check(declared_ctx.report_as_of == "2026-09-24",
              f"报告日：声明值必须原样保留（实际 {declared_ctx.report_as_of!r}）")
        check(declared_ctx.snapshot_as_of_date == "2025-12-31",
              f"快照选择日：必须独立携带快照期末（实际 {declared_ctx.snapshot_as_of_date!r}）")
        check(declared_ctx.report_as_of_source == RS.REPORT_DATE_DECLARED,
              f"报告日：来源必须标成声明（实际 {declared_ctx.report_as_of_source!r}）")
        check(declared_ctx.report_as_of != declared_ctx.snapshot_as_of_date,
              "报告日：这一组的两个日期**不同**——否则下面的判据无从区分")

        # 未声明 → 如实回落并标注（历史调用点的行为不变，但冒名看得见）。
        fallback_ctx = RContext.build_route_context(
            "SUBJ", scope="consolidated", currency="CNY", purpose="credit_analysis",
            as_of_date="2025-12-31")
        check(fallback_ctx.report_as_of == "2025-12-31"
              and fallback_ctx.report_as_of_source == RS.REPORT_DATE_SNAPSHOT_FALLBACK,
              f"回落：未声明报告日时沿用快照日但**标注**为回落"
              f"（实际 {fallback_ctx.report_as_of!r}/{fallback_ctx.report_as_of_source!r}）")

        # 无快照公司：两个日期都空，来源标 unresolved（合法空态）。
        empty_ctx = RContext.build_route_context("NO-SUCH-CO")
        check(empty_ctx.report_as_of is None
              and empty_ctx.snapshot_as_of_date is None
              and empty_ctx.report_as_of_source == RS.REPORT_DATE_UNRESOLVED,
              "空态：无快照时两个日期都为空且来源标 unresolved")

        # 历史构造点（只给 report_as_of）→ 桥接填 snapshot_as_of_date 并标注回落，
        # 行为与 `rc-rd-1` 之前逐位一致（不静默改变冻结 run 的行为）。
        legacy = RS.RouteContext(
            company_id="SUBJ", report_as_of="2024-12-31", available_document_ids=[],
            available_source_types=[], supported_db_fields=[], supported_metric_ids=[],
            available_db_fields=[], available_metric_ids=[], external_research_enabled=True)
        check(legacy.snapshot_as_of_date == "2024-12-31"
              and legacy.report_as_of_source == RS.REPORT_DATE_SNAPSHOT_FALLBACK,
              "桥接：只给 report_as_of 的历史构造点被标注为回落，且快照选择日同样被填上")

        check("来源" in _refusal(lambda: RS.RouteContext(
            company_id="S", report_as_of=None, available_document_ids=[],
            available_source_types=[], supported_db_fields=[], supported_metric_ids=[],
            available_db_fields=[], available_metric_ids=[], external_research_enabled=False,
            report_as_of_source="whatever"), RS.RoutingValidationError),
              "封闭取值：report_as_of_source 自造取值必须被拒（不是自由字符串）")

        # ---- 路由侧：选快照看的是快照选择日，不是报告参考日 ----
        need = RS.InformationNeed(
            need_id="n1", section_id="company", question="总资产是多少？",
            required_evidence_types=[], required_source_types=[], time_scope=None,
            priority="high", depends_on=[])
        target = RR.db_targets.DbTarget(
            target_type="field", standard_item_code="TOTAL_ASSETS",
            formula_id=None, formula_version=None)
        filters = RR._db_filters(target, declared_ctx, need.question)
        check(filters["snapshot_as_of_date"] == "2025-12-31",
              f"路由：DB 过滤器必须用**快照选择日**选快照（实际 {filters['snapshot_as_of_date']!r}）")
        check(filters["target_period"] == "2025-12-31",
              f"路由：问题没给目标期时，回退值同样是快照选择日（实际 {filters['target_period']!r}）")
        # 对照：旧行为（report_as_of 就是快照日）下过滤器拿到的是同一个值——因此本判据只在
        # 两个日期**不同**时才有区分力，这也是上面检查它们不相等的原因。
        legacy_filters = RR._db_filters(target, legacy, need.question)
        check(legacy_filters["snapshot_as_of_date"] == "2024-12-31",
              "路由对照面：历史上下文（两日期相同）下过滤器值不变")

        # 时效判据用的是**本地材料截止**（快照选择日），不是报告参考日。
        # `time_scope` 用 `periods.parse_period` 的规范形（`YYYY-MM`）；写不成规范形的一律
        # 按「无法判定」返回 False（那由 TIME_SCOPE_UNPARSEABLE 分支处理），因此这一组必须
        # 用**可解析**的记号，否则两条判据都会因为「解析不了」而恒为假——空断言。
        late_scope = RS.InformationNeed(
            need_id="n2", section_id="company", question="最近有什么重大诉讼？",
            required_evidence_types=[], required_source_types=[], time_scope="2026-06",
            priority="high", depends_on=[])
        check(RR._time_scope_late(late_scope, declared_ctx) is True,
              "时效：time_scope（2026-06）晚于快照选择日（2025-12-31，本地材料截至期）"
              "必须判为「晚」→ 走外部信号")
        in_range = RS.InformationNeed(
            need_id="n3", section_id="company", question="最近有什么重大诉讼？",
            required_evidence_types=[], required_source_types=[], time_scope="2025",
            priority="high", depends_on=[])
        check(RR._time_scope_late(in_range, declared_ctx) is False,
              "时效：time_scope（2025）在本地材料范围内不得触发外部信号")
        # 反面对照：若错用报告参考日（2026-09-24）当截止，「2026年6月」就会被判成「不晚」。
        wrong_cutoff = RS.RouteContext(
            company_id="SUBJ", report_as_of="2026-09-24",
            report_as_of_source=RS.REPORT_DATE_DECLARED, snapshot_as_of_date="2025-12-31",
            available_document_ids=[], available_source_types=[], supported_db_fields=[],
            supported_metric_ids=[], available_db_fields=[], available_metric_ids=[],
            external_research_enabled=True)
        check(RR._time_scope_late(late_scope, wrong_cutoff) is True,
              "时效对照：显式分开两轴后，报告日不再是判据的输入（同一 need 同一结论）")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    # ==================================================================
    # C. 静态守卫：runner 里不再有「按排序取主体」这条路
    # ==================================================================
    check(_OLD_SUBJECT_QUERY not in _ACCEPTANCE_SRC,
          f"守卫：runner 源码里不得再出现 `{_OLD_SUBJECT_QUERY}`（主体不由库排序决定）")
    dims_src = _ACCEPTANCE_SRC.split("def _financial_dims(")[1].split("\ndef ")[0]
    check("WHERE company_id = ?" in dims_src,
          "守卫：`_financial_dims` 必须按**声明的**主体过滤（`WHERE company_id = ?`）")
    check("ORDER BY as_of_date, scope, currency" in dims_src,
          "守卫：排序只用于把候选列出来，不用于选一条")
    check("--subject" in _ACCEPTANCE_SRC and "--subject-scope" in _ACCEPTANCE_SRC,
          "守卫：主体是 CLI 参数（可收窄），不是写死的规则")
    check(re.search(r"if not str\(args\.subject or \"\"\)\.strip\(\)", _ACCEPTANCE_SRC) is not None,
          "守卫：缺 `--subject` 必须被拒绝（不得回落到库里第一条）")
    check("--subject-name" in _ACCEPTANCE_SRC
          and re.search(r"if not str\(args\.subject_name or \"\"\)\.strip\(\)",
                        _ACCEPTANCE_SRC) is not None,
          "守卫：主体名称同样只由声明给出，缺即拒绝（不得只印代码，也不得由库补一个）")
    check("report_as_of=self._clock.report_as_of" in _ACCEPTANCE_SRC,
          "守卫：`build_route_context` 必须收到**声明的**报告参考日（不再由财务期末冒充）")
    # 名称的**唯一**来源必须是权威自己的登记：runner 里不得出现「另找一个名字」的查询面貌。
    name_src = _ACCEPTANCE_SRC.split("def _declared_company_name(")[1].split("\ndef ")[0]
    check("declared_company_name" in name_src and "subject_match_status = 'matched'" in name_src,
          "守卫：名称核对读的是**权威登记**（`declared_company_name` + `matched`），不是代码或目录名")
    check(re.search(r"company_name\s*=\s*company\b", _ACCEPTANCE_SRC) is None,
          "守卫：不得再把证券代码填成 `company_name`（`ReportJobInput` / 财务权威 / 财务阶段"
          "三处都必须用核对到的名称）")
    check(RS.ROUTE_CONTEXT_DATE_VERSION == "rc-rd-1"
          and ACC.SUBJECT_DECLARATION_VERSION == "subj-2",
          "守卫：三个接口各自带版本号（分离是版本化接口，不是随手加的字段；声明新增主体名称，"
          "字段集变了故 `subj-1` → `subj-2`）")

    # ==================================================================
    # D. **拒绝报告也要说清主体**（acc-15：acc-14 的真实 run 暴露的缺陷）
    # ==================================================================
    # 现场：`m930_3_acceptance_20260924T005116Z` 在第一节第一次写作调用上被 provider 截断而
    # 整轮拒绝——装配已完成、主体与两个日期都已核对出来，拒绝报告里却**根本没有**
    # `subject_declaration` 块。读者看不出这份缺口属于哪家公司。这里把三种现场都钉住。
    # 只把「按节展开」的产物写手与来源清单一并换成常数桩：它们读的是各节真对象
    # （draft / result / narrative）与来源清单条目，在真实链路里由离线 run 覆盖，与本组
    # 「拒绝报告有没有主体」无关。**其余一律走真的** —— 本组要判的就是真产物。
    _saved = (ACC._trust_root_hashes, ACC._print, ACC._artifacts_payload,
              ACC._before_after_md, ACC._manual_review_md, ACC._source_manifest_md)
    ACC._trust_root_hashes = lambda profile: {"stub": "0" * 64}
    ACC._print = lambda message: None
    ACC._artifacts_payload = lambda state: {
        "drafts": {}, "claims": [], "narratives": {}, "results": {},
        "evaluations": {}, "unresolved": []}
    ACC._before_after_md = lambda state: "# probe\n"
    ACC._manual_review_md = lambda state, gates: "# probe\n"
    ACC._source_manifest_md = lambda state: "# probe\n"

    def _refused_report(*, state, declaration) -> dict:
        with tempfile.TemporaryDirectory(prefix="m930-3-subj-refusal-") as tmp:
            run_dir = Path(tmp) / "m930-3-subj-refusal-probe"
            run_dir.mkdir()
            ACC._write_refusal(
                run_dir, mode=ACC.MODE_REAL, generated_at="2026-01-01T00:00:00Z",
                profile=None, before={}, title="能否构成一次真实验收",
                detail="AcceptanceRefusal: probe", state=state, budget_gate=None,
                declaration=declaration)
            return json.loads((run_dir / "acceptance_report.json").read_text(encoding="utf-8"))

    def _assembled_state() -> SimpleNamespace:
        """装配**已经完成**的现场：来源清单与 `dims` 按 `RealInputs` 的必有字段给全。

        替身照实补齐，不靠 `getattr` 兜底——兜底会让「字段少了」也悄悄算通过。
        """
        return SimpleNamespace(
            budget=None, mode=ACC.MODE_REAL, sections={}, section_errors={},
            assembly_error="", llm_calls=0, run_dir=None, generated_at="",
            inputs=SimpleNamespace(
                company_id="SUBJ", evidence_set_version="es-9",
                # 报告截止日（时钟派生）与下面的 `dims["as_of_date"]`（财务快照日）**故意不同**：
                # 两者一旦被合成一个字段、或让报告日回落到快照期末，下面的判据就会红。
                report_as_of="2026-09-24",
                source_manifest=SM.SourceManifest(
                    policy_version=SM.MANIFEST_POLICY_VERSION,
                    company_id="SUBJ", generated_at="2026-01-01T00:00:00Z",
                    report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
                    financial_data_cutoff="2025-12-31", entries=(),
                    provenance_findings=(), selection=(), primary_document_id=None,
                    primary_document_version=None),
                dims={"subject_declared_by": "cli", "snapshot_id": "snap-7",
                      "as_of_date": "2025-12-31",
                      # 名称是**核对过**的读数（与代码不同）：若有人把读数字段改回去读代码，
                      # 下面那条判据立刻红。
                      "company_name": "样本主体"},
                tasks={}, requirements={}, authorities={}, run_contexts={},
                resolver=None, pack_store=None, trace_sink=None, research_llm=None))

    try:
        # ① 装配已完成：给**已核对**的读数（不是把声明原样抄一遍）。
        _state = _assembled_state()
        _decl = ACC.DeclaredReportInput(
            subject_id="SUBJ", subject_name="样本主体", report_as_of="2026-09-24")
        r1 = _refused_report(state=_state, declaration=_decl)["subject_declaration"]
        check(r1["subject_id"] == "SUBJ" and r1["declared_by"] == "cli"
              and r1["declaration_version"] == ACC.SUBJECT_DECLARATION_VERSION,
              f"被拒且装配已完成：拒绝报告必须说清主体（实际 {r1.get('subject_id')!r}）")
        # **四样东西各自直接可读**：主体 / 主体名称 / 报告截止日 / 财务快照日。读者不得跳进
        # `verified_against` 才能拼出「用的是哪一期财务」，也不得反过来把两条日期轴看成一个。
        check(r1["subject_name"] == "样本主体",
              f"拒绝报告必须直接呈现**核对到**的主体名称（实际 {r1.get('subject_name')!r}）")
        check(r1["subject_name"] != _decl.subject_id,
              "名称对照：这一组的名称与代码**不同**——否则「拿代码当名字」这条缺陷无从区分")
        check("subject_name_declared" not in r1,
              "已核对时不得再出现 `subject_name_declared`：同一件事两个字段会被读成两个来源")
        check(r1["report_as_of"] == "2026-09-24",
              f"拒绝报告必须直接呈现报告截止日（时钟派生；实际 {r1.get('report_as_of')!r}）")
        check(r1["snapshot_as_of_date"] == "2025-12-31",
              f"拒绝报告必须直接呈现财务快照日（实际 {r1.get('snapshot_as_of_date')!r}）")
        check(r1["report_as_of"] != r1["snapshot_as_of_date"],
              "反面对照：本组的两个日期**不同**——否则「两条轴」与「一条轴」在这里无从区分")
        check(r1["report_as_of"] != _state.inputs.dims["as_of_date"],
              "报告截止日**不得**回落到财务快照期末（`rc-rd-1` 修掉的正是这件事）")
        check(r1["verified_against"]["financial_snapshot_id"] == "snap-7"
              and r1["verified_against"]["evidence_set_version"] == "es-9"
              and r1["verified_against"]["source_manifest_company_id"] == "SUBJ",
              "被拒的报告同样要给出**已核对**的快照身份、Evidence 版本与清单主体"
              "（不只是把声明原值抄一遍）")
        check("snapshot_as_of_date" not in r1["verified_against"]
              and "report_as_of" not in r1["verified_against"],
              "两个日期已平列到块顶层 ⇒ 不得在 `verified_against` 里再放一份（两份同样的日期"
              "会被读成两个来源）")

        # ② 装配尚未发生：只有**已声明、未经核对**的信息，`verified_against` 必须是 `None`。
        r2 = _refused_report(state=None, declaration=_decl)["subject_declaration"]
        check(r2["subject_id"] == "SUBJ" and r2["verified_against"] is None,
              "装配之前的拒绝：声明照实报告，核对面必须是 None"
              "（`{}` 会被读成「核对过、结果为空」）")
        check(r2["report_as_of_declared"] == "2026-09-24"
              and r2["narrowing"]["snapshot_as_of"] is None,
              "装配之前只呈现**已声明**的值（报告截止日的声明值；快照那一栏是收窄条件，可为空）")
        check(r2["subject_name_declared"] == "样本主体" and "subject_name" not in r2,
              "装配之前名称同样是**已声明、未核对**：只以 `subject_name_declared` 出现，"
              "读数字段名 `subject_name` 不得出现（缺字段 ≠ 读数为空，也不补 `None`）")
        check("report_as_of" not in r2 and "snapshot_as_of_date" not in r2,
              "装配之前**不得**出现读数字段名：那两个字段的含义是「已核对」，"
              "这里没有核对过。缺字段 ≠ 读数为空——所以也不给它们补一个 `None`")
        check("未" in r2["note"], "并写明「尚未核对」，不留下可被读成结论的空白")

        # ③ 连声明都没有：不得编一个主体出来。
        r3 = _refused_report(state=None, declaration=None)["subject_declaration"]
        check(r3["subject_id"] is None and r3["verified_against"] is None
              and r3["declaration_version"] is None,
              "没有声明可报告时三处都是 None（不得回落到任何默认主体）")
        check("report_as_of" not in r3 and "snapshot_as_of_date" not in r3
              and "report_as_of_declared" not in r3,
              "连声明都没有时，读数位与声明位都不出现（不得凭空造一个日期）")

        # ④ 反面对照：三种现场必须给出**不同**的载荷。若构造退化成「永远同一份」，
        #    上面四条会全绿而缺陷照旧——这一条就是为它准备的。
        check(len({json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True),
                   json.dumps(r3, sort_keys=True)}) == 3,
              "三种现场（已核对 / 只有声明 / 无声明）必须是三份不同的载荷，不是同一份模板")
        check(r1["verified_against"] is not None and r2["verified_against"] is None
              and "report_as_of_declared" in r2 and "report_as_of_declared" not in r1,
              "「已核对」与「未核对」的差别必须落在载荷上（不是只落在 note 的措辞里）")
        check("report_as_of" in r1 and "report_as_of" not in r2,
              "同一件事的正反两面：读数字段只在核对过的现场出现")

        # ⑤ 正常路径与拒绝路径必须是**同一份**构造，不是各写一份。
        r4 = ACC._subject_declaration_block(_state)
        check(r4 == r1, "正常报告与拒绝报告共用同一份 `subject_declaration` 构造（不得各写一份）")

        # ⑥ 反面对照：同一份构造喂进「报告日 = 快照期末」的旧口径现场，两个字段就会相等。
        #    它是第 ①组的判别力来源：若有人把 `report_as_of` 改成读 `dims["as_of_date"]`
        #    （`rc-rd-1` 修掉的那件事又回来了），第 ① 组立刻从「2026-09-24」变成
        #    「2025-12-31」而判红——这一条证明那组判据读的是现场，不是常量。
        _conflated = _assembled_state()
        _conflated.inputs.report_as_of = _conflated.inputs.dims["as_of_date"]
        r5 = ACC._subject_declaration_block(_conflated)
        check(r5["report_as_of"] == r5["snapshot_as_of_date"] == "2025-12-31",
              "对照现场（旧口径：报告日 = 快照期末）下两个字段会相等——第 ① 组的「两者不同」"
              "因此是有信息量的判据，不是恒真的空断言")
        check(r5["report_as_of"] != r1["report_as_of"],
              "同一份构造在两个现场给出不同的报告截止日 ⇒ 它读的是现场，不是常量")
        check(ACC.RUNNER_VERSION == "m930-3-acc-40"
              and ACC.ACCEPTANCE_REPORT_SCHEMA_VERSION == "m930-3-acc-report-34",
              "拒绝报告载荷形状变了 ⇒ runner 与报告 schema 一并升版"
              "（acc-17 / report-16：写作链按 aspect 范围确定性分批 ⇒ 被拒记录新增逐批读数，"
              "「一次尝试」不再等于「一次调用」；acc-18 / report-17：`anp-3` 键层级进产物 ⇒ "
              "`TREE_NAVIGATION` 回读平列两套键与生效层级，`nav_read_scope` 按层级分组；"
              "acc-19 / report-18：研究侧调用进共享门（门先装后研究 + 研究登记为独立轴）⇒ "
              "产物新增逐轴账与已发生的研究尝试；acc-20 / report-19：研究上限改为逐 topic "
              "自派生（15 × 该 topic aspect 数）⇒ 上限读数按 topic 平列；acc-21 / report-20："
              "跨文档「材料去向」的 Writer 清单列改为查过才给布尔值（未查过记 null + 原因），"
              "修真实 run r3 把「没查过」印成 false ⇒ 该块新增 `writer_manifest_status` / "
              "`writer_manifest_query`；acc-22 / report-21：定点返修 C 批 C1 把导航规则升到 "
              "`anp-4`（topic 标题段先按 question 归属再进祖先层）⇒ 同一批回读字段的**取值语义**"
              "换了，读产物的人据此判读「这条材料按哪一层的键读来」；acc-23 / report-22："
              "前置修复 T 批（T1 材料包读回产物进 ARTIFACTS 与 manifest；T2 台账成员按四轴核对；"
              "T3 研究轴拒收截断）⇒ 被拒路径也写 `material_pack.md`/`material_pack.json`；"
              "acc-24 / report-23：r4 后的业务内容定点返修（① 失败路径不再因 `len(None)` 丢掉"
              "整份报告，新增 `status='assembly_error'` 降级档；② A7 的 `recovered` 判据由「该节"
              "有没有 `SectionDraft`」换成批次谱系并新增两桶）；acc-25 / report-24：定点返修 ⑤ "
              "内容形态分流（勾选表单行与被拒路径的形态读法进材料包与写手输入面）；"
              "acc-26 / report-25：定点返修 ② 逐栏目取料（读根资格 `anp-4` → `anp-5`、条目 wire "
              "`anps-3` → `anps-4`）⇒ 拒绝报告同样会印出新的 `fallback_reason` "
              "`no_anchored_read_root` 与新的 `discard_reason`，报告顶层状态词表与截断策略块都变了；"
              "acc-27 / report-26：定点返修 ⑥ 行业节「仅用标点拼接 Claim」被拒之后的**有界**"
              "定向重组织（判据一字未改；正文判据面新增第 9 条「不得替系统自报检索 / 核验」，"
              "新增 `norg-reorg-1`）⇒ A3 证据块新增 `self_reported_provenance`，报告正文面的"
              "判读窗口随之变化；acc-28 / report-27：定点返修 §三/1（一条填错的补件申请不再"
              "杀死整节）⇒ 诚实性说明多出被拒申请的读数，`failure_diagnostics.json` 逐节新增 "
              "`rejected_follow_up_applications`、`follow_up_needs.json` 升到 `/2`；拒绝报告"
              "同样带这一块；acc-29 / report-28：定点返修第二段（勾选表单行的**栏目归属**与"
              "**支撑资格**）⇒ 材料包资格列五列 → 六列（新增 `trailing_content`，读法 `tmr-1` → "
              "`tmr-2`、载荷 `material-pack/2` → `/3`），整束拒绝与逐候选原因各多一条 "
              "`path_b_ineligible_material_scope`（`proposal-set-rejections/5` → `/6`），"
              "写作策略 `pw-12` → `pw-13`；拒绝报告同样带这两块）；acc-30 / report-29：本轮"
              "「合格材料 → 合法 Claim → 可读章节」的定点返修（① 蕴含边 `cer-3` → `cer-4`；"
              "③ 生产 need 构造器 `anb-1` + 外部授权执行前门）⇒ 外部来源块整块换读法"
              "（`external-retrieval/1` → `/2`：原因码封闭集 + 终态封闭枚举 + 需要侧回读 + "
              "`authorization` 子块）；拒绝报告同样带这一块——本轮若无联网授权，拒绝报告里的"
              "「未检索」同样不得被读成「已证明没有可用的外部来源」）；acc-31 / report-30："
              "r7b **后**定点返修第一段（A2 的单元格判据与已裁决的分量规则对齐）⇒ A2 财务可读性"
              "块的 `problems` 读法换了（旧读者把只差一个标点的正确代理单元格读成内容缺陷），"
              "拒绝报告同样带这一块）；acc-34 / report-33：指令 D §二（b）来源归属轴的**读者面**"
              "（`srattr-1`）⇒ 新增 `source_attribution.json` / `source_attribution.md` 两个产物与"
              "报告侧 `source_attribution` 指针块。归属语只由**系统**从已登记身份确定性渲染"
              "（材料名 + 登记 `id@版本` + 精确页码 + 可核实披露日；不可核实一律「披露日未知」，"
              "不得用入库时间／PDF 元数据／上传时间／财务期末顶替），写者一个字也不能写；它"
              "**不改正文、不改正文指纹**，只作逐句对账，拒绝报告同样带这一块；"
              "acc-35 / report-34：指令 D §三**失败侧**的门前留存与诊断（`pgr-1`）⇒ 新增 "
              "`pre_gate_draft.json` / `pre_gate_draft.md` 两个产物与报告侧 `pre_gate_draft` "
              "指针块，`proposal-set-rejections/8` → `/9`（每条被拒记录多出 `retained_pre_gate`："
              "整束被拒时门前已经写出来的逐批草稿正文 + 出处轴，逐字标注「未核验、不可发布」）。"
              "判据一字未减：它不参与任何门的判定、不进正文与预览、也不放宽任何门；"
              "拒绝报告同样带这一块）；acc-36 / report-34：业务取材纵链修复 §一 ⇒ 诊断逐 aspect "
              "新增 `column_unmet`（逐条 typed 栏目未达原因 + 派发去路计数）与逐节 "
              "`column_unmet_summary`，`failure-diagnostics/2` → `/3`。判据一字未减：五条原因"
              "彼此不可互推，不得合并成笼统的 `coverage_gate_not_met`，也不得写成「语料里没有」；"
              "报告载荷未变，故报告 schema 停在 report-34，拒绝报告同样带这一块）；"
              "acc-37 / report-34：业务取材纵链修复 §二 的**表对象信道读法**（材料包读回不再把"
              "**表对象**印成「内容形态：读不出」：另成 `kind=table_object` 一族并读同信封的 "
              "`reading_policy`——`permitted_use` 与逐条排除项，节级新增 "
              "`table_object_material_ids`，`material-pack/3` → `/4`）。判据一字未减："
              "「这张表能读」与「表里的数字能以它为准」是两条**正交**声明；报告载荷同样未变，"
              "报告 schema 仍停在 report-34，拒绝报告同样带这一块）；acc-38 / report-34："
              "业务取材纵链修复 §三 的**替身选材与组织**（过长原句按小句边界切成有界原子逐条"
              "过滤；候选原子必须自带陈述对象，无主语残片不再进正文并逐条记 typed 原因；材料按"
              "来源角色稳定重排后提案；组织侧每个接缝各取一个中性连接语）。判据一字未减：只改"
              "替身提案与行文；报告载荷同样未变，报告 schema 仍停在 report-34，"
              "拒绝报告同样带这一块）；acc-39 / report-34：§0.18 W8 单通道的**读取面**"
              "（目标表的正式材料改由图侧单通道产出，信封种类由 `tom-1` 换成 `gtm-1`；读回侧"
              "过去只认 `tom-1` ⇒ 新信道的表材料落回「读不出形态」，现同时认两条并带出**实际"
              "观察到的** `envelope_kind`，`material-pack/4` → `/5`）。判据一字未减；报告载荷"
              "同样未变，报告 schema 仍停在 report-34，拒绝报告同样带这一块）；"
              "acc-40 / report-34：M930-3 定点业务纠正① 的**勾选行栏目归属**（读法 `tmr-2` → "
              "`tmr-3`：所问事项**只**取行内前缀，行内没写主语时这一列留空，**不再**回指所在"
              "节点标题，空所问事项 fail-closed 且材料一份未删）。判据一字未减；报告载荷同样"
              "未变，报告 schema 仍停在 report-34，拒绝报告同样带这一块）")
    finally:
        (ACC._trust_root_hashes, ACC._print, ACC._artifacts_payload,
         ACC._before_after_md, ACC._manual_review_md,
         ACC._source_manifest_md) = _saved

    # ==================================================================
    # E. 主体抽取的**时间状语边界**（`nrules-18` / `ng-18`）
    # ==================================================================
    # 现场：真实运行 `m930_3_cited_real_company_cp22_r1` 的 `s0020`/`s0023` 被判
    # `unsourced_subject_surface` 硬错，报出的「主体名」是 `报告期末公司` —— 来源里根本没有
    # 这条主体名，它**也不是**主体名：`截至报告期末公司巧克力换电建站超1,000座` 里的
    # `截至报告期末` 整段是**时间状语**。
    #
    # 成因可复算，且**只有**一个：`_entity_name_run` 的回扫是**字符级**的，数字处不停留时
    # （`2024年末公司`）整段状语被当成字号；即使回扫被 `截至` 截开，剥前缀当时是**一趟**
    # 长度降序——`报告期` 排在 `截至` **之前**被检查，等 `截至` 把它剥成 `报告期末` 时那一趟
    # 已经过去了，`报告期末` 就被留成了字号。
    #
    # 本组把**两个方向**同时钉住。判据实现（`_uncovered` 的裸子串逐字比对）**一字未改**：
    # 变的只是**判定集**，故三处版本一起前进（`nrules-18` / `ng-18` / `scp-9`）。这里的正例是
    # 「不得再造出伪 token」，反例是「真正错误的主体必须**仍然**命中」——两者缺一不可：只钉正例
    # 会让「把主体名抽取器整个删掉」也变成绿。
    #
    # 主体抽取这一格与核对器政策版本的关系**到此为止**：`nrules-18`/`ng-18` 是判定集变过的
    # 那两处。核对器侧的政策串此后还可以因**其它**原因前进（本批就是 `scp-10` 的阻断集聚合
    # 口径），因此这里只钉「不得回退到 `scp-9` 之前」——写死一个精确值会让任何一次**与本组
    # 无关**的合法升版把这条钉子变红，而那条红线本来该由核对器自己的回归组去管。
    check(NS.NARRATIVE_RULES_VERSION == "nrules-18"
          and NS.NARRATIVE_GATE_VERSION == "ng-18"
          and SC.SENTENCE_CHECK_POLICY_VERSION in _SUBJECT_PINNED_CHECK_POLICIES,
          "E：判定集变了 ⇒ `nrules-18` / `ng-18` 必须一起前进，且核对器政策不得回退到 "
          "`scp-9` 之前（`nrules-10` 立下的规矩：不得共用旧版本号）；"
          f"实测核对器政策 {SC.SENTENCE_CHECK_POLICY_VERSION!r}")

    #: 正例的现场（`s0020` 的原句形态）：材料里有逗号断开，写手把逗号去掉后黏出了伪主体名。
    _E_MATERIAL = "换电业务方面，截至报告期末，公司巧克力换电建站超1,000座，分布于全国45座城市。"
    _E_SENTENCE = "换电业务方面，截至报告期末公司巧克力换电建站超1,000座，分布于全国45座城市。"
    check(NS.entity_name_tokens(_E_SENTENCE) == (),
          "E 正例：`截至报告期末公司…` 的字号是**整条时间状语**，不得产出主体名 token"
          "（修前它产出 `报告期末公司`，凭空吃一条 `unsourced_subject_surface`）")
    check(SC._uncovered(NS.entity_name_tokens(_E_SENTENCE), [_E_MATERIAL]) == (),
          "E 正例（判据轴）：`subject_surface` 轴上这批表面全部被来源逐字覆盖 —— "
          "这正是 `s0020` 那条硬错的消除方式：判据没放宽，是**判定集**不再产出伪 token")
    check(NS.entity_name_tokens("截至报告期末公司巧克力换电建站超1,000座") == ()
          and all(NS.entity_name_tokens(text) == () for text in (
              "2024年度公司主营业务收入为100亿元。",
              "2024年末公司货币资金余额为100亿元。",
              "2024年期末公司资产负债率为50%。",
              "2024年度末公司资产负债率为50%。",
              "本报告期末公司资产负债率为50%。",
              "报告期内公司营业收入为100亿元。",
              "当期公司营业收入为100亿元。",
          )),
          "E 正例：时间状语**整类**（`年度`/`年末`/`年期末`/`年度末`/`本报告期末`/`报告期内`/"
          "`当期`）都不得产出主体名 token；前导量词（`2024年期末` 的 `年`）由 "
          "`_ENTITY_TEMPORAL_LEAD_QUANTIFIERS` 归一后再查封闭集合")

    #: `nrules-18` 补的那一格：`nrules-17` **没修干净**。后缀匹配把「本公司」切开，`公司`
    #: 命中在 `本` 之后，回扫得 `报告期末本`，剥一趟前缀得 `末本` ⇒ token `末本公司`——
    #: 这个词在任何来源里都不存在，于是凭空吃一条 `unsourced_subject_surface`。真实年报里
    #: 「报告期末本公司…」是最常见的写法之一，因此这一格不是人造的边角。
    _E18_POOL = ("公司报告期末应收账款账面价值为100亿元。",)
    check(NS.entity_name_tokens("报告期末本公司应收账款账面价值为100亿元。") == (),
          "E 正例（`nrules-18`）：`报告期末本公司` 的字号是「时间状语 + 自称」，不得产出主体名 "
          "token（修前它产出 `末本公司`）")
    check(SC._uncovered(
              NS.entity_name_tokens("报告期末本公司应收账款账面价值为100亿元。"),
              _E18_POOL) == (),
          "E 正例（`nrules-18`，判据轴）：判据没放宽，是**判定集**不再产出伪 token ⇒ "
          "`subject_surface` 轴上没有未覆盖表面")
    check(NS.entity_name_tokens("期末本公司应收账款账面价值为100亿元。") == ()
          and NS.entity_name_tokens("截至报告期末本公司应收账款账面价值为100亿元。") == ()
          and NS.entity_name_tokens("2024年末本公司应收账款账面价值为100亿元。") == (),
          "E 正例（`nrules-18`）：同一格在 `期末本公司` / `截至报告期末本公司` / "
          "`2024年末本公司` 三种前导下都不得产出伪 token")
    #: 反例（`nrules-18`）：真名里带「本」的公司名**不得**被这多剥的一刀误伤。
    check(NS.entity_name_tokens("宁波均胜电子股份有限公司营业收入为100亿元。")
          == ("宁波均胜电子股份有限公司",)
          and NS.entity_name_tokens("中国石油天然气集团有限公司营业收入为100亿元。")
          == ("中国石油天然气集团有限公司",),
          "E 反例（`nrules-18`）：真主体名（含「本」字或长名）必须**仍然**逐字产出 —— "
          "多剥的那一刀只在「去掉尾部 `本` 之后剩下整条时间状语」时生效")
    check(NS.entity_head_nouns("报告期末本公司应收账款账面价值为100亿元。") == ("公司",),
          "E 保真方向（`nrules-18`）：`entity_head_nouns` 仍必须取到 `公司` 这一格读数 —— "
          "授权轴再收窄一格，**不**把代价转嫁给保真轴")

    #: 反例：**真正错误的主体**必须仍然命中。三条各自对准一条不同的失败模式。
    _E_POOL = ("宁德时代新能源科技股份有限公司2024年营业收入为100亿元。",)
    check(NS.unauthorized_surfaces("比亚迪股份有限公司2024年营业收入为100亿元。",
                                   _E_POOL) == ("比亚迪股份有限公司",),
          "E 反例 1：来源里**没有**的公司名必须仍然被报为未授权表面 —— "
          "时间状语修复不得让真正的主体错配漏过")
    check(NS.unauthorized_surfaces("宁德时代新能源科技有限公司2024年营业收入为100亿元。",
                                   _E_POOL) == ("宁德时代新能源科技有限公司",),
          "E 反例 2：**少一个词**的公司名（缺 `股份`）同样必须命中 —— 判据仍是逐字比对，"
          "不做任何「相近即通过」的归一")
    check(NS.entity_name_tokens("根据中国人民银行的规定，公司应报送报表。")
          == ("中国人民银行",)
          and NS.entity_name_tokens("发行人母公司") == ()
          and NS.entity_name_tokens("年度报告同时披露公司") == ()
          and NS.entity_name_tokens("本公司") == (),
          "E 反例 3：**结构性前缀**（`根据` / `发行人` / `年度报告同时披露` / `本公司`）"
          "的历史行为一字不动 —— 剥前缀仍必须剥到落空，修复只多认「整条时间状语」这一类")

    #: 保真方向不得被连累：授权轴收窄**不能**顺手把 `entity_head_nouns` 也收窄。
    check(all(NS.entity_head_nouns(text) == ("公司",) for text in (
              _E_SENTENCE,
              "2024年度公司主营业务收入为100亿元。",
              "2024年末公司货币资金余额为100亿元。",
              "本报告期末公司资产负债率为50%。",
          )),
          "E 保真方向：`entity_head_nouns` 仍必须取到 `公司` 这一格读数 —— "
          "把授权轴的修复代价转嫁给保真轴是不允许的（`_entity_tokens(drop_temporal_runs=False)`）")
    check(NS.entity_name_tokens("宁德时代新能源科技股份有限公司营业收入为100亿元。")
          == ("宁德时代新能源科技股份有限公司",)
          and NS.entity_name_tokens("中国石油天然气集团有限公司2024年营业收入为100亿元。")
          == ("中国石油天然气集团有限公司",)
          and NS.entity_name_tokens("年月科技有限公司2024年营业收入为100亿元。")
          == ("年月科技有限公司",)
          and NS.entity_name_tokens("月度数据服务有限公司2024年营业收入为100亿元。")
          == ("月度数据服务有限公司",),
          "E 保真方向：真实法人名一字不缩；**以量词字开头**的真字号（`年月科技`/`月度数据服务`）"
          "也不得被前导量词归一误删 —— 归一后仍必须**整串**等于封闭集合里的某一条")
    check(NS._is_temporal_run("年期末") and NS._is_temporal_run("报告期末")
          and not NS._is_temporal_run("年月科技") and not NS._is_temporal_run("")
          and not NS._is_temporal_run("末"),
          "E：`_is_temporal_run` 本身是封闭集合查表（含剥一个前导量词），"
          "空串与 1 字残余一律不是 —— 它不产生任何「看着像状语」的语义判断")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

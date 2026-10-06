"""M930-3 批次 A 反例集：来源清单、披露日期口径与「单一时钟瞬间」。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_batch_a_sources`

覆盖的裁决条目（M930-3 批次 A 补充裁决 一.1–一.6）与**反例**：

1. **时钟**（一.4 / O-11）
   - 时区跨日：UTC 2026-09-23T16:30 → 报告生成日 2026-09-24（UTC+8）；
   - `generated_at` 与 `report_as_of` 由**同一次采样**派生：`generated_at` 恒为 UTC 时刻，
     `report_as_of` 恒为该时刻按配置时区换算的日子（同一个瞬间不可能给出两个日子）；
   - 反例：naive datetime 被拒；缺时区资产 / 空 `report_timezone` / 非法 IANA 名一律
     fail-closed（**不回落系统时区**，不造默认值）；
   - 反例：`run_stamp` 取自同一瞬间，不与目录外的另一次采样混用。
2. **期间身份**（一.4 / 一.5）
   - 财务期末 ≠ 报告生成日：清单里两个字段并列且互不推导；
   - 反例：把 `FinancialSnapshot.as_of_date` 当 `report_as_of` 会被本组直接证伪
     （同一个 fixture 下两者是不同的值）。
3. **材料保留**（一.1 / 一.2）
   - 全部已登记材料都进清单：3 份进 3 份，未被选中的也在列且带 reason_code；
   - 同类较新且可核实者优先：2025 年报胜 2024 年报，旧材料**不**被丢弃；
   - 跨文档类型：不同文档类型不互相替换，未绑定的一份带 typed reason_code（单文档接口限制）。
4. **披露日期**（一.3 / 一.5 / O-12）
   - 只有月粒度的封面日期**不**升格为披露日期（`date is None`），只作另行标注的线索；
   - 入库时间原样登记但**绝不**被当作披露日期；
   - 反例：`documents.created_at` 的值必不出现在 `disclosure.date` 里。
5. **类型判断**（一.3）
   - 只看文档正文封面：文件名/路径不参与判断（`NDSD_KCZ_2026.pdf` 的名字里既有 `kcz`
     也有 `20`，文件名口径会给出 `debt_circular`；本组证明判断取的是正文里的
     「募集说明书」而不是文件名）；
   - 最长标记优先：半年度报告**不得**被判成年度报告；
   - 注册值与判断分列，二者不一致时 SourcePolicy 影响可复算（检索加权 / 索引优先级 /
     类型标签），且**不**暗改注册值。
6. **取值表不漂移**
   - 本模块的选择取值表必须与 `retrieval/retriever.py` / `retrieval/indexer.py` 的既有声明一致
     （按源码文本对账，不 import 重量级模块）。

公司无关：一律用合成公司与合成文档，不引入 300750 / 宁德时代 / 固定页码分支。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import live_span_source as LSS
from evidence import store as estore
from harness import report_clock as RC
from harness import source_manifest as SM

REPO = Path(__file__).resolve().parent.parent

#: 合成公司：本模块不引入任何真实公司常量。
_COMPANY = "ACME"
#: 合成文档的内容版本（形状与 evidence.store 一致，取值无关）。
_DOCV = "sha256-" + "a" * 16
_SETV = "set-" + "b" * 12
_TS = "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 夹具：一个只读的合成 Evidence 库（绝不触碰真实库）
# ---------------------------------------------------------------------------

def _seed_documents(db: Path, rows: list[tuple]) -> None:
    """按 `evidence/store.py` 的真实 DDL 建一个临时库，只登记 documents / evidence_sets /
    evidence_blocks 三张表（清单只读这三张）。"""
    estore.init_db(db)
    conn = sqlite3.connect(db)
    try:
        for document_id, version, source_name, source_type, created in rows:
            conn.execute(
                "INSERT INTO documents (company_id, document_id, document_version,"
                " source_name, source_path, source_type, material_group, file_sha256,"
                " file_size, page_count, declared_company_name, detected_company_names,"
                " parser_version, status, quality_flags, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_COMPANY, document_id, version, source_name,
                 f"/synthetic/{source_name}", source_type, "company_industry",
                 "f" * 64, 1, 1, None, "[]", "v1", "current", "[]", created))
            conn.execute(
                "INSERT INTO evidence_sets (company_id, document_id, document_version,"
                " evidence_set_version, dependency_versions, status, block_count, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (_COMPANY, document_id, version, _SETV,
                 '{"schema":"1","parser":"v1","builder":"1"}', "current", 0, created))
        conn.commit()
    finally:
        conn.close()


def _seed_blocks(db: Path, document_id: str, version: str,
                 blocks: list[tuple[int, int, str]]) -> None:
    """插入若干 Evidence 块（页码, 块序, 文本）。published_at / report_period 一律留空——
    真实库就是全空，清单必须能在这种前提下工作。"""
    conn = sqlite3.connect(db)
    try:
        for page, block_index, text in blocks:
            conn.execute(
                "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id,"
                " document_id, document_version, evidence_set_version, source_name,"
                " source_type, source_uri, page_number, block_index, section_path,"
                " evidence_type, text, structured_payload, report_period, published_at,"
                " entities, quality_flags, content_hash, builder_version, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{document_id}-p{page}b{block_index}", "1", _COMPANY, document_id,
                 version, _SETV, "synthetic.pdf", "annual_report", None, page,
                 block_index, "[]", "paragraph", text, None, None, None, "[]", "[]",
                 "h", "1", _TS))
        conn.commit()
    finally:
        conn.close()


def _clock_cfg(tmp: Path, *, timezone: str = "Asia/Shanghai") -> RC.ClockConfig:
    """按真实资产的形状写一份临时时钟配置（不改仓库资产）。"""
    text = (f"policy_id: {RC.POLICY_ID}\npolicy_version: {RC.POLICY_VERSION}\n"
            f"schema_version: {RC.SCHEMA_VERSION}\nstatus: approved\n"
            f'created_at: "2026-01-01"\nreport_timezone: {timezone!r}\n'
            f'report_timezone_basis: "fixture"\n')
    return RC.parse_clock_config(text)


def _entry(**kw):
    """构造一个 SourceManifestEntry（只覆盖需要变化的字段）。"""
    base = dict(
        company_id=_COMPANY, document_id="DOC_A", document_version=_DOCV,
        file_sha256="f" * 64, file_size=1, page_count=1, source_name="a.pdf",
        source_path="/synthetic/a.pdf", registry_status="current",
        material_group="company_industry", registered_source_type="annual_report",
        evidence_set_version=_SETV,
        type_judgment=SM.DocumentTypeJudgment(
            "年度报告", "年度报告", "annual_report", "annual_report",
            SM.CoverEvidence("X公司2024年年度报告2025年03月", "p1 b0", 1, 0),
            "document_cover"),
        disclosure=SM.DisclosureDateState(
            None, "unknown", None, None, "fixture", _TS),
        content_report_period=SM.ContentReportPeriod(
            "2024-01-01..2024-12-31", "2024-12-31", "verified", "fixture", 1, "fixture"),
        policy_effect=SM.judge_policy_effect("annual_report", SM.DocumentTypeJudgment(
            "年度报告", "年度报告", "annual_report", "annual_report", None, "document_cover")),
        eligibility="eligible_current", eligibility_reason="fixture")
    base.update(kw)
    return SM.SourceManifestEntry(**base)


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

    # ------------------------------------------------------------------
    # 1. 时钟：时区跨日 + 两个字段同源
    # ------------------------------------------------------------------
    cfg = _clock_cfg(Path("."))

    cross = RC.capture_report_clock(
        config=cfg, now_utc=dt.datetime(2026, 9, 23, 16, 30, tzinfo=dt.timezone.utc))
    check(cross.generated_at == "2026-09-23T16:30:00Z",
          f"generated_at 必须是该瞬间的 UTC 时间戳（实际 {cross.generated_at!r}）")
    check(cross.report_as_of == "2026-09-24",
          f"时区跨日：UTC 2026-09-23T16:30 在 Asia/Shanghai 已是 2026-09-24"
          f"（实际 {cross.report_as_of!r}）")

    same_day = RC.capture_report_clock(
        config=cfg, now_utc=dt.datetime(2026, 9, 23, 2, 0, tzinfo=dt.timezone.utc))
    check(same_day.report_as_of == "2026-09-23",
          f"非跨日时区换算不得改变日子（实际 {same_day.report_as_of!r}）")

    # 同源：一次采样只能给出一个日子；两个字段的信息合起来必须唯一确定那个瞬间。
    check(RC.run_stamp(cross) == "20260923T163000Z",
          f"run_stamp 必须来自同一瞬间的 UTC 值（实际 {RC.run_stamp(cross)!r}）")
    check(cross.report_as_of != same_day.report_as_of
          and cross.generated_at != same_day.generated_at,
          "不同瞬间必须给出（可区分的）不同派生值——若这里相等，说明有一个字段没有跟着瞬间走")

    # 反例：naive datetime 不接受（不接受「假设本地时区」）。
    naive_rejected = False
    try:
        RC.capture_report_clock(
            config=cfg, now_utc=dt.datetime(2026, 9, 23, 16, 30))
    except RC.ReportClockError:
        naive_rejected = True
    check(naive_rejected, "naive datetime 必须被拒（不得假设时区）")

    # 反例：缺资产 / 空时区 / 非法时区名一律 fail-closed。
    missing_asset = False
    try:
        RC.load_clock_config(Path("templates/policies/__no_such_clock__.yaml"))
    except RC.ReportClockError:
        missing_asset = True
    check(missing_asset, "缺时钟资产必须 fail-closed（不得回落系统时区）")

    empty_tz = _clock_cfg(Path("."), timezone="")
    check(not RC.validate_clock_config(empty_tz).valid,
          "空 report_timezone 必须校验失败")
    bad_tz = _clock_cfg(Path("."), timezone="Mars/Olympus")
    bad_result = RC.validate_clock_config(bad_tz)
    check(not bad_result.valid and "IANA" in "｜".join(bad_result.errors),
          f"非法 IANA 时区名必须校验失败（实际 {bad_result.errors}）")
    bad_load = False
    try:
        with tempfile.TemporaryDirectory(prefix="m930-3-clk-") as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("policy_id: report_clock_v1\npolicy_version: v1\n"
                         "schema_version: report-clock-v1\nreport_timezone: Mars/Olympus\n",
                         encoding="utf-8")
            RC.load_clock_config(p)
    except RC.ReportClockError:
        bad_load = True
    check(bad_load, "非法时区资产的 load 必须抛错（校验不是装饰）")

    # 仓库资产本身必须合法（否则所有真实 run 一起 fail-closed）。
    real_cfg = RC.load_clock_config(REPO / RC.DEFAULT_CLOCK_ASSET_PATH)
    check(real_cfg.report_timezone == "Asia/Shanghai",
          f"仓库时钟资产的时区（实际 {real_cfg.report_timezone!r}）")

    # ------------------------------------------------------------------
    # 2. 期间身份：财务期末 ≠ 报告生成日
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="m930-3-batch-a-") as tmp:
        tmpdir = Path(tmp)

        # 一份合成年报：封面给月粒度日期，释义给报告期；另一份募集说明书封面无日期。
        db = tmpdir / "evidence.db"
        _seed_documents(db, [
            ("DOC_ANNUAL_OLD", _DOCV, "ACME_2024_year.pdf", "annual_report", _TS),
            ("DOC_ANNUAL_NEW", "sha256-" + "b" * 16, "ACME_2025_year.pdf", "other", _TS),
            ("DOC_PROSPECTUS", "sha256-" + "c" * 16, "ACME_KCZ_2026.pdf", "debt_circular", _TS),
        ])
        _seed_blocks(db, "DOC_ANNUAL_OLD", _DOCV, [
            (1, 0, "ACME公司\n\n2024年年度报告\n\n2025年03月"),
            (7, 0, "报告期 指 2024年1月1日至2024年12月31日"),
        ])
        _seed_blocks(db, "DOC_ANNUAL_NEW", "sha256-" + "b" * 16, [
            (1, 0, "ACME公司\n\n2025年年度报告\n\n2026年3月"),
            (9, 1, "报告期 指 2025年1月1日至 2025年12月31日"),
        ])
        _seed_blocks(db, "DOC_PROSPECTUS", "sha256-" + "c" * 16, [
            (1, 0, "ACME公司\n\n2026年度绿色科技创新债券募集说明书"),
        ])

        manifest = SM.build_source_manifest(
            db_path=db, company_id=_COMPANY,
            generated_at="2026-09-23T16:30:00Z", report_as_of="2026-09-24",
            report_timezone="Asia/Shanghai", financial_data_cutoff="2026-03-31")

        # 2a. 全部已登记材料都进清单（登记 ≠ 选用）。
        check(len(manifest.entries) == 3,
              f"3 份已登记材料必须全部进清单（实际 {len(manifest.entries)}）")
        check(all(e.eligibility == "eligible_current" for e in manifest.entries),
              "三份都是 current 且有 current set，资格不得被静默降级")

        # 2b. 期间身份：财务期末与报告生成日并列且互不推导。
        check(manifest.financial_data_cutoff == "2026-03-31"
              and manifest.report_as_of == "2026-09-24",
              f"财务期末({manifest.financial_data_cutoff!r}) 与报告生成日"
              f"({manifest.report_as_of!r}) 必须是两个不同的值")
        check(manifest.financial_data_cutoff != manifest.report_as_of,
              "反例：不得由财务期末推导报告生成日（fixture 里两者本就不同）")

        # 2c. 同类较新且可核实者优先；旧材料**留在源集里**（`sm-2`：不再叫「未被选中」）。
        check(manifest.primary_document_id == "DOC_ANNUAL_NEW",
              f"同类型（年度报告）中较新且可核实者必须成为当前锚"
              f"（实际 {manifest.primary_document_id!r}）")
        check(manifest.current_state == "resolved",
              f"锚可解析时 current_state 必须是 resolved（实际 {manifest.current_state!r}）")
        old = [s for s in manifest.selection if s.document_id == "DOC_ANNUAL_OLD"]
        check(len(old) == 1 and old[0].reason_code == "same_series_older_period",
              f"旧同系列材料必须带 typed 理由留在源集里（实际 {old}）")
        check(old[0].source_role == "history_and_conflict_source"
              and old[0].retrieval_order >= 0,
              f"旧同系列材料是 history_and_conflict_source 且有序位（实际 "
              f"{old[0].source_role!r}/{old[0].retrieval_order}）")
        check("保留在源集里按主题检索" in old[0].reason,
              f"旧材料理由必须写明它仍在源集里（实际 {old[0].reason[:80]!r}）")

        # 2d. 跨系列：不互相替换，而是**同等资格**参与主题检索。
        pro = [s for s in manifest.selection if s.document_id == "DOC_PROSPECTUS"]
        check(len(pro) == 1
              and pro[0].reason_code == "different_series_topic_participating",
              f"不同文档系列必须各自记账、不得互相替代（实际 {pro}）")
        check(pro[0].source_role == "topic_participating_source",
              f"其他系列成员是 topic_participating_source（实际 {pro[0].source_role!r}）")
        check("同等资格" in pro[0].reason,
              f"理由必须写明同等资格（实际 {pro[0].reason[:120]!r}）")
        # 已删除的旧码不得以任何形式复活（实现里删了、断言也必须跟着改，不留死码）。
        check("cross_document_class_joint_retrieval_unavailable"
              not in {s.reason_code for s in manifest.selection},
              "已删除的单文档接口限制码不得留在任何选用理由里")

        # 2e. 「没成为锚」≠「材料不可用」：三份材料都进有序源集。
        pro_entry = manifest.by_document_id("DOC_PROSPECTUS")
        check(pro_entry is not None and pro_entry.eligibility == "eligible_current",
              "没成为锚 ≠ 不可用：资格与源集角色是两个字段")
        check(len(manifest.document_keys) == 3
              and [s.source_role for s in manifest.selection
                   if s.source_role != "not_used"]
              and all(s.source_role != "not_used" for s in manifest.selection),
              f"三份材料都必须进有序源集（{len(manifest.document_keys)} 个 key；"
              f"角色 {[s.source_role for s in manifest.selection]}）")
        check(SM._admit_source_set(manifest) is None,
              "锚可解析时准入闸必须放行（返回 None）")

        # ------------------------------------------------------------------
        # 3. 披露日期口径
        # ------------------------------------------------------------------
        new_entry = manifest.by_document_id("DOC_ANNUAL_NEW")
        check(new_entry.disclosure.date is None
              and new_entry.disclosure.state == "unknown",
              f"只有月粒度封面日期时披露日期必须 unknown"
              f"（实际 {new_entry.disclosure.date!r}/{new_entry.disclosure.state!r}）")
        check(new_entry.disclosure.period_hint == "2026-03"
              and new_entry.disclosure.period_hint_precision == "month",
              f"月粒度线索必须另行标注（实际 {new_entry.disclosure.period_hint!r}"
              f"/{new_entry.disclosure.period_hint_precision!r}）")
        check(not re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                               new_entry.disclosure.period_hint or ""),
              "月粒度线索不得长得像日粒度披露日期（否则读者会当成已核实）")

        # 反例：入库时间绝不冒充披露日期。
        check(new_entry.disclosure.ingestion_time == _TS,
              f"入库时间必须原样登记（实际 {new_entry.disclosure.ingestion_time!r}）")
        check(new_entry.disclosure.date != new_entry.disclosure.ingestion_time,
              "反例：入库时间不得被当作披露日期")
        check("入库时间" in new_entry.disclosure.basis
              or "不得冒充" in new_entry.disclosure.basis,
              f"依据必须写明禁用项（实际 {new_entry.disclosure.basis[:80]!r}）")

        # 封面无日期 → 明确的 unknown，不猜。
        check(manifest.by_document_id("DOC_PROSPECTUS").disclosure.date is None
              and manifest.by_document_id("DOC_PROSPECTUS").disclosure.period_hint is None,
              "封面无日期时必须 unknown 且无线索，不得猜测")

        # ------------------------------------------------------------------
        # 4. 期间抽取（内容报告期间）
        # ------------------------------------------------------------------
        check(new_entry.content_report_period.period == "2025-01-01..2025-12-31",
              f"内容报告期间必须取自文档自述（实际 "
              f"{new_entry.content_report_period.period!r}）")
        check("报告期" in new_entry.content_report_period.basis,
              "期间必须带可定位依据")
        check(manifest.by_document_id("DOC_PROSPECTUS").content_report_period.state
              == "unknown",
              "没有自述期间标记时必须 unknown，不得由文件名年份推断")

        # ------------------------------------------------------------------
        # 5. 类型判断：只看正文、最长标记优先、注册值分列
        # ------------------------------------------------------------------
        check(new_entry.type_judgment.document_class == "年度报告",
              f"封面正文判断类型（实际 {new_entry.type_judgment.document_class!r}）")
        check(manifest.by_document_id("DOC_PROSPECTUS").type_judgment.document_class
              == "债券募集说明书",
              "募集说明书必须按正文判断（文件名 `ACME_KCZ_2026.pdf` 里的 kcz/20 不参与判断）")
        check(new_entry.type_judgment.basis is not None
              and new_entry.type_judgment.basis.locator == "p1 b0",
              f"类型判断必须带可定位依据（实际 {new_entry.type_judgment.basis!r}）")

        # 反例：半年度报告不得被判成年度报告（最长标记优先）。
        half = SM.judge_document_type([SM.CoverEvidence(
            "X公司2026年半年度报告", "p1 b0", 1, 0)])
        check(half.document_class == "半年度报告",
              f"「半年度报告」含「年度报告」子串，必须最长标记优先（实际 {half.document_class!r}）")

        # 反例：正文里出现「年度报告」不算类型证据（只看封面块）。
        body_only = SM.judge_document_type([])
        check(body_only.document_class is None and body_only.confidence == "unresolved",
              "没有封面块时不得凭空判定类型")

        # 5b. 注册类型与判断分列 + SourcePolicy 影响可复算。
        check(new_entry.registered_source_type == "other",
              "注册值必须原样报告，不得被判断结果覆盖")
        eff = new_entry.policy_effect
        check(eff.judged_source_type == "annual_report"
              and eff.judged_retrieval_boost == 1.05
              and eff.retrieval_boost == 0.95,
              f"注册 other 的检索加权影响必须可复算（实际 {eff}）")
        check(eff.index_priority == 0 and eff.judged_index_priority == 1,
              "索引优先级影响必须可复算")
        check("不暗改历史 DB" in eff.note and "静默重分类" in eff.note,
              f"影响说明必须写明不暗改（实际 {eff.note[:80]!r}）")
        check(manifest.by_document_id("DOC_ANNUAL_OLD").policy_effect.note.startswith(
            "DB 注册类型 'annual_report' 与类型判断一致"),
            "一致的注册值不得被写成不一致")

        # 5c. 清单级发现必须点名披露日期的来源缺口。
        joined = "｜".join(manifest.provenance_findings)
        check("没有披露日期列" in joined and "published_at" in joined,
              f"清单级发现必须点名来源缺口（实际 {joined[:150]!r})")

        # ------------------------------------------------------------------
        # 6. 只读性：清单不写任何库
        # ------------------------------------------------------------------
        before = db.read_bytes()
        SM.build_source_manifest(
            db_path=db, company_id=_COMPANY, generated_at=_TS,
            report_as_of="2026-09-24", report_timezone="Asia/Shanghai")
        check(db.read_bytes() == before, "建立来源清单必须字节级只读（库文件不得改变）")

        # 反例：缺库不得建库。
        ghost = tmpdir / "ghost.db"
        ghost_manifest = SM.build_source_manifest(
            db_path=ghost, company_id=_COMPANY, generated_at=_TS,
            report_as_of="2026-09-24", report_timezone="Asia/Shanghai")
        check(not ghost.exists() and not ghost_manifest.entries,
              "缺库时必须返回空清单且**不建库**")

    # ------------------------------------------------------------------
    # 7. 选择规则的公司/文件名/页码无关性
    # ------------------------------------------------------------------
    # 反例：文件名里带年份、路径字典序更靠前的旧材料不得因此胜出。
    older_path_first = _entry(
        document_id="DOC_2024", document_version="sha256-" + "1" * 16,
        source_name="AAA_2020_year.pdf",
        content_report_period=SM.ContentReportPeriod(
            "2024-01-01..2024-12-31", "2024-12-31", "verified", "fixture", 1, "fixture"))
    newer_path_last = _entry(
        document_id="DOC_2025", document_version="sha256-" + "2" * 16,
        source_name="ZZZ.pdf",
        content_report_period=SM.ContentReportPeriod(
            "2025-01-01..2025-12-31", "2025-12-31", "verified", "fixture", 1, "fixture"))
    sel_ab, primary = SM.select_documents([older_path_first, newer_path_last])
    check(primary is not None and primary[0] == "DOC_2025",
          f"选择必须按可核实期间的新旧，不按文件名/路径字典序（实际 {primary}）")
    roles_ab = {s.document_id: s.source_role for s in sel_ab}
    check(roles_ab == {"DOC_2025": "current_state_source",
                       "DOC_2024": "history_and_conflict_source"},
          f"同系列两期必须给出锚 + 历史两角色（实际 {roles_ab}）")
    check([s.retrieval_order for s in sorted(sel_ab, key=lambda s: s.retrieval_order)]
          == [0, 1],
          "序位必须是确定性 0..n-1（同 role 内也确定）")

    # 反例（`§L0.3` 歧义口径）：暂定锚的**同系列组内**有期间不可核实的成员 ⇒ 无法证明锚是该组
    # 最新者 ⇒ `ambiguous_current_state`，锚与 primary 都留空。**不得**凭同类关系或入库时间推定谁新。
    no_period = _entry(
        document_id="DOC_NOPERIOD", document_version="sha256-" + "3" * 16,
        content_report_period=SM.ContentReportPeriod(
            None, None, "unknown", "fixture", 1, "fixture"))
    sel3, primary3 = SM.select_documents([no_period, newer_path_last])
    check(primary3 is None,
          f"同系列组内有未知期间成员时不得硬选一份当锚（实际 {primary3}）")
    np_reason = [s for s in sel3 if s.document_id == "DOC_NOPERIOD"]
    check(len(np_reason) == 1
          and np_reason[0].reason_code == "current_state_unresolved_source_set_member"
          and np_reason[0].source_role == "topic_participating_source",
          f"未知期间成员仍是源集成员、带 typed 理由，但**不得**被冒充成当前锚（实际 {np_reason}）")
    check("无从成立" in np_reason[0].reason,
          f"理由必须写明锚不存在时系列谓词无从成立（实际 {np_reason[0].reason[:80]!r}）")
    # 反例的对照面：另一系列（募集说明书）的未知期间成员**不清空**已可核实的全局锚。
    other_series = _entry(
        document_id="DOC_KCZ", document_version="sha256-" + "5" * 16,
        type_judgment=SM.DocumentTypeJudgment(
            "债券募集说明书", "募集说明书", "debt_circular", "prospectus",
            SM.CoverEvidence("X公司2026年度绿色科技创新债券募集说明书", "p1 b0", 1, 0),
            "document_cover"),
        content_report_period=SM.ContentReportPeriod(
            None, None, "unknown", "fixture", 1, "fixture"))
    sel3b, primary3b = SM.select_documents([other_series, newer_path_last])
    check(primary3b is not None and primary3b[0] == "DOC_2025",
          f"**另一系列**的未知期间成员不得清空已可核实的全局锚（实际 {primary3b}）")
    check({s.document_id: s.source_role for s in sel3b}
          == {"DOC_2025": "current_state_source",
              "DOC_KCZ": "topic_participating_source"},
          "另一系列的未知期间成员是 topic_participating_source，不是歧义源")

    # 反例：全体 eligible 都无可核实期间 ⇒ `no_eligible_current`（同样不留锚）。
    sel3c, primary3c = SM.select_documents([no_period, other_series])
    check(primary3c is None, "全无可用期间时必须返回 None")

    # 反例：同类判定不得退回注册类型/文件名——内容识别不出系列时同类关系不成立。
    unresolved_series = _entry(
        document_id="DOC_UNK", document_version="sha256-" + "6" * 16,
        registered_source_type="annual_report",
        type_judgment=SM.DocumentTypeJudgment(
            None, None, None, None, None, "unresolved"),
        content_report_period=SM.ContentReportPeriod(
            "2023-01-01..2023-12-31", "2023-12-31", "verified", "fixture", 1, "fixture"))
    check(SM.same_series_as(SM.derive_series_identity(unresolved_series),
                            SM.derive_series_identity(newer_path_last)) is False,
          "系列识别不出时同类关系必须**不成立**（不按注册类型或文件名代判）")
    check(SM.same_series_as(SM.derive_series_identity(older_path_first),
                            SM.derive_series_identity(newer_path_last)) is True,
          "对照面：同主体 + 同内容系列 + 仅期间不同 ⇒ 同类")

    # 反例：非 current 材料不参选，但仍留在清单里。
    stale = _entry(document_id="DOC_STALE", document_version="sha256-" + "4" * 16,
                   registry_status="superseded", eligibility="not_current",
                   eligibility_reason="documents.status='superseded'（非 current）")
    sel4, primary4 = SM.select_documents([stale, newer_path_last])
    check(primary4 is not None and primary4[0] == "DOC_2025",
          "非 current 材料不得参选")
    st = [s for s in sel4 if s.document_id == "DOC_STALE"]
    check(len(st) == 1 and st[0].reason_code == "not_eligible_current",
          f"非 current 材料必须带 typed 理由（实际 {st}）")
    check(len(sel4) == 2, "被排除的材料同样要在选用记账里，不得静默消失")

    # 反例：全部不可用时必须返回 None（不得硬选一份出来）。
    _sel5, primary5 = SM.select_documents([stale])
    check(primary5 is None, "没有可用材料时必须返回 None，不得任选一份冒充")

    # ------------------------------------------------------------------
    # 8. 取值表不漂移（按源码文本对账，不 import 重依赖模块）
    # ------------------------------------------------------------------
    retriever_src = (REPO / "retrieval/retriever.py").read_text(encoding="utf-8")
    for source_type, boost in SM.RETRIEVAL_BOOST_BY_SOURCE_TYPE.items():
        check(re.search(rf'"{source_type}":\s*{re.escape(str(boost))}', retriever_src)
              is not None,
              f"检索加权表必须与 retrieval/retriever.py 一致：{source_type}={boost}")
    indexer_src = (REPO / "retrieval/indexer.py").read_text(encoding="utf-8")
    for source_type, priority in SM.INDEX_PRIORITY_BY_SOURCE_TYPE.items():
        check(re.search(rf'return \("{source_type}",\s*{priority}\)', indexer_src)
              is not None,
              f"索引优先级表必须与 retrieval/indexer.py 一致：{source_type}={priority}")

    # ------------------------------------------------------------------
    # 9. 接口冲突事实：单文档绑定是既有接口的形状
    # ------------------------------------------------------------------
    req_fields = set(getattr(LSS.LiveSpanBuildRequest, "__dataclass_fields__", {}))
    check("document_id" in req_fields and "raw_pdf_path" in req_fields
          and "raw_pdf_sha256" in req_fields,
          f"LiveSpanBuildRequest 仍是单文档身份（实际字段 {sorted(req_fields)}）")
    check(not any("documents" in f or "document_ids" in f for f in req_fields),
          "LiveSpanBuildRequest 不得已经支持多文档（若已支持，跨类型联合检索的理由码必须改）")

    # ------------------------------------------------------------------
    # 10. `§L0.6` `SourceDocumentKey`：内容寻址 + 主体一致性
    # ------------------------------------------------------------------
    key = SM.SourceDocumentKey(company_id=_COMPANY, document_id="DOC_A",
                               document_version=_DOCV, evidence_set_version=_SETV)
    check(key.to_dict() == {"company_id": _COMPANY, "document_id": "DOC_A",
                            "document_version": _DOCV, "evidence_set_version": _SETV},
          f"文档键必须是四轴内容寻址（实际 {key.to_dict()}）")
    check(not any(f in key.to_dict() for f in
                  ("path", "created_at", "ingestion_time", "run_id")),
          "文档键不得含路径、入库时间或 run_id")
    for bad in ({"company_id": ""}, {"document_id": "  "},
                {"document_version": ""}, {"evidence_set_version": ""}):
        kw = {"company_id": _COMPANY, "document_id": "DOC_A", "document_version": _DOCV,
              "evidence_set_version": _SETV}
        kw.update(bad)
        try:
            SM.SourceDocumentKey(**kw)
            check(False, f"空轴必须 fail-closed（{bad}）")
        except ValueError:
            check(True, f"空轴 fail-closed（{bad}）")
    # 反例：路径/文件名不得充当 document_id（内容寻址，不是文件寻址）。
    try:
        SM.SourceDocumentKey(company_id=_COMPANY,
                             document_id="materials/AAA_2020_year.pdf",
                             document_version=_DOCV, evidence_set_version=_SETV)
        check(False, "路径或文件名不得充当 document_id")
    except ValueError:
        check(True, "路径或文件名充当 document_id 时 fail-closed")
    other_subject = SM.SourceDocumentKey(company_id="OTHER", document_id="DOC_B",
                                         document_version=_DOCV,
                                         evidence_set_version=_SETV)
    check(key.same_subject_as(other_subject) is False,
          "跨主体必须被判定为不同主体（源集不取多数票）")

    # ------------------------------------------------------------------
    # 11. `§L0.5` `by_document_key`：同 id 多版本不再歧义
    # ------------------------------------------------------------------
    v1 = _entry(document_id="DOC_DUP", document_version="sha256-" + "a" * 16)
    v2 = _entry(document_id="DOC_DUP", document_version="sha256-" + "b" * 16)
    dup_manifest = SM.SourceManifest(
        policy_version="sm-2", company_id=_COMPANY, generated_at=_TS,
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff=None, entries=(v1, v2), provenance_findings=(),
        selection=(), primary_document_id=None, primary_document_version=None)
    check(dup_manifest.by_document_key("DOC_DUP", "sha256-" + "b" * 16) is v2
          and dup_manifest.by_document_key("DOC_DUP", "sha256-" + "c" * 16) is None,
          "同 id 多版本必须按键精确取，不得取到另一版本或猜一个")
    check(dup_manifest.by_document_id("DOC_DUP") is v1,
          "对照面：`by_document_id` 保留（取第一个匹配），键查询另走 `by_document_key`")

    # ------------------------------------------------------------------
    # 11b. T4：台账成员关联按**四轴**核对（第四轴可比，且错轴 fail-closed）
    # ------------------------------------------------------------------
    # 正例：给了第四轴且与条目一致 ⇒ 正常命中。
    check(dup_manifest.by_document_key(
        "DOC_DUP", "sha256-" + "b" * 16, _SETV) is v2,
        "正例：四轴全同的键查询必须命中对应条目")
    # 反例：同 id 同版本、错 `evidence_set_version` ⇒ **不得**命中（内容寻址下是另一份文档）。
    check(dup_manifest.by_document_key(
        "DOC_DUP", "sha256-" + "b" * 16, "set-other") is None,
        "反例：错第四轴必须取不到（不得按 (id, version) 就近取一份条目）")
    # 对照面：不给第四轴时逐字保持旧行为（`None` 是**调用方没问**，不是「身份相同」）。
    check(dup_manifest.by_document_key("DOC_DUP", "sha256-" + "b" * 16) is v2,
          "对照面：不给第四轴时保持旧行为（这一条防止「收紧」被顺手扩到未传轴的调用方）")
    # `role_of` 同一条纪律：选用决定必须能被第四轴区分。
    dup_sel = SM.SourceManifest(
        policy_version="sm-2", company_id=_COMPANY, generated_at=_TS,
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff=None, entries=(v1, v2), provenance_findings=(),
        selection=(
            SM.DocumentSelection(
                document_id="DOC_DUP", document_version="sha256-" + "a" * 16,
                selection_state="selected_source_set", reason_code="fixture", reason="fixture",
                source_role="current_state_source", retrieval_order=0,
                evidence_set_version=_SETV),
            SM.DocumentSelection(
                document_id="DOC_DUP", document_version="sha256-" + "b" * 16,
                selection_state="selected_source_set", reason_code="fixture", reason="fixture",
                source_role="history_and_conflict_source", retrieval_order=1,
                evidence_set_version=_SETV)),
        primary_document_id=None, primary_document_version=None)
    check(dup_sel.role_of("DOC_DUP", "sha256-" + "b" * 16, _SETV)
          == "history_and_conflict_source",
          "正例：`role_of` 四轴全同时取回**那一行**的角色")
    check(dup_sel.role_of("DOC_DUP", "sha256-" + "a" * 16) == "current_state_source",
          "对照面：不给第四轴时保持旧行为（按 (id, version) 命中）")
    check(dup_sel.role_of("DOC_DUP", "sha256-" + "b" * 16, "set-other") is None,
          "反例：`role_of` 错第四轴必须回落 `None`（调用方据此 fail-closed，不得就近顶替）")
    # 旧写法构造的选用决定（不带第四轴）在**传了**第四轴时同样不命中：`None` 是「这一行没有
    # 可比身份」，不是「与任何一把键都相同」——否则这道门对历史数据恒真，等于没设。
    legacy_sel = SM.SourceManifest(
        policy_version="sm-2", company_id=_COMPANY, generated_at=_TS,
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff=None, entries=(v1, v2), provenance_findings=(),
        selection=(SM.DocumentSelection(
            document_id="DOC_DUP", document_version="sha256-" + "b" * 16,
            selection_state="selected_source_set", reason_code="fixture", reason="fixture",
            source_role="history_and_conflict_source", retrieval_order=1),),
        primary_document_id=None, primary_document_version=None)
    check(legacy_sel.role_of("DOC_DUP", "sha256-" + "b" * 16, _SETV) is None,
          "反例：选用决定缺第四轴时，传了第四轴的查询不得命中（缺身份≠身份相同）")

    # ------------------------------------------------------------------
    # 12. `§L0.3`/`§L0.7` 准入闸与注册/识别不一致审计
    # ------------------------------------------------------------------
    blocked = SM.SourceManifest(
        policy_version="sm-2", company_id=_COMPANY, generated_at=_TS,
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff=None, entries=(no_period,), provenance_findings=(),
        selection=(), primary_document_id=None, primary_document_version=None,
        document_keys=(SM.SourceDocumentKey(
            company_id=_COMPANY, document_id="DOC_NOPERIOD",
            document_version="sha256-" + "3" * 16, evidence_set_version=_SETV),),
        current_state=SM.CURRENT_STATE_NONE,
        current_state_reason=SM._NO_ELIGIBLE_CURRENT_REASON)
    gate = SM._admit_source_set(blocked)
    check(gate is not None and gate.current_state == SM.CURRENT_STATE_NONE
          and gate.source_set_size == 1 and gate.reason,
          f"无合法当前锚必须在研究之前被清单层阻断（实际 {gate}）")
    check(gate.to_dict()["rule_version"] == SM.SCOPE_ADMISSION_RULE_VERSION,
          "阻断记录必须带准入规则版本（可复算）")
    # 对照面：锚可解析 + 明确 reason 缺失时，锚存在即放行；锚缺失即阻断（fail-closed）。
    check(SM._admit_source_set(SM.SourceManifest(
        policy_version="sm-2", company_id=_COMPANY, generated_at=_TS,
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff=None, entries=(newer_path_last,), provenance_findings=(),
        selection=(), primary_document_id="DOC_2025",
        primary_document_version="sha256-" + "2" * 16)) is None,
        "锚存在时必须放行（准入闸不得误杀）")
    check(SM._admit_source_set(SM.SourceManifest(
        policy_version="sm-2", company_id=_COMPANY, generated_at=_TS,
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff=None, entries=(), provenance_findings=(), selection=(),
        primary_document_id=None, primary_document_version=None)) is not None,
        "锚缺失且无 typed 理由时同样阻断（fail-closed，不靠理由存在与否放行）")

    # 注册类型与内容识别不一致：`NDSD_2025_year` 型的真实不一致必须是 typed 审计，不是 gap。
    mm = SM.RegistrationContentClassMismatch(
        document_key=key, registered_class="other",
        content_identified_class="annual_report",
        identification_basis="封面 p1 b0 原文：X公司2024年年度报告2025年03月",
        policy_version=SM.MANIFEST_POLICY_VERSION)
    check(set(mm.to_dict()) == {"document_key", "registered_class",
                                "content_identified_class", "identification_basis",
                                "policy_version"},
          f"注册/识别不一致必须是 typed 记录（实际键 {sorted(mm.to_dict())}）")
    check("gap" not in json.dumps(mm.to_dict(), ensure_ascii=False).lower(),
          "该审计不得被写成 gap 或塞进自由文本（§0.13 第 9 条）")

    # ------------------------------------------------------------------
    # 13. `§L0.4` 披露日期的未知理由必须显式（不是一句空话）
    # ------------------------------------------------------------------
    bad_state = SM.DisclosureDateState(
        date=None, state="unknown", period_hint=None, period_hint_precision=None,
        basis="封面没有日期", ingestion_time=_TS)
    try:
        SM.assert_disclosure_state(bad_state)
        check(False, "unknown 且 basis 未写明「入库时间 / 不得冒充」时必须失败")
    except ValueError:
        check(True, "unknown 的 basis 未写明禁区时 fail-closed")
    good_state = SM.DisclosureDateState(
        date=None, state="unknown", period_hint="2025-03", period_hint_precision="month",
        basis="仅月粒度；月粒度线索不升格、不回填，入库时间不得冒充披露日期",
        ingestion_time=_TS)
    SM.assert_disclosure_state(good_state)
    check(True, "对照面：写明禁区的 unknown 通过")
    try:
        SM.assert_disclosure_state(SM.DisclosureDateState(
            date="2025-03-01", state="unknown", period_hint=None,
            period_hint_precision=None, basis="入库时间不得冒充", ingestion_time=_TS))
        check(False, "state=unknown 却带 date 必须失败（月粒度不得升格）")
    except ValueError:
        check(True, "state=unknown 带 date 时 fail-closed")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

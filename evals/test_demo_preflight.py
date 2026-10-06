"""Eval: scripts.demo_preflight 严格只读语义（Phase 3 收口专项测试）。

用法: python -m evals.test_demo_preflight

覆盖（用户 §三 专项测试）：
- temp 库路径生效、不读/不建默认库路径（DEFAULT_DB_PATH 不被触碰）；
- 缺失 DB → fail，且运行后仍不存在（不 init_db 建表）；
- 空库 / 缺 schema（0 字节）→ fail；
- validity 缺失 / stale / superseded → 全 fail（None 绝不默认 valid）；
- report_blocked / quarantine → fail；
- Evidence 无 current set → fail；
- provider / key 缺失 → fail，且输出绝不打印 Key；
- 完整健康环境 → pass；
- 运行前后 DB 文件 hash 不变（真正只读）。

M930-3 追加（P24 / 批次 3D：preflight **只新增** current 写作主链的工件 / wire / 版本
存在性与一致性检查，不改 M930-1/2 既有检查语义）：
- 7 项 current 写作主链检查在健康环境下全部就位且通过，且恰好是相对旧检查集的**新增面**；
- 把 `_check_backbone_current_chain` 替换为返回空列表后，旧检查集与逐项 detail **完全不变**
  （证明「只新增、不改语义」，而不是替换或重排）；
- 逐项反例：writer / company 相位版本相同、`MAX_FOLLOW_UP_ROUNDS≠1`、缺 writer 符号、
  current narr-4 版本与 legacy 重叠、legacy 面登记为空、current `section-result-2` 与 legacy
  重叠、legacy class 冒充 current class、index 版本与 legacy 重叠、缺 narr-4 成员角色、
  拒绝理由码为空或与 disposition 理由码有交集、缺只读入口符号、Phase 4 runner marker 未登记
  或写成 current → 对应检查 fail，且整体 `ok is False`（fail-closed 参与总门）；
- 新检查为纯模块 / 常量判定：不读 DB、不打印库路径，且运行前后 DB hash 不变。

公司无关：一律用合成公司 "ACME"，不引入 300750 / 宁德时代分支。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from evidence import store as estore
from evals.test_context import _seed_records, _specs
from financial_v2 import progress
from financial_v2 import snapshots
from financial_v2 import store as fstore
from harness import topic_runtime as TR
from scripts import demo_preflight as pf
from scripts import run_phase4_demo as RPD
from sections import artifact_loader as AL
from sections import backbone_artifacts as BA
from sections import company_worker as CW
from sections import narrative_schema as NS
from sections import schema as SS

_TS = "2026-01-01T00:00:00Z"
_COMPANY = "ACME"

#: M930-1/2 既有检查集（健康环境下的完整名单）：3D 只允许**追加**，不得删除 / 改名 / 重排。
_EXISTING_CHECKS = (
    "financial.schema_version", "financial.required_tables", "financial.current_record_set",
    "financial.validity", "financial.report_blocked", "financial.quarantine",
    "financial.scope_currency_purpose", "financial.snapshot_item_count",
    "financial.metric_result_count", "financial.formula_definition_count",
    "financial.metric_formula_versions_present", "financial.active_snapshot_id",
    "evidence.current_set_inventory",
    "provider.external_search_provider", "provider.bocha_api_key_present",
    "backbone.scope_profile_exists", "backbone.scope_yaml_readable",
    "backbone.asset.contract", "backbone.asset.source_policy",
    "backbone.asset.writing_spec", "backbone.asset.presentation_profile",
    "backbone.scope_partition_conservation", "backbone.scope_profile_loadable",
    "backbone.schema_self_check", "backbone.artifacts_importable",
)

#: M930-3（3D）新增的 current 写作主链检查（顺序与 `run()` 组装顺序一致）。
_CURRENT_CHAIN_CHECKS = (
    "backbone.writer_phase_wire",
    "backbone.narrative_wire_version",
    "backbone.claim_result_wire_version",
    "backbone.artifact_index_version",
    "backbone.follow_up_wire",
    "backbone.artifact_loader_legacy_only",
    "backbone.phase4_runner_legacy_only",
)

_DELETE = object()
_MISSING = object()


class _Patched:
    """临时替换 / 删除模块属性，退出时严格还原（含「还原为不存在」）。"""

    def __init__(self, mod, **attrs):
        self._mod = mod
        self._attrs = attrs
        self._old: dict[str, object] = {}

    def __enter__(self):
        for k, v in self._attrs.items():
            self._old[k] = getattr(self._mod, k, _MISSING)
            if v is _DELETE:
                delattr(self._mod, k)
            else:
                setattr(self._mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, old in self._old.items():
            if old is _MISSING:
                delattr(self._mod, k)
            else:
                setattr(self._mod, k, old)
        return False


# ---------------------------------------------------------------------------
# 环境构造（只写临时库；preflight 是被测对象，本处仅构造输入）
# ---------------------------------------------------------------------------

def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_pf_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _db_hash(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _mutate(db: str, sql: str, params: tuple = ()) -> None:
    """打开→执行→提交→关闭（try/finally，异常也不留半开连接）。"""
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _build_healthy_fin(company: str, fin_db: str) -> None:
    """真实主链：标准化记录 → 快照 + 指标 → current 指针 + validity=valid。"""
    fstore.init_db(fin_db)
    rs = _seed_records(company, "ok", _specs({
        "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
        "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
        "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
        "NET_PROFIT": Decimal("120"),
    }, "2024-12-31"))
    req = snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=[rs],
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id="run-pf")
    res = progress.run_pipeline(req)
    assert res.final_state == "completed", f"healthy fin build failed: {res.final_state}"


def _build_blocked_fin(company: str, fin_db: str) -> None:
    """真实主链但缺必算公式输入 → MISSING_REQUIRED_ITEM 恒阻断 → report_blocked=True。

    financial_snapshot 表是不可变表（UPDATE 触发禁止），故 report_blocked 只能经真实
    构建链产生，本测试用「缺 CURRENT_ASSETS/CURRENT_LIABILITIES 但必算 SOLV_CURRENT_RATIO」
    确定性触发 report_blocked，而非绕过约束直改快照行。
    """
    fstore.init_db(fin_db)
    rs = _seed_records(company, "blocked", _specs({
        "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
        "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
        "NET_PROFIT": Decimal("120"),
    }, "2024-12-31"))
    req = snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=[rs],
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id="run-pf-blocked")
    res = progress.run_pipeline(req)
    assert res.final_state == "waiting_human", f"blocked fin build: {res.final_state}"


def _seed_evidence(ev_db: str, company: str, doc_status: str = "current",
                   set_status: str = "current") -> None:
    conn = sqlite3.connect(ev_db)
    try:
        conn.execute(
            "INSERT INTO documents (company_id, document_id, document_version, source_name, "
            "source_type, material_group, file_sha256, file_size, parser_version, status, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (company, "doc-1", "v1", "年报.pdf", "annual_report", "company_docs",
             "a" * 64, 1024, "pdf_parser_v1", doc_status, _TS))
        conn.execute(
            "INSERT INTO evidence_sets (company_id, document_id, document_version, "
            "evidence_set_version, dependency_versions, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (company, "doc-1", "v1", "es-1", "{}", set_status, _TS))
        conn.commit()
    finally:
        conn.close()


def _build_healthy_ev(company: str, ev_db: str) -> None:
    estore.init_db(ev_db)
    _seed_evidence(ev_db, company)


def _snapshot_id(fin_db: str) -> str:
    conn = sqlite3.connect(fin_db)
    try:
        return conn.execute("SELECT snapshot_id FROM current_snapshot LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def _set_validity(fin_db: str, snap_id: str, status: str | None) -> None:
    """status=None 表示删除全部 validity 事件（latest → None）。"""
    if status is None:
        _mutate(fin_db, "DELETE FROM snapshot_validity WHERE snapshot_id=?", (snap_id,))
    else:
        _mutate(fin_db, "UPDATE snapshot_validity SET status=? WHERE snapshot_id=?",
                (status, snap_id))


def _run(company: str, fin_db: str, ev_db: str) -> dict:
    return pf.run(company=company, scope="consolidated", currency="CNY",
                  purpose="credit_analysis", fin_db=fin_db, ev_db=ev_db)


def _check_by_name(result: dict, name: str) -> dict | None:
    for c in result["checks"]:
        if c["name"] == name:
            return c
    return None


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

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

    # ===================== 健康环境 + 逐项失败态 =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        _build_healthy_fin(_COMPANY, fin_db)
        _build_healthy_ev(_COMPANY, ev_db)
        snap_id = _snapshot_id(fin_db)
        h_before_fin = _db_hash(fin_db)
        h_before_ev = _db_hash(ev_db)

        # ---- 1. temp 库路径生效 + 不读默认库路径 ----
        bogus_dir = Path(tempfile.mkdtemp())
        bogus_fin = bogus_dir / "default_fin.db"
        bogus_ev = bogus_dir / "default_ev.db"
        orig_fdef, orig_edef = fstore.DEFAULT_DB_PATH, estore.DEFAULT_DB_PATH
        fstore.DEFAULT_DB_PATH = bogus_fin
        estore.DEFAULT_DB_PATH = bogus_ev
        try:
            result = _run(_COMPANY, fin_db, ev_db)
        finally:
            fstore.DEFAULT_DB_PATH = orig_fdef
            estore.DEFAULT_DB_PATH = orig_edef
        check(result["ok"] is True, "temp 库路径生效：健康环境 preflight 全通过")
        check(not bogus_fin.exists() and not bogus_ev.exists(),
              "preflight 未读/未建默认库路径（DEFAULT_DB_PATH 不被触碰）")

        # ---- 11. 完整健康环境 → pass（逐项关键检查） ----
        check(result["failed"] == 0, f"健康环境 failed=0（实际 {result['failed']}）")
        check(_check_by_name(result, "financial.schema_version")["ok"] is True,
              "健康环境 schema 版本校验通过")
        check(_check_by_name(result, "financial.validity")["ok"] is True,
              "健康环境 validity == valid 判定通过")
        check(_check_by_name(result, "financial.snapshot_item_count")["ok"] is True
              and _check_by_name(result, "financial.metric_result_count")["ok"] is True,
              "健康环境 snapshot_item / metric_result 均非空")
        check(_check_by_name(result, "evidence.current_set_inventory")["ok"] is True,
              "健康环境 evidence current set 非空判定通过")

        # ---- 12. 运行前后 DB 文件 hash 不变（真正只读） ----
        check(_db_hash(fin_db) == h_before_fin and _db_hash(ev_db) == h_before_ev,
              "preflight 运行前后 fin/ev 库文件 hash 不变（只读不写库）")

        # ---- provider / key：缺失 fail + 输出不打印 Key ----
        orig_provider, orig_key = config.EXTERNAL_SEARCH_PROVIDER, config.BOCHA_API_KEY
        try:
            config.EXTERNAL_SEARCH_PROVIDER = "bocha"
            config.BOCHA_API_KEY = ""
            r = _run(_COMPANY, fin_db, ev_db)
            check(_check_by_name(r, "provider.bocha_api_key_present")["ok"] is False,
                  "key 缺失 → provider.bocha_api_key_present fail")

            config.BOCHA_API_KEY = "SUPERSECRET_KEY_9f3a"
            r = _run(_COMPANY, fin_db, ev_db)
            check(_check_by_name(r, "provider.bocha_api_key_present")["ok"] is True,
                  "key 已设置 → provider.bocha_api_key_present pass")
            check("SUPERSECRET_KEY_9f3a" not in json.dumps(r, ensure_ascii=False),
                  "输出绝不打印 API Key（序列化结果不含 Key 值）")

            config.EXTERNAL_SEARCH_PROVIDER = "other"
            config.BOCHA_API_KEY = "k"
            r = _run(_COMPANY, fin_db, ev_db)
            check(_check_by_name(r, "provider.external_search_provider")["ok"] is False,
                  "provider != bocha → provider.external_search_provider fail")
        finally:
            config.EXTERNAL_SEARCH_PROVIDER = orig_provider
            config.BOCHA_API_KEY = orig_key

        # ---- 5. validity stale ----
        _set_validity(fin_db, snap_id, "stale")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.validity")["ok"] is False,
              "validity=stale → financial.validity fail")
        _set_validity(fin_db, snap_id, "valid")

        # ---- 5. validity superseded ----
        _set_validity(fin_db, snap_id, "superseded")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.validity")["ok"] is False,
              "validity=superseded → financial.validity fail")
        _set_validity(fin_db, snap_id, "valid")

        # ---- 4. validity 缺失（None） ----
        _set_validity(fin_db, snap_id, None)
        r = _run(_COMPANY, fin_db, ev_db)
        v = _check_by_name(r, "financial.validity")
        check(r["ok"] is False and v["ok"] is False and "None" in v["detail"],
              "validity 缺失(None) → fail（绝不默认 valid）")
        _set_validity(fin_db, snap_id, "valid")

        # ---- 7. quarantine ----
        _mutate(fin_db, "INSERT INTO quarantine (quarantine_id, object_type, object_id, "
                "reason, quarantined_at) VALUES (?,?,?,?,?)",
                ("q1", "financial_snapshot", snap_id, "test", _TS))
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.quarantine")["ok"] is False,
              "快照被 quarantine → financial.quarantine fail")
        _mutate(fin_db, "DELETE FROM quarantine WHERE quarantine_id='q1'")

        # ---- 9. Evidence 无 current set ----
        # 清掉 evidence_sets 的 current 状态 → JOIN 为空 → inventory 空 → fail。
        _mutate(ev_db, "UPDATE evidence_sets SET status='superseded'")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False
              and _check_by_name(r, "evidence.current_set_inventory")["ok"] is False,
              "Evidence 无 current set → evidence.current_set_inventory fail")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    # ===================== 7. report_blocked（真实阻断快照） =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        _build_blocked_fin(_COMPANY, fin_db)
        _build_healthy_ev(_COMPANY, ev_db)
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.report_blocked")["ok"] is False,
              "report_blocked=true → financial.report_blocked fail")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    # ===================== 2. 缺失 DB → fail 且仍缺失 =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    _cleanup_db(fin_db)  # 确保不存在
    _cleanup_db(ev_db)
    r = _run(_COMPANY, fin_db, ev_db)
    check(r["ok"] is False, "缺失 fin/ev 库 → preflight fail-closed")
    check(not Path(fin_db).exists() and not Path(ev_db).exists(),
          "缺失库运行后仍不存在（preflight 不 init_db 建表）")
    check(_check_by_name(r, "financial.db_readonly_open") is not None
          and _check_by_name(r, "evidence.db_readonly_open") is not None,
          "缺失库返回 db_readonly_open 失败检查（非抛错崩溃）")

    # ===================== 3. 空库 / 缺 schema → fail =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        Path(fin_db).write_bytes(b"")  # 0 字节空文件
        Path(ev_db).write_bytes(b"")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False, "空库 → preflight fail-closed")
        check(_check_by_name(r, "financial.schema_version")["ok"] is False
              and _check_by_name(r, "financial.required_tables")["ok"] is False,
              "空库 schema 版本 + 缺表均 fail")
        check(_check_by_name(r, "evidence.current_set_inventory")["ok"] is False,
              "空 evidence 库 → current_set_inventory fail（不抛错崩溃）")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    # ===================== M930-3 current 写作主链追加检查（P24 / 3D） =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        _build_healthy_fin(_COMPANY, fin_db)
        _build_healthy_ev(_COMPANY, ev_db)
        h_before_fin = _db_hash(fin_db)
        h_before_ev = _db_hash(ev_db)

        base = _run(_COMPANY, fin_db, ev_db)
        names = [c["name"] for c in base["checks"]]
        base_map = {c["name"]: c for c in base["checks"]}

        # ---- 21. 追加检查就位：健康环境仍全通过，且旧检查一项不少 ----
        check(base["ok"] is True and base["failed"] == 0,
              f"M930-3 追加检查生效后健康环境仍 failed=0（实际 {base['failed']}）")
        missing_new = [n for n in _CURRENT_CHAIN_CHECKS if n not in base_map]
        check(not missing_new, f"7 项 current 写作主链检查全部就位（缺 {missing_new}）")
        check(all(base_map.get(n, {}).get("ok") is True for n in _CURRENT_CHAIN_CHECKS),
              "7 项 current 写作主链检查在健康环境全部 pass")
        missing_old = [n for n in _EXISTING_CHECKS if n not in base_map]
        check(not missing_old, f"M930-1/2 既有检查项全部保留（缺 {missing_old}）")
        check(all(base_map[n]["ok"] is True for n in _EXISTING_CHECKS),
              "既有检查项在健康环境仍全部 pass")
        check(names == list(_EXISTING_CHECKS) + list(_CURRENT_CHAIN_CHECKS),
              f"检查顺序 = 既有 25 项 + 新增 7 项（实际 {len(names)} 项）")

        # ---- 22. 「只新增、不改既有语义」：把新函数替换为返回空列表，旧检查逐项不变 ----
        with _Patched(pf, _check_backbone_current_chain=lambda: []):
            without = _run(_COMPANY, fin_db, ev_db)
        without_map = {c["name"]: c for c in without["checks"]}
        check(list(without_map) == list(_EXISTING_CHECKS),
              f"去掉新函数后恰为既有检查集（实际 {list(without_map)!r}）")
        base_old = {n: base_map[n] for n in _EXISTING_CHECKS}
        check(base_old == without_map,
              "既有检查逐项 name/ok/detail 不变（M930-1/2 语义未被改动）")
        check(without["ok"] is True and without["failed"] == 0,
              "去掉新函数后健康环境仍全通过（新检查不与旧检查互相掩盖）")

        # ---- 23. 新检查为纯模块 / 常量判定：不读 DB、不打印库路径 ----
        check(all(fin_db not in c["detail"] and ev_db not in c["detail"]
                  for c in base["checks"]),
              "所有检查的 detail 不含 fin/ev 库路径（新检查不读 DB）")
        check(_db_hash(fin_db) == h_before_fin and _db_hash(ev_db) == h_before_ev,
              "M930-3 追加检查运行前后 fin/ev 库文件 hash 不变")

        # 静态面：新函数源码不含建库 / 写盘 / 网络 / LLM 调用
        src = inspect.getsource(pf._check_backbone_current_chain)
        forbidden = [t for t in ("init_db", "connect(", "sqlite3", "write_text", "write_bytes",
                                 "open(", "subprocess", "requests", "urllib", "http",
                                 "llm", "os.remove")
                     if t in src]
        check(not forbidden,
              f"新检查源码不含建库 / 写盘 / 网络 / LLM 调用（命中 {forbidden}）")
        check(src.count("def _check_backbone_current_chain") == 1 and "return checks" in src,
              "新检查为单一函数、返回 PreflightCheck 列表")

        # ---- 24. 逐项反例：版本漂移 / 缺符号 / 冒充 / 交集 → 对应检查 fail 且总门 fail ----
        cases: list[tuple[str, object, str]] = [
            ("writer 相位版本 == company 相位版本",
             lambda: _Patched(CW, BACKBONE_WRITER_PHASE_VERSION=CW.BACKBONE_PHASE_VERSION),
             "backbone.writer_phase_wire"),
            ("MAX_FOLLOW_UP_ROUNDS=2（无界返修不可表达）",
             lambda: _Patched(CW, MAX_FOLLOW_UP_ROUNDS=2),
             "backbone.writer_phase_wire"),
            ("缺 run_backbone_writer_phase 符号",
             lambda: _Patched(CW, run_backbone_writer_phase=_DELETE),
             "backbone.writer_phase_wire"),
            ("current 叙述版本回退为 legacy（narr-3）",
             lambda: _Patched(NS, NARRATIVE_SCHEMA_VERSION="narr-3"),
             "backbone.narrative_wire_version"),
            ("legacy narrative 面登记为空",
             lambda: _Patched(NS, LEGACY_NARRATIVE_SCHEMA_VERSIONS=()),
             "backbone.narrative_wire_version"),
            ("缺 SectionDraft 符号",
             lambda: _Patched(NS, SectionDraft=_DELETE),
             "backbone.narrative_wire_version"),
            ("current section-result-2 同时登记为 legacy",
             lambda: _Patched(SS, SECTION_RESULT_SCHEMA_VERSION="section-result-1"),
             "backbone.claim_result_wire_version"),
            ("缺 load_legacy_section_result_for_audit",
             lambda: _Patched(SS, load_legacy_section_result_for_audit=_DELETE),
             "backbone.claim_result_wire_version"),
            ("LegacySectionResultV1 冒充 current SectionResult 子类",
             lambda: _Patched(
                 SS, LegacySectionResultV1=type("_FakeLegacy", (SS.SectionResult,), {})),
             "backbone.claim_result_wire_version"),
            ("LegacySectionClaimV1 冒充 current SectionClaim 子类",
             lambda: _Patched(
                 SS, LegacySectionClaimV1=type("_FakeLegacyClaim", (SS.SectionClaim,), {})),
             "backbone.claim_result_wire_version"),
            ("index 版本回退为 v1（与 legacy 重叠）",
             lambda: _Patched(BA, ARTIFACT_INDEX_SCHEMA_VERSION="demo-artifact-index-v1"),
             "backbone.artifact_index_version"),
            ("缺 follow_up_need 成员角色",
             lambda: _Patched(BA, ARTIFACT_MEMBER_ROLES=tuple(
                 r for r in BA.ARTIFACT_MEMBER_ROLES if r != "follow_up_need")),
             "backbone.artifact_index_version"),
            ("缺 load_legacy_run_artifacts_for_audit",
             lambda: _Patched(BA, load_legacy_run_artifacts_for_audit=_DELETE),
             "backbone.artifact_index_version"),
            ("拒绝理由码为空",
             lambda: _Patched(TR, FOLLOW_UP_REJECTION_REASONS=()),
             "backbone.follow_up_wire"),
            ("拒绝理由码与 disposition 理由码有交集",
             lambda: _Patched(TR, FOLLOW_UP_REJECTION_REASONS=tuple(
                 TR.FOLLOW_UP_REJECTION_REASONS) + (NS.DISPOSITION_REASON_CODES[0],)),
             "backbone.follow_up_wire"),
            ("缺 run_follow_up_needs 符号",
             lambda: _Patched(TR, run_follow_up_needs=_DELETE),
             "backbone.follow_up_wire"),
            ("缺只读入口 load_phase4_run",
             lambda: _Patched(AL, load_phase4_run=_DELETE),
             "backbone.artifact_loader_legacy_only"),
            ("Phase 4 runner marker 未登记为 legacy",
             lambda: _Patched(RPD, LEGACY_RESULT_MARKER="section-result-9"),
             "backbone.phase4_runner_legacy_only"),
            ("Phase 4 runner 写出 current marker",
             lambda: _Patched(RPD, LEGACY_RESULT_MARKER=SS.SECTION_RESULT_SCHEMA_VERSION),
             "backbone.phase4_runner_legacy_only"),
        ]
        for label, patch, expect in cases:
            with patch():
                r = _run(_COMPANY, fin_db, ev_db)
            c = _check_by_name(r, expect)
            check(c is not None and c["ok"] is False,
                  f"反例「{label}」→ {expect} fail")
            check(r["ok"] is False,
                  f"反例「{label}」→ 整体 ok False（fail-closed 参与总门）")
            others = [x for x in _EXISTING_CHECKS
                      if x != expect and _check_by_name(r, x)["ok"] is not True]
            check(not others,
                  f"反例「{label}」不影响既有检查项（异常项 {others}）")

        # ---- 25. 还原后健康环境恢复全通过（patch 无残留） ----
        after = _run(_COMPANY, fin_db, ev_db)
        check(after["checks"] == base["checks"],
              "全部反例还原后检查结果与基线逐项一致（patch 无残留）")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

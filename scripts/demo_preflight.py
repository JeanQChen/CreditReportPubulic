"""Demo 环境只读 preflight（Phase 3 最终验收准备）。

用途：在重跑 COMP-SW1（或后续 frozen_final）前，确认 Demo 数据环境是否就绪。**严格只读**：
不调用 `init_db()`，不建表、不迁移、不写任何库；用 SQLite `mode=ro` 只读探测 `--fin-db` /
`--ev-db`。缺失 / 不完整 / 版本不符 / 快照无效一律 fail-closed（exit≠0），并逐项打印恢复方法。

检查项（全部同时成立才算 PASS）：
1. 只读打开 `--fin-db` / `--ev-db`（文件不存在 / 无法只读打开 → fail）；
2. schema 版本：`schema_migrations` 应用序列是 `MIGRATIONS` 声明顺序的合法前缀，且最新
   == `financial_v2.schema.SCHEMA_VERSION`（版本不符 / 空 / 非法前缀 → fail）；
3. 所需表齐全（缺表 → fail）；
4. current Record Set 存在且有 report_periods（推导 as_of_date）；
5. `current_snapshot` 指针解析出快照，且 **`snapshot_validity` 最新状态严格 == "valid"**
   （None / stale / superseded 一律 fail，绝不默认 valid）；
6. `report_blocked=false`、未 quarantine、scope/currency/purpose/as_of 匹配；
7. `snapshot_item` 与 `metric_result` 数量 > 0；
8. Evidence current-set inventory（status=current 且有 current evidence set 的文档）非空；
9. `EXTERNAL_SEARCH_PROVIDER == "bocha"` 且 `BOCHA_API_KEY` 已设置（**绝不打印 Key**）。

M930-1 新增项（§十：只**新增**，不改 1～9 的业务语义；同样严格只读、不建库、不迁移、不调
LLM / 网络、不生成 Demo run）：
10. Demo scope YAML 是否存在且可解析（`planning.demo_scope.DEFAULT_PROFILE_PATH`）；
11. 四类冻结资产（Contract v2 / SourcePolicy / WritingSpec / PresentationProfile）声明的
    version 与 content fingerprint 是否与实际资产一致（逐类一项，漂移即 fail）；
12. DemoScope Profile 是否可加载，且 selected / out_of_scope 对冻结 Contract 构成
    **精确分区**（section / topic / question / aspect 四层：不重叠、不遗漏、不越界）；
13. M930 依赖是否可 import：`sections.backbone_schema` 自检通过、`sections.backbone_artifacts`
    的 create-only 写入 / 严格读回 / validate-only / self-check 符号齐备；
14. 可选：`--run-dir` 指定的已存在 run 目录做 **validate-only** 校验（只读，不改字节）。

M930-3 新增项（3D：只**新增** current 写作主链的工件 / wire / 版本存在性与一致性检查，不改
1～14 的业务语义；同样只读、不建库、不迁移、不调 LLM / 网络、不生成 Demo run）：
15. current writer 相位就位：`sections.company_worker` 导出 writer 相位符号，且
    `BACKBONE_WRITER_PHASE_VERSION` 与 `BACKBONE_PHASE_VERSION` 不相等、`MAX_FOLLOW_UP_ROUNDS`
    恰为 1；
16. current 写作主链 wire 就位：`sections.narrative_schema` 的写作主链符号齐备，current
    `NARRATIVE_SCHEMA_VERSION`（**按符号取，不写死字面量**：写死的版本号会随每次 wire 升版
    变成一句假话）不与登记在案的 legacy 版本重叠；
17. current `claim-2` / `section-result-2` wire 就位：`sections.schema` 的 current 版本不与
    legacy 版本重叠，`load_legacy_section_result_for_audit` 在位，且 legacy class **不是**
    current class 的子类（legacy 不得冒充 current）；
18. current artifact index 就位：`demo-artifact-index-v2` 不与 legacy index 版本重叠、
    `load_legacy_run_artifacts_for_audit` 在位、current 写作主链成员角色与 `writer` producer
    步骤均已登记；
19. `FollowUpNeed` wire 就位：`harness.topic_runtime.run_follow_up_needs` 在位、拒绝理由码非空，
    且与 `FactNarrativeDisposition` 的 disposition 理由码**零交集**（两类身份不得混用）；
20. legacy-only 分流保持：`sections.artifact_loader` 只读入口在位，`scripts.run_phase4_demo`
    登记的 legacy Result marker 是已登记 legacy 版本、且**不是** current
    `section-result-2`（该 runner 必须保持 legacy-only）。

公司无关：company / scope / currency / purpose / 库路径全部经 CLI 传入，不硬编码公司名、
金额或科目；schema 版本经 `financial_v2.schema.SCHEMA_VERSION` + `MIGRATIONS` 声明校验，
不硬编码版本号。

CLI:
  python -m scripts.demo_preflight \
    --company 300750 --fin-db data/financial_v2.db --ev-db data/evidence.db
  python -m scripts.demo_preflight --company 300750 --run-dir evaluation/results/<run_id>
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from evidence import store as estore
from financial_v2 import schema as fschema
from financial_v2 import store as fstore

logger = logging.getLogger(__name__)

# preflight 只读查询所需的最小表集合（缺任一表 → fail-closed）。
_FIN_TABLES = (
    "schema_migrations",
    "financial_source_document",
    "current_record_set",
    "financial_record_set",
    "current_snapshot",
    "financial_snapshot",
    "snapshot_validity",
    "quarantine",
    "snapshot_item",
    "metric_result",
    "formula_definition",
)


@dataclass
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    recovery: str | None = None

    def as_dict(self) -> dict:
        d = {"name": self.name, "ok": self.ok, "detail": self.detail}
        if self.recovery:
            d["recovery"] = self.recovery
        return d


# ---------------------------------------------------------------------------
# 只读连接（不 init_db，不建表 / 迁移 / 写库）
# ---------------------------------------------------------------------------

def _ro_conn(path: str) -> sqlite3.Connection:
    """以 SQLite 只读模式打开现有库；文件缺失 / 打不开即抛错（fail-closed）。"""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"数据库文件不存在: {path}")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _check_schema_version(conn: sqlite3.Connection) -> tuple[bool, str]:
    """校验 schema_migrations 是 MIGRATIONS 合法前缀，且最新 == SCHEMA_VERSION。"""
    expected = fschema.SCHEMA_VERSION
    declared = fstore.MIGRATIONS[-1][0]
    if expected != declared:
        return False, f"代码不一致：SCHEMA_VERSION={expected} != 最新 migration={declared}"
    known = [v for v, _ in fstore.MIGRATIONS]
    try:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY rowid").fetchall()
    except sqlite3.Error as e:
        return False, f"schema_migrations 表缺失或不可读: {e}"
    applied = [r["version"] for r in rows]
    if not applied:
        return False, "schema_migrations 为空（库未初始化 / 不完整）"
    if applied != known[:len(applied)]:
        return False, (f"schema_migrations 应用序列非法前缀: {applied} "
                       f"（期望 {known[:len(applied)]}）")
    latest = known[len(applied) - 1]
    if latest != expected:
        return False, f"最新 migration {latest} != 期望 {expected}（需迁移）"
    return True, f"schema 版本 {latest}（期望 {expected}）"


def _missing_tables(conn: sqlite3.Connection, tables: tuple[str, ...]) -> list[str]:
    try:
        existing = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return list(tables)  # 无法读 schema（0 字节/非库文件）→ 视为全部缺失（fail-closed）
    return [t for t in tables if t not in existing]


def _derive_as_of_date(conn: sqlite3.Connection, company: str) -> str | None:
    """由 current Record Set 的 report_periods 推导 as_of_date（只读，与 progress 一致）。"""
    periods: list[str] = []
    docs = conn.execute(
        "SELECT source_document_id FROM financial_source_document WHERE company_id=?",
        (company,)).fetchall()
    for d in docs:
        cur = conn.execute(
            "SELECT record_set_version FROM current_record_set WHERE source_document_id=?",
            (d["source_document_id"],)).fetchone()
        if cur is None:
            continue
        rs = conn.execute(
            "SELECT report_periods FROM financial_record_set WHERE record_set_version=?",
            (cur["record_set_version"],)).fetchone()
        if rs is None:
            continue
        periods.extend(json.loads(rs["report_periods"] or "[]"))
    return max(periods) if periods else None


def _latest_validity(conn: sqlite3.Connection, snapshot_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM snapshot_validity WHERE snapshot_id=? "
        "ORDER BY event_at DESC, rowid DESC LIMIT 1",
        (snapshot_id,)).fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# 财务检查（严格只读）
# ---------------------------------------------------------------------------

def _check_financial(company: str, scope: str, currency: str,
                     purpose: str, fin_db: str) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []

    try:
        conn = _ro_conn(fin_db)
    except Exception as e:  # noqa: BLE001 — fail-closed
        logger.warning("只读打开 fin-db 失败: %s", e)
        return [PreflightCheck(
            name="financial.db_readonly_open", ok=False,
            detail=f"无法只读打开 --fin-db: {e}",
            recovery="检查 --fin-db 路径；用 `python -m scripts.run_financial_v2_chain` "
                     "重建财务主链")]

    try:
        ver_ok, ver_detail = _check_schema_version(conn)
        checks.append(PreflightCheck(
            name="financial.schema_version", ok=ver_ok, detail=ver_detail,
            recovery=None if ver_ok else "重建财务库 / 执行迁移"))

        missing = _missing_tables(conn, _FIN_TABLES)
        checks.append(PreflightCheck(
            name="financial.required_tables", ok=not missing,
            detail="缺表: " + ", ".join(missing) if missing else "所需表齐全",
            recovery="重建财务库" if missing else None))
        if not ver_ok or missing:
            return checks

        as_of_date = _derive_as_of_date(conn, company)
        if as_of_date is None:
            checks.append(PreflightCheck(
                name="financial.current_record_set", ok=False,
                detail=f"company={company} 无 current Record Set / report_periods",
                recovery="重建财务主链（run_financial_v2_chain / make demo-data）"))
            checks.append(PreflightCheck(
                name="financial.active_snapshot_id", ok=False,
                detail="无 current Record Set", recovery="重建财务主链"))
            return checks
        checks.append(PreflightCheck(
            name="financial.current_record_set", ok=True,
            detail=f"as_of_date={as_of_date}"))

        snap = conn.execute(
            "SELECT s.* FROM current_snapshot c "
            "JOIN financial_snapshot s ON s.snapshot_id = c.snapshot_id "
            "WHERE c.company_id=? AND c.scope=? AND c.currency=? AND c.as_of_date=? "
            "AND c.purpose=?",
            (company, scope, currency, as_of_date, purpose)).fetchone()
        if snap is None:
            checks.append(PreflightCheck(
                name="financial.active_snapshot_id", ok=False,
                detail=f"company={company} as_of={as_of_date} 无 current 快照指针",
                recovery="重建财务主链"))
            return checks

        snapshot_id = snap["snapshot_id"]

        validity = _latest_validity(conn, snapshot_id)
        checks.append(PreflightCheck(
            name="financial.validity", ok=validity == "valid",
            detail=f"{validity or 'None'}" + ("" if validity == "valid" else "（要求 valid）"),
            recovery=None if validity == "valid" else "重新构建快照 / 修复 validity"))

        blocked = bool(snap["report_blocked"])
        checks.append(PreflightCheck(
            name="financial.report_blocked", ok=not blocked,
            detail=f"report_blocked={blocked}",
            recovery=None if not blocked else "处理被阻断快照（人工门）"))

        q = conn.execute(
            "SELECT 1 FROM quarantine WHERE object_type='financial_snapshot' "
            "AND object_id=? LIMIT 1", (snapshot_id,)).fetchone()
        checks.append(PreflightCheck(
            name="financial.quarantine", ok=q is None,
            detail="未 quarantine" if q is None else "被 quarantine",
            recovery=None if q is None else "解除 quarantine / 重建快照"))

        dims_ok = (snap["scope"] == scope and snap["currency"] == currency
                   and snap["purpose"] == purpose and snap["as_of_date"] == as_of_date)
        checks.append(PreflightCheck(
            name="financial.scope_currency_purpose", ok=dims_ok,
            detail=(f"scope={snap['scope']} currency={snap['currency']} "
                    f"purpose={snap['purpose']} as_of={snap['as_of_date']}"),
            recovery=None if dims_ok else "快照维度与 --scope/--currency/--purpose 不符"))

        n_items = conn.execute(
            "SELECT COUNT(*) AS c FROM snapshot_item WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()["c"]
        n_metrics = conn.execute(
            "SELECT COUNT(*) AS c FROM metric_result WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()["c"]
        checks.append(PreflightCheck(
            name="financial.snapshot_item_count", ok=n_items > 0, detail=f"{n_items}",
            recovery=None if n_items > 0 else "snapshot_item 为空：重建财务主链"))
        checks.append(PreflightCheck(
            name="financial.metric_result_count", ok=n_metrics > 0, detail=f"{n_metrics}",
            recovery=None if n_metrics > 0 else "metric_result 为空：重建财务主链"))

        # 公式注册表完整性：formula_definition 非空，且本快照每个 metric_result 的
        # (formula_id, formula_version) 都能在 formula_definition 找到。缺失会令
        # CitationAuthority 把真实指标判 formula_not_found（→ 批量重复 rework target）。
        n_formulas = conn.execute(
            "SELECT COUNT(*) AS c FROM formula_definition").fetchone()["c"]
        checks.append(PreflightCheck(
            name="financial.formula_definition_count", ok=n_formulas > 0,
            detail=f"{n_formulas}",
            recovery=(None if n_formulas > 0 else
                      f"公式注册表为空：运行 `python -m financial_v2.formulas persist "
                      f"--db {fin_db}`")))

        missing_formula_rows = conn.execute(
            "SELECT DISTINCT m.formula_id, m.formula_version FROM metric_result m "
            "WHERE m.snapshot_id=? AND NOT EXISTS (SELECT 1 FROM formula_definition f "
            "WHERE f.formula_id=m.formula_id AND f.formula_version=m.formula_version)",
            (snapshot_id,)).fetchall()
        missing_formulas = [f"{r['formula_id']}@{r['formula_version']}"
                            for r in missing_formula_rows]
        checks.append(PreflightCheck(
            name="financial.metric_formula_versions_present", ok=not missing_formulas,
            detail=("全部指标公式版本已注册" if not missing_formulas
                    else f"缺失公式定义: {missing_formulas}"),
            recovery=(None if not missing_formulas else
                      f"运行 `python -m financial_v2.formulas persist --db {fin_db}` "
                      f"补齐公式注册")))

        active_ok = (validity == "valid" and not blocked and q is None and dims_ok
                     and n_items > 0 and n_metrics > 0
                     and n_formulas > 0 and not missing_formulas)
        checks.append(PreflightCheck(
            name="financial.active_snapshot_id", ok=active_ok, detail=snapshot_id,
            recovery=None if active_ok else "存在缺陷：见上列具体检查"))
        return checks
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evidence 检查（严格只读）
# ---------------------------------------------------------------------------

def _check_evidence(company: str, ev_db: str) -> list[PreflightCheck]:
    try:
        conn = _ro_conn(ev_db)
    except Exception as e:  # noqa: BLE001 — fail-closed
        logger.warning("只读打开 ev-db 失败: %s", e)
        return [PreflightCheck(
            name="evidence.db_readonly_open", ok=False,
            detail=f"无法只读打开 --ev-db: {e}",
            recovery="检查 --ev-db 路径；重建 Evidence current set（make demo-data）")]
    try:
        rows = conn.execute(
            "SELECT d.document_id FROM documents d "
            "JOIN evidence_sets es ON es.company_id=d.company_id "
            "AND es.document_id=d.document_id AND es.document_version=d.document_version "
            "AND es.status='current' "
            "WHERE d.company_id=? AND d.status='current' ORDER BY d.document_id",
            (company,)).fetchall()
    except sqlite3.Error as e:  # 空库/缺 documents/evidence_sets 表 → fail-closed
        return [PreflightCheck(
            name="evidence.current_set_inventory", ok=False,
            detail=f"documents/evidence_sets 表缺失或不可读: {e}",
            recovery="重建 Evidence current set（make demo-data / 入模管线）")]
    finally:
        conn.close()
    current = [r["document_id"] for r in rows]
    ok = len(current) > 0
    return [PreflightCheck(
        name="evidence.current_set_inventory", ok=ok,
        detail=f"{len(current)} 份 current 文档：{current}",
        recovery=None if ok else "重建 Evidence current set（make demo-data / 入模管线）")]


# ---------------------------------------------------------------------------
# 外部搜索提供方（只确认配置，绝不打印 Key）
# ---------------------------------------------------------------------------

def _check_provider() -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    provider = config.EXTERNAL_SEARCH_PROVIDER
    checks.append(PreflightCheck(
        name="provider.external_search_provider", ok=provider == "bocha",
        detail=provider,
        recovery=None if provider == "bocha" else "在 .env 设 EXTERNAL_SEARCH_PROVIDER=bocha"))
    checks.append(PreflightCheck(
        name="provider.bocha_api_key_present", ok=bool(config.BOCHA_API_KEY),
        detail="已配置" if config.BOCHA_API_KEY else "未配置",
        recovery=None if config.BOCHA_API_KEY else "在 .env 设 BOCHA_API_KEY=<key>"))
    return checks


# ---------------------------------------------------------------------------
# M930 Demo Backbone 依赖（M930-1 §十：只**新增**检查，不改既有检查语义）
#
# 只做存在性 / 版本 / 指纹 / 可导入性判定与可选的 run 目录 validate-only：
# 不建库、不迁移、不研究、不调 LLM / 网络、不生成任何 Demo run。
# ---------------------------------------------------------------------------

#: Profile 中声明的资产块 → 人类可读名称（仅用于 recovery 文案）。
_BACKBONE_ASSET_LABELS = {
    "contract": "冻结 Contract v2",
    "source_policy": "SourcePolicy",
    "writing_spec": "WritingSpec",
    "presentation_profile": "PresentationProfile",
}


def _check_backbone_scope() -> list[PreflightCheck]:
    """Demo scope YAML 存在性、四类冻结资产版本/指纹、分区守恒。"""
    checks: list[PreflightCheck] = []
    scope_rel = "templates/demo_scopes/interview_backbone_v1.yaml"
    profile_path = None
    doc: dict | None = None
    try:
        from planning import demo_scope as ds
        scope_rel = ds.DEFAULT_PROFILE_PATH
        profile_path = ds.REPO_ROOT / scope_rel
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name="backbone.scope_module_importable", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 planning.demo_scope 的导入错误（M930-1 交付物）"))
        return checks

    ok_exists = bool(profile_path) and profile_path.is_file()
    checks.append(PreflightCheck(
        name="backbone.scope_profile_exists", ok=ok_exists,
        detail=f"{scope_rel}" + ("" if ok_exists else "（不存在）"),
        recovery=None if ok_exists else f"补齐 {scope_rel}"))
    if not ok_exists:
        return checks

    try:
        import yaml

        doc = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("DemoScope Profile 顶层不是 mapping")
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name="backbone.scope_yaml_readable", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 DemoScope Profile YAML"))
        return checks
    checks.append(PreflightCheck(
        name="backbone.scope_yaml_readable", ok=True, detail=f"{scope_rel} 可解析"))

    # 四类冻结资产：逐类校验 version 声明与 content fingerprint
    from contracts import schema_v2 as cs2

    assets = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
    for kind, label in _BACKBONE_ASSET_LABELS.items():
        name = f"backbone.asset.{kind}"
        block = assets.get(kind)
        if not isinstance(block, dict) or "asset" not in block or "fingerprint" not in block:
            checks.append(PreflightCheck(
                name=name, ok=False, detail="profile.assets 声明缺失",
                recovery=f"在 profile 声明 {label} 的 asset / fingerprint"))
            continue
        try:
            asset_doc = ds.load_frozen_asset_doc(block["asset"], f"冻结资产 {kind}")
            actual = cs2.content_fingerprint(asset_doc)
            identity = {k: v for k, v in block.items()
                        if k not in ("asset", "fingerprint")}
            drifted = {k: f"声明 {v!r} ≠ 资产 {asset_doc.get(k)!r}"
                       for k, v in identity.items() if asset_doc.get(k) != v}
            if drifted:
                checks.append(PreflightCheck(
                    name=name, ok=False, detail=f"{label} 版本漂移: {drifted}",
                    recovery="使 profile 声明的版本与冻结资产一致"))
            elif actual != block["fingerprint"]:
                checks.append(PreflightCheck(
                    name=name, ok=False,
                    detail=(f"{label} fingerprint 漂移: 声明 "
                            f"{str(block['fingerprint'])[:12]}… ≠ 实际 {actual[:12]}…"),
                    recovery="重新计算并回填 profile 的 fingerprint"))
            else:
                ident = " ".join(f"{k}={v}" for k, v in identity.items())
                checks.append(PreflightCheck(
                    name=name, ok=True,
                    detail=f"{label} {block['asset']} {ident} fp={actual[:12]}…"))
        except Exception as exc:  # noqa: BLE001
            checks.append(PreflightCheck(
                name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
                recovery=f"修复 {label} 冻结资产或其声明"))
            continue

    # Demo scope 加载 + 精确分区守恒
    try:
        from contracts.loader_v2 import load_contract_v2

        profile = ds.load_demo_scope_profile(profile_path)
        contract_path = Path(profile.contract_asset)
        if not contract_path.is_absolute():
            contract_path = ds.REPO_ROOT / contract_path
        contract = load_contract_v2(str(contract_path))
        index = ds._index_contract(contract)
        sel = profile.selected_id_sets()
        oos = profile.out_of_scope_id_sets()
        parts = []
        conservation = True
        for level in ("section", "topic", "question", "aspect"):
            total = len(index[level + "s"])
            s, o = set(sel[level]), set(oos[level])
            if len(sel[level]) + len(oos[level]) != total or (s & o):
                conservation = False
            parts.append(f"{level} {len(sel[level])}+{len(oos[level])}={total}")
        detail = "；".join(parts)
        if not conservation:
            checks.append(PreflightCheck(
                name="backbone.scope_partition_conservation", ok=False,
                detail=f"selected/out_of_scope 未构成精确分区: {detail}",
                recovery="修正 profile 的 selected_sections / selected_topics"))
        else:
            checks.append(PreflightCheck(
                name="backbone.scope_partition_conservation", ok=True,
                detail=f"精确分区守恒: {detail}"))
        checks.append(PreflightCheck(
            name="backbone.scope_profile_loadable", ok=True,
            detail=(f"{profile.profile_id}@{profile.profile_version} "
                    f"fp={profile.profile_fingerprint[:12]}…")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name="backbone.scope_partition_conservation", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 DemoScope Profile / 冻结 Contract 不一致"))
        checks.append(PreflightCheck(
            name="backbone.scope_profile_loadable", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="demo_scope.load_demo_scope_profile() 必须 fail-closed 通过"))
    return checks


def _check_backbone_schema_and_artifacts() -> list[PreflightCheck]:
    """M930 schema / artifact 模块可导入，且 schema 自检通过（只读、不写盘）。"""
    checks: list[PreflightCheck] = []
    try:
        from sections import backbone_schema as bs

        report = bs.self_check()
        checks.append(PreflightCheck(
            name="backbone.schema_self_check", ok=bool(report.get("ok")),
            detail=f"passed={report.get('passed')} failed={report.get('failed')}",
            recovery=None if report.get("ok") else "修复 sections.backbone_schema 的 self-check"))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name="backbone.schema_self_check", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.backbone_schema 的导入 / 自检"))

    try:
        from sections import backbone_artifacts as ba

        missing = [n for n in ("write_run_artifacts", "load_run_artifacts",
                               "validate_run_artifacts", "self_check")
                   if not hasattr(ba, n)]
        if missing:
            raise AttributeError(f"缺符号 {missing}")
        checks.append(PreflightCheck(
            name="backbone.artifacts_importable", ok=True,
            detail=(f"create-only 写入 / 严格读回 / validate-only / self-check 齐备；"
                    f"safety_policy={ba.SAFETY_POLICY_ID} "
                    f"posix_dir_fsync={ba.POSIX_DIRECTORY_FSYNC_AVAILABLE}")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name="backbone.artifacts_importable", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.backbone_artifacts 的导入 / 符号"))
    return checks


# ---------------------------------------------------------------------------
# M930-3 current 写作主链（3D：只**追加** current 工件 / wire / 版本存在性与一致性检查）
#
# 与 M930-1 段同样只做 import + 常量比对：不建库、不迁移、不研究、不调 LLM / 网络、
# 不生成任何 run。回答的是「current 写作主链是否就位，且与 legacy 面清晰分流」。
# ---------------------------------------------------------------------------

#: current 写作主链必须齐备的 wire 符号（`sections.narrative_schema`）。
_CURRENT_NARRATIVE_WIRE_SYMBOLS = (
    "SectionDraft", "ClaimCandidate", "NarrativeDraftUnit", "ProposedSupportRef",
    "ClaimBindingDecision", "ClaimEntailmentDecision", "AcceptedSupportBinding",
    "SectionNarrative", "FactNarrativeDisposition", "WriterMaterialManifest",
    "WriterMaterialManifestEntry", "ClaimSupportRef",
)

#: current writer 相位（`sections.company_worker`）必须导出的符号。
_CURRENT_WRITER_PHASE_SYMBOLS = (
    "run_backbone_writer_phase", "BackboneWriterSectionInput",
    "BackboneSectionWriterOutput", "BackboneWriterPhase", "BackboneWriterPhaseError",
    "BACKBONE_WRITER_PHASE_VERSION", "BACKBONE_PHASE_VERSION", "MAX_FOLLOW_UP_ROUNDS",
)

#: current artifact index 必须登记的 narr-4 成员角色（§16.7 矩阵的写作主链成员）。
_CURRENT_ARTIFACT_ROLES = (
    "section_draft", "claim_candidate", "narrative_draft_unit", "support_proposal",
    "claim_binding_decision", "claim_entailment_decision", "accepted_support_binding",
    "section_claim", "section_narrative", "section_result", "fact_narrative_disposition",
    "follow_up_need",
)


def _check_backbone_current_chain() -> list[PreflightCheck]:
    """M930-3 current 写作主链：符号存在性 + 版本 / 身份分流一致性（只读、不写盘）。"""
    checks: list[PreflightCheck] = []

    # ---- 15. current writer 相位 ----
    name = "backbone.writer_phase_wire"
    try:
        from sections import company_worker as cw

        missing = [n for n in _CURRENT_WRITER_PHASE_SYMBOLS if not hasattr(cw, n)]
        if missing:
            raise AttributeError(f"缺符号 {missing}")
        versions = (cw.BACKBONE_WRITER_PHASE_VERSION, cw.BACKBONE_PHASE_VERSION)
        if len(set(versions)) != len(versions):
            raise ValueError(f"writer / company 相位版本号不得相同: {versions}")
        if cw.MAX_FOLLOW_UP_ROUNDS != 1:
            raise ValueError(
                f"MAX_FOLLOW_UP_ROUNDS={cw.MAX_FOLLOW_UP_ROUNDS}（当前门要求恰为 1，"
                f"禁止无界返修）")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail=(f"writer_phase={cw.BACKBONE_WRITER_PHASE_VERSION} "
                    f"company_phase={cw.BACKBONE_PHASE_VERSION} "
                    f"max_follow_up_rounds={cw.MAX_FOLLOW_UP_ROUNDS}")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.company_worker 的 M930-3 writer 相位导出 / 版本"))

    # ---- 16. current 写作主链 wire ----
    name = "backbone.narrative_wire_version"
    try:
        from sections import narrative_schema as ns

        missing = [n for n in _CURRENT_NARRATIVE_WIRE_SYMBOLS if not hasattr(ns, n)]
        if missing:
            raise AttributeError(f"缺符号 {missing}")
        current = ns.NARRATIVE_SCHEMA_VERSION
        legacy = tuple(ns.LEGACY_NARRATIVE_SCHEMA_VERSIONS)
        if not legacy:
            raise ValueError("LEGACY_NARRATIVE_SCHEMA_VERSIONS 为空（legacy 面必须显式登记）")
        if current in legacy:
            raise ValueError(f"current {current!r} 不得同时登记为 legacy: {legacy}")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail=(f"narrative={current} legacy={legacy} "
                    f"binding_gate={ns.CLAIM_BINDING_GATE_VERSION} "
                    f"entailment_rules={ns.CLAIM_ENTAILMENT_RULES_VERSION} "
                    f"narrative_gate={ns.NARRATIVE_GATE_VERSION}")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.narrative_schema 的写作主链 wire 导出 / 版本登记"))

    # ---- 17. current claim-2 / section-result-2 wire + legacy class 不冒充 current ----
    name = "backbone.claim_result_wire_version"
    try:
        from sections import schema as ss

        if not hasattr(ss, "load_legacy_section_result_for_audit"):
            raise AttributeError("缺符号 load_legacy_section_result_for_audit")
        cur_res, cur_claim = ss.SECTION_RESULT_SCHEMA_VERSION, ss.CLAIM_SCHEMA_VERSION
        leg_res = tuple(ss.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS)
        leg_claim = tuple(ss.LEGACY_CLAIM_SCHEMA_VERSIONS)
        if cur_res in leg_res or cur_claim in leg_claim:
            raise ValueError(
                f"current wire 不得同时登记为 legacy: {cur_res!r} in {leg_res}, "
                f"{cur_claim!r} in {leg_claim}")
        for legacy_cls, current_cls in ((ss.LegacySectionResultV1, ss.SectionResult),
                                        (ss.LegacySectionClaimV1, ss.SectionClaim)):
            if issubclass(legacy_cls, current_cls):
                raise ValueError(
                    f"{legacy_cls.__name__} 不得是 {current_cls.__name__} 的子类"
                    f"（legacy 不得冒充 current）")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail=(f"result={cur_res} legacy_result={leg_res} claim={cur_claim} "
                    f"legacy_claim={leg_claim}（legacy view 非 current 子类）")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.schema 的 claim-2 / section-result-2 wire 与 legacy view"))

    # ---- 18. current artifact index ----
    name = "backbone.artifact_index_version"
    try:
        from sections import backbone_artifacts as ba

        if not hasattr(ba, "load_legacy_run_artifacts_for_audit"):
            raise AttributeError("缺符号 load_legacy_run_artifacts_for_audit")
        current = ba.ARTIFACT_INDEX_SCHEMA_VERSION
        legacy = tuple(ba.LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS)
        if not legacy or current in legacy:
            raise ValueError(f"index 版本登记非法: current={current!r} legacy={legacy}")
        roles = tuple(ba.ARTIFACT_MEMBER_ROLES)
        missing_roles = [r for r in _CURRENT_ARTIFACT_ROLES if r not in roles]
        if missing_roles:
            raise ValueError(f"缺 current 写作主链成员角色 {missing_roles}")
        if "writer" not in ba.ARTIFACT_PRODUCER_STEPS:
            raise ValueError("ARTIFACT_PRODUCER_STEPS 未登记 writer 相位")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail=(f"index={current} legacy={legacy} roles={len(roles)}"
                    f"（current 写作主链角色齐备，含 writer producer）")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.backbone_artifacts 的 index 版本 / 角色登记"))

    # ---- 19. FollowUpNeed wire + 拒绝理由码与 disposition 理由码零交集 ----
    name = "backbone.follow_up_wire"
    try:
        from harness import topic_runtime as tr
        from sections import narrative_schema as ns

        if not hasattr(tr, "run_follow_up_needs"):
            raise AttributeError("缺符号 harness.topic_runtime.run_follow_up_needs")
        reasons = tuple(tr.FOLLOW_UP_REJECTION_REASONS)
        if not reasons:
            raise ValueError("FOLLOW_UP_REJECTION_REASONS 为空（拒绝理由码必须是闭集）")
        overlap = sorted(set(reasons) & set(ns.DISPOSITION_REASON_CODES))
        if overlap:
            raise ValueError(
                f"拒绝理由码与 disposition 理由码不得混用（交集非空）: {overlap}")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail=(f"follow_up_rejection_reasons={len(reasons)} "
                    f"disposition_reason_codes={len(ns.DISPOSITION_REASON_CODES)} "
                    f"交集=∅（材料/事实/否定三类身份不混用）")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 harness.topic_runtime 的 FollowUpNeed 裁决 wire / 理由码闭集"))

    # ---- 20. legacy-only 只读入口 + Phase 4 runner 保持 legacy-only ----
    name = "backbone.artifact_loader_legacy_only"
    try:
        from sections import artifact_loader as al

        missing = [n for n in ("load_phase4_run", "list_phase4_runs", "ArtifactLoaderError")
                   if not hasattr(al, n)]
        if missing:
            raise AttributeError(f"缺符号 {missing}")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail="只读入口在位（legacy marker 分流，绝不升级 current / 不写入 Store）"))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 sections.artifact_loader 的只读入口符号"))

    name = "backbone.phase4_runner_legacy_only"
    try:
        from sections import schema as ss
        from scripts import run_phase4_demo as rpd

        marker = rpd.LEGACY_RESULT_MARKER
        if marker not in ss.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS:
            raise ValueError(f"legacy Result marker {marker!r} 未登记为 legacy 版本")
        if marker == ss.SECTION_RESULT_SCHEMA_VERSION:
            raise ValueError(
                f"Phase 4 runner 必须保持 legacy-only，不得写出 current "
                f"{ss.SECTION_RESULT_SCHEMA_VERSION!r}")
        checks.append(PreflightCheck(
            name=name, ok=True,
            detail=(f"{rpd.__name__} marker={marker!r}"
                    f"（已登记 legacy，且非 current {ss.SECTION_RESULT_SCHEMA_VERSION!r}）")))
    except Exception as exc:  # noqa: BLE001
        checks.append(PreflightCheck(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            recovery="修复 scripts.run_phase4_demo 的 legacy Result marker"))

    return checks


def _check_backbone_run_dir(run_dir: str) -> list[PreflightCheck]:
    """可选：对已存在的 run 目录做 validate-only（只读，不建库、不改字节）。"""
    try:
        from sections import backbone_artifacts as ba

        verdict = ba.validate_run_artifacts(run_dir)
        if verdict.get("ok"):
            return [PreflightCheck(
                name="backbone.run_dir_validate_only", ok=True,
                detail=(f"{run_dir} run_id={verdict['run_id']} "
                        f"manifest={verdict['manifest_id']} "
                        f"members={verdict['member_count']}"))]
        return [PreflightCheck(
            name="backbone.run_dir_validate_only", ok=False,
            detail=str(verdict.get("error")),
            recovery="重新生成该 run 目录（create-only，不覆盖）")]
    except Exception as exc:  # noqa: BLE001
        return [PreflightCheck(
            name="backbone.run_dir_validate_only", ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            recovery="确认 run 目录存在且由 M930 artifact 协议生成")]


# ---------------------------------------------------------------------------
# 主流程（无 init_db，全程只读）
# ---------------------------------------------------------------------------

def run(*, company: str, scope: str, currency: str, purpose: str,
        fin_db: str, ev_db: str, run_dir: str | None = None) -> dict:
    checks = (
        _check_financial(company, scope, currency, purpose, fin_db)
        + _check_evidence(company, ev_db)
        + _check_provider()
        + _check_backbone_scope()
        + _check_backbone_schema_and_artifacts()
        + _check_backbone_current_chain()
    )
    if run_dir:
        checks = checks + _check_backbone_run_dir(run_dir)
    ok = all(c.ok for c in checks)
    return {
        "company": company,
        "fin_db": fin_db,
        "ev_db": ev_db,
        "ok": ok,
        "passed": sum(1 for c in checks if c.ok),
        "failed": sum(1 for c in checks if not c.ok),
        "checks": [c.as_dict() for c in checks],
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.demo_preflight",
        description="Demo 环境只读 preflight（缺失 fail-closed，不改任何规则/库）")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--scope", default="consolidated", help="statement_scope")
    parser.add_argument("--currency", default="CNY", help="currency")
    parser.add_argument("--purpose", default="credit_analysis", help="快照 purpose")
    parser.add_argument("--fin-db", default=str(fstore.DEFAULT_DB_PATH),
                        help="financial_v2 SQLite 库路径")
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH),
                        help="evidence SQLite 库路径")
    parser.add_argument("--run-dir", default=None,
                        help="可选：对已存在的 M930 run 目录做 validate-only 检查")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    result = run(company=args.company, scope=args.scope, currency=args.currency,
                 purpose=args.purpose, fin_db=args.fin_db, ev_db=args.ev_db,
                 run_dir=args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv[1:]))

"""Eval: topic Store 迁移台账与 legacy/current 一严一松（§16.10 #17/#28，M930-3A）。

用法: python -m evals.test_demo_topic_store_migration

在临时库上证明（不触碰任何历史库）：

 1. #17 迁移台账：`STORE_SCHEMA_VERSION` 与最后一条 migration 一致；台账逐版本追加 1→6；
    重复 init 幂等；台账序列**非合法前缀**（跳号 / 多余版本）一律 fail-closed，不猜测、不补登；
 2. #17 legacy/current 一严一松：v4/v5 旧行只经 `load_legacy_topic_pack_for_audit` 只读回看
    （返回 `LegacyTopicPackAuditView`，不是 current Pack）；current reader 对旧行
    `SchemaVersionIncompatibleError`；legacy reader 对 current 行拒绝 —— 两条路径互不冒充；
 3. #17 legacy 视图不得凭空补 current 依赖键（v4/v5 的 `dependency_versions` 从未落库，视图
    既不提供该字段、也不据指纹反推）；
 4. #28 依赖版本精确等集（15→18）：旧 v5 15 键字典**不得**被 current 校验器接受、不得自动
    补 3 个 M930-3 键；current 工厂是唯一入口，签名里没有逐键覆盖参数；
 5. 迁移 5 是纯追加：全部 DDL 语句里没有 UPDATE / DELETE / DROP / ALTER（不改写历史行）。
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS  # noqa: E402
from harness import topic_store as TST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str, exc_type=Exception) -> None:
    try:
        fn()
    except exc_type as exc:  # noqa: BLE001
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


_LEGACY_PACK = "pack_legacy_v5"
_CURRENT_PACK = "pack_current_v6"


def _exec(db: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _rows(db: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _versions(db: Path) -> list[str]:
    return [r["version"] for r in _rows(
        db, "SELECT version FROM topic_schema_migrations ORDER BY rowid")]


def _insert_pack_row(db: Path, pack_id: str, schema_version: str) -> None:
    """直接落一条 topic_pack 物理行（模拟 v5 旧行 / current 行；不走 current 写入口）。"""
    cols = sorted(TST._V1_TOPIC_PACK_COLUMNS)
    values = {c: f"x_{c}" for c in cols}
    values.update({
        "pack_id": pack_id, "schema_version": schema_version,
        "report_as_of": "2026-06-30", "external_funnel": None, "run_id": "run-legacy-1",
        "task_id": "task-legacy-1", "company_id": "company-legacy",
        "section_id": "company", "topic_id": "company_identity",
        "contract_version": "contract-legacy-2", "source_policy_version": "sp-legacy-1",
        "question_ids": json.dumps(["company_subject_match"]), "outcome_refs": json.dumps([]),
        "usage": json.dumps({}), "uncertain_calls": json.dumps([]),
        "process_status": "completed", "coverage_status": "covered",
        "status_derivation": json.dumps({}), "contract_fingerprint": "fp" * 4,
        "dependency_fingerprint": "df" * 4, "content_fingerprint": "cf" * 4,
        "created_at": "2026-06-30T00:00:00Z",
    })
    placeholders = ", ".join("?" for _ in cols)
    _exec(db, f"INSERT INTO topic_pack ({', '.join(cols)}) VALUES ({placeholders})",
          tuple(values[c] for c in cols))


def main() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "topic_store.sqlite"
        TST.init_topic_store(db)

        # ------------------------------------------------------------------
        # 1. 台账：版本常量一致、逐版本追加、重复 init 幂等。
        # ------------------------------------------------------------------
        known = [v for v, _ in TST.MIGRATIONS]
        check(TST.MIGRATIONS[-1][0] == TS.STORE_SCHEMA_VERSION == "6",
              "STORE_SCHEMA_VERSION 等于最后一条 migration（6）")
        check(known == ["1", "2", "3", "4", "5", "6"],
              f"migration 台账是追加的 1→6（实际 {known}）")
        check(_versions(db) == known,
              "首次 init 逐版本登记（不跳号、不重复）")
        check(TST.applied_schema_version() == "6"
              and TST.self_check()["ok"] is True
              and TST.self_check()["schema_version"] == "6",
              "applied_schema_version / self_check 读数与台账一致")

        TST.init_topic_store(db)
        check(_versions(db) == known,
              "对已初始化库重复 init 是幂等追加（台账不变、不重跑迁移）")

        # ------------------------------------------------------------------
        # 2. legacy / current 一严一松。
        # ------------------------------------------------------------------
        _insert_pack_row(db, _LEGACY_PACK, "5")
        _insert_pack_row(db, _CURRENT_PACK, TS.TOPIC_PACK_SCHEMA_VERSION)
        check("5" in TS.LEGACY_TOPIC_PACK_SCHEMA_VERSIONS
              and "4" in TS.LEGACY_TOPIC_PACK_SCHEMA_VERSIONS
              and TS.TOPIC_PACK_SCHEMA_VERSION not in TS.LEGACY_TOPIC_PACK_SCHEMA_VERSIONS,
              "legacy 版本集含 v4/v5，且不含 current v7（二者不重叠）")

        view = TST.load_legacy_topic_pack_for_audit(_LEGACY_PACK)
        check(view is not None
              and isinstance(view, TS.LegacyTopicPackAuditView)
              and not isinstance(view, TS.TopicResearchPack)
              and view.legacy_schema_version == "5"
              and view.pack_id == _LEGACY_PACK,
              "v5 旧行经显式只读入口还原为 audit view，类型不是 current Pack")
        check(view.identity.section_id == "company" and view.run_id == "run-legacy-1"
              and view.contract_version == "contract-legacy-2"
              and (view.aspect_count, view.material_count, view.fact_count) == (0, 0, 0),
              "audit view 回看的是旧行的身份/来源事实与子行计数（不重算、不构造 current Pack）")
        check(not hasattr(view, "dependency_versions")
              and not hasattr(view, "aspect_results")
              and not hasattr(view, "to_pack"),
              "legacy 视图不提供 dependency_versions，也不给「变回 current Pack」的出口")
        check(TST.load_legacy_topic_pack_for_audit("pack_missing") is None,
              "不存在的行返回 None（不补建）")

        expect_raises("current reader 读 v5 旧行",
                      lambda: TST.get_pack(_LEGACY_PACK),
                      "schema_version_stale", TST.SchemaVersionIncompatibleError)
        expect_raises("legacy reader 读 current v7 行",
                      lambda: TST.load_legacy_topic_pack_for_audit(_CURRENT_PACK),
                      "只服务 legacy", TST.TopicStoreValidationError)
        # 一严一松的另一半：legacy reader 不因「读不懂」而放宽，current reader 不因「读不到」
        # 而回退到 legacy 口径。
        check(TS.TOPIC_PACK_SCHEMA_VERSION == "7",
              "current Pack 实现版本为 v7（旧行必须走 legacy 入口，不得被静默当 current 消费）")

        # ------------------------------------------------------------------
        # 3. 台账非法序列 fail-closed（跳号 / 多余版本）。
        # ------------------------------------------------------------------
        _exec(db, "INSERT INTO topic_schema_migrations (version, applied_at) VALUES (?,?)",
              ("9", "2026-06-30T00:00:00Z"))
        check(_versions(db)[-1] == "9", "对照：人为登记一条多余版本")
        expect_raises("台账含多余版本",
                      lambda: TST.self_check(), "非合法前缀", RuntimeError)
        _exec(db, "DELETE FROM topic_schema_migrations WHERE version='9'")
        _exec(db, "DELETE FROM topic_schema_migrations WHERE version='4'")
        check(_versions(db) == [v for v in known if v != "4"],
              "对照：人为删除中间版本（跳号）")
        expect_raises("台账跳号",
                      lambda: TST.self_check(), "非合法前缀", RuntimeError)
        expect_raises("台账跳号时 applied_schema_version 也 fail-closed",
                      lambda: TST.applied_schema_version(), "非合法前缀", RuntimeError)

        # ------------------------------------------------------------------
        # 4. #28 依赖版本精确等集（15→18），旧字典不得被补全。
        # ------------------------------------------------------------------
        v6_keys = TS.DEPENDENCY_VERSION_KEYS
        v5_keys = TS.LEGACY_V5_DEPENDENCY_VERSION_KEYS
        v4_keys = TS.LEGACY_V4_DEPENDENCY_VERSION_KEYS
        check(len(v6_keys) == 18 and len(v5_keys) == 15 and len(v4_keys) == 6,
              f"依赖键 6→15→18（实际 {len(v4_keys)}/{len(v5_keys)}/{len(v6_keys)}）")
        check(set(v5_keys) <= set(v6_keys) and set(v4_keys) <= set(v5_keys),
              "旧版本键集是 current 的真子集（升级只增键）")
        check(set(v6_keys) - set(v5_keys)
              == {"material_disposition", "fact_qualification", "external_fact"},
              "M930-3 新增的正是三项资格/处置/外部事实版本键")
        check(len(set(v6_keys)) == len(v6_keys) and all(k for k in v6_keys),
              "current 依赖键互不重复且非空")

        legacy_dict = {k: "legacy-1" for k in v5_keys}
        expect_raises("旧 v5 15 键字典交给 current 校验器",
                      lambda: TS.validate_dependency_versions(legacy_dict),
                      "缺键不得自动补全")
        expect_raises("空 dict",
                      lambda: TS.validate_dependency_versions({}),
                      "不得为空")
        partial = {k: "v" for k in v6_keys if k != "external_fact"}
        expect_raises("current 18 键少一键",
                      lambda: TS.validate_dependency_versions(partial), "缺键")
        extra = {**{k: "v" for k in v6_keys}, "legacy_auto_fill": "v"}
        expect_raises("current 18 键多一未知键",
                      lambda: TS.validate_dependency_versions(extra), "未知键")
        empty_value = {**{k: "v" for k in v6_keys}, "external_fact": ""}
        expect_raises("依赖键值为空",
                      lambda: TS.validate_dependency_versions(empty_value), "非空字符串")

        built = TS.build_current_dependency_versions(contract_version="c-1",
                                                    source_policy_version="sp-1")
        check(set(built) == set(v6_keys) and len(built) == 18
              and built == TS.validate_dependency_versions(built),
              "current 工厂产出恰好 18 键且自证合规（构造即校验）")
        check(TS.build_current_dependency_versions(contract_version="c-1",
                                                  source_policy_version="sp-1") is not built
              and TS.build_current_dependency_versions(contract_version="c-1",
                                                       source_policy_version="sp-1") == built,
              "工厂每次返回新对象、内容确定")
        check(TS.build_current_dependency_versions(contract_version="c-2",
                                                   source_policy_version="sp-1") != built,
              "Contract 轴变化 ⇒ 依赖版本 dict 变化")
        params = set(inspect.signature(TS.build_current_dependency_versions).parameters)
        check(params == {"contract_version", "source_policy_version"},
              f"工厂签名只有两个真实变化的轴（无可逐键覆盖入口）：{sorted(params)}")

        # ------------------------------------------------------------------
        # 5. 迁移是纯追加：DDL 里没有改写历史行的语句。
        # ------------------------------------------------------------------
        # 只扫建表/建索引语句：不可变触发器体内含 `BEFORE UPDATE/DELETE ON`，那是禁令而非改写。
        def _ddl_bodies(statements: list[str]) -> list[str]:
            return [s for s in statements
                    if s.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX"))]

        all_bodies = _ddl_bodies(TST._ddl_statements())
        trigger_count = 2 * len(TST._IMMUTABLE_TABLES)
        check(len(all_bodies) + trigger_count == len(TST._ddl_statements()),
              f"DDL 共 {len(TST._ddl_statements())} 条 = {len(all_bodies)} 条建表/建索引 + "
              f"{trigger_count} 条不可变触发器（每张不可变表 no_update/no_delete 各一）")
        joined = "\n".join(all_bodies).upper()
        check(not any(clause in joined for clause in
                      ("UPDATE ", "DELETE FROM", "DROP TABLE", "ALTER TABLE", "DROP INDEX")),
              "全部建表/建索引语句里没有 UPDATE/DELETE/DROP/ALTER（迁移只追加，不改写历史行）")
        migration5 = "\n".join(_ddl_bodies(TST._successor_ddl_statements())).upper()
        check(migration5.count("CREATE TABLE IF NOT EXISTS") == 9
              and "UPDATE " not in migration5 and "DELETE FROM" not in migration5,
              "迁移 5 只 CREATE 9 张新表（6 张 Pack 子表 + 3 张 follow-up 表），不改历史行")
        physical = {r["name"] for r in _rows(db, "PRAGMA table_info(topic_pack)")}
        check(physical == TST._V1_TOPIC_PACK_COLUMNS,
              "topic_pack 物理列自 v1 起未变（迁移 2–6 只升级解释语义/新增表，不动老列）")
        # 迁移 6（Pack v7 跨源轴）：同样只 CREATE 4 张新表，不改写历史行。
        migration6 = "\n".join(_ddl_bodies(TST._source_axis_ddl_statements())).upper()
        check(migration6.count("CREATE TABLE IF NOT EXISTS") == 4
              and "UPDATE " not in migration6 and "DELETE FROM" not in migration6
              and "ALTER TABLE" not in migration6,
              "迁移 6 只 CREATE 4 张跨源轴表（源集/责任/四臂结果/比较审计），不改历史行")
        check(set(TS.LEGACY_TOPIC_PACK_SCHEMA_VERSIONS) <= {"", "4", "5"},
              f"legacy Pack 版本集不含 v6/v7（实际 {TS.LEGACY_TOPIC_PACK_SCHEMA_VERSIONS}）")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

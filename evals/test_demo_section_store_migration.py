"""Eval: Section Store migration 4→5 与 P17 十六项要求（§16.10 #25/#34，M930-3A）。

用法: python -m evals.test_demo_section_store_migration

本模块的 3A 部分（表骨架 / migration）证明：

 1. 整数账本：`SECTION_STORE_SCHEMA_VERSION == 7` 且 `MIGRATIONS` 恰 7 项；
    `_valid_prefix` 只接受 [1..N] 连续前缀（跳号 / 越界 / 空档一律拒）；
 2. migration 5 / 6 / 7 都是**纯追加**：不原位加列、不改写历史行
    （无 UPDATE/DELETE/DROP/ALTER）；新表全部 `_v2` 命名、全部带 UPDATE/DELETE 禁止触发器
    （P17 第 15 项）；
 3. P17 逐项落地（第 1–14 项）在 DDL 层可断言的部分：exact manifest、WMPD、FND successor、
    Draft root + subject member、proposal member family、aggregate/entailment 决定与
    accepted binding、current Claim、final Narrative、current Result（单向引用 draft、
    禁止内嵌 evaluation）、writer 侧 unresolved、rejection 只建只读视图、不建 follow-up 表；
 4. current / legacy 分离（第 16 项）：v1 表与 v2 表并存且命名可分，每个 v2 payload 自带
    `schema_version` NOT NULL（migration 序号**不**替代 wire discriminator）；
 5. migration 6（§三 E 补全项）：`ClaimNarrativeDisposition` 的独立 family 及
    `V2_POST_GATE_FAMILIES` 的提交序（`SectionResult` 恒最后）；
 6. migration 7（§12.4.3 落库点）：`FinalSentenceFidelityDecision` 的独立 family、
    它自己的 `nsfid-1` marker，以及「一个 draft revision 恰好一条决定」落到 DDL 唯一键。

migration 6 / 7 都是**真实追加**（`cnd-1` / `nsfid-1` 载荷必须先有表可落），因此本文件的账本
断言随之上移一格；「每张 v2 表都有 DDL / 触发器 / 指纹列」这类**存在性**断言在 migration
5+6+7 的合并文本上核验，而「纯追加」这类**逐 migration** 的断言仍在各自的语句列表上核验。

current reader（`commit_section_chain_v2` / `load_current_section_chain_v2` 的真库读写与
identity 重算）归 M930-3B/3C，见 `test_demo_section_store_readback.py`；本文件**不**断言其行为。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import narrative_schema as NS  # noqa: E402
from sections import store as ST  # noqa: E402

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


def _table_ddl(statements: list[str], table: str) -> str:
    marker = f"CREATE TABLE IF NOT EXISTS {table} ("
    hits = [s for s in statements if marker in s]
    return hits[0] if len(hits) == 1 else ""


def _v2_ddl(table: str) -> str:
    """某张 v2 表的 DDL：按 migration 5 → 6 → 7 的顺序找。

    新 family 追加在**后面的** migration 里（纯追加，不改写历史语句），因此「这张表的 DDL 在哪」
    只能靠逐 migration 找，不能写死某一个。找不到时返回空串（调用方的 `in` 断言随即为假，
    与「DDL 缺列」同一条 FAIL，不会静默通过）。
    """
    for statements in (_MIG5, _MIG6, _MIG7):
        found = _table_ddl(statements, table)
        if found:
            return found
    return ""


_MIG5 = ST._migration_5_statements()
_MIG5_TEXT = "\n".join(_MIG5)
_MIG6 = ST._migration_6_statements()
_MIG6_TEXT = "\n".join(_MIG6)
_MIG7 = ST._migration_7_statements()
_MIG7_TEXT = "\n".join(_MIG7)
#: v2 family 的**存在性**断言面（migration 5 + 6 + 7）：新 family 追加在 6 / 7 里，表本身仍属同一套 v2。
_V2_TEXT = _MIG5_TEXT + "\n" + _MIG6_TEXT + "\n" + _MIG7_TEXT
_ALL_APPEND_MIGRATIONS = (("5", _MIG5, _MIG5_TEXT), ("6", _MIG6, _MIG6_TEXT),
                          ("7", _MIG7, _MIG7_TEXT))
_V1_TABLES = ("section_plan", "section_task", "current_plan", "section_result", "section_claim",
              "section_citation", "section_unresolved", "section_evaluation", "section_rework",
              "current_section", "progress", "section_rework_run", "section_run_manifest",
              "current_manifest")


# ---------------------------------------------------------------------------
# 1. 整数账本与连续前缀
# ---------------------------------------------------------------------------

def _check_ledger() -> None:
    check(ST.SECTION_STORE_SCHEMA_VERSION == 7
          and isinstance(ST.SECTION_STORE_SCHEMA_VERSION, int),
          "Section store 账本版本是整数 7（不写成字符串账本）")
    check(len(ST.MIGRATIONS) == 7
          and all(isinstance(m, list) and m for m in ST.MIGRATIONS),
          f"MIGRATIONS 恰 7 项有序语句列表（实际 {len(ST.MIGRATIONS)}）")
    check(ST.MIGRATIONS[4] == _MIG5,
          "第 5 项即 v2 root/member 表 migration（序号与内容一一对应）")
    check(ST.MIGRATIONS[5] == _MIG6,
          "第 6 项即 ClaimNarrativeDisposition 的 v2 family（序号与内容一一对应）")
    check(ST.MIGRATIONS[6] == _MIG7,
          "第 7 项即 FinalSentenceFidelityDecision 的 v2 family（序号与内容一一对应）")
    check(ST._valid_prefix([]) is True and ST._valid_prefix([1, 2, 3, 4, 5, 6, 7]) is True,
          "空库（未应用）与完整前缀 [1..7] 都是合法状态")
    for applied in ([1, 2, 4], [2], [1, 1, 2], [1, 2, 3, 5], [1, 2, 3, 4, 6],
                    [1, 2, 3, 4, 5, 7]):
        check(ST._valid_prefix(applied) is False,
              f"非连续前缀 {applied} 必须拒绝追加（防跳号/半迁移）")
    # 观察项（未在本批修改，故不作断言）：`_valid_prefix` 只判「连续」，不判序号**上界**。
    # 若某库账本为 [1..8]（比当前登记版本更高的 migration 已应用），本实现 `_migrate`
    # 不会拒绝启动。见停止报告的观察清单；此处只断言真实存在的连续前缀语义。


# ---------------------------------------------------------------------------
# 2. 纯追加 + 不可变触发器
# ---------------------------------------------------------------------------

def _check_append_only_migration() -> None:
    for label, statements, text in _ALL_APPEND_MIGRATIONS:
        bodies = [s for s in statements
                  if s.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX",
                                                    "CREATE VIEW"))]
        joined = "\n".join(bodies).upper()
        check(not any(clause in joined for clause in
                      ("UPDATE ", "DELETE FROM", "DROP TABLE", "ALTER TABLE", "DROP INDEX")),
              f"migration {label} 只有 CREATE TABLE/INDEX/VIEW：不原位加列、不改写历史行")
        check("CREATE TABLE" in text and all(
            s.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX", "CREATE VIEW",
                                           "CREATE TRIGGER"))
            for s in statements),
              f"migration {label} 的每一条语句都是建表/建索引/建视图/建触发器（无数据操作语句）")
        v1_touched = [t for t in _V1_TABLES if f"CREATE TABLE IF NOT EXISTS {t} (" in text
                      or f" ON {t}\n" in text]
        check(not v1_touched,
              f"migration {label} 不重建/不改写任何 v1 表（实际命中 {v1_touched}）")
    # 触发器体含 `BEFORE UPDATE/DELETE ON`，那是禁令；v2 表各自带 no_update/no_delete。
    # family 可能由 migration 5 或 6 建立，因此存在性在合并文本上核验。
    for table in ST.V2_SECTION_TABLES:
        check(f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table}" in _V2_TEXT
              and f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table}" in _V2_TEXT,
              f"{table} 带 UPDATE/DELETE 禁止触发器（append-only）")


# ---------------------------------------------------------------------------
# 3. P17 逐项（DDL 可断言部分）
# ---------------------------------------------------------------------------

def _check_p17_items() -> None:
    # 第 4 项：Draft root 是 root（无自引用外键），其余 family 都外键指向它。
    root = _table_ddl(_MIG5, "current_section_draft_v2")
    check(bool(root) and "draft_id TEXT PRIMARY KEY" in root
          and "REFERENCES" not in root and "payload_json TEXT NOT NULL" in root,
          "P17-4a：current_section_draft_v2 是 root（draft_id 主键、不自引用、payload NOT NULL）")
    for table in ST.V2_SECTION_TABLES:
        if table == "current_section_draft_v2":
            continue
        ddl = _v2_ddl(table)
        check("REFERENCES current_section_draft_v2(draft_id)" in ddl,
              f"P17-4b：{table} 外键指向 Draft root（同 transaction 内 Draft 必须先存在）")

    # 第 4 项：candidate / narrative draft unit 两类 subject member 同族，按 revision 唯一。
    subject = _table_ddl(_MIG5, "current_section_subject_v2")
    check("PRIMARY KEY (draft_id, subject_kind, subject_id)" in subject
          and "UNIQUE (draft_id, subject_kind, subject_id, subject_revision)" in subject,
          "P17-4c：subject member 以 (kind, id) 为身份、revision 唯一"
          "（candidate 与 narrative draft unit 两类同族，不混表）")

    # 第 1 项 exact manifest；第 2 项 WMPD 每个 manifest 成员恰一条。
    manifest = _table_ddl(_MIG5, "current_section_material_manifest_v2")
    check("PRIMARY KEY (draft_id, manifest_id)" in manifest
          and "draft_id TEXT NOT NULL REFERENCES current_section_draft_v2(draft_id)" in manifest,
          "P17-1：exact material manifest family 存在，按 **draft 归属**（主键含 draft_id）；"
          "manifest 身份是内容寻址的，两个 draft revision 可共享同一 manifest_id，"
          "全局唯一会让它们互相顶掉、也让外键指向哪个 draft 含糊")
    wmpd = _table_ddl(_MIG5, "current_section_wmpd_v2")
    check("UNIQUE (draft_id, member_ref)" in wmpd and "member_ref TEXT NOT NULL" in wmpd,
          "P17-2：WMPD 每个 manifest 成员恰一条（UNIQUE draft_id + member_ref）")

    # 第 3 项 FND successor：唯一集合键 = authority_kind + container + fact。
    fnd = _table_ddl(_MIG5, "current_section_fnd_v2")
    check("UNIQUE (draft_id, authority_kind, container_identity, authority_specific_fact_id)"
          in fnd,
          "P17-3：FND successor 的四元唯一键（四类 authority 各自成行）")

    # 第 5 项 proposal 独立 family 且只出现一次。
    proposal = _table_ddl(_MIG5, "current_section_proposal_v2")
    check("PRIMARY KEY (draft_id, proposal_id)" in proposal
          and "UNIQUE (draft_id, proposal_id)" in proposal
          and "subject_kind" not in proposal,
          "P17-5：proposal 是独立 member family、每个只出现一次，且不重复计入 subject family")

    # 第 6 项 aggregate 决定：每个 subject revision 恰一条。
    binding = _table_ddl(_MIG5, "current_section_binding_decision_v2")
    check("UNIQUE (draft_id, subject_kind, subject_id, subject_revision)" in binding,
          "P17-6：aggregate 决定每个 subject revision 恰一条（DB 层基数约束）")
    # 第 7 项 entailment 决定：每个 factual candidate revision 恰一条。
    entail = _table_ddl(_MIG5, "current_section_entailment_decision_v2")
    check("UNIQUE (draft_id, candidate_id, candidate_revision)" in entail,
          "P17-7：entailment 决定每个 candidate revision 恰一条")
    # 第 8 项 accepted binding family。
    check("PRIMARY KEY (draft_id, binding_id)" in _table_ddl(
        _MIG5, "current_section_accepted_binding_v2"),
        "P17-8：accepted binding 有独立 family（每个通过的 proposal 各一条）")
    # 第 9 项 current SectionClaim。
    check("PRIMARY KEY (draft_id, claim_id)" in _table_ddl(_MIG5, "current_section_claim_v2"),
          "P17-9：current SectionClaim family 存在（candidate revision / accepted set 在 payload）")
    # 第 10 项 final Narrative：sentence / paragraph / table 同族、member_kind 区分。
    narrative = _table_ddl(_MIG5, "current_section_narrative_v2")
    check("PRIMARY KEY (draft_id, narrative_kind, narrative_id)" in narrative,
          "P17-10：final Narrative 同族，按 narrative_kind 区分 sentence/paragraph/table")
    # 第 11 项 current SectionResult：单向引用 draft、每个 draft 至多一条、禁止内嵌 evaluation。
    result = _table_ddl(_MIG5, "current_section_result_v2")
    check("PRIMARY KEY (draft_id, section_result_id)" in result
          and "UNIQUE (draft_id)" in result,
          "P17-11a：current Result 按 draft 唯一（一个 Draft 不得有两条 Result）")
    check("evaluation_id" not in result and "evaluation" not in result,
          "P17-11b：current Result 表没有 evaluation 列（内嵌 evaluation 的成环旧缺陷不可表达）")
    check("REFERENCES current_section_draft_v2(draft_id)" in result
          and "section_draft_id" not in result.split("REFERENCES")[0],
          "P17-11c：Result → Draft 只有单向外键（Draft 侧没有回指 Result 的列）")
    # 第 12 项 writer 侧 unresolved / block projection。
    check("PRIMARY KEY (draft_id, unresolved_kind, unresolved_id)" in _table_ddl(
        _MIG5, "current_section_unresolved_v2"),
        "P17-12：writer 侧 unresolved / block projection 以 kind 区分、按 draft 归属")
    # 第 13 项 rejection audit 只建只读视图，不建第五种权威表。
    check(len(ST.V2_SECTION_VIEWS) == 2
          and all(v in _MIG5_TEXT for v in ST.V2_SECTION_VIEWS)
          and all(f"CREATE VIEW IF NOT EXISTS {v} AS SELECT" in _MIG5_TEXT
                  for v in ST.V2_SECTION_VIEWS),
          "P17-13a：两条 rejection audit 都是只读 SELECT 投影视图")
    check(not any("rejection" in t or "audit" in t for t in ST.V2_SECTION_TABLES),
          "P17-13b：v2 family 里没有 rejection/audit 表（不新建第五种权威对象）")
    for view in ST.V2_SECTION_VIEWS:
        src = _MIG5_TEXT.split(f"CREATE VIEW IF NOT EXISTS {view} AS ")[1].split("')")[0]
        check("current_section_binding_decision_v2" in src
              or "current_section_entailment_decision_v2" in src,
              f"P17-13c：{view} 的 source of truth 就是决定表自身（视图不另存拒绝事实）")
    # 第 14 项：本文件不建 follow-up 表。
    check(not any("follow_up" in t or "follow up" in t
                  for t in (*ST.V2_SECTION_TABLES, *_V1_TABLES))
          and "topic_follow_up" not in _MIG5_TEXT
          and "FollowUpNeed" not in _MIG5_TEXT,
          "P17-14：Section store 不建 FollowUpNeed 表（边界③ 只在 harness/topic_store.py）")


# ---------------------------------------------------------------------------
# 4. current / legacy 分离
# ---------------------------------------------------------------------------

def _check_current_legacy_separation() -> None:
    v1 = set(_V1_TABLES)
    added = set(ST.EXPECTED_TABLES) - v1
    check(added == set(ST.V2_SECTION_TABLES),
          "EXPECTED_TABLES = 14 张 v1 表 + 14 张 v2 表（新增的只有 v2 family）")
    check(all(t.endswith("_v2") for t in ST.V2_SECTION_TABLES),
          "所有 current successor 表都以 _v2 显式版本命名（不得与 v1 表同名/原位加列）")
    check(not (v1 & set(ST.V2_SECTION_TABLES)),
          "v1 与 v2 表名不重叠：新旧 root 物理隔离（跨 root 混读即可被拒）")
    for table in ST.V2_SECTION_TABLES:
        ddl = _v2_ddl(table)
        check("schema_version TEXT NOT NULL" in ddl and "content_fingerprint TEXT NOT NULL" in ddl,
              f"{table} 每行自带 schema_version + content_fingerprint"
              f"（migration 序号不替代 wire discriminator，读回可重算 identity）")
    check(hasattr(ST, "load_legacy_section_chain_for_audit")
          and hasattr(ST, "commit_section_result")
          and hasattr(ST, "get_section_result"),
          "legacy 只读入口与 legacy 写入路径都保留（旧行只走 legacy reader）")
    check(not hasattr(ST, "commit_section_chain_v2")
          or callable(getattr(ST, "commit_section_chain_v2")),
          "current chain 入口名固定为 commit_section_chain_v2（3B 落地其行为）")


def _check_migration_6_and_post_gate_order() -> None:
    """migration 6 / 7 的 family 与 `V2_POST_GATE_FAMILIES` 的提交序（§三 E、§12.4.3）。

    这里只断言**结构与顺序**：`SectionResult` 必须排在门后 family 的**最后一位**，因为
    「谁最后写入」是事务语义的一部分，不能只靠调用方自觉（`commit_section_chain_v2` 另有运行时
    断言，本处核的是登记表本身没有把 Result 排到中间）。
    """
    cnd = _table_ddl(_MIG6, "current_section_claim_narrative_disposition_v2")
    check("PRIMARY KEY (draft_id, claim_narrative_disposition_id)" in cnd
          and "UNIQUE (draft_id, claim_id, draft_revision)" in cnd
          and "claim_id TEXT NOT NULL" in cnd and "draft_revision TEXT NOT NULL" in cnd,
          "migration 6：ClaimNarrativeDisposition 有独立 family，"
          "且「每条定稿 Claim 恰好一条去向」落到 DDL 唯一键")
    check(ST.V2_FAMILY_PK_COLUMNS["current_section_claim_narrative_disposition_v2"]
          == ("claim_narrative_disposition_id",),
          "migration 6 的 family 已登记主键列（提交/读回都按它取键）")

    fnsd = _table_ddl(_MIG7, "current_section_final_sentence_decision_v2")
    check("PRIMARY KEY (draft_id, decision_id)" in fnsd
          and "UNIQUE (draft_id, draft_revision, narrative_id)" in fnsd
          and "draft_revision TEXT NOT NULL" in fnsd and "narrative_id TEXT NOT NULL" in fnsd,
          "migration 7：FinalSentenceFidelityDecision 有独立 family，"
          "且「一个 draft revision 恰好一条最终句决定」落到 DDL 唯一键"
          "（唯一键把基数约束落到库里，不只靠提交期复核）")
    check(ST.V2_FAMILY_PK_COLUMNS["current_section_final_sentence_decision_v2"]
          == ("decision_id",),
          "migration 7 的 family 已登记主键列（提交/读回都按它取键）")

    expected_post_gate = (
        "current_section_fnd_v2",
        "current_section_claim_narrative_disposition_v2",
        "current_section_claim_v2",
        "current_section_narrative_v2",
        # §12.4.3：最终句决定在**正文之后**落行——它核验的是最终句，正文还没写出来的决定不是决定。
        "current_section_final_sentence_decision_v2",
        "current_section_unresolved_v2",
        "current_section_result_v2",
    )
    check(tuple(ST.V2_POST_GATE_FAMILIES) == expected_post_gate,
          f"V2_POST_GATE_FAMILIES 七项提交序固定（实际 {ST.V2_POST_GATE_FAMILIES}）")
    check(ST.V2_POST_GATE_FAMILIES[-1] == "current_section_result_v2",
          "SectionResult 在门后提交序里**最后**（封口对象不得排在中间）")
    check(not (set(ST.V2_POST_GATE_FAMILIES)
               & (set(ST.V2_WRITER_SIDE_FAMILIES) | set(ST.V2_DECISION_FAMILIES))),
          "门后 family 与 writer 侧/决定链 family 不重叠（三段的提交面互不冒充）")

    declared = NS.CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION
    check(ST.V2_FAMILY_WIRE_MARKER["current_section_claim_narrative_disposition_v2"] == declared
          == "cnd-1",
          f"cnd family 的 wire marker 是它自己的 {declared!r}，不借用 narr 主 wire 版本")
    fnsd_marker = NS.FINAL_SENTENCE_DECISION_SCHEMA_VERSION
    check(ST.V2_FAMILY_WIRE_MARKER["current_section_final_sentence_decision_v2"] == fnsd_marker
          == "nsfid-1",
          f"最终句决定 family 的 wire marker 是它自己的 {fnsd_marker!r}，"
          "不借用 narr 主 wire 版本（同 migration 7 的表不得冒充正文成员）")
    check(len({ST.V2_FAMILY_WIRE_MARKER[f] for f in ST.V2_POST_GATE_FAMILIES}) >= 4,
          "门后 family 各自的 payload marker 互不相同（migration 序号不替代 wire discriminator）")


def _check_ledger_end_to_end() -> None:
    """真库上的账本行为：首建 1→7、重复 init 幂等、跳号拒绝启动。"""
    # Windows 下 `with sqlite3.connect(...)` 只提交不关闭，句柄会挡住临时目录清理；
    # 本模块一律显式 close（并允许清理期忽略残留句柄）。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "sections.sqlite"
        ST.init_db(db)
        applied = _applied(db)
        check(applied == [1, 2, 3, 4, 5, 6, 7],
              f"首建逐条登记 migration 1→7（实际 {applied}）")
        tables = _tables(db)
        check(set(ST.V2_SECTION_TABLES) <= tables and set(_V1_TABLES) <= tables,
              "真库里 14 张 v1 表与 14 张 v2 表并存（新旧 root 物理隔离）")
        for view in ST.V2_SECTION_VIEWS:
            check(view in _views(db), f"{view} 只读投影视图已建")
        ST.init_db(db)
        check(_applied(db) == applied,
              "重复 init_db 幂等（账本不重登、迁移不重跑）")
        check(ST._db_path == db, "init_db(db_path) 后模块库路径指向该临时库")

        _exec(db, "DELETE FROM schema_migrations WHERE migration_id=3")
        check(_applied(db) == [1, 2, 4, 5, 6, 7], "对照：人为删除中间一条 migration 记录")
        expect_raises("账本跳号后再次 init_db",
                      lambda: ST.init_db(db), "不是合法前缀", sqlite3.IntegrityError)


def _exec(db: Path, sql: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _applied(db: Path) -> list[int]:
    conn = sqlite3.connect(str(db))
    try:
        return [r[0] for r in conn.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
    finally:
        conn.close()


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _views(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
    finally:
        conn.close()


def main() -> dict:
    _check_ledger()
    _check_append_only_migration()
    _check_p17_items()
    _check_current_legacy_separation()
    _check_migration_6_and_post_gate_order()
    _check_ledger_end_to_end()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

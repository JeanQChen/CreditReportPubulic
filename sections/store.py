"""Section Store：Phase 4 章节产物不可变存储 + current 指针 + 原子提交。

- 独立库 data/sections.db，与 credit.db / evidence.db / financial_v2.db 分离。
- 不可变历史：plan / task / section_result / claim / citation / unresolved / evaluation /
  rework 全部 append-only，不覆盖、不物理删除。
- current 指针（current_plan / current_section）只在完整验证并原子提交后切换。
- 原子提交：commit_plan 把 plan + task + current_plan 指针 + progress 放在同一事务，
  失败回滚，不留半个 current plan。
- SQLite migration 只追加（schema_migrations 记账），禁止重写历史 migration。
- 连接模式与 evidence/store.py 一致：per-call connect/close，PRAGMA foreign_keys=ON。

Batch A 只实现 plan / task / current_plan / progress 的写入与读取；section_result / claim /
citation / unresolved / evaluation / rework / current_section 的 DDL 一并建齐（任务书 §8.3
要求 Store 至少包含这些表），写入器在 P4-B/C/D 接入，后续只增不改。

CLI：python -m sections.store --db data/sections.db --init | --summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from planning import schema as PS
from sections import final_sentence_fidelity as FSF
from sections import narrative_schema as NS
from sections import schema as SS
from sections import validator as svalidator

DEFAULT_DB_PATH = Path("data/sections.db")

_db_path: Path = DEFAULT_DB_PATH


class SectionStorageConflictError(Exception):
    """章节产物参数冲突：同 section_result_id 但内容/任务/章节不一致，显式报错（不覆盖、不切指针）。"""


class SectionStorageCorruptionError(Exception):
    """章节产物存储损坏：复用前规范化 payload 与自身 section_version/result_id 不一致。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_id() -> str:
    return f"p4evt_{uuid.uuid4().hex[:12]}"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# DDL（migration 1 历史 DDL，内容与 v1 完全一致，仅按语句边界拆分，不改语义）
# ---------------------------------------------------------------------------

_MIGRATION_1_DDL = """
    CREATE TABLE IF NOT EXISTS section_plan (
        plan_id             TEXT PRIMARY KEY,
        job_id              TEXT NOT NULL,
        company_id          TEXT NOT NULL,
        company_name        TEXT NOT NULL,
        credit_type         TEXT NOT NULL,
        report_as_of        TEXT NOT NULL,
        template_id         TEXT NOT NULL,
        input_fingerprint   TEXT NOT NULL,
        contract_fingerprint TEXT NOT NULL,
        planner_version     TEXT NOT NULL,
        created_at          TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS section_task (
        task_id             TEXT PRIMARY KEY,
        plan_id             TEXT NOT NULL REFERENCES section_plan(plan_id),
        section_id          TEXT NOT NULL,
        ordinal             INTEGER NOT NULL,
        title               TEXT NOT NULL,
        purpose             TEXT NOT NULL,
        research_policy     TEXT NOT NULL,
        task_schema_version TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_task_plan ON section_task(plan_id, ordinal);

    CREATE TABLE IF NOT EXISTS current_plan (
        job_id              TEXT PRIMARY KEY,
        company_id          TEXT NOT NULL,
        plan_id             TEXT NOT NULL,
        switched_at         TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS section_result (
        section_result_id   TEXT PRIMARY KEY,
        section_version     TEXT NOT NULL,
        task_id             TEXT NOT NULL REFERENCES section_task(task_id),
        section_id          TEXT NOT NULL,
        status              TEXT NOT NULL,
        section_ordinal     INTEGER NOT NULL,
        payload_json        TEXT NOT NULL,
        created_at          TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_result_task ON section_result(task_id);

    CREATE TABLE IF NOT EXISTS section_claim (
        claim_id            TEXT PRIMARY KEY,
        section_result_id   TEXT NOT NULL REFERENCES section_result(section_result_id),
        section_id          TEXT NOT NULL,
        topic_id            TEXT NOT NULL,
        question_ids_json   TEXT NOT NULL,
        text                TEXT NOT NULL,
        claim_type          TEXT NOT NULL,
        citation_refs_json  TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_claim_result ON section_claim(section_result_id);

    CREATE TABLE IF NOT EXISTS section_citation (
        citation_id         TEXT PRIMARY KEY,
        claim_id            TEXT NOT NULL REFERENCES section_claim(claim_id),
        section_result_id   TEXT NOT NULL,
        ref_type            TEXT NOT NULL,
        evidence_id         TEXT,
        snapshot_id         TEXT,
        item_code           TEXT,
        formula_id          TEXT,
        formula_version     TEXT,
        period              TEXT,
        source_snapshot_id  TEXT,
        page_number         INTEGER,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_citation_claim ON section_citation(claim_id);

    CREATE TABLE IF NOT EXISTS section_unresolved (
        unresolved_id       TEXT PRIMARY KEY,
        section_result_id   TEXT NOT NULL REFERENCES section_result(section_result_id),
        section_id          TEXT NOT NULL,
        topic_id            TEXT NOT NULL,
        question_id         TEXT,
        state               TEXT NOT NULL,
        reason_code         TEXT NOT NULL,
        detail              TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_unresolved_result ON section_unresolved(section_result_id);

    CREATE TABLE IF NOT EXISTS section_evaluation (
        evaluation_id       TEXT PRIMARY KEY,
        section_result_id   TEXT NOT NULL REFERENCES section_result(section_result_id),
        rules_version       TEXT NOT NULL,
        evaluator_prompt_version TEXT NOT NULL,
        rules_passed        INTEGER NOT NULL,
        llm_passed          INTEGER,
        decision            TEXT NOT NULL,
        payload_json        TEXT NOT NULL,
        evaluated_at        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_eval_result ON section_evaluation(section_result_id);

    CREATE TABLE IF NOT EXISTS section_rework (
        rework_id           TEXT PRIMARY KEY,
        evaluation_id       TEXT NOT NULL REFERENCES section_evaluation(evaluation_id),
        target_kind         TEXT NOT NULL,
        target_ref          TEXT NOT NULL,
        reason              TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_rework_eval ON section_rework(evaluation_id);

    CREATE TABLE IF NOT EXISTS current_section (
        task_id             TEXT PRIMARY KEY,
        section_id          TEXT NOT NULL,
        section_result_id   TEXT NOT NULL,
        switched_at         TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS progress (
        event_id            TEXT PRIMARY KEY,
        job_id              TEXT NOT NULL,
        run_id              TEXT NOT NULL,
        stage               TEXT NOT NULL,
        status              TEXT NOT NULL,
        detail              TEXT,
        created_at          TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_progress_job ON progress(job_id, created_at);
    """


def _split_statements(script: str) -> list[str]:
    """按 ';' 把 DDL 脚本拆为单条语句（migration 1 的 DDL 无字符串内分号，拆分安全）。"""
    return [s.strip() for s in script.split(";") if s.strip()]


# 历史事实表（append-only，禁止 UPDATE/DELETE）；current_plan/current_section 是可切换
# 指针，不加不可变触发器。
_IMMUTABLE_TABLES = (
    "section_plan", "section_task", "section_result", "section_claim",
    "section_citation", "section_unresolved", "section_evaluation",
    "section_rework", "progress",
)


def _no_update_trigger(table: str) -> str:
    return (f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} 是不可变历史表，禁止 UPDATE'); END")


def _no_delete_trigger(table: str) -> str:
    return (f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} 是不可变历史表，禁止 DELETE'); END")


def _migration_2_statements() -> list[str]:
    """migration 2：不可变触发器 + 缺失外键（真正的追加迁移，表交换保留行）。

    - 9 个历史事实表加 BEFORE UPDATE / BEFORE DELETE 触发器；
    - current_plan / current_section / section_citation 用「建新表 → 拷贝 → 删旧 →
      改名」补外键（SQLite 不支持 ALTER ADD CONSTRAINT，只能表交换）；
    - 迁移后 foreign_key_check 由 _apply_migration 统一校验。
    """
    stmts: list[str] = []
    # 1) 非重建历史表的不可变触发器（section_citation 在表交换后单独建）
    for t in _IMMUTABLE_TABLES:
        if t == "section_citation":
            continue
        stmts.append(_no_update_trigger(t))
        stmts.append(_no_delete_trigger(t))
    # 2) current_plan 补外键 plan_id → section_plan（可切换指针，不加不可变触发器）
    stmts.extend([
        "CREATE TABLE current_plan_new (job_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, "
        "plan_id TEXT NOT NULL REFERENCES section_plan(plan_id), switched_at TEXT NOT NULL)",
        "INSERT INTO current_plan_new (job_id, company_id, plan_id, switched_at) "
        "SELECT job_id, company_id, plan_id, switched_at FROM current_plan",
        "DROP TABLE current_plan",
        "ALTER TABLE current_plan_new RENAME TO current_plan",
    ])
    # 3) current_section 补外键 section_result_id → section_result
    stmts.extend([
        "CREATE TABLE current_section_new (task_id TEXT PRIMARY KEY, section_id TEXT NOT NULL, "
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "switched_at TEXT NOT NULL)",
        "INSERT INTO current_section_new (task_id, section_id, section_result_id, switched_at) "
        "SELECT task_id, section_id, section_result_id, switched_at FROM current_section",
        "DROP TABLE current_section",
        "ALTER TABLE current_section_new RENAME TO current_section",
    ])
    # 4) section_citation 补外键 section_result_id → section_result（表交换 + 索引 + 触发器）
    stmts.extend([
        "CREATE TABLE section_citation_new (citation_id TEXT PRIMARY KEY, "
        "claim_id TEXT NOT NULL REFERENCES section_claim(claim_id), "
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "ref_type TEXT NOT NULL, evidence_id TEXT, snapshot_id TEXT, item_code TEXT, "
        "formula_id TEXT, formula_version TEXT, period TEXT, source_snapshot_id TEXT, "
        "page_number INTEGER, payload_json TEXT NOT NULL)",
        "INSERT INTO section_citation_new (citation_id, claim_id, section_result_id, ref_type, "
        "evidence_id, snapshot_id, item_code, formula_id, formula_version, period, "
        "source_snapshot_id, page_number, payload_json) "
        "SELECT citation_id, claim_id, section_result_id, ref_type, evidence_id, snapshot_id, "
        "item_code, formula_id, formula_version, period, source_snapshot_id, page_number, "
        "payload_json FROM section_citation",
        "DROP TABLE section_citation",
        "ALTER TABLE section_citation_new RENAME TO section_citation",
        "CREATE INDEX idx_section_citation_claim ON section_citation(claim_id)",
        _no_update_trigger("section_citation"),
        _no_delete_trigger("section_citation"),
    ])
    return stmts


def _migration_3_statements() -> list[str]:
    """migration 3：返工批次 + 运行清单 + current_manifest 指针（真正追加迁移）。

    - section_rework_run / section_run_manifest 是 append-only 历史事实表（加不可变触发器）；
    - current_manifest 是可切换指针（不加不可变触发器，引用 section_run_manifest 外键）。
    """
    stmts: list[str] = [
        "CREATE TABLE section_rework_run ("
        "rework_run_id TEXT PRIMARY KEY, "
        "job_id TEXT NOT NULL, "
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "from_section_result_id TEXT NOT NULL, "
        "evaluation_id TEXT NOT NULL REFERENCES section_evaluation(evaluation_id), "
        "batch_no INTEGER NOT NULL, "
        "llm_evaluator_calls INTEGER NOT NULL, "
        "targets_json TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)",
        "CREATE INDEX idx_rework_run_from ON section_rework_run(from_section_result_id)",
        "CREATE TABLE section_run_manifest ("
        "manifest_id TEXT PRIMARY KEY, "
        "job_id TEXT NOT NULL, "
        "run_id TEXT NOT NULL, "
        "code_fingerprint TEXT NOT NULL, "
        "phase3_closure_fingerprint TEXT NOT NULL, "
        "batch_versions_json TEXT NOT NULL, "
        "frozen_json TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)",
        "CREATE INDEX idx_run_manifest_job ON section_run_manifest(job_id)",
        "CREATE TABLE current_manifest ("
        "job_id TEXT PRIMARY KEY, "
        "manifest_id TEXT NOT NULL REFERENCES section_run_manifest(manifest_id), "
        "switched_at TEXT NOT NULL)",
    ]
    stmts.append(_no_update_trigger("section_rework_run"))
    stmts.append(_no_delete_trigger("section_rework_run"))
    stmts.append(_no_update_trigger("section_run_manifest"))
    stmts.append(_no_delete_trigger("section_run_manifest"))
    return stmts


def _migration_4_statements() -> list[str]:
    """migration 4：子对象改复合归属键 (section_result_id, 内容 id)，支持跨版本内容复用。

    问题：section_claim.claim_id / section_citation.citation_id /
    section_unresolved.unresolved_id 曾是全局 PRIMARY KEY 且同时绑定 section_result_id。
    返工 merge() 复用父结果未触及的 claim（claim_id 不变）进新 section_result_id →
    全局主键冲突（IntegrityError: UNIQUE constraint failed: section_claim.claim_id）。

    修复（受控表交换）：
    - section_claim / section_unresolved → 复合主键 (section_result_id, 内容 id)；
    - section_citation → 复合主键 (section_result_id, citation_id) + 复合外键
      (section_result_id, claim_id) → section_claim(section_result_id, claim_id)。
    语义：同一 SectionResult 内重复 content_id → validator fail-closed（Store 不去重）；
    不同 SectionResult 相同内容 → 合法，各行均能完整查询。

    顺序（子表先删，父表后删，规避 SQLite DROP 父表时子表外键悬挂；不依赖 RENAME 外键
    自动改写）：先交换 section_citation（临时去掉 claim 外键）→ 交换 section_claim →
    交换 section_unresolved → 再交换 section_citation（加回复合外键）。任一步失败整体回滚。
    """
    stmts: list[str] = []

    # 1) section_citation 首轮交换：复合主键 (section_result_id, citation_id)，
    #    暂不含 claim 外键（先解除对 section_claim 的单列外键依赖）。
    stmts.extend([
        "CREATE TABLE section_citation_stage ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "citation_id TEXT NOT NULL, "
        "claim_id TEXT NOT NULL, "
        "ref_type TEXT NOT NULL, "
        "evidence_id TEXT, "
        "snapshot_id TEXT, "
        "item_code TEXT, "
        "formula_id TEXT, "
        "formula_version TEXT, "
        "period TEXT, "
        "source_snapshot_id TEXT, "
        "page_number INTEGER, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, citation_id))",
        "INSERT INTO section_citation_stage (section_result_id, citation_id, claim_id, "
        "ref_type, evidence_id, snapshot_id, item_code, formula_id, formula_version, period, "
        "source_snapshot_id, page_number, payload_json) "
        "SELECT section_result_id, citation_id, claim_id, ref_type, evidence_id, snapshot_id, "
        "item_code, formula_id, formula_version, period, source_snapshot_id, page_number, "
        "payload_json FROM section_citation",
        "DROP TABLE section_citation",
        "ALTER TABLE section_citation_stage RENAME TO section_citation",
    ])

    # 2) section_claim：复合主键 (section_result_id, claim_id)。
    stmts.extend([
        "CREATE TABLE section_claim_new ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "claim_id TEXT NOT NULL, "
        "section_id TEXT NOT NULL, "
        "topic_id TEXT NOT NULL, "
        "question_ids_json TEXT NOT NULL, "
        "text TEXT NOT NULL, "
        "claim_type TEXT NOT NULL, "
        "citation_refs_json TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, claim_id))",
        "INSERT INTO section_claim_new (section_result_id, claim_id, section_id, topic_id, "
        "question_ids_json, text, claim_type, citation_refs_json, payload_json) "
        "SELECT section_result_id, claim_id, section_id, topic_id, question_ids_json, text, "
        "claim_type, citation_refs_json, payload_json FROM section_claim",
        "DROP TABLE section_claim",
        "ALTER TABLE section_claim_new RENAME TO section_claim",
        "CREATE INDEX idx_section_claim_result ON section_claim(section_result_id)",
        _no_update_trigger("section_claim"),
        _no_delete_trigger("section_claim"),
    ])

    # 3) section_unresolved：复合主键 (section_result_id, unresolved_id)。
    stmts.extend([
        "CREATE TABLE section_unresolved_new ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "unresolved_id TEXT NOT NULL, "
        "section_id TEXT NOT NULL, "
        "topic_id TEXT NOT NULL, "
        "question_id TEXT, "
        "state TEXT NOT NULL, "
        "reason_code TEXT NOT NULL, "
        "detail TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, unresolved_id))",
        "INSERT INTO section_unresolved_new (section_result_id, unresolved_id, section_id, "
        "topic_id, question_id, state, reason_code, detail, payload_json) "
        "SELECT section_result_id, unresolved_id, section_id, topic_id, question_id, state, "
        "reason_code, detail, payload_json FROM section_unresolved",
        "DROP TABLE section_unresolved",
        "ALTER TABLE section_unresolved_new RENAME TO section_unresolved",
        "CREATE INDEX idx_section_unresolved_result ON section_unresolved(section_result_id)",
        _no_update_trigger("section_unresolved"),
        _no_delete_trigger("section_unresolved"),
    ])

    # 4) section_citation 二轮交换：加回复合外键
    #    (section_result_id, claim_id) → section_claim(section_result_id, claim_id)。
    stmts.extend([
        "CREATE TABLE section_citation_new ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "citation_id TEXT NOT NULL, "
        "claim_id TEXT NOT NULL, "
        "ref_type TEXT NOT NULL, "
        "evidence_id TEXT, "
        "snapshot_id TEXT, "
        "item_code TEXT, "
        "formula_id TEXT, "
        "formula_version TEXT, "
        "period TEXT, "
        "source_snapshot_id TEXT, "
        "page_number INTEGER, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, citation_id), "
        "FOREIGN KEY (section_result_id, claim_id) "
        "REFERENCES section_claim(section_result_id, claim_id))",
        "INSERT INTO section_citation_new (section_result_id, citation_id, claim_id, ref_type, "
        "evidence_id, snapshot_id, item_code, formula_id, formula_version, period, "
        "source_snapshot_id, page_number, payload_json) "
        "SELECT section_result_id, citation_id, claim_id, ref_type, evidence_id, snapshot_id, "
        "item_code, formula_id, formula_version, period, source_snapshot_id, page_number, "
        "payload_json FROM section_citation",
        "DROP TABLE section_citation",
        "ALTER TABLE section_citation_new RENAME TO section_citation",
        "CREATE INDEX idx_section_citation_claim ON section_citation(claim_id)",
        "CREATE INDEX idx_section_citation_result ON section_citation(section_result_id)",
        _no_update_trigger("section_citation"),
        _no_delete_trigger("section_citation"),
    ])

    return stmts


# 每个 migration 是「有序 SQL 语句列表」，在单事务内逐条执行；失败整体回滚（不留半迁移）。
MIGRATIONS: list[list[str]] = [
    _split_statements(_MIGRATION_1_DDL),
    _migration_2_statements(),
    _migration_3_statements(),
    _migration_4_statements(),
]

#: Section 侧账本迁移序号（**整数**；不写成字符串账本）。
#: M930-3（P17）：migration 1–4 落 v1 表，migration 5 落 versioned `*_v2` 表，
#: migration 6 补 `ClaimNarrativeDisposition` 的 family（§三 E：每个 current wire 对象都要有
#: 自己的 family / 主键 / 内容身份，去向记录不得寄居在 narrative family 里冒充正文成员）。
#: migration 7 补 `FinalSentenceFidelityDecision` 的 family（§12.4.3 的落库点之一：定稿路径上的
#: 最终句决定是一个有自己身份与过期锚点的 current 对象，同样不得寄居在 narrative family 里）。
#: 数据库 migration 序号**不**替代单行 wire discriminator——每个 v2 payload 自带
#: ``schema_version``，current reader 逐 payload 校验。
SECTION_STORE_SCHEMA_VERSION = 7


def _v2_family_ddl(table: str, pk_cols: tuple[str, ...], *,
                   unique: tuple[str, ...] = (),
                   extra_cols: tuple[str, ...] = ()) -> list[str]:
    """一个 versioned v2 member family 的标准 DDL（结构一致，便于逐表审计）。

    - ``draft_id`` 恒为该 root 的外键（root 表自身除外，见 ``_migration_5_statements``）；
    - ``schema_version`` 是**逐 payload** 的 wire discriminator，不是账本版本；
    - ``content_fingerprint`` 供 current reader 读回时重算 identity；
    - ``extra_cols`` 只为承载 identity/基数键的**提升列**（真值仍在 payload 内，列只用于
      约束与外键，避免依赖 JSON 表达式建唯一索引）；
    - ``unique`` 把 P17 的基数要求落到 DDL 层（如"每个 subject revision 恰一条 aggregate
      决定"、"每个 manifest 成员恰一条 WMPD"）；
    - 每个 family 都有 UPDATE/DELETE 禁止触发器（append-only）。
    """
    cols = [
        "draft_id TEXT NOT NULL REFERENCES current_section_draft_v2(draft_id)",
        *[f"{c} TEXT NOT NULL" for c in pk_cols if c != "draft_id"],
        *[f"{c} TEXT NOT NULL" for c in extra_cols],
        "ordinal INTEGER NOT NULL",
        "schema_version TEXT NOT NULL",
        "content_fingerprint TEXT NOT NULL",
        "payload_json TEXT NOT NULL",
        "created_at TEXT NOT NULL",
    ]
    if pk_cols == ("draft_id",):
        # 单行 family（如 current SectionResult）：root 即主键，禁止一个 draft 两条 Result。
        cols.append("PRIMARY KEY (draft_id)")
    else:
        cols.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
    if unique:
        cols.append(f"UNIQUE ({', '.join(unique)})")
    return [
        f"CREATE TABLE IF NOT EXISTS {table} (\n        "
        + ",\n        ".join(cols)
        + "\n    )",
        f"CREATE INDEX IF NOT EXISTS idx_{table}_draft ON {table}(draft_id, ordinal)",
        _no_update_trigger(table),
        _no_delete_trigger(table),
    ]


#: v2 root/member family 表名（封闭；供 current reader 与测试逐项断言）。
V2_SECTION_TABLES = (
    "current_section_material_manifest_v2",
    "current_section_wmpd_v2",
    "current_section_fnd_v2",
    "current_section_draft_v2",
    "current_section_subject_v2",
    "current_section_proposal_v2",
    "current_section_binding_decision_v2",
    "current_section_entailment_decision_v2",
    "current_section_accepted_binding_v2",
    "current_section_claim_v2",
    "current_section_narrative_v2",
    "current_section_result_v2",
    "current_section_unresolved_v2",
    "current_section_claim_narrative_disposition_v2",
    "current_section_final_sentence_decision_v2",
)

#: v2 只读投影视图（**不是** source of truth；P17 第 13 项：不新建第五种权威对象/表）。
V2_SECTION_VIEWS = (
    "v2_claim_binding_rejection_audit",
    "v2_claim_entailment_rejection_audit",
)


def _migration_5_statements() -> list[str]:
    """migration 5：Section 侧 current successor 链的 versioned v2 root/member 表。

    纯追加——不原位给 v1 表加列、不改不删历史行。落地 P17 的逐项要求：

    1. exact material manifest（本次 writer 消费的精确材料清单）；
    2. `WriterMaterialProcessingDisposition`（含 used / not_used 与理由码）；
    3. `FactNarrativeDisposition` successor（四类 authority tagged union）；
    4. current `SectionDraft` root + candidate / narrative-draft-unit 两类 subject member；
    5. proposals（`ProposedSupportRef`）作为**独立且只出现一次**的 member family；
    6. aggregate `ClaimBindingDecision`（每个 subject revision 恰好一条）；
    7. `ClaimEntailmentDecision`（每个 aggregate=pass 的 factual candidate revision 恰好一条）；
    8. `AcceptedSupportBinding`；
    9. current `SectionClaim`（含 candidate revision + 完整 accepted_binding_ids）；
    10. final Narrative（sentence / paragraph / table 同族，member_kind 区分）；
    11. current `SectionResult`（单向引用 `section_draft_id`，禁止内嵌 evaluation）；
    12. writer-side `SectionUnresolved` / `SectionBlockProjection`；
    13. rejection audit 不新建第五种权威表——只建只读投影视图；
    14. `FollowUpNeed` 属边界③（`harness/topic_store.py`），本文件**不建其表**；
    15. append-only：每个新表都带 UPDATE/DELETE 禁止触发器；
    16. current / legacy 读回分离由 `commit_section_chain_v2` / `load_current_section_chain_v2`
        与 `load_legacy_section_chain_for_audit` 在读取侧强制。
    """
    stmts = [
        # 4a) Draft root（current SectionDraft 本身；其余 family 均外键指向它）
        "CREATE TABLE IF NOT EXISTS current_section_draft_v2 ("
        "draft_id TEXT PRIMARY KEY, "
        "task_id TEXT NOT NULL, "
        "section_id TEXT NOT NULL, "
        "draft_revision TEXT NOT NULL, "
        "ordinal INTEGER NOT NULL, "
        "schema_version TEXT NOT NULL, "
        "content_fingerprint TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_current_section_draft_v2_task "
        "ON current_section_draft_v2(task_id, section_id)",
        _no_update_trigger("current_section_draft_v2"),
        _no_delete_trigger("current_section_draft_v2"),
    ]
    # 1) exact material manifest（**按 draft 归属**：manifest 身份是内容寻址的，同一份精确材料集
    #    的两个 draft revision 会得到同一个 manifest_id，因此主键必须含 draft_id，否则第二个
    #    revision 提交被误判为唯一约束冲突；manifest_id 全局唯一也会让外键指向哪个 draft 变得含糊）。
    stmts += _v2_family_ddl("current_section_material_manifest_v2", ("draft_id", "manifest_id"))
    # 2) WriterMaterialProcessingDisposition（每个 manifest 成员恰一条 → member 唯一）
    stmts += _v2_family_ddl("current_section_wmpd_v2", ("draft_id", "disposition_id"),
                            unique=("draft_id", "member_ref"),
                            extra_cols=("member_ref",))
    # 3) FactNarrativeDisposition successor（唯一集合键 = authority_kind + container + fact）
    stmts += _v2_family_ddl("current_section_fnd_v2", ("draft_id", "disposition_id"),
                            unique=("draft_id", "authority_kind", "container_identity",
                                    "authority_specific_fact_id"),
                            extra_cols=("authority_kind", "container_identity",
                                        "authority_specific_fact_id"))
    # 4b) subject members：ClaimCandidate / NarrativeDraftUnit
    stmts += _v2_family_ddl("current_section_subject_v2",
                            ("draft_id", "subject_kind", "subject_id"),
                            unique=("draft_id", "subject_kind", "subject_id", "subject_revision"),
                            extra_cols=("subject_revision",))
    # 5) proposals：独立 family，且每个 proposal 只出现一次
    stmts += _v2_family_ddl("current_section_proposal_v2",
                            ("draft_id", "proposal_id"),
                            unique=("draft_id", "proposal_id"))
    # 6) aggregate 决定：每个 (subject_kind, subject_id, revision) **恰好一条**
    stmts += _v2_family_ddl("current_section_binding_decision_v2",
                            ("draft_id", "decision_id"),
                            unique=("draft_id", "subject_kind", "subject_id", "subject_revision"),
                            extra_cols=("subject_kind", "subject_id", "subject_revision"))
    # 7) entailment 决定：每个 factual candidate revision **恰好一条**
    stmts += _v2_family_ddl("current_section_entailment_decision_v2",
                            ("draft_id", "decision_id"),
                            unique=("draft_id", "candidate_id", "candidate_revision"),
                            extra_cols=("candidate_id", "candidate_revision"))
    # 8) accepted bindings：每个通过的 proposal 各一条
    stmts += _v2_family_ddl("current_section_accepted_binding_v2",
                            ("draft_id", "binding_id"),
                            unique=("draft_id", "binding_id"))
    # 9) current SectionClaim
    stmts += _v2_family_ddl("current_section_claim_v2", ("draft_id", "claim_id"),
                            unique=("draft_id", "claim_id"))
    # 10) final Narrative（sentence / paragraph / table 同族）
    stmts += _v2_family_ddl("current_section_narrative_v2",
                            ("draft_id", "narrative_kind", "narrative_id"))
    # 11) current SectionResult：单向引用 draft，且必须是本 root 最后提交的对象
    stmts += _v2_family_ddl("current_section_result_v2", ("draft_id", "section_result_id"),
                            unique=("draft_id",))
    # 12) writer-side unresolved / block projection
    stmts += _v2_family_ddl("current_section_unresolved_v2",
                            ("draft_id", "unresolved_kind", "unresolved_id"))
    # 13) rejection audit：只读投影，不新建第五种权威对象/表。
    #     拒绝留痕的 source of truth 是**决定自身**（failed aggregate / rejected entailment），
    #     视图只是审计读面。
    stmts.append(
        "CREATE VIEW IF NOT EXISTS v2_claim_binding_rejection_audit AS "
        "SELECT draft_id, decision_id, subject_kind, subject_id, subject_revision, "
        "schema_version, content_fingerprint, payload_json "
        "FROM current_section_binding_decision_v2 "
        "WHERE json_extract(payload_json, '$.result') = 'fail'")
    stmts.append(
        "CREATE VIEW IF NOT EXISTS v2_claim_entailment_rejection_audit AS "
        "SELECT draft_id, decision_id, candidate_id, candidate_revision, schema_version, "
        "content_fingerprint, payload_json FROM current_section_entailment_decision_v2 "
        "WHERE json_extract(payload_json, '$.verdict') = 'rejected'")
    return stmts


def _migration_6_statements() -> list[str]:
    """migration 6：`ClaimNarrativeDisposition` 的 v2 family（§三 E 的补全项）。

    纯追加。**为什么必须单独建表**：narr-5 只把去向留在了组织器 trace 里，而
    `current_section_narrative_v2` 的载荷必须带 `NARRATIVE_SCHEMA_VERSION` marker——把去向
    记录塞进那个 family 就等于让它冒充「正文成员（句/段/表）」，读回时无法区分「这一段正文」
    与「这条去向」。因此按同一套 `_v2_family_ddl` 建独立 family：主键
    `claim_narrative_disposition_id`，唯一键 `(draft_id, claim_id, draft_revision)` 把
    「每条定稿 Claim 恰好一条去向」落到 DDL 层，载荷 marker 用它自己的 `cnd-1`。
    """
    return _v2_family_ddl(
        "current_section_claim_narrative_disposition_v2",
        ("draft_id", "claim_narrative_disposition_id"),
        unique=("draft_id", "claim_id", "draft_revision"),
        extra_cols=("claim_id", "draft_revision"))


def _migration_7_statements() -> list[str]:
    """migration 7：`FinalSentenceFidelityDecision` 的 v2 family（§12.4.3 的落库点之一）。

    纯追加。**为什么必须单独建表**：本门的决定有自己的 `schema_version`（`nsfid-1`）、自己的
    过期锚点（`draft_id` / `section_id` / `draft_revision` / `narrative_id`）与自己的覆盖面
    （`sentence_ids`）。把它塞进 `current_section_narrative_v2` 就等于让一条**判断**冒充「正文
    成员（句/段/表）」——那个 family 的 marker 纪律要求「列 marker == 载荷 marker」，读回时也
    无法区分「这一段正文」与「这条核验」。故按同一套 `_v2_family_ddl` 建独立 family：主键
    `final_sentence_fidelity_decision_id`，载荷 marker 用它自己的 `nsfid-1`。

    **唯一键 `(draft_id, draft_revision, narrative_id)` 不是装饰**：本门的基数是「一个 draft
    revision 恰好一条决定」，那条约束必须落到 DDL 层（`_assert_post_gate_cardinality` 只是提交
    前的同口径复核，改不了「库里能同时存在两条」这件事）。历史行向后兼容：本 family 在
    migration 7 之前不存在，旧链读回时该 family 缺席 ⇒ 决定集为空元组（那是「没有决定」，
    不是「无需核验」——`_assert_post_gate_cardinality` 会照旧按重算结果要求那条 typed block）。
    """
    return _v2_family_ddl(
        "current_section_final_sentence_decision_v2",
        ("draft_id", "decision_id"),
        unique=("draft_id", "draft_revision", "narrative_id"),
        extra_cols=("draft_revision", "narrative_id"))


# 每个 migration 是「有序 SQL 语句列表」，在单事务内逐条执行；失败整体回滚（不留半迁移）。
MIGRATIONS: list[list[str]] = [
    _split_statements(_MIGRATION_1_DDL),
    _migration_2_statements(),
    _migration_3_statements(),
    _migration_4_statements(),
    _migration_5_statements(),
    _migration_6_statements(),
    _migration_7_statements(),
]

assert len(MIGRATIONS) == SECTION_STORE_SCHEMA_VERSION, (
    f"Section store migration 序号必须为 {SECTION_STORE_SCHEMA_VERSION}，"
    f"实际 {len(MIGRATIONS)}")

# Store 应有表（§8.3），供 self-check / 测试断言
EXPECTED_TABLES = (
    "section_plan", "section_task", "current_plan",
    "section_result", "section_claim", "section_citation", "section_unresolved",
    "section_evaluation", "section_rework", "current_section", "progress",
    "section_rework_run", "section_run_manifest", "current_manifest",
) + V2_SECTION_TABLES


def _valid_prefix(applied: list[int]) -> bool:
    """已应用 migration 必须是 [1..N] 连续前缀，否则拒绝追加（防乱序/半迁移）。"""
    return applied == list(range(1, len(applied) + 1))


def _apply_migration(conn: sqlite3.Connection, migration_id: int,
                     statements: list[str]) -> None:
    """单事务执行一个 migration 的全部语句，foreign_key_check 通过才 COMMIT，失败整体回滚。"""
    try:
        conn.execute("BEGIN")
        for stmt in statements:
            conn.execute(stmt)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"migration {migration_id} 外键校验失败：{len(violations)} 处违例，已回滚")
        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (migration_id, _utcnow()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate(conn: sqlite3.Connection) -> None:
    """追加式 migration：先校验历史是合法前缀，再逐条执行缺失 migration。

    - 已应用历史不是 [1..N] 连续前缀 → 抛错拒绝（防半迁移/乱序）。
    - 每个 migration 单事务执行，失败回滚；重复调用幂等（已应用则跳过）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(migration_id INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = [row["migration_id"]
               for row in conn.execute(
                   "SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
    if not _valid_prefix(applied):
        raise sqlite3.IntegrityError(
            f"schema_migrations 历史不是合法前缀: {applied}（拒绝追加，防半迁移）")
    for i, statements in enumerate(MIGRATIONS, start=1):
        if i in applied:
            continue
        _apply_migration(conn, i, statements)


def init_db(db_path: str | Path | None = None) -> None:
    global _db_path
    if db_path is not None:
        _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    try:
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 写入：progress + 原子 commit_plan
# ---------------------------------------------------------------------------

def record_progress(job_id: str, run_id: str, stage: str, status: str,
                    detail: str = "") -> str:
    """追加一条 progress 事件（append-only）。"""
    event_id = _event_id()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, job_id, run_id, stage, status, detail, _utcnow()))
        conn.commit()
    finally:
        conn.close()
    return event_id


def commit_plan(plan: PS.ReportPlan, *, run_id: str = "") -> SS.CommitResult:
    """原子提交 plan + 全部 task + current_plan 指针 + PLANNED progress。

    - 幂等复用：同 plan_id + 同输入/契约指纹 → 返回 reused=True，不重写、不切指针。
    - 冲突 fail-closed：同 plan_id 但指纹不同 → 抛错，回滚。
    - 任意异常 → 回滚，不留半个 current plan。
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT job_id, input_fingerprint, contract_fingerprint FROM section_plan "
            "WHERE plan_id = ?", (plan.plan_id,)).fetchone()
        if row is not None:
            conn.rollback()
            if (row["job_id"] == plan.job_id
                    and row["input_fingerprint"] == plan.input_fingerprint
                    and row["contract_fingerprint"] == plan.contract_fingerprint):
                return SS.CommitResult(plan_id=plan.plan_id, reused=True,
                                       current_switched=False,
                                       task_count=len(plan.section_tasks))
            raise ValueError(
                f"plan_id 冲突: {plan.plan_id} 已存在但 job/输入/契约指纹不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_plan (plan_id, job_id, company_id, company_name, credit_type, "
            "report_as_of, template_id, input_fingerprint, contract_fingerprint, planner_version, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (plan.plan_id, plan.job_id, plan.company_id, plan.company_name, plan.credit_type,
             plan.report_as_of, plan.template_id, plan.input_fingerprint,
             plan.contract_fingerprint, plan.planner_version, plan.created_at))

        for ordinal, task in enumerate(plan.section_tasks):
            conn.execute(
                "INSERT INTO section_task (task_id, plan_id, section_id, ordinal, title, purpose, "
                "research_policy, task_schema_version, payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (task.task_id, task.plan_id, task.section_id, ordinal, task.title, task.purpose,
                 task.research_policy, PS.TASK_SCHEMA_VERSION,
                 json.dumps(PS.section_task_to_dict(task), ensure_ascii=False)))

        before = conn.execute("SELECT plan_id FROM current_plan WHERE job_id = ?",
                              (plan.job_id,)).fetchone()
        conn.execute(
            "INSERT INTO current_plan (job_id, company_id, plan_id, switched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "company_id=excluded.company_id, plan_id=excluded.plan_id, "
            "switched_at=excluded.switched_at",
            (plan.job_id, plan.company_id, plan.plan_id, plan.created_at))

        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), plan.job_id, run_id, "planning", "PLANNED",
             f"plan_id={plan.plan_id}", plan.created_at))

        conn.commit()
        switched = (before is None or before["plan_id"] != plan.plan_id)
        return SS.CommitResult(plan_id=plan.plan_id, reused=False,
                               current_switched=switched,
                               task_count=len(plan.section_tasks))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _load_tasks(conn: sqlite3.Connection, plan_id: str) -> list[PS.SectionTask]:
    rows = conn.execute(
        "SELECT payload_json FROM section_task WHERE plan_id = ? ORDER BY ordinal",
        (plan_id,)).fetchall()
    return [PS.section_task_from_dict(json.loads(r["payload_json"])) for r in rows]


def get_plan(plan_id: str) -> PS.ReportPlan | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM section_plan WHERE plan_id = ?",
                           (plan_id,)).fetchone()
        if row is None:
            return None
        tasks = _load_tasks(conn, plan_id)
        return PS.ReportPlan(
            plan_id=row["plan_id"],
            job_id=row["job_id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            credit_type=row["credit_type"],
            report_as_of=row["report_as_of"],
            template_id=row["template_id"],
            input_fingerprint=row["input_fingerprint"],
            contract_fingerprint=row["contract_fingerprint"],
            planner_version=row["planner_version"],
            section_tasks=tuple(tasks),
            created_at=row["created_at"],
        )
    finally:
        conn.close()


def list_tasks(plan_id: str) -> list[PS.SectionTask]:
    conn = _get_conn()
    try:
        return _load_tasks(conn, plan_id)
    finally:
        conn.close()


def get_task(task_id: str) -> PS.SectionTask | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT payload_json FROM section_task WHERE task_id = ?",
                           (task_id,)).fetchone()
        if row is None:
            return None
        return PS.section_task_from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


def get_current_plan(job_id: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT plan_id FROM current_plan WHERE job_id = ?",
                           (job_id,)).fetchone()
        return None if row is None else row["plan_id"]
    finally:
        conn.close()


def list_plans(company_id: str) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT plan_id FROM section_plan WHERE company_id = ? ORDER BY created_at",
            (company_id,)).fetchall()
        return [r["plan_id"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 章节产物写入：commit_section_result（P4-B 接入，原子提交 + current_section 切换）
# ---------------------------------------------------------------------------

def _citation_id(claim_id: str, ref) -> str:
    """citation_id 稳定派生（委托 schema.derive_citation_id，单一来源，避免二次硬编码）。"""
    return SS.derive_citation_id(claim_id, ref)


def _job_id_for_task(conn: sqlite3.Connection, task_id: str) -> str:
    row = conn.execute(
        "SELECT p.job_id FROM section_task t "
        "JOIN section_plan p ON p.plan_id = t.plan_id WHERE t.task_id = ?",
        (task_id,)).fetchone()
    return row["job_id"] if row is not None else ""


def _normalized_payload(result: SS.LegacySectionResultV1) -> dict:
    """规范化 payload（复用深度比较用）：只比内容身份，不含 created_at 等易变字段。"""
    return {
        "section_version": result.section_version,
        "task_id": result.task_id,
        "section_id": result.section_id,
        "status": result.status,
        "markdown": result.markdown,
        "dependency_fingerprint": result.dependency_fingerprint,
        "claims": [c.to_dict() for c in result.claims],
        "unresolved": [SS.unresolved_to_dict(u) for u in result.unresolved],
        "source_run_ids": list(result.source_run_ids),
        "source_question_ids": list(result.source_question_ids),
    }


def _require_legacy_result(result) -> SS.LegacySectionResultV1:
    """v1 表只承载 legacy 产物：current `section-result-2` 在此 fail-closed。

    M930 current 链只能经 ``commit_section_chain_v2`` 落 versioned v2 表；把 current Result
    写进 v1 表会让"旧 public reader 绕门返回 current 对象"，因此必须拒绝。
    """
    if isinstance(result, SS.SectionResult):
        raise SectionStorageConflictError(
            f"section_result(v1) 表只承载 legacy 产物：拒绝 current "
            f"{SS.SECTION_RESULT_SCHEMA_VERSION!r} 的 SectionResult"
            "（M930 current 链只经 commit_section_chain_v2 落 v2 表）")
    if not isinstance(result, SS.LegacySectionResultV1):
        raise SectionStorageConflictError(
            "commit_section_result 入参必须是显式 legacy 视图 "
            f"SS.LegacySectionResultV1；实际为 {type(result).__name__!r}")
    return result


def commit_section_result(result: SS.LegacySectionResultV1, *,
                          run_id: str = "") -> SS.SectionCommitResult:
    """原子提交一个 **legacy** 章节产物：section_result + claims + citations + unresolved +
    current_section 指针 + progress，失败整体回滚。

    **M930-3 定位（P17）**：本函数是 legacy-only 入口，已从 M930 current 写作链移除。
    current 链只经 ``commit_section_chain_v2`` 落 versioned v2 表；current
    `section-result-2` 对象在本函数入口即拒。

    - 写入/复用前必经：结构校验 + section_result_id 由 section_version 派生自洽 +
      task 存在且 result.section_id == task.section_id。
    - 幂等复用：同 section_result_id（由 section_version 派生）→ 深度比较规范化
      payload + dependency_fingerprint（非只比 section_version）；不一致即报存储损坏。
    - 冲突/损坏 fail-closed：任何失败都不切换 current_section 指针。
    - current_section 指针只在完整校验并写入后原子切换（UPSERT）。
    """
    result = _require_legacy_result(result)
    # 0. 结构校验（写入/复用前必经；legacy 形状走 legacy 校验器）。
    errors = svalidator.validate_legacy_section_result(result)
    if errors:
        raise SectionStorageConflictError(
            "章节结构校验失败:\n" + "\n".join(f"  - {e}" for e in errors))
    # 1. section_result_id 必须由 section_version 稳定派生（身份自洽）。
    if SS.derive_section_result_id(result.section_version) != result.section_result_id:
        raise SectionStorageConflictError(
            f"section_result_id 与 section_version 派生不一致: "
            f"{result.section_result_id} != {SS.derive_section_result_id(result.section_version)}")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 2. task 必须存在且 result.section_id == task.section_id（写入/复用前）。
        task_row = conn.execute(
            "SELECT section_id FROM section_task WHERE task_id = ?",
            (result.task_id,)).fetchone()
        if task_row is None:
            conn.rollback()
            raise SectionStorageConflictError(f"task 不存在: {result.task_id}（fail-closed）")
        if task_row["section_id"] != result.section_id:
            conn.rollback()
            raise SectionStorageConflictError(
                f"result.section_id 与 task.section_id 不一致: "
                f"{result.section_id} != {task_row['section_id']}（fail-closed）")

        row = conn.execute(
            "SELECT section_version, payload_json FROM section_result WHERE section_result_id = ?",
            (result.section_result_id,)).fetchone()
        if row is not None:
            conn.rollback()
            if row["section_version"] != result.section_version:
                raise SectionStorageConflictError(
                    f"section_result_id 冲突: {result.section_result_id} 已存在但版本不一致（fail-closed）")
            existing = SS.load_legacy_section_result_for_audit(json.loads(row["payload_json"]))
            if _normalized_payload(existing) != _normalized_payload(result):
                raise SectionStorageCorruptionError(
                    f"section_result 复用前规范化 payload 与自身身份不一致: {result.section_result_id}")
            return SS.SectionCommitResult(
                section_result_id=result.section_result_id, reused=True,
                current_switched=False, claim_count=len(result.claims),
                unresolved_count=len(result.unresolved))

        section_ordinal = PS.PHASE4_SECTION_ORDER.index(result.section_id) \
            if result.section_id in PS.PHASE4_SECTION_ORDER else 999

        conn.execute(
            "INSERT INTO section_result (section_result_id, section_version, task_id, "
            "section_id, status, section_ordinal, payload_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (result.section_result_id, result.section_version, result.task_id,
             result.section_id, result.status, section_ordinal,
             json.dumps(result.to_dict(), ensure_ascii=False),
             result.created_at or _utcnow()))

        for c in result.claims:
            conn.execute(
                "INSERT INTO section_claim (claim_id, section_result_id, section_id, "
                "topic_id, question_ids_json, text, claim_type, citation_refs_json, "
                "payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (c.claim_id, result.section_result_id, c.section_id, c.topic_id,
                 json.dumps(list(c.question_ids), ensure_ascii=False), c.text,
                 c.claim_type,
                 json.dumps([SS.citation_to_dict(r) for r in c.citation_refs],
                            ensure_ascii=False),
                 json.dumps(c.to_dict(), ensure_ascii=False)))
            for ref in c.citation_refs:
                conn.execute(
                    "INSERT INTO section_citation (citation_id, claim_id, section_result_id, "
                    "ref_type, evidence_id, snapshot_id, item_code, formula_id, "
                    "formula_version, period, source_snapshot_id, page_number, payload_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_citation_id(c.claim_id, ref), c.claim_id, result.section_result_id,
                     ref.ref_type, ref.evidence_id, ref.snapshot_id, ref.item_code,
                     ref.formula_id, ref.formula_version, ref.period,
                     ref.source_snapshot_id, ref.page_number,
                     json.dumps(SS.citation_to_dict(ref), ensure_ascii=False)))

        for u in result.unresolved:
            conn.execute(
                "INSERT INTO section_unresolved (unresolved_id, section_result_id, "
                "section_id, topic_id, question_id, state, reason_code, detail, payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (u.unresolved_id, result.section_result_id, u.section_id, u.topic_id,
                 u.question_id, u.state, u.reason_code, u.detail,
                 json.dumps(SS.unresolved_to_dict(u), ensure_ascii=False)))

        before = conn.execute("SELECT section_result_id FROM current_section WHERE task_id = ?",
                              (result.task_id,)).fetchone()
        conn.execute(
            "INSERT INTO current_section (task_id, section_id, section_result_id, switched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "section_id=excluded.section_id, section_result_id=excluded.section_result_id, "
            "switched_at=excluded.switched_at",
            (result.task_id, result.section_id, result.section_result_id,
             result.created_at or _utcnow()))

        job_id = _job_id_for_task(conn, result.task_id)
        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), job_id, run_id, "writing", "SECTION_WRITTEN",
             f"section_result_id={result.section_result_id} section_id={result.section_id}",
             result.created_at or _utcnow()))

        conn.commit()
        switched = (before is None or before["section_result_id"] != result.section_result_id)
        return SS.SectionCommitResult(
            section_result_id=result.section_result_id, reused=False,
            current_switched=switched, claim_count=len(result.claims),
            unresolved_count=len(result.unresolved))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_section_result(section_result_id: str) -> SS.LegacySectionResultV1 | None:
    """按 section_result_id 读取 **legacy** 章节产物（payload_json 往返）。

    legacy-only public reader：返回显式 ``LegacySectionResultV1``，因此不可能被下游当作
    current `section-result-2` 消费；若 v1 表里出现 current marker（不应发生），
    ``load_legacy_section_result_for_audit`` 会 fail-closed 拒绝而不是宽松读成 legacy。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_result WHERE section_result_id = ?",
            (section_result_id,)).fetchone()
        if row is None:
            return None
        return SS.load_legacy_section_result_for_audit(json.loads(row["payload_json"]))
    finally:
        conn.close()


def load_legacy_section_chain_for_audit(section_result_id: str) -> dict | None:
    """**只读**回放一条 legacy 章节链（v1 表：result + claims + citations + unresolved）。

    这是 v1 表唯一的读面：返回纯 dict（不构造任何 current 对象），供审计/迁移核对使用。
    current reader（``load_current_section_chain_v2``）只读 v2 表，二者不得互相冒充。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_result WHERE section_result_id = ?",
            (section_result_id,)).fetchone()
        if row is None:
            return None
        result = SS.load_legacy_section_result_for_audit(json.loads(row["payload_json"]))
        claims = [r["payload_json"] for r in conn.execute(
            "SELECT payload_json FROM section_claim WHERE section_result_id = ? "
            "ORDER BY rowid", (section_result_id,))]
        citations = [r["payload_json"] for r in conn.execute(
            "SELECT payload_json FROM section_citation WHERE section_result_id = ? "
            "ORDER BY rowid", (section_result_id,))]
        unresolved = [r["payload_json"] for r in conn.execute(
            "SELECT payload_json FROM section_unresolved WHERE section_result_id = ? "
            "ORDER BY rowid", (section_result_id,))]
        return {
            "ledger": "v1",
            "section_result_id": section_result_id,
            "section_result": result.to_dict(),
            "claims": [json.loads(s) for s in claims],
            "citations": [json.loads(s) for s in citations],
            "unresolved": [json.loads(s) for s in unresolved],
        }
    finally:
        conn.close()


def get_current_section(task_id: str) -> str | None:
    """按 task_id 读取 current 章节产物指针（section_result_id）。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT section_result_id FROM current_section WHERE task_id = ?",
                           (task_id,)).fetchone()
        return None if row is None else row["section_result_id"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 章节产物写入：commit_evaluation / commit_rework_run（P4-D 接入）
# ---------------------------------------------------------------------------

def _job_id_for_result(conn: sqlite3.Connection, section_result_id: str) -> str:
    row = conn.execute(
        "SELECT p.job_id FROM section_result r "
        "JOIN section_task t ON t.task_id = r.task_id "
        "JOIN section_plan p ON p.plan_id = t.plan_id "
        "WHERE r.section_result_id = ?", (section_result_id,)).fetchone()
    return row["job_id"] if row is not None else ""


def _rework_row_id(evaluation_id: str, target_id: str) -> str:
    return "rw_" + SS.sha256_json([evaluation_id, target_id])[:24]


def commit_evaluation(evaluation: SS.SectionEvaluation, *, run_id: str = "") -> SS.EvaluationCommitResult:
    """原子提交一次章节评估：section_evaluation + section_rework（rework_targets 逐条），
    失败整体回滚。

    - 写入前必经：decision 白名单 + llm_evaluator_calls ∈ {0,1} + evaluation_id 内容寻址自洽
      + 引用的 section_result 已存在（外键）。
    - 幂等复用：同 evaluation_id → 深度比较 payload，一致 reuse。
    - Evaluation 是 SectionResult 的关联对象：只写 section_evaluation/section_rework 表，
      绝不回写 section_result，绝不改变 SectionResult 内容身份。
    """
    if evaluation.decision not in SS.EVALUATION_DECISIONS:
        raise SectionStorageConflictError(
            f"evaluation.decision 非法: {evaluation.decision!r}，允许 {SS.EVALUATION_DECISIONS}")
    if evaluation.llm_evaluator_calls not in (0, 1):
        raise SectionStorageConflictError(
            f"llm_evaluator_calls 必须 ∈ {{0,1}}，收到 {evaluation.llm_evaluator_calls}（fail-closed）")
    if SS.derive_evaluation_id(
            evaluation.section_result_id, evaluation.decision, evaluation.rules_version,
            evaluation.evaluator_prompt_version, evaluation.issues, evaluation.rework_targets,
            evaluation.llm_evaluator_calls) != evaluation.evaluation_id:
        raise SectionStorageConflictError("evaluation_id 与内容派生不一致（fail-closed）")

    # 防线：同一 Evaluation 内 target_id 必须唯一（section_rework.rework_id 主键按
    # (evaluation_id, target_id) 派生）。重复应在汇总边界经 canonicalize_rework_targets
    # 去重；此处 fail-closed，绝不依赖 SQLite IntegrityError 兜底、绝不用 INSERT OR IGNORE 吞冲突。
    seen_target_ids: set[str] = set()
    for t in evaluation.rework_targets:
        if t.target_id in seen_target_ids:
            raise SectionStorageConflictError(
                f"evaluation 内存在重复 rework_target_id: {t.target_id}（fail-closed；"
                f"应在汇总边界经 canonicalize_rework_targets 去重）")
        seen_target_ids.add(t.target_id)

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM section_result WHERE section_result_id = ?",
            (evaluation.section_result_id,)).fetchone()
        if exists is None:
            conn.rollback()
            raise SectionStorageConflictError(
                f"evaluation 引用不存在的 section_result: {evaluation.section_result_id}（fail-closed）")

        row = conn.execute(
            "SELECT payload_json FROM section_evaluation WHERE evaluation_id = ?",
            (evaluation.evaluation_id,)).fetchone()
        if row is not None:
            conn.rollback()
            old = SS.evaluation_from_dict(json.loads(row["payload_json"]))
            # 复用深度比较只比内容身份，剔除 evaluated_at（时间戳非身份，重跑时必然变化）。
            old_d = SS.evaluation_to_dict(old)
            new_d = SS.evaluation_to_dict(evaluation)
            old_d.pop("evaluated_at", None)
            new_d.pop("evaluated_at", None)
            if old_d == new_d:
                return SS.EvaluationCommitResult(
                    evaluation_id=evaluation.evaluation_id, reused=True,
                    rework_target_count=len(evaluation.rework_targets))
            raise SectionStorageCorruptionError(
                f"evaluation_id 冲突: {evaluation.evaluation_id} 已存在但内容不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_evaluation (evaluation_id, section_result_id, rules_version, "
            "evaluator_prompt_version, rules_passed, llm_passed, decision, payload_json, "
            "evaluated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (evaluation.evaluation_id, evaluation.section_result_id, evaluation.rules_version,
             evaluation.evaluator_prompt_version, int(evaluation.rules_passed),
             None if evaluation.llm_passed is None else int(evaluation.llm_passed),
             evaluation.decision, json.dumps(SS.evaluation_to_dict(evaluation), ensure_ascii=False),
             evaluation.evaluated_at or _utcnow()))

        for t in evaluation.rework_targets:
            conn.execute(
                "INSERT INTO section_rework (rework_id, evaluation_id, target_kind, "
                "target_ref, reason, payload_json) VALUES (?,?,?,?,?,?)",
                (_rework_row_id(evaluation.evaluation_id, t.target_id),
                 evaluation.evaluation_id, t.target_kind, t.target_ref, t.reason,
                 json.dumps(SS.rework_target_to_dict(t), ensure_ascii=False)))

        job_id = _job_id_for_result(conn, evaluation.section_result_id)
        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), job_id, run_id, "evaluating", "EVALUATED",
             f"evaluation_id={evaluation.evaluation_id} decision={evaluation.decision}",
             evaluation.evaluated_at or _utcnow()))

        conn.commit()
        return SS.EvaluationCommitResult(
            evaluation_id=evaluation.evaluation_id, reused=False,
            rework_target_count=len(evaluation.rework_targets))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_evaluation(section_result_id: str) -> SS.SectionEvaluation | None:
    """读取某 section_result 的最新评估（独立关联对象，不 join 进 SectionResult）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_evaluation WHERE section_result_id = ? "
            "ORDER BY evaluated_at DESC, rowid DESC LIMIT 1",
            (section_result_id,)).fetchone()
        if row is None:
            return None
        return SS.evaluation_from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


def list_evaluations(section_result_id: str) -> list[SS.SectionEvaluation]:
    """读取某 section_result 的全部评估（按时间升序）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM section_evaluation WHERE section_result_id = ? "
            "ORDER BY evaluated_at ASC, rowid ASC",
            (section_result_id,)).fetchall()
        return [SS.evaluation_from_dict(json.loads(r["payload_json"])) for r in rows]
    finally:
        conn.close()


def commit_rework_run(run: SS.SectionReworkRun, *, run_id: str = "") -> SS.ReworkRunCommitResult:
    """原子提交一次返工批次（append-only 历史事实，不可变）。"""
    if run.batch_no != 0:
        raise SectionStorageConflictError(
            f"返工批次必须为 0（本阶段至多一批），收到 {run.batch_no}（fail-closed）")
    if SS.derive_rework_run_id(run.from_section_result_id, run.section_result_id,
                               run.batch_no) != run.rework_run_id:
        raise SectionStorageConflictError("rework_run_id 与内容派生不一致（fail-closed）")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json FROM section_rework_run WHERE rework_run_id = ?",
            (run.rework_run_id,)).fetchone()
        if row is not None:
            conn.rollback()
            old = SS.rework_run_from_dict(json.loads(row["payload_json"]))
            # 复用深度比较只比内容身份，剔除 created_at（时间戳非身份，重跑时必然变化）。
            old_d = SS.rework_run_to_dict(old)
            new_d = SS.rework_run_to_dict(run)
            old_d.pop("created_at", None)
            new_d.pop("created_at", None)
            if old_d == new_d:
                return SS.ReworkRunCommitResult(rework_run_id=run.rework_run_id, reused=True)
            raise SectionStorageCorruptionError(
                f"rework_run_id 冲突: {run.rework_run_id} 已存在但内容不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_rework_run (rework_run_id, job_id, section_result_id, "
            "from_section_result_id, evaluation_id, batch_no, llm_evaluator_calls, "
            "targets_json, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run.rework_run_id, run.job_id, run.section_result_id, run.from_section_result_id,
             run.evaluation_id, run.batch_no, run.llm_evaluator_calls,
             json.dumps([SS.rework_target_to_dict(t) for t in run.targets], ensure_ascii=False),
             json.dumps(SS.rework_run_to_dict(run), ensure_ascii=False),
             run.created_at or _utcnow()))

        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), run.job_id, run_id, "rework", "REWORKED",
             f"rework_run_id={run.rework_run_id} from={run.from_section_result_id} "
             f"to={run.section_result_id}", run.created_at or _utcnow()))

        conn.commit()
        return SS.ReworkRunCommitResult(rework_run_id=run.rework_run_id, reused=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_rework_runs(from_section_result_id: str) -> list[SS.SectionReworkRun]:
    """读取某父 SectionResult 触发过的全部返工批次（按时间升序）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM section_rework_run WHERE from_section_result_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (from_section_result_id,)).fetchall()
        return [SS.rework_run_from_dict(json.loads(r["payload_json"])) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 运行清单写入：commit_manifest + current_manifest 指针
# ---------------------------------------------------------------------------

def commit_manifest(manifest: SS.SectionRunManifest) -> SS.ManifestCommitResult:
    """原子提交运行清单 + current_manifest 指针切换。

    - manifest 历史 append-only（不可变）；current_manifest 是独立可切换指针。
    - 幂等复用：同 manifest_id → 深度比较 payload，一致 reuse 且不切指针。
    """
    if SS.derive_manifest_id(manifest.job_id, manifest.run_id, manifest.code_fingerprint,
                             manifest.phase3_closure_fingerprint, manifest.batch_versions,
                             manifest.frozen) != manifest.manifest_id:
        raise SectionStorageConflictError("manifest_id 与内容派生不一致（fail-closed）")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json FROM section_run_manifest WHERE manifest_id = ?",
            (manifest.manifest_id,)).fetchone()
        if row is not None:
            conn.rollback()
            old = SS.manifest_from_dict(json.loads(row["payload_json"]))
            # 复用深度比较只比内容身份，剔除 created_at（时间戳非身份，重跑时必然变化）。
            old_d = SS.manifest_to_dict(old)
            new_d = SS.manifest_to_dict(manifest)
            old_d.pop("created_at", None)
            new_d.pop("created_at", None)
            if old_d == new_d:
                return SS.ManifestCommitResult(
                    manifest_id=manifest.manifest_id, reused=True, current_switched=False)
            raise SectionStorageCorruptionError(
                f"manifest_id 冲突: {manifest.manifest_id} 已存在但内容不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_run_manifest (manifest_id, job_id, run_id, code_fingerprint, "
            "phase3_closure_fingerprint, batch_versions_json, frozen_json, payload_json, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (manifest.manifest_id, manifest.job_id, manifest.run_id, manifest.code_fingerprint,
             manifest.phase3_closure_fingerprint,
             json.dumps(manifest.batch_versions, ensure_ascii=False),
             json.dumps(manifest.frozen, ensure_ascii=False),
             json.dumps(SS.manifest_to_dict(manifest), ensure_ascii=False),
             manifest.created_at or _utcnow()))

        before = conn.execute("SELECT manifest_id FROM current_manifest WHERE job_id = ?",
                              (manifest.job_id,)).fetchone()
        conn.execute(
            "INSERT INTO current_manifest (job_id, manifest_id, switched_at) "
            "VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "manifest_id=excluded.manifest_id, switched_at=excluded.switched_at",
            (manifest.job_id, manifest.manifest_id, manifest.created_at or _utcnow()))

        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), manifest.job_id, manifest.run_id, "manifest", "MANIFESTED",
             f"manifest_id={manifest.manifest_id}", manifest.created_at or _utcnow()))

        conn.commit()
        switched = (before is None or before["manifest_id"] != manifest.manifest_id)
        return SS.ManifestCommitResult(
            manifest_id=manifest.manifest_id, reused=False, current_switched=switched)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def current_manifest(job_id: str) -> str | None:
    """按 job_id 读取 current 运行清单指针（manifest_id）。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT manifest_id FROM current_manifest WHERE job_id = ?",
                           (job_id,)).fetchone()
        return None if row is None else row["manifest_id"]
    finally:
        conn.close()


def get_manifest(manifest_id: str) -> SS.SectionRunManifest | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_run_manifest WHERE manifest_id = ?",
            (manifest_id,)).fetchone()
        if row is None:
            return None
        return SS.manifest_from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# M930-3（P17）：current v2 链的原子提交与读回 —— 与 v1 legacy 完全分离
# ---------------------------------------------------------------------------

#: 3B 落地的 **writer 侧** v2 member family（提交顺序即此序；root 表见
#: `current_section_draft_v2`）。五类 member 全部**从 Draft 自身派生**。
V2_WRITER_SIDE_FAMILIES = (
    "current_section_material_manifest_v2",
    "current_section_wmpd_v2",
    "current_section_subject_v2",
    "current_section_proposal_v2",
)
#: 3C 落地的**决定与 accepted binding** family（提交顺序即此序，紧接 writer 侧之后）。
#: 三类对象都由门产生：`ClaimBindingDecision`（每个 subject revision 恰一条）、
#: `ClaimEntailmentDecision`（每个 aggregate=pass 的 factual candidate revision 恰一条）、
#: `AcceptedSupportBinding`（每个通过的 proposal 各一条）。
V2_DECISION_FAMILIES = (
    "current_section_binding_decision_v2",
    "current_section_entailment_decision_v2",
    "current_section_accepted_binding_v2",
)
#: P17 第 3、9–12 项的**门后** family：由 3D（FND successor / Claim / final Narrative /
#: Result / writer-side unresolved / Claim 去向）落库。
#:
#: §三 E：**顺序即提交顺序**，且 `current_section_result_v2` 恒为最后一行——Result 是「整条链
#: 已经完整」的封口对象，先写它就会让「决定/Claim/Narrative 尚未落库」的链在库里看起来已完成。
#: 顺序由本元组唯一给出，`member_rows()` 只按它分段产出，不另行排序。
V2_POST_GATE_FAMILIES = (
    "current_section_fnd_v2",
    "current_section_claim_narrative_disposition_v2",
    "current_section_claim_v2",
    "current_section_narrative_v2",
    # §12.4.3：最终句决定在**正文之后**落行——它核验的是最终句，正文还没写出来的决定不是决定。
    "current_section_final_sentence_decision_v2",
    "current_section_unresolved_v2",
    "current_section_result_v2",
)

#: 逐 family 的 wire marker 期望（P17：「每个 current payload 另有自己的 wire schema marker，
#: 数据库 migration 版本不替代 wire discriminator」）。值一律取**各对象自己的版本常量**，
#: 不写字面量：subject / proposal / wmpd 与 Draft 同属 narrative wire（§三 C 起为 `narr-5`）；
#: manifest 有自己的 `wmm-1`。
V2_FAMILY_WIRE_MARKER = {
    "current_section_draft_v2": NS.NARRATIVE_SCHEMA_VERSION,
    "current_section_material_manifest_v2": NS.MATERIAL_MANIFEST_SCHEMA_VERSION,
    "current_section_wmpd_v2": NS.NARRATIVE_SCHEMA_VERSION,
    "current_section_subject_v2": NS.NARRATIVE_SCHEMA_VERSION,
    "current_section_proposal_v2": NS.NARRATIVE_SCHEMA_VERSION,
    # 3C：三个门侧 member family 与 Draft/proposal 同属 `narr-4` wire。数据库 migration 序号
    # （整数 5）**不**替代 wire discriminator，所以这里必须逐 family 给出 marker 期望。
    "current_section_binding_decision_v2": NS.NARRATIVE_SCHEMA_VERSION,
    "current_section_entailment_decision_v2": NS.NARRATIVE_SCHEMA_VERSION,
    "current_section_accepted_binding_v2": NS.NARRATIVE_SCHEMA_VERSION,
    # 3D/§三 E：门后各 family 的 wire marker **各不相同**（各对象自己的既有版本键）。
    # `SectionClaim`/`SectionResult` 是 `sections.schema` 的 current wire（claim-2 /
    # section-result-2），FND 是 `fnd-2`，Narrative 与 unresolved 的成员载荷是 narr-5，
    # 去向 family 是 `cnd-1`。
    "current_section_fnd_v2": NS.FND_SCHEMA_VERSION,
    "current_section_claim_v2": SS.CLAIM_SCHEMA_VERSION,
    "current_section_narrative_v2": NS.NARRATIVE_SCHEMA_VERSION,
    # writer-side unresolved / block projection 的载荷是**规范投影 dict**（
    # `NS.canonical_unresolved_projections`）：它没有自己的 schema_version 字段，因此这一
    # family 的 marker 用 narr-5 —— 投影形态本身就是 `SectionDraft` 的一部分，与 draft 同 wire。
    "current_section_unresolved_v2": NS.NARRATIVE_SCHEMA_VERSION,
    "current_section_result_v2": SS.SECTION_RESULT_SCHEMA_VERSION,
    "current_section_claim_narrative_disposition_v2":
        NS.CLAIM_NARRATIVE_DISPOSITION_SCHEMA_VERSION,
    # §12.4.3：最终句决定的 wire marker 是它自己的 `nsfid-1`（与 `natfid-1` 那道只读表面比对器
    # 分属两个对象，marker 也不得互相顶替）。
    "current_section_final_sentence_decision_v2": NS.FINAL_SENTENCE_DECISION_SCHEMA_VERSION,
}


def _v2_fingerprint(payload: dict) -> str:
    """payload 的**内容指纹**（规范化 JSON 的 sha256）：读回时逐行重算，列与载荷不符即拒。"""
    return hashlib.sha256(NS.canonical_json(payload).encode("utf-8")).hexdigest()


#: writer 侧各 member family 的**主键列名**（必须与 `_migration_5_statements` 的 DDL 逐字一致）。
#: `draft_id` 由 `_v2_insert` 统一前置，不在此表内重复。
V2_FAMILY_PK_COLUMNS: dict[str, tuple[str, ...]] = {
    "current_section_material_manifest_v2": ("manifest_id",),
    "current_section_wmpd_v2": ("disposition_id",),
    "current_section_subject_v2": ("subject_kind", "subject_id"),
    "current_section_proposal_v2": ("proposal_id",),
    # 3C：列名必须与 `_migration_5_statements` 的 DDL 逐字一致（`draft_id` 同样统一前置）。
    "current_section_binding_decision_v2": ("decision_id",),
    "current_section_entailment_decision_v2": ("decision_id",),
    "current_section_accepted_binding_v2": ("binding_id",),
    # 3D/§三 E：门后 family。narrative family 以 `narrative_kind` 区分成员；当前只落
    # `root` 一行（见 `post_gate_rows`），`narrative_kind` 仍在主键里，未来拆行不必改 DDL。
    "current_section_fnd_v2": ("disposition_id",),
    "current_section_claim_v2": ("claim_id",),
    "current_section_narrative_v2": ("narrative_kind", "narrative_id"),
    "current_section_unresolved_v2": ("unresolved_kind", "unresolved_id"),
    "current_section_result_v2": ("section_result_id",),
    "current_section_claim_narrative_disposition_v2": ("claim_narrative_disposition_id",),
    # 主键列只含 `decision_id`：`_v2_insert` 统一前置 `draft_id`，而 `draft_revision` /
    # `narrative_id` 是**唯一键**的提升列（基数约束在 DDL 的 UNIQUE 上，不重复列进主键）。
    "current_section_final_sentence_decision_v2": ("decision_id",),
}


@dataclass(frozen=True)
class SectionChainV2:
    """`commit_section_chain_v2` 的**唯一**输入：一条 current 链（writer 侧 + 门侧）。

    writer 侧只携带 **Draft root 本身**。manifest / WMPD / candidate / draft-unit / proposal
    五类 member 都是 `SectionDraft` 自己的字段，提交时**从 draft 派生**，因此不可能出现
    「root 与 member 各说一套」的链（也就不需要在读取侧容忍这种偏差）。

    门侧（3C）显式携带三个由门-produced 的**不可变对象束**：aggregate binding decisions、
    entailment decisions、accepted bindings。它们**不从 draft 派生**——draft 里根本没有门后
    身份（这正是 §0.13 的单向性），所以只此一处没有「派生」可依靠，必须由调用方给出，
    并在提交前用 cardinality 断言把它钉死。

    3D/§三 E 的 FND successor / Claim 去向 / Claim / final Narrative / Result 在**同一次提交**
    里成对在场：`result` 是「整条链已完整」的封口对象，因此本容器要求门后束要么**整体缺席**
    （writer + 决定链，3B/3C 的提交面），要么整体在场——半条链在库里看起来与完整链无异，那正是
    §三 E 要禁止的「半事务观感」。

    **不得**以裸 dict 形式从本容器偷渡（dataclass 不接受未登记字段）。
    """

    draft: NS.SectionDraft
    aggregate_decisions: tuple[NS.ClaimBindingDecision, ...] = ()
    entailment_decisions: tuple[NS.ClaimEntailmentDecision, ...] = ()
    accepted_bindings: tuple[NS.AcceptedSupportBinding, ...] = ()
    fact_narrative_dispositions: tuple[NS.FactNarrativeDisposition, ...] = ()
    claim_narrative_dispositions: tuple[NS.ClaimNarrativeDisposition, ...] = ()
    claims: tuple[SS.SectionClaim, ...] = ()
    narrative: NS.SectionNarrative | None = None
    #: §12.4.3：本节 final Narrative 的最终句语义决定（`nsfid-1`，至多一条）。它是**门后**对象
    #: （核验的是最终句），因此与 Claim / Narrative / Result 同装，不打乱 §0.13 的单向性。
    #: 缺省空元组是**旧产物**的合法形态：那是「没有决定」而不是「无需核验」——
    #: `_assert_post_gate_cardinality` 会按 `NS.final_sentence_gate_state` 重算，要求 Result 里
    #: 真有那条 typed block，因此「把决定漏掉」不可能被读成「本节无对象可核」。
    final_sentence_decisions: tuple[NS.FinalSentenceFidelityDecision, ...] = ()
    result: SS.SectionResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.draft, NS.SectionDraft):
            raise SectionStorageConflictError(
                "SectionChainV2.draft 必须是 current `NS.SectionDraft`"
                f"（{NS.NARRATIVE_SCHEMA_VERSION}）；"
                f"实际为 {type(self.draft).__name__!r}"
                "（legacy narr-3 / narr-4 视图只走各自的 legacy reader）")
        for name, cls in (("aggregate_decisions", NS.ClaimBindingDecision),
                          ("entailment_decisions", NS.ClaimEntailmentDecision),
                          ("accepted_bindings", NS.AcceptedSupportBinding),
                          ("fact_narrative_dispositions", NS.FactNarrativeDisposition),
                          ("claim_narrative_dispositions", NS.ClaimNarrativeDisposition),
                          ("claims", SS.SectionClaim),
                          ("final_sentence_decisions", NS.FinalSentenceFidelityDecision)):
            values = tuple(getattr(self, name))
            object.__setattr__(self, name, values)
            bad = [type(v).__name__ for v in values if not isinstance(v, cls)]
            if bad:
                raise SectionStorageConflictError(
                    f"SectionChainV2.{name} 只接受 {cls.__name__}，得到 {sorted(set(bad))}")
        if self.narrative is not None and not isinstance(self.narrative, NS.SectionNarrative):
            raise SectionStorageConflictError(
                "SectionChainV2.narrative 只接受 `NS.SectionNarrative`，"
                f"得到 {type(self.narrative).__name__}")
        if self.result is not None and not isinstance(self.result, SS.SectionResult):
            raise SectionStorageConflictError(
                "SectionChainV2.result 只接受 current `SS.SectionResult`，"
                f"得到 {type(self.result).__name__}（legacy section-result-1 只走 legacy reader）")
        self._assert_post_gate_bundle_is_all_or_nothing()
        if self.result is not None:
            for name in ("task_id", "section_id"):
                if str(getattr(self.result, name)) != str(getattr(self.draft, name)):
                    raise SectionStorageConflictError(
                        f"SectionResult.{name}={getattr(self.result, name)!r} 与本节 draft 的 "
                        f"{getattr(self.draft, name)!r} 不符：结果不得挂到别的节上")
            if str(self.result.section_draft_id) != str(self.draft.draft_id):
                raise SectionStorageConflictError(
                    "SectionResult.section_draft_id 必须**单向**指向本次 draft（"
                    f"{self.result.section_draft_id!r} ≠ {self.draft.draft_id!r}）")
            if str(self.narrative.section_draft_id) != str(self.draft.draft_id) \
                    or str(self.narrative.draft_revision) != str(self.draft.draft_revision):
                raise SectionStorageConflictError(
                    "final Narrative 必须指向同一 draft 与同一 revision"
                    "（跨 revision 的正文不得与本链同装）")
            for claim in self.claims:
                if str(claim.section_id) != str(self.draft.section_id):
                    raise SectionStorageConflictError(
                        f"SectionClaim {claim.claim_id} 不属于本节 {self.draft.section_id!r}")
            # 门后正文的五条机械判据（引用当前性 / 高风险表面 / context 授权 / 去向精确覆盖 /
            # selected 一致）复用 `NS` 的**唯一**实现，存储侧不另立第二套判据：入库前的
            # 复算与组装器读回时的复算是同一函数，否则「库里存的」与「组装出来的」会分叉。
            NS.verify_section_narrative(
                narrative=self.narrative, claims=self.claims,
                accepted_context_binding_ids=tuple(
                    b.accepted_support_binding_id for b in self.accepted_bindings
                    if b.support_semantics == "context"),
                dispositions=self.claim_narrative_dispositions)
            NS.verify_section_narrative_claims_selected(
                narrative=self.narrative, claims=self.claims,
                dispositions=self.claim_narrative_dispositions)
            # §12.4.3：最终句决定的锚点必须落在**本链这一份**正文上（跨 revision / 跨节的决定
            # 不得与本链同装）。基数（≤1）由 DDL 的唯一键与 `_assert_post_gate_cardinality` 各自
            # 把关，这里只查「它说的是不是这份正文」。
            for decision in self.final_sentence_decisions:
                if (str(decision.draft_id) != str(self.draft.draft_id)
                        or str(decision.draft_revision) != str(self.draft.draft_revision)
                        or str(decision.narrative_id) != str(self.narrative.narrative_id)
                        or str(decision.section_id) != str(self.draft.section_id)):
                    raise SectionStorageConflictError(
                        f"最终句决定 {decision.final_sentence_fidelity_decision_id} 的锚点不属于"
                        "本链的这份正文（draft/revision/narrative/section 必须逐项相同）："
                        "跨 revision 的结论不得与本链同装")

    def _assert_post_gate_bundle_is_all_or_nothing(self) -> None:
        """门后束要么整体在场（Result 封口），要么整体缺席（只提交 writer + 决定链）。"""
        absent = {
            "fact_narrative_dispositions": bool(self.fact_narrative_dispositions),
            "claim_narrative_dispositions": bool(self.claim_narrative_dispositions),
            "claims": bool(self.claims),
            "narrative": self.narrative is not None,
            # 最终句决定只参与「没有 Result 时不得单独在场」这一半：有 Result 而**没有**决定是
            # 合法的（§12.4.4 第 4 步的 typed block 场景——那正是「本门没核出来」的如实形态），
            # 但一条挂在空气里的决定同样不得落库。「该有必须有」这一半由
            # `_assert_post_gate_cardinality` 按重算结果判。
            "final_sentence_decisions": bool(self.final_sentence_decisions),
        }
        if self.result is None:
            stray = sorted(name for name, present in absent.items() if present)
            if stray:
                raise SectionStorageConflictError(
                    f"门后对象在**没有** current SectionResult 的链上单独落库：{stray}"
                    "（半条链在库里与完整链无从区分，正是 §三 E 要禁止的观感；"
                    "要提交门后束就必须连 Result 一起提交）")
            return
        if self.narrative is None:
            raise SectionStorageConflictError(
                "门后束有 Result 却缺 final Narrative：Result 是封口对象，正文不得缺席")
        # 「零 Claim 且零表格」的合法形态只有一种：draft **如实登记**了缺口（本节正文即缺口
        # 附录本身）。判据与 `NS.build_section_narrative` 用的是**同一个**实现
        # （`NS.has_registered_gaps`）——定稿口放行、封口口却拒绝，等于同一条链的两端各按一套
        # 口径裁决；而没有登记过缺口的空正文仍然在这里被拒（静默的空章节照样 fail-closed）。
        gap_only = NS.has_registered_gaps(self.draft)
        if not self.claims and not self.narrative.tables and not gap_only:
            raise SectionStorageConflictError(
                "门后束既没有定稿 Claim 也没有表格：空正文不得以 Result 封口"
                "（只有 draft 已登记缺口、正文即缺口附录时才可封口）")
        if not self.fact_narrative_dispositions and not self.claims and not gap_only:
            raise SectionStorageConflictError(
                "门后束的 FND 集为空且没有任何 Claim：被选中的权威事实没有去向记录"
                "（FND 是「预验证事实在正文里的去向」，零去向即缺口未被登记）")

    @property
    def proposals_by_subject(self) -> dict[tuple[str, str], tuple[NS.ProposedSupportRef, ...]]:
        grouped: dict[tuple[str, str], list[NS.ProposedSupportRef]] = {}
        for proposal in self.draft.proposed_support_refs:
            grouped.setdefault((proposal.binding_subject_kind,
                                proposal.binding_subject_id), []).append(proposal)
        return {k: tuple(v) for k, v in grouped.items()}

    def member_rows(self) -> tuple[tuple[str, tuple[str, ...], str, dict, dict], ...]:
        """`(family, 主键值, wire marker, payload, 提升列)` 的有序提交清单。

        顺序 = P17 的提交序：Draft root（`commit_section_chain_v2` 里先写）→ writer 侧
        member → 决定 → accepted binding。manifest 恒为 1 行。

        **writer 侧 member 必须按 Draft 自己的顺序落行，不得另排序**：`claim_candidate_ids` /
        `narrative_draft_unit_ids` / `proposed_support_ids` 是 `identity_body()` 的一部分（顺序
        参与 `derive_draft_id`），而读回时成员行就是**唯一**的原始顺序来源。行序一旦被排序打乱，
        重建出的 Draft 会与 root 声明逐项相等却**顺序不同**，于是 draft_id 对不上——身份重算
        恰好在这一点上 fail-closed（这也说明「排序」不是无害的美化）。门侧三类与门后 family
        则按各自 ID 排序：它们不是 draft 身份的一部分，`_assert_decision_cardinality` 只按集合
        判等，排序在这里只是让提交顺序确定。

        提升列（`extra`）在**这里**按语义给出，而不是在写库侧按 family 猜——`subject_kind`
        并不出现在候选/单元的载荷里，猜不出来。
        """
        draft = self.draft
        rows: list[tuple[str, tuple[str, ...], str, dict, dict]] = []
        manifest = draft.material_manifest
        rows.append(("current_section_material_manifest_v2", (manifest.manifest_id,),
                     manifest.schema_version, manifest.to_dict(), {}))
        for wmpd in draft.material_dispositions:
            rows.append(("current_section_wmpd_v2", (wmpd.wmpd_id,), wmpd.schema_version,
                         wmpd.to_dict(), {"member_ref": wmpd.member_ref}))
        for candidate in draft.claim_candidates:
            rows.append(("current_section_subject_v2",
                         ("claim_candidate", candidate.candidate_id), candidate.schema_version,
                         candidate.to_dict(),
                         {"subject_revision": candidate.draft_revision}))
        for unit in draft.narrative_draft_units:
            rows.append(("current_section_subject_v2",
                         ("narrative_draft_unit", unit.draft_unit_id), unit.schema_version,
                         unit.to_dict(), {"subject_revision": unit.draft_revision}))
        for proposal in draft.proposed_support_refs:
            rows.append(("current_section_proposal_v2", (proposal.proposed_support_id,),
                         proposal.schema_version, proposal.to_dict(), {}))
        for decision in sorted(self.aggregate_decisions,
                               key=lambda d: d.binding_decision_id):
            rows.append(("current_section_binding_decision_v2", (decision.binding_decision_id,),
                         decision.schema_version, decision.to_dict(),
                         {"subject_kind": decision.subject_kind,
                          "subject_id": decision.subject_id,
                          "subject_revision": decision.draft_revision}))
        for decision in sorted(self.entailment_decisions,
                               key=lambda d: d.entailment_decision_id):
            rows.append(("current_section_entailment_decision_v2",
                         (decision.entailment_decision_id,), decision.schema_version,
                         decision.to_dict(),
                         {"candidate_id": decision.claim_candidate_id,
                          "candidate_revision": decision.draft_revision}))
        for binding in sorted(self.accepted_bindings,
                              key=lambda b: b.accepted_support_binding_id):
            rows.append(("current_section_accepted_binding_v2",
                         (binding.accepted_support_binding_id,), binding.schema_version,
                         binding.to_dict(), {}))
        rows.extend(self.post_gate_rows())
        return tuple(rows)

    def post_gate_rows(self) -> tuple[tuple[str, tuple[str, ...], str, dict, dict], ...]:
        """门后束的提交行（**Result 恒为最后一行**）。门后束缺席时返回空元组。

        `SectionResult` 必须在本 root 上**最后**写入（P17 第 11 项）：它一封口，「这条链完整」
        才成为库里可判定的事实。若把它与其它门后行并列，一个「先写 Claim 后写 Result」的中断
        就会留下与完整链无从区分的行集——正是 §三 E 禁止的半事务观感。
        """
        if self.result is None:
            return ()
        narrative = self.narrative
        rows: list[tuple[str, tuple[str, ...], str, dict, dict]] = []
        for disp in sorted(self.fact_narrative_dispositions, key=lambda d: d.disposition_id):
            rows.append(("current_section_fnd_v2", (disp.disposition_id,), disp.schema_version,
                         disp.to_dict(),
                         {"authority_kind": disp.authority_kind,
                          "container_identity": disp.container_identity,
                          "authority_specific_fact_id": disp.authority_specific_fact_id}))
        for disp in sorted(self.claim_narrative_dispositions,
                           key=lambda d: d.claim_narrative_disposition_id):
            rows.append(("current_section_claim_narrative_disposition_v2",
                         (disp.claim_narrative_disposition_id,), disp.schema_version,
                         disp.to_dict(), {"claim_id": disp.claim_id,
                                          "draft_revision": disp.draft_revision}))
        # 定稿 Claim 的顺序与 `SectionResult` 载荷里的 `claims` 顺序**同源**（它进而进
        # `derive_section_version` 的 claim_ids），所以这里同样不得排序：行序是读回重建那个顺序的
        # 唯一来源（理由与 writer 侧 member 相同，见 `member_rows`）。
        for claim in self.claims:
            rows.append(("current_section_claim_v2", (claim.claim_id,), claim.schema_version,
                         SS.claim_to_dict(claim), {}))
        # final Narrative 只落**一行 root**（`narrative_kind="root"`），载荷是
        # `SectionNarrative.to_dict()`。**为什么不逐段落/表格拆 member 行**：段落与表格是
        # narr-5 wire 的**嵌套成员**，它们的载荷没有自己的 `schema_version`，而本 store 的
        # marker 纪律要求「列 marker == 载荷 marker」（这正是挡住 legacy 载荷冒充 current 的
        # 那一条）。为它们开例外等于在本文件里放宽 marker 纪律；而拆行想买到的「成员行被改能
        # 发现」由**身份重算**等强度地拿到——`SectionNarrative.from_dict` 会重建每个段落的
        # `paragraph_id` / 每张表的 `table_id` 并重算 `narrative_id`，任何一处被改都对不上 root
        # 身份而 fail-closed（P17 第 16 项要的正是重算，不是行数）。
        # `narrative_kind` 列仍是三列主键的一部分，未来若要拆行不必再改 DDL。
        rows.append(("current_section_narrative_v2", ("root", narrative.narrative_id),
                     narrative.schema_version, narrative.to_dict(), {}))
        # §12.4.3：最终句决定落在**正文之后**（它核验的是最终句，正文不存在时无从核验）。
        # 零条是合法形态（block 一档），但「该有必须有」不在这一层放宽：见
        # `_assert_post_gate_cardinality` 的按重算结果校验。
        for decision in sorted(self.final_sentence_decisions,
                               key=lambda d: d.final_sentence_fidelity_decision_id):
            rows.append(("current_section_final_sentence_decision_v2",
                         (decision.final_sentence_fidelity_decision_id,),
                         decision.schema_version, decision.to_dict(),
                         {"draft_revision": decision.draft_revision,
                          "narrative_id": decision.narrative_id}))
        rows.extend(self.unresolved_rows())
        rows.append(("current_section_result_v2", (self.result.section_result_id,),
                     self.result.schema_version, SS.section_result_to_dict(self.result), {}))
        return tuple(rows)

    def unresolved_rows(self) -> tuple[tuple[str, tuple[str, ...], str, dict, dict], ...]:
        """writer 侧缺口/冲突/未找到投影的 member 行（§三 E 的 typed unresolved/block 投影）。"""
        return _unresolved_member_rows(self.draft)

    def count_rows(self) -> dict[str, int]:
        """逐 family 的行数（提交前完备性检查与返回值共用同一份计数）。"""
        counts: dict[str, int] = {}
        for family, *_ in self.member_rows():
            counts[family] = counts.get(family, 0) + 1
        return counts


@dataclass(frozen=True)
class LoadedSectionChainV2:
    """`load_current_section_chain_v2` 的读回结果：重建后的 Draft + 实际读到的 family 行。

    `draft` 是用 **member 行**（而非 root 载荷里的一张快照）重建的：`SectionDraft.create`
    会重算 `draft_revision` / `draft_id`，所以任一 member 行被改动都会让身份对不上而 fail-closed
    （P17 第 16 项「每次读回重算 content_id/digest」）。
    """

    draft: NS.SectionDraft
    families: tuple[str, ...]
    members: dict[str, tuple[dict, ...]]
    #: 3C 门侧对象（逐个经各自 `from_dict` 重算身份）。**不**进 `draft`：draft 是门前束，
    #: 把门后对象塞进去会破坏 §0.13 的单向性。
    aggregate_decisions: tuple[NS.ClaimBindingDecision, ...] = ()
    entailment_decisions: tuple[NS.ClaimEntailmentDecision, ...] = ()
    accepted_bindings: tuple[NS.AcceptedSupportBinding, ...] = ()
    #: 3D/§三 E 的门后束（FND 去向 / Claim 去向 / 定稿 Claim / final Narrative / Result）。
    #: 它们是**门后**对象，所以既不能进 `draft`（门前束），也不与门侧决定混装。门后束整体缺席
    #: 时（3B/3C 的提交面）这些字段全为空/None，而不是「读不到就用默认值顶替」。
    fact_narrative_dispositions: tuple[NS.FactNarrativeDisposition, ...] = ()
    claim_narrative_dispositions: tuple[NS.ClaimNarrativeDisposition, ...] = ()
    claims: tuple[SS.SectionClaim, ...] = ()
    narrative: NS.SectionNarrative | None = None
    #: §12.4.3：本节的最终句语义决定（`nsfid-1`，至多一条）。空元组有**两种**含义，读回侧已经
    #: 用 `NS.final_sentence_gate_state` 重算把两者分开——「本节无承载事实的最终句」（合法，
    #: Result 里也没有 block）与「本门未能形成决定」（Result 里**必须**有那条 typed block）。
    final_sentence_decisions: tuple[NS.FinalSentenceFidelityDecision, ...] = ()
    result: SS.SectionResult | None = None

    def to_chain(self) -> "SectionChainV2":
        """读回结果 → 可直接再提交的链容器（供复用/往返测试）。

        **每个门后 family 都必须原样搬过来**：漏掉一类（曾经漏过 `final_sentence_decisions`）
        会让 `commit_and_verify_section_chain_v2` 的行集比对报「仅在提交侧」，而报出来的现象
        是「往返不稳定」——真正的原因不是库里的行不对，是这个转写器把读到的行又扔了。
        """
        return SectionChainV2(
            draft=self.draft, aggregate_decisions=self.aggregate_decisions,
            entailment_decisions=self.entailment_decisions,
            accepted_bindings=self.accepted_bindings,
            fact_narrative_dispositions=self.fact_narrative_dispositions,
            claim_narrative_dispositions=self.claim_narrative_dispositions,
            claims=self.claims, narrative=self.narrative,
            final_sentence_decisions=self.final_sentence_decisions,
            result=self.result)


def _v2_insert(conn: sqlite3.Connection, family: str, *, draft_id: str,
               pk_values: tuple[str, ...], marker: str, payload: dict,
               promoted: dict[str, str]) -> None:
    """把一个 member 行写进对应 v2 family（append-only；列真值只作约束与外键）。"""
    pk_columns = V2_FAMILY_PK_COLUMNS[family]
    if len(pk_columns) != len(pk_values):
        raise SectionStorageConflictError(
            f"{family} 主键列 {pk_columns} 与取值 {pk_values} 元数不符")
    columns = ["draft_id", *pk_columns]
    values: list[Any] = [draft_id, *pk_values]
    for name, value in promoted.items():
        columns.append(name)
        values.append(value)
    columns += ["ordinal", "schema_version", "content_fingerprint", "payload_json", "created_at"]
    # ordinal：同一 (draft, family) 内的序号；行序由 `member_rows()` 的排序保证确定性。
    ordinal = conn.execute(
        f"SELECT COUNT(*) AS n FROM {family} WHERE draft_id = ?", (draft_id,)).fetchone()["n"]
    values += [int(ordinal), marker, _v2_fingerprint(payload),
               NS.canonical_json(payload), _utcnow()]
    conn.execute(
        f"INSERT INTO {family} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})", tuple(values))


def commit_section_chain_v2(chain: SectionChainV2) -> dict:
    """**原子**提交一条 current 链的 writer 侧部分（P17：Draft → member 顺序，失败整体回滚）。

    与 legacy `commit_section_result` 的两个隔离面：

    1. **表隔离**：只写 `current_section_*_v2`；v1 表一行不碰，`current_section` 指针也不切；
    2. **读面隔离**：v2 行只经 `load_current_section_chain_v2` / `load_legacy_section_chain_for_audit`
       读出，两者互不冒充（`get_section_result` 只读 v1 且只返回 legacy 视图）。

    幂等复用：同 `draft_id` 已存在时逐行重算指纹并比较，全等则 `reused=True`，不等即存储损坏。
    同一 `(task_id, section_id)` 下的**不同** revision 各自成链（append-only，不做「最后写入者
    胜出」）。
    """
    draft = chain.draft
    rows = chain.member_rows()
    # 提交前的**完备性**检查：root 载荷里的 exact-set 必须与行集逐项相等（缺/多/重复即拒）。
    expected = {
        "current_section_material_manifest_v2": [draft.material_manifest.manifest_id],
        "current_section_wmpd_v2": sorted(draft.material_disposition_ids),
        "current_section_subject_v2": sorted(
            [f"claim_candidate:{i}" for i in draft.claim_candidate_ids]
            + [f"narrative_draft_unit:{i}" for i in draft.narrative_draft_unit_ids]),
        "current_section_proposal_v2": sorted(draft.proposed_support_ids),
    }
    actual: dict[str, list[str]] = {}
    for family, pk_values, marker, payload, _promoted in rows:
        actual.setdefault(family, []).append(_v2_row_key(pk_values))
        # wire marker 在**开事务之前**核完：数据库 migration 序号不替代 wire discriminator。
        if (marker, payload.get("schema_version")) != \
                (V2_FAMILY_WIRE_MARKER[family], V2_FAMILY_WIRE_MARKER[family]):
            raise SectionStorageConflictError(
                f"{family} 行的 wire marker 不一致：列 {marker!r}，载荷 "
                f"{payload.get('schema_version')!r}，期望 {V2_FAMILY_WIRE_MARKER[family]!r}")
    for family, ids in expected.items():
        if sorted(actual.get(family, [])) != ids:
            raise SectionStorageConflictError(
                f"{family} 的行集与 Draft root 声明的 exact-set 不相等：行集 "
                f"{sorted(actual.get(family, []))}，声明 {ids}"
                "（缺行/多行/重复行都必须在写库前 fail-closed）")
    # 3C：门侧三类的**基数**断言。它们不能从 draft 派生，所以「是否完整」必须在这里钉死，
    # 否则一次提交少一条决定就能让「每个 subject revision 恰一条」变成纸面约束。
    _assert_decision_cardinality(chain)
    # 3D/§三 E：门后束的基数与守恒断言，外加「Result 是最后一行」这一条**顺序**约束。
    _assert_post_gate_cardinality(chain, rows)
    unknown = sorted(set(actual) - set(V2_WRITER_SIDE_FAMILIES)
                     - set(V2_DECISION_FAMILIES) - set(V2_POST_GATE_FAMILIES))
    if unknown:
        raise SectionStorageConflictError(f"提交面出现未登记 family：{unknown}")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT schema_version, content_fingerprint, payload_json "
            "FROM current_section_draft_v2 WHERE draft_id = ?", (draft.draft_id,)).fetchone()
        if existing is not None:
            stored = conn.execute(
                "SELECT COUNT(*) AS n FROM current_section_draft_v2 WHERE draft_id = ?",
                (draft.draft_id,)).fetchone()["n"]
            root_payload = json.loads(existing["payload_json"])
            stored_rows, stored_payloads = _v2_collect_rows(
                conn, draft.draft_id,
                families=(*V2_WRITER_SIDE_FAMILIES, *V2_DECISION_FAMILIES,
                          *V2_POST_GATE_FAMILIES))
            conn.rollback()
            if stored != 1:
                raise SectionStorageCorruptionError(
                    f"draft_id {draft.draft_id} 在 v2 root 表出现 {stored} 行")
            if (existing["schema_version"] != draft.schema_version
                    or existing["content_fingerprint"] != _v2_fingerprint(root_payload)
                    or root_payload.get("draft_id") != draft.draft_id):
                raise SectionStorageCorruptionError(
                    f"current SectionDraft 复用前自洽性校验失败: {draft.draft_id}")
            # 复用不是「root 在就算数」，也不是「主键对得上就算数」：
            # (1) 行集必须与本次声明的 exact-set 逐项相等（缺行/多行都拒）——**门侧三类同样**：
            #     已存决定/绑定行被删或被加也必须发现，否则「复用」会把缺决定的链固化下来；
            # (2) 用已存 member 行**重建** Draft（门侧对象各自 `from_dict` 重算身份），身份必须与
            #     root 载荷一致（载荷被改而指纹自洽的情况只有重建才能发现）。
            for family, ids in sorted(expected.items()):
                if stored_rows.get(family, []) != ids:
                    raise SectionStorageCorruptionError(
                        f"{family} 已存行集与 root 声明的 exact-set 不相等：已存 "
                        f"{stored_rows.get(family, [])}，声明 {ids}")
            for family in (*V2_DECISION_FAMILIES, *V2_POST_GATE_FAMILIES):
                if sorted(stored_rows.get(family, [])) != sorted(actual.get(family, [])):
                    raise SectionStorageCorruptionError(
                        f"{family} 已存行集与本次声明的行集不相等：已存 "
                        f"{sorted(stored_rows.get(family, []))}，声明 "
                        f"{sorted(actual.get(family, []))}")
            _rebuild_draft_v2(draft.draft_id, root_payload, stored_payloads)
            return {"ledger": "v2", "draft_id": draft.draft_id, "reused": True,
                    "family_rows": {f: len(v) for f, v in sorted(actual.items())}}

        # root 载荷 = `identity_body` + 两个**不进身份体但进修订**的字段（`draft_id` /
        # `created_at` 是 root 行自己的登记项）。`writer_attempt` 与 `natural_prose_draft`
        # 与 `SectionDraft.to_dict()` 同款必须随载荷留存：`draft_revision` 由
        # `derive_draft_revision(attempt=…, natural_prose_digest=…)` 算出，而读回路径只能拿
        # **载荷**里的这两项复算。丢掉它们，带草稿层（或 `attempt > 1`）的 draft 会在读回时
        # 算出一个**没有草稿层**的修订，与成员行声明的修订不符 —— 整条链 fail-closed。
        # 它们不进 `identity_body`，因此 `draft_id` 与全部历史载荷逐字节不变；历史载荷缺这两个
        # 键时读回按默认（首轮 / 无草稿层）取值，与加入它们之前的行为完全相同。
        root_payload = {**draft.identity_body(), "draft_id": draft.draft_id,
                        "created_at": draft.created_at,
                        "writer_attempt": draft.writer_attempt,
                        "natural_prose_draft": [u.to_dict() for u in draft.natural_prose_draft]}
        conn.execute(
            "INSERT INTO current_section_draft_v2 (draft_id, task_id, section_id, "
            "draft_revision, ordinal, schema_version, content_fingerprint, payload_json, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (draft.draft_id, draft.task_id, draft.section_id, draft.draft_revision,
             _section_ordinal(draft.section_id), draft.schema_version,
             _v2_fingerprint(root_payload), NS.canonical_json(root_payload), _utcnow()))
        for family, pk_values, marker, payload, promoted in rows:
            _v2_insert(conn, family, draft_id=draft.draft_id, pk_values=pk_values,
                       marker=marker, payload=payload, promoted=promoted)
        conn.commit()
        return {"ledger": "v2", "draft_id": draft.draft_id, "reused": False,
                "family_rows": {f: len(v) for f, v in sorted(actual.items())}}
    except sqlite3.Error as exc:
        conn.rollback()
        raise SectionStorageConflictError(f"current 链提交失败（已回滚）：{exc}") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def commit_and_verify_section_chain_v2(chain: SectionChainV2) -> dict:
    """**恰一次**正式提交 + 从 store 读回并重算（§三 E 的生产入口）。

    为什么不是两个公开步骤：`commit_section_chain_v2` 与 `load_current_section_chain_v2`
    分开调用时，「提交成功但读回没人做」是一种**静默降级**——库里那条链是否真能重建出同一份
    产物，只有读回重算才知道。本函数把「提交恰好一次」变成结构约束：调用方拿不到第二次提交的
    机会，也就不可能用「再提交一遍」掩盖第一次的读回失败。

    读回重算的判据是**行集逐行相等**（`LoadedSectionChainV2.to_chain().member_rows()` vs 本次
    提交的 `member_rows()`）：它同时覆盖 root 载荷、五类 writer 侧 member、三类门侧决定、门后
    Claim/Narrative/unresolved/Result —— 任一行被改写或身份重算对不上都在这里现形。逐字段对比
    是行集相等的子集，因此不再重复一遍。

    失败语义：提交本身的失败由 `commit_section_chain_v2` 的事务整体回滚（不留半条链）；读回失败
    即存储损坏，抛 `SectionStorageCorruptionError`，**不**回写、不重试、不省略。
    """
    outcome = commit_section_chain_v2(chain)
    draft_id = chain.draft.draft_id
    loaded = load_current_section_chain_v2(draft_id)
    if loaded is None:
        raise SectionStorageCorruptionError(
            f"提交回报成功但读回为空: {draft_id}（提交面与读面不一致，不得当作已落库）")
    committed = chain.member_rows()
    reread = loaded.to_chain().member_rows()
    if reread != committed:
        committed_keys = {(f, _v2_row_key(pk)) for f, pk, *_ in committed}
        reread_keys = {(f, _v2_row_key(pk)) for f, pk, *_ in reread}
        raise SectionStorageCorruptionError(
            f"读回重算与本次提交的行集不相等（往返不稳定）: {draft_id}；"
            f"仅在提交侧 {sorted(committed_keys - reread_keys)[:6]}，"
            f"仅在读回侧 {sorted(reread_keys - committed_keys)[:6]}，"
            f"共有行但内容不同 {len(committed) - len(committed_keys ^ reread_keys)}")
    return {"commit": outcome, "roundtrip": "verified",
            "family_rows": dict(chain.count_rows())}


def _assert_decision_cardinality(chain: SectionChainV2) -> None:
    """提交前核 P17 第 6–8 项的**逐 subject 基数**（缺/多/重/额外一律 fail-closed）。

    * 每个 `(subject_kind, subject_id, draft_revision)` **恰好**一条 aggregate decision，
      且 subject 集与 draft 声明的 subject 集**相等**；
    * 每个 aggregate=pass 的 factual candidate revision **恰好**一条 entailment decision；
      aggregate=fail 的 factual candidate 与所有 context subject **零条**（failed aggregate
      自身即机械拒绝 audit，不另存一条 entailment）；
    * 每个**通过**的 proposal **恰好**一条 accepted binding；未通过 proposal 零条；
    * factual accepted binding 必须引用一条已提交的 entailment decision，context binding
      必须**不带** entailment 引用（两个方向都拒，而不是只拒一个）。
    """
    draft = chain.draft
    revision = draft.draft_revision

    by_subject: dict[tuple[str, str], NS.ClaimBindingDecision] = {}
    for decision in chain.aggregate_decisions:
        if decision.draft_revision != revision:
            raise SectionStorageConflictError(
                f"aggregate 决定 {decision.binding_decision_id} 属于另一 draft revision："
                "不得跨 revision 混装进同一条链")
        key = (decision.subject_kind, decision.subject_id)
        if key in by_subject:
            raise SectionStorageConflictError(
                f"subject {key} 配到多条 aggregate 决定：每个 subject revision 恰好一条")
        by_subject[key] = decision
    expected_subjects = set(chain.proposals_by_subject)
    if set(by_subject) != expected_subjects:
        raise SectionStorageConflictError(
            "aggregate 决定的 subject 集与 draft 的 proposal subject 集不相等"
            f"（多 {sorted(set(by_subject) - expected_subjects)}，"
            f"缺 {sorted(expected_subjects - set(by_subject))}）")

    by_candidate: dict[tuple[str, str], NS.ClaimEntailmentDecision] = {}
    for decision in chain.entailment_decisions:
        if decision.draft_revision != revision:
            raise SectionStorageConflictError(
                f"entailment 决定 {decision.entailment_decision_id} 属于另一 draft revision")
        key = (decision.claim_candidate_id, decision.draft_revision)
        if key in by_candidate:
            raise SectionStorageConflictError(
                f"候选 {key[0]}（revision {key[1]}）有两条 entailment 决定："
                "同一 candidate revision 恰好一条")
        by_candidate[key] = decision
        aggregate = by_subject.get(("claim_candidate", decision.claim_candidate_id))
        if aggregate is None:
            raise SectionStorageConflictError(
                f"entailment 决定 {decision.entailment_decision_id} 指向没有 aggregate 决定的"
                "候选（伪造决定）")
        if aggregate.result != "pass":
            raise SectionStorageConflictError(
                f"aggregate 未通过的候选 {decision.claim_candidate_id} 仍有 entailment 决定："
                "failed aggregate 自身即机械拒绝 audit，语义门不得在它上面开跑")
        if decision.binding_decision_id != aggregate.binding_decision_id \
                or decision.support_set_digest != aggregate.support_set_digest:
            raise SectionStorageConflictError(
                f"entailment 决定 {decision.entailment_decision_id} 未绑定同一通过的 aggregate "
                "决定与同一 support-set digest")
    for key, aggregate in by_subject.items():
        if aggregate.result != "pass":
            continue
        if key[0] == "claim_candidate" and (key[1], revision) not in by_candidate:
            raise SectionStorageConflictError(
                f"通过的 factual candidate {key[1]} 缺 entailment 决定："
                "零条不是「未核验」而是缺记录，拒绝提交")

    passing_proposals = {
        proposal.proposed_support_id
        for (kind, subject_id), proposals in chain.proposals_by_subject.items()
        if by_subject[(kind, subject_id)].result == "pass"
        for proposal in proposals}
    factual_subject_ids = {subject_id for (kind, subject_id), decision in by_subject.items()
                          if kind == "claim_candidate" and decision.result == "pass"
                          and (subject_id, revision) in by_candidate
                          and by_candidate[(subject_id, revision)].verdict == "entailed"}
    bound: dict[str, NS.AcceptedSupportBinding] = {}
    for binding in chain.accepted_bindings:
        pid = binding.proposed_support_id
        if pid in bound:
            raise SectionStorageConflictError(
                f"proposal {pid} 配到多条 accepted binding：每个通过的 proposal 恰好一条")
        if pid not in passing_proposals:
            raise SectionStorageConflictError(
                f"accepted binding {binding.accepted_support_binding_id} 指向未通过的 proposal "
                f"{pid}：未通过的支撑边不得被接受")
        if binding.draft_revision != revision:
            raise SectionStorageConflictError(
                f"accepted binding {binding.accepted_support_binding_id} 属于另一 draft revision")
        if binding.support_semantics == "factual":
            if binding.binding_subject_id not in factual_subject_ids:
                raise SectionStorageConflictError(
                    f"factual accepted binding {binding.accepted_support_binding_id} 的候选不是"
                    "「已通过聚合门且已被蕴含」的候选")
            decision = by_candidate.get((binding.binding_subject_id, revision))
            if decision is None or binding.entailment_decision_id != \
                    decision.entailment_decision_id:
                raise SectionStorageConflictError(
                    f"factual accepted binding {binding.accepted_support_binding_id} 未引用本链"
                    "中该候选的 entailment 决定")
        elif binding.entailment_decision_id:
            raise SectionStorageConflictError(
                f"context accepted binding {binding.accepted_support_binding_id} 携带 entailment "
                "决定：context 不进入语义门")
        bound[pid] = binding
    missing = sorted(passing_proposals - set(bound))
    if missing:
        raise SectionStorageConflictError(
            f"通过的 proposal 缺 accepted binding：{missing}"
            "（每个通过的 proposal 各产生一条，缺一条即不得提交）")


def _assert_post_gate_cardinality(
        chain: SectionChainV2,
        rows: tuple[tuple[str, tuple[str, ...], str, dict, dict], ...],
) -> None:
    """提交前核门后束的基数、守恒与**行序**（§三 E）。

    本函数**不做**任何语义判断（它不看文风、不看正文好坏），只问「同一批门后对象有没有彼此
    对齐」：Claim 集是否与 Result 一致、每条支撑边是否在**它那条路径**上恰好一条去向
    （路径 A → FND，路径 B → 它自己那份 material 的 WMPD `support_usages`）、每条定稿 Claim
    是否落在本次 revision 上。门后束缺席（`result is None`）时它只检查「没有半条链」，其余
    交给 `SectionChainV2.__post_init__`。

    与 `_assert_decision_cardinality` 同一定位：这些关系不能从 draft 派生，所以「是否完整」
    必须在写库前钉死，否则一次提交少一行就能让纸面约束变成空话。
    """
    if chain.result is None:
        return
    draft = chain.draft
    revision = str(draft.draft_revision)

    # (1) Result 恒为最后一行：它一封口，「这条链完整」才成为库里可判定的**行序**事实。
    if not rows or rows[-1][0] != "current_section_result_v2":
        raise SectionStorageConflictError(
            f"门后提交序的最后一行是 {rows[-1][0] if rows else None!r} 而不是 "
            "current_section_result_v2：SectionResult 必须在同一事务里最后写入")
    if sum(1 for row in rows if row[0] == "current_section_result_v2") != 1:
        raise SectionStorageConflictError("一条链只能有一个 current SectionResult")

    # (2) Result 的 Claim 集与链上定稿 Claim 集必须**逐条同内容**（不是「至少包含」）：
    #     否则「Result 里写着 A、链上存着 B」会同时成立，而下游只读 Result。
    chain_claims = {claim.claim_id: SS.claim_to_dict(claim) for claim in chain.claims}
    result_claims = {claim.claim_id: SS.claim_to_dict(claim) for claim in chain.result.claims}
    if chain_claims != result_claims:
        differing = sorted(cid for cid in set(chain_claims) & set(result_claims)
                           if chain_claims[cid] != result_claims[cid])
        raise SectionStorageConflictError(
            "SectionResult.claims 与链上定稿 Claim 集不相等："
            f"Result 独有 {sorted(set(result_claims) - set(chain_claims))[:6]}，"
            f"链上独有 {sorted(set(chain_claims) - set(result_claims))[:6]}，"
            f"同 id 不同内容 {differing[:6]}")

    # (3) 缺口：Result 的 unresolved 必须**精确等于** draft 声明的缺口 ∪ **至多一条**由
    #     `NS.final_sentence_gate_state` 重算出的最终句门 block（§12.4.4 第 4 步的收窄；与 §五
    #     的 draft↔Result 同一判据）。收窄**不是**放宽：draft 缺口一条不得丢，Result 也不得
    #     多出重算结果之外的第二条；而「重算说要阻断」时那条 block **必须真的在**——只查
    #     「没多出东西」会让一个没带最终句决定的链被读成「一致」。
    block = FSF.final_sentence_block_unresolved(
        section_id=str(draft.section_id), narrative=chain.narrative,
        decisions=chain.final_sentence_decisions, claims=chain.claims,
        accepted_bindings=chain.accepted_bindings)
    declared_gaps = set(str(x) for x in draft.unresolved_ids)
    result_gaps = {str(u.unresolved_id): u for u in chain.result.unresolved}
    want_extra = {str(block.unresolved_id)} if block is not None else set()
    added = sorted(set(result_gaps) - declared_gaps)
    if declared_gaps - set(result_gaps) or set(added) != want_extra:
        raise SectionStorageConflictError(
            "SectionResult 的缺口集与 draft 声明的缺口集不相等："
            f"Result 独有 {added[:6]}（最终句门重算要求独有 {sorted(want_extra)[:6]}），"
            f"draft 独有 {sorted(declared_gaps - set(result_gaps))[:6]}"
            "（draft 缺口不得丢；Result 也不得多出重算之外的第二条）")
    if block is not None:
        recorded = result_gaps[str(block.unresolved_id)]
        if recorded != block:
            raise SectionStorageConflictError(
                f"最终句门 block {block.unresolved_id} 与重算结果不相等："
                f"提交 {recorded!r}，重算 {block!r}"
                "（同一缺口集合只有一个内容寻址身份）")

    # (4) 每条定稿 Claim：落在本次 revision 上，且引用的支撑边都在本链中；同时它**自己的
    #     候选**得到的每条 accepted binding 都必须被它引用（反向无遗漏）。
    binding_by_id = {b.accepted_support_binding_id: b for b in chain.accepted_bindings}
    for claim in chain.claims:
        if str(claim.claim_candidate_revision) != revision:
            raise SectionStorageConflictError(
                f"定稿 Claim {claim.claim_id} 属于另一 candidate revision "
                f"（{claim.claim_candidate_revision!r} ≠ {revision!r}）")
        dangling = sorted(set(claim.accepted_binding_ids) - set(binding_by_id))
        if dangling:
            raise SectionStorageConflictError(
                f"定稿 Claim {claim.claim_id} 引用了本链不存在的 accepted binding："
                f"{dangling[:6]}")
        own = {b.accepted_support_binding_id for b in chain.accepted_bindings
               if str(b.binding_subject_id) == str(claim.claim_candidate_id)}
        omitted = sorted(own - set(claim.accepted_binding_ids))
        if omitted:
            raise SectionStorageConflictError(
                f"定稿 Claim {claim.claim_id} 漏引它自己候选的 accepted binding："
                f"{omitted[:6]}（Claim 的支撑边必须完整有序，不得只取一部分）")

    # (5) 支撑边守恒：两条 factual 路径**各自的同源去向**都不得静默省略。
    #
    #     路径 A（prevalidated authority fact）的去向是 FND：每条路径 A 边恰被一条 FND 声明。
    #
    #     路径 B（exact material 派生）**没有 FND**，而且不可能有：`FactNarrativeDisposition`
    #     的边形状被 `validate_support_edge_shape(..., authorization_path="path_a_prevalidated")`
    #     固定——它必须绑定 authority 专属 **fact id**，并显式禁止 `path_b_material_derived`
    #     边携带任何 fact identity。因此把「每条 factual 边都要有 FND」当作守恒判据，等于要求
    #     材料派生的边伪造一条预验证事实身份（材料冒充事实）。路径 B 的去向在**材料侧**：
    #     该边所属 proposal 必须出现在**它自己那份 material** 的 WMPD `support_usages` 里。
    factual_edges = {b.accepted_support_binding_id: b for b in chain.accepted_bindings
                     if str(b.support_semantics) == "factual"}
    path_a = {bid: b for bid, b in factual_edges.items()
              if str(b.authorization_path) == "path_a_prevalidated"}
    path_b = {bid: b for bid, b in factual_edges.items()
              if str(b.authorization_path) == "path_b_material_derived"}
    unclassified = sorted(set(factual_edges) - set(path_a) - set(path_b))
    if unclassified:
        raise SectionStorageConflictError(
            f"这些 factual 支撑边的授权路径既不是路径 A 也不是路径 B：{unclassified[:6]}"
            "（factual 边只有这两条路径；第三条路径必然绕过本节的守恒判据）")

    declared: dict[str, int] = {}
    for disp in chain.fact_narrative_dispositions:
        stray_claims = sorted(set(disp.section_claim_ids) - set(chain_claims))
        if stray_claims:
            raise SectionStorageConflictError(
                f"FND {disp.disposition_id} 引用了本节未定稿的 Claim：{stray_claims[:6]}")
        for binding_id in disp.accepted_binding_ids:
            if binding_id in path_b:
                raise SectionStorageConflictError(
                    f"FND {disp.disposition_id} 把路径 B 支撑边 {binding_id} 记成了"
                    "「预验证权威事实的去向」：材料派生的边不得冒充权威事实"
                    "（它的去向在材料侧 WMPD 的 support_usages 里）")
            # `path_a` 与 `binding_by_id` 都取自同一条链，因此「路径 A 成员」即蕴含「边存在」，
            # 不存在第三种「引用了不存在的边」的情形——不另设一条永远不可达的分支。
            if binding_id not in path_a:
                raise SectionStorageConflictError(
                    f"FND {disp.disposition_id} 引用了非路径 A factual 边 {binding_id}："
                    "FND 只记录预验证权威事实的去向")
            declared[binding_id] = declared.get(binding_id, 0) + 1
    duplicated = sorted(b for b, n in declared.items() if n > 1)
    if duplicated:
        raise SectionStorageConflictError(
            f"这些支撑边被多条去向同时声明：{duplicated[:6]}"
            "（同一支撑边只能属于一个权威事实的去向）")
    undeclared = sorted(set(path_a) - set(declared))
    if undeclared:
        raise SectionStorageConflictError(
            f"这些路径 A 支撑边没有任何去向记录：{undeclared[:6]}"
            "（被接受的预验证权威事实必须在 FND 里有唯一去向，不得静默省略）")

    # 路径 B 的材料侧同源：边 → manifest 成员 → 该成员的 WMPD，三段身份必须逐字对齐。
    wmpd_by_member = {str(d.member_ref): d for d in draft.material_dispositions}
    for binding_id in sorted(path_b):
        binding = path_b[binding_id]
        member_ref = NS.manifest_member_ref(str(binding.authority_container_id),
                                            str(binding.material_id))
        member = draft.material_manifest.entry_for(member_ref)
        if member is None:
            raise SectionStorageConflictError(
                f"路径 B 支撑边 {binding_id} 引用的 material "
                f"{binding.material_id!r}（容器 {binding.authority_container_id!r}）不在本节 "
                "exact manifest 里：路径 B 只授权 manifest 内的真实材料")
        wmpd = wmpd_by_member.get(member_ref)
        if wmpd is None:
            raise SectionStorageConflictError(
                f"路径 B 支撑边 {binding_id} 的 manifest 成员 {member_ref} 没有处理去向"
                "（每个成员恰一条 WMPD）")
        if str(wmpd.usage) != "used" or \
                str(binding.proposed_support_id) not in wmpd.support_usages:
            raise SectionStorageConflictError(
                f"路径 B 支撑边 {binding_id} 的 proposal "
                f"{binding.proposed_support_id} 不在它自己那份 material 的 WMPD "
                f"support_usages 里（usage={wmpd.usage!r}）：材料侧去向与支撑边不同源")
        if str(binding.source_identity) != str(member.source_identity):
            raise SectionStorageConflictError(
                f"路径 B 支撑边 {binding_id} 的 source_identity 与 manifest 成员不符："
                f"{binding.source_identity!r} ≠ {member.source_identity!r}"
                "（同一 material_id 不得指向另一份来源）")
        if binding.payload_ref is None or \
                dict(binding.payload_ref) != dict(member.payload_ref):
            raise SectionStorageConflictError(
                f"路径 B 支撑边 {binding_id} 的 payload_ref 与 manifest 成员的 exact 载体"
                "不符或缺席：路径 B 的载荷必须逐字来自该成员解析出的真实 payload")
        if NS.locator_sort_key(binding.locator_ref) != NS.locator_sort_key(member.locator_ref):
            raise SectionStorageConflictError(
                f"路径 B 支撑边 {binding_id} 的 locator_ref 与 manifest 成员的 exact 定位"
                "不符：路径 B 必须落在该成员自己的 locator 上")
    claim_binding_ids = {claim.claim_id: claim.accepted_binding_ids
                         for claim in chain.claims}
    binding_roles = {b.accepted_support_binding_id: b.support_role
                     for b in chain.accepted_bindings}
    for disp in chain.fact_narrative_dispositions:
        NS.verify_fnd_reference_sets(
            disp, accepted_binding_ids=disp.accepted_binding_ids,
            section_claim_ids=disp.section_claim_ids,
            claim_binding_ids=claim_binding_ids, binding_roles=binding_roles)


def _v2_row_key(pk_values: tuple[str, ...]) -> str:
    """member 行在**行集比对**里的规范键（subject family 用 `kind:id`，其余用单键）。"""
    return ":".join(str(v) for v in pk_values) if len(pk_values) > 1 else str(pk_values[0])


#: 缺口投影的三个来源（提交序）：族名 → (投影字段, 该投影自带的权威 id 字段候选)。
_UNRESOLVED_GROUPS = (
    ("unresolved", "unresolved_projections", ("unresolved_id",)),
    ("conflict", "conflict_projections", ("conflict_id", "audit_id")),
    ("not_found", "not_found_projections", ("not_found_id", "audit_id")),
)


def _unresolved_member_rows(
        draft: NS.SectionDraft,
) -> tuple[tuple[str, tuple[str, ...], str, dict, dict], ...]:
    """缺口/冲突/未找到投影的 member 行（§三 E 的 typed unresolved/block 投影）。

    行的载荷是一层**显式信封**：`{"schema_version", "unresolved_kind", "unresolved_id",
    "projection"}`（`unresolved_id` 与主键列同名同值）。
    投影本身是 current draft wire 里的规范 dict，**没有**自己的 wire discriminator，所以绝不把
    `schema_version` 塞进投影内部（那会让行载荷与 `draft.unresolved_projections` 不再逐字节
    相同，读回就无法比对）。行 id 优先用投影自带的权威 id（`unresolved` 投影必然带
    `unresolved_id`，因此行集可与 `draft.unresolved_ids` 逐项对齐），没有自带 id 的投影
    （conflict / not_found audit）用规范形态的**内容寻址**。

    投影在 `SectionDraft` 里已按规范序排好，所以行序天然确定，不需要在这里再排一次。
    """
    rows: list[tuple[str, tuple[str, ...], str, dict, dict]] = []
    for kind, field, id_fields in _UNRESOLVED_GROUPS:
        for projection in getattr(draft, field):
            row_id = _projection_row_id(kind, projection, id_fields)
            rows.append(("current_section_unresolved_v2", (kind, row_id),
                         draft.schema_version,
                         # `unresolved_id` 与主键列**同名同值**：读回时不查列就能拿到行身份。
                         {"schema_version": draft.schema_version,
                          "unresolved_kind": kind, "unresolved_id": row_id,
                          "projection": dict(projection)},
                         {}))
    return tuple(rows)


def _projection_row_id(kind: str, projection: dict, id_fields: tuple[str, ...]) -> str:
    """缺口投影行的身份：投影自带权威 id 时用它（可与其上游 exact-set 逐项比对），否则内容寻址。

    两者都是**确定性**的：同一投影永远得到同一 id，因此「同一批缺口、不同提交顺序」不会造出
    两套行身份。
    """
    for field in id_fields:
        value = str(projection.get(field) or "")
        if value:
            return value
    return NS.content_id("proj_", {"unresolved_kind": kind, "projection": projection})


def _v2_read_row(family: str, row: sqlite3.Row) -> dict:
    """读一行 member/root 并逐项校验：载荷指纹、载荷 marker、列 marker。

    复用路径与读回路径共用同一个校验：**只比主键不足以发现被改过的载荷**，因此任何
    「已存在即可复用」的判断都必须先过这里。
    """
    item = json.loads(row["payload_json"])
    if _v2_fingerprint(item) != row["content_fingerprint"]:
        raise SectionStorageCorruptionError(
            f"{family} 载荷指纹与列不符（draft {row['draft_id']}）")
    expected = V2_FAMILY_WIRE_MARKER[family]
    if item.get("schema_version") != expected:
        raise SectionStorageCorruptionError(
            f"{family} 载荷 wire marker 不是 {expected!r}（实际 {item.get('schema_version')!r}）")
    if row["schema_version"] != item["schema_version"]:
        raise SectionStorageCorruptionError(
            f"{family} 列 marker 与载荷 marker 不一致（draft {row['draft_id']}）")
    return item


def _v2_collect_rows(conn: sqlite3.Connection, draft_id: str, *,
                     families: tuple[str, ...] = (),
                     ) -> tuple[dict[str, list[str]], dict[str, list[dict]]]:
    """读出给定 family 的全部 member 行：`(规范键集, 载荷集)`；逐行先过 `_v2_read_row`。

    `families` 缺省为 writer 侧（3B 的提交面）。3C 的复用路径要把三个门侧 family 一并读出，
    否则「已存在即可复用」会放过「决定行被删掉」的存储损坏。
    """
    keys: dict[str, list[str]] = {}
    payloads: dict[str, list[dict]] = {}
    for family in (families or V2_WRITER_SIDE_FAMILIES):
        keys[family] = []
        payloads[family] = []
        for row in conn.execute(f"SELECT * FROM {family} WHERE draft_id = ? ORDER BY ordinal",
                               (draft_id,)):
            payloads[family].append(_v2_read_row(family, row))
            keys[family].append(_v2_row_key(tuple(row[c] for c in V2_FAMILY_PK_COLUMNS[family])))
        keys[family] = sorted(keys[family])
    return keys, payloads


def _section_ordinal(section_id: str) -> int:
    return PS.PHASE4_SECTION_ORDER.index(section_id) \
        if section_id in PS.PHASE4_SECTION_ORDER else 999


def load_current_section_chain_v2(draft_id: str) -> LoadedSectionChainV2 | None:
    """读回一条 current 链（**只读 v2 表**），并用 member 行重建 Draft、重算身份。

    逐 payload 校验 wire marker：载荷里出现 `narr-3` 等 legacy marker 即拒（legacy 只经
    `load_legacy_section_chain_for_audit` / `get_section_result` 读）；列与载荷不一致也拒。
    """
    conn = _get_conn()
    try:
        root = conn.execute(
            "SELECT schema_version, content_fingerprint, payload_json "
            "FROM current_section_draft_v2 WHERE draft_id = ?", (draft_id,)).fetchone()
        if root is None:
            return None
        payload = json.loads(root["payload_json"])
        if _v2_fingerprint(payload) != root["content_fingerprint"]:
            raise SectionStorageCorruptionError(
                f"current SectionDraft 载荷指纹与列不符: {draft_id}")
        if payload.get("schema_version") != NS.NARRATIVE_SCHEMA_VERSION:
            raise SectionStorageCorruptionError(
                f"current SectionDraft 载荷 wire marker 不是 "
                f"{NS.NARRATIVE_SCHEMA_VERSION!r}（实际 {payload.get('schema_version')!r}）："
                "legacy 载荷只经 legacy reader 读")
        if root["schema_version"] != payload["schema_version"]:
            raise SectionStorageCorruptionError(
                f"current SectionDraft 列 marker 与载荷 marker 不一致: {draft_id}")

        fetched: dict[str, list[dict]] = {}
        #: 主键列的真值（非载荷字段）。narrative family 的 `narrative_kind` 只存在于**列**里
        #: （载荷是 `SectionNarrative.to_dict()`，它没有 kind 字段），所以「这一行是不是 root」
        #: 只能从列判定，也必须把列与载荷的 `narrative_id` 对齐——否则改一列就能让一行段落载荷
        #: 冒充 root。
        row_pks: dict[str, list[tuple[str, ...]]] = {}
        for family in (*V2_WRITER_SIDE_FAMILIES, *V2_DECISION_FAMILIES,
                       *V2_POST_GATE_FAMILIES):
            rows = conn.execute(
                f"SELECT * FROM {family} WHERE draft_id = ? ORDER BY ordinal",
                (draft_id,)).fetchall()
            for row in rows:
                row_pks.setdefault(family, []).append(
                    tuple(str(row[c] or "") for c in V2_FAMILY_PK_COLUMNS[family]))
                fetched.setdefault(family, []).append(_v2_read_row(family, row))
        draft = _rebuild_draft_v2(draft_id, payload, fetched)
        # 门侧对象逐行用**自己的 reader** 重建（`from_dict` 会重算派生身份），因此被改过的
        # 决定/绑定载荷在读回时对不上身份而 fail-closed，而不是被当成"已存在"读出来。
        try:
            aggregate_decisions = tuple(
                NS.ClaimBindingDecision.from_dict(d)
                for d in fetched.get("current_section_binding_decision_v2", []))
            entailment_decisions = tuple(
                NS.ClaimEntailmentDecision.from_dict(d)
                for d in fetched.get("current_section_entailment_decision_v2", []))
            accepted_bindings = tuple(
                NS.AcceptedSupportBinding.from_dict(d)
                for d in fetched.get("current_section_accepted_binding_v2", []))
            post_gate = _rebuild_post_gate_v2(fetched, draft, row_pks=row_pks,
                                              accepted_bindings=accepted_bindings)
        except NS.NarrativeSchemaError as exc:
            raise SectionStorageCorruptionError(
                f"current 链的门侧/门后 member 行无法重建为同一对象（被改行/缺行）：{exc}") \
                from exc
        return LoadedSectionChainV2(
            draft=draft, families=tuple(sorted(fetched)),
            members={f: tuple(v) for f, v in sorted(fetched.items())},
            aggregate_decisions=aggregate_decisions,
            entailment_decisions=entailment_decisions,
            accepted_bindings=accepted_bindings,
            fact_narrative_dispositions=post_gate["fact_narrative_dispositions"],
            claim_narrative_dispositions=post_gate["claim_narrative_dispositions"],
            claims=post_gate["claims"], narrative=post_gate["narrative"],
            final_sentence_decisions=post_gate["final_sentence_decisions"],
            result=post_gate["result"])
    finally:
        conn.close()


def _rebuild_post_gate_v2(fetched: dict[str, list[dict]], draft: NS.SectionDraft, *,
                          row_pks: dict[str, list[tuple[str, ...]]],
                          accepted_bindings: tuple[NS.AcceptedSupportBinding, ...] = (),
                          ) -> dict[str, Any]:
    """用门后 member 行重建门后束，并**在读取侧重做同一套完整性判定**。

    两条纪律：

    * **整体缺席合法，半条链非法**：`current_section_result_v2` 是本束的封口对象，它缺席时
      其余五个 family 必须**都是空行集**；反之它在场时读回必须能重建出 Narrative 与全部去向。
      这样「写一半」在库里就不是一个可读回的状态，而不是等到下游拼装时才炸。
    * **每个成员各走自己的 reader**：`from_dict` 重算派生身份（Claim 身份、FND 身份、
      narrative_id），被改过的行在这里对不上身份而 fail-closed——不是「读得到就算数」。
    """
    result_rows = fetched.get("current_section_result_v2", [])
    others = {family: fetched.get(family, [])
              for family in V2_POST_GATE_FAMILIES
              if family != "current_section_result_v2"}
    if not result_rows:
        half = sorted(family for family, rows in others.items() if rows)
        if half:
            raise SectionStorageCorruptionError(
                f"current 链有门后行却缺 current SectionResult：{half}"
                "（门后束必须整体在场或整体缺席，半条链不可读回）")
        return {"fact_narrative_dispositions": (), "claim_narrative_dispositions": (),
                "claims": (), "narrative": None, "result": None,
                "final_sentence_decisions": ()}
    if len(result_rows) != 1:
        raise SectionStorageCorruptionError(
            f"current 链的 SectionResult 必须恰 1 行，实际 {len(result_rows)} 行")
    claims = tuple(SS.claim_from_dict(p) for p in fetched.get("current_section_claim_v2", []))
    narrative_rows = fetched.get("current_section_narrative_v2", [])
    narrative_pks = row_pks.get("current_section_narrative_v2", [])
    kinds = [pk[0] for pk in narrative_pks if len(pk) == 2]
    if len(narrative_rows) != 1 or kinds != ["root"]:
        raise SectionStorageCorruptionError(
            "current 链的 narrative family 必须恰 1 行且**列** kind=root"
            f"（实际 {len(narrative_rows)} 行，列 kind {kinds}）："
            "段落/表格是 root 载荷里的嵌套成员，不得出现独立行")
    # 列与载荷必须指同一条正文：载荷没有 kind 字段，所以「这一行是 root」由列承担；列的
    # `narrative_id` 若与载荷自报的不同，就是「另一条正文的 id 挂在 root 行上」。
    if narrative_pks[0][1] != str(narrative_rows[0].get("narrative_id") or ""):
        raise SectionStorageCorruptionError(
            "current 链的 narrative 行列 narrative_id 与载荷自身身份不一致："
            f"列 {narrative_pks[0][1]!r}，载荷 {narrative_rows[0].get('narrative_id')!r}")
    narrative = NS.SectionNarrative.from_dict(narrative_rows[0])
    dispositions = tuple(NS.FactNarrativeDisposition.from_dict(p)
                         for p in fetched.get("current_section_fnd_v2", []))
    claim_dispositions = tuple(
        NS.ClaimNarrativeDisposition.from_dict(p)
        for p in fetched.get("current_section_claim_narrative_disposition_v2", []))
    result = SS.section_result_from_dict(result_rows[0])
    # 读回后的复算：正文明细与去向、缺口投影都在这里对同一批行重新成立，否则「库里存的」
    # 与「写进去的」会分叉，而分叉只能被组装器的逐字节比对偶然发现。
    NS.verify_section_narrative(
        narrative=narrative, claims=claims,
        accepted_context_binding_ids=tuple(
            str(p["accepted_support_binding_id"])
            for p in fetched.get("current_section_accepted_binding_v2", [])
            if str(p.get("support_semantics")) == "context"),
        dispositions=claim_dispositions)
    NS.verify_section_narrative_claims_selected(
        narrative=narrative, claims=claims, dispositions=claim_dispositions)
    if {c.claim_id for c in claims} != {c.claim_id for c in result.claims}:
        raise SectionStorageCorruptionError(
            "current 链的定稿 Claim 行集与 SectionResult 载荷的 Claim 集不相等")
    # 顺序也是身份：`SectionResult.claim_ids` 的**顺序**进了 `section_version`，所以行的顺序
    # 与载荷的顺序必须逐位相同——只比集合会把「顺序被换过」读成同一条链。
    if tuple(c.claim_id for c in claims) != tuple(c.claim_id for c in result.claims):
        raise SectionStorageCorruptionError(
            "current 链的定稿 Claim 行顺序与 SectionResult 载荷的 Claim 顺序不一致")
    # 缺口投影的行集必须与 draft 声明的投影**逐项相等**（缺行/多行/被改行一律拒）：缺口是
    # 「不得静默丢弃」的那一类，所以它既要有行可查，也要与 root 的缺口集对齐。
    stored_projection_keys = sorted(
        _v2_row_key((str(p["unresolved_kind"]), str(p["unresolved_id"])))
        for p in fetched.get("current_section_unresolved_v2", []))
    expected_projection_keys = sorted(
        _v2_row_key(pk_values)
        for _family, pk_values, _marker, _payload, _promoted
        in _unresolved_member_rows(draft))
    if stored_projection_keys != expected_projection_keys:
        raise SectionStorageCorruptionError(
            "current 链的缺口投影行集与 draft 声明的投影不相等：已存 "
            f"{stored_projection_keys[:6]}，应存 {expected_projection_keys[:6]}")
    # `unresolved` 投影还带权威 id，因此额外与 draft 的缺口 id 集对齐（conflict / not_found
    # 是审计投影，没有对应的 draft id 集）。
    stored_unresolved_ids = sorted(str(p["unresolved_id"])
                                   for p in fetched.get("current_section_unresolved_v2", [])
                                   if str(p.get("unresolved_kind")) == "unresolved")
    if stored_unresolved_ids != sorted(str(x) for x in draft.unresolved_ids):
        raise SectionStorageCorruptionError(
            "current 链的 unresolved 投影行与 draft.unresolved_ids 不相等")
    # §12.4.3：最终句决定逐行走**自己的 reader**（重算身份），并在读回时**重做**写入侧的同一套
    # 判定：Result 的缺口必须 = draft 的缺口 ∪ 按 `NS.final_sentence_gate_state` 重算出的那一条
    # typed block。**「该有必须有」这一半正是读回存在的理由**——只查「没多出东西」会让一条
    # 把决定漏掉（或被删掉）的链读成自洽：重算说要阻断，而 Result 里没有那条 block。
    decisions = tuple(
        NS.FinalSentenceFidelityDecision.from_dict(p)
        for p in fetched.get("current_section_final_sentence_decision_v2", []))
    derive_block = FSF.final_sentence_block_unresolved(
        section_id=str(draft.section_id), narrative=narrative, decisions=decisions,
        claims=claims, accepted_bindings=accepted_bindings)
    declared_gaps = {str(x) for x in draft.unresolved_ids}
    result_gaps = {str(u.unresolved_id): u for u in result.unresolved}
    want_extra = {str(derive_block.unresolved_id)} if derive_block is not None else set()
    added = sorted(set(result_gaps) - declared_gaps)
    if declared_gaps - set(result_gaps) or set(added) != want_extra:
        raise SectionStorageCorruptionError(
            "current 链的 Result 缺口与 draft 缺口 + 最终句门 block 不相等："
            f"Result 独有 {added[:6]}（重算要求独有 {sorted(want_extra)[:6]}），"
            f"draft 独有 {sorted(declared_gaps - set(result_gaps))[:6]}")
    if derive_block is not None and result_gaps[str(derive_block.unresolved_id)] != derive_block:
        raise SectionStorageCorruptionError(
            f"current 链的最终句门 block {derive_block.unresolved_id} 与重算结果不相等："
            f"已存 {result_gaps[str(derive_block.unresolved_id)]!r}，重算 {derive_block!r}")
    return {"fact_narrative_dispositions": dispositions,
            "claim_narrative_dispositions": claim_dispositions,
            "claims": claims, "narrative": narrative, "result": result,
            "final_sentence_decisions": decisions}


def _rebuild_draft_v2(draft_id: str, root: dict,
                      fetched: dict[str, list[dict]]) -> NS.SectionDraft:
    """用 member 行重建 Draft 并核验 root 身份（缺行/多行/被改行都在这里 fail-closed）。"""
    manifests = fetched.get("current_section_material_manifest_v2", [])
    if len(manifests) != 1:
        raise SectionStorageCorruptionError(
            f"current 链的 exact material manifest 必须恰 1 行，实际 {len(manifests)} 行")
    manifest = NS.WriterMaterialManifest.from_dict(manifests[0])
    subjects = fetched.get("current_section_subject_v2", [])
    try:
        draft = NS.SectionDraft.create(
            # 载荷**声明**的修订显式传入：不传的话 `create` 会自行推导，`__post_init__` 的
            # 「声明 vs 重算」比对就变成同义反复，载荷里那个值等于没人核。显式传入后，「重建
            # 输入算不出载荷声明的修订」（例如载荷少了进修订的 `natural_prose_draft` /
            # `writer_attempt`）会**指名**修订不符，而不是绕到候选行的比对上去报一个含糊的错。
            draft_revision=root["draft_revision"],
            task_id=root["task_id"], section_id=root["section_id"],
            company_id=root["company_id"], report_as_of=root["report_as_of"],
            contract_version=root["contract_version"],
            contract_fingerprint=root["contract_fingerprint"],
            producer_kind=root["producer_kind"],
            writer_policy_version=root["writer_policy_version"],
            prompt_version=root["prompt_version"], model_policy=root["model_policy"],
            authority_container_ids=tuple(root["authority_container_ids"]),
            material_manifest=manifest,
            material_dispositions=tuple(NS.WriterMaterialProcessingDisposition.from_dict(d)
                                        for d in fetched.get("current_section_wmpd_v2", [])),
            claim_candidates=tuple(NS.ClaimCandidate.from_dict(s) for s in subjects
                                   if s.get("candidate_id")),
            narrative_draft_units=tuple(NS.NarrativeDraftUnit.from_dict(s) for s in subjects
                                        if s.get("draft_unit_id")),
            proposed_support_refs=tuple(NS.ProposedSupportRef.from_dict(p)
                                        for p in fetched.get("current_section_proposal_v2", [])),
            unresolved_ids=tuple(root["unresolved_ids"]),
            unresolved_projections=tuple(root["unresolved_projections"]),
            coverage_summary=dict(root["coverage_summary"]),
            conflict_projections=tuple(root["conflict_projections"]),
            not_found_projections=tuple(root["not_found_projections"]),
            dependency_fingerprint=root["dependency_fingerprint"],
            created_at=root.get("created_at") or "", title=root.get("title") or "",
            writer_rules_version=root.get("writer_rules_version") or "",
            writer_renderer_version=root.get("writer_renderer_version") or "",
            # 这两项**不进身份体但进修订**（`derive_draft_revision`）：不带它们重建，带草稿层
            # 或 `attempt > 1` 的 draft 会算出与成员行不符的修订而 fail-closed。缺键的历史载荷
            # 取默认（首轮 / 无草稿层），与加入这两个键之前读回的结果逐字节相同。
            writer_attempt=int(root.get("writer_attempt") or 1),
            natural_prose_draft=tuple(NS.NaturalProseDraftUnit.from_dict(u)
                                      for u in root.get("natural_prose_draft") or ()))
    except NS.NarrativeSchemaError as exc:
        raise SectionStorageCorruptionError(
            f"current 链的 member 行无法重建出同一 Draft（缺行/多行/被改行）：{exc}") from exc
    rebuilt = draft.identity_body()
    declared = {k: v for k, v in root.items() if k in rebuilt}
    if draft.draft_id != draft_id or rebuilt != declared:
        raise SectionStorageCorruptionError(
            f"current 链重建后的 Draft 身份与 root 载荷不一致: {draft_id}"
            f"（不一致字段 {_identity_diff(rebuilt, declared)}）")
    return draft


def _identity_diff(rebuilt: dict, declared: dict) -> str:
    """身份不一致的**可读**诊断：逐字段给出两侧摘要（列表只报条数与首尾差异项）。"""
    parts: list[str] = []
    for key in sorted(rebuilt):
        left, right = rebuilt[key], declared.get(key)
        if left == right:
            continue
        if isinstance(left, list) and isinstance(right, list):
            parts.append(
                f"{key}(重建 {len(left)} 项 / root {len(right)} 项，仅在重建 "
                f"{[str(x) for x in left if x not in right][:3]}，仅在 root "
                f"{[str(x) for x in right if x not in left][:3]})")
        elif isinstance(left, dict) and isinstance(right, dict):
            parts.append(f"{key}(重建 {sorted(left)[:6]} / root {sorted(right)[:6]})")
        else:
            parts.append(f"{key}(重建 {str(left)[:60]!r} / root {str(right)[:60]!r})")
    return "；".join(parts[:6])


# ---------------------------------------------------------------------------
# self-check / summary
# ---------------------------------------------------------------------------

def _summary() -> dict:
    conn = _get_conn()
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for t in ("section_plan", "section_task", "current_plan", "section_result",
                  "section_claim", "section_unresolved", "section_evaluation",
                  "section_rework", "section_rework_run", "section_run_manifest",
                  "current_manifest", "progress"):
            counts[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        views = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        return {
            "store_schema_version": SECTION_STORE_SCHEMA_VERSION,
            "expected_tables_present": [t for t in EXPECTED_TABLES if t in tables],
            "missing_tables": [t for t in EXPECTED_TABLES if t not in tables],
            "missing_v2_views": [v for v in V2_SECTION_VIEWS if v not in views],
            "migration_count": conn.execute(
                "SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"],
            "row_counts": counts,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.store",
        description="Section Store 初始化 / 摘要")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    init_db(args.db)
    print(f"已初始化: {args.db}")
    if args.init or args.summary:
        print(json.dumps(_summary(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

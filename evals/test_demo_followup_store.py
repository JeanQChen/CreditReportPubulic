"""Eval: 边界③ follow-up 三表的真库追加语义（§16.10 #35，M930-3B）。

用法: python -m evals.test_demo_followup_store

在临时库上（不触碰任何历史库、不联网、不调 LLM）证明：

 1. `init_topic_store` 建出 need/decision/run 三表 + 全部 no_update/no_delete 触发器；
 2. `commit_follow_up_run` 只**追加**：need / run / decision 各按自身身份落行，重放同一批次
    幂等（`reused=True`、行数不变）；
 3. append-only：同 id 但内容不同的 need / run 行 → `StorageConflictError`，绝不覆盖历史行；
    UPDATE / DELETE 被触发器 ABORT；
 4. 批次内聚：decision 的 `need_id` 必须属于本批次；已执行裁决的 `new_pack_id` 必须**真的
    存在**于 `topic_pack`（新 Pack 是 append 出来的，不得自报）；
 5. 读取只经 follow-up 自己的 reader：`load_follow_up_run` / `load_follow_up_needs` /
    `load_follow_up_decisions`，三表都不是 Pack 的 `_CHILD_TABLES`（不混进 Pack 读路径）；
 6. 同一 draft revision 的后续批次追加新行，旧行逐字不变（「need 已裁决」靠追加表达）。
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_runtime as TR  # noqa: E402
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


_RUN_ID = "run-fu-1"
_TASK_ID = "task-fu-1"
_DRAFT_REV = "rev-7"


def _need(statement: str, *, aspect_id: str = "a1", revision: str = _DRAFT_REV,
          writer: str = "writer-1") -> TS.FollowUpNeed:
    return TS.FollowUpNeed(
        need_id=TS.derive_follow_up_need_id(statement, "company::company_identity",
                                            aspect_id, "company", revision),
        need_schema_version=TS.FOLLOW_UP_NEED_SCHEMA_VERSION, statement=statement,
        target_requirement_id="company::company_identity", topic_id="company_identity",
        question_id="company_subject_match", aspect_id=aspect_id, section_id="company",
        section_draft_revision=revision,
        contract_authorized_scope=("a1", "company_identity", "company_subject_match"),
        requiredness="required", expected_source_class="company_industry", budget_hint="",
        writer_identity=writer)


def _rejected(need: TS.FollowUpNeed, reason: str = "target_requirement_unresolved"
              ) -> TS.FollowUpDecision:
    return TR._reject_follow_up(need, reason, "harness 侧证明", None, None)


def _executed(need: TS.FollowUpNeed, pack_id: str) -> TS.FollowUpDecision:
    return TS.FollowUpDecision(
        decision_id=TS.derive_follow_up_decision_id(need.need_id, "accepted", "accepted_run",
                                                    TR.FOLLOW_UP_RULES_VERSION),
        need_id=need.need_id, verdict="accepted", rules_version=TR.FOLLOW_UP_RULES_VERSION,
        authorized_requirement_id="company::company_identity",
        resolved_topic_id="company_identity", resolved_aspect_id=need.aspect_id,
        reason="accepted_run", executed=True, new_pack_id=pack_id, trace_refs=("trace-fu-1",))


def _run(needs: tuple[TS.FollowUpNeed, ...], decisions: tuple[TS.FollowUpDecision, ...],
         *, new_pack_ids: tuple[str, ...] = (), trace_refs: tuple[str, ...] = ("trace-fu-1",),
         writer: str = "writer-1", revision: str = _DRAFT_REV,
         run_id: str = _RUN_ID) -> TS.FollowUpRun:
    need_ids = tuple(n.need_id for n in needs)
    return TS.FollowUpRun(
        follow_up_run_id=TS.derive_follow_up_run_id(run_id, need_ids,
                                                    TR.FOLLOW_UP_RULES_VERSION),
        run_id=run_id, task_id=_TASK_ID, section_id="company",
        section_draft_revision=revision, writer_identity=writer,
        rules_version=TR.FOLLOW_UP_RULES_VERSION, need_ids=need_ids,
        decision_ids=tuple(d.decision_id for d in decisions), new_pack_ids=new_pack_ids,
        trace_refs=trace_refs)


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _payload(db: Path, table: str, id_col: str, row_id: str) -> str:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT payload FROM {table} WHERE {id_col}=?",
                            (row_id,)).fetchone()["payload"]
    finally:
        conn.close()


def main() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "topic_store.sqlite"
        TST.init_topic_store(db)

        # ------------------------------------------------------------------
        # 1. 结构：三表 + 触发器 + 无 Pack 依赖。
        # ------------------------------------------------------------------
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            triggers = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
        finally:
            conn.close()
        check(set(TST._FOLLOW_UP_TABLES) <= tables,
              "边界③三表在真库中建成")
        for table in TST._FOLLOW_UP_TABLES:
            check({f"trg_{table}_no_update", f"trg_{table}_no_delete"} <= triggers,
                  f"{table} 的 UPDATE/DELETE 触发器已建")

        # ------------------------------------------------------------------
        # 2. 追加 + 重放幂等。
        # ------------------------------------------------------------------
        n1, n2 = _need("需要补充主体一致性材料"), _need("需要补充经营范围材料", aspect_id="a2")
        d1, d2 = _rejected(n1), _rejected(n2, reason="cross_section_target")
        run = _run((n1, n2), (d1, d2))
        first = TST.commit_follow_up_run(run, (n1, n2), (d1, d2))
        check(first.follow_up_run_id == run.follow_up_run_id and first.need_count == 2
              and first.decision_count == 2 and first.new_pack_ids == ()
              and first.reused is False,
              "首批追加：2 need + 2 decision + 1 run，reused=False")
        counts = {t: _count(db, t) for t in TST._FOLLOW_UP_TABLES}
        check(counts == {"topic_follow_up_need": 2, "topic_follow_up_run": 1,
                         "topic_follow_up_decision": 2},
              f"行数与批次一致：{counts}")

        second = TST.commit_follow_up_run(run, (n1, n2), (d1, d2))
        check(second.reused is True
              and {t: _count(db, t) for t in TST._FOLLOW_UP_TABLES} == counts,
              "重放同一批次幂等：reused=True 且一行不增")

        loaded = TST.load_follow_up_run(run.follow_up_run_id)
        check(loaded is not None and loaded.to_dict() == run.to_dict(),
              "run 行按身份读回，逐字等于追加时的对象")
        check(tuple(n.need_id for n in TST.load_follow_up_needs(_RUN_ID))
              == (n1.need_id, n2.need_id),
              "need 按 run_id + seq 读回（顺序即追加顺序）")
        check(tuple(d.decision_id for d in TST.load_follow_up_decisions(run.follow_up_run_id))
              == (d1.decision_id, d2.decision_id),
              "decision 按 run 身份 + seq 读回")
        check(TST.load_follow_up_run("fur_nonexistent") is None,
              "读不存在的 run 返回 None（不补建、不抛错）")

        # ------------------------------------------------------------------
        # 3. append-only：同 id 不同内容 → 冲突；UPDATE/DELETE → ABORT。
        # ------------------------------------------------------------------
        same_id_other_writer = _need("需要补充主体一致性材料", writer="writer-2")
        check(same_id_other_writer.need_id == n1.need_id
              and same_id_other_writer.to_dict() != n1.to_dict(),
              "对照 need：同 need_id 但内容不同（仅 writer_identity 不同）")
        # 换一个 run 身份，才能看清「need 行冲突」时整个批次都没落库（否则命中的是已存在的 run 行）。
        conflict_run = _run((same_id_other_writer, n2), (d1, d2), run_id="run-fu-conflict")
        expect_raises("同 need_id 不同内容再追加",
                      lambda: TST.commit_follow_up_run(
                          conflict_run, (same_id_other_writer, n2), (d1, d2)),
                      "append-only 不得覆盖", TST.StorageConflictError)
        # 失败批次必须整批回滚：run 行不得半途落库。
        check(TST.load_follow_up_run(conflict_run.follow_up_run_id) is None
              and _count(db, "topic_follow_up_need") == 2,
              "冲突批次整批回滚（不留下半个 run）")

        same_needs_other_trace = _run((n1, n2), (d1, d2), trace_refs=("trace-fu-2",))
        check(same_needs_other_trace.follow_up_run_id == run.follow_up_run_id,
              "对照 run：need 集与规则版本相同 ⇒ 身份相同（trace 不同）")
        expect_raises("同 run 身份不同 trace 再追加",
                      lambda: TST.commit_follow_up_run(same_needs_other_trace, (n1, n2),
                                                       (d1, d2)),
                      "append-only 不得覆盖", TST.StorageConflictError)

        for table, id_col in (("topic_follow_up_need", "need_id"),
                              ("topic_follow_up_decision", "decision_id"),
                              ("topic_follow_up_run", "follow_up_run_id")):
            expect_raises(f"{table} UPDATE 被拒",
                          lambda t=table: _write(db, f"UPDATE {t} SET created_at='x'"),
                          "immutable (UPDATE forbidden)", sqlite3.IntegrityError)
            expect_raises(f"{table} DELETE 被拒",
                          lambda t=table: _write(db, f"DELETE FROM {t}"),
                          "immutable (DELETE forbidden)", sqlite3.IntegrityError)
            check(_count(db, table) > 0, f"{table} 被拒写入后行数不变")

        before = _payload(db, "topic_follow_up_need", "need_id", n1.need_id)

        # ------------------------------------------------------------------
        # 4. 批次内聚：跨批 need、幽灵新 Pack 都 fail-closed。
        # ------------------------------------------------------------------
        n3 = _need("需要补充关联交易材料", aspect_id="a3")
        d_foreign = _rejected(n3)
        run_foreign = _run((n1,), (d_foreign,))
        expect_raises("decision 指向不属于本批次的 need",
                      lambda: TST.commit_follow_up_run(run_foreign, (n1,), (d_foreign,)),
                      "不属于本批次", TST.TopicStoreValidationError)

        ghost = _executed(n1, "pack_ghost")
        run_ghost = _run((n1,), (ghost,), new_pack_ids=("pack_ghost",))
        expect_raises("已执行裁决自报不存在的新 Pack",
                      lambda: TST.commit_follow_up_run(run_ghost, (n1,), (ghost,)),
                      "不存在", TST.TopicStoreValidationError)
        check(TST.load_follow_up_run(run_ghost.follow_up_run_id) is None,
              "自报新 Pack 的批次不落库（新 Pack 必须真的 append 出来）")

        # ------------------------------------------------------------------
        # 5. 后续批次追加：旧行逐字不变。
        # ------------------------------------------------------------------
        later = _need("需要补充控股股东材料", aspect_id="a4")
        run2 = _run((later,), (_rejected(later, reason="aspect_unresolved"),))
        TST.commit_follow_up_run(run2, (later,), (_rejected(later, reason="aspect_unresolved"),))
        check(_count(db, "topic_follow_up_need") == 3
              and _count(db, "topic_follow_up_run") == 2
              and _count(db, "topic_follow_up_decision") == 3,
              "同一 draft revision 的新批次只追加新行")
        check(_payload(db, "topic_follow_up_need", "need_id", n1.need_id) == before,
              "旧 need 行逐字不变（「已裁决」不靠改历史行表达）")
        check(tuple(n.need_id for n in TST.load_follow_up_needs(_RUN_ID))
              == (n1.need_id, n2.need_id, later.need_id),
              "按 run_id 读回时新旧批次 need 都在（append-only 历史）")

        # ------------------------------------------------------------------
        # 6. 三表不进 Pack 读路径。
        # ------------------------------------------------------------------
        check(not (set(TST._FOLLOW_UP_TABLES) & set(TST._CHILD_TABLES)),
              "follow-up 三表不是 Pack 的 _CHILD_TABLES（不混进 Pack 读写路径）")
        check(not (set(TST._FOLLOW_UP_TABLES) & set(TST._SUCCESSOR_TABLES)),
              "follow-up 三表与 Pack v6 子表分属两个集合")
        check(set(TS.TopicResearchPack.__dataclass_fields__)
              & set(TST._FOLLOW_UP_TABLES) == set()
              and not any("follow_up" in f
                          for f in TS.TopicResearchPack.__dataclass_fields__),
              "TopicResearchPack 没有任何字段引用 follow-up"
              "（need/decision/run 不是 Pack 的成员）")

        # 版本兼容读取口径：run 行按自身版本键还原，不冒充 Pack 版本。
        round_trip = TST.load_follow_up_run(run2.follow_up_run_id)
        check(round_trip is not None
              and round_trip.schema_version == TS.FOLLOW_UP_RUN_SCHEMA_VERSION
              and round_trip.follow_up_run_id == run2.follow_up_run_id,
              "run 行按 furun-1 自身版本键还原")
        check(dataclasses.is_dataclass(TS.FollowUpNeed)
              and dataclasses.is_dataclass(TS.FollowUpDecision),
              "need / decision 的 wire 对象是不可变 dataclass（真库里的 payload 由其 to_dict 定义）")
        check(json.loads(_payload(db, "topic_follow_up_decision", "decision_id",
                                  d1.decision_id)) == d1.to_dict(),
              "decision 行的 payload 即该对象的 to_dict（无第二套 wire）")

    return _results


def _write(db: Path, sql: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

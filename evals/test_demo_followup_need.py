"""Eval: `FollowUpNeed` / `FollowUpDecision` / `FollowUpRun` 的独立身份（§16.10 #23/#24/#35，M930-3A）。

用法: python -m evals.test_demo_followup_need

本模块的 3A 部分证明（纯类型/DDL 层，不建库、不联网、不调 LLM）：

 1. #23：`FollowUpNeed` **不是** `SectionDraft` 的字段，也不进其 `identity_body`；Writer 结果
    以 `draft + follow_up_needs` 并列返回，两者各自独立身份；
 2. #24：`FollowUpNeed` **不是** gap，也**不是** `SectionUnresolved`；三者不共享字段、不共享
    持久化边界、不得互相填充（need 无 gap_id/block_id/unresolved_id/reason_code）；
 3. #35（类型层）：「need 已裁决」只能靠追加 `FollowUpDecision` 表达；三张 follow-up 表
    append-only、各自独立主键，decision 的唯一外键指向 follow-up run（不是 topic_pack），
    任何一表都不进 Section Store v2 family；
 4. 三态裁决与批次基数：每条 need 恰一条 decision；accepted/reduced 必须已执行且绑定**新**
    Pack（append-only successor，不回写旧 Pack）；rejected 必须未执行、无 new_pack_id。

`run_follow_up_needs` 的完整运行时反例（target 解析、来源类、预算、scope 授权与真库追加）
需要真实 Contract/aspect fixture，按计划归 M930-3B 的 #41 行；本文件当前**不**断言那些行为。
`commit_follow_up_run` 的真库幂等/冲突路径归 `test_demo_followup_store.py`（3B）。
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_runtime as TR  # noqa: E402
from harness import topic_schema as TS  # noqa: E402
from harness import topic_store as TST  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import store as ST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str, exc_type=TS.SchemaValidationError) -> None:
    try:
        fn()
    except exc_type as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


_NEED_FIELDS = ("statement", "target_requirement_id", "topic_id", "question_id", "aspect_id",
                "section_id", "section_draft_revision", "expected_source_class", "writer_identity")
_AUTHORIZED_SCOPE = ("a1", "company_identity", "company_subject_match")


def _need(**kw) -> TS.FollowUpNeed:
    base = dict(
        statement="需要补充该 aspect 的权威来源材料",
        target_requirement_id="company::company_identity",
        topic_id="company_identity", question_id="company_subject_match", aspect_id="a1",
        section_id="company", section_draft_revision="rev-7",
        contract_authorized_scope=_AUTHORIZED_SCOPE,
        requiredness="required", expected_source_class="company_industry",
        budget_hint="", writer_identity="writer-1")
    base.update(kw)
    base["need_schema_version"] = kw.get("need_schema_version", TS.FOLLOW_UP_NEED_SCHEMA_VERSION)
    base["need_id"] = kw.get("need_id") or TS.derive_follow_up_need_id(
        base["statement"], base["target_requirement_id"], base["aspect_id"],
        base["section_id"], base["section_draft_revision"])
    return TS.FollowUpNeed(**base)


def _decision(need: TS.FollowUpNeed, **kw) -> TS.FollowUpDecision:
    base = dict(verdict="rejected", rules_version=TR.FOLLOW_UP_RULES_VERSION,
                authorized_requirement_id=None, resolved_topic_id=None,
                resolved_aspect_id=None, reason="target_requirement_unresolved",
                executed=False, new_pack_id=None)
    base.update(kw)
    base["need_id"] = kw.get("need_id", need.need_id)
    base["decision_id"] = kw.get("decision_id") or TS.derive_follow_up_decision_id(
        base["need_id"], base["verdict"], base["reason"], base["rules_version"])
    return TS.FollowUpDecision(**base)


def _run(**kw) -> TS.FollowUpRun:
    base = dict(run_id="run-1", task_id="task-1", section_id="company",
                section_draft_revision="rev-7", writer_identity="writer-1",
                rules_version=TR.FOLLOW_UP_RULES_VERSION, need_ids=("fun_a", "fun_b"),
                decision_ids=("fund_a", "fund_b"), new_pack_ids=(),
                trace_refs=("trace-1",), schema_version=TS.FOLLOW_UP_RUN_SCHEMA_VERSION)
    base.update(kw)
    base["follow_up_run_id"] = kw.get("follow_up_run_id") or TS.derive_follow_up_run_id(
        base["run_id"], base["need_ids"], base["rules_version"])
    return TS.FollowUpRun(**base)


def _usage() -> TS.TopicUsageSnapshot:
    return TS.TopicUsageSnapshot(
        budget_policy=TS.BudgetPolicySnapshot(
            schema_version="bp-1", canonical_hash=TS.sha256_canonical({"tier": "demo"}),
            tier="demo"),
        cumulative_usage=())


# ---------------------------------------------------------------------------
# 1. #23：need 是 draft 之外的独立身份
# ---------------------------------------------------------------------------

def _check_need_identity() -> None:
    need = _need()
    check(need.need_id.startswith("fun_")
          and TS.FollowUpNeed.from_dict(need.to_dict()).to_dict() == need.to_dict(),
          "FollowUpNeed 往返一致、身份带 fun_ 前缀")
    check(need.need_schema_version == TS.FOLLOW_UP_NEED_SCHEMA_VERSION == "fun-1",
          "need 版本键只失效自身（fun-1，不属于三套 identity 的任何一套）")
    for field in ("statement", "target_requirement_id", "aspect_id", "section_id",
                  "section_draft_revision"):
        other = _need(**{field: need.to_dict()[field] + "_x"})
        check(other.need_id != need.need_id, f"{field} 参与 need 身份")
    check(_need(requiredness="optional").need_id == need.need_id
          and _need(budget_hint="3 次外部检索").need_id == need.need_id,
          "requiredness / budget_hint 只影响优先级，不改变 need 身份"
          "（同一 ask 在同一 draft revision 上只有一个身份）")

    expect_raises("版本键不符",
                  lambda: _need(need_schema_version="fun-2"), "need_schema_version")
    for name in _NEED_FIELDS:
        expect_raises(f"{name} 为空",
                      lambda n=name: _need(**{n: ""}), f"FollowUpNeed.{name}")
    expect_raises("未登记 requiredness",
                  lambda: _need(requiredness="nice_to_have"), "requiredness")
    expect_raises("need_id 与派生值不符",
                  lambda: _need(need_id="fun_manual"), "与确定性派生值")
    check(_need(budget_hint="").budget_hint == "",
          "budget_hint 允许为空（它是提示，不是授权）")
    # 类型层只要求 `expected_source_class` 非空；来源类枚举由 runtime 裁决（3B 的 #41 行）。
    check(_need(expected_source_class="not_a_source_class").expected_source_class
          == "not_a_source_class",
          "来源类枚举不在类型层（由 runtime 按 SOURCE_CLASSES 裁决，见 #41）")

    # 执行结果/Pack/material 引用在类型层不可表达。
    check(set(TS.FOLLOW_UP_NEED_FORBIDDEN_KEYS)
          & set(TS.FollowUpNeed.__dataclass_fields__) == set(),
          "need 的任何合法字段都不与禁止键重名")
    for key in TS.FOLLOW_UP_NEED_FORBIDDEN_KEYS:
        expect_raises(f"读数注入 {key}",
                      lambda k=key: TS.FollowUpNeed.from_dict({**need.to_dict(), k: "x"}),
                      "未知字段")
    check(not ({"result", "execution_result", "outcome_refs", "pack_id", "pack_ref",
                "material_id", "material_ref"}
               & set(TS.FollowUpNeed.__dataclass_fields__)),
          "need 不含任何执行结果 / Pack / material 引用（那些是 Harness 裁决之后的产物）")


# ---------------------------------------------------------------------------
# 2. #23：need 不是 draft 的字段，也不进 draft 身份
# ---------------------------------------------------------------------------

def _check_need_is_not_draft_field() -> None:
    draft_fields = set(NS.SectionDraft.__dataclass_fields__)
    check(TS.FollowUpNeed.__module__ == "harness.topic_schema"
          and NS.SectionDraft.__module__ == "sections.narrative_schema",
          "need 与 draft 分属两侧模块（Writer 侧 sidecar 不反向持有研究侧 run 实体）")
    check(not {f for f in draft_fields if "follow_up" in f or "need" in f},
          "#23：SectionDraft 没有任何 follow_up/need 字段")
    check(draft_fields & set(TS.FollowUpNeed.__dataclass_fields__) == {"section_id"},
          "#23：draft 与 need 的字段交集只有 section_id 这一**定位键**，没有归属边"
          "（draft 不持有 need，need 不持有 draft）")
    check("draft_id" not in TS.FollowUpNeed.__dataclass_fields__
          and "section_result_id" not in TS.FollowUpNeed.__dataclass_fields__,
          "need 不反向引用 draft / Result（只按 section_id + section_draft_revision 定位）")
    check(not ({"follow_up_need_ids", "need_ids", "needs"} & draft_fields),
          "draft 不为需要补件的 ask 预留字段：Writer 结果以 draft + needs 并列返回")

    # draft 身份由 identity_body 定义；它必须对 need 一无所知。
    body_src = inspect.getsource(NS.SectionDraft.identity_body)
    check("follow_up" not in body_src and "need_id" not in body_src
          and "need_schema_version" not in body_src,
          "#23：SectionDraft.identity_body 不含任何 need 键（need 变化不得改变 draft 身份）")
    check(not {"claim_ids", "support_ref_ids", "paragraph_ids"}
          & set(TS.FollowUpNeed.__dataclass_fields__),
          "need 不携带 draft 的产物身份（它只描述「还缺什么」）")


# ---------------------------------------------------------------------------
# 3. #24：need 不是 gap，也不是 SectionUnresolved
# ---------------------------------------------------------------------------

def _check_need_is_not_gap() -> None:
    need_fields = set(TS.FollowUpNeed.__dataclass_fields__)
    check(TS.FollowUpNeed is not TS.ContractGap
          and TS.FollowUpNeed is not SS.SectionUnresolved
          and not issubclass(TS.FollowUpNeed, TS.ContractGap)
          and not issubclass(TS.FollowUpNeed, SS.SectionUnresolved),
          "need 与 gap / SectionUnresolved 是互不继承的三套身份")
    check(not ({"gap_id", "block_id", "unresolved_id", "reason_code", "impact_scopes",
                "unmet_required_items", "required_unmet_proof", "contract_sha256"}
               & need_fields),
          "#24：need 没有 gap/block/unresolved 身份字段（不得被三者中任何一个冒充）")
    contract_gap_fields = set(TS.ContractGap.__dataclass_fields__)
    unresolved_fields = set(SS.SectionUnresolved.__dataclass_fields__)
    check(not ({"need_id", "requiredness", "expected_source_class", "budget_hint",
                "writer_identity", "section_draft_revision",
                "target_requirement_id"} & (contract_gap_fields | unresolved_fields)),
          "#24：gap / SectionUnresolved 也没有 need 的字段（不得互相填充）")
    # need 并不因为「未取得材料」就自动成为缺口：它的语义是「请再取」，不是「已判定缺」。
    expect_raises("need 读数注入 gap_id",
                  lambda: TS.FollowUpNeed.from_dict({**_need().to_dict(), "gap_id": "cgap_1"}),
                  "未知字段")
    check("verdict" not in need_fields and "executed" not in need_fields,
          "need 本身不携带裁决（「是否执行/是否达标」只能由追加的 FollowUpDecision 表达）")


def _check_follow_up_persistence_boundary() -> None:
    check(set(TST._FOLLOW_UP_TABLES)
          == {"topic_follow_up_need", "topic_follow_up_decision", "topic_follow_up_run"},
          "边界③固定为三张 follow-up 表")
    check(set(TST._FOLLOW_UP_TABLES) <= set(TST._IMMUTABLE_TABLES),
          "#35：三张 follow-up 表全部 append-only（受不可变触发器保护）")
    check(not (set(TST._FOLLOW_UP_TABLES) & set(TST._SUCCESSOR_TABLES))
          and not (set(TST._FOLLOW_UP_TABLES) & set(TST._IMMUTABLE_TABLES_V2)),
          "follow-up 表既不是 Pack 子表、也不与包内表混编")
    check(not (set(TST._FOLLOW_UP_TABLES) & set(ST.V2_SECTION_TABLES)),
          "#35：follow-up 三表不进 Section Store 的 v2 family")

    ddl = "\n".join(TST._ddl_statements())
    need_ddl = [s for s in TST._ddl_statements() if "topic_follow_up_need" in s
                and "CREATE TABLE" in s]
    check(len(need_ddl) == 1 and "PRIMARY KEY" in need_ddl[0]
          and "need_id TEXT PRIMARY KEY" in need_ddl[0] and "REFERENCES" not in need_ddl[0],
          "need 行以 need_id 为独立主键、无外键（不挂在 Pack 行上）")
    decision_ddl = [s for s in TST._ddl_statements() if "topic_follow_up_decision" in s
                    and "CREATE TABLE" in s]
    check(len(decision_ddl) == 1
          and "REFERENCES topic_follow_up_run(follow_up_run_id)" in decision_ddl[0],
          "decision 的唯一外键指向 follow-up run（不指向 topic_pack）")
    check("topic_pack" not in "".join(need_ddl + decision_ddl),
          "#35：follow-up 建表语句里没有 topic_pack 依赖（不回写旧 Pack）")
    for table in TST._FOLLOW_UP_TABLES:
        check(f"TRIGGER IF NOT EXISTS trg_{table}_no_update" in ddl
              and f"TRIGGER IF NOT EXISTS trg_{table}_no_delete" in ddl,
              f"{table} 有 no_update / no_delete 触发器")


# ---------------------------------------------------------------------------
# 4. 三态裁决：accepted/reduced 必须真的执行并产出新 Pack
# ---------------------------------------------------------------------------

def _check_decision_gate() -> None:
    need = _need()
    rejected = _decision(need)
    check(rejected.decision_id.startswith("fund_")
          and TS.FollowUpDecision.from_dict(rejected.to_dict()).to_dict()
          == rejected.to_dict(),
          "FollowUpDecision 往返一致、身份带 fund_ 前缀")
    check(rejected.verdict == "rejected" and not rejected.executed
          and rejected.new_pack_id is None,
          "rejected 裁决未执行、无新 Pack（不联网、不产出）")
    accepted = _decision(need, verdict="accepted", reason="accepted_topic_path_b",
                         executed=True, new_pack_id="pack_new_1")
    check(accepted.decision_id != rejected.decision_id
          and accepted.new_pack_id == "pack_new_1",
          "accepted 裁决绑定**新** Pack identity（append-only successor）")
    reduced = _decision(need, verdict="reduced", reason="reduced_scope_partial",
                        executed=True, new_pack_id="pack_new_2")
    check(reduced.verdict == "reduced" and reduced.executed,
          "reduced 是「已执行但取得的材料少于 ask」，不是 rejection 也不是 gap")
    check(len({rejected.decision_id, accepted.decision_id, reduced.decision_id}) == 3,
          "同一条 need 的三种裁决是三行不同身份（不得覆盖 need 行）")

    expect_raises("accepted 未标记 executed",
                  lambda: _decision(need, verdict="accepted", reason="reason_a",
                                    executed=False, new_pack_id="pack_x"),
                  "必须标记 executed")
    expect_raises("accepted 无新 Pack",
                  lambda: _decision(need, verdict="accepted", reason="reason_a",
                                    executed=True, new_pack_id=None),
                  "必须绑定新 Pack identity")
    expect_raises("reduced 无新 Pack",
                  lambda: _decision(need, verdict="reduced", reason="reason_r",
                                    executed=True, new_pack_id=None),
                  "必须绑定新 Pack identity")
    expect_raises("rejected 标记 executed",
                  lambda: _decision(need, verdict="rejected", reason="reason_j",
                                    executed=True),
                  "不得标记 executed")
    expect_raises("rejected 携带 new_pack_id",
                  lambda: _decision(need, verdict="rejected", reason="reason_j",
                                    new_pack_id="pack_z"),
                  "不得携带 new_pack_id")
    expect_raises("未登记 verdict",
                  lambda: _decision(need, verdict="deferred", reason="reason_d"),
                  "verdict")
    expect_raises("空 reason",
                  lambda: _decision(need, reason=""), "reason 必须非空")
    expect_raises("空 rules_version",
                  lambda: _decision(need, rules_version=""), "rules_version 必须非空")
    expect_raises("decision_id 与派生值不符",
                  lambda: _decision(need, decision_id="fund_manual"), "与确定性派生值")
    check(_decision(need, reason="another_reason").decision_id != rejected.decision_id,
          "reason 参与裁决身份（同一 need 的两种理由不得折叠成一行）")
    check(_decision(need, rules_version="fud-0", decision_id=None).decision_id
          != rejected.decision_id,
          "裁决口径版本参与身份：规则版本升级不覆盖旧裁决行")
    check("proof" not in TS.FollowUpDecision.__dataclass_fields__
          and "gap_id" not in TS.FollowUpDecision.__dataclass_fields__
          and "block_id" not in TS.FollowUpDecision.__dataclass_fields__,
          "裁决 wire 里没有 proof/gap/block 字段（typed 理由码不得退化成自由文本）")


# ---------------------------------------------------------------------------
# 5. run 行：同一批次一个身份，append-only、不回写
# ---------------------------------------------------------------------------

def _check_run_gate() -> None:
    run = _run()
    check(run.schema_version == TS.FOLLOW_UP_RUN_SCHEMA_VERSION == "furun-1"
          and run.follow_up_run_id.startswith("fur_")
          and TS.FollowUpRun.from_dict(run.to_dict()).to_dict() == run.to_dict(),
          "FollowUpRun 往返一致、版本 furun-1、身份带 furun_ 前缀")
    check(_run().follow_up_run_id == run.follow_up_run_id,
          "同一 (run_id, need 集, 规则版本) ⇒ 同一 run 身份（重放同一批次是幂等追加）")
    check(_run(need_ids=("fun_a", "fun_c"), decision_ids=("fund_a", "fund_c")).follow_up_run_id
          != run.follow_up_run_id,
          "批次不同 ⇒ run 身份不同")
    check(_run(rules_version="fud-0").follow_up_run_id != run.follow_up_run_id,
          "规则版本不同 ⇒ run 身份不同")
    fields = set(TS.FollowUpRun.__dataclass_fields__)
    check("new_pack_ids" in fields
          and not ({"pack_id", "current_pack_id", "replaced_pack_id", "old_pack_id"} & fields),
          "run 只以 new_pack_ids（追加集合）引用新 Pack，没有「当前包/被替换包」这类回写字段")

    expect_raises("空 need_ids",
                  lambda: _run(need_ids=(), decision_ids=()), "need_ids 不得为空")
    expect_raises("need_ids 重复",
                  lambda: _run(need_ids=("fun_a", "fun_a"), decision_ids=("fund_a", "fund_b")),
                  "不得重复")
    expect_raises("decision 数少于 need 数",
                  lambda: _run(decision_ids=("fund_a",)), "恰有一条 decision")
    expect_raises("decision 数多于 need 数",
                  lambda: _run(decision_ids=("fund_a", "fund_b", "fund_c")), "恰有一条 decision")
    expect_raises("new_pack_ids 多于 need 数",
                  lambda: _run(new_pack_ids=("p1", "p2", "p3")), "至多一个新 Pack")
    expect_raises("new_pack_ids 重复",
                  lambda: _run(new_pack_ids=("p1", "p1")), "不得重复")
    expect_raises("版本键不符",
                  lambda: _run(schema_version="furun-2"), "schema_version")
    expect_raises("run 身份与派生值不符",
                  lambda: _run(follow_up_run_id="furun_manual"), "与确定性派生值")


# ---------------------------------------------------------------------------
# 6. 批次基数：每条 need 恰一条裁决
# ---------------------------------------------------------------------------

def _check_execution_result_cardinality() -> None:
    n1, n2 = _need(), _need(aspect_id="a2")
    d1 = _decision(n1)
    d2 = _decision(n2, reason="cross_section_target")
    ok = TR.FollowUpExecutionResult(needs=(n1, n2), decisions=(d1, d2), new_pack_ids=(),
                                    trace_refs=(), usage=_usage())
    check(ok.rejected_need_ids() == (n1.need_id, n2.need_id)
          and ok.accepted_need_ids() == () and ok.reduced_need_ids() == (),
          "批次结果按裁决分类，rejected 不产出 Pack")
    with_pack = TR.FollowUpExecutionResult(
        needs=(n1, n2),
        decisions=(_decision(n1, verdict="accepted", reason="accepted_a", executed=True,
                             new_pack_id="pack_n1"), d2),
        new_pack_ids=("pack_n1",), trace_refs=(), usage=_usage())
    check(with_pack.accepted_need_ids() == (n1.need_id,)
          and with_pack.new_pack_ids == ("pack_n1",),
          "已执行裁决与新 Pack id 一一对应")

    expect_raises("缺一条裁决",
                  lambda: TR.FollowUpExecutionResult(needs=(n1, n2), decisions=(d1,),
                                                     new_pack_ids=(), trace_refs=(),
                                                     usage=_usage()),
                  "恰给一条 FollowUpDecision", TR.TopicRuntimeError)
    expect_raises("多一条额外裁决",
                  lambda: TR.FollowUpExecutionResult(needs=(n1,), decisions=(d1, d2),
                                                     new_pack_ids=(), trace_refs=(),
                                                     usage=_usage()),
                  "恰给一条 FollowUpDecision", TR.TopicRuntimeError)
    expect_raises("输入 need 重复",
                  lambda: TR.FollowUpExecutionResult(needs=(n1, n1), decisions=(d1,),
                                                     new_pack_ids=(), trace_refs=(),
                                                     usage=_usage()),
                  "输入 need 重复", TR.TopicRuntimeError)
    expect_raises("new_pack_ids 与已执行裁决不符",
                  lambda: TR.FollowUpExecutionResult(
                      needs=(n1,), decisions=(_decision(n1, verdict="accepted",
                                                        reason="accepted_a", executed=True,
                                                        new_pack_id="pack_n1"),),
                      new_pack_ids=("pack_other",), trace_refs=(), usage=_usage()),
                  "必须与已执行裁决一一对应", TR.TopicRuntimeError)


# ---------------------------------------------------------------------------
# 7. runtime 侧 typed 拒绝码（#41 的纯函数部分）
# ---------------------------------------------------------------------------

#: 封闭拒绝码集合的**逐条**期望值。用「集合相等」而不是「数量相等」断言：多一条少一条都必须
#: 在这里显式出现（数量相等挡不住「加一条也删一条」）。加码只能随获批的机制批次一同加，并在此
#: 留下依据——`successor_base_missing` / `successor_base_incomplete` 是 §三 F 后继装配的前置门：
#: 没有可继承的**完整** current Pack 时，follow-up 不得退化成「只提交被重研的那一条 aspect」。
_EXPECTED_REJECTION_CODES = (
    "target_requirement_unresolved", "cross_section_target", "cross_topic_target",
    "question_not_in_requirement", "aspect_unresolved", "aspect_ambiguous",
    "question_mismatch", "identity_mismatch", "scope_not_authorized",
    "unknown_source_class", "source_policy_unresolved", "budget_exhausted",
    "successor_base_missing", "successor_base_incomplete",
)


def _check_typed_rejection_codes() -> None:
    codes = TR.FOLLOW_UP_REJECTION_REASONS
    check(len(set(codes)) == len(codes) and all(isinstance(c, str) and c for c in codes)
          and set(codes) == set(_EXPECTED_REJECTION_CODES) and sorted(codes) == sorted(
              _EXPECTED_REJECTION_CODES),
          "follow-up 拒绝码逐条等于登记的封闭集合（互不重复的 typed 字符串，不是自由文本）："
          f"多={sorted(set(codes) - set(_EXPECTED_REJECTION_CODES))}，"
          f"少={sorted(set(_EXPECTED_REJECTION_CODES) - set(codes))}")
    need = _need()
    for code in codes:
        decision = TR._reject_follow_up(need, code, "proof-text", None, None)
        check(decision.verdict == "rejected" and not decision.executed
              and decision.new_pack_id is None and decision.reason == code
              and decision.decision_id == TS.derive_follow_up_decision_id(
                  need.need_id, "rejected", code, TR.FOLLOW_UP_RULES_VERSION),
              f"typed 拒绝码 {code} 产出未执行裁决，且沿用唯一派生口径")
    check("proof-text" not in json.dumps(TR._reject_follow_up(need, codes[0], "proof-text",
                                                             None, None).to_dict(),
                                         ensure_ascii=False),
          "拒绝证明文本不进裁决 wire（它只作 trace 依据，不冒充 typed 理由码）")
    expect_raises("未登记的拒绝码",
                  lambda: TR._reject_follow_up(need, "writer_says_no", "p", None, None),
                  "未登记的 follow-up 拒绝码", TR.TopicRuntimeError)
    check(sorted(codes) == sorted(TR.FOLLOW_UP_REJECTION_REASONS),
          "runtime 导出的拒绝码集合即登记集合（无第二套口径）")


def _check_writer_outlet() -> None:
    """M930-3B：Writer 产出的 need 必须**直接**落在上面这套研究侧身份上（两模块同一 wire）。

    这是 #23/#24 的端到端一侧：前面的断言证明「类型上不共享字段」，这里证明「真实 Writer
    出口产出的 need，就是研究侧 runtime 能直接裁决的那一个对象」——不经过任何再包装、
    不放宽任何字段，且 Writer 不因此获得任何回写 Pack 的能力。
    """
    from evals import test_demo_pack_writer as FIXT          # 同批夹具，避免两套替身漂移
    from sections import pack_writer as PW

    spec = FIXT.WS.load_writing_spec(FIXT.SPEC_PATH)
    profile = FIXT.PP.load_presentation_profile(FIXT.PROFILE_PATH)
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    task = FIXT._task("company", (FIXT.TOPIC_BUSINESS,), task_id="task-fun-outlet")
    authority = FIXT._company_authority(
        task,
        facts=(FIXT._fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                          (FIXT.ASP_BUSINESS_MAIN,)),),
        aspects=(FIXT._AspectResult(FIXT.ASP_BUSINESS_MAIN, "covered"),
                 FIXT._AspectResult(FIXT.ASP_BUSINESS_SALES, "partial")),
        requirements=(FIXT._Req(FIXT.TOPIC_BUSINESS, (
            FIXT._aspect(FIXT.ASP_BUSINESS_MAIN, FIXT.TOPIC_BUSINESS, "q-company_business"),
            FIXT._aspect(FIXT.ASP_BUSINESS_SALES, FIXT.TOPIC_BUSINESS,
                         "q-company_business", status="partial"),)),))
    scan = PW.scan_topic_pack(authority, task)
    req_id = scan.requirement_ids[FIXT.TOPIC_BUSINESS]
    authority_input_id = authority.input_id
    fu_spec = FIXT._follow_up(
        "需要补充公司销售模式的口径说明。", target_requirement_id=req_id,
        topic_id=FIXT.TOPIC_BUSINESS, question_id="q-company_business",
        aspect_id=FIXT.ASP_BUSINESS_SALES)

    def _write_once(*, with_need: bool = True) -> object:
        return PW.write_section(
            task, authority, projection=projection, writing_spec=spec,
            presentation_profile=profile,
            llm_client=FIXT._StubLlm(FIXT._plan(
                candidates=[FIXT._cand("c1", scan.facts[0].text,
                                       FIXT._fact_edge(scan, "f-1"))],
                follow_ups=[fu_spec] if with_need else [])),
            dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT,
            # §三 A：topic 权威的写作必须带 `wmctx-1` 材料正文上下文（缺它一律 fail-closed）。
            material_context=FIXT._writer_material_context(
                authority.pack_set, task_id=str(task.task_id), section_id="company"))

    outcome = _write_once()
    without_need = _write_once(with_need=False)
    check(len(outcome.follow_up_needs) == 1
          and isinstance(outcome.follow_up_needs[0], TS.FollowUpNeed),
          "Writer 出口产出的就是研究侧 `FollowUpNeed` 本体（不另包一层）")
    need = outcome.follow_up_needs[0]
    check(need.need_schema_version == TS.FOLLOW_UP_NEED_SCHEMA_VERSION,
          f"need 必须带登记 schema 版本（实际 {need.need_schema_version!r}）")
    check(need.section_id == outcome.draft.section_id
          and need.section_draft_revision == outcome.draft.draft_revision,
          "need 必须由写入侧**自己**定位到本次 draft revision（不由申请文本指定）")
    check(need.writer_identity == outcome.prompt_version
          and need.writer_identity == PW.NARRATION_PROMPT_VERSION,
          f"need 的 writer_identity 必须取自登记的 prompt 资产版本（实际 "
          f"{need.writer_identity!r}）")
    check("writer_identity" not in PW._FOLLOW_UP_KEYS,
          "writer_identity 不在模型可写的申请词表里（不得自报身份）")
    # 无回写：申请不得改变权威输入，也不得改变 draft 身份之外的任何东西。
    check(authority.input_id == authority_input_id,
          "补件申请不得回写权威输入（Pack 侧身份必须逐字不变）")
    # `pw-19`：写入边界不再接受空白 `budget_hint`（检索侧对空白字段 fail-closed，空串不是
    # 「没有偏好」而是「这条诉求注定执行不了」）。这里断言的是它**逐字带过**申请时写的提示，
    # 且**不是**授权面——`need.budget_hint` 只影响优先级，授权仍由研究侧的裁决与预算门决定。
    check(need.writer_identity != "" and need.budget_hint == fu_spec["budget_hint"]
          and need.budget_hint.strip() != "",
          f"budget_hint 必须逐字带过申请里写的提示（实际 {need.budget_hint!r}），"
          "且不得为空白；它只是提示，不是已授权的预算")
    # 同一权威 + 同一候选集 + 同一申请 → 逐字相同的 need 身份（确定性，可重放）。
    again = _write_once()
    check(again.follow_up_needs[0].need_id == need.need_id
          and again.draft.draft_id == outcome.draft.draft_id,
          "同一输入下 need_id 与 draft_id 都必须逐字可重放")
    # need 不是缺口：它既不在 outcome.unresolved，也不在 draft 的缺口投影里。
    check(need.need_id not in {p["unresolved_id"]
                               for p in outcome.draft.unresolved_projections}
          and need.need_id not in {u.unresolved_id for u in outcome.unresolved},
          "need 不得被塞进 draft 的缺口投影或 SectionUnresolved（「请再取」≠「已判定缺」）")
    check(all(getattr(u, "reason_code", "") != "follow_up_required"
              and not hasattr(u, "need_id") for u in outcome.unresolved),
          "Writer 出口的申请不得被自动改写成 SectionUnresolved（三套身份不得互填）")
    # 申请的有无不得改变缺口集合：申请不是缺口，缺口也不是申请的替代品。
    check(outcome.draft.unresolved_projections
          == without_need.draft.unresolved_projections
          and {u.unresolved_id for u in outcome.unresolved}
          == {u.unresolved_id for u in without_need.unresolved},
          "补件申请的有无不得改变缺口集合（申请不冒充缺口，也不抵消缺口）")
    check(not outcome.follow_up_needs == without_need.follow_up_needs,
          "反例不是恒等：带申请的产出与不带的产出必须在 need 上不同")
    # 两模块同一 wire：研究侧的裁决门必须能直接吃这一条 need。
    decision = _decision(need)
    check(decision.need_id == need.need_id
          and decision.decision_id == TS.derive_follow_up_decision_id(
              need.need_id, decision.verdict, decision.reason, decision.rules_version),
          "研究侧裁决门必须能直接裁决 Writer 产出的 need（同一身份，不重新派生）")
    # 反例：把 need 改挂到别的 draft revision 上 —— 身份派生必须随之改变（不得复用旧身份）。
    other = _need(section_draft_revision="rev-other")
    check(other.need_id != _need().need_id,
          "draft revision 变化必须改变 need 身份（申请绑定在具体 revision 上）")
    # 反例：申请词表不得出现任何检索/载荷表达面。
    check(not ({"query", "url", "payload_ref", "material_id", "fact_id", "locator_ref"}
               & set(PW._FOLLOW_UP_KEYS)),
          "补件申请不得携带检索/载荷/坐标字段（它只描述「还缺什么」，不指定去哪取）")


def main() -> dict:
    _check_need_identity()
    _check_need_is_not_draft_field()
    _check_need_is_not_gap()
    _check_follow_up_persistence_boundary()
    _check_decision_gate()
    _check_run_gate()
    _check_execution_result_cardinality()
    _check_typed_rejection_codes()
    _check_writer_outlet()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

# -*- coding: utf-8 -*-
"""Eval: M930-4 独立审查 / Assurance 的线格式（`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §7.3）。

用法: python -X utf8 -m evals.test_demo_review_schema

本模块只证明**线格式**这一层的行为，不触及硬门、Reviewer 调用与 Controller 聚合：那三样各有
自己的模块。这里要钉死的是 §7.7 退出门里属于本层、且一旦写错就再也没法在别处补救的几条：

 1. `ReviewIssue.category` 是封闭词表，且 `unsupported` / `clarity` 被拒绝时**必须给出可执行
    的替代**——否则 Reviewer 只会换个写法再猜一次；
 2. `supported` 是「被审核单元的正向结果」，不是 PASS：它带严重度或 blocking 在构造期即非法；
 3. Reviewer 的**放行/判定/改写正文**输出在这里是未登记字段，一律抛错（§7.5/§7.7）；
 4. `HardGateIssue` 的两个布尔**正交**，且「两个都不阻断」的记录不构成硬门失败；
 5. `AssuranceResult` 里**没有** `formal_closure` / `run_id` 的位置：治理状态不可能被这一层
     顺手写出来；
 6. 四个状态轴彼此独立，`system_release_eligible` **不随** `human_review_state` 变化；
 7. 聚合确定性：乱序输入得到同一 id（id 元组在构造期即被要求有序唯一）。

全部为纯内存断言，不联网、不调 LLM、不读写任何 run 产物。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assurance import schema as AS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str) -> None:
    try:
        fn()
    except AS.AssuranceSchemaError as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:110]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 AssuranceSchemaError，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


_RV = "rv_" + "0" * 24
_RV2 = "rv_" + "1" * 24


def _unit(kind: str = "claim", uid: str = "c1") -> AS.ReviewUnitRef:
    return AS.ReviewUnitRef.create(unit_kind=kind, unit_id=uid)


def _issue(**kw) -> AS.ReviewIssue:
    base = dict(report_version=_RV, unit_ref=_unit(), category="supported",
                severity="none", blocking=False, reason="该 Claim 与其引用一致")
    base.update(kw)
    return AS.ReviewIssue.create(**base)


def _gate_issue(**kw) -> AS.HardGateIssue:
    base = dict(gate_kind="required_gap_retention", reviewability_blocking=False,
                release_blocking=True, field="gap_index.required",
                declared="3", observed="1", reason="Contract 必需缺口未被保留")
    base.update(kw)
    return AS.HardGateIssue.create(**base)


def _bundle(**kw):
    base = dict(report_version=_RV, report_id="rep_1",
                unit_inventory=(_unit("claim", "c1"), _unit("section", "s1")),
                model_policy_id="mp_1",
                allowed_content_fingerprint=AS.sha256_text("preview"))
    base.update(kw)
    return AS.ReviewInputBundle.create(**base)


def _assurance(**kw):
    base = dict(report_version=_RV, hard_gate_result_id="hgr_" + "a" * 24,
                reviewer_run_record_id="rrr_" + "b" * 24, issue_ids=(),
                blocking_issue_count=0, process_state="flow_complete",
                preview_state="draft_previewable",
                system_review_state="system_review_passed_awaiting_human",
                human_review_state="human_not_reviewed",
                system_release_eligible=True)
    base.update(kw)
    return AS.AssuranceResult.create(**base)


# ---------------------------------------------------------------------------
# 1. category 封闭词表与可执行替代
# ---------------------------------------------------------------------------

def _check_category_vocabulary() -> None:
    check(AS.REVIEW_CATEGORIES == ("supported", "contradicted", "insufficient",
                                   "missing_content"),
          "category 词表与上位设计逐项一致（§7.3）")

    expect_raises("category='unsupported' 被拒绝且给出可执行替代",
                  lambda: _issue(category="unsupported", severity="high"),
                  "insufficient")
    expect_raises("category='clarity' 被拒绝且说明归 Section Evaluator",
                  lambda: _issue(category="clarity", severity="low"),
                  "Section Evaluator")
    expect_raises("未知 category 被拒绝",
                  lambda: _issue(category="probably_fine", severity="low"),
                  "必须属于")

    # 「换个写法猜一次」也必须失败：常见同义词全部有登记替代。
    for alias in ("not_supported", "unclear", "missing", "contradiction"):
        expect_raises(f"category 同义写法 {alias!r} 被拒绝",
                      lambda a=alias: _issue(category=a, severity="low"),
                      "非法取值")

    # §7.7：Reviewer 输出 pass/decision 等被拒绝（在 category 之外的字段层）。
    for name, value in (("decision", "PASS"), ("verdict", "pass"), ("pass", True),
                        ("ready", True), ("publish", True), ("publishable", True)):
        d = _issue().to_dict()
        d[name] = value
        expect_raises(f"ReviewIssue 拒绝 Reviewer 越权字段 {name!r}",
                      lambda dd=d: AS.ReviewIssue.from_dict(dd), "被禁止字段")

    for name in ("rewritten_text", "revised_text", "suggested_text", "new_text",
                 "final_text", "rewrite"):
        d = _issue().to_dict()
        d[name] = "改写后的正文"
        expect_raises(f"ReviewIssue 拒绝正文改写字段 {name!r}",
                      lambda dd=d: AS.ReviewIssue.from_dict(dd), "被禁止字段")

    check(not (set(AS.REVIEWER_FORBIDDEN_FIELDS) & set(AS.REVIEW_CATEGORIES)),
          "禁列字段名与合法 category 不相交（否则合法值会被禁列误杀）")
    check("unsupported" not in AS.REVIEW_CATEGORIES and "clarity" not in AS.REVIEW_CATEGORIES,
          "unsupported / clarity 不在合法词表内")


# ---------------------------------------------------------------------------
# 2. supported 是正向结果，不是 PASS
# ---------------------------------------------------------------------------

def _check_supported_truth_table() -> None:
    ok = _issue()
    check(ok.category == "supported" and ok.severity == "none" and not ok.blocking,
          "supported 可构造，且为 severity=none / blocking=False")

    expect_raises("supported 不得带严重度",
                  lambda: _issue(category="supported", severity="high"),
                  "severity 必须为 'none'")
    expect_raises("supported 不得 blocking",
                  lambda: _issue(category="supported", blocking=True),
                  "不得 blocking=True")
    expect_raises("supported 不得带 suggested_target",
                  lambda: _issue(suggested_target=AS.SuggestedTarget.create(
                      target_kind="section", target_ref="s1")),
                  "不得带 suggested_target")

    expect_raises("非 supported 必须给出严重度",
                  lambda: _issue(category="insufficient", severity="none"),
                  "必须给出 severity")
    for cat in ("contradicted", "insufficient", "missing_content"):
        expect_raises(f"{cat} 的 severity 词表受约束",
                      lambda c=cat: _issue(category=c, severity="catastrophic"),
                      "severity 必须属于")

    expect_raises("contradicted 必须给出反证出处",
                  lambda: _issue(category="contradicted", severity="high"),
                  "必须给出 evidence_refs")
    good = _issue(category="contradicted", severity="high", blocking=True,
                  evidence_refs=("cit_9",), reason="引用原文与 Claim 数字相反")
    check(good.category == "contradicted" and good.evidence_refs == ("cit_9",),
          "contradicted 带出处可构造")

    expect_raises("missing_content 必须给出 suggested_target",
                  lambda: _issue(category="missing_content", severity="medium"),
                  "必须给出 suggested_target")
    mc = _issue(category="missing_content", severity="medium", blocking=True,
                suggested_target=AS.SuggestedTarget.create(
                    target_kind="contract_aspect", target_ref="a7"),
                reason="该单元应有内容，但材料中未取得")
    check(mc.suggested_target.target_kind == "contract_aspect",
          "missing_content 的 suggested_target 指向 aspect 而非正文")

    # suggested_target 只承载位置，不承载正文。
    check(not ({"text", "content", "body", "rewritten_text"}
               & set(AS.SuggestedTarget.__dataclass_fields__)),
          "SuggestedTarget 没有承载正文的字段")
    expect_raises("SuggestedTarget 的 target_kind 受封闭词表约束",
                  lambda: AS.SuggestedTarget.create(target_kind="paragraph_text",
                                                    target_ref="p1"),
                  "必须属于")

    expect_raises("reason 非空",
                  lambda: _issue(reason=""),
                  "不得为空")
    expect_raises("reason 不得是长文",
                  lambda: _issue(reason="x" * (AS.MAX_REASON_CHARS + 1)),
                  "简短")


# ---------------------------------------------------------------------------
# 3. 身份：内容寻址、拒绝 unknown、篡改可检
# ---------------------------------------------------------------------------

def _check_identity_and_unknown() -> None:
    a, b = _issue(), _issue()
    check(a.issue_id == b.issue_id, "同内容得同 issue_id")
    other = _issue(reason="另一条理由")
    check(other.issue_id != a.issue_id, "改一个字节得新 issue_id")
    check(a.issue_id.startswith("rvi_") and len(a.issue_id) == 28,
          "issue_id 形如 rvi_ + 24 hex")

    d = a.to_dict()
    check(AS.ReviewIssue.from_dict(d).to_dict() == d, "ReviewIssue to_dict/from_dict 往返稳定")

    d2 = dict(d)
    d2["issue_id"] = "rvi_" + "f" * 24
    expect_raises("伪造 issue_id 被检出", lambda: AS.ReviewIssue.from_dict(d2), "与内容不符")

    # 篡改身份字段（不触发真值表的前置校验）后 id 必须不再自洽。
    d3 = dict(d)
    d3["report_version"] = _RV2
    expect_raises("篡改 report_version 后 id 不再自洽",
                  lambda: AS.ReviewIssue.from_dict(d3), "与内容不符")
    d3b = dict(d)
    d3b["reason"] = "另一条理由"
    expect_raises("篡改 reason 后 id 不再自洽",
                  lambda: AS.ReviewIssue.from_dict(d3b), "与内容不符")

    d4 = dict(d)
    d4["totally_new_field"] = 1
    expect_raises("未知字段被拒绝（拒绝 unknown 是 wire 契约）",
                  lambda: AS.ReviewIssue.from_dict(d4), "未登记字段")

    # unit_ref 词表与形状
    expect_raises("unit_kind 受 §7.3 词表约束",
                  lambda: AS.ReviewUnitRef.create(unit_kind="footnote", unit_id="f1"),
                  "必须属于")
    check(AS.ReviewUnitRef.create(unit_kind="row", unit_id="r1").key == "row:r1",
          "unit_ref 的规范键是 kind:id")

    # HardGateIssue 往返 + 篡改
    gi = _gate_issue()
    gd = gi.to_dict()
    check(AS.HardGateIssue.from_dict(gd).to_dict() == gd, "HardGateIssue 往返稳定")
    gd2 = dict(gd)
    gd2["reviewability_blocking"] = True
    expect_raises("篡改硬门布尔后 id 不再自洽",
                  lambda: AS.HardGateIssue.from_dict(gd2), "与内容不符")


# ---------------------------------------------------------------------------
# 4. HardGateIssue 两个正交布尔
# ---------------------------------------------------------------------------

def _check_hard_gate_booleans() -> None:
    rv_only = _gate_issue(gate_kind="artifact_index_integrity", reviewability_blocking=True,
                          release_blocking=True, field="index.sha256",
                          declared="a" * 8, observed="b" * 8,
                          reason="artifact index 与产物不符")
    rel_only = _gate_issue()
    check(rv_only.reviewability_blocking and rel_only.release_blocking,
          "两个布尔可分别取值")
    check(AS.HardGateIssue.create(
        gate_kind="cross_section_exact_conflict", reviewability_blocking=True,
        release_blocking=False, field="conflicts", declared="0", observed="1",
        reason="两节对同一事实给出互斥取值").reviewability_blocking,
        "可存在「只阻断可审查性」的硬门失败")

    expect_raises("两个布尔皆 False 不构成硬门失败",
                  lambda: _gate_issue(reviewability_blocking=False, release_blocking=False),
                  "至少阻断一个轴")
    expect_raises("布尔必须是真布尔",
                  lambda: _gate_issue(release_blocking=1),
                  "必须是布尔值")

    res = AS.HardGateResult.create(report_version=_RV, issues=(rel_only, rv_only))
    check(res.reviewability_blocking_count == 1 and res.release_blocking_count == 2,
          "计数由 issues 派生")
    check(res.reviewability_blocked and res.release_blocked, "两个阻断谓词分别可用")

    empty = AS.HardGateResult.create(report_version=_RV)
    check(empty.issues == () and not empty.reviewability_blocked and not empty.release_blocked,
          "无硬门失败时两个谓词均为 False")

    rd = res.to_dict()
    check(AS.HardGateResult.from_dict(rd).to_dict() == rd, "HardGateResult 往返稳定")
    rd2 = dict(rd)
    rd2["release_blocking_count"] = 0
    expect_raises("篡改声明计数被重算检出",
                  lambda: AS.HardGateResult.from_dict(rd2), "与 issues 重算不符")

    expect_raises("gate_rules_version 必须为当前规则版本",
                  lambda: AS.HardGateResult(
                      schema_version=AS.HARD_GATE_RESULT_SCHEMA_VERSION,
                      result_id="hgr_" + "0" * 24, report_version=_RV,
                      gate_rules_version="ahg-0", issues=(),
                      reviewability_blocking_count=0, release_blocking_count=0),
                  "gate_rules_version")

    # 乱序输入 → 同一 result_id（聚合确定性在线级保证）
    shuffled = AS.HardGateResult.create(report_version=_RV, issues=(rv_only, rel_only))
    check(shuffled.result_id == res.result_id, "issues 乱序不改变 result_id")


# ---------------------------------------------------------------------------
# 5. ReviewInputBundle：预定义单元集 + 隔离声明
# ---------------------------------------------------------------------------

def _check_input_bundle() -> None:
    b = _bundle()
    check(b.unit_keys() == ("claim:c1", "section:s1"), "单元集按规范键升序")
    declared = set(b.excluded_context)
    check(declared >= set(AS.REQUIRED_EXCLUDED_CONTEXT),
          "bundle 默认声明全部必须排除的上下文（Writer 历史/提示词/自评/期望结论/人工 verdict）")
    check(len(AS.REQUIRED_EXCLUDED_CONTEXT) == 5,
          "必须排除的上下文恰为 §7.5 的五类")

    expect_raises("未声明排除 Writer 自评即拒绝",
                  lambda: _bundle(excluded_context=("writer_prompt",)),
                  "未声明必须排除的上下文")
    expect_raises("排除声明不能只写一个泛化词",
                  lambda: _bundle(excluded_context=("everything_irrelevant",)),
                  "未声明必须排除的上下文")

    expect_raises("单元集不得为空",
                  lambda: _bundle(unit_inventory=()),
                  "必须是非空元组")
    expect_raises("单元集不得重复",
                  lambda: _bundle(unit_inventory=(_unit(), _unit())),
                  "含重复单元")
    # 构造器**归一**（内部排序），线格式**严格**（读回时乱序即拒）——分工是刻意的。
    unsorted_payload = dict(b.to_dict())
    unsorted_payload["unit_inventory"] = [_unit("section", "s1").to_dict(),
                                          _unit("claim", "c1").to_dict()]
    expect_raises("线格式读回时单元集必须按规范键有序",
                  lambda: AS.ReviewInputBundle.from_dict(unsorted_payload),
                  "必须按单元键升序")
    built = _bundle(unit_inventory=(_unit("section", "s1"), _unit("claim", "c1")))
    check(built.unit_keys() == ("claim:c1", "section:s1"),
          "构造器把乱序单元集归一为规范顺序（同一 bundle_id）")

    ex = AS.ReviewExcerpt.create(unit_ref=_unit("claim", "c1"), text="原文片段",
                                 source_locator="p12#L3", citation_id="cit_1")
    expect_raises("excerpt 不得引用单元集之外的单元",
                  lambda: _bundle(excerpts=(AS.ReviewExcerpt.create(
                      unit_ref=_unit("claim", "c9"), text="t",
                      source_locator="p1#L1", citation_id="cit_1"),)),
                  "审核单元集之外")
    ok = _bundle(excerpts=(ex,))
    check(ok.excerpts[0].citation_id == "cit_1", "excerpt 可绑定已解析出处")

    # ReviewExcerpt 不含生成过程痕迹字段
    check(not ({"writer_history", "writer_prompt", "self_evaluation", "hidden_history"}
               & set(AS.ReviewExcerpt.__dataclass_fields__)),
          "ReviewExcerpt 没有生成过程痕迹字段（§7.5 隔离在线形状上）")

    expect_raises("content fingerprint 必须是 sha256 hex",
                  lambda: _bundle(allowed_content_fingerprint="not-a-hash"),
                  "sha256 hex")

    bd = ok.to_dict()
    check(AS.ReviewInputBundle.from_dict(bd).to_dict() == bd, "ReviewInputBundle 往返稳定")
    expect_raises("未知字段被拒绝",
                  lambda: AS.ReviewInputBundle.from_dict({**bd, "extra": 1}),
                  "未登记字段")

    shuffled = _bundle(unit_inventory=(_unit("section", "s1"), _unit("claim", "c1")),
                       excerpts=(ex,))
    check(shuffled.bundle_id == ok.bundle_id, "单元集乱序不改变 bundle_id")
    check(_bundle(allowed_content_fingerprint=AS.sha256_text("preview!")).bundle_id
          != ok.bundle_id,
          "允许内容改一字即换 bundle_id")


# ---------------------------------------------------------------------------
# 6. ReviewerRunRecord：一次调用，不得重试刷过
# ---------------------------------------------------------------------------

def _check_reviewer_run_record() -> None:
    rec = AS.ReviewerRunRecord.create(
        report_version=_RV, bundle_id="rib_" + "0" * 24,
        prompt_version=AS.INDEPENDENT_REVIEWER_PROMPT_VERSION, model_policy_id="mp_1",
        outcome="reviewed", covered_unit_keys=("claim:c1", "section:s1"),
        issue_ids=("rvi_" + "0" * 24,), response_fingerprint=AS.sha256_text("{}"))
    check(rec.called and rec.llm_call_count == 1, "reviewed 记录恰一次调用")

    expect_raises("reviewed 必须恰一次调用",
                  lambda: AS.ReviewerRunRecord(
                      schema_version=AS.REVIEWER_RUN_RECORD_SCHEMA_VERSION,
                      record_id="rrr_" + "0" * 24, report_version=_RV,
                      bundle_id="rib_" + "0" * 24, prompt_version="p@1",
                      model_policy_id="mp_1", outcome="reviewed", llm_call_count=2,
                      covered_unit_keys=(), issue_ids=(), response_fingerprint="a" * 64),
                  "llm_call_count 必须为 1")

    skip = AS.ReviewerRunRecord.create(
        report_version=_RV, bundle_id="", prompt_version=AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
        model_policy_id="mp_1", outcome="skipped_reviewability_blocked")
    check(not skip.called and skip.llm_call_count == 0 and skip.response_fingerprint == "",
          "可审查性被阻断时记录为零调用")

    expect_raises("未调用不得携带响应指纹",
                  lambda: AS.ReviewerRunRecord.create(
                      report_version=_RV, bundle_id="", prompt_version="p@1",
                      model_policy_id="mp_1", outcome="failed",
                      response_fingerprint="a" * 64),
                  "必须为空串")
    expect_raises("已调用必须携带响应指纹",
                  lambda: AS.ReviewerRunRecord.create(
                      report_version=_RV, bundle_id="rib_" + "0" * 24, prompt_version="p@1",
                      model_policy_id="mp_1", outcome="reviewed"),
                  "sha256 hex")
    expect_raises("outcome 受封闭词表约束",
                  lambda: AS.ReviewerRunRecord.create(
                      report_version=_RV, bundle_id="", prompt_version="p@1",
                      model_policy_id="mp_1", outcome="retried_until_pass"),
                  "必须属于")

    rdict = rec.to_dict()
    check(AS.ReviewerRunRecord.from_dict(rdict).to_dict() == rdict,
          "ReviewerRunRecord 往返稳定")


# ---------------------------------------------------------------------------
# 7. AssuranceResult：四个状态轴 + 无治理字段
# ---------------------------------------------------------------------------

def _check_assurance_result() -> None:
    ok = _assurance()
    check(ok.system_release_eligible and ok.human_review_state == "human_not_reviewed",
          "系统可放行与人工未复核可同时成立（最高自动状态止于 awaiting_human）")

    # human 轴不参与放行判定：只改 human 轴，其余逐字节相同 ⇒ 放行结论不变。
    a = _assurance(human_review_state="human_not_reviewed")
    b = _assurance(human_review_state="human_reviewed")
    check(a.system_release_eligible == b.system_release_eligible,
          "system_release_eligible 不随 human_review_state 变化")
    check(a.assurance_id != b.assurance_id,
          "human 轴仍然进自身内容身份（它不是被忽略的字段）")

    blocked = _assurance(reviewer_run_record_id="", issue_ids=(),
                         system_review_state="system_review_not_run",
                         system_release_eligible=False)
    check(blocked.reviewer_run_record_id == "" and not blocked.system_release_eligible,
          "可审查性被阻断：零 Reviewer 记录 + 不可放行")

    expect_raises("未运行却绑定 reviewer 记录即拒绝",
                  lambda: _assurance(system_review_state="system_review_not_run"),
                  "不得绑定 reviewer_run_record_id")
    expect_raises("运行过却不绑定 reviewer 记录即拒绝",
                  lambda: _assurance(reviewer_run_record_id=""),
                  "reviewer_run_record_id")
    expect_raises("未通过系统审核却可放行是矛盾状态",
                  lambda: _assurance(system_review_state="system_review_not_passed"),
                  "system_review_not_passed")
    expect_raises("系统审核通过蕴含无 blocking issue",
                  lambda: _assurance(issue_ids=("rvi_" + "0" * 24,), blocking_issue_count=1),
                  "不得存在 blocking issue")
    expect_raises("流程未完成不得可放行",
                  lambda: _assurance(process_state="flow_incomplete"),
                  "流程未完成")
    expect_raises("blocking 计数不得超过 issue 总数",
                  lambda: _assurance(issue_ids=(), blocking_issue_count=1),
                  "超过 issue_ids 数")
    expect_raises("聚合规则版本必须为当前版本",
                  lambda: AS.AssuranceResult(
                      schema_version=AS.ASSURANCE_RESULT_SCHEMA_VERSION,
                      assurance_id="asr_" + "0" * 24, report_version=_RV,
                      hard_gate_result_id="hgr_" + "a" * 24,
                      reviewer_run_record_id="rrr_" + "b" * 24, issue_ids=(),
                      blocking_issue_count=0, process_state="flow_complete",
                      preview_state="draft_previewable",
                      system_review_state="system_review_passed_awaiting_human",
                      human_review_state="human_not_reviewed",
                      system_release_eligible=True, aggregate_rules_version="aag-0"),
                  "aggregate_rules_version")

    d = ok.to_dict()
    check(AS.AssuranceResult.from_dict(d).to_dict() == d, "AssuranceResult 往返稳定")
    expect_raises("未知字段被拒绝",
                  lambda: AS.AssuranceResult.from_dict({**d, "extra": 1}), "未登记字段")

    # §7.7：Controller 不能产生 formal closure / human acceptance / run 归属。
    for name in AS.ASSURANCE_FORBIDDEN_FIELDS:
        expect_raises(f"AssuranceResult 拒绝治理字段 {name!r}",
                      lambda n=name: AS.AssuranceResult.from_dict({**d, n: "anything"}),
                      "被禁止字段")
    check(not (set(AS.ASSURANCE_FORBIDDEN_FIELDS)
               & set(AS.AssuranceResult.__dataclass_fields__)),
          "禁列字段名与 AssuranceResult 实际字段不相交")
    check("run_id" not in AS.AssuranceResult.__dataclass_fields__,
          "AssuranceResult 不含 run_id：外部锚只能是 report_version")

    # 改正文（report_version 变）⇒ 旧结论不可复用，因为身份就不一样了。
    check(_assurance(report_version=_RV2).assurance_id != ok.assurance_id,
          "换 report_version 即换 assurance_id（旧结果不可复用）")

    expect_raises("process_state 词表封闭",
                  lambda: _assurance(process_state="finished"), "process_state")
    expect_raises("preview_state 词表封闭",
                  lambda: _assurance(preview_state="published"), "preview_state")
    expect_raises("system_review_state 词表封闭",
                  lambda: _assurance(system_review_state="system_review_ok"),
                  "system_review_state")
    expect_raises("human_review_state 词表封闭",
                  lambda: _assurance(human_review_state="user_said_ok"), "human_review_state")


# ---------------------------------------------------------------------------
# 8. 第三套身份：本包不注册进 TS4/TS5 版本双射
# ---------------------------------------------------------------------------

def _check_identity_separation() -> None:
    src = (Path(__file__).resolve().parent.parent
           / "document_structure" / "versions.py").read_text(encoding="utf-8")
    low = src.lower()
    check("assurance" not in low,
          "document_structure/versions.py 不登记 assurance（那是 TS4/TS5 的版本双射轴）")
    for const in (AS.ASSURANCE_SCHEMA_VERSION, AS.REVIEW_ISSUE_SCHEMA_VERSION,
                  AS.REVIEW_INPUT_BUNDLE_SCHEMA_VERSION, AS.HARD_GATE_RESULT_SCHEMA_VERSION,
                  AS.REVIEWER_RUN_RECORD_SCHEMA_VERSION, AS.ASSURANCE_RESULT_SCHEMA_VERSION):
        check(const not in src, f"版本常量 {const!r} 不出现在 TS4/TS5 版本表里")

    # 本包自持全部版本常量（与 `sections/` 同风格），不依赖外部注册。
    for name in ("ASSURANCE_SCHEMA_VERSION", "REVIEW_ISSUE_SCHEMA_VERSION",
                 "REVIEW_INPUT_BUNDLE_SCHEMA_VERSION", "HARD_GATE_RESULT_SCHEMA_VERSION",
                 "REVIEWER_RUN_RECORD_SCHEMA_VERSION", "ASSURANCE_RESULT_SCHEMA_VERSION",
                 "HARD_GATE_RULES_VERSION", "ASSURANCE_AGGREGATE_RULES_VERSION"):
        check(isinstance(getattr(AS, name), str) and getattr(AS, name) != "",
              f"assurance.schema 自持版本常量 {name}")

    # 硬门种类与 §7.4 十一项一一对应
    check(len(AS.HARD_GATE_KINDS) == 11 and len(set(AS.HARD_GATE_KINDS)) == 11,
          "HARD_GATE_KINDS 是 11 项且无重复")
    check(AS.REVIEW_UNIT_KINDS == ("section", "paragraph", "table", "row", "claim",
                                   "citation", "locator"),
          "REVIEW_UNIT_KINDS 与 §7.3 的引用轴逐项一致")


def main() -> dict:
    _check_category_vocabulary()
    _check_supported_truth_table()
    _check_identity_and_unknown()
    _check_hard_gate_booleans()
    _check_input_bundle()
    _check_reviewer_run_record()
    _check_assurance_result()
    _check_identity_separation()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

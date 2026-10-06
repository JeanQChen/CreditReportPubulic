"""Eval: Section 侧结构校验器的 split 语义（§16.10 #16/#20/#25，M930-3A）。

用法: python -m evals.test_section_validator

证明（纯结构，无 I/O、无 LLM）：

 1. current `claim-2`（`validate_claim`）逐项 fail-closed：缺 candidate id/revision、factual
    缺 `accepted_binding_ids`、binding 重复、marker 非 current、topic/question/文本/影响面
    不合规、无依据的 fact/calculation；
 2. legacy `claim-1`（`validate_legacy_claim`）**不放宽**共有项：claim-1 声称自己老，不等于
    可以无依据、无 topic、自带 recommendation（P7 拆分不得造成 fail-open 回归）；
 3. 两种 wire 互不冒充：current validator 拒绝 legacy 对象，legacy validator 拒绝 current 对象；
 4. current `result-2`（`validate_section_result`）：缺 `section_draft_id`、内嵌 `evaluation`
    （post-gate 身份成环）、claim 的 section_id 与 result 不一致、claim_id / citation_id /
    unresolved_id 重复、legacy claim 混进 current Result，全部报错；
 5. legacy Result validator 与 current Result validator 分工不重叠，且**共用**同一套成员约束。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import schema as CS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import validator as V  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def has(errors: list[str], needle: str) -> bool:
    return any(needle in e for e in errors)


def _schema_rejects(fn) -> bool:
    try:
        fn()
    except SS.SectionSchemaError:
        return True
    return False


def _ref(**kw) -> SS.CitationRef:
    base = {"ref_type": "evidence", "evidence_id": "ev_1", "page_number": 12}
    base.update(kw)
    return SS.CitationRef(**base)


def _claim(**kw) -> SS.SectionClaim:
    body = {
        "section_id": "company", "topic_id": "company_identity",
        "question_ids": ("company_subject_match",), "text": "主体一致",
        "claim_type": "fact", "citation_refs": (_ref(),),
        "claim_candidate_id": "ccand_1", "claim_candidate_revision": "dr-1",
        "accepted_binding_ids": ("asb_1",),
    }
    body.update(kw)
    body["claim_id"] = SS.derive_claim_id(
        body["claim_type"], body["topic_id"], body["question_ids"], body["text"],
        body["citation_refs"], body["claim_candidate_id"],
        body["claim_candidate_revision"], body["accepted_binding_ids"])
    body["schema_version"] = SS.CLAIM_SCHEMA_VERSION
    return SS.SectionClaim(**body)


def _legacy_claim(**kw) -> SS.LegacySectionClaimV1:
    body = {
        "claim_id": "c_legacy_1", "section_id": "company", "topic_id": "company_identity",
        "question_ids": ("company_subject_match",), "text": "主体一致",
        "claim_type": "fact", "citation_refs": (_ref(),),
    }
    body.update(kw)
    return SS.LegacySectionClaimV1(**body)


def _injected(obj, **kw):
    """把一个已经过 schema 构造的合法对象改坏（用于测试 validator 的第二道门）。

    `claim_candidate_id` / `accepted_binding_ids` 等项在 `sections/schema.py` 构造时即被拒，
    故无法直接构造出坏对象；validator 对它们的检查是**结构上不可达的纵深防御**，
    只能用注入方式验证它确实仍在判。
    """
    for key, value in kw.items():
        object.__setattr__(obj, key, value)
    return obj


def _current_result(**kw) -> SS.SectionResult:
    body = {
        "section_result_id": "sr_cur", "schema_version": SS.SECTION_RESULT_SCHEMA_VERSION,
        "section_version": "secver_cur", "section_draft_id": "sdraft_cur",
        "task_id": "task_cur", "section_id": "company", "status": "COMPLETED",
    }
    body.update(kw)
    return SS.SectionResult(**body)


# ---------------------------------------------------------------------------
# 1. current claim-2
# ---------------------------------------------------------------------------

def _check_current_claim() -> None:
    check(V.validate_claim(_claim()) == [],
          "合法 current claim-2 零错误（validator 不误报）")
    # 以下四项 schema 构造即拒（`_injected` 注入），validator 是第二道门。
    empty_candidate = _injected(_claim(), claim_candidate_id="")
    check(SS.SectionClaim is not None
          and _schema_rejects(lambda: _claim(claim_candidate_id=""))
          and has(V.validate_claim(empty_candidate), "claim_candidate_id 为空"),
          "缺 candidate id 被拒（schema 先拒 + validator 仍判：纵深防御）")
    empty_revision = _injected(_claim(), claim_candidate_revision="")
    check(_schema_rejects(lambda: _claim(claim_candidate_revision=""))
          and has(V.validate_claim(empty_revision), "claim_candidate_revision 为空"),
          "缺 candidate revision 被拒（schema 先拒 + validator 仍判）")
    no_binding = _injected(_claim(), accepted_binding_ids=())
    check(_schema_rejects(lambda: _claim(accepted_binding_ids=()))
          and has(V.validate_claim(no_binding), "factual claim 缺 factual accepted_binding_ids"),
          "factual claim 缺 accepted bindings 被拒（不得凭 candidate 身份冒充已接受）")
    dup_binding = _injected(_claim(), accepted_binding_ids=("asb_1", "asb_1"))
    check(_schema_rejects(lambda: _claim(accepted_binding_ids=("asb_1", "asb_1")))
          and has(V.validate_claim(dup_binding), "accepted_binding_ids 含重复"),
          "accepted bindings 重复被拒（schema 先拒 + validator 仍判）")
    check(SS.CLAIM_TYPES == ("fact", "calculation", "inference"),
          "claim_type 封闭为 fact/calculation/inference（陈述性 narrative 不是 Claim 类型）")
    inference = _claim(claim_type="inference", text="本期收入增长主要由主业驱动",
                       citation_refs=(), accepted_binding_ids=())
    check(V.validate_claim(inference) == [],
          "非 factual（inference）claim 既不要求 citation 也不要求 accepted bindings"
          "（基数要求只落在 fact/calculation 上）")

    # 命题 / 覆盖范围与事实性依据。
    check(has(V.validate_claim(_claim(text="   ")), "claim.text 为空"),
          "空命题被拒")
    check(has(V.validate_claim(_claim(text="建议授信", claim_type="recommendation")),
              "recommendation"),
          "recommendation 类型被拒（授信建议属 Phase 5）")
    check(has(V.validate_claim(_claim(topic_id="")), "claim.topic_id 为空"),
          "缺 topic 归属被拒")
    check(has(V.validate_claim(_claim(question_ids=())), "claim.question_ids 为空"),
          "缺 question 归属被拒")
    check(has(V.validate_claim(_claim(citation_refs=())), "既无 citation 也无 derived_from_claim_ids"),
          "fact 无任何可回查依据被拒")
    foreign_scope = _claim(impact_scope=("everything",))
    check(has(V.validate_claim(foreign_scope), "impact_scope 非法"),
          "未登记 impact_scope 被拒")
    bad_conf = _claim(confidence="absolute")
    check(has(V.validate_claim(bad_conf), "confidence 非法"),
          "未登记 confidence 被拒")
    bad_ref = _claim(citation_refs=(_ref(ref_type="recommendation"),))
    check(has(V.validate_claim(bad_ref), "ref_type 非法"),
          "非法 citation ref_type 被拒")


def _check_legacy_claim_not_relaxed() -> None:
    check(V.validate_legacy_claim(_legacy_claim()) == [],
          "合法 legacy claim-1 零错误")
    check(has(V.validate_legacy_claim(_legacy_claim(text="建议授信",
                                                    claim_type="recommendation")),
              "recommendation"),
          "legacy 链不放宽 claim_type 白名单（类型拆分 ≠ 校验放宽）")
    check(has(V.validate_legacy_claim(_legacy_claim(citation_refs=())),
              "既无 citation 也无 derived_from_claim_ids"),
          "legacy 链不放宽「必须可回查」")
    check(has(V.validate_legacy_claim(_legacy_claim(question_ids=())),
              "claim.question_ids 为空"),
          "legacy 链不放宽 topic/question 归属")
    check(not has(V.validate_legacy_claim(_legacy_claim()), "accepted_binding"),
          "claim-2 特有的 accepted binding 基数不适用于 claim-1")

    # 两条 wire 互不冒充。
    check(has(V.validate_claim(_legacy_claim()), "只接受 current SectionClaim"),
          "current claim validator 拒绝 legacy 对象")
    check(has(V.validate_legacy_claim(_claim()), "只接受 LegacySectionClaimV1"),
          "legacy claim validator 拒绝 current 对象")


# ---------------------------------------------------------------------------
# 2. current result-2
# ---------------------------------------------------------------------------

def _check_current_result() -> None:
    check(V.validate_section_result(_current_result()) == [],
          "合法 current Result 零错误")
    no_draft = _injected(_current_result(), section_draft_id="")
    check(_schema_rejects(lambda: _current_result(section_draft_id=""))
          and has(V.validate_section_result(no_draft), "section_draft_id 为空"),
          "缺 section_draft_id 的 current Result 被拒（必须单向引用 Draft）")
    check(has(V.validate_section_result(_current_result(status="MAYBE")),
              "section status 非法"),
          "未登记 section status 被拒")

    # 内嵌 evaluation：current SectionResult 类型上无该字段，validator 仍须拦住旧载体。
    embedded = _current_result()
    try:
        object.__setattr__(embedded, "evaluation", _legacy_claim())
        injected = True
    except Exception:  # noqa: BLE001
        injected = False
    check(injected and has(V.validate_section_result(embedded), "禁止内嵌 evaluation"),
          "内嵌 evaluation 的 current Result 被拒（post-gate 身份不得成环）")

    # 成员约束：归属、重复、wire 类型。
    wrong_section = _claim(section_id="industry")
    check(has(V.validate_section_result(_current_result(claims=(wrong_section,))),
              "section_id 与 result 不一致"),
          "claim 的 section_id 与 result 不一致被拒")
    dup_claim = _claim()
    dup_errors = V.validate_section_result(_current_result(claims=(dup_claim, dup_claim)))
    check(has(dup_errors, "claim_id 重复") and has(dup_errors, "citation_id 重复"),
          "同 Result 内 claim_id 重复被拒，且同一引用的 citation 身份重复也被点出")
    # citation_id 是「claim_id + 引用身份」的内容派生：跨 Result 同内容合法，同 Result 内才非法。
    check(SS.derive_citation_id(dup_claim.claim_id, _ref())
          == SS.derive_citation_id(dup_claim.claim_id, _ref()),
          "citation_id 由 (claim_id, 引用身份) 确定性派生（同内容幂等）")
    other_claim = _claim(text="命题二")
    check(SS.derive_citation_id(other_claim.claim_id, _ref())
          != SS.derive_citation_id(dup_claim.claim_id, _ref())
          and V.validate_section_result(_current_result(claims=(dup_claim, other_claim))) == [],
          "不同 claim 携带同一引用是合法的（citation 身份随 claim 归属，不误报重复）")
    check(has(V.validate_section_result(
        _current_result(claims=(_legacy_claim(),))), "wire 类型与本 validator 不匹配"),
          "legacy claim 混进 current Result 被拒")
    check(has(V.validate_section_result(
        _current_result(claims=(_injected(_claim(), accepted_binding_ids=()),))),
        "factual claim 缺 factual accepted_binding_ids"),
          "成员校验复用 current claim-2 校验（不是第二套弱口径）")

    dup_unresolved = SS.SectionUnresolved(
        unresolved_id="ur_1", section_id="company", topic_id="company_identity",
        question_id="company_subject_match", state="NOT_FOUND_AFTER_SEARCH",
        reason_code="no_evidence", detail="未检索到")
    check(has(V.validate_section_result(
        _current_result(unresolved=(dup_unresolved, dup_unresolved))),
        "unresolved_id 重复"),
          "同 Result 内 unresolved_id 重复被拒")
    bad_unresolved = SS.SectionUnresolved(
        unresolved_id="ur_2", section_id="company", topic_id="company_identity",
        question_id="company_subject_match", state="NOT_FOUND_AFTER_SEARCH",
        reason_code="no_evidence", detail="未检索到", impact_scope=("everything",))
    check(has(V.validate_section_result(_current_result(unresolved=(bad_unresolved,))),
              "unresolved.impact_scope 非法"),
          "unresolved 的 impact_scope 必须落在 IMPACT_SCOPES")
    check(CS.IMPACT_SCOPES, "IMPACT_SCOPES 非空（影响面校验基于冻结 Contract 枚举）")


def _check_legacy_result_split() -> None:
    legacy_result = SS.LegacySectionResultV1(
        section_result_id="sr_legacy", section_version="secver_legacy",
        task_id="task_legacy", section_id="company", status="COMPLETED",
        claims=(_legacy_claim(),), markdown="# 公司概况")
    check(V.validate_legacy_section_result(legacy_result) == [],
          "合法 legacy Result 零错误")
    check(has(V.validate_legacy_section_result(_current_result()),
              "只接受 LegacySectionResultV1"),
          "legacy Result validator 拒绝 current 对象")
    check(has(V.validate_section_result(legacy_result),
              "只接受 current SectionResult"),
          "current Result validator 拒绝 legacy 对象")
    # 共享成员约束：legacy 链同样拦重复 claim_id 与 wire 混装。
    dup = _legacy_claim()
    legacy_dup = SS.LegacySectionResultV1(
        section_result_id="sr_legacy", section_version="secver_legacy",
        task_id="task_legacy", section_id="company", status="COMPLETED",
        claims=(dup, dup), markdown="")
    check(has(V.validate_legacy_section_result(legacy_dup), "claim_id 重复"),
          "legacy Result 同样拦重复 claim_id（成员约束共用一套）")
    mixed = SS.LegacySectionResultV1(
        section_result_id="sr_legacy", section_version="secver_legacy",
        task_id="task_legacy", section_id="company", status="COMPLETED",
        claims=(_claim(),), markdown="")
    check(has(V.validate_legacy_section_result(mixed), "wire 类型与本 validator 不匹配"),
          "current claim 混进 legacy Result 被拒（双向不冒充）")


def _check_self_check() -> None:
    sc = V._self_check()
    check(all(sc.values()) and "recommendation_rejected" in sc and "valid_claim_passes" in sc,
          f"validator 自带 self-check 全绿：{sc}")
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = V.main(["--self-check"])
    check(code == 0 and "PASS" in buf.getvalue(),
          "CLI --self-check 退出码 0 且打印 PASS")


def main() -> dict:
    _check_current_claim()
    _check_legacy_claim_not_relaxed()
    _check_current_result()
    _check_legacy_result_split()
    _check_self_check()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

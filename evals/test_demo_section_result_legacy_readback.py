"""Eval: 旧形状 `SectionResult` 的 legacy 只读回放（§16.10 #25，M930-3A）。

用法: python -m evals.test_demo_section_result_legacy_readback

证明：
 1. 旧形状 Result 载荷只经 `load_legacy_section_result_for_audit` 还原为 `LegacySectionResultV1`；
 2. `section-result-2` current reader 逐条拒绝：缺 marker、marker 非 current、缺
    `section_draft_id`、内嵌 `evaluation`（post-gate 身份成环）；
 3. legacy reader **拒绝** current `section-result-2` 载荷（单向分流）；
 4. public legacy reader 返回的类型**不是** current `SectionResult`，调用方无法把它当
    result-2 交给 current 链；
 5. current `section_result_to_dict` 不得宽松序列化 legacy 对象。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from sections import schema as SS  # noqa: E402

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
    except SS.SectionSchemaError as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 SectionSchemaError，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


def _legacy_result() -> SS.LegacySectionResultV1:
    claim = SS.LegacySectionClaimV1(
        claim_id="c_legacy_1", section_id="company", topic_id="company_identity",
        question_ids=("company_subject_match",), text="主体一致",
        claim_type="fact",
        citation_refs=(CitationRef(ref_type="evidence", evidence_id="ev_1"),))
    return SS.LegacySectionResultV1(
        section_result_id="sr_legacy_1", section_version="secver_legacy_1",
        task_id="task_legacy_1", section_id="company", status="COMPLETED",
        claims=(claim,), markdown="# 公司概况")


def _current_result() -> SS.SectionResult:
    return SS.SectionResult(
        section_result_id="sr_cur", schema_version=SS.SECTION_RESULT_SCHEMA_VERSION,
        section_version="secver_cur", section_draft_id="sdraft_cur", task_id="task_cur",
        section_id="company", status="COMPLETED")


def main() -> dict:
    # ------------------------------------------------------------------
    # 1. 登记的 legacy 版本集与 current marker。
    # ------------------------------------------------------------------
    check(SS.SECTION_RESULT_SCHEMA_VERSION == "section-result-2"
          and SS.SECTION_RESULT_SCHEMA_VERSION not in SS.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS
          and "" in SS.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS,
          "current marker 为 section-result-2；登记 legacy 值含空（旧形状无 marker）")

    # ------------------------------------------------------------------
    # 2. 旧形状 Result 只读还原（含内嵌 evaluation 的旧产物）。
    # ------------------------------------------------------------------
    legacy = _legacy_result()
    doc = legacy.to_dict()
    back = SS.load_legacy_section_result_for_audit(doc)
    check(isinstance(back, SS.LegacySectionResultV1)
          and not isinstance(back, SS.SectionResult)
          and back.to_dict() == doc,
          "旧形状 Result 只读还原且往返一致，类型不是 current SectionResult")
    check(not hasattr(back, "schema_version") and not hasattr(back, "section_draft_id"),
          "legacy Result 视图既无 result-2 marker 也无 section_draft_id")

    ev = SS.SectionEvaluation(
        evaluation_id="eval_1", section_result_id="sr_legacy_1",
        rules_version="rv", evaluator_prompt_version="pv",
        rules_passed=True, llm_passed=True, decision="PASS",
        rework_targets=(), llm_evaluator_calls=1)
    with_eval = dataclasses.replace(legacy, evaluation=ev)
    check(SS.load_legacy_section_result_for_audit(with_eval.to_dict()).evaluation
          is not None,
          "内嵌 evaluation 的旧产物仍可只读回放（legacy 允许成环）")

    # ------------------------------------------------------------------
    # 3. current reader 的四条拒绝条件。
    # ------------------------------------------------------------------
    expect_raises("current reader 读无 marker 的旧载荷",
                  lambda: SS.section_result_from_dict(doc), "旧形状 Result")
    expect_raises("current reader 读 claim-1 形状之外的 marker",
                  lambda: SS.section_result_from_dict(
                      {**doc, "schema_version": "section-result-1"}),
                  "旧形状 Result")
    # 只留「缺 section_draft_id」这一个缺陷：legacy 载荷的 `evaluation` 键必须先摘掉，
    # 否则命中的是「内嵌 evaluation」那条拒绝条件，测不到 draft 缺失这一项。
    missing_draft = {k: v for k, v in {**doc,
                                       "schema_version": SS.SECTION_RESULT_SCHEMA_VERSION}.items()
                     if k not in ("section_draft_id", "evaluation")}
    expect_raises("current reader 读缺 section_draft_id 的 result-2",
                  lambda: SS.section_result_from_dict(missing_draft), "section_draft_id")
    embedded = {**doc, "schema_version": SS.SECTION_RESULT_SCHEMA_VERSION,
                "section_draft_id": "sdraft_cur", "evaluation": ev.to_dict()
                if hasattr(ev, "to_dict") else SS.evaluation_to_dict(ev)}
    expect_raises("current reader 读内嵌 evaluation 的 result-2",
                  lambda: SS.section_result_from_dict(embedded), "evaluation")
    expect_raises("current reader 读非对象载荷",
                  lambda: SS.section_result_from_dict(json.dumps(doc)), "必须是对象")

    # ------------------------------------------------------------------
    # 4. legacy reader 拒绝 current result-2 载荷（单向分流）。
    # ------------------------------------------------------------------
    current_doc = SS.section_result_to_dict(_current_result())
    expect_raises("legacy reader 读 current result-2 载荷",
                  lambda: SS.load_legacy_section_result_for_audit(current_doc),
                  "不得经 legacy reader 读回")
    expect_raises("legacy reader 读非对象载荷",
                  lambda: SS.load_legacy_section_result_for_audit(json.dumps(current_doc)),
                  "必须是对象")

    # ------------------------------------------------------------------
    # 5. current serializer 不宽松序列化 legacy 对象。
    # ------------------------------------------------------------------
    expect_raises("current section_result_to_dict 吃 legacy 对象",
                  lambda: SS.section_result_to_dict(legacy), "legacy")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

"""Phase 4 章节产物结构校验器（Batch A 结构层；业务覆盖由 P4-D Rules Evaluator 承担）。

不调 LLM / Embedding / Chroma / 网络。只做硬结构校验：类型白名单、必填字段、引用身份。
- fact/calculation claim 必须有 citation 或 derived_from_claim_ids（可回查）。
- claim_type 白名单拒绝 recommendation（授信建议属 Phase 5）。

CLI：python -m sections.validator --self-check
"""

from __future__ import annotations

import argparse
import sys

from contracts import schema as CS
from sections import schema as SS


def validate_citation_ref(ref) -> list[str]:
    errors: list[str] = []
    if ref.ref_type not in SS.CITATION_TYPES:
        errors.append(f"citation ref_type 非法: {ref.ref_type!r}")
        return errors
    if ref.ref_type == "evidence" and not ref.evidence_id:
        errors.append("evidence 引用缺少 evidence_id")
    if ref.ref_type == "structured":
        if not ref.snapshot_id:
            errors.append("structured 引用缺少 snapshot_id")
        if not ref.item_code and not ref.formula_id:
            errors.append("structured 引用既无 item_code 也无 formula_id")
    if ref.ref_type == "external" and not ref.source_snapshot_id:
        errors.append("external 引用缺少 source_snapshot_id")
    return errors


def _validate_claim_common(claim) -> list[str]:
    """legacy `claim-1` 与 current `claim-2` **共有**的结构检查。

    抽出来的理由是 P7 的类型拆分只应放宽 claim-2 特有的项（marker、candidate revision、
    accepted binding）；若 legacy 链因此另起一个更弱的校验器，就会顺带丢掉 claim_type
    白名单、可回查依据、引用身份这些**既有** fail-closed 检查 —— 那是 fail-open 回归。
    """
    errors: list[str] = []
    if claim.claim_type not in SS.CLAIM_TYPES:
        errors.append(f"claim_type 非法: {claim.claim_type!r}，允许 {SS.CLAIM_TYPES}"
                      f"（第一阶段不含 recommendation）")
    if not (claim.text or "").strip():
        errors.append("claim.text 为空")
    if not claim.topic_id:
        errors.append("claim.topic_id 为空")
    if not claim.question_ids:
        errors.append("claim.question_ids 为空")
    if claim.confidence not in SS.CONFIDENCE_LEVELS:
        errors.append(f"claim.confidence 非法: {claim.confidence!r}")
    for sc in claim.impact_scope:
        if sc not in CS.IMPACT_SCOPES:
            errors.append(f"claim.impact_scope 非法: {sc!r}")
    for ref in claim.citation_refs:
        errors.extend(validate_citation_ref(ref))
    # fact/calculation 必须有可回查依据（citation 或 derived_from_claim_ids）
    if (claim.claim_type in ("fact", "calculation")
            and not claim.citation_refs and not claim.derived_from_claim_ids):
        errors.append(f"{claim.claim_type} claim 既无 citation 也无 derived_from_claim_ids")
    return errors


def validate_claim(claim: SS.SectionClaim) -> list[str]:
    """current `claim-2` 校验：共有检查 + claim-2 特有的 candidate/binding 检查。"""
    if not isinstance(claim, SS.SectionClaim):
        return [f"current claim validator 只接受 current SectionClaim"
                f"（实际 {type(claim).__name__}）；claim-1 只经 validate_legacy_claim"]
    errors: list[str] = []
    if claim.schema_version != SS.CLAIM_SCHEMA_VERSION:
        errors.append(f"claim.schema_version 非 current {SS.CLAIM_SCHEMA_VERSION!r}: "
                      f"{claim.schema_version!r}")
    if not (claim.claim_candidate_id or "").strip():
        errors.append("claim.claim_candidate_id 为空（必须回指来源 candidate）")
    if not (claim.claim_candidate_revision or "").strip():
        errors.append("claim.claim_candidate_revision 为空（必须回指来源 candidate revision）")
    bindings = tuple(claim.accepted_binding_ids or ())
    if len(set(bindings)) != len(bindings):
        errors.append("claim.accepted_binding_ids 含重复")
    if claim.claim_type == "fact" and not bindings:
        errors.append("factual claim 缺 factual accepted_binding_ids")
    errors.extend(_validate_claim_common(claim))
    return errors


def validate_legacy_claim(claim: SS.LegacySectionClaimV1) -> list[str]:
    """`claim-1` 形状 Claim 的校验（**只有** legacy 链可调用）。

    只是**不放宽**共有项：claim-1 声称自己老，不等于它可以无依据、无 topic 或自带
    recommendation。claim-2 特有的 marker/revision/binding 项在此不适用。
    """
    if not isinstance(claim, SS.LegacySectionClaimV1):
        return [f"legacy claim validator 只接受 LegacySectionClaimV1"
                f"（实际 {type(claim).__name__}）；current claim-2 只经 validate_claim"]
    return _validate_claim_common(claim)


def validate_unresolved(u: SS.SectionUnresolved) -> list[str]:
    errors: list[str] = []
    if u.state not in CS.QUESTION_STATES:
        errors.append(f"unresolved.state 非法: {u.state!r}，允许 {CS.QUESTION_STATES}")
    if not u.reason_code:
        errors.append("unresolved.reason_code 为空")
    if not u.topic_id:
        errors.append("unresolved.topic_id 为空")
    for sc in u.impact_scope:
        if sc not in CS.IMPACT_SCOPES:
            errors.append(f"unresolved.impact_scope 非法: {sc!r}")
    for b in u.blocking_effects:
        if b not in CS.BLOCKING_LEVELS or b == "NONE":
            errors.append(f"unresolved.blocking_effects 非法: {b!r}")
    return errors


def validate_section_result(result: SS.SectionResult) -> list[str]:
    if not isinstance(result, SS.SectionResult):
        return [f"current Result validator 只接受 current SectionResult"
                f"（实际 {type(result).__name__}）；legacy 形状只经 validate_legacy_section_result"]
    errors: list[str] = []
    if result.schema_version != SS.SECTION_RESULT_SCHEMA_VERSION:
        errors.append(f"result.schema_version 非 current "
                      f"{SS.SECTION_RESULT_SCHEMA_VERSION!r}: {result.schema_version!r}")
    if not result.section_draft_id:
        errors.append("section_draft_id 为空（current Result 必须单向引用 Draft）")
    if getattr(result, "evaluation", None) is not None:
        errors.append("current Result 禁止内嵌 evaluation（post-gate 身份成环）")
    if result.status not in SS.SECTION_STATUSES:
        errors.append(f"section status 非法: {result.status!r}，允许 {SS.SECTION_STATUSES}")
    if not result.section_version:
        errors.append("section_version 为空")
    if not result.task_id:
        errors.append("task_id 为空")
    if not result.section_id:
        errors.append("section_id 为空")

    _validate_result_members(result, validate_claim, errors, expect_legacy=False)
    return errors


def _validate_result_members(result, claim_validator, errors: list[str], *,
                             expect_legacy: bool) -> None:
    """claim / unresolved 成员校验（legacy 与 current 共用同一套成员约束）。

    `claim_validator` 由调用方选定（`validate_claim` 或 `validate_legacy_claim`），
    使 legacy 链不会因为类型拆分而丢掉重复 claim_id、section_id 归属、重复 citation_id
    与 unresolved 校验 —— 这些都不依赖 claim-2 特有字段。`expect_legacy` 同时禁止两种
    wire 类型互相冒充（legacy claim 混进 current Result 或反之）。
    """
    seen_claim_ids: set[str] = set()
    seen_citation_ids: set[str] = set()
    for c in result.claims:
        if isinstance(c, SS.LegacySectionClaimV1) != expect_legacy:
            errors.append(f"claim {c.claim_id} 的 wire 类型与本 validator 不匹配"
                          f"（实际 {type(c).__name__}）")
            continue
        if c.claim_id in seen_claim_ids:
            errors.append(f"claim_id 重复: {c.claim_id}")
        seen_claim_ids.add(c.claim_id)
        if c.section_id != result.section_id:
            errors.append(f"claim {c.claim_id} 的 section_id 与 result 不一致")
        # 同一结果内 citation_id 重复检测（内容身份；跨 SectionResult 合法，由复合归属键承载）。
        for ref in c.citation_refs:
            cid = SS.derive_citation_id(c.claim_id, ref)
            if cid in seen_citation_ids:
                errors.append(f"citation_id 重复: {cid}")
            seen_citation_ids.add(cid)
        errors.extend(claim_validator(c))

    seen_unresolved_ids: set[str] = set()
    for u in result.unresolved:
        if u.unresolved_id in seen_unresolved_ids:
            errors.append(f"unresolved_id 重复: {u.unresolved_id}")
        seen_unresolved_ids.add(u.unresolved_id)
        errors.extend(validate_unresolved(u))


def validate_legacy_section_result(result: SS.LegacySectionResultV1) -> list[str]:
    """legacy 形状 Result 的校验（**只有** legacy 链可调用；current 链不得调用）。"""
    if not isinstance(result, SS.LegacySectionResultV1):
        return [f"legacy Result validator 只接受 LegacySectionResultV1"
                f"（实际 {type(result).__name__}）；current result-2 只经 validate_section_result"]
    errors: list[str] = []
    if result.status not in SS.SECTION_STATUSES:
        errors.append(f"section status 非法: {result.status!r}，允许 {SS.SECTION_STATUSES}")
    if not result.section_version:
        errors.append("section_version 为空")
    if not result.task_id:
        errors.append("task_id 为空")
    if not result.section_id:
        errors.append("section_id 为空")
    ev = result.evaluation
    if ev is not None:
        if ev.decision not in SS.EVALUATION_DECISIONS:
            errors.append(f"evaluation.decision 非法: {ev.decision!r}")
        if ev.section_result_id != result.section_result_id:
            errors.append("evaluation.section_result_id 与 result 不一致")
        for i in ev.issues:
            if not i.issue_id:
                errors.append("issue 缺 issue_id")
            if not i.rule_id:
                errors.append("issue 缺 rule_id")
        for rt in ev.rework_targets:
            if rt.target_kind not in ("question", "topic", "claim"):
                errors.append(f"rework_target.target_kind 非法: {rt.target_kind!r}")
    _validate_result_members(result, validate_legacy_claim, errors, expect_legacy=True)
    return errors


def _claim(**overrides) -> SS.SectionClaim:
    """self-check 的 current claim-2 样例构造（claim_id 由内容派生，身份自洽）。"""
    body = {
        "section_id": "company", "topic_id": "company_identity",
        "question_ids": ("company_subject_match",), "text": "主体一致",
        "claim_type": "fact",
        "citation_refs": (SS.CitationRef(ref_type="evidence", evidence_id="ev_1",
                                        page_number=12),),
        "claim_candidate_id": "ccand_selfcheck", "claim_candidate_revision": "dr-1",
        "accepted_binding_ids": ("asb_selfcheck",),
    }
    body.update(overrides)
    body["claim_id"] = SS.derive_claim_id(
        body["claim_type"], body["topic_id"], body["question_ids"], body["text"],
        body["citation_refs"], body["claim_candidate_id"],
        body["claim_candidate_revision"], body["accepted_binding_ids"])
    body["schema_version"] = SS.CLAIM_SCHEMA_VERSION
    return SS.SectionClaim(**body)


def _self_check() -> dict:
    """构造合法/非法样例，验证校验器行为（无 I/O）。"""
    bad_rec = SS.CitationRef(ref_type="recommendation")

    valid_claim = _claim()

    bad_type = _claim(text="建议授信", claim_type="recommendation")

    bad_rec_claim = _claim(text="某事实", citation_refs=(bad_rec,))

    fact_no_cite = _claim(text="无依据事实", citation_refs=())

    valid_unresolved = SS.SectionUnresolved(
        unresolved_id="ur_1", section_id="company", topic_id="company_identity",
        question_id="company_subject_match", state="NOT_FOUND_AFTER_SEARCH",
        reason_code="no_evidence", detail="未检索到")

    ok = validate_claim(valid_claim) == []
    got_bad_type = any("recommendation" in e for e in validate_claim(bad_type))
    got_bad_rec = any("ref_type" in e for e in validate_claim(bad_rec_claim))
    got_no_cite = any("无 citation" in e for e in validate_claim(fact_no_cite))
    ok_unresolved = validate_unresolved(valid_unresolved) == []

    # current reader 必须拒绝 claim-1 / 缺 candidate-binding 的载荷（不得宽松读成 current）。
    claim_payload = SS.claim_to_dict(valid_claim)
    legacy_payload = dict(claim_payload, schema_version="claim-1")
    missing_binding = {k: v for k, v in claim_payload.items()
                       if k not in ("accepted_binding_ids", "schema_version")}
    rejected_legacy = rejected_missing = False
    try:
        SS.claim_from_dict(legacy_payload)
    except SS.SectionSchemaError:
        rejected_legacy = True
    try:
        SS.claim_from_dict(missing_binding)
    except SS.SectionSchemaError:
        rejected_missing = True

    return {
        "valid_claim_passes": ok,
        "recommendation_rejected": got_bad_type,
        "bad_ref_type_rejected": got_bad_rec,
        "fact_without_citation_rejected": got_no_cite,
        "valid_unresolved_passes": ok_unresolved,
        "claim1_payload_rejected_by_current_reader": rejected_legacy,
        "claim2_missing_binding_rejected": rejected_missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.validator",
        description="章节产物结构校验器 self-check")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_check:
        parser.print_help()
        return 0
    import json
    result = _self_check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    all_ok = all(result.values())
    print("\nself-check:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

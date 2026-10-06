"""Eval: Phase 4 Batch A — 章节产物 Schema 与身份派生。

不调用 LLM / Embedding / Chroma / 互联网。

覆盖：
1. CitationRef 复用 harness.schema.CitationRef（非第二套引用类型）。
2. claim_type 白名单（fact/calculation/inference，不含 recommendation）。
3. derive_claim_id / derive_section_version 确定性且随内容变化。
4. citation_identity 三类引用身份。
5. 结构校验器：合法 claim 通过；recommendation 拒绝；fact 无 citation 拒绝。
6. `claim-2`：candidate revision 与**完整** accepted binding 集必须进入身份 —— 借用另一
   candidate / 改写命题 / 漏掉或改动 accepted set 都必须换出另一个 claim_id，故"带旧
   candidate 的 claim_id + 新内容"必然被判为身份不符（§16.10 #20）。
7. `section-result-2`（§16.10 #16）：current Result 必须单向引用 `section_draft_id`，且
   **不得**内嵌 evaluation —— 该环在类型层不可表达；Draft 侧也没有任何反向引用 Result 的
   字段；缺 `section_draft_id` 的载荷只走 legacy reader，current reader 一律拒绝。

用法: python -m evals.test_section_schema
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import schema as HS  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import validator as SV  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def main():
    def expect_raises(msg: str, fn, needle: str) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if needle in text:
                check(True, f"{msg} → {type(exc).__name__}: {text[:110]}")
            else:
                check(False, f"{msg} → 抛错但原因不符（期望含 {needle!r}）：{text[:150]}")
        else:
            check(False, f"{msg} → 未抛错（应 fail-closed）")

    # 1. CitationRef 复用 harness 身份（不是第二套类型）
    check(SS.CitationRef is HS.CitationRef,
          "SectionClaim 引用应复用 harness.schema.CitationRef")

    # 2. claim_type 白名单
    check("recommendation" not in SS.CLAIM_TYPES, "第一阶段不含 recommendation")
    check(SS.CLAIM_TYPES == ("fact", "calculation", "inference"), "claim 类型三态")

    # claim-2 fixture：candidate revision 与完整 accepted binding 集是**构造期必需**字段。
    ref = SS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=12)

    def claim(**over) -> SS.SectionClaim:
        base = dict(
            section_id="company", topic_id="company_identity",
            question_ids=("company_subject_match",), text="主体一致",
            claim_type="fact", citation_refs=(ref,),
            claim_candidate_id="cc_1", claim_candidate_revision="rev-1",
            accepted_binding_ids=("ab_1",),
        )
        base.update(over)
        base["claim_id"] = SS.derive_claim_id(
            base["claim_type"], base["topic_id"], base["question_ids"], base["text"],
            base["citation_refs"], base["claim_candidate_id"],
            base["claim_candidate_revision"], base["accepted_binding_ids"])
        return SS.SectionClaim(schema_version=SS.CLAIM_SCHEMA_VERSION, **base)

    # 3. derive_claim_id 确定性 + 随正文变化
    cid1 = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                              "主体一致", [ref])
    cid2 = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                              "主体一致", [ref])
    check(cid1 == cid2, "derive_claim_id 确定性")
    cid3 = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                              "主体不一致", [ref])
    check(cid1 != cid3, "正文变化改变 claim_id")

    # 6. claim-2 身份：candidate revision 与 accepted binding 集必须进入 claim_id
    cid_cand = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                                  "主体一致", [ref], "cc_2", "rev-1", ("ab_1",))
    cid_rev = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                                 "主体一致", [ref], "cc_1", "rev-2", ("ab_1",))
    cid_bind = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                                  "主体一致", [ref], "cc_1", "rev-1", ("ab_2",))
    check(len({cid_cand, cid_rev, cid_bind}) == 3,
          "换 candidate / 换 revision / 换 accepted binding 集都必须换出不同 claim_id")
    check(cid_cand not in (cid1,), "candidate 身份进入 claim_id（不靠 payload 比对兜底）")

    # 7. section-result-2：Result→Draft 单向引用；Result 不得内嵌 evaluation
    result_fields = set(SS.SectionResult.__dataclass_fields__)
    check("evaluation" not in result_fields,
          "current SectionResult 没有 evaluation 字段（回指自身的 evaluation 在类型层不可表达）")
    draft_fields = set(NS.SectionDraft.__dataclass_fields__)
    check(not any(f.startswith("section_result") or f.startswith("evaluation")
                  for f in draft_fields),
          "current SectionDraft 没有任何反向引用 Result / evaluation 的字段（单向关系）")
    check(SS.SECTION_RESULT_SCHEMA_VERSION == "section-result-2"
          and "" in SS.LEGACY_SECTION_RESULT_SCHEMA_VERSIONS,
          "result 的 legacy 登记含「缺 marker」形态（旧形状本就没有 marker）")

    # 4. derive_section_version 确定性 + 随 claims / draft 变化
    claim_v1 = claim()
    v1 = SS.derive_section_version("task_x", [claim_v1], [], section_draft_id="draft_1",
                                   renderer_version="r1", rules_version="g1")
    v2 = SS.derive_section_version("task_x", [claim_v1], [], section_draft_id="draft_1",
                                   renderer_version="r1", rules_version="g1")
    check(v1 == v2, "derive_section_version 确定性")
    claim2 = claim(claim_id="", claim_candidate_id="cc_2", text="另一事实",
                   accepted_binding_ids=("ab_2",))
    v3 = SS.derive_section_version("task_x", [claim_v1, claim2], [], section_draft_id="draft_1",
                                   renderer_version="r1", rules_version="g1")
    check(v1 != v3, "claims 变化改变 section_version")
    v4 = SS.derive_section_version("task_x", [claim_v1], [], section_draft_id="draft_2",
                                   renderer_version="r1", rules_version="g1")
    check(v1 != v4, "Draft 变化改变 section_version（版本必须覆盖 Draft identity）")
    # §16.10 #16：section_draft_id 是必填关键字，省略即等于版本不覆盖 Draft → fail-closed
    expect_raises("derive_section_version 缺 section_draft_id",
                  lambda: SS.derive_section_version("task_x", [claim_v1], [],
                                                    renderer_version="r1", rules_version="g1"),
                  "section_draft_id")
    expect_raises("derive_section_version 传空 section_draft_id",
                  lambda: SS.derive_section_version("task_x", [claim_v1], [],
                                                    section_draft_id="  ",
                                                    renderer_version="r1", rules_version="g1"),
                  "section_draft_id")

    # 5. citation_identity 三类
    check(SS.citation_identity(SS.CitationRef(ref_type="evidence", evidence_id="e",
                                              page_number=3)) == "evidence:e:3",
          "evidence 引用身份")
    check(SS.citation_identity(SS.CitationRef(ref_type="structured", snapshot_id="s",
                                              formula_id="f", formula_version="v1",
                                              period="2025-12-31"))
          == "structured:s::f:v1:2025-12-31", "structured 引用身份")
    check(SS.citation_identity(SS.CitationRef(ref_type="external",
                                              source_snapshot_id="x")) == "external:x",
          "external 引用身份")

    # 8. 结构校验器
    ok_ref = SS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=12)
    valid = claim(citation_refs=(ok_ref,))
    check(SV.validate_claim(valid) == [], "合法 claim 通过校验")
    check(SV.validate_section_result(SS.SectionResult(
        section_result_id="sr_1", schema_version=SS.SECTION_RESULT_SCHEMA_VERSION,
        section_version=v1, section_draft_id="draft_1", task_id="task_x",
        section_id="company", status="COMPLETED", claims=(valid,))) == [],
        "合法 Result 通过校验（带 section_draft_id、无内嵌 evaluation）")
    rec = claim(claim_type="recommendation", text="建议授信")
    check(any("recommendation" in e for e in SV.validate_claim(rec)),
          "recommendation 被拒绝")
    nocite = claim(citation_refs=())
    check(any("既无 citation 也无 derived_from_claim_ids" in e
              for e in SV.validate_claim(nocite)),
          "fact 无 citation 被拒绝")

    # 6b. claim-2 反例：借用另一 candidate / 改写命题 / 漏掉 accepted set 后 claim_id 不再自洽
    expect_raises("SectionClaim schema_version 为 legacy claim-1",
                  lambda: claim(schema_version="claim-1"), "schema_version")
    expect_raises("SectionClaim 缺 candidate revision",
                  lambda: claim(claim_candidate_revision=""), "claim_candidate_revision")
    expect_raises("SectionClaim 借用另一 candidate 的 claim_id",
                  lambda: SS.SectionClaim(
                      schema_version=SS.CLAIM_SCHEMA_VERSION, claim_id=claim_v1.claim_id,
                      section_id="company", topic_id="company_identity",
                      question_ids=("company_subject_match",), text="主体一致",
                      claim_type="fact", citation_refs=(ref,),
                      claim_candidate_id="cc_other", claim_candidate_revision="rev-1",
                      accepted_binding_ids=("ab_1",)),
                  "claim_id 与内容不符")
    expect_raises("SectionClaim 改写命题却不改 claim_id",
                  lambda: SS.SectionClaim(
                      schema_version=SS.CLAIM_SCHEMA_VERSION, claim_id=claim_v1.claim_id,
                      section_id="company", topic_id="company_identity",
                      question_ids=("company_subject_match",), text="主体不一致",
                      claim_type="fact", citation_refs=(ref,),
                      claim_candidate_id="cc_1", claim_candidate_revision="rev-1",
                      accepted_binding_ids=("ab_1",)),
                  "claim_id 与内容不符")
    expect_raises("SectionClaim 漏掉完整 accepted 集却不改 claim_id",
                  lambda: SS.SectionClaim(
                      schema_version=SS.CLAIM_SCHEMA_VERSION, claim_id=claim_v1.claim_id,
                      section_id="company", topic_id="company_identity",
                      question_ids=("company_subject_match",), text="主体一致",
                      claim_type="fact", citation_refs=(ref,),
                      claim_candidate_id="cc_1", claim_candidate_revision="rev-1",
                      accepted_binding_ids=("ab_1", "ab_2")),
                  "claim_id 与内容不符")
    expect_raises("factual SectionClaim 无 accepted binding",
                  lambda: claim(accepted_binding_ids=()), "accepted_binding_id")
    expect_raises("SectionClaim accepted set 含重复",
                  lambda: claim(accepted_binding_ids=("ab_1", "ab_1")), "重复")

    # 7b. result-2 反例：current reader 拒绝缺 draft / legacy marker 的载荷
    result_doc = SS.section_result_to_dict(SS.SectionResult(
        section_result_id="sr_1", schema_version=SS.SECTION_RESULT_SCHEMA_VERSION,
        section_version=v1, section_draft_id="draft_1", task_id="task_x",
        section_id="company", status="COMPLETED", claims=(valid,)))
    check(SS.section_result_from_dict(result_doc).section_draft_id == "draft_1",
          "current Result round-trip 保留 section_draft_id")
    missing_draft = dict(result_doc)
    missing_draft.pop("section_draft_id")
    expect_raises("current Result 缺 section_draft_id",
                  lambda: SS.section_result_from_dict(missing_draft), "section_draft_id")
    legacy_marker = dict(result_doc, schema_version="")
    expect_raises("current result reader 拒绝无 marker 的 legacy 载荷",
                  lambda: SS.section_result_from_dict(legacy_marker), "legacy")
    legacy_doc = {k: v for k, v in missing_draft.items() if k != "schema_version"}
    legacy_view = SS.load_legacy_section_result_for_audit(legacy_doc)
    check(legacy_view.section_result_id == "sr_1"
          and not isinstance(legacy_view, SS.SectionResult),
          "缺 marker / 缺 section_draft_id 的旧载荷只经显式 legacy reader 只读回放")
    expect_raises("current result-2 载荷经 legacy reader 读回",
                  lambda: SS.load_legacy_section_result_for_audit(result_doc),
                  "不得经 legacy reader 读回")

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))

"""Phase 4 Batch D — 章节 Rules Evaluator 专项评测（12 项规则 + 返回契约）。

纯离线：注入假 CitationAuthority / FinancialFactPack，不读库、不调 LLM。
覆盖任务书 §13.1 的 12 项规则及 RulesVerdict 返回契约（幂等 id、阻断/返工/警告三档）。
"""

from __future__ import annotations

import ast
import copy
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS  # noqa: E402
from harness.schema import CitationRef  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import material_context as MC  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402
from sections import pack_writer as PW  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import rules_evaluator as RE  # noqa: E402
from sections.citation_authority import CitationVerdict  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _task(aspects=(), blocking=(), impact=(), topic_ids=("t1", "t2")):
    return PS.SectionTask(
        task_id="task_eval", plan_id="plan_eval", section_id="financial",
        title="财务分析", purpose="p", research_policy="workflow",
        topic_ids=topic_ids,
        questions=(
            PS.PlannedQuestion(question_id="q1", question="Q1", priority="P0",
                               topic_id="t1", required_aspects=aspects,
                               blocking_policy=blocking, impact_scope=impact),
            PS.PlannedQuestion(question_id="q2", question="Q2", priority="P1",
                               topic_id="t2", blocking_policy=(), impact_scope=()),
        ),
        evaluation_rule_ids=(), allowed_capabilities=(),
        output_requirements=(), blocking_rules=(), dependency_versions={},
    )


def _claim(cid, topic_id, qids, text, ctype="fact", refs=(), impact=()):
    # P11：`evaluate_section` 保留 Phase-4 `ab-1` 历史角色，其入参是**旧形状** Result。
    # current `claim-2` / `section-result-2` 需要 candidate revision 与 accepted binding，
    # 旧规则集（12 条确定性规则）不消费它们，所以这里显式构造 legacy 视图而不是给
    # claim-2 填假字段 —— 用假 candidate id 造 current 对象会把「历史角色」测成假 current 链。
    return SS.LegacySectionClaimV1(claim_id=cid, section_id="financial", topic_id=topic_id,
                                   question_ids=qids, text=text, claim_type=ctype,
                                   citation_refs=refs, impact_scope=impact)


def _struct_ref(sid="S1", item="TOTAL_ASSETS", period="2025-12-31"):
    return CitationRef(ref_type="structured", snapshot_id=sid, item_code=item, period=period)


def _result(task, claims, unresolved=(), status="COMPLETED", markdown="# 财务分析"):
    return SS.LegacySectionResultV1(
        section_result_id="sr_eval", section_version="secver_eval", task_id=task.task_id,
        section_id="financial", status=status, claims=tuple(claims),
        unresolved=tuple(unresolved), markdown=markdown)


class _Fact:
    def __init__(self, kind, code, period, display, status="CALCULATED_EXACT", note=""):
        self.kind, self.code, self.period, self.display, self.status = \
            kind, code, period, display, status
        # 权威自己的口径说明（代理口径事实才非空）：限定语措辞来自权威，测试也不代写。
        self.note = note


class _Pack:
    def __init__(self, facts):
        self.facts = facts


class _Authority:
    def __init__(self, fail=(), unknown=(), grades=None):
        self._fail = set(fail)
        self._unknown = set(unknown)
        self._grades = grades or {}
        self.calls = 0

    def validate(self, ref):
        self.calls += 1
        ident = SS.citation_identity(ref)
        if ident in self._fail:
            return CitationVerdict(ref.ref_type, False, "snapshot_not_current")
        if ref.ref_type == "external" and ident in self._unknown:
            return CitationVerdict("external", True, None, ("published_at_unknown",))
        return CitationVerdict(ref.ref_type, True)

    def external_get(self, sid):
        class _Ext:
            source_grade = self._grades.get(sid)
        return _Ext()


def _rule_ids(v: RE.RulesVerdict) -> set[str]:
    return {i.rule_id for i in v.issues}


def main():
    fact = _Fact("fact", "TOTAL_ASSETS", "2025-12-31", "1,234.56万元")
    task_ok = _task()
    ref_ok = _struct_ref(item="TOTAL_ASSETS")
    c_ok = _claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (ref_ok,))
    c_q2 = _claim("c2", "t2", ("q2",), "整体财务稳健", "inference", ())
    pack = _Pack([fact])

    # --- 规则 1：契约覆盖 ---
    v = RE.evaluate_section(_result(task_ok, [c_ok, c_q2]), task_ok,
                            citation_authority=_Authority(), fact_pack=pack)
    check(v.rules_passed and not v.blocking, "合法章节通过（规则 1/4/5/6 无告警）")

    res = _result(task_ok, [c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("coverage_missing_topic" in _rule_ids(v), "缺失 topic 检出 coverage_missing_topic")
    check("coverage_missing_question" in _rule_ids(v), "缺失 question 检出 coverage_missing_question")

    # unresolved 覆盖 question 时不重复报 coverage_missing_question
    u_cov = SS.SectionUnresolved(unresolved_id="ur_cov", section_id="financial",
                                 topic_id="t2", question_id="q2",
                                 state="NOT_FOUND_AFTER_SEARCH", reason_code="write_not_found",
                                 detail="未检索到")
    res = _result(task_ok, [c_ok], unresolved=[u_cov], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check("coverage_missing_question" not in _rule_ids(v),
          "unresolved 覆盖问题后不再报覆盖缺口")

    # --- 规则 2：required aspect（warning，非阻断）---
    task_aspect = _task(aspects=("流动比率",))
    res = _result(task_aspect, [c_ok, c_q2])
    v = RE.evaluate_section(res, task_aspect)
    check(any(i.rule_id == "aspect_uncovered" and i.severity == "warning" for i in v.issues),
          "未字面出现的 aspect → warning 且不阻断")
    c_aspect = _claim("c3", "t1", ("q1",), "流动比率 1.23", "fact", (ref_ok,))
    res = _result(task_aspect, [c_aspect, c_q2])
    v = RE.evaluate_section(res, task_aspect)
    check("aspect_uncovered" not in _rule_ids(v), "字面出现的 aspect 不报")

    # --- 规则 3：阻断后果一致性 ---
    u_conf = SS.SectionUnresolved(unresolved_id="ur_conf", section_id="financial",
                                  topic_id="t1", question_id="q1", state="CONFLICT",
                                  reason_code="conflict_pause", detail="口径冲突")
    res = _result(task_ok, [c_ok, c_q2], unresolved=[u_conf], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check(v.blocking and "conflict_unresolved" in _rule_ids(v), "CONFLICT → blocking")

    u_wh = SS.SectionUnresolved(unresolved_id="ur_wh", section_id="financial",
                                topic_id="t1", question_id="q1", state="WAITING_HUMAN",
                                reason_code="transfer_human", detail="需确认")
    res = _result(task_ok, [c_ok, c_q2], unresolved=[u_wh], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check(v.blocking and "waiting_human" in _rule_ids(v), "WAITING_HUMAN → blocking")

    # blocking_effects 与 blocking_policy 不一致
    task_blk = _task(blocking=("SECTION_BLOCKED",), impact=("subject",))
    u_mismatch = SS.SectionUnresolved(unresolved_id="ur_mm", section_id="financial",
                                      topic_id="t1", question_id="q1",
                                      state="NOT_FOUND_AFTER_SEARCH", reason_code="x",
                                      detail="d", blocking_effects=())
    res = _result(task_blk, [c_ok, c_q2], unresolved=[u_mismatch], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_blk)
    check("blocking_policy_mismatch" in _rule_ids(v), "blocking_effects 不一致 → blocking_policy_mismatch")

    # SECTION_BLOCKED unresolved 但 status 非 SECTION_BLOCKED
    u_sb = SS.SectionUnresolved(unresolved_id="ur_sb", section_id="financial",
                                topic_id="t1", question_id="q1",
                                state="NOT_FOUND_AFTER_SEARCH", reason_code="x",
                                detail="d", blocking_effects=("SECTION_BLOCKED",))
    res = _result(task_ok, [c_ok, c_q2], unresolved=[u_sb], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check("status_not_blocked" in _rule_ids(v), "SECTION_BLOCKED unresolved 但 status 未阻断 → status_not_blocked")

    # --- 规则 4：关键 claim 有 citation ---
    c_nocite = _claim("c4", "t1", ("q1",), "某事实", "fact", ())
    res = _result(task_ok, [c_nocite, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("claim_missing_citation" in _rule_ids(v), "fact 无 citation → claim_missing_citation")

    # --- 规则 5：引用可解析 ---
    res = _result(task_ok, [c_ok, c_q2])
    auth_fail = _Authority(fail={SS.citation_identity(ref_ok)})
    v = RE.evaluate_section(res, task_ok, citation_authority=auth_fail, fact_pack=pack)
    check("citation_unresolvable" in _rule_ids(v), "引用不可解析 → citation_unresolvable")

    # 缓存：同引用只 validate 一次
    auth = _Authority()
    v = RE.evaluate_section(res, task_ok, citation_authority=auth, fact_pack=pack)
    check(auth.calls == 1, f"相同引用只 validate 一次（实际 {auth.calls} 次）")

    # --- 规则 6：财务数值 == 权威值 ---
    res = _result(task_ok, [c_ok, c_q2])
    v = RE.evaluate_section(res, task_ok, citation_authority=_Authority(),
                            fact_pack=_Pack([_Fact("fact", "TOTAL_ASSETS", "2025-12-31", "9,999万元")]))
    check("financial_value_mismatch" in _rule_ids(v), "display 不在正文 → financial_value_mismatch")

    ref_unknown = _struct_ref(item="REVENUE")
    c_unknown = _claim("c5", "t1", ("q1",), "营收增长", "fact", (ref_unknown,))
    res = _result(task_ok, [c_unknown, c_q2])
    v = RE.evaluate_section(res, task_ok, citation_authority=_Authority(), fact_pack=pack)
    check("financial_unresolvable_value" in _rule_ids(v), "structured 引用解析不到 → financial_unresolvable_value")

    fact_proxy = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                       status="CALCULATED_PROXY")
    ref_metric = CitationRef(ref_type="structured", snapshot_id="S1",
                             formula_id="SOLV_CURRENT_RATIO", formula_version="v1",
                             period="2025-12-31")
    c_proxy = _claim("c6", "t1", ("q1",), "流动比率精确值为 1.50", "calculation", (ref_metric,))
    res = _result(task_ok, [c_proxy, c_q2])
    v = RE.evaluate_section(res, task_ok, citation_authority=_Authority(),
                            fact_pack=_Pack([fact_proxy]))
    check("proxy_claimed_exact" in _rule_ids(v), "proxy 写成精确 → proxy_claimed_exact")

    # --- 规则 6b（3.6）：代理口径**必须显式标记** ---
    # 这条只否证「把代理写成精确」；正面要求是「断言里必须写出权威自己的口径限定语」。
    # 限定语逐字取自权威 `note`（`financial_worker` 生成的那份措辞），测试不代写一个更顺口的。
    fact_proxy_note = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                            status="CALCULATED_PROXY", note="代理口径（PROXY_INPUT）")
    c_unmarked = _claim("c6b", "t1", ("q1",), "流动比率为 1.50", "calculation", (ref_metric,))
    v = RE.evaluate_section(_result(task_ok, [c_unmarked, c_q2]), task_ok,
                            citation_authority=_Authority(), fact_pack=_Pack([fact_proxy_note]))
    check("proxy_not_marked" in _rule_ids(v),
          "代理口径的断言没写出权威自己的限定语 → proxy_not_marked")
    check(any(t.reason == "proxy_unmarked" for t in v.rework_targets),
          "proxy_not_marked 必须是 rework（typed target），不是 warning")
    # 正例：逐字写出限定语 → 不再报。没有这条，规则就等于「代理口径永远不可能通过」。
    c_marked = _claim("c6c", "t1", ("q1",), "流动比率为 1.50，代理口径（PROXY_INPUT）",
                      "calculation", (ref_metric,))
    v = RE.evaluate_section(_result(task_ok, [c_marked, c_q2]), task_ok,
                            citation_authority=_Authority(), fact_pack=_Pack([fact_proxy_note]))
    check("proxy_not_marked" not in _rule_ids(v),
          "写出限定语后不得再报 proxy_not_marked（否则这条规则是拒绝而不是要求）")
    # 反例：精确口径的事实不得被要求写口径标记（正面要求只对代理事实成立）。
    fact_exact = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                       status="CALCULATED_EXACT")
    v = RE.evaluate_section(_result(task_ok, [c_unmarked, c_q2]), task_ok,
                            citation_authority=_Authority(), fact_pack=_Pack([fact_exact]))
    check("proxy_not_marked" not in _rule_ids(v),
          "精确口径的事实不得被要求写口径标记（要求只对代理事实成立）")
    # 规则集身份：新增正面要求后 `p4-rules-v1` 的「规则通过」不再等价于本版，版本必须前进。
    check(RE.RULES_VERSION == "p4-rules-v2",
          f"规则集变了，RULES_VERSION 必须前进（实际 {RE.RULES_VERSION!r}）")

    # --- 规则 7：external 完整性 + 来源分级 ---
    ref_ext = CitationRef(ref_type="external", source_snapshot_id="x1")
    c_ext = _claim("c7", "t1", ("q1",), "行业数据", "fact", (ref_ext,))
    res = _result(task_ok, [c_ext, c_q2])
    v = RE.evaluate_section(res, task_ok,
                            citation_authority=_Authority(unknown={SS.citation_identity(ref_ext)}))
    check("external_missing_date" in _rule_ids(v), "external 缺发布日期 → warning")

    c_ext_core = _claim("c8", "t1", ("q1",), "行业关键结论", "fact", (ref_ext,),
                        impact=("key_financial",))
    res = _result(task_ok, [c_ext_core, c_q2])
    v = RE.evaluate_section(res, task_ok,
                            citation_authority=_Authority(grades={"x1": "D"}))
    check("d_source_sole_basis" in _rule_ids(v), "D 级来源唯一支撑核心结论 → rework")
    v = RE.evaluate_section(res, task_ok,
                            citation_authority=_Authority(grades={"x1": "C"}))
    check("single_c_source" in _rule_ids(v), "C 级来源唯一支撑核心结论 → warning")

    # --- 规则 8：未发现/不存在措辞 ---
    c_unfound = _claim("c9", "t1", ("q1",), "未发现重大诉讼", "fact", (ref_ok,))
    res = _result(task_ok, [c_unfound, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("unfound_claimed_absent" in _rule_ids(v), "「未发现」写成事实 → unfound_claimed_absent")

    c_absent = _claim("c10", "t1", ("q1",), "不存在重大诉讼", "fact", (ref_ok,))
    u_nf = SS.SectionUnresolved(unresolved_id="ur_nf", section_id="financial",
                                topic_id="t1", question_id="q1",
                                state="NOT_FOUND_AFTER_SEARCH", reason_code="write_not_found",
                                detail="未检索到")
    res = _result(task_ok, [c_absent, c_q2], unresolved=[u_nf], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check("absence_contradicts_gap" in _rule_ids(v), "「不存在」与 NOT_FOUND 缺口矛盾 → absence_contradicts_gap")

    # --- 规则 9：claim 类型与内容 ---
    c_calc = _claim("c11", "t1", ("q1",), "流动比率良好", "calculation", (ref_ext,))
    res = _result(task_ok, [c_calc, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("calculation_missing_structured" in _rule_ids(v),
          "calculation 无 structured → calculation_missing_structured")

    c_hedged = _claim("c12", "t1", ("q1",), "预计未来营收增长", "fact", (ref_ok,))
    res = _result(task_ok, [c_hedged, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("fact_hedged" in _rule_ids(v), "fact 含研判措辞 → fact_hedged")

    # --- 规则 10：维度一致 ---
    ref_s2 = _struct_ref(sid="S2", item="TOTAL_ASSETS")
    c_mix = _claim("c13", "t1", ("q1",), "总资产对比", "fact", (ref_ok, ref_s2))
    res = _result(task_ok, [c_mix, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("dimension_mismatch" in _rule_ids(v), "混用快照 → dimension_mismatch")

    # --- 规则 11：空壳 ---
    res = _result(task_ok, [], markdown="")
    v = RE.evaluate_section(res, task_ok)
    check(v.blocking and "empty_section" in _rule_ids(v), "空壳章节 → blocking empty_section")

    # --- 规则 12：自创授信方案 ---
    c_scheme = _claim("c14", "t1", ("q1",), "建议授信额度 3 亿元，期限 3 年", "fact", ())
    res = _result(task_ok, [c_scheme, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("self_invented_scheme" in _rule_ids(v), "无引用建议 → self_invented_scheme")

    c_existing = _claim("c15", "t1", ("q1",), "公司现有授信额度 20 亿元", "fact", (ref_ok,))
    res = _result(task_ok, [c_existing, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("self_invented_scheme" not in _rule_ids(v), "存量事实豁免（有引用 + 现有措辞）")

    # --- RulesVerdict 契约：幂等 + 三档 ---
    v1 = RE.evaluate_section(_result(task_ok, [c_nocite, c_q2]), task_ok)
    v2 = RE.evaluate_section(_result(task_ok, [c_nocite, c_q2]), task_ok)
    check({i.issue_id for i in v1.issues} == {i.issue_id for i in v2.issues},
          "相同输入 issue_id 幂等")
    check(any(t.target_kind == "claim" and t.target_ref == "c4" for t in v1.rework_targets),
          "rework target 指向 claim c4")
    check(v1.summary["rework_target_count"] == len(v1.rework_targets),
          "summary rework_target_count 一致")

    # warning-only 不阻断、不返工
    task_w = _task(aspects=("流动比率",))
    v = RE.evaluate_section(_result(task_w, [c_ok, c_q2]), task_w)
    check(v.rules_passed and not v.blocking and not v.rework_targets,
          "仅 warning 时 rules_passed=True 且无 blocking/rework")

    # =====================================================================
    # P11：factual 链唯一协调者（只编排 P8 → P9 → P10）+ `support_semantics` 分支
    # =====================================================================
    run_claim_chain_checks(check)
    run_support_rules_checks(check)

    return _results


# ---------------------------------------------------------------------------
# P11 — `evaluate_claim_chain`（唯一协调者）与 `_narrative_rules` 的 support 分支
# ---------------------------------------------------------------------------

_FP = "a" * 64


def _scan(*facts):
    return PW.AuthorityScan(facts=tuple(facts), aspect_status={}, aspect_topic={},
                            aspect_impact={}, aspect_blocking={}, aspect_question={},
                            excluded_facts=(), conflicts=(), not_found=(), gaps=(),
                            coverage_counts={})


_PACK_FACT = PW.AuthorityFactEntry(
    authority_kind="topic_pack", container_identity="pack-1", fact_id="f-1",
    text="公司2024年营业收入为1234.56亿元。", topic_id="t-1", aspect_ids=("a-1",),
    required=True, fact_type="metric", period="2024", scope="公司", material_id="m-1",
    payload_ref={"object_type": "research_material"},
    locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40),
    source_identity="evidence:ev-1", provenance_identity="prov-1", content_fingerprint=_FP)

#: §三 A：`wmctx-1` 的**已解析正文**上下文。本模块的链是**路径 A**（预验证权威事实），
#: 命题不靠材料正文成立，但 `evaluate_claim_chain` 的守卫是集合级的：manifest 非空即必须
#: 有正文上下文，否则绑定门与语义门会替一份它们从未见过的正文背书。这里按 `wmctx-1` 的
#: 公开构造入口手搭一份成员与 manifest 逐字段一致的上下文（本夹具没有 `VerifiedPackSet`，
#: 因此不走 `resolve_writer_material_context`；「Bytes → 哈希/定位/身份」那一半由
#: `test_demo_pack_writer` §11b 在真实 PackSet 上覆盖（本夹具只给出**一致**的一份上下文，
#: 不重复那一半的篡改面）。
_MEMBER_REF = NS.manifest_member_ref("pack-1", "m-1")
_MATERIAL_TEXT = "本节引用的公开材料描述了该公司主营业务的构成与经营模式，属于必要背景。"
_RESOLVED_MATERIAL = MC.ResolvedWriterMaterial.create(
    member_ref=_MEMBER_REF, topic_id="t-1", pack_id="pack-1", material_id="m-1",
    material_type="evidence_span", research_material_disposition_id="rmd-1",
    source_identity="evidence:ev-1", provenance_identity="prov-1",
    locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40),
    payload_ref=TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
        content_hash=_FP,
        locator=TS.EvidenceLocator(document_id="doc-1", document_version="v1",
                                   section_path="s1", page=3),
        created_dependency_fingerprint="e" * 64).to_dict(),
    payload_hash=_FP, content_hash=_FP, material_content_fingerprint=_FP,
    reading_view=_MATERIAL_TEXT)
_MATERIAL_CONTEXT = MC.WriterMaterialContext.create(
    task_id="t-1", section_id="company", pack_set_fingerprint="d" * 64,
    materials=(_RESOLVED_MATERIAL,))

_MANIFEST = NS.WriterMaterialManifest.create(members=(
    NS.WriterMaterialManifestEntry.create(
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=_FP, topic_id="t-1",
        material_type="evidence_span", payload_ref=dict(_RESOLVED_MATERIAL.payload_ref),
        locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40), payload_hash=_FP,
        reading_view_fingerprint=_RESOLVED_MATERIAL.reading_view_fingerprint),))

_REVISION = NS.derive_draft_revision(
    task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
    contract_version="cv-1", contract_fingerprint="cf-1", writer_policy_version="wp-1",
    prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
    manifest_id=_MANIFEST.manifest_id, manifest_fingerprint=_MANIFEST.fingerprint())

_CAND = NS.ClaimCandidate.create(
    draft_revision=_REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入为1234.56亿元。", fact_type="metric")
_CAND_2 = NS.ClaimCandidate.create(
    draft_revision=_REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入同比下降。", fact_type="metric")
_UNIT = NS.NarrativeDraftUnit.create(
    draft_revision=_REVISION, section_id="company", index=0, unit_kind="paragraph",
    text="本节说明公司经营情况。")


def _factual_proposal(candidate=_CAND, **over):
    kw = dict(binding_subject_kind="claim_candidate", binding_subject_id=candidate.candidate_id,
              draft_revision=_REVISION, manifest_id=_MANIFEST.manifest_id,
              manifest_fingerprint=_MANIFEST.fingerprint(), authority_kind="topic_pack",
              authority_container_id="pack-1", source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="primary", support_semantics="factual",
              authorization_path="path_a_prevalidated", content_fingerprint=_FP,
              dependency_fingerprint="dep-1", fact_id="f-1", material_id="m-1",
              payload_ref={"object_type": "research_material"},
              locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40))
    kw.update(over)
    return NS.ProposedSupportRef.create(**kw)


_CONTEXT_PROPOSAL = NS.ProposedSupportRef.create(
    binding_subject_kind="narrative_draft_unit", binding_subject_id=_UNIT.draft_unit_id,
    draft_revision=_REVISION, manifest_id=_MANIFEST.manifest_id,
    manifest_fingerprint=_MANIFEST.fingerprint(), authority_kind="topic_pack",
    authority_container_id="pack-1", source_identity="evidence:ev-1",
    provenance_identity="prov-1", support_role="corroborating",
    support_semantics="context", authorization_path="context_only",
    content_fingerprint=_FP, dependency_fingerprint="dep-1", material_id="m-1",
    # §四.2：context 边必须自闭合（payload_ref + exact locator 与 manifest 成员逐字相同）。
    payload_ref=dict(_MANIFEST.entries[0].payload_ref),
    locator_ref=_MANIFEST.entries[0].locator_ref)


def _narr4_draft(proposals, candidates=(_CAND,), units=(_UNIT,), unresolved_ids=()):
    # 有 proposal 时该材料确实被「用了」（`used` 必须给出实际引用它的 support_usages，不得自报）；
    # 没有 proposal 的零 subject 反例则只能登记为 `not_used` 并带 typed 理由 —— 「本轮没用到」
    # 与「Contract 必需事实未取得」是两条不同的轴，反例不得把它伪装成前者之外的任何东西。
    if proposals:
        disposition = NS.WriterMaterialProcessingDisposition.create(
            manifest_id=_MANIFEST.manifest_id, manifest_fingerprint=_MANIFEST.fingerprint(),
            pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
            material_content_fingerprint=_FP, processed=True, usage="used",
            support_usages=tuple(p.proposed_support_id for p in proposals), reason_code=None,
            reason_proof=None, writer_policy_version="wp-1")
    else:
        disposition = NS.WriterMaterialProcessingDisposition.create(
            manifest_id=_MANIFEST.manifest_id, manifest_fingerprint=_MANIFEST.fingerprint(),
            pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
            material_content_fingerprint=_FP, processed=True, usage="not_used",
            support_usages=(), reason_code="irrelevant_to_section_goal",
            reason_proof={"policy_version": "mnp-1", "policy_fingerprint": "c" * 64,
                          "relevance_decision_ref": "rd-1"},
            writer_policy_version="wp-1")
    return NS.SectionDraft.create(
        task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
        contract_version="cv-1", contract_fingerprint="cf-1", producer_kind="topic_harness",
        writer_policy_version="wp-1", prompt_version="pack_section_writer_proposals_v1",
        model_policy="stub", authority_container_ids=("pack-1",), material_manifest=_MANIFEST,
        material_dispositions=(disposition,),
        claim_candidates=tuple(candidates), narrative_draft_units=tuple(units),
        proposed_support_refs=tuple(sorted(proposals, key=lambda p: p.proposed_support_id)),
        unresolved_ids=tuple(unresolved_ids), unresolved_projections=(), coverage_summary={},
        conflict_projections=(), not_found_projections=(), dependency_fingerprint="dep-1")


#: 反向对照夹具：**本节确实没有 Pack 材料**（空 manifest、零 disposition）。这不是「夹具凑合」，
#: 而是路径 A 的真实形态之一 —— 命题由预验证权威事实支撑，材料清单本来就可能是空的。用它证明
#: `evaluate_claim_chain` 的正文守卫（`manifest.entries` 非空才要求 `wmctx-1`）是**集合级**的，
#: 而不是「一律拒绝无正文上下文的调用」。
_EMPTY_MANIFEST = NS.WriterMaterialManifest.create(members=())


def _draft_without_material():
    return NS.SectionDraft.create(
        task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
        contract_version="cv-1", contract_fingerprint="cf-1", producer_kind="topic_harness",
        writer_policy_version="wp-1", prompt_version="pack_section_writer_proposals_v1",
        model_policy="stub", authority_container_ids=("pack-1",),
        material_manifest=_EMPTY_MANIFEST, material_dispositions=(),
        claim_candidates=(), narrative_draft_units=(), proposed_support_refs=(),
        unresolved_ids=("u-1",), unresolved_projections=(), coverage_summary={},
        conflict_projections=(), not_found_projections=(), dependency_fingerprint="dep-1")


class _StubEntailmentClient:
    """结构化 stub：记录调用，返回可编程判定文本（不联网、不真实调用）。"""

    def __init__(self, text, *, status="ok"):
        self.calls: list[dict] = []
        self.text = text
        self.status = status

    def evaluate(self, *, messages, system, prompt_version, model_policy, **extra):
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        return PW.NarrationResult(
            text=self.text, call_id=f"call-{len(self.calls)}",
            model=PW.resolve_model_policy(model_policy), prompt_version=prompt_version,
            status=self.status, error="" if self.status == "ok" else "transport down")


_ENTAILED = '{"verdict": "entailed", "reason_code": null, "rationale": "权威逐字给出"}'
_NOT_ENTAILED = ('{"verdict": "rejected", "reason_code": "unsupported_specificity", '
                 '"rationale": "过细"}')


def run_claim_chain_checks(check) -> None:
    scan = _scan(_PACK_FACT)

    def chain(draft, client, **kw):
        """本模块 `evaluate_claim_chain` 的**唯一**调用口径：注入 §三 A 的正文上下文。

        守卫是集合级的（manifest 非空 ⇒ 必须有 `wmctx-1` 上下文），因此正例与「边本身要坏」
        的反例走同一口径；只有专门验证入口守卫的反例（缺 client / 入参类型不符）才绕过它。
        """
        return RE.evaluate_claim_chain(draft, scan, llm_client=client,
                                       material_context=_MATERIAL_CONTEXT, **kw)

    def expect_raises(msg, fn, exc, *, needle=""):
        try:
            fn()
        except exc as e:
            check(not needle or needle in str(e),
                  f"{msg}（原因不符：{str(e)[:120]}）")
        except Exception as e:  # noqa: BLE001
            check(False, f"{msg}：抛出 {type(e).__name__}（{str(e)[:120]}）")
        else:
            check(False, f"{msg}：未拒绝")

    # --- 正例：一条 factual（路径 A）+ 一条 context → 整链通过 ---
    draft = _narr4_draft([_factual_proposal(), _CONTEXT_PROPOSAL])
    client = _StubEntailmentClient(_ENTAILED)
    out = chain(draft, client)
    check(out.result == "pass" and not out.failure_reasons,
          f"两条合法支撑边 → 链通过（result={out.result}，{list(out.failure_reasons)}）")
    check(len(out.aggregate_decisions) == 2
          and {(d.subject_kind, d.subject_id) for d in out.aggregate_decisions}
          == {("claim_candidate", _CAND.candidate_id),
              ("narrative_draft_unit", _UNIT.draft_unit_id)}
          and all(d.result == "pass" for d in out.aggregate_decisions),
          "每个 subject revision 恰一条聚合决定（2 个 subject → 2 条，全部 pass）")
    check(len(out.entailment_decisions) == 1,
          f"只对 factual candidate 生成一条 entailment 决定（实际 {len(out.entailment_decisions)}）")
    check(len(client.calls) == 1 and out.llm_calls == 1,
          f"仅 factual candidate 调模型一次（实际 {len(client.calls)}）")
    check(len(out.accepted_bindings) == 2,
          f"两条支撑边各自产生一条 accepted binding（实际 {len(out.accepted_bindings)}）")
    factual_bound = out.bindings_for_subject("claim_candidate", _CAND.candidate_id)
    context_bound = out.bindings_for_subject("narrative_draft_unit", _UNIT.draft_unit_id)
    check(len(factual_bound) == 1 and factual_bound[0].support_semantics == "factual",
          "按 subject 取到 factual accepted binding")
    check(len(context_bound) == 1 and context_bound[0].support_semantics == "context",
          "按 subject 取到 context accepted binding")
    check(factual_bound[0].entailment_decision_id and not context_bound[0].entailment_decision_id,
          "factual binding 带 entailment 决定、context binding 不带")
    check(not any(getattr(context_bound[0], n, None)
                  for n in ("fact_id", "financial_fact_id", "note_fact_id", "external_fact_id")),
          "context accepted binding 不携带任何事实身份")

    # --- 确定性：同一输入（同一 stub 文本）两次得到同一 binding 集 ---
    again = chain(draft, _StubEntailmentClient(_ENTAILED))
    check([b.accepted_support_binding_id for b in out.accepted_bindings]
          == [b.accepted_support_binding_id for b in again.accepted_bindings],
          "同一输入的 accepted binding 身份与顺序确定")

    # --- 语义拒绝 → 整链 fail，拒绝**显式**登记，且通过的 subject 记录不被清空 ---
    out_rej = chain(draft, _StubEntailmentClient(_NOT_ENTAILED))
    check(out_rej.result == "fail" and out_rej.failure_reasons,
          "语义 rejected → 整链 fail 且带显式失败原因")
    check(any(r.subject_id == _CAND.candidate_id and r.stage == "semantic"
              for r in out_rej.rejected_subjects),
          "语义拒绝以 typed rejected subject 显式登记（不是静默丢弃）")
    check(not out_rej.bindings_for_subject("claim_candidate", _CAND.candidate_id),
          "被语义拒绝的候选没有 accepted binding")
    check(out_rej.bindings_for_subject("narrative_draft_unit", _UNIT.draft_unit_id),
          "链级 fail 不清空已通过 subject 的记录（可审计性优先于摘要）")

    # --- 机械拒绝：坏 fact_id → 门 fail，且**零次** LLM 调用（语义门不得在失败聚合上开跑）---
    bad = _narr4_draft([_factual_proposal(fact_id="f-ghost"), _CONTEXT_PROPOSAL])
    mclient = _StubEntailmentClient(_ENTAILED)
    out_mech = chain(bad, mclient)
    check(out_mech.result == "fail" and len(mclient.calls) == 0 and out_mech.llm_calls == 0,
          f"机械门失败 → 零次模型调用（实际 {len(mclient.calls)}）")
    check(any(r.stage == "mechanical" for r in out_mech.rejected_subjects),
          "机械拒绝以 stage=mechanical 登记")
    check(not out_mech.entailment_decisions, "机械门失败不产生 entailment 决定")

    # --- fail-closed：语义门不得被「不传 client」静默跳过 ---
    expect_raises("llm_client=None 必须拒绝（语义门不得被跳过）",
                  lambda: RE.evaluate_claim_chain(draft, scan), ValueError,
                  needle="llm_client")
    expect_raises("candidate_bundle 不是 SectionDraft → 拒绝",
                  lambda: RE.evaluate_claim_chain(object(), scan,
                                                  llm_client=_StubEntailmentClient(_ENTAILED)),
                  ValueError, needle="SectionDraft")

    # --- §三 A：**看不到真实正文**时链条必须拒绝。判据是「manifest 非空」这个集合级事实，
    #     不是「本节有没有候选」：两个门都会替一份它们从未见过的正文背书，因此拒绝必须发生在
    #     **任何门运行之前**（错误类型是入口参数错，不是 `result="fail"` 的可表达失败）。---
    naked_client = _StubEntailmentClient(_ENTAILED)
    expect_raises("manifest 非空但缺 wmctx-1 正文上下文 → 绑定门/语义门不得运行",
                  lambda: RE.evaluate_claim_chain(
                      draft, scan, llm_client=naked_client, material_context=None),
                  ValueError, needle="看不到真实正文")
    check(len(naked_client.calls) == 0,
          f"拒绝发生在任何门之前（实测模型调用 {len(naked_client.calls)} 次，期望 0）")

    # --- 反向对照：**本节确实没有 Pack 材料**（空 manifest）是合法状态，同一入口不得被守卫
    #     一刀切拒绝 —— 否则「路径 A 为空」就被误判成「整节不可评估」。---
    no_material = _draft_without_material()
    check(not no_material.material_manifest.entries,
          "反向对照夹具：空 manifest（本节没有任何 Pack 材料）")
    out_nomaterial = RE.evaluate_claim_chain(
        no_material, scan, llm_client=_StubEntailmentClient(_ENTAILED),
        material_context=None)
    check(out_nomaterial.result == "fail"
          and any("no_binding_subject" in r for r in out_nomaterial.failure_reasons),
          "空 manifest + 无正文上下文：守卫不触发，链条照常走到自己的结论（"
          f"result={out_nomaterial.result}，{list(out_nomaterial.failure_reasons)}）")

    # --- P1-B：零 subject（没有候选/单元/proposal）不得以「无期望」为由放行 ---
    empty = _narr4_draft([], candidates=(), units=(), unresolved_ids=("u-1",))
    out_empty = chain(empty, _StubEntailmentClient(_ENTAILED))
    check(out_empty.result == "fail"
          and any("no_binding_subject" in r for r in out_empty.failure_reasons),
          f"零 subject 的 draft → fail（{list(out_empty.failure_reasons)}）")

    # --- 版本常量：本模块只**启用**两个门的版本，不写第三份字面量 ---
    check(RE.CLAIM_BINDING_GATE_VERSION == NS.CLAIM_BINDING_GATE_VERSION
          and RE.CLAIM_ENTAILMENT_RULES_VERSION == NS.CLAIM_ENTAILMENT_RULES_VERSION,
          "协调者 re-export 的门版本常量与 schema 的定义一致（无第三份字面量）")
    check(out.summary()["binding_gate_version"] == NS.CLAIM_BINDING_GATE_VERSION
          and out.summary()["claim_entailment_rules_version"] == NS.CLAIM_ENTAILMENT_RULES_VERSION,
          "链结论 summary 暴露两个门版本（可追溯）")
    check("acceptance" in inspect.signature(RE.evaluate_narrative_section).parameters,
          "evaluate_narrative_section 暴露 acceptance 关键字（否则 support 分支永不生效）")

    # --- 协调者不得自建语义判断（P1-6）：`evaluate_claim_chain` 体内不得出现 prompt 加载、
    #     不得直接调模型。判语义的唯一位置是 P9；本文件再判一次就是第二裁判。---
    tree = ast.parse(Path(RE.__file__).read_text(encoding="utf-8"))
    chain_fn = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "evaluate_claim_chain")
    body_src = ast.dump(chain_fn)
    check("load_prompt" not in body_src and "chat_with_usage" not in body_src,
          "evaluate_claim_chain 不加载 prompt、不直接调模型（只编排）")
    check("NARRATIVE_EVALUATOR_PROMPT" not in body_src,
          "evaluate_claim_chain 不借用 legacy 章级 review LLM 的 prompt 作第二语义裁判")
    called = {n.func.attr for n in ast.walk(chain_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and n.func.value.id in {"CBG", "CEE", "AB"}}
    check(called == {"decide_draft_bindings", "factual_candidate_revisions",
                     "evaluate_entailments", "accept_draft_bindings"},
          f"链体只调用三个门各自的编排入口，不自建判定（实际 {sorted(called)}）")


class _StubGate:
    blocking = False
    issues: tuple = ()
    gate_result_id = "gate-stub"


def run_support_rules_checks(check) -> None:
    """`_narrative_rules` 的 `support_semantics` 分支（P11）。"""

    class _Res:
        section_id: str = "company"
        claims: tuple = ()
        unresolved: tuple = ()

    scan = _scan(_PACK_FACT)

    def rules(draft, *, acceptance=None) -> set[str]:
        acc: list = []
        # 本函数只评 `support_semantics` 分支；不传 `narrative` 时同样的输入必然追加一条
        # `narrative_final_narrative_absent`（门后正文在门前对象上不存在）。该阻断与本节断言
        # 的 rule_id 集合不相交，故如实保留，不为了「干净」把它藏起来。
        RE._narrative_rules(_Res(), draft, scan, acc, gate=_StubGate(), acceptance=acceptance)
        return {i.rule_id for i, _, _ in acc}

    def chain(draft, client, **kw):
        return RE.evaluate_claim_chain(draft, scan, llm_client=client,
                                       material_context=_MATERIAL_CONTEXT, **kw)

    # 合法正例：真实 accepted binding 束下不报任何 support 问题。
    draft = _narr4_draft([_factual_proposal(), _CONTEXT_PROPOSAL])
    out = chain(draft, _StubEntailmentClient(_ENTAILED))
    ids = rules(draft, acceptance=out.acceptance)
    check(not {i for i in ids if i.startswith("narrative_support")
               or i in ("narrative_claim_candidate_unbound",
                        "narrative_context_binding_carries_fact")},
          f"合法 factual+context 支撑不产生 support 问题（{sorted(ids)}）")

    # 有 proposal 却没有束 → fail-closed（不得把「没核验」读成「核验通过」）。
    check("narrative_support_bindings_absent" in rules(draft),
          "带 proposal 的 current draft 缺 accepted binding 束 → blocking")

    # 历史 ab-1 draft（无 proposal）不适用当前角色规则，也不报 absence。
    legacy = type("_LegacyDraft", (), {"draft_id": "d-legacy", "paragraphs": (), "tables": (),
                                       "unresolved_ids": (), "unresolved_projections": ()})()
    check("narrative_support_bindings_absent" not in rules(legacy),
          "不带 proposal 的历史 draft 不报 support absence（当前规则对它是空的）")

    # 候选没有被接受的 factual 边 → blocking（「没有边」是缺口，不是通过）。
    no_fact = _narr4_draft([_factual_proposal(_CAND), _factual_proposal(_CAND_2)],
                           candidates=(_CAND, _CAND_2))
    rej = chain(no_fact, _StubEntailmentClient(_NOT_ENTAILED))
    rejected_ids = rules(no_fact, acceptance=rej.acceptance)
    check("narrative_support_subject_rejected" in rejected_ids,
          "被拒 subject 在 narrative 规则里显式 blocking（不得静默丢弃）")
    check("narrative_claim_candidate_unbound" in rejected_ids,
          "没有被接受的 factual 边的候选 → blocking（「没有边」是缺口，不是通过）")

    # context 边携带事实身份（绕过 schema 的伪造形状）→ blocking。
    context = out.acceptance.accepted_bindings[-1]
    if context.support_semantics != "context":
        context = next(b for b in out.acceptance.accepted_bindings
                       if b.support_semantics == "context")
    forged = copy.copy(context)
    object.__setattr__(forged, "fact_id", "f-1")
    forged_acceptance = copy.copy(out.acceptance)
    object.__setattr__(forged_acceptance, "accepted_bindings",
                       tuple(forged if b.accepted_support_binding_id
                             == context.accepted_support_binding_id else b
                             for b in out.acceptance.accepted_bindings))
    check("narrative_context_binding_carries_fact"
          in rules(draft, acceptance=forged_acceptance),
          "context 支撑边携带 fact_id → blocking（context 不得授权事实）")

    # 主体与角色错配（factual 挂在 draft unit 上）→ blocking。
    swapped = copy.copy(context)
    object.__setattr__(swapped, "support_semantics", "factual")
    swapped_acceptance = copy.copy(out.acceptance)
    object.__setattr__(swapped_acceptance, "accepted_bindings",
                       tuple(swapped if b.accepted_support_binding_id
                             == context.accepted_support_binding_id else b
                             for b in out.acceptance.accepted_bindings))
    check("narrative_support_semantics_subject_mismatch"
          in rules(draft, acceptance=swapped_acceptance),
          "factual 支撑边挂在 narrative_draft_unit 上 → blocking（角色与主体错配）")


if __name__ == "__main__":
    import json
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

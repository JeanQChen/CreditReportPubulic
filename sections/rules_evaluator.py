"""Phase 4 Batch D — 章节 Rules Evaluator（12 项确定性规则，无 LLM）。

任务书 §13.1 的 12 项规则，在 LLM Evaluator 之前做确定性预检。原则（修订四/十一）：
- Rules 失败不能被 LLM 覆盖：任一 blocking/rework 级 issue → rules_passed=False，LLM 不得
  将其改写为 PASS。
- 财务数字复核复用现有安全链（Structured Citation → FinancialFactPack → display 精确等价），
  **不新建正则数字解析器**；marker/裸数字安全链在 Worker 侧已强制，这里只做 display 回查。
- 自创授信方案联合判定（不是关键词封杀）：recommendation 动词 + 方案维度词共现，且
  排除「有引用的存量事实」措辞；「额度/期限/评级/担保/增信」作为存量事实出现不封杀。

严重度三档（决定后续状态机分支）：
- ``blocking``：不可返工 → BLOCKED（CONFLICT / WAITING_HUMAN / 阻断后果不一致 / 空壳）。
- ``rework``：  可定向返工 → REWORK（附 ReworkTarget）。
- ``warning``： 非阻断缺口 → 交由 LLM Evaluator 语义复核 / PASS_WITH_GAPS。

本模块纯确定性、可注入、无 I/O：citation_authority（``.validate(ref)`` + 可选
``.external_get``）与 fact_pack（``.facts`` 元素含 kind/code/period/display/status）均为
注入对象，便于离线合成测试；不注入时相应规则跳过并在 summary 记 ``*_available: False``。

CLI: python -m sections.rules_evaluator --self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from sections import narrative_schema as NS
from sections import schema as SS
from sections import accepted_binding as AB
from sections import claim_binding_gate as CBG
from sections import claim_entailment_evaluator as CEE
from sections.citation_authority import CitationVerdict

logger = logging.getLogger("sections.rules_evaluator")

# 版本常量（变更必须递增；进入 evaluation_id 派生）。
# `p4-rules-v2`（M930-3 §三 3.6）：`_rule_financial` 增加**正面**要求 —— 绑定代理口径财务
# 事实的断言必须逐字保留权威自己的口径限定语（`proxy_not_marked`）。规则集变了，同一份
# SectionResult 在 v1/v2 下会得到不同的 verdict，因此身份必须前进：`p4-rules-v1` 的结果
# 「规则通过」不再等价于 `p4-rules-v2` 的「规则通过」。
RULES_VERSION = "p4-rules-v2"

# 严重度三档。
SEVERITY_BLOCKING = "blocking"
SEVERITY_REWORK = "rework"
SEVERITY_WARNING = "warning"

# 规则 8：「未检索到」不得写成「不存在」。
_NOT_FOUND_PHRASES = ("未发现", "未检索到", "未找到", "未查到", "查无")
_ABSENCE_PHRASES = ("不存在", "未发生")

# 规则 9：fact claim 不应含研判措辞（→ 应降 inference）。
_HEDGE_PHRASES = ("预计", "推测", "预测", "估计", "大概率", "或可", "倾向认为", "判断为")

# 规则 12：自创授信方案联合判定（动词 + 维度词共现；存量事实豁免）。
_RECOMMENDATION_VERBS = ("建议", "拟", "应予", "应授", "应给予")
_SCHEME_DIMENSIONS = ("授信额度", "授信", "额度", "期限", "担保", "增信", "评级", "利率", "抵押率")
_EXISTING_FRAMING = ("现有", "目前", "存量", "截至", "当前", "已获批", "已取得")


@dataclass(frozen=True)
class RulesVerdict:
    """Rules 预检结论（不可变）。rules_passed = 无 blocking 且无 rework target。"""

    rules_passed: bool
    blocking: bool
    issues: tuple[SS.SectionIssue, ...]
    rework_targets: tuple[SS.ReworkTarget, ...]
    summary: dict


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _rework_target(kind: str, ref: str, reason: str) -> SS.ReworkTarget:
    return SS.ReworkTarget(
        target_id=SS.derive_rework_target_id(kind, ref, reason),
        target_kind=kind, target_ref=ref, reason=reason)


def _add(acc: list, rule_id: str, severity: str, location: str, detail: str,
         action: str = "", target: SS.ReworkTarget | None = None) -> None:
    """追加一条 issue（及可选 rework target），acc 元素为 (issue, target|None, is_blocking)。"""
    issue = SS.SectionIssue(
        issue_id=SS.derive_issue_id(rule_id, location, detail, severity),
        rule_id=rule_id, severity=severity, location=location,
        detail=detail, suggested_action=action)
    acc.append((issue, target, severity == SEVERITY_BLOCKING))


# ---------------------------------------------------------------------------
# 规则 3 — 阻断后果一致性（blocking）
# ---------------------------------------------------------------------------

def _rule_blocking(result: SS.SectionResult, task, acc: list) -> None:
    qmap = {q.question_id: q for q in task.questions}
    has_section_blocking = False
    for u in result.unresolved:
        q = qmap.get(u.question_id) if u.question_id else None
        # 期望值与登记值必须过**同一套**规范化：契约把「不阻断」显式写成字面量 `NONE`
        # （`templates/contracts/standard_v3.yaml` 实际在用），而 `blocking_effects` 只登记
        # 真正的阻断等级（`validate_unresolved` 拒绝 `NONE`）。不规范化则这类问题永远无法
        # produce 自洽产物。规范化不改语义：问题说 SECTION_BLOCKED、登记值留空时仍然报错。
        # 期望值只对**挂在某个问题上**的缺口成立：**节级缺口**（`question_id` 为空，例如
        # §12.4.4 第 4 步的最终句语义门 block）不属于任何问题，因此**没有**问题策略可比。
        # 此时把期望值取成 `()` 并不是「严格」而是凭空造一条不一致：缺口自己声明
        # `SECTION_BLOCKED` 就会永久报错，产物无法自洽。跳过比较**不放松**阻断——该缺口的
        # `blocking_effects` 照样参与下面的 `has_section_blocking`，节级阻断仍然成立。
        if u.question_id:
            expected = SS.normalize_blocking_effects(q.blocking_policy) if q else ()
            if tuple(u.blocking_effects) != expected:
                _add(acc, "blocking_policy_mismatch", SEVERITY_BLOCKING,
                     f"unresolved:{u.unresolved_id}",
                     f"unresolved blocking_effects {tuple(u.blocking_effects)} 与问题 "
                     f"blocking_policy {expected} 不一致")
        if u.state == "CONFLICT":
            _add(acc, "conflict_unresolved", SEVERITY_BLOCKING,
                 f"unresolved:{u.unresolved_id}",
                 f"存在 CONFLICT 未解决项（多来源/口径冲突）：{u.detail}")
        if u.state == "WAITING_HUMAN":
            _add(acc, "waiting_human", SEVERITY_BLOCKING,
                 f"unresolved:{u.unresolved_id}",
                 f"问题需客户经理确认（WAITING_HUMAN），章节不能自动完成：{u.detail}")
        if "SECTION_BLOCKED" in u.blocking_effects or "JOB_BLOCKED" in u.blocking_effects:
            has_section_blocking = True
    # 断言的落点要与**状态派生**的唯一口径一致（`research_common.derive_status`：任一缺口
    # `WAITING_HUMAN` 优先于任何阻断等级，该状态本身也是阻断级）。只认 `SECTION_BLOCKED`
    # 会让「同时含 WAITING_HUMAN 与 SECTION_BLOCKED 缺口」的章节**永远**报错：派生只会给出
    # `WAITING_HUMAN`，而这条要求它必须是 `SECTION_BLOCKED`——两条口径互相矛盾，产物无解。
    # 要求因此收窄为「阻断级缺口必须反映成**某个**阻断状态」，`COMPLETED*` 仍然照样报错。
    if has_section_blocking and result.status not in ("SECTION_BLOCKED", "WAITING_HUMAN"):
        _add(acc, "status_not_blocked", SEVERITY_BLOCKING, "section",
             f"存在 SECTION_BLOCKED/JOB_BLOCKED 未解决项但章节 status={result.status!r}，"
             f"应为 SECTION_BLOCKED（或 WAITING_HUMAN）")


# ---------------------------------------------------------------------------
# 规则 1 — 契约覆盖（topic/question，rework）
# ---------------------------------------------------------------------------

def _rule_coverage(result: SS.SectionResult, task, acc: list) -> None:
    covered_topics = {c.topic_id for c in result.claims}
    unresolved_topics = {u.topic_id for u in result.unresolved}
    for tid in task.topic_ids:
        if tid not in covered_topics and tid not in unresolved_topics:
            _add(acc, "coverage_missing_topic", SEVERITY_REWORK, f"topic:{tid}",
                 f"主题 {tid} 无任何 claim 且无 unresolved",
                 target=_rework_target("topic", tid, "missing_topic"))
    covered_questions = {qid for c in result.claims for qid in c.question_ids}
    unresolved_questions = {u.question_id for u in result.unresolved if u.question_id}
    for q in task.questions:
        if q.question_id in covered_questions or q.question_id in unresolved_questions:
            continue
        _add(acc, "coverage_missing_question", SEVERITY_REWORK,
             f"question:{q.question_id}",
             f"问题 {q.question_id} 未被 claim 覆盖且无 unresolved",
             target=_rework_target("question", q.question_id, "missing_question"))


# ---------------------------------------------------------------------------
# 规则 2 — required aspect 覆盖（warning：字面可追溯，语义由 LLM Evaluator）
# ---------------------------------------------------------------------------

def _rule_aspects(result: SS.SectionResult, task, acc: list) -> None:
    by_question: dict[str, list[str]] = {}
    for c in result.claims:
        for qid in c.question_ids:
            by_question.setdefault(qid, []).append(c.text)
    for q in task.questions:
        if not q.required_aspects or q.question_id not in by_question:
            continue
        blob = " ".join(by_question[q.question_id])
        for aspect in q.required_aspects:
            if aspect and aspect not in blob:
                _add(acc, "aspect_uncovered", SEVERITY_WARNING,
                     f"question:{q.question_id}",
                     f"必需方面「{aspect}」未在覆盖 claim 正文中字面出现（语义覆盖由 LLM Evaluator 复核）")


# ---------------------------------------------------------------------------
# 规则 4 — 关键 claim 有 citation（rework；结构校验器已兜底，此处纵深）
# ---------------------------------------------------------------------------

def _rule_citation(result: SS.SectionResult, acc: list) -> None:
    for c in result.claims:
        if (c.claim_type in ("fact", "calculation")
                and not c.citation_refs and not c.derived_from_claim_ids):
            _add(acc, "claim_missing_citation", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"{c.claim_type} claim 无 citation 且无 derived_from_claim_ids",
                 target=_rework_target("claim", c.claim_id, "missing_citation"))


# ---------------------------------------------------------------------------
# 规则 5 — 引用可解析 + 锁定版本（rework；复用 citation_authority）
# ---------------------------------------------------------------------------

def _rule_resolvability(result: SS.SectionResult, acc: list, verdict_of: Callable) -> None:
    warned_types: set[str] = set()
    for c in result.claims:
        for ref in c.citation_refs:
            v = verdict_of(ref)
            if v.valid:
                continue
            reason = v.reason or "unknown"
            if reason == "authority_unavailable":
                if ref.ref_type not in warned_types:
                    warned_types.add(ref.ref_type)
                    _add(acc, "citation_authority_unavailable", SEVERITY_WARNING,
                         f"ref_type:{ref.ref_type}",
                         f"引用类型 {ref.ref_type} 的权威校验不可用（未提供对应库），"
                         f"由 LLM Evaluator 语义复核")
                continue
            _add(acc, "citation_unresolvable", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"引用不可解析（{reason}）：{SS.citation_identity(ref)}",
                 target=_rework_target("claim", c.claim_id, f"citation:{reason}"))


# ---------------------------------------------------------------------------
# 规则 6 — 财务数值 == 权威值（rework；复用 fact_pack 安全链，不新建解析器）
# ---------------------------------------------------------------------------

def _rule_financial(result: SS.SectionResult, acc: list, fact_pack) -> None:
    if fact_pack is None:
        return
    by_item: dict = {}
    by_metric: dict = {}
    for f in fact_pack.facts:
        if getattr(f, "kind", None) == "fact":
            by_item[(f.code, f.period)] = f
        else:
            by_metric[(f.code, f.period)] = f
    for c in result.claims:
        for ref in c.citation_refs:
            if ref.ref_type != "structured":
                continue
            fact = None
            if ref.formula_id:
                fact = by_metric.get((ref.formula_id, ref.period))
            elif ref.item_code:
                fact = by_item.get((ref.item_code, ref.period))
            if fact is None:
                _add(acc, "financial_unresolvable_value", SEVERITY_REWORK,
                     f"claim:{c.claim_id}",
                     f"structured 引用无法解析到权威值：{SS.citation_identity(ref)}",
                     target=_rework_target("claim", c.claim_id, "unresolvable_value"))
                continue
            if fact.display and fact.display not in c.text:
                _add(acc, "financial_value_mismatch", SEVERITY_REWORK,
                     f"claim:{c.claim_id}",
                     f"claim 正文缺少权威 display「{fact.display}」（{fact.code}@{fact.period}）",
                     target=_rework_target("claim", c.claim_id, "value_mismatch"))
            if NS.is_proxy_fact(fact) and _has_exactness(c.text):
                _add(acc, "proxy_claimed_exact", SEVERITY_REWORK, f"claim:{c.claim_id}",
                     f"代理口径指标 {fact.code} 被写成精确值",
                     target=_rework_target("claim", c.claim_id, "proxy_as_exact"))
            # §三 3.6：代理口径**必须显式标记**。上面那条只否证「把代理写成精确」；一条既不
            # 声称精确、也不写口径的断言，在读者眼里与受审的精确值没有区别 —— 缺的是**正面**
            # 要求：断言里必须逐字出现权威自己的口径限定语（`NS.proxy_qualifier`）。限定语措辞
            # 只来自权威（`status`/`note`），本规则不发明任何新术语，也不推断口径。
            if NS.is_proxy_fact(fact):
                qualifier = NS.proxy_qualifier(fact)
                if qualifier not in c.text:
                    _add(acc, "proxy_not_marked", SEVERITY_REWORK, f"claim:{c.claim_id}",
                         f"代理口径指标 {fact.code}@{fact.period} 的断言没有写出代理口径限定语"
                         f"「{qualifier}」：读者会把代理口径的数值读成受审的精确值",
                         target=_rework_target("claim", c.claim_id, "proxy_unmarked"))


_EXACTNESS_PHRASES = ("精确", "确切", "精确值", "准确", "权威精确")


def _has_exactness(text: str) -> bool:
    return any(p in text for p in _EXACTNESS_PHRASES)


# ---------------------------------------------------------------------------
# 规则 7 — external 引用完整性（warning：日期未知；rework：D 级唯一依据）
# ---------------------------------------------------------------------------

def _rule_external(result: SS.SectionResult, acc: list, verdict_of: Callable,
                   external_get: Callable | None) -> None:
    for c in result.claims:
        ext_refs = [r for r in c.citation_refs if r.ref_type == "external"]
        if not ext_refs:
            continue
        grades: list[str] = []
        for ref in ext_refs:
            v = verdict_of(ref)
            if "published_at_unknown" in v.warnings:
                _add(acc, "external_missing_date", SEVERITY_WARNING, f"claim:{c.claim_id}",
                     f"external 引用缺发布日期，不能支撑强时点结论")
            if external_get is not None:
                snap = external_get(ref.source_snapshot_id)
                g = getattr(snap, "source_grade", None)
                if g:
                    grades.append(g)
        if not grades:
            continue
        if c.impact_scope and all(g == "D" for g in grades):
            _add(acc, "d_source_sole_basis", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"核心结论（impact_scope={list(c.impact_scope)}）仅由 D 级来源支撑，"
                 f"不得作为关键结论唯一依据",
                 target=_rework_target("claim", c.claim_id, "d_source_sole_basis"))
        elif c.impact_scope and all(g == "C" for g in grades):
            _add(acc, "single_c_source", SEVERITY_WARNING, f"claim:{c.claim_id}",
                 f"核心结论仅由 C 级来源支撑，建议补充 A/B 级来源")


# ---------------------------------------------------------------------------
# 规则 8 — 「未发现/不存在」措辞（rework；缺证据 ≠ 事实不存在）
# ---------------------------------------------------------------------------

def _rule_absence(result: SS.SectionResult, acc: list) -> None:
    not_found_by_q: dict[str, set[str]] = {}
    for u in result.unresolved:
        if u.question_id:
            not_found_by_q.setdefault(u.question_id, set()).add(u.state)
    for c in result.claims:
        has_not_found = any(p in c.text for p in _NOT_FOUND_PHRASES)
        has_absence = any(p in c.text for p in _ABSENCE_PHRASES)
        if has_not_found and c.claim_type == "fact":
            _add(acc, "unfound_claimed_absent", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"fact claim 以事实语气断言「未发现/未检索到」，应写诚实缺口或降 inference",
                 target=_rework_target("claim", c.claim_id, "unfound_as_absent"))
        if has_absence:
            for qid in c.question_ids:
                if "NOT_FOUND_AFTER_SEARCH" in not_found_by_q.get(qid, set()):
                    _add(acc, "absence_contradicts_gap", SEVERITY_REWORK,
                         f"claim:{c.claim_id}",
                         f"claim 断言「不存在/未发生」但问题 {qid} 状态为 NOT_FOUND_AFTER_SEARCH"
                         f"（缺证据 ≠ 事实不存在）",
                         target=_rework_target("claim", c.claim_id, "absence_contradicts_gap"))
                    break


# ---------------------------------------------------------------------------
# 规则 9 — claim 类型与内容相符（rework）
# ---------------------------------------------------------------------------

def _rule_claim_type(result: SS.SectionResult, acc: list) -> None:
    for c in result.claims:
        if c.claim_type == "calculation" and not any(
                r.ref_type == "structured" for r in c.citation_refs):
            _add(acc, "calculation_missing_structured", SEVERITY_REWORK,
                 f"claim:{c.claim_id}",
                 f"calculation claim 无 structured 引用（计算值应来自结构化数据）",
                 target=_rework_target("claim", c.claim_id, "calculation_missing_structured"))
        if c.claim_type == "fact":
            for p in _HEDGE_PHRASES:
                if p in c.text:
                    _add(acc, "fact_hedged", SEVERITY_REWORK, f"claim:{c.claim_id}",
                         f"fact claim 含研判措辞「{p}」（应降 inference）",
                         target=_rework_target("claim", c.claim_id, "fact_hedged"))
                    break


# ---------------------------------------------------------------------------
# 规则 10 — 期间/scope/currency/单位一致（rework；scope/currency 由规则 5 权威校验）
# ---------------------------------------------------------------------------

def _rule_dimensions(result: SS.SectionResult, acc: list) -> None:
    for c in result.claims:
        snapshots = {r.snapshot_id for r in c.citation_refs
                     if r.ref_type == "structured" and r.snapshot_id}
        if len(snapshots) > 1:
            _add(acc, "dimension_mismatch", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"claim 混用多个快照 {sorted(snapshots)}（scope/currency/purpose 可能不一致）",
                 target=_rework_target("claim", c.claim_id, "dimension_mismatch"))


# ---------------------------------------------------------------------------
# 规则 11 — 非空标题/内容（blocking：空壳章节；rework：空壳 claim）
# ---------------------------------------------------------------------------

def _rule_emptiness(result: SS.SectionResult, acc: list) -> None:
    if not result.claims and not result.unresolved and not (result.markdown or "").strip():
        _add(acc, "empty_section", SEVERITY_BLOCKING, "section",
             "章节无 claim、无 unresolved、无 markdown（空壳）")
    for c in result.claims:
        if len((c.text or "").strip()) < 2:
            _add(acc, "empty_shell_claim", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"claim 内容为空壳（长度 {len((c.text or '').strip())}）",
                 target=_rework_target("claim", c.claim_id, "empty_shell_claim"))


# ---------------------------------------------------------------------------
# 规则 12 — 自创授信方案（rework；联合判定，非关键词封杀）
# ---------------------------------------------------------------------------

def _is_self_invented_scheme(text: str, has_citation: bool) -> bool:
    if not any(v in text for v in _RECOMMENDATION_VERBS):
        return False
    if not any(d in text for d in _SCHEME_DIMENSIONS):
        return False
    # 存量事实豁免：有引用且措辞为「现有/目前/存量」等既有事实，非新建议。
    if has_citation and any(f in text for f in _EXISTING_FRAMING):
        return False
    return True


def _rule_scheme(result: SS.SectionResult, acc: list, proposed_scheme) -> None:
    for c in result.claims:
        has_citation = bool(c.citation_refs)
        if _is_self_invented_scheme(c.text, has_citation):
            _add(acc, "self_invented_scheme", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"claim 含用户未提供的授信建议/评级（Phase 4 不产出 recommendation）",
                 target=_rework_target("claim", c.claim_id, "self_invented_scheme"))


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def _cached_validator(citation_authority) -> Callable:
    cache: dict = {}

    def verdict_of(ref) -> CitationVerdict:
        key = SS.citation_identity(ref)
        if key not in cache:
            try:
                cache[key] = citation_authority.validate(ref)
            except Exception as e:  # noqa: BLE001 - 权威查询异常 → fail-closed
                logger.exception("引用权威查询异常（fail-closed）")
                cache[key] = CitationVerdict(ref.ref_type, False,
                                             f"authority_query_failed:{type(e).__name__}")
        return cache[key]

    return verdict_of


def evaluate_section(result: SS.SectionResult, task, *, company_id: str = "",
                     citation_authority=None, fact_pack=None,
                     proposed_scheme=None, rule_accumulator: list | None = None) -> RulesVerdict:
    """对单个 SectionResult 执行 12 项确定性规则，返回 RulesVerdict。

    - citation_authority：``.validate(ref) -> CitationVerdict``（可选 ``.external_get``）。
    - fact_pack：``.facts`` 元素含 kind/code/period/display/status（财务章节注入）。
    - proposed_scheme：保留上下文（Phase 4 不产出 recommendation，规则 12 不做用户输入豁免）。
    - rule_accumulator：组合调用方（章级 narrative 评估）传入的同一份累加器。传入时，
      本函数把 ``(issue, target|None, is_blocking)`` 三元组就地并入该列表，使组合方
      **复用同一份规则实现**而不是另写一套（§八）。不传时行为与以往完全一致。
    """
    acc: list = [] if rule_accumulator is None else rule_accumulator

    _rule_blocking(result, task, acc)
    _rule_coverage(result, task, acc)
    _rule_aspects(result, task, acc)
    _rule_citation(result, acc)
    _rule_financial(result, acc, fact_pack)
    _rule_absence(result, acc)
    _rule_claim_type(result, acc)
    _rule_dimensions(result, acc)
    _rule_emptiness(result, acc)
    _rule_scheme(result, acc, proposed_scheme)

    if citation_authority is not None:
        verdict_of = _cached_validator(citation_authority)
        external_get = getattr(citation_authority, "external_get", None)
        _rule_resolvability(result, acc, verdict_of)
        _rule_external(result, acc, verdict_of, external_get)

    issues = tuple(i for i, _, _ in acc)
    rework_targets = tuple(t for _, t, _ in acc if t is not None)
    blocking = any(b for _, _, b in acc)

    covered_topics = {c.topic_id for c in result.claims}
    covered_questions = {qid for c in result.claims for qid in c.question_ids}
    rule_hits = dict(Counter(i.rule_id for i in issues))

    summary = {
        "rules_version": RULES_VERSION,
        "rules_passed": (not blocking) and (not rework_targets),
        "blocking": blocking,
        "issue_count": len(issues),
        "rework_target_count": len(rework_targets),
        "blocking_issue_count": sum(1 for i in issues if i.severity == SEVERITY_BLOCKING),
        "rework_issue_count": sum(1 for i in issues if i.severity == SEVERITY_REWORK),
        "warning_issue_count": sum(1 for i in issues if i.severity == SEVERITY_WARNING),
        "rule_hits": rule_hits,
        "covered_topics": sorted(covered_topics),
        "uncovered_topics": sorted(set(task.topic_ids) - covered_topics),
        "covered_questions": sorted(covered_questions),
        "uncovered_questions": sorted(q.question_id for q in task.questions
                                      if q.question_id not in covered_questions),
        "citation_authority_available": citation_authority is not None,
        "fact_pack_available": fact_pack is not None,
    }

    return RulesVerdict(
        rules_passed=summary["rules_passed"],
        blocking=blocking,
        issues=issues,
        rework_targets=rework_targets,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# CLI 自检（纯函数，注入假对象，不读库）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    from planning import schema as PS

    def _task(aspects=(), blocking=(), impact=()):
        return PS.SectionTask(
            task_id="task_sc", plan_id="plan_sc", section_id="financial",
            title="财务分析", purpose="p", research_policy="workflow",
            topic_ids=("t1", "t2"),
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

    def _claim(cid, topic_id, qids, text, ctype="fact", refs=()):
        return SS.LegacySectionClaimV1(claim_id=cid, section_id="financial", topic_id=topic_id,
                               question_ids=qids, text=text, claim_type=ctype,
                               citation_refs=refs)

    def _struct_ref(sid="S1", item="TOTAL_ASSETS", period="2025-12-31"):
        return SS.CitationRef(ref_type="structured", snapshot_id=sid,
                              item_code=item, period=period)

    def _result(task, claims, unresolved=(), status="COMPLETED", markdown="# 财务分析"):
        return SS.LegacySectionResultV1(
            section_result_id="sr_sc", section_version="secver_sc", task_id=task.task_id,
            section_id="financial", status=status, claims=tuple(claims),
            unresolved=tuple(unresolved), markdown=markdown)

    class _Fact:
        def __init__(self, kind, code, period, display, status="CALCULATED_EXACT", note=""):
            self.kind, self.code, self.period, self.display, self.status = \
                kind, code, period, display, status
            # 权威自己的口径说明（代理口径事实才非空）。这里的措辞也不代写：限定语逐字来自权威。
            self.note = note

    class _Pack:
        def __init__(self, facts):
            self.facts = facts

    class _Authority:
        """可配置假权威：fail 集合 → 不可解析；unknown 集合 → 缺日期警告。"""

        def __init__(self, fail=(), unknown=(), grades=None):
            self._fail = set(fail)
            self._unknown = set(unknown)
            self._grades = grades or {}

        def validate(self, ref):
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

    out: dict = {}
    checks: list[tuple[str, bool, str]] = []

    def check(name, cond, msg=""):
        checks.append((name, bool(cond), msg))
        out[name] = bool(cond)

    # 1) 合法章节通过。
    fact = _Fact("fact", "TOTAL_ASSETS", "2025-12-31", "1,234.56万元")
    task_ok = _task()
    ref_ok = _struct_ref(item="TOTAL_ASSETS")
    c_ok = _claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (ref_ok,))
    c_q2 = _claim("c2", "t2", ("q2",), "整体财务稳健", "inference", ())
    res_ok = _result(task_ok, [c_ok, c_q2])
    v = evaluate_section(res_ok, task_ok, citation_authority=_Authority(),
                         fact_pack=_Pack([fact]))
    check("happy_path_passes", v.rules_passed and not v.blocking,
          str([i.rule_id for i in v.issues]))

    # 2) 缺失 topic → coverage_missing_topic。
    c_q2 = _claim("c2", "t2", ("q2",), "某内容", "fact", (ref_ok,))
    res = _result(task_ok, [c_q2])
    v = evaluate_section(res, task_ok)
    check("missing_topic_detected", any(i.rule_id == "coverage_missing_topic" for i in v.issues))

    # 3) 缺失 question → coverage_missing_question。
    res = _result(task_ok, [c_ok])
    v = evaluate_section(res, task_ok)
    check("missing_question_detected",
          any(i.rule_id == "coverage_missing_question" for i in v.issues))

    # 4) aspect 未字面出现 → aspect_uncovered（warning，不阻断）。
    task_aspect = _task(aspects=("流动比率",))
    res = _result(task_aspect, [c_ok])
    v = evaluate_section(res, task_aspect)
    check("aspect_uncovered_warning",
          any(i.rule_id == "aspect_uncovered" and i.severity == "warning" for i in v.issues))

    # 5) CONFLICT 未解决 → conflict_unresolved（blocking）。
    u_conflict = SS.SectionUnresolved(
        unresolved_id="ur_conf", section_id="financial", topic_id="t1",
        question_id="q1", state="CONFLICT", reason_code="conflict_pause", detail="口径冲突")
    res = _result(task_ok, [c_ok], unresolved=[u_conflict], status="COMPLETED_WITH_GAPS")
    v = evaluate_section(res, task_ok)
    check("conflict_blocks", v.blocking and any(
        i.rule_id == "conflict_unresolved" for i in v.issues))

    # 6) fact 无 citation → claim_missing_citation。
    c_nocite = _claim("c3", "t1", ("q1",), "某事实", "fact", ())
    res = _result(task_ok, [c_nocite])
    v = evaluate_section(res, task_ok)
    check("missing_citation_detected",
          any(i.rule_id == "claim_missing_citation" for i in v.issues))

    # 7) 引用不可解析 → citation_unresolvable。
    res = _result(task_ok, [c_ok])
    v = evaluate_section(res, task_ok,
                         citation_authority=_Authority(fail={SS.citation_identity(ref_ok)}))
    check("unresolvable_citation_detected",
          any(i.rule_id == "citation_unresolvable" for i in v.issues))

    # 8) 财务值不等 → financial_value_mismatch。
    res = _result(task_ok, [c_ok])  # 正文 "1,234.56万元" 与 fact display 不符时用另一 fact
    fact2 = _Fact("fact", "TOTAL_ASSETS", "2025-12-31", "9,999万元")
    v = evaluate_section(res, task_ok, citation_authority=_Authority(),
                         fact_pack=_Pack([fact2]))
    check("financial_value_mismatch_detected",
          any(i.rule_id == "financial_value_mismatch" for i in v.issues))

    # 9) proxy 写成精确 → proxy_claimed_exact。
    fact_proxy = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                       status="CALCULATED_PROXY")
    ref_metric = SS.CitationRef(ref_type="structured", snapshot_id="S1",
                                formula_id="SOLV_CURRENT_RATIO", formula_version="v1",
                                period="2025-12-31")
    c_proxy = _claim("c4", "t1", ("q1",), "流动比率精确值为 1.50", "calculation", (ref_metric,))
    res = _result(task_ok, [c_proxy])
    v = evaluate_section(res, task_ok, citation_authority=_Authority(),
                         fact_pack=_Pack([fact_proxy]))
    check("proxy_claimed_exact_detected",
          any(i.rule_id == "proxy_claimed_exact" for i in v.issues))

    # 10) 3.6 的**正面**要求：代理口径事实的断言没写出权威自己的口径限定语 →
    #     proxy_not_marked。上面那条只否证「把代理写成精确」，这条说的是「什么都没写」。
    fact_proxy_note = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                            status="CALCULATED_PROXY", note="代理口径（PROXY_INPUT）")
    c_unmarked = _claim("c5", "t1", ("q1",), "流动比率为 1.50", "calculation", (ref_metric,))
    v = evaluate_section(_result(task_ok, [c_unmarked]), task_ok,
                         citation_authority=_Authority(), fact_pack=_Pack([fact_proxy_note]))
    check("proxy_not_marked_detected",
          any(i.rule_id == "proxy_not_marked" for i in v.issues),
          str([i.rule_id for i in v.issues]))

    # 11) 写出限定语（逐字，与权威 note 同一份措辞）→ 不再出 proxy_not_marked。没有这条
    #     正例，规则就变成「代理口径的断言永远不可能通过」——那是拒绝而不是要求。
    c_marked = _claim("c6", "t1", ("q1",), "流动比率为 1.50，代理口径（PROXY_INPUT）",
                      "calculation", (ref_metric,))
    v = evaluate_section(_result(task_ok, [c_marked]), task_ok,
                         citation_authority=_Authority(), fact_pack=_Pack([fact_proxy_note]))
    check("proxy_marked_passes",
          not any(i.rule_id == "proxy_not_marked" for i in v.issues),
          str([i.rule_id for i in v.issues]))

    # 12) 反例：精确口径的事实**不得**被要求写口径标记（正面要求只对代理事实成立，
    #     给精确值加限定语是另一类错误，不在这条规则里被奖励）。
    v = evaluate_section(_result(task_ok, [c_unmarked]), task_ok,
                         citation_authority=_Authority(),
                         fact_pack=_Pack([_Fact("calculation", "SOLV_CURRENT_RATIO",
                                                "2025-12-31", "1.50",
                                                status="CALCULATED_EXACT")]))
    check("exact_fact_needs_no_proxy_marker",
          not any(i.rule_id == "proxy_not_marked" for i in v.issues),
          str([i.rule_id for i in v.issues]))

    # 10) external 缺日期 → external_missing_date（warning）。
    ref_ext = SS.CitationRef(ref_type="external", source_snapshot_id="x1")
    c_ext = _claim("c5", "t1", ("q1",), "行业可比数据", "fact", (ref_ext,))
    res = _result(task_ok, [c_ext])
    v = evaluate_section(res, task_ok,
                         citation_authority=_Authority(unknown={SS.citation_identity(ref_ext)}))
    check("external_missing_date_warning",
          any(i.rule_id == "external_missing_date" and i.severity == "warning"
              for i in v.issues))

    # 11) 「未发现」写成事实 → unfound_claimed_absent。
    c_unfound = _claim("c6", "t1", ("q1",), "未发现重大诉讼", "fact", (ref_ok,))
    res = _result(task_ok, [c_unfound])
    v = evaluate_section(res, task_ok)
    check("unfound_claimed_absent_detected",
          any(i.rule_id == "unfound_claimed_absent" for i in v.issues))

    # 12) calculation 无 structured → calculation_missing_structured。
    c_calc = _claim("c7", "t1", ("q1",), "流动比率良好", "calculation", (ref_ext,))
    res = _result(task_ok, [c_calc])
    v = evaluate_section(res, task_ok)
    check("calculation_missing_structured_detected",
          any(i.rule_id == "calculation_missing_structured" for i in v.issues))

    # 13) fact 含研判 → fact_hedged。
    c_hedged = _claim("c8", "t1", ("q1",), "预计未来营收增长", "fact", (ref_ok,))
    res = _result(task_ok, [c_hedged])
    v = evaluate_section(res, task_ok)
    check("fact_hedged_detected", any(i.rule_id == "fact_hedged" for i in v.issues))

    # 14) 混用快照 → dimension_mismatch。
    ref_s2 = _struct_ref(sid="S2", item="TOTAL_ASSETS")
    c_mix = _claim("c9", "t1", ("q1",), "总资产对比", "fact", (ref_ok, ref_s2))
    res = _result(task_ok, [c_mix])
    v = evaluate_section(res, task_ok)
    check("dimension_mismatch_detected",
          any(i.rule_id == "dimension_mismatch" for i in v.issues))

    # 15) 空壳章节 → empty_section（blocking）。
    res_empty = _result(task_ok, [], markdown="")
    v = evaluate_section(res_empty, task_ok)
    check("empty_section_blocks", v.blocking and any(
        i.rule_id == "empty_section" for i in v.issues))

    # 16) 自创授信方案 → self_invented_scheme；存量事实豁免。
    c_scheme = _claim("c10", "t1", ("q1",), "建议授信额度 3 亿元，期限 3 年", "fact", ())
    res = _result(task_ok, [c_scheme])
    v = evaluate_section(res, task_ok)
    check("self_invented_scheme_detected",
          any(i.rule_id == "self_invented_scheme" for i in v.issues))
    c_existing = _claim("c11", "t1", ("q1",), "公司现有授信额度 20 亿元", "fact", (ref_ok,))
    res = _result(task_ok, [c_existing])
    v = evaluate_section(res, task_ok)
    check("existing_scheme_exempted",
          not any(i.rule_id == "self_invented_scheme" for i in v.issues))

    # 17) WAITING_HUMAN → blocking。
    u_wh = SS.SectionUnresolved(
        unresolved_id="ur_wh", section_id="financial", topic_id="t1",
        question_id="q1", state="WAITING_HUMAN", reason_code="transfer_human",
        detail="需确认", blocking_effects=())
    res = _result(task_ok, [c_ok], unresolved=[u_wh], status="COMPLETED_WITH_GAPS")
    v = evaluate_section(res, task_ok)
    check("waiting_human_blocks", v.blocking and any(
        i.rule_id == "waiting_human" for i in v.issues))

    return out


# ---------------------------------------------------------------------------
# M930-3 §六：章节级 narrative 评估（确定性规则 + 至多一次 LLM evaluator）
# ---------------------------------------------------------------------------

def _narrative_schema() -> Any:
    """延迟导入 `sections.narrative_schema`（模块级导入会与本模块形成环）。"""
    import sections.narrative_schema as NS

    return NS


#: narrative 章节级规则版本（进入 evaluation_id 派生）。
#:
#: §八/§四 9：章级评估复用 `evaluate_section` 的同一份 12 项规则，因此规则版本**只能有一个
#: 权威定义**——即 `sections.narrative_schema.NARRATIVE_RULES_VERSION`。这里不再另写字面量：
#: 两处各写一份正是「第二套版本」漂移的来源（旧产物会带着一个只属于写入侧的版本号被组装）。
NARRATIVE_RULES_VERSION = _narrative_schema().NARRATIVE_RULES_VERSION
#: 允许注入的 LLM evaluator prompt（复用已有 section_evaluator 资产）。
NARRATIVE_EVALUATOR_PROMPT = "section_evaluator"
#: LLM evaluator 只允许输出这三档严重度，且不得覆盖确定性结论。
_LLM_SEVERITIES = (SEVERITY_BLOCKING, SEVERITY_REWORK, SEVERITY_WARNING)
_LLM_ANSWER_KEYS = ("issues",)
_LLM_ISSUE_KEYS = ("rule_id", "severity", "location", "detail", "suggested_action")
#: 缺口状态里出现这些原始状态，就说明该缺口**没有被改写**成完整结论。
_NON_COVERED = NS.NON_COVERED_ASPECT_STATUSES


@dataclass(frozen=True)
class NarrativeEvaluationOutcome:
    """本章节级评估结论 + Draft/Gate/Evaluation/Result 四者身份绑定。"""

    evaluation: SS.SectionEvaluation
    binding: Any
    llm_evaluator_calls: int
    summary: dict


def _narrative_support_rules(draft: Any, acceptance: Any | None, acc: list) -> None:
    """`support_semantics` 分支（P11）：支撑边的**角色**必须显式声明且不得被误用。

    判定只在拿到 accepted binding 束（P10 的 `DraftAcceptance`）时做角色感知检查。门槛是
    「门前 draft 有没有 proposal」而不是「调用方传没传束」：带 proposal 的 current draft
    必然走过机械门与语义门，所以「有 proposal 却没有束」只能说明链没跑完 —— 此时
    fail-closed，不得把「没核验」读成「核验通过」（P1-B）。
    """
    import sections.narrative_schema as NS

    # §三 G：形状判断必须**显式**，不得用 `getattr(draft, "proposed_support_refs", ())` 那种
    # 「取不到就当空」的写法——它会把「current draft 缺字段」也读成「历史 draft，没有 proposal」，
    # 于是本规则静默失效。current 对象按**严格字段访问**读取；只有确认是历史视图时才无事可判。
    if isinstance(draft, NS.SectionDraft):
        proposal_count = len(tuple(draft.proposed_support_refs))
        candidates = tuple(draft.claim_candidates)
    else:
        proposal_count = 0
        candidates = ()
    if acceptance is None:
        if proposal_count:
            _add(acc, "narrative_support_bindings_absent", SEVERITY_BLOCKING, draft.draft_id,
                 f"门前 draft 有 {proposal_count} 条支撑 proposal 却没有 accepted binding 束："
                 "支撑语义尚未被机械门/语义门核验，不得据此评估或放行")
        return

    # 1. 角色与主体错配：factual 只允许 `claim_candidate`，context 只允许
    #    `narrative_draft_unit`；context 边不得携带任何事实身份（否则它就在授权事实）。
    for binding in acceptance.accepted_bindings:
        semantics = getattr(binding, "support_semantics", None)
        kind = getattr(binding, "binding_subject_kind", None)
        binding_id = getattr(binding, "accepted_support_binding_id", "")
        if semantics == "factual" and kind != "claim_candidate":
            _add(acc, "narrative_support_semantics_subject_mismatch", SEVERITY_BLOCKING,
                 binding_id, f"factual 支撑边的主体是 {kind!r}：factual 只允许 claim_candidate")
        if semantics == "context":
            if kind != "narrative_draft_unit":
                _add(acc, "narrative_support_semantics_subject_mismatch", SEVERITY_BLOCKING,
                     binding_id,
                     f"context 支撑边的主体是 {kind!r}：context 只允许 narrative_draft_unit")
            carried = sorted(name for name in ("fact_id", "financial_fact_id", "note_fact_id",
                                              "external_fact_id")
                             if getattr(binding, name, None))
            if carried:
                _add(acc, "narrative_context_binding_carries_fact", SEVERITY_BLOCKING,
                     binding_id,
                     f"context 支撑边携带事实身份 {carried}：context 不得授权任何事实")

    # 2. 被拒 subject 不得被静默丢弃：机械/语义门的拒绝是**结论**，必须以 blocking 显式
    #    处理（此处不伪造 ReworkTarget —— 门前没有 SectionClaim ID 可作 target）。
    for rejected in acceptance.rejected_subjects:
        _add(acc, "narrative_support_subject_rejected", SEVERITY_BLOCKING,
             f"{rejected.subject_kind}:{rejected.subject_id}",
             f"{rejected.stage} 门拒绝了该支撑主体（{list(rejected.reason_codes)}）："
             "被拒的支撑不得进入正文，也不得被悄悄略过")

    # 3. 每个事实性候选必须**恰好**有一束 factual accepted binding：未被接受的候选不构成
    #    正文事实来源（「没有边」是缺口，不是通过）。
    bound: dict[str, int] = {}
    for binding in acceptance.accepted_bindings:
        if getattr(binding, "support_semantics", None) == "factual":
            key = getattr(binding, "binding_subject_id", "")
            bound[key] = bound.get(key, 0) + 1
    for candidate in candidates:
        if not bound.get(candidate.candidate_id):
            _add(acc, "narrative_claim_candidate_unbound", SEVERITY_BLOCKING,
                 candidate.candidate_id,
                 "候选没有任何 factual accepted binding：未被接受的候选不得成为正文事实来源")


def _narrative_rules(result: SS.SectionResult, draft: Any, authority: Any, acc: list,
                     *, gate: Any | None = None, acceptance: Any | None = None,
                     narrative: Any | None = None,
                     claim_dispositions: Sequence[Any] = ()) -> None:
    """确定性 narrative 规则（只读；不检索、不改正文、不放行）。

    `gate` 由调用方传入**当前重算**的硬门结果（§八 3）。缺省时本函数自行重算——
    任何情况下都**不**使用调用方（`outcome`）带来的那份门结论：旧门可能是在正文/缺口
    被改动之前算出来的，把它当依据等于让被篡改的产物自证清白。

    `acceptance`（可选）是 P10 的 accepted binding 束：传入时启用 `support_semantics`
    角色感知规则；不传时对**带 proposal 的**门前 draft fail-closed（见
    `_narrative_support_rules`），对不带 proposal 的历史 ab-1 draft 则无事可判。

    `narrative` 是**门后**的 final `SectionNarrative`（narr-5）。§三 C 明确要求本评估器
    消费 final Narrative，**不再**读 `draft.paragraphs`：门前候选束在 narr-4 之后根本没有
    正文字段，而 `getattr(draft, "paragraphs", ())` 会把「没有正文可判」读成「正文全部合格」
    ——那是本文件里最后一个把缺失字段变成空集合通过的写法。缺 final Narrative 现在一律
    fail-closed（blocking），因为章级评估的对象就是这份正文。
    """
    import sections.narrative_schema as NS

    if gate is None:
        # narr-4：门**只**吃门前候选束 + 权威输入（`gate_draft(bundle, authority_input)`）。
        # 定稿 Claim 是门**后**身份，把它回灌进门既不可能也不合法（§0.13）。
        gate = NS.gate_draft(draft, authority)
    if gate.blocking:
        _add(acc, "narrative_gate_blocking", SEVERITY_BLOCKING, draft.draft_id,
             "narrative 硬门仍有 blocking issue：" + json.dumps(
                 [i.rule_id for i in gate.issues if i.severity == "blocking"],
                 ensure_ascii=False))

    # 正文必须由 Claim 承重，且高风险表面逐字可追溯：**五条判据的唯一实现**在
    # `NS.verify_section_narrative` 里，本评估器只是又一个调用点（组织器在发出前自检、
    # 组装器在读回后复算，这里是评估时的第三次复算）。三处共用同一函数，口径不可能分叉。
    if narrative is None:
        _add(acc, "narrative_final_narrative_absent", SEVERITY_BLOCKING, result.section_id,
             "章级评估必须拿到门后 final Narrative（narr-5）：门前候选束没有正文，"
             "「调用方没给正文」不得被读成「正文没有问题」")
    else:
        # context 的**已接受**集合只能来自 accepted binding 束：没有束就没有「已接受」，
        # 此时句级 context 引用一条也过不了（verifier 的判据是「必须在已接受集合内」）。
        accepted_context = (
            () if acceptance is None else tuple(sorted(
                str(b.accepted_support_binding_id)
                for b in acceptance.accepted_bindings
                if str(b.support_semantics) == "context")))
        try:
            NS.verify_section_narrative(
                narrative=narrative, claims=tuple(result.claims),
                accepted_context_binding_ids=accepted_context,
                dispositions=tuple(claim_dispositions or ()))
        except NS.NarrativeSchemaError as e:
            _add(acc, "narrative_final_narrative_invalid", SEVERITY_BLOCKING,
                 narrative.narrative_id, f"final Narrative 未通过门后确定性核验：{e}")
        # 表格同样只在**门后**存在：narr-4 之后门前候选束没有 `tables`，因此这条判据的
        # 对象是 final Narrative 的表格（此前读 `getattr(draft, "tables", ())` 恒为空，
        # 等于表格永远「没有问题」——那正是 §三 G 要禁的缺失字段变空集合）。
        for table in tuple(narrative.tables):
            for name in ("entity_scope", "unit", "period"):
                if not str(getattr(table, name, "") or "").strip():
                    _add(acc, "narrative_table_scope_missing", SEVERITY_REWORK,
                         table.table_id, f"表 {table.caption} 缺 {name}")
            for row in table.rows:
                if not row.claim_ids and not row.non_factual_reason:
                    _add(acc, "narrative_table_row_unbound", SEVERITY_BLOCKING,
                         row.row_id, "表格行既无 Claim 也未标注为非事实展示行")

    # 缺口不得被静默丢弃，也不得被改写成完整结论。
    declared = set(draft.unresolved_ids)
    in_result = {u.unresolved_id for u in result.unresolved}
    dropped = sorted(declared - in_result)
    if dropped:
        _add(acc, "narrative_gap_dropped", SEVERITY_BLOCKING, ",".join(dropped[:4]),
             "draft 登记的缺口未出现在 canonical SectionResult.unresolved 中")
    for projection in draft.unresolved_projections:
        raw = str(projection.get("authority_status") or "")
        if raw and raw not in _NON_COVERED:
            continue
        detail = str(projection.get("detail") or "")
        if raw and raw not in detail:
            _add(acc, "narrative_gap_rewritten", SEVERITY_BLOCKING,
                 str(projection.get("unresolved_id")),
                 f"缺口文本未保留权威原始状态 {raw!r}（缺口不得被改写）")

    _narrative_support_rules(draft, acceptance, acc)


def _llm_narrative_issues(text: str) -> tuple[list[SS.SectionIssue], str]:
    """解析 LLM evaluator 输出；只接受严格结构，任何越权内容一律丢弃。

    返回 ``(issues, malformed_reason)``：`malformed_reason` 非空表示这次调用**没有**产出
    可用结论。此时调用方必须记一条 blocking issue（§九），而不是把它当成「评估通过」——
    「模型没回答」与「模型回答没问题」在审计上不能等价。
    """
    if not isinstance(text, str) or not text.strip():
        return [], "empty_output"
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return [], "not_json"
    if not isinstance(payload, dict):
        return [], f"not_object:{type(payload).__name__}"
    unknown = sorted(set(payload) - set(_LLM_ANSWER_KEYS))
    if unknown:
        return [], f"unknown_answer_keys:{unknown}"
    raw_items = payload.get("issues")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        return [], f"issues_not_list:{type(raw_items).__name__}"
    issues: list[SS.SectionIssue] = []
    dropped: list[str] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            dropped.append(f"#{index}:not_object")
            continue
        extra = sorted(set(raw) - set(_LLM_ISSUE_KEYS))
        if extra:
            dropped.append(f"#{index}:unknown_keys:{extra}")
            continue
        severity = str(raw.get("severity") or "")
        rule_id = str(raw.get("rule_id") or "")
        detail = str(raw.get("detail") or "")
        if severity not in _LLM_SEVERITIES or not rule_id or not detail:
            dropped.append(f"#{index}:invalid_fields")
            continue
        # LLM issue 一律加前缀，保证与确定性规则 id 不可能混淆；不产生 ReworkTarget。
        location = str(raw.get("location") or "")
        issues.append(SS.SectionIssue(
            issue_id=SS.derive_issue_id(f"llm_{rule_id}", location, detail, severity),
            rule_id=f"llm_{rule_id}", severity=severity, location=location, detail=detail,
            suggested_action=str(raw.get("suggested_action") or "")))
    if dropped:
        # 部分畸形同样算「未给出可用结论」：静默丢弃会让审计无法区分「模型没说」与「被吃掉」。
        return issues, "malformed_issues:" + ";".join(dropped[:4])
    return issues, ""


def _llm_output_fingerprint(text: Any) -> str:
    """LLM 原始输出的内容指纹（可追溯，不落原文）。"""
    if not isinstance(text, str):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate_narrative_section(outcome: Any, *, task: Any, authority: Any,
                               llm_client: Any | None = None,
                               allow_llm_evaluator: bool = False,
                               allow_targeted_rework: bool = False,
                               evaluated_at: str = "",
                               citation_authority: Any | None = None,
                               fact_pack: Any | None = None,
                               acceptance: Any | None = None) -> NarrativeEvaluationOutcome:
    """§六/§八 章节级评估：正式章级规则 → narrative 附加规则 →（可选，至多一次）LLM。

    边界：本函数不检索、不改正文、不补研究；它**不是**最终放行者，也不产出任何
    Assurance / release 结论。确定性规则失败不得被 LLM 覆盖。

    §八：这里的「正式章级规则」就是 `evaluate_section` 本身 —— 调用方把同一个累加器传进去，
    narrative 附加规则并入同一份 `acc`。章级评估因此不会退化成一套更弱的第二实现。
    """
    import sections.narrative_schema as NS

    draft = outcome.draft
    result = outcome.section_result
    # §三 C：章级评估的正文对象是**门后** final Narrative（narr-5）。缺少它一律 fail-closed
    # （在 `_narrative_rules` 里记 blocking），不得退回到读 `draft.paragraphs`。
    narrative = outcome.narrative
    claim_dispositions = outcome.claim_narrative_dispositions
    acc: list = []
    evaluate_section(result, task, company_id=getattr(draft, "company_id", "") or "",
                     citation_authority=citation_authority, fact_pack=fact_pack,
                     rule_accumulator=acc)
    section_rule_issue_count = len(acc)
    # §八 3：硬门**每次使用重新计算**。`outcome.gate_result` 是调用方带来的产物字段，
    # 它可能是在当前正文/缺口/投影之前算出来的（或被直接伪造）；只有沿 authority 重算出来的
    # 门才有资格作为依据，也只有这份重算结果可以被写进 Binding。
    gate = NS.gate_draft(draft, authority)
    supplied_gate = outcome.gate_result
    supplied_gate_id = str(supplied_gate.gate_result_id or "")
    if supplied_gate_id != gate.gate_result_id:
        _add(acc, "narrative_gate_stale", SEVERITY_BLOCKING, draft.draft_id,
             f"调用方带来的硬门结论 {supplied_gate_id or '(缺失)'} 与当前重算的 "
             f"{gate.gate_result_id} 不一致：门必须在正文/缺口/投影的当前状态上重新计算，"
             "旧门不得作为评估或放行依据")
    _narrative_rules(result, draft, authority, acc, gate=gate, acceptance=acceptance,
                     narrative=narrative, claim_dispositions=claim_dispositions)

    rules_blocking = any(b for _, _, b in acc)
    llm_issues: list[SS.SectionIssue] = []
    llm_calls = 0
    llm_output_fingerprint = ""
    llm_status = "not_requested"
    llm_malformed = False
    llm_call_meta: dict = {}
    if allow_llm_evaluator and not rules_blocking:
        if llm_client is None:
            raise ValueError("allow_llm_evaluator=True 但未提供 llm_client")
        try:
            from llm import client as _llmc
            system = _llmc.load_prompt(NARRATIVE_EVALUATOR_PROMPT)
        except Exception as e:  # noqa: BLE001 — prompt 资产缺失时 fail-closed，不发明、不空跑
            logger.exception("LLM evaluator prompt 资产加载失败（fail-closed）")
            llm_status = f"prompt_unavailable:{type(e).__name__}"
            _add(acc, "narrative_evaluator_prompt_unavailable", SEVERITY_BLOCKING,
                 result.section_id,
                 f"LLM evaluator prompt 资产 {NARRATIVE_EVALUATOR_PROMPT!r} 无法加载"
                 f"（{type(e).__name__}）：章级评估不得在无 prompt 的情况下空跑，"
                 "也不得据此放行")
            # 已记 blocking；把 client 置空以**不发起**这次调用（system 保持 None 作哨兵）。
            llm_client = None
    if allow_llm_evaluator and llm_client is not None and not rules_blocking:
        # 输入面给的是**门后 final Narrative 的实际句**（含它声明的 Claim / citation /
        # context 绑定），而不是门前 draft 的任何字段：评估器要评的就是这份正文，把门前
        # 候选态喂给它会让「评的正文」与「发布的正文」变成两个对象。
        messages = [{"role": "user", "content": json.dumps({
            "section_id": result.section_id, "claims": [
                {"claim_id": c.claim_id, "text": c.text} for c in result.claims],
            "paragraphs": [{
                "topic_ids": list(p.topic_ids),
                "sentences": [{"text": s.text, "sentence_kind": s.sentence_kind,
                               "claim_ids": list(s.claim_ids),
                               "citation_ids": list(s.citation_ids),
                               "context_binding_ids": list(s.context_binding_ids)}
                              for s in p.sentences]} for p in narrative.paragraphs]
            if narrative is not None else [],
            "tables": [{"caption": t.caption, "row_count": len(t.rows)}
                       for t in (narrative.tables if narrative is not None else ())],
            "claim_dispositions": [{"claim_id": d.claim_id, "disposition": d.disposition,
                                    "reason_code": d.reason_code}
                                   for d in claim_dispositions],
            "gaps": [dict(x) for x in draft.unresolved_projections],
            "output_schema": {"issues": [{"rule_id": "...", "severity": "rework|warning",
                                          "location": "...", "detail": "..."}]},
        }, ensure_ascii=False)}]
        call = llm_client.narrate(messages=messages, system=system,
                                  prompt_version=NARRATIVE_EVALUATOR_PROMPT,
                                  model_policy="evaluator")
        # 调用已经发生：计数必须记这次调用，而不是记「解析成功的次数」。
        llm_calls = 1
        # §五 1：评估器与写作器共用同一套结构化结果；调用元数据必须原样留存（不得只留文本）。
        if hasattr(call, "to_dict") and not isinstance(call, str):
            llm_call_meta = dict(call.to_dict())
            text = str(getattr(call, "text", "") or "")
            call_status = str(getattr(call, "status", "") or "")
        else:
            llm_call_meta = {"status": "malformed",
                             "error": "llm_client 未返回结构化 NarrationResult"}
            text = str(call)
            call_status = "malformed"
        llm_output_fingerprint = _llm_output_fingerprint(text)
        llm_issues, malformed = _llm_narrative_issues(text)
        if call_status != "ok":
            # 调用本身失败/未返回结构化结果：与「输出畸形」同一处置——不得当作「没问题」。
            malformed = malformed or f"llm_call_status={call_status!r}"
        if malformed:
            logger.warning("LLM evaluator 输出不可用：%s", malformed)
            llm_status = malformed
            llm_malformed = True
            _add(acc, "narrative_evaluator_output_malformed", SEVERITY_BLOCKING,
                 result.section_id,
                 "LLM evaluator 未给出可用结论"
                 f"（{malformed}，输出指纹 {llm_output_fingerprint[:16]}）："
                 "「模型没回答」不得等价于「模型回答没问题」")
        else:
            llm_status = "ok"

    issues = tuple(i for i, _, _ in acc) + tuple(llm_issues)
    # §九/§十五 13：LLM 只能**加**问题。它给出的 blocking 结论必须同样阻断——不能因为
    # 「确定性规则这一条没过问」就让它落空，也不能被降级成 PASS/PASS_WITH_GAPS。
    # 注意 `acc` 在此刻还包含 LLM 环节**自己补记**的阻断（畸形输出、prompt 资产缺失），
    # 因此必须重新扫一遍 acc，不能用 LLM 调用前的那份快照（那会把这些阻断丢掉）。
    deterministic_blocking = any(b for _, _, b in acc)
    llm_blocking = any(i.severity == SEVERITY_BLOCKING for i in llm_issues)
    blocking = deterministic_blocking or llm_blocking
    rework_targets: tuple[SS.ReworkTarget, ...] = ()
    if not blocking:
        needs_rework = any(i.severity == SEVERITY_REWORK for i in issues)
        if needs_rework and allow_targeted_rework:
            first = next(i for i in issues if i.severity == SEVERITY_REWORK)
            # §六：**至多一个**定向返工目标——plan 未显式允许时一个也不给。
            rework_targets = (_rework_target(
                "topic", first.location, f"章节级定向返工：{first.rule_id}"),)

    gaps_present = bool(draft.unresolved_ids)
    if blocking:
        decision = "BLOCKED"
    elif rework_targets or any(i.severity == SEVERITY_REWORK for i in issues):
        decision = "REWORK"
    elif gaps_present:
        decision = "PASS_WITH_GAPS"
    else:
        decision = "PASS"
    rules_passed = not deterministic_blocking
    # LLM 只能**加**问题、不能清问题：`llm_passed` 只看这次调用是否给出 blocking 结论，
    # 且确定性 blocking 独立成立，不受 llm_passed 影响。
    llm_passed = None
    if llm_calls:
        # §五 8：畸形/失败的输出**不是**「没有发现问题」。`llm_passed` 只在「调用成功、输出
        # 可用、且模型自己没报 blocking」时才为真；否则一律 False（绝不为 None/True）。
        llm_passed = (not llm_malformed) and (llm_status == "ok") and not any(
            i.severity == SEVERITY_BLOCKING and i.rule_id.startswith("llm_")
            for i in llm_issues)
    if not llm_calls and llm_status.startswith("prompt_unavailable"):
        llm_passed = False
    prompt_version = NARRATIVE_EVALUATOR_PROMPT if llm_calls else ""
    evaluation = SS.SectionEvaluation(
        evaluation_id=SS.derive_evaluation_id(
            result.section_result_id, decision, NARRATIVE_RULES_VERSION, prompt_version,
            issues, rework_targets, llm_calls),
        section_result_id=result.section_result_id, rules_version=NARRATIVE_RULES_VERSION,
        evaluator_prompt_version=prompt_version, rules_passed=rules_passed,
        llm_passed=llm_passed, decision=decision, issues=issues,
        rework_targets=rework_targets, evaluated_at=evaluated_at,
        llm_evaluator_calls=llm_calls)
    binding = NS.NarrativeEvaluationBinding.create(
        section_result_id=result.section_result_id, section_draft_id=draft.draft_id,
        evaluation_id=evaluation.evaluation_id,
        narrative_gate_result_id=gate.gate_result_id,
        rules_version=NARRATIVE_RULES_VERSION, gate_version=NS.NARRATIVE_GATE_VERSION)
    summary = {
        "rules_version": NARRATIVE_RULES_VERSION, "decision": decision,
        "rules_passed": rules_passed, "blocking": blocking,
        # 阻断来源必须可审计：确定性规则阻断、LLM 阻断、或两者同时。
        # （LLM 环节自身的程序性阻断——畸形输出/prompt 缺失——按设计记进 `acc`，
        # 因此归入 rules 侧，并可由 rule_id 与 `llm_status` 精确区分。）
        "blocking_source": "+".join(
            name for name, hit in (("rules", deterministic_blocking), ("llm", llm_blocking))
            if hit) or "none",
        "issue_count": len(issues), "rework_target_count": len(rework_targets),
        "blocking_issue_count": sum(1 for i in issues if i.severity == SEVERITY_BLOCKING),
        "rework_issue_count": sum(1 for i in issues if i.severity == SEVERITY_REWORK),
        "section_rule_issue_count": section_rule_issue_count,
        "narrative_rule_issue_count": len(issues) - section_rule_issue_count,
        "section_rules_source": "sections.rules_evaluator.evaluate_section"
                                f"（{RULES_VERSION}，与叙事评估同一份实现）",
        "llm_evaluator_calls": llm_calls,
        "llm_status": llm_status,
        "llm_passed": llm_passed,
        "llm_issue_count": len(llm_issues),
        "llm_output_fingerprint": llm_output_fingerprint,
        "llm_call": dict(llm_call_meta),
        "gaps_present": gaps_present,
        # §八 3：门是本次调用**重新计算**的，且必须与调用方带来的那份一致才算通过。
        "narrative_gate_result_id": gate.gate_result_id,
        "narrative_gate_recomputed": True,
        "narrative_gate_supplied_matched": supplied_gate_id == gate.gate_result_id,
        "narrative_gate_blocking": bool(gate.blocking),
        "rule_hits": dict(Counter(i.rule_id for i in issues)),
        "scope": "chapter_level_only（非最终放行者，不产出 Assurance/release 结论）",
    }
    return NarrativeEvaluationOutcome(evaluation=evaluation, binding=binding,
                                      llm_evaluator_calls=llm_calls, summary=summary)


# ---------------------------------------------------------------------------
# P11：factual 链**唯一**协调者（只编排 P8 → P9 → P10，不重建它们）
# ---------------------------------------------------------------------------

#: 本模块只**启用**（re-export）两个门的版本常量：机械门与语义门的版本字面量各自唯一定义在
#: `narrative_schema`，协调者不得再写第三份字面量（版本漂移的唯一来源就是「多写一份」）。
CLAIM_BINDING_GATE_VERSION: str = CBG.CLAIM_BINDING_GATE_VERSION
CLAIM_ENTAILMENT_RULES_VERSION: str = CEE.CLAIM_ENTAILMENT_RULES_VERSION

#: 链结果取值：任一 subject 被拒即整链 fail；零 subject 也算 fail（P1-B：不得以「无期望」放行）。
CLAIM_CHAIN_RESULTS: tuple[str, ...] = ("pass", "fail")


@dataclass(frozen=True)
class ClaimChainOutcome:
    """一条 Draft 的门后链结论：决定 + accepted binding + 显式拒绝 + 链级结论。

    `result="fail"` 时 `accepted_bindings` **不**被清空：通过 subject 的接受记录是真实事实，
    清空它们会丢掉可审计信息；链级结论由 `result`/`failure_reasons` 承担，消费方（组装器）
    必须检查 `result`，不得只看 binding 是否存在。
    """

    draft_id: str
    section_id: str
    draft_revision: str
    manifest_id: str
    manifest_fingerprint: str
    aggregate_decisions: tuple[Any, ...]
    entailment_decisions: tuple[Any, ...]
    acceptance: Any
    result: str
    failure_reasons: tuple[str, ...]
    llm_calls: int

    def __post_init__(self) -> None:
        if self.result not in CLAIM_CHAIN_RESULTS:
            raise ValueError(f"ClaimChainOutcome.result={self.result!r} 不在 {CLAIM_CHAIN_RESULTS}")

    @property
    def accepted_bindings(self) -> tuple[Any, ...]:
        return tuple(self.acceptance.accepted_bindings)

    @property
    def rejected_subjects(self) -> tuple[Any, ...]:
        return tuple(self.acceptance.rejected_subjects)

    def bindings_for_subject(self, subject_kind: str, subject_id: str) -> tuple[Any, ...]:
        """后继侧按 subject 取引用（反向引用由 Claim / final Narrative 侧持有）。"""
        return AB.bindings_for_subject(self.acceptance, subject_kind, subject_id)

    def summary(self) -> dict:
        return {
            "result": self.result, "failure_reasons": list(self.failure_reasons),
            "draft_id": self.draft_id, "draft_revision": self.draft_revision,
            "aggregate_decision_count": len(self.aggregate_decisions),
            "entailment_decision_count": len(self.entailment_decisions),
            "accepted_binding_count": len(self.accepted_bindings),
            "rejected_subject_count": len(self.rejected_subjects),
            "llm_calls": self.llm_calls,
            "binding_gate_version": CLAIM_BINDING_GATE_VERSION,
            "claim_entailment_rules_version": CLAIM_ENTAILMENT_RULES_VERSION,
            "scope": "claim_chain_only（不组装、不放行、不产出 Assurance 结论）",
        }


def evaluate_claim_chain(candidate_bundle: Any, authority: Any, *,
                         manifest: Any = None, llm_client: Any | None = None,
                         material_context: Any = None,
                         model_policy: str = CEE.CLAIM_ENTAILMENT_MODEL_POLICY,
                         on_call: Callable[[Any], None] | None = None) -> ClaimChainOutcome:
    """Draft 束 → 机械门（P8）→ 语义门（P9）→ accepted binding（P10）。

    边界（越界即缺陷）：本函数**只编排**。它不判语义、不重实现 cardinality/digest 断言、不
    另找材料、不写库、不组装最终报告，也**不**把 LLM 的结论升级或覆盖掉机械门的结论。

    `llm_client` 参数存在只是为了依赖注入，**不是**「可跳过语义门」的开关：缺省为 `None` 时
    本函数 fail-closed 抛出——一条「已接受但没有语义决定」的支撑边不具备证据意义。

    fail-closed：集合级问题（空/重复/非 canonical 顺序）、digest 不一致、资产漂移、传输失败
    一律由被调用的门**抛出**（本函数不吞、不转译）；可表达的失败（逐边失败、语义 rejected）
    形成 `result="fail"` + 显式 `failure_reasons`，绝不静默丢弃。
    """
    draft = candidate_bundle
    if not isinstance(draft, NS.SectionDraft):
        raise ValueError("evaluate_claim_chain 的 candidate_bundle 必须是 SectionDraft（draft 束）")
    if llm_client is None:
        raise ValueError(
            "evaluate_claim_chain 必须注入 llm_client：语义门不得被跳过"
            "（缺省 None 不是「无需核验」的开关）")
    manifest = manifest if manifest is not None else draft.material_manifest
    # §三 A.1/A.6：只拿到「材料 ID 清单」时链不得继续。本节确实没有 Pack 材料（空 manifest）
    # 是合法状态；但 manifest 非空却没有已解析正文上下文，意味着机械门与语义门都会替一份
    # 它们从未见过的正文背书，必须在**任何门运行之前**拒绝。
    if manifest.entries and material_context is None:
        raise ValueError(
            "evaluate_claim_chain：本节 manifest 非空，但组合根没有注入 wmctx-1 材料正文"
            "上下文：绑定门与语义门不得在看不到真实正文的情况下运行"
            "（「只有 material ID」不是可降级的输入形态）")

    aggregate_decisions = CBG.decide_draft_bindings(
        draft, authority, manifest=manifest, material_context=material_context)
    bundles = CEE.factual_candidate_revisions(draft, authority, manifest=manifest,
                                              material_context=material_context)
    calls: list[str] = []

    def _observe(bundle: Any) -> None:
        calls.append(bundle.candidate.candidate_id)
        if on_call is not None:
            on_call(bundle)

    entailment_decisions = CEE.evaluate_entailments(
        bundles, aggregate_decisions, manifest=manifest, llm_client=llm_client,
        model_policy=model_policy, on_call=_observe)
    acceptance = AB.accept_draft_bindings(draft, aggregate_decisions,
                                         entailment_decisions=entailment_decisions,
                                         manifest=manifest)

    failure_reasons: list[str] = []
    if not acceptance.subject_keys:
        failure_reasons.append("no_binding_subject：本节的 subject 宇宙为空（没有候选也没有"
                               "草稿单元），不得以「无期望」为由放行")
    for rejected in acceptance.rejected_subjects:
        failure_reasons.append(
            f"{rejected.stage}:{rejected.subject_kind}:{rejected.subject_id}:"
            f"{'|'.join(rejected.reason_codes)}")
    for decision in aggregate_decisions:
        if decision.result != "pass":
            code = ",".join(sorted({e.reason_code for e in decision.edge_results
                                    if e.result != "pass"}))
            failure_reasons.append(
                f"aggregate_fail:{decision.subject_kind}:{decision.subject_id}:{code}")
    return ClaimChainOutcome(
        draft_id=draft.draft_id, section_id=draft.section_id,
        draft_revision=draft.draft_revision, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(),
        aggregate_decisions=tuple(aggregate_decisions),
        entailment_decisions=tuple(entailment_decisions), acceptance=acceptance,
        result="pass" if not failure_reasons else "fail",
        failure_reasons=tuple(sorted(set(failure_reasons))), llm_calls=len(calls))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.rules_evaluator",
        description="Phase 4 章节 Rules Evaluator（12 项确定性规则）自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        result = _self_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        all_ok = all(result.values())
        print("\nself-check:", "PASS" if all_ok else "FAIL")
        return 0 if all_ok else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())

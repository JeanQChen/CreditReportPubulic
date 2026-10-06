"""M930-3 数字**逐值**链条台账（读侧现算，`cvt-1`）。

为什么要有这个模块
==================

逐句核对能说出「这一句的数字没有合格依据」（`numeric_basis_not_qualified`），但它说的是
**一句话**。同一句话里往往有四个数字，而它们的来路可以完全不同；报告面、缺口面、事实面
三处各说一个数，谁也说不清某个具体数值**卡在哪一步**。

本模块把链条摊平成一列**逐值**的行：每一个数字表面，从「材料里有没有它」一直走到
「正文写没写它」，取**最先命中的那一步**为结论，并把每一步的证据一起留下。

*这是读侧台账，不是判据*：它不改任何机械结论、不新增 wire 字段、不进任何身份体。逐句的
判定仍然只有 `sections/sentence_check.py` 一处；本模块只是把它的数字轴**按值**拆开解释。

七态（`cvt-1` 的封闭词表）
==========================

从**正文**往回走，第一个命中的就是结论：

* :data:`STAGE_WRITTEN` —— 正文写了它。此时再单独记一条**授权轴**（`authorization`，见下）：
  写了，不代表授权到了。
* :data:`STAGE_DELIVERED_NOT_WRITTEN` —— 它是**已送达**的权威事实里的数字，但正文没写。
* :data:`STAGE_QUALIFIED_NOT_DELIVERED` —— 研究侧形成了合格事实，却没进 Writer 的事实读视图。
  有 typed 排除记录（期间门等）就一并给出原因；**没有**记录时如实写「无记录」，不猜。
* :data:`STAGE_CANDIDATE_REJECTED` —— 有候选命题含它，资格决定 `rejected`，带 typed
  `rejection_reason`。
* :data:`STAGE_NO_CANDIDATE` —— 材料里逐字有它，但没有任何候选命题含它。
* :data:`STAGE_ABSENT_FROM_MATERIAL_SPANS` —— 送达材料里**根本没有**它。
* :data:`STAGE_NOT_OBSERVABLE` —— 上游（候选 / 资格 / 合格事实）在**本次产物里读不到**。

后三态需要研究侧对象（`FactCandidate` / `FactQualificationDecision` / `SupportedFact`）。真实
运行现场这三样都在内存里（`scripts/run_m930_3_cited_chain.py` 的 `inputs.topic_results`）；而
**已落盘的**历史 run 目录只留下清单与正文，`topic_results` 不在其中。那种情况下本模块**不**
把「读不到」写成「没有」：整份报告标 `upstream_observed=False`，逐值取
:data:`STAGE_NOT_OBSERVABLE`。这正是「材料原文里有多少个数字」与「有多少条被拒的候选」**必须
分开报**的那条边界（§0.20：`Pack` 原文有数字，不等于有这么多条被拒事实）。

授权轴（与 `sentence_check` **同一判据**）
=========================================

:data:`AUTHORIZATION_BASES` 四档，与 `sentence_check` 的数字轴逐字对应：

* ``qualified_fact``：被引**合格事实**的文本逐字覆盖它（路径 A）。
* ``table_cell_source``：落在被引**表材料**的某一格（原值 + 单位对上）——它说明「这个数字
  出自这张表的这一格、格级位置在」，**不**表示它取得了任何权威身份。
* ``material_surface_only``：只在被引**普通材料**原文里逐字出现过。金额与比率走到这一档，
  正是 `numeric_qualification` 要拦的那件事。
* ``none``：被引来源里没有任何一处逐字有它。

四个档位靠的是仓内**同一批原语**（`NS.scan_numeric_tokens` / `NS.authorized_numeric_tokens`
/ `NS.numeric_token_authorized` / `SC.is_qualified_numeric_surface` / `SC.table_cell_matches` /
`SC.table_declared_unit`），本模块一个判据都不另写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from sections import narrative_schema as NS
from sections import sentence_check as SC

__all__ = [
    "VALUE_TRACE_POLICY_VERSION",
    "VALUE_TRACE_STAGES",
    "VALUE_STAGE_LABELS",
    "AUTHORIZATION_BASES",
    "CitedValueTraceError",
    "CitedValueTraceRow",
    "CitedValueTraceReport",
    "draft_sentences",
    "material_numeric_surfaces",
    "trace_cited_values",
    "render_value_trace_lines",
]

#: 台账政策版本。改任何一个封闭词表（阶段、授权档位）或改判定顺序都要一起升版。
VALUE_TRACE_POLICY_VERSION = "cvt-1"

STAGE_WRITTEN = "written"
STAGE_DELIVERED_NOT_WRITTEN = "delivered_not_written"
STAGE_QUALIFIED_NOT_DELIVERED = "qualified_not_delivered"
STAGE_CANDIDATE_REJECTED = "candidate_rejected"
STAGE_NO_CANDIDATE = "no_candidate"
STAGE_ABSENT_FROM_MATERIAL_SPANS = "absent_from_material_spans"
STAGE_NOT_OBSERVABLE = "not_observable"

#: 逐值结论的**封闭**取值。判定顺序即列表顺序（从正文往回走，第一个命中即结论）。
VALUE_TRACE_STAGES = (
    STAGE_WRITTEN,
    STAGE_DELIVERED_NOT_WRITTEN,
    STAGE_QUALIFIED_NOT_DELIVERED,
    STAGE_CANDIDATE_REJECTED,
    STAGE_NO_CANDIDATE,
    STAGE_ABSENT_FROM_MATERIAL_SPANS,
    STAGE_NOT_OBSERVABLE,
)

VALUE_STAGE_LABELS = {
    STAGE_WRITTEN: "正文写了",
    STAGE_DELIVERED_NOT_WRITTEN: "已送达权威事实、正文未写",
    STAGE_QUALIFIED_NOT_DELIVERED: "已合格、未送达 Writer 读视图",
    STAGE_CANDIDATE_REJECTED: "有候选、资格决定拒绝",
    STAGE_NO_CANDIDATE: "材料里有、未形成任何候选",
    STAGE_ABSENT_FROM_MATERIAL_SPANS: "送达材料里没有（来源缺口）",
    STAGE_NOT_OBSERVABLE: "本产物读不到上游（不写成没有）",
}

#: 授权轴（与 `sentence_check` 数字轴同一判据，取值逐字对应）。
AUTHORIZATION_BASES = (
    "qualified_fact",
    "table_cell_source",
    "material_surface_only",
    "none",
)


class CitedValueTraceError(Exception):
    """台账输入不完整或自相矛盾（fail-closed，不猜）。"""


@dataclass(frozen=True)
class CitedValueTraceRow:
    """一个数字表面的**一行**台账。"""

    #: `NS.scan_numeric_tokens` 给出的 token（单位已算进 token，如「41.85%」「541 亿元」）。
    value: str
    stage: str
    #: 这一档是**凭什么**判出来的（封闭短码，供人读与审计）。
    stage_basis: str
    #: 正文是否写了它（= `stage == STAGE_WRITTEN` 的判据本身，单独留一列便于筛）。
    in_draft: bool
    #: 是否属于「必须拿到合格事实或格级来源才可授权」的那一类表面（金额 / 比率）。
    requires_qualified_basis: bool
    authorization: str
    #: 写了它的句子 id（按草稿顺序）。
    sentence_ids: tuple[str, ...] = ()
    #: 这些句子引用到的 citation_key（材料与事实共用一个命名空间，按出现顺序去重）。
    citation_keys: tuple[str, ...] = ()
    #: 逐字含它的**送达材料**（按清单顺序；表材料不进这一列——表数字走格）。
    material_keys: tuple[str, ...] = ()
    #: 逐字含它的**已送达清单事实**的键。
    delivered_fact_keys: tuple[str, ...] = ()
    #: 研究侧含它的候选 id（读不到上游时为空）。
    candidate_ids: tuple[str, ...] = ()
    qualification_decision_id: str = ""
    rejection_reason: str = ""
    #: `qualified_not_delivered` 时的 typed 排除原因；**空串表示没有记录**，不是「没被排除」。
    delivery_exclusion_reason: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.stage not in VALUE_TRACE_STAGES:
            raise CitedValueTraceError(
                f"CitedValueTraceRow.stage={self.stage!r} 不在封闭词表 {list(VALUE_TRACE_STAGES)} 内")
        if self.authorization not in AUTHORIZATION_BASES:
            raise CitedValueTraceError(
                f"CitedValueTraceRow.authorization={self.authorization!r} "
                f"不在封闭词表 {list(AUTHORIZATION_BASES)} 内")
        if self.stage == STAGE_WRITTEN and not self.sentence_ids:
            raise CitedValueTraceError(
                f"value={self.value!r} 记成 {STAGE_WRITTEN} 却没有任何句子 id")

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "stage": self.stage,
            "stage_basis": self.stage_basis,
            "in_draft": self.in_draft,
            "requires_qualified_basis": self.requires_qualified_basis,
            "authorization": self.authorization,
            "sentence_ids": list(self.sentence_ids),
            "citation_keys": list(self.citation_keys),
            "material_keys": list(self.material_keys),
            "delivered_fact_keys": list(self.delivered_fact_keys),
            "candidate_ids": list(self.candidate_ids),
            "qualification_decision_id": self.qualification_decision_id,
            "rejection_reason": self.rejection_reason,
            "delivery_exclusion_reason": self.delivery_exclusion_reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CitedValueTraceReport:
    """一整节数字的逐值台账（读侧现算，不进任何 wire）。"""

    policy_version: str
    #: 上游（候选 / 资格 / 合格事实）这次**在不在**输入里。**不是**「有没有内容」。
    upstream_observed: bool
    #: 目标值的来源（`draft_numeric_tokens` / `material_numeric_surfaces` / `caller_named`）。
    target_sources: tuple[str, ...]
    values: tuple[CitedValueTraceRow, ...]
    #: 目标判定的输入规模（供人读「命中率」，不是判据）。
    draft_sentence_count: int = 0
    material_count: int = 0
    fact_count: int = 0

    def stage_counts(self) -> dict[str, int]:
        """逐档计数（**封闭键**：每一档都给，没有的记 0，不留缺口也不合并档）。"""
        out = {stage: 0 for stage in VALUE_TRACE_STAGES}
        for row in self.values:
            out[row.stage] += 1
        return out

    def authorization_counts(self) -> dict[str, int]:
        out = {basis: 0 for basis in AUTHORIZATION_BASES}
        for row in self.values:
            out[row.authorization] += 1
        return out

    def rows_for(self, value: str) -> tuple[CitedValueTraceRow, ...]:
        """按表面**逐字**取行（只做千分位/空白归一，与 `NS.numeric_token_authorized` 同判据）。"""
        return tuple(r for r in self.values if NS.numeric_token_authorized(value, [r.value]))

    def to_dict(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "upstream_observed": self.upstream_observed,
            "target_sources": list(self.target_sources),
            "draft_sentence_count": self.draft_sentence_count,
            "material_count": self.material_count,
            "fact_count": self.fact_count,
            "stage_counts": self.stage_counts(),
            "authorization_counts": self.authorization_counts(),
            "values": [r.to_dict() for r in self.values],
        }


def draft_sentences(draft: Any) -> tuple[Any, ...]:
    """把草稿摊平成句子序列（小节 → 段落 → 句子），顺序即正文顺序。

    duck-typing 与 `sentence_check` 走的是同一份草稿形状；缺字段的层级**跳过**而不是猜一个
    默认对象——摊不出句子的草稿不值得下一层结论。
    """
    out: list[Any] = []
    for sub in (getattr(draft, "subsections", ()) or ()):
        for para in (getattr(sub, "paragraphs", ()) or ()):
            for sent in (getattr(para, "sentences", ()) or ()):
                out.append(sent)
    return tuple(out)


def material_numeric_surfaces(
    materials: Sequence[Any], *, qualified_only: bool = True
) -> dict[str, tuple[str, ...]]:
    """送达材料里逐字出现的数字表面 → 含它的 `citation_key`（按清单顺序）。

    `qualified_only=True` 只收**金额 / 比率**那一类（`SC.is_qualified_numeric_surface`）：
    普通数量（「10 万台」「27 个」）按 §0.20 照常引用，收进来只会把台账淹掉。表材料
    （`structured_view` 非空）**不进**本表——表数字的授权走格，不走压平正文。
    """
    out: dict[str, list[str]] = {}
    for material in materials:
        if getattr(material, "structured_view", None) is not None:
            continue
        view = str(getattr(material, "reading_view", "") or "")
        if not view:
            continue
        key = str(getattr(material, "citation_key", "") or "")
        for token in NS.scan_numeric_tokens(view):
            if qualified_only and not SC.is_qualified_numeric_surface(token, view):
                continue
            out.setdefault(token, []).append(key)
    return {tok: tuple(dict.fromkeys(keys)) for tok, keys in out.items()}


def _delivery_exclusions(scan: Any) -> dict[str, str]:
    """`AuthorityScan` 里的逐 fact 排除原因（`fact_id → 原因码`）。

    `period_exclusions` 的每条是带 `fact_id` / `reason` 的 dict（期间门等）；`excluded_facts`
    是 `(container, fact_id, topic_id)` **无原因码**的三元组，按「`excluded_by_container_scope`」
    记——它是确定的另一种排除，不冒充有原因的那一种。
    """
    out: dict[str, str] = {}
    if scan is None:
        return out
    for row in (tuple(getattr(scan, "period_exclusions", ()) or ())):
        fid = str(row.get("fact_id", "") or "")
        if fid:
            out.setdefault(fid, str(row.get("reason", "") or "unspecified"))
    for container, fact_id, _topic in (tuple(getattr(scan, "excluded_facts", ()) or ())):
        out.setdefault(str(fact_id), f"excluded_by_container_scope:{container}")
    return out


@dataclass(frozen=True)
class _Upstream:
    """研究侧对象的只读索引（`token → 候选 / 资格决定 / 合格事实`）。

    `observed` 说的是「研究侧对象这次**有没有被交进来**」，与 `by_value` 空不空**无关**：
    交进来了但一个候选都没有（`no_candidate` 的正常形状）必须与「压根没交」分开——否则
    「读不到」会把「确实没有候选」吞掉，那正是本模块最不能犯的错。
    """

    by_value: Mapping[str, dict[str, tuple[Any, ...]]]
    observed: bool = False


def _upstream_index(topic_results: Any, research_facts: Sequence[Any] = ()) -> _Upstream:
    """从 `topic_results` 与 Pack 里的合格事实建索引。取不到属性就**跳过**——形状不认识时如实
    退成「读不到」，不把解析失败说成「没有候选」。

    `research_facts` 单独进来，是因为二者**不在同一处**：候选与资格决定挂在
    `TopicRuntimeResult` 上，而**合格事实**（`SupportedFact`）在 Pack 里（`VerifiedPackSet.
    packs[*].facts`）——`TopicRuntimeResult` 只带候选与决定。少了这一路，「已合格但未送达」
    这一档永远判不出来。
    """
    results: list[Any] = []
    if topic_results:
        if isinstance(topic_results, Mapping):
            results = [v for v in topic_results.values() if v is not None]
        else:
            results = [topic_results]
    if not results and not research_facts:
        return _Upstream(by_value={})

    cands: dict[str, list[Any]] = {}
    facts: dict[str, list[Any]] = {}
    decisions: dict[str, Any] = {}
    for result in results:
        if isinstance(result, (tuple, list)):
            nested = list(result)
        else:
            nested = [result]
        for item in nested:
            for cand in (getattr(item, "fact_candidates", ()) or ()):
                statement = str(getattr(cand, "statement", "") or "")
                for token in NS.scan_numeric_tokens(statement):
                    cands.setdefault(token, []).append(cand)
            for fact in (getattr(item, "supported_facts", ()) or ()):
                text = str(getattr(fact, "text", "") or "")
                for token in NS.scan_numeric_tokens(text):
                    facts.setdefault(token, []).append(fact)
            for dec in (getattr(item, "fact_qualification_decisions", ()) or ()):
                decisions[str(getattr(dec, "candidate_id", "") or "")] = dec
    for fact in research_facts:
        text = str(getattr(fact, "text", "") or "")
        for token in NS.scan_numeric_tokens(text):
            facts.setdefault(token, []).append(fact)

    by_value: dict[str, dict[str, tuple[Any, ...]]] = {}
    for token in set(cands) | set(facts):
        entry_cands = tuple(cands.get(token, ()))
        by_value[token] = {
            "candidates": entry_cands,
            "facts": tuple(facts.get(token, ())),
            "decisions": tuple(d for d in (
                decisions.get(str(getattr(c, "candidate_id", "") or "")) for c in entry_cands)
                if d is not None),
        }
    return _Upstream(by_value=by_value, observed=True)


def trace_cited_values(
    *,
    draft: Any,
    materials: Sequence[Any],
    facts: Sequence[Any],
    scan: Any = None,
    topic_results: Any = None,
    research_facts: Sequence[Any] = (),
    targets: Iterable[str] | None = None,
    targets_are_caller_named: bool = False,
) -> CitedValueTraceReport:
    """**唯一**入口：把一节正文与它的输入摊成逐值台账。

    * `draft` / `materials` / `facts`：草稿、清单材料、清单事实（本链**已送达** Writer 的两样）。
    * `scan`：`pack_writer.AuthorityScan`（可选）。给了就能说清「合格了但被排除」的原因。
    * `topic_results`：`{section_id: TopicRuntimeResult}` / 单个结果 / 结果元组（可选）。给了
      才有候选与资格决定。
    * `research_facts`：Pack 里的**合格事实**（`SupportedFact`，`VerifiedPackSet.packs[*].facts`）。
      它与 `topic_results` 是两处：候选与决定挂在运行时结果上，合格事实在 Pack 里。少了它，
      「已合格但未送达」这一档判不出来。
    * `targets`：人工点名的表面。**不**给时目标由**本链自己的产物**决定——正文里写了的数字
      （句子自带的 `numeric_tokens`）∪ 材料里的金额 / 比率表面。两处的目标都由运行现场算
      出来，**不写任何公司、页码、关键词专用清单**。

    只读：不修改任何入参，不写盘。
    """
    sentences = draft_sentences(draft)
    draft_tokens: list[str] = []
    for sent in sentences:
        scanned = tuple(getattr(sent, "numeric_tokens", ()) or ()) or \
            NS.scan_numeric_tokens(str(getattr(sent, "text", "") or ""))
        for token in scanned:
            if str(token) not in draft_tokens:
                draft_tokens.append(str(token))

    surfaces = material_numeric_surfaces(materials, qualified_only=True)
    sources: list[str] = []
    if targets is not None:
        ordered: list[str] = []
        for token in targets:
            token = str(token)
            if token and not _has_equivalent(ordered, token):
                ordered.append(token)
        sources.append("caller_named" if targets_are_caller_named else "caller_supplied")
    else:
        ordered = list(draft_tokens)
        for token in surfaces:
            if not _has_equivalent(ordered, token):
                ordered.append(token)
        if draft_tokens:
            sources.append("draft_numeric_tokens")
        if surfaces:
            sources.append("material_numeric_surfaces")

    upstream = _upstream_index(topic_results, research_facts)
    exclusions = _delivery_exclusions(scan)

    non_table = [m for m in materials if getattr(m, "structured_view", None) is None]
    table_views = [m for m in materials if getattr(m, "structured_view", None) is not None]
    material_token_sets = {
        str(getattr(m, "citation_key", "") or ""):
            NS.authorized_numeric_tokens([str(getattr(m, "reading_view", "") or "")])
        for m in non_table}
    fact_token_sets = {
        str(getattr(f, "citation_key", "") or ""):
            NS.authorized_numeric_tokens([str(getattr(f, "text", "") or "")])
        for f in facts}
    sentence_views = [
        (str(getattr(s, "sentence_id", "") or ""),
         tuple(str(c) for c in (getattr(s, "citations", ()) or ())),
         NS.authorized_numeric_tokens([str(getattr(s, "text", "") or "")]),
         str(getattr(s, "text", "") or ""))
        for s in sentences]

    rows = tuple(
        _trace_one(
            value=value, sentence_views=sentence_views, non_table=non_table,
            table_views=table_views, material_token_sets=material_token_sets,
            fact_token_sets=fact_token_sets, upstream=upstream, exclusions=exclusions,
            upstream_observed=upstream.observed)
        for value in ordered)
    return CitedValueTraceReport(
        policy_version=VALUE_TRACE_POLICY_VERSION,
        upstream_observed=upstream.observed,
        target_sources=tuple(sources),
        values=rows,
        draft_sentence_count=len(sentences),
        material_count=len(materials),
        fact_count=len(facts),
    )


def _has_equivalent(existing: Sequence[str], token: str) -> bool:
    """`existing` 里有没有**与 `token` 逐字等价**的一个（同一等价面见下）。

    等价面**不另立一份**：直接问 `NS.numeric_token_authorized` 两个方向是否互相成立——它判的
    正是「千分位与空白的排版归一」。PDF 文字层把「1.2 个百分点」写成一个空格，正文写
    「1.2个百分点」，二者在授权轴上**就是同一个数**；不合并就会让台账凭空多出一行，把
    「材料里有 50 个数字」这类读数再吹大一次。
    """
    return any(NS.numeric_token_authorized(token, [other])
               and NS.numeric_token_authorized(other, [token]) for other in existing)


def _covered(value: str, token_sets: Mapping[str, frozenset[str]]) -> tuple[str, ...]:
    """哪些键的 token 集合逐字覆盖了这个值（按给定映射的顺序）。"""
    return tuple(key for key, tokens in token_sets.items()
                 if NS.numeric_token_authorized(value, tokens))


def _table_hit(value: str, cited_materials: Sequence[Any]) -> bool:
    """该值是不是**任意一张被引表材料**的某一格（判据全部来自 `sentence_check`）。"""
    for material in cited_materials:
        view = getattr(material, "structured_view", None)
        if view is None:
            continue
        cells, _problems = SC.table_cells(view)
        unit = SC.table_declared_unit(view)
        for cell in cells:
            if SC.table_cell_matches(value, cell, unit):
                return True
    return False


def _authorization(*, value: str, in_draft: bool, sentence_views: Sequence[tuple],
                   material_token_sets: Mapping[str, frozenset[str]],
                   fact_token_sets: Mapping[str, frozenset[str]],
                   cited_materials: Sequence[Any]) -> str:
    """写了它的那些句子里，**被引来源**给了它哪一档授权（与 `sentence_check` 同序）。"""
    if not in_draft:
        return "none"
    cited_keys = {key for _sid, keys, _tokens, _text in sentence_views
                  if NS.numeric_token_authorized(value, _tokens) for key in keys}
    if any(NS.numeric_token_authorized(value, fact_token_sets[k])
           for k in cited_keys if k in fact_token_sets):
        return "qualified_fact"
    if _table_hit(value, cited_materials):
        return "table_cell_source"
    if any(NS.numeric_token_authorized(value, material_token_sets[k])
           for k in cited_keys if k in material_token_sets):
        return "material_surface_only"
    return "none"


def _trace_one(*, value: str, sentence_views: Sequence[tuple], non_table: Sequence[Any],
               table_views: Sequence[Any], material_token_sets: Mapping[str, frozenset[str]],
               fact_token_sets: Mapping[str, frozenset[str]], upstream: _Upstream,
               exclusions: Mapping[str, str], upstream_observed: bool) -> CitedValueTraceRow:
    """一个表面的判定（顺序**就是** `VALUE_TRACE_STAGES` 的顺序）。"""
    sentence_ids = tuple(sid for sid, _keys, tokens, _text in sentence_views
                         if NS.numeric_token_authorized(value, tokens))
    in_draft = bool(sentence_ids)
    cited_text = ""
    citation_keys: list[str] = []
    for _sid, keys, tokens, text in sentence_views:
        if not NS.numeric_token_authorized(value, tokens):
            continue
        cited_text = cited_text or text
        for key in keys:
            if key not in citation_keys:
                citation_keys.append(key)
    material_keys = _covered(value, material_token_sets)
    delivered_fact_keys = _covered(value, fact_token_sets)
    cited_materials = [m for m in list(non_table) + list(table_views)
                       if str(getattr(m, "citation_key", "") or "") in set(citation_keys)]
    authorization = _authorization(
        value=value, in_draft=in_draft, sentence_views=sentence_views,
        material_token_sets=material_token_sets, fact_token_sets=fact_token_sets,
        cited_materials=cited_materials)

    entry = upstream.by_value.get(value, {"candidates": (), "facts": (), "decisions": ()})
    qualified_facts = tuple(
        f for f in entry["facts"]
        if NS.numeric_token_authorized(
            value, NS.authorized_numeric_tokens([str(getattr(f, "text", "") or "")])))
    rejected = tuple(d for d in entry["decisions"]
                     if str(getattr(d, "verdict", "") or "") == "rejected")

    if in_draft:
        stage, basis = STAGE_WRITTEN, "draft_sentence_carries_value"
    elif delivered_fact_keys:
        stage, basis = STAGE_DELIVERED_NOT_WRITTEN, "delivered_fact_carries_value"
    elif not upstream_observed:
        stage, basis = STAGE_NOT_OBSERVABLE, "upstream_objects_absent_from_artifact"
    elif qualified_facts:
        stage, basis = STAGE_QUALIFIED_NOT_DELIVERED, "supported_fact_carries_value"
    elif entry["candidates"] and rejected:
        stage, basis = STAGE_CANDIDATE_REJECTED, "qualification_decision_rejected"
    elif material_keys:
        stage, basis = STAGE_NO_CANDIDATE, "material_carries_value_no_candidate"
    else:
        stage, basis = STAGE_ABSENT_FROM_MATERIAL_SPANS, "no_material_span_carries_value"

    rejection_reason = ""
    decision_id = ""
    if rejected:
        decision_id = str(getattr(rejected[0], "decision_id", "") or "")
        rejection_reason = str(getattr(rejected[0], "rejection_reason", "") or "")
    exclusion_reason = ""
    if stage == STAGE_QUALIFIED_NOT_DELIVERED:
        for fact in qualified_facts:
            fid = str(getattr(fact, "fact_id", "") or "")
            if fid in exclusions:
                exclusion_reason = exclusions[fid]
                break

    #: 「金额/比率」这一类要看**句子原文**：裸缩放词（「… 千」）后面紧不紧接着「元」，只有在
    #: 原句里才看得出来（见 `SC.is_qualified_numeric_surface`）。没写进正文的值按保守一侧判。
    requires = SC.is_qualified_numeric_surface(value, cited_text)
    return CitedValueTraceRow(
        value=value, stage=stage, stage_basis=basis, in_draft=in_draft,
        requires_qualified_basis=requires, authorization=authorization,
        sentence_ids=sentence_ids, citation_keys=tuple(citation_keys),
        material_keys=material_keys, delivered_fact_keys=delivered_fact_keys,
        candidate_ids=tuple(str(getattr(c, "candidate_id", "") or "")
                            for c in entry["candidates"]),
        qualification_decision_id=decision_id, rejection_reason=rejection_reason,
        delivery_exclusion_reason=exclusion_reason,
        detail=_detail(stage=stage, authorization=authorization, requires=requires,
                       upstream_observed=upstream_observed))


def _detail(*, stage: str, authorization: str, requires: bool, upstream_observed: bool) -> str:
    parts = [f"阶段={VALUE_STAGE_LABELS[stage]}"]
    if stage == STAGE_WRITTEN:
        if authorization == "qualified_fact":
            parts.append("授权=被引合格事实逐字覆盖（路径 A）")
        elif authorization == "table_cell_source":
            parts.append("授权=落在被引表材料的某一格（只说明格级位置在，**不**表示取得权威身份）")
        elif authorization == "material_surface_only":
            # 「只在材料原文里逐字出现」这一档要说两件**不同**的事：金额/比率走到这里就是
            # `numeric_qualification` 要拦的那件事；而普通数量（「541GWh」里的 `541`）本就
            # 按 §0.20 照常引用，不要求格级/事实级资格。此前两者共用一句 `else`，于是
            # `authorization` 列写 `material_surface_only`、`detail` 却写「没有任何一处逐字有它」
            # ——同一行自相矛盾（`541` 就是这样读出来的）。
            if requires:
                parts.append("授权=只在被引普通材料原文里逐字出现 —— 金额/比率走到这一档正是"
                             "`numeric_qualification` 要拦的那件事（材料原文出现**不**授权金额与比率）")
            else:
                parts.append("授权=只在被引普通材料原文里逐字出现 —— 这是**普通数量**"
                             "（非金额/比率），按 §0.20 照常引用即可，不要求格级或事实级资格")
        else:
            parts.append("授权=被引来源里没有任何一处逐字有它")
    elif stage == STAGE_DELIVERED_NOT_WRITTEN:
        parts.append("已送达 Writer 事实读视图，但草稿没有写它")
    elif stage == STAGE_QUALIFIED_NOT_DELIVERED:
        parts.append("研究侧已形成合格事实，未进入 Writer 事实读视图")
    elif stage == STAGE_CANDIDATE_REJECTED:
        parts.append("候选命题含该值，资格决定拒绝（原因见 `rejection_reason`）")
    elif stage == STAGE_NO_CANDIDATE:
        parts.append("材料里逐字有该值，但没有任何候选命题含它 —— 这是**未产生候选**，"
                     "不是**候选被拒**")
    elif stage == STAGE_ABSENT_FROM_MATERIAL_SPANS:
        parts.append("送达材料里根本没有该值 —— 这是**来源缺口**，不是候选或资格的问题")
    else:
        parts.append("本次产物里读不到研究侧对象（候选 / 资格决定 / 合格事实）：**不**把"
                     "「读不到」写成「没有」")
    return "；".join(parts)


def render_value_trace_lines(report: CitedValueTraceReport) -> tuple[str, ...]:
    """人读台账（Markdown 行），供运行读回直接拼进预览。"""
    lines: list[str] = []
    lines.append(f"- 政策 `{report.policy_version}`；逐值 **{len(report.values)}** 个"
                 f"（目标来源 {'/'.join(report.target_sources) or '无'}）；"
                 f"上游对象本次{'**在**' if report.upstream_observed else '**不在**'}输入里。")
    counts = report.stage_counts()
    lines.append("- 逐档：" + "；".join(
        f"{VALUE_STAGE_LABELS[s]} {counts[s]}" for s in VALUE_TRACE_STAGES if counts[s]))
    auth = report.authorization_counts()
    lines.append("- 授权档：" + "；".join(f"`{b}` {auth[b]}" for b in AUTHORIZATION_BASES if auth[b]))
    lines.append("")
    lines.append("| 值 | 阶段 | 授权 | 句 | 被引键 | 材料 | 送达事实 | 拒绝/排除原因 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in report.values:
        reason = row.rejection_reason or row.delivery_exclusion_reason or ""
        if row.stage == STAGE_QUALIFIED_NOT_DELIVERED and not reason:
            reason = "**无 typed 排除记录**"
        lines.append(
            f"| `{row.value}` | {VALUE_STAGE_LABELS[row.stage]} | `{row.authorization}` | "
            f"{','.join(row.sentence_ids)} | {','.join(row.citation_keys)} | "
            f"{','.join(row.material_keys)} | {','.join(row.delivered_fact_keys)} | {reason} |")
    #: 上表列窄，**逐值那一句要说清的事**（`detail`）挤不进去。只对**读者最容易读错**的那一档
    #: 补一段清单：**写进了正文、但授权不是 `qualified_fact` / `table_cell_source`** 的值——
    #: 它们能让句子过 `unsourced_number_surface`，却**没有**金额/比率的数字权威（普通数量则是
    #: §0.20 允许照常引用）。不补这一截，`detail` 就只是 `to_dict()` 里的一个没人读的字段。
    caveats = [r for r in report.values
               if r.stage == STAGE_WRITTEN
               and r.authorization not in ("qualified_fact", "table_cell_source")]
    if caveats:
        lines += ["", "**写进正文、但（按本链口径）没有数字权威的值**（逐值说明，"
                      "别把「原文里有」读成「已取得权威」）：", ""]
        lines += [f"- `{r.value}`：{r.detail}" for r in caveats]
    return tuple(lines)

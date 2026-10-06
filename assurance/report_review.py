"""M930-4 报告级只读审阅**输入**（隔离、版本绑定、零调用）。

A1 的逐句独立审阅只覆盖「带引用的正文句」。读者实际还能看到三块它从未表态的内容：

1. `financial_a2` —— 财务 tab 的 A2 确定性资产负债结构
   （`producer_kind=deterministic_presentation`、`model_calls_issued=0`）；
2. `cross_section` —— 两节正文与 A2 **合读之后**才可能出现的矛盾；
3. `material_selectivity` —— 输入清单里存在、但正文没有使用的关键材料。

本模块为这三块**准备**隔离、只读、内容寻址、可版本失效的审阅输入。
它**不发起任何模型调用**、**不写盘**、**不产生 `ReviewIssue`**、**不产生 `AssuranceResult`**，
也**不声称**这些内容已被审阅：每个 scope 的 `state` 恒为 `review_not_run`。

**A2 有独立内容身份。** `financial_a2` 的 `scope_version` 只由 A2 产物自己的字节派生，
**不**把 `crpv_*` 正文版本并进身份体；两节正文版本只作为并列链接出现在
`parent_report_versions` 里。因此：正文改一字不会让 A2 的审阅输入失效，
A2 改一字也不会假装正文改过——两条失效轴各自独立可验。

**依赖方向。** 本模块只依赖标准库、`assurance.schema` 与 `sections` 的**公开解码类型**；
来源行的投影**复用** `sections.cited_review` 的同一条判据，而不是另写一份：
审阅者拿到的来源串必须与作者拿到的是同一串字节，否则「独立性」只是措辞
（`cited_review._fact_location` 的 docstring 已把这条理由写死）。

**`citation_id` 约定。** `ReviewExcerpt.citation_id` 在冻结 wire 上是必填的。对
`material_selectivity` 与 `cross_section` 里引到清单成员的单元，它就是真实的引用键；
对 A2 这种没有 `m*` 键的单元，它是**作用域内锚点** `a2:<aspect_id>`——位置由
`source_locator` 指向 A2 产物自身的相对路径与条目 id，不指任何正文材料。

**两个 scope 的判据面（2026-10-03 修正）。** 只给「材料 + 用没用」不足以让审阅者回答
「遗漏是否实质改变已写结论」：那需要**成稿句段**与 **Contract 逐字要求**。因此

* `material_selectivity` 的 payload 逐节给出 `contract_requirements`（栏目对位的要求文本）
  与 `prose_sentences`（读者读到的句子 + 它挂的栏目 + 它的引用键），且**两者都进
  `scope_version`**——正文改一句话而引用键一条没动时，这份审阅随之失效；
* `cross_section` 的 payload 逐节给出 `cited_sources`（该节实际引用的**全部来源原文**，
  与作者拿到的是同一串 `_source_row` 投影）与 A2 附注，因为「一节的说法被另一节的材料推翻」
  这个判断需要**另一节的材料本身**；问题文本同时写明「没有给的材料不得作为判断依据」。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

from assurance import schema as AS
from sections import cited_review as CR


REPORT_REVIEW_POLICY_VERSION = "rrrp-2"
REPORT_REVIEW_SCHEMA_VERSION = "rrs-2"
REPORT_REVIEW_SCOPE_KINDS = ("financial_a2", "cross_section", "material_selectivity")
#: 只有「输入已备、审阅未运行」一种状态：本模块没有调用路径，也就没有第二种取值。
REPORT_REVIEW_STATES = ("review_not_run",)
#: 没有**任何**模型被证明用过；这个哨兵值把这一点写进身份体，避免日后被读成「已选定模型」。
REPORT_REVIEW_MODEL_POLICY_ID = "mrp_unbound_pending_call_authorization"

#: **分批规则本体**（`rrb-1`）。逐 scope 的调用上限**不是一个手写常数**，而是由这几条规则
#: 切出来的批数；因此身份体里存的是**规则**，不是某个批数的结论——同一份输入在两种分批规则下
#: 可以推出不同的调用次数，"上限 = 1" 这种写法会把「输入面」和「结论」混成一件事。
#:
#: 规则与 `assurance.report_reviewer` 里的容量常数**逐字相等**（该模块导入本常量并断言，
#: 不一致即导入期失败）。放在这里而不是那里，是因为 `report_reviewer` 依赖本模块，
#: 反过来导入会成环。
REPORT_REVIEW_BATCHING_RULES = {
    "plan_version": "rrb-1",
    "max_units_per_call": 25,
    "max_input_chars_per_call": 60000,
    "max_output_tokens": 8192,
    "chars_per_token": 1.60,
}

_SCOPE_FIELDS = frozenset({
    "schema_version", "scope_id", "scope_kind", "scope_version",
    "parent_report_versions", "covered_unit_ids", "covered_sentence_ids",
    "state", "policy_version", "batching_rules", "prompt_version", "model_policy_id",
    "bundle", "request_fingerprint", "request",
})


class ReportReviewError(ValueError):
    """报告级审阅输入无法在可信身份下构造出来。"""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportReviewError(message)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _sorted_unique(values: Any, what: str) -> tuple[str, ...]:
    items = tuple(str(v) for v in values)
    _require(len(set(items)) == len(items), f"{what} 含重复项：{sorted(items)}")
    _require(list(items) == sorted(items), f"{what} 必须升序（确定性聚合的前提）")
    return items


@dataclass(frozen=True)
class ReportReviewRequest:
    """请求面：**就是**将来会投给模型的那一个 JSON 对象，外加它的稳定指纹。

    请求面与 `ReviewInputBundle.allowed_content_fingerprint` 取同一个指纹，于是
    「bundle 声明的可读内容」与「实际会投出去的 payload」是同一件事，而不是两处各说各的。
    """

    payload: dict
    fingerprint: str

    def __post_init__(self) -> None:
        _require(isinstance(self.payload, dict), "ReportReviewRequest.payload 必须是对象")
        want = _digest(self.payload)
        _require(self.fingerprint == want,
                 f"ReportReviewRequest.fingerprint 与 payload 不符：声明 "
                 f"{self.fingerprint!r}，应为 {want!r}")

    def to_dict(self) -> dict:
        return {"payload": self.payload, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, d: Any) -> "ReportReviewRequest":
        _require(isinstance(d, dict) and set(d) == {"payload", "fingerprint"},
                 "ReportReviewRequest 字段集不合约")
        return cls(payload=d["payload"], fingerprint=str(d["fingerprint"]))


@dataclass(frozen=True)
class SectionReviewFacts:
    """一个章节的已解码只读事实：清单、草稿、正文版本，财务另带 A2 原始产物。"""

    section_id: str
    manifest: Any
    draft: Any
    version: Any
    balance: dict[str, Any] | None = None

    def cited_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        for subsection in self.draft.subsections:
            for paragraph in subsection.paragraphs:
                for sentence in paragraph.sentences:
                    for key in sentence.citations:
                        if key not in keys:
                            keys.append(key)
        return tuple(keys)

    def sentence_ids(self) -> tuple[str, ...]:
        return tuple(sentence.sentence_id
                     for subsection in self.draft.subsections
                     for paragraph in subsection.paragraphs
                     for sentence in paragraph.sentences)


@dataclass(frozen=True)
class ReportReviewScope:
    """一块读者可见、A1 从未表态的内容的**审阅输入**记录。

    `scope_version` 是这块内容自己的版本（`rrv_…`）；`bundle.report_version` 必须等于它，
    因此「输入绑定在哪个版本上」是线形状强制的，不是调用方的纪律。
    `parent_report_versions` 只是**并列链接**，不参与 `scope_version` 的计算。
    """

    schema_version: str
    scope_id: str
    scope_kind: str
    scope_version: str
    parent_report_versions: tuple[str, ...]
    covered_unit_ids: tuple[str, ...]
    covered_sentence_ids: tuple[str, ...]
    state: str
    policy_version: str
    batching_rules: dict
    prompt_version: str
    model_policy_id: str
    bundle: AS.ReviewInputBundle
    request: ReportReviewRequest

    def __post_init__(self) -> None:
        _require(self.schema_version == REPORT_REVIEW_SCHEMA_VERSION,
                 f"ReportReviewScope.schema_version 必须为 {REPORT_REVIEW_SCHEMA_VERSION!r}")
        _require(self.scope_kind in REPORT_REVIEW_SCOPE_KINDS,
                 f"未知 scope_kind：{self.scope_kind!r}")
        _require(self.state in REPORT_REVIEW_STATES,
                 f"未知 state：{self.state!r}")
        _require(self.policy_version == REPORT_REVIEW_POLICY_VERSION,
                 f"ReportReviewScope.policy_version 必须为 {REPORT_REVIEW_POLICY_VERSION!r}")
        _require(self.model_policy_id == REPORT_REVIEW_MODEL_POLICY_ID,
                 "报告级审阅输入不得预先声明一个已选定的模型策略")
        _require(self.batching_rules == REPORT_REVIEW_BATCHING_RULES,
                 f"{self.scope_kind}：batching_rules 必须逐字等于本版本的冻结分批规则"
                 f"（实际 {self.batching_rules!r}）——"
                 "调用上限由这批规则推出，不由输入面自己声明一个批数")
        _require(self.prompt_version == AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
                 "报告级审阅输入必须声明冻结的独立审阅 prompt 版本")
        _require(isinstance(self.bundle, AS.ReviewInputBundle),
                 "ReportReviewScope.bundle 必须是 ReviewInputBundle")
        _require(isinstance(self.request, ReportReviewRequest),
                 "ReportReviewScope.request 必须是 ReportReviewRequest")
        # 版本绑定与内容对账（线形状强制，不靠调用方自觉）。
        _require(self.bundle.report_version == self.scope_version,
                 f"{self.scope_kind}：bundle.report_version "
                 f"{self.bundle.report_version!r} ≠ scope_version {self.scope_version!r}")
        _require(self.bundle.allowed_content_fingerprint == self.request.fingerprint,
                 f"{self.scope_kind}：bundle 声明的可读内容与实际请求面不是同一件事")
        _require(self.scope_version not in self.parent_report_versions,
                 f"{self.scope_kind}：本块内容身份不得等于它所链接的正文版本")

        parents = _sorted_unique(self.parent_report_versions,
                                 "ReportReviewScope.parent_report_versions")
        _require(bool(parents), "ReportReviewScope.parent_report_versions 不得为空")
        units = _sorted_unique(self.covered_unit_ids, "ReportReviewScope.covered_unit_ids")
        _require(bool(units), "ReportReviewScope.covered_unit_ids 不得为空")
        _require(units == tuple(self.bundle.unit_keys()),
                 f"{self.scope_kind}：covered_unit_ids 与 bundle.unit_inventory 不一致")
        _sorted_unique(self.covered_sentence_ids, "ReportReviewScope.covered_sentence_ids")
        _require(tuple(self.covered_sentence_ids) == tuple(self.bundle.sentence_inventory),
                 f"{self.scope_kind}：covered_sentence_ids 与 bundle.sentence_inventory 不一致")

        expected = AS.content_id("rrs_", self.identity_body())
        _require(self.scope_id == expected,
                 f"ReportReviewScope.scope_id 与内容不符：声明 {self.scope_id!r}，"
                 f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "scope_kind": self.scope_kind,
            "scope_version": self.scope_version,
            "parent_report_versions": list(self.parent_report_versions),
            "covered_unit_ids": list(self.covered_unit_ids),
            "covered_sentence_ids": list(self.covered_sentence_ids),
            "state": self.state,
            "policy_version": self.policy_version,
            "batching_rules": dict(self.batching_rules),
            "prompt_version": self.prompt_version,
            "model_policy_id": self.model_policy_id,
            "bundle": self.bundle.to_dict(),
            "request_fingerprint": self.request.fingerprint,
        }

    def to_dict(self) -> dict:
        return {"scope_id": self.scope_id, **self.identity_body(),
                "request": self.request.to_dict()}

    @classmethod
    def create(cls, *, scope_kind: str, scope_version: str,
               parent_report_versions: Sequence[str],
               covered_unit_ids: Sequence[str],
               covered_sentence_ids: Sequence[str],
               bundle: AS.ReviewInputBundle,
               request: ReportReviewRequest) -> "ReportReviewScope":
        units = tuple(sorted(str(v) for v in covered_unit_ids))
        sentences = tuple(sorted(str(v) for v in covered_sentence_ids))
        parents = tuple(sorted(str(v) for v in parent_report_versions))
        body = {
            "schema_version": REPORT_REVIEW_SCHEMA_VERSION,
            "scope_kind": scope_kind,
            "scope_version": scope_version,
            "parent_report_versions": list(parents),
            "covered_unit_ids": list(units),
            "covered_sentence_ids": list(sentences),
            "state": REPORT_REVIEW_STATES[0],
            "policy_version": REPORT_REVIEW_POLICY_VERSION,
            "batching_rules": dict(REPORT_REVIEW_BATCHING_RULES),
            "prompt_version": AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
            "model_policy_id": REPORT_REVIEW_MODEL_POLICY_ID,
            "bundle": bundle.to_dict(),
            "request_fingerprint": request.fingerprint,
        }
        return cls(scope_id=AS.content_id("rrs_", body), schema_version=body["schema_version"],
                   scope_kind=scope_kind, scope_version=scope_version,
                   parent_report_versions=parents, covered_unit_ids=units,
                   covered_sentence_ids=sentences, state=body["state"],
                   policy_version=body["policy_version"],
                   batching_rules=body["batching_rules"],
                   prompt_version=body["prompt_version"],
                   model_policy_id=body["model_policy_id"],
                   bundle=bundle, request=request)

    @classmethod
    def from_dict(cls, d: Any) -> "ReportReviewScope":
        _require(isinstance(d, dict) and set(d) == _SCOPE_FIELDS | {"scope_id", "request"},
                 "ReportReviewScope 字段集不合约")
        return cls(
            schema_version=str(d["schema_version"]), scope_id=str(d["scope_id"]),
            scope_kind=str(d["scope_kind"]), scope_version=str(d["scope_version"]),
            parent_report_versions=tuple(d["parent_report_versions"]),
            covered_unit_ids=tuple(d["covered_unit_ids"]),
            covered_sentence_ids=tuple(d["covered_sentence_ids"]),
            state=str(d["state"]), policy_version=str(d["policy_version"]),
            batching_rules=dict(d["batching_rules"]), prompt_version=str(d["prompt_version"]),
            model_policy_id=str(d["model_policy_id"]),
            bundle=AS.ReviewInputBundle.from_dict(d["bundle"]),
            request=ReportReviewRequest.from_dict(d["request"]))


# ---------------------------------------------------------------------------
# 构造
# ---------------------------------------------------------------------------

def _scope_version(body: dict) -> str:
    return "rrv_" + _digest(body)[:24]


def _bundle(*, scope_version: str, report_id: str, units: Sequence[AS.ReviewUnitRef],
            excerpts: Sequence[AS.ReviewExcerpt], request: ReportReviewRequest,
            sentence_inventory: Sequence[str] = ()) -> AS.ReviewInputBundle:
    return AS.ReviewInputBundle.create(
        report_version=scope_version, report_id=report_id,
        unit_inventory=tuple(units), excerpts=tuple(excerpts),
        citation_ids=tuple(excerpt.citation_id for excerpt in excerpts),
        allowed_content_fingerprint=request.fingerprint,
        prompt_version=AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
        model_policy_id=REPORT_REVIEW_MODEL_POLICY_ID,
        schema_version=AS.REVIEW_INPUT_BUNDLE_SCHEMA_VERSION,
        sentence_inventory=tuple(sentence_inventory))


def _contract_requirements(manifest: Any) -> list[dict[str, Any]]:
    """清单里 Contract 的**逐字**要求，按小节/栏目对位（`crr-6` 的同一投影）。

    与 `cited_review.build_cited_review_request` 取**同一条**判据（`_aspect_requirement_pairs`）：
    「这一段答的是哪一栏」在两处必须得到同一个答案，否则选择性审阅会拿一份作者没见过的
    要求表去判「该写没写」。对不上的小节退回小节级要求文本（栏目键留空），**不猜**对位。
    """
    rows: list[dict[str, Any]] = []
    for spec in manifest.subsections:
        pairs = CR._aspect_requirement_pairs(spec)
        if pairs:
            rows.extend({"subsection_id": spec.subsection_id, "title": spec.title,
                         "aspect_id": pair["aspect_id"],
                         "requirement_text": pair["requirement_text"]}
                        for pair in pairs)
        else:
            rows.append({"subsection_id": spec.subsection_id, "title": spec.title,
                         "aspect_id": "", "requirement_text": spec.requirement_text})
    return rows


def _prose_sentences(draft: Any) -> list[dict[str, Any]]:
    """成稿句段的**逐句**读视图（与 A1 请求面同形：句子 + 段落声明的栏目 + 引用键）。

    选择性审阅要判的是「这句话的结论会不会被某个未用材料改写」，因此它必须看到
    **读者读到的那句话本身**、这句话挂在哪个栏目下、以及它靠哪几条引用立起来。
    """
    rows: list[dict[str, Any]] = []
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                rows.append({
                    "sentence_id": sentence.sentence_id,
                    "subsection_id": subsection.subsection_id,
                    "paragraph_id": paragraph.paragraph_id,
                    "paragraph_aspect_ids": list(paragraph.aspect_ids),
                    "text": sentence.text,
                    "citations": list(sentence.citations),
                })
    return rows


def _item_text(item: dict[str, Any]) -> str:
    """A2 条目**自身字段**拼成的可读文本：不替它补写、不替它润色。"""
    lines = [f"[{item.get('state')}] {item.get('label')}"]
    requirement = str(item.get("requirement_text") or "").strip()
    if requirement:
        lines.append(f"Contract 要求：{requirement}")
    statement = str(item.get("statement") or "").strip()
    if statement:
        lines.append(statement)
    reason = str(item.get("reason") or "").strip()
    if reason:
        lines.append(f"未取得原因：{reason}")
    readings = item.get("readings") or []
    if readings:
        lines.append("逐项取值坐标：" + _canonical(readings))
    for gap in item.get("gaps") or []:
        lines.append("缺口：" + _canonical(gap))
    return "\n".join(lines)


def _a2_scope(*, report_id: str, facts: SectionReviewFacts) -> ReportReviewScope:
    balance = facts.balance
    _require(isinstance(balance, dict), "financial_a2 需要财务 A2 原始产物")
    items = list(balance.get("items") or ())
    notes = list(balance.get("notes") or ())
    _require(bool(items), "财务 A2 产物没有任何条目")
    artifact = f"{facts.section_id}/cited_balance_structure__fin_balance_structure.json"

    version_body = {
        "policy_version": REPORT_REVIEW_POLICY_VERSION,
        "scope_kind": "financial_a2",
        "section_id": facts.section_id,
        "a2_schema_version": balance.get("schema_version"),
        "a2_producer_kind": balance.get("producer_kind"),
        "a2_fingerprint": balance.get("fingerprint"),
        "items": [{"aspect_id": item.get("aspect_id"), "state": item.get("state"),
                   "label": item.get("label")} for item in items],
        "notes": [{"note_id": note.get("note_id"), "state": note.get("state")}
                  for note in notes],
    }
    scope_version = _scope_version(version_body)

    units: list[AS.ReviewUnitRef] = []
    excerpts: list[AS.ReviewExcerpt] = []
    rows: list[dict[str, Any]] = []
    for item in items:
        aspect_id = str(item["aspect_id"])
        unit = AS.ReviewUnitRef.create(unit_kind="row", unit_id=aspect_id)
        units.append(unit)
        excerpts.append(AS.ReviewExcerpt.create(
            unit_ref=unit, text=_item_text(item),
            source_locator=f"{artifact}#{aspect_id}", citation_id=f"a2:{aspect_id}"))
        rows.append({"aspect_id": aspect_id, "label": item.get("label"),
                     "state": item.get("state"),
                     "requirement_text": item.get("requirement_text"),
                     "statement": item.get("statement"), "reason": item.get("reason"),
                     "readings": item.get("readings") or [],
                     "gaps": item.get("gaps") or []})
    note_rows: list[dict[str, Any]] = []
    for note in notes:
        note_id = str(note["note_id"])
        unit = AS.ReviewUnitRef.create(unit_kind="row", unit_id=f"note.{note_id}")
        units.append(unit)
        text = f"[{note.get('state')}] {note.get('label')}\n{note.get('detail') or ''}".strip()
        excerpts.append(AS.ReviewExcerpt.create(
            unit_ref=unit, text=text, source_locator=f"{artifact}#note.{note_id}",
            citation_id=f"a2:note.{note_id}"))
        note_rows.append({"note_id": note_id, "label": note.get("label"),
                          "state": note.get("state"), "detail": note.get("detail"),
                          "reason": note.get("reason")})

    payload = {
        "policy_version": REPORT_REVIEW_POLICY_VERSION,
        "prompt_version": AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
        "model_policy_id": REPORT_REVIEW_MODEL_POLICY_ID,
        "scope_kind": "financial_a2",
        "scope_version": scope_version,
        "report_id": report_id,
        "section_id": facts.section_id,
        "source_artifact": artifact,
        "a2_schema_version": balance.get("schema_version"),
        "a2_producer_kind": balance.get("producer_kind"),
        "a2_fingerprint": balance.get("fingerprint"),
        "model_calls_issued": balance.get("model_calls_issued"),
        "routing_version": balance.get("routing_version"),
        "contract_threshold_rule": balance.get("contract_threshold_rule"),
        "contract_topic_aspects": balance.get("contract_topic_aspects") or [],
        "fact_scope": balance.get("fact_scope"),
        "items": rows,
        "notes": note_rows,
        "review_question": (
            "下面每一条都是**不经模型**、由权威事实与 Decimal 确定性生成的资产负债结构结论。"
            "请逐条判断：该条 statement 是否被它自己列出的 readings（原始权威取值坐标）支持，"
            "有无夸大、因果误写、局部推整体或把带代理口径的输入写成正式结论。"
            "不得重算、不得补写、不得替它修正措辞。"),
        "excluded_context": list(AS.REQUIRED_EXCLUDED_CONTEXT),
    }
    request = ReportReviewRequest(payload=payload, fingerprint=_digest(payload))
    bundle = _bundle(scope_version=scope_version, report_id=report_id, units=units,
                     excerpts=excerpts, request=request)
    return ReportReviewScope.create(
        scope_kind="financial_a2", scope_version=scope_version,
        parent_report_versions=(facts.version.report_version,),
        covered_unit_ids=tuple(u.key for u in units), covered_sentence_ids=(),
        bundle=bundle, request=request)


def _scoped_sentence_ids(facts: SectionReviewFacts) -> tuple[str, ...]:
    """本节的句子在**报告级**上的名字。

    句子 id 只在节内唯一（两节都从 `s0001` 起），跨节汇总必须带节前缀，
    否则「必须逐句表态的集合」会被去重悄悄缩小。
    """
    return tuple(f"{facts.section_id}:{sid}" for sid in facts.sentence_ids())


def _cross_section_scope(*, report_id: str, sections: Sequence[SectionReviewFacts],
                         previews: dict[str, str],
                         a2_scope_version: str) -> ReportReviewScope:
    _require(len(sections) == 2, "cross_section 需要恰好两节")
    ordered = sorted(sections, key=lambda s: s.section_id)
    # 每节**实际引用**的来源原文：判「一节的说法被另一节的材料推翻」需要另一节的材料本身，
    # 只有正文不足以做这个判断。投影走 `cited_review._source_row`——审阅者拿到的来源串
    # 与作者拿到的是同一串字节，否则这份独立性只是措辞。
    sourced: dict[str, list[dict[str, Any]]] = {
        s.section_id: [CR._source_row(manifest=s.manifest, key=key)
                       for key in s.cited_keys()] for s in ordered}
    balance_facts = next((s for s in ordered if s.balance is not None), None)
    _require(balance_facts is not None, "cross_section 需要财务 A2 作为合读对象之一")
    a2_notes = list(balance_facts.balance.get("notes") or ())
    version_body = {
        "policy_version": REPORT_REVIEW_POLICY_VERSION,
        "scope_kind": "cross_section",
        "a2_scope_version": a2_scope_version,
        "sections": [{"section_id": s.section_id,
                      "report_version": s.version.report_version,
                      "preview_sha256": _digest(previews[s.section_id]),
                      "sentences": [{"sentence_id": sn.sentence_id,
                                     "text_sha256": _digest(sn.text)}
                                    for sn in (x for sub in s.draft.subsections
                                               for par in sub.paragraphs
                                               for x in par.sentences)],
                      "sources": [{"citation_id": row["citation_id"],
                                   "source_digest": _digest(row)}
                                  for row in sourced[s.section_id]]}
                     for s in ordered],
        "a2_notes": [{"note_id": note.get("note_id"), "state": note.get("state"),
                      "detail_sha256": _digest(str(note.get("detail") or ""))}
                     for note in a2_notes],
    }
    scope_version = _scope_version(version_body)

    units: list[AS.ReviewUnitRef] = []
    excerpts: list[AS.ReviewExcerpt] = []
    section_rows: list[dict[str, Any]] = []
    for facts in ordered:
        unit = AS.ReviewUnitRef.create(unit_kind="section", unit_id=facts.section_id)
        units.append(unit)
        preview = previews[facts.section_id]
        excerpts.append(AS.ReviewExcerpt.create(
            unit_ref=unit, text=preview,
            source_locator=f"{facts.section_id}/cited_preview.md",
            citation_id=f"prose:{facts.section_id}"))
        section_rows.append({
            "section_id": facts.section_id,
            "section_title": getattr(facts.manifest, "section_title", ""),
            "report_version": facts.version.report_version,
            "manifest_id": facts.manifest.manifest_id,
            "reader_preview": preview,
            "sentence_ids": list(facts.sentence_ids()),
            "sentence_citations": [{"sentence_id": sn.sentence_id,
                                    "citations": list(sn.citations)}
                                   for sn in (x for sub in facts.draft.subsections
                                              for par in sub.paragraphs
                                              for x in par.sentences)],
            "cited_sources": sourced[facts.section_id],
        })

    balance = balance_facts
    a2_unit = AS.ReviewUnitRef.create(unit_kind="table", unit_id="fin_balance_structure")
    units.append(a2_unit)
    a2_text = "\n\n".join(
        [_item_text(item) for item in balance.balance["items"]]
        + [f"[附注 · {note.get('state')}] {note.get('label')}\n{note.get('detail') or ''}"
           for note in a2_notes])
    excerpts.append(AS.ReviewExcerpt.create(
        unit_ref=a2_unit, text=a2_text,
        source_locator="financial/cited_balance_structure__fin_balance_structure.json",
        citation_id="a2:fin_balance_structure"))

    payload = {
        "policy_version": REPORT_REVIEW_POLICY_VERSION,
        "prompt_version": AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
        "model_policy_id": REPORT_REVIEW_MODEL_POLICY_ID,
        "scope_kind": "cross_section",
        "scope_version": scope_version,
        "report_id": report_id,
        "sections": section_rows,
        "a2_scope_version": a2_scope_version,
        "a2_items": [{"aspect_id": item.get("aspect_id"), "state": item.get("state"),
                      "statement": item.get("statement"),
                      "reason": item.get("reason")}
                     for item in balance.balance["items"]],
        "a2_notes": [{"note_id": note.get("note_id"), "label": note.get("label"),
                      "state": note.get("state"), "detail": note.get("detail"),
                      "reason": note.get("reason")} for note in a2_notes],
        "review_question": (
            "你现在拿到的是**全部**可用材料：(a) 两节正文全文；(b) 逐句的引用键；"
            "(c) 两节正文实际引用的**全部来源原文**（每节的 `cited_sources`，含定位与身份）；"
            "(d) 财务 A2 的逐条结论与附注。请**只基于这些材料**判断：两节之间、或任一节与"
            "财务 A2 之间，是否出现彼此矛盾、同一事实在两节取值/口径/期间不一致、"
            "或一节的结论被**上面列出的**某条来源原文推翻。"
            "你没有看到的材料（未被任何一句引用的清单成员、PDF 原图、外部资料）"
            "**不得**作为判断依据；确实需要它们才能判断时，就明说「给定材料不足以判断」，"
            "不要猜。不得改稿、不得补事实、不得替任一节重写。"),
        "excluded_context": list(AS.REQUIRED_EXCLUDED_CONTEXT),
    }
    request = ReportReviewRequest(payload=payload, fingerprint=_digest(payload))
    scoped_sentences = tuple(sid for facts in ordered
                             for sid in _scoped_sentence_ids(facts))
    bundle = _bundle(
        scope_version=scope_version, report_id=report_id, units=units, excerpts=excerpts,
        request=request, sentence_inventory=scoped_sentences)
    return ReportReviewScope.create(
        scope_kind="cross_section", scope_version=scope_version,
        parent_report_versions=tuple(s.version.report_version for s in ordered),
        covered_unit_ids=tuple(u.key for u in units),
        covered_sentence_ids=scoped_sentences,
        bundle=bundle, request=request)


def _selectivity_scope(*, report_id: str,
                       sections: Sequence[SectionReviewFacts]) -> ReportReviewScope:
    ordered = sorted(sections, key=lambda s: s.section_id)
    member_rows: dict[str, list[dict[str, Any]]] = {}
    for facts in ordered:
        used = set(facts.cited_keys())
        rows: list[dict[str, Any]] = []
        for key in (*facts.manifest.material_keys(), *facts.manifest.fact_keys()):
            source = CR._source_row(manifest=facts.manifest, key=key)
            rows.append({"citation_key": key, "used_in_prose": key in used,
                         "source_row": source,
                         "member_digest": _digest(source)})
        member_rows[facts.section_id] = rows

    version_body = {
        "policy_version": REPORT_REVIEW_POLICY_VERSION,
        "scope_kind": "material_selectivity",
        "sections": [{"section_id": facts.section_id,
                      "manifest_id": facts.manifest.manifest_id,
                      "members": [{"citation_key": row["citation_key"],
                                   "member_digest": row["member_digest"],
                                   "used_in_prose": row["used_in_prose"]}
                                  for row in member_rows[facts.section_id]],
                      # 判据侧：Contract 的逐字要求，以及成稿句段本身。两者都必须进内容身份
                      # ——否则「正文改了一句话、引用键一条没动」时这份审阅**不会**失效，
                      # 而它恰恰是靠正文来判「遗漏有没有实质改变已写结论」的。
                      "requirements": [{"subsection_id": req["subsection_id"],
                                        "aspect_id": req["aspect_id"],
                                        "requirement_text": req["requirement_text"]}
                                       for req in _contract_requirements(facts.manifest)],
                      "prose": [{"sentence_id": row["sentence_id"],
                                 "paragraph_aspect_ids": row["paragraph_aspect_ids"],
                                 "citations": row["citations"],
                                 "text_sha256": _digest(row["text"])}
                                for row in _prose_sentences(facts.draft)]}
                     for facts in ordered],
    }
    scope_version = _scope_version(version_body)

    units: list[AS.ReviewUnitRef] = []
    excerpts: list[AS.ReviewExcerpt] = []
    section_rows: list[dict[str, Any]] = []
    for facts in ordered:
        rows = member_rows[facts.section_id]
        for row in rows:
            unit = AS.ReviewUnitRef.create(unit_kind="citation",
                                           unit_id=row["citation_key"])
            units.append(unit)
            excerpts.append(AS.ReviewExcerpt.create(
                unit_ref=unit, text=str(row["source_row"]["text"]),
                source_locator=str(row["source_row"]["locator"]),
                citation_id=row["citation_key"]))
        used = [row["citation_key"] for row in rows if row["used_in_prose"]]
        section_rows.append({
            "section_id": facts.section_id,
            "section_title": getattr(facts.manifest, "section_title", ""),
            "manifest_id": facts.manifest.manifest_id,
            "report_version": facts.version.report_version,
            "contract_requirements": _contract_requirements(facts.manifest),
            "prose_sentences": _prose_sentences(facts.draft),
            "members": [{"citation_key": row["citation_key"],
                         "axis": row["source_row"]["axis"],
                         "used_in_prose": row["used_in_prose"],
                         "source_row": row["source_row"]} for row in rows],
            "used_citation_keys": used,
            "unused_citation_keys": [row["citation_key"] for row in rows
                                     if not row["used_in_prose"]],
        })

    payload = {
        "policy_version": REPORT_REVIEW_POLICY_VERSION,
        "prompt_version": AS.INDEPENDENT_REVIEWER_PROMPT_VERSION,
        "model_policy_id": REPORT_REVIEW_MODEL_POLICY_ID,
        "scope_kind": "material_selectivity",
        "scope_version": scope_version,
        "report_id": report_id,
        "sections": section_rows,
        "review_question": (
            "每一节给你四样东西：`contract_requirements`（Contract 的**逐字**要求，"
            "按栏目对位）、`prose_sentences`（读者实际读到的**成稿句段**，含它挂在哪一栏、"
            "靠哪几条引用）、`members`（本次 Writer 清单里的**全部**来源成员及其原文或命题文本）、"
            "以及逐成员标明正文用没用它的 `used_in_prose` / `used_citation_keys` / "
            "`unused_citation_keys`。\n"
            "请按这个次序判断：\n"
            "1. 对每一条 Contract 要求，正文的那几句话是否已经给出了回答（要求 ↔ 句段对得上吗）；\n"
            "2. 在**未被使用**的成员里，是否存在一条直接对应某条 Contract 要求、"
            "其内容会**实质改变**上面某个已写结论（或本应被写出的判断）的关键材料；\n"
            "3. 若有，请指出它改变的是**哪一句**、以及它为什么构成实质改变。\n"
            "未被使用本身**不是**缺陷：合法 `not_used` 不计为问题，你也不要因为某条材料"
            "「更有意思」就要求写进正文。不得替正文补写、不得重新检索、不得把缺材料当成"
            "正文的错。"),
        "excluded_context": list(AS.REQUIRED_EXCLUDED_CONTEXT),
    }
    request = ReportReviewRequest(payload=payload, fingerprint=_digest(payload))
    bundle = _bundle(scope_version=scope_version, report_id=report_id, units=units,
                     excerpts=excerpts, request=request)
    return ReportReviewScope.create(
        scope_kind="material_selectivity", scope_version=scope_version,
        parent_report_versions=tuple(s.version.report_version for s in ordered),
        covered_unit_ids=tuple(u.key for u in units),
        # 选择性遗漏只对**来源成员**表态（用没用、漏了没有），不对句子表态：
        # 句子级 faithfulness 是 A1 逐句审阅的职责，塞进来只会让「必须表态的集合」
        # 与「能表态的集合」分离。成稿句段（`prose_sentences`）在本 scope 里是**判据**，
        # 不是表态对象——它让审阅者能回答「遗漏是否实质改变已写结论」，而不是逐句复核正文。
        covered_sentence_ids=(),
        bundle=bundle, request=request)


def build_report_review_scopes(*, report_id: str,
                               sections: Sequence[SectionReviewFacts],
                               previews: dict[str, str]
                               ) -> tuple[ReportReviewScope, ...]:
    """构造三块内容的审阅输入。**只读、确定性、零调用。**

    `previews` 是各节 `cited_preview.md` 的原文（读者实际看到的那份），用于 cross_section：
    跨节审阅读的必须是读者读到的同一串字节，而不是另做一份摘要。
    """
    _require(bool(report_id), "report_id 必须非空")
    ordered = sorted(sections, key=lambda s: s.section_id)
    _require(len(ordered) == 2, "报告级审阅输入要求恰好两节")
    seen: set[str] = set()
    for facts in ordered:
        _require(facts.section_id not in seen, f"章节重复：{facts.section_id}")
        seen.add(facts.section_id)
        _require(facts.section_id in previews, f"缺少 {facts.section_id} 的读者预览")

    financial = next((s for s in ordered if s.balance is not None), None)
    _require(financial is not None, "报告级审阅输入要求财务 A2 产物")
    a2 = _a2_scope(report_id=report_id, facts=financial)
    cross = _cross_section_scope(report_id=report_id, sections=ordered,
                                 previews=previews, a2_scope_version=a2.scope_version)
    selectivity = _selectivity_scope(report_id=report_id, sections=ordered)
    return (a2, cross, selectivity)


def summarize(scope: ReportReviewScope) -> dict[str, Any]:
    """scope → 可落盘的摘要（不含逐条 payload，供诊断文件保持可读）。"""
    return {
        "scope_kind": scope.scope_kind,
        "scope_id": scope.scope_id,
        "scope_version": scope.scope_version,
        "parent_report_versions": list(scope.parent_report_versions),
        "bundle_id": scope.bundle.bundle_id,
        "unit_count": len(scope.bundle.unit_inventory),
        "excerpt_count": len(scope.bundle.excerpts),
        "covered_sentence_count": len(scope.covered_sentence_ids),
        "state": scope.state,
        "batching_rules": dict(scope.batching_rules),
        "prompt_version": scope.prompt_version,
        "model_policy_id": scope.model_policy_id,
        "request_fingerprint": scope.request.fingerprint,
        "request_char_count": len(_canonical(scope.request.payload)),
        "excluded_context": list(scope.bundle.excluded_context),
    }

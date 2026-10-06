"""`fin_source_scope` 的**确定性**来源 / 口径呈现（`css-2`）。

`fin_source_scope` 在本阶段 A 的 profile 里是**选中 topic**（与 `company_business`、`fin_solvency`
并列）。它的十条 Contract 要求问的是「这次报告用的报表本身齐不齐、期间是什么、什么口径、什么
单位、审计意见有没有」——这些问题的答案**已经在**本节财务权威里了：`FinancialPackArtifact` 自己
带的 `statements_available` / `periods` / `period_note` / `snapshot` 就是权威对这一组问题的陈述。

因此本模块**不**让模型来写这一栏：让模型复述「本次快照的 scope 是 consolidated」不会让这句话更
可信，只会让一个权威字段变成一句需要被复核的生成文本。逐条取值、逐条标注**取的是哪个字段**、
取不到的逐条留 typed 缺口——这是这一栏唯一诚实的落法，也是它**零模型调用**的原因。

三件本模块**不做**的事，各自对应一条会读错的写法：

* **不复制 `fin_solvency` 的偿债指标表。** 那一栏的表是「指标 × 期间」的数值表，本栏问的是
  「这些数值的来源与口径」。用偿债表充本栏，等于用答案替掉问题——读者会以为「报表齐备」
  这句话已经被算出来了。
* **不把「齐备」写成「可靠」。** `balance_sheet_ready` 说的是「本次登记的主表里有资产负债表」，
  不是「这张表经过审计」、更不是「表里的数字可用」。审计那一部分本次取不到，是一条**缺口**。
* **不用 `write_not_found` 之外的措辞掩盖缺口。** 缺口逐条带冻结 Contract 的原话与封闭原因码；
  「这一项暂时没有」与「这一项已核对通过」在产物上必须是两个不同的字段。

本模块纯确定性：不发起任何模型调用、不做任何计算、不读库、不检索。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "CITED_SOURCE_SCOPE_SCHEMA_VERSION",
    "CITED_SOURCE_SCOPE_POLICY_VERSION",
    "SOURCE_SCOPE_ROUTING_VERSION",
    "SOURCE_SCOPE_PRODUCER_KIND",
    "SOURCE_SCOPE_ITEM_STATES",
    "SOURCE_SCOPE_GAP_REASONS",
    "SOURCE_SCOPE_ASPECT_ROUTING",
    "CitedSourceScopeError",
    "SourceScopeItem",
    "CitedSourceScopePresentation",
    "build_cited_source_scope",
    "render_cited_source_scope_markdown",
]

#: 产物 schema 版本。条目字段集变化即升版。
CITED_SOURCE_SCOPE_SCHEMA_VERSION = "css-2"
#: 呈现政策版本（判据集合、缺口理由集合、读字段的映射任一变化即升版）。
#:
#: `cssp-2`：`fin_audit_opinion.amount_unit` 从「有币种即 `determined`」改为**一律 typed
#: 缺口**——币种不是金额单位，权威没有承载「金额单位」的字段就不能把这一栏判成已呈现。
CITED_SOURCE_SCOPE_POLICY_VERSION = "cssp-2"
#: 「哪一条 Contract 要求读哪一个权威字段」这张表的版本。
SOURCE_SCOPE_ROUTING_VERSION = "ssr-1"

#: 产出者身份。它**不是**任何一个模型：这一栏没有生成调用，产物里不许出现模型名或 prompt 版本。
SOURCE_SCOPE_PRODUCER_KIND = "deterministic_presentation"

#: 条目状态，封闭两档。
SOURCE_SCOPE_ITEM_STATES = ("determined", "gap")

#: 缺口原因，封闭集合。名称直指「取不到」的那一层，不含任何程度的判断。
SOURCE_SCOPE_GAP_REASONS = (
    #: 本次登记的来源里**没有**承载这一项的材料或字段：不是「找不到」，是「没提供」。
    "not_provided_by_registered_sources",
)

#: 主表 `statement_type` 的封闭集合（与 `financial_worker._STATEMENT_TYPES` 同一口径的只读副本：
#: 这里逐字写出，是为了让「本模块读的是哪三种主表」在读者面可见，而不是藏在别处的一个常量里）。
_STATEMENT_TYPES = ("balance_sheet", "income_statement", "cash_flow")

_STATEMENT_CN = {
    "balance_sheet": "资产负债表",
    "income_statement": "利润表",
    "cash_flow": "现金流量表",
}


class CitedSourceScopeError(Exception):
    """确定性呈现的构造期拒绝。**不是**一次运行结果。"""


def _statements_available(artifact: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in (getattr(artifact, "statements_available", ()) or ()))


def _periods(artifact: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in (getattr(artifact, "periods", ()) or ()))


def _snapshot_get(artifact: Any, name: str) -> str:
    snapshot = getattr(artifact, "snapshot", None)
    return str(getattr(snapshot, name, "") or "").strip() if snapshot is not None else ""


def _statement_reader(statement_type: str) -> Callable[[Any], tuple[str, str, str]]:
    """某个主表是否齐备的读法：**只看** `statements_available` 里有没有它。"""

    def _read(artifact: Any) -> tuple[str, str, str]:
        available = _statements_available(artifact)
        cn = _STATEMENT_CN[statement_type]
        if statement_type in available:
            return (f"本次登记的主表里含{cn}（`statement_type={statement_type}`）。",
                    "artifact.statements_available", statement_type)
        return ("", "artifact.statements_available",
                f"{cn}（`statement_type={statement_type}`）不在本次登记的主表集合 "
                f"{list(available)} 内")

    return _read


def _three_year_reader(artifact: Any) -> tuple[str, str, str]:
    periods = _periods(artifact)
    if not periods:
        return ("", "artifact.periods", "本次快照没有声明任何报告期")
    return (f"本次快照声明的报告期为 {'、'.join(periods)}。",
            "artifact.periods", "、".join(periods))


def _reporting_period_reader(artifact: Any) -> tuple[str, str, str]:
    #: 与「近三年趋势覆盖」读的是**同一个**字段、但回答的不是同一个问题：前者问覆盖够不够，
    #: 后者问报告期是哪几个。两者共用一次读数不构成重复条目——它们的 Contract 要求不同。
    return _three_year_reader(artifact)


def _consolidation_scope_reader(artifact: Any) -> tuple[str, str, str]:
    scope = _snapshot_get(artifact, "scope")
    if not scope:
        return ("", "artifact.snapshot.scope", "本次快照没有声明报表口径（scope）")
    return (f"本次快照声明的报表口径为 `{scope}`（合并口径）。",
            "artifact.snapshot.scope", scope)


def _amount_unit_reader(artifact: Any) -> tuple[str, str, str]:
    """Contract 要求「金额单位」；本节权威只有**币种**（`currency`）这一个字段。

    **币种 ≠ 金额单位**：币种说"用哪种货币计价"（`CNY`），金额单位说"一个数字代表多少钱"
    （元／千元／万元／亿元）。把 `CNY` 印成「金额单位」的读数，等于用币种冒名顶替刻度——
    读者会以为"表里的 1 就是 1 元"。

    **因此本项一律落 typed 缺口，不落 `determined`**（M930-3 定点纠正：币种不是金额单位，
    有币种也**不能**把「金额单位」这一栏判成已呈现）。`build_cited_source_scope` 把
    「非空 statement + 真字段名」判成 `determined`（:data:`SOURCE_SCOPE_ITEM_STATES`），
    所以这里**必须**返回空 statement，把币种这一点事实写进缺口的**否定描述**字段里——
    那一段文字说的是「本该承载它的位置是什么、以及本节权威在此处有什么、没有什么」，
    不是一个看起来存在的字段名。读者面因此看到的是：币种 `CNY` 在册，
    「金额单位」与「统计口径」本节权威**没有承载字段**。
    """
    currency = _snapshot_get(artifact, "currency")
    if not currency:
        return ("",
                "（本节权威**连币种 `currency` 都没有声明**；「金额单位」"
                "（元／千元／万元／亿元）与「统计口径」同样没有承载字段——三根轴都不声明）",
                "")
    return ("",
            f"（本节权威只声明**币种** `{currency}`，**没有**承载「金额单位」"
            "（元／千元／万元／亿元）与「统计口径」的字段——**币种不是金额单位**，"
            "本行不替它们作声明）",
            "")


def _not_provided_reader(what: str) -> Callable[[Any], tuple[str, str, str]]:
    def _read(_artifact: Any) -> tuple[str, str, str]:
        return ("", "（本次登记的来源里没有承载这一项的材料）",
                f"本次登记的三份材料都是财务报表本体（`source_class=financial_statement`），"
                f"{what}不在其中；本节权威也没有承载它的字段")
    return _read


#: 「哪一条 Contract 要求读哪一个权威字段」——本模块的**唯一**路由表。
#:
#: 逐条写明，不由 aspect_id 的名字猜：`fin_audit_opinion.reporting_period` 与
#: `fin_statements_availability.three_year_trend_coverage` 读同一个字段却是两条不同的要求，
#: 而 `fin_audit_opinion.accounting_firm` 与 `..._type` 名字同族却什么都读不到。按名字分支的
#: 写法在这张表上会同时犯这两类错。
SOURCE_SCOPE_ASPECT_ROUTING: tuple[tuple[str, str, Callable[[Any], tuple[str, str, str]]], ...] = (
    ("fin_statements_availability.balance_sheet_ready",
     "报表可得性：资产负债表", _statement_reader("balance_sheet")),
    ("fin_statements_availability.income_statement_ready",
     "报表可得性：利润表", _statement_reader("income_statement")),
    ("fin_statements_availability.cashflow_statement_ready",
     "报表可得性：现金流量表", _statement_reader("cash_flow")),
    ("fin_statements_availability.audit_opinion_ready",
     "报表可得性：审计意见", _not_provided_reader("审计意见")),
    ("fin_statements_availability.three_year_trend_coverage",
     "期间覆盖", _three_year_reader),
    ("fin_audit_opinion.audit_opinion_type",
     "审计意见类型", _not_provided_reader("审计意见的类型")),
    ("fin_audit_opinion.accounting_firm",
     "会计师事务所", _not_provided_reader("会计师事务所")),
    ("fin_audit_opinion.reporting_period",
     "报告期", _reporting_period_reader),
    ("fin_audit_opinion.consolidation_scope",
     "合并范围", _consolidation_scope_reader),
    ("fin_audit_opinion.amount_unit",
     "金额单位", _amount_unit_reader),
)


@dataclass(frozen=True)
class SourceScopeItem:
    """一条 Contract 要求的确定性答案（或一条 typed 缺口）。

    `authority_field` / `authority_value` 是这一条**从哪里读出来的**：没有它们，「已呈现」
    这句话就只是一句自述。缺口的 `authority_field` 写的是「本该承载它的位置」的**否定描述**，
    而不是一个假的字段名——一个看起来像字段名的字符串会被读成「这个字段存在但为空」。
    """

    aspect_id: str
    requirement_text: str
    label: str
    state: str
    statement: str
    authority_field: str
    authority_value: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in SOURCE_SCOPE_ITEM_STATES:
            raise CitedSourceScopeError(
                f"未登记的条目状态 {self.state!r}（封闭取值 {list(SOURCE_SCOPE_ITEM_STATES)}）")
        if self.state == "determined":
            if not self.statement:
                raise CitedSourceScopeError(
                    f"{self.aspect_id}: state='determined' 必须有一句实际呈现，不得为空")
            if self.reason:
                raise CitedSourceScopeError(
                    f"{self.aspect_id}: state='determined' 不得带缺口原因（原因码只属于缺口）")
        else:
            if self.reason not in SOURCE_SCOPE_GAP_REASONS:
                raise CitedSourceScopeError(
                    f"{self.aspect_id}: 未登记的缺口原因 {self.reason!r}（封闭取值 "
                    f"{list(SOURCE_SCOPE_GAP_REASONS)}）")
            if self.statement:
                raise CitedSourceScopeError(
                    f"{self.aspect_id}: state='gap' 不得带呈现句——缺口不许用一句话把"
                    "「没取到」写成「已经写了」")

    def to_dict(self) -> dict:
        return {"aspect_id": self.aspect_id, "requirement_text": self.requirement_text,
                "label": self.label, "state": self.state, "statement": self.statement,
                "authority_field": self.authority_field,
                "authority_value": self.authority_value, "reason": self.reason}


@dataclass(frozen=True)
class CitedSourceScopePresentation:
    """`fin_source_scope` 一栏的完整确定性呈现：版本化的路由表 + 逐条答案与缺口。"""

    section_id: str
    topic_id: str
    schema_version: str
    policy_version: str
    routing_version: str
    producer_kind: str
    contract_topic_aspects: tuple[str, ...]
    items: tuple[SourceScopeItem, ...]
    model_calls_issued: int = 0

    def __post_init__(self) -> None:
        if self.producer_kind != SOURCE_SCOPE_PRODUCER_KIND:
            raise CitedSourceScopeError(
                f"产出者身份只能是 {SOURCE_SCOPE_PRODUCER_KIND!r}，实得 {self.producer_kind!r}")
        if self.model_calls_issued != 0:
            raise CitedSourceScopeError(
                f"本栏的确定性呈现不得发起模型调用，实得 {self.model_calls_issued} 次")
        seen = [item.aspect_id for item in self.items]
        if len(set(seen)) != len(seen):
            raise CitedSourceScopeError(f"同一栏出现重复条目：{seen}")
        missing = [a for a in self.contract_topic_aspects if a not in set(seen)]
        if missing:
            raise CitedSourceScopeError(
                f"Contract 要求 {missing} 在呈现里没有条目：未取到的项目也必须**逐条**出现"
                "（留白会让「没问」看起来像「问过了」）")

    @property
    def determined(self) -> tuple[SourceScopeItem, ...]:
        return tuple(i for i in self.items if i.state == "determined")

    @property
    def gaps(self) -> tuple[SourceScopeItem, ...]:
        return tuple(i for i in self.items if i.state == "gap")

    def identity_body(self) -> dict:
        #: `model_calls_issued` 进身份体：它恒为 0，而「这一栏有没有花过模型额度」是这条产物
        #: 最容易被误读的一个读数，因此让它在身份体里恒等于 0，而不是靠读者去别处推断。
        return {"section_id": self.section_id, "topic_id": self.topic_id,
                "schema_version": self.schema_version,
                "policy_version": self.policy_version,
                "routing_version": self.routing_version,
                "producer_kind": self.producer_kind,
                "contract_topic_aspects": list(self.contract_topic_aspects),
                "items": [i.to_dict() for i in self.items],
                "model_calls_issued": self.model_calls_issued}

    def fingerprint(self) -> str:
        import json
        body = json.dumps(self.identity_body(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {**self.identity_body(), "fingerprint": self.fingerprint()}


def build_cited_source_scope(*, section_id: str, topic_id: str, requirement: Any,
                             artifact: Any) -> CitedSourceScopePresentation:
    """按 Contract 的 aspect 次序逐条读本节财务权威，产出确定性呈现。

    Contract 是**驱动方**：遍历的是 `requirement.aspects` 的次序，而不是路由表的次序。路由表里
    没有登记的 aspect 是一条构造期拒绝——「Contract 要、而本模块不知道怎么读」这件事必须在
    这里停住，不能悄悄少呈现一条。
    """
    aspects = tuple(getattr(requirement, "aspects", ()) or ())
    if not aspects:
        raise CitedSourceScopeError(
            f"topic {topic_id!r} 在冻结 Contract 投影里没有任何 aspect：没有要求就没有呈现")
    readers: Mapping[str, Callable[[Any], tuple[str, str, str]]] = {
        aspect_id: reader for aspect_id, _label, reader in SOURCE_SCOPE_ASPECT_ROUTING}
    labels: Mapping[str, str] = {
        aspect_id: label for aspect_id, label, _reader in SOURCE_SCOPE_ASPECT_ROUTING}

    items: list[SourceScopeItem] = []
    aspect_ids: list[str] = []
    for aspect in aspects:
        aspect_id = str(getattr(aspect, "aspect_id", "") or "")
        requirement_text = str(getattr(aspect, "requirement_text", "") or "")
        if not aspect_id:
            raise CitedSourceScopeError("冻结 Contract 投影里有一条第 aspect_id 为空的栏目")
        if aspect_id not in readers:
            raise CitedSourceScopeError(
                f"Contract 栏目 {aspect_id!r} 在本模块的路由表里没有登记读法（版本 "
                f"{SOURCE_SCOPE_ROUTING_VERSION}）：宁可在构造期停住，也不得少呈现一条已"
                "要求的内容")
        aspect_ids.append(aspect_id)
        statement, field, value = readers[aspect_id](artifact)
        if statement and field and not field.startswith("（"):
            items.append(SourceScopeItem(
                aspect_id=aspect_id, requirement_text=requirement_text,
                label=labels[aspect_id], state="determined",
                statement=statement, authority_field=field, authority_value=str(value)))
        else:
            items.append(SourceScopeItem(
                aspect_id=aspect_id, requirement_text=requirement_text,
                label=labels[aspect_id], state="gap", statement="",
                authority_field=field, reason="not_provided_by_registered_sources"))

    return CitedSourceScopePresentation(
        section_id=section_id, topic_id=topic_id,
        schema_version=CITED_SOURCE_SCOPE_SCHEMA_VERSION,
        policy_version=CITED_SOURCE_SCOPE_POLICY_VERSION,
        routing_version=SOURCE_SCOPE_ROUTING_VERSION,
        producer_kind=SOURCE_SCOPE_PRODUCER_KIND,
        contract_topic_aspects=tuple(aspect_ids), items=tuple(items))


def render_cited_source_scope_markdown(
        presentation: CitedSourceScopePresentation, *, requirement_text_by_aspect: Mapping[
            str, str] | None = None) -> str:
    """一栏的读者面：逐条给出 `Contract 原话 / 本次读数 / 取值字段`，缺口单列。"""
    lines: list[str] = []
    lines.append(f"## {presentation.topic_id}（确定性呈现，"
                 f"`{presentation.producer_kind}`，模型调用 {presentation.model_calls_issued} 次）")
    lines.append("")
    lines.append("> 本栏**不经过模型**：逐条取值来自本节财务权威自己的字段，"
                 "逐条标出读的是哪一个字段。缺口的措辞与取值字段分列，"
                 "「没取到」不得被读成「已核对通过」。")
    lines.append("")
    lines.append(f"- 路由表版本：`{presentation.routing_version}`"
                 f"　产物政策：`{presentation.policy_version}`")
    lines.append(f"- 已呈现 **{len(presentation.determined)}** 条，"
                 f"typed 缺口 **{len(presentation.gaps)}** 条，"
                 f"共 **{len(presentation.items)}** 条（= 冻结 Contract 本 topic 的 aspect 数）")
    lines.append("")
    lines.append("| # | Contract 要求 | 本次读数 | 取值字段 |")
    lines.append("|---|---|---|---|")
    for index, item in enumerate(presentation.items, start=1):
        reading = item.statement if item.state == "determined" else \
            f"**缺口**（`{item.reason}`）：本栏目在本次登记来源里没有可取的项"
        lines.append(f"| {index} | {item.requirement_text} | {reading} "
                     f"| `{item.authority_field}` |")
    lines.append("")
    gaps = presentation.gaps
    if gaps:
        lines.append(f"### 本栏缺口（{len(gaps)} 条；逐条带冻结 Contract 原话）")
        lines.append("")
        for item in gaps:
            lines.append(f"- `{item.aspect_id}`（{item.requirement_text}）："
                         f"{item.authority_field}")
        lines.append("")
    return "\n".join(lines)

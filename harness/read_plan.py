"""读取计划（`rpo-1`）：把**已经导航到的**有界读集按「实质业务正文优先」重排。

这一层修的是一条**消费侧**断点，不碰导航：读集**集合**由导航决定、本层一字不改；本层只
决定**先读什么**。断点是——`inspect_outline_materials` 按 `node_ids` 顺序逐 span 消费，
`spans_read >= max_spans` 即停（`harness/tree_tools.py`），而读集顺序是「读根优先级 →
子树文档序」、两条补读规则（`anp-7`/`anp-9`）的根又排在本层读根**之后**。于是**实质业务
分支恰好排在最后**，被同一份读集的前几个名额挤死。

本层给的排序依据全部是**既有、公司无关的结构量**，没有词表、没有公司名、没有页码：

1. **实质正文量** `index.body_chars(node)`（本节点**自有**准入正文字符数）降序。读集已经被
   导航限定在「关于这一栏的那几节」里，因此在这一集合内部，「这一节自己写了多少字」正是
   把**并列业务分支**与**模板勾选 / 短残句 / 空容器**分开的量（实测：2025 年报动力业务 1321、
   储能业务 869、新兴领域 1436 对 `（5）营业成本构成` 4、`（7）…重大变化…` 8；容器自有 0）。
2. **Contract 键命中数**降序（`relevance_hits`）。用的是**既有**完整标签段判据
   （`NAV_SEGMENT_RULE_ID`），键是冻结 Contract 派生的 **topic 级**并集，因此与
   「这一栏为什么被导航到这里」同源。它只作**次序**，绝不产生任何支持判定。
3. **文档序** `index.document_order(node)` 升序——确定性总序的最后一档（同分同量时先出现者先读）。

**桶**（`substance_tier`）把「量」变成可在产物里逐条对账的三档：`0` = 自有正文 ≥
`READ_PLAN_MIN_SUBSTANTIVE_BODY`（实质），`1` = 有自有正文但不足阈值（短），`2` = 自有正文
为零（容器 / 无自有文字）。阈值**逐字复用**既有 `anp-7`/`anp-9` 判「这一节自带实质正文」
时用的那一个常量，本层**不另定第二个数字**。

**不因标题措辞降级**：本层从不看标题里有没有「概述」「其他」这类词——2024 年报的 `1、概述`
自有正文 145 字，按量排在全部短残句与空容器之前，是**实质档**。公司名、证券代码、页码、
答案关键词都不参与。

**降级**：`index.body_measure_available` 为假（调用方没给逐节点正文量）时，本层**不猜**、
**不重排**，交回导航原序并记 `body_measure_unavailable`——与 `anp-7`/`anp-9` 遇到同一情形时
的处置逐字同构。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

from document_structure import navigation as NAV

__all__ = [
    "READ_PLAN_MIN_SUBSTANTIVE_BODY", "READ_PLAN_RULE_VERSION", "READ_PLAN_STATUSES",
    "ReadPlan", "ReadPlanRow", "plan_read_order", "topic_plan_keys",
]

#: 规则版本（进事件、进复用键；换规则即换版本）。
READ_PLAN_RULE_VERSION = "rpo-1"

#: 计划的状态（封闭集合）。
#:
#: - `ordered`：真的重排过（或本来就已是该顺序）；
#: - `not_needed`：读集为空或只有一个节点，没有可排的东西；
#: - `body_measure_unavailable`：没有逐节点正文量 ⇒ 交回导航原序（不猜）。
READ_PLAN_STATUSES = ("ordered", "not_needed", "body_measure_unavailable")

#: 「这一节自带实质正文」的阈值：**逐字复用**既有规则已用的那一个
#: （`document_structure.navigation._SUPPLEMENT_MIN_BODY_CHARS`），本模块不另定数字。
#: 名字若在上游被改动，这里会立刻 `AttributeError` 而不是静默换一个阈值。
READ_PLAN_MIN_SUBSTANTIVE_BODY = NAV._SUPPLEMENT_MIN_BODY_CHARS


@dataclass(frozen=True)
class ReadPlanRow:
    """一个读集成员在计划里的**逐条依据**（只读、确定性、可对账）。"""

    node_id: str
    substance_tier: int
    body_chars: int
    relevance_hits: int
    document_order: int
    rank: int

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "rank": self.rank,
            "substance_tier": self.substance_tier,
            "body_chars": self.body_chars,
            "relevance_hits": self.relevance_hits,
            "document_order": self.document_order,
        }


@dataclass(frozen=True)
class ReadPlan:
    """读集的**有序消费计划**。`ordered_node_ids` 与入参读集**同集合**，只是次序。"""

    ordered_node_ids: tuple[str, ...]
    plan_id: str
    rule_version: str
    status: str
    rows: tuple[ReadPlanRow, ...]
    #: 导航原序（对账用：证明本层只做**置换**、集合一字未改）。
    navigation_node_ids: tuple[str, ...]

    @property
    def reordered(self) -> bool:
        return self.ordered_node_ids != self.navigation_node_ids

    def to_dict(self) -> dict:
        return {
            "rule_version": self.rule_version,
            "plan_id": self.plan_id,
            "status": self.status,
            "reordered": self.reordered,
            "min_substantive_body": READ_PLAN_MIN_SUBSTANTIVE_BODY,
            "navigation_node_ids": list(self.navigation_node_ids),
            "ordered_node_ids": list(self.ordered_node_ids),
            "rows": [r.to_dict() for r in self.rows],
        }


def _relevance_hits(index: NAV.NavigationIndex, keys: Sequence[str],
                    node_id: str) -> int:
    """topic 级 Contract 键在本节点**标题 ∪ 祖先标题**上的完整标签段命中数。

    只看「这一段标签是不是那个键」，与 `NAV_SEGMENT_RULE_ID` 同一判据、同一实现
    （`NAV.segment_hits`），不新建词表。命中多寡只影响**先读谁**。
    """
    if not keys:
        return 0
    hit: set[str] = set()
    hit.update(NAV.segment_hits(index, keys, node_id))
    for ancestor in index.ancestors_of(node_id):
        hit.update(NAV.segment_hits(index, keys, ancestor))
    return len(hit)


def _plan_id(rule_version: str, ordered: Sequence[str],
             rows: Sequence[ReadPlanRow]) -> str:
    """计划身份：只由规则版本与**排序结果及其依据**决定，不含任何结果字段。"""
    payload = "|".join(
        [rule_version]
        + [f"{r.node_id}:{r.substance_tier}:{r.body_chars}:{r.relevance_hits}:"
           f"{r.document_order}" for r in rows]
        + list(ordered))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def topic_plan_keys(profile: NAV.AspectNavigationProfile, topic_id: str
                    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """本 topic **全部** aspect 的 Contract 键并集 `(nav_keys, parent_keys)`。

    必须是 **topic 级**而不是逐 aspect 的：计划要能被同 topic 的多个栏目**共用一次**
    真实工具结果（需求原文：「同一份来源、同一读集、同一计划可跨栏目共享一次实际工具结果」）。
    逐 aspect 取值会让每个栏目对同一读集算出不同的 `plan_id`，复用当场失效——那正是本批
    要修的「先执行的栏目把名额吃光」。并集只由冻结 Contract 派生，与公司/文档无关。
    """
    nav_keys: set[str] = set()
    parent_keys: set[str] = set()
    for entry in profile.entries:
        if entry.topic_id != topic_id:
            continue
        nav_keys.update(str(k) for k in entry.nav_keys)
        parent_keys.update(str(k) for k in entry.parent_keys)
    return tuple(sorted(nav_keys)), tuple(sorted(parent_keys))


def plan_read_order(index: NAV.NavigationIndex,
                    read_node_ids: Iterable[str], *,
                    topic_nav_keys: Sequence[str] = (),
                    topic_parent_keys: Sequence[str] = ()) -> ReadPlan:
    """由导航读集算出**有序消费计划**（纯函数；集合一字不改）。

    `topic_nav_keys` / `topic_parent_keys` 是**冻结 Contract** 派生的 topic 级键并集
    （本层只读它们做次序，不判任何栏目是否获得支持）。
    """
    if not isinstance(index, NAV.NavigationIndex):
        raise NAV.NavigationError("读取计划需要 NavigationIndex")
    ordered_input = tuple(str(n) for n in read_node_ids)
    for node_id in ordered_input:
        if index.node(node_id) is None:
            raise NAV.NavigationError(
                f"读集里的节点 {node_id!r} 不在这棵树上：读取计划不得对未定位的节点排序")

    if len(ordered_input) <= 1:
        return ReadPlan(ordered_node_ids=ordered_input,
                        plan_id=_plan_id(READ_PLAN_RULE_VERSION, ordered_input, ()),
                        rule_version=READ_PLAN_RULE_VERSION, status="not_needed",
                        rows=(), navigation_node_ids=ordered_input)

    if not index.body_measure_available:
        # **不猜**：没有逐节点正文量就不重排，交回导航原序并如实记状态。
        return ReadPlan(ordered_node_ids=ordered_input,
                        plan_id=_plan_id(READ_PLAN_RULE_VERSION, ordered_input, ()),
                        rule_version=READ_PLAN_RULE_VERSION,
                        status="body_measure_unavailable", rows=(),
                        navigation_node_ids=ordered_input)

    keys = tuple(dict.fromkeys(
        [str(k) for k in topic_nav_keys] + [str(k) for k in topic_parent_keys]))
    rows = []
    for node_id in ordered_input:
        body = index.body_chars(node_id)
        tier = (0 if body >= READ_PLAN_MIN_SUBSTANTIVE_BODY
                else (1 if body > 0 else 2))
        rows.append(ReadPlanRow(
            node_id=node_id, substance_tier=tier, body_chars=body,
            relevance_hits=_relevance_hits(index, keys, node_id),
            document_order=index.document_order(node_id), rank=0))
    ordered_rows = tuple(sorted(
        rows, key=lambda r: (r.substance_tier, -r.body_chars, -r.relevance_hits,
                             r.document_order)))
    ranked = tuple(
        ReadPlanRow(node_id=r.node_id, substance_tier=r.substance_tier,
                    body_chars=r.body_chars, relevance_hits=r.relevance_hits,
                    document_order=r.document_order, rank=i)
        for i, r in enumerate(ordered_rows))
    ordered = tuple(r.node_id for r in ranked)
    return ReadPlan(ordered_node_ids=ordered,
                    plan_id=_plan_id(READ_PLAN_RULE_VERSION, ordered, ranked),
                    rule_version=READ_PLAN_RULE_VERSION, status="ordered",
                    rows=ranked, navigation_node_ids=ordered_input)

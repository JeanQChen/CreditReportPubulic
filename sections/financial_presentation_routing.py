"""财务**呈现层**的「指标 → 栏目」路由声明（`fpr-1`）。

本模块回答的问题是这条链上至今**没有**回答的那一个：「读者在某一栏里读到的那个指标，
本来是不是这一栏的？」

它**不是**事实权威，也不冒充事实权威。三件事必须同时成立，缺一条这条声明就变成一次伪证：

* **它在呈现层，不在权威层。** 财务 workflow 至今没有 aspect 级归属（
  `pack_writer.scan_financial` 的 docstring 写着「不编造 aspect 状态」，并且逐条给事实的
  `aspect_ids` 传空元组）。本模块**不**去填那个字段：`authority_facts[].aspect_ids` 在本链
  上**始终为空**，声明走自己的一路（`source = presentation_layer_declaration`）。把路由写进
  `aspect_ids`，等于让读者以为「权威自己认领了这一栏」，那是一句没收到的授权。
* **它是声明，不是推导。** 每条路由都是本文件里写明的一行 `metric_code → aspect_id`，带版本号
  `fpr-1`。它不从指标名猜、不含公司/行业分支、也没有关键词表。
* **它只约束**「这个指标可以出现在哪一栏」，**不**证明那一栏的 Contract 要求已被完整满足。
  冻结 Contract 里某一栏的要求若没有任何指标落进去，本模块只留下**栏目缺口**（
  :class:`PresentationColumnGap`），不拿别的栏目的事实去顶——「流动比率」不充「净资产水平」，
  「有息负债总额」不充「刚性债务结构」，这两条是同一枚硬币的两面。

判据的用途有两处，两处都只**核对**、不改写：

1. 确定性指标表的**展示层级**：某行所在栏在冻结 Contract 里声明 `display_tier=diagnostic_only`
   时，这一行必须真的落在诊断档；带 `PROXY_FINANCE_EXPENSES` 的代理值不得混进正文主指标表。
2. 正文的**小节归属**：一句正文写进某一栏，而它所引事实经本声明路由到**另一栏**时，
   `sentence_check` 的呈现层栏目归属轴如实判硬错误。

本模块纯确定性：不发起任何模型调用、不做任何计算、不读库。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from financial_v2 import derived_facts as FD

__all__ = [
    "FINANCIAL_PRESENTATION_ROUTING_VERSION",
    "PRESENTATION_LAYER_SOURCE",
    "METRIC_COLUMN_ROUTING",
    "ROUTED_COLUMN_ORDER",
    "PresentationRoutingError",
    "MetricColumnRoute",
    "PresentationColumnGap",
    "FinancialPresentationRouting",
    "build_presentation_routing",
    "routing_for_metric_code",
]

#: 路由声明的版本。**任何一条映射、标签或缺口理由的变化都必须升版**——这份声明会进输入清单
#: 的身份体，版本不动而内容动了，读者面就会拿旧标称去认新内容。
#:
#: `fpr-2`（M930-3 阶段 A v2）：资产负债表科目（`fin_balance_structure`）从「本节之外」回到
#: 本节（profile v2 把该 topic 纳入 `selected_topics`），于是这条声明要**同时**覆盖两个 topic
#: 的栏目。两处随之改变，都不是措辞问题：
#:
#: 1. 映射表新增 `fin_balance_structure.*` 的 17 条科目 code（理由见 `METRIC_COLUMN_ROUTING`）；
#: 2. 身份体新增 `route_topics`——「同一份映射用在别的 topic 上不是同一件事」这句话在
#:    `fpr-1` 里只由单值 `contract_topic` 承担，一个声明覆盖两个 topic 之后它承担不了。
#:
#: **缺口账仍然只覆盖写正文的那个 topic**（`contract_topic`）。这不是漏算：`fin_balance_structure`
#: 三栏的覆盖由它自己的确定性呈现产物逐条回答（`sections.cited_balance_structure`），
#: 本声明的那一套减法（「有多少栏没有指标可落」）用在**不写正文**的 topic 上会答错题——
#: 该 topic 的 `fifteen_pct_forced_analysis` 根本不是一组独立的指标，而是对同一组科目施加的
#: **筛选**（冻结 Contract 的 `complete_set_rule` 逐字写着「占资产或负债15%以上的科目强制分析」），
#: 按「有没有 code 落在这一栏」去数它，必然数出一个不存在的缺口。
FINANCIAL_PRESENTATION_ROUTING_VERSION = "fpr-2"

#: 声明的来源标记。它进每一份产物的身份体，因此「这条栏目是从哪来的」在读者面可读回：
#: 权威登记（`aspect_ids`）与本声明是**两件事**，不得互相冒名。
PRESENTATION_LAYER_SOURCE = "presentation_layer_declaration"

#: 指标 code → 冻结 Contract 的栏目 `aspect_id`。**取指标 code 而不是中文名**：code 是权威事实
#: 自己的字段（`FinancialFactProjection.code`），中文名会被同义改写、会在别的章节里重名。
#:
#: 六条映射各自对应一句可核对的业务话：
#:
#: * 流动比率 / 速动比率 —— 短期偿债能力；
#: * 资产负债率 / 权益乘数 —— 长期偿债能力（杠杆表述）；
#: * 资产负债率的跨期变动（`SOLV_DEBT_RATIO_DELTA_PP`，派生展示事实）—— 与它的输入指标同栏：
#:   变动说的是同一件事在两个期间之间的走向，不是新的一栏；
#: * 有息负债余额 —— 有息负债；
#: * 利息保障倍数 —— 利息费用代理（该栏在冻结 Contract 里就是 `display_tier=diagnostic_only`
#:   / `content_role=audit_only`，因为本次口径用财务费用顶了利息费用）。
#:
#: 冻结 Contract `fin_solvency` 的六栏里，`net_asset_level` 与 `rigid_debt_structure` **不在**
#: 这张表上：本次选中事实里没有任何一条指标能证明「净资产水平」或「完整债务结构」。它们不是
#: 被漏掉，是被**如实留成缺口**（见 :func:`build_presentation_routing` 的 `column_gaps`）。
#:
#: `fpr-2` 起这张表还覆盖 `fin_balance_structure` 的两栏（下面两个常量）：该 topic 进入
#: profile v2 的 `selected_topics` 之后，资产负债表科目事实回到本节，而**确定性指标表**
#: （`sections.cited_financial_table`）对「清单里有、却落不到任何一栏」的 code 是**构造期拒绝**
#: （`metric_column_not_declared`）。不给它们登记栏目，本节会在建表那一步整节停下来——
#: 那不是更严格，那是把一次注定失败的运行伪装成可运行。
#:
#: 资产负债表**总量与结构**类科目：它们答的是「资产结构 / 负债结构」这一栏（冻结 Contract 该 aspect
#: 的 `required_fields` 逐字是 `[资产结构, 负债结构]`）。
_BS_STRUCTURE_CODES: tuple[str, ...] = (
    "TOTAL_ASSETS", "CURRENT_ASSETS", "NON_CURRENT_ASSETS",
    "TOTAL_LIABILITIES", "CURRENT_LIABILITIES", "NON_CURRENT_LIABILITIES",
    "TOTAL_EQUITY", "TOTAL_LIABILITIES_AND_EQUITY",
)

#: 资产负债表**具体科目**：它们答的是「重大科目变化」这一栏（`required_fields` 逐字是
#: `[重大科目, 变化幅度, 变化原因]`）。
#:
#: 这一组与 `_BS_STRUCTURE_CODES` 是**互斥且穷尽** `financial_worker._BALANCE_SHEET_ITEMS`
#: 的两半（该表是「哪些 code 属于资产负债表」的唯一来源，本表只回答「归属哪一栏」）。
#: 两表的成员资格由 `evals/test_m930_3_financial_presentation.py` 逐条对账——不在这里 import
#: 上游模块，是为了让「本声明有哪几条」在读者面上就看得到，而不是转到另一个文件去数。
_BS_ACCOUNT_CODES: tuple[str, ...] = (
    "CASH_AND_EQUIVALENTS", "INVENTORY", "ACCOUNTS_RECEIVABLE", "FIXED_ASSETS",
    "GOODWILL", "INTANGIBLE_ASSETS",
    "SHORT_TERM_BORROWINGS", "LONG_TERM_BORROWINGS", "BONDS_PAYABLE",
)

#: 科目 code → 冻结 Contract 的栏目 `aspect_id` 的**第二段**（`fin_balance_structure` 那一半）。
#:
#: 与前半段（`fin_solvency`）分开写，理由与 `financial_worker._STRUCTURE_ITEM_TO_TOPIC` 分开写
#: 是同一个：两半的 code 来自**不同的权威字段**（指标 code 来自 `FinancialFactProjection.code`
#: 的 formula 侧，科目 code 来自它的 `standard_item_code` 侧）。合成一张匿名大表会把这件事藏起来。
#:
#: `fin_balance_structure` 的**第三栏** `fifteen_pct_forced_analysis` **不在这张表上**，而且
#: 这不是遗漏：该栏的冻结 Contract 要求是「占资产或负债 15% 以上的科目**强制分析**」——
#: 它是**对上面这 9 条科目施加的筛选**，不是另有一组 code。给它编一组 code 出来才是错的
#: （那等于说「有一批数字只属于 15% 这一栏」）。它由确定性呈现器在同一组科目上算出。
_BS_CODE_TO_COLUMN: dict[str, str] = {
    **{code: "fin_balance_structure.balance_structure"
       for code in _BS_STRUCTURE_CODES},
    **{code: "fin_balance_structure.major_account_changes"
       for code in _BS_ACCOUNT_CODES},
}

METRIC_COLUMN_ROUTING: dict[str, str] = {
    "SOLV_CURRENT_RATIO": "fin_solvency.short_term_solvency",
    "SOLV_QUICK_RATIO": "fin_solvency.short_term_solvency",
    "SOLV_DEBT_RATIO": "fin_solvency.long_term_solvency",
    "SOLV_EQUITY_MULT": "fin_solvency.long_term_solvency",
    FD.DELTA_FACT_CODE: "fin_solvency.long_term_solvency",
    "INTEREST_BEARING_DEBT": "fin_solvency.interest_bearing_debt",
    "SOLV_INTEREST_COVER": "fin_solvency.interest_expense_proxy",
    **_BS_CODE_TO_COLUMN,
}

#: 本声明的**栏目次序**（读者面按此排序，不由字典遍历顺序决定）。
ROUTED_COLUMN_ORDER: tuple[str, ...] = (
    "fin_solvency.short_term_solvency",
    "fin_solvency.long_term_solvency",
    "fin_solvency.interest_bearing_debt",
    "fin_solvency.interest_expense_proxy",
    "fin_balance_structure.balance_structure",
    "fin_balance_structure.major_account_changes",
    "fin_balance_structure.fifteen_pct_forced_analysis",
)

#: 栏目缺口的**封闭**原因码集合。
COLUMN_GAP_REASONS = (
    #: 冻结 Contract 里有这一栏，本次选中事实里没有任何指标经本声明落到这一栏。
    "no_registered_metric_in_selected_facts",
)

#: 未路由事实的**封闭**原因码集合。
UNROUTED_FACT_REASONS = (
    #: 这条事实的指标 code 不在本声明的映射表里：它不属于 `fin_solvency` 的任何一栏，
    #: 因此**不得**被写进其中任何一栏的正文。
    "no_column_declared_for_metric_code",
)


class PresentationRoutingError(Exception):
    """路由声明的构造期拒绝。**不是**一次运行结果。"""


@dataclass(frozen=True)
class MetricColumnRoute:
    """一条映射：一个指标 code 落在冻结 Contract 的哪一栏、那一栏在 Contract 里的展示层级。"""

    metric_code: str
    presentation_column: str
    contract_display_tier: str

    def to_dict(self) -> dict:
        return {"metric_code": self.metric_code,
                "presentation_column": self.presentation_column,
                "contract_display_tier": self.contract_display_tier}


@dataclass(frozen=True)
class PresentationColumnGap:
    """一栏**没有**任何本次事实可证明时留下的准确缺口。

    `contract_requirement_text` 逐字取自冻结 Contract——缺口说的话必须与要求说的话是同一句，
    否则读者无法判断缺的到底是哪一部分。
    """

    column: str
    contract_requirement_text: str
    contract_display_tier: str
    reason: str

    def to_dict(self) -> dict:
        return {"column": self.column,
                "contract_requirement_text": self.contract_requirement_text,
                "contract_display_tier": self.contract_display_tier,
                "reason": self.reason}


@dataclass(frozen=True)
class FinancialPresentationRouting:
    """一次运行里生效的呈现层路由声明（版本化、可回查、可进身份体）。"""

    routing_version: str
    source: str
    #: **缺口账**所覆盖的那个 topic（写正文的那一个）。`column_gaps` 是它的栏目做减法得来的。
    contract_topic: str
    #: 本声明的**栏目可以取自哪些 topic**（含 `contract_topic`）。`fpr-2` 起它不再恒为单值：
    #: 一个财务节里同时有写正文的 topic 与确定性呈现的 topic 时，后者的事实同样要各归其栏。
    route_topics: tuple[str, ...]
    routes: tuple[MetricColumnRoute, ...]
    fact_columns: tuple[tuple[str, str, str], ...]  # (fact_id, metric_code, column)
    unrouted_facts: tuple[tuple[str, str, str], ...]  # (fact_id, metric_code, reason)
    column_gaps: tuple[PresentationColumnGap, ...]

    def __post_init__(self) -> None:
        if self.source != PRESENTATION_LAYER_SOURCE:
            raise PresentationRoutingError(
                f"路由声明的来源标记只能是 {PRESENTATION_LAYER_SOURCE!r}，实得 "
                f"{self.source!r}：换一个标记就是让声明冒充别的轴")
        for gap in self.column_gaps:
            if gap.reason not in COLUMN_GAP_REASONS:
                raise PresentationRoutingError(
                    f"未登记的栏目缺口原因 {gap.reason!r}（封闭取值 "
                    f"{list(COLUMN_GAP_REASONS)}）")
        for _fid, _code, reason in self.unrouted_facts:
            if reason not in UNROUTED_FACT_REASONS:
                raise PresentationRoutingError(
                    f"未登记的未路由原因 {reason!r}（封闭取值 "
                    f"{list(UNROUTED_FACT_REASONS)}）")

    # -- 查表 ----------------------------------------------------------------
    def column_for_metric_code(self, metric_code: str) -> str | None:
        code = str(metric_code or "").strip()
        for route in self.routes:
            if route.metric_code == code:
                return route.presentation_column
        return None

    def column_for_fact_id(self, fact_id: str) -> str | None:
        fid = str(fact_id or "").strip()
        for entry_fid, _code, column in self.fact_columns:
            if entry_fid == fid:
                return column
        return None

    def display_tier_for_column(self, column: str) -> str | None:
        col = str(column or "").strip()
        for route in self.routes:
            if route.presentation_column == col:
                return route.contract_display_tier
        for gap in self.column_gaps:
            if gap.column == col:
                return gap.contract_display_tier
        return None

    @property
    def gap_columns(self) -> tuple[str, ...]:
        return tuple(gap.column for gap in self.column_gaps)

    # -- 身份 ----------------------------------------------------------------
    def identity_body(self) -> dict:
        """身份体：「这一栏是这么路由的」这件事的全部可回查内容。

        `contract_topic` 进身份体：同一份映射用在别的 topic 上（若那个 topic 有同名的
        `aspect_id`）不是同一件事，身份必须跟着变。
        """
        return {
            "routing_version": self.routing_version,
            "source": self.source,
            "contract_topic": self.contract_topic,
            "route_topics": list(self.route_topics),
            "routes": [r.to_dict() for r in self.routes],
            "fact_columns": [{"fact_id": f, "metric_code": c, "presentation_column": col}
                             for f, c, col in self.fact_columns],
            "unrouted_facts": [{"fact_id": f, "metric_code": c, "reason": r}
                               for f, c, r in self.unrouted_facts],
            "column_gaps": [g.to_dict() for g in self.column_gaps],
        }

    def fingerprint(self) -> str:
        import json
        body = json.dumps(self.identity_body(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {**self.identity_body(), "routing_fingerprint": self.fingerprint()}


def routing_for_metric_code(metric_code: str) -> str | None:
    """映射表的**唯一**读入口：未知 code 返回 `None`（调用方负责把它记成一条未路由事实）。"""
    return METRIC_COLUMN_ROUTING.get(str(metric_code or "").strip())


def build_presentation_routing(*, gap_scope_topic: str, requirement: Any,
                               facts: Iterable[Any],
                               extra_route_requirements: Mapping[str, Any] | None = None,
                               ) -> FinancialPresentationRouting | None:
    """构造本节的呈现层路由声明；**没有**写正文 topic 的节返回 `None`。

    `requirement` 是**写正文那个 topic** 的冻结 Contract 投影（由调用方从 `inputs.requirements`
    里按 topic 取）：它承担两件事——其一，它的栏目集合就是 `column_gaps` 做减法的被减数；
    其二，`gap_scope_topic` 这个名字必须与它一致，否则这份声明会拿 A 的栏目去说 B 的缺口。

    `extra_route_requirements`（`fpr-2` 新增）是**同一节里其他确定性呈现 topic** 的投影，
    键是 topic_id。它们**只**参与一件事：让 `METRIC_COLUMN_ROUTING` 里属于这些 topic 的栏目
    **认得出来**（含它们的 `display_tier`）。它们**不**参与缺口减法——理由写在
    `FINANCIAL_PRESENTATION_ROUTING_VERSION` 的 `fpr-2` 那一段：在那类 topic 上按「有没有 code
    落在这一栏」数缺口会数出不存在的东西。

    `facts` 是**本节这次真的要投递的事实行**（不是整只财务 artifact 的全部事实）。取本节清单
    而不是整只 artifact，是因为这份声明要进本节清单的身份体，也要被本节逐句核对：把本节看不到
    的事实写进声明，会让 `unrouted_facts` 里出现一批「本节根本没有的指标」，读者无法判断那个
    读数意味着什么。

    三样都是**既有**输入：本函数不读库、不问模型、不新造事实。

    栏目缺口由**减法**得出，不是另一张硬编码表：冻结 Contract 里属于 `gap_scope_topic` 的每一栏，
    减去本声明路由到的那些栏，剩下的就是本次没有指标可证明的栏——因此冻结 Contract 换一版
    （多一栏、少一栏）时，缺口读数自动跟着变，不会留下一句对不上的旧话。
    """
    if requirement is None:
        return None
    topic_id = str(gap_scope_topic or "")
    if not topic_id:
        raise PresentationRoutingError(
            "没有给出写正文的 topic（`gap_scope_topic` 为空）：没有栏目集合就无从判定"
            "「哪一栏是缺口」，本函数拒绝构造一份凭空的声明")

    def _aspect_index(req: Any) -> tuple[dict[str, Any], list[str]]:
        by_id: dict[str, Any] = {}
        order: list[str] = []
        for aspect in (getattr(req, "aspects", ()) or ()):
            aspect_id = str(getattr(aspect, "aspect_id", "") or "")
            if not aspect_id:
                continue
            by_id[aspect_id] = aspect
            order.append(aspect_id)
        return by_id, order

    aspect_by_id, order = _aspect_index(requirement)
    if not aspect_by_id:
        raise PresentationRoutingError(
            f"topic {topic_id!r} 在本次运行里没有冻结 Contract 投影的栏目：没有栏目集合就无从"
            "判定「哪一栏是缺口」，本函数拒绝构造一份凭空的声明")

    route_topics: list[str] = [topic_id]
    for topic, req in sorted(dict(extra_route_requirements or {}).items()):
        name = str(topic or "")
        if not name or name == topic_id or req is None:
            continue
        extra_by_id, _extra_order = _aspect_index(req)
        for aspect_id, aspect in extra_by_id.items():
            #: 同名 aspect 两处都出现 ⇒ 两份投影不一致，取哪一份都是替另一边作决定。
            if aspect_id in aspect_by_id:
                raise PresentationRoutingError(
                    f"aspect {aspect_id!r} 同时出现在 topic {topic_id!r} 与 {name!r} 的投影里："
                    "同一栏有两份声明时本函数不替它们挑一份")
            aspect_by_id[aspect_id] = aspect
        route_topics.append(name)

    routed_columns: list[str] = []
    for column in ROUTED_COLUMN_ORDER:
        if column in aspect_by_id and column not in routed_columns:
            routed_columns.append(column)

    def _tier(aspect_id: str) -> str:
        aspect = aspect_by_id.get(aspect_id)
        return str(getattr(aspect, "display_tier", "") or "") if aspect is not None else ""

    routes = tuple(
        MetricColumnRoute(metric_code=code, presentation_column=METRIC_COLUMN_ROUTING[code],
                          contract_display_tier=_tier(METRIC_COLUMN_ROUTING[code]))
        for code in sorted(METRIC_COLUMN_ROUTING)
        if METRIC_COLUMN_ROUTING[code] in aspect_by_id)
    #: 映射表按**两个 topic 族**登记（`fin_solvency` 指标 + `fin_balance_structure` 科目），
    #: 而一次运行可能只选中其中一族——那一族的 code 本次**本来就不该有**归属，不是缺陷。
    #: 真正的缺陷只有一种：**code 所属的 topic 在本次投影里，而它指的那一栏不在**。
    #: 那说明冻结 Contract 的这一栏和映射表对不上（改名、删栏、抄错），此时静默丢掉这条 code
    #: 会让「这一条已登记」与「这一条其实没生效」在产物上长得一样。
    stale = sorted(
        code for code in METRIC_COLUMN_ROUTING
        if METRIC_COLUMN_ROUTING[code].rsplit(".", 1)[0] in set(route_topics)
        and METRIC_COLUMN_ROUTING[code] not in aspect_by_id)
    if stale:
        raise PresentationRoutingError(
            f"映射表里的 code {stale} 指向的栏目不在**它自己那个 topic** 的本次投影里"
            f"（topic 已在本次范围内，已见栏目 {sorted(aspect_by_id)}）："
            "不认得它就不许把它当成已登记")

    fact_columns: list[tuple[str, str, str]] = []
    unrouted: list[tuple[str, str, str]] = []
    for fact in sorted(tuple(facts or ()), key=lambda f: str(getattr(f, "fact_id", ""))):
        fact_id = str(getattr(fact, "fact_id", "") or "")
        code = str(getattr(fact, "code", "") or "")
        column = METRIC_COLUMN_ROUTING.get(code)
        if column is None or column not in aspect_by_id:
            unrouted.append((fact_id, code, "no_column_declared_for_metric_code"))
            continue
        fact_columns.append((fact_id, code, column))

    #: 缺口账**只**对写正文的那个 topic 做减法（`fpr-2`）：其余 topic 的栏目覆盖由它们各自的
    #: 确定性呈现产物逐条回答，在这里重数一遍会给出一个更弱、且会数错的读数。
    gap_columns = routed_columns
    gaps = tuple(
        PresentationColumnGap(
            column=aspect_id,
            contract_requirement_text=str(
                getattr(aspect_by_id[aspect_id], "requirement_text", "") or ""),
            contract_display_tier=_tier(aspect_id),
            reason="no_registered_metric_in_selected_facts")
        for aspect_id in order if aspect_id not in gap_columns)

    return FinancialPresentationRouting(
        routing_version=FINANCIAL_PRESENTATION_ROUTING_VERSION,
        source=PRESENTATION_LAYER_SOURCE,
        contract_topic=topic_id,
        route_topics=tuple(route_topics),
        routes=routes,
        fact_columns=tuple(fact_columns),
        unrouted_facts=tuple(unrouted),
        column_gaps=gaps)

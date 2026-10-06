"""§0.19/§0.20：财务节的**确定性**「指标 × 期间」表格（`cmt-4` / `cmtr-4`）。

## 为什么需要它

旧链的「指标 × 期间」表由 `narrative_schema.build_metric_period_tables` 构造，它的**候选集
是定稿 `Claim`**：一条 `Claim` 恰好被**一条** `financial_pack` 事实支撑，才成为一行。§0.20
把写作主链换成「Pack 驱动 + 引用」之后，这一节**不再产生 `Claim`** —— 于是那张表就再也构造
不出来了，财务节在读者面上只剩若干句直述事实的句子。

本模块把同一套规则**原样搬到新链的输入面**上，而不是另立第二套财务口径：

| 规则（来源：`build_metric_period_tables` 的 1–6 条） | 本模块的做法 |
|---|---|
| 候选 = 恰好被一条 `financial_pack` 事实支撑的 `Claim` | 候选 = **进入本节事实清单的合格 `financial_pack` 事实**（见下） |
| `FINANCIAL_TABLE_MIN_PERIODS` 条期间才成行 | **同一个**常量 |
| 双期派生事实（`input_periods` 长度 2）不是行 | **同一个** `is_derived_two_period_fact` |
| 单元格 = `financial_table_cell` | **同一个**函数 |
| 列名 = `financial_period_text`，顺序取 artifact 自己声明的 `periods` | **同一套**，顺序不自创 |
| 表按 `(unit, period_basis)` 成组、表题 = `financial_table_caption` | **同一套** |
| 分量（数值 / 期间表达 / 完整口径限定语）必须由 Claim 文本逐字承担 | 必须**逐字取自权威事实自己的字段**，并当格核验（见下） |
| 同一格两条断言 / 跨期 label·unit 不一致 / 口径未声明 → fail-closed | **同样** fail-closed |

### `cmtr-1` → `cmtr-2`：候选集不再由**写作结果**决定

`cmtr-1` 的候选集是「**被本节正文句引用**的财务事实」。那条规则把「表里有没有这一行」交给
了写作侧的一次生成结果：同一份权威事实，换一次写作就换一张表；某个必需指标只要没被任何一句
正文提到，它就从读者面上消失，且消失的理由不落在表上。本轮裁决改为：

* **行有没有** = 该指标是否**合格权威事实**（进入本节事实清单、期间口径已声明、非双期派生、
  有 code/期间/渲染值）× 是否达到 `FINANCIAL_TABLE_MIN_PERIODS` 个期间 * 列轴是否由 artifact
  自己声明的期间顺序组成。写作侧引用与否**不**再参与这条判据。
* **每一格**仍当格核验，但核对基准从「引用这条事实的正文句」换成**权威事实自己的字段**
  （见 `_checked_cell`：数值、期间表达对列、量纲对行、代理限定语确实渲染进格、引用键非空）。
  这与「逐句核对」是两根轴：正文句归 `sentence_check`，表格格归这里。
* 表格**不是**第二条投递路径：它的每一个非空格都带 `citation_key`（本清单的事实轴键）与
  `fact_id`，逐格可回查；它只陈列**本节清单内**的权威事实，本章节看不到的事实不进表。

### `cmtr-2` → `cmtr-3`：展示层级（`display_tier`）真的参与成表

`cmtr-2` 的表把**所有**合格指标行并进同一张正文表，其中包括**代理口径**的行。冻结 Contract
把这类内容写成 `display_tier: diagnostic_only` / `content_role: audit_only`（例如
`fin_solvency.interest_expense_proxy`，逐字要求「利息费用代理（不可靠，仅进入诊断槽位）」），
`FORMULA_REVIEW.md` §1 也写明代理结果「不参与『精确口径』比较」。**「格子上带了限定语」不是
入场券**：一条只由代理口径输入算出来的指标，不得因为自己带了 `PROXY_FINANCE_EXPENSES` 标记
就进入正文指标表。

`cmtr-3` 因此按行分层：

* **`required_body`** —— 精确口径的指标行，进**正文指标表**；
* **`diagnostic_only`** —— 该行**至少一格**来自代理口径事实（`NS.is_proxy_fact`），整行改入
  **诊断槽位**。判定读的是**事实自己的标记**（权威写的 `reason_code`），不是指标名、不是
  公司名、不是任何关键词表：`PROXY_FINANCE_EXPENSES` 这条代数关系由权威自己声明。

分层只决定**这张表放在读者面的哪一块**，不改任何一格的值、期间、单位、引用键——那四样仍逐字
取权威字段并按 `_checked_cell` 当格核验。代理限定语**照旧渲染进格**（`cmtr-2` 的判据一条都
没撤）：读者在诊断槽位里看到的仍是带 `代理口径（PROXY_FINANCE_EXPENSES）` 的原值，不是被
抹平的裸数字。

### `cmtr-3` → `cmtr-4`：分层还要与**呈现层路由**指向的那一栏对上

`cmtr-3` 的分层只读**权威自己的代理标记**：有代理值就进诊断槽位。这条判据单独成立，却答不出
另一半问题——冻结 Contract 对**那一栏**要求的是正文还是仅诊断。两者恰好一致时读起来没问题，
不一致时：

* 权威没打代理标记、而 Contract 把这一栏写成 `diagnostic_only` ⇒ 这条值进了正文指标表，
  而读者面没有任何东西说明它本该只在诊断槽位里；
* 权威打了代理标记、而 Contract 把这一栏写成 `required_body` ⇒ 这条值被藏进诊断槽位，
  而 Contract 要求它出现在正文里。

`cmtr-4` 因此把呈现层的路由声明（`sections.financial_presentation_routing`）接进来：每个指标
code 经声明落到冻结 Contract 的哪一栏、那一栏的 `display_tier` 是什么，与权威自己的代理标记
**逐行对上**；对不上就在构造期停住，不替任何一方作决定。行上因此多一个
`presentation_column`（这个指标按声明属于哪一栏），表上多一个 `display_tier_basis`（这张表
凭什么落在这一档）。

这份声明是**呈现层**的，不是事实权威：它**不**写进任何事实的 `aspect_ids`（那一支恒为空），
也不证明那一栏的 Contract 要求已被满足。冻结 Contract 里没有任何指标落进去的栏（例如本次
`fin_solvency.net_asset_level` 与 `rigid_debt_structure`）只留下**栏目缺口**，本模块不拿别的
栏目的指标去顶。

### 必需指标的覆盖是**独立对账**，不是替身草稿的自报

`CitedMetricTableOutcome.coverage` 逐条列出「本节必需事实 × 期间」的去向：`displayed`
（进了某张表的某一格）／`available_not_displayed`（合格但没成行，带类型化原因）／
`missing`（权威自己记的必需指标缺口，带 `reason_code`）。三档都不读小说草稿，因此
「必需缺口 0」这句话不再由替身自报。

## 不重算：表格里不出现任何 Python 算出来的数

金额格式化、期间表达、代理口径限定语一律**逐字取权威自己的字段**（`display` / `value_text` /
`period_label` / `proxy_qualifier`）。本模块**没有**任何算术：不求和、不换算单位、不推比率、
不给缺格补值。缺格渲染为 `FINANCIAL_TABLE_EMPTY_CELL`（空串），绝不回填。

## 拒绝是**一等产出**，不是静默空表

「这一节没有可成表的指标」和「算表格的时候撞上了不一致」是两件不同的事：

* 前者是**合法缺席**（权威不是财务、权威没有事实、没有 ≥2 期的同指标、artifact 没声明期间
  顺序）→ 产出一条 `CitedMetricTableRefusal`（typed，可记录、可显示）；
* 后者是**数据/规则伤**（期间不在 artifact 声明内、同格两条断言、跨期 label 不一致、口径
  未声明或不在封闭取值内、某格分量没被任何引用句承担）→ 抛 `CitedMetricTableError`。

把后者也写成「空表 + 一条温和的说明」，正是本仓明令禁止的 fail-open。

## 本批的边界（未做、且不得被读成已做）

这张表**已经**按本轮裁决并入 `CitedReportVersion` 的身份体（`crpv-2` 的
`metric_table_ids` / `metric_tables_fingerprint`）：表内容一变，预览版本就变。**并入身份
不等于**表本身获得了发布资格——`CitedReportVersion.publishability` 仍是恒定
`not_publishable`，四个状态轴照旧分列。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from financial_v2 import period_basis as _FPB
from sections import cited_writer as CW
from sections import narrative_schema as NS

__all__ = [
    "CITED_METRIC_TABLE_SCHEMA_VERSION",
    "CITED_METRIC_TABLE_RULE_VERSION",
    "CITED_METRIC_TABLE_REFUSAL_KINDS",
    "CITED_METRIC_TABLE_EMPTY_CELL",
    "CITED_METRIC_COVERAGE_STATES",
    "CITED_METRIC_DISPLAY_TIERS",
    "CITED_METRIC_TIER_BASES",
    "CITED_METRIC_DIAGNOSTIC_CAPTION_SUFFIX",
    "CITED_METRIC_NOT_DISPLAYED_REASONS",
    "CitedMetricTableError",
    "CitedMetricTableRefusal",
    "CitedMetricCoverageRow",
    "CitedMetricTableRow",
    "CitedMetricTable",
    "CitedMetricTableOutcome",
    "build_cited_metric_tables",
    "render_cited_metric_table_markdown",
    "render_cited_metric_coverage_markdown",
]

#: 表格 wire 版本。字段增删即升版。
#:
#: `cmt-4`：行增加 `presentation_column`（该指标经**呈现层**路由声明落在哪一栏）、表增加
#: `display_tier_basis`（这一张表**凭什么**落在这一档）。字段增删即升版；升版会换掉全部表格 id，
#: 这是**刻意**的：同一份事实在新旧 wire 下不是同一张表。
#: （`cmt-3` 曾增加 `display_tier`；`cmt-2` 曾增加 `CitedMetricTableOutcome.schema_version`
#: 与 `coverage`。）
CITED_METRIC_TABLE_SCHEMA_VERSION = "cmt-4"
#: 规则版本。上表里任何一条判据变化即升版（它进表格身份体，因此升版会换掉全部表格 id）。
#: `cmtr-4`：展示层级不再只由「权威自己的代理标记」判——它还要与**呈现层路由声明**所指向那一栏
#: 在冻结 Contract 里声明的 `display_tier` **对上**；对不上就在构造期停住（见 `_row_display_tier`）。
#: `cmtr-3` 曾只按权威的代理标记分层。
CITED_METRIC_TABLE_RULE_VERSION = "cmtr-4"

#: 展示层级的封闭取值（与冻结 Contract 的 `DISPLAY_TIERS` 同形，但**本模块只认两档**：
#: 落在本节的合格事实要么进正文表、要么只进诊断槽位；`optional_body` 之类的中间档不在本表
#: 的判据面内，遇到就按正文档处理——它是**更宽**而不是更窄的一档，不会把内容误升格）。
CITED_METRIC_DISPLAY_TIERS = ("required_body", "diagnostic_only")

#: 「凭什么把这一张表放在这一档」——封闭的**判据名**集合，不是判据的结论。诊断档必须能逐条说清
#: 是哪一条判据把它放进去的；`required_body` 档不带任何判据名（没有一条判据把它往诊断档推）。
CITED_METRIC_TIER_BASES = (
    #: 该行指标经**呈现层**路由声明落到的那一栏，在冻结 Contract 里声明 `diagnostic_only`。
    "contract_display_tier",
    #: 该行**至少一格**来自代理口径（权威自己写的 `reason_code`）。
    "authority_proxy_fact_marker",
)

#: 冻结 Contract 的 `display_tier` 里本表认得的取值。冻结取值出现第三种时在构造期停住：
#: 「本模块不认得这一档」与「这一档不是诊断档」是两件事，按默认值往前走会把新档静默读成正文档。
_CONTRACT_DISPLAY_TIERS = ("required_body", "diagnostic_only")

#: 诊断槽位表题的后缀。表题是读者眼睛先落的那一行，两档表如果表题逐字相同，读者只能靠小字
#: 区分——这正是 `financial_table_caption` 要解决的那类同一性缺陷，不能在分层之后重新引入。
CITED_METRIC_DIAGNOSTIC_CAPTION_SUFFIX = (
    "诊断槽位（`display_tier=diagnostic_only`；**不并入正文指标表**）")

#: 合法缺席的封闭原因集。**只**放「这一节本来就没有可成表的指标」这一类的四种情形；
#: 数据/规则伤不在这个集合里，它们一律抛错（见模块开头）。
CITED_METRIC_TABLE_REFUSAL_KINDS = (
    #: 本权威不是财务权威（`producer_kind != "financial_workflow"`）：不得据此构造指标表。
    "authority_not_financial",
    #: 财务权威一份事实都没有：没有可陈列的单元格。
    "no_authority_facts",
    #: 本权威有事实，但**没有一条**同时满足「在本节事实清单内」且「同一指标
    #: ≥ `FINANCIAL_TABLE_MIN_PERIODS` 个期间」。`cmtr-1` 下这条写成
    #: `no_cited_two_period_metric`（判据里含着写作侧的引用结果），`cmtr-2` 起引用不再参与，
    #: 故随判据一起改名——留在旧名上会让读的人以为「没被正文引用」仍是不成表的原因之一。
    "no_two_period_metric",
    #: 权威 artifact 没有声明任何期间顺序：列轴不得自创（与旧表同一条 fail-closed 规则）。
    "no_declared_period_order",
)

#: 缺格。逐字取自权威表的同一常量：**空**，而不是「—」或「不适用」这类会被读成结论的记号。
CITED_METRIC_TABLE_EMPTY_CELL = NS.FINANCIAL_TABLE_EMPTY_CELL

#: 一条「本节必需事实 × 期间」在**本轮产出**里的去向（封闭三档）。
#:
#: * `displayed`：它落在某张已构造表格的某一格里，读者能直接读到（带表 id 与期间列名）。
#: * `available_not_displayed`：事实本身合格、也在本节清单内，但没成行；必须带
#:   `CITED_METRIC_NOT_DISPLAYED_REASONS` 里的一条原因。
#: * `missing`：权威**自己**记下的必需指标缺口（`artifact.gaps`），必须带权威的
#:   `reason_code`。这一档的判据不在本模块，本模块只逐字搬运。
CITED_METRIC_COVERAGE_STATES = ("displayed", "available_not_displayed", "missing")

#: 「合格但没成行」的封闭原因集。每一档都是构造期**真实**会让一条事实落到表外的那条判据，
#: 且**互不重叠**（按构造顺序取第一个命中的）。刻意不放两档：
#:
#: * 「期间不在 artifact 声明的顺序里」不是本档——它在构造期**抛错**（`period_not_declared_
#:   by_artifact`），不是一条安静的去向；
#: * 「同 `(unit, period_basis)` 分组没成表」不单独成档——分组凑够期间就成表，凑不够就是
#:   `below_min_periods`，两档指的是同一件事，并列会让同一条事实有两种说法。
CITED_METRIC_NOT_DISPLAYED_REASONS = (
    #: 同一指标在本节清单里只凑到不足 `FINANCIAL_TABLE_MIN_PERIODS` 个期间：单期指标不成行
    #: （它本来就该写在正文里）。
    "below_min_periods",
    #: 该事实是双期派生事实：它的 `period` 是复合记号，任何单一列名都在说一件不成立的事。
    "derived_two_period_fact",
    #: 缺 code / 期间 / 渲染值中的任意一项 ⇒ 没有可陈列的单元格。
    "no_displayable_cell",
    #: artifact 没有声明任何期间顺序 ⇒ 整节退回 `no_declared_period_order`，列轴不得自创。
    "period_axis_not_declared",
    #: 事实合格、也属本节主题，但没有出现在本次事实清单里（原因在**清单构造**侧，
    #: 不在本模块的成表判据里——它与上面四条分开记，读的人才知道该去查哪一层）。
    "not_in_input_manifest",
)


class CitedMetricTableError(Exception):
    """财务指标表的 fail-closed（结构性伤，不是合法缺席）。"""

    def __init__(self, message: str, *, reason: str = "", table_key: Any = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.table_key = table_key


def _reject_unknown(d: Any, allowed: set[str], what: str) -> dict:
    """未登记字段一律拒（wire 面不留自由扩展位）。

    实现在 `NS._reject_unknown`（全仓唯一一份「未知字段」判据），这里只把错误类型收敛成
    本模块的 `CitedMetricTableError` —— 调用方不该为了读一张财务表去 catch 叙事层的异常。
    """
    try:
        return NS._reject_unknown(d, allowed, what)
    except NS.NarrativeSchemaError as exc:
        raise CitedMetricTableError(str(exc), reason="unknown_field") from exc


@dataclass(frozen=True)
class CitedMetricTableRefusal:
    """一次**合法缺席**的 typed 记录：为什么这一节没有指标表。"""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        if self.reason not in CITED_METRIC_TABLE_REFUSAL_KINDS:
            raise CitedMetricTableError(
                f"表格拒绝原因 {self.reason!r} 不在封闭取值 "
                f"{list(CITED_METRIC_TABLE_REFUSAL_KINDS)} 内（拒绝原因不得自造）")
        if not str(self.detail or "").strip():
            raise CitedMetricTableError("表格拒绝必须带 detail（理由码本身不足以定位）")
        object.__setattr__(self, "detail", str(self.detail).strip())

    def to_dict(self) -> dict:
        return {"reason": self.reason, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Any) -> "CitedMetricTableRefusal":
        d = _reject_unknown(d, {"reason", "detail"}, "CitedMetricTableRefusal")
        return cls(reason=str(d.get("reason") or ""), detail=str(d.get("detail") or ""))


@dataclass(frozen=True)
class CitedMetricCoverageRow:
    """一条「本节必需事实（指标 × 期间）」在**本轮产出**里的去向读数。

    它回答的是「未展示的必需指标去哪了」，因此每一行都必须**恰好**说清三件事：是哪个指标
    的哪一期、它落在哪一档、以及那一档的原因。`missing` 档的原因来自权威自己的
    `reason_code`（本模块只搬运），另外两档的原因必须来自本模块的封闭词表。

    `fact_id` 在 `missing` 档是**权威记的那个坐标**（`financial_worker` 的
    `metric_{formula}_{period}`），因此两档用的是同一套命名，读的人可以逐条对齐。
    """

    state: str
    fact_id: str
    code: str
    label: str
    period: str
    reason: str
    table_id: str = ""
    column_text: str = ""

    def __post_init__(self) -> None:
        if self.state not in CITED_METRIC_COVERAGE_STATES:
            raise CitedMetricTableError(
                f"覆盖读数的去向 {self.state!r} 不在封闭取值 "
                f"{list(CITED_METRIC_COVERAGE_STATES)} 内")
        for name in ("fact_id", "code", "label", "period", "reason"):
            if not str(getattr(self, name) or "").strip():
                raise CitedMetricTableError(
                    f"覆盖读数缺 {name}：一条「必需指标去哪了」的记录必须能逐条回查")
        if self.state == "displayed":
            if self.reason != "displayed":
                raise CitedMetricTableError(
                    "覆盖读数 state='displayed' 时 reason 必须逐字为 'displayed'")
            if not str(self.table_id or "").strip():
                raise CitedMetricTableError(
                    "覆盖读数 state='displayed' 必须带表 id：读者要能翻到那一格")
        else:
            if self.state == "missing":
                # 权威的 reason_code 不在本模块词表里——本模块**不**替它归一化。
                if self.table_id or self.column_text:
                    raise CitedMetricTableError(
                        "覆盖读数 state='missing' 不得带表 id / 列名：权威缺口没有读者面那一格")
            elif self.reason not in CITED_METRIC_NOT_DISPLAYED_REASONS:
                raise CitedMetricTableError(
                    f"「合格但没成行」的原因 {self.reason!r} 不在封闭词表 "
                    f"{list(CITED_METRIC_NOT_DISPLAYED_REASONS)} 内")

    def to_dict(self) -> dict:
        return {"state": self.state, "fact_id": self.fact_id, "code": self.code,
                "label": self.label, "period": self.period, "reason": self.reason,
                "table_id": self.table_id, "column_text": self.column_text}

    @classmethod
    def from_dict(cls, d: Any) -> "CitedMetricCoverageRow":
        d = _reject_unknown(
            d, {"state", "fact_id", "code", "label", "period", "reason", "table_id",
                "column_text"}, "CitedMetricCoverageRow")
        return cls(state=str(d.get("state") or ""), fact_id=str(d.get("fact_id") or ""),
                   code=str(d.get("code") or ""), label=str(d.get("label") or ""),
                   period=str(d.get("period") or ""), reason=str(d.get("reason") or ""),
                   table_id=str(d.get("table_id") or ""),
                   column_text=str(d.get("column_text") or ""))


@dataclass(frozen=True)
class CitedMetricTableRow:
    """一行指标：`cells` 与 `header[1:]` 逐列对齐，空格是**留空**不是推算。

    `fact_ids` / `citation_keys` / `period_texts` 只记**非空**那些格（与旧表的
    `claim_ids` / `row_periods` 同一口径）：留空的格没有事实、没有引用、也没有期间表达。

    `presentation_column`（`cmt-4`）是这一行指标经**呈现层**路由声明落到的那一栏。它是
    **呈现层**的读数，不是权威对这条数字的认领：同一张表里两行的 `presentation_column`
    可以不同（正文表的行按单位与口径成组，不按栏目成组），而权威事实自己的 `aspect_ids`
    照旧为空。空串表示本次运行**没有**给这一节装呈现层路由声明（那一节不做这条核对）。
    """

    label: str
    unit: str
    cells: tuple[str, ...]
    fact_ids: tuple[str, ...]
    citation_keys: tuple[str, ...]
    period_texts: tuple[str, ...]
    presentation_column: str = ""

    def __post_init__(self) -> None:
        if not str(self.label or "").strip():
            raise CitedMetricTableError("表格行必须有指标名（`label`）：不得由渲染层推定")
        if not str(self.unit or "").strip():
            raise CitedMetricTableError("表格行必须有单位（`unit`）：含数字的行缺量纲无法阅读")
        object.__setattr__(self, "cells", tuple(str(c) for c in (self.cells or ())))
        for name in ("fact_ids", "citation_keys", "period_texts"):
            object.__setattr__(self, name, tuple(str(x) for x in (getattr(self, name) or ())))
        #: 一格一个事实：非空格数必须等于事实坐标数。少了 → 有格没记归属；多了 → 有一格记了
        #: 两条断言（后者在分组期已被拒，这里是纵深防线）。
        non_empty = sum(1 for c in self.cells if c)
        if non_empty != len(self.fact_ids):
            raise CitedMetricTableError(
                f"指标 {self.label!r} 的非空格 {non_empty} 个，事实坐标 {len(self.fact_ids)} 个："
                "每一格必须恰好归属一条权威事实", reason="row_cell_fact_count_mismatch")
        if len(self.citation_keys) != len(self.fact_ids):
            raise CitedMetricTableError(
                f"指标 {self.label!r} 的事实坐标与引用键数量不一致", reason="row_key_count_mismatch")
        if len(self.period_texts) != len(self.fact_ids):
            raise CitedMetricTableError(
                f"指标 {self.label!r} 的期间表达与事实坐标数量不一致",
                reason="row_period_count_mismatch")

    def to_dict(self) -> dict:
        return {"label": self.label, "unit": self.unit, "cells": list(self.cells),
                "fact_ids": list(self.fact_ids), "citation_keys": list(self.citation_keys),
                "period_texts": list(self.period_texts),
                "presentation_column": self.presentation_column}

    def identity_body(self) -> dict:
        return self.to_dict()

    @classmethod
    def from_dict(cls, d: Any) -> "CitedMetricTableRow":
        d = _reject_unknown(
            d, {"label", "unit", "cells", "fact_ids", "citation_keys", "period_texts",
                "presentation_column"},
            "CitedMetricTableRow")
        return cls(label=str(d.get("label") or ""), unit=str(d.get("unit") or ""),
                   cells=tuple(str(x) for x in (d.get("cells") or ())),
                   fact_ids=tuple(str(x) for x in (d.get("fact_ids") or ())),
                   citation_keys=tuple(str(x) for x in (d.get("citation_keys") or ())),
                   period_texts=tuple(str(x) for x in (d.get("period_texts") or ())),
                   presentation_column=str(d.get("presentation_column") or ""))


@dataclass(frozen=True)
class CitedMetricTable:
    """一张展示表：表题 + 列轴 + 行。**不携带任何模型产物**，全部逐字取权威字段。"""

    table_id: str
    schema_version: str
    rule_version: str
    section_id: str
    caption: str
    header: tuple[str, ...]
    entity_scope: str
    unit: str
    period_basis: str
    rows: tuple[CitedMetricTableRow, ...]
    display_tier: str = "required_body"
    """这一张表在读者面上属于哪一块：`required_body`（正文指标表）或 `diagnostic_only`
    （诊断槽位）。分层判据见模块头 `cmtr-3 → cmtr-4`：它由事实自己的代理标记**与**呈现层
    路由所指向那一栏在冻结 Contract 里的展示层级共同判定，两条判据必须一致。"""
    display_tier_basis: tuple[str, ...] = ()
    """这一张表**凭什么**落在 `display_tier` 这一档（`CITED_METRIC_TIER_BASES` 的子集）。

    诊断档必须能说清是哪一条判据把它放进去的，否则「为什么这个数只在诊断槽位里」在产物上
    无从回答；`required_body` 档一律为空元组——没有判据把它往诊断档推。"""

    def __post_init__(self) -> None:
        if self.schema_version != CITED_METRIC_TABLE_SCHEMA_VERSION:
            raise CitedMetricTableError(
                f"CitedMetricTable.schema_version 必须为 {CITED_METRIC_TABLE_SCHEMA_VERSION!r}")
        if self.rule_version != CITED_METRIC_TABLE_RULE_VERSION:
            raise CitedMetricTableError(
                f"CitedMetricTable.rule_version 必须为 {CITED_METRIC_TABLE_RULE_VERSION!r}")
        if self.display_tier not in CITED_METRIC_DISPLAY_TIERS:
            raise CitedMetricTableError(
                f"未登记的展示层级：{self.display_tier!r}（封闭取值 "
                f"{list(CITED_METRIC_DISPLAY_TIERS)}）")
        bases = tuple(str(b) for b in (self.display_tier_basis or ()))
        for name in bases:
            if name not in CITED_METRIC_TIER_BASES:
                raise CitedMetricTableError(
                    f"未登记的分层判据名 {name!r}（封闭取值 {list(CITED_METRIC_TIER_BASES)}）",
                    reason="tier_basis_unknown")
        if len(set(bases)) != len(bases):
            raise CitedMetricTableError(
                f"分层判据名有重复：{list(bases)}", reason="tier_basis_duplicate")
        if self.display_tier == "diagnostic_only" and not bases:
            raise CitedMetricTableError(
                "诊断档的表必须带至少一条分层判据名：否则「为什么这个数不在正文指标表里」"
                "在产物上无从回答", reason="tier_basis_missing")
        if self.display_tier == "required_body" and bases:
            raise CitedMetricTableError(
                f"正文指标表不得带分层判据名（实得 {list(bases)}）：带上它意味着有一条判据"
                "把这张表往诊断档推，那张表就不该在正文档", reason="tier_basis_unexpected")
        object.__setattr__(self, "display_tier_basis", bases)
        for name in ("table_id", "section_id", "caption", "entity_scope", "unit"):
            if not str(getattr(self, name) or "").strip():
                raise CitedMetricTableError(f"CitedMetricTable.{name} 必须非空")
        header = tuple(str(x) for x in (self.header or ()))
        rows = tuple(self.rows or ())
        if not rows:
            raise CitedMetricTableError("表格至少要有一行：空表不是表格，是拒绝")
        if len(header) < 2:
            raise CitedMetricTableError(
                "表格列轴至少要有一列期间（第一列是行标签）：只有标签列不成表")
        for row in rows:
            if len(row.cells) != len(header) - 1:
                raise CitedMetricTableError(
                    f"指标 {row.label!r} 的格数 {len(row.cells)} 与期间列数 {len(header) - 1} "
                    "不一致：行与列轴必须逐列对齐", reason="row_column_mismatch")
        object.__setattr__(self, "header", header)
        object.__setattr__(self, "rows", rows)
        if self.table_id != self._derive_id():
            raise CitedMetricTableError(
                f"CitedMetricTable.table_id 与内容不符：声明 {self.table_id!r}，"
                f"应为 {self._derive_id()!r}", reason="table_id_mismatch")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version, "rule_version": self.rule_version,
            "section_id": self.section_id, "caption": self.caption,
            "header": list(self.header), "entity_scope": self.entity_scope,
            "unit": self.unit, "period_basis": self.period_basis,
            "display_tier": self.display_tier,
            "display_tier_basis": list(self.display_tier_basis),
            "rows": [row.identity_body() for row in self.rows],
        }

    def _derive_id(self) -> str:
        return NS.content_id("cmt_", self.identity_body())

    def fingerprint(self) -> str:
        return NS.content_id("cmtf_", self.identity_body())

    def to_dict(self) -> dict:
        return {**self.identity_body(), "table_id": self.table_id,
                "rows": [row.to_dict() for row in self.rows]}

    @classmethod
    def create(cls, *, section_id: str, caption: str, header: tuple[str, ...],
               entity_scope: str, unit: str, period_basis: str,
               rows: tuple[CitedMetricTableRow, ...],
               display_tier: str = "required_body",
               display_tier_basis: tuple[str, ...] = ()) -> "CitedMetricTable":
        basis = tuple(str(b) for b in (display_tier_basis or ()))
        body = {"schema_version": CITED_METRIC_TABLE_SCHEMA_VERSION,
                "rule_version": CITED_METRIC_TABLE_RULE_VERSION, "section_id": section_id,
                "caption": caption, "header": list(header), "entity_scope": entity_scope,
                "unit": unit, "period_basis": period_basis,
                "display_tier": display_tier, "display_tier_basis": list(basis),
                "rows": [row.identity_body() for row in rows]}
        return cls(table_id=NS.content_id("cmt_", body),
                   schema_version=CITED_METRIC_TABLE_SCHEMA_VERSION,
                   rule_version=CITED_METRIC_TABLE_RULE_VERSION, section_id=section_id,
                   caption=caption, header=header, entity_scope=entity_scope, unit=unit,
                   period_basis=period_basis, rows=rows, display_tier=display_tier,
                   display_tier_basis=basis)

    @classmethod
    def from_dict(cls, d: Any) -> "CitedMetricTable":
        d = _reject_unknown(
            d, {"table_id", "schema_version", "rule_version", "section_id", "caption",
                "header", "entity_scope", "unit", "period_basis", "rows", "display_tier",
                "display_tier_basis"},
            "CitedMetricTable")
        return cls(table_id=str(d.get("table_id") or ""),
                   schema_version=str(d.get("schema_version") or ""),
                   rule_version=str(d.get("rule_version") or ""),
                   section_id=str(d.get("section_id") or ""),
                   caption=str(d.get("caption") or ""),
                   header=tuple(str(x) for x in (d.get("header") or ())),
                   entity_scope=str(d.get("entity_scope") or ""),
                   unit=str(d.get("unit") or ""),
                   period_basis=str(d.get("period_basis") or ""),
                   rows=tuple(CitedMetricTableRow.from_dict(r)
                              for r in (d.get("rows") or ())),
                   display_tier=str(d.get("display_tier") or "required_body"),
                   display_tier_basis=tuple(str(x) for x in (d.get("display_tier_basis") or ())))


@dataclass(frozen=True)
class CitedMetricTableOutcome:
    """一次构造的**完整**产出：表格 + 覆盖读数 + 合法缺席的 typed 记录。

    「表格与拒绝不得同时存在」这条不变；覆盖读数与两者都**可以**并存——它是独立的一根轴
    （「本节必需事实逐条去哪了」），不是第三种表状态。
    """

    section_id: str
    schema_version: str
    rule_version: str
    authority_kind: str
    tables: tuple[CitedMetricTable, ...] = ()
    refusals: tuple[CitedMetricTableRefusal, ...] = ()
    coverage: tuple[CitedMetricCoverageRow, ...] = ()

    def __post_init__(self) -> None:
        tables = tuple(self.tables or ())
        refusals = tuple(self.refusals or ())
        coverage = tuple(self.coverage or ())
        if self.schema_version != CITED_METRIC_TABLE_SCHEMA_VERSION:
            raise CitedMetricTableError(
                f"CitedMetricTableOutcome.schema_version 必须为 "
                f"{CITED_METRIC_TABLE_SCHEMA_VERSION!r}，得到 {self.schema_version!r}")
        if self.rule_version != CITED_METRIC_TABLE_RULE_VERSION:
            raise CitedMetricTableError(
                f"CitedMetricTableOutcome.rule_version 必须为 "
                f"{CITED_METRIC_TABLE_RULE_VERSION!r}")
        if tables and refusals:
            raise CitedMetricTableError(
                "一次构造不得同时产出表格与拒绝：拒绝的语义是「这一节没有表」，"
                "两者并列会让读者不知道哪一条才算数", reason="table_and_refusal")
        for row in coverage:
            if not isinstance(row, CitedMetricCoverageRow):
                raise CitedMetricTableError(
                    f"覆盖读数只能是 CitedMetricCoverageRow，得到 {type(row).__name__}")
        keys = [(r.code, r.period, r.state) for r in coverage]
        if len(set(keys)) != len(keys):
            raise CitedMetricTableError(
                "覆盖读数在同一 (指标, 期间, 去向) 上有重复行：汇总读数不得重复计数",
                reason="coverage_row_duplicate")
        #: 「已展示」的读数必须真的落在本次某张表里——否则它就是一句自报。
        shown = {t.table_id for t in tables}
        for row in coverage:
            if row.state == "displayed" and row.table_id not in shown:
                raise CitedMetricTableError(
                    f"覆盖读数声称 {row.code}@{row.period} 已在表 {row.table_id!r} 中展示，"
                    "但本次产出里没有这张表", reason="coverage_displayed_table_missing")
        object.__setattr__(self, "tables", tables)
        object.__setattr__(self, "refusals", refusals)
        object.__setattr__(self, "coverage", coverage)

    def coverage_counts(self) -> tuple[tuple[str, int], ...]:
        """按去向汇总的条数（只对账用；不产生任何「覆盖通过」结论）。"""
        counts: dict[str, int] = {}
        for row in self.coverage:
            counts[row.state] = counts.get(row.state, 0) + 1
        return tuple(sorted(counts.items()))

    def to_dict(self) -> dict:
        return {"section_id": self.section_id, "schema_version": self.schema_version,
                "rule_version": self.rule_version, "authority_kind": self.authority_kind,
                "tables": [t.to_dict() for t in self.tables],
                "refusals": [r.to_dict() for r in self.refusals],
                "coverage": [r.to_dict() for r in self.coverage],
                "coverage_counts": dict(self.coverage_counts())}

    @classmethod
    def from_dict(cls, d: Any) -> "CitedMetricTableOutcome":
        d = _reject_unknown(
            d, {"section_id", "schema_version", "rule_version", "authority_kind",
                "tables", "refusals", "coverage", "coverage_counts"},
            "CitedMetricTableOutcome")
        return cls(section_id=str(d.get("section_id") or ""),
                   schema_version=str(d.get("schema_version") or ""),
                   rule_version=str(d.get("rule_version") or ""),
                   authority_kind=str(d.get("authority_kind") or ""),
                   tables=tuple(CitedMetricTable.from_dict(t) for t in (d.get("tables") or ())),
                   refusals=tuple(CitedMetricTableRefusal.from_dict(r)
                                  for r in (d.get("refusals") or ())),
                   coverage=tuple(CitedMetricCoverageRow.from_dict(r)
                                  for r in (d.get("coverage") or ())))


# ---------------------------------------------------------------------------
# 构造
# ---------------------------------------------------------------------------

def _refuse(section_id: str, authority_kind: str, reason: str, detail: str,
            coverage: tuple[CitedMetricCoverageRow, ...] = ()) -> CitedMetricTableOutcome:
    """合法缺席。拒绝**不吞掉**覆盖读数：退回无表时，必需指标逐条的去向照旧列出来。"""
    return CitedMetricTableOutcome(
        section_id=section_id, schema_version=CITED_METRIC_TABLE_SCHEMA_VERSION,
        rule_version=CITED_METRIC_TABLE_RULE_VERSION,
        authority_kind=authority_kind,
        refusals=(CitedMetricTableRefusal(reason=reason, detail=detail),),
        coverage=coverage)


def _required_coords(authority: Any, fin: dict[tuple[str, str, str], Any]
                     ) -> frozenset[tuple[str, str, str]]:
    """本节**必需**的财务事实坐标（`required_fact_ids` 的唯一实现在叙事层，这里只做坐标化）。

    `required_fact_ids` 给出的是 `fact_id` 集合；表格一行一格都按 `(kind, container, fact_id)`
    坐标记账，因此这里把它映射回坐标。`artifact.facts` 里同名 fact_id 不重复，映射是单值的。
    """
    required_ids = NS.required_fact_ids(authority)
    return frozenset(coord for coord, fact in fin.items()
                     if str(getattr(fact, "fact_id", "") or "") in required_ids)


def _coverage_rows(*, authority: Any, required: frozenset[tuple[str, str, str]],
                   fin: dict[tuple[str, str, str], Any],
                   disposition: dict[tuple[str, str, str], str],
                   displayed: dict[tuple[str, str, str], tuple[str, str]]
                   ) -> tuple[CitedMetricCoverageRow, ...]:
    """本节必需事实 × 期间的逐条去向（`displayed` / `available_not_displayed` / `missing`）。

    **不是**第二套构造：`disposition` 与 `displayed` 都是构造期顺手记下的真实结果，本函数只把
    它们与权威自己的缺口拼成可读的行。因此覆盖读数与表格永远同源——表格变了，读数跟着变。

    三档的来源各只有一处，读者可以逐档回查：
    * `missing` ← 权威自己的 `artifact.gaps`（`financial_worker` 只把 **required** 公式的缺失
      写进这一列；本模块只按**本节主题归属**过滤，用的是与 `scan_financial` 同一个权威方法）；
    * `displayed` ← 构造期真的把这条事实写进了某张表的某一格；
    * `available_not_displayed` ← 构造期真的把它挡在表外的那一条判据（`disposition`）。
    """
    rows: list[CitedMetricCoverageRow] = []
    own_topics = {str(topic_id) for topic_id in getattr(authority, "topic_ids", ())}

    # 第一档：权威自己记下的必需缺口。理由码逐字搬运，本模块不替它归一化。
    for gap in (getattr(getattr(authority, "artifact", None), "gaps", ()) or ()):
        fact_id = str(gap.get("fact_id") or "")
        if authority.topic_for_fact(fact_id) not in own_topics:
            # 缺口不属于本节（它在别的章节），不进本节的必需指标覆盖表。
            continue
        rows.append(CitedMetricCoverageRow(
            state="missing", fact_id=fact_id,
            code=str(gap.get("formula_id") or ""), label=str(gap.get("label") or ""),
            period=str(gap.get("period") or ""),
            reason=str(gap.get("reason_code") or gap.get("status") or "unresolved")))

    # 第二、三档：本节确实拿到的必需事实。展示与「展示不了」的分界来自构造期的两个映射。
    for coord in sorted(required):
        fact = fin[coord]
        fact_id = str(getattr(fact, "fact_id", "") or "")
        shown = displayed.get(coord)
        if shown is not None:
            table_id, column_text = shown
            rows.append(CitedMetricCoverageRow(
                state="displayed", fact_id=fact_id,
                code=str(getattr(fact, "code", "") or ""),
                label=str(getattr(fact, "label", "") or ""),
                period=str(getattr(fact, "period", "") or ""), reason="displayed",
                table_id=table_id, column_text=column_text))
            continue
        if coord not in disposition:
            # 构造期漏记一条必需事实的去向 ⇒ 覆盖读数会**少一行**，而少的东西正好是
            # 「未展示的必需指标去哪了」。这是内部不一致，不得静默跳过。
            raise CitedMetricTableError(
                f"必需财务事实 {fact_id!r} 既没有进表，也没有在构造期记下未成表的原因："
                "覆盖读数不得遗漏任何一条必需事实", reason="coverage_disposition_missing")
        rows.append(CitedMetricCoverageRow(
            state="available_not_displayed", fact_id=fact_id,
            code=str(getattr(fact, "code", "") or ""),
            label=str(getattr(fact, "label", "") or ""),
            period=str(getattr(fact, "period", "") or ""),
            reason=disposition[coord]))
    return tuple(rows)


def _row_display_tier(bucket: Mapping[str, Any], *, metric_code: str,
                      routing: Any = None) -> tuple[str, tuple[str, ...], str]:
    """一条指标行落在哪一档，以及**凭什么**（返回 `(tier, basis, presentation_column)`）。

    两条判据，`cmtr-4` 起必须**一致**：

    1. **权威自己的标记**：`NS.is_proxy_fact` 读的是权威写给这条事实的 `reason_code`
       （`PROXY_FINANCE_EXPENSES`）——「这个数值是拿财务费用顶利息费用算出来的」这件事由
       **权威自己**声明，本模块不猜指标名、不按公司/行业分支、也没有任何关键词表。
       一行里**只要有一格**来自代理口径，整行进诊断槽位：行是表格里最小的展示身份，同一行有
       一格是诊断口径、另一格是精确口径时，只把其中一格「藏起来」会让这一行在读者眼里仍然
       成立，而它其实一半是代理值。
    2. **呈现层路由声明 + 冻结 Contract**：这个指标经路由声明落到哪一栏（
       :mod:`sections.financial_presentation_routing`），那一栏在冻结 Contract 里声明的
       `display_tier` 是 `required_body` 还是 `diagnostic_only`。

    两条判据不一致时**在构造期停住**，不替任何一方作决定。「呈现层说这一栏只是诊断档，而权威
    没有给这条事实打代理标记」与「Contract 要这一栏进正文，而行里却有代理值」都是真的分歧：
    取一边就会把另一边盖掉，而盖掉的那一边恰好是读者看不到的那一半。构造期停住是本模块唯一
    诚实的落法（与 `_checked_cell` 同一条 fail-closed 规则）。

    没有路由声明（`routing is None`）时退回 `cmtr-3` 的单判据行为——那一节没有装这份声明，
    本模块不替它编一份。
    """
    observed = "required_body"
    for entry in bucket.values():
        if NS.is_proxy_fact(entry[0]):
            observed = "diagnostic_only"
            break
    if routing is None:
        basis = ("authority_proxy_fact_marker",) if observed == "diagnostic_only" else ()
        return observed, basis, ""
    column = str(routing.column_for_metric_code(metric_code) or "")
    if not column:
        raise CitedMetricTableError(
            f"指标 {metric_code!r} 经呈现层路由声明（版本 {routing.routing_version}）落不到任何"
            "一栏：本表不知道它该进正文指标表还是诊断槽位。呈现层没有声明它属于哪一栏，"
            "本模块不替它挑一个", reason="metric_column_not_declared")
    contract = str(routing.display_tier_for_column(column) or "")
    if contract not in _CONTRACT_DISPLAY_TIERS:
        raise CitedMetricTableError(
            f"指标 {metric_code!r} 所在栏 {column!r} 在冻结 Contract 里的展示层级是 "
            f"{contract!r}，不在本模块认得的 {list(_CONTRACT_DISPLAY_TIERS)} 内："
            "认不出的档不得按默认档往前走", reason="contract_display_tier_unknown")
    if contract != observed:
        raise CitedMetricTableError(
            f"指标 {metric_code!r} 的分层判据不一致：呈现层声明把它放在 `{column}`"
            f"（冻结 Contract 的展示层级 `{contract}`），而本行实际观察到的权威口径是 "
            f"`{observed}`（{'行内有一格来自代理口径' if observed == 'diagnostic_only' else '行内没有代理口径标记'}）。"
            "两条判据各说一半时取哪一边都是在替另一边作决定，本表在构造期停住",
            reason="display_tier_not_agreed_with_contract")
    basis = (("contract_display_tier",) if contract == "diagnostic_only" else ()) \
        + (("authority_proxy_fact_marker",) if observed == "diagnostic_only" else ())
    return observed, tuple(b for b in CITED_METRIC_TIER_BASES if b in basis), column


def _table_caption(*, unit: str, basis: str, display_tier: str) -> str:
    """表题：`NS.financial_table_caption` 的原话，诊断档再挂一条**分层**后缀。

    后缀不是事实，也不含任何数字与主体名；它只说明这张表在读者面的哪一块，从而让两档表的
    表题不会逐字相同（`financial_table_caption` 的同一性判据在分层之后必须继续成立）。
    """
    caption = NS.financial_table_caption(unit=unit, basis=basis)
    if display_tier == "diagnostic_only":
        return f"{caption} · {CITED_METRIC_DIAGNOSTIC_CAPTION_SUFFIX}"
    return caption


def _checked_cell(*, fact: Any, column_text: str, unit: str, citation_key: str,
                  table_key: Any) -> str:
    """渲染一格并**当格核对**（`cmtr-2` 的逐格轴）。**不做任何算术**：全部逐字取权威字段。

    五条判据各自对应一件读者看得见的事，缺任何一条都不是形式问题：

    * **数值** —— 权威自己的 `display`/`value_text` 必须非空，且真的出现在渲染结果里
      （渲染层丢值在这里暴露，不等到读者发现空格）；
    * **期间表达** —— 事实自己声明的期间说法必须与**它所在那一列**的列名逐字一致：期间是这一格
      身份的一部分，错列会让读者把 2024 的数读成 2025 的；
    * **单位** —— 事实自己的量纲必须与所在行声明的单位一致，否则这一格的数字没有量纲；
    * **代理口径** —— 代理事实的限定语必须真的渲染进这一格（只写在引用链里不算披露：引用链
      不进表格正文，读者在那一格里看到的仍是光秃秃的数值）；
    * **引用** —— 非空格必须带引用键，读者才能逐格回查。

    判据的载体从「引用这条事实的正文句」（`cmtr-1`）换成**权威事实自己的字段**：写作结果不再
    参与表格构造，因此不能再拿它当核对基准。标点依旧不承担任何判据。
    """
    components = NS.financial_cell_components(fact)
    fact_id = str(getattr(fact, "fact_id", "") or "?")
    problems: list[str] = []
    if not components.value:
        problems.append("数值为空")
    elif components.value not in components.cell:
        problems.append(f"渲染结果 {components.cell!r} 里没有数值 {components.value!r}")
    if components.period_text != column_text:
        problems.append(
            f"期间表达 {components.period_text!r} 与所在列名 {column_text!r} 不一致")
    if str(getattr(fact, "unit", "") or "").strip() != unit:
        problems.append(f"量纲 {getattr(fact, 'unit', '')!r} 与所在行单位 {unit!r} 不一致")
    if components.qualifier and components.qualifier not in components.cell:
        problems.append(f"代理口径的限定语 {components.qualifier!r} 没有渲染进这一格")
    if not str(citation_key or "").strip():
        problems.append("这一格没有引用键")
    if problems:
        raise CitedMetricTableError(
            f"财务事实 {fact_id!r} 落在期间列 {column_text!r} 的这一格没有通过逐格核对："
            + "；".join(problems), reason="cell_component_not_carried", table_key=table_key)
    return components.cell


def build_cited_metric_tables(*, section_id: str, authority: Any,
                              manifest: CW.CitedWriterInputManifest,
                              presentation_routing: Any = None) -> CitedMetricTableOutcome:
    """财务节的确定性「指标 × 期间」表。**不发起任何模型调用、不做任何计算**。

    输入恰好三样：本节 id、本节权威、本节输入清单；可选的第四样是**呈现层**路由声明
    （`cmtr-4` 起）：它**不**参与「表里有没有这一行」，只参与「这一行进正文表还是诊断槽位」，
    且必须与权威自己的代理标记对得上（见 :func:`_row_display_tier`）。传 `None` 的那一节退回
    单判据行为，不替它编一份声明出来。

    **正文草稿不是输入**——写作结果不参与「表里有没有这一行」，因此本函数连草稿都看不到
    （`cmtr-1` 的 `draft` 参数已随判据一起撤销）。
    """
    kind = str(getattr(authority, "producer_kind", "") or "")
    if kind != "financial_workflow":
        return _refuse(section_id, kind, "authority_not_financial",
                       f"本权威 producer_kind={kind!r} 不是财务权威：指标表只由财务事实构成")

    entries = NS.authority_fact_entries(authority)
    fin: dict[tuple[str, str, str], Any] = {
        coord: fact for coord, fact in entries.items() if coord[0] == "financial_pack"}
    required = _required_coords(authority, fin)

    #: 构造期顺手记下的三根账：某条事实**为什么**没进表 / 它进了哪张表的哪一列 /
    #: 它所在行**凭什么**落在这一档、按呈现层声明属于哪一栏。
    #: 覆盖读数直接由前两者拼出（见 `_coverage_rows`），因此两者永远同源。
    disposition: dict[tuple[str, str, str], str] = {}
    displayed: dict[tuple[str, str, str], tuple[str, str]] = {}
    row_meta: dict[tuple[str, str, str], tuple[tuple[str, ...], str]] = {}

    def _coverage() -> tuple[CitedMetricCoverageRow, ...]:
        return _coverage_rows(authority=authority, required=required, fin=fin,
                              disposition=disposition, displayed=displayed)

    if not fin:
        return _refuse(section_id, kind, "no_authority_facts",
                       f"财务权威 {getattr(authority, 'artifact', None)!r} 没有任何事实",
                       coverage=_coverage())

    # 清单是**本节读者面**的边界：权威里有、但没进本清单的事实不进表（两处口径必须一致，
    # 否则表格会展示一条本节清单都不知道的事实）。清单来自权威扫描，与写作结果无关。
    key_by_coord = {f.key: f.citation_key for f in manifest.facts}
    declared_periods = tuple(
        str(p) for p in (getattr(getattr(authority, "artifact", None), "periods", ()) or ()))

    groups: dict[tuple[str, str, str], dict[str, tuple[Any, str, tuple[str, str, str]]]] = {}
    order: list[tuple[str, str, str]] = []
    for coord, fact in fin.items():
        is_required = coord in required
        citation_key = key_by_coord.get(coord)
        if citation_key is None:
            if is_required:
                disposition[coord] = "not_in_input_manifest"
            continue
        # 双期派生事实永不进期间轴表格（`FORMULA_REVIEW` §5.1 的 Δpp）：它的 `period` 是复合
        # 记号，不是任何单一期间，任何单一列名都在说一件不成立的事。判据是事实自己声明的字段
        # （`input_periods`），不是 `code` 字符串、也不是期间形状。
        if NS.is_derived_two_period_fact(fact):
            if is_required:
                disposition[coord] = "derived_two_period_fact"
            continue
        code = str(getattr(fact, "code", "") or "").strip()
        period = str(getattr(fact, "period", "") or "").strip()
        value = (str(getattr(fact, "display", "") or "").strip()
                 or str(getattr(fact, "value_text", "") or "").strip())
        if not (code and period and value):
            # 缺 code/期间/数值渲染的财务事实没有可陈列的单元格（它是缺口，不是表格行）。
            if is_required:
                disposition[coord] = "no_displayable_cell"
            continue
        # 口径必须由权威自己声明（`financial_v2.period_basis`）。声明了却不在封闭取值内、或
        # 期间记号本身是权威日期形状却没有声明口径 —— 两种都不许「按外观补一个口径」：
        # 补错方向恰好是把期间量写成时点量。
        basis = str(getattr(fact, "period_basis", "") or "").strip()
        fact_id = str(getattr(fact, "fact_id", "") or "")
        if basis and basis not in _FPB.PERIOD_BASES:
            raise CitedMetricTableError(
                f"财务事实 {fact_id!r} 的期间口径 {basis!r} 不在 {list(_FPB.PERIOD_BASES)} 内"
                "（口径是封闭取值，不得自造）", reason="period_basis_unknown")
        if not basis and _FPB.is_authority_period_token(period):
            raise CitedMetricTableError(
                f"财务事实 {fact_id!r} 的期间 {period!r} 是权威日期形状，却没有声明期间口径"
                "（`period_basis`）：无法判断它是时点量还是期间量，表格不得凭期间记号的外观"
                "替它挑一个", reason="period_basis_undeclared")
        if not declared_periods:
            # 列轴自创被拒之后整节会退回 `no_declared_period_order`；这条事实的去向在此记下，
            # 免得那句拒绝背后是一张空白的覆盖表。
            if is_required:
                disposition[coord] = "period_axis_not_declared"
        key = (str(getattr(fact, "kind", "") or ""), code, basis)
        bucket = groups.setdefault(key, {})
        if period in bucket:
            raise CitedMetricTableError(
                f"财务事实 {key} 在期间 {period!r} 上有两条事实：同一格不得归属两条断言",
                reason="cell_owner_ambiguous", table_key=key)
        bucket[period] = (fact, citation_key, coord)
        if key not in order:
            order.append(key)

    if not order:
        return _refuse(
            section_id, kind, "no_two_period_metric",
            f"财务权威有 {len(fin)} 条事实，但没有一条同时满足「在本节事实清单内」且"
            "「同一指标 ≥2 个期间」：本节没有可成表的「重复指标 × 期间」",
            coverage=_coverage())

    if not declared_periods:
        return _refuse(
            section_id, kind, "no_declared_period_order",
            "权威 artifact 没有声明任何期间顺序：财务表格的列轴不得自创顺序",
            coverage=_coverage())

    company_id = str(getattr(authority, "company_id", "") or "").strip()
    if not company_id:
        raise CitedMetricTableError(
            "财务表格的 entity_scope 必须来自权威主体身份（company_id 为空，不得留空）",
            reason="company_id_missing")
    # 主体必须写成**可核实的公司名称**，并保留可回查的主体标识。名称来自权威输入自己的
    # `company_name`；权威没有名称时**不发明一个** —— 退到主体标识，读者看到的仍是真实字段。
    company_name = str(getattr(authority, "company_name", "") or "").strip()
    entity_scope = f"{company_name}（{company_id}）" if company_name else company_id

    # 表按 `(unit, period_basis)` 成组：同一张表的每一行必须同口径，否则列名只能对着其中
    # 一半行说真话（`2025年末` 对着利润表流量就是错的，`2025年度` 对着资产负债表余额也是错的）。
    by_table: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    table_order: list[tuple[str, str]] = []
    for key in order:
        bucket = groups[key]
        if len(bucket) < NS.FINANCIAL_TABLE_MIN_PERIODS:
            # 单期指标不成行（它本来就该写在正文里）：把它**当成一条去向**记下，覆盖读数里
            # 每一条未展示的必需指标才会都有一条准确原因。
            for _fact, _key, coord in bucket.values():
                if coord in required:
                    disposition.setdefault(coord, "below_min_periods")
            continue
        labels = {str(getattr(f, "label", "") or "").strip() for f, _k, _t in bucket.values()}
        units = {str(getattr(f, "unit", "") or "").strip() for f, _k, _t in bucket.values()}
        if len(labels) != 1 or len(units) != 1:
            raise CitedMetricTableError(
                f"财务事实 {key} 跨期间的 label/unit 不一致（label={sorted(labels)}，"
                f"unit={sorted(units)}）：同一个指标不得在表格里有两套名字或两个量纲",
                reason="metric_label_unit_inconsistent", table_key=key)
        label = labels.pop()
        unit = units.pop()
        if not label or not unit:
            raise CitedMetricTableError(
                f"财务事实 {key} 缺 label/unit：含数字的表格行必须有指标名与单位，"
                "不得由渲染层推定", reason="missing_label_or_unit", table_key=key)
        tier, tier_basis, presentation_column = _row_display_tier(
            bucket, metric_code=key[1], routing=presentation_routing)
        row_meta[key] = (tier_basis, presentation_column)
        table_key = (unit, key[2], tier)
        by_table.setdefault(table_key, []).append(key)
        if table_key not in table_order:
            table_order.append(table_key)
    if not by_table:
        return _refuse(
            section_id, kind, "no_two_period_metric",
            f"本节清单内的财务事实里没有任何指标达到 {NS.FINANCIAL_TABLE_MIN_PERIODS} 个期间："
            "单期指标不成行（它本来就该写在正文里）", coverage=_coverage())

    tables: list[CitedMetricTable] = []
    for unit, basis, display_tier in table_order:
        keys = by_table[(unit, basis, display_tier)]
        used = {period for key in keys for period in groups[key]}
        columns = [period for period in declared_periods if period in used]
        if set(columns) != used:
            raise CitedMetricTableError(
                f"财务表格出现 artifact 未声明的期间 {sorted(used - set(declared_periods))}："
                "列轴只能由权威声明的期间组成", reason="period_not_declared_by_artifact")
        # 列名写**期间表达**而不是裸的期间末日（`financial_period_text`）。同一列只能有一种
        # 表达——期间表达是权威事实自己带的字段，同一期间上出现两种说法是数据冲突，不得由排版抹平。
        column_texts: list[str] = []
        for period in columns:
            texts: set[str] = set()
            for key in keys:
                entry = groups[key].get(period)
                if entry is not None:
                    texts.add(NS.financial_period_text(entry[0]))
            if len(texts) != 1:
                raise CitedMetricTableError(
                    f"财务表格的期间 {period!r} 在列名上有不止一种期间表达 {sorted(texts)}："
                    "同一列不得有两种期间说法", reason="period_text_conflict")
            column_texts.append(texts.pop())
        if len(set(column_texts)) != len(column_texts):
            raise CitedMetricTableError(
                f"财务表格出现重名的期间表达 {column_texts}：两列同名时读者无法判断哪一列是"
                "哪个期间", reason="period_text_conflict")

        #: 这一张表的分层判据名 = 表内各行判据名的并集，按 `CITED_METRIC_TIER_BASES` 的固定
        #: 次序排列（不由行的遍历次序决定）。同一张表里任何一行是诊断档的，整张表就是诊断档
        #: （分组的 `display_tier` 已经保证行与表同档），因此这个并集如实回答「凭什么」。
        merged_basis: list[str] = []
        for key in keys:
            for name in row_meta[key][0]:
                if name not in merged_basis:
                    merged_basis.append(name)
        tier_basis = tuple(n for n in CITED_METRIC_TIER_BASES if n in merged_basis)

        row_specs: list[CitedMetricTableRow] = []
        shown_here: list[tuple[tuple[str, str, str], str]] = []
        for key in keys:
            bucket = groups[key]
            label = str(getattr(next(iter(bucket.values()))[0], "label", "") or "").strip()
            cells: list[str] = []
            fact_ids: list[str] = []
            citation_keys: list[str] = []
            row_periods: list[str] = []
            for period, column_text in zip(columns, column_texts):
                entry = bucket.get(period)
                if entry is None:
                    # 缺值显式留空：这一格在本节清单内没有权威事实，不得推算/补齐/回填。
                    cells.append(CITED_METRIC_TABLE_EMPTY_CELL)
                    continue
                fact, citation_key, coord = entry
                cells.append(_checked_cell(fact=fact, column_text=column_text,
                                           unit=unit, citation_key=citation_key,
                                           table_key=key))
                fact_ids.append(str(getattr(fact, "fact_id", "") or ""))
                citation_keys.append(citation_key)
                row_periods.append(column_text)
                shown_here.append((coord, column_text))
            row_specs.append(CitedMetricTableRow(
                label=label, unit=unit, cells=tuple(cells), fact_ids=tuple(fact_ids),
                citation_keys=tuple(citation_keys), period_texts=tuple(row_periods),
                presentation_column=row_meta[key][1]))
        table = CitedMetricTable.create(
            section_id=section_id,
            caption=_table_caption(unit=unit, basis=basis, display_tier=display_tier),
            header=(NS.FINANCIAL_TABLE_LABEL_HEADER, *column_texts),
            entity_scope=entity_scope, unit=unit, period_basis=basis,
            rows=tuple(row_specs), display_tier=display_tier,
            display_tier_basis=tier_basis)
        tables.append(table)
        # 去向读数只在**表真的建成**之后记：claimed-displayed 与新表 id 同源，读者能翻到那一格。
        for coord, column_text in shown_here:
            displayed[coord] = (table.table_id, column_text)

    return CitedMetricTableOutcome(
        section_id=section_id, schema_version=CITED_METRIC_TABLE_SCHEMA_VERSION,
        rule_version=CITED_METRIC_TABLE_RULE_VERSION, authority_kind=kind,
        tables=tuple(tables), coverage=_coverage())


def render_cited_metric_table_markdown(table: CitedMetricTable,
                                       *, reader_unit: bool = True) -> str:
    """一张表的读者面 Markdown：表题（含单位与口径）+ 表头 + 行 + 单位/口径说明两行。

    单位与口径**必须**出现在表题那一行：读者在读到数字之前就该知道这张表是元还是倍、
    是时点量还是期间量，不必先读列名再回推。
    """
    lines = [f"**{table.caption}**", "",
             f"- 主体：{table.entity_scope}",
             f"- 单位：{NS.reader_unit_text(table.unit)}" if reader_unit
             else f"- 单位：{table.unit}",
             f"- 期间口径：{NS.FINANCIAL_BASIS_TEXTS.get(table.period_basis, table.period_basis)}"
             f"（`{table.period_basis or '未声明'}`）", ""]
    if table.display_tier_basis:
        #: 「凭什么这一档」必须与表题同处可见：读者在读到那一行数字之前就该知道它为什么不在
        #: 正文指标表里，而不是靠自己从代理限定语里回推。
        lines.append(f"- 分层判据：{'、'.join('`%s`' % b for b in table.display_tier_basis)}")
        lines.append("")
    lines.append("| " + " | ".join(table.header) + " |")
    lines.append("|" + "---|" * len(table.header))
    for row in table.rows:
        shown = [cell if cell else "（留空）" for cell in row.cells]
        lines.append("| " + " | ".join([row.label, *shown]) + " |")
    lines.append("")
    lines.append("> 空着的格是**留空**：该指标在该期间没有进入本节事实清单的合格权威事实。"
                 "本表不做任何推算、补齐或按公式回填。")
    return "\n".join(lines)


def render_cited_metric_coverage_markdown(outcome: CitedMetricTableOutcome) -> str:
    """「必需指标 × 期间」覆盖表的读者面 Markdown：逐条列出去向与原因。

    它**不是**一张表格数据，而是一份对账清单：读的人据此知道某个必需指标是进了表、还是没进
    表以及为什么。最后一行只报计数——**不给**任何「覆盖通过 / 未通过」的结论：那是人工判断。
    """
    lines = [f"**必需财务指标 × 期间覆盖对账**（规则 `{outcome.rule_version}`）", ""]
    counts = dict(outcome.coverage_counts())
    lines.append("- 必需事实条数：" + str(len(outcome.coverage)))
    for state in CITED_METRIC_COVERAGE_STATES:
        lines.append(f"- `{state}`：{counts.get(state, 0)}")
    lines.append("")
    if not outcome.coverage:
        lines.append("> 本节没有被标记为「必需」的财务事实。")
        return "\n".join(lines)
    lines.append("| 去向 | 指标 | 期间 | 说明 |")
    lines.append("|---|---|---|---|")
    for row in outcome.coverage:
        where = (f"表 `{row.table_id}` 列 {row.column_text}" if row.state == "displayed"
                 else f"原因 {row.reason}")
        lines.append(f"| `{row.state}` | {row.label}（`{row.code}`） | {row.period} | {where} |")
    lines.append("")
    lines.append("> `missing` 那一档的原因是**权威自己**记的 `reason_code`，本表逐字搬运；"
                 "其余两档的原因来自构造期的真实判据。"
                 "「已展示」不等于「数字有权威」——数值权威另有其门。")
    return "\n".join(lines)

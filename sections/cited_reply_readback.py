"""真实 Writer **返回**的只读诊断（`crrb-1`）：原稿人读页 + 能在现有证据下做的业务核对。

为什么单独一个模块、不直接跑 `sentence_check`（`sc-5`）：`check_cited_prose` 要一份
**精确输入清单**（`CitedWriterInputManifest`）并与草稿的 `input_manifest_id` 对账。真实 r2
那一轮**没有**在运行目录里落下清单身份，且清单里有一批字段（`pack_id`、`provenance_identity`、
`reading_view_fingerprint`、`document_id`、`structured_view`、`content_qualification`）**不在**
请求面里。拿另一轮的清单顶替，或按请求面拼一份「像清单的东西」，都是**伪造身份**——本模块
不做，也不假装做过。

能做的、且**判据与被顶替的那条链逐字相同**的事：

* 引用键是否存在（`citation_identity`）、有没有引用（`citation_present`）；
* 本句所引来源的**登记栏目**里有没有本小节声明的那个（`aspect_attribution`，复用请求面自带的
  `materials[].aspect_ids` × `subsections[].declared_aspect_ids`）；
* 数字表面有没有逐字来源（`numeric_surface`）与金额/比率的**资格**（`numeric_qualification`）：
  两条都用 `NS.authorized_numeric_tokens` + `NS.numeric_token_authorized` 与
  `SC.is_qualified_numeric_surface` —— 与 `sc-5` 同一批原语，不另立词表；
* 含糊期间措辞（`NS.vague_period_hits`）。**注意**：这与 `sc-5` 的 `period_surface` **不是**
  同一件事——那一轴明确授权「报告期」这个报告自身的期间概念。这里记的是**读者面**能不能指出
  具体期间，属语义审阅，不判硬错。

明确的**未判**轴（每条都写进产物，不静默略过）：采用去向、`sc-5` 的完整逐句核对（含表数字的
格级授权、来源角色、旧来源不得写成当前状态、呈现层路由）、`report_version` 与清单绑定、
独立审阅。这些都需要那份没留下来的精确清单。

本模块**只读**：不改句子、不挪句子、不判语义、不代替独立审阅，也不从普通材料原文里的一个数字
推出「这是一条合格事实」。
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from harness import topic_schema as TS
from sections import cited_reply_normalize as CRN
from sections import narrative_schema as NS
from sections import sentence_check as SC


#: 诊断规则版本（换判据就换号）。
#:
#: `crrb-2`：产物**形状**变了——新增 `analyst_notes`（人工判断，与机械判据分开列），
#: `numeric_surface` 拆成「来源里确实没有」（硬错）与「只差排版空格」（待审）两条，
#: 并新增通用的 `cross_subsection_near_duplicate`。
#:
#: `crrb-3`：**判据收紧了一处、形状扩了三处**。
#:
#: * 空格型数字差异回到**硬核对失败**：`crrb-2` 把它整条降成待审，于是「硬错 9 句」被读成
#:   「硬错 8 句」——那是**记账错误**，不是放宽。现在它同时记两条：`numeric_surface`（硬错，
#:   现行 `sc-5` 判据确实失败）与 `numeric_surface_spacing_suspect`（待审，成因归类）。
#:   金额 / 比例 / 期间 / 数字权威规则**一律没有放宽**。
#: * 新增 `adjudicable_summary`（可判轴头条，数字由逐句结论算出）、`citation_labels`
#:   （引用键 → 请求面自带的材料/事实标签）、`gap_context`（缺口 reason 与「该栏本轮有没有
#:   材料」的对账）；`coverage` 增加**真正比过的** `same_order`。
#: * 人读页逐句补上引用材料、所在栏目、硬错或待审原因；新增 §5 把业务错因指到正确环节。
#:
#: `crrb-4`：新增 `source_class_context`（补件「期望来源类别」的逐条对账：自述值是不是一个
#: 可检索来源类、在不在该栏 Contract 允许的那几类里）。**判据一处也没动**——`crrb-3` 对
#: 缺口 reason 的读法照旧，数字规则照旧，硬错计数照旧。加这一轴是因为 `financial_pack`
#: 这类取值会把补件指向一个**不是检索入口**的方向。
#:
#: `crrb-5`：栏目归属这一根轴**真的动了判据**，同时补上一件跟着变的读数。
#:
#: * **判据**：`aspect_attribution` 从「材料登记 ∋ 小节声明的那一栏」改成两级——
#:   「材料登记 ∩ 小节声明 ≠ ∅」**且**（本段声明了服务栏目时）「材料登记 ∩ 本段声明 ≠ ∅」。
#:   这一改是跟着生产侧 `sentence_check` 走的：小节从「恰好一栏」放宽到「覆盖多栏」之后，
#:   只看小节那一边，宽小节里「拿 A 栏的材料写 B 栏」会被放过（小节声明覆盖了 A，交集非空）。
#:   **没有放宽**任何一条：交集为空照旧硬错，空段声明照旧不放松判据。
#: * **读数**：逐句多带一个 `paragraph_aspect_ids`（本段自己声明服务的那几栏），人读页把
#:   「本小节覆盖哪几栏」与「本段答的是哪一栏」**分两行**印出来——只印前者，读者会把
#:   「这一节很宽」读成「这一栏写过了」。
#:
#: `crrb-6`：数字表面轴**换口径**（跟着生产侧 `sc-7` 走）——「数字与单位之间的**排版空白**」
#: 这一类差异（来源 `24 家` / 正文 `24家`；来源 `1,700 万` / 正文 `1,700万`）不再记硬错：
#: `NS._literal_token` 从「只折叠空白」改为「**删除**空白」，于是它落进**严格排版空白等价**，
#: 与生产侧同一把尺子。
#:
#: * `numeric_surface_spacing_suspect`（待审）被 `numeric_surface_spacing_equivalent`（**非
#:   失败**，`可核` 档）取代：它只点出「本句哪几串数字靠排版空白等价才被接受」，
#:   **不**从硬错句数里减掉任何一句。两个数各自成键，谁也不算进谁。
#: * **放宽的只有空白**：数值 / 符号 / 单位 / 尾零一个都不派生（`-5`≠`5`，`1.6`≠`1.60`，
#:   `129,641,258千`≠`129,641,258千元`）。去空白后仍找不到的数字照样硬错——数字权威规则
#:   一条都没有放宽。
#: * `adjudicable_summary` 相应换键：`spacing_suspect_*` / `spacing_only_failure_*` 退场，
#:   换成 `spacing_equivalent_sentences` / `spacing_equivalent_sentence_ids`。
#: `crrb-7`：缺口这一轴**两处判据换成链上那一份**，并让「空壳段」这一条在离线侧**真的跑到**。
#:
#: * `gap_context` 从「整节声明的栏目」改成「**这条缺口自己的那一栏**」：请求面自带
#:   `subsections[].requirement_text`（逐行）↔ `declared_aspect_ids`（逐位）的**位置对位**，
#:   所以一条缺口落到哪一栏是**能判**的——判据逐字来自生产侧
#:   `cited_writer.aspect_for_requirement`，不另写一份。改之前，本节别处有材料就会被读成
#:   「这一栏有材料」：`materials_registered_for_aspect` 会给每一个缺口印上同一个**整节**的大数，
#:   `claims_no_source_but_materials_exist` 随之对每一条缺口都置真——那正是
#:   `assign_gap_reason` 的注释里点名的那个错误（「供应商栏一份来源都没有」被记成
#:   「有材料但不合格」，下游据此去补事实资格而不是去检索）。
#:   对不上栏位（行数与栏数不等、或文本不逐字相等）时**退回原来的整节口径**并把
#:   `column` 记成 `null`——「判不出来」与「判出来是这一栏」在产物上分得开。
#: * 来源计数从**只有材料行**改成**材料行 ∪ 事实行**：与生产侧 `registered_source_keys`
#:   同一根轴。财务/附注/外部那几支的 `aspect_ids` 常为空、靠呈现层路由认栏，只数材料会把
#:   它们一律读成「本栏没有来源」。
#: * `diagnose` 现在把请求面读数交给 `crn-4` 的第三条规则
#:   （`CRN.EmptyShellContext`）：归一那一步在生产侧**已经跑过**，离线侧若还按 `crn-1` 读，
#:   同一串字节在两个产物上会得到两个 `empty_shell_rule`。读数**四项**（这一栏登记到几条来源、
#:   顶层有没有系统判定的 `no_source_in_manifest` 缺口、这一栏**属不属于该段所在的小节**、
#:   这一栏在该小节有没有**可唯一对应**的缺口支持）**只**由请求面算出来，模型自述的 `reason`
#:   一个字都不采信。
#: * `crrb-8` = `crrb-7` + 第三项读数的接线：`crn-3` 新增「本小节归属」，一段借用**别的小节**
#:   声明的栏目造空壳时**不得**被删。离线侧若不把这第三项交给 `EmptyShellContext`，
#:   同一串字节会判出与生产侧**相反**的 `empty_shell_rule`——生产删、离线留，或反过来。
#:   归属表**只由请求面自己声明的 `declared_aspect_ids` 建**，不经过 `_face_spec`
#:   （`title` 缺失会让规格建不起来，那是另一件事，不该顺手把归属判成「查不到」）。
#: * `crrb-9` = `crrb-8` + 第四项读数的接线：`crn-4` 新增「缺口支持」（同一小节内**可唯一
#:   对应**的顶层缺口、其**系统判定**理由落在 `GAP_SUPPORTED_REASONS`）。这一项与 `crrb-8` 那项
#:   同理：离线侧漏了它，同一串字节就会判出与生产侧相反的 `empty_shell_rule`。建表的**唯一**
#:   口径落在 `cited_writer.unique_subsection_gap_reasons`，离线侧不另写一份。
CITED_REPLY_READBACK_VERSION = "crrb-9"

#: 一句话的**结论档**。第四档是这一批的特殊产物：**因缺精确清单本轴未判**。
VERDICT_CHECKABLE = "可核"
VERDICT_HARD_ERROR = "硬错"
VERDICT_NEEDS_REVIEW = "需语义审阅"
VERDICT_UNJUDGED = "因缺精确清单本轴未判"

_VERDICT_ORDER = (VERDICT_HARD_ERROR, VERDICT_NEEDS_REVIEW, VERDICT_CHECKABLE, VERDICT_UNJUDGED)

#: 核对轴名（与 `sentence_check.CHECK_KINDS` 同名同义者**复用同名**，便于两处对账）。
AXIS_ADJUDICATED = "adjudicated"
AXIS_UNJUDGED = "not_adjudicated_missing_manifest"

#: 数字表面轴上的**两个**记账位。前者与 `sentence_check` 的 `CHECK_KINDS` 同名同义（硬错）。
#:
#: 后者是**读回侧专有**的**非失败**记录（`crrb-6` 起）：来源把「数字 + 单位」写成带一个排版
#: 空白（`24 家`），正文写 `24家`——`sc-7` 起这一类**严格排版空白等价**已并入
#: `NS._literal_token`（只删空白，不改数值 / 符号 / 单位 / 尾零），所以它**不再**是硬错。
#: 这条记录只把「本句有哪几串数字是靠排版空白等价才被接受」点出来，好让读者知道
#: 「这一串表面与来源不是逐字节相同」。它**不**放宽任何一条：去空白后仍找不到的数字照样硬错。
CHECK_NUMERIC_SURFACE = "numeric_surface"
CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT = "numeric_surface_spacing_equivalent"

#: 第一句可判轴的**原话**。它是给人读的，所以写成常量而不是拼在渲染里：数字要能对得上
#: 「硬错 N 句里有 M 句是空格型」这一条，不能一处改、另一处忘。
ADJUDICABLE_NOT_FORMAL = ("因 r2 精确输入清单缺失，以上是**可判轴**上的结论，"
                          "**不**是本轮的正式完整核对结果。")

#: 发行人对自己的**评价性**措辞（只用于**标出待审**，从不据此判合格/不合格）。
#:
#: 这是一份**通用**词表：它说的是「发行人自述的优势判断」这一类表述，与任何一家公司、任何一个
#: 答案关键词无关。命中只把该句标进「需语义审阅」，不判硬错、不改写、不删句。
EVALUATIVE_MARKERS = (
    "领先", "一流", "优势", "先进", "完善", "显著", "卓越", "最优", "引领", "名列前茅",
    "龙头", "标杆", "率先", "首创", "核心竞争", "深度合作", "紧密合作", "广泛认可",
)

#: 跨小节**近**重复的判据：字符级相似度 ≥ 此值即标出（逐字相同者由逐字那一路另报，不重复计）。
#:
#: 0.80 不是拍的：本批真实返回里，真正「一段话换说法去填第二个栏目」的那一对是 0.915
#: （生产模式 × 销售模式），而仅是同一批数字/同一句套话在两栏各出现一次的那几对在
#: 0.756–0.814。取 0.80 把前者标出、同时**不**把行业链位置 × 技术路线（0.814，确属同一句
#: 自我介绍被两栏各写一次）漏掉。它只是一个**待审**标记：不判合格、不删句、不改写。
NEAR_DUPLICATE_RATIO = 0.80

#: 太短的句子（如「不适用」「未披露」）相似度天然虚高，不参与近重复比对。
NEAR_DUPLICATE_MIN_CHARS = 20


def _norm_ws(text: Any) -> str:
    """空白归一（**只**归一空白与全角空格：不改字符、不派生等价写法）。"""
    return re.sub(r"[\s　]+", " ", str(text if text is not None else "")).strip()


def _strip_ws(text: Any) -> str:
    """去掉**全部**空白字符。

    `sc-7` 起这与 `NS._literal_token` 同判据（后者也只删空白 + 千分位）。本函数在**读回侧**
    用来点出「这一串数字是靠排版空白等价才被接受」——它**只**做记录，判定合格与否在
    `NS.numeric_token_authorized`，不在这里。
    """
    return re.sub(r"[\s　]+", "", str(text if text is not None else ""))


# ---------------------------------------------------------------------------
# 记录类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentenceFinding:
    """一句上的一条**机械**发现。`check_kind` 复用 `sc-5` 的轴名时**同义**。"""

    check_kind: str
    verdict: str
    detail: str
    surfaces: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"check_kind": self.check_kind, "verdict": self.verdict,
                "detail": self.detail, "surfaces": list(self.surfaces)}


@dataclass(frozen=True)
class SentenceReading:
    """派生稿里的一句：编号台账 + 原样文本 + 引用键 + 逐条发现。"""

    final_id: str
    model_id: str
    subsection_id: str
    declared_aspect_ids: tuple[str, ...]
    text: str
    citations: tuple[str, ...]
    findings: tuple[SentenceFinding, ...] = ()
    #: **本段**自己声明服务的 Contract 栏目（`cw-4`；空 tuple = 本段不对应任何栏目）。
    #: 小节声明是「这一节负责哪些栏」，段声明是「这一段在答哪一栏」。两者不是一件事：
    #: `cwm-6` 把一个小节从「恰好一栏」放宽到「覆盖多栏」之后，「18 栏都有人写」这句话
    #: 就只能从段这一级读出来——只留小节那一边，页面会把「一节很宽」印成「这一栏写过了」。
    paragraph_aspect_ids: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        for level in _VERDICT_ORDER:
            if any(f.verdict == level for f in self.findings):
                return level
        return VERDICT_CHECKABLE

    def to_dict(self) -> dict:
        return {"final_id": self.final_id, "model_id": self.model_id,
                "subsection_id": self.subsection_id,
                "declared_aspect_ids": list(self.declared_aspect_ids),
                "paragraph_aspect_ids": list(self.paragraph_aspect_ids),
                "text": self.text, "citations": list(self.citations),
                "verdict": self.verdict,
                "findings": [f.to_dict() for f in self.findings]}


@dataclass(frozen=True)
class SubsectionReading:
    subsection_id: str
    title: str
    declared_aspect_ids: tuple[str, ...]
    requirement_text: str
    readings: tuple[SentenceReading, ...] = ()
    gaps: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict:
        return {"subsection_id": self.subsection_id, "title": self.title,
                "declared_aspect_ids": list(self.declared_aspect_ids),
                "requirement_text": self.requirement_text,
                "sentences": [r.to_dict() for r in self.readings],
                "gaps": [dict(g) for g in self.gaps]}


@dataclass(frozen=True)
class ReplyDiagnosis:
    """一次返回的完整只读诊断（可落盘、可回查）。"""

    reply_sha256: str
    request_face_sha256: str
    call: Mapping[str, Any] = field(default_factory=dict)
    subsections: tuple[SubsectionReading, ...] = ()
    original_subsections: tuple[Mapping[str, Any], ...] = ()
    gaps: tuple[Mapping[str, Any], ...] = ()
    follow_up_needs: tuple[Mapping[str, Any], ...] = ()
    normalization: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    axes: tuple[Mapping[str, str], ...] = ()
    #: 引用键 → **请求面自带**的可读标签（材料：id/类型/登记栏目/来源角色；事实：kind/期间/口径）。
    #: 它只由请求面里已有的字段拼出，不推断、不补全：人读页要能回答「这一句引的是哪份材料」，
    #: 而引用键本身回答不了这个问题。
    citation_labels: Mapping[str, str] = field(default_factory=dict)
    #: 逐条缺口与请求面材料登记的对账（见 :func:`_gap_context`）。
    gap_context: tuple[Mapping[str, Any], ...] = ()
    #: 逐条补件「期望来源类别」的对账（见 :func:`_source_class_context`）：自述值是不是一个
    #: 可检索来源类、在不在该栏 Contract 允许的那几类里。
    source_class_context: tuple[Mapping[str, Any], ...] = ()
    #: **人**的判断（读稿人写下的栏目错配、凑数等）。与 `findings` 严格分开：那些是机械判据，
    #: 这些不是。产物里逐条标注「人工判断」，本模块不据此改句、不据此判合格。
    analyst_notes: tuple[Mapping[str, Any], ...] = ()
    version: str = CITED_REPLY_READBACK_VERSION

    def sentences(self) -> tuple[SentenceReading, ...]:
        return tuple(r for s in self.subsections for r in s.readings)

    def to_dict(self) -> dict:
        return {
            "readback_version": self.version,
            "publishable": False,
            "boundary": {
                "what_this_is": ("真实模型返回**经离线派生归一后的只读诊断**。它**不是**那一轮的"
                                 "正式草稿，也**不是**「r2 通过」：该轮没有在运行目录里落下"
                                 "精确输入清单身份，本诊断因此无法与清单对账。"),
                "original_untouched": ("原稿逐字保留在上面的 `original_subsections` 与"
                                       "`original_draft.md` 里，一个字都没有改。"),
                "what_it_does_not_do": ("不改句、不挪句、不改事实、不判语义、不代替独立审阅、"
                                        "不把普通材料原文里的数字升格成合格事实。"),
            },
            "reply_sha256": self.reply_sha256,
            "request_face_sha256": self.request_face_sha256,
            "call": dict(self.call),
            "coverage": dict(self.coverage),
            "adjudicable_summary": self.adjudicable_summary(),
            "citation_labels": dict(self.citation_labels),
            "gap_context": [dict(g) for g in self.gap_context],
            "source_class_context": [dict(g) for g in self.source_class_context],
            "axes": [dict(a) for a in self.axes],
            "normalization": dict(self.normalization),
            "gaps": [dict(g) for g in self.gaps],
            "follow_up_needs": [dict(n) for n in self.follow_up_needs],
            "analyst_notes": [dict(n) for n in self.analyst_notes],
            "subsections": [s.to_dict() for s in self.subsections],
            "original_subsections": [dict(s) for s in self.original_subsections],
        }

    def verdict_counts(self) -> dict[str, int]:
        counts = {level: 0 for level in _VERDICT_ORDER}
        for reading in self.sentences():
            counts[reading.verdict] += 1
        return counts

    def adjudicable_summary(self) -> dict[str, Any]:
        """可判轴上的**头条**：至少几句硬核对失败，其中几句是空格型差异。

        两个数都从逐句结论**算出来**，不手写：写成常量就会在句子集合变化时变成谎话。
        「至少」不是客套——未判轴上还可能藏着错，只是本轮没有证据判它。
        """
        sentences = self.sentences()
        hard = [r for r in sentences if r.verdict == VERDICT_HARD_ERROR]
        #: 「靠排版空白等价才被接受」的句子 = 数字表面轴上挂了那条**非失败**的记录。
        #: 它**不是**硬错句，也**不**从硬错里减掉任何一句：那两个数各自独立成键，谁也不算进谁。
        spacing_equiv = [
            r for r in sentences
            if any(f.check_kind == CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT
                   for f in r.findings)]
        hard_ids = [r.final_id for r in hard]
        spacing_ids = [r.final_id for r in spacing_equiv]
        headline = f"已知至少 {len(hard)}/{len(sentences)} 句硬核对失败"
        if spacing_ids:
            headline += (f"；另有 {len(spacing_ids)} 句的数字表面靠**严格排版空白等价**"
                         "被接受（非失败，已单列）")
        return {
            "axis": AXIS_ADJUDICATED,
            "sentence_total": len(sentences),
            "hard_error_sentences": len(hard),
            "hard_error_sentence_ids": hard_ids,
            "spacing_equivalent_sentences": len(spacing_equiv),
            "spacing_equivalent_sentence_ids": spacing_ids,
            "headline": headline,
            "not_a_formal_check": ADJUDICABLE_NOT_FORMAL,
            "rule_unchanged": ("数字权威规则**一条都没有放宽**：来源里**找不到**的数字照样硬错"
                               "（`sc-7` 只把「数字与单位之间的排版空白」这一类并入判据，"
                               "数值 / 符号 / 单位 / 尾零都不派生）。"),
        }


# ---------------------------------------------------------------------------
# 核对
# ---------------------------------------------------------------------------


def _material_index(request_face: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for row in request_face.get("materials") or ():
        key = str(row.get("key", "") or "")
        if key:
            index[key] = row
    for row in request_face.get("authority_facts") or ():
        key = str(row.get("key", "") or "")
        if key:
            index[key] = row
    return index


def _fact_texts(request_face: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for row in request_face.get("authority_facts") or ():
        for name in ("text", "value_text", "display", "value"):
            value = row.get(name)
            if isinstance(value, str) and value:
                texts.append(value)
    return texts


def _citation_labels(request_face: Mapping[str, Any]) -> dict[str, str]:
    """引用键 → 可读标签。**只**用请求面里已有的字段拼，缺字段就如实留空。"""
    labels: dict[str, str] = {}
    for row in request_face.get("materials") or ():
        key = str(row.get("key", "") or "")
        if not key:
            continue
        aspects = "、".join(str(a) for a in (row.get("aspect_ids") or ())) or "（未登记栏目）"
        labels[key] = (f"材料 {row.get('material_id', '')}（类型 `{row.get('material_type', '')}`｜"
                       f"登记栏目 {aspects}｜来源角色 `{row.get('source_role', '')}`｜"
                       f"内容形态 `{row.get('content_kind', '')}`｜"
                       f"{'表材料' if row.get('is_table') else '非表材料'}）")
    for row in request_face.get("authority_facts") or ():
        key = str(row.get("key", "") or "")
        if not key:
            continue
        labels[key] = (f"权威事实（`{row.get('authority_kind', '')}`｜期间 "
                       f"`{row.get('period', '')}`｜口径 `{row.get('scope', '')}`｜"
                       f"事实类型 `{row.get('fact_type', '')}`）")
    return labels


def _declared_aspects_of(spec: Mapping[str, Any]) -> tuple[tuple[str, ...], bool]:
    """读一小节行的**声明栏目集合**，返回 `(集合, 是否用了旧单数键)`。

    `cwm-6` 把小节行上的 `declared_aspect_id: str` 换成了 `declared_aspect_ids: [str]`。
    **历史留存**（`cwm-5` 及更早的请求面，例如 r28 的真实字节）仍然只带单数键，而这份读回的
    一项职责正是拿历史字节复算。认不出旧键，栏目归属轴会在整份历史面上**静默跳过**——
    那比不跑更坏：它会把「没判过」印成「都对上了」。

    因此这里保留一条**有界**的旧键读法，并把「这一次读的是旧键」一路记进诊断
    （`coverage.declared_aspect_field`）。它不是「取集合首元」的兼容——旧键就是旧 wire 的
    完整含义（那时一个小节恰好声明一栏），读它是**回放**，不是把两种 wire 混成一种。
    """
    if "declared_aspect_ids" in spec:
        return (tuple(str(a).strip() for a in (spec.get("declared_aspect_ids") or ())
                      if str(a).strip()), False)
    legacy = str(spec.get("declared_aspect_id") or "").strip()
    return ((legacy,) if legacy else (), True)


def _check_sentence(*, text: str, citations: Sequence[str],
                    declared_aspect_ids: Sequence[str],
                    materials: Mapping[str, Mapping[str, Any]],
                    material_keys: frozenset[str], fact_keys: frozenset[str],
                    fact_texts: Sequence[str],
                    paragraph_aspect_ids: Sequence[str] = ()) -> tuple[SentenceFinding, ...]:
    """一句的机械发现。**只**判能在现有证据下判的那些轴。"""
    findings: list[SentenceFinding] = []
    declared_set = tuple(dict.fromkeys(str(a).strip() for a in declared_aspect_ids
                                       if str(a).strip()))
    paragraph_set = tuple(dict.fromkeys(str(a).strip() for a in paragraph_aspect_ids
                                        if str(a).strip()))

    # ---- 轴 1：引用存在与引用身份（与 `sc-6` 同名同义） -----------------------
    known = material_keys | fact_keys
    unknown = [c for c in citations if c not in known]
    if not citations:
        findings.append(SentenceFinding(
            check_kind="citation_present", verdict=VERDICT_HARD_ERROR,
            detail="这句话没有任何引用（`uncited_sentence`）：逐句引用是这条链的硬约束"))
    if unknown:
        findings.append(SentenceFinding(
            check_kind="citation_identity", verdict=VERDICT_HARD_ERROR,
            detail=(f"引用的键不在本次输入清单里 {unknown}（`citation_not_in_input`）："
                    f"清单里只有材料键 {sorted(material_keys)} 与事实键 {sorted(fact_keys)}"),
            surfaces=tuple(unknown)))

    cited_materials = [materials[c] for c in citations if c in material_keys]

    # ---- 轴 2：栏目归属（`aspect_attribution`，与 `sc-6` 同名同义） -----------
    #: `scp-5` 起判据是**集合有交集**（小节可以覆盖多个 Contract 栏目），不是单值等号。
    if declared_set and cited_materials:
        registered: list[str] = []
        for row in cited_materials:
            for aspect in row.get("aspect_ids") or ():
                if str(aspect) not in registered:
                    registered.append(str(aspect))
        if not (set(registered) & set(declared_set)):
            findings.append(SentenceFinding(
                check_kind="aspect_attribution", verdict=VERDICT_HARD_ERROR,
                detail=(f"本句所引来源的**登记栏目** {registered} 与本小节声明的 "
                        f"{list(declared_set)} **一个都不相交**"
                        "（`sentence_aspect_not_registered`）"),
                surfaces=tuple(registered)))
        elif paragraph_set and not (set(registered) & set(paragraph_set)):
            #: 同一条轴上的**第二个条件**（与生产侧 `sections/sentence_check.py` 逐字同判）：
            #: 小节这一级过了，还要看**本段自己**声明服务的栏目在不在所引来源的登记里。
            #: 只看小节那一边，宽小节里「拿 A 栏的材料去写 B 栏」这一段就会被放过——
            #: 小节声明覆盖了 A，交集非空，判据通过，而这一段其实一栏都没答上。
            #: 空 `paragraph_set`（本段没声明服务哪一栏）**不**在此放宽判据：那一种情形
            #: 由覆盖账回答（「没有任何段落声明服务的栏目 = 漏答」），不在这里假装判过。
            findings.append(SentenceFinding(
                check_kind="aspect_attribution", verdict=VERDICT_HARD_ERROR,
                detail=(f"本句所引来源的**登记栏目** {registered} 与本**段**声明服务的 "
                        f"{list(paragraph_set)} **一个都不相交**"
                        "（`sentence_aspect_not_registered`）；本小节声明的 "
                        f"{list(declared_set)} 里有交集那一栏不是这一段在答的"),
                surfaces=tuple(registered)))

    # ---- 轴 3：数字表面与资格（`numeric_surface` / `numeric_qualification`） --
    tokens = tuple(NS.scan_numeric_tokens(text))
    if tokens:
        material_texts = [str(row.get("text", "") or "") for row in cited_materials]
        pool = material_texts + [str(f.get("text", "") or "")
                                 for f in (materials[c] for c in citations if c in fact_keys)]
        allowed = NS.authorized_numeric_tokens(pool)
        unsourced = tuple(t for t in tokens
                          if not NS.numeric_token_authorized(t, allowed))
        #: `sc-7` 起：`NS._literal_token` **删除**数字与单位之间的排版空白，于是
        #: 「来源 `24 家` / 正文 `24家`」这一类**严格排版空白等价**被并入判据，**不再**是硬错。
        #
        #: 放宽的**只有空白**：数值、符号、单位、尾零一个都没派生（`-5`≠`5`，`1.6`≠`1.60`，
        #: `129,641,258千`≠`129,641,258千元`——后者差的是**单位**不是空格）。数值权威规则
        #: 完全保留：去空白后仍找不到的数字照样落 `unsourced` 硬错。
        #:
        #: 下面这条**非失败**记录只做一件事：把「本句哪几串数字是靠排版空白等价才被接受」
        #: 点出来，好让读者知道这一串表面与来源**不是逐字节相同**。它不吃掉任何硬错。
        #: 比的是**同一把尺子**：来源原文也过一遍 `NS.scan_numeric_tokens`（本链自己的扫描器），
        #: 再看「去空白后是不是同一个 token、而原样不同」；子串包含会把「少一个单位」也算成
        #: 排版差异，扫描器对扫描器之下这类误判在定义上不成立。
        source_tokens = [t for text_ in pool for t in NS.scan_numeric_tokens(text_)]
        raw_sources = {t for t in source_tokens if t}
        relaxed_sources = {_strip_ws(t) for t in source_tokens if t}
        if unsourced:
            findings.append(SentenceFinding(
                check_kind="numeric_surface", verdict=VERDICT_HARD_ERROR,
                detail=("这些数字表面在被引用的来源里**找不到**（判据为 `sc-7` 的 "
                        "`NS._literal_token`：删千分位与**全部空白**，不改数值/符号/单位/尾零）："
                        "来源里没有的数字不得写进正文。**严格排版空白等价已经并入判据**——"
                        "去空白后能找到的不在这一条里；仍出现在这里的，差的不只是空格"),
                surfaces=unsourced))
        spacing_equivalent = tuple(
            t for t in tokens if t not in raw_sources and _strip_ws(t) in relaxed_sources)
        if spacing_equivalent:
            findings.append(SentenceFinding(
                check_kind=CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT,
                verdict=VERDICT_CHECKABLE,
                detail=("这几串数字与被引来源**逐字**差异只在「数字与单位之间」的**排版空白**上"
                        "（如正文 `24家`、来源 `24 家`）：`sc-7` 起这一类**严格排版空白等价**"
                        "已并入 `NS._literal_token`，因此**不**记硬错。"
                        "数值、符号、「超」、单位与数字权威规则**一条都没有放宽**——"
                        "去空白后仍找不到的数字照样落上面那条硬错"),
                surfaces=spacing_equivalent))
        qualified_only = [t for t in tokens if SC.is_qualified_numeric_surface(t, text)]
        #: 金额 / 比率只在**普通材料**原文里逐字出现**不**构成授权（`sc-5` 的
        #: `numeric_basis_not_qualified`）。这里比「本句有没有引合格事实」更细一层：**逐 token**
        #: 看它有没有落在本句所引的某条权威事实的文本里——整句有引事实，不等于这一笔金额就被那条
        #: 事实授权。含义是否相符、期间/口径是否对得上属**未判轴**（需要清单侧的身份），本轴只判
        #: 「这串字符有没有出现在本句所引的权威事实文本里」。
        cited_fact_texts = [str(materials[c].get("text", "") or "")
                            for c in citations if c in fact_keys]
        fact_covered = NS.authorized_numeric_tokens(cited_fact_texts)
        unbacked = [t for t in qualified_only
                    if not NS.numeric_token_authorized(t, fact_covered)]
        if unbacked:
            findings.append(SentenceFinding(
                check_kind="numeric_qualification", verdict=VERDICT_HARD_ERROR,
                detail=("这些金额 / 比率没有落在**本句所引的任何一条权威事实**的文本里"
                        f"（本句引了 {len(cited_fact_texts)} 条权威事实；请求面 `authority_facts` "
                        f"共 {len(fact_texts)} 条），也没有落到所引表材料的某一格："
                        "只在被引普通材料的原文里逐字出现**不**构成授权"
                        "（`numeric_basis_not_qualified`）"),
                surfaces=tuple(unbacked)))

    # ---- 轴 4：含糊期间措辞（**不是** `sc-5` 的 `period_surface`） ------------
    vague = NS.vague_period_hits(text)
    if vague:
        findings.append(SentenceFinding(
            check_kind="vague_period_wording", verdict=VERDICT_NEEDS_REVIEW,
            detail=("本句用了含糊期间措辞，读者面指不出到底是哪个期间；"
                    "`sc-5` 的 `period_surface` 明确授权「报告期」这一报告自身的期间概念，"
                    "因此这里**不**判硬错——期间能否对上具体年度属语义审阅"),
            surfaces=tuple(vague)))

    # ---- 轴 5：发行人自述的优势判断（词表命中，只标待审） --------------------
    hits = tuple(m for m in EVALUATIVE_MARKERS if m in text)
    if hits:
        findings.append(SentenceFinding(
            check_kind="issuer_self_evaluation", verdict=VERDICT_NEEDS_REVIEW,
            detail=("本句含发行人自述的评价性措辞：它们是**发行人自己的判断**，"
                    "引用只证明「发行人这么说过」，不证明「事情就是这样」"),
            surfaces=hits))

    return tuple(findings)


#: 人工判断的**封闭**归类。`judgment` 必须是其中之一：自由文本的归类会立刻长成第二套口径，
#: 而这一栏要的是「人看出了哪一类问题」，不是「人怎么措辞」。
ANALYST_JUDGMENTS = ("栏目错配", "凑数", "来源断言越界", "重复叙述", "其他")

_ANALYST_NOTE_FIELDS = ("subject", "judgment", "sentence_ids", "note", "evidence", "boundary")


def _clean_analyst_note(note: Mapping[str, Any]) -> dict:
    """人工判断条目的**校验**（缺字段 / 多字段 / 归类不在集合里都拒绝，不静默吞）。"""
    if not isinstance(note, Mapping):
        raise ValueError(f"analyst note 必须是对象，得到 {type(note).__name__}")
    unknown = sorted(set(note) - set(_ANALYST_NOTE_FIELDS))
    if unknown:
        raise ValueError(f"analyst note 含未登记字段 {unknown}（允许 {list(_ANALYST_NOTE_FIELDS)}）")
    subject = str(note.get("subject", "") or "").strip()
    if not subject:
        raise ValueError("analyst note 缺 `subject`（这条判断说的是哪个小节/哪句话）")
    judgment = str(note.get("judgment", "") or "").strip()
    if judgment not in ANALYST_JUDGMENTS:
        raise ValueError(f"analyst note 的 `judgment` 必须是 {list(ANALYST_JUDGMENTS)} 之一，"
                         f"得到 {judgment!r}")
    ids = note.get("sentence_ids") or ()
    if isinstance(ids, str) or not isinstance(ids, (list, tuple)):
        raise ValueError("analyst note 的 `sentence_ids` 必须是字符串数组")
    return {"subject": subject, "judgment": judgment,
            "sentence_ids": [str(i) for i in ids],
            "note": str(note.get("note", "") or ""),
            "evidence": str(note.get("evidence", "") or ""),
            "boundary": str(note.get("boundary", "") or "")}


def _face_source_rows(request_face: Mapping[str, Any],
                      ) -> tuple[tuple[str, frozenset[str]], ...]:
    """请求面 → **来源行**：`(引用键, 该行登记到的栏目集)`，按请求面顺序、材料在前事实在后。

    与生产侧 `cited_writer.registered_source_keys` 回答的是**同一个问题**（这一栏本次有没有
    来源），差别只在输入：链上读精确清单，这里只能读请求面自带的那两份行表（`materials[]` /
    `authority_facts[]`，两处都带 `aspect_ids`）。请求面正是由那份清单生成的，所以这不是第二份
    口径，是同一份口径在**没有清单**时的可判子集——清单里那批不在请求面上的身份字段
    （`pack_id`、`provenance_identity`、`reading_view_fingerprint`…）本来也不参与这个计数。
    """
    rows: list[tuple[str, frozenset[str]]] = []
    for row in (list(request_face.get("materials") or ())
                + list(request_face.get("authority_facts") or ())):
        aspects = frozenset(str(a) for a in (row.get("aspect_ids") or ()) if str(a).strip())
        rows.append((str(row.get("key", "") or ""), aspects))
    return tuple(rows)


def _face_spec(spec: Mapping[str, Any]) -> Any:
    """请求面的一段小节行 → 链自己的 `CitedSubsectionSpec`；**建不起来就返回 `None`**。

    fail-soft 是刻意的：本模块只能读请求面，历史上/夹具里的面可能缺字段（例如没有 `title`）。
    那种面**不**能被拼成一个「像规格的东西」去凑判定——返回 `None`，调用方退回整节口径并把
    「判不出来」写进产物。方向因此是保守的：少认一处，只会让结论更宽，不会凭空收窄。
    """
    from sections import cited_writer as CW

    declared, _legacy = _declared_aspects_of(spec)
    try:
        return CW.CitedSubsectionSpec(
            subsection_id=str(spec.get("subsection_id", "") or ""),
            title=str(spec.get("title", "") or ""),
            requirement_text=str(spec.get("requirement_text", "") or ""),
            declared_aspect_ids=tuple(str(a) for a in declared),
            allowed_source_classes=tuple(
                str(c) for c in (spec.get("allowed_source_classes") or ())))
    except Exception:                                     # noqa: BLE001
        return None


def _face_column(specs: Mapping[str, Any], subsection_id: str,
                 requirement_text: str) -> str | None:
    """这条缺口的要求文本**逐字**落到哪一栏；判据是链上的 `aspect_for_requirement`。

    规格建不起来、或文本对不上任何一行 → `None`（调用方退回整节口径并把 `column` 记成 `null`）。
    """
    from sections import cited_writer as CW

    spec = specs.get(subsection_id)
    if spec is None:
        return None
    return CW.aspect_for_requirement(spec, requirement_text)


def _empty_shell_context_for_face(request_face: Mapping[str, Any],
                                  gaps: Sequence[Mapping[str, Any]]) -> Any:
    """请求面 → `crn-4` 第三条规则要的四项读数（`EmptyShellContext`）。**只读请求面。**

    * 登记数：逐栏数 :func:`_face_source_rows`，与生产侧同一根轴（材料行 ∪ 事实行）。
    * 「已声明无来源」：逐条缺口用 `aspect_for_requirement` 定栏、再用 `assign_gap_reason`
      取**系统判定**——模型自述的那个字不作数（自称 `no_source_in_manifest` 而该栏有来源的，
      会被改判成 `source_present_but_not_admissible`，也就不进这个集合）。落不到栏上的缺口
      **不**充当任何一栏的证据。
    * 「本小节归属」：逐小节照抄 `request_face["subsections"]` 自己声明的 `declared_aspect_ids`。
      **只由请求面本身建**，不经过 `_face_spec`——规格建不起来只是因为 `title` 缺失，
      那是别的事，不该顺手把归属记成「查不到」（「查不到」在这条规则里等于不许删，
      方向与另两项相反，见 `CRN.EmptyShellContext` 的类注释）。
      生产侧 `cited_writer.empty_shell_context` 的同一张表建自**本次返回真的写了的小节**；
      这里建自**请求面列出的全部小节**。对任何一个**真实存在**的段落，这两种口径给出同一个
      答案（段落只可能长在它自己那一小节里），差别只在「这一小节本次一段都没写」——
      那种小节不可能承载段落，也就不进判定。请求面**没有列出**的小节一律不登记：
      它与「查不到」同形，一并照「没证明就不许删」处理，不必也不该替它补一条。

    * 「缺口支持」（`crn-4` 新增）：逐条缺口按上面同一套栏位定栏、取**系统判定**理由，再交给
      `cited_writer.unique_subsection_gap_reasons` 定「同一小节内**可唯一对应**」的那一条。
      与另三项不同，这一项**多一条就多删一段**，因此口径必须与生产侧逐字相同——共用同一个函数
      是唯一的办法。

    四项都只回答请求面自己说了什么。交不出读数时调用方应当**不传**上下文（那时
    `crn-4` 的第三条规则不评估，行为与 `crn-1` 逐字相同）——本函数不猜。
    """
    from sections import cited_writer as CW

    rows = _face_source_rows(request_face)
    subsections = [s for s in (request_face.get("subsections") or ())]
    specs = {str(s.get("subsection_id", "") or ""): _face_spec(s) for s in subsections}
    counts: dict[str, int] = {}
    declared_by_subsection: dict[str, frozenset[str]] = {}
    for sub in subsections:
        declared = _declared_aspects_of(sub)[0]
        declared_by_subsection[str(sub.get("subsection_id", "") or "")] = frozenset(
            str(a) for a in declared)
        for aspect in declared:
            counts.setdefault(str(aspect), 0)
    for aspect in counts:
        counts[aspect] = sum(1 for _key, aspects in rows if aspect in aspects)

    unsourced: set[str] = set()
    gap_rows: list[tuple[str, str, str]] = []
    for gap in gaps:
        subsection_id = str(gap.get("subsection_id", "") or "")
        column = _face_column(specs, subsection_id,
                              str(gap.get("requirement_text", "") or ""))
        if not column:
            continue
        reason, _assignment = CW.assign_gap_reason(
            claimed_reason=str(gap.get("reason", "") or ""), spec=specs.get(subsection_id),
            registered_count=counts.get(column, 0))
        if reason == "no_source_in_manifest":
            unsourced.add(column)
        #: 第四项读数（`crn-4`）：逐条缺口的三元组，交给**同一个**
        #: :func:`sections.cited_writer.unique_subsection_gap_reasons` 定「可唯一对应」。
        #: 离线侧若漏了这一项，同一串字节会判出与生产侧**相反**的 `empty_shell_rule`
        #: ——生产删、离线留，或反过来。
        gap_rows.append((subsection_id, column, reason))
    return CRN.EmptyShellContext(
        registered_source_counts=counts,
        no_source_gap_aspects=frozenset(unsourced),
        subsection_aspect_ids=declared_by_subsection,
        subsection_gap_reason=CW.unique_subsection_gap_reasons(gap_rows))


def _gap_context(request_face: Mapping[str, Any],
                 gaps: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """逐条缺口对账：**它说的「没有」是「没有来源」还是「来源在但不合格」？**

    请求面自带 `subsections[].requirement_text`（逐行）与 `declared_aspect_ids`（逐位）的
    **位置对位**，所以「**这条缺口所属的那一栏**在本轮输入里到底有没有来源」是**能判**的：
    判据逐字来自生产侧 `cited_writer.aspect_for_requirement`（与 `assign_gap_reason` 的改判
    同一条）。一条缺口若自称 `no_source_in_manifest`、而该栏登记着来源，那它把「缺合格数字
    事实」记成了「没有来源」——两句话对下游的含义完全不同：前者要补事实，后者会被读成
    「这一节本来就没什么可写」，于是该去检索的变成了去补资格。这里只**标出**，不改缺口一个字。

    对不上栏位（行数与栏数不等、或这条文本不逐字等于其中一行）时退回**整节声明**的较宽口径，
    并把 `column` 记成 `null`：判不出来的那一条不得被读成「判过了，就是那一栏」。
    """
    rows_in = _face_source_rows(request_face)
    declared_by_sub = {str(s.get("subsection_id", "") or ""): _declared_aspects_of(s)[0]
                       for s in (request_face.get("subsections") or ())}
    specs = {str(s.get("subsection_id", "") or ""): _face_spec(s)
             for s in (request_face.get("subsections") or ())}
    rows: list[Mapping[str, Any]] = []
    for gap in gaps:
        subsection_id = str(gap.get("subsection_id", "") or "")
        declared = declared_by_sub.get(subsection_id, ())
        requirement_text = str(gap.get("requirement_text", "") or "")
        column = _face_column(specs, subsection_id, requirement_text)
        scope = frozenset((column,)) if column else frozenset(str(a) for a in declared)
        registered = [key for key, aspects in rows_in if aspects & scope]
        reason = str(gap.get("reason", "") or "")
        rows.append({
            "subsection_id": subsection_id,
            "requirement_text": requirement_text,
            #: 这一条缺口落到的那一栏；`null` = 对不上（那时上面用的是整节口径）。
            "column": column,
            "scope_aspect_ids": sorted(scope),
            "reason": reason,
            "declared_aspect_ids": list(declared),
            "materials_registered_for_aspect": len(registered),
            "material_keys_registered_for_aspect": registered,
            "claims_no_source_but_materials_exist": bool(
                reason == "no_source_in_manifest" and registered),
        })
    return tuple(rows)


#: 补件「期望来源类别」的**封闭**判定（逐条给出，不改原稿一个字）。
#:
#: 这一栏存在的理由：`expected_source_class` 会被下游读成**取材指令**，写错一个类别，等于把
#: 下一次检索直接指挥到错误方向。而它到底对不对，**有一部分是能判的**——可检索来源类是一个
#: 封闭词表（`harness.topic_schema.SOURCE_CLASSES`），Contract 允许的来源类又已经在请求面上
#: （`cwm-5` 起）。判不了的那部分（「这一栏该不该有证据要求」「这几类里哪一类足以结案」）
#: 留给人工与语义审阅。
#:
#: 自述值在封闭词表内、且在该栏 Contract 允许的来源类里。
SOURCE_CLASS_CONSISTENT = "consistent"
#: 自述值在封闭词表内，但该栏 Contract 允许的那几类里不含它。
SOURCE_CLASS_OUTSIDE_CONTRACT = "outside_contract_allowed_classes"
#: 自述值在封闭词表内，而该轮请求面**没有** Contract 允许来源这一轴（`cwm-4` 及更早）。
#: 只能判到词表这一层——但那已经足够判出「它不是来源类」。
SOURCE_CLASS_VOCABULARY_ONLY = "consistent_vocabulary_only"
#: 自述值**不是**一个可检索来源类。`financial_pack` 这类取值落在这里：它是**权威类型**
#: （`narrative_schema.AUTHORITY_KINDS`），回答的是「这份事实由谁背书」，而不是「去哪里
#: 检索」。把权威当成来源，补件真要执行时无处可去。
SOURCE_CLASS_NOT_A_SOURCE_CLASS = "not_a_source_class"
#: 这一条根本没写来源类（`cp-3` 起是**正常**情形：来源类由系统按 Contract 推导）。
SOURCE_CLASS_NOT_DECLARED = "not_declared"

SOURCE_CLASS_VERDICTS = (SOURCE_CLASS_CONSISTENT, SOURCE_CLASS_OUTSIDE_CONTRACT,
                         SOURCE_CLASS_VOCABULARY_ONLY, SOURCE_CLASS_NOT_A_SOURCE_CLASS,
                         SOURCE_CLASS_NOT_DECLARED)


def _source_class_context(
        request_face: Mapping[str, Any],
        needs: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """逐条补件对账：**它说的「期望来源类别」是不是一个来源类别？**

    两件事**能判**，都不需要清单、不需要联网：

    * 可检索来源类是**封闭词表**（`TS.SOURCE_CLASSES`），所以「这个取值根本不是一个来源类」
      当场可判——`financial_pack` 正是这一类：它是**权威类型**，回答「这份事实由谁背书」，
      不是「去哪里检索」；
    * `cwm-5` 起，请求面的小节行带着该栏 Contract 允许的来源类，所以「这个取值在不在该栏
      允许的那几类里」也可判。

    判不了的（该栏该不该有证据要求、这几类里哪一类足以结案）本页**不判**，只把取值摆出来。
    `cp-3` 起生成器不再写这个字段，因此新轮的正常读数是 `not_declared`——那是**结论**，
    不是缺字段。
    """
    specs = {str(s.get("subsection_id", "") or ""): s
             for s in (request_face.get("subsections") or ())}
    rows: list[Mapping[str, Any]] = []
    for need in needs:
        subsection_id = str(need.get("subsection_id", "") or "")
        spec = specs.get(subsection_id) or {}
        allowed_raw = spec.get("allowed_source_classes")
        allowed = tuple(str(c) for c in allowed_raw) if isinstance(allowed_raw, (list, tuple)) \
            else None
        claimed = str(need.get("expected_source_class", "") or "").strip()
        if not claimed:
            verdict = SOURCE_CLASS_NOT_DECLARED
        elif claimed not in TS.SOURCE_CLASSES:
            verdict = SOURCE_CLASS_NOT_A_SOURCE_CLASS
        elif allowed is None:
            verdict = SOURCE_CLASS_VOCABULARY_ONLY
        elif claimed in allowed:
            verdict = SOURCE_CLASS_CONSISTENT
        else:
            verdict = SOURCE_CLASS_OUTSIDE_CONTRACT
        rows.append({
            "subsection_id": subsection_id,
            "aspect_id": str(need.get("aspect_id", "") or ""),
            "writer_claimed_source_class": claimed,
            "is_authority_kind": claimed in NS.AUTHORITY_KINDS,
            "contract_allowed_source_classes": (list(allowed) if allowed is not None else None),
            "verdict": verdict,
        })
    return tuple(rows)


#: 缺口**应当**改成的口径（本次给读稿人的模板句；不是自动改写——本模块不改缺口一个字）。
GAP_REWRITE_PRESCRIPTION = (
    "本次 Writer 输入缺少对应栏目的合格数值事实，暂不能撰写构成及占比，需补充合格事实。")

#: 缺口措辞的两条**禁止**（逐条写在页面上，免得下一次又写回去）。
GAP_REWRITE_PROHIBITIONS = (
    "不得把「缺合格数字事实」记成「没有材料」——两者对下游的含义不同：前者要补事实，"
    "后者会被读成「这一节本来就没什么可写」。",
    "不得说原 PDF 里不存在该内容——Writer 只见过本次输入，没有见过原文件，"
    "本页也无权替原文件下任何结论。",
)


def _cross_subsection_duplicates(payload: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """跨小节的**逐字**重复叙事：同一串文本出现在两个以上小节里。"""
    places: dict[str, list[str]] = {}
    for sub in payload.get("subsections") or ():
        subsection_id = str(sub.get("subsection_id", "") or "")
        for para in sub.get("paragraphs") or ():
            for sentence in para.get("sentences") or ():
                key = _norm_ws(sentence.get("text"))
                if key:
                    places.setdefault(key, [])
                    if subsection_id not in places[key]:
                        places[key].append(subsection_id)
    return {text: tuple(subs) for text, subs in places.items() if len(subs) > 1}


#: 近重复的三种形态：`near` 是换说法；`contained` / `contains` 是包含关系的**两个方向**
#: （短句整个落在另一小节那句里 / 长句把另一小节那句整个包住）。方向必须分写：把「被包含」
#: 印成「包含」，读者会拿着这句话去改错那一头。
NEAR_KIND_NEAR = "near"
NEAR_KIND_CONTAINED = "contained"
NEAR_KIND_CONTAINS = "contains"


def _cross_subsection_near_duplicates(
        payload: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """跨小节的**近**重复：同一段叙述换个说法（或整段）再去填另一个栏目。

    两种形态各有一条判据，都与公司、栏目名、答案关键词无关：

    * `contained`——一句的文本整个出现在另一小节的某句里（**无阈值**，这一条是确定的）；
    * `near`——字符级相似度 ≥ :data:`NEAR_DUPLICATE_RATIO`（一条明写的阈值，用于捕捉
      「同一段话换个说法」这种没有包含关系的情况）。

    它**不**判合格与否：只把「同一段话被拿来填两个栏目」摆出来，交语义审阅。
    """
    entries: list[tuple[str, str]] = []
    for sub in payload.get("subsections") or ():
        subsection_id = str(sub.get("subsection_id", "") or "")
        for para in sub.get("paragraphs") or ():
            for sentence in para.get("sentences") or ():
                text = _norm_ws(sentence.get("text"))
                if len(text) >= NEAR_DUPLICATE_MIN_CHARS:
                    entries.append((subsection_id, text))
    near: dict[str, list[Mapping[str, Any]]] = {}

    def note(text: str, other_sub: str, other_text: str, kind: str, ratio: float) -> None:
        near.setdefault(text, []).append(
            {"subsection_id": other_sub, "kind": kind, "ratio": round(ratio, 3),
             "other_text": other_text})

    for index, (sub_a, text_a) in enumerate(entries):
        for sub_b, text_b in entries[index + 1:]:
            if sub_a == sub_b or text_a == text_b:
                continue
            ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
            if text_a in text_b:
                note(text_a, sub_b, text_b, NEAR_KIND_CONTAINED, ratio)
                note(text_b, sub_a, text_a, NEAR_KIND_CONTAINS, ratio)
            elif text_b in text_a:
                note(text_b, sub_a, text_a, NEAR_KIND_CONTAINED, ratio)
                note(text_a, sub_b, text_b, NEAR_KIND_CONTAINS, ratio)
            elif ratio >= NEAR_DUPLICATE_RATIO:
                note(text_a, sub_b, text_b, NEAR_KIND_NEAR, ratio)
                note(text_b, sub_a, text_a, NEAR_KIND_NEAR, ratio)
    return {text: tuple(rows) for text, rows in near.items()}


def diagnose(*, reply_text: Any, request_face: Any, call: Mapping[str, Any] | None = None,
             analyst_notes: Sequence[Mapping[str, Any]] = (),
             ) -> ReplyDiagnosis:
    """留存回复 + 请求面 → 只读诊断。**不联网、不调模型、不写盘。**

    `analyst_notes` 是**人**的判断（读稿人自己看出来的栏目错配、凑数等），原样带进产物并
    **明确标注**为人工判断：它不是机械判据，本模块也不据此改任何东西。
    """
    request_face = dict(request_face or {})
    raw_reply = str(reply_text if reply_text is not None else "")
    payload = json.loads(raw_reply)
    original_subsections = tuple(json.loads(json.dumps(s, ensure_ascii=False))
                                 for s in (payload.get("subsections") or ()))
    normalized = CRN.normalize_cited_reply(
        payload, empty_shell=_empty_shell_context_for_face(
            request_face, payload.get("gaps") or ()))

    materials = _material_index(request_face)
    material_keys = frozenset(str(r.get("key", "") or "")
                              for r in (request_face.get("materials") or ()))
    fact_keys = frozenset(str(r.get("key", "") or "")
                          for r in (request_face.get("authority_facts") or ()))
    fact_texts = _fact_texts(request_face)

    specs = {str(s.get("subsection_id", "") or ""): s
             for s in (request_face.get("subsections") or ())}
    duplicates = _cross_subsection_duplicates(normalized.payload)
    near_duplicates = _cross_subsection_near_duplicates(normalized.payload)

    model_id_by_final: dict[str, str] = {
        a.final_id: a.model_id for a in normalized.sentence_ids}

    subsections: list[SubsectionReading] = []
    for sub in normalized.payload.get("subsections") or ():
        subsection_id = str(sub.get("subsection_id", "") or "")
        spec = specs.get(subsection_id, {})
        declared, _legacy = _declared_aspects_of(spec)
        rows: list[SentenceReading] = []
        for para in sub.get("paragraphs") or ():
            paragraph_aspects = tuple(str(a).strip() for a in (para.get("aspect_ids") or ())
                                      if str(a).strip())
            for sentence in para.get("sentences") or ():
                final_id = str(sentence.get("sentence_id", "") or "")
                text = str(sentence.get("text", "") or "")
                citations = tuple(str(c) for c in (sentence.get("citations") or ()))
                findings = list(_check_sentence(
                    text=text, citations=citations, declared_aspect_ids=declared,
                    paragraph_aspect_ids=paragraph_aspects,
                    materials=materials, material_keys=material_keys, fact_keys=fact_keys,
                    fact_texts=fact_texts))
                key = _norm_ws(text)
                others = duplicates.get(key)
                if others:
                    findings.append(SentenceFinding(
                        check_kind="cross_subsection_duplicate",
                        verdict=VERDICT_NEEDS_REVIEW,
                        detail=(f"同一串文本在 {list(others)} 这几个小节里逐字重复出现："
                                "同一段叙述被拿来填两个不同的栏目，读者会以为它们各说了一件事"),
                        surfaces=tuple(others)))
                near = near_duplicates.get(key)
                if near:
                    where = sorted({str(row["subsection_id"]) for row in near})

                    def _subs(kind: str) -> list[str]:
                        return sorted({str(r["subsection_id"]) for r in near if r["kind"] == kind})

                    contained, contains = _subs(NEAR_KIND_CONTAINED), _subs(NEAR_KIND_CONTAINS)
                    if contained:
                        how = (f"本句的文本**整个**出现在 {contained} 里的一句话中"
                               "（无阈值，是确定包含）")
                    elif contains:
                        how = (f"本句把 {contains} 里的一句话**整个**包住了"
                               "（无阈值，是确定包含）")
                    else:
                        how = (f"本句与 {where} 里的一句话**换了说法但基本是同一段叙述**"
                               f"（相似度 {max(row['ratio'] for row in near) or 0}）")
                    findings.append(SentenceFinding(
                        check_kind="cross_subsection_near_duplicate",
                        verdict=VERDICT_NEEDS_REVIEW,
                        detail=(f"{how}：同一件事被写进两个栏目，须语义审阅确认这两个栏目是不是"
                                "本来就该说同一件事"),
                        surfaces=tuple(where)))
                rows.append(SentenceReading(
                    final_id=final_id, model_id=model_id_by_final.get(final_id, ""),
                    subsection_id=subsection_id, declared_aspect_ids=declared,
                    text=text, citations=citations, findings=tuple(findings),
                    paragraph_aspect_ids=paragraph_aspects))
        subsection_gaps = tuple(dict(g) for g in (normalized.payload.get("gaps") or ())
                                if str(g.get("subsection_id", "") or "") == subsection_id)
        subsections.append(SubsectionReading(
            subsection_id=subsection_id, title=str(sub.get("title", "") or ""),
            declared_aspect_ids=declared,
            requirement_text=str(spec.get("requirement_text", "") or ""),
            readings=tuple(rows), gaps=subsection_gaps))

    asked = [str(s.get("subsection_id", "") or "") for s in (request_face.get("subsections") or ())]
    got = [s.subsection_id for s in subsections]
    #: 这一份请求面用的是哪一代字段：`cwm-6` 起是集合键 `declared_aspect_ids`，更早是单数键
    #: `declared_aspect_id`。逐小节读出来的是**同一个问题**，但读者要知道这次判的是哪一份 wire
    #: ——否则「历史面按旧键判过」与「新面按集合键判过」会看起来一模一样。
    face_fields = {_declared_aspects_of(s)[1] for s in (request_face.get("subsections") or ())}
    if not face_fields:
        declared_field = "no_subsections"
    elif face_fields == {False}:
        declared_field = "declared_aspect_ids"
    elif face_fields == {True}:
        declared_field = "declared_aspect_id"
    else:
        declared_field = "mixed"
    coverage = {"subsection_ids_asked": asked, "subsection_ids_returned": got,
                "exact_match": sorted(asked) == sorted(got),
                #: 集合相等与**同序**是两条不同的等式。`same_order` 之前没算过——于是
                #: 「顺序也对上了」这句话在本页上曾经**没有证据**，只是没写反而已。
                "same_order": asked == got,
                "order_mismatch_at": next(
                    (i + 1 for i, (a, b) in enumerate(zip(asked, got)) if a != b), None),
                "missing": sorted(set(asked) - set(got)),
                "extra": sorted(set(got) - set(asked)),
                "note": ("小节集合逐条相等、**且顺序相同**这两条**都能判**（请求面自带小节清单），"
                         "与 `_check_subsection_coverage` 同一判据。"),
                "declared_aspect_field": declared_field}

    axes = _axes(request_face)
    return ReplyDiagnosis(
        reply_sha256=NS.body_fingerprint_of(raw_reply),
        request_face_sha256=NS.body_fingerprint_of(json.dumps(request_face, ensure_ascii=False,
                                                              sort_keys=True)),
        call=dict(call or {}), subsections=tuple(subsections),
        original_subsections=original_subsections,
        gaps=tuple(dict(g) for g in (normalized.payload.get("gaps") or ())),
        follow_up_needs=tuple(dict(n) for n in (normalized.payload.get("follow_up_needs") or ())),
        normalization=normalized.to_dict(), coverage=coverage, axes=axes,
        citation_labels=_citation_labels(request_face),
        gap_context=_gap_context(request_face, normalized.payload.get("gaps") or ()),
        source_class_context=_source_class_context(
            request_face, normalized.payload.get("follow_up_needs") or ()),
        analyst_notes=tuple(_clean_analyst_note(n) for n in (analyst_notes or ())))


def _axes(request_face: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    """逐轴说清「判了没有」。**未判的轴必须出现**，不能靠不写来省略。"""
    facts = request_face.get("authority_facts") or ()
    def axis(name: str, status: str, why: str = "") -> dict[str, str]:
        return {"axis": name, "status": status, "basis": why}
    return (
        axis("citation_present", AXIS_ADJUDICATED, "请求面自带材料与事实键"),
        axis("citation_identity", AXIS_ADJUDICATED, "请求面自带材料与事实键"),
        axis("aspect_attribution", AXIS_ADJUDICATED,
             "请求面自带 `materials[].aspect_ids` × `subsections[].declared_aspect_ids`"),
        axis("numeric_surface", AXIS_ADJUDICATED,
             "被引来源的原文（请求面自带），判据为 `sc-7` 的 `NS._literal_token`"
             "（删千分位与**全部空白**，不改数值/符号/单位/尾零）。数字与单位之间的**严格排版"
             f"空白等价**已并入判据，靠它被接受的另有 `{CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT}`"
             " 单列（非失败）；去空白后仍找不到的数字照样硬错"),
        axis("numeric_qualification", AXIS_ADJUDICATED,
             "逐 token 比对「本句所引权威事实的文本」；请求面 `authority_facts` "
             f"{len(facts)} 条。**只**判这串字符在不在所引事实文本里，"
             "含义是否相符 / 期间口径是否对得上属未判轴"),
        axis("vague_period_wording", AXIS_ADJUDICATED, "`NS.vague_period_hits`"),
        axis("issuer_self_evaluation", AXIS_ADJUDICATED, "本模块的通用词表（只标待审）"),
        axis("citation_locator", AXIS_UNJUDGED, "需要清单侧的定位对象，请求面只有 `locator` 字段"),
        axis("template_text", AXIS_UNJUDGED, "需要清单侧的 `content_qualification`"),
        axis("current_state_scope", AXIS_UNJUDGED, "需要清单侧的 `document_id` 与来源角色映射"),
        axis("source_role", AXIS_UNJUDGED, "同上"),
        axis("table_cell_provenance", AXIS_UNJUDGED, "需要清单侧的 `structured_view`"),
        axis("table_row_label", AXIS_UNJUDGED, "同上"),
        axis("table_column_header", AXIS_UNJUDGED, "同上"),
        axis("table_declaration", AXIS_UNJUDGED, "同上"),
        axis("presentation_column_attribution", AXIS_UNJUDGED, "需要清单侧的呈现层路由声明"),
        axis("material_adoption", AXIS_UNJUDGED,
             "`MaterialAdoptionRecord` 绑清单身份；本轮清单未留存"),
        axis("report_version_binding", AXIS_UNJUDGED, "同上"),
        axis("independent_review", AXIS_UNJUDGED, "本轮没有独立审阅调用"),
    )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

_BANNER = (
    "> ## ⚠ 真实模型门前原稿／未经硬核对、独立审阅／**不可发布**\n"
    ">\n"
    "> 本页逐字来自真实 Writer 的返回（call_id 见下），**一个字都没有改**。它**不是**成品正文：\n"
    "> 没有经过 `sc-5` 完整逐句硬核对，没有经过独立语义审阅，没有经过人工接受。\n"
    "> 下面的「派生诊断」是在**现有证据**下能做的核对与结构归一，**不是**「该轮通过」。\n"
)


def _reading_by_position(diagnosis: ReplyDiagnosis) -> dict[tuple[str, int, int, int], SentenceReading]:
    """`(小节, 小节序号, 段序号, 句序号)` → 该句的诊断读数。

    坐标来自**归一化台账**（`crn-1` 为每句记下的结构位置），不是靠文本反查：同一句文本可能在
    全节出现两次，靠文本匹配会把读数挂到错的那一句上。
    """
    readings = {r.final_id: r for r in diagnosis.sentences()}
    index: dict[tuple[str, int, int, int], SentenceReading] = {}
    for row in diagnosis.normalization.get("sentence_ids") or ():
        reading = readings.get(str(row.get("final_id", "") or ""))
        if reading is None:
            continue
        index[(str(row.get("subsection_id", "") or ""), int(row.get("subsection_index") or 0),
               int(row.get("paragraph_index") or 0), int(row.get("sentence_index") or 0))] = reading
    return index


def _sentence_block(reading: SentenceReading | None, *, text: str,
                    citations: Sequence[Any], model_id: str,
                    labels: Mapping[str, str]) -> list[str]:
    """人读页里**一句**的完整交代：引用材料、所在栏目、硬错或待审原因。"""
    if reading is None:
        return [f"- `{model_id}`（未取到派生读数）｜{text}"]
    #: 徽章自带强调（`**硬错**`），这里**不**再套一层：`****硬错****` 在 Markdown 里只画出两个
    #: 字面星号，人读页上会变成「硬错」两边各挂两个 `*`。
    head = (f"- {_verdict_badge(reading.verdict)} `{reading.final_id}`"
            f"（模型原 ID `{model_id}`）｜{text}")
    lines = [head]
    if citations:
        for key in citations:
            label = labels.get(str(key), "（请求面里没有这个键）")
            lines.append(f"  - 引用 `{key}`：{label}")
    else:
        lines.append("  - 引用：（无）——逐句引用是这条链的硬约束")
    column = (" / ".join(f"`{a}`" for a in reading.declared_aspect_ids)
              or "（本小节未声明栏目）")
    lines.append(f"  - 所在小节声明覆盖的栏目：{column}")
    if reading.paragraph_aspect_ids:
        own = " / ".join(f"`{a}`" for a in reading.paragraph_aspect_ids)
        lines.append(f"  - **本段**声明服务的那一栏：{own}")
    else:
        lines.append("  - **本段**没有声明服务哪一栏——这一栏有没有人写，要看覆盖账，"
                     "不能由这一句读成「写过了」")
    for finding in reading.findings:
        surfaces = f"｜表面 {list(finding.surfaces)}" if finding.surfaces else ""
        lines.append(f"  - {_verdict_badge(finding.verdict)} `{finding.check_kind}`："
                     f"{finding.detail}{surfaces}")
    return lines


def render_original_markdown(diagnosis: ReplyDiagnosis) -> str:
    """原稿人读页：逐小节、逐句列出**引用材料、所在栏目、硬错或待审原因**。

    页面顺序与原稿一致（同一份 `original_subsections`），读数按结构坐标挂上去——**不重排**：
    人读页要能跟着原稿往下读，而不是先按结论排序再回头找原句。
    """
    call = dict(diagnosis.call)
    summary = diagnosis.adjudicable_summary()
    lines = ["# 真实 Writer 返回｜原稿人读页", "",
             _BANNER, "",
             f"**可判轴头条**：{summary['headline']}。{summary['not_a_formal_check']}", ""]
    if call:
        lines += ["| 项 | 值 |", "|---|---|",
                  f"| call_id | `{call.get('call_id', '')}` |",
                  f"| 模型 | `{call.get('model', '')}` |",
                  f"| prompt | `{call.get('prompt_version', '')}` |",
                  f"| 状态 | `{call.get('status', '')}` |",
                  f"| finish_reason | `{call.get('finish_reason', '')}` |",
                  f"| 输入/输出 token | {call.get('input_tokens')} / {call.get('output_tokens')} |",
                  f"| 延迟(ms) | {call.get('latency_ms')} |",
                  f"| 可见正文字符 | {call.get('visible_chars')} |", ""]
    _field = str(diagnosis.coverage.get("declared_aspect_field") or "")
    _field_note = {
        "declared_aspect_ids": "集合键（`cwm-6` 起）",
        "declared_aspect_id": "**单数键（`cwm-6` 之前的历史请求面）**——本次读的是旧 wire 的"
                              "回放，栏目归属轴因此按「那一小节当时声明的那一栏」判，"
                              "不是按「这一小节覆盖的多栏集合」判",
        "mixed": "**混用**（同一份请求面里两种键同时出现）——这不是任何一版 wire 的形状，"
                 "须人工查明来源",
        "no_subsections": "（请求面里没有小节行）",
    }.get(_field, "（未记录）")
    lines += [f"- 回复体 SHA256：`{diagnosis.reply_sha256}`",
              f"- 请求面 SHA256：`{diagnosis.request_face_sha256}`",
              f"- 小节对账：集合{'逐条相等' if diagnosis.coverage.get('exact_match') else '不等'}"
              f"、顺序{'相同' if diagnosis.coverage.get('same_order') else '不同'}", "",
              f"- 本次读的声明栏目字段：{_field_note}", "",
              "## 原稿全文（逐小节，原样文本 + 派生读数）", ""]

    index = _reading_by_position(diagnosis)
    labels = diagnosis.citation_labels
    for sub_index, sub in enumerate(diagnosis.original_subsections, 1):
        subsection_id = str(sub.get("subsection_id", "") or "")
        lines.append(f"### {subsection_id}｜{sub.get('title', '')}")
        spec = next((s for s in diagnosis.subsections if s.subsection_id == subsection_id), None)
        if spec is not None:
            lines.append(f"- 本小节要求（Contract 逐字）：{spec.requirement_text or '（请求面未给出）'}")
            lines.append("- 本小节声明覆盖的栏目："
                         + (" / ".join(f"`{a}`" for a in spec.declared_aspect_ids)
                            or "（未声明）"))
        inside_gaps = sub.get("gaps")
        paragraphs = sub.get("paragraphs") or ()
        if not paragraphs:
            lines.append("")
            lines.append("_本小节**没有任何句子**（原稿在此只留了缺口副本）。_")
        for para_index, para in enumerate(paragraphs, 1):
            lines.append("")
            lines.append(f"第 {para_index} 段（`{para.get('paragraph_id', '')}`）：")
            for sent_index, sentence in enumerate(para.get("sentences") or (), 1):
                key = (subsection_id, sub_index, para_index, sent_index)
                lines += _sentence_block(
                    index.get(key), text=str(sentence.get("text", "") or ""),
                    citations=sentence.get("citations") or (),
                    model_id=str(sentence.get("sentence_id", "") or ""), labels=labels)
        if isinstance(inside_gaps, list):
            lines += ["", f"原稿在小节内另抄了一份缺口（{len(inside_gaps)} 条）——见 `gaps` 段。"]
        lines.append("")

    lines += ["## 顶层缺口（原样）", ""]
    for gap in diagnosis.gaps:
        lines += [f"- `{gap.get('subsection_id', '')}`｜`{gap.get('reason', '')}`｜"
                  f"要求：{gap.get('requirement_text', '')}",
                  f"  - {gap.get('detail', '')}"]
    lines += ["", "## 补件需求（原样）", ""]
    for need in diagnosis.follow_up_needs:
        lines += [f"- `{need.get('subsection_id', '')}`｜aspect `{need.get('aspect_id', '')}`｜"
                  f"{need.get('requiredness', '')}｜原稿自述来源类 "
                  f"`{need.get('expected_source_class') or '（未写）'}`"
                  f"（**不作数**：来源类由 Contract 推导，见下文对账表）",
                  f"  - {need.get('statement', '')}"]
    lines += ["", "---", "",
              "**边界**：本页只做展示。逐句错因与未判轴的汇总见 `derived_diagnosis.md`；"
              "「什么要人工裁决」「什么只能靠语义审阅」见该页的 §5。", ""]
    return "\n".join(lines)


def _verdict_badge(verdict: str) -> str:
    return {VERDICT_HARD_ERROR: "**硬错**", VERDICT_NEEDS_REVIEW: "需语义审阅",
            VERDICT_CHECKABLE: "可核", VERDICT_UNJUDGED: "未判"}.get(verdict, verdict)


def render_diagnosis_markdown(diagnosis: ReplyDiagnosis) -> str:
    """派生诊断页：结构归一做了什么 + 逐句业务结论 + 未判轴。"""
    counts = diagnosis.verdict_counts()
    summary = diagnosis.adjudicable_summary()
    norm = diagnosis.normalization
    lines = ["# 真实 Writer 返回｜离线派生诊断", "", _BANNER, "",
             "## 0. 先说清楚这份诊断是什么", "",
             "- 结构归一：删掉了"
             f" **{len(norm.get('stripped_subsection_gaps') or [])}** 条"
             "「与顶层同小节缺口逐字段完全相同」的小节内副本（逐条见 `normalization`），"
             "并把 `sentence_id` 重编为**全节唯一**值（模型原 ID / 结构位置 / 新 ID 的映射全在）。",
             f"- 小节集合对账：{'**逐条相等**' if diagnosis.coverage.get('exact_match') else '**不等**'}"
             f"（缺 {diagnosis.coverage.get('missing')}，多 {diagnosis.coverage.get('extra')}）。",
             "- 本诊断**不与清单对账**：那一轮没有在运行目录里落下精确输入清单身份，"
             "而请求面里没有构造一份真清单所需的字段。因此它只能叫**离线派生诊断**，"
             "**不**能倒写成那一轮的正式通过。", "",
             "## 1. 逐句结论", "",
             f"共 **{len(diagnosis.sentences())}** 句："
             + "；".join(f"{k} {v}" for k, v in counts.items() if v) + "。", "",
             f"{summary['headline']}。",
             f"- {summary['rule_unchanged']}",
             "- 数字表面轴分**两条**记：`numeric_surface`（`sc-7` 判据里**找不到** ⇒ **硬错**）与 "
             f"`{CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT}`（数字与单位之间只差**排版空白** ⇒ "
             "**非失败**，单列）。后者**不**从硬错句数里减掉任何一句：两个数各自成键，"
             "谁也不算进谁。放宽的**只有空白**——数值、符号、「超」、单位与数字权威规则"
             "一条都没有放宽，去空白后仍找不到的数字照样是硬错。", "",
             "| 新 ID | 模型原 ID | 小节 | 结论 | 发现 | 文本 |",
             "|---|---|---|---|---|---|"]
    for reading in diagnosis.sentences():
        findings = "；".join(f"`{f.check_kind}`" for f in reading.findings) or "—"
        lines.append(f"| `{reading.final_id}` | `{reading.model_id}` | "
                     f"`{reading.subsection_id}` | {_verdict_badge(reading.verdict)} | "
                     f"{findings} | {reading.text} |")
    lines += ["", "### 1.1 逐条发现", ""]
    for reading in diagnosis.sentences():
        if not reading.findings:
            continue
        lines.append(f"- **`{reading.final_id}`**（`{reading.subsection_id}`）")
        for finding in reading.findings:
            surfaces = f"｜表面 {list(finding.surfaces)}" if finding.surfaces else ""
            lines.append(f"  - `{finding.check_kind}`（{_verdict_badge(finding.verdict)}）"
                         f"：{finding.detail}{surfaces}")
    lines += ["", "## 2. 错栏 / 重复 / 期间 / 数字 四类汇总", ""]
    by_kind: dict[str, list[str]] = {}
    for reading in diagnosis.sentences():
        for finding in reading.findings:
            by_kind.setdefault(finding.check_kind, []).append(reading.final_id)
    for kind in sorted(by_kind):
        lines.append(f"- `{kind}`：{len(by_kind[kind])} 句 —— "
                     + "、".join(f"`{i}`" for i in by_kind[kind]))
    lines += ["", "## 3. 未判轴（因缺精确清单，逐条列出）", "",
              "| 轴 | 状态 | 依据 |", "|---|---|---|"]
    for axis in diagnosis.axes:
        lines.append(f"| `{axis['axis']}` | "
                     f"{'**已判**' if axis['status'] == AXIS_ADJUDICATED else '未判'} | "
                     f"{axis['basis']} |")
    lines += ["", "---", "",
              "**不代替独立审阅**：本页的每一条都是机械判据。语义是否成立、结论是否站得住，"
              "仍须独立审阅与人工接受；本页不做、也不宣称做过。", ""]
    return "\n".join(lines)


def render_analyst_notes(diagnosis: ReplyDiagnosis) -> str:
    """§4 人工判断：与机械判据**分栏分色**地写清楚，避免被当成程序结论。"""
    lines = ["## 4. 人工判断（**不是**机械判据）", "",
             "下面每一条都是**读稿人**看出来的，不是本模块判出来的。列在这里只为不让它散落"
             "在对话里：它**不**自动改句、**不**自动降级、**也**不能代替独立审阅。", ""]
    if not diagnosis.analyst_notes:
        return "\n".join(lines + ["_本次没有登记人工判断。_", ""])
    for note in diagnosis.analyst_notes:
        ids = "、".join(f"`{i}`" for i in note.get("sentence_ids") or ()) or "—"
        lines += [f"### {note.get('judgment', '')}｜`{note.get('subject', '')}`",
                  f"- 涉及句：{ids}",
                  f"- 判断：{note.get('note', '')}",
                  f"- 为什么机械判据抓不到：{note.get('evidence', '')}"]
        if note.get("boundary"):
            lines.append(f"- **本条的边界**：{note['boundary']}")
        lines.append("")
    return "\n".join(lines)


#: 缺口 `detail` 里**关于来源文档**的断言标记（通用词，与具体公司/页码无关）。
#:
#: Writer 只见过本次输入，没见过原文件；它说「表格中披露」时，读者会读成「原 PDF 里有那张表
#: 只是没给我」——那是一条本模块无权判定的断言。可核的说法是「本次 Writer 输入缺少合格数值
#: 事实」。这里只**标出来**，不改写缺口文本。
SOURCE_ASSERTION_MARKERS = (
    #: 指到具体载体/位置的措辞：一出现，说的就不是「我这次拿到了什么」，而是「原文件里有什么」。
    "表格中", "表中", "表内", "以表格形式", "列示",
    "原文件", "原始文件", "年报中", "招股说明书中",
)

#: 光有「披露」两个字不算：缺口里说「相关**披露**范围」是在引本报告该披露什么，是**要求**侧的
#: 话，不是对原文件的断言。只有**否定式**的披露（未/无/不/没 披露）才在断言「来源里没有」。
_SOURCE_ABSENCE_DISCLOSURE_RE = re.compile(r"[未无不没]\S{0,6}披露")


def _source_assertion_hits(detail: str) -> tuple[str, ...]:
    hits = [m for m in SOURCE_ASSERTION_MARKERS if m in detail]
    if _SOURCE_ABSENCE_DISCLOSURE_RE.search(detail):
        hits.append("（否定式）披露")
    return tuple(hits)


def gap_source_assertions(diagnosis: ReplyDiagnosis) -> tuple[Mapping[str, Any], ...]:
    """缺口 `detail` 里越出「本次输入」范围的断言（逐条列出，供人改写）。"""
    hits: list[Mapping[str, Any]] = []
    for gap in diagnosis.gaps:
        detail = str(gap.get("detail", "") or "")
        surfaces = _source_assertion_hits(detail)
        if surfaces:
            hits.append({"subsection_id": str(gap.get("subsection_id", "") or ""),
                         "surfaces": list(surfaces), "detail": detail})
    return tuple(hits)


def render_gap_summary(diagnosis: ReplyDiagnosis) -> str:
    """缺口与补件的**只读**汇总：本批只报告，不推断来源是否真的没有。"""
    lines = ["## 缺口与补件（只读汇总）", "",
             f"- 顶层缺口 {len(diagnosis.gaps)} 条；补件需求 {len(diagnosis.follow_up_needs)} 条。",
             "- 缺口 `detail` 里若出现「多在表格中披露」这类**关于来源文档**的断言，"
             "本页照原样列出并标出：本次 Writer 输入里**没有**合格数值事实是**可核**的，"
             "而「原文件里有没有那张表」本页**无权**判定。", ""]
    assertions = gap_source_assertions(diagnosis)
    if assertions:
        lines += [f"### 越出「本次输入」范围的缺口措辞（{len(assertions)} 条，**须人工改写**）", ""]
        for row in assertions:
            lines.append(f"- `{row['subsection_id']}`｜命中 {row['surfaces']}｜原样：{row['detail']}")
        lines += ["",
                  "改写口径：只写「本次 Writer 输入缺少合格数值事实」，"
                  "**不要**替来源文档下「有表但没给」的结论。", ""]
    lines += ["", "### 缺口「没有」这个词指的是哪一种没有（逐条对账）", "",
              "请求面自带 `subsections[].requirement_text`（逐行）与 `declared_aspect_ids`"
              "（逐位）的**位置对位**，所以「**这条缺口所属的那一栏**本轮有没有来源」**能判**"
              "（判据即生产侧 `aspect_for_requirement`）：", "",
              "| 小节 | 缺口 reason | 落到的那一栏 | 该栏登记来源数 | 来源键 |",
              "|---|---|---|---|---|"]
    for row in diagnosis.gap_context:
        keys = "、".join(f"`{k}`" for k in row["material_keys_registered_for_aspect"]) or "—"
        if row.get("column"):
            where = f"`{row['column']}`"
        else:
            declared = " / ".join(f"`{a}`" for a in row["declared_aspect_ids"]) or "（未声明）"
            where = f"**对不上栏位**（退回整节口径：{declared}）"
        lines.append(f"| `{row['subsection_id']}` | `{row['reason']}` | "
                     f"{where} | "
                     f"{row['materials_registered_for_aspect']} | {keys} |")
    contradictory = [r for r in diagnosis.gap_context
                     if r["claims_no_source_but_materials_exist"]]
    if contradictory:
        lines += ["", f"**口径可疑 {len(contradictory)} 条**（自称 `no_source_in_manifest`，"
                  "而**它所属的那一栏**在本次输入里**有**来源）：", ""]
        for row in contradictory:
            lines.append(f"- `{row['subsection_id']}`｜栏 `{row.get('column')}`：该栏本轮有 "
                         f"{row['materials_registered_for_aspect']} 条来源，说「没有来源」"
                         "是把「来源在但不合格」记成了「没有来源」。")
        lines.append("")
    lines += ["应改写的口径（模板句，供人改写；本页**不**自动替换原文）：",
              f"> {GAP_REWRITE_PRESCRIPTION}", ""]
    for rule in GAP_REWRITE_PROHIBITIONS:
        lines.append(f"- {rule}")
    lines += ["", "### 补件「期望来源类别」是哪一种取值（逐条对账）", "",
              "可检索来源类是**封闭词表**："
              f"{'、'.join(f'`{c}`' for c in TS.SOURCE_CLASSES)}。"
              "`financial_pack` 这类取值不是来源类，它是**权威类型**（回答「这份事实由谁背书」，"
              "不是「去哪里检索」）。来源类**必须**由该栏 Contract 允许的来源推导，"
              "不由 Writer 自由发明：", "",
              "| 小节 | 自述来源类 | 是权威类型吗 | 该栏 Contract 允许的来源类 | 判定 |",
              "|---|---|---|---|---|"]
    for row in diagnosis.source_class_context:
        allowed = row["contract_allowed_source_classes"]
        allowed_text = ("、".join(f"`{c}`" for c in allowed) if allowed
                        else ("（该轮请求面没有这一轴）" if allowed is None
                              else "（该栏无证据要求）"))
        lines.append(f"| `{row['subsection_id']}` | "
                     f"`{row['writer_claimed_source_class'] or '（未写）'}` | "
                     f"{'**是**' if row['is_authority_kind'] else '否'} | "
                     f"{allowed_text} | `{row['verdict']}` |")
    mismatched = [r for r in diagnosis.source_class_context
                  if r["verdict"] in (SOURCE_CLASS_NOT_A_SOURCE_CLASS,
                                      SOURCE_CLASS_OUTSIDE_CONTRACT)]
    if mismatched:
        lines += ["", f"**类别错配 {len(mismatched)} 条**（须人工改写；本页不改原报告）：", ""]
        for row in mismatched:
            if row["verdict"] == SOURCE_CLASS_NOT_A_SOURCE_CLASS:
                lines.append(f"- `{row['subsection_id']}`：自述值 "
                             f"`{row['writer_claimed_source_class']}`"
                             + (" 是**权威类型**，不是来源类" if row["is_authority_kind"]
                                else " 不在封闭来源类词表内")
                             + "。补件要找的是**去哪里检索**，不是**由谁背书**；"
                               "正确取值由该栏 Contract 允许的来源推导。")
            else:
                lines.append(f"- `{row['subsection_id']}`：自述值 "
                             f"`{row['writer_claimed_source_class']}` 是来源类，但不在该栏 "
                             f"Contract 允许的 "
                             f"{'、'.join('`' + c + '`' for c in row['contract_allowed_source_classes'])} "
                             f"之内。")
        lines.append("")
    gap_subs = [str(g.get("subsection_id", "") or "") for g in diagnosis.gaps]
    need_subs = [str(n.get("subsection_id", "") or "") for n in diagnosis.follow_up_needs]
    missing_need = [s for s in gap_subs if s not in need_subs]
    lines += ["", f"- 有缺口但**没有**对应补件需求的小节：{missing_need or '（无）'}"]
    return "\n".join(lines + [""])


#: §5 的三条**通用**判据提醒。它们说的是「哪一种错误该指到哪一环」，与公司、页码、
#: 答案关键词都无关；写成常量是为了让这三句话在各处渲染时**逐字一致**。
BUSINESS_POINTERS = (
    ("机械命中「材料登记在这一栏」**不等于**「这句话回答了这一栏」",
     "`aspect_attribution` 判的是「本句所引来源**登记**在本小节声明的栏目里」，它**不判**"
     "这句话的内容是不是这一栏要的那件事。同一大类下的另一种切分照样能命中登记轴"
     "（要求「按**业务**列示收入构成」时写**分地区 / 境外占比**；要求「按产品」时写按客户；"
     "要求「按业务」时写按渠道）——这类错配只能由语义审阅判，本页把它**列出来**，不替它下结论。"),
    ("别的栏目回答的问题**不能**充作这一栏的答案",
     "要求写「销售模式」时，**生产安排、产能与扩产手段、采购与研发体系**都不是销售模式；"
     "要求写「成本构成」时，**毛利水平与费用率**不是构成；要求写「客户集中度」时，"
     "**行业格局**不是集中度。这类句子的问题既不是「数字错」也不是「引用错」，"
     "而是**回答的是另一栏**——它要回到选材与栏目目标上去改，不是回到数字规则上去改。"),
    ("跨栏目重复与发行人自评，机械层只能**标出**、不能判定",
     "同一段话（逐字或换说法）被拿来填两个栏目、以及发行人对自己优势的评价性表述，"
     "能不能接受取决于「这两个栏目是不是本来就该说同一件事」「报告是否允许转述发行人自评」。"
     "两条都进独立审阅；本页不替审阅下结论。"),
)


def render_business_pointers(diagnosis: ReplyDiagnosis) -> str:
    """§5 把业务错误**指到正确的那一环**：先说判据，再逐句给出栏目坐标。"""
    lines = ["## 5. 业务错因指向哪一环（判据 + 本批坐标）", "",
             "这一节要回答的是「这句错在链条的哪一段」，而不是「这句算不算合格」——"
             "后者是独立审阅与人工接受的事。", ""]
    for index, (headline, body) in enumerate(BUSINESS_POINTERS, 1):
        lines += [f"### 5.{index} {headline}", "", body, ""]
    lines += ["### 5.4 本批逐句栏目坐标（句 → 栏目 → 要求 → 结论 → 触发的机械轴）", "",
              "| 句 | 所在栏目 | 该栏要求（Contract 逐字） | 结论 | 触发原因 |",
              "|---|---|---|---|---|"]
    for reading in diagnosis.sentences():
        kinds = "；".join(f"`{f.check_kind}`" for f in reading.findings) or "—"
        requirement = next((s.requirement_text for s in diagnosis.subsections
                            if s.subsection_id == reading.subsection_id), "")
        declared = " / ".join(f"`{a}`" for a in reading.declared_aspect_ids) or "（未声明）"
        lines.append(f"| `{reading.final_id}` | `{reading.subsection_id}` "
                     f"（{declared}） | {requirement} | "
                     f"{_verdict_badge(reading.verdict)} | {kinds} |")
    notes = [n for n in diagnosis.analyst_notes if n.get("sentence_ids")]
    if notes:
        lines += ["", "### 5.5 人工判断指向的句子（**不是**机械判据，见 §4）", ""]
        for note in notes:
            ids = "、".join(f"`{i}`" for i in note["sentence_ids"])
            lines.append(f"- {ids}｜{note.get('judgment', '')}｜{note.get('subject', '')}")
    lines += ["", "**这一节的边界**：本页不自动改写返回原文、不用候选事实重拼正文，"
              "也不把这里的任何一条升格成「合格 / 不合格」的结论。选材、栏目目标与去重的改善"
              "只落在**下一次**的 Writer 输入与提示词上。", ""]
    return "\n".join(lines)


def diagnosis_files(diagnosis: ReplyDiagnosis) -> dict[str, str]:
    """一次诊断要落的三个文件（名字 → 内容）。"""
    return {"original_draft.md": render_original_markdown(diagnosis),
            "derived_diagnosis.md": (render_diagnosis_markdown(diagnosis) + "\n"
                                     + render_analyst_notes(diagnosis) + "\n"
                                     + render_gap_summary(diagnosis) + "\n"
                                     + render_business_pointers(diagnosis)),
            "derived_diagnosis.json": json.dumps(diagnosis.to_dict(), ensure_ascii=False,
                                                 indent=1, sort_keys=True)}


def iter_finding_kinds(diagnosis: ReplyDiagnosis) -> Iterable[str]:
    seen: dict[str, None] = {}
    for reading in diagnosis.sentences():
        for finding in reading.findings:
            seen[finding.check_kind] = None
    return tuple(seen)

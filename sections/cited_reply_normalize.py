"""§0.20 第一步的**纯净结构归一**（`crn-4`）：只做三件**写入方不该负责**的记账。

背景是 M930-3 真实 r2（call_id `5856a5b820a14b2ca50bd3e31924264d`）那次返回：模型把同一个缺口
既写进顶层 `gaps`、又在对应小节里再抄一遍，并且 `sentence_id` 在**每一个**小节里都从 `s01`
重新起算。前者使整份返回在 schema 层被判「未登记字段」而整节作废，后者使 27 句共用 5 个 id。
两件事都不是内容缺陷，是**记账**缺陷；而记账不该由模型负责——编号是程序的事，去重是结构的事。

`crn-2` 加的第三件事（见 :func:`drop_empty_shell_paragraphs`）来自 M930-5 全过程真实 run
`m930_3_cited_upload_20261005T091926Z`：模型把「这一栏没有来源」写成**一个只有
`paragraph_id` / `aspect_ids` / `sentences: []` 的空壳段**，而 `CitedParagraph` 对空段落
fail-closed（`empty_paragraph`），于是**整节**作废。那也不是内容缺陷，而是**同一件记账的第三种
形态**：模型已经用顶层缺口把「这里没有来源」说清楚了，空壳段是那件事的第二份副本。

本模块因此只做三件事，且都 fail-closed：

* :func:`strip_duplicate_subsection_gaps`——小节内那份缺口**只有当它与顶层同小节的缺口逐字段
  完全相同**时才被确定性删除。**不同、缺失、多出、或「有键但是空表而顶层有」一律照旧拒绝**：
  归一化不是「尽量理解模型」的宽容层，它只删**可证明是同一件事的第二份副本**。
* :func:`assign_section_unique_sentence_ids`——最终 `sentence_id` 由程序按
  「小节 → 段落 → 句子」的稳定遍历顺序生成全节唯一值（`s0001`、`s0002`……），模型原 ID、
  结构位置与新 ID 的映射随返回值一起交出，供落盘回查。**写空 ID 的句子仍然拒绝**：归一化
  只重命名，不替模型补一个它没写的编号（那会把「模型漏了」静默改写成「模型写了」）。
* :func:`drop_empty_shell_paragraphs`——**纯空壳段**被确定性删除。它要满足：字段集恰好是那三项、
  `sentences` 是**空**列表、`paragraph_id` 非空、`aspect_ids` 非空且逐条非空无重复，且它声明的
  **每一栏都属它自己所在的小节**；最后**每一栏**都至少满足**下面两条依据之一**（并集，`crn-4`）：
  **依据一**——该栏在**同一小节**内有一条**可唯一对应**的顶层缺口，其**系统判定**理由落在
  :data:`GAP_SUPPORTED_REASONS`（「本栏本次没有**可写**来源」的理由闭集：零登记来源 ⇒
  `no_source_in_manifest`；登记着来源但没有合格数字事实 ⇒ `source_present_but_not_admissible`）；
  **依据二**（`crn-3` 原口径，一字未改）——该栏在请求面**零登记来源**，且在顶层有一条**系统判定**
  为 `no_source_in_manifest` 的对应缺口。**缺任何一项就原样留下**，由 `CitedParagraph` 照旧抛
  `empty_paragraph`——「模型漏写了整段」与「模型把缺口写成了空壳」在产物上仍必须分得开。
  （依据二正是依据一在「零登记来源」那一支上的特例，因此 `crn-3` 的行为逐字兼容，不是收紧。）

  **第六条（本小节归属）是「证明」不是「未证伪」**：一段借用**别的小节**的「零来源 + 缺口」来
  造空壳，不得被删。删掉它就等于把「模型声明了本小节不负责的栏目」这件事整段抹掉，而这正是
  `CitedWriter._check_paragraph_aspects`（原因码 `paragraph_aspect_out_of_subsection`）要拒的形状：
  同一个输入不能被这条规则先删干净、再让那一道门无对象可判。请求面**查不到**该小节时同样记
  `aspect_not_in_subsection`——查不到就是没证明，没证明就不删（方向与另外两项读数一致）。

  **被删掉的段不计入栏目覆盖。** 它整段从归一结果里消失，因此既不出现在 `_check_paragraph_aspects`
  的段级声明里，也不贡献任何一句；台账逐条写明 `counts_toward_coverage: False`。这不是「这一栏
  写好了」，恰恰相反：它记的是「这一栏**本次写不出来**，而且模型自己也这么说」。

  第三件事**只在调用方交得出请求面读数时**才评估（`EmptyShellContext`）。交不出时
  `empty_shell_rule` 记 `not_evaluated_no_request_face`，行为与 `crn-1` 逐字相同：归一化不猜
  「这一栏有没有来源」——那是清单侧的事实，不是返回的形态。

为什么映射**不进**草稿身份体：`draft_id` 是**内容**寻址。同一段最终正文，不应因为模型当初用了
`s01` 还是 `sent01` 而得到两个不同的身份——那样「同一份正文」这句话就先不成立了。映射属于
**留存证据**，落 `cited_call_journal.json`（见 :mod:`sections.cited_call_journal`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sections import cited_writer as CW
from sections import narrative_schema as NS


#: 归一化规则版本。规则本身（删什么、怎么编号）换一种写法就换一个版本号。
#: `crn-2` = `crn-1` 的两条规则 + 空壳段删除（五项条件）。`crn-3` = `crn-2` + 第六条
#: **本小节归属**：一段只能删掉**自己所在小节**声明的栏目，跨小节借用的那一类照旧拒收。
#: `crn-4` = `crn-3` + **缺口支持**：删除依据不再要求「所声明每一栏零登记来源」，而是要求
#: **每一栏在同一小节内有一条可唯一对应的顶层缺口、且其系统判定理由落在
#: :data:`GAP_SUPPORTED_REASONS`**。`crn-3` 的口径是它的一个特例（零登记来源的栏，
#: `assign_gap_reason` 只会给出 `no_source_in_manifest`），因此**两条既有拒收码语义不变**：
#: 无合格对应缺口时仍分别报 `aspect_has_registered_source` / `aspect_without_no_source_gap`。
#: 读回侧**按这个声明分派**：旧产物里写的是 `crn-1`，它们当时**没有**跑过第三条规则；
#: `crn-2` 的产物少一条归属判据、`crn-3` 的产物少一条缺口支持判据，都不得按新口径回读。
CITED_REPLY_NORMALIZE_VERSION = "crn-4"

#: 第三条规则这一轮**跑了没有**。两态都写进台账，因为「跑了、没删任何东西」与
#: 「根本没评估」在产物上必须分得开——后者是 `crn-1` 的行为。
EMPTY_SHELL_RULE_APPLIED = "applied"
EMPTY_SHELL_RULE_NOT_EVALUATED = "not_evaluated_no_request_face"

#: 空壳段被删的**唯一**原因码。它同时说明「为什么可以删」：这一栏本次零登记来源，
#: 顶层也已经有对应的 `no_source_in_manifest` 缺口。
EMPTY_SHELL_NO_SOURCE = "empty_shell_no_source"

#: `crn-4` 新增的**第二个**删除原因码：本段所声明**每一栏**在同一小节内都有一条可唯一对应、
#: 且系统判定理由落在 :data:`GAP_SUPPORTED_REASONS` 的顶层缺口——其中**至少一栏**在请求面
#: **登记着来源**（否则这一段的删除依据就是 `empty_shell_no_source`）。它说的是「这一段用的
#: 是比『零来源』更宽的那条依据」，读回侧据此能分清「删它靠的是哪一条」。
EMPTY_SHELL_GAP_SUPPORTED = "empty_shell_gap_supported"

#: 本方案认可的**缺口理由**闭集：只有这两条表达「这一栏本次没有**可写**来源」。
#:
#: 它们是**同一条业务事实**的两面，正是 :func:`sections.cited_writer.assign_gap_reason` 自己
#: 声明的两条改判规则：零登记来源时只能是 `no_source_in_manifest`；登记着来源而没有合格数字
#: 事实时是 `source_present_but_not_admissible`。其余理由（例如
#: `manifest_partial_for_requirement`——「清单对这一条要求只覆盖了一部分」）说的是另一件事，
#: **不**构成删除依据：把「写不全」当成「没得写」，正是本条规则要避免的那类混淆。
GAP_SUPPORTED_REASONS = ("no_source_in_manifest", "source_present_but_not_admissible")

#: 空壳段**被留下**时，是哪一项条件没满足。逐条封闭，不留自由文本——读回侧要按它分流。
#:
#: 只列**可达**的取值。`sentences` 缺键、`null`、写成一个字符串这些形态在这里**不留台账**：
#: 它们不是「空壳段」，是坏形，由 :func:`assign_section_unique_sentence_ids` 用
#: `paragraph_field_malformed` 拒绝——同一个输入不能被两处各记一条不同的话。
EMPTY_SHELL_KEPT_REASONS = (
    "paragraph_extra_fields",
    "paragraph_missing_fields",
    "paragraph_id_empty",
    "aspect_ids_missing_or_malformed",
    "aspect_not_in_subsection",
    "aspect_has_registered_source",
    "aspect_without_no_source_gap",
)

#: 最终句子编号的形状：`s` + 定宽十进制序号。**全节唯一**由遍历顺序保证（不是由模型的自觉）。
CITED_SENTENCE_ID_PREFIX = "s"
CITED_SENTENCE_ID_WIDTH = 4

#: 缺口与顶层返回的**登记字段集**（与 `CitedProseGap` 的 wire 逐字相同）。
_GAP_FIELDS = ("subsection_id", "requirement_text", "reason", "detail")

#: 「这个字段**没有出现**」与「这个字段出现了、值是 `null`」是**两件事**。
#:
#: 比对副本时若用 `gap.get(name)`，两者都会变成 `None` 而相互抵消——于是「少抄了一个字段」
#: 会被读成「抄了一个 null」，一份**不完整**的副本会被当成**完全相同**的副本删掉。这里用一个
#: 不可能与任何合法取值相撞的哨兵把「缺席」单独编码；同时缺口本身也要求四个字段**全部在场**
#: （见 :func:`_require_gap_fields`），两道一起，缺席既不可解析、也不会与 `null` 混同。
_GAP_ABSENT = "\x00absent\x00"
_REPLY_FIELDS = ("subsections", "gaps", "follow_up_needs")
_SUBSECTION_FIELDS = ("subsection_id", "title", "paragraphs", "gaps")
_PARAGRAPH_FIELDS = ("paragraph_id", "sentences", "aspect_ids")


# ---------------------------------------------------------------------------
# 记录类型（可落盘、可回查）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentenceIdAssignment:
    """一句的**编号台账**一行：模型原 ID、结构位置、程序给的新 ID。三者缺一不可。

    只留新 ID 会让「模型当初写了什么」消失；只留原 ID 会让「读者面看到的是哪一句」错位。
    """

    final_id: str
    model_id: str
    subsection_id: str
    subsection_index: int
    paragraph_index: int
    sentence_index: int

    def to_dict(self) -> dict:
        return {"final_id": self.final_id, "model_id": self.model_id,
                "subsection_id": self.subsection_id,
                "subsection_index": self.subsection_index,
                "paragraph_index": self.paragraph_index,
                "sentence_index": self.sentence_index}


@dataclass(frozen=True)
class StrippedSubsectionGap:
    """被删掉的那**一份副本**（内容与保留下来的顶层缺口逐字段相同，因此只记定位与文本）。"""

    subsection_id: str
    requirement_text: str
    reason: str
    detail: str

    def to_dict(self) -> dict:
        return {"subsection_id": self.subsection_id,
                "requirement_text": self.requirement_text,
                "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class EmptyShellContext:
    """判定「纯空壳段」需要的**请求面读数**。三项都必须由调用方从清单侧算好，归一化不猜。

    * `registered_source_counts`：`aspect_id` → 该栏在请求面登记到的**来源数**
      （材料行 ∪ 事实行）。取不到该栏就是 `0`——「零登记」是**结论**（这一栏本次没有来源），
      不是「读不到」。真实链里这两个数逐字来自
      :func:`sections.cited_writer.registered_source_keys`，不另造一份口径。
    * `no_source_gap_aspects`：顶层缺口里**系统判定**为 `no_source_in_manifest` 的那些栏
      （判据是 :func:`sections.cited_writer.assign_gap_reason` 的输出，不是模型自述的那个字）。
      一条缺口的 `requirement_text` 落到哪一栏由 `aspect_for_requirement` 决定；落不到栏上的
      缺口**不**在这里充当任何一栏的证据。
    * `subsection_aspect_ids`：`subsection_id` → 该小节在请求面**声明的栏目集合**（真实链里
      逐字取自 `manifest_spec_for_subsection(...).declared_aspect_ids`）。它回答的是**归属**：
      「这一栏是这一段自己那一节的栏目吗」。
    * `subsection_gap_reason`（`crn-4` 新增）：`subsection_id` → 栏 → 该栏在**这一个小节**里
      **可唯一对应**的那条顶层缺口的**系统判定**理由。**只登记恰好一条的**：同一栏在本小节被
      0 条或 ≥2 条缺口指认时一律不进表——「可唯一对应」是这条依据的前提，认不出唯一的那条，
      就交不出唯一的那句话。真实链里由
      :func:`sections.cited_writer.unique_subsection_gap_reasons` 建；离线读回侧用**同一个**
      函数，不另造口径。

    第四项与前三项的方向也不同，必须说清：它**取不到即「没有这条依据」**（保守方向与
    「零来源」一致）——但它**不是**在回答「这一栏能不能写」，而是在回答「这一栏这次**有没有
    一条被系统判定为『没有可写来源』的缺口**」。这一条成立**不**代表「登记了材料就足以证明
    集中度」，恰恰相反：它只在 Material 登记着但**没有合格数字事实**时才成立。

    第三项与另外两项的**缺省方向相反**，必须说清：前两项「取不到即零/即无」——因为「这一栏本次
    没有来源」是一个**结论**；第三项取不到则是**没证明**，而没证明**不许删**。因此**查不到该小节
    就一律不删**（`aspect_not_in_subsection`），这与 :func:`drop_empty_shell_paragraphs` 的单向性
    是同一条纪律：读数不全时多出来的只会是**留下来**的段。

    三项都只回答「这一栏本次有没有来源、是不是本小节的栏目」——**不**回答「这一栏能不能写」。
    """

    registered_source_counts: Mapping[str, int] = field(default_factory=dict)
    no_source_gap_aspects: frozenset[str] = frozenset()
    subsection_aspect_ids: Mapping[str, frozenset[str]] = field(default_factory=dict)
    subsection_gap_reason: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        counts: dict[str, int] = {}
        for key, value in dict(self.registered_source_counts or {}).items():
            counts[str(key)] = int(value)
        object.__setattr__(self, "registered_source_counts", counts)
        object.__setattr__(self, "no_source_gap_aspects",
                           frozenset(str(a) for a in (self.no_source_gap_aspects or ())))
        declared: dict[str, frozenset[str]] = {}
        for key, value in dict(self.subsection_aspect_ids or {}).items():
            declared[str(key)] = frozenset(str(a) for a in (value or ()) if str(a).strip())
        object.__setattr__(self, "subsection_aspect_ids", declared)
        reasons: dict[str, dict[str, str]] = {}
        for sub_key, per_aspect in dict(self.subsection_gap_reason or {}).items():
            #: 空理由（`""`）与缺席同义：理由取不到就不构成依据，因此不留空登记。
            rows = {str(a): str(r) for a, r in dict(per_aspect or {}).items() if str(r).strip()}
            if rows:
                reasons[str(sub_key)] = rows
        object.__setattr__(self, "subsection_gap_reason", reasons)

    def aspect_is_source_free(self, aspect_id: str) -> bool:
        """这一栏在请求面**零登记来源**吗。缺项即零（见类注释）。"""
        return int(self.registered_source_counts.get(str(aspect_id), 0)) == 0

    def aspect_declared_unsourced(self, aspect_id: str) -> bool:
        """顶层已有一条系统判定为 `no_source_in_manifest` 的、落到这一栏的缺口吗。"""
        return str(aspect_id) in self.no_source_gap_aspects

    def aspect_belongs_to(self, subsection_id: str, aspect_id: str) -> bool:
        """这一栏是**该小节**声明的栏目吗。**查不到该小节即 `False`**（没证明就不许删）。"""
        declared = self.subsection_aspect_ids.get(str(subsection_id))
        if declared is None:
            return False
        return str(aspect_id) in declared

    def aspect_unique_gap_reason(self, subsection_id: str, aspect_id: str) -> str:
        """这一栏在**该小节**里那条**可唯一对应**的顶层缺口的系统判定理由；取不到即空串。

        **空串同时覆盖三种情形**——没有缺口、同一栏被两条以上缺口指认、该小节查不到。
        三者都不构成依据（见类注释：第四项取不到即「没有这条依据」），因此不必分开表示：
        要分的是「可删／不可删」，而判据是 :data:`GAP_SUPPORTED_REASONS` 的成员资格，
        空串天然不在其中。**理由本身照实返回**（例如 `manifest_partial_for_requirement`）——
        它是不是放行依据由调用方按那个闭集判，本读数只回答「是哪一句」。
        逐栏的登记数与理由另由删除台账（:class:`DroppedEmptyShellParagraph`）照实记下，
        读回侧要分流时看那里。
        """
        per_aspect = self.subsection_gap_reason.get(str(subsection_id))
        if not per_aspect:
            return ""
        return str(per_aspect.get(str(aspect_id), ""))


@dataclass(frozen=True)
class DroppedEmptyShellParagraph:
    """被删掉的那个空壳段。定位、它声明的栏目、**它不计入覆盖**这句话，以及**删它的依据**。

    `registered_source_counts` / `system_gap_reasons`（`crn-4` 新增）是这条规则的**证据行**：
    逐栏记下「这一栏本次登记到几条来源」与「这一栏那条可唯一对应的缺口被**系统**判成了什么
    理由」。有了这两列，删除决定可以逐栏复核——「登记了材料」不会被读成「足以证明」，
    「零登记」也不会被伪装成有来源；「登记着来源」这一栏走的正是
    `source_present_but_not_admissible` 那条码。
    """

    subsection_id: str
    subsection_index: int
    paragraph_index: int
    paragraph_id: str
    aspect_ids: tuple[str, ...] = ()
    reason: str = EMPTY_SHELL_NO_SOURCE
    registered_source_counts: Mapping[str, int] = field(default_factory=dict)
    system_gap_reasons: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"reason": self.reason, "subsection_id": self.subsection_id,
                "subsection_index": self.subsection_index,
                "paragraph_index": self.paragraph_index,
                "paragraph_id": self.paragraph_id,
                "aspect_ids": list(self.aspect_ids),
                #: 逐栏登记来源数（材料行 ∪ 事实行）。**零就是零**，不因「这一栏有没有缺口」而改。
                "registered_source_counts": {str(a): int(n) for a, n in
                                             dict(self.registered_source_counts or {}).items()},
                #: 逐栏那条可唯一对应缺口的**系统判定**理由（模型自述的那一个字不作数）。
                "system_gap_reasons": {str(a): str(r) for a, r in
                                       dict(self.system_gap_reasons or {}).items()},
                #: 这一行是这条规则的**结论本身**：删掉不等于写好。空壳段既不出现在段级栏目
                #: 声明里，也不贡献任何一句，因此覆盖账上它一个字都不算。
                "counts_toward_coverage": False}


@dataclass(frozen=True)
class KeptEmptyParagraph:
    """一个**没有句子**的段落，但**不满足**纯空壳的全部条件 ⇒ 原样留下。

    留下它就意味着 `CitedParagraph.__post_init__` 照旧抛 `empty_paragraph`、整节作废——
    这正是要点：「模型漏写了整段」「这一栏其实有来源」都不能被这条规则悄悄放过。
    """

    subsection_id: str
    subsection_index: int
    paragraph_index: int
    paragraph_id: str
    aspect_ids: tuple[str, ...] = ()
    blocked_by: str = ""

    def to_dict(self) -> dict:
        return {"blocked_by": self.blocked_by, "subsection_id": self.subsection_id,
                "subsection_index": self.subsection_index,
                "paragraph_index": self.paragraph_index,
                "paragraph_id": self.paragraph_id,
                "aspect_ids": list(self.aspect_ids)}


@dataclass(frozen=True)
class NormalizedCitedReply:
    """归一化结果：归一后的返回 + 四份台账（两份编号/去重、两份空壳段）。"""

    payload: dict
    sentence_ids: tuple[SentenceIdAssignment, ...] = ()
    stripped_subsection_gaps: tuple[StrippedSubsectionGap, ...] = ()
    dropped_empty_shell_paragraphs: tuple[DroppedEmptyShellParagraph, ...] = ()
    kept_empty_paragraphs: tuple[KeptEmptyParagraph, ...] = ()
    empty_shell_rule: str = EMPTY_SHELL_RULE_NOT_EVALUATED
    version: str = CITED_REPLY_NORMALIZE_VERSION

    def to_dict(self) -> dict:
        return {"normalize_version": self.version,
                "empty_shell_rule": self.empty_shell_rule,
                "sentence_ids": [a.to_dict() for a in self.sentence_ids],
                "stripped_subsection_gaps": [g.to_dict()
                                             for g in self.stripped_subsection_gaps],
                "dropped_empty_shell_paragraphs": [
                    d.to_dict() for d in self.dropped_empty_shell_paragraphs],
                "kept_empty_paragraphs": [k.to_dict() for k in self.kept_empty_paragraphs]}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _fail(message: str, *, reason: str) -> None:
    raise CW.CitedWriterError(message, reason=reason)


def _require_mapping(value: Any, *, what: str, reason: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{what} 必须是对象，得到 {type(value).__name__}", reason=reason)
    return value


def _reject_unknown_fields(value: Any, *, allowed: Sequence[str], what: str,
                           reason: str) -> dict:
    """字段集校验，但抛出**本链自己的** typed 原因码（而不是 `NarrativeSchemaError`）。

    `NS._reject_unknown` 是「未登记字段」这条规则的**唯一**实现，这里复用它、只换异常类型：
    读回侧要按原因码分流，而 `NarrativeSchemaError` 不带链内原因码。
    """
    value = _require_mapping(value, what=what, reason=reason)
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        _fail(f"{what} 含未登记字段 {unknown}", reason=reason)
    return value


def _canonical(value: Any) -> str:
    """缺口比对的**唯一**判据：排序键 + 紧凑分隔符的稳定序列化。"""
    return NS.canonical_json(value)


def _require_gap_fields(gap: dict, *, what: str) -> None:
    """缺口四个登记字段必须**全部在场**。

    少了 `detail` 的缺口与把 `detail` 显式写成 `null` 的缺口是两份不同的记账；前者是模型漏抄
    了字段，后者是模型抄了一个空值。只有在场性先被钉住，`_gap_body` 的缺席哨兵才有意义。
    """
    absent = [name for name in _GAP_FIELDS if name not in gap]
    if absent:
        _fail(f"{what} 缺少登记字段 {absent}（只有这四个字段全部在场才可与另一份副本比对）",
              reason="gap_field_missing")


def _gap_body(gap: dict) -> dict:
    """缺口的**在场性视图**：缺席编码为 :data:`_GAP_ABSENT`，显式 `null` 原样保留为 `None`。

    这样「少抄一个字段」与「抄了一个 null」在规范化文本里就是两个不同的串，比较不会互相抵消。
    """
    return {name: (gap[name] if name in gap else _GAP_ABSENT) for name in _GAP_FIELDS}


# ---------------------------------------------------------------------------
# 规则一：小节内复制的缺口
# ---------------------------------------------------------------------------


def strip_duplicate_subsection_gaps(payload: dict, *,
                                     subsections: Sequence[dict] | None = None,
                                     ) -> tuple[dict, tuple[StrippedSubsectionGap, ...]]:
    """删掉「与顶层同小节缺口逐字段完全相同」的小节内副本；任何不一致都拒绝。

    判定按**多重集**（同一小节有两条缺口时，两份也要一一对上），因为「少抄了一条」与
    「多抄了一条」都说明模型的两处记账已经不一致，这时删或不删都是替它做决定。

    `subsections` 允许调用方传入（读回侧先做小节级校验、再进本函数），缺省取
    `payload["subsections"]`。返回**新**的 payload（原对象不被改动：读回侧还要用原文）。
    """
    top = payload.get("gaps") or ()
    if not isinstance(top, (list, tuple)):
        _fail("顶层 gaps 必须是列表", reason="gap_field_malformed")
    subs = (list(subsections) if subsections is not None
            else list(payload.get("subsections") or ()))
    sub_ids: list[str] = []
    for index, sub in enumerate(subs, 1):
        sub = _reject_unknown_fields(sub, allowed=_SUBSECTION_FIELDS,
                                     what=f"第 {index} 个小节", reason="subsection_field_malformed")
        sub_ids.append(str(sub.get("subsection_id") or ""))

    top_by_subsection: dict[str, list[str]] = {}
    top_kept: list[dict] = []
    for index, gap in enumerate(top, 1):
        gap = _reject_unknown_fields(gap, allowed=_GAP_FIELDS,
                                     what=f"顶层第 {index} 条缺口", reason="gap_field_malformed")
        _require_gap_fields(gap, what=f"顶层第 {index} 条缺口")
        subsection_id = str(gap.get("subsection_id") or "")
        if subsection_id not in sub_ids:
            _fail(
                f"顶层缺口指向的小节 {subsection_id!r} 不在本次返回的小节里 "
                f"{sorted(set(sub_ids))}：两处记账已经脱钩，删哪一份都是替模型做决定",
                reason="gap_subsection_unknown")
        top_by_subsection.setdefault(subsection_id, []).append(_canonical(_gap_body(gap)))
        top_kept.append(dict(gap))

    stripped: list[StrippedSubsectionGap] = []
    cleaned: list[dict] = []
    for index, sub in enumerate(subs, 1):
        row = dict(sub)
        if "gaps" not in row:
            #: 没有这一份副本就没有可删的东西。**不**因「顶层有、小节里没有」而拒绝：
            #: 那是「模型没有重复抄写」，正是正常形状。
            cleaned.append(row)
            continue
        subsection_id = str(row.get("subsection_id") or "")
        inside = row.get("gaps") or ()
        if not isinstance(inside, (list, tuple)):
            _fail(f"小节 {subsection_id!r} 的 gaps 必须是列表", reason="gap_field_malformed")
        inside_bodies: list[dict] = []
        inside_canon: list[str] = []
        for position, gap in enumerate(inside, 1):
            gap = _reject_unknown_fields(
                gap, allowed=_GAP_FIELDS,
                what=f"小节 {subsection_id!r} 内第 {position} 条缺口",
                reason="gap_field_malformed")
            _require_gap_fields(gap, what=f"小节 {subsection_id!r} 内第 {position} 条缺口")
            inside_bodies.append(dict(gap))
            inside_canon.append(_canonical(_gap_body(gap)))
        theirs = top_by_subsection.get(subsection_id, [])
        if sorted(inside_canon) != sorted(theirs):
            _fail(
                f"小节 {subsection_id!r} 内的 {len(inside_canon)} 条缺口与顶层同小节的 "
                f"{len(theirs)} 条**不是**逐字段相同的一份：小节内={sorted(inside_canon)}，"
                f"顶层={sorted(theirs)}。只有可证明是同一件事的第二份副本才允许删除",
                reason="subsection_gap_mismatch")
        for body in inside_bodies:
            stripped.append(StrippedSubsectionGap(
                subsection_id=str(body.get("subsection_id") or ""),
                requirement_text=str(body.get("requirement_text") or ""),
                reason=str(body.get("reason") or ""), detail=str(body.get("detail") or "")))
        row.pop("gaps")
        cleaned.append(row)

    return {"subsections": cleaned, "gaps": top_kept,
            "follow_up_needs": list(payload.get("follow_up_needs") or ())}, tuple(stripped)


# ---------------------------------------------------------------------------
# 规则二：全节唯一的句子编号
# ---------------------------------------------------------------------------


def derive_sentence_id(ordinal: int) -> str:
    """第 `ordinal`（1 起）句的最终 ID。同一个序号在任何一次运行里都给同一个字符串。"""
    return f"{CITED_SENTENCE_ID_PREFIX}{int(ordinal):0{CITED_SENTENCE_ID_WIDTH}d}"


def assign_section_unique_sentence_ids(
        payload: dict) -> tuple[dict, tuple[SentenceIdAssignment, ...]]:
    """按「小节 → 段落 → 句子」稳定顺序重编全节唯一的 `sentence_id`。

    **不**接受空 ID：这里的职责是重命名，不是补编号。空 ID 仍然 fail-closed，否则
    「模型漏写了编号」会被静默改写成「模型写了编号」。
    """
    subs = payload.get("subsections")
    if not isinstance(subs, (list, tuple)):
        _fail("subsections 必须是列表", reason="subsection_field_malformed")
    assignments: list[SentenceIdAssignment] = []
    cleaned_subs: list[dict] = []
    ordinal = 0
    for sub_index, sub in enumerate(subs, 1):
        sub = _require_mapping(sub, what=f"第 {sub_index} 个小节",
                               reason="subsection_field_malformed")
        subsection_id = str(sub.get("subsection_id") or "")
        paragraphs = sub.get("paragraphs") or ()
        if not isinstance(paragraphs, (list, tuple)):
            _fail(f"小节 {subsection_id!r} 的 paragraphs 必须是列表",
                  reason="paragraph_field_malformed")
        cleaned_paras: list[dict] = []
        for para_index, para in enumerate(paragraphs, 1):
            para = _reject_unknown_fields(para, allowed=_PARAGRAPH_FIELDS,
                                          what=f"小节 {subsection_id!r} 第 {para_index} 段",
                                          reason="paragraph_field_malformed")
            #: 段级 `aspect_ids`（`cw-4`）声明本段服务哪几条 Contract 栏目。**缺席合法**
            #: （「本段不对应任何栏目」与「引用为空」同一条纪律），但**出现就得是字符串数组**：
            #: 交给下游用 `tuple(...)` 兜底会把一个字符串拆成单个字，那种坏形会一路装成
            #: 「这一段声明了一堆栏目」——越界判定随后只会报越界，读不出「本来就是个坏形」。
            #: 是否越出本小节声明集合不在这里判（`_check_paragraph_aspects` 才拿得到那一侧）。
            declared_here = para.get("aspect_ids")
            if declared_here is not None:
                if not isinstance(declared_here, (list, tuple)):
                    _fail(f"小节 {subsection_id!r} 第 {para_index} 段的 aspect_ids 必须是列表"
                          f"（得到 {type(declared_here).__name__}）",
                          reason="paragraph_field_malformed")
                if any(not isinstance(a, str) or not a.strip() for a in declared_here):
                    _fail(f"小节 {subsection_id!r} 第 {para_index} 段的 aspect_ids 必须逐条是"
                          f"非空字符串（得到 {list(declared_here)!r}）",
                          reason="paragraph_field_malformed")
                para = {**para, "aspect_ids": [a.strip() for a in declared_here]}
            sentences = para.get("sentences") or ()
            if not isinstance(sentences, (list, tuple)):
                _fail(f"小节 {subsection_id!r} 第 {para_index} 段的 sentences 必须是列表",
                      reason="sentence_field_malformed")
            cleaned_sentences: list[dict] = []
            for sent_index, sentence in enumerate(sentences, 1):
                sentence = _require_mapping(
                    sentence,
                    what=f"小节 {subsection_id!r} 第 {para_index} 段第 {sent_index} 句",
                    reason="sentence_field_malformed")
                model_id = sentence.get("sentence_id")
                if not isinstance(model_id, str) or not model_id.strip():
                    _fail(
                        f"小节 {subsection_id!r} 第 {para_index} 段第 {sent_index} 句没有可辨的 "
                        f"`sentence_id`（得到 {model_id!r}）：归一化只重命名，不替模型补编号",
                        reason="sentence_id_empty")
                ordinal += 1
                final_id = derive_sentence_id(ordinal)
                assignments.append(SentenceIdAssignment(
                    final_id=final_id, model_id=model_id, subsection_id=subsection_id,
                    subsection_index=sub_index, paragraph_index=para_index,
                    sentence_index=sent_index))
                cleaned_sentences.append({**sentence, "sentence_id": final_id})
            cleaned_paras.append({**para, "sentences": cleaned_sentences})
        cleaned_subs.append({**sub, "paragraphs": cleaned_paras})
    return {**payload, "subsections": cleaned_subs}, tuple(assignments)


# ---------------------------------------------------------------------------
# 规则三：纯空壳段
# ---------------------------------------------------------------------------


def _empty_shell_blocked_by(paragraph: dict, *, subsection_id: str,
                            context: EmptyShellContext) -> str:
    """这一段**为什么不能**按空壳删。返回空串表示「可以删」。

    只对「`sentences` 是一个**空**列表」的段调用（其余形态根本进不到空壳判定，见
    :data:`EMPTY_SHELL_KEPT_REASONS`）。判据的顺序是**固定的**，因此同一段永远得到同一个
    原因码；顺序本身也表达优先级：先看它是不是「真的什么都没有」（形态），再看请求面那四项
    （事实）——事实里先问**归属**（这一栏是不是这一段自己那一节的），再问这一栏**有没有一条
    可唯一对应的、被系统判成「本栏本次没有可写来源」的缺口**；这一条不成立时，才退回按
    「零登记来源」分别报两个码。

    `crn-4` 相对 `crn-3` 只改了一件事：把「零登记来源 **且** 有 `no_source_in_manifest` 缺口」
    这一对条件，推广为**同一条缺口依据的两种理由**（见 :data:`GAP_SUPPORTED_REASONS`）。
    登记数**仍然逐栏如实参与**——它不再是否决条件，但它决定这一栏会走到哪个码上，
    也因此被逐栏记进删除台账。
    """
    #: 形态一：字段集**恰好**是那三项。多一个字段（例如模型自己加的 `text` / `note`）就不是
    #: 「什么都没有」——那是一个带了别的内容的段，归一化无权替它决定那内容算什么。
    fields = set(paragraph)
    expected = set(_PARAGRAPH_FIELDS)
    if fields - expected:
        return "paragraph_extra_fields"
    if expected - fields:
        #: 少一个字段也**不是**空壳段，但它与「多一个字段」是两件事（一个是模型塞了别的东西，
        #: 一个是模型没把这一段写完），读回侧要按码分流，因此两个码不合并。
        return "paragraph_missing_fields"
    if not str(paragraph.get("paragraph_id") or "").strip():
        return "paragraph_id_empty"
    declared = paragraph.get("aspect_ids")
    if not isinstance(declared, (list, tuple)) or not declared:
        return "aspect_ids_missing_or_malformed"
    aspects = [str(a).strip() for a in declared]
    if any(not a for a in aspects) or len(set(aspects)) != len(aspects):
        return "aspect_ids_missing_or_malformed"
    for aspect in aspects:
        #: **归属先于来源**：借用别的小节的栏目（或本小节在请求面无从查证）时，这一段说的不是
        #: 「我这里没有来源」，是「我这里声明了本小节不负责的栏目」——后者是另一件更重的事，
        #: 不能被记成前者，更不能被删掉。同一条件另有下游门 `paragraph_aspect_out_of_subsection`。
        if not context.aspect_belongs_to(subsection_id, aspect):
            return "aspect_not_in_subsection"
        #: **缺口依据一（`crn-4` 新加的）**：这一栏在本小节里有一条**可唯一对应**、且被
        #: 系统判成「本栏本次没有可写来源」的顶层缺口。成立即放行——那一段与那条缺口是同一件
        #: 事实的两份记账，删掉它不改变任何一栏的结论（缺口原样留在顶层）。
        #: 「可唯一对应」是对**这一条**的要求（0 条或 ≥2 条都不算）；下一条是 `crn-3` 的原口径，
        #: 只要求「有这样一条缺口」，不要求唯一——两条依据是**并集**，因此第三项读数与
        #: `crn-3` 逐字兼容，不是把它收紧。
        if context.aspect_unique_gap_reason(subsection_id, aspect) in GAP_SUPPORTED_REASONS:
            continue
        #: **缺口依据二（`crn-3` 原口径，一字未改）**：零登记来源 + 有一条 `no_source_in_manifest`
        #: 缺口。
        if context.aspect_is_source_free(aspect) and context.aspect_declared_unsourced(aspect):
            continue
        #: 两条依据都不成立时，按 `crn-3` 的两条口径分别给出**不同的**码：有登记来源是
        #: 「来源在、但本次没有合格的写得出的事实」（该去补事实资格），零登记来源才是
        #: 「本栏本次没有来源」（该去检索）。两者要补的东西不同，不能合并成一个码。
        if not context.aspect_is_source_free(aspect):
            return "aspect_has_registered_source"
        return "aspect_without_no_source_gap"
    return ""


def drop_empty_shell_paragraphs(
        payload: dict, *, context: EmptyShellContext,
        ) -> tuple[dict, tuple[DroppedEmptyShellParagraph, ...],
                   tuple[KeptEmptyParagraph, ...]]:
    """删掉**纯空壳段**；留下其余一切。返回**新** payload（原对象不被改动）。

    「纯空壳段」= 一个只写了 `paragraph_id` / `aspect_ids` / `sentences: []` 的段，且它声明的
    **每一栏**都**属它自己所在的小节**、并在**同一小节**内有一条**可唯一对应**的顶层缺口、
    那条缺口的**系统判定**理由又是「本栏本次没有可写来源」（零登记来源 ⇒
    `no_source_in_manifest`；登记着来源但没有合格数字事实 ⇒ `source_present_but_not_admissible`，
    见 :data:`GAP_SUPPORTED_REASONS`）。它是模型对同一件事实的第二份记账（顶层缺口是第一份），
    删掉它不改变任何一栏的结论——那条缺口、补件需求与来源身份**一个不留地原样留着**。

    **借来的栏目不算**：一段声明了别的小节的「零来源 + 缺口」栏目时，它记的不是「我这里没有
    来源」，而是「我这里声明了本小节不负责的栏目」——那件事有专门的拒收门
    （`_check_paragraph_aspects`，原因码 `paragraph_aspect_out_of_subsection`），本条规则
    **不得**抢在它前面把整段删掉、让那道门无对象可判。

    **不满足全部条件的一律原样留下**：前三项读数（归属、登记数、有无 `no_source_in_manifest`
    缺口）只可能「少认出」，算错它们多出来的只会是**没删掉**的段，下游 `CitedParagraph` 照旧
    fail-closed。`crn-4` 的第四项（缺口支持）方向**不同**，必须说清：它多一条就多删一段，因此
    这条规则不再无条件地「只少删」。它仍然不可能把一次**应该**失败的解析变成成功，理由是三项：

    1. **删的依据是同一次返回里那条顶层缺口**，缺口、补件需求、来源身份一字不动地留着；
       被删的段本身**零句、零引用**，删它不改变任何一栏的结论，也不产生任何新证据。
    2. **删掉的不进任何账**：`counts_toward_coverage: false`，不进段级栏目声明、不计事实资格、
       不计审阅通过、不计系统放行。
    3. **每一栏的依据逐项落台账**：登记数与系统理由都记下来，可以逐栏复核「这一栏当时到底
       有没有来源、理由是谁判的」。

    仍要说清这一条**做不到**什么：系统只知道某栏**登记着**来源（`registered_source_keys`），
    看不见那些来源里到底有没有合格事实。因此模型若为一条**有登记来源**的栏自述
    `source_present_but_not_admissible`，这一句**不被系统独立证伪**（`assign_gap_reason` 只在
    登记数为 0 时才把它改判回 `no_source_in_manifest`）。这条规则接受它，但接受的只是「这一段
    没有句子可写」这个**空的**结论——它换不到任何内容、任何引用或任何放行。

    结构读不动时（`subsections` 不是列表等）原样返回、两份台账为空：那一种输入由
    :func:`assign_section_unique_sentence_ids` 用它自己的原因码拒绝，不在这里抢先报另一个。
    """
    subs = payload.get("subsections")
    if not isinstance(subs, (list, tuple)):
        return dict(payload), (), ()
    dropped: list[DroppedEmptyShellParagraph] = []
    kept: list[KeptEmptyParagraph] = []
    cleaned_subs: list[Any] = []
    for sub_index, sub in enumerate(subs, 1):
        if not isinstance(sub, dict):
            cleaned_subs.append(sub)
            continue
        subsection_id = str(sub.get("subsection_id") or "")
        paragraphs = sub.get("paragraphs")
        if not isinstance(paragraphs, (list, tuple)):
            cleaned_subs.append(dict(sub))
            continue
        kept_paras: list[Any] = []
        for para_index, para in enumerate(paragraphs, 1):
            empty = (isinstance(para, dict)
                     and isinstance(para.get("sentences"), (list, tuple))
                     and not para.get("sentences"))
            if not empty:
                kept_paras.append(para)
                continue
            blocked_by = _empty_shell_blocked_by(para, subsection_id=subsection_id,
                                                 context=context)
            row = {"subsection_id": subsection_id, "subsection_index": sub_index,
                   "paragraph_index": para_index,
                   "paragraph_id": str(para.get("paragraph_id") or ""),
                   "aspect_ids": tuple(str(a).strip()
                                       for a in (para.get("aspect_ids") or ())
                                       if str(a).strip())}
            if blocked_by:
                kept.append(KeptEmptyParagraph(blocked_by=blocked_by, **row))
                kept_paras.append(para)
                continue
            #: 删除**依据**逐栏落台账（`crn-4`）：登记数与系统理由都照实记，缺一栏就少一行，
            #: 不替它编。理由码据此分流——全部栏零登记来源时走的仍是 `crn-3` 那条路。
            counts = {aspect: int(context.registered_source_counts.get(aspect, 0))
                      for aspect in row["aspect_ids"]}
            reasons = {aspect: context.aspect_unique_gap_reason(subsection_id, aspect)
                       for aspect in row["aspect_ids"]}
            reason = (EMPTY_SHELL_NO_SOURCE if all(n == 0 for n in counts.values())
                      else EMPTY_SHELL_GAP_SUPPORTED)
            dropped.append(DroppedEmptyShellParagraph(
                reason=reason, registered_source_counts=counts,
                system_gap_reasons=reasons, **row))
        cleaned_subs.append({**sub, "paragraphs": kept_paras})
    return {**payload, "subsections": cleaned_subs}, tuple(dropped), tuple(kept)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def normalize_cited_reply(payload: Any, *,
                          empty_shell: EmptyShellContext | None = None,
                          ) -> NormalizedCitedReply:
    """模型返回（一个 JSON 对象）→ 归一后的返回 + 四份台账。**唯一**入口。

    输入不被改动（内部先做浅层结构拷贝）：读回侧要同时保留原文与派生稿，两者的差额正是
    「这次归一改了什么」这句话本身。

    `empty_shell` 缺省为 `None`：那时**第三条规则不评估**（`empty_shell_rule` 记
    `not_evaluated_no_request_face`），行为与 `crn-1` 逐字相同。只有拿得到请求面读数的调用方
    （真实链的解析入口、读回侧的离线诊断）才交得出它。
    """
    payload = _reject_unknown_fields(payload, allowed=_REPLY_FIELDS,
                                     what="CitedProseDraft 返回",
                                     reason="reply_field_malformed")
    without_gaps, stripped = strip_duplicate_subsection_gaps(dict(payload))
    dropped: tuple[DroppedEmptyShellParagraph, ...] = ()
    kept: tuple[KeptEmptyParagraph, ...] = ()
    if empty_shell is None:
        #: 缺省路径**不跑**第三条规则：读不到请求面就不猜「这一栏有没有来源」。
        rule_state = EMPTY_SHELL_RULE_NOT_EVALUATED
    else:
        without_gaps, dropped, kept = drop_empty_shell_paragraphs(
            without_gaps, context=empty_shell)
        rule_state = EMPTY_SHELL_RULE_APPLIED
    normalized, assignments = assign_section_unique_sentence_ids(without_gaps)
    verify_section_unique_sentence_ids(normalized)
    return NormalizedCitedReply(payload=normalized, sentence_ids=assignments,
                                stripped_subsection_gaps=stripped,
                                dropped_empty_shell_paragraphs=dropped,
                                kept_empty_paragraphs=kept,
                                empty_shell_rule=rule_state)


def section_sentence_ids(payload: dict) -> tuple[str, ...]:
    """按遍历顺序取出全部 `sentence_id`（判唯一性用；不做任何改写）。"""
    ids: list[str] = []
    for sub in payload.get("subsections") or ():
        for para in (sub.get("paragraphs") or ()):
            for sentence in (para.get("sentences") or ()):
                ids.append(str(sentence.get("sentence_id") or ""))
    return tuple(ids)


def verify_section_unique_sentence_ids(payload: dict) -> None:
    """解析边界的**唯一性断言**：全节不得有两句共用同一个 `sentence_id`。

    归一之后这条等式由构造保证；这里再断一次，是为了让**绕过归一化**的任何路径（将来新增的
    调用方、手改的返回）在同一条线上失败——不同句被同一个 ID 合并，核对与审阅都会读错对象，
    而那种错误在结论里是看不出来的。
    """
    ids = section_sentence_ids(payload)
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in ids:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        _fail(f"全节句子 ID 不唯一：{duplicates}（共 {len(ids)} 句，"
              f"{len(seen)} 个不同 ID）", reason="sentence_id_duplicate")

    return None


def iter_gap_bodies(gaps: Iterable[Any]) -> tuple[dict, ...]:
    """顶层缺口的登记字段视图（读回侧渲染用；不重排、不改值）。"""
    return tuple(_gap_body(g) for g in gaps)

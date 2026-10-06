"""§0.20 第三步：**写后局部返修**，最多一次（`cwr-2` / `cwrp-3` / prompt `@crw-2`）。

`DESIGN_V2.md` §0.20 的最后一句是「必要时 Writer 最多有界返修一次，改后重新核对与审阅；
仍有问题的句子保留原文及标记，不因一处失败抹掉整节已有合法文字」。本模块就是那句话的执行
体：**一个**接口、**一轮**调用、改完**重跑**同一套逐句硬核对，然后无论成败都把两份草稿与两份
读数一并交回。

## 它**不是**什么（逐条对着 §0.20 写下来，免得后来者把它读成第二次自由写作）

* **不是重写整节。** 入参点名了「哪些句子有问题」（逐句硬错 + 调用方**明确**给出的语义待审句）。
  没有问题的句子与它们的引用**必须原样留在新稿里**——判据是逐字比对 `(text, citations)`，
  改动即 fail-closed。真实 cp-21 的 28 句里，按现行 `scp-7` 机械硬错 13 句、其余 15 句因此
  是**受保护**的（`scp-6` 下是 11 / 17；两个数都只在各自的核对版本下成立）。
* **不是后台换引用。** 新稿里出现**任何**基准草稿没用过的引用键，都必须在回复的
  `citation_changes` 里**逐条声明**（`{sentence_id, from, to}`）；没声明就 fail-closed。
  这一条把「静默改绑」变成「写明改绑」，后者才是可读、可复核的。
* **不是升级数字权威。** 金额与比率的资格仍只由 `scp-7` 的 `numeric_qualification` 轴判定，
  本模块**一个字都不改**那套判据，也**不**为返修稿放宽任何一个轴：返修稿重跑的是**同一个**
  `SC.check_cited_prose` 与**同一份** `input_manifest_id`。`ndc-4`（`cwrp-3`）只把初稿请求面
  已有的**派生**读数（`writable_fact_keys` / `numeric_authorization`）**投影**到返修输入面上，
  让返修者看到的「哪几条事实本版可以写成正文数字」与逐句核对**同源**——不是新判据，也不是
  新权威。
* **不是循环。** `CITED_REWORK_MAX_ROUNDS = 1` 是硬编码的上限，`rounds` 传别的值当场抛。
  本模块没有「再试一次」的入口，也不读上一次返修的结果。

## 三道留存的产物（失败路径下同样齐备）

一次返修无论成败都产出 :class:`CitedReworkOutcome`，它同时带上：

1. **原稿**（`base_draft_id` / `base_check_report_id` / 原硬错句集）——原稿本身**不被改写**，
   本模块不持有它的可变句柄；
2. **新稿**（`reworked_draft_id` / `reworked_check_report_id`）——解析成功就有，哪怕随后
   校验失败；解析失败时为 `None`；
3. **这次返修自己提的问题与去向**（`sentence_dispositions` / `changed_sentence_ids` /
   `withdrawn_sentence_ids` / `ambiguous_sentence_ids` / `declared_citation_changes` /
   `failure_reason`）。

「本节有多少句被重写了 / 被撤下了」必须在读回面上看得见：一次返修把整节清零，与一次返修
修好三句，是两件完全不同的事，混成一个「返修过了」的布尔就分不出来了。

### 去向台账为什么按**编号台账**算，而不是按 id 求交集（`cwr-2` 修的正是这条）

点名集合 `problem_sentence_ids` 里的 id 来自**初稿**；新稿的 id 已被
`sections.cited_reply_normalize.assign_section_unique_sentence_ids` **按遍历顺序重编**
（`derive_sentence_id(ordinal)` → `s0001…`）。两个编号空间**不同源**，对它们取交集得到的
既不是「改过的」也不是「留下的」，只是一个恒假的读数。

`cwr-2` 起改为：以归一化交回的 `assignments`（`model_id → final_id`）为**唯一**判据，
对每个点名 id 求它在回复里**实际出现**的最终编号：

* 恰好命中一句 ⇒ `kept`（它被改过或没被改过，都不是本表能判的；本表只说它还在）；
* 命中不止一句 ⇒ `ambiguous` / `id_reused_in_reply`（模型给两句写了同一个编号，拆句或缺号）；
* 一句也没命中，但**该句的 `(正文, 引用)` 逐字出现在一句模型编号之外的句子上** ⇒
  `ambiguous` / `id_absent_text_present`（同文换号，或同文重复的两句被并到一处）；
* 一句也没命中，但**该句正文被另一句的正文逐字吞进去**（真包含，且被吞正文 ≥ :data:`_ABSORB_MIN_CHARS`）
  ⇒ `ambiguous` / `id_absent_text_absorbed`（合句）；
* 以上都不成立 ⇒ `withdrawn`（这一句的 id 与正文都从新稿里消失了）。

`kept ∪ withdrawn ∪ ambiguous == problem_ids`，两两不相交，是 :func:`_validate_rework` 的
事后不变量。**不猜**：任何不能唯一对应的情形都落进 `ambiguous` 并带上闭集原因码，而不是被
算进两个计数里的某一个。

## 谁决定「哪几句待审」

机械硬错由 `SentenceCheckReport` 直接给出。这里读的是**两族并集**
（`hard_error_sentence_ids`），**不是** `blocked_sentence_ids`：自 `scp-10` 起后者只管
「系统按哪几句阻断放行」（只收事实安全族），而返修要修的是**全部**机械缺陷——栏目覆盖族
（`aspect_attribution` / `presentation_column_attribution`）的句子也必须可改，否则它们会被
当成受保护句，一改就把整次返修作废。两个口径都不是「这句事实是假的」：栏目覆盖只是说
「字与来源都成立，但服务错了栏目」。`hard_error_sentence_ids` 与 `scp-10` 之前
`blocked_sentence_ids` 的逐字口径相同，因此本模块对旧产物**行为不变**。

**语义待审句由调用方
显式给出**（`semantic_sentence_ids`），本模块不从审阅回复里**推**——`semantic_review_sentence_ids`
只做一件读取的事：把审阅自己判为**非** `supported` 的句子取出来。审阅说「没问题」的句子
不进返修：那是审阅的判断，机械门另有权拦它；本模块不替审阅翻案，也不替它补判。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from sections import cited_writer as CW
from sections import narrative_schema as NS
from sections import sentence_check as SC

__all__ = [
    "CITED_REWORK_SCHEMA_VERSION",
    "CITED_REWORK_POLICY_VERSION",
    "CITED_REWORK_PROMPT_ASSET",
    "CITED_REWORK_PROMPT_REVISION",
    "CITED_REWORK_PROMPT_VERSION",
    "CITED_REWORK_MAX_ROUNDS",
    "CITED_REWORK_RESULTS",
    "CITED_REWORK_FAILURE_REASONS",
    "CITED_REWORK_ENVELOPE_FIELDS",
    "REWORK_SENTENCE_DISPOSITIONS",
    "REWORK_AMBIGUITY_REASONS",
    "CitedReworkError",
    "ReworkSentenceDisposition",
    "resolve_sentence_dispositions",
    "CitedReworkRequest",
    "CitedReworkClient",
    "LlmCitedReworkClient",
    "CitedReworkOutcome",
    "semantic_review_sentence_ids",
    "build_cited_rework_request",
    "build_cited_rework_messages",
    "load_cited_rework_prompt",
    "parse_cited_rework_reply",
    "run_bounded_cited_rework",
]

#: 一次返修的**结果** wire 版本（`CitedReworkOutcome`）。字段增删即升版。
#: `cwr-1` → `cwr-2`：`changed_sentence_ids` / `withdrawn_sentence_ids` 的判据从「两个编号
#: 空间求交集」改成「按归一化编号台账逐句对应」，并新增 `ambiguous_sentence_ids` 与
#: `sentence_dispositions`。`cwr-1` 的这两个字段**恒错**，因此不保留旧口径——磁盘上不存在
#: 任何一份 `cwr-1` 产物（`evaluation/` 下没有 `cited_rework.json`），旧读数没有被改写。
CITED_REWORK_SCHEMA_VERSION = "cwr-2"

#: 返修政策版本（点名规则、受保护句判据、引用改绑的声明义务、轮次上限、失败留存、**输入面
#: 的数字授权投影**，任一变化即升版）。
#: `cwrp-2` → `cwrp-3`（`ndc-4`）：返修请求面开始携带**本版可写的数字键**
#: （`subsections[].columns[].writable_fact_keys`）与**逐轴数字授权块**（顶层
#: `numeric_authorization`），两者都是 `sections/cited_writer.py` 那一份**派生读视图**的逐字
#: 投影，读的是 `sections/sentence_check.py::fact_numeric_writability` 这一处实现。在此之前返修
#: 面只有 `citable_fact_keys`（Pack 侧**登记**），返修者据此把占比事实也当成可写数字键。
CITED_REWORK_POLICY_VERSION = "cwrp-3"

#: prompt 资产名与修订号；与既有 `*_vN@rev` 口径一致。资产名**不带扩展名**——`llm.load_prompt`
#: 自己拼 `.txt`，这里带上就会变成 `….txt.txt`，而报出来的错会是「资产不存在」。
#: `crw-1` → `crw-2`（`ndc-4`）：第 6 条「金额与比率不许升级」改成**金额／占比分列**——金额按
#: `writable_fact_keys` 里的合格金额事实（业务／期间／单位／来源）保留或改写；被
#: `numeric_authorization.withheld` 标出的事实（`denominator_unverified`，占比那一类）不得写成
#: 正文数字，撤下该数字并留结构化缺口；**撤一个占比不等于撤掉整段营收分析**。
CITED_REWORK_PROMPT_ASSET = "cited_prose_rework_v1"
CITED_REWORK_PROMPT_REVISION = "crw-2"
CITED_REWORK_PROMPT_VERSION = f"{CITED_REWORK_PROMPT_ASSET}@{CITED_REWORK_PROMPT_REVISION}"

#: **最多一次**。这不是可调参数：`run_bounded_cited_rework` 对别的值当场抛。
CITED_REWORK_MAX_ROUNDS = 1

#: 一次返修的闭集结论。
#:
#:   * `reworked`——新稿产出且**通过**了受保护句与引用改绑两道校验（**不代表**新稿已经零硬错，
#:     只代表「这次返修没有破坏原稿」）；新稿的硬错由 `reworked_check_report` 如实给出；
#:   * `not_needed`——点名集合为空（没有硬错句、也没有语义待审句）：**不发**调用，原稿就是
#:     结论。为一个没有问题的草稿花掉一次调用，是拿预算换一个恒真的动作；
#:   * `failed`——调用了但没通过（回复不可解析 / 受保护句被改 / 出现未声明的引用 / 受保护句
#:     被改出新硬错 / 调用本身失败）。原稿照旧是有效草稿。
CITED_REWORK_RESULTS = ("reworked", "not_needed", "failed")

#: 失败原因码闭集（**只**输出这几种，不输出自由文本）。
CITED_REWORK_FAILURE_REASONS = (
    "",
    "rework_rounds_out_of_range",
    "base_report_draft_mismatch",
    "base_report_manifest_mismatch",
    "rework_call_failed",
    "rework_reply_not_json",
    "rework_reply_not_object",
    "rework_reply_unknown_field",
    "rework_draft_invalid",
    "protected_sentence_altered",
    "protected_sentence_broken",
    "undeclared_citation_change",
    "citation_change_unknown_key",
    "citation_change_not_a_list",
    "citation_change_sentence_unknown",
    "citation_change_sentence_ambiguous",
    "citation_change_target_mismatch",
    "prompt_asset_identity_missing",
    "prompt_asset_identity_mismatch",
)

#: 回复信封允许的**全部**顶层键。前三个与写作侧 `crn-1` 逐字相同（同一份回复合约，不另立
#: 第二套正文形状）；第四个是返修**独有**的声明键。
CITED_REWORK_ENVELOPE_FIELDS = ("subsections", "gaps", "follow_up_needs", "citation_changes")

#: 与写作侧同一份正文形状（`crn-1`）——返修回复里的 `subsections` / `gaps` /
#: `follow_up_needs` 由它归一，本模块**不**另写一份。
_WRITER_ENVELOPE_FIELDS = ("subsections", "gaps", "follow_up_needs")

#: 一条引用改绑声明**必须**给出的键（缺一即该条不合格）。
_CITATION_CHANGE_FIELDS = ("sentence_id", "from", "to")

#: 一个点名句在返修稿里的**去向**闭集。三值互斥且穷尽点名集合。
REWORK_SENTENCE_DISPOSITIONS = ("kept", "withdrawn", "ambiguous")

#: `ambiguous` 的原因码闭集；`kept` / `withdrawn` 一律是空串。不是自由文本——「为什么这两句
#: 对不上」必须能被程序分开，否则「同文换号」与「合句」会混成一句「有歧义」。
REWORK_AMBIGUITY_REASONS = (
    "",
    "id_reused_in_reply",
    "id_absent_text_present",
    "id_absent_text_absorbed",
)

#: 参与「正文被吞」（合句）判定的**最短**被吞正文长度。短于此不下这个判断：两三字的残句
#: 在旁边任何一句里都可能碰巧出现，那不是证据。
_ABSORB_MIN_CHARS = 8


class CitedReworkError(RuntimeError):
    """返修面的事前/事后拒绝。带闭集 `reason`，**不**是「一次模型调用结果」。"""

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = str(reason or "")


# ---------------------------------------------------------------------------
# 回复解析：一份信封 + 一次委托
# ---------------------------------------------------------------------------


def _loads_reply(text: Any) -> Mapping[str, Any]:
    raw = str(text if text is not None else "").strip()
    if not raw:
        raise CitedReworkError("返修返回为空（没有可解析的正文）", reason="rework_reply_not_json")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CitedReworkError(f"返修返回不是一个完整 JSON 值：{exc}",
                               reason="rework_reply_not_json") from exc
    if not isinstance(payload, Mapping):
        raise CitedReworkError("返修返回的 JSON 顶层必须是对象",
                               reason="rework_reply_not_object")
    return payload


def _split_envelope(payload: Mapping[str, Any]) -> tuple[dict, tuple[dict, ...]]:
    """拆信封：返修**独有**的 `citation_changes` 取出来，正文那部分原样交给 `crn-1`。

    为什么在这里拆而不是把键加进 `crn-1`：`crn-1` 是**写作侧**的回复合约，本批不动它——
    动它等于给写作面也开一个「可以顺手换引用」的字段。拆信封让返修合约**严格包含**写作合约，
    两边各自 fail-closed。
    """
    unknown = [key for key in payload if key not in CITED_REWORK_ENVELOPE_FIELDS]
    if unknown:
        raise CitedReworkError(
            f"返修返回里出现未登记的顶层键 {sorted(unknown)}：本链只收 "
            f"{list(CITED_REWORK_ENVELOPE_FIELDS)}",
            reason="rework_reply_unknown_field")
    changes_raw = payload.get("citation_changes") or ()
    if not isinstance(changes_raw, (list, tuple)):
        raise CitedReworkError("citation_changes 必须是数组",
                               reason="citation_change_not_a_list")
    changes: list[dict] = []
    for row in changes_raw:
        if not isinstance(row, Mapping):
            raise CitedReworkError("citation_changes 的每一项必须是对象",
                                   reason="citation_change_not_a_list")
        missing = [k for k in _CITATION_CHANGE_FIELDS if k not in row]
        if missing:
            raise CitedReworkError(
                f"citation_changes 的一条声明缺字段 {missing}：改绑必须写明句子与前后引用",
                reason="citation_change_not_a_list")
        changes.append({"sentence_id": str(row.get("sentence_id") or ""),
                        "from": tuple(str(k) for k in (row.get("from") or ())),
                        "to": tuple(str(k) for k in (row.get("to") or ()))})
    body = {k: payload[k] for k in _WRITER_ENVELOPE_FIELDS if k in payload}
    return body, tuple(changes)


# ---------------------------------------------------------------------------
# 受保护句与引用改绑：两道可复核的判据
# ---------------------------------------------------------------------------


def _sentence_pairs(draft: CW.CitedProseDraft) -> dict[str, tuple[str, tuple[str, ...]]]:
    """草稿里每句的 `(正文, 引用键元组)` —— **只**取这两个面，不含段落与行序。

    不含段落：允许「移到语义与来源登记一致的栏目」这条合法动作（句子换段落时正文与引用
    一字未改）。不含行序：允许拆分与重排。两个面都改了才算「被改」。
    """
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                out[sentence.sentence_id] = (sentence.text,
                                             tuple(str(c) for c in sentence.citations))
    return out


def _citation_pool(draft: CW.CitedProseDraft) -> set[str]:
    return {key for _, keys in _sentence_pairs(draft).values() for key in keys}


def _declared_target_keys(changes: Sequence[Mapping[str, Any]]) -> set[str]:
    return {key for row in changes for key in (row.get("to") or ())}


def _resolve_citation_changes(
        changes: Sequence[Mapping[str, Any]], *, assignments: Sequence[Any],
        new_pairs: Mapping[str, tuple[str, tuple[str, ...]]]) -> tuple[dict, ...]:
    """把模型的改绑声明**落回返修稿的真实句子**上，并逐条核对「声明 == 实情」。

    这里必须先过 `assign_section_unique_sentence_ids` 的编号台账：归一化会把模型写的
    `sentence_id` 重编成全节唯一的 `sNNNN`（`crn-1`），因此声明里的 id 是**模型临时编号**，
    不解析台账就直接拿它去比对，会把每一句都读成「找不到」。`model_id → final_id` 是多对一
    不允许的：临时编号重复时（正是归一化要修的那种形），这条声明就是**歧义**的，按拒绝处理。

    声明不只被「收下」，还被**核对**：`to` 必须逐字等于那一句在返修稿里的真实引用集。
    只收不核的话，`citation_changes` 会退化成一张「事后可以随便往 `to` 里塞键」的白名单，
    静默换引只是换了种写法。
    """
    final_by_model: dict[str, list[str]] = {}
    for assignment in assignments:
        if str(assignment.final_id) in new_pairs:
            final_by_model.setdefault(str(assignment.model_id), []).append(
                str(assignment.final_id))
    resolved: list[dict] = []
    for row in changes:
        sid = str(row.get("sentence_id") or "")
        targets = [sid] if sid in new_pairs else final_by_model.get(sid, [])
        if len(targets) != 1:
            raise CitedReworkError(
                f"citation_changes 指向的句子 {sid!r} 在返修稿里"
                + ("找不到" if not targets else "有不止一句"),
                reason=("citation_change_sentence_unknown" if not targets
                        else "citation_change_sentence_ambiguous"))
        final_id = targets[0]
        actual = tuple(sorted(new_pairs[final_id][1]))
        declared_to = tuple(sorted(str(key) for key in (row.get("to") or ())))
        if actual != declared_to:
            raise CitedReworkError(
                f"citation_changes 为 {sid}（最终 {final_id}）声明的新引用 {list(declared_to)} "
                f"与该句在返修稿里的实际引用 {list(actual)} 不符：改绑声明必须与改后的正文一致",
                reason="citation_change_target_mismatch")
        resolved.append({"sentence_id": final_id, "model_sentence_id": sid,
                         "from": [str(key) for key in (row.get("from") or ())],
                         "to": list(declared_to)})
    return tuple(resolved)


@dataclass(frozen=True)
class ReworkSentenceDisposition:
    """**一个点名句**在返修稿里的去向。逐句一行，可回查、可反驳。

    `final_sentence_ids` 是这一句在返修稿里的最终编号（`kept` 恰好一个；`ambiguous` 可能多个
    或为空；`withdrawn` 恒空）。`ambiguity_reason` 只在 `ambiguous` 时非空，取自
    :data:`REWORK_AMBIGUITY_REASONS`。
    """

    base_sentence_id: str
    disposition: str
    final_sentence_ids: tuple[str, ...] = ()
    ambiguity_reason: str = ""

    def to_dict(self) -> dict:
        return {"base_sentence_id": self.base_sentence_id,
                "disposition": self.disposition,
                "final_sentence_ids": list(self.final_sentence_ids),
                "ambiguity_reason": self.ambiguity_reason}


def resolve_sentence_dispositions(
        *, base_pairs: Mapping[str, tuple[str, tuple[str, ...]]],
        reply_pairs: Mapping[str, tuple[str, tuple[str, ...]]],
        assignments: Sequence[Any],
        problem_ids: Sequence[str]) -> tuple[ReworkSentenceDisposition, ...]:
    """点名 id → 返修稿去向。**判据只有一条**：归一化交回的 `model_id → final_id` 台账。

    `base_pairs` 是**初稿**每句的 `(正文, 引用)`，`reply_pairs` 是**返修稿**每句的同一个面，
    `assignments` 是 `sections.cited_reply_normalize.assign_section_unique_sentence_ids`
    的记录（`model_id` = 模型回复里写的编号，`final_id` = 重编后的全节唯一编号）。

    这里**不**对比两个编号空间的 id，只问「这一句在回复里出现了吗、出现在哪里」。判不出唯一
    对应就记 `ambiguous` 并写明原因，绝不挑一个塞进计数（详见模块 docstring 那张表）。
    """
    final_by_model: dict[str, list[str]] = {}
    for assignment in assignments:
        final_id = str(getattr(assignment, "final_id", "") or "")
        if final_id and final_id in reply_pairs:
            final_by_model.setdefault(str(getattr(assignment, "model_id", "") or ""),
                                      []).append(final_id)
    base_ids = set(base_pairs)
    #: 回复里**模型写了初稿没出现过的编号**的那些句子——同文换号只可能藏在这里。
    unmapped_pairs = {reply_pairs[final_id]
                      for model_id, finals in final_by_model.items()
                      if model_id not in base_ids
                      for final_id in finals}

    rows: list[ReworkSentenceDisposition] = []
    for base_id in problem_ids:
        finals = tuple(sorted(final_by_model.get(base_id) or ()))
        if len(finals) == 1:
            rows.append(ReworkSentenceDisposition(base_id, "kept", finals))
            continue
        if len(finals) > 1:
            rows.append(ReworkSentenceDisposition(base_id, "ambiguous", finals,
                                                  "id_reused_in_reply"))
            continue
        pair = base_pairs.get(base_id)
        text = pair[0] if pair is not None else ""
        if pair is not None and pair in unmapped_pairs:
            rows.append(ReworkSentenceDisposition(base_id, "ambiguous", (),
                                                  "id_absent_text_present"))
        elif len(text) >= _ABSORB_MIN_CHARS and any(
                other != text and text in other for other, _ in reply_pairs.values()):
            rows.append(ReworkSentenceDisposition(base_id, "ambiguous", (),
                                                  "id_absent_text_absorbed"))
        else:
            rows.append(ReworkSentenceDisposition(base_id, "withdrawn", ()))
    return tuple(rows)


def _validate_rework(*, base_draft: CW.CitedProseDraft, draft: CW.CitedProseDraft,
                     manifest: CW.CitedWriterInputManifest, problem_ids: Sequence[str],
                     citation_changes: Sequence[Mapping[str, Any]],
                     assignments: Sequence[Any],
                     base_pairs: Mapping[str, tuple[str, tuple[str, ...]]]) -> dict[str, Any]:
    """受保护句 + 引用改绑两道判据。**通过**才返回读数，否则抛 `CitedReworkError`。"""
    protected = {sid: pair for sid, pair in base_pairs.items() if sid not in set(problem_ids)}
    new_pairs = _sentence_pairs(draft)
    new_pool = {key for _, keys in new_pairs.values() for key in keys}

    # ---- 判据 1：受保护句必须逐字（正文 + 引用）原样在场 -------------------
    new_multiset: list[tuple[str, tuple[str, ...]]] = list(new_pairs.values())
    for sid, pair in protected.items():
        # 多重集包含：同一段文字在基准稿里出现两次时，新稿也得出现两次（少一次就是删了一处）
        if pair in new_multiset:
            new_multiset.remove(pair)
        else:
            raise CitedReworkError(
                f"受保护句 {sid} 的正文或引用被改动了：本接口只允许改点名的句子"
                f"（正文 {pair[0][:24]!r}…，引用 {list(pair[1])}）",
                reason="protected_sentence_altered")

    # ---- 判据 2：改绑声明逐条落到真实句子上，且与实情一致 -----------------
    resolved = _resolve_citation_changes(citation_changes, assignments=assignments,
                                         new_pairs=new_pairs)
    declared = _declared_target_keys(resolved)
    known_keys = set(manifest.all_keys())
    unknown = sorted({key for key in declared if key not in known_keys})
    if unknown:
        raise CitedReworkError(
            f"citation_changes 声明的键 {unknown} 不在本次输入清单里：改绑只能落到清单内的行",
            reason="citation_change_unknown_key")

    # ---- 判据 3：新出现的引用键必须**全部**在声明里 -----------------------
    undeclared = sorted(new_pool - _citation_pool(base_draft) - declared)
    if undeclared:
        raise CitedReworkError(
            f"返修稿里出现未声明的引用键 {undeclared}：改绑必须在 `citation_changes` 里"
            "逐条写明（句子、原引用、新引用），本链不接受后台静默换引",
            reason="undeclared_citation_change")
    # ---- 判据 4：点名句的去向按编号台账逐句算，并**事后核账** ---------------
    # 不变量：三桶互斥、并起来正好是点名集合。这条不满足就是本模块自己的 bug，不是模型的
    # 问题——所以它在这儿当场抛，不允许一份自相矛盾的台账落盘。
    dispositions = resolve_sentence_dispositions(
        base_pairs=base_pairs, reply_pairs=new_pairs, assignments=assignments,
        problem_ids=tuple(str(sid) for sid in problem_ids))
    by_disposition = {name: tuple(row.base_sentence_id for row in dispositions
                                  if row.disposition == name)
                      for name in REWORK_SENTENCE_DISPOSITIONS}
    tally = [sid for ids in by_disposition.values() for sid in ids]
    if len(tally) != len(dispositions) or set(tally) != {str(s) for s in problem_ids}:
        raise CitedReworkError(
            "返修去向台账自相矛盾：点名 "
            f"{sorted({str(s) for s in problem_ids})}，台账算出 {sorted(tally)}",
            reason="rework_draft_invalid")
    return {"protected_sentence_count": len(protected),
            "sentence_dispositions": dispositions,
            "changed_sentence_ids": by_disposition["kept"],
            "withdrawn_sentence_ids": by_disposition["withdrawn"],
            "ambiguous_sentence_ids": by_disposition["ambiguous"],
            "declared_citation_changes": resolved}


# ---------------------------------------------------------------------------
# 请求面
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitedReworkRequest:
    """一次返修的**输入面**（`cwr-2` 的请求体 + 它自己的身份）。

    `payload` 是**请求面原文**，调用方原样发给模型；`request_id` 由它内容寻址。带上
    `base_draft_id` / `base_check_report_id`：重放时必须能证明「这一次返修读的是哪一份原稿、
    哪一份硬错读数」——少了它，事后回查只能证明「发生过一次返修」。
    """

    request_id: str
    base_draft_id: str
    base_check_report_id: str
    input_manifest_id: str
    problem_sentence_ids: tuple[str, ...]
    semantic_sentence_ids: tuple[str, ...]
    payload: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "base_draft_id": self.base_draft_id,
                "base_check_report_id": self.base_check_report_id,
                "input_manifest_id": self.input_manifest_id,
                "problem_sentence_ids": list(self.problem_sentence_ids),
                "semantic_sentence_ids": list(self.semantic_sentence_ids),
                "payload": dict(self.payload)}


def semantic_review_sentence_ids(review_outcome: Any) -> tuple[str, ...]:
    """审阅自己**判为有问题**的句子（`category != "supported"`）。

    只做读取：审阅说「没问题」的句子不进返修——那是它的判断，本模块不翻案、不补判、也不
    把「一律 supported」当成「一律要返修」（那会把一条粗暴规则装进返修面）。
    """
    if review_outcome is None:
        return ()
    issues = tuple(getattr(review_outcome, "issues", ()) or ())
    return tuple(dict.fromkeys(
        str(issue.sentence_id) for issue in issues
        if str(getattr(issue, "category", "")) not in ("", "supported")))


def _material_row(material: CW.CitedMaterialEntry) -> dict:
    """返修请求面里的一条来源行。**逐字**给出模型要重写时唯一该依据的东西。"""
    return {"key": material.citation_key, "aspect_ids": list(material.aspect_ids),
            "source_role": material.source_role, "document_id": material.document_id,
            "content_kind": material.content_kind, "is_table": material.structured_view is not None,
            "locator": dict(material.locator_ref), "text": material.reading_view}


def build_cited_rework_request(
        *, draft: CW.CitedProseDraft, check_report: SC.SentenceCheckReport,
        manifest: CW.CitedWriterInputManifest,
        semantic_sentence_ids: Sequence[str] = ()) -> CitedReworkRequest:
    """装配返修请求面。两道前置等式 fail-closed，与逐句核对器同源：

    * `draft.input_manifest_id == manifest.manifest_id`（否则「同一份清单」这句话不成立）；
    * `check_report.draft_id == draft.draft_id`（否则点名的硬错句可能来自**别的**草稿）。
    """
    if str(draft.input_manifest_id) != str(manifest.manifest_id):
        raise CitedReworkError(
            "草稿与清单不是同一次装配（input_manifest_id 对不上）：返修不得跨清单",
            reason="base_report_manifest_mismatch")
    if str(check_report.draft_id) != str(draft.draft_id):
        raise CitedReworkError(
            "逐句核对报告不是这份草稿的（draft_id 对不上）：点名返修必须落在同一份原稿上",
            reason="base_report_draft_mismatch")

    #: 返修点名的句子 = **两族并集**的机械硬错句（见模块 docstring）。用 `blocked_sentence_ids`
    #: 会把栏目覆盖族的句子误当成受保护句（`scp-10` 起该属性只收事实安全族）。
    problem_ids = tuple(dict.fromkeys(str(s) for s in check_report.hard_error_sentence_ids))
    semantic_ids = tuple(dict.fromkeys(
        str(s) for s in semantic_sentence_ids if str(s))) if semantic_sentence_ids else ()

    # ---- 硬错逐条：轴 + 原因码 + 表面 + 所引键。**不**带自由文本建议 --------
    problems: list[dict] = []
    for record in check_report.records:
        if record.verdict != "hard_error":
            continue
        problems.append({"sentence_id": record.sentence_id,
                         "check_kind": record.check_kind,
                         "failure_reason": record.failure_reason,
                         "surfaces": list(record.surfaces),
                         "citation_keys": list(record.citation_keys)})

    # ---- 只送**被引到**的材料原文 + 逐栏的候选键表 -------------------------
    # 理由与审阅面同源：把整份清单倒进来，模型就会去改正文没写过的东西。返修只需要
    # 「它自己引到的那几行」的原文，加上「本小节各栏登记了哪些键」这张表——后者只给键，
    # 不给正文，于是「改绑到一行自己没读过的原文」这件事在输入面上就不成立，除非它按
    # `citation_changes` 声明（声明之后仍要过同一套逐句核对）。
    #
    # `ndc-4`：这张键表里再加两根**同源**读数——逐栏的 `writable_fact_keys` 与顶层
    # `numeric_authorization`。它们**不**新造候选、**不**动登记语义，只是把「哪几条事实本版
    # 可以写成正文数字」这件事从初稿请求面**投影**过来：少了它们，返修面只有
    # `citable_fact_keys`（登记），返修者就会把占比事实也当成可写数字键——正是 `ndc-3` 暴露的
    # 那处自相矛盾在返修面上的复刻。
    pairs = _sentence_pairs(draft)
    cited = {key for _, keys in pairs.values() for key in keys}
    rows = [_material_row(m) for m in manifest.materials if m.citation_key in cited]
    columns = []
    for spec in manifest.subsections:
        columns.append({"subsection_id": spec.subsection_id,
                        "declared_aspect_ids": list(spec.declared_aspect_ids),
                        "columns": [{"aspect_id": col["aspect_id"],
                                     "requirement_text": col["requirement_text"],
                                     "citable_material_keys": list(col["citable_material_keys"]),
                                     "citable_fact_keys": list(col["citable_fact_keys"]),
                                     #: **本版可写的**数字事实键（`ndc-4`）。它是
                                     #: `CW.writable_fact_keys` 的**逐字投影**——与初稿请求面同一份
                                     #: 派生读视图，因此两处的 9／3 分法不可能分家。`citable_fact_keys`
                                     #: 仍是 Pack 侧**登记**，登记语义一字未动（被撤回的键仍在其中）。
                                     "writable_fact_keys": list(
                                         CW.writable_fact_keys(manifest, col["aspect_id"]))}
                                    for col in CW.citable_columns(manifest, spec)]})

    payload: dict[str, Any] = {
        "task_id": manifest.task_id, "section_id": manifest.section_id,
        "section_title": manifest.section_title,
        "base_draft": {"draft_id": draft.draft_id,
                       "subsections": [sub.to_dict() for sub in draft.subsections]},
        "problems": problems,
        "problem_sentence_ids": list(problem_ids),
        "semantic_sentence_ids": list(semantic_ids),
        "materials": rows,
        "subsections": columns,
        #: **逐轴数字授权块**（`ndc-4`）：与初稿请求面顶层那一块**同一份**派生读视图
        #: （`CW.numeric_authorization`），逐字投影，不另写一份。返修者据此知道哪几条事实本版
        #: 可以写成正文数字、哪几条不可以、不可以的那个原因码叫什么。
        "numeric_authorization": CW.numeric_authorization(manifest=manifest),
        "citation_changes_contract": {
            "sentence_id": "改绑后那一句的临时 ID（与返回里逐字相同）",
            "from": ["原引用键"], "to": ["新引用键"],
        },
    }
    request_id = NS.content_id("cwrq_", {"base_draft_id": draft.draft_id,
                                         "base_check_report_id": check_report.report_id,
                                         "input_manifest_id": manifest.manifest_id,
                                         "problem_sentence_ids": list(problem_ids),
                                         "semantic_sentence_ids": list(semantic_ids),
                                         "payload": payload})
    return CitedReworkRequest(request_id=request_id, base_draft_id=draft.draft_id,
                              base_check_report_id=check_report.report_id,
                              input_manifest_id=manifest.manifest_id,
                              problem_sentence_ids=problem_ids,
                              semantic_sentence_ids=semantic_ids, payload=payload)


def load_cited_rework_prompt() -> str:
    """加载返修 system prompt，**加载即对账**（与写作侧 `load_cited_writer_prompt` 同一纪律）。"""
    from llm import client as llm
    text = llm.load_prompt(CITED_REWORK_PROMPT_ASSET)
    declared = CW.declared_prompt_identity(text)
    if declared is None:
        raise CitedReworkError(
            f"prompt 资产 {CITED_REWORK_PROMPT_ASSET!r} 第 1 行没有声明的 "
            f"`（<asset>，revision <rev>）`，无法与 CITED_REWORK_PROMPT_REVISION 对账",
            reason="prompt_asset_identity_missing")
    asset, revision = declared
    if (asset, revision) != (CITED_REWORK_PROMPT_ASSET, CITED_REWORK_PROMPT_REVISION):
        raise CitedReworkError(
            f"prompt 资产自称 {asset}@{revision}，本链声明的是 {CITED_REWORK_PROMPT_VERSION}："
            "账本上的 prompt_version 将不对应任何字节",
            reason="prompt_asset_identity_mismatch")
    return text


def build_cited_rework_messages(request: CitedReworkRequest, *,
                                system: str) -> list[dict]:
    """请求面 → messages。**一个** user 消息，内容是请求面的 canonical JSON。"""
    return [{"role": "user", "content": NS.canonical_json(dict(request.payload))}]


# ---------------------------------------------------------------------------
# 调用面
# ---------------------------------------------------------------------------


class CitedReworkClient(Protocol):
    """返修能拿到的全部外部能力。与写作客户端同一形状，**没有**任何检索/工具入口。"""

    def compose(self, *, messages: Sequence[Mapping[str, str]], system: str,
                prompt_version: str, model_policy: str) -> CW.CitedProseResult: ...


class LlmCitedReworkClient:
    """真实客户端适配器：与 `LlmCitedProseClient` 同一实现，只是 prompt 归属换成返修那一支。"""

    def __init__(self, *, model: str | None = None, max_tokens: int = 8192,
                 thinking: Mapping[str, Any] | None = None,
                 reject_truncated: bool = True) -> None:
        self._inner = CW.LlmCitedProseClient(
            model=model, max_tokens=max_tokens,
            thinking=dict(thinking) if thinking else None,
            reject_truncated=reject_truncated)

    @property
    def calls(self) -> list[dict]:
        """本适配器**真正发起**的调用（含失败），与写作侧同名属性对齐。"""
        return self._inner.calls

    def compose(self, *, messages, system: str, prompt_version: str,
                model_policy: str) -> CW.CitedProseResult:
        return self._inner.compose(messages=messages, system=system,
                                   prompt_version=prompt_version,
                                   model_policy=model_policy)


def parse_cited_rework_reply(
        text: Any, *, base_draft: CW.CitedProseDraft,
        manifest: CW.CitedWriterInputManifest, writer_identity: str,
        problem_ids: Sequence[str],
) -> tuple[CW.CitedProseDraft, tuple[dict, ...], dict[str, Any]]:
    """返修回复 → `(新草稿, 引用改绑声明, 读数)`。四关全过才返回，否则抛。"""
    payload = _loads_reply(text)
    body, changes = _split_envelope(payload)
    base_pairs = _sentence_pairs(base_draft)
    try:
        draft, normalization = CW.parse_cited_prose_with_normalization(
            json.dumps(body, ensure_ascii=False), manifest=manifest,
            writer_identity=writer_identity)
    except CW.CitedWriterError as exc:
        raise CitedReworkError(f"返修稿没有通过写作侧的同一套解析校验：{exc}",
                               reason="rework_draft_invalid") from exc
    reading = _validate_rework(base_draft=base_draft, draft=draft, manifest=manifest,
                               problem_ids=problem_ids, citation_changes=changes,
                               assignments=tuple(getattr(normalization, "sentence_ids", ()) or ()),
                               base_pairs=base_pairs)
    return draft, reading["declared_citation_changes"], reading


# ---------------------------------------------------------------------------
# 编排：**一轮**，且只有一轮
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitedReworkOutcome:
    """一次有界返修的完整读数。**两份草稿、两份硬错读数、一个问题去向表**，无论成败。"""

    schema_version: str
    policy_version: str
    outcome: str
    rounds: int
    base_draft_id: str
    base_check_report_id: str
    input_manifest_id: str
    problem_sentence_ids: tuple[str, ...] = ()
    semantic_sentence_ids: tuple[str, ...] = ()
    request_id: str = ""
    call: Mapping[str, Any] | None = None
    failure_reason: str = ""
    failure_detail: str = ""
    draft: CW.CitedProseDraft | None = field(default=None, repr=False)
    check_report: SC.SentenceCheckReport | None = field(default=None, repr=False)
    protected_sentence_count: int = 0
    changed_sentence_ids: tuple[str, ...] = ()
    withdrawn_sentence_ids: tuple[str, ...] = ()
    ambiguous_sentence_ids: tuple[str, ...] = ()
    sentence_dispositions: tuple[ReworkSentenceDisposition, ...] = ()
    declared_citation_changes: tuple[Mapping[str, Any], ...] = ()

    @property
    def disposition_counts(self) -> dict[str, int]:
        """三桶计数。**派生读数**，不落盘：它由 `sentence_dispositions` 现算，
        免得两个字段将来对不上。"""
        return {name: sum(1 for row in self.sentence_dispositions
                          if row.disposition == name)
                for name in REWORK_SENTENCE_DISPOSITIONS}

    @property
    def reworked_draft_id(self) -> str:
        return self.draft.draft_id if self.draft is not None else ""

    @property
    def reworked_check_report_id(self) -> str:
        return self.check_report.report_id if self.check_report is not None else ""

    @property
    def hard_error_sentence_ids(self) -> tuple[str, ...]:
        """**返修稿**的硬错句（解析成功才有）。原稿的硬错在调用方手里（它传进来的那份）。

        取**两族并集**（`hard_error_sentence_ids`）而不是 `blocked_sentence_ids`：这个名字问的是
        「机械层还标着几句」，栏目覆盖族的残留同样算没修干净；而「按哪几句阻断放行」是另一个
        问题（`scp-10` 起只收事实安全族），由 :attr:`blocked_sentence_ids` 那条口径回答。
        """
        if self.check_report is None:
            return ()
        return tuple(self.check_report.hard_error_sentence_ids)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "policy_version": self.policy_version,
            "outcome": self.outcome, "rounds": self.rounds,
            "base_draft_id": self.base_draft_id,
            "base_check_report_id": self.base_check_report_id,
            "input_manifest_id": self.input_manifest_id,
            "problem_sentence_ids": list(self.problem_sentence_ids),
            "semantic_sentence_ids": list(self.semantic_sentence_ids),
            "request_id": self.request_id, "call": dict(self.call) if self.call else None,
            "failure_reason": self.failure_reason, "failure_detail": self.failure_detail,
            "reworked_draft_id": self.reworked_draft_id,
            "reworked_check_report_id": self.reworked_check_report_id,
            "protected_sentence_count": self.protected_sentence_count,
            "changed_sentence_ids": list(self.changed_sentence_ids),
            "withdrawn_sentence_ids": list(self.withdrawn_sentence_ids),
            "ambiguous_sentence_ids": list(self.ambiguous_sentence_ids),
            "sentence_dispositions": [row.to_dict() for row in self.sentence_dispositions],
            "declared_citation_changes": [dict(row) for row in self.declared_citation_changes],
            "reworked_mechanical_verdict": (
                self.check_report.mechanical_verdict if self.check_report else ""),
            "reworked_hard_error_sentence_ids": list(self.hard_error_sentence_ids),
        }


def _outcome(**kwargs: Any) -> CitedReworkOutcome:
    return CitedReworkOutcome(schema_version=CITED_REWORK_SCHEMA_VERSION,
                              policy_version=CITED_REWORK_POLICY_VERSION, **kwargs)


def _failure(*, base_draft: CW.CitedProseDraft, check_report: SC.SentenceCheckReport,
             problem_ids: Sequence[str], semantic_ids: Sequence[str], reason: str,
             detail: str, call: Mapping[str, Any] | None = None,
             draft: CW.CitedProseDraft | None = None,
             request_id: str = "") -> CitedReworkOutcome:
    # 受保护句与成功态**同一口径**（初稿句数 − 点名句数）：返修失败没有改动任何一句，
    # 原稿整体仍是有效草稿。写 0 会让「受保护 + 点名 == 初稿句数」在失败态上假失败。
    named = {str(s) for s in problem_ids} | {str(s) for s in semantic_ids}
    protected = sum(1 for sid in _sentence_pairs(base_draft) if sid not in named)
    return _outcome(outcome="failed", rounds=1, base_draft_id=base_draft.draft_id,
                    base_check_report_id=check_report.report_id,
                    input_manifest_id=base_draft.input_manifest_id,
                    problem_sentence_ids=tuple(problem_ids),
                    semantic_sentence_ids=tuple(semantic_ids),
                    protected_sentence_count=protected,
                    request_id=request_id, call=call, failure_reason=reason,
                    failure_detail=detail, draft=draft)


def run_bounded_cited_rework(
        *, draft: CW.CitedProseDraft, check_report: SC.SentenceCheckReport,
        manifest: CW.CitedWriterInputManifest, client: CitedReworkClient,
        semantic_sentence_ids: Sequence[str] = (), model_policy: str = "",
        rounds: int = CITED_REWORK_MAX_ROUNDS) -> CitedReworkOutcome:
    """**一轮**局部返修。没有循环、没有重试、没有第二次机会。

    点名集合为空 ⇒ 不发调用（`not_needed`）。这正是「不因一处失败抹掉整节已有合法文字」的
    另一半：**没有失败时也不要凭空重写**。

    `protected_sentence_count` 在三态上**同一口径**：初稿句数 − 点名句数。没有点名的句子就是
    受保护的句子，因此「点名集合为空 ⇒ 全部受保护」，不是 0。三态各写各的会让
    「受保护 + 点名 == 初稿句数」这条可核等式只在一半的终态上成立。
    """
    if int(rounds) != CITED_REWORK_MAX_ROUNDS:
        raise CitedReworkError(
            f"返修轮次只能是 {CITED_REWORK_MAX_ROUNDS}（本批只批了一次）：得到 {rounds}",
            reason="rework_rounds_out_of_range")
    request = build_cited_rework_request(
        draft=draft, check_report=check_report, manifest=manifest,
        semantic_sentence_ids=semantic_sentence_ids)
    if not request.problem_sentence_ids and not request.semantic_sentence_ids:
        return _outcome(outcome="not_needed", rounds=1, base_draft_id=draft.draft_id,
                        base_check_report_id=check_report.report_id,
                        input_manifest_id=manifest.manifest_id,
                        request_id=request.request_id,
                        protected_sentence_count=len(draft.sentence_ids()))

    try:
        system = load_cited_rework_prompt()
        result = client.compose(messages=build_cited_rework_messages(request, system=system),
                                system=system, prompt_version=CITED_REWORK_PROMPT_VERSION,
                                model_policy=model_policy)
    except CitedReworkError as exc:
        return _failure(base_draft=draft, check_report=check_report,
                        problem_ids=request.problem_sentence_ids,
                        semantic_ids=request.semantic_sentence_ids,
                        reason=str(exc.reason or "rework_call_failed"), detail=str(exc),
                        request_id=request.request_id)
    except Exception as exc:                                            # noqa: BLE001
        return _failure(base_draft=draft, check_report=check_report,
                        problem_ids=request.problem_sentence_ids,
                        semantic_ids=request.semantic_sentence_ids,
                        reason="rework_call_failed", detail=str(exc),
                        request_id=request.request_id)
    call = {"call_id": result.call_id, "model": result.model,
            "prompt_version": result.prompt_version, "status": result.status,
            "response_hash": result.response_hash}
    if result.status != "ok":
        return _failure(base_draft=draft, check_report=check_report,
                        problem_ids=request.problem_sentence_ids,
                        semantic_ids=request.semantic_sentence_ids,
                        reason="rework_call_failed",
                        detail=str(result.error or "返修调用返回非 ok"),
                        call=call, request_id=request.request_id)

    named = tuple(request.problem_sentence_ids) + tuple(request.semantic_sentence_ids)
    try:
        new_draft, changes, reading = parse_cited_rework_reply(
            result.text, base_draft=draft, manifest=manifest,
            writer_identity=result.call_id, problem_ids=named)
    except CitedReworkError as exc:
        return _failure(base_draft=draft, check_report=check_report,
                        problem_ids=request.problem_sentence_ids,
                        semantic_ids=request.semantic_sentence_ids,
                        reason=str(exc.reason or "rework_draft_invalid"), detail=str(exc),
                        call=call, request_id=request.request_id)

    new_report = SC.check_cited_prose(draft=new_draft, manifest=manifest)

    # ---- 受保护句不得被**改出新硬错**（改的是它所在的段落也算）--------------
    base_pairs = _sentence_pairs(draft)
    protected_pairs = {pair for sid, pair in base_pairs.items() if sid not in set(named)}
    for record in new_report.records:
        if record.verdict != "hard_error":
            continue
        pair = _sentence_pairs(new_draft).get(record.sentence_id)
        if pair is not None and pair in protected_pairs:
            return _failure(
                base_draft=draft, check_report=check_report,
                problem_ids=request.problem_sentence_ids,
                semantic_ids=request.semantic_sentence_ids,
                reason="protected_sentence_broken",
                detail=(f"受保护句 {record.sentence_id} 在返修稿里新出现硬错 "
                        f"{record.check_kind}/{record.failure_reason}：返修不得把没点名的句子"
                        "改坏"),
                call=call, draft=new_draft, request_id=request.request_id)

    return _outcome(
        outcome="reworked", rounds=1, base_draft_id=draft.draft_id,
        base_check_report_id=check_report.report_id,
        input_manifest_id=manifest.manifest_id,
        problem_sentence_ids=request.problem_sentence_ids,
        semantic_sentence_ids=request.semantic_sentence_ids,
        request_id=request.request_id, call=call, draft=new_draft, check_report=new_report,
        protected_sentence_count=int(reading["protected_sentence_count"]),
        changed_sentence_ids=tuple(reading["changed_sentence_ids"]),
        withdrawn_sentence_ids=tuple(reading["withdrawn_sentence_ids"]),
        ambiguous_sentence_ids=tuple(reading["ambiguous_sentence_ids"]),
        sentence_dispositions=tuple(reading["sentence_dispositions"]),
        declared_citation_changes=tuple(reading["declared_citation_changes"]))

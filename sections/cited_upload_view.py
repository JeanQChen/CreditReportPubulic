"""`cuv-1`：读回**本 run 自己**的产物，作为第 3 屏的唯一内容来源。

为什么不能直接复用 `cdb-1`
---------------------------

`sections/cited_demo_binding.py` 是**历史对照**的展示绑定：它把 run id 与 17 个产物的完整
SHA-256 写死在源码里，并且（这是关键）它要求逐句审阅的 `call_id` 出现在**那个** run 的成功
调用账本里，还要求 `publishability == "not_publishable"`。把新 run 塞进那道门只有两种结果：
要么永远进不去（账本对不上），要么为了让新 run 进去而放宽那道门——后者会把历史对照的钉住
价值一起作废。所以本 run 的页面**另开一条读法**。

本模块做什么
------------

只做一件事：把一个运行目录里的产物**按它们自己的身份**读回来，并如实报出四件事：

* 这份正文是谁产出的（`writer_producer`：**真实模型** / **离线替身** / **无法核实** 三档之一）；
  这一档**不是**单一字段的读数——它把本 run 自己的写作调用记录、`cited_call_journal.json`
  记的输入策略、以及整轮账本里同一 `call_id`／节／类别／模型／成功状态**三处交叉核对**后
  才敢下判断；三处缺一则记「无法核实」，三处互相矛盾也记「无法核实」。**只看 `mode=real`
  或只看一个字段就宣称「这是模型写的」是这一档要挡掉的那种误读。**
* 这次审阅是不是独立 LLM 审阅，调用有没有落在**本 run 自己的**成功账本里
  （`review_is_model` / `review_attested`）；
* `publishability` / 四个状态轴**原样读出**，不做任何断言、不预设通过也不预设不通过；
* 运行输入绑定在运行目录里与暂存目录里是否**逐字一致**（`run_input_verified`）。

校验仍然 fail-closed：`report_version` ↔ 清单 ↔ 草稿 ↔ 逐句核对 ↔ 审阅 ↔ 缺口分箱 ↔ 原 PDF
展示集，任一处对不上即拒，不给「降级为仍可看」这条路。区别只在于——**它不要求产物必须
「不可发布」，也不要求审阅必须是真实调用**。那两条对一个刚跑完的离线 run 是假的，用它们当
入口条件等于把「诚实展示未审」变成「打不开页面」。

产物布局
--------

单节 run（`--section company`）逐字沿用扁平布局，全部文件在 run 根；多节 run 每节在
`<run_id>/<section_id>/`。两种都能读，因为本模块按 `<section_id>/` 与根各探一次，并以文件
自己声明的 `section_id` 为准，不靠目录名猜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sections import cited_budget
from sections import cited_financial_input
from sections import cited_financial_table as financial_table
from sections import cited_report
from sections import cited_review
from sections import cited_run_input
from sections import cited_run_journal
from sections import cited_writer
from sections import sentence_check
from sections import cited_demo_loader as _loader


UPLOAD_VIEW_VERSION = "cuv-1"

#: 一次审阅算不算「独立 LLM 审阅」。常量取自 `cited_review` 自己登记的那一组（由 client 本身
#: 推导，调用方无从谎报），不在这里另立一份名单。
INDEPENDENT_REVIEW_KIND = "independent_llm_review"
if INDEPENDENT_REVIEW_KIND not in cited_review.CITED_REVIEW_PRODUCER_KINDS:  # pragma: no cover
    raise RuntimeError(
        f"`cited_review.CITED_REVIEW_PRODUCER_KINDS` 里已经没有 {INDEPENDENT_REVIEW_KIND!r}："
        "本模块的「是不是真实独立审阅」这一读数失去依据，必须同步更新")

#: 写作产出者的**三档**读数。它回答的是「这一节正文到底是谁写的」，而不是「这次运行是什么
#: 模式」。三档之间不是「通过/不通过」，而是「有证据是模型」「有证据是离线替身」「证据不足或
#: 互相矛盾，无法核实」——第三档必须**显式**存在，否则「读不到证据」会被悄悄写进前两档里的
#: 某一档，那正是本批要修的那种误标。
PRODUCER_MODEL = "model"            #: 三处证据一致指向真实模型
PRODUCER_STANDIN = "standin"        #: 三处证据一致指向离线替身
PRODUCER_UNVERIFIABLE = "unverifiable"  #: 证据缺失或互相矛盾；不写成前两档里的任何一档
PRODUCER_READINGS = (PRODUCER_MODEL, PRODUCER_STANDIN, PRODUCER_UNVERIFIABLE)

#: 本 run 写作调用的类别标签。取自 `cited_budget` **自己登记**的那一组（唯一真值），不在
#: 这里另写一份字面量。账本的 `axis` 对写作与审阅都是 `"writing"`，判别只能靠 `category`，
#: 不能靠 `axis`——本批的误标正与此有关。
CITED_WRITING_CATEGORY = cited_budget.CATEGORY_CITED_PROSE_WRITING
if CITED_WRITING_CATEGORY != "cited_prose_writing":  # pragma: no cover
    raise RuntimeError(
        f"`cited_budget.CATEGORY_CITED_PROSE_WRITING` 已经变成 {CITED_WRITING_CATEGORY!r}："
        "本模块按它核账本类别，必须同步更新")

#: 写作调用日志。它在写作客户端落盘，记录**这次调用的输入策略**（`real:<model>` 或
#: `offline_stub`）与可见回复。它随节产物一起写在节目录下，是交叉核对的第二处证据。
WRITER_JOURNAL_NAME = "cited_call_journal.json"

#: 一节要能被展示，至少要有这些。缺任一份 ⇒ 「本节尚未产出」，不是「绑定失败」——
#: 运行中途页面会在轮询，这时把「还没写出来」报成错误是没有信息量的。
_SECTION_REQUIRED = (
    "cited_report_version.json",
    "cited_input_manifest.json",
    "cited_prose.json",
    "sentence_checks.json",
    "review_issues.json",
    #: `_emit_section` 对这一节**无条件**落盘它（成功是一份 `cmt-*`，非财务节或构造失败是
    #: `{"outcome": None, "error": ...}`）。既然读侧一定会取它，就必须算进「产物齐了」这件事，
    #: 否则缺它时抛的是 `KeyError` 而不是具名的「本节尚未产出」。
    "cited_metric_tables.json",
    "source_table_display.json",
    "cited_gap_bins.json",
    "cited_preview.md",
)
_SECTION_OPTIONAL = (
    "cited_prose_reworked.json",
    "demo_page.md",
    "readback.md",
)
_DEFAULT_SECTIONS = ("company", "financial")

#: 仓库根。由本文件位置推出，不 import 别的模块——只读展示层不该把一个重型运行时拖进来。
_REPO = Path(__file__).resolve().parents[1]


class CitedUploadViewError(ValueError):
    """本 run 的产物无法按它自己的身份读回。**不降级**为「仍然可以看」。"""


class UploadRunIncomplete(CitedUploadViewError):
    """产物还没齐（运行在跑，或已失败在半途）。这是如实状态，不是错误。"""


@dataclass(frozen=True)
class UploadRunSection:
    """一节的可展示读数。

    字段名与 `cited_demo_loader.CitedDemoSection` / `cited_demo_binding.CompanyDisplay`
    **刻意对齐**（`report_version` / `manifest` / `draft` / `checks` / `review` /
    `metric_tables` / `source_display` / `preview_markdown` / `demo_markdown` /
    `source_regions` / `source_images`），因此既有渲染器可按鸭子类型直接消费，不必复制一份
    渲染逻辑。新增的字段只描述**本 run 自己**的两个诚实读数。
    """

    section_id: str
    report_version: dict[str, Any]
    manifest: dict[str, Any]
    draft: dict[str, Any]
    checks: dict[str, Any]
    review: dict[str, Any]
    metric_tables: dict[str, Any]
    source_display: dict[str, Any]
    gap_bins: dict[str, Any]
    preview_markdown: str
    demo_markdown: str
    readback_markdown: str
    source_regions: tuple[dict[str, Any], ...]
    source_images: dict[str, bytes]
    initial_draft: dict[str, Any]
    reviewed_outcome: cited_review.CitedReviewOutcome | None
    #: 有效稿来自哪个文件（`cited_prose.json` 或 `cited_prose_reworked.json`）。
    draft_source: str
    #: 本 run 的写作调用记录（`cited_prose.json` 的 `call`），原样保留作核对凭据之一。
    writer_call: dict[str, Any]
    #: 写作产出者的**三档**读数（`PRODUCER_MODEL` / `PRODUCER_STANDIN` /
    #: `PRODUCER_UNVERIFIABLE`）。由 `current_writer_producer` 从三处证据交叉核对得出，
    #: **不**由 `mode` 或单一字段决定。
    writer_producer: str
    #: 三处证据一致指向真实模型时才为 `True`。它是 `writer_producer == PRODUCER_MODEL` 的
    #: 派生读数，保留给既有只读探针与读回自述；新代码应直接读 `writer_producer`。
    writer_is_model: bool
    #: 审阅是不是**独立 LLM 审阅**。离线回声替身恒为 False。
    review_is_model: bool
    #: 这次审阅的调用有没有落在本 run 自己的成功账本里。离线 run 的账本里没有可记的尝试，
    #: 因此恒为 False——这一条**不是**缺陷，它是「本次没有真实调用」的如实结果。
    review_attested: bool
    #: A2 财务确定性呈现（`cited_balance_structure__fin_balance_structure.json`）。**只有**财务节
    #: 有它；没有这一份就是「本节没有 A2」，与「A2 写坏了」是两回事，因此这里如实给 `None`，
    #: 「本 run 记 completed 却没有它」那一句由 `load_upload_run` 判。
    balance_structure: dict[str, Any] | None = None
    #: 用来做账本核对的那次审阅调用的 `call_id`。成功形状取自 `review_issues.json` 的 `call`；
    #: **失败形状**（`outcome=None`）没有 `call`，取自 `diagnostic.call_id`——那正是发起后回复
    #: 不合约的那一次调用。两处都没有时是空串，「这次审阅有没有真实调用过」如实无从成立。
    review_call_id: str = ""
    #: 失败形状自报的失败码（如 `review_reply_unparsable`）。成功形状为空串——**不是**「没有失败」，
    #: 而是「这一栏只在没有产出意见时才有内容」。
    review_failure: str = ""
    #: 失败形状的 `diagnostic` 原文（`error_type` / `message` / `sentence_id` / `citation_id`），
    #: 原样保留供页面如实说明「回复在哪里不合约」。成功形状为 `None`。
    review_diagnostic: dict[str, Any] | None = None

    @property
    def hard_sentence_ids(self) -> tuple[str, ...]:
        """机械层标红的全部句子（事实安全族 ∪ 栏目覆盖族）。"""
        return tuple(str(s["sentence_id"]) for s in self.checks["sentence_states"]
                     if s["verdict"] == "hard_error")

    @property
    def fact_safety_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.checks["fact_safety_sentence_ids"])

    @property
    def column_coverage_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.checks["column_coverage_sentence_ids"])

    @property
    def blocking_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.checks["blocked_sentence_ids"])

    @property
    def sentence_count(self) -> int:
        return int(self.report_version["sentence_count"])

    @property
    def review_available(self) -> bool:
        """本次有没有**产出意见**。`False` 只说明「审阅这一环没有产出」，不是「审阅通过」。"""
        return self.reviewed_outcome is not None

    @property
    def review_issues(self) -> tuple[dict[str, Any], ...]:
        if self.reviewed_outcome is None:
            return ()
        return tuple(self.review["issues"])

    @property
    def review_ran_without_opinions(self) -> bool:
        """本 run **确实发过**这次审阅调用，但没有产出可用意见（回复不合约、整批作废）。

        与「审阅根本没跑」是两件事，判据只有一条：这次审阅的 `call_id` 在不在本 run 自己的
        成功账本里（`review_attested`）。`review_available` 回答的是「有没有**意见**」，
        这条回答的是「**调用**发没发」——**调用成功不等于审阅成功**，两条轴不合并。
        """
        return not self.review_available and self.review_attested

    def sentence_texts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for subsection in self.draft["subsections"]:
            for paragraph in subsection["paragraphs"]:
                for sentence in paragraph["sentences"]:
                    out[str(sentence["sentence_id"])] = str(sentence["text"])
        return out


@dataclass(frozen=True)
class UploadRunView:
    run_id: str
    run_dir: Path
    ledger: dict[str, Any]
    journal: tuple[cited_run_journal.RunProgressEvent, ...]
    sections: dict[str, UploadRunSection]
    #: 有目录、但产物没写全的节。页面显示「尚未产出」，并继续按 run-id 轮询。
    pending_sections: tuple[str, ...]
    run_input_binding: dict[str, Any] | None
    run_request: dict[str, Any] | None
    run_input_verified: bool
    run_input_note: str
    file_hashes: dict[str, str]
    #: 本 run 的 `cfi-1` 财务输入绑定（三份上传 XLSX 与它们对应的快照来源版本）。没有它时是
    #: `None`——那是「本 run 没有财务上传」，不是「财务上传对不上」。
    financial_input_binding: dict[str, Any] | None = None
    #: 这份财务上传绑定有没有与暂存目录里重新读回的那一份逐字对上。
    financial_input_verified: bool = False
    financial_input_note: str = ""

    @property
    def mode(self) -> str:
        return str(self.ledger.get("mode") or "")

    @property
    def run_outcome(self) -> str:
        return str(self.ledger.get("run_outcome") or "")

    @property
    def failed(self) -> bool:
        return self.run_outcome == "failed"

    @property
    def failure(self) -> dict[str, Any] | None:
        value = self.ledger.get("failure")
        return dict(value) if isinstance(value, dict) else None

    @property
    def progress(self) -> dict[str, Any]:
        return {
            "event_count": len(self.journal),
            "last_stage": self.journal[-1].stage if self.journal else "",
            "last_at_utc": self.journal[-1].at_utc if self.journal else "",
            "failed": self.failed,
            "has_journal": bool(self.journal),
            "resumable": False,
        }

    @property
    def source_regions(self) -> tuple[dict[str, Any], ...]:
        """本节各栏的原 PDF 只读区域，**按 `region_key` 去重**后并起来。

        去重与 `cited_demo_loader` 的跨节读法同一口径：同一个区域键在两个节里各出现一次时，
        它是**一个**区域，不是两个。渲染器按鸭子类型取这两个属性，因此这里给出与前两屏
        同名的读法。
        """
        seen: dict[str, dict[str, Any]] = {}
        for section in self.sections.values():
            for region in section.source_regions:
                seen.setdefault(str(region["region"]["region_key"]), region)
        return tuple(seen.values())

    @property
    def source_images(self) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        for section in self.sections.values():
            out.update(section.source_images)
        return out


# ------------------------------------------------------------------ 读回


def _present(run_dir: Path, rel: str) -> bool:
    return (run_dir / rel).is_file()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CitedUploadViewError(message)


def _decode_review(raw: dict[str, Any]) -> cited_review.CitedReviewOutcome | None:
    """`review_issues.json` 两种形状：有意见（`outcome="reviewed"`）/ 没有产出（`outcome=None`）。"""
    if "outcome" not in raw:
        raise CitedUploadViewError(
            "review_issues.json 既没有 `outcome` 字段，也不是一份完整的审阅结果："
            "无法判断这次审阅到底有没有产出意见")
    if raw["outcome"] is None:
        return None
    return _loader._review_from_wire(raw)


def _review_call_id(raw: dict[str, Any]) -> str:
    """这次审阅调用的 `call_id`——成功形状与**失败形状**各从一处取。

    成功形状（`outcome="reviewed"`）把它记在自己的 `call` 里；失败形状（`outcome=None`）没有
    `call`，但把**发起后回复不合约的那一次调用**的 `call_id` 记在 `diagnostic` 里。只读前者会
    把「调用确实发了、只是回复不合约」错判成「这次审阅没跑」——`_attest_review` 的判据一个字
    都不改（仍是 call_id／category／section_id／status=ok 四项全中），这里只是把**该核对哪个
    `call_id`** 找对。两处都没有时给空串，`_attest_review` 会如实返回 False。
    """
    call = raw.get("call")
    if isinstance(call, dict):
        value = str(call.get("call_id") or "")
        if value:
            return value
    diagnostic = raw.get("diagnostic")
    if isinstance(diagnostic, dict):
        return str(diagnostic.get("call_id") or "")
    return ""


def _attest_review(ledger: dict[str, Any], *, call_id: str, section_id: str) -> bool:
    """这次审阅的 `call_id` 在不在**本 run 自己**的成功账本里。

    判据只看本 run 的 `cited_call_ledger.json`：借任何别的 run 的账本都会把「本 run 发过
    这次调用」变成一句无从核实的话。离线 run 的账本是 `{"ledger": None}` 形状（见
    `_write_cited_call_ledger`），因此这里恒为 False —— 如实。
    """
    if not call_id:
        return False
    attempts = ((ledger.get("call_budget") or {}).get("attempts") or ())
    return any(str(attempt.get("call_id") or "") == call_id
               and attempt.get("category") == "cited_prose_review"
               and attempt.get("section_id") == section_id
               and attempt.get("status") == "ok"
               for attempt in attempts)


def _attest_writing(ledger: dict[str, Any] | None, *, call_id: str, section_id: str,
                    model: str) -> bool:
    """写作调用 `call_id` 在不在**本 run 自己**的成功账本里。

    与 `_attest_review` 同一口径，只是换到写作那一类。**逐字段全中**才算数：`call_id`、节、
    类别（`cited_prose_writing`，不是 `axis`——账本把写作与审阅都记在 `axis="writing"` 上）、
    模型名与 `status="ok"` 四项缺一不可。模型名必须**逐字相等**：账本记的是 `deepseek-v4-pro`
    而写作记录里写别的名字时，这不是「同一次调用」，不得据此宣称模型产出。
    """
    if not call_id or not model:
        return False
    attempts = ((ledger or {}).get("call_budget") or {}).get("attempts") or ()
    return any(str(attempt.get("call_id") or "") == call_id
               and attempt.get("category") == CITED_WRITING_CATEGORY
               and attempt.get("section_id") == section_id
               and str(attempt.get("model") or "") == model
               and attempt.get("status") == "ok"
               for attempt in attempts)


def _ledger_mode(ledger: dict[str, Any] | None) -> str:
    return str((ledger or {}).get("mode") or "")


def _looks_offline(*values: str) -> bool:
    return any("offline" in value.lower() for value in values if value)


def current_writer_producer(*, prose_call: dict[str, Any] | None,
                            journal: dict[str, Any] | None,
                            ledger: dict[str, Any] | None,
                            section_id: str) -> str:
    """交叉核对三处证据，判这一节正文的产出者。

    三处证据：①本 run 自己的写作调用记录（`cited_prose.json` 的 `call`）；②
    `cited_call_journal.json` 记的输入策略与它留存的回复；③整轮账本里**同一** `call_id`、
    节、类别、模型、成功状态的尝试。

    * 只有三处**同时**指向真实模型，才记 `PRODUCER_MODEL`；
    * 只有写作记录自己声明离线、且账本没有把它记成一次成功的真实写作，才记 `PRODUCER_STANDIN`；
    * 任何一处缺失、或三处互相矛盾，一律记 `PRODUCER_UNVERIFIABLE`。

    **本函数不做**「模式是 real 就记模型」这种事：`mode` 只进冲突判定，不进结论。这正是本批
    要修的那处误标的根因——旧读法读的是审阅记录才有的 `model_policy`，对写作记录永远读空，
    于是把一次真实写作显示成了离线替身。
    """
    call = dict(prose_call or {})
    model = str(call.get("model") or "")
    call_id = str(call.get("call_id") or "")
    status = str(call.get("status") or "")
    j = journal if isinstance(journal, dict) else None

    j_policy = str(((j or {}).get("input") or {}).get("model_policy") or "")
    j_reply = dict(((j or {}).get("reply") or {}))
    j_reply_call = str(j_reply.get("call_id") or "")
    j_reply_model = str(j_reply.get("model") or "")

    led_hit = _attest_writing(ledger, call_id=call_id, section_id=section_id, model=model)
    mode = _ledger_mode(ledger)

    # 写作记录自己声明离线（模型名或调用号带 `offline`）：它**不可能**同时是一次真实模型调用。
    if _looks_offline(model, call_id):
        if led_hit:
            return PRODUCER_UNVERIFIABLE  # 冲突：记录说离线，账本却记了一次成功的真实写作
        if j_policy and not j_policy.lower().startswith("offline"):
            return PRODUCER_UNVERIFIABLE  # 冲突：调用日志的输入策略说真实
        if j is not None and j_reply_call and j_reply_call != call_id:
            return PRODUCER_UNVERIFIABLE  # 冲突：调用日志留存的是另一次调用
        if mode and mode != "offline":
            return PRODUCER_UNVERIFIABLE  # 冲突：整轮账本不是离线模式
        return PRODUCER_STANDIN

    # 不是离线标记：要记「模型产出」，三处必须同时成立，缺一记「无法核实」。
    if not model or not call_id or status != "ok":
        return PRODUCER_UNVERIFIABLE
    if j is None:
        return PRODUCER_UNVERIFIABLE          # 没有调用日志：输入策略无从核实
    if not j_policy.startswith("real:"):
        return PRODUCER_UNVERIFIABLE          # 调用日志的输入策略不是 `real:`
    if j_reply_call != call_id:
        return PRODUCER_UNVERIFIABLE          # 调用日志留存的不是这一次调用
    if j_reply_model and j_reply_model != model:
        return PRODUCER_UNVERIFIABLE          # 两处记的模型名不一致
    if mode != "real":
        return PRODUCER_UNVERIFIABLE          # 整轮账本不是真实模式
    if not led_hit:
        return PRODUCER_UNVERIFIABLE          # 本 run 账本里没有这次成功的写作尝试
    return PRODUCER_MODEL


def _section_placement(snapshot: _loader._Snapshot, run_dir: Path, section_id: str,
                       cache: dict[str, dict[str, Any]]) -> str | None:
    """本节产物在哪个前缀下。`"<section_id>/"` 或 `""`（扁平），没有则 `None`。

    以**文件自己声明的 `section_id`** 为准：目录名只是线索，不是身份。
    """
    if _present(run_dir, f"{section_id}/cited_report_version.json"):
        return f"{section_id}/"
    if _present(run_dir, "cited_report_version.json"):
        raw = cache.get("") or snapshot.json("cited_report_version.json")
        cache[""] = raw
        if str(raw.get("section_id") or "") == section_id:
            return ""
    return None


def _load_section(snapshot: _loader._Snapshot, run_dir: Path, section_id: str,
                  prefix: str, ledger: dict[str, Any]) -> UploadRunSection:
    missing = tuple(name for name in _SECTION_REQUIRED if not _present(run_dir, prefix + name))
    if missing:
        raise UploadRunIncomplete(
            f"节 {section_id} 的产物还没写全，缺 {list(missing)}")

    raw = {name: (snapshot.json(prefix + name) if name.endswith(".json")
                  else snapshot.text(prefix + name)) for name in _SECTION_REQUIRED}
    optional = {name: (snapshot.json(prefix + name) if name.endswith(".json")
                       else snapshot.text(prefix + name))
                for name in _SECTION_OPTIONAL if _present(run_dir, prefix + name)}

    try:
        #: 盘上读 `cited_report_version.json` **一律**走 `from_persisted_dict`：按文件自己声明
        #: 的发布期分派，一次口径升版不会把全部历史 run 变成打不开。见
        #: `cited_report.CitedReportVersion.from_persisted_dict`。
        version = cited_report.CitedReportVersion.from_persisted_dict(
            raw["cited_report_version.json"])
        manifest = cited_writer.CitedWriterInputManifest.from_dict(
            raw["cited_input_manifest.json"])
        initial = raw["cited_prose.json"]
        initial_draft = cited_writer.CitedProseDraft.from_dict(initial["draft"])
        checks = sentence_check.SentenceCheckReport.from_dict(raw["sentence_checks.json"])
        reviewed = _decode_review(raw["review_issues.json"])
        #: 指标表旁挂产物两种形状都**原样**保留：成功是一份 `cmt-*`，非财务节或构造失败是
        #: `{"outcome": None, "error": ...}`。把后者改写成一张空表，会让「本节没有表」与
        #: 「表没建出来」重新混成一件事——那正是这份产物自己费力分开的两条读数。
        tables = raw["cited_metric_tables.json"]
        display = raw["source_table_display.json"]
        gap_bins = raw["cited_gap_bins.json"]
    except CitedUploadViewError:
        raise
    except (_loader.CitedDemoLoadError, cited_report.CitedReportError,
            cited_writer.CitedWriterError, sentence_check.SentenceCheckError,
            financial_table.CitedMetricTableError, cited_review.CitedReviewError) as exc:
        raise CitedUploadViewError(
            f"节 {section_id} 的产物不可按现行版本解码（旧版不隐式兼容）：{exc}") from exc

    # ---- 有效稿：由 `report_version.draft_id` **指定**，不由文件名猜 ------------------
    reworked_raw = optional.get("cited_prose_reworked.json")
    candidates: list[tuple[str, Any]] = [("cited_prose.json", initial_draft)]
    if reworked_raw is not None:
        try:
            candidates.append(("cited_prose_reworked.json",
                               cited_writer.CitedProseDraft.from_dict(reworked_raw)))
        except cited_writer.CitedWriterError as exc:
            raise CitedUploadViewError(
                f"节 {section_id} 的返修稿不可解码：{exc}") from exc
    matched = [name for name, draft in candidates if draft.draft_id == version.draft_id]
    _require(len(matched) == 1,
             f"节 {section_id}：report_version 绑定的草稿 {version.draft_id!r} 在本次产物里"
             f"命中 {len(matched)} 份（{matched}），必须恰好一份；"
             "不得拿另一份草稿顶替")
    draft_source = matched[0]
    effective = dict(candidates)[draft_source]

    # ---- 本节内部的逐项身份等式（与 `cdb-1` 同一组，一条不少，一条不放宽） ----------
    _require(version.section_id == section_id,
             f"节 {section_id} 的 report_version 声明的是 {version.section_id!r}")
    _require(manifest.section_id == section_id,
             f"节 {section_id} 的 Writer 清单声明的是 {manifest.section_id!r}")
    _require(version.input_manifest_id == manifest.manifest_id,
             f"节 {section_id} 的 Writer 清单 ID 与 report_version 不符")
    _require(version.check_report_id == checks.report_id,
             f"节 {section_id} 的逐句核对 ID 与 report_version 不符")
    _require(int(version.sentence_count) == len(effective.sentences()),
             f"节 {section_id} 的句数与有效稿不符")
    _require(version.report_version == str(gap_bins.get("report_version") or ""),
             f"节 {section_id} 的缺口账本绑的不是本节 report_version")
    _require(version.report_version in raw["cited_preview.md"],
             f"节 {section_id} 的预览文本未标明本节 report_version")

    hard_from_raw = tuple(s["sentence_id"] for s in raw["sentence_checks.json"]["sentence_states"]
                          if s["verdict"] == "hard_error")
    _require(tuple(checks.hard_error_sentence_ids) == hard_from_raw,
             f"节 {section_id} 的逐句核对文件里硬错误集不自洽")
    _require(int(version.blocking_sentence_count) == checks.blocked_sentence_count,
             f"节 {section_id} 的机械阻断句数与逐句核对文件不符")

    if reviewed is not None:
        _require(version.review_bundle_id == reviewed.bundle_id,
                 f"节 {section_id} 的独立审阅输入 ID 与 report_version 不符")
        _require(reviewed.report_version == version.report_version,
                 f"节 {section_id} 的审阅绑的不是本节 report_version")
        _require(reviewed.draft_id == effective.draft_id,
                 f"节 {section_id} 的审阅绑的不是本节有效稿")
        _require(reviewed.input_manifest_id == manifest.manifest_id,
                 f"节 {section_id} 的审阅清单 ID 与 Writer 清单不符")
        _require(str(version.review_producer_kind or "") == reviewed.review_producer_kind,
                 f"节 {section_id}：report_version 记的审阅产出者是 "
                 f"{version.review_producer_kind!r}，而审阅结果自己声明的是 "
                 f"{reviewed.review_producer_kind!r}；两者必须一致")

    try:
        _loader._validate_display(snapshot, section_id, display, version.report_version,
                                  prefix=prefix.rstrip("/"))
    except _loader.CitedDemoLoadError as exc:
        raise CitedUploadViewError(f"节 {section_id} 的原 PDF 展示集校验失败：{exc}") from exc

    # ---- 两个诚实读数 ---------------------------------------------------------
    #: 写作调用记录。**只看它自己的字段**是不够的：写作记录**不带** `model_policy`（那是审阅
    #: 记录才有的键），旧代码按它判，于是对每一次真实写作都读出空串、显示成「离线替身」。
    #: 这里改成交叉核对三处，见 `current_writer_producer`。
    writer_call = dict(initial.get("call") or {})
    journal_raw = (snapshot.json(prefix + WRITER_JOURNAL_NAME)
                   if _present(run_dir, prefix + WRITER_JOURNAL_NAME) else None)
    writer_producer = current_writer_producer(
        prose_call=writer_call, journal=journal_raw, ledger=ledger, section_id=section_id)
    writer_is_model = writer_producer == PRODUCER_MODEL
    review_raw = dict(raw["review_issues.json"])
    review_call_id = _review_call_id(review_raw)
    review_is_model = (reviewed is not None
                       and reviewed.review_producer_kind == INDEPENDENT_REVIEW_KIND)
    review_attested = _attest_review(ledger, call_id=review_call_id, section_id=section_id)

    regions = tuple(display["regions"])
    image_prefix = f"{prefix}source_display"
    images = {str(r["region"]["region_key"]):
              snapshot.read("/".join(p for p in (image_prefix, str(r["render_relpath"])) if p))
              for r in regions}

    #: A2（财务确定性呈现）。它是**可选**产物：只有财务节有它，且某些 run 根本没跑财务节。
    #: 「文件不在」与「A2 与本节版本对不上」必须分得开：前者在这里就是 `None`，后者由下面的
    #: `_require` 具名拒——把两者都写成「没有 A2」会让「写坏了」混进「本来就没有」。
    aa_raw = snapshot.json(prefix + _loader._A2_FILE) if _present(
        run_dir, prefix + _loader._A2_FILE) else None
    if aa_raw is not None:
        _require(str(aa_raw.get("section_id") or "") == section_id,
                 f"节 {section_id} 的 A2 产物声明的是 {aa_raw.get('section_id')!r}")

    return UploadRunSection(
        section_id=section_id, report_version=raw["cited_report_version.json"],
        manifest=raw["cited_input_manifest.json"], draft=dict(effective.to_dict()),
        checks=raw["sentence_checks.json"], review=raw["review_issues.json"],
        metric_tables=tables, source_display=display, gap_bins=gap_bins,
        preview_markdown=raw["cited_preview.md"],
        demo_markdown=str(optional.get("demo_page.md") or ""),
        readback_markdown=str(optional.get("readback.md") or ""),
        source_regions=regions, source_images=images,
        initial_draft=initial, reviewed_outcome=reviewed, draft_source=draft_source,
        writer_call=writer_call, writer_producer=writer_producer,
        writer_is_model=writer_is_model, review_is_model=review_is_model,
        review_attested=review_attested, balance_structure=aa_raw,
        review_call_id=review_call_id,
        review_failure=(str(review_raw.get("review_failure") or "")
                        if reviewed is None else ""),
        review_diagnostic=(dict(review_raw["diagnostic"])
                           if reviewed is None
                           and isinstance(review_raw.get("diagnostic"), dict) else None))


def load_upload_run(run_id: str, *, results_root: str | Path = _loader.DEFAULT_RESULTS_ROOT,
                    run_input_root: str | Path | None = None,
                    section_ids: tuple[str, ...] = _DEFAULT_SECTIONS) -> UploadRunView:
    """读回**本 run** 的产物。不发请求、不写任何文件、不读别的 run。

    `run_input_root` 给出时，还会把运行目录里那份 `run_input_binding.json` 与暂存目录里
    重新读回的清单**逐字比对**：这一步证明「运行自己记的输入」与「页面上那次上传落在盘上的
    字节」是同一份，而不是各说各话。对不上即拒。
    """
    if not _loader._RUN_ID.fullmatch(str(run_id)):
        raise CitedUploadViewError(f"非法 run_id：{run_id!r}")
    root = Path(results_root).resolve(strict=False)
    run_dir = root / str(run_id)
    if not run_dir.is_dir():
        raise UploadRunIncomplete(f"运行目录尚不存在：{run_id}")
    snapshot = _loader._Snapshot(run_dir.resolve())

    if not _present(run_dir, "cited_call_ledger.json"):
        raise UploadRunIncomplete(f"调用账本尚未落盘：{run_id}")
    ledger = snapshot.json("cited_call_ledger.json")

    version_cache: dict[str, dict[str, Any]] = {}
    sections: dict[str, UploadRunSection] = {}
    pending: list[str] = []
    completed = str(ledger.get("run_outcome") or "") == "completed"
    for section_id in section_ids:
        prefix = _section_placement(snapshot, run_dir, section_id, version_cache)
        if prefix is None:
            #: 账本说这一轮**跑完了**，可请求的节**整个没有**：这不是「还在跑」，是缺陷。
            #: 少了这一条，一次只写出公司节的 run 会被读成「财务节也齐了」——页面于是拿
            #: 单节的成功画成双节的完成。
            if completed:
                raise CitedUploadViewError(
                    f"账本记 completed，但请求的节 {section_id!r} **没有产出**：本轮不是一次"
                    "完整产出，页面不得把它当作完成品")
            continue
        try:
            sections[section_id] = _load_section(snapshot, run_dir, section_id, prefix, ledger)
        except UploadRunIncomplete:
            #: 账本说这一轮**跑完了**，可这一节的产物却没写全：那不是「还在跑」，是缺陷。
            #: 把「跑完」与「只写了一半」分开，正是账本存在的理由。
            if completed:
                raise CitedUploadViewError(
                    f"账本记 completed，但节 {section_id} 的产物不完整："
                    "本轮不是一次完整产出，页面不得把它当作完成品")
            pending.append(section_id)

    #: A2 是财务节的**请求产物**之一（v2 profile 含 `fin_balance_structure`）。账本记 completed
    #: 却在财务节里找不到它，与「财务节整个没产出」是同一类缺陷：两者都让页面把「少了一块」
    #: 画成「都齐了」。只在 `financial` 被请求时才要求它——别的节本来就没有 A2。
    if completed and "financial" in sections and sections["financial"].balance_structure is None:
        raise CitedUploadViewError(
            "账本记 completed，但财务节没有 A2 结构产物"
            f"（`{_loader._A2_FILE}`）：本轮不是一次完整产出，页面不得把它当作完成品")

    binding = snapshot.json("run_input_binding.json") if _present(
        run_dir, "run_input_binding.json") else None
    fin_binding = snapshot.json(cited_financial_input.BINDING_COPY_NAME) if _present(
        run_dir, cited_financial_input.BINDING_COPY_NAME) else None
    request = _read_run_request(run_dir, snapshot, run_input_root=run_input_root)
    verified, note = _verify_run_input(run_dir, binding, run_input_root=run_input_root)
    fin_verified, fin_note = _verify_financial_input(
        run_dir, fin_binding, run_input_root=run_input_root)

    return UploadRunView(
        run_id=str(run_id), run_dir=run_dir, ledger=ledger,
        journal=cited_run_journal.read_run_progress(run_dir),
        sections=sections, pending_sections=tuple(pending),
        run_input_binding=binding, run_request=request,
        run_input_verified=verified, run_input_note=note,
        file_hashes=snapshot.verify_unchanged(),
        financial_input_binding=fin_binding,
        financial_input_verified=fin_verified, financial_input_note=fin_note)


def _verify_financial_input(run_dir: Path, binding: dict[str, Any] | None, *,
                            run_input_root: str | Path | None) -> tuple[bool, str]:
    """运行目录里记的**财务**输入绑定，与暂存目录里那份，是不是同一份。

    与 `_verify_run_input` 同一条口径，只是换了一条 `cfi-1` 的绑定：它证明「运行自己记的
    三份上传 XLSX」与「页面上那次上传落在盘上的字节」是同一份。缺绑定是「本 run 没有财务
    上传」，不是「对不上」——但那也不能读成「已核验」。
    """
    if binding is None:
        return False, "运行目录里没有财务输入绑定：本 run 未声明财务上传输入"
    if run_input_root is None:
        return False, "未提供运行输入目录：本次只读运行产物，无法比对财务上传"
    staged_dir = Path(run_input_root) / run_dir.name
    if not staged_dir.is_dir():
        return False, f"运行输入目录不可达：{staged_dir}"
    try:
        staged = cited_financial_input.load_financial_input(staged_dir)
    except cited_financial_input.CitedFinancialInputError as exc:
        return False, f"财务输入无法读回：{exc}"
    recorded = str(binding.get("binding_sha256") or "")
    if not recorded:
        return False, "运行目录里的财务输入绑定没有 binding_sha256：无法比对"
    if recorded != staged.binding_sha256:
        raise CitedUploadViewError(
            f"运行目录记的财务输入绑定（{recorded[:16]}…）与暂存目录重新读回的"
            f"（{staged.binding_sha256[:16]}…）不是同一份：本 run 读的 XLSX 与本次上传的"
            "XLSX 对不上，页面不得声称「本次财务上传被本次运行核验过」")
    return True, (f"财务输入绑定逐字一致：{len(staged.sources)} 份对象与快照 "
                  f"`{staged.snapshot.snapshot_id}` 的 `source_versions` 逐份同字节，"
                  f"绑定指纹 {staged.binding_sha256[:16]}…")


def _read_run_request(run_dir: Path, snapshot: Any, *,
                      run_input_root: str | Path | None) -> dict[str, Any] | None:
    """「我请求了什么」——`run_request.json` 是**发起侧**写的，它落在运行输入目录。

    只读运行目录会把这一行读成 `None`（链从不把请求面抄进产物），页面于是显示不出这次请求的
    模式与节集合；而刷新之后要按 run-id 读出真实读数，靠的正是它。运行目录里那份仍然作为
    历史布局的回落保留。
    """
    if run_input_root is not None:
        target = Path(run_input_root) / run_dir.name / cited_run_input.REQUEST_NAME
        if target.is_file():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    if _present(run_dir, cited_run_input.REQUEST_NAME):
        return snapshot.json(cited_run_input.REQUEST_NAME)
    return None


def _verify_run_input(run_dir: Path, binding: dict[str, Any] | None, *,
                      run_input_root: str | Path | None) -> tuple[bool, str]:
    """运行目录里记的输入绑定，与暂存目录里的那份，是不是同一份。"""
    if run_input_root is None:
        return False, "未提供运行输入目录：本次只读运行产物"
    staged_dir = Path(run_input_root) / run_dir.name
    if not staged_dir.is_dir():
        return False, f"运行输入目录不可达：{staged_dir}"
    try:
        staged = cited_run_input.load_run_input(staged_dir)
    except cited_run_input.CitedRunInputError as exc:
        return False, f"运行输入无法读回：{exc}"
    if binding is None:
        return False, "运行目录里没有 run_input_binding.json：本 run 未声明输入绑定"
    recorded = str(binding.get("manifest_sha256") or "")
    if not recorded:
        return False, "运行目录里的输入绑定没有 manifest_sha256：无法比对"
    if recorded != staged.manifest_sha256:
        raise CitedUploadViewError(
            f"运行目录记的输入绑定（{recorded[:16]}…）与暂存目录重新读回的清单"
            f"（{staged.manifest_sha256[:16]}…）不是同一份：本 run 读的字节与本次上传的字节"
            "对不上，页面不得声称「本次上传被本次运行读过」")
    return True, (f"运行输入绑定逐字一致：{len(staged.documents)} 份对象，"
                  f"清单指纹 {staged.manifest_sha256[:16]}…")


def declared_documents_for(company_id: str, *,
                           evidence_db: str | Path | None = None
                           ) -> tuple[cited_run_input.DeclaredDocument, ...]:
    """当前 Evidence 登记里这一家公司的当前来源材料。只读，不写库。

    声明的权威是 **Evidence 登记**，不是页面。默认库路径只是「到哪儿读」，换一个库不影响
    「声明从登记来」这条口径。
    """
    db = Path(evidence_db) if evidence_db is not None else (_REPO / "data" / "evidence.db")
    return cited_run_input.registered_documents(db, company_id)


__all__ = [
    "UPLOAD_VIEW_VERSION",
    "INDEPENDENT_REVIEW_KIND",
    "PRODUCER_MODEL",
    "PRODUCER_STANDIN",
    "PRODUCER_UNVERIFIABLE",
    "PRODUCER_READINGS",
    "CITED_WRITING_CATEGORY",
    "WRITER_JOURNAL_NAME",
    "current_writer_producer",
    "CitedUploadViewError",
    "UploadRunIncomplete",
    "UploadRunSection",
    "UploadRunView",
    "load_upload_run",
    "declared_documents_for",
]


if __name__ == "__main__":  # pragma: no cover - 手工自检
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法：python -m sections.cited_upload_view <run_id>")
    view = load_upload_run(sys.argv[1],
                           results_root=sys.argv[2] if len(sys.argv) > 2 else
                           _loader.DEFAULT_RESULTS_ROOT,
                           run_input_root=(sys.argv[3] if len(sys.argv) > 3 else None))
    print(json.dumps({
        "view_version": UPLOAD_VIEW_VERSION, "run_id": view.run_id,
        "mode": view.mode, "run_outcome": view.run_outcome,
        "sections": {sid: {
            "sentences": s.sentence_count,
            "hard": len(s.hard_sentence_ids),
            "fact_safety": len(s.fact_safety_sentence_ids),
            "column_coverage": len(s.column_coverage_sentence_ids),
            "writer_is_model": s.writer_is_model,
            "writer_producer": s.writer_producer,
            "review_is_model": s.review_is_model,
            "review_attested": s.review_attested,
            "review_available": s.review_available,
            "publishability": s.report_version["publishability"],
            "source_regions": len(s.source_regions),
        } for sid, s in view.sections.items()},
        "journal_events": len(view.journal),
        "run_input_verified": view.run_input_verified,
        "run_input_note": view.run_input_note,
    }, ensure_ascii=False, indent=2))

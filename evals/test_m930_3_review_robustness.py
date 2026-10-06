"""Eval: M930-3 审阅健壮性——r28 留存字节的**离线回放** + 本批三处修复的行为判据。

用法: python -X utf8 -m evals.test_m930_3_review_robustness

## 这个模块要回答什么

2026-10-01 的真实 r28（`evaluation/results/m930_3_cited_real_r28_company/`）把 29 份材料送达
Writer、Writer 返回 23 句与 8 条缺口、独立 Reviewer 也返回了覆盖 23 句的结果——**整轮却在人读
出口一节不剩**：构造审阅结果时抛 `AssuranceSchemaError: ReviewIssue.reason 不得为空字符串`。
本模块用 r28 **自己留存的字节**回放这件事，再钉住本批三处修复的行为：

  §1 留存字节的身份与形状：writer 调用留存（`cited_call_journal.json`：输入面身份、请求面原文、
     可见回复原文、解析状态）与那一轮**真实的两份调用日志**（`logs/llm/`：写作一份、审阅一份）。
     三份都是这一轮**自己的**字节，字节完整性当场重算。
  §2 七条无引用句**逐句具名**，且**两份互不相干的留存互相印证**：写作侧留存的回复里没有引用的
     那 7 句，与审阅侧留存回复里 `citation_id` 为空的那 7 句，是同一组 id。
  §3 r28 的三层不合规**逐层重放**（本节只读、不修、不降级）：回复里 22/23 行 `reason` 为空、
     7 行 `citation_id` 为空、22 行判 `supported`。生产解析口对空 `reason` 当场抛出——且这条
     抛出**今天照旧**（协议要求它非空，不是本批放宽的那一条）；失败被归成 typed 原因码。
  §3b **机械硬错误与错栏**：用**生产判据**（`cited_reply_readback.diagnose`）在留存字节上重读
     一遍——11 句硬错误可复算、其中 7 句是无引用句，另有 2 句**错栏**（引用的材料没登记到本
     小节声明的那一栏）逐句具名。同时把**判不了的轴**逐条列出（12 根，理由都是缺清单侧对象）：
     留存缺口因此是**被圈定**的，而不是一句笼统的「不可复算」。
  §4 **新形态的纯缺口不进入正文**：一个小节可以没有段落，缺口写在 `gaps` 里；这样的一节既不
     产出零引用句，也不白烧一次审阅调用。同时钉住缺口理由的**读者面**取值——模型自述「清单里
     没有材料」而清单里明明登记了该栏材料时，系统改判，模型原话留在 `claimed_reason`。
  §5 `supported` 也必须有**非空理由**（协议层的唯一新增硬约束），且「无理由的没问题」与
     「没看过」在产物上可区分。
  §6 **③ 不得越过 ②**，且**审阅失败不再吃掉整轮**：机械硬错误句上的 `supported` 只记成一条
     分歧（`hard_error_override_sentence_ids`），不使解析失败；但只要还有机械硬错误，③ 轴就
     **不可能**是 `passed_awaiting_human`。反过来，审阅解析失败时，正文、逐句硬核对、缺口与
     补件需求照旧落盘成一份明确标注「不可发布／独立审阅未完成」的**降级诊断预览**。
  §7 **财务呈现层既有路由与权威表不被本批改坏**：新加的 ③ 轴降级规则不改变指标表的身份、
     不改变它在预览里的呈现，也不把「表在」读成「正文没问题」。

## 本模块不主张的事

  * 它**不**重放 r28 的**输入清单**。r28 跑完后只留了调用日志、调用账本与这张表证明矩阵，
     **清单本身没有留存**——因此凡是要按清单侧对象判的轴（`structured_view` 逐格数字、
     `document_id` 旧材料当前化、呈 现路由、材料采用去向、版本锚），本轮**判不了**。§3b 把这
     12 根轴**逐条列出来**并写明各自缺的是哪一样，而不是拿一句「不可复算」盖过去。
  * 它**不**因为「11 句硬错误可在留存字节上复算」就宣称这一轮的核对照旧完整：可复算的只是
     那 7 根只需请求面自带信息的轴。**条数可复算 ≠ 核对照旧成立。**
  * 它**不**发起任何真实模型调用、不联网、不写库、不写 `logs/llm`（只读打开）。
  * 它**不**宣称 M930-3、TS5、正式树门或后续阶段关闭，也**不**产生任何放行结论。

数据依赖：`evaluation/results/m930_3_cited_real_r28_company/cited_call_journal.json`、
`logs/llm/` 下 r28 那两次调用的 jsonl。都不在版本控制里，缺失时本模块 **typed skip**
（不计通过），不静默绿。
"""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assurance import schema as AS                     # noqa: E402
from evals import test_m930_3_cited_writer as E         # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN     # noqa: E402
from sections import cited_financial_table as CFT      # noqa: E402
from sections import cited_reply_readback as CRRB      # noqa: E402
from sections import cited_report as CRP               # noqa: E402
from sections import cited_review as CR                # noqa: E402
from sections import cited_writer as CW                # noqa: E402
from sections import sentence_check as SC              # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_RESULTS = _ROOT / "evaluation" / "results" / "m930_3_cited_real_r28_company"
_JOURNAL = _RESULTS / "cited_call_journal.json"
_LEDGER = _RESULTS / "cited_call_ledger.json"
_LOGS = _ROOT / "logs" / "llm"

#: r28 那两次调用在留存账本里的 `call_id`。写成常量不是「钉版本号」，是**定位**：账本里每个
#: 类别恰好一次尝试，这两个 id 把「写作的调用日志」与「审阅的调用日志」分别指到具体那一份
#: jsonl 上。取不到就 typed skip，绝不退而求其次去猜「哪份日志看起来像审阅」。
_WRITE_CALL_ID = "95dc9ff802cd4238b136c79ed892884c"
_REVIEW_CALL_ID = "8a191220c32f4c3bb968d374a83607d3"

#: r28 写作留存的**回复**与**审阅日志**在版本控制外，缺失即 skip。
def _missing() -> list[str]:
    out: list[str] = []
    if not _JOURNAL.is_file():
        out.append(str(_JOURNAL))
    if not _LEDGER.is_file():
        out.append(str(_LEDGER))
    for call_id in (_WRITE_CALL_ID, _REVIEW_CALL_ID):
        if not list(_LOGS.glob(f"*__{call_id}.jsonl")):
            out.append(f"logs/llm/*__{call_id}.jsonl")
    return out


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _log_row(call_id: str) -> dict:
    hits = list(_LOGS.glob(f"*__{call_id}.jsonl"))
    return json.loads(hits[0].read_text(encoding="utf-8").strip().splitlines()[0])


# ---------------------------------------------------------------------------
# §2 用的重建：把**留存的写作回复**变成一份草稿（不需要、也不伪造输入清单）
# ---------------------------------------------------------------------------

def _draft_from_retained_reply(journal: dict) -> tuple[CW.CitedProseDraft, list[dict]]:
    """留存回复 + 留存归一化表 → `CitedProseDraft`。

    句子 id **逐字取留存归一化表**（模型自己写的 id 在同一小节里会重复，归一化表是系统定的
    最终 id）；正文与引用逐字取回复。`input_manifest_id` 取留存输入面的 `manifest_id`——
    它是**记录值**，不重算：重算要那份没留存的清单。

    返回 `(草稿, 逐句原始行)`；原始行按同一顺序，供「哪几句没有引用」这类判断在**未解释的
    字节**上再做一遍。
    """
    reply = json.loads(journal["reply"]["text"])
    final_ids = [str(x["final_id"]) for x in journal["parse"]["normalization"]["sentence_ids"]]
    raw: list[dict] = []
    for subsection in reply["subsections"]:
        for paragraph in subsection.get("paragraphs") or []:
            for sentence in paragraph.get("sentences") or []:
                raw.append({**sentence, "subsection_id": subsection["subsection_id"]})
    if len(raw) != len(final_ids):
        raise AssertionError(
            f"留存归一化表 {len(final_ids)} 句、回复里 {len(raw)} 句：两者对不上，"
            "本模块不猜谁对")
    subsections = []
    index = 0
    for subsection in reply["subsections"]:
        paragraphs = []
        for paragraph in subsection.get("paragraphs") or []:
            sentences = []
            for sentence in paragraph.get("sentences") or []:
                sentences.append(CW.CitedSentence(
                    final_ids[index], sentence["text"],
                    tuple(sentence.get("citations") or ())))
                index += 1
            paragraphs.append(CW.CitedParagraph(
                paragraph_id=str(paragraph.get("paragraph_id") or "p1"),
                sentences=tuple(sentences)))
        subsections.append(CW.CitedSubsection(
            subsection_id=subsection["subsection_id"],
            title=str(subsection.get("title") or subsection["subsection_id"]),
            paragraphs=tuple(paragraphs)))
    gaps = []
    for gap in reply.get("gaps") or []:
        gaps.append(CW.CitedProseGap.create(
            subsection_id=gap["subsection_id"], requirement_text=gap["requirement_text"],
            reason=gap.get("reason") or "no_source_in_manifest",
            detail=str(gap.get("detail") or "")))
    draft = CW.CitedProseDraft.create(
        task_id=journal["input"]["task_id"], section_id=journal["input"]["section_id"],
        input_manifest_id=journal["input"]["manifest_id"],
        writer_identity="r28-retained-reply", subsections=tuple(subsections),
        gaps=tuple(gaps))
    return draft, raw


# ---------------------------------------------------------------------------
# §1／§2／§3 r28 留存字节（只读）
# ---------------------------------------------------------------------------

def _check_retained_bytes(check, check_eq, details) -> None:
    details.append("## §1 r28 留存字节：三份产物都指向同一轮")
    journal_bytes = _JOURNAL.read_bytes()
    ledger_bytes = _LEDGER.read_bytes()
    journal = json.loads(journal_bytes.decode("utf-8"))
    ledger = json.loads(ledger_bytes.decode("utf-8"))
    write_log = _log_row(_WRITE_CALL_ID)
    review_log = _log_row(_REVIEW_CALL_ID)

    check_eq(journal["journal_version"], "ccj-1", "写作留存是本链自己的 `ccj-1`（不是别处的日志）")
    check_eq(journal["reply"]["status"], "ok", "写作调用状态 ok（回复是真的收到了）")
    reply_text = str(journal["reply"]["text"])
    check_eq(_sha256_bytes(reply_text.encode("utf-8")), journal["reply"]["visible_sha256"],
             "留存回复的 sha256 与它自报的逐字节相符（我们读到的就是那一串字节）")
    check_eq(journal["parse"]["ok"], True, "写作解析当时是成功的（整轮不是死在写作这一环）")
    check_eq(journal["input"]["material_count"], 29, "送达 Writer 的材料 29 份")
    check_eq(journal["input"]["fact_count"], 0, "本次没有具名权威事实行（`authority_facts` 为空）")
    check_eq(journal["reply"]["call_id"], _WRITE_CALL_ID,
             "留存回复的 call_id 与账本里那条写作尝试对得上")

    check_eq(ledger["run_outcome"], "failed", "这一轮的结局是 **failed**，留存不改变它")
    check_eq(ledger["failure"]["error"], "ReviewIssue.reason 不得为空字符串",
             "账本记下的失败原因逐字保留")
    check_eq(ledger["failure"]["error_type"], "AssuranceSchemaError",
             "失败类型是构造期 schema 约束，不是网络或解析器崩了")
    checks = ledger["call_budget"]["attempts_by_category"]
    check_eq(checks, {"cited_prose_writing": 1, "cited_prose_review": 1},
             "两个类别各恰好一次尝试（写作与审阅都真的发出去了）")
    check_eq(ledger["call_budget"]["refusal_total"], 0, "本次没有一条被预算门拒绝的尝试")

    check_eq(str(write_log["call_id"]), _WRITE_CALL_ID, "写作调用日志按 call_id 定位到")
    check_eq(str(review_log["call_id"]), _REVIEW_CALL_ID, "审阅调用日志按 call_id 定位到")
    write_face = json.loads(write_log["messages"][0]["content"])
    review_face = json.loads(review_log["messages"][0]["content"])
    check_eq(str(review_face["section_id"]), "company", "审阅请求面是公司节")
    check_eq(len(write_face["materials"]), 29, "写作请求面逐条给出 29 份材料（留存原文可比）")
    check_eq(str(write_face["prompt_version"]), str(journal["input"]["prompt_version"]),
             "写作请求面与留存输入面声明同一个 prompt 版本")

    draft, raw = _draft_from_retained_reply(journal)

    details.append("## §2 七条无引用句逐句具名，两份留存互相印证")
    sentences = draft.sentences()
    uncited = [s.sentence_id for s in sentences if not s.citations]
    #: 同一件事在**未解释的原始字节**上再数一遍：模型自己写的 `sentence_id` 在小节内会重复
    #: （r28 的回复里 7 句都叫 `s01`），所以这一遍按**位置**取归一化表里的最终 id，而不是
    #: 拿模型的自述 id 当身份。
    raw_uncited = [s.sentence_id for s, r in zip(sentences, raw)
                   if not (r.get("citations") or ())]
    check_eq(len(sentences), 23, "留存回复里 23 句（与它自报的 sentence_count 一致）")
    check_eq(len(draft.subsections), 18, "18 个小节")
    check_eq(len(draft.gaps), 8, "8 条缺口")
    check_eq(len(uncited), 7, "其中 7 句一个字都没引")
    check_eq(raw_uncited, uncited,
             "7 条无引用句在**未解释的原始字节**上按位置再数一遍，仍是同一组")
    check(len({str(r.get("sentence_id")) for r in raw}) < len(raw),
          "模型自述的 `sentence_id` 在小节内重复，当不了身份——这正是那份归一化表存在的理由，"
          "也是上面那条比较必须**按位置**才对得上的原因")
    check(all(s.text.startswith("本次材料中未取得") or "本次材料中未" in s.text
              for s in draft.sentences() if s.sentence_id in set(uncited)),
          "这 7 句的正文都是「本次材料中未取得……」这类**说明缺口的话**——它们被写进了正文，"
          "而正文的每一句都应当引用本次清单成员")

    review_reply = json.loads(str(review_log["completion"]))
    rows = review_reply["issues"]
    no_citation = [str(r["sentence_id"]) for r in rows
                   if not str(r.get("citation_id") or "").strip()]
    check_eq(len(rows), 23, "审阅回复也是 23 行（逐句覆盖等式当时是满足的）")
    check_eq(sorted(no_citation), sorted(uncited),
             "**两份互不相干的留存互相印证**：审阅侧 `citation_id` 为空的那 7 行，正是写作侧"
             "没有引用的那 7 句——同一组 id，不是两次各自数的")
    for sentence_id in uncited:
        sentence = next(s for s in draft.sentences() if s.sentence_id == sentence_id)
        check_eq(tuple(sentence.citations), (),
                 f"{sentence_id} 的引用集是空的：`rvi-2` 要求 `citation_id` 取自**该句自己的"
                 f"引用集**，空集里挑不出任何合法取值——这一句给不出审阅单元")

    details.append("## §3 r28 三层不合规逐层重放（生产解析口怎么判，今天就怎么判）")
    empty_reason = [r for r in rows if not str(r.get("reason") or "").strip()]
    check_eq(len(empty_reason), 22, "23 行里 22 行的 `reason` 是空串")
    check_eq(len([r for r in rows if r.get("category") == "supported"]), 22,
             "22 行判 `supported`（连没有引用的那几句也被判了 supported）")
    check_eq(len(no_citation), 7, "7 行的 `citation_id` 是空串")
    # 第一层：空 `reason` 在构造期当场抛出——这是 r28 账本里记下的那一条，逐字重放。
    # `crr-9` 起这一层的**抛出类型**换了：解析口把这一条意见的任何不合约都收敛成链自己的
    # `CitedReviewError`（原样放行裸的 `AssuranceSchemaError` 时，那条异常**不带**是哪一条
    # 意见出错，整条审阅轴随之丢失）。判据的**内容**不变，反而更强：消息仍与账本同源，
    # 且现在带 `sentence_id` / `citation_id` 定位。
    first_empty = empty_reason[0]
    try:
        CR._issue_from_payload(first_empty, draft=draft,
                               bundle=SimpleNamespace(citation_ids=()), report_version="crpv_x")
    except CR.CitedReviewError as exc:
        check("不得为空字符串" in str(exc),
              f"生产解析口对空 `reason` 当场抛出，消息与账本记录同源：{exc}")
        check(exc.reason == "review_reply_unparsable"
              and exc.sentence_id == str(first_empty["sentence_id"]),
              "而且带**定位**（原因码 + 是哪一句），不再是裸的 schema 报错")
        check_eq(CHAIN._review_failure_kind(exc), "review_reply_unparsable",
                 "这条失败被归成 typed 原因码（不是无声的失败）")
        check(CHAIN._review_failure_kind(exc) in CRP.CITED_REVIEW_FAILURE_KINDS,
              "原因码属于封闭词表 `CITED_REVIEW_FAILURE_KINDS`")
    else:
        check(False, "空 `reason` 本来必须 fail-closed，但它通过了")
    # 第二层：无引用句**给不出任何合法的审阅单元**——`rvi-2` 要求 `citation_id` 非空，而这一句
    # 的引用集是空的：它连「挑一个自己的键」都做不到，也就没有合法的意见行。
    for row in [r for r in rows if not str(r.get("citation_id") or "").strip()]:
        try:
            CR._issue_from_payload({**row, "reason": "给它补一条理由再试"},
                                   draft=draft, bundle=SimpleNamespace(citation_ids=()),
                                   report_version="crpv_x")
        except CR.CitedReviewError as exc:
            check("citation_id" in str(exc),
                  f"{row['sentence_id']} 在 `rvi-2` 上给不出 `citation_id`（该句引用集为空），"
                  f"意见行造不出来：{exc}")
        else:
            check(False, f"{row['sentence_id']} 本该给不出合法审阅单元，但它通过了")
    # 而**当前**的生产范围门会明确地把这 7 句挡在审阅对象之外，并逐句具名——这不是本模块自己
    # 数的，是 `cited_review` 自己的判定。
    by_sentence = {s.sentence_id: s for s in draft.sentences()}
    try:
        CR._review_scope(draft=draft, reviewed=[s.sentence_id for s in draft.sentences()],
                         sentences=by_sentence)
    except CR.CitedReviewError as exc:
        message = str(exc)
        check(all(sid in message for sid in uncited),
              f"把这 7 句塞进审阅对象 ⇒ 生产范围门当场拒并**逐句具名**（{exc}）")
        check("缺 []" in message,
              "它同时说清「一句都不缺」：挡住的只有那 7 句无引用句，带引用的 16 句照旧可审")
    else:
        check(False, "审阅对象里混进零引用句本该 fail-closed，但它通过了")
    check_eq(CR._review_scope(draft=draft,
                              reviewed=[s.sentence_id for s in draft.sentences()
                                        if s.citations],
                              sentences=by_sentence), tuple(uncited),
             "只报**带引用**的那 16 句为审阅对象时，圈外句恰好是这 7 句——"
             "本批的修法就是把这个圈定写进请求面，而不是去放宽 `rvi-2`")


# ---------------------------------------------------------------------------
# §3b 机械硬错误与**错栏**：用生产判据在留存字节上重读一遍
# ---------------------------------------------------------------------------
#
# 上面那一段刻意**不**重算硬错误条数，理由是「没有清单就没有输入」。这个理由只对一半：
# `cited_reply_readback` 的判据面**只需要留存的请求面**（它自带 `materials[].aspect_ids`、
# `subsections[].declared_aspect_id`（历史单数键）、材料原文、事实行），因此凡是不需要清单侧对象的轴，
# 今天就能在 r28 的真实字节上读出同一个结论。剩下的轴则**逐条声明**「缺清单、判不了」——
# 留存缺口因此是被**圈定**的，不是一句笼统的「不可复算」。
#
# 这一段的判据是**生产模块**给的（`CRRB.diagnose`），本模块只读它的读数并逐条钉住。

#: 留存缺口圈定的读数：可判轴数 / 判不了的轴数。写在断言里而不是注释里——
#: 「有多少轴真的没判」是个会变的量，变了就必须有人看见。
_EXPECTED_ADJUDICATED_AXES = 7
_EXPECTED_UNADJUDICABLE_AXES = 12

#: r28 的两句**错栏**（生产判据逐句点名）。左：句 id、声明栏目；右：它实际引的材料。
_WRONG_COLUMN_SENTENCES = (("s0006", "company_business_main.app_scenarios", "m17"),
                           ("s0016", "company_business_model.tech_route", "m01"))


def _check_hard_errors_from_retained_bytes(check, check_eq, details) -> None:
    journal = json.loads(_JOURNAL.read_text(encoding="utf-8"))
    face = json.loads(journal["input"]["request_face"])
    draft, _rows = _draft_from_retained_reply(journal)
    diag = CRRB.diagnose(reply_text=journal["reply"]["text"], request_face=face)

    by_kind: dict[str, list[tuple[str, str]]] = {}
    for sub in diag.subsections:
        for row in sub.readings:
            for finding in row.findings:
                by_kind.setdefault(finding.check_kind, []).append(
                    (row.final_id, finding.verdict))

    def _hard(kind: str) -> list[str]:
        return [sid for sid, verdict in by_kind.get(kind, ())
                if verdict == CRRB.VERDICT_HARD_ERROR]

    hard_sentences = sorted({row.final_id for sub in diag.subsections
                             for row in sub.readings
                             if row.verdict == CRRB.VERDICT_HARD_ERROR})
    check_eq(len(hard_sentences), 11,
             "生产判据在**留存的回复 + 留存的请求面**上读出 11 句机械硬错误"
             "（指令里报的那个数，今天可复算）")
    uncited = sorted(s.sentence_id for s in draft.sentences() if not s.citations)
    check(all(sid in hard_sentences for sid in uncited),
          "那 7 句无引用句是这 11 句的**子集**（无引用本身是一条机械硬错误）")
    check_eq(_hard("citation_present"), uncited,
             "`citation_present` 这一轴点名的，恰是写作侧没有引用的那 7 句")

    # 错栏：这一轴**不是恒真**——它点名的那两句与「引用在场」是两回事。
    check_eq(_hard("aspect_attribution"),
             sorted(sid for sid, _aspect, _mat in _WRONG_COLUMN_SENTENCES),
             "错栏轴点名的**恰是**那两句：引用的材料没登记到本小节声明的那一栏"
             "（生产判据的判据面就是「`materials[].aspect_ids` ∩ 小节声明的栏目 ≠ ∅」）")
    material_aspects = {str(m.get("key") or ""): frozenset(m.get("aspect_ids") or ())
                        for m in (face.get("materials") or ())}
    #: 这份请求面是 r28 的**历史字节**，用的是 `cwm-6` 之前的**单数键**。本行因此**逐字**读
    #: 旧键——读回来的是「那一小节当时声明的那一栏」，与生产读回 `_declared_aspects_of` 的
    #: 旧 wire 回放是同一条口径。
    declared = {str(s.get("subsection_id") or ""): str(s.get("declared_aspect_id") or "")
                for s in (face.get("subsections") or ())}
    for sid, aspect, material in _WRONG_COLUMN_SENTENCES:
        row = next(r for sub in diag.subsections for r in sub.readings
                   if r.final_id == sid)
        check_eq(row.declared_aspect_ids, (aspect,), f"{sid} 所在小节声明的那一栏")
        check(material in row.citations,
              f"{sid} 确实引了 {material}（错栏判的是这一句，不是别的）")
        check(aspect not in material_aspects.get(material, frozenset()),
              f"{material} 的登记栏目里**没有** {aspect}——"
              "「材料在清单里」不等于「它支持这一栏」")
    # 反例面：同一批 16 句里，其余句子的引用**都**登记到了自己的栏目 ⇒ 这道轴不误伤。
    ok_rows = [r for sub in diag.subsections for r in sub.readings
               if r.citations and r.final_id not in
               {sid for sid, _a, _m in _WRONG_COLUMN_SENTENCES}]
    check(ok_rows and all(not any(f.check_kind == "aspect_attribution"
                                  for f in r.findings) for r in ok_rows),
          "带引用、且引用都落在本栏的句子**一条错栏都不报**（不是一道恒假门）")

    # 留存缺口的**边界**：哪些轴判不了、为什么，逐条列出来。
    adjudicated = [a["axis"] for a in diag.axes if a["status"] == "adjudicated"]
    unadjudicable = [a["axis"] for a in diag.axes
                     if a["status"] == "not_adjudicated_missing_manifest"]
    check_eq(len(adjudicated), _EXPECTED_ADJUDICATED_AXES,
             "能在留存字节上判的轴有 7 根（这几根只需请求面自带的东西）")
    check_eq(len(unadjudicable), _EXPECTED_UNADJUDICABLE_AXES,
             "判不了的轴有 12 根——每一根的理由都是**缺清单侧对象**，不是「判过但对」")
    check(all(a["basis"] for a in diag.axes if a["status"] != "adjudicated"),
          "判不了的轴逐条写明**缺的是哪一样**（缺口被圈定，不是一句笼统的「不可复算」）")
    check("current_state_scope" in unadjudicable and "table_cell_provenance" in unadjudicable,
          "旧材料当前化与表数字逐格这两轴落在**判不了**那一侧："
          "它们要的是清单侧的 `document_id` 与 `structured_view`，留存面里没有")
    check("material_adoption" in unadjudicable and "report_version_binding" in unadjudicable,
          "材料采用去向与版本锚同样判不了——它们绑的是**清单身份**（`member_ref`），"
          "而 r28 的清单没有留存。这就是为什么下面这一句必须说清："
          "「11 句硬错误可复算」**不**等于「这一轮的核对照旧完整」")

    details.append("## §3b 机械硬错误与错栏（生产判据 + 留存字节）")
    details.append(
        f"NOTE 逐轴读数：可判 {len(adjudicated)} 轴 / 判不了 {len(unadjudicable)} 轴；"
        f"硬错误 {len(hard_sentences)} 句"
        f"（`citation_present` {len(_hard('citation_present'))}、"
        f"`aspect_attribution` {len(_hard('aspect_attribution'))}、"
        f"`numeric_surface` {len(_hard('numeric_surface'))}、"
        f"`numeric_qualification` {len(_hard('numeric_qualification'))}）。"
        "数值轴那两条的具体读数、以及空格型成因归类，归 `test_m930_3_cited_reply_readback` 钉；"
        "本段只钉「条数可复算」「错栏可逐句具名」「缺口边界被圈定」三件事。")


# ---------------------------------------------------------------------------
# §4 新形态的纯缺口
# ---------------------------------------------------------------------------

class _StubProseClient:
    """最小替身：回放一段给定文本，不联网、不调用模型。"""

    model = "stub-model"
    thinking = {"type": "disabled"}

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def compose(self, *, messages, system, prompt_version, model_policy):
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        return CW.CitedProseResult(
            text=self.text, call_id="call-stub", model=self.model,
            prompt_version=prompt_version, status="ok", input_tokens=1, output_tokens=1,
            latency_ms=1, finish_reason="end_turn", error="")


def _pure_gap_payload(manifest: CW.CitedWriterInputManifest, *, gap_reason: str) -> str:
    """一节的最小合法返回：**第一栏写空段落 + 一条缺口**，其余小节照旧各写一句。

    这就是本批要求 Writer 学会的形态：没有可支持的正文时这一栏就写空段落，缺什么写在 `gaps`
    里，**不**在正文里写一句零引用的「本次未取得……」。
    """
    key = manifest.materials[0].citation_key
    subsections = []
    gaps = []
    for index, spec in enumerate(manifest.subsections):
        if index == 0:
            subsections.append({"subsection_id": spec.subsection_id, "title": spec.title,
                                "paragraphs": []})
            gaps.append({"subsection_id": spec.subsection_id,
                         "requirement_text": spec.requirement_text,
                         "reason": gap_reason, "detail": "夹具：这一栏本次没有合格数字事实"})
        else:
            subsections.append({"subsection_id": spec.subsection_id, "title": spec.title,
                                "paragraphs": [{"paragraph_id": "p1", "sentences": [
                                    {"sentence_id": "s1", "text": E._BODY_A,
                                     "citations": [key]}]}]})
    return json.dumps({"subsections": subsections, "gaps": gaps,
                       "follow_up_needs": []}, ensure_ascii=False)


def _check_pure_gap(check, check_eq, details) -> str:
    details.append("## §4 纯缺口：可以没有段落，但不得写成零引用正文")
    manifest = E._input_manifest(authority=None, facts=())
    first = manifest.subsections[0]
    registered = CW.registered_material_keys(manifest, first.declared_aspect_ids)
    check(bool(registered),
          f"反例前件：清单里**登记**了第一小节覆盖的栏目（{list(first.declared_aspect_ids)}）"
          f"的材料 {list(registered)}——所以「清单里没有材料」这句话与现场不符")

    client = _StubProseClient(_pure_gap_payload(manifest, gap_reason="no_source_in_manifest"))
    outcome = CW.write_cited_section(manifest=manifest, client=client, model_policy="stub")
    draft = outcome.draft
    check_eq(len(client.calls), 1, "写作调用一次（本模块不发真实请求，替身也只被调一次）")
    body_of_first = [s for sub in draft.subsections if sub.subsection_id == first.subsection_id
                     for para in sub.paragraphs for s in para.sentences]
    check_eq(body_of_first, [], "第一栏**一句话都没有**：没有可支持的正文时它照旧出现在小节里，"
                                "但 `paragraphs` 是空的")
    check(all(s.citations for s in draft.sentences()),
          "整节里不存在任何零引用的正文句（「说明缺口」那句话没有进正文）")
    gaps = [g for g in draft.gaps if g.subsection_id == first.subsection_id]
    check_eq(len(gaps), 1, "第一栏的缺口**恰好一条**，写在 `gaps` 里")
    gap = gaps[0]
    check_eq(gap.reason, "source_present_but_not_admissible",
             "读者面拿到的是**核实后**的缺口原因：材料在清单里，只是没有合格的数字事实")
    check_eq(gap.reason_assignment, "system_reassigned_from_manifest",
             "改判留下判定来源，不是静默换词")
    check_eq(gap.claimed_reason, "no_source_in_manifest",
             "模型当时自述的说法**原样**留在 `claimed_reason`（诊断面不丢原始读数）")

    # 这一节因此**没有**可审对象：零引用句不给审阅单元，也不该白烧一次审阅调用。
    check_eq(CHAIN._review_no_object_failure(draft), "review_no_reviewable_sentence",
             "没有一句带引用的话 ⇒ 不发审阅调用（省下一次真实调用，而不是拿一次调用去审 0 句）")
    request = CR.build_cited_review_request(draft=draft, manifest=manifest)
    check_eq(request.payload["sentences"], [],
             "审阅请求面里没有任何可审的句子（这个键**始终存在**，空表与「没这个检查」可区分）")
    check_eq(request.payload["uncited_sentence_ids"], [],
             "而排除集也是空的：这一节根本没有零引用句——「没有可审对象」与「把零引用句挡在"
             "门外」是两件事，读者面能分开看")

    # 反例：模型不改习惯，仍在这一栏里写一句「本次未取得……」的零引用话，并且**同栏**另写一句
    # 带引用的正文（这样这一节仍有可审对象，考的是「有可审的」与「有问题的」是两条轴）。
    key = manifest.materials[0].citation_key
    zero_led = _StubProseClient(_pure_gap_payload(manifest, gap_reason="no_source_in_manifest"))
    bad = json.loads(zero_led.text)
    bad["subsections"][0]["paragraphs"] = [{"paragraph_id": "p1", "sentences": [
        {"sentence_id": "s1", "text": E._BODY_A, "citations": [key]},
        {"sentence_id": "s2", "text": "本次材料中未取得本栏要求的合格数字事实。",
         "citations": []}]}]
    zero_led.text = json.dumps(bad, ensure_ascii=False)
    bad_draft = CW.write_cited_section(manifest=manifest, client=zero_led,
                                       model_policy="stub").draft
    report = SC.check_cited_prose(draft=bad_draft, manifest=manifest)
    blocked = [s for s in bad_draft.sentences() if not s.citations]
    check_eq(len(blocked), 1, "反例确实构造出了那一句零引用正文")
    check(blocked[0].sentence_id in report.blocked_sentence_ids,
          "旧的写法今天照旧被判**硬错误**——本批**没有**为了让它好看而放宽硬对照")
    check_eq(CHAIN._review_no_object_failure(bad_draft), "",
             "但这一节仍有可审对象（同栏那句带引用的正文），所以审阅照跑——"
             "「有可审的」与「有问题的」是两条轴")
    bad_request = CR.build_cited_review_request(draft=bad_draft, manifest=manifest)
    check_eq(bad_request.payload["uncited_sentence_ids"], [blocked[0].sentence_id],
             "请求面把这句零引用正文**具名排除**并声明出来：审阅者看不到它，也不会为它出意见")
    check_eq([s["sentence_id"] for s in bad_request.payload["sentences"]],
             [s.sentence_id for s in bad_draft.sentences() if s.citations],
             "审阅对象**恰好**是本节里带引用的那些句子——零引用句既不进对象，也不被静默跳过")
    return draft.draft_id


# ---------------------------------------------------------------------------
# §5／§6 ③ 轴的边界与失败时的可读草稿
# ---------------------------------------------------------------------------

def _fixture():
    """§5–§7 的夹具：复用 `test_m930_3_cited_writer` 的真实 PackSet / 材料 / 事实入口。"""
    task, authority = E._task_and_authority()
    facts = E._scan_facts(authority, task)
    manifest = E._input_manifest(authority=authority, facts=facts)
    spec = manifest.subsections[0]
    sentences = (
        CW.CitedSentence("s1", E._BODY_A, ("m01",)),
        CW.CitedSentence("s2", "报告期内公司主营业务收入为 999.99 万元。", ("m02",)),
        CW.CitedSentence("s3", E._FACT_TEXT, ("f01",)),
    )
    draft = CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id=manifest.manifest_id, writer_identity="stub-writer",
        subsections=(CW.CitedSubsection(
            subsection_id=spec.subsection_id, title=spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=sentences),)),))
    check_report = SC.check_cited_prose(draft=draft, manifest=manifest)
    request = CR.build_cited_review_request(draft=draft, manifest=manifest)
    anchor = CRP.derive_report_version(draft=draft, manifest=manifest)
    bundle = CR.build_cited_review_bundle(
        draft=draft, manifest=manifest, request=request, report_version=anchor,
        report_id="rep-robustness", model_policy_id="mp-stub")
    return manifest, draft, check_report, bundle, anchor


def _review_json(rows) -> str:
    return json.dumps({"issues": list(rows)}, ensure_ascii=False)


def _row(sentence_id, citation_id, *, category="supported", reason="该句与其引用一致",
         semantic="", severity="none", blocking=False, evidence=(), target=None) -> dict:
    return {"sentence_id": sentence_id, "citation_id": citation_id, "category": category,
            "semantic_category": semantic, "severity": severity, "blocking": blocking,
            "reason": reason, "evidence_refs": list(evidence), "suggested_target": target}


def _parse(text, *, draft, bundle, anchor, check_report=None, **kw):
    return CR.parse_cited_review(
        text, draft=draft, bundle=bundle, report_version=anchor,
        review_producer_kind=kw.pop("review_producer_kind", "independent_llm_review"),
        check_report=check_report, **kw)


def _check_supported_needs_reason(check, check_eq, details, manifest, draft, check_report,
                                  bundle, anchor) -> None:
    details.append("## §5 `supported` 也必须有非空理由：两种「没表态」不得混同")
    text = _review_json([_row("s1", "m01", reason=""), _row("s2", "m02"), _row("s3", "f01")])
    try:
        _parse(text, draft=draft, bundle=bundle, anchor=anchor, check_report=check_report)
    except CR.CitedReviewError as exc:
        check("不得为空字符串" in str(exc) and exc.reason == "review_reply_unparsable",
              f"`supported` 带空理由 ⇒ 当场拒（协议要求的非空理由在结构上被守住）：{exc}")
    else:
        check(False, "空理由的 `supported` 本该 fail-closed，但它通过了")
    filled = _review_json([_row("s1", "m01", reason="与 `m01` 第 1 段原文一致，未见越界"),
                           _row("s2", "m02"), _row("s3", "f01")])
    outcome = _parse(filled, draft=draft, bundle=bundle, anchor=anchor,
                     check_report=check_report)
    check_eq(outcome.sentence_verdicts[0], ("s1", "supported"),
             "填了非空理由的 `supported` 照旧通过——本批加的是**理由**这一项义务，"
             "不是把 `supported` 变成不许说")


def _check_axis_three_boundary(check, check_eq, details, manifest, draft, check_report,
                               bundle, anchor) -> None:
    details.append("## §6 ③ 不得越过 ②；审阅失败也留得下可读草稿")
    check_eq(check_report.blocked_sentence_ids, ("s2",),
             f"夹具前件：机械层只判 s2 硬错误（实测 {check_report.blocked_sentence_ids}）")

    # (a) 审阅对硬错误句说 supported ⇒ 记下分歧，不解析失败；但它的「没问题」换不掉硬错误。
    override = _parse(_review_json([_row("s1", "m01"), _row("s2", "m02"),
                                    _row("s3", "f01")]),
                      draft=draft, bundle=bundle, anchor=anchor, check_report=check_report)
    check_eq(override.hard_error_override_sentence_ids, ("s2",),
             "审阅在硬错误句上说了 supported ⇒ 记一条**分歧**，不是解析失败")
    check_eq(override.hard_error_sentence_ids, ("s2",),
             "机械层的硬错误集照样在结果里（两条轴各自留档，谁也不顶替谁）")
    version = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report,
        review_outcome=override, report_id="rep-robustness")
    check_eq(version.system_review_state, "system_review_not_passed",
             "③ 轴**不可能**升到 `passed_awaiting_human`：审阅没提 blocking，拦住它的是机械层")
    check_eq(version.blocking_issue_count, 0,
             "反例前件成立：这份审阅一条 blocking 都没提——挡住升格的确实是硬错误")
    try:
        CRP.CitedReportVersion(**{
            **{n: getattr(version, n) for n in CRP.CitedReportVersion.__dataclass_fields__},
            "system_review_state": "system_review_passed_awaiting_human"})
    except CRP.CitedReportError as exc:
        check("不得写" in str(exc) and "硬错误" in str(exc),
              f"直接构造也拦得住：③ 轴写成 passed 而本节有硬错误即拒（{exc}）")
    else:
        check(False, "「有硬错误却写 passed」本该 fail-closed，但它通过了")

    # (b) 审阅解析失败 ⇒ 正文、逐句硬核对、缺口与补件照旧可读，且明确「不可发布／审阅未完成」。
    r28_shaped = _review_json([_row("s1", "m01", reason=""), _row("s2", "m02", reason=""),
                               _row("s3", "f01", reason="")])
    failure_kind = ""
    try:
        _parse(r28_shaped, draft=draft, bundle=bundle, anchor=anchor,
               check_report=check_report)
    except CR.CitedReviewError as exc:
        failure_kind = CHAIN._review_failure_kind(exc)
    check_eq(failure_kind, "review_reply_unparsable",
             "r28 形状的回复（空理由）在今天仍被判成一次**未完成的审阅**，有 typed 原因码")
    degraded = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=None,
        report_id="rep-robustness", review_failure=failure_kind)
    check_eq(degraded.process_state, "flow_incomplete",
             "流程状态如实记成**没跑完**（不是「跑完了、没意见」）")
    check_eq(degraded.system_review_state, "system_review_not_run",
             "③ 轴是 `not_run`，而不是任何形式的「审过」")
    check("review_not_completed" in degraded.release_blockers(),
          "「若要发布还差什么」里如实列出「审阅没跑完」")
    preview = CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=None,
        version=degraded)
    check_eq(len(preview.rows), 3,
             "**23 句草稿不再整节消失**：三句正文逐句还在预览里（本批之前这里什么都没有）")
    check(all(row.review_state == "review_not_completed" and not row.ok
              for row in preview.rows),
          "每一行的审阅档位都是「未完成」且 `ok` 为假：没被独立看过 ≠ 看过没问题")
    check(all(row.text for row in preview.rows), "正文原文逐句可读，一个字没丢")
    markdown = CRP.render_cited_preview_markdown(preview)
    check("独立审阅未完成" in markdown and "降级诊断预览" in markdown,
          "读者面上明确写「独立审阅未完成／降级诊断预览」")
    check("不可发布" in markdown and "system_review_passed_awaiting_human" not in markdown,
          "预览照旧声明不可发布，且**永不**出现「系统审阅已通过」")
    check("本次未取得" not in markdown or "缺口" in markdown,
          "缺口在预览里有自己的段落，不被读成正文")


# ---------------------------------------------------------------------------
# §7 财务呈现层
# ---------------------------------------------------------------------------

def _metric_table(*, section_id: str, keys: tuple[str, ...]) -> CFT.CitedMetricTable:
    return CFT.CitedMetricTable.create(
        section_id=section_id, caption="主要财务指标（夹具）",
        header=("指标", "本期", "上期"), entity_scope="演示主体（demo）",
        unit="CNY", period_basis="duration",
        rows=(CFT.CitedMetricTableRow(
            label="营业收入", unit="CNY", cells=("1.00", ""), fact_ids=("fx",),
            citation_keys=keys, period_texts=("本期",)),))


def _check_financial_layer(check, check_eq, details, manifest, draft, check_report,
                           bundle, anchor) -> None:
    details.append("## §7 财务呈现层：新规则不改动权威表与它的呈现")
    table = _metric_table(section_id=manifest.section_id, keys=("f01",))
    outcome = CFT.CitedMetricTableOutcome(
        section_id=manifest.section_id, schema_version=CFT.CITED_METRIC_TABLE_SCHEMA_VERSION,
        rule_version=CFT.CITED_METRIC_TABLE_RULE_VERSION, authority_kind="financial_workflow",
        tables=(table,))
    review = _parse(_review_json([_row("s1", "m01"), _row("s2", "m02"), _row("s3", "f01")]),
                    draft=draft, bundle=bundle, anchor=anchor, check_report=check_report)
    version = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=review,
        report_id="rep-robustness", metric_tables=(table,))
    check_eq(version.metric_table_ids, (table.table_id,),
             "指标表 id 照旧进版本记录（本批没有动它的身份轴）")
    check(bool(version.metric_tables_fingerprint),
          "聚合指纹照旧在（表一变记录就变这条不变式没被本批碰到）")
    check_eq(version.system_review_state, "system_review_not_passed",
             "机械硬错误照旧挡住 ③ 轴——加表**不**等于正文没问题")
    preview = CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=review,
        version=version, metric_table_outcome=outcome)
    check_eq(tuple(t.table_id for t in preview.metric_table_outcome.tables), (table.table_id,),
             "预览里的表格与它声称的那一版是同一条记录（表格呈现路径没被本批改变）")
    markdown = CRP.render_cited_preview_markdown(preview)
    check(table.caption in markdown and version.metric_tables_fingerprint in markdown,
          "预览照旧印出表格与它的指纹：读者仍可据此核对「我读的格子属于哪一版」")
    check("财务指标表" in markdown,
          "财务表在自己的段落里（呈现层路由与权威表读法一个字没动）")
    failed_review = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=None,
        report_id="rep-robustness", metric_tables=(table,),
        review_failure="review_reply_unparsable")
    check_eq(failed_review.metric_table_ids, (table.table_id,),
             "反例：审阅失败**不**顺带丢掉权威表——③ 轴与呈现层是两条轴，表照旧进记录")
    check_eq(failed_review.publishability, "not_publishable",
             "表在也不改变发布资格：预览恒不可发布")


def _run_checks(check, check_eq, details) -> None:
    _check_retained_bytes(check, check_eq, details)
    _check_hard_errors_from_retained_bytes(check, check_eq, details)
    _check_pure_gap(check, check_eq, details)
    manifest, draft, check_report, bundle, anchor = _fixture()
    _check_supported_needs_reason(check, check_eq, details, manifest, draft, check_report,
                                  bundle, anchor)
    _check_axis_three_boundary(check, check_eq, details, manifest, draft, check_report,
                               bundle, anchor)
    _check_financial_layer(check, check_eq, details, manifest, draft, check_report,
                           bundle, anchor)


def main() -> dict:
    details: list[str] = []
    passed = failed = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS  {msg}")
        else:
            failed += 1
            details.append(f"FAIL  {msg}")

    def check_eq(actual, expected, msg: str) -> None:
        check(actual == expected, f"{msg}（实得 {actual!r}，应为 {expected!r}）")

    missing = _missing()
    if missing:
        return {"passed": 0, "failed": 0, "skipped": 1,
                "details": [f"SKIP r28 留存字节缺失，本模块不猜、不静默绿：{missing}"]}

    before = {p: _sha256_bytes(p.read_bytes()) for p in (_JOURNAL, _LEDGER)}
    _run_checks(check, check_eq, details)
    after = {p: _sha256_bytes(p.read_bytes()) for p in (_JOURNAL, _LEDGER)}
    check_eq([after[p] for p in before], [before[p] for p in before],
             "本模块是**只读**回放：r28 的两份留存字节一轮跑完一个字节都没变")
    details.append("NOTE 本模块只回放 r28 的**留存字节**并证明本批修复的判据；"
                   "它不宣称 M930-3、TS5、正式树门或后续阶段关闭，也不产生任何放行结论。"
                   "真实审阅调用与 create-only run 须另经单独授权。")
    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = main()
    for line in result["details"]:
        print(line)
    print(f"\npassed = {result['passed']}, failed = {result['failed']}, "
          f"skipped = {result['skipped']}")

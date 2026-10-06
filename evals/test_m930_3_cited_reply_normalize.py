"""Eval: 写作返回的**纯结构归一**（`crn-4`）——只删可证明是副本的缺口/空壳段，编号由程序给。

用法: python -m evals.test_m930_3_cited_reply_normalize

缘起是 M930-3 真实 r2（call_id `5856a5b820a14b2ca50bd3e31924264d`）那一次返回：缺口在顶层与
小节内各写一份（整份返回因此在 schema 层被判「未登记字段」而作废），`sentence_id` 在每个小节
里都从 `s01` 重新起算（27 句共用 5 个 id）。两件事都是**记账**问题，而记账不该由写入方负责。

`crn-2` 加的是**同一件记账的第三种形态**（§6）：M930-5 全过程真实 run
`m930_3_cited_upload_20261005T091926Z` 的 `p10` 把「这一栏没有来源」写成一个
`"sentences": []` 的空壳段，而 `CitedParagraph` 对空段落 fail-closed ⇒ **整节**作废
（同一次返回里其余 9 段 30 句一个字都留不下来）。

`crn-3` 给这第三种形态补的是**第六条边界**（§6g）：待删段声明的**每一栏都必须属于它自己所在
的小节**。借用别的小节的「零来源 + 缺口」来造空壳，说明的不是「我这里没有来源」，而是
「我这里声明了本小节不负责的栏目」——那是另一件更重的事，有专门的门
（`cited_writer._check_paragraph_aspects` / `paragraph_aspect_out_of_subsection`）判它；
本条归一**不得**抢先把整段删掉、让那道门无对象可判。

`crn-4` 加的是**第二条删除依据**（§6h）：另一类真实 run
（`m930_3_cited_upload_20261005T131532Z`）里同一个 `p10` 声明的三栏客户集中度**在请求面登记着
来源**（`m47/m51/m52`），模型自述的 `no_source_in_manifest` 被 `assign_gap_reason` **系统改判**
为 `source_present_but_not_admissible`——于是 `crn-3` 的「零登记来源」这一条不成立，整节再次
作废。新依据要求：该栏在**同一小节**内有一条**可唯一对应**的顶层缺口，其**系统判定**理由落在
`GAP_SUPPORTED_REASONS`。`crn-3` 的原口径**一字未改**，两条依据是**并集**；删掉的段零句零引用，
不计入栏目覆盖、事实资格、审阅通过与系统放行。

本模块钉住的是**归一的边界**，不是它的措辞。逐条证明：

1. **删除只对「可证明是同一件事的第二份副本」生效**：小节内那份缺口与顶层同小节的缺口逐字段
   完全相同（按**多重集**，含条数）才删；`detail` 差一个字、少抄一条、多抄一条、或「有键但是
   空表而顶层有」——四种不一致全部 fail-closed，绝不挑一份留下来。
2. **「没有这一份副本」是正常形状，不是缺陷**：小节里**没有** `gaps` 键的返回原样通过。
3. **编号由程序按结构给**：最终 `sentence_id` 按「小节 → 段落 → 句子」稳定遍历顺序取全节唯一
   值；模型原 ID、结构位置、新 ID 三者在映射里同时在场。
4. **归一化只重命名，不补编号**：空 `sentence_id` 仍然 fail-closed——否则「模型漏了编号」会被
   静默改写成「模型写了编号」。
5. **解析边界再断一次唯一**：绕过归一化的路径（手改的返回、将来的新调用方）在同一条线上失败。
   不同句被同一个 ID 合并，核对与审阅都会读错对象，而那种错误在结论里看不出来。
6. **归一不动正文**：句文本、引用键、段落划分逐字保持；输入对象本身不被改动（读回侧还要用它
   与派生稿对差额）。
7. **幂等**：对归一结果再归一，得到同一份 payload、且这次没有任何可删的副本。
8. **纯空壳段只在六项同时满足时被删**（§6）：字段集恰好三项、`sentences` 是空列表、
   `paragraph_id` 非空、`aspect_ids` 非空且逐条非空无重复、它声明的**每一栏都属它自己所在的
   小节**（§6g，第六条），且这些栏都同时「请求面零登记来源」与「顶层已有系统判定为
   `no_source_in_manifest` 的对应缺口」。缺任何一项 ⇒ 原样留下，由构造器照旧抛
   `empty_paragraph`。**删的方向是单向的**：读数算错只会让段**留下来**，不可能把一次本应失败
   的解析变成成功。被删的段**不计入栏目覆盖**。

夹具是**合成的 wire**（dict），无公司代号、文件名、页码或答案关键词；不调 LLM、不联网、不写库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import cited_reply_normalize as CRN   # noqa: E402
from sections import cited_writer as CW             # noqa: E402


def _sentence(model_id: str, text: str, citations=()) -> dict:
    return {"sentence_id": model_id, "text": text, "citations": list(citations)}


def _para(paragraph_id: str, *sentences: dict, aspect_ids=None) -> dict:
    row = {"paragraph_id": paragraph_id, "sentences": list(sentences)}
    #: 段级声明（`cw-4`）**默认不写**：这个模块的夹具整套共用，给默认值会让它们悄悄多带一个
    #: 自己没打算验的字段。要验那一条的显式传 `aspect_ids=...`。
    if aspect_ids is not None:
        row["aspect_ids"] = aspect_ids
    return row


def _sub(subsection_id: str, *paras: dict, gaps=None) -> dict:
    row = {"subsection_id": subsection_id, "title": subsection_id, "paragraphs": list(paras)}
    if gaps is not None:
        row["gaps"] = gaps
    return row


def _gap(subsection_id: str, detail: str = "本次输入里没有该事实。") -> dict:
    return {"subsection_id": subsection_id, "requirement_text": f"{subsection_id} 的要求",
            "reason": "no_source_in_manifest", "detail": detail}


def _reply(*subs: dict, gaps=(), follow_up_needs=()) -> dict:
    return {"subsections": list(subs), "gaps": list(gaps),
            "follow_up_needs": list(follow_up_needs)}


def _expect_fail(fn, *, token: str, reason: str) -> str:
    """这一路必须 fail-closed，且原因码是**typed** 的（读回侧要按码分流，不能靠认字符串）。"""
    try:
        fn()
    except CW.CitedWriterError as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(f"异常消息里没有 {token!r}：{message}") from None
        if exc.reason != reason:
            raise AssertionError(
                f"原因码不是 {reason!r} 而是 {exc.reason!r}：{message}") from None
        return message
    raise AssertionError(f"这一路必须 fail-closed（期望原因码 {reason!r}）")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ============================================ §1 副本删除的边界（含四种不一致）
    details.append("## §1 小节内缺口副本：只有「逐字段完全相同」才删")
    outer = _gap("sub-a")
    shared = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")), gaps=[dict(outer)]),
                    gaps=[dict(outer)])
    result = CRN.normalize_cited_reply(json.loads(json.dumps(shared)))
    check("gaps" not in result.payload["subsections"][0],
          "小节内那一份与顶层逐字段相同时被**确定性删除**（键整个消失，不是留空表）")
    check(len(result.stripped_subsection_gaps) == 1
          and result.stripped_subsection_gaps[0].subsection_id == "sub-a",
          "被删的那一份作为**台账**交出（不是悄悄丢掉）")
    check(result.stripped_subsection_gaps[0].detail == outer["detail"],
          "台账里的缺口内容与顶层那条逐字相同 ⇒ 事后可证明删的是副本")
    check(len(result.payload["gaps"]) == 1,
          "顶层那份**保留**（删的是第二份副本，不是这一件事本身）")
    details.append("NOTE §1a：删的是副本，顶层原件一字不动。")

    altered = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")),
                          gaps=[{**outer, "detail": "本次输入里没有该事实"}]),
                     gaps=[dict(outer)])
    _expect_fail(lambda: CRN.normalize_cited_reply(altered),
                 token="不是**逐字段相同", reason="subsection_gap_mismatch")
    details.append("NOTE §1b：`detail` 差一个字 ⇒ 拒（不挑一份留下来）。")

    two_top = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")), gaps=[dict(outer)]),
                     gaps=[dict(outer), _gap("sub-a", detail="另一件事。")])
    _expect_fail(lambda: CRN.normalize_cited_reply(two_top),
                 token="不是**逐字段相同", reason="subsection_gap_mismatch")
    details.append("NOTE §1c：顶层两条、小节内一条（少抄）⇒ 拒。")

    two_inside = _reply(
        _sub("sub-a", _para("p1", _sentence("s01", "甲。")),
             gaps=[dict(outer), _gap("sub-a", detail="多出来的一条。")]),
        gaps=[dict(outer)])
    _expect_fail(lambda: CRN.normalize_cited_reply(two_inside),
                 token="不是**逐字段相同", reason="subsection_gap_mismatch")
    details.append("NOTE §1d：小节内多抄一条 ⇒ 拒（两处记账已经不一致）。")

    empty_inside = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")), gaps=[]),
                          gaps=[dict(outer)])
    _expect_fail(lambda: CRN.normalize_cited_reply(empty_inside),
                 token="不是**逐字段相同", reason="subsection_gap_mismatch")
    details.append("NOTE §1e：「有键但是空表而顶层有」⇒ 拒：空表不等于「没有这一份副本」。")

    # 「缺字段」与「显式 null」**不是**同一件事。`gap.get(name)` 会把两者都变成 `None`：
    # 于是「少抄一个字段」的副本能被读成「抄了一个 null」的副本，一份**不完整**的副本会被当成
    # **完全相同**的副本删掉。两条：先在**存在性**上拒，再在比较上分。
    missing_field = dict(outer)
    missing_field.pop("detail")
    _expect_fail(lambda: CRN.normalize_cited_reply(
        _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。"))),
               gaps=[dict(missing_field)])),
        token="缺少登记字段", reason="gap_field_missing")
    explicit_null = {**outer, "detail": None}
    _expect_fail(lambda: CRN.normalize_cited_reply(
        _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")),
                    gaps=[dict(explicit_null)]),
               gaps=[dict(outer)])),
        token="不是**逐字段相同", reason="subsection_gap_mismatch")
    both_null = CRN.normalize_cited_reply(
        _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")),
                    gaps=[dict(explicit_null)]),
               gaps=[dict(explicit_null)]))
    check(len(both_null.stripped_subsection_gaps) == 1,
          "⇒ 两边**都**显式写 null 时仍判为同一份副本（`None` 与 `None` 当然相等）"
          "——被区分的只是「缺席」")
    check(CRN.iter_gap_bodies([{**outer, "detail": None}])
          != CRN.iter_gap_bodies([{k: v for k, v in outer.items() if k != "detail"}]),
          "⇒ **在场性视图**里「没有这个键」与「这个键是 null」是两个不同的串"
          "（缺席用哨兵编码，`None` 原样保留）")
    details.append("NOTE §1h：缺字段与显式 null 走**不同**的 reason（`gap_field_missing` / "
                   "`subsection_gap_mismatch`）——两者都是记账不一致，但不是同一种。")

    no_key = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。"))), gaps=[dict(outer)])
    plain = CRN.normalize_cited_reply(no_key)
    check(not plain.stripped_subsection_gaps,
          "小节里**没有** `gaps` 键 ⇒ 原样通过、零删除（这正是正常形状）")
    check(all("gaps" not in s for s in plain.payload["subsections"]),
          "⇒ 归一后也不凭空多出一个空的 `gaps` 键")
    details.append("NOTE §1f：「模型没有重复抄写」不得被读成缺陷。")

    stranger = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。"))),
                      gaps=[_gap("sub-zzz")])
    _expect_fail(lambda: CRN.normalize_cited_reply(stranger),
                 token="不在本次返回的小节里", reason="gap_subsection_unknown")
    details.append("NOTE §1g：顶层缺口指向本次没有的小节 ⇒ 拒（两处记账已脱钩）。")

    bad_gap = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。"))),
                     gaps=[{**outer, "severity": "high"}])
    _expect_fail(lambda: CRN.normalize_cited_reply(bad_gap),
                 token="未登记字段", reason="gap_field_malformed")
    bad_sub = _reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。"))), gaps=[dict(outer)])
    bad_sub["subsections"][0]["confidence"] = 0.9
    _expect_fail(lambda: CRN.normalize_cited_reply(bad_sub),
                 token="未登记字段", reason="subsection_field_malformed")
    bad_reply = dict(_reply(_sub("sub-a", _para("p1", _sentence("s01", "甲。")))))
    bad_reply["notes"] = "模型自己加的字段"
    _expect_fail(lambda: CRN.normalize_cited_reply(bad_reply),
                 token="未登记字段", reason="reply_field_malformed")
    details.append("NOTE §1h：缺口/小节/顶层任一含未登记字段 ⇒ 各自 typed 拒收"
                   "（r2 那次正是整份返回栽在这里）。")

    # ============================================ §2 编号由程序给（全节唯一）
    details.append("## §2 `sentence_id` 由解析边界按结构分配，全节唯一")
    many = _reply(
        _sub("sub-a", _para("p1", _sentence("s01", "甲一。"), _sentence("s02", "甲二。")),
             _para("p2", _sentence("s01", "甲三。"))),
        _sub("sub-b", _para("p1", _sentence("s01", "乙一。"), _sentence("s01", "乙二。"))))
    source = json.loads(json.dumps(many))
    normalized = CRN.normalize_cited_reply(many)
    ids = CRN.section_sentence_ids(normalized.payload)
    check(list(ids) == ["s0001", "s0002", "s0003", "s0004", "s0005"],
          f"5 句得到全节唯一且按遍历顺序的编号：{list(ids)}")
    check(len(set(ids)) == len(ids), "⇒ 全节没有任何两句共用同一个 ID")
    check([a.model_id for a in normalized.sentence_ids] == ["s01", "s02", "s01", "s01", "s01"],
          "映射逐条保留**模型原 ID**（模型当初写过什么不会消失）")
    check([[a.subsection_index, a.paragraph_index, a.sentence_index]
           for a in normalized.sentence_ids]
          == [[1, 1, 1], [1, 1, 2], [1, 2, 1], [2, 1, 1], [2, 1, 2]],
          "映射逐条带**结构位置**（小节/段落/句下标，1 起）")
    check([a.subsection_id for a in normalized.sentence_ids]
          == ["sub-a", "sub-a", "sub-a", "sub-b", "sub-b"],
          "映射逐条带所属小节 ⇒ 「读者面看到的是哪一句」可回查")
    details.append("NOTE §2a：编号不是模型自觉，是遍历顺序的函数。")

    texts_before = [s["text"] for sub in source["subsections"]
                    for p in sub["paragraphs"] for s in p["sentences"]]
    texts_after = [s["text"] for sub in normalized.payload["subsections"]
                   for p in sub["paragraphs"] for s in p["sentences"]]
    check(texts_before == texts_after, "归一**不动正文**：句文本逐字保持、顺序保持")
    check(CRN.section_sentence_ids(source)[0] == "s01",
          "输入对象**未被改动**（读回侧要用原文与派生稿对差额）")
    details.append("NOTE §2b：归一改的是记账，不是内容。")

    second = CRN.normalize_cited_reply(normalized.payload)
    check(second.payload == normalized.payload and not second.stripped_subsection_gaps,
          "**幂等**：对归一结果再归一只得到同一份 payload、且没有可删的副本")
    details.append("NOTE §2c：归一第二次跑不会继续改写。")

    # ============================================ §3 空 ID 与绕道唯一性
    details.append("## §3 空编号仍然拒绝；解析边界再断一次唯一")
    blank = _reply(_sub("sub-a", _para("p1", _sentence("", "甲。"))))
    _expect_fail(lambda: CRN.normalize_cited_reply(blank),
                 token="归一化只重命名，不替模型补编号", reason="sentence_id_empty")
    details.append("NOTE §3a：归一化只重命名，不补编号——否则「漏写」会被改写成「写了」。")

    handcrafted = _reply(_sub("sub-a", _para("p1", _sentence("s0001", "甲。"),
                                             _sentence("s0001", "乙。"))))
    _expect_fail(lambda: CRN.verify_section_unique_sentence_ids(handcrafted),
                 token="全节句子 ID 不唯一", reason="sentence_id_duplicate")
    CRN.verify_section_unique_sentence_ids(normalized.payload)
    check(True, "归一后的 payload 通过同一条唯一性断言（两个方向都试过）")
    check(CRN.derive_sentence_id(7) == "s0007" and CRN.derive_sentence_id(1) == "s0001",
          "`derive_sentence_id` 定宽、从 1 起：同一序号在任何一次运行里都给同一个字符串")
    details.append("NOTE §3b：绕过归一化的路径在同一条线上失败"
                   "（不同句被同一 ID 合并，核对与审阅都会读错对象）。")

    # ============================================ §4 台账形状
    details.append("## §4 两份台账可落盘、可回查")
    body = normalized.to_dict()
    check(body["normalize_version"] == CRN.CITED_REPLY_NORMALIZE_VERSION,
          f"台账带版本号 `{body['normalize_version']}`")
    check(len(body["sentence_ids"]) == 5 and len(body["stripped_subsection_gaps"]) == 0,
          "台账逐条落盘（本份没有可删副本 ⇒ 该表为空，是**空**不是缺字段）")
    check(json.dumps(body, ensure_ascii=False, sort_keys=True)
          == json.dumps(normalized.to_dict(), ensure_ascii=False, sort_keys=True),
          "同一份归一结果两次序列化逐字相同（可回查、可对账）")
    details.append("NOTE §4：台账只含结构事实，不含任何对正文的评价。")

    # ==================================================== §5 段级声明（`cw-4`）
    details.append("## §5 段级 `aspect_ids`：缺席合法，出现就得是**字符串数组**")
    with_decl = CRN.normalize_cited_reply(_reply(
        _sub("sub-a", _para("p1", _sentence("s1", "甲。", ("m01",)),
                            aspect_ids=["aspect-a", "aspect-b"]))))
    got_para = with_decl.payload["subsections"][0]["paragraphs"][0]
    check(got_para["aspect_ids"] == ["aspect-a", "aspect-b"],
          "正例：段级声明逐条原样穿过归一（归一改的是编号，不改声明）")
    check("aspect_ids" not in CRN.normalize_cited_reply(_reply(
        _sub("sub-a", _para("p1", _sentence("s1", "甲。", ("m01",))))))
        .payload["subsections"][0]["paragraphs"][0],
          "反例：不写这一字段 ⇒ 归一也**不**凭空补一个空数组"
          "（「本段不对应任何栏目」与「本段声明了一个空集合」是两件事，缺席就保持缺席）")
    _expect_fail(
        lambda: CRN.normalize_cited_reply(_reply(
            _sub("sub-a", _para("p1", _sentence("s1", "甲。", ("m01",)),
                                aspect_ids="aspect-a")))),
        token="aspect_ids 必须是列表", reason="paragraph_field_malformed")
    details.append("NOTE 5a：给一个字符串当数组，下游 `tuple(...)` 会把它拆成单个字——"
                   "那种坏形会装成「这一段声明了一堆栏目」，读回来的越界判定只会报越界，"
                   "读不出「本来就是个坏形」。所以在归一这道边界就拒。")
    _expect_fail(
        lambda: CRN.normalize_cited_reply(_reply(
            _sub("sub-a", _para("p1", _sentence("s1", "甲。", ("m01",)),
                                aspect_ids=["aspect-a", ""])))),
        token="非空字符串", reason="paragraph_field_malformed")
    details.append("NOTE 5b：空串不是一条栏目身份。收下它，覆盖账里会多出一栏谁都不认识的"
                   "「栏目」，而它永远匹配不上任何登记。")
    details.append("NOTE §5：段落声明**越界**（写了一条不属于本小节的栏目）这件事本身**不**在"
                   "这一层判——那道门在 `cited_writer._check_paragraph_aspects`"
                   "（原因码 `paragraph_aspect_out_of_subsection`）。归一这边只在"
                   "**候选空壳段要不要删**这一件事上问归属（§6g）：问它的目的恰恰是**别抢**"
                   "那道门的活儿——跨小节借用的那一段必须原样留下去给它判。")

    # ==================================================== §6 空壳段（`crn-4` 第三条规则）
    details.append("## §6 纯空壳段：形态与归属全过之后，**两条依据取其一**（零来源＋那条缺口 / "
                   "同小节可唯一对应的缺口支持）；两条都不成立就留给构造器照旧拒")
    #: 动因（M930-5 全过程真实 run `m930_3_cited_upload_20261005T091926Z`）：`p10` 只写了
    #: `paragraph_id` / `aspect_ids`（供应商集中度两栏）/ `sentences: []`。它那两栏在请求面
    #: **零登记来源**，顶层 `gaps` 里也各有一条系统判定为 `no_source_in_manifest` 的缺口，
    #: 且**就属于 `p10` 自己所在的小节**——三件事都成立，空壳段是同一件事的第二份记账。
    #: 但 `CitedParagraph` 对空段落 fail-closed ⇒ **整节**作废。这里钉的是**删的边界**；
    #: 其中第六条（归属，`crn-3` 补）正是为了不把「借别的小节的栏目」误当成「这里没有来源」。
    #: `crn-4` 再补第四条**依据**（§6h）：同一栏在**同一小节**内有一条**可唯一对应**的顶层缺口、
    #: 其**系统判定**理由落在 `GAP_SUPPORTED_REASONS` 时也放行——本次真实 run
    #: `m930_3_cited_upload_20261005T131532Z` 的 `p10`（三栏客户集中度**登记着** `m47/m51/m52`，
    #: 模型自述 `no_source_in_manifest` 被系统改判为 `source_present_but_not_admissible`）走的
    #: 就是这一条；`crn-3` 原口径（零来源＋`no_source_in_manifest` 缺口）**一字未改**，是并集。

    _FREE = "aspect-source-free"
    _FREE_2 = "aspect-source-free-2"
    _SOURCED = "aspect-with-source"
    _UNCLAIMED = "aspect-nobody-claimed"
    #: 别的小节声明的栏目。它在本轮**同样零登记来源、同样有一条系统判定的缺口**——
    #: 也就是说另外两项读数都成立，唯独「属不属于这一段所在的小节」不成立。用它把第六条
    #: 单独隔出来：只有这一条能拦住它。
    _FOREIGN = "aspect-of-another-subsection"
    _OWN_SUB = "sub-a"
    _OTHER_SUB = "sub-other"
    _ctx = CRN.EmptyShellContext(
        registered_source_counts={_SOURCED: 3},
        no_source_gap_aspects=frozenset({_FREE, _FREE_2, _FOREIGN}),
        subsection_aspect_ids={
            _OWN_SUB: frozenset({_FREE, _FREE_2, _SOURCED, _UNCLAIMED}),
            _OTHER_SUB: frozenset({_FOREIGN})})

    def _shell(**changes) -> dict:
        """一个**候选**空壳段：只写那三项，且 `sentences` 是空列表。逐条反例在它上面改。"""
        row = {"paragraph_id": "p-shell", "aspect_ids": [_FREE], "sentences": []}
        row.update(changes)
        return row

    def _one(paragraph: dict) -> dict:
        return _reply(_sub("sub-a", dict(paragraph)))

    def _direct(paragraph: dict, *, context=_ctx):
        """直接调**规则三**（绕过入口）：五种不满足条件各自的原样返回在这里才看得见。"""
        return CRN.drop_empty_shell_paragraphs(_one(paragraph), context=context)

    def _fate(paragraph: dict, *, context=_ctx) -> str:
        """这一段**不得**被这条规则放过：要么归一就拒，要么留给构造器拒。返回拒在哪一道。"""
        try:
            result = CRN.normalize_cited_reply(_one(paragraph), empty_shell=context)
        except CW.CitedWriterError as exc:
            return f"归一:{exc.reason}"
        if result.dropped_empty_shell_paragraphs:
            raise AssertionError("这一路**不得**被空壳段规则删除")
        try:
            [CW._subsection_from_dict(s) for s in result.payload["subsections"]]
        except Exception as exc:  # noqa: BLE001
            return f"构造:{getattr(exc, 'reason', None) or type(exc).__name__}"
        raise AssertionError(f"不合格的空壳段被静默放过了：{paragraph!r}")

    # ---------------------------------------------- §6a 正例：五项全满足 ⇒ 删，且不计覆盖
    dropped = CRN.normalize_cited_reply(_one(_shell()), empty_shell=_ctx)
    kept_paras = dropped.payload["subsections"][0]["paragraphs"]
    check(kept_paras == [] and "paragraphs" in dropped.payload["subsections"][0],
          "**正例**：六项全满足 ⇒ 空壳段从归一小节里**消失**（键还在，剩空表）")
    check([d.reason for d in dropped.dropped_empty_shell_paragraphs]
          == [CRN.EMPTY_SHELL_NO_SOURCE],
          f"⇒ 删除走**唯一**原因码 `{CRN.EMPTY_SHELL_NO_SOURCE}`（不是自由文本）")
    row = dropped.dropped_empty_shell_paragraphs[0].to_dict()
    check(row["paragraph_id"] == "p-shell" and row["aspect_ids"] == [_FREE]
          and row["subsection_id"] == "sub-a"
          and [row["subsection_index"], row["paragraph_index"]] == [1, 1],
          "⇒ 台账逐项定位到**原返回**里的位置与它声明的栏目（删了也查得到删的是哪一段）")
    check(row["counts_toward_coverage"] is False,
          "⇒ **被删的段不计入栏目覆盖**：它既不进段级声明、也不贡献任何一句，"
          "这一行就是那句结论本身")
    check(row["registered_source_counts"] == {_FREE: 0} and row["system_gap_reasons"] == {_FREE: ""},
          "⇒ **删除依据逐栏落台账**（`crn-4`）：登记数如实记 `0`（不因「有缺口」而改写），"
          "第四条依据这一栏取不到，就记空——「零登记」与「登记着来源」在台账上是两个数")
    check(_FREE not in json.dumps(dropped.payload, ensure_ascii=False),
          "⇒ 归一后的 payload 里**再也找不到**那一栏——它没有以任何形态补进覆盖账")
    check(dropped.empty_shell_rule == CRN.EMPTY_SHELL_RULE_APPLIED
          and dropped.version == CRN.CITED_REPLY_NORMALIZE_VERSION,
          f"⇒ 状态记 `applied`、版本记 `{CRN.CITED_REPLY_NORMALIZE_VERSION}`")
    both = CRN.normalize_cited_reply(_one(_shell(aspect_ids=[_FREE, _FREE_2])),
                                     empty_shell=_ctx)
    check(len(both.dropped_empty_shell_paragraphs) == 1
          and both.dropped_empty_shell_paragraphs[0].aspect_ids == (_FREE, _FREE_2),
          "⇒ **两栏都**零来源且都已被声明为无来源时，同一个空壳段照样只删一次"
          "（逐栏都成立才成立，不是「有一栏成立就成立」）")

    # ---------------------------------------------- §6b 反例：非空句不是候选（没有例外）
    live = CRN.normalize_cited_reply(
        _one(_shell(sentences=[_sentence("s1", "甲。", ("m01",))])), empty_shell=_ctx)
    check(not live.dropped_empty_shell_paragraphs
          and len(live.payload["subsections"][0]["paragraphs"][0]["sentences"]) == 1
          and live.empty_shell_rule == CRN.EMPTY_SHELL_RULE_APPLIED,
          "**反例（非空句）**：同一个段只要有一句，就**不**是空壳段——这一条没有例外，"
          "哪怕它声明的栏目确实零来源")
    details.append("NOTE §6a：`sentences` 非空 ⇒ 连候选都不是，规则三看都不看它。")

    # ---------------------------------------------- §6c 五种「不满足」逐条原样留下
    blocked_cases = (
        ("paragraph_extra_fields", _shell(note="模型自己加的字段")),
        ("paragraph_missing_fields",
         {k: v for k, v in _shell().items() if k != "aspect_ids"}),
        ("paragraph_missing_fields",
         {k: v for k, v in _shell().items() if k != "paragraph_id"}),
        ("paragraph_id_empty", _shell(paragraph_id="   ")),
        ("aspect_ids_missing_or_malformed", _shell(aspect_ids=[])),
        ("aspect_ids_missing_or_malformed", _shell(aspect_ids=_FREE)),
        ("aspect_ids_missing_or_malformed", _shell(aspect_ids=[_FREE, ""])),
        ("aspect_ids_missing_or_malformed", _shell(aspect_ids=[_FREE, _FREE])),
        ("aspect_has_registered_source", _shell(aspect_ids=[_SOURCED])),
        ("aspect_has_registered_source", _shell(aspect_ids=[_FREE, _SOURCED])),
        ("aspect_without_no_source_gap", _shell(aspect_ids=[_UNCLAIMED])),
        # 第六条（本小节归属）：借用**别的小节**声明的栏目，另外两项读数都成立也不许删。
        ("aspect_not_in_subsection", _shell(aspect_ids=[_FOREIGN])),
        ("aspect_not_in_subsection", _shell(aspect_ids=[_FREE, _FOREIGN])),
    )
    for expected, paragraph in blocked_cases:
        payload, dropped_rows, kept_rows = _direct(paragraph)
        check(not dropped_rows and [k.blocked_by for k in kept_rows] == [expected]
              and payload["subsections"][0]["paragraphs"] == [paragraph],
              f"**反例**：`{expected}` ⇒ 段**原样留在** payload 里，台账记为它"
              f"（判据：{json.dumps(paragraph, ensure_ascii=False, sort_keys=True)}）")
        check(_fate(paragraph).startswith(("归一:", "构造:")),
              f"⇒ 同一段走入口也**不**被放过：{_fate(paragraph)}"
              f"（「模型漏写了整段」与「这一栏其实有来源」都不能被这条规则悄悄抹掉）")
    details.append("NOTE §6b：六种「不满足」逐条各走一遍：形态两项（字段集、段 ID）、"
                   "声明一项（`aspect_ids` 的四种坏形）、归属一项（声明的栏属不属于本小节）、"
                   "请求面两项（有无登记来源、有无那条缺口）。")
    details.append("NOTE §6b-2：`aspect_has_registered_source` 在 `crn-4` 下读作「**有登记来源**"
                   "**且**本小节内没有可唯一对应的缺口支持」——本节的 `_ctx` **不带**第四条读数，"
                   "所以这两条反例正好pin住「无合格对应缺口 ⇒ 仍拒」这一半；"
                   "「有来源 + 每栏都有可留痕的对应缺口 ⇒ 归一」那一半在 §6h。"
                   "两条合起来才是完整的边界，**没有**删除任何一条反例。")
    details.append("NOTE §6c：**只列可达取值**——`sentences` 缺键 / `null` / 写成一个字符串"
                   "这些形态**不**在这里留台账：它们不是「空壳段」，是坏形，"
                   "由 `assign_section_unique_sentence_ids` 用 `paragraph_field_malformed` 拒"
                   "（同一段被同一个字符串形拒绝时，构造器仍会在 `empty_paragraph` 上再拒一次）。")

    # ---------------------------------------------- §6d 坏形（`sentences` 不是空数组）
    _no_sent_key = {"paragraph_id": "p-shell", "aspect_ids": [_FREE]}
    check(_direct(_no_sent_key)[1:] == ((), ()),
          "**坏形一**：`sentences` **缺键** ⇒ 不是候选（`[]` 这个形态本身不成立），零台账")
    check(_fate(_no_sent_key) == "构造:empty_paragraph",
          "⇒ 但走入口时它**仍被拒**：空段落由构造器抛 `empty_paragraph`，一步都没放过")
    check(_direct({**_no_sent_key, "sentences": None})[1:] == ((), ()),
          "**坏形二**：`sentences` 写 `null` ⇒ 同上（`null` 不是「空数组」）")
    check(_fate({**_no_sent_key, "sentences": None}) == "构造:empty_paragraph",
          "⇒ 仍被构造器拒")
    _str_sent = {**_no_sent_key, "sentences": "甲。"}
    check(_direct(_str_sent)[1:] == ((), ()), "**坏形三**：`sentences` 写成字符串 ⇒ 不是候选")
    check(_fate(_str_sent).startswith("归一:"),
          f"⇒ 归一自己用 `sentence_field_malformed` 拒：{_fate(_str_sent)}"
          "（一个本该是数组的字段被写成一个字符串，下游会逐字拆开——那种坏形必须在边界上拒）")
    details.append("NOTE §6d：**「空数组」是一个形态，不是一个真值**——"
                   "缺键、`null`、字符串都不是它。三者都不进空壳判定，各有各的拒收点。")

    # ---------------------------------------------- §6e 单向性：读数算错只会「少删」
    blind = CRN.EmptyShellContext()          # 什么都没读到：零栏有源、零栏已声明无源、零小节归属
    payload, dropped_rows, kept_rows = CRN.drop_empty_shell_paragraphs(
        _one(_shell()), context=blind)
    check(not dropped_rows
          and [k.blocked_by for k in kept_rows] == ["aspect_not_in_subsection"],
          "**方向是单向的**：三项读数**全空**时，多出来的只会是**没删掉**的段。"
          "这时先被问的是归属（它排在最前），而「查不到该小节」在第六条里等于「没证明」"
          "——保守方向与另两项一致")
    try:
        [CW._subsection_from_dict(s) for s in payload["subsections"]]
    except CW.CitedWriterError as exc:
        check(exc.reason == "empty_paragraph",
              "⇒ 该段随后仍被构造器拒（`empty_paragraph`）——"
              "这条规则**不可能**把一次本应失败的解析变成成功")
    else:
        check(False, "读数算错时空壳段被放行了：这条规则不再是单向的")
    blind_sources = CRN.EmptyShellContext(   # 只交出归属，另外两项读成「什么都没声明」
        subsection_aspect_ids={_OWN_SUB: frozenset({_FREE})})
    check(not _direct(_shell(), context=blind_sources)[1]
          and _direct(_shell(), context=blind_sources)[2][0].blocked_by
          == "aspect_without_no_source_gap",
          "⇒ 归属**能**证明、另两项读成「什么都没声明」时，退回请求面那一项（保守方向）")
    wrong_source = CRN.EmptyShellContext(
        registered_source_counts={_FREE: 1},
        no_source_gap_aspects=frozenset({_FREE}),
        subsection_aspect_ids={_OWN_SUB: frozenset({_FREE})})
    check(not _direct(_shell(), context=wrong_source)[1]
          and _direct(_shell(), context=wrong_source)[2][0].blocked_by
          == "aspect_has_registered_source",
          "⇒ 读数把它算成**有来源**时也只让段留下来（保守方向），不会误删")
    details.append("NOTE §6e：三项读数都只可能「少认出」——所以这条规则是保守的，"
                   "不是宽容的。")

    # ---------------------------------------------- §6g 第六条：跨小节借用的栏目不得被删
    _loan = _shell(aspect_ids=[_FOREIGN])
    payload, dropped_rows, kept_rows = _direct(_loan)
    check(not dropped_rows and [k.blocked_by for k in kept_rows]
          == ["aspect_not_in_subsection"] and payload["subsections"][0]["paragraphs"] == [_loan],
          "**反例（跨小节借用）**：这一段声明的 `_FOREIGN` 是**别的小节**的栏目，它本轮"
          "同样零登记来源、同样有一条系统判定的缺口——另外两项读数都成立，"
          "唯独归属不成立 ⇒ 段**原样留下**，原因码 `aspect_not_in_subsection`")
    check(_fate(_loan) == "构造:empty_paragraph",
          "⇒ 走入口也不被放过：它留给构造器抛 `empty_paragraph`，"
          "而更下游还有 `cited_writer._check_paragraph_aspects`（`paragraph_aspect_out_of_subsection`）"
          "要判「声明了本小节不负责的栏目」——这条归一规则**不得**抢在那道门前面把整段删掉、"
          "让它无对象可判")
    _own_sub_only = CRN.EmptyShellContext(   # 归属表里**没有**这一小节（例如请求面没写到它）
        registered_source_counts={},
        no_source_gap_aspects=frozenset({_FREE}),
        subsection_aspect_ids={_OTHER_SUB: frozenset({_FOREIGN})})
    check(not _direct(_shell(), context=_own_sub_only)[1]
          and _direct(_shell(), context=_own_sub_only)[2][0].blocked_by
          == "aspect_not_in_subsection",
          "**反例（查不到该小节）**：归属表里**没有**这段所在的小节 ⇒ 同样记 "
          "`aspect_not_in_subsection`——「查不到」就是没证明，没证明就不删"
          "（方向与另两项相反的正是这一条：那里「取不到即零」，这里「取不到即不许删」）")
    _foreign_gap = CRN.drop_empty_shell_paragraphs(
        _reply(_sub(_OWN_SUB, dict(_shell(aspect_ids=[_FOREIGN])))), context=_ctx)
    check(not _foreign_gap[1] and _foreign_gap[2][0].blocked_by == "aspect_not_in_subsection",
          "⇒ 归属**先于**来源被问：同一个跨小节栏目，读成「有来源」或「无来源」都不改变"
          "它被留下的理由——拦它的是归属，不是那两项")
    details.append("NOTE §6g：**借来的栏目不算**。一段借别的小节的「零来源 + 缺口」来造空壳，"
                   "说的不是「我这里没有来源」，是「我这里声明了本小节不负责的栏目」——"
                   "后者有专门的门（`paragraph_aspect_out_of_subsection`），本条归一不得抢跑。")

    # ---------------------------------------------- §6h 第四条依据：缺口支持（`crn-4`）
    details.append("## §6h 缺口支持：**有登记来源**的栏也能有一份可留痕的第二记账——"
                   "但它只在「同一小节内可唯一对应、且系统判成没有可写来源」时成立")
    _SUPPORTED = "aspect-sourced-with-supported-gap"
    _TWO_GAPS = "aspect-with-two-gaps"
    _BAD_REASON = "aspect-gap-reason-not-supported"
    _GAP_ELSEWHERE = "aspect-gap-lives-in-another-subsection"
    #: 四条栏**都登记着来源**（另加一条零来源的 `_UNCLAIMED` 用来演「只覆盖一部分」）。
    #: 第四项读数只交出一条：`_SUPPORTED`。其余三条分别因为「不唯一」（`_TWO_GAPS` 没进表）、
    #: 「理由不在闭集」（`_BAD_REASON`）、「缺口登记在**别的小节**」（`_GAP_ELSEWHERE`）拿不到。
    _ctx_gap = CRN.EmptyShellContext(
        registered_source_counts={_SUPPORTED: 3, _TWO_GAPS: 2, _BAD_REASON: 1,
                                  _GAP_ELSEWHERE: 4},
        no_source_gap_aspects=frozenset(),
        subsection_aspect_ids={_OWN_SUB: frozenset({_SUPPORTED, _TWO_GAPS, _BAD_REASON,
                                                    _GAP_ELSEWHERE, _UNCLAIMED})},
        subsection_gap_reason={
            _OWN_SUB: {_SUPPORTED: "source_present_but_not_admissible",
                       _BAD_REASON: "manifest_partial_for_requirement"},
            #: `_GAP_ELSEWHERE` 的缺口**只**登记在别的小节里：本小节查不到 ⇒ 不构成依据。
            _OTHER_SUB: {_GAP_ELSEWHERE: "source_present_but_not_admissible"}})

    # ---- 「可唯一对应」只有一处口径：`unique_subsection_gap_reasons` ----
    _uni = CW.unique_subsection_gap_reasons([
        ("s", "a", "no_source_in_manifest"),
        ("s", "b", "no_source_in_manifest"),          # 两条、理由不同 ⇒ 不唯一
        ("s", "b", "manifest_partial_for_requirement"),
        ("s", "c", "no_source_in_manifest"),          # 两条、理由相同 ⇒ **仍**不唯一
        ("s", "c", "no_source_in_manifest"),
        ("s", "", "no_source_in_manifest"),           # 落不到栏上 ⇒ 不进表
        ("", "d", "no_source_in_manifest"),           # 没有小节 ⇒ 不进表
        ("s", "e", "no_source_in_manifest"),          # 同栏在不同小节里各自唯一 ⇒ 各自保留
        ("t", "e", "source_present_but_not_admissible")])
    check(_uni == {"s": {"a": "no_source_in_manifest", "e": "no_source_in_manifest"},
                   "t": {"e": "source_present_but_not_admissible"}},
          "⇒ 「可唯一对应」是**一处**口径：同一栏两条缺口（哪怕理由一模一样）一律不进表，"
          f"落不到栏上/没有小节的一律不进表；同栏在**不同小节**里各自唯一 ⇒ 各自保留。得到 {_uni!r}")
    check(CRN.GAP_SUPPORTED_REASONS == ("no_source_in_manifest",
                                        "source_present_but_not_admissible"),
          "⇒ 依据只认**闭集**里这两条——它们正是 `assign_gap_reason` 那条业务事实的两面；"
          "`manifest_partial_for_requirement`（「只覆盖了一部分」）说的是另一件事，不在列")

    # ---- 正例：有登记来源 + 本小节唯一对应 + 系统判定理由在闭集 ⇒ 删，依据逐栏落台账 ----
    _supported = CRN.normalize_cited_reply(_one(_shell(aspect_ids=[_SUPPORTED])),
                                          empty_shell=_ctx_gap)
    check(not _supported.kept_empty_paragraphs
          and [d.reason for d in _supported.dropped_empty_shell_paragraphs]
          == [CRN.EMPTY_SHELL_GAP_SUPPORTED]
          and _supported.payload["subsections"][0]["paragraphs"] == [],
          f"**正例（缺口支持）**：这一栏**登记着 3 条来源**、本小节内有一条可唯一对应的缺口、"
          f"系统判定为 `source_present_but_not_admissible` ⇒ 段被删，原因码是"
          f"`{CRN.EMPTY_SHELL_GAP_SUPPORTED}`（与零来源那条**分开记**）")
    _srow = _supported.dropped_empty_shell_paragraphs[0].to_dict()
    check(_srow["registered_source_counts"] == {_SUPPORTED: 3}
          and _srow["system_gap_reasons"] == {_SUPPORTED: "source_present_but_not_admissible"}
          and _srow["counts_toward_coverage"] is False,
          "⇒ 台账把**登记数 3** 与**系统理由**都记下来——「登记了材料」**不**被写成"
          "「足以证明集中度」，登记数**不**被伪装成 0，被删的段仍**不计入覆盖**")

    # ---- 反例：`crn-3` 原有的两条码**逐条仍在**，且第四条依据缺一不可 ----
    _gap_blocked = (
        ("aspect_has_registered_source", [_TWO_GAPS], "同一栏**两条**缺口 ⇒ 不唯一"),
        ("aspect_has_registered_source", [_BAD_REASON], "唯一，但理由不在闭集"),
        ("aspect_has_registered_source", [_GAP_ELSEWHERE], "唯一的缺口**登记在别的小节**"),
        ("aspect_has_registered_source", [_SUPPORTED, _TWO_GAPS], "**只覆盖一部分栏目**"),
        ("aspect_without_no_source_gap", [_SUPPORTED, _UNCLAIMED],
         "另一栏零来源又**没有**缺口 ⇒ 逐栏都成立才成立"),
    )
    for _expected, _aspects, _why in _gap_blocked:
        _dir = CRN.drop_empty_shell_paragraphs(_one(_shell(aspect_ids=_aspects)),
                                               context=_ctx_gap)
        check(not _dir[1] and [_k.blocked_by for _k in _dir[2]] == [_expected],
              f"**反例**：{_why} ⇒ 段**原样留下**，码 `{_expected}`（依据缺一不可）")

    # ---- 「零来源＋那条缺口」与「缺口支持」是**并集**：混在一段里照样只删一次 ----
    _ctx_both = CRN.EmptyShellContext(
        registered_source_counts={**_ctx.registered_source_counts, _SUPPORTED: 3},
        no_source_gap_aspects=_ctx.no_source_gap_aspects,
        subsection_aspect_ids={_OWN_SUB: frozenset({_FREE, _FREE_2, _SOURCED, _UNCLAIMED,
                                                    _SUPPORTED})},
        subsection_gap_reason={_OWN_SUB: {_SUPPORTED: "source_present_but_not_admissible"}})
    _mixed = CRN.normalize_cited_reply(_one(_shell(aspect_ids=[_FREE, _SUPPORTED])),
                                       empty_shell=_ctx_both)
    check(len(_mixed.dropped_empty_shell_paragraphs) == 1
          and _mixed.dropped_empty_shell_paragraphs[0].reason == CRN.EMPTY_SHELL_GAP_SUPPORTED
          and _mixed.dropped_empty_shell_paragraphs[0].aspect_ids == (_FREE, _SUPPORTED),
          "⇒ 两条依据是**并集**、一段只删一次：一栏走零来源那条、另一栏走缺口支持那条，"
          "只要**每一栏**都有依据就删；整段里**至少一栏登记着来源**时，原因码记"
          f"`{CRN.EMPTY_SHELL_GAP_SUPPORTED}`——读回侧据此知道「这一次用到了更宽的那条」")
    details.append("NOTE §6h：这条依据**换不到任何内容**——被删的段零句零引用，缺口、补件需求与"
                   "来源身份一字不动地留在顶层；删掉的不进栏目覆盖、不计事实资格、不计审阅通过、"
                   "不计系统放行。它只把「模型把整段留空」从**整节作废**改判成**那条缺口的第二份"
                   "记账**，且依据逐栏可复核。")

    # ---------------------------------------------- §6f 状态、幂等、不动输入、编号连续
    plain_state = CRN.normalize_cited_reply(
        _reply(_sub("sub-a", _para("p1", _sentence("s1", "甲。")))))
    check(plain_state.empty_shell_rule == CRN.EMPTY_SHELL_RULE_NOT_EVALUATED
          and plain_state.dropped_empty_shell_paragraphs == ()
          and plain_state.kept_empty_paragraphs == (),
          "**缺省（读不到请求面）**：`empty_shell_rule` 记 "
          f"`{CRN.EMPTY_SHELL_RULE_NOT_EVALUATED}`——第三条规则**不评估**，"
          "行为与 `crn-1` 逐字相同（归一不猜「这一栏有没有来源」）")
    _applied = CRN.normalize_cited_reply(
        _reply(_sub("sub-a", _para("p1", _sentence("s1", "甲。")))), empty_shell=_ctx)
    check(_applied.empty_shell_rule == CRN.EMPTY_SHELL_RULE_APPLIED
          and not _applied.dropped_empty_shell_paragraphs
          and not _applied.kept_empty_paragraphs,
          "**带了读数但一个空段都没有**：状态仍记 `applied`、两份台账都为空——"
          "「跑了、没删任何东西」与「根本没评估」在产物上分得开")

    mixed = _reply(_sub("sub-a", _para("p1", _sentence("s1", "甲。", ("m01",))),
                        _shell()))
    snapshot = json.dumps(mixed, ensure_ascii=False, sort_keys=True)
    first = CRN.normalize_cited_reply(mixed, empty_shell=_ctx)
    check(json.dumps(mixed, ensure_ascii=False, sort_keys=True) == snapshot,
          "归一**不改动输入对象**（读回侧还要拿原文与派生稿对差额）")
    check(first.dropped_empty_shell_paragraphs[0].paragraph_index == 2,
          "⇒ 台账记的是**原返回**里的段序号（第 2 段），不是归一后的位置")
    check([a.final_id for a in first.sentence_ids] == ["s0001"],
          "⇒ 空壳段在**编号之前**就被移走：剩下的句子仍拿到连续编号（空段不占号）")
    second = CRN.normalize_cited_reply(json.loads(json.dumps(first.payload)),
                                       empty_shell=_ctx)
    check(second.payload == first.payload and not second.dropped_empty_shell_paragraphs,
          "**幂等**：对归一结果再归一得到同一份 payload，且没有可再删的空壳段")
    body = first.to_dict()
    check(body["normalize_version"] == CRN.CITED_REPLY_NORMALIZE_VERSION
          and body["empty_shell_rule"] == CRN.EMPTY_SHELL_RULE_APPLIED
          and len(body["dropped_empty_shell_paragraphs"]) == 1
          and body["kept_empty_paragraphs"] == []
          and set(body["dropped_empty_shell_paragraphs"][0])
          == {"reason", "subsection_id", "subsection_index", "paragraph_index",
              "paragraph_id", "aspect_ids", "registered_source_counts",
              "system_gap_reasons", "counts_toward_coverage"},
          "⇒ 台账形状是**等号**（键集固定），逐项可落盘、可回查——"
          "`crn-4` 新增的两键（逐栏登记数、逐栏系统理由）是删除依据本身，随版本一起进来；"
          "更早的产物没有这两键，读回侧按各自版本理解，不假设在场")
    observed_kept = {k.blocked_by for _, p in blocked_cases for k in _direct(p)[2]}
    check(observed_kept and observed_kept <= set(CRN.EMPTY_SHELL_KEPT_REASONS),
          f"⇒ 「留下」的全部取值都落在闭集 `EMPTY_SHELL_KEPT_REASONS` 里"
          f"（观察到的：{sorted(observed_kept)}）——不留自由文本，读回侧可以按码分流")
    check(CRN.EMPTY_SHELL_NO_SOURCE not in CRN.EMPTY_SHELL_KEPT_REASONS
          and len(set(CRN.EMPTY_SHELL_KEPT_REASONS)) == len(CRN.EMPTY_SHELL_KEPT_REASONS),
          "⇒ 两个原因码空间**不相交**、闭集无重复：删（一条）与留（七项）"
          "不会被同一个字符串表达")
    check(observed_kept == set(CRN.EMPTY_SHELL_KEPT_REASONS),
          f"⇒ 闭集里**每一条**都在本模块里被真的走通过（不留没人产生过的码）："
          f"{sorted(CRN.EMPTY_SHELL_KEPT_REASONS)}")

    details.append("NOTE §6：`crn-4` 的第三条规则**只删同一件缺口的第二份记账**。"
                   "它读的四个数（这一段声明的栏属不属于本小节、这一栏登记到几条来源、"
                   "有没有系统判定的 `no_source_in_manifest` 缺口、本小节内有没有可唯一对应的"
                   "缺口支持）全部来自清单侧，模型自己写的 `reason` 一个字都不采信；"
                   "删掉的段整段消失、**不计入栏目覆盖**——那正是「这一栏本次写不出来」的诚实记账，"
                   "不是「这一栏写好了」。")

    details.append(
        "NOTE 本模块只证明**归一的结构边界**；正文写得对不对由逐句核对、独立审阅与人工验收"
        "判定，本模块不产生任何正式产物，也不调用模型。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

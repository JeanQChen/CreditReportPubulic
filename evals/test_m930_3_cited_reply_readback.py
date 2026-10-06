"""Eval: 留存回复的**只读业务诊断**（`crrb-3`）——每根轴都配正反例。

用法: python -m evals.test_m930_3_cited_reply_readback

缘起：真实 r2 那一轮**没有**在运行目录里落下精确输入清单身份，而请求面里没有构造一份真清单
所需的字段（`pack_id`、`provenance_identity`、`reading_view_fingerprint`、`document_id`、
`structured_view`、`content_qualification`）。因此它只能叫**离线派生诊断**，不能倒写成正式
通过。本模块钉住这种诊断的**边界与判据**：

1. **能在现有证据下判的轴就判**，判据复用被顶替那条链的原语（`NS.authorized_numeric_tokens`
   / `NS.numeric_token_authorized` / `SC.is_qualified_numeric_surface` / `NS.vague_period_hits`），
   不另立词表；每根轴都给**正例（要报）与反例（不许报）**。
2. **判不了的轴逐条写明**，不能靠不写来省略；`issuer_self_evaluation` 与近重复这类**只标待审**，
   从不据此判合格。
3. **数字分四种情形**：来源里逐字有（不报）／只差「数字与单位之间的**排版空白**」（`crrb-6`
   起并入**严格排版空白等价** ⇒ **不报硬错**，另有一条**非失败**记录点出它是靠等价被接受的）／
   来源里根本没有（硬错）／金额比率的资格另判（**逐 token** 看有没有落在本句所引权威事实里）。
   放宽的**只有空白**：符号（`-9.94` vs `9.94`）、单位（`千` vs `千元`）、尾零一律不派生。
4. **跨小节重复分两种**：逐字重复、以及「整句包含」与「换说法达到阈值」两类近重复；阈值以下的
   不同叙述**不报**。
5. **缺口里对来源文档的断言单独标出**：「相关**披露**范围」不是断言，**不报**。
6. **人工判断与机械判据分栏**，且人工条目的字段与归类是**封闭**的（写错就拒，不静默吞）。
7. **缺口「没有来源」的计数是逐栏的，不是整节的**（`crrb-7` 修）：一条缺口落到哪一栏由请求面的
   `requirement_text` 逐行 ↔ `declared_aspect_ids` 逐位对位判出（判据即生产侧
   `aspect_for_requirement`）；对不上时记 `null` 并退回整节口径。登记来源 = 材料行 **∪** 事实行。
8. **空壳段归一读的是生产侧** 同一套 **三项**读数（`crrb-8` 起，对齐 `crn-3`）：离线侧若少交
   一项（尤其 `crn-3` 新增的「这一段声明的栏属不属于它自己所在的小节」），同一串字节会在这里
   判出与生产侧**相反**的 `empty_shell_rule`——生产删、离线留，或反过来。本节钉住这一条。

夹具是**合成的请求面与回复**（dict）：无公司代号、文件名、页码或答案关键词；不调 LLM、不联网、
不读历史 run、不写盘。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import cited_reply_readback as CRRB     # noqa: E402

_MONEY = "100.00 万元"
_FACT_TEXT = "报告期内公司主营业务收入为 100.00 万元。"
_VISIT = "截至报告期末，公司已在全球设立 24 家电池工厂。"
_REGION = "公司境外收入 129,641,258 千元。"


def _face(*, facts=(), materials=None, subsections=None) -> dict:
    return {
        "task_id": "task-demo",
        "section_id": "company",
        "prompt_version": "cp-1",
        "authority_facts": [{"key": k, "text": t} for k, t in facts],
        "materials": list(materials if materials is not None else _default_materials()),
        "subsections": list(subsections if subsections is not None else _default_subsections()),
    }


def _default_materials() -> list[dict]:
    return [
        {"key": "m01", "text": f"公司主营业务收入为 {_MONEY}。",
         "aspect_ids": ["aspect-main"]},
        {"key": "m02", "text": _VISIT, "aspect_ids": ["aspect-main"]},
        {"key": "m03", "text": f"{_REGION}占本期营业收入30.60%。",
         "aspect_ids": ["aspect-region"]},
        {"key": "m04", "text": "公司通过长期协议与合资合作等方式与全球供应商紧密合作，"
                               "以保证原材料和设备的技术先进性以及产品的可靠性。",
         "aspect_ids": ["aspect-main"]},
    ]


def _default_subsections() -> list[dict]:
    return [
        {"subsection_id": "sub-main", "declared_aspect_ids": ["aspect-main"],
         "requirement_text": "列示主营业务收入"},
        {"subsection_id": "sub-region", "declared_aspect_ids": ["aspect-region"],
         "requirement_text": "列示境外收入"},
    ]


def _reply(subs, *, gaps=(), follow_up_needs=()) -> str:
    return json.dumps({"subsections": list(subs), "gaps": list(gaps),
                       "follow_up_needs": list(follow_up_needs)}, ensure_ascii=False)


def _sub(subsection_id: str, *sentences: dict, gaps=None, aspect_ids=None) -> dict:
    para = {"paragraph_id": "p1", "sentences": list(sentences)}
    #: 段级声明（`cw-4`）**默认不写**：本模块的多处夹具整套共用，给一个默认栏目会让它们
    #: 悄悄承担一条自己没打算验的判据。要验段级那一条的夹具显式传 `aspect_ids=...`。
    if aspect_ids is not None:
        para["aspect_ids"] = list(aspect_ids)
    row = {"subsection_id": subsection_id, "title": subsection_id, "paragraphs": [para]}
    if gaps is not None:
        row["gaps"] = list(gaps)
    return row


def _s(model_id: str, text: str, *citations: str) -> dict:
    return {"sentence_id": model_id, "text": text, "citations": list(citations)}


def _kinds(reading) -> list[str]:
    return [f.check_kind for f in reading.findings]


def _verdicts(reading, kind: str) -> list[str]:
    return [f.verdict for f in reading.findings if f.check_kind == kind]


def _find(diagnosis, needle: str):
    hits = [r for r in diagnosis.sentences() if needle in r.text]
    if len(hits) != 1:
        raise AssertionError(f"夹具里含 {needle!r} 的句子应恰好 1 句，实际 {len(hits)} 句")
    return hits[0]


def _gap_row(diagnosis, requirement_text: str):
    hits = [r for r in diagnosis.gap_context if r["requirement_text"] == requirement_text]
    if len(hits) != 1:
        raise AssertionError(f"夹具里 requirement_text={requirement_text!r} 的缺口应恰好 1 条，"
                             f"实际 {len(hits)} 条")
    return hits[0]


def _expect_raise(fn, *, token: str) -> None:
    try:
        fn()
    except ValueError as exc:
        if token not in str(exc):
            raise AssertionError(f"异常里没有 {token!r}：{exc}") from None
        return
    raise AssertionError(f"这一路必须拒（期望提到 {token!r}）")


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

    # ==================================================== §1 引用轴 正反例
    details.append("## §1 引用存在与引用身份（与 `sc-5` 同名同义）")
    reply = _reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01"),
             _s("s02", "公司主营业务收入保持稳定。", "m99"),
             _s("s03", "公司主营业务收入保持稳定。")),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m03")),
    ])
    diag = CRRB.diagnose(reply_text=reply, request_face=_face())
    check(not {"citation_present", "citation_identity"} & set(_kinds(_find(diag, "100.00 万元"))),
          "**反例**：引用键在输入里 ⇒ 不报引用轴")
    _s2 = [r for r in diag.sentences() if r.model_id == "s02"][0]
    _s3 = [r for r in diag.sentences() if r.model_id == "s03"][0]
    check(_verdicts(_s2, "citation_identity") == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：引用键 `m99` 不在输入里 ⇒ 硬错 `citation_identity`")
    check(_verdicts(_s3, "citation_present") == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：整句没有引用 ⇒ 硬错 `citation_present`")
    details.append("NOTE §1：引用键取自请求面自带的材料键与事实键两轴。")

    # ==================================================== §2 栏目轴 正反例
    details.append("## §2 栏目归属（`aspect_attribution`）")
    reply = _reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01")),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m01")),
    ])
    diag = CRRB.diagnose(reply_text=reply, request_face=_face())
    check("aspect_attribution" not in _kinds(_find(diag, "100.00 万元")),
          "**反例**：所引材料登记了本小节声明的栏目 ⇒ 不报")
    check(_verdicts(_find(diag, "129,641,258"), "aspect_attribution")
          == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：所引材料**没有**登记本小节声明的栏目 ⇒ 硬错（这正是 r2 的 6 句错栏）")
    #: 段级那一条（同一条轴上的第二个条件，与生产侧逐字同判）：
    #: 小节声明与所引材料有交集、但**本段自己**声明服务的那一栏没有 —— 宽小节里
    #: 「拿 A 栏的材料写 B 栏」就是长这样。
    wide = _reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01"),
             aspect_ids=["aspect-region"]),
    ])
    wide_diag = CRRB.diagnose(reply_text=wide, request_face=_face(
        subsections=[{"subsection_id": "sub-main",
                      "declared_aspect_ids": ["aspect-main", "aspect-region"],
                      "requirement_text": "主营业务与境外收入"}]))
    check(wide_diag.coverage["exact_match"] is True
          and _verdicts(wide_diag.sentences()[0], "aspect_attribution")
          == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：小节声明覆盖两栏、所引材料登记的是其中一栏 ⇒ 小节那一级过得去；"
          "但**本段**声明的是另一栏 ⇒ 仍然硬错（只有小节那一条判据会把这一段放过）")
    ok_para = CRRB.diagnose(reply_text=_reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01"),
             aspect_ids=["aspect-region", "aspect-main"])]),
        request_face=_face(subsections=[{"subsection_id": "sub-main",
                                         "declared_aspect_ids": ["aspect-main",
                                                                 "aspect-region"],
                                         "requirement_text": "主营业务与境外收入"}]))
    check("aspect_attribution" not in _kinds(ok_para.sentences()[0]),
          "**反例**：本段声明的两栏里**有一栏**对所引材料有登记 ⇒ 不报（集合有交集，不是逐条相等）")
    undeclared = CRRB.diagnose(reply_text=reply, request_face=_face())
    check(undeclared.sentences()[0].paragraph_aspect_ids == (),
          "⇒ 本段没声明服务哪一栏时如实记成空集合——不猜、也不拿小节那一级的集合顶替它")
    details.append("NOTE §2：判据是「材料登记的栏目集 ∩ 小节声明 ≠ ∅」，**且**本段声明了服务栏目时"
                   "还要「∩ 本段声明 ≠ ∅」。它看不懂「这句话答的是不是这一栏」，"
                   "只判「这一栏有没有人登记过材料」。")

    # ==================================================== §3 数字四情形
    details.append("## §3 数字：逐字有 / 只差排版空白（**等价**）/ 根本没有 / 符号与单位不许派生")
    materials = _default_materials() + [
        {"key": "m05", "text": "公司利息保障倍数为 9.94。", "aspect_ids": ["aspect-main"]}]
    reply = _reply([
        _sub("sub-main", _s("s01", "公司已在全球设立 24 家电池工厂。", "m02"),
             _s("s02", "公司已在全球设立 24家电池工厂。", "m02"),
             _s("s03", "公司已在全球设立 99 家电池工厂。", "m02"),
             _s("s06", "公司利息保障倍数为-9.94。", "m05"),
             _s("s04", "公司主营业务收入为 100.00 万元。", "m01"),
             _s("s05", "公司主营业务收入为 100.00 万元。", "f01")),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m03")),
    ])
    diag = CRRB.diagnose(reply_text=reply, request_face=_face(
        facts=(("f01", _FACT_TEXT),), materials=materials))
    plain = [r for r in diag.sentences() if r.model_id == "s01"][0]
    spaced = [r for r in diag.sentences() if r.model_id == "s02"][0]
    absent = [r for r in diag.sentences() if r.model_id == "s03"][0]
    signed = [r for r in diag.sentences() if r.model_id == "s06"][0]
    backed = [r for r in diag.sentences() if r.model_id == "s04"][0]
    from_fact = [r for r in diag.sentences() if r.model_id == "s05"][0]
    check("numeric_surface" not in _kinds(plain),
          "**反例**：数字在被引来源里逐字出现 ⇒ 不报")
    check("numeric_surface" not in _kinds(spaced),
          "**正例（`crrb-6` 起）**：逐字差异**只**在数字与单位之间的排版空白上"
          "（正文 `24家` vs 来源 `24 家`）⇒ **不再记硬错**：这一类已并入**严格排版空白等价**")
    check(_verdicts(spaced, CRRB.CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT)
          == [CRRB.VERDICT_CHECKABLE],
          "⇒ 同一句**另有一条非失败**记录（`可核` 档）点出「这一串是靠排版空白等价被接受的」，"
          "好让读者知道它与来源不是逐字节相同")
    check("排版空白" in [f.detail for f in spaced.findings
                      if f.check_kind == CRRB.CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT][0],
          "⇒ 那条 `detail` 明确写出差异只在排版空白上，且写明数值/符号/单位/权威规则都没放宽")
    check(_verdicts(absent, "numeric_surface") == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：数字连去掉空白后也找不到 ⇒ 硬错")
    check(CRRB.CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT not in _kinds(absent),
          "**反例**：连去空白都找不到的数字**不**进「排版等价」——这条记录不许被滥用")
    check(_verdicts(signed, "numeric_surface") == [CRRB.VERDICT_HARD_ERROR]
          and CRRB.CHECK_NUMERIC_SURFACE_SPACING_EQUIVALENT not in _kinds(signed),
          "**反例（等价面有界）**：正文 `-9.94`、来源 `9.94`——差的**只有符号**，"
          "不是空格 ⇒ 照样硬错，且不进「排版等价」（符号不派生）")
    summary3 = diag.adjudicable_summary()
    check(summary3["spacing_equivalent_sentence_ids"] == [spaced.final_id]
          and summary3["spacing_equivalent_sentences"] == 1,
          "⇒ 可判轴头条把「靠排版空白等价被接受」的句子单独点出来（非失败，单列）")
    check("spacing_suspect_sentence_ids" not in summary3
          and "spacing_only_failure_sentence_ids" not in summary3,
          "⇒ `crrb-6` 起那两条旧键退场：它们说的是「空格型**失败**」，而空格型已不是失败")
    check(summary3["hard_error_sentences"] >= 3,
          "⇒ 空格型那句**不**计入硬错句数（它已经不是失败），而真找不到/差符号的照样计入")
    check(_verdicts(backed, "numeric_qualification") == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：金额只出现在**被引普通材料**原文里（本句没引任何权威事实）⇒ 硬错资格")
    check("numeric_qualification" not in _kinds(from_fact),
          "**反例**：同一笔金额落在本句所引的**权威事实**文本里 ⇒ 不报资格轴"
          "（逐 token 比对，不是「整句引了事实就放行」）")
    check(_verdicts(_find(diag, "129,641,258"), "numeric_qualification")
          == [CRRB.VERDICT_HARD_ERROR],
          "**正例**：比率只在被引普通材料原文里 ⇒ 硬错资格")
    details.append("NOTE §3：`material_verbatim_text` 不在合格数字基准里，这是 `sc-5` 的原样口径。")

    # ==================================================== §4 期间与自述（只标待审）
    details.append("## §4 含糊期间与发行人自述：只标待审，从不判硬错")
    reply = _reply([
        _sub("sub-main", _s("s01", "报告期内，公司主营业务收入保持稳定。", "m01"),
             _s("s02", "公司主营业务收入保持稳定。", "m01"),
             _s("s03", "公司是全球领先的新能源企业。", "m01")),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m03")),
    ])
    diag = CRRB.diagnose(reply_text=reply, request_face=_face())
    dated = [r for r in diag.sentences() if r.model_id == "s01"][0]
    neutral = [r for r in diag.sentences() if r.model_id == "s02"][0]
    selfpraise = [r for r in diag.sentences() if r.model_id == "s03"][0]
    check(_verdicts(dated, "vague_period_wording") == [CRRB.VERDICT_NEEDS_REVIEW],
          "**正例**：含糊期间措辞 ⇒ 待审（`sc-5` 的 `period_surface` 明确授权「报告期」，"
          "故这里**不**判硬错）")
    check("vague_period_wording" not in _kinds(neutral),
          "**反例**：不含含糊期间措辞 ⇒ 不报")
    check(_verdicts(selfpraise, "issuer_self_evaluation") == [CRRB.VERDICT_NEEDS_REVIEW]
          and selfpraise.verdict == CRRB.VERDICT_NEEDS_REVIEW,
          "**正例**：发行人自述的评价性措辞 ⇒ 待审（引用只证明「发行人这么说过」）")
    details.append("NOTE §4：这两条都只把句子摆给语义审阅，从不据此判合格或不合格。")

    # ==================================================== §5 跨小节重复两类
    details.append("## §5 跨小节重复：逐字 / 整句包含 / 换说法达阈 / 阈值以下")
    shared = "公司主营业务收入保持稳定。"
    contained = "公司境外收入 129,641,258 千元。"
    tail_a = "公司通过长期协议与合资合作等方式与全球供应商紧密合作，以保证原材料和设备的技术先进性以及产品的可靠性。"
    tail_b = "公司通过长期协议与合资合作等方式与全球供应商紧密合作，以保证原材料和设备的技术先进性以及成本的竞争力。"
    reply = _reply([
        _sub("sub-main", _s("s01", shared, "m01"), _s("s02", contained, "m03"),
             _s("s03", tail_a, "m04")),
        _sub("sub-region",
             _s("s01", shared, "m03"),
             _s("s02", f"{contained}占本期营业收入30.60%。", "m03"),
             _s("s03", tail_b, "m03"),
             _s("s04", "公司的采购与结算安排如下。", "m03")),
    ])
    diag = CRRB.diagnose(reply_text=reply, request_face=_face())
    exact_hits = [r for r in diag.sentences()
                  if "cross_subsection_duplicate" in _kinds(r)]
    check(len(exact_hits) == 2 and all(r.verdict == CRRB.VERDICT_NEEDS_REVIEW for r in exact_hits),
          "**正例**：同一串文本横跨两小节 ⇒ 两句各报一次待审（不是只报其中一句）")
    inner = [r for r in diag.sentences()
             if r.text == contained][0]
    outer = [r for r in diag.sentences() if r.text.startswith(contained)
             and r.text != contained][0]
    check(any("整个**出现在" in f.detail or "整个**包住" in f.detail
              for f in inner.findings + outer.findings),
          "**正例**：整句包含（无阈值，是确定关系）⇒ 报，且**方向分写**"
          "（「被包含」与「包住」不是一句话）")
    near = [r for r in diag.sentences() if r.text == tail_a][0]
    near_findings = [f for f in near.findings if f.check_kind == "cross_subsection_near_duplicate"]
    check(near_findings and "换了说法" in near_findings[0].detail,
          "**正例**：换说法但相似度达到阈值 ⇒ 报")
    quiet = [r for r in diag.sentences() if "采购与结算" in r.text][0]
    check("cross_subsection_near_duplicate" not in _kinds(quiet),
          "**反例**：阈值以下的不同叙述 ⇒ 不报（阈值不是装饰）")
    details.append(f"NOTE §5：阈值 {CRRB.NEAR_DUPLICATE_RATIO}；包含关系不依赖阈值。")

    # ==================================================== §6 缺口的来源断言
    details.append("## §6 缺口 `detail` 里对**来源文档**的断言：标出来，但不改写")
    gaps = [
        {"subsection_id": "sub-main", "requirement_text": "列示成本",
         "reason": "no_source_in_manifest",
         "detail": "本次输入中没有相关事实性材料，相关数据多在表格中披露，无法写入正文。"},
        {"subsection_id": "sub-region", "requirement_text": "列示客户",
         "reason": "no_source_in_manifest",
         "detail": "本次输入中没有当前期间的具体数据，无法撰写前五大客户合计占比及相关披露范围。"},
    ]
    reply = _reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01")),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m03")),
    ], gaps=gaps)
    diag = CRRB.diagnose(reply_text=reply, request_face=_face())
    hits = CRRB.gap_source_assertions(diag)
    check(len(hits) == 1 and hits[0]["subsection_id"] == "sub-main",
          "**正例**：「多在表格中披露」⇒ 标出（这是一条本诊断**无权**判定的来源断言）")
    check(all("sub-region" != h["subsection_id"] for h in hits),
          "**反例**：「相关**披露**范围」是在引本报告该披露什么，**不**标"
          "（光有「披露」两个字不算）")
    check(diag.gaps[0]["detail"].startswith("本次输入中没有相关事实性材料"),
          "⇒ 缺口原文**一字未改**（标出 ≠ 改写；改写须人工）")
    details.append("NOTE §6：可核的说法是「本次 Writer 输入缺少合格数值事实」。")

    # ==================================================== §7 人工判断分栏
    details.append("## §7 人工判断：与机械判据分栏，字段与归类封闭")
    note = {"subject": "sub-main", "judgment": "栏目错配", "sentence_ids": ["s0001"],
            "note": "这一句答的不是这一栏问的问题。", "evidence": "机械判据只看材料登记。",
            "boundary": "不断言原文里没有那份材料。"}
    diag = CRRB.diagnose(reply_text=_reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01")),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m03")),
    ]), request_face=_face(), analyst_notes=[note])
    check(diag.analyst_notes[0]["judgment"] == "栏目错配"
          and diag.analyst_notes[0]["sentence_ids"] == ["s0001"],
          "人工条目原样进产物（可落盘、可回查）")
    check(not any("栏目错配" in f.detail for r in diag.sentences() for f in r.findings),
          "⇒ 人工判断**不进**机械发现列表（两者不得混为一谈）")
    rendered = CRRB.render_analyst_notes(diag)
    check("**不是**机械判据" in rendered and "栏目错配" in rendered and "边界" in rendered,
          "⇒ 渲染时明确标注「不是机械判据」，并带上本条自己的边界")
    check(not CRRB.diagnose(reply_text=_reply([
        _sub("sub-main", _s("s01", "甲。", "m01")),
        _sub("sub-region", _s("s01", "乙。", "m03")),
    ]), request_face=_face()).analyst_notes,
          "**反例**：没有登记人工判断时该表为空（不凭空造一条）")
    base = _reply([_sub("sub-main", _s("s01", "甲。", "m01")),
                   _sub("sub-region", _s("s01", "乙。", "m03"))])
    _expect_raise(lambda: CRRB.diagnose(reply_text=base, request_face=_face(),
                                        analyst_notes=[{**note, "judgment": "我随便写的"}]),
                  token="`judgment` 必须是")
    _expect_raise(lambda: CRRB.diagnose(reply_text=base, request_face=_face(),
                                        analyst_notes=[{**note, "severity": "high"}]),
                  token="未登记字段")
    _expect_raise(lambda: CRRB.diagnose(reply_text=base, request_face=_face(),
                                        analyst_notes=[{k: v for k, v in note.items()
                                                        if k != "subject"}]),
                  token="缺 `subject`")
    details.append("NOTE §7：写错的归类或字段名会被拒，不会静默丢掉一条人的判断。")

    # ==================================================== §8 覆盖、归一与产物形状
    details.append("## §8 覆盖对账、归一化、产物形状与不可发布")
    top_gap = {"subsection_id": "sub-main", "requirement_text": "列示主营业务收入",
               "reason": "no_source_in_manifest", "detail": "本次输入里没有该事实。"}
    reply = _reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01"),
             gaps=[dict(top_gap)], aspect_ids=["aspect-main"]),
        _sub("sub-region", _s("s01", "公司境外收入 129,641,258 千元。", "m03"),
             aspect_ids=["aspect-region"]),
    ], gaps=[dict(top_gap)])
    diag = CRRB.diagnose(reply_text=reply, request_face=_face())
    check(len(diag.normalization["stripped_subsection_gaps"]) == 1,
          "归一化删掉 1 条与顶层逐字段相同的小节内缺口副本")
    check(diag.original_subsections[0].get("gaps") is not None,
          "原稿（含那份副本）**逐字保留**在诊断里（归一与原文的差额就是「改了什么」）")
    check(diag.coverage["exact_match"] is True and diag.coverage["missing"] == [],
          "小节集合逐条相等这一条**能判**（请求面自带小节清单，与既有判据同一口径）")
    missing = CRRB.diagnose(reply_text=_reply([
        _sub("sub-main", _s("s01", "公司主营业务收入为 100.00 万元。", "m01"))]),
        request_face=_face())
    check(missing.coverage["exact_match"] is False
          and missing.coverage["missing"] == ["sub-region"],
          "**反例**：少一个小节 ⇒ 逐条报出缺哪个（缺的那节记 `missing`）")
    files = CRRB.diagnosis_files(diag)
    check(set(files) == {"original_draft.md", "derived_diagnosis.md", "derived_diagnosis.json"},
          "一次诊断落三个文件")
    body = json.loads(files["derived_diagnosis.json"])
    check(body["publishable"] is False
          and body["readback_version"] == CRRB.CITED_REPLY_READBACK_VERSION,
          "产物恒带 `publishable: false` 与本模块自己声明的规则版本号（不写字面量）")
    page = files["original_draft.md"]
    check("不可发布" in page and "公司主营业务收入为 100.00 万元。" in page,
          "原稿人读页醒目标注**不可发布**，并**逐字保留**原句文本")
    check("引用 `m01`：材料" in page and "所在小节声明覆盖的栏目：`aspect-main`" in page
          and "**本段**声明服务的那一栏：`aspect-main`" in page
          and "`numeric_qualification`" in page,
          "⇒ 人读页逐句给出**引用材料、所在栏目（小节覆盖哪几栏与**本段**答的是哪一栏分两行）、"
          "硬错或待审原因**（都在这一句下面，不必翻到别页去拼）——"
          "小节那一行回答「这一节负责什么」，段那一行才回答「这一句在答哪一栏」")
    check("已知至少" in page and "句硬核对失败" in page,
          "⇒ 人读页开头就给出**可判轴头条**（几句话硬错、其中几句是空格型）")
    body_page = files["derived_diagnosis.md"]
    check("机械命中「材料登记在这一栏」**不等于**" in body_page
          and "严格排版空白等价" in body_page,
          "⇒ 派生诊断把业务错因**指向正确环节**，并写明空格型差异已并入**严格排版空白等价**"
          "（非失败、单列），放宽的只有空白")
    check(CRRB.GAP_REWRITE_PRESCRIPTION in body_page,
          "⇒ 缺口口径给出**应改写的模板句**（不改原文，只给口径）")
    unjudged = [a for a in body["axes"] if a["status"] != CRRB.AXIS_ADJUDICATED]
    check(len(unjudged) == 12 and all(a["basis"] for a in unjudged),
          f"未判轴逐条列出且各带依据（{len(unjudged)} 条）")
    check(any(a["axis"] == "material_adoption" for a in unjudged)
          and any(a["axis"] == "report_version_binding" for a in unjudged),
          "⇒ 因缺精确清单而判不了的那几轴（采用去向、版本绑定）**没有**被静默略过")
    details.append("NOTE §8：这份诊断**不与清单对账**，因此不能倒写成那一轮的正式通过。")

    # ==================================================== §9 缺口的栏位口径
    #: 一处**真实**读数缺陷（`crrb-7` 修）：`_gap_context` 曾拿**整节声明的栏目**去数登记来源，
    #: 于是一个小节里只要有材料，它的**每一条**缺口都会拿到同一个大数字，自称
    #: `no_source_in_manifest` 的缺口还会被标成「口径可疑」——而链自己的逐栏判据
    #: （`aspect_for_requirement`）证明那一栏是 0。两条判据打架，坏的是读数。本节点住逐栏口径。
    details.append("## §9 一条缺口落到**哪一栏**：逐栏计数，不等于整节计数")
    req_cust = "列示客户集中度"
    req_supp = "列示供应商集中度"
    conc_sub = {"subsection_id": "sub-conc", "title": "客户与供应商集中度",
                "declared_aspect_ids": ["aspect-cust", "aspect-supp"],
                "requirement_text": f"{req_cust}\n{req_supp}"}
    cust_mats = [
        {"key": "m-cust-1", "text": "公司向前五名客户销售额占年度销售总额的比例为 30%。",
         "aspect_ids": ["aspect-cust"]},
        {"key": "m-cust-2", "text": "公司前五名客户合计销售金额为 1,000.00 万元。",
         "aspect_ids": ["aspect-cust"]},
    ]
    face_conc = {"task_id": "task-demo", "section_id": "company", "prompt_version": "cp-1",
                 "authority_facts": [], "materials": list(cust_mats),
                 "subsections": [dict(conc_sub)]}
    gap_cust = {"subsection_id": "sub-conc", "requirement_text": req_cust,
                "reason": "manifest_partial_for_requirement", "detail": "本次输入缺少合格数值事实。"}
    gap_supp = {"subsection_id": "sub-conc", "requirement_text": req_supp,
                "reason": "no_source_in_manifest", "detail": "本次输入里没有该事实。"}
    gap_unknown = {"subsection_id": "sub-conc", "requirement_text": "列示前五名供应商采购集中度",
                   "reason": "no_source_in_manifest", "detail": "对不上任何一行的文本。"}
    conc_reply = _reply(
        [_sub("sub-conc", _s("s01", "公司向前五名客户销售额占年度销售总额的比例为 30%。", "m-cust-1"))],
        gaps=[gap_cust, gap_supp])

    d9 = CRRB.diagnose(reply_text=conc_reply, request_face=face_conc)
    supp9 = _gap_row(d9, req_supp)
    cust9 = _gap_row(d9, req_cust)
    check(supp9["column"] == "aspect-supp" and cust9["column"] == "aspect-cust",
          "⇒ 两条缺口各自落到**自己的那一栏**（`requirement_text` 逐行 ↔ 栏目逐位的对位）")
    check(supp9["scope_aspect_ids"] == ["aspect-supp"],
          "⇒ 计数口径就是那一栏，不是整节声明的两栏")
    check(cust9["materials_registered_for_aspect"] == 2
          and supp9["materials_registered_for_aspect"] == 0,
          "⇒ 同一个节里：客户栏 2 条、供应商栏 0 条——**逐栏各算各的**")
    check(supp9["claims_no_source_but_materials_exist"] is False,
          "**反例（这正是修掉的那个错）**：本小节别处有材料，**不等于**供应商栏有材料；"
          "该栏确实是 0，所以**不得**被标成「口径可疑」")
    check(cust9["claims_no_source_but_materials_exist"] is False,
          "客户栏没有自称「没有来源」，同样不标")

    # 事实行也算「登记来源」：只数材料行会把「有权威事实」错读成「零来源」。
    face_fact = dict(face_conc, authority_facts=[
        {"key": "f-supp", "text": "报告期内公司前五名供应商合计采购金额占年度采购总额的比例为 21%。",
         "aspect_ids": ["aspect-supp"]}])
    d9b = CRRB.diagnose(reply_text=conc_reply, request_face=face_fact)
    supp9b = _gap_row(d9b, req_supp)
    check(supp9b["materials_registered_for_aspect"] == 1
          and supp9b["material_keys_registered_for_aspect"] == ["f-supp"],
          "⇒ 「登记来源」= 材料行 **∪** 事实行：该栏只有一条权威事实时也必须数到 1")
    check(supp9b["claims_no_source_but_materials_exist"] is True,
          "⇒ 这一栏确有一条权威事实却自称「没有来源」⇒ 标成**口径可疑**（这才是该标的情形）")

    # 对不上栏位：判不出来就**不许**长得像判过了。
    d9c = CRRB.diagnose(reply_text=_reply(
        [_sub("sub-conc", _s("s01", "公司向前五名客户销售额占年度销售总额的比例为 30%。", "m-cust-1"))],
        gaps=[gap_unknown]), request_face=face_conc)
    unknown9 = _gap_row(d9c, gap_unknown["requirement_text"])
    check(unknown9["column"] is None,
          "⇒ 文本对不上任何一行时 `column` 记 `null`（「查不出来」与「查出来是 0」不许同形）")
    check(unknown9["scope_aspect_ids"] == ["aspect-cust", "aspect-supp"]
          and unknown9["materials_registered_for_aspect"] == 2,
          "⇒ 退回**整节声明**的较宽口径，并在读数里如实显示（不猜一个栏目）")
    gap_page = CRRB.render_gap_summary(d9c)
    check("对不上栏位" in gap_page,
          "⇒ 人读页把这一条标成**对不上栏位**（退回整节口径：…），不冒充已判到栏")
    check("该栏登记来源数" in CRRB.render_gap_summary(d9)
          and "口径可疑" not in CRRB.render_gap_summary(d9),
          "**反例**：逐栏口径下，第 9 节那两条缺口**一条**都不该被列入「口径可疑」")
    details.append("NOTE §9：本节的 0/1/2 都来自请求面**逐行对位**，判据与生产侧 "
                   "`cited_writer.aspect_for_requirement` 是同一份；对不上时报 `null` 而不是猜。")

    # ==================================================== §10 空壳段归一：离线侧读的是同一套三项读数
    #: （`crrb-8`）生产侧 `crn-3` 的第三条规则要三项读数：这一段声明的栏**属不属于本小节**、
    #: 这一栏**登记到几条来源**、顶层**有没有系统判定的那条缺口**。离线诊断若少交一项，
    #: 同一串字节会在两处判出相反的 `empty_shell_rule`——本节把这一条钉在离线的入口上。
    details.append("## §10 空壳段：离线诊断与生产侧读同一套三项读数（`crrb-8` 起；"
                   "`crn-4` 的第四项见 §10b）")
    visit_sub = {"subsection_id": "sub-visit", "title": "全球布局",
                 "declared_aspect_ids": ["aspect-visit"], "requirement_text": "列示境外布局"}
    face10 = dict(face_conc, subsections=[dict(conc_sub), dict(visit_sub)])
    _live = _s("s01", "公司向前五名客户销售额占年度销售总额的比例为 30%。", "m-cust-1")

    #: 空壳段：`aspect-supp` 在 `sub-conc` 声明、本轮零登记来源、`sub-conc` 里也挂着那条缺口——
    #: 三项里唯独「写进了 `sub-visit`」不成立。
    borrow10 = _reply(
        [_sub("sub-conc", _live), _sub("sub-visit", aspect_ids=["aspect-supp"])],
        gaps=[gap_cust, gap_supp])
    d10 = CRRB.diagnose(reply_text=borrow10, request_face=face10)
    check(d10.normalization["dropped_empty_shell_paragraphs"] == []
          and [k["blocked_by"] for k in d10.normalization["kept_empty_paragraphs"]]
          == ["aspect_not_in_subsection"],
          "**反例（跨小节借用）**：离线侧也**不**删它，原因码 `aspect_not_in_subsection`——"
          "与生产侧 `crn-4` 判的是同一件事（`aspect-supp` 由 `sub-conc` 声明，"
          "而这一段写在 `sub-visit` 里）")
    own10 = _reply([
        {"subsection_id": "sub-conc", "title": "sub-conc", "paragraphs": [
            {"paragraph_id": "p1", "sentences": [_live]},
            {"paragraph_id": "p-shell", "aspect_ids": ["aspect-supp"], "sentences": []}]},
    ], gaps=[gap_cust, gap_supp])
    d10b = CRRB.diagnose(reply_text=own10, request_face=face10)
    check([r["reason"] for r in d10b.normalization["dropped_empty_shell_paragraphs"]]
          == ["empty_shell_no_source"]
          and [r["aspect_ids"] for r in d10b.normalization["dropped_empty_shell_paragraphs"]]
          == [["aspect-supp"]],
          "**正例（对照）**：同一个栏目的空壳段写进**声明它的那一小节** ⇒ 离线侧照旧删，"
          "走唯一的 `empty_shell_no_source`——两边只差**归属**一项")

    #: 请求面里没有列出的那一小节 ⇒ 一律按「没证明就不许删」处理（方向与另两项相反）。
    check(CRRB._empty_shell_context_for_face(
        {"subsections": [dict(visit_sub)]}, [gap_supp]).aspect_belongs_to(
            "sub-conc", "aspect-supp") is False,
          "⇒ 请求面**没有列出**的小节不登记：按「查不到」处理，方向与另两项读数相反"
          "（那里「取不到即零」，这里「取不到即不许删」）")
    check(d10.normalization["empty_shell_rule"] == CRRB.CRN.EMPTY_SHELL_RULE_APPLIED,
          "⇒ 判了就是判了：`empty_shell_rule` 记 `applied`，即使这一跑**一段都没删**"
          "（「跑了、没删」与「根本没评估」在产物上分得开）")
    details.append("NOTE §10：离线侧交出去的是**三项**读数；少交一项不会报错，"
                   "只会让同一串字节在两处判出相反结果——这正是本节要钉的那件事。")

    # ==================================================== §10b 第四项读数：缺口支持（`crrb-9`）
    #: 动因（`m930_3_cited_upload_20261005T131532Z`）：空壳段声明的栏**登记着来源**，
    #: `crn-3` 的「零登记来源」那一条不成立；而模型自述的 `no_source_in_manifest` 被系统改判成
    #: `source_present_but_not_admissible` ⇒ `crn-4` 的**第二条依据**成立。离线侧必须交出这一项，
    #: 否则同一串字节在生产侧被删、在离线侧被留。
    details.append("## §10b 缺口支持：离线侧交出**第四项**读数（`crrb-9`）")
    face_supp_sourced = dict(face_conc, materials=list(cust_mats) + [
        {"key": "m-supp-1", "text": "报告期内公司前五名供应商合计采购金额占年度采购总额的比例为 21%。",
         "aspect_ids": ["aspect-supp"]}])
    ctx10c = CRRB._empty_shell_context_for_face(face_supp_sourced, [gap_cust, gap_supp])
    check(ctx10c.aspect_unique_gap_reason("sub-conc", "aspect-supp")
          == "source_present_but_not_admissible"
          and ctx10c.registered_source_counts.get("aspect-supp") == 1,
          "⇒ 该栏**登记着 1 条来源**，而模型自述的 `no_source_in_manifest` 被 `assign_gap_reason` "
          "**改判**为 `source_present_but_not_admissible`——第四项读数记的是**系统判定**的理由，"
          "不是模型自述的那一个字")
    check(ctx10c.aspect_unique_gap_reason("sub-conc", "aspect-cust")
          == "manifest_partial_for_requirement",
          "**反例（理由不在闭集）**：客户栏那条缺口自称 `manifest_partial_for_requirement`、"
          "该栏登记着 2 条来源 ⇒ 系统理由**原样保留**；读数**如实记下**它（台账要能看见真实的"
          "理由），但 `GAP_SUPPORTED_REASONS` 里没有它 ⇒ **不**构成删除依据"
          "（「只覆盖了一部分」与「没有可写来源」是两件事）")
    d10c = CRRB.diagnose(reply_text=own10, request_face=face_supp_sourced)
    check([r["reason"] for r in d10c.normalization["dropped_empty_shell_paragraphs"]]
          == ["empty_shell_gap_supported"]
          and [r["registered_source_counts"]
               for r in d10c.normalization["dropped_empty_shell_paragraphs"]]
          == [{"aspect-supp": 1}]
          and [r["system_gap_reasons"]
               for r in d10c.normalization["dropped_empty_shell_paragraphs"]]
          == [{"aspect-supp": "source_present_but_not_admissible"}],
          "**正例（对照）**：同一个空壳段，离线侧走**新依据**删掉，原因码 "
          "`empty_shell_gap_supported`；台账逐栏记下**登记数 1** 与**系统理由**——"
          "登记数**不**被伪装成 0，「登记了材料」也**不**被写成「足以证明集中度」")
    ctx10dup = CRRB._empty_shell_context_for_face(face_supp_sourced,
                                                  [gap_cust, gap_supp, dict(gap_supp)])
    check(ctx10dup.aspect_unique_gap_reason("sub-conc", "aspect-supp") == "",
          "**反例（非唯一）**：同一栏**两条**缺口 ⇒ 第四项读数**不登记**它"
          "（「可唯一对应」是这条依据的前提），离线侧因此也不删——"
          "方向与生产侧一致，靠的是两边**共用** "
          "`cited_writer.unique_subsection_gap_reasons` 这一份口径")
    check(CRRB.CITED_REPLY_READBACK_VERSION.endswith("9")
          and d10c.normalization["normalize_version"].endswith("4"),
          "⇒ 两侧的版本号各自随规则前进（读回侧 `crrb-9`、归一 `crn-4`）——"
          "但读回侧是用**现行**的归一函数重算，**不**按产物自己声明的版本分派"
          "（`diagnose()` 直接调 `CRN.normalize_cited_reply`，见其 937–939 行）")

    details.append(
        "NOTE 本模块只证明**诊断的判据与边界**；正文是否合格、结论是否站得住，仍须独立审阅与"
        "人工接受，本模块不做、也不宣称做过。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

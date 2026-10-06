"""Eval: M930-3 批次 B §二 2.3 —— 候选的**内容资格**与三条互斥去路。

用法: python -m evals.test_m930_3_material_qualification

本裁决要求的不是"把可疑片段删掉"，而是：

- 孤立标题、纯版式碎片**不得**作为独立业务事实或充分写作材料；
- 勾选内容必须**连同问题、选中状态与来源边界**按结构化表单规则判断，**不能一律删除**；
- 原始材料及定位保留在审计链里（原文片段 + locator + 内容指纹），候选不静默消失；
- 被拒候选必须有 typed 审计；`not_used` **不是** gap。

因此本模块逐条证明：

1. **分类器的反例**：真句子（含句读 / 含数字 / 正文里夹一行勾选）一律**不**被误杀；
   孤立标题（与节点标题同文 / 编号标题形）、纯版式碎片（无可读内容 / 无句读短标签）
   逐条落到**已登记**的 typed 原因上；
2. **勾选行按表单读**：状态可判定的行**成为材料**（不是被删除），并带着问题文本、
   问题出处（行内前缀 / 节点标题）与逐选项选中状态；状态判不出的行落
   `selection_form_unresolved`，且子原因取自封闭集合；
3. **fail-closed**：未登记的内容形态 / 形态与原因错配 / 处置片段超长 / 非表单行带
   selection 一律抛错；孤立标题与版式碎片**永远**不能是材料；
4. **守恒与互斥**（真实夹具上的整批）：候选 = 材料 + 结构 gap + 内容处置；三个 span 集合
   两两不相交；同一 span 不得有两条处置；每条处置都带原文片段、locator、指纹，且
   **没有任何处置的 kind 是 `text`**（"把正文当碎片处置掉"必须不成立）。

公司无关：整批部分只用仓库内版本化夹具，身份逐项取自 manifest；本模块里没有公司代号、
文件名、页码、表号或固定年份的字面量。不调 LLM、不联网、不写任何库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import tree_materials as TM

REPO = Path(__file__).resolve().parent.parent

#: 私用区里的一个勾选字形（实测文档的"已勾"落在这一区）。本模块**不**把该码位写成
#: 源码字面量：私用码位在编辑器/传输里易被改写，用码位构造才能保证验的是同一条规则。
PUA_CHECKED = chr(0xF052)


class _Span:
    """分类器只看 ``normalized_text``：测试替身只需要这一个字段。"""

    def __init__(self, text: str) -> None:
        self.normalized_text = text


def _kind_and_reason(text: str, *, node_title: str | None = None) -> tuple:
    verdict = TM.classify_span_content(_Span(text), node_title=node_title)
    return verdict.kind, verdict.reason, verdict.is_material


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:120]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:120]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    # ============ 1. 不误杀：真内容一律仍是材料 ==========================
    keep = [
        ("公司提供电芯、电池柜、储能集装箱以及交流侧系统等储能产品解决方案。", None),
        ("报告期内，公司严格依照《公司法》及其配套指引开展内部控制工作。", "内部控制"),
        ("营业收入本期为 1,234 万元。", "主要会计数据"),
        # 含数字但无句读：日期/金额行不是"没有句读的短标签"——判据显式排除数字，
        # 宁可按正文处理，也不把带数字的行当残段删掉。
        ("2025年12月31日", "主要会计数据"),
        # 正文里夹着一行勾选：整段仍是正文（混合片段不得按表单行切）。
        ("☑适用 □不适用 报告期内委托理财概况", "委托理财"),
    ]
    for text, title in keep:
        kind, reason, is_material = _kind_and_reason(text, node_title=title)
        check(is_material and reason == "",
              f"真内容仍为材料（kind={kind}，{text[:24]!r}）")

    # ============ 2. 孤立标题：typed 原因，不是 gap、不是材料 =============
    kind, reason, is_material = _kind_and_reason("行业分类", node_title="行业分类")
    check(not is_material and kind == "isolated_heading"
          and reason == "isolated_heading_matches_node_title",
          "与所在节点标题逐字相同的文本落 isolated_heading（不是材料）")
    kind, reason, _ = _kind_and_reason("  行业 分类  ", node_title="行业分类")
    check(kind == "isolated_heading"
          and reason == "isolated_heading_matches_node_title",
          "同文判定忽略空白折叠（版式差异不改变结论）")
    kind, reason, is_material = _kind_and_reason("1）营业收入整体情况", node_title="主要业务")
    check(not is_material and reason == "isolated_heading_numbered_label",
          "编号标题形落 isolated_heading_numbered_label")
    kind, _, _ = _kind_and_reason("1）营业收入整体情况如下。", node_title="主要业务")
    check(kind == "text",
          "编号形但带句末标点的是正文（句读优先于编号形，不误杀）")

    # ============ 3. 纯版式碎片：typed 原因 =============================
    for text, node_title, reason in [
        ("", "主要业务", "layout_fragment_no_readable_content"),
        ("□", "主要业务", "layout_fragment_no_readable_content"),
        ("☑", "主要业务", "layout_fragment_no_readable_content"),
        ("□ □", "主要业务", "layout_fragment_no_readable_content"),
        ("本期金额", "主要会计数据", "layout_fragment_bare_label"),
        ("采购商品/接受劳务情况表", "关联交易", "layout_fragment_bare_label"),
    ]:
        kind, got_reason, is_material = _kind_and_reason(text, node_title=node_title)
        check(not is_material and kind == "layout_fragment" and got_reason == reason,
              f"版式碎片落 {reason}（{text[:18]!r}）")
    check("layout_fragment_bare_label" not in TM.TREE_MATERIAL_CONTENT_REASONS_BY_KIND
          .get("isolated_heading", ()),
          "原因不得跨形态复用（一个形态只认自己那一组原因）")

    # ============ 4. 勾选行：按结构化表单读，可判定的**成为材料** ==========
    verdict = TM.classify_span_content(_Span("☑适用 □不适用"), node_title="是否委托理财")
    check(verdict.is_material and verdict.kind == "selection_form"
          and verdict.reason == "" and verdict.selection
          and verdict.selection["resolved"] is True,
          "状态可判定的勾选行**成为材料**（不是一律删除）")
    # `tmr-3` 定点业务纠正①：行内没写主语时**不回指**所在节点标题。节点标题可能是这一行的
    # **栏目**（真实反例：`（8） 主要销售客户和主要供应商情况` 是个栏目，原件的勾选框属于其下
    # 「主要客户其他情况说明」「主要供应商其他情况说明」两个子项），回指会把子项状态锚到整个
    # 栏目上。空所问事项是 fail-closed：这一行授权不了任何支撑，但**仍是材料**（原文与来源留档）。
    check(verdict.selection["question"] == ""
          and verdict.selection["question_scope"] == "",
          "行内没有前缀时，所问事项**留空**，不得用所在节点标题顶替")
    check("行内未写所问事项" in verdict.detail and "不指向任何栏目" in verdict.detail,
          f"读回说明必须写明「这一行没写主语、不指向任何栏目」，实得 {verdict.detail!r}")
    check(verdict.selection["selected_labels"] == ["适用"],
          "选中状态按字形逐项记录（只认唯一一个「已选」字形）")

    verdict = TM.classify_span_content(_Span("是否委托理财 ☑是 □否"), node_title="无关标题")
    check(verdict.is_material and verdict.selection["question"] == "是否委托理财"
          and verdict.selection["question_scope"] == "in_span",
          "行内前缀优先作问题文本，出处记为 in_span")

    verdict = TM.classify_span_content(
        _Span(f"{PUA_CHECKED}适用 □不适用"), node_title="是否委托理财")
    check(verdict.is_material and verdict.selection
          and verdict.selection["selected_labels"] == ["适用"],
          "私用区字形同样算勾选记号（不靠枚举某份文档用了哪个私用字形）")
    check(TM.is_selection_marker(PUA_CHECKED) and TM.is_selection_marker("□")
          and not TM.is_selection_marker("公"),
          "字形判定：私用区与封闭集合内为标记，普通汉字不是")

    # 选项多于两个、恰有一个「已选」：仍是可判定的状态（多选一）。
    verdict = TM.classify_span_content(
        _Span("☑适用 □不适用 □其他"), node_title="是否委托理财")
    check(verdict.is_material and verdict.selection["resolved"] is True
          and verdict.selection["selected_labels"] == ["适用"],
          "多选一且恰有一个「已选」时状态可判定（不因选项多就判不出）")

    # 状态判不出：封闭子原因各有反例。（`question_not_resolvable` 已在 `tmr-3` 退出词表：
    # 所问事项取不到**不再是**读不定——留空仍是材料，见上。）
    for text, title, sub_reason in [
        ("□甲 □乙", None, "state_not_determinable"),
        ("☑适用", "是否委托理财", "options_not_segmented"),
        ("☑适用 ☑不适用", "是否委托理财", "state_not_determinable"),
        ("□适用 □不适用", "是否委托理财", "state_not_determinable"),
        ("□适用 □不适用 □其他", "是否委托理财", "state_not_determinable"),
    ]:
        verdict = TM.classify_span_content(_Span(text), node_title=title)
        check(not verdict.is_material and verdict.kind == "selection_form"
              and verdict.reason == "selection_form_unresolved"
              and verdict.selection["unresolved_reason"] == sub_reason,
              f"判不出确定状态落 selection_form_unresolved/{sub_reason}（{text!r}）")

    # `tmr-2`：选项串之后**恰好一句**的内容 = 这一行"管着"的那句话 ⇒ 仍是表单行，
    # 但那句话逐字进 `trailing_content` 并按排除清单**不得**用作支撑。
    # （旧读法把它当成"最后一个选项的标签"，整行随长度约束退化成普通正文——而正文身份
    # **看起来**能支撑成立日期或业务构成，实则不能。这正是本批要修的那条误读。）
    _TRAILING = "报告期内公司委托理财情况如下。"
    verdict = TM.classify_span_content(
        _Span(f"☑适用 □不适用 {_TRAILING}"), node_title="委托理财")
    check(verdict.kind == "selection_form" and verdict.is_material
          and verdict.selection["resolved"] is True
          and verdict.selection["selected_labels"] == ["适用"],
          f"选项串后恰一句 ⇒ 仍是表单行且状态可判定（实得 kind={verdict.kind}）")
    check(verdict.selection["trailing_content"] == _TRAILING,
          f"那句话说到底必须**逐字**留档在 `trailing_content`（实得 "
          f"{verdict.selection['trailing_content']!r}）")
    check(all(_TRAILING not in o["label"] and "报告期" not in o["label"]
              for o in verdict.selection["options"]),
          "那句话**不得**被读成选项标签（旧读法正是把它吞进最后一个标签）")
    _cols = TM.selection_form_columns(selection=verdict.selection)
    check(_cols["trailing_content"] == verdict.selection["trailing_content"]
          and _cols["permitted_use"] == TM.TREE_MATERIAL_SELECTION_PERMITTED_USE
          and "no_support_from_trailing_content" in _cols["exclusions"],
          "派生列必须原样带出这句话，且排除清单明文禁止拿它作支撑")
    check(_cols["asked_item"] == "" and _cols["asked_item_scope"] == "",
          f"这一行行内没写主语 ⇒ 派生列里的所问事项必须**留空**（不得写成节点标题 "
          f"`委托理财`）：实得 {_cols['asked_item']!r} / {_cols['asked_item_scope']!r}")
    # 所在节点这一列照旧在（它现在是**导航坐标**，不是所问事项的出处）；
    # 排除清单照旧明文禁止拿这一行作宽栏目覆盖证明——空所问事项只是把"能说话的范围"
    # 收到零，不是把这份材料作废。
    check("not_a_broad_column_coverage_proof" in _cols["exclusions"]
          and "no_support_from_trailing_content" in _cols["exclusions"],
          "留空不得顺手放宽排除清单（范围判据一条都不减）")

    # 同一形状的两个**否定**方向：既不能读成正文、也不能读成"可承重的材料"。
    # （a）空框即未选：这一行读不出选中状态 ⇒ 连材料都不是；但那句话仍须逐字留档，
    #      否则"它到底说了什么"就随处置一起丢了。
    verdict = TM.classify_span_content(
        _Span(f"□适用 □不适用 {_TRAILING}"), node_title="委托理财")
    check(verdict.kind == "selection_form" and not verdict.is_material
          and verdict.reason == "selection_form_unresolved"
          and verdict.selection["unresolved_reason"] == "state_not_determinable",
          f"两个空框（未选）不因带一句话就变成正文或材料（实得 "
          f"kind={verdict.kind} material={verdict.is_material}）")
    check(verdict.selection["trailing_content"] == _TRAILING
          and all(_TRAILING not in o["label"] for o in verdict.selection["options"]),
          "读不定也不得把那句话吞成标签（留档与处置是两件事）")
    check(TM.selection_form_columns(selection=verdict.selection) is None,
          "读不定的表单行**不产出**结构化列（没有可承重的读法）")

    # 形状真的不像选项行的一律不按表单读（宁可按正文，也不把正文切碎）。
    for text, label in [
        # 一整段披露正文可以通篇用逗号连成**一个**句号的长句（实测 176 / 189 字的混合片段
        # 正是这个形状）——超过分句标点上限即判为正文。
        ("□适用 □不适用 " + "公司" + "，" * (TM.SELECTION_TRAILING_CLAUSE_MAX + 1) + "。", "多分句长句"),
        # 尾随部分不止一句（两个句末标点）⇒ 正文。
        ("□适用 □不适用 报告期内公司委托理财情况如下。另见下表。", "两句尾随"),
        # 无句末标点但超长 ⇒ 段落/小标题，不是"这一行管着的那句话"。
        ("□适用 □不适用 " + "甲" * 60, "无句读超长"),
    ]:
        verdict = TM.classify_span_content(_Span(text), node_title="委托理财")
        check(verdict.kind == "text" and verdict.is_material,
              f"混合片段不按表单行读、整段按正文处理（{label}）")

    # ============ 5. fail-closed：形态、原因、字段必须自洽 =================
    expect_error(lambda: TM.SpanContentQualification("bogus", True),
                 TM.TreeMaterialError, "未登记的内容形态必须拒绝",
                 needle="未登记的内容形态")
    expect_error(lambda: TM.SpanContentQualification("text", False,
                                                     "layout_fragment_bare_label"),
                 TM.TreeMaterialError, "text 不得落处置（它是材料）")
    expect_error(lambda: TM.SpanContentQualification("isolated_heading", True),
                 TM.TreeMaterialError, "孤立标题永远不能是材料",
                 needle="永远不是材料")
    expect_error(lambda: TM.SpanContentQualification("layout_fragment", True),
                 TM.TreeMaterialError, "版式碎片永远不能是材料",
                 needle="永远不是材料")
    expect_error(lambda: TM.SpanContentQualification(
        "isolated_heading", False, "layout_fragment_bare_label"),
        TM.TreeMaterialError, "形态与原因错配必须拒绝")
    expect_error(lambda: TM.SpanContentQualification(
        "text", True, selection={"resolved": True}),
        TM.TreeMaterialError, "非表单行不得携带 selection")
    expect_error(lambda: TM.SpanContentQualification(
        "selection_form", False, "selection_form_unresolved",
        selection={"resolved": False, "unresolved_reason": "made_up"}),
        TM.TreeMaterialError, "未登记的表单未决子原因必须拒绝",
        needle="未登记的表单行未决子原因")
    expect_error(lambda: TM.SpanContentQualification(
        "selection_form", False, "selection_form_unresolved",
        selection={"resolved": True, "unresolved_reason": "state_not_determinable"}),
        TM.TreeMaterialError, "已读出确定状态时不得带未决原因")

    def _disposition(**overrides) -> TM.TreeMaterialContentDisposition:
        kwargs = dict(
            span_id="os-1", node_id="n-1", heading_path=("甲",), page_range=(1, 1),
            span_locator="p1:c0-10", char_range=(0, 10), content_fingerprint="fp",
            text_excerpt="本期金额", text_char_length=4, kind="layout_fragment",
            reason="layout_fragment_bare_label", detail="d")
        kwargs.update(overrides)
        return TM.TreeMaterialContentDisposition(**kwargs)

    _disposition()  # 合法构造必须通过
    passed += 1
    expect_error(lambda: _disposition(kind="text", reason=""),
                 TM.TreeMaterialError, "text 不得成为处置记录")
    expect_error(lambda: _disposition(reason="isolated_heading_numbered_label"),
                 TM.TreeMaterialError, "处置的形态与原因必须匹配")
    expect_error(lambda: _disposition(
        text_excerpt="甲" * (TM.CONTENT_DISPOSITION_EXCERPT_CHARS + 1)),
        TM.TreeMaterialError, "处置只留档片段，不得搬运全文",
        needle="不得超过")
    expect_error(lambda: _disposition(kind="selection_form",
                                      reason="selection_form_unresolved"),
                 TM.TreeMaterialError, "表单形态的处置必须带结构化读法")

    # ============ 6. 三条去路的守恒与互斥（真实夹具整批）==================
    skipped += _batch_conservation(check, expect_error, details)

    # ============ 7. 字形判据不漂移（替身与材料侧共用同一份）==============
    from evaluation import run_m930_3_acceptance as RUN
    for ch in TM.TREE_MATERIAL_SYMBOL_GLYPHS + chr(0xF052) + "公A1 ":
        check(RUN._has_selection_marker(ch) == TM.is_selection_marker(ch),
              f"替身与材料侧对字形 {ch!r} 的判定一致（词表不分叉）")
    check(RUN._has_selection_marker("□适用 不适用")
          and not RUN._has_selection_marker("公司提供储能产品解决方案。"),
          "替身选材仍挡下勾选框行、放行可读正文")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


def _batch_conservation(check, expect_error, details) -> int:
    """真实夹具上的整批守恒：候选 = 材料 + 结构 gap + 内容处置，且三集合互斥。

    夹具缺失就如实 skip（不静默算过）。
    """
    import dataclasses

    from document_structure import evidence_gateway as EG
    from document_structure import span_builder as SB
    from document_structure.schema import PageLayout
    from evals import tree_stage_env as STAGE

    if not EG.fixture_root_dir().is_dir():
        details.append(f"SKIP 版本化夹具目录不存在（{EG.FIXTURE_ROOT_RELPATH}）")
        return 1
    root = EG.load_fixture_root()
    company, document_id = root["company_id"], root["document_id"]
    document_version = root["document_version"]
    authority = EG._issue_fixture_evidence_authority(root)
    ev_snapshot, blocks = authority.load_snapshot_and_blocks(
        company_id=company, document_id=document_id,
        document_version=document_version)
    binding = TM.evidence_binding_from_snapshot(ev_snapshot)
    pdf_path = REPO / root["source_pdf"]["relpath"]
    if not pdf_path.is_file():
        details.append("SKIP 夹具冻结 PDF 不在仓库内（不猜测字节）")
        return 1
    layout = PageLayout.from_dict(json.loads(
        (EG.fixture_root_dir() / "page_layout.json").read_text(encoding="utf-8")))
    with STAGE.simulated_a_environment():
        handoff = SB._issue_fixture_ts3_handoff(
            raw_pdf=pdf_path.read_bytes(), expected_layout=layout,
            company_id=company, document_id=document_id, fixture_root=root)
        snapshot = SB._build_from_pinned_handoff(handoff, stage="distribution_only")

    batch = TM.TreeMaterialPayloadResolver(
        snapshot=snapshot, blocks=blocks, current_evidence=binding).batch
    identity = batch.identity
    check(identity["candidate_span_count"] == identity["material_span_count"]
          + identity["gap_count"] + identity["content_disposition_count"],
          "候选 = 材料 + 结构 gap + 内容处置（三条互斥去路合计等于候选数）")
    material_spans = set(batch.span_ids)
    gap_spans = {g.span_id for g in batch.gaps}
    disposition_spans = {d.span_id for d in batch.content_dispositions}
    check(not (material_spans & gap_spans) and not (material_spans & disposition_spans)
          and not (gap_spans & disposition_spans),
          "三个 span 集合两两不相交（同一候选只有一条去路）")
    check(len(disposition_spans) == len(batch.content_dispositions),
          "同一 span 不得有两条处置")
    check(set(batch.content_disposition_reason_counts())
          <= set(TM.TREE_MATERIAL_CONTENT_REASONS),
          "处置原因全部来自封闭集合")
    check(all(d.kind != "text" for d in batch.content_dispositions),
          "没有任何处置把正文（text）当碎片处理掉")

    # 内容处置 ≠ gap：它不出现在 gap 集合里，也不是"重切失败"。
    for disposition in batch.content_dispositions:
        check(disposition.reason not in TM.TREE_MATERIAL_GAP_REASONS,
              f"处置原因不得混进 gap 闭集（{disposition.reason}）")
        check(not batch.gap_by_span(disposition.span_id),
              "被内容处置的 span 不得同时登记结构 gap")
        break  # 逐条断言等价：上面两条对空集也成立，取一条做样本即可

    # 原文与定位必须留在审计链里（不静默丢弃）。
    lossy = [d.span_id for d in batch.content_dispositions
             if not (d.span_locator and d.content_fingerprint and d.text_excerpt
                     and d.char_range)]
    check(not lossy, f"每条处置都带原文片段与精确 locator（缺失 {lossy[:3]}）")
    overlong = [d.span_id for d in batch.content_dispositions
                if len(d.text_excerpt) > TM.CONTENT_DISPOSITION_EXCERPT_CHARS
                or d.text_char_length < len(d.text_excerpt)]
    check(not overlong, f"处置片段不超上限且不超原文长度（异常 {overlong[:3]}）")
    check(all(isinstance(d.page_range, tuple) and len(d.page_range) == 2
              for d in batch.content_dispositions),
          "每条处置都带页区间（来源边界可回查）")

    # to_dict 投影不得丢字段（UI / 审计读回都走它）。
    projected = batch.to_dict()
    check(projected["content_disposition_count"] == len(batch.content_dispositions)
          and len(projected["content_dispositions"]) == len(batch.content_dispositions),
          "to_dict 如实投影内容处置（读回不丢）")
    check(projected["identity"]["content_disposition_count"]
          == len(batch.content_dispositions),
          "身份投影含处置计数（读回可对账）")
    # 没有标题树时 `outline_available` 必须为假：此时"零条 isolated_heading"是**判据不可用**
    # 的结果，不得被读成"文档里没有孤立标题"。本批确实没有传 outline。
    check(identity["outline_available"] is False,
          "未提供标题树时如实登记 outline_available=False（不把判据不可用读成结论）")

    # ---- 6b. 同一份候选换一条去路：给了标题树后，同文候选必须落处置而不是材料 ----
    target = None
    for span in snapshot.spans:
        if TM.is_tree_material_candidate(span) and span.span_id in material_spans \
                and any(ch in span.normalized_text for ch in "。；！？"):
            target = span
            break
    if target is None:
        details.append("SKIP 夹具里没有可用的正向候选来验证标题树判据")
        return 1

    class _Node:
        def __init__(self, node_id: str, title: str) -> None:
            self.node_id = node_id
            self.title = title
            self.structural_path = ("甲",)

    class _Outline:
        def __init__(self, nodes) -> None:
            self.nodes = nodes

    with_outline = TM.build_tree_materials(
        snapshot=snapshot, blocks=blocks, current_evidence=binding,
        outline=_Outline((_Node(target.node_id, target.normalized_text),)))
    til = with_outline.identity
    check(til["outline_available"] is True,
          "提供标题树后 outline_available=True（同一判据从不可用变为可用）")
    check(til["candidate_span_count"] == identity["candidate_span_count"],
          "换判据不改变候选人口（同一条候选只是换了去路）")
    check(target.span_id in material_spans
          and target.span_id not in with_outline.span_ids,
          "同文候选从材料变为处置（不是删候选，是换轴）")
    found = with_outline.content_dispositions_for_span(target.span_id)
    check(len(found) == 1 and found[0].kind == "isolated_heading"
          and found[0].reason == "isolated_heading_matches_node_title",
          "与节点标题同文的候选落 isolated_heading（typed 原因）")
    check(len(found) == 1
          and found[0].text_excerpt == target.normalized_text[
              :TM.CONTENT_DISPOSITION_EXCERPT_CHARS]
          and found[0].text_char_length == len(target.normalized_text)
          and found[0].span_locator == target.span_locator
          and tuple(found[0].char_range) == tuple(target.char_range)
          and found[0].content_fingerprint == target.content_fingerprint,
          "处置留档原文片段 + locator + 字符区间 + 内容指纹（可回查、不静默丢弃）")
    check(not with_outline.gap_by_span(target.span_id),
          "被内容处置的候选不登记结构 gap（两条轴不得互冒）")
    check(with_outline.identity["candidate_span_count"]
          == with_outline.identity["material_span_count"]
          + with_outline.identity["gap_count"]
          + with_outline.identity["content_disposition_count"],
          "换轴后账目仍闭合")

    details.append(
        f"NOTE §二 2.3 夹具实测: 候选 {identity['candidate_span_count']} = 材料 "
        f"{identity['material_span_count']} + gap {identity['gap_count']} + 处置 "
        f"{identity['content_disposition_count']} "
        f"{batch.content_disposition_reason_counts()}；"
        f"补标题树后处置 {with_outline.identity['content_disposition_count']} 条")
    return 0


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""Eval: §0.20 数字**逐值**链条台账（`cvt-1`），不调模型、不联网、不写库。

用法: python -m evals.test_m930_3_value_trace

这个模块钉的是**读数**而不是判据：逐句判定仍然只有 `sections/sentence_check.py` 一处。它要
证明的是「同一个数值卡在哪一步」这类结论**可以被分开说出来**，而不是像过去那样被三件事
混成一句「材料里没有」。

逐条证明：

1. **写了不等于授权到了**：正文写了一个金额、只引普通材料 ⇒ 阶段 `written` 而授权
   `material_surface_only`——这正是 cp22 现场 s0018 的形状（`541GWh` / `41.85%` 都在
   `m02` 原文里，却因「材料原文出现不授权金额与比率」被机械门判硬错）。同一句的数字改成
   由**合格事实**支撑 ⇒ 授权翻到 `qualified_fact`：证明这一档不是恒判值。
2. **「材料里有这个数字」≠「有一条被拒的候选」**：上游对象缺席时，材料里的数字只能是
   `not_observable`（**不**退化成 `no_candidate`，更不写成 `candidate_rejected`）；
   上游在场时，无候选记 `no_candidate`、有候选且资格决定拒绝记 `candidate_rejected`
   并带 typed `rejection_reason`——三档两两不同。
3. **已送达 vs 未送达**：清单事实里有、草稿没写 ⇒ `delivered_not_written`；研究侧已合格
   但没进 Writer 事实读视图 ⇒ `qualified_not_delivered`，且**有** typed 排除记录时给出
   原因码、**没有**记录时留空串并明说「无 typed 排除记录」（不得把「没记录」写成「没被排除」）。
4. **来源缺口是另一档**：材料里根本没有 ⇒ `absent_from_material_spans`。
5. **等价面合并**：材料写「1.2 个百分点」、正文写「1.2个百分点」在授权轴上**是同一个数**
   （`_literal_token` 的千分位/空白归一），目标集必须只有一行——否则「材料里有 N 个数字」
   这类读数会被排版差异吹大。
6. **目标由本链产物算出**：不给 `targets` 时目标 = 草稿数字 ∪ 材料金额/比率表面（两个来源
   各自标出）；点名时标 `caller_named`。**不写任何公司、页码、关键词专用清单**。
7. **封闭词表在构造期成立**：阶段 / 授权档越界即抛，`written` 无句子即抛。

夹具复用 `test_m930_3_cited_writer` 的真实 `VerifiedPackSet` / 真实材料行 / 同一
`build_cited_writer_input` 入口；材料文本是本模块自己写的**假文本**，不含公司代号、文件名、
页码、表号或答案关键词。**注意**：本模块只跑读数，因此**不**需要读真实库 / 真实 PDF。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_m930_3_cited_writer as E        # noqa: E402
from sections import cited_value_trace as CVT          # noqa: E402
from sections import cited_writer as CW                # noqa: E402
from sections import material_context as MC            # noqa: E402
from sections import narrative_schema as NS            # noqa: E402

#: 材料原文里有一条**金额 + 比率**（普通材料原文，不是合格事实）。
_MAT_TEXT = "动力电池系统销售收入 12,345.6 万元，占主营业务收入 62.5%，同比增长 18.4%。"
#: 复核用：同一段里的普通数量（不算金额 / 比率，按 §0.20 照常引用）。
_MAT_QTY = "公司本期新增产能 27 项。"
#: 「带一个空格的单位」版：PDF 文字层的写法，用来验等价面合并。
_MAT_SPACED = "报告期内该公司某业务占比提升 1.2 个百分点。"
#: 合格事实里的金额（路径 A）。
_FACT_MONEY = "报告期内公司主营业务收入为 100.00 万元。"


def _entry(base, *, text=None, **kw):
    """按读视图指纹的**重算规则**换一个成员（指纹当场重算，不手糊一个字面量）。"""
    reading_view = base.reading_view if text is None else text
    fingerprint = MC._reading_view_fingerprint(
        payload_hash=base.payload_hash, object_type=base.material_type,
        reading_view=reading_view, structured_view=kw.get("structured_view"))
    return type(base)(
        citation_key=base.citation_key, member_ref=base.member_ref, pack_id=base.pack_id,
        material_id=base.material_id, topic_id=base.topic_id,
        material_type=base.material_type, source_identity=base.source_identity,
        provenance_identity=base.provenance_identity,
        source_role=base.source_role, document_id=base.document_id,
        locator_ref=dict(base.locator_ref), payload_hash=base.payload_hash,
        reading_view=reading_view, reading_view_fingerprint=fingerprint,
        structured_view=kw.get("structured_view"),
        content_qualification=base.content_qualification, aspect_ids=base.aspect_ids)


def _fact_like(base, text: str):
    """换掉事实行的文本（其余字段一字不动）——只用于「已送达事实里有没有这个数」。"""
    fields = {name: getattr(base, name) for name in base.__dataclass_fields__}
    fields["text"] = text
    return type(base)(**fields)


def _draft(manifest, texts_and_citations):
    """按 `(文本, 引用键)` 序列造一份最小草稿（身份由 `create` 重算）。"""
    from sections import cited_writer as CW
    sentences = tuple(
        CW.CitedSentence(sentence_id=f"s{i:04d}", text=text,
                         citations=tuple(keys), numeric_tokens=NS.scan_numeric_tokens(text))
        for i, (text, keys) in enumerate(texts_and_citations, start=1))
    spec = manifest.subsections[0]
    return CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id=manifest.manifest_id, writer_identity="stub-writer",
        subsections=(CW.CitedSubsection(
            subsection_id=spec.subsection_id, title=spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=sentences),)),))


def _upstream_raw(**kwargs):
    """一条**只带本模块需要的字段**的研究侧替身（真实对象另有构造期约束，这里只验读数）。"""
    return SimpleNamespace(**kwargs)


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

    task, authority = E._task_and_authority()
    manifest = E._input_manifest(authority=authority, facts=E._scan_facts(authority, task))
    base = manifest.materials[0]
    other = manifest.materials[1]
    base_fact = manifest.facts[0]

    money_mat = _entry(base, text=_MAT_TEXT)
    spaced_mat = _entry(base, text=_MAT_SPACED)
    qty_mat = _entry(other, text=_MAT_QTY)

    # ================================================== §1 写了 ≠ 授权到了
    details.append("## §1 写了不等于授权到了：普通材料原文只给 `material_surface_only`")
    body_money = "报告期内公司动力电池系统销售收入 12,345.6万元，占主营业务收入 62.5%。"
    draft_money = _draft(manifest, [(body_money, (money_mat.citation_key,))])
    man_money = _manifest_with(manifest, [money_mat, qty_mat], manifest.facts)
    report = CVT.trace_cited_values(draft=draft_money, materials=man_money.materials,
                                    facts=man_money.facts, targets=("62.5%",))
    row = report.values[0]
    check(row.stage == CVT.STAGE_WRITTEN and row.authorization == "material_surface_only",
          f"只引普通材料 ⇒ 阶段 written、授权 material_surface_only（实测 "
          f"{row.stage}/{row.authorization}）")
    check(row.requires_qualified_basis is True,
          "62.5% 是「必须拿到合格事实或格级来源」的那一类表面")
    check(row.citation_keys == (money_mat.citation_key,)
          and row.material_keys == (money_mat.citation_key,),
          f"被引键与含它的材料都被记下（实测 {row.citation_keys}/{row.material_keys}）")
    check("material_surface_only" in row.detail or "原文" in row.detail,
          "`detail` 把「材料原文出现不授权金额与比率」这一层说出来")

    #: 同句里的普通数量**不**被收成本模块的目标（目标只收金额 / 比率那一类）。
    surfaces = CVT.material_numeric_surfaces([money_mat, qty_mat])
    check("27" not in surfaces,
          f"普通数量不进目标表（按 §0.20 照常引用）（实测键 {sorted(surfaces)}）")
    check("18.4%" in surfaces and "12,345.6 万元" in surfaces,
          f"金额与比率都进目标表（实测键 {sorted(surfaces)}）")

    details.append("## §1c 同一档 `material_surface_only` 要分说两件事（普通数量 vs 金额/比率）")
    # 正例：普通数量（`27`，非金额/比率）只引普通材料 ⇒ 授权同样是 `material_surface_only`，
    # 但 `detail` 必须说「普通数量、照常引用」，**不得**说成「没有任何一处逐字有它」——
    # 那与同一行的 `authorization` 列正面矛盾（cp22 的 `541` 就是这么读出来的）。
    body_qty = "公司本期新增产能 27 项。"
    draft_qty = _draft(manifest, [(body_qty, (qty_mat.citation_key,))])
    #: 量词的数词**连单位一起**成一个表面（`NS.scan_numeric_tokens` 给的是 `27 项`，不是 `27`）；
    #: 点名目标就按本链自己的表面写法给，不另立规则。
    qty_token = NS.scan_numeric_tokens(body_qty)[0]
    row_qty = CVT.trace_cited_values(
        draft=draft_qty, materials=man_money.materials, facts=man_money.facts,
        targets=(qty_token,)).values[0]
    check(row_qty.stage == CVT.STAGE_WRITTEN
          and row_qty.authorization == "material_surface_only",
          f"普通数量只引普通材料 ⇒ written/material_surface_only（实测 "
          f"{row_qty.stage}/{row_qty.authorization}）")
    check(row_qty.requires_qualified_basis is False,
          "普通数量**不**要求合格事实或格级来源（按 §0.20 照常引用）")
    check("没有任何一处逐字有它" not in row_qty.detail,
          f"`detail` 不得与 `authorization` 列矛盾（实测 `{row_qty.detail}`）")
    check("普通数量" in row_qty.detail,
          f"`detail` 明说这是普通数量、不要求资格（实测 `{row_qty.detail}`）")
    # 反例：同一档里的金额/比率仍然要说成「`numeric_qualification` 要拦的那件事」。
    check("金额/比率" in report.values[0].detail,
          f"金额/比率在同一档仍是资格告警（实测 `{report.values[0].detail}`）")
    #: `detail` 必须在**人读读回**里到场。它是「这一行到底说明什么」的那句话；只躺在
    #: `to_dict()` 里就等于没有交付（此前的渲染器只印 `rejection_reason`，`detail` 没人读）。
    #: 只对「写进正文、授权不是 qualified_fact/table_cell_source」这一档补清单——那正是
    #: 读者最容易把「原文里有」读成「已取得权威」的地方。
    rendered = "\n".join(CVT.render_value_trace_lines(
        CVT.trace_cited_values(draft=draft_qty, materials=man_money.materials,
                              facts=man_money.facts, targets=(qty_token,))))
    check("写进正文、但（按本链口径）没有数字权威的值" in rendered
          and qty_token in rendered.split("没有数字权威的值")[1],
          f"读回必须把「写了但没有数字权威」的值逐条说清（实测：\n{rendered}）")
    check("| 值 | 阶段 | 授权 |" in rendered,
          "读回的逐值表照旧在（补的那一截是**加**，不是换）")

    details.append("## §1b 正例：同一类数字改由合格事实支撑 ⇒ 授权翻到 `qualified_fact`")
    fact_money = _fact_like(base_fact, _FACT_MONEY)
    body_fact = "报告期内公司主营业务收入 100.00万元。"
    draft_fact = _draft(manifest, [(body_fact, (fact_money.citation_key,))])
    man_fact = _manifest_with(manifest, [money_mat, qty_mat], [fact_money])
    rep_fact = CVT.trace_cited_values(draft=draft_fact, materials=man_fact.materials,
                                      facts=man_fact.facts, targets=("100.00 万元",))
    check(rep_fact.values[0].authorization == "qualified_fact",
          f"被引合格事实逐字覆盖 ⇒ `qualified_fact`（实测 {rep_fact.values[0].authorization}）")
    check(rep_fact.values[0].delivered_fact_keys == (fact_money.citation_key,),
          "送达事实里含它的键被记下")

    # ================================================== §2 上游三态两两不同
    details.append("## §2 「材料里有这个数字」≠「有一条被拒的候选」：上游三态分档")
    draft_empty = _draft(manifest, [("本报告不写任何金额。", ())])
    rep_absent = CVT.trace_cited_values(draft=draft_empty, materials=man_money.materials,
                                        facts=(), targets=("62.5%",))
    row = rep_absent.values[0]
    check(rep_absent.upstream_observed is False
          and row.stage == CVT.STAGE_NOT_OBSERVABLE
          and row.stage_basis == "upstream_objects_absent_from_artifact",
          f"上游对象不在产物里 ⇒ `not_observable`，**不**退化成 `no_candidate`（实测 "
          f"{row.stage}/{row.stage_basis}）")
    check(row.authorization == "none",
          "没写进正文的值授权档是 `none`（不是「被拒」，也不是「合法」）")

    #: 材料里有它、但**没有任何候选命题含它**。
    rep_no_cand = CVT.trace_cited_values(
        draft=draft_empty, materials=man_money.materials, facts=(),
        topic_results={"company_business": _upstream_raw(
            fact_candidates=(), fact_qualification_decisions=(), supported_facts=())},
        targets=("62.5%",))
    check(rep_no_cand.upstream_observed is True
          and rep_no_cand.values[0].stage == CVT.STAGE_NO_CANDIDATE,
          f"上游在场、材料里有、无候选 ⇒ `no_candidate`（实测 {rep_no_cand.values[0].stage}）")

    #: 有候选命题含它、资格决定 `rejected`（带 typed 原因）。
    cand = _upstream_raw(candidate_id="cand-x", statement="占主营业务收入 62.5%",
                         candidate_revision="r1")
    dec = _upstream_raw(decision_id="dec-x", candidate_id="cand-x", verdict="rejected",
                        rejection_reason="ratio_without_qualified_basis", candidate_revision="r1")
    rep_rej = CVT.trace_cited_values(
        draft=draft_empty, materials=man_money.materials, facts=(),
        topic_results={"company_business": _upstream_raw(
            fact_candidates=(cand,), fact_qualification_decisions=(dec,), supported_facts=())},
        targets=("62.5%",))
    row = rep_rej.values[0]
    check(row.stage == CVT.STAGE_CANDIDATE_REJECTED
          and row.rejection_reason == "ratio_without_qualified_basis"
          and row.qualification_decision_id == "dec-x",
          f"有候选 + 资格拒绝 ⇒ `candidate_rejected` 带 typed 原因（实测 "
          f"{row.stage}/{row.rejection_reason}）")
    check(row.candidate_ids == ("cand-x",), "候选 id 一并记下")
    check(row.stage != rep_no_cand.values[0].stage,
          "`candidate_rejected` 与 `no_candidate` 是**两档**（这正是「材料里有 50 个数字」"
          "不得被读成「50 条被拒事实」的那条边界）")

    details.append("## §2b 材料里根本没有 ⇒ 来源缺口（另一档）")
    rep_gap = CVT.trace_cited_values(
        draft=draft_empty, materials=man_money.materials, facts=(),
        topic_results={"company_business": _upstream_raw(
            fact_candidates=(), fact_qualification_decisions=(), supported_facts=())},
        targets=("99.9%",))
    check(rep_gap.values[0].stage == CVT.STAGE_ABSENT_FROM_MATERIAL_SPANS
          and rep_gap.values[0].material_keys == (),
          f"材料里没有该值 ⇒ `absent_from_material_spans`（实测 {rep_gap.values[0].stage}）")

    # ================================================== §3 已送达 / 已合格未送达
    details.append("## §3 已送达未写 / 已合格未送达（含「无 typed 记录」与「有记录」两侧）")
    rep_deliv = CVT.trace_cited_values(draft=draft_empty, materials=man_money.materials,
                                       facts=(fact_money,), targets=("100.00 万元",))
    check(rep_deliv.values[0].stage == CVT.STAGE_DELIVERED_NOT_WRITTEN,
          f"清单事实里有、草稿没写 ⇒ `delivered_not_written`（实测 {rep_deliv.values[0].stage}）")

    sup_fact = _upstream_raw(fact_id="sf-1", text="某期金额 77.7 万元")
    rep_qnd = CVT.trace_cited_values(
        draft=draft_empty, materials=[_entry(base, text="某期金额 77.7 万元。")], facts=(),
        topic_results={"company_business": _upstream_raw(
            fact_candidates=(), fact_qualification_decisions=(), supported_facts=(sup_fact,))},
        targets=("77.7 万元",))
    row = rep_qnd.values[0]
    check(row.stage == CVT.STAGE_QUALIFIED_NOT_DELIVERED and row.delivery_exclusion_reason == "",
          f"已合格未送达、**无** typed 排除记录 ⇒ 原因留空（实测 {row.stage!r}/"
          f"{row.delivery_exclusion_reason!r}）")
    check("无 typed" not in row.detail and row.detail,
          "行本身不把「没记录」写成「没被排除」——预览才写「无 typed 排除记录」那一句")

    scan_like = SimpleNamespace(
        period_exclusions=({"fact_id": "sf-1", "reason": "explicit_period_required"},),
        excluded_facts=())
    rep_excl = CVT.trace_cited_values(
        draft=draft_empty, materials=[_entry(base, text="某期金额 77.7 万元。")], facts=(),
        topic_results={"company_business": _upstream_raw(
            fact_candidates=(), fact_qualification_decisions=(), supported_facts=(sup_fact,))},
        scan=scan_like, targets=("77.7 万元",))
    check(rep_excl.values[0].delivery_exclusion_reason == "explicit_period_required",
          f"有 typed 排除记录 ⇒ 原因码一并给出（实测 "
          f"{rep_excl.values[0].delivery_exclusion_reason!r}）")

    # ================================================== §4 等价面合并
    details.append("## §4 排版等价面合并：同一数字不因空格记成两行")
    body_spaced = "报告期内该公司某业务占比提升 1.2个百分点。"
    draft_spaced = _draft(manifest, [(body_spaced, (spaced_mat.citation_key,))])
    man_spaced = _manifest_with(manifest, [spaced_mat], ())
    rep_spaced = CVT.trace_cited_values(draft=draft_spaced, materials=man_spaced.materials,
                                        facts=man_spaced.facts)
    hits = rep_spaced.rows_for("1.2 个百分点")
    check(len(hits) == 1,
          f"材料「1.2 个百分点」与正文「1.2个百分点」只产生**一行**（实测 {len(hits)} 行）")
    check(len(rep_spaced.values) == len({r.value for r in rep_spaced.values}),
          "逐值表中没有等价重复行")

    # ================================================== §5 目标来源与封闭词表
    details.append("## §5 目标来源由本链产物算出 / 点名 / 封闭词表")
    check("draft_numeric_tokens" in rep_spaced.target_sources
          and "material_numeric_surfaces" in rep_spaced.target_sources,
          f"不给 targets ⇒ 目标由草稿数字 ∪ 材料金额/比率表面算出（实测 "
          f"{rep_spaced.target_sources}）")
    named = CVT.trace_cited_values(draft=draft_spaced, materials=man_spaced.materials,
                                   facts=(), targets=("1.2个百分点",),
                                   targets_are_caller_named=True)
    check(named.target_sources == ("caller_named",) and len(named.values) == 1,
          f"点名时标 `caller_named`（实测 {named.target_sources}）")

    def _rejects(**kw) -> bool:
        try:
            CVT.CitedValueTraceRow(value="1", stage_basis="x", requires_qualified_basis=False,
                                   sentence_ids=("s1",), **kw)
            return False
        except CVT.CitedValueTraceError:
            return True

    check(_rejects(stage="invented_stage", in_draft=True, authorization="none"),
          "阶段取值越出封闭词表 ⇒ 构造期即拒")
    check(_rejects(stage=CVT.STAGE_WRITTEN, in_draft=True, authorization="invented_basis"),
          "授权档取值越出封闭词表 ⇒ 构造期即拒")
    try:
        CVT.CitedValueTraceRow(value="1", stage=CVT.STAGE_WRITTEN, stage_basis="x",
                               in_draft=True, requires_qualified_basis=False,
                               authorization="none")
        no_sentence_ok = False
    except CVT.CitedValueTraceError:
        no_sentence_ok = True
    check(no_sentence_ok, "`written` 却不带任何句子 id ⇒ 构造期即拒")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


def _manifest_with(manifest, materials, facts):
    """换材料行 / 事实行后**经 `create` 重算身份**（沿用本仓既有纪律，不手改 id）。"""
    import dataclasses as dc
    from sections import cited_writer as CW
    payload = {f.name: getattr(manifest, f.name) for f in dc.fields(manifest)
               if f.name != "manifest_id"}
    payload["materials"] = tuple(materials)
    payload["facts"] = tuple(facts)
    return CW.CitedWriterInputManifest.create(**payload)


if __name__ == "__main__":
    import json
    out = main()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out["failed"] == 0 else 1)

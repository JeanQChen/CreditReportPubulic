"""Eval: §0.20 第二步（前半）—— 逐句机械底线核对（`sc-1` / `scp-1`），不调模型。

用法: python -m evals.test_m930_3_sentence_check

本模块钉住**判据本身**，不钉措辞、不钉版本字面量。逐条证明：

1. **干净句全轴通过**——不是「没有记录」，而是每个轴各自给出 `pass`（轴在场才可能失败）。
2. **一处错误只标记那一句**：同段另一句照旧通过，报告不把整节清零。
3. **数字 / 否定 / 时点 / 主体四轴各自成立**：伪造的数字、伪造的否定、说不出来源的时点、
   伪造的主体，各自在**自己那一轴**上硬错误，且四轴失败集合的并集与仓内既有原语
   `NS.unauthorized_surfaces` 的方向一致（防止本模块另立一套口径）。
4. **两套授权池**：报告框架（小节标题 + Contract 逐字要求文本）能授权时点与主体，
   **不**能授权数字与否定。
5. **旧材料不得写成当前状态**：同一句话，来源角色是历史来源时硬错误；补上期间限定后通过。
6. **勾选 / 模板文字不得冒充公司事实**：只引非叙述形态材料时硬错误；同时引一条合格事实则不响。
7. **表数字必须逐格可核**：压平正文里有数字但没有「格」⇒ 硬错误（现场通道的真实形状）；
   有格但句子没写业务行 ⇒ 硬错误；行、列、格级 locator、单位/期间/口径声明齐全 ⇒ **通过**
   （证明这道门不是恒假门），且该数字的来源类型记为 `table_cell_source`——它**不**因此取得
   任何权威身份。
8. **机械核对不得宣称语义已被支持**：构造期强制 `semantic_support_claimed=False`、
   `publishability=not_publishable`，两者都改不动。
9. **金额 / 比率的资格另有更严的一道门（`scp-2`）**：同一句金额，只引普通材料时在**资格轴**
   （`numeric_qualification`）上硬错误——即使它在被引材料原文里**逐字都在**；改成由合格事实
   支撑、或落到所引表材料的一格，则通过。反向对照同批给出：不含单位的普通数量
   （「3 项」）与裸缩放词的非金额写法（「10 万台」）**不**被这道门误伤——§0.20 明确允许
   普通经营描述照常引用材料。
10. **需求侧用**同一份**判据**：离线抽取式替身自己撤下金额 / 比率句，逐条记账为
   「原表已展示、正文数字尚未授权」；同一份材料里的普通描述照旧写出，候选句全是金额的
   小节给 typed 缺口 `source_present_but_not_admissible`——**不**被写成「材料里没有」。
11. **栏目归属轴（`scp-3`）只在有这根轴时判**：材料 / 事实登记了栏目、小节声明了栏目时，
    引用另一栏的材料 ⇒ 硬错误 `sentence_aspect_not_registered`，该句照旧可读但**不计入**
    本栏目覆盖；一次输入里**没有任何**来源被登记到栏目时（财务 / 附注 / 外部快照那一支），
    本轴记「不适用」而**不**判成「全错栏」。
12. **「不适用」不是一种结论**（`sc-4` 的 `applicable`）：两个读数（未计入覆盖 / 已判过并
    通过）在「本轴不适用」那一支上**同时为空**，第三种状态
    `sentences_with_aspect_axis_not_applicable()` 把原因单独说出来——拿空集当「栏目全对上」
    就是把同一个假门装回去。`applicable=False` 只与 `pass` 同时出现（构造期强制）。
13. **替身撤下的另两条理由**各给一行正反例：表单控件字形（`☑适用 □不适用` 前缀）⇒ 整片
    撤下，去掉字形则照旧写出；只由历史来源支撑且自身无期间限定 ⇒ 整片撤下，句子自己锚到
    年份则**不**撤（判的是措辞的期间性，不是来源的「新」）。
14. **切分与去重各一对正反例**（此前无任何聚焦回归）：以 `；` 结尾的列举项与无句末标点的
    版式行即使整段长于下限也切不出句子——同段里紧随其后的真句子照旧逐字写出；切空时给
    typed 缺口 `manifest_partial_for_requirement` 而**不**说「材料里没有」。去重判的是
    **字面互相包含**（长包短、短是长的尾巴都算），两件措辞接近但不同的业务事实**不**判重复。

夹具复用 `test_m930_3_cited_writer` 的真实 `VerifiedPackSet` / 真实 `ResearchMaterial` / 同一
`build_cited_writer_input` 入口；无公司代号、文件名、页码、表号或固定年份的生产字面量。
不调 LLM、不联网、不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_m930_3_cited_writer as E        # noqa: E402
from scripts import run_m930_3_cited_chain as RUN      # noqa: E402
from sections import cited_writer as CW                # noqa: E402
from sections import material_context as MC            # noqa: E402
from sections import narrative_schema as NS            # noqa: E402
from sections import sentence_check as SC              # noqa: E402

_MAT_A = E._MAT_A
_BODY_A = E._BODY_A
_BODY_B = E._BODY_B
_FACT_TEXT = E._FACT_TEXT
_REQ_TEXT = E._REQ_TEXT


def _entries(manifest):
    return dict(manifest.__dict__)


#: 「不传」的哨兵（见 :func:`_manifest`）：`None` 是一个**有意义**的取值，不能兼任缺省。
_UNSET = object()


def _sentence(sentence_id: str, text: str, citations=()) -> CW.CitedSentence:
    return CW.CitedSentence(sentence_id=sentence_id, text=text,
                            citations=tuple(citations),
                            numeric_tokens=NS.scan_numeric_tokens(text))


def _entry(base, *, text=None, structured_view=None, content_qualification=None,
           source_role=None, document_id=None, aspect_ids=None):
    """按读视图指纹的**重算规则**换一个成员（指纹当场重算，不手糊一个字面量）。"""
    reading_view = base.reading_view if text is None else text
    fingerprint = MC._reading_view_fingerprint(
        payload_hash=base.payload_hash, object_type=base.material_type,
        reading_view=reading_view, structured_view=structured_view)
    return type(base)(
        citation_key=base.citation_key, member_ref=base.member_ref, pack_id=base.pack_id,
        material_id=base.material_id, topic_id=base.topic_id,
        material_type=base.material_type, source_identity=base.source_identity,
        provenance_identity=base.provenance_identity,
        source_role=base.source_role if source_role is None else source_role,
        document_id=base.document_id if document_id is None else document_id,
        locator_ref=dict(base.locator_ref), payload_hash=base.payload_hash,
        reading_view=reading_view, reading_view_fingerprint=fingerprint,
        structured_view=structured_view, content_qualification=content_qualification,
        aspect_ids=(base.aspect_ids if aspect_ids is None else tuple(aspect_ids)))


def _manifest(manifest, materials=None, facts=None, subsections=None,
              presentation_routing=_UNSET):
    """换材料行 / 事实行 / 小节行后**经 `create` 重算身份**（沿用 `E._rebuild` 的同一条纪律）。

    本模块比 `E._rebuild` 多两根轴：这里要改的是**登记归属**，而事实行也在归属轴的取值域
    里，小节行上的 `declared_aspect_ids` 更是这条轴的**另一端**，所以三样都要能整批替换。
    身份仍由 `create` 按内容重算，不手改 `manifest_id`。

    `presentation_routing` 用哨兵默认值：**不传**= 原样保留（`...` 会被 `create` 正确读成
    `None`，那样「不传」与「显式传 None」就分不开，本模块需要后者来测「没有这条轴」）。
    """
    import dataclasses as dc
    payload = {f.name: getattr(manifest, f.name) for f in dc.fields(manifest)
               if f.name != "manifest_id"}
    if materials is not None:
        payload["materials"] = tuple(materials)
    if facts is not None:
        payload["facts"] = tuple(facts)
    if subsections is not None:
        payload["subsections"] = tuple(subsections)
    if presentation_routing is not _UNSET:
        payload["presentation_routing"] = presentation_routing
    return CW.CitedWriterInputManifest.create(**payload)


def _subsections_with(manifest, declared_aspect_ids):
    """把每个小节的**声明栏目集合**换成给定值（其余字段一字不动，只改这一根轴）。

    取值传 `str` 时按**单元素集合**处理——那是「这个陈述会只覆盖一栏」的简写，不是
    「集合只有一个合法取值」。`cwm-6` 起没有单数版字段，所以这里也不做「取首元」的兼容。
    """
    wanted = ((declared_aspect_ids,) if isinstance(declared_aspect_ids, str)
              else tuple(declared_aspect_ids))
    return tuple(
        CW.CitedSubsectionSpec(subsection_id=s.subsection_id, title=s.title,
                               requirement_text=s.requirement_text,
                               declared_aspect_ids=wanted)
        for s in manifest.subsections)


def _facts_with(facts, aspect_ids):
    """把若干事实行的**登记归属**换成给定值（其余字段一字不动，只改这一根轴）。"""
    out = []
    for fact in facts:
        fields = {name: getattr(fact, name) for name in fact.__dataclass_fields__}
        fields["aspect_ids"] = tuple(aspect_ids)
        out.append(type(fact)(**fields))
    return out


def _table_cell(*, row: int, column: int, row_label: str, header: str, value: str) -> dict:
    return {"row_index": row, "column_index": column, "row_label": row_label,
            "column_header": header, "value_text": value,
            "cell_locator": NS.table_cell_locator(
                "evidence_document:doc-demo-1@v1#table", table_ref="table-ref-1",
                row_index=row, column_index=column)}


def _verdicts(report) -> dict:
    return {state.sentence_id: state.verdict for state in report.sentence_states()}


def _reasons(report, sentence_id: str) -> tuple:
    return tuple(r.failure_reason for r in report.records_for(sentence_id)
                 if r.verdict == "hard_error")


def _reasons_for_kind(records, check_kind: str) -> tuple:
    return tuple(r.failure_reason for r in records
                 if r.check_kind == check_kind and r.verdict == "hard_error")


def _kinds(records) -> set:
    return {r.check_kind for r in records}


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
    context = E.T._writer_material_context(authority.pack_set, task_id=str(task.task_id),
                                           section_id="company")
    facts = E._scan_facts(authority, task)
    manifest = E._input_manifest(authority=authority, facts=facts)
    base = manifest.materials[0]
    subsection_id = manifest.subsections[0].subsection_id

    def report_with(man, sentences):
        """整节入口：声明栏目由**清单里的小节行**给出（本模块不在这里另传一个栏目）。"""
        spec = man.subsections[0]
        draft = CW.CitedProseDraft.create(
            task_id=man.task_id, section_id=man.section_id,
            input_manifest_id=man.manifest_id, writer_identity="stub-writer",
            subsections=(CW.CitedSubsection(
                subsection_id=spec.subsection_id, title=spec.title,
                paragraphs=(CW.CitedParagraph(paragraph_id="p1",
                                              sentences=tuple(sentences)),)),))
        return SC.check_cited_prose(draft=draft, manifest=man)

    def report_for(sentences):
        return report_with(manifest, sentences)

    # ============================================================ §1 干净句
    details.append("## §1 干净句：每个轴都**在场且通过**")
    clean = _sentence("s1", _BODY_A, ("m01",))
    records = SC.check_sentence(sentence=clean, subsection_id=subsection_id,
                                paragraph_id="p1", manifest=manifest)
    check(_kinds(records) == set(SC.CHECK_KINDS),
          f"每个轴各产一条记录（实测 {len(_kinds(records))} 个轴 / {len(SC.CHECK_KINDS)}）")
    check(all(r.verdict == "pass" for r in records),
          "全部通过（来源就是它自己那一行的原文）")
    check(any(r.applicable for r in records) and any(not r.applicable for r in records),
          "`pass` 里**两类都在场**：这一句确实判过的那些轴，与这一句没有它要判的东西、"
          "因此记「不适用」的那些轴（本句没有表数字 ⇒ 表格那几轴不适用）——"
          "两者 verdict 都是 `pass`，含义却相反，只能靠 `applicable` 分开")
    check(not SC._uncovered(NS.high_risk_surface_tokens(clean.text),
                            [base.reading_view]),
          "与仓内既有原语对账：`NS.unauthorized_surfaces` 对同一来源池也判「无未授权表面」")

    # ============================================================ §2 一句错不清零
    details.append("## §2 一处错误不得清零整节")
    report = report_for([_sentence("s1", "公司主营业务收入为 888.88 万元。", ("m01",)),
                         _sentence("s2", _BODY_B, ("m02",))])
    check(_verdicts(report) == {"s1": "hard_error", "s2": "pass"},
          f"只有出问题的那一句被标记（实测 {_verdicts(report)}）")
    check(report.blocked_sentence_ids == ("s1",),
          "封锁列表逐句给出，另一句照旧可读")
    check(report.mechanical_verdict == "has_hard_errors",
          "整节结论如实为「有硬错误」——它既不冒充可发布，也不把整节判成不可读")

    # ============================================================ §3 四轴
    details.append("## §3 数字 / 否定 / 时点 / 主体：各管各的轴")

    # 3a 数字：伪造一个来源里没有的数字
    fake_num = SC.check_sentence(
        sentence=_sentence("s1", "公司主营业务收入为 888.88 万元。", ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=manifest)
    check(_reasons_for_kind(fake_num, "numeric_surface") == ("unsourced_number_surface",),
          "伪造的数字落在**数字轴**上（`unsourced_number_surface`）")
    check("888.88 万元" in fake_num[[r.check_kind for r in fake_num].index(
        "numeric_surface")].surfaces,
          "记录里逐字给出是哪一个表面没有来源（数字 token 自带单位，单位也必须被授权）")

    # 3b 数字有来源：来自**权威事实**（路径 A）
    fact_num = SC.check_sentence(
        sentence=_sentence("s1", _FACT_TEXT, ("f01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=manifest)
    numeric_record = [r for r in fact_num if r.check_kind == "numeric_surface"][0]
    check(numeric_record.verdict == "pass" and numeric_record.numeric_bases == ("qualified_fact",),
          f"事实文本里的数字在数字轴上通过，来源类型记为 qualified_fact"
          f"（实测 {numeric_record.numeric_bases}）")

    # 3c 否定：材料里没有的否定不许写
    fake_neg = SC.check_sentence(
        sentence=_sentence("s1", "公司主营业务不存在重大变化。", ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=manifest)
    check(_reasons_for_kind(fake_neg, "negation_surface") == ("unsourced_negation_surface",),
          "没有来源的否定落在**否定轴**上")
    # 反向：材料原文里逐字有否定 ⇒ 通过
    negation_body = "公司报告期内不存在重大变化。"
    neg_manifest = _manifest(manifest, [_entry(base, text=negation_body),
                                        manifest.materials[1]])
    neg_ok = SC.check_sentence(sentence=_sentence("s1", negation_body, ("m01",)),
                               subsection_id=subsection_id, paragraph_id="p1",
                               manifest=neg_manifest)
    check(_reasons_for_kind(neg_ok, "negation_surface") == (),
          "来源原文里逐字有这条否定 ⇒ 通过（不是「见到否定就拒」）")

    # 3d 时点：报告框架能授权「报告期」，**不**能授权一个具体年份
    period_ok = SC.check_sentence(
        sentence=_sentence("s1", "报告期内公司主营业务由动力电池系统与储能电池系统构成。",
                           ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=manifest)
    check(_reasons_for_kind(period_ok, "period_surface") == (),
          "「报告期」由 Contract 逐字要求文本授权（报告框架那一套池），时点轴通过")
    # 报告框架之外的措辞（本节不在这份清单的小节表里）⇒ 时点轴自己报出来
    outside = SC.check_sentence(
        sentence=_sentence("s1", "报告期内公司主营业务由动力电池系统与储能电池系统构成。",
                           ("m01",)),
        subsection_id="sub-not-in-manifest", paragraph_id="p1", manifest=manifest)
    check(_reasons_for_kind(outside, "period_surface") == ("unsourced_period_surface",),
          "本节不在清单的小节表里 ⇒ 报告框架那一套池不适用，时点轴自己报出来")
    period_bad = SC.check_sentence(
        sentence=_sentence("s1", "2024年公司主营业务由动力电池系统构成。", ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=manifest)
    check(_reasons_for_kind(period_bad, "period_surface") == ()
          and _reasons_for_kind(period_bad, "numeric_surface") == ("unsourced_number_surface",),
          "绝对年份不是「含糊期间」而是**数字表面**（`2024年` 是数字 token）⇒ "
          "在数字轴上报出来，时点轴不重复报（分轴不等于两处都判）")

    # 3e 主体：伪造一个来源里没有的法人主体名
    subject_text = "某某新能源有限公司为公司的控股子公司。"
    subject_surfaces = NS.entity_name_tokens(subject_text)
    check(bool(subject_surfaces),
          f"夹具文本确实含法人主体名 token（实测 {subject_surfaces}）——否则本轴测不出东西")
    subject_bad = SC.check_sentence(
        sentence=_sentence("s1", subject_text, ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=manifest)
    check(_reasons_for_kind(subject_bad, "subject_surface") == ("unsourced_subject_surface",),
          "伪造的主体落在**主体轴**上")

    # 3f 与仓内既有原语对账：既有原语打出的表面，本模块至少有一轴报出来
    pool = [fact.text for fact in facts] + [base.reading_view]
    aggregate = NS.unauthorized_surfaces(subject_text, pool)
    mine = set()
    for record in subject_bad:
        mine |= set(record.surfaces)
    check(bool(aggregate) and set(aggregate) <= mine,
          f"`NS.unauthorized_surfaces` 判出的表面都被本模块的分轴记录覆盖"
          f"（既有 {sorted(aggregate)} ⊆ 本模块 {sorted(mine)}）")

    # ============================================================ §4 旧来源当前化
    details.append("## §4 旧材料不得被写成当前状态")
    # 只留一条材料：同一份文档在同一清单里出现两个角色本身就是 fail-closed 的（见下）
    stale_manifest = _manifest(manifest, [
        _entry(base, source_role="history_and_conflict_source")])
    stale = SC.check_sentence(sentence=_sentence("s1", _BODY_A, ("m01",)),
                              subsection_id=subsection_id, paragraph_id="p1",
                              manifest=stale_manifest)
    check(_reasons_for_kind(stale, "current_state_scope") == ("history_material_as_current_state",),
          "历史来源 + 无期间限定 ⇒ 硬错误（读者会把它读成持续至今的当前状态）")
    # `srsc-4`（`scp-7`）收紧：**相对**期间限定词配较旧来源**不再**豁免。``报告期`` 是文档
    # 自述的那个期间，它不携带年份——读者要读出「这是哪一年」，只能另配那份**较旧**文档自己
    # 的期间，前提恰好不成立。真实 cp-21 里 `s0028`「截至报告期末，公司已实现动力电池累计
    # 装车超1,700万辆」引 2024 年报，正是这条要挡的形状。
    stale_relative = SC.check_sentence(
        sentence=_sentence("s1", "报告期内" + _BODY_A[2:], ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=stale_manifest)
    check(_reasons_for_kind(stale_relative, "current_state_scope")
          == ("history_material_as_current_state",),
          "**相对**期间限定词 + 只由较旧来源支撑 ⇒ 照旧硬错误（相对词不携带年份，"
          "配旧文档读出来的仍是当前态）")
    # 反例方向：把断言自己锚到**绝对年份**，读者不需要那份文档的期间也能读出口径 ⇒ 通过。
    # 判的是措辞的**期间性**，不是来源的「新」。
    stale_absolute = SC.check_sentence(
        sentence=_sentence("s1", "2024年" + _BODY_A[2:], ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=stale_manifest)
    check(_reasons_for_kind(stale_absolute, "current_state_scope") == (),
          "同一句话补上**绝对年份** ⇒ 通过（判的是措辞的期间性，不是来源的「新」）")
    # 角色读不到 ⇒ 不猜，且本轴不另做一处结论
    #（有文档身份、台账里却查不到角色：这正是 `_material_source_roles` 留空的那种输入）
    orphan = _manifest(manifest, [_entry(base, document_id="doc-unknown", source_role="")])
    orphan_records = SC.check_sentence(sentence=_sentence("s1", _BODY_A, ("m01",)),
                                       subsection_id=subsection_id, paragraph_id="p1",
                                       manifest=orphan)
    check(_reasons_for_kind(orphan_records, "source_role") == ("source_role_unreadable",)
          and _reasons_for_kind(orphan_records, "current_state_scope") == (),
          "来源角色读不到 ⇒ `source_role_unreadable`，当前态那一轴**不**另做一处结论")
    # 同一份文档两个角色 ⇒ 按哪一份都是猜，当场拒（本模块不自造第二个真值）
    conflicted = _manifest(manifest, [_entry(base),
                                      _entry(manifest.materials[1],
                                             source_role="history_and_conflict_source")])
    try:
        SC.document_roles(conflicted)
    except SC.SentenceCheckError as exc:
        check("角色不一致" in str(exc),
              "同一 document_id 两个角色 ⇒ fail-closed（与 `srsc-1` 台账同一判据）")
    else:
        check(False, "同一 document_id 两个角色本应 fail-closed")

    # ============================================================ §5 模板文字
    details.append("## §5 勾选 / 模板文字不得冒充公司事实")
    form_manifest = _manifest(manifest, [
        _entry(base, content_qualification={
            "kind": "selection_form", "is_material": True,
            "selection": {"question": "是否属于主营业务", "selected": ["是"]}}),
        manifest.materials[1]])
    form_only = SC.check_sentence(sentence=_sentence("s1", _BODY_A, ("m01",)),
                                  subsection_id=subsection_id, paragraph_id="p1",
                                  manifest=form_manifest)
    check(_reasons_for_kind(form_only, "template_text") == ("template_text_as_company_fact",),
          "只引勾选表单行 ⇒ 硬错误（它逐字在原文里，也不作数）")
    form_with_fact = SC.check_sentence(sentence=_sentence("s1", _BODY_A, ("m01", "f01")),
                                       subsection_id=subsection_id, paragraph_id="p1",
                                       manifest=form_manifest)
    check(_reasons_for_kind(form_with_fact, "template_text") == (),
          "同句另有一条合格事实 ⇒ 本轴不响（这是引用卫生，不是硬错误）")

    # --- `scp-7` 第二条路径：勾选块 + 通篇无句末标点 -------------------------
    # 真实形状取自 cp-21 清单里的 `m18`（年报勾选行带一条长尾串，分类器按尾串长度退回
    # `text`）。这一组正反例逐字用那一份材料的内容，证明判据认得住的**只有**这一个形状。
    _pua = chr(0xF052)   # 年报里的勾选字形；按**码位**构造，不在源码里写私用字符
    form_tail_text = (
        f"{_pua}适用 □不适用 公司需遵守《深圳证券交易所上市公司自律监管指引第4 号——"
        "创业板行业信息披露》中的“锂离子电池产业链相关业务” 的披露要求 "
        "1）营业收入及营业成本整体情况")
    tail_manifest = _manifest(manifest, [
        _entry(base, text=form_tail_text,
               content_qualification={"is_material": True, "kind": "text"}),
        manifest.materials[1]])
    tail_sentence = f"公司需遵守《深圳证券交易所上市公司自律监管指引第4号——创业板行业信息披露》中的“锂离子电池产业链相关业务”的披露要求。"
    tail_quote = SC.check_sentence(sentence=_sentence("s1", tail_sentence, ("m01",)),
                                   subsection_id=subsection_id, paragraph_id="p1",
                                   manifest=tail_manifest)
    check(_reasons_for_kind(tail_quote, "template_text") == ("template_text_as_company_fact",),
          "**正例（硬错）**：所引材料通篇是一行披露模板（勾选块 + 无句末标点），"
          "即使它的 `content_kind` 是 `text` ⇒ 本轴照旧报出"
          "（真实 cp-21 的 `s0026` 正是这一形状）")
    # 反例方向甲：同一条勾选前缀，后面**真的**接着可读正文（有句末标点）⇒ 不响。
    # 真实形状取自 `m16`（`☑适用 □不适用 报告期内，公司销售境外的主要产品为电池系统…。`）。
    prose_after_form = (
        f"{_pua}适用 □不适用 报告期内，公司销售境外的主要产品为电池系统，"
        "较上年同期相比未发生明显变化。公司境外收入占本期营业收入的比例保持稳定。")
    prose_manifest = _manifest(manifest, [
        _entry(base, text=prose_after_form,
               content_qualification={"is_material": True, "kind": "text"}),
        manifest.materials[1]])
    prose_check = SC.check_sentence(
        sentence=_sentence("s1", "公司销售境外的主要产品为电池系统。", ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=prose_manifest)
    check(_reasons_for_kind(prose_check, "template_text") == (),
          "**反例（通过）**：同一条勾选前缀后面接着**可读正文**（材料里有句末标点）⇒ "
          "本轴不响——判的是「这份材料有没有正文」，不是「有没有出现勾选字形」")
    # 反例方向乙：通篇无句末标点，但**没有**「适用 / 不适用」勾选块 ⇒ 不响（版式碎片不是表单行）
    fragment_only = _manifest(manifest, [
        _entry(base, text="2025 年，公司投资活动产生的现金流量净额较上年减少 456 亿元，"
                          "下降 93.30%，主要是购买",
               content_qualification={"is_material": True, "kind": "text"}),
        manifest.materials[1]])
    fragment_check = SC.check_sentence(
        sentence=_sentence("s1", "公司投资活动产生的现金流量净额较上年减少。", ("m01",)),
        subsection_id=subsection_id, paragraph_id="p1", manifest=fragment_only)
    check(_reasons_for_kind(fragment_check, "template_text") == (),
          "**反例（通过）**：通篇无句末标点但**没有**勾选块（半截叙述正文）⇒ 本轴不响"
          "（真实 cp-21 的 `m15` 正是这一形状，两条件缺一即不命中）")

    # ============================================================ §6 表数字逐格可核
    details.append("## §6 表数字：必须逐格可核（现场通道的真实形状也在这里）")
    table_number_text = "公司营业收入本期发生额为 100.00 万元。"

    # 6a 现场形状：表材料有整行文本、**没有格**
    flat_manifest = _manifest(manifest, [
        _entry(base, structured_view={"body_row_texts": ["营业收入 100.00"]}),
        manifest.materials[1]])
    flat = SC.check_sentence(sentence=_sentence("s1", table_number_text, ("m01",)),
                             subsection_id=subsection_id, paragraph_id="p1",
                             manifest=flat_manifest)
    check(_reasons_for_kind(flat, "table_cell_provenance")
          == ("table_cell_provenance_unavailable",),
          "压平正文里有数字、表里没有格 ⇒ `table_cell_provenance_unavailable`")
    check(_reasons_for_kind(flat, "numeric_surface") == ("unsourced_number_surface",),
          "同一个数字在数字轴上也无来源（表材料**不**进普通来源池，不得自证合法）")
    flat_problems = SC.table_cells({"body_row_texts": ["营业收入 100.00"]})
    check(flat_problems[0] == () and flat_problems[1] == ("no_cells",),
          "格提取器如实报出「没有格」而不是静默给出空集")

    # 6b 有格但缺某几项 ⇒ 该格不合格（不是「部分可用」）
    cell = _table_cell(row=1, column=2, row_label="营业收入", header="本期发生额",
                       value="100.00")
    broken = dict(cell)
    broken.pop("cell_locator")
    ok_cells, problems = SC.table_cells({"cells": [broken]})
    check(ok_cells == () and any("cell_locator" in p for p in problems),
          "缺格级 locator 的格**不**进合格集（缺一即不合格，没有「部分可用」）")

    # 6c 有格但句子没写业务行 ⇒ 硬错误
    partial_manifest = _manifest(manifest, [
        _entry(base, structured_view={"unit": "万元", "period": "本期", "scope": "合并",
                                      "cells": [cell]}),
        manifest.materials[1]])
    partial_text = "公司本期发生额为 100.00 万元。"
    partial = SC.check_sentence(sentence=_sentence("s1", partial_text, ("m01",)),
                               subsection_id=subsection_id, paragraph_id="p1",
                               manifest=partial_manifest)
    check(_reasons_for_kind(partial, "table_row_label") == ("table_row_label_missing",),
          "数字能落格、但句子没写它所在的业务行 ⇒ `table_row_label_missing`")
    check(_reasons_for_kind(partial, "numeric_surface") == (),
          "该数字在数字轴上由**格**授权（不是由压平正文授权）")
    check(_reasons_for_kind(partial, "table_column_header") == (),
          "句子里写到了指标列，列标签那一轴通过")

    # 6d 行、列、格级 locator、单位/期间/口径齐全 ⇒ **通过**（证明不是恒假门）
    full_text = "营业收入本期发生额为 100.00 万元。"
    full = SC.check_sentence(sentence=_sentence("s1", full_text, ("m01",)),
                             subsection_id=subsection_id, paragraph_id="p1",
                             manifest=partial_manifest)
    check(all(r.verdict == "pass" for r in full),
          f"行列齐全、口径声明在、格级 locator 在 ⇒ 全轴通过（实测失败 "
          f"{[(r.check_kind, r.failure_reason) for r in full if r.verdict != 'pass']}）")
    full_numeric = [r for r in full if r.check_kind == "numeric_surface"][0]
    check(full_numeric.numeric_bases == ("table_cell_source",),
          "该数字的来源类型如实记为 `table_cell_source`（它**不**因此成为合格事实或数字权威）")
    # 缺口径声明 ⇒ 口径无从核对
    naked_manifest = _manifest(manifest, [
        _entry(base, structured_view={"cells": [cell]}), manifest.materials[1]])
    naked = SC.check_sentence(sentence=_sentence("s1", full_text, ("m01",)),
                              subsection_id=subsection_id, paragraph_id="p1",
                              manifest=naked_manifest)
    check(_reasons_for_kind(naked, "table_declaration") == ("table_declaration_unavailable",),
          "表材料没有单位 / 期间 / 口径声明 ⇒ 口径无从核对，`table_declaration_unavailable`")

    # ============================================================ §7 记录与报告的硬约束
    details.append("## §7 记录的词表、一对一约束与「不得宣称语义已支持」")
    record = SC.SentenceCheckRecord.create(
        sentence_id="s1", subsection_id=subsection_id, paragraph_id="p1",
        check_kind="numeric_surface", verdict="hard_error",
        failure_reason="unsourced_number_surface", detail="d")
    check(record.record_id.startswith("scr_"), "记录身份内容寻址（`scr_`）")

    def _must_fail(fn, token: str) -> str:
        try:
            fn()
        except SC.SentenceCheckError as exc:
            assert token in str(exc), f"异常消息里没有 {token!r}：{exc}"
            return str(exc)
        raise AssertionError(f"这一路必须 fail-closed（期望含 {token!r}）")

    _must_fail(lambda: SC.SentenceCheckRecord.create(
        sentence_id="s", subsection_id="a", paragraph_id="p", check_kind="numeric_surface",
        verdict="hard_error", failure_reason="uncited_sentence"), "必须是 'unsourced_number_surface'")
    _must_fail(lambda: SC.SentenceCheckRecord.create(
        sentence_id="s", subsection_id="a", paragraph_id="p", check_kind="numeric_surface",
        verdict="pass", failure_reason="unsourced_number_surface"), "通过的记录不得带失败原因码")
    _must_fail(lambda: SC.SentenceCheckRecord.create(
        sentence_id="s", subsection_id="a", paragraph_id="p", check_kind="numeric_surface",
        verdict="partial"), "不在 ['pass', 'hard_error'] 内")
    _must_fail(lambda: SC.SentenceCheckRecord.create(
        sentence_id="s", subsection_id="a", paragraph_id="p", check_kind="semantics",
        verdict="pass"), "不在封闭集合")
    details.append("NOTE §7：原因码与轴一对一、通过记录不得带原因码、只有两档结论——"
                   "「部分可读」这类程度判断在结构上不可表达。")

    report = report_for([_sentence("s1", _BODY_A, ("m01",))])
    check(report.publishability == "not_publishable" and report.semantic_support_claimed is False,
          "报告恒为「不可发布」且不宣称语义支持")
    check(report.mechanical_verdict == "no_hard_errors",
          "「机械层无硬错误」照实给出——它与「可发布」是**两件事**，不得互相顶替")
    _must_fail(lambda: SC.SentenceCheckReport(
        report_id="scg_x", schema_version=SC.SENTENCE_CHECK_SCHEMA_VERSION,
        policy_version=SC.SENTENCE_CHECK_POLICY_VERSION, draft_id="d", input_manifest_id="m",
        dependency_fingerprint="f", sentence_count=1, records=(),
        semantic_support_claimed=True), "语义已被支持")
    _must_fail(lambda: SC.SentenceCheckReport(
        report_id="scg_x", schema_version=SC.SENTENCE_CHECK_SCHEMA_VERSION,
        policy_version=SC.SENTENCE_CHECK_POLICY_VERSION, draft_id="d", input_manifest_id="m",
        dependency_fingerprint="f", sentence_count=1, records=(),
        publishability="publishable"), "可发布")
    round_trip = SC.SentenceCheckReport.from_dict(report.to_dict())
    check(round_trip.report_id == report.report_id
          and round_trip.to_dict() == report.to_dict(),
          "报告 `to_dict` → `from_dict` 逐字段还原（同一身份、同一逐句状态）")
    check(round_trip.dependency_fingerprint == manifest.fingerprint(),
          "报告的依赖指纹就是本次输入清单的指纹（依赖轴一动，报告身份必换）")
    _must_fail(lambda: SC.check_cited_prose(
        draft=CW.CitedProseDraft.create(
            task_id="t", section_id="company", input_manifest_id="cwm_other",
            writer_identity="w", subsections=()),
        manifest=manifest), "不是本次清单")

    # ============================================================ §8 金额/比率的资格
    details.append("## §8 金额 / 比率：普通材料原文里出现过**不**构成授权（`scp-2`）")

    # 8a 先钉分类器本身（判据即这一支的全部）
    for probe, text, expected in (
            ("129,641,258 千", "公司境外收入129,641,258 千元。", True),
            ("100.00 万元", "营业收入为 100.00 万元。", True),
            ("30.60%", "占本期营业收入30.60%。", True),
            ("5 个百分点", "同比提升 5 个百分点。", True),
            ("3 项", "报告期内新增 3 项专利。", False),
            ("10 万", "销售动力电池系统 10 万台。", False),
            ("2024 年", "2024年公司主营业务未变。", False),
            ("1.2 倍", "收入较上年增长 1.2 倍。", False)):
        check(SC.is_qualified_numeric_surface(probe, text) is expected,
              f"分类器：{probe!r} 在 {text!r} 里 ⇒ "
              f"{'金额/比率（需格级或事实级）' if expected else '普通数量（只走来源轴）'}")
    check(SC.QUALIFIED_NUMERIC_BASES == ("qualified_fact", "table_cell_source")
          and "material_verbatim_text" not in SC.QUALIFIED_NUMERIC_BASES,
          "够格的两种来源就是合格事实与格级来源，**没有**「普通材料逐字出现」这一档")

    # 8b 现场缺陷形状：业务表数字被写进正文，引用只给了那条普通材料（`m*`）
    money_body = "公司境外收入129,641,258 千元，占本期营业收入30.60%。"
    money_manifest = _manifest(manifest, [_entry(base, text=money_body),
                                          manifest.materials[1]])
    money = SC.check_sentence(sentence=_sentence("s1", money_body, ("m01",)),
                              subsection_id=subsection_id, paragraph_id="p1",
                              manifest=money_manifest)
    check(_reasons_for_kind(money, "numeric_surface") == (),
          "数字轴（「有没有来源」那一轴）照旧通过：两个表面逐字都在被引材料的原文里")
    check(_reasons_for_kind(money, "numeric_qualification")
          == ("numeric_basis_not_qualified",),
          "资格轴硬错误：金额与比率只在普通材料原文里出现过，没有合格事实、也没落格")
    qual_record = [r for r in money if r.check_kind == "numeric_qualification"][0]
    check(set(qual_record.surfaces) == {"129,641,258 千", "30.60%"},
          f"逐字给出是哪几个表面没过资格（实测 {qual_record.surfaces}）")
    check(qual_record.numeric_bases == (),
          "该记录不谎报来源类型：这一句的金额/比率没有拿到够格的来源")
    emitted = [r.check_kind for r in money]
    check(sorted(emitted) == sorted(SC.CHECK_KINDS) and len(emitted) == len(set(emitted)),
          f"新增一轴之后每个轴照旧**恰好**各产一条记录（实测 {len(emitted)} 条 / "
          f"{len(SC.CHECK_KINDS)} 轴）")
    check(emitted.index("numeric_qualification") > emitted.index("numeric_surface")
          and emitted.index("numeric_qualification") < emitted.index("current_state_scope"),
          "资格轴紧挨在四类来源轴之后、当前态轴之前——它判的是「来源够了，但够不够格」")

    # 8c 反向：同一条金额改由**合格事实**支撑（路径 A）⇒ 通过，且不必再逐格
    check(_reasons_for_kind(fact_num, "numeric_qualification") == (),
          "改由合格事实支撑 ⇒ 资格轴通过（合格事实是独立的授权来源，不因此被拖进逐格要求）")

    # 8d 反向：同一条金额落到所引表材料的**一格** ⇒ 通过
    check(_reasons_for_kind(full, "numeric_qualification") == (),
          "落进表材料的合格格（行列 + 原值 + 格级 locator + 单位口径）⇒ 资格轴通过")
    check(_reasons_for_kind(partial, "numeric_qualification") == (),
          "同一格里即使句子漏写业务行，资格轴也只管「有没有拿到格」，不重复报别的轴的事")

    # 8e 反向：普通经营描述不被误伤（§0.20 明确允许照常引用材料）
    for ordinary_body in ("公司报告期内新增 3 项专利授权。",
                          "公司报告期内销售动力电池系统 10 万台。",
                          "公司主营业务覆盖 2 家境外生产基地。"):
        ordinary_manifest = _manifest(manifest, [_entry(base, text=ordinary_body),
                                                 manifest.materials[1]])
        ordinary = SC.check_sentence(sentence=_sentence("s1", ordinary_body, ("m01",)),
                                     subsection_id=subsection_id, paragraph_id="p1",
                                     manifest=ordinary_manifest)
        check(_reasons_for_kind(ordinary, "numeric_surface") == ()
              and _reasons_for_kind(ordinary, "numeric_qualification") == (),
              f"普通数量不被资格门误伤：{ordinary_body}")

    details.append(
        "NOTE §8：这道门**只**在「来源那一步已经过了」时才响——来源都没有的数字由数字轴负责，"
        "不两处判同一件事。它拦的是「库外正文里的金额看起来像结论」这一类，"
        "**不**拦普通经营描述，也**不**把任何数字升格成合格事实。")

    # ============================================================ §9 需求侧同源
    details.append("## §9 离线替身自己撤下金额 / 比率句（判据与核对器**同源**）")
    _ASPECT_A = "biz.operations"
    _ASPECT_B = "biz.profitability"
    money_material = ("公司主营业务收入为 100.00 万元。"
                      "公司主营业务由动力电池系统与储能电池系统两大业务条线构成。")
    only_money_material = ("公司报告期内营业收入为 200.00 万元。"
                           "公司报告期内综合毛利率为 30.60%。")
    # `cwm-3` 起，写作者必须**声明**本小节要写的 Contract 栏目，选材按材料的登记归属
    # （`aspect_ids`）走——旧替身那种「按清单顺序摊派」正是把法律风险材料写进供应商栏、
    # 把无关段落写进别栏目的原因，本批撤掉。
    payload = {
        "materials": [
            {"key": "m01", "text": money_material, "is_table": False, "content_kind": "",
             "aspect_ids": [_ASPECT_A]},
            {"key": "m02", "text": only_money_material, "is_table": False,
             "content_kind": "", "aspect_ids": [_ASPECT_B]}],
        "subsections": [
            {"subsection_id": "sub-a", "title": "经营情况", "requirement_text": _REQ_TEXT,
             "declared_aspect_ids": [_ASPECT_A]},
            {"subsection_id": "sub-b", "title": "盈利能力", "requirement_text": _REQ_TEXT,
             "declared_aspect_ids": [_ASPECT_B]}],
    }
    client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1,
                                                   sentences_per_material=2)
    result = client.compose(messages=[{"content": json.dumps(payload, ensure_ascii=False)}],
                            system="", prompt_version="cwp-1", model_policy="offline")
    composed = json.loads(result.text)
    sub_a, sub_b = composed["subsections"]
    kept = [s["text"] for p in sub_a["paragraphs"] for s in p["sentences"]]
    check(kept == ["公司主营业务由动力电池系统与储能电池系统两大业务条线构成。"],
          f"含金额的那一段被撤下、同一份材料里的**普通描述**照旧写出（实测 {kept}）")
    check(all(s["citations"] == ["m01"] for p in sub_a["paragraphs"] for s in p["sentences"]),
          "写出来的句子仍然只挂材料引用（替身不假装拿到了格级或事实级授权）")
    check(all(s["text"] in money_material
              for p in sub_a["paragraphs"] for s in p["sentences"]),
          "留下的句子仍是原文的**逐字子串**（替身不改写、不润色）")
    check(sub_b["paragraphs"] == [],
          "候选句全是金额 / 比率的那一小节 ⇒ 一句都不写（不写未经授权的数字句）")

    gaps_by_sub = {g["subsection_id"]: g for g in composed["gaps"]}
    check(gaps_by_sub["sub-b"]["reason"] == "source_present_but_not_admissible",
          "该小节的缺口理由是 typed 的 `source_present_but_not_admissible`"
          f"（实测 {gaps_by_sub.get('sub-b', {}).get('reason')!r}）")
    check(RUN.WITHHELD_NUMERIC_NOTE in gaps_by_sub["sub-b"]["detail"],
          "缺口说明里逐字写着「原表已展示、正文数字尚未授权」——"
          "**不**得被读成「材料里没有这张表」")
    check("sub-a" not in gaps_by_sub,
          "写出了一部分正文的小节不产生缺口（缺口 ≠ 少写了哪一句）")

    withheld_by_key = {}
    for row in client.withheld:
        withheld_by_key.setdefault(row["material_key"], []).extend(row["surfaces"])
    check(withheld_by_key == {"m01": ["100.00 万元"],
                              "m02": ["200.00 万元", "30.60%"]},
          f"逐条记下撤下的是哪几个表面（实测 {withheld_by_key}）")
    check(all(row["note"] == RUN.WITHHELD_NUMERIC_NOTE for row in client.withheld),
          "每条撤下记录都带去向说明（不写成「材料里没有」）")
    check(client.calls[-1]["withheld_sentences"] == len(client.withheld) == 3,
          "撤下句数进调用元数据（读者不必从正文推测少写了什么）")

    # 反例 9a：材料没被登记到任何栏目 ⇒ **选不出**它，缺口必须写成「本轮未取得 / 未获支持」
    unregistered_payload = {
        "materials": [{"key": "m01", "text": money_material, "is_table": False,
                       "content_kind": "", "aspect_ids": []}],
        "subsections": [payload["subsections"][0]]}
    unr_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    unr_out = json.loads(unr_client.compose(
        messages=[{"content": json.dumps(unregistered_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    unr_gap = unr_out["gaps"][0]
    check(unr_out["subsections"][0]["paragraphs"] == []
          and unr_gap["reason"] == "no_source_in_manifest",
          f"登记归属为空 ⇒ 本栏目一句都选不出来（实测 {unr_gap['reason']!r}）")
    check("本轮未取得" in unr_gap["detail"] and "未获支持" in unr_gap["detail"]
          and "上传语料里不存在该内容" in unr_gap["detail"],
          "缺口文案说的是「本轮未取得 / 未获支持」，并**逐字排除**「上传语料里不存在该内容」"
          "——登记归属为空只说明本轮没有来源在这一栏被认领，语料里有没有不由替身回答")
    check(not unr_client.withheld,
          "反例 9a 对照：这一路**不**产生撤下记录（撤下与「没被认领」是两件事）")

    # 反向对照：不含金额 / 比率的材料照旧写出，且一条撤下记录都没有
    clean_payload = {
        "materials": [{"key": "m01", "text": money_material.split("。")[1] + "。",
                       "is_table": False, "content_kind": "", "aspect_ids": [_ASPECT_A]}],
        "subsections": [payload["subsections"][0]]}
    clean_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    clean_out = json.loads(clean_client.compose(
        messages=[{"content": json.dumps(clean_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    check(not clean_client.withheld and clean_out["gaps"] == []
          and clean_out["subsections"][0]["paragraphs"],
          "不含金额 / 比率的材料：照旧写出、零撤下、零缺口（这不是一道恒假门）")

    # 9b 同一栏目被多个小节声明时，一份材料整轮只写一次；写完就走 typed 缺口，不回绕重发
    plain_a = "公司主营业务由动力电池系统与储能电池系统两大业务条线构成。"
    plain_b = "公司动力电池系统产品主要应用于新能源乘用车与商用车领域。"
    reuse_payload = {
        "materials": [{"key": "m01", "text": plain_a, "is_table": False, "content_kind": "",
                       "aspect_ids": [_ASPECT_A]},
                      {"key": "m02", "text": plain_b, "is_table": False, "content_kind": "",
                       "aspect_ids": [_ASPECT_A]}],
        "subsections": [{"subsection_id": f"sub-{i}", "title": f"栏目{i}",
                         "requirement_text": _REQ_TEXT, "declared_aspect_ids": [_ASPECT_A]}
                        for i in range(3)]}
    reuse_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    reuse_out = json.loads(reuse_client.compose(
        messages=[{"content": json.dumps(reuse_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    cited = [[s["citations"][0] for p in sub["paragraphs"] for s in p["sentences"]]
             for sub in reuse_out["subsections"]]
    check(cited[0] == ["m01"] and cited[1] == ["m02"],
          f"前两小节各拿到一份**没用过**的登记材料（实测 {cited[:2]}）")
    check(cited[2] == [] and [g["reason"] for g in reuse_out["gaps"]]
          == ["source_present_but_not_admissible"],
          f"第三小节拿不到第三份：本栏目材料已写走 ⇒ typed 缺口，**不**回绕重发同一句"
          f"（实测 {cited[2]} / {[g['reason'] for g in reuse_out['gaps']]}）")
    check(reuse_client.calls[-1]["skipped_as_repeat"] == 0
          and reuse_out["subsections"][2]["paragraphs"] == [],
          "「不被认领/已写走」与「重复」分开记：本轮零重复句，重复计数如实为 0")

    # 反例 9b：两段文字相同的材料 ⇒ 第二片**整片撤下**并如实计数（重复句不算有效正文）
    dup_payload = {
        "materials": [{"key": "m01", "text": plain_a, "is_table": False, "content_kind": "",
                       "aspect_ids": [_ASPECT_A]},
                      {"key": "m02", "text": plain_a, "is_table": False, "content_kind": "",
                       "aspect_ids": [_ASPECT_A]}],
        "subsections": [{"subsection_id": "sub-0", "title": "栏目0",
                         "requirement_text": _REQ_TEXT, "declared_aspect_ids": [_ASPECT_A]}]}
    dup_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=2)
    dup_out = json.loads(dup_client.compose(
        messages=[{"content": json.dumps(dup_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    dup_texts = [s["text"] for p in dup_out["subsections"][0]["paragraphs"]
                 for s in p["sentences"]]
    check(dup_texts == [plain_a],
          f"同一句文字只写一次（实测 {dup_texts}）")
    check(dup_client.calls[-1]["skipped_as_repeat"] == 1,
          "重复的那一片**如实计数**进调用元数据"
          f"（实测 {dup_client.calls[-1]['skipped_as_repeat']}）")

    # ============================================ §10 栏目归属轴：有轴才判，没轴不判
    details.append("## §10 栏目归属轴：清单里**有**这条轴才逐句判，没有就如实记「不适用」")

    # 有轴：材料登记在 A 栏、事实登记在 B 栏，小节**声明**的也是 B 栏
    axis_manifest = _manifest(
        manifest,
        materials=[_entry(base, aspect_ids=[_ASPECT_A]),
                   _entry(manifest.materials[1], aspect_ids=[_ASPECT_A])],
        facts=_facts_with(manifest.facts, [_ASPECT_B]),
        subsections=_subsections_with(manifest, _ASPECT_B))
    check(SC.manifest_has_aspect_axis(axis_manifest) is True,
          "至少一条来源带非空 `aspect_ids` ⇒ 这条轴**适用**，逐句照判")

    fact_ok = SC.check_sentence(
        sentence=_sentence("s1", _FACT_TEXT, ("f01",)), subsection_id=subsection_id,
        paragraph_id="p1", manifest=axis_manifest, declared_aspect_ids=(_ASPECT_B,))
    check(_reasons_for_kind(fact_ok, "aspect_attribution") == (),
          "引用的事实登记在**本小节声明的那一栏** ⇒ 栏目轴通过（不是一道恒假门）")
    cross = SC.check_sentence(
        sentence=_sentence("s1", _BODY_A, ("m01",)), subsection_id=subsection_id,
        paragraph_id="p1", manifest=axis_manifest, declared_aspect_ids=(_ASPECT_B,))
    check(_reasons_for_kind(cross, "aspect_attribution")
          == ("sentence_aspect_not_registered",),
          "同一份材料换一栏声明 ⇒ 栏目轴硬错误 `sentence_aspect_not_registered`"
          f"（实测 {_reasons_for_kind(cross, 'aspect_attribution')}）")
    check(_reasons_for_kind(cross, "citation_present") == ()
          and _reasons_for_kind(cross, "numeric_surface") == (),
          "错栏**不**牵连其余各轴：引用在场、文字也在——「有出处 / 是出处原话 / 属于这一栏」"
          "三件事正交，本轴不替它们下结论")

    axis_report = report_with(axis_manifest, [_sentence("s1", _BODY_A, ("m01",)),
                                              _sentence("s2", _FACT_TEXT, ("f01",))])
    check(axis_report.sentences_without_aspect_registration() == ("s1",)
          and axis_report.sentences_with_declared_aspect_registered() == ("s2",),
          "错栏句**照旧可读**，但**一条都不计入**本栏目覆盖（「写了 2 句」与「有 1 句算这一栏」"
          f"是两件事；实测未计入 {axis_report.sentences_without_aspect_registration()} / "
          f"计入 {axis_report.sentences_with_declared_aspect_registered()}）")
    check(_verdicts(axis_report) == {"s1": "hard_error", "s2": "pass"},
          "硬错误只落在**那两句中的一句**上，另一句照旧通过（不清零整节）")

    # 无轴：材料与事实的 `aspect_ids` 全空（= 财务 / 附注 / 外部快照那一支的真实形状）
    no_axis_manifest = _manifest(
        manifest,
        materials=[_entry(base, aspect_ids=[]),
                   _entry(manifest.materials[1], aspect_ids=[])],
        facts=_facts_with(manifest.facts, []))
    check(SC.manifest_has_aspect_axis(no_axis_manifest) is False,
          "没有任何来源被登记到任何栏目 ⇒ 这条链上**没有**这条轴")
    no_axis = SC.check_sentence(
        sentence=_sentence("s1", _BODY_A, ("m01",)), subsection_id=subsection_id,
        paragraph_id="p1", manifest=no_axis_manifest, declared_aspect_ids=(_ASPECT_A,))
    axis_rec = [r for r in no_axis if r.check_kind == "aspect_attribution"][0]
    check(axis_rec.verdict == "pass" and "不适用" in axis_rec.detail,
          "轴照旧产出一条 `pass` 记录，并在 detail 里**逐字写明**这是「不适用」"
          f"（实测 {axis_rec.verdict!r} / {axis_rec.detail[:20]!r}…）")
    check(all(r.verdict == "pass" for r in no_axis),
          "没有栏目的那一支**不会**每句都硬错误（把「没有轴」判成「全错栏」是一道假门）")
    no_axis_report = report_with(no_axis_manifest, [_sentence("s1", _BODY_A, ("m01",))])
    check(no_axis_report.sentences_with_declared_aspect_registered() == ()
          and no_axis_report.sentences_without_aspect_registration() == (),
          "「不适用而通过」的句子**不**被算成「已判过并通过」的覆盖——它没有被判过，"
          "不能被读成判过")
    check(no_axis_report.sentences_with_aspect_axis_not_applicable() == ("s1",),
          "两个读数**同时为空**时，第三种状态把**原因**说出来：这一句是「本轴没判」，"
          "不是「栏目全对上」（拿空集当通过证据，就是把 §10 要防的假门装回去）")
    check(axis_report.sentences_with_aspect_axis_not_applicable() == (),
          "有轴的那一节：没有任何一句落在「没判」上——有轴就必须逐句判过，"
          "两个读数之和因此等于全部句子数")

    # 「不适用」不是一种结论：它只能与 `pass` 同时出现，且**恒**与 hard_error 互斥
    tri = SC.SentenceCheckRecord.create(
        sentence_id="s1", subsection_id=subsection_id, paragraph_id="p1",
        check_kind="aspect_attribution", verdict="pass", detail="不适用（测试构造）",
        applicable=False)
    check(tri.verdict == "pass" and tri.applicable is False
          and "applicable" in tri.to_dict(),
          "`applicable` 进记录身份体（`to_dict` 里带这一根轴，读回与重算同一份）")
    try:
        SC.SentenceCheckRecord.create(
            sentence_id="s1", subsection_id=subsection_id, paragraph_id="p1",
            check_kind="aspect_attribution", verdict="hard_error", applicable=False)
    except SC.SentenceCheckError:
        check(True, "`applicable=False` 与 `hard_error` 同时出现 ⇒ 构造期 fail-closed"
                    "（没有「没判但判错了」这种记录）")
    else:
        check(False, "`applicable=False` 与 `hard_error` 本应被构造期拒绝")

    # ================================== §11 替身撤下：表单字形片 / 旧材料当前化片
    details.append("## §11 替身撤下：表单字形片与旧材料当前化片（各一行正反例）")

    plain = "公司主营业务由动力电池系统与储能电池系统两大业务条线构成。"
    # 11a 表单字形：正例带字形（r21 现场形状：`□是 ☑否` / `☑适用 □不适用` 前缀）
    glyph_payload = {
        "materials": [{"key": "m01", "text": "☑适用　□不适用　" + plain,
                       "is_table": False, "content_kind": "", "aspect_ids": [_ASPECT_A]}],
        "subsections": [{"subsection_id": "sub-a", "title": "经营情况",
                         "requirement_text": _REQ_TEXT, "declared_aspect_ids": [_ASPECT_A]}]}
    glyph_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    glyph_out = json.loads(glyph_client.compose(
        messages=[{"content": json.dumps(glyph_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    check(glyph_out["subsections"][0]["paragraphs"] == []
          and [r["reason"] for r in glyph_client.withheld] == ["selection_form_surface"],
          "带表单字形的一整片**整片撤下**（替身只删不改，因此不做裁剪），"
          f"原因码如实记为 `selection_form_surface`（实测 {[r['reason'] for r in glyph_client.withheld]}）")
    check(all(r["note"] == RUN._TEMPLATE_SURFACE_NOTE for r in glyph_client.withheld),
          "撤下记录带**该原因自己的**去向说明（读者据此知道是字形被撤，不是内容被判假）")
    check([g["reason"] for g in glyph_out["gaps"]]
          == ["source_present_but_not_admissible"],
          "本小节切出的候选片全被撤 ⇒ typed 缺口说「有东西、但不可采」")
    check("上传语料里不存在该内容" not in glyph_out["gaps"][0]["detail"],
          "缺口文案**不**把撤下写成「上传语料里不存在该内容」（那是两件不同的事）")

    # 11a 反例：同一句话去掉字形 ⇒ 照旧写出，零撤下
    plain_payload = {
        "materials": [{"key": "m01", "text": plain, "is_table": False,
                       "content_kind": "", "aspect_ids": [_ASPECT_A]}],
        "subsections": glyph_payload["subsections"]}
    plain_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    plain_out = json.loads(plain_client.compose(
        messages=[{"content": json.dumps(plain_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    check(not plain_client.withheld
          and [s["text"] for p in plain_out["subsections"][0]["paragraphs"]
               for s in p["sentences"]] == [plain],
          "同一句话去掉字形 ⇒ 照旧逐字写出、零撤下（撤的是**字形**，不是这句话的内容）")

    # 11b 旧材料当前化：正例只有历史角色支撑、自己没有期间限定
    history_payload = {
        "materials": [{"key": "m01", "text": plain, "is_table": False,
                       "content_kind": "", "aspect_ids": [_ASPECT_A],
                       "source_role": "history_and_conflict_source"}],
        "subsections": glyph_payload["subsections"]}
    history_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    history_out = json.loads(history_client.compose(
        messages=[{"content": json.dumps(history_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    check(history_out["subsections"][0]["paragraphs"] == []
          and [r["reason"] for r in history_client.withheld]
          == ["history_source_as_current_state"],
          "只由历史来源支撑、且自己没有期间限定 ⇒ 整片撤下，"
          f"原因码 `history_source_as_current_state`（实测 {[r['reason'] for r in history_client.withheld]}）")
    check(all(r["note"] == RUN._HISTORY_AS_CURRENT_NOTE for r in history_client.withheld),
          "该原因自己的去向说明在记录里（旧来源不得**默认**当前化）")

    # 11b 反例：同一份历史材料，句子自己把断言锚到某个年份 ⇒ 不撤
    dated = "2025年公司主营业务由动力电池系统与储能电池系统两大业务条线构成。"
    dated_payload = {
        "materials": [{"key": "m01", "text": dated, "is_table": False,
                       "content_kind": "", "aspect_ids": [_ASPECT_A],
                       "source_role": "history_and_conflict_source"}],
        "subsections": glyph_payload["subsections"]}
    dated_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    dated_out = json.loads(dated_client.compose(
        messages=[{"content": json.dumps(dated_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    check([s["text"] for p in dated_out["subsections"][0]["paragraphs"]
           for s in p["sentences"]] == [dated]
          and [r["reason"] for r in dated_client.withheld] == [],
          "同一份历史材料，句子把断言**自己锚到年份** ⇒ 不撤（判的是措辞的期间性，"
          f"不是来源的「新」；实测撤下 {[r['reason'] for r in dated_client.withheld]}）")

    # ============================================ §12 切分与去重：碎片不算句，包含即重复
    details.append("## §12 切分与去重：列举项/版式行不算句，**互相包含**才算重复")

    _real_sentence = ("公司主营业务由动力电池系统与储能电池系统两大业务条线构成，"
                      "产品覆盖多个应用领域。")
    #: 年报里最常见的两种**非句子**：以 `；` 结尾的列举项、以及没有句末标点的版式行。
    #: 整段**长于** `_MIN_SENTENCE_CHARS`（所以材料本身是「可叙述来源」），
    #: 但按它自己的句读切下去，一片句子都得不到。
    _fragments_only = "营业收入　营业成本　毛利率；\n单位：万元\n总资产　期末余额；\n"

    check(RUN._split_verbatim(_fragments_only, limit=5) == [],
          "一整段只剩列举项与版式行时，切分给出**空集**——不硬凑一句"
          f"（实测 {RUN._split_verbatim(_fragments_only, limit=5)}）")
    check(RUN._split_verbatim(_fragments_only + _real_sentence, limit=5)
          == [_real_sentence],
          "同一段里，列举项与版式行被挡在门外，紧随其后的**真句子**逐字写出"
          f"（实测 {RUN._split_verbatim(_fragments_only + _real_sentence, limit=5)}）")
    check(RUN._split_verbatim(_fragments_only + "无。", limit=5) == [],
          "长度下限管的是「这一片像不像一句」：短于下限的碎片即使带句末标点也不写进正文"
          "——它**不会**被静默丢掉，随后由小节的 typed 缺口如实记账（见下一条）")

    frag_payload = {
        "materials": [{"key": "m01", "text": _fragments_only, "is_table": False,
                       "content_kind": "", "aspect_ids": [_ASPECT_A]}],
        "subsections": [{"subsection_id": "sub-0", "title": "栏目0",
                         "requirement_text": _REQ_TEXT, "declared_aspect_ids": [_ASPECT_A]}]}
    frag_client = RUN.OfflineExtractiveCitedProseClient(materials_per_subsection=1)
    frag_out = json.loads(frag_client.compose(
        messages=[{"content": json.dumps(frag_payload, ensure_ascii=False)}],
        system="", prompt_version="cwp-1", model_policy="offline").text)
    check(frag_out["subsections"][0]["paragraphs"] == []
          and [g["reason"] for g in frag_out["gaps"]]
          == ["manifest_partial_for_requirement"],
          "材料**在场且已登记**、只是切不出完整句 ⇒ 零正文 + 原因码 "
          "`manifest_partial_for_requirement`，如实说「切不出可引的完整句」，"
          "**不**写成「材料里没有该内容」"
          f"（实测 {[g['reason'] for g in frag_out['gaps']]}）")

    _short = "公司动力电池业务收入同比上升。"
    _long = _short + "储能电池业务收入同比上升。"
    check(RUN._is_repeat(_long, [_short]) and RUN._is_repeat(_short, [_long]),
          "**互相包含**（长句把短句整句包住、或后一片是前一片的尾巴）两个方向都判重复"
          "——读者两个方向看到的都是同一串字")
    check(not RUN._is_repeat("公司动力电池系统产品的销量同比增长明显。", [_short])
          and not RUN._is_repeat(_short, ["公司动力电池系统产品的销量同比增长明显。"]),
          "两件**不同**的业务事实即使措辞接近也**不**判重复：判据是字面包含，"
          "不是相似度——相似度判重会把两件不同的事合成一件，那是删事实")

    # ==================================== §13 展示角色：诊断槽位事实不得进普通正文
    details.append("## §13 展示角色轴（`scp-6`）：诊断槽位事实写进正文 ⇒ 硬错")
    fact = manifest.facts[0]
    sub = manifest.subsections[0].subsection_id
    #: 路由声明把事实落到的栏目设成**本小节自己声明的栏目**——这是有意的：这样轴 8 的栏目
    #: 归属**通过**，本句就只剩展示档这一条判据在分正反例。若把栏目设成另一个名字，两轴会
    #: 同时红，就分不清「写错了栏目」与「栏目对但展示层用错」。取值从**清单自己**读，不住手
    #: 写一个字面栏目名（那样夹具一改，本条的结论就悄悄变成别的意思）。
    _column = manifest.subsections[0].declared_aspect_ids[0]

    def _routing(tier: str) -> dict:
        return {"routing_version": "pr-1", "source": "stub",
                "fact_columns": [{"fact_id": fact.fact_id, "metric_code": "SOLV_INTEREST_COVER",
                                  "presentation_column": _column}],
                "routes": [{"metric_code": "SOLV_INTEREST_COVER",
                            "presentation_column": _column,
                            "contract_display_tier": tier}]}

    def _kinds_failing(man, sentence) -> set:
        recs = SC.check_sentence(sentence=sentence, subsection_id=sub, paragraph_id="p1",
                                 manifest=man)
        return {r.check_kind for r in recs if r.verdict == "hard_error"}, recs

    fact_sentence = _sentence("s1", fact.text, (fact.citation_key,))
    diag_manifest = _manifest(manifest, presentation_routing=_routing("diagnostic_only"))
    failing, diag_records = _kinds_failing(diag_manifest, fact_sentence)
    check(failing == {"display_role"},
          "**正例（硬错）**：句子引的是一条 `contract_display_tier=diagnostic_only` 的事实 ⇒ "
          "只有展示角色轴红（栏目归属轴**通过**——两轴正交，它们抓的不是同一件事）"
          f"（实测红的轴：{sorted(failing)}）")
    check(_reasons_for_kind(diag_records, "display_role") == ("diagnostic_fact_in_body",),
          "失败原因码是**本轴唯一**的 `diagnostic_fact_in_body`"
          f"（实测 {_reasons_for_kind(diag_records, 'display_role')}）")

    body_manifest = _manifest(manifest, presentation_routing=_routing("required_body"))
    failing_body, _ = _kinds_failing(body_manifest, fact_sentence)
    check(not failing_body,
          "**反例（通过）**：同一句话、同一条事实、同一个栏目，只把展示档换成 "
          f"`required_body` ⇒ 一处不红（实测红的轴：{sorted(failing_body)}）"
          "——本轴判的是**档位**，不是「有没有引事实」")

    no_axis, no_axis_records = _kinds_failing(manifest, fact_sentence)
    no_axis_display = [r for r in no_axis_records if r.check_kind == "display_role"]
    check(not no_axis and len(no_axis_display) == 1
          and no_axis_display[0].verdict == "pass"
          and not no_axis_display[0].applicable,
          "**反例（不适用）**：本节这次输入**没有**呈现层路由声明 ⇒ 本轴记「不适用」"
          "（`verdict=pass` 且 `applicable=False`），**不**判成「全写错档」"
          f"（实测 {[(r.verdict, r.applicable) for r in no_axis_display]}）")

    mat_records = SC.check_sentence(
        sentence=_sentence("s1", _BODY_A, ("m01",)), subsection_id=sub,
        paragraph_id="p1", manifest=diag_manifest)
    mat_display = [r for r in mat_records if r.check_kind == "display_role"][0]
    check(mat_display.verdict == "pass" and not mat_display.applicable,
          "**反例（不适用）**：句子只引**材料**行 ⇒ 本轴不适用（展示档只声明在事实行上；"
          "材料行的角色由「来源角色」轴负责）")

    partial = _manifest(manifest, presentation_routing={
        "routing_version": "pr-1",
        "fact_columns": [{"fact_id": fact.fact_id, "presentation_column": _column}],
        "routes": []})
    _, partial_records = _kinds_failing(partial, fact_sentence)
    partial_display = [r for r in partial_records if r.check_kind == "display_role"][0]
    check(partial_display.verdict == "pass" and partial_display.applicable
          and "查不到展示档" in partial_display.detail,
          "**边界**：栏目查得到、展示档查不到 ⇒ **不**硬错，但在 detail 里如实说「本轴不为它"
          "代言，也不假装它已核过」——不为一条读不全的声明造一道假门"
          f"（实测 detail={partial_display.detail!r}）")

    details.append(
        "NOTE §13：展示角色是 `DESIGN_V2.md` §0.21 的确定性落点，**不由写作侧自述**决定，"
        "也不问 review LLM。它只**增加**一条轴：`sc-6` ⊂ `sc-7`，旧记录形状一字未改。"
        "「栏目对得上」与「展示层用对」必须分开报——合成一条，读者面就分不清"
        "「写错了栏目」与「写完栏目但把诊断口径当正文结论」。")

    details.append(
        "NOTE §12：碎句与重复句此前**没有任何聚焦回归**，本条补上正反例各一对。"
        "两条纪律都只**少写**、不改写、不清零整节：切不出句子时小节的缺口如实写出原因码，"
        "重复时调用元数据里的 `skipped_as_repeat` 如实计数。")

    details.append(
        "NOTE §10：这条轴的**适用性**与「逐句都判过」是两回事。两个读数（"
        "未计入覆盖 / 已判过并通过）在「不适用」那一支上**同时为空**——不是因为都对上了，"
        "而是因为这一节根本没有这条轴。任何拿「未计入 = 空」当「栏目全对上」的写法，"
        "都是把 §10 后半段读反了。")

    details.append(
        "NOTE §11：替身撤下的三条理由（金额未授权 / 表单字形 / 旧来源当前化）都**只撤句、"
        "不改写、不清零整节**，且每条都在缺口说明里带上**自己的**去向。撤下 ≠ 材料里没有"
        "——原表在原 PDF 表区只读展示里已经展示，缺的是路径 A 的预验证。")

    details.append(
        "NOTE §9b：不重复发只减少**重复**，它**不**建立相关性——替身没有判断力，不得假装"
        "「这一小节就该用这份材料」。切题分配只能由真实写作模型在授权 run 里给出；"
        "本读回据此把「重复」与「跑题」分开报，不混成一条。")

    details.append(
        "NOTE §9：撤下的判据**复用**核对器的 `is_qualified_numeric_surface`，不在这里抄第二份"
        "——两处各写一份，「替身撤了什么」与「核对器拒了什么」迟早会对不上。"
        "撤下 ≠ 材料里没有那张表：原表在原 PDF 表区只读展示里已经展示，缺的是路径 A 的预验证。")

    details.append(
        "NOTE 本模块只证明**机械底线**：它能说「这一句有一处可机械证明站不住的地方」，"
        "**没有**资格说「这句话说得对」。后者属于随后那次独立只读审阅（`cited_review`）。")

    # ==================================================== §14 报告族（`scp-8`）
    details.append("## §14 报告族：把「来源撑不住」与「引用未登记本栏」分开计数")
    #: 机制（`scp-8`）：判据本身一字未改，新增的只是**读者面的分类**——同一个「硬错 N 条」
    #: 里混着两类处置完全不同的失败：`fact_safety`（来源/资格撑不住这句话 ⇒ 撤数或补资格）
    #: 与 `column_coverage`（字与来源都成立、错在服务哪一栏 ⇒ 补一次正确的取材/改栏）。
    #: 第三档 `criteria_diagnostic` 根本不是失败（这一轴这次没判）。这里钉的是**表与词表同源**、
    #: **两族都不是空集也不是全集**、以及**未知轴必须抛错而不是被猜进某一族**。
    check(set(SC.CHECK_FAMILY_BY_KIND) == set(SC.CHECK_KINDS),
          f"**完整性**：族表与 `CHECK_KINDS` 逐轴相等（轴 {len(SC.CHECK_KINDS)} 条、"
          f"表 {len(SC.CHECK_FAMILY_BY_KIND)} 条；缺 "
          f"{sorted(set(SC.CHECK_KINDS) - set(SC.CHECK_FAMILY_BY_KIND))}、多 "
          f"{sorted(set(SC.CHECK_FAMILY_BY_KIND) - set(SC.CHECK_KINDS))}）"
          "——新增轴若落进「无族」，分族计数会静默少算一条")
    check(all(f in SC.CHECK_FAMILIES for f in SC.CHECK_FAMILY_BY_KIND.values()),
          "族的**值域**只有 `CHECK_FAMILIES` 里那几档（不新造第三个失败族）")
    check(SC.CRITERIA_DIAGNOSTIC_FAMILY not in SC.CHECK_FAMILIES,
          "「这一轴这次没判」**不在**失败族里——混进去会让「族计数之和 = 硬错数」当场不成立")
    for family in SC.CHECK_FAMILIES:
        members = [k for k, v in SC.CHECK_FAMILY_BY_KIND.items() if v == family]
        check(bool(members), f"**正例**：`{family}` 至少有一条轴（不是空族）")
        check(len(members) < len(SC.CHECK_KINDS),
              f"**反例**：`{family}` 不是全集——一族吞下所有轴就等于没分族")
    check(SC.CHECK_FAMILY_BY_KIND["aspect_attribution"] == "column_coverage"
          and SC.CHECK_FAMILY_BY_KIND["presentation_column_attribution"] == "column_coverage",
          "两条栏目归属轴（登记轴与呈现轴）都归 `column_coverage`")
    check(SC.CHECK_FAMILY_BY_KIND["numeric_qualification"] == "fact_safety"
          and SC.CHECK_FAMILY_BY_KIND["current_state_scope"] == "fact_safety",
          "「数字无资格」「历史材料当前化」归 `fact_safety`——它们的返修动作与栏目覆盖不重叠")
    check(SC.CHECK_FAMILY_BY_KIND["numeric_qualification"]
          != SC.CHECK_FAMILY_BY_KIND["aspect_attribution"],
          "**反例**：把无资格数字与未登记本栏归成同一族，正是本批要拆开的那个误读")
    check(SC.family_of_check_kind("subject_surface") == "fact_safety",
          "**正例**：已知轴查得到族")
    try:
        SC.family_of_check_kind("__no_such_kind__")
    except SC.SentenceCheckError:
        check(True, "**反例**：未知轴**抛错**，不猜一族（猜错会让计数当场变成谎话）")
    else:
        check(False, "未知轴被静默分进了某一族")
    check(SC.family_of_failure_reason("sentence_aspect_not_registered") == "column_coverage",
          "**正例**：原因码 → 族（读者面只拿得到原因码时的入口）")
    check(SC.family_of_failure_reason("numeric_basis_not_qualified") == "fact_safety",
          "**正例**：无资格金额/比率的原因码归事实安全")
    check(SC.family_of_failure_reason("__no_such_reason__") == "fact_safety",
          "**反例**：表外的原因码按**事实安全**处理——分叉时宁可多报一条可机械证明的失败，"
          "也不得把它静默归到不阻断的那一族里")

    # ==================================================== §F 分族聚合口径（`scp-10`）
    details.append("## §F 分族聚合：`blocked_sentence_ids` 只收事实安全族，并集另报")
    #: 机制（`scp-10`）：判据一条未改，改的是**聚合口径**——「阻断句」从两族并集收窄为
    #: **事实安全族**。两族两件事：`fact_safety` 是「来源/资格撑不住这句话」，`column_coverage`
    #: 是「字与来源都成立、错在服务哪一栏」。前者的返修动作是撤数或补资格，后者是补一次正确
    #: 的取材/改栏；把两者加在同一个数里，读者只会读到「正文有硬错」。这里钉四件事：
    #: ① 当前政策登记在封闭表里；② 只有栏目覆盖族时阻塞集为空、档位是新增的第四档；
    #: ③ 事实安全族在场时照旧阻断；④ 旧产物（`scp-10` 之前）按其**自己声明的**政策版本解码，
    #: 仍走并集口径 ⇒ 盘上的 `mechanical_state` / 阻断句数重算后逐字相等。
    check(SC.SENTENCE_CHECK_POLICY_VERSION in SC.FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS,
          f"**钉子**：当前政策 `{SC.SENTENCE_CHECK_POLICY_VERSION}` 必须登记在 "
          "`FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS` 里——漏登记会让新写的报告被当成旧口径"
          "（并集）读，阻断句数当场变大，而且没有任何一处会报错")
    #: `scp-11` 进表是因为它**只换了一支的授权判据**（`numeric_qualification` 的事实支改成
    #: 语义配对）、`scp-12` 再收一格（声明了逐值身份的事实只按声明配对，不得回落文本反推）、
    #: `scp-13` 又收两格（声明了身份的事实不再享有「读不出即放行」的回落；已声明的占比事实在
    #: 分母可核之前一律不授权），聚合口径与 `scp-10` 逐字相同——分族聚合的整套读数因此可以
    #: 照旧读它们。
    #: 更早的版本（`scp-9` 及以前）走的仍是并集口径，写进来等于回头改历史的读法。
    check(set(SC.FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS) <= {"scp-10", "scp-11", "scp-12",
                                                             "scp-13"},
          "**反例**：登记表只能收「已按分族聚合计口径」的那几版；把更早的版本写进来，"
          "等于回头改历史的读法")
    check(axis_report.mechanical_verdict == "column_coverage_only"
          and axis_report.blocked_sentence_ids == ()
          and axis_report.blocked_sentence_count == 0
          and axis_report.hard_error_sentence_ids == ("s1",)
          and axis_report.column_coverage_sentence_ids == ("s1",)
          and axis_report.column_coverage_sentence_count == 1,
          "**正例**：整节只有栏目覆盖族失败 ⇒ 阻断集**空**、并集仍是 `s1`、"
          f"档位 `{axis_report.mechanical_verdict}`（实测 阻断 "
          f"{axis_report.blocked_sentence_ids} / 并集 {axis_report.hard_error_sentence_ids}）"
          "——旧读法（并集）会把这一节读成「一句阻断」，新读法只把它读成「这一栏没答」")
    check(axis_report.sentences_without_aspect_registration() == ("s1",)
          and axis_report.sentences_with_declared_aspect_registered() == ("s2",),
          "**不放开旧门**：分族只是把数拆开，`s1` 照旧**一条都不计入**本栏覆盖——"
          "「不阻断事实安全」不等于「这一栏答过了」")
    _fact_fam = report_for([_sentence("s1", "2024年公司主营业务由动力电池系统构成。",
                                      ("m01",))])
    check(_fact_fam.blocked_sentence_ids == ("s1",)
          and _fact_fam.mechanical_verdict == "has_hard_errors"
          and _fact_fam.column_coverage_sentence_ids == (),
          "**反例**：事实安全族在场 ⇒ 阻断集**照旧**非空、档位回到 `has_hard_errors`"
          f"（实测 {_fact_fam.blocked_sentence_ids} / {_fact_fam.mechanical_verdict}）")
    #: 旧产物要**忠实**才谈得上向后兼容。`policy_version` 在身份体里（`_report_identity_body`），
    #: 所以真实 `scp-9` 写盘时 `report_id` 是按**它自己**那份身份体（`policy_version="scp-9"`）
    #: 算的。只把版本串改掉、不重算 `report_id`，造出来的是**自相矛盾**的记录——它被身份闸
    #: 拒掉正是该有的行为，**不是**「旧产物读不出来」。下面两例把这两件事分开钉死。
    _tampered = axis_report.to_dict()
    _tampered["policy_version"] = "scp-9"
    for _key in ("blocked_sentence_ids", "blocked_sentence_count", "hard_error_sentence_ids",
                 "fact_safety_sentence_ids", "column_coverage_sentence_ids",
                 "column_coverage_sentence_count"):
        _tampered.pop(_key, None)
    try:
        SC.SentenceCheckReport.from_dict(_tampered)
    except SC.SentenceCheckError as exc:
        check("report_id" in str(exc),
              "**反例**：改 `policy_version` 却不重算 `report_id` 的记录必须被身份闸拒掉"
              f"（实测 {exc!r}）——这条挡的是**伪造**，不是兼容性；"
              "一旦有人把 `policy_version` 移出身份体，这里会转红")
    else:
        check(False, "**反例**：改 `policy_version` 不重算 `report_id` 竟被接受 ⇒ "
                     "`policy_version` 已不在身份体里，同一份记录的版本串可以随便改")
    #: 忠实旧产物：用 `create()` 按 `scp-9` 现造一份（真实旧盘就是这么写出来的），
    #: 身份体与 `report_id` 天然自洽；再剥掉 `scp-10` 才有的读视图键，模拟旧盘的字段集合。
    _legacy_raw = SC.SentenceCheckReport.create(
        draft_id=axis_report.draft_id, input_manifest_id=axis_report.input_manifest_id,
        dependency_fingerprint=axis_report.dependency_fingerprint,
        sentence_count=axis_report.sentence_count, records=axis_report.records,
        policy_version="scp-9").to_dict()
    for _key in ("blocked_sentence_ids", "blocked_sentence_count", "hard_error_sentence_ids",
                 "fact_safety_sentence_ids", "column_coverage_sentence_ids",
                 "column_coverage_sentence_count"):
        _legacy_raw.pop(_key, None)
    try:
        _legacy = SC.SentenceCheckReport.from_dict(_legacy_raw)
    except SC.SentenceCheckError as exc:  # pragma: no cover - 结构一改这里立刻变成红
        check(False, f"旧产物（`scp-9`）应照旧解码得出，实测抛 {exc!r}")
    else:
        check(_legacy.uses_family_scoped_blocking is False
              and _legacy.blocked_sentence_ids == _legacy.hard_error_sentence_ids == ("s1",)
              and _legacy.mechanical_verdict == "has_hard_errors",
              "**旧产物按它自己声明的政策解码**：`scp-9` 走并集口径，"
              "阻断句与档位重算后与它写时逐字相同（不把新口径追认到旧报告上）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

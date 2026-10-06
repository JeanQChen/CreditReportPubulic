"""Eval: §0.20 第一步 —— Pack → 带逐句引用的自然正文（`cwm-1` / `cw-1` / `cwp-1`）。

用法: python -m evals.test_m930_3_cited_writer

本模块钉住新写作接口的**边界**，不是它的措辞。逐条证明：

1. **输入面逐条等于当前 Pack 的精确材料清单**：`CitedWriterInputManifest` 的材料行集合与
   `pack_writer._derive_material_manifest` 的成员集合**逐成员相等**（不是「有多少条」相等）；
   清单里少一份 / 多一份都 fail-closed，且不经过 `CitedWriterInputManifest` 的构造期。
2. **引用键的命名空间由本清单独占**：材料轴 `m…` / 事实轴 `f…` **前缀不相交**，某个键不存在
   于本清单时**逐句**抛出（带 `sentence_id` 与 `citation_key`），而不是让一个错键静默变成
   一次字典未命中。
3. **财务事实不伪装成普通 Pack 材料**：`CitedMaterialEntry.authority_kind` 只能是
   `topic_pack`；`CitedFactEntry` 的 `fact_id` 按该 kind 的 branch-specific 字段取值，
   四条 `authority_kind` 各保各的身份。
4. **草稿小节与请求面小节一一对应**：少一个 / 多一个都拒（「模型漏写」与「本来没有内容」
   必须可区分）。
5. **采用去向按句引用记，不按 proposal ID**：每个清单成员恰一条记录，`not_used` 带空引用，
   且 `not_used` **不是**缺口。
6. **一处无引用的句子不清零整节**：空引用在 wire 层合法（由第二步逐句核对标记），因此
   同一份草稿里其余句子仍然可读、可回查。
7. **往返**：`to_dict` → `from_dict` 逐字段还原，篡改任一字段必被身份重算抓住。
8. **纯空壳段（`crn-2`）的接线**（§16）：判定要的两个读数（这一栏登记到几条来源、顶层有没有
   那条系统判定的 `no_source_in_manifest` 缺口）**都来自清单侧**，模型自述的 `reason` 不作数；
   五项同时满足才删，删掉的那个段**不计入栏目覆盖**、缺口照旧由顶层那条记着；缺任一条件、
   含其他内容或非空句子时仍照旧拒收。

夹具复用 `test_demo_pack_writer` 的真实 `VerifiedPackSet` / 真实 `ResearchMaterial` / 同一条
`resolve_writer_material_context` 入口；模块无公司代号、文件名、页码、表号或固定年份的生产
字面量。不调 LLM（stub 只回放夹具）、不联网、不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T        # noqa: E402
from harness import topic_schema as TS              # noqa: E402
from sections import cited_reply_normalize as CRN   # noqa: E402
from sections import cited_writer as CW             # noqa: E402
from sections import material_context as MC         # noqa: E402
from sections import narrative_schema as NS         # noqa: E402
from sections import pack_writer as PW              # noqa: E402

_TOPIC = T.TOPIC_BUSINESS
_ASPECT = T.ASP_BUSINESS_MAIN
_MAT_A = "m-mat-a"
_MAT_B = "m-mat-b"
_BODY_A = "公司主营业务由动力电池系统与储能电池系统两大业务条线构成。"
_BODY_B = "动力电池系统产品主要应用于新能源乘用车与商用车领域。"
_REQ_TEXT = "列示报告期内主营业务收入的构成"


#: 一条**权威事实**（路径 A）。它的引用锚点落在 `_MAT_A` 的来源身份上，因此**不会**让
#: `auto_materials` 另造一条材料——事实行与材料行各自的身份在这个夹具里都真实在场。
_FACT_TEXT = "报告期内公司主营业务收入为 100.00 万元。"


def _task_and_authority():
    """一份最小的真实夹具：一个 topic、两条材料、一条权威事实、一条 aspect 要求。"""
    task = T._task("company", (_TOPIC,))
    materials = (T._Material(_MAT_A, f"evidence:{T.EV_ID}", page=12),
                 T._Material(_MAT_B, "evidence:ev-b", page=13))
    pack_set = T._pack_set(
        task, packs=(T._Pack(topic_id=_TOPIC, materials=materials,
                             facts=(T._fact("f-topic-1", _FACT_TEXT, (_ASPECT,),
                                            page=12, period="2025年度", scope="合并"),),
                             aspect_results=(T._AspectResult(_ASPECT, "covered"),)),),
        requirements=(T._Req(_TOPIC, (T._aspect(_ASPECT, _TOPIC, T._question_id(_TOPIC),
                                                requirement_text=_REQ_TEXT),)),))
    authority = PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=T.COMPANY_ID, report_as_of=T.REPORT_AS_OF,
        contract_version=T.CONTRACT_VERSION, contract_fingerprint=T.CONTRACT_FINGERPRINT)
    return task, authority


def _subsections():
    return (CW.CitedSubsectionSpec(subsection_id="sub-products", title="产品或服务",
                                   requirement_text=_REQ_TEXT,
                                   declared_aspect_ids=(_ASPECT,)),)


def _input_manifest(*, authority=None, facts=()):
    task, built = _task_and_authority()
    authority = authority or built
    context = T._writer_material_context(authority.pack_set, task_id=str(task.task_id),
                                         section_id="company")
    return CW.build_cited_writer_input(
        authority=authority, material_context=context, subsections=_subsections(),
        facts=facts, section_title="主营业务")


def _scan_facts(authority, task):
    return PW.scan_topic_pack(authority, task).facts


def _draft_payload(*, manifest, citations_a, citations_b, extra_subsection=None):
    """按请求面小节生成一份最小合法返回（引用键取自本清单的真实键）。"""
    subs = []
    for spec in manifest.subsections:
        subs.append({"subsection_id": spec.subsection_id, "title": spec.title,
                     "paragraphs": [{"paragraph_id": "p1", "sentences": [
                         {"sentence_id": "s1", "text": _BODY_A,
                          "citations": list(citations_a)},
                         {"sentence_id": "s2", "text": _BODY_B,
                          "citations": list(citations_b)}]}]})
    subs.extend(extra_subsection or [])
    return json.dumps({"subsections": subs, "gaps": [], "follow_up_needs": []},
                      ensure_ascii=False)


def _parse(text, *, manifest):
    return CW.parse_cited_prose(text, manifest=manifest)


def _rebuild(manifest, materials):
    """换掉材料行、**经 `create` 重算身份**（不得用 `replace` 留旧 id：那是伪造身份）。

    身份必须由 `create` 按内容重算，所以这里只换输入、不换 id——`replace` 会带着旧
    `manifest_id` 触发本类自己的「身份与内容不符」fail-closed，恰好证明那道校验是真的。
    """
    fields = {f.name for f in __import__("dataclasses").fields(manifest)}
    return CW.CitedWriterInputManifest.create(**{
        **{name: getattr(manifest, name) for name in fields if name != "manifest_id"},
        "materials": tuple(materials)})


def _split_material(*, material_id: str, reading_view: str, span_id: str,
                    piece_index: int, piece_count: int, span_char_length: int,
                    local_range: list) -> MC.ResolvedWriterMaterial:
    """一条**被切过**的材料（§13 用）：正文是本片自己的，`span_continuity` 逐字来自信封形状。"""
    cont = MC._continuity_from_split(
        {"span_id": span_id, "piece_index": piece_index, "piece_count": piece_count,
         "span_char_length": span_char_length, "span_local_char_range": list(local_range)},
        member_ref=material_id)
    return MC.ResolvedWriterMaterial.create(
        member_ref=NS.manifest_member_ref("pack-1", material_id), topic_id="topic-x",
        pack_id="pack-1", material_id=material_id, material_type="evidence_span",
        research_material_disposition_id=f"rmd-{material_id}",
        source_identity="evidence:ev-1", provenance_identity=f"prov-{material_id}",
        locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40),
        payload_ref=TS.MaterialPayloadRef(
            object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
            content_hash="a" * 64,
            locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                                       section_path="s1", page=3),
            created_dependency_fingerprint="d" * 64).to_dict(),
        payload_hash="a" * 64, content_hash="a" * 64,
        material_content_fingerprint="a" * 64, reading_view=reading_view,
        span_continuity=cont)


def _expect_error(fn, *, token: str, reason: str = "") -> str:
    try:
        fn()
    except CW.CitedWriterError as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(
                f"异常消息里没有 {token!r}（无法据此定位）：{message}") from None
        if reason and exc.reason != reason:
            raise AssertionError(
                f"typed 原因码不是 {reason!r}，而是 {exc.reason!r}：{message}") from None
        return message
    raise AssertionError(f"这一路必须 fail-closed，但它通过了（期望含 {token!r}）")


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

    task, authority = _task_and_authority()
    context = T._writer_material_context(authority.pack_set, task_id=str(task.task_id),
                                         section_id="company")
    facts = _scan_facts(authority, task)
    manifest = CW.build_cited_writer_input(
        authority=authority, material_context=context, subsections=_subsections(),
        facts=facts, section_title="主营业务")

    # ============================================================ §1 输入面 = 精确清单
    details.append("## §1 输入面逐条等于当前 Pack 的精确材料清单")
    exact = PW._derive_material_manifest(authority, material_context=context)
    check([m.member_ref for m in manifest.materials]
          == [e.member_ref for e in exact.entries],
          "材料行与 `_derive_material_manifest` 的成员**逐成员同序相等**（不是条数相等）")
    check(len(manifest.materials) == 2,
          f"本次夹具的两条材料都在场（实测 {len(manifest.materials)} 条）")
    check(all(m.reading_view for m in manifest.materials),
          "每一行都带**原文**（`wmctx-1` 的读视图），不是只有 material ID")
    for entry, resolved in zip(manifest.materials, context.materials):
        check(entry.reading_view == resolved.reading_view,
              f"成员 {entry.material_id}：清单里的正文与上下文读视图**逐字节相同**")
        check(entry.locator_ref == resolved.locator_ref,
              f"成员 {entry.material_id}：精确位置随正文一同进入输入面")
    check(all(m.source_role in (None, "") or isinstance(m.source_role, str)
              for m in manifest.materials),
          "来源角色是**字符串**（无该轴时为空串，不是缺字段）")

    # 反例 1a：清单里有成员、上下文里没有 ⇒ fail-closed（「有 ID」≠「正文在场」）
    _expect_error(
        lambda: CW.build_cited_writer_input(
            authority=authority, material_context=None, subsections=_subsections()),
        token="材料正文上下文", reason="material_context_missing")
    details.append("NOTE 反例 1a：缺正文上下文 ⇒ `material_context_missing`，"
                   "不得降级成「只有 material ID 的写作」。")

    # 反例 1b：字段形状替身不得进入权威输入
    class _FakeContext:
        pass

    _expect_error(
        lambda: CW.build_cited_writer_input(
            authority=authority, material_context=_FakeContext(), subsections=_subsections()),
        token="WriterMaterialContext", reason="material_context_not_real")
    details.append("NOTE 反例 1b：字段形状替身 ⇒ `material_context_not_real`（§三 4）。")

    # ============================================================ §2 引用键命名空间
    details.append("## §2 引用键的命名空间由本清单独占；两轴前缀不相交")
    mat_keys = manifest.material_keys()
    fact_keys = manifest.fact_keys()
    check(mat_keys and all(CW.citation_key_axis(k) == "material" for k in mat_keys),
          f"材料键落在材料轴：{mat_keys}")
    check(all(CW.citation_key_axis(k) == "fact" for k in fact_keys),
          f"事实键落在事实轴：{fact_keys}")
    check(set(mat_keys) & set(fact_keys) == set(),
          "两轴的键集合不相交（任何一侧都产不出另一侧的取值）")
    check(mat_keys == tuple(CW.cited_material_key(i) for i in range(len(mat_keys))),
          "材料键按清单序**连续**编号（有空洞时「键不存在」与「本来没有」不可区分）")
    _expect_error(lambda: CW.citation_key_axis("x01"),
                  token="不属于任何一条登记过的引用轴", reason="citation_key_malformed")

    # 反例 2a：引用一个本清单里不存在的键 ⇒ 逐句抛出，带句子与键
    bad = _draft_payload(manifest=manifest, citations_a=("m99",), citations_b=("m02",))
    message = _expect_error(lambda: _parse(bad, manifest=manifest),
                            token="不存在的键", reason="citation_not_in_input")
    #: `cw-2` 起，`sentence_id` 是**解析边界按结构分配的最终编号**（夹具里模型原写 `s1`，
    #: 最终编号是 `s0001`）。因此这里点名的是最终编号——核查与审阅都按最终编号定位对象，
    #: 而模型原 ID / 结构位置 / 最终 ID 三者的映射由 `crn-1` 的台账给出（见
    #: `test_m930_3_cited_reply_normalize`）。
    check("s0001" in message and "m99" in message,
          "拒绝信息点名**哪一句（最终编号）、哪一个键**（否则无法逐句定位）")
    details.append("NOTE 反例 2a：越界引用键 ⇒ `citation_not_in_input`，逐句带最终 `sentence_id` "
                   "与 `citation_key`。")

    # 反例 2b：把事实轴的键写进材料行 ⇒ 类型层即拒
    _expect_error(
        lambda: CW.CitedMaterialEntry(
            citation_key="f01", member_ref=manifest.materials[0].member_ref,
            pack_id="p", material_id="m", topic_id="t", material_type="evidence_span",
            source_identity="s", provenance_identity="p", source_role="",
            locator_ref=manifest.materials[0].locator_ref,
            payload_hash="a" * 64, reading_view="x", reading_view_fingerprint="b" * 64),
        token="不是材料轴键", reason="citation_key_wrong_axis")
    details.append("NOTE 反例 2b：事实键冒充材料行 ⇒ `citation_key_wrong_axis`（类型层）。")

    # ============================================================ §3 三套身份不混用
    details.append("## §3 财务事实不伪装成普通 Pack 材料")
    _expect_error(
        lambda: CW.CitedMaterialEntry(
            citation_key="m01", member_ref=manifest.materials[0].member_ref,
            pack_id="p", material_id="m", topic_id="t", material_type="evidence_span",
            source_identity="s", provenance_identity="p", source_role="",
            locator_ref=manifest.materials[0].locator_ref,
            payload_hash="a" * 64, reading_view="x", reading_view_fingerprint="b" * 64,
            authority_kind="financial_pack"),
        token="不得伪装成一条普通 Pack 材料", reason="material_authority_forged")
    details.append("NOTE 反例 3a：`authority_kind != topic_pack` 的材料行 ⇒ "
                   "`material_authority_forged`。")

    # 正例：权威事实行进清单时，身份字段按 kind 的 branch-specific 字段取值
    if facts:
        fact_row = manifest.facts[0]
        check(fact_row.fact_field == NS.FACT_FIELD_BY_AUTHORITY_KIND[fact_row.authority_kind],
              f"事实行 {fact_row.citation_key}：`fact_id` 落在它自己 kind 的字段上"
              f"（{fact_row.fact_field}）")
        check(fact_row.authority_kind in NS.AUTHORITY_KINDS,
              "事实行的 authority_kind 是四元 union 的成员")
    else:
        skipped += 1
        details.append("SKIP §3 正例：本夹具的扫描没有产出权威事实行")
    _expect_error(
        lambda: CW.CitedFactEntry(
            citation_key="f01", authority_kind="not_a_kind", container_identity="c",
            fact_id="x", text="t", topic_id="t", aspect_ids=(), fact_type="k",
            period="", scope="", required=False),
        token="不在", reason="authority_kind_unknown")

    # ============================================================ §4 小节一一对应
    details.append("## §4 草稿小节与请求面小节必须一一对应")
    ok = _parse(_draft_payload(manifest=manifest, citations_a=("m01",),
                               citations_b=("m02",)), manifest=manifest)
    check([s.subsection_id for s in ok.subsections]
          == [s.subsection_id for s in manifest.subsections],
          "正向：草稿小节与请求面小节逐条相同")
    missing = json.dumps({"subsections": [], "gaps": [], "follow_up_needs": []},
                         ensure_ascii=False)
    _expect_error(lambda: _parse(missing, manifest=manifest),
                  token="不一一对应", reason="subsection_coverage_mismatch")
    extra = _draft_payload(
        manifest=manifest, citations_a=("m01",), citations_b=("m02",),
        extra_subsection=[{"subsection_id": "sub-extra", "title": "多出来的",
                           "paragraphs": []}])
    _expect_error(lambda: _parse(extra, manifest=manifest),
                  token="不一一对应", reason="subsection_coverage_mismatch")
    details.append("NOTE §4：少一个小节与多一个小节**都**拒——「模型漏写」与「本来没有内容」"
                   "必须可区分。")
    # 反例 4c：**集合相同、顺序不同**。顺序是**另一条**等式：提示词第 1 条硬约束要求逐一
    # 对应，下游的栏目读回、缺口定位、人读页也全是按位置解释这份草稿的。集合比较吸收不了它。
    # 主夹具只有一个小节，换序无从发生，所以这里**另建一份两小节清单**（同一套材料/事实）。
    two_specs = _subsections() + (CW.CitedSubsectionSpec(
        subsection_id="sub-services", title="服务", requirement_text=_REQ_TEXT,
        declared_aspect_ids=(_ASPECT,)),)
    two = CW.build_cited_writer_input(
        authority=authority, material_context=context, subsections=two_specs, facts=facts,
        section_title="主营业务")
    in_order = json.loads(_draft_payload(manifest=two, citations_a=("m01",),
                                         citations_b=("m02",)))
    check([s["subsection_id"] for s in in_order["subsections"]]
          == [s.subsection_id for s in two.subsections],
          "**正例**：两小节的草稿**按请求顺序**写 ⇒ 通过（这是提示词第 1 条硬约束的形状）")
    swapped_body = {**in_order, "subsections": list(reversed(in_order["subsections"]))}
    outcomes: dict[str, object] = {"swapped": None}
    try:
        _parse(json.dumps(swapped_body, ensure_ascii=False), manifest=two)
    except CW.CitedWriterError as exc:  # noqa: BLE001 - 这里要的就是那条 typed 原因
        outcomes["swapped"] = exc
    check(outcomes["swapped"] is not None
          and getattr(outcomes["swapped"], "reason", "") == "subsection_order_mismatch",
          "**反例**：小节集合相同、**顺序不同** ⇒ 拒（`subsection_order_mismatch`）"
          "——不再声明同序却先排序后比较")
    check(outcomes["swapped"] is not None
          and str(getattr(outcomes["swapped"], "reason", "")) != "subsection_coverage_mismatch",
          "⇒ 这条与「少一个/多一个」用**不同**的原因码：把换序记成缺件，读回侧会去找一个"
          "并不缺的小节")
    details.append("NOTE §4b：顺序这条等式此前**没有代码在判**——`sorted(asked) != sorted(got)` "
                   "先把顺序信息丢掉了，于是「同序」只是提示词里的一句话。")

    # ============================================================ §5 采用去向
    details.append("## §5 采用去向按**句引用**记，`not_used` 不是缺口")
    adoptions = CW.derive_material_adoptions(draft=ok, manifest=manifest)
    check(len(adoptions) == len(manifest.materials),
          f"每个清单成员**恰一条**采用记录（实测 {len(adoptions)} 条 / "
          f"{len(manifest.materials)} 份材料）")
    by_material = {a.material_id: a for a in adoptions}
    check(by_material[_MAT_A].sentence_ids == ("s0001",)
          and by_material[_MAT_B].sentence_ids == ("s0002",),
          "被采用的成员逐条记下**是哪几句（最终编号）**引用了它"
          "（不是「用过/没用过」两态）")
    check(all(a.disposition == "adopted" for a in adoptions),
          "本次两段正文各引一份材料，两条都是 `adopted`")

    # 反例 5a：一份材料完全没被引用 ⇒ `not_used`，且**不**产生缺口
    unused = _parse(_draft_payload(manifest=manifest, citations_a=("m01",),
                                   citations_b=("m01",)), manifest=manifest)
    unused_adoptions = CW.derive_material_adoptions(draft=unused, manifest=manifest)
    unused_row = next(a for a in unused_adoptions if a.material_id == _MAT_B)
    check(unused_row.disposition == "not_used" and unused_row.sentence_ids == (),
          "没被任何一句引用的成员 ⇒ `not_used`（带空引用）")
    check(unused.gaps == (),
          "`not_used` **不是**缺口：空闲的材料不产生 `CitedProseGap`")
    details.append("NOTE 反例 5a：未采用 ⇒ `not_used` + 空引用；缺口清单**保持不变**"
                   "（拒绝/空闲 ≠ 缺口）。")

    # 反例 5b：跨清单记账必须拒（题目不同 ⇒ 清单身份不同 ⇒ 不得混账）
    same_again = _input_manifest(authority=authority, facts=facts)
    check(same_again.manifest_id == manifest.manifest_id,
          "同一份输入两次构造必须得到**同一个** manifest_id（内容寻址）")
    other_context = T._writer_material_context(authority.pack_set,
                                               task_id=str(task.task_id),
                                               section_id="company")
    other_manifest = CW.build_cited_writer_input(
        authority=authority, material_context=other_context, subsections=_subsections(),
        facts=facts, section_title="另一节标题")
    check(other_manifest.manifest_id != manifest.manifest_id,
          "换一个题目 ⇒ 清单身份必须不同（否则两份不同的输入会共用一个身份）")
    _expect_error(
        lambda: CW.validate_citations(draft=unused, manifest=other_manifest),
        token="不是本次清单", reason="draft_manifest_mismatch")
    details.append("NOTE 反例 5b：把 A 清单的草稿拿去 B 清单记账 ⇒ "
                   "`draft_manifest_mismatch`（采用去向不得跨清单）。")

    # ============================================================ §6 一处错误不清零整节
    details.append("## §6 一处无引用的句子不清零整节")
    uncited = json.dumps({"subsections": [{
        "subsection_id": manifest.subsections[0].subsection_id,
        "title": manifest.subsections[0].title,
        "paragraphs": [{"paragraph_id": "p1", "sentences": [
            {"sentence_id": "s1", "text": _BODY_A, "citations": []},
            {"sentence_id": "s2", "text": _BODY_B, "citations": ["m02"]}]}]}],
        "gaps": [], "follow_up_needs": []}, ensure_ascii=False)
    draft_uncited = _parse(uncited, manifest=manifest)
    check(len(draft_uncited.sentences()) == 2,
          "含一条**无引用**句子的返回仍构造成功（空引用在 wire 层合法）")
    check(draft_uncited.sentences()[0].citations == ()
          and draft_uncited.sentences()[1].citations == ("m02",),
          "无引用只落在**那一条**句子上，同段另一句的引用原样保留")
    check(CW.derive_material_adoptions(draft=draft_uncited, manifest=manifest)[0]
          .disposition == "not_used",
          "无引用句不冒充采用：该成员如实记为 `not_used`")
    details.append("NOTE §6：这条无引用句由**第二步**的逐句核对判 `uncited_sentence` "
                   "——它在结构层是可表达的，因此一处错误不会让整节不可读。")

    # ============================================================ §7 往返与身份
    details.append("## §7 往返与身份重算")
    back = CW.CitedWriterInputManifest.from_dict(
        json.loads(json.dumps(manifest.to_dict(), ensure_ascii=False)))
    check(back.manifest_id == manifest.manifest_id
          and back.fingerprint() == manifest.fingerprint(),
          "输入清单 `to_dict` → `from_dict` 身份完全还原")
    check([m.reading_view for m in back.materials]
          == [m.reading_view for m in manifest.materials],
          "往返后**正文本体**逐字节不变（读者面与复核面读同一串字节）")
    back_draft = CW.CitedProseDraft.from_dict(
        json.loads(json.dumps(ok.to_dict(), ensure_ascii=False)))
    check(back_draft.draft_id == ok.draft_id
          and back_draft.fingerprint() == ok.fingerprint(),
          "正文草稿 `to_dict` → `from_dict` 身份完全还原")
    _expect_error(
        lambda: CW.CitedProseDraft(
            draft_id="cwd_tampered", schema_version=CW.CITED_WRITER_DRAFT_SCHEMA_VERSION,
            task_id=ok.task_id, section_id=ok.section_id,
            input_manifest_id=ok.input_manifest_id, writer_identity=ok.writer_identity,
            subsections=ok.subsections, gaps=ok.gaps,
            follow_up_needs=ok.follow_up_needs),
        token="draft_id 与内容不符", reason="draft_id_mismatch")
    _expect_error(
        lambda: CW.CitedWriterInputManifest(**{**{
            f: getattr(manifest, f)
            for f in ("manifest_id", "schema_version", "task_id", "section_id",
                      "section_title", "policy_version", "pack_set_fingerprint",
                      "material_manifest_id", "material_manifest_fingerprint",
                      "material_context_id", "material_context_fingerprint",
                      "subsections", "materials", "facts")},
            "section_title": "被篡改的标题"}),
        token="manifest_id 与内容不符", reason="manifest_id_mismatch")
    details.append("NOTE §7：改一个字节 ⇒ 身份重算必然不符（内容寻址，不是自报字段）。")

    # ============================================================ §8 请求面
    details.append("## §8 请求面：模型看到的正文 == 复核者看到的正文")
    request = CW.build_cited_prose_request(manifest=manifest)
    check([row["text"] for row in request["materials"]]
          == [m.reading_view for m in manifest.materials],
          "请求面 `materials[].text` 就是清单里的读视图**本体**（不摘要、不改写）")
    check([row["key"] for row in request["materials"]] == list(mat_keys),
          "请求面的键与清单键逐条相同（模型唯一的回指手段）")
    check(all("source_role" in row for row in request["materials"]),
          "来源角色随每行进入请求面（否则「哪份是旧年报」只能靠文件名猜）")
    #: `cp-9`（`cp-10` 沿用）：来源**文档身份**也随每行进入请求面，且逐行与清单本体相同——
    #: 第 3c 条要模型「先按 `document_id` 分组、再分开判来源角色与事实适用期间」，模型就得在
    #: 动笔前看得见「这几行来自同一份文档」。注意这一列**只管分组**：它不带期间（`cp-10` 明说
    #: 不得由 `document_id` 推测期间），取值仍取自清单同一字段，不是另算的一列。
    check([row["document_id"] for row in request["materials"]]
          == [m.document_id for m in manifest.materials],
          "来源文档身份随每行进入请求面，且与清单**同一字段**（逐行相同，不是另行推断的一列）")
    check([row["requirement_text"] for row in request["subsections"]] == [_REQ_TEXT],
          "Contract 的**逐字**要求文本进入请求面")

    # ============================================================ §9 缺口词表
    details.append("## §9 缺口的封闭词表")
    gap = CW.CitedProseGap.create(subsection_id="sub-products", requirement_text=_REQ_TEXT,
                                  reason="no_source_in_manifest", detail="本次输入无该来源")
    check(gap.gap_id.startswith("cgap_"), "缺口身份内容寻址（`cgap_`）")
    _expect_error(
        lambda: CW.CitedProseGap.create(subsection_id="s", requirement_text="r",
                                        reason="没拿到", detail=""),
        token="不在封闭词表", reason="gap_reason_unknown")

    # ============================================================ §10 三个新轴
    details.append("## §10 第二步要用的三个轴：文档身份 / 结构化读视图 / 内容形态")
    import dataclasses as _dc

    from sections import material_context as MC

    base = manifest.materials[0]
    check(bool(base.document_id) and bool(base.source_role),
          f"文档系列材料带 `document_id`（实测 {base.document_id!r}）与来源角色："
          "逐句判「旧材料是否被写成当前状态」靠的是这一轴，不是文件名")
    check(base.document_id in str(base.locator_ref.get("owner", "")),
          "文档身份**就出自**这条材料自己的定位容器（不是另行推断出来的一个名字）")

    # 结构化读视图：它在**不**进身份体的前提下仍被读视图指纹钉住（改一格必被抓）
    cells = {"cells": [{"row_index": 0, "column_index": 0, "row_label": "营业收入",
                        "column_header": "本期发生额", "value_text": "100.00",
                        "cell_locator": NS.table_cell_locator(
                            "evidence_document:doc-demo-1@v1#table", table_ref="tobj-1",
                            row_index=0, column_index=0)}]}
    forged = MC._reading_view_fingerprint(
        payload_hash=base.payload_hash, object_type=base.material_type,
        reading_view=base.reading_view, structured_view=cells)
    table_entry = _dc.replace(base, structured_view=cells, reading_view_fingerprint=forged)
    check(table_entry.structured_view == cells and table_entry.content_kind == "",
          "表材料的结构化读视图可以随清单一起进 Writer 输入面并落盘可回放")
    check("structured_view" not in table_entry.identity_body()
          and "reading_view" not in table_entry.identity_body(),
          "结构化层与正文本体都**不进**身份体（只进它们的指纹：身份不含全文，"
          "否则每一次 id 重算都要哈希全库）")
    check(table_entry.identity_body() != base.identity_body(),
          "⇒ 但指纹在体内，改一格结构化层照样换身份")
    _expect_error(
        lambda: _dc.replace(base, structured_view=cells),
        token="读视图", reason="reading_view_forged")
    details.append("NOTE 反例 10a：只改结构化层、留着旧指纹 ⇒ `reading_view_forged`"
                   "（本类当场重算，不把声明当事实）。")

    request_with_table = CW.build_cited_prose_request(
        manifest=_rebuild(manifest, (table_entry, *manifest.materials[1:])))
    check(request_with_table["materials"][0]["is_table"] is True
          and request_with_table["materials"][1]["is_table"] is False,
          "「这一行是不是表」在**请求面**可读（模型不必靠正文长相猜）")

    # 内容形态：勾选表单行必须带结构化读法，且未在词表内的形态一律拒
    form = _dc.replace(base, content_qualification={
        "kind": "selection_form", "is_material": True,
        "selection": {"question": "是否属于主营业务", "selected": ["是"]}})
    check(form.content_kind == "selection_form",
          "勾选表单行的形态读法可进清单（`content_kind` 可读）")
    _expect_error(
        lambda: _dc.replace(base, content_qualification={"kind": "selection_form"}),
        token="selection", reason="content_qualification_selection_missing")
    _expect_error(
        lambda: _dc.replace(base, content_qualification={"kind": "business_prose"}),
        token="封闭词表", reason="content_qualification_kind_unknown")
    details.append("NOTE 反例 10b：形态名不在封闭词表内、或表单行缺结构化读法 ⇒ 各自 typed 拒收。")

    # 文档身份是**身份轴**：改它必换清单身份（与结构化层相反）
    renamed = _rebuild(manifest, (_dc.replace(base, document_id="doc-other"),
                                  *manifest.materials[1:]))
    check(renamed.identity_body() != manifest.identity_body(),
          "改 `document_id` ⇒ 换清单**身份体**（它是身份轴，不是读法；"
          "身份体一变，`create()` 算出的 manifest_id 必变）")
    check(renamed.manifest_id != manifest.manifest_id,
          "⇒ 同一份清单换了身份，读回时旧 id 不再被接受")
    details.append("NOTE §9：缺口原因取自封闭四值词表，不得塞进泛化字符串。")

    # ============================================================ §11 失败前留存
    details.append("## §11 失败前留存口：留存**不改变**成败判定，也**不吞**异常")
    import tempfile as _tempfile

    from sections import cited_call_journal as CCJ

    class _StubProseClient:
        """最小替身：回放一段真实形状的返回，或按需直接抛（模拟客户端自己没有可见回复）。"""

        model = "stub-model"
        thinking = {"type": "disabled"}

        def __init__(self, *, text="", boom: Exception | None = None):
            self.text = text
            self.boom = boom

        def compose(self, *, messages, system, prompt_version, model_policy):
            if self.boom is not None:
                raise self.boom
            return CW.CitedProseResult(
                text=self.text, call_id="call-stub", model=self.model,
                prompt_version=prompt_version, status="ok", input_tokens=11,
                output_tokens=22, latency_ms=33, finish_reason="end_turn", error="")

    good_reply = _draft_payload(manifest=manifest, citations_a=("m01",), citations_b=("m02",))
    _messages, _ = CW.build_cited_prose_messages(manifest=manifest, system="sys")
    with _tempfile.TemporaryDirectory() as _tmp:
        journal = CCJ.journal_for(Path(_tmp) / "section")
        outcome = CW.write_cited_section(
            manifest=manifest, client=_StubProseClient(text=good_reply), journal=journal)
        body = journal.load()
        check(body["input"]["manifest_id"] == manifest.manifest_id
              and body["input"]["request_face"] == _messages[0]["content"],
              "留存里的请求面**逐字等于**真正发出去的那一份（不是重新拼的摘要）")
        check(body["reply"]["text"] == good_reply
              and body["reply"]["visible_sha256"],
              "⇒ 可见回复在**解析之前**落盘，且带自己的哈希")
        check(body["parse"]["ok"] is True
              and body["parse"]["draft_id"] == outcome.draft.draft_id
              and body["parse"]["sentence_count"] == len(outcome.draft.sentence_ids()),
              "⇒ 解析成功后草稿身份与句数进留存（成功后同样可回查）")
        check(body["parse"]["normalization"]["sentence_ids"][0]["model_id"] == "s1"
              and body["parse"]["normalization"]["sentence_ids"][0]["final_id"] == "s0001",
              "⇒ 模型原 ID / 结构位置 / 最终 ID 的映射随留存落盘"
              "（它**不**进草稿身份：内容寻址不依赖模型当初的编号）")
        check(body["publishable"] is False, "⇒ 留存内容恒标**不可发布**")

    class _Boom(RuntimeError):
        call_id = "call-boom"
        finish_reason = "max_tokens"

    with _tempfile.TemporaryDirectory() as _tmp:
        journal = CCJ.journal_for(Path(_tmp) / "section")
        escaped = ""
        try:
            CW.write_cited_section(manifest=manifest,
                                   client=_StubProseClient(boom=_Boom("截断")),
                                   journal=journal)
        except _Boom as exc:
            escaped = str(exc)
        except Exception as exc:  # noqa: BLE001 - 换了类型就说明留存把异常换掉了
            escaped = f"WRONG:{type(exc).__name__}"
        check(escaped == "截断",
              "客户端自己抛的那条路：**原异常原样逃逸**（留存不吞、不换类型）")
        body = journal.load()
        check(body["input"] is not None and body["reply"]["status"] == "client_error"
              and "截断" in body["reply"]["error"],
              "⇒ 同一份留存里仍写明「这一轮死在收到回复之前」"
              "（否则事后只能从 `logs/llm` 反推，那正是 r2 缺失的那一半）")
        check(body["reply"]["text"] == "" and body["parse"] is None,
              "⇒ 没有可见回复就不伪造一段正文，也不假装解析发生过")

    details.append("NOTE §11：留存是**证据**；证据写不进去是证据的问题，不得顶掉原异常。")

    # ============================================================ §12 提示词资产与版本对账
    details.append("## §12 提示词资产头声明的修订号必须与 `prompt_version` 对得上")
    real = CW.load_cited_writer_prompt()
    declared = CW.declared_prompt_identity(real)
    check(declared == (CW.CITED_WRITER_PROMPT_ASSET, CW.CITED_WRITER_PROMPT_REVISION),
          "**正例**：仓内资产第 1 行声明的 `（asset，revision）` 与本链常量**逐字相同**"
          "（加载即对账：加载器不通过就抛）")
    check("临时" in real and "全节唯一" in real and "由程序" in real,
          "⇒ 资产里 `sentence_id` 的措辞是「**临时** ID 非空即可、最终**全节唯一**编号**由程序**"
          "生成」——提示词承诺的东西与程序实际依赖的东西是同一件")
    check(CW.declared_prompt_identity("你是撰写者。\n没有任何声明。") is None,
          "**反例**：资产头没有声明句式 ⇒ 取不到身份（不是「版本对得上」）")
    import llm.client as _llm
    _original_load = _llm.load_prompt
    try:
        _llm.load_prompt = lambda name: (
            f"你是撰写者（{CW.CITED_WRITER_PROMPT_ASSET}，revision cp-999）。")
        _expect_error(lambda: CW.load_cited_writer_prompt(),
                      token="不对应任何字节", reason="prompt_asset_identity_mismatch")
        _llm.load_prompt = lambda name: "没有声明的资产头。"
        _expect_error(lambda: CW.load_cited_writer_prompt(),
                      token="没有声明", reason="prompt_asset_identity_missing")
    finally:
        _llm.load_prompt = _original_load
    check(CW.declared_prompt_identity(real) == declared,
          "⇒ 还原后仍读的是真资产（上面两次替换只活在 try 里）")
    details.append("NOTE §12：这两条**不**替断言某个字面版本号——断言的是「账本上的 "
                   "`prompt_version` 一定对应某份真实字节」这条关系。")

    #: --- 术语守卫（`cp-26` 同版收尾）：一个词不能同时指两件相反的事
    #: 本版第 103 行把「空段落」逐字定义成 `"sentences": []` 的**硬错误**，因此这份资产里
    #: **任何**一处「空段落」都只能出现在**否定**语境里，绝不能是一条「去写空段落」的指令。
    #: 旧稿（`cp-19`）里同一个词指的是 `paragraphs: []`（那是**允许**的），一个词指两件相反
    #: 的事，正是 M930-5 真实 run 里 `p10` 空壳段的最可能成因。钉住这条：以后改提示词时，
    #: 要么把它写成否定、要么换一个词。
    _blank_lines = [ln for ln in real.splitlines() if "空段落" in ln]
    _negations = ("不是", "不要", "不许", "不得", "不应", "勿", "禁止")
    check(bool(_blank_lines)
          and all(any(neg in ln for neg in _negations) for ln in _blank_lines),
          f"⇒ 资产里 {len(_blank_lines)} 处「空段落」**全部**在否定语境里——"
          "这个词在本版是**硬错误**的名字，不是一条可执行的指令")
    check('"sentences": []' in real and "`paragraphs` 写成 `[]`" in real
          and "整段不生成" in real,
          "⇒ 被禁止的形态（`\"sentences\": []`）与正确的替代做法（那一段**整段不生成**／"
          "把该小节的 `paragraphs` 写成 `[]`）在资产里都逐字写着——"
          "「两者都没有」时写什么，答案在资产里是**唯一**的")

    # ============================================ §13 跨块切分相邻关系（`cwm-7`）
    details.append("## §13 跨块切分的相邻关系看得见，但不合并两片")
    #: 同一段原文被来源块边界切成的两片：正文各是各的，片序与区间来自信封。
    piece1 = _split_material(material_id="mat-p1", reading_view="公司拥有独立的研发、",
                             span_id="os-1", piece_index=1, piece_count=2,
                             span_char_length=40, local_range=[0, 20])
    piece2 = _split_material(material_id="mat-p2", reading_view="采购、生产和销售体系。",
                             span_id="os-1", piece_index=2, piece_count=2,
                             span_char_length=40, local_range=[21, 40])
    check(piece1.span_continuity == {
        "span_id": "os-1", "piece_index": 1, "piece_count": 2, "span_char_length": 40,
        "span_local_char_range": [0, 20], "continued_from": None, "continued_in": None},
        "**正例**：被切过的材料带续接读法（片序 / 共片数 / 全文长度 / 本片区间逐字来自信封）")
    check(piece1.reading_view == "公司拥有独立的研发、" and piece2.reading_view == "采购、生产和销售体系。",
          "⇒ 两片的正文字面**各是各的**：续接不合并、不改写、不拼接")
    MC._link_span_pieces([piece1, piece2])
    check(piece1.span_continuity["continued_in"] == piece2.member_ref
          and piece2.span_continuity["continued_from"] == piece1.member_ref,
          "**正例**：同 `span_id` 的两片按片序串起来，前/后片 ref 互为对方（按已固定成员集合解析）")
    check(MC._link_span_pieces([piece1]) is None,
          "⇒ 只有一片在清单里时不伪造邻居（`continued_*` 保持 None——那是结论，不是缺失）")

    # 反例：片序形状读不懂就 fail-closed，不降级成「普通整段材料」。
    for name, payload, token in (
            ("共片数 < 2", {"span_id": "s", "piece_index": 1, "piece_count": 1,
                            "span_char_length": 10, "span_local_char_range": [0, 10]},
             "片序不成立"),
            ("片序越界", {"span_id": "s", "piece_index": 5, "piece_count": 3,
                          "span_char_length": 10, "span_local_char_range": [0, 10]},
             "片序不成立"),
            ("区间反向", {"span_id": "s", "piece_index": 1, "piece_count": 2,
                          "span_char_length": 10, "span_local_char_range": [9, 3]},
             "本地区间不成立"),
            ("区间越界", {"span_id": "s", "piece_index": 1, "piece_count": 2,
                          "span_char_length": 10, "span_local_char_range": [0, 11]},
             "本地区间不成立"),
            ("缺 span_id", {"span_id": "", "piece_index": 1, "piece_count": 2,
                            "span_char_length": 10, "span_local_char_range": [0, 5]},
             "缺 span_id")):
        try:
            MC._continuity_from_split(payload, member_ref="m")
        except MC.MaterialContextError as exc:
            check(token in str(exc), f"**反例**（{name}）：续接读法读不懂 ⇒ fail-closed（{token}）")
        else:
            raise AssertionError(f"{name} 竟然通过了：它会被读成「没有续接」")

    # 派生字段的纪律：进序列化、不进身份体；往返不丢。
    entry = CW.CitedMaterialEntry(
        citation_key="m01", member_ref=piece1.member_ref, pack_id="pack-1",
        material_id="mat-p1", topic_id="topic-x", material_type="evidence_span",
        source_identity="evidence:ev-1", provenance_identity="prov-mat-p1",
        source_role="current_state_source", locator_ref=piece1.locator_ref,
        payload_hash="a" * 64, reading_view=piece1.reading_view,
        reading_view_fingerprint=piece1.reading_view_fingerprint,
        span_continuity=dict(piece1.span_continuity))
    check("span_continuity" in entry.to_dict() and entry.to_dict()["span_continuity"],
          "⇒ 续接读法**进序列化**（读者与复核面都看得见相邻关系）")
    check("span_continuity" not in entry.identity_body(),
          "⇒ 续接读法**不进身份体**（同一片原文换了个「看得见邻居」的读法，不是换了一份清单）")
    back = CW._material_from_dict(entry.to_dict())
    check(back.span_continuity == entry.span_continuity
          and back.identity_body() == entry.identity_body(),
          "⇒ 往返逐字段还原，且身份体逐字节不变")

    # 请求面：模型那一侧真的看得到续接关系。
    request_rows = CW.build_cited_prose_request(
        manifest=CW.CitedWriterInputManifest.create(
            task_id="t", section_id="company", section_title="主营业务",
            pack_set_fingerprint="a" * 64, material_manifest_id="wmm_x",
            material_manifest_fingerprint="b" * 64, material_context_id="wmctx_x",
            material_context_fingerprint="c" * 64, subsections=_subsections(),
            materials=(entry,), facts=()))["materials"]
    check(request_rows and request_rows[0].get("span_continuity") is not None,
          "⇒ **请求面**每一行带续接读法（非空表示与相邻行同属一段原文；两片仍是两行、两个 locator）")
    details.append("NOTE §13：续接只**陈述关系**；`text` / `locator` 仍是本片自己的——"
                   "把两片说成一个位置就是伪造 locator。")

    # ============================================ §14 材料行的**呈现序**（`cp-18`）
    details.append("## §14 材料按来源角色/文档分组呈现——但一行都不丢、一行都不改")
    #: 机制：清单行序此前就是**身份序**（hash 序），文档与角色在列表里交错；一份长年报的
    #: 业务整段可能落在几十行之末。`material_presentation_order` 只**重排**，不改行内容、
    #: 不裁行。这里钉的是「重排」这件事的三条性质，**不**钉任何具体顺序的字面。

    def _mat(key: str, *, role: str, doc: str, text: str) -> CW.CitedMaterialEntry:
        payload_hash = "a" * 64
        return CW.CitedMaterialEntry(
            citation_key=key, member_ref=NS.manifest_member_ref("pack-1", key),
            pack_id="pack-1", material_id=key, topic_id="topic-x",
            material_type="evidence_span",
            source_identity="evidence:ev-1", provenance_identity=f"prov-{key}",
            source_role=role,
            locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40),
            payload_hash=payload_hash, reading_view=text,
            reading_view_fingerprint=MC._reading_view_fingerprint(
                payload_hash=payload_hash, object_type="evidence_span",
                reading_view=text, structured_view=None),
            document_id=doc)

    #: 输入按 **hash 序**（身份序）给：文档与角色交错，短行在前、长行在后。
    identity_order = (
        _mat("m01", role="history_and_conflict_source", doc="doc-old", text="上年情况。"),
        _mat("m02", role="current_state_source", doc="doc-new", text="本期第一段。"),
        _mat("m03", role="history_and_conflict_source", doc="doc-old", text="上年另一段，长一些。"),
        _mat("m04", role="current_state_source", doc="doc-new", text="本期第二段，明显更长的一段正文。"),
        _mat("m05", role="current_state_source", doc="doc-mid", text="另一份文档。"),
        _mat("m06", role="", doc="", text="没有来源角色的材料（空串是结论，不是缺失）。"),
    )
    ordered = CW.material_presentation_order(identity_order)
    check(len(ordered) == len(identity_order),
          f"**不丢行**：重排后行数不变（{len(ordered)} == {len(identity_order)}）")
    check([e.citation_key for e in ordered] != [e.citation_key for e in identity_order],
          "前提：这组输入在**身份序**下确实不是呈现序（否则本测试什么也没测到）")
    check(sorted(e.material_id for e in ordered)
          == sorted(e.material_id for e in identity_order),
          "**不丢行、不换行**：重排前后是同一批 `material_id`（只是顺序不同）")
    original_by_key = {e.citation_key: e for e in identity_order}
    for entry in ordered:
        source = original_by_key[entry.material_id]
        check(entry.identity_body() == source.identity_body(),
              f"{entry.material_id}：重排**不碰行内容**（身份体逐字节不变——"
              "换的只是它排在第几个，不是它是什么）")
    check(CW.material_presentation_order(ordered) == ordered,
          "**幂等**：对已排好的序列再排一次，结果逐行相同")
    check(CW.material_presentation_order(identity_order)
          == CW.material_presentation_order(identity_order),
          "**确定性**：同一份输入任何时候排出同一个序")

    roles = [e.source_role for e in ordered]
    check(roles.index("history_and_conflict_source") > max(
              i for i, r in enumerate(roles) if r == "current_state_source"),
          "**分组性**：当前态来源整块排在历史来源**之前**——历史来源按角色判据本来就"
          "不能单独表达当前状态，摆到最前只会诱导生成器拿旧料起头")
    check(all(r == "current_state_source" for r in roles[:3]),
          "**同组连续**：当前态来源的三行连成一块，没有被别的角色插断")
    seen_docs: list[str] = []
    for role, doc in zip(roles, [e.document_id for e in ordered]):
        if role != "current_state_source" or not doc:
            continue
        if not seen_docs or seen_docs[-1] != doc:
            seen_docs.append(doc)
    check(seen_docs == sorted(seen_docs) and len(seen_docs) == len(set(seen_docs)),
          f"**同文档连续**：同一 `document_id` 的材料连成一块（实测块序 {seen_docs}）"
          "——提示词第 3c 条要求「先按文档分组」，这一步就是把它做出来")
    check(ordered[-1].source_role == "",
          "**未知/空角色排到表尾**：不在呈现序词表内的角色一律后置，"
          "按角色名字典序兜底（仍是全序，不因此报错——角色是否合法由 `srsc-1` 那条轴判）")

    #: 键的定义本就是「按**清单序**连续编号」（`_check_key_order`），因此重排必须重编键，
    #: 否则键就不再能当回指手段用。这里按 `build_cited_writer_input` 的做法显式重编一次，
    #: 证明「重编后仍是合法清单、且请求面与清单一序」。
    rekeyed = tuple(_dc.replace(e, citation_key=CW.cited_material_key(i))
                    for i, e in enumerate(ordered))
    reordered_manifest = _rebuild(manifest, rekeyed)
    keys = [m.citation_key for m in reordered_manifest.materials]
    check(keys == [CW.cited_material_key(i) for i in range(len(keys))],
          f"**键按新序连续重编**（实测 {keys}）——重排却不重编键，"
          "键就不再能当回指手段用")
    check([m.material_id for m in reordered_manifest.materials]
          == [e.material_id for e in ordered],
          "⇒ 重编键**不改行序**：清单仍按呈现序排列（换的是键，不是内容也不是次序）")
    request_rows = CW.build_cited_prose_request(manifest=reordered_manifest)["materials"]
    check([r["key"] for r in request_rows] == keys
          and [r["ref"] for r in request_rows]
          == [m.member_ref for m in reordered_manifest.materials],
          "**请求面与清单一序**：模型看到的行序 == 清单行序（不是各排一次）")
    #: 真实 `build_cited_writer_input` 的产物本身就是该方法的不动点——否则「请求面按呈现序」
    #: 就得靠调用方自觉，而不是靠构造口保证。
    check(tuple(manifest.materials) == CW.material_presentation_order(manifest.materials),
          "**不动点**：`build_cited_writer_input` 造出的清单已满足呈现序"
          "（再排一次逐行相同）")
    details.append("NOTE §14：呈现序是**可用性排序**，不是相关性判断——它只决定模型"
                   "先看到哪一片，不裁行、不改行内容、不放松任何一条判据。"
                   "「正文写得对不对」仍由逐句核对与独立审阅判定。")

    # ============================================ §15 阅读顺序提纲（`cp-20`，`co-1`）
    details.append("## §15 `content_outline`：把业务导航线摊成看得见的几步——但它不是门")
    #: 动因（v2_r1 真实公司节）：这一节 18 个 Contract 栏里**没有一栏叫「经营表现」**，
    #: `cp-19` 在提示词里发明的「业务经营表现」收尾步**没有栏位锚点**，于是换电生态、储能
    #: 销量、海外与售后、巧克力换电、低空·船舶·数据中心这一整批当期经营正文（约 3 千字）
    #: 被整体跳过。这里钉的是：提纲把那一步**锚到 `main_business` 栏**，且**只看栏位身份**。

    _business_aspects = (
        "company_business_main.main_business",
        "company_business_main.products_solutions",
        "company_business_main.app_scenarios",
        "company_business_main.revenue_breakdown",
        "company_business_main.cost_gross_margin",
        "company_business_main.industry_chain_position",
        "company_business_main.period_unit_caliber",
        "company_business_model.procurement_mode",
        "company_business_model.production_mode",
        "company_business_model.sales_mode",
        "company_business_model.tech_route",
        "company_business_model.cost_structure",
        "company_business_model.cost_competitiveness",
        "company_customer_concentration.customer_current_concentration",
        "company_customer_concentration.customer_concentration_change",
        "company_customer_concentration.customer_anonymity",
        "company_supplier_concentration.supplier_current_concentration",
        "company_supplier_concentration.supplier_concentration_change",
    )

    def _outline_spec(aspects, *, title="主营业务"):
        #: 逐栏的 `requirement_text`：这里用栏位身份后缀充当，只为满足
        #: `requirement_lines_by_aspect` 的**位置对位**（行数 == 栏数），不是业务文案。
        lines = [a.rsplit(".", 1)[-1] for a in aspects]
        return CW.CitedSubsectionSpec(
            subsection_id="sub-outline", title=title,
            requirement_text="\n".join(lines), declared_aspect_ids=tuple(aspects))

    #: **正例**：真实夹具小节声明的就是 `main_business` ⇒ 请求面真的带上了提纲，
    #: 且提纲把那一步锚在该栏上。
    face_sub = CW.build_cited_prose_request(manifest=manifest)["subsections"][0]
    outline = face_sub.get("content_outline")
    check(isinstance(outline, dict)
          and outline.get("outline_version") == CW.CONTENT_OUTLINE_VERSION,
          f"**正例**：请求面带上版本化提纲（{CW.CONTENT_OUTLINE_VERSION}）"
          "——它是写作组织，只摆在模型那一侧")
    check([st["step_id"] for st in outline["steps"]] == outline["reader_order"],
          "**次序可读**：`reader_order` 与 `steps` 的次序逐项一致（不是一个另排的副本）")
    check([a for st in outline["steps"] for a in st["aspect_ids"]] == [_ASPECT]
          and outline["unmapped_aspect_ids"] == [],
          "⇒ 只声明一栏时，提纲就一步、一栏，`unmapped_aspect_ids` 为空（空是结论）")

    #: **18 栏不重不漏**：真实公司小节声明的 18 栏落在 10 步里，且**互不相交**。
    full = CW.build_content_outline(_outline_spec(_business_aspects))
    covered = [a for st in full["steps"] for a in st["aspect_ids"]]
    check(sorted(covered) == sorted(_business_aspects),
          f"**不重不漏**：18 栏恰好被 {len(full['steps'])} 步覆盖一次"
          "（漏一栏或重一栏都会让「这一步落到哪几栏」变成一句无法核对的话）")
    check(full["unmapped_aspect_ids"] == [],
          "⇒ 业务小节 18 栏全部落在提纲里")
    #: 每一步的 `requirement_texts` 逐字来自**本小节自己的**要求行，不是另写的文案。
    lines_by_aspect = CW.requirement_lines_by_aspect(_outline_spec(_business_aspects))
    check(all(st["requirement_texts"] == [lines_by_aspect[a] for a in st["aspect_ids"]]
              for st in full["steps"]),
          "⇒ 每一步的 `requirement_texts` 逐字取自本小节自己的要求行（不另造措辞）")

    #: **收尾步的锚点**就是 `cp-20` 要修的那件事：那批当期经营正文本无栏位可落。
    tail = [st for st in full["steps"]
            if st["step_id"] == "main_business_and_performance"]
    check(len(tail) == 1
          and tail[0]["aspect_ids"] == ["company_business_main.main_business"]
          and tail[0]["guidance"],
          "**收尾步有锚点**：「报告期内经营表现」落在 `main_business` 一栏上，"
          "并带一句写作提示——`s0015` 那种「把客户合作名录挂到销售模式栏」正是它缺锚点时的形状")

    #: **反例**：财务小节声明的栏与这张业务提纲**一条都不沾** ⇒ **不发**该字段。
    fin = _outline_spec(("fin_solvency.net_asset_level", "fin_solvency.rigid_debt_structure"),
                        title="偿债能力")
    check(CW.build_content_outline(fin) is None,
          "**反例**：财务小节不发业务提纲（`None`）——`null` 是结论（这一节不适用这张提纲），"
          "不是「读取失败」，那种小节按第 12b 条写")

    #: **反例（半匹配）**：几条沾、几条不沾 ⇒ **照发**，未落步的栏原样列进 `unmapped_aspect_ids`
    #: （照常可写，只是没有阅读顺序建议）——「对不上」与「对上了」在产物上不能长得一样。
    mixed = CW.build_content_outline(_outline_spec(
        ("company_business_main.products_solutions", "fin_solvency.interest_expense_proxy")))
    check(mixed is not None
          and mixed["unmapped_aspect_ids"] == ["fin_solvency.interest_expense_proxy"],
          "**反例（半匹配）**：沾边的栏进步骤，不沾的栏进 `unmapped_aspect_ids`"
          "——既不报错、也不隐藏、更不留缺口")

    #: **不是门**：提纲只读 `spec`（栏位身份 + 要求行），**不接触**任何材料/事实，
    #: 因此不可能改变任何一条资格判定；且可复现（同一 spec 任何时候排出同一份）。
    check(CW.build_content_outline(_outline_spec(_business_aspects)) == full,
          "**确定性**：同一份 spec 任何时候排出同一份提纲")
    text = json.dumps(full, ensure_ascii=False)
    check("citation" not in text and "m0" not in text and "m1" not in text
          and "页" not in text and "%" not in text,
          "**不含材料/数字字面量**：提纲里没有引用键、没有页码、没有数值——"
          "它是写作组织，不是资格判定，也不可能被当成第二份材料清单")
    check(all("columns" not in st for st in full["steps"]),
          "⇒ **没给清单就不给 `columns` 键**（不是给一个空数组）：「这一跳需要清单才能算」"
          "与「本步零候选」在产物上不能长得一样")

    # ---------------------------------------- §15b 每一步的可引键（`cp-21` 立 `co-2`；`cp-22` 升 `co-3`）
    details.append("### §15b 每一步带上**该步自己**的可引键——`co-1` 那一跳在这里被摊平")
    #: 动因（`cp-20` 真实公司节 `m930_3_cited_real_company_cp20_r1`）：四条硬错
    #: （`s0008`/`s0009`/`s0012`/`s0016`）全是 `sentence_aspect_not_registered`，而正文里的字
    #: **逐字都在清单里**——只是落在**另一栏**登记的那一行上。`co-1` 的提纲只给 `aspect_ids`，
    #: 「步 → aspect_id → 栏 → 键表」这一跳得模型自己在 10 万字符的请求面上做，它没做出来。
    #: 这里钉的是：给了清单，每一步就把该步各栏的键**摆出来**，且**逐字投影**自
    #: `citable_columns`——同一函数、同一份登记轴，**不新造候选、不新造判定**。
    wide = CW.build_content_outline(_outline_spec(_business_aspects), manifest=manifest)
    wide_cols = CW.citable_columns(manifest, _outline_spec(_business_aspects))
    order = [a for st in wide["steps"] for a in st["aspect_ids"]]
    projected = [col for st in wide["steps"] for col in st["columns"]]
    check([col["aspect_id"] for col in projected] == order,
          "⇒ 各步的 `columns` 逐栏覆盖该步的 `aspect_ids`，次序也一致")
    #: `columns` 里**没有** `requirement_text`（那一行在步级的 `requirement_texts` 里，不重复发）——
    #: 所以逐字一致要比的是 `columns` 自己声明的那几把键，不是整项相等。
    _COL_KEYS = ("content_objective", "citation_opportunity",
                 "citable_material_keys", "citable_fact_keys", "writable_fact_keys")
    check(projected == [{"aspect_id": a, **{k: c[k] for k in _COL_KEYS}}
                        for a in order
                        for c in wide_cols if c["aspect_id"] == a],
          "**正例**：每一步的键与 `citable_columns` 对同一栏**逐字一致**——投影不发明候选；"
          "两份读数若不同，就等于把「本步能引什么」变成了第二套登记轴")
    check(all(set(col) == {"aspect_id", "content_objective", "citation_opportunity",
                           "citable_material_keys", "citable_fact_keys",
                           "writable_fact_keys"}
              for col in projected),
          "⇒ `co-6` 起每栏**恰是这六个键**（`columns` 的键集是等号，不是包含）："
          "登记（`citable_fact_keys`）与**本版可写**（`writable_fact_keys`）是两根轴，"
          "只给一根，模型只能把登记读成可写（`ndc-4` 必修的那处自相矛盾）")

    # ---------------------------------------- §15c 逐栏的内容目标与引用机会（`cp-22`，`co-3`）
    details.append("### §15c 每一栏要答什么——`co-2` 只摆候选，`co-3` 才说目标")
    #: 动因（`cp-21` 真实公司节 `m930_3_cited_real_company_cp22_r1`）：`sales_mode` 有正文
    #: （`s0015`）、有出处（`m13`）、有登记核对，`draft.gaps` 里却**连一条它的缺口都没有**——
    #: 渠道类型／直销还是经销／结算方式一项没写，账上也看不出来。根因是 `co-2` 的
    #: `operating_model` 步 `guidance` 全是**禁则**，从没说过这一栏要答什么。
    #: 这里钉的是：目标文本**只**由冻结 Contract 的**栏位身份后缀**派生，且**只**组织写作。
    sales = [c for c in wide_cols if c["aspect_id"].endswith(".sales_mode")]
    check(len(sales) == 1 and sales[0]["content_objective"] and sales[0]["citation_opportunity"],
          "**正例**：`sales_mode` 栏带非空的 `content_objective` / `citation_opportunity`")
    check(all(tok in sales[0]["content_objective"] for tok in ("①", "②", "③", "第 7 条")),
          "⇒ 内容目标**逐点**列出该栏要答的几点，并写明写不出的点按第 7 条记缺口")
    #: **`co-4` 纠偏**（两处过度推断）：请求面上的目标文本必须把「拥有**独立的**销售体系」
    #: 与「自建／直销」**显式切开**，并把「客户需求牵引排产」放进「不写」（属**生产模式**）。
    #: 这两条钉的是**栏位本身**的读法，不是某一家公司。
    _sales_obj = sales[0]["content_objective"]
    check("独立的" in _sales_obj and "**不**等于自建" in _sales_obj,
          "⇒ `co-4`：目标把「独立体系」与「自建／直销」切开（本栏材料不支持渠道推定）")
    check("排产" in _sales_obj.split("**不写**", 1)[-1]
          and "排产" not in _sales_obj.split("**不写**", 1)[0],
          "⇒ `co-4`：`客户需求牵引排产` 只在「不写」一侧（属**生产模式**，不进销售栏）")
    #: **反例**：未登记的后缀**键在、值为空串**——「本栏没有额外目标」与「读不到」在产物上
    #: 不能长得一样（与 `columns` 缺席/为 `[]` 同一条纪律）。`co-5`（`cp-24`）起登记的是
    #: 三栏（`sales_mode`/`production_mode`/`revenue_breakdown`），其余仍是空串。
    _enrolled = ("sales_mode", "production_mode", "revenue_breakdown")
    other = [c for c in wide_cols
             if not c["aspect_id"].endswith(tuple(f".{s}" for s in _enrolled))]
    check(bool(other) and all(c["content_objective"] == "" and c["citation_opportunity"] == ""
                              for c in other),
          f"**反例**：其余 {len(other)} 栏的两个键**在**、值为空串（空串是结论，不是键缺席）")
    #: **正例（`co-5`）**：新登记的两栏在**同一条派生轴**上给出非空文本——逐字投影进 `columns`。
    for _s in ("production_mode", "revenue_breakdown"):
        _c = [c for c in wide_cols if c["aspect_id"].endswith(f".{_s}")]
        check(bool(_c) and all(c["content_objective"] and c["citation_opportunity"] for c in _c),
              f"⇒ `co-5`：`{_s}` 栏的 `content_objective` / `citation_opportunity` 非空")
    #: **同一条派生轴**：只看 Contract 的栏位身份后缀，换个主题前缀得同一条目标——
    #: 这条目标讲的是**栏位本身**该回答什么，不是哪一家公司。
    check(CW.column_content_objective("company_business_model.sales_mode")
          == CW.column_content_objective("any_other_topic.sales_mode"),
          "⇒ 目标只认**栏位身份后缀**（换主题前缀不换目标）；公司名／材料 ID／页码"
          "／答案关键词一个都不出现在这张表里")
    check(CW.column_content_objective("no_such_column") == ("", ""),
          "**反例**：未登记的后缀返回 `(\"\", \"\")`——不报错、不猜、不留缺口")
    #: **仍不加门、不掺来源**：目标表是模块常量——不读材料、不读清单、不读页号，也不点名
    #: 任何引用键。文本里出现「第 7 条」这类**规则编号**是允许的（它指的是本提示词自己的条款）。
    check(not re.search(r"m\d+", sales[0]["content_objective"]
                        + sales[0]["citation_opportunity"])
          and "页" not in sales[0]["content_objective"],
          "⇒ 目标／引用机会文本里**没有引用键（`m01` 形状）、没有页码**——"
          "它是写作组织，不是第二份材料清单")


    #: **反例**：登记到甲栏的键，**不得**出现在乙栏的清单里。夹具里材料只登记到
    #: `company_business_main.main_business` 一栏 ⇒ 其余各步各栏的清单必须为空。把「本栏有行」
    #: 读成「随便哪一行都行」，正是那四条硬错的形状。
    elsewhere = CW.registered_material_keys(manifest, _ASPECT)
    stray = [(st["step_id"], col["aspect_id"], k)
             for st in wide["steps"] for col in st["columns"]
             if col["aspect_id"] != _ASPECT
             for k in col["citable_material_keys"]]
    check(bool(elsewhere) and not stray,
          f"**反例**：`{_ASPECT}` 登记着 {len(elsewhere)} 行，而其余各栏的清单**都为空**"
          "（离散落到别栏的键：" + f"{stray[:4]}" + "）——"
          "登记只认本栏，不会因为「这份材料讲的就是这件事」而漏进别栏")
    #: 事实轴同理（`registered_fact_keys` 另有 `presentation_routing` 一路，这里只钉形状）：
    #: 夹具里唯一那条权威事实登记在 `_ASPECT`，因此只有那一栏的事实键非空。
    check([col["aspect_id"] for col in projected if col["citable_fact_keys"]] == [_ASPECT],
          "⇒ 事实轴同样只落在登记到该事实的那一栏上（另一条 `presentation_routing` 路在 §11）")

    #: **确定性**：同一份清单任何时候投影出同一份；且**仍不加门**——投影只重排既有清单。
    check(CW.build_content_outline(_outline_spec(_business_aspects), manifest=manifest) == wide,
          "**确定性**：同一份清单任何时候排出同一份带键提纲")
    face_wide = [sub.get("content_outline") for sub in
                 CW.build_cited_prose_request(manifest=manifest)["subsections"]]
    check(all(o is not None and all("columns" in st for st in o["steps"]) for o in face_wide),
          "⇒ **请求面**上的提纲每一行都带 `columns`（清单在请求面是在场的）")

    details.append("NOTE §15：提纲**不加任何新的 fail-closed 门**；登记（第 3d 条）、"
                   "数字授权（第 4 条）、逐句硬核对器、缺口词表、预算、冻结 Contract 一律未动。"
                   "`co-2` 只把**既有**的 `citable_columns` 按步切一刀——键的**来源**没变、"
                   "**取值**没变、**判据**没变，「这一步能引什么」在请求面上从一次集合运算"
                   "变成一份看得见的清单。`co-3` 再加一层同级的东西：每栏的**内容目标**与"
                   "**引用机会**——它说的是「这一栏要答哪几点」，**不**说「这几点必须答到」；"
                   "答不到的仍按第 7 条由模型产生缺口，本模块不代它判。")

    # ============================================ §16 空壳段的**接线**（`crn-4`，`cp-26`）
    details.append("## §16 请求面读数 → 空壳段判定：四项读数都来自清单侧，不采信模型自述"
                   "（`crn-4` 起第四项是「本小节内可唯一对应的缺口理由」）")
    #: 动因（M930-5 全过程真实 run `m930_3_cited_upload_20261005T091926Z` 的公司节）：模型把
    #: 「供应商集中度这两栏没有来源」写成了一个 `"sentences": []` 的**空壳段**，而
    #: `CitedParagraph` 对空段落 fail-closed ⇒ **整节**作废（同一次返回里其余 30 句一个字都
    #: 留不下来）。§6 已经在纯结构层钉住了删的边界；这里钉的是**接线**：判定要的三个读数
    #: （这一段声明的栏属不属于本小节、这一栏登记到几条来源、顶层有没有那条缺口）确实来自
    #: 清单侧，且方向是单向的。

    #: 一条**本 Pack 里没有来源**的 Contract 栏目：夹具的 Pack 只登记了 `_ASPECT`，
    #: 因此这一栏的 `registered_source_keys` 必然是空 tuple——「零登记」是**结论**。
    _NO_SRC_ASPECT = "aspect-no-source-in-this-pack"
    _NO_SRC_REQ = "列示报告期内向前五名供应商的采购集中度"
    _NO_SRC_SUB = "sub-no-source"
    _SRC_SUB = "sub-with-source"

    def _two_subsection_manifest(shell_aspect=_NO_SRC_ASPECT):
        """本节的两个写作小节：一个**有来源**、一个的同名小节声明 `shell_aspect`。"""
        specs = (
            CW.CitedSubsectionSpec(subsection_id=_SRC_SUB, title="产品或服务",
                                   requirement_text=_REQ_TEXT,
                                   declared_aspect_ids=(_ASPECT,)),
            CW.CitedSubsectionSpec(subsection_id=_NO_SRC_SUB, title="供应商集中度",
                                   requirement_text=_NO_SRC_REQ,
                                   declared_aspect_ids=(shell_aspect,)),
        )
        return CW.build_cited_writer_input(
            authority=authority, material_context=context, subsections=specs,
            facts=facts, section_title="主营业务")

    shell_manifest = _two_subsection_manifest()
    _cite = CW.registered_material_keys(shell_manifest, _ASPECT)
    #: 「登记来源」= 材料行 ∪ 事实行（`registered_source_keys`），不是只有材料行。
    _sources = CW.registered_source_keys(shell_manifest, _ASPECT)
    check(bool(_cite) and len(_sources) > len(_cite)
          and not CW.registered_source_keys(shell_manifest, (_NO_SRC_ASPECT,)),
          f"夹具前提：`{_ASPECT}` 有 {len(_sources)} 条登记来源（材料 {len(_cite)} + 事实 "
          f"{len(_sources) - len(_cite)}），`{_NO_SRC_ASPECT}` 一条都没有"
          "（零登记是**结论**，不是「读不到」）")

    def _shell_body(*, shell_paragraphs, gaps) -> str:
        return json.dumps({
            "subsections": [
                {"subsection_id": _SRC_SUB, "title": "产品或服务",
                 "paragraphs": [{"paragraph_id": "p1", "aspect_ids": [_ASPECT],
                                 "sentences": [{"sentence_id": "s1", "text": _BODY_A,
                                                "citations": [_cite[0]]}]}]},
                {"subsection_id": _NO_SRC_SUB, "title": "供应商集中度",
                 "paragraphs": list(shell_paragraphs)}],
            "gaps": list(gaps), "follow_up_needs": []}, ensure_ascii=False)

    def _gap_row(*, requirement_text=_NO_SRC_REQ, reason="no_source_in_manifest"):
        return {"subsection_id": _NO_SRC_SUB, "requirement_text": requirement_text,
                "reason": reason, "detail": "本次输入里没有该来源。"}

    def _parse_shell(text, *, manifest_of=None):
        return CW.parse_cited_prose_with_normalization(
            text, manifest=manifest_of or shell_manifest, task_id=str(task.task_id),
            section_id="company", writer_identity=CW.CITED_WRITER_PROMPT_VERSION)

    #: --- 读数一：每栏登记数 = `registered_source_keys` 的同一次调用（不是第二份口径）
    _ctx = CW.empty_shell_context(
        payload={"subsections": [{"subsection_id": _SRC_SUB},
                                 {"subsection_id": _NO_SRC_SUB}]},
        manifest=shell_manifest)
    check(_ctx.registered_source_counts.get(_ASPECT) == len(_sources)
          and _ctx.registered_source_counts.get(_NO_SRC_ASPECT) == 0,
          "**读数一**：每一栏的登记数**逐字**等于 `registered_source_keys` 的同一次调用"
          "（材料行 ∪ 事实行；请求面、缺口改判、空壳判定读的是同一根轴，不是三份口径）")
    check(_ctx.aspect_is_source_free(_NO_SRC_ASPECT)
          and _ctx.aspect_is_source_free("aspect-never-mentioned"),
          "⇒ 「缺项即零」：没被问到的栏按 0 处理——那是「这一栏本次没有来源」的结论形状")

    #: --- 读数三（`crn-3` 新增）：归属表按 `subsection_id` 建，取自**这一份清单**的声明
    check(_ctx.aspect_belongs_to(_NO_SRC_SUB, _NO_SRC_ASPECT)
          and _ctx.aspect_belongs_to(_SRC_SUB, _ASPECT)
          and not _ctx.aspect_belongs_to(_SRC_SUB, _NO_SRC_ASPECT)
          and not _ctx.aspect_belongs_to(_NO_SRC_SUB, _ASPECT),
          "**读数三**：每一小节声明的栏目各自归位（`_NO_SRC_SUB` 声明 `_NO_SRC_ASPECT`、"
          "`_SRC_SUB` 声明 `_ASPECT`），交叉着问一律 `False`——"
          "归属表与请求面 `citable_columns`、下游 `_check_paragraph_aspects` 读的是同一份声明")
    check(not _ctx.aspect_belongs_to("a-subsection-not-written-this-time", _NO_SRC_ASPECT)
          and set(_ctx.subsection_aspect_ids) == {_SRC_SUB, _NO_SRC_SUB},
          "⇒ **只登记这一次返回真的写了的小节**：没写到的小节与「查不到」同形，"
          "一律按「没证明就不许删」处理——方向与上面两项**相反**")

    #: --- 读数二：`no_source_in_manifest` 出自**系统判定**，不是模型自述的那个字
    _ctx_gap = CW.empty_shell_context(
        payload={"subsections": [{"subsection_id": _SRC_SUB},
                                 {"subsection_id": _NO_SRC_SUB}],
                 "gaps": [_gap_row(),
                          _gap_row(requirement_text=_REQ_TEXT,
                                   reason="manifest_partial_for_requirement")]},
        manifest=shell_manifest)
    check(_ctx_gap.no_source_gap_aspects == frozenset({_NO_SRC_ASPECT}),
          f"**读数二**：只有 `{_NO_SRC_ASPECT}` 被算作「已声明无来源」")
    check(_ASPECT not in _ctx_gap.no_source_gap_aspects,
          "⇒ 同一份返回里模型自述 `manifest_partial_for_requirement`、而该栏**登记着来源**时，"
          "`assign_gap_reason` 按系统口径把它改判，**不**落进「已声明无来源」——"
          "零登记才是那句话的前提，模型怎么写不作数")
    check(_ctx_gap.subsection_gap_reason
          == {_NO_SRC_SUB: {_NO_SRC_ASPECT: "no_source_in_manifest"}},
          "**读数四（`crn-4`）**：`empty_shell_context` 用生产侧同一份口径交出"
          "「本小节内**可唯一对应**的顶层缺口理由」——这里 `_NO_SRC_SUB` 下只有 "
          f"`{_NO_SRC_ASPECT}` 一条、系统判定为 `no_source_in_manifest`；"
          "落不到栏上的那条要求（另一小节的 `requirement_text`）不入表")
    _unmapped = CW.empty_shell_context(
        payload={"subsections": [{"subsection_id": _SRC_SUB},
                                 {"subsection_id": _NO_SRC_SUB}],
                 "gaps": [_gap_row(requirement_text="一句落不到任何栏上的要求")]},
        manifest=shell_manifest)
    check(not _unmapped.no_source_gap_aspects,
          "⇒ 缺口文本**落不到栏上**时不猜：那一栏**不**被算作已声明无来源"
          "（少认一项 ⇒ 只会让段留下来，方向是保守的）")

    #: --- 正例：六项全满足 ⇒ 整节**解析成功**，空壳段被删且**不计入栏目覆盖**
    draft, ledger = _parse_shell(_shell_body(
        shell_paragraphs=[{"paragraph_id": "p-shell",
                           "aspect_ids": [_NO_SRC_ASPECT], "sentences": []}],
        gaps=[_gap_row()]))
    check([s.subsection_id for s in draft.subsections] == [_SRC_SUB, _NO_SRC_SUB]
          and [len(s.paragraphs) for s in draft.subsections] == [1, 0],
          "**正例（端到端）**：空壳段所在的小节**零段落**、整节解析成功——"
          "同一次返回里另一小节的正文一句不少地留了下来")
    check([s.sentence_ids() for s in draft.subsections] == [("s0001",), ()],
          "⇒ 归一改的是记账：存活句子拿到全节唯一编号，空壳段不占号也不留空句")
    check(ledger.empty_shell_rule == "applied"
          and [d.reason for d in ledger.dropped_empty_shell_paragraphs]
          == ["empty_shell_no_source"]
          and ledger.dropped_empty_shell_paragraphs[0].aspect_ids == (_NO_SRC_ASPECT,),
          "⇒ 台账落了 `applied` + 唯一的 `empty_shell_no_source` 一行，含它声明的栏目")
    _row = ledger.to_dict()["dropped_empty_shell_paragraphs"][0]
    check(_row["counts_toward_coverage"] is False
          and set(_row) == {"reason", "subsection_id", "subsection_index",
                            "paragraph_index", "paragraph_id", "aspect_ids",
                            "counts_toward_coverage",
                            "registered_source_counts", "system_gap_reasons"},
          "⇒ 那一行**自带** `counts_toward_coverage: false`（键集是等号，`crn-4` 起为 9 键："
          "另两键是删除**依据**的逐栏证据行）——「删掉」不等于「写好」，它一个字都不进覆盖账")
    check(all(_NO_SRC_ASPECT not in p.aspect_ids
              for s in draft.subsections for p in s.paragraphs),
          "⇒ 草稿里**没有任何存活段落**声称服务那一栏"
          "（删除发生在段级声明进入草稿之前，不是删完之后再补一句）")
    check([g.reason for g in draft.gaps] == ["no_source_in_manifest"]
          and draft.gaps[0].subsection_id == _NO_SRC_SUB,
          "**缺口仍在**：那一栏「本次写不出来」由**顶层缺口**如实记着——"
          "删掉空壳段是去掉同一件事的第二份记账，不是把那件事抹掉")
    check(draft.gaps[0].claimed_reason == "no_source_in_manifest"
          and draft.gaps[0].reason_assignment == "writer_declared",
          "⇒ 本栏确实零登记，系统判定与自述一致（`writer_declared` 是核过之后的结论）")

    #: --- 反例一：**没有那条缺口**（同一段、同一栏，其余一字不改）⇒ 照旧整节作废
    _expect_error(
        lambda: _parse_shell(_shell_body(
            shell_paragraphs=[{"paragraph_id": "p-shell",
                               "aspect_ids": [_NO_SRC_ASPECT], "sentences": []}],
            gaps=[])),
        token="没有任何句子", reason="empty_paragraph")
    details.append("NOTE §16a：缺**任一**条件都照旧拒——这里缺的是「顶层已声明无来源」那一项，"
                   "于是「模型漏写了整段」与「模型把缺口写成空壳」仍然分得开。")

    #: --- 反例二（`crn-4` 改写）：**有来源的那一栏** + **没有可唯一对应的顶层缺口** ⇒ 仍拒。
    #: 动因（真实 run `m930_3_cited_upload_20261005T131532Z` 的公司节）：`p10` 声明三栏客户
    #: 集中度，三栏在请求面**各登记着材料**（`m47`／`m51`／`m52`），`crn-3` 下这必然撞
    #: `aspect_has_registered_source`。`crn-4` **没有**把这句改宽：放行的前提是「这一栏在
    #: **同一小节**有一条**可唯一对应**、且系统判定理由落在闭集里的顶层缺口」；缺了那条缺口，
    #: 同一段照旧整节 fail-closed。这里钉的正是「**无合格对应缺口** ⇒ 仍拒」。
    _sourced_manifest = _two_subsection_manifest(shell_aspect=_ASPECT)
    _sourced_reply = json.dumps({"subsections": [
        {"subsection_id": _SRC_SUB, "title": "产品或服务", "paragraphs": []},
        {"subsection_id": _NO_SRC_SUB, "title": "供应商集中度",
         "paragraphs": [{"paragraph_id": "p-shell", "aspect_ids": [_ASPECT],
                         "sentences": []}]}],
        "gaps": [], "follow_up_needs": []}, ensure_ascii=False)
    _sourced_body = json.loads(_sourced_reply)
    _sourced_ctx = CW.empty_shell_context(payload=_sourced_body, manifest=_sourced_manifest)
    check(not _sourced_ctx.aspect_is_source_free(_ASPECT)
          and _sourced_ctx.aspect_unique_gap_reason(_NO_SRC_SUB, _ASPECT) == "",
          "**反例二（`crn-4`）**：`_ASPECT` 登记着来源、`_NO_SRC_SUB` 内又**没有**可唯一对应的"
          "顶层缺口 ⇒ 两条依据都不成立")
    check(CRN._empty_shell_blocked_by(
              _sourced_body["subsections"][1]["paragraphs"][0],
              subsection_id=_NO_SRC_SUB, context=_sourced_ctx)
          == "aspect_has_registered_source",
          "⇒ 首个阻挡码是 `aspect_has_registered_source`——与本次真实 run 里 `p10` 在 `crn-3` "
          "下的读数**同码**，证明 `crn-4` 只是**多**了一条更窄的放行依据，"
          "不是把这条码放宽")
    _expect_error(lambda: _parse_shell(_sourced_reply, manifest_of=_sourced_manifest),
                  token="没有任何句子", reason="empty_paragraph")
    details.append("NOTE §16b：[反例] 同一形状的段落写在**登记着来源**却**没有合格对应缺口**的"
                   "那一栏上 ⇒ 仍整节 fail-closed（首码 `aspect_has_registered_source`）。"
                   "规则要求每一栏在**同一小节**内都有一条**可唯一对应**、理由已由系统改判得出的"
                   "顶层缺口——「这一栏有 3 份上年来源」与「这一栏一份都没有」仍然不得同记一条；"
                   "「有来源」本身**不是**放行条件。")

    #: --- 正例三（`crn-4` 新增的接线，正是本次真实 `p10` 的形状）：**同一栏、有来源，
    #: 但有一条落在同一小节、可唯一对应的顶层缺口**——模型的 `no_source_in_manifest` 被
    #: `assign_gap_reason` 系统改判为 `source_present_but_not_admissible` ⇒ 空壳段被删、
    #: 原因码是 `empty_shell_gap_supported`（**不是** `empty_shell_no_source`：这一栏确实
    #: 有登记来源），台账逐栏记下登记数与系统理由；同一次返回里另一小节的正文一句不少地留下。
    _supported_reply = json.dumps({"subsections": [
        {"subsection_id": _SRC_SUB, "title": "产品或服务",
         "paragraphs": [{"paragraph_id": "p1", "aspect_ids": [_ASPECT],
                         "sentences": [{"sentence_id": "s1", "text": _BODY_A,
                                        "citations": [_cite[0]]}]}]},
        {"subsection_id": _NO_SRC_SUB, "title": "供应商集中度",
         "paragraphs": [{"paragraph_id": "p-shell", "aspect_ids": [_ASPECT],
                         "sentences": []}]}],
        "gaps": [_gap_row()], "follow_up_needs": []}, ensure_ascii=False)
    _sup_draft, _sup_ledger = _parse_shell(_supported_reply, manifest_of=_sourced_manifest)
    check([len(s.paragraphs) for s in _sup_draft.subsections] == [1, 0]
          and [s.sentence_ids() for s in _sup_draft.subsections] == [("s0001",), ()],
          "**正例三（`crn-4`）**：空壳段被删、同一次返回里另一小节的正文一句不少地留下——"
          "删只发生在段级，正文侧的句子编号不受影响")
    _sup_row = _sup_ledger.to_dict()["dropped_empty_shell_paragraphs"][0]
    check(_sup_row["reason"] == "empty_shell_gap_supported"
          and _sup_row["registered_source_counts"] == {_ASPECT: len(_sources)}
          and _sup_row["system_gap_reasons"]
          == {_ASPECT: "source_present_but_not_admissible"}
          and _sup_row["counts_toward_coverage"] is False,
          "⇒ 台账如实记下：这一栏**确实有**登记来源（数不为 0）、系统判定理由是 "
          "`source_present_but_not_admissible`；被删的段 `counts_toward_coverage` 仍是 `False`"
          "（「删掉」不等于「写好」，它一个字都不进覆盖账）")
    check([g.reason for g in _sup_draft.gaps] == ["source_present_but_not_admissible"]
          and _sup_draft.gaps[0].reason_assignment == "system_reassigned_from_manifest"
          and _sup_draft.gaps[0].claimed_reason == "no_source_in_manifest",
          "**缺口仍在**：顶层缺口原样保留（模型自述留在 `claimed_reason`，系统判定记在 "
          "`reason`）——删掉空壳段是去掉同一件事的第二份记账，不是把那件事抹掉")

    #: --- 反例五（`crn-3` 归属判据）：**跨小节借用**的栏目 ⇒ 归属先于来源被问，段不得被删
    _BORROW = "aspect-declared-only-by-the-other-subsection"
    _BORROW_REQ = "列示报告期内向前五名客户的销售集中度"

    def _borrow_manifest():
        """`_BORROW` 只由 `_SRC_SUB` 声明（Pack 里同样**一条来源都没有**）；
        `_NO_SRC_SUB` 只声明 `_NO_SRC_ASPECT`。于是同一段空壳写进 `_NO_SRC_SUB` 时，
        「零登记来源」与「顶层已有那条缺口」**都成立**，唯独归属不成立。"""
        specs = (
            CW.CitedSubsectionSpec(subsection_id=_SRC_SUB, title="产品或服务",
                                   requirement_text=f"{_REQ_TEXT}\n{_BORROW_REQ}",
                                   declared_aspect_ids=(_ASPECT, _BORROW)),
            CW.CitedSubsectionSpec(subsection_id=_NO_SRC_SUB, title="供应商集中度",
                                   requirement_text=_NO_SRC_REQ,
                                   declared_aspect_ids=(_NO_SRC_ASPECT,)),
        )
        return CW.build_cited_writer_input(
            authority=authority, material_context=context, subsections=specs,
            facts=facts, section_title="主营业务")

    _borrow_manifest_v = _borrow_manifest()
    check(bool(_cite) and not CW.registered_source_keys(_borrow_manifest_v, (_BORROW,))
          and not CW.registered_source_keys(_borrow_manifest_v, (_NO_SRC_ASPECT,)),
          f"夹具前提：`{_BORROW}` 与 `{_NO_SRC_ASPECT}` 在本 Pack 里都**一条来源都没有**"
          f"——所以拦住那段空壳的只可能是归属，不是来源")

    #: 缺口挂在**声明它的那一小节**（`_SRC_SUB`）上——这正是漏洞的形状：
    #: 缺口「说对了栏」，空壳段却写到了别的小节里。删掉它，那一栏的覆盖就从产物里消失了。
    _borrow_gap = {"subsection_id": _SRC_SUB, "requirement_text": _BORROW_REQ,
                   "reason": "no_source_in_manifest", "detail": "本次输入里没有该来源。"}

    def _borrow_body(*, shell_in):
        _shell_para = {"paragraph_id": "p-shell", "aspect_ids": [_BORROW], "sentences": []}
        _live_para = {"paragraph_id": "p1", "aspect_ids": [_ASPECT],
                      "sentences": [{"sentence_id": "s1", "text": _BODY_A,
                                     "citations": [_cite[0]]}]}
        src_paras = [_live_para]
        no_src_paras = []
        (src_paras if shell_in == _SRC_SUB else no_src_paras).append(dict(_shell_para))
        return json.dumps({
            "subsections": [
                {"subsection_id": _SRC_SUB, "title": "产品或服务",
                 "paragraphs": src_paras},
                {"subsection_id": _NO_SRC_SUB, "title": "供应商集中度",
                 "paragraphs": no_src_paras}],
            "gaps": [dict(_borrow_gap)], "follow_up_needs": []}, ensure_ascii=False)

    _borrow_ctx = CW.empty_shell_context(
        payload=json.loads(_borrow_body(shell_in=_NO_SRC_SUB)),
        manifest=_borrow_manifest_v)
    check(_borrow_ctx.no_source_gap_aspects == frozenset({_BORROW})
          and _borrow_ctx.aspect_is_source_free(_BORROW),
          "⇒ 另外两项读数**都成立**：这一栏零登记来源，顶层确实有一条系统判定的 "
          "`no_source_in_manifest` 缺口（挂在声明它的 `_SRC_SUB` 上）")
    _borrow_norm = CRN.normalize_cited_reply(
        json.loads(_borrow_body(shell_in=_NO_SRC_SUB)), empty_shell=_borrow_ctx)
    check(not _borrow_norm.dropped_empty_shell_paragraphs
          and [k.blocked_by for k in _borrow_norm.kept_empty_paragraphs]
          == ["aspect_not_in_subsection"],
          "**反例五（跨小节借用）**：空壳段写进了**不是声明该栏**的那一小节 ⇒ "
          "段**原样留下**，原因码 `aspect_not_in_subsection`——"
          "删掉它等于把「模型声明了这一小节不负责的栏目」整段抹掉")
    _expect_error(lambda: _parse_shell(_borrow_body(shell_in=_NO_SRC_SUB),
                                       manifest_of=_borrow_manifest_v),
                  token="没有任何句子", reason="empty_paragraph")
    details.append("NOTE §16d：归属**先于**来源被问。同一个跨小节栏目，读成「有来源」或"
                   "「零来源」都不改变它被留下的理由——拦它的是归属。留下的段随即由构造器抛 "
                   "`empty_paragraph`；更下游 `_check_paragraph_aspects` 才是"
                   "「声明了本小节不负责的栏目」的正主，归一**不得**抢在它前面把整段删掉。")

    #: 对照：同一段、同一条缺口，写进**自己声明该栏**的那一小节 ⇒ 归属成立 ⇒ 照旧被删
    _own_norm = CRN.normalize_cited_reply(
        json.loads(_borrow_body(shell_in=_SRC_SUB)),
        empty_shell=CW.empty_shell_context(
            payload=json.loads(_borrow_body(shell_in=_SRC_SUB)),
            manifest=_borrow_manifest_v))
    check([d.reason for d in _own_norm.dropped_empty_shell_paragraphs]
          == ["empty_shell_no_source"]
          and [d.aspect_ids for d in _own_norm.dropped_empty_shell_paragraphs]
          == [(_BORROW,)],
          "**正例（对照）**：同一个栏目的空壳段写进**声明它的那一小节** ⇒ 各项全满足 ⇒ "
          "照旧被删、走唯一的 `empty_shell_no_source`——两边只差**归属**一项，"
          "证明拦住上面那一段的确实是归属判据，不是别的")

    #: --- 反例三：**非空句**的空壳段（其余全对）⇒ 连候选都不是
    _live_draft, _live_ledger = _parse_shell(_shell_body(
        shell_paragraphs=[{"paragraph_id": "p-shell", "aspect_ids": [_NO_SRC_ASPECT],
                           "sentences": [{"sentence_id": "s1", "text": _BODY_B,
                                          "citations": [_cite[0]]}]}],
        gaps=[_gap_row()]))
    check(not _live_ledger.dropped_empty_shell_paragraphs
          and _live_draft.subsections[1].paragraphs[0].aspect_ids == (_NO_SRC_ASPECT,),
          "**反例三（非空句）**：同一个小节、同一条缺口，只要写了一句就**不**是空壳段——"
          "这条规则不因为「这一栏确实零来源」而顺手把有正文的段也删掉")

    #: --- 反例四：**多一个字段**的段 ⇒ 不删，且归一自己 typed 拒
    _expect_error(
        lambda: _parse_shell(_shell_body(
            shell_paragraphs=[{"paragraph_id": "p-shell",
                               "aspect_ids": [_NO_SRC_ASPECT], "sentences": [],
                               "note": "模型自己加的字段"}],
            gaps=[_gap_row()])),
        token="未登记字段", reason="paragraph_field_malformed")
    details.append("NOTE §16c：形态不合格的段**不**走空壳这条路——"
                   "它是坏形，由归一用 `paragraph_field_malformed` 拒，"
                   "读回侧按码分流时不会把两者当成一件事。")

    #: --- 方向单向（端到端）：读不到请求面读数的那条路仍是 `crn-1` 的行为
    _bare = CRN.normalize_cited_reply(json.loads(_shell_body(
        shell_paragraphs=[{"paragraph_id": "p-shell",
                           "aspect_ids": [_NO_SRC_ASPECT], "sentences": []}],
        gaps=[_gap_row()])))
    check(_bare.empty_shell_rule == "not_evaluated_no_request_face"
          and not _bare.dropped_empty_shell_paragraphs,
          "⇒ 缺省（不带读数）时第三条规则**不评估**：空壳段原样留在 payload 里，"
          "行为与 `crn-1` 逐字相同")

    details.append(
        "NOTE §16：这条规则**不改任何判据**——登记（第 3d 条）、缺口改判"
        "（`assign_gap_reason`）、逐句硬核对器、数字授权、预算、冻结 Contract 一律未动。"
        "它只是把「模型已经用顶层缺口说过的那件事」从正文侧再删一遍；"
        "被删的段整段消失、**不计入栏目覆盖**，缺口照旧由顶层那条记着。"
        "`crn-3` 补的归属判据（本小节归属）**只减不加**：它让规则少删一类段"
        "（借别的小节的「零来源 + 缺口」造空壳的那一类），交给下游"
        "`_check_paragraph_aspects` 去判——删得比 `crn-2` 少，方向仍是保守的。"
        "`crn-4` 补的**缺口支持**是唯一**加**删的一条：它有登记来源也照删，"
        "因此边界被逐条钉死——**无合格对应缺口**（§16b）、跨小节（§16d）、非唯一对应、"
        "理由不在闭集、只覆盖部分栏，逐条仍拒；它**不**放宽 `CitedParagraph` 的非空约束，"
        "**不**改数字授权与逐句核对，被删的段也**不**进覆盖、事实资格、审阅与系统放行。")

    details.append(
        "NOTE 本模块只证明**接口结构**；「正文写得对不对」由第二步逐句核对与独立审阅判定，"
        "本模块不产生任何正式产物。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""M930-3 反例集：`sections.narrative_schema` 的 narrative wire 必须能自己拒绝伪造。

本模块是**反例优先**的（里程碑 §11「每个缺陷只增加能唯一对应该缺陷的反例」）。它不读库、
不调 LLM、不跑真实文档：全部断言都建立在「类型化身份必须自洽」这一点上，因此可以在任何
环境下稳定复现。

覆盖该缺陷族（narr-4：门前**候选束** + 门 + 门后组装身份）：
1. 候选/单元/proposal/束的类型层与 wire 层都没有门后身份（决定 / accepted binding /
   final Claim / 最终 Narrative / Result），经写侧工厂夹带的反向边进不了身份与载荷；
2. 候选身份不被支撑/材料/定位输入污染（候选与支撑是两条正交轴）；
3. `authority_kind` 决定哪套 id 是承重身份（§16.10 #8）：`payload_ref` 不得脱离 material
   载体，external 边必须同时在场 external_fact_id + snapshot 载体五字段；
4. 门前门（`gate_draft`，`ng-4`）逐条回**权威输入**核验：容器 / fact / material / payload /
   snapshot 载体的伪边必须被拒，且规则词表封闭（事实表面守恒、表格展示元数据、final Claim
   归属、缺失去向都属门后 3C/3D，不得提前出现在门前门里）；
5. 数字权威（§6.2.1 通道 A / §十一）：数字/日期/币种只能走路径 A 预验证；把权威期间改写成
   等价措辞、自造数值、纯路径 B 候选携带数字一律拒；授权池只取命题与规范值，页码 / URL /
   body hash 等定位与载体字段不得被借来凑数字；
6. 含糊期间（§十二 4）在候选与门前草稿单元两处都拒；
7. exact manifest / WMPD 的三层集合等式（§6.4.1）：available = processed、used ∩ not_used = ∅、
   used ∪ not_used = available，`not_used` 必须有封闭理由与确定性证明，且**不是**缺口；
8. required fact 在门前只有 rework 级提示（闭环在门后 FND）；
9. 门后对象自身仍 fail-closed：`FactNarrativeDisposition`（claimed 无 claim / 未呈现缺 reason /
   required 未呈现无 unresolved）；
10. id 伪造与内容寻址稳定性（同内容同 id、改一字变 id、roundtrip 保持）；
11. 身份不一致的组合（Draft/Evaluation binding）与 `report_version` 失配；
12. Draft→Result 单向与身份失效（§16.10 #16/#18）：Draft 在类型层/载荷层/读回层都没有
    回指 post-gate Result 的边，而句子的文本与绑定、draft 的标题与投影一变，内容身份必须
    随之失效（改字段不重算 id ⇒ 拒），重建路径得到的身份确实不同。
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as HS
from sections import backbone_schema as BACKBONE
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS


class _NS:
    """只读命名空间替身（gate 只按属性名读取权威输入，不需要真 Pack 对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _citation(evidence_id: str = "ev1", page: int | None = None) -> HS.CitationRef:
    return HS.CitationRef(ref_type="evidence", evidence_id=evidence_id, page_number=page)


def _claim(text: str, *, topic_id: str = "company_business", evidence_id: str = "ev1",
           claim_type: str = "fact", question_ids: tuple[str, ...] = ("q1",),
           section_id: str = "company", candidate_id: str = "ccand_1",
           binding_ids: tuple[str, ...] = ("asb_1",),
           candidate_revision: str = "dr-1") -> SS.SectionClaim:
    """current `claim-2`（M930-3A/3B）：candidate revision 与 accepted binding 集无默认值。

    本模块只关心 narrative 侧的身份，因此这里给出**确定**的 candidate/binding 夹具；
    candidate 与 binding 自身的门（proposal → aggregate → entailment → accepted）在
    `test_demo_claim_proposal.py` / `test_demo_accepted_binding.py` 里各自证明。
    """
    refs = (_citation(evidence_id),)
    bindings = tuple(binding_ids)
    claim_id = SS.derive_claim_id(claim_type, topic_id, question_ids, text, refs,
                                  candidate_id, candidate_revision, bindings)
    return SS.SectionClaim(claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION,
                           section_id=section_id, topic_id=topic_id,
                           question_ids=question_ids, text=text, claim_type=claim_type,
                           citation_refs=refs, claim_candidate_id=candidate_id,
                           claim_candidate_revision=candidate_revision,
                           accepted_binding_ids=bindings)


def _cit_id(claim: SS.SectionClaim) -> str:
    return SS.derive_citation_id(claim.claim_id, claim.citation_refs[0])


def _fact(fact_id: str, text: str, *, aspect_ids: tuple[str, ...] = ("asp1",),
          evidence_id: str = "ev1", amount: str | None = None, period: str | None = None) -> _NS:
    value_identity = None
    if amount is not None:
        value_identity = _NS(amount_canonical=amount, metric="营业收入", unit="亿元",
                             period=period or "2024年", scope="合并")
    return _NS(fact_id=fact_id, text=text, period=period, scope=None,
               value_identity=value_identity, aspect_ids=aspect_ids,
               citation_refs=(_citation(evidence_id),))


class _PayloadRef:
    """material 的 payload reference 替身（`material_index` 只认带 `to_dict()` 的载荷）。"""

    def __init__(self, **kw) -> None:
        self._payload = dict(kw)

    def to_dict(self) -> dict:
        return dict(self._payload)


def _material(material_id: str, source_identity: str = "src1", *,
              payload: dict | None = None, page: int | None = None) -> _NS:
    return _NS(material_id=material_id, source_identity=source_identity,
               payload_ref=(_PayloadRef(**payload) if payload is not None else None),
               locator=(_NS(page=page) if page is not None else None))


def _external_fact(external_fact_id: str = "ef1", *, snapshot_id: str = "snap-1",
                   statement: str = "公司将受益于行业需求增长。",
                   aspect_ids: tuple[str, ...] = ("asp1",),
                   canonical_url: str = "https://example.invalid/disclosure/1",
                   body_hash: str = "d" * 64, source_policy_version: str = "spol-1",
                   as_of_date: str = "2026-06-30") -> _NS:
    """formal `ExternalFact` 替身（§6.2.3：容器身份 = snapshot，事实身份 = external_fact_id）。"""
    return _NS(external_fact_id=external_fact_id, candidate_id="fextcand1",
               candidate_revision="fr1", qualification_decision_id="fqd1",
               statement=statement, aspect_ids=aspect_ids, source_snapshot_id=snapshot_id,
               canonical_url=canonical_url, body_hash=body_hash,
               source_policy_version=source_policy_version, as_of_date=as_of_date)


def _authority(*, pack_id: str = "pack1", topic_id: str = "company_business",
               facts: tuple = (), materials: tuple = (), blocking: bool = True,
               producer_kind: str = "topic_harness", external_facts: tuple = ()) -> _NS:
    aspects = (_NS(aspect_id="asp1", topic_id=topic_id,
                   blocking_policy=("block",) if blocking else ()),)
    requirement = _NS(topic_id=topic_id, aspects=aspects)
    pack = _NS(pack_id=pack_id, topic_id=topic_id, facts=tuple(facts),
               materials=tuple(materials), external_facts=tuple(external_facts))
    return _NS(producer_kind=producer_kind,
               pack_set=_NS(packs=(pack,), requirements=(requirement,)))


def main() -> dict:
    passed = 0
    failed = 0
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
                details.append(f"FAIL {msg}：原因不符（{str(e)[:140]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:140]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    def gate_ids(bundle: NS.SectionDraft, authority) -> tuple[str, ...]:
        """门前门（narr-4）只管**候选束**：它没有 `claims` 参数，也不看 final Narrative。"""
        return tuple(i.rule_id for i in NS.gate_draft(bundle, authority).issues)

    # ------------------------------------------------------------------
    # 1. ClaimSupportRef：伪边与 authority kind 混装
    # ------------------------------------------------------------------
    claim = _claim("公司主营动力电池系统的研发、生产与销售。")
    cit = _cit_id(claim)
    good_ref = NS.ClaimSupportRef.create(
        claim_id=claim.claim_id, authority_kind="topic_pack", authority_container_id="pack1",
        citation_id=cit, support_role="primary", authority_state="authoritative",
        fact_id="f1")
    check(good_ref.support_ref_id.startswith("csr_"), "合法 support edge 可构造")
    check(NS.ClaimSupportRef.from_dict(good_ref.to_dict()).support_ref_id
          == good_ref.support_ref_id, "support edge roundtrip 保持 id")

    expect_error(
        lambda: dataclasses.replace(good_ref, fact_id="f9"),
        NS.NarrativeSchemaError, "手改 fact_id 而不重算 id 必须被拒")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="topic_pack",
            authority_container_id="pack1", citation_id=cit, support_role="primary",
            authority_state="authoritative", fact_id="f1", financial_fact_id="ff1"),
        NS.NarrativeSchemaError, "topic_pack 边携带 financial_fact_id 必须被拒",
        needle="混装")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="financial_pack",
            authority_container_id="ffpa_1", citation_id=cit, support_role="primary",
            authority_state="authoritative", financial_fact_id="ff1", material_id="m1"),
        NS.NarrativeSchemaError, "financial_pack 边携带 material_id 必须被拒", needle="混装")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="financial_pack",
            authority_container_id="ffpa_1", citation_id=cit, support_role="primary",
            authority_state="authoritative"),
        NS.NarrativeSchemaError, "financial_pack 边缺 financial_fact_id 必须被拒")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="evidence_note",
            authority_container_id="note_set:t1", citation_id=cit, support_role="primary",
            authority_state="authoritative", fact_id="nf1"),
        NS.NarrativeSchemaError, "evidence_note 边缺 locator_ref 必须被拒")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="topic_pack",
            authority_container_id="pack1", citation_id=cit, support_role="primary",
            authority_state="supplemental_only", fact_id="f1"),
        NS.NarrativeSchemaError, "非权威来源充当 primary 支撑必须被拒")

    # #8 容器/来源身份跨 kind 借用：`authority_kind` 决定哪一套 id（fact / financial_fact /
    # material / locator / snapshot）是承重身份，任何「两套 id 混装」都必须 fail-closed。
    check(NS.AUTHORITY_KINDS == ("topic_pack", "financial_pack", "evidence_note",
                                 "external_snapshot"),
          "#8：authority union 是封闭四元（kind 是承重轴，不是自由字符串）")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="external_snapshot",
            authority_container_id="snap_1", citation_id=cit, support_role="primary",
            authority_state="authoritative", payload_ref={"snapshot_id": "snap_1"}),
        NS.NarrativeSchemaError, "#8：external 事实不得走 narr-3 的 ClaimSupportRef 边",
        needle="不承载 external_snapshot 边")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="topic_pack",
            authority_container_id="pack1", citation_id=cit, support_role="primary",
            authority_state="authoritative", fact_id="f1", locator_ref=("ev1", 0, 10)),
        NS.NarrativeSchemaError, "#8：pack 边不得夹带 note 的 locator_ref（容器身份混装）",
        needle="不得携带 locator_ref")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="evidence_note",
            authority_container_id="note_set:t1", citation_id=cit, support_role="primary",
            authority_state="authoritative", fact_id="nf1", locator_ref=("ev1", 0, 10),
            material_id="m1"),
        NS.NarrativeSchemaError, "#8：note 边不得夹带 pack 的 material_id（来源身份混装）",
        needle="authority kind 混装")
    expect_error(
        lambda: NS.ClaimSupportRef.create(
            claim_id=claim.claim_id, authority_kind="topic_pack",
            authority_container_id="pack1", citation_id=cit, support_role="primary",
            authority_state="authoritative", fact_id="f1",
            payload_ref={"material_payload": "p1"}),
        NS.NarrativeSchemaError, "#8：payload_ref 脱离 material 载体必须被拒（引用无载体可解析）",
        needle="payload_ref 只在绑定 material_id 时允许")

    # ------------------------------------------------------------------
    # 2. 句子：裸数字、过渡句断言事实、隐藏数字
    # ------------------------------------------------------------------
    check(NS.scan_numeric_tokens("2024年营业收入1234.56亿元，同比-3.2%。")
          == ("2024年", "1234.56亿元", "-3.2%"), "数字扫描确定且单位最长匹配")
    expect_error(
        lambda: NS.NarrativeSentence.create(paragraph_id="p1", index=0,
                                            text="公司经营情况良好。",
                                            sentence_kind="factual"),
        NS.NarrativeSchemaError, "factual 句无 claim 必须被拒")
    # §十：过渡句现在只能由**封闭词表里的连接语**构成（文本必须逐字等于 connector），
    # 所以「过渡句带数字」不再是一条可以独立触发的规则：合法 connector 自身不含数字，
    # 含数字的文本必然违反「text 逐字等于 connector」。这里既验证该闭包，也如实记录
    # 数字规则因此被上层规则吸收（保留为纵深防御，不再有可构造的入口）。
    check(not any(NS.scan_numeric_tokens(c) for c in NS.CONNECTORS),
          "封闭连接词表本身不得含数字（否则数字可绕过 Claim 进入正文）")
    expect_error(
        lambda: NS.NarrativeSentence.create(paragraph_id="p1", index=0,
                                            text="公司2024年营业收入增长。",
                                            sentence_kind="transition", connector="此外，"),
        NS.NarrativeSchemaError, "transition 句文本不等于 connector 必须被拒",
        needle="逐字等于 connector")
    expect_error(
        lambda: NS.NarrativeSentence.create(paragraph_id="p1", index=0,
                                            text="此外，公司经营稳健。",
                                            sentence_kind="transition",
                                            connector="此外，",
                                            claim_ids=(claim.claim_id,)),
        NS.NarrativeSchemaError, "transition 句携带 claim_id 必须被拒")
    sentence = NS.NarrativeSentence.create(
        paragraph_id="p1", index=0, text="公司主营动力电池系统的研发、生产与销售。",
        sentence_kind="factual", claim_ids=(claim.claim_id,), citation_ids=(cit,))
    expect_error(
        lambda: dataclasses.replace(sentence, text="公司主营动力电池，2024年收入1234.56亿元。"),
        NS.NarrativeSchemaError, "改文本而不重算 numeric_tokens/sentence_id 必须被拒")

    # ------------------------------------------------------------------
    # 3. 段落：跨段落拼装与并集失配
    # ------------------------------------------------------------------
    para = NS.NarrativeParagraph.create(
        section_id="company", topic_ids=("company_business", "company_identity"), index=0,
        sentence_specs=[
            {"text": "公司主营动力电池系统的研发、生产与销售。", "sentence_kind": "factual",
             "claim_ids": (claim.claim_id,), "citation_ids": (cit,)},
            {"text": "此外，", "sentence_kind": "transition", "connector": "此外，"}])
    check(para.claim_ids == (claim.claim_id,), "段落 claim 并集由句子推出")
    expect_error(
        lambda: dataclasses.replace(para, claim_ids=()),
        NS.NarrativeSchemaError, "段落 claim_ids 与句子并集不符必须被拒")
    expect_error(
        lambda: NS.NarrativeParagraph(
            paragraph_id=para.paragraph_id, section_id="company",
            topic_ids=para.topic_ids, index=0,
            sentences=tuple(dataclasses.replace(s, paragraph_id="other")
                            for s in para.sentences),
            claim_ids=para.claim_ids, citation_ids=para.citation_ids),
        NS.NarrativeSchemaError, "句子 paragraph_id 与段落不符必须被拒")

    # ------------------------------------------------------------------
    # 4. 表格：行必须恰有其一 + 数值行必须带 unit/period
    # ------------------------------------------------------------------
    expect_error(
        lambda: NS.NarrativeTableRow.create(table_id="t1", index=0, label="营业收入",
                                            cells=("1234.56",), unit="亿元", period="2024年"),
        NS.NarrativeSchemaError, "表格行既无 Claim 又未标非事实必须被拒")
    expect_error(
        lambda: NS.NarrativeTableRow.create(
            table_id="t1", index=0, label="营业收入", cells=("1234.56",),
            claim_ids=(claim.claim_id,), citation_ids=(cit,), unit="亿元", period="2024年",
            non_factual_reason="display_label_only"),
        NS.NarrativeSchemaError, "表格行同时有 Claim 与非事实标记必须被拒")
    expect_error(
        lambda: NS.NarrativeTableRow.create(
            table_id="t1", index=0, label="营业收入", cells=("1234.56",),
            claim_ids=(claim.claim_id,), citation_ids=(cit,), period="2024年"),
        NS.NarrativeSchemaError, "含数字的表格行缺 unit 必须被拒")
    expect_error(
        lambda: NS.NarrativeTableRow.create(
            table_id="t1", index=0, label="营业收入", cells=("1234.56",),
            claim_ids=(claim.claim_id,), citation_ids=(cit,), unit="亿元"),
        NS.NarrativeSchemaError, "含数字的表格行缺 period 必须被拒")
    NS.NarrativeTableRow.create(table_id="t1", index=0, label="单位", cells=(),
                                non_factual_reason="unit_declaration")
    check(True, "非事实展示行可构造")

    # ------------------------------------------------------------------
    # 4b. §三 G 门后两条守恒/限定判据（`verify_section_narrative` 第 6/7 条）
    #
    # 这两条不是「组织器愿不愿意」的礼貌问题，而是**门**：组织器、组装器、章节评估器规则侧与
    # Store 读回四处共用同一个实现，因此反例直接打在实现上——伪造引用与改挂主题都必须当场被拒。
    # 同时钉住「缺字段不得被当成空集合放行」：缺 `citation_refs`/`topic_id` 的替身对象必须
    # typed fail-closed，否则拿一个只有 id+text 的对象就能跳过这两条核验。
    # ------------------------------------------------------------------
    topic_b_claim = _claim("公司同时经营储能电池系统业务。", topic_id="company_identity",
                           evidence_id="ev2")
    cit_b = _cit_id(topic_b_claim)

    def _narrative(topic_ids: tuple[str, ...], sentence_claim_ids: tuple[str, ...],
                   citation_ids: tuple[str, ...]) -> NS.SectionNarrative:
        paragraph = NS.NarrativeParagraph.create(
            section_id="company", topic_ids=topic_ids, index=0,
            sentence_specs=[{"text": "公司主营动力电池系统的研发、生产与销售。",
                             "sentence_kind": "factual",
                             "claim_ids": sentence_claim_ids,
                             "citation_ids": citation_ids}])
        return NS.SectionNarrative.create(
            task_id="t1", section_id="company", section_draft_id="sd1", draft_revision="dr-1",
            paragraphs=(paragraph,), tables=())

    # 正向对照：合法派生（引用逐条来自本句声明的 Claim、主题等于派生序列）必须过门。
    NS.verify_section_narrative(
        narrative=_narrative(("company_business",), (claim.claim_id,), (cit,)),
        claims=(claim, topic_b_claim))
    NS.verify_section_narrative(
        narrative=_narrative(("company_business", "company_identity"),
                             (claim.claim_id, topic_b_claim.claim_id), (cit, cit_b)),
        claims=(claim, topic_b_claim))
    check(True, "§三 G：合法派生（含跨主题段落按首次出现序）通过门后核验")

    # 第 6 条：引用不是本句声明 Claim 给出的（伪造/事后补引用/引用本节未选中材料的来源）。
    foreign_cit = SS.derive_citation_id(claim.claim_id, _citation("ev-not-mine"))
    expect_error(lambda: NS.verify_section_narrative(
        narrative=_narrative(("company_business",), (claim.claim_id,), (cit, foreign_cit)),
        claims=(claim,)), NS.NarrativeSchemaError,
        "§三 G 6：句子里出现本节 Claim 没给出的引用必须被拒",
        needle="不是它绑定的")

    # 第 7 条：把另一 topic 的事实表面改挂到本 topic 名下（段落自报主题）。
    expect_error(lambda: NS.verify_section_narrative(
        narrative=_narrative(("company_identity",), (claim.claim_id,), (cit,)),
        claims=(claim, topic_b_claim)), NS.NarrativeSchemaError,
        "§三 G 7：段落 topic 归属与它真正引用的 Claim 不符必须被拒",
        needle="topic 归属")

    # 缺字段的替身对象：不得被当成「它没有引用/没有 topic」而放行。
    class _StubClaim:
        claim_id = claim.claim_id
        text = claim.text

    expect_error(lambda: NS.verify_section_narrative(
        narrative=_narrative(("company_business",), (claim.claim_id,), (cit,)),
        claims=(_StubClaim(),)), NS.NarrativeSchemaError,
        "§三 G：缺 citation_refs 的替身 Claim 必须 typed fail-closed（不得默认成空集放行）",
        needle="缺 citation_refs")
    expect_error(lambda: NS.claim_citation_ids(_StubClaim()), NS.NarrativeSchemaError,
                 "§三 G：引用派生对缺字段对象同样 fail-closed", needle="缺 citation_refs")
    expect_error(lambda: NS.claim_topic_for_conservation(_StubClaim()),
                 NS.NarrativeSchemaError,
                 "§三 G：topic 派生对缺 topic_id 的对象同样 fail-closed", needle="缺 topic_id")

    # 组织器与门共用同一个派生口径（引用/主题各一条实现，不允许两边各解释一遍）。
    from sections import narrative_organizer as NO  # 局部导入：本模块其余部分不需要组织器
    check(NO._claim_citation_ids(claim) == NS.claim_citation_ids(claim) == (cit,)
          and NO._claim_citation_ids(topic_b_claim) == (cit_b,),
          "§三 G：组织器与门后核验共用同一个 citation 派生实现（口径不得分叉）")

    # 「缺字段」不得被读成「空集合」：必需事实集与缺口投影都是**结论性集合**，缺字段冒充空集
    # 等于把「无从判定」判成「没有必需事实 / 没有缺口」，比读错更危险（它是 fail-open）。
    class _DerivedNoRequired:
        producer_kind = "derived_section"
        topic_ids = ("company_business",)

    expect_error(lambda: NS.required_fact_ids(_DerivedNoRequired()), NS.NarrativeSchemaError,
                 "§三 G：缺 required_fact_ids 的 derived 权威输入必须 fail-closed（不得当成无必需事实）",
                 needle="缺字段 'required_fact_ids'")

    class _DraftNoGaps:
        paragraphs: tuple = ()
        tables: tuple = ()

    expect_error(lambda: NS.render_section_markdown("公司", _DraftNoGaps()),
                 NS.NarrativeSchemaError,
                 "§三 G：缺 unresolved_projections 的 draft 不得被当成「本节没有缺口」",
                 needle="缺字段 'unresolved_projections'")

    # ------------------------------------------------------------------
    # 5. 门前候选束（narr-4 `SectionDraft`）：exact manifest + WMPD + 候选/单元/proposal
    # ------------------------------------------------------------------
    # narr-4 的 `SectionDraft` 是**门前的候选束**：只承载 candidate / draft unit / proposal /
    # exact material manifest 与处理去向，没有任何门后身份（final Claim、accepted binding、
    # 两个决定、最终 Narrative、Result）。本节先钉住「合法束能构造、能读回」，再逐条反例钉住
    # 「门后身份不可表达」与「候选身份不被支撑/材料污染」。
    _TASK = "t1"
    _WRITER_POLICY = PW.PACK_WRITER_POLICY_VERSION
    _PROMPT_VERSION = "pack_section_writer_proposals_v1"
    _PAYLOAD = {"object_type": "research_material", "authority_identity": "ai-1",
                "version": "v1", "content_hash": "a" * 64,
                "created_dependency_fingerprint": "dep-1"}
    _FINGERPRINT = "a" * 64
    #: 权威事实 f1 的文本。候选正文与它逐字相同时，数字授权与期间都自然成立。
    _F1_TEXT = "公司2024年营业收入为1234.56亿元。"
    _F1 = _fact("f1", _F1_TEXT, period="2024年", amount="1234.56亿元")
    #: f1 的引用来源身份是 `evidence:ev1`（`_fact` 的默认引用），material m1 正是**同一个**
    #: 来源身份 —— 于是权威侧要求 proposal 绑定它（存在却漏绑 = 血缘里抹掉载荷锚点），
    #: 且 `payload_ref` 必须逐字等于 m1 自己的载荷。m2 属于另一个来源身份，用来验证
    #: 「未使用的成员仍须逐条留去向」。
    _M1 = _material("m1", "evidence:ev1", payload=_PAYLOAD)
    _M2 = _material("m2", "evidence:ev2", payload={**_PAYLOAD, "authority_identity": "ai-2"})
    _AUTHORITY_OK = _authority(facts=(_F1,), materials=(_M1, _M2))
    #: `wmm-2`：成员身份必须携带**可解析的真实引用**（§三 A.3）。只有 material ID 的成员
    #: 现在在构造期就不可表达——「成员存在」与「正文已解析并校验」是同一件事。
    _ENTRY_M1 = NS.WriterMaterialManifestEntry.create(
        pack_id="pack1", material_id="m1", research_material_disposition_id="rmd1",
        source_identity="evidence:ev1", provenance_identity="prov1",
        material_content_fingerprint=_FINGERPRINT, topic_id="company_business",
        material_type="evidence_span", payload_ref=dict(_PAYLOAD),
        locator_ref=NS.char_range_locator("evidence:ev1", 0, 40), payload_hash=_PAYLOAD["content_hash"],
        reading_view_fingerprint="c" * 64)
    _ENTRY_M2 = NS.WriterMaterialManifestEntry.create(
        pack_id="pack1", material_id="m2", research_material_disposition_id="rmd2",
        source_identity="evidence:ev2", provenance_identity="prov1",
        material_content_fingerprint="b" * 64, topic_id="company_business",
        material_type="evidence_span",
        payload_ref={**_PAYLOAD, "authority_identity": "ai-2"},
        locator_ref=NS.char_range_locator("evidence:ev2", 0, 40), payload_hash=_PAYLOAD["content_hash"],
        reading_view_fingerprint="d" * 64)

    def bundle(*, candidate_specs, proposal_specs, unit_specs=(), containers=("pack1",),
               entries=None, producer_kind="topic_harness") -> NS.SectionDraft:
        """按「候选文本 + proposal 覆盖项」装配一份门前束；manifest/WMPD 由 proposal 推出。

        proposal 规格用 `subject_kind` + `subject_index` 指向本节第 n 个候选/草稿单元，
        其余键逐项覆盖默认 proposal（容器 / 授权路径 / material / payload / locator…）。
        """
        entries = tuple(entries) if entries is not None else (_ENTRY_M1,)
        manifest = NS.WriterMaterialManifest.create(members=entries)
        revision = NS.derive_draft_revision(
            task_id=_TASK, section_id="company", company_id="c1",
            report_as_of="2024-12-31", contract_version="cv1", contract_fingerprint="cf1",
            writer_policy_version=_WRITER_POLICY, prompt_version=_PROMPT_VERSION,
            model_policy="stub", manifest_id=manifest.manifest_id,
            manifest_fingerprint=manifest.fingerprint())
        candidates = tuple(NS.ClaimCandidate.create(
            draft_revision=revision, task_id=_TASK, section_id="company", company_id="c1",
            report_as_of="2024-12-31", contract_version="cv1", contract_fingerprint="cf1",
            claim_text=text, fact_type=fact_type) for text, fact_type in candidate_specs)
        units = tuple(NS.NarrativeDraftUnit.create(
            draft_revision=revision, section_id="company", index=index, unit_kind=kind,
            text=text) for index, (kind, text) in enumerate(unit_specs))
        subject_ids = {("claim_candidate", index): candidate.candidate_id
                       for index, candidate in enumerate(candidates)}
        subject_ids.update({("narrative_draft_unit", index): unit.draft_unit_id
                            for index, unit in enumerate(units)})
        proposals = []
        for spec in proposal_specs:
            kind = spec["subject_kind"]
            body = {"binding_subject_kind": kind,
                    "binding_subject_id": subject_ids[(kind, spec.get("subject_index", 0))],
                    "draft_revision": revision, "manifest_id": manifest.manifest_id,
                    "manifest_fingerprint": manifest.fingerprint(),
                    "authority_kind": "topic_pack", "authority_container_id": "pack1",
                    "source_identity": "evidence:ev1", "provenance_identity": "prov1",
                    "support_role": "primary", "support_semantics": "factual",
                    "authorization_path": "path_a_prevalidated",
                    "content_fingerprint": _FINGERPRINT, "dependency_fingerprint": "dep-1",
                    "fact_id": "f1", "material_id": "m1", "payload_ref": dict(_PAYLOAD),
                    "locator_ref": None}
            body.update({k: v for k, v in spec.items()
                         if k not in ("subject_kind", "subject_index")})
            proposals.append(NS.ProposedSupportRef.create(**body))
        proposals = tuple(proposals)
        dispositions = []
        for entry in entries:
            usages = tuple(p.proposed_support_id for p in proposals
                           if p.material_id == entry.material_id
                           and p.authority_container_id == entry.pack_id)
            dispositions.append(NS.WriterMaterialProcessingDisposition.create(
                manifest_id=manifest.manifest_id,
                manifest_fingerprint=manifest.fingerprint(), pack_id=entry.pack_id,
                material_id=entry.material_id,
                research_material_disposition_id=entry.research_material_disposition_id,
                material_content_fingerprint=entry.material_content_fingerprint,
                processed=True, usage="used" if usages else "not_used",
                support_usages=usages,
                reason_code=None if usages else "irrelevant_to_section_goal",
                reason_proof=None if usages else {
                    "policy_version": "relpol-1", "policy_fingerprint": "b" * 64,
                    "relevance_decision_ref": "reldec_1"},
                writer_policy_version=_WRITER_POLICY))
        return NS.SectionDraft.create(
            task_id=_TASK, section_id="company", company_id="c1",
            report_as_of="2024-12-31", contract_version="cv1", contract_fingerprint="cf1",
            producer_kind=producer_kind, writer_policy_version=_WRITER_POLICY,
            prompt_version=_PROMPT_VERSION, model_policy="stub",
            authority_container_ids=tuple(containers), material_manifest=manifest,
            material_dispositions=tuple(dispositions), claim_candidates=candidates,
            narrative_draft_units=units, proposed_support_refs=proposals,
            unresolved_ids=(), unresolved_projections=(), coverage_summary={},
            conflict_projections=(), not_found_projections=(),
            dependency_fingerprint="dep-1", title="")

    draft = bundle(candidate_specs=[(_F1_TEXT, "metric")],
                   proposal_specs=[{"subject_kind": "claim_candidate"}])
    check(draft.draft_id.startswith("sdraft_"), "合法门前束可构造")
    check(NS.SectionDraft.from_dict(draft.to_dict()).draft_id == draft.draft_id,
          "门前束 roundtrip 保持 id")
    check(NS.SectionDraft.from_dict(draft.to_dict()).draft_revision == draft.draft_revision,
          "draft_revision 是 Writer 输入 + manifest 身份的确定性函数（读回不变）")
    expect_error(lambda: NS.render_section_markdown("公司", draft),
                 NS.NarrativeSchemaError, "门前束不得被当作含 final Narrative 的章节渲染",
                 needle="final Narrative")

    # 5.1 exact manifest / WMPD 的三层集合等式（§6.4.1）：available = processed，
    #     used ∩ not_used = ∅，used ∪ not_used = available。
    check(draft.material_manifest.entry_for(NS.manifest_member_ref("pack1", "m1")) is not None,
          "manifest 成员键是 (容器, material) 二元组（不得用裸 material_id 折叠）")
    check(tuple(d.usage for d in draft.material_dispositions) == ("used",),
          "被 proposal 引用的材料必须记为 used 并列出该用途")
    unused = bundle(candidate_specs=[(_F1_TEXT, "metric")],
                    proposal_specs=[{"subject_kind": "claim_candidate"}],
                    entries=(_ENTRY_M1, _ENTRY_M2))
    rows = {row.material_id: row for row in unused.material_dispositions}
    check(set(rows) == {"m1", "m2"} and rows["m1"].usage == "used"
          and rows["m2"].usage == "not_used"
          and rows["m2"].reason_code == "irrelevant_to_section_goal"
          and rows["m2"].support_usages == (),
          "每个 manifest 成员恰一条去向；未使用的成员不得静默消失"
          "（not_used + 封闭理由 + 确定性证明）")
    expect_error(lambda: bundle(candidate_specs=[(_F1_TEXT, "metric")],
                                proposal_specs=[{"subject_kind": "claim_candidate",
                                                 "material_id": "m9"}]),
                 NS.NarrativeSchemaError, "proposal 引用 manifest 之外的材料必须被拒",
                 needle="不在 manifest 中")
    expect_error(lambda: bundle(candidate_specs=[(_F1_TEXT, "metric")],
                                proposal_specs=[{"subject_kind": "claim_candidate"}],
                                entries=()),
                 NS.NarrativeSchemaError, "空 manifest 与引用材料的 proposal 不自洽即拒",
                 needle="不在 manifest 中")
    expect_error(lambda: bundle(candidate_specs=[(_F1_TEXT, "metric")],
                                proposal_specs=[{"subject_kind": "claim_candidate",
                                                 "manifest_id": "wmm_other"}]),
                 NS.NarrativeSchemaError, "proposal 绑定别的 manifest identity 必须被拒",
                 needle="manifest")

    # 5.2 §16.10 #1/#2/#30：候选/单元/proposal/束都不得携带任何门后身份。
    _FUTURE_IDS = ("binding_decision_id", "entailment_decision_id",
                   "accepted_support_binding_id", "section_claim_id", "section_result_id",
                   "narrative_gate_result_id", "claim_id", "sentence_id", "paragraph_id",
                   "table_id")
    for _label, _cls in (("ClaimCandidate", NS.ClaimCandidate),
                         ("NarrativeDraftUnit", NS.NarrativeDraftUnit),
                         ("ProposedSupportRef", NS.ProposedSupportRef),
                         ("SectionDraft", NS.SectionDraft)):
        check(not (set(_FUTURE_IDS) & set(_cls.__dataclass_fields__)),
              f"#1：{_label} 类型层没有门后身份字段"
              "（决定 / accepted binding / final Claim / 最终 Narrative / Result）")
    check(not ({"claim_ids", "support_refs", "dispositions", "paragraphs", "tables"}
               & set(NS.SectionDraft.__dataclass_fields__)),
          "#1：门前束没有 narr-3 的定稿 Claim / support edge / 段落 / 表格字段"
          "（候选 ≠ 定稿）")
    for _label, _obj in (("候选", draft.claim_candidates[0]),
                         ("proposal", draft.proposed_support_refs[0]),
                         ("门前束", draft)):
        check(not (set(_FUTURE_IDS) & set(_obj.to_dict())),
              f"#30：{_label} 的 wire 载荷里没有门后身份")
    expect_error(lambda: NS.ProposedSupportRef.from_dict(
        {**draft.proposed_support_refs[0].to_dict(), "accepted_support_binding_id": "asb_1"}),
        NS.NarrativeSchemaError, "#30：读回携带 accepted binding 的 proposal 必须被拒",
        needle="未登记字段")
    expect_error(lambda: NS.ProposedSupportRef.from_dict(
        {**draft.proposed_support_refs[0].to_dict(), "binding_decision_id": "cbd_1"}),
        NS.NarrativeSchemaError, "#30：读回携带 aggregate 决定身份的 proposal 必须被拒",
        needle="未登记字段")
    expect_error(lambda: NS.ClaimCandidate.from_dict(
        {**draft.claim_candidates[0].to_dict(), "section_claim_id": "c_1"}),
        NS.NarrativeSchemaError, "#2：读回被门后身份污染的候选必须被拒",
        needle="未登记字段")
    expect_error(lambda: NS.SectionDraft.from_dict(
        {**draft.to_dict(), "section_result_id": "sr1"}),
        NS.NarrativeSchemaError, "#1：读回回指 Result 的候选束必须被拒",
        needle="未登记字段")

    # #2：候选身份只由「候选文本 + 类型 + revision + 输入坐标」决定；支撑/材料/定位
    # 输入既不进身份也不进载荷（候选与支撑是两条不同的轴）。
    _clean_candidate = draft.claim_candidates[0]
    _polluted = NS.ClaimCandidate.create(
        draft_revision=_clean_candidate.draft_revision, task_id=_TASK, section_id="company",
        company_id="c1", report_as_of="2024-12-31", contract_version="cv1",
        contract_fingerprint="cf1", claim_text=_F1_TEXT, fact_type="metric",
        material_id="m1", fact_id="f1", locator_ref=NS.char_range_locator("ev1", 0, 3),
        citation_refs=("cit1",))
    check(_polluted.candidate_id == _clean_candidate.candidate_id
          and not ({"material_id", "fact_id", "locator_ref", "citation_refs", "payload_ref"}
                   & set(_polluted.to_dict())),
          "#2：候选身份不随支撑/材料输入改变，载荷里也没有这些字段")
    check(not ({"material_id", "fact_id", "locator_ref", "citation_refs", "payload_ref",
                "authority_container_id", "support_role"}
               & set(NS.ClaimCandidate.__dataclass_fields__)),
          "#2：候选类型层没有 support/material/citation 字段（污染在类型层不可表达）")

    # ------------------------------------------------------------------
    # 6. FactNarrativeDisposition successor（门后对象）本身的反例
    # ------------------------------------------------------------------
    # successor 的 authority 维度是封闭 tagged union：本节的每条反例只动**一个**轴，
    # 其余保持合格——否则「被拒」可能来自另一条规则。四类 authority 的 union 矩阵、
    # 集合键相等与「完整有序精确引用集合」在 `test_demo_fact_narrative_disposition` 里测。
    def _fnd(**kw):
        base = dict(authority_kind="topic_pack", authority_container_id="pack1",
                    pack_id="pack1", fact_id="f1", fact_qualification_decision_id="fqd1",
                    disposition="claimed", required=True,
                    source_identity="src1", provenance_identity="prov1",
                    content_fingerprint="f" * 64,
                    accepted_binding_ids=("ab1",), section_claim_ids=("cl1",))
        base.update(kw)
        return NS.FactNarrativeDisposition.create(**base)

    _fnd()
    check(True, "合格的 successor 可构造（以下反例不是恒失败）")
    expect_error(lambda: _fnd(accepted_binding_ids=()),
                 NS.NarrativeSchemaError, "claimed 无 accepted binding 必须被拒",
                 needle="至少一条 factual accepted binding")
    expect_error(lambda: _fnd(section_claim_ids=()),
                 NS.NarrativeSchemaError, "claimed 无 SectionClaim 必须被拒",
                 needle="至少一条 SectionClaim")
    expect_error(
        lambda: _fnd(disposition="not_presented_with_reason", required=False,
                     accepted_binding_ids=(), section_claim_ids=()),
        NS.NarrativeSchemaError, "未呈现缺 reason_code 必须被拒",
        needle="必须给出登记过的 reason_code")
    expect_error(
        lambda: _fnd(disposition="not_presented_with_reason", required=True,
                     reason_code="outside_narrative_scope",
                     accepted_binding_ids=(), section_claim_ids=()),
        NS.NarrativeSchemaError, "required fact 未呈现且无 unresolved 必须被拒",
        needle="required fact")
    _fnd(disposition="not_presented_with_reason", required=True,
         reason_code="outside_narrative_scope", unresolved_id="u1",
         accepted_binding_ids=(), section_claim_ids=())
    check(True, "required fact 未呈现但有 unresolved 可构造")
    # 引用集合必须**确定性排序、无重复**：顺序漂移会让「完整有序精确集合」失去意义。
    expect_error(
        lambda: NS.FactNarrativeDisposition(
            **{**_fnd().to_dict(), "accepted_binding_ids": ["ab2", "ab1"]}),
        NS.NarrativeSchemaError, "引用集合非升序必须被拒", needle="确定性排序")
    expect_error(
        lambda: NS.FactNarrativeDisposition(
            **{**_fnd().to_dict(), "accepted_binding_ids": ["ab1", "ab1"]}),
        NS.NarrativeSchemaError, "引用集合含重复必须被拒", needle="确定性排序")
    # fnd-1 载荷不得冒充 successor：旧字段未登记、旧版本号被拒。
    expect_error(
        lambda: NS.FactNarrativeDisposition.from_dict(
            {"disposition_id": "fnd_x", "authority_kind": "topic_pack",
             "authority_container_id": "pack1", "fact_id": "f1",
             "disposition": "claimed", "required": True, "claim_ids": ["c1"]}),
        NS.NarrativeSchemaError, "fnd-1 载荷（claim_ids）必须被拒", needle="未登记字段")
    expect_error(
        lambda: NS.FactNarrativeDisposition.from_dict(
            {**_fnd().to_dict(), "schema_version": "fnd-1"}),
        NS.NarrativeSchemaError, "fnd-1 版本号必须被拒", needle="fnd-2")

    # ------------------------------------------------------------------
    # 7. gate_draft（门前机械门）：容器 / fact / material / payload / snapshot 载体
    #    + 数字权威 + 含糊期间 + required fact 提示
    # ------------------------------------------------------------------
    # 门只管**门前**能判定的事：它不产生 aggregate `ClaimBindingDecision`（3C），不做原子
    # 蕴含语义判断（P9），也不看 final Narrative 与 FND 闭环（3D）。7.6 把这条分工钉成性质
    # 断言：门的规则词表是**封闭**的，门后规则不得提前在这里出现。
    check(gate_ids(draft, _AUTHORITY_OK) == (), "合法门前束过门（反例不是恒失败）")

    # 7.1 容器 / fact / material / payload：逐条回**权威输入**核验，不看 proposal 自报
    # 反例只换容器身份：material 一起解绑，否则会在**构造期**先撞上 manifest 闭合
    # （那属于 §6.4.1 的材料闭合，不是本节要测的容器解析）。
    ghost_container = bundle(candidate_specs=[(_F1_TEXT, "metric")],
                             proposal_specs=[{"subject_kind": "claim_candidate",
                                              "authority_container_id": "pack_ghost",
                                              "material_id": None, "payload_ref": None}],
                             containers=("pack1", "pack_ghost"))
    _ids = gate_ids(ghost_container, _AUTHORITY_OK)
    check("narrative_container_unknown" in _ids,
          "门认出束声明的权威容器不在当前 WorkerAuthorityInput 内")
    check("support_container_not_in_authority" in _ids,
          "门逐条认出 proposal 的容器不在当前 WorkerAuthorityInput 内")
    unknown_fact = bundle(candidate_specs=[("公司主营业务保持稳定。", "descriptive")],
                          proposal_specs=[{"subject_kind": "claim_candidate",
                                           "fact_id": "f9"}])
    check("support_fact_unknown" in gate_ids(unknown_fact, _AUTHORITY_OK),
          "门认出 proposal 绑定了容器内不存在的 fact（事实身份不得自报）")
    ghost_material = bundle(
        candidate_specs=[("公司主营业务保持稳定。", "descriptive")],
        proposal_specs=[{"subject_kind": "claim_candidate", "material_id": "m9",
                         "payload_ref": None}],
        entries=(NS.WriterMaterialManifestEntry.create(
            pack_id="pack1", material_id="m9", research_material_disposition_id="rmd9",
            source_identity="evidence:ev9", provenance_identity="prov1",
            material_content_fingerprint="c" * 64, topic_id="company_business",
            material_type="evidence_span", payload_ref=dict(_PAYLOAD),
            locator_ref=NS.char_range_locator("evidence:ev9", 0, 40), payload_hash=_PAYLOAD["content_hash"],
            reading_view_fingerprint="e" * 64),))
    check("support_material_unknown" in gate_ids(ghost_material, _AUTHORITY_OK),
          "门认出 proposal 引用了容器内不存在的 material")
    unbound = bundle(candidate_specs=[(_F1_TEXT, "metric")],
                     proposal_specs=[{"subject_kind": "claim_candidate",
                                      "material_id": None, "payload_ref": None}])
    check("support_material_unbound" in gate_ids(unbound, _AUTHORITY_OK),
          "§四：权威事实的引用来源身份确实对应一份 material 时漏绑必须被拒"
          "（material 存在却漏绑 = 血缘里抹掉载荷锚点）")
    mis_bound = bundle(candidate_specs=[(_F1_TEXT, "metric")],
                       proposal_specs=[{"subject_kind": "claim_candidate",
                                        "material_id": "m2", "payload_ref": None}],
                       entries=(_ENTRY_M1, _ENTRY_M2))
    check("support_material_unbound" in gate_ids(mis_bound, _AUTHORITY_OK),
          "§四：绑到另一份**真实存在**的 material 同样被拒（来源身份不对就是不对）")
    wrong_payload = bundle(
        candidate_specs=[(_F1_TEXT, "metric")],
        proposal_specs=[{"subject_kind": "claim_candidate",
                         "payload_ref": {**_PAYLOAD, "content_hash": "9" * 64}}])
    check("support_payload_mismatch" in gate_ids(wrong_payload, _AUTHORITY_OK),
          "payload 锚点必须逐字回查该 material 自己的 payload（不得自证）")

    # 7.2 数字权威（§6.2.1 通道 A / §十一）：数字/日期/币种只能走路径 A 预验证
    _P_FACT = _fact("f-period", "2026-03-31的有息负债为1,264.84亿元。", period="2026-03-31",
                    amount="1,264.84亿元")
    _P_AUTHORITY = _authority(facts=(_P_FACT,), materials=(_M1,))
    copied = bundle(candidate_specs=[(_P_FACT.text, "metric")],
                    proposal_specs=[{"subject_kind": "claim_candidate",
                                     "fact_id": "f-period"}])
    check(gate_ids(copied, _P_AUTHORITY) == (),
          "§十一 对照：逐字照抄权威事实的候选过门（被拒的是改写，不是期间本身）")
    rewritten = bundle(candidate_specs=[("2026年3月末的有息负债为1,264.84亿元。", "metric")],
                       proposal_specs=[{"subject_kind": "claim_candidate",
                                        "fact_id": "f-period"}])
    check("narrative_number_unauthorized" in gate_ids(rewritten, _P_AUTHORITY),
          "§十一：把权威期间 `2026-03-31` 改写成「2026年3月末」必须被判为非权威数字"
          "（门只认权威的写法，不看「看起来是同一段时间」）")
    invented = bundle(candidate_specs=[("2026-03-31的有息负债为9999.99亿元。", "metric")],
                      proposal_specs=[{"subject_kind": "claim_candidate",
                                       "fact_id": "f-period"}])
    check("narrative_number_unauthorized" in gate_ids(invented, _P_AUTHORITY),
          "§十一：候选自造数值（权威事实里没有的数字）必须被拒")
    path_b = bundle(candidate_specs=[(_F1_TEXT, "metric")],
                    proposal_specs=[{"subject_kind": "claim_candidate", "fact_id": None,
                                     "material_id": "m1", "payload_ref": dict(_PAYLOAD),
                                     "locator_ref": NS.char_range_locator("evidence:ev1", 0, 40),
                                     "authorization_path": "path_b_material_derived"}])
    check("narrative_number_unauthorized" in gate_ids(path_b, _AUTHORITY_OK),
          "§6.2.1：只有路径 B 支撑的候选不得携带任何数字"
          "（数字/日期/币种是高风险硬事实；描述性原子不含数字）")
    # 数字授权池只取权威侧的**命题与规范值**：页码 / URL / body hash 是定位/载体字段，不得被
    # 借来「凑」出正文里的数字（历史实现把 citation.page_number 混进池里，这条钉住它不在）。
    _paged = _NS(**{**vars(_fact("f3", "公司有息负债水平保持稳定。")),
                    "citation_refs": (_citation("ev1", page=987654),)})
    _paged_pool = NS.authorized_numeric_tokens(
        NS.authority_numeric_texts("topic_pack", _paged))
    check(not any(NS.numeric_token_authorized(token, _paged_pool)
                  for token in NS.scan_numeric_tokens("987654")),
          "§十一 2：citation 页码不参与数字授权池（数字只能来自权威命题/规范值）")

    # 7.3 含糊期间（§十二 4）：候选与门前草稿单元都不得出现无期间措辞
    vague_candidate = bundle(
        candidate_specs=[("报告期内公司主营业务未发生重大变化。", "descriptive")],
        proposal_specs=[{"subject_kind": "claim_candidate", "fact_id": None,
                         "material_id": "m1", "payload_ref": dict(_PAYLOAD),
                         "locator_ref": NS.char_range_locator("evidence:ev1", 0, 40),
                         "authorization_path": "path_b_material_derived"}])
    check("narrative_vague_period" in gate_ids(vague_candidate, _AUTHORITY_OK),
          "§十二 4：候选文本含「报告期」这类未绑定权威期间的措辞必须被拒")
    vague_unit = bundle(
        candidate_specs=[("公司主营业务保持稳定。", "descriptive")],
        proposal_specs=[{"subject_kind": "claim_candidate", "fact_id": None,
                         "material_id": "m1", "payload_ref": dict(_PAYLOAD),
                         "locator_ref": NS.char_range_locator("evidence:ev1", 0, 40),
                         "authorization_path": "path_b_material_derived"},
                        {"subject_kind": "narrative_draft_unit", "fact_id": None,
                         "material_id": "m1", "payload_ref": dict(_PAYLOAD),
                         "locator_ref": NS.char_range_locator("evidence:ev1", 0, 40),
                         "support_role": "corroborating", "support_semantics": "context",
                         "authorization_path": "context_only"}],
        unit_specs=[("paragraph", "报告期内公司所处行业未发生重大变化。")])
    check("narrative_vague_period" in gate_ids(vague_unit, _AUTHORITY_OK),
          "§十二 4：门前草稿单元文本同样不得含含糊期间措辞")

    # 7.4 required fact 提示：门前只有这条**半条**规则（闭环在门后的 FND / Result）
    two_facts = _authority(facts=(_F1, _fact("f2", "公司动力电池产能为500GWh。")),
                           materials=(_M1, _M2))
    _issues = NS.gate_draft(draft, two_facts).issues
    check(any(i.rule_id == "required_fact_not_proposed" and i.severity == "rework"
              for i in _issues),
          "Contract 必需事实没有任何 factual proposal 引用时只出 rework 级提示"
          "（门前既不得宣布它已呈现，也不得在看不到权威侧 gap 记录时冒充缺口判定）")
    check("required_fact_not_proposed" not in gate_ids(draft, _AUTHORITY_OK),
          "对照：已被引用的必需事实不触发该提示")

    # 7.4b 代理口径必须**显式标记**（M930-3 §三 3.6）：财务权威事实自报 `CALCULATED_PROXY`
    #      时，绑定它的候选必须逐字写出**该事实自己**的口径限定语（`note` 原话）。这里缺的
    #      **不是数字授权**——`authority_numeric_texts` 早已把 `note` 纳入池内，所以「3.2」本来
    #      就合法；缺的是那个限定语本身。写不出来就必须拦在门前：读者会把用代理输入算出来的
    #      数值读成受审的精确值。措辞只有一处来源（`NS.proxy_qualifier`），门不得自造同义词。
    _PROXY_NOTE = "代理口径（INTEREST_EXPENSE_MISSING）"
    _PROXY_FACT = _NS(fact_id="ff1", label="利息保障倍数", code="SOLV_INTEREST_COVER",
                      period="2025-12-31", display="3.2", unit="倍", value_text="3.2",
                      status=NS.PROXY_STATUS, note=_PROXY_NOTE,
                      citation={"ref_type": "evidence", "evidence_id": "ev1",
                                "page_number": 71})
    _PROXY_AUTHORITY = _NS(producer_kind="financial_workflow", company_id="c1",
                           artifact=_NS(artifact_id="fpack1", facts=(_PROXY_FACT,)),
                           note_facts=None, topic_ids=("company_business",),
                           topic_for_fact=lambda fact_id: "company_business")

    def proxy_bundle(text: str, *, note: str = _PROXY_NOTE) -> NS.SectionDraft:
        return bundle(candidate_specs=[(text, "metric")],
                      proposal_specs=[{"subject_kind": "claim_candidate",
                                       "authority_kind": "financial_pack",
                                       "authority_container_id": "fpack1",
                                       "fact_id": None, "financial_fact_id": "ff1",
                                       "material_id": None, "payload_ref": None}],
                      containers=("fpack1",), entries=(),
                      producer_kind="financial_workflow")

    check(NS.is_proxy_fact(_PROXY_FACT)
          and NS.proxy_qualifier(_PROXY_FACT) == _PROXY_NOTE,
          "§三 3.6：代理口径的判定与措辞只有一处来源（权威 status + 权威 note 原话）")
    _proxy_marked = "2025-12-31的利息保障倍数为3.2。" + _PROXY_NOTE + "。"
    check(gate_ids(proxy_bundle(_proxy_marked), _PROXY_AUTHORITY) == (),
          "§三 3.6 对照：逐字写出权威口径限定语的候选过门（正例不是恒失败）")
    check("narrative_proxy_fact_unmarked" in gate_ids(
        proxy_bundle("2025-12-31的利息保障倍数为3.2。"), _PROXY_AUTHORITY),
        "§三 3.6：省略口径限定语必须被拒（这个数字本来就在授权池里，缺的是口径本身）")
    check("narrative_proxy_fact_unmarked" in gate_ids(
        proxy_bundle("2025-12-31的利息保障倍数为3.2，精确值为3.2。"), _PROXY_AUTHORITY),
        "§三 3.6：把代理口径改写成「精确」同样被拒（改写与省略是同一个缺陷）")
    check("narrative_proxy_fact_unmarked" in gate_ids(
        proxy_bundle("2025-12-31的利息保障倍数为3.2，代理口径。"), _PROXY_AUTHORITY),
        "§三 3.6：自造一个近义限定语不算数（必须逐字等于权威自己的措辞）")
    check(NS.PROXY_STATUS == "CALCULATED_PROXY"
          and NS.PROXY_MARKER_PHRASE == "代理口径",
          "§三 3.6：代理状态/标记词的身份取自财务权威的封闭取值，不是本门新造的词表")

    # 7.4c 权威**表面**的每个数字都必须有授权来源（M930-3 返修：财务期间表达漏登记）
    #      `pb-1` 之后财务事实的「期间记号」与「期间表达」分了家：`period` 是身份/分组/列对齐
    #      用的 `2023-12-31`，`period_label` 是给读者看、也**逐字写进正文**的 `2023年末`。
    #      `authoritative_fact_surface` 拼句时用的正是后者，而数字授权池曾经只登记了前者——
    #      于是候选**逐字照抄自己的权威表面**也会被判「未授权数字」，财务节必然 fail-closed。
    #      这里钉住的是**一般不变式**，不是某一个字段：权威表面里的每个数字 token 都必须在池里，
    #      否则门就在拒绝自己的权威表面。给表面加字段时必须同一批在池里登记。
    _FIN_LABELLED = _NS(fact_id="ff2", label="有息负债", code="INTEREST_BEARING_DEBT",
                        period="2023-12-31", period_label="2023年末", display="1,251.59亿元",
                        unit="元", value_text="125159170000.00", status="", note="",
                        citation={"ref_type": "evidence", "evidence_id": "ev1",
                                  "page_number": 71})
    _fin_surface = NS.authoritative_fact_surface("financial_pack", _FIN_LABELLED)
    _fin_pool = NS.authorized_numeric_tokens(
        NS.authority_numeric_texts("financial_pack", _FIN_LABELLED))
    check(_fin_surface == "2023年末的有息负债为1,251.59亿元。",
          f"§十一 2 前置：财务权威表面由权威字段拼出（实测 {_fin_surface!r}）")
    check([t for t in NS.scan_numeric_tokens(_fin_surface)
           if not NS.numeric_token_authorized(t, _fin_pool)] == [],
          "§十一 2：**权威表面自己的每个数字都必须被授权**——表面上写着 `2023年末`，"
          "池里就必须有它（池子与表面同源；少了字段，门就在拒绝自己的权威表面）")
    check("2023年末" in NS.authority_numeric_texts("financial_pack", _FIN_LABELLED),
          "§十一 2：登记的是权威自己的期间表达（`period_label`），不是本门新造的措辞")
    # 反向：**权威没说的**期间表达照样不授权——这一条证明修的是「漏登记一个权威字段」，
    # 而不是把期间表达整体放宽（否则任何年份都能自己写出来）。
    check(not NS.numeric_token_authorized("2024年", _fin_pool),
          "§十一 2 反向：权威说的是 `2023年末`，`2024年` 仍然不被授权"
          "（不是「凡带『年末』都好使」）")
    # 权威没有给出期间表达时，表面逐字退回期间记号；那条路径同样必须自洽。
    _FIN_UNLABELLED = _NS(**{**vars(_FIN_LABELLED), "period_label": ""})
    _unlabelled_surface = NS.authoritative_fact_surface("financial_pack", _FIN_UNLABELLED)
    _unlabelled_pool = NS.authorized_numeric_tokens(
        NS.authority_numeric_texts("financial_pack", _FIN_UNLABELLED))
    check([t for t in NS.scan_numeric_tokens(_unlabelled_surface)
           if not NS.numeric_token_authorized(t, _unlabelled_pool)] == [],
          "§十一 2：权威没给期间表达时表面退回期间记号，该路径同样必须全部被授权"
          "（两条路径都不成立才是缺陷）")

    def fin_bundle(text: str, *, fact=_FIN_LABELLED, container="fpack2") -> NS.SectionDraft:
        return bundle(candidate_specs=[(text, "metric")],
                      proposal_specs=[{"subject_kind": "claim_candidate",
                                       "authority_kind": "financial_pack",
                                       "authority_container_id": container,
                                       "fact_id": None, "financial_fact_id": str(fact.fact_id),
                                       "material_id": None, "payload_ref": None}],
                      containers=(container,), entries=(),
                      producer_kind="financial_workflow")

    _FIN_AUTHORITY = _NS(producer_kind="financial_workflow", company_id="c1",
                         artifact=_NS(artifact_id="fpack2", facts=(_FIN_LABELLED,)),
                         note_facts=None, topic_ids=("company_business",),
                         topic_for_fact=lambda fact_id: "company_business")
    check(gate_ids(fin_bundle(_fin_surface), _FIN_AUTHORITY) == (),
          "§十一 2 正例：逐字照抄权威表面的财务候选过门（照抄自己的权威不得被判未授权数字）")
    check("narrative_number_unauthorized" in gate_ids(
        fin_bundle("2024年末的有息负债为1,251.59亿元。"), _FIN_AUTHORITY),
        "§十一 2 反例：把期间换成本权威**没有**声明的年份仍必须被拒"
        "（授权的是权威的措辞，不是「任意期间表达」）")
    check("narrative_number_unauthorized" in gate_ids(
        fin_bundle("2023年末的有息负债为9,999.99亿元。"), _FIN_AUTHORITY),
        "§十一 2 反例：改动数值仍必须被拒（本次修复只补期间字段，不动数值授权）")

    # 7.5 formal `ExternalFact` 可达（§6.2.3 / 计划行 #32）：容器由 snapshot 身份派生，
    #     事实身份是 external_fact_id，载体的五个字段必须逐字来自该事实自己。
    _EXT = _external_fact()
    _EXT_CONTAINER = NS.external_authority_container_id(_EXT)
    _EXT_PAYLOAD = {"snapshot_id": "snap-1",
                    "canonical_url": "https://example.invalid/disclosure/1",
                    "body_hash": "d" * 64, "source_policy_version": "spol-1",
                    "as_of_date": "2026-06-30"}
    _EXT_AUTHORITY = _authority(facts=(), materials=(), external_facts=(_EXT,))
    _EXT_POOL = NS.authorized_numeric_tokens(
        NS.authority_numeric_texts("external_snapshot", _EXT))
    check(_EXT.canonical_url not in NS.authority_numeric_texts("external_snapshot", _EXT)
          and not any(NS.numeric_token_authorized(token, _EXT_POOL)
                      for token in NS.scan_numeric_tokens(_EXT.canonical_url)),
          "§6.2.3：external 的 URL 与 body hash 是载体字段，不进数字授权池"
          "（否则正文能从一串地址/十六进制里「借」到未受权的数字）")

    def ext_bundle(**overrides) -> NS.SectionDraft:
        spec = {"subject_kind": "claim_candidate", "authority_kind": "external_snapshot",
                "authority_container_id": _EXT_CONTAINER, "fact_id": None,
                "external_fact_id": "ef1", "material_id": None,
                "payload_ref": dict(_EXT_PAYLOAD), "locator_ref": NS.char_range_locator("ev-ext", 0, 12),
                "source_identity": "external:snap-1"}
        spec.update(overrides)
        return bundle(candidate_specs=[(_EXT.statement, "descriptive")],
                      proposal_specs=[spec], containers=(_EXT_CONTAINER,), entries=())

    check(_EXT_CONTAINER.startswith("external_snapshot:")
          and _EXT_CONTAINER != _EXT.external_fact_id,
          "§6.2.3：外部权威的容器身份（snapshot 记录）与事实身份必须分开")
    check(gate_ids(ext_bundle(), _EXT_AUTHORITY) == (),
          "#32：formal ExternalFact 在门前可达（载体逐字一致时过门）")
    check("support_snapshot_ref_mismatch" in gate_ids(
        ext_bundle(payload_ref={**_EXT_PAYLOAD,
                                "canonical_url": "https://evil.invalid/x"}),
        _EXT_AUTHORITY),
        "§6.2.3：proposal 自报的 snapshot 载体与 ExternalFact 自己的载体不一致必须被拒"
        "（snapshot 只是来源载体，不得单独授权，也不得被换成 snippet/URL 正文）")
    check("support_snapshot_ref_mismatch" in gate_ids(
        ext_bundle(payload_ref={**_EXT_PAYLOAD, "body_hash": "9" * 64}), _EXT_AUTHORITY),
        "§6.2.3：body hash 被改写同样必须被拒（载体字段逐字回查）")
    _ext_ghost_authority = _authority(facts=(), materials=(),
                                      external_facts=(_external_fact("ef2"),))
    check("support_fact_unknown" in gate_ids(ext_bundle(), _ext_ghost_authority),
          "§6.2.3：external_fact_id 必须真的在该 snapshot 容器内（不得自报事实身份）")

    # 7.6 分工性质断言：门前门的规则词表**封闭**，门后规则不得提前出现
    _PRE_GATE_RULES = frozenset({
        "narrative_container_unknown", "support_container_not_in_authority",
        "support_fact_unknown", "support_material_unknown", "support_material_ambiguous",
        "support_material_unbound", "support_payload_mismatch", "support_locator_mismatch",
        "support_snapshot_ref_mismatch", "narrative_number_unauthorized",
        "narrative_proxy_fact_unmarked", "narrative_vague_period",
        "required_fact_not_proposed"})
    _seen: set[str] = set()
    for _bundle_obj, _authority_obj in (
            (draft, _AUTHORITY_OK), (ghost_container, _AUTHORITY_OK),
            (unknown_fact, _AUTHORITY_OK), (ghost_material, _AUTHORITY_OK),
            (unbound, _AUTHORITY_OK), (mis_bound, _AUTHORITY_OK),
            (wrong_payload, _AUTHORITY_OK), (copied, _P_AUTHORITY),
            (rewritten, _P_AUTHORITY), (invented, _P_AUTHORITY),
            (path_b, _AUTHORITY_OK), (vague_candidate, _AUTHORITY_OK),
            (vague_unit, _AUTHORITY_OK), (draft, two_facts),
            (ext_bundle(), _EXT_AUTHORITY),
            (proxy_bundle("2025-12-31的利息保障倍数为3.2。"), _PROXY_AUTHORITY),
            (proxy_bundle(_proxy_marked), _PROXY_AUTHORITY)):
        _seen |= set(gate_ids(_bundle_obj, _authority_obj))
    check(_seen <= _PRE_GATE_RULES,
          f"门前门的规则词表封闭（多出 {sorted(_seen - _PRE_GATE_RULES)}）")
    check(not (_seen & {"narrative_fact_surface_drift", "narrative_table_display_drift",
                        "narrative_claim_unknown", "disposition_missing"}),
          "事实表面守恒 / 表格展示元数据 / final Claim 归属 / 缺失去向都**不**在门前门里"
          "（它们要看到 final Narrative 与 FND 闭环，属 3C/3D）")

    # ------------------------------------------------------------------
    # 8. 绑定与组装身份
    # ------------------------------------------------------------------
    binding = NS.NarrativeEvaluationBinding.create(
        section_result_id="sr1", section_draft_id=draft.draft_id, evaluation_id="ev1",
        narrative_gate_result_id="ngr1", rules_version=NS.NARRATIVE_RULES_VERSION,
        gate_version=NS.NARRATIVE_GATE_VERSION)
    check(NS.NarrativeEvaluationBinding.from_dict(binding.to_dict()).binding_id
          == binding.binding_id, "binding roundtrip 保持 id")
    expect_error(
        lambda: dataclasses.replace(binding, section_draft_id="sdraft_other"),
        NS.NarrativeSchemaError, "手改 draft 身份而不重算 binding 必须被拒")

    section = NS.AssembledSection(
        section_id="company", title="公司", section_result_id="sr1",
        section_draft_id=draft.draft_id, evaluation_id="ev1",
        narrative_gate_result_id="ngr1", binding_id=binding.binding_id,
        decision="PASS_WITH_GAPS", coverage_summary={"covered": 0, "partial": 1},
        claim_ids=(claim.claim_id,), paragraph_ids=(para.paragraph_id,), table_ids=(),
        unresolved_ids=("u1",), markdown="正文")

    # §四：报告版本**只有一个**权威根 —— 冻结的 `ReportVersionIdentity`。这里没有任何
    # 「report_version 由正文自己派生」的第二套口径。
    # M930-3 任务二（§4.6）：身份除 body_fingerprint 外还覆盖「各节 Draft 身份」与
    # 「排除自身版本字段后的 assembled canonical payload」指纹。
    def payload_for(markdown: str, *, sections=None, **overrides) -> dict:
        body = {
            "schema_version": NS.REPORT_SCHEMA_VERSION,
            "job_id": "job1", "company_id": "c1", "report_as_of": "2026-06-30",
            "profile_fingerprint": "e" * 64, "projection_id": "proj_" + "0" * 24,
            "contract_version": "v2", "contract_fingerprint": "a" * 64,
            "assembler_version": "asm-4",
            "sections": (section,) if sections is None else tuple(sections),
            "scope_coverage": (), "disposition_index": (), "support_ref_ids": (),
            "claim_ids": (), "gap_index": (), "conflict_index": (),
            "authority_container_ids": (), "retention": {}, "markdown": markdown,
            "dependency_fingerprint": "1" * 64,
        }
        body.update(overrides)
        return body

    def rvi(payload: dict, **overrides) -> BACKBONE.ReportVersionIdentity:
        kw = dict(
            profile_fingerprint="e" * 64, scope_input_fingerprint="f" * 64,
            projection_id="proj_" + "0" * 24, plan_id="dplan_" + "0" * 24,
            job_id="job1", company_id="c1", report_as_of="2026-06-30",
            selected_task_ids=("dtask_a",), topic_pack_ids=("pack1",),
            financial_fact_pack_artifact_id=None,
            contract_fingerprint="a" * 64, source_policy_fingerprint="b" * 64,
            writing_spec_fingerprint="c" * 64, presentation_profile_fingerprint="d" * 64,
            dependency_fingerprint="1" * 64,
            body_fingerprint=NS.body_fingerprint_of(payload["markdown"]),
            section_draft_ids=tuple(sorted(
                str(s.section_draft_id) for s in payload["sections"])),
            assembled_payload_fingerprint=NS.assembled_payload_fingerprint(**payload),
            narrative_schema_version=NS.NARRATIVE_SCHEMA_VERSION,
            claim_schema_version=SS.CLAIM_SCHEMA_VERSION,
            table_schema_version=SS.TABLE_SCHEMA_VERSION,
            writer_schema_version=PW.WRITER_RENDERER_VERSION,
            assembler_schema_version="asm-4", prompt_version=PW.NARRATION_PROMPT_VERSION,
            # 判定链的两个规则版本也是 report version 的承重输入（§四）：门/蕴含规则一变，
            # 同一份正文的可信度就不再相同，必须换 report_version，不能沿用旧号。
            claim_binding_gate_version=NS.CLAIM_BINDING_GATE_VERSION,
            claim_entailment_rules_version=NS.CLAIM_ENTAILMENT_RULES_VERSION,
            model_policy_id="mp-1")
        kw.update(overrides)
        return BACKBONE.ReportVersionIdentity.build(**kw)

    def build_report(markdown: str, *, sections=None, generated_at: str = "",
                     version_identity=None, **payload_overrides) -> NS.AssembledReport:
        payload = payload_for(markdown, sections=sections, **payload_overrides)
        return NS.AssembledReport.create(
            version_identity=(version_identity if version_identity is not None
                              else rvi(payload)),
            **payload, generated_at=generated_at)

    report = build_report("正文")
    check(NS.AssembledReport.from_dict(report.to_dict()).report_version
          == report.report_version, "report roundtrip 保持 version")
    expect_error(
        lambda: dataclasses.replace(report, markdown="正文（改）"),
        NS.NarrativeSchemaError, "正文变化而不重算 report_version 必须被拒",
        needle="assembled_payload_fingerprint")
    # 载荷指纹已重算、只差 body_fingerprint 的那一路也必须被拒（两条路都要 fail-closed）
    payload_changed = payload_for(
        "正文（改）", sections=(dataclasses.replace(section, markdown="正文（改）"),))
    expect_error(
        lambda: NS.AssembledReport.create(
            version_identity=rvi(payload_changed,
                                 body_fingerprint=NS.body_fingerprint_of("正文")),
            **payload_changed),
        NS.NarrativeSchemaError, "正文变化而 body_fingerprint 未同步重算必须被拒",
        needle="body_fingerprint")
    changed = build_report("正文（改）", sections=(dataclasses.replace(section, markdown="正文（改）"),))
    check(changed.report_version != report.report_version,
          "正文变化后 report_version 必须改变")
    check("generated_at" not in report.identity_body()
          and "generated_at" not in report.payload_kwargs()
          and "report_id" not in report.payload_kwargs()
          and "report_version" not in report.payload_kwargs()
          and "version_identity" not in report.payload_kwargs(),
          "report_version 覆盖范围不含 generated_at / report_id / report_version / "
          "version_identity（排除字段闭集）")
    stable = build_report("正文", generated_at="2099-01-01T00:00:00Z")
    check(stable.report_version == report.report_version,
          "同内容不同生成时间（操作性 run 字段）必须得到同一 report_version")

    # --- §4.6 反例 A：只改各节 SectionDraft 身份（正文与 Pack 逐字节不变）→ 必须换版本 ---
    renamed_section = dataclasses.replace(section, section_draft_id="sdraft_probe_0001")
    renamed = build_report("正文", sections=(renamed_section,))
    check(renamed.report_version != report.report_version,
          "只改各节 SectionDraft 身份（正文与依赖指纹不变）必须换出新的 report_version")
    expect_error(
        lambda: dataclasses.replace(report, sections=(renamed_section,)),
        NS.NarrativeSchemaError, "Draft 身份变化而不重算身份必须被拒",
        needle="SectionDraft")
    expect_error(
        lambda: build_report("正文", sections=(renamed_section,),
                             version_identity=rvi(payload_for("正文"))),
        NS.NarrativeSchemaError, "身份声明的 Draft 身份与产物不符必须被拒",
        needle="SectionDraft")

    # --- §4.6 反例 B：改未呈现在 Markdown 中但属于报告身份的内容 → 必须换版本 ---
    for label, over in {
        "retention": dict(retention={"company:dtask_a": {"kept": 1}}),
        "claim_ids": dict(claim_ids=("clm_probe_0001",)),
        "gap_index": dict(gap_index=({"section_id": "company", "unresolved_id": "u2"},)),
        "scope_coverage": dict(scope_coverage=({"section_id": "company"},)),
        "authority_container_ids": dict(authority_container_ids=("pack_probe_0001",)),
        "assembler_version": dict(assembler_version="asm-5"),
    }.items():
        other = build_report("正文", **over)
        check(other.report_version != report.report_version,
              f"改非 Markdown 载荷 {label} 必须换出新的 report_version")
    expect_error(
        lambda: dataclasses.replace(report, retention={"probe": True}),
        NS.NarrativeSchemaError, "载荷变化而不重算身份必须被拒",
        needle="assembled_payload_fingerprint")

    # --- §4.6 反例 C：规范载荷的纳入/排除闭集必须覆盖全部字段 ---
    check(set(NS.ASSEMBLED_PAYLOAD_INCLUDED_FIELDS)
          | set(NS.ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS)
          == set(NS.AssembledReport.__dataclass_fields__)
          and not (set(NS.ASSEMBLED_PAYLOAD_INCLUDED_FIELDS)
                   & set(NS.ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS)),
          "规范载荷纳入/排除闭集互补且恰好覆盖 AssembledReport 全部字段")
    expect_error(
        lambda: NS.assembled_payload_body(
            **{**report.payload_kwargs(), "report_id": "rpt_probe"}),
        NS.NarrativeSchemaError, "被排除字段不得进入规范载荷")
    expect_error(
        lambda: NS.assembled_payload_body(
            **{k: v for k, v in report.payload_kwargs().items() if k != "retention"}),
        NS.NarrativeSchemaError, "缺纳入字段不得构造规范载荷")
    check(isinstance(NS.assembled_payload_fingerprint_of(report), str)
          and len(NS.assembled_payload_fingerprint_of(report)) == 64,
          "规范载荷指纹可独立重算且为 64 位 hex")

    # --- §4.6 反例 D：旧 schema / 旧身份对象不得静默当 current ---
    expect_error(
        lambda: NS.AssembledReport.from_dict(
            {**report.to_dict(),
             "version_identity": {**report.version_identity.to_dict(),
                                  "schema_version": "demo-report-version-v2"}}),
        BACKBONE.BackboneSchemaError, "legacy 报告身份不得被 current reader 读成 current",
        needle="legacy")
    expect_error(
        lambda: NS.AssembledReport.from_dict({**report.to_dict(),
                                              "schema_version": "abr-2"}),
        NS.NarrativeSchemaError, "旧 abr schema 的组装产物不得被读成 current",
        needle="abr-3")

    # 同形替身不得冒充权威身份（§四：不得再出现第二套报告版本类型）
    expect_error(
        lambda: build_report("正文", version_identity=_NS(
            content_fingerprint="0" * 64, body_fingerprint=NS.body_fingerprint_of("正文"))),
        NS.NarrativeSchemaError, "同形状对象冒充 ReportVersionIdentity 必须被拒",
        needle="ReportVersionIdentity")
    expect_error(
        lambda: build_report("正文", version_identity=rvi(
            payload_for("正文"), body_fingerprint="9" * 64)),
        NS.NarrativeSchemaError, "身份里的 body_fingerprint 与正文不符必须被拒",
        needle="body_fingerprint")
    expect_error(
        lambda: build_report("正文", version_identity=rvi(
            payload_for("正文"), assembled_payload_fingerprint="9" * 64)),
        NS.NarrativeSchemaError, "身份里的规范载荷指纹与产物不符必须被拒",
        needle="assembled_payload_fingerprint")

    # ------------------------------------------------------------------
    # 9. 门结果自身的内容寻址
    # ------------------------------------------------------------------
    gate_ok = NS.NarrativeGateResult.build(section_draft_id=draft.draft_id, issues=())
    check(gate_ok.rules_passed and not gate_ok.blocking, "空 problems 的门 rules_passed=True")
    gate_bad = NS.NarrativeGateResult.build(
        section_draft_id=draft.draft_id,
        issues=(NS.NarrativeGateIssue(rule_id="r", severity="blocking", location="l",
                                      detail="d"),))
    check(gate_bad.blocking and not gate_bad.rules_passed, "blocking issue 使门不通过")
    check(gate_bad.gate_result_id != gate_ok.gate_result_id, "门结果 id 随 issue 变化")
    expect_error(
        lambda: dataclasses.replace(gate_ok, section_draft_id="sdraft_other"),
        NS.NarrativeSchemaError, "手改门结果的 draft 身份必须被拒")

    # ------------------------------------------------------------------
    # 10. required_fact_ids 只依据类型化字段
    # ------------------------------------------------------------------
    req = NS.required_fact_ids(two_facts)
    check(req == frozenset({"f1", "f2"}), f"blocking aspect 的 fact 全是 required，实为 {req}")
    req2 = NS.required_fact_ids(_authority(
        facts=(_fact("f1", "文本。", amount="1"),), blocking=False))
    check(req2 == frozenset(), "非 blocking aspect 的 fact 不是 required")

    # ------------------------------------------------------------------
    # 11. Draft→Result 单向（#16）与身份失效（#18）
    # ------------------------------------------------------------------
    # #16：Draft 是门**前**对象，post-gate Result 单向引用它；反向边（Draft 回指 Result /
    # 内嵌 evaluation）在**类型层、载荷层、读回层**三处都必须不可表达。此时 `draft` 是
    # §5 造出的合法 draft（若它自己带 Result 引用，下面的读回断言就会自相矛盾）。
    check("section_result_id" not in NS.SectionDraft.__dataclass_fields__,
          "#16：SectionDraft 类型上没有 section_result_id 字段（narr-4 反向边不可表达）")
    check("section_draft_id" in SS.SectionResult.__dataclass_fields__
          and "section_result_id" not in NS.SectionDraft.__dataclass_fields__,
          "#16：Result→Draft 单向（Result 持 section_draft_id，Draft 没有回指字段）")
    check("section_result_id" not in draft.to_dict()
          and "evaluation_id" not in draft.to_dict(),
          "#16：draft 载荷里没有 section_result_id / evaluation_id")
    expect_error(
        lambda: NS.SectionDraft.from_dict({**draft.to_dict(), "section_result_id": "sr1"}),
        NS.NarrativeSchemaError, "#16：读回带 section_result_id 的 draft 必须被拒")
    check(NS.SectionDraft.create(**draft.to_dict()).draft_id == draft.draft_id,
          "#16：合法重建路径（create(**to_dict())）不因缺 Result 引用而变形")
    # 写侧工厂是 kwargs 形状：多给的 `section_result_id` 既不进身份也不进载荷。
    # （kwargs 工厂对未登记键是**静默丢弃**，这本身是观察项；此处只钉「反向边进不了 wire」。）
    smuggled = NS.SectionDraft.create(**{**draft.to_dict(), "section_result_id": "sr1"})
    check(smuggled.draft_id == draft.draft_id
          and "section_result_id" not in smuggled.to_dict(),
          "#16：经写侧工厂夹带的 Result 引用被丢弃，进不了 draft 身份/载荷")
    expect_error(
        lambda: NS.AssembledSection(
            section_id="company", title="公司", section_result_id="sr1", section_draft_id="",
            evaluation_id="ev1", narrative_gate_result_id="ngr1",
            binding_id=binding.binding_id, decision="PASS", coverage_summary={},
            claim_ids=(), paragraph_ids=(), table_ids=(), unresolved_ids=(), markdown="正文"),
        NS.NarrativeSchemaError,
        "#16：post-gate 组装切片缺 draft 身份必须被拒（Result 必须指向它的 Draft）")

    # #18：改句子文本 / 绑定、改 draft 的标题或缺口投影，都必须使**内容身份**失效 ——
    #「改字段但沿用旧 id」一律拒；身份不覆盖这些字段的话，改一处而版本不变就是静默换内容。
    phrase_b = _claim("公司同时经营储能电池系统业务。", evidence_id="ev2")
    phrase_b_cit = _cit_id(phrase_b)
    sentence_b = NS.NarrativeSentence.create(
        paragraph_id="p1", index=0, text=phrase_b.text, sentence_kind="factual",
        claim_ids=(phrase_b.claim_id,), citation_ids=(phrase_b_cit,))
    check(sentence_b.sentence_id != sentence.sentence_id,
          "#18：不同文本/不同绑定的句式身份不同（对照：不是恒等 id）")
    expect_error(
        lambda: dataclasses.replace(sentence, claim_ids=(phrase_b.claim_id,)),
        NS.NarrativeSchemaError, "#18：改句子绑定的 Claim 而不重算 sentence_id 必须被拒")
    expect_error(
        lambda: dataclasses.replace(sentence, citation_ids=(phrase_b_cit,)),
        NS.NarrativeSchemaError, "#18：改句子绑定的引用而不重算 sentence_id 必须被拒")
    expect_error(
        lambda: dataclasses.replace(para, index=1),
        NS.NarrativeSchemaError, "#18：改段落序号而不重算 paragraph_id 必须被拒")
    row = NS.NarrativeTableRow.create(
        table_id="t1", index=0, label="营业收入", cells=("1234.56",),
        claim_ids=(claim.claim_id,), citation_ids=(cit,), unit="亿元", period="2024年")
    expect_error(
        lambda: dataclasses.replace(row, cells=("9999.99",)),
        NS.NarrativeSchemaError, "#18：改表格单元而不重算 row_id 必须被拒")
    expect_error(
        lambda: dataclasses.replace(draft, title="公司概况"),
        NS.NarrativeSchemaError, "#18：改 draft 标题而不重算 draft_id 必须被拒")
    expect_error(
        lambda: dataclasses.replace(draft, coverage_summary={"covered": 3}),
        NS.NarrativeSchemaError, "#18：缺口/覆盖投影变化而不重算 draft_id 必须被拒")
    rebuilt = NS.SectionDraft.create(**{**draft.to_dict(), "title": "公司概况"})
    check(rebuilt.draft_id != draft.draft_id,
          "#18：标题变化后 draft_id 确实不同（身份覆盖该字段，不是同一版本的换皮）")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

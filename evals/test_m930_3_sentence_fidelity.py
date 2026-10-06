"""M930-3 指令 E 第 3 项的聚焦判据集：门前**自然草稿层**（`narr-8`：出处两条互斥轴）+ 门后
**最终句保真核对**（`natfid-1` / `ng-15` / `nrules-16`）。

跑法（无管道 / 无重定向）：`python -X utf8 -m evals.test_m930_3_sentence_fidelity`

这一批是**本门第一次**在某个句子类别上把「逐字等于 Claim 文本」换成别的判据，因此本模块的
职责不是「多测几条」，而是把**换掉了什么、没换什么、新加了什么、以及没覆盖什么**逐条钉死：

* §1 版本与三张封闭补语表：`natfid-1`、四元缺陷码、单字阈值表，以及「补语表是**新增**的、
  不是删了风险词换来的」——逐词对照 `HIGH_RISK_SURFACE_MARKERS` 验证两份表的关系。
* §2 `assertion_critical_surfaces` 的**内容**（这是「不得丢」方向的唯一口径）：数字 / 否定与状态
  标记 / 含糊期间 / 实体头部名词 / 范围与结论强度语，按首次出现序去重；空文本给空。
* §3 七轴逐轴正例 + 反例（直接调 `verify_sentence_fidelity`）：
  1 数量（`1,235,000,000元` → `12.35亿元`）、2 期间（丢 `2024年`）、3 否定（`未发生` → `发生`）、
  4 范围（`部分` → `全部`）、5 结论强度（加 `显著`）、6 因果（加 `由于`）、7 引用（`stray_citation`）。
  每条反例都**钉住方向**：是「丢」还是「添」，两个方向不得混成一个理由。
* §4 单字阈值：`均` / `各` 作为子串不得误伤，而长度 ≥ 2 的范围语（`部分`）照旧命中。这是本判据
  **唯一**的放宽处，且被限定在一张显式声明的封闭表里。
* §5 结论指纹与只读性：句文本 / 绑定集合 / Claim 文本任一改动都换指纹（「结论过期」不得伪装成
  「仍然有效」）；空白归一不换指纹；报告是冻结对象、不写任何东西；不传 `claim_citations` 时
  **这一轴不核**（`stray_citations` 恒空）——那是「没做」，不是「通过了」。
* §6 `NaturalProseDraftUnit` + `natural_prose_draft_digest` + `validate_natural_prose_mapping`：
  六类映射问题逐条可定位；草稿单元在类型层**不可表达**未来的决定/产物字段；它不是第三种
  binding subject；出处的**两条互斥轴**各有正例（材料轴 / 事实轴），空两轴、两轴同时非空、
  以及两种键写错轴各有一条反例。
* §7 **门前草稿层非空的 `SectionDraft` 必须真的构造得出来**（这是本批实测到一个**阻断性**缺陷的
  回归：摘要一旦取 `identity_body()`（内含 `draft_revision`）、而 `draft_revision` 又由该摘要推出，
  就得到 `R = f(g(R))` 这个**无解**的不动点方程，草稿层非空的载荷根本无法构造）。这里同时钉住
  两遍顺序是**必需**的（单元带着临时修订必须 fail-closed），以及旧载荷的修订**一字未动**。
* §8 `natural` 在冻结门里的**分派**：同一段文本写成 `natural` 放行、写成 `composed` 照旧被拒
  ——证明这是**新增的一支**，不是把第 8 条删掉了；同时第 2/3 条（不得新增高风险表面）与第 4 条
  （context 只作背景）对 `natural` 一句不放宽；句类只能由系统标注，模型自报不算。
* §9 **诚实的边界**（不含糊声明，逐条给可执行证据）：头部名词替身对「光杆公司」开头无读数；
  整句扫描会在子句边界**合成**出不存在的表面（只会错拒合法改写，不会放过越权）；「具体主体 →
  泛指」（`该公司`）在本核验下**两方向都拦不住**，由 Claim 级蕴含门与人工读回承担。
* §10 唯一实现与机械纪律：定义只有一处，且 schema 层不 import `llm`。

夹具纪律：不读库、不连网、不调 LLM、不写任何文件；文本夹具全部公司无关（`示例…`），不写
固定页码、不写答案关键词、不为任何一家公司写专用规则。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as HS
from harness import topic_schema as TS
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import schema as SS

REPO = Path(__file__).resolve().parent.parent

TOPIC = "topic-company-business"
IDENTITY = {"task_id": "task-1", "section_id": "company",
            "section_draft_id": "draft-1", "draft_revision": "rev-1"}

FP = "a" * 64
MEMBER_LOCATOR = NS.char_range_locator("evidence:ev-1", 3, 40)
#: 真实形状的成员引用（§三 A.7：只有 material ID 的成员在构造期就不可表达）。
PAYLOAD_REF = TS.MaterialPayloadRef(
    object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
    content_hash=FP,
    locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                               section_path="s1", page=3),
    created_dependency_fingerprint="d" * 64).to_dict()
MANIFEST = NS.WriterMaterialManifest.create(members=(
    NS.WriterMaterialManifestEntry.create(
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=PAYLOAD_REF, locator_ref=MEMBER_LOCATOR,
        payload_hash=FP, reading_view_fingerprint=FP),))
MEMBER_REF = MANIFEST.member_refs()[0]
#: **另一个 Pack 的**同名成员（不是本节 manifest 里的那一行）。用来把「引用了清单之外的成员」
#: 与「换了一个成员 ⇒ 摘要变」两件事都写成真实形状的键，而不是裸 `m-other` 这种不在任何一条
#: 出处轴命名空间里的字符串（`pprov-1` 之后，那种字符串在构造期就不可表达）。
OTHER_MEMBER_REF = NS.manifest_member_ref("pack-2", "m-1")


def _occ_entry(material_id: str, document_id: str, document_version: str,
               *, pack_id: str = "pack-1") -> NS.WriterMaterialManifestEntry:
    """`npr-1` 归属核对用的成员：locator 的 owner 是**声明了文档两轴**的容器
    （`evidence_document:<doc>@<ver>#…`，与 `SRS.declared_axes_from_locator` 读的那个面
    逐字同形），因此这一组夹具上归属核对会真的比到文档版本与容器，而不只比材料 ID。"""
    return NS.WriterMaterialManifestEntry.create(
        pack_id=pack_id, material_id=material_id, research_material_disposition_id="rmd-occ",
        source_identity=f"evidence:{material_id}", provenance_identity=f"prov-{material_id}",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=PAYLOAD_REF,
        locator_ref=NS.char_range_locator(
            f"evidence_document:{document_id}@{document_version}#block_span", 3, 40),
        payload_hash=FP, reading_view_fingerprint=FP)


def _occ_member(material_id: str, document_id: str, document_version: str,
                *, pack_id: str = "pack-1") -> str:
    return NS.manifest_member_ref(pack_id, material_id)


#: `npr-1` 的三行成员表：**两份不同的文档**各一行，外加**同一 material_id 的另一个容器**
#: （第三行）。三样东西正是「这一次表达落在哪一行上」要分开的三格：材料不同 / 版本不同 /
#: 容器不同。这一组夹具是**独立的**（不动 `MANIFEST`：那份夹具的处置面只登记一个成员，
#: 加成员会让「处置完备」那条判据在别的用例里变味）。
OCC_MANIFEST = NS.WriterMaterialManifest.create(members=(
    _occ_entry("m-1", "doc-1", "dv-1"),
    _occ_entry("m-2", "doc-2", "dv-2"),
    _occ_entry("m-1", "doc-1", "dv-1", pack_id="pack-2"),
))
#: 一条**事实侧**出处键（`narr-8` 的第二条轴）。纯 `FinancialFactPack` 节里材料清单合法为空，
#: 草稿的出处只能落在这一轴上——本模块用它把两轴的命名空间与互斥性各钉一条反例。
FACT_REF = NS.fact_provenance_ref("financial_fact_pack", "ffp-1", "fact-1")


def _claim(text: str, index: int) -> SS.SectionClaim:
    """一条真实形状的 current Claim（正文核验读的就是它：`claim_id` / `text` / `citation_refs`）。"""
    question_ids = (f"q-{index}",)
    refs = (HS.CitationRef(ref_type="evidence", evidence_id=f"evidence:ev-{index}"),)
    claim_id = SS.derive_claim_id("fact", TOPIC, question_ids, text, refs,
                                  f"cand-{index}", "rev-1", (f"asb-{index}",))
    return SS.SectionClaim(
        claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
        topic_id=TOPIC, question_ids=question_ids, text=text, claim_type="fact",
        citation_refs=refs, claim_candidate_id=f"cand-{index}",
        claim_candidate_revision="rev-1", accepted_binding_ids=(f"asb-{index}",))


def _citations(claim_objs) -> list[str]:
    out: list[str] = []
    for c in claim_objs:
        for ref in c.citation_refs:
            cid = SS.derive_citation_id(c.claim_id, ref)
            if cid not in out:
                out.append(cid)
    return out


def _narrative(text: str, kind: str, claim_objs, *, section_context=(),
               sentence_context=None) -> NS.SectionNarrative:
    """节级（本节**已接受**的 context 绑定全集）与句级（这一句**实际使用**的）是两个量：句级是
    节级的子集。默认两边同批，即正常的「已接受 → 被本句使用」形态；要造「凭空引用」的反例，
    就把句级抬高到节级之外（`sentence_context=("ctx-ghost",)`）。"""
    used = tuple(section_context if sentence_context is None else sentence_context)
    specs = [{"text": text, "sentence_kind": kind,
              "claim_ids": [c.claim_id for c in claim_objs],
              "citation_ids": _citations(claim_objs),
              "context_binding_ids": used}]
    paragraph = NS.NarrativeParagraph.create(
        section_id="company", topic_ids=(TOPIC,), index=0, sentence_specs=specs)
    return NS.SectionNarrative.create(
        task_id=IDENTITY["task_id"], section_id="company",
        section_draft_id=IDENTITY["section_draft_id"],
        draft_revision=IDENTITY["draft_revision"], paragraphs=(paragraph,), tables=(),
        context_binding_ids=tuple(section_context))


def _gate_error(text: str, kind: str, claim_objs, *, section_context=(),
                sentence_context=None, accepted_context_binding_ids=None) -> str:
    """冻结门对这一句的判定：拒 → 错误文本；放行 → 空串。"""
    if accepted_context_binding_ids is None:
        accepted_context_binding_ids = tuple(section_context)
    try:
        narrative = _narrative(text, kind, claim_objs, section_context=section_context,
                               sentence_context=sentence_context)
        NS.verify_section_narrative(
            narrative=narrative,
            claims=claim_objs, accepted_context_binding_ids=accepted_context_binding_ids,
            dispositions=())
    except NS.NarrativeSchemaError as e:
        return str(e)
    return ""


def _fidelity(text: str, claim_objs, **kw) -> dict:
    report = NS.verify_sentence_fidelity(text=text, claims=claim_objs, **kw)
    return {"ok": report.ok, "defects": list(report.defects()),
            "added": list(report.added_surfaces),
            "dropped": [list(x) for x in report.dropped_surfaces],
            "extra": [list(x) for x in report.added_scope_or_strength],
            "stray": list(report.stray_citations)}


# ===========================================================================
# 文本夹具（公司无关）
# ===========================================================================
CA = "公司2024年营业收入为1,234.56亿元。"
CB = "公司2025年营业收入同比增长。"
NUMBER_CLAIM = "公司2024年营业收入为1,235,000,000元。"
NEGATION_CLAIM = "公司2024年经营环境未发生重大变化。"
SCOPE_CLAIM = "公司部分产品用于储能领域。"
TREND_CLAIM = "公司2024年营业收入下降。"
COST_CLAIM = "公司2024年营业收入下降，营业成本下降。"
PERIOD_CLAIM = "公司2024年营业收入为100亿元。"
FULLNAME_CLAIM = "示例新能源科技股份有限公司2024年营业收入为100亿元。"
GROWTH_CLAIM = "公司2024年营业收入较上年同期保持增长。"
REORDER_CLAIM = "公司主要从事动力电池的研发、生产与销售。"

C1 = _claim(CA, 1)
C2 = _claim(CB, 2)
C_NUMBER = _claim(NUMBER_CLAIM, 3)
C_NEGATION = _claim(NEGATION_CLAIM, 4)
C_SCOPE = _claim(SCOPE_CLAIM, 5)
C_TREND = _claim(TREND_CLAIM, 6)
C_COST = _claim(COST_CLAIM, 7)
C_PERIOD = _claim(PERIOD_CLAIM, 8)
C_FULLNAME = _claim(FULLNAME_CLAIM, 9)
C_GROWTH = _claim(GROWTH_CLAIM, 10)
C_REORDER = _claim(REORDER_CLAIM, 11)


def _prose_draft(*, revision: str, text: str, member_refs, atom_ids,
                 fact_refs=(), index: int = 0) -> NS.NaturalProseDraftUnit:
    """草稿单元工厂。两条出处轴各自显式传入（缺省：材料轴非空、事实轴为空）。"""
    return NS.NaturalProseDraftUnit.create(
        draft_revision=revision, section_id="company", index=index, text=text,
        source_member_refs=tuple(member_refs), source_fact_refs=tuple(fact_refs),
        atom_candidate_ids=tuple(atom_ids))


def _prose_probe_candidate() -> NS.ClaimCandidate:
    """只为取身份前缀/映射替身用的最小候选（不参与任何语义断言）。"""
    return NS.ClaimCandidate.create(
        draft_revision="r", task_id="t-1", section_id="company", company_id="c-1",
        report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
        claim_text="x", fact_type="metric")


def _revisioned(unit: NS.NaturalProseDraftUnit, revision: str) -> NS.NaturalProseDraftUnit:
    """同一段草稿内容换一个修订（身份按内容重算，不走 `dataclasses.replace` 的伪造路径）。"""
    return _prose_draft(revision=revision, text=unit.text,
                        member_refs=unit.source_member_refs,
                        atom_ids=unit.atom_candidate_ids, index=unit.index)


def _revision_kwargs(manifest=MANIFEST) -> dict:
    return dict(task_id="t-1", section_id="company", company_id="c-1",
                report_as_of="2024-12-31", contract_version="cv-1",
                contract_fingerprint="cf-1", writer_policy_version="wp-1",
                prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
                manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint())


def _prose_layer_draft(*, prose_units, candidates, proposals, dispositions,
                       **over) -> NS.SectionDraft:
    base = dict(task_id="t-1", section_id="company", company_id="c-1",
                report_as_of="2024-12-31", contract_version="cv-1",
                contract_fingerprint="cf-1", producer_kind="topic_harness",
                writer_policy_version="wp-1", prompt_version="pack_section_writer_proposals_v1",
                model_policy="stub", authority_container_ids=("pack-1",),
                material_manifest=MANIFEST, material_dispositions=tuple(dispositions),
                claim_candidates=tuple(candidates), narrative_draft_units=(),
                proposed_support_refs=tuple(sorted(proposals,
                                                   key=lambda p: p.proposed_support_id)),
                unresolved_ids=(), unresolved_projections=(), coverage_summary={},
                conflict_projections=(), not_found_projections=(),
                dependency_fingerprint="dep-1", natural_prose_draft=tuple(prose_units))
    base.update(over)
    return NS.SectionDraft.create(**base)


def _used_disposition(proposal_ids) -> NS.WriterMaterialProcessingDisposition:
    return NS.WriterMaterialProcessingDisposition.create(
        manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint(),
        pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
        material_content_fingerprint=FP, processed=True, usage="used",
        support_usages=tuple(proposal_ids), reason_code=None, reason_proof=None,
        writer_policy_version="wp-1")


def _proposal(*, candidate_id: str, revision: str, fact_id: str,
              material_id: str = "m-1", container_id: str = "pack-1",
              locator_ref=MEMBER_LOCATOR, manifest=MANIFEST) -> NS.ProposedSupportRef:
    """一条路径 A 的 factual 支撑边。载体 + payload + locator 必须自闭合且与 manifest 成员逐字相同，
    否则材料层先于草稿层报「用途不得借别的材料凑」（本批实测）。

    `material_id` / `container_id` / `locator_ref`（`npr-1`）：归属核对要逐 occurrence 比这
    三样，因此它们必须是可参数化的——缺省值就是原本那一条边，旧用例一字未变。"""
    return NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id=candidate_id,
        draft_revision=revision, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), authority_kind="topic_pack",
        authority_container_id=container_id, source_identity="evidence:ev-1",
        provenance_identity="prov-1", support_role="primary", support_semantics="factual",
        authorization_path="path_a_prevalidated", content_fingerprint=FP,
        dependency_fingerprint="dep-1", fact_id=fact_id, material_id=material_id,
        payload_ref={"object_type": "research_material"}, locator_ref=locator_ref)


class _Cand:
    """只给映射闭合核对用的最小候选替身：`validate_natural_prose_mapping` 对候选只读
    `candidate_id` 这一个字段，不读文本、不做任何语义判断。"""

    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id


def main() -> dict:  # noqa: C901 - 逐节顺序即判据顺序，拆散会看不出覆盖面
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

    def note(msg: str) -> None:
        details.append(msg)

    def expect_raises(msg: str, fn, needle: str = "",
                      exc: type[BaseException] = NS.NarrativeSchemaError) -> None:
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                nonlocal failed, passed
                failed += 1
                details.append(f"FAIL {msg}（异常文本未含 {needle!r}：{str(e)[:160]}）")
            else:
                passed += 1
            return
        except Exception as e:  # noqa: BLE001 - 异常类型不对也是失败，必须逐条报出来
            failed += 1
            details.append(f"FAIL {msg}（抛了 {type(e).__name__}：{str(e)[:160]}）")
            return
        failed += 1
        details.append(f"FAIL {msg}（没有抛错）")

    # ======================================================= §1 版本与封闭补语表
    # `narr-7` 让草稿层进 wire；`narr-8` 又把草稿的出处从**一条**材料轴拆成两条互斥轴
    # （`source_member_refs` / `source_fact_refs`，`pprov-1`）。形状变了 ⇒ marker 前进一版
    # （形状可变而 marker 不变就是让 reader 去猜）。
    check(NS.NARRATIVE_SCHEMA_VERSION == "narr-8",
          f"草稿出处两条互斥轴必须进 wire（narr-8），实为 {NS.NARRATIVE_SCHEMA_VERSION!r}")
    check(NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS
          == ("narr-3", "narr-4", "narr-5", "narr-6", "narr-7"),
          "五个历史版本各留一个只读 legacy 读回入口"
          f"（实为 {NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS!r}）")
    # M930-3 定点业务闭环批 §二 2 再推一次：第 12 条（当前式措辞）的**判据实现一字未改**，
    # 但它所读的封闭标记集新增普遍化组与否定式普遍化组 ⇒ 判定集变了（`A此外公司一直<B>` 在
    # `nrules-15` 下通过、`nrules-16` 下被拒），故规则集与门各前进到 `nrules-16` / `ng-15`。
    # schema 版本**不动**：本批没有新增/删除字段，句子类别取值集也没变。
    # M930-3 主营业务质量返修批 §四再推一次：主体名抽取的**判据实现**改了
    # （`_entity_name_run` 剥前缀改为不动点；`_ENTITY_TEMPORAL_RUNS` 不再把「整串即时间状语」
    # 当字号），判定集随之**收窄**——`截至报告期末公司` 在 `ng-15` 下被判未授权主体名、
    # `ng-16` 下不再产出该 token。同批再补一格：`报告期末本公司` 的「本公司」被后缀切开，
    # `nrules-17` 下仍产出伪 token `末本公司`，`nrules-18` 才修干净。门与规则集因此前进到
    # `ng-17` / `nrules-18`。schema 版本**不动**：本批没有新增/删除字段，句子类别取值集也没变。
    # M930-3 `ndc-2` 批：实现仍一字未改，动的是支撑边的**材料判别集**（`mbind-1` 把材料候选按
    # 事实的已核验输入材料收窄）⇒ 只门前进到 `ng-18`（规则集继续停在 `nrules-18`）。
    check(NS.NARRATIVE_GATE_VERSION == "ng-18",
          f"判定集变了必须换门版本（ng-18），"
          f"实为 {NS.NARRATIVE_GATE_VERSION!r}")
    check(NS.NARRATIVE_RULES_VERSION == "nrules-18",
          f"判定集变了规则版本必须前进（nrules-18），实为 {NS.NARRATIVE_RULES_VERSION!r}")
    check(NS.SENTENCE_KINDS == ("factual", "transition", "composed", "natural"),
          f"句类封闭四元（含新增 natural），实为 {NS.SENTENCE_KINDS!r}")
    check(NS.BINDING_SUBJECT_KINDS == ("claim_candidate", "narrative_draft_unit"),
          "草稿层不得引入第三种 binding subject"
          f"（实为 {NS.BINDING_SUBJECT_KINDS!r}）")
    check(NS.FINAL_SENTENCE_FIDELITY_VERSION == "natfid-1",
          f"保真判据版本号，实为 {NS.FINAL_SENTENCE_FIDELITY_VERSION!r}")
    check(NS.SENTENCE_FIDELITY_DEFECTS == ("unauthorized_surface", "dropped_assertion_surface",
                                           "added_scope_or_strength", "stray_citation"),
          f"保真缺陷码必须是这四个 typed 码，实为 {NS.SENTENCE_FIDELITY_DEFECTS!r}")

    CAUSAL = NS.CAUSAL_ASSERTION_MARKERS
    SCOPE = NS.SCOPE_QUALIFIER_MARKERS
    STRENGTH = NS.CONCLUSION_STRENGTH_MARKERS
    for label, table in (("CAUSAL_ASSERTION_MARKERS", CAUSAL),
                         ("SCOPE_QUALIFIER_MARKERS", SCOPE),
                         ("CONCLUSION_STRENGTH_MARKERS", STRENGTH)):
        check(bool(table) and len(set(table)) == len(table)
              and all(isinstance(m, str) and m for m in table),
              f"{label} 必须是非空、无重复的字符串表（实为 {table!r}）")
    check(NS.FIDELITY_SINGLE_CHAR_MARKERS == ("故", "均", "各", "仅"),
          f"单字阈值表，实为 {NS.FIDELITY_SINGLE_CHAR_MARKERS!r}")
    single_chars = {m for m in CAUSAL + SCOPE + STRENGTH if len(m) < 2}
    check(single_chars == set(NS.FIDELITY_SINGLE_CHAR_MARKERS),
          "单字成员必须**恰好**是声明的那四个：阈值表是封闭的，不得就地放行某个单字"
          f"（三表里的单字成员实测 {sorted(single_chars)}）")

    ALREADY_HIGH_RISK = ("因此", "因而", "导致", "从而", "说明", "表明", "反映")
    ONLY_IN_COMPLEMENT = ("故", "于是", "使得", "由于", "因为", "可见", "意味着", "证明",
                          "印证", "体现")
    check(all(m in NS.HIGH_RISK_SURFACE_MARKERS for m in ALREADY_HIGH_RISK),
          "这七个因果词本来就在高风险表面表里（判据复用，不得重写一份）")
    check(all(m in CAUSAL for m in ALREADY_HIGH_RISK + ONLY_IN_COMPLEMENT),
          f"补语表必须覆盖**全部**因果连接词（实为 {CAUSAL!r}）")
    check(all(m not in NS.HIGH_RISK_SURFACE_MARKERS for m in ONLY_IN_COMPLEMENT),
          "这十个因果词不在高风险表面表里——补语表**新增**的正是这一批，"
          "而不是「把风险词删掉、换个地方再写一遍」")
    check(not (set(CAUSAL) & {"综上", "故此", "由此可见", "可以看出", "这意味着",
                              "主要是因为", "究其原因"}),
          "补语表只补 `HIGH_RISK_SURFACE_MARKERS` **没有**的那部分：已在表内的词不得再抄一遍"
          "（两份表各自封闭、互不复制）")

    # ======================================================= §2 断言关键表面的内容
    check(NS.assertion_critical_surfaces("") == (), "空文本没有任何断言关键表面")
    T = "公司2024年营业收入为1,234.56亿元，同比未发生重大变化。"
    acf = NS.assertion_critical_surfaces(T)
    check(acf == ("2024年", "1,234.56亿元", "未发生", "同比"),
          f"断言关键表面必须逐字是这四项（数字/期间/否定/表格关系），实测 {acf!r}")
    for label, source in (("数字", NS.scan_numeric_tokens(T)),
                          ("否定与状态标记", NS.marker_hits(T)),
                          ("含糊期间", NS.vague_period_hits(T)),
                          ("实体头部名词", NS.entity_head_nouns(T)),
                          ("范围补语", tuple(m for m in SCOPE if m in T)),
                          ("结论强度补语", tuple(m for m in STRENGTH if m in T))):
        missing = [x for x in source if x not in acf]
        check(not missing, f"{label} 必须全部进断言关键表面，缺 {missing}")
    scope_text = "公司全部产品用于储能领域，核心竞争力显著提升。"
    check("全部" in NS.assertion_critical_surfaces(scope_text)
          and "显著" in NS.assertion_critical_surfaces(scope_text),
          "范围语与结论强度语必须进断言关键表面（否则「不得丢」方向漏掉范围与强度轴）")
    repeated = "公司2024年营业收入为100亿元，2024年毛利率上升。"
    check(NS.assertion_critical_surfaces(repeated).count("2024年") == 1,
          "重复出现的同一表面只登记一次（按首次出现序去重）")

    # ======================================================= §3 七轴正例与反例
    ok_identical = _fidelity(CA, [C1])
    check(ok_identical["ok"] and ok_identical["defects"] == [],
          f"正例：逐字同文的句子必须通过（实测 {ok_identical}）")
    ok_two = _fidelity("公司2024年营业收入为1,234.56亿元，2025年营业收入同比增长。", [C1, C2])
    check(ok_two["ok"],
          f"正例：一条自然句写两条已审原子（换语序、省主语）必须通过（实测 {ok_two}）")
    ok_ws = _fidelity("公司 2024年营业收入为1,234.56亿元。", [C1])
    check(ok_ws["ok"], f"正例：空白差异不得被判成丢了表面（实测 {ok_ws}）")

    n_number = _fidelity("公司2024年营业收入约为12.35亿元。", [C_NUMBER])
    check(n_number["defects"] == ["unauthorized_surface", "dropped_assertion_surface"]
          and n_number["added"] == ["12.35亿元"]
          and ["1,235,000,000元"] in [x[1:] for x in n_number["dropped"]],
          f"轴 1 数量：把 `1,235,000,000元` 改写成 `12.35亿元` 必须两方向同时被判"
          f"（新增 + 丢弃），实测 {n_number}")

    n_period = _fidelity("公司营业收入为100亿元。", [C_PERIOD])
    check(n_period["defects"] == ["dropped_assertion_surface"]
          and ["2024年"] in [x[1:] for x in n_period["dropped"]] and n_period["added"] == [],
          f"轴 2 期间：丢掉 `2024年` 必须只判「丢」（不得混进「添」），实测 {n_period}")

    n_negation = _fidelity("公司2024年经营环境发生了重大变化。", [C_NEGATION])
    check(n_negation["defects"] == ["dropped_assertion_surface"]
          and ["未发生"] in [x[1:] for x in n_negation["dropped"]],
          f"轴 3 否定：把 `未发生` 写成 `发生` 必须判「丢了否定」，实测 {n_negation}")

    n_scope = _fidelity("公司全部产品用于储能领域。", [C_SCOPE])
    check(n_scope["defects"] == ["dropped_assertion_surface", "added_scope_or_strength"]
          and ["部分"] in [x[1:] for x in n_scope["dropped"]]
          and [x[0] for x in n_scope["extra"]] == ["全部"],
          f"轴 4 范围：`部分` → `全部` 必须两方向同时被判，实测 {n_scope}")

    n_strength = _fidelity("公司2024年营业收入显著下降。", [C_TREND])
    check(n_strength["defects"] == ["added_scope_or_strength"]
          and [x[0] for x in n_strength["extra"]] == ["显著"],
          f"轴 5 结论强度：自行加 `显著` 必须被判（`显著` 不在高风险表面表里，"
          f"所以只有这一支拦得住它），实测 {n_strength}")

    n_causal = _fidelity("由于营业成本下降，公司2024年营业收入下降。", [C_COST])
    check(n_causal["defects"] == ["added_scope_or_strength"]
          and [x[0] for x in n_causal["extra"]] == ["由于"],
          f"轴 6 因果：把并列升级成因果（加 `由于`）必须被判（`由于` 不在高风险表面表里），"
          f"实测 {n_causal}")

    real = _citations([C1])[0]
    n_stray = _fidelity(CA, [C1], citation_ids=("cite-ghost",),
                        claim_citations={C1.claim_id: (real,)})
    check(n_stray["defects"] == ["stray_citation"] and n_stray["stray"] == ["cite-ghost"],
          f"轴 7 引用：声明一条本句 Claim 派生不出的引用必须被判，实测 {n_stray}")
    ok_stray = _fidelity(CA, [C1], citation_ids=(real,), claim_citations={C1.claim_id: (real,)})
    check(ok_stray["ok"], f"轴 7 正向对照：由本句 Claim 派生的引用必须通过，实测 {ok_stray}")
    no_axis = _fidelity(CA, [C1], citation_ids=("cite-ghost",))
    check(no_axis["ok"] and no_axis["stray"] == [],
          "不传 `claim_citations` 时这一轴**不核**（stray 恒空）：按 stray 为空断言引用合法"
          "会读错这份报告——「没做」不是「通过了」")

    # ======================================================= §4 单字阈值（唯一放宽处）
    ok_single = _fidelity("公司2024年营业收入下降，各产品线均如此。", [C_TREND])
    check(ok_single["ok"],
          f"阈值正向：`各`/`均` 作为子串（`各产品线`/`均如此`）不得被判成新增范围语，"
          f"实测 {ok_single}")
    n_single = _fidelity("公司2024年营业收入下降，仅部分产品线如此。", [C_TREND])
    check(n_single["defects"] == ["added_scope_or_strength"]
          and [x[0] for x in n_single["extra"]] == ["部分"],
          f"阈值不得修过头：长度 ≥ 2 的范围语（`部分`）照旧命中，实测 {n_single}")

    # ======================================================= §5 结论指纹与只读性
    check(NS.sentence_fidelity_digest(CA, [C1.claim_id], [CA], ["asb-1"])
          == NS.sentence_fidelity_digest(CA, [C1.claim_id], [CA], ["asb-1"]),
          "同一输入必须得到同一指纹（可复算）")
    check(NS.sentence_fidelity_digest("A B", ["c"], ["A B"], [])
          == NS.sentence_fidelity_digest("AB", ["c"], ["AB"], []),
          "空白归一：只有空白差异不影响指纹")
    check(NS.sentence_fidelity_digest("A", ["c"], ["A"], [])
          != NS.sentence_fidelity_digest("A。", ["c"], ["A。"], []),
          "改了句文本必须换指纹")
    check(NS.sentence_fidelity_digest("A", ["c"], ["A"], ["b1"])
          != NS.sentence_fidelity_digest("A", ["c"], ["A"], ["b2"]),
          "换了绑定集合必须换指纹")
    check(NS.sentence_fidelity_digest("A", ["c"], ["A"], [])
          != NS.sentence_fidelity_digest("A", ["c"], ["A。"], []),
          "换了某条 Claim 的文本必须换指纹（「结论过期」不得伪装成「仍然有效」）")

    report = NS.verify_sentence_fidelity(text=CA, claims=[C1])
    check(report.fidelity_digest == NS.sentence_fidelity_digest(
        CA, report.claim_ids, [C1.text], report.accepted_binding_ids),
        "报告里的指纹必须与独立复算一致")
    check(report.to_dict()["ok"] is True and report.to_dict()["defects"] == [],
          "报告的可序列化视图必须与属性一致")
    before = (C1.claim_id, C1.text, tuple(C1.citation_refs))
    first = NS.verify_sentence_fidelity(text="公司2024年营业收入为9,999.99亿元。", claims=[C1])
    second = NS.verify_sentence_fidelity(text="公司2024年营业收入为9,999.99亿元。", claims=[C1])
    check(first.to_dict() == second.to_dict(),
          "只读：同一输入两次调用必须得到逐字段相同的结果（不得累积状态）")
    check((C1.claim_id, C1.text, tuple(C1.citation_refs)) == before,
          "只读：核验不得改写传入的 Claim 对象")
    expect_raises("报告是冻结对象：不得就地改字段",
                  lambda: setattr(report, "added_surfaces", ("x",)),
                  exc=dataclasses.FrozenInstanceError)

    # ======================================================= §6 草稿单元与映射闭合
    unit_a = _prose_draft(revision="rev-x", text="公司2024年营业收入为100亿元。",
                          member_refs=(MEMBER_REF,), atom_ids=("ccand-a",))
    check(unit_a.prose_unit_id.startswith("npdu_") and unit_a.schema_version == "narr-8",
          f"草稿单元身份前缀与版本，实测 {unit_a.prose_unit_id[:5]}/{unit_a.schema_version!r}")
    check({unit_a.prose_unit_id.split("_")[0], C1.claim_id.split("_")[0],
           _prose_probe_candidate().candidate_id.split("_")[0],
           NS.NarrativeDraftUnit.create(draft_revision="r", section_id="s", index=0,
                                        unit_kind="paragraph",
                                        text="x").draft_unit_id.split("_")[0]}
          == {"npdu", "claim", "ccand", "ndu"},
          "四种身份（正文 Claim / 事实候选 / 门前叙述单元 / 自然草稿单元）必须分属四个命名空间，"
          "不得有任意两个共用前缀——共用前缀是可读性出事时最难发现的一类混淆")
    forbidden = ("binding_decision_id", "entailment_decision_id", "accepted_support_binding_id",
                 "section_claim_id", "section_result_id", "sentence_id",
                 "binding_subject_kind")
    field_names = {f.name for f in dataclasses.fields(NS.NaturalProseDraftUnit)}
    check(not (field_names & set(forbidden)),
          "草稿单元在类型层不得表达未来的决定/产物字段，实测多出 "
          f"{sorted(field_names & set(forbidden))}")
    expect_raises("草稿单元文本不得为空",
                  lambda: _prose_draft(revision="r", text="   ", member_refs=(MEMBER_REF,),
                                       atom_ids=("c",)),
                  "text 不得为空")
    expect_raises("两条出处轴都为空（无出处的散句）必须拒",
                  lambda: _prose_draft(revision="r", text="x", member_refs=(), atom_ids=("c",)),
                  "恰有一条出处轴非空")
    expect_raises("两条出处轴同时非空必须拒（同一格不得表达两种身份）",
                  lambda: _prose_draft(revision="r", text="x", member_refs=(MEMBER_REF,),
                                       atom_ids=("c",), fact_refs=(FACT_REF,)),
                  "不得同时非空")
    check(_prose_draft(revision="r", text="x", member_refs=(MEMBER_REF,), atom_ids=("c",),
                       fact_refs=()).source_fact_refs == (),
          "材料轴单元的事实侧取值照旧为空（正例：反例不是恒失败）")
    check(_prose_draft(revision="r", text="x", member_refs=(), atom_ids=("c",),
                       fact_refs=(FACT_REF,)).source_fact_refs == (FACT_REF,),
          "事实轴单元可构造（纯 FinancialFactPack 的草稿出处只能在事实轴上，材料清单合法为空）")
    expect_raises("事实键写进材料轴必须拒（两轴命名空间不相交）",
                  lambda: _prose_draft(revision="r", text="x", member_refs=(FACT_REF,),
                                       atom_ids=("c",)),
                  "不属于 'material' 轴")
    expect_raises("材料键写进事实轴必须拒（同上，反向）",
                  lambda: _prose_draft(revision="r", text="x", member_refs=(), atom_ids=("c",),
                                       fact_refs=(MEMBER_REF,)),
                  "不属于 'fact' 轴")
    expect_raises("不含任何已审原子的草稿必须拒",
                  lambda: _prose_draft(revision="r", text="x", member_refs=(MEMBER_REF,),
                                       atom_ids=()),
                  "atom_candidate_ids 不得为空")
    expect_raises("草稿单元身份与内容必须同源",
                  lambda: dataclasses.replace(unit_a, prose_unit_id="npdu_forged"),
                  "prose_unit_id 与内容不符")
    expect_raises("草稿单元的版本必须是当前版本",
                  lambda: dataclasses.replace(unit_a, schema_version="narr-6"),
                  "schema_version 必须为")
    expect_raises("草稿层不是第三种 binding subject：proposal 不得指向它",
                  lambda: NS.ProposedSupportRef.create(
                      binding_subject_kind="natural_prose_draft_unit",
                      binding_subject_id=unit_a.prose_unit_id, draft_revision="rev-x",
                      manifest_id=MANIFEST.manifest_id,
                      manifest_fingerprint=MANIFEST.fingerprint(),
                      authority_kind="topic_pack", authority_container_id="pack-1",
                      source_identity="evidence:ev-1", provenance_identity="prov-1",
                      support_role="primary", support_semantics="factual",
                      authorization_path="path_a_prevalidated", content_fingerprint=FP,
                      dependency_fingerprint="dep-1", fact_id="f-1"),
                  "binding_subject_kind")

    def _problems(units, candidates, member_refs=(MEMBER_REF,), section_id="company",
                  revision="rev-x", proposals=None, manifest=None) -> tuple[str, ...]:
        return NS.validate_natural_prose_mapping(
            natural_prose_draft=units, claim_candidates=candidates, member_refs=member_refs,
            section_id=section_id, draft_revision=revision,
            proposed_support_refs=proposals, material_manifest=manifest)

    check(NS.validate_natural_prose_mapping(
        natural_prose_draft=(), claim_candidates=(), member_refs=(),
        section_id="company", draft_revision="rev-x") == (),
        "空草稿返回空列表（narr-6 及更早的行为一字未变）")
    check(_problems((unit_a,), [_Cand("ccand-a")]) == (),
          "闭合的草稿/候选/成员三元组必须无问题")
    second_unit = _prose_draft(revision="rev-x", text="另一句。", member_refs=(MEMBER_REF,),
                               atom_ids=("ccand-a",), index=1)
    for label, needle, problems in (
            ("归属不符", "draft_revision/section_id 与本节不符",
             _problems((unit_a,), [_Cand("ccand-a")], revision="rev-y")),
            ("顺序不符", "index 与顺序不符",
             _problems((_prose_draft(revision="rev-x", text=unit_a.text,
                                     member_refs=(MEMBER_REF,), atom_ids=("ccand-a",),
                                     index=3),), [_Cand("ccand-a")])),
            ("借用清单外的成员", "manifest 之外的成员",
             _problems((unit_a,), [_Cand("ccand-a")], member_refs=(OTHER_MEMBER_REF,))),
            # 「同一候选被两个单元声明」在 `npr-0` 下是**唯一性**判据；`npr-1` 起它由逐
            # occurrence 的支撑核对承担，见本函数下方 §6b。这里保留的是**核对面缺席**那一格：
            # 没收到支撑提案面与 manifest 时，归属核对**不能**退成「核不了就算过」。
            ("缺核对面时同一候选被两个单元声明", "缺核对面时原子归属仍必须唯一",
             _problems((unit_a, second_unit), [_Cand("ccand-a")])),
            # 这一格候选集**为空**：否则「草稿声明了不存在的候选」会与「某候选没被声明」
            # 同时命中，两句话说的是两回事，混在一起就定位不到真正的那个问题。
            ("声明了不存在的候选", "自然草稿声明了本节不存在的候选",
             _problems((unit_a,), [])),
            ("候选没被任何单元声明", "候选未被任何自然草稿单元声明",
             _problems((unit_a,), [_Cand("ccand-a"), _Cand("ccand-b")])),
    ):
        check(len(problems) == 1 and needle in problems[0],
              f"映射问题「{label}」必须恰好报一条可定位的问题，实测 {problems!r}")
    note("NOTE 映射闭合核对共六类问题，逐类可定位（见上）；草稿为空时全部不生效"
         "（narr-6 及更早没有任何这一层）。")

    # ---- §6b `npr-1`：逐 occurrence 的支撑核对（容器 + 材料 + 文档版本，或权威事实行） ------
    # 这一组钉住**版本化的多 occurrence 政策**：一条候选可以在多段草稿里各出现一次，但每一次
    # 都要在**这条候选自己**的 factual 提案里找到落在**同一行**上的边。这里逐格给正反例：
    # 正例（两次出现各有一条自己的边）/ 无匹配支撑 / 错文档版本 / 错容器 / 同一段内重复声明 /
    # 事实轴正例与事实轴错行。判据版本与「门后由组织器在合格表达里选」（不把两年相似表述推成
    # 「始终如此」）见 :data:`PROSE_OCCURRENCE_POLICY_VERSION`。
    check(NS.PROSE_OCCURRENCE_POLICY_VERSION == "npr-1",
          f"多 occurrence 政策必须有自己的版本号（实为 "
          f"{NS.PROSE_OCCURRENCE_POLICY_VERSION!r}）：它是归属那一格的判据，"
          "与 `nrules-16`/`ng-15`（文本判据集）不是同一条轴")
    check("PROSE_OCCURRENCE_POLICY_VERSION" in NS.__all__,
          f"政策版本必须从模块导出（不进 `__all__` 的版本号等于写在注释里；`__all__` 装的是"
          f"**名字**，不是取值）。实测 `__all__` 共 {len(NS.__all__)} 项")

    occ_ref_1 = _occ_member("m-1", "doc-1", "dv-1")
    occ_ref_2 = _occ_member("m-2", "doc-2", "dv-2")
    occ_ref_other_container = _occ_member("m-1", "doc-1", "dv-1", pack_id="pack-2")
    loc_1 = NS.char_range_locator("evidence_document:doc-1@dv-1#block_span", 3, 40)
    loc_2 = NS.char_range_locator("evidence_document:doc-2@dv-2#block_span", 3, 40)
    #: 同一个 `(容器, 材料)` 的**另一个文档版本**（边侧声明，单元侧来自清单成员）。
    loc_other_version = NS.char_range_locator("evidence_document:doc-1@dv-2#block_span", 3, 40)
    occ_cand = _Cand("ccand-occ")
    occ_unit_1 = _prose_draft(revision="rev-x", text="第一处表达。", member_refs=(occ_ref_1,),
                              atom_ids=("ccand-occ",), index=0)
    occ_unit_2 = _prose_draft(revision="rev-x", text="第二处表达。", member_refs=(occ_ref_2,),
                              atom_ids=("ccand-occ",), index=1)
    occ_edge_1 = _proposal(candidate_id="ccand-occ", revision="rev-x", fact_id="f-1",
                           material_id="m-1", container_id="pack-1", locator_ref=loc_1,
                           manifest=OCC_MANIFEST)
    occ_edge_2 = _proposal(candidate_id="ccand-occ", revision="rev-x", fact_id="f-2",
                           material_id="m-2", container_id="pack-1", locator_ref=loc_2,
                           manifest=OCC_MANIFEST)

    def _occ(units, candidates, proposals, member_refs=OCC_MANIFEST.member_refs()) -> tuple:
        return _problems(units, candidates, member_refs=member_refs,
                         proposals=tuple(proposals), manifest=OCC_MANIFEST)

    check(_occ((occ_unit_1, occ_unit_2), [occ_cand], [occ_edge_1, occ_edge_2]) == (),
          "**多 occurrence 正例**：一条候选出现在两段草稿里，两段各自有一条落在**它自己那份"
          "材料**上的 factual 支撑边时，映射闭合必须通过（这正是 r9 保存字节里那段"
          "「两年各写了一遍」的合法形态）")
    check(_occ((occ_unit_1,), [occ_cand], [occ_edge_1]) == (),
          "对照：只出现一次的同一形态照旧通过（正例不是恒过）")
    one_edge = _occ((occ_unit_1, occ_unit_2), [occ_cand], [occ_edge_1])
    check(len(one_edge) == 1 and "没有一条落在本段的来源上" in one_edge[0],
          f"**无匹配支撑**：第二段的出处（doc-2 的材料）在这条候选**自己**的提案里没有落点，"
          f"必须逐 occurrence 被拒（实测 {one_edge!r}）")
    check("容器 pack-1 的材料 m-2" in one_edge[0] and "容器 pack-1 的材料 m-1" in one_edge[0],
          "拒绝原因必须逐条列出**这一次出现落在哪一行**与这条候选自己的边落在哪些行上"
          f"——读的人要能自己看出「有没有一条本该在此」（实测 {one_edge[0]!r}）")
    wrong_version = _occ((occ_unit_1,), [occ_cand],
                         [_proposal(candidate_id="ccand-occ", revision="rev-x", fact_id="f-1",
                                    material_id="m-1", container_id="pack-1",
                                    locator_ref=loc_other_version, manifest=OCC_MANIFEST)])
    check(len(wrong_version) == 1 and "没有一条落在本段的来源上" in wrong_version[0],
          f"**错文档版本**：容器与 material_id 都相同、只有边声明的文档版本不同时必须拒"
          f"（材料 ID 相等**不等于**同一版正文）（实测 {wrong_version!r}）")
    occ_unit_other_container = _prose_draft(
        revision="rev-x", text="同一个 material_id 的另一份载体。",
        member_refs=(occ_ref_other_container,), atom_ids=("ccand-occ",), index=0)
    wrong_container = _occ((occ_unit_other_container,), [occ_cand], [occ_edge_1])
    check(len(wrong_container) == 1 and "没有一条落在本段的来源上" in wrong_container[0],
          f"**错容器**：同一个 `material_id` 出现在另一个 Pack 里时，成员键（`(pack_id, "
          f"material_id)`）不同即不是同一行——裸 material_id 比对会把「同 ID 错 Evidence "
          f"Set」折叠成一条（实测 {wrong_container!r}）")
    def _duplicate_inside_unit() -> None:
        _prose_draft(revision="rev-x", text="同一段说了两遍。", member_refs=(occ_ref_1,),
                     atom_ids=("ccand-occ", "ccand-occ"), index=0)

    expect_raises("**同一单元内**重复声明同一条候选仍拒（那不是「两个版本各写了一遍」，"
                  "是一格把一件事说了两遍）——这一条在**类型层**判，`NaturalProseDraftUnit` "
                  "是 frozen 的，构造出来的东西没有任何路径能再长出重复的原子集",
                  _duplicate_inside_unit, needle="含重复元素")
    # 事实轴：键本身就是坐标（`fprov_*`）。`authority_kind` 的真实字面量是 `financial_pack`
    # （`AUTHORITY_KINDS`）——§6 上面那条 `FACT_REF` 用的是另一个串，它只用来钉命名空间互斥，
    # 不参与本节的归属核对。
    occ_fact_ref = NS.fact_provenance_ref("financial_pack", "ffp-1", "fact-1")

    def _fact_edge(fact_id: str) -> NS.ProposedSupportRef:
        return NS.ProposedSupportRef.create(
            binding_subject_kind="claim_candidate", binding_subject_id="ccand-occ",
            draft_revision="rev-x", manifest_id=OCC_MANIFEST.manifest_id,
            manifest_fingerprint=OCC_MANIFEST.fingerprint(), authority_kind="financial_pack",
            authority_container_id="ffp-1", source_identity="financial_pack:ffp-1",
            provenance_identity="prov-fin", support_role="primary", support_semantics="factual",
            authorization_path="path_a_prevalidated", content_fingerprint=FP,
            dependency_fingerprint="dep-1", financial_fact_id=fact_id)

    fact_unit = _prose_draft(revision="rev-x", text="财务节的事实轴表达。",
                             member_refs=(), fact_refs=(occ_fact_ref,),
                             atom_ids=("ccand-occ",), index=0)
    check(_occ((fact_unit,), [occ_cand], [_fact_edge("fact-1")]) == (),
          "事实轴正例：单元声明的那一条权威事实行正是该候选自己绑定的那一行时必须通过")
    fact_mismatch = _occ((fact_unit,), [occ_cand], [_fact_edge("fact-2")])
    check(len(fact_mismatch) == 1 and "没有一条落在本段的来源上" in fact_mismatch[0],
          f"事实轴反例：绑定的是**另一条**权威事实行时必须拒（实测 {fact_mismatch!r}）")

    # 反查：一条候选的**全部**表达必须都能取到（只留「第一段」会让下游在不知道还有第二段
    # 的情况下拿第一段去核，失败时看起来像「材料没写过这句话」）。这里刻意走**真实构造路径**
    # （`SectionDraft.create` → `__post_init__` → 本层），因此它同时钉住「多 occurrence 的正式
    # 草稿确实构造得出来」——否则这条政策只是映射函数上的一个说法。
    # 本节修订由草稿集**推导**（`derive_draft_revision_with_natural_prose`），摘要只含顺序 /
    # text / 两条出处轴（不含候选 id，见 `natural_prose_draft_digest`），因此可以先按占位原子
    # 推出修订，再用它建候选与单元——两遍收敛（§7 已单独钉过）。
    occ_temp_1 = _prose_draft(revision="rev-x", text="第一处表达。", member_refs=(occ_ref_1,),
                              atom_ids=("ccand-temp",), index=0)
    occ_temp_2 = _prose_draft(revision="rev-x", text="第二处表达。", member_refs=(occ_ref_2,),
                              atom_ids=("ccand-temp",), index=1)
    occ_real_revision = NS.derive_draft_revision_with_natural_prose(
        natural_prose_draft=(occ_temp_1, occ_temp_2),
        **_revision_kwargs(manifest=OCC_MANIFEST))
    occ_real_cand = NS.ClaimCandidate.create(
        draft_revision=occ_real_revision, task_id="t-1", section_id="company",
        company_id="c-1", report_as_of="2024-12-31", contract_version="cv-1",
        contract_fingerprint="cf-1", claim_text="公司在两个年度分别披露了同一项经营安排。",
        fact_type="metric")
    occ_real_unit_1 = _prose_draft(revision=occ_real_revision, text=occ_temp_1.text,
                                   member_refs=(occ_ref_1,),
                                   atom_ids=(occ_real_cand.candidate_id,), index=0)
    occ_real_unit_2 = _prose_draft(revision=occ_real_revision, text=occ_temp_2.text,
                                   member_refs=(occ_ref_2,),
                                   atom_ids=(occ_real_cand.candidate_id,), index=1)
    check(NS.natural_prose_draft_digest((occ_temp_1, occ_temp_2))
          == NS.natural_prose_draft_digest((occ_real_unit_1, occ_real_unit_2)),
          "换真修订后草稿集摘要不变（否则本节修订与单元对不上，草稿根本构造不出来）")
    occ_real_edge_1 = _proposal(candidate_id=occ_real_cand.candidate_id,
                                revision=occ_real_revision, fact_id="f-1", material_id="m-1",
                                container_id="pack-1", locator_ref=loc_1,
                                manifest=OCC_MANIFEST)
    occ_real_edge_2 = _proposal(candidate_id=occ_real_cand.candidate_id,
                                revision=occ_real_revision, fact_id="f-2", material_id="m-2",
                                container_id="pack-1", locator_ref=loc_2,
                                manifest=OCC_MANIFEST)

    def _occ_disposition(material_id: str, proposal_ids=(), *, pack_id: str = "pack-1",
                         disposition_id: str = "rmd-occ",
                         ) -> NS.WriterMaterialProcessingDisposition:
        """与 `_occ_entry` 的 `research_material_disposition_id` 逐字一致——「member_ref 相同
        不等于内容相同」，P6 的三层等式要的是内容同一，不是键同一。"""
        if proposal_ids:
            return NS.WriterMaterialProcessingDisposition.create(
                manifest_id=OCC_MANIFEST.manifest_id,
                manifest_fingerprint=OCC_MANIFEST.fingerprint(), pack_id=pack_id,
                material_id=material_id, research_material_disposition_id=disposition_id,
                material_content_fingerprint=FP, processed=True, usage="used",
                support_usages=tuple(proposal_ids), reason_code=None, reason_proof=None,
                writer_policy_version="wp-1")
        return NS.WriterMaterialProcessingDisposition.create(
            manifest_id=OCC_MANIFEST.manifest_id,
            manifest_fingerprint=OCC_MANIFEST.fingerprint(), pack_id=pack_id,
            material_id=material_id, research_material_disposition_id=disposition_id,
            material_content_fingerprint=FP, processed=True, usage="not_used",
            support_usages=(), reason_code="irrelevant_to_section_goal",
            reason_proof={"policy_version": "mnp-1", "policy_fingerprint": "c" * 64,
                          "relevance_decision_ref": "rd-occ"},
            writer_policy_version="wp-1")

    occ_draft = _prose_layer_draft(
        prose_units=(occ_real_unit_1, occ_real_unit_2), candidates=(occ_real_cand,),
        proposals=(occ_real_edge_1, occ_real_edge_2),
        dispositions=(_occ_disposition("m-1", (occ_real_edge_1.proposed_support_id,)),
                      _occ_disposition("m-2", (occ_real_edge_2.proposed_support_id,)),
                      _occ_disposition("m-1", pack_id="pack-2")),
        material_manifest=OCC_MANIFEST)
    check(occ_draft.draft_revision == occ_real_revision
          and len(occ_draft.natural_prose_draft) == 2,
          "正式路径：一条候选两处表达、每处各有一条自己那份材料上的边的草稿必须真的构造得出来"
          "（这是 `npr-1` 存在的全部理由；构造不出来就等于政策没落地）")
    occurrences = occ_draft.natural_prose_occurrences_for_candidate(occ_real_cand.candidate_id)
    check([u.prose_unit_id for u in occurrences]
          == [u.prose_unit_id for u in (occ_real_unit_1, occ_real_unit_2)],
          f"多出现反查必须返回**全部**表达（按草稿顺序），实测 "
          f"{[u.text for u in occurrences]!r}")
    check(occ_draft.natural_prose_for_candidate(occ_real_cand.candidate_id) is occurrences[0],
          "单数反查是复数反查的一元特例（按草稿顺序取第一段）——两条口径不得各扫一遍")
    check(occ_draft.natural_prose_occurrences_for_candidate("ccand-none") == (),
          "没有草稿表达的候选返回空元组（不是 None：调用方对「一条都没有」也要能直接迭代）")

    # --- 摘要本身：空集、不动点、有序、逐内容
    check(NS.natural_prose_draft_digest(()) == "",
          "空草稿的摘要是空串：进身份体的口径与「没有草稿层」同形")
    check(NS.natural_prose_draft_digest((unit_a,)).startswith("npdd_"),
          f"摘要前缀，实测 {NS.natural_prose_draft_digest((unit_a,))[:5]}")
    check(NS.natural_prose_draft_digest((_revisioned(unit_a, "rev-z"),))
          == NS.natural_prose_draft_digest((unit_a,)),
          "摘要只取不依赖本修订的内容（prose 文本 + 出处 + 顺序）：否则 `draft_revision` 与"
          "摘要互指成环，草稿层非空的载荷根本无法构造")
    check(NS.natural_prose_draft_digest(
        (_prose_draft(revision="rev-x", text="换了一句话。", member_refs=(MEMBER_REF,),
                      atom_ids=("ccand-a",)),)) != NS.natural_prose_draft_digest((unit_a,)),
        "prose 文本变了摘要必须变")
    check(NS.natural_prose_draft_digest(
        (_prose_draft(revision="rev-x", text=unit_a.text, member_refs=(OTHER_MEMBER_REF,),
                      atom_ids=("ccand-a",)),)) != NS.natural_prose_draft_digest((unit_a,)),
        "材料出处变了摘要必须变")
    two = (unit_a, second_unit)
    check(NS.natural_prose_draft_digest(two)
          != NS.natural_prose_draft_digest(tuple(reversed(two))),
          "顺序变了摘要必须变（有序摘要）")

    # ======================================================= §7 草稿层非空的 Draft 可构造
    empty_revision = NS.derive_draft_revision(**_revision_kwargs())
    check(NS.derive_draft_revision(natural_prose_digest="", **_revision_kwargs()) == empty_revision,
          "没有草稿层时修订**一字未变**（narr-6 及更早的全部载荷读回来还是同一个修订）")

    TEMP_REV = "sdrev_" + "0" * 24
    temp_unit = _prose_draft(revision=TEMP_REV, text=CA, member_refs=(MEMBER_REF,),
                             atom_ids=("cand-temp",))
    real_revision = NS.derive_draft_revision_with_natural_prose(
        natural_prose_draft=(temp_unit,), **_revision_kwargs())
    check(real_revision == NS.derive_draft_revision(
        natural_prose_digest=NS.natural_prose_draft_digest((temp_unit,)), **_revision_kwargs()),
        "草稿层在场时的唯一推导入口必须与手工两遍推导一致")
    check(NS.natural_prose_draft_digest((temp_unit,))
          == NS.natural_prose_draft_digest((_revisioned(temp_unit, real_revision),)),
          "两遍顺序必须收敛：用真修订重建单元后摘要不变（不动点已消失）")

    candidate = NS.ClaimCandidate.create(
        draft_revision=real_revision, task_id="t-1", section_id="company", company_id="c-1",
        report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
        claim_text=CA, fact_type="metric")
    prose_unit = _prose_draft(revision=real_revision, text=CA, member_refs=(MEMBER_REF,),
                              atom_ids=(candidate.candidate_id,))
    proposal = _proposal(candidate_id=candidate.candidate_id, revision=real_revision,
                         fact_id="f-1")
    draft = _prose_layer_draft(prose_units=(prose_unit,), candidates=(candidate,),
                               proposals=(proposal,),
                               dispositions=(_used_disposition(
                                   (proposal.proposed_support_id,)),))
    check(draft.draft_revision == real_revision and len(draft.natural_prose_draft) == 1,
          "回归：草稿层非空的 `SectionDraft` 必须真的构造得出来（本批实测到的阻断性缺陷："
          "摘要取 `identity_body()` 会让修订与摘要互指成环，载荷根本无法构造）")
    check(NS.SectionDraft.from_dict(draft.to_dict()).draft_id == draft.draft_id,
          "读回：草稿层必须逐字往返（旧工件读回兼容的前提）")
    check(NS.SectionDraft.from_dict(draft.to_dict()).natural_prose_draft
          == draft.natural_prose_draft,
          "读回：自然草稿单元必须逐字段还原")

    expect_raises("两遍顺序是必需的：单元带着临时修订必须 fail-closed",
                  lambda: _prose_layer_draft(
                      prose_units=(temp_unit,), candidates=(candidate,),
                      proposals=(proposal,),
                      dispositions=(_used_disposition((proposal.proposed_support_id,)),)),
                  "draft_revision/section_id 与本节不符")
    orphan = NS.ClaimCandidate.create(
        draft_revision=real_revision, task_id="t-1", section_id="company", company_id="c-1",
        report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
        claim_text=CB, fact_type="metric")
    orphan_proposal = _proposal(candidate_id=orphan.candidate_id, revision=real_revision,
                                fact_id="f-2")
    expect_raises("旁路塞进来的候选（不是从正文长出来的）必须拒",
                  lambda: _prose_layer_draft(
                      prose_units=(prose_unit,), candidates=(candidate, orphan),
                      proposals=(proposal, orphan_proposal),
                      dispositions=(_used_disposition((proposal.proposed_support_id,
                                                       orphan_proposal.proposed_support_id)),)),
                  "候选未被任何自然草稿单元声明")

    # ======================================================= §8 natural 在冻结门的分派
    NATURAL_TEXT = "公司2024年营业收入为1,234.56亿元，较上年同期保持增长。"
    check(_gate_error(NATURAL_TEXT, "natural", [C1, C_GROWTH]) == "",
          "分派正向：同一段文本写成 natural 必须放行（这正是本模式存在的理由）")
    composed_error = _gate_error(NATURAL_TEXT, "composed", [C1, C_GROWTH])
    check("没有按序出现在正文里" in composed_error,
          "反向对照：**同一段文本**写成 composed 照旧被拒——证明这是新增的一支，"
          f"不是把第 8 条删掉了（实测 {composed_error[:120]!r}）")
    dropped_error = _gate_error("公司营业收入较上年同期保持增长。", "natural", [C1, C_GROWTH])
    check("dropped_assertion_surface" in dropped_error and "改写越过了保真边界" in dropped_error,
          "门侧「不得丢」：丢了 `2024年`/`1,234.56亿元` 的 natural 句必须被拒且缺陷码成串"
          f"（实测 {dropped_error[:120]!r}）")
    added_error = _gate_error("公司2024年营业收入为9,999.99亿元。", "natural", [C1, C_GROWTH])
    check("没有的高风险表面" in added_error and "9,999.99亿元" in added_error,
          "门侧「不得新增」**不放宽**：natural 句自造一个数字必须被第 2/3 条拒"
          f"（实测 {added_error[:120]!r}）")
    ctx_error = _gate_error(NATURAL_TEXT, "natural", [C1, C_GROWTH],
                            section_context=("ctx-real",), sentence_context=("ctx-ghost",))
    check("本节未接受的绑定" in ctx_error and "ctx-ghost" in ctx_error,
          "门侧第 4 条**不放宽**：natural 句凭空引用一条本节没接受的 context 绑定必须被拒"
          f"（实测 {ctx_error[:120]!r}）")
    check(_gate_error(NATURAL_TEXT, "natural", [C1, C_GROWTH],
                      section_context=("ctx-real",)) == "",
          "门侧第 4 条正向：已接受的 context 绑定照旧放行（判据没变，只是句类多了一支）")
    known = {c.claim_id: c for c in (C1, C_GROWTH)}
    organized = NO._sentence_specs(
        {"sentences": [{"text": NATURAL_TEXT, "claim_ids": [C1.claim_id, C_GROWTH.claim_id],
                        "sentence_kind": "natural"}]}, known_claims=known)
    check([s["sentence_kind"] for s in organized] == ["composed"],
          "句类只能由系统标注：模型在计划里自称 `natural` 不算数，一律按 composed 组装"
          f"（否则自称句类就能躲开第 8 条），实测 {[s['sentence_kind'] for s in organized]}")

    # ======================================================= §9 诚实边界（逐条给证据）
    check(NS.entity_head_nouns("公司2024年度动力电池销量为100万辆") == (),
          "边界 1（已登记）：光杆「公司」开头且无主体后缀时，头部名词替身**没有读数**，"
          "主体轴在「丢」方向的这一格里不起作用（不是「通过了」，是「量不到」）")
    check(NS.entity_name_tokens("2024年度公司主营业务收入为100亿元。") == (),
          "边界 1 的成因已被 `nrules-17`／`scp-8` 修掉：`年度` 是**整串即时间状语**的字号，"
          "不再产出 `年度公司` 这条来源里根本不存在的实体（原先它会在授权轴上凭空吃一条"
          "`unsourced_subject_surface`）")
    check(NS.entity_head_nouns("2024年度公司主营业务收入为100亿元。") == ("公司",),
          "防修过头：头部名词替身**不**继承那个粘连（`年度公司` 若被当成丢了的主体，"
          "`公司2024年度…` 这种完全合规的自然改写就会被判越权）")
    # 边界 2（**本批登记，不改判据**）：`natural` 支只能整句扫描（改写句里没有可用来分段的
    # Claim 逐字文本），因此在子句接缝处会**合成**出原文里不存在的表面。两个接缝词成对登记，
    # 说明这不是「系词」单一成因：`是` 这类系词**不在** `_ENTITY_RUN_STOP` 里（同类的 `为` 在），
    # 接缝是系词时合成；接缝不是系词时（`构成了`）同样合成。
    # 修它要动主体名匹配集这条**判定集**——按 `nrules-10` 立下的规矩必须与三处版本前进
    # 一起落地并重核离线重放，不在本批「最小返修」的范围内，故只登记，不实施。
    copula = _fidelity("动力电池的研发、生产与销售是公司的主营业务。", [C_REORDER])
    check(not copula["ok"] and "销售是公司" in copula["added"],
          "边界 2（已登记，方向是 fail-closed）：接缝上是系词 `是` 时，整句扫描会合成出原文里"
          f"不存在的表面；实测 {copula}")
    check(["主要"] in [x[1:] for x in copula["dropped"]],
          "同一条实测里被丢的 `主要` 是**真实**丢字（范围语）——`added` 与 `dropped` 是两个"
          "独立读数，不得混成一个")
    reordered = _fidelity("动力电池的研发、生产与销售构成了公司的主营业务。", [C_REORDER])
    check(not reordered["ok"] and "销售构成了公司" in reordered["added"],
          "边界 2（非系词接缝）：子句边界同样合成，因此成因不止「系词没进停止集」这一条；"
          "它只会错拒合法改写，不会放过越权。"
          f"实测 {reordered}")
    general = _fidelity("该公司2024年营业收入为100亿元。", [C_FULLNAME])
    check(general["ok"] and general["defects"] == [],
          "边界 3（已登记）：「具体主体 → 泛指」在本核验下**两方向都拦不住**"
          "（指代式主体字号不足 2 个汉字，回扫落空，因此它不进高风险表面集）。"
          f"实测 {general}")
    note("NOTE 边界 3：`ok` / `defects()` / 四组明细全空**不等于**「主体没有被改写」，"
         "只说明本核验的三样读数在这一格上没有读数。这一格由 Claim 级蕴含门与人工逐句读回"
         "承担；把「核验通过」读成「断言忠实」超出了 `natfid-1` 声称的范围。")

    # ======================================================= §10 唯一实现与机械纪律
    implementers = sorted(p.name for p in (REPO / "sections").glob("*.py")
                          if "def verify_sentence_fidelity" in p.read_text(encoding="utf-8"))
    check(implementers == ["narrative_schema.py"],
          f"保真核对只能有一份实现（实测 {implementers}）：口径分叉是最难发现的一类缺陷")
    tree = ast.parse((REPO / "sections" / "narrative_schema.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("llm" not in imported,
          f"保真核对是机械判据，schema 层不得 import llm（实测 {sorted(imported)}）")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

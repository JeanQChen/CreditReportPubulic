"""Eval: M930-3C P8/P11 确定性 aggregate Binding Gate（§16.7.1 P8；§16.10 第 3/4/22/27/29 项）。

用法: python -m evals.test_demo_claim_binding_gate

本文件只测**机械门**：它不判语义、不调 LLM、不替调用方排序、不写库。判据全部来自真实
`SectionDraft` / `AuthorityScan` / `WriterMaterialManifest` 形状，不写公司名、不写固定页码或
答案关键词专用的规则。

覆盖（每条对应一个具体缺陷，不写凑数用例）：
* §3  proposal 缺失 / 重复 / **额外** / 顺序漂移 → 集合级问题必须 fail-closed 抛出，且
      **不得**伪装成一条 `result="fail"` 的决定（`narr-4` 的 wire 容量发现）；
* §4  aggregate digest 是**绑定内容**的函数，不是装饰：逐项扰动都必须改变它，且同一输入
      在同一进程内可重复得到同一 digest；
* §22 topic 路径 A 的最小集（P1-2）：缺 `ResearchMaterial` / payload / 精确 locator 必须拒；
      有 payload 而无 material 载体也必须拒；
* §30 引用了**别的节** manifest 的支撑边（r4 ④ 的真实形态）：材料真实存在、payload/locator
      可解析，错的只是归属 → 拒为 `manifest_identity_mismatch`；同一条边换成本节身份才落
      `missing_material_or_locator`。两条码**不得**混用（错 Pack 支撑边不得消失成材料缺口）；
* §27 同一 candidate revision 的重复决定：门本身是确定性的（同一输入两次得到同一条决定），
      因此「两条不同的决定」不可能由本门产生；真正的「两条决定」拒绝落在 P10（见
      `evals/test_demo_accepted_binding.py`）；
* §29 一条 aggregate 只有一个 subject：决定 id 与 subject 同源，`BindingSubjectRevision`
      不接受非 subject 对象，因此不存在「无 key 决定」这种形状。
* 纪律：本模块**不得** import `llm`（机械门不调模型）——用 AST 静态核对。

不调 LLM、不联网、不写任何文件。
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from sections import claim_binding_gate as CBG
from sections import narrative_schema as NS
from sections import pack_writer as PW

REPO = Path(__file__).resolve().parent.parent

FP = "a" * 64
FP_FIN = "b" * 64


def _scan(*facts):
    return PW.AuthorityScan(facts=tuple(facts), aspect_status={}, aspect_topic={},
                            aspect_impact={}, aspect_blocking={}, aspect_question={},
                            excluded_facts=(), conflicts=(), not_found=(), gaps=(),
                            coverage_counts={})


#: 路径 B / context 边的**闭合**值（§四.1/§四.2）：边上的 payload_ref 与 exact locator 必须与
#: 它声称绑定的 manifest 成员逐字相同，因此夹具里只用一处定义、成员与边共用一个常量。
MEMBER_LOCATOR = NS.char_range_locator("evidence:ev-1", 3, 40)

PACK_FACT = PW.AuthorityFactEntry(
    authority_kind="topic_pack", container_identity="pack-1", fact_id="f-1",
    text="公司2024年营业收入为1234.56亿元。", topic_id="t-1", aspect_ids=("a-1",),
    required=True, fact_type="metric", period="2024", scope="公司", material_id="m-1",
    payload_ref={"object_type": "research_material"}, locator_ref=MEMBER_LOCATOR,
    source_identity="evidence:ev-1", provenance_identity="prov-1", content_fingerprint=FP)
FIN_FACT = PW.AuthorityFactEntry(
    authority_kind="financial_pack", container_identity="fin-1", fact_id="ff-1",
    text="营业收入 1234.56 亿元", topic_id="t-1", aspect_ids=("a-1",), required=False,
    fact_type="metric", period="2024", scope="公司", material_id=None,
    payload_ref={"object_type": "financial_fact"}, locator_ref=None,
    source_identity="financial:art-1", provenance_identity="prov-f-1", content_fingerprint=FP_FIN)

#: `wmm-2` 成员身份必须包含**可解析的真实引用**（§三 A.7）：只有 material ID 的成员现在
#: 在构造期就不可表达，因为「成员存在」与「正文已解析」必须同一件事。这里用真实
#: `MaterialPayloadRef` 的规范 dict 构造（与生产同形，不做形态替身）。
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
MEMBER_PAYLOAD = dict(MANIFEST.entries[0].payload_ref)
#: 成员自己声明的 payload_ref 与 locator_ref，就是路径 B / context 边必须逐字带上的闭合值。

#: **兄弟节**的 manifest（§30）：成员身份、payload、locator 全部是**真实可解析**的引用，
#: 唯一区别是它属于**另一个** Pack / 另一节。r4 现场就是这个形态——被拒的支撑边引用的材料
#: **确实存在**，只是存在于别的节里；因此不能用「幽灵材料 id」来近似它。
SIBLING_LOCATOR = NS.char_range_locator("evidence:ev-1", 41, 78)
SIBLING_MANIFEST = NS.WriterMaterialManifest.create(members=(
    NS.WriterMaterialManifestEntry.create(
        pack_id="pack-2", material_id="m-sibling", research_material_disposition_id="rmd-2",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=PAYLOAD_REF, locator_ref=SIBLING_LOCATOR,
        payload_hash=FP, reading_view_fingerprint=FP),))

REVISION = NS.derive_draft_revision(
    task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
    contract_version="cv-1", contract_fingerprint="cf-1", writer_policy_version="wp-1",
    prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
    manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint())
CANDIDATE = NS.ClaimCandidate.create(
    draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入为1234.56亿元。", fact_type="metric")
CANDIDATE_2 = NS.ClaimCandidate.create(
    draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
    report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
    claim_text="公司2024年营业收入同比增长。", fact_type="metric")
UNIT = NS.NarrativeDraftUnit.create(
    draft_revision=REVISION, section_id="company", index=0, unit_kind="paragraph",
    text="本节说明公司经营情况。")


def factual(**over):
    kw = dict(binding_subject_kind="claim_candidate", binding_subject_id=CANDIDATE.candidate_id,
              draft_revision=REVISION, manifest_id=MANIFEST.manifest_id,
              manifest_fingerprint=MANIFEST.fingerprint(), authority_kind="topic_pack",
              authority_container_id="pack-1", source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="primary", support_semantics="factual",
              authorization_path="path_a_prevalidated", content_fingerprint=FP,
              dependency_fingerprint="dep-1", fact_id="f-1", material_id="m-1",
              payload_ref={"object_type": "research_material"},
              locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40))
    kw.update(over)
    return NS.ProposedSupportRef.create(**kw)


def context(**over):
    kw = dict(binding_subject_kind="narrative_draft_unit", binding_subject_id=UNIT.draft_unit_id,
              draft_revision=REVISION, manifest_id=MANIFEST.manifest_id,
              manifest_fingerprint=MANIFEST.fingerprint(), authority_kind="topic_pack",
              authority_container_id="pack-1", source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="corroborating",
              support_semantics="context", authorization_path="context_only",
              content_fingerprint=FP, dependency_fingerprint="dep-1", material_id="m-1",
              # §四.2：context 边必须自闭合（载体 + payload + locator 同在一条边上），值与
              # manifest 成员逐字相同。
              payload_ref=dict(MEMBER_PAYLOAD), locator_ref=MEMBER_LOCATOR)
    kw.update(over)
    return NS.ProposedSupportRef.create(**kw)


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

    def expect_raises(msg: str, fn, exc, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:160]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    scan = _scan(PACK_FACT, FIN_FACT)
    sr1 = CBG.BindingSubjectRevision.from_subject(CANDIDATE)

    # ============================================================ §1 单边通过 → 恰好一条决定
    p1 = factual()
    d1 = CBG.decide_bindings(sr1, [p1], scan, manifest=MANIFEST)
    check(d1.result == "pass" and d1.structural_reason_code is None,
          f"单边通过必须 result=pass 且无 structural 码（实际 {d1.result!r}/"
          f"{d1.structural_reason_code!r}）")
    check([e.result for e in d1.edge_results] == ["pass"]
          and [e.reason_code for e in d1.edge_results] == [None],
          "逐边结果必须记录 pass 与 None 原因码")
    check(d1.proposal_ids == (p1.proposed_support_id,)
          and d1.proposal_hashes == (p1.content_hash(),),
          "决定必须绑定 proposal id 与**内容**哈希（不只 id）")
    check(d1.authority_kind == "topic_pack" and d1.subject_kind == "claim_candidate",
          "决定必须记录主导 authority kind 与 subject kind")
    check(d1.rules_version == NS.CLAIM_BINDING_GATE_VERSION == CBG.CLAIM_BINDING_GATE_VERSION,
          "决定的 rules_version 必须是 gate 版本（唯一字面量在 wire 模块）")
    check(CBG.CLAIM_BINDING_GATE_VERSION == "cbg-3",
          f"gate 版本字面量必须是 cbg-3（判据实现未改，但所读的高风险标记集随 nrules-11 "
          f"前进，实际 {CBG.CLAIM_BINDING_GATE_VERSION!r}）")

    # 确定性：同一输入两次得到同一条决定（不是「差不多」，是逐字段相同）。
    check(CBG.decide_bindings(sr1, [p1], scan, manifest=MANIFEST).to_dict() == d1.to_dict(),
          "同一输入的两次决定必须逐字段相同（决定里不得混入时间/随机）")

    # ============================================================ §2 digest 是绑定内容的函数
    recomputed = CBG.support_set_digest(
        subject_revision=sr1, proposal_ids=d1.proposal_ids, proposal_hashes=d1.proposal_hashes,
        manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint())
    check(recomputed == d1.support_set_digest,
          f"决定的 digest 必须等于独立重算值（{d1.support_set_digest!r} ≠ {recomputed!r}）")
    baseline = d1.support_set_digest
    drifts = {
        "换 subject revision": dict(
            subject_revision=CBG.BindingSubjectRevision.from_subject(CANDIDATE_2),
            proposal_ids=d1.proposal_ids, proposal_hashes=d1.proposal_hashes,
            manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint()),
        "换 proposal hash（内容变了）": dict(
            subject_revision=sr1, proposal_ids=d1.proposal_ids, proposal_hashes=("0" * 64,),
            manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint()),
        "换 manifest 身份": dict(
            subject_revision=sr1, proposal_ids=d1.proposal_ids, proposal_hashes=d1.proposal_hashes,
            manifest_id="wmm-other", manifest_fingerprint=MANIFEST.fingerprint()),
        "换 manifest 指纹": dict(
            subject_revision=sr1, proposal_ids=d1.proposal_ids, proposal_hashes=d1.proposal_hashes,
            manifest_id=MANIFEST.manifest_id, manifest_fingerprint="0" * 64),
        "换规则版本": dict(
            subject_revision=sr1, proposal_ids=d1.proposal_ids, proposal_hashes=d1.proposal_hashes,
            manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint(),
            rules_version="cbg-2"),
    }
    for label, kwargs in drifts.items():
        check(CBG.support_set_digest(**kwargs) != baseline,
              f"{label} 必须改变 support-set digest（否则 digest 是装饰，不是绑定）")

    # ============================================================ §3 集合级问题 ⋯ 抛出，绝不成决定
    p2 = factual(authority_kind="financial_pack", authority_container_id="fin-1",
                 financial_fact_id="ff-1", fact_id=None, material_id=None,
                 payload_ref={"object_type": "financial_fact"}, locator_ref=None,
                 source_identity="financial:art-1", provenance_identity="prov-f-1",
                 content_fingerprint=FP_FIN, support_role="corroborating")
    ordered = sorted([p1, p2], key=lambda p: p.proposed_support_id)
    d_multi = CBG.decide_bindings(sr1, ordered, scan, manifest=MANIFEST)
    check(d_multi.result == "pass" and d_multi.authority_kind == "topic_pack",
          "跨 authority kind 的合法集合必须通过，且主导 kind 取 canonical order 里第一条 primary 边")
    check(len(d_multi.proposal_ids) == 2,
          "跨 kind 集合的决定必须绑定**两条** proposal（不得只留主导那条）")

    structurals = {
        # 缺失：集合里有边没有身份（没有 id 的边不进聚合签名）。
        "proposal_missing": lambda: CBG.decide_bindings(
            sr1, [_mutated(p1, proposed_support_id=""), p2], scan, manifest=MANIFEST),
        # 重复：同一 proposal 出现两次会让 cardinality 静默失效。
        "proposal_duplicate": lambda: CBG.decide_bindings(sr1, [p1, p1], scan, manifest=MANIFEST),
        # 顺序漂移：本门不替调用方排序（排序会静默改变被绑定的有序集合）。
        "proposal_order_drift": lambda: CBG.decide_bindings(sr1, ordered[::-1], scan,
                                                            manifest=MANIFEST),
        # 空集：narr-4 的决定无法表达空 proposal 集。
        "proposal_set_empty": lambda: CBG.decide_bindings(sr1, [], scan, manifest=MANIFEST),
    }
    for code, fn in structurals.items():
        expect_raises(f"集合级问题 {code} 必须 fail-closed 抛出", fn, CBG.ClaimBindingGateError,
                      needle=code)
    check(not (set(CBG.STRUCTURAL_PRIORITY) - set(structurals) - {"proposal_set_incomplete",
                                                                 "proposal_set_has_unknown"}),
          "STRUCTURAL_PRIORITY 里的码必须都在本文件覆盖或有明确归属")

    # 「额外」：proposal 指向 Draft 里不存在的 subject → 不得凭空多出一条无主决定。
    ghost = factual(binding_subject_id="ccand-ghost")
    check(ghost.proposed_support_id != p1.proposed_support_id,
          "前置条件：幽灵 subject 的 proposal 是另一条边")
    # 第一层：Draft 构造器自己就拒绝伪 proposal（上游不是靠门来兜底）。
    expect_raises("SectionDraft 构造器必须拒绝指向本节之外 subject 的 proposal",
                  lambda: _draft(proposals=(p1, ghost, context()), candidates=(CANDIDATE,)),
                  NS.NarrativeSchemaError, needle="指向本节之外的 subject")
    # 第二层：门**自己**再断言一次（不假设上游校验过）——用一个绕过构造器的 Draft 触发。
    bypass = _draft(proposals=(p1, context()), candidates=(CANDIDATE,))
    bypass = copy.copy(bypass)
    object.__setattr__(bypass, "proposed_support_refs",
                       tuple(sorted((p1, ghost, context()),
                                    key=lambda p: p.proposed_support_id)))
    expect_raises("proposal 指向 Draft 里不存在的 subject 必须拒",
                  lambda: CBG.decide_draft_bindings(bypass, scan),
                  CBG.ClaimBindingGateError, needle="不存在的 subject")

    # 协调器输出必须覆盖 Draft 声明的 exact proposal 集（缺失/多出都拒）。
    expect_raises("manifest 与 Draft 自带的 exact manifest 不同必须拒",
                  lambda: CBG.decide_draft_bindings(
                      _draft(proposals=(p1, context()), candidates=(CANDIDATE,)), scan,
                      manifest=NS.WriterMaterialManifest.create(members=())),
                  CBG.ClaimBindingGateError, needle="不是同一份")

    # ============================================================ §22 路径 A 的最小集（P1-2）
    a_no_payload = factual(payload_ref=None, locator_ref=None)
    d_a = CBG.decide_bindings(sr1, [a_no_payload], scan, manifest=MANIFEST)
    check(d_a.result == "fail"
          and [e.reason_code for e in d_a.edge_results] == ["missing_material_or_locator"],
          "路径 A 缺 payload / 精确 locator 必须逐边 fail 为 missing_material_or_locator"
          f"（实际 {[e.reason_code for e in d_a.edge_results]}）")
    # schema 层已经拒绝「有 payload 无 material」的**构建**（所以这里只能造一个未过 schema 的
    # 鸭子对象）：门必须自己再拒一次，不得假设上游校验过。
    payload_no_material = _mutated(p1, material_id=None)
    expect_raises("schema 层必须拒绝构造「有 payload 无 material」的 proposal",
                  lambda: factual(material_id=None), NS.NarrativeSchemaError,
                  needle="payload_ref 只在绑定 material_id 时允许")
    d_p = CBG.decide_bindings(sr1, [payload_no_material], scan, manifest=MANIFEST)
    check(d_p.result == "fail"
          and [e.reason_code for e in d_p.edge_results] == ["forbidden_field_present"],
          "topic_pack 有 payload 而无 material 载体必须 fail 为 forbidden_field_present")
    bad_locator = factual(locator_ref=NS.char_range_locator("evidence:ev-1", 4, 41))
    d_l = CBG.decide_bindings(sr1, [bad_locator], scan, manifest=MANIFEST)
    check(d_l.result == "fail"
          and [e.reason_code for e in d_l.edge_results] == ["missing_material_or_locator"],
          "路径 A 的 locator 与权威事实不一致必须 fail（不得只看「有没有」）")
    # 路径 A 的坐标必须真实存在：换容器 / 换 kind / 换 fact 分别是三个不同的码。
    for label, kwargs, code in (
            ("同 kind+fact、容器不同", dict(authority_container_id="pack-other"),
             "authority_container_mismatch"),
            ("同容器+fact、kind 不同", dict(authority_kind="financial_pack",
                                            financial_fact_id="f-1", fact_id=None,
                                            material_id=None, payload_ref=None,
                                            locator_ref=None, content_fingerprint=FP),
             "authority_kind_mismatch"),
            ("坐标不存在", dict(fact_id="f-ghost"), "missing_authority_fact")):
        d = CBG.decide_bindings(sr1, [factual(**kwargs)], scan, manifest=MANIFEST)
        check([e.reason_code for e in d.edge_results] == [code],
              f"路径 A {label} 必须 fail 为 {code}"
              f"（实际 {[e.reason_code for e in d.edge_results]}）")
    # 材料不在 exact manifest 里（Writer 漏掉的材料）→ 路径 B / context 都必须失败。
    for label, prop in (("路径 B", factual(
            authorization_path="path_b_material_derived", fact_id=None,
            payload_ref=dict(MEMBER_PAYLOAD), locator_ref=MEMBER_LOCATOR,
            material_id="m-ghost")), ("context", context(material_id="m-ghost"))):
        sr = CBG.BindingSubjectRevision.from_subject(UNIT) if label == "context" else sr1
        d = CBG.decide_bindings(sr, [prop], scan, manifest=MANIFEST)
        check([e.reason_code for e in d.edge_results] == ["missing_material_or_locator"],
              f"{label} 引用不在 manifest 里的材料必须 fail 为 missing_material_or_locator")

    # ======================================== §30 引用**别的节**的 manifest（④ 的真实形态）
    # r4 现场（`实`）：`mat-tm-b6bc…` 是 r4 **公司经营节** Pack 的真实成员，却被一条
    # `company_legal_risks` 的支撑边引用——材料本身存在、payload 与 locator 都可解析，
    # 错的只是**归属**。下面三条是同一条边、同一份真实材料，唯一变量是 **manifest 身份**；
    # 拒的时候必须说得出「这是别的节的身份」，不得一律读成「材料不存在」。
    check(SIBLING_MANIFEST.manifest_id != MANIFEST.manifest_id
          and SIBLING_MANIFEST.fingerprint() != MANIFEST.fingerprint(),
          "前置：兄弟节 manifest 的身份必须与本节的**确实不同**（否则这一组是平凡的）")
    check([e.material_id for e in SIBLING_MANIFEST.entries] == ["m-sibling"]
          and [e.material_id for e in MANIFEST.entries] == ["m-1"],
          "前置：兄弟节成员确实存在（不是幽灵材料），且它**不在**本节的成员表里")
    sibling = dict(material_id="m-sibling",
                   payload_ref=dict(SIBLING_MANIFEST.entries[0].payload_ref),
                   locator_ref=SIBLING_LOCATOR)
    sr_unit = CBG.BindingSubjectRevision.from_subject(UNIT)
    # (a) 边带**兄弟节**的 manifest 身份 → 拒，码是 `manifest_identity_mismatch`。
    d_x = CBG.decide_bindings(
        sr_unit, [context(manifest_id=SIBLING_MANIFEST.manifest_id,
                          manifest_fingerprint=SIBLING_MANIFEST.fingerprint(), **sibling)],
        scan, manifest=MANIFEST)
    check(d_x.result == "fail"
          and [e.reason_code for e in d_x.edge_results] == ["manifest_identity_mismatch"],
          "引用别的节 manifest 身份的支撑边必须 fail 为 manifest_identity_mismatch"
          f"（实际 {[e.reason_code for e in d_x.edge_results]}）")
    # (b) 同一份材料、同一条边，换成**本节**身份 → 该材料确实不在本节成员表里 →
    #     `missing_material_or_locator`。两条码必须**分开**：一个是「身份是别人的」，
    #     一个是「这里没有这件材料」；把前者读成后者会让「错 Pack 支撑边」消失成材料缺口。
    d_y = CBG.decide_bindings(sr_unit, [context(**sibling)], scan, manifest=MANIFEST)
    check([e.reason_code for e in d_y.edge_results] == ["missing_material_or_locator"],
          "同一条边换成本节 manifest 身份后必须 fail 为 missing_material_or_locator"
          f"（实际 {[e.reason_code for e in d_y.edge_results]}）")
    # (c) 正例：本节自己的成员 + 本节身份 → 通过（证明上面两条不是「context 边一律被拒」）。
    d_z = CBG.decide_bindings(sr_unit, [context()], scan, manifest=MANIFEST)
    check(d_z.result == "pass" and [e.reason_code for e in d_z.edge_results] == [None],
          "本节自己的成员 + 本节身份必须通过（否则上面两条反例是平凡的）")

    # ============================================================ §二 路径 B 授权面（P0，`cbg-2`）
    # 门必须**自己**判一次「路径 B 只授权非高风险描述性原子」，不得依赖上游 Writer 的预验证：
    # 下面每个 Draft 都是**手工构造**的（直接 `NS.SectionDraft.create`，完全没有经过
    # `PW.write_section`），因此写入侧那道预验证在此**根本不会被调用**——门若只看「材料里
    # 逐字有没有」就会全部放行。而且门根本看不到材料正文（它只有 manifest 身份）：
    # 正例与反例的 manifest、pack、material 完全相同，唯一变量是**候选文本本身**。
    def _path_b_draft(text: str):
        candidate = NS.ClaimCandidate.create(
            draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
            report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
            claim_text=text, fact_type="descriptive")
        prop = factual(binding_subject_id=candidate.candidate_id,
                       authorization_path="path_b_material_derived", fact_id=None,
                       payload_ref=dict(MEMBER_PAYLOAD), locator_ref=MEMBER_LOCATOR,
                       material_id="m-1")
        return _draft(proposals=(prop,), candidates=(candidate,))

    def _path_b_decision(text: str):
        draft = _path_b_draft(text)
        decisions = CBG.decide_draft_bindings(draft, scan)
        check(len(decisions) == 1 and draft.claim_candidates[0].claim_text == text,
              "手工 Draft 的前置条件：exact proposal 集只含一条路径 B 边")
        return decisions[0]

    ok_text = "主营业务由动力电池与储能两大业务条线构成。"
    ok_decision = _path_b_decision(ok_text)
    check(NS.high_risk_surface_tokens(ok_text) == (),
          f"正向对照夹具本身必须不含任何高风险表面（实测 "
          f"{NS.high_risk_surface_tokens(ok_text)}）")
    check(ok_decision.result == "pass"
          and [e.reason_code for e in ok_decision.edge_results] == [None],
          "正向对照：不含任何高风险表面的单原子业务描述必须走通路径 B 的绑定门"
          f"（实际 {ok_decision.result!r}/{ok_decision.edge_results[0].reason_code!r}）")

    for label, text, surface in (
            ("显式否定", "经营环境未发生重大变化。", "未发生"),
            ("法人主体身份", "示例产业集团从事动力电池制造。", "示例产业集团"),
            ("勾选 / 适用状态", "该事项不适用。", "不适用"),
            ("表格行列关系", "主营业务收入中动力电池占比较高。", "占比")):
        check(surface in NS.high_risk_surface_tokens(text),
              f"反例前置条件：{label}反例文本必须命中高风险表面「{surface}」"
              f"（实测 {NS.high_risk_surface_tokens(text)}）")
        decision = _path_b_decision(text)
        check(decision.result == "fail"
              and [e.reason_code for e in decision.edge_results] == ["path_b_high_risk_surface"],
              f"手工构造 Draft 绕过 Writer 时，{label}仍必须被绑定门逐边拒为 "
              f"path_b_high_risk_surface（实际 {decision.result!r}/"
              f"{[e.reason_code for e in decision.edge_results]}）")
    check(ok_decision.support_set_digest
          != _path_b_decision("该事项不适用。").support_set_digest,
          "正例与反例的 support-set digest 必须不同（否则「同一输入同一决定」会掩盖文本差异）")

    # ============================================================ §27/§29 subject 身份与唯一性
    check(CBG.BindingSubjectRevision.from_subject(CANDIDATE).key
          == ("claim_candidate", CANDIDATE.candidate_id, REVISION),
          "subject key 必须是 (kind, id, revision) 三元组")
    check(CBG.BindingSubjectRevision.from_subject(UNIT).key
          != CBG.BindingSubjectRevision.from_subject(CANDIDATE).key,
          "claim_candidate 与 narrative_draft_unit 不得折成同一个 key")
    expect_raises("非 subject 对象（无绑定身份）必须拒",
                  lambda: CBG.BindingSubjectRevision.from_subject("not-a-subject"),
                  CBG.ClaimBindingGateError)
    d2 = CBG.decide_bindings(CBG.BindingSubjectRevision.from_subject(CANDIDATE_2),
                             [factual(binding_subject_id=CANDIDATE_2.candidate_id,
                                      fact_id="f-ghost")], scan, manifest=MANIFEST)
    check(d2.binding_decision_id != d1.binding_decision_id,
          "另一 candidate revision 的决定必须是另一条 id（一条决定不得复用于另一 subject）")
    decisions = CBG.decide_draft_bindings(
        _draft(proposals=sorted([p1, context()], key=lambda p: p.proposed_support_id),
               candidates=(CANDIDATE,)), scan)
    check(len(decisions) == 2
          and {d.subject_kind for d in decisions} == {"claim_candidate", "narrative_draft_unit"},
          "Draft 的 exact subject 集（候选 + 草稿单元）各得到恰好一条决定")
    check(len({d.binding_decision_id for d in decisions}) == 2,
          "两条决定必须各自独立（不得由一条 id 覆盖两个 subject）")

    # ============================================================ §4 机械门不调模型
    tree = ast.parse((REPO / "sections" / "claim_binding_gate.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("llm" not in imported,
          f"机械门不得 import llm（实测 {sorted(imported)}）：判语义不是它的职责")
    check("llm" not in dir(CBG) and not hasattr(CBG, "evaluate"),
          "机械门不得暴露任何 LLM 调用面")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


def _draft(*, proposals, candidates, units=(UNIT,)):
    return NS.SectionDraft.create(
        task_id="t-1", section_id="company", company_id="c-1", report_as_of="2024-12-31",
        contract_version="cv-1", contract_fingerprint="cf-1", producer_kind="topic_harness",
        writer_policy_version="wp-1", prompt_version="pack_section_writer_proposals_v1",
        model_policy="stub", authority_container_ids=("pack-1", "fin-1"),
        material_manifest=MANIFEST,
        material_dispositions=(NS.WriterMaterialProcessingDisposition.create(
            manifest_id=MANIFEST.manifest_id, manifest_fingerprint=MANIFEST.fingerprint(),
            pack_id="pack-1", material_id="m-1", research_material_disposition_id="rmd-1",
            material_content_fingerprint=FP, processed=True, usage="used",
            support_usages=tuple(p.proposed_support_id for p in proposals), reason_code=None,
            reason_proof=None, writer_policy_version="wp-1"),),
        claim_candidates=tuple(candidates), narrative_draft_units=tuple(units),
        proposed_support_refs=tuple(sorted(proposals, key=lambda p: p.proposed_support_id)),
        unresolved_ids=(), unresolved_projections=(), coverage_summary={},
        conflict_projections=(), not_found_projections=(), dependency_fingerprint="dep-1")


def _mutated(proposal, **over):
    """一条**真实** proposal 的浅拷贝，改成未过 schema 的形状（`__post_init__` 之后改字段）。

    门对**任何**传入对象做机械复核（不假设上游校验过），这些反例必须是「schema 会拒、但门也
    必须自己拒」的形状。浅拷贝是必须的：反例不得污染后续用例里的正例对象。
    """
    clone = copy.copy(proposal)
    for name, value in over.items():
        object.__setattr__(clone, name, value)
    return clone


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

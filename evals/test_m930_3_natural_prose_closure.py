"""Eval: M930-3 指令 E 第 3 项（**门后**那一半）——门前草稿作为表达基础的**句级闭合**。

用法: python -X utf8 -m evals.test_m930_3_natural_prose_closure

本模块只测**一件事**：`natural` 句类怎么被**产生**出来，以及「借一段草稿之名」这条路有没有
被堵死。保真判据本身（`natfid-1` 的七条轴）归 `evals.test_m930_3_sentence_fidelity`；这里只
引用它的**存在**（正向自然句真的过了它、丢断言的自然句真的被它拒），不重复它的用例。

覆盖（每条对应一个具体缺陷）：

* §1 台账（`prose_draft_ledger`）：门前草稿 → 逐原子 `kept` / `dropped` 账；`kept` 必须真的
      对应一条**已定稿 Claim**（候选没过门 ⇒ 没有 claim_id ⇒ `dropped`）；**表格承载的 Claim
      不算 kept**（它不在本次调用的 claims 面里，正文再绑一次就是重复陈列），读数单列；
      **原子全没过门的草稿不进输入面**（投过去只会诱使模型把没过门的断言写回来）；
      键集封闭且顺序由声明决定。
* §2 正向：声明 `prose_unit_id` 的句子**以草稿为表达基础**——同一段文本在逐字写法下必被拒
      （Claim 文本没有逐字按序出现），声明出处后放行；句类由**系统**判定为 `natural`；
      补救阶梯（⑥）对它**整句跳过**（否则每一条自然句都会被判成 `claims_not_present_in_order`
      然后 fail-closed）；`trace` 留草稿层读数与自然句计数。
* §3 反例（句级来源闭合，`norg-7` 规则集新增的两条）：
      3.1 未在本轮输入里的 `prose_unit_id`（自造的 / `kept` 原子为空的 / 属于别的修订的）→ 拒；
      3.2 借一段草稿之名声明那一行**没有**的 Claim → 拒（否则草稿出处就是任意 Claim 的通行证）；
      3.3 声明出处却不绑定任何 Claim → 拒（自然句承载事实，没有事实来源的句子不得成立）；
      3.4 系统补救产出的句子挂草稿出处 → 拒（补救过的文本不再是草稿里那一句）；
      3.5 模型自报 `sentence_kind` 一律无效：自称 `natural` 换不来自然写法（该句照旧过逐字
          判据并被拒），自称 `factual` 也换不掉系统判定；
* §4 阳性对照与「没有放宽」：
      4.1 空草稿层（没有草稿 / 草稿原子全没过门）时输入面与旧修订**逐字相同**（旧调用方
          行为不变），此时声明出处必被拒；
      4.2 逐字判据**没有**被删掉：同样的自然句文本，不声明出处照旧被拒；
      4.3 保真判据**真的在门内跑**：声明了出处但丢掉声明 Claim 的数字表面 → 拒。

全部离线、确定性：生成器是返回结构化 `PW.NarrationResult` 的 stub。不读库、不连网、
不调真实 LLM、不写任何文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as HS
from harness import topic_schema as TS
from sections import material_context as MC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

TOPIC_A = "topic-company-business"
TOPIC_B = "topic-company-legal"
IDENTITY = {"task_id": "task-1", "section_id": "company",
            "section_draft_id": "draft-1", "draft_revision": "rev-1"}

#: 已定稿 Claim 的文本（原子子句口径，与候选侧一致）。
TEXT_A = "公司主营业务由动力电池与储能两大条线构成"
TEXT_B = "公司销售以直销为主"
TEXT_C = "公司境外收入占比相对较低"
TEXT_N = "公司2024年营业收入为1234.56亿元"
TEXT_M = "公司2024年研发投入为56.78亿元"

#: 草稿文本：**材料驱动的自然散文**（不是 Claim 文本的拼接），它表达三段事实原子。
UNIT1_TEXT = ("公司主营业务由动力电池与储能两大条线构成，销售以直销为主，"
              "2024 年研发投入 56.78 亿元，境外收入占比相对较低。")
#: 另一段草稿：它的原子**一条都没有**通过上游两道门（候选存在、但没有对应 Claim）。
UNIT2_TEXT = "公司于报告期内完成了对某标的企业控股权的收购。"


def _citation(evidence_id: str) -> HS.CitationRef:
    return HS.CitationRef(ref_type="evidence", evidence_id=evidence_id)


def _claim(text: str, topic_id: str, *, index: int, candidate: str) -> SS.SectionClaim:
    question_ids = (f"q-{index}",)
    refs = (_citation(f"evidence:ev-{index}"),)
    claim_id = SS.derive_claim_id("fact", topic_id, question_ids, text, refs,
                                  candidate, "rev-1", (f"asb-{index}",))
    return SS.SectionClaim(
        claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
        topic_id=topic_id, question_ids=question_ids, text=text, claim_type="fact",
        citation_refs=refs, claim_candidate_id=candidate,
        claim_candidate_revision="rev-1", accepted_binding_ids=(f"asb-{index}",))


#: 草稿单元的**材料出处轴**（`narr-8`）：必须是登记过的两条轴之一的前缀（材料侧 `wmmref_`）。
#: 这里用真实派生器产出，而不是早先那个自造字面量 `"manifest:m-1"`——后者在任何一条轴上都不
#: 成立，`narr-8` 起在**构造期**即抛（本文档 §0 里「夹具失真」的同一类问题）。
PACK_ID, MATERIAL_ID = "pack-1", "m-1"
MEMBER_REF = NS.manifest_member_ref(PACK_ID, MATERIAL_ID)
_MATERIAL_TEXT = UNIT1_TEXT
_FP = "a" * 64


def _resolved_material() -> MC.ResolvedWriterMaterial:
    return MC.ResolvedWriterMaterial.create(
        member_ref=MEMBER_REF, topic_id=TOPIC_A, pack_id=PACK_ID, material_id=MATERIAL_ID,
        material_type="evidence_span", research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40),
        payload_ref=TS.MaterialPayloadRef(
            object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
            content_hash=_FP,
            locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                                       section_path="s1", page=3),
            created_dependency_fingerprint="d" * 64).to_dict(),
        payload_hash=_FP, content_hash=_FP, material_content_fingerprint=_FP,
        reading_view=_MATERIAL_TEXT, content_qualification={"kind": "text"})


def _manifest() -> NS.WriterMaterialManifest:
    """清单成员的身份字段逐项取自**已解析材料**，因此两侧默认逐字相等（`orgmc-1` 的核对面）。"""
    material = _resolved_material()
    return NS.WriterMaterialManifest.create(members=(
        NS.WriterMaterialManifestEntry.create(
            pack_id=material.pack_id, material_id=material.material_id,
            research_material_disposition_id=material.research_material_disposition_id,
            source_identity=material.source_identity,
            provenance_identity=material.provenance_identity,
            material_content_fingerprint=material.material_content_fingerprint,
            topic_id=material.topic_id, material_type=material.material_type,
            payload_ref=dict(material.payload_ref), locator_ref=dict(material.locator_ref),
            payload_hash=material.payload_hash,
            reading_view_fingerprint=material.reading_view_fingerprint),))


def _material_context() -> MC.WriterMaterialContext:
    return MC.WriterMaterialContext.create(
        task_id="t-1", section_id="company", pack_set_fingerprint="e" * 64,
        materials=(_resolved_material(),))


#: 本次调用投给组织器的材料正文上下文（`orgmc-1`）：门后改写要有材料原文可依。
MATERIAL_CONTEXT = _material_context()
MANIFEST = _manifest()


def _unit(*, index: int, text: str, atoms, refs=(MEMBER_REF,)) -> NS.NaturalProseDraftUnit:
    return NS.NaturalProseDraftUnit.create(
        index=index, text=text, draft_revision="rev-1", section_id="company",
        source_member_refs=list(refs), atom_candidate_ids=list(atoms))


class _DraftStub:
    """`SectionDraft` 的最小形状替身：组织器读它的 `natural_prose_draft`（表达基础）与
    `material_manifest`（`orgmc-1` 依据面的身份核对面）。"""

    def __init__(self, units=(), manifest=MANIFEST) -> None:
        self.natural_prose_draft = tuple(units)
        self.material_manifest = manifest


class _StubLlm:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def narrate(self, *, messages, system, prompt_version, model_policy):
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy,
                           "messages": messages, "system": system})
        if not self.responses:
            raise AssertionError("stub LLM 被超额调用（应当 fail-closed 而不是继续重试）")
        item = self.responses.pop(0)
        index = len(self.calls)
        return PW.NarrationResult(
            text=item if isinstance(item, str) else json.dumps(item, ensure_ascii=False),
            call_id=f"call-{index}", model=PW.MODEL_POLICY_STUB,
            prompt_version=prompt_version, status="ok",
            input_tokens=128, output_tokens=64, latency_ms=1, finish_reason="stop")


def _plan(sentences, dispositions):
    return {"paragraphs": [{"sentences": list(sentences)}],
            "claim_dispositions": list(dispositions)}


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
                details.append(f"FAIL {msg}：原因不符（{str(e)[:200]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:200]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    # --------------------------------------------------------------- 夹具
    claim_a = _claim(TEXT_A, TOPIC_A, index=1, candidate="cand-1")
    claim_b = _claim(TEXT_B, TOPIC_A, index=2, candidate="cand-2")
    claim_c = _claim(TEXT_C, TOPIC_B, index=3, candidate="cand-5")
    claim_n = _claim(TEXT_N, TOPIC_A, index=4, candidate="cand-4")
    claim_m = _claim(TEXT_M, TOPIC_A, index=5, candidate="cand-6")
    claims = (claim_a, claim_b, claim_c, claim_n, claim_m)
    by_candidate = {c.claim_candidate_id: c for c in claims}

    #: `cand-3`（草稿里有原子）与 `cand-9`（第二段草稿的原子）**没有**对应 Claim：
    #: 这两条候选没过门，因此对应的草稿分句在正文里必须被删掉或改写掉。
    unit1 = _unit(index=0, text=UNIT1_TEXT,
                  atoms=("cand-1", "cand-2", "cand-3", "cand-6"))
    unit2 = _unit(index=1, text=UNIT2_TEXT, atoms=("cand-9",))

    def _d(claim, disposition="selected", reason=None):
        return {"claim_id": claim.claim_id, "disposition": disposition, "reason_code": reason}

    # ============================================================ §1 台账
    payload, readings = NO.prose_draft_ledger(
        draft=_DraftStub((unit1, unit2)), claims=claims, tabled=(claim_n.claim_id,))
    check(tuple(payload[0]) == NO._PROSE_UNIT_KEYS,
          f"台账行键集必须由声明决定（实测 {tuple(payload[0])}）")
    check(tuple(payload[0]["atoms"][0]) == NO._PROSE_ATOM_KEYS,
          f"台账原子键集必须由声明决定（实测 {tuple(payload[0]['atoms'][0])}）")
    check(readings == {"prose_units_total": 2, "prose_units_offered": 1,
                       "prose_units_without_kept_atom": 1, "atoms_total": 5,
                       "atoms_kept": 3, "atoms_dropped": 2, "atoms_kept_but_tabled": 0},
          f"草稿层读数必须逐项可查（实测 {readings}）")
    check(len(payload) == 1 and payload[0]["prose_unit_id"] == unit1.prose_unit_id,
          "只有 kept 原子至少一条的草稿才进输入面（`unit2` 的原子全没过门）")
    check(payload[0]["text"] == UNIT1_TEXT
          and payload[0]["material_member_refs"] == [MEMBER_REF],
          "草稿文本与出处标注必须逐字进输入面（这一段是表达基础，不是重写过的）")
    dropped_atoms = [a for a in payload[0]["atoms"] if a["status"] == NO.PROSE_ATOM_DROPPED]
    check([a["atom_candidate_id"] for a in dropped_atoms] == ["cand-3"]
          and dropped_atoms[0]["claim_id"] is None,
          f"没过门的原子必须以 `dropped` 且**无 claim_id** 的形态入账（实测 {dropped_atoms}）")
    kept_atoms = [a for a in payload[0]["atoms"] if a["status"] == NO.PROSE_ATOM_KEPT]
    check([a["claim_id"] for a in kept_atoms]
          == [claim_a.claim_id, claim_b.claim_id, claim_m.claim_id],
          f"`kept` 原子必须逐条对上一条已定稿 Claim（实测 {kept_atoms}）")
    check(NO.prose_ledger_claim_ids(payload)
          == {unit1.prose_unit_id: (claim_a.claim_id, claim_b.claim_id, claim_m.claim_id)},
          "台账的读取口径（`prose_ledger_claim_ids`）必须与构造口径一致")

    # 表格承载的 Claim **不算** kept：它不在本次调用的 claims 面里。
    _p2, r2 = NO.prose_draft_ledger(
        draft=_DraftStub((unit1, _unit(index=2, text="公司2024年营业收入为1234.56亿元。",
                                       atoms=("cand-4",)))),
        claims=claims, tabled=(claim_n.claim_id,))
    check(r2["atoms_kept"] == 3 and r2["atoms_kept_but_tabled"] == 1
          and r2["atoms_dropped"] == 2,
          "由门后表格承载的 Claim 不得记为 kept（否则正文会把表格行再陈列一遍），"
          f"读数必须单列（实测 {r2}）")
    check([a for a in _p2[0]["atoms"] if a["status"] == NO.PROSE_ATOM_KEPT] and
          all(a["claim_id"] != claim_n.claim_id for a in _p2[0]["atoms"]),
          "表格承载的 Claim 不得出现在任何 kept 原子里")

    # ============================================================ §2 正向：以草稿为表达基础
    #: 自然改写：两条 Claim 被合成一句，`主营` 被省去、`公司销售` 被写成 `销售`——用词与语序
    #: 都与 Claim 文本不同。它在逐字写法下**必然被拒**（`claims_not_present_in_order`），
    #: 这正是本模式存在的理由。
    natural_text = "公司业务由动力电池与储能两大条线构成，销售以直销为主。"
    natural_plan = _plan(
        [{"text": natural_text, "claim_ids": [claim_a.claim_id, claim_b.claim_id],
          "context_binding_ids": [], "prose_unit_id": unit1.prose_unit_id}],
        [_d(claim_a), _d(claim_b), _d(claim_c, "omitted", "outside_section_topic"),
         _d(claim_n, "omitted", "outside_section_topic"),
         _d(claim_m, "omitted", "outside_section_topic")])
    llm = _StubLlm(natural_plan)
    narrative, trace = NO.organize_section_narrative(
        claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
        presentation_profile=object(), identity=IDENTITY, llm_client=llm,
        draft=_DraftStub((unit1, unit2)), material_context=MATERIAL_CONTEXT)
    sentence = narrative.paragraphs[0].sentences[0]
    check(sentence.sentence_kind == "natural",
          f"声明了草稿出处的句子必须由**系统**判定为 natural（实测 {sentence.sentence_kind!r}）")
    check(sentence.text == natural_text
          and tuple(sentence.claim_ids) == (claim_a.claim_id, claim_b.claim_id),
          "自然句的文本与绑定必须逐字保持模型声明的内容（系统只判句类，不改文本）")
    check(trace["prose_draft"]["prose_units_offered"] == 1
          and trace["natural_sentence_count"] == 1,
          f"trace 必须留草稿层读数与自然句计数（实测 {trace.get('prose_draft')}）")
    check(trace["reorganizations"] == 0,
          "自然句必须**整句跳过**补救阶梯（⑥ 的两级都以「Claim 文本逐字在场」为前提，"
          f"送进去会逐句 fail-closed）（实测 {trace.get('reorganizations')}）")
    check(trace["prompt_version"] == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION
          and trace["rules_version"] == NO.NARRATIVE_ORGANIZER_RULES_VERSION,
          "自然路径与逐字路径必须共用同一份资产与规则版本（不得为它另开一次调用）")

    def _organize(plan, *, draft, spec_direct=None):
        return NO.organize_section_narrative(
            claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
            presentation_profile=object(), identity=IDENTITY, llm_client=_StubLlm(plan),
            draft=draft, material_context=MATERIAL_CONTEXT)

    # 同一段文本，**不**声明出处 → 逐字判据照旧拒（见 §4.2 的对照）。
    # 形似的补救对照：两条 Claim 文本首尾相接（句中句末标点）在逐字写法下走第一级补救。
    repair_plan = _plan(
        [{"text": TEXT_A + "。" + TEXT_B + "。",
          "claim_ids": [claim_a.claim_id, claim_b.claim_id], "context_binding_ids": []}],
        [_d(claim_a), _d(claim_b), _d(claim_c, "omitted", "outside_section_topic"),
         _d(claim_n, "omitted", "outside_section_topic"),
         _d(claim_m, "omitted", "outside_section_topic")])
    _n, repair_trace = _organize(repair_plan, draft=_DraftStub((unit1, unit2)))
    check(repair_trace["reorganizations"] == 1
          and repair_trace["reorganization_remedies"]
          == [NO.REORGANIZATION_SPLIT_AT_TERMINATORS],
          "对照：未声明出处的同类句子**照旧**进补救阶梯（跳过是针对声明，不是关掉补救）"
          f"（实测 {repair_trace.get('reorganizations')}）")

    # ============================================================ §3 句级来源闭合（反例）
    def _with_unit(unit_id, *, claim_ids, text=natural_text, **extra):
        sentence = {"text": text, "claim_ids": list(claim_ids),
                    "context_binding_ids": [], "prose_unit_id": unit_id, **extra}
        return _plan(
            [sentence],
            [_d(claim_a), _d(claim_b), _d(claim_c, "omitted", "outside_section_topic"),
             _d(claim_n, "omitted", "outside_section_topic"),
             _d(claim_m, "omitted", "outside_section_topic")])

    expect_error(
        lambda: _organize(_with_unit(unit2.prose_unit_id, claim_ids=[claim_a.claim_id]),
                          draft=_DraftStub((unit1, unit2))),
        NO.NarrativeOrganizerError,
        "3.1 声明一段「原子全没过门」的草稿（它根本没进输入面）必须被拒",
        needle="prose_unit_id")
    expect_error(
        lambda: _organize(_with_unit("npdu_0000000000000000", claim_ids=[claim_a.claim_id]),
                          draft=_DraftStub((unit1, unit2))),
        NO.NarrativeOrganizerError,
        "3.1 自造的 prose_unit_id 必须被拒（不得凭一个字符串换到自然写法）",
        needle="prose_unit_id")
    expect_error(
        lambda: _organize(_with_unit(unit1.prose_unit_id, claim_ids=[claim_c.claim_id]),
                          draft=_DraftStub((unit1, unit2))),
        NO.NarrativeOrganizerError,
        "3.2 借一段草稿之名声明那一行**没有**的 Claim 必须被拒"
        "（否则草稿出处就是任意 Claim 的通行证）",
        needle="借草稿行")
    expect_error(
        lambda: _organize(_with_unit(unit1.prose_unit_id, claim_ids=[claim_n.claim_id]),
                          draft=_DraftStub((unit1, unit2))),
        NO.NarrativeOrganizerError,
        "3.2 借草稿之名声明一条**由表格承载**的 Claim 同样必须被拒（输入面里根本没有它）",
        needle="借草稿行")

    # 3.3 / 3.4 走句规格层：解析层先一步拦住空 claim_ids，因此这两条判据必须自己在场。
    known = {c.claim_id: c for c in claims}
    ledger = NO.prose_ledger_claim_ids(payload)
    expect_error(
        lambda: NO._sentence_specs(
            {"sentences": [{"text": natural_text, "claim_ids": [],
                            "prose_unit_id": unit1.prose_unit_id}]},
            known_claims=known, prose_ledger=ledger),
        NO.NarrativeOrganizerError,
        "3.3 声明草稿出处却不绑定任何 Claim 必须被拒（空绑定的句子没有事实来源）",
        needle="Claim")
    expect_error(
        lambda: NO._sentence_specs(
            {"sentences": [{"text": natural_text, "claim_ids": [claim_a.claim_id],
                            "prose_unit_id": unit1.prose_unit_id,
                            NO.REPAIRED_SENTENCE_KIND_KEY: "composed"}]},
            known_claims=known, prose_ledger=ledger),
        NO.NarrativeOrganizerError,
        "3.4 系统补救产出的句子挂草稿出处必须被拒"
        "（补救过的文本不再是草稿里那一句，挂出处就是描述一件没发生过的事）",
        needle="系统句类")

    # 3.5 模型自报句类无效。
    specs = NO._sentence_specs(
        {"sentences": [{"text": natural_text, "claim_ids": [claim_a.claim_id],
                        "prose_unit_id": unit1.prose_unit_id,
                        "sentence_kind": "factual"}]},
        known_claims=known, prose_ledger=ledger)
    check(specs[0]["sentence_kind"] == "natural",
          f"模型自称 `factual` 换不掉系统判定（实测 {specs[0]['sentence_kind']!r}）")
    specs = NO._sentence_specs(
        {"sentences": [{"text": TEXT_A, "claim_ids": [claim_a.claim_id],
                        "sentence_kind": "natural"}]},
        known_claims=known, prose_ledger=ledger)
    check(specs[0]["sentence_kind"] == "composed",
          f"模型自称 `natural`（未声明出处）换不来自然写法（实测 {specs[0]['sentence_kind']!r}）")

    # ============================================================ §4 阳性对照：没有放宽
    # 4.1 空草稿层：输入面与旧修订逐字相同。
    base = NO.build_organizer_messages(
        claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
        presentation_profile=object(), identity=IDENTITY)
    same = NO.build_organizer_messages(
        claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
        presentation_profile=object(), identity=IDENTITY, prose_draft=())
    check(base == same, "空草稿层时输入面必须与旧调用方逐字相同（缺省参数不得改行为）")
    body = json.loads(base[0]["content"])
    check(body["prose_draft"] == [],
          f"空草稿层必须以空数组出现（而不是缺键）（实测 {body.get('prose_draft')!r}）")
    filled = NO.build_organizer_messages(
        claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
        presentation_profile=object(), identity=IDENTITY, prose_draft=payload)
    filled_body = json.loads(filled[0]["content"])
    check(filled_body["prose_draft"] == payload
          and {k: v for k, v in filled_body.items() if k != "prose_draft"}
          == {k: v for k, v in body.items() if k != "prose_draft"},
          "投了草稿层时，除 `prose_draft` 外的输入面必须一字不变（草稿不是新的授权面）")
    expect_error(
        lambda: _organize(_with_unit(unit1.prose_unit_id, claim_ids=[claim_a.claim_id]),
                          draft=_DraftStub(())),
        NO.NarrativeOrganizerError,
        "4.1 没有草稿层时声明出处必须被拒（空数组只有一个含义：本轮没有草稿可用）",
        needle="prose_unit_id")

    # 4.2 逐字判据没有为了「读起来顺」而被删掉。
    verbatim_free_plan = _plan(
        [{"text": natural_text, "claim_ids": [claim_a.claim_id, claim_b.claim_id],
          "context_binding_ids": []}],
        [_d(claim_a), _d(claim_b), _d(claim_c, "omitted", "outside_section_topic"),
         _d(claim_n, "omitted", "outside_section_topic"),
         _d(claim_m, "omitted", "outside_section_topic")])
    expect_error(
        lambda: _organize(verbatim_free_plan, draft=_DraftStub((unit1, unit2))),
        NS.NarrativeSchemaError,
        "4.2 同一段自然文本在不声明出处时**照旧被拒**：逐字判据没有被删掉，"
        "自然写法是一条**要声明来源**的写法，不是一份人人可用的豁免",
        needle="没有按序出现")

    # 4.3 保真判据真的在门内跑（丢断言的自然句必须被拒）。
    drop_plan = _plan(
        [{"text": "公司业务由动力电池与储能两大条线构成，销售以直销为主。",
          "claim_ids": [claim_a.claim_id, claim_b.claim_id, claim_m.claim_id],
          "context_binding_ids": [], "prose_unit_id": unit1.prose_unit_id}],
        [_d(claim_a), _d(claim_b), _d(claim_c, "omitted", "outside_section_topic"),
         _d(claim_n, "omitted", "outside_section_topic"), _d(claim_m)])
    expect_error(
        lambda: _organize(drop_plan, draft=_DraftStub((unit1, unit2))),
        NS.NarrativeSchemaError,
        "4.3 声明了出处但丢掉声明 Claim 的数字表面必须被拒（保真判据在门内真的跑）",
        needle="保真")

    # ============================================================ §5 门后入口把草稿投进去
    llm2 = _StubLlm(natural_plan)
    narrative2, trace2 = NO.build_final_narrative(
        claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
        presentation_profile=object(), identity=IDENTITY, llm_client=llm2,
        draft=_DraftStub((unit1, unit2)), material_context=MATERIAL_CONTEXT)
    check(narrative2.paragraphs[0].sentences[0].sentence_kind == "natural"
          and trace2["natural_sentence_count"] == 1,
          "门后唯一入口必须把 `draft` 一路投进组织器（否则门前草稿白写、表达基础退回 Claim 拼文）")
    check(json.loads(llm2.calls[0]["messages"][0]["content"])["prose_draft"] == payload,
          "投给模型的草稿层必须与台账构造口径逐字一致")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(1 if outcome["failed"] else 0)

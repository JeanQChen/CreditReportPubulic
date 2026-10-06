"""Eval: M930-3 返修 P0 —— 事实期间链（所引原文 → 候选 → 唯一构造器 → 结果 → 写作侧门）。

用法: python -m evals.test_m930_3_period_chain

本轮真实内容链失败的**因果起点**是这条链断了：生产从未给事实填过期间，于是 Pack 侧期间门
按「指标/事件类必须有显式期间」把每一条事实逐条排除，Writer 的 `authority_facts` 恒为 0
（材料明明在），写作侧才只能靠路径 B 编数字并被 fail-closed 拒掉。因此本模块逐条证明：

1. **期间只能来自被引材料自己的精确定位原文**：`content.text` 里显式可核验的期间被抽出来；
   多个互不包含的期间 → 歧义（不选、不猜）；抽不到 → 没有（不是"用别的东西顶替"）；
   非法日期（13 月 / 99 日）不得被当成期间；
2. **候选 → 结果逐字一致**：`FactCandidate.period` 经**唯一**构造器进入 `SupportedFact`，
   读回 `to_dict`/`from_dict` 后重算 `candidate_revision` 逐字符相等；构造器**不接受**任何
   期间旁路实参（否则"候选一套、结果另一套"就会静默发生）；
3. **生产路径 `harness.topic_runtime._adopt_facts`** 三类结果各自成立：抽到 → eligible 事实
   带该期间；歧义 → 候选在审计里 + 恰一条 rejected 决定（`period_ambiguous_in_text`）+
   零条 qualified result；抽不到 → eligible 但期间为空（交给 Pack 侧期间门，**不得**用
   `report_as_of` 或快照期末顶替）；
4. **期间要求只有一个实现**：研究侧与写作侧的判据必须逐例同值（三态：explicit / optional /
   period_unresolved），且「报告期内」这类含糊措辞仍然被拒。

公司无关：本模块没有公司代号、文件名、页码、表号或固定年份的生产字面量（断言里的年份是
**输入文本**的一部分，不是判据）；不调 LLM、不联网、不写库。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import period_extraction as PE
from harness import schema as HS
from harness import topic_runtime as TR
from harness import topic_schema as TS
from routing import schema as RS
from sections import pack_writer as PW


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _payload_bytes(text: str) -> bytes:
    """公共 payload 信封（`content.text` = 该 OutlineSpan 的精确定位原文）。"""
    return json.dumps({"content": {"text": text}}, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")


class _Resolver:
    """按 content_hash 建索引的 payload resolver（只读；与生产 `TreeMaterialPayloadResolver`
    同一约定：payload 字节是**已验**字节，调用方不读旁的字段）。"""

    def __init__(self, by_hash: dict) -> None:
        self._by_hash = dict(by_hash)

    def resolve(self, payload_ref):
        entry = self._by_hash.get(payload_ref.content_hash)
        if entry is None:
            return None
        locator, payload_bytes = entry
        return TS.ResolvedPayload(
            object_type=payload_ref.object_type,
            authority_identity=payload_ref.authority_identity,
            version=payload_ref.version, locator=locator, content_hash=payload_ref.content_hash,
            payload_bytes=payload_bytes)


def _material(text: str, *, mid: str = "m1", evidence_id: str = "ev1", page: int = 1) -> tuple:
    """一份 evidence_span 材料 + 它的 payload 字节（确定性、可重算为 authoritative）。"""
    payload_bytes = _payload_bytes(text)
    content_hash = hashlib.sha256(payload_bytes).hexdigest()
    locator = TS.EvidenceLocator(document_id="doc1", document_version="v1",
                                 section_path="s1", page=page)
    authority = TS.EvidenceAuthorityAssessment(
        evidence_id=evidence_id, document_id="doc1", document_version="v1",
        company_id="c1", is_current_document=True, is_current_set=True, page=page,
        fetched_inspected_nonempty=True, content_hash=_sha("evidence:" + evidence_id),
        verdict="authoritative", reason="", validator_version="vv1")
    source_identity = TS.authority_source_identity(authority)
    payload_ref = TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity=source_identity, version="v1",
        content_hash=content_hash, locator=locator,
        created_dependency_fingerprint=_sha("cdep"))
    material = TS.ResearchMaterial(
        material_id=mid, material_type="evidence_span", source_identity=source_identity,
        locator=locator, payload_ref=payload_ref, content_hash=content_hash,
        authority_assessment=authority)
    return material, payload_bytes, locator


def _aspect(kind: str = "financial_metric", time_scope: str = "CURRENT_AS_OF_WITH_24M_CHANGES"):
    return TS.TopicAspectRequirementSnapshot(
        aspect_id="a1", question_id="q1", topic_id="t1", requirement_text="req",
        kind=kind, producer_kind="company", execution_path="direct", required_fields=("f1",),
        coverage_rules=("required_fields_complete",), complete_set_rule="",
        evidence_requirement_ids=(TS.EvidenceRequirementRef(
            requirement_id="er1", contract_sha256=_sha("contract"),
            requirement_fingerprint=_sha("req:er1"), schema_version="1"),),
        source_policy_ref=TS.SourcePolicyRef(policy_id="sp1", policy_version="v1",
                                            content_fingerprint=_sha("policy")),
        time_scope=time_scope, display_tier="primary", content_role="subject",
        missing_policy="none", blocking_policy=(), applicability_policy=None,
        impact_scope=("subject",), output_destination="body", derived_from=(),
        business_review_status="none", contract_version="v1",
        contract_sha256=_sha("contract"), canonical_fingerprint=_sha("canonical:a1"),
        dependency_fingerprint=_sha("dep"))


def _outcome(texts, *, claim_prefix: str = "c") -> HS.ResearchOutcome:
    """一次 `run_question` 结果的最小真实形状：claim 文本逐字给定、verdict 一律 SUPPORTED。"""
    claims, citations, verdicts = [], [], []
    for i, text in enumerate(texts):
        claims.append(HS.Claim(claim_id=f"{claim_prefix}{i}", text=text, kind="fact",
                               citation_refs=[0]))
        citations.append(TS.CitationRef(ref_type="evidence", evidence_id="ev1", page_number=1))
        verdicts.append(HS.EntailmentVerdict(
            claim_id=f"{claim_prefix}{i}", citation_ids=["0"], verdict="SUPPORTED",
            reason="fixture"))
    need = RS.InformationNeed(
        need_id="n1", section_id="s1", question="q", required_evidence_types=[],
        required_source_types=[], time_scope=None, priority="P0", depends_on=[])
    answer = HS.ResearchAnswer(
        question_id="n1", answer_text=(texts[0] if texts else ""), claims=claims,
        citations=citations, completion_status=("COMPLETED" if claims else "UNRESOLVED"))
    state = HS.ResearchState(
        run_id="r1", case_id="case1", question_id="n1", company_id="c1", section_id="s1",
        original_question="q", need=need, status="COMPLETED", entailment_verdicts=verdicts,
        usage=HS.UsageLedger(rounds=1, llm_calls=1))
    return HS.ResearchOutcome(state=state, answer=answer, success=bool(claims),
                              completion_status=answer.completion_status,
                              stop_reason=("COMPLETED" if claims else "NOT_FOUND"))


def _adopt(text: str, *, page: int = 1, evidence_id: str = "ev1"):
    material, payload_bytes, locator = _material(text, page=page, evidence_id=evidence_id)
    resolver = _Resolver({material.payload_ref.content_hash: (locator, payload_bytes)})
    return TR._adopt_facts(_aspect(), _outcome([text]),
                           {evidence_id: (material,)}, resolver=resolver)


def _two_materials():
    """同一份 Evidence 下的两份 span 材料（m1 P10 / m2 P20）——真实多材料 citation 的形状。

    同一 evidence_id 才能过资格门的来源身份闭合（`citation_source_identity` 与材料
    `authority_source_identity` 必须同域同值），两份材料靠 page 精确定位区分。
    """
    m1, pw1, loc1 = _material("截至2025年12月31日，公司为部分客户提供产品质量保证，"
                              "计提预计负债 3 亿元。", mid="m1", evidence_id="ev1", page=10)
    m2, pw2, loc2 = _material("截至2025年12月31日，公司存在与某方的重大诉讼风险，"
                              "涉及金额 8 亿元。", mid="m2", evidence_id="ev1", page=20)
    return (m1, pw1, loc1), (m2, pw2, loc2)


def _multi_citation_outcome(pages: tuple[int, ...], *, refs=None):
    """一条 fact claim 引两份材料；``pages`` 给定 citation 的页序（即解析顺序）。"""
    text = "截至2025年12月31日，公司存在重大诉讼风险。"
    citations = [TS.CitationRef(ref_type="evidence", evidence_id="ev1", page_number=p)
                 for p in pages]
    if refs is None:
        refs = list(range(len(pages)))
    claim = HS.Claim(claim_id="c0", text=text, kind="fact", citation_refs=list(refs))
    verdict = HS.EntailmentVerdict(claim_id="c0", citation_ids=[str(i) for i in refs],
                                  verdict="SUPPORTED", reason="fixture")
    need = RS.InformationNeed(
        need_id="n1", section_id="s1", question="q", required_evidence_types=[],
        required_source_types=[], time_scope=None, priority="P0", depends_on=[])
    answer = HS.ResearchAnswer(question_id="n1", answer_text=text, claims=[claim],
                               citations=citations, completion_status="COMPLETED")
    state = HS.ResearchState(
        run_id="r1", case_id="case1", question_id="n1", company_id="c1", section_id="s1",
        original_question="q", need=need, status="COMPLETED", entailment_verdicts=[verdict],
        usage=HS.UsageLedger(rounds=1, llm_calls=1))
    return HS.ResearchOutcome(state=state, answer=answer, success=True,
                              completion_status="COMPLETED", stop_reason="COMPLETED")


def _adopt_multi(pages: tuple[int, ...], materials):
    (m1, pw1, loc1), (m2, pw2, loc2) = materials
    resolver = _Resolver({
        m1.payload_ref.content_hash: (loc1, pw1),
        m2.payload_ref.content_hash: (loc2, pw2)})
    return TR._adopt_facts(_aspect(), _multi_citation_outcome(pages), {"ev1": (m1, m2)},
                           resolver=resolver)


def _gate_blocks(decs, cands, materials, facts=()):
    """白盒调用 Pack 门对决定输入身份的重算（不构造 Pack，只喂它读的四个字段）。"""
    import types as _types

    from sections import pack_set as PSet
    pack = _types.SimpleNamespace(
        topic_id="t1", materials=[materials[0][0], materials[1][0]],
        fact_qualification_decisions=list(decs), facts=list(facts))
    out: list = []
    PSet._check_decision_inputs(pack, out, {c.candidate_id: c for c in cands})
    return out


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

    # -----------------------------------------------------------------
    # 1. 抽取：只认原文里显式、唯一、合法的期间
    # -----------------------------------------------------------------
    range_text = "公司  报告期  指  2025年1月 1日至  2025年12月 31日  ，下同。"
    got = PE.extract_explicit_period(range_text)
    check(got.status == PE.PERIOD_EXTRACTED, f"区间期间必须被抽出，实为 {got.status}")
    check(got.display == "2025-01-01..2025-12-31", f"区间期间规范化错误: {got.display!r}")
    check(got.literal == "2025年1月 1日至  2025年12月 31日", f"逐字切片错误: {got.literal!r}")
    check(bool(got.span) and range_text[got.span[0]:got.span[1]] == got.literal,
          "期间 literal 必须能被 span 逐字回查")
    check(got.kind == PE.PERIOD_KIND_RANGE,
          "长模式必须优先：区间不得被拆成两个更短的时点")

    for text, status, what in (
            ("公司主营业务为动力电池。", PE.PERIOD_ABSENT, "无期间措辞"),
            ("2025年13月99日完成交割。", PE.PERIOD_ABSENT, "非法日期"),
            ("", PE.PERIOD_ABSENT, "空文本")):
        got = PE.extract_explicit_period(text)
        check(got.status == status, f"{what} 必须判为 {status}，实为 {got.status}")
        check(got.display == "", f"{what} 不得给出任何期间表达，实为 {got.display!r}")

    got = PE.extract_explicit_period("截至2023年12月31日、2024年12月31日余额分别为 1、2 亿元。")
    check(got.status == PE.PERIOD_AMBIGUOUS, f"两个互不包含的期间必须判为歧义，实为 {got.status}")
    check(got.candidates == ("2023-12-31", "2024-12-31"),
          f"歧义必须给出全部候选，实为 {got.candidates}")
    check(got.display == "", "歧义时不得给出任何单一期间表达（不得任选一条）")

    # -----------------------------------------------------------------
    # 1b. 期间必须落在「值自己那句话」里（`period-extract-2`）
    #
    # 缺陷现场：整段扫描会把**后文**的年份追认给**前句**的值。下面用同一段两句话的原文，
    # 只换「值的命题句是哪一句」，正例抽得到、反例抽不到——判据不是「整段里有没有年份」，
    # 而是「值自己那句话里有没有依据」。
    # -----------------------------------------------------------------
    multi_sentence = ("报告期内公司动力电池销量增长明显。"
                      "2025年公司动力电池产能持续扩张。")

    # 正例：值的命题句**自己**带显式期间 ⇒ 抽得到，且切片落在该句内（可逐字回查）
    pos_text = "2025年公司动力电池销量为 541GWh。公司产能持续扩张。"
    pos = PE.extract_explicit_period_in_sentence(pos_text, anchor="公司动力电池销量为 541GWh")
    check(pos.status == PE.PERIOD_EXTRACTED and pos.display == "2025年度",
          f"值自己那句带显式期间时必须抽到，实为 {pos.status}/{pos.display!r}")
    check(bool(pos.span) and pos_text[pos.span[0]:pos.span[1]] == pos.literal,
          "句内抽取的 span 必须能被全文逐字回查（坐标换算不得错位）")

    # 反例：值的命题句**没有**显式期间，后文另有年份 ⇒ 不得追认
    neg = PE.extract_explicit_period_in_sentence(multi_sentence,
                                                 anchor="公司动力电池销量增长明显")
    check(neg.status == PE.PERIOD_ABSENT and neg.display == "",
          f"后文的年份不得追认前句，实为 {neg.status}/{neg.display!r}")

    # 对照（本修要挡的正是这一条）：整段口径确实会把后文年份追认给前句
    whole = PE.extract_explicit_period(multi_sentence)
    check(whole.status == PE.PERIOD_EXTRACTED and whole.display == "2025年度",
          "整段口径会追认后文年份——这是被本修在生产调用点弃用它的原因（对照，不改其原义）")

    # 定位不到值自己那句（命题不是逐字/重复出现）⇒ fail-closed，**不得**退回整段
    stray = PE.extract_explicit_period_in_sentence(multi_sentence, anchor="动力电池销量上升")
    check(stray.status == PE.PERIOD_ABSENT,
          f"定位不到值的句子时必须按「未取得显式期间」处理，实为 {stray.status}")

    # 生产路径：命题句与材料原文不同长时，取的是**命题句**的期间
    mat_text = multi_sentence
    material, payload_bytes, locator = _material(mat_text)
    resolver = _Resolver({material.payload_ref.content_hash: (locator, payload_bytes)})
    cands, decs, facts, rejected = TR._adopt_facts(
        _aspect(), _outcome(["公司动力电池销量增长明显"]), {"ev1": (material,)},
        resolver=resolver)
    check(len(facts) == 1 and facts[0].period is None,
          f"命题句无显式期间时期间必须为空（不得拿后句年份顶替），实为 "
          f"{facts[0].period if facts else None!r}")

    # -----------------------------------------------------------------
    # 2. 候选 → 结果：逐字一致 + 读回重算一致 + 无期间旁路实参
    # -----------------------------------------------------------------
    import dataclasses
    candidate = TS.build_fact_candidate(
        candidate_source_kind="topic_material", statement="2025年1月1日至2025年12月31日收入…",
        fact_type="fact", aspect_ids=("a1",), question_ids=("q1",),
        material_ids=("m1",), period="2025-01-01..2025-12-31")
    material, payload_bytes, locator = _material("x")
    decision = TS.build_qualification_decision(
        candidate, verdict="eligible", input_identity_digest=_sha("id"),
        input_source_identity=material.source_identity, input_locator_digest=_sha("loc"),
        input_payload_digest=_sha("pay"))
    fact = TS.build_supported_fact("fact-tr-1", candidate, decision, text=candidate.statement,
                                   fact_type="fact", citation_refs=(),
                                   source_authority=material.authority_assessment)
    check(fact.period == candidate.period,
          f"结果期间必须逐字复制候选期间：{fact.period!r} != {candidate.period!r}")
    check(fact.candidate_revision == candidate.compute_revision(),
          "结果回指的 candidate_revision 必须等于候选内容再算一遍的值")
    round_trip = TS.SupportedFact.from_dict(fact.to_dict())
    check(round_trip.period == fact.period and round_trip.fact_id == fact.fact_id,
          "读回（to_dict → from_dict）后期间与身份必须逐字不变")
    rebuilt = TS.FactCandidate.from_dict(candidate.to_dict())
    check(rebuilt.compute_revision() == candidate.candidate_revision,
          "读回的候选再算一遍 revision 必须与写入值逐字相等（期间参与内容身份）")
    period_params = {p for p in inspect.signature(TS.build_supported_fact).parameters
                     if p in ("period", "scope", "report_as_of", "as_of")}
    check(not period_params,
          f"唯一构造器不得接受期间/基准日旁路实参，实为 {sorted(period_params)}")

    # -----------------------------------------------------------------
    # 3. 生产路径：三类结果
    # -----------------------------------------------------------------
    cands, decs, facts, rejected = _adopt(range_text)
    check(len(cands) == 1 and len(decs) == 1 and len(facts) == 1,
          f"原文含显式期间 → 恰一条候选/决定/事实，实为 {len(cands)}/{len(decs)}/{len(facts)}")
    check(facts and facts[0].period == "2025-01-01..2025-12-31",
          f"事实期间必须来自所引原文，实为 {facts[0].period if facts else None!r}")
    check(facts and facts[0].text == range_text, "事实文本不得被期间抽取改写")
    check(facts and facts[0].period == cands[0].period,
          "结果与候选的期间必须一致（单向链不得出现两套期间）")

    ambiguous_text = "截至2023年12月31日、2024年12月31日余额分别为 1、2 亿元。"
    cands, decs, facts, rejected = _adopt(ambiguous_text)
    check(len(cands) == 1 and len(facts) == 0,
          f"期间歧义 → 候选保留、零条 qualified result，实为 {len(cands)}/{len(facts)}")
    check(cands and cands[0].period is None,
          "期间歧义时候选不得带任何一个候选期间（不得任选一条）")
    check([d.verdict for d in decs] == ["rejected"]
          and decs[0].rejection_reason == "period_ambiguous_in_text",
          f"期间歧义必须产生恰一条 typed rejection，实为 "
          f"{[(d.verdict, d.rejection_reason) for d in decs]}")
    check(any("period_ambiguous_in_text" in r for r in rejected),
          "期间歧义必须留在拒绝诊断里（不静默丢弃）")

    undated_text = "公司主营业务为动力电池。"
    cands, decs, facts, rejected = _adopt(undated_text)
    check(len(facts) == 1 and facts[0].period is None,
          f"抽不到期间 → 事实仍在（期间为空），实为 {facts[0].period if facts else None!r}")
    check(all(d.verdict == "eligible" for d in decs),
          "抽不到期间**不是**资格拒绝（排除点唯一：Pack 侧期间门）")
    check("report_as_of" not in json.dumps([f.to_dict() for f in facts], ensure_ascii=False),
          "事实载荷里不得出现 report_as_of 顶替期间")

    # 负向对照：**读不到** payload 原文时不得凭事实文本里"看得见的年份"补期间。
    # （期间只能来自被引材料的精确定位原文；读不到就是没取得，绝不能转去解析 claim 文本。）
    material, payload_bytes, locator = _material(range_text)
    blind = _Resolver({})
    cands, decs, facts, rejected = TR._adopt_facts(
        _aspect(), _outcome([range_text]), {"ev1": (material,)}, resolver=blind)
    check(len(facts) == 1 and facts[0].period is None,
          f"读不到原文时期间必须为空（不得从事实文本反推），实为 "
          f"{facts[0].period if facts else None!r}")

    # 引用歧义（同一父 Evidence 的两份 span、citation 没有页号）→ 不构候选（原有行为保持）
    text = "截至2025年12月31日余额为 1 亿元。"
    m1, b1, loc1 = _material(text, mid="m1", page=1)
    m2, b2, loc2 = _material(text, mid="m2", page=2)
    resolver = _Resolver({m1.payload_ref.content_hash: (loc1, b1),
                          m2.payload_ref.content_hash: (loc2, b2)})
    outcome = _outcome([text])
    outcome.answer.citations[0] = TS.CitationRef(ref_type="evidence", evidence_id="ev1")
    cands, decs, facts, rejected = TR._adopt_facts(
        _aspect(), outcome, {"ev1": (m1, m2)}, resolver=resolver)
    check(not cands and not decs and not facts,
          "引用在多个 span 间歧义时不得构成候选（不得任选一条）")
    check(any("citation_ambiguous_across_spans" in r for r in rejected),
          f"引用歧义必须留下 typed 原因，实为 {rejected}")

    adopt_params = set(inspect.signature(TR._adopt_facts).parameters)
    check(not {"report_as_of", "as_of", "snapshot_date"} & adopt_params,
          f"采纳路径不得接受报告日/快照期末旁路，实为 {sorted(adopt_params)}")

    # -----------------------------------------------------------------
    # 4. 期间要求：唯一实现 + 三态 + 含糊措辞仍被拒
    # -----------------------------------------------------------------
    check((PW.PERIOD_REQUIREMENT_EXPLICIT, PW.PERIOD_REQUIREMENT_OPTIONAL,
           PW.PERIOD_UNRESOLVED_REASON)
          == (TS.PERIOD_REQUIREMENT_EXPLICIT, TS.PERIOD_REQUIREMENT_OPTIONAL,
              TS.PERIOD_UNRESOLVED_REASON),
          "研究侧与写作侧的期间要求三态常量必须是同一份值")
    check(PW._PERIOD_BEARING_ASPECT_KINDS == TS.PERIOD_BEARING_ASPECT_KINDS,
          "必须带显式期间的 aspect 类型集合不得有两份定义")

    undated_fact = dataclasses.replace(fact, period=None)
    probe = (
        (("financial_metric",), ("CURRENT_AS_OF_WITH_24M_CHANGES",)),
        (("event_set",), ("RECENT_24M_WITH_OPEN_TAIL",)),
        (("single_judgment",), ("STRUCTURAL_5Y_CURRENT",)),
        (("single_judgment",), ("",)),
        (("fact_set",), ("THREE_YEARS_PLUS_LATEST",)),
    )
    for kinds, scopes in probe:
        left = PW._period_requirement(undated_fact, kinds, scopes)
        right = TS.period_requirement(
            fact_type=str(getattr(undated_fact, "fact_type", "") or ""),
            has_value_identity=getattr(undated_fact, "value_identity", None) is not None,
            aspect_kinds=kinds, time_scopes=scopes)
        check(left == right,
              f"期间要求必须同值：aspect={kinds}/scope={scopes} → {left!r} != {right!r}")
    check(TS.period_requirement(fact_type="fact", has_value_identity=False,
                               aspect_kinds=("financial_metric",), time_scopes=("",))
          == TS.PERIOD_REQUIREMENT_EXPLICIT,
          "指标类 aspect 没有显式期间必须要求显式（原有排除行为保持）")
    check(TS.period_requirement(fact_type="fact", has_value_identity=False,
                               aspect_kinds=("single_judgment",),
                               time_scopes=("STRUCTURAL_5Y_CURRENT",))
          == TS.PERIOD_REQUIREMENT_OPTIONAL,
          "结构性 aspect 的期间必须是可选（描述性事实本就不是时点量）")
    check(TS.period_requirement(fact_type="fact", has_value_identity=False,
                               aspect_kinds=("single_judgment",), time_scopes=("",))
          == TS.PERIOD_UNRESOLVED_REASON,
          "无法判定事实类型必须 fail-closed 为 period_unresolved")
    check(bool(PW.NS.vague_period_hits("报告期内公司主营业务未发生变化。")),
          "「报告期内」这类含糊期间措辞必须仍被判为含糊（不得因本轮修复放行）")
    check(TS.fact_explicit_period(undated_fact) == "",
          "事实自己没有期间时必须返回空（不得从旁路字段推断）")

    # -----------------------------------------------------------------
    # 5. 多材料 citation 的资格决定 digest 必须可由**声明的输入**重算
    # -----------------------------------------------------------------
    # 真实 run r2 实证：`company_legal_risks` 的 Pack set 被 `pack_set._check_decision_inputs`
    # 以 `decision_input_identity_mismatch` 挡住三条。根因是决定自带的三个 digest 按
    # **citation 解析顺序**派生，而门按决定声明的 `input_material_ids`（排序）重算；单材料
    # 时两者恒等，一旦一条 claim 引到两份材料就分叉。本段固定"按声明的规范序"这一口径：
    # 正例（两种 citation 顺序 → 同一 digest、零 block）+ 去重 + 反例（篡改 digest 仍须 block）。
    materials = _two_materials()
    sorted_pages = (10, 20)   # 与 sorted(material_ids)=(m1, m2) 一致
    reversed_pages = (20, 10)

    cands_s, decs_s, facts_s, rej_s = _adopt_multi(sorted_pages, materials)
    cands_r, decs_r, facts_r, rej_r = _adopt_multi(reversed_pages, materials)
    check(len(decs_s) == 1 and len(decs_r) == 1 and len(facts_s) == 1 and len(facts_r) == 1,
          f"一条引两份材料的 fact claim 必须产出唯一决定与唯一事实，实为 "
          f"{len(decs_s)}/{len(decs_r)} 决定、{len(facts_s)}/{len(facts_r)} 事实，诊断 {rej_s} {rej_r}")

    if decs_s and decs_r:
        d_s, d_r = decs_s[0], decs_r[0]
        check(tuple(d_s.input_material_ids) == tuple(d_r.input_material_ids) == ("m1", "m2"),
              f"决定声明的输入材料必须是规范序（排序去重），实为 "
              f"{d_s.input_material_ids!r} / {d_r.input_material_ids!r}")
        check(d_s.input_locator_digest == d_r.input_locator_digest
              and d_s.input_payload_digest == d_r.input_payload_digest
              and d_s.input_identity_digest == d_r.input_identity_digest,
              "citation 顺序不得改变三个输入 digest（必须只由声明的输入集派生）")

        blocks_s = _gate_blocks(decs_s, cands_s, materials, facts_s)
        blocks_r = _gate_blocks(decs_r, cands_r, materials, facts_r)
        check(not blocks_s and not blocks_r,
              f"两种 citation 顺序都必须过 Pack 门，实为 "
              f"{[b.reason for b in blocks_s]} / {[b.reason for b in blocks_r]}")

        # 去重：同一份材料被引两次 → 仍是一条输入，digest 仍可重算
        dup_outcome = _multi_citation_outcome(reversed_pages, refs=[0, 1, 1])
        (m1, pw1, loc1), (m2, pw2, loc2) = materials
        dup_resolver = _Resolver({m1.payload_ref.content_hash: (loc1, pw1),
                                  m2.payload_ref.content_hash: (loc2, pw2)})
        dup = TR._adopt_facts(_aspect(), dup_outcome,
                              {"ev1": (m1, m2)}, resolver=dup_resolver)
        dup_cands, dup_decs = dup[0], dup[1]
        check(len(dup_decs) == 1
              and tuple(dup_decs[0].input_material_ids) == ("m1", "m2"),
              f"重复引用同一材料必须去重为一条输入，实为 "
              f"{[tuple(d.input_material_ids) for d in dup_decs]}")
        dup_blocks = _gate_blocks(dup_decs, dup_cands, materials, dup[2])
        check(not dup_blocks,
              f"重复引用后 digest 仍须可重算，实为 {[b.reason for b in dup_blocks]}")

        # 反例：门自己必须继续 fail-closed —— 篡改任一 digest 仍须 block
        for field in ("input_locator_digest", "input_payload_digest", "input_identity_digest"):
            forged = dataclasses.replace(decs_s[0], **{field: "0" * 64})
            forged_blocks = _gate_blocks([forged], cands_s, materials, facts_s)
            check(any(b.reason == "decision_input_identity_mismatch" for b in forged_blocks),
                  f"篡改 {field} 必须仍被门挡住（不得因本轮修复而静默放行），"
                  f"实为 {[b.reason for b in forged_blocks]}")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    result = main()
    for d in result["details"]:
        print(d)
    print(f"passed={result['passed']} failed={result['failed']} skipped={result['skipped']}")
    sys.exit(1 if result["failed"] else 0)

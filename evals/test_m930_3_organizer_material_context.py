"""Eval: M930-3 指令「打通真实写作请求」P1-d —— 门后组织器的**只读材料正文依据面**（`orgmc-1`）。

用法: python -m evals.test_m930_3_organizer_material_context

**本模块要证的那一件事。** 门后组织器此前手里只有「草稿文本 + 逐原子 `kept`/`dropped` 台账 +
材料 ID」，**没有材料正文**。于是「照材料原文的用词与语序改写这一段草稿」在模型那一侧不可执行：
它只能拿草稿自己的措辞，或者退回去把 Claim 文本一条条重新拼一遍——而后者正是写法一要拦的东西。
本批给它一份**只读**依据面：草稿**点名过的**成员的材料正文（出处身份 + exact 定位 + 正文 + 形态）。
本模块逐条钉住这份依据面的**边界**：它给的是**表达**，不是**事实**。

覆盖（每条对应一个具体缺陷）：

* §1 正向（公司节，Pack 材料）：**真的发出的那次请求**（解码 `messages[0]["content"]`，不是核
      prompt、不是核解析器）里，`material_context` 恰有一行，行的成员就是台账点名的那个，
      `text` 逐字等于已解析的读视图，出处身份 / exact 定位 / payload 哈希 / 读视图指纹 / 形态
      五项都在行里；
* §2 **有界**：清单里没被点名的成员**不得**出现在请求里的任何位置（只投点名过的成员，
      投整份清单会让输入面从 1 行涨到本节全部材料条数，并放大「照别处材料另写一句」的诱因）；
* §3 **财务天然空集**（纯 `FinancialFactPack`）：草稿单元声明的是权威事实出处
      （`source_fact_refs`）而不是 Pack 材料（`source_member_refs`）⇒ 点名的成员为空 ⇒
      投出空 `rows`，**不**为凑非空输入伪造 `ResearchMaterial`，也不因此 fail-closed；
* §4 四条**反例**（各带一个类型化原因，逐条断言原因名，不是只断言「抛了」）：
      4.1 台账点名了清单里没有的成员 → `declared_member_not_in_manifest`；
      4.2 成员在清单里、但已解析上下文里没有它的正文 → `declared_member_not_in_context`；
      4.3 上下文里有一份 **同 `member_ref`、正文不同、指纹自洽重算** 的材料
          （`material_content_fingerprint` 与清单**相同**，只有 `reading_view_fingerprint` 不同）
          → `member_identity_mismatch`。这条是本节最要紧的反例：任何只比 `member_ref` 与
          「正文非空」的实现都会放它过，于是模型照着一份**没经过本届两道门**的材料写句子；
      4.4 清单读不到 `entries`（没有 `wmm-2` 清单却点了名）→ `declared_member_not_in_manifest`；
* §5 规则面：版本常量逐字为 `orgmc-1`、规则名集合是封闭的三条、投给模型的 `rules` 里
      **有**这条纪律（给出材料却不给边界，等于把越权的诱因直接递到模型面前）、读数进 trace
      （`rows` / `requested_members` / `identity_checks` 三件事分列，空表时 `identity_checks`
      与 `rows` 一起为 0，不是「查过了没查」）；
* §6 **只读不是授权（可执行）**：材料正文里有一个数字、而这一句声明的 Claim 文本里没有它——
      照材料写出来的那一句**必须被门拒**；把那个数字删掉、其余一字不动，同一份计划**必须被接受**。
      这一对正反例才排除了「投材料正文等于放宽表面核验」这种读法；只做反例，会得到「什么都被拒」
      的弱结论。
* §7 **一条原子、两处表达**（`npr-1` 之后，组织器仍**不重建**）：同一条候选在两段草稿里各出现
      一次、两段各指**自己**那份材料时，组织器必须照旧收到**两段**——台账两行（共享的
      `claim_id` 出现在两行的 `kept` 原子里）、材料正文两行，且两处表达都能写进正文。这一节是
      **输入面读数**：`norg-8` 的判定集一个字没动，改的是写入侧允许的草稿形态。

全部离线、确定性：不读库、不连网、不调真实 LLM、不写任何文件。
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
IDENTITY = {"task_id": "task-1", "section_id": "company",
            "section_draft_id": "draft-1", "draft_revision": "rev-1"}

FP_REAL = "a" * 64
FP_OTHER = "b" * 64

#: 材料正文。它是**抽取式原文**：模型改写时照它的用词与语序写。
MATERIAL_TEXT = "报告期内，公司动力电池产能利用率保持较高水平，产销规模稳步扩大。"
#: 只出现在材料正文里的**高风险表面**（任何 Claim 文本里都没有）：§6 拿它做「只读不是授权」的
#: 反例。选「报告期内」不是随手取的：它逐字在材料里、是**期间**这一类高风险表面，而且
#: 「照材料原文用词写」恰恰是它天然会溜进句子的方式——正因为如此，只投材料正文而不发授权
#: 才需要被可执行地钉住。
MATERIAL_ONLY_SURFACE = "报告期内"
#: 清单里**第二份**材料的正文：它在清单里，但**不在**本节草稿的点名之列 ⇒ 一个字都不该投出去。
OTHER_TEXT = "公司海外业务收入占比较上年下降，主要受汇率波动影响。"
#: 伪造者写的正文：与真实正文完全不同，**指纹按伪造内容自洽重算**。
FORGED_TEXT = "本节所述内容均以公开材料为准，不构成任何投资建议。"

CLAIM_TEXT = "公司动力电池产能利用率保持较高水平"
CLAIM_TEXT_2 = "公司产销规模稳步扩大"

#: §7 的两段草稿：**同一条原子**（`CLAIM_TEXT`）在两份材料里各写一遍，两处措辞不同——一段
#: 还多带自己那半事实（`CLAIM_TEXT_2`）。`npr-1` 允许这种形态，代价是每次出现都要与这条候选
#: 自己的支撑边核对材料身份；门后组织器因此要收到**两行**台账与**两份**材料正文。
EXPR_A = "公司动力电池产能利用率保持较高水平，产销规模稳步扩大。"
EXPR_B = "公司动力电池产能利用率保持较高水平。"


def _payload_ref(authority_identity: str, content_hash: str) -> dict:
    """规范 `MaterialPayloadRef` dict（与生产同形，不做形态替身）。"""
    return TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity=authority_identity, version="v1",
        content_hash=content_hash,
        locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                                   section_path="s1", page=3),
        created_dependency_fingerprint="d" * 64).to_dict()


def _locator_ref() -> dict:
    return NS.char_range_locator("evidence:ev-1", 3, 40)


def _resolved(*, reading_view: str = MATERIAL_TEXT,
              source_identity: str = "evidence:ev-1",
              pack_id: str = "pack-1", material_id: str = "m-1",
              material_content_fingerprint: str = FP_REAL,
              qualification: dict | None = None) -> MC.ResolvedWriterMaterial:
    """已解析材料：指纹由 `create` 从正文确定性重算（与生产同一入口）。"""
    return MC.ResolvedWriterMaterial.create(
        member_ref=NS.manifest_member_ref(pack_id, material_id), topic_id=TOPIC_A,
        pack_id=pack_id, material_id=material_id, material_type="evidence_span",
        research_material_disposition_id=f"rmd-{material_id}", source_identity=source_identity,
        provenance_identity=f"prov-{material_id}", locator_ref=_locator_ref(),
        payload_ref=_payload_ref(source_identity, FP_REAL), payload_hash=FP_REAL,
        content_hash=FP_REAL, material_content_fingerprint=material_content_fingerprint,
        reading_view=reading_view, content_qualification=qualification)


def _entry(material: MC.ResolvedWriterMaterial) -> NS.WriterMaterialManifestEntry:
    """清单成员：身份字段逐项取自**已解析材料**，因此两侧默认逐字相等。"""
    return NS.WriterMaterialManifestEntry.create(
        pack_id=material.pack_id, material_id=material.material_id,
        research_material_disposition_id=material.research_material_disposition_id,
        source_identity=material.source_identity,
        provenance_identity=material.provenance_identity,
        material_content_fingerprint=material.material_content_fingerprint,
        topic_id=material.topic_id, material_type=material.material_type,
        payload_ref=dict(material.payload_ref), locator_ref=dict(material.locator_ref),
        payload_hash=material.payload_hash,
        reading_view_fingerprint=material.reading_view_fingerprint)


def _context(*materials: MC.ResolvedWriterMaterial) -> MC.WriterMaterialContext:
    return MC.WriterMaterialContext.create(
        task_id="t-1", section_id="company", pack_set_fingerprint="e" * 64,
        materials=tuple(materials))


def _citation(evidence_id: str) -> HS.CitationRef:
    return HS.CitationRef(ref_type="evidence", evidence_id=evidence_id)


def _candidate(text: str, *, index: int) -> NS.ClaimCandidate:
    return NS.ClaimCandidate.create(
        draft_revision="rev-1", task_id="task-1", section_id="company", company_id="c-1",
        report_as_of="2024-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
        claim_text=text, fact_type="fact")


def _claim(candidate: NS.ClaimCandidate, *, index: int) -> SS.SectionClaim:
    question_ids = (f"q-{index}",)
    refs = (_citation("evidence:ev-1"),)
    claim_id = SS.derive_claim_id("fact", TOPIC_A, question_ids, candidate.claim_text, refs,
                                  candidate.candidate_id, "rev-1", (f"asb-{index}",))
    return SS.SectionClaim(
        claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
        topic_id=TOPIC_A, question_ids=question_ids, text=candidate.claim_text,
        claim_type="fact", citation_refs=refs,
        claim_candidate_id=candidate.candidate_id,
        claim_candidate_revision="rev-1", accepted_binding_ids=(f"asb-{index}",))


class _Draft:
    """`SectionDraft` 在组织器眼里的**最小形状**：它只读 `natural_prose_draft` 与
    `material_manifest`（见 `prose_draft_ledger` / `organizer_material_context`）。
    草稿单元本身是真的 `NS.NaturalProseDraftUnit`，不是替身。"""

    def __init__(self, *, units, manifest) -> None:
        self.natural_prose_draft = tuple(units)
        self.material_manifest = manifest


class _StubLlm:
    """按脚本返回结构化 `PW.NarrationResult` 的 stub；没有任何工具/检索入口。"""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def narrate(self, *, messages, system, prompt_version, model_policy):
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy,
                           "messages": messages, "system": system})
        if not self.responses:
            raise AssertionError("stub LLM 被超额调用（应当 fail-closed 而不是继续重试）")
        item = self.responses.pop(0)
        return PW.NarrationResult(
            text=item if isinstance(item, str) else json.dumps(item, ensure_ascii=False),
            call_id=f"call-{len(self.calls)}", model=PW.MODEL_POLICY_STUB,
            prompt_version=prompt_version, status="ok",
            input_tokens=128, output_tokens=64, latency_ms=1, finish_reason="stop")


def _sent_payload(llm: _StubLlm, index: int = 0) -> dict:
    """**解码真发出去的那次请求**（不是核 prompt 资产、不是核解析器）。"""
    return json.loads(llm.calls[index]["messages"][0]["content"])


def _plan(sentences, dispositions):
    return {"paragraphs": [{"sentences": list(sentences)}],
            "claim_dispositions": list(dispositions)}


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

    def check_eq(actual, expected, msg: str) -> None:
        check(actual == expected, f"{msg}（实得 {actual!r}，期望 {expected!r}）")

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
    real = _resolved()
    #: 清单里的**第二份**材料：正文与真实材料完全不同，用来证有界性（它没被点名 ⇒ 一个字都不投）。
    other = _resolved(reading_view=OTHER_TEXT, source_identity="evidence:ev-2", pack_id="pack-2",
                      material_id="m-2", material_content_fingerprint=FP_OTHER)
    manifest = NS.WriterMaterialManifest.create(members=(_entry(real), _entry(other)))
    context = _context(real, other)
    member_ref = real.member_ref
    other_ref = other.member_ref
    check(member_ref != other_ref, "前置条件：夹具的两份材料身份必须不同")
    # 有界性反例（§2）只有在清单**确实含有一个未被点名**的成员时才成立。
    check_eq(len(manifest.entries), 2, "前置条件：清单有 2 个成员，本节只点名其中 1 个")
    check_eq(tuple(e.member_ref for e in manifest.entries), (member_ref, other_ref),
             "前置条件：未点名的那个成员**在**清单里（否则 §2 是空断言）")

    cand_a, cand_b = _candidate(CLAIM_TEXT, index=1), _candidate(CLAIM_TEXT_2, index=2)
    claim_a, claim_b = _claim(cand_a, index=1), _claim(cand_b, index=2)
    claims = (claim_a, claim_b)

    def _unit(unit_id: str, *, member_refs=(), fact_refs=(), atoms,
              member_ref: str | None = None, text: str = MATERIAL_TEXT,
              index: int = 0) -> NS.NaturalProseDraftUnit:
        # `create` 的出处轴是**互斥**的（`narr-8`）：这里按需要二选一。
        # `member_ref` / `text` / `index` 三个缺省值与本文档其余小节逐字相同；§7 的多
        # occurrence 场景需要让**第二段**草稿指到它自己的那份材料上，因此它们可显式传入
        # ——夹具不能替写入侧把两段草稿都指回同一份材料（那正是「错来源」）。
        kwargs: dict = {"index": index, "draft_revision": "rev-1", "section_id": "company",
                        "text": text, "atom_candidate_ids": list(atoms)}
        if member_refs:
            kwargs["source_member_refs"] = [member_ref or NS.manifest_member_ref("pack-1", "m-1")]
        if fact_refs:
            kwargs["source_fact_refs"] = [NS.fact_provenance_ref("financial_fact_pack",
                                                                 "ffp-1", "fact-1")]
        return NS.NaturalProseDraftUnit.create(**kwargs)

    company_unit = _unit("pu-1", member_refs=True, atoms=[cand_a.candidate_id,
                                                          cand_b.candidate_id])
    company_draft = _Draft(units=[company_unit], manifest=manifest)
    #: 财务形态：单元声明的是权威事实出处（`source_fact_refs`），Pack 材料清单**合法为空**。
    fin_unit = _unit("pu-fin", fact_refs=True, atoms=[cand_a.candidate_id,
                                                      cand_b.candidate_id])
    fin_draft = _Draft(units=[fin_unit], manifest=None)

    legal_plan = _plan(
        [{"text": f"{CLAIM_TEXT}，同时{CLAIM_TEXT_2}。",
          "claim_ids": [claim_a.claim_id, claim_b.claim_id]}],
        [{"claim_id": claim_a.claim_id, "disposition": "selected", "reason_code": None},
         {"claim_id": claim_b.claim_id, "disposition": "selected", "reason_code": None}])
    dispositions = legal_plan["claim_dispositions"]

    def _organize(*, draft, material_context, plan=None, llm=None):
        return NO.organize_section_narrative(
            claims=claims, accepted_context_bindings=(), unresolved=(),
            writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
            llm_client=llm if llm is not None else _StubLlm(plan),
            draft=draft, material_context=material_context)

    # ============================================================ §1 正向（公司节）
    llm = _StubLlm(legal_plan)
    narrative, trace = _organize(draft=company_draft, material_context=context, llm=llm)
    check_eq(len(llm.calls), 1, "组织器恰发起一次调用")
    sent = _sent_payload(llm)
    block = sent.get("material_context")
    check(isinstance(block, dict), "发出去的请求里必须带 `material_context` 块（本次新增的依据面）")
    check_eq(block.get("policy_version"), "orgmc-1", "依据面的版本逐字为 `orgmc-1`")
    check(block.get("readonly") is True,
          "依据面必须自报 `readonly: true`：它是只读的依据，不是可引用的对象面")
    rows = block.get("rows")
    check_eq(len(rows), 1, "只投台账**点名过的**成员：本次点名 1 个，因此恰 1 行")
    row = rows[0]
    check_eq(row.get("member_ref"), member_ref, "行的成员身份 = 台账点名的那一个")
    check_eq(row.get("text"), MATERIAL_TEXT, "行的正文逐字等于已解析的读视图（不是摘要、不是转述）")
    check_eq(row.get("locator_ref"), real.locator_ref,
             "行带 **exact 定位**（`loc-1` locator），读者据此可回查这一行出自哪一处")
    check_eq(row.get("payload_hash"), real.payload_hash, "行带 payload 哈希（材料版本可核）")
    check_eq(row.get("reading_view_fingerprint"), real.reading_view_fingerprint,
             "行带读视图指纹（正文本身可核）")
    check_eq(row.get("source_identity"), real.source_identity, "行带权威来源身份")
    check("content_qualification" in row,
          "行必须带内容形态读法：勾选表单行与普通叙述材料在这一点上分开")
    check_eq(row.get("topic_id"), TOPIC_A, "行带主题身份（组织线索与材料身份同源）")
    # 形状是**封闭键序**：多一个键即说明本模块自己另加了一列（口径漂移的常见形态）。
    check_eq(tuple(row), NO._ORGANIZER_MATERIAL_ROW_KEYS,
             "材料行的键集与键序逐项等于登记的那一份（不得临时加列）")

    # `narrative` / `trace` 本身仍是同一份 narr-8 产物：本批**不改输出面**。
    check_eq(NS.NARRATIVE_SCHEMA_VERSION, "narr-8",
             "本批不改 wire：仍是 narr-8（依据面是输入侧政策，不是输出字段）")
    check_eq(len(narrative.paragraphs), 1, "正向样本真的产出了一段正文（不是空产物）")
    check_eq(trace.get("organizer_material_context"),
             {"policy_version": "orgmc-1", "requested_members": 1, "rows": 1,
              "identity_checks": 1},
             "读数三件事分列进 trace：点名数 / 投出行数 / 逐成员核身份次数")

    # ============================================================ §2 有界：未被点名的成员不投
    joined = json.dumps(sent, ensure_ascii=False)
    check_eq(joined.count(other_ref), 0,
             "**未被台账点名**的成员不得出现在请求里的任何位置（只投点名过的成员）")
    check(OTHER_TEXT not in joined,
          "未被点名的成员的**正文**一个字都不得出现在请求里（有界性：投整份清单会把输入面"
          "从 1 行涨到本节全部材料条数）")
    no_rows = NO.build_organizer_messages(
        claims=claims, accepted_context_bindings=(), unresolved=(), writing_spec=object(),
        presentation_profile=object(), identity=IDENTITY)
    check_eq(json.loads(no_rows[0]["content"])["material_context"]["rows"], [],
             "不传 `material_context_rows` 时投出的就是空表（默认值不会悄悄变回整份清单）")
    check_eq(block.get("rows")[0]["member_ref"], member_ref,
             "有界性断言的正向对照：点名过的那个成员**在**行里")

    # 内容形态随正文一起走：勾选表单行的形态必须是行的字段，而不是让模型从正文自己猜。
    qualified = _resolved(qualification={"kind": "text"})
    q_manifest = NS.WriterMaterialManifest.create(members=(_entry(qualified),))
    q_draft = _Draft(units=[company_unit], manifest=q_manifest)
    q_llm = _StubLlm(legal_plan)
    _organize(draft=q_draft, material_context=_context(qualified), llm=q_llm)
    q_row = _sent_payload(q_llm)["material_context"]["rows"][0]
    check_eq(q_row["content_qualification"], {"kind": "text"},
             "内容形态读法随正文一起到达（不是调用方另算的一份）")

    # ============================================================ §3 财务：天然空集
    fin_llm = _StubLlm(legal_plan)
    _, fin_trace = _organize(draft=fin_draft, material_context=None, llm=fin_llm)
    fin_block = _sent_payload(fin_llm)["material_context"]
    check_eq(fin_block["rows"], [],
             "纯 FinancialFactPack 的草稿单元声明的是权威事实出处 ⇒ 点名成员为空 ⇒ 投空表；"
             "**不**为凑出非空输入而伪造 ResearchMaterial")
    check_eq(fin_trace.get("organizer_material_context"),
             {"policy_version": "orgmc-1", "requested_members": 0, "rows": 0,
              "identity_checks": 0},
             "空表的读数必须把「点名 0 个」与「核身份 0 次」分开记（不是「查过了没查」）")
    check_eq(fin_trace.get("prose_draft", {}).get("prose_units_offered"), 1,
             "财务节的草稿层**在场**（它不是「没有草稿」）：空的是材料正文轴，不是草稿轴")

    # 没有草稿层时（组织器仍要为 Claim 写正文）：同样空表，且不得 fail-closed。
    nodraft_llm = _StubLlm(legal_plan)
    _, nodraft_trace = _organize(draft=None, material_context=context, llm=nodraft_llm)
    check_eq(_sent_payload(nodraft_llm)["material_context"]["rows"], [],
             "没有草稿层 ⇒ 没有点名的成员 ⇒ 空表（不是「材料丢了」，也不是失败）")
    check_eq(nodraft_trace["organizer_material_context"]["requested_members"], 0,
             "同上：点名数为 0，与「投了 0 行」一起如实报读")

    # ============================================================ §4 反例（逐条带原因）
    reasons = NO.ORGANIZER_MATERIAL_CONTEXT_REASONS
    check_eq(reasons, ("declared_member_not_in_manifest", "declared_member_not_in_context",
                       "member_identity_mismatch"),
             "失败原因是**封闭三元组**（每条 fail-closed 恰好一个，可被调用方逐条认读）")
    check_eq(NO.ORGANIZER_MATERIAL_CONTEXT_VERSION, "orgmc-1",
             "依据面版本是登记的字面量（不是从别处推导出来的值）")

    # 4.1 台账点名了清单里**没有**的成员。
    expect_error(
        lambda: NO.organizer_material_context(
            prose_payload=[{"material_member_refs": [other_ref]}],
            manifest=NS.WriterMaterialManifest.create(members=(_entry(real),)),
            material_context=context),
        NO.NarrativeOrganizerError, "点名清单里没有的成员必须 fail-closed",
        needle=reasons[0])
    # 4.2 成员在清单里，但已解析上下文里没有它的正文。
    expect_error(
        lambda: NO.organizer_material_context(
            prose_payload=[{"material_member_refs": [member_ref]}],
            manifest=manifest, material_context=_context(other)),
        NO.NarrativeOrganizerError, "清单成员缺已解析正文必须 fail-closed",
        needle=reasons[1])
    # 4.2b 清单非空却根本没有上下文（不得把「只有 ID 的出处」当成「模型看过正文」）。
    expect_error(
        lambda: NO.organizer_material_context(
            prose_payload=[{"material_member_refs": [member_ref]}],
            manifest=manifest, material_context=None),
        NO.NarrativeOrganizerError, "点名了成员却没有材料正文上下文必须 fail-closed",
        needle=reasons[1])
    # 4.3 **同 member_ref、正文不同、指纹自洽重算**的伪造上下文。
    #   注意 `material_content_fingerprint` 与清单**故意相同**：只比它、或只比 member_ref，
    #   都拦不住这次伪造——唯一能拦住的是读视图指纹。
    forged = _resolved(reading_view=FORGED_TEXT, material_content_fingerprint=FP_REAL)
    check_eq(forged.member_ref, member_ref, "前置条件：伪造材料的 member_ref 与真实材料相同")
    check_eq(forged.material_content_fingerprint, real.material_content_fingerprint,
             "前置条件：伪造材料在**内容指纹**上与真实材料相同（不比读视图就看不出来）")
    check(forged.reading_view_fingerprint != real.reading_view_fingerprint,
          "前置条件：伪造材料的读视图指纹必与真实材料不同（正文真的换过）")
    expect_error(
        lambda: NO.organizer_material_context(
            prose_payload=[{"material_member_refs": [member_ref]}],
            manifest=manifest, material_context=_context(forged)),
        NO.NarrativeOrganizerError, "同 member_ref 但正文不同（指纹自洽重算）必须 fail-closed",
        needle=reasons[2])
    # 反向对照：同一份伪造材料若**不在**点名之列，本节不该因为它而失败（有界性同时也是
    # 「不越界检查」）——否则一处无关材料被换过会让整节正文写不出来。
    ok_rows, ok_readings = NO.organizer_material_context(
        prose_payload=[{"material_member_refs": [other_ref]}],
        manifest=NS.WriterMaterialManifest.create(members=(_entry(real), _entry(other))),
        material_context=_context(forged, other))
    check_eq([r["member_ref"] for r in ok_rows], [other_ref],
             "未被点名的成员即便正文已被换过也不影响本节（只核点名过的那些）")
    check_eq(ok_readings["identity_checks"], 1, "核身份的**次数**必须等于点名成员数（不是清单条数）")

    # 4.4 清单读不到 `entries`（没有 wmm-2 清单却点了名）。
    class _NoEntries:
        pass

    expect_error(
        lambda: NO.organizer_material_context(
            prose_payload=[{"material_member_refs": [member_ref]}],
            manifest=_NoEntries(), material_context=context),
        NO.NarrativeOrganizerError, "清单没有 entries 字段时必须当场抛（不吃默认值）",
        needle=reasons[0])
    # 上下文没有 `materials` 字段时同样不得静默给出空集。
    expect_error(
        lambda: NO.organizer_material_context(
            prose_payload=[{"material_member_refs": [member_ref]}],
            manifest=manifest, material_context=object()),
        NO.NarrativeOrganizerError, "上下文没有 materials 字段时必须当场抛（不吃默认值）",
        needle=reasons[1])

    # 同一段草稿点名的两个成员 → 两行，且顺序是**点名顺序**（不是清单顺序）。
    two_rows, two_readings = NO.organizer_material_context(
        prose_payload=[{"material_member_refs": [other_ref, member_ref]}],
        manifest=manifest, material_context=context)
    check_eq([r["member_ref"] for r in two_rows], [other_ref, member_ref],
             "行序 = 台账里的**点名顺序**（身份集合与清单顺序一致不代表行序可以随便排）")
    check_eq(two_readings["rows"], 2, "两个点名成员 ⇒ 两行")

    # ============================================================ §5 规则面
    rules = sent.get("rules")
    check(isinstance(rules, list) and rules, "请求里必须带规则清单")
    joined_rules = "\n".join(str(r) for r in rules)
    for token in ("material_context", "只读", "不是授权", "claim_ids"):
        check(token in joined_rules,
              f"投给模型的规则里必须写明材料正文依据面的纪律（缺 {token!r}）——"
              "给出材料却不给边界，等于把越权的诱因直接递到模型面前")
    check_eq(set(sent["output_schema"]), {"paragraphs", "claim_dispositions"},
             "输出面**一个键都不增**：依据面是输入侧政策，不是模型可回写的新字段")
    check_eq(tuple(sent["output_schema"]["paragraphs"][0]["sentences"][0]),
             ("text", "claim_ids", "context_binding_ids", "prose_unit_id"),
             "句子面的键集逐项不变（`prose_unit_id` 仍是唯一可选键）")

    # ============================================================ §6 只读不是授权（可执行）
    # 写法二（保真核验）：句子声明 `prose_unit_id`，因此它**必须**指向本次投给组织器的那一行
    # 草稿——`prose_unit_id` 是 `create` 里确定性派生的，不能由调用方自造（上一条断言即是）。
    unit_id = company_unit.prose_unit_id
    check(isinstance(unit_id, str) and unit_id, "草稿单元的身份由 `create` 派生（不得自造）")
    check(MATERIAL_ONLY_SURFACE not in CLAIM_TEXT and MATERIAL_ONLY_SURFACE not in CLAIM_TEXT_2,
          "前置条件：这个表面**只**在材料正文里，任何 Claim 文本里都没有")
    check(MATERIAL_ONLY_SURFACE in MATERIAL_TEXT,
          "前置条件：这个表面逐字在材料正文里（照材料写就会带上它）")
    rewrite_plan = _plan(
        [{"text": f"{MATERIAL_ONLY_SURFACE}，{CLAIM_TEXT}。",
          "claim_ids": [claim_a.claim_id], "prose_unit_id": unit_id}],
        dispositions)
    # 拒绝理由必须是**这个表面本身**：只断言「抛了 `NarrativeSchemaError`」会让任何一条别的
    # 语法错（例如自造 `prose_unit_id`）冒充这条反例——那正是本模块一开始踩到的假通过。
    expect_error(
        lambda: _organize(draft=company_draft, material_context=context,
                          llm=_StubLlm(rewrite_plan)),
        NS.NarrativeSchemaError,
        "照**材料正文**写进一个 Claim 里没有的表面必须被门拒（依据面不是授权面）",
        needle="报告期")
    # 正向对照：把那个表面删掉、其余一字不动，同一份计划必须被接受——否则上一条只是
    # 「什么都被拒」的弱结论（写法二整条路径根本没跑通）。
    ok_plan = _plan(
        [{"text": f"{CLAIM_TEXT}，公司{CLAIM_TEXT_2}。",
          "claim_ids": [claim_a.claim_id, claim_b.claim_id], "prose_unit_id": unit_id}],
        dispositions)
    ok_llm = _StubLlm(ok_plan)
    ok_narrative, _ = _organize(draft=company_draft, material_context=context, llm=ok_llm)
    check_eq(len(ok_narrative.paragraphs), 1,
             "同一份计划去掉那个越界表面后必须被接受（否则上一条反例不成立）")
    check_eq(ok_llm.calls[0]["messages"][0]["content"] is not None, True,
             "正向对照跑的仍是**同一条**投料路径（材料正文行在场）")
    check_eq(_sent_payload(ok_llm)["material_context"]["rows"][0]["text"], MATERIAL_TEXT,
             "正向对照里材料正文行**确实在场**：它没有授权，但也没有被撤掉")

    # 台账点名的成员不出现在行里是不可能的（正向已断言）——反向再钉一次：**行不会**因为
    # 模型写了越界内容而被追加或改写（依据面是输入，不随输出变化）。
    check_eq(_sent_payload(ok_llm)["material_context"],
             _sent_payload(llm)["material_context"],
             "同一份草稿 + 同一份上下文下，依据面**逐字**与上一次请求相同"
             "（它不随模型上一版输出变化）")

    # ============================================================ §7 一条原子、两处表达
    # `npr-1` 允许同一条候选在**多段**草稿里各出现一次（每段有它自己的材料出处）。门后组织器
    # 必须照旧收到**这两段**：台账两行、材料正文两行。这里钉住的是「它继续收到这些内容」——
    # 组织器本身**不改**（`norg-8` 的判定集一个字没动），因此这一节全是输入面读数。
    m_expr_a = _resolved(reading_view=EXPR_A)
    m_expr_b = _resolved(reading_view=EXPR_B, source_identity="evidence:ev-2", pack_id="pack-2",
                         material_id="m-2", material_content_fingerprint=FP_OTHER)
    expr_manifest = NS.WriterMaterialManifest.create(members=(_entry(m_expr_a), _entry(m_expr_b)))
    expr_context = _context(m_expr_a, m_expr_b)
    cand_shared = _candidate(CLAIM_TEXT, index=1)
    cand_own = _candidate(CLAIM_TEXT_2, index=2)
    claim_shared = _claim(cand_shared, index=1)
    claim_own = _claim(cand_own, index=2)
    expr_claims = (claim_shared, claim_own)
    # 两段草稿：**各自**只指自己那份材料；共享原子在两段里各出现一次（一段还多带自己那半事实）。
    unit_expr_a = _unit("pu-expr-a", member_refs=True, member_ref=m_expr_a.member_ref,
                        text=EXPR_A, index=0,
                        atoms=[cand_shared.candidate_id, cand_own.candidate_id])
    unit_expr_b = _unit("pu-expr-b", member_refs=True, member_ref=m_expr_b.member_ref,
                        text=EXPR_B, index=1, atoms=[cand_shared.candidate_id])
    expr_draft = _Draft(units=[unit_expr_a, unit_expr_b], manifest=expr_manifest)
    check(m_expr_a.member_ref != m_expr_b.member_ref,
          "前置条件：两段草稿各自出处的材料身份必须不同（否则测的是同一份材料的抄写）")

    # 7.1 台账面：两段都进输入面，且**同一条** `claim_id` 出现在两行的 `kept` 原子里。
    ledger_payload, ledger_readings = NO.prose_draft_ledger(
        draft=expr_draft, claims=expr_claims)
    check_eq(ledger_readings["prose_units_total"], 2,
             "两段草稿都必须进台账的总数读数（不得只统计一段）")
    check_eq(ledger_readings["prose_units_offered"], 2,
             "两段草稿都必须被**投出**（各自都含 `kept` 原子）——`npr-1` 的多 occurrence "
             "在台账面上就是两行，不是一行")
    check_eq(ledger_readings["atoms_kept"], 3,
             "三条原子事实（共享那条算两次）必须全部 `kept`：它们各自对应一条已定稿 Claim")
    check_eq(len(ledger_payload), 2, "台账恰两行（逐段一行）")
    check_eq([row["material_member_refs"] for row in ledger_payload],
             [[m_expr_a.member_ref], [m_expr_b.member_ref]],
             "两行各自带**它自己**的材料出处（共享原子不等于两行共用一份出处）")
    check_eq([row["text"] for row in ledger_payload], [EXPR_A, EXPR_B],
             "两行正文逐字等于两段草稿（不得只留第一段）")
    shared_rows = [row for row in ledger_payload
                   if claim_shared.claim_id in [str(a["claim_id"]) for a in row["atoms"]]]
    check_eq(len(shared_rows), 2,
             "同一条 `claim_id` 必须出现在**两行**的 `kept` 原子里（这正是多 occurrence）")
    ledger_read = NO.prose_ledger_claim_ids(ledger_payload)
    check_eq(ledger_read,
             {unit_expr_a.prose_unit_id: (claim_shared.claim_id, claim_own.claim_id),
              unit_expr_b.prose_unit_id: (claim_shared.claim_id,)},
             "单元 → Claim 的**唯一**读取口径必须把两段都读成「含这条共享 Claim」")

    # 7.2 真发出去的请求：两段台账 + **两份**材料的正文都到了组织器手里。
    expr_llm = _StubLlm(_plan(
        [{"text": f"{CLAIM_TEXT}，公司{CLAIM_TEXT_2}。",
          "claim_ids": [claim_shared.claim_id, claim_own.claim_id],
          "prose_unit_id": unit_expr_a.prose_unit_id},
         {"text": EXPR_B, "claim_ids": [claim_shared.claim_id],
          "prose_unit_id": unit_expr_b.prose_unit_id}],
        [{"claim_id": claim_shared.claim_id, "disposition": "selected", "reason_code": None},
         {"claim_id": claim_own.claim_id, "disposition": "selected", "reason_code": None}]))
    expr_narrative, expr_trace = _organize(draft=expr_draft, material_context=expr_context,
                                           llm=expr_llm)
    expr_sent = _sent_payload(expr_llm)
    sent_rows = expr_sent.get("prose_draft")
    check_eq([row["text"] for row in sent_rows], [EXPR_A, EXPR_B],
             "请求里的 `prose_draft` 必须是**两行**、逐字等于两段草稿（组织器以草稿为表达基础）")
    check_eq([row["material_member_refs"] for row in sent_rows],
             [[m_expr_a.member_ref], [m_expr_b.member_ref]],
             "请求里两行各自带它自己的材料出处")
    check_eq([{a["status"] for a in row["atoms"]} for row in sent_rows],
             [{"kept"}, {"kept"}],
             "两行的原子都必须是 `kept`（没过门的原子不进这一面，也不进正文）")
    check_eq([len(row["atoms"]) for row in sent_rows], [2, 1],
             "共享原子在两行里各出现一次——台账不把第二处抹掉")
    # 材料正文面：两段各自点名的成员都投出，行序 = 台账点名顺序。
    expr_ctx_rows = expr_sent["material_context"]["rows"]
    check_eq([row["member_ref"] for row in expr_ctx_rows],
             [m_expr_a.member_ref, m_expr_b.member_ref],
             "两段草稿点名的**两份**材料正文都要投给组织器（只投第一份会让第二处表达无从照原文改）")
    check_eq([row["text"] for row in expr_ctx_rows], [EXPR_A, EXPR_B],
             "投出的正文逐字等于两份已解析材料的读视图")
    check_eq(expr_trace["organizer_material_context"],
             {"policy_version": "orgmc-1", "requested_members": 2, "rows": 2,
              "identity_checks": 2},
             "读数：点名 2 个成员 / 投出 2 行 / 逐成员核身份 2 次")
    check_eq(expr_trace["prose_draft"], ledger_readings,
             "trace 里的草稿层读数必须与台账**同一次**读数逐项相同（不是另算一遍）")
    # 7.3 两处表达都真的被采纳：门后组织器**不**替读者删掉第二处，而是把它作为一处独立表达
    #     保留下来（读者面重复由「表达选择」处理，不由删段/截字处理）。
    expr_sentences = [s for p in expr_narrative.paragraphs for s in p.sentences]
    check_eq(len(expr_sentences), 2,
             "两段草稿各自成句：组织器必须能把**两处**表达都写进正文（不是只留第一处）")
    check(all(claim_shared.claim_id in tuple(s.claim_ids) for s in expr_sentences),
          "两处表达都必须声明同一条共享 Claim（否则第二处就不是这一条原子的表达）")
    check(expr_sentences[0].text != expr_sentences[1].text,
          "两处表达必须是**不同**的文字（同一句抄两遍不是「两处表达」）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

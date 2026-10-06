"""Eval: M930-3 §三 C 门后**自然组织** successor（narr-8 / norg-8；判定集 `norg-7`）。

用法: python -m evals.test_demo_narrative_organizer

覆盖（每条对应一个具体缺陷）：

* §1 组织器**不是**第二个写作器/LLM runtime：只接受注入的 `NarrationClient`，只发起**一次**
      调用，静态上不持有 store / 检索 / 数据库入口，且只有一处 prompt 加载点；
* §2 正向：多条已定稿 Claim 被组织进**一个自然句**（`sentence_kind="composed"`），句子声明的
      `claim_ids` 就是它真正用到的那几条，citation 与段落 topic 由系统**派生**而非模型自报；
* §3 确定性核验逐条反例化：
      3.1 引用不存在 / 非当前的 Claim → 拒；
      3.2 正文新增高风险表面（数字 / 主体身份 / 显式否定 / 因果措辞）→ 拒；逐字沿用 → 放行；
          相邻 Claim 的接缝不带分隔标点（`A此外，B`）→ 拒；`A，此外，B` → 放行；
      3.3 未接受的 context 绑定 → 拒；没有 Claim 的句子 → 拒（context 不授权事实）；
      3.4 去向缺失 / 重复 / 多余 / 自由文本理由 / selected 带理由 → 拒；记号与正文矛盾 → 拒；
      3.5 记号 `selected` 却在正文里一次都没被引用 → 拒；
* §4 句子文本与它声明的绑定**共用一个内容身份**：改文本、改 Claim 绑定、改 context 绑定
      都会改 `sentence_id`；
* §5 prompt 资产纪律：自报身份唯一、正文指纹被真覆盖、单点变异即拒、自带样例可被解析器接受；
      两个版本号**分开钉住**（判定集 `norg-7` / 资产修订 `norg-8`，本批只改输入面）；
* §6 退化路径走确定性构造器；**有 Claim 却缺 `llm_client` 必须 fail-closed**（不得静默跳过）；
      调用未成功也必须 fail-closed（不得凭旧文本组织）；
* §7 r7b 公司节的现场形态：引用落在**跨页材料**上的采购 / 生产 / 销售三条 Claim 必须能组织成
      **一整段一句话**；机械拼接不得以「多 Claim 自然句」落地（补救成逐条事实句）；一句塞入
      超过上界的 Claim 且接缝带组织语必须 fail-closed（不得靠补救洗成合法正文）。

全部离线、确定性：生成器是返回结构化 `PW.NarrationResult` 的 stub。不读库、不连网、
不调真实 LLM、不写任何文件。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as HS
from llm import client as LLMC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import schema as SS

ROOT = Path(__file__).resolve().parent.parent
ORGANIZER_PATH = ROOT / "sections" / "narrative_organizer.py"
PROFILE_PATH = str(ROOT / "templates" / "presentation_profiles" / "interview_demo_v1.yaml")

TOPIC_A = "topic-company-business"
TOPIC_B = "topic-company-legal"
IDENTITY = {"task_id": "task-1", "section_id": "company",
            "section_draft_id": "draft-1", "draft_revision": "rev-1"}

#: 已定稿 Claim 的文本 = **原子子句**（不带句末标点）。这正是 P2 之后候选侧的口径
#: （`proposals-7`：「候选文本的写法」一节），也是能被组织进**同一句话**的前提——
#: 自带句末标点的 Claim 只能单独成句（`ng-8` 判据 d）。
TEXT_A = "公司主营业务由动力电池与储能两大条线构成"
TEXT_B = "公司销售以直销为主"
TEXT_C = "公司境外收入占比相对较低"
#: 一条**自带句末标点**的 Claim（事实句的渲染形态：`render_factual_text` 逐条去掉尾部
#: `。；` 再用 `；` 连接、末尾补 `。`）。它只能单独成句，不得与别的 Claim 串一句。
TEXT_D = "公司2024年营业收入为1234.56亿元"


def _citation(evidence_id: str) -> HS.CitationRef:
    return HS.CitationRef(ref_type="evidence", evidence_id=evidence_id)


def _claim(text: str, topic_id: str, *, index: int, evidence: str) -> SS.SectionClaim:
    question_ids = (f"q-{index}",)
    refs = (_citation(evidence),)
    claim_id = SS.derive_claim_id("fact", topic_id, question_ids, text, refs,
                                  f"cand-{index}", "rev-1", (f"asb-{index}",))
    return SS.SectionClaim(
        claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
        topic_id=topic_id, question_ids=question_ids, text=text, claim_type="fact",
        citation_refs=refs, claim_candidate_id=f"cand-{index}",
        claim_candidate_revision="rev-1", accepted_binding_ids=(f"asb-{index}",))


class _ContextBinding:
    """门后 context accepted binding 的**最小形状替身**（组织器只读它的身份字段）。"""

    def __init__(self, binding_id: str, *, material_id: str = "m-1") -> None:
        self.accepted_support_binding_id = binding_id
        self.material_id = material_id
        self.authority_container_id = "pack-1"
        self.support_role = "corroborating"
        self.support_semantics = "context"


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
        index = len(self.calls)
        if isinstance(item, Exception):
            return PW.NarrationResult(
                text="", call_id=f"err-{index}", model=PW.MODEL_POLICY_STUB,
                prompt_version=prompt_version, status="error",
                error=f"{type(item).__name__}: {item}")
        return PW.NarrationResult(
            text=item if isinstance(item, str) else json.dumps(item, ensure_ascii=False),
            call_id=f"call-{index}", model=PW.MODEL_POLICY_STUB,
            prompt_version=prompt_version, status="ok",
            input_tokens=128, output_tokens=64, latency_ms=1, finish_reason="stop")


def _sentence(text: str, claim_ids, context=()):
    return {"text": text, "claim_ids": list(claim_ids), "context_binding_ids": list(context)}


def _plan(sentences, dispositions):
    return {"paragraphs": [{"sentences": list(sentences)}],
            "claim_dispositions": list(dispositions)}


def _organize(claims, *, plan=None, contexts=(), llm=None):
    return NO.organize_section_narrative(
        claims=claims, accepted_context_bindings=contexts, unresolved=(),
        writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
        llm_client=llm if llm is not None else _StubLlm(plan))


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
                details.append(f"FAIL {msg}：原因不符（{str(e)[:160]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    claim_a = _claim(TEXT_A, TOPIC_A, index=1, evidence="evidence:ev-1")
    claim_b = _claim(TEXT_B, TOPIC_A, index=2, evidence="evidence:ev-2")
    claim_c = _claim(TEXT_C, TOPIC_B, index=3, evidence="evidence:ev-3")
    claims = (claim_a, claim_b, claim_c)
    by_index = {1: claim_a, 2: claim_b, 3: claim_c}

    def _d(index: int, disposition: str = "selected", reason=None):
        return {"claim_id": by_index[index].claim_id, "disposition": disposition,
                "reason_code": reason}

    context_binding = _ContextBinding("asb-ctx-1")
    #: 合法组织：把 A、B 两条 Claim 组织成一句（C 属另一主题，如实 omit）。§七 1 之后，
    #: 「合法」的含义是**几何上真的被组织过**：两条 Claim 文本逐字、完整、按声明顺序在场，
    #: 组织语（「同时，」）只插在它们**之间**——不是 `A；B` 式首尾相接，也不是在 Claim 内部
    #: 换词/省主语（那会让 Claim 文本不再逐字在场，等于正文与绑定不一致）。
    #: P2（§三 2.4）之后还多一条：**一句就是一个句子**——句末标点至多出现一次且只能在末尾，
    #: 因此两条原子子句之间不能夹句末标点，句末那一个 `。` 由你（组织器）给。
    #: 判据 e（`nrules-12`）再收紧一格：**接缝必须以分隔标点起头**——连接语自带一个尾部逗号，
    #: 直接写在接缝开头就是「上一条断言 + 连接语」黏成 `…研发、生产、销售此外，公司…`。
    #: 因此合法的组织语形如 `，同时，`（逗号起头），`同时，` 光杆粘在 Claim 后面不是组织。
    composed_text = TEXT_A + "，同时，" + TEXT_B + "。"
    composed_plan = _plan(
        [_sentence(composed_text, [claim_a.claim_id, claim_b.claim_id])],
        [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])

    # ============================================================ §1 不是第二个 runtime
    source = ORGANIZER_PATH.read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=str(ORGANIZER_PATH))):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = sorted(imported & {"sqlite3", "requests", "harness", "evaluation", "subprocess"})
    check(not forbidden,
          f"组织器不得持有 store / 检索 / 数据库入口（实测 import {forbidden}）")
    check(source.count("load_prompt(") == 1,
          "组织器必须只有一处 prompt 加载点（不得自带第二份措辞或第二套资产）")

    # ============================================================ §2 正向：多 Claim 一句
    llm = _StubLlm(composed_plan)
    narrative, trace = _organize(claims, llm=llm)
    check(len(llm.calls) == 1 and trace["llm_calls"] == 1,
          f"组织必须**恰好一次**调用（实测 {len(llm.calls)} 次）")
    check(llm.calls[0]["prompt_version"] == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION
          and llm.calls[0]["model_policy"] == NO.NARRATIVE_ORGANIZER_MODEL_POLICY,
          "调用的 prompt 版本与 model policy 必须取自登记值（不得自报）")
    check(trace.get("status") == "ok" and trace.get("call_id") == "call-1",
          "trace 必须留结构化调用元数据（call_id / status）")
    sentence = narrative.paragraphs[0].sentences[0]
    check(sentence.sentence_kind == "composed",
          f"多 Claim 组织的句子必须是 composed（实测 {sentence.sentence_kind!r}）")
    check(set(sentence.claim_ids) == {claim_a.claim_id, claim_b.claim_id},
          "composed 句声明的 claim_ids 必须是它真正用到的那几条")
    expected_citations = {SS.derive_citation_id(c.claim_id, ref)
                          for c in (claim_a, claim_b) for ref in c.citation_refs}
    check(set(sentence.citation_ids) == expected_citations,
          "citation 必须由系统从所绑定 Claim 派生（模型自报 citation 不可表达）")
    check(narrative.claim_ids == (claim_a.claim_id, claim_b.claim_id),
          "final Narrative 的 Claim 并集必须等于正文真实引用")
    check(narrative.paragraphs[0].topic_ids == (TOPIC_A,),
          "段落 topic 归属必须从所引用 Claim 派生（不得模型自报）")
    disposition_kinds = [d["disposition"] for d in trace["claim_narrative_dispositions"]]
    check(disposition_kinds == ["selected", "selected", "omitted"],
          f"去向必须如实反映「选两条、omit 一条」（实测 {disposition_kinds}）")
    check(trace["composed_sentence_count"] == 1 and trace["sentence_count"] == 1,
          "trace 必须记录句数/composed 句数（人读内容门按这个数验收，不凭印象）")
    # 组装器用**同一函数**复算必须同样通过（一个实现、两个调用点）。
    NS.verify_section_narrative(
        narrative=narrative, claims=claims, accepted_context_binding_ids=(),
        dispositions=tuple(NS.ClaimNarrativeDisposition.from_dict(x)
                           for x in trace["claim_narrative_dispositions"]))
    passed += 1

    # 组织器只吃**已定稿**对象：输入面不得出现门前候选束的任何字段（否则它绕过两道门）。
    body = NO.build_organizer_messages(
        claims=claims, accepted_context_bindings=(), unresolved=(),
        writing_spec=object(), presentation_profile=object(),
        identity=IDENTITY)[0]["content"]
    for token in ("claim_candidates", "proposed_support_refs", "narrative_draft_units",
                  "ClaimCandidate", "ProposedSupportRef", "fact_candidates"):
        check(token not in body,
              f"组织器输入面不得出现门前候选束字段 {token!r}")

    # §三 A / 3.2：门后组织器与门前写作器必须投**同一份**呈现视图。修复前两边各写了一份
    # 字段名表、两份都取不存在的字段名，于是两个生成器看到的 `presentation_profile` 都恒为
    # `{"schema_version": ...}`；这条断言钉住「同一实现、同一内容」，而不只是「两边都有字段」。
    real_profile = PP.load_presentation_profile(PROFILE_PATH)
    real_body = json.loads(NO.build_organizer_messages(
        claims=claims, accepted_context_bindings=(), unresolved=(),
        writing_spec=object(), presentation_profile=real_profile,
        identity=IDENTITY)[0]["content"])["presentation_profile"]
    check(real_body == PW._jsonable(PP.presentation_payload(real_profile)),
          "门后组织器的呈现视图必须逐字等于共用投影函数的结果（不得各写一份字段表）")
    check(set(real_body) == set(PP._PRESENTATION_PAYLOAD_FIELDS)
          and real_body["presentation_profile_id"] == real_profile.presentation_profile_id
          and real_body["display_rules"] == PW._jsonable(real_profile.display_rules),
          f"真实呈现规格必须真的进组织器请求面（实为 {sorted(real_body)}）")
    check(real_body != {"schema_version": real_profile.schema_version},
          "真实呈现规格的视图不得退化成「只有 schema_version」——那正是 3.2 修复前的形态")
    # 形状替身（`object()`）如实给出空视图：组织器不得因为拿不到规格就伪造一份内容。
    check(json.loads(body)["presentation_profile"] == {},
          "没有声明呈现字段的对象必须得到空视图（如实，不伪造、不补默认值）")

    # ============================================================ §3.1 引用非当前 Claim
    expect_error(
        lambda: _organize(claims, plan=_plan(
            [_sentence(TEXT_A, ["cl_not_current"])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])),
        NO.NarrativeOrganizerError, "引用本次定稿集之外的 Claim 必须被拒",
        needle="本次定稿集之外")

    # ============================================================ §3.2 新增高风险表面
    def _text_case(text: str):
        return lambda: _organize(claims, plan=_plan(
            [_sentence(text, [claim_a.claim_id, claim_b.claim_id])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")]))

    expect_error(
        _text_case(composed_text + "该业务占比为 62.5%。"),
        NS.NarrativeSchemaError, "正文新增 Claim 里没有的数字必须被拒", needle="高风险表面")
    expect_error(
        _text_case(composed_text + "由宁德新能源科技有限公司负责。"),
        NS.NarrativeSchemaError, "正文新增 Claim 里没有的主体身份必须被拒", needle="高风险表面")
    expect_error(
        _text_case(composed_text + "未披露其他渠道。"),
        NS.NarrativeSchemaError, "正文新增显式否定必须被拒（把「有」写成「没有」是最高风险改写）",
        needle="高风险表面")
    expect_error(
        _text_case(composed_text + "因此渠道结构稳定。"),
        NS.NarrativeSchemaError, "正文新增「因此」式结论必须被拒（并列不得升级为推理）",
        needle="高风险表面")
    expect_error(
        lambda: _organize(claims, plan=_plan(
            [_sentence("本节概述公司经营情况。", [claim_a.claim_id])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])),
        NS.NarrativeSchemaError, "写了 Claim 文字之外的话必须被拒（不得拿 Claim 当挡箭牌）",
        needle="高风险表面")

    # 正向对照：数字**逐字**来自所绑定 Claim 时放行（判据是「逐字在场」，不是「命中即拒」）。
    numeric_claim = _claim(TEXT_D, TOPIC_A, index=9, evidence="evidence:ev-9")
    ok_text = numeric_claim.text + "，此外，" + claim_b.text + "。"
    narrative_ok, _ = NO.organize_section_narrative(
        claims=(numeric_claim, claim_b), accepted_context_bindings=(), unresolved=(),
        writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
        llm_client=_StubLlm(_plan(
            [_sentence(ok_text, [numeric_claim.claim_id, claim_b.claim_id])],
            [{"claim_id": numeric_claim.claim_id, "disposition": "selected", "reason_code": None},
             {"claim_id": claim_b.claim_id, "disposition": "selected", "reason_code": None}])))
    ok_tokens = narrative_ok.paragraphs[0].sentences[0].numeric_tokens
    check(bool(ok_tokens) and all(t in (numeric_claim.text + claim_b.text) for t in ok_tokens),
          f"逐字沿用 Claim 数字的合法组织必须放行（实测数字 {ok_tokens}）")

    # ============================================================ §3.3 context 边界
    expect_error(
        lambda: _organize(claims, plan=_plan(
            [_sentence(composed_text, [claim_a.claim_id, claim_b.claim_id],
                       context=["asb-ctx-forged"])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])),
        NS.NarrativeSchemaError, "使用本节未接受的 context 绑定必须被拒", needle="未接受")
    narrative_ctx, _ = _organize(
        claims, contexts=(context_binding,),
        plan=_plan([_sentence(composed_text, [claim_a.claim_id, claim_b.claim_id],
                              context=[context_binding.accepted_support_binding_id])],
                   [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")]))
    check(narrative_ctx.paragraphs[0].sentences[0].context_binding_ids
          == (context_binding.accepted_support_binding_id,),
          "已接受的 context 绑定必须能被 composed 句如实声明")
    check(narrative_ctx.context_binding_ids
          == (context_binding.accepted_support_binding_id,),
          "节级声明必须是本节已接受的 context 全集（完备性由节级承载）")
    expect_error(
        lambda: NO.parse_organizer_plan(json.dumps(_plan(
            [_sentence("本节概述公司经营情况。", [],
                       context=[context_binding.accepted_support_binding_id])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")]), ensure_ascii=False)),
        NO.NarrativeOrganizerError, "没有 Claim 的句子必须被拒（context 不授权事实）",
        needle="没有 Claim")

    # ============================================================ §3.4 去向完备性
    def _disposition_case(dispositions):
        return lambda: _organize(claims, plan=_plan(
            [_sentence(composed_text, [claim_a.claim_id, claim_b.claim_id])], dispositions))

    expect_error(_disposition_case([_d(1), _d(2)]),
                 NS.NarrativeSchemaError, "缺一条 Claim 的去向必须被拒", needle="不精确相等")
    expect_error(_disposition_case([_d(1), _d(1), _d(2), _d(3, "omitted", "outside_section_topic")]),
                 NS.NarrativeSchemaError, "同一 Claim 出现两条去向必须被拒", needle="多条去向")
    expect_error(_disposition_case([_d(1), _d(2), _d(3, "omitted", "outside_section_topic"),
                                    {"claim_id": "cl_nonexistent", "disposition": "selected",
                                     "reason_code": None}]),
                 NS.NarrativeSchemaError, "给不存在的 Claim 编造去向必须被拒", needle="不精确相等")
    expect_error(_disposition_case([_d(1), _d(2), _d(3, "omitted", "some_free_text_reason")]),
                 NS.NarrativeSchemaError, "omitted 的自由文本理由必须被拒（理由码是封闭集）",
                 needle="reason_code")
    expect_error(_disposition_case([_d(1), _d(2), _d(3, "selected", "outside_section_topic")]),
                 NS.NarrativeSchemaError, "selected 携带缺席理由必须被拒", needle="reason_code")
    expect_error(
        lambda: _organize(claims, plan=_plan(
            # 多写一句是**合法**的组织动作（一句 = 一个句子），因此这里必须另起一句来试
            # 「记号 omitted 却真的被引用」——把 C 硬接到 A/B 那一条句子上会先被
            # 「句中句末标点」拒掉，那条判据在下面 §3.6 单独反例化。
            [_sentence(composed_text, [claim_a.claim_id, claim_b.claim_id]),
             _sentence(TEXT_C + "。", [claim_c.claim_id])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])),
        NS.NarrativeSchemaError, "记号 omitted 但正文真的用到它必须被拒", needle="omitted")

    # ============================================================ §3.6 composed 句必须真被组织过
    # 这几条判据对应 §七 1：机器门不能只看 `len(claim_ids) >= 2`。判据是**机械**的——声明的
    # Claim 文本是否逐字、完整、按声明顺序在场；剔除它们之后是否还剩组织语；一句承载几条；
    # 以及句子中间有没有句末标点（`A。同时B。` 是**两个**句子首尾相接）。
    #
    # ⑥ 之后这一节分成两半，两半都必须看到：
    #   * **判据侧一字未改**：同样的夹具仍然被判为同一类机械缺陷（封闭码，`ng-9 == ng-8`）；
    #   * **补救侧**：被拒的那一版由 `_bounded_reorganization` **确定性**重组织（不再发模型调用）
    #     ——先按模型自己的句中句末标点切段，切不出全合法的段时改为每条 Claim 各自成一句
    #     逐字事实句。补救产物必须能整份过冻结门，否则"补救了"就只是一句自述。
    def _judged(text: str, texts) -> "str | None":
        return NS.composed_organization_defect(text=text, authorized_texts=texts)

    def _sentences_of(narrative):
        return list(narrative.paragraphs[0].sentences)

    def _gate_ok(narrative, claim_objs=claims) -> bool:
        try:
            NS.verify_section_narrative(
                narrative=narrative, claims=claim_objs, accepted_context_binding_ids=(),
                dispositions=())
        except NS.NarrativeSchemaError:
            return False
        return True

    texts_ab = (TEXT_A, TEXT_B)
    selected_two = [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")]
    for label, joined, defect, remedy in (
            ("两条 Claim 文本直接相连", TEXT_A + TEXT_B,
             NS.COMPOSED_DEFECT_MECHANICAL_JOIN, NO.REORGANIZATION_PER_CLAIM_FACTUAL),
            ("用「；」首尾相接", TEXT_A + "；" + TEXT_B,
             NS.COMPOSED_DEFECT_MECHANICAL_JOIN, NO.REORGANIZATION_SPLIT_AT_TERMINATORS),
            ("用「。」「，」相接", TEXT_A + "。，" + TEXT_B,
             NS.COMPOSED_DEFECT_MECHANICAL_JOIN, NO.REORGANIZATION_SPLIT_AT_TERMINATORS),
            ("用逗号相接", TEXT_A + "，" + TEXT_B,
             NS.COMPOSED_DEFECT_MECHANICAL_JOIN, NO.REORGANIZATION_PER_CLAIM_FACTUAL),
            ("双句容器 `A。同时B。`", TEXT_A + "。同时，" + TEXT_B + "。",
             NS.COMPOSED_DEFECT_INTERNAL_TERMINATOR, NO.REORGANIZATION_SPLIT_AT_TERMINATORS),
            ("句中 `；`", TEXT_A + "；同时，" + TEXT_B + "。",
             NS.COMPOSED_DEFECT_INTERNAL_TERMINATOR, NO.REORGANIZATION_SPLIT_AT_TERMINATORS)):
        check(_judged(joined, texts_ab) == defect,
              f"第 8 条的判据必须照旧拒该形态（{label}，实测 "
              f"{_judged(joined, texts_ab)!r}）")
        narrative, trace = _organize(claims, plan=_plan(
            [_sentence(joined, [claim_a.claim_id, claim_b.claim_id])], selected_two))
        check(trace["reorganization_version"] == NO.NARRATIVE_ORGANIZER_REORGANIZATION_VERSION
              and trace["reorganizations"] == 1
              and trace["reorganization_remedies"] == [remedy],
              f"被拒的形态必须被确定性补救（{label}，实测 "
              f"{trace.get('reorganization_version')!r} / {trace.get('reorganization_remedies')!r}）")
        check(trace["reorganization_details"][0]["defect"] == defect,
              f"补救记录必须写明**封闭缺陷码**（{label}）")
        check(_gate_ok(narrative),
              f"补救后的正文必须能整份过冻结门（{label}）")
        if remedy == NO.REORGANIZATION_SPLIT_AT_TERMINATORS:
            check("".join(s.text for s in _sentences_of(narrative)) == joined,
                  f"切段必须逐字保留模型原文，禁止改写或补写（{label}，实测 "
                  f"{[s.text for s in _sentences_of(narrative)]}）")
            check([s.sentence_kind for s in _sentences_of(narrative)] == ["composed"] * 2,
                  f"切出来的段仍是 composed 句（同一口径）（{label}）")
        else:
            check([s.text for s in _sentences_of(narrative)]
                  == [NS.render_factual_text([TEXT_A]), NS.render_factual_text([TEXT_B])],
                  f"第二级的正文只能是 Claim 文本的逐字渲染（{label}，实测 "
                  f"{[s.text for s in _sentences_of(narrative)]}）")
            check([s.sentence_kind for s in _sentences_of(narrative)] == ["factual"] * 2
                  and all(len(s.claim_ids) == 1 for s in _sentences_of(narrative)),
                  f"第二级必须每条 Claim 各自成句（{label}）")
    # 组织语只允许插在 Claim 文本**之间**：在 Claim 内部换词/省主语会让 Claim 文本不再逐字在场。
    # 这一类**不补救**：切段变不出声明里没有的 Claim，只能 fail-closed（把「正文与绑定不一致」
    # 改写成一条看着合法的句子，才是真正的放水）。
    expect_error(
        lambda: _organize(claims, plan=_plan(
            [_sentence(TEXT_A + "同时，销售以直销为主。",
                       [claim_a.claim_id, claim_b.claim_id])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])),
        NS.NarrativeSchemaError,
        "在 Claim 内部省略主语（正文与绑定不一致）必须被拒（且不得被补救）",
        needle="没有按序出现在正文里")
    # 这条用的是**自带句末标点**的 Claim（事实句的渲染形态），把它末尾的「。」在正文里吞掉
    # 就是「Claim 文本不再逐字在场」——它必须被拒，而不是因为「少一个标点」被放行。
    punctuated = _claim(TEXT_D + "。", TOPIC_A, index=10, evidence="evidence:ev-10")
    expect_error(
        lambda: NO.organize_section_narrative(
            claims=(punctuated, claim_b), accepted_context_bindings=(), unresolved=(),
            writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
            llm_client=_StubLlm(_plan(
                [_sentence(TEXT_D + "同时，" + TEXT_B + "。",
                           [punctuated.claim_id, claim_b.claim_id])],
                [{"claim_id": punctuated.claim_id, "disposition": "selected",
                  "reason_code": None},
                 {"claim_id": claim_b.claim_id, "disposition": "selected",
                  "reason_code": None}]))),
        NS.NarrativeSchemaError,
        "在 Claim 内部改标点（句末「。」被吞掉）也必须被拒", needle="没有按序出现在正文里")
    # 自带句末标点的 Claim 与别的 Claim 串一句：判据照旧判为双句容器，而补救**恰好**把它切成
    # 「Claim 自己的那一句」+「衔接语 + 后一条 Claim」——模型写的衔接语留在相邻那段，一个字不少。
    punct_dispositions = [(lambda c: {"claim_id": c.claim_id, "disposition": "selected",
                                      "reason_code": None})(punctuated),
                          {"claim_id": claim_b.claim_id, "disposition": "selected",
                           "reason_code": None}]
    punct_plan = _sentence(punctuated.text + "同时，" + TEXT_B + "。",
                           [punctuated.claim_id, claim_b.claim_id])
    check(_judged(punct_plan["text"], (punctuated.text, TEXT_B))
          == NS.COMPOSED_DEFECT_INTERNAL_TERMINATOR,
          "自带句末标点的 Claim 与别的 Claim 串一句必须被判为双句容器")
    punct_narrative, punct_trace = _organize(
        (punctuated, claim_b), plan=_plan([punct_plan], punct_dispositions))
    check(punct_trace["reorganization_remedies"] == [NO.REORGANIZATION_SPLIT_AT_TERMINATORS]
          and [s.text for s in _sentences_of(punct_narrative)]
          == [punctuated.text, "同时，" + TEXT_B + "。"],
          "补救必须切成「Claim 自己那句」+「衔接语 + 后一条 Claim」（实测 "
          f"{[s.text for s in _sentences_of(punct_narrative)]}）")
    check(_gate_ok(punct_narrative, (punctuated, claim_b)),
          "这类容器补救后的正文必须合法")
    # 正向对照（防修过头）：**原子子句 + 安全衔接语**必须算一条多 Claim 自然句，
    # 而且判据 d 只看**句中**——末尾的一个句号是合法句读，不是「句末标点出现两次」。
    check(not NS.has_internal_sentence_terminator(composed_text)
          and NS.has_internal_sentence_terminator(TEXT_A + "。同时，" + TEXT_B + "。")
          and not NS.has_internal_sentence_terminator(TEXT_A + "同时，" + TEXT_B + "。"),
          "判据 d 必须只拒「句中句末标点」：末尾的一个句号是合法句读")
    # 判据 e（`nrules-12`）的正反例，与判据 d 一样只问**判据本身**：
    #   * 沾连接语但接缝不带分隔标点 → `unseparated_seam`（这正是 r7b 正文里
    #     「…研发、生产、销售此外，公司…」的形态，会读成一条断言加半个词）；
    #   * 连接语前面多一个逗号 → 合规（组织语仍逐字是模型写的那几个字，一个字没多）。
    check(NS.composed_organization_defect(
              text=TEXT_A + "同时，" + TEXT_B + "。", authorized_texts=(TEXT_A, TEXT_B))
          == NS.COMPOSED_DEFECT_UNSEPARATED_SEAM,
          "接缝不带分隔标点必须被判为 `unseparated_seam`（不得只报「被组织过」）")
    check(NS.composed_organization_defect(
              text=composed_text, authorized_texts=(TEXT_A, TEXT_B)) is None,
          f"接缝以分隔标点起头是合法组织（实测 "
          f"{NS.composed_organization_defect(text=composed_text, authorized_texts=(TEXT_A, TEXT_B))!r}）")
    expect_error(
        lambda: _organize(claims, plan=_plan(
            [_sentence(TEXT_A + "同时，" + TEXT_B + "。", [claim_a.claim_id, claim_b.claim_id])],
            [_d(1), _d(2), _d(3, "omitted", "outside_section_topic")])),
        NS.NarrativeSchemaError,
        "黏连接缝的正文必须被整份核验拒掉（判据不能只活在谓词里）", needle="接缝")
    # 上界：一句最多 4 条。要覆盖更多 Claim 就多写一句，不是把句子写长。
    many = tuple(_claim(f"公司第{n}项业务由自有产线承担", TOPIC_A, index=20 + n,
                        evidence=f"evidence:ev-{20 + n}") for n in range(1, 6))
    many_ids = [c.claim_id for c in many]
    many_dispositions = [{"claim_id": cid, "disposition": "selected", "reason_code": None}
                         for cid in many_ids]
    # 上界：一句最多 4 条。这句里模型**自己写了组织语**（`，同时，`），补救会丢掉它们，
    # 于是「拆成各自成句」会把模型贡献的字洗掉——这一版必须原样 fail-closed。
    # 接缝写成带分隔标点的 `，同时，`，否则先撞的是判据 e（黏连接缝），上界判据（c）轮不到。
    over_bound = "，同时，".join(c.text for c in many) + "。"
    check(_judged(over_bound, tuple(c.text for c in many))
          == NS.COMPOSED_DEFECT_OVER_CLAIM_BOUND,
          "一个超长句塞入 5 条互不组织的 Claim 必须被判为超过上界")
    expect_error(
        lambda: _organize(many, plan=_plan([_sentence(over_bound, many_ids)],
                                           many_dispositions)),
        NS.NarrativeSchemaError,
        "带模型自己组织语的超上界句不得被「补救」（补救会丢掉模型写的字 = 洗掉失败）",
        needle="超过一句最多 4 条的上界")
    check(not NS.is_punctuation_only_join(over_bound, tuple(c.text for c in many)),
          "前置条件：`，同时，` 接缝不是纯标点接缝（判据必须看得见模型写的组织语）")
    # 反过来，**纯标点**接缝的 5 Claim 长句（模型一个字都没贡献）才是第二级补救的对象：
    # 它被拆成 5 条各自成句的事实句，每个 Claim 逐字成句。
    comma_join = "，".join(c.text for c in many) + "。"
    check(_judged(comma_join, tuple(c.text for c in many))
          == NS.COMPOSED_DEFECT_MECHANICAL_JOIN
          and NS.is_punctuation_only_join(comma_join, tuple(c.text for c in many)),
          "前置条件：`，` 相接的 5 Claim 长句是「机械拼接」且接缝是纯标点")
    narrative_many, many_trace = _organize(
        many, plan=_plan([_sentence(comma_join, many_ids)], many_dispositions))
    check(many_trace["reorganization_remedies"] == [NO.REORGANIZATION_PER_CLAIM_FACTUAL]
          and len(_sentences_of(narrative_many)) == 5
          and all(len(s.claim_ids) == 1 and s.sentence_kind == "factual"
                  for s in _sentences_of(narrative_many)),
          "机械拼接的长句必须被拆成**每条 Claim 各自一句**的事实句（不得有任何一句仍承载多 Claim）")
    check([s.text for s in _sentences_of(narrative_many)]
          == [NS.render_factual_text([c.text]) for c in many],
          "拆出来的每一句只能是它那条 Claim 的逐字渲染")
    check(_gate_ok(narrative_many, many),
          "拆成各自成句之后的正文必须合法（补救不是把超上界洗成合法，而是真的拆开）")
    # 正向对照：4 条以内、真的被组织过，放行（上界不是「多 Claim 就拒」），且**不触发**补救。
    narrative_four, four_trace = _organize(many, plan=_plan(
        [_sentence("，同时，".join(c.text for c in many[:4]) + "。", many_ids[:4])],
        [{"claim_id": cid, "disposition": "selected" if i < 4 else "omitted",
          "reason_code": None if i < 4 else "redundant_with_selected_claim"}
         for i, cid in enumerate(many_ids)]))
    check(len(narrative_four.paragraphs[0].sentences[0].claim_ids) == 4,
          "一句组织 4 条 Claim 是合法组织（上界是 4，不是 2 也不是无穷）")
    check(four_trace["reorganizations"] == 0,
          "合法组织不得被「补救」（补救只在第 8 条的机械缺陷上动作）")

    # ============================================================ §3.5 selected 必须真被引用
    narrative_partial, _ = _organize(claims, plan=_plan(
        [_sentence(TEXT_A, [claim_a.claim_id])],
        [_d(1), _d(2, "omitted", "redundant_with_selected_claim"),
         _d(3, "omitted", "outside_section_topic")]))
    check(len(narrative_partial.paragraphs[0].sentences) == 1,
          "omit 掉两条 Claim 是合法组织（不是每条 Claim 都必须各自成句）")
    forged = NS.ClaimNarrativeDisposition.create(
        section_id="company", draft_revision="rev-1", claim_id=claim_b.claim_id,
        disposition="selected", reason_code=None)
    expect_error(
        lambda: NS.verify_section_narrative_claims_selected(
            narrative=narrative_partial, claims=claims, dispositions=(forged,)),
        NS.NarrativeSchemaError, "记号 selected 却在正文里一次都没被引用必须被拒",
        needle="一次都没被引用")

    # ============================================================ §4 文本与绑定共用身份
    base = narrative_ctx.paragraphs[0].sentences[0]
    check(NS.NarrativeSentence.create(
        paragraph_id=base.paragraph_id, index=base.index, text=base.text + "另有补充。",
        sentence_kind="composed", claim_ids=base.claim_ids, citation_ids=base.citation_ids,
        context_binding_ids=base.context_binding_ids).sentence_id != base.sentence_id,
        "只改文本也必须改 sentence_id（否则「文本换了」在身份层不可见）")
    check(NS.NarrativeSentence.create(
        paragraph_id=base.paragraph_id, index=base.index, text=base.text,
        sentence_kind="composed", claim_ids=(claim_a.claim_id,),
        citation_ids=base.citation_ids,
        context_binding_ids=base.context_binding_ids).sentence_id != base.sentence_id,
        "只改 Claim 绑定也必须改 sentence_id（否则「绑定换了」在身份层不可见）")
    check(NS.NarrativeSentence.create(
        paragraph_id=base.paragraph_id, index=base.index, text=base.text,
        sentence_kind="composed", claim_ids=base.claim_ids,
        citation_ids=base.citation_ids, context_binding_ids=()).sentence_id
        != base.sentence_id,
        "只改 context 绑定也必须改 sentence_id（context 也是这句话的一部分）")

    # ============================================================ §5 prompt 资产纪律
    asset_text = LLMC.load_prompt(NO.NARRATIVE_ORGANIZER_PROMPT_ASSET)
    declarations = PW._PROMPT_DECL_RE.findall(asset_text)
    check(len(declarations) == 1,
          f"资产自报身份必须恰好一处（实测 {len(declarations)} 处）")
    check(f"{declarations[0][0]}@{declarations[0][1]}" == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
          "资产自报身份必须等于登记身份")
    check(PW.prompt_asset_fingerprint(asset_text) == NO.NARRATIVE_ORGANIZER_PROMPT_SHA256,
          "登记的正文指纹必须等于资产当前正文的指纹")
    # 组织规则版本与资产修订号**不再同源**，而且这件事本身要被钉住。
    # 此前两者同步前进，是因为每一次「判定集变了」都同时要改资产正文。`norg-8`（`orgmc-1`）
    # 打破了这条巧合：本批只给输入面多投一份**只读**材料正文，判定集一个字没改，因此规则版本
    # **必须**留在 `norg-7`——两个版本号回答的是两个不同问题（「同一份输出会不会得到不同裁决」
    # vs 「模型被告知要遵守什么」），把它们锁成相等就等于让其中一个再也答不了自己的问题。
    # 代价是「两个串可以各自漂移」，所以这里改用**双向钉子**：两个都逐字钉住，且断言规则版本
    # 确实是资产正文里那个**更早**的修订号（不是随手写的一个值）。
    check(NO.NARRATIVE_ORGANIZER_PROMPT_REVISION == "norg-8",
          f"资产修订号必须逐字钉住（实得 {NO.NARRATIVE_ORGANIZER_PROMPT_REVISION!r}）")
    check(NO.NARRATIVE_ORGANIZER_RULES_VERSION == "norg-7",
          "组织规则版本必须逐字钉住：本批（`orgmc-1`）只改输入面、不改判定集，"
          f"因此它**留在** `norg-7`（实得 {NO.NARRATIVE_ORGANIZER_RULES_VERSION!r}）")
    check(NO.NARRATIVE_ORGANIZER_RULES_VERSION != NO.NARRATIVE_ORGANIZER_PROMPT_REVISION,
          "两个版本号必须**分开**：它们答的是两个问题（判定集 / 模型被告知什么），"
          "锁成相等会让其中一个再也答不了自己的问题")
    check("norg-7" in asset_text,
          "规则版本对应的那个修订号必须真的在资产正文里出现过（不是凭空写的版本号）")
    NO.verify_organizer_prompt_asset(asset_text)
    passed += 1
    for label, variant in (("CRLF", asset_text.replace("\n", "\r\n")),
                           ("CR", asset_text.replace("\n", "\r"))):
        NO.verify_organizer_prompt_asset(variant)
        check(PW.prompt_asset_fingerprint(variant) == NO.NARRATIVE_ORGANIZER_PROMPT_SHA256,
              f"{label} 检出的同一份资产不得被判成「被换过」（行尾归一）")
    for msg, mutate, needle in (
            ("正文改一个字必须被拒", lambda t: t.replace("正文组织器", "正文组织机"), "正文指纹"),
            ("只加一个尾随换行也必须被拒", lambda t: t + "\n", "正文指纹"),
            ("自报修订号漂移必须被拒", lambda t: t.replace("revision norg-8", "revision norg-7"),
             "自报"),
            ("资产未自报身份必须被拒", lambda t: "你是组织器（没有自报身份）", "")):
        mutated = mutate(asset_text)
        check(mutated != asset_text, f"前置条件：该变异必须真的改动了正文（{msg}）")
        expect_error(lambda m=mutated: NO.verify_organizer_prompt_asset(m),
                     NO.NarrativeOrganizerError, msg, needle=needle)
    check(NO.NARRATIVE_ORGANIZER_PROMPT_ASSET != PW.NARRATION_PROMPT_ASSET,
          "同一份资产不得同时承担门前提案器与门后组织器两种职责")
    # `norg-6`（M930-3 返修 ④）：组织语不得把断言挪到当前时点。这条纪律必须真的写给模型——
    # 判据（`ng-13` 第 12 条）只读既有的句子文本与它声明的 Claim，因此「规则已进代码」不等于
    # 「模型被告知」。缺了这一节，模型只能靠猜，判据就退化成事后罚款。
    for token in ("norg-6", "目前", "当前", "持续", "逐字出现在你这一句声明的 Claim 文本里"):
        check(token in asset_text,
              f"资产必须写明「不得把断言挪到当前时点」这条纪律（缺 {token!r}）")
    check("此前" in asset_text and "曾经" in asset_text,
          "同一节必须点明反向的改时点写法（用「此前」「曾经」把当前断言降格）同样被拒")
    # `norg-7`（M930-3 指令 E 第 3 项）：门前草稿作为表达基础。这条写法必须真的写给模型——
    # 句级闭合判据（`_sentence_specs`）只拦得住「借草稿之名声明别的 Claim」，它拦不住
    # 「模型根本不知道有草稿可用」，而后者会让整条自然写法永远落不了地（正文退回 Claim 拼文）。
    for token in ("norg-7", "prose_unit_id", "prose_draft", "kept", "dropped",
                  "表达基础", "不得新增"):
        check(token in asset_text,
              f"资产必须写明「以门前草稿为表达基础」这条写法（缺 {token!r}）")
    # 两种写法的**选择权**必须写清：不声明出处 = 逐字写法，声明出处 = 保真核验。
    check("只能选一种" in asset_text and "只有写法一可用" in asset_text,
          "资产必须写明两种写法的排他与「草稿层为空时只有逐字写法可用」"
          "（否则模型会凭一张空表去声明出处，或被判据在事后罚一次）")
    # `norg-8`（`orgmc-1`）：材料正文上下文。这条也必须是**写给模型**的——给它材料正文却不告诉
    # 它「这不授权」，等于把一份原文递给一个正在改写正文的模型，然后指望它自己去猜边界。
    for token in ("orgmc-1", "material_context", "readonly", "member_ref",
                  "material_member_refs", "空数组"):
        check(token in asset_text,
              f"资产必须写明「材料正文是只读依据面」这条纪律（缺 {token!r}）")
    check("不授权任何字面" in asset_text or "不授权" in asset_text,
          "同一节必须点明「材料正文不授权任何字面」——只给材料不给边界，等于把越权的诱因"
          "直接递到模型面前")
    check("勾选" in asset_text,
          "同一节必须点明勾选表单行不得当作经营正文用（形态维度与「不得新增事实表面」是同一件事）")
    # 资产自带的输出样例必须真能被解析层接受（prompt 与解析器不得各说一套）。
    anchor = asset_text.index("{", asset_text.index("只输出 JSON"))
    sample, end = json.JSONDecoder().raw_decode(asset_text[anchor:])
    check(NO.parse_organizer_plan(asset_text[anchor:anchor + end]) == sample
          and set(sample) == {"paragraphs", "claim_dispositions"},
          "资产自带的输出样例必须与解析器口径一致")

    # ============================================================ §6 退化路径与缺 client
    expect_error(
        lambda: NO.build_final_narrative(
            claims=claims, accepted_context_bindings=(), unresolved=(),
            writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
            llm_client=None),
        NO.NarrativeOrganizerError, "有 Claim 却没有 llm_client 必须 fail-closed",
        needle="不得被静默跳过")
    # 「既没有 Claim 也没有表格」的判据**不属于**组织器：它由唯一构造器
    # `NS.build_section_narrative` 抛出 `NarrativeSchemaError`（有真实 draft 时的完整反例在
    # `test_demo_writer_formal_chain` 的相位级断言里）。这里只证明组织器**没有**另立一个
    # 错误类型去截胡：同一个调用必须走到构造器自己的校验。
    expect_error(
        lambda: NO.build_final_narrative(
            claims=(), accepted_context_bindings=(), unresolved=(),
            writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
            llm_client=None, tables=(), draft=object()),
        NS.NarrativeSchemaError, "退化路径必须把「零 Claim 零表格」交给唯一构造器判（不得另立错误类型）",
        needle="必须是门前 SectionDraft")
    expect_error(
        lambda: NO.build_final_narrative(
            claims=(), accepted_context_bindings=(), unresolved=(),
            writing_spec=object(), presentation_profile=object(), identity=IDENTITY,
            llm_client=None, tables=(object(),)),
        NO.NarrativeOrganizerError, "退化路径缺 draft 必须 fail-closed（不得猜身份）",
        needle="draft")
    expect_error(
        lambda: _organize(claims, llm=_StubLlm(RuntimeError("provider 5xx"))),
        NO.NarrativeOrganizerError, "调用未成功必须 fail-closed，不得凭旧文本组织",
        needle="没有成功调用就没有正文")
    # §四.5 把 `locator_ref` 的 wire 形状换成 `loc-1` tagged union，因此 schema 版本前进到
    # `narr-6`；规则集与门判定集另有一路（同一份正文在 ng-6/ng-7 下可能一个通过、一个被拒，
    # 不得共用版本号）。§七 1 新增第 8 条判据（composed 句必须真的被组织过），规则集与门判定集
    # 再前进到 nrules-7 / ng-7；P2 §三 2.4 又在同一判定集里加了两条（组合句表面边界感知 +
    # 「不得含句中句末标点」），因此再前进到 nrules-8 / ng-8；⑥ 又加了第 9 条判据（正文不得替
    # 系统自报检索 / 核验）+ 第 8 条的**确定性补救**，判定集再前进到 nrules-9 / ng-9；M930-3
    # §一.1 又改了**共享扫描语义**（主体名 token 与高风险表面词为同一实现，三个使用点都经它），
    # 判据口径变了 ⇒ 规则集再前进到 nrules-10；门判定集**未变**（ng-9 仍在用，正文指纹到 §四 才动）；
    # M930-3 写作主链定点批 §一 修订了「门判定集未变」这条：新增五组高风险封闭标记（`nrules-11`），
    # 而门第 2/3 条读的就是这个标记集，同一份组织段在 ng-9 与 ng-10 下可一通过一被拒 ⇒ 门到 ng-10。
    # 同一批 §二 又给第 8 条加了判据 e「接缝必须以分隔标点起头」（`nrules-12`）：`A此外，B。` 在
    # ng-10 下通过、ng-11 下被拒 ⇒ 门再前进到 ng-11。**wire 未变**，schema 版本仍停在 narr-6
    # （没有新增/删除字段，`sentence_kind` 的取值集也没变）。
    # M930-3 指令三 再给第 8 条加判据 10/11「关系性连接语必须有材料逐字支持」「发行人自述必须有
    # 归属」（`nrules-13`）：`A，另一方面，B。`（材料没说过这个对照）在 ng-11 下通过、ng-12 下被拒
    # ⇒ 门第三次前进到 ng-12。**wire 仍未变**：两条判据读的都是**既有**句子文本与既有 `claim_ids`，
    # `redundancy_candidates` 只是输入面的**线索**（与 `theme_key` 同性质），逐对裁决的载体仍是
    # 既有 `claim_dispositions`——没有新增字段、没有改字段形状，schema 版本必须继续停在 narr-6。
    # M930-3 返修 ④ 再给门加第 12 条「组织语不得把断言挪到当前时点」（`nrules-14`）：`A，此外，
    # 公司目前<B>`（「目前」材料没写过）在 ng-12 下通过、ng-13 下被拒 ⇒ 门第四次前进到 ng-13。
    # **wire 仍未变**：这一条读的仍是既有句子文本与既有 `claim_ids`，判据只扫**组织段**，
    # 没有新增字段、没有改字段形状，schema 版本必须继续停在 narr-6。
    # M930-3 指令 E 第 3 项：这一批**破了**上面连续五次的「wire 未变」——`SectionDraft` 新增
    # `natural_prose_draft`，`SENTENCE_KINDS` 新增 `natural`，且草稿非空时它进 `draft_revision`。
    # 因此 schema 版本必须前进到 `narr-7`（形状可变而 marker 不变 = 让 reader 去猜），门与规则集
    # 各前进到 `ng-14` / `nrules-15`（第 8 条按句类分派：`composed` 逐字、`natural` 保真）。
    # M930-3 定点返修 P1-B：这一批**再破一次**「wire 未变」——草稿单元的出处从**一条**材料轴拆成
    # 两条互斥轴（`source_member_refs` / `source_fact_refs`，`pprov-1`）。形状又变了 ⇒ marker 必须
    # 再前进一版到 `narr-8`（同上：形状可变而 marker 不变就是让 reader 去猜）。**门与规则集不动**：
    # 两条轴读的都是既有文本与既有 `claim_ids`，`source_fact_refs` 只是「这段话从哪来」的登记格，
    # 不新增判定、不改任何规则的判据 ⇒ `nrules-15` / `ng-14` 如实保留。
    # M930-3 定点业务闭环批 §二 2：**wire 仍未变**，动的是另一条并行的维度——第 12 条所用的
    # **封闭标记集**新增普遍化组（`一直`/`始终`/`历来`/`向来`/`一向`/`一贯`/`素来`/`从来`）与
    # 否定式普遍化组（`从未`/`未曾`）。第 12 条的判据实现一字未改，但判定集变了：`A此外公司
    # 一直<B>` 在 `nrules-15` 下通过、`nrules-16` 下被拒 ⇒ 规则集与门各前进到 `nrules-16` /
    # `ng-15`，schema 版本继续停在 `narr-8`（没有新增/删除字段，句子类别取值集也没变）。
    # M930-3 主营业务质量返修批 §四：**wire 仍未变**，动的是主体名抽取的判据实现
    # （剥前缀改不动点 + 整串即时间状语的字号不产出 + 「时间状语 + 自称」那一格）。
    # 判定集两度收窄 ⇒ 规则集与门各前进到 `nrules-18` / `ng-17`，schema 版本继续停在 `narr-8`
    # （没有新增/删除字段，句子类别取值集也没变）。
    # M930-3 `ndc-2` 批：实现仍一字未改，动的是**支撑边的材料判别集**——同一事实的已核验输入
    # 材料先把材料候选收窄（`mbind-1`），`ng-17` 下命中 `support_material_ambiguous` 的正文在
    # `ng-18` 下通过 ⇒ 只门前进到 `ng-18`（规则集与 schema 版本都不动）。
    check(NS.NARRATIVE_SCHEMA_VERSION == "narr-8" and NS.NARRATIVE_RULES_VERSION == "nrules-18"
          and NS.NARRATIVE_GATE_VERSION == "ng-18",
          "草稿出处第二条轴进 wire 必须带 schema 版本：narr-8 + nrules-18/ng-18")
    check(NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS
          == ("narr-3", "narr-4", "narr-5", "narr-6", "narr-7"),
          "旧版必须保留为**只读**可审计版本，而不是被新版覆盖（narr-7 是本批新登记的 legacy）")
    check("natural" in NS.SENTENCE_KINDS and len(NS.SENTENCE_KINDS) == 4,
          f"`sentence_kind` 取值集必须含第四种 `natural`：实际 {NS.SENTENCE_KINDS!r}")
    check(hasattr(NS, "load_legacy_narrative_narr6_for_audit"),
          "narr-6 载荷必须有**自己的单版本**只读 reader（冻结 run 工件全是 narr-6）")
    check(hasattr(NS, "load_legacy_narrative_narr7_for_audit"),
          "narr-7 载荷必须有**自己的单版本**只读 reader（草稿层已在场、但出处只有材料一条轴）")
    # `pwr-4`（M930-3 §四 读者面）：渲染口径**确实**动了，但动因不是 narr-6——locator wire 升版
    # 只进决定与门，渲染器一个字都不消费它（上面这条判断仍然成立）。§四 改的是另外三处**人读**
    # 口径：单位按 `reader_unit_text` 与单元格显示一致、代理口径补中文说明、可见缺口不回显内部字段。
    # 三者都只改渲染字节，不改表格元数据，也不改任何判定集——因此当时 `ng-9` 如实保留（判据未变），
    # 版本责任落在渲染器上。**定点批 §一 之后 ng-9 已不再成立**（见上：标记集动了），
    # `pwr-4` 这条判断本身不受影响——渲染器仍不消费 locator_ref，也仍不消费标记集。
    # `pwr-5`（M930-3 定点批 §四 财务来源读回）：渲染口径再动一次——段落与表格各多一行
    # `- 来源：…`，口径名与中文期间取自权威自己的公式登记表与期间口径。动因**同样不是**
    # narr-6：渲染器仍不消费 `locator_ref`、也不消费任何判定标记集，它消费的是 Claim 自己的
    # `CitationRef`（`formula_id` / `formula_version` / `period`）。
    check(PW.WRITER_RENDERER_VERSION == "pwr-5",
          "渲染字节变了就必须前进渲染器版本（`pwr-5`）；同时确认它**不是**被 narr-6 带动的："
          "渲染器不消费 locator_ref，定位只进决定与门")

    # ============================================================ §7 跨页材料支撑的多 Claim 自然段
    # r7b 公司节的现场形态：路径 A 为零，经营模式一组只有两页**跨页**材料（p15 起 / p16 续）。
    # 采购 / 生产 / 销售三条 Claim 的引用分别落在两页上，它们必须能组织进**同一个自然句**——
    # 「多 Claim 自然段」在这里是可判定的形态（composed 句 + 逐字按序在场的 Claim 文本 +
    # 只插在它们**之间**的组织语），而不是一句 574 字长串。
    TXT_PROC = "公司采购通过长期协议与全球供应商合作"
    TXT_PROD = "公司生产以自建生产基地为主"
    TXT_SALES = "公司销售以直销为主"
    claim_proc = _claim(TXT_PROC, TOPIC_A, index=11, evidence="evidence:co-bm-p15")
    claim_prod = _claim(TXT_PROD, TOPIC_A, index=12, evidence="evidence:co-bm-p16")
    claim_sales = _claim(TXT_SALES, TOPIC_A, index=13, evidence="evidence:co-bm-p15")
    bm_claims = (claim_proc, claim_prod, claim_sales)
    check(len({str(c.citation_refs[0].evidence_id) for c in bm_claims}) == 2,
          "聚焦前提：三条 Claim 的引用**恰好落在两页材料**上（跨页，不是同一页的三段）")

    bm_text = TXT_PROC + "，" + TXT_PROD + "，同时，" + TXT_SALES + "。"
    bm_plan = _plan(
        [_sentence(bm_text, [c.claim_id for c in bm_claims])],
        [{"claim_id": c.claim_id, "disposition": "selected", "reason_code": None}
         for c in bm_claims])
    bm_narrative, _bm_trace = _organize(bm_claims, plan=bm_plan)
    bm_sentence = bm_narrative.paragraphs[0].sentences[0]
    check(str(bm_sentence.sentence_kind) == "composed"
          and len(bm_sentence.claim_ids) == 3,
          "跨页材料支撑的采购 / 生产 / 销售必须能组织成**一条**多 Claim 自然句"
          f"（实际 kind={bm_sentence.sentence_kind!r} claims={len(bm_sentence.claim_ids)}）")
    check(str(bm_sentence.text) == bm_text and len(bm_narrative.paragraphs) == 1,
          "多 Claim 自然段必须是**一整段一句话**（组织语只插在 Claim 之间，句末标点至多一个）")
    check(not NS.has_internal_sentence_terminator(str(bm_sentence.text)),
          "多 Claim 自然句不得含句中句末标点（`A。B。` 不是一句话）")

    # 反例方向一：把三条 Claim 用标点首尾相接**不是**「多 Claim 自然段」。§3.6 已经钉住判据侧
    # 对机械拼接的判定与两级补救；这里要钉的是**业务结果**：这类计划绝不允许以「一条三 Claim
    # 组成句」的形态落地——机器门看到的若是「有多 Claim 自然句」，读者看到的其实是清单。
    raw_join_text = TXT_PROC + "，" + TXT_PROD + "，" + TXT_SALES + "。"
    check(NS.composed_organization_defect(
              text=raw_join_text, authorized_texts=(TXT_PROC, TXT_PROD, TXT_SALES))
          == NS.COMPOSED_DEFECT_MECHANICAL_JOIN,
          "三条 Claim 用「，」首尾相接必须被判为机械拼接（判据侧）")
    raw_join_plan = _plan(
        [_sentence(raw_join_text, [c.claim_id for c in bm_claims])],
        [{"claim_id": c.claim_id, "disposition": "selected", "reason_code": None}
         for c in bm_claims])
    raw_narrative, raw_trace = _organize(bm_claims, plan=raw_join_plan)
    raw_sentences = list(raw_narrative.paragraphs[0].sentences)
    check(raw_trace["reorganization_remedies"] == [NO.REORGANIZATION_PER_CLAIM_FACTUAL]
          and len(raw_sentences) == 3
          and all(s.sentence_kind == "factual" and len(s.claim_ids) == 1
                  for s in raw_sentences),
          "机械拼接不得以「一条多 Claim 自然句」落地：必须被补救成逐条事实句（实测 "
          f"{raw_trace.get('reorganization_remedies')!r} / "
          f"{[s.sentence_kind for s in raw_sentences]!r}）")
    check([s.text for s in raw_sentences]
          == [NS.render_factual_text([t]) for t in (TXT_PROC, TXT_PROD, TXT_SALES)],
          "补救后的三句只能是 Claim 文本的逐字渲染（不得改写或补写事实表面）")

    # 反例方向二：一句塞进超过上界的 Claim 条数必须被拒（「多」有上界，不是越多越好）。
    # 上界 = MAX_COMPOSED_CLAIMS_PER_SENTENCE，因此这里补到上界 + 1 条，且各条文本互不相同
    # （避免同一文本出现两次时「按序逐字」落在哪一处变得不可判）。
    TXT_COST = "公司成本受上游原材料价格波动影响"
    TXT_CAPACITY = "公司储能业务处于产能爬坡阶段"
    claim_cost = _claim(TXT_COST, TOPIC_A, index=14, evidence="evidence:co-bm-p15")
    claim_capacity = _claim(TXT_CAPACITY, TOPIC_A, index=15, evidence="evidence:co-bm-p16")
    over_claims = bm_claims + (claim_cost, claim_capacity)
    check(len(over_claims) == NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE + 1,
          "聚焦前提：反例句比上界恰好多一条 Claim")
    over_text = (TXT_PROC + "，" + TXT_PROD + "，同时，" + TXT_SALES
                 + "，另外，" + TXT_COST + "，并且，" + TXT_CAPACITY + "。")
    over_plan = _plan(
        [_sentence(over_text, [c.claim_id for c in over_claims])],
        [{"claim_id": c.claim_id, "disposition": "selected", "reason_code": None}
         for c in over_claims])
    check(NS.composed_organization_defect(
              text=over_text, authorized_texts=tuple(c.text for c in over_claims))
          == NS.COMPOSED_DEFECT_OVER_CLAIM_BOUND,
          f"一句承载超过 {NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE} 条 Claim 必须被判为上界违规")
    # 这一形态**不得**被补救：接缝上是模型自己写的组织语（`同时，`/`另外，`/`并且，`），第二级
    # 补救（各自成句）只对「Claim 文本 + 纯标点接缝」生效——否则丢掉接缝会把「一条塞了 5 条的
    # 长句」洗成一份看不出问题的正文。因此它按原样 fail-closed：这就是「上界」这条判据的用处。
    expect_error(
        lambda: _organize(over_claims, plan=over_plan),
        NS.NarrativeSchemaError,
        f"一句承载超过 {NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE} 条 Claim 且接缝带组织语，"
        "必须 fail-closed（不得靠补救洗成合法正文）",
        needle="上界")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

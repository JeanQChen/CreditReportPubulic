"""M930-3.2/3.3 反例集：验收 runner 的**判据本身**必须会拒，而不是只会在真实样本上判绿。

本模块只测 `evaluation.run_m930_3_acceptance` 里的判据函数（A1 的两个子门、A2 的可读结构门、
A4 的整本读回重组、A5 的逐句零裸事实复算与身份分层）。它**不**读真实库、不建真实 Pack、
不调 LLM：夹具是同一批判据在真实链上会读到的那些形状（`SectionClaim` / `SectionNarrative` /
accepted binding / 财务 authority artifact），wire 层对象用**真类**构造
（`NarrativeParagraph.create` 等），只有判据按属性名读取的产物容器用 `_NS` 替身。

覆盖的假绿（§八「修复验收假绿」）：

1. **A1 子门必须分别可见，且拒 raw join**：「≥1 条合法路径 B Claim」与「≥1 个多 Claim 自然
   段落」是两个分别可见的子门；子门红 ⇒ 门红，不得混成一句 detail；
2. **A2 的「可读」是结构门**：`body_chars > 0` 不是可读性；正文句不得是 Claim 文本的机械拼接，
   也不得出现未由所声明 Claim 授权的表面（含**趋势结论**）；表格必须是「指标 × ≥2 期间」、
   单元格按非空顺序逐位配对其 Claim、逐字来自该 Claim、期间与该 Claim 的权威事实一致；
3. **A5 逐句复算覆盖 composed**：真实样本正文全是 composed，只统计 factual 句会让这一门空通过。
   composed 句承载的原子与 factual 句同级：Claim 是否 current、是否有 factual accepted binding、
   aggregate 与蕴含决定是否在本节决定集内、citation 是否由所声明 Claim 确定性派生、composed
   文本是否新增未授权高风险表面、是否有未知/重复/遗漏的 Claim。
4. **A4 的「整本读回重组」不得空通过**：`skipped`（没跑过）与 `pass`（跑过且相同）是两回事。
   门把任何非 `pass` 记成问题，否则「报告从未组装成功」的一次运行能白拿 A4 绿门；
5. **A5 的身份分层**（§四 1 / §4.6）：补件运行是**操作事件**，不得改写冻结的策略依赖指纹、
   不得把自己的运行 ID 泄进 Draft 的内容身份，也不得让后继 Pack 身份消失；两次有界执行的
   trace ref 必须各自指向自己的 `follow_up_run_id`，不得折叠成同一串。每个反例都配一个
   **正向对照**，证明这些判据不会把合法现场误判为红。
6. **A5 的版本身份面**（§四 1）：`ReportVersionInputs` 的字段集合本身不得有承载「哪一次补件
   执行」的位置（否则换运行 ID 就会漂移内容版本），而报告版本里混进旧 Pack、声明集少一个
   Pack、报告级容器清单漏包、报告版本根被改写、本轮没有实得报告，都必须为红；同样配正向对照。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_acceptance_gates`
"""
from __future__ import annotations

import ast
import builtins
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from evaluation import run_m930_3_acceptance as ACC
from harness import runtime as HRT
from harness import schema as HS
from harness import source_manifest as SM
from sections import backbone_schema as BS
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import report_assembler as RA
from sections import schema as SS

#: 夹具使用的 topic：「同一主题」判据要求它稳定。
TOPIC = "financial_metrics"


class _NS:
    """只读命名空间替身（判据只按属性名读取产物容器，不要求真 Pack/真库对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _citation(evidence_id: str = "ev1") -> HS.CitationRef:
    return HS.CitationRef(ref_type="evidence", evidence_id=evidence_id, page_number=None)


def _claim(text: str, *, topic_id: str = TOPIC, section_id: str = "financial",
           binding_ids: tuple[str, ...] = ("asb_1",), evidence_id: str = "ev1",
           candidate_id: str = "ccand_1") -> SS.SectionClaim:
    refs = (_citation(evidence_id),)
    bindings = tuple(binding_ids)
    claim_id = SS.derive_claim_id("fact", topic_id, ("q1",), text, refs,
                                  candidate_id, "dr-1", bindings)
    return SS.SectionClaim(claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION,
                           section_id=section_id, topic_id=topic_id, question_ids=("q1",),
                           text=text, claim_type="fact", citation_refs=refs,
                           claim_candidate_id=candidate_id, claim_candidate_revision="dr-1",
                           accepted_binding_ids=bindings)


def _cit_ids(claim: SS.SectionClaim) -> tuple[str, ...]:
    return NS.claim_citation_ids(claim)


def _binding(binding_id: str, *, kind: str = "financial_pack", container: str = "art-1",
             fact_id: str | None = "item_revenue_2024", semantics: str = "factual",
             path: str = "path_a_prevalidated", binding_decision_id: str | None = "cbd-1",
             entailment_decision_id: str | None = "ced-1") -> _NS:
    """accepted binding 替身。

    字段名与 `NS.proposal_fact_key` / `NS.binding_fact_key` 读取的字段一致，使事实坐标复算走
    与生产链**同一条**实现。binding 自己的类型层不变量（路径 A 必带两个决定、路径 B 不得携带
    事实身份等）在 `test_demo_accepted_binding.py` 里证明；本模块只验判据逻辑。
    """
    kwargs = {"accepted_support_binding_id": binding_id, "authority_kind": kind,
              "authority_container_id": container, "material_id": None,
              "support_semantics": semantics, "authorization_path": path,
              "source_identity": "src1", "payload_ref": None, "locator_ref": None,
              "fact_id": None, "financial_fact_id": None, "note_fact_id": None,
              "external_fact_id": None,
              "binding_decision_id": binding_decision_id,
              "entailment_decision_id": entailment_decision_id}
    kwargs[NS.FACT_FIELD_BY_AUTHORITY_KIND[kind]] = fact_id
    return _NS(**kwargs)


def _fact(fact_id: str, *, period: str, display: str, label: str = "营业收入",
          code: str = "revenue", unit: str = "元", period_basis: str = "flow",
          status: str = "available", note: str = "") -> _NS:
    """一条被选中财务事实的替身（形状对齐当前 `ffpa-2` 投影）。

    `period_basis` 缺省 `flow`：营业收入是**利润表科目**（期间量）——`ffpa-2` 起每条事实都自己
    携带期间口径，验收侧据此核验列名不是裸期间末日。这里必须照实声明，否则本夹具模拟的就不是
    当前权威产物的形状。`period_label` 取与生产侧 `period_basis.period_expression` 同一规则下的
    表达（本夹具的期间记号 `2024年` 不是 `YYYY-MM-DD` 形状，按该规则**逐字保留原记号**）。
    """
    return _NS(fact_id=fact_id, kind="item", code=code, period=period, display=display,
               value_text=display, label=label, unit=unit,
               period_basis=period_basis, period_label=period,
               status=status, note=note, reason_code=None,
               text=f"{label}{period}为{display}{unit}")


def _authority(*, artifact_id: str = "art-1", periods=("2024年", "2023年"), facts=()) -> _NS:
    return _NS(artifact=_NS(artifact_id=artifact_id, periods=tuple(periods),
                            facts=tuple(facts)))


def _paragraph(section_id: str, specs, *, topic_ids=(TOPIC,), index: int = 0):
    return NS.NarrativeParagraph.create(section_id=section_id, topic_ids=topic_ids,
                                        index=index, sentence_specs=specs)


def _table(section_id: str, *, header, row_specs, caption: str = NS.FINANCIAL_TABLE_CAPTION,
           index: int = 0, unit: str = "元", entity_scope: str = "300750") -> NS.NarrativeTable:
    """建表：行级 `unit`/`period` 由表级补齐（含数字的行在 wire 层强制二者非空）。"""
    period = "、".join(str(h) for h in header[1:])
    specs = [{"unit": unit, "period": period, **dict(spec)} for spec in row_specs]
    return NS.NarrativeTable.create(section_id=section_id, topic_ids=(TOPIC,), index=index,
                                    caption=caption, header=header, row_specs=specs,
                                    entity_scope=entity_scope, unit=unit, period=period)


def _disposition(claim_id: str, disposition: str, reason_code: str | None = None):
    return NS.ClaimNarrativeDisposition.create(section_id="financial", draft_revision="dr-1",
                                               claim_id=claim_id, disposition=disposition,
                                               reason_code=reason_code)


def _section(*, section_id: str = "financial", claims=(), bindings=(), paragraphs=(), tables=(),
             dispositions=(), aggregate_decisions=(), entailment_decisions=()) -> _NS:
    narrative = NS.SectionNarrative.create(
        task_id="task-1", section_id=section_id, section_draft_id="sdraft-1",
        draft_revision="dr-1", paragraphs=tuple(paragraphs), tables=tuple(tables))
    return _NS(section_id=section_id, claims=tuple(claims), narrative=narrative,
               acceptance=_NS(accepted_bindings=tuple(bindings)),
               claim_narrative_dispositions=tuple(dispositions),
               aggregate_decisions=tuple(aggregate_decisions),
               entailment_decisions=tuple(entailment_decisions))


def _pair() -> tuple[SS.SectionClaim, SS.SectionClaim, tuple[_NS, _NS]]:
    """一对「同一指标 × 两个期间」的财务 Claim + 它们的路径 A 边。"""
    c24 = _claim("营业收入2024年为1,234元", binding_ids=("asb-24",))
    c23 = _claim("营业收入2023年为1,000元", binding_ids=("asb-23",))
    return c24, c23, (_binding("asb-24", fact_id="item_revenue_2024"),
                      _binding("asb-23", fact_id="item_revenue_2023"))


def _two_period_facts() -> tuple[_NS, ...]:
    return (_fact("item_revenue_2024", period="2024年", display="1,234"),
            _fact("item_revenue_2023", period="2023年", display="1,000"))


def _table_row(c24, c23, *, cells=None, label: str = "营业收入"):
    return {"label": label, "cells": cells or ("1,234", "1,000"),
            "claim_ids": (c24.claim_id, c23.claim_id),
            "citation_ids": (*_cit_ids(c24), *_cit_ids(c23))}


def _good_financial_section() -> _NS:
    """§七 2 正向夹具：重复的「指标 × 期间」由**表格**承载（不另设事实句）。"""
    c24, c23, bindings = _pair()
    table = _table("financial", header=(NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年", "2023年"),
                   row_specs=[_table_row(c24, c23)])
    return _section(claims=(c24, c23), bindings=bindings, tables=(table,),
                    dispositions=(_disposition(c24.claim_id, "omitted", "presented_as_table_row"),
                                  _disposition(c23.claim_id, "omitted",
                                               "presented_as_table_row")))


def _prose_financial_section(*, text: str | None = None) -> _NS:
    """同一对财务 Claim 用**正文**呈现（用于正文判据的正/反例）。"""
    c24, c23, bindings = _pair()
    body = text if text is not None else f"{c24.text}，此外，{c23.text}"
    paragraph = _paragraph("financial", [{"text": body, "sentence_kind": "composed",
                                          "claim_ids": (c24.claim_id, c23.claim_id),
                                          "citation_ids": (*_cit_ids(c24), *_cit_ids(c23))}])
    return _section(claims=(c24, c23), bindings=bindings, paragraphs=(paragraph,),
                    dispositions=(_disposition(c24.claim_id, "selected"),
                                  _disposition(c23.claim_id, "selected")))


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

    def check_problem(rows, needle: str, msg: str) -> None:
        joined = "｜".join(str(r) for r in rows)
        check(needle in joined, f"{msg}（判据须命中 {needle!r}；实际 {joined[:220]!r}）")

    c24, c23, _bindings = _pair()
    c24_ids, c23_ids = _cit_ids(c24), _cit_ids(c23)
    check(c24.claim_id != c23.claim_id and c24_ids != c23_ids,
          "夹具前提：两条 Claim 及其 citation ID 必须互不相同（否则下面的复算无意义）")

    # ------------------------------------------------------------------
    # §七 1 / §八 A1：组织判据 —— raw join、乱序、超上界
    # ------------------------------------------------------------------
    raw = _paragraph("financial", [{"text": f"{c24.text}；{c23.text}", "sentence_kind": "composed",
                                    "claim_ids": (c24.claim_id, c23.claim_id),
                                    "citation_ids": (*c24_ids, *c23_ids)}])
    audit = ACC._natural_organization_audit(_section(claims=(c24, c23), paragraphs=(raw,)))
    check(audit["multi_claim_count"] == 1 and audit["natural_count"] == 0,
          "A1.2：`A；B` 式机械拼接不得算作自然组织")
    check(audit["sentences"][0]["raw_join"] is True,
          "A1.2：机械拼接必须被显式标记 raw_join")
    check(audit["sentences"][0]["claims_verbatim_in_order"] is True,
          "A1.2：raw join 的 Claim 文本确实按序在场（因此拒它的理由只能是「没组织」）")

    joined = _paragraph("financial", [{"text": f"{c24.text}，此外，{c23.text}",
                                       "sentence_kind": "composed",
                                       "claim_ids": (c24.claim_id, c23.claim_id),
                                       "citation_ids": (*c24_ids, *c23_ids)}])
    audit = ACC._natural_organization_audit(_section(claims=(c24, c23), paragraphs=(joined,)))
    check(audit["natural_count"] == 1 and audit["sentences"][0]["raw_join"] is False,
          "A1.2：带汉字组织成分的句子必须算作自然组织（正向不得误拒）")

    reordered = _paragraph("financial", [{"text": f"{c23.text}，此外，{c24.text}",
                                          "sentence_kind": "composed",
                                          "claim_ids": (c24.claim_id, c23.claim_id),
                                          "citation_ids": (*c24_ids, *c23_ids)}])
    audit = ACC._natural_organization_audit(_section(claims=(c24, c23), paragraphs=(reordered,)))
    check(audit["sentences"][0]["claims_verbatim_in_order"] is False
          and audit["natural_count"] == 0,
          "A1.2：声明顺序与正文顺序不一致必须判为「绑定与正文不一致」")

    extra = tuple(_claim(f"营业收入202{x}年为1,2{x}3元", binding_ids=(f"asb-x{x}",))
                  for x in range(5))
    over = _paragraph("financial", [{"text": "，此外，".join(c.text for c in extra),
                                     "sentence_kind": "composed",
                                     "claim_ids": tuple(c.claim_id for c in extra),
                                     "citation_ids": tuple(
                                         cid for c in extra for cid in _cit_ids(c))}])
    audit = ACC._natural_organization_audit(_section(claims=extra, paragraphs=(over,)))
    check(audit["sentences"][0]["over_claim_bound"] is True and audit["natural_count"] == 0,
          f"A1.2：一句承载超过 {NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE} 条 Claim 必须判超上界")

    # ------------------------------------------------------------------
    # §八 A1：两个子门分别可见；子门红 ⇒ 门红
    # ------------------------------------------------------------------
    gate = ACC._gate("A1", "标题", [], {"probe": True},
                     sub_gates=(ACC._sub_gate("A1.1", "授权面", [], {}),
                                ACC._sub_gate("A1.2", "组织面", ["机械拼接"], {})))
    check(gate.status == "fail", "子门红必须使门红（绿门里不得藏红子门）")
    check("机械拼接" in gate.detail and "A1.2" in gate.detail,
          "子门失败原因必须带子门号进入门级 detail")
    check([s["sub_gate_id"] for s in gate.to_dict()["sub_gates"]] == ["A1.1", "A1.2"],
          "A1 的两个子门必须分别出现在产物里")

    # A1 的门级计数与数字复算不是本模块的对象：把数字复算换成纯 stub，使断言只针对子门结构。
    real_numbers = ACC._numbers_recomputed_from_authority
    ACC._numbers_recomputed_from_authority = lambda *a, **k: {
        "numbers_checked": 0, "unauthorized": [], "unauthorized_count": 0}
    try:
        a1 = ACC._gate_a1_company(
            _NS(section_id="company",
                draft=_NS(proposed_support_refs=(_NS(),), claim_candidates=(_NS(),),
                          material_manifest=_NS(entries=(_NS(member_ref="wmmref-1"),)),
                          writer_rules_version="wrules-x", writer_renderer_version="wrender-x"),
                aggregate_decisions=(_NS(binding_decision_id="cbd-1"),),
                entailment_decisions=(_NS(entailment_decision_id="ced-1"),),
                acceptance=_NS(accepted_bindings=(_binding("asb-24"), _binding("asb-23"))),
                claims=(c24, c23),
                narrative=_section(claims=(c24, c23), paragraphs=(raw,)).narrative,
                result=_NS(markdown="x"),
                gate_result=_NS(gate_version="ng-x", blocking=False),
                persistence={"committed": True}),
            _NS(pack_set=None, artifact=_NS()), _NS(topic_ids=("t",), section_id="company"),
            None)
    finally:
        ACC._numbers_recomputed_from_authority = real_numbers
    sub_ids = [s["sub_gate_id"] for s in a1.sub_gates]
    check(sub_ids == ["A1.1", "A1.2"], f"A1 必须产出两个子门（实际 {sub_ids}）")
    check(a1.sub_gates[1]["status"] == "fail",
          "A1.2 必须因机械拼接为红（不得因为「有两条 Claim 的句子」就判绿）")
    check("A1.2" in a1.detail and "没有「同一主题下 ≥2 条合法描述性原子自然组织成一句」" in a1.detail,
          "A1 的门级 detail 必须点名红子门 A1.2 与它的理由")
    excluded = [r for row in a1.sub_gates[1]["evidence"]["natural_multi_claim_sentences"]
                for r in row["excluded_by"]]
    check(any("机械拼接" in r for r in excluded),
          f"A1.2 的逐句排除原因里必须出现机械拼接（实际 {excluded}）")
    check(a1.status == "fail", "子门红时 A1 必须整体为红")

    # ------------------------------------------------------------------
    # §八 A3（acc-27）：验收侧**独立重算**「正文替系统自报检索 / 核验」
    # ------------------------------------------------------------------
    # A3 的证据块用的是这份独立读数，不是生产侧判据的返回值；两侧各有一份封闭词表，
    # 因此这里必须同时钉住「两份副本没有漂移」与「读数的两侧行为」。
    check(ACC._SYSTEM_PROVENANCE_PHRASES == NS.SYSTEM_PROVENANCE_PHRASES,
          "验收侧的系统自报词表必须与判据侧逐字相同（两份独立副本，漂移即静默失效）")
    checkbox = "2）已签订的重大采购合同截至本报告期的履行情况 □适用 √不适用"
    c_ck = _claim(checkbox, binding_ids=("asb-ck",), candidate_id="ccand-ck")
    c_ck_ids = _cit_ids(c_ck)
    c_ck_para = _paragraph("financial", [{"text": checkbox, "sentence_kind": "factual",
                                          "claim_ids": (c_ck.claim_id,),
                                          "citation_ids": c_ck_ids}])
    check(ACC._self_reported_provenance(
        _section(claims=(c_ck,), paragraphs=(c_ck_para,)).narrative, (c_ck,)) == [],
          "A3 独立读数：**原文转录**的勾选表单行（含 `□适用 √不适用`）不是系统自报，不得计入")
    c_web = _claim("公司披露称该数据来自互联网公开资料并已核验",
                   binding_ids=("asb-web",), candidate_id="ccand-web")
    c_web_para = _paragraph("financial", [{"text": c_web.text, "sentence_kind": "factual",
                                           "claim_ids": (c_web.claim_id,),
                                           "citation_ids": _cit_ids(c_web)}])
    check(ACC._self_reported_provenance(
        _section(claims=(c_web,), paragraphs=(c_web_para,)).narrative, (c_web,)) == [],
          "A3 独立读数：词表里的词**出现在本句声明的 Claim 文本里**时不得计入"
          "（判的是「谁在自报」，不是「出现过哪些字」）")
    self_report = f"{c24.text}本系统已独立联网核验，{c23.text}"
    sr_para = _paragraph("financial", [{"text": self_report, "sentence_kind": "composed",
                                        "claim_ids": (c24.claim_id, c23.claim_id),
                                        "citation_ids": (*c24_ids, *c23_ids)}])
    hits = ACC._self_reported_provenance(
        _section(claims=(c24, c23), paragraphs=(sr_para,)).narrative, (c24, c23))
    check(len(hits) == 1 and set(hits[0]["phrases"]) == {"联网", "核验", "本系统"}
          and hits[0]["sentence_id"] == sr_para.sentences[0].sentence_id,
          f"A3 独立读数：接缝上替系统自报检索 / 核验必须被点名（句子 + 命中的词，实际 {hits}）")

    # ------------------------------------------------------------------
    # §七 2 / §八 A2：可读性结构门
    # ------------------------------------------------------------------
    authority = _authority(facts=_two_period_facts())
    readability = ACC._financial_readability(_good_financial_section(), authority)
    check(readability["problem_count"] == 0,
          f"A2 正向：指标 × 期间表格必须通过可读结构门（实际 {readability['problems'][:3]}）")

    readability = ACC._financial_readability(_section(), _authority())
    check_problem(readability["problems"], "既没有段落也没有表格",
                  "A2：零段落零表格必须为红（`body_chars > 0` 不是可读性）")

    readability = ACC._financial_readability(_prose_financial_section(), authority)
    check(readability["problem_count"] == 0,
          f"A2：有组织的正文句必须通过（实际 {readability['problems'][:3]}）")

    readability = ACC._financial_readability(
        _prose_financial_section(text=f"{c24.text}；{c23.text}"), authority)
    check_problem(readability["problems"], "机械拼接", "A2：正文机械拼接必须为红")

    readability = ACC._financial_readability(
        _prose_financial_section(text=f"营业收入同比上升，{c24.text}，此外，{c23.text}"),
        authority)
    check_problem(readability["problems"], "未由所声明 Claim 逐字授权",
                  "A2：没有预验证趋势事实就写「同比上升」必须为红")

    thin = _table("financial", header=(NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年"),
                  row_specs=[{"label": "营业收入", "cells": ("1,234",),
                              "claim_ids": (c24.claim_id,), "citation_ids": c24_ids}])
    readability = ACC._financial_readability(
        _section(claims=(c24,), bindings=(_binding("asb-24"),), tables=(thin,),
                 dispositions=(_disposition(c24.claim_id, "omitted", "presented_as_table_row"),)),
        authority)
    check_problem(readability["problems"], "不是「指标 + ≥2 期间」",
                  "A2：期间列不足 2 列的表必须为红")

    wrong = _table("financial", header=(NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年", "2023年"),
                   row_specs=[_table_row(c24, c23, cells=("9,999", "1,000"))])
    readability = ACC._financial_readability(
        _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                 tables=(wrong,),
                 dispositions=(_disposition(c24.claim_id, "omitted", "presented_as_table_row"),
                               _disposition(c23.claim_id, "omitted",
                                            "presented_as_table_row"))), authority)
    # 单元格携带的数值不是它配对的那个事实 → 两条各自独立判红：输出侧（那一格不等于权威可见
    # 文本）+ 分量侧（权威事实的数值没有逐字出现在配对 Claim 的文本里，C4）。本夹具里
    # `_binding` 的 `fact_id` 缺省绑 2024 事实，因此 `c23`（2023 那一格）配的正是 2024 事实 ——
    # 分量判据恰在这一格上也被验证到。
    check_problem(readability["problems"], "不等于该权威事实应有的可见文本",
                  "A2：单元格数值不在其配对 Claim 文本里必须为红（表格不得携带 LLM 计算值）")
    check_problem(readability["problems"], "配对 Claim",
                  "A2：同一格还必须过**分量**判据（权威事实的数值须逐字出现在配对 Claim 文本里）")

    # 期间错位：**值都对**（每一格都逐字等于它配对事实的权威可见文本），唯一的问题是权威事实被
    # 放在了**别的期间列**下。这正是「单元格是 Claim 的子串」抓不到的那一类错 —— 列名与事实期间
    # 必须各自对上（M930-3 返修 P3 起还要求列名是**期间表达**而不是裸期间末日）。
    # 本节的两个边都显式绑到**各自期间**的事实，否则上面那条前提会被夹具自身的错绑推翻。
    shifted = _table("financial", header=(NS.FINANCIAL_TABLE_LABEL_HEADER, "2023年", "2024年"),
                     row_specs=[_table_row(c24, c23, cells=("1,234", "1,000"), label="营业收入")])
    readability = ACC._financial_readability(
        _section(claims=(c24, c23),
                 bindings=(_binding("asb-24", fact_id="item_revenue_2024"),
                           _binding("asb-23", fact_id="item_revenue_2023")),
                 tables=(shifted,),
                 dispositions=(_disposition(c24.claim_id, "omitted", "presented_as_table_row"),
                               _disposition(c23.claim_id, "omitted",
                                            "presented_as_table_row"))), authority)
    check_problem(readability["problems"], "与该事实期间",
                  "A2：单元格所在列期间与其权威事实期间不一致必须为红（期间错位）")
    check(not any("不等于该权威事实应有的可见文本" in p for p in readability["problems"])
          and not any("配对 Claim" in p for p in readability["problems"]),
          f"A2 期间错位的反例前提：每一格都必须逐字等于它配对事实的权威可见文本，且分量齐备，"
          f"否则拒它的理由就不是期间错位（实际 {readability['problems'][:3]}）")

    # 非空单元格数 != Claim 数：wire 层已拒（`NarrativeTableRow.__post_init__`），runner 侧是
    # 冗余防线 —— 用 `object.__setattr__` 造出「坏对象已经到达验收侧」的情形再点一次。
    corrupted = _good_financial_section().narrative.tables[0]
    object.__setattr__(corrupted.rows[0], "cells", ("", "1,000"))
    readability = ACC._financial_readability(
        _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                 tables=(corrupted,),
                 dispositions=(_disposition(c24.claim_id, "omitted", "presented_as_table_row"),
                               _disposition(c23.claim_id, "omitted",
                                            "presented_as_table_row"))), authority)
    check_problem(readability["problems"], "非空单元格数",
                  "A2：空单元格与 Claim 数不逐位对应必须为红（缺值只能显式留空）")

    readability = ACC._financial_readability(
        _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                 tables=(_good_financial_section().narrative.tables[0],)), authority)
    check_problem(readability["problems"], "没有 Claim 去向记录",
                  "A2：表格承载的 Claim 缺去向记录必须为红")

    readability = ACC._financial_readability(
        _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                 tables=(_good_financial_section().narrative.tables[0],),
                 dispositions=(_disposition(c24.claim_id, "omitted", "presented_as_table_row"),
                               _disposition(c23.claim_id, "omitted", "outside_section_topic"))),
        authority)
    check_problem(readability["problems"], "的去向不是 omitted/presented_as_table_row",
                  "A2：在表格里却声明「本节之外的主题」必须为红（去向与呈现位置不一致）")

    # ------------------------------------------------------------------
    # §八 A5：零裸事实的**逐句**复算（含 composed）
    # ------------------------------------------------------------------
    def _run(section) -> _NS:
        return _NS(sections={"financial": section})

    def _with_decisions(section) -> _NS:
        section.aggregate_decisions = (_NS(binding_decision_id="cbd-1"),)
        section.entailment_decisions = (_NS(entailment_decision_id="ced-1"),)
        return section

    coverage = ACC._factual_atom_coverage(_run(_with_decisions(_prose_financial_section())))
    check(coverage["unsupported_count"] == 0,
          f"A5 正向：composed 句里两条合规原子必须零裸事实（实际 {coverage['unsupported'][:3]}）")
    check(coverage["composed_sentences"] == 1 and coverage["factual_sentences"] == 0,
          "A5：`factual_sentences=0 / composed=1` 的样本必须仍被逐句检查（这正是旧口径的空通过）")
    check(coverage["claims_covered"] == {"financial": 2},
          "A5：composed 句声明的两条 Claim 必须计入覆盖")

    context_only = _section(
        claims=(c24,), bindings=(_binding("asb-24", kind="topic_pack", fact_id=None,
                                         semantics="context", path="context_only"),),
        paragraphs=(_paragraph("financial", [{"text": c24.text, "sentence_kind": "factual",
                                              "claim_ids": (c24.claim_id,),
                                              "citation_ids": c24_ids}]),))
    coverage = ACC._factual_atom_coverage(_run(context_only))
    check_problem(coverage["unsupported"], "context",
                  "A5：只有 context 边的 Claim 不得取得事实地位")

    missing_entailment = _section(
        claims=(c24,), bindings=(_binding("asb-24", entailment_decision_id=None),),
        paragraphs=(_paragraph("financial", [{"text": c24.text, "sentence_kind": "factual",
                                              "claim_ids": (c24.claim_id,),
                                              "citation_ids": c24_ids}]),))
    coverage = ACC._factual_atom_coverage(_run(missing_entailment))
    check_problem(coverage["unsupported"], "蕴含决定",
                  "A5：缺蕴含决定的 factual 边必须为红")

    foreign = _section(claims=(c24,), bindings=(_binding("asb-24"),),
                       paragraphs=(_paragraph(
                           "financial", [{"text": c24.text, "sentence_kind": "factual",
                                          "claim_ids": (c24.claim_id,),
                                          "citation_ids": c24_ids}]),))
    foreign.aggregate_decisions = (_NS(binding_decision_id="cbd-OTHER"),)
    foreign.entailment_decisions = (_NS(entailment_decision_id="ced-1"),)
    coverage = ACC._factual_atom_coverage(_run(foreign))
    check_problem(coverage["unsupported"], "不在本节决定集内",
                  "A5：边指向别节/不存在的决定必须为红（决定必须在本节决定集内）")

    unknown = _section(claims=(c24,), bindings=(_binding("asb-24"),),
                       paragraphs=(_paragraph(
                           "financial", [{"text": f"{c24.text}，此外，营业收入口径未变",
                                          "sentence_kind": "composed",
                                          "claim_ids": (c24.claim_id, "claim_NOT_THERE"),
                                          "citation_ids": c24_ids}]),))
    coverage = ACC._factual_atom_coverage(_run(unknown))
    check_problem(coverage["unsupported"], "不存在的 Claim",
                  "A5：句子声明了本节不存在的 Claim 必须为红")

    # citation 少一条：wire 层已强制每句至少一条，所以这里用「声明两条 Claim 却只带一条
    # citation」的 composed 句 —— 句子声明的支撑与它实际呈现的来源不一致。
    bad_citation = _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                            paragraphs=(_paragraph(
                                "financial", [{"text": f"{c24.text}，此外，{c23.text}",
                                               "sentence_kind": "composed",
                                               "claim_ids": (c24.claim_id, c23.claim_id),
                                               "citation_ids": (c24_ids[0],)}]),))
    coverage = ACC._factual_atom_coverage(_run(bad_citation))
    check_problem(coverage["unsupported"], "citation",
                  "A5：citation 不是由所声明 Claim 确定性派生（多寡不等）必须为红")

    added_surface = _section(claims=(c24, c23),
                             bindings=(_binding("asb-24"), _binding("asb-23")),
                             paragraphs=(_paragraph(
                                 "financial", [{"text": f"营业收入同比增加，{c24.text}，此外，"
                                                        f"{c23.text}",
                                                "sentence_kind": "composed",
                                                "claim_ids": (c24.claim_id, c23.claim_id),
                                                "citation_ids": (*c24_ids, *c23_ids)}]),))
    coverage = ACC._factual_atom_coverage(_run(added_surface))
    check_problem(coverage["unsupported"], "新增未授权高风险表面",
                  "A5：composed 文本新增未授权高风险表面必须为红")

    # 缺值显式留空：`cells` 的空串是 wire 承认的「本列无已接受权威事实」，行期间只列在场期间
    # （与门后构造器 `build_metric_period_tables` 同形）。
    one_cell = _table(
        "financial", header=(NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年", "2023年"),
        row_specs=[{"label": "营业收入", "cells": ("1,234", ""), "period": "2024年",
                    "claim_ids": (c24.claim_id,), "citation_ids": c24_ids}])
    twice = _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                     paragraphs=(_paragraph(
                         "financial", [{"text": f"{c24.text}；{c23.text}",
                                        "sentence_kind": "composed",
                                        "claim_ids": (c24.claim_id, c23.claim_id),
                                        "citation_ids": (*c24_ids, *c23_ids)}]),),
                     tables=(one_cell,),
                     dispositions=(_disposition(c24.claim_id, "selected"),
                                   _disposition(c23.claim_id, "selected")))
    coverage = ACC._factual_atom_coverage(_run(twice))
    check_problem(coverage["unsupported"], "同时出现在正文句与表格里",
                  "A5：同一条 Claim 两处呈现必须为红（呈现位置恰好一个）")

    unplaced = _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                        tables=(one_cell,),
                        dispositions=(_disposition(c24.claim_id, "omitted",
                                                   "presented_as_table_row"),))
    coverage = ACC._factual_atom_coverage(_run(unplaced))
    check_problem(coverage["unsupported"], "没有 Claim 去向记录",
                  "A5：未呈现且没有去向记录的 Claim 必须为红（静默遗漏）")

    excused = _section(claims=(c24, c23), bindings=(_binding("asb-24"), _binding("asb-23")),
                       tables=(one_cell,),
                       dispositions=(_disposition(c24.claim_id, "omitted",
                                                  "presented_as_table_row"),
                                     _disposition(c23.claim_id, "omitted",
                                                  "redundant_with_selected_claim")))
    coverage = ACC._factual_atom_coverage(_run(excused))
    check(coverage["unsupported_count"] == 0,
          f"A5：带封闭理由的 omitted Claim 不算遗漏（实际 {coverage['unsupported'][:3]}）")

    # ------------------------------------------------------------------
    # §七 2：缺值必须能被**显式留空**（门后「指标 × 期间」构造器，不经过 runner）
    # ------------------------------------------------------------------
    # 这一条是反向回归：`build_metric_period_tables` 对缺值列正是靠空串单元格表达「本列没有
    # 已接受的权威事实」，若 wire 层拒空串，它就只能 Hard fail —— 而「缺值」恰恰必须能被如实
    # 表达（既不能推算补齐，也不能把整张表取消掉）。
    def _fin_claim(prefix: str, label: str, code: str, period: str, display: str):
        text = f"{label}{period}为{display}元"
        fact_id = f"item_{code}_{period[:4]}"
        return (_claim(text, binding_ids=(f"asb-{prefix}",)),
                _binding(f"asb-{prefix}", fact_id=fact_id),
                _NS(fact_id=fact_id, kind="item", code=code, period=period, display=display,
                    value_text=display, label=label, unit="元", text=text))

    # 营业收入有 2024/2023，营业成本有 2024/2022：同单位下成一张三列表，营业成本那一行的
    # 2023 列**没有**已接受事实 —— 这一格必须显式留空，而不是被 2022 的值补齐或整行消失。
    rev24, b_rev24, f_rev24 = _fin_claim("rev24", "营业收入", "revenue", "2024年", "1,234")
    rev23, b_rev23, f_rev23 = _fin_claim("rev23", "营业收入", "revenue", "2023年", "1,000")
    cost24, b_cost24, f_cost24 = _fin_claim("cost24", "营业成本", "cost", "2024年", "800")
    cost22, b_cost22, f_cost22 = _fin_claim("cost22", "营业成本", "cost", "2022年", "700")
    tables = NS.build_metric_period_tables(
        section_id="financial", claims=(rev24, rev23, cost24, cost22),
        authority=_NS(producer_kind="financial_workflow", company_id="300750",
                      artifact=_NS(artifact_id="art-1",
                                   periods=("2024年", "2023年", "2022年"),
                                   facts=(f_rev24, f_rev23, f_cost24, f_cost22))),
        acceptance=_NS(accepted_bindings=(b_rev24, b_rev23, b_cost24, b_cost22)))
    check(len(tables) == 1, f"§七 2：同一单位下的两个指标应成一张表（实际 {len(tables)}）")
    rows = {str(r.label): r for r in tables[0].rows}
    check(tuple(tables[0].header) == (NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年", "2023年", "2022年"),
          f"§七 2：列轴必须是 artifact 声明的期间顺序（实际 {tables[0].header}）")
    check(rows["营业成本"].cells == ("800", NS.FINANCIAL_TABLE_EMPTY_CELL, "700"),
          f"§七 2：缺值列必须显式留空（实际 {rows['营业成本'].cells}）")
    check(tuple(rows["营业成本"].claim_ids) == (cost24.claim_id, cost22.claim_id),
          "§七 2：留空单元格不携带 Claim（非空单元格与 Claim 按非空顺序逐位对应）")
    check(tuple(tables[0].claim_ids)
          == (rev24.claim_id, rev23.claim_id, cost24.claim_id, cost22.claim_id),
          "§七 2：表级 claim_ids 必须是行并集（含留空行的在场 Claim）")
    check(NS.NarrativeTable.from_dict(tables[0].to_dict()) == tables[0],
          "§七 2：含留空单元格的表必须能原样 round-trip")

    # ------------------------------------------------------------------
    # §八 A4：「章节读回通过」不得冒充「整本通过」
    # ------------------------------------------------------------------
    # 旧口径只在 `_reassemble_from_readback` 返回 `fail` 时记账，于是「报告从未组装过 ⇒ 没有
    # 比较对象 ⇒ skipped」的一次运行也能拿到 A4 绿门——它其实一次整本读回重算都没做。这里钉住
    # 两件事：`skipped` 是它自己的一个状态（不冒充 pass），以及门把任何非 pass 都记成问题。
    # 本夹具驱动的是「已有其它 A4 问题 ⇒ 读回重组不执行」那条分支；「没有实得报告」那条分支
    # 也走同一条规则（`status != "pass"`），因此下面同时直接核了它的状态。
    no_report = _NS(sections={}, readback={}, commit_counts={}, rollback=None, report=None,
                    run_dir=Path("."), section_errors={})
    check(ACC._reassemble_from_readback(no_report)["status"] == "skipped",
          "A4 反例前提：没有实得报告时读回重组如实记为 skipped（不是 pass，也不是 fail）")
    a4 = ACC._gate_a4_persistence(Path("a4_gate_probe_never_touched.db"), no_report)
    check(a4.status == "fail", "A4：没有任何章节产出时必须为红")
    check("整本读回重组未通过（status=skipped）" in a4.detail,
          f"A4：整本读回重组没跑过必须记成问题（实际 detail={a4.detail[:200]!r}）")
    check(a4.evidence["readback_reassembly"]["status"] == "skipped",
          "A4：证据里必须保留读回重组的真实状态，供复核者区分「跑过且通过」与「没跑过」")

    # ------------------------------------------------------------------
    # §八 A5：§四 1 身份分层（补件是操作事件，不得改写策略依赖，也不得丢掉后继身份）
    # ------------------------------------------------------------------
    # 这一组反例针对的是「用操作身份顶替内容/版本身份」的假绿：旧实现里补件执行会把 Draft 的
    # `dependency_fingerprint` 改写成新值（于是组装器的「同族逐字节一致」被拒），而两次执行
    # 的 trace ref 又会折叠成同一串（于是产物里分不出跑了几次）。下面每个反例都必须被点出来，
    # 最后再用一个**正向对照**证明这些判据不会把合法现场误判成红。
    FROZEN_FP = "fp-frozen-by-projection"
    BASE_PACK, SUCC_PACK, SUCC_PACK_2 = "pack-base", "pack-succ-1", "pack-succ-2"

    class _Draft:
        """判据只按属性名读取的 Draft 替身（`identity_body` 用于核「运行 ID 是否泄进内容身份」）。"""

        def __init__(self, *, fingerprint: str, containers: tuple[str, ...],
                     extra: dict | None = None) -> None:
            self.dependency_fingerprint = fingerprint
            self.authority_container_ids = tuple(containers)
            self._extra = dict(extra or {})

        def identity_body(self) -> dict:
            return {"authority_container_ids": list(self.authority_container_ids),
                    "dependency_fingerprint": self.dependency_fingerprint, **self._extra}

    class _Section:
        def __init__(self, draft: _Draft, refs: tuple[str, ...] = ()) -> None:
            self.draft = draft
            self.follow_up_run_refs = tuple(refs)

    def _follow_run(run_id: str, *, need_id: str, new_packs: tuple[str, ...],
                    refs: tuple[str, ...], verdict: str = "accepted",
                    executed: bool = True) -> _NS:
        return _NS(follow_up_run_id=run_id,
                   needs=(_NS(need_id=need_id, target_requirement_id=f"req-{TOPIC}",
                              topic_id=TOPIC, aspect_id="aspect-1",
                              section_draft_revision="rev-1"),),
                   decisions=(_NS(verdict=verdict, executed=executed, need_id=need_id),),
                   new_pack_ids=tuple(new_packs), trace_refs=tuple(refs),
                   rules_version="fud-2")

    def _ref(context: str, run_id: str, index: int = 0) -> str:
        return f"{context}:FOLLOW_UP_EXECUTED:{run_id}:{index}"

    def _fp_run(*, draft: _Draft, used_pack: str, refs: tuple[str, ...] = (),
                runs: tuple = ()) -> _NS:
        base = _NS(pack_set=_NS(packs=(_NS(topic_id=TOPIC, pack_id=BASE_PACK),)))
        used = _NS(pack_set=_NS(packs=(_NS(topic_id=TOPIC, pack_id=used_pack),)))
        return _NS(inputs=_NS(projection=_NS(dependency_fingerprint=FROZEN_FP),
                              authorities={"financial": base}),
                   sections={"financial": _Section(draft, refs)},
                   authority_of=lambda _sid: used,
                   follow_up_runs={"financial": tuple(runs)})

    def _fp_problems(**kw) -> list[str]:
        return ACC._follow_up_identity_audit(_fp_run(**kw))["problems"]

    # (1) 补件运行改写了冻结的策略依赖指纹 ⇒ 红
    rewritten = _fp_problems(
        draft=_Draft(fingerprint="fp-rewritten-by-follow-up", containers=(BASE_PACK,)),
        used_pack=BASE_PACK)
    check_problem(rewritten, "依赖指纹被改写",
                  "A5：操作身份改写冻结策略依赖指纹必须为红（同族必须逐字节一致）")

    # (2) 后继 Pack 身份没有落在内容身份里 ⇒ 红（换了后继却在 Draft 上看不出来）
    lost = _fp_problems(draft=_Draft(fingerprint=FROZEN_FP, containers=(BASE_PACK,)),
                        used_pack=SUCC_PACK,
                        refs=(_ref("ctx", "fur-1"),),
                        runs=(_follow_run("fur-1", need_id="n1", new_packs=(SUCC_PACK,),
                                          refs=(_ref("ctx", "fur-1"),)),))
    check_problem(lost, "不在 Draft 的 authority_container_ids",
                  "A5：后继 Pack 必须出现在 Draft 的内容身份里（否则后继身份丢失）")

    # (3) 运行 ID 泄进内容身份（identity_body）⇒ 红
    leaked = _fp_problems(
        draft=_Draft(fingerprint=FROZEN_FP, containers=(SUCC_PACK,),
                     extra={"note": "fur-1"}),  # 内容身份里出现运行 ID
        used_pack=SUCC_PACK, refs=(_ref("ctx", "fur-1"),),
        runs=(_follow_run("fur-1", need_id="n1", new_packs=(SUCC_PACK,),
                          refs=(_ref("ctx", "fur-1"),)),))
    check_problem(leaked, "内容身份里出现了补件运行 ID",
                  "A5：操作身份不得进入 Draft 的内容身份（§4.6）")

    # (4) 两次执行的 trace ref 折叠成同一串 ⇒ 红（真实缺陷形态：ref 不带每次执行的身份，
    #     两次执行都留下同一个 `run_context_id:FOLLOW_UP_EXECUTED:0`）
    collapsed = "run-context-1:FOLLOW_UP_EXECUTED:0"
    folding = _fp_problems(
        draft=_Draft(fingerprint=FROZEN_FP, containers=(SUCC_PACK,)),
        used_pack=SUCC_PACK, refs=(collapsed,),
        runs=(_follow_run("fur-a", need_id="n1", new_packs=(SUCC_PACK,),
                          refs=(collapsed,)),
              _follow_run("fur-b", need_id="n2", new_packs=(SUCC_PACK_2,),
                          refs=(collapsed,))))
    check_problem(folding, "折叠成同一串",
                  "A5：两次有界补件执行的 trace ref 不得相同（否则分不出跑了几次）")
    check_problem(folding, "没有指向它自己",
                  "A5：ref 不带运行时身份时，必须同时点明它没有指向自己的 follow_up_run_id")

    # (5) 正向对照：冻结指纹 + 后继进内容身份 + 两次执行各自指向自己的 run ⇒ 零问题
    healthy = _fp_problems(
        draft=_Draft(fingerprint=FROZEN_FP, containers=(SUCC_PACK_2,)),
        used_pack=SUCC_PACK_2,
        refs=(_ref("ctx", "fur-a", 0), _ref("ctx", "fur-b", 1)),
        runs=(_follow_run("fur-a", need_id="n1", new_packs=(SUCC_PACK,),
                          refs=(_ref("ctx", "fur-a", 0),)),
              _follow_run("fur-b", need_id="n2", new_packs=(SUCC_PACK_2,),
                          refs=(_ref("ctx", "fur-b", 1),))))
    check(healthy == [],
          f"A5 正向：合法现场（指纹冻结 + 后继在内容身份 + 两次执行各指向自己的 run）不得误判为红"
          f"（实际 {healthy[:3]}）")

    # ------------------------------------------------------------------
    # §八 A5 版本身份：只换补件运行 ID 不得让内容版本漂移；旧 Pack / 错 Pack 仍 fail-closed
    # ------------------------------------------------------------------
    # 版本身份的输入面**在结构上**没有承载操作性运行身份的位置：能漂移版本的只有权威侧
    # （Pack 集 / 财务 artifact / 投影 / 策略版本）。这不是约定俗成，而是字段集合本身——
    # 所以「只换了补件运行 ID」不可能让 `report_version` 变化。
    version_fields = tuple(RA.ReportVersionInputs.__dataclass_fields__)
    check(set(version_fields) == {"scope_input_fingerprint", "plan_id", "selected_task_ids",
                                  "topic_pack_ids", "financial_fact_pack_artifact_id",
                                  "model_policy_id", "prompt_version"},
          f"A5 版本身份：输入面必须恰是这七项（实际 {version_fields}）")
    check(not [name for name in version_fields
               if any(token in name for token in ("run", "follow_up", "trace", "need_id",
                                                  "session", "idempotency"))],
          "A5 版本身份：ReportVersionInputs 不得有承载「哪一次补件执行」的字段"
          f"（否则运行 ID 会让内容版本漂移；实际 {version_fields}）")

    PACK_USED, PACK_OLD = "pack-used-1", "pack-old-0"
    # 冻结投影的依赖指纹：身份层要求 64 位 sha256 hex，因此夹具也必须用真形状的值。
    REPORT_FP = "5" * 64
    REPORT_FP_REWRITTEN = "6" * 64

    # §4.6（acc-5）：审计要独立重算规范载荷指纹，因此夹具必须拿**真** AssembledReport，
    # 不能用只带几个属性的替身（替身正是「无法重算」那一类，另有单独反例）。
    _V_JOB, _V_COMPANY = "job-v", "c-v"
    _V_DRAFT = "sdraft_" + "0" * 24
    _V_MARKDOWN = "正文"
    _V_SECTION = NS.AssembledSection(
        section_id="company", title="公司", section_result_id="sres_v",
        section_draft_id=_V_DRAFT, evaluation_id="seval_v",
        narrative_gate_result_id="ngr_v", binding_id="neb_v", decision="PASS_WITH_GAPS",
        coverage_summary={}, claim_ids=(), paragraph_ids=(), table_ids=(),
        unresolved_ids=(), markdown="节正文")

    def _version_report(*, report_packs: tuple[str, ...], containers: tuple[str, ...],
                        report_fp: str, disposition_index: tuple = (),
                        gap_index: tuple = (), draft_id: str | None = None,
                        scope_coverage: tuple = (),
                        ) -> NS.AssembledReport:
        section = _V_SECTION if draft_id is None else dataclasses.replace(
            _V_SECTION, section_draft_id=draft_id)
        payload = {
            "schema_version": NS.REPORT_SCHEMA_VERSION,
            "job_id": _V_JOB, "company_id": _V_COMPANY, "report_as_of": "2026-06-30",
            "profile_fingerprint": "e" * 64, "projection_id": "proj_" + "0" * 24,
            "contract_version": "v2", "contract_fingerprint": "a" * 64,
            "assembler_version": RA.ASSEMBLER_VERSION, "sections": (section,),
            "scope_coverage": scope_coverage, "disposition_index": disposition_index,
            "support_ref_ids": (),
            "claim_ids": (), "gap_index": gap_index, "conflict_index": (),
            "authority_container_ids": tuple(containers), "retention": {},
            "markdown": _V_MARKDOWN, "dependency_fingerprint": report_fp,
        }
        identity = BS.ReportVersionIdentity.build(
            profile_fingerprint="e" * 64, scope_input_fingerprint="f" * 64,
            projection_id="proj_" + "0" * 24, plan_id="dplan_" + "0" * 24,
            job_id=_V_JOB, company_id=_V_COMPANY, report_as_of="2026-06-30",
            selected_task_ids=("dtask_a",), topic_pack_ids=tuple(report_packs),
            financial_fact_pack_artifact_id=None,
            contract_fingerprint="a" * 64, source_policy_fingerprint="b" * 64,
            writing_spec_fingerprint="c" * 64, presentation_profile_fingerprint="d" * 64,
            dependency_fingerprint=report_fp,
            body_fingerprint=NS.body_fingerprint_of(_V_MARKDOWN),
            section_draft_ids=(section.section_draft_id,),
            assembled_payload_fingerprint=NS.assembled_payload_fingerprint(**payload),
            narrative_schema_version=NS.NARRATIVE_SCHEMA_VERSION,
            claim_schema_version=SS.CLAIM_SCHEMA_VERSION,
            table_schema_version=SS.TABLE_SCHEMA_VERSION,
            writer_schema_version="writer-v1",
            assembler_schema_version=RA.ASSEMBLER_VERSION,
            claim_binding_gate_version=NS.CLAIM_BINDING_GATE_VERSION,
            claim_entailment_rules_version=NS.CLAIM_ENTAILMENT_RULES_VERSION,
            prompt_version="prompt-v1", model_policy_id="mp-1")
        return NS.AssembledReport.create(version_identity=identity, **payload)

    def _version_run(*, authority_packs: tuple[str, ...], declared: tuple[str, ...] | None = None,
                     report_packs: tuple[str, ...] | None = None,
                     containers: tuple[str, ...] | None = None,
                     report_fp: str = REPORT_FP, report: bool = True,
                     report_obj: object | None = None,
                     live_drafts: tuple[str, ...] | None = None) -> _NS:
        authority = _NS(pack_set=_NS(packs=tuple(_NS(pack_id=p) for p in authority_packs)))
        built: object | None = report_obj
        if built is None and report:
            built = _version_report(
                report_packs=(authority_packs if report_packs is None else report_packs),
                containers=(authority_packs if containers is None else containers),
                report_fp=report_fp)
        # 定稿现场（`RunState.sections` 的形状）：Draft 身份独立核对只能取自这里。
        live = {f"s{i}": _NS(draft=_NS(draft_id=d)) for i, d in enumerate(
            (_V_DRAFT,) if live_drafts is None else live_drafts)}
        return _NS(
            authority_of=lambda _sid: authority,
            version_inputs=_NS(topic_pack_ids=tuple(
                authority_packs if declared is None else declared)),
            report=built,
            sections=live,
            inputs=_NS(projection=_NS(dependency_fingerprint=REPORT_FP)))

    def _version_problems(**kw) -> list[str]:
        return ACC._report_version_identity_audit(_version_run(**kw))["problems"]

    check(_version_problems(authority_packs=(PACK_USED,)) == [],
          "A5 版本身份正向：声明集 = 报告版本集 = 定稿权威集时必须是零问题（不得把合法现场判红）")
    check_problem(
        _version_problems(authority_packs=(PACK_USED,),
                          report_packs=(PACK_USED, PACK_OLD)),
        "旧 Pack / 错 Pack 不得进版本",
        "A5：report_version 里混进本轮未使用的旧 Pack 必须为红")
    check_problem(
        _version_problems(authority_packs=(PACK_USED,), declared=()),
        "与各节定稿权威取回的 Pack 集",
        "A5：声明集少一个 Pack（有节的 Pack 身份丢失）必须为红")
    check_problem(
        _version_problems(authority_packs=(PACK_USED,), containers=()),
        "报告级权威容器清单缺少本轮实际使用的 Pack",
        "A5：报告级容器清单漏掉本轮实际使用的 Pack 必须为红")
    check_problem(
        _version_problems(authority_packs=(PACK_USED,), report_fp=REPORT_FP_REWRITTEN),
        "报告级依赖指纹不等于冻结投影的指纹",
        "A5：报告版本根被改写必须为红")
    unavailable = ACC._report_version_identity_audit(
        _version_run(authority_packs=(PACK_USED,), report=False))
    check(unavailable["status"] == "unavailable" and unavailable["problems"],
          "A5：本轮没有实得报告时，版本身份必须如实记 unavailable 并算问题（不得白拿绿门）")

    # §4.6（acc-5）：身份声明的 Draft 身份与规范载荷指纹必须与产物一致，且**独立重算**。
    positive = ACC._report_version_identity_audit(_version_run(authority_packs=(PACK_USED,)))
    check(positive["section_draft_ids_declared"] == [_V_DRAFT]
          and positive["section_draft_ids_expected"] == [_V_DRAFT]
          and positive["assembled_payload_fingerprint_recomputed"]
          == positive["assembled_payload_fingerprint_declared"],
          f"A5 版本身份正向：Draft 身份与规范载荷指纹逐字对账通过（实际 "
          f"{positive.get('problems')}）")
    stub = _NS(version_identity=_NS(topic_pack_ids=(PACK_USED,), section_draft_ids=(_V_DRAFT,),
                                    assembled_payload_fingerprint="0" * 64),
               authority_container_ids=(PACK_USED,), sections=(_V_SECTION,),
               dependency_fingerprint=REPORT_FP)
    check_problem(
        ACC._report_version_identity_audit(
            _version_run(authority_packs=(PACK_USED,), report_obj=stub))["problems"],
        "无法独立重算",
        "A5：报告对象不足以独立重算规范载荷指纹时必须为红（不得白拿绿门）")

    # §4.6 反例（本轮要修的缺陷）：**只改各节 SectionDraft 身份**（正文、Pack、其余载荷逐字
    # 不变）时必须换出新的 report_version —— 否则「换过 Draft 的报告」会被当成同一版。
    _base_report = _version_report(report_packs=(PACK_USED,), containers=(PACK_USED,),
                                   report_fp=REPORT_FP)
    _draft_swapped = _version_report(report_packs=(PACK_USED,), containers=(PACK_USED,),
                                     report_fp=REPORT_FP,
                                     draft_id="sdraft_" + "9" * 24)
    check(_draft_swapped.markdown == _base_report.markdown
          and _draft_swapped.report_version != _base_report.report_version,
          "A5 版本身份反例：正文与 Pack 不变、只改 §各节 Draft 身份 ⇒ report_version 必须变"
          f"（实际 {_base_report.report_version!r} vs {_draft_swapped.report_version!r}）")
    # 反向：Draft 身份对不上**定稿现场**时，审计必须点名。这里送进去的是一份**自洽**的报告
    # （正文、Pack、身份声明三处都同步换成了另一个 Draft ID）——只拿报告自报值互核是自证循环，
    # 必须用本轮 `run.sections` 的定稿 Draft 身份才核得出来。
    check_problem(
        ACC._report_version_identity_audit(
            _version_run(authority_packs=(PACK_USED,),
                         report_obj=_draft_swapped))["problems"],
        "各节 SectionDraft 身份",
        "A5：报告自称另一个 Draft（身份声明同步重建）时，必须与定稿现场对不上才算红")
    _no_live = _version_run(authority_packs=(PACK_USED,))
    del _no_live.sections
    check_problem(
        ACC._report_version_identity_audit(_no_live)["problems"],
        "各节定稿现场",
        "A5：拿不到各节定稿现场时 Draft 身份无从独立核对，必须为红（不得白拿绿门）")
    # 反向（§4.6 第三类反例）：改**未呈现在 Markdown 里、但属于报告身份**的内容（覆盖/缺口面）
    # 也必须换版本 —— 否则「同一版正文」可以携带两份不同的覆盖结论。
    _coverage_report = _version_report(
        report_packs=(PACK_USED,), containers=(PACK_USED,), report_fp=REPORT_FP,
        scope_coverage=({"unit_id": "u-1", "has_body": True, "has_explicit_gap": False},))
    check(_coverage_report.markdown == _base_report.markdown
          and _coverage_report.report_version != _base_report.report_version,
          "A5 版本身份反例：正文逐字不变、只改 Markdown 之外的身份内容（scope_coverage）⇒ "
          f"report_version 必须变（实际 {_base_report.report_version!r} vs "
          f"{_coverage_report.report_version!r}）")

    # §4.6 行序不参与身份：`disposition_index` / `gap_index` 是**目录**，写回序来自本节产出、
    # 读回序来自存储行序 —— 同一集合换了行序不得换出不同 report_version（否则读回重组会
    # 与实得报告「内容相同、身份不同」）。
    _DISP_A = {"section_id": "company", "task_id": "dtask_a", "fact_id": "fact-1",
               "disposition": "not_presented_with_reason", "required": True,
               "claim_ids": [], "binding_ids": [], "reason_code": "r", "unresolved_id": "u1"}
    _DISP_B = {**_DISP_A, "fact_id": "fact-2", "unresolved_id": "u2"}
    _GAP_A = {"section_id": "company", "unresolved_id": "u1", "topic_id": "t",
              "state": "not_found", "reason_code": "r", "detail": "d"}
    _GAP_B = {**_GAP_A, "unresolved_id": "u2"}
    _perm_report = _version_report(
        report_packs=(PACK_USED,), containers=(PACK_USED,), report_fp=REPORT_FP,
        disposition_index=(_DISP_B, _DISP_A), gap_index=(_GAP_B, _GAP_A))
    _same_report = _version_report(
        report_packs=(PACK_USED,), containers=(PACK_USED,), report_fp=REPORT_FP,
        disposition_index=(_DISP_A, _DISP_B), gap_index=(_GAP_A, _GAP_B))
    check(_perm_report.report_version == _same_report.report_version
          and _perm_report.report_id == _same_report.report_id,
          "A5 版本身份：索引目录换行序不得换版本（行序不是内容；`AssembledReport` 自校验"
          f"亦须通过，实际 {_perm_report.report_version!r} vs "
          f"{_same_report.report_version!r}）")

    # ------------------------------------------------------------------
    # A1 源头对账：只读诊断（不得编造、不得因缺对象崩掉、不得把风险句算成可用）
    # ------------------------------------------------------------------
    # 这条诊断存在的唯一理由，是让「材料在哪一层掉的」可复核。它自己必须守同样的纪律：
    # 回查不到就不许编数字，显式否定句不许被算成可写事实，缺对象时如实记 unavailable。
    plain = ACC._slice_audit("公司主营业务为动力电池系统的研发、生产与销售。")
    check(plain["eligible"] == ["公司主营业务为动力电池系统的研发、生产与销售"],
          f"A1 对账前提：普通描述性句子必须落在「可用原子」一侧（实际 {plain['eligible']}）")
    risky = ACC._slice_audit("报告期内公司未发生重大诉讼、仲裁事项。")
    check(not risky["eligible"]
          and any("高风险表面" in r for r in risky["sentences"][0]["excluded_by"]),
          "A1 对账：显式否定句必须留在「不可用」一侧（对账不得把它算成可写事实）")
    # 无终止符的读视图（表格/勾选框/标题类正文）在**切句**这一层就没有句子可判：它不是
    # 「被排除的句子」，而是「连一条句子都没切出来」。对账必须如实区分这两件事。
    check(ACC._slice_audit("□适用 □不适用") == {"sentences": [], "eligible": []},
          "A1 对账：没有句末终止符的读视图必须如实记「切不出句子」，不得算作被排除的句子")
    check(ACC._reason_histogram([{"excluded_by": ["a", "b"]},
                                 {"excluded_by": ["a"]}]) == {"a": 2, "b": 1},
          "A1 对账：排除原因按条计数、不互斥（一条句子可同时踩多条）")
    # 逐层计数**只计数、不互斥、不解释成因**：原因缺失时记 `?`，不得丢事件。
    check(ACC._count_by(
        [{"reason_code": "tree_material_bounded_out"}, {"reason_code": "tree_material_gaps"},
         {"reason_code": "tree_material_bounded_out"}, {}], "reason_code")
        == {"tree_material_bounded_out": 2, "tree_material_gaps": 1, "?": 1},
        "A1 对账：逐层计数必须按字段计数，缺失记 `?`、事件不得丢")
    check(ACC._material_text_chars(_NS(payload_ref=object()), None) == -1,
          "A1 对账：材料正文回查不到时必须记 -1，不得编造字数")
    missing = ACC._a1_source_reconciliation(
        _NS(inputs=_NS(tasks={}, requirements={}), sections={}, authorities={},
            research_llm=None, trace_sink=None))
    check(missing["status"] == "unavailable",
          f"A1 对账：本节连 task 都没有（本 run 没跑到这一节）时如实记 unavailable，"
          f"不得崩掉也不得伪造清单（实际 {missing.get('status')!r}）")

    # 写作没成功（有 task、无 section）**不是**整份不可读的理由：七层里只有第七层读写作
    # 产物，前六层（需求 / 导航 / 召回 / 轨迹 / 材料 / Pack manifest）是研究相位的事实。
    # 真实 run r3 实证：三节写作全失败 ⇒ 这句 early return 把「研究真的读过什么」一并吞成
    # 一句「源头对账无从做起」，读回的人无从区分「研究没读到」与「写作没产出」。
    _aspect = _NS(aspect_id="a-1", question_id="q1", kind="fact",
                  content_role="substantive", output_destination="section_body",
                  missing_policy="gap", time_scope="current", requirement_text="r")
    _task = _NS(topic_ids=["t-co"], question_ids=["q1"], aspects=(_aspect,))
    _failed_state = _NS(
        mode=ACC.MODE_OFFLINE, sections={},
        authority_of=lambda sid: _NS(pack_set=_NS(
            pack_for=lambda tid: (_ for _ in ()).throw(RuntimeError("no pack")))),
        inputs=_NS(tasks={"company": _task},
                   requirements={"t-co": _NS(question_ids=["q1"], aspects=(_aspect,))},
                   resolver=None, trace_sink=None, run_contexts={}, research_llm=None))
    failed_rec = ACC._a1_source_reconciliation(_failed_state, "company")
    check(failed_rec["status"] == "read_only_diagnostic",
          f"A1 对账：有 task 无 section（写作失败）时**仍须**交出研究相位的前六层，"
          f"不得整份记 unavailable（实际 {failed_rec.get('status')!r}）")
    check(failed_rec["nav_read_scope"]["event_count"] == 0
          and failed_rec["requirements"] and failed_rec["trace"]["event_count"] == 0,
          "A1 对账：前六层照常平列（需求层有行、导航/轨迹层如实为 0——0 在这里是"
          "**可读回**的零，不是「没读回」）")
    check(failed_rec["writer_manifest_status"] == "not_available"
          and "无 SectionDraft" in failed_rec["writer_manifest_error"],
          "A1 对账：只有第七层标不可判定，并写明原因")
    _lost = failed_rec["lost_at"]
    check(_lost["writer_side_status"] == "not_available"
          and all(_lost[key] is None for key in (
              "pack_materials_missing_from_writer_manifest", "writer_manifest_members_not_in_pack",
              "writer_members_without_sentence", "writer_members_zero_eligible",
              "writer_member_excluded_by_reason")),
          "A1 对账：Writer 侧五项读不回时记 None（**不是** `[]`/`{}`——空容器会被读成"
          "「一份都没掉」，而真实情况是「这一层没读回」）")
    _ok_state = _NS(
        mode=ACC.MODE_OFFLINE,
        sections={"company": _NS(draft=_NS(material_manifest=_NS(entries=())))},
        authority_of=lambda sid: _NS(pack_set=_NS(
            pack_for=lambda tid: (_ for _ in ()).throw(RuntimeError("no pack")))),
        inputs=_NS(tasks={"company": _task},
                   requirements={"t-co": _NS(question_ids=["q1"], aspects=(_aspect,))},
                   resolver=None, trace_sink=None, run_contexts={}, research_llm=None))
    ok_rec = ACC._a1_source_reconciliation(_ok_state, "company")
    _ok_lost = ok_rec["lost_at"]
    check(ok_rec["writer_manifest_status"] == "read_back_ok"
          and _ok_lost["writer_side_status"] == "read_back_ok"
          and _ok_lost["pack_materials_missing_from_writer_manifest"] == []
          and _ok_lost["writer_member_excluded_by_reason"] == {},
          "对照面：清单可读回（空集）时 Writer 侧五项回到 `[]`/`{}`、状态是 read_back_ok"
          "（否则「一律返回 None」也能让上一条变绿）")

    # ------------------------------------------------------------------
    # 失败路径（acc-24）：三节写作全失败时，报告与 artifact index **仍须完整落盘**
    # ------------------------------------------------------------------
    # 缺陷现场：`_write_run` 的诚实性说明对第七层读数直接取 `len(...)`，而那一层在三节全失败时
    # 是 `None`（**不可读回**）而不是 `0`（**确实为零**）⇒ 报告装配在最后一步抛
    # `TypeError: object of type 'NoneType' has no len()`，`acceptance_report.json` 与
    # `artifact_index.json` **一份都没落盘**：最需要留证的那一次失败 run，恰恰什么都没留下。
    # 同一段里还有第二处 `company_section.draft.claim_candidates`（有节记录、无 draft 时
    # `AttributeError`）。本组走**真的** `_write_run`（只把与失败面无关的重型文本/读数生产者
    # 换成替身），同时钉住两件事：① 两份产物必须写出来；② 「不可读回」不得被印成「0 个」。
    _three = ("company", "industry", "financial")
    # 冻结 aspect 快照的替身必须带上**真实存在**的字段：报告装配读 `evidence_requirement_ids`
    # （冻结 `EvidenceRequirementRef` 的投影）来派生 usage-scope。替身少给这个字段，装配会在
    # 诚实性说明那一段 `AttributeError` 炸掉——那正是本组要钉的缺陷现场，不能由夹具再制造一次。
    # 空元组是**诚实**取值：没有冻结使用资格 ⇒ required/supplemental 类皆空，而不是编一个。
    _aspect3 = _NS(aspect_id="a-1", question_id="q1", kind="fact",
                   content_role="substantive", output_destination="section_body",
                   missing_policy="gap", time_scope="current", requirement_text="r",
                   evidence_requirement_ids=())
    _requirements3 = {f"t-{s}": _NS(question_ids=["q1"], aspects=(_aspect3,))
                      for s in _three}

    def _no_pack(_topic_id: str):
        raise RuntimeError("夹具：没有 Pack")

    def _three_inputs():
        _tasks = {s: _NS(title=f"{s} 节", topic_ids=[f"t-{s}"], question_ids=["q1"],
                         aspects=(_aspect3,)) for s in _three}
        return _NS(
            company_id="SUBJ-3F", document_id="doc-3f", document_version="v1",
            raw_pdf_sha256="0" * 64, evidence_set_version="es-3f",
            report_as_of="2026-09-24", generated_at="2026-09-24T00:00:00Z",
            report_timezone="Asia/Shanghai", scope_fingerprint="scope-3f",
            contract=_NS(contract_version="c-1"),
            projection=_NS(projection_id="p-1", contract_fingerprint="cf-1"),
            source_manifest=SM.SourceManifest(
                policy_version=SM.MANIFEST_POLICY_VERSION, company_id="SUBJ-3F",
                generated_at="2026-01-01T00:00:00Z", report_as_of="2026-09-24",
                report_timezone="Asia/Shanghai", financial_data_cutoff="2025-12-31",
                entries=(), provenance_findings=(), selection=(),
                primary_document_id=None, primary_document_version=None),
            dims={"subject_declared_by": "cli", "snapshot_id": "snap-3f",
                  "as_of_date": "2025-12-31"},
            financial_phase=_NS(artifact=_NS(artifact_id="fpa-3f", facts=()),
                                evidence_note_facts=()),
            tasks=_tasks, requirements=_requirements3, authorities={},
            # `pack_sets` 是「逐节解析出来的 Pack 集」的载体，报告的外部实况那一段要读它。
            # 本组是「三节全失败、一份 Pack 都没解析出来」的形状，因此空映射是**忠实**取值
            # （不是缺省值）：它落到报告里的读数是「本轮没有任何外部漏斗痕迹」。
            pack_sets={},
            resolver=None, trace_sink=None, run_contexts={}, research_llm=None)

    # 与失败面无关的重型生产者换成替身：报告装配本身（诚实性说明那一整段 + 产物索引）走真的。
    _stub = {
        "_artifacts_payload": lambda state: {"drafts": {}, "claims": [], "narratives": {},
                                             "results": {}, "evaluations": {},
                                             "unresolved": []},
        "_before_after_md": lambda state: "# probe\n",
        "_manual_review_md": lambda state, gates: "# probe\n",
        "_source_manifest_md": lambda state: "# probe\n",
        "_cross_document_md": lambda state: "# probe\n",
        "_cross_document_evidence": lambda state, s: {"status": "probe", "section_id": s},
        "_material_pack_readback_all": lambda state: {
            "schema_version": ACC.MATERIAL_PACK_SCHEMA_VERSION, "status": "probe",
            "entry_count": 0, "sections_readback": [], "sections_unavailable": list(_three),
            "note": "probe"},
        "_material_pack_md": lambda state: "# probe\n",
        "_failure_diagnostics_payload": lambda state: {
            "schema_version": ACC.FAILURE_DIAGNOSTICS_SCHEMA_VERSION, "sections": {}},
        "_subject_declaration_block": lambda state: {"subject_id": "SUBJ-3F"},
        "_research_axis_report_block": lambda state: {"axis_max_attempts": None,
                                                      "note": "probe"},
        "_composed_sentence_observation": lambda state: {},
        "_ledger_payload": lambda state, **kw: {"status": kw.get("status"), "probe": True},
        # 补件身份审计读的是 `section.draft`：生产链把节记录写进 `state.sections` **只在相位
        # 成功时**（`sections[section_id] = phase.sections[0]`），因此真实现场里这一条恒为跳过。
        # 换替身是为了让下面的第二组形状（人工构造）能走到诚实性说明那一段；它不改变第一组
        # （生产真实形状）的读法。
        "_follow_up_identity_audit": lambda state: {"run_count": 0, "runs": [],
                                                    "problems": [], "probe": True},
    }
    _saved = {name: getattr(ACC, name) for name in _stub}
    for _name, _fn in _stub.items():
        setattr(ACC, _name, _fn)
    try:
        for _shape, _label in (
                ({}, "三节都没有节记录（**生产链的真实形状**：相位失败不写 `sections`）"),
                # 第二组是**人工构造**的形状，生产链今天产不出来（`_drive_into` 只在相位成功时
                # 写 `sections[section_id]`，因此真实现场里 `sections` 的每个值都有 `.draft`）。
                # 保留它只为钉住那段文字：它必须按「这一层没有读数」写，不得回到旧口径那句
                # 「材料全部可读、无一被丢在 Pack→Writer 之间」。
                (dict.fromkeys(_three),
                 "人工构造：有节记录、无 draft（**不是**生产形状，只为钉住那段文字）")):
            with tempfile.TemporaryDirectory(prefix="m930-3-3fail-") as _tmp:
                _run_dir = Path(_tmp) / "m930-3-acc-3fail"
                _run_dir.mkdir()
                _state3 = ACC.RunState(inputs=_three_inputs(), run_dir=_run_dir,
                                       mode=ACC.MODE_REAL,
                                       generated_at="2026-09-24T00:00:00Z",
                                       policy=_NS(policy_version="wp-1", prompt_version="p-1",
                                                  renderer_version="r-1", rules_version="ru-1",
                                                  model_policy="approved", max_llm_retries=1))
                _state3.authorities = {s: _NS(pack_set=_NS(pack_for=_no_pack)) for s in _three}
                _state3.section_errors = {s: f"PackWriterError: {s} 写作相位失败" for s in _three}
                if _shape:
                    _state3.sections = {s: _NS(llm_calls=0) for s in _shape}
                _result = ACC._write_run(
                    _state3, before={"probe": 1}, after={"probe": 1},
                    gates=[ACC._gate("A1", "标题 / 材料 / 事实门", ["夹具：A1 判据未成立"], {})],
                    llm_calls={}, status="failed")
                _report_path = _run_dir / "acceptance_report.json"
                _index_path = _run_dir / "artifact_index.json"
                check(_report_path.exists() and _index_path.exists(),
                      f"{_label}：报告装配**不得**在最后一步炸掉——`acceptance_report.json` 与 "
                      f"`artifact_index.json` 必须都落盘（缺陷现场是 `len(None)` 让两份都没写出来；"
                      f"实际 report={_report_path.exists()}、index={_index_path.exists()}）")
                if not (_report_path.exists() and _index_path.exists()):
                    continue
                _rep = json.loads(_report_path.read_text(encoding="utf-8"))
                _idx = json.loads(_index_path.read_text(encoding="utf-8"))
                _honesty = "".join(_rep["honesty"])
                check(_rep["status"] == "failed" and _rep["mode"] == ACC.MODE_REAL
                      and sorted(_rep["section_errors"]) == sorted(_three)
                      and _rep["gate_summary"] == {"A1": "fail"},
                      f"{_label}：报告要如实带出失败身份（status / mode / 逐节错误 / 红门），"
                      f"不得因为「本轮全失败」就少写一半（实际 status={_rep.get('status')!r}、"
                      f"errors={sorted(_rep.get('section_errors') or ())}、"
                      f"gates={_rep.get('gate_summary')!r}）")
                check(_result["report"]["status"] == "failed"
                      and "acceptance_report.json" not in _result["missing"]
                      and "artifact_index.json" not in _result["missing"],
                      "`_write_run` 的返回值必须与落盘事实一致：本轮没写出来的那些（无 draft、"
                      "无组装）照实进 `missing`，而报告与索引**不得**在其中"
                      f"（实际 missing={_result['missing']!r}）")
                _names = [row["artifact"] for row in _idx["artifacts"]]
                check("acceptance_report.json" in _names and "manifest.json" in _names
                      and all(row["bytes"] > 0 and len(row["sha256"]) == 64
                              for row in _idx["artifacts"]),
                      f"{_label}：产物索引要逐文件给出字节数与 sha256（索引本身是「哪些产物真的"
                      f"写出来了」的凭据；实际 {_names}）")
                check("Writer 精确材料清单成员 **不可读回**" in _honesty
                      and "Writer 精确材料清单成员 0 个" not in _honesty,
                      f"{_label}：第七层读不回时必须写「不可读回」，**不得**印成「0 个」——"
                      f"「没有读数」与「确实为零」是两件事（实际 {_honesty[:200]!r}）")
                check("A1 源头对账逐主题" in _honesty
                      and _rep["observations"]["company_material_source_reconciliation"]["status"]
                      == "read_only_diagnostic",
                      f"{_label}：逐主题对账照常平列、`observations` 里也留着它自己的读数"
                      f"（研究相位的前六层不受写作失败影响）")
                if _shape:
                    check("没有产出 `SectionDraft`" in _honesty
                          and "在本轮不可读回" in _honesty
                          and "无一被丢在 Pack→Writer 之间" not in _honesty,
                          f"{_label}：A1 红的落点必须说「这一层没有读数」，**不得**沿用那句"
                          f"「材料全部可读、无一被丢在 Pack→Writer 之间」——写作侧根本没读回来时"
                          f"没有任何读数支得住那句话（实际 {_honesty[-600:]!r}）")
                    check(_honesty.count("没有产出 `SectionDraft`") == 1,
                          "该段落只出现一次（两段归因是互斥的：有 draft / 无 draft 各自只说"
                          "站得住的那一句，不得两句都印）")

        # ② 「报告装配**本身**抛错」这一档：完整报告写不出来时，仍要先落一份降级报告与
        # artifact index，再重抛。它不是「把缺陷咽下去」——`status` 用一个**新值**
        # `assembly_error`，不冒充任何一条门的结论。
        with tempfile.TemporaryDirectory(prefix="m930-3-asmfail-") as _tmp:
            _run_dir2 = Path(_tmp) / "m930-3-acc-asmfail"
            _run_dir2.mkdir()
            _stateA = ACC.RunState(inputs=_three_inputs(), run_dir=_run_dir2, mode=ACC.MODE_REAL,
                                   generated_at="2026-09-24T00:00:00Z",
                                   policy=_NS(policy_version="wp-1", prompt_version="p-1",
                                              renderer_version="r-1", rules_version="ru-1",
                                              model_policy="approved", max_llm_retries=1))
            _stateA.section_errors = {s: f"PackWriterError: {s} 写作相位失败" for s in _three}
            _fallback = ACC._write_run_assembly_fallback(
                _stateA, before={"probe": 1}, after={"probe": 1},
                gates=[ACC._gate("A1", "标题 / 材料 / 事实门", ["夹具：A1 判据未成立"], {})],
                llm_calls={}, status="failed",
                error="TypeError: object of type 'NoneType' has no len()")
            _pf, _if = _run_dir2 / "acceptance_report.json", _run_dir2 / "artifact_index.json"
            check(_pf.exists() and _if.exists(),
                  "报告装配抛错时，降级报告与 artifact index 必须**先落盘**再重抛"
                  f"（实际 report={_pf.exists()}、index={_if.exists()}）")
            if _pf.exists() and _if.exists():
                _drep = json.loads(_pf.read_text(encoding="utf-8"))
                _didx = json.loads(_if.read_text(encoding="utf-8"))
                _dhon = "".join(_drep["honesty"])
                check(_drep["status"] == "assembly_error" and _drep["degraded_report"] is True
                      and _drep["gates_were_computed_as"] == "failed"
                      and _drep["gate_summary"] == {"A1": "fail"}
                      and "has no len()" in _drep["assembly_error"],
                      "降级报告：`status=assembly_error` 是**新值**（不冒充 pass/fail/refused），"
                      "已算完的门结论原样带出，异常逐字写出"
                      f"（实际 status={_drep.get('status')!r}）")
                check("降级报告" in _dhon and "没装进来" in _dhon
                      and "确实为零" in _dhon,
                      "降级报告必须自己说清：本文件缺的观察项是「没装进来」，"
                      "**不得**被读成空值或「确实为零」（这两句必须同在）")
                check(_didx.get("degraded_report") is True
                      and [r["artifact"] for r in _didx["artifacts"]],
                      "降级档的 artifact index 同样要标记自己是降级产物，并列出实际写出了哪些文件")
                check(_fallback["report"]["status"] == "assembly_error"
                      and "acceptance_report.json" not in _fallback["missing"],
                      "兜底函数返回值与落盘事实一致")
        # 调用点接线（结构判据，不是文本搜索）：`_write_run` 必须被 **try/except** 包住，且该
        # except 里既要调用兜底、又要**重抛**——少任何一半，上面那条兜底就永远不会被走到。
        _tree3 = ast.parse(Path(ACC.__file__).read_text(encoding="utf-8"))
        _wired = False
        for _node in ast.walk(_tree3):
            if not isinstance(_node, ast.Try):
                continue
            _write_calls = [c for _stmt in _node.body for c in ast.walk(_stmt)
                            if isinstance(c, ast.Call)
                            and getattr(c.func, "id", "") == "_write_run"]
            if not _write_calls:
                continue
            _has_fallback = any(
                isinstance(c, ast.Call)
                and getattr(c.func, "id", "") == "_write_run_assembly_fallback"
                for _handler in _node.handlers for _stmt in _handler.body
                for c in ast.walk(_stmt))
            _rethrows = any(isinstance(_stmt, ast.Raise)
                            for _handler in _node.handlers for _stmt in _handler.body)
            if _has_fallback and _rethrows:
                _wired = True
        check(_wired,
              "调用点接线：`_write_run` 必须被 try 包住，except 里**先调兜底、再重抛**——"
              "少任何一半，兜底就是死代码（且「装配抛错不得让代码缺陷被咽下去」也一并成立）")
    finally:
        for _name, _fn in _saved.items():
            setattr(ACC, _name, _fn)

    # ------------------------------------------------------------------
    # 真实模式的恒真前提（acc-12）：**只走 `--mode real` 的分支也必须有断言**
    # ------------------------------------------------------------------
    # 缺陷现场：`RealEnvironment._build` 里写了 `RT.RealResearchLLM()`，但 `RT` 从未被导入，
    # 真实模式第一次启动即 `NameError` 崩溃；而 MOCK/离线套件从不走这条分支，于是「真实模式
    # 可启动」当时**没有任何证据**。下面两条把这一类缺陷变成离线可拦的断言。
    _runner_src = Path(ACC.__file__).read_text(encoding="utf-8")
    _tree = ast.parse(_runner_src)
    _bound: set[str] = set()
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Import):
            for _a in _node.names:
                _bound.add(_a.asname or _a.name.split(".")[0])
        elif isinstance(_node, ast.ImportFrom):
            for _a in _node.names:
                _bound.add(_a.asname or _a.name)
        elif isinstance(_node, ast.Name) and isinstance(_node.ctx, ast.Store):
            _bound.add(_node.id)
        elif isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _bound.add(_node.name)
        elif isinstance(_node, ast.arg):
            _bound.add(_node.arg)
        elif isinstance(_node, ast.ExceptHandler) and _node.name:
            _bound.add(_node.name)
        elif isinstance(_node, (ast.Global, ast.Nonlocal)):
            _bound.update(_node.names)
        elif isinstance(_node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            # `match` 的绑定形式（`case {"a": a}` / `case [*rest]`），不得漏计。
            if getattr(_node, "name", None):
                _bound.add(_node.name)
            if getattr(_node, "rest", None):
                _bound.add(_node.rest)
    _module_globals = {"__name__", "__file__", "__doc__", "__package__", "__spec__"}
    _loaded = {
        _n.id for _n in ast.walk(_tree)
        if isinstance(_n, ast.Name) and isinstance(_n.ctx, ast.Load)
    }
    _unbound = sorted((_loaded - _bound) - set(dir(builtins)) - _module_globals)
    check(not _unbound,
          f"runner 里读到的裸名必须在本文件内被绑定过（导入/赋值/参数/定义）、由内建提供，"
          f"或属模块自带全局；否则那条分支一旦被执行就是 `NameError`（本批实测：真实模式的 `RT`，"
          f"只在 `--mode real` 上触发，离线套件永远看不到）——未绑定：{_unbound}")
    check(isinstance(getattr(HRT, "RealResearchLLM", None), type)
          and HRT.RealResearchLLM.__module__ == "harness.runtime",
          "真实模式注入的研究侧 LLM 必须是 `harness.runtime.RealResearchLLM` 这个真实类"
          "（不得改指替身，也不得靠 try/except 吞掉缺失）")
    check(PW.MODEL_POLICY_PROVIDER_DEFAULT == "provider_default",
          f"真实模式的模型策略必须是「provider 默认」（实际 {PW.MODEL_POLICY_PROVIDER_DEFAULT!r}）")
    # 报告里 `approved_model` 是常量，而真实调用取 `config.LLM_MODEL`——两者不一致时报告会
    # **声称**用了已批准模型而实际不是，且从产物里看不出来。此处对齐，不一致即整轮拒绝。
    _orig_model = config.LLM_MODEL
    try:
        config.LLM_MODEL = "deepseek-not-approved"
        _refused = False
        try:
            ACC._writer_policy(mode=ACC.MODE_REAL, model=None)
        except ACC.AcceptanceRefusal:
            _refused = True
        check(_refused,
              "真实模式：`config.LLM_MODEL` 与报告声称的 approved_model 不一致时必须整轮拒绝"
              "（否则报告里的 approved_model 是一句假声明）")
    finally:
        config.LLM_MODEL = _orig_model
    # 正向对照：已批准模型下真实模式必须放行，且写死为 provider 默认 + 有界一次返修。
    _policy = ACC._writer_policy(mode=ACC.MODE_REAL, model=None)
    check(_policy.model_policy == PW.MODEL_POLICY_PROVIDER_DEFAULT
          and _policy.max_llm_retries == ACC.APPROVED_WRITER_MAX_LLM_RETRIES,
          "正向对照：已批准模型下真实模式必须放行，且策略为 provider 默认 + 已批准的有界返修次数")
    _mismatch_refused = False
    try:
        ACC._writer_policy(mode=ACC.MODE_REAL, model="some-other-model")
    except ACC.AcceptanceRefusal:
        _mismatch_refused = True
    check(_mismatch_refused, "显式传入非已批准模型必须被拒（这条判据未被本次改动放宽）")

    # ------------------------------------------------------------------
    # 判据形状变了 → runner 与报告 schema 必须同时升版（历史 run 只读）
    # ------------------------------------------------------------------
    check(ACC.RUNNER_VERSION == "m930-3-acc-40",
          "acc-16 改的是**拒绝报告不得比正常报告少说主体与日期**（acc-16 把「主体 / 报告截止日 / "
          "财务快照日」三者平列到块顶层）；acc-17 改的是**写作链本身**（按 aspect 范围确定性分批、"
          "截断即拒该批并按对半缩小重问、全部批次成功才合并成一份完整提案集）与由它带出的"
          "**预算重估**（叙述类每节 ≤62 / 整轮 ≤144）；acc-18 改的是**导航"
          "读集的可读面**（`anp-3` 键层级进产物）；acc-19 改的是**预算门的位置与轴的拆分**"
          "（门移到 `with RealEnvironment(...)` **之前**，研究调用接进同一个共享入口并走独立的"
          "研究轴；三个写作计数的对账因此收窄为写作轴，另加研究轴与两轴恒等式）；"
          "acc-20 改的是**研究轴上限的推导形式**（由「一个共享值 = 3 × 首个 topic 的 aspect 数」"
          "改成「逐 topic 自派生 = 每 aspect 上界 15 × 该 topic 的 aspect 数」，整轴 = 各 topic 之和；"
          "预检新增研究轴的覆盖检查；镜像复核改为逐 topic 逐值）；acc-21 改的是**跨文档证据块的"
          "读数字段**（「材料去向」的 Writer 清单列改为查过才给布尔值，未产出/清单不可得记 "
          "`null` + `writer_manifest_query`，块级加 `writer_manifest_status`，见 acc-20 的真实"
          "run r3 把「没查过」印成 `in_writer_manifest=false`）；acc-22 改的是**导航规则本身**"
          "（定点返修 C 批 C1：`anp-3` → `anp-4`，topic 标题段改成先按 question 归属再进祖先层，"
          "同 topic 的 aspect 不再齐读别的 question 的父节点正文）⇒ 判据/载荷变化"
          "必须升 RUNNER_VERSION（实际 "
          f"{ACC.RUNNER_VERSION}）；acc-23 是前置修复 T 批（材料包读回 / 台账四轴 / 研究轴拒收"
          "截断）；acc-24 是 r4 后的业务内容定点返修首版（① 失败路径 `len(None)` + "
          "`status='assembly_error'` 降级报告；② A7 截断判据换批次谱系；③ 研究轴说明按实际"
          "句柄状态生成；④ `reproposal=null` 的读法收回到产物支得住的那一句）；acc-25 是同批"
          "第二次升版（⑤ 内容形态分流：`content_qualification` 进材料包与写手输入面）；"
          f"acc-26 是同批第三次升版（② 逐栏目取料 / 读根资格 `anp-4` → `anp-5`、条目 wire "
          f"`anps-3` → `anps-4`）；acc-27 是同批第四次升版（⑥ 行业节的「仅用标点拼接 Claim」"
          "改判为「拒 + **确定性**有界定向重组织」：判据一字未改（`nrules-9` / `ng-9` 与 "
          "`nrules-8` / `ng-8` 在「什么样的 composed 句通过」上完全一致），改的是被拒之后——"
          "切段逐字保留原文 / 切不出则每条 Claim 各自一句逐字事实句（只对纯标点接缝生效）；"
          "并新增第 9 条「正文不得替系统自报检索 / 核验」与 A3 的独立复算。**未**实现「再发一次"
          "定向组织调用」：那会把每节叙述上限从 62 / 44 / 38 抬到 63 / 45 / 39，撑破"
          "`_assert_budget_covers_structural_bound` 的推导上界，须单独授权）；acc-28 是同批第五次"
          "升版（§三/1：**一条填错的补件申请不再杀死整节**——校验一字未减，改的是不成立的范围，"
          "不成立的那条照原样留成 typed 记录并随产物落盘；`_follow_up_needs` 的「全有或全无」"
          "语义原样保留）；acc-29 是同批第六次升版（定点返修第二段：上市公司**勾选表单行**的"
          "**栏目归属**与**支撑资格**——归属读法 `mar-1`、材料包 `material-pack/2` → `/3`、"
          "拒绝审计 `proposal-set-rejections/5` → `/6`；判据一字未减，改的是「这一行属于哪一栏、"
          "凭什么可以撑这一栏」）；acc-30 是本轮「合格材料 → 合法 Claim → 可读章节」的定点返修"
          "（① 蕴含边读法 `cer-3` → `cer-4`：逐字镜像的比较改在**标点归一化视图**上做、ASCII "
          "`.` 刻意排除在外，使「值 / 期间 / 口径限定语全同、只少一个句号」的候选按构造就被"
          "认定为**一条**原子断言，不再落进无引导的原子性裁量——同一现场的 2024/2025/2026Q1 "
          "三个同形态候选本来就被判 entailed，唯一那条 `non_atomic_claim` 是裁决不一致；"
          "③ 注入的 need 构造器由纯结构件换成生产实现 `anb-1`：冻结 `source_classes` 经权威"
          "派生器 `TS.derive_support_eligibility` 搬进正式 `InformationNeed`，路由判据 "
          "`requires_external_source()` **第一次可能为真**；并把外部检索开关从「一行 prompt 文案"
          "+ 一个报告位」变成**执行前真门**（`harness.policies.external_research_unauthorized` "
          "在 `harness.runtime` 的工具执行路径上拒掉三个外部动作并留痕）——没有这道门，接上"
          "构造器就等于静默放开真联网）；acc-31 是 r7b **后**定点返修第一段（**A2 的单元格判据"
          "与已裁决的分量规则对齐**：wire 门在 C4 已按分量核 Claim 文本，本 runner 的 A2 却仍"
          "要求「整格文本是配对 Claim 的连续子串」——而权威渲染用「。」拼接数值与限定语、写作"
          "侧又被禁止在 `claim_text` 中间写句末标点，两条要求互斥，r7b 的 4 个代理单元格只因"
          "一个标点差异被整体作废。判据一字未减：精确等于权威可见文本、事实坐标、期间口径与"
          "表达、citation、呈现位置唯一全部保留，只把连续子串换成三个分量各自逐字出现）；"
          "acc-32 是本轮 §一.2 的**逐候选裁出**（`cco-1`：束里只有部分候选踩线时，被点名的**逐条**"
          "拒掉、其余候选以**新 revision** 继续走链，`proposal_set_rejections.json` 升到 `/8`，"
          "记录多出一个 `carve_out` 字段并带五个 `candidate_carve_out_*` 载荷键；原束留档一字不改，"
          "`model_calls_added` 恒为 `0`）；acc-33 是同批 §一.2 的**读者面**（主体名称改由声明"
          "给出并与权威来源文档登记逐字核对，`subj-1` → `subj-2`；`pwr-4` 改单位 / 主体 / 代理"
          "口径说明的渲染读法）；acc-34 是指令 D §二（b）来源归属轴的**读者面**（`srattr-1`："
          "新增 `source_attribution.json` / `.md` 两个产物与报告侧 `source_attribution` 指针块；"
          "归属语只由**系统**从已登记身份渲染，写者一个字也不能写，且**不改正文与正文指纹**）；"
          "acc-35 是指令 D §三的**失败侧可读面**（`pgr-1` 门前留存：整束被拒时把门前已经写出来的"
          "逐批草稿正文、逐单元出处轴与该批自己的标签原样留档，`proposal-set-rejections/8` → "
          "`/9`；新增 `pre_gate_draft.json` / `.md` 两个产物与报告侧 `pre_gate_draft` 指针块。"
          "判据一字未减：它不参与任何门的判定、不进 `SectionResult`、不进正文与预览、也不放宽"
          "任何门——改的是「这一节到底写出过什么」在产物里读不读得出来）；acc-36 是业务取材"
          "纵链修复 §一的**栏目未达原因可读面**（`RealInputs.topic_results` 把运行现场的逐 "
          "topic 结果留一份，诊断逐 aspect 新增 `column_unmet` 与逐节 `column_unmet_summary`，"
          "`failure-diagnostics/2` → `/3`；判据一字未减：五条 typed 原因彼此不可互推、"
          "**不得**合并成笼统的 `coverage_gate_not_met`，更不得写成「语料里没有」，"
          "取不到运行结果时是「不可判定」而不是空的 `entries`）；acc-37 是同批 §二的"
          "**表对象信道读法**（材料包读回不再把**表对象**印成「内容形态：**读不出**」："
          "表对象信封按设计不带 §二 2.3 的 `content_qualification`，过去一律返回 `None`，"
          "于是**已准入、已保留、已进 Writer 清单**的表对象在人读页印成「读不出」、在节级"
          "直方图里落进 `unavailable`——把「这条信道不归那个分类器管」写成了「读失败」。"
          "现在另成 `kind=table_object` 一族，形态与允许用途取同信封的 `reading_policy`"
          "（`permitted_use` 与逐条排除项，图内另一支），节级另加 `table_object_material_ids`；"
          "`material-pack/3` → `/4`。判据一字未减：「这张表能读」与「表里的数字能以它为准」"
          "是两条**正交**声明，`numeric_authority=false` 照旧，表里的数字照旧不因「能读」"
          "而获得任何权威）；acc-38 是同批 §三的**替身选材与组织**（过长却有业务价值的原句"
          "按小句边界切成有界原子，逐原子各自过同一套过滤器，不再「一句里有一条高危就整句"
          "落选」；候选原子必须**自带陈述对象**——接上一小句往下说的残片（「并通过长期协议…」"
          "「能满足快充…」）逐字、可回查、非高风险，写入侧看不出来，读者却不知道说的是谁，"
          "因此不再进正文并逐条记 typed 未采用原因，且不补主语；材料按**来源角色**稳定重排后"
          "提案，同一段原文同时落在较新与较旧两份同类材料里时，不再由较旧的那一份先占住这句"
          "话；组织侧每个接缝各取一个中性连接语。判据一字未减：只改**替身提案与组织的选材、"
          "行文**，Pack、Writer 清单、资格门、绑定门、蕴含门、引用门与只读 UI 一律不动，"
          "候选被拒仍即整节 fail-closed。报告载荷形状未变，报告 schema 仍停在 report-34）；"
          "acc-39 是 §0.18 W8 单通道的**读取面**（`tlp-1`/`gto-3`/`gtm-1` 落地后目标表的正式材料"
          "改由图侧单通道产出，信封种类由 `tom-1` 换成 `gtm-1`。读回侧过去**只认** `tom-1`，"
          "新信道的表材料因此落回 `kind=unavailable`，**已放行、已进 Pack、已进 Writer 清单**"
          "的表又被印成「内容形态：读不出」——与 acc-37 修掉的是同一个错，只是换了一条信道"
          "重演；现同时认两条种类并带出**实际观察到的** `envelope_kind`，`material-pack/4` → "
          "`/5`。判据一字未减：「这张表能读」与「表里的数字能以它为准」仍是两条正交声明，"
          "报告载荷形状未变，报告 schema 仍停在 report-34）；acc-40 是 M930-3 定点业务纠正①的"
          "**勾选行栏目归属**（读法 `tmr-2` → `tmr-3`：所问事项**只**取行内前缀，行内没写主语时"
          "这一列留空，**不再**回指所在节点标题——回指会把**子项**的勾选状态锚到整个**栏目**上，"
          "真实反例是 NDSD_2025 第 28 页那行 `□适用 不适用`：它所在节点"
          "`（8） 主要销售客户和主要供应商情况` 是栏目，原件的勾选框属于其下「主要客户其他情况"
          "说明」「主要供应商其他情况说明」两个子项，同一页紧跟披露的集中度表真实存在、与那行"
          "勾选框无关且当前未取得数字资格 ⇒ 记系统的读取/交付缺口，不是「来源称集中度不适用」；"
          "空所问事项 fail-closed（`_scope_confined` 对空所问事项一律返回假），**材料一份未删**"
          "（原文、locator、指纹、选项与选中状态全部留档）；读取面同步把「所在节点」标成**仅"
          "导航坐标**并说明「行内没写主语」。报告载荷形状未变，报告 schema 仍停在 report-34）"
          "⇒ 上述任一变化都必须升 RUNNER_VERSION（实际 "
          f"{ACC.RUNNER_VERSION}）")
    check(ACC.ACCEPTANCE_REPORT_SCHEMA_VERSION == "m930-3-acc-report-34",
          "acc-17 让**一次尝试不再等于一次调用**（`proposal_set_rejections.json` 升到 /3，报告侧"
          "新增 `batches` 的读法说明）；acc-18 让**导航回读多出键层级**（A1 层 2b 的 "
          "`TREE_NAVIGATION` 回读平列 `nav_keys` / `parent_keys` / `key_tier` / `rule_version`，"
          "`nav_read_scope` 新增 `declared_tier_aspects` / `ancestor_tier_aspects`）；"
          "acc-19 让**调用账多出第四个视图与一条轴恒等式**"
          "（`reconciliation.research` / `reconciliation.axis_identity` / `llm_calls.research_axis`，"
          "写作轴的守恒式键名由 `conservation.ledger_total` 改为 `conservation.writing_axis_total`）；"
          "acc-20 让**研究侧上限块逐 topic 化**"
          "（`axis_cap.per_scope_max_attempts` 与 `per_topic_cap_by_topic` 进产物，"
          "`per_topic_cap` 降为**单值缺省**、不再等于逐 topic 上限；镜像读数改为 "
          "`observed_aspects_by_topic` / `derived_axis_cap_by_topic` / "
          "`axis_per_scope_max_attempts`）；acc-21 让**跨文档证据块新增两个读数字段、一列由 "
          "bool 变 bool|None**（`writer_manifest_status` 块级、`writer_manifest_query` 逐行）；"
          "acc-22 让 **A1 层 2b 那四个回读字段的取值语义换了**（字段形状不变，但 `nav_keys` / "
          "`parent_keys` 自 `anp-4` 起是「按 question 归属后的键」，`rule_version` 读出 `anp-4`，"
          "同一份报告在旧读法下会被误读成「这些子项本来就读到了父节点正文」）；acc-23 让"
          "**材料包读回**成为报告可指认的产物（`material_pack.md` / `material_pack.json`）；"
          "acc-24 让**截断处置的判据与键换了**（`truncation_policy` 的 `recovered` 判据由「该节"
          "最终有没有 `SectionDraft`」换成**批次谱系**，并新增 `recovered_downstream_failed` / "
          "`recovered_unverified` / `lineage_outcomes`；报告顶层新增 `status='assembly_error'` "
          "这一档降级报告）；acc-25 让**材料包与写手输入面多出形态读法**"
          "（`content_qualification` 逐条摊开五列 + 允许用途 + 排除项，载荷 `material-pack/2`、"
          "写作策略 `pw-11`）；acc-26 让**导航回读面的取值集合换了**（`rule_version` 写 "
          "`anp-5`；`fallback_reason` 新增 `no_anchored_read_root`；`candidates[].discard_reason` "
          "新增 `not_label_anchored` / `not_subject_adjacent`；条目新增 `subject_head`）"
          "——报告的判读窗口变了 ⇒ 报告 schema 必须升版；acc-27 让**行业节的正文判据多出一条、"
          "A3 的证据块多出一个读数**（`self_reported_provenance`：同一份正文在旧读法下会被读成"
          "「没有任何系统自述」，而它其实自称过联网核验）⇒ 报告 schema 同样必须升版；acc-28 让"
          "**「本节没有 Draft」与「本节有一条自己不成立的补件申请」可以分开读**（诚实性说明多出"
          "被拒申请的读数，`failure_diagnostics.json` 逐节多出 `rejected_follow_up_applications`，"
          "`follow_up_needs.json` 升到 `/2`：旧读者会把「没有待裁决诉求」读成「模型没提过"
          "补件」）；acc-29 让**材料包的归属与资格读法换了**（`material-attribution/1` / `mar-1`、"
          "`material-pack/3`：勾选表单行不再只按“挂在哪个字段下”读，而按**栏目归属**与**支撑"
          "资格**读——同一份材料在旧读法下会被读成“它撑得起这一栏”）；acc-30 让**外部来源块的"
          "读法整块换了**（`external-retrieval/1` → `/2`）；acc-31 让**A2 财务可读性块的 "
          "`problems` 读法换了**（同一份 r7b 产物：`report-29` 的读者会把 4 个只差一个标点的"
          "正确代理单元格读成「单元格不是 Claim 的逐字子串」这一条内容缺陷，`report-30` 的读者"
          "看到的是三个分量各自逐字到位——内容没变，**判据变了**，旧读法会据此误判财务节内容"
          "不合格）；acc-32 让**整束拒绝的记录多出一条出口**（`proposal-set-rejections/7` → `/8`，"
          "记录新增 `carve_out`：同一份产物在旧读法下会被读成「这一束整束作废、什么都没继续」，"
          "而它其实逐候选交代了每条原候选的去向、其余候选带着新身份继续走链；且 `carve_out=null` "
          "本身**有歧义**，要按新读法配 `answered_by_attempt` / `reproposal` 与写作侧那一轮的"
          "读数一起读）；acc-34 让**报告多出一个来源归属指针块**（`source_attribution`：共几条"
          "归属语、几句正文带材料支撑边、其中几句「多版本无期间」、几条材料因何没渲染出归属语），"
          "旧读者在一份带正文的产物上找不到「这句话归到哪份材料的哪一页、披露日可不可核实」）；"
          "acc-35 让**报告多出一个失败侧的门前诊断指针块**（`pre_gate_draft`：逐节的门口状态"
          "「被采信 / 留了未核验内容 / 无可留存 / 读不回来」、逐轮拒绝原因与逐批调用读数；"
          "`proposal-set-rejections/8` → `/9`，每条被拒记录多出 `retained_pre_gate`）——"
          "旧读者在一份有节被拒的产物上只能读到「为什么被拒」，读不到「门前已经写出过什么」，"
          "于是会把「这一节没有正文」与「正文写出来了但整束被拒」读成同一件事）"
          "⇒ 报告 schema 必须升版"
          f"（实际 {ACC.ACCEPTANCE_REPORT_SCHEMA_VERSION}）")
    # 两处构造必须真的共用同一份实现：否则「拒绝报告也说了主体」可以靠复制一份写出来，
    # 之后两边各自漂移（这正是本条缺陷的成因形态：正常路径有、拒绝路径漏了）。
    check(_runner_src.count("_subject_declaration_block(") == 3,
          "`subject_declaration` 的构造只有一处实现、被两处调用（正常报告 + 拒绝报告），"
          f"实际出现 {_runner_src.count('_subject_declaration_block(')} 次（1 定义 + 2 调用）")
    check("_unverified_declaration_block(" in _runner_src
          and "_write_refusal(" in _runner_src,
          "装配尚未发生时的拒绝也有声明块（只有声明本身，`verified_against` 为 `None`）")
    # 判据变化必须同时体现在它依赖的口径版本上（否则「换了判据、沿用旧号」会静默成立）
    check(RA.ASSEMBLER_VERSION == "asm-4" and NS.REPORT_SCHEMA_VERSION == "abr-3"
          and BS.REPORT_VERSION_SCHEMA_VERSION == "demo-report-version-v3",
          f"§4.6 身份补齐必须同时升组装器/产物/身份三处版本（实际 "
          f"{RA.ASSEMBLER_VERSION}/{NS.REPORT_SCHEMA_VERSION}/"
          f"{BS.REPORT_VERSION_SCHEMA_VERSION}）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

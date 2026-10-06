"""Eval: M930-3 指令 D 第四项 —— **最终句语义门 B**（`nsfid-1` / `nsfr-1` / 输入面 `nsfr-2`）。

跑法（无管道 / 无重定向）：`python -X utf8 -m evals.test_m930_3_final_sentence_fidelity`

这一门是 `_M930_3_STOP_REPORT.md` §12.4 裁决的落地。它与既有的 `natfid-1`
（`NS.verify_sentence_fidelity`，只读**表面**比较器）**并存、不替换**：`natfid-1` 比字面，
本门比**授权**。本模块的职责因此不是「多测几条」，而是把**增量**与**边界**逐条钉死：

* §1 版本与资产锚：`nsfid-1` / `nsfr-1` / 资产名 / **正文指纹**；三种漂移（自报别的修订号、
  正文被改、未自报）各自 fail-closed；封闭枚举与 prompt 资产逐项互查（prompt 不得与 wire 漂移）。
* §2 确定性原子定位器：六类抽取**全部复用** `narrative_schema` 的既有抽取器（逐类给可执行接线
  读数）；`table_relation` 与 `negation_or_state` 同源于 `marker_hits`，靠**同一份**具名切片分流；
  单字补语按 `natfid-1` 的**同一条**规则跳过（`平均` 不得被读成范围语 `均`）。
* §3 定位 ≠ 判定：定位器不产判定字段、也不产 `subject_or_metric`（认指标名需要指标词表，
  而词表化指标名就是「写死答案关键词」）；`declared_by_claim_id` 只是提示。
* §4 判定面 fail-closed：非法 JSON、越界枚举、未认领的已定位表面、覆盖面不等、聚合与逐原子
  不符——每一条都是**异常**，不是一条 `rejected` 决定（封闭原因码里没有「调用失败」，
  把机械故障写成语义判决就是伪造证据）。三档 verdict 的字段约束各有一条反例。
* §5 **三个盲区**（本门存在的理由）：`natfid-1` 在「`变动为` → `变动约为`」「`资产负债率` →
  `有息负债率`」「丢掉 `代理口径（PROXY_FINANCE_EXPENSES）`」三例上**实测放行**（缺陷码 `[]`），
  本门在模型如实报告时逐例拒绝——每条都同时给出两个读数，且都注明成因是**可执行**的。
* §6 聚合与三档推导：全部未通过原子都是「无位置」⇒ `atom_not_located`；混合 ⇒ `atom_not_entailed`。
* §7 决定身份：内容派生 id、dict 往返、`atom_verdicts()` 读视图、过期锚点与覆盖面；
  改句 / 改 Claim / 换绑定各有一条过期读数（含机制说明：都经由句子身份变化）。
* §8 输入束结构门（真实对象）：非 Narrative、非 SectionClaim、跨节 Claim、非支撑边、
  context 边、换过 revision、乱序边、句子引用束外 Claim、束里带未被引用的 Claim。
* §9 一次真调用（桩）：正例产出**恰好一条**决定并带全调用元数据；调用失败 / 版本不符 /
  模型不符 / 非 JSON 各自**不**产出决定。
* §10 `map_decisions_to_narratives`：按 `draft_id` 建立（不按顺序配对）、缺一条 / 多一条 /
  指向束外 / 过期各自拒。
* §11 唯一实现与机械纪律（AST 级）：定位器只有一份实现；具名切片是子集而非副本；
  本门源码不内嵌词表字面量；wire 层不 import llm。
* §12 输入面（`nsfr-2`）：定位读数按**句**分组为**预填槽位**（键 = 每一句的 sentence_id），
  判定字段一律留空；「留空」不是「通过」——照抄槽位回填必须 fail-closed。`v1` 的冻结资产
  与指纹仍在册（历史 run 的 `prompt_version` 必须还能对上号）。
* §13 r9 现场（`nsfr-2` 的定点返修）：同句三个原子（两个年份 + 一个变动值）**各自单独作答**
  即通过；**合并**成一条更宽的表述、**漏掉**其中一个年份，都必须因「已定位表面无人认领」
  而 fail-closed（r9 的真实返回正是把两个年份合成一条期间原子）；改指标、丢代理口径限定语
  在**同一份槽位面**上各有一条负例。

**诚实的边界（逐条给可执行证据，不含糊声明）**：

* 表格单元格**不在**本门覆盖面内：`NarrativeTableRow` 没有 `sentence_id`（它按 `row_id` 内容
  寻址），本门的覆盖面是 `NS.fact_bearing_sentence_ids`。表格一侧由 `natfid-1` 的表面比对与
  「行内非空单元格 ↔ Claim 逐位同数」的构造约束承担。这是**已登记的缺口**，不是「覆盖了」。
* 定位器**不产** `subject_or_metric`，`约` 这类模糊语也不在它的读数里（加词表就是写死指标名 /
  答案关键词）。这两类只能由判定面补出；模型若只报机械定位的那几条、而放过自己读出来的越权
  成分，本门**拦不住**——这是所有 LLM 判定门的共同边界。§5 给出的是可执行的反面证据：
  同一个输入，模型如实报告即被拒、而机械层单独看是放行的。

夹具纪律：不读库、不连网、不写任何文件、不调真实模型；文本夹具全部公司无关
（`示例科技有限公司` / 通用 `公司`），不写固定页码、不写答案关键词、不为任何一家公司写专用规则。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as HS
from llm import client as llm
from sections import final_sentence_fidelity as FSF
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

REPO = Path(__file__).resolve().parent.parent

TOPIC = "topic-company-business"
SECTION = "company"
FP = "a" * 64
MEMBER_LOCATOR = NS.char_range_locator("evidence:ev-1", 3, 40)
DRAFT_ID = "draft-1"
REVISION = "rev-1"

#: 冻结的 `final_sentence_fidelity_v1`（revision `nsfr-1`）正文指纹。`nsfr-2` 只许**新增**
#: v2 资产文件；就地改写 v1 会让历史 run 记录的 `prompt_version` 指向另一份正文。
FROZEN_V1_PROMPT_SHA256 = "77e1313870c8d9e493ae0684c78291d196cd99d7432d536e8dbe4dd9a83005e3"


# ---------------------------------------------------------------------------
# 夹具：真实形状的 Claim / factual 支撑边 / Narrative / 权威读视图
# ---------------------------------------------------------------------------

def _binding(*, fact_id: str = "f-1", draft_revision: str = REVISION,
             semantics: str = "factual", path: str = "path_a_prevalidated",
             subject_kind: str = "claim_candidate", container: str = "pack-1"):
    """一条真实形状的支撑边。路径 A 走权威事实（`fact_id`），路径 B / context 走精确材料。"""
    kw = dict(proposed_support_id=f"psr-{fact_id}", proposal_content_hash=FP,
              binding_subject_kind=subject_kind, binding_subject_id="cc-1",
              draft_revision=draft_revision, binding_decision_id="cbd-1",
              support_set_digest="d" * 64, authority_kind="topic_pack",
              authority_container_id=container, source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="primary",
              support_semantics=semantics, authorization_path=path,
              content_fingerprint=FP, entailment_decision_id=None, fact_id=fact_id,
              material_id="m-1", payload_ref={"object_type": "research_material"},
              locator_ref=MEMBER_LOCATOR)
    if semantics == "factual":
        kw["entailment_decision_id"] = f"ced-{fact_id}"
    else:
        kw["authorization_path"] = "context_only"
        kw["fact_id"] = None
    return NS.AcceptedSupportBinding.create(**kw)


def _claim(text: str, index: int, *, section: str = SECTION, bindings=()) -> SS.SectionClaim:
    """一条真实形状的 current Claim（它的 `claim_id` 把支撑边身份也含进内容寻址）。"""
    question_ids = (f"q-{index}",)
    refs = (HS.CitationRef(ref_type="evidence", evidence_id=f"evidence:ev-{index}"),)
    return SS.SectionClaim(
        claim_id=SS.derive_claim_id("fact", TOPIC, question_ids, text, refs,
                                    f"cand-{index}", REVISION, tuple(bindings)),
        schema_version=SS.CLAIM_SCHEMA_VERSION, section_id=section, topic_id=TOPIC,
        question_ids=question_ids, text=text, claim_type="fact", citation_refs=refs,
        claim_candidate_id=f"cand-{index}", claim_candidate_revision=REVISION,
        accepted_binding_ids=tuple(bindings))


def _claim_and_binding(text: str, index: int, *, section: str = SECTION):
    binding = _binding(fact_id=f"f-{index}")
    return _claim(text, index, section=section,
                  bindings=(binding.accepted_support_binding_id,)), binding


def _citations(claims) -> list[str]:
    out: list[str] = []
    for claim in claims:
        for ref in claim.citation_refs:
            cid = SS.derive_citation_id(claim.claim_id, ref)
            if cid not in out:
                out.append(cid)
    return out


def _sentence_spec(text: str, kind: str, claims, *, context=()) -> dict:
    return {"text": text, "sentence_kind": kind,
            "claim_ids": [c.claim_id for c in claims],
            "citation_ids": _citations(claims),
            "context_binding_ids": tuple(context)}


def _narrative(specs, *, draft_id: str = DRAFT_ID, revision: str = REVISION):
    paragraph = NS.NarrativeParagraph.create(section_id=SECTION, topic_ids=(TOPIC,), index=0,
                                             sentence_specs=tuple(specs))
    return NS.SectionNarrative.create(
        task_id="task-1", section_id=SECTION, section_draft_id=draft_id,
        draft_revision=revision, paragraphs=(paragraph,), tables=(),
        context_binding_ids=())


class _Authority:
    """权威事实的**确定性读视图**（`CBG.authority_fact_table` 唯一需要的接口是 `.facts`）。"""

    def __init__(self, facts=()):
        self.facts = tuple(facts)


def _fact_entry(fact_id: str, text: str) -> PW.AuthorityFactEntry:
    return PW.AuthorityFactEntry(
        authority_kind="topic_pack", container_identity="pack-1", fact_id=fact_id, text=text,
        topic_id=TOPIC, aspect_ids=("a-1",), required=True, fact_type="metric",
        period="2024", scope="示例科技有限公司", material_id="m-1",
        payload_ref={"object_type": "research_material"}, locator_ref=MEMBER_LOCATOR,
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        content_fingerprint=FP)


def _bundle(narrative, claims, bindings, *, authority=None, manifest=None,
            material_context=None):
    return FSF.SentenceFidelityBundle(
        narrative=narrative, claims=tuple(claims), accepted_bindings=tuple(bindings),
        authority=authority, manifest=manifest, material_context=material_context)


# ---------------------------------------------------------------------------
# 端到端正例用的完整真实形状：一条 Claim + 它的 factual 路径 A 边 + 权威读视图
# ---------------------------------------------------------------------------
E2E_TEXT = "公司2024年营业收入为1,234.56亿元。"
E2E_BINDING = _binding(fact_id="f-e2e")
E2E_CLAIM = _claim(E2E_TEXT, 90, bindings=(E2E_BINDING.accepted_support_binding_id,))
E2E_NARRATIVE = _narrative([_sentence_spec(E2E_TEXT, "natural", [E2E_CLAIM])])
E2E_BUNDLE = _bundle(E2E_NARRATIVE, [E2E_CLAIM], [E2E_BINDING],
                     authority=_Authority([_fact_entry("f-e2e", E2E_TEXT)]))

#: 六类原子各至少一条的夹具（公司无关；`示例科技有限公司` 是占位名，不是任何真实公司）。
S2_TEXT = ("示例科技有限公司2024年营业收入为1,234.56亿元，报告期内部分产品用于储能领域，"
           "本表合计为3,000万元，同比未发生重大变化。")


class _StubClient:
    """桩：**不做**任何判定，只把给定文本包成 `NarrationResult`（真实调用路径的全部元数据）。"""

    def __init__(self, text: str, *, status: str = "ok",
                 prompt_version: str = FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
                 model: str | None = None, error: str = ""):
        self.text = text
        self.status = status
        self.prompt_version = prompt_version
        self.model = model or PW.resolve_model_policy(PW.MODEL_POLICY_STUB)
        self.error = error
        self.calls = 0

    def evaluate(self, *, messages, system, prompt_version, model_policy):
        self.calls += 1
        return PW.NarrationResult(text=self.text, call_id="call-fsf-1", model=self.model,
                                 prompt_version=self.prompt_version, status=self.status,
                                 error=self.error)


def _atom_row(kind: str, surface: str, claim_id, *, verdict: str = "entailed",
              reason_code=None, bindings=()) -> dict:
    return {"atom_kind": kind, "atom_surface": surface, "claim_id": claim_id,
            "accepted_binding_ids": list(bindings), "verdict": verdict,
            "reason_code": reason_code, "rationale": "夹具"}


def _located_rows(sentence, claim, binding, *, extra=()) -> list[dict]:
    """先按**机械定位**给每一句配一份齐备的原子行，再并入模型自己补出来的那些。

    这不是「模型只会照抄定位」：§5 的每一条反例都**额外**加了定位器抓不到的原子，
    正是要证「可以、也应当补充」是可执行的。
    """
    bids = (binding.accepted_support_binding_id,)
    rows = [_atom_row(a.atom_kind, a.atom_surface, claim.claim_id, bindings=bids)
            for a in FSF.locate_fact_atoms(sentence_id=sentence.sentence_id,
                                           text=sentence.text,
                                           claim_texts={claim.claim_id: claim.text})]
    rows.extend(extra)
    return rows


def _input_face(bundle) -> dict:
    """本门真实的 LLM 输入面（`build_sentence_fidelity_messages` 的 user 正文，`nsfr-2`）。"""
    messages, _system = FSF.build_sentence_fidelity_messages(bundle)
    return json.loads(messages[0]["content"])


def _claimed(row: dict, claim, binding_ids) -> dict:
    """把一行**预填槽位**逐行作答（正例的答法：认领它声明的 Claim 与 factual 支撑边）。"""
    return {"atom_kind": row["atom_kind"], "atom_surface": row["atom_surface"],
            "claim_id": claim.claim_id, "accepted_binding_ids": list(binding_ids),
            "verdict": "entailed", "reason_code": None, "rationale": "夹具"}


def _payload(per_sentence, verdict: str, reason_code) -> dict:
    return {"per_sentence": per_sentence, "verdict": verdict, "reason_code": reason_code}


def _payload_text(per_sentence, verdict: str, reason_code) -> str:
    return json.dumps(_payload(per_sentence, verdict, reason_code), ensure_ascii=False)


def _natfid(text: str, claim) -> dict:
    """`natfid-1` 对这一句的读数（既有只读表面比较器，本批一字未动）。"""
    report = NS.verify_sentence_fidelity(text=text, claims={claim.claim_id: claim.text})
    return {"ok": report.ok, "defects": list(report.defects()),
            "added": list(report.added_surfaces),
            "dropped": [list(x) for x in report.dropped_surfaces],
            "extra": [list(x) for x in report.added_scope_or_strength]}


#: 三个盲区的文本夹具（公司无关；每条各配一条真实形状的声明 Claim 与支撑边）。
BLIND_SCOPE_CLAIM, BLIND_SCOPE_BINDING = _claim_and_binding(
    "公司2024年资产负债率变动为-1.2个百分点。", 1)
BLIND_SCOPE_SENTENCE = "公司2024年资产负债率变动约为-1.2个百分点。"
BLIND_METRIC_CLAIM, BLIND_METRIC_BINDING = _claim_and_binding("公司2024年资产负债率为58.20%。", 2)
BLIND_METRIC_SENTENCE = "公司2024年有息负债率为58.20%。"
BLIND_PROXY_CLAIM, BLIND_PROXY_BINDING = _claim_and_binding(
    "公司2024年财务费用为-21.35亿元，代理口径（PROXY_FINANCE_EXPENSES）。", 3)
BLIND_PROXY_SENTENCE = "公司2024年财务费用为-21.35亿元。"


def _blind_case(sentence_text: str, claim, binding, extra_atom: dict) -> dict:
    """对一句「`natfid-1` 放行、B 拒绝」的盲区样本，同时给出两个读数。"""
    narrative = _narrative([_sentence_spec(sentence_text, "natural", [claim])])
    bundle = _bundle(narrative, [claim], [binding])
    sentence = bundle.sentences()[0]
    payload = _payload(
        [{"sentence_id": sentence.sentence_id,
          "atoms": _located_rows(sentence, claim, binding, extra=[extra_atom])}],
        "rejected", "atom_not_entailed")
    atoms, verdict, reason = FSF.interpret_sentence_fidelity_output(
        payload, sentence_ids=(sentence.sentence_id,),
        located_by_sentence=FSF.located_readings(bundle))
    return {"natfid": _natfid(sentence_text, claim), "b_verdict": verdict,
            "b_reason": reason, "b_atoms": atoms,
            "b_located": FSF.located_readings(bundle)[sentence.sentence_id]}


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
                      exc: type[BaseException] = FSF.FinalSentenceFidelityError) -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：异常类型对，但信息里没有 {needle!r}（实测 {e}）")
            else:
                passed += 1
        except BaseException as e:  # noqa: BLE001 - 反例：抛错也要如实记账
            failed += 1
            details.append(f"FAIL {msg}：抛了 {type(e).__name__} 而不是 {exc.__name__}（{e}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：没有抛错")

    # ======================================================= §1 版本与资产锚
    check(NS.FINAL_SENTENCE_DECISION_SCHEMA_VERSION == "nsfid-1",
          f"决定 schema 版本，实测 {NS.FINAL_SENTENCE_DECISION_SCHEMA_VERSION!r}")
    check(NS.FINAL_SENTENCE_RULES_VERSION == "nsfr-1",
          f"规则版本，实测 {NS.FINAL_SENTENCE_RULES_VERSION!r}")
    check(FSF.FINAL_SENTENCE_FIDELITY_RULES_VERSION == NS.FINAL_SENTENCE_RULES_VERSION,
          "规则版本只有一个所有者（wire），判定面只能 re-export，不得自行再写一个常量")
    check(FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION == "final_sentence_fidelity_v2@nsfr-2",
          f"登记的 prompt 版本，实测 {FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION!r}")
    check(FSF.FINAL_SENTENCE_FIDELITY_MODEL_POLICY in PW.MODEL_POLICIES,
          "本门的缺省 model policy 必须在已登记的策略集内")
    check(set(NS.FINAL_SENTENCE_DECISION_VERDICTS) == {"entailed", "rejected"}
          and set(NS.FINAL_SENTENCE_DECISION_REJECTION_REASONS)
          == {"atom_not_entailed", "atom_not_located"},
          "决定层的封闭结果集与拒绝原因集")
    check(set(NS.FINAL_SENTENCE_ATOM_VERDICTS)
          == {"entailed", "not_entailed", "atom_not_located"},
          "逐原子核验的封闭结果集")
    check("subject_or_metric" in NS.FINAL_SENTENCE_ATOM_KINDS,
          "「主体或指标被换掉」必须在封闭原子类别里有一个位置，否则这条越权无处可写")
    check(len(set(NS.FINAL_SENTENCE_ATOM_REASON_CODES))
          == len(NS.FINAL_SENTENCE_ATOM_REASON_CODES),
          "逐原子原因码是封闭集合且无重复")

    asset = FSF.verify_final_sentence_fidelity_prompt_asset()
    check(bool(asset.strip()), "prompt 资产必须能加载且非空")
    check(PW.prompt_asset_fingerprint(asset) == FSF.FINAL_SENTENCE_FIDELITY_PROMPT_SHA256,
          "登记的正文指纹必须与资产实际正文一致")
    check("七条轴" in asset,
          "下面那条「正文被改」反例的前提：资产里确实有「七条轴」这几个字"
          "（否则那条反例是恒失败的假证据）")
    # 「登记版本 = 资产自报 = 正文指纹」三侧；下面三种漂移各一条反例。
    expect_raises("资产自报别的修订号必须拒（不得用旧记录冒充）",
                  lambda: FSF.verify_final_sentence_fidelity_prompt_asset(
                      asset.replace("revision nsfr-2", "revision nsfr-3", 1)),
                  "不一致")
    expect_raises("资产正文被改必须拒（资产名与修订号可被照抄，正文不行）",
                  lambda: FSF.verify_final_sentence_fidelity_prompt_asset(
                      asset.replace("七条轴", "八条轴", 1)),
                  "正文指纹")
    expect_raises("资产未自报 (资产名, revision) 必须拒（身份无从证明）",
                  lambda: FSF.verify_final_sentence_fidelity_prompt_asset("没有任何声明的资产"),
                  "未自报")
    # v1 是**冻结**资产：换输入面只许新增 v2 文件，不得就地改写 v1——否则历史 run 记录的
    # `prompt_version` 会指向另一份正文，改一次就会让所有历史核验读数无从复核。
    frozen_v1 = llm.load_prompt("final_sentence_fidelity_v1")
    check("final_sentence_fidelity_v1，revision nsfr-1" in frozen_v1
          and PW.prompt_asset_fingerprint(frozen_v1) == FROZEN_V1_PROMPT_SHA256,
          "冻结的 v1 资产必须仍能加载且正文逐字未变（新版本只许新增文件，不许就地改写）")
    check(FSF.FINAL_SENTENCE_FIDELITY_PROMPT_ASSET != "final_sentence_fidelity_v1",
          "输入面改过就必须换资产版本：不能让同一个版本号在登记表里指向两份不同的正文")
    # prompt 不得与 wire 漂移：每一档枚举都必须在资产正文里逐项出现。
    for label, table in (("原子类别", NS.FINAL_SENTENCE_ATOM_KINDS),
                         ("逐原子结果", NS.FINAL_SENTENCE_ATOM_VERDICTS),
                         ("逐原子原因码", NS.FINAL_SENTENCE_ATOM_REASON_CODES),
                         ("决定结果", NS.FINAL_SENTENCE_DECISION_VERDICTS),
                         ("决定拒绝原因", NS.FINAL_SENTENCE_DECISION_REJECTION_REASONS)):
        missing = [x for x in table if x not in asset]
        check(not missing,
              f"{label}必须逐项写在资产里（判定面与 prompts 不得漂移），缺 {missing}")
    probe = NS.FinalSentenceFidelityDecision.create(
        task_id="t", section_id="s", draft_id="d", draft_revision="r", narrative_id="n",
        sentence_ids=("sent-1",),
        atoms=(NS.SentenceAtomReading(sentence_id="sent-1", atom_kind="numeric",
                                      atom_surface="1亿元", claim_id="claim-1",
                                      accepted_binding_ids=("asb-1",), verdict="entailed"),),
        support_set_digest="d" * 64, prompt_version="p", model_policy="stub",
        verdict="entailed", reason_code=None, call_id="call-1")
    check(probe.rubric_version == NS.FINAL_SENTENCE_RULES_VERSION,
          "不显式给 rubric_version 时取 wire 的规则版本（不得留空）")

    # ======================================================= §2 确定性原子定位器
    located = FSF.locate_fact_atoms(sentence_id="s-1", text=S2_TEXT)
    kinds = {a.atom_kind for a in located}
    check({"numeric", "period", "negation_or_state", "entity_head", "table_relation",
           "scope_or_strength"} <= kinds,
          f"六类机械可定位的原子都要出现，实测 {sorted(kinds)}")
    check("subject_or_metric" not in kinds,
          "定位器**不产** `subject_or_metric`：认指标名需要指标词表，而词表化指标名正是"
          "「写死答案关键词」（这一档只能由判定面补出）")
    check([a.atom_ref for a in located] == [f"la-{i + 1}" for i in range(len(located))],
          "原子引用号必须按定位顺序编号（确定性）")
    check(all(a.sentence_id == "s-1" for a in located),
          "每条原子都必须挂回它所属的句子")
    check(len({(a.atom_kind, a.atom_surface) for a in located}) == len(located),
          "同一句里同一 (类别, 表面) 只算一条原子（重复只增加要认领的行数，不增加覆盖面）")
    check(FSF.locate_fact_atoms(sentence_id="s-1", text=S2_TEXT) == located,
          "同输入必须得到同一读数（可复算，无隐藏状态）")
    # 逐类接线：抽取器报出的每一条表面，都必须在定位结果里以**映射后的类别**出现。
    wiring = (("numeric", NS.scan_numeric_tokens(S2_TEXT)),
              ("period", NS.vague_period_hits(S2_TEXT)),
              ("entity_head", NS.entity_head_nouns(S2_TEXT)),
              ("negation_or_state", tuple(m for m in NS.marker_hits(S2_TEXT)
                                          if m not in NS.TABLE_RELATION_SURFACE_MARKERS)),
              ("table_relation", tuple(m for m in NS.marker_hits(S2_TEXT)
                                       if m in NS.TABLE_RELATION_SURFACE_MARKERS)),
              ("scope_or_strength", tuple(m for markers in
                                          (NS.SCOPE_QUALIFIER_MARKERS,
                                           NS.CONCLUSION_STRENGTH_MARKERS,
                                           NS.CAUSAL_ASSERTION_MARKERS)
                                          for m in NS._fidelity_marker_hits(S2_TEXT, markers)
                                          if not (len(m) < 2
                                                  and m in NS.FIDELITY_SINGLE_CHAR_MARKERS))))
    seen_kinds = {(a.atom_kind, a.atom_surface) for a in located}
    for kind, surfaces in wiring:
        if not surfaces:
            note(f"NOTE §2 接线：{kind} 的抽取器在这条夹具上返回空，无法验证接线")
            continue
        missing = [s for s in surfaces if (kind, s) not in seen_kinds]
        check(not missing, f"{kind} 抽取器的读数必须进定位结果且类别正确，缺 {missing}")
    check([a.atom_surface for a in located if a.atom_kind == "negation_or_state"] == ["未发生"]
          and "合计" in [a.atom_surface for a in located if a.atom_kind == "table_relation"],
          "两张同源子表的分流必须两边都在（分离不得把一边吃掉）")
    check(all(a.declared_by_claim_id is None for a in located),
          "不传声明面时每条的 declared_by_claim_id 都是 None（「没有声明面」≠「未被声明」）")
    declared = FSF.locate_fact_atoms(sentence_id="s-1", text=S2_TEXT,
                                     claim_texts={"claim-b": S2_TEXT, "claim-a": S2_TEXT})
    check({a.declared_by_claim_id for a in declared} == {"claim-a"},
          "多条 Claim 都含同一表面时取**最小 id**（确定性，不得随映射顺序漂移）")
    check(FSF.locate_fact_atoms(sentence_id="s-1", text=S2_TEXT,
                                claim_texts={"claim-a": "公司2024年营业收入为1,234.56亿元。"})
          != declared,
          "声明面确实参与读数（否则上一项会因为「恒为同一个值」而通过）")
    expect_raises("空 sentence_id 必须拒（原子必须挂在一句真实的最终句上）",
                  lambda: FSF.locate_fact_atoms(sentence_id="", text=S2_TEXT), "不得为空")
    check(FSF.locate_fact_atoms(sentence_id="s-1", text=None) == (),
          "非字符串文本按空处理（定位器不替调用方判断前提）")
    avg = FSF.locate_fact_atoms(sentence_id="s-1", text="公司平均融资成本为3.2%")
    check([a.atom_surface for a in avg] == ["3.2%"],
          f"单字补语阈值：`平均` 里的 `均` 不得被读成范围语（与 `natfid-1` **同一条**跳过规则），"
          f"实测 {[a.atom_surface for a in avg]}")
    check("部分" in [a.atom_surface for a in FSF.locate_fact_atoms(
              sentence_id="s-1", text="公司部分产品用于储能领域。")],
          "阈值不得修过头：长度 ≥ 2 的范围语照旧定位")

    # ======================================================= §3 定位 ≠ 判定
    check("verdict" not in {f.name for f in dataclasses.fields(FSF.LocatedAtom)},
          "`LocatedAtom` 在类型层不得表达判定（否则「定位」与「判定」会被读成一件事）")
    scope_located = FSF.locate_fact_atoms(sentence_id="s-1", text=BLIND_SCOPE_SENTENCE)
    check("约" not in [a.atom_surface for a in scope_located],
          "定位器**看不到**模糊语 `约`（它没有、也不允许有这张词表）——这条边界登记在案，"
          "由判定面补出（见 §5），不在这里「修好」")
    metric_located = FSF.locate_fact_atoms(sentence_id="s-1", text=BLIND_METRIC_SENTENCE)
    check(not any(a.atom_kind == "subject_or_metric" for a in metric_located),
          "换掉的指标名同样不在机械读数里")

    # ======================================================= §4 判定面 fail-closed
    SID = "sent-1"
    LOCATED = {SID: ("1,234.56亿元",)}
    good = _atom_row("numeric", "1,234.56亿元", "claim-1",
                     bindings=(E2E_BINDING.accepted_support_binding_id,))

    def interpret(payload, *, sentence_ids=(SID,), located=LOCATED):
        return FSF.interpret_sentence_fidelity_output(
            payload, sentence_ids=sentence_ids, located_by_sentence=located)

    def _wire(**kw) -> NS.SentenceAtomReading:
        base = dict(sentence_id=SID, atom_kind="numeric", atom_surface="1亿元",
                    claim_id="claim-1", accepted_binding_ids=("asb-1",), verdict="entailed")
        base.update(kw)
        return NS.SentenceAtomReading(**base)

    expect_raises("输出不是合法 JSON：不伪造决定",
                  lambda: FSF._parse_json_object("这不是 JSON", what="最终句语义核验"),
                  "不是合法 JSON")
    check(FSF._parse_json_object('```json\n{"a": 1}\n```', what="x") == {"a": 1},
          "整整一层 markdown 围栏可容忍（真实模型的常见形态）")
    expect_raises("顶层不是对象必须拒",
                  lambda: FSF._parse_json_object("[1, 2]", what="x"), "必须是 JSON 对象")
    expect_raises("per_sentence 不是数组必须拒",
                  lambda: interpret(_payload("x", "entailed", None)), "必须是数组")
    expect_raises("per_sentence 的项不是对象必须拒",
                  lambda: interpret(_payload(["x"], "entailed", None)), "必须是 JSON 对象")
    expect_raises("缺 sentence_id 必须拒",
                  lambda: interpret(_payload([{"atoms": []}], "entailed", None)),
                  "必须给出 sentence_id")
    expect_raises("出现未被覆盖的句子必须拒",
                  lambda: interpret(_payload([{"sentence_id": "sent-9", "atoms": []},
                                              {"sentence_id": SID, "atoms": [good]}],
                                             "entailed", None)),
                  "未被本 revision 覆盖")
    expect_raises("同一句出现两次必须拒",
                  lambda: interpret(_payload([{"sentence_id": SID, "atoms": [good]},
                                              {"sentence_id": SID, "atoms": [good]}],
                                             "entailed", None)),
                  "出现多次")
    expect_raises("atoms 不是数组必须拒",
                  lambda: interpret(_payload([{"sentence_id": SID, "atoms": None}],
                                             "entailed", None)), "必须是数组")
    expect_raises("原子行含未登记字段必须拒（不得自造字段）",
                  lambda: interpret(_payload(
                      [{"sentence_id": SID, "atoms": [dict(good, confidence="high")]}],
                      "entailed", None)), "未登记字段")
    expect_raises("原子类别越界必须拒",
                  lambda: interpret(_payload(
                      [{"sentence_id": SID, "atoms": [dict(good, atom_kind="vibes")]}],
                      "entailed", None)), "atom_kind")
    expect_raises("verdict 越界必须拒",
                  lambda: interpret(_payload(
                      [{"sentence_id": SID, "atoms": [dict(good, verdict="fine")]}],
                      "entailed", None)), "verdict")
    expect_raises("已定位的表面没有被逐条认领必须拒（「未发现原子」不得自动通过）",
                  lambda: interpret(_payload([{"sentence_id": SID, "atoms": []}],
                                             "entailed", None)),
                  "未被输出认领")
    expect_raises("覆盖面少一句必须拒",
                  lambda: interpret(_payload([], "entailed", None)),
                  "覆盖面与当前正文不等")
    expect_raises("定位读数的句子集与本次核验不等必须拒（辅助读数不得漏句）",
                  lambda: interpret(_payload([{"sentence_id": SID, "atoms": [good]}],
                                             "entailed", None), located={}),
                  "定位读数的句子集")
    expect_raises("聚合与逐原子结果相反必须拒",
                  lambda: interpret(_payload([{"sentence_id": SID, "atoms": [
                      dict(good, verdict="not_entailed", claim_id="claim-1",
                           reason_code="subject_or_metric_replaced")]}],
                      "entailed", None)),
                  "与逐原子结果推出")
    expect_raises("聚合把自己的拒绝说轻必须拒（不得用更宽松的聚合掩盖未通过的原子）",
                  lambda: interpret(_payload([{"sentence_id": SID, "atoms": [
                      dict(good, verdict="not_entailed", claim_id="claim-1",
                           reason_code="period_changed")]}],
                      "rejected", "atom_not_located")),
                  "与逐原子结果推出")
    atoms_ok, verdict_ok, reason_ok = interpret(
        _payload([{"sentence_id": SID, "atoms": [good]}], "entailed", None))
    check(verdict_ok == "entailed" and reason_ok is None and len(atoms_ok) == 1,
          "正例：全覆盖 + 全 entailed ⇒ 聚合 entailed 且无拒绝原因")

    # 三档 verdict 的字段约束（wire 层再拒一次，不假设解释层已挡住）
    expect_raises("entailed 不引支撑边必须拒（没有支撑边的原子不构成已核验的映射）",
                  lambda: _wire(accepted_binding_ids=()), "没有引任何 factual 支撑边",
                  exc=NS.NarrativeSchemaError)
    expect_raises("entailed 携带 reason_code 必须拒",
                  lambda: _wire(reason_code="period_changed"), "不得携带 reason_code",
                  exc=NS.NarrativeSchemaError)
    expect_raises("not_entailed 不得用 `atom_absent_from_claims`（有 claim_id 就不是无位置）",
                  lambda: _wire(verdict="not_entailed", reason_code="atom_absent_from_claims"),
                  "只属于 atom_not_located", exc=NS.NarrativeSchemaError)
    expect_raises("not_entailed 必须给封闭原因码",
                  lambda: _wire(verdict="not_entailed", reason_code=None),
                  "SentenceAtomReading.reason_code", exc=NS.NarrativeSchemaError)
    expect_raises("atom_not_located 不得携带 claim_id",
                  lambda: _wire(verdict="atom_not_located", claim_id="claim-1",
                                accepted_binding_ids=(),
                                reason_code="atom_absent_from_claims"),
                  "不得携带 claim_id", exc=NS.NarrativeSchemaError)
    expect_raises("atom_not_located 不得引支撑边",
                  lambda: _wire(verdict="atom_not_located", claim_id=None,
                                reason_code="atom_absent_from_claims"),
                  "不得引支撑边", exc=NS.NarrativeSchemaError)
    expect_raises("atom_not_located 只能有一个原因码",
                  lambda: _wire(verdict="atom_not_located", claim_id=None,
                                accepted_binding_ids=(), reason_code="period_changed"),
                  "atom_absent_from_claims", exc=NS.NarrativeSchemaError)
    dropped_proxy = _wire(atom_kind="scope_or_strength",
                          atom_surface="代理口径（PROXY_FINANCE_EXPENSES）",
                          verdict="not_entailed", reason_code="proxy_qualifier_dropped")
    check(dropped_proxy.atom_surface.startswith("代理口径"),
          "被丢掉的原子写它在**声明 Claim 里**的原样字（句子里本来就没有这几个字）")

    # ======================================================= §5 三个盲区
    scope_case = _blind_case(BLIND_SCOPE_SENTENCE, BLIND_SCOPE_CLAIM, BLIND_SCOPE_BINDING,
                             _atom_row("scope_or_strength", "约", BLIND_SCOPE_CLAIM.claim_id,
                                       verdict="not_entailed",
                                       reason_code="scope_or_strength_changed",
                                       bindings=(BLIND_SCOPE_BINDING
                                                 .accepted_support_binding_id,)))
    check(scope_case["natfid"]["ok"] and scope_case["natfid"]["defects"] == [],
          "盲区 1 前置：`natfid-1` 对「变动为 → 变动约为」**实测放行**（四组读数全空），"
          f"实测 {scope_case['natfid']}")
    check(scope_case["b_verdict"] == "rejected" and scope_case["b_reason"] == "atom_not_entailed"
          and ("scope_or_strength", "约", "not_entailed", "scope_or_strength_changed")
          in [(a.atom_kind, a.atom_surface, a.verdict, a.reason_code)
              for a in scope_case["b_atoms"]],
          f"盲区 1：B 在模型如实报告时拒绝并给出 typed 原因码，实测 "
          f"{scope_case['b_verdict']}/{scope_case['b_reason']}")
    check("约" not in scope_case["b_located"],
          "盲区 1 的成因是**可执行**的：`约` 不在机械定位读数里，只能由判定面补出"
          f"（定位读数实测 {scope_case['b_located']}）")

    metric_case = _blind_case(BLIND_METRIC_SENTENCE, BLIND_METRIC_CLAIM, BLIND_METRIC_BINDING,
                              _atom_row("subject_or_metric", "有息负债率",
                                        BLIND_METRIC_CLAIM.claim_id, verdict="not_entailed",
                                        reason_code="subject_or_metric_replaced",
                                        bindings=(BLIND_METRIC_BINDING
                                                  .accepted_support_binding_id,)))
    check(metric_case["natfid"]["ok"] and metric_case["natfid"]["defects"] == [],
          "盲区 2 前置：`natfid-1` 对「资产负债率 → 有息负债率」**实测放行**，"
          f"实测 {metric_case['natfid']}")
    check(metric_case["b_verdict"] == "rejected"
          and any(a.atom_kind == "subject_or_metric" and a.verdict == "not_entailed"
                  for a in metric_case["b_atoms"]),
          f"盲区 2：换掉指标名是同一位置换了一个断言，B 必须拒绝，实测 {metric_case['b_verdict']}")
    check("有息负债率" not in metric_case["b_located"],
          "盲区 2 的成因同样可执行：指标名不在机械定位读数里（这一类只能由判定面补出）")

    proxy_case = _blind_case(BLIND_PROXY_SENTENCE, BLIND_PROXY_CLAIM, BLIND_PROXY_BINDING,
                             _atom_row("scope_or_strength",
                                       "代理口径（PROXY_FINANCE_EXPENSES）",
                                       BLIND_PROXY_CLAIM.claim_id, verdict="not_entailed",
                                       reason_code="proxy_qualifier_dropped",
                                       bindings=(BLIND_PROXY_BINDING
                                                 .accepted_support_binding_id,)))
    check(proxy_case["natfid"]["ok"] and proxy_case["natfid"]["defects"] == [],
          "盲区 3 前置：`natfid-1` 对「丢掉代理口径限定语」**实测放行**，"
          f"实测 {proxy_case['natfid']}")
    check(proxy_case["b_verdict"] == "rejected"
          and any(a.reason_code == "proxy_qualifier_dropped" for a in proxy_case["b_atoms"]),
          f"盲区 3：代理口径是同一个断言的一部分，丢了就是改了断言，B 必须拒绝，"
          f"实测 {proxy_case['b_verdict']}")

    # ======================================================= §6 聚合与三档推导
    two_located = {SID: ("1,234.56亿元", "20%")}
    all_unlocated = _payload([{"sentence_id": SID, "atoms": [
        good,
        _atom_row("numeric", "20%", None, verdict="atom_not_located",
                  reason_code="atom_absent_from_claims")]}],
        "rejected", "atom_not_located")
    _, v, r = FSF.interpret_sentence_fidelity_output(all_unlocated, sentence_ids=(SID,),
                                                     located_by_sentence=two_located)
    check((v, r) == ("rejected", "atom_not_located"),
          "未通过的原子**全部**无位置 ⇒ aggregate 原因是 `atom_not_located`"
          "（读者读到了一条越出授权面的断言）")
    mixed = _payload([{"sentence_id": SID, "atoms": [
        good,
        _atom_row("numeric", "20%", None, verdict="atom_not_located",
                  reason_code="atom_absent_from_claims"),
        _atom_row("period", "2025年", "claim-1", verdict="not_entailed",
                  reason_code="period_changed")]}],
        "rejected", "atom_not_entailed")
    _, v2, r2 = FSF.interpret_sentence_fidelity_output(mixed, sentence_ids=(SID,),
                                                      located_by_sentence=two_located)
    check((v2, r2) == ("rejected", "atom_not_entailed"),
          "混合未通过 ⇒ aggregate 原因是 `atom_not_entailed`（不得只报较轻的那一类）")
    expect_raises("aggregate rejected 却没有任何未通过原子必须拒",
                  lambda: NS.FinalSentenceFidelityDecision.create(
                      task_id="t", section_id="s", draft_id="d", draft_revision="r",
                      narrative_id="n", sentence_ids=(SID,), atoms=(_wire(),),
                      support_set_digest="d" * 64, prompt_version="p", model_policy="stub",
                      verdict="rejected", reason_code="atom_not_entailed", call_id="c"),
                  "没有任何未通过的原子", exc=NS.NarrativeSchemaError)
    expect_raises("原子行挂在本决定未覆盖的句子上必须拒",
                  lambda: NS.FinalSentenceFidelityDecision.create(
                      task_id="t", section_id="s", draft_id="d", draft_revision="r",
                      narrative_id="n", sentence_ids=(SID,),
                      atoms=(_wire(sentence_id="sent-9"),),
                      support_set_digest="d" * 64, prompt_version="p", model_policy="stub",
                      verdict="entailed", reason_code=None, call_id="c"),
                  "未被本决定覆盖的句子", exc=NS.NarrativeSchemaError)

    # ======================================================= §7 决定身份与过期
    sent_ids = E2E_BUNDLE.sentence_ids()
    located_e2e = FSF.located_readings(E2E_BUNDLE)
    e2e_atoms, v_e2e, r_e2e = FSF.interpret_sentence_fidelity_output(
        _payload([{"sentence_id": s.sentence_id,
                   "atoms": _located_rows(s, E2E_CLAIM, E2E_BINDING)}
                  for s in E2E_BUNDLE.sentences()], "entailed", None),
        sentence_ids=sent_ids, located_by_sentence=located_e2e)
    decision = NS.FinalSentenceFidelityDecision.create(
        task_id=E2E_NARRATIVE.task_id, section_id=SECTION, draft_id=DRAFT_ID,
        draft_revision=REVISION, narrative_id=E2E_NARRATIVE.narrative_id,
        sentence_ids=sent_ids, atoms=e2e_atoms,
        support_set_digest=E2E_BUNDLE.support_set_digest(),
        prompt_version=FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
        model_policy=PW.MODEL_POLICY_STUB, verdict=v_e2e, reason_code=r_e2e, call_id="call-1")
    check(decision.final_sentence_fidelity_decision_id.startswith("nsfid_"),
          "决定身份前缀（与 `ncbg_` / `nced_` 分属不同命名空间）")
    check(NS.FinalSentenceFidelityDecision.from_dict(decision.to_dict()).to_dict()
          == decision.to_dict(), "决定必须逐字往返（持久化与只读回放的前提）")
    check(bool(decision.atom_verdicts())
          and all(len(row) == 5 for row in decision.atom_verdicts()),
          "逐原子读视图必须给出 (sentence_id, kind, surface, verdict, reason_code)")
    first_seen: list[str] = []
    for atom in decision.atoms:
        if atom.sentence_id not in first_seen:
            first_seen.append(atom.sentence_id)
    check(first_seen == list(sent_ids) and len(decision.atoms) >= len(sent_ids),
          "原子行的句子顺序必须与覆盖面逐位一致（每句至少一条原子，且不出现束外句子）")

    def _stale(**over) -> bool:
        kw = dict(draft_id=DRAFT_ID, section_id=SECTION, draft_revision=REVISION,
                  narrative_id=E2E_NARRATIVE.narrative_id, sentence_ids=sent_ids)
        kw.update(over)
        return decision.is_stale_for(**kw)

    check(not _stale(), "锚点与覆盖面都一致时不得判为过期")
    check(_stale(draft_id="draft-9"), "换过 draft_id 必须判过期")
    check(_stale(draft_revision="rev-2"), "换过 draft revision 必须判过期（结论只对那一版成立）")
    check(_stale(narrative_id="narr-9"), "换过 Narrative 身份必须判过期")
    check(_stale(sentence_ids=("sent-x",)), "改句（增删句子）必须判过期")
    changed_claim = _claim_and_binding("公司2024年营业收入为9,999.99亿元。", 91)[0]
    changed_ids = NS.fact_bearing_sentence_ids(
        _narrative([_sentence_spec(changed_claim.text, "natural", [changed_claim])]))
    check(changed_ids != sent_ids and _stale(sentence_ids=changed_ids),
          "改 Claim 文本会改句子身份 ⇒ 覆盖面一比就过期（不需要新哈希）")
    changed_binding = _binding(fact_id="f-other")
    check(changed_binding.accepted_support_binding_id
          != E2E_BINDING.accepted_support_binding_id,
          "换绑定必须换出一个不同的支撑边身份（否则「换过绑定」这件事无从观察）")
    rebound_claim = _claim(E2E_TEXT, 90,
                           bindings=(changed_binding.accepted_support_binding_id,))
    rebound_ids = NS.fact_bearing_sentence_ids(
        _narrative([_sentence_spec(E2E_TEXT, "natural", [rebound_claim])]))
    check(rebound_ids != sent_ids and _stale(sentence_ids=rebound_ids),
          "换绑定会改 Claim 身份 ⇒ 改句子身份 ⇒ 覆盖面一比就过期")
    check(NS.sentence_support_set_digest(
              draft_id=DRAFT_ID, draft_revision=REVISION,
              narrative_id=E2E_NARRATIVE.narrative_id, sentence_ids=sent_ids,
              accepted_binding_ids=(E2E_BINDING.accepted_support_binding_id,))
          != NS.sentence_support_set_digest(
              draft_id=DRAFT_ID, draft_revision=REVISION,
              narrative_id=E2E_NARRATIVE.narrative_id, sentence_ids=sent_ids,
              accepted_binding_ids=(changed_binding.accepted_support_binding_id,)),
          "换绑定必须换支撑集摘要（决定绑的就是这一份摘要）")

    # ======================================================= §8 输入束的结构门
    other_claim, other_binding = _claim_and_binding("公司2025年营业收入为2,000亿元。", 4,
                                                    section="industry")
    expect_raises("narrative 不是 SectionNarrative 必须拒",
                  lambda: _bundle("not-a-narrative", [E2E_CLAIM], [E2E_BINDING]),
                  "必须是 SectionNarrative")
    expect_raises("claims 只接受已定稿 SectionClaim",
                  lambda: _bundle(E2E_NARRATIVE, ["claim-object"], [E2E_BINDING]),
                  "SectionClaim")
    expect_raises("跨节 Claim 不得进入最终句核验",
                  lambda: _bundle(E2E_NARRATIVE, [E2E_CLAIM, other_claim], [E2E_BINDING]),
                  "不属于本节")
    expect_raises("accepted_bindings 只接受 AcceptedSupportBinding",
                  lambda: _bundle(E2E_NARRATIVE, [E2E_CLAIM], ["asb-1"]),
                  "AcceptedSupportBinding")
    context_binding = _binding(fact_id="f-ctx", semantics="context", path="context_only",
                               subject_kind="narrative_draft_unit")
    expect_raises("context 支撑边不得进入本门（context 不授权事实）",
                  lambda: _bundle(E2E_NARRATIVE, [E2E_CLAIM], [context_binding]),
                  "semantics 不是 factual")
    expect_raises("支撑边属于别的 revision 必须拒（不得在换过的正文上做语义核验）",
                  lambda: _bundle(E2E_NARRATIVE, [E2E_CLAIM],
                                  [_binding(fact_id="f-2", draft_revision="rev-2")]),
                  "draft_revision")
    order_probe = [_binding(fact_id="f-1"), _binding(fact_id="f-2")]
    ascending = sorted(order_probe, key=lambda b: b.accepted_support_binding_id)
    check(ascending != order_probe,
          "反例前提：这两条边的 id 确实分先后，且 `order_probe` 本身不是升序"
          "（否则「乱序」这条反例恒真）")
    expect_raises("支撑边必须按 id 升序（canonical order）",
                  lambda: _bundle(E2E_NARRATIVE, [E2E_CLAIM], order_probe), "升序")
    ghost_claim = _claim_and_binding("公司2024年营业收入为7,777.77亿元。", 5)[0]
    expect_raises("句子引用了本束未给出的 Claim 必须拒（授权面不完整）",
                  lambda: _bundle(_narrative([_sentence_spec(ghost_claim.text, "natural",
                                                             [ghost_claim])]),
                                  [E2E_CLAIM], [E2E_BINDING]),
                  "未给出的已接受 Claim")
    unused_claim = _claim_and_binding("公司2024年毛利率为20%。", 6)[0]
    expect_raises("束里带了没有任何句子引用的 Claim 必须拒（授权面必须恰好被用到）",
                  lambda: _bundle(E2E_NARRATIVE, [E2E_CLAIM, unused_claim], [E2E_BINDING]),
                  "没有任何句子引用")
    check(E2E_BUNDLE.sentence_ids() == sent_ids
          and E2E_BUNDLE.factual_binding_ids()
          == (E2E_BINDING.accepted_support_binding_id,)
          and E2E_BUNDLE.support_set_digest()
          == NS.sentence_support_set_digest(
              draft_id=DRAFT_ID, draft_revision=REVISION,
              narrative_id=E2E_NARRATIVE.narrative_id, sentence_ids=sent_ids,
              accepted_binding_ids=(E2E_BINDING.accepted_support_binding_id,)),
          "束的三个读视图：承载事实的句子集、factual 支撑边集、支撑集摘要")
    check(FSF.authority_facts(E2E_BUNDLE)[0].fact_id == "f-e2e"
          and FSF.material_members(E2E_BUNDLE) == (),
          "路径 A 的边解析出权威事实、且不产生材料成员（两条路径的解析不混）")

    # ======================================================= §9 一次真调用（桩）
    e2e_text = _payload_text(
        [{"sentence_id": s.sentence_id,
          "atoms": _located_rows(s, E2E_CLAIM, E2E_BINDING)}
         for s in E2E_BUNDLE.sentences()], "entailed", None)
    stub = _StubClient(e2e_text)
    live = FSF.evaluate_final_sentence_fidelity(E2E_BUNDLE, llm_client=stub,
                                                model_policy=PW.MODEL_POLICY_STUB)
    check(stub.calls == 1, f"每个 draft revision 至多一次调用，实测 {stub.calls} 次")
    check(live.verdict == "entailed" and live.reason_code is None
          and live.call_id == "call-fsf-1",
          "正例：产出恰好一条 entailed 决定，并带上调用元数据")
    check(live.prompt_version == FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION
          and live.model_policy == PW.MODEL_POLICY_STUB
          and live.rubric_version == NS.FINAL_SENTENCE_RULES_VERSION,
          "决定必须记下 prompt 版本、model policy 与规则版本（三样都不能空）")
    check(live.support_set_digest == E2E_BUNDLE.support_set_digest(),
          "决定绑定的支撑集摘要必须是本 revision 的那一份")
    calls: list[str] = []
    batch = FSF.evaluate_final_sentence_fidelities(
        (E2E_BUNDLE,), llm_client=stub, model_policy=PW.MODEL_POLICY_STUB,
        on_call=lambda b: calls.append(b.narrative.narrative_id))
    check(len(batch) == 1 and calls == [E2E_NARRATIVE.narrative_id],
          "批量入口逐个 revision 各一次调用，观测钩子不得改判定")
    expect_raises("同一 draft 在批量输入里出现两次必须拒（每个 revision 恰好一条决定）",
                  lambda: FSF.evaluate_final_sentence_fidelities(
                      (E2E_BUNDLE, E2E_BUNDLE), llm_client=stub,
                      model_policy=PW.MODEL_POLICY_STUB),
                  "出现多次")
    expect_raises("bundle 类型不对必须拒",
                  lambda: FSF.evaluate_final_sentence_fidelity("not-a-bundle", llm_client=stub,
                                                              model_policy=PW.MODEL_POLICY_STUB),
                  "SentenceFidelityBundle")
    expect_raises("未登记的 model policy 必须拒",
                  lambda: FSF.evaluate_final_sentence_fidelity(E2E_BUNDLE, llm_client=stub,
                                                               model_policy="free"),
                  "model policy")
    expect_raises("调用失败不得产出决定（封闭原因码里没有「调用失败」）",
                  lambda: FSF.evaluate_final_sentence_fidelity(
                      E2E_BUNDLE, model_policy=PW.MODEL_POLICY_STUB,
                      llm_client=_StubClient("", status="error", error="网络中断")),
                  "不伪造决定")
    expect_raises("实际调用版本与记录不符必须拒（不得一个写在决定、另一个实际调用）",
                  lambda: FSF.evaluate_final_sentence_fidelity(
                      E2E_BUNDLE, model_policy=PW.MODEL_POLICY_STUB,
                      llm_client=_StubClient(e2e_text,
                                             prompt_version="final_sentence_fidelity_v1@nsfr-0")),
                  "不一致")
    expect_raises("实际调用模型与 model policy 解析值不符必须拒",
                  lambda: FSF.evaluate_final_sentence_fidelity(
                      E2E_BUNDLE, model_policy=PW.MODEL_POLICY_STUB,
                      llm_client=_StubClient(e2e_text, model="some-other-model")),
                  "model policy")
    expect_raises("非 JSON 输出必须拒（不得静默当成通过）",
                  lambda: FSF.evaluate_final_sentence_fidelity(
                      E2E_BUNDLE, model_policy=PW.MODEL_POLICY_STUB,
                      llm_client=_StubClient("我看了一下，没问题")),
                  "不是合法 JSON")

    # ======================================================= §10 决定的确定性映射
    other_narrative = _narrative([_sentence_spec(E2E_TEXT, "natural", [E2E_CLAIM])],
                                 draft_id="draft-2", revision="rev-2")
    other_decision = NS.FinalSentenceFidelityDecision.create(
        task_id="task-1", section_id=SECTION, draft_id="draft-2", draft_revision="rev-2",
        narrative_id=other_narrative.narrative_id,
        sentence_ids=NS.fact_bearing_sentence_ids(other_narrative), atoms=(),
        support_set_digest="d" * 64,
        prompt_version=FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
        model_policy=PW.MODEL_POLICY_STUB, verdict="entailed", reason_code=None, call_id="c-2")
    pairs = FSF.map_decisions_to_narratives((E2E_NARRATIVE, other_narrative),
                                            (live, other_decision))
    check([d.draft_id for _n, d in pairs] == [DRAFT_ID, "draft-2"],
          "映射只按 draft_id 建立（不按输入顺序配对：顺序配对会在丢一条时静默错配）")
    check(FSF.map_decisions_to_narratives((E2E_NARRATIVE,), (live,))[0][1] is live,
          "正例：锚点与覆盖面都一致的决定必须被接受")
    expect_raises("某个 draft 缺决定必须拒（缺失即阻断定稿）",
                  lambda: FSF.map_decisions_to_narratives((E2E_NARRATIVE, other_narrative),
                                                          (live,)),
                  "必须恰有一条")
    expect_raises("同一 draft 配到两条决定必须拒",
                  lambda: FSF.map_decisions_to_narratives((E2E_NARRATIVE,), (live, live)),
                  "必须恰有一条")
    expect_raises("决定指向输入之外的 draft 必须拒（不得静默丢弃）",
                  lambda: FSF.map_decisions_to_narratives((E2E_NARRATIVE,),
                                                          (live, other_decision)),
                  "不在输入里")
    stale_decision = NS.FinalSentenceFidelityDecision.create(
        task_id="task-1", section_id=SECTION, draft_id=DRAFT_ID, draft_revision=REVISION,
        narrative_id=E2E_NARRATIVE.narrative_id, sentence_ids=("sent-old",), atoms=(),
        support_set_digest="d" * 64,
        prompt_version=FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
        model_policy=PW.MODEL_POLICY_STUB, verdict="entailed", reason_code=None, call_id="c-3")
    expect_raises("过期的决定不得当作有效",
                  lambda: FSF.map_decisions_to_narratives((E2E_NARRATIVE,), (stale_decision,)),
                  "已过期")

    # ======================================================= §11 唯一实现与机械纪律
    implementers = sorted(p.name for p in (REPO / "sections").glob("*.py")
                          if "def locate_fact_atoms" in p.read_text(encoding="utf-8"))
    check(implementers == ["final_sentence_fidelity.py"],
          f"原子定位只能有一份实现（实测 {implementers}）：口径分叉是最难发现的一类缺陷")
    check(set(NS.TABLE_RELATION_SURFACE_MARKERS) <= set(NS.HIGH_RISK_SURFACE_MARKERS),
          "`TABLE_RELATION_SURFACE_MARKERS` 必须是高风险表面表的**具名切片**，不是第二份词表")
    check(len(set(NS.TABLE_RELATION_SURFACE_MARKERS)) == len(NS.TABLE_RELATION_SURFACE_MARKERS)
          and all(m in NS.TABLE_RELATION_SURFACE_MARKERS for m in
                  ("合计", "小计", "总计", "占比", "其中", "同比", "环比", "较上年", "较上期",
                   "增减", "差额", "平均值", "本表", "下表", "上表")),
          "切片必须逐条在册（取个名字不得顺手改内容）")
    fsf_src = (REPO / "sections" / "final_sentence_fidelity.py").read_text(encoding="utf-8")
    check("宁德" not in fsf_src and "300750" not in fsf_src,
          "本门不得内嵌任何真实公司名或答案关键词（含注释与文档）")
    fsf_tree = ast.parse(fsf_src)
    docstrings: set[int] = set()
    for node in ast.walk(fsf_tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    #: **代码面**（非文档字符串的字符串字面量 + 标识符）。文档里写清「哪一类越权拦不住」是必须的，
    #: 那不是词表；真正被禁的是让**代码**认某个指标名或答案关键词——所以判据落在代码面上。
    code_strings = {n.value for n in ast.walk(fsf_tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docstrings}
    code_strings |= {n.name for n in ast.walk(fsf_tree)
                     if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    hardcoded = sorted(s for s in code_strings
                       if "资产负债率" in s or "营业收入" in s or "财务费用" in s)
    check(not hardcoded,
          f"代码面不得内嵌指标名（那是写死答案关键词），实测 {hardcoded}")
    word_tables = (set(NS.HIGH_RISK_SURFACE_MARKERS) | set(NS.SCOPE_QUALIFIER_MARKERS)
                   | set(NS.CONCLUSION_STRENGTH_MARKERS) | set(NS.CAUSAL_ASSERTION_MARKERS))
    smuggled = sorted(code_strings & word_tables)
    check(not smuggled,
          f"定位器不得把词表抄成字面量（那是第二份词表，会与唯一实现漂移），实测 {smuggled}")
    for name in ("SCOPE_QUALIFIER_MARKERS", "CONCLUSION_STRENGTH_MARKERS",
                 "CAUSAL_ASSERTION_MARKERS", "TABLE_RELATION_SURFACE_MARKERS",
                 "FIDELITY_SINGLE_CHAR_MARKERS", "scan_numeric_tokens", "marker_hits",
                 "vague_period_hits", "entity_head_nouns"):
        check(f"NS.{name}" in fsf_src,
              f"六类抽取必须经 `NS.{name}` 共享实现取得（不得就地重写一份）")
    ns_src = (REPO / "sections" / "narrative_schema.py").read_text(encoding="utf-8")
    ns_imports: set[str] = set()
    for node in ast.walk(ast.parse(ns_src)):
        if isinstance(node, ast.Import):
            ns_imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            ns_imports.add(node.module.split(".")[0])
    check("llm" not in ns_imports,
          "wire 层（`narrative_schema`）不得 import llm：判定面与 wire 必须分层，"
          f"实测 import 面 {sorted(ns_imports)}")

    # ======================================================= §12 输入面：逐句预填槽位
    # `nsfr-2` 只改输入面：定位读数按**句**分组，每一行都是待答的槽位、判定字段留空。
    # 「留空」不是「通过」——照抄槽位回填必须当场 fail-closed。
    face = _input_face(E2E_BUNDLE)
    slots = face["located_atoms"]
    e2e_sids = [s.sentence_id for s in E2E_BUNDLE.sentences()]
    check(isinstance(slots, dict) and sorted(slots) == sorted(e2e_sids),
          f"定位面必须是**逐句的槽位表**（键 = 每一句的 sentence_id），实测 "
          f"{type(slots).__name__}/"
          f"{sorted(slots) if isinstance(slots, dict) else slots}")
    check(all([r["atom_surface"] for r in slots[sid]] == list(located_e2e[sid])
              for sid in e2e_sids),
          "每一句的槽位行必须与机械定位读数逐位相同（分组只改形状，不改读数）")
    slot_rows = [row for sid in e2e_sids for row in slots[sid]]
    check(bool(slot_rows), "反例前提：E2E 句子里确实有机械定位到的原子（否则本节恒真）")
    check(all(row["verdict"] is None and row["claim_id"] is None
              and list(row["accepted_binding_ids"]) == [] and row["reason_code"] is None
              and str(row["rationale"]) == ""
              for row in slot_rows),
          "预填槽位的判定字段必须一律留空（`claim_id` / `accepted_binding_ids` / `verdict` / "
          "`reason_code` / `rationale`）：留空是**还没有答案**，不是「通过」")
    check(set(row["verdict"] for row in slot_rows) & set(NS.FINAL_SENTENCE_ATOM_VERDICTS)
          == set(),
          "槽位里不得出现任何一档 verdict（预填「通过」就是把判定面架空了）")
    check(all(row["atom_kind"] in NS.FINAL_SENTENCE_ATOM_KINDS and row["atom_surface"]
              for row in slot_rows),
          "槽位的定位字段（类别提示 + 表面）必须都填好：判定留空，定位不得留空")
    check(all(sorted(row) == ["accepted_binding_ids", "atom_kind", "atom_surface", "claim_id",
                              "rationale", "reason_code", "verdict"] for row in slot_rows),
          "槽位的键就是判定面要回填的那七个字段（模型不必猜形状，也不得自造字段）")
    expect_raises("照抄槽位（留空）回填必须 fail-closed：留空不是「通过」",
                  lambda: FSF.interpret_sentence_fidelity_output(
                      _payload([{"sentence_id": sid, "atoms": slots[sid]}
                                for sid in e2e_sids], "entailed", None),
                      sentence_ids=e2e_sids, located_by_sentence=located_e2e),
                  "原子行不合法")

    # ======================================================= §13 r9 现场：逐项认领与四类负例
    # r9 财务节的真实返回：输入面已定位 `2025年` / `2024年` / `-3.3个百分点`，模型却把两个年份
    # **合成**成一条期间原子（`2025年末较2024年末`），两条预填行无人认领 ⇒ 本门未形成决定。
    # 下面用同一形状的句子（公司无关）钉死两件事：**逐行作答即通过**；合并 / 漏项一律 fail-closed。
    r9_text = "示例科技有限公司资产负债率2025年末较2024年末变动-3.3个百分点。"
    r9_claim, r9_binding = _claim_and_binding(r9_text, 120)
    r9_narrative = _narrative([_sentence_spec(r9_text, "natural", [r9_claim])])
    r9_bundle = _bundle(r9_narrative, [r9_claim], [r9_binding],
                        authority=_Authority([_fact_entry("f-120", r9_text)]))
    r9_sentence = r9_bundle.sentences()[0]
    r9_sid = r9_sentence.sentence_id
    r9_rows = _input_face(r9_bundle)["located_atoms"][r9_sid]
    r9_located = FSF.located_readings(r9_bundle)
    r9_bids = (r9_binding.accepted_support_binding_id,)
    check([row["atom_surface"] for row in r9_rows]
          == ["2025年", "2024年", "-3.3个百分点", "公司"],
          f"r9 现场的定位面：两个年份各自成行（不是「一条期间」），实测 "
          f"{[row['atom_surface'] for row in r9_rows]}")

    r9_atoms, r9_verdict, r9_reason = FSF.interpret_sentence_fidelity_output(
        _payload([{"sentence_id": r9_sid,
                   "atoms": [_claimed(row, r9_claim, r9_bids) for row in r9_rows]}],
                 "entailed", None),
        sentence_ids=(r9_sid,), located_by_sentence=r9_located)
    check(r9_verdict == "entailed" and r9_reason is None,
          f"r9 正例：三项（两个年份 + 一个变动值）**各自单独作答**即通过，实测 "
          f"{r9_verdict}/{r9_reason}")
    check([a.atom_surface for a in r9_atoms if a.atom_kind in ("numeric", "period")]
          == ["2025年", "2024年", "-3.3个百分点"],
          "两个年份必须是**两条**原子：读者读到几个成分就写几行（合并即改了成分数）")

    def _error_text(fn) -> str:
        """反例要读它到底说了什么，因此把异常原文取回来（不吞错、不换类型）。"""
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - 反例：抛错也要如实记账
            return f"{type(exc).__name__}: {exc}"
        return ""

    merged_row = {"atom_kind": "period", "atom_surface": "2025年末较2024年末",
                  "claim_id": r9_claim.claim_id, "accepted_binding_ids": list(r9_bids),
                  "verdict": "entailed", "reason_code": None, "rationale": "夹具"}
    merge_err = _error_text(lambda: FSF.interpret_sentence_fidelity_output(
        _payload([{"sentence_id": r9_sid,
                   "atoms": [merged_row]
                   + [_claimed(row, r9_claim, r9_bids)
                      for row in r9_rows if row["atom_surface"] not in ("2025年", "2024年")]}],
                 "entailed", None),
        sentence_ids=(r9_sid,), located_by_sentence=r9_located))
    check("未被输出认领" in merge_err and "2025年" in merge_err and "2024年" in merge_err,
          f"r9 合并负例：两个年份被并成一条更宽的表述 ⇒ 两条预填行无人认领，必须 "
          f"fail-closed 并点名被吞掉的年份（实测 {merge_err[:200]!r}）")

    drop_err = _error_text(lambda: FSF.interpret_sentence_fidelity_output(
        _payload([{"sentence_id": r9_sid,
                   "atoms": [_claimed(row, r9_claim, r9_bids) for row in r9_rows
                             if row["atom_surface"] != "2024年"]}],
                 "entailed", None),
        sentence_ids=(r9_sid,), located_by_sentence=r9_located))
    check("未被输出认领" in drop_err and "2024年" in drop_err,
          f"r9 漏年份负例：少答一行与合并同等对待（同一条 fail-closed，且点名漏掉的表面），"
          f"实测 {drop_err[:200]!r}")

    metric_text = "示例科技有限公司有息负债率2025年末较2024年末变动-3.3个百分点。"
    metric_claim, metric_binding = _claim_and_binding(r9_text, 121)
    metric_bundle = _bundle(_narrative([_sentence_spec(metric_text, "natural", [metric_claim])]),
                            [metric_claim], [metric_binding],
                            authority=_Authority([_fact_entry("f-121", r9_text)]))
    metric_sentence = metric_bundle.sentences()[0]
    metric_rows = _input_face(metric_bundle)["located_atoms"][metric_sentence.sentence_id]
    metric_bids = (metric_binding.accepted_support_binding_id,)
    metric_atoms, metric_verdict, metric_reason = FSF.interpret_sentence_fidelity_output(
        _payload([{"sentence_id": metric_sentence.sentence_id,
                   "atoms": [_claimed(row, metric_claim, metric_bids) for row in metric_rows]
                   + [{"atom_kind": "subject_or_metric", "atom_surface": "有息负债率",
                       "claim_id": metric_claim.claim_id,
                       "accepted_binding_ids": list(metric_bids),
                       "verdict": "not_entailed",
                       "reason_code": "subject_or_metric_replaced",
                       "rationale": "句中的指标名与声明 Claim 不是同一个"}]}],
                 "rejected", "atom_not_entailed"),
        sentence_ids=(metric_sentence.sentence_id,),
        located_by_sentence=FSF.located_readings(metric_bundle))
    check(metric_verdict == "rejected" and metric_reason == "atom_not_entailed"
          and any(a.reason_code == "subject_or_metric_replaced" for a in metric_atoms),
          "r9 改指标负例：槽位面全部认领之后，换掉的指标名仍由判定面补出并拒绝，"
          f"实测 {metric_verdict}/{metric_reason}")

    proxy_text = ("示例科技有限公司资产负债率2025年末较2024年末变动-3.3个百分点，"
                  "代理口径（PROXY_FINANCE_EXPENSES）。")
    proxy_claim, proxy_binding = _claim_and_binding(proxy_text, 122)
    proxy_bundle = _bundle(_narrative([_sentence_spec(r9_text, "natural", [proxy_claim])]),
                           [proxy_claim], [proxy_binding],
                           authority=_Authority([_fact_entry("f-122", proxy_text)]))
    proxy_sentence = proxy_bundle.sentences()[0]
    proxy_rows = _input_face(proxy_bundle)["located_atoms"][proxy_sentence.sentence_id]
    proxy_bids = (proxy_binding.accepted_support_binding_id,)
    proxy_atoms, proxy_verdict, proxy_reason = FSF.interpret_sentence_fidelity_output(
        _payload([{"sentence_id": proxy_sentence.sentence_id,
                   "atoms": [_claimed(row, proxy_claim, proxy_bids) for row in proxy_rows]
                   + [{"atom_kind": "scope_or_strength",
                       "atom_surface": "代理口径（PROXY_FINANCE_EXPENSES）",
                       "claim_id": proxy_claim.claim_id,
                       "accepted_binding_ids": list(proxy_bids),
                       "verdict": "not_entailed",
                       "reason_code": "proxy_qualifier_dropped",
                       "rationale": "声明 Claim 里的代理口径限定语在最终句里被丢掉"}]}],
                 "rejected", "atom_not_entailed"),
        sentence_ids=(proxy_sentence.sentence_id,),
        located_by_sentence=FSF.located_readings(proxy_bundle))
    check(proxy_verdict == "rejected" and proxy_reason == "atom_not_entailed"
          and any(a.reason_code == "proxy_qualifier_dropped" for a in proxy_atoms),
          "r9 错代理口径负例：限定语是同一个断言的一部分，丢了就是改了断言，"
          f"实测 {proxy_verdict}/{proxy_reason}")
    check("代理口径" not in r9_text and "代理口径" in proxy_text,
          "反例前提：最终句丢掉了声明 Claim 里的代理口径限定语（否则这条负例恒真）")

    note("NOTE §13 r9 现场的读数：槽位 4 行（`2025年` / `2024年` / `-3.3个百分点` / `公司`）逐行"
         "作答 ⇒ entailed；把两个年份合并成一条更宽的表述、或漏答其中一个年份 ⇒ 均以「已定位"
         "表面无人认领」fail-closed；改指标与丢代理口径限定语各在槽位全认领之后仍被拒绝。")

    note("NOTE 边界：表格单元格**不在**本门覆盖面内（`NarrativeTableRow` 没有 `sentence_id`，"
         "它按 `row_id` 内容寻址）。本门覆盖面是 `NS.fact_bearing_sentence_ids`，表格一侧由"
         "`natfid-1` 与「行内非空单元格 ↔ Claim 逐位同数」的构造约束承担；这是**已登记的缺口**，"
         "不得读成「已覆盖」。")
    note("NOTE 边界：定位器**不产** `subject_or_metric`，`约` 也不在它的读数里（加词表就是写死"
         "指标名/答案关键词）。这两类只能由判定面补出；模型若只报机械定位的那几条、而放过自己"
         "读出来的越权成分，本门拦不住——这是所有 LLM 判定门的共同边界。§5 的三条反例是"
         "「同一输入、模型如实报告即被拒」的可执行证据。")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

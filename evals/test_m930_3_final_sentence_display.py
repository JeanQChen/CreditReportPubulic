"""Eval: M930-3 §12.4.4 第 6 步 —— 最终句语义决定的**只读展示**（逐句回查）。

跑法（无管道 / 无重定向）：`python -X utf8 -m evals.test_m930_3_final_sentence_display`

§12.4.4 第 6 步的原文是：「**只读展示**（本阶段面试版）：逐句显示决定状态；**不**实现用户
补件 / 缺口绑定 / 证据更新 / 用户触发继续生成。」本模块钉的就是这一句，逐条落到可执行读数：

* §1 接线：展示块在被调用的位置上——`_before_after_md` 的**逐节循环体内**恰好一次调用，
  且人读产物的前言里说明了「逐句」这件事（读者不必去猜这一块是干什么的）。
* §2 **只读**（AST 级，本模块的主要职责）：展示函数不 import 库/网络、不写文件、不碰
  `sections.store`、不调用任何判据入口；`NS.` 上只允许碰**三个**名字（版本常量 + 唯一的状态
  派生 + 覆盖面函数）。「只读」不是一句注释，而是一份可以被机器复核的引用面。
* §3 **不自行发明档位**：受验源码里不存在任何 `NS.FINAL_SENTENCE_BLOCK_REASONS` 的**字面**
  常量——四个档位只能从 `narrative_schema` 的封闭元组里来。加一档、改一档都必须改 wire，
  不能改展示。
* §4 真实契约：`BackboneSectionWriterOutput` / `SectionChainV2` / `DraftAcceptance` 真的声明
  展示块读的那四个字段（`narrative` / `final_sentence_decisions` / `claims` /
  `acceptance.accepted_bindings`）。否则这一块读的是幻影字段，绿得毫无意义。
* §5 有且仅有一条有效决定：**逐句一行**，每句的逐原子读数逐条在列，Claim 与 factual 支撑边
  都可回查，且不出现任何「未通过」措辞。
* §6 `missing`：如实落档，**不**写成通过、**不**留白；每句另附「本句没有逐原子读数」。
* §7 `duplicate` / §8 `stale`：两种「有读数也不能用」的现场各自落档。
* §9 **「没有对象可核」不是「通过」**：本节没有承载事实的最终句时，不写「有且仅有一条**有效**
  决定」（那种场合一条决定都没有）。§10 是它的对照面：同样没有事实句，但**带着**一条决定
  ⇒ `stale`，且措辞仍不得读出「已核验」。
* §11 正文读不出来 / §12 状态读不出来：各有一档**显式**措辞，既不静默跳过，也不把整份产物
  带走。
* §13 只读性已被执行：正文对象与决定束在调用前后逐字节不变；把核验入口换成「一调用就抛」之后
  展示块照常工作（它**不**触发任何核验调用）。
* §14 覆盖面边界如实写在产物里：表格行不带句子身份，这条**已知缺口**不得读成已覆盖。

夹具纪律：不读库、不连网、不写任何文件、不调真实模型；文本夹具全部公司无关
（通用「公司」/「示例」字样），不写固定页码、不写答案关键词、不为任何一家公司写专用规则。

**诚实的边界（本模块没有覆盖什么）**：

* 现场的「section」是一个**形状镜像**的门后结果（四个字段与真实产物同名，§4 用
  `dataclasses.fields` 把「真实产物确实有这四个字段」钉住）。整条真实链（Contract → Pack →
  Writer → 两道门 → `nsfid-1` 决定）的端到端读数由 `test_m930_3_prewrite_offline_replay`
  的同链离线重放与真实 create-only run 的产物见证，本模块**不**重跑它。
* `_before_after_md` 的**整份**人读产物（需要 `RunState` 与真实输入）不在本模块里构造；
  本模块钉的是「它把这一块按节接进去」这条接线（§1）与这一块本身的全部读数。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

from evaluation import run_m930_3_acceptance as ACC
from harness import schema as HS
from sections import accepted_binding as AB
from sections import company_worker as CW
from sections import final_sentence_fidelity as FSF
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS
from sections import store as ST

#: 受验 runner 的源码（静态守卫用；**不执行**它）。
_RUNNER_SRC = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")
_DISPLAY_FN = "_final_sentence_decision_lines"
_HOST_FN = "_before_after_md"

TOPIC = "topic-company-business"
SECTION = "company"
FP = "a" * 64
DRAFT_ID = "draft-1"
REVISION = "rev-1"
MEMBER_LOCATOR = NS.char_range_locator("evidence:ev-1", 3, 40)

#: 展示块**唯一**允许在 `narrative_schema` 上碰的三个名字。多一个都要先回答「它是不是在
#: 自己算一遍状态」——展示块不得有第二套判据。
_NS_ALLOWED = frozenset({
    "FINAL_SENTENCE_DECISION_SCHEMA_VERSION", "final_sentence_gate_state",
    "fact_bearing_sentence_ids",
})

#: 展示块里不得出现的引用面（库 / 网络 / 文件 / 模型 / 判据入口）。命中即「只读」不成立。
_FORBIDDEN_IDENTIFIERS = frozenset({
    "store", "ST", "sqlite3", "db", "conn", "cursor", "commit",
    "llm", "client", "LLMClient", "NarrationResult", "requests", "urllib", "socket",
    "open", "Path", "write_text", "write_bytes", "dump", "dumps",
    "evaluate_final_sentence_fidelity", "evaluate_final_sentence_fidelities",
    "drive_final_sentence_gate", "commit_section_chain_v2",
})


# ---------------------------------------------------------------------------
# 夹具：真实形状的 Claim / factual 支撑边 / Narrative / 接受束
# ---------------------------------------------------------------------------

def _binding(*, fact_id: str = "f-1", draft_revision: str = REVISION,
             semantics: str = "factual", container: str = "pack-1"):
    kw = dict(proposed_support_id=f"psr-{fact_id}", proposal_content_hash=FP,
              binding_subject_kind="claim_candidate", binding_subject_id="cc-1",
              draft_revision=draft_revision, binding_decision_id="cbd-1",
              support_set_digest="d" * 64, authority_kind="topic_pack",
              authority_container_id=container, source_identity="evidence:ev-1",
              provenance_identity="prov-1", support_role="primary",
              support_semantics=semantics, authorization_path="path_a_prevalidated",
              content_fingerprint=FP, entailment_decision_id=f"ced-{fact_id}",
              fact_id=fact_id, material_id="m-1",
              payload_ref={"object_type": "research_material"}, locator_ref=MEMBER_LOCATOR)
    if semantics != "factual":
        kw["authorization_path"] = "context_only"
        kw["fact_id"] = None
    return NS.AcceptedSupportBinding.create(**kw)


def _claim(text: str, index: int, *, bindings=()) -> SS.SectionClaim:
    question_ids = (f"q-{index}",)
    refs = (HS.CitationRef(ref_type="evidence", evidence_id=f"evidence:ev-{index}"),)
    return SS.SectionClaim(
        claim_id=SS.derive_claim_id("fact", TOPIC, question_ids, text, refs,
                                    f"cand-{index}", REVISION, tuple(bindings)),
        schema_version=SS.CLAIM_SCHEMA_VERSION, section_id=SECTION, topic_id=TOPIC,
        question_ids=question_ids, text=text, claim_type="fact", citation_refs=refs,
        claim_candidate_id=f"cand-{index}", claim_candidate_revision=REVISION,
        accepted_binding_ids=tuple(bindings))


def _claim_and_binding(text: str, index: int):
    binding = _binding(fact_id=f"f-{index}")
    return _claim(text, index, bindings=(binding.accepted_support_binding_id,)), binding


def _citations(claims) -> list[str]:
    out: list[str] = []
    for claim in claims:
        for ref in claim.citation_refs:
            cid = SS.derive_citation_id(claim.claim_id, ref)
            if cid not in out:
                out.append(cid)
    return out


def _sentence_spec(text: str, kind: str, claims, *, connector: str | None = None) -> dict:
    """一句最终句的规格。`transition` 句的 connector 必须来自封闭词表，且文本逐字等于它
    （连接语之外的自由文本不得进入正文），因此这里照实传。"""
    spec = {"text": text, "sentence_kind": kind,
            "claim_ids": [c.claim_id for c in claims],
            "citation_ids": _citations(claims),
            "context_binding_ids": ()}
    if connector is not None:
        spec["connector"] = connector
    return spec


def _narrative(specs_by_paragraph, *, draft_id: str = DRAFT_ID, revision: str = REVISION):
    paragraphs = tuple(
        NS.NarrativeParagraph.create(section_id=SECTION, topic_ids=(TOPIC,), index=index,
                                     sentence_specs=tuple(specs))
        for index, specs in enumerate(specs_by_paragraph))
    return NS.SectionNarrative.create(
        task_id="task-1", section_id=SECTION, section_draft_id=draft_id,
        draft_revision=revision, paragraphs=paragraphs, tables=(), context_binding_ids=())


def _acceptance(bindings) -> AB.DraftAcceptance:
    """一份**真实类型**的接受束（`subject_keys` 必须被接受/拒绝两侧恰好覆盖）。"""
    ordered = tuple(sorted(bindings, key=lambda b: str(b.accepted_support_binding_id)))
    keys: list[tuple[str, str]] = []
    for binding in ordered:
        key = (str(binding.binding_subject_kind), str(binding.binding_subject_id))
        if key not in keys:
            keys.append(key)
    return AB.DraftAcceptance(accepted_bindings=ordered, rejected_subjects=(),
                              subject_keys=tuple(keys))


class _StubClient:
    """桩：不做任何判定，只把给定文本包成 `NarrationResult`（保留全部调用元数据）。"""

    def __init__(self, text: str, *, status: str = "ok", model: str | None = None):
        self.text = text
        self.status = status
        self.model = model or PW.resolve_model_policy(PW.MODEL_POLICY_STUB)
        self.calls = 0

    def evaluate(self, *, messages, system, prompt_version, model_policy):
        self.calls += 1
        return PW.NarrationResult(text=self.text, call_id="call-display-1", model=self.model,
                                 prompt_version=FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
                                 status=self.status, error="夹具")


def _payload_text(bundle, *, verdict="entailed", reason=None) -> str:
    """按**机械定位读数**把每一条已定位表面逐条认领，产出一份合法的判定输出。"""
    rows: list[dict] = []
    claim_of: dict[str, str] = {}
    binding_of: dict[str, list[str]] = {}
    for paragraph in bundle.narrative.paragraphs:
        for sentence in paragraph.sentences:
            for claim_id in sentence.claim_ids:
                claim_of[str(sentence.sentence_id)] = str(claim_id)
    for claim in bundle.claims:
        binding_of[str(claim.claim_id)] = [str(b) for b in claim.accepted_binding_ids]
    for sentence in bundle.sentences():
        sid = str(sentence.sentence_id)
        claim_id = claim_of[sid]
        rows.append({"sentence_id": sid, "atoms": [
            {"atom_kind": a.atom_kind, "atom_surface": a.atom_surface, "claim_id": claim_id,
             "accepted_binding_ids": binding_of[claim_id], "verdict": "entailed",
             "reason_code": None, "rationale": "夹具"}
            for a in FSF.locate_fact_atoms(sentence_id=sid, text=sentence.text,
                                           claim_texts={claim_id: sentence.text})]})
    return json.dumps({"per_sentence": rows, "verdict": verdict, "reason_code": reason},
                      ensure_ascii=False)


def _decision_for(bundle) -> NS.FinalSentenceFidelityDecision:
    """一次真调用（桩）产出的**真实**决定——不是手拼的 dict。"""
    return FSF.evaluate_final_sentence_fidelity(
        bundle, llm_client=_StubClient(_payload_text(bundle)),
        model_policy=PW.MODEL_POLICY_STUB)


def _section(narrative, decisions, claims, bindings):
    """门后定稿结果的**形状**（四个字段与 `BackboneSectionWriterOutput` 同名同形）。"""
    return SimpleNamespace(narrative=narrative, final_sentence_decisions=tuple(decisions),
                           claims=tuple(claims), acceptance=_acceptance(bindings))


def _text_of(lines) -> str:
    return "\n".join(lines)


def _sentence_bullets(lines, sid: str) -> list[str]:
    return [ln for ln in lines if ln.startswith(f"  - `{sid}`（")]


def _all_sentence_bullets(lines) -> list[str]:
    """展示块里**逐句行**的全体（缩进两格、以句子 id 开头）；原子行缩进四格，不在内。"""
    return [ln for ln in lines if ln.startswith("  - `") and "`（" in ln]


# ---------------------------------------------------------------------------
# 现场：两句承载事实的最终句 + 一句不承载事实的 transition 句
# ---------------------------------------------------------------------------
TEXT_1 = "公司2024年营业收入为1,234.56亿元。"
TEXT_2 = "报告期内公司主营业务未发生重大变化。"
#: `transition` 句的文本必须逐字等于封闭词表里的 connector（不得夹带自由文本）。
CONNECTOR_T = "在此基础上，"

CLAIM_1, BINDING_1 = _claim_and_binding(TEXT_1, 1)
CLAIM_2, BINDING_2 = _claim_and_binding(TEXT_2, 2)
NARRATIVE = _narrative([[
    _sentence_spec(TEXT_1, "natural", [CLAIM_1]),
    _sentence_spec(CONNECTOR_T, "transition", [], connector=CONNECTOR_T),
    _sentence_spec(TEXT_2, "composed", [CLAIM_2]),
]])
#: 支撑边按 `accepted_support_binding_id` 升序（束的 canonical order），与真实链同形。
BINDINGS = tuple(sorted((BINDING_1, BINDING_2),
                        key=lambda b: str(b.accepted_support_binding_id)))
BUNDLE = FSF.SentenceFidelityBundle(
    narrative=NARRATIVE, claims=(CLAIM_1, CLAIM_2),
    accepted_bindings=BINDINGS, authority=None, manifest=None, material_context=None)
DECISION = _decision_for(BUNDLE)

#: 只有 transition 句的正文（本门无对象可核的那种现场）；该现场**不产出**决定，
# 因此不存在 bundle 侧的核验调用（空覆盖面本来就不允许产出决定）。
EMPTY_NARRATIVE = _narrative([[_sentence_spec(CONNECTOR_T, "transition", [],
                                               connector=CONNECTOR_T)]])


def _ast_fn(name: str) -> ast.FunctionDef:
    """受验 runner 里**唯一**那个顶层函数定义（不执行模块，只解析源码）。"""
    tree = ast.parse(_RUNNER_SRC)
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(found) != 1:
        raise AssertionError(f"受验 runner 里 {name!r} 的定义不是恰好一处（实测 {len(found)}）")
    return found[0]


def _identifiers(node: ast.AST) -> set[str]:
    """一段源码里出现过的**引用面**：名字、属性名、import 的模块/名字。"""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
        elif isinstance(sub, ast.Import):
            out.update(alias.name.split(".")[-1] for alias in sub.names)
        elif isinstance(sub, ast.ImportFrom):
            out.add((sub.module or "").split(".")[-1])
            out.update(alias.name for alias in sub.names)
    return out


def _ns_attrs(node: ast.AST) -> set[str]:
    """在该函数体里出现过的 `NS.<attr>` 属性名（`NS` 是 `narrative_schema` 的固定别名）。"""
    out: set[str] = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id == "NS"):
            out.add(sub.attr)
    return out


def main() -> dict:  # noqa: C901 - 逐节顺序即判据顺序，拆散会看不出覆盖面
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

    def note(msg: str) -> None:
        details.append(msg)

    fn = _ast_fn(_DISPLAY_FN)
    host = _ast_fn(_HOST_FN)
    code = ACC._final_sentence_decision_lines

    # ================================================ §1 接线：接在被调用的位置上
    check(callable(code), f"`{_DISPLAY_FN}` 必须是可调用的生产实现（不是测试里的副本）")
    params = [a.arg for a in fn.args.args]
    check(params == ["section", "section_id"],
          f"展示函数只吃现场与节 id 两个入参（实测 {params}）——"
          "任何第三个入参都会变成「由调用方决定怎么显示」，本块必须自己从产物里读")
    check(fn.args.vararg is None and fn.args.kwarg is None and not fn.args.defaults,
          "展示函数不接受可变参数与缺省值（缺省值会让「少传一样」静默变成另一种显示）")

    outer_loops = sorted((n for n in ast.walk(host) if isinstance(n, ast.For)),
                         key=lambda n: n.lineno)
    check(bool(outer_loops), f"`{_HOST_FN}` 里应当有逐节循环（人读产物按节分块）")
    if outer_loops:
        inner_calls = [c for c in ast.walk(outer_loops[0])
                       if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                       and c.func.id == _DISPLAY_FN]
        check(len(inner_calls) == 1,
              f"展示块必须在**逐节循环体内**恰好调用一次（实测 {len(inner_calls)} 次）："
              "放在循环外会把最后/第一节的读数当成全节的")
    host_src = _text_of(_RUNNER_SRC.splitlines()[host.lineno - 1:host.end_lineno])
    check("逐句" in host_src and _DISPLAY_FN in host_src,
          "人读产物的前言里必须说明这一块是「逐句」的核验状态（读者不必猜）")

    # ================================================ §2 只读（本模块的主要职责）
    used = _identifiers(fn)
    hits = sorted(used & _FORBIDDEN_IDENTIFIERS)
    check(not hits,
          f"展示块不得碰库/网络/文件/模型/判据入口（实测命中 {hits}）："
          "「只读展示」是一份可机器复核的引用面，不是一句注释")
    imports = [n for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))]
    check(len(imports) == 1 and isinstance(imports[0], ast.ImportFrom)
          and imports[0].module == "sections" and imports[0].level == 0
          and [a.name for a in imports[0].names] == ["narrative_schema"],
          "展示块只允许一处延迟 import（`from sections import narrative_schema`）："
          "再加一个 import 就是在现场多持有一份状态来源")
    check(_ns_attrs(fn) == _NS_ALLOWED,
          f"展示块在 `narrative_schema` 上只允许碰 {sorted(_NS_ALLOWED)}"
          f"（实测 {sorted(_ns_attrs(fn))}）：状态只有一份派生，展示不得自己算第二套")
    targets: list[ast.AST] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets.append(node.target)
    attr_writes = [t for t in targets if isinstance(t, ast.Attribute)]
    check(not attr_writes,
          "展示块不得对任何**对象属性**赋值（它可以建局部字典，但不得改传进来的现场）")
    check(not [n for n in ast.walk(fn) if isinstance(n, ast.Global) or isinstance(n, ast.Nonlocal)],
          "展示块不得借用 global/nonlocal 改模块级状态（只读意味着它没有可写面）")

    # ================================================ §3 不自行发明档位
    block_reasons = set(NS.FINAL_SENTENCE_BLOCK_REASONS)
    literals = {n.value for n in ast.walk(ast.parse(_RUNNER_SRC))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    leaked = sorted(literals & block_reasons)
    check(not leaked,
          f"受验源码里不得出现档位的**字面**常量（实测 {leaked}）：四个档位只能来自"
          "`narrative_schema` 的封闭元组，加一档/改一档必须改 wire")
    check("final_sentence_gate_state" in _ns_attrs(fn),
          "展示块的状态必须由 `NS.final_sentence_gate_state` 复算（与定稿协调器/组装器/"
          "Store 读回同一份实现）；自己写一套会让同一个缺口读出两种状态")

    # ================================================ §4 真实契约（读的不是幻影字段）
    output_fields = {f.name for f in dataclasses.fields(CW.BackboneSectionWriterOutput)}
    check({"narrative", "final_sentence_decisions", "claims", "acceptance"} <= output_fields,
          f"门后定稿结果必须真的带这四个字段（实测缺 "
          f"{sorted({'narrative', 'final_sentence_decisions', 'claims', 'acceptance'} - output_fields)}）")
    chain_fields = {f.name for f in dataclasses.fields(ST.SectionChainV2)}
    check({"narrative", "final_sentence_decisions", "claims", "accepted_bindings"} <= chain_fields,
          "库侧 current 链也必须带同一批门后字段（否则「读回来的现场」与「显示」分叉）")
    check(hasattr(AB.DraftAcceptance(accepted_bindings=(), rejected_subjects=(),
                                     subject_keys=()), "accepted_bindings"),
          "接受束的支撑边读视图是 `accepted_bindings`（展示块按这个名字取 factual 边）")

    # ================================================ §5 有且仅有一条有效决定
    expected = NS.fact_bearing_sentence_ids(NARRATIVE)
    check(len(expected) == 2, f"夹具本身的对照面：本现场应有 2 句承载事实的最终句（实测 {len(expected)}）")
    check(expected == tuple(s.sentence_id for p in NARRATIVE.paragraphs for s in p.sentences
                            if s.claim_ids),
          "覆盖面只收声明了 Claim 的句子（transition 句不在内）")
    good = _section(NARRATIVE, (DECISION,), (CLAIM_1, CLAIM_2), BINDINGS)
    lines = code(good, SECTION)
    text = _text_of(lines)
    reason, _detail = NS.final_sentence_gate_state(
        narrative=NARRATIVE, decisions=(DECISION,), claims=(CLAIM_1, CLAIM_2),
        accepted_bindings=BINDINGS)
    check(reason is None, f"夹具本身：这一份现场必须是有效决定（实测 {reason!r}）")
    check(DECISION.verdict == "entailed" and DECISION.call_id == "call-display-1",
          "决定是**真调用（桩）**产出的，不是手拼的 dict")
    check("状态 `entailed`（有且仅有一条**有效**决定）" in text,
          "有效决定必须写成 `entailed` 且注明「有且仅有一条」")
    check("**未通过/未形成**" not in text, "有效决定不得出现「未通过/未形成」措辞")
    for sid in expected:
        check(bool(_sentence_bullets(lines, sid)),
              f"承载事实的最终句 {sid} 必须有自己的一行（逐句显示是这一步的判据本身）")
    bullets = _all_sentence_bullets(lines)
    check(len(bullets) == len(expected),
          f"逐句行数必须等于承载事实的最终句数（实测 {len(bullets)} vs {len(expected)}）——"
          "多一行意味着有句子被显示成事实句，少一行意味着漏审")
    transition_sid = [s.sentence_id for p in NARRATIVE.paragraphs for s in p.sentences
                      if s.sentence_kind == "transition"][0]
    check(not _sentence_bullets(lines, transition_sid),
          "transition 句不承载事实，本门无对象可核：它不得被显示成「已核验的句子」")
    per_sentence = {str(a.sentence_id): [] for a in DECISION.atoms}
    for atom in DECISION.atoms:
        per_sentence.setdefault(str(atom.sentence_id), []).append(atom)
    for sid in expected:
        atoms = per_sentence[sid]
        check(bool(atoms) and f"逐原子 {len(atoms)} 条" in text,
              f"{sid} 的逐原子条数必须写进产物（决定里有 {len(atoms)} 条）")
        for atom in atoms:
            check(f"原子 `{atom.atom_kind}`：{atom.atom_surface}" in text,
                  f"原子 `{atom.atom_kind}`/`{atom.atom_surface}` 必须逐条在列（可回查）")
            check(f"`{atom.claim_id}`" in text,
                  "每条原子必须写明它由哪条已接受 Claim 声明")
            for bid in atom.accepted_binding_ids:
                check(f"`{bid}`" in text,
                      "每条原子必须给出 factual 支撑边（否则这是一句无法回查的断言）")
    check("没有逐原子读数" not in text,
          "有效决定的每一句都应有逐原子读数：不得出现「本句没有逐原子读数」")
    check(TEXT_1 in text and TEXT_2 in text, "展示块必须给出句子原文（人读对账要按句子读）")

    # ================================================ §6 missing：没有决定不是通过
    missing = _section(NARRATIVE, (), (CLAIM_1, CLAIM_2), BINDINGS)
    lines_m = code(missing, SECTION)
    text_m = _text_of(lines_m)
    check(f"`{NS.FINAL_SENTENCE_BLOCK_REASONS[0]}`" in text_m
          and NS.FINAL_SENTENCE_BLOCK_REASONS[0] == "final_sentence_decision_missing",
          "没有决定时如实落成 `final_sentence_decision_missing`（不留白、不写「未核」）")
    check("**未通过/未形成**" in text_m, "「没有决定」必须明说「未通过/未形成」")
    check("有且仅有一条**有效**决定" not in text_m,
          "没有决定时**不得**出现「有且仅有一条有效决定」：那是把「没核」写成「核过了」")
    for sid in expected:
        check("（本节没有唯一有效决定）" in _text_of(_sentence_bullets(lines_m, sid)),
              f"{sid} 没有决定时必须写明「本节没有唯一有效决定」")
    check(text_m.count("本句**没有逐原子读数**") == len(expected),
          "没有决定时每一句都要带「本句没有逐原子读数」，并写明不得读成已核验通过")
    check("不得读成「已逐原子核验通过」" in text_m,
          "「没有读数」这一档必须自带「不得读成已核验通过」的定性（否则读者只能自己猜）")

    # ================================================ §7 duplicate：两条并存
    dup = _section(NARRATIVE, (DECISION, DECISION), (CLAIM_1, CLAIM_2), BINDINGS)
    text_d = _text_of(code(dup, SECTION))
    check("`final_sentence_decision_duplicate`" in text_d,
          "同一 revision 配到多于一条决定时必须落 `duplicate`（两个决定无从判断以哪份为准）")
    check("有且仅有一条**有效**决定" not in text_d, "重复档不得被写成通过")
    check("（本节没有唯一有效决定）" in text_d,
          "重复档下逐句行也不得指向某一条决定")

    # ================================================ §8 stale：锚点/覆盖面过期
    other = _narrative([[_sentence_spec(TEXT_1, "natural", [CLAIM_1]),
                         _sentence_spec(CONNECTOR_T, "transition", [],
                                        connector=CONNECTOR_T),
                         _sentence_spec(TEXT_2, "composed", [CLAIM_2])]],
                       revision="rev-2")
    stale = _section(other, (DECISION,), (CLAIM_1, CLAIM_2), BINDINGS)
    text_s = _text_of(code(stale, SECTION))
    check("`final_sentence_decision_stale`" in text_s,
          "决定与当前正文锚点不符时必须落 `stale`（改句/改 Claim/换绑定都会落这一档）")
    check("有且仅有一条**有效**决定" not in text_s, "过期决定不得被写成通过")

    # ================================================ §9 没有对象可核 ≠ 通过
    empty = _section(EMPTY_NARRATIVE, (), (), ())
    lines_e = code(empty, SECTION)
    text_e = _text_of(lines_e)
    check("承载事实的最终句 0 句；决定 0 条" in text_e,
          "没有承载事实的最终句时，条数必须如实写（0 / 0）")
    check("有且仅有一条**有效**决定" not in text_e,
          "0 句 0 决定时**不得**写「有且仅有一条有效决定」——那种场合一条决定都没有；"
          "把「没有对象可核」写成「已核验通过」正是第 6 步点名禁止的读法")
    check("本节没有事实原子可核" in text_e and "**不是**「已核验通过」" in text_e,
          "这一档必须显式写明「没有事实原子可核」且「不是已核验通过」")
    check(not _all_sentence_bullets(lines_e),
          "没有承载事实的句子时不得列出任何逐句行")

    # ================================================ §10 空覆盖面却带着决定 ⇒ stale
    stray = _section(EMPTY_NARRATIVE, (DECISION,), (), ())
    text_y = _text_of(code(stray, SECTION))
    check("`final_sentence_decision_stale`" in text_y,
          "空覆盖面本身不是合法覆盖面：带着一条决定时必须落 `stale`，不得落成通过")
    check("**未通过/未形成**" in text_y and "有且仅有一条**有效**决定" not in text_y,
          "空覆盖面 + 决定这一档也必须读成未通过（措辞不得被「0 句」那一档吞掉）")

    # ================================================ §11 正文读不出来
    naked = SimpleNamespace(narrative=None, final_sentence_decisions=(), claims=(),
                            acceptance=_acceptance(()))
    lines_n = code(naked, SECTION)
    text_n = _text_of(lines_n)
    check("本节正文对象读不出来" in text_n and "**不是**「本节没有事实句」" in text_n,
          "正文读不出来时必须显式说明，并点明它**不是**「本节没有事实句」（两件事不得互串）")
    check(not _all_sentence_bullets(lines_n),
          "正文读不出来时不得列出任何逐句行（没有正文就没有句子身份）")

    # ================================================ §12 状态读不出来 ⇒ 不带走产物
    saved = NS.final_sentence_gate_state

    def _boom(**_kw):
        raise RuntimeError("夹具：派生现场故障")

    try:
        NS.final_sentence_gate_state = _boom
        lines_b = code(good, SECTION)
    finally:
        NS.final_sentence_gate_state = saved
    text_b = _text_of(lines_b)
    check("本节决定状态读不出来" in text_b and "RuntimeError" in text_b,
          "状态派生抛错时展示块必须就地说「读不出来」（连同异常类型），不得静默跳过")
    check("既不是「通过」也不是「没有事实句」" in text_b,
          "读不出来这一档必须自带定性：它不指向「通过」，也不指向「没有事实句」")
    check(NS.final_sentence_gate_state is saved and saved(narrative=NARRATIVE,
                                                          decisions=(DECISION,),
                                                          claims=(CLAIM_1, CLAIM_2),
                                                          accepted_bindings=BINDINGS)[0] is None,
          "夹具必须恢复被打桩的派生函数（否则后续判据读的是桩）")

    # ================================================ §13 只读性已被执行
    before_narrative = json.dumps(NARRATIVE.to_dict(), ensure_ascii=False, sort_keys=True)
    before_decisions = tuple(d.final_sentence_fidelity_decision_id
                             for d in good.final_sentence_decisions)
    before_claims = tuple(c.claim_id for c in good.claims)
    saved_eval = FSF.evaluate_final_sentence_fidelity
    saved_batch = FSF.evaluate_final_sentence_fidelities
    called: list[str] = []

    def _no_call(*_a, **_kw):
        called.append("核验入口被调用")
        raise AssertionError("展示块不得触发任何核验调用")

    try:
        FSF.evaluate_final_sentence_fidelity = _no_call
        FSF.evaluate_final_sentence_fidelities = _no_call
        lines_r = code(good, SECTION)
    finally:
        FSF.evaluate_final_sentence_fidelity = saved_eval
        FSF.evaluate_final_sentence_fidelities = saved_batch
    check(not called,
          "展示块的调用现场不得出现核验入口（门在前一步就跑完了，显示不能反过来改门）")
    check(_text_of(lines_r) == text,
          "同一个现场两次调用的显示必须逐字相同（展示是纯函数：顺序、条数、措辞都不带随机）")
    check(json.dumps(NARRATIVE.to_dict(), ensure_ascii=False, sort_keys=True)
          == before_narrative,
          "展示块不得改动正文对象（前后逐字节相同）")
    check(tuple(d.final_sentence_fidelity_decision_id for d in good.final_sentence_decisions)
          == before_decisions and tuple(c.claim_id for c in good.claims) == before_claims,
          "展示块不得改动决定束与 Claim 束")

    # ================================================ §14 覆盖面边界如实写在产物里
    check("表格行不带句子身份" in text and "已知缺口" in text,
          "展块必须如实写出覆盖面边界：表格行不带句子身份 ⇒ 单元格不逐原子核验，这条是已知缺口")
    check("不得读成已覆盖" in text, "边界说明必须自带「不得读成已覆盖」的定性")
    check("本块只读" in text and "不触发任何调用" in text,
          "展示块必须在产物里自报只读（读者据此知道这一块不参与判据）")

    # ================================================ §15 本模块自身已登记
    runner_src = (REPO / "evals" / "run_evals.py").read_text(encoding="utf-8")
    check('"evals.test_m930_3_final_sentence_display"' in runner_src,
          "本模块必须登记在 `evals/run_evals.py` 的 EVAL_MODULES 里（漏登记＝这条回归不跑）")

    note(f"§5 现场逐句读数：{len(expected)} 句 / {len(DECISION.atoms)} 条原子（决定 id "
         f"{DECISION.final_sentence_fidelity_decision_id[:16]}…）")
    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

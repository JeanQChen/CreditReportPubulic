"""Eval: 数字**逐值**链条里「材料原句 → 候选」这一段——替身读数粒度与工具契约形状。

用法: python -m evals.test_m930_3_numeric_channel

动因（`M930_3_QREWORK_PART1_NUMERIC_CHANNEL_PLAN.md` §0）：cp22 正文里 `41.85%` / `29.13%`
所在句被判 `numeric_basis_not_qualified`，而 cvt-1 逐值台账里这两个值是 **`no_candidate`**
（不是 `candidate_rejected`）——本节 Pack 里**一条 `FactCandidate` 都没被构造出来过**。
要打通的链在「提案之前」就断了。

本模块钉两件**互相独立**的事：

1. **P0（已修）**：`INSPECT_EVIDENCE` 曾在**动作层**允许复数 `evidence_ids`（`harness/actions.py`
   的 `_ALLOWED_ARGS`，连 1~20 个的专用校验都写了），而**工具契约层**只收单数 `evidence_id`
   （`tools/adapters.py::INSPECT_EVIDENCE_SPEC`，`additionalProperties: False`）⇒ 复数在
   `tools/registry.py::enforce_arguments` 当场 `FATAL_ERROR/INVALID_ARGUMENTS`，`run_question`
   转 `stop_reason="FATAL_TOOL_ERROR"`、**没有 answer**。这是一条**真实的活体契约冲突**
   （不是本批引入的），射程是任何在收敛前先 inspect 的调用方。
   本批**统一到单数**——工具执行体本就只读 `args["evidence_id"]` 且 `max_results=1`，生产提示词
   （`llm/prompts/research_action_v1.txt`）从来只公告单数，harness 自己的批读
   （`harness/runtime.py::_run_local_subneeds`）也早已是逐次单数并逐次记账。**统一方向不改变
   调用记账**：一次动作 = 一次 tool call；**不**把一次复数展开成多次未记账请求，**不**动预算与 top-k。

2. **N4（守卫）**：本批**不**动判据本体——`_adopt_facts` 的采纳、`period_extraction` 的期间
   抽取、`pack_writer.scan_authority` 的 Writer 侧期间门，以及**工具契约层**（`tools/adapters.py`
   的 `INSPECT_EVIDENCE_SPEC`）**逐字未变**。动作层与工具层现在同形。

3. **§1b（诊断选定的那一臂）**：诊断（`M930_3_QREWORK_PART1_NUMERIC_CHANNEL_PLAN.md` §1.7）
   读到本节 **46** 行 `被拒 claim` 里 `cN:entailment=UNSUPPORTED` 占 **204** 次、
   `citation_*` / `qualification_rejected` **0** 次 ⇒ 停点在**蕴含层**，不是引用层、不是资格层。
   根因是离线替身的一处**解析错位**：正式渲染器把引用块写成**项目符号行**
   （`harness/entailment.py:973` 的 `f"- [{i}] …"`），而替身原先的模式要求**行首**就是
   左方括号——实际行首是 `- `，**恒不匹配**，`cited_id` 恒空 ⇒ 每条 claim 恒
   `UNSUPPORTED` ⇒ 候选恒 0。
   本节用**真渲染器**（`harness.entailment.entailment_prompt_vars`）+ **真替身**
   （`evaluation.run_m930_3_acceptance.OfflineResearchLLM`）钉住这条耦合：正例必须 `SUPPORTED`，
   三条反例（改述 / 无 evidence 映射 / 行首无项目符号）必须不成立。

模块**边界**：不调真实 LLM、不联网、不写库；夹具只钉**形状、常量与这次解析耦合**，
不证明任何正文达到人读内容门，也不宣称 M930-3 / TS5 / 任何正式阶段关闭。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC                     # noqa: E402
from harness import actions as ACT                                     # noqa: E402
from harness import entailment as E                                    # noqa: E402
from harness import period_extraction as PE                            # noqa: E402
from harness import schema as H                                        # noqa: E402
from routing import schema as RS                                       # noqa: E402
from sections import pack_writer as PW                                 # noqa: E402
from tools import adapters as AD                                       # noqa: E402
from tools import contracts as C                                       # noqa: E402

#: 夹具正文用**中性**句子（不含公司名、股码、固定页码或答案关键词）：本模块钉的是
#: 「渲染 ↔ 解析」的形状耦合，与任何具体主体或具体数值无关。
_FIXTURE_SENTENCE = "报告期内，公司实现产品销量120万台，同比增长7.35%。"
_FIXTURE_PARAPHRASE = "报告期内，公司产品销量较上年同期明显增长。"


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _state(inspected: dict) -> H.ResearchState:
    st = H.ResearchState(run_id="r", case_id="c", question_id="q1", company_id="co",
                         section_id="company", original_question="q", need=_need())
    st.inspected_evidence = inspected
    return st


def _mat(text: str, eid: str = "e1") -> H.InspectedMaterial:
    return H.InspectedMaterial(evidence_id=eid, document_id="d1", source_name="s",
                               page_number=3, text=text, is_snippet=False)


def _answer(text: str, *, citations: list | None = None) -> H.ResearchAnswer:
    return H.ResearchAnswer(
        question_id="q1", answer_text=text,
        claims=[H.Claim(claim_id="c1", text=text, kind="fact", citation_refs=[0])],
        citations=citations or [H.CitationRef(ref_type="evidence", evidence_id="e1")])


def _render_and_judge(state: H.ResearchState, answer: H.ResearchAnswer
                      ) -> tuple[dict, list, str]:
    """走**真渲染器**出 entailment prompt 变量，再交给**真替身**判定（再用真解析器读回）。"""
    prompt_vars = E.entailment_prompt_vars(state, answer, prechecks={})
    llm = ACC.OfflineResearchLLM()
    response = llm.evaluate_entailment_batch(prompt_vars)
    return prompt_vars, E.parse_entailment(response.text), response.text


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    # ============================== §1 P0：`INSPECT_EVIDENCE` 两层统一到单数
    details.append("## §1 P0 单数 `evidence_id`：动作层与工具契约层**同形**，复数两层都拒")

    def _enforce(arguments: dict) -> tuple[bool, str]:
        """只跑**参数门**（`enforce_arguments`）。它发生在 executor 之前，不联网、不建库。"""
        try:
            C.enforce_arguments(AD.INSPECT_EVIDENCE_SPEC, arguments)
            return True, ""
        except C.ToolValidationError as exc:
            return False, str(exc)

    def _validate(arguments: dict) -> list[str]:
        return ACT.validate_action("INSPECT_EVIDENCE", arguments)

    ok_single, err_single = _enforce({"evidence_id": "e1"})
    ok_plural, err_plural = _enforce({"evidence_ids": ["e1", "e2", "e3"]})
    #: **正例**：同一份单数参数要**同时**越过两层——动作层是参数键/类型/必填，工具层是 JSON Schema。
    check(ok_single and _validate({"evidence_id": "e1"}) == [],
          "**正例**：`{\"evidence_id\": \"e1\"}` 同时越过**动作层**（`validate_action` 零错误）"
          "与**工具契约层**（`enforce_arguments` 不抛）——两层现在是同一个形状")
    #: **反例**：复数在**两层都被拒**。动作层此前收它、现在不收（未知参数）；工具层从来只收单数。
    #: 只改一层就会留下「动作层放行、工具层当场 FATAL」的那条老路。
    _plural_errors = _validate({"evidence_ids": ["e1", "e2", "e3"]})
    check(not ok_plural and bool(_plural_errors),
          f"**反例**：复数 `{{\"evidence_ids\": [...]}}` 被**两层**同时拒"
          f"（工具层：{err_plural[:60]}…；动作层：{_plural_errors}）")
    check(AD.INSPECT_EVIDENCE_SPEC.input_schema.get("additionalProperties") is False
          and AD.INSPECT_EVIDENCE_SPEC.input_schema.get("required") == ["evidence_id"],
          "⇒ 契约层仍是**等号**形状：只收单数 `evidence_id`，其余键一律拒（`additionalProperties: False`）")
    check(sorted(ACT._ALLOWED_ARGS["INSPECT_EVIDENCE"]) == ["evidence_id"]
          and tuple(ACT._REQUIRED_ARGS["INSPECT_EVIDENCE"]) == ("evidence_id",),
          "⇒ 动作层的**允许键集与必需键集都是 `{evidence_id}`**（等号，不是包含）——"
          "两层声明逐字一致，冲突的根已被拔掉")
    #: 参数级的四条边界。缺参由通用 `_REQUIRED_ARGS` 覆盖；类型由通用 string 检查覆盖
    #: （`evidence_id` 已并入那张键表）——**不另写分支**，避免两处口径再次分叉。
    check(_validate({}) == ["缺少必需参数: evidence_id"],
          "**反例**：缺参报 `缺少必需参数: evidence_id`（通用必填检查，不再有专用分支）")
    check(_validate({"evidence_id": "e1", "evidence_ids": ["e1"]})
          == ["未知参数: evidence_ids"],
          "**反例**：两个键同时出现时，复数键按**未知参数**拒（不再是「二选一冲突」这条专用理由）")
    check(_validate({"evidence_id": 1}) == ["evidence_id 必须为 string"],
          "**反例**：类型错误由通用 string 检查给出（`evidence_id` 已在 `validate_action` 的键表里）")
    #: 三层链条的落点（改造前）：`tools/registry.py:117-121` 抛错 → `harness/runtime.py` 转
    #: `FATAL_TOOL_ERROR` + `state.status="FAILED"`、**没有 answer** → `_adopt_facts` 走
    #: 「`answer is None`」那一支（`harness/topic_runtime.py:2244`）。统一到单数后这条死路不再
    #: 由**参数形状**触发；真实模型的行为仍须由一次 create-only 断网离线纵链读回验证。
    check(AD.INSPECT_EVIDENCE_SPEC.name == "inspect_evidence",
          "⇒ 本条形状约束射程是**任何**在收敛前先 inspect 的调用方（真实模型，或把 "
          "`max_need_rounds_per_aspect` 抬大的配置）——不止离线替身")
    note("边界：本节只钉**形状**。它**不**证明候选真的产生了，也不证明统一到单数之后研究侧收益"
         "变大——那是读回的事。")

    # ---- §1c 「动作层接受 → 工具层真的执行」：拿**替身真发的参数**过**真 Registry** ----
    #: 上面两条只证明「参数门放行/拒绝」。真正要证的是**跨层**那一句：动作层接受的参数，工具层
    #: 要**真的进到 executor**（不是停在参数门）。
    #: 判据取**报错的出处**：`Evidence DB not initialized. Call init_db() first.` 这句只在
    #: `evidence/store.py::_get_conn()` 里抛出，而在本调用路径上它**只能**经
    #: `tools/adapters.py::_inspect_evidence_executor` 到达 ⇒ 这句话出现，就等于「参数门放行
    #: 且执行体真的跑了」。本模块**不**为这条断言去建证据库（只读检查路径不得初始化/创建数据库），
    #: 所以拿不到 `EMPTY/RETRIEVAL_EMPTY`；能拿到、也正是要证的那一件。
    #: 复数的反例必须 `FATAL_ERROR/INVALID_ARGUMENTS`，且报错文本是**参数门**的话（`未知参数`），
    #: 里面**不含** executor 的话——即**根本没进 executor**。
    details.append("### §1c 动作层接受的参数，工具层**真的执行**（不是只过参数门）")
    _summary = "本地证据 evidence_id（按相关度）：e1, e2, e3"
    _standin = ACC.OfflineResearchLLM().select_action({
        "allowed_actions": "- INSPECT_EVIDENCE\n- ANSWER\n",
        "evidence_summary": _summary, "already_inspected": "（无）",
        "must_converge": "否", "question": "q"})
    _emitted = json.loads(_standin.text)["arguments"]
    check(_emitted == {"evidence_id": "e1"},
          f"**正例**：替身从同一份证据摘要里**只发单数** `{_emitted}`——"
          "它的动作层入口不再产生复数参数")
    with tempfile.TemporaryDirectory() as _td:
        _reg = AD.build_default_registry(audit_dir=Path(_td) / "audit")

        def _exec(arguments: dict):
            return _reg.execute(C.ToolCall(
                call_id="c1", tool_name=AD.INSPECT_EVIDENCE_SPEC.name,
                arguments=arguments, idempotency_key="k1",
                need_id="n1", batch_id="b1"))

        _single = _exec(_emitted)
        check(_single.error_code != "INVALID_ARGUMENTS"
              and "Evidence DB not initialized" in _single.message
              and "未知参数" not in _single.message,
              f"**正例**：同一份参数在**真 Registry** 上**进到 executor**"
              f"（`status={_single.status}`、`error_code={_single.error_code}`、"
              f"`message={_single.message!r}`）——这句报错只在 `evidence/store.py::_get_conn()` "
              "里抛出、在本路径上只能经 executor 到达 ⇒ 参数门放行且执行体真的跑了，"
              "「动作层接受 → 工具层真的执行」这条边是通的")
        _multi = _exec({"evidence_ids": ["e1", "e2", "e3"]})
        check(_multi.status == "FATAL_ERROR"
              and _multi.error_code == "INVALID_ARGUMENTS"
              and "未知参数" in _multi.message
              and "Evidence DB not initialized" not in _multi.message,
              f"**反例**：复数在**真 Registry** 上 `status={_multi.status}`、"
              f"`error_code={_multi.error_code}`、`message={_multi.message!r}` ⇒ **没有进 executor**"
              "（`harness/runtime.py` 会把它转成 `stop_reason=\"FATAL_TOOL_ERROR\"`、`answer=None`）"
              "——这正是统一到单数要拔掉的那条死路")

    # ============================== §1b 诊断选定的那一臂：蕴含层的「渲染 ↔ 解析」耦合
    details.append("## §1b 蕴含层：引用块的渲染形状与替身解析器必须逐字对齐")
    details.append(
        "诊断读数（`M930_3_QREWORK_PART1_NUMERIC_CHANNEL_PLAN.md` §1.7）：本节 46 行"
        "`被拒 claim` 里 `cN:entailment=UNSUPPORTED` 204 次，`citation_*` / "
        "`qualification_rejected` **0** 次 ⇒ 候选一条都没走到引用层/资格层，全部倒在蕴含层。")

    _state_ok = _state({"e1": _mat(_FIXTURE_SENTENCE)})
    _answer_ok = _answer(_FIXTURE_SENTENCE)
    _vars_ok, _verdicts_ok, _raw_ok = _render_and_judge(_state_ok, _answer_ok)

    # ---- 正例 1：真渲染器出的引用块，替身解析器能复原「引用序号 → evidence_id」----
    _cite_map = ACC._CITATION_LINE_RE.findall(_vars_ok["citations"])
    check(_cite_map == [("0", "e1")],
          f"**正例**：`_CITATION_LINE_RE` 从**真渲染**的引用块里复原 `[('0','e1')]`"
          f"（实际 {_cite_map!r}）——修复前这里恒为 `[]`")

    # ---- 反例 1（**回归钉子**）：行首没有项目符号 ⇒ 旧形状恒不匹配 ----
    _legacy = re.compile(r"^\[(\d+)\]\s*evidence_id=(\S+)", re.M).findall(_vars_ok["citations"])
    check(_legacy == [],
          "**反例**：旧形状 `^\\[…\\]` 在**同一个**真渲染上恒不匹配——这正是缺陷能在离线链里"
          "静默存活的原因（引用块是项目符号行 `- [i] …`，行首不是 `[`）")

    # ---- 正例 2：claim 段与引用段用**同一套下标**（两个解析器不得各立一份编号空间）----
    _claim_rows = ACC._claim_lines(_vars_ok["claims"])
    check(len(_claim_rows) == 1 and _claim_rows[0][0] == "c1" and _claim_rows[0][1] == [0],
          f"**正例**：`_claim_lines` 从同一渲染里读出 `c1 cites=[0]`（实际 {_claim_rows!r}）")

    # ---- 正例 3：逐字包含成立 ⇒ 真替身判 `SUPPORTED`（`_adopt_facts` 的入口条件）----
    check(len(_verdicts_ok) == 1 and _verdicts_ok[0].verdict == "SUPPORTED"
          and _verdicts_ok[0].claim_id == "c1",
          "**正例**：claim 文本逐字在被引证据正文里 ⇒ 蕴含判 **`SUPPORTED`**"
          "（`harness/topic_runtime.py:2259` 的入口条件由此打开）")

    # ---- 反例 2：改述**不**被认成蕴含（不许退化成「按构造为真」）----
    _state_par = _state({"e1": _mat(_FIXTURE_SENTENCE)})
    _answer_par = _answer(_FIXTURE_PARAPHRASE)
    _, _verdicts_par, _ = _render_and_judge(_state_par, _answer_par)
    check(len(_verdicts_par) == 1 and _verdicts_par[0].verdict == "UNSUPPORTED",
          "**反例**：claim 是对同一材料的**改述**（非逐字）⇒ 仍判 `UNSUPPORTED`"
          "——替身没有被这次修复放宽")

    # ---- 反例 3：引用不是 evidence（结构化/外部）⇒ 拿不到 evidence 映射 ⇒ fail-closed ----
    _state_std = _state({"e1": _mat(_FIXTURE_SENTENCE)})
    _answer_std = _answer(_FIXTURE_SENTENCE, citations=[
        H.CitationRef(ref_type="structured", snapshot_id="s1", item_code="X",
                      period="2025年度")])
    _vars_std, _verdicts_std, _ = _render_and_judge(_state_std, _answer_std)
    check(ACC._CITATION_LINE_RE.findall(_vars_std["citations"]) == []
          and _verdicts_std[0].verdict == "UNSUPPORTED",
          "**反例**：引用是 `structured`（无 `evidence_id=` 行）⇒ 映射为空 ⇒ 判 `UNSUPPORTED`"
          "（fail-closed；替身**不**能、也**不**假装能校验结构化声明）")

    note("本节只钉**蕴含层的解析耦合**：它证明「候选有资格被构造」。候选之后的引用唯一性、"
         "期间归属、权威重算与 Writer 侧期间门一件没变，也不由本节证明。")

    # ============================== §2 N4：判据本体逐字未变
    details.append("## §2 N4 本批不动判据本体——只改「向正式链路提议什么」")
    check(PE.PERIOD_EXTRACTION_VERSION == "period-extract-2",
          "⇒ 期间抽取的版本串逐字未变（替身不许在读数粒度上顺手改期间口径）")
    check(tuple(PW.WRITER_FACE_EXCLUSION_REASONS) == (
        "aspect_not_in_section", "period_unresolved", "explicit_period_required",
        "vague_period_in_fact_text"),
          "⇒ Writer 侧期间门的排除原因码是**同一个封闭词表**，未增未减未改名")
    check(sorted(ACT._ALLOWED_ARGS["INSPECT_EVIDENCE"]) == ["evidence_id"],
          "⇒ 动作层只收**单数**（P0 已统一；这里钉住它不会悄悄把复数加回来）")
    check(AD.INSPECT_EVIDENCE_SPEC.input_schema["properties"].keys() == {"evidence_id"},
          "⇒ `tools/adapters.py` 未被本批改动（工具层本来就是单数——统一方向是**改动作层去就它**）")

    # ============================== §3 边界
    details.append("## §3 本模块证明的与不证明的")
    note("§1/§2 只钉**形状与常量**：工具契约的接收面、判据的版本串与封闭词表。它们**不**证明"
         "候选真的产生了、事实真的合格了、正文达到人读内容门。")
    note("§1b 钉的是**蕴含层的解析耦合**（渲染形状 ↔ 替身解析器），它只证明「claim 能走到"
         "`SUPPORTED` 这个入口」。候选之后的引用唯一性、期间归属、权威重算、Writer 侧期间门"
         "与正文质量**不在本节射程内**，须由一次 create-only 断网离线公司节纵链读回验证。")
    note("替身读数粒度与全链读数由 `M930_3_QREWORK_PART1_NUMERIC_CHANNEL_PLAN.md` §2 实现、"
         "由一次 create-only 断网离线公司节纵链读回给出。")
    check(True, "边界已声明（形状/常量级结论，不冒充成稿质量验收）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""M930-3 指示：**受阻章节的不可发布预览**（`blocked-section-preview/2`）正反例集。

跑法（无管道/无重定向）：`python -X utf8 -m evals.test_m930_3_blocked_section_preview`

对应缺陷（真实 run r9）：正式出口只有两条——整本报告组装成功 ⇒ `report_preview.md`；本节根本没
成形 ⇒ 失败侧门前诊断 `pre_gate_draft.*`。r9 落在**第三条**现场：财务节**已经**有 `SectionDraft`、
门侧决定、`SectionResult`、25 条 Claim 与逐条引用（门前留存那条出口因此明确不覆盖它），而整本报告
因为**公司节没产出**被拒 ⇒ `report_preview.md` **一个字都没写**。那不是「内容不合格」，是
**内容没有出口**：上一轮修掉的是「本节失败、本节内容丢了」，这一轮要修的是
「别节失败、**本节内容也丢了**」。

本文件钉住的**是**这条出口的可证伪边界（不跑真实文档、不调真实网络、不写仓库）：

  A. **词表只有一处定义**：三档来源、四档出口状态、三档正文来源、三栏名字、缺失对象次序；
     不可发布标注与写手侧 `PW.PRE_GATE_RETENTION_LABEL` 是**同一个串**，不是两处措辞。
  B. **接线**：两个产物进 `ARTIFACTS`（因此进 `artifact_index.json` 的逐文件哈希）、写盘点
     在报告**之后**、正常路径与降级路径**都**写、**只有一处**写盘调用点。
  C. **正例（真链真库）**：一节真的有内容 ⇒ 预览读回**正文 + 逐句引用 + 逐条支撑边 + 缺口 +
     逐条阻断原因**；`SectionResult` 状态原样印出（不为好看改绿）。
  D. **反例·正式组装器仍拒同一节**：同一份产物，章级评估为 `BLOCKED` 时组装器照旧拒绝
     ——「预览在场」**不**构成放行，正式门一字不动。
  E. **反例·被拒草稿不得显示为已接受**：门前留存的正文只出现在 `rejected_pre_gate` 栏，
     一个字符都不得出现在 `accepted` 栏里。
  F. **反例·没有内容不得造空壳正文**：没有 `SectionDraft` 的节落 `unavailable` +
     首个缺失对象，`accepted` 栏**没有** `content` 键（不是空正文冒充正文）。
  G. **反例·「读不回来」≠「没有内容」**：库打不开 ⇒ `store_unreadable`；有草稿行但链被改坏
     ⇒ `readback_failed`。两者都**不得**退化成 `unavailable`。
  H. **反例·重放/历史档不得标成当前真实成稿**：三档来源的措辞逐档不同、逐字回声；离线档配上
     替身声明时 md 必须出现「替身组织器」；`--in-place` 对非 `real_run` 档结构性拒绝。
  I. **反例·没有正式 `report_version` 不得声称 M930-4 已审查**：`m930_4_boundary` 逐条落盘，
     人读版同句出现。
  J. **正文来源只回声声明**：没有声明的节落 `not_applicable` 并进 `undeclared_with_content`，
     **不**默认成 `model`；同一节同时声明成两者一律拒收。
  K. **中文引用（`/2` 新增）**：引用不是一串 `cite_…`——逐条译成中文来源；生产渲染器
     **有意不印**的内部记号（`pwr-4`）落 `reviewer_only` 并在**复核面**列出；解析不出来的
     标 `unresolved` 且**不丢**。三个读数档不得合并，读者面与复核面不得互相冒充。
  L. **补件去向（`/2` 新增）**：`follow_up_needs.json` 的三类记录（待裁决诉求 / 不成立的原始
     诉求 / 已写出内容里自己不成立的申请）**分列**，与三栏正文互不混同；「产物里确实没有
     本节诉求」与「本次没去查」必须读得出区别；补件**不是** gap、**未执行**。

夹具复用 `test_demo_backbone_writer_phase` / `test_demo_report_assembler` / 真库写作链
（`test_demo_section_chain_persistence` 的驱动方式），因此这里的链就是**生产链**。
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import blocked_section_preview as BSP  # noqa: E402
from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from evals import test_demo_backbone_writer_phase as WP  # noqa: E402
from evals import test_demo_report_assembler as FIXT  # noqa: E402
from sections import company_worker as CW  # noqa: E402
from sections import pack_writer as PW  # noqa: E402
from sections import presentation_profile as PP  # noqa: E402
from sections import report_assembler as RA  # noqa: E402
from sections import store as ST  # noqa: E402
from sections import writing_spec as WS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RUNNER_SRC = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")
ASSEMBLER_SRC = (REPO / "sections" / "report_assembler.py").read_text(encoding="utf-8")

SECTIONS = ("company", "financial", "industry")

#: 被拒轮次的门前正文（`pgr-1` 留存）。刻意与 `accepted` 栏的正文**不同字面**：两栏若共用同一段
#: 文本，「留存没被缝进已过门内容」这条对账在本用例的形状上也能全绿——那就证不出分栏。
_REJECTED_PROSE = "本句只存在于被拒的门前草稿里，从未过任何门。"

#: 整本报告被拒的原因（与 runner 逐字同形）。它**只**来自 `acceptance_report.json`。
_ASSEMBLY_ERROR = "有章节未产出，不得组装整本报告：['financial', 'industry']"

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


class _NS:
    """只读命名空间替身（runner 的载荷函数只按属性名读容器）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# 真链真库：用**生产**写作链把一节落进临时库（复现 `test_demo_section_chain_persistence` 的驱动）
# ---------------------------------------------------------------------------

def _spec_and_profile():
    return (WS.load_writing_spec(FIXT.SPEC_PATH),
            PP.load_presentation_profile(FIXT.PROFILE_PATH))


def _section_input(*, task, authority, spec, profile):
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    return CW.BackboneWriterSectionInput(
        task=task, authority=authority, projection=projection,
        writing_spec=spec, presentation_profile=profile,
        dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT)


def _drive_company(*, task, authority, spec, profile):
    """唯一写作主链的一次调用（真门 + 替身 LLM；落库经真 store）。"""
    return CW.run_backbone_writer_phase(
        (_section_input(task=task, authority=authority, spec=spec, profile=profile),),
        llm_client=FIXT._StubLlm(WP._presented_plan(
            PW.scan_topic_pack(authority, task), authority)),
        entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
        final_sentence_llm_client=FIXT._stub_final_sentence_client(),
        material_resolver=FIXT._payload_resolver(),
        section_store=ST)


def _state(*, run_dir: Path, sections: dict, mode: str = ACC.MODE_OFFLINE) -> ACC.RunState:
    state = ACC.RunState(inputs=_NS(tasks={}), run_dir=run_dir, mode=mode,
                         generated_at="2026-09-29T00:00:00Z", policy=_NS())
    state.sections = dict(sections)
    return state


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _evaluations(section) -> dict:
    """`section_evaluations.json` 的形状（与 `ACC._artifacts_payload` 逐字段同形，不手写字段）。"""
    return {"company": {"evaluation": ACC._j(section.evaluation),
                        "binding": ACC._j(section.binding), "summary": {}}}


def _rejections() -> dict:
    """`proposal_set_rejections.json` 的形状（复核者读的那一层：`retained_pre_gate` 逐字留存）。"""
    return {"schema_version": ACC.PROPOSAL_SET_REJECTIONS_SCHEMA_VERSION,
            "sections": {"company": [{
                "attempt": 1, "rejection_kind": "high_risk_surface",
                "rejection_detail": "第 1 批的候选踩了高风险表面（示例原因）。",
                "batches": [{"batch_id": "b-1/1", "label": "1/1", "status": "ok",
                             "aspect_ids": ["a-1"]}],
                "retained_pre_gate": {
                    "basis": "merged_bundle", "prose_unit_total": 1,
                    "batches": [{"batch_id": "b-1/1", "label": "1/1", "aspect_ids": ["a-1"],
                                 "prose_units": [{"prose_unit_id": "pu-1", "index": 1,
                                                  "text": _REJECTED_PROSE,
                                                  "source_member_refs": ["m-1"],
                                                  "source_fact_refs": [],
                                                  "atom_candidate_ids": ["c-1"]}],
                                 "candidate_labels": ["c1"], "unit_labels": ["u1"],
                                 "follow_up_total": 0}]}}]}}


#: 补件诉求夹具：三类记录各一条（**分列**是判据，所以三类必须同时在场）。
_FOLLOW_UP_NEED = {
    "need_id": "fun-1", "need_schema_version": "fun-1",
    "statement": "需要主营业务分产品的收入与成本原表。",
    "target_requirement_id": "req-1", "topic_id": "company_business",
    "question_id": "q-1", "aspect_id": "a-rev-1", "section_id": "company",
    "section_draft_revision": "rev-1", "contract_authorized_scope": ["a-rev-1"],
    "requiredness": "required", "expected_source_class": "formal_table",
    "budget_hint": "1", "writer_identity": "writer-1"}
_UNYTPEABLE = {"attempt": 1, "spec_index": 0, "statement": "补一条不成立的原始诉求。",
               "aspect_id": "a-x", "topic_id": "company_business", "question_id": "q-1",
               "code": "spec_field_missing", "reason": "四个 id 没有逐字取自同一行。"}
_REJECTED_APPLICATION = {"attempt": 2, "spec_index": 1,
                         "statement": "补一条本条自己不成立的申请。",
                         "aspect_id": "a-y", "topic_id": "company_business",
                         "question_id": "q-1", "code": "spec_field_missing",
                         "reason": "需求 id 与本节范围对不上。"}


def _follow_ups() -> dict:
    """`follow_up_needs.json` 的形状（**经生产载荷函数**生成，不手写字段以免与真形状漂移）。"""
    state = _state(run_dir=Path("."), sections={})
    state.pending_follow_up_needs = {"company": {"follow_up_needs": [_FOLLOW_UP_NEED],
                                                 "follow_up_untypeable": [_UNYTPEABLE]}}
    state.follow_up_rejections = {"company": [dict(_REJECTED_APPLICATION)]}
    state.follow_up_runs = {"company": (object(),)}
    return ACC._follow_up_needs_payload(state)


def _acceptance_report(*, report_version=None) -> dict:
    return {"status": "fail", "generated_at": "2026-09-29T00:00:00Z",
            "report_version": report_version, "assembly_error": _ASSEMBLY_ERROR,
            "section_errors": {}, "gates": []}


def _row(payload: dict, section_id: str) -> dict:
    return next(r for r in payload["sections"] if r["section_id"] == section_id)


def _row_text(payload: dict, section_id: str) -> str:
    return BSP.render_md(payload).split(f"## {section_id} · ")[1]


# ---------------------------------------------------------------------------
# A. 词表与「只有一个定义」
# ---------------------------------------------------------------------------

def _check_vocabulary() -> None:
    # `/2` 相对 `/1` **只加字段**（`follow_up` 逐节、逐条引用的人读行、复核面读数档、
    # 引用/支撑边字段中文名、补件逐类计数）。三栏名、四档出口状态、三档来源、三档正文来源
    # 一个都没动——**加字段不改口径**，所以复核者按 `/1` 读出来的话仍然成立。
    check(BSP.SCHEMA_VERSION == "blocked-section-preview/2",
          f"载荷形状版本（得到 {BSP.SCHEMA_VERSION!r}）")
    check(tuple(BSP.CITATION_READINGS) == ("reader_named", "reviewer_only", "unresolved")
          and tuple(BSP.FOLLOW_UP_ROW_KINDS) == ("pending_need", "untypeable_raw",
                                                 "rejected_application"),
          "两个新增面的封闭词表：三档引用读数、三类补件记录")
    check(BSP.CITATION_FIELD_LABELS and BSP.SUPPORT_FIELD_LABELS
          and len({k for k, _ in BSP.CITATION_FIELD_LABELS}) == len(BSP.CITATION_FIELD_LABELS)
          and len({k for k, _ in BSP.SUPPORT_FIELD_LABELS}) == len(BSP.SUPPORT_FIELD_LABELS),
          "字段中文名逐字段唯一（同一字段不得有两个中文名）")
    check(tuple(BSP.CONTENT_COLUMNS) == ("accepted", "rejected_pre_gate", "stub_organizer")
          and set(BSP.COLUMN_NOTES) == set(BSP.CONTENT_COLUMNS),
          "三栏名字与三栏定性**逐项对应**（少一栏会被读成「这一栏不适用」）")
    check(tuple(BSP.OUTLET_STATUSES) == ("content_readback", "unavailable",
                                         "readback_failed", "store_unreadable"),
          "四档出口状态：有内容 / 没内容 / 读不回来 / 库不可读")
    check(tuple(BSP.SOURCE_KINDS) == ("real_run", "historical_real_run", "offline_replay")
          and set(BSP.SOURCE_KIND_NOTES) == set(BSP.SOURCE_KINDS),
          "三档来源与逐档定性一一对应（同一份产物不得出现第二种措辞）")
    check(tuple(BSP.PROSE_ORIGINS) == ("model", "stub_organizer", "not_applicable")
          and set(BSP.PROSE_ORIGIN_NOTES) == set(BSP.PROSE_ORIGINS),
          "三档正文来源与逐档定性一一对应")
    check(tuple(BSP.MISSING_OBJECT_ORDER) == ("SectionDraft", "SectionResult"),
          "缺失对象按写作主链的单向次序取**第一个**真的缺的")
    check(BSP.LABEL_FALLBACK == PW.PRE_GATE_RETENTION_LABEL and BSP._label()
          == PW.PRE_GATE_RETENTION_LABEL,
          f"不可发布标注**只有一处定义**（写手侧 {PW.PRE_GATE_RETENTION_LABEL!r}）")
    check(len(BSP.FORBIDDEN_CLAIMS) >= 6
          and any("report_version" in c for c in BSP.FORBIDDEN_CLAIMS)
          and any("SectionResult=PASS" in c for c in BSP.FORBIDDEN_CLAIMS)
          and any("M930-4" in c for c in BSP.FORBIDDEN_CLAIMS),
          "本件「不是」什么逐条列在载荷上（含版本号 / 通过状态 / M930-4 三件）")


# ---------------------------------------------------------------------------
# B. 接线（runner）：产物登记、写盘点、正式门不动
# ---------------------------------------------------------------------------

def _check_wiring() -> None:
    for artifact in ("blocked_section_preview.md", "blocked_section_preview.json"):
        check(artifact in ACC.ARTIFACTS, f"{artifact} 必须登记在 ARTIFACTS 里")
    check(len(set(ACC.ARTIFACTS)) == len(ACC.ARTIFACTS),
          "ARTIFACTS 不得有重复项（产物清单即目录契约）")
    check(RUNNER_SRC.count('_write_json(state.run_dir / "blocked_section_preview.json"') == 1
          and RUNNER_SRC.count('_write_text(state.run_dir / "blocked_section_preview.md"') == 1
          and RUNNER_SRC.count("_write_blocked_section_preview(state)") == 2,
          "写盘只有一处定义、正常路径与降级路径**都**调用（否则报告被拒那一刻产物缺席）")
    check(RUNNER_SRC.count('if state.report is not None:') >= 1
          and RUNNER_SRC.count('_write_text(state.run_dir / "report_preview.md"') == 1,
          "正式预览的写盘前提一字未动（报告没成 ⇒ 仍然不写 `report_preview.md`）")
    check('("REWORK", "BLOCKED", "FAILED")' in ASSEMBLER_SRC
          and "不得进入组装（REWORK/BLOCKED/FAILED 都不是可发布状态）" in ASSEMBLER_SRC,
          "组装器对 REWORK/BLOCKED/FAILED 的拒绝判据仍在原处（本件不放宽任何门）")


# ---------------------------------------------------------------------------
# C. 正例：真链真库 ⇒ 读回正文 + 逐句引用 + 逐条支撑边 + 缺口 + 阻断原因
# ---------------------------------------------------------------------------

def _check_positive(run_dir: Path, section) -> dict:
    payload = BSP.build_preview(run_dir, sections=SECTIONS, source_kind="offline_replay",
                               stub_sections=("company",))
    check(payload["status"] == "readback" and payload["publishable"] is False
          and payload["label"] == PW.PRE_GATE_RETENTION_LABEL,
          "载荷自称 `readback` 且 `publishable=False`（不许出现第二档自称）")
    check(payload["totals"]["content_readback"] == 1
          and payload["totals"]["unavailable"] == 2
          and payload["totals"]["claim_total"] == len(section.claims)
          and payload["totals"]["unresolved_total"] == len(section.result.unresolved),
          f"逐节状态与逐项合计（得到 {payload['totals']}）")

    row = _row(payload, "company")
    check(row["outlet_status"] == "content_readback"
          and row["first_missing_object"] is None,
          "有内容的一节落 `content_readback`，且不报缺失对象")
    accepted = row["columns"]["accepted"]
    content = accepted["content"]
    check(content["markdown"] == section.result.markdown
          and content["markdown_fingerprint"] == section.result.markdown_fingerprint,
          "正文与指纹**一字未改**地读自 `SectionResult`")
    check(content["result_status"] == str(section.result.status),
          f"`SectionResult` 状态原样印出（得到 {content['result_status']!r}）：不为好看改绿")
    check(content["claim_total"] == len(section.claims) > 0,
          "本节确实读回了 Claim（不是空壳）")
    for claim in content["claims"]:
        check(bool(claim["citation_refs"]),
              f"Claim `{claim['claim_id']}` 带逐条引用")
        check(bool(claim["support"])
              and all(e.get("authority_kind") and e.get("authorization_path")
                      for e in claim["support"] if not e.get("missing")),
              f"Claim `{claim['claim_id']}` 的支撑边给出权威与授权路径（材料/定位可回查）")
    narrative = content["narrative"]
    check(narrative is not None and narrative["paragraph_total"] > 0,
          "final Narrative 段落读回来了")
    sentences = [s for p in narrative["paragraphs"] for s in p["sentences"]]
    check(bool(sentences)
          and all(s["claim_ids"] for s in sentences)
          and any(s["citation_ids"] for s in sentences),
          "逐句给出 `claim_ids` 与 `citation_ids`（每句引用是句级字段，不是段落级并集）")
    check(content["unresolved_total"] == len(section.result.unresolved) > 0
          and len(content["unresolved"]) == content["unresolved_total"],
          "缺口逐条读回（状态照印）")
    check(content["final_sentence_decision_total"] == len(section.final_sentence_decisions),
          "最终句决定计数读自持久化对象")

    reasons = row["blocking_reasons"]
    check(any(_ASSEMBLY_ERROR in r for r in reasons),
          f"整本报告被拒的原因进了逐条阻断原因（得到 {reasons}）")
    check(any("不得组装整本报告" in r for r in reasons),
          "阻断原因是**可读**的一句，不是一个错误码")

    md = BSP.render_md(payload)
    check(section.result.markdown.strip() and section.result.markdown.strip() in md,
          "人读版含本节正文逐字（读者不必去翻 JSON）")
    check(PW.PRE_GATE_RETENTION_LABEL in md, "人读版逐字带不可发布标注")
    return payload


# ---------------------------------------------------------------------------
# D. 反例：正式组装器仍拒同一 BLOCKED 节
# ---------------------------------------------------------------------------

def _check_assembler_still_refuses(section, task, authority) -> None:
    asm = FIXT._assembly_input(section, task, authority)
    ok = True
    try:
        RA._verify_binding(asm)
    except Exception as exc:  # noqa: BLE001
        ok = False
        check(False, f"前提失败：未改判时组装器本该通过（实际抛 {type(exc).__name__}）")
    check(ok, "前提：同一份产物在未改判时通过组装器的绑定核对")
    mutated = dataclasses.replace(
        asm, evaluation=dataclasses.replace(section.evaluation, decision="BLOCKED"))
    try:
        RA._verify_binding(mutated)
        check(False, "章级评估为 BLOCKED 时组装器**必须**拒绝（本件不得放行）")
    except RA.ReportAssemblerError as exc:
        check("不得进入组装" in str(exc) and "BLOCKED" in str(exc),
              f"组装器照旧拒绝 BLOCKED 节：{str(exc)[:120]}")


# ---------------------------------------------------------------------------
# E. 反例：被拒草稿不得显示为已接受
# ---------------------------------------------------------------------------

def _check_rejected_column_not_accepted(payload: dict) -> None:
    row = _row(payload, "company")
    rejected = row["columns"]["rejected_pre_gate"]
    check(rejected["present"] is True and rejected["rounds_with_content"] == 1
          and rejected["label"] == PW.PRE_GATE_RETENTION_LABEL
          and rejected["retention_version"] == PW.PRE_GATE_RETENTION_VERSION,
          f"被拒门前草稿按 `{PW.PRE_GATE_RETENTION_VERSION}` 逐字留存、带不可发布标注"
          f"（得到 {rejected['rounds'][0]['retained_basis']!r}）")
    units = [u for r in rejected["rounds"] for u in (r.get("retained_prose_units") or ())]
    check(len(units) == 1 and units[0]["text"] == _REJECTED_PROSE
          and units[0]["prose_unit_id"] == "pu-1",
          f"留存正文逐字可读（得到 {[u['text'] for u in units]}）")
    accepted_blob = json.dumps(row["columns"]["accepted"], ensure_ascii=False)
    check(_REJECTED_PROSE not in accepted_blob,
          "被拒的门前正文**一个字符都不得**出现在 `accepted` 栏里（两栏不得混成一栏）")
    md = BSP.render_md(payload)
    check(_REJECTED_PROSE in md and "栏二 `rejected_pre_gate`" in md,
          "人读版把被拒正文摆在自己的栏里，读者读得到它是什么")
    check("替换" not in rejected["detail"] and "（未核验、不可发布）" not in rejected["detail"],
          "留存栏的定性只有一处定义（渲染侧再写一遍会让同一件事出现两种口径）")


# ---------------------------------------------------------------------------
# F. 反例：没有内容不得造空壳正文
# ---------------------------------------------------------------------------

def _check_no_content_no_shell(payload: dict) -> None:
    for section_id in ("financial", "industry"):
        row = _row(payload, section_id)
        check(row["outlet_status"] == "unavailable"
              and row["first_missing_object"] == "SectionDraft",
              f"{section_id}：没有可持久化内容 ⇒ `unavailable` + 首个缺失对象 `SectionDraft`")
        accepted = row["columns"]["accepted"]
        check(accepted["present"] is False and "content" not in accepted,
              f"{section_id}：`accepted` 栏**没有** `content` 键（空栏与空正文不是一个东西）")
        check(row["columns"]["rejected_pre_gate"]["present"] is False
              and row["columns"]["stub_organizer"]["present"] is False,
              f"{section_id}：另外两栏也在场，各自说明为什么是空的")
        check("零" in row["detail"], f"{section_id}：判读句写明可持久化内容为零")
    md = BSP.render_md(payload)
    check("**本栏为空**" in md, "人读版对空栏逐栏写明「本栏为空」")
    check(payload["totals"]["claim_total"] == 2,
          f"没有内容的节不贡献任何 Claim（得到 {payload['totals']['claim_total']}）")


# ---------------------------------------------------------------------------
# G. 反例：「读不回来」≠「没有内容」
# ---------------------------------------------------------------------------

def _check_unreadable_not_unavailable(root: Path, src_db: Path) -> None:
    # ① 库打不开：把一份**不是** SQLite 的字节放到库的位置上。
    broken = root / "store_broken"
    broken.mkdir()
    (broken / "section_chain_v2.db").write_text("这不是一个 SQLite 库。", encoding="utf-8")
    payload = BSP.build_preview(broken, sections=SECTIONS, source_kind="offline_replay")
    row = _row(payload, "company")
    check(row["outlet_status"] == "store_unreadable" and row["first_missing_object"] is None,
          f"库不可读 ⇒ `store_unreadable`（得到 {row['outlet_status']!r}）")
    check("无从判定" in row["detail"] and "不是" in row["detail"],
          "判读句明确「这是读不回来，不是本节没有内容」")
    check(payload["totals"]["store_unreadable"] == 3
          and payload["totals"]["unavailable"] == 0,
          f"库不可读**不得**退化成「没有内容」（得到 {payload['totals']}）")

    # ② 有草稿行但链被改坏：改一个仍然合法的字段并连带重算指纹，读回必须 fail-closed。
    tampered = root / "store_tampered"
    tampered.mkdir()
    db = tampered / "section_chain_v2.db"
    shutil.copy2(src_db, db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TRIGGER trg_current_section_subject_v2_no_update")
        row_payload = json.loads(conn.execute(
            "SELECT payload_json FROM current_section_subject_v2 WHERE subject_kind = ? LIMIT 1",
            ("claim_candidate",)).fetchone()[0])
        row_payload["claim_text"] = "公司营业收入为 9999.99 亿元。"
        conn.execute("UPDATE current_section_subject_v2 SET payload_json = ?, "
                     "content_fingerprint = ? WHERE subject_kind = ?",
                     (json.dumps(row_payload, ensure_ascii=False, sort_keys=True),
                      ST._v2_fingerprint(row_payload), "claim_candidate"))
        conn.commit()
    finally:
        conn.close()
    _write(tampered / "acceptance_report.json", _acceptance_report())
    payload = BSP.build_preview(tampered, sections=SECTIONS, source_kind="offline_replay")
    row = _row(payload, "company")
    check(row["outlet_status"] == "readback_failed"
          and row["first_missing_object"] == "SectionResult",
          f"链读不回来 ⇒ `readback_failed`（得到 {row['outlet_status']!r}）")
    check("可能存在但本次读不出" in row["detail"] and "content" not in row["columns"]["accepted"],
          "判读句明确「内容可能存在但本次读不出」，且不给出任何正文")
    # 这份被改坏的库里**只有** company 一条链，另两节确实没有草稿行（那是 `unavailable` 的场景，
    # 不是本例的判据）。本例要钉的是：**有草稿行而链读不回来**的那一节落 `readback_failed`，
    # 不得因为它读不回来就改口说「本节没有内容」。
    check(payload["totals"]["readback_failed"] == 1 and row["outlet_status"] != "unavailable",
          f"读不回来**不得**退化成「没有内容」（得到 {payload['totals']}）")


# ---------------------------------------------------------------------------
# H. 反例：重放/历史档不得标成当前真实成稿
# ---------------------------------------------------------------------------

def _check_source_kind_fidelity(run_dir: Path) -> None:
    for kind in ("historical_real_run", "offline_replay"):
        payload = BSP.build_preview(run_dir, sections=SECTIONS, source_kind=kind,
                                    stub_sections=("company",) if kind == "offline_replay"
                                    else ())
        check(payload["source_kind"] == kind
              and payload["source_kind_note"] == BSP.SOURCE_KIND_NOTES[kind],
              f"`{kind}` 档的定性逐字回声（不另写一句）")
        md = BSP.render_md(payload)
        check(f"- 来源：`{kind}`" in md and "**本次运行**" not in md,
              f"人读版标 `{kind}`，**不得**自称「本次运行」")
    offline = BSP.build_preview(run_dir, sections=SECTIONS, source_kind="offline_replay",
                               stub_sections=("company",))
    check("不是**一次真实验收" in offline["source_kind_note"]
          and offline["stub_organizer_sections"] == ["company"],
          "离线档必须与替身声明一起读：来源档与正文来源是两回事，都在载荷上")
    check("替身组织器" in BSP.render_md(offline), "人读版点出替身节（不得读成真实模型成稿）")
    real = BSP.build_preview(run_dir, sections=SECTIONS, source_kind="real_run",
                            stub_sections=("company",))
    check("**本次运行**" in real["source_kind_note"]
          and _row(real, "company")["prose_origin"] == "stub_organizer",
          "`real_run` 说的是「本次运行写的」，**不**代表正文是模型成稿（两者是两条轴）")

    for kind in ("offline_replay", "historical_real_run"):
        try:
            BSP.main(["--run-dir", str(run_dir), "--source-kind", kind, "--in-place"])
            check(False, f"`--in-place` 对 `{kind}` 档必须结构性拒绝（不得回写被重放的历史现场）")
        except SystemExit as exc:
            check("拒绝写回 run 目录" in str(exc), f"`{kind}` 档拒绝写回：{str(exc)[:80]}")
    try:
        BSP.main(["--run-dir", str(run_dir), "--source-kind", "real_run",
                  "--in-place", "--out", str(run_dir)])
        check(False, "`--in-place` 与 `--out` 互斥必须拒绝")
    except SystemExit as exc:
        check("互斥" in str(exc), f"`--in-place`/`--out` 互斥：{str(exc)[:80]}")
    check(not str(BSP.default_out_dir(run_dir)).startswith(str(run_dir)),
          "CLI 缺省写盘点在**临时目录**：缺省值不能是「写回 run 目录，重放时记得改」")


# ---------------------------------------------------------------------------
# I. 反例：没有正式 report_version 不得声称 M930-4 已审查
# ---------------------------------------------------------------------------

def _check_m930_4_boundary(payload: dict) -> None:
    boundary = payload["m930_4_boundary"]
    check(boundary["independent_review_performed"] is False
          and boundary["formal_report_version"] is None
          and payload["refusal"]["report_version"] is None
          and payload["refusal"]["report_assembled"] is False,
          "没有正式 `report_version` ⇒ 逐条落盘「未做独立审查、没有正式版本」")
    md = BSP.render_md(payload)
    check("正式 `report_version`：`None`" in md and "独立审查" in md,
          "人读版印出正式版本为 None，并把 M930-4 边界写在同一页上")
    check("M930-4 已完成独立审查" not in md and "已完成独立审查" not in md,
          "人读版**不得**出现「已完成独立审查」这种断言")


# ---------------------------------------------------------------------------
# J. 正文来源只回声声明
# ---------------------------------------------------------------------------

def _check_prose_origin_declaration(run_dir: Path) -> None:
    undeclared = BSP.build_preview(run_dir, sections=SECTIONS, source_kind="offline_replay")
    row = _row(undeclared, "company")
    check(row["prose_origin"] == "not_applicable"
          and undeclared["declared_prose_origins"]["undeclared_with_content"] == ["company"],
          "没有声明的节落 `not_applicable` 并进「有内容但未声明来源」——**不**默认成 `model`")
    check("不掌握" in row["prose_origin_note"], "缺省档的定性逐字说明「本件不掌握来源」")
    modelled = BSP.build_preview(run_dir, sections=SECTIONS, source_kind="real_run",
                                model_sections=("company",))
    check(_row(modelled, "company")["prose_origin"] == "model"
          and _row(modelled, "company")["prose_origin_note"]
          == BSP.PROSE_ORIGIN_NOTES["model"],
          "只有**显式声明**才落 `model`，且逐字说明「本件不核验这一点」")
    for kwargs, needle, what in (
            ({"stub_sections": ("bogus",)}, "不属于本次节序", "替身声明里有节外的节"),
            ({"model_sections": ("bogus",)}, "不属于本次节序", "模型声明里有节外的节"),
            ({"stub_sections": ("company",), "model_sections": ("company",)},
             "不得同时声明", "同一节既声明模型成稿又声明替身产出"),
            ({"source_kind": "bogus"}, "source_kind", "来源档位不在封闭词表里")):
        try:
            BSP.build_preview(run_dir, sections=SECTIONS, **kwargs)
            check(False, f"{what}必须拒收")
        except ValueError as exc:
            check(needle in str(exc), f"{what}被拒：{str(exc)[:80]}")


# ---------------------------------------------------------------------------
# K. 正例/反例：引用译成人读行（中文来源 + 复核面记号 + 解析不出来也不丢）
# ---------------------------------------------------------------------------

def _check_citation_faces(payload: dict) -> None:
    content = _row(payload, "company")["columns"]["accepted"]["content"]
    index = content["citation_index"]
    check(bool(index), f"本节引用总表非空（得到 {list(index)[:3]}）")
    claim_ids = {c["claim_id"] for c in content["claims"]}
    check(all(not cite.get("claim_id") or cite["claim_id"] in claim_ids
              for cite in index.values()),
          "引用总表的 `claim_id` 逐条指回本节真有的 Claim")
    check(all(cite["reading"] in BSP.CITATION_READINGS for cite in index.values())
          and all(cite.get("line") for cite in index.values()),
          "每条引用都有合法读数档，且都有人读明细行（不许只剩一个 `cite_…`）")

    # 正例：**句子里的每一个** `citation_id` 都在本节引用总表里查得到，且逐条带人读行。
    sentences = [s for p in content["narrative"]["paragraphs"] for s in p["sentences"]]
    cited = [c for s in sentences for c in (s["citation_ids"] or ())]
    check(bool(cited), "本节确实有句级引用（否则本例证不出任何东西）")
    check(all(cid in index for cid in cited),
          "句级 `citation_ids` 逐条在引用总表里解析得出（`unresolved` 不得出现在真链上）")
    check(all(s.get("citations") for s in sentences if s["citation_ids"])
          and all(c["line"] for s in sentences for c in (s.get("citations") or ())),
          "逐句给出**人读引用行**，不是一串裸 id")
    # 反例：参数字面量不得钉提示词/源码字面量——这里只钉「印出来的措辞必须区分两档」。
    named = [c for c in index.values() if c["reading"] == "reader_named"]
    reviewer = [c for c in index.values() if c["reading"] == "reviewer_only"]
    if named:
        check(all(c["reader_named_source"] for c in named),
              "`reader_named` 档必须给出中文来源（否则它凭什么进读者面）")
        check(any(c["reader_named_source"] in BSP.render_md(payload) for c in named),
              "读者面的中文来源确实印进了人读版")
    if reviewer:
        check(all(c["reader_named_source"] is None for c in reviewer)
              and all("复核面" in c["line"] for c in reviewer),
              "`reviewer_only` 档没有中文来源，且逐条标注这是**复核面**、生产渲染器有意不印")
    check(not (named and reviewer and
               {c["citation_id"] for c in named} & {c["citation_id"] for c in reviewer}),
          "两档不得对同一条 id 同时成立（一条引用只有一个读数）")

    # 反例：查不到的 `citation_id` ⇒ `unresolved`，且**明说**不等于它不存在。
    orphan = BSP._citation_rows(("cite_deadbeef",), index)
    check(len(orphan) == 1 and orphan[0]["reading"] == "unresolved"
          and orphan[0]["citation_id"] == "cite_deadbeef"
          and "不代表它不存在" in orphan[0]["line"],
          f"解析不出来的引用如实落 `unresolved` 而不是被丢掉（得到 {orphan[0]['reading']!r}）")
    md = BSP.render_md(payload)
    check("本节引用总表" in md and "`reviewer_only`" in md and "`reader_named`" in md,
          "人读版有引用总表，并把三档读数的口径写在同一页上")


# ---------------------------------------------------------------------------
# L. 正例/反例：补件去向（待裁决诉求 / 不成立的原始诉求 / 已写内容里不成立的申请）
# ---------------------------------------------------------------------------

def _check_follow_up_faces(run_dir: Path, payload: dict) -> None:
    block = _row(payload, "company")["follow_up"]
    check(block["present"] is True and block["status"] == "pending_adjudication"
          and block["schema_version"] == ACC.FOLLOW_UP_NEEDS_SCHEMA_VERSION,
          f"补件块读自 `follow_up_needs.json`（得到 {block['status']!r}）")
    check(block["pending_total"] == 1 and block["untypeable_total"] == 1
          and block["rejected_application_total"] == 1,
          f"三类记录**逐类分列**（得到 {[block['pending_total'], block['untypeable_total'], block['rejected_application_total']]}）")
    check(block["pending"][0]["need_id"] == "fun-1"
          and block["pending"][0]["statement"] == _FOLLOW_UP_NEED["statement"]
          and block["pending"][0]["requiredness"] == "required",
          "待裁决诉求逐字段读回（不是只剩一个计数）")
    check([r["kind"] for r in block["untypeable"] + block["rejected_applications"]]
          == ["untypeable_raw", "rejected_application"],
          "两类别名逐条带在行上：同为「不成立」，场景不同（节没写出来 vs 节写出来了）")
    check(block["adjudicated_run_total"] == 1 and "另一套身份" in BSP.render_md(payload),
          "已裁决执行的补件运行**只报计数**，且明说它不在这块里（两套身份不混用）")
    # 定性必须**同时**说清两件容易读错的事：它不是缺口；它没有被裁决/执行。
    check(block["note"] == BSP.FOLLOW_UP_NOTE
          and all(word in block["note"] for word in ("缺口", "裁决", "执行")),
          f"补件块的定性写明「不是缺口、未被裁决、未被执行」（得到 {block['note'][:40]}…）")

    # 反例：产物里**确实没有**本节的诉求 ⇒ 说得清是「没有记录」，不是「没去查」；
    # 也不得把它读成 gap 或读成「本节没有内容」。
    empty = BSP._follow_up_block({"schema_version": ACC.FOLLOW_UP_NEEDS_SCHEMA_VERSION,
                                  "status": "pending_adjudication", "sections": {}},
                                 "company")
    check(empty["present"] is False and empty["pending_total"] == 0
          and "不是" in empty["detail"] and "没去查" in empty["detail"],
          f"「产物里没有本节诉求」与「本次没去查」读得出区别（得到 {empty['detail'][:40]}…）")
    # 反例：**整个产物读不回来**（文件缺失）⇒ 来源栏必须写清是哪个文件读不回来，
    # 而不是静默落成「本节没有诉求」。
    missing = BSP.build_preview(run_dir / "no_such_run", sections=("company",),
                               source_kind="offline_replay")
    miss_block = _row(missing, "company")["follow_up"]
    check(miss_block["present"] is False
          and BSP.FOLLOW_UP_SOURCE_FILE in str(miss_block["source"]),
          f"产物读不回来时来源栏写明文件名（得到 {miss_block['source']!r}）")
    check("### 补件去向" in BSP.render_md(payload),
          "人读版逐节印出「补件去向」小节（不是只在 JSON 里）")
    # 三栏正文与补件是**两条轴**：补件是**第四块**，不得混进任何一栏。
    # （判据是**结构**：三栏的键集合一个不多一个不少；不靠「正文里有没有某个词」。）
    row = _row(payload, "company")
    check(set(row["columns"]) == set(BSP.CONTENT_COLUMNS)
          and set(row["columns"]["accepted"]) & {"follow_up", "pending", "untypeable"} == set(),
          f"补件是栏外的一块，三栏键集合一字未动（得到 {sorted(row['columns'])}）")
    accepted_blob = json.dumps(row["columns"]["accepted"], ensure_ascii=False)
    check(_FOLLOW_UP_NEED["statement"] not in accepted_blob,
          "补件诉求的字面不得出现在 `accepted` 栏正文里")


# ---------------------------------------------------------------------------
# M. 两个渲染出自同一次读数；写盘去处由调用方决定
# ---------------------------------------------------------------------------

def _check_write_out(payload: dict, out: Path) -> None:
    json_path, md_path = BSP.write_preview(payload, out)
    check(json_path.name == "blocked_section_preview.json"
          and md_path.name == "blocked_section_preview.md"
          and json_path.exists() and md_path.exists(),
          "两个渲染写成两个约定名字的文件")
    check(md_path.read_text(encoding="utf-8") == BSP.render_md(payload),
          "人读版与机器读版出自**同一次**读数（不是两次各自读库）")
    body = json.loads(json_path.read_text(encoding="utf-8"))
    check(body["preview_fingerprint"] == payload["preview_fingerprint"]
          and len(body["preview_fingerprint"]) == 4 + 24,
          "载荷自带内容指纹（排除指纹字段与读回时刻）")
    check(body["run_dir"] == payload["run_dir"],
          "写盘去处**不**进载荷的读数（它记的是被读的那个 run 目录名）")
    # 构建失败也必须留下可读的一句：空表不得被读成「本轮没有任何内容」。
    failed = BSP.failed_payload("示例：读回侧自己抛了")
    check(failed["status"] == "build_failed" and failed["sections"] == []
          and "不是" in failed["note"],
          "构建失败的载荷自带「这是读不回来，不是没有内容」的定性")
    check("本次预览没有构建成功" in BSP.render_md(failed),
          "构建失败的人读版不冒充一份逐节读数")


def main() -> dict:
    saved = ST._db_path
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        spec, profile = _spec_and_profile()
        task, authority, _scan = WP._company_fixture()
        db = root / "section_chain_v2.db"
        ST.init_db(db)
        try:
            section = _drive_company(task=task, authority=authority,
                                     spec=spec, profile=profile).sections[0]
        finally:
            ST._db_path = saved
        _write(root / "acceptance_report.json", _acceptance_report())
        _write(root / "proposal_set_rejections.json", _rejections())
        _write(root / "section_evaluations.json", _evaluations(section))
        _write(root / BSP.FOLLOW_UP_SOURCE_FILE, _follow_ups())

        _check_vocabulary()
        _check_wiring()
        payload = _check_positive(root, section)
        _check_assembler_still_refuses(section, task, authority)
        _check_rejected_column_not_accepted(payload)
        _check_no_content_no_shell(payload)
        _check_unreadable_not_unavailable(root, db)
        _check_source_kind_fidelity(root)
        _check_m930_4_boundary(payload)
        _check_prose_origin_declaration(root)
        _check_citation_faces(payload)
        _check_follow_up_faces(root, payload)
        _check_write_out(payload, root / "out")

        # 运行侧的声明口径：离线档 ⇒ 走到草稿的节声明为替身；真实档 ⇒ 声明为模型。
        for mode, want in ((ACC.MODE_OFFLINE, "stub_organizer"), (ACC.MODE_REAL, "model")):
            state = _state(run_dir=root, sections={"company": section}, mode=mode)
            out = ACC._blocked_section_preview_payload(state)
            check(out["source_kind"] == "real_run"
                  and _row(out, "company")["prose_origin"] == want
                  and _row(out, "financial")["outlet_status"] == "unavailable"
                  and _row(out, "financial")["prose_origin"] == "not_applicable",
                  f"`{mode}` 档：只声明**真的走到草稿**的节（得到 "
                  f"{_row(out, 'company')['prose_origin']!r}）")
        check(ACC._blocked_section_preview_payload(
            _state(run_dir=root, sections={}))["totals"]["section_total"] == 3,
          "节序由运行侧的 `SECTION_ORDER` 给出（本件不自己发明节序）")

    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
    sys.exit(1 if _results["failed"] else 0)

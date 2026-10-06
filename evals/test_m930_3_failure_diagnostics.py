"""Eval: M930-3 返修 P5 —— **失败也可审计**的只读诊断产物。

用法: python -m evals.test_m930_3_failure_diagnostics

本条缺陷（计划 §5 第 14 行）
---------------------------
「章节失败（无 `SectionDraft`）」时，产物里必须**逐 aspect** 写「本 run 未能读回验收」，
**不得**写成「已证明本节没有材料」——两者是完全不同的结论：前者是「这一轮没跑完，无从读回」，
后者是一条关于语料的断言。旧口径只有一句 `section_errors[section_id]`（900 字符截断），读回的人
无法分辨「20 个候选被拒」与「其中 3 个被拒」，也无法分辨「没产出」与「没材料」。

本模块测**产物的契约与诚实性**（措辞、None ≠ 0、登记 ≠ 选用、失败节内联、接线），
驱动方式是把手搭的只读现场交给 `ACC._failure_diagnostics_payload(state)`。
它**不**重跑真实研究链、不手工拼任何正文、不冒充 `TopicPack`/`SectionDraft`/`SectionResult`；
真实链上的读回由 P6 的 create-only 真实 run 产物见证。

三种现场（判据自带对照面，防空断言）
------------------------------------
* **有 draft**：对账跑得通 ⇒ 逐层是 dict / `[]` / `0`，状态 `read_back_ok`；
* **有节但 draft 为 None**（失败节的真实形态）：状态是那句措辞，逐层是 `None`；
* **连节都没有**：同上，且失败原因来自「没有产出」而不是异常。

「逐层是 `None`」这条判据必须配「有 draft 时同一字段是 `0`/`[]`/dict」这条对照——否则代码
**一律**返回 `None` 也能让判据变绿，而「一律 None」恰恰是本条缺陷要防的那种不可读回。
同理，「未选中的文档也在列」配「选中的那份也在同一张清单里」，「那句措辞出现在产物里」配
「产物里没有把『没读回』说成事实的句子」。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

from evaluation import run_m930_3_acceptance as ACC
from harness import source_manifest as SM
from harness import tree_materials as TM

#: 受验 runner 的源码（静态守卫用；不执行它）。
_RUNNER_SRC = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")

#: 出现「已证明」时，同一句里必须带着的**禁止**标记：只有「不许这么读」这一种用法是合法的。
_PROHIBITION = ("不得", "不等于", "不能", "不是")


def _aspect(aspect_id: str) -> SimpleNamespace:
    return SimpleNamespace(aspect_id=aspect_id, question_id="q-1", kind="quantitative",
                           content_role="fact", output_destination="section_body",
                           missing_policy="record_gap", time_scope="annual",
                           requirement_text=f"aspect {aspect_id} 的冻结要求原文")


def _requirement(topic_id: str, aspect_ids: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(topic_id=topic_id, question_ids=[f"q-{topic_id}"],
                           contract_version="c-1", contract_fingerprint="fp-1",
                           aspects=tuple(_aspect(a) for a in aspect_ids))


def _task(section_id: str, topic_id: str) -> SimpleNamespace:
    return SimpleNamespace(section_id=section_id, title=f"{section_id} 章节",
                           topic_ids=[topic_id],
                           questions=[SimpleNamespace(question_id=f"q-{topic_id}")])


def _manifest() -> SM.SourceManifest:
    """一份**真实类型**的来源清单：两份登记、一份选中、一份未选中（登记 ≠ 选用）。"""
    judgment = SM.DocumentTypeJudgment(
        document_class="annual_report", matched_marker="年度报告",
        evidence_source_type="annual_report", local_source_class="annual_report",
        basis=SM.CoverEvidence(text="2025 年年度报告", locator="p1 b0",
                               page_number=1, block_index=0),
        confidence="document_cover")
    unresolved = SM.DocumentTypeJudgment(
        document_class=None, matched_marker=None, evidence_source_type=None,
        local_source_class=None, basis=None, confidence="unresolved")
    disclosure = SM.DisclosureDateState(
        date=None, state="unknown", period_hint="2025-03", period_hint_precision="month",
        basis="封面月粒度", ingestion_time="2026-01-01T00:00:00Z")
    period = SM.ContentReportPeriod(
        period="2025-01-01..2025-12-31", period_end="2025-12-31", state="verified",
        basis="封面", scanned_blocks=3, scan_bound="cover")

    def entry(document_id: str, judged, source_name: str) -> SM.SourceManifestEntry:
        return SM.SourceManifestEntry(
            company_id="SUBJ", document_id=document_id, document_version=f"{document_id}@v1",
            file_sha256=f"sha-{document_id}", file_size=10, page_count=9,
            source_name=source_name, source_path=f"materials/{source_name}",
            registry_status="current", material_group="uploaded",
            registered_source_type="other", evidence_set_version="es-1",
            type_judgment=judged, disclosure=disclosure, content_report_period=period,
            policy_effect=SM.SourcePolicyEffect(
                registered_source_type="other", retrieval_boost=1.0, index_priority=5,
                display_label="其他", judged_source_type=judged.document_class,
                judged_retrieval_boost=1.0, judged_index_priority=5, note=""),
            eligibility="eligible_current", eligibility_reason="当前集合内")

    return SM.SourceManifest(
        policy_version="sm-2", company_id="SUBJ", generated_at="2026-09-24T00:00:00Z",
        report_as_of="2026-09-24", report_timezone="Asia/Shanghai",
        financial_data_cutoff="2025-12-31",
        entries=(entry("doc-A", judgment, "A_2025_year.pdf"),
                 entry("doc-B", unresolved, "B_circular.pdf")),
        provenance_findings=("披露日期不可核实到日",),
        selection=(
            SM.DocumentSelection(document_id="doc-A", document_version="doc-A@v1",
                                 selection_state="selected_source_set",
                                 reason_code="current_state_source_anchor", reason="选中",
                                 source_role="current_state_source", retrieval_order=0),
            SM.DocumentSelection(document_id="doc-B", document_version="doc-B@v1",
                                 selection_state="selected_source_set",
                                 reason_code="different_series_topic_participating",
                                 reason="另一系列的成员，同等资格按主题检索",
                                 source_role="topic_participating_source",
                                 retrieval_order=1)),
        primary_document_id="doc-A", primary_document_version="doc-A@v1",
        current_state="resolved",
        document_keys=(
            SM.SourceDocumentKey(company_id="SUBJ", document_id="doc-A",
                                 document_version="doc-A@v1", evidence_set_version="es-1"),
            SM.SourceDocumentKey(company_id="SUBJ", document_id="doc-B",
                                 document_version="doc-B@v1", evidence_set_version="es-1")))


def _draft() -> SimpleNamespace:
    """最小可用的 `SectionDraft`：诊断只读它的 `material_manifest.entries`（空清单）。"""
    return SimpleNamespace(draft_revision="rev-1",
                           material_manifest=SimpleNamespace(entries=()))


class _FakeState:
    """诊断函数只读的现场（`RunState` 的只读切片）。

    `sections` 的三种取值对应三种现场：
      * `{"industry": 有 draft 的节}` → 对账跑得通；
      * `{"industry": draft 为 None 的节}` → 失败节的真实形态；
      * `{}`（节不存在）→ 连节都没有。
    """

    def __init__(self, *, sections: dict, errors: dict, rejections: dict | None = None,
                 pending: dict | None = None, authority_error: bool = False,
                 follow_up_rejections: dict | None = None) -> None:
        self.mode = ACC.MODE_OFFLINE
        self.generated_at = "2026-09-24T00:00:00Z"
        self.run_dir = Path("run-under-test")
        self.sections = sections
        self.section_errors = errors
        self.proposal_set_rejections = rejections or {}
        self.pending_follow_up_needs = pending or {}
        # §三/1：**被采信**那一轮里自己不成立的补件申请（逐节）。它与上面两块是三种场景：
        # 「整束被拒」/「相位终止、诉求待裁决」/「本节写出来了、但某条申请不成立」。
        self.follow_up_rejections = follow_up_rejections or {}
        self._authority_error = authority_error

    def authority_of(self, section_id: str):
        if self._authority_error:
            raise RuntimeError("权威不可得（测试用）")
        # 非 `TopicPackAuthorityInput`/`FinancialAuthorityInput` ⇒ `scan_authority` 抛，
        # 诊断如实记错误路径（这正是「权威取不到」的现场）。
        return SimpleNamespace()


def _inputs(*, with_tasks: bool = True) -> SimpleNamespace:
    requirements = {}
    tasks = {}
    for section_id, topic_id in (("company", "t-co"), ("industry", "t-in")):
        requirements[topic_id] = _requirement(topic_id, (f"a-{topic_id}-1", f"a-{topic_id}-2"))
        if with_tasks:
            tasks[section_id] = _task(section_id, topic_id)
    requirements["t-fin"] = _requirement("t-fin", ("a-fin-1",))
    tasks["financial"] = _task("financial", "t-fin")
    return SimpleNamespace(source_manifest=_manifest(), tasks=tasks,
                           requirements=requirements, authorities={},
                           resolver=None, pack_store=None, run_contexts={},
                           trace_sink=None, research_llm=None)


def _payload(*, drafted: tuple[str, ...] = (), attempted: tuple[str, ...] = (),
             authority_error: bool = False, rejections: dict | None = None,
             pending: dict | None = None, with_tasks: bool = True) -> dict:
    sections: dict[str, SimpleNamespace] = {}
    for section_id in attempted:                      # 有节、无 draft（失败节）
        sections[section_id] = SimpleNamespace(draft=None)
    for section_id in drafted:                        # 有节、有 draft
        sections[section_id] = SimpleNamespace(draft=_draft())
    state = _FakeState(sections=sections,
                       errors={"industry": "PackWriterError: 整束被拒", "company": ""},
                       rejections=rejections, pending=pending,
                       authority_error=authority_error)
    state.inputs = _inputs(with_tasks=with_tasks)
    return ACC._failure_diagnostics_payload(state)


def _strings(value, path: str = "$"):
    """递归列出载荷里所有字符串（连同它们的路径），供措辞类的检查用。"""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def _absence_claims(payload: dict) -> list[str]:
    """找出把「没读回」说成**事实断言**的句子：出现「已证明」而周围没有禁止标记。

    合法的用法只有一种——「**不得**读成『已证明本节没有材料』」。断言「已证明」是否出现因此
    不够：既可能漏掉真正的冒充，也会误伤那句禁止语。这里逐句判读。
    """
    bad: list[str] = []
    for path, text in _strings(payload):
        for match in re.finditer("已证明", text):
            window = text[max(0, match.start() - 60): match.end() + 60]
            if not any(marker in window for marker in _PROHIBITION):
                bad.append(f"{path}: …{window}…")
    return bad


def _none_layers(section: dict) -> dict:
    """逐层报出这一节里读不回的那些字段（供 None-vs-0 对照）。"""
    layers = {}
    for row in section["aspects"]:
        for key in ("raw", "pack", "content_dispositions"):
            if row[key] is None:
                layers[f"aspect[{row['aspect_id']}].{key}"] = None
    manifest = section["writer_material_manifest"]
    if manifest["members"] is None:
        layers["writer_material_manifest.members"] = None
    if manifest["member_count"] is None:
        layers["writer_material_manifest.member_count"] = None
    return layers


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    label = ACC.NOT_READ_BACK_THIS_RUN
    check(label == "本 run 未能读回验收",
          f"唯一措辞常量就是那句（实际 {label!r}）：换措辞要改这个常量，不能各处自己写")

    # ==================================================================
    # A. 失败节（有节、无 SectionDraft）：逐 aspect 写「本 run 未能读回验收」
    # ==================================================================
    payload = _payload(attempted=("industry",))
    industry = payload["sections"]["industry"]
    check(industry["has_section_draft"] is False
          and industry["read_back_status"] == label,
          "无 SectionDraft 的节：read_back_status 恰是「本 run 未能读回验收」")
    check(bool(industry["aspects"]), "逐 aspect 摊开（有冻结 Contract 的 aspect 就有行）")
    check(all(row["read_back_status"] == label for row in industry["aspects"]),
          f"该节每个 aspect 都带同一状态（{len(industry['aspects'])} 个）")
    check(all(row["authority_status"] is None for row in industry["aspects"]),
          "权威读视图取不到时 authority_status 是 None（不是 `covered` 这类肯定值）")
    check(industry["section_error"].startswith("PackWriterError"),
          "该节的失败原因原样带出（900 字符那条字符串仍在，不是被替换掉）")
    check(industry["rejected_proposal_bundles"]["count"] == 0,
          "读数与判据分开：没有被拒束就是 0（0 在这里是**可读回**的零，不是 None）")
    note = industry["authority_face_note"]
    check(label in note and "不得" in note and "没有材料" in note,
          "输入侧说明里写明：本节没有 SectionDraft，不得读成「已证明本节没有材料」")

    unread = _none_layers(industry)
    check(sorted({key.rsplit(".", 1)[-1] for key in unread})
          == ["content_dispositions", "member_count", "members", "pack", "raw"],
          f"读不回的每一层都如实是 None（{len(unread)} 个字段）")
    check(industry["writer_material_manifest"]["read_back_status"] == label
          and industry["writer_material_manifest"]["read_back_note"],
          "精确清单读不回：状态是那句措辞，并附上**为什么**读不回（原因不吞掉）")
    # 两种「没有产出」必须说成两句：**有节记录但没形成 `SectionDraft`** 与
    # **产物里没有这一节的记录**。共同点只有「本节没有产出」这一层；原因混成一句，读回的
    # 人就分不出这一节是跑失败了还是根本没轮到它。原因里带上本节错误，不吞掉。
    failed_note = industry["writer_material_manifest"]["read_back_note"]
    check("没有产出" in failed_note and "SectionDraft" in failed_note
          and "PackWriterError" in failed_note,
          "失败节的原因说清是「有节记录但无 SectionDraft」，并带上本节错误")

    # ==================================================================
    # A′ 对照面：有 SectionDraft 的节 —— 同一批字段必须是 0 / [] / dict
    #    没有这一面，「一律返回 None」也能让上面那几条变绿。
    # ==================================================================
    payload_ok = _payload(drafted=("industry",))
    ok_section = payload_ok["sections"]["industry"]
    check(ok_section["has_section_draft"] is True
          and ok_section["read_back_status"] == "read_back_ok"
          and all(row["read_back_status"] == "read_back_ok"
                  for row in ok_section["aspects"]),
          "对照面：有 SectionDraft 的节状态是 read_back_ok，不是那句措辞")
    check(_none_layers(ok_section) == {},
          "对照面：可读回时这三层**都不是 None**（否则「一律 None」也能变绿）")
    check(all(isinstance(row["raw"], dict) and isinstance(row["pack"], list)
              and row["content_dispositions"] == []
              for row in ok_section["aspects"]),
          "对照面：可读回时 raw 是 dict、pack 是 list、content_dispositions 是 `[]`"
          "（`[]` = 「确实空」，None = 「没读回」，两者在产物里长得不一样）")
    ok_manifest = ok_section["writer_material_manifest"]
    check(ok_manifest["member_count"] == 0 and ok_manifest["members"] == []
          and ok_manifest["read_back_note"] == "",
          "对照面：清单可读回时 member_count 是 0（不是 None）、members 是 `[]`、无原因文字")

    # 同一份产物里三种现场并存：逐节各自如实，不互相污染。
    mixed = _payload(drafted=("company",), attempted=("industry",))
    check(mixed["sections"]["company"]["read_back_status"] == "read_back_ok"
          and mixed["sections"]["industry"]["read_back_status"] == label
          and mixed["sections"]["financial"]["read_back_status"] == label,
          "同一次运行里三种状态并存（有产出 / 无产出 / 未涉及）：逐节各自如实")

    # 连节都没有（没跑到这一节）与失败节区分开来：原因来自「没有产出」。
    payload_absent = _payload(drafted=())
    absent = payload_absent["sections"]["industry"]
    check(absent["read_back_status"] == label
          and "没有产出" in absent["writer_material_manifest"]["read_back_note"],
          "连节都没有时：措辞相同，但原因如实写「没有产出」，不冒充「跑过了但没材料」")
    check("产物里没有这一节的节记录"
          in absent["writer_material_manifest"]["read_back_note"]
          and absent["writer_material_manifest"]["read_back_note"]
          != industry["writer_material_manifest"]["read_back_note"],
          "连节都没有与失败节的原因**不是同一句**（否则「分开说」只是措辞）")

    # 没有 task 的节：不因缺 task 而崩，也不写成「已证明没有 aspect」。
    payload_no_task = _payload(drafted=(), with_tasks=False)
    no_task = payload_no_task["sections"]["company"]
    check(no_task["aspects"] == [] and no_task["read_back_status"] == label
          and label in no_task["authority_face_note"],
          "缺 task 的节：aspect 行是空的，但读回状态仍是那句措辞（空 ≠ 已证明没有 aspect）")

    # 产物里不出现把「没读回」说成事实的句子。
    bad = _absence_claims(payload)
    check(bad == [], f"产物里没有一句把「没读回」说成事实（若有：{bad[:2]}）")
    check(_absence_claims({"x": "已证明本节没有材料"}) != [],
          "对照面：这条判读抓得住真正的冒充（把「已证明」写进产物的写法会被抓）")

    # ==================================================================
    # B. 逐文档选用/未选用：登记 ≠ 选用，两者都在列
    # ==================================================================
    docs = payload["documents"]
    check(docs["registered_total"] == 2 and docs["selected_document_id"] == "doc-A",
          "清单登记 2 份、选中 doc-A（登记数与选用数是两个数）")
    check({row["document_id"] for row in docs["entries"]} == {"doc-A", "doc-B"}
          and {row["document_id"] for row in docs["selection"]} == {"doc-A", "doc-B"},
          "两份材料**都在**清单里，且**都**有选用决定（对照面：未选中的没有消失）")
    # `sm-2` 起：**进入源集** ≠ 「未被选中」。doc-B 是另一系列的成员，它是
    # `topic_participating_source`（同等资格按主题检索），不是 `not_used`——诊断必须把这两类
    # 分开，否则报告会把「本 run 没读到它」读成「它不属于本 run 的材料」。
    check({row["document_id"]: row["source_role"] for row in docs["selection"]}
          == {"doc-A": "current_state_source",
              "doc-B": "topic_participating_source"},
          f"逐份 source_role 进诊断（实际 {[r.get('source_role') for r in docs['selection']]!r}）")
    check(docs["source_set"] and docs["current_state"] == "resolved"
          and [k["document_id"] for k in docs["source_set"]] == ["doc-A", "doc-B"],
          "有序源集按内容寻址身份（四轴）进诊断，当前锚状态可读")
    not_used = [row for row in docs["selection"] if row["source_role"] == "not_used"]
    check(not_used == [] and all(row["retrieval_order"] >= 0 for row in docs["selection"]),
          "本夹具里没有 not_used 成员（对照面：源集成员的序位都 >= 0）")
    check(all(row["reason_code"] for row in docs["selection"]),
          "每份都有 typed reason_code（不是只留一句「未选中」）")
    row_a = next(row for row in docs["entries"] if row["document_id"] == "doc-A")
    check(row_a["registered_source_type"] == "other"
          and row_a["judged_document_class"] == "annual_report",
          "注册类型与有证据支持的类型判断**分列**：注册值原样报告、不暗改历史 DB")
    check(row_a["type_basis"] is not None and row_a["type_basis"]["locator"] == "p1 b0",
          "类型判断的依据带 locator（可定位到封面块）")
    row_b = next(row for row in docs["entries"] if row["document_id"] == "doc-B")
    check(row_b["judged_document_class"] is None and row_b["type_basis"] is None,
          "类型未定就是 None（不塞一个看起来像结论的占位值）")

    # ==================================================================
    # C. 被拒提案与待裁决补件：失败节内联，成功节只留指针与计数
    # ==================================================================
    rejection = {"bundle_digest": "d-1", "candidate_audit": [{"candidate_index": 0}]}
    pending = {"industry": {"follow_up_needs": [{"need_id": "n-1"}],
                            "follow_up_untypeable": [{"raw": "x"}]}}
    failed_sec = _payload(attempted=("industry",), rejections={"industry": [rejection]},
                          pending=pending)["sections"]["industry"]
    check(failed_sec["rejected_proposal_bundles"]["count"] == 1
          and failed_sec["rejected_proposal_bundles"]["rows"] == [rejection],
          "失败节：被拒束**内联**在诊断里（那些行只挂在异常对象上，整节失败时是唯一证据）")
    check(failed_sec["pending_follow_up_needs"]["count"] == 1
          and failed_sec["pending_follow_up_needs"]["untypeable_count"] == 1,
          "待裁决补件逐节计数（含无法定型的那些），并与规范产物指针并列")
    ok_sec = _payload(drafted=("industry",),
                      rejections={"industry": [rejection]})["sections"]["industry"]
    check(ok_sec["rejected_proposal_bundles"]["count"] == 1
          and ok_sec["rejected_proposal_bundles"]["rows"] == []
          and ok_sec["pending_follow_up_needs"]["rows"] == {},
          "对照组：有产出的节只留计数 + 指针（不把整份审计抄第二遍）")

    # ==================================================================
    # D. 权威取不到：如实记原因，且**不改变**读回状态
    # ==================================================================
    err_sec = _payload(attempted=("industry",), authority_error=True)["sections"]["industry"]
    check(err_sec["authority_error"] and "RuntimeError" in err_sec["authority_error"],
          "权威取不到时如实记原因（诊断不因一节取不到就整份失败）")
    check(err_sec["read_back_status"] == label and err_sec["authority_coverage"] is None,
          "权威取不到**不改变**读回状态：无 SectionDraft 仍是那句，也不编造覆盖计数")
    ok_err = _payload(drafted=("industry",), authority_error=True)["sections"]["industry"]
    check(ok_err["read_back_status"] == "read_back_ok" and ok_err["authority_error"],
          "对照面：两件事各自独立——有产出就是 read_back_ok，同时如实记权威取不到")

    # ==================================================================
    # E. 诊断写手**自身**崩掉时：产物仍在，且明说「诊断没读回」
    #    （本轮已经失败了，写产物再崩一次就等于连现场都没有——那正是本条缺陷的最糟形态）
    # ==================================================================
    broken_state = SimpleNamespace(
        mode=ACC.MODE_OFFLINE, generated_at="2026-09-24T00:00:00Z",
        run_dir=Path("run-under-test"), sections={}, section_errors={},
        proposal_set_rejections={}, pending_follow_up_needs={},
        inputs=SimpleNamespace())          # 缺 `source_manifest` ⇒ 构建必失败
    broken = ACC._failure_diagnostics_payload(broken_state)   # 调用方**不得**被带走
    check(str(broken.get("diagnostics_error", "")).startswith("AttributeError"),
          f"诊断构建失败时如实写出异常名与消息（实际 {broken.get('diagnostics_error')!r}）")
    check(broken["documents"] is None and broken["sections"] is None,
          "诊断没读回时 `documents`/`sections` 是 None（**不是** `{}`——空容器会被读成「确实没有」）")
    check("不得" in broken["note"] and "没有材料" in broken["note"],
          "失败载荷自己写明：不得读成「本次没有材料」")
    check("diagnostics_error" not in payload,
          "对照面：正常构建的载荷里没有这个键（它的出现就是「诊断没读回」的标记）")

    # ==================================================================
    # F. 与 runner 的接线：产物存在、版本化、两条写盘路径都写它
    # ==================================================================
    check("failure_diagnostics.json" in ACC.ARTIFACTS,
          "`failure_diagnostics.json` 在 ARTIFACTS 里（因此进 artifact_index 的逐文件哈希）")
    # §三/1：升到 /2（逐节新增 `rejected_follow_up_applications`）。升号的理由同样是**读法**：
    # /1 的逐节诊断里，「本节没有 Draft」只有「材料/事实/阶段终止」几档原因可读，于是
    # 「模型提的补件申请自己不成立、把整节连同已过硬门的 Draft 一起带走」这件事在产物里
    # **无痕**——旧读者会把 r5 财务节读成普通的「写不出内容」。
    # §一（acc-36）：再升到 /3（逐 aspect 新增 `column_unmet`）。升号理由同样是**读法**：
    # /2 的读者看不到 typed 栏目未达原因，只能从 Pack 反推「覆盖门没过」，于是「本轮预算没让
    # 检索发生」「派发了没命中」「材料到了话对不上栏目」「表未获资格」被读成同一件事。
    check(ACC.FAILURE_DIAGNOSTICS_SCHEMA_VERSION == "failure-diagnostics/3",
          f"诊断载荷带自己的 schema 版本（实际 {ACC.FAILURE_DIAGNOSTICS_SCHEMA_VERSION}）")
    check(payload["schema_version"] == ACC.FAILURE_DIAGNOSTICS_SCHEMA_VERSION
          and payload["not_read_back_label"] == label
          and payload["note"],
          "载荷自带版本、那句措辞与用法说明（读者不必读源码才知道怎么判读）")
    # 两条写盘路径：正常路径与**整轮被拒**路径。拒绝也是失败，同样要可审计。
    check(_RUNNER_SRC.count('_write_json(state.run_dir / "failure_diagnostics.json"') == 1
          and _RUNNER_SRC.count('_write_json(run_dir / "failure_diagnostics.json"') == 1,
          "正常路径与拒绝路径各写一次诊断产物（整轮被拒的那次同样有现场可看）")
    # 那句措辞只能在**一处**作为字符串字面出现（常量自己的定义行）；其余写法一律引用常量，
    # 否则两处措辞会各自漂移。注释里复述这句不算——它们不参与产物判读，故只数字面。
    check(_RUNNER_SRC.count(f'"{label}"') == 1,
          f"那句措辞只有一处字符串字面（实际 {_RUNNER_SRC.count(chr(34) + label + chr(34))} 处）")
    check(len(re.findall(r'"failure_diagnostics":\s*[({]', _RUNNER_SRC)) >= 2,
          "正常报告与拒绝报告里都有 `failure_diagnostics` 块，读者不必猜产物叫什么")
    check(ACC.RUNNER_VERSION == "m930-3-acc-40"
          and ACC.ACCEPTANCE_REPORT_SCHEMA_VERSION == "m930-3-acc-report-34",
          "新增产物与报告块 ⇒ runner 与报告 schema 都已升版（acc-17 / report-16：写作链按"
          "aspect 范围确定性分批、截断即拒该批并按对半缩小重问，一次尝试不再等于一次调用 ⇒"
          "拒绝记录新增逐批读数 `batches`；acc-18 / report-17：`anp-3` 键层级进产物 ⇒"
          "`TREE_NAVIGATION` 回读平列 `nav_keys` / `parent_keys` / `key_tier` / `rule_version`；"
          "acc-19 / report-18：预算门提前 + 两条预算轴；acc-20 / report-19：研究轴上限改逐 topic "
          "自派生、研究侧上限块逐 topic 化 ⇒ 诊断的 `documents` 块也随之新增 `source_set` / "
          "`current_state` / `registration_class_mismatches` 与逐份 `source_role`；"
          "acc-21 / report-20：跨文档「材料去向」的 Writer 清单列改为**查过才给布尔值**"
          "（未产出/清单不可得 ⇒ `null` + `writer_manifest_query` + 块级"
          "`writer_manifest_status`，并附 typed 原因），修真实 run r3 把「没查过」印成"
          "`in_writer_manifest=false` 的误读 ⇒ 该块新增这两个字段、一列由 bool 变 bool|None；"
          "acc-22 / report-21：定点返修 C 批 C1 把导航规则升到 `anp-4`（topic 标题段先按 question "
          "归属再随该 question 进祖先层，同 topic 的 aspect 不再齐读别的 question 的父节点正文）"
          "⇒ 上述回读字段的形状未变、**取值语义换了**；"
          "acc-23 / report-22：前置修复 T 批（T1 从权威 Pack 读回的材料包人读/机器读 `material_pack.md`/"
          "`material_pack.json` 进 ARTIFACTS 与 manifest；T2 台账成员一律按四轴身份核对、同 id 错身份"
          "fail-closed；T3 研究轴同样拒收截断，门设在调用返回之前）⇒ 本产物新增 ARTIFACTS 成员与 "
          "`material_pack` 指针块；"
          "acc-24 / report-23：r4 后的业务内容定点返修（① 失败路径不再因 `len(None)` 丢掉整份报告，"
          "装配抛错也先落 `status='assembly_error'` 的降级报告 + `artifact_index.json`；② A7 的"
          "`recovered` 判据由「该节有没有 `SectionDraft`」换成**批次谱系**）⇒ 报告顶层状态词表与"
          "截断策略块的读法都变了；"
          "acc-25 / report-24：定点返修 ⑤ 内容形态分流（§二 2.3 的 `content_qualification` 此前"
          "被 `material_context._decode_envelope` 丢在解析层，Writer 与所有门都看不见"
          "「这一份是勾选表单行」与「这一份是叙述材料」的分界）⇒ 材料包载荷升到 "
          "`material-pack/2`：逐条新增 `content_qualification`，勾选表单行另带五列 + 允许用途 + "
          "排除项，节级新增 `content_kind_counts` / `selection_form_material_ids`；写作策略升到 "
          "`pw-11`（生成器看到的 materials 行多了一列形态读法）；"
          "acc-26 / report-25：定点返修 ② 逐栏目取料（读根资格，`anp-4` → `anp-5`、条目 wire "
          "`anps-3` → `anps-4`）⇒ 报告的**判读窗口**又变了四处：`rule_version` 写 `anp-5`；"
          "`fallback_reason` 新增 `no_anchored_read_root`（有候选、也够分，但上提后没有一支读根"
          "定位得到这一栏 ⇒ 读集为空，不退回读不合格的根）；`candidates[].discard_reason` 新增 "
          "`not_label_anchored` / `not_subject_adjacent`；条目新增 `subject_head`。"
          "本产物的 `documents` / 诊断字段形状**未变**，变的是同一字段的取值集合；"
          "acc-27 / report-26：定点返修 ⑥ 行业节「仅用标点拼接 Claim」改判为「拒 + 确定性有界"
          "定向重组织」（组织器 trace 新增 `reorganization_version` / `reorganizations` / "
          "`reorganization_remedies` / `reorganization_details`）并新增第 9 条判据「正文不得替"
          "系统自报检索 / 核验」，A3 证据块新增 `self_reported_provenance`。本产物（失败诊断）"
          "的形状与取值集合**都未变**，升版是为了让读者知道同一份 `acceptance_report.json` 的"
          "A3 块与正文判据面换了一版；"
          "acc-28 / report-27：定点返修 §三/1「一条填错的补件申请不再杀死整节」。**本产物的"
          "形状这次真的变了**：逐节新增 `rejected_follow_up_applications`（含 `count` / "
          "`artifact` / `rows`），且**无论本节有没有 `SectionDraft` 都内联**——这正是 r5 财务节"
          "缺的那一读：它已经写出内容、硬门已过，却因为**一条**申请的四个 id 跨行拼而整节消失，"
          "在旧产物里与「本节写不出内容」长得一模一样。载荷因此升到 `failure-diagnostics/2`，"
          "`follow_up_needs.json` 升到 `follow-up-needs/2`；"
          "acc-29 / report-28：定点返修第二段（勾选表单行的栏目归属与支撑资格）。本产物的形状"
          "这次**未变**，变的是同一字段的取值集合与它引用载荷的版本：材料包资格列五列 → 六列"
          "（`material-pack/2` → `/3`、读法 `tmr-2`）、逐候选原因与整束 kind 各多一条 "
          "`path_b_ineligible_material_scope`（`proposal-set-rejections/5` → `/6`）——"
          "旧读者会把「这条候选没被单独点名」读成「它可以留用」，而新原因恰好落在那一栏旁边；"
          "acc-30 / report-29：本轮「合格材料 → 合法 Claim → 可读章节」的定点返修。**本产物的"
          "形状这次未变**，变的是它旁边的两条链：① 蕴含边读法 `cer-3` → `cer-4`（逐字镜像的"
          "比较改在标点归一化视图上做）；③ 注入的 need 构造器换成生产实现 `anb-1` 且外部检索"
          "开关变成**执行前真门**——本产物的逐节读数因此可能第一次出现「要求到了 need、但本轮"
          "未检索」这种组合，旧读者会把它读成「本节没有外部来源可用」；"
          "acc-31 / report-30：r7b **后**定点返修第一段（A2 的单元格判据与已裁决的分量规则"
          "对齐）。**本产物的形状未变**，变的是读者对同一份 `acceptance_report.json` 的 A2 块的"
          "判读：旧判据把「三个分量逐字到位、只差一个句末标点」的正确代理单元格读成内容缺陷；"
          "acc-14 的真实 run 只证明缺陷存在，不是本次的实测通过；"
          "acc-32 / report-31 与 acc-33 / report-32：定点批「先证明能成稿」（前者把逐候选裁出这条"
          "出口带上产物与报告；后者让主体名称可核实）。**本产物的形状在这两段里同样未变**，"
          "变的是同一份报告里 `proposal_set_rejections` 与 `subject_declaration` 两块；"
          "acc-34 / report-33：指令 D §二（b）来源归属轴的**读者面**（`srattr-1`）⇒ 新增 "
          "`source_attribution.json` / `source_attribution.md` 两个产物与报告侧 "
          "`source_attribution` 指针块。归属语只由**系统**从已登记身份确定性渲染（材料名 + "
          "登记 `id@版本` + 精确页码 + 可核实披露日；不可核实一律「披露日未知」，不得用入库"
          "时间／PDF 元数据／上传时间／财务期末顶替），写者一个字也不能写；它**不改正文、"
          "不改正文指纹**，只作逐句对账，本产物的形状在这一段里同样未变；"
          "acc-35 / report-34：指令 D §三**失败侧**的门前留存与诊断（`pgr-1`）⇒ 新增 "
          "`pre_gate_draft.json` / `pre_gate_draft.md` 两个产物与报告侧 `pre_gate_draft` 指针块，"
          "`proposal-set-rejections/8` → `/9`（每条被拒记录多出 `retained_pre_gate`）。"
          "它与**本产物**（失败诊断）读的不是同一件事：本产物说「这一节为什么没有正文」，"
          "门前留存说「这一节门前写出过什么」。本产物的形状在这一段里未变；"
          "acc-37 / report-34：业务取材纵链修复 §二 的**表对象信道读法**（材料包读回另成 "
          "`kind=table_object` 一族并读同信封的 `reading_policy`，节级新增 "
          "`table_object_material_ids`，`material-pack/3` → `/4`）。**本产物的形状在这一段里"
          "同样未变**，变的是它旁边那份材料包里 `content_kind_counts` 的**键族**——"
          "旧读者会把表对象读成 `unavailable`（把「不归那个分类器管」读成「读失败」）；"
          "acc-38 / report-34：业务取材纵链修复 §三 的**替身选材与组织**（过长原句按小句边界"
          "切成有界原子逐条过滤；候选原子必须自带陈述对象，无主语残片不再进正文并逐条记 typed "
          "原因；材料按来源角色稳定重排后提案；组织侧每个接缝各取一个中性连接语）。"
          "**本产物的形状与取值集合在这一段里都未变**：它逐条读的是节级运行现场与逐候选拒绝"
          "审计，而本段只改替身提案与行文，门一律不动；acc-39 / report-34：§0.18 W8 单通道的"
          "**读取面**（目标表的正式材料改由图侧单通道产出，信封种类由 `tom-1` 换成 `gtm-1`；"
          "读回侧过去只认 `tom-1`，新信道的表材料因此落回「读不出形态」，现同时认两条并带出"
          "**实际观察到的** `envelope_kind`，`material-pack/4` → `/5`）。**本产物的形状在这一"
          "段里同样未变**，变的是它旁边那份材料包里 `reading_policy` 的键面；acc-40 / "
          "report-34：定点业务纠正①的**勾选行栏目归属**（读法 `tmr-2` → `tmr-3`：所问事项只取"
          "行内前缀，行内没写主语时留空、不再回指所在节点标题；回指会把子项状态锚到整个栏目上，"
          "真实反例见 NDSD_2025 第 28 页那行 `□适用 不适用`）。**本产物的形状在这一段里同样未变**，"
          "变的是它旁边那份材料包里逐条 `content_qualification.selection` 的**所问事项取值**"
          "（可能为空）与人读版对新取值的那两行说明）")

    # ==================================================================
    # G. 跨文档「材料去向」的 Writer 清单列：**没查过 ≠ false**
    #    真实 run r3 实证：三节都没产出 ⇒ `writer_keys` 只从已落库的 draft 派生 ⇒ 整列为空
    #    ⇒ 159 行全被印成 `in_writer_manifest=false`，而该列在产物里的含义是
    #    「到了 Writer 精确清单这一层之前掉的」——「没查过」被记成了「没到 Writer」，
    #    正是验收规则里「未执行不得记为零命中」禁止的那种读数。
    # ==================================================================
    def _destination_state(*, section_present: bool, manifest_entries):
        """跨文档证据只读的最小现场：一个 topic、一个 Pack、一条臂 A outcome、一份材料。"""
        doc_key = SimpleNamespace(document_id="doc-A")
        outcome = SimpleNamespace(
            source_document_key=doc_key, aspect_id="a-1", arm="A",
            material_ids=("m-1",),
            to_dict=lambda: {"aspect_id": "a-1", "arm": "A", "material_ids": ["m-1"],
                             "source_document_key": {"document_id": "doc-A"}})
        material = SimpleNamespace(
            material_id="m-1", material_type="evidence_span",
            locator=SimpleNamespace(to_dict=lambda: {"page": 10}),
            payload_ref=SimpleNamespace())        # 回查必失败 ⇒ payload_chars = -1（不编造）
        pack = SimpleNamespace(
            pack_id="pack-1", source_set=None, materials=(material,), aspect_results=(),
            aspect_source_responsibility=(), source_aspect_outcomes=(outcome,))
        pack_set = SimpleNamespace(pack_for=lambda _topic_id: pack)
        sections: dict = {}
        if section_present:
            draft = (None if manifest_entries is None else
                     SimpleNamespace(material_manifest=SimpleNamespace(
                         entries=tuple(manifest_entries))))
            sections["company"] = SimpleNamespace(draft=draft)
        state = SimpleNamespace(
            sections=sections, inputs=SimpleNamespace(resolver=None,
                                                     tasks={"company": _task("company", "t-co")}))
        state.authority_of = lambda _sid: SimpleNamespace(pack_set=pack_set)
        return state

    def _row(block):
        rows = block["material_destination_by_document"]
        return rows[0] if rows else {}

    # 现场 1：没有节（真实 r3 的形态）——整列不可判定。
    no_section = ACC._cross_document_evidence(
        _destination_state(section_present=False, manifest_entries=None), "company")
    check(no_section["writer_manifest_status"] == "not_available"
          and no_section["writer_manifest_status"] != "checked",
          "无节的现场：块级 Writer 清单状态是 not_available（不是「查过且空」）")
    check("无 draft" in no_section["writer_manifest_error"],
          "无节的原因如实指向「无 draft」，不冒充「材料没到 Writer」")
    row_absent = _row(no_section)
    check(row_absent.get("in_pack") is True,
          "同一行里 `in_pack` 仍是可读回的 True（材料确实在 Pack 里）——两列各自如实")
    check(row_absent.get("in_writer_manifest") is None
          and row_absent.get("writer_manifest_query") == "not_available",
          "该列是 None + query=not_available（**不是** False；False 会被读成「掉了」）")

    # 现场 2：有节但清单读不出——同样不可判定，原因不同（不是「没产出」）。
    no_manifest = ACC._cross_document_evidence(
        _destination_state(section_present=True, manifest_entries=None), "company")
    check(no_manifest["writer_manifest_status"] == "not_available"
          and "清单不可得" in no_manifest["writer_manifest_error"]
          and "无 draft" not in no_manifest["writer_manifest_error"],
          "有节无清单：仍不可判定，且原因与「没产出」分开（两种读不出不是一件事）")
    check(_row(no_manifest).get("in_writer_manifest") is None,
          "该现场该列仍是 None")

    # 对照面：真查过就必须给布尔值——否则「一律返回 None」也能让上面几条变绿。
    empty_manifest = ACC._cross_document_evidence(
        _destination_state(section_present=True, manifest_entries=()), "company")
    check(empty_manifest["writer_manifest_status"] == "checked"
          and empty_manifest["writer_manifest_error"] == "",
          "对照面：清单可读回（空集）时状态是 checked、无原因文字")
    check(_row(empty_manifest).get("in_writer_manifest") is False
          and _row(empty_manifest).get("writer_manifest_query") == "checked",
          "对照面：查过且不在清单里 ⇒ False（这里的 False 才是**可读回**的假）")
    hit = SimpleNamespace(pack_id="pack-1", material_id="m-1")
    matched = ACC._cross_document_evidence(
        _destination_state(section_present=True, manifest_entries=(hit,)), "company")
    check(_row(matched).get("in_writer_manifest") is True,
          "对照面：清单里命中 ⇒ True")

    # 人读版：不可判定不得渲染成 None / False。
    md_no_section = ACC._cross_document_md(
        _destination_state(section_present=False, manifest_entries=None))
    check("不可判定" in md_no_section and "不是 false" in md_no_section,
          "人读版把该列渲染成「不可判定」并写明它不是 false（读者不必去读 JSON 才知道）")
    check("None" not in md_no_section.split("### 材料去向")[-1].split("###")[0],
          "人读版的去向表里不出现裸 `None`（那是实现细节，不是读数）")

    # ==================================================================
    # H. T1 材料包读回：从**权威 Pack** 读回完整原文与去向
    #    这一条防的是「产物里只有 ID 与字数、或从 Writer prompt 倒抄」——那种读回无法回答
    #    「这一次到底取到了哪些原文」，而人验收的第一问恰恰是这一个。
    # ==================================================================
    import hashlib

    _TEXT = "# 经营模式\n公司以直营门店为主，2024 年末门店 1,234 家。"
    _BODY = json.dumps({"content": {"text": _TEXT}}).encode("utf-8")
    _HASH = hashlib.sha256(_BODY).hexdigest()

    def _material_pack_state(*, text_bytes, members, dispositions, locator_id="doc-A",
                             locator_version="v1", with_section=False, body_hash=None):
        body_hash = _HASH if body_hash is None else body_hash
        locator = SimpleNamespace(document_id=locator_id, document_version=locator_version,
                                  to_dict=lambda: {"page": 10, "block_range": [3, 7]})
        ref = SimpleNamespace(object_type="evidence", authority_identity="DOC_AUTHORITY",
                              version="v1", locator=locator, content_hash=body_hash,
                              to_dict=lambda: {"object_type": "evidence"})
        resolved = SimpleNamespace(object_type="evidence", authority_identity="DOC_AUTHORITY",
                                   version="v1", locator=locator, content_hash=body_hash,
                                   payload_bytes=text_bytes)
        resolver = SimpleNamespace(resolve=lambda _ref: resolved)
        material = SimpleNamespace(material_id="m-1", material_type="evidence_span",
                                   locator=locator, content_hash=body_hash, payload_ref=ref)
        aspect_result = SimpleNamespace(aspect_id="a-1", material_ids=("m-1",))
        pack = SimpleNamespace(
            pack_id="pack-1", materials=(material,), aspect_results=(aspect_result,),
            material_dispositions=tuple(dispositions),
            source_set=SimpleNamespace(
                members=members, fingerprint=lambda: "fp-1"))
        pack_set = SimpleNamespace(pack_for=lambda _topic_id: pack)
        sections: dict = {}
        if with_section:
            hit = SimpleNamespace(pack_id="pack-1", material_id="m-1")
            sections["company"] = SimpleNamespace(draft=SimpleNamespace(
                material_manifest=SimpleNamespace(entries=(hit,))))
        state = SimpleNamespace(
            sections=sections,
            inputs=SimpleNamespace(resolver=resolver,
                                   tasks={"company": _task("company", "t-co")}))
        state.authority_of = lambda _sid: SimpleNamespace(pack_set=pack_set)
        return state

    member_key = SM.SourceDocumentKey("ACME", "doc-A", "v1", "set-1")
    _DISP = SimpleNamespace(
        disposition_id="rmd-1", material_id="m-1", admission_state="admitted",
        retention_state="retained", source_validation="validated",
        reason_code="aspect_material_admitted", reason_proof="proved by fixture",
        policy_version="rmd-1", aspect_ids=("a-1",))
    ok_state = _material_pack_state(
        text_bytes=_BODY, members=((member_key, "current_state_source"),),
        dispositions=(_DISP,), with_section=True)
    block = ACC._material_pack_readback(ok_state, "company")
    check(block["status"] == "readback" and block["entry_count"] == 1,
          f"材料包读回给出逐条读数（实际 status={block.get('status')}）")
    row = block["entries"][0]
    check(row["text"] == _TEXT and row["text_chars"] == len(_TEXT)
          and row["text_status"] == "resolved",
          "读回的是**完整原文**，不只是字数（字数够不够读与原文是什么是两个问题）")
    check(row["locator"] == {"page": 10, "block_range": [3, 7]},
          "locator 是 Pack 里的**原件**（不重写、不折算）")
    check(row["document_identity"] == {"company_id": "ACME", "document_id": "doc-A",
                                       "document_version": "v1",
                                       "evidence_set_version": "set-1"}
          and row["document_identity_basis"] == "source_set_member_same_id_and_version",
          f"文档身份是**四轴**，第四轴来自源集成员（实际 {row['document_identity']}）")
    check(row["aspect_ids_from_aspect_results"] == ["a-1"],
          "topic/aspect 对应逐条给出")
    check(row["disposition"]["admission_state"] == "admitted"
          and row["disposition"]["retention_state"] == "retained"
          and row["disposition"]["reason_code"] == "aspect_material_admitted"
          and row["disposition"]["reason_proof"] == "proved by fixture",
          "保留/拒绝去向与 typed 理由逐条给出（不是一句「已处置」）")
    check(row["retention_destination"] == "retained_for_writing"
          and row["in_writer_manifest"] is True,
          "去向下场与 Writer 清单命中各自成列")
    check(block["admission_states"] == ["admitted", "rejected"]
          and block["reason_codes"],
          "词表随载荷给出（读者不必读源码才知道有哪些档）")

    # 第四轴取不到时**留 None 并写明依据**，不得猜一个（与全链的四轴纪律同一条）。
    no_member = ACC._material_pack_readback(
        _material_pack_state(text_bytes=_BODY, members=(),
                             dispositions=(_DISP,)), "company")["entries"][0]
    check(no_member["document_identity"]["evidence_set_version"] is None
          and no_member["document_identity_basis"] == "no_source_set_member",
          "源集里对不上 ⇒ 第四轴是 None + typed 依据（**不猜**）")
    dup_member = ACC._material_pack_readback(
        _material_pack_state(
            text_bytes=_BODY,
            members=((member_key, "current_state_source"),
                     (member_key, "history_and_conflict_source")),
            dispositions=(_DISP,)), "company")["entries"][0]
    check(dup_member["document_identity"]["evidence_set_version"] is None
          and dup_member["document_identity_basis"] == "ambiguous_2_source_set_members",
          "对上**多**份同样不可判定（不取第一份——那正是就近匹配的错法）")

    # 原文读不出 ⇒ typed 状态 + `text_chars=-1`，**不得**把空串当「原文为空」。
    empty_payload = ACC._material_pack_readback(
        _material_pack_state(text_bytes=None, members=((member_key, "current_state_source"),),
                             dispositions=(_DISP,)), "company")["entries"][0]
    check(empty_payload["text_status"].startswith("unavailable")
          and empty_payload["text_chars"] == -1,
          f"payload 无字节 ⇒ text_status=unavailable 且字数 -1（实际 "
          f"{empty_payload['text_status']!r}）")

    # 缺处置记录时必须**看得见**（材料侧义务是每份恰一条）。
    no_disp = ACC._material_pack_readback(
        _material_pack_state(text_bytes=_BODY, members=((member_key, "current_state_source"),),
                             dispositions=()), "company")["entries"][0]
    check(no_disp["disposition"] is None
          and no_disp["retention_destination"] == "no_disposition_recorded",
          "缺处置记录如实记「没有」，不冒充已处置")

    # 人读版与机器读版**同一次读回**的两个渲染：人读版里必须出现同一段原文与同一条理由。
    md = ACC._material_pack_md_from(ACC._material_pack_readback_all(ok_state))
    check(_TEXT in md and "aspect_material_admitted" in md and "set-1" in md,
          "人读版渲染出同一段原文、同一条处置理由与同一个第四轴")

    # 读回入口**自己不抛**：现场缺 `inputs.tasks` 时也只能给出「读不回来」，不能把 run 带走。
    broken = SimpleNamespace(sections={}, inputs=SimpleNamespace())
    broken.authority_of = lambda _sid: SimpleNamespace(pack_set=None)
    ok_all = ACC._material_pack_readback_all(broken)
    check(ok_all["status"] == "readback"
          and len(ok_all["sections_unavailable"]) == len(ACC.SECTION_ORDER)
          and "build_failed_sections" not in ok_all,
          f"读回入口对残缺现场不抛，逐节记「读不回来」（实际 status={ok_all['status']}）")
    broken_md = ACC._material_pack_md(broken)
    check("material_pack.json" in broken_md and "不可读" in broken_md,
          "残缺现场的人读版写明「不可读」并指向机器读版（不抛，也不印成空材料表）")

    # 接线：两个文件进 ARTIFACTS（因此进 artifact_index 的逐文件哈希）、两条写盘路径都写、
    # manifest 与报告各留指针。
    check("material_pack.md" in ACC.ARTIFACTS and "material_pack.json" in ACC.ARTIFACTS,
          "材料包的人读版与机器读版都在 ARTIFACTS 里")
    # 载荷自带 schema 版本，读者不必为了判读去读源码。`material-pack/2` → `/3`（acc-29）：
    # 资格列多一列（`trailing_content`）且勾选行的形状判据变了，旧读者会把「选项串之后那句」
    # 读成选项标签或正文。`/3` → `/4`（acc-37）：**表对象**不再落进 `content_qualification = None`
    # ——旧读者会把表对象读成 `unavailable`，把「这条信道不归 span 词表管」读成「读失败」。
    # `/4` → `/5`（acc-39）：`reading_policy` 多出 `envelope_kind`——读回侧同时认 `gtm-1`
    # 与 `tom-1` 两条表对象信道，`/4` 的读者会以为只有一条。
    check(ACC.MATERIAL_PACK_SCHEMA_VERSION == "material-pack/5"
          and ok_all["schema_version"] == ACC.MATERIAL_PACK_SCHEMA_VERSION,
          "材料包载荷带自己的 schema 版本")

    # ==================================================================
    # acc-25 ⑤：**内容形态分流**——信封里早就算出的 §二 2.3 形态此前被丢在解析层，
    # 读回产物里因此看不出「这一份是勾选表单行」与「这一份是叙述材料」。
    # ==================================================================
    _FORM_TEXT = "1）已签订的重大采购合同截至本报告期的履行情况 □适用 √不适用"
    _FORM_ENVELOPE = json.dumps({
        "content": {"text": _FORM_TEXT},
        "content_qualification": {
            "kind": "selection_form",
            "selection": {"resolved": True, "unresolved_reason": "",
                          "question": "已签订的重大采购合同截至本报告期的履行情况",
                          "question_scope": "in_span",
                          "options": [{"marker": "□", "marker_state": "hollow", "label": "适用"},
                                      {"marker": "√", "marker_state": "marked", "label": "不适用"}],
                          "selected_labels": ["不适用"]}},
    }).encode("utf-8")
    _form_hash = hashlib.sha256(_FORM_ENVELOPE).hexdigest()

    def _form_state(*, envelope_bytes=_FORM_ENVELOPE):
        return _material_pack_state(
            text_bytes=envelope_bytes, members=((member_key, "current_state_source"),),
            dispositions=(_DISP,),
            body_hash=hashlib.sha256(envelope_bytes).hexdigest())

    form_block = ACC._material_pack_readback(_form_state(), "company")
    form_row = form_block["entries"][0]
    qualification = form_row["content_qualification"]
    check(isinstance(qualification, dict) and qualification["kind"] == "selection_form"
          and qualification["kind_in_vocabulary"] is True,
          "读回把信封里的形态读法带出来（不再丢在解析层）")
    columns = qualification["selection"]
    check(columns["asked_item"] == "已签订的重大采购合同截至本报告期的履行情况"
          and columns["selected_labels"] == ["不适用"]
          and columns["options"][1]["selected"] is True
          and columns["options"][0]["selected"] is False,
          "勾选表单行摊成「所问事项 / 选项 / 选中状态」三列，选中状态逐项分开")
    check(columns["node"]["section_path"] == "（无）" or "section_path" in columns["node"],
          "「所在节点」取自原件 locator 的 section_path（不重排、不折算）")
    check(columns["source"]["document_id"] == "doc-A" and columns["source"]["page"] == 10,
          "「来源」列出原件身份与精确落点")
    check(columns["permitted_use"] == TM.TREE_MATERIAL_SELECTION_PERMITTED_USE
          and "not_a_broad_column_coverage_proof" in columns["exclusions"]
          and "no_negation_beyond_marked_state" in columns["exclusions"],
          "允许用途只有「所问事项的适用性」一条，排除项逐条写明读不出什么")
    check(form_row["text"] == _FORM_TEXT and form_row["text_chars"] == len(_FORM_TEXT),
          "**形态读法不改写原文**：`text` 仍是逐字原文，勾选字形原样保留")
    check(form_block["content_kind_counts"].get("selection_form") == 1
          and form_block["selection_form_material_ids"] == ["m-1"],
          "节级读数另给形态计数与表单行 material_id 一览（不必去正文里认字形）")
    form_md = ACC._material_pack_md_from({"sections": {"company": form_block},
                                          "sections_readback": ["company"]})
    check("selection_form" in form_md and "所问事项" in form_md and "不适用" in form_md,
          "人读版把五列摊在原文之前")

    # 反例一：形态名读不懂时**不降级成 text**（那是替它声称「这是叙述材料」）。
    _bad_kind = json.dumps({"content": {"text": _FORM_TEXT},
                            "content_qualification": {"kind": "not_a_kind"}}).encode("utf-8")
    bad_block = ACC._material_pack_readback(_form_state(envelope_bytes=_bad_kind), "company")
    bad_q = bad_block["entries"][0]["content_qualification"]
    check(bad_q["kind"] == "not_a_kind" and bad_q["kind_in_vocabulary"] is False
          and "不在封闭词表内" in bad_q["kind_basis"],
          "形态名不在封闭词表内时如实记「读不懂」，不降级成叙述材料")

    # 反例二：勾选表单行但状态未判定 → 摊不出五列，且**不**与「本来就不是表单行」混成一句。
    _unresolved = json.dumps({
        "content": {"text": _FORM_TEXT},
        "content_qualification": {"kind": "selection_form",
                                  "selection": {"resolved": False,
                                                "unresolved_reason": "state_not_determinable",
                                                "question": "履行情况", "question_scope": "in_span",
                                                "options": [], "selected_labels": []}},
    }).encode("utf-8")
    un_block = ACC._material_pack_readback(_form_state(envelope_bytes=_unresolved), "company")
    un_q = un_block["entries"][0]["content_qualification"]
    check(un_q["selection"] is None
          and un_q["selection_basis"].startswith("selection_form_unresolved"),
          "读不定的表单行摊不出五列，并区分于「本来就不是表单行」")

    # 反例三：**正文里夹有勾选符号但正文仍有意义**的混合片段，不得被当成表单行。
    mixed = "公司主营业务为动力电池系统的研发、生产及销售，报告期内 □未发生重大变化。"
    mixed_check = TM.classify_span_content(
        SimpleNamespace(normalized_text=mixed), node_title="经营模式")
    check(mixed_check.kind == "text" and mixed_check.is_material is True,
          "正文里夹着勾选符号的混合片段仍按叙述材料读（不误删有效材料）")
    check(TM.selection_form_columns(
        selection={"resolved": False, "options": [], "selected_labels": []}) is None,
          "未判定的表单行不产出五列（它连材料都不是）")
    check(_RUNNER_SRC.count('state.run_dir / "material_pack.json"') == 1
          and _RUNNER_SRC.count('run_dir / "material_pack.json"') >= 1,
          "正常路径与拒绝路径各写一次材料包（整轮被拒时同样有「取到了什么」可看）")
    check('"material_pack": {' in _RUNNER_SRC
          and _RUNNER_SRC.count('"material_pack": {') >= 2,
          "`manifest.json` 与 `acceptance_report.json` 各留一个 `material_pack` 指针块")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

"""Eval: M930-3 批次四 §1 —— r5 真实失败响应的**离线复演**（replay ≠ pass）。

用法: python -m evals.test_m930_3_r5_financial_replay

## 为什么要有这个模块

r5（`m930-3-acc-27`）的 financial 节**死在门后的一条补件申请上**，整节消失：

  * `proposal_set_rejections.json`：`sections['financial']` = **0 条** typed 拒绝记录
    （company 2 条、industry 2 条），而 `sections_failed['financial']` 是一条
    `PackWriterError: follow_up_need「本节未取得…」 的 aspect_id='fin_statements_availability'
     不在 topic 'fin_source_scope' 的 Contract 投影内`；
  * `follow_up_needs.json`：`sections` 里**只有** `industry`（8 条），financial 的 2 条诉求
    连「不成立」的记录都没有——它们是随异常一起蒸发的；
  * `section_drafts.json` 是空产物：那一轮解析出来的 24 条候选与 1 个草稿单元，
    没有任何一节记录留档。

本模块把**同一批真实字节**（`logs/llm/…__d2b8a776….jsonl` 里 financial 那次 narration
的原始返回）拿回来，在当前代码上重放一遍，回答三件事：

  1. 当前的解析器对同一批字节仍然逐字保留 24 / 1 / 2（§2）；
  2. financial 权威的读视图**即便有必用事实**，其 aspect 投影面与 requirement id 也是**空的**
     ——这不是「本节没材料」，而是这条权威侧没有 Contract 投影（§3）；
  3. 同一批 2 条真实诉求落在该读视图上，现在是**逐条 typed 拒绝、0 条成立、且不再抛异常**
     （§4）；旧的全有或全无入口在同一次调用上仍然抛，且抛出的消息与 r5 记录**逐字相同**
     （§5）——「不成立的申请杀不掉整节」是**后果范围**的改变，不是判据被放宽。

## replay ≠ pass（本文件明确不主张的事）

复演证明的是**失败形状**被替换，不是本节已经产出内容。它不重放绑定门、不重放事实回查、
不重放表格与正文渲染：那需要 r5 当轮的权威目录（artifact 内容、Pack 材料并集），而
`manifest.json` / `follow_up_needs.json` 里没有逐条记录，凭产物重建就是发明。
因此本模块**不断言**「financial 节现在会通过」，也不把任何一条诉求读成已裁决、已执行或已满足；
它只断言：同一批字节下，**杀死整节的那条路径不再存在**（§5 把它逐字复现出来作对照），
而它替换成了什么（§4 的逐条记录）可以逐条复核。整节是否成形要用一次新的 create-only run 回答。

数据依赖：`logs/llm/…__d2b8a776….jsonl` 与 `evaluation/results/m930_3_acceptance_crossdoc_real_r5/`。
两者都不在版本控制里（生成结果、日志不得提交），缺失时本模块 **typed skip**，不静默通过。
"""

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import schema as PS
from sections import pack_writer as PW

#: 夹具（真实 `FinancialPackArtifact` / `PlannedQuestion` 的合成工厂）复用既有反例模块，
#: 不在这里另搭一套——两份 financial 权威替身一旦分叉，「复演的是同一条权威形状」就不成立。
#: 该模块顶层只有常量与函数定义，导入它不执行任何用例。
from evals import test_demo_pack_writer as FIX  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R5_DIR = ROOT / "evaluation" / "results" / "m930_3_acceptance_crossdoc_real_r5"
#: r5 里 financial 节**被采信**的那次 narration 调用（`finish_reason == "end_turn"`）。
RAW_CALL_ID = "d2b8a7768d2e40c6a3ae5c21ab43a84c"
RAW_LOG = ROOT / "logs" / "llm" / f"20260925T160619820372__{RAW_CALL_ID}.jsonl"

#: r5 记录下来的整节失败文本（`sections_failed['financial']`）。它只用于**对账**：
#: 用例从产物里读它，再与本模块自己抛出的消息比对，不把它当判据、也不据它写代码分支。
RECORDED_FAILURE_PREFIX = "PackWriterError: "

#: 本模块读的是 `proposals-10` 形态的**留存字节**：r5 那一轮的返回里根本没有草稿层（`pw-15`
#: 之后才有这一层，`pw-16` 起「有候选、无草稿」在当前线上是 typed failure）。按指令，旧线只能经
#: **显式标注**的兼容路径读回，因此这里把线格式**声明出来**、解析模式从它派生，而不是在调用点
#: 手写一个布尔开关——否则「这次复演读的是哪条线」就只存在于调用点的一个隐式约定里。
#: 历史线只在离线 stand-in 下可用（见 `WriterPolicy.__post_init__`），本模块不调 LLM。
LEGACY_REPLAY_POLICY = PW.WriterPolicy(proposal_wire="proposals-10")

#: §3 的读视图真值表：**同一批真实诉求**（读不到第二个变量）在不同的读视图上得到的原因码。
#: 6 种现场覆盖 topic / question / aspect / requirement 四个门与唯一一个「成立」，
#: 用来证明这些原因码是**从读视图派生**出来的，而不是一段恒定输出。
#:
#: `authorized_scope_empty` 不在表里，且这不是遗漏：门 3/4 通过意味着
#: `scan.aspect_topic[aspect] == topic_id` 且 `scan.aspect_question[aspect] == question_id`
#: 都非空，而 `_authorized_scope_of` 正是拿这两个值加 aspect id 拼授权范围——因此该码在前
#: 四个门通过时**不可达**。它在封闭词表里存在（§3 单独钉住这一条），本文件不为了凑一个
#: 反例去造一个 topic id 是空串的 Contract（真实 Contract 没有这种形状）。
VARIANT_CODES = {
    "real": ("aspect_not_in_topic", "aspect_not_in_topic"),
    "no_question": ("question_not_under_topic", "question_not_under_topic"),
    "no_topic": ("topic_not_in_section", "topic_not_in_section"),
    "control_requirement": ("target_requirement_mismatch", "target_requirement_mismatch"),
}

#: 复演用的章节形状：两个 topic 与 r5 请求面**逐字相同**（`task.topic_ids` 在 r5 的请求
#: payload 里是 `["fin_source_scope", "fin_solvency"]`）。
SECTION_ID = "financial"
TOPICS = ("fin_source_scope", "fin_solvency")
#: 两个 aspect id 逐字取自那两条真实诉求（§2 断言它们与原始返回一致，不是这里手写的值）。
ASPECTS = ("fin_statements_availability", "fin_audit_opinion")


def _read_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _question(question_id: str, topic_id: str) -> PS.PlannedQuestion:
    return PS.PlannedQuestion(
        question_id=question_id, question=f"{question_id} 的问题？", priority="required",
        topic_id=topic_id, required_aspects=(), impact_scope=("subject",))


def _financial_case():
    """r5 financial 的**最小可复演形状**：真实 artifact 形状 + 两条真实诉求的四元组坐标。

    两处**重建**（都不冒充已恢复的事实，逐条说明）：

      * `task.questions` —— r5 的 `task` 没有落进任何产物（`manifest.json` 里只剩那条失败消息），
        因此问题目录是按「记录下来的失败必须在此处再现」重建的：r5 记录下来的那条消息是
        **aspect 门**（门序 topic → question → aspect），所以那一轮 topic 与 question 两门已过，
        本模块据此把 `ASPECTS` 的 id 同时声明为 `fin_source_scope` 下的问题 id。这一重建只影响
        「先在哪个门被拒」，不影响结论：§3 的真值表把问题目录也当作一个变量跑了一遍。
      * `fact_topic_map` —— 多 topic 的财务权威必须显式给出事实归属（不得发明），这里给唯一
        一条事实一条真实归属。
    """
    questions = tuple(_question(aspect_id, "fin_source_scope") for aspect_id in ASPECTS) + (
        _question("q-fin_solvency", "fin_solvency"),)
    task = FIX._task(SECTION_ID, TOPICS, task_id="task-fin-r5-replay", questions=questions,
                     title="财务分析")
    citation = {"ref_type": "evidence", "evidence_id": FIX.EV_ID, "page_number": 12}
    artifact = FIX._Artifact(
        task_id=task.task_id,
        facts=(FIX._FinFact(fact_id="ff-short", label="短期偿债能力",
                            display="流动比率为 1.20 倍。", period=FIX.REPORT_AS_OF,
                            unit="倍", citation=citation),))
    authority = FIX._financial_authority(
        task, artifact, fact_topic_map=(("ff-short", "fin_solvency"),),
        note_gap=FIX._NoteGap(task_id=task.task_id))
    return task, authority


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

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = ""):
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:200]}）")
            else:
                passed += 1
            return e
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:200]}）")
            return None
        failed += 1
        details.append(f"FAIL {msg}：未拒绝")
        return None

    # ============================================================ §0 数据依赖：缺就 typed skip
    if not RAW_LOG.exists() or not R5_DIR.is_dir():
        skip(f"缺少 r5 原始调用日志或产物目录（{RAW_LOG.name} / {R5_DIR.name}）："
             "复演需要真实运行的字节与记录，二者都不在版本控制里")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    recorded = _read_json(R5_DIR / "proposal_set_rejections.json")
    needs_doc = _read_json(R5_DIR / "follow_up_needs.json")
    raw_row = json.loads(RAW_LOG.read_text(encoding="utf-8").strip())

    # ============================================================ §1 记录侧事实（从产物读回）
    # 这一节把 r5 的**失败形状**钉死在产物上：先证明「整节死了」，后面的复演才有意义。
    check_eq(raw_row.get("call_id"), RAW_CALL_ID, "原始日志必须是 financial 那次被采信的调用")
    check_eq(raw_row.get("finish_reason"), "end_turn",
             "那次调用是正常结束（不是被截断的那几次）")
    sections_failed = recorded.get("sections_failed") or {}
    recorded_fin = str(sections_failed.get(SECTION_ID) or "")
    check(recorded_fin.startswith(RECORDED_FAILURE_PREFIX),
          "r5 记录下来的 financial 失败是一条 Writer 侧异常（而不是候选审计）")
    check("aspect_id='fin_statements_availability'" in recorded_fin
          and "'fin_source_scope'" in recorded_fin and "Contract 投影内" in recorded_fin,
          "记录的失败文本指向 aspect 门的投影判定（topic/aspect 两个 id 都在文本里）")
    check_eq(len(((recorded.get("sections") or {}).get(SECTION_ID)) or []), 0,
             "r5 的 financial **没有**留下任何逐束 typed 拒绝记录（company 2 / industry 2）")
    check_eq(recorded.get("sections_completed"), [],
             "r5 没有任何一节完成（复演不得据此宣称通过）")
    check(SECTION_ID not in (needs_doc.get("sections") or {}),
          "r5 的 financial 诉求连「不成立」都没留档——`follow_up_needs.json` 里只有 industry")
    check_eq(needs_doc.get("total_pending_needs"), 8,
             "记录侧只有 industry 那 8 条诉求进入待裁决")
    check_eq(_read_json(R5_DIR / "section_drafts.json"), {},
             "r5 的 `section_drafts.json` 是空产物：那一轮的候选与草稿单元没有任何留档")

    # ============================================================ §2 同一批字节，当前解析器
    bundle = PW.parse_writer_proposals(
        raw_row["completion"],
        require_natural_draft=LEGACY_REPLAY_POLICY.requires_natural_draft())
    raw_payload = json.loads(raw_row["completion"])
    check_eq(len(bundle["claim_candidates"]), 24, "同一批字节解析出的候选条数不变")
    check_eq(len(bundle["narrative_draft_units"]), 1, "同一批字节解析出的草稿单元数不变")
    check_eq(len(bundle["follow_up_needs"]), 2, "同一批字节解析出的补件申请数不变")
    # 复演不得被读成「新版本真实成功」：这批字节是旧线形态，自然草稿层如实为空。若哪一天这里
    # 解析出非空草稿，那只能说明解析器替旧记录编了一段草稿——那是伪造，不是修复。
    check_eq(bundle["natural_prose_draft"], [],
             "留存字节是 `proposals-10` 形态：自然草稿层必须如实为空")
    check(all(bundle["claim_candidates"][i]["claim_text"]
              == str(raw_payload["claim_candidates"][i]["claim_text"]).strip()
              for i in range(len(bundle["claim_candidates"]))),
          "候选文本逐字保留（解析只做去空白，不改写内容）")
    check(all(edge["authority_kind"] == "financial_pack"
              and edge["authorization_path"] == "path_a_prevalidated"
              for candidate in bundle["claim_candidates"] for edge in candidate["support"]),
          "那一轮 24 条候选的支撑边全部是财务权威的路径 A（与本次理赔的范围一致）")
    specs = bundle["follow_up_needs"]
    check_eq(tuple(str(s["aspect_id"]) for s in specs), ASPECTS,
             "复演用的两个 aspect id 必须逐字来自那两条真实诉求，不是手写的")
    check(all(str(s["topic_id"]) == "fin_source_scope" for s in specs),
          "两条真实诉求都挂在 `fin_source_scope` 上")

    task, authority = _financial_case()
    scan = PW.scan_financial(authority, task)
    policy = PW.WriterPolicy()

    # ============================================================ §3 真实读视图：有事实，无投影
    check_eq(scan.facts[0].required, True,
             "本节**有**必用事实（否则「读视图为空」会被误读成「本节根本没材料」）")
    check_eq(scan.facts[0].topic_id, "fin_solvency",
             "必用事实归属本节某个 topic（财务事实的 topic 只能由显式映射给出）")
    check_eq(scan.topics_without_facts, ("fin_source_scope",),
             "另一 topic 一条事实都没有——模型申请它并不是无的放矢")
    check_eq(PW._requestable_aspects(scan, task), [],
             "该读视图下可申请的 aspect 目录为空（目录只收录能通过全部校验的行）")
    check_eq(dict(scan.aspect_topic), {}, "财务权威侧没有 Contract 投影：aspect → topic 为空")
    check_eq(dict(scan.aspect_question), {}, "aspect → question 为空")
    check_eq(dict(scan.aspect_status), {}, "aspect 状态为空")
    check_eq(dict(scan.requirement_ids), {},
             "topic → requirement id 为空：财务权威不携带 Contract requirement 对象")

    # 四条门各有一条原因码：用**同一批真实诉求**跑一遍真值表。读视图是唯一变量。
    def _run(scan_obj, task_obj, authority_obj):
        return PW._follow_up_needs_partition(
            specs=specs, task=task_obj, authority=authority_obj, scan=scan_obj,
            draft_revision="sdrev_replay", policy=policy, attempt=1)

    needs, rejections = _run(scan, task, authority)
    check_eq((len(needs), tuple(r["code"] for r in rejections)), (0, VARIANT_CODES["real"]),
             "真实读视图：2 条诉求全部逐条拒绝，原因码是 aspect 门（与 r5 记录一致）")
    check_eq(tuple(r["spec_index"] for r in rejections), (0, 1),
             "逐条记录带上它在**自己那一份响应**里的序号（谁填错了可定位）")
    check(all(r["reason"] and r["statement"] and r["attempt"] == 1 for r in rejections),
          "每条记录都有可读原因、原诉求文本与所属轮次（不是只有计数）")
    check("authorized_scope_empty" in PW.FOLLOW_UP_REJECTION_CODES,
          "`authorized_scope_empty` 在封闭词表里存在（前四门通过时它不可达，见模块 docstring）")

    # 问题目录也是变量：拿掉重建出来的那两个问题，失败前移到 question 门。
    no_question_task = FIX._task(SECTION_ID, TOPICS, task_id=task.task_id,
                                 questions=task.questions[2:], title=task.title)
    _, rej_no_question = _run(scan, no_question_task, authority)
    check_eq(tuple(r["code"] for r in rej_no_question), VARIANT_CODES["no_question"],
             "没有该问题：原因码前移到 question 门（说明原因码来自门序，不是常量）")

    # 权威主题集也是变量：本节只声明另一个 topic 时，失败前移到 topic 门。
    solo_task = FIX._task(SECTION_ID, ("fin_solvency",), task_id=task.task_id,
                          questions=task.questions, title=task.title)
    solo_authority = FIX._financial_authority(
        solo_task, authority.artifact, fact_topic_map=(("ff-short", "fin_solvency"),),
        note_gap=FIX._NoteGap(task_id=solo_task.task_id))
    _, rej_no_topic = _run(scan, task, solo_authority)
    check_eq(tuple(r["code"] for r in rej_no_topic), VARIANT_CODES["no_topic"],
             "本节不含该 topic：原因码前移到 topic 门")

    # **正例面**：把投影面补齐到「该 topic 真的声明了这个 requirement id」，同一批字节就能
    # 成立——证明拒绝来自读视图，而不是这两条诉求或这个函数本身。
    filled = dict(
        aspect_status={a: "not_covered" for a in ASPECTS},
        aspect_topic={a: "fin_source_scope" for a in ASPECTS},
        aspect_question={a: a for a in ASPECTS},
        aspect_impact={a: ("subject",) for a in ASPECTS})
    control_scan = dataclasses.replace(
        scan, **filled,
        requirement_ids={"fin_source_scope": "replay::fin_source_scope",
                         "fin_solvency": "replay::fin_solvency"})
    _, rej_control = _run(control_scan, task, authority)
    check_eq(tuple(r["code"] for r in rej_control), VARIANT_CODES["control_requirement"],
             "投影补齐但 requirement id 对不上：原因码落在 requirement 门（第四门可达）")

    full_scan = dataclasses.replace(
        scan, **filled,
        requirement_ids={"fin_source_scope": ASPECTS[0], "fin_solvency": "replay::fin_solvency"})
    accepted, rej_full = _run(full_scan, task, authority)
    check_eq(len(accepted), 1, "读视图补齐到与第一条诉求一致时，该条**成立**（正例面）")
    check(accepted[0].aspect_id == ASPECTS[0] and accepted[0].topic_id == "fin_source_scope"
          and accepted[0].target_requirement_id == ASPECTS[0],
          "成立的诉求逐字带上它在读视图里的三个 id")
    check_eq(tuple(accepted[0].contract_authorized_scope),
             ("fin_source_scope", ASPECTS[0], "subject"),
             "授权范围取自同一份投影（aspect/topic/question + impact_scope，升序去重）")
    check_eq(tuple(r["code"] for r in rej_full), ("target_requirement_mismatch",),
             "同一 topic 下第二条诉求仍不成立：一个 topic 只有一个 requirement id，"
             "两条诉求不可能同时回指同一个它——这一条是模型侧的错，不是链路的错")
    check(all(code not in ("", None) for code in
              [r["code"] for r in rejections + rej_no_question + rej_no_topic
               + rej_control + rej_full]),
          "全部拒绝记录都带封闭原因码（没有靠消息措辞的形态）")

    # ============================================================ §4 旧入口：r5 的记录逐字复现
    # 「不成立的申请杀不掉整节」是**后果范围**的改变。同一次调用走旧的全有或全无入口仍然抛，
    # 并且抛出的消息与 r5 产物里那条失败文本**逐字相同**——判据没有被放宽，被换掉的只是后果。
    old_error = expect_error(
        lambda: PW._follow_up_needs(specs=specs, task=task, authority=authority, scan=scan,
                                    draft_revision="sdrev_replay", policy=policy),
        PW.PackWriterError, "旧入口在同一次调用上仍然抛出", needle=ASPECTS[0])
    if old_error is not None:
        old_text = str(old_error)
        recorded_body = recorded_fin[len(RECORDED_FAILURE_PREFIX):]
        check(recorded_body and old_text.startswith(recorded_body),
              "旧的抛错文本与 r5 记录下来的失败文本逐字相同（产物对账，不是相似）")
        check(isinstance(old_error, PW.FollowUpNeedRejected)
              and old_error.code == VARIANT_CODES["real"][0]
              and old_error.spec_index == 0,
              "同一个异常现在带封闭原因码与它在响应里的序号（记录侧不再只有一段文本）")
        check(isinstance(old_error, PW.PackWriterError),
              "它仍然继承 PackWriterError：既有的 `except PackWriterError` 全部接得住")

    # ============================================================ §5 复演不宣称的事
    # 这一节是**反向自证**：本模块的结论里没有任何一条能读成「本节已经产出内容」。
    check_eq(len(needs), 0,
             "真实读视图上成立的诉求是 0 条：复演没有把任何诉求读成已满足")
    # 键集是**四类**（`pw-15` / 指令 E 第 3 项加了门前自然草稿这一路），仍然封闭：
    # 这四类里没有任何完成态/决定字段可被误读成通过。
    check_eq(sorted(bundle),
             ["claim_candidates", "follow_up_needs", "narrative_draft_units",
              "natural_prose_draft"],
             "解析结果只有候选/草稿单元/诉求/自然草稿四类键，没有任何完成态字段可被误读成通过")
    check_eq(recorded.get("sections_completed"), [],
             "记录侧至今仍是 0 节完成：整节是否成形必须由新的 create-only run 回答")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

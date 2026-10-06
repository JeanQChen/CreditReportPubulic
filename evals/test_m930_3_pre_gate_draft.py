"""M930-3 指令 D §三 反例集：整束被拒时**门前草稿的只读留存**（`pgr-1`）与失败侧诊断。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_pre_gate_draft`

对应缺陷（真实 run r8 的 company 节）：模型分四批被问，前三批**各自写出了内容**，第四批的返回
结构不合法（自然草稿单元没声明任何出处轴）。整轮是**全有或全无**的——`draft=None`，于是前三批
已经合法解析、已经写出正文的那部分**随异常一起蒸发**：产物里只留下一条属于**失败那一批**的
`draft_unit_ids`，而「这一节到底写出过什么」在 r8 的产物里读不出来（LLM 账本只有元数据，
没有响应正文，事后也**不可恢复**）。修法不是「别失败」（那等于放宽门），而是**失败时留下一份
不改判任何门的只读留档**。

本文件钉住的是这条链上**可证伪**的那几件事（不跑真实文档、不调真实网络、不写库）：

  A. **词表**：留存口径 `pgr-1`、三档 `basis`、逐字标注「未核验、不可发布」、门前诊断的四档
     门口状态与产物形状版本——都只有**一处**定义，读者面与写手面共用同一个串。
  B. **留存的两条来源按可用性择优**（`merged_bundle` / `per_batch_before_failure` / `none`），
     且 `to_dict()` 可 JSON 往返（它要落盘，tuple 会在往返后变成 list）。
  C. **不变量**：「无可留存」（`none` + 空表）与「有留存」（非 none + 非空表）不得互相冒充；
     留存行必须有可读 label；留存里的草稿单元 `text` 不得为空。
  D. **r8 同形端到端（本文件的主用例）**：8 批的形状（85 个 Contract aspect / 单批上限 12），
     前 7 批合法解析、第 8 批两次都不合法 ⇒ 整轮被拒，**前 7 批逐批的正文逐字留在拒绝记录里**，
     且**没有**因此产生任何候选身份、没有 `SectionDraft`、`whole_set_rejected` 仍为真。
     「留内容」与「造身份」是两件事——这正是本条要证的那条边界。
  E. **无合法候选 ⇒ 不留存**（反例）：第一批就失败 ⇒ `basis="none"` + 空表。不得为了让诊断页
     好看，凭空造一段没有出处的草稿冒充「门前写过的东西」。
  F. **读者面（runner）**：四档门口状态各自可达、互不冒充（尤其 `unavailable` ≠
     `nothing_retained`）；两个渲染（`.json` / `.md`）出自同一次读数、都逐字带标注；产物登记、
     写盘与 manifest 指针齐备；**诊断不喂正文**——它只有一处调用点，且那条调用点在 `_write_run`。

夹具复用 `test_demo_pack_writer` / `test_demo_writer_batching` 的替身（两套替身各自漂移会让
「测试里过的链」与「真实链」不是同一条）。本文件不调真实 LLM、不联网、不读真实库。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from evals import test_demo_pack_writer as FIXT
from evals import test_demo_writer_batching as BATCH
from sections import pack_writer as PW

REPO = Path(__file__).resolve().parent.parent
RUNNER_SRC = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")

#: 本节用的权威事实文本。**不带任何高风险表面**（数值 / 期间 / 否定 / 勾选 / 因果），
#: 因此这份夹具只测「留存」这一件事，不把路径 B 的授权判据混进来。
_FACT_TEXT = "公司主营业务为动力电池系统的研发与销售。"

#: 逐批各写一句**彼此不同**的门前草稿。刻意逐批不同：只留一段共享文本的话，
#: 「逐批坐标 + 逐批正文」这条对账在本用例的形状上也能全绿——那就证不出留存是**逐批**的。
_BATCH_TEXTS = (
    "公司主营业务为动力电池系统的研发与销售。",
    "公司主要产品包括动力电池系统与储能系统。",
    "公司的销售模式为直销方式。",
    "公司生产环节由自有产线承担。",
    "公司采购的主要原材料为正极材料。",
    "公司的客户主要为境内外整车企业。",
    "公司在多地设有生产基地。",
)


class _NS:
    """只读命名空间替身（产物函数只按属性名读取容器，不要求真 Pack/真库对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _state(*, sections: tuple = (), rejections: dict | None = None,
           errors: dict | None = None, tasks: object = None) -> ACC.RunState:
    inputs = _NS(tasks=({} if tasks is None else tasks))
    state = ACC.RunState(inputs=inputs, run_dir=REPO, mode=ACC.MODE_OFFLINE,
                         generated_at="2026-09-27T00:00:00Z", policy=_NS())
    state.sections = {s: object() for s in sections}
    state.proposal_set_rejections = dict(rejections or {})
    state.section_errors = dict(errors or {})
    return state


def _tasks(titles: dict) -> dict:
    return {sid: _NS(title=title) for sid, title in titles.items()}


def _retained(*, basis: str = "per_batch_before_failure", batches: tuple) -> dict:
    """一份**真实**留存对象渲染出来的载荷（与生产同一 `to_dict()`，不手写字段）。"""
    return PW.RetainedPreGateDraft(basis=basis, batches=batches).to_dict()


def _retained_row(*, label: str = "1/2", batch_id: str = "b-1", aspects: tuple = ("a-1",),
                  texts: tuple = ("门前草稿正文一。",), unit_prefix: str = "pu") -> dict:
    return {"batch_id": batch_id, "label": label, "aspect_ids": list(aspects),
            "prose_units": [{"prose_unit_id": f"{unit_prefix}-{i + 1}", "index": i + 1,
                             "text": text, "source_member_refs": [f"m-{i + 1}"],
                             "source_fact_refs": [], "atom_candidate_ids": [f"c-{i + 1}"]}
                            for i, text in enumerate(texts)],
            "candidate_labels": ["c1"], "unit_labels": ["u1"], "follow_up_total": 0}


def _rejection_row(*, attempt: int = 1, kind: str = "schema_invalid",
                   detail: str = "第 8 批的返回结构不合法（示例原因）。",
                   retained: dict | None = None, batches: tuple = ()) -> dict:
    return {"attempt": attempt, "rejection_kind": kind, "rejection_detail": detail,
            "batches": list(batches), "retained_pre_gate": retained}


def _batch_call(*, label: str = "1/2", status: str = "ok", aspects: tuple = ("a-1",)) -> dict:
    """一条逐批调用读数。`status` 取**真实词表**（`ok` / `truncated` / `error`）——
    「这一次调用怎么样」，不是「这一次返回有没有通过解析」；后者写在轮级 `rejection_kind`
    与 `rejection_detail` 里，两者不得混成一格。"""
    return {"batch_id": f"b-{label}", "label": label, "status": status,
            "aspect_ids": list(aspects)}


def _company_case(aspect_ids: tuple, task_id: str) -> dict:
    """一个 company 节的权威输入：`aspect_ids` 个 aspect + 一条覆盖它们全部的事实。

    与 `test_demo_writer_batching.company_case` 同形，差别只在这里传的是**整份**投影的 aspect
    列表（85 个 ⇒ 8 批），因此本用例的批数是投影自己决定的，不是靠参数凑出来的。
    """
    task = FIXT._task("company", (FIXT.TOPIC_BUSINESS,), task_id=task_id)
    facts = (FIXT._fact("f-1", _FACT_TEXT, tuple(aspect_ids)),)
    pack_set = FIXT._pack_set(
        task, facts=facts,
        aspect_results=tuple(FIXT._AspectResult(a, "covered") for a in aspect_ids),
        requirements=(FIXT._Req(
            FIXT.TOPIC_BUSINESS,
            tuple(FIXT._aspect(a, FIXT.TOPIC_BUSINESS, "q-company_business")
                  for a in aspect_ids)),))
    authority = PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=FIXT.COMPANY_ID, report_as_of=FIXT.REPORT_AS_OF,
        contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    scan = PW.scan_topic_pack(authority, task)
    return {"task": task, "authority": authority, "scan": scan,
            "entry": {e.fact_id: e for e in scan.facts}["f-1"],
            "material_context": FIXT._writer_material_context(
                pack_set, task_id=str(task.task_id), section_id="company")}


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

    # ==================================================================
    # A. 词表：口径版本、三档 basis、逐字标注、四档门口状态、产物版本
    # ==================================================================
    check(PW.PRE_GATE_RETENTION_VERSION == "pgr-1",
          f"留存口径必须有版本号（实为 {PW.PRE_GATE_RETENTION_VERSION!r}）："
          "留存的**读法**（哪几档来源、哪些字段、怎么读）会随批次坐标的可考程度变，"
          "没有号就无法判断一份历史产物是按哪一版读的")
    check(PW.PRE_GATE_RETENTION_BASES == ("merged_bundle", "per_batch_before_failure", "none"),
          f"三档来源必须封闭且有序（实为 {PW.PRE_GATE_RETENTION_BASES!r}）："
          "「本轮各批全部成功并已合并」（逐批归属不可考）/「其余批已解析、某批失败」"
          "（逐批可考）/「本轮无任何一批交出合法返回」——三者的可读结论不同，不得合并成一档")
    check(PW.PRE_GATE_RETENTION_EMPTY_BASIS == "none"
          and ACC.PRE_GATE_EMPTY_BASIS == PW.PRE_GATE_RETENTION_EMPTY_BASIS
          and PW.PRE_GATE_RETENTION_EMPTY_BASIS in PW.PRE_GATE_RETENTION_BASES,
          f"「本轮无一批交出合法返回」这一档的来源码必须**逐字**同源（实为 "
          f"{PW.PRE_GATE_RETENTION_EMPTY_BASIS!r} / {ACC.PRE_GATE_EMPTY_BASIS!r}）："
          "它是非空字符串，判据只判真假就会把「门前没有可留的东西」印成"
          "「留了一段未核验的内容」——两侧各写一个字面量迟早会漂移")
    check(PW.PRE_GATE_RETENTION_LABEL == "未核验、不可发布"
          and ACC._PRE_GATE_LABEL == PW.PRE_GATE_RETENTION_LABEL,
          f"逐字标注只有一处定义、读者面与写手面共用同一个串（实为 "
          f"{PW.PRE_GATE_RETENTION_LABEL!r} / {ACC._PRE_GATE_LABEL!r}）："
          "两处各写一句措辞，读者面就会与产物里的标注漂移")
    check(ACC.PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION == "m930-3-pre-gate-draft-1",
          f"门前诊断的载荷形状版本必须钉住（实为 "
          f"{ACC.PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION!r}）")
    check(ACC.PRE_GATE_DRAFT_SECTION_STATUSES == (
        "readback", "retained_unvetted", "nothing_retained", "unavailable"),
        f"门口状态必须封闭且四者互不冒充（实为 {ACC.PRE_GATE_DRAFT_SECTION_STATUSES!r}）："
        "「被采信了」「留了未核验的内容」「本来就没有可留的」「读不回来」")
    check(RUNNER_SRC.count('_PRE_GATE_LABEL = "未核验、不可发布"') == 1,
          "标注必须由**一个**常量给出；渲染处逐字再写一遍就会让两处措辞各自漂移")

    # ==================================================================
    # B. 留存的两条来源按可用性择优 + 可 JSON 往返
    # ==================================================================
    # B1. 没有任何来源 ⇒ `none` + 空表（不是「留了个空的」）。
    empty = PW._retained_pre_gate(source=None, batch_plans=())
    check(empty.basis == "none" and empty.batches == (),
          f"两个来源都没有时必须如实记 `none` + 空表（实为 {empty.basis!r} / "
          f"{len(empty.batches)} 行）")

    # B2. 逐批的合法返回 ⇒ `per_batch_before_failure`，逐批带**自己**的坐标与正文。
    plan_a = {"natural_prose_draft": [{"prose_key": "p1", "text": "A 批写出的正文。",
                                       "source_member_refs": ["m1"], "source_fact_refs": [],
                                       "atom_candidate_keys": ["c1"]}],
              "claim_candidates": [{"candidate_key": "c1"}],
              "narrative_draft_units": [{"unit_key": "u1"}],
              "follow_up_needs": [{"statement": "需要补件一。"}]}
    plan_b = {"natural_prose_draft": [{"prose_key": "p2", "text": "B 批写出的正文。",
                                       "source_member_refs": ["m2"], "source_fact_refs": [],
                                       "atom_candidate_keys": ["c2"]}],
              "claim_candidates": [{"candidate_key": "c2"}],
              "narrative_draft_units": [{"unit_key": "u2"}],
              "follow_up_needs": [{"statement": "需要补件二。"}, {"statement": "需要补件三。"}]}
    batch_a = PW.WriterPlanBatch(batch_id="b-a", index=1, total=3, aspect_ids=("a-1", "a-2"))
    batch_b = PW.WriterPlanBatch(batch_id="b-b", index=2, total=3, aspect_ids=("a-3",))
    kept = PW._retained_pre_gate(source=None, batch_plans=((batch_a, plan_a), (batch_b, plan_b)))
    check(kept.basis == "per_batch_before_failure" and len(kept.batches) == 2,
          f"逐批返回合法时必须按批留存（实为 {kept.basis!r} / {len(kept.batches)} 行）")
    check([r["label"] for r in kept.batches] == ["1/3", "2/3"]
          and [r["batch_id"] for r in kept.batches] == ["b-a", "b-b"],
          f"每一行必须带**那一批自己的**坐标（实为 "
          f"{[(r['batch_id'], r['label']) for r in kept.batches]}）")
    check([r["prose_units"][0]["text"] for r in kept.batches]
          == ["A 批写出的正文。", "B 批写出的正文。"],
          "逐批的草稿正文必须逐字留在**它自己**那一行里（不得并成一段）")
    check([r["candidate_labels"] for r in kept.batches] == [["c1"], ["c2"]]
          and [r["unit_labels"] for r in kept.batches] == [["u1"], ["u2"]]
          and [r["follow_up_total"] for r in kept.batches] == [1, 2],
          "逐批的候选 / 草稿单元 / 补件计数必须各自留档："
          "「这一批写了几条、提过几件补件」是逐批的读数，不是整轮的合计")

    # B3. 合并后的束 ⇒ `merged_bundle`，且如实写「逐批归属已不可考」，不编一个批次坐标。
    bundle_stub = _NS(prose_units=(
        _NS(prose_unit_id="pu-1", index=1, text="合并后束里的正文。",
            source_member_refs=("m1",), source_fact_refs=(), atom_candidate_ids=("c1",)),),
        candidates=(_NS(candidate_id="cand-1"),),
        units=(_NS(draft_unit_id="du-1"),),
        follow_up_specs=({"statement": "需要补件。"},))
    merged = PW._retained_pre_gate(source=bundle_stub, batch_plans=())
    check(merged.basis == "merged_bundle" and len(merged.batches) == 1
          and merged.batches[0]["batch_id"] is None
          and merged.batches[0]["label"] == "（本轮合并后的完整提案集）",
          f"有合并束时必须用它并**如实**写「逐批归属不可考」（实为 {merged.basis!r} / "
          f"{merged.batches[0]['label'] if merged.batches else None!r}）："
          "编一个批次坐标会让读者以为这一行是某一批的原样返回")
    check(merged.batches[0]["candidate_labels"] == ["cand-1"]
          and merged.batches[0]["unit_labels"] == ["du-1"]
          and merged.batches[0]["follow_up_total"] == 1,
          "合并束那一行必须给出**合并后**的身份标签与补件计数")

    # B4. 落盘往返：这份载荷要进 JSON，tuple 会在往返后变成 list。
    for label, obj in (("per_batch_before_failure", kept), ("merged_bundle", merged),
                       ("none", empty)):
        payload = obj.to_dict()
        check(json.loads(json.dumps(payload, ensure_ascii=False)) == payload,
              f"`to_dict()` 必须可 JSON 往返（{label}）："
              "落盘后与内存里读出来不是同一个形状，reader 就会把「有 2 行」读成「有 2 项」"
              "之外的东西")
        check(payload["version"] == PW.PRE_GATE_RETENTION_VERSION
              and payload["label"] == PW.PRE_GATE_RETENTION_LABEL
              and "未被采信" in payload["note"] and "不改变这次拒绝" in payload["note"],
              f"载荷必须带口径版本、逐字标注，并在同一份 note 里说清「它不改变这次拒绝」"
              f"（{label}）")
    check(kept.to_dict()["prose_unit_total"] == 2 and merged.to_dict()["prose_unit_total"] == 1
          and empty.to_dict()["prose_unit_total"] == 0,
          "草稿单元合计必须逐档可复核（读者据它判断「这一轮到底写出过几段」）")

    # ==================================================================
    # C. 不变量：两件事不得互相冒充
    # ==================================================================
    expect_error(lambda: PW.RetainedPreGateDraft(basis="unknown", batches=()),
                 PW.PackWriterError, "未知的 basis 必须被拒", needle="不在")
    expect_error(lambda: PW.RetainedPreGateDraft(
        basis="none", batches=(_retained_row(),)),
        PW.PackWriterError,
        "`none` 与**非空**留存表不得共存（空表冒充「有留存」的反向也要拒）",
        needle="不一致")
    expect_error(lambda: PW.RetainedPreGateDraft(basis="merged_bundle", batches=()),
                 PW.PackWriterError,
                 "`merged_bundle` 与**空**表不得共存（「无可留存」不得冒充「有留存」）",
                 needle="不一致")
    expect_error(lambda: PW.RetainedPreGateDraft(
        basis="per_batch_before_failure",
        batches=({"batch_id": "b", "label": "", "aspect_ids": [], "prose_units": [],
                  "candidate_labels": [], "unit_labels": [], "follow_up_total": 0},)),
        PW.PackWriterError, "留存行必须有可读 label（读到的是哪一批）", needle="label")
    expect_error(lambda: PW.RetainedPreGateDraft(
        basis="per_batch_before_failure",
        batches=[{"batch_id": "b", "label": "1/2", "aspect_ids": [],
                  "prose_units": [{"prose_unit_id": "p1", "index": 1, "text": "   ",
                                   "source_member_refs": ["m1"], "source_fact_refs": [],
                                   "atom_candidate_ids": ["c1"]}],
                  "candidate_labels": [], "unit_labels": [], "follow_up_total": 0}]),
        PW.PackWriterError,
        "留存里的草稿单元 text 不得为空——空串会被读成「模型写了空话」，而事实是「没写」",
        needle="text")

    # ==================================================================
    # D. r8 同形端到端：逐批拒绝不得抹掉前几批**真实写过**的内容
    # ==================================================================
    spec = FIXT.WS.load_writing_spec(FIXT.SPEC_PATH)
    profile = FIXT.PP.load_presentation_profile(FIXT.PROFILE_PATH)
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    all_aspects = tuple(a.aspect_id for a in projection.aspects)
    case = _company_case(all_aspects, "task-pre-gate-r8")
    # 真正的批数是**投影 + 扫读状态**自己决定的（写手侧按 `scan.aspect_status ∩ 投影` 选面），
    # 因此这里用**同一个**确定性派生算，而不是拿 aspect 总数除单批上限——两者不等（实测 85 个
    # aspect 只落到 7 批）。夹具按这个数摆脚本，才不会出现「替身多给一条、批数却少一批」这种
    # 靠不住的对齐。
    selected = tuple(sorted(a for a in case["scan"].aspect_status
                            if projection.aspect(a) is not None))
    expected_batches = len(PW.plan_aspect_batches(projection, selected, case["scan"]))
    check(expected_batches >= 4 and len(_BATCH_TEXTS) >= expected_batches,
          f"前置条件：本用例的形状必须**天然**是多批、且逐批正文池够用（实测 "
          f"{expected_batches} 批 / 池 {len(_BATCH_TEXTS)} 句，"
          f"{len(selected)} 个可写 aspect / 单批上限 {PW.MAX_ASPECTS_PER_BATCH}）")

    def plan_of(case: dict, text: str) -> str:
        """该案的合格输出：草稿单元**逐字**给出本批自己那句话，原子映射到本批的候选。

        候选文本**逐批不同**：整轮合并按（`claim_text`, 事实类型）判同一性，逐批都写同一句话
        会让七批合成**一条**候选、却被七个草稿单元声明——那是自然草稿闭合性（原子归属必须唯一）
        该拒的形状，与本用例要测的「留存」无关。逐批不同才是「每批各写了一句」的真实形态。
        """
        candidate = FIXT._cand("c1", text, FIXT._fact_edge(case["scan"], "f-1"))
        prose = FIXT._prose("p1", text, members=["m1"], atoms=["c1"])
        return json.dumps(FIXT._plan(candidates=[candidate], prose=[prose]),
                          ensure_ascii=False)

    def run(case: dict, llm) -> PW.PackWriteOutcome:
        return PW.write_section(
            case["task"], case["authority"], projection=projection, writing_spec=spec,
            presentation_profile=profile, llm_client=llm,
            policy=PW.WriterPolicy(max_llm_retries=1),
            dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT,
            material_context=case["material_context"])

    # D1. 前几批合法、末批两次都不合法 ⇒ 整轮被拒；前几批的**逐批正文**必须留档。
    ok_texts = list(_BATCH_TEXTS[:expected_batches - 1])
    ok_labels = [f"{i}/{expected_batches}" for i in range(1, expected_batches)]
    last_label = f"{expected_batches}/{expected_batches}"
    llm = BATCH._ScriptedLlm(
        *[plan_of(case, text) for text in ok_texts],
        '{"claim_candidates": [{"candidate_key": "c1"',
        '{"claim_candidates": [{"candidate_key": "c1"')
    try:
        run(case, llm)
    except PW.ProposalSetRejectedError as exc:
        record = exc.rejections[0]
        check(record.rejection_kind == "schema_invalid",
              f"末批两次都不合法必须是 typed `schema_invalid`（实测 "
              f"{record.rejection_kind!r}）")
        check(record.candidate_ids == () and record.proposal_ids == (),
              "整束被拒 ⇒ **没有**任何候选身份：留存不得被读成「这些内容已经成了候选」")
        kept = record.retained_pre_gate
        check(kept is not None, "整束被拒时必须留下门前留档（本用例的修法就是这一条）")
        if kept is not None:
            body = kept.to_dict()
            check(body["basis"] == "per_batch_before_failure",
                  f"前几批已合法解析、末批失败 ⇒ 留存来源必须是逐批那一档（实测 "
                  f"{body['basis']!r}）")
            check([r["label"] for r in body["batches"]] == ok_labels,
                  f"恰好前 {len(ok_labels)} 批逐批留档、末批**不得**被当作留存内容（实测 "
                  f"{[r['label'] for r in body['batches']]}）")
            check([r["prose_units"][0]["text"] for r in body["batches"]] == ok_texts,
                  "每一批写出的正文必须逐字留在**它自己**那一行里（顺序、内容都不许串）")
            check(all(r["prose_units"] and all(u["source_member_refs"]
                                               for u in r["prose_units"])
                      for r in body["batches"]),
                  "留存必须带上逐单元的出处轴：只留正文、不留出处，"
                  "读者无法判断那段话当时的依据是哪一行")
            check(all(r["candidate_labels"] for r in body["batches"])
                  and all("unit_labels" in r for r in body["batches"]),
                  "留存必须带上逐批**自己给出的**标签（本夹具的批次没有交草稿单元，"
                  "因此 `unit_labels` 合法为空——空表也是读数，缺字段不是）")
            check(body["prose_unit_total"] == expected_batches - 1,
                  f"门前草稿单元合计必须等于前几批写出的段数（实测 {body['prose_unit_total']}）")
            check(not hasattr(kept, "draft_id") and not hasattr(kept, "draft_revision"),
                  "留存不得携带任何 `SectionDraft` 身份：带上它就会被读成「这一节其实成形了」")
        # 末批**失败**的原因照样逐批可读（逐批调用读数在拒绝记录上，与留存同一条记录）。
        # `BatchCallRecord.status` 说的是**这一次调用**的读数（`ok` / `truncated` / `error`），
        # 而「这一次返回没通过」是**解析**的结论——它逐字写在 `rejection_detail` 里并点名那一批。
        # 两件事混成同一格，就会把「调用失败」与「返回不合法」读成同一种病。
        check([b.label for b in record.batches]
              == ok_labels + [last_label, last_label]
              and all(b.status == "ok" for b in record.batches),
              f"末批的两次调用都必须逐次入账（含它自己那一批的坐标），实测 "
              f"{[(b.label, b.status) for b in record.batches]}")
        check(last_label in record.rejection_detail
              and "没有任何候选进入合并结果" in record.rejection_detail,
              f"被拒原因必须**点名**是那一批、并写明它覆盖的 aspect 因此没有候选（实测 "
              f"{record.rejection_detail[:160]!r}）")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 末批不合法必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:160]}）")
    else:
        failed += 1
        details.append("FAIL 末批两次都不合法时不得产出章节")

    # ==================================================================
    # E. 无合法候选 ⇒ 不留存（不得凭空造一段没有出处的草稿）
    # ==================================================================
    llm_none = BATCH._ScriptedLlm('{"claim_candidates": [{"candidate_key": "c1"',
                                  '{"claim_candidates": [{"candidate_key": "c1"')
    try:
        run(case, llm_none)
    except PW.ProposalSetRejectedError as exc:
        record_none = exc.rejections[0]
        check(record_none.retained_pre_gate is not None
              and record_none.retained_pre_gate.basis == "none"
              and record_none.retained_pre_gate.batches == (),
              f"第一批就失败 ⇒ 没有合法返回可留，必须如实记 `none` + 空表（实测 "
              f"{None if record_none.retained_pre_gate is None else record_none.retained_pre_gate.basis!r}）")
        check(record_none.retained_pre_gate.to_dict()["prose_unit_total"] == 0,
              "「无可留存」的合计必须是 0，不得为了诊断页好看凭空补一段正文")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 无合法候选时也必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:160]}）")
    else:
        failed += 1
        details.append("FAIL 无合法候选时不得产出章节")

    # ==================================================================
    # F. 读者面（runner）：四档状态、两个渲染、产物登记、诊断不喂正文
    # ==================================================================
    tasks = _tasks({"company": "公司业务", "industry": "行业情况", "financial": "财务概况"})
    row_kept = _rejection_row(
        retained=_retained(batches=(_retained_row(
            label="1/2", batch_id="b-1", aspects=("a-1", "a-2"),
            texts=("前一批写出的正文一。", "前一批写出的正文二。")),)),
        batches=(_batch_call(label="1/2", status="ok", aspects=("a-1", "a-2")),
                 _batch_call(label="2/2", status="ok", aspects=("a-3",))))
    row_none = _rejection_row(
        attempt=1, kind="batch_truncated", detail="第一批就被截断（示例原因）。",
        retained=_retained(basis="none", batches=()),
        batches=(_batch_call(label="1/2", status="truncated"),))

    state = _state(sections=("company",), tasks=tasks,
                   rejections={"company": [row_kept], "industry": [row_none],
                               "financial": ["不是对象"]},
                   errors={"industry": "TopicRuntimeError: 示例失败"})
    payload = ACC._pre_gate_draft_payload(state)
    by_section = {s["section_id"]: s for s in payload["sections"]}

    check(payload["status"] == "readback" and payload["label"] == "未核验、不可发布"
          and payload["retention_version"] == PW.PRE_GATE_RETENTION_VERSION
          and payload["schema_version"] == ACC.PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION,
          f"诊断载荷必须自带口径版本与逐字标注（实测 {payload['status']!r} / "
          f"{payload['label']!r}）")
    check(set(by_section) == {"company", "industry", "financial"},
          f"三节都必须出现在诊断里（实为 {sorted(by_section)}）："
          "只列有内容的那几节，会把「读不回来的节」静默地读成「不存在的节」")
    check(by_section["company"]["status"] == "readback"
          and "被采信" in by_section["company"]["detail"],
          f"有正式 SectionDraft 的节必须记 `readback`（实测 "
          f"{by_section['company']['status']!r}）：门前留存不是它的出口")
    check(by_section["industry"]["status"] == "nothing_retained"
          and by_section["industry"]["retained_rounds"][0]["retained_basis"] == "none",
          f"本轮一批都没交出合法返回 ⇒ `nothing_retained`（实测 "
          f"{by_section['industry']['status']!r}）：这是「无可留存」，不是「读不回来」")
    check(by_section["financial"]["status"] == "unavailable"
          and "不得读成「没有留存」" in by_section["financial"]["detail"],
          f"拒绝记录形状不对 ⇒ `unavailable`（实测 "
          f"{by_section['financial']['status']!r}）：读回失败不得冒充「没有留存」")
    check(by_section["industry"]["section_error"] == "TopicRuntimeError: 示例失败"
          and by_section["company"]["title"] == "公司业务"
          and by_section["company"]["retained_rounds"][0]["rejection_detail"]
          == row_kept["rejection_detail"],
          "诊断必须逐字给出本节标题、本节错误与被拒原因（原文，不转述）")

    # F2. `retained_unvetted` 这一档：本节未成形、但被拒的某一轮留有门前正文。
    state_kept = _state(tasks=tasks, rejections={"company": [row_kept]})
    payload_kept = ACC._pre_gate_draft_payload(state_kept)
    section_kept = payload_kept["sections"][0]
    check(section_kept["status"] == "retained_unvetted"
          and payload_kept["prose_unit_total"] == 2,
          f"本节未成形但留有门前正文 ⇒ `retained_unvetted`（实测 "
          f"{section_kept['status']!r} / 合计 {payload_kept['prose_unit_total']}）")
    check(ACC._PRE_GATE_LABEL in section_kept["detail"],
          "这一档的判读必须逐字带上不可发布标注：留存的是**未核验**的门前草稿")
    check([b["status"] for b in section_kept["retained_rounds"][0]["batch_calls"]]
          == ["ok", "ok"],
          "逐批调用读数必须与留存同一条记录：两格都返回了，"
          "「哪一批的返回没通过」在轮级拒绝原因里，不得挤进这一格")

    # F3. 两个渲染出自同一次读数。
    md = ACC._pre_gate_draft_md(payload_kept)
    check(ACC._PRE_GATE_LABEL in md and "# M930-3 门前草稿" in md,
          "人读版必须逐字带标注（标题行与引用行各一处）")
    check("## company · 公司业务" in md
          and "### 第 1 轮 · 拒绝原因 `schema_invalid`" in md
          and row_kept["rejection_detail"] in md,
          "人读版必须逐节、逐轮列出被拒原因（逐字原文）")
    check("| `1/2` | `ok` | 2 项 |" in md and "| `2/2` | `ok` | 1 项 |" in md,
          f"逐批调用读数必须以表格给出（实测：{'| `1/2` | `ok` | 2 项 |' in md}）；"
          "末批那两格都是 `ok`——那个 `ok` 说的是「调用返回了」，"
          "「返回不合法」由上面的轮级原因逐字交代，两者不得印成同一格")
    check("#### 留存批次 1/2（aspects 2 项）" in md
          and "`pu-1`（第 1 段；出处：材料行 `m-1`；原子候选 1 条）" in md
          and "  > 前一批写出的正文一。" in md,
          "留存批次必须逐单元给出：正文 + 序号 + 出处轴 + 原子候选数")
    check("门前留存草稿单元合计：**2** 段" in md,
          "人读版必须给出留存段数合计（读者据此判断这一轮到底写出过多少）")
    check("机器可读版在 `pre_gate_draft.json`" in md,
          "人读版必须指向机器可读版，并声明两者出自同一次读数")

    # F4. 兜底：读回自身失败不得把整轮产物带走，也不得伪装成「门前什么都没有」。
    broken = _state(tasks=tasks)
    broken.proposal_set_rejections = ["不是映射"]
    payload_broken = ACC._pre_gate_draft_payload(broken)
    check(payload_broken["status"] == "build_failed" and payload_broken["sections"] == []
          and "本身没读成" in payload_broken["note"],
          f"读回构建期异常必须返回 `build_failed` 并明说（实测 "
          f"{payload_broken['status']!r}）：空表会被读成「本轮门前什么都没有」")
    md_broken = ACC._pre_gate_draft_md(payload_broken)
    check("本次门口诊断本身没读成" in md_broken and ACC._PRE_GATE_LABEL in md_broken,
          "人读版同样要说清「读不回来」，且不得丢掉不可发布标注")

    # F5. 产物登记、写盘、manifest 指针，以及「诊断不喂正文」。
    for artifact in ("pre_gate_draft.md", "pre_gate_draft.json"):
        check(artifact in ACC.ARTIFACTS, f"{artifact} 必须登记在 ARTIFACTS 里")
    check(len(set(ACC.ARTIFACTS)) == len(ACC.ARTIFACTS),
          "ARTIFACTS 不得有重复项（产物清单即目录契约）")
    check(RUNNER_SRC.count('_write_json(state.run_dir / "pre_gate_draft.json"') == 1
          and RUNNER_SRC.count('_write_text(state.run_dir / "pre_gate_draft.md"') == 1,
          "两个渲染必须各有**恰好一处**写盘点（多一处就是两次读数）")
    check('"pre_gate_draft": {' in RUNNER_SRC
          and '"artifact": "pre_gate_draft.json"' in RUNNER_SRC
          and '"human_readable_artifact": "pre_gate_draft.md"' in RUNNER_SRC,
          "manifest.json 必须有一个 `pre_gate_draft` 块指向两份产物（否则产物存在但无人引用）")
    # 「不进正文」这条必须靠**结构**证：诊断只有一处调用点，且那条调用点在写盘处。
    # 若它被接进报告 / 预览 / 组装，这里的计数与调用点文本会当场变化。
    check(RUNNER_SRC.count("_pre_gate_draft_payload(") == 2
          and "pre_gate_draft = _pre_gate_draft_payload(state)" in RUNNER_SRC,
          "门前诊断必须只有**一处**调用点（`_write_run` 里那次）："
          "它若被报告 / 预览 / 组装读一次，未核验的门前草稿就成了可发布的正文")
    # 「诊断不喂正文」的第二半：留存正文只出现在**留存行**里，不出现在本节判读、拒绝原因等
    # 任何会被读成「这一节到底有什么」的字段里。逐字取本节除留存行之外的全文来核。
    outside_retention = json.dumps(
        {k: v for k, v in section_kept.items() if k != "retained_rounds"},
        ensure_ascii=False)
    outside_retention += "".join(
        str(r.get("rejection_detail") or "") + str(r.get("rejection_kind") or "")
        for r in section_kept["retained_rounds"])
    check("前一批写出的正文一。" not in outside_retention,
          "门前草稿正文不得混进本节判读 / 拒绝原因里——那些位置会被读成「这一节有什么」")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    try:  # GBK 控制台：中文详情必须可读，读不出来就等于没有失败原因
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

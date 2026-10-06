"""M930-3「先证明能成稿」批 §一.2：**逐候选裁出**与**批内逐候选补救**的边界。

跑法（无管道/无重定向）：`python -X utf8 -m evals.test_m930_3_candidate_carve_out`

本模块只测两件新增的事，且都要求「**不**放行任何本来该拒的东西」：

1. `carve_out_candidate_subset` / `_carve_out_trace`（束级裁出）：束里**只有部分**候选踩线时，
   被点名的逐条拒掉、其余**逐字**以新修订继续走链。边界逐条反证：一条都没踩线 ⇒ 不裁；
   一条都没幸存 ⇒ 不裁（退回整束拒绝）；点名了本束不存在的候选 ⇒ 抛；新修订里的候选文本
   被改过一个字 ⇒ 抛；裁出**不新增任何模型调用**（`model_calls_added` 恒为 0）。
2. `_salvage_batch_plan`（批内补救）：整响应可解析而只有部分候选结构非法时，逐条拒掉它们。
   边界：顶层不可解析 / 顶层键集不封闭 / 一条都没被拒 / 一条都没幸存 / **草稿单元或补件诉求
   整体不合法** ⇒ 一律返回 `None`（退回既有 C4 路径）。最后一条是本模块最要紧的反证：
   候选被逐条拒掉有逐条记录，草稿单元与补件诉求没有——因此那两者一律不得被静默丢掉。

夹具纪律：所有 JSON 文本夹具都是**手写的合法形状**，逐字取自 r7b 现场的错误类型
（`c9`/`c16` 的首条支撑边不是 `primary`），但**不含**任何真实 run 的产物字节，也不写任何文件、
不调 LLM、不联网。反例用真类或只读属性替身（`_Cand`），与生产签名同形。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import pack_writer as PW

REPO = Path(__file__).resolve().parent.parent

#: 草稿**材料轴**出处的键（`narr-8` / `pprov-1`）：走生产的唯一编码入口 `wmmref_<hex>`，
#: 夹具不手写一个看起来像的身份串。裸短名（`member-1`）在两条轴上都不合法——材料侧与事实侧
#: 的命名空间不相交，正是靠这条可核验性，「这段文字从哪来」才不会被写成第二种身份。
_M1 = PW.NS.manifest_member_ref("pack-1", "member-1")
_M2 = PW.NS.manifest_member_ref("pack-1", "member-2")
_M9 = PW.NS.manifest_member_ref("pack-1", "member-9")

#: 一条合法的路径 B factual 支撑边（长格式：不给别名表时的写法）。
EDGE_B = {"authority_kind": "topic_pack", "container_id": "pack-1",
          "material_id": "mat-1", "support_role": "primary",
          "support_semantics": "factual", "authorization_path": "path_b_material_derived"}


def _salvage(text: str):
    """批内补救的**语法层**入口（`require_natural_draft=False`）。

    本模块的补救探针只钉两件事：候选被逐条拒掉要逐条记名、草稿单元与补件诉求整体不合法时一律
    退回。这些夹具里根本没有草稿层（它们是候选面的形状反例），而当前线下「有候选、无草稿」是
    一条**独立**的形状判定（`pw-16`）——不显式关掉它，下面每一条断言都会先撞上它，探针就不再
    钉它要钉的那一条。那条判定由自己的专项反例与 `test_demo_pack_writer` 的端到端覆盖。
    """
    return PW._salvage_batch_plan(text, require_natural_draft=False)


def _edge_b(role: str = "primary", material_id: str = "mat-1") -> dict:
    return {**EDGE_B, "support_role": role, "material_id": material_id}


class _Cand:
    """只读候选替身：裁出只读 `candidate_id` / `claim_text`（不要求真 `ClaimCandidate`）。"""

    def __init__(self, candidate_id: str, claim_text: str, fact_type: str = "descriptive"):
        self.candidate_id = candidate_id
        self.claim_text = claim_text
        self.fact_type = fact_type


class _Unit:
    """只读 context 单元替身：`cco-6` 那一轴只读 `draft_unit_id` / `unit_kind` / `text`
    （不要求真 `NarrativeDraftUnit`——裁出**不**改这段文字的任何一个字）。"""

    def __init__(self, draft_unit_id: str, text: str, unit_kind: str = "paragraph") -> None:
        self.draft_unit_id = draft_unit_id
        self.unit_kind = unit_kind
        self.text = text


class _Bundle:
    def __init__(self, *candidates, prose_units: tuple = (), units: tuple = ()) -> None:
        self.candidates = tuple(candidates)
        # `cco-6`：门前束的 **context 单元**（`NarrativeDraftUnit`，context 支撑边的唯一合法
        # target）。与 `plan["narrative_draft_units"]` **按下标对齐**——这两个列表同源于
        # `_build_pre_gate_bundle` 的一次构造，因此裁出才敢按下标逐单元删除。
        self.units: tuple = tuple(units)
        self.proposals: tuple = ()
        self.follow_up_specs: tuple = ()
        # `cco-4` / `pw-15`：门前束带**草稿层**（空元组 = 本节没有草稿层，`narr-6` 及更早形态）。
        # 字段名与生产 `_PreGateBundle` 同形——裁出对账两侧都按它读。
        self.prose_units = tuple(prose_units)


def _edge_context(material_id: str = "mat-1") -> dict:
    """一条合法的 context 支撑边——**解析后的形状**（与 `_parse_context_edge` 的输出逐字同形：
    `ref` 是输入面的别名，解析完就没了，留下的是 `(container, material)` 那一对声明）。

    裁出不会「读 `ref`」：它必须把这对声明回查进**本次精确材料清单**，按本模块唯一的材料身份
    口径（`manifest_member_ref`）记录来源。这与 `_parse_context_edge` 的判据一致：context 边
    必须绑定一份真实 material，不得凭空。
    """
    return {"authority_kind": "topic_pack", "container_id": "pack-1",
            "material_id": material_id, "support_role": "corroborating",
            "support_semantics": "context", "authorization_path": "context_only"}


class _Manifest:
    """只读 manifest 替身：`_resolve_material_declaration` 只调 `entry_for(member_ref)`。

    与生产同形（`NS.WriterMaterialManifest.entry_for`），成员身份按**生产那一处**推导
    （`NS.manifest_member_ref`）——夹具不手写一个看起来像的身份串。
    """

    def __init__(self, *materials: tuple[str, str]) -> None:
        self.entries = {
            PW.NS.manifest_member_ref(pack, mat): _Entry(PW.NS.manifest_member_ref(pack, mat))
            for pack, mat in (materials or (("pack-1", "mat-1"),))}

    def entry_for(self, member_ref: str):
        return self.entries.get(str(member_ref))


class _Entry:
    def __init__(self, member_ref: str) -> None:
        self.member_ref = member_ref


def _unit_spec(key: str, text: str, *material_ids: str, kind: str = "paragraph") -> dict:
    """与 `bundle.units` **同序**的单元规格（`cco-6` 也按下标对齐）。"""
    return {"unit_key": key, "unit_kind": kind, "text": text,
            "context_support": [_edge_context(m) for m in (material_ids or ("mat-1",))]}


def _plan(*texts: str, unit_specs: list | None = None) -> dict:
    """与 `bundle.candidates` **同序**的提案集（裁出按下标对齐，两者必须同源）。

    `unit_specs` 同理与 `bundle.units` 同序；不传 = 本节没有 context 单元（两个列表都空）。
    """
    return {"claim_candidates": [
        {"candidate_key": f"c{i + 1}", "claim_text": t, "support": [_edge_b()]}
        for i, t in enumerate(texts)],
        "narrative_draft_units": list(unit_specs or ()), "follow_up_needs": []}


def _batch_json(*items: dict, units: list | None = None,
                follow_ups: list | None = None, **extra) -> str:
    return json.dumps({"claim_candidates": list(items),
                       "narrative_draft_units": units or [],
                       "follow_up_needs": follow_ups or [], **extra}, ensure_ascii=False)


def _candidate(key: str, text: str, *, first_role: str = "primary") -> dict:
    return {"candidate_key": key, "claim_text": text,
            "support": [_edge_b(role=first_role)]}


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

    T1, T2, T3 = "公司主要采用以销定产的生产模式", "公司通过直销与经销两种渠道销售", "公司采购以锂盐为主"

    # ---- 1. 束级裁出：只有被点名的那几条离开，其余逐字继续 -------------------------
    bundle = _Bundle(_Cand("cand-1", T1), _Cand("cand-2", T2), _Cand("cand-3", T3))
    plan = _plan(T1, T2, T3)
    out = PW.carve_out_candidate_subset(
        plan=plan, bundle=bundle, high_risk={"cand-2": ("同比",)}, ineligible={},
        from_attempt=1, to_attempt=2, revision_for_plan=lambda plan: "rev-2")
    check(out is not None, "有候选被点名、且有候选幸存时必须能裁出")
    carved_plan, decision = out
    check([s["claim_text"] for s in carved_plan["claim_candidates"]] == [T1, T3],
          f"裁出后的提案集必须恰是幸存者的原序：{[s['claim_text'] for s in carved_plan['claim_candidates']]}")
    check(carved_plan["claim_candidates"][0] is plan["claim_candidates"][0]
          and carved_plan["claim_candidates"][1] is plan["claim_candidates"][2],
          "幸存候选的 spec 必须**逐对象复用**（裁出只做删除，不得重新拼一份）")
    check(carved_plan["narrative_draft_units"] is plan["narrative_draft_units"]
          and carved_plan["follow_up_needs"] is plan["follow_up_needs"],
          "裁出只作用于候选；草稿单元与补件诉求按原对象带过（不得被顺手重整）")
    check(decision.source_candidate_ids == ("cand-1", "cand-2", "cand-3"),
          f"原束的**完整有序**身份必须留档：{decision.source_candidate_ids}")
    check(decision.surviving_candidate_ids == ("cand-1", "cand-3"),
          f"幸存者按原序：{decision.surviving_candidate_ids}")
    check([e.candidate_id for e in decision.excluded] == ["cand-2"],
          "被排除的只有被点名的那一条")
    check(decision.excluded[0].reasons == ("path_b_high_risk_surface",)
          and decision.excluded[0].surfaces == ("同比",),
          f"排除原因与逐字表面必须同源：{decision.excluded[0].to_dict()}")
    check(not decision.excluded_context_units and not decision.source_draft_unit_ids,
          "候选裁出**不**得顺手写一条空的单元轴（那一轴只在真有单元被撤下时在场）")
    check(PW.CANDIDATE_CARVE_OUT_VERSION == "cco-6"
          and decision.version == PW.CANDIDATE_CARVE_OUT_VERSION,
          "裁决必须带自己的规则版本（判据口径变了才升版）。`cco-5` → `cco-6`：裁出多认一类"
          "**被裁对象**——context 草稿单元（`narrative_draft_units`），多一张逐单元去向表"
          "（`source_draft_unit_ids` / `excluded_context_units`），且「至少排除一项」这条不变式"
          "的成立面从一个（候选）变成两个（候选 ∪ context 单元）。"
          "`cco-4` → `cco-5`：`path_b_unproven_current_state` 那一项多带一个**类型化原因码**"
          "（`unproven_current_state_cause_code`）——变的不是原因词表而是**同一条原因下的分级**"
          "（材料不足 vs 措辞口径变了），两者在 `cco-4` 的裁决里长得一模一样、出路却不同，"
          "读的人无法从版本号判断手上这份有没有这一级。`cco-3` → `cco-4` 是裁出的作用面从候选"
          "扩到**草稿层**（`pw-15`，多一张逐单元去向表 `prose_destinations`，且 `to_revision` "
          "改由裁出后的提案集重算）；`cco-2` → `cco-3` 是原因词表多一类 "
          "`path_b_unproven_current_state`，同一理由")

    # 1.1 两类踩线同时命中：原因并列、两组明细各归各位。
    out2 = PW.carve_out_candidate_subset(
        plan=plan, bundle=bundle, high_risk={"cand-2": ("未发生",)},
        ineligible={"cand-2": ("mat-9",)}, from_attempt=1, to_attempt=2,
        revision_for_plan=lambda plan: "rev-2")
    check(out2 is not None and out2[1].excluded[0].reasons
          == ("path_b_high_risk_surface", "path_b_ineligible_material_scope")
          and out2[1].excluded[0].ineligible_member_refs == ("mat-9",),
          f"同一条候选两类踩线必须并列（不得只留一类）：{out2[1].excluded[0].to_dict() if out2 else None}")

    # 1.2 `cco-4` / `pw-15`：裁出**同时**作用在草稿层。两个边界各反证一次。
    #
    # 草稿单元是「从材料写出来的那段话」，被裁候选只说明**它那几条原子不得保留**：部分原子被裁 ⇒
    # 那段话照旧在（文本逐字不动，原子账里去掉那几条，由门后组织器按已接受的原子改写或删除）；
    # 全部原子被裁 ⇒ 整段撤下（草稿层不允许没有原子的单元），原文逐字进裁决。两种情形都留痕。
    P1 = "公司生产以锂盐为主要原材料。"
    P2 = "公司销售以直销为主，另有经销渠道。"
    prose_prev = (
        PW.NS.NaturalProseDraftUnit.create(
            draft_revision="rev-1", section_id="sec-1", index=0, text=P1,
            source_member_refs=(_M1,), atom_candidate_ids=("cand-1", "cand-2")),
        PW.NS.NaturalProseDraftUnit.create(
            draft_revision="rev-1", section_id="sec-1", index=1, text=P2,
            source_member_refs=(_M2,), atom_candidate_ids=("cand-3",)))
    prose_bundle = _Bundle(_Cand("cand-1", T1), _Cand("cand-2", T2), _Cand("cand-3", T3),
                           prose_units=prose_prev)
    prose_plan = dict(plan)
    prose_plan["natural_prose_draft"] = [
        {"prose_key": "p1", "text": P1, "source_member_refs": [_M1],
         "atom_candidate_keys": ["c1", "c2"]},
        {"prose_key": "p2", "text": P2, "source_member_refs": [_M2],
         "atom_candidate_keys": ["c3"]}]
    out3 = PW.carve_out_candidate_subset(
        plan=prose_plan, bundle=prose_bundle, high_risk={"cand-2": ("同比",)},
        ineligible={}, from_attempt=1, to_attempt=2,
        revision_for_plan=lambda carved: f"rev-{len(carved['claim_candidates'])}")
    check(out3 is not None, "有草稿层时裁出照旧可裁（草稿层不改变裁出的候选级判据）")
    carved3, decision3 = out3
    check(decision3.to_revision == "rev-2",
          "新修订必须由**裁出后**的提案集算出来（传一个算好的值进来会与那一轮实际的 "
          f"draft_revision 不同源）：{decision3.to_revision}")
    kept3 = carved3["natural_prose_draft"]
    check([s["prose_key"] for s in kept3] == ["p1", "p2"]
          and kept3[0]["text"] == P1 and kept3[0]["source_member_refs"] == [_M1]
          and kept3[0]["atom_candidate_keys"] == ["c1"],
          f"部分原子被裁：该单元仍在、文本与出处逐字不动、只去掉被裁的原子：{kept3}")
    check(kept3[1]["atom_candidate_keys"] == ["c3"],
          "没被牵扯的草稿单元必须一个字都不动（含原子账）")
    check(len(decision3.prose_destinations) == 1
          and decision3.prose_destinations[0].prose_unit_id == prose_prev[0].prose_unit_id
          and decision3.prose_destinations[0].dropped_atom_candidate_ids == ("cand-2",)
          and decision3.prose_destinations[0].remaining_atom_count == 1
          and decision3.prose_destinations[0].dropped is False,
          "逐单元去向表必须记下「哪一段少了几条原子、还剩几条」（用原束候选身份）")
    # 全部原子被裁 ⇒ 整段撤下，原文逐字留档。
    out4 = PW.carve_out_candidate_subset(
        plan=prose_plan, bundle=prose_bundle, high_risk={"cand-3": ("同比",)},
        ineligible={}, from_attempt=1, to_attempt=2,
        revision_for_plan=lambda carved: "rev-2")
    check(out4 is not None and [s["prose_key"] for s in out4[0]["natural_prose_draft"]] == ["p1"],
          "整段原子都被裁掉时该草稿单元必须从提案集里撤下（草稿层不允许没有原子的单元）")
    dropped = out4[1].prose_destinations[0]
    check(dropped.dropped is True and dropped.remaining_atom_count == 0
          and dropped.prose_text == P2 and dropped.source_member_refs == (_M2,)
          and dropped.to_dict()["dropped"] is True,
          f"撤下的那段文字必须逐字留档（不得无声消失）：{dropped.to_dict()}")
    # 草稿层两侧不同源 ⇒ 抛（逐单元去向按下标对齐，数不一样时不得继续）。
    try:
        PW.carve_out_candidate_subset(
            plan=prose_plan, bundle=bundle, high_risk={"cand-2": ("同比",)},
            ineligible={}, from_attempt=1, to_attempt=2,
            revision_for_plan=lambda carved: "rev-2")
        check(False, "门前束草稿单元数与提案集草稿规格数不一致时必须抛")
    except PW.PackWriterError:
        check(True, "")
    # 1.2.1 跨修订对账：草稿层也必须是原束的**逐字删除子集**（文本 + 出处 + 原子逐位对应）。
    next_prose = (
        PW.NS.NaturalProseDraftUnit.create(
            draft_revision="rev-2", section_id="sec-1", index=0, text=P1,
            source_member_refs=(_M1,), atom_candidate_ids=("cand-a",)),
        PW.NS.NaturalProseDraftUnit.create(
            draft_revision="rev-2", section_id="sec-1", index=1, text=P2,
            source_member_refs=(_M2,), atom_candidate_ids=("cand-c",)))
    trace3 = PW._carve_out_trace(
        decision=decision3, previous_bundle=prose_bundle,
        bundle=_Bundle(_Cand("cand-a", T1), _Cand("cand-c", T3), prose_units=next_prose))
    check(trace3.decision is decision3 and trace3.model_calls_added == 0,
          "带草稿层的对账必须挂着同一次裁决、且仍声明零调用")
    for bad_units, why in (
            ((PW.NS.NaturalProseDraftUnit.create(
                draft_revision="rev-2", section_id="sec-1", index=0, text=P1 + "。",
                source_member_refs=(_M1,), atom_candidate_ids=("cand-a",)),
              next_prose[1]), "文本被改过"),
            ((PW.NS.NaturalProseDraftUnit.create(
                draft_revision="rev-2", section_id="sec-1", index=0, text=P1,
                source_member_refs=(_M9,), atom_candidate_ids=("cand-a",)),
              next_prose[1]), "材料出处被换过"),
            ((PW.NS.NaturalProseDraftUnit.create(
                draft_revision="rev-2", section_id="sec-1", index=0, text=P1,
                source_member_refs=(_M1,),
                atom_candidate_ids=("cand-a", "cand-2")),
              next_prose[1]), "原子账里混进了被裁的候选")):
        try:
            PW._carve_out_trace(
                decision=decision3, previous_bundle=prose_bundle,
                bundle=_Bundle(_Cand("cand-a", T1), _Cand("cand-c", T3),
                               prose_units=bad_units))
            check(False, f"裁出后的草稿{why}时必须抛（草稿只做删除，不重写、不新增）")
        except PW.PackWriterError:
            check(True, "")
    try:
        PW._carve_out_trace(
            decision=decision3, previous_bundle=prose_bundle,
            bundle=_Bundle(_Cand("cand-a", T1), _Cand("cand-c", T3),
                           prose_units=next_prose[:1]))
        check(False, "裁出后的草稿单元数与原束减被撤下单元不符时必须抛")
    except PW.PackWriterError:
        check(True, "")
    # 1.2.2 去向表自身的构造期不变量（草稿层版）。
    for kw, why in ((dict(prose_unit_id="npdu_x", prose_text=P1, source_member_refs=("m-1",),
                          dropped_atom_candidate_ids=(), remaining_atom_count=1),
                     "没有任何原子被裁"),
                    (dict(prose_unit_id="npdu_x", prose_text=P1, source_member_refs=("m-1",),
                          dropped_atom_candidate_ids=("c-1", "c-1"), remaining_atom_count=1),
                     "被裁原子有重复"),
                    (dict(prose_unit_id="npdu_x", prose_text="   ", source_member_refs=("m-1",),
                          dropped_atom_candidate_ids=("c-1",), remaining_atom_count=0),
                     "撤下的原文为空")):
        try:
            PW.CandidateCarveOutProseDestination(**kw)
            check(False, f"草稿去向条目{why}时必须抛")
        except PW.PackWriterError:
            check(True, "")

    # ---- 2. 裁出的边界：一条都没踩线 / 一条都没幸存 / 点名了不存在的候选 -----------
    check(PW.carve_out_candidate_subset(
        plan=plan, bundle=bundle, high_risk={}, ineligible={}, from_attempt=1,
        to_attempt=2, revision_for_plan=lambda plan: "rev-2") is None,
        "一条都没被点名时**不裁**（那不是裁出该处理的事，退回既有判定）")
    all_named = PW.carve_out_candidate_subset(
        plan=plan, bundle=bundle, high_risk={"cand-2": ("同比",)},
        ineligible={"cand-1": ("mat-9",), "cand-3": ("mat-9",)},
        from_attempt=1, to_attempt=2, revision_for_plan=lambda plan: "rev-2")
    check(all_named is None,
          "全部候选都被点名时**不裁**（幸存数为 0 与整束拒绝是同一件事，不得丢掉整束语义）")
    try:
        PW.carve_out_candidate_subset(
            plan=plan, bundle=bundle, high_risk={"cand-99": ("同比",)}, ineligible={},
            from_attempt=1, to_attempt=2, revision_for_plan=lambda plan: "rev-2")
        check(False, "点名了本束不存在的候选必须抛（不得「顺手多删一条」）")
    except PW.PackWriterError:
        check(True, "")
    try:
        PW.carve_out_candidate_subset(
            plan=_plan(T1, T2), bundle=bundle, high_risk={"cand-1": ("同比",)},
            ineligible={}, from_attempt=1, to_attempt=2, revision_for_plan=lambda plan: "rev-2")
        check(False, "提案集与门前束候选数不一致必须抛（按下标对齐，两者不同源时不得继续）")
    except PW.PackWriterError:
        check(True, "")

    # ---- 3. 跨修订对账：原身份 → 新身份，逐条落实、逐字相同 ------------------------
    new_bundle = _Bundle(_Cand("cand-a", T1), _Cand("cand-c", T3))
    trace = PW._carve_out_trace(decision=decision, previous_bundle=bundle, bundle=new_bundle)
    check([d.candidate_id for d in trace.destinations] == ["cand-1", "cand-2", "cand-3"],
          "去向表的键集必须恰好等于原束完整有序身份（不裁剪、不重排、不补）")
    check(decision.surviving_candidate_ids == ("cand-1", "cand-3")
          and trace.next_candidate_ids == ("cand-a", "cand-c"),
          f"原身份 → 新身份必须一一对应：{decision.surviving_candidate_ids} → "
          f"{trace.next_candidate_ids}")
    check(trace.surviving_next_candidate_ids == trace.next_candidate_ids,
          "两个名字必须指向同一个东西（旧身份在裁决里、新身份在回填里）")
    check(trace.destinations[1].reasons == ("path_b_high_risk_surface",)
          and trace.destinations[1].next_candidate_id == "",
          "被排除的那条在新修订里**没有**身份")
    check(trace.model_calls_added == 0,
          "裁出**不新增任何模型调用**（这条写进产物，不是一句注释）")
    check(trace.decision is decision, "对账必须挂着它自己那一次裁决（同一对象，不得重造一份）")
    check(trace.to_dict()["model_calls_added"] == 0
          and trace.to_dict()["next_candidate_ids"] == ["cand-a", "cand-c"]
          and trace.to_dict()["decision"]["version"] == PW.CANDIDATE_CARVE_OUT_VERSION,
          "落盘形态必须带裁决、新修订完整有序身份与零调用声明")
    check(decision.surviving_candidate_ids != trace.next_candidate_ids,
          "夹具前提：旧身份与新身份必须真的不同（否则上面那条对账是同义反复）")
    # 3.1 反证：新修订里少一条 / 多一条 / 文本被改过 ⇒ 一律抛。
    for bad, why in ((_Bundle(_Cand("cand-a", T1)), "少一条"),
                     (_Bundle(_Cand("cand-a", T1), _Cand("cand-c", T3),
                              _Cand("cand-d", "多出来的一条")), "多一条"),
                     (_Bundle(_Cand("cand-a", T1), _Cand("cand-c", T3 + "。")), "文本被改过")):
        try:
            PW._carve_out_trace(decision=decision, previous_bundle=bundle, bundle=bad)
            check(False, f"新修订{why}时必须抛（裁出不得改写任何候选）")
        except PW.PackWriterError:
            check(True, "")
    # 3.2 留档的「新修订完整有序身份」与逐条去向不一致 ⇒ 抛（含凭空多写一条新身份）。
    for wrong, why in (((trace.next_candidate_ids + ("cand-e",)), "多写一条新身份"),
                       ((trace.next_candidate_ids[0],), "少写一条新身份"),
                       (("cand-c", "cand-a"), "新身份次序被换过")):
        try:
            PW.CandidateCarveOutTrace(decision=decision,
                                      destinations=trace.destinations,
                                      next_candidate_ids=wrong)
            check(False, f"新修订身份{why}时必须抛（否则「候选悄悄消失/多出」读不出来）")
        except PW.PackWriterError:
            check(True, "")
    try:
        PW.CandidateCarveOutTrace(decision=decision, destinations=trace.destinations,
                                  next_candidate_ids=trace.next_candidate_ids,
                                  model_calls_added=1)
        check(False, "声明裁出新增了模型调用时必须抛（裁出是零调用出口）")
    except PW.PackWriterError:
        check(True, "")

    # ---- 4. 裁决与对账的构造期不变量（每一档逐条反证） -----------------------------
    def _decision(**kw):
        base = {"version": PW.CANDIDATE_CARVE_OUT_VERSION, "from_attempt": 1,
                "to_attempt": 2, "to_revision": "rev-2",
                "source_candidate_ids": ("cand-1", "cand-2"),
                "excluded": (PW.CandidateCarveOutDestination(
                    candidate_id="cand-2", claim_text=T2,
                    reasons=("path_b_high_risk_surface",), surfaces=("同比",)),)}
        base.update(kw)
        return PW.CandidateCarveOutDecision(**base)

    try:
        _decision(version="cco-0")
        check(False, "裁决的规则版本必须是当前版本")
    except PW.PackWriterError:
        check(True, "")
    try:
        _decision(excluded=())
        check(False, "没有任何候选被排除时不得构造裁决")
    except PW.PackWriterError:
        check(True, "")
    try:
        _decision(source_candidate_ids=("cand-1", "cand-2"), excluded=(
            PW.CandidateCarveOutDestination(candidate_id="cand-9", claim_text="x",
                                            reasons=("path_b_high_risk_surface",),
                                            surfaces=("同比",)),
            PW.CandidateCarveOutDestination(candidate_id="cand-2", claim_text=T2,
                                            reasons=("path_b_high_risk_surface",),
                                            surfaces=("同比",))),)
        check(False, "被排除的候选不在原束身份里时必须抛")
    except PW.PackWriterError:
        check(True, "")
    try:
        _decision(excluded=(PW.CandidateCarveOutDestination(
            candidate_id="cand-1", claim_text=T1,
            reasons=("path_b_high_risk_surface",), surfaces=("同比",)),
            PW.CandidateCarveOutDestination(
                candidate_id="cand-2", claim_text=T2,
                reasons=("path_b_high_risk_surface",), surfaces=("同比",))))
        check(False, "全部候选都被排除时不得构造裁决（幸存数为 0）")
    except PW.PackWriterError:
        check(True, "")
    # 去向条目的自洽：恰好「带排除原因」或「带新身份」之一；未登记原因不得出现。
    for kw, why in ((dict(candidate_id="cand-1", claim_text=T1), "两者都没有"),
                    (dict(candidate_id="cand-1", claim_text=T1,
                          reasons=("path_b_high_risk_surface",), surfaces=("同比",),
                          next_candidate_id="cand-a"), "两者都有"),
                    (dict(candidate_id="cand-1", claim_text=T1,
                          reasons=("made_up_reason",)), "原因不在封闭词表里"),
                    (dict(candidate_id="cand-1", claim_text=T1,
                          reasons=("path_b_high_risk_surface",)), "缺逐字表面")):
        try:
            PW.CandidateCarveOutDestination(**kw)
            check(False, f"去向条目{why}时必须抛")
        except PW.PackWriterError:
            check(True, "")
    check(set(PW.CANDIDATE_CARVE_OUT_REASONS) == {
        "path_b_high_risk_surface", "path_b_ineligible_material_scope",
        "path_b_history_only_current_state", "path_b_unproven_current_state",
        "candidate_structure_invalid"},
        f"裁出原因词表必须封闭且恰好这五条（四条候选级判据 + 结构非法）。"
        "这条断言的作用就是**逼人在这里改**：加一条原因而不动它，判据与词表就会悄悄分叉。"
        "`cco-3` 新增 `path_b_unproven_current_state`（`srsc-2` 独立支撑结论）；"
        f"`cco-4` **没有**再动这张表（动的是裁出的作用面）：{PW.CANDIDATE_CARVE_OUT_REASONS}")

    # ---- 5. 拒绝记录上的 `carve_out`：只对触发类存在、与 reproposal 互斥 ----------
    def _record(**kw):
        base = dict(attempt=1, rejection_kind="path_b_high_risk_surface",
                    rejection_detail="d",
                    candidate_ids=("cand-1", "cand-2", "cand-3"),
                    candidate_audit=tuple(
                        PW.RejectedCandidateAudit(candidate_id=c,
                                                  reasons=("not_individually_implicated",))
                        for c in ("cand-1", "cand-2", "cand-3")))
        base.update(kw)
        return PW.ProposalSetRejectionRecord(**base)

    record = _record(carve_out=trace)
    check(record.carve_out is trace and record.to_dict()["carve_out"] is not None,
          "带裁出的记录必须能落盘（且不改变 `whole_set_rejected`）")
    check(record.to_dict()["whole_set_rejected"] is True,
          "原束的留档语义**不变**：`whole_set_rejected` 恒为 true")
    check(record.answered_by_attempt is None and record.reproposal is None,
          "裁出这一路上 `answered_by_attempt` 必须留空（它没有向模型重问任何东西）")
    try:
        _record(rejection_kind="natural_draft_not_closed", carve_out=trace)
        check(False, "非触发类 kind 不得带裁出（只有触发类有逐项可写的裁出内容）")
    except PW.PackWriterError:
        check(True, "")
    check(set(PW.CARVE_OUT_ELIGIBLE_KINDS) == set(PW.REPROPOSAL_TRIGGER_KINDS)
          | {"narrative_gate_blocking"},
          "可带裁出的 kind 恰好是四类触发类 + `narrative_gate_blocking`（`cco-6` 起后者也可"
          "逐项排除 context 单元，但没有定向重提案的内容）。两张表**只在这一项上分叉**："
          "触发类有「向模型重问什么」可写，硬门阻断只有「哪些 context 单元撤下、为什么」可写。"
          f"{PW.CARVE_OUT_ELIGIBLE_KINDS}")
    check(set(PW.CONTEXT_UNIT_CARVE_OUT_REASONS) == {"narrative_vague_period"},
          "context 单元裁出原因是**另一张封闭表**，不得与候选原因词表合并："
          "两张表的成立理由不同（候选那五条说的是「这条候选为什么不能当事实」，"
          "这条说的是「这个背景单元的**文本表面**为什么进不了正文」），"
          f"合并就再也分不清是哪一轴判的。{PW.CONTEXT_UNIT_CARVE_OUT_REASONS}")
    carve_record = _record(rejection_kind="narrative_gate_blocking", carve_out=trace)
    check(carve_record.carve_out is trace,
          "`narrative_gate_blocking` 自 `cco-6` 起属于可裁出类（硬门阻断的 context 单元要能逐项撤下）")
    try:
        _record(answered_by_attempt=2, carve_out=trace)
        check(False, "裁出与定向重提案必须互斥（同时排定就是凭空多出一轮）")
    except PW.PackWriterError:
        check(True, "")
    # 裁出的幸存集必须来自本束的完整有序身份（旧身份一侧，不得凭空引入新候选）。
    alien_decision = PW.CandidateCarveOutDecision(
        version=PW.CANDIDATE_CARVE_OUT_VERSION, from_attempt=1, to_attempt=2,
        to_revision="rev-2", source_candidate_ids=("cand-1", "cand-9"),
        excluded=(PW.CandidateCarveOutDestination(
            candidate_id="cand-1", claim_text=T1,
            reasons=("path_b_high_risk_surface",), surfaces=("同比",)),))
    alien_trace = PW.CandidateCarveOutTrace(
        decision=alien_decision,
        destinations=(alien_decision.excluded[0],
                      PW.CandidateCarveOutDestination(
                          candidate_id="cand-9", claim_text="束外的候选",
                          next_candidate_id="cand-x")),
        next_candidate_ids=("cand-x",))
    check(alien_trace.next_candidate_ids == ("cand-x",), "束外夹具自洽")
    try:
        _record(carve_out=alien_trace)
        check(False, "幸存身份超出本束身份时必须抛（不得凭空引入新候选）")
    except PW.PackWriterError:
        check(True, "")

    # ---- 6. 批内逐候选补救：只有部分候选结构非法 --------------------------------
    text = _batch_json(_candidate("c1", T1),
                       _candidate("c9", T2, first_role="corroborating"),
                       _candidate("c16", "公司股权结构未发生变化", first_role="corroborating"),
                       _candidate("c3", T3))
    salvaged = _salvage(text)
    check(salvaged is not None, "整响应可解析、只有部分候选结构非法时必须能补救")
    salvaged_plan, rejected = salvaged
    check([s["claim_text"] for s in salvaged_plan["claim_candidates"]] == [T1, T3],
          f"幸存候选必须按原序逐字保留：{[s['claim_text'] for s in salvaged_plan['claim_candidates']]}")
    check([r["candidate_key"] for r in rejected] == ["c9", "c16"],
          f"被逐条拒掉的候选必须逐条记名（模型自己写的标签）：{rejected}")
    check(all(r["reason"] == "candidate_structure_invalid" for r in rejected),
          f"批内补救的原因必须来自封闭词表：{[r['reason'] for r in rejected]}")
    check(all(r["detail"] and r["claim_text"] for r in rejected),
          "被拒条目必须带**原文**与逐字错误串（否则读的人只知道「有几条不行」）")
    check("primary" in rejected[0]["detail"], f"错误串必须说明是哪一条判据：{rejected[0]['detail']}")
    check(salvaged_plan["narrative_draft_units"] == []
          and salvaged_plan["follow_up_needs"] == [],
          "本轮没有单元与诉求时，补救结果里也必须是空列表（不得凭空补一个字段）")

    # 6.1 r7b 现场的另一类：`strict=False` 容忍字符串内的裸换行。
    raw_newline = ('{"claim_candidates": [{"candidate_key": "c1",'
                   ' "claim_text": "公司以销定产。\n客户集中度方面，前五大客户合计占比 30%",'
                   ' "support": [{"authority_kind": "topic_pack", "container_id": "pack-1",'
                   ' "material_id": "mat-1", "support_role": "primary",'
                   ' "support_semantics": "factual",'
                   ' "authorization_path": "path_b_material_derived"}]},'
                   ' {"candidate_key": "c2", "claim_text": "x",'
                   ' "support": [{"authority_kind": "topic_pack", "container_id": "pack-1",'
                   ' "material_id": "mat-1", "support_role": "corroborating",'
                   ' "support_semantics": "factual",'
                   ' "authorization_path": "path_b_material_derived"}]}],'
                   ' "narrative_draft_units": [], "follow_up_needs": []}')
    strict_failed = False
    try:
        json.loads(raw_newline)
    except ValueError:
        strict_failed = True
    check(strict_failed, "夹具前提：这份返回在严格 JSON 下必须解析失败（否则这条反证不成立）")
    nl = _salvage(raw_newline)
    check(nl is not None and len(nl[0]["claim_candidates"]) == 1
          and "\n" in nl[0]["claim_candidates"][0]["claim_text"],
          "字符串内的裸换行只影响**编码**、不影响语义：必须解析出逐字原文（含换行本身）")
    try:
        PW.parse_writer_proposals(raw_newline)
        check(False, "夹具前提：这份返回里确有一条结构非法候选，整束解析必须拒它")
    except PW.PackWriterError:
        check(True, "")
    # 把那条非法候选的支撑边换成合法 primary 之后，整束解析必须能读——这才证明
    # `strict=False` 只容忍「字符串内裸控制字符」这一类**序列化**缺陷，不放宽任何结构判据。
    check(PW.parse_writer_proposals(raw_newline.replace('"corroborating"', '"primary"'),
                                    require_natural_draft=False) is not None,
          "结构判据本身一个字没放宽：修好那条非法边之后整束必须能解析"
          "（关掉的只是「有候选必须有草稿」这条与本组无关的形状判定，见 `_salvage`）")

    # 6.2 容错**逐类收窄**：`strict=False` 是布尔参数，一开就同时放行全部 `0x00–0x1F`。
    #     已证明需要处理的只有字符串内的裸换行/回车/制表符（6.1 的现场证据），因此其余裸控制
    #     字符必须由本侧判定并拒掉——「顺带接受任意隐藏控制字符」不是容错，是放宽 fail-closed。
    check(PW._raw_control_chars_in_strings(raw_newline) == ["\n"],
          "扫描面必须先证明它只认出**字符串内**那一个裸换行（否则下面的反例不成立）")
    check(PW._raw_control_chars_in_strings('{"a": "x\\ny"}') == [],
          "转义写法（反斜杠 + n）不是裸控制字符：不得被扫描面当成裸换行")
    check(PW._raw_control_chars_in_strings('{"a": \x07}') == [],
          "字符串**外**的裸控制字符不进扫描面（它由 JSON 语法自己拒，不由本函数放行）")
    for char, label in (("\x00", "NUL"), ("\x07", "BEL"), ("\x0b", "VT"), ("\x0c", "FF")):
        hidden = raw_newline.replace("。\n", "。" + char)
        check(PW._raw_control_chars_in_strings(hidden) == [char],
              f"{label} 必须在字符串内被扫描面逐字认出")
        hidden_failed = False
        try:
            json.loads(hidden)
        except ValueError:
            hidden_failed = True
        check(hidden_failed, f"夹具前提：含裸 {label} 的返回在严格 JSON 下必须解析失败")
        try:
            PW.parse_writer_proposals(hidden)
            check(False, f"字符串内的裸 {label} 没有任何现场证据、语义也不唯一：必须被拒")
        except PW.PackWriterError as exc:
            check("裸控制字符" in str(exc),
                  f"拒因必须点名「未经证明的裸控制字符」而不是笼统的语法错：{exc}")
            check(f"U+{ord(char):04X}" in str(exc),
                  f"拒因必须给出该字符的码位（否则读的人不知道要改哪一个字节）：{exc}")
        check(_salvage(hidden) is None,
              f"含裸 {label} 的返回不得进入批内补救：连顶层都不该被读出来")
    # 反向：字符串**外**的裸控制字符仍然不是合法 JSON（容错不许把它当空白吃掉）。
    outside = raw_newline.replace('{"claim_candidates"', '{\x07"claim_candidates"')
    try:
        PW.parse_writer_proposals(outside)
        check(False, "字符串外的裸控制字符不得被容错当空白吃掉")
    except PW.PackWriterError:
        check(True, "")
    # 反向：回车与制表符与裸换行**同类**（同一台模型、同一类序列化缺陷），必须同样放行。
    for char, label in (("\r", "CR"), ("\t", "TAB")):
        raw = raw_newline.replace("。\n", "。" + char)
        check("\n" not in raw, f"夹具前提：{label} 版必须不含裸换行（否则测的不是它）")
        parsed = _salvage(raw)
        check(parsed is not None and char in parsed[0]["claim_candidates"][0]["claim_text"],
              f"字符串内的裸{label} 与裸换行同类：必须解析出逐字原文（含该字符本身）")

    # ---- 7. 批内补救的边界：一律退回既有 C4 路径（返回 None） --------------------
    check(_salvage("不是 JSON") is None, "顶层读不出时必须退回 fail-closed")
    check(_salvage('["数组"]') is None, "顶层不是对象时必须退回 fail-closed")
    check(_salvage(json.dumps({"claim_candidates": [],
                                              "narrative_draft_units": [],
                                              "follow_up_needs": [],
                                              "tools": []})) is None,
          "顶层键集不封闭时必须退回（未知顶层键是未登记意图，不得被忽略）")
    check(_salvage(json.dumps({"claim_candidates": {},
                                              "narrative_draft_units": []})) is None,
          "候选不是数组时必须退回")
    # 全部候选都合法 / 全部候选都非法 ⇒ 都不做批内补救。
    check(_salvage(_batch_json(_candidate("c1", T1))) is None,
          "一条都没被拒时不做补救（本该走整束解析）")
    check(_salvage(_batch_json(
        _candidate("c1", T1, first_role="corroborating"),
        _candidate("c2", T2, first_role="corroborating"))) is None,
        "一条都没幸存时不做补救（整批没有可继续的内容）")
    # **最要紧的一条**：草稿单元 / 补件诉求整体不合法时，一律不补救。
    bad_unit = _batch_json(
        _candidate("c1", T1), _candidate("c9", T2, first_role="corroborating"),
        units=[{"unit_key": "u1", "unit_kind": "不存在的类型", "text": "x"}])
    check(_salvage(bad_unit) is None,
          "草稿单元整体不合法时必须**不**补救：候选被逐条拒掉有逐条记录，单元没有——"
          "静默丢掉单元就是把内容丢失当成通过")
    bad_follow = _batch_json(
        _candidate("c1", T1), _candidate("c9", T2, first_role="corroborating"),
        follow_ups=[{"不存在的字段": "x"}])
    check(_salvage(bad_follow) is None,
          "补件诉求整体不合法时同上（不得静默丢掉模型提过的诉求）")
    # 单元与诉求**整体合法**时，补救必须把它们逐字带进结果。
    good = _salvage(_batch_json(
        _candidate("c1", T1), _candidate("c9", T2, first_role="corroborating"),
        units=[{"unit_key": "u1", "unit_kind": "paragraph", "text": "公司以销定产。"}]))
    check(good is not None and [u["text"] for u in good[0]["narrative_draft_units"]]
          == ["公司以销定产。"],
          "单元整体合法时，补救必须把单元逐字带过（补救只作用于候选）")

    # ---- 9. `cco-6`：context 单元那一轴（与候选轴正交，逐项 typed 排除） ----------
    # 这一轴处理的是 r9 现场的一类越权：**明确否定事实**（「报告期内未发生变更」）被模型写成
    # *context* 单元。它没有事实身份，因此既不能重新提案为 factual 候选（那要路径 A 的预验证
    # 事实），也不能只是把「报告期」三个字删掉（那是改写正文而不是撤下内容）。唯一合法出路是
    # **逐项撤下**这段衔接文字，并把原文 / 来源 / 原因 / 命中表面留档。
    UA = "公司主要产品包括锂离子电池正极材料。"
    UB = "报告期内，公司控股股东与实际控制人未发生变更。"
    unit_bundle = _Bundle(
        _Cand("cand-1", T1), _Cand("cand-2", T2), _Cand("cand-3", T3),
        units=(_Unit("ndu_a", UA), _Unit("ndu_b", UB)))
    unit_manifest = _Manifest(("pack-1", "mat-1"), ("pack-1", "mat-2"))
    unit_plan = _plan(T1, T2, T3, unit_specs=[
        _unit_spec("u1", UA, "mat-1"), _unit_spec("u2", UB, "mat-2")])
    unit_out = PW.carve_out_candidate_subset(
        plan=unit_plan, bundle=unit_bundle, high_risk={}, ineligible={},
        blocking_context_units={"ndu_b": ("narrative_vague_period",)},
        manifest=unit_manifest,
        from_attempt=1, to_attempt=2, revision_for_plan=lambda p: "rev-2")
    check(unit_out is not None, "点名了 context 单元时必须产出裁出（而不是整束作废）")
    carved_plan, unit_decision = unit_out
    check([c["claim_text"] for c in carved_plan["claim_candidates"]] == [T1, T2, T3],
          "单元那一轴开火时，**候选一条都不许被连坐**（两个坏单元不得销毁其余独立合格的描述）")
    check([u["text"] for u in carved_plan["narrative_draft_units"]] == [UA],
          "被点名的单元逐条离开，幸存单元按原序保留")
    check(carved_plan["narrative_draft_units"][0] is unit_plan["narrative_draft_units"][0],
          "幸存单元必须**逐对象复用**（这一轴在场却没开火 ≠ 这一轴不在场）")
    check(carved_plan["follow_up_needs"] is unit_plan["follow_up_needs"],
          "补件诉求按原对象带过（裁出这一步不新增、不改写、也不代为裁决诉求去向）")
    check(unit_decision.excluded == () and unit_decision.surviving_candidate_ids
          == unit_decision.source_candidate_ids == ("cand-1", "cand-2", "cand-3"),
          "单元轴上候选零排除时，候选侧身份必须原样完整")
    check(unit_decision.source_draft_unit_ids == ("ndu_a", "ndu_b"),
          f"原束单元的**完整有序**身份必须留档：{unit_decision.source_draft_unit_ids}")
    check(len(unit_decision.excluded_context_units) == 1, "恰好撤下被点名的那一个单元")
    cut = unit_decision.excluded_context_units[0]
    check(cut.draft_unit_id == "ndu_b" and cut.unit_key == "u2"
          and cut.unit_kind == "paragraph",
          "被撤单元的把手、模型标签与类型必须逐字留档（回填那一侧按把手对账）")
    check(cut.text == UB and cut.excluded,
          f"被撤单元的**原文**必须逐字留档（审计要读得出撤的是什么）：{cut.text!r}")
    check(cut.reasons == ("narrative_vague_period",)
          and cut.hit_phrases == ("报告期",),
          f"撤下原因与命中表面必须同源、且都写进记录：{cut.to_dict()}")
    check(cut.context_member_refs == (PW.NS.manifest_member_ref("pack-1", "mat-2"),),
          "被撤单元的 context 来源必须按**本模块唯一的材料身份口径**逐条可读"
          f"（裸 material ID 不是身份）：{cut.context_member_refs}")
    check(cut.next_draft_unit_id == "",
          "被撤行的「新身份」必须留空（空 ⟺ 被撤下，非空 ⟺ 幸存）")
    dumped = unit_decision.to_dict()
    check(dumped["source_draft_unit_ids"] == ["ndu_a", "ndu_b"]
          and dumped["excluded_context_units"][0]["text"] == UB,
          "单元轴的两张表都必须落盘（否则「撤了什么、为什么」只在内存里）")
    # 跨修订对账：新束 = 原束**减去**被撤单元，文本与类型逐字不变；去向表覆盖原束全部单元。
    next_bundle = _Bundle(
        _Cand("cand-1", T1), _Cand("cand-2", T2), _Cand("cand-3", T3),
        units=(_Unit("ndu_a", UA),))
    unit_trace = PW._carve_out_trace(decision=unit_decision,
                                    previous_bundle=unit_bundle, bundle=next_bundle)
    check(unit_trace.next_draft_unit_ids == ("ndu_a",),
          f"幸存单元身份按原序：{unit_trace.next_draft_unit_ids}")
    check([d.draft_unit_id for d in unit_trace.context_unit_destinations]
          == ["ndu_a", "ndu_b"],
          "去向表必须覆盖**原束全部**单元（不只是被撤的那几个）")
    check(unit_trace.context_unit_destinations[1].next_draft_unit_id == ""
          and unit_trace.context_unit_destinations[1].hit_phrases == ("报告期",),
          "被撤行复用裁决里的那一条（原因与命中表面不得在对账时被重算成另一份）")
    check(unit_trace.context_unit_destinations[0].next_draft_unit_id == "ndu_a"
          and unit_trace.context_unit_destinations[0].hit_phrases == ()
          and unit_trace.context_unit_destinations[0].unit_key == "",
          "幸存行只带新身份，不带任何排除证据（两类行不得互相冒充）")
    check(unit_trace.model_calls_added == 0,
          "单元裁出同样是**零调用**出口（它不向模型重问任何东西）")
    check(unit_trace.next_candidate_ids == ("cand-1", "cand-2", "cand-3"),
          "单元轴开火不改变候选侧身份")

    # 边界逐条反证：每一条都必须是「不裁 / 抛」，没有一条是「顺手放行」。
    def _expect_raise(why: str, **kw) -> None:
        try:
            PW.carve_out_candidate_subset(
                plan=kw.pop("plan", unit_plan), bundle=unit_bundle, high_risk={}, ineligible={},
                blocking_context_units=kw.pop("units", {"ndu_b": ("narrative_vague_period",)}),
                manifest=kw.pop("manifest", unit_manifest),
                from_attempt=1, to_attempt=2, revision_for_plan=lambda p: "rev-2")
            check(False, why)
        except PW.PackWriterError:
            check(True, "")

    _expect_raise("点名了本束不存在的 context 单元时必须抛",
                  units={"ndu_zzz": ("narrative_vague_period",)})
    _expect_raise("原因不在封闭词表里时必须抛（本出口不得替别的判据做决定）",
                  units={"ndu_b": ("natural_draft_not_closed",)})
    _expect_raise("点名的原因在这段文字里没有逐字表面时必须抛（取数面缺陷不得写成记录）",
                  units={"ndu_a": ("narrative_vague_period",)})
    _expect_raise("全部 context 单元都被撤下时不得构造裁决（新修订里一段衔接都没有）",
                  units={"ndu_a": ("narrative_vague_period",),
                         "ndu_b": ("narrative_vague_period",)})
    # 提案集与门前束单元数不一致 ⇒ 抛（按下标对齐的前提不成立时不得继续）。
    _expect_raise("提案集单元数与门前束不一致时必须抛（两者不同源时不得按下标删除）",
                  plan=_plan(T1, T2, T3, unit_specs=[_unit_spec("u1", UA, "mat-1")]))
    # 来源不可回查 ⇒ 不得撤下（「撤下了但不知道为什么从哪来」不是一条可回查的记录）。
    _expect_raise("被撤单元的 context 边缺 material_id 时必须抛（来源不得缺一条）",
                  plan=_plan(T1, T2, T3, unit_specs=[
                      _unit_spec("u1", UA, "mat-1"),
                      {"unit_key": "u2", "unit_kind": "paragraph", "text": UB,
                       "context_support": [{"authority_kind": "topic_pack",
                                            "container_id": "pack-1", "material_id": "",
                                            "support_role": "corroborating",
                                            "support_semantics": "context",
                                            "authorization_path": "context_only"}]}]))
    _expect_raise("被撤单元引用的材料不在本次精确材料清单里时必须抛（材料不得自报）",
                  plan=_plan(T1, T2, T3, unit_specs=[
                      _unit_spec("u1", UA, "mat-1"), _unit_spec("u2", UB, "mat-9")]))
    _expect_raise("单元轴要开火却没有精确材料清单可回查时必须抛（来源不得写成裸 ID）",
                  manifest=None)
    # 没点名任何单元 ⇒ 这一轴一句话都不说：既不裁、也不留下空的单元轴。
    # （注意这里**不**传 manifest：候选轴独立于来源回查，单元轴不在场就不该要求清单。）
    axis_off = PW.carve_out_candidate_subset(
        plan=unit_plan, bundle=unit_bundle, high_risk={"cand-2": ("同比",)},
        ineligible={}, from_attempt=1, to_attempt=2,
        revision_for_plan=lambda p: "rev-2")
    check(axis_off is not None and not axis_off[1].excluded_context_units
          and axis_off[0]["narrative_draft_units"] is unit_plan["narrative_draft_units"],
          "候选轴单独开火时，单元列表必须原对象带过（不产生空单元轴、不重建列表）")
    # 两条轴同时开火：各自逐项，各自留档。
    both = PW.carve_out_candidate_subset(
        plan=unit_plan, bundle=unit_bundle, high_risk={"cand-2": ("同比",)},
        ineligible={}, blocking_context_units={"ndu_b": ("narrative_vague_period",)},
        manifest=unit_manifest,
        from_attempt=1, to_attempt=2, revision_for_plan=lambda p: "rev-2")
    check(both is not None and [c["claim_text"] for c in both[0]["claim_candidates"]]
          == [T1, T3] and [u["text"] for u in both[0]["narrative_draft_units"]] == [UA],
          "两条轴同时开火时各自逐项生效（不得只执行其中一条）")
    check([e.candidate_id for e in both[1].excluded] == ["cand-2"]
          and [e.draft_unit_id for e in both[1].excluded_context_units] == ["ndu_b"]
          and both[1].source_draft_unit_ids == ("ndu_a", "ndu_b"),
          "两条轴的留档必须同时在位（混成一张表就再也分不清是哪一轴判的）")
    # 判据与文本必须**当场**互核：真判据不许被放宽。
    check(PW.NS.vague_period_hits(UB) == ("报告期",)
          and PW.NS.vague_period_hits(UA) == (),
          "判据本身（逐字命中、按词根）不得因为多了这条出口而被放宽")
    check("VAGUE_PERIOD_PHRASES" in (REPO / "sections" / "narrative_schema.py")
          .read_text(encoding="utf-8"),
          "`narrative_vague_period` 的判据词表必须仍在生产代码里（本出口只撤内容，不改判据）")

    # ---- 10. 「可裁 / 不可裁」的判定：全部可裁才裁，有一条不可裁即整束退回 ---------
    def _issue(rule_id: str, location: str, severity: str = "blocking"):
        return PW.NS.NarrativeGateIssue(rule_id=rule_id, severity=severity,
                                        location=location, detail="d")

    gb = _Bundle(_Cand("cand-1", T1), _Cand("cand-2", T2),
                 units=(_Unit("ndu_a", UA), _Unit("ndu_b", UB)))
    only_units = PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "ndu_b"),))
    check(only_units == {"ndu_b": ("narrative_vague_period",)},
          f"全部 blocking 问题都点名 context 单元且原因在词表里 ⇒ 可裁：{only_units}")
    check(PW._carvable_context_units(bundle=gb, issues=()) is None,
          "没有任何 blocking 问题时返回 `None`（`{}` 永不出现在返回值里：调用方只判 `None`）")
    check(PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "cand-1"),)) is None,
        "**候选**踩同一判据时一律不可裁——它的出路由模型改绑路径 A 的预验证事实或撤下，"
        "裁出不得替它决定（把候选与衔接文字一起静默撤下 = 用一个删除动作回答一条断言）")
    check(PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "ndu_b"),
                           _issue("narrative_number_unbound", "ndu_a"))) is None,
        "**有一条** blocking 问题不属于可裁形状 ⇒ 整束不可裁（先裁一半只是把同一次拒绝"
        "拆成两轮，多出一条内容相同的拒绝记录）")
    check(PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "ndu_b"),
                           _issue("narrative_vague_period", "ndu_zzz"))) is None,
        "点名了本束不存在的单元 ⇒ 不可裁")
    check(PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "ndu_b"),
                           _issue("narrative_vague_period", "ndu_b",
                                  severity="rework"))) == {"ndu_b": ("narrative_vague_period",)},
        "只按 blocking 判：同一判据的 rework 级问题不在调用方传进来的那一份里")
    check(PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "ndu_b"),
                           _issue("narrative_vague_period", "ndu_b"))) == {
                               "ndu_b": ("narrative_vague_period",)},
        "同一单元被同一判据点名两次时按单元归并（原因不重复写）")
    # 条件 4（幸存数）：被点名的单元数**等于**束单元数 ⇒ 撤下之后一段衔接都不剩 ⇒ 不可裁。
    # 这条边界的后果只有一种：调用方退回既有路径（整束 fail-closed，原因里带着门规则 id）。
    # 少了它，这一支会走到裁决对象构造处撞校验，抛出一条**不带门规则 id** 的错误，让「这一束
    # 为什么被拒」在产物里读成一个跟 `narrative_vague_period` 无关的原因。
    # 端到端的回退行为由 `test_demo_pack_writer`「草稿单元文本含未绑定权威期间的措辞必须被硬门
    # 拒」那条覆盖（它要求异常原因里逐字出现 `narrative_vague_period`）；这里钉的是判定本身。
    check(PW._carvable_context_units(
        bundle=gb, issues=(_issue("narrative_vague_period", "ndu_a"),
                           _issue("narrative_vague_period", "ndu_b"))) is None,
        "全部单元都被点名 ⇒ 幸存数为 0 ⇒ 不可裁（「全撤」与整束拒绝是同一件事；"
        "退回既有路径，而不是让这一支抛出一条不带门规则 id 的错误）")
    gb3 = _Bundle(units=(_Unit("ndu_a", UA), _Unit("ndu_b", UB), _Unit("ndu_c", UA)))
    check(PW._carvable_context_units(
        bundle=gb3, issues=(_issue("narrative_vague_period", "ndu_a"),
                            _issue("narrative_vague_period", "ndu_b")))
        == {"ndu_a": ("narrative_vague_period",), "ndu_b": ("narrative_vague_period",)},
        "撤下两个还剩一个 ⇒ 仍可裁（条件是「严格小于束单元数」，不是「至多撤一个」）")
    check(PW._carvable_context_units(
        bundle=gb3, issues=(_issue("narrative_vague_period", "ndu_a"),
                            _issue("narrative_vague_period", "ndu_b"),
                            _issue("narrative_vague_period", "ndu_c"))) is None,
        "三个单元全被点名时同样不可裁（幸存数为 0 的判据与束有多大无关）")

    # ---- 8. 接线对账：规则版本、入口、以及「没删任何判据」 -----------------------
    src = (REPO / "sections" / "pack_writer.py").read_text(encoding="utf-8")
    for literal in ('_reject("path_b_high_risk_surface", detail, source=bundle, text=""',
                    '_reject("path_b_ineligible_material_scope", detail, source=bundle',
                    '_reject("path_b_history_only_current_state", detail, source=bundle',
                    '_reject("path_b_high_risk_surface", detail, **reject_kwargs)',
                    '_reject("path_b_ineligible_material_scope", detail, **reject_kwargs)',
                    '_reject("path_b_history_only_current_state", detail, **reject_kwargs)'):
        check(literal in src,
              f"三条出口的原因码必须仍是**字面量**（静态审计按字面量核对出口是否活着）：{literal}")
    check("carve_out_candidate_subset(" in src and "_carve_out_trace(" in src
          and "_salvage_batch_plan(" in src,
          "裁出与批内补救的实现在生产路径上必须被真的调用（不得只有定义）")
    check(src.count("pending_carve_out = None") >= 2,
          "待产出裁出必须在「回填后」与「该轮失败后」两处被清空（否则会把同一份 plan 重复重放）")
    check("MAX_DIRECTED_REPROPOSAL_PASSES = 1" in src
          and "MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH = 1" in src
          and "MAX_SWEEP_SHRINK_STEPS" in src,
          "裁出**不**改动任何既有额度常量（它是零调用出口，不是加预算）")
    # `cco-6` 接线：硬门那一支真的把「可裁单元」传下去，且判据没有被放宽。
    check("_carvable_context_units(" in src and "carve_units = (None if" in src,
          "`narrative_gate_blocking` 那一支必须真的调用可裁判定（不得只有定义）")
    check("blocking_context_units=carve_units" in src,
          "可裁判定结果必须真的传进裁出（否则判定算出来也没人用）")
    check("carved = (None if carve_units is None else" in src,
          "判定为不可裁（`None`）时必须**不构造**裁决对象、直接退回既有 fail-closed 出口："
          "把 `None` 交给裁出函数就等于让这一支去构造一份「一段衔接都不剩」的裁决，"
          "而那会抛出一条不带门规则 id 的错误")
    check("CONTEXT_UNIT_CARVE_OUT_REASONS = (" in src
          and len(PW.CONTEXT_UNIT_CARVE_OUT_REASONS) == 1
          and not (set(PW.CONTEXT_UNIT_CARVE_OUT_REASONS)
                   & set(PW.CANDIDATE_CARVE_OUT_REASONS)),
          "context 单元的可裁原因词表必须仍是**单元素**且与候选那张表分开"
          f"：{PW.CONTEXT_UNIT_CARVE_OUT_REASONS}")
    check('"narrative_vague_period"' in src
          and "def _carvable_context_units" in src
          and "def vague_period_hits" in (REPO / "sections" / "narrative_schema.py")
          .read_text(encoding="utf-8"),
          "本出口只**撤内容**：判据（`narrative_vague_period` / `vague_period_hits`）必须仍在，"
          "不得被这条出口顺带放松或绕过")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

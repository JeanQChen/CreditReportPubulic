"""Eval: legacy narrative 只读回放 —— narr-3 / narr-4 / narr-5 / narr-6 / narr-7 **各一个单版本
reader**（§16.10 #17 / #25）。

用法: python -m evals.test_demo_narrative_legacy_readback

证明：
 1. 登记面：legacy 集恰是 `("narr-3", "narr-4", "narr-5", "narr-6", "narr-7")`，current 是
    `narr-8` 且不在 legacy 集里；每个 legacy reader 绑定**自己那一版**的常量（不是「在 legacy
    集里」这种集合成员判定）；
 2. narr-3 载荷只经 `load_legacy_narrative_narr3_for_audit` 还原为**只读**视图；
 3. narr-3 reader **拒** narr-4 载荷（新增：legacy 集增长不得让旧 reader 静默多吞一版）与
    current 载荷，且缺 marker / 非对象 / 缺 `section_result_id` 一律 fail-closed；
 4. narr-4 载荷只经 `load_legacy_narrative_narr4_for_audit` 还原为**只读**视图；该 reader
    同样**拒** narr-3 载荷（即使字段集合法、只翻转 marker）与 current 载荷；
 5. narr-5 reader 只读还原 narr-5 载荷，并把它身上的**裸三元组 locator** 读成 `loc-0` 审计
    locator（§四.5：wire 升版必须提供 legacy audit reader，且禁止静默重解释成 `loc-1`）；
 5b. narr-6 reader（M930-3 指令 E 第 3 项）只读还原 narr-6 载荷：payload **逐字**回放（narr-6 的
    locator 已是 `loc-1`，不做任何转换），且**拒**带 `natural_prose_draft`（narr-7 的形状）、
    带 `section_result_id`（narr-3 的形状）、只有 marker 没有内容、以及 locator 仍是裸三元组的
    载荷；**不**要求 `proposed_support_refs` 非空（r7b 的 industry 束就是 0 proposal + unresolved
    的真实记录，必须能读回）；
 5d. narr-7 reader 只读还原 narr-7 载荷：草稿层**在场**（这是它相对 narr-6 的形状标记，本
    reader 必须接受），且**拒**草稿单元带 `source_fact_refs`（那是 narr-8 的形状）、材料轴为空、
    带 `section_result_id`、只有 marker 没有内容、非对象载荷；视图 `to_dict` 与载荷逐字一致
    （含单元身份：legacy 视图**不重算** id）；
 6. narr-8 current reader（`SectionDraft.from_dict` / 构造器）**拒**五个 legacy 版本，
    不按字段宽松降级读取；
 7. legacy 视图不是 current `SectionDraft`：没有 identity_body、不参与身份派生。

**本模块不宣称的事**：legacy reader **不**重新校验历史字段集。narr-3 的字段集与 current 明确
不同（`section_result_id` / `claim_ids` / `paragraphs` …），而 narr-4 / narr-5 / narr-6 的
`SectionDraft` **字段集相同**（§三 C 的 wire 变化在 `NarrativeSentence` 与门后束，不在候选束），
三者的形状差别是 wire family marker 本身、相对 narr-3 的 `section_result_id` 负向断言、
相对 narr-7 的 `natural_prose_draft` 负向断言，以及（narr-5 ↔ narr-6）`locator_ref` 的形状。
narr-6 ↔ narr-7 的差别是 `natural_prose_draft` **在不在**；narr-7 ↔ narr-8 的差别是草稿单元的
出处轴上 `source_fact_refs` **这个键在不在**（不是「有但为空」——见 §5d 对夹具本身的断言）。
**没有 proposal 的载荷里根本没有 locator 可判**，因此那种载荷上 narr-4/5/6 在形状上不可分——
真正把它们分开的是单版本 marker 精确比对；这一点如实写在下面，不靠编出来的判别式假装可分。
历史载荷的字段保真度由磁盘上的真实记录 + store 的 migration/readback eval 负责，不由本模块负责。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import narrative_schema as NS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

#: M930-3 唯一一次真实纵向跑的 draft 束（离线只读；缺文件时本模块 SKIP，不伪造）。
_FROZEN_DRAFTS = (Path(__file__).resolve().parent.parent / "evaluation" / "results"
                  / "m930_3_acceptance_crossdoc_real_r7b" / "section_drafts.json")


def skip(msg: str) -> None:
    _results["skipped"] += 1
    _results["details"].append(f"SKIP: {msg}")


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str) -> None:
    try:
        fn()
    except NS.NarrativeSchemaError as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望拒绝理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 NarrativeSchemaError，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


def _narr3_payload(draft_id: str = "sdraft_legacy_1",
                   section_result_id: str = "sr_legacy_1") -> dict:
    """narr-3 `SectionDraft` 载荷的最小真实形状。

    narr-3 的 draft **携带** `section_result_id`（Draft ↔ Result 双向成环）；narr-4 去掉了
    该字段，所以这一项既是 legacy 的识别特征，也是 current reader 必须拒绝的原因之一。
    """
    return {
        "schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR3,
        "draft_id": draft_id,
        "section_result_id": section_result_id,
        "task_id": "task_legacy_1",
        "section_id": "company",
        "company_id": "C1",
        "report_as_of": "2025-12-31",
        "contract_version": "v2",
        "contract_fingerprint": "cfp_legacy",
        "producer_kind": "section_writer",
        "writer_policy_version": "wp-legacy",
        "writer_rules_version": "wr-legacy",
        "writer_renderer_version": "wren-legacy",
        "prompt_version": "prompt-legacy",
        "model_policy": "mp-legacy",
        "authority_container_ids": [],
        "claim_ids": ["c_legacy_1"],
        "support_refs": [],
        "dispositions": [],
        "paragraphs": [],
        "tables": [],
        "unresolved_ids": [],
        "unresolved_projections": [],
        "coverage_summary": {},
        "conflict_projections": [],
        "not_found_projections": [],
        "dependency_fingerprint": "dfp_legacy",
        "created_at": "2026-01-01T00:00:00Z",
        "title": "公司概况",
    }


def _narr5_wire_ref(locator_value) -> dict:
    """一条 narr-5 形态的 `ProposedSupportRef`：字段集与 current 相同，locator 是**裸三元组**。"""
    ref = NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id="cc_legacy_1",
        draft_revision="dr_legacy_1", manifest_id="wmm_legacy_1", manifest_fingerprint="mfp_legacy",
        authority_kind="topic_pack", authority_container_id="tp_legacy_1",
        source_identity="src_legacy_1", provenance_identity="prov_legacy_1",
        support_role="primary", support_semantics="factual",
        authorization_path="path_b_material_derived", content_fingerprint="cfp_legacy_1",
        dependency_fingerprint="dfp_legacy_1", material_id="m_legacy_1",
        payload_ref={"object_type": "research_material", "object_id": "m_legacy_1",
                     "version": "v1"},
        locator_ref=NS.char_range_locator("evidence:ev1", 0, 40))
    body = ref.to_dict()
    body["locator_ref"] = locator_value  # 退回 narr-5 的裸三元组 wire（loc-1 之前的样子）
    return body


def _narr5_payload(locator_value=("evidence:ev1", 0, 40)) -> dict:
    """narr-5 `SectionDraft` 载荷的真实形状。

    造法刻意是「current 写侧工厂产出的候选束 → 只把 `locator_ref` 退回裸三元组 + 翻转 marker」：
    narr-5 与 narr-6 的**字段集**完全相同，唯一差别就是 locator 的 wire 形状（§四.5），所以这样
    造出的载荷就是历史 wire 的真形态，而不是手写 guess。
    """
    payload = _current_legal_draft().to_dict()
    payload["schema_version"] = NS.LEGACY_NARRATIVE_SCHEMA_NARR5
    payload["proposed_support_refs"] = [_narr5_wire_ref(locator_value)]
    return payload


def _strip_locator_refs(value):
    """递归抹掉所有 `locator_ref`（用于「审计视图只在这里与原始载荷不同」的比对）。"""
    if isinstance(value, dict):
        return {k: _strip_locator_refs(v) for k, v in value.items() if k != "locator_ref"}
    if isinstance(value, list):
        return [_strip_locator_refs(v) for v in value]
    return value


def _narr7_payload() -> dict:
    """narr-7 `SectionDraft` 载荷的真实形状（门前自然草稿层**在场**，但出处只有材料一条轴）。

    造法与 `_narr5_payload` 同一套路：current 写侧工厂产出一个**材料轴**草稿单元，再把 narr-8
    才有的那条轴键 `source_fact_refs` **删掉**——narr-7 的单元上根本没有这个键，留着它（哪怕是
    空表）就不是历史 wire 的形状了，narr-7 reader 也正是靠「这个键在不在」区分两个版本。

    单元身份 `prose_unit_id` 仍是按**当前**规则派生的：legacy 视图是**只读回放**，不重算身份
    （下面 §5d 把这条作为断言钉住，而不是把它当成缺陷藏起来）。
    """
    unit = NS.NaturalProseDraftUnit.create(
        index=0, draft_revision="dr_legacy_7", section_id="company",
        text="公司主营业务由动力电池与储能两大条线构成。",
        source_member_refs=[NS.manifest_member_ref("pack-legacy-7", "m-legacy-7")],
        atom_candidate_ids=["cc_legacy_7"])
    unit_body = unit.to_dict()
    unit_body.pop("source_fact_refs")
    return {"schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR7,
            "draft_id": "sdraft_legacy_7",
            "natural_prose_draft": [unit_body]}


def _current_legal_draft() -> NS.SectionDraft:
    """current 写侧工厂造出的最小合法 `SectionDraft`（下面前提/反例的基线）。"""
    return NS.SectionDraft.create(
        task_id="task_1", section_id="company", company_id="C1", report_as_of="2025-12-31",
        contract_version="v2", contract_fingerprint="cfp", producer_kind="topic_harness",
        writer_policy_version="wp", prompt_version="p", model_policy="mp",
        authority_container_ids=(), material_manifest=NS.WriterMaterialManifest.create(members=()),
        material_dispositions=(), claim_candidates=(), narrative_draft_units=(),
        proposed_support_refs=(), unresolved_ids=("u1",), dependency_fingerprint="d")


def main() -> dict:
    # ------------------------------------------------------------------
    # 1. 登记面与「单版本 reader」纪律。
    # ------------------------------------------------------------------
    check(NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS
          == ("narr-3", "narr-4", "narr-5", "narr-6", "narr-7")
          and NS.NARRATIVE_SCHEMA_VERSION == "narr-8"
          and NS.NARRATIVE_SCHEMA_VERSION not in NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS,
          f"narr-3 / narr-4 / narr-5 / narr-6 / narr-7 是登记 legacy 版本，narr-8 是 current"
          f"（互不重叠）：实际 current={NS.NARRATIVE_SCHEMA_VERSION!r} "
          f"legacy={NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS!r}")
    check(NS.LEGACY_NARRATIVE_SCHEMA_NARR3 == NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS[0]
          and NS.LEGACY_NARRATIVE_SCHEMA_NARR4 == NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS[1]
          and NS.LEGACY_NARRATIVE_SCHEMA_NARR5 == NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS[2]
          and NS.LEGACY_NARRATIVE_SCHEMA_NARR6 == NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS[3]
          and NS.LEGACY_NARRATIVE_SCHEMA_NARR7 == NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS[4]
          and len(set(NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS)) == 5,
          "五个 legacy 版本常量必须与登记元组逐项一致（reader 用常量精确比对，元组不得漂移）")

    # ------------------------------------------------------------------
    # 2. narr-3 载荷只读还原（#17）。
    # ------------------------------------------------------------------
    payload = _narr3_payload()
    view = NS.load_legacy_narrative_narr3_for_audit(payload)
    check(isinstance(view, NS.Narr3SectionDraftView)
          and view.schema_version == "narr-3"
          and view.draft_id == payload["draft_id"]
          and view.section_result_id == payload["section_result_id"],
          "narr-3 载荷经 legacy reader 只读还原（draft_id / section_result_id 如实保留）")
    check(view.to_dict() == payload,
          "legacy 视图 to_dict 与载荷逐字段一致（不改写、不升级）")
    check(not isinstance(view, NS.SectionDraft),
          "legacy 视图**不是** current SectionDraft（类型层不可冒充）")

    # ------------------------------------------------------------------
    # 3. narr-3 reader 的 fail-closed 边界（含跨 legacy 版本）。
    # ------------------------------------------------------------------
    expect_raises("缺 marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      {k: v for k, v in payload.items() if k != "schema_version"}),
                  "不是登记的 narr-3 legacy 载荷")
    expect_raises("narr-6 current marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      {**payload, "schema_version": NS.NARRATIVE_SCHEMA_VERSION}),
                  "不是登记的 narr-3 legacy 载荷")
    expect_raises("缺 section_result_id 的 narr-3 载荷",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      {k: v for k, v in payload.items() if k != "section_result_id"}),
                  "section_result_id")
    expect_raises("非对象载荷",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      json.dumps(payload)),
                  "必须是 JSON 对象")
    expect_raises("空 section_result_id",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      {**payload, "section_result_id": ""}),
                  "section_result_id")
    # 跨 legacy 版本：narr-4 载荷**也是**登记的 legacy 版本，但 narr-3 reader 是单版本 reader。
    # 这一条防的是「legacy 集增长 → 旧 reader 用集合成员判定 → 静默多吞一版」这类缺陷。
    expect_raises("narr-4 marker 的载荷（跨 legacy 版本不得互相宽容）",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      {**payload, "schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR4}),
                  "不是登记的 narr-3 legacy 载荷")
    expect_raises("narr-4 marker 且不带 section_result_id 的载荷",
                  lambda: NS.load_legacy_narrative_narr3_for_audit(
                      {k: v for k, v in payload.items()
                       if k not in ("schema_version", "section_result_id")}
                      | {"schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR4}),
                  "不是登记的 narr-3 legacy 载荷")

    # ------------------------------------------------------------------
    # 4. narr-4 reader：只读还原 + 与 narr-3 对称的 fail-closed 边界。
    # ------------------------------------------------------------------
    # narr-4 与 narr-5 的候选束字段集相同（见模块 docstring），所以一份「current 写侧工厂产出、
    # 只翻转 marker」的载荷就是合法的 narr-4 载荷形态。
    current_payload = _current_legal_draft().to_dict()
    narr4_payload = {**current_payload, "schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR4}
    view4 = NS.load_legacy_narrative_narr4_for_audit(narr4_payload)
    check(isinstance(view4, NS.Narr4SectionDraftView)
          and view4.schema_version == "narr-4"
          and view4.draft_id == narr4_payload["draft_id"],
          "narr-4 载荷经 legacy reader 只读还原（draft_id 如实保留）")
    check(view4.to_dict() == narr4_payload,
          "narr-4 legacy 视图 to_dict 与载荷逐字段一致（不改写、不升级）")
    check(not isinstance(view4, NS.SectionDraft),
          "narr-4 legacy 视图**不是** current SectionDraft（类型层不可冒充）")
    expect_raises("narr-4 reader 读缺 marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr4_for_audit(
                      {k: v for k, v in narr4_payload.items() if k != "schema_version"}),
                  "不是登记的 narr-4 legacy 载荷")
    expect_raises("narr-4 reader 读 narr-3 marker 的载荷（字段集合法也不行）",
                  lambda: NS.load_legacy_narrative_narr4_for_audit(
                      {**current_payload, "schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR3}),
                  "不是登记的 narr-4 legacy 载荷")
    expect_raises("narr-4 reader 读 narr-6 current marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr4_for_audit(
                      {**current_payload, "schema_version": NS.NARRATIVE_SCHEMA_VERSION}),
                  "不是登记的 narr-4 legacy 载荷")
    expect_raises("narr-4 reader 读带 section_result_id 的载荷（那是 narr-3 的形状）",
                  lambda: NS.load_legacy_narrative_narr4_for_audit(
                      {**narr4_payload, "section_result_id": "sr_x"}),
                  "不得携带 section_result_id")
    expect_raises("narr-4 reader 读非对象载荷",
                  lambda: NS.load_legacy_narrative_narr4_for_audit(
                      json.dumps(narr4_payload)),
                  "必须是 JSON 对象")

    # ------------------------------------------------------------------
    # 5. narr-5 reader：只读还原 + 裸三元组 locator 读成 `loc-0` 审计形态（§四.5）。
    # ------------------------------------------------------------------
    # narr-6 把 `locator_ref` 从裸三元组升为 `loc-1` tagged union。§四.5 要求：wire 字段改变
    # 必须显式升版 **并提供 legacy audit reader**，而 legacy 载荷不得被静默重解释成新 wire。
    # 这一节钉的就是这句话：narr-5 载荷里的三元组只被读成 `char_range`，且产物带 `loc-0`
    # 标签——因此它进不了任何 current 决定。
    narr5_payload = _narr5_payload()
    view5 = NS.load_legacy_narrative_narr5_for_audit(narr5_payload)
    check(isinstance(view5, NS.Narr5SectionDraftView)
          and view5.schema_version == "narr-5"
          and view5.draft_id == narr5_payload["draft_id"],
          "narr-5 载荷经 legacy reader 只读还原（draft_id 如实保留）")
    check(not isinstance(view5, NS.SectionDraft),
          "narr-5 legacy 视图**不是** current SectionDraft（类型层不可冒充）")
    _read5 = view5.to_dict()
    _audit_loc = _read5["proposed_support_refs"][0]["locator_ref"]
    check(_audit_loc.get("locator_schema") == NS.LEGACY_LOCATOR_SCHEMA_VERSION
          and _audit_loc.get("locator_kind") == "char_range"
          and (_audit_loc.get("owner"), _audit_loc.get("start"), _audit_loc.get("end"))
          == ("evidence:ev1", 0, 40),
          f"narr-5 裸三元组只被读成 `loc-0` 的 char_range（不猜闭块区间）：实际 {_audit_loc!r}")
    expect_raises("`loc-0` 审计 locator 回流进 current 决定",
                  lambda: NS.validate_locator(_audit_loc, "audit_view"), "loc-0")
    check(_strip_locator_refs(_read5) == _strip_locator_refs(narr5_payload)
          and _read5["proposed_support_refs"][0]["locator_ref"]
          != narr5_payload["proposed_support_refs"][0]["locator_ref"],
          "审计视图与原始载荷**只在** locator_ref 形状上不同（其余字段逐字回放，不改写、不升级）")
    check(narr5_payload["proposed_support_refs"][0]["locator_ref"] == ("evidence:ev1", 0, 40),
          "只读转换不得改写磁盘载荷本身（原始三元组仍是三元组）")
    # 闭块区间在 legacy wire 上不可判定：`(0, 0)` 读不出来时**原样保留**，绝不推断成单块。
    _zero_view = NS.load_legacy_narrative_narr5_for_audit(
        _narr5_payload(locator_value=("evidence:ev1", 0, 0)))
    _zero_loc = _zero_view.to_dict()["proposed_support_refs"][0]["locator_ref"]
    check(_zero_loc == ("evidence:ev1", 0, 0),
          f"读不出的 legacy locator 原样保留（`(0,0)` 不得被推断成闭块区间或 char_range）："
          f"实际 {_zero_loc!r}")
    # 单版本 reader 的 fail-closed 边界。
    expect_raises("narr-5 reader 读缺 marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr5_for_audit(
                      {k: v for k, v in narr5_payload.items() if k != "schema_version"}),
                  "不是登记的 narr-5 legacy 载荷")
    for _other in (NS.LEGACY_NARRATIVE_SCHEMA_NARR3, NS.LEGACY_NARRATIVE_SCHEMA_NARR4,
                   NS.NARRATIVE_SCHEMA_VERSION):
        expect_raises(f"narr-5 reader 读 marker={_other} 的载荷（字段集合法也不行）",
                      lambda v=_other: NS.load_legacy_narrative_narr5_for_audit(
                          {**narr5_payload, "schema_version": v}),
                      "不是登记的 narr-5 legacy 载荷")
    expect_raises("narr-5 reader 读带 section_result_id 的载荷（那是 narr-3 的形状）",
                  lambda: NS.load_legacy_narrative_narr5_for_audit(
                      {**narr5_payload, "section_result_id": "sr_x"}),
                  "不得携带 section_result_id")
    expect_raises("narr-5 reader 读非对象载荷",
                  lambda: NS.load_legacy_narrative_narr5_for_audit(json.dumps(narr5_payload)),
                  "必须是 JSON 对象")
    # narr-5 的形状标记：succeeded 六类对象在场（用来与 narr-4 区分）。
    expect_raises("narr-5 reader 读 proposed_support_refs 为空的载荷",
                  lambda: NS.load_legacy_narrative_narr5_for_audit(
                      {**narr5_payload, "proposed_support_refs": []}),
                  "proposed_support_refs")

    # ------------------------------------------------------------------
    # 5b. narr-6 reader：逐字回放 + 「拒绝 narr-7 形状」这条新边界（指令 E 第 3 项）。
    # ------------------------------------------------------------------
    # narr-6 与 narr-7 的 `SectionDraft` 字段集差别**只有一个**新增字段 `natural_prose_draft`；
    # 因此这一节的要点不是「能读」，而是**不许把带草稿层的载荷当 narr-6 读进来**——那等于把
    # 门前自然草稿静默丢掉，而 `draft_revision` 是按带草稿层算出来的，读的人会拿到一束
    # 「修订号对不上内容」的记录。
    narr6_payload = {**current_payload, "schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR6}
    view6 = NS.load_legacy_narrative_narr6_for_audit(narr6_payload)
    check(isinstance(view6, NS.Narr6SectionDraftView)
          and view6.schema_version == "narr-6"
          and view6.draft_id == narr6_payload["draft_id"],
          "narr-6 载荷经 legacy reader 只读还原（draft_id 如实保留）")
    check(view6.to_dict() == narr6_payload,
          "narr-6 legacy 视图 to_dict 与载荷**逐字**一致（locator 已是 loc-1，不做任何转换）")
    check(not isinstance(view6, NS.SectionDraft),
          "narr-6 legacy 视图**不是** current SectionDraft（类型层不可冒充）")
    expect_raises("narr-6 reader 读缺 marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr6_for_audit(
                      {k: v for k, v in narr6_payload.items() if k != "schema_version"}),
                  "不是登记的 narr-6 legacy 载荷")
    expect_raises("narr-6 reader 读带 natural_prose_draft 的载荷（那是 narr-7 的形状）",
                  lambda: NS.load_legacy_narrative_narr6_for_audit(
                      {**narr6_payload, "natural_prose_draft": [{"prose_unit_id": "npdu_x"}]}),
                  "natural_prose_draft")
    expect_raises("narr-6 reader 读带 section_result_id 的载荷（那是 narr-3 的形状）",
                  lambda: NS.load_legacy_narrative_narr6_for_audit(
                      {**narr6_payload, "section_result_id": "sr_x"}),
                  "不得携带 section_result_id")
    expect_raises("narr-6 reader 读只有 marker 没有内容的载荷（marker 不得充当内容）",
                  lambda: NS.load_legacy_narrative_narr6_for_audit(
                      {k: v for k, v in narr6_payload.items()
                       if k in ("schema_version", "draft_id")}),
                  "空载荷不是本节记录")
    expect_raises("narr-6 reader 读非对象载荷",
                  lambda: NS.load_legacy_narrative_narr6_for_audit(json.dumps(narr6_payload)),
                  "必须是 JSON 对象")
    # **不**要求 proposal 非空：真实冻结工件里 industry 束就是 0 proposal + 4 unresolved。
    _unresolved_only = {k: v for k, v in
                        {**current_payload,
                         "schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR6}.items()
                        if k not in ("claim_candidates", "proposed_support_refs",
                                     "narrative_draft_units")}
    _unresolved_only["unresolved_ids"] = ["u1"]
    _view_unresolved = NS.load_legacy_narrative_narr6_for_audit(_unresolved_only)
    check(_view_unresolved.to_dict() == _unresolved_only,
          "narr-6 legacy 载荷可以是「只有 unresolved、没有 proposal」的真实失败形状"
          "（r7b industry 束即如此；要求 proposal 非空会把真实记录挡在门外）")
    # locator 形状：payload 里**已携带**的 locator 必须已是 loc-1（裸三元组 = narr-5）。
    expect_raises("narr-6 reader 读 locator 仍是裸三元组的载荷（那是 narr-5 的形状）",
                  lambda: NS.load_legacy_narrative_narr6_for_audit(
                      {**narr6_payload,
                       "proposed_support_refs": [_narr5_wire_ref(("evidence:ev1", 0, 40))]}),
                  "不是 `loc-1`")

    # ------------------------------------------------------------------
    # 5c. 跨 legacy 版本互不宽容：legacy 集每涨一版，旧 reader 都不得开始吞新版本
    #     （3 → 4 涨的是 narr-6，4 → 5 涨的是 narr-7；判据同一处，不随版本数变形）。
    # ------------------------------------------------------------------
    for _label, _reader, _own in (
            ("narr-3", NS.load_legacy_narrative_narr3_for_audit,
             NS.LEGACY_NARRATIVE_SCHEMA_NARR3),
            ("narr-4", NS.load_legacy_narrative_narr4_for_audit,
             NS.LEGACY_NARRATIVE_SCHEMA_NARR4),
            ("narr-5", NS.load_legacy_narrative_narr5_for_audit,
             NS.LEGACY_NARRATIVE_SCHEMA_NARR5),
            ("narr-6", NS.load_legacy_narrative_narr6_for_audit,
             NS.LEGACY_NARRATIVE_SCHEMA_NARR6),
            ("narr-7", NS.load_legacy_narrative_narr7_for_audit,
             NS.LEGACY_NARRATIVE_SCHEMA_NARR7)):
        for _other in (*NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS, NS.NARRATIVE_SCHEMA_VERSION):
            if _other == _own:
                continue
            expect_raises(f"{_label} reader 读 marker={_other} 的载荷（单版本 reader 不得互相宽容）",
                          lambda r=_reader, v=_other: r({**current_payload,
                                                         "schema_version": v}),
                          f"不是登记的 {_label} legacy 载荷")

    # ------------------------------------------------------------------
    # 5d. narr-7 reader：草稿层在场、出处只有材料一条轴。
    # ------------------------------------------------------------------
    # 这一版与 narr-6 的分界**不是**「有没有草稿层」（那是 narr-7 自己的形状标记，本 reader 必须
    # 接受它），而是「草稿单元的出处落在哪条轴上」：narr-8 的 `source_fact_refs` 一旦出现，
    # 这份载荷就不是 narr-7 了——本 reader 必须拒，而不是「忽略未知字段」地吞下。
    narr7_payload = _narr7_payload()
    view7 = NS.load_legacy_narrative_narr7_for_audit(narr7_payload)
    check(view7.schema_version == "narr-7" and view7.draft_id == narr7_payload["draft_id"],
          "narr-7 载荷经 legacy reader 只读还原（draft_id 如实保留）")
    check(view7.to_dict() == narr7_payload,
          "narr-7 legacy 视图 to_dict 与载荷**逐字**一致（含单元身份：本层不重算 id）")
    check(not isinstance(view7, NS.SectionDraft),
          "narr-7 legacy 视图**不是** current SectionDraft（类型层不可冒充）")
    _unit7 = narr7_payload["natural_prose_draft"][0]
    check("source_fact_refs" not in _unit7 and _unit7["source_member_refs"],
          "夹具确是两个版本之间的**真**形态差异：单元上**没有** `source_fact_refs` 这个键"
          "（不是「有但为空」），且材料轴非空")
    expect_raises("narr-7 reader 读带 source_fact_refs 的单元（那是 narr-8 的形状）",
                  lambda: NS.load_legacy_narrative_narr7_for_audit(
                      {**narr7_payload,
                       "natural_prose_draft": [dict(_unit7, source_fact_refs=["fprov_x"])]}),
                  "narr-8 的形状")
    expect_raises("narr-7 reader 读材料轴为空的单元（narr-7 里出处只有材料一条轴）",
                  lambda: NS.load_legacy_narrative_narr7_for_audit(
                      {**narr7_payload,
                       "natural_prose_draft": [dict(_unit7, source_member_refs=[])]}),
                  "非空 source_member_refs")
    expect_raises("narr-7 reader 读缺 marker 的载荷",
                  lambda: NS.load_legacy_narrative_narr7_for_audit(
                      {k: v for k, v in narr7_payload.items() if k != "schema_version"}),
                  "不是登记的 narr-7 legacy 载荷")
    expect_raises("narr-7 reader 读带 section_result_id 的载荷（那是 narr-3 的形状）",
                  lambda: NS.load_legacy_narrative_narr7_for_audit(
                      {**narr7_payload, "section_result_id": "sr_x"}),
                  "section_result_id")
    expect_raises("narr-7 reader 读只有 marker 的载荷（marker 不得充当内容）",
                  lambda: NS.load_legacy_narrative_narr7_for_audit(
                      {"schema_version": NS.LEGACY_NARRATIVE_SCHEMA_NARR7, "draft_id": "d"}),
                  "不得以 marker 充当内容")
    expect_raises("narr-7 reader 读非对象载荷",
                  lambda: NS.load_legacy_narrative_narr7_for_audit(json.dumps(narr7_payload)),
                  "必须是 JSON 对象")

    # ------------------------------------------------------------------
    # 6. narr-8 current reader 拒绝五个 legacy 版本（一严一松反例，§16.10 #17）。
    # ------------------------------------------------------------------
    # narr-3 载荷先被字段白名单挡下（`section_result_id` / `claim_ids` / `paragraphs` … 都是
    # narr-3 独有、current 禁有的字段）。这一条本身**不足以**证明 marker 被检查：白名单先跑，
    # 下面再用「字段集完全合法、只把 marker 换成 legacy」的载荷把 marker 检查单独钉住。
    expect_raises("current draft reader 读 narr-3 载荷",
                  lambda: NS.SectionDraft.from_dict(payload), "section_result_id")
    _stripped = {k: v for k, v in payload.items() if k != "section_result_id"}
    expect_raises("current draft reader 读去环后的 narr-3 载荷（残留字段走白名单）",
                  lambda: NS.SectionDraft.from_dict(_stripped), "未登记字段")
    # marker 本身必须被拒：字段集逐字合法（由 current 自己的写侧工厂造出，再只翻转 marker）。
    check(_current_legal_draft().schema_version == NS.NARRATIVE_SCHEMA_VERSION,
          "current 最小合法 draft 可构造（下两条反例的基线，反例不是恒失败）")
    for _legacy in NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS:
        expect_raises(f"current draft reader 读「字段集合法、marker={_legacy}」的载荷",
                      lambda v=_legacy: NS.SectionDraft.from_dict(
                          {**current_payload, "schema_version": v}),
                      "narr-8")
    # 显式构造器同样拒（不能只靠 from_dict 的字段白名单）。
    for _legacy in NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS:
        expect_raises(f"current draft 构造器吃 {_legacy} marker",
                      lambda v=_legacy: NS.SectionDraft(
                          schema_version=v, draft_id="sdraft_x", draft_revision="dr_x",
                          task_id="t1", section_id="company", company_id="C1",
                          report_as_of="2025-12-31", contract_version="v2",
                          contract_fingerprint="cfp", producer_kind="section_writer",
                          writer_policy_version="wp", prompt_version="p", model_policy="mp",
                          authority_container_ids=(), material_manifest=NS.WriterMaterialManifest.create(members=()),
                          material_dispositions=(), claim_candidates=(),
                          narrative_draft_units=(), proposed_support_refs=(), unresolved_ids=(),
                          unresolved_projections=(), coverage_summary={},
                          conflict_projections=(), not_found_projections=(),
                          dependency_fingerprint="d"),
                      "narr-8")

    # ------------------------------------------------------------------
    # 7. current draft 的 identity 里没有 Result 反向边（#16 方向）。
    # ------------------------------------------------------------------
    check(not any(f.startswith("section_result")
                  for f in NS.SectionDraft.__dataclass_fields__),
          "current SectionDraft 无 section_result_id 字段（Result→Draft 单向）")
    check(not hasattr(NS.Narr3SectionDraftView, "identity_body")
          and not hasattr(NS.Narr4SectionDraftView, "identity_body")
          and not hasattr(NS.Narr5SectionDraftView, "identity_body")
          and not hasattr(NS.Narr6SectionDraftView, "identity_body")
          and not hasattr(NS.Narr7SectionDraftView, "identity_body"),
          "五个 legacy 视图都不参与 current 身份派生（无 identity_body）")

    # ------------------------------------------------------------------
    # 8. 真实冻结工件（离线只读，零调用）：narr-6 reader 必须能读回 M930-3 r7b 的真跑记录。
    # ------------------------------------------------------------------
    # 这一节存在的理由：上面每一份载荷都是**构造**出来的。构造的东西只能证明「按我理解的形状
    # 能读」，证明不了「磁盘上真有的东西能读」。r7b 是唯一一次真实纵向跑，它的两束 draft 都还是
    # narr-6；它们必须能经 legacy reader 原样读回，且必须**不能**经 current reader 读成 narr-8。
    if not _FROZEN_DRAFTS.exists():
        skip(f"冻结 r7b 工件不在场（{_FROZEN_DRAFTS.name}）：本轮不宣称真实载荷可读")
    else:
        frozen = json.loads(_FROZEN_DRAFTS.read_text(encoding="utf-8"))
        check(isinstance(frozen, dict) and bool(frozen),
              "冻结 r7b 工件含至少一束 section draft（JSON 对象非空）")
        for _key, _payload in sorted(frozen.items()):
            check(_payload.get("schema_version") == NS.LEGACY_NARRATIVE_SCHEMA_NARR6,
                  f"冻结 r7b 的 {_key} 束 marker 是 narr-6（历史事实，不得被改写）")
            _fview = NS.load_legacy_narrative_narr6_for_audit(_payload)
            check(_fview.to_dict() == _payload,
                  f"冻结 r7b 的 {_key} 束经 narr-6 reader 逐字回放（不改写、不升级）")
            expect_raises(f"current reader 读冻结 r7b 的 {_key} 束（必须拒绝，不得宽松降级）",
                          lambda p=_payload: NS.SectionDraft.from_dict(p), "narr-8")
        _shapes = {k: (len(v.get("proposed_support_refs") or ()),
                       len(v.get("unresolved_ids") or ()))
                   for k, v in frozen.items()}
        check(any(_p == 0 for _p, _ in _shapes.values())
              and any(_p > 0 for _p, _ in _shapes.values()),
              f"真实工件里「有 proposal 的束」与「0 proposal、只有 unresolved 的束」都在场"
              f"（形状：{_shapes}），因此上面那条「0 proposal 载荷必须能读回」有真实依据，"
              "不是为了让断言通过而构造出来的反例")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

"""Eval: M930-3 `ndc-2` —— 合格事实 → **唯一材料** 的消歧（`mbind-1`）。

用法: python -m evals.test_m930_3_material_disambiguation

**这条链此前断在「材料绑定的两个面用了两套口径」上。** 采纳侧按**本 aspect 的材料集**解析
（`topic_runtime._resolve_material_for_citation`），写作侧 `pack_writer.scan_topic_pack` 却按
**整 Pack 的** `material_index` 解析：同一份 Pack 在采纳时唯一、到写作时变成「同来源身份同页
三份候选」⇒ 整节在 `scan_topic_pack` 里 fail-closed，一个字都写不出来。

本批（方案 A，**不改 `CitationRef` wire**）补的是「读出来的收窄」，不是「挑出来的一个」：

1. **唯一收窄**（`mbind-1`）：同一个当前 Pack 内先把 `SupportedFact → FactCandidate → eligible
   FactQualificationDecision` 这条资格链**逐环重算**（三个 digest 与
   `pack_set._check_decision_inputs` 同一口径），取出该事实**已核验的输入材料**；它是**单元素**
   时才用它与「引用的类型化来源身份 + 页号」选出的候选**求交**。收窄依据是事实自己的资格链，
   不是候选顺序、不是评分、不是「最后写入者胜出」。
2. **判据不放宽**：交集为空、候选多项、错页、跨 Pack、身份不一致、决定与候选不一致、决定
   非 `eligible`、三个 digest 重算不符 —— 一律**不绑定**（该拒的仍然拒）。空收窄集 ⇒ 当场抛
   `MaterialBindingAmbiguityError`，绝不退回去任选一个；`for_support_edge` 也不是开关。
3. **两侧同源**：`scan_topic_pack` 与独立支撑门 `_authority_material_expectations` 必须给出
   **同一个**绑定，否则一边放行、另一边仍报歧义。

公司无关：本模块没有公司代号、文件名、页码、表号或答案数字的生产字面量；夹具的「公司」与
「业务描述」逐字取自既有 `test_demo_pack_writer` 夹具（不新造第二套身份域）。不调 LLM、不联网、
不写库、不建第二套 Pack/Writer。

**边界如实登记**：`_resolve_material_binding` 的「空交集 ⇒ 抛」这条防护，在当前生产路径上
**不可达** —— 上游的资格链闭合检查（输入 material 必须在 Pack 内、来源身份必须与 fact 一致）
已经先一步把「事实的输入材料不在引用候选集里」这个形态挡掉了，`fact_verified_input_material_ids`
因此返回 `None` 而不是一个对不上的收窄集，绑定退回旧规则（同页多候选仍拒）。§3 直接对这条
防护做单元测试，并把「不可达」明确写出来，而不是伪造一个能触发的生产输入。
"""

from __future__ import annotations

import dataclasses as dc
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T               # noqa: E402
from harness import topic_schema as TS                     # noqa: E402
from sections import narrative_schema as NS                # noqa: E402
from sections import pack_writer as PW                     # noqa: E402

#: 来源身份（与夹具 `_citation()` 落在同一身份域：`evidence:<裸 id>`）。
IDENT = f"evidence:{T.EV_ID}"
ASP = T.ASP_BUSINESS_MAIN
TOPIC = T.TOPIC_BUSINESS
#: 纯业务描述，无高风险表面（路径 B 夹具同理）。
TEXT = "公司主营业务为动力电池系统的研发、生产与销售。"


# ---------------------------------------------------------------------------
# 夹具

def _authority(task_id: str, *, materials, facts):
    """一个单 topic 的公司节权威输入（复用 `test_demo_pack_writer` 的物化路径，不另立一套）。"""
    task = T._task("company", (TOPIC,), task_id=task_id)
    authority = T._company_authority(
        task, facts=tuple(facts), materials=tuple(materials),
        aspects=(T._AspectResult(ASP, "covered"),),
        requirements=(T._Req(TOPIC, (T._aspect(ASP, TOPIC, T._question_id(TOPIC)),)),))
    return task, authority


def _pack(authority):
    return authority.pack_set.packs[0]


def _container(authority) -> str:
    return str(_pack(authority).pack_id)


def _materials_by_id(pack) -> dict:
    return {str(m.material_id): m for m in pack.materials}


def _holder(packs):
    """只有 `producer_kind` + `pack_set.packs` 的权威替身（**改不动**真实 `VerifiedPackSet`
    时用：它已经物化完毕，重算会连带换掉 pack 内容身份，那就不再是「同一份 Pack 的残缺形态」）。"""
    holder = type("_Authority", (), {})()
    holder.producer_kind = "topic_harness"
    holder.pack_set = type("_PackSet", (), {})()
    holder.pack_set.packs = tuple(packs)
    return holder


def _chain(pack, fact):
    """`(candidate, decision)` —— 该事实资格链的两环（按 id 逐字取，不做模糊匹配）。"""
    cand = next(c for c in pack.fact_candidates
                if str(c.candidate_id) == str(fact.candidate_id))
    dec = next(d for d in pack.fact_qualification_decisions
               if str(d.decision_id) == str(fact.qualification_decision_id))
    return cand, dec


def _broken_chain(pack, fact, *, material_ids=None, verdict=None, **decision_overrides):
    """把资格链**一环**做残 → `(只读 pack 替身, 指向它的残 fact)`；其余字段逐字不动。

    两个字段是**内容寻址**的，改不得也硬改不了：

    * `FactCandidate.candidate_id`（含 `material_ids`）⇒ 改候选声明的输入材料只能经
      `TS.build_fact_candidate` 重造；
    * `FactQualificationDecision.decision_id` 由 `(candidate_id, revision, kind, verdict,
      rules_version)` 派生 ⇒ 换了候选或换了 verdict 就必须经
      `TS.build_qualification_decision` 重建（`rejected` 还强制要 `rejection_reason`）。

    其余字段（三个 digest、`input_source_identity`、`input_material_ids`）不进决定身份，
    可以 `dataclasses.replace` 直接改 —— 正是「决定与候选对不上」的那一格。

    Pack 一侧用**只读替身**而不是 `dataclasses.replace(真 pack)`：真 Pack 的 `pack_id` 也是
    内容寻址的，重造会连带换掉它的内容身份，那就不再是「同一份 Pack 的残缺形态」了。被测函数
    （`fact_verified_input_material_ids` / `_authority_material_expectations`）只读
    `fact_candidates` / `fact_qualification_decisions` / `materials` / `pack_id` 四个字段。
    """
    cand, dec = _chain(pack, fact)
    if material_ids is not None:
        cand = TS.build_fact_candidate(
            candidate_source_kind=str(cand.candidate_source_kind), statement=str(cand.statement),
            fact_type=str(cand.fact_type), aspect_ids=tuple(cand.aspect_ids),
            question_ids=tuple(cand.question_ids), material_ids=tuple(material_ids),
            period=cand.period, scope=cand.scope)
    if material_ids is not None or verdict is not None:
        rejected = (verdict or dec.verdict) == "rejected"
        fields = {
            "input_identity_digest": str(dec.input_identity_digest),
            "input_source_identity": str(dec.input_source_identity),
            "input_locator_digest": str(dec.input_locator_digest),
            "input_payload_digest": str(dec.input_payload_digest),
        }
        for key in fields:
            if key in decision_overrides:
                fields[key] = decision_overrides.pop(key)
        dec = TS.build_qualification_decision(
            cand, verdict=str(verdict or dec.verdict), rules_version=str(dec.rules_version),
            rejection_reason=(dec.rejection_reason or "夹具：verdict 被改成非 eligible")
            if rejected else None, **fields)
    if decision_overrides:
        dec = dc.replace(dec, **decision_overrides)
    broken_fact = dc.replace(
        fact, candidate_id=cand.candidate_id, candidate_revision=cand.candidate_revision,
        qualification_decision_id=dec.decision_id)
    holder_pack = SimpleNamespace(
        pack_id=str(pack.pack_id), fact_candidates=(cand,),
        fact_qualification_decisions=(dec,), materials=tuple(pack.materials),
        facts=(broken_fact,))
    return holder_pack, broken_fact


def main() -> dict:  # noqa: C901 - 逐条断言，长而直
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
            details.append(f"FAIL: {msg}")

    def expect_error(fn, exc_type, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc_type as exc:                                  # noqa: PERF203
            if needle and needle not in str(exc):
                failed += 1
                details.append(f"FAIL: {msg}（异常类型对，但正文不含 {needle!r}：{exc}）")
            else:
                passed += 1
        except Exception as exc:                                 # noqa: BLE001
            failed += 1
            details.append(f"FAIL: {msg}（期望 {exc_type.__name__}，实际 "
                           f"{type(exc).__name__}: {exc}）")
        else:
            failed += 1
            details.append(f"FAIL: {msg}（未抛异常）")

    check(NS.MATERIAL_BINDING_POLICY_VERSION == "mbind-1",
          f"绑定消歧判据版本必须是 mbind-1（实际 {NS.MATERIAL_BINDING_POLICY_VERSION!r}）")

    # ==================================================================
    # 1. 唯一收窄（正例）：同来源身份同页三份候选 + 事实的唯一合格输入
    # ==================================================================
    b1_task, b1_auth = _authority(
        "task-mbind-b1",
        materials=(T._Material("m-a1", IDENT, page=12),
                   T._Material("m-a2", IDENT, page=12),
                   T._Material("m-a3", IDENT, page=12)),
        facts=(T._fact("f-b1", TEXT, (ASP,), material_ids=("m-a2",)),))
    b1_pack = _pack(b1_auth)
    b1_container = _container(b1_auth)
    b1_fact = b1_pack.facts[0]
    b1_mats = _materials_by_id(b1_pack)
    check(sorted(b1_mats) == ["m-a1", "m-a2", "m-a3"],
          f"同页三份候选必须全部保留（实际 {sorted(b1_mats)}）")
    #: 引用侧的候选集**不止一个**：旧规则在这里就抛歧义了。
    check(len(NS.material_index(b1_auth)[b1_container][IDENT]) == 3,
          "同一来源身份下三份候选都必须在索引里（不得折叠成单值）")
    check(NS.fact_verified_input_material_ids(b1_pack, b1_fact) == frozenset({"m-a2"}),
          "事实的已核验输入材料必须是单元素 {m-a2}（逐环重算资格链读出来，不是自报）")

    bound = NS.material_binding_for_citation(b1_auth, b1_container, T._citation(12),
                                             fact=b1_fact, pack=b1_pack)
    check(bound is not None and bound[0] == "m-a2",
          f"同页三份候选下必须按事实自己的资格链收窄到 m-a2（实际 "
          f"{bound[0] if bound else None}）")
    check(bound is not None and NS.canonical_json(bound[1]) == NS.canonical_json(
        b1_mats["m-a2"].payload_ref.to_dict()),
        "绑定必须带**被选中那一份**自己的 payload reference（不得跨候选拼装）")

    b1_scan = PW.scan_topic_pack(b1_auth, b1_task)
    check([str(e.fact_id) for e in b1_scan.facts] == ["f-b1"],
          "唯一收窄后这条事实必须仍然进入写作读视图（不得整节 fail-closed）")
    check(str(b1_scan.facts[0].material_id) == "m-a2",
          f"写作读视图的事实行必须绑定同一个 m-a2（实际 "
          f"{b1_scan.facts[0].material_id!r}）")
    check(b1_scan.facts[0].text == TEXT,
          "收窄不得改动事实文本（绑的是材料，不是命题）")

    #: 声明顺序不影响结果：候选索引按 material_id 规范化排序，收窄是集合求交。
    b1_rev_task, b1_rev_auth = _authority(
        "task-mbind-b1-rev",
        materials=(T._Material("m-a3", IDENT, page=12),
                   T._Material("m-a2", IDENT, page=12),
                   T._Material("m-a1", IDENT, page=12)),
        facts=(T._fact("f-b1", TEXT, (ASP,), material_ids=("m-a2",)),))
    b1_rev = NS.material_binding_for_citation(
        b1_rev_auth, _container(b1_rev_auth), T._citation(12),
        fact=_pack(b1_rev_auth).facts[0], pack=_pack(b1_rev_auth))
    check(b1_rev is not None and b1_rev[0] == "m-a2",
          "夹具材料声明顺序反转后绑定必须逐字不变")

    # ==================================================================
    # 2. 门侧同源（B9）：同一事实在独立支撑门里得到同一个绑定
    # ==================================================================
    b1_expectations, b1_ambiguous = NS._authority_material_expectations(b1_auth)
    check(b1_ambiguous == set(),
          f"唯一收窄成功的事实不得登记歧义（实际 {sorted(b1_ambiguous)}）")
    check(b1_expectations.get((b1_container, "f-b1"), (None,))[0] == "m-a2",
          f"门侧必须给出同一个 m-a2 绑定（实际 "
          f"{b1_expectations.get((b1_container, 'f-b1'))}）")
    check(NS.canonical_json(b1_expectations[(b1_container, "f-b1")][1])
          == NS.canonical_json(b1_mats["m-a2"].payload_ref.to_dict()),
          "门侧绑定的 payload 必须与扫描侧逐字相同（两侧同源）")

    # ==================================================================
    # 3. 保留的旧反例：出处不唯一（资格链自己声明两份输入）⇒ 仍 fail-closed
    # ==================================================================
    b7_task, b7_auth = _authority(
        "task-mbind-b7",
        materials=(T._Material("m-a1", IDENT, page=12),
                   T._Material("m-a2", IDENT, page=12),
                   T._Material("m-a3", IDENT, page=12)),
        facts=(T._fact("f-b7", TEXT, (ASP,), material_ids=("m-a1", "m-a2")),))
    b7_pack = _pack(b7_auth)
    b7_container = _container(b7_auth)
    b7_fact = b7_pack.facts[0]
    check(NS.fact_verified_input_material_ids(b7_pack, b7_fact)
          == frozenset({"m-a1", "m-a2"}),
          "两输入事实的资格链必须可核验地读回两份输入材料（收窄集不是单元素）")
    expect_error(
        lambda: NS.material_binding_for_citation(b7_auth, b7_container, T._citation(12),
                                                 fact=b7_fact, pack=b7_pack),
        NS.MaterialBindingAmbiguityError,
        "事实出处不唯一（两输入）+ 同页多候选时必须 fail-closed（不得动用收窄）")
    expect_error(
        lambda: PW.scan_topic_pack(b7_auth, b7_task),
        PW.PackWriterError,
        "同页歧义在写作入口仍必须拒（不得任选其一）", needle="多个 material 候选")
    b7_expectations, b7_ambiguous = NS._authority_material_expectations(b7_auth)
    check(b7_ambiguous == {(b7_container, "f-b7")} and not b7_expectations,
          f"歧义必须登记在 (容器, fact_id) 上且不产生绑定（实际 "
          f"{sorted(b7_ambiguous)} / {sorted(b7_expectations)}）")

    # ==================================================================
    # 4. 错页：引用页号与事实材料不符 ⇒ 不绑定、不任选（不是「退回按页挑」）
    # ==================================================================
    b2_task, b2_auth = _authority(
        "task-mbind-b2",
        materials=(T._Material("m-a1", IDENT, page=12),
                   T._Material("m-a2", IDENT, page=12),
                   T._Material("m-a3", IDENT, page=12)),
        facts=(T._fact("f-b2", TEXT, (ASP,), page=20, material_ids=("m-a2",)),))
    b2_pack = _pack(b2_auth)
    b2_container = _container(b2_auth)
    b2_fact = b2_pack.facts[0]
    check(NS.fact_verified_input_material_ids(b2_pack, b2_fact) == frozenset({"m-a2"}),
          "错页反例的事实本身资格链完整（错的是定位，不是资格）")
    b2_bound = NS.material_binding_for_citation(b2_auth, b2_container, T._citation(20),
                                                fact=b2_fact, pack=b2_pack)
    check(b2_bound is None,
          f"引用页号与事实材料页号不符 ⇒ 不得绑定（实际 {b2_bound}）")
    b2_expectations, b2_ambiguous = NS._authority_material_expectations(b2_auth)
    check(not b2_ambiguous and (b2_container, "f-b2") not in b2_expectations,
          f"错页既不算歧义也不产生绑定（实际 {sorted(b2_ambiguous)} / "
          f"{sorted(b2_expectations)}）")

    # ==================================================================
    # 5. 跨 Pack / 决定与候选不一致 / 非 eligible / digest 不符 ⇒ 无收窄集 ⇒ 旧规则
    # ==================================================================
    def _no_restrict_case(label, broken_pack, broken_fact):
        """残缺资格链一律**不产生**收窄集：绑定退回旧规则（同页多候选仍拒）。"""
        check(NS.fact_verified_input_material_ids(broken_pack, broken_fact) is None,
              f"{label}：资格链不完整时必须返回 None（无收窄集，不猜）")
        expect_error(
            lambda: NS.material_binding_for_citation(
                _holder([broken_pack]), b1_container, T._citation(12), fact=broken_fact,
                pack=broken_pack),
            NS.MaterialBindingAmbiguityError,
            f"{label}：退回旧规则后同页三份候选仍必须 fail-closed")
        _exp, amb = NS._authority_material_expectations(_holder([broken_pack]))
        check((b1_container, str(broken_fact.fact_id)) in amb,
              f"{label}：门级派生必须同样登记歧义（两侧不得一边放行一边报错）")

    #: (1) 跨 Pack：事实链声明的输入材料不在本 Pack 内（候选内容寻址，只能经构造器重造）。
    _no_restrict_case("跨 Pack（输入 material 不在本 Pack）",
                      *_broken_chain(b1_pack, b1_fact, material_ids=("m-elsewhere",)))
    #: (2) 决定与候选声明的材料集合不一致。
    _no_restrict_case("决定与候选材料不一致",
                      *_broken_chain(b1_pack, b1_fact,
                                     input_material_ids=("m-a1", "m-a2")))
    #: (3) 决定 verdict 非 eligible。
    _no_restrict_case("决定 verdict 非 eligible",
                      *_broken_chain(b1_pack, b1_fact, verdict="rejected"))
    #: (4) 三个 digest 之一重算不符。
    _no_restrict_case("input_payload_digest 重算不符",
                      *_broken_chain(b1_pack, b1_fact, input_payload_digest="0" * 64))
    _no_restrict_case("input_identity_digest 重算不符",
                      *_broken_chain(b1_pack, b1_fact, input_identity_digest="0" * 64))
    _no_restrict_case("input_locator_digest 重算不符",
                      *_broken_chain(b1_pack, b1_fact, input_locator_digest="0" * 64))
    #: (5) 来源身份不闭合：决定说的输入来源与事实自己的来源不是同一个。
    _no_restrict_case("来源身份不闭合",
                      *_broken_chain(b1_pack, b1_fact,
                                     input_source_identity="evidence:other"))

    #: 对照：同样的残缺链落在**唯一候选**的 Pack 上时，退回旧规则仍能绑定 —— 这证明上面的
    #: 各档是「收窄集取不到就退回旧规则」，不是「资格链一残就整节拒」。
    solo_task, solo_auth = _authority(
        "task-mbind-solo",
        materials=(T._Material("m-a2", IDENT, page=12),),
        facts=(T._fact("f-solo", TEXT, (ASP,), material_ids=("m-a2",)),))
    solo_pack = _pack(solo_auth)
    solo_broken, solo_fact = _broken_chain(solo_pack, solo_pack.facts[0], verdict="rejected")
    solo_bound = NS.material_binding_for_citation(
        _holder([solo_broken]), _container(solo_auth), T._citation(12), fact=solo_fact,
        pack=solo_broken)
    check(solo_bound is not None and solo_bound[0] == "m-a2",
          f"残缺链 + 唯一候选时按旧规则仍须绑定（实际 {solo_bound}）")
    check(str(PW.scan_topic_pack(solo_auth, solo_task).facts[0].material_id) == "m-a2",
          "唯一候选的正例绑定不得因本批而回归")

    # ==================================================================
    # 6. 断链不静默：候选指针在场、资格决定不在本 Pack 内 ⇒ 抛，而不是回落
    # ==================================================================
    b6_fact = dc.replace(b1_fact, qualification_decision_id="dec-not-in-pack")
    expect_error(
        lambda: NS.fact_verified_input_material_ids(b1_pack, b6_fact),
        NS.NarrativeSchemaError,
        "有候选指针却读不到资格决定时必须抛（断链不得静默回落去猜出处）",
        needle="不在本 Pack 内")
    expect_error(
        lambda: NS.material_binding_for_citation(b1_auth, b1_container, T._citation(12),
                                                 fact=b6_fact, pack=b1_pack),
        NS.NarrativeSchemaError,
        "断链必须从唯一绑定入口一路抛出（不得被吞成「找不到 material」）")
    expect_error(
        lambda: PW._material_binding_of(b1_auth, b1_pack, b1_container, T._citation(12),
                                        b6_fact),
        PW.PackWriterError,
        "写作入口必须把断链转成 PackWriterError（整节不写，不猜材料）")
    #: 反向：候选指针在场、候选**不在本 Pack 内** ⇒ 链的第一环就不成立 ⇒ 读回 None（无收窄集），
    #: 由调用方退回旧规则。这与上面「决定缺失 ⇒ 抛」的**不对称**是设计：候选是链的入口，入口
    #: 都没有就谈不上「猜出处」；决定是把候选落到材料上的那一环，缺了就是在猜。
    orphan = dc.replace(b1_fact, candidate_id="fc-not-in-pack")
    check(NS.fact_verified_input_material_ids(b1_pack, orphan) is None,
          "候选不在本 Pack 内 ⇒ 返回 None（无收窄集，不抛）")
    expect_error(
        lambda: NS.material_binding_for_citation(b1_auth, b1_container, T._citation(12),
                                                 fact=orphan, pack=b1_pack),
        NS.MaterialBindingAmbiguityError,
        "候选缺失 ⇒ 退回旧规则，同页三份候选仍必须 fail-closed")
    #: 两条早退防护（「两个指针都空」「有候选无决定指针」）在**真实类型层不可达**：`SupportedFact`
    #: 的 `__post_init__` 强制单向回指 candidate。这里先证明不可达，再直接对防护本身断言，
    #: 而不是伪造一个真实类型造不出来的事实。
    expect_error(
        lambda: dc.replace(b1_fact, candidate_id=""),
        TS.SchemaValidationError,
        "SupportedFact 类型层必须强制回指 candidate（故上面的早退防护在生产路径上不可达）")
    for label, stub in (
            ("两个指针都空", SimpleNamespace(candidate_id="", qualification_decision_id="",
                                            fact_id="f-x")),
            ("有候选指针、无决定指针", SimpleNamespace(candidate_id=b1_fact.candidate_id,
                                               qualification_decision_id="", fact_id="f-x"))):
        check(NS.fact_verified_input_material_ids(b1_pack, stub) is None,
              f"防护分支（{label}）必须返回 None（不抛、不猜；生产路径上不可达）")

    # ==================================================================
    # 7. 跨页候选不被收窄破坏（B8）：命中哪一页仍由 citation 定位决定
    # ==================================================================
    b8_task, b8_auth = _authority(
        "task-mbind-b8",
        materials=(T._Material("m-p12", IDENT, page=12), T._Material("m-p20", IDENT, page=20)),
        facts=(T._fact("f-b8", TEXT, (ASP,), page=20, material_ids=("m-p20",)),))
    b8_pack = _pack(b8_auth)
    b8_container = _container(b8_auth)
    b8_fact = b8_pack.facts[0]
    b8_bound = NS.material_binding_for_citation(b8_auth, b8_container, T._citation(20),
                                                fact=b8_fact, pack=b8_pack)
    check(b8_bound is not None and b8_bound[0] == "m-p20",
          f"跨页候选 + 事实链指向 p20 ⇒ 必须绑定 m-p20（实际 {b8_bound}）")
    #: 同一 Pack、同一引用身份，仅 citation 页号不同而事实链指向另一份 ⇒ 不得按页号挑走。
    mixed = NS.material_binding_for_citation(b8_auth, b8_container, T._citation(12),
                                             fact=b8_fact, pack=b8_pack)
    check(mixed is None,
          f"事实链指向 p20 而 citation 说 12 ⇒ 不绑定（实际 {mixed}）")

    # ==================================================================
    # 8. 防护分支的直接单元测试（生产路径上不可达，见模块 docstring）
    # ==================================================================
    b1_candidates = NS.material_index(b1_auth)[b1_container][IDENT]
    expect_error(
        lambda: NS._resolve_material_binding(b1_candidates, T._citation(12), b1_container,
                                             frozenset({"m-zzz"})),
        NS.MaterialBindingAmbiguityError,
        "收窄集与候选集**交集为空**时必须当场抛（不得退回去任选一个）",
        needle="不在引用锚点")
    expect_error(
        lambda: NS._resolve_material_binding(b1_candidates, T._citation(12), b1_container,
                                             frozenset({"m-a1", "m-a2"})),
        NS.MaterialBindingAmbiguityError,
        "收窄集非单元素时不得收窄（同页多候选仍必须 fail-closed）")
    #: 空收窄集 ≠ 「候选为空」：不得被当成「全部排除」而静默返回 None。
    expect_error(
        lambda: NS._resolve_material_binding(b1_candidates, T._citation(12), b1_container,
                                             frozenset()),
        NS.MaterialBindingAmbiguityError,
        "空收窄集不得改变旧规则（同页多候选仍拒），也不得静默返回 None")
    #: 上游闭合检查保证生产路径上不会出现「收窄集与候选集相交为空」：资格链自己就已经拒了。
    check(NS.fact_verified_input_material_ids(b1_pack, b1_fact) is not None
          and all(mid in {c.material_id for c in b1_candidates}
                  for mid in NS.fact_verified_input_material_ids(b1_pack, b1_fact)),
          "可核验的单元素收窄集必然落在同一来源身份的候选集内（防护分支不可达的原因）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    result = main()
    for d in result["details"]:
        print(d)
    print(f"passed={result['passed']} failed={result['failed']} skipped={result['skipped']}")
    sys.exit(1 if result["failed"] else 0)

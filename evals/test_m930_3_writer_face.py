"""Eval: M930-3 返修 P1 —— 写作「被提供面」与硬门的一致性（逐轮修订 + 排除原因）。

用法: python -m evals.test_m930_3_writer_face

本模块钉住一条曾被真实 run 打出来的缺陷链（返修计划 §0 R2）：硬门要求「必需事实必须被某个
proposal 引用」，而它迭代的是**权威侧被选中的事实集**（`pack.facts` 逐条），那个集合里包含
已经**被期间门排除、根本没进 `authority_facts` 载荷**的事实。模型看不到它，却被要求引用它
——于是每一轮都必然返修，返修预算耗尽后整节 fail-closed，而产物里读不出「为什么」。

因此这里逐条证明（而不是「改了代码看起来对」）：

1. **被提供面 == 生成器实际收到的行**：`TopicWriterFace.provided` 恰好等于请求面
   `authority_facts` 载荷逐行的 `(container_id, fact_id)`（从 stub 记录的**原始请求字节**里
   解出来对账，不另算一遍）；
2. **两面严格互斥**：`provided ∩ excluded == ∅`；每条被排除的事实带**它自己的**原因码
   （期间门三态原样沿用，不另造同义词）；同一 `(container, fact)` 落在两条排除通道上时
   fail-closed，而不是后写入者胜出；
3. **反例（R2）**：一条**必需**事实被期间门排除时，门**不得**要求模型引用它——同时证明
   这条事实**确实仍在**门的迭代集里（否则测的就不是这条缺陷），且它的期间缺口**照常**成立
   （拒绝 ≠ 缺口，两件事分别保留）；
4. **正面对照**：同一条必需事实带上显式期间、真的进了被提供面而未被引用时，**仍然**出
   `required_fact_not_proposed`（这条提示没有被这次返修顺手关掉）；
5. **面只能派生**：构造参数里没有它；`dataclasses.replace` / `create` 声明即被拒；篡改后
   重跑校验必然被抓；
6. **逐轮修订**：同一节两次生成 ⇒ 两个 `draft_revision`、两套完整有序 proposal 身份；被拒
   那一轮留在 typed 审计里，**不是**被原地删掉；首轮取值与历史载荷逐字节相同（历史读回不动）。

公司无关：本模块没有公司代号、文件名、页码、表号或固定年份的生产字面量；不调 LLM（stub 只
回放夹具）、不联网、不写库、不建第二套 Router/Harness/Pack。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import writing_spec as WS

#: `test_demo_pack_writer` 的模块级夹具已覆盖：任务 / 事实 / aspect 要求 / 提案束 / stub LLM /
#: 权威构造器。这里只补一件它未导出的东西——**写入口的薄包装**（那份包装原本定义在它的
#: `main()` 里，因此无法直接 import）。补齐的方式与它逐字一致：同一个 writing spec、
#: 同一个 presentation profile、同一个正文上下文入口。
_SPEC = WS.load_writing_spec(T.SPEC_PATH)
_PROFILE = PP.load_presentation_profile(T.PROFILE_PATH)
_PROJECTIONS = {sid: PW.ContractProjection.create(
    _SPEC, section_id=sid, contract_version=T.CONTRACT_VERSION,
    contract_fingerprint=T.CONTRACT_FINGERPRINT)
    for sid in ("company", "industry", "financial")}


def _write(task, authority, *, section_id: str, llm, policy=None):
    """唯一公开入口的薄包装（与 `test_demo_pack_writer` 内的同名包装逐字同源）。"""
    material_context = T._writer_material_context(
        authority.pack_set, task_id=str(task.task_id),
        section_id=str(section_id or task.section_id))
    return PW.write_section(
        task, authority, projection=_PROJECTIONS[section_id], writing_spec=_SPEC,
        presentation_profile=_PROFILE, llm_client=llm, policy=policy,
        dependency_fingerprint=T.DEPENDENCY_FINGERPRINT, material_context=material_context)


def _request_payload(stub, index: int = 0) -> dict:
    """从 stub 记录的**原始请求字节**里解出这一轮实际投给生成器的载荷。"""
    return json.loads(str(stub.calls[index]["messages"][0]["content"]))


def _payload_fact_keys(payload: dict) -> set[tuple[str, str]]:
    return {(str(row["container_id"]), str(row["fact_id"]))
            for row in payload["authority_facts"]}


def _rev_of(draft, attempt: int, *, prose_digest: str) -> str:
    """按产物**自己**的字段重算第 `attempt` 轮的 draft revision（与生产同一函数、同一入参）。

    `prose_digest` 是**必填**，不是可选默认：`pw-15` 起修订还覆盖该轮的草稿层
    （`natural_prose_digest` 只在草稿非空时进身份体，见 `NS.derive_draft_revision`）。给个默认
    空串会让「忘了带草稿层」的调用方拿到一个**看起来对**的修订取值，而它恰恰是本次要钉住的那
    一类错——上一版的这个助手就没有这一项，于是断言在带草稿层的产物上必然假红。
    """
    manifest = draft.material_manifest
    return NS.derive_draft_revision(
        task_id=draft.task_id, section_id=draft.section_id, company_id=draft.company_id,
        report_as_of=draft.report_as_of, contract_version=draft.contract_version,
        contract_fingerprint=draft.contract_fingerprint,
        writer_policy_version=draft.writer_policy_version,
        prompt_version=draft.prompt_version, model_policy=draft.model_policy,
        manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint(),
        attempt=attempt, natural_prose_digest=prose_digest)


def expect_error(call, exc_type, msg: str, *, needle: str = "") -> None:
    """反例的标准形态：必须**抛指定类型**，且（给了 `needle` 时）消息必须点名原因。

    「抛了别的异常」与「什么都没抛」都是失败——前者会被读成「拒绝得对」，其实拒绝的理由
    根本对不上号。
    """
    try:
        call()
    except exc_type as exc:  # noqa: BLE001
        if needle and needle not in str(exc):
            raise AssertionError(f"{msg}：抛出了 {type(exc).__name__} 但消息未点名 {needle!r}"
                                 f"（实为 {str(exc)[:200]!r}）") from exc
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"{msg}：期望 {exc_type.__name__}，实抛 {type(exc).__name__}: "
                             f"{str(exc)[:200]}") from exc
    raise AssertionError(f"{msg}：未拒绝")


def _required_authority(task_id: str, *, must_period: str):
    """「必需事实」夹具：`f-must` 与 `f-keep` 同属一个 **blocking** aspect，因此两条都必需。

    `blocking_policy` 非空是唯一的「必需」判据（`NS.required_fact_ids`），这里不给它做任何
    白名单或旁路：必需性完全由冻结 Contract 的 aspect 要求决定。

    `must_period=""` ⇒ `f-must` 被期间门排除（不进 `authority_facts`），而 `f-keep` 带显式
    期间、照常被提供——这正是真实 run 里的形态：**同一批必需事实里只有一部分进得了正文面**。
    两件事必须分别成立：被排除的那条不得被门要求引用，被提供的那条**仍然**必须被引用。
    `f-side` 是另一个（非 blocking）aspect 上的可选事实，用来验证「可选的没被引用时不被要求」。
    """
    task = T._task("company", (T.TOPIC_BUSINESS,), task_id=task_id)
    authority = T._company_authority(
        task,
        facts=(T._fact("f-must", "公司主营业务为动力电池系统的研发、生产与销售。",
                       (T.ASP_BUSINESS_MAIN,), period=must_period),
               T._fact("f-keep", "公司主营业务收入来自动力电池系统的销售。",
                       (T.ASP_BUSINESS_MAIN,)),
               T._fact("f-side", "公司销售模式以直销为主。", (T.ASP_BUSINESS_SALES,))),
        aspects=(T._AspectResult(T.ASP_BUSINESS_MAIN, "covered"),
                 T._AspectResult(T.ASP_BUSINESS_SALES, "covered")),
        requirements=(T._Req(T.TOPIC_BUSINESS, (
            T._aspect(T.ASP_BUSINESS_MAIN, T.TOPIC_BUSINESS, "q-company_business",
                      blocking=("SECTION_BLOCKED",)),
            T._aspect(T.ASP_BUSINESS_SALES, T.TOPIC_BUSINESS, "q-company_business"),)),))
    return task, authority


def main() -> dict:
    passed = failed = 0
    details: list[str] = []

    def check(ok: bool, msg: str) -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def _demands(outcome) -> list:
        return [i for i in outcome.gate_result.issues
                if i.rule_id == "required_fact_not_proposed"]

    # ==================================================================
    # 1. 被提供面 == 生成器实际收到的 `authority_facts` 行
    # ==================================================================
    task, authority = _required_authority("task-face-provided", must_period="")
    scan = PW.scan_topic_pack(authority, task)
    face = authority.writer_face
    container = str(authority.pack_set.packs[0].pack_id)
    entry = {e.fact_id: e for e in scan.facts}
    check(face is not None and authority.writer_face_unavailable == "",
          f"可构造的权威必须有被提供面（实际 face={face!r} / "
          f"unavailable={authority.writer_face_unavailable!r}）")
    stub = T._StubLlm(T._plan(candidates=[
        T._cand("c1", entry["f-side"].text, T._fact_edge(scan, "f-side"))]))
    outcome = _write(task, authority, section_id="company", llm=stub)
    payload_keys = _payload_fact_keys(_request_payload(stub))
    check(payload_keys == face.provided_keys() == NS._writer_provided_fact_keys(authority),
          f"被提供面必须**恰好**等于请求面 `authority_facts` 的实际行集（三者同源同值）；"
          f"实为 payload={sorted(payload_keys)} / face={sorted(face.provided_keys())} / "
          f"helper={sorted(NS._writer_provided_fact_keys(authority) or ())}")
    check(payload_keys == {(container, "f-keep"), (container, "f-side")},
          f"夹具前置：两条带显式期间的事实进入被提供面，被排除的那条不进；实为 "
          f"{sorted(payload_keys)}")
    check(face.to_dict()["version"] == PW.WRITER_FACE_VERSION == "writer-face-1"
          and list(face.to_dict()) == ["version", "provided", "excluded"],
          f"被提供面是版本化派生对象（键集封闭、口径变更必须升版本）；实为 "
          f"{face.to_dict()!r}")

    # ==================================================================
    # 2. 两面严格互斥 + 逐条排除原因（封闭词表）
    # ==================================================================
    excluded_keys = {(c, f) for c, f, _r in face.excluded}
    check(face.provided_keys().isdisjoint(excluded_keys),
          f"被提供面与排除面必须严格互斥（实为交集 "
          f"{sorted(face.provided_keys() & excluded_keys)}）")
    check(excluded_keys == {(container, "f-must")}
          and face.exclusion_reason(container, "f-must") == "explicit_period_required",
          f"被期间门排除的事实必须带**它自己的**原因码（期间门三态原样沿用）；实为 "
          f"excluded={sorted(excluded_keys)} / "
          f"reason={face.exclusion_reason(container, 'f-must')!r}")
    check(face.exclusion_reason(container, "f-keep") == ""
          and face.exclusion_reason(container, "no-such-fact") == "",
          "被提供的事实与不存在的坐标都不带排除原因（原因只给真被排除的那些）")
    check(all(str(reason) for _c, _f, reason in face.excluded)
          and all(reason in PW.WRITER_FACE_EXCLUSION_REASONS
                  for _c, _f, reason in face.excluded),
          f"排除原因必须落在封闭词表内（期间门三态 + aspect 不在本节），实为 "
          f"{sorted({r for _c, _f, r in face.excluded})}")
    check([e.fact_id for e in scan.facts] == ["f-keep", "f-side"]
          and scan.period_unresolved == ((container, "f-must", T.TOPIC_BUSINESS),),
          f"扫描自己必须如实登记这条排除（缺口与面是同一份扫描的两个读视图）；实为 "
          f"facts={[e.fact_id for e in scan.facts]} / "
          f"period_unresolved={scan.period_unresolved!r}")
    check("aspect_not_in_section" not in PW.WRITER_FACE_EXCLUSION_REASONS
          or PW.WRITER_FACE_EXCLUSION_ASPECT in PW.WRITER_FACE_EXCLUSION_REASONS,
          "排除原因词表必须显式包含「aspect 不在本节」这一支")

    # ==================================================================
    # 3. 反例（R2）：期间被排除的必需事实不得被门要求引用
    # ==================================================================
    selected = NS._authority_selected_facts(authority)
    check((container, "f-must") in selected,
          f"反例前置：门迭代的**被选中集**里仍有这条被排除的事实（否则测的不是本缺陷）；"
          f"实为 {sorted(selected)}")
    check(NS.required_fact_ids(authority) == frozenset({"f-must", "f-keep"}),
          f"反例前置：两条事实都是**必需**的（同一个 blocking aspect 支撑）——它们的差别只在于"
          f"是否进得了正文面，不在必需性；实为 {sorted(NS.required_fact_ids(authority))}")
    demanded = _demands(outcome)
    check([i.location for i in demanded] == [f"{container}:f-keep"],
          f"门只能对自己**实际交给 Writer 的**必需事实提要求：被提供但未引用的出提示，"
          f"被期间门排除的一条**不得**出提示；实为 {[i.location for i in demanded]}")
    check(any(u.reason_code == "period_unresolved" for u in outcome.unresolved),
          f"被排除那条事实的期间缺口必须**照常**成立（拒绝 ≠ 缺口：不是「不用提它」，"
          f"而是「它另走缺口」）；实为 {[u.reason_code for u in outcome.unresolved]}")

    # ==================================================================
    # 4. 正面对照：真的被提供了、却未被引用的必需事实**仍然**出提示
    # ==================================================================
    ok_task, ok_authority = _required_authority("task-face-demanded",
                                               must_period=T.REPORT_AS_OF)
    ok_scan = PW.scan_topic_pack(ok_authority, ok_task)
    ok_entry = {e.fact_id: e for e in ok_scan.facts}
    ok_container = str(ok_authority.pack_set.packs[0].pack_id)
    check(ok_entry["f-must"].fact_id in ok_entry
          and (ok_container, "f-must") in ok_authority.writer_face.provided_keys(),
          "正面对照前置：带上显式期间的 `f-must` 确实进入被提供面")
    ok_outcome = _write(ok_task, ok_authority, section_id="company",
                        llm=T._StubLlm(T._plan(candidates=[
                            T._cand("c1", ok_entry["f-side"].text,
                                    T._fact_edge(ok_scan, "f-side"))])))
    ok_issues = _demands(ok_outcome)
    check([i.location for i in ok_issues] == [f"{ok_container}:f-keep",
                                              f"{ok_container}:f-must"]
          and all(i.severity == "rework" for i in ok_issues),
          f"被提供却未被引用的必需事实必须**继续**出 rework 级提示（这次返修不得把它顺手"
          f"关掉），且顺序稳定（升序）；实为 {[i.to_dict() for i in ok_issues]}")

    # ==================================================================
    # 5. 两面重叠 / 两条排除通道冲突 ⇒ fail-closed（不得后写入者胜出）
    # ==================================================================
    real_scan = PW.scan_topic_pack
    try:
        overlapping = dataclasses.replace(
            scan, excluded_facts=((container, "f-keep", T.TOPIC_BUSINESS),))
        PW.scan_topic_pack = lambda *a, **kw: overlapping  # noqa: ARG005
        expect_error(lambda: PW.topic_writer_face(authority), PW.PackWriterError,
                       "同一坐标同时落在提供面与排除面必须 fail-closed", needle="严格互斥")
        expect_error(lambda: PW.TopicPackAuthorityInput.create(
            task, authority.pack_set, company_id=T.COMPANY_ID,
            report_as_of=T.REPORT_AS_OF, contract_version=T.CONTRACT_VERSION,
            contract_fingerprint=T.CONTRACT_FINGERPRINT), PW.PackWriterError,
            "自相矛盾的扫描必须在**构造期**就终止（不得被当成「面不可用」吞掉）",
            needle="严格互斥")
        both_channels = dataclasses.replace(
            scan, excluded_facts=((container, "f-must", T.TOPIC_BUSINESS),))
        PW.scan_topic_pack = lambda *a, **kw: both_channels  # noqa: ARG005
        expect_error(lambda: PW.topic_writer_face(authority), PW.PackWriterError,
                       "同一坐标落在两条互斥的排除通道上必须 fail-closed", needle="自相矛盾")
    finally:
        PW.scan_topic_pack = real_scan

    # ==================================================================
    # 6. 面只能派生，不得声明
    # ==================================================================
    expect_error(lambda: dataclasses.replace(authority, writer_face=face), ValueError,
                   "`writer_face` 必须是 `init=False` 的派生字段：声明它即被拒"
                   "（`dataclasses.replace` 对 `init=False` 字段抛 ValueError）")
    expect_error(lambda: dataclasses.replace(authority, writer_face_unavailable="x"),
                   ValueError, "`writer_face_unavailable` 同样是派生字段，不得声明")
    expect_error(lambda: PW.TopicPackAuthorityInput.create(
        task, authority.pack_set, company_id=T.COMPANY_ID, report_as_of=T.REPORT_AS_OF,
        contract_version=T.CONTRACT_VERSION,
        contract_fingerprint=T.CONTRACT_FINGERPRINT, writer_face=face), TypeError,
        "`create()` 也不得接受被提供面作为入参")
    tampered = PW.TopicPackAuthorityInput.create(
        task, authority.pack_set, company_id=T.COMPANY_ID, report_as_of=T.REPORT_AS_OF,
        contract_version=T.CONTRACT_VERSION, contract_fingerprint=T.CONTRACT_FINGERPRINT)
    object.__setattr__(tampered, "writer_face",
                       PW.TopicWriterFace(provided=(), excluded=()))
    expect_error(lambda: PW._validate_topic_pack_authority(tampered), PW.PackWriterError,
                   "篡改后的面必须被「声明 vs 重算」比对抓住", needle="只能派生")

    # ==================================================================
    # 7. material 候选歧义：权威**可构造**，但面不可用，扫描仍 fail-closed
    # ==================================================================
    cross = f"evidence:{T.EV_ID}"

    def _ambiguous():
        amb_task = T._task("company", (T.TOPIC_BUSINESS,), task_id="task-face-ambiguous")
        return amb_task, T._company_authority(
            amb_task,
            facts=(T._fact("f-cross", "公司主营业务为动力电池系统的研发、生产与销售。",
                           (T.ASP_BUSINESS_MAIN,)),),
            materials=(T._Material("m-a", cross, page=12),
                       T._Material("m-b", cross, page=12)),
            aspects=(T._AspectResult(T.ASP_BUSINESS_MAIN, "covered"),),
            requirements=(T._Req(T.TOPIC_BUSINESS, (
                T._aspect(T.ASP_BUSINESS_MAIN, T.TOPIC_BUSINESS,
                          "q-company_business"),)),))

    amb_task, amb_authority = _ambiguous()
    check(amb_authority.writer_face is None
          and "多个 material 候选" in amb_authority.writer_face_unavailable,
          f"material 候选歧义时**构造身份**照常可算，但必须明确记下「本次没有被提供面」"
          f"（不得伪装成空面）；实为 face={amb_authority.writer_face!r} / "
          f"unavailable={amb_authority.writer_face_unavailable!r}")
    expect_error(lambda: PW.scan_topic_pack(amb_authority, amb_task), PW.PackWriterError,
                   "歧义的扫描必须在扫描处以自己的诊断 fail-closed（语义与返修前逐字一致）",
                   needle="多个 material 候选")
    check(NS._writer_provided_fact_keys(amb_authority) is None,
          "面不可用时门侧读到的必须是 `None`（= 本次没有被提供面，退回原有判据），"
          "而不是一张会被读成「一条也没提供」的空面")

    # ==================================================================
    # 8. 逐轮修订：两次生成 ⇒ 两个修订、两套完整有序身份
    # ==================================================================
    two_task, two_authority = _required_authority("task-face-attempts", must_period="")
    two_scan = PW.scan_topic_pack(two_authority, two_task)
    two_entry = {e.fact_id: e for e in two_scan.facts}
    #: 两轮的**提案束原文**留成具名变量：逐轮修订必须按**该轮自己的**草稿层复算，被拒那一轮的
    #: 草稿只有它自己知道（被采信的那一束里没有它）。
    plan1 = T._plan(candidates=[T._cand("c1", two_entry["f-side"].text,
                                        T._fact_edge(two_scan, "f-side"))])
    plan2 = T._plan(candidates=[T._cand("c1", two_entry["f-side"].text,
                                        T._fact_edge(two_scan, "f-side")),
                                T._cand("c2", two_entry["f-keep"].text,
                                        T._fact_edge(two_scan, "f-keep"))])
    two_stub = T._StubLlm(plan1, plan2)
    two = _write(two_task, two_authority, section_id="company", llm=two_stub,
                 policy=PW.WriterPolicy(max_llm_retries=1))
    check(len(two_stub.calls) == 2 and two.llm_calls == 2 and len(two.rejections) == 1,
          f"两次生成 ⇒ 恰好一条被拒审计 + 一次被采信（实为 calls={len(two_stub.calls)} / "
          f"llm_calls={two.llm_calls} / rejections={len(two.rejections)}）")
    rec = two.rejections[0]
    check(rec.attempt == 1 and two.draft.writer_attempt == 2,
          f"被拒的是第 1 次、被采信的是第 2 次（实为 record.attempt={rec.attempt} / "
          f"draft.writer_attempt={two.draft.writer_attempt}）")
    accepted_proposal_ids = {str(p.proposed_support_id)
                             for p in two.draft.proposed_support_refs}
    check(len(rec.proposal_ids) == 1 and len(accepted_proposal_ids) == 2
          and set(rec.proposal_ids).isdisjoint(accepted_proposal_ids),
          f"两轮必须各有**完整有序**的 proposal 身份且互不重叠（同一句候选在两轮里逐字重现，"
          f"身份却必须不同）；实为 rejected={list(rec.proposal_ids)} / "
          f"accepted={sorted(accepted_proposal_ids)}")
    accepted_texts = {c.claim_text: str(c.candidate_id)
                      for c in two.draft.claim_candidates}
    check(two_entry["f-side"].text in accepted_texts
          and set(rec.candidate_ids).isdisjoint(set(accepted_texts.values())),
          f"被拒那一轮的候选身份不得与被采信那一轮共用（否则逐轮修订形同虚设）；实为 "
          f"rejected={list(rec.candidate_ids)} / accepted={sorted(accepted_texts.values())}")
    draft = two.draft
    manifest = draft.material_manifest

    #: 被采信那一轮的草稿层摘要。`digest1` 只能从其**提案束原文**算——而按规格算的入口要的是
    #: **已解析**的出处（`wmmref_*` / `fprov_*`），提案束里给的是短别名，因此这里不能直接把
    #: `plan1["natural_prose_draft"]` 喂进去（那正是上一版这条断言假红的原因）。被拒那一轮的
    #: 解析后规格与草稿单元**都不在产物里**：`ProposalSetRejectionRecord` 记的是
    #: `draft_unit_ids`（身份，不是内容）+ 候选身份 + 逐条原因，没有任何字段能反推出它的
    #: `draft_revision`。因此本模块**不**声称复算过被拒那一轮的修订，只把两条可复算的因果轴
    #: 各自单独钉住，并把这一处边界写在这里（而不是让「两轮修订不同」这句话靠一个算不出来的
    #: 数字撑着）。
    digest2 = NS.natural_prose_draft_digest(draft.natural_prose_draft)
    check(bool(digest2) and digest2 == PW._prose_specs_digest(
        [{"text": u.text, "source_member_refs": u.source_member_refs,
          "source_fact_refs": u.source_fact_refs} for u in draft.natural_prose_draft]),
        "被采信那一轮的草稿层必须非空，且「按规格算」与「按单元算」两个入口给出同一摘要")

    # 轴一：**只**改 `attempt`（其余入参含草稿摘要一律不动）⇒ 另一个修订。这一条把 `attempt`
    # 单独隔离出来，不依赖任何算不出来的东西。
    check(_rev_of(draft, 1, prose_digest=digest2) != _rev_of(draft, 2, prose_digest=digest2)
          and draft.draft_revision == _rev_of(draft, 2, prose_digest=digest2),
          "`attempt` 必须进修订身份：同一批输入、只把轮次从 1 换成 2 就是另一个修订，且被采信的"
          f"那一份等于它自己轮次的重算值（实为 attempt=2 → "
          f"{_rev_of(draft, 2, prose_digest=digest2)!r}，draft={draft.draft_revision!r}）")
    # 轴二：**只**改草稿层摘要（轮次不动）⇒ 另一个修订。少了这一条，「草稿层进身份」只是文档里
    # 的一句话；有了它，「同一修订、两段不同草稿」才真的不可表达。
    check(_rev_of(draft, 2, prose_digest="") != draft.draft_revision,
          "同一轮、同一输入，只把草稿层按「没有草稿层」算必须得到**另一个**修订：草稿层非空时它"
          "就在身份体里，否则两版草稿会共用一个修订")
    # 「被拒那一轮的修订不是被采信那一轮的修订」这句话在**产物层面**能核到什么程度，如实钉住：
    # 拒绝记录里没有任何字段承载那一轮的修订或草稿内容（`draft_unit_ids` 也只在与草稿层有关的
    # 拒绝上才非空，本条拒绝是空的）。因此本模块只声称上面两条**可复算**的因果轴，不声称复算过
    # 被拒那一轮的修订——把算不出来的数字写成断言，只会得到一个恒真的句子。
    rejection_fields = {f.name for f in dataclasses.fields(type(rec))}
    check("draft_revision" not in rejection_fields
          and not {"prose_digest", "natural_prose_digest"} & rejection_fields,
          f"拒绝记录不得声称携带那一轮的修订或草稿摘要（实测字段 {sorted(rejection_fields)}）；"
          "若将来真的加上了，本模块的边界声明要跟着改")
    check(len(two.draft.claim_candidates) == 2
          and len(two.draft.narrative_draft_units) == 0,
          "被采信那一轮的**完整**候选集必须原样落进产物（不得只留通过门的那几条）")
    expect_error(lambda: NS.derive_draft_revision(
        task_id=draft.task_id, section_id=draft.section_id, company_id=draft.company_id,
        report_as_of=draft.report_as_of, contract_version=draft.contract_version,
        contract_fingerprint=draft.contract_fingerprint,
        writer_policy_version=draft.writer_policy_version, prompt_version=draft.prompt_version,
        model_policy=draft.model_policy, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), attempt=0), NS.NarrativeSchemaError,
        "轮次从 1 起：0 / 负数 / 非整数一律 fail-closed，不得被当成首轮")

    # ==================================================================
    # 9. 历史读回不动：`attempt` 缺省 == 1，且旧载荷缺 `writer_attempt` 时按首轮还原
    # ==================================================================
    body = {
        "task_id": draft.task_id, "section_id": draft.section_id,
        "company_id": draft.company_id, "report_as_of": draft.report_as_of,
        "contract_version": draft.contract_version,
        "contract_fingerprint": draft.contract_fingerprint,
        "writer_policy_version": draft.writer_policy_version,
        "prompt_version": draft.prompt_version, "model_policy": draft.model_policy,
        "manifest_id": manifest.manifest_id, "manifest_fingerprint": manifest.fingerprint()}
    check(NS.derive_draft_revision(**body) == NS.derive_draft_revision(**body, attempt=1),
          "首轮取值必须与**不带** `attempt` 的历史取值逐字节相同（冻结载荷的修订不得被动改号）")
    wire = draft.to_dict()
    check(wire.get("writer_attempt") == 2,
          f"`writer_attempt` 必须随产物落盘（否则读回时无法复算修订）；实为 "
          f"{wire.get('writer_attempt')!r}")
    rebuilt = NS.SectionDraft.create(**wire)
    check(rebuilt.draft_revision == draft.draft_revision
          and rebuilt.writer_attempt == draft.writer_attempt
          and rebuilt.draft_id == draft.draft_id,
          "`create(**to_dict())` 必须是合法的重建路径（修订 / 轮次 / 身份逐字不变）")
    # 真正的历史载荷 = **首轮**那一份 + 没有 `writer_attempt` 这个键。旧载荷的修订本来就等于
    # 首轮取值（`attempt` 缺省即 1），因此删键必须逐字读回。
    first = outcome.draft
    check(first.writer_attempt == 1
          and first.draft_revision == _rev_of(
              first, 1, prose_digest=NS.natural_prose_draft_digest(
                  first.natural_prose_draft)),
          "反例前置：§1 那一份就是首轮产物（`writer_attempt=1`、修订等于首轮取值）")
    legacy = dict(first.to_dict())
    del legacy["writer_attempt"]
    old = NS.SectionDraft.from_dict(legacy)
    check(old.writer_attempt == 1 and old.draft_revision == first.draft_revision
          and old.draft_id == first.draft_id,
          f"缺 `writer_attempt` 的**首轮**旧载荷必须按首轮读回且修订/身份不变"
          f"（历史 SectionDraft 仍可读）；实为 attempt={old.writer_attempt} / "
          f"revision={old.draft_revision!r} / draft_id={old.draft_id!r}")
    # 反面：把**非首轮**载荷的 `writer_attempt` 删掉，等于声称「这份 attempt=2 的修订是首轮
    # 产出的」——那是一个自相矛盾的状态，必须被修订重算抓住，而不是被静默读成首轮。
    forged = dict(wire)
    del forged["writer_attempt"]
    expect_error(lambda: NS.SectionDraft.from_dict(forged), NS.NarrativeSchemaError,
                 "删掉非首轮载荷的 `writer_attempt` 后必须被修订重算拒绝（不得静默退回首轮）",
                 needle="不符")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"passed={result['passed']} failed={result['failed']} skipped={result['skipped']}")
    sys.exit(1 if result["failed"] else 0)

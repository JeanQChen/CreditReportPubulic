"""Eval: M930-3 r9 后返修 **B** —— 一条原子、多处表达：`npr-1` 在**真实写入链**上的正反例。

用法: python -X utf8 -m evals.test_m930_3_prose_occurrence

**本模块要证的那一件事。** 门前草稿是按**材料出处**分段的：同一句事实（例如「公司采购模式以
集中采购为主」）在两份材料里各有一段正文时，草稿层天然会把它写两遍——每一遍是**那一份材料**
自己的表达（这也是本批要保留的东西：读者面重复由门后的组织器在**合格表达**里选，不由删段或
截字来消除）。旧判据要求「一条候选只能由一段草稿声明」，于是「同一段话在两份年报里都出现」
被判成不合法，整束 fail-closed。

修法**不是**删掉唯一性断言（删了它，「同一格把一件事说了两遍」也就过了），而是**版本化地**
允许一个原子候选在多段草稿里各出现一次，代价是每一次出现都要与**该候选自己的**支撑提案逐项
核对材料身份（`PROSE_OCCURRENCE_POLICY_VERSION = "npr-1"`；逐格的判据用例在
`evals.test_m930_3_sentence_fidelity` §6b）。

本模块**不重复**那些逐格用例，也不在类型层取证：它走**真实写入链**（`PW.write_section`）。
因为「判据写在函数里」与「生产入口上真的调用它、且失败时走 typed 通道、诊断不蒸发」是三件事：

* §1 **正向**：同一条候选在两段草稿里各出现一次，两段各自有一条落在**它自己那份材料**上的
      factual 支撑边 ⇒ 草稿**真的构造出来**、门不阻断、两段正文与各自的出处原样保留（不删段、
      不截字、不把两段合成一段），且这条候选的两处表达都能被
      `natural_prose_occurrences_for_candidate` 读回（不是只剩第一处）。
* §2 **反例**（三条，都走生产入口，逐条断言**类型化原因**，不是只断言「抛了」）：
      2.1 **错来源**：第二段草稿声明的材料上，这条候选**自己**没有任何支撑边；
      2.2 **漏候选**：某一候选没有任何草稿段声明它（候选必须从草稿正文长出来）；
      2.3 **单元内部重复**：同一段草稿把同一条候选声明两遍（类型层当场拒，不靠运行时判）。
      失败**不是**「静默 fail-closed」：被拒那一轮的候选、草稿正文与补件诉求按原样留在
      `retained_pre_gate`（`pgr-1`，标「未核验、不可发布」）与 `follow_up_needs` 里，且
      **不**产生 `SectionResult`、不留 `draft_revision` 身份。
* §3 **读法面**：`natural_prose_for_candidate` 是 `natural_prose_occurrences_for_candidate` 的
      **一元特例**（前者恒等于后者的第一项）——两处读法不得各算一遍。

公司无关：没有公司代号、文件名、页码、年份或答案关键词的生产字面量；不调真实 LLM（stub 只
回放夹具）、不联网、不读库、不写任何文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import writing_spec as WS

#: 与 `test_m930_3_writer_face` 逐字同源的三件壳：同一个 writing spec、同一个 presentation
#: profile、同一个正文上下文入口（`test_demo_pack_writer` 的 `_write` 定义在它的 `main()` 里，
#: 无法直接 import；这里只补壳，不改任何语义）。
_SPEC = WS.load_writing_spec(T.SPEC_PATH)
_PROFILE = PP.load_presentation_profile(T.PROFILE_PATH)
_PROJECTIONS = {sid: PW.ContractProjection.create(
    _SPEC, section_id=sid, contract_version=T.CONTRACT_VERSION,
    contract_fingerprint=T.CONTRACT_FINGERPRINT)
    for sid in ("company", "industry", "financial")}

#: 经营模式的两个 aspect（**冻结 Contract** 的 aspect id，不是公司相关的字面量）。
#: `test_demo_pack_writer` 里同名的那两个是它 `main()` 内部的局部量，不可 import。
ASP_PROCUREMENT = "company_business_model.procurement_mode"
ASP_PRODUCTION = "company_business_model.production_mode"


# ---------------------------------------------------------------------------
# 夹具文本：**两份材料**，同一句原子在两份里各出现一次
# ---------------------------------------------------------------------------

#: 材料 A：协议合作 + 采购模式。材料 B：采购模式 + 生产模式。两段的**分歧**部分各有一句，
#: 交集部分（采购模式那一句）逐字相同——这正是「同一段话在两份材料里都出现」的现场。
MAT_A = "公司通过长期协议与主要供应商保持合作。公司采购模式以集中采购为主。"
MAT_B = "公司采购模式以集中采购为主。公司生产模式以自建产线为主。"

#: 三条原子（都不带任何数字/期间措辞，因此与数字门、含糊期间门无关）。
C_AGREE = "公司通过长期协议与主要供应商保持合作"
C_PROC = "公司采购模式以集中采购为主"
C_PROD = "公司生产模式以自建产线为主"

#: 两段草稿：**各自**只写自己那份材料支持得住的话。共享原子在两段里各写一遍——两段的措辞
#: **不同**（这正是「两年相似表述不得被推成『始终如此』」的可执行形态：两处表达各自留在
#: 它自己的材料语境里，草稿层不把它们合并成一句全称句）。
DRAFT_A = "公司通过长期协议与主要供应商保持合作，采购模式以集中采购为主。"
DRAFT_B = "公司采购模式以集中采购为主，生产模式以自建产线为主。"

#: 经营模式这一组的体系句（门前叙述单元，不带任何事实原子）。
UNIT_TEXT = "公司拥有独立的研发、采购、生产和销售体系。"


def _write(task, authority, *, section_id: str, llm):
    """唯一公开入口的薄包装（与 `test_demo_pack_writer` / `test_m930_3_writer_face` 同源）。"""
    material_context = T._writer_material_context(
        authority.pack_set, task_id=str(task.task_id),
        section_id=str(section_id or task.section_id))
    return PW.write_section(
        task, authority, projection=_PROJECTIONS[section_id], writing_spec=_SPEC,
        presentation_profile=_PROFILE, llm_client=llm,
        dependency_fingerprint=T.DEPENDENCY_FINGERPRINT, material_context=material_context)


def _candidate_id(draft, text: str) -> str:
    """按**文本**取这条候选的现场身份（`ccand_…` 是内容派生的，夹具不自己拼一个）。

    三条候选的文本互不相同且逐字来自夹具，因此「恰有一条匹配」是可断言的；不匹配即抛——
    按顺序/按下标取会把「夹具以为的那条」与「链上那条」的错位变成一次静默的假通过。
    """
    hits = [str(c.candidate_id) for c in draft.claim_candidates
            if str(c.claim_text) == str(text)]
    if len(hits) != 1:
        raise AssertionError(
            f"文本 {text!r} 在本节 draft 里对应 {len(hits)} 条候选（期望恰 1 条）："
            f"{[str(c.claim_text) for c in draft.claim_candidates]}")
    return hits[0]


def _occurrence_case():
    """夹具：两份材料、三条候选、两段草稿（共享原子各写一遍）。

    返回 `(task, authority, pack_id, ref_a, ref_b, cand_specs)`；材料别名用**本节 manifest
    自己的顺序**取（`T._member_ref`），不猜 `m1`。
    """
    task = T._task("company", (T.TOPIC_BUSINESS,), task_id="task-co-occurrence")
    aspects = (T._AspectResult(ASP_PROCUREMENT, "partial"),
               T._AspectResult(ASP_PRODUCTION, "partial"))
    requirements = (T._Req(T.TOPIC_BUSINESS, (
        T._aspect(ASP_PROCUREMENT, T.TOPIC_BUSINESS, T._question_id(T.TOPIC_BUSINESS),
                  requirement_text="说明采购模式及其沿革。"),
        T._aspect(ASP_PRODUCTION, T.TOPIC_BUSINESS, T._question_id(T.TOPIC_BUSINESS),
                  requirement_text="说明生产模式及其沿革。"))),)
    material_a = T._material_of_identity("m-occ-a", "evidence:co-occ-a", page=15,
                                         text=MAT_A)
    material_b = T._material_of_identity("m-occ-b", "evidence:co-occ-b", page=16,
                                         text=MAT_B)
    authority = T._company_authority(task, facts=(), materials=(material_a, material_b),
                                     aspects=aspects, requirements=requirements)
    pack_id = str(authority.pack_set.packs[0].pack_id)
    ref_a = T._member_ref(authority, task_id=str(task.task_id), section_id="company",
                          material_id="m-occ-a")
    ref_b = T._member_ref(authority, task_id=str(task.task_id), section_id="company",
                          material_id="m-occ-b")
    specs = {"pack_id": pack_id, "ref_a": ref_a, "ref_b": ref_b}
    return task, authority, specs


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

    details.append("## §0 前提：两份材料、两段草稿、三条原子")
    task, authority, specs = _occurrence_case()
    pack_id, ref_a, ref_b = specs["pack_id"], specs["ref_a"], specs["ref_b"]
    # 前提一：交集那一句**逐字**出现在两份材料里（否则 §1 测不到「同一句话在两处」）。
    check(C_PROC in MAT_A and C_PROC in MAT_B,
          "前置条件：共享原子必须逐字出现在**两份**材料正文里")
    check(MAT_A != MAT_B and ref_a != ref_b,
          "前置条件：两段材料（与它们的别名）必须是两个不同身份")
    # 前提二：全部文本不含高风险表面——本模块要测的是**归属**政策，不是数字门；混进一个数字
    # 就会让失败原因变成「数字未授权」，读起来像是本判据红了。
    for label, text in (("材料 A", MAT_A), ("材料 B", MAT_B), ("草稿 A", DRAFT_A),
                        ("草稿 B", DRAFT_B), ("单元", UNIT_TEXT), ("原子 1", C_AGREE),
                        ("原子 2", C_PROC), ("原子 3", C_PROD)):
        check(NS.high_risk_surface_tokens(text) == (),
              f"前置条件：{label}不得含高风险表面（实测 {NS.high_risk_surface_tokens(text)}）")
    check(NS.vague_period_hits(DRAFT_A) == () and NS.vague_period_hits(DRAFT_B) == (),
          "前置条件：两段草稿都不得含含糊期间措辞（那是另一条门，不该在这里红）")
    check_eq(NS.PROSE_OCCURRENCE_POLICY_VERSION, "npr-1",
             "归属政策版本是登记的字面量（多 occurrence 是一版**政策**，不是一个开关）")

    # ==================================================================== §1 正向
    details.append("## §1 正向：一条候选、两处表达，走真实写入链")
    pos_plan = T._plan(
        candidates=[
            T._cand("c-agree", C_AGREE, T._material_edge(pack_id, "m-occ-a")),
            # 共享原子：主边在 A、corroborating 边在 B（两条边都属于**它自己**）。
            T._cand("c-proc", C_PROC, T._material_edge(pack_id, "m-occ-a"),
                    T._material_edge(pack_id, "m-occ-b", role="corroborating")),
            T._cand("c-prod", C_PROD, T._material_edge(pack_id, "m-occ-b")),
        ],
        prose=[T._prose("p-a", DRAFT_A, members=[ref_a], atoms=["c-agree", "c-proc"]),
               T._prose("p-b", DRAFT_B, members=[ref_b], atoms=["c-proc", "c-prod"])],
        units=[T._unit("u-system", UNIT_TEXT)])
    pos_outcome = _write(task, authority, section_id="company",
                         llm=T._StubLlm(pos_plan))
    check(not pos_outcome.gate_result.blocking,
          "一条候选在两段草稿里各出现一次（每段各有一条落在**它自己那份材料**上的支撑边）"
          "必须能构造出 `SectionDraft` 并过门前硬门"
          f"（实际门问题 {[i.rule_id for i in pos_outcome.gate_result.issues_of('blocking')][:4]}）")
    pos_draft = pos_outcome.draft
    # 1.1 两段草稿**都在**，且文本逐字未改（不删段、不截字、不合成一段）。
    check_eq(len(pos_draft.natural_prose_draft), 2,
             "两段草稿都必须留在产物里（读者面重复由门后组织器在合格表达里选，不在这里删段）")
    check_eq([str(u.text) for u in pos_draft.natural_prose_draft], [DRAFT_A, DRAFT_B],
             "两段正文必须逐字保留（既不得截断，也不得被合并成一段）")
    # 短别名（`m1`/`m2`）在写入侧展开成清单成员身份（`wmmref_…`）：断言要落在**展开后**的
    # 那一侧，否则「两段各自指到了哪一份材料」就退化成比两个夹具自己写的串。
    member_ref_of = {str(e.material_id): str(e.member_ref)
                     for e in pos_draft.material_manifest.entries}
    check_eq(sorted(member_ref_of), ["m-occ-a", "m-occ-b"],
             "前置条件：本节清单恰好装这两份材料（下面按 material_id 反查成员身份）")
    check_eq([tuple(u.source_member_refs) for u in pos_draft.natural_prose_draft],
             [(member_ref_of["m-occ-a"],), (member_ref_of["m-occ-b"],)],
             "每一段草稿保留**它自己的**材料出处（共享原子不等于两段共用一份出处）")
    # 1.2 三条候选全部进入 Draft（共享原子不因「只留一处」而少写）。
    check_eq(len(pos_draft.claim_candidates), 3,
             "三条候选必须全部进入 Draft（不得只因多了一处表达就少写一条）")
    agree_id = _candidate_id(pos_draft, C_AGREE)
    proc_id = _candidate_id(pos_draft, C_PROC)
    prod_id = _candidate_id(pos_draft, C_PROD)
    check_eq([tuple(str(a) for a in u.atom_candidate_ids)
              for u in pos_draft.natural_prose_draft],
             [(agree_id, proc_id), (proc_id, prod_id)],
             "两段各自的原子表按**声明顺序**落到现场身份上（共享那条在两段里都指向同一身份）")
    check(agree_id != proc_id != prod_id,
          "三条原子必须是三个不同身份（否则下面的 occurrence 断言无从定位）")
    # 1.3 两处表达都能被读回——这正是「不是只剩第一处」的可执行形态。
    occurrences = pos_draft.natural_prose_occurrences_for_candidate(proc_id)
    check_eq(tuple(str(u.prose_unit_id) for u in occurrences),
             tuple(str(u.prose_unit_id) for u in pos_draft.natural_prose_draft),
             "共享原子的**两处**表达必须都能读回（按草稿顺序），且顺序与草稿一致")
    check_eq(len(pos_draft.natural_prose_occurrences_for_candidate(agree_id)), 1,
             "只出现一次的原子照旧只读回一处（多 occurrence 不是「恒返两处」）")
    check_eq(pos_draft.natural_prose_occurrences_for_candidate(prod_id),
             (pos_draft.natural_prose_draft[1],),
             "另一条只出现一次的原子读回的是**它自己**那一处（不是第一处）")
    # 1.4 共享原子的两条 factual 支撑边分别落在两份材料上（归属核对拿它当核对面）。
    proc_edges = [p for p in pos_draft.proposed_support_refs
                  if str(p.binding_subject_id) == proc_id]
    check_eq(sorted(str(p.material_id) for p in proc_edges),
             ["m-occ-a", "m-occ-b"],
             "共享原子**自己**的支撑边必须落在两段草稿各自声明的那份材料上（核对面就在这里）")
    check(all(str(p.support_semantics) == "factual" for p in proc_edges),
          "两条边都必须是 factual 语义（context 边不得给候选撑事实）")
    # 1.5 两份材料都被消费（不得靠删掉其中一份材料来「达标」）。
    check_eq([str(d.usage) for d in pos_draft.material_dispositions], ["used", "used"],
             "两份材料都必须登记为 used：共享原子的两处表达各消费一份，不删任何一份")
    # 1.6 两处表达各留自己那半事实：共享原子之外，每段还带**另一段没有**的原子。
    #     把差异原子合并掉（或把两段合成一句「始终如此」的全称句），这一条就红。
    u1_atoms = set(str(a) for a in pos_draft.natural_prose_draft[0].atom_candidate_ids)
    u2_atoms = set(str(a) for a in pos_draft.natural_prose_draft[1].atom_candidate_ids)
    check(DRAFT_A != DRAFT_B,
          "两段草稿必须是两处**不同**的表达（同一段文字被抄两遍不是「多 occurrence」）")
    check(u1_atoms - u2_atoms == {agree_id} and u2_atoms - u1_atoms == {prod_id},
          "两段各自保留**它自己那半事实**（只有共享原子为两段共有）：把差异原子合并掉，"
          "就等于把两份材料的差别抹成一句全称句"
          f"（实得 {sorted(u1_atoms - u2_atoms)} / {sorted(u2_atoms - u1_atoms)}）")

    # ==================================================================== §2 反例
    details.append("## §2 反例：错来源 / 漏候选 / 段内重复（逐条断言类型化原因）")

    #: 补件诉求的落点取自本节 Contract 的真实要求（不由夹具自造一个 id）。
    requirement_id = PW.scan_authority(authority, task).requirement_ids[T.TOPIC_BUSINESS]
    follow_up_spec = T._follow_up(
        "需要补充采购模式的更多材料。", target_requirement_id=requirement_id,
        topic_id=T.TOPIC_BUSINESS, question_id=T._question_id(T.TOPIC_BUSINESS),
        aspect_id=ASP_PROCUREMENT)

    def _rejected(plan, label: str) -> PW.ProposalSetRejectedError:
        """跑一次注定被拒的写作，返回 typed 异常（类型不符即抛，不静默降级）。"""
        plan = dict(plan)
        plan["follow_up_needs"] = [dict(follow_up_spec)]
        try:
            _write(task, authority, section_id="company", llm=T._StubLlm(plan))
        except PW.ProposalSetRejectedError as exc:
            return exc
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"{label}：期望整束 typed 拒绝，实抛 {type(exc).__name__}: {str(exc)[:200]}"
            ) from exc
        raise AssertionError(f"{label}：未拒绝（这一束被采信了）")

    def _assert_typed_rejection(exc, label: str, needle: str) -> None:
        check(isinstance(exc, PW.PackWriterError),
              f"{label}：拒绝必须走在写的 typed 通道上（`PackWriterError` 一族），"
              f"实际 {type(exc).__name__}")
        check_eq(tuple(str(r.rejection_kind) for r in exc.rejections),
                 ("natural_draft_not_closed",),
                 f"{label}：被拒原因是**草稿闭合**（`natural_draft_not_closed`），"
                 "不是别的原因冒充")
        record = exc.rejections[-1]
        check(needle in str(record.rejection_detail),
              f"{label}：拒绝理由必须点名**这一条**判据（缺 {needle!r}）"
              f"（实际 {str(record.rejection_detail)[:240]!r}）")
        # 逐候选审计：键集恒等于完整有序候选身份（结构化 kind 必带，不得裁剪）。
        check_eq(len(record.candidate_audit), len(record.candidate_ids),
                 f"{label}：逐候选审计必须覆盖**全部**候选（不得只留幸存者）")
        check_eq(tuple(str(a.candidate_id) for a in record.candidate_audit),
                 tuple(str(c) for c in record.candidate_ids),
                 f"{label}：逐候选审计的顺序与身份必须与 `candidate_ids` 逐项相同")
        check(len(record.candidate_ids) == 3,
              f"{label}：三条候选**全部**在册（不得先删掉一条再报「拒了整束」）")
        # 门前留存：只读诊断，带自己的版本与标签，且**不带**任何 draft 身份。
        retained = record.retained_pre_gate
        check(retained is not None, f"{label}：整束被拒时必须留存门前已写出的内容")
        retained_dict = retained.to_dict()
        check_eq(tuple(retained_dict),
                 ("version", "label", "basis", "batches", "prose_unit_total", "note"),
                 f"{label}：留存面的键集与键序是登记的那一份（不得临时加列）")
        check_eq(retained_dict["label"], PW.PRE_GATE_RETENTION_LABEL,
                 f"{label}：留存必须自报「未核验、不可发布」")
        check_eq(PW.PRE_GATE_RETENTION_LABEL, "未核验、不可发布",
                 "留存标签是登记的字面量（复核者据此判读）")
        check_eq(retained_dict["version"], PW.PRE_GATE_RETENTION_VERSION,
                 f"{label}：留存面必须带自己的版本（`pgr-1`）")
        check(retained_dict["basis"] in PW.PRE_GATE_RETENTION_BASES,
              f"{label}：留存来源必须是封闭三态之一（实得 {retained_dict['basis']!r}）")
        check("draft_revision" not in json.dumps(retained_dict, ensure_ascii=False),
              f"{label}：留存**不得**带 `draft_revision`——它不是 `SectionDraft`，"
              "把未核验的门前草稿冒充成核验过的产物就是这条断言要拦的")
        check_eq(retained_dict["prose_unit_total"], 2,
                 f"{label}：留存里必须留下**两段**门前草稿正文（模型当时真的写出了这两段）")
        # 补件诉求不随异常蒸发（r9 现场是 20 条一起没了）。
        check_eq(len(exc.follow_up_needs), 1,
                 f"{label}：模型提出的补件诉求必须随 typed 拒绝带出，不得随异常蒸发")
        check_eq(tuple(exc.follow_up_untypeable), (),
                 f"{label}：本夹具的诉求是合法的，因此不得出现「不成立」那一类")
        check_eq(record.follow_up_count, 1,
                 f"{label}：记录里的诉求条数必须与带出的诉求集一致")

    # 2.1 错来源：第二段声明的材料上，这条候选**自己**没有支撑边。
    wrong_source_plan = T._plan(
        candidates=[
            T._cand("c-agree", C_AGREE, T._material_edge(pack_id, "m-occ-a")),
            # 只有 A 上的一条边，草稿却在 B 那段里又声明了它。
            T._cand("c-proc", C_PROC, T._material_edge(pack_id, "m-occ-a")),
            T._cand("c-prod", C_PROD, T._material_edge(pack_id, "m-occ-b")),
        ],
        prose=[T._prose("p-a", DRAFT_A, members=[ref_a], atoms=["c-agree", "c-proc"]),
               T._prose("p-b", DRAFT_B, members=[ref_b], atoms=["c-proc", "c-prod"])],
        units=[T._unit("u-system", UNIT_TEXT)])
    _assert_typed_rejection(
        _rejected(wrong_source_plan, "错来源"),
        "错来源",
        "没有一条落在本段的来源上")

    # 2.2 漏候选：某一候选没有被任何草稿段声明（候选必须从草稿正文长出来）。
    uncovered_plan = T._plan(
        candidates=[
            T._cand("c-agree", C_AGREE, T._material_edge(pack_id, "m-occ-a")),
            T._cand("c-proc", C_PROC, T._material_edge(pack_id, "m-occ-a"),
                    T._material_edge(pack_id, "m-occ-b", role="corroborating")),
            T._cand("c-prod", C_PROD, T._material_edge(pack_id, "m-occ-b")),
        ],
        prose=[T._prose("p-a", DRAFT_A, members=[ref_a], atoms=["c-agree", "c-proc"]),
               T._prose("p-b", DRAFT_B, members=[ref_b], atoms=["c-proc"])],
        units=[T._unit("u-system", UNIT_TEXT)])
    _assert_typed_rejection(
        _rejected(uncovered_plan, "漏候选"),
        "漏候选",
        "候选未被任何自然草稿单元声明")

    # 2.3 单元内部重复：同一段把同一条候选声明两遍。这一格在**类型层**就构造不出来
    #     （frozen + `atom_candidate_ids` 去重），因此它不经过写入链，而是直接对这一层断言。
    try:
        NS.NaturalProseDraftUnit.create(
            index=0, draft_revision="rev-x", section_id="company", text=DRAFT_A,
            source_member_refs=[ref_a], atom_candidate_ids=["ccand-x", "ccand-x"])
    except Exception as exc:  # noqa: BLE001
        check("重复" in str(exc) or "dup" in str(exc).lower(),
              f"段内重复必须在类型层当场拒，且理由点名「重复」（实际 {str(exc)[:160]!r}）")
    else:
        check(False, "同一段草稿把同一条候选声明两遍必须被拒（类型层）")

    # ==================================================================== §3 读法面
    details.append("## §3 读法面：一元反查是多项反查的特例")
    for cand in pos_draft.claim_candidates:
        cid = str(cand.candidate_id)
        occurrences = pos_draft.natural_prose_occurrences_for_candidate(cid)
        first = pos_draft.natural_prose_for_candidate(cid)
        check(first is occurrences[0],
              f"候选 {cid}：一元反查必须**就是**多项反查的第一项（两处读法不得各算一遍）")
    check(pos_draft.natural_prose_for_candidate("ccand-none") is None,
          "不存在的候选一元反查返回 None（空集不是「第一处为空」）")
    check_eq(pos_draft.natural_prose_occurrences_for_candidate("ccand-none"), (),
             "不存在的候选多项反查返回空元组（与「没有草稿表达」同一读数）")

    # 现场读数（人读用）：本模块不产出任何正式产物，只打印这一批的字面量。
    details.append(
        f"NOTE §1 正向现场：候选 {proc_id}（{C_PROC}）在两段草稿里各出现一次，"
        f"两段出处分别是 {ref_a} / {ref_b}；两段正文逐字保留，两条材料都登记为 used。")
    details.append(
        "NOTE §2 三条反例都走生产入口，被拒原因逐条为 `natural_draft_not_closed`，"
        "留存面 `pgr-1` 标「未核验、不可发布」，补件诉求随 typed 拒绝带出、不蒸发。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

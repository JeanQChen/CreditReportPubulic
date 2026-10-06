"""Eval: M930-2 完整 Pack set 读门（`sections.pack_set`）。

用法: python -m evals.test_demo_pack_set

输入身份**只能**来自公开入口（返修裁决 §十二 item 6）：`planning.demo_scope` →
冻结 Contract v2 → `build_scope_input_manifest` → `project_contract_v2_scope` →
`verify_demo_projection`。公司/文档/Evidence set 取自真实样本与只读 evidence DB；task /
requirement / aspect 快照 / SourcePolicyRef / 依赖集全部来自冻结投影。**不再**手写
`t_company_business` / `a1` / `sp-eval` / `producer_kind="company"`，也不挑「span 最多的节点」
当导航键。

正例：三个真实 selected 公司 topic 由同一个 `SectionTask` 承载并由唯一正式链（树工具 →
runtime → 真提交）产出 current Pack，再经 `resolve_pack_set` 通过全部读门。

反例：全部**挂在真实 task/requirement 上做单轴漂移注入**（task_id / section_id /
report_as_of / 依赖集 / aspect 集 / 冻结快照 / question 集），no-current 反例用同一投影里
**真实存在但未研究**的财务 task+requirement。另有两类只能经 Store 协议替身证明的
防御性反例（错 identity 的 current 行、自报权威与重算不符的材料、同槽异版本 current），
如实标注为防御性证明，不假装真实路径可复现。

M930-3B（P4）追加 §6b–§6e：读门对 successor exact-set 的**独立重算**（material↔RMD、
candidate↔资格决定、eligible/rejected↔qualified result、decision 输入身份与 digest、
formal `ExternalFact` 闭合、gap/block 的 Contract basis）在**真实提交的 current Pack** 上做
单点变异验证；`ExternalFact` 的真实产出链尚未接线，故用唯一工厂合成一条 external 链挂到
真实 Pack 上（先证良构零 block，再逐项破坏）。在 Pack 构造期就被 `verify_pack_successor_sets`
拒掉的形状（缺/多/重/跨 kind/双结果/孤儿）如实登记为「读门是第二道防线」，不假装经 Store 可达。

M930-3（§三 F）追加 §6f：follow-up **后继语义**在真库上的贯通——同一批真 requirement 先提交
三条 current，再由唯一入口 `run_follow_up_needs` 对其中**一条真有材料的 aspect** 发起 follow-up，
证明：后继是**完整** aspect/question 集（同 identity、同依赖指纹）且成为新 current、未重研的
aspect 逐字段继承、need 的 statement/授权范围/来源类/理由真的进入了被使用的 `InformationNeed`、
旧 Pack 在前后两次独立读回中逐字段未变（未回写）、后继能通过 P4 读门，以及两条 typed 拒绝码
（`successor_base_missing` / `successor_base_incomplete`）与授权越界在真库上真的触发且不产出
任何新 Pack。本段在**自己的临时库**上跑，并用 `_in_topic_db` 显式绑定库路径（模块级
`topic_store` 函数按「最近一次 init 的路径」工作，绑错库会让断言看错对象）。

不调 LLM、不联网；Pack 只提交到**临时** SQLite，真实库只读；缺真实样本时如实 skip。
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.loader_v2 import load_contract_v2
from document_structure import live_span_source as LSS
from evals import test_demo_external_fact as EXT
from document_structure import navigation as NAV
from document_structure import synopsis as SY
from harness import r2_dependencies as R2
from harness import schema as HS
from harness import source_policy_resolver as SPR
from harness import topic_runtime as TR
from harness import topic_schema as TS
from harness import topic_store as Store
from harness import tree_tools as TT
from planning import demo_scope as SC
from planning import schema as PS
from routing import schema as RS
from sections import pack_set as PSet
from tools import adapters as A
from tools import contracts as C

REPO = Path(__file__).resolve().parent.parent
BUDGET_TIER = "demo_backbone"
STARTED_AT = "2026-09-20T00:00:00Z"
#: 与 runtime 同一套停止原因（替身不得自造第二套词表）。
STOP_NOT_FOUND = "NOT_FOUND_AFTER_SEARCH"
STOP_NO_SEARCHABLE_SCOPE = "NO_MORE_HIGH_VALUE_ACTION"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sample_pdf() -> Path | None:
    for path in sorted((REPO / "data/samples").glob("*/announcements/*.pdf")):
        return path
    return None


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
                details.append(f"FAIL {msg}：原因不符（{str(e)[:120]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:120]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    def blocked(fn, msg: str, *, expect: str) -> tuple[str, ...]:
        """要求 `PackSetBlocked`，且**指定原因码**必须出现；返回全部原因码供附加断言。"""
        nonlocal passed, failed
        try:
            fn()
        except PSet.PackSetBlocked as e:
            reasons = tuple(b.reason for b in e.blocks)
            if expect not in reasons:
                failed += 1
                details.append(f"FAIL {msg}：原因码缺 {expect!r}（实为 {reasons}）")
            else:
                passed += 1
            return reasons
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:140]}）")
            return ()
        failed += 1
        details.append(f"FAIL {msg}：未 block")
        return ()

    pdf = _sample_pdf()
    evidence_db = REPO / "data/evidence.db"
    if pdf is None or not evidence_db.exists():
        skipped += 1
        details.append("SKIP 缺 live 样本或 data/evidence.db")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    from evidence import store as estore
    estore._db_path = evidence_db.resolve()
    company, sha = pdf.parents[1].name, hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc_version = "sha256-" + sha[:16]
    set_version = estore.current_evidence_set_ro(
        evidence_db.resolve(), company, pdf.stem, doc_version)
    if not isinstance(set_version, str) or set_version == "":
        skipped += 1
        details.append("SKIP Evidence 库里没有该文档的 current set（不猜值）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
        company_id=company, document_id=pdf.stem, document_version=doc_version,
        raw_pdf_path=str(pdf), raw_pdf_sha256=sha,
        expected_current_evidence_set_version=set_version))

    # =================== 真实 M930-1 权威输入 ==============================
    profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
    policy_resolver = SPR.FrozenSourcePolicyResolver.from_asset(
        REPO / profile.source_policy_asset)
    contract = load_contract_v2(str(REPO / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_m930_2_pack_set_0001", company_id=company, company_name=company,
        credit_type="general", report_as_of="2026-06-30", contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_2_pack_set_0001",
        "document_id": pdf.stem, "document_version": doc_version,
        "raw_pdf_sha256": sha, "current_evidence_set_version": set_version,
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest = SC.build_scope_input_manifest(profile, business, source_inputs)
    projection = SC.project_contract_v2_scope(contract, profile, manifest)
    SC.verify_demo_projection(projection, contract)

    reqs = {r.topic_id: r for r in projection.requirements}
    tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
    company_task = tasks["company"]
    financial_task = tasks["financial"]
    company_reqs = tuple(reqs[t] for t in company_task.topic_ids)
    financial_reqs = tuple(reqs[t] for t in financial_task.topic_ids)
    check(len(company_task.topic_ids) >= 2 and len(company_reqs) >= 2,
          "真实投影给出多个 selected 公司 topic（本读门要验跨 topic 顺序）")

    budget = TR.ResearchBudgetPolicy(
        policy_id=profile.budget_policy_id, version=profile.budget_policy_version,
        tier=BUDGET_TIER, max_need_rounds_per_aspect=2, max_need_rounds_per_topic=2,
        max_tool_calls_per_topic=3 * len(reqs[company_task.topic_ids[1]].aspects),
        max_tokens_per_topic=200000,
        max_llm_calls_per_topic=3 * len(reqs[company_task.topic_ids[1]].aspects),
        max_elapsed_ms_per_topic=900000,
        tree_max_spans_per_aspect=6, tree_max_chars_per_span=4000)

    with tempfile.TemporaryDirectory() as td:
        reg, session = A.build_tree_bound_registry(
            live_span_source=live, audit_dir=Path(td) / "audit")
        snapshot, outline = live.snapshot, live.document_outline
        node_ids = sorted({n.node_id for n in outline.nodes})
        synopses = SY.build_navigation_synopses(
            node_ids=node_ids, spans=snapshot.spans, coverages=snapshot.coverages,
            dispositions=snapshot.dispositions, policy=live.qualification_policy)
        index = NAV.NavigationIndex(
            outline, synopses, span_node_ids={s.node_id for s in snapshot.spans})
        if index.unavailable_reason is not None:
            skipped += 1
            details.append(f"SKIP 该文档的导航索引不可用（{index.unavailable_reason}）")
            return {"passed": passed, "failed": failed, "skipped": skipped,
                    "details": details}

        db = Path(td) / "h.db"
        store = TR.SqliteTopicStoreAdapter(db)
        store.init()
        _, _sp, scv, sev = R2.build_r2_material_dependencies(db)
        reader = PSet.SqlitePackSetStore(path=db, resolver=session.payload_resolver)
        sink = _Sink()
        document = TR.DocumentIdentity(**session.document_identity())

        def _navigation_for(requirement):
            """导航 profile 与生产侧**同源**装配。

            `ancestor_labels` / `sibling_keys` 必须照生产传
            （`run_m930_3_acceptance._deps` 用 `NAV.contract_ancestor_inputs(contract)`）：
            `anp-6` 的读根依赖 `parent_keys`（父主题标题层）与兄弟项排除才能落到真实读根；
            不传 ⇒ `parent_keys=[]`、该层读根整片消失（或兄弟栏目名重新混入），本测试跑的
            就不是生产那条链（"少跑了"却仍可能全绿）。
            """
            _labels, _siblings = NAV.contract_ancestor_inputs(contract)
            nav_profile = NAV.build_navigation_profile(
                requirement.aspects, contract_version=requirement.contract_version,
                contract_fingerprint=requirement.contract_fingerprint,
                ancestor_labels=_labels, sibling_keys=_siblings)
            return TR.IndexedTreeNavigation(index=index, profile=nav_profile)

        def _requirement_context(requirement, *, run_id: str):
            return TR.TopicRunContext(
                run_id=run_id, case_id=run_id + "-case",
                company_id=requirement.company_id, section_id=requirement.section_id,
                task_id=requirement.task_id, report_as_of=requirement.report_as_of,
                demo_scope_fingerprint=manifest.scope_input_fingerprint,
                projection_version=projection.projection_id, document=document,
                sources=TS.DocumentSourceSet.single_document(
                    company_id=document.company_id, document_id=document.document_id,
                    document_version=document.document_version,
                    evidence_set_version=document.evidence_set_version),
                budget_policy=budget, started_at=STARTED_AT)

        def _deps_for(requirement, *, run_id: str, active_store, research=None):
            """一套依赖装配：`_run`（首轮）与 `run_follow_up_needs`（后继）走**同一套**运行时。

            §三 F 只允许一种运行时：后继不是「第二套研究链」，而是同一入口在 `successor_of`
            模式下的又一次调用，因此依赖装配必须共用（含同一个 store、同一个树导航、同一个
            替身 `research_question` 出口）。
            """
            navigation = _navigation_for(requirement)
            ctx = _requirement_context(requirement, run_id=run_id)
            if research is None:
                research = _SpanRecallResearch(
                    navigation=navigation, requirement=requirement, document=document,
                    tree_max_spans=budget.tree_max_spans_per_aspect,
                    tree_max_chars=budget.tree_max_chars_per_span)
            deps = TR.TopicRuntimeDependencies(
                registry=reg, llm=object(), navigation=navigation,
                information_need_builder=_NeedBuilder(),
                route_context_builder=lambda: _route_context(requirement.company_id),
                route_fn=_route_fn,
                budget_state=TR.TopicBudgetState(policy=budget), trace_sink=sink,
                store=active_store, payload_resolvers=(session.payload_resolver,),
                source_policy_resolver=policy_resolver,
                set_completeness_verifier=scv, set_enumeration_verifier=sev,
                clock=lambda: STARTED_AT, research_question=research)
            return ctx, deps, research

        def _run(requirement, *, run_id: str, active_store=None, research=None):
            ctx, deps, _ = _deps_for(
                requirement, run_id=run_id,
                active_store=store if active_store is None else active_store,
                research=research)
            return TR.run_topic_requirement(requirement, run_context=ctx, dependencies=deps)

        results = {}
        for offset, requirement in enumerate(company_reqs):
            results[requirement.topic_id] = _run(
                requirement, run_id=f"m930-2-packset-{offset}")

        # ================= 1. 正例（真实 task + 真实 requirements）=========
        good = PSet.resolve_pack_set(company_task, company_reqs, reader)
        check(good.gate_version == PSet.PACK_SET_GATE_VERSION,
              "读门版本等于当前具名常量")
        check(good.task_id == company_task.task_id
              and good.section_id == company_task.section_id
              and good.topic_ids == company_task.topic_ids
              and tuple(p.topic_id for p in good.packs) == good.topic_ids,
              "topic 顺序 = 真实 SectionTask.topic_ids，且与 packs 逐位一致")
        for topic_id, runtime_result in results.items():
            check(good.pack_for(topic_id).pack_id == runtime_result.pack_id,
                  f"{topic_id}: pack_for 拿到本次真实提交的 Pack")
        check(good.binding.task_id == company_task.task_id
              and good.binding.section_id == company_task.section_id
              and good.binding.company_id == projection.company_id
              and good.binding.report_as_of == manifest.report_as_of
              and good.binding.contract_fingerprint
              == company_reqs[0].contract_fingerprint
              and good.binding.source_policy_version == projection.source_policy_version
              and good.binding.dependency_fingerprint
              == company_reqs[0].dependency_fingerprint(),
              "绑定对象逐字段等于真实 requirement 身份（读门不替调用方编身份）")
        check(good.binding.dependency_versions
              == dict(sorted(company_reqs[0].dependency_versions.items()))
              and len(good.binding.dependency_versions) == len(TS.DEPENDENCY_VERSION_KEYS),
              "绑定记录的依赖版本就是冻结投影的当前依赖集（精确等集，无增无减）")
        check(company_reqs[0].dependency_versions
              == TS.build_current_dependency_versions(
                  contract_version=company_reqs[0].contract_version,
                  source_policy_version=company_reqs[0].source_policy_version),
              "冻结投影的依赖集 = 当前依赖集公共工厂（不是手写的旧版本）")
        check(bool(good.materials()),
              f"真实材料穿过读门（实测 {len(good.materials())} 条）")
        payload_ok = True
        for material in good.materials():
            try:
                TS.verify_material_payload_ref(material.payload_ref, session.payload_resolver)
            except TS.SchemaValidationError:
                payload_ok = False
        check(payload_ok, "通过读门的每条材料 payload 都能独立重切")
        check(good.aspect_ids()
              == tuple(a.aspect_id for r in company_reqs for a in r.aspects),
              "selected aspect 精确覆盖真实 requirement 的 aspect 集（不缺不重）")
        check(tuple(r.topic_id for r in good.requirements) == good.topic_ids,
              "读门回带的 requirement 顺序与 topic_ids 一致")

        # 缺口/未找到记录未被过滤：门自证的双轴不变量在返回对象上仍成立
        for pack in good.packs:
            cov = pack.coverage_status
            grouped = (set(cov.covered_aspect_ids) | set(cov.gap_aspect_ids)
                       | set(cov.not_applicable_aspect_ids))
            check(grouped == {r.aspect_id for r in pack.aspect_results}
                  and len(grouped) == len(cov.covered_aspect_ids)
                  + len(cov.gap_aspect_ids) + len(cov.not_applicable_aspect_ids),
                  f"{pack.topic_id}: 双轴 coverage 分组精确划分全部 aspect")
            gap_ids = {g.unresolved_id for g in pack.unresolved}
            check(all(u in gap_ids for r in pack.aspect_results
                      for u in r.unresolved_ids),
                  f"{pack.topic_id}: 每条 aspect 引用的 unresolved 都真在 Pack.unresolved 中")
        check(len(good.gaps()) == sum(len(p.unresolved) for p in good.packs)
              and len(good.not_found_audits())
              == sum(len(p.not_found_audits) for p in good.packs),
              "读门视图逐 Pack 拼接（不去重、不筛选）")
        details.append("NOTE 正例实测: " + json.dumps(
            {p.topic_id: {"aspects": len(p.aspect_results),
                          "materials": len(p.materials), "facts": len(p.facts),
                          "gaps": len(p.unresolved),
                          "nf_audits": len(p.not_found_audits)}
             for p in good.packs}, ensure_ascii=False))

        expect_error(lambda: good.pack_for("topic_not_in_set"), PSet.PackSetError,
                     "pack_for 未知 topic 必须报错（不返回 None）")
        expect_error(lambda: PSet.PackSetBlock(reason="bogus_reason", detail="x"),
                     PSet.PackSetError, "未登记的 block 原因码必须拒")
        expect_error(lambda: dataclasses.replace(good, gate_version="psg-0"),
                     PSet.PackSetError, "旧门结论不得在消费方复活")
        expect_error(lambda: PSet.resolve_pack_set(
            company_task, company_reqs, PSet.SqlitePackSetStore(path=db)),
            PSet.PackSetError, "缺 resolver 的 Store 必须 fail-closed", needle="resolver")

        # ================= 2. topic 集合与用法反例 =========================
        blocked(lambda: PSet.resolve_pack_set(
            dataclasses.replace(company_task,
                                topic_ids=company_task.topic_ids + ("topic_ghost",)),
            company_reqs, reader),
            "SectionTask 多一个 topic 必须 block", expect="requirements_topic_set_invalid")
        blocked(lambda: PSet.resolve_pack_set(
            dataclasses.replace(
                company_task, topic_ids=company_task.topic_ids + (
                    company_task.topic_ids[0],)),
            company_reqs, reader),
            "SectionTask topic 重复必须 block", expect="task_topic_set_invalid")
        blocked(lambda: PSet.resolve_pack_set(
            dataclasses.replace(company_task, topic_ids=company_task.topic_ids[:1]),
            company_reqs, reader),
            "requirements 与 task 的 topic 集合不等集必须 block",
            expect="requirements_topic_set_invalid")
        expect_error(lambda: PSet.resolve_pack_set(company_task, (), reader),
                     PSet.PackSetError, "空 requirements 不得冒充完整 Pack set")
        expect_error(lambda: PSet.resolve_pack_set(
            company_task, ({"topic_id": company_task.topic_ids[0]},), reader),
            PSet.PackSetError, "非 TopicResearchRequirement 必须拒")
        expect_error(lambda: PSet.resolve_pack_set(None, company_reqs, reader),
                     PSet.PackSetError, "非 SectionTask 必须拒")

        # ================= 3. 身份/绑定/依赖反例（真实 req 单轴漂移）=======
        # 每个反例只动**一根轴**，其余 requirement 原样保留：这样 block 的原因必然出自被动的
        # 那一项，而不是「topic 集合不齐」这类前置门先响。
        first, second = company_reqs[0], company_reqs[1]

        def _swap(index: int, **changes):
            return tuple(dataclasses.replace(r, **changes) if i == index else r
                         for i, r in enumerate(company_reqs))

        blocked(lambda: PSet.resolve_pack_set(
            company_task, _swap(1, task_id="task-drifted"), reader),
            "requirement.task_id 与 SectionTask 不符必须 block",
            expect="requirement_task_mismatch")
        blocked(lambda: PSet.resolve_pack_set(
            company_task, _swap(1, section_id="industry"), reader),
            "requirement.section_id 与 SectionTask 不符必须 block",
            expect="requirement_section_mismatch")
        blocked(lambda: PSet.resolve_pack_set(
            company_task, _swap(1, report_as_of="2000-01-01"), reader),
            "requirement 之间 report_as_of 不唯一必须 block",
            expect="requirement_binding_not_unanimous")
        # 过期依赖集只能是**全体一致的旧版本**：单条漂移会先被「绑定不一致」拦下，永远走不到
        # 新鲜度检查。真实场景是整组 requirement 都带着旧 runtime 版本（历史 run 遗留）。
        stale_deps = dict(second.dependency_versions)
        stale_deps["topic_runtime"] = "tr-STALE"
        stale_reqs = tuple(dataclasses.replace(r, dependency_versions=stale_deps)
                           for r in company_reqs)
        reasons = blocked(lambda: PSet.resolve_pack_set(
            company_task, stale_reqs, reader),
            "过期依赖集必须 block", expect="requirement_dependency_stale")
        check(reasons.count("requirement_dependency_stale") == len(company_reqs),
              f"每个 requirement 各报一条过期依赖（实得 {reasons}）")
        check("current_pack_dependency_mismatch" in reasons,
              "旧依赖集下 current 的依赖指纹也不再匹配（同一条缺陷的两个面）")

        # ================= 4. 没有 current 的真实 topic ====================
        # 财务 task 与它的 requirement 都来自同一冻结投影（真实身份），只是本轮没研究过：
        # 因此读门必须如实报 no_current，而不是让财务 Pack 冒充公司 task 的输入。
        reasons = blocked(lambda: PSet.resolve_pack_set(
            financial_task, financial_reqs, reader),
            "真实存在但未研究的 topic 必须 block", expect="no_current_pack")
        check(reasons.count("no_current_pack") == len(financial_reqs),
              f"每个未研究 topic 各报一条 no_current_pack（实得 {reasons}）")

        # ================= 5. 内容反例（走真实 current，只改读门输入）======
        ghost = dataclasses.replace(first.aspects[0],
                                    aspect_id=first.aspects[0].aspect_id + ".ghost")
        blocked(lambda: PSet.resolve_pack_set(
            company_task, _swap(0, aspects=first.aspects + (ghost,)), reader),
            "aspect 集合混入额外投影必须 block", expect="aspect_set_mismatch")
        blocked(lambda: PSet.resolve_pack_set(
            company_task, _swap(0, aspects=(
                dataclasses.replace(first.aspects[0],
                                    canonical_fingerprint=_sha("tampered")),
            ) + first.aspects[1:]), reader),
            "冻结投影被改坏必须 block", expect="aspect_snapshot_mismatch")
        blocked(lambda: PSet.resolve_pack_set(
            company_task, _swap(0, question_ids=("q-drifted",)), reader),
            "question 集合不符必须 block", expect="question_set_mismatch")
        blocked(lambda: PSet.resolve_pack_set(
            company_task, company_reqs,
            PSet.SqlitePackSetStore(path=db, resolver=_NullResolver())),
            "payload 重切不出来的 Pack 必须 block", expect="payload_unresolvable")

        # ================= 6. 防御性反例（Store 协议替身）==================
        # 读门的存在理由就是「不信任库里的行」。下面用符合 `PackSetStore` 协议的替身模拟被改过
        # 的行。「错 identity 的 current」在公开读 API 上**走不到**：错 identity 的 current 落在
        # 别的 key 上，正确 identity 只会得到 no_current（第 4 节已证）。故这里只证明「万一读到
        # 这样的行，门会拒绝」，不假装可真实复现。
        committed = Store.load_current_pack(results[first.topic_id].identity).pack
        check(committed is not None, "防御性反例的前提：真实 current 已提交")
        if committed is not None:
            wrong_identity = dataclasses.replace(committed,
                                                 source_policy_version="drifted")
            blocked(lambda: PSet.resolve_pack_set(
                company_task, company_reqs,
                _FakeStore(wrong_identity, session.payload_resolver)),
                "读到错 identity 的 current 行必须 block",
                expect="current_pack_identity_mismatch")
            blocked(lambda: PSet.resolve_pack_set(
                company_task, company_reqs,
                _FakeStore(committed, session.payload_resolver, foreign=wrong_identity)),
                "同槽异版本 current 必须 block（读门自己发现冒充者）",
                expect="foreign_current_for_topic")

            if committed.materials:
                real = committed.materials[0].authority_assessment
                flip = "rejected" if real.verdict == "authoritative" else "authoritative"
                forged = dataclasses.replace(
                    committed.materials[0],
                    authority_assessment=dataclasses.replace(real, verdict=flip))
                forged_pack = dataclasses.replace(
                    committed, materials=(forged,) + committed.materials[1:])
                blocked(lambda: PSet.resolve_pack_set(
                    company_task, company_reqs,
                    _FakeStore(forged_pack, session.payload_resolver)),
                    "自报权威与重算不符的 material 必须 block",
                    expect="material_authority_invalid")

            # ---------- 6b. successor exact-set 的独立重算（M930-3B / P4）-------
            # 这一段直接调用读门的重算函数（白盒），理由如实说明：任何被改过的 Pack 其
            # `pack_id` 必然对不上，`resolve_pack_set` 会先报 `current_pack_fingerprint_mismatch`；
            # 那与本段要证的「重算是否真的独立」是两回事，混在一起会让断言失去区分度。
            # 反例一律在**真实提交的 current Pack** 上做单点变异。
            def _gate_reasons(pack, resolver=None) -> tuple[str, ...]:
                out: list[PSet.PackSetBlock] = []
                PSet._check_material_and_fact_authority(
                    pack, resolver if resolver is not None else session.payload_resolver, out)
                return tuple(b.reason for b in out)

            check(_gate_reasons(committed) == (),
                  f"真实 current Pack 必须零 block（实测 {_gate_reasons(committed)}）")

            if committed.material_dispositions:
                rmd = committed.material_dispositions[0]
                forged_rmd = dataclasses.replace(
                    rmd, container_identity="evidence_document:ghost@sha256-ghost")
                mutated = dataclasses.replace(
                    committed, material_dispositions=(forged_rmd,)
                    + committed.material_dispositions[1:])
                check("material_disposition_mismatch" in _gate_reasons(mutated),
                      "RMD 的 container 身份自报（与 material 重算不符）必须 block")

            if committed.fact_qualification_decisions:
                decision = committed.fact_qualification_decisions[0]
                forged_inputs = dataclasses.replace(decision, input_material_ids=("m-ghost",))
                check("candidate_decision_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, fact_qualification_decisions=(forged_inputs,)
                    + committed.fact_qualification_decisions[1:])),
                    "决定自报的输入 material 集合与 candidate 重算不符必须 block")

                forged_locator = dataclasses.replace(
                    decision, input_locator_digest=_sha("forged-locator"))
                check("decision_input_identity_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, fact_qualification_decisions=(forged_locator,)
                    + committed.fact_qualification_decisions[1:])),
                    "决定自报的 locator digest 与输入 material 重算不符必须 block")

                forged_identity = dataclasses.replace(
                    decision, input_identity_digest=_sha("forged-identity"))
                check("decision_input_identity_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, fact_qualification_decisions=(forged_identity,)
                    + committed.fact_qualification_decisions[1:])),
                    "决定自报的 input identity digest 无法由候选+输入材料重算必须 block")

                forged_source = dataclasses.replace(
                    decision, input_source_identity="evidence:ghost")
                check("decision_input_identity_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, fact_qualification_decisions=(forged_source,)
                    + committed.fact_qualification_decisions[1:])),
                    "eligible 决定的来源身份与输入 material 来源不一致必须 block")

            # ---------- 6c. formal ExternalFact 闭合（合成 external 链）--------
            # 真实链当前**不产出** external 事实（见 §八 报告），故这里用唯一工厂合成一条
            # 良构 external 链挂到真实 Pack 上，先证「良构必须零 block」，再逐项单点破坏。
            ext_statement = "外部来源命题（eval 合成，不进任何正式产物）"
            ext_cand, ext_dec = EXT._ext_chain(ext_statement)
            ext_fact = EXT._external_fact(ext_statement, candidate=ext_cand, decision=ext_dec)
            ext_fact = dataclasses.replace(
                ext_fact, source_policy_version=committed.source_policy_version,
                as_of_date=committed.report_as_of or "2025-01-01")
            ext_resolver = _ExtResolver(ext_fact.payload_ref)
            augmented = dataclasses.replace(
                committed,
                fact_candidates=committed.fact_candidates + (ext_cand,),
                fact_qualification_decisions=(
                    committed.fact_qualification_decisions + (ext_dec,)),
                external_facts=committed.external_facts + (ext_fact,))
            check(_gate_reasons(augmented, ext_resolver) == (),
                  f"良构 formal ExternalFact 必须零 block（实测 "
                  f"{_gate_reasons(augmented, ext_resolver)}）")

            def _augmented(**changes):
                return dataclasses.replace(
                    augmented, external_facts=committed.external_facts + (
                        dataclasses.replace(ext_fact, **changes),))

            check("external_fact_closure_mismatch" in _gate_reasons(
                _augmented(external_fact_id="ef-forged"), ext_resolver),
                "external_fact_id 与（snapshot+命题+locator）重算不符必须 block")
            check("external_fact_closure_mismatch" in _gate_reasons(
                _augmented(source_policy_version="source-policy-drifted"), ext_resolver),
                "ExternalFact 的 SourcePolicy 版本与 Pack 不一致必须 block")
            check("external_fact_closure_mismatch" in _gate_reasons(
                _augmented(as_of_date="2099-12-31"), ext_resolver),
                "ExternalFact 的 as_of_date 晚于报告基准日必须 block")
            check("external_fact_closure_mismatch" in _gate_reasons(
                _augmented(as_of_date="not-a-date"), ext_resolver),
                "ExternalFact 的 as_of_date 不是可比较日期必须 block")
            forged_authority = dataclasses.replace(
                ext_fact.source_authority, source_grade="D", min_grade_met=False)
            check("external_fact_closure_mismatch" in _gate_reasons(
                _augmented(source_authority=forged_authority), ext_resolver),
                "ExternalFact 的来源权威重算不合格（自报 authoritative）必须 block")
            check("payload_unresolvable" in _gate_reasons(augmented, session.payload_resolver),
                  "ExternalFact 的 payload_ref 在真实 resolver 下 dangling 必须 block")

            # ---------- 6d. gap / block 的 Contract basis 重算 ----------------
            if committed.contract_gaps:
                gap = committed.contract_gaps[0]
                check("gap_basis_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, contract_gaps=(
                        dataclasses.replace(gap, requirement_fingerprint=_sha("forged-basis")),
                    ) + committed.contract_gaps[1:])),
                    "gap 的 requirement fingerprint 与 aspect 冻结投影重算不符必须 block")
                check("gap_basis_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, contract_gaps=(
                        dataclasses.replace(gap, contract_version="v1"),)
                    + committed.contract_gaps[1:])),
                    "gap 绑定非 current Contract 版本必须 block")
                check("gap_basis_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, contract_gaps=(
                        dataclasses.replace(gap, required_unmet_proof="   "),)
                    + committed.contract_gaps[1:])),
                    "gap 缺少非空 required-unmet 证明必须 block")
                check("gap_basis_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, contract_gaps=(
                        dataclasses.replace(gap, rejected_candidate_ids=("c-ghost",)),)
                    + committed.contract_gaps[1:])),
                    "gap 回指不存在的 rejected candidate 必须 block")
                ghost_gap = dataclasses.replace(
                    gap, aspect_id="a-ghost",
                    gap_id=TS.derive_contract_gap_id(
                        gap.topic_id, "a-ghost", gap.reason_code, gap.unmet_required_items))
                check("gap_basis_mismatch" in _gate_reasons(dataclasses.replace(
                    committed, contract_gaps=(ghost_gap,) + committed.contract_gaps[1:])),
                    "gap 指向本 Pack 之外的 aspect 必须 block")

            # ---------- 6e. 构造期已拒 → 读门是第二道防线（如实登记）----------
            # 「缺/多/重/跨 kind/双结果/孤儿」这些形状在 `TopicResearchPack.__post_init__` 就
            # 被 `verify_pack_successor_sets` 拒掉，连 Pack 对象都造不出来，因此**不可能**经
            # Store 走到读门。读门里对应的分支是第二道防线，本测试不假装它们可达。
            if committed.material_dispositions:
                expect_error(lambda: dataclasses.replace(
                    committed, material_dispositions=committed.material_dispositions[1:]),
                    TS.SchemaValidationError, "构造期：material 缺 RMD 即拒（读门不可达）")
            if committed.fact_qualification_decisions:
                expect_error(lambda: dataclasses.replace(
                    committed, fact_qualification_decisions=(
                        committed.fact_qualification_decisions
                        + (committed.fact_qualification_decisions[0],))),
                    TS.SchemaValidationError, "构造期：candidate 双决定即拒（读门不可达）")
            expect_error(lambda: dataclasses.replace(
                augmented, external_facts=committed.external_facts + (
                    dataclasses.replace(ext_fact, candidate_id=(
                        committed.fact_candidates[0].candidate_id if committed.fact_candidates
                        else "c-ghost")),)),
                TS.SchemaValidationError, "构造期：跨 kind 的 qualified result 即拒（读门不可达）")
            expect_error(lambda: dataclasses.replace(
                ext_fact, canonical_url="https://ghost.example/9"),
                TS.SchemaValidationError,
                "构造期：ExternalFact 的 locator/canonical_url 不闭合即拒（读门不可达）")
            expect_error(lambda: dataclasses.replace(
                ext_fact, body_hash=_sha("forged-body")),
                TS.SchemaValidationError,
                "构造期：ExternalFact 的 body hash 三方不闭合即拒（读门不可达）")

        # ================= 6f. §三 F：follow-up → **完整后继** Pack =========
        # 这一段用**自己的临时库**（不碰上面三条 current，也不受 §7 的失效标记影响），
        # 真 requirement + 真树工具 + 唯一 runtime + 真 Store，经**唯一**的 follow-up 入口
        # `run_follow_up_needs` 证明四件事：
        #   (1) 通过的 follow-up 产出的是**完整** aspect/question 集的后继 Pack（同 identity、
        #       同依赖指纹），并成为该 identity 新的 current；
        #   (2) 旧 Pack **未被回写**（旧 pack_id 仍原样可读、未被标记失效）；
        #   (3) 后继能通过 P4 读门 —— 这正是修复前被 `aspect_set_mismatch` /
        #       `question_set_mismatch` 卡死的那一步；
        #   (4) 两条 typed 拒绝码在真库上真的会触发（identity 无 current / current 不完整），
        #       且拒绝时**不产生任何新 Pack**（不退化成「只提交被重研的那一条 aspect」）。
        db2 = Path(td) / "successor.db"
        store2 = TR.SqliteTopicStoreAdapter(db2)
        store2.init()
        reader2 = PSet.SqlitePackSetStore(path=db2, resolver=session.payload_resolver)

        def _in_topic_db(db_path: Path, fn):
            """把模块级 `topic_store` 函数的库路径**显式**绑到 `db_path` 再调用。

            这些函数按「最近一次 init 的路径」工作，而本段要同时看几个临时库：不显式绑定就会
            读到别的库（那样「旧 Pack 未被回写」之类的断言等于看错了对象）。
            """
            Store.init_topic_store(db_path)
            return fn()

        base_results = {}
        for offset, requirement in enumerate(company_reqs):
            base_results[requirement.topic_id] = _run(
                requirement, run_id=f"m930-3-successor-base-{offset}", active_store=store2)
        pre = PSet.resolve_pack_set(company_task, company_reqs, reader2)
        check(tuple(p.pack_id for p in pre.packs)
              == tuple(base_results[t].pack_id for t in company_task.topic_ids),
              "后继实验的前置：三条真 topic 的 current 都是本轮刚提交的 base Pack")

        # 重研目标选「base 里真有材料」的那条 aspect：只有它能让本轮真的再走一次树工具，
        # 后继的「替换该 aspect 的行」才不是空转。选择规则是**数据驱动的**（扫 base Pack 里
        # 引用非空 material_ids 的第一条 aspect），不写死任何公司/页码/aspect 名字。
        target_req, target_aspect, base_pack = company_reqs[0], None, None
        for requirement in company_reqs:
            pack = _in_topic_db(
                db2, lambda: Store.get_pack(base_results[requirement.topic_id].pack_id))
            hit = next((r for r in pack.aspect_results if r.material_ids), None)
            if hit is not None:
                target_req, base_pack = requirement, pack
                target_aspect = next(a for a in requirement.aspects
                                     if a.aspect_id == hit.aspect_id)
                break
        check(base_pack is not None,
              "后继实验的前置：base 里存在一条引用真实材料的 aspect"
              "（只有它能让本轮真的再走一次树工具，替换行才不是空转）")
        if base_pack is None:  # 真样本退化时如实 FAIL（不静默放宽），并仍以首条 aspect 继续
            target_aspect = target_req.aspects[0]
            base_pack = _in_topic_db(
                db2, lambda: Store.get_pack(base_results[target_req.topic_id].pack_id))
        check(base_pack is not None and base_pack.identity()
              == TR.requirement_pack_identity(target_req),
              "后继实验的前置：base Pack 可从真库读回且身份正确")
        frozen_identity = TR.requirement_pack_identity(target_req)
        target_requirement_id = TR.topic_requirement_id(target_req)

        def _follow_up_need(requirement, aspect, *, statement: str, revision: str = "rev-1",
                            authorized=None, source_class=None):
            """由**冻结 requirement/aspect** 造 need；need_id 走唯一派生口径（不自造）。"""
            granted_revision = revision
            need_id = TS.derive_follow_up_need_id(
                statement, TR.topic_requirement_id(requirement), aspect.aspect_id,
                requirement.section_id, granted_revision)
            return TS.FollowUpNeed(
                need_id=need_id, need_schema_version=TS.FOLLOW_UP_NEED_SCHEMA_VERSION,
                statement=statement, target_requirement_id=TR.topic_requirement_id(requirement),
                topic_id=requirement.topic_id, question_id=aspect.question_id,
                aspect_id=aspect.aspect_id, section_id=requirement.section_id,
                section_draft_revision=granted_revision,
                # 授权范围取 runtime 的**同一个**确定性派生口径（private helper 在测试侧复用，
                # 不在这里另写一套「授权」算法）。
                contract_authorized_scope=(TR._authorized_scope(aspect)
                                           if authorized is None else authorized),
                requiredness="required",
                expected_source_class=(TS.SOURCE_CLASSES[0] if source_class is None
                                       else source_class),
                budget_hint="tree_inspect:1",
                writer_identity="writer-m930-3-successor")

        granted_need = _follow_up_need(
            target_req, target_aspect,
            statement="现有材料不足以覆盖该 aspect 的当前期间要求，需在同一授权范围内补取")
        target_research = _SpanRecallResearch(
            navigation=_navigation_for(target_req), requirement=target_req,
            document=document, tree_max_spans=budget.tree_max_spans_per_aspect,
            tree_max_chars=budget.tree_max_chars_per_span)
        follow_ctx, follow_deps, _research = _deps_for(
            target_req, run_id="m930-3-successor-followup", active_store=store2,
            research=target_research)
        follow = TR.run_follow_up_needs(
            follow_up_needs=(granted_need,),
            requirements_by_id={
                TR.topic_requirement_id(r): r for r in (*company_reqs, *financial_reqs)},
            run_context=follow_ctx, dependencies=follow_deps)
        decision = follow.decisions[0]
        check(follow.rules_version == "fud-2"
              and TR.FOLLOW_UP_RULES_VERSION == "fud-2",
              f"follow-up 裁决版本必须是后继语义的 fud-2（得到 {follow.rules_version!r}）")
        check(decision.executed is True and decision.verdict in ("accepted", "reduced")
              and follow.new_pack_ids == (decision.new_pack_id,),
              f"通过的 follow-up 必须真的执行并登记新 Pack（verdict={decision.verdict!r} "
              f"executed={decision.executed} new={follow.new_pack_ids}）")
        check(TR.PACK_SUCCESSOR_ASSEMBLY_VERSION in (decision.reason or ""),
              f"裁决理由必须记录后继装配版本 {TR.PACK_SUCCESSOR_ASSEMBLY_VERSION!r}"
              f"（得到 {decision.reason!r}）")

        successor_id = decision.new_pack_id
        successor = (_in_topic_db(db2, lambda: Store.get_pack(successor_id))
                     if successor_id else None)
        check(successor is not None and successor_id != base_pack.pack_id,
              "后继是**新** Pack 且可按 pack_id 从真库读回")
        if successor is not None and base_pack is not None:
            check(successor.identity() == frozen_identity == base_pack.identity(),
                  "后继与 base 同 identity（同一 requirement 的 current 才可能被读门认）")
            check(set(r.aspect_id for r in successor.aspect_results)
                  == set(target_req.aspect_ids())
                  and len(successor.aspect_results) == len(target_req.aspect_ids()),
                  f"后继 aspect 集**完整**（实测 {len(successor.aspect_results)} vs 冻结 "
                  f"{len(target_req.aspect_ids())}）——不得只提交被重研的那一条")
            check(set(successor.question_ids) == set(target_req.question_ids),
                  "后继 question 集完整（与冻结 requirement 精确等集）")
            check(successor.dependency_fingerprint == target_req.dependency_fingerprint(),
                  "后继依赖指纹 = 冻结 requirement 重算值（不继承过期指纹）")
            check(successor.run_id == "m930-3-successor-followup",
                  "后继 run_id 指向**本轮**运行（继承来的行不改变这一点）")

            # 确定性继承：未被重研的 aspect **逐字段**继承；被重研的那条由本轮重新产出。
            base_by = {r.aspect_id: r for r in base_pack.aspect_results}
            succ_by = {r.aspect_id: r for r in successor.aspect_results}
            inherited = sorted(set(base_by) - {target_aspect.aspect_id})
            check(all(succ_by[a].to_dict() == base_by[a].to_dict() for a in inherited),
                  f"未被重研的 {len(inherited)} 条 aspect 结果逐字段继承（不重跑、不改写）")
            check(all(succ_by[a].requirement_snapshot.to_dict()
                      == base_by[a].requirement_snapshot.to_dict() for a in inherited),
                  "继承 aspect 的冻结投影逐字段等于 base（不偏离 Contract）")
            check(succ_by[target_aspect.aspect_id].requirement_snapshot.to_dict()
                  == base_by[target_aspect.aspect_id].requirement_snapshot.to_dict(),
                  "被重研 aspect 的冻结投影同样不得偏离 Contract")
            # 重研真的发生了：树工具被调用过，且引用闭合（被重研 aspect 引用的材料都在后继里）
            check(len(target_research.calls) >= 1
                  and all(c["aspect_id"] == target_aspect.aspect_id
                          for c in target_research.calls),
                  f"重研只跑被点名的那一条 aspect（实测 {target_research.calls}）")
            material_ids = {m.material_id for m in successor.materials}
            check(set(succ_by[target_aspect.aspect_id].material_ids) <= material_ids,
                  "后继里被重研 aspect 引用的材料都真的在 Pack 内（引用闭合）")
            check(tuple(r.unresolved_id for r in successor.unresolved)
                  and set(succ_by[target_aspect.aspect_id].unresolved_ids)
                  <= {g.unresolved_id for g in successor.unresolved},
                  "后继里被重研 aspect 引用的 gap 都在 Pack 内（引用闭合）")

            # §三 F item 1：statement / 目标 aspect / 授权理由真的进入了**被使用**的
            # `InformationNeed`（不是只写在裁决记录里）。
            check(len(target_research.needs) >= 1,
                  "重研真的把 InformationNeed 交给了 research_question")
            used_need = target_research.needs[-1]
            focus = dict((used_need.metadata or {}).get(
                TR.FOLLOW_UP_FOCUS_METADATA_KEY) or {})
            check(focus.get("need_id") == granted_need.need_id
                  and focus.get("statement") == granted_need.statement
                  and focus.get("aspect_id") == target_aspect.aspect_id
                  and focus.get("contract_authorized_scope")
                  == list(granted_need.contract_authorized_scope)
                  and focus.get("expected_source_class") == granted_need.expected_source_class
                  and focus.get("rules_version") == follow.rules_version
                  and bool(focus.get("grant_reason")),
                  f"need 的 statement/目标 aspect/授权范围/来源类/裁决版本/理由都进入了被使用的 "
                  f"InformationNeed（实测 keys={sorted(focus)}）")
            check(all(dict((n.metadata or {}).get(TR.FOLLOW_UP_FOCUS_METADATA_KEY) or {})
                      .get("need_id") == granted_need.need_id
                      and dict((n.metadata or {}).get(TR.FOLLOW_UP_FOCUS_METADATA_KEY) or {})
                      .get("bound_need_id") == n.need_id
                      and (n.metadata or {}).get("aspect_id") == target_aspect.aspect_id
                      for n in target_research.needs),
                  "每一轮被使用的 need 都带同一诉求，且 focus 回指各自的 need 身份、"
                  "原有 metadata 未被 focus 覆盖")
            check(tuple(sorted(focus)) == tuple(sorted(
                      (*TR.FOLLOW_UP_FOCUS_FIELDS, "contract_authorized_scope",
                         "bound_need_id"))),
                  f"focus 字段集恰为冻结清单 + 授权范围 + bound_need_id（得到 {tuple(sorted(focus))}）")
            bound_events = [e for e in sink.events if e["type"] == "FOLLOW_UP_FOCUS_BOUND"]
            check(any(e["payload"].get("need_id") == granted_need.need_id
                      for e in bound_events),
                  "FOLLOW_UP_FOCUS_BOUND 事件如实登记在 trace 上")

            # 旧 Pack 未被回写 + 旧 Draft 未被回写（need 只**回指** revision，不当输入）
            # `base_pack` 是 follow-up **之前**的独立读回，这里是之后：两次逐字段相同即
            # 「后继提交没有回写旧行」。pack_id 是内容寻址的，任何原地改写都会改掉它。
            read_back = _in_topic_db(db2, lambda: Store.get_pack(base_pack.pack_id))
            check(read_back is not None
                  and read_back.content_fingerprint() == base_pack.content_fingerprint()
                  and read_back.pack_id == base_pack.pack_id
                  and read_back.to_dict() == base_pack.to_dict(),
                  "旧 Pack 在 follow-up 前后两次独立读回逐字段相同（未被回写）")
            check(all(e.event_type != "invalidated"
                      for e in _in_topic_db(db2, lambda: Store.list_events(
                          base_pack.pack_id))),
                  "旧 Pack 未被标记失效（后继只是新 current，不是把旧行改写）")
            history = sorted(_in_topic_db(db2, lambda: Store.list_pack_history(
                frozen_identity)), key=lambda p: p.pack_id)
            check({p.pack_id for p in history} == {base_pack.pack_id, successor_id}
                  and len(history) == 2,
                  f"该 identity 的历史恰两行（base + 后继），没有第三个 current"
                  f"（实测 {[p.pack_id for p in history]}）")
            current_now = _in_topic_db(db2, lambda: Store.load_current_pack(frozen_identity))
            check(current_now.available and current_now.pack.pack_id == successor_id,
                  "后继成为该 identity 新的 current")
            logged_needs = _in_topic_db(
                db2, lambda: Store.load_follow_up_needs(follow_ctx.run_id))
            logged_decisions = _in_topic_db(
                db2, lambda: Store.load_follow_up_decisions(follow.follow_up_run_id))
            check(_in_topic_db(db2, lambda: Store.load_follow_up_run(
                      follow.follow_up_run_id)) is not None
                  and tuple(n.to_dict() for n in logged_needs)
                  == (granted_need.to_dict(),)
                  and tuple(d.decision_id for d in logged_decisions)
                  == (decision.decision_id,),
                  "边界③：need/decision 行只追加落库，且落库的 need 与输入逐字段相同"
                  "（revision 只作回指，不被写回任何 Draft）")

            # 读门接受后继 —— 修复前后继会被 `aspect_set_mismatch` 卡死
            post = PSet.resolve_pack_set(company_task, company_reqs, reader2)
            check(post.pack_for(target_req.topic_id).pack_id == successor_id,
                  "P4 读门接受后继作为该 topic 的 current Pack（exact-set 重新验证通过）")
            check(all(post.pack_for(r.topic_id).pack_id == base_results[r.topic_id].pack_id
                      for r in company_reqs if r.topic_id != target_req.topic_id),
                  "其余 topic 的 current 不被这次 follow-up 触动")
            details.append("NOTE §三 F 后继实测: " + json.dumps({
                "topic": target_req.topic_id, "aspect": target_aspect.aspect_id,
                "verdict": decision.verdict,
                "aspects_in_successor": len(successor.aspect_results),
                "aspects_inherited": len(inherited),
                "tree_calls_for_researched": len(target_research.calls),
                "materials": len(successor.materials),
                "facts": len(successor.facts),
                "gaps": len(successor.unresolved),
                "base_pack": base_pack.pack_id[:12],
                "successor_pack": successor_id[:12],
            }, ensure_ascii=False))

        # 两次独立执行必须在 trace 上**可区分**（M930-3.2 停止报告里的 trace 折叠缺陷）：同一
        # topic / aspect 再发一条**新**诉求（revision=rev-2），它的 refs 必须指向**它自己**的
        # `follow_up_run_id`，且与上一轮的 refs 不相交。修复前两次执行都会得到
        # `<run_id>:FOLLOW_UP_EXECUTED:<本调用内序号>`（两次都是 `…:0`），产物里分不出「1 次」
        # 还是「2 次」，也就谈不上「各自指向真实 run / 需求 / 后继 Pack」。
        second_need = _follow_up_need(
            target_req, target_aspect,
            statement="继上一轮补取之后，仍需在该 aspect 的授权范围内补充后续材料",
            revision="rev-2")
        second_research = _SpanRecallResearch(
            navigation=_navigation_for(target_req), requirement=target_req,
            document=document, tree_max_spans=budget.tree_max_spans_per_aspect,
            tree_max_chars=budget.tree_max_chars_per_span)
        second_ctx, second_deps, _second_r = _deps_for(
            target_req, run_id="m930-3-successor-followup-2", active_store=store2,
            research=second_research)
        second = TR.run_follow_up_needs(
            follow_up_needs=(second_need,),
            requirements_by_id={
                TR.topic_requirement_id(r): r for r in (*company_reqs, *financial_reqs)},
            run_context=second_ctx, dependencies=second_deps)
        first_refs = tuple(str(t) for t in follow.trace_refs)
        second_refs = tuple(str(t) for t in second.trace_refs)
        check(second.follow_up_run_id != follow.follow_up_run_id,
              "同一 task 上的两次执行必须是两个不同的 follow_up_run_id（操作身份由 run id + "
              f"need 集派生）：{follow.follow_up_run_id} vs {second.follow_up_run_id}")
        check(second.decisions[0].executed is True and bool(second_refs),
              f"第二次执行也必须真的跑完（verdict={second.decisions[0].verdict!r} "
              f"refs={second_refs}）——否则本段的「可区分」没有对象")
        check(bool(first_refs) and all(follow.follow_up_run_id in r for r in first_refs)
              and all(second.follow_up_run_id in r for r in second_refs),
              "每次执行的 refs 必须指向**它自己**的 follow_up_run_id："
              f"run1={follow.follow_up_run_id} refs1={first_refs} / "
              f"run2={second.follow_up_run_id} refs2={second_refs}")
        check(not (set(first_refs) & set(second_refs)),
              "两次执行的 trace refs 不得折叠成同一串（旧缺陷：两次都是 "
              f"`…:FOLLOW_UP_EXECUTED:0`）：交集={sorted(set(first_refs) & set(second_refs))}")
        check(_in_topic_db(db2, lambda: Store.load_follow_up_run(
                  second.follow_up_run_id)) is not None
              and bool(_in_topic_db(db2, lambda: Store.load_follow_up_decisions(
                  second.follow_up_run_id))),
              "第二次执行的 run / decision 行真的落库（refs 指向的行确实存在）")
        check(_in_topic_db(db2, lambda: Store.load_follow_up_run(
                  follow.follow_up_run_id)) is not None,
              "第一次执行的 run 行也仍在（两次执行都保留，不互相覆盖）")

        # 反例 A：identity 在库里没有 current Pack ⇒ typed 拒绝，且不产出新 Pack。
        missing_req = financial_reqs[0]
        missing_need = _follow_up_need(
            missing_req, missing_req.aspects[0],
            statement="该 identity 尚无 current Pack，请求补取（不得据此只提交单条 aspect）")
        missing_ctx, missing_deps, _r = _deps_for(
            missing_req, run_id="m930-3-successor-missing", active_store=store2)
        missing_run = TR.run_follow_up_needs(
            follow_up_needs=(missing_need,),
            requirements_by_id={
                TR.topic_requirement_id(r): r for r in (*company_reqs, *financial_reqs)},
            run_context=missing_ctx, dependencies=missing_deps)
        check(missing_run.decisions[0].reason == "successor_base_missing"
              and missing_run.decisions[0].executed is False
              and missing_run.new_pack_ids == ()
              and missing_run.decisions[0].verdict == "rejected",
              f"没有可继承的完整 current Pack 时必须 typed 拒绝 successor_base_missing"
              f"（得到 {missing_run.decisions[0].reason!r} new={missing_run.new_pack_ids}）")
        check(_in_topic_db(db2, lambda: Store.load_current_pack(
                  TR.requirement_pack_identity(missing_req))).available is False,
              "拒绝后该 identity 在库里仍然没有 current（不留下半份权威）")

        # 反例 B：current Pack 存在但**不完整**（aspect 集与冻结 requirement 不等）⇒ typed 拒绝。
        # 用独立临时库先提交一份「被收窄到单条 aspect」的 Pack（这正是修复前的错误形态），
        # 再用**完整** requirement 发起 follow-up。
        db3 = Path(td) / "successor_incomplete.db"
        store3 = TR.SqliteTopicStoreAdapter(db3)
        store3.init()
        narrow_req = dataclasses.replace(
            target_req, aspects=(target_aspect,),
            question_ids=(target_aspect.question_id,))
        _run(narrow_req, run_id="m930-3-successor-narrow-base", active_store=store3)
        narrow_current = _in_topic_db(db3, lambda: Store.load_current_pack(frozen_identity))
        check(narrow_current.available
              and len(narrow_current.pack.aspect_results) == 1
              and narrow_current.pack.dependency_fingerprint
              == target_req.dependency_fingerprint(),
              "反例 B 的前提：库里确有一份 aspect 集不完整、但身份/依赖指纹相同的 current")
        incomplete_need = _follow_up_need(
            target_req, target_aspect,
            statement="请求在既有 current 上补取（该 current 自身不完整）")
        inc_ctx, inc_deps, _r = _deps_for(
            target_req, run_id="m930-3-successor-incomplete", active_store=store3)
        incomplete_run = TR.run_follow_up_needs(
            follow_up_needs=(incomplete_need,),
            requirements_by_id={
                TR.topic_requirement_id(r): r for r in (*company_reqs, *financial_reqs)},
            run_context=inc_ctx, dependencies=inc_deps)
        check(incomplete_run.decisions[0].reason == "successor_base_incomplete"
              and incomplete_run.decisions[0].executed is False
              and incomplete_run.new_pack_ids == (),
              f"继承来源不完整时必须 typed 拒绝 successor_base_incomplete"
              f"（得到 {incomplete_run.decisions[0].reason!r}）")
        check(len(_in_topic_db(db3, lambda: Store.list_pack_history(frozen_identity))) == 1,
              "拒绝后库里仍只有那一份不完整 current（没有用后继掩盖不完整）")

        # 反例 C：授权范围越界 ⇒ typed 拒绝，且**不**触发任何研究（真库上零新行）。
        out_of_scope_need = _follow_up_need(
            target_req, target_aspect, statement="请求取该 aspect 授权范围之外的材料",
            revision="rev-2",
            authorized=tuple(sorted({*TR._authorized_scope(target_aspect), "scope_ghost"})))
        store2.init()  # 反例 B 把库路径绑到了 db3；这里显式绑回 db2（runtime 内部按库路径工作）
        scope_ctx, scope_deps, _r = _deps_for(
            target_req, run_id="m930-3-successor-scope", active_store=store2)
        scope_run = TR.run_follow_up_needs(
            follow_up_needs=(out_of_scope_need,),
            requirements_by_id={TR.topic_requirement_id(r): r for r in company_reqs},
            run_context=scope_ctx, dependencies=scope_deps)
        check(scope_run.decisions[0].reason == "scope_not_authorized"
              and scope_run.new_pack_ids == ()
              and scope_run.decisions[0].executed is False,
              f"授权范围越界必须 typed 拒绝 scope_not_authorized（得到 "
              f"{scope_run.decisions[0].reason!r}）")
        check(not [e for e in sink.events if e["type"] == "FOLLOW_UP_EXECUTED"
                   and e["payload"].get("need_id") == out_of_scope_need.need_id],
              "越界请求不得触发任何研究执行（不产生新包）")
        check(not [n for n in target_research.needs
                   if dict((n.metadata or {}).get(TR.FOLLOW_UP_FOCUS_METADATA_KEY) or {})
                   .get("need_id") == out_of_scope_need.need_id],
              "越界请求的诉求不得进入任何 InformationNeed（拒绝≠偷偷检索）")
        check(_in_topic_db(db2, lambda: Store.load_current_pack(
                  frozen_identity)).pack.pack_id == successor_id,
              "越界请求不触动 current（仍是上一轮的后继）")

        # 本段用完自己的临时库，把库路径还原给主临时库（§7 仍按同一套模块级函数读它）。
        store.init()

        # ================= 7. current 被标记失效 ==========================
        # 放在最后：这一步会真实改写临时库里的 current 事件。
        Store.mark_invalidated(results[first.topic_id].pack_id, "stale", reason="eval")
        blocked(lambda: PSet.resolve_pack_set(company_task, company_reqs, reader),
                "current 被标记 stale 必须 block", expect="current_pack_invalidated")
        check(Store.load_current_pack(results[first.topic_id].identity).available is False,
              "失效 current 在 Store 读回时也不可用（不是读门一厢情愿）")

        # ================= 8. 适配器不得读别的库 ==========================
        expect_error(lambda: PSet.SqlitePackSetStore(
            path=Path(td) / "other.db", resolver=session.payload_resolver).list_current(),
            PSet.PackSetError, "适配器 path 与已绑定库不一致必须 fail-closed",
            needle="不一致")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


# ---------------------------------------------------------------------------
# 真实切片：研究替身（只读真实 span；MOCK 只在「不编造证据」这一条下被允许）
# ---------------------------------------------------------------------------

class _SpanRecallResearch:
    """经**正式树工具**读真实 span 并逐字引用；参数与 runtime 自己的树调用逐字段相同。"""

    def __init__(self, *, navigation, requirement, document, tree_max_spans,
                 tree_max_chars) -> None:
        self._navigation = navigation
        self._requirement = requirement
        self._document = document
        self._tree_max_spans = tree_max_spans
        self._tree_max_chars = tree_max_chars
        self._aspects = {a.aspect_id: a for a in requirement.aspects}
        self.calls: list[dict] = []
        #: 收到的 `InformationNeed` **原对象**（§三 F：follow-up 的 focus 必须真的落在被使用的
        #: need 上，所以这里留的是 runtime 实际交给 `research_question` 的那一个对象，不是复制品）。
        self.needs: list[RS.InformationNeed] = []

    def __call__(self, *, need, route_result, registry, llm, budget, run_id, case_id,
                 company_id, section_id, trace_enabled, context):
        self.needs.append(need)
        aspect = self._aspects[str((need.metadata or {}).get("aspect_id"))]
        decision = self._navigation.candidates(aspect, requirement=self._requirement)
        node_ids = self._navigation.node_ids_for(decision)
        claims, citations, verdicts = [], [], []
        tool_calls = 0
        stop_reason = STOP_NO_SEARCHABLE_SCOPE
        if node_ids:
            call = C.ToolCall(
                call_id=need.need_id + "--recall",
                tool_name=TT.TREE_INSPECT_TOOL_NAME,
                arguments={
                    **self._document.to_dict(), "need_id": need.need_id,
                    "aspect_id": aspect.aspect_id,
                    "topic_id": self._requirement.topic_id,
                    "node_ids": list(node_ids),
                    "max_spans": self._tree_max_spans,
                    "max_chars_per_span": self._tree_max_chars,
                },
                idempotency_key=need.need_id + "--recall",
                need_id=need.need_id, batch_id=self._requirement.topic_id)
            result = registry.execute(call, route="DIRECT_EVIDENCE", run_id=run_id)
            tool_calls = 1
            candidates = list((result.data or {}).get("candidates") or ())
            self.calls.append({"aspect_id": aspect.aspect_id, "status": result.status,
                               "candidates": len(candidates)})
            if candidates:
                counts = collections.Counter(
                    c["parent_evidence_id"] for c in candidates)
                pick = next((c for c in candidates
                             if counts[c["parent_evidence_id"]] == 1), candidates[0])
                citations.append(TS.CitationRef(
                    ref_type="evidence", evidence_id=pick["parent_evidence_id"]))
                claims.append(HS.Claim(claim_id="c1", text=pick["text"], kind="fact",
                                       citation_refs=[0]))
                verdicts.append(HS.EntailmentVerdict(
                    claim_id="c1", citation_ids=["0"], verdict="SUPPORTED",
                    reason="claim 文本逐字取自该 span 的真实文本"))
                stop_reason = "COMPLETED"
            else:
                stop_reason = STOP_NOT_FOUND
        answer = HS.ResearchAnswer(
            question_id=need.need_id,
            answer_text=(claims[0].text if claims else ""),
            claims=claims, citations=citations,
            completion_status=("COMPLETED" if claims else "UNRESOLVED"))
        state = HS.ResearchState(
            run_id=run_id, case_id=case_id, question_id=need.need_id,
            company_id=company_id, section_id=section_id,
            original_question=need.question, need=need,
            status=("COMPLETED" if claims else "BLOCKED"),
            entailment_verdicts=verdicts,
            usage=HS.UsageLedger(rounds=1, tool_calls=tool_calls,
                                 local_searches=tool_calls, llm_calls=1,
                                 input_tokens=120, output_tokens=40, elapsed_ms=8))
        return HS.ResearchOutcome(
            state=state, answer=answer, success=bool(claims),
            completion_status=answer.completion_status, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event_type: str, payload: dict) -> None:
        self.events.append({"type": event_type, "payload": payload})


class _NeedBuilder:
    def build(self, aspect, *, need_id, company_id, section_id, report_as_of):
        return RS.InformationNeed(
            need_id=need_id, section_id=section_id, question=aspect.requirement_text,
            required_evidence_types=[], required_source_types=[], time_scope=None,
            priority="primary", depends_on=[],
            metadata={"aspect_id": aspect.aspect_id, "topic_id": aspect.topic_id})


class _NullResolver:
    """永远解析不出 payload 的 resolver（用于证明 dangling 会被 block）。"""

    def resolve(self, payload_ref):
        return None


class _ExtResolver:
    """只解析夹具里那一份 external_snapshot payload（其余一律 dangling，不假装能解析）。"""

    def __init__(self, payload_ref) -> None:
        self._ref = payload_ref

    def resolve(self, payload_ref):
        if payload_ref.to_dict() != self._ref.to_dict():
            return None
        return TS.ResolvedPayload(
            object_type=payload_ref.object_type,
            authority_identity=payload_ref.authority_identity,
            version=payload_ref.version, locator=payload_ref.locator,
            content_hash=payload_ref.content_hash, payload_bytes=None)


class _FakeStore:
    """符合 `PackSetStore` 协议的替身：按调用方 identity **原样**返回给定的 Pack。

    模拟「库里的行被改过」这一读门必须处理的情形；不提供任何写入口。
    """

    def __init__(self, pack, resolver, *, foreign=None) -> None:
        self._pack = pack
        self._resolver = resolver
        self._foreign = foreign

    @property
    def resolver(self):
        return self._resolver

    def load_current(self, identity: TS.PackIdentity) -> PSet.PackSetCurrentLoad:
        return PSet.PackSetCurrentLoad(pack=self._pack)

    def list_current(self) -> tuple[TS.TopicResearchPack, ...]:
        return (self._foreign,) if self._foreign is not None else ()


def _route_context(company_id: str) -> RS.RouteContext:
    return RS.RouteContext(
        company_id=company_id, report_as_of=None, available_document_ids=[],
        available_source_types=[], supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[], external_research_enabled=False)


def _route_fn(need, context):
    budget = RS.RetrievalBudget(candidate_k_sparse=4, candidate_k_dense=4,
                                fusion_k=1, context_k=1, timeout_ms=1000)
    decision = RS.RouteDecision(
        need_id=need.need_id, route="DIRECT_EVIDENCE", reason_code="eval",
        filters={}, budget=budget, fallback_routes=[], decided_by="rule",
        rule_version="eval", confidence="high")
    return RS.RouterResult(status="DECIDED", decision=decision, error_code=None,
                           trace_id="eval")


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

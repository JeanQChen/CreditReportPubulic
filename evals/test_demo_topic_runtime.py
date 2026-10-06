"""Eval: M930-2 唯一 topic 研究运行时（`harness.topic_runtime`）+ 真实 M930-1 权威输入的纵向切片。

用法: python -m evals.test_demo_topic_runtime

一、真实代表性纵向切片（返修裁决 §十二）
   输入身份**只能**来自公开入口：`planning.demo_scope.load_demo_scope_profile()` →
   冻结 Contract v2 → `build_scope_input_manifest()` → `project_contract_v2_scope()` →
   `verify_demo_projection()`。公司 / 文档 / Evidence set 身份取自真实样本与只读 evidence DB，
   topic / question / aspect / SourcePolicyRef 全部来自冻结投影 —— 不手写 `t_*` / `a1` /
   `sp-eval` 之类冒充身份，也不挑「span 最多的节点」当导航键。

   研究替身（MOCK ResearchLLM，§十二 item 3 明确允许）只经**正式树工具**读真实 span：
   - claim 文本逐字取自被引用 span 的 `normalized_text`（不写固定答案）；
   - 引用父 Evidence ID 但**不带 locator**，于是「同一 Evidence 被切成多 span」时必然
     `citation_ambiguous_across_spans` 而不是任选一条；
   - 只有「已定位到可检索 scope 且工具真实返回 0 候选」才上报 `NOT_FOUND_AFTER_SEARCH`；
     未定位到 scope 的 aspect 一律保留 blocked（不得由「没查到」反推 not_found）。

二、运行时门（都用**真实 requirement**，只做单轴漂移注入）：§605 跨 run 幂等、运行账本门、
   身份/依赖门、装配门、组合 payload resolver 0/1/2 命中。

三、行业 section 与公司 section 走同一 runtime；§四 身份闭合的正例（行业用**自己真实的**
   导航 profile）与反例（行业复用公司 profile、同 ID 但不同 topic 的冒充条目）都在这里。

四、not_found 资格门（P1-B）：只调**生产**判定单元 `TR.derive_not_found_audit`，输入一律用
   **真实冻结 aspect** + **真实树工具结果**，逐条钉住「不得由任意一次尝试推出 not_found」：
   只查树材料而应有范围还含外部来源 → 不成立；`allowed_capabilities` 非空但没有真实调用 →
   不成立；有未尝试候选 → 不成立；预算耗尽 → 不成立；停止原因不对 → 不成立；冻结语义无从
   声明可比范围 → fail-closed。只有实际轨迹逐类覆盖应有范围、无未尝试候选、非预算耗尽时才
   允许 not_found（纯 external 范围的正反例只差"有没有真实外部用量"这一个事实）。
   真实切片的 not_found 数量**不作固定断言**，只核资格一致性并报告重算后的实际分布。

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
from document_structure import navigation as NAV
from document_structure import synopsis as SY
from harness import r2_dependencies as R2
from harness import schema as HS
from harness import source_manifest as SM
from harness import source_policy_resolver as SPR
from harness import topic_runtime as TR
from harness import topic_schema as TS
from harness import topic_store as Store
from harness import tree_tools as TT
from planning import demo_scope as SC
from planning import schema as PS
from routing import schema as RS
from sections import company_worker as CW
from sections import industry_worker as IW
from sections import pack_set as PSet
from tools import adapters as A
from tools import contracts as C

REPO = Path(__file__).resolve().parent.parent

#: 冻结 Contract / Profile 中本切片必须验证的 topic 身份（取自冻结投影，不是答案关键词）。
SLICE_COMPANY_TOPICS = ("company_identity", "company_business", "company_legal_risks")
SLICE_INDUSTRY_TOPIC = "industry_scale_cycle"

#: 原子停止原因取自 `harness.schema.STOP_REASONS`（登记值，不得自造）。
STOP_NOT_FOUND = "NOT_FOUND_AFTER_SEARCH"    # 已检索且检索范围内未取得支持材料
STOP_NO_SEARCHABLE_SCOPE = "NO_MORE_HIGH_VALUE_ACTION"  # 未定位到可检索 scope

#: §九 规定的 not_found 文案（不得写成「未披露 / 不存在」）。
NOT_FOUND_WORDING = "在本轮已纳入材料及检索范围内未取得"

#: 不得出现在任何 gap 文案里的「无证据断定不存在」措辞。
FORBIDDEN_ABSENCE_WORDING = ("不存在", "未披露", "没有披露", "未提及")

STARTED_AT = "2026-09-20T00:00:00Z"

#: 逐 aspect 终态封闭集合（必须与 `harness.topic_schema.ASPECT_RESULT_STATUSES` 同集）。
_STATUSES = ("covered", "partial", "not_found", "blocked", "not_applicable")


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

    def note(msg: str) -> None:
        """把**实测数字**写进报告（不作为断言，供停止报告逐项引用）。"""
        details.append(f"NOTE {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:140]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:140]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    pdf = _sample_pdf()
    evidence_db = REPO / "data/evidence.db"
    if pdf is None or not evidence_db.exists():
        skipped += 1
        details.append("SKIP 缺 live 样本或 data/evidence.db")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    check(STOP_NOT_FOUND in HS.STOP_REASONS
          and STOP_NO_SEARCHABLE_SCOPE in HS.STOP_REASONS,
          "本切片使用的原子停止原因都是 schema 登记值")

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

    # =================== A. M930-1 权威输入（公开入口） ======================
    profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
    policy_resolver = SPR.FrozenSourcePolicyResolver.from_asset(
        REPO / profile.source_policy_asset)
    contract = load_contract_v2(str(REPO / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_m930_2_real_slice_0001", company_id=company,
        company_name=company, credit_type="general", report_as_of="2026-06-30",
        contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_2_real_slice_0001",
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

    policy_identity = policy_resolver.identity()
    check(policy_identity["policy_id"] == projection.source_policy_id
          and policy_identity["policy_version"] == projection.source_policy_version
          and policy_identity["content_fingerprint"]
          == projection.source_policy_fingerprint,
          "生产 SourcePolicyResolver 从冻结资产独立解析出与投影逐项一致的政策身份"
          f"（resolver={policy_identity['resolver_version']}）")

    reqs = {r.topic_id: r for r in projection.requirements}
    tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
    check(set(SLICE_COMPANY_TOPICS) <= set(projection.selected_topic_ids),
          f"真实投影的 selected topics 含 {list(SLICE_COMPANY_TOPICS)}")
    check(set(SLICE_COMPANY_TOPICS) <= set(reqs)
          and SLICE_INDUSTRY_TOPIC in reqs,
          "真实投影给出了公司三 topic 与行业 topic 的 TopicResearchRequirement")
    company_task = tasks["company"]
    industry_task = tasks["industry"]
    check(set(SLICE_COMPANY_TOPICS) <= set(company_task.topic_ids)
          and company_task.research_policy in CW.SECTION_POLICIES
          and all(a.producer_kind == CW.PRODUCER_KIND
                  for t in SLICE_COMPANY_TOPICS for a in reqs[t].aspects),
          "两个（以上）selected 公司 topic 由同一个 topic_harness 生产者 SectionTask 承载")
    check(company_task.task_id == reqs[SLICE_COMPANY_TOPICS[0]].task_id
          and all(reqs[t].task_id == company_task.task_id
                  for t in SLICE_COMPANY_TOPICS),
          "公司 requirement 的 task_id 就是投影 SectionTask 的 task_id（非手写）")
    check(industry_task.topic_ids == (SLICE_INDUSTRY_TOPIC,)
          and reqs[SLICE_INDUSTRY_TOPIC].section_id == "industry",
          "行业 requirement 与行业 SectionTask 同身份")
    for topic in SLICE_COMPANY_TOPICS:
        req = reqs[topic]
        check(req.company_id == manifest.company_id
              and req.report_as_of == manifest.report_as_of
              and req.contract_fingerprint == projection.contract_fingerprint
              and req.source_policy_version == projection.source_policy_version
              and req.dependency_versions
              == TS.build_current_dependency_versions(
                  contract_version=req.contract_version,
                  source_policy_version=req.source_policy_version),
              f"{topic}: requirement 的公司/期间/Contract/政策/依赖集全部来自冻结投影")
        check(all(a.source_policy_ref.content_fingerprint
                  == projection.source_policy_fingerprint for a in req.aspects),
              f"{topic}: 每条 aspect 的 SourcePolicyRef 就是冻结投影的那一份")

    budget = TR.ResearchBudgetPolicy(
        policy_id=profile.budget_policy_id, version=profile.budget_policy_version,
        tier="demo_backbone",
        # 逐 aspect 至多 2 轮 focused follow-up（第 2 轮只为「有新证据但未闭环」留一次），
        # 不靠放大预算换「完整」。
        max_need_rounds_per_aspect=2, max_need_rounds_per_topic=2,
        max_tool_calls_per_topic=3 * len(reqs[SLICE_COMPANY_TOPICS[1]].aspects),
        max_tokens_per_topic=200000, max_llm_calls_per_topic=3 * len(
            reqs[SLICE_COMPANY_TOPICS[1]].aspects),
        max_elapsed_ms_per_topic=900000,
        tree_max_spans_per_aspect=6, tree_max_chars_per_span=4000)
    other_budget = dataclasses.replace(budget, version=budget.version + "-other")

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

        document = TR.DocumentIdentity(**session.document_identity())
        check(document.company_id == projection.company_id,
              "运行文档身份的公司 = 投影公司（同一 run-bound verified 源）")
        check(document.evidence_set_version == set_version,
              "运行文档身份绑定的就是 Evidence 库里该文档的 current set")

        db = Path(td) / "backbone.db"
        store = TR.SqliteTopicStoreAdapter(db)
        store.init()
        _, _sp, scv, sev = R2.build_r2_material_dependencies(db)
        reader = PSet.SqlitePackSetStore(path=db, resolver=session.payload_resolver)
        sink = _Sink()

        def _navigation_for(requirement):
            """导航 profile 只由**该 requirement 自己的真实 aspect**派生（§四/§十二）。

            `ancestor_labels` / `sibling_keys` 必须与生产侧同源地传进来：生产 runner 用
            `NAV.contract_ancestor_inputs(contract)`（见 `run_m930_3_acceptance._deps`），
            `anp-6` 的读根判据**依赖 `parent_keys`**（父主题标题层）才能落到真实读根
            （如 `company_business_model.*` → `3经营模式`），并依赖兄弟项排除才不把兄弟
            栏目的章节读成自己的材料。不传 ⇒ `parent_keys=[]`，该层读根整片消失，本测试会
            假装"真实切片里没有任何纯 company_industry 且有候选的 aspect"——那是**夹具
            失真**，不是产物读数。
            """
            _labels, _siblings = NAV.contract_ancestor_inputs(contract)
            nav_profile = NAV.build_navigation_profile(
                requirement.aspects, contract_version=requirement.contract_version,
                contract_fingerprint=requirement.contract_fingerprint,
                ancestor_labels=_labels, sibling_keys=_siblings)
            return TR.IndexedTreeNavigation(index=index, profile=nav_profile), nav_profile

        def _context(requirement, *, run_id: str, section_id: str = "",
                     task_id: str = "", report_as_of: str | None = "__same__",
                     company_id: str = "", document_=None, policy=None):
            doc = document_ or document
            return TR.TopicRunContext(
                run_id=run_id, case_id=run_id + "-case",
                company_id=company_id or requirement.company_id,
                section_id=section_id or requirement.section_id,
                task_id=task_id or requirement.task_id,
                report_as_of=(requirement.report_as_of
                              if report_as_of == "__same__" else report_as_of),
                demo_scope_fingerprint=manifest.scope_input_fingerprint,
                projection_version=projection.projection_id,
                document=doc,
                # 单文档退化源集：与 `document` 同一份、角色为唯一当前锚，照常走
                # `DocumentSourceSet` 的全部校验（不是绕过校验的测试捷径）。
                sources=TS.DocumentSourceSet.single_document(
                    company_id=doc.company_id, document_id=doc.document_id,
                    document_version=doc.document_version,
                    evidence_set_version=doc.evidence_set_version),
                budget_policy=policy or budget,
                started_at=STARTED_AT)

        def _deps(requirement, run_context, *, budget_state=None, navigation=None,
                  policy_resolver_=None, research=None, policy=None,
                  resolvers=None, store_=None, recall_log=None):
            navigation_ = navigation if navigation is not None else _navigation_for(
                requirement)[0]
            if research is None:
                research = _SpanRecallResearch(
                    navigation=navigation_, requirement=requirement, document=document,
                    tree_max_spans=budget.tree_max_spans_per_aspect,
                    tree_max_chars=budget.tree_max_chars_per_span)
                # 诊断面（默认关闭）：把替身**实例**留给调用方。它记着「这次有界调用召回了
                # 什么、采信了哪一条、其余为什么没进材料」——这是 A1 源头对账的唯一现场。
                if recall_log is not None:
                    recall_log.append((str(requirement.topic_id), research))
            return TR.TopicRuntimeDependencies(
                registry=reg, llm=object(), navigation=navigation_,
                information_need_builder=_NeedBuilder(),
                route_context_builder=lambda: _route_context(
                    run_context.company_id),
                route_fn=_route_fn,
                budget_state=budget_state or TR.TopicBudgetState(
                    policy=policy or budget),
                trace_sink=sink, store=store_ if store_ is not None else store,
                payload_resolvers=(resolvers if resolvers is not None
                                   else (session.payload_resolver,)),
                source_policy_resolver=(policy_resolver_
                                        if policy_resolver_ is not None
                                        else policy_resolver),
                set_completeness_verifier=scv, set_enumeration_verifier=sev,
                clock=lambda: STARTED_AT, research_question=research)

        company_ctx = _context(reqs[SLICE_COMPANY_TOPICS[0]], run_id="m930-2-company-1")
        industry_ctx = _context(reqs[SLICE_INDUSTRY_TOPIC], run_id="m930-2-industry-1")
        #: A1 源头对账的现场：`(topic_id, 研究替身实例)` 逐条记下（默认空 ⇒ 不影响既有行为）。
        company_recall: list = []

        # ================= B. 真实代表性纵向切片 =========================
        company_reqs = tuple(reqs[t] for t in company_task.topic_ids)
        phase = CW.run_backbone_topic_phase(
            company_task, section_id="company", requirements=company_reqs,
            run_context=company_ctx,
            dependencies_of=lambda req, ctx: _deps(req, ctx, recall_log=company_recall),
            store=reader)
        check(phase.phase_version and phase.section_id == "company"
              and phase.task_id == company_task.task_id,
              "backbone phase 记录自身版本与真实 task/section 身份")
        check(tuple(r.identity.topic_id for r in phase.results)
              == tuple(company_task.topic_ids),
              "phase 结果顺序 = 真实 SectionTask.topic_ids（含两个以上 selected 公司 topic）")
        check(all(r.current for r in phase.results),
              "每个真实 topic 都提交了 current Pack")

        packs = {p.topic_id: p for p in phase.pack_set.packs}
        results = {r.identity.topic_id: r for r in phase.results}
        check(set(packs) == set(company_task.topic_ids),
              "读门回读到的 Pack 集合 = 真实 task 的 topic 集合")

        # (2) 真实合格 span 材料合计 + 逐条可独立重切
        material_total = sum(len(p.materials) for p in packs.values())
        for topic in sorted(packs):
            note(f"{topic}: aspect={len(packs[topic].aspect_results)} "
                 f"materials={len(packs[topic].materials)} "
                 f"facts={len(packs[topic].facts)} "
                 f"unresolved={len(packs[topic].unresolved)}")
        check(material_total >= 2,
              f"切片合计取得多个真实合格 span 材料（实测 {material_total}）")
        payload_ok = True
        table_materials = 0
        for pack in packs.values():
            for material in pack.materials:
                if material.material_type == TR.TABLE_MATERIAL_TYPE:
                    table_materials += 1
                try:
                    TS.verify_material_payload_ref(material.payload_ref,
                                                   session.payload_resolver)
                except TS.SchemaValidationError:
                    payload_ok = False
        check(payload_ok,
              f"切片每条材料 payload 都能独立重切（含 {table_materials} 份表对象材料；"
              f"走 v6 合并解析器，不采信运行对象自报）")

        # (3)(4) 事实只能经**唯一 span** 闭环；同 Evidence 多 span 必须 fail-closed。
        # 唯一性判在**该事实所属 aspect 已准入的材料**上（与 runtime 同一判据单位）；跨 aspect
        # 共享同一父 Evidence 不算歧义。每条事实的文本还要与它引用的那**一份真实 span** 的
        # payload 文本逐字相等——用独立重切读出的字节，不采信运行对象自报。
        fact_total = 0
        unique_span_facts = 0
        verbatim_ok = True
        for pack in packs.values():
            by_material_id = {m.material_id: m for m in pack.materials}
            facts_by_id = {f.fact_id: f for f in pack.facts}
            for res in pack.aspect_results:
                own = [by_material_id[i] for i in res.material_ids
                       if i in by_material_id]
                own_by_evidence = _index_by_evidence(own)
                for fact_id in res.supported_fact_ids:
                    fact_total += 1
                    fact = facts_by_id[fact_id]
                    refs = tuple(fact.citation_refs)
                    if (not refs or any(r.ref_type != "evidence" for r in refs)
                            or len(refs) != 1
                            or any(len(own_by_evidence.get(r.evidence_id, ())) != 1
                                   for r in refs)):
                        verbatim_ok = False
                        continue
                    unique_span_facts += 1
                    cited = own_by_evidence[refs[0].evidence_id][0]
                    if _payload_text(cited, session.resolver) != fact.text:
                        verbatim_ok = False
                    if fact.aspect_ids != (res.aspect_id,):
                        verbatim_ok = False
        # ---- A1 源头对账（只读诊断）--------------------------------------
        # 公司节的描述性材料是在**源头哪一层**掉的？替身每次有界调用都留下召回全集与采信
        # 结果，这里用与验收 runner **同一份**选材实现（`run_m930_3_acceptance._slice_audit`）
        # 逐条判读**未被采信**的候选：它们里面是否本来就有合法描述性原子。**不改变采集或
        # 裁决**——召回与采信仍按原规则执行，本段只读产物、只印清单。
        from evaluation import run_m930_3_acceptance as ACC

        recall_rows = [(str(topic_id), call) for topic_id, research_ in company_recall
                       for call in research_.calls]
        # 采信结果按 **span 身份**记（`adopted_span_id`），不按父 Evidence：一个父 Evidence 下
        # 可以有多个 span，按父记录会让「多条 span 同父」时的未采信数凭空变成 0。
        check(bool(recall_rows) and all(
            {"recalled", "adopted_span_id", "discarded", "max_spans",
             "max_chars_per_span", "tool_gaps", "tool_skipped"} <= set(call)
            for _, call in recall_rows),
            "研究替身必须逐次记下召回全集、按 span 的采信结果、未采信集与当次上界"
            "（源头对账的唯一现场）")
        consistent = True
        for _, call in recall_rows:
            adopted_rows = [row for row in call["recalled"]
                            if row["span_id"] == call["adopted_span_id"]]
            if len(call["discarded"]) != len(call["recalled"]) - len(adopted_rows):
                consistent = False
        check(consistent,
              "每次调用的「未采信」必须恰好是召回集里除去采信 span 的那些（记录不得自相矛盾）")
        dropped_with_atoms: list[dict] = []
        for topic_id, call in recall_rows:
            for row in call["discarded"]:
                audit = ACC._slice_audit(row["text"])
                if audit["eligible"]:
                    dropped_with_atoms.append({
                        "topic_id": topic_id, "aspect_id": call["aspect_id"],
                        "span_id": row["span_id"], "chars": row["chars"],
                        "atoms": audit["eligible"][:2]})
        # 「召回集里到底有没有合法描述性原子」是把「语料里没有」与「召回/采信选错了」分开的
        # 唯一读数：只看**被采信**的那一条会把「采信选到 `□适用 不适用` 这类行」误判成
        # 「源头没有可写内容」。这里对**召回全集**逐条判读（只读，不改召回与采信规则）。
        recalled_with_atoms: list[dict] = []
        for topic_id, call in recall_rows:
            for row in call["recalled"]:
                audit = ACC._slice_audit(row["text"])
                if audit["eligible"]:
                    recalled_with_atoms.append({
                        "topic_id": topic_id, "aspect_id": call["aspect_id"],
                        "span_id": row["span_id"], "chars": row["chars"],
                        "adopted": row["span_id"] == call["adopted_span_id"],
                        "atoms": audit["eligible"][:2]})
        check(len(recalled_with_atoms) >= len(dropped_with_atoms),
              "「召回集里含合法描述性原子」的条数必须 ≥「被丢掉的候选里含合法描述性原子」的条数"
              "（未采信集是召回集的子集）")
        # 抽样印出**真实召回文本**的前 60 字（含被采信的那条）：对账要能看出源头这一层实际
        # 落到的是叙述句、表格单元格还是勾选行。只印预览，不把整段真实正文抄进报告。
        def _span_preview(text: str) -> str:
            flat = " ".join(str(text or "").split())
            return flat[:60] + ("…" if len(flat) > 60 else "")

        longest = sorted([(row["chars"], call, row)
                          for _, call in recall_rows for row in call["recalled"]],
                         key=lambda item: -item[0])[:3]
        note(f"A1 召回面：召回集里**含合法描述性原子**的 span {len(recalled_with_atoms)}/"
             f"{sum(len(c['recalled']) for _, c in recall_rows)} 条（其中已被采信的 "
             f"{sum(1 for r in recalled_with_atoms if r['adopted'])} 条）；按字数最长的三条真实"
             f"召回文本预览：{[_span_preview(r['text']) for _, _, r in longest]}")
        skipped_reasons: dict[str, int] = {}
        for _, call in recall_rows:
            for row in call["tool_skipped"]:
                key = str(row.get("reason") or "?")
                skipped_reasons[key] = skipped_reasons.get(key, 0) + 1
        note(f"A1 源头对账：公司节有界树调用 {len(recall_rows)} 次 / 召回 "
             f"{sum(len(c['recalled']) for _, c in recall_rows)} 条 span 候选 / 采信 "
             f"{sum(1 for _, c in recall_rows if c['adopted_span_id'])} 条 / 未被采信 "
             f"{sum(len(c['discarded']) for _, c in recall_rows)} 条；其中**未被采信但已含合法"
             f"描述性原子**的候选 {len(dropped_with_atoms)} 条；树工具自报 gaps "
             f"{sum(c['tool_gaps'] for _, c in recall_rows)} 条 / skipped "
             f"{sum(len(c['tool_skipped']) for _, c in recall_rows)} 条 "
             f"{dict(sorted(skipped_reasons.items()))}（上界未放大：{sorted({c['max_spans'] for _, c in recall_rows})} "
             f"span / {sorted({c['max_chars_per_span'] for _, c in recall_rows})} 字）"
             + (f"。示例：{dropped_with_atoms[:3]}" if dropped_with_atoms else
                "（被丢掉的候选里没有合法描述性原子）"))

        check(unique_span_facts >= 2,
              f"至少两个事实经唯一 span 闭环（实测 {unique_span_facts}）")
        check(verbatim_ok,
              "每个事实的 citation 都唯一确定一份材料，且 claim 文本逐字等于该 span 的真实文本")
        check(fact_total == unique_span_facts,
              f"没有事实走「同 Evidence 多 span 任选一条」的旁路（facts={fact_total}）")

        ambiguous = []
        for topic, result in results.items():
            for gap in result.gaps:
                detail = str(gap.get("detail") or "")
                if "citation_ambiguous_across_spans" in detail:
                    ambiguous.append((topic, gap.get("aspect_id")))
        check(bool(ambiguous),
              "同一 Evidence 被切成多 span 时，事实采纳被 citation_ambiguous_across_spans 拒绝")
        for topic, aspect_id in ambiguous[:5]:
            no_fact = all(not r.supported_fact_ids for r in packs[topic].aspect_results
                          if r.aspect_id == aspect_id)
            check(no_fact,
                  f"{aspect_id}: 歧义 citation 不产生任何事实（fail-closed）")

        # (5) typed readback（只读 Store，不看运行对象）
        for topic, result in results.items():
            loaded = reader.load_current(result.identity).pack
            check(loaded is not None and loaded.pack_id == result.pack_id,
                  f"{topic}: typed readback 读回同一个 current Pack")
            if loaded is None:
                continue
            check([a.aspect_id for a in loaded.aspect_results]
                  == [a for a, _ in result.aspect_statuses],
                  f"{topic}: 读回 aspect 顺序与运行结果一致")
            check(loaded.dependency_fingerprint
                  == reqs[topic].dependency_fingerprint(),
                  f"{topic}: Pack 依赖指纹 = requirement 重算值")
            check(all(a.requirement_snapshot.source_policy_ref.content_fingerprint
                      == projection.source_policy_fingerprint
                      for a in loaded.aspect_results),
                  f"{topic}: 读回的 Pack 绑定的就是冻结投影的 SourcePolicy")

        # (6) 完整 Pack set 门
        check(phase.pack_set.gate_version == PSet.PACK_SET_GATE_VERSION
              and phase.pack_set.task_id == company_task.task_id,
              "phase 给出通过读门的完整 Pack set（gate 版本 + 真实 task 身份）")
        for topic, result in results.items():
            check(phase.pack_set.pack_for(topic).pack_id == result.pack_id,
                  f"{topic}: Pack set 中的 pack_id = 刚提交的运行结果")
        check(bool(phase.pack_set.materials()),
              "Pack set 交付的材料非空（真实内容，不是空壳）")

        # (7) 终态与 searched-scope gap（不得把「未取得」写成「不存在」）
        statuses: dict[str, str] = {}
        for topic, result in results.items():
            packing = packs[topic]
            check(all(s in _STATUSES for _, s in result.aspect_statuses),
                  f"{topic}: 逐 aspect 终态来自封闭集合")
            check([a for a, _ in result.aspect_statuses]
                  == [a.aspect_id for a in reqs[topic].aspects],
                  f"{topic}: 逐 aspect 结果精确覆盖投影的 aspect 集合（不缺不重不混入）")
            for aspect_id, status in result.aspect_statuses:
                statuses[aspect_id] = status
            gap_ids = {g.unresolved_id for g in packing.unresolved}
            for res in packing.aspect_results:
                check(all(u in gap_ids for u in res.unresolved_ids),
                      f"{res.aspect_id}: 每个 unresolved 引用都真在 Pack.unresolved 中")
                if res.status == "covered":
                    check(bool(res.supported_fact_ids) and bool(res.material_ids),
                          f"{res.aspect_id}: covered 必须同时有材料与事实")
                else:
                    check(bool(res.unresolved_ids),
                          f"{res.aspect_id}: 非 covered 必须留下 unresolved 记录")
            declared = {g["reason"] for g in result.gaps}
            check(declared <= set(TR.RUNTIME_GAP_REASONS),
                  f"{topic}: 声明型 gap 原因全部来自封闭集合")
            check(all(isinstance(g, dict) and g.get("aspect_id") and g.get("detail")
                      for g in result.gaps),
                  f"{topic}: 每条声明型 gap 都定位到 aspect 并带说明（不静默止步）")
        note("终态分布: " + json.dumps(
            collections.Counter(statuses.values()), ensure_ascii=False))

        searched_scope_gaps = []
        for topic, result in results.items():
            for gap in result.gaps:
                detail = str(gap.get("detail") or "")
                # 只在**运行链自己写的部分**上判措辞：冻结 requirement 文本是上游输入，
                # 不属本轮的表述责任（故先把已知的 requirement 文本尾巴剥掉）。
                own = detail
                for aspect in reqs[topic].aspects:
                    own = own.replace(aspect.requirement_text[:120], "").replace(
                        aspect.requirement_text, "")
                if any(w in own for w in FORBIDDEN_ABSENCE_WORDING):
                    check(False,
                          f"{topic}: gap 文案不得判断「不存在」：{own[:80]}")
                if gap.get("reason") in ("tree_structure_unavailable", "tree_no_material",
                                         "not_found_conditions_unmet",
                                         "required_search_scope_unproven",
                                         "focused_research_no_supported_fact"):
                    searched_scope_gaps.append((topic, gap.get("aspect_id"),
                                                gap.get("reason")))
        check(bool(searched_scope_gaps),
              "切片在真实检索范围内如实给出 searched-scope gap（不写成不存在）")
        for topic, aspect_id, reason in searched_scope_gaps[:6]:
            note(f"searched-scope gap: {topic}/{aspect_id} <- {reason}")

        not_found_packs = [p for p in packs.values()
                           if any(a.status == "not_found" for a in p.aspect_results)]
        for pack in not_found_packs:
            audited = {a.audit_id for a in pack.not_found_audits}
            for res in pack.aspect_results:
                if res.status != "not_found":
                    continue
                check(res.not_found_audit_id in audited,
                      f"{res.aspect_id}: not_found 必须带同 id 的真实审计")
                owned = [g for g in pack.unresolved
                         if g.unresolved_id in res.unresolved_ids
                         and res.aspect_id in g.aspect_ids]
                check(len(owned) == 1 and owned[0].detail.startswith(NOT_FOUND_WORDING),
                      f"{res.aspect_id}: not_found 文案只能是「{NOT_FOUND_WORDING}」"
                      f"（实得 {[g.detail[:40] for g in owned]}）")
                check(owned[0].not_found_audit_id == res.not_found_audit_id
                      if owned else False,
                      f"{res.aspect_id}: not_found gap 与审计 id 一致")
        note(f"not_found aspects: {sum(1 for p in packs.values() for a in p.aspect_results if a.status == 'not_found')}")

        # (8) P1-B：not_found 必须由**可复核的实际检索范围**证明（反例 + 正例）
        #
        # 旧口径把 `allowed_capabilities`（= 允许调用哪些动作）当成"应有检索范围"，并接受
        # 任意一次尝试当作"范围已查过"的证明，于是在真实切片里凭空产出 not_found。下面用
        # **真实冻结 aspect** 与**真实树工具结果**逐条钉住新口径：期望值不写死，只钉资格条件。
        check(TR.NOT_FOUND_GAP_WORDING == NOT_FOUND_WORDING,
              "生产写 not_found 文案用的是与本测试同一个常量（规则不只在测试里）")
        check(TR.NOT_FOUND_AUDIT_POLICY_VERSION == "nfap-2",
              "not-found 审计派生口径已升版本（口径变了必须升版本，审计字段可据此复算）")
        check("required_search_scope_unproven" in TR.RUNTIME_GAP_REASONS,
              "「应有检索范围不可证」是运行时封闭 gap 原因之一（终态如实 blocked）")

        # `projection_aspects` 覆盖全部 selected topic（用于挑真实输入）；
        # `all_aspects` 只是已经跑过 runtime、有终态的**公司**那几个 topic。
        topic_of_aspect = {a.aspect_id: req.topic_id
                           for req in projection.requirements for a in req.aspects}
        projection_aspects = tuple(a for req in projection.requirements
                                   for a in req.aspects)
        all_aspects = tuple(a for t in company_task.topic_ids
                            for a in reqs[t].aspects)
        scope_of = {a.aspect_id: TR.required_search_scope(a)
                    for a in projection_aspects}
        check(all(scope_of.values()),
              "每条真实 aspect 的应有检索范围都由冻结 EvidenceRequirement 语义非空派生"
              "（正是旧口径缺的那一步）")
        check(any("external" in s for s in scope_of.values()),
              "真实投影中确有 aspect 的应有范围含 external（反例场景来自真实契约）")
        ci_only = tuple(a for a in all_aspects
                        if scope_of[a.aspect_id] == ("company_industry",))
        check(bool(ci_only),
              "真实投影中确有应有范围恰为 company_industry 的 aspect（正例有真实输入）")

        def _real_tree_trace(aspect):
            """经**正式树工具**为某 aspect 真跑一次，返回真实 `ToolResult`。

            参数与 runtime 自己的树调用逐字段相同；不添加任何"来源标签"——轨迹里有什么
            来源类，就只能由它自己的字段派生出来。
            """
            topic = topic_of_aspect[aspect.aspect_id]
            nav = _navigation_for(reqs[topic])[0]
            node_ids = nav.node_ids_for(nav.candidates(aspect, requirement=reqs[topic]))
            if not node_ids:
                return None
            call_id = f"p1b-{aspect.aspect_id}"
            call = C.ToolCall(
                call_id=call_id, tool_name=TT.TREE_INSPECT_TOOL_NAME,
                arguments={**document.to_dict(), "need_id": call_id,
                           "aspect_id": aspect.aspect_id, "topic_id": topic,
                           "node_ids": list(node_ids),
                           "max_spans": budget.tree_max_spans_per_aspect,
                           "max_chars_per_span": budget.tree_max_chars_per_span},
                idempotency_key=call_id, need_id=call_id, batch_id=topic)
            return reg.execute(call, route="DIRECT_EVIDENCE", run_id="m930-2-p1b")

        traces_by_aspect = {}
        for aspect in projection_aspects:
            got = _real_tree_trace(aspect)
            if got is not None:
                traces_by_aspect[aspect.aspect_id] = got

        def _has_candidates(aspect) -> bool:
            return bool(getattr(traces_by_aspect.get(aspect.aspect_id),
                                "evidence_ids", ()))

        with_candidates = tuple(a for a in all_aspects if _has_candidates(a))
        check(bool(with_candidates),
              "真实树工具确实返回过带父 Evidence 的候选（反例/正例都用真实 ToolResult）")

        def _audit(aspect, *, traces, stop_reason=STOP_NOT_FOUND,
                   searched=("p1b-need-fixture",), attempts=1, unattempted=(),
                   budget_exhausted=False, external_attempted=False):
            """只调**生产**判定单元 `TR.derive_not_found_audit`（决定 not_found/blocked 的那一步）。

            `searched` 是本单元的夹具 id：生产路径传的是真实 `attempted` need 列表；
            `traces` 一律是真实对象，绝不传自造的"来源标签"。
            """
            return TR.derive_not_found_audit(
                aspect=aspect, searched_need_ids=tuple(searched),
                valid_attempt_count=attempts, traces=tuple(traces),
                external_attempted=external_attempted,
                alternative_candidate_ids=(),
                unattempted_candidate_ids=tuple(unattempted),
                context_expansion_attempted=False, stop_reason=stop_reason,
                budget_exhausted=budget_exhausted)

        # 正例：实际轨迹覆盖了应有范围，且未尝试候选为空、预算未耗尽 → 才允许 not_found。
        pos_pool = tuple(a for a in with_candidates
                         if scope_of[a.aspect_id] == ("company_industry",))
        check(bool(pos_pool),
              "真实切片里存在『应有范围=company_industry 且树工具真返回过候选』的 aspect")
        # 反例（§三 item 1）：只查了树材料，而应有范围还含别的来源类 → 不得 not_found。
        tree_only_pool = tuple(a for a in with_candidates
                               if set(scope_of[a.aspect_id]) - {"company_industry"})
        check(bool(tree_only_pool),
              "真实切片里存在『树工具返回过候选但应有范围还含外部/结构化来源』的 aspect")
        # 纯 external 范围：只有真实外部用量才能证明它 → 一对正反例只差这一个真实事实。
        ext_only_pool = tuple(a for a in projection_aspects
                              if scope_of[a.aspect_id] == ("external",))
        check(bool(ext_only_pool),
              "真实投影中确有应有范围恰为 external 的 aspect（门槛可被真实外部用量满足）")

        if pos_pool:
            pos = pos_pool[0]
            pos_traces = (traces_by_aspect[pos.aspect_id],)
            pos_audit, pos_blockers = _audit(pos, traces=pos_traces)
            check(pos_audit.qualified is True and pos_blockers == (),
                  f"{pos.aspect_id}: 真实轨迹覆盖应有范围 + 无未尝试候选 + 非预算耗尽 "
                  f"→ 才允许 not_found（blockers={pos_blockers}）")
            check(pos_audit.required_source_scope
                  == scope_of[pos.aspect_id]
                  and set(pos_audit.required_source_scope)
                  <= set(pos_audit.attempted_source_types),
                  f"{pos.aspect_id}: 审计的应有范围 = 冻结语义派生值，且被真实轨迹逐类覆盖")
            check(pos_audit.policy_version == TR.NOT_FOUND_AUDIT_POLICY_VERSION
                  and pos_audit.valid_attempt_count == 1,
                  f"{pos.aspect_id}: 审计自带派生规则版本与真实尝试次数")

            # 反例 2：有真实存在过的候选被有界上限挡下（范围已覆盖，照样不得 not_found）。
            real_candidate_ids = tuple(traces_by_aspect[pos.aspect_id].evidence_ids)
            audit_u, blockers_u = _audit(pos, traces=pos_traces,
                                         unattempted=(real_candidate_ids[0],))
            check(audit_u.qualified is False
                  and audit_u.unattempted_candidate_ids == (real_candidate_ids[0],)
                  and any("挡下" in b for b in blockers_u)
                  and set(audit_u.required_source_scope)
                  <= set(audit_u.attempted_source_types),
                  f"{pos.aspect_id}: 范围已覆盖但仍有真实候选未尝试 → 照样不得 not_found")

            # 反例 3：因预算耗尽停止。
            audit_b, blockers_b = _audit(pos, traces=pos_traces,
                                         budget_exhausted=True)
            check(audit_b.qualified is False and audit_b.budget_exhausted is True
                  and any("预算耗尽" in b for b in blockers_b),
                  f"{pos.aspect_id}: 预算耗尽停止 → 不得 not_found（是没查完，不是确无）")

            # 反例 4：原子停止原因不是 NOT_FOUND_AFTER_SEARCH（这里用"没有可检索 scope"）。
            audit_s, blockers_s = _audit(pos, traces=pos_traces,
                                         stop_reason=STOP_NO_SEARCHABLE_SCOPE)
            check(audit_s.qualified is False
                  and any("NOT_FOUND_AFTER_SEARCH" in b for b in blockers_s),
                  f"{pos.aspect_id}: 停止原因不是 NOT_FOUND_AFTER_SEARCH → 不得 not_found")

            # 反例 5：没有真实 searched need / 没有真实尝试。
            audit_n, blockers_n = _audit(pos, traces=pos_traces, searched=(), attempts=0)
            check(audit_n.qualified is False and len(blockers_n) >= 2
                  and any("searched need" in b for b in blockers_n)
                  and any("尝试" in b for b in blockers_n),
                  f"{pos.aspect_id}: 没有真实 searched need / 没有真实尝试 → 不得 not_found")

            # 反例 6：冻结 EvidenceRequirement 无从声明可比来源范围 → 一律 fail-closed。
            no_er = dataclasses.replace(pos, evidence_requirement_ids=())
            try:
                scope_none = TR.required_search_scope(no_er)
            except Exception:            # noqa: BLE001 —— 冻结语义无从派生即 fail-closed
                scope_none = ()
            audit_e, blockers_e = _audit(no_er, traces=pos_traces)
            check(audit_e.qualified is False
                  and (not scope_none
                       or any("required_search_scope_unproven" in b
                              for b in blockers_e)),
                  f"{pos.aspect_id}: 冻结语义无法声明可比范围 → fail-closed，不新增猜测映射")

        if tree_only_pool:
            only = tree_only_pool[0]
            audit_o, blockers_o = _audit(only, traces=(traces_by_aspect[only.aspect_id],))
            missing = sorted(set(scope_of[only.aspect_id]) - {"company_industry"})
            check(audit_o.qualified is False
                  and any("required_search_scope_unproven" in b for b in blockers_o)
                  and sorted(set(audit_o.required_source_scope)
                             - set(audit_o.attempted_source_types)) == missing,
                  f"{only.aspect_id}: 只查树材料而应有范围还含 {missing} → 判定不成立、"
                  f"终态只能 blocked（不得由『没查到』反推 not_found）")
            check(audit_o.attempted_source_types == ("company_industry",),
                  f"{only.aspect_id}: 实际触达范围只由真实轨迹字段派生（不把树工具当全部来源）")

        if ext_only_pool:
            ext = ext_only_pool[0]
            ext_traces = ((traces_by_aspect[ext.aspect_id],)
                          if ext.aspect_id in traces_by_aspect else ())
            audit_x1, blockers_x1 = _audit(ext, traces=ext_traces,
                                           external_attempted=True)
            audit_x0, blockers_x0 = _audit(ext, traces=ext_traces,
                                           external_attempted=False)
            check(audit_x1.qualified is True
                  and "external" in audit_x1.attempted_source_types,
                  f"{ext.aspect_id}: 有真实外部用量时纯 external 范围可被证明（blockers={blockers_x1}）")
            check(audit_x0.qualified is False
                  and any("required_search_scope_unproven" in b for b in blockers_x0),
                  f"{ext.aspect_id}: 同一条轨迹、只去掉真实外部用量 → 立刻不可证（正反例只差这一个事实）")

        # (9) 真实切片的资格一致性：不固定断言 not_found 数量，只按新口径核资格与分布。
        audit_by_id = {a.audit_id: a for p in packs.values() for a in p.not_found_audits}
        real_reason_counts = collections.Counter(
            g.get("reason") for t in results for g in results[t].gaps)
        note("运行链声明的 gap 原因分布: "
             + json.dumps(real_reason_counts, ensure_ascii=False, sort_keys=True))
        check(all(r in TR.RUNTIME_GAP_REASONS for r in real_reason_counts),
              "真实切片声明的 gap 原因全部来自封闭集合")
        check("required_search_scope_unproven" in real_reason_counts,
              "真实切片确有 aspect 因『应有检索范围不可证』被如实降级为 blocked")

        # (9b) §二 2.2：Contract 声明了表格槽位的 aspect，本轮材料里没有**表格类材料**时
        # 必须留下 typed gap，不得静默"零材料"（"零材料"分不清「查过、确实没有」与
        # 「这条通道根本不产表格材料」）。反向也必须成立：正文 aspect 不得被误发这条 gap。
        # 切片内契约面比本次 run 宽（Projection 覆盖全 Contract，本次只跑 company 节）：
        # 只对**真的被评估**的 aspect 断言（`statuses` 正好是逐 aspect 结果的面）。
        snapshot_of = {a.aspect_id: a for t in reqs for a in reqs[t].aspects}
        table_aspects = [a for a in snapshot_of.values()
                         if a.aspect_id in statuses and TR._requires_table_output(a)]
        check(bool(table_aspects),
              "本切片确实评估了 Contract 声明表格槽位的 aspect（否则下面的检查是空转）")
        table_gaps_by_aspect: dict[str, list[dict]] = collections.defaultdict(list)
        for t in results:
            for g in results[t].gaps:
                if g.get("reason") == "tree_table_material_unavailable":
                    table_gaps_by_aspect[g.get("aspect_id")].append(g)
        check(all(TR._requires_table_output(snapshot_of[aid]) for aid in table_gaps_by_aspect),
              "表格槽位缺口只发给 Contract content_role 真的声明了表格的 aspect（不误伤正文 aspect）")
        # v6 之后「零表格材料」是**可复算的结论**而不是结构性宿命：表对象通道会把已定位块里
        # 获放行的表连成 `table_context` 材料。因此这条 gap 的成立条件是**条件式**的：
        # 恰好 = 该 aspect 本轮一份表格类材料都没拿到。拿到就不能发（发了就是把已取得的
        # 材料当成没取得），没拿到就必须发（不得静默零材料）。
        table_material_count_by_aspect: dict[str, int] = collections.Counter()
        for t in results:
            pack = packs[t]
            table_ids = {m.material_id for m in (pack.materials or ())
                         if m.material_type == TR.TABLE_MATERIAL_TYPE}
            for d in (pack.material_dispositions or ()):
                if d.material_id not in table_ids:
                    continue
                for aid in tuple(d.aspect_ids or ()):
                    table_material_count_by_aspect[str(aid)] += 1
        starved = sorted(a.aspect_id for a in table_aspects
                         if table_material_count_by_aspect.get(a.aspect_id, 0) == 0)
        fed = sorted(a.aspect_id for a in table_aspects
                     if table_material_count_by_aspect.get(a.aspect_id, 0) > 0)
        note("表格槽位缺口对账: 零表材料（必须留 gap）"
             + json.dumps(starved, ensure_ascii=False)
             + " / 已取得表材料（不得留 gap）"
             + json.dumps(fed, ensure_ascii=False))
        check(sorted(table_gaps_by_aspect) == starved,
              f"表格槽位缺口与「本轮零表格材料」逐 aspect 等价"
              f"（有 gap 的={len(table_gaps_by_aspect)} / 零表材料={len(starved)}）")
        check(all(a not in table_gaps_by_aspect for a in fed),
              f"已取得表材料（含表对象材料）的表格槽位 aspect 不得再发缺口（{len(fed)} 条）")
        check(all(len(v) == 1 for v in table_gaps_by_aspect.values()),
              "同一 aspect 的表格槽位缺口不重复登记")
        check(all(g.get("table_material_count") == 0
                  and g.get("content_role") == snapshot_of[g["aspect_id"]].content_role
                  and isinstance(g.get("material_count"), int)
                  and isinstance(g.get("table_object_material_count"), int)
                  for v in table_gaps_by_aspect.values() for g in v),
              "缺口自带 typed 维度：content_role 逐字来自 Contract、表格材料数为 0、"
              "正文材料数与表对象材料数分列如实计数")
        check(all(statuses[aid] in _STATUSES for aid in table_gaps_by_aspect),
              "表格槽位缺口不改 aspect 终态：它只登记缺口的维度，不把整个 aspect 判废")
        check(all("表格" in g["detail"] and "没有在本轮被定位到并放行" in g["detail"]
                  and "不是「语料里没有这张表」" in g["detail"]
                  for v in table_gaps_by_aspect.values() for g in v),
              "缺口说明写清『本栏目本轮未取得合格表材料』而不是『查无此表』（不得判断不存在）"
              "并指向 typed 拒绝轨迹")

        # (9c) §二 2.8：缺口只由「Contract 必需内容**经检索后仍未满足**」产生，且三条身份不混用。
        #  - 每个 `research_contract_gap` 的 aspect 必须**一份合格事实都没有**（未满足才是缺口；
        #    已满足的不得因别的理由被造缺口）；
        #  - 表格槽位缺口是 **runtime 声明型**，只进 `TopicRuntimeResult.gaps`：它**不得**溜进
        #    Store 侧的 `ContractGap` 集合，也不得蹭进该 aspect 的 `unresolved_ids`
        #    （`ResearchGap.reason_code` 有自己的封闭集合，两个身份不得混装）。
        facts_of = {res.aspect_id: tuple(res.supported_fact_ids)
                    for t in results for res in packs[t].aspect_results}
        contract_gap_aspects = {g.aspect_id for t in results
                                for g in packs[t].contract_gaps}
        check(bool(contract_gap_aspects),
              "本切片确实产生了 ContractGap（否则『只发给零事实 aspect』这条检查是空转）")
        check(all(not facts_of.get(aid, ()) for aid in contract_gap_aspects),
              f"ContractGap 只发给一份合格事实都没有的 aspect（{len(contract_gap_aspects)} 条逐一核对："
              f"必需内容已取得就不得再造缺口）")
        store_gap_reasons = {g.reason_code for t in results
                             for g in packs[t].unresolved}
        check("tree_table_material_unavailable" not in store_gap_reasons,
              "表格槽位缺口是 runtime 声明型：不得写进 Store 侧 reason_code 封闭集合")
        check(all("gap_id" not in g
                  for v in table_gaps_by_aspect.values() for g in v),
              "表格槽位缺口不携带 ContractGap 身份（不是它的替身，也不得冒用它的字段）")
        check(all(not any(gid.startswith("cgap_") for gid in res.unresolved_ids)
                  for t in results for res in packs[t].aspect_results
                  if res.aspect_id in table_gaps_by_aspect),
              "表格槽位缺口不进入 aspect 的 unresolved 引用面（声明型缺口不冒充 Store 缺口）")

        # (9d) §二 2.1 / 2.3 的**数据源**：验收侧 A1 对账只读真轨迹事件。因此真轨迹里必须
        # 同时带上「三条互斥去路」的两个计数与逐条内容处置（typed 原因 + locator），
        # 否则对账只能看到候选总数，分不清「结构上切不出来」与「切出来了内容不合格」。
        tree_results = [e for e in sink.events if e.get("type") == "TREE_TOOL_RESULT"]
        recuts = [e for e in sink.events if e.get("type") == "EVIDENCE_FALLBACK_RECUT"]
        dispositions = [e for e in sink.events if e.get("type") == "TREE_CONTENT_DISPOSITION"]
        check(bool(tree_results),
              "真轨迹里有树检视结果事件（A1 对账的召回层读数来自它，不是替身的私有记录）")
        check(all(isinstance(e["payload"].get("gap_count"), int)
                  and isinstance(e["payload"].get("content_disposition_count"), int)
                  for e in tree_results),
              "树检视事件同时报结构 gap 数与内容处置数（三条去路不得混成一句『没进材料』）")
        check(all(isinstance(e["payload"].get("content_disposition_count"), int)
                  for e in recuts),
              "有界 Evidence 重切事件同样报内容处置数（两条召回路径口径一致）")
        check(bool(dispositions) and all(
            e["payload"].get("aspect_id") and e["payload"].get("kind")
            and e["payload"].get("reason") for e in dispositions),
            "逐条内容处置事件带 aspect / 内容形态 / typed 原因（可回查，不是丢弃）")
        # 「汇总计数 = 逐条明细」这条等式只对**真的派发过调用**的行成立：真读才会逐条
        # `_emit`。复用行（`REUSED_READ_SET`）**不派发调用、不产生新事件**，但按设计必须把
        # 首读的完整读数（含 gap 数与内容处置数）一并交回，因此它的计数是**结转**值——把它
        # 加进等式会把同一批处置数成两遍。这里沿用本文件既有的「真读 = 状态非
        # REUSED_READ_SET」口径只对真读求和，结转值另立一条 check 逐行核对（下一条）。
        real_tree_results = [e for e in tree_results
                             if e["payload"].get("status") != "REUSED_READ_SET"]
        reuse_result_rows = [e for e in tree_results
                             if e["payload"].get("status") == "REUSED_READ_SET"]
        check(len(dispositions)
              == sum(e["payload"]["content_disposition_count"]
                     for e in real_tree_results + recuts),
              f"逐条内容处置数 = **真读**汇总计数之和（{len(dispositions)} 条；"
              f"不得只报总数不落明细，也不得报了明细却漏记总数）")

        def _read_key(payload) -> tuple:
            return (json.dumps(payload.get("source_document_key"), sort_keys=True,
                               ensure_ascii=False),
                    payload.get("read_plan_id"))

        real_by_read_key = {_read_key(e["payload"]):
                            e["payload"]["content_disposition_count"]
                            for e in real_tree_results}
        carry_checked = [e for e in reuse_result_rows
                         if _read_key(e["payload"]) in real_by_read_key]
        check(all(e["payload"]["content_disposition_count"]
                  == real_by_read_key[_read_key(e["payload"])]
                  for e in carry_checked)
              and len(carry_checked) == len(reuse_result_rows),
              "复用行结转首读的**内容处置数**（不洗成 0、不另编一个数），且每一行都能对上"
              f"它的首读行（{len(carry_checked)}/{len(reuse_result_rows)} 条复用行有首读可比）")

        not_found_pairs = [(t, res) for t in results
                           for res in packs[t].aspect_results if res.status == "not_found"]
        note(f"重算后终态: not_found={len(not_found_pairs)} "
             f"blocked={sum(1 for s in statuses.values() if s == 'blocked')} "
             f"partial={sum(1 for s in statuses.values() if s == 'partial')}")
        for topic, res in not_found_pairs:
            audit = audit_by_id.get(res.not_found_audit_id)
            check(audit is not None and audit.qualified is True
                  and bool(audit.required_source_scope)
                  and bool(audit.attempted_source_types)
                  and set(audit.required_source_scope)
                  <= set(audit.attempted_source_types)
                  and audit.budget_exhausted is False
                  and not audit.unattempted_candidate_ids
                  and bool(audit.searched_need_ids),
                  f"{topic}/{res.aspect_id}: not_found 必须附完整合格审计"
                  f"（范围可证 / 无未尝试候选 / 非预算耗尽 / 有真实 searched need）")
        for topic in results:
            for res in packs[topic].aspect_results:
                if res.status != "not_found":
                    check(res.not_found_audit_id is None,
                          f"{res.aspect_id}: 非 not_found 不得挂 not_found 审计")
        for aspect in all_aspects:
            if "external" in scope_of[aspect.aspect_id]:
                check(statuses.get(aspect.aspect_id) != "not_found",
                      f"{aspect.aspect_id}: 应有范围含 external 而本轮无真实外部轨迹"
                      f" → 不得 not_found（终态 {statuses.get(aspect.aspect_id)!r}）")

        # ================= C. §605 跨 run 幂等（同一真实 requirement） =====
        cb = reqs[SLICE_COMPANY_TOPICS[1]]
        before = len(Store.list_pack_history(results[cb.topic_id].identity))
        second = TR.run_topic_requirement(
            cb, run_context=company_ctx, dependencies=_deps(cb, company_ctx))
        after = len(Store.list_pack_history(results[cb.topic_id].identity))
        check(second.pack_id == results[cb.topic_id].pack_id,
              "同 task + 同权威内容跨 run 复现同一内容身份（§605）")
        check(results[cb.topic_id].reused is False and second.reused is True,
              "第二次运行是幂等复用（不重写、不产生新内容身份）")
        check(before == after == 1, f"Pack 历史不因重跑增长（{before}→{after}）")
        first = results[cb.topic_id]
        check(second.usage.to_dict() == first.usage.to_dict(),
              "usage 快照跨 run 恒等（只含内容用量，不含实测墙钟）")
        check(second.versions == first.versions, "版本身份跨 run 恒等")
        check(second.aspect_statuses == first.aspect_statuses,
              "逐 aspect 终态跨 run 恒等")
        check(second.gaps == first.gaps, "声明型 gap 跨 run 恒等")
        third = TR.run_topic_requirement(
            cb, run_context=company_ctx, dependencies=_deps(cb, company_ctx))
        check(third.pack_id == first.pack_id,
              "第三次运行仍复现同一身份（不是偶然相同）")

        # ================= D. 运行账本 / 身份 / 依赖门 =====================
        dirty = TR.TopicBudgetState(policy=budget)
        dirty.rounds = 1
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=company_ctx,
            dependencies=_deps(cb, company_ctx, budget_state=dirty)),
            TR.TopicRuntimeError, "复用已记账的 ledger 必须拒",
            needle="全新 TopicBudgetState")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=company_ctx,
            dependencies=_deps(cb, company_ctx,
                               budget_state=TR.TopicBudgetState(policy=other_budget))),
            TR.TopicRuntimeError, "budget_state.policy 与 run_context 不符必须拒",
            needle="budget_policy 不一致")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=company_ctx,
            dependencies=_deps(cb, company_ctx, policy=other_budget)),
            TR.TopicRuntimeError, "账本政策版本与 run_context 政策不一致必须拒",
            needle="budget_policy 不一致")

        stale_dv = dict(cb.dependency_versions)
        stale_dv["topic_runtime"] = "tr-STALE"
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, dependency_versions=stale_dv),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "过期依赖集必须拒", needle="依赖集不一致")
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, task_id="other-task"),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "requirement.task_id 不符必须拒", needle="task_id")
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, section_id="industry"),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "requirement.section_id 不符必须拒", needle="section_id")
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, report_as_of="2000-01-01"),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "report_as_of 不符必须拒", needle="report_as_of")
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, company_id="company_other"),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "公司不符必须拒", needle="company_id")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=_context(cb, run_id="m930-2-bad-1", task_id="other-task"),
            dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "run_context.task_id 不符必须拒", needle="task_id")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=_context(cb, run_id="m930-2-bad-2",
                                     document_=dataclasses.replace(
                                         document, company_id="company_other")),
            dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "run_context.document 公司不符必须拒",
            needle="document.company_id")
        expect_error(lambda: TR.run_topic_requirement(
            {"topic_id": cb.topic_id}, run_context=company_ctx,
            dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "非 TopicResearchRequirement 必须拒",
            needle="TopicResearchRequirement")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=object(), dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "非 TopicRunContext 必须拒", needle="TopicRunContext")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=company_ctx, dependencies=object()),
            TR.TopicRuntimeError, "非 TopicRuntimeDependencies 必须拒",
            needle="TopicRuntimeDependencies")
        expect_error(lambda: TR.TopicRuntimeDependencies(
            registry=reg, llm=object(),
            navigation=_navigation_for(cb)[0], information_need_builder=_NeedBuilder(),
            route_context_builder=lambda: _route_context(document.company_id),
            route_fn=_route_fn, budget_state=TR.TopicBudgetState(policy=budget),
            trace_sink=sink, store=store, payload_resolvers=(),
            source_policy_resolver=policy_resolver,
            set_completeness_verifier=scv, set_enumeration_verifier=sev,
            clock=lambda: STARTED_AT),
            TR.TopicRuntimeError, "payload_resolvers 为空必须拒（材料必须可独立复核）",
            needle="payload_resolvers")

        # ================= E. 装配门（单轴漂移，不另造身份） =================
        ghost = dataclasses.replace(cb.aspects[0],
                                    aspect_id=cb.aspects[0].aspect_id + ".ghost")
        ghost_profile = NAV.build_navigation_profile(
            (ghost,) + tuple(cb.aspects), contract_version=cb.contract_version,
            contract_fingerprint=cb.contract_fingerprint)
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=company_ctx,
            dependencies=_deps(cb, company_ctx, navigation=TR.IndexedTreeNavigation(
                index=index, profile=ghost_profile))),
            TR.TopicRuntimeError,
            "导航 profile 含 requirement 之外的 aspect（同 ID 集合被撑大）必须拒",
            needle="不一致")

        bad_ref = TS.SourcePolicyRef(
            policy_id=cb.aspects[0].source_policy_ref.policy_id,
            policy_version=cb.aspects[0].source_policy_ref.policy_version,
            content_fingerprint="0" * 64)
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, aspects=tuple(
                dataclasses.replace(a, source_policy_ref=bad_ref)
                for a in cb.aspects)),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError,
            "SourcePolicy 指纹与该冻结资产不符时无法独立解析必须拒",
            needle="无法从独立冻结来源解析")
        other_ref = TS.SourcePolicyRef(
            policy_id="other_source_policy", policy_version="v1",
            content_fingerprint=_sha("other-policy"))
        expect_error(lambda: TR.run_topic_requirement(
            dataclasses.replace(cb, aspects=(
                cb.aspects[0],
                dataclasses.replace(cb.aspects[1], source_policy_ref=other_ref),
            ) + tuple(cb.aspects[2:])),
            run_context=company_ctx, dependencies=_deps(cb, company_ctx)),
            TR.TopicRuntimeError, "一个 Pack 绑多个不同 SourcePolicyRef 必须拒",
            needle="SourcePolicyRef")
        expect_error(lambda: TR.run_topic_requirement(
            cb, run_context=company_ctx,
            dependencies=_deps(cb, company_ctx, navigation=_WrongNavigation())),
            TR.TopicRuntimeError, "导航提供者取不到本 aspect 必须拒",
            needle="不在导航 profile")

        expect_error(lambda: TR.CombinedPayloadResolver(()), TR.TopicRuntimeError,
                     "空组合解析器必须拒")
        sample_payload = packs[cb.topic_id].materials[0].payload_ref
        combined = TR.CombinedPayloadResolver((session.resolver, _NullResolver()))
        check(combined.resolve(sample_payload) is not None,
              "组合解析器单命中 → 返回该结果")
        expect_error(lambda: TR.CombinedPayloadResolver(
            (session.resolver, session.resolver)).resolve(sample_payload),
            TR.TopicRuntimeError, "同一 payload 被两个来源认领必须拒", needle="同时认领")
        check(TR.CombinedPayloadResolver((_NullResolver(),)).resolve(
            sample_payload) is None,
            "组合解析器 0 命中 → None（dangling 由上游 fail-closed）")

        # ================= F. §四 身份闭合：反例 + 行业正例 =================
        industry_req = reqs[SLICE_INDUSTRY_TOPIC]
        company_navigation = _navigation_for(cb)[0]
        expect_error(lambda: TR.run_topic_requirement(
            industry_req, run_context=industry_ctx,
            dependencies=_deps(industry_req, industry_ctx,
                               navigation=company_navigation)),
            TR.TopicRuntimeError,
            "行业 requirement 复用公司 topic 的导航 profile 必须拒（旧正例已改反例）",
            needle="不一致")

        drifted_topic_aspects = tuple(
            dataclasses.replace(a, topic_id=cb.topic_id)
            for a in industry_req.aspects)
        drifted_profile = NAV.build_navigation_profile(
            drifted_topic_aspects, contract_version=industry_req.contract_version,
            contract_fingerprint=industry_req.contract_fingerprint)
        expect_error(lambda: TR.run_topic_requirement(
            industry_req, run_context=industry_ctx,
            dependencies=_deps(industry_req, industry_ctx,
                               navigation=TR.IndexedTreeNavigation(
                                   index=index, profile=drifted_profile))),
            TR.TopicRuntimeError,
            "同 aspect_id 但 topic_id 冒充的导航条目必须拒（身份漂移）",
            needle="身份漂移")

        i_phase = IW.run_backbone_topic_phase(
            industry_task, requirements=(industry_req,),
            run_context=industry_ctx,
            dependencies_of=lambda req, ctx: _deps(req, ctx), store=reader)
        check(i_phase.section_id == "industry"
              and i_phase.results[0].identity.topic_id == SLICE_INDUSTRY_TOPIC
              and i_phase.pack_set.pack_for(SLICE_INDUSTRY_TOPIC).pack_id
              == i_phase.results[0].pack_id,
              "行业 section 用**自己真实的**导航 profile 走同一 runtime 并通过读门")

        # 缺口不得被吞：读门读不到 current（替身按协议如实回 no_current）时，phase 必须
        # **抛错**而不是返回半套 Pack。用替身是因为真实库上本 runtime 刚提交过 current。
        expect_error(lambda: IW.run_backbone_topic_phase(
            industry_task, requirements=(industry_req,),
            run_context=industry_ctx,
            dependencies_of=lambda req, ctx: _deps(req, ctx),
            store=_NoCurrentStore(session.resolver)),
            PSet.PackSetBlocked, "缺 current 的 topic 必须让 phase fail-closed（不吞缺口）",
            needle="no_current_pack")

        # ================= G. 兜底重切不得替这一栏造读集（`fba-1`） ==========
        # 两侧都要有现场读数，所以用**同一个**研究替身（只引用真实父 Evidence、不闭环
        # ⇒ 只可能走 §七 有界 Evidence 重切）跑两遍：一遍把真实导航决策换成读集为空的
        # fallback 终态，一遍用真实导航（读集开着）。判据是同一件事的两面：
        #   - 读集为空 ⇒ 重切材料**不得**成为这一栏的栏目材料（typed gap + 材料仍在）；
        #   - 读集开着 ⇒ 重切材料照旧登记（不得因为这条规则把真读到的栏目也一起挡住）。
        real_nav, _ = _navigation_for(cb)
        # 三种 fallback 终态逐条轮转（读集为空的三种理由在 navigation.py 里互不相同，
        # 规则只看读集 ⇒ 三者必须行为一致：都被挡下、都留 typed gap）。
        fallback_reasons = ("low_confidence", "no_anchored_read_root",
                            "explicit_cross_reference")
        reason_by_aspect = {a.aspect_id: fallback_reasons[i % len(fallback_reasons)]
                            for i, a in enumerate(cb.aspects)}
        blank_nav = _ReadSetOverrideNavigation(
            real_nav, {a.aspect_id: () for a in cb.aspects},
            fallback_reason_by_aspect=reason_by_aspect)
        cite_only_ids = tuple(sorted(
            {m.authority_assessment.evidence_id
             for m in packs[cb.topic_id].materials}))[:2]
        check(bool(cite_only_ids),
              "本切片有真实父 Evidence ID 可用于逼出重切（否则本节是空转）")

        def _read_single_node(aspect, node):
            """经**正式树工具**只读该 aspect 读集里的一个 node（正例现场的真实读数）。"""
            call_id = f"fba1-read1-{aspect.aspect_id}-{node}"
            call = C.ToolCall(
                call_id=call_id, tool_name=TT.TREE_INSPECT_TOOL_NAME,
                arguments={**document.to_dict(), "need_id": f"fba1-{aspect.aspect_id}",
                           "aspect_id": aspect.aspect_id, "topic_id": cb.topic_id,
                           "node_ids": [str(node)],
                           "max_spans": budget.tree_max_spans_per_aspect,
                           "max_chars_per_span": budget.tree_max_chars_per_span},
                idempotency_key=call_id, need_id=f"fba1-{aspect.aspect_id}",
                batch_id=cb.topic_id)
            return reg.execute(call, route="DIRECT_EVIDENCE", run_id="fba1-read1")

        # 正例的真实输入：找一个「读集开着、但读不出任何候选材料」的现场。收窄后的读集
        # 因此是该 aspect **真实读集的子集**（下面逐条断言这条关系），不是编出来的 node。
        affirm_pick = None
        for aspect in cb.aspects:
            decision = real_nav.candidates(aspect, requirement=cb)
            nodes = tuple(str(n) for n in real_nav.node_ids_for(decision))
            if not nodes:
                continue
            for node in nodes:
                read = _read_single_node(aspect, node)
                if not ((read.data or {}).get("candidates") or ()):
                    affirm_pick = (aspect.aspect_id, node, nodes, read.status)
                    break
            if affirm_pick:
                break
        check(affirm_pick is not None and affirm_pick[1] in affirm_pick[2],
              "真实切片里存在『读集开着、但其中某个 node 读不出任何候选材料』的现场"
              f"（正例的真实输入）；实际：{affirm_pick}")
        affirm_aspect_id, affirm_node, affirm_read_set, affirm_status = affirm_pick
        note(f"fba-1 正例输入：aspect={affirm_aspect_id} 真实读集 {len(affirm_read_set)} 个 node，"
             f"收窄到 {affirm_node}（真读 {affirm_status}、零候选）")

        def _recut_rows(events, policy_open=None):
            rows = [e["payload"] for e in events
                    if e.get("type") == "EVIDENCE_FALLBACK_RECUT"
                    and e["payload"].get("attribution_policy")
                    == TR.EVIDENCE_FALLBACK_ATTRIBUTION_VERSION]
            if policy_open is None:
                return rows
            return [r for r in rows if bool(r.get("read_set_open")) is policy_open]

        def _drive(navigation):
            # 两个 run 共用一个 sink ⇒ 按**本次 run 新增的**事件切片读数。只按载荷标志
            # 过滤会把两个 run 的重切混进同一个桶，读出来的"对照"是两个 run 相加的假数。
            # Pack 同理：两个 run 共用同一份 identity（同一 run_id、同一 topic），后跑的
            # 会把 current pack 顶掉，所以**跑完立刻**取，不等到两个 run 都跑完再取——
            # 否则读到的是后一个 run 的包，判据会张冠李戴。
            mark = len(sink.events)
            run = TR.run_topic_requirement(
                cb, run_context=company_ctx,
                dependencies=_deps(cb, company_ctx, navigation=navigation,
                                   research=_CiteWithoutAdopting(
                                       evidence_ids=cite_only_ids)))
            return run, tuple(sink.events[mark:]), store.load_current(run.identity)

        def _unattributed_gaps(run):
            return [g for g in run.gaps
                    if g.get("reason") == TR.EVIDENCE_FALLBACK_UNATTRIBUTED_REASON]

        closed_run, closed_events, closed_pack = _drive(blank_nav)
        open_run, open_events, open_pack = _drive(real_nav)
        note(f"fba-1 对照：读集为空的重切 —— 受控 run "
             f"{len(_recut_rows(closed_events, False))} 次 / 真实导航 run "
             f"{len(_recut_rows(open_events, False))} 次 / 读集开着的重切 "
             f"{len(_recut_rows(open_events, True))} 次")
        open_called = {str(e["payload"]["aspect_id"]) for e in open_events
                       if e.get("type") == "TREE_TOOL_RESULT"}
        note(f"fba-1 对照：真实导航下有树调用的 aspect {len(open_called)} 个，"
             f"其中 `material_ids` 为空的 "
             f"{sorted(a for a in open_called if not [r for r in open_pack.aspect_results if r.aspect_id == a and r.material_ids])[:3]}")

        # (G1) 读集为空一侧：重切真的发生过（否则下面的检查全是空转）。
        closed_recuts = _recut_rows(closed_events)
        check(bool(closed_recuts),
              "读集为空时**重切真的被触发过**（本节的判据只在真有现场时才有意义）")
        check(all(r.get("read_set_open") is False
                  and r.get("attributed_material_count") == 0
                  and int(r.get("recut_material_count") or 0) >= 1
                  for r in closed_recuts),
              "读集为空的重切事件：交出过材料，但没有一份被登记为本 aspect 的栏目材料"
              "（`attributed_material_count` 恒为 0）")
        covered_reasons = {str(r.get("trigger")) for r in closed_recuts}
        check(set(fallback_reasons) <= covered_reasons,
              "读集为空的**三种** fallback 终态都被真实驱动过（规则只看读集，不由理由分档）："
              f"实际 {sorted(covered_reasons)}")
        unattributed = _unattributed_gaps(closed_run)
        check(bool(unattributed),
              "读集为空时重切材料未被归属，必须留下 typed gap（不得静默不归属）")
        check(all(g.get("attribution_policy") == TR.EVIDENCE_FALLBACK_ATTRIBUTION_VERSION
                  and g.get("read_node_count") == 0
                  and int(g.get("recut_material_count") or 0) >= 1
                  for g in unattributed),
              "该 gap 自带 typed 维度：策略版本、读集节点数为 0、重切材料数如实计数")
        closed_by_aspect: dict[str, dict] = {}
        for row in unattributed:
            closed_by_aspect.setdefault(str(row.get("aspect_id")), row)
        check(len(closed_by_aspect) == len(unattributed),
              "同一 aspect 的同一条 gap 不重复登记")
        recut_ids = {mid for r in closed_recuts
                     for mid in (r.get("recut_material_ids") or ())}
        check(bool(recut_ids), "受控 run 的重切事件如实记下了交出的材料 id")
        blocked_offenders = [
            (res.aspect_id, tuple(res.material_ids), tuple(res.supported_fact_ids))
            for res in closed_pack.aspect_results
            if res.aspect_id in closed_by_aspect
            and (res.material_ids or res.supported_fact_ids)]
        check(not blocked_offenders,
              "被挡下的 aspect：`material_ids` 与 `supported_fact_ids` 都为空"
              "（兜底材料既不成栏目材料，也不得据此闭环出事实）；实际："
              f"{blocked_offenders[:3]}")
        # 材料本身不丢：仍在 Pack 全局材料集与四轴台账里（"这一次调用交出了什么"照旧可查）。
        pack_material_ids = {m.material_id for m in closed_pack.materials}
        check(all(mid in pack_material_ids for mid in closed_recuts[0]["recut_material_ids"]),
              "重切材料仍进 Pack 全局材料集（可回查、可被支撑边精确引用）")
        check(all(any(d.material_id == mid for d in closed_pack.material_dispositions)
                  for mid in closed_recuts[0]["recut_material_ids"]),
              "重切材料仍逐份带 `ResearchMaterialDisposition`（身份没被这条规则动过）")
        # 四臂台账这一面也必须同步不认领：臂 A 的含义是"这一份**按标题树真的读到**了这些
        # 材料"（它要求本份有真实调用痕迹），兜底重切交出的材料**不得**借臂 A 立起来——
        # 否则「兜底读到的」会在台账里被读成「按树读到的」，正是本批要堵的那条错读。
        anchor_axes = SM.source_key_axes(company_ctx.sources.current_state_key())
        arm_rows = [o for o in closed_pack.source_aspect_outcomes
                    if o.aspect_id in closed_by_aspect
                    and SM.source_key_axes(o.source_document_key) == anchor_axes]
        note(f"fba-1 对照：被挡下的 aspect 在本份台账上的落臂 "
             f"{sorted({o.arm for o in arm_rows})}")
        check(bool(arm_rows) and all(
            not (o.arm == "A" and set(o.material_ids) & recut_ids)
            for o in arm_rows),
            "被挡下的 aspect 不得在四臂台账里凭兜底材料成立臂 A"
            "（臂 A = 本份按标题树真的读到了这些材料；这一次是兜底重切交出的）")
        check(all(o.arm != "A" for o in arm_rows),
              "读集为空 ⇒ 本份没有树调用痕迹，落臂也不得是 A；实际："
              f"{sorted({o.arm for o in arm_rows})}")

        # (G2) 真实导航一侧：判据是**逐条重切事件**的，不是"整跑一次"的。真实导航下本来
        # 就会有一部分 aspect 真的落到 fallback（读集为空），它们的重切同样不得归属；
        # 读集开着的那些才归属。两侧各按自己的读集读数，不互相顶替、也不整跑一刀切。
        open_recuts = _recut_rows(open_events)
        check(all(r.get("attributed_material_count")
                  == (int(r.get("recut_material_count") or 0)
                      if r.get("read_set_open") else 0)
                  for r in closed_recuts + open_recuts),
              "「交出几份 / 归属几份」逐条对账：读集开着才逐份归属，读集为空恒为 0")
        check({str(r.get("aspect_id")) for r in _recut_rows(open_events)
               if not r.get("read_set_open")}
              <= {str(g.get("aspect_id")) for g in _unattributed_gaps(open_run)},
              "真实导航里落到 fallback 的重切，同样逐条留下 typed gap"
              "（这条规则不是只对受控夹具生效）")
        # 不误伤真读到的栏目：真实导航下**有树调用**的 aspect，其栏目材料照旧在
        # （`fba-1` 只挡"读集为空时的兜底归属"，不碰树读出来的材料）。
        check(bool(open_called) and all(
            any(r.aspect_id == a and r.material_ids
                for r in open_pack.aspect_results) for a in open_called),
            "真实导航下有树调用的 aspect 照旧持有栏目材料（不误伤真读到的栏目）；实际为空者："
            f"{sorted(a for a in open_called if not [r for r in open_pack.aspect_results if r.aspect_id == a and r.material_ids])[:3]}")

        # (G3) 正例：读集**开着**、这一次读却没读出材料（`tree_no_material`），此时重切交出
        # 的材料**照旧登记**为本 aspect 的栏目材料——`fba-1` 只挡读集为空的那一侧，不得
        # 把真开着的读集也一并挡掉。两侧合起来才是这条规则的完整判据。
        affirm_nav = _ReadSetOverrideNavigation(
            real_nav, {affirm_aspect_id: (affirm_node,)})
        affirm_run, affirm_events, affirm_pack = _drive(affirm_nav)
        affirm_rows = [r for r in _recut_rows(affirm_events)
                       if str(r.get("aspect_id")) == affirm_aspect_id]
        check(bool(affirm_rows) and all(
            r.get("read_set_open") is True
            and int(r.get("attributed_material_count") or 0)
            == int(r.get("recut_material_count") or 0) >= 1
            for r in affirm_rows),
            "正例：读集开着时，重切交出的材料逐份登记为本 aspect 的栏目材料；实际："
            f"{affirm_rows[:1]}")
        affirm_recut_ids = {mid for r in affirm_rows
                            for mid in (r.get("recut_material_ids") or ())}
        affirm_result = next((r for r in affirm_pack.aspect_results
                              if r.aspect_id == affirm_aspect_id), None)
        check(affirm_result is not None and bool(affirm_recut_ids)
              and affirm_recut_ids <= set(affirm_result.material_ids),
              "正例：这些材料真的进了 `material_ids`（读集开着的栏目不被这条规则误伤）；实际："
              f"{affirm_result.material_ids if affirm_result else None}")
        check(not [g for g in affirm_run.gaps
                   if g.get("reason") == TR.EVIDENCE_FALLBACK_UNATTRIBUTED_REASON
                   and str(g.get("aspect_id")) == affirm_aspect_id],
              "正例：读集开着的 aspect 不得留下「重切材料未被归属」的 gap")
        # 同一跑里读集仍为空的其他 aspect 照旧被挡（规则没有因为正例而整体放开）。
        affirm_closed = [r for r in _recut_rows(affirm_events, False)]
        check(bool(affirm_closed) and all(
            str(g.get("aspect_id")) in {str(r.get("aspect_id")) for r in affirm_closed}
            for g in _unattributed_gaps(affirm_run)),
            "同一跑里读集为空的重切照旧留下 typed gap（正例不放松另一侧）")

        # ============= H. 初次必读顺序 / 同读集复用 / 栏目级原因（M930-3 本批）===========
        # 本批修的是**顺序**：`retrieval_required` 的 `(栏目, 来源)` 的第一次读取是「该查」
        # 的最基本义务，而可选补件（有界重切 / focused follow-up）与它共用同一个 topic 工具
        # 账本——补件先把额度吃光，尾部栏目连第一次读取都发不出去（真实读数：company_business
        # 54/54 用尽后尾部栏目全无树结果，company_litigation 55/54 越顶）。三个结构量已在
        # `harness/topic_runtime.py` 版本化落地；下面逐条给正反两侧的**真实**读数。
        # `rsr-2`：复用交回的必须是**首读的完整读数**——`stop_reason` 随材料一起移交，且
        # `complete=False` 时按同一张对映表在复用侧补一条 typed gap（`rsr-1` 只交材料与计数，
        # 一次没读完的读数复用后会只剩 `complete=False` 而无因，容易被当成「读完」）。
        check(TR.MANDATORY_FIRST_READ_RULE_VERSION == "mandatory-first-read/1"
              and TR.FOCUSED_NEED_RESERVE_RULE_VERSION == "fnr-1"
              and TR.READ_SET_REUSE_RULE_VERSION == "rsr-2",
              "初次必读 / 事前预留 / 同读集复用三条规则各自版本化（规则一变即改版本号）")
        check(len(set(TR.UNMET_COLUMN_REASONS)) == len(TR.UNMET_COLUMN_REASONS)
              and "column_unmet" in TR.RUNTIME_GAP_REASONS
              and "column_unmet" not in TR.UNMET_COLUMN_REASONS,
              "栏目级原因闭集无重复；顶层声明原因 `column_unmet` 已登记，且与那五条**不是**"
              "同一身份（前者是包装，后者是原因）")

        # (H0) 结构上界：在**真实**公司文档集与**真实** Contract aspect 上复算「初次必读几次」。
        # 多成员源集用库里该公司的**真实** current 文档构造（四轴身份逐条取自库，不编造）。
        extra_keys = []
        for _rec in estore.list_documents_ro(evidence_db.resolve(), company):
            if _rec.document_id == document.document_id or _rec.status != "current":
                continue
            _sv = estore.current_evidence_set_ro(
                evidence_db.resolve(), company, _rec.document_id, _rec.document_version)
            if not _sv:
                continue
            extra_keys.append(SM.SourceDocumentKey(
                company_id=company, document_id=_rec.document_id,
                document_version=_rec.document_version, evidence_set_version=_sv))
        check(bool(extra_keys),
              "真实 Evidence 库里该公司确有多份 current 文档（多成员源集不是造出来的），"
              f"实际额外成员 {[k.document_id for k in extra_keys]}")

        def _multi_source_set(role):
            members = [(company_ctx.sources.members[0][0], "current_state_source")]
            members += [(k, role) for k in extra_keys]
            return TS.DocumentSourceSet(members=tuple(members))

        def _responsibility(sources):
            return tuple(
                row for aspect in cb.aspects
                for row in TR.derive_aspect_source_responsibility(
                    aspect=aspect, sources=sources,
                    current_state=SM.CURRENT_STATE_RESOLVED))

        def _bound_for(sources, *, multi=True, skip=frozenset()):
            rows = _responsibility(sources)
            return TR.mandatory_dispatch_counts(
                cb.aspects, rows, anchor=sources.current_state_key(),
                multi_source_navigation=multi, skip_aspect_ids=skip), rows

        multi_sources = _multi_source_set("topic_participating_source")
        counts, resp_rows = _bound_for(multi_sources)
        # 独立复算：直接对每条 aspect 调 `_dispatch_source_keys`（结构量的定义式），
        # 不走 `mandatory_dispatch_counts` 的求和路径——两条式子若漂移，本断言立刻红。
        recomputed = {
            a.aspect_id: len(TR._dispatch_source_keys(
                a.aspect_id, resp_rows, anchor=multi_sources.current_state_key(),
                multi_source_navigation=True))
            for a in cb.aspects}
        check(counts == recomputed and sum(counts.values()) > 0,
              "逐 aspect 派发基数 == 直接复算 `_dispatch_source_keys` 的长度"
              "（同一条式子，不是另写一遍）")
        check(all(v >= 1 for v in counts.values()),
              "锚**无条件**派发：每条 aspect 的派发基数至少为 1（单文档退化时逐字等价）")
        check(sum(counts.values()) > len(cb.aspects),
              "多成员源集下派发基数**严格大于** aspect 数（多出来的正是逐份必读的部分）；"
              f"实际合计 {sum(counts.values())} / aspect 数 {len(cb.aspects)}")
        note(f"H0 初次必读结构上界：多成员源集 {len(multi_sources.members)} 份 / "
             f"逐 aspect 派发基数 {counts} / 合计 {sum(counts.values())}"
             f"（aspect 数 {len(cb.aspects)}）；旧口径上限 3×aspect = {3 * len(cb.aspects)}")
        # 不变性：换来源角色不改任何一位（结构量只由锚与 `retrieval_required` 决定）。
        alt_counts, _ = _bound_for(_multi_source_set("history_and_conflict_source"))
        check(alt_counts == counts,
              "来源角色不进入派发基数（历史/参与两种角色下逐 aspect 数字完全相同）")
        # 反例一：导航只有单文档能力 ⇒ 一律只派发锚，基数退化为 1×aspect 数。
        single_counts, _ = _bound_for(multi_sources, multi=False)
        check(set(single_counts.values()) == {1},
              "导航只有单文档能力时只派发锚（被责任判定必须检索却没派发的成员落成臂 C2"
              "`not_dispatched`，不消失、也不被改写成「无需检索」）")
        # 反例二：本轮直接继承的 aspect 不派发、也不占预留。
        first_id = cb.aspects[0].aspect_id
        partial = TR.mandatory_dispatch_bound(
            cb.aspects, resp_rows, anchor=multi_sources.current_state_key(),
            multi_source_navigation=True, skip_aspect_ids=frozenset({first_id}))
        check(partial == sum(counts.values()) - counts[first_id],
              "只继承一部分时预留精确减去那一部分（不是整片放过、也不是整片扣掉）")
        check(TR.mandatory_dispatch_bound(
            cb.aspects, resp_rows, anchor=multi_sources.current_state_key(),
            multi_source_navigation=True,
            skip_aspect_ids=frozenset(a.aspect_id for a in cb.aspects)) == 0,
              "整轮继承时预留为 0（继承不是「又读了一遍」，虚占额度会挤掉可选补件）")

        # (H1) 上限必须盖得住结构上界：盖不住即 fail-closed。现场用本模块的单文档运行源集，
        # 其结构上界 = aspect 数（导航只有单文档能力 ⇒ 只派发锚）。
        real_nav_h, _ = _navigation_for(cb)
        live_counts = TR.mandatory_dispatch_counts(
            cb.aspects, _responsibility(company_ctx.sources),
            anchor=company_ctx.sources.current_state_key(),
            multi_source_navigation=False)
        live_bound = int(sum(live_counts.values()))
        check(live_bound == len(cb.aspects) >= 2,
              f"本模块运行源集的结构上界 = aspect 数（{live_bound} == {len(cb.aspects)}），"
              "且不少于 2（下面的挡下/放行对照才有现场）")
        tight = dataclasses.replace(budget, max_tool_calls_per_topic=live_bound)
        below = dataclasses.replace(budget, max_tool_calls_per_topic=live_bound - 1)
        cite_ids = tuple(sorted({m.authority_assessment.evidence_id
                                 for m in packs[cb.topic_id].materials}))[:2]
        check(bool(cite_ids),
              "本切片有真实父 Evidence ID 可用于逼出有界重切（否则可选调用一侧是空转）")
        ctx_below = _context(cb, run_id="m9303-h1-below", policy=below)
        expect_error(
            lambda: TR.run_topic_requirement(
                cb, run_context=ctx_below,
                dependencies=_deps(cb, ctx_below, policy=below,
                                   research=_CiteWithoutAdopting(evidence_ids=cite_ids))),
            TR.TopicRuntimeError,
            "上限低于结构上界即 fail-closed（不发出任何调用，不把注定失败的运行伪装成可运行）",
            needle="初次必读")

        # 正例：上限**恰等于**结构上界 ⇒ 运行照旧完成，可选余量为 0，必读一个不少。
        ctx_tight = _context(cb, run_id="m9303-h1-tight", policy=tight)
        tight_ledger = TR.TopicBudgetState(policy=tight)
        mark = len(sink.events)
        tight_run = TR.run_topic_requirement(
            cb, run_context=ctx_tight,
            dependencies=_deps(cb, ctx_tight, policy=tight, budget_state=tight_ledger,
                               research=_CiteWithoutAdopting(evidence_ids=cite_ids)))
        tight_events = tuple(sink.events[mark:])
        tight_payloads = [e["payload"] for e in tight_events]
        budget_event = next((e["payload"] for e in tight_events
                             if e["type"] == "MANDATORY_FIRST_READ_BUDGET"), None)
        check(budget_event is not None
              and budget_event["mandatory_bound"] == live_bound
              and budget_event["optional_allowance"] == 0
              and budget_event["multi_source_navigation"] is False
              and budget_event["rule_version"] == TR.MANDATORY_FIRST_READ_RULE_VERSION
              and budget_event["per_aspect_dispatch"] == dict(live_counts),
              "上限恰等于结构上界时**静态**可选余量为 0，而运行照旧完成（上限盖得住必读）")
        check("static_upper_bound" in str(budget_event.get("optional_allowance_basis", ""))
              and budget_event.get("reserve_rule_version")
              == TR.FOCUSED_NEED_RESERVE_RULE_VERSION,
              "那个 0 在产物里写明是**静态上界**（上限 − 必读结构上界），并写明现场余量还取决于"
              "必读的真实花费——否则读回的人会把这个 0 读成「补件一次都没发生」")
        tight_tree = [e["payload"] for e in tight_events if e["type"] == "TREE_TOOL_RESULT"]
        check(tight_ledger.tool_calls <= tight.max_tool_calls_per_topic
              and all(int(p.get("tool_calls") or 0) <= tight.max_tool_calls_per_topic
                      for p in tight_tree),
              f"全程工具计数不越顶（{tight_ledger.tool_calls} ≤ "
              f"{tight.max_tool_calls_per_topic}）——55/54 那类越顶不得再现")
        check(len([p for p in tight_tree if p.get("status") != "REUSED_READ_SET"])
              <= live_bound,
              "真实树调用数不超过结构上界（必读是无条件的那一批，复用不额外发调用）")
        # 本批真正要保的性质**不是**「余量 0 ⇒ 补件一次都不发生」（那是把静态上界当成现场
        # 事实），而是：**必读一条都不落**。现场余量来自「必读的真实花费」——同读集复用与
        # 「导航无候选」都不花调用，未花掉的必读份额会真实地变成可选余量；只要每一次可选
        # 调用都过 `can_afford_optional`，预留份额就不会被补件吃掉，尾部栏目不会因为补件而
        # 失去自己的第一次读取。
        tight_recuts = [p for p in tight_payloads
                        if p.get("attribution_policy")
                        == TR.EVIDENCE_FALLBACK_ATTRIBUTION_VERSION]
        tree_concluded = {str(e["payload"].get("aspect_id")) for e in tight_events
                          if e["type"] == "TREE_TOOL_RESULT"}
        typed_no_dispatch = {str(g.get("aspect_id")) for g in tight_run.gaps
                             if g.get("reason") == "tree_structure_unavailable"}
        starved = [a.aspect_id for a in cb.aspects
                   if a.aspect_id not in tree_concluded | typed_no_dispatch]
        mandatory_blocked = [g for g in tight_run.gaps
                             if g.get("reason") == "tree_budget_exhausted"]
        check(not starved and not mandatory_blocked
              and tight_ledger.reserved_tool_calls == 0,
              "上限恰等于结构上界时**没有任何栏目被饿死**：每条 aspect 要么拿到树工具结论"
              "（真读或复用）、要么有一条准确的「导航无候选」typed 原因；没有一条必读被预算"
              f"截断；收尾时预留归零。实际被饿死 {starved}，必读被截断 "
              f"{len(mandatory_blocked)} 条")
        note(f"H1 现场：上限 {tight.max_tool_calls_per_topic} == 结构上界，工具实际花掉 "
             f"{tight_ledger.tool_calls} 次（必读真读 "
             f"{len([p for p in tight_tree if p.get('status') != 'REUSED_READ_SET'])} 次 / 复用 "
             f"{len([p for p in tight_tree if p.get('status') == 'REUSED_READ_SET'])} 次 / "
             f"导航无候选 {len(typed_no_dispatch)} 条），未花掉的必读份额变成现场可选余量后"
             f"发出有界重切 {len(tight_recuts)} 次；不越顶、预留未被补件吃掉")
        # 被预算挡下的**可选**调用（补件那一档）必须留 typed 原因，且与必读那一档分开计数。
        tight_blocked = [g for g in tight_run.gaps
                         if g.get("unmet_column_reason") == "budget_blocked_dispatch"]
        check(bool(tight_blocked) and all(
            g.get("reason") in TR.RUNTIME_GAP_REASONS for g in tight_blocked),
              "被预算挡下的补件留 typed 栏目级原因 `budget_blocked_dispatch`"
              f"（「本轮没查」≠「查过没有」）；实际 {len(tight_blocked)} 条、"
              "顶层原因一律取自 `RUNTIME_GAP_REASONS`")

        # 放行一侧：同一份替身，只把可选余量抬高 4 次。两侧合起来说明「挡下补件的是预算的
        # **真实余量**，不是规则本身禁止补件」，也说明补件确实会随余量变化（不是常开常关）。
        roomy = dataclasses.replace(budget, max_tool_calls_per_topic=live_bound + 4)
        ctx_roomy = _context(cb, run_id="m9303-h1-roomy", policy=roomy)
        roomy_ledger = TR.TopicBudgetState(policy=roomy)
        mark = len(sink.events)
        roomy_run = TR.run_topic_requirement(
            cb, run_context=ctx_roomy,
            dependencies=_deps(cb, ctx_roomy, policy=roomy, budget_state=roomy_ledger,
                               research=_CiteWithoutAdopting(evidence_ids=cite_ids)))
        roomy_events = tuple(sink.events[mark:])
        roomy_recuts = [e["payload"] for e in roomy_events
                        if e["type"] == "EVIDENCE_FALLBACK_RECUT"
                        and e["payload"].get("attribution_policy")
                        == TR.EVIDENCE_FALLBACK_ATTRIBUTION_VERSION]
        roomy_blocked = [g for g in roomy_run.gaps
                         if g.get("unmet_column_reason") == "budget_blocked_dispatch"]
        check(roomy_ledger.tool_calls <= roomy.max_tool_calls_per_topic
              and (len(roomy_recuts) >= len(tight_recuts)
                   or len(roomy_blocked) <= len(tight_blocked)),
              "余量抬高 4 次后补件过得**更宽**（重切不多于放宽前，被挡下的不多于放宽前），"
              "且两侧都不越顶（`fnr-1` 按真实可能消耗事前预留）"
              f"；tight 重切 {len(tight_recuts)} 次 / 挡下 {len(tight_blocked)} 条 vs "
              f"roomy 重切 {len(roomy_recuts)} 次 / 挡下 {len(roomy_blocked)} 条，"
              f"实际花掉 {roomy_ledger.tool_calls} ≤ {roomy.max_tool_calls_per_topic}")

        # (H2) 事前预留口径（`fnr-1`）：两个数都从既有常量推出，不新定数字。
        reserve = TR.focused_need_reserve(tight)
        inner_budget = tight.need_budget().build()
        check(reserve.tool_calls == inner_budget.max_tool_calls
              == max(2, tight.max_need_rounds_per_aspect * 2)
              and reserve.llm_calls == tight.max_need_rounds_per_aspect + 3
              and reserve.rule_version == "fnr-1",
              "单次 need 的事前预留 = 内层真实工具上界 +（轮数 + 收敛 + answer + entailment）")
        check(reserve.to_dict() == {"tool_calls": reserve.tool_calls,
                                    "llm_calls": reserve.llm_calls,
                                    "rule_version": "fnr-1"},
              "预留对象可序列化进 gap/trace（读回的人能核「按什么预留的」）")
        expect_error(lambda: TR.focused_need_reserve(_ZeroToolNeed()),
                     TR.TopicRuntimeError,
                     "内层预算没有可用工具上界时拒绝以 0 预留（0 预留 = follow-up 免费）",
                     needle="max_tool_calls")

        # (H3) 可选调用门：预留在场时可选补件不得动用它——「尾部栏目连第一次读取都发不出去」
        # 的负例；旧口径（只看已花掉的数）在同一现场会放行，故必须分开断言。
        # 现场数字就取 54/54 那一处：上限 == 结构上界，已发出 `bound - 1` 次真实调用，
        # 还剩**最后一条** aspect 的必读没有发起（它的份额仍在预留里）。
        tail_ledger = TR.TopicBudgetState(policy=tight)
        tail_ledger.tool_calls = live_bound - 1
        tail_ledger.reserve_tool_calls(1)
        check(tail_ledger.can_afford(tool_calls=1) is True
              and tail_ledger.can_afford_optional(tool_calls=1) is False,
              "尾部栏目的现场：旧门放行最后一次可选补件，新门拿「还欠 1 次初次必读」把它挡下"
              f"（上限 {live_bound} / 已花 {live_bound - 1} / 预留 1）——"
              "补件因此吃不到尾部栏目唯一的那次必读")
        tail_ledger.tool_calls += 1          # 尾部那条 aspect 的必读如实发出去
        tail_ledger.release_tool_calls(1)    # 它的窗口打开，预留归还
        check(tail_ledger.reserved_tool_calls == 0
              and tail_ledger.can_afford(tool_calls=1) is False
              and tail_ledger.can_afford_optional(tool_calls=1) is False,
              "必读发完、上限用满后，可选补件仍拿不到任何**超出上限**的额度"
              "（归还只发生在本 aspect 窗口打开时，且归还≠放大上限）")
        probe_policy = dataclasses.replace(
            budget, max_tool_calls_per_topic=live_bound + 1)
        probe_ledger = TR.TopicBudgetState(policy=probe_policy)
        probe_ledger.reserve_tool_calls(live_bound)
        check(probe_ledger.can_afford(tool_calls=2) is True
              and probe_ledger.can_afford_optional(tool_calls=2) is False
              and probe_ledger.can_afford_optional(tool_calls=1) is True,
              "门按**本次要花多少**判：余量 1 ⇒ 1 次补件放行、2 次挡下"
              "（门不是「一律禁止补件」）")
        probe_ledger.release_tool_calls(live_bound)
        check(probe_ledger.reserved_tool_calls == 0
              and probe_ledger.can_afford_optional(tool_calls=1) is True,
              "必读窗口打开后预留归还，可选补件重新可用（归还只发生在本 aspect 窗口打开时）")
        expect_error(lambda: probe_ledger.reserve_tool_calls(-1),
                     TR.TopicRuntimeError, "预留数必须非负（负数预留等于凭空放大额度）")
        snap_ledger = TR.TopicBudgetState(policy=budget)
        snap_ledger.reserve_tool_calls(5)
        snap_metrics = {e.metric: e.value
                        for e in snap_ledger.to_usage_snapshot().cumulative_usage}
        check(snap_ledger.reserved_tool_calls == 5
              and snap_metrics.get("tool_calls") == 0
              and "reserved_tool_calls" not in snap_metrics,
              "预留只进运行账本、不进 Pack 用量快照（否则同一内容跨 run 的 pack_id 会漂）")

        # (H4) 同读集复用（`rsr-1`）：同一份来源 + 同一个读集只真读一次，后来者复用**同一批
        # 材料对象**（一份原文一份材料身份）；复用不虚增调用，也不在痕迹里造一次没发生过的调用。
        # 纯函数面先钉死：键由四轴身份与读集本身构成，不含任何结果字段。
        anchor_key = company_ctx.sources.current_state_key()
        base_key = TR.read_set_reuse_key(anchor_key, ("n1", "n2"))
        check(base_key == TR.read_set_reuse_key(anchor_key, ("n2", "n1")),
              "复用键对读集**顺序**不敏感（同一读集不会因展开顺序不同而变成两个键）")
        check(base_key != TR.read_set_reuse_key(anchor_key, ("n1",))
              and base_key != TR.read_set_reuse_key(anchor_key, ("n1", "n2", "n3")),
              "读集不同 ⇒ 键不同（收窄/扩张后的读集不是同一个读集）")
        check(base_key != TR.read_set_reuse_key(
            dataclasses.replace(anchor_key,
                                document_version=anchor_key.document_version + "-x"),
            ("n1", "n2"))
              and base_key != TR.read_set_reuse_key(
                  dataclasses.replace(anchor_key,
                                      evidence_set_version=
                                      anchor_key.evidence_set_version + "-x"),
                  ("n1", "n2")),
              "四轴身份任一不同 ⇒ 键不同（不得按 document_id 就近匹配）")

        real_read_set: dict[str, tuple[str, ...]] = {}
        for aspect in cb.aspects:
            _dec = real_nav_h.candidates(aspect, requirement=cb)
            _nodes = tuple(str(n) for n in real_nav_h.node_ids_for(_dec))
            if _nodes:
                real_read_set[aspect.aspect_id] = _nodes
        mats_by_aspect = {r.aspect_id: tuple(r.material_ids or ())
                          for r in packs[cb.topic_id].aspect_results}
        with_mats = [a for a, m in mats_by_aspect.items()
                     if m and real_read_set.get(a)]
        check(len(with_mats) >= 2,
              "真实切片里至少两条 aspect「读集非空且真读到了材料」"
              f"（复用正反例的现场）；实际 {len(with_mats)}")

        def _reuse_key_of(nodes) -> tuple:
            return TR.read_set_reuse_key(company_ctx.sources.current_state_key(), nodes)

        if len(with_mats) >= 2:
            r_key = _reuse_key_of
            pos_first, pos_second = with_mats[0], with_mats[1]
            # 正例：把第二条 aspect 的读集显式改成**第一条的真实读集**——同一份来源 + 同一个
            # 读集。改写用的是真实决策里的真实 node（`_ReadSetOverrideNavigation` 只换读集）。
            same_overrides = {pos_first: real_read_set[pos_first],
                              pos_second: real_read_set[pos_first]}
            same_nav = _ReadSetOverrideNavigation(real_nav_h, same_overrides)
            mark = len(sink.events)
            same_run = TR.run_topic_requirement(
                cb, run_context=company_ctx,
                dependencies=_deps(cb, company_ctx, navigation=same_nav,
                                   research=_CiteWithoutAdopting(evidence_ids=cite_ids)))
            same_events = tuple(sink.events[mark:])
            same_pack = store.load_current(same_run.identity)
            dispatched = [a.aspect_id for a in cb.aspects
                          if same_overrides.get(a.aspect_id)
                          or real_read_set.get(a.aspect_id)]
            used_keys = {r_key(same_overrides.get(a, real_read_set.get(a, ())))
                         for a in dispatched}
            reuse_rows = [e["payload"] for e in same_events
                          if e["type"] == "TREE_READ_SET_REUSED"]
            real_calls = [e["payload"] for e in same_events
                          if e["type"] == "TREE_TOOL_RESULT"
                          and e["payload"].get("status") != "REUSED_READ_SET"]
            check(len(real_calls) == len(used_keys)
                  and len(real_calls) + len(reuse_rows) == len(dispatched),
                  "真读次数 == 本跑用到的**不同读集**数；每个派发恰好消耗一次真读或一次复用"
                  f"（真读 {len(real_calls)} / 复用 {len(reuse_rows)} / "
                  f"派发 {len(dispatched)}）")
            pos_reuse = [r for r in reuse_rows
                         if str(r["aspect_id"]) == pos_second]
            check(len(pos_reuse) == 1
                  and int(pos_reuse[0]["reused_material_count"]) > 0,
                  "同一份来源 + 同一个读集的第二条 aspect 走复用，且复用出了非空材料"
                  f"（本跑另有 {len(reuse_rows) - len(pos_reuse)} 条复用是真实导航自然产生的——"
                  "多栏落在同一读集上是常态，断言只钉被改写的那一条）；实际 "
                  f"{[(str(r['aspect_id']), r['reused_material_count']) for r in reuse_rows]}")
            pos_calls = [e["payload"] for e in same_events
                         if e["type"] == "TREE_TOOL_RESULT"
                         and str(e["payload"]["aspect_id"]) == pos_second]
            check(len(pos_calls) == 1
                  and pos_calls[0]["status"] == "REUSED_READ_SET",
                  "复用侧**没有**发出真实树调用（该 aspect 的 `TREE_TOOL_RESULT` 只有一条，"
                  "状态是 REUSED_READ_SET）")
            same_results = {r.aspect_id: r for r in same_pack.aspect_results}
            check(bool(same_results[pos_second].material_ids),
                  "复用的材料照样成为本栏目的栏目材料（一段材料服务多栏，各栏各自记录用途）")
            check(set(pos_reuse[0]["reused_material_ids"])
                  <= set(same_results[pos_first].material_ids),
                  "复用交出的正是先读那一条**真实**读到的材料（同一批材料对象，不是另切一批）")
            check(len(same_pack.materials)
                  == len({m.material_id for m in same_pack.materials}),
                  "复用不复制材料身份（Pack 全局材料集里 material_id 无重复）")
            note(f"H4 复用正例：aspect {pos_first} 真读读集 {real_read_set[pos_first]} "
                 f"→ aspect {pos_second} 复用同一读集，复用材料 "
                 f"{len(pos_reuse[0]['reused_material_ids'])} 份")

            # 反例：读集**不同**的两条 aspect 不得共用缓存，各自真读。被改写的那条 aspect
            # 的新读集必须与本跑里其他任何一条的读集都不同——否则复用会（正确地）触发，
            # 负例就变成了正例；因此先在测试侧把本跑将用到的键算出来，再挑一个不撞的。
            neg_aspect, neg_nodes = None, None
            for _a in cb.aspects:
                if _a.aspect_id == pos_second or not real_read_set.get(_a.aspect_id):
                    continue
                for _n in real_read_set[_a.aspect_id]:
                    if r_key((_n,)) not in used_keys:
                        neg_aspect, neg_nodes = _a.aspect_id, (_n,)
                        break
                if neg_nodes:
                    break
            check(neg_nodes is not None,
                  "真实切片里存在一个「与其他任何读集都不同」的单节点读集"
                  "（负例不与复用规则本身冲突，也不会被别的 aspect 顶替成命中）")
            if neg_nodes:
                diff_nav = _ReadSetOverrideNavigation(
                    real_nav_h, {neg_aspect: neg_nodes})
                mark = len(sink.events)
                TR.run_topic_requirement(
                    cb, run_context=company_ctx,
                    dependencies=_deps(cb, company_ctx, navigation=diff_nav,
                                       research=_CiteWithoutAdopting(
                                           evidence_ids=cite_ids)))
                diff_events = tuple(sink.events[mark:])
                # 断言只钉**被改写的那一条**：真实导航下其余 aspect 之间本就存在自然复用
                # （多栏落在同一读集上），把它们也一并禁止会把真实行为读成缺陷。
                check(not [e for e in diff_events
                           if e["type"] == "TREE_READ_SET_REUSED"
                           and str(e["payload"]["aspect_id"]) == neg_aspect],
                      "读集不同的 aspect 不共用缓存（不同读集不是同一个读集，自己真读）")
                diff_calls = [e["payload"] for e in diff_events
                              if e["type"] == "TREE_TOOL_RESULT"
                              and str(e["payload"]["aspect_id"]) == neg_aspect]
                check(len(diff_calls) == 1
                      and diff_calls[0]["status"] != "REUSED_READ_SET",
                      "读集不同的 aspect 走真实调用，不虚报复用")
                note(f"H4 复用负例：aspect {neg_aspect} 收窄到单节点 {neg_nodes} "
                     f"（键 {r_key(neg_nodes)} 不在本跑其他读集键内）⇒ 无复用、一次真调用")

        # (H5) 栏目级原因：四类彼此不可互推，不得塌成 `coverage_gate_not_met`，更不得写成
        # 「语料里没有」。先逐条在纯函数面上钉死，再看真实运行里它们是不是分开留下的。
        empty_reasons, empty_counts = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={}, search_trace_count=0,
            requires_table_output=False, table_material_count=0)
        check(empty_reasons == ()
              and empty_counts == {"budget_blocked": 0, "no_candidate": 0,
                                   "call_fired": 0, "optional_blocked": 0},
              "既没派发也没材料 ⇒ 不得凭空产出一条原因（不猜、不从「没有材料」倒推）")
        budget_only, budget_counts = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={"k": "budget_exhausted"},
            search_trace_count=0, requires_table_output=False, table_material_count=0)
        check(budget_only == ("budget_blocked_dispatch",)
              and budget_counts["budget_blocked"] == 1
              and budget_counts["call_fired"] == 0,
              "预算挡下的派发 ⇒ `budget_blocked_dispatch`（本轮没查），不由 `call_fired` 冒领")
        cand_only, cand_counts = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={"k": "dispatched_no_candidate"},
            search_trace_count=0, requires_table_output=False, table_material_count=0)
        check(cand_only == ("dispatched_no_candidate",) and cand_counts["call_fired"] == 0,
              "派发过、导航给不出候选 ⇒ `dispatched_no_candidate`（与预算挡下是两件事）")
        found_only, _ = TR.derive_column_unmet_reasons(
            materials=(object(),), facts=(), dispatched_without_call={},
            search_trace_count=3, requires_table_output=False, table_material_count=0)
        check(found_only == ("material_found_but_unsupported",),
              "材料拿到了、没有合格事实闭环 ⇒ `material_found_but_unsupported`（取得 ≠ 支持）；"
              "这也是「客户合作 ≠ 客户集中度」「关联交易 ≠ 客户/供应商集中度」那类同源不同栏"
              "误召回在运行时的落点：材料在，支持不在")
        table_only, _ = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={}, search_trace_count=0,
            requires_table_output=True, table_material_count=0)
        check(table_only == ("table_material_unqualified",),
              "Contract 声明表格槽位而本轮无合格表格对象 ⇒ `table_material_unqualified`")
        audit_only, _ = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={}, search_trace_count=1,
            requires_table_output=False, table_material_count=0,
            not_found_branch=True, not_found_qualified=False)
        check(audit_only == ("search_audit_incomplete",),
              "真实未命中但 not-found 条件未全部成立 ⇒ 只能留 `search_audit_incomplete`")
        undetermined, _ = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={}, search_trace_count=1,
            requires_table_output=False, table_material_count=0,
            not_found_branch=True, not_found_qualified=None)
        check(undetermined == (),
              "not-found 资格**未定**（None）不得当成不成立 ⇒ 不产 `search_audit_incomplete`")
        all_five, all_counts = TR.derive_column_unmet_reasons(
            materials=(object(),), facts=(),
            dispatched_without_call={"a": "budget_exhausted",
                                     "b": "dispatched_no_candidate"},
            search_trace_count=2, requires_table_output=True, table_material_count=0,
            not_found_branch=True, not_found_qualified=False)
        check(set(all_five) == set(TR.UNMET_COLUMN_REASONS)
              and all_counts == {"budget_blocked": 1, "no_candidate": 1,
                                 "call_fired": 2, "optional_blocked": 0},
              "五条同时成立时逐条并列返回（一个栏目可同时成立多条），计数各归各的桶")
        # 「必读发了、补件被预算挡下」这一档：它**不是**「已派发但未命中」，也不是
        # 「必读发不出去」。落同一个原因码 `budget_blocked_dispatch`（都是「预算没让该查的
        # 调用发生」），但计数落在**另一个桶**里，读回的人因此分得开结构性缺陷与预算用量。
        opt_only, opt_counts = TR.derive_column_unmet_reasons(
            materials=(), facts=(), dispatched_without_call={}, search_trace_count=3,
            requires_table_output=False, table_material_count=0,
            optional_budget_blocked=2)
        check(opt_only == ("budget_blocked_dispatch",)
              and opt_counts["optional_blocked"] == 2
              and opt_counts["budget_blocked"] == 0
              and opt_counts["no_candidate"] == 0
              and opt_counts["call_fired"] == 3,
              "必读如实发完、可选补件被预算挡下 ⇒ 同样落 `budget_blocked_dispatch`，"
              "但计数在 `optional_blocked` 桶里（不与「必读发不出去」混成一条）")
        check(all(r in TR.UNMET_COLUMN_REASONS for r in all_five),
              "原因码一律取自闭集，绝不写「材料不存在」这类判决式措辞")

        # 真实运行一侧：同一条规则在真跑里留下的读数。
        col_gaps = [g for g in tight_run.gaps if g.get("reason") == "column_unmet"]
        not_covered = [a for a, s in tight_run.aspect_statuses if s != "covered"]
        check(len(col_gaps) == len(not_covered) == len(cb.aspects),
              "每条未达 covered 的 aspect 恰好一条 `column_unmet`"
              f"（不塌成一个码、也不重复登记）；实际 {len(col_gaps)} 条 / "
              f"未覆盖 {len(not_covered)} 条")
        check(all(set(g.get("unmet_column_reasons") or ()) <= set(TR.UNMET_COLUMN_REASONS)
                  and g.get("closed_set") == list(TR.UNMET_COLUMN_REASONS)
                  and g.get("rule_version") == TR.MANDATORY_FIRST_READ_RULE_VERSION
                  for g in col_gaps),
              "每条 `column_unmet` 自带闭集快照与规则版本，原因一律取自闭集")
        check(any("budget_blocked_dispatch" in (g.get("unmet_column_reasons") or ())
                  for g in col_gaps),
              "真实运行里「预算阻止派发」如实出现在该栏目的原因里（不是笼统的覆盖门未过）")
        unsupported = [g for g in col_gaps
                       if int(g.get("material_count") or 0) > 0
                       and int(g.get("fact_count") or 0) == 0]
        check(bool(unsupported) and all(
            "material_found_but_unsupported" in (g["unmet_column_reasons"] or ())
            for g in unsupported),
              "有材料、没有合格事实的栏目一律登记 `material_found_but_unsupported`"
              f"（取得 ≠ 支持）；实际 {len(unsupported)} 条")
        check(all(not ("dispatched_no_candidate" in (g.get("unmet_column_reasons") or ())
                       and not (g.get("dispatched_without_call") or {}))
                  for g in col_gaps if int(g.get("material_count") or 0) > 0),
              "有材料又没有任何「派发未命中」记录的栏目，不得报「已派发但未命中」"
              "（两类原因不得互相冒充）")
        coarse_gaps = [g for g in tight_run.gaps
                       if g.get("reason") in ("coverage_gate_not_met",
                                              "usage_scope_not_satisfied")]
        check(all("unmet_column_reasons" in g for g in coarse_gaps),
              "粗粒度覆盖门码旁必须并列逐条 typed 原因（读回的人不会把四件事读成一件）")

        # (H6) 「一段材料服务多栏」与「事实不得跨栏借」：直接读 Pack，不另造口径。
        cb_pack = packs[cb.topic_id]
        shared_ids = [m.material_id for m in cb_pack.materials
                      if sum(1 for r in cb_pack.aspect_results
                             if m.material_id in set(r.material_ids or ())) > 1]
        check(bool(shared_ids),
              "真实切片里同一份材料**真的**被两条以上 aspect 引用"
              f"（一段材料服务多栏不是空话）；实际 {len(shared_ids)} 份被多栏引用")
        check(len(cb_pack.materials)
              == len({m.material_id for m in cb_pack.materials}),
              "多栏复用**不复制材料身份**（一份原文一份 material_id，各栏各自记录用途）")
        covered_offenders = [
            (r.aspect_id, tuple(r.supported_fact_ids))
            for r in cb_pack.aspect_results
            if r.status == "covered" and not r.supported_fact_ids]
        check(not covered_offenders,
              "任何 `covered` 栏目必须有自己的合格事实（不得凭别栏的事实/材料立起来）；"
              f"实际 {covered_offenders[:3]}")
        fact_owner: dict[str, set] = {}
        for r in cb_pack.aspect_results:
            for fid in (r.supported_fact_ids or ()):
                fact_owner.setdefault(fid, set()).add(r.aspect_id)
        check(all(len(v) == 1 for v in fact_owner.values()),
              "同一条事实不得同时算作两条 aspect 的合格事实（事实逐栏各归各的）")
        note(f"H6 现场读数：company_business 材料 {len(cb_pack.materials)} 份 / 被多栏引用 "
             f"{len(shared_ids)} 份 / 事实 {len(fact_owner)} 条 / 覆盖栏目 "
             f"{sum(1 for r in cb_pack.aspect_results if r.status == 'covered')} 条 / "
             f"aspect 总数 {len(cb_pack.aspect_results)}")

        # (H7) 同源不同栏目不得互相顶替：引用**必须是本栏目自己材料里的那一个**父 Evidence
        # 才算闭环。机制在 `_adopt_facts` → `_resolve_material_for_citation`（只按本 aspect 的
        # `material_by_evidence` 解析）。正反两侧用**同一个**替身，只换被引的 evidence id：
        # 换成本栏目材料里的那个 ⇒ 采纳；换成别栏材料的那个 ⇒ 连候选都构不成，只留一条 typed
        # 诊断。这就是「客户合作 ≠ 客户集中度」「关联交易 ≠ 客户/供应商集中度」在运行时的落点：
        # 材料在同一份文档、同一个主题里，支持关系却逐栏各判。
        pack_by_mid = {m.material_id: m for m in cb_pack.materials}
        target_aspect = own_ev = own_text = None
        own_materials: list = []
        foreign_ev = foreign_note = None
        for _r in cb_pack.aspect_results:
            _own = [pack_by_mid[mid] for mid in (getattr(_r, "material_ids", ()) or ())
                    if mid in pack_by_mid]
            if not _own:
                continue
            _index = _index_by_evidence(_own)
            _unique = sorted(k for k, v in _index.items() if len(v) == 1)
            if not _unique:
                continue
            target_aspect = _r.aspect_id
            own_materials = _own
            own_ev = _unique[0]
            own_text = _payload_text(_index[own_ev][0], session.resolver)
            break
        check(target_aspect is not None and own_ev and own_text,
              "真实切片里存在一个「本栏目材料唯一对应一个父 Evidence」的栏目"
              f"（正反例的现场）；实际 {target_aspect}")
        if target_aspect is not None:
            _own_ids = {m.authority_assessment.evidence_id for m in own_materials}
            for _r in cb_pack.aspect_results:
                if _r.aspect_id == target_aspect:
                    continue
                _other = [pack_by_mid[mid]
                          for mid in (getattr(_r, "material_ids", ()) or ())
                          if mid in pack_by_mid]
                _hit = next((m.authority_assessment.evidence_id for m in _other
                             if m.authority_assessment.evidence_id not in _own_ids), None)
                if _hit:
                    foreign_ev, foreign_note = _hit, _r.aspect_id
                    break
            check(bool(foreign_ev),
                  "真实切片里存在「属于**别栏**材料、不属于本栏材料」的父 Evidence"
                  f"（反例才是跨栏，不是编造 id）；实际 {foreign_ev} 来自 {foreign_note}")

        def _aspect_facts(run_result, aspect_id):
            """Pack 内容只能经 typed readback 取（`TopicRuntimeResult` 只是身份/用量包装）。"""
            pack_obj = store.load_current(run_result.identity)
            for _r in pack_obj.aspect_results:
                if _r.aspect_id == aspect_id:
                    return tuple(getattr(_r, "supported_fact_ids", ()) or ())
            return None

        if target_aspect is not None and foreign_ev:
            ctx_own = _context(cb, run_id="m9303-h7-own")
            own_run = TR.run_topic_requirement(
                cb, run_context=ctx_own,
                dependencies=_deps(cb, ctx_own,
                                   research=_CiteEvidenceClaim(evidence_id=own_ev,
                                                               text=own_text)))
            own_facts = _aspect_facts(own_run, target_aspect)
            check(bool(own_facts),
                  "正例：引用**本栏目自己材料**里的父 Evidence ⇒ 采纳为本栏目的合格事实"
                  f"（{target_aspect} 事实 {len(own_facts or ())} 条）")

            ctx_foreign = _context(cb, run_id="m9303-h7-foreign")
            foreign_run = TR.run_topic_requirement(
                cb, run_context=ctx_foreign,
                dependencies=_deps(cb, ctx_foreign,
                                   research=_CiteEvidenceClaim(evidence_id=foreign_ev,
                                                               text=own_text)))
            foreign_facts = _aspect_facts(foreign_run, target_aspect)
            check(foreign_facts == (),
                  "反例：同一份替身、只把被引的父 Evidence 换成**别栏材料**里的那个 ⇒ 本栏目"
                  f"一条事实都不采纳（跨栏借证据不闭环）；实际 {foreign_facts}")
            foreign_target = [g for g in foreign_run.gaps
                              if g.get("aspect_id") == target_aspect
                              and g.get("reason") == "focused_research_no_supported_fact"]
            check(bool(foreign_target) and any(
                "citation_not_backed_by_aspect_material" in str(g.get("detail", ""))
                for g in foreign_target),
                  "跨栏引用留的是 typed 诊断 `citation_not_backed_by_aspect_material`"
                  "（不是静默丢弃，也不写成「材料不存在」）")
            foreign_mid = {m.authority_assessment.evidence_id: m.material_id
                           for m in cb_pack.materials}
            target_mids = set()
            for _r in store.load_current(foreign_run.identity).aspect_results:
                if _r.aspect_id == target_aspect:
                    target_mids = set(getattr(_r, "material_ids", ()) or ())
            check(foreign_mid.get(foreign_ev) not in target_mids,
                  "跨栏引用**不动本栏目的材料身份**（别栏那份材料不会被改挂到本栏目名下）")
            note(f"H7 跨栏反例：本栏目 {target_aspect} 自持材料里的父 Evidence {own_ev} ⇒ 采纳；"
                 f"别栏（{foreign_note}）材料里的父 Evidence {foreign_ev} ⇒ 零采纳、留 typed 诊断")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


# ---------------------------------------------------------------------------
# 真实切片：研究替身（只读真实 span；MOCK 只在「不编造证据」这一条下被允许）
# ---------------------------------------------------------------------------

class _SpanRecallResearch:
    """把「一次 focused 研究」替身为：**经正式树工具**读真实 span，逐字引用。

    - 工具、会话、参数与 runtime 自己的树调用**逐字段相同**（同一 selector、同一
      `max_spans` / `max_chars_per_span`），因此候选集就是 runtime 已准入的那一份；
    - 只在「该 span 的父 Evidence 在本 aspect 候选里唯一」时才闭环引用；否则仍然引用父
      Evidence ID（不带 locator）——这正是 §五 要求 fail-closed 的那条路径；
    - 只有「已定位到 node 且工具真实返回 0 候选」才上报 `NOT_FOUND_AFTER_SEARCH`；
      未定位到 node 时不上报该原因（没有真实搜索就不得写 not_found）。

    `calls` 里每次调用还留下**诊断面**（不参与采集与裁决）：召回全集（按 `span_id` 身份，
    含每个 span 的 node / 父 Evidence / 标题路径 / 字数）、采信的那个 span、未被采信的其余
    span、当次上界与树工具自报的 `gaps`/`skipped`。A1 的源头对账据此回答「材料是在召回、
    采信、入 Pack 还是 Writer 选材哪一层掉的」。
    """

    def __init__(self, *, navigation, requirement, document, tree_max_spans,
                 tree_max_chars) -> None:
        self._navigation = navigation
        self._requirement = requirement
        self._document = document
        self._tree_max_spans = tree_max_spans
        self._tree_max_chars = tree_max_chars
        self._aspects = {a.aspect_id: a for a in requirement.aspects}
        self.calls: list[dict] = []

    def __call__(self, *, need, route_result, registry, llm, budget, run_id, case_id,
                 company_id, section_id, trace_enabled, context):
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
            data = result.data or {}
            candidates = list(data.get("candidates") or ())
            # 诊断面（**不参与**采集与裁决）：这一次有界调用到底召回了什么、采信了哪一条、
            # 其余为什么没有进入材料，必须能从产物里对账。**身份用 `span_id`**（一条父
            # Evidence 会被切成多 span：按父身份去重会把「同一父 Evidence 的其余 span 被丢」
            # 记成「没有丢弃」）。`text` 只留在内存里给验收侧的源头对账用（报告侧只写统计与
            # 预览），不作任何判据输入。`skipped` 原样留下：上界截断（`over_max_spans`）与
            # 单 span 超长（`over_max_chars_per_span`）都是「材料在哪一层掉的」的如实证据。
            recalled = [{"span_id": str(c.get("span_id") or ""),
                         "node_id": str(c.get("node_id") or ""),
                         "parent_evidence_id": str(c["parent_evidence_id"]),
                         "heading_path": [str(x) for x in (c.get("heading_path") or ())],
                         "chars": len(str(c.get("text") or "")),
                         "text": str(c.get("text") or "")}
                        for c in candidates]
            adopted_span_id: str | None = None
            if candidates:
                counts = collections.Counter(
                    c["parent_evidence_id"] for c in candidates)
                pick = next((c for c in candidates
                             if counts[c["parent_evidence_id"]] == 1), candidates[0])
                adopted_span_id = str(pick.get("span_id") or "")
                citations.append(TS.CitationRef(
                    ref_type="evidence", evidence_id=pick["parent_evidence_id"]))
                # 文本**逐字**取自真实 span：不写固定答案，也不改写。
                claims.append(HS.Claim(claim_id="c1", text=pick["text"], kind="fact",
                                       citation_refs=[0]))
                verdicts.append(HS.EntailmentVerdict(
                    claim_id="c1", citation_ids=["0"], verdict="SUPPORTED",
                    reason="claim 文本逐字取自该 span 的真实文本"))
                stop_reason = "COMPLETED"
            else:
                stop_reason = STOP_NOT_FOUND
            self.calls.append({
                "aspect_id": aspect.aspect_id, "status": result.status,
                "candidates": len(candidates), "node_ids": list(node_ids),
                "max_spans": self._tree_max_spans,
                "max_chars_per_span": self._tree_max_chars,
                "recalled": recalled, "adopted_span_id": adopted_span_id,
                "discarded": [r for r in recalled
                              if r["span_id"] != adopted_span_id],
                "tool_gaps": len(list(data.get("gaps") or ())),
                "tool_skipped": [dict(s) for s in (data.get("skipped") or ())]})
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
            # usage 进 Pack 内容身份（§605 幂等），故替身必须确定性上报；
            # 实测墙钟由 runtime 单独记进 trace，不进内容身份。
            usage=HS.UsageLedger(rounds=1, tool_calls=tool_calls,
                                 local_searches=tool_calls, llm_calls=1,
                                 input_tokens=120, output_tokens=40, elapsed_ms=8))
        return HS.ResearchOutcome(
            state=state, answer=answer, success=bool(claims),
            completion_status=answer.completion_status, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

def _index_by_evidence(materials) -> dict:
    """`evidence_id -> tuple[材料, ...]`（与 runtime 同规则：绝不做最后写入者胜出）。"""
    index: dict = {}
    for material in materials:
        index.setdefault(
            material.authority_assessment.evidence_id, []).append(material)
    return {k: tuple(v) for k, v in index.items()}


def _payload_text(material, resolver) -> str:
    """材料 payload 字节里的真实 span 文本（独立于运行对象重切后读取）。"""
    resolved = TS.verify_material_payload_ref(material.payload_ref, resolver)
    if resolved.payload_bytes is None:
        raise AssertionError("payload 字节缺失：无法证明 claim 文本来自真实 span")
    return json.loads(resolved.payload_bytes.decode("utf-8"))["content"]["text"]


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
    """永远解析不出 payload 的 resolver（用于证明 0 命中 → None）。"""

    def resolve(self, payload_ref):
        return None


class _NoCurrentStore:
    """符合 `PackSetStore` 协议的替身：如实回「没有 current」，不提供任何写入口。

    用于证明 backbone phase 在读门不成立时**抛错**，而不是返回半套 Pack。
    """

    def __init__(self, resolver=None) -> None:
        self._resolver = resolver

    @property
    def resolver(self):
        return self._resolver

    def load_current(self, identity):
        return PSet.PackSetCurrentLoad(pack=None, reason="no_current")

    def list_current(self):
        return ()


class _WrongNavigation:
    """只暴露 candidates 的导航提供者：故意对任何 aspect 都报「不在 profile」。"""

    def candidates(self, aspect, *, requirement):
        raise TR.TopicRuntimeError(
            f"aspect {getattr(aspect, 'aspect_id', aspect)!r} 不在导航 profile"
            f"（替身：证明换 profile 会 fail-closed）")

    def node_ids_for(self, decision):
        return ()


class _ReadSetOverrideNavigation:
    """按 aspect 改写内层真实导航的**读集**，其余一字不改（`fba-1` 正反例夹具）。

    两种改写，都是 runtime 自己承认的既有形状：

    - **清空**（`()`）：与真实 fallback 终态逐字段同形——`status="fallback"`、带
      `fallback_reason`、`selected_node_id=None`、读根 / 近分带 / 读集 / 未读范围全空。
      `document_structure/navigation.py::_decision` 对 fallback 终态的要求正是这些
      （「fallback 终态不得携带读集 / 读根」），所以这不是造出来的形状，而是**同一形状**。
      真实文档上「读集为空」只由候选不够分 / 上提后没有合格读根触发，测试无法按需制造。
    - **收窄**（非空 tuple）：仍是 `selected`，读根与读集都指向内层**真实读集里的** node
      （测试逐条断言这条子集关系），只是这一次读一个候选材料也读不出来——这正是 runtime
      自己命名的状态 `tree_no_material`（「树导航未产出可重切的 OutlineSpan 材料」）。

    两层都不造 node：改写用的 node 一律取内层真实决策，仍过同一个 runtime、同一份真实
    材料与同一个工具。用途是让「读集为空 ⇒ 兜底不替这一栏造读集」这条规则的两侧都能被
    真实驱动，而不是只验其中一侧。
    """

    def __init__(self, inner, read_set_by_aspect, fallback_reason_by_aspect=None) -> None:
        self._inner = inner
        self._override = {str(k): tuple(v)
                          for k, v in read_set_by_aspect.items()}
        # 逐 aspect 的 fallback 终态理由（清空时才用得上）。三种理由都是 `navigation.py`
        # 自己会给出的终态名，逐条覆盖：这条规则只看**读集**，三种理由必须行为一致。
        self._reasons = {str(k): str(v) for k, v in
                         (fallback_reason_by_aspect or {}).items()}

    def _rewrite(self, decision):
        if decision is None:
            return None
        aspect_id = str(getattr(decision, "aspect_id", ""))
        if aspect_id not in self._override:
            return decision
        narrowed = self._override[aspect_id]
        if not narrowed:
            return dataclasses.replace(
                decision, status="fallback",
                fallback_reason=self._reasons.get(aspect_id, "low_confidence"),
                selected_node_id=None, subtree_node_ids=(), band_node_ids=(),
                read_root_node_ids=(), read_node_ids=(), unread_node_ids=(),
                unread_total=0)
        return dataclasses.replace(
            decision, status="selected", fallback_reason=None,
            selected_node_id=narrowed[0], subtree_node_ids=(), band_node_ids=(),
            read_root_node_ids=narrowed, read_node_ids=narrowed,
            unread_node_ids=(), unread_total=0)

    def candidates(self, aspect, *, requirement):
        return self._rewrite(self._inner.candidates(aspect, requirement=requirement))

    def node_ids_for(self, decision):
        aspect_id = str(getattr(decision, "aspect_id", ""))
        if aspect_id in self._override:
            return self._override[aspect_id]
        return self._inner.node_ids_for(decision)

    def __getattr__(self, name):
        # 单文档提供方（`IndexedTreeNavigation`）**没有** `candidates_for`：这一点必须
        # 照实暴露，否则 runtime 会以为换上了跨源提供方而改走逐份派发（§L3.3 的错配）。
        if name == "candidates_for":
            inner_for = getattr(self._inner, "candidates_for")

            def _rewritten(aspect, *, requirement, source_key):
                return self._rewrite(inner_for(
                    aspect, requirement=requirement, source_key=source_key))

            return _rewritten
        return getattr(self._inner, name)


class _CiteWithoutAdopting:
    """研究替身：**只引用**真实父 Evidence、一条 claim 都不提（逼出有界重切）。

    runtime 只有在「没有任何事实被闭环采纳、但确有被引用的父 Evidence」时才会走
    有界 Evidence 重切（§七）。本替身恰好只制造这个形状：citations 是真的（父 Evidence
    ID 由调用方给），claims 为空 ⇒ `adopted` 为空 ⇒ 重切被触发；而重切交出的材料是否
    成为该 aspect 的栏目材料，正是 `fba-1` 要判的那件事。

    它**不代表任何真实研究结论**，也不编造证据文本：它只说「这一次研究引用了这些东西、
    没有闭环」。因此它只出现在专门验证重切归属的用例里。
    """

    def __init__(self, *, evidence_ids) -> None:
        self._evidence_ids = tuple(evidence_ids)
        self.calls = 0

    def __call__(self, *, need, route_result, registry, llm, budget, run_id, case_id,
                 company_id, section_id, trace_enabled, context):
        self.calls += 1
        citations = [TS.CitationRef(ref_type="evidence", evidence_id=e)
                     for e in self._evidence_ids]
        answer = HS.ResearchAnswer(
            question_id=need.need_id, answer_text="", claims=[],
            citations=citations, completion_status="UNRESOLVED")
        state = HS.ResearchState(
            run_id=run_id, case_id=case_id, question_id=need.need_id,
            company_id=company_id, section_id=section_id,
            original_question=need.question, need=need, status="BLOCKED",
            entailment_verdicts=(), usage=HS.UsageLedger(
                rounds=1, tool_calls=0, local_searches=0, llm_calls=1,
                input_tokens=90, output_tokens=20, elapsed_ms=7))
        return HS.ResearchOutcome(
            state=state, answer=answer, success=False,
            completion_status="UNRESOLVED", stop_reason="COMPLETED")


class _CiteEvidenceClaim:
    """研究替身：引用**一个指定的**真实父 Evidence，并为它提一条 fact claim（判「背得动吗」）。

    它只做一件事：把「引用哪一份材料」这一个变量交给调用方。除被引的 evidence id 之外，
    两个实例逐字段同形（同文本、同 kind、同 entailment verdict、同用量）。因此两侧跑出来的
    差别只能来自**那一份材料属不属于本栏目**——这正是「客户合作 ≠ 客户集中度」「关联交易 ≠
    客户/供应商集中度」那类同源不同栏误召回要判的事。

    它不编造证据：文本由调用方从真实材料 payload 里逐字取出（`_payload_text`）。
    """

    def __init__(self, *, evidence_id: str, text: str) -> None:
        self._evidence_id = str(evidence_id)
        self._text = str(text)
        self.calls = 0

    def __call__(self, *, need, route_result, registry, llm, budget, run_id, case_id,
                 company_id, section_id, trace_enabled, context):
        self.calls += 1
        citations = [TS.CitationRef(ref_type="evidence", evidence_id=self._evidence_id)]
        claims = [HS.Claim(claim_id="c1", text=self._text, kind="fact",
                           citation_refs=[0])]
        verdicts = [HS.EntailmentVerdict(
            claim_id="c1", citation_ids=["0"], verdict="SUPPORTED",
            reason="替身声明该 claim 被被引材料支持（由 runtime 另行判它是否背得动本栏目）")]
        answer = HS.ResearchAnswer(
            question_id=need.need_id, answer_text=self._text, claims=claims,
            citations=citations, completion_status="COMPLETED")
        state = HS.ResearchState(
            run_id=run_id, case_id=case_id, question_id=need.need_id,
            company_id=company_id, section_id=section_id,
            original_question=need.question, need=need, status="COMPLETED",
            entailment_verdicts=verdicts,
            usage=HS.UsageLedger(rounds=1, tool_calls=0, local_searches=0,
                                 llm_calls=1, input_tokens=110, output_tokens=35,
                                 elapsed_ms=9))
        return HS.ResearchOutcome(
            state=state, answer=answer, success=True,
            completion_status=answer.completion_status, stop_reason="COMPLETED")


class _ZeroToolNeed:
    """替身预算政策：`need_budget().build().max_tool_calls == 0`（§H 反例夹具）。

    只用于证明 `focused_need_reserve` 的**拒收**一侧：内层工具上界为 0 时，「单次 need 最多
    花多少工具调用」不可知，按 0 预留就等于让 focused follow-up 免费（可选补件不受门）。
    它不冒充任何真实政策，也不参与任何采集与裁决。
    """

    max_need_rounds_per_aspect = 2
    max_tool_calls = 0

    def need_budget(self):
        return self

    def build(self):
        return self


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

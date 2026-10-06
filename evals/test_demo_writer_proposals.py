"""M930-3 反例集：Writer 的**门前候选提案契约**（`sections.pack_writer.parse_writer_proposals`）。

对应缺陷族（`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` 行 2406 / §16.10 的 proposal 项，编码批 3B）：

 A. Writer **不再直出定稿对象**：提案词汇表与 `SectionDraft` 的类型层都**不可表达**
    `SectionClaim` / 最终 Narrative / 接受绑定 / `SectionResult` / 自评决定。这不是「约定」，
    而是词表封闭 + 字段集检查能证伪的**结构事实**——写死一个 `claims` 键就会被 `_strict_keys`
    直接拒。
 B. 提案的**语法与语义声明缺失即拒**：顶层键集、候选、factual 边、context 边、草稿单元、
    补件申请六层，每一层的每个必备项都各有一条独立反例；不设默认值、不做宽容降级。
 C. 语法层**只判结构**，不判「这个坐标在不在本次权威输入里」——后者由写入侧回查权威目录与
    独立硬门完成（不按文本相似度猜）。本模块同时钉住这条分工：语法层放行的坐标，写入侧仍
    可能拒；反之语法层拒绝的输出，写入侧根本收不到。

本模块不读库、不连网、不跑真实文档、不调真实 LLM：A 组用 `test_demo_pack_writer` 的替身夹具
做一次真实 `write_section`（stub 生成器），B/C 组是纯结构反例（不构造任何权威输入）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import narrative_schema as NS
from sections import pack_writer as PW
# A 组需要一份真实权威 + stub 生成器：复用同批夹具，避免两套替身各自漂移。
from evals import test_demo_pack_writer as FIXT


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

    def raw(payload) -> str:
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # A. Writer 不再直出定稿对象（类型层 + 词表层双重不可表达）
    # ------------------------------------------------------------------
    # A1. 门前产物**没有**门后身份的字段（不是「暂时为空」，是根本没有这个槽位）。
    outcome_fields = set(PW.PackWriteOutcome.__dataclass_fields__)
    # `material_context` 是**门前输入**（`wmctx-1` 已解析正文），不是门后身份：它必须随产物
    # 一起交回组合根，否则语义门拿不到与 Writer 相同的那份正文。
    # `rejections` 是**门前**的 typed 拒绝审计（被整束拒绝的候选提案集，§二 3），也不是门后
    # 身份：它是「被拒的候选没有被原地删掉」的可回查证据，必须随产物一起留存。
    # `batch_audit` 是**门前**的分批审计（`pw-10` 起：一轮 = 一次分批扫描，逐批的 aspect 范围、
    # 调用读数与合并结果），同样不是门后身份：它证明「一份完整提案集是怎么从各批合出来的」。
    # `follow_up_rejections`（§三/1）同理：被采信那一轮里**自己不成立**的补件申请（逐条原因
    # 码 + 序号 + 可读原因）。它与 `follow_up_needs` 是同一件事的两半，也不是门后身份；少了
    # 它，一条填错的申请会让整节连 Draft 一起消失，或把「诉求不成立」写成「没有诉求」。
    check(outcome_fields == {"draft", "follow_up_needs", "follow_up_rejections", "unresolved",
                             "gate_result", "prompt_version", "model_policy", "llm_calls",
                             "llm_trace", "rejections", "coverage_summary", "material_context",
                             "batch_audit"},
          f"PackWriteOutcome 字段集必须封闭（实际 {sorted(outcome_fields)}）")
    check(not ({"claims", "section_result", "narrative", "evaluation", "decisions",
                "accepted_bindings", "claim_entailment_decisions", "final_narrative"}
               & outcome_fields),
          "门前产物不得携带任何门后身份（定稿 Claim / 最终 Narrative / 接受绑定 / "
          "entailment 决定 / SectionResult / 自评）")

    # A2. 草稿本身只到候选态：候选、草稿单元、proposal、缺口投影；没有定稿与决定。
    draft_fields = set(NS.SectionDraft.__dataclass_fields__)
    check(not ({"claims", "section_result", "evaluation", "accepted_bindings",
                "claim_entailment_decisions", "narrative", "paragraphs", "tables",
                "fact_narrative_dispositions"} & draft_fields),
          f"SectionDraft 不得承载门后身份（实际多出 "
          f"{sorted({'claims', 'section_result', 'evaluation', 'accepted_bindings', 'claim_entailment_decisions', 'narrative', 'paragraphs', 'tables', 'fact_narrative_dispositions'} & draft_fields)}）")
    check({"claim_candidates", "narrative_draft_units", "proposed_support_refs",
           "material_manifest", "material_dispositions", "unresolved_projections"}
          <= draft_fields,
          "草稿必须只剩候选态身份（候选 / 草稿单元 / proposal / 材料清单与处理行 / 缺口投影）")

    # A3. sidecar 是门前产物的可审计切片，键集必须先验地落在这个产物里（A4 用真实产物复核）。
    check(PW.PackWriteOutcome.sidecar.__doc__ is not None,
          "sidecar 必须显式声明自己是**门前部分**（不含正文渲染与门后决定）")

    # A4. 端到端：真实 stub 生成一次，draft 里只有候选态，且 proposal union 是**唯一**的
    #     「支撑引用」wire（不存在第三种）。
    spec = FIXT.WS.load_writing_spec(FIXT.SPEC_PATH)
    profile = FIXT.PP.load_presentation_profile(FIXT.PROFILE_PATH)
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    task = FIXT._task("company", (FIXT.TOPIC_BUSINESS,), task_id="task-proposals-a")
    authority = FIXT._company_authority(
        task,
        facts=(FIXT._fact("f-1", "公司主营业务为动力电池系统的研发、生产与销售。",
                          (FIXT.ASP_BUSINESS_MAIN,)),),
        aspects=(FIXT._AspectResult(FIXT.ASP_BUSINESS_MAIN, "covered"),),
        requirements=(FIXT._Req(FIXT.TOPIC_BUSINESS, (
            FIXT._aspect(FIXT.ASP_BUSINESS_MAIN, FIXT.TOPIC_BUSINESS,
                         "q-company_business"),)),))
    scan = PW.scan_topic_pack(authority, task)
    entry = {e.fact_id: e for e in scan.facts}["f-1"]
    # §三 A：topic 权威的写作必须带 `wmctx-1` 材料正文上下文（缺它一律 fail-closed，
    # 「只给 material ID」不是可降级的输入形态）。这里走与生产组合根同一入口现解析。
    material_context = FIXT._writer_material_context(
        authority.pack_set, task_id=str(task.task_id), section_id="company")
    # 本节有材料行（fact 的来源 material 自动入 manifest），因此草稿的出处必须走**材料轴**：
    # `source_fact_refs` 只对材料面合法为空的节（财务节）开放。别名由写入侧同一函数派生，
    # 夹具不自己拼 `m1`。
    manifest = PW._derive_material_manifest(authority, material_context=material_context)
    aliases = PW._support_aliases(scan, manifest)
    member_ref = str(aliases.materials[0].ref)
    outcome = PW.write_section(
        task, authority, projection=projection, writing_spec=spec,
        presentation_profile=profile, llm_client=FIXT._StubLlm(FIXT._plan(
            candidates=[FIXT._cand("c1", entry.text, FIXT._fact_edge(scan, "f-1"))],
            # 当前线（`proposals-12`）要求先有自然草稿：有候选而没有草稿的束会被整批拒。
            prose=[FIXT._prose("p1", entry.text, members=[member_ref], atoms=["c1"])])),
        dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT,
        material_context=material_context)
    candidate_fields = set(NS.ClaimCandidate.__dataclass_fields__)
    check(outcome.draft.claim_candidates
          and not ({"claim_id", "accepted_support_refs", "support_bindings",
                    "entailment_decision_id", "scope_identity"} & candidate_fields),
          f"门前候选**不是**定稿 Claim：不得带定稿身份字段（实际 {sorted(candidate_fields)}）")
    check("candidate_id" in candidate_fields and "claim_id" not in candidate_fields,
          "门前候选只有候选态身份 `candidate_id`（定稿 `claim_id` 由门后派生）")
    check(all(isinstance(r, NS.ProposedSupportRef)
              for r in outcome.draft.proposed_support_refs)
          and not any(isinstance(r, NS.AcceptedSupportBinding)
                      for r in outcome.draft.proposed_support_refs),
          "门前只有 `ProposedSupportRef`（proposal），不得出现 `AcceptedSupportBinding`")
    check(PW.ClaimSupportRef.__args__ == (NS.ProposedSupportRef, NS.AcceptedSupportBinding)
          if hasattr(PW, "ClaimSupportRef") else True,
          "`ClaimSupportRef` 只是 `ProposedSupportRef | AcceptedSupportBinding` 的兼容 union")
    check(not [n for n in ("render_body_and_appendix",)
               if n in dir(PW) and "narrative" in str(
                   getattr(PW, n, ""))[:200].lower()],
          "门前 Writer 不产出正文渲染（最终 Narrative 属门后）")
    sidecar = outcome.sidecar()
    check(set(sidecar) == {"section_draft", "follow_up_needs", "section_unresolved",
                           "narrative_gate_result", "prompt_version", "model_policy",
                           "llm_calls", "llm_trace", "proposal_set_rejections",
                           "follow_up_rejections",
                           "coverage_summary", "writer_material_context",
                           "writer_batch_audit"},
          f"sidecar 的键集必须与门前产物一一对应（不另开一条 wire，实际 {sorted(sidecar)}）")
    slice_extras = set(sidecar["section_draft"]) - draft_fields
    check(slice_extras == {"claim_candidate_ids", "narrative_draft_unit_ids",
                           "proposed_support_ids", "material_member_refs",
                           "material_disposition_ids", "manifest_id",
                           "manifest_fingerprint"},
          f"sidecar 的 draft 切片只允许追加**派生索引**，不得叠加门后身份"
          f"（实际多出 {sorted(slice_extras)}）")
    body = json.dumps(sidecar, ensure_ascii=False, default=str)
    for forbidden in ("section_result", "claim_entailment_decision", "accepted_binding",
                      "claim_binding_decision", "fact_narrative_disposition"):
        check(forbidden not in body,
              f"门前 sidecar 不得出现门后身份键 `{forbidden}`")

    # A5. 词表层：把定稿对象**写进提案**必须被拒（未登记字段）。
    for bad_key, what in (("claims", "定稿 Claim 列表"),
                          ("section_result", "章节结果"),
                          ("accepted_bindings", "接受绑定"),
                          ("claim_entailment_decisions", "entailment 决定"),
                          ("narrative", "最终 Narrative"),
                          ("evaluation", "自评决定"),
                          ("payload_ref", "自报 payload 载体")):
        expect_error(
            lambda k=bad_key: PW.parse_writer_proposals(
                raw({"claim_candidates": [], "narrative_draft_units": [], k: []})),
            PW.PackWriterError, f"提案顶层出现{what}必须被拒（定稿对象不可表达）",
            needle="未登记字段")

    # ------------------------------------------------------------------
    # B. 顶层语法：键集封闭、类型、JSON
    # ------------------------------------------------------------------
    expect_error(lambda: PW.parse_writer_proposals("{不是 JSON"),
                 PW.PackWriterError, "非法 JSON 必须被拒", needle="合法 JSON")
    expect_error(lambda: PW.parse_writer_proposals("[1, 2, 3]"),
                 PW.PackWriterError, "提案顶层必须是对象", needle="必须是对象")
    expect_error(lambda: PW.parse_writer_proposals(raw({"tool_calls": []})),
                 PW.PackWriterError, "工具调用不可表达", needle="未登记字段")
    expect_error(lambda: PW.parse_writer_proposals(raw({"retrieval": {"q": "x"}})),
                 PW.PackWriterError, "检索请求不可表达", needle="未登记字段")
    expect_error(lambda: PW.parse_writer_proposals(raw({"need_more": True})),
                 PW.PackWriterError, "补充检索意图不可表达", needle="未登记字段")
    expect_error(
        lambda: PW.parse_writer_proposals(raw({"claim_candidates": {},
                                               "narrative_draft_units": []})),
        PW.PackWriterError, "claim_candidates 不是数组必须被拒", needle="必须是数组")
    # `pw-15` / 指令 E 第 3 项在输出面加了**门前自然草稿**这一路，因此顶层键集是四类而不是三类
    # （`_PROSE_KEYS` 描述它的线格式）。键集仍然**封闭**：多一个键即拒，且这四类里没有任何
    # 完成态/决定字段（`_PLAN_KEYS` 的注释就是这条契约本身）。
    check(PW._PLAN_KEYS == ("natural_prose_draft", "claim_candidates", "narrative_draft_units",
                            "follow_up_needs"),
          f"顶层键集必须封闭且与文档一致（实际 {PW._PLAN_KEYS}）")
    parsed = PW.parse_writer_proposals("{}")
    check(set(parsed) == set(PW._PLAN_KEYS)
          and parsed == {"natural_prose_draft": [], "claim_candidates": [],
                         "narrative_draft_units": [], "follow_up_needs": []},
          f"空提案只能解析成四个空数组（不得补默认内容，实际 {parsed}）")

    # ------------------------------------------------------------------
    # C. 候选：标签唯一、文本非空、支撑边非空、首边 primary
    # ------------------------------------------------------------------
    def edge(**over) -> dict:
        body = {"authority_kind": "topic_pack", "container_id": "pack-x", "fact_id": "fx",
                "material_id": None, "support_role": "primary",
                "support_semantics": "factual",
                "authorization_path": "path_a_prevalidated"}
        body.update(over)
        return body

    def cand_payload(edge_body: dict, *, text: str = "公司主营业务为动力电池系统的研发与销售。",
                     key: str = "c1", extra: dict | None = None) -> dict:
        item = {"candidate_key": key, "claim_text": text, "support": [edge_body]}
        if extra:
            item.update(extra)
        return {"claim_candidates": [item], "narrative_draft_units": []}

    check(PW._CANDIDATE_KEYS == ("candidate_key", "claim_text", "support"),
          f"候选字段集必须封闭（实际 {PW._CANDIDATE_KEYS}）")
    expect_error(lambda: PW.parse_writer_proposals(raw({"claim_candidates": ["x"]})),
                 PW.PackWriterError, "候选必须是对象", needle="必须是对象")
    expect_error(lambda: PW.parse_writer_proposals(raw(cand_payload(
        edge(), extra={"claim_id": "forged"}))),
        PW.PackWriterError, "候选自带 claim_id 必须被拒（定稿身份不可自报）",
        needle="未登记字段")
    expect_error(lambda: PW.parse_writer_proposals(raw(cand_payload(edge(), text="   "))),
                 PW.PackWriterError, "候选文本为空必须被拒", needle="claim_text 不得为空")
    expect_error(lambda: PW.parse_writer_proposals(raw({"claim_candidates": [
        {"candidate_key": "c1", "claim_text": "文本", "support": []}]})),
        PW.PackWriterError, "候选没有支撑边必须被拒（无据陈述不得进入）",
        needle="至少有一条 factual 支撑边")
    expect_error(lambda: PW.parse_writer_proposals(raw({"claim_candidates": [
        {"candidate_key": "c1", "claim_text": "文本", "support": [edge()]},
        {"candidate_key": "c1", "claim_text": "另一段文本", "support": [edge()]}]})),
        PW.PackWriterError, "候选标签重复必须被拒（束内必须唯一可指）", needle="重复")
    expect_error(lambda: PW.parse_writer_proposals(raw(cand_payload(
        edge(support_role="corroborating")))),
        PW.PackWriterError, "候选第一条支撑边不是 primary 必须被拒", needle="primary")
    expect_error(lambda: PW.parse_writer_proposals(raw({"claim_candidates": [
        {"candidate_key": "c1", "claim_text": "文本", "support": ["not-an-object"]}]})),
        PW.PackWriterError, "支撑边必须是对象", needle="必须是对象")
    # 标签缺省时的确定性编号（不得因缺省而重号）。
    # `require_natural_draft=False`（本模块 B–F 组的**语法层**探针统一如此）：这些探针不带
    # `aliases`（支撑边一律写长格式），而草稿单元的出处**恰有一条轴要非空**、两轴都靠别名表
    # 展开——没有别名表时一份**合法**草稿层在语法上就写不出来。因此「有候选、无草稿」这条
    # **形状**判定必须显式关掉，否则每一处语法层成功路径都会先撞上它，探针就不再钉它要钉的
    # 那一条。草稿层的缺省即拒由 A4 的端到端与专项反例钉住（那是唯一带真实别名表的入口）。
    numbered = PW.parse_writer_proposals(raw({"claim_candidates": [
        {"claim_text": "第一段文本", "support": [edge()]},
        {"claim_text": "第二段文本", "support": [edge()]}]}), require_natural_draft=False)
    check([c["candidate_key"] for c in numbered["claim_candidates"]] == ["c1", "c2"],
          f"缺省候选标签必须确定性编号（实际 "
          f"{[c['candidate_key'] for c in numbered['claim_candidates']]}）")

    # ------------------------------------------------------------------
    # D. factual 支撑边：路径 A / 路径 B 字段互斥，每项声明缺失即拒
    # ------------------------------------------------------------------
    check(PW._FACTUAL_SUPPORT_KEYS == ("authority_kind", "container_id", "fact_id",
                                       "material_id", "support_role", "support_semantics",
                                       "authorization_path"),
          f"factual 边字段集必须封闭（实际 {PW._FACTUAL_SUPPORT_KEYS}）")

    def edge_reject(over: dict, msg: str, needle: str) -> None:
        body = {k: v for k, v in edge().items() if k not in over.get("__drop__", ())}
        body.update({k: v for k, v in over.items() if k != "__drop__"})
        expect_error(lambda: PW.parse_writer_proposals(raw(cand_payload(body))),
                     PW.PackWriterError, msg, needle=needle)

    edge_reject({"authority_kind": ""}, "支撑边缺 authority_kind 必须被拒", "authority_kind")
    edge_reject({"authority_kind": "financial_pack_v2"},
                "未登记的 authority_kind 必须被拒", "authority_kind")
    edge_reject({"container_id": ""}, "支撑边缺容器身份必须被拒", "container_id")
    edge_reject({"__drop__": ("support_semantics",)},
                "支撑边缺 support_semantics 必须被拒", "support_semantics")
    edge_reject({"support_semantics": "background"},
                "未登记的 support_semantics 必须被拒", "support_semantics")
    edge_reject({"support_semantics": "context", "authorization_path": "context_only"},
                "候选的支撑边声明 context 必须被拒（context 不授权事实）",
                "factual")
    edge_reject({"__drop__": ("support_role",)},
                "支撑边缺 support_role 必须被拒", "support_role")
    edge_reject({"support_role": "primary_plus"},
                "未登记的 support_role 必须被拒", "support_role")
    edge_reject({"__drop__": ("authorization_path",)},
                "支撑边缺 authorization_path 必须被拒", "authorization_path")
    edge_reject({"authorization_path": "path_b_exact_material"},
                "未登记的授权路径必须被拒（词汇表里只有三条）", "authorization_path")
    edge_reject({"authorization_path": "context_only"},
                "context_only 不得支撑事实性候选", "不能支撑事实性候选")
    # 路径 A：必须给事实身份，且**不得**自选 material。
    edge_reject({"__drop__": ("fact_id",)},
                "路径 A 缺 fact_id 必须被拒（事实身份不得省略）", "必须给出 fact_id")
    edge_reject({"material_id": "m-1"},
                "路径 A 自带 material_id 必须被拒（material 锚点由权威事实的引用派生）",
                "不由模型选择")
    # 路径 B：只存在于 topic_pack；必须给 material；不得带任何事实身份。
    edge_reject({"authorization_path": "path_b_material_derived"},
                "路径 B 缺 material_id 必须被拒", "必须给出 material_id")
    edge_reject({"authorization_path": "path_b_material_derived",
                 "authority_kind": "financial_pack", "material_id": "m-1",
                 "__drop__": ("fact_id",)},
                "路径 B 只能落在 topic_pack 上", "只存在于 topic_pack")
    edge_reject({"authorization_path": "path_b_material_derived", "material_id": "m-1"},
                "路径 B 不得携带事实身份（材料派生的描述性原子不带 fact_id）",
                "不得携带任何事实身份")
    # 支撑边不得自报载荷 / 定位 / 快照载体（这些只能由权威侧派生）。
    for extra_key in ("payload_ref", "locator_ref", "snapshot_ref", "body_hash",
                      "canonical_url", "amount", "period", "unit"):
        body = dict(edge())
        body[extra_key] = "x"
        expect_error(lambda b=body: PW.parse_writer_proposals(raw(cand_payload(b))),
                     PW.PackWriterError,
                     f"支撑边不得声明 `{extra_key}`（载荷/定位/展示字段只能由权威侧派生）",
                     needle="未登记字段")

    # ------------------------------------------------------------------
    # E. context 支撑边：只允许 topic_pack，且必须绑真实 material
    # ------------------------------------------------------------------
    check(PW._CONTEXT_SUPPORT_KEYS == ("authority_kind", "container_id", "material_id",
                                       "support_role", "support_semantics",
                                       "authorization_path"),
          f"context 边字段集必须封闭（实际 {PW._CONTEXT_SUPPORT_KEYS}）")
    check(PW._CONTEXT_AUTHORITY_KINDS == ("topic_pack",),
          "本批 context 边只允许 topic_pack（其余权威去掉事实身份后没有可解析载体）")

    def ctx_edge(**over) -> dict:
        body = {"authority_kind": "topic_pack", "container_id": "pack-x",
                "material_id": "m-1", "support_role": "corroborating",
                "support_semantics": "context", "authorization_path": "context_only"}
        body.update(over)
        return body

    def unit_payload(context: list, *, kind: str = "paragraph",
                     text: str = "以下按业务条线说明公司经营结构。",
                     key: str = "u1", extra: dict | None = None) -> dict:
        item = {"unit_key": key, "unit_kind": kind, "text": text,
                "context_support": context}
        if extra:
            item.update(extra)
        return {"claim_candidates": [], "narrative_draft_units": [item]}

    def unit_check(over: dict, msg: str) -> None:
        body = {k: v for k, v in ctx_edge().items() if k not in over.get("__drop__", ())}
        body.update({k: v for k, v in over.items() if k != "__drop__"})
        expect_error(lambda b=body: PW.parse_writer_proposals(
            raw(unit_payload([b]))), PW.PackWriterError, msg)

    check(PW._UNIT_KEYS == ("unit_key", "unit_kind", "text", "context_support"),
          f"草稿单元字段集必须封闭（实际 {PW._UNIT_KEYS}）")
    expect_error(
        lambda: PW.parse_writer_proposals(raw({"narrative_draft_units": [
            {"unit_key": "u1", "unit_kind": "table", "text": "表",
             "header": ["期间"], "rows": [["2025年"]]}]})),
        PW.PackWriterError,
        "草稿单元不得承载表级/行级展示元数据（不可表达，不是「本轮没填」）",
        needle="未登记字段")
    expect_error(
        lambda: PW.parse_writer_proposals(raw(unit_payload([], extra={"unit_id": "forged"}))),
        PW.PackWriterError, "草稿单元不得自报定稿身份", needle="未登记字段")
    expect_error(
        lambda: PW.parse_writer_proposals(raw({"narrative_draft_units": [
            {"unit_key": "u1", "unit_kind": "paragraph", "text": "一", "context_support": []},
            {"unit_key": "u1", "unit_kind": "paragraph", "text": "二",
             "context_support": []}]})),
        PW.PackWriterError, "草稿单元标签重复必须被拒", needle="重复")
    expect_error(lambda: PW.parse_writer_proposals(raw(unit_payload([], kind="list"))),
                 PW.PackWriterError, "未登记的 unit_kind 必须被拒", needle="unit_kind")
    expect_error(lambda: PW.parse_writer_proposals(raw(unit_payload([], text="  "))),
                 PW.PackWriterError, "草稿单元文本为空必须被拒", needle="text 不得为空")
    expect_error(lambda: PW.parse_writer_proposals(
        raw({"narrative_draft_units": [{"unit_key": "u1", "unit_kind": "paragraph",
                                        "text": "一段话", "context_support": "x"}]})),
        PW.PackWriterError, "context_support 不是数组必须被拒", needle="必须是数组")
    unit_check({"material_id": ""},
               "context 边没有真实 material 必须被拒（context 不得凭空）")
    unit_check({"__drop__": ("support_semantics",)},
               "context 边缺 support_semantics 必须被拒")
    unit_check({"support_semantics": "factual"},
               "context 边声明 factual 必须被拒")
    unit_check({"authorization_path": "path_a_prevalidated"},
               "context 边的授权路径必须是 context_only")
    unit_check({"__drop__": ("support_role",)},
               "context 边缺 support_role 必须被拒")
    unit_check({"support_role": "primary"},
               "context 边的 support_role 必须是 corroborating")
    unit_check({"authority_kind": "financial_pack"},
               "financial_pack 不可表达为 context 边")
    unit_check({"authority_kind": "evidence_note"},
               "evidence_note 不可表达为 context 边（去掉事实身份后没有可解析载体）")
    unit_check({"authority_kind": "external_snapshot"},
               "external_snapshot 不可表达为 context 边")
    for extra_key in ("fact_id", "fact_key", "locator_ref", "payload_ref", "snapshot_id"):
        unit_check({extra_key: "x"},
                   f"context 边不得声明 `{extra_key}`（context 不授权事实，也不自报载体）")

    # ------------------------------------------------------------------
    # F. 补件申请：门前唯一的「请求更多材料」出口，归属项缺一即拒
    # ------------------------------------------------------------------
    check(PW._FOLLOW_UP_KEYS == ("statement", "target_requirement_id", "topic_id",
                                 "question_id", "aspect_id", "requiredness",
                                 "expected_source_class", "budget_hint"),
          f"补件申请字段集必须封闭（实际 {PW._FOLLOW_UP_KEYS}）")

    def fu(**over) -> dict:
        body = {"statement": "需要补充公司销售模式的口径说明。",
                "target_requirement_id": "req-1", "topic_id": "company_business",
                "question_id": "q-company_business", "aspect_id": "company_business_sales",
                "requiredness": "required", "expected_source_class": "company_industry",
                "budget_hint": "1"}
        body.update(over)
        return {"claim_candidates": [], "narrative_draft_units": [], "follow_up_needs": [body]}

    def fu_check(over: dict, msg: str, *, drop: tuple = (), needle: str = "") -> None:
        body = {k: v for k, v in fu()["follow_up_needs"][0].items() if k not in drop}
        body.update(over)
        payload = {"claim_candidates": [], "narrative_draft_units": [],
                   "follow_up_needs": [body]}
        expect_error(lambda: PW.parse_writer_proposals(raw(payload)),
                     PW.PackWriterError, msg, needle=needle)

    expect_error(lambda: PW.parse_writer_proposals(raw({"follow_up_needs": ["x"]})),
                 PW.PackWriterError, "补件申请必须是对象", needle="必须是对象")
    expect_error(lambda: PW.parse_writer_proposals(raw(fu(decision="ACCEPTED"))),
                 PW.PackWriterError, "补件申请不得自带裁决（裁决是门后身份）",
                 needle="未登记字段")
    for key in ("target_requirement_id", "topic_id", "question_id", "aspect_id"):
        fu_check({}, f"补件申请缺 {key} 必须被拒（缺口不得无归属）", drop=(key,), needle=key)
    fu_check({"statement": "   "}, "补件申请陈述为空必须被拒", drop=("statement",),
             needle="statement 不得为空")
    fu_check({"requiredness": "maybe"}, "未登记的 requiredness 必须被拒", needle="requiredness")
    fu_check({"expected_source_class": "web_search"},
             "未登记的来源类别必须被拒（不得越 SourcePolicy）",
             needle="expected_source_class")
    check(set(NS.AUTHORITY_KINDS) == {"topic_pack", "financial_pack", "evidence_note",
                                      "external_snapshot"},
          f"权威种类词表必须封闭（实际 {sorted(NS.AUTHORITY_KINDS)}）")
    check(set(NS.SUPPORT_ROLES) == {"primary", "corroborating"}
          and set(NS.SUPPORT_SEMANTICS) == {"factual", "context"}
          and set(NS.AUTHORIZATION_PATHS) == {"path_a_prevalidated",
                                              "path_b_material_derived", "context_only"},
          "role / semantics / authorization path 三条正交轴的词表都必须各自封闭")
    check(not (set(NS.SUPPORT_ROLES) & set(NS.AUTHORIZATION_PATHS)),
          "支撑边角色与授权路径不得共用词表（正交轴不得混用）")

    # ------------------------------------------------------------------
    # G. 语法层只判结构：同一份合法提案重复解析必须逐字相同（确定性、无副作用）
    # ------------------------------------------------------------------
    good = raw({"claim_candidates": [
        {"candidate_key": "c1", "claim_text": "公司主营业务为动力电池系统的研发与销售。",
         "support": [edge()]}],
        "narrative_draft_units": [
            {"unit_key": "u1", "unit_kind": "paragraph", "text": "以下按业务条线说明。",
             "context_support": [ctx_edge()]}],
        "follow_up_needs": [fu()["follow_up_needs"][0]]})
    first = PW.parse_writer_proposals(good, require_natural_draft=False)
    second = PW.parse_writer_proposals(good, require_natural_draft=False)
    check(first == second, "同一份提案重复解析必须给出逐字相同的结果（纯函数、无副作用）")
    check(set(first) == set(PW._PLAN_KEYS)
          and set(first["claim_candidates"][0]) == set(PW._CANDIDATE_KEYS)
          and set(first["claim_candidates"][0]["support"][0]) == set(PW._FACTUAL_SUPPORT_KEYS)
          and set(first["narrative_draft_units"][0]) == set(PW._UNIT_KEYS)
          and set(first["narrative_draft_units"][0]["context_support"][0])
          == set(PW._CONTEXT_SUPPORT_KEYS)
          and set(first["follow_up_needs"][0]) == set(PW._FOLLOW_UP_KEYS),
          "解析结果必须逐层落在封闭词表上（不得夹带额外字段）")
    # 语法层放行的坐标**不保证**在权威输入里 —— 那是写入侧回查权威目录与独立硬门的职责。
    check(first["claim_candidates"][0]["support"][0]["container_id"] == "pack-x",
          "语法层不得按文本相似度猜坐标（`pack-x` 这种不存在的容器照原样放行，"
          "由写入侧回查权威目录拒绝）")

    # ------------------------------------------------------------------
    # H. 提案词汇表里没有任何「自批 / 自评 / 检索」的表达面
    # ------------------------------------------------------------------
    source = (Path(__file__).resolve().parent.parent / "sections" / "pack_writer.py"
              ).read_text(encoding="utf-8")
    import re
    for forbidden in ("ToolRegistry", "tool_registry", "retriev", "web_search", "requests.",
                      "urllib", "http.client"):
        check(not re.search(rf"^\s*(from|import)\s.*{forbidden}", source, re.MULTILINE),
              f"pack_writer 不得 import `{forbidden}`（Writer 不检索、不联网）")

    # ------------------------------------------------------------------
    # I. C3：定向重提案的跨修订去向记录（`hrrp-1`）——词表闭合、键集完整、方向单向
    # ------------------------------------------------------------------
    # 这一层的反例是**记录自己**的：去向词与明细必须双向自洽（「还在不在」与「列了哪几条」
    # 不能互相矛盾），键集必须恰好等于原束的完整有序候选身份，终点必须是**紧接的下一轮**。
    # 行为面（真的救回一节 / 额度用尽即停 / 不新增调用）在 `test_demo_writer_batching` 的 G3。
    def _dest(**overrides) -> PW.CandidateReproposalDestination:
        body = {"candidate_id": "ccand_a", "claim_text": "候选文本", "fact_type": "business",
                "destination": "carried_verbatim", "next_candidate_ids": ("ccand_b",),
                "next_fact_types": ("business",)}
        body.update(overrides)
        return PW.CandidateReproposalDestination(**body)

    def _trace(**overrides) -> PW.RejectionReproposalTrace:
        body = {"from_attempt": 1, "to_attempt": 2,
                "note_version": PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION,
                "original_candidate_ids": ("ccand_a",), "next_candidate_ids": ("ccand_b",),
                "destinations": (_dest(),)}
        body.update(overrides)
        return PW.RejectionReproposalTrace(**body)

    check(PW.MAX_DIRECTED_REPROPOSAL_PASSES == 1
          and PW.REPROPOSAL_TRIGGER_KINDS == ("path_b_high_risk_surface",
                                              "path_b_ineligible_material_scope",
                                              "path_b_history_only_current_state",
                                              "path_b_unproven_current_state"),
          "定向重提案必须**有界**（一次，四种触发共用），且只对四类「候选自己能改」的路径 B 面"
          "拒绝排定：高风险表面、支撑资格、期间/来源角色（`srsc-1`）、独立支撑结论（`srsc-2`）"
          "（其余 kind 要么没有可信的逐候选身份，要么原因不在候选身上；把它们也算进来就是对所有"
          "失败重问一遍）")
    check(set(PW.CANDIDATE_REPROPOSAL_DESTINATIONS)
          == {"carried_verbatim", "carried_rebound", "carried_ambiguous", "not_reexpressed"},
          "跨修订去向词表必须是那四档闭合集：多一档（如「被改写了」）就是发明归属，"
          "少一档则「同文多条」会被迫猜")
    check(_dest().destination == "carried_verbatim" and _trace().to_attempt == 2,
          "正向对照：自洽的去向记录必须能构造出来（否则下面每条反例都可能是误拒）")
    expect_error(lambda: _dest(destination="rewritten"),
                 PW.PackWriterError, "词表之外的去向必须被拒", needle="不在")
    expect_error(lambda: _dest(destination="carried_verbatim",
                               next_fact_types=("other_type",)),
                 PW.PackWriterError, "「逐字保留」与「类型变了」不得同时成立", needle="与明细不符")
    expect_error(lambda: _dest(destination="not_reexpressed",
                               next_candidate_ids=("ccand_b",)),
                 PW.PackWriterError, "列了同文候选就不得声称「没有重新表达」", needle="与明细不符")
    expect_error(lambda: _dest(destination="carried_ambiguous"),
                 PW.PackWriterError,
                 "同文多条才可记 `carried_ambiguous`（一条时必须判「原样」还是「换了绑定」）",
                 needle="与明细不符")
    expect_error(lambda: _dest(next_candidate_ids=("ccand_b", "ccand_b"),
                               next_fact_types=("business", "other")),
                 PW.PackWriterError, "同文候选 id 不得重复", needle="重复")
    expect_error(lambda: _trace(to_attempt=3),
                 PW.PackWriterError, "终点必须是**紧接的下一轮**", needle="紧接的下一轮")
    expect_error(lambda: _trace(original_candidate_ids=("ccand_a", "ccand_c")),
                 PW.PackWriterError, "去向的键集必须恰好等于原束完整有序候选身份", needle="键集")
    expect_error(lambda: _trace(next_candidate_ids=("ccand_z",)),
                 PW.PackWriterError, "去向不得指向下一修订里不存在的 id", needle="不在下一修订")
    expect_error(lambda: PW.ProposalSetRejectionRecord(
        attempt=1, rejection_kind="path_b_high_risk_surface", rejection_detail="d",
        candidate_ids=("ccand_a",), candidate_audit=(
            PW.RejectedCandidateAudit(candidate_id="ccand_a",
                                      reasons=("path_b_high_risk_surface",),
                                      surfaces=("3",)),),
        answered_by_attempt=1),
        PW.PackWriterError, "定向重提案不得排定到被拒的那一轮自己", needle="之后的某一轮")
    expect_error(lambda: PW.ProposalSetRejectionRecord(
        attempt=1, rejection_kind="narrative_gate_blocking", rejection_detail="d",
        candidate_ids=("ccand_a",), candidate_audit=(
            PW.RejectedCandidateAudit(candidate_id="ccand_a", reasons=("gate_blocking",),
                                      rule_ids=("r-1",)),),
        answered_by_attempt=2),
        PW.PackWriterError, "非触发类拒绝不得排定定向重提案", needle="不得排定")
    expect_error(lambda: PW.ProposalSetRejectionRecord(
        attempt=1, rejection_kind="path_b_high_risk_surface", rejection_detail="d",
        candidate_ids=("ccand_a",), candidate_audit=(
            PW.RejectedCandidateAudit(candidate_id="ccand_a",
                                      reasons=("path_b_high_risk_surface",),
                                      surfaces=("3",)),),
        answered_by_attempt=3, reproposal=_trace()),
        PW.PackWriterError, "去向必须正好落在被排定的那一轮上", needle="正好是")
    ok = PW.ProposalSetRejectionRecord(
        attempt=1, rejection_kind="path_b_high_risk_surface", rejection_detail="d",
        candidate_ids=("ccand_a",), candidate_audit=(
            PW.RejectedCandidateAudit(candidate_id="ccand_a",
                                      reasons=("path_b_high_risk_surface",),
                                      surfaces=("3",)),),
        answered_by_attempt=2, reproposal=_trace())
    check(ok.to_dict()["answered_by_attempt"] == 2
          and ok.to_dict()["reproposal"]["destinations"][0]["destination"]
          == "carried_verbatim",
          "自洽的记录必须能被序列化进产物（读的人不必回头读源码）")
    # 单向：**已排定但那一轮没能产出结构化提案集**时 trace 必须缺席——不得用空 trace 冒充
    # 「逐条都追过了」。
    scheduled_only = PW.ProposalSetRejectionRecord(
        attempt=1, rejection_kind="path_b_high_risk_surface", rejection_detail="d",
        candidate_ids=("ccand_a",), candidate_audit=(
            PW.RejectedCandidateAudit(candidate_id="ccand_a",
                                      reasons=("path_b_high_risk_surface",),
                                      surfaces=("3",)),),
        answered_by_attempt=2)
    check(scheduled_only.to_dict()["reproposal"] is None
          and scheduled_only.to_dict()["answered_by_attempt"] == 2,
          "「已排定但下一轮未成形」必须如实写成「排定了、无去向」，而不是空 trace")
    check(PW._directed_reproposal_note.__doc__ is not None
          and PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION == "hrrp-5",
          "定向说明的版本号必须是一个可回归的常量（说明文本是输入面的一部分）")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

"""Eval: M930-2 标题导航、树材料重切与 tree inspection 工具。

用法: python -m evals.test_demo_tree_materials

覆盖：
- 导航只读：索引不含 Evidence；导航键取自**真实标题树自身的节点标题**（不写答案关键词）；
  选定终态必须给出真实 node 与子树，fallback 必须携带**已登记**原因；
  用别的 profile 导航 / 传入非法对象一律 fail-closed；
- 材料重切：材料必须是**精确定位的 OutlineSpan**（span-local 与 evidence 字符区间都在
  payload 信封里），逐位同序、id 唯一、候选账目闭合（候选 = 材料 + 结构 gap + 内容处置），
  gap 原因全部来自封闭集合；跨公司绑定 / 空快照 / 非 current 绑定一律 fail-closed；
- payload 解析器**从不读盘**：按重算哈希建索引，dangling 返回 None（不伪装成"未找到"），
  命中但 authority / 依赖指纹 / locator 不符立即报错；
- 工具只注册进**既有** registry；声明身份不符 → SOURCE_UNTRUSTED，未知 node →
  INVALID_ARGUMENTS，超限只减少候选（绝不截断 span 文本）；非 live 源不得建会话。

公司无关：样本取自 `data/samples/<company>/...` 既有样例目录，样本缺失时如实 skip。
不调 LLM、不联网、不写任何库。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import live_span_source as LSS
from document_structure import navigation as NAV
from document_structure import synopsis as SY
from harness import tree_materials as TM
from harness import tree_tools as TT
from tools import adapters as A
from tools import contracts as C

SESSION = TT.TREE_INSPECT_TOOL_NAME
REPO = Path(__file__).resolve().parent.parent
#: 与 runtime 同一套停止原因（本模块不自造词表）。
STOP_PATH_NOT_IMPLEMENTED = "PATH_NOT_IMPLEMENTED"


def _sample_pdf() -> Path | None:
    import hashlib
    for path in sorted(Path("data/samples").glob("*/announcements/*.pdf")):
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

    pdf = _sample_pdf()
    evidence_db = Path("data/evidence.db")
    # ============ 0. 非公司专用夹具的同一正向链（与 live 样本无关）========
    # 放在最前，且**不**与 live 部分共用 skip 条件：夹具正向链是 §十三 要求的
    # 「同一材料重切 / payload 解析器 / Pack 身份 / 读门」在非特定公司上的正面证据，
    # 缺 live 样本时它仍然必须跑。
    skipped += _fixture_positive(check, expect_error, details)

    if pdf is None or not evidence_db.exists():
        skipped += 1
        details.append("SKIP 缺少 live 样本或 data/evidence.db（夹具正向链已单独跑过）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    import hashlib
    from evidence import store as estore
    estore._db_path = evidence_db.resolve()
    company, sha = pdf.parents[1].name, hashlib.sha256(pdf.read_bytes()).hexdigest()
    set_version = estore.current_evidence_set_ro(
        evidence_db.resolve(), company, pdf.stem, "sha256-" + sha[:16])
    if not isinstance(set_version, str) or set_version == "":
        skipped += 1
        details.append("SKIP Evidence 库里没有该文档的 current set（不猜值）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
        company_id=company, document_id=pdf.stem,
        document_version="sha256-" + sha[:16], raw_pdf_path=str(pdf),
        raw_pdf_sha256=sha, expected_current_evidence_set_version=set_version))

    with tempfile.TemporaryDirectory() as td:
        registry, session = A.build_tree_bound_registry(
            live_span_source=live, audit_dir=Path(td) / "audit")
        snapshot, outline = live.snapshot, live.document_outline
        binding = session.current_evidence
        batch = session.batch

        # ============ 1. 导航：只读、真实标题、终态封闭 ==================
        node_ids = sorted({n.node_id for n in outline.nodes})
        synopses = SY.build_navigation_synopses(
            node_ids=node_ids, spans=snapshot.spans, coverages=snapshot.coverages,
            dispositions=snapshot.dispositions, policy=live.qualification_policy)
        index = NAV.NavigationIndex(
            outline, synopses, span_node_ids={s.node_id for s in snapshot.spans})
        check(index.unavailable_reason is None, "真实树上导航索引可用")
        check(index.produces_coverage() is False,
              "导航索引不产生 coverage/authority")
        check(not hasattr(index, "materials") and not hasattr(index, "evidence_ids"),
              "导航索引不持有材料 / Evidence id")

        spans_by_node: dict[str, int] = {}
        for span in snapshot.spans:
            spans_by_node[span.node_id] = spans_by_node.get(span.node_id, 0) + 1
        node_by_id = {n.node_id: n for n in outline.nodes}
        target = max(spans_by_node, key=lambda k: spans_by_node[k])
        nav_key = node_by_id[target].title_normalized
        check(NAV.normalize_navigation_text(nav_key)
              == NAV.normalize_navigation_text(" " + nav_key.replace("（", "(") + " "),
              "导航文本归一化吸收空白与全/半角括号")

        profile = NAV.build_navigation_profile(
            (_aspect(nav_key),), contract_version="v1",
            contract_fingerprint="c" * 64)
        entry = profile.entries[0]
        check(entry.aspect_id == "a1" and entry.nav_keys,
              "导航 profile 为 aspect 派生出非空导航键")
        decision = NAV.navigate(index, entry, profile=profile)
        check(decision.status in NAV.decision_statuses,
              f"导航终态来自封闭集合（{decision.status}）")
        check(decision.status == "selected"
              and decision.selected_node_id == target,
              "导航键取自该 node 自身标题 → 选中该 node")
        check(set(decision.subtree_node_ids) <= set(index.node_ids)
              and decision.subtree_node_ids,
              "子树节点都在本树上且非空")
        check(decision.outline_id == outline.outline_id
              and decision.profile_id == profile.profile_id,
              "终态绑定真实 outline 与 profile 身份")
        check(all(c.node_id in index.node_ids for c in decision.ranked),
              "候选打分的 node 都在本树上")
        check(not decision.produces_coverage(),
              "导航终态不产生 coverage（导航只导航）")

        # 反例：用别的 profile 导航
        other_profile = NAV.build_navigation_profile(
            (_aspect(nav_key, aspect_id="a-other"),), contract_version="v1",
            contract_fingerprint="c" * 64)
        expect_error(lambda: NAV.navigate(index, entry, profile=other_profile),
                     NAV.NavigationError, "aspect 不在 profile 内必须拒",
                     needle="不属于 profile")
        expect_error(lambda: NAV.navigate(object(), entry, profile=profile),
                     NAV.NavigationError, "非 NavigationIndex 必须拒")
        expect_error(lambda: NAV.navigate(index, entry, profile=profile,
                                          limits={"max_candidates": 1}),
                     NAV.NavigationError, "非 NavigationLimits 必须拒")
        # 反例：索引不可用（无任何简介）→ 显式 fallback，原因必须已登记
        blind = NAV.NavigationIndex(outline, ())
        check(blind.unavailable_reason is not None,
              "无简介的索引必须给出不可用原因")
        blind_decision = NAV.navigate(blind, entry, profile=profile)
        check(blind_decision.is_fallback()
              and blind_decision.fallback_reason in NAV.FALLBACK_REASONS
              and blind_decision.selected_node_id is None,
              "结构不可用时走显式 fallback（不猜 node）")

        # ============ 2. 材料重切：精确定位 + 账目闭合 ====================
        check(batch.materials, "真实树上产出材料")
        check(len(batch.materials) == len(batch.payload_records)
              == len(batch.span_ids) == len(batch.material_ids),
              "材料 / payload / span / id 逐位同序等长")
        check(len(set(batch.material_ids)) == len(batch.material_ids),
              "材料 id 唯一")
        blocks_by_id = {b.evidence_block_id: b for b in live.evidence_blocks}
        out_of_range: list[str] = []
        for material, span_id in zip(batch.materials, batch.span_ids):
            envelope = json.loads(
                batch.payload_bytes_by_id(material.content_hash).decode("utf-8"))
            tree = envelope.get("tree_material") or {}
            check(envelope.get("envelope_kind") == TM.TREE_MATERIAL_ENVELOPE_KIND,
                  f"{span_id}: payload 信封类型正确")
            check(material.authority_assessment is not None
                  and bool(material.authority_assessment.evidence_id)
                  and material.material_type == TM.TREE_MATERIAL_TYPE,
                  f"{span_id}: 材料类型正确且带权威评估的父 Evidence id")
            check(bool(tree.get("span_local_char_range")),
                  f"{span_id}: 材料是 span-local 精确定位（不是整块 Evidence）")
            rng = tuple(tree.get("evidence_char_range") or ())
            block = blocks_by_id.get(material.authority_assessment.evidence_id)
            if (not rng or block is None or not (0 <= rng[0] < rng[1] <= len(block.text))):
                out_of_range.append(f"{span_id}:{'|'.join(str(x) for x in rng)}")
        check(not out_of_range,
              f"材料 locator 必须落在父 Evidence 块文本区间内（越界 {out_of_range[:3]}）")
        # 一份材料 = 一个真实 Evidence 块（R2）：跨块 span 因此产出**逐块片段**材料，
        # 段数必须等于它跨的块数——多于块数是重复准入，少于块数是丢段。
        admitted_blocks: dict = {}
        for component in snapshot.components:
            if not getattr(component, "admitted", False):
                continue
            admitted_blocks.setdefault(component.span_id, set()).add(
                component.evidence_block_id)
        per_span_expected = {sid: len(blocks)
                             for sid, blocks in admitted_blocks.items()}
        mismatch = [(sid[:12], len(batch.material_ids_for_span(sid)),
                     per_span_expected.get(sid))
                    for sid in set(batch.span_ids)
                    if len(batch.material_ids_for_span(sid)) != per_span_expected.get(sid)]
        check(not mismatch,
              f"每个候选 span 的段数 = 它跨的 Evidence 块数（不重复准入、不丢段、不整块兜底）"
              f"：{mismatch[:3]}")

        counts = batch.gap_reason_counts()
        check(set(counts) <= set(TM.TREE_MATERIAL_GAP_REASONS),
              "gap 原因全部来自封闭集合")
        identity = batch.identity
        # §二 2.3 之后候选有三条互斥去路：成为材料 / 结构 gap / 内容处置（孤立标题、版式碎片、
        # 读不出状态的勾选行）。账目仍是**闭合**的——只是从两项和改成三项和；少了第三项，
        # 被内容处置掉的候选会被读成"从没进过人口"。
        check(identity["candidate_span_count"] == identity["material_span_count"]
              + identity["gap_count"] + identity["content_disposition_count"],
              "候选账目闭合：候选 = 材料 + 结构 gap + 内容处置")
        # 材料的**条数**与**产出材料的 span 数**分开登记：跨块 span 一份 span 出多份片段材料，
        # 少了后者，跨块文档上的守恒账就会被读成不闭合。
        check(identity["material_span_count"] == len(set(batch.span_ids))
              and identity["material_count"] == len(batch.materials)
              and identity["material_span_count"]
              + identity["multi_block_material_count"] - identity["multi_block_span_count"]
              == len(batch.materials)
              and identity["gap_count"] == len(batch.gaps)
              and identity["content_disposition_count"] == len(batch.content_dispositions),
              "身份投影的记录数与实际一致（材料条数 / 产出 span 数 / 跨块增量可对账）")
        check(all(g.span_id and g.detail for g in batch.gaps),
              "每条 gap 都指向具体 span 且带原因说明（不静默丢弃）")

        # ============ 3. payload 解析器：重算为准，读盘不可信 =============
        resolver = session.resolver
        ref = batch.materials[0].payload_ref
        resolved = resolver.resolve(ref)
        check(resolved is not None
              and resolved.payload_bytes
              == batch.payload_bytes_by_id(batch.materials[0].content_hash),
              "命中 ref 的解析结果就是本会话重切出的字节")
        check(resolver.resolve(_ref_replace(ref, object_type="table_context")) is None,
              "非本类型 ref → None（不越界代答）")
        check(resolver.resolve(_ref_replace(ref, version="v0")) is None,
              "非本解析语义版本 → None")
        check(resolver.resolve(_ref_replace(ref, content_hash="0" * 64)) is None,
              "重算不出的哈希 → None（dangling 不伪装成未找到）")
        expect_error(lambda: resolver.resolve(_ref_replace(ref, authority_identity="x")),
                     TM.TreeMaterialError, "命中但 authority 不符必须报错",
                     needle="authority_identity")

        # ============ 4. 构建期 fail-closed ==============================
        expect_error(lambda: TM.build_tree_materials(
            snapshot=snapshot, blocks=live.evidence_blocks,
            current_evidence=dict(binding.to_dict())),
            TM.TreeMaterialError, "非 CurrentEvidenceBinding 必须拒",
            needle="CurrentEvidenceBinding")
        expect_error(lambda: TM.build_tree_materials(
            snapshot=_EmptySnapshot(live.qualification_policy),
            blocks=live.evidence_blocks, current_evidence=binding),
            TM.TreeMaterialError, "空 span 快照必须拒（不得产出空材料批次冒充成功）",
            needle="spans 为空")
        expect_error(lambda: TM.TreeMaterialGap("s", None, "not_a_reason", "d"),
                     TM.TreeMaterialError, "未登记的 gap 原因必须拒")
        expect_error(lambda: TM.evidence_binding_from_snapshot(
            _Status("superseded")), TM.TreeMaterialError,
            "非 current Evidence 快照不得产出正式材料", needle="current")
        # 反例：绑定到别的公司 → 父块身份不符 → 全部进 gap，0 材料
        leaked = TM.build_tree_materials(
            snapshot=snapshot, blocks=live.evidence_blocks,
            current_evidence=TM.CurrentEvidenceBinding(
                **dict(binding.to_dict(), company_id="000001")))
        # 注：跨公司绑定后逐块父块身份都不符，单块与跨块 span 一律走
        # `evidence_block_unavailable`（跨块 span 的重切核验里就查父块身份）。这里断言的是
        # **结果**：零材料 + 账目仍然闭合 + 原因全部来自封闭集合 + 逐 span 有登记。
        check(leaked.materials == (),
              "跨公司绑定时不得产出任何材料")
        check(leaked.gaps and all(g.span_id for g in leaked.gaps),
              "跨公司 gap 仍逐 span 登记（不静默丢弃）")
        check(all(g.reason in TM.TREE_MATERIAL_GAP_REASONS for g in leaked.gaps),
              "跨公司 gap 原因仍来自封闭集合")
        leaked_identity = leaked.identity
        check(leaked_identity["candidate_span_count"] == leaked_identity["material_span_count"]
              + leaked_identity["gap_count"] + leaked_identity["content_disposition_count"]
              and leaked_identity["material_span_count"] == 0
              and leaked_identity["gap_count"] == len(leaked.gaps),
              "跨公司时账目仍闭合（候选 = 0 材料 + 全部 gap + 内容处置）")

        # ============ 5. 工具：注册进既有 registry + 参数门 ===============
        spec = registry.get(SESSION)
        check(spec is not None and spec.version == TT.TREE_INSPECT_TOOL_VERSION,
              "tree inspection 工具已注册进既有 default registry")
        check(TT.TREE_INSPECT_SPEC.input_schema.get("additionalProperties") is False,
              "工具入参不接受额外字段（additionalProperties=False）")
        expect_error(lambda: TT.TreeInspectionSession(object()), TT.TreeToolError,
                     "非 live 源不得建会话", needle="LiveVerifiedSpanSource")

        args = dict(session.document_identity(), need_id="need-1",
                    aspect_id="a1", topic_id="t_company_business",
                    node_ids=[target], max_spans=4)
        result = session.inspect(args)
        check(result.status in ("SUCCESS", "PARTIAL") and result.data["candidates"],
              f"按真实 node 取材料（status={result.status}）")
        first = result.data["candidates"][0]
        # v3：候选粒度 = **材料**（一份材料 = 一个真实 Evidence 块）。跨块 span 的候选文本是
        # **该段的精确片段**，不是整段 span 正文——候选与它引用的材料必须是同一段文字，否则
        # "claim 逐字来自材料"就不成立。片段必须逐字等于 span 正文在它本地区间上的那段。
        first_span_text = live.span_by_id(first["span_id"]).normalized_text
        lo, hi = first["span_local_char_range"]
        check(first["span_id"] in set(batch.span_ids)
              and first["material_id"] in set(batch.material_ids)
              and first["text"] == first_span_text[lo:hi],
              "候选文本就是该材料那一段的正文（逐字切片，未复制、未改写）")
        check((first["span_split"] is None) == (hi - lo == len(first_span_text)),
              "单块材料的候选不带 span_split；跨块片段带段身份（第几段 / 共几段）")
        check(first["evidence_char_range"] and first["span_local_char_range"],
              "候选带双重定位（父 Evidence 区间 + span-local 区间）")
        check(result.evidence_ids
              and all(eid == first["parent_evidence_id"] for eid in result.evidence_ids),
              "证据 id 是真实父 Evidence 块 id")
        check(all(c["material_id"] in set(batch.material_ids)
                  for c in result.data["candidates"]),
              "候选只引用本会话重切出的材料 id")

        # 反例：未知 node / 声明身份不符 / 超限只减少候选
        bad_node = session.inspect(dict(args, node_ids=["node-not-exist"]))
        check(bad_node.status == "FATAL_ERROR"
              and bad_node.error_code == "INVALID_ARGUMENTS",
              "未知 node 必须 INVALID_ARGUMENTS")
        bad_doc = session.inspect(dict(args, document_version="sha256-0000000000000000"))
        check(bad_doc.status == "FATAL_ERROR"
              and bad_doc.error_code == "SOURCE_UNTRUSTED",
              "声明身份与 run-bound 源不符必须 SOURCE_UNTRUSTED")
        limited = session.inspect(dict(args, max_spans=1))
        check(len(limited.data["candidates"]) == 1
              and limited.data["skipped"]
              and any(s.get("reason") == "over_max_spans"
                      for s in limited.data["skipped"]),
              "超 max_spans 只减少候选并显式登记（不截断文本）")
        check(limited.data["candidates"][0]["text"]
              == result.data["candidates"][0]["text"]
              and limited.data["candidates"][0]["material_id"]
              == result.data["candidates"][0]["material_id"],
              "受限不截断文本：第一条候选与放开上界时逐字相同（同一份材料）")

        # 反例：经 registry.execute 的正式入口同样拒绝额外字段
        call = C.ToolCall(call_id="c1", tool_name=SESSION,
                          arguments=dict(args, unexpected="x"),
                          idempotency_key="ik-1", need_id="need-1",
                          batch_id="batch-1")
        executed = registry.execute(call, route="DIRECT_EVIDENCE", run_id="eval")
        check(executed.status != "SUCCESS" and executed.error_code,
              "registry 正式入口拒绝额外字段（fail-closed）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


# ---------------------------------------------------------------------------
# §十三：非公司专用夹具上的同一正向链
# ---------------------------------------------------------------------------

def _fixture_positive(check, expect_error, details) -> int:
    """仓库内版本化夹具（`evidence_gateway.FIXTURE_ROOT_RELPATH`）上的正向链。

    证明「材料重切 → payload 解析器 → TopicResearchPack 身份 → 完整 Pack set 读门」
    这条链**不依赖任何特定公司/文档**：全部身份（公司、文档、版本、Evidence 集合、
    页码、Evidence id、aspect、task）都现场取自夹具 manifest 与冻结 Contract 投影，
    本函数里没有任何公司代号、页码、表号或 Evidence id 字面量。

    **本正向链不宣称 aspect 级研究闭环**：夹具没有 live 会话，也就没有按 aspect 的
    标题导航与工具调用，因此逐 aspect 结果如实记为 blocked + 登记缺口，Pack 里带的
    是同一套夹具材料。它证明的是材料/解析器/身份/读门四项，不是内容覆盖。

    返回 skip 计数（夹具缺失时如实 skip，不静默算过）。
    """
    import dataclasses

    from contracts.loader_v2 import load_contract_v2
    from document_structure import evidence_gateway as EG
    from document_structure import span_builder as SB
    from document_structure.schema import PageLayout
    from evals import tree_stage_env as STAGE
    from harness import r2_dependencies as R2
    from harness import source_policy_resolver as SPR
    from harness import topic_runtime as TR
    from harness import topic_schema as TS
    from planning import demo_scope as SC
    from planning import schema as PS
    from sections import pack_set as PSet

    if not EG.fixture_root_dir().is_dir():
        details.append(f"SKIP 版本化夹具目录不存在（{EG.FIXTURE_ROOT_RELPATH}）")
        return 1
    root = EG.load_fixture_root()
    company, document_id = root["company_id"], root["document_id"]
    document_version, set_version = root["document_version"], root["evidence_set_version"]
    check(bool(company) and bool(document_id) and bool(document_version)
          and bool(set_version),
          "夹具身份逐项取自 manifest（测试不写死公司/文档/版本）")
    check(len(root["members"]) == len(EG.FIXTURE_MEMBER_ROLES),
          "夹具成员清单与登记的成员角色数量一致")

    # ---- 1. 夹具 Evidence：签发 → 取数 → current 绑定 --------------------
    authority = EG._issue_fixture_evidence_authority(root)
    ev_snapshot, blocks = authority.load_snapshot_and_blocks(
        company_id=company, document_id=document_id,
        document_version=document_version)
    binding = TM.evidence_binding_from_snapshot(ev_snapshot)
    check(binding.is_current is True and binding.company_id == company
          and binding.document_id == document_id
          and binding.document_version == document_version
          and binding.evidence_set_version == set_version,
          "夹具 current 绑定与 manifest 逐项一致（跨公司/文档/集合一律不符）")
    expect_error(lambda: authority.load_snapshot_and_blocks(
        company_id=company + "-other", document_id=document_id,
        document_version=document_version),
        EG.EvidenceGatewayError, "夹具不得跨公司复用（fail-closed）")

    # ---- 2. 夹具 span 快照（冻结阶段核验，不绕过阶段门）------------------
    pdf_path = REPO / root["source_pdf"]["relpath"]
    if not pdf_path.is_file():
        details.append("SKIP 夹具冻结 PDF 不在仓库内（不猜测字节）")
        return 1
    layout = PageLayout.from_dict(json.loads(
        (EG.fixture_root_dir() / "page_layout.json").read_text(encoding="utf-8")))
    with STAGE.simulated_a_environment():
        handoff = SB._issue_fixture_ts3_handoff(
            raw_pdf=pdf_path.read_bytes(), expected_layout=layout,
            company_id=company, document_id=document_id, fixture_root=root)
        snapshot = SB._build_from_pinned_handoff(handoff, stage="distribution_only")
    check(len(snapshot.spans) > 0,
          f"夹具 span 快照非空（{len(snapshot.spans)} 条）")

    # ---- 3. 同一材料重切 + 同一 payload 解析器 ---------------------------
    resolver = TM.TreeMaterialPayloadResolver(
        snapshot=snapshot, blocks=blocks, current_evidence=binding)
    batch = resolver.batch
    check(len(batch.materials) > 0,
          f"夹具上真的重切出合格材料（{len(batch.materials)} 条）")
    check(all(m.material_type == TM.TREE_MATERIAL_TYPE for m in batch.materials),
          "夹具材料同样是精确定位的 OutlineSpan（不是整块 Evidence）")
    auth_bad, payload_bad, envelope_bad = [], [], []
    for material in batch.materials:
        if TS.recompute_authority_verdict(material.authority_assessment) != "authoritative":
            auth_bad.append(material.material_id[:12])
        try:
            TS.verify_material_payload_ref(material.payload_ref, resolver)
        except TS.SchemaValidationError:
            payload_bad.append(material.material_id[:12])
        envelope = json.loads(
            batch.payload_bytes_by_id(material.content_hash).decode("utf-8"))
        tree = envelope.get("tree_material") or {}
        if not (tree.get("span_local_char_range")
                and tree.get("evidence_char_range")):
            envelope_bad.append(material.material_id[:12])
    check(not auth_bad,
          f"夹具材料权威可重算且为 authoritative（不合格 {auth_bad[:3]}）")
    check(not payload_bad,
          f"夹具材料的 payload_ref 都能由同一解析器独立重切（失败 {payload_bad[:3]}）")
    check(not envelope_bad,
          f"夹具材料带双重精确区间（缺失 {envelope_bad[:3]}）")
    check(set(batch.gap_reason_counts()) <= set(TM.TREE_MATERIAL_GAP_REASONS),
          "夹具 gap 原因同样来自封闭集合")
    fx_identity = batch.identity
    check(fx_identity["candidate_span_count"] == fx_identity["material_span_count"]
          + fx_identity["gap_count"] + fx_identity["content_disposition_count"],
          "夹具候选账目闭合：候选 = 材料 + 结构 gap + 内容处置")
    details.append(
        f"NOTE §十三 夹具实测: 材料 {len(batch.materials)} / gap {len(batch.gaps)}"
        f" {batch.gap_reason_counts()}")

    # ---- 4. 夹具公司的真实 Contract v2 投影（同一公开入口）--------------
    profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
    policy_resolver = SPR.FrozenSourcePolicyResolver.from_asset(
        REPO / profile.source_policy_asset)
    contract = load_contract_v2(str(REPO / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_m930_13_fixture", company_id=company, company_name=company,
        credit_type="general", report_as_of="2026-06-30", contract_version="v2")
    manifest = SC.build_scope_input_manifest(profile, business, {
        "case_input_id": "case_m930_13_fixture",
        "document_id": document_id, "document_version": document_version,
        "raw_pdf_sha256": root["source_pdf_sha256"],
        "current_evidence_set_version": set_version,
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64})
    projection = SC.project_contract_v2_scope(contract, profile, manifest)
    SC.verify_demo_projection(projection, contract)
    check(projection.company_id == company,
          "投影公司就是夹具公司（身份不来自公司专用分支）")
    reqs = {r.topic_id: r for r in projection.requirements}
    tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
    task = tasks["company"]
    task_reqs = tuple(reqs[t] for t in task.topic_ids)
    check(len(task_reqs) >= 2 and all(r.company_id == company for r in task_reqs),
          "真实 SectionTask 跨多个 selected topic 且都绑夹具公司")
    check(all(r.dependency_versions == TS.build_current_dependency_versions(
        contract_version=r.contract_version, source_policy_version=r.source_policy_version)
        for r in task_reqs),
        "夹具投影的依赖集就是当前依赖集（不是历史遗留版本）")

    # ---- 5. 逐 topic 装配并提交（同一 Store 写门）------------------------
    budget = TR.ResearchBudgetPolicy(
        policy_id=profile.budget_policy_id, version=profile.budget_policy_version,
        tier="demo_backbone", max_need_rounds_per_aspect=1, max_need_rounds_per_topic=1,
        max_tool_calls_per_topic=1, max_tokens_per_topic=1, max_llm_calls_per_topic=1,
        max_elapsed_ms_per_topic=1, tree_max_spans_per_aspect=1,
        tree_max_chars_per_span=1)

    def _pack_for(requirement, *, resolver_, stop_reason=STOP_PATH_NOT_IMPLEMENTED):
        ledger = TR.TopicBudgetState(policy=budget)
        ledger.note_stop(stop_reason)
        gaps, results = [], []
        for aspect in requirement.aspects:
            impact = next((i for i in aspect.impact_scope if i in TS.GAP_IMPACTS),
                          TS.GAP_IMPACTS[0])
            gap = TS.ResearchGap(
                unresolved_id="gap-fx-" + aspect.aspect_id,
                aspect_ids=(aspect.aspect_id,), reason_code="unresolved",
                detail=("夹具正向链只证明材料重切 / payload 解析 / Pack 身份 / 读门，"
                        f"不宣称 aspect 级检索闭环：{aspect.requirement_text[:60]}"),
                attempted_need_ids=(), blocking=True, impact=impact)
            gaps.append(gap)
            results.append(TS.AspectResearchResult(
                aspect_id=aspect.aspect_id, question_ids=(aspect.question_id,),
                requirement_snapshot=aspect, status="blocked",
                supported_fact_ids=(), material_ids=(), attempted_need_ids=(),
                unresolved_ids=(gap.unresolved_id,), not_found_audit_id=None))
        process, coverage, derivation = TS.derive_pack_status(
            tuple(a.aspect_id for a in requirement.aspects), tuple(results),
            stop_reason=ledger.stop_reason)
        # M930-3 门 1：每个 material 恰一条 RMD（本夹具无 fact，故 candidate/qualified 三族为空）。
        aspect_ids = tuple(a.aspect_id for a in requirement.aspects)
        dispositions = tuple(
            TS.build_material_disposition(
                m, aspect_ids=aspect_ids, admission_state="admitted",
                retention_state="retained", source_validation="validated",
                reason_code="aspect_material_admitted",
                reason_proof="tree material fixture",
                policy_version=TS.MATERIAL_DISPOSITION_VERSION)
            for m in resolver_.batch.materials)
        return TS.TopicResearchPack(
            schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="",
            run_id="m930-13-fixture", task_id=requirement.task_id,
            company_id=requirement.company_id, report_as_of=requirement.report_as_of,
            contract_version=requirement.contract_version,
            contract_fingerprint=requirement.contract_fingerprint,
            source_policy_version=requirement.source_policy_version,
            section_id=requirement.section_id, topic_id=requirement.topic_id,
            question_ids=requirement.question_ids, aspect_results=tuple(results),
            materials=resolver_.batch.materials, facts=(),
            material_dispositions=dispositions, fact_candidates=(),
            fact_qualification_decisions=(), external_facts=(), contract_gaps=(),
            research_blocks=(), outcome_refs=(),
            external_funnel=None, conflicts=(), not_found_audits=(),
            unresolved=tuple(gaps), usage=ledger.to_usage_snapshot(),
            uncertain_calls=(), process_status=process, coverage_status=coverage,
            status_derivation=derivation,
            dependency_fingerprint=requirement.dependency_fingerprint(),
            source_set=TS.DocumentSourceSet.single_document(
                company_id=requirement.company_id, document_id="doc-tree-materials-fixture",
                document_version="dv-1", evidence_set_version="esv-1"))

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "fixture.db"
        adapter = TR.SqliteTopicStoreAdapter(db)
        adapter.init()
        _, _sp, scv, sev = R2.build_r2_material_dependencies(db)

        expect_error(lambda: adapter.commit_pack(
            _pack_for(task_reqs[0], resolver_=resolver), task_reqs[0],
            _NullResolver(), policy_resolver, scv, sev),
            TS.SchemaValidationError,
            "payload 不可解析的 Pack 不得提交（fail-closed）", needle="dangling")

        commits = []
        for requirement in task_reqs:
            commits.append(adapter.commit_pack(
                _pack_for(requirement, resolver_=resolver), requirement,
                resolver, policy_resolver, scv, sev))
        check(len(commits) == len(task_reqs)
              and all(c.pack_id for c in commits)
              and all(c.material_count == len(batch.materials) for c in commits),
              "每个真实 topic 各提交一个 current Pack（都带夹具材料）")
        check(len({c.pack_id for c in commits}) == len(commits),
              "不同 topic 的 Pack id 互不相同")

        # ---- 6. 同一完整 Pack set 读门：正向 ----------------------------
        reader = PSet.SqlitePackSetStore(path=db, resolver=resolver)
        good = PSet.resolve_pack_set(task, task_reqs, reader)
        check(good.gate_version == PSet.PACK_SET_GATE_VERSION
              and good.topic_ids == task.topic_ids
              and tuple(p.topic_id for p in good.packs) == good.topic_ids,
              "夹具 Pack set 通过读门且顺序 = 真实 SectionTask.topic_ids")
        check(all(p.company_id == company for p in good.packs),
              "通过读门的 Pack 全部绑夹具公司")
        check(len(good.materials()) == len(batch.materials) * len(task_reqs),
              "读门回读到的材料数与提交数一致（材料没被挑拣或丢弃）")
        check(good.aspect_ids()
              == tuple(a.aspect_id for r in task_reqs for a in r.aspects),
              "aspect 精确覆盖真实 requirement（不缺不重不混入）")
        check(len(good.gaps()) == sum(len(r.aspects) for r in task_reqs),
              "逐 aspect 缺口原样保留（读门不过滤 gap）")
        check(all(g.reason_code in TS.GAP_REASON_CODES for g in good.gaps()),
              "夹具缺口原因码仍来自封闭集合")
        details.append(
            f"NOTE §十三 读门实测: topic {list(good.topic_ids)} / 材料 "
            f"{len(good.materials())} / aspect {len(good.aspect_ids())} / gap "
            f"{len(good.gaps())} / facts {len(good.facts())}")

        # ---- 7. 反例：身份真的绑定在夹具公司上 --------------------------
        drifted = tuple(dataclasses.replace(r, company_id=company + "-other")
                        for r in task_reqs)
        try:
            PSet.resolve_pack_set(task, drifted, reader)
        except PSet.PackSetBlocked as e:
            reasons = [b.reason for b in e.blocks]
            check(reasons.count("no_current_pack") == len(task_reqs),
                  f"换成别的公司读不到夹具 current（实得 {reasons}）")
        except Exception as e:  # noqa: BLE001
            check(False, f"换公司必须按读门 block，实抛 {type(e).__name__}: {str(e)[:80]}")
        else:
            check(False, "换公司读同一批 Pack 必须 fail-closed")

        # 反例：同一 Pod 的 ref 不得被另一个文档的解析器认领
        tampered = dataclasses.replace(
            resolver.batch.materials[0].payload_ref, content_hash="0" * 64)
        check(resolver.resolve(tampered) is None,
              "改哈希的夹具 ref → None（dangling 不伪装成未找到）")

    return 0


# ---------------------------------------------------------------------------
# 测试替身 / 小工具
# ---------------------------------------------------------------------------

class _NullResolver:
    """永远解析不出 payload 的解析器（证明「材料在库但 payload 不可重切」会被拒）。"""

    def resolve(self, payload_ref):
        return None


def _aspect(nav_key: str, *, aspect_id: str = "a1"):
    from harness import topic_schema as TS
    sha = lambda s: __import__("hashlib").sha256(s.encode()).hexdigest()  # noqa: E731
    return TS.TopicAspectRequirementSnapshot(
        aspect_id=aspect_id, question_id="q1", topic_id="t_company_business",
        requirement_text=nav_key, kind="fact_set", producer_kind="company",
        execution_path="direct", required_fields=(nav_key,),
        coverage_rules=("direct_support", "minimum_sources"), complete_set_rule="",
        evidence_requirement_ids=(TS.EvidenceRequirementRef(
            requirement_id="er-" + aspect_id, contract_sha256=sha("c"),
            requirement_fingerprint=sha("r"), schema_version="1",
            source_classes=()),),
        source_policy_ref=TS.SourcePolicyRef(
            policy_id="sp", policy_version="v1", content_fingerprint=sha("p")),
        time_scope="period", display_tier="required_body", content_role="paragraph",
        missing_policy="none", blocking_policy=(), applicability_policy=None,
        impact_scope=("subject",), output_destination="body", derived_from=(),
        business_review_status="none", contract_version="v1",
        contract_sha256=sha("c"), canonical_fingerprint=sha("canon"),
        dependency_fingerprint=sha("dep"))


def _ref_replace(ref, **changes):
    import dataclasses
    return dataclasses.replace(ref, **changes)


class _EmptySnapshot:
    """最小替身：只有一个空 spans 列表 + 自载策略（用于证明空树不被接受）。

    真实 `SpanBuildSnapshot` 自身就不允许 spans/coverages 不一致，故空树只能这样构造。
    """

    def __init__(self, policy) -> None:
        self.spans = ()
        self.components = ()
        self.coverages = ()
        self.qualification_policy = policy


class _Status:
    """最小替身：只提供 `evidence_binding_from_snapshot` 读取的 status。"""

    def __init__(self, status: str) -> None:
        self.status = status


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""Eval: M930-3 —— **表宿主人口**与 **table-only 返回**的真工具回归（v7 图侧单通道）。

用法: python -X utf8 -m evals.test_m930_3_table_host_population

这一批钉的是 `harness/tree_tools.py` 工具面上的两件事：

1. **人口口径。** 表对象的人口必须是"本次派发**已定位**的宿主块"，不是"本次产出过
   `role="body"` 候选的块"。因此：
   - 选中的宿主块里**有表、却零正文候选**时，表材料**必须**照常返回；
   - 与本次选中节点没有任何 component 归属关系的块**不得**混入（不按整篇扫表）；
   - `node_id` 为空的宿主块**不可归属**：即使把全树节点都选上，它也**不得**进人口；
   - span 侧的上界（`max_spans`）只限正文通道，**不得**连带吞掉表对象。
2. **table-only 返回。** 有表材料、而无正文候选时，`status` / `error_code` /
   `evidence_ids` / `message` 必须如实表示"表在、来源在"，**不得**报
   `EMPTY`/`RETRIEVAL_EMPTY`；真的什么都没有时才报空。

**v7 的口径变化（必须先说清）。** 表对象的来源换成图侧单通道（`gto-3` → `gtm-1`）后，
"这一份文档里放行了多少张表"不再由本模块断言 —— 那是**逐表完整证明**（`tlp-1`）的结论，
而真实留存文档里每一张表的结构状态都还不是 `complete`（见下）。因此本模块：

* **断言**的是与放行数量无关的结构面：人口集、定序、两条反例、派发一致性、两路计数、
  span 上界隔离、fail-closed；
* **如实报出**（PASS 之外的 INFO / NOTE 行）逐表放行的现场读数 —— 零放行时按 §0.19 记
  **系统能力缺陷**，**不得**据此宣称已通过，也**不得**把缺表写成"该块没有表"。

本模块**只测真工具入口**（`TreeInspectionSession.inspect` 与 `located_table_hosts`），
不是只测读回页解析。样例（节点、块）在真文档上**动态发现**后再断言，不写死页码/节点 id；
样本或 Evidence 库缺失时如实 skip，不伪造通过。不调 LLM、不联网、不写任何库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import table_local_proof as TLP   # noqa: E402
from evaluation import business_material_readback as RB   # noqa: E402
from harness import tree_tools as TT                      # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "data/samples/300750/announcements"
EVIDENCE_DB = REPO / "data/evidence.db"

#: 演示主题的目标表所在文档（图侧对象逐张可查的那一份）。
TARGET_DOC = "NDSD_KCZ_2026"
#: 目标表表题的**结构**needle（不是答案关键词：只用来在现场指认"这三类表"，
#: 判据仍是对象自己的 `title_text` 与放行记录）。
TARGET_TITLE_NEEDLES = ("主营业务收入构成", "主营业务成本构成", "毛利润及毛利率")
#: §6 找"正文候选 ≥2 且含零候选表宿主"的现场时最多试多少个节点（按 span 数从多到少）。
_CAP_SEARCH_LIMIT = 60

_SESSIONS: dict = {}


def _session(document_id: str):
    """按文档建 live 只读会话（本模块内缓存；`build_index` 自己恢复全局库路径）。"""
    if document_id in _SESSIONS:
        return _SESSIONS[document_id]
    pdf = SAMPLES / f"{document_id}.pdf"
    if not pdf.exists() or not EVIDENCE_DB.exists():
        _SESSIONS[document_id] = None
        return None
    _index, _snapshot, _outline, blocks, live = RB.build_index(REPO, pdf)
    session = TT.TreeInspectionSession(live)
    session._ensure()
    _SESSIONS[document_id] = (session, blocks)
    return _SESSIONS[document_id]


def _inspect(session, selector: str, ids, **overrides):
    args = dict(session.document_identity(), need_id="need-population",
                aspect_id="company_business_main.revenue_breakdown",
                topic_id="t_company_business", **overrides)
    args[selector] = list(ids)
    return session.inspect(args)


def _facts(session):
    """本次会话的只读事实面：宿主块 → 放行记录 / 归属节点 / 是否产出过正文候选。"""
    batch = session.graph_table_batch()
    cand_blocks = {str(m.authority_assessment.evidence_id)
                   for m in session._batch.materials}
    nodes_by_block: dict = {}
    for component in session._source.snapshot.components:
        if component.node_id:
            nodes_by_block.setdefault(str(component.evidence_block_id), set()).add(
                str(component.node_id))
    span_nodes_by_block: dict = {}
    for bid, span_ids in self_spans(session).items():
        for span_id in span_ids:
            span = session._spans_by_id.get(span_id)
            node_id = getattr(span, "node_id", None)
            if node_id:
                span_nodes_by_block.setdefault(str(bid), set()).add(str(node_id))
    by_host: dict = {}
    for record in batch.records:
        for host in batch.release_hosts.get(str(record.get("release_id") or ""), ()):
            by_host.setdefault(str(host), []).append(record)
    return {"batch": batch, "cand_blocks": cand_blocks, "nodes_by_block": nodes_by_block,
            "span_nodes_by_block": span_nodes_by_block, "by_host": by_host}


def self_spans(session):
    return session._spans_by_evidence


def _ordered_hosts(blocks, facts):
    def page(host):
        block = blocks.get(host)
        return (getattr(block, "page_number", 0) or 0,
                getattr(block, "block_index", 0) or 0)
    return sorted(facts["by_host"], key=page)


def _material_ready(facts, host) -> bool:
    return any(facts["batch"].material_for_release(record) is not None
               for record in facts["by_host"][host])


def _first_zero_candidate_case(facts, blocks):
    """找一个"已定位、有表材料、却零正文候选"的宿主块（修复前它进不了人口）。"""
    for host in _ordered_hosts(blocks, facts):
        if host in facts["cand_blocks"]:
            continue
        if not facts["nodes_by_block"].get(host):
            continue
        if not _material_ready(facts, host):
            continue
        return host
    return None


def _check_attribution(check, details) -> dict | None:
    """§1 人口口径：真文档上的 `located_table_hosts` 四面（含两条反例）。

    这一节**不**依赖放行数量：它读的是"这条 Evidence 落在哪个节点"这条**准入事实**本身。
    """
    entry = _session(TARGET_DOC)
    if entry is None:
        details.append(f"SKIP 缺 {TARGET_DOC} 样本或 data/evidence.db（§1 未执行）")
        return None
    session, blocks = entry
    facts = _facts(session)

    # 样例块：优先取"零正文候选但有表材料"的现场；没有就退回任一有归属节点的块。
    host = _first_zero_candidate_case(facts, blocks)
    if host is None:
        host = next((h for h in _ordered_hosts(blocks, facts)
                     if facts["nodes_by_block"].get(h)), None)
    if host is None:
        host = next((h for h, nodes in facts["nodes_by_block"].items() if nodes), None)
    check(host is not None, "真文档里能找到带 component 归属节点的宿主块（§1 前提）")
    if host is None:
        return None
    node = sorted(facts["nodes_by_block"][host])[0]
    hosts_one, nodes_map = session.located_table_hosts("node_ids", [node])
    check(host in set(hosts_one) and node in set(nodes_map.get(host, ())),
          "选中节点后：该宿主块在**已定位**集合里，且带归属节点")
    check(hosts_one == session.located_table_hosts("node_ids", [node])[0],
          "已定位集合是确定性的（同参数两次调用逐项一致）")

    def _page(bid):
        block = blocks.get(bid)
        return (getattr(block, "page_number", 0) or 0,
                getattr(block, "block_index", 0) or 0)

    check(list(hosts_one) == sorted(hosts_one, key=_page),
          "已定位集合按（页号, 块序）定序——与 `ordered_evidence_blocks()` 同一顺序来源")

    # 反例①：另选一个**归属别的宿主块**的节点 ⇒ 本块不得混入。
    other = None
    for candidate_host in sorted(facts["nodes_by_block"], key=_page):
        if candidate_host == host or not facts["nodes_by_block"].get(candidate_host):
            continue
        other_nodes = sorted(facts["nodes_by_block"][candidate_host])
        if not (set(other_nodes) & facts["nodes_by_block"][host]):
            other = other_nodes[0]
            break
    check(other is not None, "真文档里能找到与样例块无共同归属节点的另一节点（反例前提）")
    if other is not None:
        other_hosts, _ = session.located_table_hosts("node_ids", [other])
        check(host not in set(other_hosts),
              "反例：选中与样例块无关的节点时，样例块**不**进人口（不按整篇扫表）")

    # 反例②：node_id 为空的宿主块**不可归属** —— 即使全树节点都选上也不得进人口。
    orphans = sorted((h for h, nodes in facts["nodes_by_block"].items() if not nodes),
                     key=_page)
    all_nodes = sorted({str(n.node_id) for n in session._source.document_outline.nodes})
    all_hosts, _ = session.located_table_hosts("node_ids", all_nodes)
    check(all(orphan not in set(all_hosts) for orphan in orphans),
          f"反例：{len(orphans)} 个不可归属（component 无 node_id）的宿主块"
          f"在全树节点都选中时**仍不进人口**（不猜归属、不按标题补）")
    for orphan in orphans[:3]:
        check(all(getattr(session._spans_by_id.get(sid), "node_id", None) is None
                  for sid in self_spans(session).get(orphan, ())),
              "不可归属块在 span 侧也确无节点落点（两条关系都归不到，才叫不可归属）")

    # evidence selector：选中的块本身就是宿主，不要求块里有正文候选。
    ev_hosts, _ = session.located_table_hosts("evidence_ids", [host])
    check(list(ev_hosts) == [host],
          "evidence selector 下：选中的块**本身就是**已定位宿主（不要求正文候选）")
    return {"session": session, "blocks": blocks, "facts": facts, "host": host,
            "node": node, "other": other, "orphans": orphans}


def _check_node_dispatch(check, details, ctx) -> None:
    """§2 真工具派发（node selector）：本次已定位宿主块的表材料必须一致返回。"""
    session, facts, host, node = ctx["session"], ctx["facts"], ctx["host"], ctx["node"]
    result = _inspect(session, "node_ids", [node])
    check(result.status in ("SUCCESS", "PARTIAL"),
          f"node 派发：状态为完成类（实得 {result.status}）")
    want = {str(o.get("release_id") or "") for o in facts["by_host"].get(host, ())}
    got = {str(o.get("release_id") or "") for o in result.data["table_objects"]}
    got_mats = {str(m.get("release_id") or "") for m in result.data["table_materials"]}
    check(want <= got,
          f"本次已定位宿主块里已放行的表，表对象**按人口一致返回**"
          f"（现场该块放行 {len(want)} 张）")
    check(got <= want,
          "返回的对象**不**超出本次已定位块的人口（另一侧不串味）")
    check(got <= got_mats and len(result.data["table_materials"]) == len(got),
          "每个被返回的对象都带**恰一份**表材料（对象与材料一起给，不半截）")
    check(len(result.data["table_objects"]) <= TT.MAX_TABLE_OBJECTS,
          "工具级上界仍在（人口变大不等于取消上界）")
    check(all(str(o.get("host_evidence_id") or "") in set(
        session.located_table_hosts("node_ids", [node])[0])
        for o in result.data["table_objects"]),
          "每个返回对象的宿主块都在本次已定位集合里")

    if ctx["other"] is not None:
        other_result = _inspect(session, "node_ids", [ctx["other"]])
        other_got = {str(o.get("release_id") or "")
                     for o in other_result.data["table_objects"]}
        check(not (want & other_got),
              "反例：与样例块无关的节点派发**拿不到**它的对象（两侧不串味）")


def _check_evidence_dispatch(check, details, ctx) -> None:
    """§3 真工具派发（evidence selector）：同上，且未选中的块不得混入。"""
    session, facts, host = ctx["session"], ctx["facts"], ctx["host"]
    result = _inspect(session, "evidence_ids", [host])
    want = {str(o.get("release_id") or "") for o in facts["by_host"].get(host, ())}
    got = {str(o.get("release_id") or "") for o in result.data["table_objects"]}
    check(want <= got,
          "evidence 派发：选中的块里已放行的表，表对象**仍被返回**")
    check(got <= want,
          "evidence 派发：返回的对象只来自选中的块（逐块隔离）")
    check(all(str(c.get("parent_evidence_id") or "") == host
              for c in result.data["candidates"]),
          "evidence 派发：候选只来自选中的块（选择器语义未被人口口径放宽）")

    other_host = next((h for h in _ordered_hosts(ctx["blocks"], facts) if h != host), None)
    if other_host is not None:
        other = _inspect(session, "evidence_ids", [other_host])
        check(not (want & {str(o.get("release_id") or "")
                           for o in other.data["table_objects"]}),
              "未选中的块即使有同文档的表，也不混入本次返回（逐块隔离）")


def _check_target_tables(check, details) -> None:
    """§4 逐表放行的现场读数：目标表逐张报出**图侧证明的结论**。

    这里**不**断言"三类目标表一定在" —— 放行结论是 `tlp-1` 的产物，本模块不替它下结论。
    断言的是"报出来的东西自洽"：批次对账、拒绝理由落在闭集内、放行的表带材料且不声称
    数字权威。缺表一律记 INFO 并标注**系统能力缺陷**。
    """
    entry = _session(TARGET_DOC)
    if entry is None:
        details.append(f"SKIP 缺 {TARGET_DOC} 样本（§4 未执行）")
        return
    session, _blocks = entry
    facts = _facts(session)
    batch = session.graph_release_batch()
    check(batch["batch_state"] in ("accounted", "no_table_objects"),
          f"批次状态落在封闭词表内（实得 {batch['batch_state']}）")
    released = list(batch.get("released") or ())
    refusals = list(batch.get("refusals") or ())
    details.append(
        f"INFO {TARGET_DOC} 图侧表批次：state={batch['batch_state']} "
        f"document_qualified={batch.get('document_qualified')} "
        f"declared={batch.get('declared_table_count')} released={len(released)} "
        f"refused={len(refusals)}")
    if batch["batch_state"] == "accounted":
        check(len(released) + len(refusals) == batch["declared_table_count"],
              "逐表放行结果与快照成员数严格对账")

    reason_codes = {str(r.get("reason") or "") for r in refusals}
    check(reason_codes <= set(TLP.TABLE_LOCAL_PROOF_DEFECTS),
          f"拒绝理由全部落在 `tlp-1` 的封闭缺陷词表内（实得 {sorted(reason_codes)}）")
    for record in released:
        material = facts["batch"].material_for_release(record)
        check(material is not None,
              f"放行的表 {record['table_id']} 必须有 Pack 材料（放行了却进不了 Pack "
              f"是另一本账，必须逐条留痕）")
        qualification = record.get("content_qualification") or {}
        check(qualification.get("reading_material") is True
              and qualification.get("numeric_authority") is False
              and qualification.get("financial_authority_claimed") is False,
              "放行记录逐字声明：是阅读材料、不是数字权威、不是财务权威")

    titles = {str(r.get("title_text") or "") for r in released}
    refusals_by_page = {}
    for record in refusals:
        refusals_by_page.setdefault(int(record.get("page_number") or 0), []).append(
            record.get("reason"))
    found = set()
    for needle in TARGET_TITLE_NEEDLES:
        hit_rel = sorted(t for t in titles if needle in t)
        hit_ref = sorted({str(r.get("page_number") or "")
                          for r in refusals
                          if needle in str(r.get("title_text") or "")})
        if hit_rel:
            found.add(needle)
            details.append(f"INFO 目标表「{needle}」已放行：{hit_rel}")
        else:
            details.append(
                f"INFO 目标表「{needle}」**未放行**（图侧放行 {len(released)} 张、"
                f"拒发 {len(refusals)} 张；该文档拒发页/理由 {sorted(refusals_by_page)}）"
                f" ⇒ 按 §0.19 记为**系统能力缺陷**，M930-3 内容门不得上报通过")
    details.append(
        f"INFO 目标表 needle 命中：{sorted(found)}（表题读数来自 `title_text`，"
        f"图侧没读到表题时为空串）")


def _check_table_only(check, details) -> None:
    """§5 table-only 返回：有表无正文时不得报空；真没有时才报空。"""
    entry = _session(TARGET_DOC)
    if entry is None:
        details.append(f"SKIP 缺 {TARGET_DOC} 样本（§5 未执行）")
        return
    session, blocks = entry
    facts = _facts(session)
    if not facts["by_host"]:
        details.append(
            "NOTE §5 本份文档图侧零放行表 ⇒ 没有 table-only 现场（这不是'该文档没有表'，"
            "而是逐表完整证明把每一张都拒发了；见 §4 的读数）。正例由 "
            "`evals/test_m930_3_tree_table_branch` 的夹具覆盖")
    else:
        table_only_node = None
        for host in _ordered_hosts(blocks, facts):
            if not facts["nodes_by_block"].get(host) or not _material_ready(facts, host):
                continue
            for node in sorted(facts["nodes_by_block"][host]):
                hosts, _ = session.located_table_hosts("node_ids", [node])
                if not hosts:
                    continue
                if all(b not in facts["cand_blocks"] for b in hosts):
                    table_only_node = node
                    break
            if table_only_node is not None:
                break
        check(table_only_node is not None,
              "真文档里存在「选中节点下全是零正文候选的宿主块」的派发（table-only 现场）")
        if table_only_node is not None:
            result = _inspect(session, "node_ids", [table_only_node])
            check(not result.data["candidates"], "现场确认：这次派发**零正文候选**")
            check(result.data["table_materials"],
                  "现场确认：这次派发**有**表材料（否则不是 table-only）")
            check(result.status in ("SUCCESS", "PARTIAL") and result.error_code is None,
                  f"table-only 返回不报空：status={result.status}、"
                  f"error_code={result.error_code}")
            check(bool(result.evidence_ids),
                  "table-only 返回的 `evidence_ids` 非空（来源块如实带出，不是 [])")
            hosts, _ = session.located_table_hosts("node_ids", [table_only_node])
            check(set(hosts) <= set(result.evidence_ids),
                  "`evidence_ids` 覆盖本次已定位的宿主块（表及其来源都被表示）")
            check("表对象材料" in (result.message or ""),
                  f"message 说清两路计数（实得 {result.message!r}）")

    # 反例：真的什么都没有时，仍必须是 EMPTY / RETRIEVAL_EMPTY。
    for node in sorted({str(n.node_id) for n in session._source.document_outline.nodes}):
        hosts, _ = session.located_table_hosts("node_ids", [node])
        if hosts:
            continue
        probe = _inspect(session, "node_ids", [node])
        if not probe.data["candidates"] and not probe.data["table_objects"]:
            check(probe.status == "EMPTY" and probe.error_code == "RETRIEVAL_EMPTY",
                  f"反例：既无正文候选、也无表对象时仍报 EMPTY/RETRIEVAL_EMPTY"
                  f"（实得 {probe.status}/{probe.error_code}）")
            check(probe.evidence_ids == [],
                  "反例：确实没有交出任何来源块时 `evidence_ids` 为空")
            return
    details.append("NOTE §5 反例：本树里没有「零归属宿主块且零候选」的节点，未构造"
                   "（不得据此跳过断言——上面的正例与 §1–§4 已执行）")


def _check_span_cap_independent(check, details, ctx) -> None:
    """§6 span 上界只限正文通道，不得连带吞掉表对象。

    真实派发的 read set 本来就是**多个节点**：有的节点名下是正文章节、有的节点名下只有表。
    所以现场取 `[表宿主节点, 正文节点]` 两节点派发：先把上界放到很宽、再收到 1，
    断言①正文候选确实变少、②表对象集合**逐项不变**。正文节点动态发现（正文候选 ≥2），
    找不到就**如实记 NOTE**（不得当成通过）。
    """
    entry = _session(TARGET_DOC)
    if entry is None or ctx is None:
        details.append(f"SKIP 缺 {TARGET_DOC} 样本（§6 未执行）")
        return
    session, _blocks = entry
    facts = ctx["facts"]
    table_node = ctx["node"]
    prose_node = None
    ordered = sorted(session._spans_by_node,
                     key=lambda n: (-len(session._spans_by_node.get(n, ())), str(n)))
    for node in ordered[:_CAP_SEARCH_LIMIT]:
        if node == table_node:
            continue
        probe = _inspect(session, "node_ids", [node], max_spans=999)
        if len(probe.data["candidates"]) >= 2:
            prose_node = node
            break
    if prose_node is None:
        details.append(f"NOTE §6 没找到正文候选 ≥2 的节点（试了 {_CAP_SEARCH_LIMIT} 个），"
                       f"未执行「span 上界不动表对象」对照——**不得**据此宣称已通过")
        return
    both = [table_node, prose_node]
    full = _inspect(session, "node_ids", both, max_spans=999)
    tight = _inspect(session, "node_ids", both, max_spans=1)
    want = {str(o.get("release_id") or "") for o in facts["by_host"].get(ctx["host"], ())}
    full_ids = {str(o.get("release_id") or "") for o in full.data["table_objects"]}
    tight_ids = {str(o.get("release_id") or "") for o in tight.data["table_objects"]}
    check(want <= full_ids,
          "两节点派发里，本次已定位宿主块的对象在宽上界下被返回（现场前提）")
    check(full_ids == tight_ids,
          f"把 span 上界从 999 收到 1，表对象集合**逐项不变**（{len(full_ids)} 个）")
    if not full_ids:
        details.append("NOTE §6 该文档图侧零放行表 ⇒ 这一对照在本份文档上是空集，"
                       "隔离性由 `evals/test_m930_3_tree_table_branch` 的夹具覆盖")
    check(any(s.get("reason") == "over_max_spans" for s in tight.data["skipped"]),
          "span 截断仍如实登记在 skipped（截断不得被读成零命中）")
    check(len(tight.data["candidates"]) < len(full.data["candidates"]),
          f"span 上界**确实**收紧了正文通道（{len(tight.data['candidates'])} < "
          f"{len(full.data['candidates'])}：证明上一条不是「两边都没变」的假通过）")


def _check_fail_closed(check, details) -> None:
    """§7 fail-closed 不回退：错文档版本 / 错 Evidence Set 仍被挡在门外。"""
    entry = _session(TARGET_DOC)
    if entry is None:
        details.append(f"SKIP 缺 {TARGET_DOC} 样本（§7 未执行）")
        return
    session, _blocks = entry
    for field, bad in (("document_version", "sha256-0000000000000000"),
                       ("evidence_set_version", "es-does-not-exist")):
        args = dict(session.document_identity(), need_id="need-population",
                    aspect_id="a", topic_id="t", node_ids=sorted(
                        {str(n.node_id) for n in session._source.document_outline.nodes})[:1])
        args[field] = bad
        result = session.inspect(args)
        check(result.status == "FATAL_ERROR" and result.error_code == "SOURCE_UNTRUSTED",
              f"错 {field} ⇒ FATAL_ERROR/SOURCE_UNTRUSTED（实得 "
              f"{result.status}/{result.error_code}）")
        check("table_objects" not in (result.data or {}),
              f"身份不符时不交出任何表对象（{field}）")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    ctx = _check_attribution(check, details)
    if ctx is None:
        skipped += 1
    else:
        _check_node_dispatch(check, details, ctx)
        _check_evidence_dispatch(check, details, ctx)
    _check_target_tables(check, details)
    _check_table_only(check, details)
    _check_span_cap_independent(check, details, ctx)
    _check_fail_closed(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))

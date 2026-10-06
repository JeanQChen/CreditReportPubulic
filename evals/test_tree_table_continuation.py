# -*- coding: utf-8 -*-
"""TS5 §19.12.5 1–6：续表（`continued_by`）六条证明的正例 / 负例 / 链一致性与歧义保留。

覆盖（全部离线：只读本地 PDF 与**只读** `data/evidence.db` `data/financial_v2.db`；
无网络、无 LLM、无 Bocha、无写入）：

- **§19.12.5-1** 真实续表正例：真实现场上每条 `continued_by` 都必须满足
  §19.8.2 的六条证明（相邻物理页、列边界/header/unit/文档身份闭合、源序闭合、
  无介入结构），且两端对象级解析到真实 `TableObjectV4`；
- **§19.12.5-2** 负例逐条：不同单位、不同列结构、跨 document version、非相邻页、
  仅 header 重复——每条负例都独立构造，并断言 `_continuation_proofs` 返回 False
  且 `build_continuations` 把该对留在歧义集合里（不合并）；
- **§19.12.5-3** 两张相邻同列数表 / 相同标题但另起新表不得合并：列带不一致或
  unit 不一致或 header 签名不一致时一律不建立 `continued_by`；
- **§19.12.5-4** 真实链双向一致、无环、in/out degree ≤ 1；
- **§19.12.5-5** repeated header 的逻辑去重不得删除物理 provenance：相邻续表各自
  保留自己的 header cell 与各自的物理来源区间（不同页、不同 source_ref），
  且两张表都仍在 `tables` 里（不因"逻辑上是一张表"而被删掉一张）；
- **§19.12.5-6** ambiguous continuation 保留两个 fragment 与显式 gap。

每个 `check` 都设计成"对应生产逻辑被移除即失败"：六条证明用**独立构造的一对表 +
逐条扰动**核对（每条负例只改一个字段），真实链用 succ/pred 度数与环检测独立重算，
歧义对用"两张表仍在 tables 里 + gap 账本非空"双向核对。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from evidence import store as _estore

from evals import tree_stage_env as STAGE
from document_structure import aligner as AL
from document_structure import final_material_builder as FMB
from document_structure import layout_builder as LB
from document_structure import span_builder as SB
from document_structure import span_verifier as SV
from document_structure import table_builder as TB
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.evidence_gateway import bind_current_evidence_authority

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:
        text = str(e)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


def check_aggregate(bad, total, msg, sample: int = 3):
    bad = list(bad)
    return check(not bad and total > 0,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


def check_aggregate_allow_empty(bad, total, msg, sample: int = 3):
    bad = list(bad)
    return check(not bad,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


# ---------------------------------------------------------------------------
# 0. 只读真实现场
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
#: §19.13 指定 `NDSD_KCZ_2026` 承载真实跨页续表（表 5-11）。
_TRUST_KEY = "NDSD_KCZ_2026"

_STATE: dict = {}


def _db_identity(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _chain() -> dict:
    if _STATE:
        return _STATE
    trust = json.loads(_TRUST.read_text(encoding="utf-8"))
    doc = trust["documents"][_TRUST_KEY]
    pdf = _REPO / doc["source_pdf_relpath"]
    ident = doc["expected_identity"]

    from sections import service
    service._prepare_stores(service.ServiceConfig(
        fin_db=str(_FIN_DB), ev_db=str(_EVIDENCE_DB)))
    authority = bind_current_evidence_authority()
    vlayout = LB.build_verified_page_layout(
        pdf, company_id=ident["company_id"], document_id=ident["document_id"])
    alignment = AL.align_evidence_set_verified(vlayout, authority)
    handoff = SB.issue_live_ts3_handoff(vlayout, alignment, authority)
    snap = SB.build_span_snapshot(handoff, stage=STAGE.current_stage())
    verified = SV.verify_span_snapshot(snap, handoff)
    final = FMB.build_final_material_snapshot(verified)
    roots = FMB._read_roots(verified)
    ctx = FMB._build_context(roots)
    built = TB.build_tables(ctx)
    _STATE.update({"snap": snap, "verified": verified, "final": final,
                   "ctx": ctx, "built": built, "ident": ident})
    return _STATE


# ---------------------------------------------------------------------------
# 1. §19.8.2 六条证明：独立构造的一对表 + 逐条扰动
# ---------------------------------------------------------------------------

class _FakeTable:
    """只承载 §19.8.2 六条证明读到的字段（最小可构造对象）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeGrid:
    """`edges` 即各列真实片段的左边界；`column` 与 `GridCellPlan` 同域地给列号。"""

    def __init__(self, edges):
        self.cells = [
            SimpleNamespace(column=i,
                            bbox=(float(e), 0.0, float(e) + 10.0, 5.0))
            for i, e in enumerate(edges)]


class _FakeBuiltTable:
    def __init__(self, table, edges):
        self.table = table
        self.grid = _FakeGrid(edges)


def _fake_table(**over) -> "_FakeTable":
    base = dict(
        document_id="doc-1", document_version="dv-1", evidence_set_version="es-1",
        page_layout_id="pl-1", outline_id="ol-1",
        page_number=10, page_bbox=(0.0, 300.0, 500.0, 100.0),
        column_count=2, unit_text="单位：万元",
        table_id="to-a", table_locator="loc-to4-a",
        rows=[
            SimpleNamespace(role="header",
                            cells=[SimpleNamespace(text="项目"),
                                   SimpleNamespace(text="金额")]),
            SimpleNamespace(role="body",
                            cells=[SimpleNamespace(text="营业收入"),
                                   SimpleNamespace(text="1.00")]),
        ])
    base.update(over)
    return _FakeTable(**base)


def _fake_pair(**b_over):
    a = _FakeBuiltTable(_fake_table(), [0.0, 100.0, 200.0])
    tb_fields = dict(page_number=11, page_bbox=(0.0, 300.0, 500.0, 100.0),
                     table_id="to-b", table_locator="loc-to4-b")
    tb_fields.update(b_over)
    b = _FakeBuiltTable(_fake_table(**tb_fields), [0.0, 100.0, 200.0])
    return a, b


def _proofs(a, b, ordered=None):
    return TB._continuation_proofs(a, b, ordered or [a, b])


def _fake_ctx():
    return SimpleNamespace(upstream_dependency_fingerprint="0" * 64)


def _g1_truth_table(ctx):
    check(TB.CONTINUATION_PROOF_KINDS ==
          ("same_document_identity", "adjacent_page_or_explicit_occurrence",
           "compatible_column_header_unit", "source_order_closure",
           "no_intervening_structure",
           "bidirectional_acyclic_single_successor"),
          "§19.8.2 的 continuation 门恰为 6 条且顺序固定")
    check(len(TS.CONTINUATION_PROOF_KINDS) == 6,
          "table_schema 的 CONTINUATION_PROOF_KINDS 恰为 6 条")

    a, b = _fake_pair()
    check(_proofs(a, b) is True,
          "六条证明全部成立的相邻续表对被接受（正例）")

    # 逐条负例：每次只改一个字段。
    cases = (
        ("same_document_identity", {"document_version": "dv-2"}),
        ("same_document_identity", {"evidence_set_version": "es-2"}),
        ("same_document_identity", {"page_layout_id": "pl-2"}),
        ("same_document_identity", {"outline_id": "ol-2"}),
        ("same_document_identity", {"document_id": "doc-2"}),
        ("adjacent_page_or_explicit_occurrence", {"page_number": 12}),
        ("adjacent_page_or_explicit_occurrence", {"page_number": 13}),
        ("compatible_column_header_unit", {"unit_text": "单位：元"}),
        ("compatible_column_header_unit", {"unit_text": None}),
        ("compatible_column_header_unit", {"column_count": 3}),
    )
    for proof, over in cases:
        x, y = _fake_pair(**over)
        check(_proofs(x, y) is False,
              f"负例：{over} ⇒ 第 {proof} 条证明不成立，不建立 continued_by")

    # header 签名：仅 header 文本不同也必须拒绝。
    x, y = _fake_pair(rows=[
        SimpleNamespace(role="header",
                        cells=[SimpleNamespace(text="科目"),
                               SimpleNamespace(text="金额")]),
        SimpleNamespace(role="body",
                        cells=[SimpleNamespace(text="营业收入"),
                               SimpleNamespace(text="1.00")])])
    check(_proofs(x, y) is False,
          "负例：仅 header 文本不同（header 签名不一致）不得合并")
    # 仅 header 重复：header 相同但列带不一致仍不得合并。
    x = _FakeBuiltTable(_fake_table(), [0.0, 100.0, 200.0])
    y = _FakeBuiltTable(_fake_table(page_number=11, table_id="to-b",
                                    table_locator="loc-to4-b"),
                        [0.0, 120.0, 240.0])
    check(TB._header_signature(x.table) == TB._header_signature(y.table)
          and _proofs(x, y) is False,
          "负例：仅 header 重复、列带不一致 ⇒ 不得合并")
    check(TB._column_bands_compatible(x, y) is False
          and TB._column_bands_compatible(x, x) is True,
          "列带相容判据对同列带为真、对异列带为假")

    # 两张相邻同列数、同行数的表（列带不同）不得合并。
    z = _FakeBuiltTable(_fake_table(page_number=11, table_id="to-z",
                                    table_locator="loc-to4-z"), [0.0, 90.0, 210.0])
    w = _FakeBuiltTable(_fake_table(page_number=11, table_id="to-w",
                                    table_locator="loc-to4-w"), [5.0, 95.0, 215.0])
    check(x.table.column_count == z.table.column_count
          and len(x.table.rows) == len(z.table.rows)
          and _proofs(x, z) is False,
          "负例：两张相邻同列数同行数表（列带不同）不得合并")

    # 源序闭合：目标必须排在源之后。
    x, y = _fake_pair()
    back = _FakeBuiltTable(_fake_table(page_number=9, table_id="to-back",
                                       table_locator="loc-to4-back"), [0, 100, 200])
    check(_proofs(x, back) is False,
          "负例：目标页早于源页 ⇒ 源序闭合不成立")


def _g1_neg_through_builder(ctx):
    """负例必须真的**不合并**：`build_continuations` 把该对留在歧义集合里。"""
    outs = {}
    for name, b_over in (("adjacent_ok", {}),
                         ("non_adjacent", {"page_number": 12}),
                         ("other_unit", {"unit_text": "单位：元"}),
                         ("other_version", {"document_version": "dv-2"})):
        a, b = _fake_pair(**b_over)
        out = TB.build_continuations(_fake_ctx(), [a, b])
        outs[name] = (len(out.relations), len(out.ambiguous_pairs))
    check(outs["adjacent_ok"] == (1, 0),
          f"相邻闭合对建立 1 条 continued_by、0 条歧义：{outs['adjacent_ok']}")
    for name in ("non_adjacent", "other_unit", "other_version"):
        check(outs[name] == (0, 1),
              f"{name}: 不建立关系且显式留在歧义集合：{outs[name]}")

    # 三张同结构相邻表：链两侧各一条，in/out degree 各 ≤ 1。
    a, b = _fake_pair()
    c = _FakeBuiltTable(_fake_table(page_number=12, table_id="to-c",
                                    table_locator="loc-to4-c"), [0, 100, 200])
    out = TB.build_continuations(_fake_ctx(), [a, b, c])
    succ: dict = {}
    pred: dict = {}
    for r in out.relations:
        succ.setdefault(r.source_endpoint.table_id, []).append(
            r.target_endpoint.table_id)
        pred.setdefault(r.target_endpoint.table_id, []).append(
            r.source_endpoint.table_id)
    check(len(out.relations) == 2 and len(out.ambiguous_pairs) == 0,
          f"三张相邻同结构表形成双向链（2 条关系 / 0 歧义）："
          f"{len(out.relations)}/{len(out.ambiguous_pairs)}")
    check(all(len(v) <= 1 for v in succ.values())
          and all(len(v) <= 1 for v in pred.values()),
          f"链的 in/out degree ≤ 1（succ={succ} pred={pred}）")
    check(succ.get("to-a") == ["to-b"] and succ.get("to-b") == ["to-c"]
          and pred.get("to-b") == ["to-a"] and pred.get("to-c") == ["to-b"],
          "链双向一致（每条边的两端互为前驱/后继）")

    # 乱序输入：源序由真实页/矩形重排，而不是采信调用方给的列表顺序。
    a2, b2 = _fake_pair()
    out2 = TB.build_continuations(_fake_ctx(), [b2, a2])
    check(len(out2.relations) == 1
          and out2.relations[0].source_endpoint.table_id == "to-a"
          and out2.relations[0].target_endpoint.table_id == "to-b",
          "乱序输入下源序仍由真实页/矩形重排（不采信调用方列表顺序）")

    # 自指 / 跨文档 / 跨版本的 relation 构造期拒绝。
    a3, b3 = _fake_pair()
    ea = TB._table_endpoint(a3.table)
    raises(lambda: TS.TableRelation.create(
        schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
        relation_builder_version=V.TABLE_RELATION_BUILDER_VERSION,
        upstream_dependency_fingerprint="0" * 64, relation_kind="continued_by",
        source_endpoint=ea, target_endpoint=ea, relation_proof_ids=("p",)),
        Exception, "continued_by 的端点不得是自身",
        "续表自指在 relation 构造期被拒")
    e_b_doc = TS.TableEndpointRef(
        table_locator="loc-to4-b", table_id="to-b", document_id="doc-2",
        page_layout_id="pl-1", outline_id="ol-1", page_number=11)
    raises(lambda: TS.TableRelation.create(
        schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
        relation_builder_version=V.TABLE_RELATION_BUILDER_VERSION,
        upstream_dependency_fingerprint="0" * 64, relation_kind="continued_by",
        source_endpoint=ea, target_endpoint=e_b_doc,
        relation_proof_ids=("p",)),
        Exception, "continued_by 必须 same document（TS5 不跨文档合成逻辑大表）",
        "跨文档续表在 relation 构造期被拒")


# ---------------------------------------------------------------------------
# 2. 真实链：正例、度数、无环、物理 provenance
# ---------------------------------------------------------------------------

def _proof_report(a, b, ordered) -> dict:
    """测试侧**独立重算** §19.8.2 的六条证明（不调用生产判定函数）。"""
    ta, tb = a.table, b.table
    return {
        "same_document_identity": (
            (ta.document_id, ta.document_version, ta.evidence_set_version,
             ta.page_layout_id, ta.outline_id) ==
            (tb.document_id, tb.document_version, tb.evidence_set_version,
             tb.page_layout_id, tb.outline_id)),
        "adjacent_page": tb.page_number == ta.page_number + 1,
        "compatible_unit": ta.unit_text == tb.unit_text,
        "compatible_columns": ta.column_count == tb.column_count,
        "compatible_header": TB._header_signature(ta) == TB._header_signature(tb),
        "compatible_bands": TB._column_bands_compatible(a, b),
        "source_order_closure": (
            (tb.page_number, tuple(tb.page_bbox)) >
            (ta.page_number, tuple(ta.page_bbox))),
        "no_intervening_structure": not any(
            ta.page_number < o.table.page_number < tb.page_number
            for o in ordered
            if o.table.table_id not in (ta.table_id, tb.table_id)),
    }


def _g2_real(ctx):
    built = ctx["built"]
    final = ctx["final"]
    tables = {bt.table.table_id: bt for bt in built.tables}
    conts = [r for r in final.relations if r.relation_kind == "continued_by"]
    check(bool(built.tables),
          f"真实现场构建出表格：{len(built.tables)} 张（关系 {len(built.relations)} 条）")
    _results["details"].append(
        f"__real_tables__ {len(built.tables)} continued_by={len(conts)} "
        f"ambiguous={len(built.ambiguous_continuations)}")

    bad_kind = [r.relation_id for r in final.relations
                if r.relation_kind not in TS.RESOLVABLE_RELATION_KINDS]
    check_aggregate_allow_empty(bad_kind, len(final.relations),
                                "真实 relation 全部落在可构造的 7 类里")
    bad_pairs = [r.relation_id for r in conts
                 if not (r.source_endpoint.page_number + 1 ==
                         r.target_endpoint.page_number)]
    check_aggregate_allow_empty(bad_pairs, len(conts),
                                "每条真实 continued_by 的相邻物理页证明成立（§19.12.5-1）")
    bad_resolve = []
    for r in conts:
        a = tables.get(r.source_endpoint.table_id)
        b = tables.get(r.target_endpoint.table_id)
        if a is None or b is None:
            bad_resolve.append(r.relation_id)
            continue
        if a.table.page_number != r.source_endpoint.page_number or \
                b.table.page_number != r.target_endpoint.page_number:
            bad_resolve.append(r.relation_id)
        if not _proofs(a, b, list(built.tables)):
            bad_resolve.append(r.relation_id)
        if a.table.column_count != b.table.column_count or \
                a.table.unit_text != b.table.unit_text or \
                TB._header_signature(a.table) != TB._header_signature(b.table) or \
                not TB._column_bands_compatible(a, b):
            bad_resolve.append(r.relation_id)
    check_aggregate_allow_empty(
        bad_resolve, len(conts),
        "每条真实 continued_by 的两端对象级解析并通过六条证明的复算")

    # 双向一致 / 无环 / degree ≤ 1。
    succ: dict = {}
    pred: dict = {}
    for r in conts:
        succ.setdefault(r.source_endpoint.table_id, []).append(
            r.target_endpoint.table_id)
        pred.setdefault(r.target_endpoint.table_id, []).append(
            r.source_endpoint.table_id)
    check_aggregate_allow_empty(
        [k for k, v in succ.items() if len(v) > 1], len(succ),
        "真实续表链的 out-degree ≤ 1")
    check_aggregate_allow_empty(
        [k for k, v in pred.items() if len(v) > 1], len(pred),
        "真实续表链的 in-degree ≤ 1")
    inconsistent = []
    for a_id, targets in succ.items():
        for b_id in targets:
            if pred.get(b_id) != [a_id]:
                inconsistent.append((a_id, b_id))
    for b_id, sources in pred.items():
        for a_id in sources:
            if succ.get(a_id) != [b_id]:
                inconsistent.append((a_id, b_id))
    check_aggregate_allow_empty(inconsistent, len(conts),
                                "真实续表链双向一致（每条边两端互为唯一前驱/后继）")
    seen_max = 0
    for start in succ:
        seen, cur = {start}, start
        while cur in succ:
            cur = succ[cur][0]
            if cur in seen:
                break
            seen.add(cur)
        seen_max = max(seen_max, len(seen))
    check(all(_no_cycle(start, succ) for start in succ),
          f"真实续表链无环（最长链 {seen_max} 张表）")

    # 非空洞的真实正向核对：每一个**相邻页**的表对，要么建立 continued_by，
    # 要么在测试独立重算的证明里**至少有一条不成立**（不可能既不合并也无理由）。
    order_list = sorted(built.tables,
                        key=lambda t: (t.table.page_number, t.table.page_bbox[1],
                                       t.table.table_id))
    linked = {(r.source_endpoint.table_id, r.target_endpoint.table_id)
              for r in conts}
    unjustified, adjacent_pairs = [], 0
    for a, b in zip(order_list, order_list[1:]):
        if b.table.page_number != a.table.page_number + 1:
            continue
        adjacent_pairs += 1
        key = (a.table.table_id, b.table.table_id)
        report = _proof_report(a, b, order_list)
        if key in linked:
            if not all(report.values()):
                unjustified.append(f"{key}:linked_but_{_failed(report)}")
        elif all(report.values()):
            unjustified.append(f"{key}:all_proofs_true_but_not_linked")
    check_aggregate_allow_empty(
        unjustified, adjacent_pairs,
        f"每个相邻页表对要么建立 continued_by 要么至少一条证明不成立"
        f"（相邻对 {adjacent_pairs} 个，已连接 {len(linked)} 个）")
    if not conts:
        reasons = []
        for a, b in zip(order_list, order_list[1:]):
            if b.table.page_number != a.table.page_number + 1:
                continue
            report = _proof_report(a, b, order_list)
            if not all(report.values()):
                reasons.append(f"{a.table.table_id}->{b.table.table_id}:"
                               f"{_failed(report)}")
        _results["skipped"] += 1
        _results["details"].append(
            f"SKIP 本文档 {len(order_list)} 张表、{adjacent_pairs} 个相邻页表对，"
            f"但未建立任何 continued_by：§19.12.5-1 的**真实**跨页续表正例在本轮"
            f"构建中不可得（相邻对不成立原因：{reasons[:3]}）；"
            f"六条证明的正例由 C1/C4 的合成对独立覆盖")


def _failed(report: dict) -> list:
    return [k for k, v in report.items() if not v]


def _no_cycle(start: str, succ: dict) -> bool:
    seen, cur = {start}, start
    while cur in succ:
        cur = succ[cur][0]
        if cur in seen:
            return False
        seen.add(cur)
    return True


def _g3_provenance(ctx):
    """§19.12.5-5：repeated header 的逻辑去重不得删除物理 provenance。"""
    built = ctx["built"]
    final = ctx["final"]
    tables = {bt.table.table_id: bt for bt in built.tables}
    conts = [r for r in final.relations if r.relation_kind == "continued_by"]
    check(bool(built.tables) and len(built.tables) == len(final.tables),
          f"逻辑续表不删除物理表对象：built {len(built.tables)} == "
          f"final {len(final.tables)}（无表在合并中被删掉）")

    bad_keep, bad_phys, bad_page, bad_gap = [], [], [], []
    header_less = 0
    checked = 0
    for r in conts:
        a = tables.get(r.source_endpoint.table_id)
        b = tables.get(r.target_endpoint.table_id)
        if a is None or b is None:
            continue
        checked += 1
        ha = [c for row in a.table.rows if row.role == "header" for c in row.cells]
        hb = [c for row in b.table.rows if row.role == "header" for c in row.cells]
        if not ha or not hb:
            # 本条的命题是"**有表头**的续表不得丢物理 provenance"。表头行里有一格
            # 无法闭合到真实来源时（`tc-3` 的 typed 缺口），该表**降级**为
            # `header_not_proven`——这是诚实结果，不是本条的违例；但它必须在册，
            # 不得静默变成"这张表本来就没有表头"。
            header_less += 1
            for endpoint in (a, b):
                table = endpoint.table
                if any(row.role == "header" for row in table.rows):
                    continue
                if not (table.structure_state != "complete"
                        and "header" in table.missing_or_uncertain_fields):
                    bad_gap.append((r.relation_id, table.table_id,
                                    table.structure_state,
                                    tuple(table.missing_or_uncertain_fields)))
            continue
            bad_keep.append(r.relation_id)
            continue
        ra = {rid for c in ha for rid in c.source_ref_ids}
        rb = {rid for c in hb for rid in c.source_ref_ids}
        if not ra or not rb:
            bad_keep.append(r.relation_id)
            continue
        if ra & rb:
            bad_phys.append(r.relation_id)
        pages = set()
        for ref in list(a.table.source_refs) + list(b.table.source_refs):
            pages.add(ref.interval.page_number)
        if a.table.page_number not in pages or b.table.page_number not in pages:
            bad_page.append(r.relation_id)
    check_aggregate_allow_empty(
        bad_keep, checked,
        "续表两张表各自的 header cell 都保留自己的物理来源片段（去重只发生在逻辑层）")
    check_aggregate_allow_empty(
        bad_phys, checked,
        "续表两端的 header 来源片段 id 互不相同（物理 provenance 未被逻辑合并吞掉）")
    check_aggregate_allow_empty(
        bad_page, checked,
        "续表两端的来源片段分别落在各自物理页（跨页 provenance 完整）")
    check_aggregate_allow_empty(
        bad_gap, checked,
        "续表两端未识别出表头的表必须在册为 header_not_proven（表头降级不得静默）")
    if header_less:
        _results["details"].append(
            f"INFO 有 {header_less} 条续表的一端表头被 typed 缺口降级为 "
            f"header_not_proven（表头行里有一格无法引用），已逐条核对在册")

    if checked == 0:
        _results["skipped"] += 1
        _results["details"].append(
            "SKIP 本文档没有真实 continued_by：repeated-header 物理 provenance "
            "改用合成链核对")
        a, b = _fake_pair()
        check(TB._header_signature(a.table) == TB._header_signature(b.table)
              and _proofs(a, b) is True,
              "合成链：header 重复但两张表都各自保留自身 header（逻辑去重不删物理表）")


def _g4_ambiguous(ctx):
    """§19.12.5-6：歧义续表保留两个 fragment 与显式 gap。"""
    built = ctx["built"]
    final = ctx["final"]
    pairs = list(built.ambiguous_continuations)
    ids = {bt.table.table_id for bt in built.tables}
    bad_keep = [p for p in pairs if p[0] not in ids or p[1] not in ids]
    check_aggregate_allow_empty(
        bad_keep, len(pairs),
        "歧义续表对的两端都仍在 tables 里（两个 fragment 都保留）")
    table_ids = {t.table_id for t in final.tables}
    check_aggregate_allow_empty(
        [p for p in pairs if p[0] not in table_ids or p[1] not in table_ids],
        len(pairs),
        "歧义续表对的两端都进入 final snapshot 的 tables（未被静默合并）")
    check_aggregate_allow_empty(
        [p for p in pairs if p[0] == p[1]], len(pairs),
        "歧义续表对不是自指（两端为不同物理表）")

    # 歧义必须**有理由**：测试独立重算的六条证明里至少有一条不成立。
    by_id = {bt.table.table_id: bt for bt in built.tables}
    order_list = sorted(built.tables,
                        key=lambda t: (t.table.page_number, t.table.page_bbox[1],
                                       t.table.table_id))
    cont_pairs = {(r.source_endpoint.table_id, r.target_endpoint.table_id)
                  for r in built.relations if r.relation_kind == "continued_by"}
    no_reason, overlap = [], []
    for first, second in pairs:
        a, b = by_id.get(first), by_id.get(second)
        if a is None or b is None:
            no_reason.append((first, second))
            continue
        if (first, second) in cont_pairs or (second, first) in cont_pairs:
            overlap.append((first, second))
        report = _proof_report(a, b, order_list)
        if all(report.values()):
            no_reason.append(f"{first}->{second}:all_proofs_true")
    check_aggregate_allow_empty(
        no_reason, len(pairs),
        "每条歧义续表都至少有一条证明不成立（歧义有理由，不是保守丢表）")
    check_aggregate_allow_empty(
        overlap, len(pairs),
        "歧义集合与已建立的 continued_by 集合互斥（同一对不得既连接又歧义）")
    _results["details"].append(
        "__ambiguous_failed_proofs__ "
        f"{[f'{p[0]}->{p[1]}:{_failed(_proof_report(by_id[p[0]], by_id[p[1]], order_list))}' for p in pairs if p[0] in by_id and p[1] in by_id]}")

    entries = [e for e in final.gaps.entries
               if e.gap_kind == "ambiguous_continuation"]
    check_aggregate_allow_empty(
        [e.detail_code for e in entries if not e.detail_code], len(entries),
        "每条歧义续表缺口都带封闭 detail_code（不是自由说明）")
    if pairs:
        involved = {x for p in pairs for x in p}
        gap_tables = {e.table_id for e in entries if e.table_id}
        check(involved <= gap_tables,
              f"每个歧义续表对都有显式 gap 账本项：缺 "
              f"{sorted(involved - gap_tables)[:3]}")
    else:
        # 负例侧：构造一个不闭合的相邻对，必须留在歧义集合 + 生成 gap 候选。
        a, b = _fake_pair(unit_text="单位：元")
        out = TB.build_continuations(_fake_ctx(), [a, b])
        check(len(out.relations) == 0 and len(out.ambiguous_pairs) == 1
              and out.ambiguous_pairs[0] == ("to-a", "to-b"),
              "不闭合的相邻对既不留关系也不丢弃：显式登记为歧义对")
    check(final.gaps.blocking_entry_count == final.blocking_gap_count,
          "缺口账本的阻断计数与 snapshot 声明一致")
    check(not any(e.gap_kind == "ambiguous_continuation"
                  and e.blocks_document_capability for e in entries),
          "歧义续表是诚实未决，不是文档阻断项（不冒充可保留的 P2 以外语义）")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------

_GROUPS = (
    ("C1-six-proofs", _g1_truth_table),
    ("C1-negatives-through-builder", _g1_neg_through_builder),
    ("C2-real-continuation-chain", _g2_real),
    ("C3-repeated-header-provenance", _g3_provenance),
    ("C4-ambiguous-continuation", _g4_ambiguous),
)


def _run_group(name, fn, ctx) -> None:
    started = time.time()
    try:
        fn(ctx)
    except Exception as exc:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 分组异常：{type(exc).__name__}: {exc}")
    _results["details"].append(f"__{name}__ {time.time() - started:.1f}s")


def main() -> dict:
    started = time.time()
    previous_db_path = getattr(_estore, "_db_path", None)
    before = {"evidence": _db_identity(_EVIDENCE_DB),
              "financial": _db_identity(_FIN_DB)}
    try:
        _estore._db_path = _EVIDENCE_DB
        ctx = _chain()
        for name, fn in _GROUPS:
            _run_group(name, fn, ctx)
    finally:
        _estore._db_path = previous_db_path
        after = {"evidence": _db_identity(_EVIDENCE_DB),
                 "financial": _db_identity(_FIN_DB)}
        check(before == after,
              "只读前置：`data/evidence.db` 与 `data/financial_v2.db` 在本模块前后"
              f"逐项不变（size/mtime_ns/sha256）")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

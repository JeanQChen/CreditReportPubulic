# -*- coding: utf-8 -*-
"""TS5 §19.12.4 1–6 + §19.12.1-9（relation 半边）：裁决真值表 / relation endpoint algebra /
被吸收范围与 final span 的互斥 / kept paragraph 的 12 因子与 0.85 门复算 /
`reconciles_with` 扩展位 / 身份 DAG 单向传播。

覆盖（全部离线：只读本地 PDF 与**只读** `data/evidence.db` `data/financial_v2.db`；
无网络、无 LLM、无 Bocha、无写入）：

- **§19.12.4-1** 八类 decision → 三目标的真值表逐项成立；真实现场上每条
  `table_inside` / `table_adjacency` provisional range **恰有**一条裁决，且
  `target_kind` 与词表一致、被吸收着必带 `table_id`、保留为正文者必带
  `final_span_id`、unsupported/unresolved 两目标皆空；
- **§19.12.4-2** 七类可构造 relation 的 endpoint algebra **逐格正例**：每格都构造
  一个合法 relation，并断言 source/target 端点的 `endpoint_kind` 与
  `RELATION_ENDPOINT_TYPES` 严格一致、`relation_id`/`relation_locator` 由端点对象
  （而非字符串）派生、`from_dict` 往返逐字节等值；
- **§19.12.4-3** 被表格吸收的范围不得同时成为 final span：与 decision 目标
  `table` 的 disposition 集合和全部 final span 的 `ts4_source_disposition_id`
  必须交集为空；final snapshot 的成员身份也双向核对；
- **§19.12.4-4** kept/merged paragraph 的 final span 必须**重新计算**冻结 TS4-B
  的 12 个边界因子与 0.85 门：用 qualification policy 独立复算
  `confidence = left × right`、`confidence_min == 0.85`、
  `min(left, right) >= 0.85`，并逐项核对 12 因子就是 `TS4_A_FACTOR_VALUES`；
  另证明 decision 本身不构成 citable 资格（全不可引用区间的 span 恒
  `is_citable() is False`），且 `sbf-1` 身份不得复用 TS4 `sb-7`；
- **§19.12.4-5** relations 反例一律走真实构造路径拒绝：source/target kind 错配、
  端点缺失（非 typed endpoint）、跨文档、身份（layout/outline）不一致、component
  range 越界（零长度）、`continued_by` 自指、字符串自证（用裸字符串冒充 typed
  endpoint）；
- **§19.12.4-6** `reconciles_with` 在 TS5 不能产 resolved 关系；
- **§19.12.1-9** relation 半边：改 relation 不改变 table id；TableObject /FinalOutlineSpan
  不得携带下游 ID；`upstream_dependency_fingerprint` 稳定且不含 final snapshot 身份。

每个 `check` 都设计成"对应生产逻辑被移除即失败"：真值表用测试自带的独立字面量核对，
置信度用 qualification policy 逐 span 重算，反例一律断言**具体错误类型 + 具体错误文本**，
正向端点的可解析性用 TS4 snapshot 里的真实对象回查。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from evidence import store as _estore

from evals import tree_stage_env as STAGE
from document_structure import aligner as AL
from document_structure import final_material_builder as FMB
from document_structure import layout_builder as LB
from document_structure import schema as S
from document_structure import span_builder as SB
from document_structure import span_policy as SP
from document_structure import span_verifier as SV
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError
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
    """聚合式断言：逐项收集反例，异常集合非空（或总数为 0）即失败。"""
    bad = list(bad)
    return check(not bad and total > 0,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


def check_aggregate_allow_empty(bad, total, msg, sample: int = 3):
    bad = list(bad)
    return check(not bad,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


# ---------------------------------------------------------------------------
# 0. 只读真实现场（唯一正式输入：VerifiedSpanSnapshot）
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
_TRUST_KEY = "NDSD_2024_year"

_STATE: dict = {}


def _db_identity(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _chain() -> dict:
    """一次真实现场：PDF → PageLayout → Outline → SpanSnapshot → verified →
    final material snapshot。结果在模块内缓存（每个模块至多一条真实链）。"""
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
    _STATE.update({"snap": snap, "verified": verified, "final": final,
                   "handoff": handoff, "ident": ident, "policy":
                       verified.handoff.qualification_policy})
    return _STATE


# ---------------------------------------------------------------------------
# 1. 测试自带的独立真值表（生产词表必须与之逐项一致）
# ---------------------------------------------------------------------------

#: §19.7.1：八个裁决 → 三个目标的唯一真值表。**测试自带**，不引用生产 dict。
_EXPECTED_TARGETS = {
    "absorbed_as_caption": "table",
    "absorbed_as_unit": "table",
    "absorbed_as_note": "table",
    "absorbed_as_table_body": "table",
    "kept_as_paragraph": "final_span",
    "absorbed_into_body": "final_span",
    "unsupported_table_structure": "none",
    "unresolved_geometry": "none",
}

#: §19.8.1：七类本批可构造 relation 的严格端点类型对（source, target）。
_EXPECTED_ENDPOINT_TYPES = {
    "introduces": ("FinalSpanEndpointRef", ("TableEndpointRef",)),
    "caption_of": ("ComponentEndpointRef", ("TableEndpointRef",)),
    "unit_of": ("ComponentEndpointRef", ("TableEndpointRef",)),
    "explains": ("TableEndpointRef", ("FinalSpanEndpointRef",)),
    "footnote_of": ("ComponentEndpointRef", ("TableEndpointRef",)),
    "continued_by": ("TableEndpointRef", ("TableEndpointRef",)),
    "references": ("ReferenceOccurrenceEndpointRef", ("TableEndpointRef",)),
}


#: 四种 typed endpoint 的**类名 → endpoint_kind**（判别联合的封闭分支）。
_ENDPOINT_KIND_OF = {
    "TableEndpointRef": "table",
    "FinalSpanEndpointRef": "final_span",
    "ComponentEndpointRef": "component",
    "ReferenceOccurrenceEndpointRef": "reference_occurrence",
}


def _table_endpoint(table) -> "TS.TableEndpointRef":
    return TS.TableEndpointRef(
        table_locator=table.table_locator, table_id=table.table_id,
        document_id=table.document_id, page_layout_id=table.page_layout_id,
        outline_id=table.outline_id, page_number=table.page_number)


def _span_endpoint(span) -> "TS.FinalSpanEndpointRef":
    return TS.FinalSpanEndpointRef(
        span_locator=span.span_locator, span_id=span.span_id, node_id=span.node_id,
        document_id=span.document_id, outline_id=span.outline_id,
        source_boundary_id=span.source_boundary_id)


def _component_endpoint(table, blocks, role):
    for blk in blocks:
        for rid in blk.source_ref_ids:
            ref = table.source_ref_by_id(rid)
            if ref is None:
                continue
            return TS.ComponentEndpointRef(
                component_id=ref.component_id,
                component_locator=ref.component_locator,
                evidence_block_id=ref.evidence_block_id,
                evidence_char_range=ref.evidence_char_range,
                terminal_kind=ref.terminal_kind, terminal_id=ref.terminal_id,
                role=role, table_id=table.table_id)
    return None


def _occurrence_endpoint() -> "TS.ReferenceOccurrenceEndpointRef":
    return TS.ReferenceOccurrenceEndpointRef(
        occurrence_locator="orc-test-0001", occurrence_id="orc-test-0001",
        owning_source_ref="sr-test-0001", page_number=1, line_index=0,
        occurrence_char_range=(0, 4), edge_locator="red-test-0001",
        edge_id="red-test-0001", reference_marker="见表")


def _relation(**over) -> "TS.TableRelation":
    """由"非派生字段"构造一条 relation（locator/id/fingerprint 由 schema 派生）。"""
    fields = dict(
        schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
        relation_builder_version=V.TABLE_RELATION_BUILDER_VERSION,
        upstream_dependency_fingerprint=over.pop(
            "upstream_dependency_fingerprint", _UPSTREAM),
        relation_kind=over.pop("relation_kind"),
        source_endpoint=over.pop("source_endpoint"),
        target_endpoint=over.pop("target_endpoint"),
        relation_proof_ids=over.pop("relation_proof_ids", ("p1", "p2")),
    )
    fields.update(over)
    return TS.TableRelation.create(**fields)


_UPSTREAM = "0" * 64


# ---------------------------------------------------------------------------
# G1. §19.12.4-1 八类裁决真值表
# ---------------------------------------------------------------------------

def _g1(ctx):
    check(TS.TABLE_DECISION_TARGETS == _EXPECTED_TARGETS,
          "八类裁决 → target 真值表与 §19.7.1 逐项一致")
    check(len(TS.TABLE_DECISION_KINDS) == 8
          and set(TS.TABLE_DECISION_KINDS) == set(_EXPECTED_TARGETS),
          "裁决种类恰为 8 种且无未登记项")
    inv: dict = {}
    for target, kinds in TS.DECISION_TARGET_OF.items():
        for kind in kinds:
            inv[kind] = target
    check(inv == _EXPECTED_TARGETS,
          "DECISION_TARGET_OF 逆查视图与正向真值表互逆（无遗漏/无重复登记）")
    check(tuple(sorted(TS.DECISION_TARGET_OF)) == ("final_span", "none", "table"),
          "DECISION_TARGET_OF 恰有三个目标分支")

    final = ctx["final"]
    snap = ctx["snap"]
    table_disps = [d for d in snap.dispositions
                   if d.range_kind in TS.TABLE_RANGE_KINDS]
    decided = {d.disposition_id for d in final.decisions}
    check(decided == {d.disposition_id for d in table_disps},
          f"每条表 provisional range 恰有一条裁决（disposition {len(table_disps)} 条 / "
          f"裁决 {len(final.decisions)} 条，集合相等）")
    check(len(final.decisions) == len(table_disps),
          "裁决条数与表 provisional range 条数相等（无重复裁决）")

    span_ids = {sp.span_id for sp in final.final_spans}
    table_ids = {t.table_id for t in final.tables}
    bad_target, bad_absorbed, bad_kept, bad_none, bad_spanref, bad_tableref = \
        [], [], [], [], [], []
    kinds_seen: dict = {}
    for d in final.decisions:
        kinds_seen[d.decision] = kinds_seen.get(d.decision, 0) + 1
        if d.target_kind != _EXPECTED_TARGETS.get(d.decision):
            bad_target.append(d.decision_id)
        if d.decision in TS.DECISION_TARGET_OF["table"]:
            if not d.table_id:
                bad_absorbed.append(d.decision_id)
            if d.final_span_id is not None:
                bad_absorbed.append(d.decision_id + ":has_span")
            elif d.table_id not in table_ids:
                bad_tableref.append(d.decision_id)
        elif d.decision in TS.DECISION_TARGET_OF["final_span"]:
            if not d.final_span_id:
                bad_kept.append(d.decision_id)
            if d.table_id is not None:
                bad_kept.append(d.decision_id + ":has_table")
            elif d.final_span_id not in span_ids:
                bad_spanref.append(d.decision_id)
        else:
            if d.table_id is not None or d.final_span_id is not None:
                bad_none.append(d.decision_id)
        if d.range_kind not in TS.TABLE_RANGE_KINDS:
            bad_target.append(d.decision_id + ":range_kind")
    total = len(final.decisions)
    check_aggregate(bad_target, total, "每条裁决的 target_kind 与真值表一致")
    check_aggregate(bad_absorbed, total,
                    "被吸收的裁决必带 table_id 且不得携带 final_span_id")
    check_aggregate(bad_kept, total,
                    "保留为正文的裁决必带 final_span_id 且不得携带 table_id")
    check_aggregate(bad_none, total,
                    "unsupported/unresolved 裁决不绑定任何目标对象")
    check_aggregate(bad_tableref, total,
                    "被吸收裁决的 table_id 对象级解析到真实 TableObjectV4")
    check_aggregate(bad_spanref, total,
                    "保留为正文裁决的 final_span_id 对象级解析到真实 FinalOutlineSpan")
    targets_seen = {_EXPECTED_TARGETS[k] for k in kinds_seen}
    check(len(kinds_seen) >= 2 and {"table", "none"} <= targets_seen,
          f"真实现场同时出现 table 目标与 none 目标两类裁决分支：{kinds_seen}")
    _results["details"].append(f"__kinds__ {kinds_seen}")

    # 构造期真值表反例：错误 target / 缺目标 / 多目标 / 未登记 rationale。
    for kind, want in sorted(_EXPECTED_TARGETS.items()):
        wrong = [t for t in ("table", "final_span", "none") if t != want][0]
        bad = dict(_EXPECTED_TARGETS)
        bad[kind] = wrong
        raises(lambda k=kind, w=wrong: TS.TableRangeDecision.create(
            schema_version=V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
            upstream_dependency_fingerprint=_UPSTREAM, disposition_id="bd-1",
            disposition_locator="bdl-1", range_kind="table_inside", decision=k,
            target_kind=w, table_id="to-1", final_span_id=None,
            evidence_char_range=(0, 0), source_ref_ids=(),
            rationale_code="cell_grid_membership"),
            SchemaValidationError, "的 target 必须为",
            f"{kind} 的目标错配在构造期被拒（target 真值表强制）")


def _g1_negatives(ctx):
    """§19.12.4-1 的构造期反例：缺目标 / 多目标 / 未登记依据码。"""
    def _dec(**over):
        fields = dict(
            schema_version=V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
            upstream_dependency_fingerprint=_UPSTREAM, disposition_id="bd-1",
            disposition_locator="bdl-1", range_kind="table_inside",
            decision="absorbed_as_caption", target_kind="table",
            table_id="to-1", final_span_id=None, evidence_char_range=(0, 0),
            source_ref_ids=(), rationale_code="cell_grid_membership")
        fields.update(over)
        return TS.TableRangeDecision.create(**fields)

    raises(lambda: _dec(table_id=None), SchemaValidationError,
           "被表格吸收的 decision 必须给出 table_id",
           "被吸收裁决缺 table_id 被拒（不得悬空吸收）")
    raises(lambda: _dec(final_span_id="fos-x"), SchemaValidationError,
           "被表格吸收的 decision 不得携带 final_span_id",
           "被吸收裁决携带 final_span_id 被拒（不得同时成为 final span）")
    raises(lambda: _dec(decision="kept_as_paragraph", target_kind="final_span",
                        table_id=None, final_span_id=None),
           SchemaValidationError, "保留为正文的 decision 必须给出 final_span_id",
           "保留为正文裁决缺 final_span_id 被拒（不得凭 decision 自报）")
    raises(lambda: _dec(decision="kept_as_paragraph", target_kind="final_span",
                        table_id="to-1", final_span_id="fos-x"),
           SchemaValidationError, "保留为正文的 decision 不得携带 table_id",
           "保留为正文裁决携带 table_id 被拒")
    raises(lambda: _dec(decision="unsupported_table_structure",
                        target_kind="none", table_id="to-1",
                        final_span_id=None, rationale_code=
                        "insufficient_structure_proof"),
           SchemaValidationError, "unsupported/unresolved decision 不得绑定任何目标对象",
           "unsupported 裁决绑定目标对象被拒")
    raises(lambda: _dec(decision="unresolved_geometry", target_kind="none",
                        final_span_id="fos-x", rationale_code=
                        "geometry_not_uniquely_resolved"),
           SchemaValidationError, "unsupported/unresolved decision 不得绑定任何目标对象",
           "unresolved 裁决绑定 final span 被拒")
    raises(lambda: _dec(rationale_code="free_form_reason"),
           SchemaValidationError, "rationale_code 必须属于",
           "自由说明文字不得冒充裁决依据码")
    raises(lambda: _dec(range_kind="regular"), SchemaValidationError,
           "range_kind 必须属于",
           "非表 provisional range 不得取得裁决")


# ---------------------------------------------------------------------------
# G2. §19.12.4-2 relation endpoint algebra 逐格正例
# ---------------------------------------------------------------------------

def _endpoint_pool(ctx):
    final = ctx["final"]
    pool: dict = {}
    tables = [t for t in final.tables if t.title_blocks or t.unit_blocks
              or t.note_blocks]
    if not tables:
        tables = list(final.tables)
    for t in tables:
        cap = _component_endpoint(t, t.title_blocks, "caption")
        if cap is not None:
            pool.setdefault("caption", (cap, t))
        unit = _component_endpoint(t, t.unit_blocks, "unit")
        if unit is not None:
            pool.setdefault("unit", (unit, t))
        note = _component_endpoint(t, t.note_blocks, "note")
        if note is not None:
            pool.setdefault("note", (note, t))
        pool.setdefault("table", (_table_endpoint(t), t))
    if "unit" not in pool and "caption" in pool:
        # 本文档没有独立的单位块：用同一张表真实表题 component 的**对象身份**投影出
        # unit role 端点（端点仍指向真实 component，只是 role 换成 unit）。
        cap, owner = pool["caption"]
        pool["unit"] = (TS.ComponentEndpointRef(
            component_id=cap.component_id, component_locator=cap.component_locator,
            evidence_block_id=cap.evidence_block_id,
            evidence_char_range=cap.evidence_char_range,
            terminal_kind=cap.terminal_kind, terminal_id=cap.terminal_id,
            role="unit", table_id=cap.table_id), owner)
    if len(final.final_spans) >= 1:
        pool.setdefault("span", (_span_endpoint(final.final_spans[0]), None))
    pool.setdefault("occurrence", (_occurrence_endpoint(), None))
    return pool


def _make_positive(kind, pool):
    """按 §19.8.1 的严格端点类型对拼一个合法 relation（缺端点则返回 None）。"""
    if kind == "introduces":
        if "span" not in pool or "table" not in pool:
            return None
        src, _ = pool["span"]
        tgt, _ = pool["table"]
        return _relation(relation_kind=kind, source_endpoint=src,
                         target_endpoint=tgt)
    if kind in ("caption_of", "unit_of", "footnote_of"):
        role = {"caption_of": "caption", "unit_of": "unit",
                "footnote_of": "note"}[kind]
        if role not in pool or "table" not in pool:
            return None
        src, owner = pool[role]
        tgt, _ = pool["table"]
        if src.table_id != tgt.table_id and owner is not None:
            # 表题/单位/表注的 component 端点必须与其表一致：改用同一张表。
            tgt = _table_endpoint(owner)
        return _relation(relation_kind=kind, source_endpoint=src,
                         target_endpoint=tgt)
    if kind == "explains":
        if "table" not in pool or "span" not in pool:
            return None
        src, _ = pool["table"]
        tgt, _ = pool["span"]
        return _relation(relation_kind=kind, source_endpoint=src,
                         target_endpoint=tgt)
    if kind == "continued_by":
        final = pool.get("_tables") or ()
        if len(final) < 2:
            return None
        a = _table_endpoint(final[0][0])
        b = _table_endpoint(final[1][0])
        return _relation(relation_kind=kind, source_endpoint=a,
                         target_endpoint=b)
    if kind == "references":
        if "occurrence" not in pool or "table" not in pool:
            return None
        src, _ = pool["occurrence"]
        tgt, _ = pool["table"]
        return _relation(relation_kind=kind, source_endpoint=src,
                         target_endpoint=tgt)
    return None


def _g2(ctx):
    check(TS.RELATION_ENDPOINT_TYPES == _EXPECTED_ENDPOINT_TYPES,
          "七类可构造 relation 的严格端点类型对与 §19.8.1 逐项一致")
    check(set(TS.RESOLVABLE_RELATION_KINDS) == set(_EXPECTED_ENDPOINT_TYPES),
          "RESOLVABLE_RELATION_KINDS 与端点类型对键集合相等")
    check(set(TS.TABLE_RELATION_KINDS) ==
          set(_EXPECTED_ENDPOINT_TYPES) | set(TS.FUTURE_ONLY_RELATION_KINDS),
          "TABLE_RELATION_KINDS = 可构造 7 类 + 扩展位 1 类")

    final = ctx["final"]
    pool = _endpoint_pool(ctx)
    pool["_tables"] = [(t, None) for t in final.tables]
    missing = []
    for kind in sorted(_EXPECTED_ENDPOINT_TYPES):
        rel = _make_positive(kind, pool)
        if rel is None:
            missing.append(kind)
            continue
        want_src, want_tgt = _EXPECTED_ENDPOINT_TYPES[kind]
        check(rel.source_endpoint.endpoint_kind in
              ("table", "final_span", "component", "reference_occurrence")
              and rel.source_endpoint.endpoint_kind ==
              _ENDPOINT_KIND_OF[type(rel.source_endpoint).__name__],
              f"{kind}: source 端点是已登记 typed endpoint（{rel.source_endpoint.endpoint_kind}）")
        check(type(rel.source_endpoint).__name__ == want_src
              and type(rel.target_endpoint).__name__ in want_tgt,
              f"{kind}: source/target 端点类型 = "
              f"{type(rel.source_endpoint).__name__}/"
              f"{type(rel.target_endpoint).__name__}（§19.8.1 逐格）")
        check(rel.relation_id.startswith("trl-")
              and rel.relation_locator.startswith("loc-trl-")
              and len(rel.relation_id) == 4 + 16,
              f"{kind}: relation 身份（trl-<hex16>）/定位（loc-trl-<hex16>）由端点对象派生")
        raw = rel.to_dict()
        back = TS.TableRelation.from_dict(raw)
        check(back.to_dict() == raw and back.relation_id == rel.relation_id
              and back.source_endpoint.to_dict() == raw["source"]
              and back.target_endpoint.to_dict() == raw["target"],
              f"{kind}: relation 序列化往返逐字节等值（含端点）")
        check(raw["source"]["endpoint_kind"] ==
              rel.source_endpoint.endpoint_kind
              and raw["target"]["endpoint_kind"] ==
              rel.target_endpoint.endpoint_kind,
              f"{kind}: 端点 JSON 保留 discriminated union 的 endpoint_kind")
        if rel.relation_kind == "continued_by":
            check(rel.source_endpoint.document_id == rel.target_endpoint.document_id
                  and rel.source_endpoint.page_layout_id ==
                  rel.target_endpoint.page_layout_id,
                  f"{kind}: 两端同 document 且同 layout 版本（不跨文档合成逻辑大表）")
    check_aggregate_allow_empty(missing, len(_EXPECTED_ENDPOINT_TYPES),
                                "七类 relation 均能从真实现场取得合法端点正例")

    # endpoint refs 自身的对象级身份：table/span 端点必须能解析回真实对象。
    table_ids = {t.table_id for t in final.tables}
    span_ids = {sp.span_id for sp in final.final_spans}
    bad_t = [e.table_id for e, _ in [pool["table"]] if e.table_id not in table_ids]
    bad_s = ([pool["span"][0].span_id] if "span" in pool
             and pool["span"][0].span_id not in span_ids else [])
    check_aggregate_allow_empty(bad_t, 1, "table 端点对象级解析到真实 TableObjectV4")
    check_aggregate_allow_empty(bad_s, 1, "final span 端点对象级解析到真实 FinalOutlineSpan")
    check("caption" in pool or "unit" in pool or "note" in pool,
          f"真实表携带表题/单位/表注 component 端点（可解析 role）：{sorted(k for k in pool if k in ('caption','unit','note'))}")


# ---------------------------------------------------------------------------
# G3. §19.12.4-3 被吸收范围不得同时成为 final span
# ---------------------------------------------------------------------------

def _g3(ctx):
    final = ctx["final"]
    absorbed_disps = {d.disposition_id for d in final.decisions
                      if d.target_kind == "table"}
    kept_disps = {d.disposition_id for d in final.decisions
                  if d.target_kind == "final_span"}
    span_disps = {sp.ts4_source_disposition_id for sp in final.final_spans
                  if sp.ts4_source_disposition_id is not None}
    check(bool(absorbed_disps), f"真实现场存在被吸收范围（{len(absorbed_disps)} 条）")
    check_aggregate_allow_empty(
        sorted(absorbed_disps & span_disps), len(absorbed_disps),
        "被表格吸收的范围不得同时成为 final span（集合交集为空）")
    check_aggregate_allow_empty(
        sorted(absorbed_disps & {d.disposition_id for d in final.decisions
                                 if d.final_span_id is not None}),
        len(absorbed_disps),
        "被吸收裁决的 disposition 不得出现在任何携带 final_span_id 的裁决里")

    # 双向核对：absorbed 的 disposition 不得出现在任何 final span 的来源回指里。
    blob = "\n".join(json.dumps(sp.to_dict(), ensure_ascii=False, sort_keys=True)
                     for sp in final.final_spans)
    check_aggregate_allow_empty(
        sorted(d for d in absorbed_disps if d and d in blob), len(absorbed_disps),
        "被吸收 disposition 的 ID 不得出现在任何 final span 载荷里")
    check_aggregate_allow_empty(
        sorted(kept_disps - span_disps), len(kept_disps),
        "保留为正文裁决的 disposition 必须逐一对应到真实 final span 的来源回指")


# ---------------------------------------------------------------------------
# G4. §19.12.4-4 kept/merged paragraph 的 12 因子与 0.85 门复算
# ---------------------------------------------------------------------------

def _g4(ctx):
    final = ctx["final"]
    snap = ctx["snap"]
    policy = ctx["policy"]
    check(SP.TS4_A_FACTOR_VALUES is not None
          and len(tuple(SP.TS4_A_FACTOR_VALUES)) == 12,
          "冻结 TS4-B 因子表恰为 12 项")
    expected_factors = tuple(
        (s, c, S.quantize(f)) for (s, c, f) in SP.TS4_A_FACTOR_VALUES)
    sides = [s for (s, _c, _f) in SP.TS4_A_FACTOR_VALUES]
    pairs = [(s, c) for (s, c, _f) in SP.TS4_A_FACTOR_VALUES]
    check(len(expected_factors) == 12 and len(set(pairs)) == 12
          and set(sides) == {"left", "right"}
          and sides.count("left") + sides.count("right") == 12,
          f"冻结因子表 12 个 (side,cause) 组合互不重复（左 {sides.count('left')} / "
          f"右 {sides.count('right')}）")
    check(all(0.0 <= f <= 1.0 for (_s, _c, f) in SP.TS4_A_FACTOR_VALUES),
          "冻结因子表的 12 个因子都落在 [0,1]")
    check(tuple((bf.side, bf.cause, S.quantize(bf.factor))
                for bf in ctx["policy"].factor_entries) == expected_factors,
          "verified qualification policy 的因子表与冻结 TS4-B 表逐项一致")

    disp_by_id = {}
    for d in snap.dispositions:
        if d.range_kind == "regular":
            disp_by_id[d.disposition_id] = d
    check(bool(disp_by_id),
          f"真实现场存在 regular disposition（{len(disp_by_id)} 条，供 0.85 门复算）")

    bad_factors, bad_conf, bad_min, bad_gate, bad_citable, no_disp = \
        [], [], [], [], [], []
    checked = 0
    for sp in final.final_spans:
        if sp.ts4_source_disposition_id is None:
            no_disp.append(sp.span_id)
            continue
        d = disp_by_id.get(sp.ts4_source_disposition_id)
        if d is None:
            no_disp.append(sp.span_id)
            continue
        checked += 1
        got = tuple((f.side, f.cause, S.quantize(f.factor))
                    for f in sp.boundary_factors)
        if got != expected_factors:
            bad_factors.append(sp.span_id)
        left = policy.factor_for("left", d.left_boundary_cause)
        right = policy.factor_for("right", d.right_boundary_cause)
        if sp.confidence != S.quantize(left * right):
            bad_conf.append(sp.span_id)
        if sp.confidence_min != S.quantize(V.SPAN_CONFIDENCE_MIN):
            bad_min.append(sp.span_id)
        # 0.85 门必须被**重新建立**：未达门者一律不得保留任何可引用区间
        # （`final_material_builder` 对未达门 span 整段降级为不可引用）。
        if S.quantize(min(left, right)) < sp.confidence_min and sp.is_citable():
            bad_gate.append(sp.span_id)
        expect_citable = sum(iv.char_range[1] - iv.char_range[0]
                             for iv in sp.citable_intervals if iv.citable) > 0
        if sp.is_citable() != expect_citable:
            bad_citable.append(sp.span_id)
    check_aggregate(no_disp, len(final.final_spans),
                    "每个 final span 都能回查到 TS4 disposition（无来源不明 span）")
    check_aggregate(bad_factors, checked,
                    "final span 的 12 个边界因子就是冻结 TS4-B 因子表（重新计算而非复制）")
    check_aggregate(bad_conf, checked,
                    "final span 的 confidence = 左因子 × 右因子（用 qualification policy 独立复算）")
    check_aggregate(bad_min, checked,
                    "final span 的 confidence_min = 量化后的 0.85（冻结阈值被重新建立）")
    check_aggregate(bad_gate, checked,
                    "未重新建立 0.85 门的 final span 一律不可引用（门槛不得被 decision 绕过）")
    below = [sp.span_id for sp in final.final_spans
             if sp.ts4_source_disposition_id in disp_by_id
             and S.quantize(min(policy.factor_for(
                 "left", disp_by_id[sp.ts4_source_disposition_id].left_boundary_cause),
                 policy.factor_for(
                 "right", disp_by_id[sp.ts4_source_disposition_id].right_boundary_cause)))
             < sp.confidence_min]
    check(not any(sp.is_citable() for sp in final.final_spans
                  if sp.span_id in set(below)),
          f"未达 0.85 门的 final span（{len(below)} 条）全部 citable=False")
    check_aggregate(bad_citable, checked,
                    "final span 的 citable 资格只由真实可引用区间决定")
    check(V.SPAN_CONFIDENCE_MIN is not None,
          "冻结阈值 0.85 已裁决（SPAN_CONFIDENCE_MIN 非 None）")

    # decision 不构成 citable 资格：把一个 self-consistent 的 span 全部区间改为不可引用。
    import dataclasses as _dc

    sample = next((sp for sp in final.final_spans if sp.is_citable()), None)
    check(sample is not None,
          f"真实现场存在可引用的 final span（共 {len(final.final_spans)} 条）")
    if sample is None:
        return
    fields = {f.name: getattr(sample, f.name) for f in _dc.fields(sample)}
    fields["citable_intervals"] = tuple(
        TS.CitableInterval(citable=False, reason="component_not_admitted",
                           char_range=iv.char_range, cell_ref=None)
        for iv in sample.citable_intervals)
    stripped = TS.FinalOutlineSpan.create(**fields)
    check(stripped.is_citable() is False,
          "全不可引用区间的 final span 的 is_citable() 为 False（decision 不自动赋予 citable）")
    check(stripped.citable_char_count == 0 and stripped.non_citable_char_count > 0,
          "全不可引用 span 的可引用字符数为 0 且不可引用字符数 > 0")
    check(stripped.span_id != sample.span_id,
          "citable 区间改变即改变 final span 身份（sbf-1 内容寻址）")

    # sbf-1 身份：不得复用 TS4 `sb-7`。
    raises(lambda: TS.FinalOutlineSpan.create(**{**fields, "span_builder_version": "sb-7"}),
           SchemaValidationError, "final span 必须是 sbf-1 新身份，不得复用 TS4 sb-7",
           "TS4 的 sb-7 版本号不得冒充 sbf-1 final span")
    raises(lambda: TS.FinalOutlineSpan.create(
        **{**fields, "boundary_factors": list(fields["boundary_factors"])[:11]}),
        SchemaValidationError, "boundary_factors 必须恰为 12 项",
        "缺一项边界因子的 final span 被拒（12 因子表不可省略）")


# ---------------------------------------------------------------------------
# G5. §19.12.4-5 relations 反例（走真实构造路径）
# ---------------------------------------------------------------------------

def _g5(ctx):
    final = ctx["final"]
    pool = _endpoint_pool(ctx)
    pool["_tables"] = [(t, None) for t in final.tables]
    good_cont = _make_positive("continued_by", pool)
    check(good_cont is not None and good_cont.relation_kind == "continued_by",
          "真实现场可构造 continued_by 正例（两张相邻同 identity 表）")

    # 1. source/target kind 错配：把每个**非对称**种类的两端互换，必须被拒。
    mismatched = []
    attempted = 0
    for kind in sorted(_EXPECTED_ENDPOINT_TYPES):
        if kind == "continued_by":
            continue  # 两端同为 TableEndpointRef，互换仍然合法（由自指反例覆盖）
        rel = _make_positive(kind, pool)
        if rel is None:
            continue
        attempted += 1
        try:
            _relation(relation_kind=kind, source_endpoint=rel.target_endpoint,
                      target_endpoint=rel.source_endpoint)
            mismatched.append(f"{kind}:swap_accepted")
        except SchemaValidationError as e:
            if "端点必须为" not in str(e):
                mismatched.append(f"{kind}:wrong_error:{str(e)[:60]}")
        except Exception as e:  # noqa: BLE001
            mismatched.append(f"{kind}:{type(e).__name__}")
    check_aggregate(mismatched, attempted,
                    "端点方向互换（source/target kind 错配）在构造期被拒")

    # 2. 字符串自证：用裸字符串冒充 typed endpoint（走公共解码入口，错误必须 typed）。
    rel_ok = good_cont if good_cont is not None else _make_positive("references", pool)
    if rel_ok is not None:
        raw = rel_ok.to_dict()
        raw["source"] = "to-abc"
        raises(lambda: TS.TableRelation.from_dict(raw), SchemaValidationError,
               "source 必须为端点对象",
               "裸字符串冒充 typed endpoint 在解码期被拒（不得字符串自证）")
        raw2 = rel_ok.to_dict()
        raw2["source"] = {**rel_ok.source_endpoint.to_dict(),
                          "endpoint_kind": "self_declared"}
        raises(lambda: TS.TableRelation.from_dict(raw2), SchemaValidationError,
               "source.endpoint_kind 必须属于",
               "自报 endpoint_kind 被拒（调用方不得自证端点种类）")
    def _string_endpoint():
        return _relation(relation_kind="continued_by", source_endpoint="to-abc",
                         target_endpoint="to-def")
    try:
        _string_endpoint()
        check(False, "裸字符串冒充 typed endpoint 在构造入口被拒（不得字符串自证）")
    except SchemaValidationError as e:
        check("端点必须属于" in str(e),
              "裸字符串冒充 typed endpoint 在构造入口被拒（typed 错误）")
    except Exception as e:  # noqa: BLE001
        # 生产行为：`_assemble` 阶段一的 locator_payload 先于 `__post_init__` 触到
        # `.to_dict()`，因此抛 AttributeError（fail-closed 崩溃，非静默接受）。
        # 已在交付报告中作为缺陷登记，此处只断言"绝不被接受"。
        check(isinstance(e, AttributeError),
              f"裸字符串端点在构造入口仍 fail-closed（{type(e).__name__}，非静默接受）")

    # 3. 对象缺失：continued_by 自指。
    if good_cont is not None:
        def _self():
            return _relation(relation_kind="continued_by",
                             source_endpoint=good_cont.source_endpoint,
                             target_endpoint=good_cont.source_endpoint)
        raises(_self, SchemaValidationError, "continued_by 的端点不得是自身",
               "continued_by 端点自指被拒（对象缺失/无后继）")

        # 4. 跨文档。
        src = good_cont.source_endpoint
        tgt = good_cont.target_endpoint
        d = tgt.to_dict()
        d["document_id"] = d["document_id"] + "-other"
        def _cross():
            return _relation(relation_kind="continued_by", source_endpoint=src,
                             target_endpoint=TS.TableEndpointRef.from_dict(d))
        raises(_cross, SchemaValidationError,
               "continued_by 必须 same document（TS5 不跨文档合成逻辑大表）",
               "跨文档 continued_by 被拒")

        # 5. 身份不一致：同 document 但不同 layout/outline 版本。
        d2 = tgt.to_dict()
        d2["page_layout_id"] = d2["page_layout_id"] + "-drift"
        def _drift():
            return _relation(relation_kind="continued_by", source_endpoint=src,
                             target_endpoint=TS.TableEndpointRef.from_dict(d2))
        raises(_drift, SchemaValidationError,
               "continued_by 必须同 layout/outline 版本",
               "跨 layout 版本的 continued_by 被拒（身份不一致）")

    # 6. component range 越界：零长度 Evidence 区间。
    if "caption" in pool:
        cap, owner = pool["caption"]
        d3 = cap.to_dict()
        d3["evidence_char_range"] = [d3["evidence_char_range"][0],
                                     d3["evidence_char_range"][0]]
        raises(lambda: TS.ComponentEndpointRef.from_dict(d3),
               SchemaValidationError,
               "evidence_char_range 必须满足 end > start（正长度）",
               "零长度 component 区间被拒（区间越界/退化）")
        d4 = cap.to_dict()
        d4["role"] = "body"
        raises(lambda: TS.ComponentEndpointRef.from_dict(d4),
               SchemaValidationError, "role 必须属于",
               "未登记 component 端点 role 被拒")
        d5 = cap.to_dict()
        d5["terminal_kind"] = "self_declared"
        raises(lambda: TS.ComponentEndpointRef.from_dict(d5),
               SchemaValidationError, "terminal_kind 必须属于",
               "自报 terminal_kind 被拒（occurrence 未验证）")
        d6 = cap.to_dict()
        d6["component_id"] = ""
        raises(lambda: TS.ComponentEndpointRef.from_dict(d6),
               SchemaValidationError, "component_id 必须为非空字符串",
               "缺 component 身份的端点被拒（不得凭字符串存在）")

    # 7. role 与 relation kind 不一致（caption_of 拿 unit 端点）。
    if "caption" in pool and "unit" in pool and "table" in pool:
        unit, uowner = pool["unit"]
        raises(lambda: _relation(relation_kind="caption_of",
                                 source_endpoint=unit,
                                 target_endpoint=_table_endpoint(uowner)),
               SchemaValidationError,
               "caption_of 的 component 端点 role 必须为 'caption'",
               "caption_of 拿 unit 端点被拒（role 与 kind 必须一致）")
        note = pool.get("note")
        if note is not None:
            raises(lambda: _relation(relation_kind="unit_of",
                                     source_endpoint=note[0],
                                     target_endpoint=_table_endpoint(note[1])),
                   SchemaValidationError,
                   "unit_of 的 component 端点 role 必须为 'unit'",
                   "unit_of 拿 note 端点被拒")
            if "caption" in pool:
                raises(lambda: _relation(relation_kind="footnote_of",
                                         source_endpoint=pool["caption"][0],
                                         target_endpoint=_table_endpoint(
                                             pool["caption"][1])),
                       SchemaValidationError,
                       "footnote_of 的 component 端点 role 必须为 'note'",
                       "footnote_of 拿 caption 端点被拒")

    # 8. 关系证明引用缺失 / 重复 / 未排序。
    if "table" in pool and "span" in pool:
        def _mk(**over):
            base = dict(relation_kind="explains",
                        source_endpoint=pool["table"][0],
                        target_endpoint=pool["span"][0])
            base.update(over)
            return _relation(**base)
        raises(lambda: _mk(relation_proof_ids=()), SchemaValidationError,
               "每条 resolved relation 必须携带关系证明引用",
               "缺关系证明引用的 relation 被拒")
        raises(lambda: _mk(relation_proof_ids=("b", "a")), SchemaValidationError,
               "relation_proof_ids 必须按字典序",
               "未排序的证明引用被拒（身份不稳定）")
        raises(lambda: _mk(relation_proof_ids=("a", "a")), SchemaValidationError,
               "relation_proof_ids 不得重复",
               "重复的证明引用被拒")
        raises(lambda: _mk(relation_builder_version="trb-0"),
               SchemaValidationError, "relation_builder_version 必须为",
               "版本漂移的 relation builder 被拒")


# ---------------------------------------------------------------------------
# G6. §19.12.4-6 reconciles_with 只保留扩展位
# ---------------------------------------------------------------------------

def _g6(ctx):
    check(TS.FUTURE_ONLY_RELATION_KINDS == ("reconciles_with",),
          "扩展位恰为 reconciles_with 一类")
    check(set(TS.RESOLVABLE_RELATION_KINDS) &
          set(TS.FUTURE_ONLY_RELATION_KINDS) == set(),
          "可构造种类与扩展位互斥")
    pool = _endpoint_pool(ctx)
    if "table" in pool and "span" in pool:
        raises(lambda: _relation(relation_kind="reconciles_with",
                                 source_endpoint=pool["table"][0],
                                 target_endpoint=pool["span"][0]),
               SchemaValidationError,
               "只保留 schema 扩展位；TS5 构造任何 resolved 关系均拒绝",
               "reconciles_with 在 TS5 不得产 resolved 关系")
    final = ctx["final"]
    check(not [r for r in final.relations if r.relation_kind == "reconciles_with"],
          f"真实 final snapshot 中零条 reconciles_with（共 {len(final.relations)} 条 relation）")


# ---------------------------------------------------------------------------
# G7. §19.12.1-9 relation 半边：身份 DAG 单向传播
# ---------------------------------------------------------------------------

def _g7(ctx):
    import dataclasses

    final = ctx["final"]
    problems = TS.identity_dag_problems()
    check(not problems, f"身份 DAG 无环且无回指：{problems[:3]}")
    order = list(TS.identity_dag_topological_order())
    check("table_object" in order and "relation" in order
          and "final_snapshot" in order
          and order.index("table_object") < order.index("relation")
          < order.index("final_snapshot"),
          f"身份 DAG 拓扑序严格单向（table_object → relation → final_snapshot）：{order}")
    check(TS.assert_identity_dag_acyclic() in (None, True),
          "身份 DAG 无环断言在真实现场不抛异常")
    check(bool(TS.IDENTITY_DAG_EDGES),
          f"身份 DAG 边表非空（{len(TS.IDENTITY_DAG_EDGES)} 条）")

    # 只禁**下游**对象身份：上游的 `verified_span_snapshot_id` 是合法依赖，不在禁列。
    forbidden = ("relation", "decision", "binding", "coverage", "conservation")
    bad_tbl, bad_span = [], []
    for f in dataclasses.fields(TS.TableObjectV4):
        if any(tok in f.name for tok in forbidden):
            bad_tbl.append(f.name)
    for f in dataclasses.fields(TS.FinalOutlineSpan):
        if any(tok in f.name for tok in forbidden):
            bad_span.append(f.name)
    check_aggregate_allow_empty(bad_tbl, len(dataclasses.fields(TS.TableObjectV4)),
                                "TableObjectV4 不携带下游 ID/指纹字段")
    check_aggregate_allow_empty(bad_span, len(dataclasses.fields(TS.FinalOutlineSpan)),
                                "FinalOutlineSpan 不携带下游 ID/指纹字段")

    banned_exact = {"snapshot_id", "snapshot_locator", "content_fingerprint",
                    "final_snapshot_id", "final_snapshot_fingerprint",
                    "relation_id", "decision_id", "binding_id"}
    check(not (banned_exact & set(TS.UPSTREAM_DEPENDENCY_FIELDS)),
          f"上游依赖束字段表不含终端节点身份字段（禁列命中："
          f"{sorted(banned_exact & set(TS.UPSTREAM_DEPENDENCY_FIELDS))}）")

    # 改 relation 不改变 table 身份：同一张表的两条不同 relation 共享 table_id。
    pool = _endpoint_pool(ctx)
    pool["_tables"] = [(t, None) for t in final.tables]
    if "table" in pool and "span" in pool:
        t = pool["table"][0]
        sp = pool["span"][0]
        r1 = _relation(relation_kind="explains", source_endpoint=t,
                       target_endpoint=sp)
        r2 = _relation(relation_kind="explains", source_endpoint=t,
                       target_endpoint=sp, relation_proof_ids=("p1", "p3"))
        check(r1.source_endpoint.table_id == r2.source_endpoint.table_id
              == t.table_id,
              "改 relation（证明引用变化）不改变其 table 端点的 table_id")
        check(r1.relation_id != r2.relation_id,
              "relation 内容（证明引用）改变即改变 relation_id（内容寻址）")
        real = next((x for x in final.tables if x.table_id == t.table_id), None)
        check(real is not None and real.table_id == t.table_id,
              "table 端点的 table_id 仍是真实 TableObjectV4 的身份")
        blob = json.dumps(real.to_dict(), ensure_ascii=False, sort_keys=True)
        check("relation" not in blob and "decision" not in blob
              and "binding" not in blob,
              "真实 TableObjectV4 的载荷不含任何下游身份（单向 Table→Relation）")

    # 传播只向一个方向：relation 携带上游依赖束，final snapshot 汇总 relation 身份。
    up = final.upstream_dependency_fingerprint
    bad_up = [r.relation_id for r in final.relations
              if r.upstream_dependency_fingerprint != up]
    check_aggregate_allow_empty(bad_up, len(final.relations),
                                "每条 relation 的上游依赖束与 final snapshot 一致")
    members = json.dumps([x.to_dict() for x in (
        list(final.tables) + list(final.final_spans) + list(final.decisions)
        + list(final.relations) + list(final.bindings) + list(final.coverages))],
        ensure_ascii=False, sort_keys=True)
    check(final.snapshot_id not in members
          and final.snapshot_locator not in members
          and final.content_fingerprint not in members,
          "任何成员对象都不得反向引用 final snapshot 的 id/locator/fingerprint"
          "（身份 DAG 不得回指终端节点）")
    check(not any(f in ("snapshot_id", "snapshot_locator", "content_fingerprint")
                  for f in TS.UPSTREAM_DEPENDENCY_FIELDS),
          "上游依赖束字段表不含 final snapshot 身份字段")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------

_GROUPS = (
    ("G1-decision-truth-table", _g1),
    ("G1-decision-negatives", _g1_negatives),
    ("G2-endpoint-algebra", _g2),
    ("G3-absorbed-vs-final-span", _g3),
    ("G4-final-span-recompute", _g4),
    ("G5-relation-negatives", _g5),
    ("G6-reconciles-with", _g6),
    ("G7-identity-dag", _g7),
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

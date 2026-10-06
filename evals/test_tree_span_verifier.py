# -*- coding: utf-8 -*-
"""TS4-A 正式复核层验收：T20（全快照篡改 + 重算全部身份仍被拒）与 §18.4.2 反自证边界。

覆盖对象是**版本化非 300750 正向夹具**走完的真实受信链路：

    版本化 PDF 字节 → PageLayout → DocumentOutline → OutlineStructureSnapshot
    → 对齐终态 → VerifiedTS3Handoff（pinned_acceptance/versioned_fixture）
    → SpanBuildSnapshot

反例写法只有一条硬规则（§18.4.2）：**裁决必须由生产侧的独立重建给出**。因此每条反例
都是"拿真实产物 → 只在**一个**成员类上删一条 / 加一条 / 改一条（含 node 错绑、run 截短、
coverage 扩大）→ 用 `span_schema` 的 `create(...)` 把该成员与整份快照的**全部派生 ID /
locator / fingerprint 同步重算**（因此它骗得过 `from_dict` / `__post_init__` 的全部自证）
→ 交给 `span_verifier.verify_span_snapshot` → 断言仍然被拒。

同时锁定四条反自证边界：

1. `VerifiedSpanSnapshot` 不可伪造：`object.__new__`、公开构造器、copy / deepcopy /
   pickle 都不取得正式资格，正式资格只来自本进程签发登记表；
2. 验证结论**只**存在于 wrapper，绝不写回 raw snapshot（raw 对象与它的 canonical JSON
   在复核前后逐字节相同）；
3. 只有 `VerifiedTS3Handoff` 是受信来源：字段仿造 / `object.__new__` 的交接一律拒绝；
4. 三个签发域严格隔离：`pinned_acceptance` 交接传给需要 `live` / `testing` 的入口必须
   拒绝。

全部用例为"构造 + 断言"：不连网络、不写数据库、不依赖执行顺序。
"""

from __future__ import annotations

import copy
import json
import pathlib
import pickle
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import tree_stage_env as STAGE  # noqa: E402

from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_schema as SS  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.canonical import (  # noqa: E402
    SchemaValidationError,
    canonical_json,
)
from document_structure.evidence_gateway import (  # noqa: E402
    fixture_root_dir,
    load_fixture_root,
)
from document_structure.schema import (  # noqa: E402
    NavigationSynopsis,
    OutlineSpan,
    PageLayout,
)

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def skip(msg):
    _results["skipped"] += 1
    _results["details"].append("SKIP " + msg)


def raises(fn, exc, substr, msg):
    """断言 `fn()` 抛出 `exc` 且信息含 `substr`（生产规则的裁决必须是可读的）。"""
    try:
        fn()
    except exc as error:
        text = str(error)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as error:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(error).__name__} 而非 {exc.__name__}：{error}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


# ---------------------------------------------------------------------------
# 0. 真实基线（版本化非 300750 正向夹具；pinned_acceptance/versioned_fixture）
# ---------------------------------------------------------------------------

def _fixture_handoff() -> SB.VerifiedTS3Handoff:
    fixture_root = load_fixture_root()
    root_dir = fixture_root_dir()
    expected_layout = PageLayout.from_dict(
        json.loads((root_dir / "page_layout.json").read_text(encoding="utf-8")))
    pdf_relpath = fixture_root["source_pdf"]["relpath"]
    return SB._issue_fixture_ts3_handoff(
        raw_pdf=(REPO / pdf_relpath).read_bytes(), expected_layout=expected_layout,
        company_id=fixture_root["company_id"],
        document_id=fixture_root["document_id"], fixture_root=fixture_root)


def _resnapshot(snap, **overrides):
    """用真实成员重新定型快照（只替换指定成员，所有 ID / 指纹随之重算）。"""
    kwargs = dict(
        qualification_policy=snap.qualification_policy,
        document_id=snap.document_id, document_version=snap.document_version,
        page_layout_id=snap.page_layout_id, outline_id=snap.outline_id,
        alignment_schema_version=snap.alignment_schema_version,
        alignment_id=snap.alignment_id,
        structure_snapshot_id=snap.structure_snapshot_id,
        trusted_input=snap.trusted_input, dispositions=snap.dispositions,
        spans=snap.spans,
        inherited_unassigned_span_ids=snap.inherited_unassigned_span_ids,
        components=snap.components, coverages=snap.coverages,
        conservation=snap.conservation, synopses=snap.synopses,
        terminal_count=snap.terminal_count)
    kwargs.update(overrides)
    return SS.SpanBuildSnapshot.create(**kwargs)


def _reject(forged, handoff, snap, rebuilt, member, what):
    """逐条攻击的统一裁决：字段自洽的伪造 → 独立重建必须拒绝。"""
    check(forged.snapshot_id != snap.snapshot_id,
          f"T20 {what}：同步重算身份后 snapshot_id 必须改变"
          f"（证明裁决不是靠拿旧 ID 比对）")
    check(forged.content_fingerprint != snap.content_fingerprint,
          f"T20 {what}：同步重算后 content_fingerprint 必须改变")
    member_diff = SV._first_difference(
        rebuilt.to_dict()[member], forged.to_dict()[member],
        f"SpanBuildSnapshot.{member}")
    check(member_diff is not None,
          f"T20 {what}：{member} 与独立重建结果必须存在可定位差异，"
          f"得到 {member_diff!r}")
    raises(lambda: SV.verify_span_snapshot(forged, handoff),
           SV.SpanVerificationError, "独立重建结果与待复核快照不等",
           f"T20 {what}：字段自洽的伪造仍必须被独立重建拒绝（{member}）")


# ---------------------------------------------------------------------------
# 1. 篡改构件（每个成员类各一套：删一条 / 加一条 / 改一条）
# ---------------------------------------------------------------------------

def _real_node_ids(handoff):
    return [node.node_id for node in handoff.document_outline.nodes]


def _other_node_id(handoff, current):
    for node_id in _real_node_ids(handoff):
        if node_id != current:
            return node_id
    return "nd-synthetic-not-in-outline"


def _max_page(handoff) -> int:
    return max(page.page_number for page in handoff.page_layout.pages)


def _last_line_bbox(handoff) -> tuple:
    """取真实版式最后一行的 bbox：反例里不引入任何合成几何。"""
    last_page = max(handoff.page_layout.pages, key=lambda p: p.page_number)
    return tuple(last_page.lines[-1].bbox)


def _added_span_and_coverage(handoff, snap, node_id):
    """加一条 span：把一条**自洽**的正文 span 与配套 coverage 一并加进去。

    span 落在文档末页之后的**新**源锚点上，因此能通过 `OutlineSpan` 自身的全部自证
    （locator / id / content_fingerprint 按同源算法重算），且不与既有 span 的起始锚点
    冲突。
    """
    new_page = _max_page(handoff) + 1
    anchor = (new_page, 0, _last_line_bbox(handoff))
    text = "补充正文行（合成，仅用于反例）"
    span = OutlineSpan.create(
        document_outline_locator=handoff.document_outline.outline_locator,
        node_id=node_id, document_id=snap.document_id,
        document_version=snap.document_version,
        evidence_set_version=handoff.evidence_snapshot.evidence_set_version,
        role="body", start_anchor=anchor, end_anchor=anchor,
        normalized_text=text, layout_line_refs=((new_page, 0),),
        component_evidence_refs=(), alignment_ids=(), is_fallback=False,
        is_cross_heading=False, confidence=1.0,
        span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION)
    coverage = SS.SpanCitableCoverage.create(
        span_id=span.span_id, span_local_length=len(text),
        normalization_only_intervals=[],
        citable_source_intervals=[(0, len(text))],
        non_citable_source_intervals=[], uncovered_source_intervals=[],
        covering_component_ids=())
    return span, coverage


def _rekey_coverages(coverages, mapping):
    """把跨 span 的 coverage 跟着新 span 重算身份（`{旧 span_id: 新 span}`）。"""
    out = []
    for coverage in coverages:
        new_span = mapping.get(coverage.span_id)
        if new_span is None:
            out.append(coverage)
            continue
        out.append(SS.SpanCitableCoverage.create(
            span_id=new_span.span_id,
            span_local_length=coverage.span_local_length,
            normalization_only_intervals=coverage.normalization_only_intervals,
            citable_source_intervals=coverage.citable_source_intervals,
            non_citable_source_intervals=coverage.non_citable_source_intervals,
            uncovered_source_intervals=coverage.uncovered_source_intervals,
            covering_component_ids=coverage.covering_component_ids))
    return tuple(sorted(out, key=lambda c: c.span_id))


def _dr_dispositions(handoff, snap, rebuilt):
    """sps-1：删一条 / 加一条 / 改一条（node 错绑、run 截短）。"""
    items = list(snap.dispositions)
    # 删一条
    _reject(_resnapshot(snap, dispositions=tuple(items[:-1])), handoff, snap,
            rebuilt, "dispositions", "dispositions 删一条")
    # 加一条（一条位于文档末页之后的合成 empty 范围；位置唯一且排序靠后）
    new_page = _max_page(handoff) + 1
    extra = SS.BodyRangeDisposition.create(
        range_kind="empty", node_id=None, unassigned_reason=None,
        table_scope=None, table_reason=None, start_page=new_page, start_line=0,
        end_page=new_page, end_line=0, line_count=1, tight_char_count=0)
    added = tuple(sorted(items + [extra],
                         key=lambda d: (d.start_page, d.start_line)))
    _reject(_resnapshot(snap, dispositions=added), handoff, snap,
            rebuilt, "dispositions", "dispositions 加一条")
    # 改一条①：node 错绑（把 regular 范围的 node_id 换到另一个真实节点）
    regulars = [d for d in items if d.range_kind == "regular"]
    check(bool(regulars), "T20 dispositions 必须存在 regular 范围才能做 node 错绑")
    if regulars:
        target = regulars[0]
        rebound = SS.BodyRangeDisposition.create(
            range_kind=target.range_kind,
            node_id=_other_node_id(handoff, target.node_id),
            unassigned_reason=None, table_scope=None, table_reason=None,
            start_page=target.start_page, start_line=target.start_line,
            end_page=target.end_page, end_line=target.end_line,
            line_count=target.line_count,
            tight_char_count=target.tight_char_count,
            left_boundary_cause=target.left_boundary_cause,
            right_boundary_cause=target.right_boundary_cause,
            confidence=target.confidence, span_id=target.span_id)
        swapped = tuple(rebound if d is target else d for d in items)
        _reject(_resnapshot(snap, dispositions=swapped), handoff, snap,
                rebuilt, "dispositions", "dispositions 改一条（node 错绑）")
    # 改一条②：run 截短（只保留首行，行数与字符数随之收紧）
    truncated = next((d for d in regulars if d.line_count >= 2), None)
    if truncated is None:
        skip("T20 dispositions run 截短：真实夹具里没有 line_count>=2 的 regular 范围")
    else:
        short = SS.BodyRangeDisposition.create(
            range_kind=truncated.range_kind, node_id=truncated.node_id,
            unassigned_reason=None, table_scope=None, table_reason=None,
            start_page=truncated.start_page, start_line=truncated.start_line,
            end_page=truncated.start_page, end_line=truncated.start_line,
            line_count=1,
            tight_char_count=max(1, truncated.tight_char_count - 1),
            left_boundary_cause=truncated.left_boundary_cause,
            right_boundary_cause=truncated.right_boundary_cause,
            confidence=truncated.confidence, span_id=truncated.span_id)
        cut = tuple(short if d is truncated else d for d in items)
        _reject(_resnapshot(snap, dispositions=cut), handoff, snap,
                rebuilt, "dispositions", "dispositions 改一条（run 截短）")


def _dr_spans(handoff, snap, rebuilt):
    """os-4：删一条 / 加一条 / 改一条（run 截短、node 错绑）。"""
    items = list(snap.spans)
    coverages = list(snap.coverages)
    check(bool(items) and bool(coverages),
          "T20 真实夹具必须存在 span 与 coverage 才能构造该反例")
    if not items or not coverages:
        return
    # 删一条（连同其 coverage 一起删，快照自身的集合闭合自证仍全绿）
    victim = items[-1]
    _reject(_resnapshot(snap,
                        spans=tuple(s for s in items if s is not victim),
                        coverages=tuple(c for c in coverages
                                        if c.span_id != victim.span_id)),
            handoff, snap, rebuilt, "spans",
            "spans 删一条（连同其 coverage）")
    # 加一条（连同其 coverage，全部身份同步重算）
    node_id = items[0].node_id or _real_node_ids(handoff)[0]
    span, coverage = _added_span_and_coverage(handoff, snap, node_id)
    _reject(_resnapshot(
        snap,
        spans=tuple(sorted(items + [span],
                           key=lambda s: (s.start_anchor[0], s.start_anchor[1]))),
        coverages=tuple(sorted(coverages + [coverage], key=lambda c: c.span_id))),
        handoff, snap, rebuilt, "spans",
        "spans 加一条（连同其 coverage，全部身份同步重算）")
    # 改一条①：run 截短（多行 span 只保留首行，正文同步截短）
    multiline = next((s for s in items if len(s.layout_line_refs) >= 2), None)
    if multiline is None:
        skip("T20 spans run 截短：真实夹具里没有跨多行的 span")
    else:
        keep = multiline.layout_line_refs[:1]
        short_text = multiline.normalized_text[
            :max(1, len(multiline.normalized_text) // 2)]
        short_span = OutlineSpan.create(
            document_outline_locator=multiline.document_outline_locator,
            node_id=multiline.node_id, document_id=multiline.document_id,
            document_version=multiline.document_version,
            evidence_set_version=multiline.evidence_set_version,
            role=multiline.role, start_anchor=multiline.start_anchor,
            end_anchor=(keep[0][0], keep[0][1], multiline.start_anchor[2]),
            normalized_text=short_text, layout_line_refs=keep,
            component_evidence_refs=multiline.component_evidence_refs,
            alignment_ids=multiline.alignment_ids,
            is_fallback=multiline.is_fallback,
            is_cross_heading=multiline.is_cross_heading,
            confidence=multiline.confidence,
            span_builder_version=multiline.span_builder_version)
        _reject(_resnapshot(
            snap,
            spans=tuple(short_span if s is multiline else s for s in items),
            coverages=_rekey_coverages(coverages,
                                       {multiline.span_id: short_span})),
            handoff, snap, rebuilt, "spans", "spans 改一条（run 截短）")
    # 改一条②：node 错绑（span 与它的 coverage 身份同步重算）
    target = items[0]
    rebound = OutlineSpan.create(
        document_outline_locator=target.document_outline_locator,
        node_id=_other_node_id(handoff, target.node_id),
        document_id=target.document_id, document_version=target.document_version,
        evidence_set_version=target.evidence_set_version, role=target.role,
        start_anchor=target.start_anchor, end_anchor=target.end_anchor,
        normalized_text=target.normalized_text,
        layout_line_refs=target.layout_line_refs,
        component_evidence_refs=target.component_evidence_refs,
        alignment_ids=target.alignment_ids, is_fallback=target.is_fallback,
        fallback_derivation=target.fallback_derivation,
        is_cross_heading=target.is_cross_heading, confidence=target.confidence,
        char_range=target.char_range,
        span_builder_version=target.span_builder_version)
    _reject(_resnapshot(
        snap,
        spans=tuple(rebound if s is target else s for s in items),
        coverages=_rekey_coverages(coverages, {target.span_id: rebound})),
        handoff, snap, rebuilt, "spans", "spans 改一条（node 错绑）")


def _dr_components(handoff, snap, rebuilt):
    """spc-1：删一条 / 加一条 / 改一条（坐标改写、node 错绑）。"""
    items = list(snap.components)
    check(bool(items), "T20 真实夹具必须存在 provenance 组件才能构造该反例")
    if not items:
        return
    _reject(_resnapshot(snap, components=tuple(items[:-1])), handoff, snap,
            rebuilt, "components", "components 删一条")
    # 加一条：一条"偏移不可核验"的合成组件（落点真值表允许，且键不与既有冲突）
    block_id = items[0].evidence_block_id
    used = {(c.evidence_block_id, c.evidence_char_range, c.landing)
            for c in items}
    start = 0
    while (block_id, (start, start + 1), "alignment_offset_unverifiable") in used:
        start += 1
    extra = SS.SpanEvidenceComponent.create(
        evidence_block_id=block_id, terminal_kind="alignment",
        terminal_id="al-synthetic-not-real", verdict="aligned",
        evidence_char_range=(start, start + 1),
        landing="alignment_offset_unverifiable", admitted=False,
        admission_reason="offset_unverifiable")
    _reject(_resnapshot(
        snap,
        components=tuple(sorted(items + [extra], key=SB._component_sort_key))),
        handoff, snap, rebuilt, "components",
        "components 加一条（全部身份同步重算）")
    # 改一条①：坐标改写（区间右端 +1，投影不再落回原处）
    target = items[0]
    widened = SS.SpanEvidenceComponent.create(
        evidence_block_id=target.evidence_block_id,
        terminal_kind=target.terminal_kind, terminal_id=target.terminal_id,
        verdict=target.verdict,
        evidence_char_range=(target.evidence_char_range[0],
                             target.evidence_char_range[1] + 1),
        landing=target.landing, admitted=target.admitted,
        admission_reason=target.admission_reason,
        refusal_reason=target.refusal_reason, residue_class=target.residue_class,
        span_local_char_range=target.span_local_char_range,
        node_id=target.node_id, span_id=target.span_id,
        disposition_id=target.disposition_id, layout_hits=target.layout_hits)
    _reject(_resnapshot(
        snap,
        components=tuple(sorted((widened if c is target else c for c in items),
                                key=SB._component_sort_key))),
        handoff, snap, rebuilt, "components", "components 改一条（坐标改写）")
    # 改一条②：node 错绑
    rebound = SS.SpanEvidenceComponent.create(
        evidence_block_id=target.evidence_block_id,
        terminal_kind=target.terminal_kind, terminal_id=target.terminal_id,
        verdict=target.verdict, evidence_char_range=target.evidence_char_range,
        landing=target.landing, admitted=target.admitted,
        admission_reason=target.admission_reason,
        refusal_reason=target.refusal_reason, residue_class=target.residue_class,
        span_local_char_range=target.span_local_char_range,
        node_id=_other_node_id(handoff, target.node_id),
        span_id=target.span_id, disposition_id=target.disposition_id,
        layout_hits=target.layout_hits)
    _reject(_resnapshot(
        snap,
        components=tuple(sorted((rebound if c is target else c for c in items),
                                key=SB._component_sort_key))),
        handoff, snap, rebuilt, "components", "components 改一条（node 错绑）")


def _dr_coverages(handoff, snap, rebuilt):
    """spv-1：删一条 / 加一条 / 改一条（coverage 扩大 / 收紧）。"""
    coverages = list(snap.coverages)
    spans = list(snap.spans)
    check(bool(coverages), "T20 真实夹具必须存在 coverage 才能构造该反例")
    if not coverages:
        return
    # 删一条：只删 coverage 会被快照自身的集合闭合拒绝（这正是"快照自证"的边界）。
    victim = coverages[-1]
    raises(lambda: _resnapshot(snap, coverages=tuple(coverages[:-1])),
           SchemaValidationError, "coverages 的 span_id 集合必须恰好等于 spans 的",
           "T20 coverages 删一条（只删 coverage）已由快照自身的集合闭合拒绝")
    # 删一条（连同其 span，快照自证全绿，只能靠独立重建发现）
    _reject(_resnapshot(snap,
                        coverages=tuple(c for c in coverages if c is not victim),
                        spans=tuple(s for s in spans
                                    if s.span_id != victim.span_id)),
            handoff, snap, rebuilt, "coverages",
            "coverages 删一条（连同其 span）")
    # 加一条（连带 span，全部身份同步重算）
    node_id = spans[0].node_id or _real_node_ids(handoff)[0]
    span, coverage = _added_span_and_coverage(handoff, snap, node_id)
    _reject(_resnapshot(
        snap,
        spans=tuple(sorted(spans + [span],
                           key=lambda s: (s.start_anchor[0], s.start_anchor[1]))),
        coverages=tuple(sorted(coverages + [coverage], key=lambda c: c.span_id))),
        handoff, snap, rebuilt, "coverages",
        "coverages 加一条（连带 span，全部身份同步重算）")
    # 改一条①：coverage 扩大（把"仅归一化"的区域说成可引用来源）
    expanded = None
    for target in coverages:
        if target.normalization_only_intervals:
            promoted = target.normalization_only_intervals[0]
            expanded = SS.SpanCitableCoverage.create(
                span_id=target.span_id,
                span_local_length=target.span_local_length,
                normalization_only_intervals=[
                    iv for iv in target.normalization_only_intervals
                    if iv is not promoted],
                citable_source_intervals=(
                    list(target.citable_source_intervals)
                    + [(promoted.start, promoted.end)]),
                non_citable_source_intervals=target.non_citable_source_intervals,
                uncovered_source_intervals=target.uncovered_source_intervals,
                covering_component_ids=target.covering_component_ids)
            break
    if expanded is None:
        skip("T20 coverages 扩大：真实夹具里没有仅归一化区间，"
             "无法做扩大可引用区间的反例")
    else:
        _reject(_resnapshot(
            snap, coverages=tuple(expanded if c is target else c
                                  for c in coverages)),
            handoff, snap, rebuilt, "coverages",
            "coverages 改一条（coverage 扩大：仅归一化区间并入 citable）")
    # 改一条①b：coverage 扩大（把 uncovered 并进 citable；夹具无 uncovered 则跳过）
    grown = None
    for target in coverages:
        if target.uncovered_source_intervals:
            grown = SS.SpanCitableCoverage.create(
                span_id=target.span_id,
                span_local_length=target.span_local_length,
                normalization_only_intervals=target.normalization_only_intervals,
                citable_source_intervals=(
                    list(target.citable_source_intervals)
                    + list(target.uncovered_source_intervals)),
                non_citable_source_intervals=target.non_citable_source_intervals,
                uncovered_source_intervals=[],
                covering_component_ids=target.covering_component_ids)
            break
    if grown is None:
        skip("T20 coverages 扩大：真实夹具里没有 uncovered 区间，"
             "无法做未覆盖转可引用的扩大反例")
    else:
        _reject(_resnapshot(
            snap, coverages=tuple(grown if c is target else c
                                  for c in coverages)),
            handoff, snap, rebuilt, "coverages",
            "coverages 改一条（coverage 扩大：uncovered 并入 citable）")
    # 改一条②：可引用区间收紧（整段 citable 降级为 non_citable）
    shrunk = None
    for target in coverages:
        if target.citable_source_intervals:
            shrunk = SS.SpanCitableCoverage.create(
                span_id=target.span_id,
                span_local_length=target.span_local_length,
                normalization_only_intervals=target.normalization_only_intervals,
                citable_source_intervals=[],
                non_citable_source_intervals=(
                    list(target.non_citable_source_intervals)
                    + list(target.citable_source_intervals)),
                uncovered_source_intervals=target.uncovered_source_intervals,
                covering_component_ids=target.covering_component_ids)
            break
    if shrunk is None:
        skip("T20 coverages 收紧：真实夹具里没有可引用区间，无法做降级反例")
    else:
        _reject(_resnapshot(
            snap, coverages=tuple(shrunk if c is target else c
                                  for c in coverages)),
            handoff, snap, rebuilt, "coverages",
            "coverages 改一条（可引用区间收紧：citable 降级为 non_citable）")


def _dr_conservation(handoff, snap, rebuilt):
    """spr-1：删一条 / 加一条 / 改一条（自洽的层间折算）。"""
    conservation = snap.conservation
    rows = list(conservation.evidence_layer)
    check(bool(rows), "T20 真实夹具必须存在 Evidence 守恒行才能构造该反例")
    if not rows:
        return
    # 删一条
    dropped = SS.SpanConservation.create(
        document_layer=conservation.document_layer,
        body_layer=conservation.body_layer,
        evidence_layer=tuple(rows[:-1]), gaps=())
    check(dropped.conserved is True,
          "T20 conservation 删一条的自洽伪造必须能通过它自己的重算")
    _reject(_resnapshot(snap, conservation=dropped), handoff, snap, rebuilt,
            "conservation", "conservation 删一条（少一条 Evidence 守恒行）")
    # 加一条：一条与任何真实块无关、但自身三分法守恒的合成行
    extra = SS.EvidenceConservationRow(
        evidence_id="zz-synthetic-not-real", block_char_length=3,
        mapped_intervals=(SS.ClosedInterval(start=0, end=3),),
        unverifiable_intervals=(), residue_intervals=(),
        landing_counts=(SS.LandingCount(landing="body_span", segment_count=1,
                                        char_count=3),),
        conserved=True, problems=())
    added = SS.SpanConservation.create(
        document_layer=conservation.document_layer,
        body_layer=conservation.body_layer,
        evidence_layer=tuple(sorted(rows + [extra],
                                    key=lambda r: r.evidence_id)), gaps=())
    check(added.conserved is True,
          "T20 conservation 加一条的自洽伪造必须能通过它自己的重算")
    _reject(_resnapshot(snap, conservation=added), handoff, snap, rebuilt,
            "conservation", "conservation 加一条（一条合成的自洽 Evidence 行）")
    # 改一条：文档层 body 桶与正文层 regular 桶**同步**折算一行（合计处处成立）
    doc_layer = tuple(
        SS.LayerBucket(bucket=b.bucket, line_count=b.line_count + 1,
                       tight_char_count=b.tight_char_count + 1)
        if b.bucket == "body" else b
        for b in conservation.document_layer)
    body_layer = tuple(
        SS.LayerBucket(bucket=b.bucket, line_count=b.line_count + 1,
                       tight_char_count=b.tight_char_count + 1)
        if b.bucket == "regular" else b
        for b in conservation.body_layer)
    folded = SS.SpanConservation.create(
        document_layer=doc_layer, body_layer=body_layer,
        evidence_layer=conservation.evidence_layer, gaps=())
    check(folded.conserved is True,
          "T20 conservation 改一条的自洽伪造必须能通过它自己的重算")
    _reject(_resnapshot(snap, conservation=folded), handoff, snap, rebuilt,
            "conservation", "conservation 改一条（文档层与正文层同步折算一行）")


def _dr_synopses(handoff, snap, rebuilt):
    """ns-2：删一条 / 加一条 / 改一条（状态 / 原因码翻转）。"""
    items = list(snap.synopses)
    check(bool(items), "T20 真实夹具必须存在节点简介才能构造该反例")
    if not items:
        return
    _reject(_resnapshot(snap, synopses=tuple(items[:-1])), handoff, snap,
            rebuilt, "synopses", "synopses 删一条")
    # 加一条：一个不属于任何真实节点的简介
    extra = NavigationSynopsis.unavailable(node_id="zz-synthetic-not-in-outline",
                                           reason_code="no_span")
    _reject(_resnapshot(snap,
                        synopses=tuple(sorted(items + [extra],
                                              key=lambda n: n.node_id))),
            handoff, snap, rebuilt, "synopses",
            "synopses 加一条（一个不属于任何真实节点的简介）")
    # 改一条：原因码翻转
    target = items[0]
    cloned = NavigationSynopsis.unavailable(
        node_id=target.node_id,
        reason_code=("empty_text" if target.reason_code == "no_span"
                     else "no_span"),
        source_span_ids=target.source_span_ids)
    check(cloned.synopsis_id != target.synopsis_id,
          "T20 synopses 改一条必须改变 synopsis_id（身份随状态 / 原因码重算）")
    _reject(_resnapshot(snap,
                        synopses=tuple(cloned if n is target else n
                                       for n in items)),
            handoff, snap, rebuilt, "synopses",
            "synopses 改一条（原因码翻转）")


# ---------------------------------------------------------------------------
# 2. 反自证边界
# ---------------------------------------------------------------------------

def _anti_self_attestation(handoff, snap, rebuilt, verified):
    """① wrapper 不可伪造；② 结论不写回 raw；③ 只有受信 handoff 是来源；④ 域隔离。"""
    # ①a 公开构造器直接造一个 wrapper：字段全同也不取得资格。
    forged_wrapper = SV.VerifiedSpanSnapshot(
        snapshot=snap, handoff=handoff, scope=verified.issuer_scope,
        source_kind=verified.source_kind,
        issuer_version=verified.issuer_version,
        verification_fingerprint=verified.verification_fingerprint)
    check(forged_wrapper.identity() == verified.identity(),
          "T20 手写 wrapper 的身份字段必须与真品逐项相同（否则这条反例不成立）")
    raises(lambda: SV.issued_capability(forged_wrapper, "VerifiedSpanSnapshot"),
           SS.CapabilityError, "不是本进程由正式签发路径产生",
           "T20 手写的 VerifiedSpanSnapshot 不得取得正式资格")
    # ①b object.__new__ + 逐槽复制（含真实 wrapper 的全部字段）
    bare = object.__new__(SV.VerifiedSpanSnapshot)
    for slot, value in (("_snapshot", snap), ("_handoff", handoff),
                        ("_scope", verified.issuer_scope),
                        ("_source_kind", verified.source_kind),
                        ("_issuer_version", verified.issuer_version),
                        ("_verification_fingerprint",
                         verified.verification_fingerprint)):
        object.__setattr__(bare, slot, value)
    check(bare.identity() == verified.identity(),
          "T20 逐槽复制出的 wrapper 身份字典必须与真品一致（否则这条反例不成立）")
    raises(lambda: SV.issued_capability(bare, "VerifiedSpanSnapshot"),
           SS.CapabilityError, "不是本进程由正式签发路径产生",
           "T20 object.__new__ + 逐槽复制不得取得正式资格")
    span_id = snap.spans[0].span_id if snap.spans else "os-synthetic"
    raises(lambda: SV.is_completion_eligible(bare, span_id),
           SS.CapabilityError, "不是本进程由正式签发路径产生",
           "T20 仿造 wrapper 不得用于完成资格判定")
    # ①c copy / deepcopy / pickle / to_dict
    raises(lambda: copy.copy(verified), SV.SpanVerificationError, "不可 copy",
           "T20 VerifiedSpanSnapshot 不可 copy")
    raises(lambda: copy.deepcopy(verified), SV.SpanVerificationError,
           "不可 deepcopy", "T20 VerifiedSpanSnapshot 不可 deepcopy")
    raises(lambda: pickle.dumps(verified), SV.SpanVerificationError,
           "不可 pickle", "T20 VerifiedSpanSnapshot 不可 pickle")
    raises(lambda: verified.to_dict(), SV.SpanVerificationError, "不得序列化",
           "T20 VerifiedSpanSnapshot 不得序列化")

    # ② 复核结论只存在于 wrapper，不得写回 raw snapshot
    before_json = canonical_json(snap.to_dict())
    before_id = snap.snapshot_id
    before_fp = snap.content_fingerprint
    before_input = snap.trusted_input
    again = SV.verify_span_snapshot(snap, handoff)
    check(canonical_json(snap.to_dict()) == before_json,
          "T20 复核过程不得改动 raw snapshot 的任何字段（canonical JSON 逐字节相等）")
    check((snap.snapshot_id, snap.content_fingerprint, snap.trusted_input)
          == (before_id, before_fp, before_input),
          "T20 复核前后 raw snapshot 的身份与指纹必须逐字段不变")
    check(again.snapshot is snap,
          "T20 复核能力必须持有被复核的同一个 raw 对象，而不是它的副本")
    check(verified.verification_fingerprint not in before_json,
          "T20 复核指纹不得出现在 raw snapshot 的 canonical JSON 里")
    check("verification" not in before_json.lower(),
          "T20 raw snapshot 不得携带任何复核结论字段")
    check(set(snap.to_dict()) == set(rebuilt.to_dict()),
          "T20 raw snapshot 与独立重建结果的顶层键集必须一致")

    # ③ 只有受信 handoff 是来源
    raises(lambda: SV.verify_span_snapshot(
        snap, object.__new__(SB.VerifiedTS3Handoff)),
        SS.CapabilityError, "不是本进程由正式签发路径产生",
        "T20 object.__new__ 的 handoff 不得作为复核来源")
    auth = handoff.authority_fingerprints()
    forged_handoff = SB.VerifiedTS3Handoff(
        layout_capability=handoff.layout_capability,
        document_outline=handoff.document_outline,
        structure_snapshot=handoff.structure_snapshot,
        evidence_authority=handoff.evidence_authority,
        evidence_snapshot=handoff.evidence_snapshot,
        evidence_blocks=handoff.evidence_blocks, alignment=handoff.alignment,
        policy=handoff.qualification_policy, scope=handoff.issuer_scope,
        source_kind=handoff.source_kind, issuer_version=handoff.issuer_version,
        policy_provider_authority_fingerprint=(
            handoff.policy_provider_authority_fingerprint),
        structure_provider_authority_fingerprint=(
            auth["structure_provider_authority_fingerprint"]),
        evidence_gateway_authority_fingerprint=(
            auth["evidence_gateway_authority_fingerprint"]),
        terminal_provider_authority_fingerprint=(
            auth["terminal_provider_authority_fingerprint"]),
        handoff_identity=handoff.handoff_identity)
    check(forged_handoff.identity() == handoff.identity(),
          "T20 字段仿造的 handoff 身份必须与真品逐项相同（否则这条反例不成立）")
    raises(lambda: SV.verify_span_snapshot(snap, forged_handoff),
           SS.CapabilityError, "不是本进程由正式签发路径产生",
           "T20 字段仿造的 handoff 不得作为复核来源")
    raises(lambda: forged_handoff.to_dict(), SB.SpanBuildError, "不得序列化",
           "T20 VerifiedTS3Handoff 不得序列化")
    raises(lambda: copy.copy(handoff), SB.SpanBuildError, "不可 copy",
           "T20 VerifiedTS3Handoff 不可 copy")
    raises(lambda: pickle.dumps(handoff), SB.SpanBuildError, "不可 pickle",
           "T20 VerifiedTS3Handoff 不可 pickle")

    # ④ 签发域严格隔离：pinned 交接不得进入 live / testing 入口
    check(handoff.issuer_scope == "pinned_acceptance",
          f"T20 夹具交接必须签发在 pinned_acceptance 域，得到 {handoff.issuer_scope!r}")
    check(verified.issuer_scope == "pinned_acceptance",
          f"T20 复核能力必须继承交接签发域，得到 {verified.issuer_scope!r}")
    raises(lambda: SB.build_span_snapshot(handoff, stage="distribution_only"),
           SS.CapabilityError, "不在允许集合",
           "T20 pinned 交接传给需要 live 的公开 builder 必须拒绝")
    raises(lambda: SB._testing_build_span_snapshot(handoff,
                                                    stage="distribution_only"),
           SS.CapabilityError, "不在允许集合",
           "T20 pinned 交接传给 testing 域 builder 必须拒绝")
    check(rebuilt.snapshot_id == snap.snapshot_id,
          "T20 pinned 交接走 pinned 入口必须仍然可用且重建同一份快照")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> dict:
    # 本模块的基线是**TS4-A 版**产物（含 A 版交接）：进入 TS4-B 之后必须显式把阶段拨回
    # A（`evals.tree_stage_env`，退出即逐字还原），而不是绕过生产代码的阶段门。
    with STAGE.simulated_a_environment():
        handoff = _fixture_handoff()
        snap = SB._build_from_pinned_handoff(handoff, stage="distribution_only")
        rebuilt = SB._build_from_pinned_handoff(handoff, stage="distribution_only")
    verified = SV.verify_span_snapshot(snap, handoff)
    check(verified.snapshot.snapshot_id == snap.snapshot_id,
          "T20 正向基线必须先能通过复核（否则全部反例不成立）")

    structure = SV.verify_outline_structure_snapshot(handoff)
    check(structure["ok"] is True,
          f"T20 结构终态复核必须通过：{structure['problems'][:1]}")

    counts = {
        "dispositions": len(snap.dispositions), "spans": len(snap.spans),
        "components": len(snap.components), "coverages": len(snap.coverages),
        "evidence_rows": len(snap.conservation.evidence_layer),
        "synopses": len(snap.synopses),
        "outline_nodes": len(handoff.document_outline.nodes),
        "evidence_blocks": len(handoff.evidence_blocks),
    }
    _results["counts"] = counts
    for name, count in sorted(counts.items()):
        check(count > 0, f"T20 基线成员 {name} 必须非空（得到 {count}）")

    _dr_dispositions(handoff, snap, rebuilt)
    _dr_spans(handoff, snap, rebuilt)
    _dr_components(handoff, snap, rebuilt)
    _dr_coverages(handoff, snap, rebuilt)
    _dr_conservation(handoff, snap, rebuilt)
    _dr_synopses(handoff, snap, rebuilt)
    _anti_self_attestation(handoff, snap, rebuilt, verified)
    return _results


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["failed"] == 0 else 1)

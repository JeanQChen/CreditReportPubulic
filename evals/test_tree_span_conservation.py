# -*- coding: utf-8 -*-
"""TS4 三层守恒验收（计划 §18.13 的 T29 / T30 / T31 / T32）。

覆盖对象是**真实链路**：

    电子 PDF → PageLayout → DocumentOutline → OutlineStructureSnapshot
    → 对齐终态 → VerifiedTS3Handoff → SpanBuildSnapshot

因此本文件先记录 `data/evidence.db` 的 size / mtime_ns / sha256，跑完再断言三者未变
（库只读，绝不写入；PDF 同样只读）。

反例的写法只有一条硬规则：**裁决必须由生产规则自己给出**，不得由测试自己算一遍
"应该不相等"就宣布通过。所以每个反例都是"拿真实产物 → 只改一处（或注入一处伪造）
→ 交给生产侧判定函数 → 断言它抛错 / 报缺口"。造一个"字段完全自洽"的伪造品（把
所有 ID 与 hash 一起重算）也在反例之列：它骗得过快照自证，骗不过一次真正的独立重建。

三层口径（§18.9）：

- 文档层：`D_nonfurniture = H_heading ⊎ F_formal_unassigned ⊎ N_non_content ⊎ B_body`
- 正文层：`B_body = S_regular ⊎ A_adjacent_provisional ⊎ T_inside_table
  ⊎ U_ts4_unassigned ⊎ E_empty`
- Evidence 层：逐条 `[0, block_char_length) = mapped ⊎ offset_unverifiable
  ⊎ residue_unmapped`

三层口径各不相同，**不得互相替代**：文档层与正文层按行计数，Evidence 层按每条
Evidence 自己的字符域独立分区（跨 Evidence 禁止相加）。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evidence import store as estore  # noqa: E402

from evals import tree_stage_env as STAGE  # noqa: E402
from document_structure import outline_builder as OB  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_schema as SS  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure.aligner import align_evidence_set_verified  # noqa: E402
from document_structure.evidence_gateway import (  # noqa: E402
    bind_current_evidence_authority,
)
from document_structure.layout_builder import build_verified_page_layout  # noqa: E402

#: TS4-A P1-A 反例模块：复用它的**独立重算**原语（表格前导闭包的几何判据），使 T29 的
#: 表范围口径覆盖闭包，而不必再写第二套判定。
from evals import test_tree_span_table_adjacency as TA  # noqa: E402

DB_PATH = REPO / "data" / "evidence.db"
PDF_PATH = (REPO / "data" / "samples" / "300750" / "announcements"
            / "NDSD_2024_year.pdf")
COMPANY_ID = "300750"
DOCUMENT_ID = "NDSD_2024_year"

_results: dict = {"passed": 0, "failed": 0, "skipped": 0,
                  "details": [], "timings": {}}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def _one_disposition(run):
    """单条正文范围的处置记录（W1 后 `_build_non_regular_disposition` 返回**列表**）。

    本模块只在这里用单帧合成范围驱动该函数（`empty` / `unassigned`，都不参与按页
    分段），因此必须恰好一条；跨页 `table_inside` 的多条行为由
    `evals.test_tree_span_cross_page_table` 覆盖。出现多条即夹具前提失效，显式报错。
    """
    out = SB._build_non_regular_disposition(run)
    if len(out) != 1:
        raise AssertionError(
            f"单帧合成范围必须产生恰好一条处置记录，得到 {len(out)} 条"
            f"（{run[0].key}，kind={run[0].kind!r}）")
    return out[0]


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
# 0. 只读保证与合成事实的最小替身
# ---------------------------------------------------------------------------

def _db_stat() -> dict:
    data = DB_PATH.read_bytes()
    stat = DB_PATH.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(data).hexdigest()}


class _Stub:
    """合成事实用的最小替身：只提供生产函数真正读取的字段。

    生产代码读的是 `fact.line.text` 与 `fact.state.body_attachment`，因此这里只给这
    两个。用它构造的 `_LineFact` 只喂给 `_run_merge_key` / `_split_runs` /
    `_build_run_span` / `_build_non_regular_disposition` 这几个纯函数。
    """

    __slots__ = ("text", "body_attachment")

    def __init__(self, text: str = "", body_attachment: str | None = None) -> None:
        self.text = text
        self.body_attachment = body_attachment


def _mk_fact(key, kind, *, node_id=None, text="", attachment=None,
             table_scope=None, table_reason=None):
    stub = _Stub(text=text, body_attachment=attachment)
    return SB._LineFact(
        key=key, page=1, line=stub, state=stub, kind=kind, node_id=node_id,
        table_scope=table_scope, table_reason=table_reason)


def _bucket(layer, name):
    for item in layer:
        if item.bucket == name:
            return item
    raise AssertionError(f"层里没有桶 {name!r}")


def _shift(bucket, dlines: int, dchars: int):
    return SS.LayerBucket(bucket=bucket.bucket,
                          line_count=bucket.line_count + dlines,
                          tight_char_count=bucket.tight_char_count + dchars)


def _reforge(conservation, *, document_layer=None, body_layer=None):
    """用给定层（缺省用真实层）重新定型守恒对象（身份随之重算，故自身永远自洽）。"""
    return SS.SpanConservation.create(
        document_layer=(document_layer if document_layer is not None
                        else conservation.document_layer),
        body_layer=(body_layer if body_layer is not None
                    else conservation.body_layer),
        evidence_layer=conservation.evidence_layer, gaps=())


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


def _pick_line(facts, kind):
    """挑一条该类别的真实行，返回它的 tight 字符数（优先取非零字符的行）。"""
    fallback = None
    for fact in facts:
        if fact.kind != kind:
            continue
        count = SB.tight_char_count_of(fact.line.text)
        if fallback is None:
            fallback = count
        if count > 0:
            return count
    if fallback is None:
        raise AssertionError(f"真实数据里没有 {kind} 行，无法构造该反例")
    return fallback


def _forged_structure(handoff, *, drop_key=None, recolour_key=None):
    """由真实结构终态造一份"字段自洽"的伪造结构快照。"""
    states = []
    for state in handoff.structure_snapshot.line_states:
        key = (state.page_number, state.line_index)
        if drop_key is not None and key == drop_key:
            continue
        if recolour_key is not None and key == recolour_key:
            states.append(SS.LineStructureState(
                page_number=state.page_number, line_index=state.line_index,
                line_identity=state.line_identity, state="body_under_node",
                node_id=None, body_attachment="preceding_heading",
                reason_code=state.reason_code))
            continue
        states.append(state)
    bound = handoff.structure_snapshot
    return SS.OutlineStructureSnapshot.create(
        outline_algorithm_version=bound.outline_algorithm_version,
        heading_profile_version=bound.heading_profile_version,
        table_region_version=bound.table_region_version,
        toc_reconciliation_version=bound.toc_reconciliation_version,
        normalization_version=bound.normalization_version,
        document_id=bound.document_id, document_version=bound.document_version,
        page_layout_id=bound.page_layout_id,
        outline_locator=bound.outline_locator, outline_id=bound.outline_id,
        line_states=tuple(states))


def _closure(handoff, structure_snapshot):
    return SB._assert_upstream_closure(
        layout=handoff.layout_capability.layout,
        document_outline=handoff.document_outline,
        structure_snapshot=structure_snapshot,
        evidence_snapshot=handoff.evidence_snapshot,
        evidence_blocks=handoff.evidence_blocks,
        terminals=handoff.alignment.terminals)


# ---------------------------------------------------------------------------
# T29：三层正例
# ---------------------------------------------------------------------------

def _t29_layers(handoff, snap, facts):
    conservation = snap.conservation
    doc = conservation.document_layer
    body = conservation.body_layer
    structure = handoff.structure_snapshot

    # 1) 文档层四桶必须与结构终态的四态计数逐项相等（§18.9.1 的三方同一性）。
    state_to_bucket = {"heading_node": "heading",
                       "formal_unassigned": "formal_unassigned",
                       "non_content": "non_content",
                       "body_under_node": "body"}
    counts = {item.key: item.line_count for item in structure.counts}
    check(sorted(counts) == sorted(state_to_bucket),
          f"T29 结构终态计数键集必须恰为四态：{sorted(counts)}")
    for state_name, bucket_name in sorted(state_to_bucket.items()):
        bucket = _bucket(doc, bucket_name)
        check(bucket.line_count == counts.get(state_name),
              f"T29 文档层 {bucket_name} 行数 {bucket.line_count} 必须等于结构终态 "
              f"{state_name} 计数 {counts.get(state_name)}")

    # 2) 文档层合计 == 结构终态行数 == 真实版式非家具行数（D_nonfurniture 定义）。
    layout = handoff.layout_capability.layout
    real_lines = {(page.page_number, line.line_index)
                  for page in layout.pages for line in page.lines
                  if not line.is_furniture}
    doc_lines = sum(item.line_count for item in doc)
    check(doc_lines == len(structure.line_states),
          f"T29 文档层行数合计 {doc_lines} 必须等于结构终态行数 "
          f"{len(structure.line_states)}")
    check(doc_lines == len(real_lines),
          f"T29 文档层行数合计 {doc_lines} 必须等于真实版式非家具行数 "
          f"{len(real_lines)}")

    # 3) 正文层五桶合计必须恰为文档层 body 桶（生产侧重算规则见 `_gaps_recomputed`）。
    check(tuple(item.bucket for item in doc) == SS.DOCUMENT_LAYER_BUCKETS,
          f"T29 文档层桶必须按序恰为 {SS.DOCUMENT_LAYER_BUCKETS}")
    check(tuple(item.bucket for item in body) == SS.BODY_LAYER_BUCKETS,
          f"T29 正文层桶必须按序恰为 {SS.BODY_LAYER_BUCKETS}")
    doc_body = _bucket(doc, "body")
    check(sum(item.line_count for item in body) == doc_body.line_count,
          f"T29 正文层行数合计必须等于文档层 body 桶 {doc_body.line_count}")
    check(sum(item.tight_char_count for item in body) == doc_body.tight_char_count,
          f"T29 正文层字符合计必须等于文档层 body 桶 {doc_body.tight_char_count}")

    # 4) 正文层各桶必须等于按生产映射复算的处置分账（行与字符同时相等）。
    tally: dict = {name: [0, 0] for name in SS.BODY_LAYER_BUCKETS}
    for disposition in snap.dispositions:
        name = SB._BODY_BUCKET_BY_KIND[disposition.range_kind]
        tally[name][0] += disposition.line_count
        tally[name][1] += disposition.tight_char_count
    for name in SS.BODY_LAYER_BUCKETS:
        bucket = _bucket(body, name)
        check((bucket.line_count, bucket.tight_char_count) == tuple(tally[name]),
              f"T29 正文层桶 {name} 必须等于该类正文范围处置的合计："
              f"{(bucket.line_count, bucket.tight_char_count)} != {tuple(tally[name])}")

    # 5) 守恒对象自报 `conserved` 且无缺口（该字段由重算决定，不是自报结论）。
    check(conservation.conserved is True and conservation.gaps == (),
          f"T29 真实链路必须零缺口，得到 conserved={conservation.conserved} "
          f"gaps={[gap.reason_code for gap in conservation.gaps]}")

    # 6) span 与其 regular 处置必须一一对应，且行范围逐位一致。
    spans = {span.span_id: span for span in snap.spans}
    regular = {d.span_id: d for d in snap.dispositions if d.range_kind == "regular"}
    check(set(spans) == set(regular),
          f"T29 span 集合与 regular 处置集合必须相等：{len(spans)} vs {len(regular)}")
    bad_pair = []
    for span_id, span in sorted(spans.items()):
        disposition = regular.get(span_id)
        if disposition is None:
            bad_pair.append(span_id)
            continue
        refs = tuple(span.layout_line_refs)
        if ((disposition.start_page, disposition.start_line) != refs[0]
                or (disposition.end_page, disposition.end_line) != refs[-1]
                or disposition.line_count != len(refs)):
            bad_pair.append(span_id)
    check(not bad_pair,
          f"T29 每个 span 的行范围必须与其 regular 处置逐位一致，异常 {bad_pair[:3]}")
    check(all(span.component_evidence_refs is not None for span in snap.spans),
          "T29 每个 span 都必须带 component_evidence_refs")

    # 7) 表范围口径：逐行事实的表类别必须与**独立重算**一致。表格邻接行有两个真实
    #    来源：冻结 `trg-3` 版式判定的 `adjacent_to_table`，以及当前正文算法（`sb-7`）
    #    的**表格邻接闭包**（前导向上 + 尾部向下，改桶成 `table_adjacency` +
    #    `table_scope="none"` + 冻结原因码 `table_row_adjacent`）。后者不采信构建器自己的
    #    分类：由 `TA.verified_closure` 用几何与源序独立重算复核，声称吸收而不成立的行
    #    既会进 `over_absorbed`，也会落进下面的 drift。两个方向的行**签名相同**，因此
    #    用同一个并集做闭包行过滤。
    scopes = OB.table_region_scopes(layout)
    scope_of_kind = {SB._KIND_TABLE_INSIDE: "inside_table",
                     SB._KIND_TABLE_ADJACENT: "adjacent_to_table"}
    closure, over_absorbed = TA.verified_closure(facts)
    check(not over_absorbed,
          "T29 被吸收的表格邻接行必须经独立几何重算成立（不得吞掉跨页 / 跨节点 / "
          "栏不交叠 / 间距超阈值 / 夹着家具行 / 无已证明表格区域的行）："
          f"{over_absorbed[:3]}")
    drift = []
    for fact in facts:
        got = scopes.get(fact.key)
        frozen = None if got is None else got[0]
        if fact.key in closure:
            if frozen is not None or fact.table_scope != TA.CLOSURE_SCOPE \
                    or fact.table_reason != TA.CLOSURE_REASON:
                drift.append((fact.key, fact.kind, frozen, "闭包签名不符"))
            continue
        if fact.kind in scope_of_kind:
            if frozen != scope_of_kind[fact.kind]:
                drift.append((fact.key, fact.kind, frozen))
        elif fact.kind in (SB._KIND_REGULAR, SB._KIND_EMPTY):
            if frozen is not None:
                drift.append((fact.key, fact.kind, frozen))
    check(not drift,
          f"T29 表内/表邻接行与普通正文行必须与重算的表格范围一致，异常 {drift[:3]}")
    adjacent_facts = [fact for fact in facts
                      if fact.kind == SB._KIND_TABLE_ADJACENT]
    frozen_adjacent = sum(1 for entry in scopes.values()
                          if entry[0] == "adjacent_to_table")
    check(_bucket(body, "table_adjacency_provisional").line_count
          == len(adjacent_facts),
          "T29 表邻接 provisional 桶必须逐行等于逐行事实里的表格邻接行："
          f"{_bucket(body, 'table_adjacency_provisional').line_count} != "
          f"{len(adjacent_facts)}")
    check(len(adjacent_facts) <= frozen_adjacent + len(closure),
          "T29 表邻接行不得超过「冻结邻接范围 + 独立重算成立的表格闭包行」上限"
          f"（每行至多一段）：{len(adjacent_facts)} > {frozen_adjacent} + "
          f"{len(closure)}")


def _t29_evidence_layer(handoff, snap, text_by_id):
    rows = snap.conservation.evidence_layer
    check(len(rows) == len(handoff.evidence_blocks),
          f"T29 Evidence 层必须逐块一行：{len(rows)} != "
          f"{len(handoff.evidence_blocks)}")

    # 1) 每条 Evidence 的域长度必须等于按真实文本重算的 tight 长度。
    bad_len = [row.evidence_id for row in rows
               if row.block_char_length != len(SB.tight(text_by_id[row.evidence_id]))]
    check(not bad_len, f"T29 每条 Evidence 域长度必须等于真实 tight 长度，异常 "
                       f"{bad_len[:3]}")

    # 2) 三分法必须 tile 整个块域，且落点直方图合计 == 映射段 + 残差段字符数。
    tiling_bad, landing_bad = [], []
    for row in rows:
        problems = SS.partition_problems(
            (("mapped", row.mapped_intervals),
             ("unverifiable", row.unverifiable_intervals),
             ("residue", [(a, b) for (a, b, _c) in row.residue_intervals])),
            row.block_char_length)
        if problems:
            tiling_bad.append((row.evidence_id, problems[:1]))
        residue_chars = sum(b - a for (a, b, _c) in row.residue_intervals)
        if sum(lc.char_count for lc in row.landing_counts) != (
                SS.interval_length(row.mapped_intervals) + residue_chars):
            landing_bad.append(row.evidence_id)
    check(not tiling_bad, f"T29 三分法必须 tile 每块域，异常 {tiling_bad[:2]}")
    check(not landing_bad, f"T29 落点直方图合计必须等于映射+残差字符数，异常 "
                           f"{landing_bad[:3]}")

    # 3) 逐行复算：把该行自报的数字装回 `_BlockTally` 再走生产 `_conserve_block`，
    #    结果必须与真实行 canonical 全等（证明这行确实是生产规则算出来的）。
    block_by_id = {block.evidence_block_id: block for block in handoff.evidence_blocks}
    mismatched = []
    for row in rows:
        tally = SB._BlockTally()
        tally.mapped = list(row.mapped_intervals)
        tally.unverifiable = list(row.unverifiable_intervals)
        tally.residue = list(row.residue_intervals)
        for item in row.landing_counts:
            tally.landing_chars[item.landing] = item.char_count
            tally.landing_segments[item.landing] = item.segment_count
        rebuilt = SB._conserve_block(block_by_id[row.evidence_id], tally, text_by_id)
        if rebuilt.to_dict() != row.to_dict():
            mismatched.append(row.evidence_id)
    check(not mismatched,
          f"T29 每条 Evidence 行都必须能由生产 `_conserve_block` 复算复现，异常 "
          f"{mismatched[:3]}")

    # 4) 组件落点与 Evidence 行的落点直方图必须互相印证（同一批复算的两侧）。
    #    残差段每段各记一个组件、各记一次落点（`_build_snapshot` 里两者同源），因此
    #    这里必须一并统计，不能把残差排除在外。
    by_block_landing: dict = {}
    for component in snap.components:
        key = (component.evidence_block_id, component.landing)
        by_block_landing.setdefault(key, [0, 0])
        by_block_landing[key][0] += 1
        by_block_landing[key][1] += (component.evidence_char_range[1]
                                     - component.evidence_char_range[0])
    drift = []
    for row in rows:
        for item in row.landing_counts:
            got = by_block_landing.get((row.evidence_id, item.landing), [0, 0])
            if got != [item.segment_count, item.char_count]:
                drift.append((row.evidence_id, item.landing, got,
                              [item.segment_count, item.char_count]))
    check(not drift,
          f"T29 组件侧统计必须与 Evidence 行落点直方图逐项相等，异常 {drift[:2]}")


def _synthetic_component_on_line(facts, kind, run_of_key,
                                 disposition_id_by_run_page,
                                 unassigned_span_by_line, terminal):
    """在**真实版式行**上用一条合成 char_map 段走一遍生产定型，取该行的落点。

    段的字符区间由真实 LayoutSpan 的非空白前缀推出，因此 `_walk_segment` 的四步
    走查是真的在真实版式字节上成立的；只有"这条段来自哪次对齐"是合成的（本证据集
    里没有任何 char_map 段落在这类行上）。落点判定本身只看"段所在行的类别"，与段
    的来源无关。
    """
    fact = next((item for item in facts if item.kind == kind), None)
    if fact is None:
        return None
    line = fact.line
    if not line.spans:
        return None
    layout_span = line.spans[0]
    raw = layout_span.text
    offset = next((i for i, ch in enumerate(raw) if not ch.isspace()), None)
    if offset is None:
        return None
    width = sum(1 for ch in raw[offset:] if not ch.isspace())
    if width <= 0:
        return None
    segment = (0, width, fact.key[0], fact.key[1], 0, offset)
    line_by_key = {item.key: (item.page, item.line) for item in facts}
    try:
        hit, line_start, line_stop = SB._walk_segment(
            line_by_key, page_number=fact.key[0], line_index=fact.key[1],
            span_index=0, char_offset=offset, char_start=0, char_end=width,
            evidence_tight=SB.tight(line.text))
    except SB._WalkFailure:
        return None
    projection = SB._SegmentProjection(terminal, segment, hit, line_start,
                                       line_stop, None)
    return SB._build_component(
        inp=None, projection=projection, run_of_key=run_of_key, span_by_run={},
        disposition_id_by_run_page=disposition_id_by_run_page,
        unassigned_span_by_line=unassigned_span_by_line, tally=SB._BlockTally())


def _t29_positive_landings(handoff, snap, facts):
    """对四类"非正文"正例落点，重走一遍生产走查 + 组件定型，必须重现冻结的组件。"""
    line_by_key = {(page.page_number, line.line_index): (page, line)
                   for page in handoff.layout_capability.layout.pages
                   for line in page.lines}
    text_by_id = {block.evidence_block_id: block.text
                  for block in handoff.evidence_blocks}
    kind_by_key = {fact.key: fact.kind for fact in facts}
    runs = SB._split_runs(facts)
    run_of_key: dict = {}
    for run in runs:
        for fact in run:
            run_of_key[fact.key] = run
    dispositions = list(snap.dispositions)
    # W1 之后一条非 regular run 可能对应**多条**处置记录（跨页 `table_inside` 按页
    # 分段），故映射是「run → {页号: disposition_id}」。这里复用生产侧唯一的构造
    # 函数，而不是在本模块另写一套定位口径。
    disposition_id_by_run_page = SB._disposition_id_by_run_page(runs, dispositions)
    unassigned_span_by_line: dict = {}
    for span in handoff.document_outline.unassigned:
        for ref in span.layout_line_refs:
            unassigned_span_by_line[(ref[0], ref[1])] = span.span_id
    frozen = {}
    for component in snap.components:
        frozen.setdefault((component.evidence_block_id,
                           component.evidence_char_range), []).append(component)

    wanted = {"heading_node": "heading_node",
              "formal_unassigned": "formal_unassigned",
              "non_content": "non_content",
              "table_adjacency": "table_adjacency"}
    found: dict = {}
    for terminal in handoff.alignment.terminals:
        if len(found) == len(wanted):
            break
        evidence_tight = SB.tight(text_by_id[terminal.evidence_block_id])
        for segment in terminal.char_map:
            kind = kind_by_key.get((int(segment[2]), int(segment[3])))
            if kind not in wanted or kind in found:
                continue
            bs, be = int(segment[0]), int(segment[1])
            try:
                hit, line_start, line_stop = SB._walk_segment(
                    line_by_key, page_number=int(segment[2]),
                    line_index=int(segment[3]), span_index=int(segment[4]),
                    char_offset=int(segment[5]), char_start=bs, char_end=be,
                    evidence_tight=evidence_tight)
            except SB._WalkFailure:
                continue
            projection = SB._SegmentProjection(terminal, segment, hit,
                                               line_start, line_stop, None)
            component = SB._build_component(
                inp=None, projection=projection, run_of_key=run_of_key,
                span_by_run={},
                disposition_id_by_run_page=disposition_id_by_run_page,
                unassigned_span_by_line=unassigned_span_by_line,
                tally=SB._BlockTally())
            found[kind] = component
            break
    for kind, landing in sorted(wanted.items()):
        component = found.get(kind)
        if kind == "non_content" and component is None:
            # 本证据集里没有任何 char_map 段落在 non_content 行上（真实数据事实：这
            # 类行不属于可对齐内容）。因此改用**真实 non_content 行** + 一条与真实
            # 版式文本逐字走查成功的合成段，再走同一条生产定型路径取落点。
            component = _synthetic_component_on_line(
                facts, kind, run_of_key, disposition_id_by_run_page,
                unassigned_span_by_line, handoff.alignment.terminals[0])
        check(component is not None,
              f"T29 必须能在真实版式上复现 {kind} 行的段落点（正例落点 {landing}）")
        if component is None:
            continue
        check(component.landing == landing,
              f"T29 {kind} 行的段落点必须为 {landing!r}，得到 {component.landing!r}")
        same = [item for item in frozen.get(
            (component.evidence_block_id, component.evidence_char_range), [])
            if item.landing == landing]
        if kind == "non_content":
            check(not same,
                  "T29 non_content 落点在本证据集里未被任何 char_map 段触发（如实"
                  "记录，不得反过来断言不存在）")
            continue
        check(len(same) == 1 and same[0].component_id == component.component_id,
              f"T29 重走生产定型必须重现冻结组件（{kind} → {landing}）："
              f"{component.component_id[:12]}")


def _t29_verification(handoff, snap):
    structure = SV.verify_outline_structure_snapshot(handoff)
    check(structure["ok"] is True and structure["problem_count"] == 0,
          f"T29 结构终态复核必须通过：{structure['problems'][:1]}")
    start = time.time()
    verified = SV.verify_span_snapshot(snap, handoff)
    _results["timings"]["verify_span_snapshot"] = round(time.time() - start, 2)
    check(verified.issuer_scope == "live",
          f"T29 真实链路复核结果必须签发在 live 域，得到 {verified.issuer_scope!r}")
    check(verified.snapshot.snapshot_id == snap.snapshot_id,
          "T29 复核能力必须绑定被复核的那一份快照")
    check(len(verified.verification_fingerprint) == 64,
          "T29 复核指纹必须为 64 位十六进制")
    return verified


# ---------------------------------------------------------------------------
# T30：文档层负例（删标题行 / 折 formal_unassigned / 删 non_content 行 / 重复计桶）
# ---------------------------------------------------------------------------

def _t30_document_layer(handoff, snap, facts):
    doc = snap.conservation.document_layer
    body = snap.conservation.body_layer
    layout = handoff.layout_capability.layout
    real_lines = {(page.page_number, line.line_index)
                  for page in layout.pages for line in page.lines
                  if not line.is_furniture}

    # ① 删掉一个标题行：行集合闭合必须报出缺哪一个键。
    heading_key = next(f.key for f in facts if f.kind == SB._KIND_HEADING)
    forged = _forged_structure(handoff, drop_key=heading_key)
    check(len(forged.line_states) == len(real_lines) - 1,
          "T30① 伪造结构终态确实少了一条标题行")
    raises(lambda: _closure(handoff, forged), SB.SpanBuildError, "不闭合",
           f"T30① 删标题行必须被行集合闭合拒绝（缺 {heading_key}）")
    raises(lambda: _closure(handoff, forged), SB.SpanBuildError,
           str(heading_key), f"T30① 闭合报错必须点名缺的那一行 {heading_key}")

    # ③ 删掉一个 non_content 行：同一条闭合规则，换一类行。
    non_content_key = next(f.key for f in facts if f.kind == SB._KIND_NON_CONTENT)
    forged_nc = _forged_structure(handoff, drop_key=non_content_key)
    raises(lambda: _closure(handoff, forged_nc), SB.SpanBuildError, "不闭合",
           f"T30③ 删 non_content 行必须被行集合闭合拒绝（缺 {non_content_key}）")
    raises(lambda: _closure(handoff, forged_nc), SB.SpanBuildError,
           str(non_content_key), f"T30③ 闭合报错必须点名缺的那一行 {non_content_key}")

    # ② 把 formal_unassigned 折进 U（正文层 unassigned）：非自洽写法（只动文档层）
    #    必须被守恒对象的**重算**拒绝，而不是被自报字段拒绝。
    chars = _pick_line(facts, SB._KIND_FORMAL)
    folded_doc = tuple(
        _shift(item, -1, -chars) if item.bucket == "formal_unassigned"
        else (_shift(item, 1, chars) if item.bucket == "body" else item)
        for item in doc)
    raises(lambda: _reforge(snap.conservation, document_layer=folded_doc),
           SS.SchemaValidationError, "conserved 必须由重算决定",
           "T30② 把 formal_unassigned 折进 body（正文层未同步）必须被重算拒绝")

    # ②b 自洽写法：文档层与正文层一起改（formal 行同时进 body 与正文层 unassigned），
    #     守恒对象与快照的**自证**全部通过——只能由独立重建拆穿。
    forged_body = tuple(
        _shift(item, 1, chars) if item.bucket == "unassigned" else item
        for item in body)
    forged_conservation = _reforge(snap.conservation, document_layer=folded_doc,
                                   body_layer=forged_body)
    check(forged_conservation.conserved is True,
          "T30②b 自洽伪造守恒对象必须能通过它自己的重算（否则这条反例不成立）")
    forged_snap = _resnapshot(snap, conservation=forged_conservation)
    check(forged_snap.content_fingerprint != snap.content_fingerprint,
          "T30②b 伪造快照的内容指纹必须随之改变（字段自洽）")
    raises(lambda: SV.verify_span_snapshot(forged_snap, handoff),
           SV.SpanVerificationError, "独立重建结果与待复核快照不等",
           "T30②b 把 formal_unassigned 折进正文层：字段自洽的伪造仍必须被独立重建拒绝")
    check(SV._first_difference(snap.conservation.document_layer[1].to_dict(),
                               forged_conservation.document_layer[1].to_dict(),
                               "document_layer.formal_unassigned") is not None,
          "T30②b 差异定位必须能指出文档层 formal_unassigned 桶被改过")

    # ④ 一行正文同时计入两个正文层桶（regular 与 table_inside 各多记一行）。
    inside_chars = _pick_line(facts, SB._KIND_TABLE_INSIDE)
    double = tuple(
        _shift(item, 1, inside_chars)
        if item.bucket in ("regular", "table_inside") else item
        for item in body)
    raises(lambda: _reforge(snap.conservation, body_layer=double),
           SS.SchemaValidationError, "conserved 必须由重算决定",
           "T30④ 同一行正文计入两个桶必须被正文层合计重算拒绝")

    # ⑤（附加）闭合本身管不住"行集合不变、只改分桶"：改判一行不算漏行，必须由重建
    #    全等拦住。这是"删行"之外本层唯一的分桶攻击面。
    recolour_key = next(f.key for f in facts if f.kind == SB._KIND_FORMAL)
    forged_recolour = _forged_structure(handoff, recolour_key=recolour_key)
    try:
        _closure(handoff, forged_recolour)
        closed = True
    except Exception:  # noqa: BLE001
        closed = False
    check(closed, "T30⑤ 改判行（行集合不变）确实能骗过行集合闭合——这正是它需要"
                  "重建全等门的原因")
    check(SV._first_difference(handoff.structure_snapshot.to_dict(),
                               forged_recolour.to_dict(),
                               "OutlineStructureSnapshot") is not None,
          "T30⑤ 改判行后的结构终态必须与绑定值存在差异")
    raises(lambda: SB._require_canonical_equal(
        "OutlineStructureSnapshot", handoff.structure_snapshot.to_dict(),
        forged_recolour.to_dict()), SB.SpanBuildError, "canonical 不相等",
        "T30⑤ 改判行必须被 `_build_span_input` 的重建全等门拒绝")


# ---------------------------------------------------------------------------
# T31：正文层负例（少记 table_inside / 并 table_adjacency / 并空行 / unassigned 伪装）
# ---------------------------------------------------------------------------

def _t31_body_layer(handoff, snap, facts):
    body = snap.conservation.body_layer

    # ① 少记一行 table_inside：正文层合计不再等于文档层 body 桶，必须被重算拒绝。
    inside_chars = _pick_line(facts, SB._KIND_TABLE_INSIDE)
    short = tuple(
        _shift(item, -1, -inside_chars) if item.bucket == "table_inside" else item
        for item in body)
    raises(lambda: _reforge(snap.conservation, body_layer=short),
           SS.SchemaValidationError, "conserved 必须由重算决定",
           "T31① 少记一行 table_inside 必须被正文层合计重算拒绝")

    # ② 把 table_adjacency 并进 regular：行数与字符数都不变，故**合计**看不出问题；
    #    这种"自洽"的正文层表只能由独立重建拒绝。
    adjacent = _bucket(body, "table_adjacency_provisional")
    merged = tuple(
        _shift(item, adjacent.line_count, adjacent.tight_char_count)
        if item.bucket == "regular"
        else (_shift(item, -adjacent.line_count, -adjacent.tight_char_count)
              if item.bucket == "table_adjacency_provisional" else item)
        for item in body)
    forged_conservation = _reforge(snap.conservation, body_layer=merged)
    check(forged_conservation.conserved is True,
          "T31② 把 table_adjacency 并进 regular 的自洽伪造必须能通过自身重算")
    check(SV._first_difference(snap.conservation.body_layer[1].to_dict(),
                               forged_conservation.body_layer[1].to_dict(),
                               "body_layer.regular") is not None,
          "T31② 差异定位必须能指出正文层 regular 桶被并入了表邻接行")
    raises(lambda: SV.verify_span_snapshot(
        _resnapshot(snap, conservation=forged_conservation), handoff),
        SV.SpanVerificationError, "独立重建结果与待复核快照不等",
        "T31② 把 table_adjacency 并进 regular 必须被独立重建拒绝")

    # ②b 生产侧的切分规则本来就不允许二者同 run：合并键必须不同。
    table_key = ("table_scope-x", "table_reason-x")
    regular_fact = _mk_fact((1, 1), SB._KIND_REGULAR, node_id="on-x",
                            text="正文行")
    adjacent_fact = _mk_fact((1, 2), SB._KIND_TABLE_ADJACENT, node_id="on-x",
                             text="紧邻表格的正文行", table_scope=table_key[0],
                             table_reason=table_key[1])
    check(SB._run_merge_key(regular_fact) != SB._run_merge_key(adjacent_fact),
          "T31②b 正文行与表邻接行的合并键必须不同（否则会被切进同一个 run）")
    runs = SB._split_runs((regular_fact, adjacent_fact))
    check(len(runs) == 2 and len(runs[0]) == 1 and len(runs[1]) == 1,
          f"T31②b 表邻接行不得与普通正文行并入同一个 run，得到 "
          f"{[len(r) for r in runs]}")

    # ③ 空行不得并入相邻正文 run（真实文档没有空正文行，故用合成事实驱动同一批生产
    #    纯函数）。诚实的空行由 `_collect_line_facts` 判为 empty，合并键与正文行不同。
    empty_fact = _mk_fact((1, 2), SB._KIND_EMPTY, node_id="on-x", text="   ")
    runs = SB._split_runs((regular_fact, empty_fact,
                           _mk_fact((1, 3), SB._KIND_REGULAR, node_id="on-x",
                                    text="后续正文行")))
    check([len(run) for run in runs] == [1, 1, 1],
          f"T31③ 空行必须自成 range，得到 {[len(r) for r in runs]}")
    disposition = _one_disposition((empty_fact,))
    check(disposition.range_kind == "empty" and disposition.line_count == 1
          and disposition.tight_char_count == 0,
          f"T31③ 空行范围必须处置为 empty/1 行/0 字符，得到 "
          f"{disposition.range_kind}/{disposition.line_count}/"
          f"{disposition.tight_char_count}")
    check(SB._BODY_BUCKET_BY_KIND["empty"] == "empty",
          "T31③ 空行必须归入正文层 empty 桶")
    # ③b 若把空行伪装成正文行：合并键相同 → 会被并进正文 run（合计看不出问题，
    #     这是三层守恒的一个已知口径边界）；而它自己作为正文 span 也不成立。
    forged_regular_empty = _mk_fact((1, 2), SB._KIND_REGULAR, node_id="on-x",
                                    text="   ")
    check(len(SB._split_runs((regular_fact, forged_regular_empty))) == 1,
          "T31③b 空行被伪装成正文行后确实会被并进同一个 run（守恒口径不可见）")
    raises(lambda: SB._build_run_span(None, (forged_regular_empty,), [], (), {},
                                      None),
           SB.SpanBuildError, "regular run 的规范化文本不得为空",
           "T31③b 空行不得成为一个正文 span（正文 span 定型拒绝空文本）")

    # ④ 把一个 unassigned 范围伪装成 regular：regular run 必须有 node_id，故必被拒。
    unassigned_fact = _mk_fact((1, 4), SB._KIND_UNASSIGNED, node_id=None,
                               text="文档开头、无标题语境的正文行",
                               attachment="before_first_heading")
    raises(lambda: SB._build_run_span(None, (unassigned_fact,), [], (), {}, None),
           SB.SpanBuildError, "regular run 必须有 node_id",
           "T31④ 把 unassigned 范围伪装成 regular 必须被拒绝（node_id 空值真值表）")
    honest = _one_disposition((unassigned_fact,))
    check(honest.range_kind == "unassigned"
          and honest.unassigned_reason == "no_heading_context"
          and honest.node_id is None,
          f"T31④ 诚实的 unassigned 范围必须处置为 unassigned/"
          f"no_heading_context/node_id=None，得到 {honest.range_kind}/"
          f"{honest.unassigned_reason}/{honest.node_id}")
    real_unassigned = [d for d in snap.dispositions if d.range_kind == "unassigned"]
    check(real_unassigned and all(d.node_id is None for d in real_unassigned),
          f"T31④ 真实数据里所有 unassigned 处置的 node_id 必须为 None，"
          f"得到 {len(real_unassigned)} 条")


# ---------------------------------------------------------------------------
# T32：Evidence 层负例（跨 Evidence 累积 / 重复覆盖 / 未合并 / 走查失败 / 残差）
# ---------------------------------------------------------------------------

def _t32_evidence_layer(handoff, snap, text_by_id):
    blocks = handoff.evidence_blocks
    rows = {row.evidence_id: row for row in snap.conservation.evidence_layer}
    # 选一条真的带 body_span 组件的 Evidence，才能构造"重复覆盖"反例。
    body_span_blocks = {c.evidence_block_id for c in snap.components
                        if c.landing == "body_span"}
    block_a = next(b for b in blocks if b.evidence_block_id in body_span_blocks)
    block_b = next(b for b in blocks
                   if b.evidence_block_id != block_a.evidence_block_id)
    row_a = rows[block_a.evidence_block_id]
    length_a = row_a.block_char_length
    other_length = len(SB.tight(text_by_id[block_b.evidence_block_id]))

    # (a) 跨 Evidence 累积：把另一条 Evidence 的字符数当成"漏到本块"的区间塞进本块的
    #     分账。生产 `_conserve_block` 必须报"越出域"，而不是默默并进 mapped。
    tally = SB._BlockTally()
    tally.mapped = list(row_a.mapped_intervals)
    tally.unverifiable = list(row_a.unverifiable_intervals)
    tally.residue = list(row_a.residue_intervals)
    tally.mapped.append(SS.ClosedInterval(start=length_a - 1,
                                          end=length_a + other_length))
    for item in row_a.landing_counts:
        tally.landing_chars[item.landing] = item.char_count
        tally.landing_segments[item.landing] = item.segment_count
    raises(lambda: SB._conserve_block(block_a, tally, text_by_id),
           SB.SpanBuildError, "越出域",
           f"T32(a) 跨 Evidence 累积（{block_b.evidence_block_id[:8]} 的字符落到 "
           f"{block_a.evidence_block_id[:8]}）必须被划分检查拒绝")

    # (b) 重复覆盖掩盖：同一段被算了两次。合并会把重复区间吸收掉（划分仍然合法），
    #     因此必须由"落点直方图合计 == 映射段 + 残差段"这条不变式拦住。
    duplicate = SB._BlockTally()
    duplicate.mapped = list(row_a.mapped_intervals)
    duplicate.unverifiable = list(row_a.unverifiable_intervals)
    duplicate.residue = list(row_a.residue_intervals)
    dup_span = next((c for c in snap.components
                     if c.evidence_block_id == row_a.evidence_id
                     and c.landing == "body_span"), None)
    check(dup_span is not None,
          f"T32(b) 该 Evidence 必须有 body_span 组件才能构造重复覆盖反例："
          f"{block_a.evidence_block_id[:8]}")
    if dup_span is not None:
        dup_chars = (dup_span.evidence_char_range[1]
                     - dup_span.evidence_char_range[0])
        base = SS.interval_length(row_a.mapped_intervals)
        duplicate.landing_chars["body_span"] = base + dup_chars
        duplicate.landing_segments["body_span"] = 2
        raises(lambda: SB._conserve_block(block_a, duplicate, text_by_id),
               SB.SpanBuildError, "落点直方图",
               "T32(b) 重复覆盖（划分合法但落点字符被多算）必须被落点合计不变式拒绝")

    # (c) 未合并区间：相邻同类区间必须已合并（wire 侧由 `_need_intervals` 强制）。
    refs = [row for row in snap.conservation.evidence_layer
            if any(iv.length() >= 2 for iv in row.mapped_intervals)]
    check(bool(refs), "T32(c) 必须存在至少一段长度 >= 2 的映射区间用于拆分")
    if refs:
        row = refs[0]
        first = next(iv for iv in row.mapped_intervals if iv.length() >= 2)
        mid = first.start + 1
        payload = row.to_dict()
        # 必须**原地**拆成两半：把半段挪到列表末尾会先撞上"升序"检查，而不是撞上
        # 本反例要证明的那条"相邻同类区间必须已合并"。
        halves = []
        for iv in row.mapped_intervals:
            if iv is first:
                halves += [[first.start, mid], [mid, first.end]]
            else:
                halves.append([iv.start, iv.end])
        payload["mapped_intervals"] = halves
        raises(lambda: SS.EvidenceConservationRow.from_dict(payload),
               SS.SchemaValidationError, "相邻同类区间必须已合并",
               f"T32(c) 未合并的相邻映射区间必须被拒（{row.evidence_id[:8]}）")
        check(SS.EvidenceConservationRow.from_dict(row.to_dict()).to_dict()
              == row.to_dict(),
              f"T32(c) 真实行必须能 wire 往返（{row.evidence_id[:8]}）")
        payload["mapped_intervals"] = row.to_dict()["mapped_intervals"]
        check(SS.EvidenceConservationRow.from_dict(payload).to_dict()
              == row.to_dict(),
              "T32(c) 复原后的载荷仍必须与真实行相等（证明差异只来自未合并）")

    # (d) 走查失败的 char_map 段：必须落 `alignment_offset_unverifiable`，且**不得**
    #     伪造任何版式落点（layout_hits 为空、无 span 本地区间），并且不记落点直方图
    #     （否则 Evidence 行的落点合计会被多算一次）。
    terminal = handoff.alignment.terminals[0]
    segment = terminal.char_map[0]
    bs, be = int(segment[0]), int(segment[1])
    projection = SB._SegmentProjection(terminal, segment, None, None, None,
                                       "合成：走查失败")
    tally = SB._BlockTally()
    component = SB._build_component(
        inp=None, projection=projection, run_of_key={}, span_by_run={},
        disposition_id_by_run_page={}, unassigned_span_by_line={}, tally=tally)
    check(component.landing == "alignment_offset_unverifiable"
          and component.admission_reason == "offset_unverifiable"
          and component.admitted is False,
          f"T32(d) 走查失败的段必须落 offset_unverifiable 且不准入，得到 "
          f"{component.landing}/{component.admission_reason}/{component.admitted}")
    check(component.layout_hits == () and component.span_local_char_range is None,
          "T32(d) 走查失败的段不得伪造任何版式落点")
    check(tally.unverifiable == [(bs, be)] and tally.mapped == [],
          f"T32(d) 走查失败的段只能计入 unverifiable，得到 "
          f"unverifiable={tally.unverifiable} mapped={tally.mapped}")
    check(tally.landing_chars == {},
          f"T32(d) 不可核验段不得记入落点直方图，得到 {tally.landing_chars}")

    # (e) 残差段：必须落 `alignment_residue_unmapped`，residue_class 原样保留，
    #     同样不得伪造版式落点；真实数据里组件与所在行的残差三元组必须一致。
    residue_components = [c for c in snap.components
                          if c.landing == "alignment_residue_unmapped"]
    check(bool(residue_components),
          "T32(e) 真实数据必须存在残差组件（否则该反例不成立）")
    bad_forged = [c.component_id for c in residue_components
                  if c.layout_hits != () or c.span_local_char_range is not None
                  or c.admitted or c.span_id is not None
                  or c.disposition_id is not None]
    check(not bad_forged,
          f"T32(e) 残差段不得伪造版式落点 / span / 处置，异常 {bad_forged[:3]}")
    sample = residue_components[0]
    sample_row = rows[sample.evidence_block_id]
    triple = (sample.evidence_char_range[0], sample.evidence_char_range[1],
              sample.residue_class)
    check(triple in tuple(sample_row.residue_intervals),
          f"T32(e) 残差组件的 (start, end, class) 必须原样出现在所在行的残差区间里："
          f"{triple}")
    made = SB._build_residue_component(terminal, bs, be, "unexplained")
    check(made.landing == "alignment_residue_unmapped"
          and made.admission_reason == "residue_unmapped"
          and made.residue_class == "unexplained"
          and made.layout_hits == () and made.span_local_char_range is None,
          "T32(e) 生产残差组件构造器必须落 alignment_residue_unmapped 且不准入")
    classes = sorted({seg[2] for c in residue_components
                      for seg in [(c.evidence_char_range[0],
                                   c.evidence_char_range[1], c.residue_class)]})
    _results["timings"]["residue_classes"] = classes
    check(all(isinstance(cls, str) and cls != "" for cls in classes),
          f"T32(e) 残差类必须为非空字符串（词表由行构造约束），得到 {classes}")

    # (f) 正例落点不得被残差/不可核验口径污染：body_span 组件必须真实可定位。
    body_components = [c for c in snap.components if c.landing == "body_span"]
    check(bool(body_components),
          "T32(f) 真实数据必须存在 body_span 组件")
    offender = [c.component_id for c in body_components
                if c.span_id is None or not c.layout_hits]
    check(not offender,
          f"T32(f) 每个 body_span 组件都必须绑定 span_id 且至少一个真实落点，"
          f"异常 {offender[:3]}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> dict:
    before = _db_stat()
    start = time.time()
    estore._db_path = DB_PATH
    authority = bind_current_evidence_authority()
    vlayout = build_verified_page_layout(PDF_PATH, company_id=COMPANY_ID,
                                        document_id=DOCUMENT_ID)
    alignment = align_evidence_set_verified(vlayout, authority)
    # 本模块的基线是 **TS4-A 版**产物（含 A 版交接）：进入 TS4-B 之后必须显式把阶段拨回
    # A（`evals.tree_stage_env`，退出即逐字还原），而不是绕过生产代码的阶段门。
    with STAGE.simulated_a_environment():
        handoff = SB.issue_live_ts3_handoff(vlayout, alignment, authority)
        inp = SB._build_span_input(handoff, stage="distribution_only")
        snap = SB._build_snapshot(inp)
    _results["timings"]["build_span_snapshot"] = round(time.time() - start, 2)

    text_by_id = {block.evidence_block_id: block.text
                  for block in handoff.evidence_blocks}
    facts = SB._collect_line_facts(inp)

    _t29_layers(handoff, snap, facts)
    _t29_evidence_layer(handoff, snap, text_by_id)
    _t29_positive_landings(handoff, snap, facts)
    _t29_verification(handoff, snap)

    _t30_document_layer(handoff, snap, facts)
    _t31_body_layer(handoff, snap, facts)
    _t32_evidence_layer(handoff, snap, text_by_id)

    # 只读保证：真实链路不得改动证据库一个字节。
    after = _db_stat()
    check(before["size"] == after["size"],
          f"证据库大小不得变化：{before['size']} -> {after['size']}")
    check(before["mtime_ns"] == after["mtime_ns"],
          f"证据库 mtime_ns 不得变化：{before['mtime_ns']} -> {after['mtime_ns']}")
    check(before["sha256"] == after["sha256"],
          f"证据库 sha256 不得变化：{before['sha256'][:16]} -> "
          f"{after['sha256'][:16]}")
    _results["timings"]["total"] = round(time.time() - start, 2)
    _results["timings"]["db_before"] = before
    return _results


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["failed"] == 0 else 1)

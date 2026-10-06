# -*- coding: utf-8 -*-
"""TS4 §18.13 T9–T12：坐标核验（walk 公式 / 禁止原域误用 / 乱序 `char_map` / `L_s→L_l→S`）。

覆盖（全部为**纯离线**、无 LLM / 网络 / 数据库 / PDF 引擎 / 子进程）：

T9  §18.5.2 walk 公式
    自建 `PageLayout` + 真实 `TextAlignmentRecord`（公共 `create` 构造器）上的**逐段**
    四步走查：命中、走读、等值与行内定位全部成立；并给出三类走查失败的构造反例
    （`char_offset` 落在空白上 / `be-bs` 超出剩余非空白数 / 走读片段与 Evidence tight
    片段不等），断言它们被**显式**报告为 `offset_unverifiable`，不得被静默接受。

T10 §18.5.2「**禁止** `raw[char_offset : char_offset + (be - bs)]`」
    构造"紧邻非空白被空白隔开"的样本（原域切片与 tight 走读切片**必然不等**），
    证明走查产出的是 tight 走读结果而不是原域位移切片；并断言
    `tight(walked) == tight(evidence_text)[bs:be]` 与
    `line.text[line_start:line_stop] == walked`。

T11 §18.5.5 乱序 `char_map`
    两半：(a) 冻结 wire 语义不变 —— 合法（升序）的 `als-3` / `alr-2` 载荷经冻结
    `from_dict` 读回后 `char_map` 逐段**顺序与内容一字不变**（TS4 不排序、不改写旧
    载荷）；(b) 正式 TS4 输入边界经 `_is_non_ascending_char_map` **拒绝**乱序终态
    （不得排序后继续），且该谓词只看**存储序**，对同内容的升序排列必须放行。

T12 §18.5.3 `L_s → L_l → S` 投影
    同一行多个 `LayoutSpan`、目标 span 之前有正文、span 之间为空白，映射自目标 span
    **中部**开始并**中部**结束：投影必须恰为
    `s = off_i + len(canonical_text(raw_line[:line_start]))`，其中
    `off_i = Σ_{j<i}(len(canonical_text(l_j.text)) + 1)`；并给出"漏加
    `LayoutSpan.char_start`"的反例（其取值必然不同，且会取到同一行前一个 span 的文本）。

**不覆盖项**：T9 的 step4（行内定位）在本层**无法**被反例触发 —— `LayoutLine` 的构造期
不变量强制 `spans[i].text == line.text[char_start:char_end]`，因此只要 step1–3 成立，
`line.text[line_start:line_stop] == walked` 必然成立；本文件对全部正例断言该等式成立，
但**不**伪造一个"step4 失败"的样本。

本文件的断言只读取被测对象的公开行为（是否抛错、切片是否相等、投影取值），不读取
实现自报的任何"结论字段"作为通过依据。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from document_structure import schema as S
from document_structure import span_builder as SB
from document_structure import versions as V
from document_structure.aligner import (
    AlignmentTerminal,
    _is_non_ascending_char_map,
)
from document_structure.canonical import SchemaValidationError, canonical_text
from document_structure.normalization import tight

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


# ---------------------------------------------------------------------------
# 固定装置（不使用任何公司特例字段 / 固定业务页码或答案关键词）
# ---------------------------------------------------------------------------

_DOC_SHA = hashlib.sha256(
    "ts4-coords-fixture-pdf-bytes".encode("utf-8")).hexdigest()
_DOC_ID = "doc-ts4-coords"
_COMPANY_ID = "company-A"
_SET_VERSION = "set-0123456789ab"
_BLOCK_ID = "ev-block-coords"
_NODE_ID = "on-0000000000000001"
_X0 = 50.0
_LINE_H = 12.0
_STEP = 14.0


def _mk_line(line_index, text, cuts, y0):
    """一行真实版式：`cuts` 为 `(char_start, char_end)` 的排版片段。

    `LayoutLine` 的构造期不变量会逐条强制"`spans[i].text` 逐字符等于行内切片、
    spans 升序不重叠、空隙只允许空白"，因此本夹具无需自己重复这些约束。
    """
    spans = []
    for (a, b) in cuts:
        spans.append(S.LayoutSpan(
            text=text[a:b], font="SimSun", size=10.5, is_bold=False,
            bbox=(_X0 + a * 6.0, y0, _X0 + b * 6.0, y0 + _LINE_H),
            char_start=a, char_end=b))
    return S.LayoutLine(
        line_index=line_index, reading_order=line_index, column_index=0,
        bbox=(_X0, y0, _X0 + len(text) * 6.0, y0 + _LINE_H),
        spans=tuple(spans), text=text, is_furniture=False, furniture_kind=None)


def _mk_layout(lines):
    page = S.LayoutPage(
        page_number=1, width=600.0, height=800.0, rotation=0,
        lines=tuple(lines), has_text_layer=True)
    return S.PageLayout.create(
        document_id=_DOC_ID, company_id=_COMPANY_ID,
        document_version="sha256-" + _DOC_SHA[:16],
        source_file_sha256=_DOC_SHA, pages=(page,))


def _line_by_key(pl):
    return {(p.page_number, line.line_index): (p, line)
            for p in pl.pages for line in p.lines}


def _terminal_of(record):
    """把一条真实对齐记录投影成 TS4 正式边界看到的 `AlignmentTerminal`。

    逐字段对照 `aligner.alignment_terminal_views`：这里同样只做**派生**，不引入第二
    套事实来源；`AlignmentTerminal` 本身不校验 `char_map`，所以它同时也是"乱序终态
    抵达正式边界"的唯一可达形态。`als-3` 与 `alr-2` 两种终态的字段差异（`verdict`
    与 `refusal_reason` 的互斥、`coverage` 与 `exact_coverage` 的取值口径）与
    正式视图一致。
    """
    is_alignment = hasattr(record, "alignment_id")
    return AlignmentTerminal(
        page_number=record.page_number, block_index=record.block_index,
        evidence_block_id=record.evidence_block_id,
        evidence_set_version=record.evidence_set_version,
        terminal_kind=("alignment" if is_alignment else "refusal"),
        terminal_id=(record.alignment_id if is_alignment else record.refusal_id),
        terminal_locator=record.alignment_locator,
        terminal_schema_version=record.schema_version,
        aligner_version=record.aligner_version,
        partition_validator_version=V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        verdict=(record.verdict if is_alignment else None),
        refusal_reason=(None if is_alignment else record.refusal_reason),
        residue_class=record.residue_class,
        block_char_length=record.block_char_length,
        matched_chars=record.matched_chars,
        exact_coverage=(record.coverage if is_alignment
                        else record.exact_coverage),
        char_map=record.char_map, residue=record.residue)


def _terminal_with_char_map(record, char_map):
    """同一条记录、**仅**替换 `char_map` 的终态视图。

    `AlignmentTerminal` 是冻结 dataclass 且不做任何 `__post_init__` 校验，因此它是
    "乱序 `char_map` 抵达 TS4 正式边界"的唯一可达形态 —— 也是本文件用以直接检验
    `_is_non_ascending_char_map` 的真对象。
    """
    return AlignmentTerminal(
        page_number=record.page_number, block_index=record.block_index,
        evidence_block_id=record.evidence_block_id,
        evidence_set_version=record.evidence_set_version,
        terminal_kind="alignment", terminal_id=record.alignment_id,
        terminal_locator=record.alignment_locator,
        terminal_schema_version=record.schema_version,
        aligner_version=record.aligner_version,
        partition_validator_version=V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        verdict=record.verdict, refusal_reason=None,
        residue_class=record.residue_class,
        block_char_length=record.block_char_length,
        matched_chars=record.matched_chars, exact_coverage=record.coverage,
        char_map=char_map, residue=record.residue)


def _facts(pl, node_id, line_indexes):
    """一个 regular run 的逐行受信事实（与 `_collect_line_facts` 同形的真实类型）。"""
    by_key = _line_by_key(pl)
    out = []
    for line_index in line_indexes:
        page, line = by_key[(1, line_index)]
        out.append(SB._LineFact(
            key=(1, line_index), page=page, line=line, state=None,
            kind=SB._KIND_REGULAR, node_id=node_id, table_scope=None,
            table_reason=None))
    return tuple(out)


def _span_of(pl, node_id, normalized_text, line_refs):
    """一个真实 `OutlineSpan`（走查结果最终的归属单元，`_build_component` 只读其 id）。"""

    def _anchor(line_index):
        return (1, line_index,
                (_X0, 100.0 + line_index * _STEP, _X0 + 120.0,
                 100.0 + line_index * _STEP + _LINE_H))

    return S.OutlineSpan.create(
        document_outline_locator=S.derive_document_outline_locator(
            page_layout_id=pl.page_layout_id, document_id=pl.document_id,
            algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
            schema_version=V.OUTLINE_SCHEMA_VERSION),
        node_id=node_id, document_id=pl.document_id,
        document_version=pl.document_version, evidence_set_version=_SET_VERSION,
        role="body", start_anchor=_anchor(line_refs[0][1]),
        end_anchor=_anchor(line_refs[-1][1]), normalized_text=normalized_text,
        layout_line_refs=tuple(line_refs), confidence=1.0)


# ---------------------------------------------------------------------------
# walk 驱动：与 `_build_snapshot` 步骤 ①/②/④ 走同一条私有算法路径
# ---------------------------------------------------------------------------

def _walk_all(pl, record, evidence_text):
    """逐段执行 §18.5.2 的走查，返回 `_SegmentProjection` 序列（不吞异常）。"""
    line_by_key = _line_by_key(pl)
    evidence_tight = tight(evidence_text)
    terminal = _terminal_of(record)
    out = []
    for segment in record.char_map:
        bs, be = int(segment[0]), int(segment[1])
        try:
            hit, line_start, line_stop = SB._walk_segment(
                line_by_key, page_number=int(segment[2]), line_index=int(segment[3]),
                span_index=int(segment[4]), char_offset=int(segment[5]),
                char_start=bs, char_end=be, evidence_tight=evidence_tight)
        except SB._WalkFailure as failure:
            out.append(SB._SegmentProjection(
                terminal, segment, None, None, None, str(failure)))
        else:
            out.append(SB._SegmentProjection(
                terminal, segment, hit, line_start, line_stop, None))
    return out, line_by_key, evidence_tight


def _project_to_span(projections, run):
    """复刻步骤 ②：把命中段投影到 run 的 `S` 域（供组件构造使用）。"""
    projector = SB._RunProjector(run)
    keys = {f.key for f in run}
    for projection in projections:
        if projection.hit is None:
            continue
        key = (int(projection.segment[2]), int(projection.segment[3]))
        if key not in keys:
            continue
        try:
            projection.s_range = projector.to_span_local(
                key, projection.line_start, projection.line_stop)
        except SB._WalkFailure:
            projection.s_range = None
    return projector


def _component_of(projection, run=None, span_obj=None):
    """复刻步骤 ④（`_build_component` 不使用 `inp`，故此处显式传 `None`）。"""
    run_of_key = {} if run is None else {
        f.key: run for f in run}
    span_by_run = {} if (run is None or span_obj is None) else {id(run): span_obj}
    return SB._build_component(
        inp=None, projection=projection, run_of_key=run_of_key,
        span_by_run=span_by_run, disposition_id_by_run_page={},
        unassigned_span_by_line={}, tally=SB._BlockTally())


def _expected_walk(raw, char_offset, width):
    """测试侧独立复算的 §18.5.2 step2/step3（不调用被测实现）。"""
    seen = 0
    position = char_offset
    while position < len(raw) and seen < width:
        if not raw[position].isspace():
            seen += 1
        position += 1
    return seen, position


# ---------------------------------------------------------------------------
# T9：§18.5.2 walk 公式逐段成立 + 三类走查失败的显式报告
# ---------------------------------------------------------------------------

# 四行真实版式；每行按真实空白切成多个排版片段（`1,000` 的逗号不是空白）。
_T9_LINES = (
    ("第一节 公司概况", ((0, 3), (4, 8))),
    ("营业收入 1,000 万元", ((0, 4), (5, 10), (11, 13))),
    ("净利润 200 万元", ((0, 3), (4, 7), (8, 10))),
    ("同比增长 15%", ((0, 4), (5, 8))),
)
_T9_EVIDENCE_TEXT = "第一节 公司概况 营业收入 1,000 万元 净利润 200 万元 同比增长 15%"

#: `(bs, be, page, line, span_index, char_offset)`；逐段恰铺满 `[0, block_char_length)`。
#: 第 5 段自 `LayoutSpan` **中部**（`char_offset=3`）起读，覆盖 `1,000` 的尾部 `00`。
_T9_CHAR_MAP = (
    (0, 3, 1, 0, 0, 0),
    (3, 7, 1, 0, 1, 0),
    (7, 11, 1, 1, 0, 0),
    (11, 14, 1, 1, 1, 0),
    (14, 16, 1, 1, 1, 3),
    (16, 18, 1, 1, 2, 0),
    (18, 21, 1, 2, 0, 0),
    (21, 24, 1, 2, 1, 0),
    (24, 26, 1, 2, 2, 0),
    (26, 30, 1, 3, 0, 0),
    (30, 33, 1, 3, 1, 0),
)


def _t9_layout():
    lines = [_mk_line(i, text, cuts, 100.0 + i * _STEP)
             for i, (text, cuts) in enumerate(_T9_LINES)]
    return _mk_layout(lines)


def _t9_record(pl):
    return S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=len(tight(_T9_EVIDENCE_TEXT)), residue=(),
        char_map=_T9_CHAR_MAP)


def _test_t9_walk_formula():
    pl = _t9_layout()
    record = _t9_record(pl)
    evidence_text = _T9_EVIDENCE_TEXT
    evidence_tight = tight(evidence_text)
    check(len(evidence_tight) == record.block_char_length,
          "夹具：终态块长度必须等于真实 Evidence 文本的 tight 长度")
    check(record.verdict == "aligned" and record.is_citable() is True,
          "夹具：逐段铺满的 `als-3` 记录必须重算为 aligned 且可引证")

    projections, line_by_key, _t = _walk_all(pl, record, evidence_text)
    check(len(projections) == len(record.char_map),
          "每个 char_map 段都必须产出一条组件投影（不得静默丢弃）")
    failed = [p for p in projections if p.hit is None]
    check(not failed,
          f"真实夹具上全部段必须走查成功，得到失败段 {[p.segment for p in failed]}")

    run = _facts(pl, _NODE_ID, (0, 1, 2, 3))
    projector = _project_to_span(projections, run)
    span_obj = _span_of(pl, _NODE_ID, projector.normalized_text,
                        [(1, 0), (1, 1), (1, 2), (1, 3)])
    check(projector.normalized_text == canonical_text(
        " ".join(text for text, _cuts in _T9_LINES)),
        "run 的规范化重构必须等于逐行归一再以单空格相连")

    for projection in projections:
        segment = projection.segment
        bs, be, page_number = int(segment[0]), int(segment[1]), int(segment[2])
        line_index, span_index = int(segment[3]), int(segment[4])
        char_offset = int(segment[5])
        width = be - bs
        page, line = line_by_key[(page_number, line_index)]
        layout_span = line.spans[span_index]
        raw = layout_span.text

        # -- step1 命中 --------------------------------------------------
        check(0 <= char_offset < len(raw) and not raw[char_offset].isspace(),
              f"T9 step1：[{bs},{be}) 的 char_offset={char_offset} 必须命中非空白的"
              f"真实字符（{raw!r}）")
        # -- step2 走读（测试侧独立复算，不采信实现自报） ------------------
        seen, position = _expected_walk(raw, char_offset, width)
        check(seen == width,
              f"T9 step2：[{bs},{be}) 在本 span 内的非空白字符必须足量")
        span_stop = position
        walked = raw[char_offset:span_stop]
        check(projection.hit is not None
              and projection.hit.span_char_range == (char_offset, span_stop),
              f"T9：LayoutHit.span_char_range 必须为 L_s 域的 "
              f"({char_offset}, {span_stop})")
        # -- step3 等值 --------------------------------------------------
        check(tight(walked) == evidence_tight[bs:be],
              f"T9 step3：tight({walked!r}) 必须等于 Evidence tight 的 "
              f"[{bs},{be}) 片段")
        check(len(tight(walked)) == width,
              "T9 step3：等值必须建立在**恰好 width 个**非空白字符之上")
        # -- step4 行内定位 ----------------------------------------------
        line_start = layout_span.char_start + char_offset
        line_stop = layout_span.char_start + span_stop
        check(line.text[line_start:line_stop] == walked,
              f"T9 step4：line.text[{line_start}:{line_stop}] 必须逐字符等于走读片段")
        check(projection.hit is not None
              and projection.hit.line_char_range == (line_start, line_stop),
              "T9：LayoutHit.line_char_range 必须为 L_l 域的 (line_start, line_stop)")
        check(projection.hit is not None
              and projection.hit.layout_span_index == span_index
              and projection.hit.page_number == page.page_number
              and projection.hit.line_index == line.line_index,
              "T9：LayoutHit 必须指向真实 (页, 行, 片段)")

        # -- 落点：走查成功的段必须成为**准入**组件（§18.8.1） --------------
        component = _component_of(projection, run, span_obj)
        check(component.landing == "body_span"
              and component.admission_reason == "aligned_projected"
              and component.admitted is True,
              f"T9：[{bs},{be}) 走查成功且 verdict=aligned 时必须准入 body_span"
              f"（得到 {component.landing}/{component.admission_reason}）")
        check(component.span_id == span_obj.span_id
              and component.span_local_char_range == projection.s_range,
              "T9：准入组件必须绑定真实 span 并携带 `S` 域投影区间")
        check(len(component.layout_hits) == 1
              and component.layout_hits[0] == projection.hit,
              "T9：准入组件必须携带**逐段**走查落点（不多不少）")


def _t9_counterexamples():
    """§18.5.2：三类走查失败必须被显式报告为 `offset_unverifiable`。"""
    # 专用夹具：单个 span 内部含真实空白（"甲 乙丙 丁"），因此可以构造
    # "char_offset 落在空白上"与"剩余非空白不足"两种失败。
    text = "甲 乙丙 丁"
    pl = _mk_layout([_mk_line(0, text, ((0, len(text)),), 100.0)])
    line = pl.pages[0].lines[0]
    raw = line.spans[0].text
    check(raw == text and raw[1] == " " and raw[4] == " ",
          "反例夹具：span 内部必须含真实空白（用于制造 step1/step2 失败）")

    evidence_text = "甲乙丙丁"
    evidence_tight = tight(evidence_text)
    line_by_key = _line_by_key(pl)

    cases = (
        # (段, 期望失败步, 说明)
        ((0, 3, 1, 0, 0, 1), "step1", "char_offset=1 指向空白字符"),
        ((0, 3, 1, 0, 0, 4), "step1", "char_offset=4 指向空白字符"),
        ((0, 1, 1, 0, 0, len(raw)), "step1", "char_offset 越出本 span"),
        ((0, 3, 1, 0, 0, 3), "step2",
         "be-bs=3 超出'丙'之后剩余的非空白数 2"),
        ((0, 4, 1, 0, 0, 5), "step2", "be-bs=4 超出'丁'之后剩余的非空白数 1"),
        ((0, 2, 1, 0, 0, 3), "step3",
         "走读片段 '丙丁' 与 Evidence tight 的 [0,2)='甲乙' 不等"),
    )
    for segment, step, why in cases:
        bs, be = int(segment[0]), int(segment[1])
        record = S.TextAlignmentRecord.create(
            page_layout_id=pl.page_layout_id, evidence_set_version=_SET_VERSION,
            page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
            block_char_length=len(evidence_tight), residue=(),
            char_map=((0, len(evidence_tight), 1, 0, 0, 0),))
        # 走查本身必须先抛 `_WalkFailure`（不得返回一个"看起来成功"的落点）。
        raised = raises(
            lambda seg=segment: SB._walk_segment(
                line_by_key, page_number=int(seg[2]), line_index=int(seg[3]),
                span_index=int(seg[4]), char_offset=int(seg[5]),
                char_start=bs, char_end=be, evidence_tight=evidence_tight),
            SB._WalkFailure, step,
            f"T9 反例（{why}）必须抛 {step} 类的走查失败")
        if not raised:
            continue
        # 失败段仍必须生成**正式记录**，落点为 offset_unverifiable 且不得准入。
        projection = SB._SegmentProjection(
            _terminal_of(record), segment, None, None, None,
            f"走查失败：{step}")
        component = _component_of(projection)
        check(component.landing == "alignment_offset_unverifiable",
              f"T9 反例（{why}）落点必须为 alignment_offset_unverifiable，"
              f"得到 {component.landing!r}")
        check(component.admission_reason == "offset_unverifiable"
              and component.admitted is False,
              f"T9 反例（{why}）必须记为 offset_unverifiable 且 admitted=False")
        check(component.layout_hits == ()
              and component.span_local_char_range is None
              and component.span_id is None,
              f"T9 反例（{why}）不得冒充已有 Layout 落点或冒充 span-local 区间")


# ---------------------------------------------------------------------------
# T10：禁止把 tight 计数当原域位移（`raw[char_offset : char_offset+(be-bs)]`）
# ---------------------------------------------------------------------------

def _t10_fixture():
    """"紧邻非空白被空白隔开"的样本：tight 域相邻、原域中隔着一个空白。

    行文本 `"营业收入 与 净利润"`：tight 后为 8 个字符，而原域长度为 10。目标片段
    `[0,8)` 的原域位移切片是 `"营业收入 与 净"`（tight 后 6 字），与 tight 走读结果
    `"营业收入 与 净利润"` **必然不等**。
    """
    text = "营业收入 与 净利润"
    pl = _mk_layout([_mk_line(0, text, ((0, len(text)),), 100.0)])
    evidence_text = "营业收入与净利润"
    record = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=len(tight(evidence_text)), residue=(),
        char_map=((0, len(tight(evidence_text)), 1, 0, 0, 0),))
    return pl, record, evidence_text


def _test_t10_no_raw_domain_slice():
    pl, record, evidence_text = _t10_fixture()
    evidence_tight = tight(evidence_text)
    line = pl.pages[0].lines[0]
    raw = line.spans[0].text
    segment = record.char_map[0]
    bs, be = int(segment[0]), int(segment[1])
    char_offset = int(segment[5])

    check(len(raw) > len(evidence_tight),
          "T10 夹具：原域必须比 tight 域长（否则原域误用无法被区分）")
    check(raw.count(" ") >= 1 and " " not in evidence_tight,
          "T10 夹具：原域含空白、tight 域不含空白")

    projections, _by_key, _t = _walk_all(pl, record, evidence_text)
    check(len(projections) == 1 and projections[0].hit is not None,
          "T10 夹具：唯一段必须走查成功")
    hit = projections[0].hit
    walked = raw[hit.span_char_range[0]:hit.span_char_range[1]]
    line_start, line_stop = hit.line_char_range

    naive = raw[char_offset:char_offset + (be - bs)]
    check(walked != naive,
          f"T10：tight 走读结果 {walked!r} 必须**不等于**原域位移切片 {naive!r}")
    check(walked == raw,
          "T10：本样本的 tight 走读必须覆盖整个原域片段（空白被透明跨过）")
    check(len(walked) > be - bs,
          "T10：原域长度必须大于 `be-bs`（这正是原域位移会截断的原因）")
    check(tight(naive) != tight(walked),
          "T10：两种切片的 tight 结果必须可区分（否则反例无鉴别力）")

    # §18.5.2 明确要求的两条等式。
    check(tight(walked) == evidence_tight[bs:be],
          "T10：tight(walked) 必须等于 tight(evidence_text)[bs:be]")
    check(line.text[line_start:line_stop] == walked,
          "T10：line.text[line_start:line_stop] 必须逐字符等于 walked")
    check((line_start, line_stop) == (char_offset, char_offset + len(walked)),
          "T10：L_l 区间必须由**走读终点**给出，而不是 `char_offset + (be-bs)`")
    check(char_offset + (be - bs) != line_stop,
          "T10：原域位移的终点必须与真实走读终点不同（原域误用会被此断言抓住）")

    # 第二个样本：char_offset 落在片段中部，且其后仍有空白需要跨过。
    text2 = "前注甲 乙 丙丁戊"
    pl2 = _mk_layout([_mk_line(0, text2, ((0, len(text2)),), 100.0)])
    evidence2 = "前注甲乙丙丁戊"
    raw2 = pl2.pages[0].lines[0].spans[0].text
    evidence2_tight = tight(evidence2)
    check(len(raw2) == 9 and len(evidence2_tight) == 7 and raw2.count(" ") == 2,
          f"T10 样本二夹具：原域须为 9 字符且含两处空白，得到 {raw2!r}")
    record2 = S.TextAlignmentRecord.create(
        page_layout_id=pl2.page_layout_id, evidence_set_version=_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=len(evidence2_tight), residue=(),
        char_map=((0, len(evidence2_tight), 1, 0, 0, 0),))
    projections2, _b2, _t2 = _walk_all(pl2, record2, evidence2)
    hit2 = projections2[0].hit
    check(hit2 is not None, "T10 样本二：必须走查成功")
    walked2 = raw2[hit2.span_char_range[0]:hit2.span_char_range[1]]
    check((hit2.span_char_range[0], hit2.line_char_range) == (0, (0, len(raw2))),
          "T10 样本二：段自 span 起点开始，L_l 区间必须与 span 对齐")
    check(tight(walked2) == evidence2_tight,
          "T10 样本二：走读结果必须与 Evidence tight 全等")

    # 第三样本：中部起步且**其后仍有空白** —— 原域位移会在空白处提前截断。
    # 段为 tight `[3,7)` = `"乙丙丁戊"`，自原域偏移 4（`"乙"`）起走读。
    hit3, line_start3, line_stop3 = SB._walk_segment(
        _line_by_key(pl2), page_number=1, line_index=0, span_index=0,
        char_offset=4, char_start=3, char_end=7,
        evidence_tight=evidence2_tight)
    walked3 = raw2[4:hit3.span_char_range[1]]
    naive3 = raw2[4:4 + (7 - 3)]
    check(walked3 == "乙 丙丁戊" and len(walked3) == 5,
          f"T10 样本三：走读必须跨过中部空白，得到 {walked3!r}")
    check(walked3 != naive3 and len(naive3) == 4,
          f"T10 样本三：原域位移切片 {naive3!r} 必须在空白处提前截断")
    check(tight(walked3) == evidence2_tight[3:7],
          "T10 样本三：tight 走读片段必须等于 Evidence tight 的 [3,7)")
    check(line_start3 == 4 and line_stop3 == 4 + len(walked3),
          "T10 样本三：L_l 起点必须等于 span.char_start + char_offset")


# ---------------------------------------------------------------------------
# T11：乱序 `char_map` —— 冻结 wire 语义不变 + TS4 正式输入边界拒绝
# ---------------------------------------------------------------------------

#: `alr-2` 拒绝终态：精确 0.8998 < 0.90，而量化展示值恰为 0.9（量化边界张力）。
_REFUSAL_BLOCK_LEN = 10000
_REFUSAL_MATCHED = 8998


def _t11_ordered_record():
    pl = _t9_layout()
    return pl, _t9_record(pl)


def _t11_ordered_refusal():
    pl = _t9_layout()
    return S.AlignmentRefusalRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=_REFUSAL_BLOCK_LEN, matched_chars=_REFUSAL_MATCHED,
        residue=((_REFUSAL_MATCHED, _REFUSAL_BLOCK_LEN, "column_reorder"),),
        char_map=((0, _REFUSAL_MATCHED, 1, 0, 0, 0),),
        refusal_reason="quantization_boundary_refused")


def _test_t11_wire_semantics_unchanged():
    """半一：合法（升序）历史载荷经冻结 `from_dict` 读回，`char_map` 一字不变。"""
    _pl, record = _t11_ordered_record()
    payload = record.to_dict()
    back = S.TextAlignmentRecord.from_dict(payload)
    check(back.char_map == record.char_map,
          "T11(a)：`als-3` 载荷读回后 char_map 必须逐段逐字段等于原文（不得排序）")
    check(back.char_map == tuple(tuple(seg) for seg in payload["char_map"]),
          "T11(a)：读回的 char_map 顺序必须与 wire 载荷的**书写顺序**一致")
    check(back.to_dict() == payload,
          "T11(a)：`als-3` 往返必须逐键相等（wire 语义不变）")
    # 段序被交换后仍必须**降序可见**：证明 TS4 侧看到的是存储序，而不是排序后的副本。
    swapped = tuple(reversed(record.char_map))
    check(swapped != record.char_map,
          "T11(a) 夹具：交换段序后必须与原序不同（否则谓词无鉴别力）")
    check(_is_non_ascending_char_map(_terminal_of(record)) is False,
          "T11(b)：升序终态在 TS4 正式边界上必须被放行")
    check(_is_non_ascending_char_map(_terminal_of(back)) is False,
          "T11(b)：读回的升序终态同样必须被放行")

    refusal = _t11_ordered_refusal()
    r_payload = refusal.to_dict()
    r_back = S.AlignmentRefusalRecord.from_dict(r_payload)
    check(r_back.char_map == refusal.char_map and r_back.to_dict() == r_payload,
          "T11(a)：`alr-2` 拒绝终态往返必须逐键相等（wire 语义不变）")
    check(_is_non_ascending_char_map(_terminal_of(r_back)) is False,
          "T11(b)：`alr-2` 落地终态在 TS4 边界上必须被放行")


def _test_t11_out_of_order_rejected():
    """半二：正式 TS4 输入边界必须拒绝乱序终态，且**不得**排序后继续。"""
    _pl, record = _t11_ordered_record()
    ordered = record.char_map
    # 同内容、仅段序颠倒：`(0,3)`/`(3,7)` → `(3,7)`/`(0,3)`。
    out_of_order = (ordered[1], ordered[0]) + tuple(ordered[2:])
    check(out_of_order != ordered and sorted(out_of_order) == sorted(ordered),
          "T11 夹具：乱序载荷必须与升序载荷**段集合相同、存储序不同**")

    # (b1) 谓词本身：只看**存储序**，不看排序副本后的顺序。
    check(_is_non_ascending_char_map(
        _terminal_with_char_map(record, out_of_order)) is True,
          "T11(b)：乱序终态必须被判为非升序（TS4 必须拒绝，而不是排序后继续）")
    check(_is_non_ascending_char_map(
        _terminal_with_char_map(record, sorted(out_of_order))) is False,
          "T11(b)：同内容的**升序**排列必须放行（TS4 不排序，故谓词对存储序敏感）")
    # 形状非法的段（元素不足 2 个）同样不得被当成"升序"。
    check(_is_non_ascending_char_map(
        _terminal_with_char_map(record, ((0,),))) is True,
          "T11(b)：元素不足 2 个的畸形段必须被判为非升序（fail-closed）")
    check(_is_non_ascending_char_map(
        _terminal_with_char_map(record, ())) is False,
          "T11(b)：空 char_map 不含乱序证据，谓词不得凭空判负")

    # (a2) 冻结 `from_dict` 上，乱序载荷**不得**被静默排序或接受。
    #      实测当前实现：`als-3`/`alr-2` 的 `_need_char_map` 与 `__post_init__` 都在
    #      构造 / 反序列化期强制存储序升序，因此乱序载荷在**读回阶段**即被拒绝。
    #      这一事实与计划 §18.5.5「乱序对象允许按历史 schema 读回」**不一致**，
    #      已记录在交付说明中（本文件不修改任何生产代码）。
    payload = record.to_dict()
    payload["char_map"] = [list(seg) for seg in out_of_order]
    raises(lambda: S.TextAlignmentRecord.from_dict(dict(payload)),
           SchemaValidationError, "升序",
           "T11(b)：乱序 `als-3` 载荷必须被**显式**拒绝（不得静默排序后接受）")
    refusal_payload = _t11_ordered_refusal().to_dict()
    refusal_payload["char_map"] = [[5, _REFUSAL_MATCHED, 1, 0, 0, 0],
                                   [0, 5, 1, 0, 0, 5]]
    raises(lambda: S.AlignmentRefusalRecord.from_dict(dict(refusal_payload)),
           SchemaValidationError, "升序",
           "T11(b)：乱序 `alr-2` 载荷同样必须被显式拒绝（不得静默排序）")

    # (b2) 正式 TS4 边界接线：闭合核必须**逐终态**调用该谓词并 fail-closed 抛错。
    src = pathlib.Path(SB.__file__).read_text(encoding="utf-8")
    closure = src.split("def _assert_upstream_closure(", 1)[1].split(
        "\ndef ", 1)[0]
    check("_is_non_ascending_char_map(terminal)" in closure,
          "T11(b)：正式输入边界必须逐终态调用 `_is_non_ascending_char_map`")
    check("非升序" in closure and "SpanBuildError" in closure,
          "T11(b)：乱序终态必须以 SpanBuildError fail-closed（不得排序后继续）")


# ---------------------------------------------------------------------------
# T12：`L_s → L_l → S` 投影（必须加 `LayoutSpan.char_start`）
# ---------------------------------------------------------------------------

#: 目标行：`"营业收入情况 持续 增长备注结束"`。
#:  - span0 `(0,6)` = "营业收入情况"：同一行里**目标 span 之前**的正文；
#:  - span1 `(7,16)` = "持续 增长备注结束"：目标 span，自身含**内部空白**；
#:  - `raw[6]` 是 span 之间的空白；`raw[9]` 是目标 span 内部的空白。
#: 目标 span 内部的空白使"自目标 span 中部起读"的起点可以紧跟在**非空白**字符之后，
#: 从而 §18.5.3-3 的行内映射在本夹具的主段上是**无歧义**的（见本文件 T12 末段注释）。
_T12_TARGET_LINE = "营业收入情况 持续 增长备注结束"
_T12_TARGET_CUTS = ((0, 6), (7, 16))
_T12_PREV_LINE = "报告期内"
_T12_EVIDENCE_TEXT = "报告期内营业收入情况持续增长备注结束"

#: 逐段铺满 `[0,18)`。主段 `(13,15,1,1,1,4)` 自目标 span 中部（行内偏移 11）起读、
#: 到中部（行内偏移 13）结束，且起点紧跟在非空白之后，是 T12 核对 §18.5.3-3 与反例
#: 的依据。段 `(12,13,1,1,1,3)` 的起点紧跟空白，用于标注契约级歧义（见末段注释）。
_T12_CHAR_MAP = (
    (0, 4, 1, 0, 0, 0),
    (4, 10, 1, 1, 0, 0),
    (10, 12, 1, 1, 1, 0),
    (12, 13, 1, 1, 1, 3),
    (13, 15, 1, 1, 1, 4),
    (15, 18, 1, 1, 1, 6),
)


def _t12_fixture():
    lines = [_mk_line(0, _T12_PREV_LINE, ((0, 4),), 100.0),
             _mk_line(1, _T12_TARGET_LINE, _T12_TARGET_CUTS, 100.0 + _STEP)]
    pl = _mk_layout(lines)
    record = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=len(tight(_T12_EVIDENCE_TEXT)), residue=(),
        char_map=_T12_CHAR_MAP)
    return pl, _facts(pl, _NODE_ID, (0, 1)), record


def _test_t12_ls_to_l_s():
    pl, run, record = _t12_fixture()
    line = pl.pages[0].lines[1]
    target = line.spans[1]
    raw = line.text
    check(len(line.spans) == 2 and target.char_start > 0,
          "T12 夹具：目标 span 之前必须存在同一行的**前一个 span** 正文")
    check(raw[6] == " " and raw[9] == " ",
          "T12 夹具：span 之间与目标 span 内部都必须存在真实空白")
    check(canonical_text(raw) == raw,
          "T12 夹具：本行不含需要折叠的连续空白，便于逐位核对")
    check(record.verdict == "aligned",
          "T12 夹具：逐段铺满的 char_map 必须重算为 aligned（否则组件不会准入）")

    projector = SB._RunProjector(run)
    # 测试侧独立复算 `off_i = Σ_{j<i}(len(canonical_text(l_j.text)) + 1)`。
    offsets = {}
    offset = 0
    for fact in run:
        offsets[fact.key] = offset
        offset += len(canonical_text(fact.line.text)) + 1
    check(projector.offsets == offsets,
          f"T12：run 内每行的 `S` 域落点必须等于 §18.5.3-2 的公式，"
          f"得到 {projector.offsets} vs {offsets}")
    off = offsets[(1, 1)]
    check(off == len(canonical_text(_T12_PREV_LINE)) + 1 and off > 0,
          "T12：第二行的 off_i 必须 > 0（否则公式的 Σ 部分未被检验）")
    check(tight(projector.normalized_text) == tight(_T12_EVIDENCE_TEXT),
          "T12 夹具：run 的规范化重构必须与 Evidence tight 域在字符上一致")
    check(projector.normalized_text.count(" ") == 3
          and len(projector.normalized_text)
          == len(tight(_T12_EVIDENCE_TEXT)) + 3,
          "T12 夹具：`S` 域必须保留 1 个行连接空格 + 2 个行内单空格"
          "（`tight` 会抹掉它们）")

    # -- 主段：自目标 span 中部起读，且起点紧跟在**非空白**之后（无歧义） ----
    segment = _T12_CHAR_MAP[4]
    bs, be, char_offset = int(segment[0]), int(segment[1]), int(segment[5])
    line_start = target.char_start + char_offset
    span_stop = line_start + (be - bs)
    walked = raw[line_start:line_start + (be - bs)]
    check(char_offset > 0 and line_start + (be - bs) < target.char_end,
          f"T12：主段必须自目标 span **中部**开始并**中部**结束，得到行内 "
          f"[{line_start},{line_start + (be - bs)}) / span [{target.char_start},"
          f"{target.char_end})")
    check(not raw[line_start - 1].isspace(),
          "T12 夹具：主段起点必须紧跟非空白字符（使行内映射无歧义）")
    check(walked == "长备", f"T12：主段走读片段必须为 '长备'，得到 {walked!r}")

    s_start, s_stop = projector.to_span_local((1, 1), line_start, span_stop)
    expected_start = off + len(canonical_text(raw[:line_start]))
    check(s_start == expected_start,
          f"T12：`L_l → S` 起点必须恰为 off_i + len(canonical_text(line_raw[:line_start]))"
          f" = {expected_start}，得到 {s_start}")
    check(s_stop - s_start == len(canonical_text(walked)),
          "T12：投影区间长度必须等于走读片段的规范化长度")
    check(projector.normalized_text[s_start:s_stop] == canonical_text(walked),
          "T12：无歧义起点下，投影区间必须**逐字符**等于走读片段")

    # -- 端到端：真实 walk + 真实 span + 组件必须携带该 `S` 域区间 ----------
    line_by_key = _line_by_key(pl)
    evidence_tight = tight(_T12_EVIDENCE_TEXT)
    path = []
    for seg in _T12_CHAR_MAP:
        hit, hit_start, hit_stop = SB._walk_segment(
            line_by_key, page_number=int(seg[2]), line_index=int(seg[3]),
            span_index=int(seg[4]), char_offset=int(seg[5]),
            char_start=int(seg[0]), char_end=int(seg[1]),
            evidence_tight=evidence_tight)
        path.append((seg, hit, hit_start, hit_stop))
    check(all(item[1] is not None for item in path),
          "T12：逐段铺满的真实载荷必须全部走查成功")
    hit, hit_start, hit_stop = path[4][1], path[4][2], path[4][3]
    check((hit_start, hit_stop) == (line_start, span_stop),
          "T12：走查的 L_l 区间必须等于 span.char_start + (char_offset, span_stop)")
    span_obj = _span_of(pl, _NODE_ID, projector.normalized_text, [(1, 0), (1, 1)])
    components = []
    for seg, seg_hit, seg_start, seg_stop in path:
        projection = SB._SegmentProjection(
            _terminal_of(record), seg, seg_hit, seg_start, seg_stop, None)
        projection.s_range = projector.to_span_local(
            (int(seg[2]), int(seg[3])), seg_start, seg_stop)
        components.append(_component_of(projection, run, span_obj))
    check(all(c.admitted is True and c.landing == "body_span"
              for c in components),
          "T12：逐段铺满且 verdict=aligned 时全部段必须准入 body_span")
    check(components[4].span_local_char_range == (s_start, s_stop),
          f"T12：主段组件携带的 span 本地区间必须等于 ({s_start}, {s_stop})")
    check(len(components[4].layout_hits) == 1
          and components[4].layout_hits[0] == hit,
          "T12：主段组件必须携带该段自身的真实 Layout 落点")
    check(span_obj.normalized_text[s_start:s_stop] == canonical_text(walked),
          "T12：真实 span 的 normalized_text 在该区间上必须等于走读片段")

    # -- 反例：漏加 `LayoutSpan.char_start`（会误取同一行前一个 span 的文本） --
    seg0_char_offset = int(_T12_CHAR_MAP[4][5])
    check(seg0_char_offset == char_offset,
          "T12 反例：必须直接以主段的行内偏移构造反例（同一段、两种取值）")
    span0_range = projector.to_span_local((1, 1), 0, target.char_start)
    check(span0_range == (off, off + len(canonical_text(line.spans[0].text))),
          f"T12：前一个 span 在 `S` 域上的区间必须为 {span0_range}")
    omitted_start = off + len(canonical_text(raw[:seg0_char_offset]))
    omitted_stop = omitted_start + len(canonical_text(walked))
    check(omitted_start != s_start,
          f"T12 反例：漏加 LayoutSpan.char_start 的取值 {omitted_start} 必须与正确值 "
          f"{s_start} 不同")
    check(span0_range[0] <= omitted_start < span0_range[1],
          f"T12 反例：漏加 char_start 的起点 {omitted_start} 会落进前一个 span 的 "
          f"`S` 区间 {span0_range}（正是被禁止的错位）")
    picked = projector.normalized_text[omitted_start:omitted_stop]
    check(picked != canonical_text(walked),
          f"T12 反例：漏加 char_start 会取到 {picked!r}，而不是目标 span 的 "
          f"{walked!r}")
    check(picked == canonical_text(line.spans[0].text)[
              omitted_start - span0_range[0]:omitted_stop - span0_range[0]],
          f"T12 反例：取到的 {picked!r} 必须逐字符等于同一行**前一个 span** 的文本片段"
          f"（snippet 绝不能误取它）")

    # 当行内切点**紧跟空白**之后（例如"自 span 起点开始"，`raw[line_start-1]` 为空白）
    # 时，§18.5.3-3 的 `len(canonical_text(line_raw[:o]))` 会把前缀末尾的空白折叠掉，
    # 因此投影区间的**起点**落在该处由原始空白折叠而来的 `S` 单空格上。这**不是**错位：
    # §18.5.3-3 明确要求对任意 `0 <= o <= len(line_raw)` 都用同一闭式，**不得**添加
    # "前一字符必须为空白"这类伪条件；而 §18.5.3-5 只把 `S` 区间内的**非空白来源字符**
    # 计入 `citable_source_intervals`，所以边界空格既不进可引用覆盖，也不构成缺口。
    # 故此处只断言**公式本身**（两端各自的闭式），不断言逐字符切片相等。
    ambiguous_start, ambiguous_stop = projector.to_span_local(
        (1, 1), target.char_start, target.char_start + 2)
    check(ambiguous_start
          == off + len(canonical_text(raw[:target.char_start])),
          "T12：自 span 起点开始时仍必须使用加过 char_start 的行内坐标")
    check(ambiguous_stop
          == off + len(canonical_text(raw[:target.char_start + 2])),
          "T12：终点必须恰为 off_i + len(canonical_text(line_raw[:line_stop]))，"
          "不得写成 len(prefix) + len(segment)（那会丢掉最后一个真实字符）")
    # 两种切点下都必须成立的**内容**不变量：投影区间的非空白内容 == 走读片段。
    # 这条不变量与 `S`/`L_l` 之间"只差空白折叠"的语义等价，且不受边界空格归属影响。
    check(tight(projector.normalized_text[ambiguous_start:ambiguous_stop])
          == tight(raw[target.char_start:target.char_start + 2]),
          "T12：自 span 起点开始时投影区间的非空白内容仍必须等于走读片段")
    check(tight(projector.normalized_text[s_start:s_stop]) == tight(walked),
          "T12：无歧义起点下投影区间的非空白内容必须等于走读片段")
    # 反例：把终点写成 `len(prefix) + len(segment)`（本批修复前的实现）会让区间整体
    # 左移一位并**丢掉最后一个真实字符**，内容不变量必然被打破。
    buggy_stop = off + len(canonical_text(raw[:target.char_start])) \
        + len(canonical_text(raw[target.char_start:target.char_start + 2]))
    check(buggy_stop != ambiguous_stop
          and tight(projector.normalized_text[ambiguous_start:buggy_stop])
          != tight(raw[target.char_start:target.char_start + 2]),
          "T12 反例：终点写成 len(prefix)+len(segment) 会丢掉最后一个真实字符")
    check(ambiguous_start != off and ambiguous_start
          != off + len(canonical_text(raw[:0])),
          "T12 反例：省略 char_start 会退化为行首坐标（必然不同）")


# ---------------------------------------------------------------------------


def main() -> dict:
    _test_t9_walk_formula()
    _t9_counterexamples()
    _test_t10_no_raw_domain_slice()
    _test_t11_wire_semantics_unchanged()
    _test_t11_out_of_order_rejected()
    _test_t12_ls_to_l_s()
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

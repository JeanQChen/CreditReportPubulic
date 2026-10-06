"""TS1 对抗性反例：**先写反例，再据此修实现**（对应 TS1 修正轮 §四）。

本文件**刻意**构造上一轮可以构造成功的错误对象，逐条证明它们现在 fail-closed：

A. 不可变身份变异矩阵（P1-1）
   同一 `*_id` + 已改变的规范形必须被拒绝；只有内容变化的真值才换 id。

B. 版本与文档身份（P1-1.5 / P1-7）
   源文件哈希 ↔ document_version 不一致；schema 版本缺失 / 未知 / 旧格式；
   版本字段不是装饰品（必须进入身份）。

C. 版式内部文本与几何自洽（P1-2）
   行文本 `XYZ` 配 span 文本 `abc`；零长度字符区间；span 越界 / 重叠 / 逆序 /
   非空白空隙；零宽零高 bbox；NaN / ±Inf；越出页面的 bbox。

D. 标题树与引用图完整性（P1-3）
   节点顺序倒置；子节点未继承父路径；父不存在 / 层级错；重复 node_id；
   ordinal 错；child_ids 不对称；悬空 resolved edge；无对象集合的端点被标 resolved；
   未解析边缺 reason_code / 缺 occurrence / 编造目标 id；两处同源同目标引用塌缩。

E. 对齐不能自报成功（P1-4）
   coverage / verdict 与 char_map 重算不符；零长度块；覆盖率虚高；unexplained 残差；
   `ALIGN_MIN=None` 不得有旁路（同时证明阈值一旦裁决即可放行，gate 是真的）。

F. OutlineSpan 引用与闭合资格（P1-5）
   alignment 记录缺失 / 重复 / 多余 / 错绑；component Evidence ↔ 记录不一对一；
   零长度 char_range；低置信 / 跨标题 / fallback / unassigned 不得闭合集合。

G. TableObject provenance 与 cell_grid（P1-6）
   缺 component span / alignment；网格重叠、越界、空洞、单元格与行文本错绑、
   单元格 bbox 越出表格 bbox、单元格来源页不等于本表页；字符串集合不得再充当
   provenance；rowspan / colspan 的**正例**必须可构造；
   表体内容变化而不换 id；仅空白差异不得换 id；业务语义字段不得存在。

H. 通用规则与公司无关（§三）
   「公司无关」= 规则 / 算法不得随公司变化，**不是**"不同公司的存储身份必须相等"：
   不同 company_id 必须得到**不同**的存储身份（身份范围闭合到公司），
   同公司同内容必须逐字节确定性地重现；
   源码中不得出现固定页码 / 表号比较或按 company_id 字面量分支，
   且禁用词表必须定义在测试内而非生产代码提供。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from document_structure import schema as S
from document_structure import span_policy as sp
from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_text,
    is_finite,
)

from evals.test_tree_structure_schema import (
    _BLOCK_ID,
    _BLOCK_LEN,
    _DOC_ID,
    _DOC_SHA,
    _DOC_VERSION,
    _EVIDENCE_SET_VERSION,
    _H,
    _LINE_COUNT,
    _REF_SPAN_LINE,
    _REF_TEXT_1,
    _REF_TEXT_1_START,
    _REF_TEXT_2_START,
    _X0,
    _Y0,
    _anchor,
    _line_text,
    _make_alignment,
    _make_occurrence,
    _make_outline,
    _make_page_layout,
    _make_profile,
    _make_ref_span,
    _make_span,
    _make_synopsis,
    _make_table,
    _mk_line,
    _mk_page,
    _outline_locator,
    _read_module,
    _sha,
    _table_rows,
)

from document_structure.canonical import canonical_json

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def must_raise(fn, msg, substr=None):
    """必须抛 `SchemaValidationError`（可选检查消息片段）。"""
    try:
        fn()
    except SchemaValidationError as e:
        if substr is None or substr in str(e):
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{e}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 SchemaValidationError：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出异常（错误对象可构造）")
    return False


def must_equal(a, b, msg):
    return check(a == b, f"{msg}（{a!r} != {b!r}）" if a != b else msg)


# ---------------------------------------------------------------------------
# A. 不可变身份变异矩阵
# ---------------------------------------------------------------------------

def _change_body(table):
    """内部一致的"表体内容被改"：行文本与网格同步改，只有内容身份必须变。"""
    body = tuple(replace(r, cells=("营业收入", "101")) if r.row_index == 1 else r
                 for r in table.body_rows)
    grid = tuple(replace(c, text="101") if (c.row == 1 and c.column == 1) else c
                 for c in table.cell_grid)
    return replace(table, body_rows=body, cell_grid=grid)


def _mutation_matrix(pl, outline, span, table, al, syn, prof, edge):
    """(说明, 触发函数) —— 每条都必须是"同一 id + 另一种规范形"，必须失败。"""
    # 带 occurrence 的边（`parent_child` 允许省略 occurrence，位置变异只能在这条上做）。
    occ_edge = outline.edges[1]
    page = pl.pages[0]
    line = page.lines[0]
    empty_page = S.LayoutPage(page_number=1, width=600.0, height=800.0, rotation=0,
                              lines=(), has_text_layer=False)
    return (
        # -- PageLayout --
        ("PageLayout 页内容被改而 id 不变",
         lambda: replace(pl, pages=(empty_page,))),
        ("PageLayout 源文件哈希被改而 id 不变",
         lambda: replace(pl, source_file_sha256=_sha("other-pdf"))),
        ("PageLayout 文档版本被改而 id 不变",
         lambda: replace(pl, document_version="sha256-0000000000000000")),
        ("PageLayout 页数被改而 id 不变",
         lambda: replace(pl, page_count=2)),
        ("PageLayout 定位身份被换而 id 不变",
         lambda: replace(pl, page_layout_locator="loc-pl-0000000000000000")),
        # -- LayoutPage / LayoutLine / LayoutSpan（子结构） --
        # 子结构**不自带身份**（由父对象携带并校验），所以变异必须在容器内做：
        # 单独 `replace(page, ...)` 得到的是一个没有 id 的对象，不构成 P1-1 的
        # "同一 id 对应两种规范形"，只有放进 `PageLayout.pages` 才可判定。
        ("LayoutPage 增删行而父 id 不变",
         lambda: replace(pl, pages=(replace(page, lines=()),))),
        ("LayoutPage 页面尺寸被改而父 id 不变",
         lambda: replace(pl, pages=(replace(page, width=1.0),))),
        ("LayoutLine 文本被改而 span 不变",
         lambda: replace(pl, pages=(replace(page, lines=(
             replace(line, text=line.text + "改"),) + page.lines[1:]),))),
        ("LayoutLine 整行内容被换而父 id 不变",
         lambda: replace(pl, pages=(replace(page, lines=(
             _mk_line(0, "另一行文本", line.bbox[1],
                      [(0, 5, "SimSun", 10.5, False)]),) + page.lines[1:]),))),
        ("LayoutLine 行号被改而父 id 不变",
         lambda: replace(pl, pages=(replace(page, lines=(
             replace(line, line_index=5),) + page.lines[1:]),))),
        ("LayoutSpan 文本被改而字符区间不变",
         lambda: replace(pl, pages=(replace(page, lines=(
             replace(line, spans=(replace(line.spans[0], text="完全不同的文本"),)
                     + line.spans[1:]),) + page.lines[1:]),))),
        ("LayoutSpan 字体被改而父 id 不变",
         lambda: replace(pl, pages=(replace(page, lines=(
             replace(line, spans=(replace(line.spans[0], font="KaiTi"),)
                     + line.spans[1:]),) + page.lines[1:]),))),
        ("LayoutSpan 字号被改而父 id 不变",
         lambda: replace(pl, pages=(replace(page, lines=(
             replace(line, spans=(replace(line.spans[0], size=9.0),)
                     + line.spans[1:]),) + page.lines[1:]),))),
        # -- OutlineNode / DocumentOutline --
        ("OutlineNode 标题被改而 id 不变",
         lambda: replace(outline.nodes[0], title="被改的标题",
                         title_normalized="被改的标题",
                         structural_path=("被改的标题",))),
        ("OutlineNode 源位置被改而 id 不变",
         lambda: replace(outline.nodes[0], source_anchor=_anchor(3))),
        ("DocumentOutline 节点被删而 id 不变",
         lambda: replace(outline, nodes=outline.nodes[:1])),
        ("DocumentOutline 候选来源被改而指纹不变",
         lambda: replace(outline, candidate_sources=("toc_page",))),
        ("DocumentOutline 未归属 span 被清空而指纹不变",
         lambda: replace(outline, unassigned=())),
        ("DocumentOutline 内容指纹被篡改",
         lambda: replace(outline, content_fingerprint="0" * 64)),
        # -- SynopsisSnippet / NavigationSynopsis --
        ("NavigationSynopsis 片段文本被改而 id 不变",
         lambda: replace(syn, snippets=(replace(syn.snippets[0], text="改成别的"),))),
        ("NavigationSynopsis 状态被翻转而 id 不变",
         lambda: replace(syn, status="synopsis_unavailable")),
        ("SynopsisSnippet 字符区间被改",
         lambda: replace(syn.snippets[0], char_end=syn.snippets[0].char_end + 1)),
        # -- TextAlignmentRecord --
        ("TextAlignmentRecord 覆盖率被改而 id 不变",
         lambda: replace(al, coverage=0.5)),
        ("TextAlignmentRecord verdict 被改而 id 不变",
         lambda: replace(al, verdict="unaligned")),
        ("TextAlignmentRecord char_map 被改而 id 不变",
         lambda: replace(al, char_map=((0, 3, 1, 0, 0, 0),))),
        # -- OutlineSpan --
        ("OutlineSpan 正文被改而 id 不变",
         lambda: replace(span, normalized_text="完全不同的正文",
                         char_range=(0, len("完全不同的正文")))),
        ("OutlineSpan 置信度被改而 id 不变",
         lambda: replace(span, confidence=0.1)),
        ("OutlineSpan 角色被改而 id 不变",
         lambda: replace(span, role="list")),
        ("OutlineSpan 归属节点被改而 id 不变",
         lambda: replace(span, node_id="on-0000000000000000")),
        ("OutlineSpan 行引用被改而 id 不变",
         lambda: replace(span, layout_line_refs=((1, 2),))),
        # -- TableRow / TableCell / TableObject --
        ("TableObject 表体内容被改而 id 不变",
         lambda: _change_body(table)),
        ("TableObject 表题被改而 id 不变",
         lambda: replace(table, title="被改的表题")),
        ("TableObject 单位被改而 id 不变",
         lambda: replace(table, unit="元")),
        ("TableObject 结构分类被改而 id 不变",
         lambda: replace(table, structure_class="note_table")),
        # 单页物理片段模型下"来源范围"就是 `page_number`；变异必须**内部自洽**，
        # 否则先触发的是"单元格来源页必须等于本表页"，测不到身份重算。
        ("TableObject 来源页被改而 id 不变",
         lambda: replace(table, page_number=2, cell_grid=tuple(
             replace(c, source_locator=(2, c.row, 0)) for c in table.cell_grid))),
        ("TableObject 续表关系被改而 id 不变",
         lambda: replace(table, continuation_locators=("loc-to-0000000000000000",))),
        ("TableObject 续表来源被改而 id 不变",
         lambda: replace(table, continuation_of_locator="loc-to-0000000000000000")),
        ("TableObject 组件 span 被改而 id 不变",
         lambda: replace(table, component_span_ids=("os-0000000000000000",))),
        ("TableCell 文本被改而表格 id 不变",
         lambda: replace(table, cell_grid=(
             replace(table.cell_grid[0], text="别的"),) + table.cell_grid[1:])),
        ("TableRow 行类型被改而表格 id 不变",
         lambda: replace(table, body_rows=(
             replace(table.body_rows[0], kind="total", label="合计"),))),
        # -- AspectNavigationEntry / AspectNavigationProfile --
        ("AspectNavigationProfile 条目被改而 id 不变",
         lambda: replace(prof, entries=(replace(
             prof.entries[0], nav_keys=("别的键",)),))),
        ("AspectNavigationProfile Contract 版本被改而 id 不变",
         lambda: replace(prof, contract_version="v3")),
        # -- ReferenceEdge --
        ("ReferenceEdge 解析证据被改而 id 不变",
         lambda: replace(edge, resolution_evidence="另一条证据")),
        # 出现位置现在是一个**正式子结构**：只改它的位置（行号）而保留旧 locator/id，
        # 必须被"locator 与派生身份不一致"拒绝。
        ("ReferenceEdge 出现位置被改而 id 不变",
         lambda: replace(occ_edge, occurrence=replace(occ_edge.occurrence,
                                                       line_index=6))),
        ("ReferenceEdge 出现位置的字符区间被改而 id 不变",
         lambda: replace(occ_edge, occurrence=replace(
             occ_edge.occurrence, char_start=1,
             char_end=1 + len(occ_edge.occurrence.normalized_text)))),
    )


def _test_mutation_matrix():
    pl = _make_page_layout()
    outline = _make_outline(pl)
    span = _make_span(pl)
    table = _make_table(pl)
    al = _make_alignment(pl)
    syn = _make_synopsis()
    prof = _make_profile()
    edge = outline.edges[0]

    cases = _mutation_matrix(pl, outline, span, table, al, syn, prof, edge)
    check(len(cases) >= 40, f"变异矩阵样本过少：{len(cases)}")
    for label, fn in cases:
        must_raise(fn, f"变异矩阵：{label} 必须被拒绝")

    # 反向确证：矩阵确实改变了内容（否则"拒绝"可能只是重复同一个对象）。
    changed = _make_table(pl, rows=(_table_rows()[0],
                                    replace(_table_rows()[1], cells=("营业收入", "101")),
                                    _table_rows()[2]))
    check(changed.table_id != table.table_id,
          "表体内容变化必须改变 table_id")
    check(_make_span(pl, ).span_id == span.span_id, "同一输入必须得到同一 span_id")


# ---------------------------------------------------------------------------
# B. 版本与文档身份
# ---------------------------------------------------------------------------

def _test_document_identity_and_versions():
    pl = _make_page_layout()

    # 源文件哈希 ↔ document_version 必须符合现行文档身份规则。
    must_raise(lambda: S.PageLayout.create(
        document_id=_DOC_ID, document_version="sha256-0000000000000000",
        company_id="company-A", source_file_sha256=_DOC_SHA, pages=(_mk_page(),)),
        "document_version 与 source_file_sha256 不一致必须被拒绝",
        "document_version")
    must_raise(lambda: S.PageLayout.create(
        document_id=_DOC_ID, document_version="sha256-" + _sha("other-pdf")[:16],
        company_id="company-A", source_file_sha256=_DOC_SHA, pages=(_mk_page(),)),
        "换了源文件哈希却沿用旧 document_version 必须被拒绝",
        "document_version")

    # 与 evidence 层现行规则交叉核对（本层不得自造一套文档身份）。
    from evidence.ids import derive_document_version
    check(derive_document_version(_DOC_SHA) == _DOC_VERSION,
          "document_version 必须等于 evidence.ids.derive_document_version 的结果")

    # schema 版本缺失 / 未知 / 旧格式：每个版本化类型都必须逐一拒绝。
    fixtures = {
        "PageLayout": pl,
        "DocumentOutline": _make_outline(pl),
        "OutlineSpan": _make_span(pl),
        "TableObject": _make_table(pl),
        "NavigationSynopsis": _make_synopsis(),
        "TextAlignmentRecord": _make_alignment(pl),
        "AspectNavigationProfile": _make_profile(),
        "ReferenceEdge": _make_outline(pl).edges[0],
    }
    for typename, obj in fixtures.items():
        d = obj.to_dict()
        must_raise(lambda d=d, o=obj: type(o).from_dict(
            {k: v for k, v in d.items() if k != "schema_version"}),
            f"{typename} 缺 schema_version 必须被拒绝", "schema_version")
        must_raise(lambda d=d, o=obj: type(o).from_dict(
            {**d, "schema_version": "zz-999"}),
            f"{typename} 未知 schema 版本必须被拒绝", "未知版本")
        must_raise(lambda d=d, o=obj: type(o).from_dict(
            {**d, "schema_version": 2}),
            f"{typename} 非字符串 schema 版本必须被拒绝", "schema_version")

    legacy_pairs = (("PageLayout", "pl-1"), ("DocumentOutline", "do-1"),
                    ("OutlineSpan", "os-1"), ("AspectNavigationProfile", "anps-1"))
    for typename, legacy in legacy_pairs:
        obj = fixtures[typename]
        d = obj.to_dict()
        must_raise(lambda d=d, o=obj, k=legacy: type(o).from_dict(
            {**d, "schema_version": k}),
            f"{typename} 旧 wire format {legacy} 必须要求显式迁移",
            "旧 wire format")
    # `TableObject` 是唯一有**两个**读入面的类型，因此单独断言，且两个方向都要拦：
    #
    # - 历史 reader 只认它自己那一版 `to-3`；更早的 `to-1` / `to-2` 连它也不接受，
    #   必须指向当前实现的迁移路径；
    # - 当前 reader（`TableObjectV4`，`to-4`）反过来不得把 `to-3` 静默当成 `to-4`
    #   ——这一条在 `test_tree_structure_schema` 与 `test_tree_table_schema` 里各
    #   断言一次（那边有真正的 `to-4` 夹具），本模块不重复造第二个 `to-4` 夹具。
    legacy_table = fixtures["TableObject"]
    d = legacy_table.to_dict()
    try:
        _readback = type(legacy_table).from_dict(d)
        _ok = _readback == legacy_table
        _why = "读回对象与原对象不等"
    except Exception as _e:  # noqa: BLE001
        _ok, _why = False, f"{type(_e).__name__}: {_e}"
    check(_ok, "历史 reader 必须能读回它自己那一版 to-3"
               f"（否则历史资产无路可走）—— {_why}")
    for legacy in V.LEGACY_SCHEMA_VERSIONS["TABLE_SCHEMA_VERSION"]:
        if legacy == V.LEGACY_TABLE_SCHEMA_VERSION:
            continue
        must_raise(lambda d=d, o=legacy_table, k=legacy: type(o).from_dict(
            {**d, "schema_version": k}),
            f"TableObject 更早的 {legacy} 连历史 reader 都不接受，"
            f"必须指向当前实现的迁移",
            "必须由当前实现")

    # 版本字段不是装饰品：常量变化必然改变对应身份。
    check(S.derive_page_layout_locator(
        company_id=pl.company_id, document_id=pl.document_id,
        document_version=_DOC_VERSION, engine=V.LAYOUT_ENGINE,
        engine_version=V.LAYOUT_ENGINE_VERSION, schema_version="pl-999",
        normalization_version=V.NORMALIZATION_VERSION) != pl.page_layout_locator,
        "LAYOUT_SCHEMA_VERSION 必须真正进入身份")
    # 身份范围闭合：company_id / document_id / document_version / 引擎与归一化版本
    # 每一项都必须真正进入 locator（"版本字段不是装饰品"的统一检查）。
    base = dict(company_id=pl.company_id, document_id=pl.document_id,
                document_version=_DOC_VERSION, engine=V.LAYOUT_ENGINE,
                engine_version=V.LAYOUT_ENGINE_VERSION,
                schema_version=V.LAYOUT_SCHEMA_VERSION,
                normalization_version=V.NORMALIZATION_VERSION)
    check(S.derive_page_layout_locator(**base) == pl.page_layout_locator,
          "同一输入必须得到同一 page_layout_locator（确定性）")
    for field, bad in (("company_id", "company-B"), ("document_id", "doc-9999"),
                       ("document_version", "sha256-0000000000000000"),
                       ("engine_version", "pymupdf-2"),
                       ("normalization_version", "norm-2")):
        check(S.derive_page_layout_locator(**{**base, field: bad})
              != pl.page_layout_locator,
              f"{field} 必须真正进入 page_layout_locator（身份范围闭合）")
    for constant_name in V.SCHEMA_VERSION_CONSTANT_NAMES:
        value = V.VERSION_CONSTANTS[constant_name]
        check(V.classify_schema_version(constant_name, value) == "current",
              f"{constant_name} 的当前值必须被识别为 current")
    check(isinstance(V.ALIGN_MIN, float) and V.ALIGN_MIN == 0.90,
          "ALIGN_MIN 必须已冻结为单一文本对齐阈值 0.90（用户 + Codex 2026-09-17）")
    # A/B 真值表：阶段由 `SPAN_CONFIDENCE_MIN` 是否裁决派生，不写死当前阶段。
    gate = sp.ab_gate_truth_table()
    check(gate["stage"] == ("threshold_enabled"
                            if V.SPAN_CONFIDENCE_MIN is not None
                            else "distribution_only")
          and gate["completion_enabled"] is (V.SPAN_CONFIDENCE_MIN is not None),
          "A/B 真值表阶段与完成资格必须由 SPAN_CONFIDENCE_MIN 派生")
    if V.SPAN_CONFIDENCE_MIN is None:
        check(gate["completion_enabled"] is False
              and gate["set_complete_supported"] is False,
              "阈值未裁决（A 阶段）时完成资格与 set_complete 支持必须为假")
    check(V.classify_schema_version("ALIGNER_VERSION", V.ALIGNER_VERSION) == "current"
          and V.ALIGNER_VERSION not in V.legacy_versions("ALIGNER_VERSION"),
          f"对齐规则/输入信任边界已变，ALIGNER_VERSION 必须为当前权威版本"
          f"（得到 {V.ALIGNER_VERSION}，历史 {V.legacy_versions('ALIGNER_VERSION')}）")


# ---------------------------------------------------------------------------
# C. 版式内部文本与几何自洽
# ---------------------------------------------------------------------------

def _mk_line_raw(text, cuts, y0=_Y0, line_index=0, bbox=None):
    """直接构造（不做切片校验的旁路），用于注入不一致的 span。"""
    spans = []
    for (a, b, sptext) in cuts:
        spans.append(S.LayoutSpan(
            text=sptext, bbox=(_X0 + a * 6.0, y0, _X0 + b * 6.0, y0 + _H),
            font="SimSun", size=10.5, is_bold=False, char_start=a, char_end=b))
    return S.LayoutLine(
        line_index=line_index,
        bbox=bbox or (_X0, y0, _X0 + len(text) * 6.0, y0 + _H), spans=tuple(spans),
        text=text, is_furniture=False, furniture_kind=None, reading_order=line_index,
        column_index=0)


def _test_layout_self_consistency():
    # 行文本 `XYZ` 配 span 文本 `abc`（上一轮可直接构造成功的反例）。
    must_raise(lambda: _mk_line_raw("XYZ", [(0, 3, "abc")]),
               "行文本 XYZ 配 span 文本 abc 必须被拒绝", "逐字符等于")
    must_raise(lambda: _mk_line_raw("XYZ", [(0, 3, "XY")]),
               "span 长度与字符区间不符必须被拒绝")
    must_raise(lambda: _mk_line_raw("XYZ", [(0, 4, "XYZ ")]),
               "span 越出行的字符范围必须被拒绝", "越出")

    # 零长度字符区间 / 空 span 文本。
    must_raise(lambda: S.LayoutSpan(
        text="", bbox=(_X0, _Y0, _X0 + 6.0, _Y0 + _H), font="SimSun", size=10.5,
        is_bold=False, char_start=0, char_end=0),
        "空 span 文本必须被拒绝", "非空")
    span_d = _mk_line(0, "ABC", _Y0, [(0, 3, "SimSun", 10.5, False)]).spans[0].to_dict()
    must_raise(lambda: S.LayoutSpan.from_dict(
        {**span_d, "char_start": 1, "char_end": 1}),
        "零长度字符区间（from_dict 旁路）必须被拒绝", "零长度")
    must_raise(lambda: S.LayoutSpan.from_dict({**span_d, "char_end": 2}),
               "from_dict 平移字符区间使文本不再逐字符相等必须被拒绝")

    # span 重叠 / 逆序 / 非空白空隙。
    must_raise(lambda: _mk_line_raw("ABCD", [(0, 3, "ABC"), (2, 4, "CD")]),
               "span 重叠必须被拒绝", "不重叠")
    # 逆序时"首个 span 之前还有正文"必然先触发空隙规则；两条路径都 fail-closed，
    # 真正覆盖升序分支的是上面的重叠样例（`char_start < prev_end`）。
    must_raise(lambda: _mk_line_raw("ABCD", [(2, 4, "CD"), (0, 2, "AB")]),
               "span 逆序必须被拒绝", "空隙")
    must_raise(lambda: _mk_line_raw("AXXB", [(0, 1, "A"), (3, 4, "B")]),
               "span 之间非空白空隙必须被拒绝", "空隙")

    # 零宽 / 零高 bbox。
    for label, bbox in (("零宽", (_X0, _Y0, _X0, _Y0 + _H)),
                        ("零高", (_X0, _Y0, _X0 + 10.0, _Y0)),
                        ("负宽", (_X0 + 10.0, _Y0, _X0, _Y0 + _H))):
        must_raise(lambda b=bbox: S.LayoutSpan(
            text="A", bbox=b, font="SimSun", size=10.5, is_bold=False,
            char_start=0, char_end=1), f"{label} bbox 必须被拒绝", "bbox")

    # NaN / ±Inf 不得通过比较，也不得进入任何对象。
    for label, bad in (("NaN", float("nan")), ("+Inf", float("inf")),
                       ("-Inf", float("-inf"))):
        must_raise(lambda v=bad: S.LayoutSpan(
            text="A", bbox=(_X0, _Y0, _X0 + 6.0, _Y0 + _H), font="SimSun", size=v,
            is_bold=False, char_start=0, char_end=1),
            f"span size={label} 必须被拒绝")
        must_raise(lambda v=bad: S.LayoutSpan(
            text="A", bbox=(_X0, _Y0, v, _Y0 + _H), font="SimSun", size=10.5,
            is_bold=False, char_start=0, char_end=1),
            f"span bbox 含 {label} 必须被拒绝")
        must_raise(lambda v=bad: S.LayoutPage(
            page_number=1, width=v, height=800.0, rotation=0, lines=(),
            has_text_layer=False), f"页面宽 {label} 必须被拒绝")
        must_raise(lambda v=bad: S.compute_alignment_verdict(v, None, None),
                   f"coverage={label} 必须被拒绝", "有限实数")
        must_raise(lambda v=bad: S.compute_alignment_verdict(0.5, None, v),
                   f"align_min={label} 必须被拒绝", "有限实数")
        must_raise(lambda v=bad: S.quantize(v), f"quantize({label}) 必须被拒绝",
                   "有限实数")
        check(is_finite(bad) is False, f"is_finite({label}) 必须为 False")
    check(S.compute_alignment_verdict(0.5, None, None) == "partially_aligned",
          "正常 coverage 必须仍然给出确定性 verdict（反例不得靠全拒绝通过）")
    check(S.quantize(1.0004) == 1.0, "正常数值必须仍然可量化")
    check(is_finite(True) is False, "bool 不得被当成数值")
    check(is_finite("1.0") is False, "字符串不得被当成数值")
    check(is_finite(1.0) is True, "有限实数必须为 True")

    # 越出页面的 bbox（行与 span 各自）。
    wide = _mk_line_raw("很长的行" * 20, [(0, 80, "很长的行" * 20)])
    must_raise(lambda: S.LayoutPage(
        page_number=1, width=100.0, height=200.0, rotation=0, lines=(wide,),
        has_text_layer=True), "行 bbox 越出页面必须被拒绝", "越出页面范围")
    off_page = _mk_line_raw("AB", [(0, 2, "AB")], y0=-50.0,
                            bbox=(_X0, -50.0, _X0 + 12.0, -38.0))
    must_raise(lambda: S.LayoutPage(
        page_number=1, width=600.0, height=800.0, rotation=0, lines=(off_page,),
        has_text_layer=True), "负坐标 bbox 越出页面必须被拒绝", "越出页面范围")

    # 无文本层页面不得携带文本行（不得伪造）。
    must_raise(lambda: S.LayoutPage(
        page_number=1, width=600.0, height=800.0, rotation=0,
        lines=(_mk_line(0, _line_text(0), _Y0, [(0, len(_line_text(0)),
                                                 "SimSun", 10.5, False)]),),
        has_text_layer=False), "无文本层页面携带文本行必须被拒绝")
    must_raise(lambda: S.LayoutPage(
        page_number=1, width=600.0, height=800.0, rotation=45, lines=(),
        has_text_layer=False), "非法旋转角必须被拒绝")
    must_raise(lambda: S.LayoutPage(
        page_number=0, width=600.0, height=800.0, rotation=0, lines=(),
        has_text_layer=False), "页码 0 必须被拒绝")

    # 正常路径必须仍然可用（反例不得靠"全拒绝"通过）。
    ok = _mk_line(0, _line_text(0), _Y0,
                  [(0, 3, "SimSun", 10.5, False),
                   (3, len(_line_text(0)), "SimSun", 10.5, False)])
    check(ok.spans[0].text == _line_text(0)[:3], "正常多 span 行必须可构造")
    check(S.quantize(1.0004) == 1.0, "量化必须按 FLOAT_PRECISION 生效")


# ---------------------------------------------------------------------------
# D. 标题树与引用图完整性
# ---------------------------------------------------------------------------

def _outline_with(pl, nodes, edges=(), unassigned=None, sources=("body_numbering",),
                  ctx=None, **overrides):
    base = _make_outline(pl)
    kwargs = dict(
        document_id=pl.document_id, document_version=_DOC_VERSION,
        page_layout_id=pl.page_layout_id, nodes=nodes,
        edges=edges, unassigned=base.unassigned if unassigned is None else unassigned,
        candidate_sources=tuple(sources), reference_context=ctx)
    kwargs.update(overrides)
    return S.DocumentOutline.create(**kwargs)


def _node_occ(node_id, **kw):
    """绑定 `node:` 来源的**声明用** occurrence（来源核验由 outline 层执行）。

    TS1.4 起未解析边的 occurrence 也必须在真实 `LayoutLine` 上核验，但那是
    `DocumentOutline` 的入口；`ReferenceEdge.create` 仍只要求
    `occurrence.source_ref == from_ref`。因此本节这些夹具只用来验证**边层声明**规则。
    """
    kw.setdefault("source_ref", f"node:{node_id}")
    return _make_occurrence(node_id, **kw)


def _test_outline_graph():
    pl = _make_page_layout()
    outline = _make_outline(pl)
    a, a1, b = outline.nodes
    oloc = _outline_locator(pl)

    # 节点源位置必须按文档顺序严格递增。
    must_raise(lambda: _outline_with(pl, (a1, a)),
               "节点顺序倒置必须被拒绝", "严格递增")
    must_raise(lambda: _outline_with(pl, (a, b, a1)),
               "源位置回退必须被拒绝", "严格递增")
    same_anchor = replace(
        a1, source_anchor=a.source_anchor,
        node_id=S.derive_node_id(document_outline_locator=oloc,
                                 structural_path=a1.structural_path,
                                 source_anchor=a.source_anchor, title=a1.title))
    must_raise(lambda: _outline_with(pl, (a, same_anchor)),
               "两节点同一源位置必须被拒绝", "严格递增")

    # 子节点必须继承父路径。
    bad_path = replace(
        a1, structural_path=("别的父标题", "第一节第一小节"),
        node_id=S.derive_node_id(document_outline_locator=oloc,
                                 structural_path=("别的父标题", "第一节第一小节"),
                                 source_anchor=a1.source_anchor, title=a1.title))
    must_raise(lambda: _outline_with(pl, (a, bad_path)),
               "子节点未继承父路径必须被拒绝", "继承父节点路径")

    # 父必须存在、层级必须连续。
    orphan = replace(
        a1, parent_id="on-0000000000000000",
        node_id=S.derive_node_id(document_outline_locator=oloc,
                                 structural_path=a1.structural_path,
                                 source_anchor=a1.source_anchor, title=a1.title))
    must_raise(lambda: _outline_with(pl, (a, orphan)),
               "父节点不存在必须被拒绝", "不存在于 nodes")
    # 层级错必须用"自洽的 3 段路径 + level=2"来构造，否则先触发节点自身的
    # `level == len(structural_path)-1`，测不到"必须比父节点深 1"这条。
    deep_path = ("第一节标题", a1.title, a1.title)
    wrong_level = replace(
        a1, level=2, structural_path=deep_path,
        node_id=S.derive_node_id(document_outline_locator=oloc,
                                 structural_path=deep_path,
                                 source_anchor=a1.source_anchor, title=a1.title))
    must_raise(lambda: _outline_with(pl, (a, wrong_level)),
               "层级与父节点不连续必须被拒绝", "深 1")

    # 重复 node_id、ordinal 错、child_ids 不对称。
    must_raise(lambda: _outline_with(pl, (a, a)),
               "重复 node_id 必须被拒绝", "唯一")
    must_raise(lambda: _outline_with(pl, (a, a1, replace(b, ordinal=0))),
               "同级 ordinal 必须等于文档序号", "ordinal")
    must_raise(lambda: _outline_with(pl, (replace(a, child_ids=()), a1, b)),
               "child_ids 必须与其子节点对称", "child_ids")
    must_raise(lambda: _outline_with(pl, (a, a1, b),
                                     unassigned=(_make_span(pl),)),
               "unassigned 里放已归属 span 必须被拒绝", "显式未归属")

    # 悬空 resolved edge：节点不存在却标为已解析。
    dangling = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref="node:does-not-exist", edge_kind="cross_reference",
        resolution_evidence="自报已解析", occurrence=_node_occ(a.node_id))
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(dangling,)),
               "悬空 resolved edge 必须被拒绝", "悬空")
    # 端点类型必须按 edge_kind 显式规定（不是"边里出现 span 就拒绝"的宽松前缀检查）。
    check(S.EDGE_ENDPOINT_KINDS["parent_child"] == (("node",), ("node",)),
          "parent_child 的端点词表必须是 node → node")
    check(S.EDGE_ENDPOINT_KINDS["cross_reference"]
          == (("node", "span"), ("node", "span", "table")),
          "cross_reference 的端点词表必须分别列出源与目标两类")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref="span:does-not-exist", edge_kind="parent_child",
        resolution_evidence="自报已解析"),
        "parent_child 的 span 端点必须被拒绝", "必须属于")

    # --- P1-2：字符串清单 / 同值回填不得证明目标对象存在 ------------------------
    # 上一轮的 `ReferenceTargetRegistry` 是"调用方传入的字符串清单"：把伪造的目标
    # 字符串回填进集合就能让边 `is_resolved=True`。它必须已经不存在。
    check(not hasattr(S, "ReferenceTargetRegistry")
          and not hasattr(S, "REFERENCE_TARGET_SETS"),
          "字符串清单注册表必须已删除（字符串清单不是对象存在证明）")
    check("reference_targets" not in S.DocumentOutline.__dataclass_fields__,
          "DocumentOutline 不得再持久化调用方提供的目标字符串清单")

    # 伪造的 TOC 来源：TS1.3 §四.1 删除了"toc_to_body 一律不得 resolved"的**永久结构
    # 禁令**（本层现有类型化的真实来源对象 `TocSource`），因此边层不再拒绝它——边层
    # 只做声明。拒绝必须发生在对象级核验入口：字符串 toc id 配自报文本与证据，在
    # `_ts13_outline(...)` / 本函数后文的 `_outline_with(...)` 上必须失败。
    forged_toc = "toc:toc-does-not-exist"
    forged_toc_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=forged_toc,
        to_ref=f"node:{a.node_id}", edge_kind="toc_to_body",
        resolution_evidence="自报已解析",
        occurrence=_make_occurrence("unused", source_ref=forged_toc,
                                    reference_kind="toc_to_body",
                                    declared_target="第一节标题"))
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(forged_toc_edge,),
        ctx=S.ReferenceValidationContext(layout=pl)),
        "伪造 toc 字符串 + 自报文本与证据不得在对象级核验中产生 resolved 边",
        "TocSource")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref="toc:toc-does-not-exist", edge_kind="cross_reference",
        resolution_evidence="自报已解析",
        occurrence=_node_occ(a.node_id)),
        "目标为 toc 的已解析边必须被拒绝（不得凭空声称目录对象存在）",
        "必须属于")

    # 伪造的表格 locator：来源与目标都必须是**真实 TableObject**，字符串形状不算证明。
    forged_from = f"table:{S.TABLE_LOCATOR_PREFIX}0123456789abcdef"
    forged_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=forged_from,
        to_ref=f"table:{S.TABLE_LOCATOR_PREFIX}fedcba9876543210",
        edge_kind="table_continuation", resolution_evidence="自报表头一致")
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(forged_edge,)),
               "伪表端点的已解析边必须要求真实对象上下文",
               "ReferenceValidationContext")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(forged_edge,),
        ctx=S.ReferenceValidationContext()),
        "空上下文不得让伪表目标 resolved（不得只凭 loc-to- 字符串形状）",
        "真实 TableObject")

    # 真实 TableObject 但归属另一文档：对象"真实"不等于"属于本 outline"。
    other_pl = _make_page_layout(document_id="doc-other")
    foreign_table = _make_table(other_pl)
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(forged_edge,),
        ctx=S.ReferenceValidationContext(tables=(foreign_table,))),
        "上下文中的真实表格属于另一文档时必须被拒绝")

    # 真实 TableObject 且归属正确，但边引用的 locator 与它不一致：必须被拒绝。
    real_t1 = _make_table(pl, table_index_on_page=0)
    real_t2 = _make_table(pl, table_index_on_page=1, page_number=2,
                          continuation_of_locator=real_t1.table_locator)
    real_t1 = _make_table(pl, table_index_on_page=0,
                          continuation_locators=(real_t2.table_locator,))
    real_cont = S.ReferenceEdge.create(
        document_outline_locator=oloc,
        from_ref=f"table:{real_t1.table_locator}",
        to_ref=f"table:{real_t2.table_locator}",
        edge_kind="table_continuation", resolution_evidence="表头签名与列边界一致")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(real_cont,),
        ctx=S.ReferenceValidationContext(tables=(real_t1,))),
        "续表另一端不在真实对象上下文中时必须被拒绝", "真实 TableObject")
    # 正例：两端都交出真实对象 ⇒ table_continuation 可以成为**已解析事实**
    # （声明了却永不可达的 edge kind 必须被关闭）。
    ctx_cont = S.ReferenceValidationContext(tables=(real_t1, real_t2))
    resolved_cont = _outline_with(pl, (a, a1, b), edges=(real_cont,), ctx=ctx_cont)
    check(resolved_cont.verify_references(ctx_cont).is_verified(real_cont.edge_id),
          "两端都交出真实 TableObject 后 table_continuation 必须可解析")
    # 同一来源、同一目标、同一类型的两处引用（字符偏移不同）必须是**两条边**：
    # 这正是"多个 详见 / 如下表 被并成一条"的成因，不得塌缩。
    # TS1.4：未解析边的 occurrence 也要落到真实 `LayoutLine` 上，因此这两条夹具边由
    # 真实正文 span 承载（节点标题行在真实版式上无法承载该引用文本）。
    occ_ref_span = _make_ref_span(pl)
    ctx_occ = S.ReferenceValidationContext(spans=(occ_ref_span,), layout=pl)
    occ_a = _make_occurrence(occ_ref_span.span_id)
    occ_b = _make_occurrence(occ_ref_span.span_id, occurrence_index=1,
                             char_start=_REF_TEXT_2_START,
                             char_end=_REF_TEXT_2_START + len(_REF_TEXT_1))
    e_a = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{occ_ref_span.span_id}",
        to_ref=None, edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=occ_a)
    e_b = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{occ_ref_span.span_id}",
        to_ref=None, edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=occ_b)
    check(e_a.edge_locator != e_b.edge_locator and e_a.edge_id != e_b.edge_id,
          "同一来源同一目标但字符偏移不同的两处引用必须是两条不同的边"
          "（occurrence 位置必须进入 edge 定位身份）")
    check(len(_outline_with(pl, (a, a1, b), edges=(e_a, e_b),
                            ctx=ctx_occ).edges) == 2,
          "两处真实引用必须都能保留在同一 outline 中")

    # occurrence 自身：偏移与 marker 必须匹配，区间必须正长度。
    must_raise(lambda: _node_occ(a.node_id, reference_marker="如下表"),
               "occurrence 偏移与 marker 不匹配必须被拒绝", "marker")
    must_raise(lambda: _node_occ(a.node_id, char_start=3, char_end=3),
               "occurrence 零长度字符区间必须被拒绝", "正长度")
    must_raise(lambda: _node_occ(a.node_id, char_end=occ_a.char_end + 1),
               "occurrence 区间长度与归一文本不符必须被拒绝", "长度")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=occ_a),
        "occurrence 来源与 from_ref 不一致必须被拒绝", "source_ref")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence",
        occurrence=_node_occ(a.node_id, reference_kind="toc_to_body")),
        "occurrence.reference_kind 与 edge_kind 不符必须被拒绝", "reference_kind")
    check(occ_a.verify_source_text(occ_a.normalized_text) is None,
          "occurrence 必须能用来源原文逐字复核（正例）")
    must_raise(lambda: occ_a.verify_source_text("只有很短的一段文本"),
               "来源原文不足以覆盖字符区间时必须被拒绝", "不足以覆盖")

    # parent_child 是树内派生关系：允许省略文本 occurrence，但仍必须与真实父子一致。
    tree_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{a1.node_id}", edge_kind="parent_child",
        resolution_evidence="标题编号与缩进一致")
    check(tree_edge.occurrence is None, "parent_child 允许省略文本 occurrence")
    tree_outline = _outline_with(pl, (a, a1, b), edges=(tree_edge,))
    check(tree_outline.verify_references().is_verified(tree_edge.edge_id),
          "parent_child 无文本 occurrence 时必须仍可成立并通过对象级核验")

    # 端点类型必须按 edge_kind 分别规定（不是宽松前缀检查）。
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref="table:to-1", edge_kind="parent_child",
        resolution_evidence="x"), "parent_child 的 table 端点必须被拒绝", "必须属于")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref="span:os-1",
        to_ref=f"node:{a.node_id}", edge_kind="parent_child",
        resolution_evidence="x"), "parent_child 的 span 端点必须被拒绝", "必须属于")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref="bogus:1",
        to_ref=f"node:{a.node_id}", edge_kind="parent_child",
        resolution_evidence="x"), "未知引用前缀必须被拒绝", "kind")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{a.node_id}", edge_kind="parent_child",
        resolution_evidence="x"), "自环边必须被拒绝")

    # parent_child 必须与真实父子关系一致。
    wrong_parent = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}",
        to_ref=f"node:{a1.node_id}", edge_kind="parent_child",
        resolution_evidence="自报父子")
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(wrong_parent,)),
               "parent_child 与实际父子关系不符必须被拒绝", "父子关系")

    # 未解析边必须带 reason_code、真实 occurrence 与原始声明目标。
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=None, edge_kind="cross_reference",
        resolution_evidence=None, reason_code=None,
        occurrence=_node_occ(a.node_id)),
        "未解析边缺 reason_code 必须被拒绝", "reason_code")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=None, edge_kind="cross_reference",
        resolution_evidence=None, reason_code="随便写的",
        occurrence=_node_occ(a.node_id)),
        "未解析边用自造原因码必须被拒绝", "原因码")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=None, edge_kind="cross_reference",
        resolution_evidence=None, reason_code="no_textual_evidence"),
        "未解析 edge 缺 occurrence 必须被拒绝", "occurrence")
    # 未解析边**不得编造目标 id**（`to_ref` 必须为 None）。
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref="node:does-not-exist", edge_kind="cross_reference",
        resolution_evidence=None, reason_code="no_textual_evidence",
        occurrence=_node_occ(a.node_id)),
        "未解析边编造目标 id 必须被拒绝", "不得声称目标 id")
    # 未解析边必须保留原文声明的目标文字（不得丢掉原始引用）。
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=None, edge_kind="cross_reference",
        resolution_evidence=None, reason_code="no_textual_evidence",
        occurrence=_node_occ(a.node_id, declared_target=None)),
        "未解析边丢掉原文声明目标必须被拒绝", "declared_target")
    must_raise(lambda: replace(outline.edges[1], is_resolved=True,
                               reason_code=None),
               "未解析边被翻转成已解析必须被拒绝")
    must_raise(lambda: replace(outline.edges[0], is_resolved=False,
                               reason_code="deferred_to_later_stage"),
               "已解析边被翻转成未解析必须被拒绝")

    # 边必须属于本 outline。
    foreign = S.ReferenceEdge.create(
        document_outline_locator="loc-do-0000000000000000",
        from_ref=f"node:{a.node_id}", to_ref=f"node:{a1.node_id}",
        edge_kind="parent_child", resolution_evidence="x")
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(foreign,)),
               "外来 outline 的边必须被拒绝", "不一致")

    # 正常路径必须仍然可用。
    normal = _outline_with(pl, (a, a1, b), edges=(outline.edges[0],))
    check(len(normal.verify_references().verified_edge_ids()) == 1,
          "正常 resolved edge 必须可构造并通过对象级核验")
    check(outline.node_by_id(a.node_id) is not None, "正常节点必须可查表")


# ---------------------------------------------------------------------------
# E. 对齐不能自报成功
# ---------------------------------------------------------------------------

def _test_alignment_self_report():
    pl = _make_page_layout()
    al = _make_alignment(pl)

    # coverage 与 char_map 不一致（自报覆盖率，与 char_map 实际覆盖不符）。
    must_raise(lambda: replace(al, coverage=0.5), "自报 coverage 与 char_map 不符必须被拒绝",
               "确定性重算")
    must_raise(lambda: replace(al, coverage=0.0), "自报 coverage=0.0 必须被拒绝",
               "确定性重算")
    # §七 P1-E：`char_map ∪ residue` 必须**恰好**覆盖 `[0, block_char_length)`。
    # 想表达"只覆盖了 2/100 字符"，就要把剩下 98 个字符显式登记为残差；留下空洞不是
    # "低覆盖"，而是分区本身不成立（公共验证器 fail-closed）。
    weak = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=100, char_map=((0, 2, 1, 0, 0, 0),),
        residue=((2, 100, "unexplained"),))
    check(weak.coverage == 0.02, "只覆盖 2/100 字符时 coverage 必须重算为 0.02")
    check(weak.verdict != "aligned", "只覆盖 2/100 字符时不得是 aligned")
    must_raise(lambda: replace(weak, coverage=1.0),
               "低覆盖块自报满分必须被拒绝", "确定性重算")

    # verdict 与重算不符（§七.9：调用方自报的 verdict 一律不得绕过确定性重算）。
    # `al` 在冻结阈值下重算即为 aligned；这里改为**向下**篡改来证明 verdict 不是自报字段。
    must_raise(lambda: replace(al, verdict="unaligned"),
               "达标记录自报 unaligned 必须被拒绝（verdict 只能是重算结果）",
               "必须为重算结果")
    must_raise(lambda: replace(al, verdict="partially_aligned"),
               "达标记录自报 partially_aligned 必须被拒绝", "必须为重算结果")
    must_raise(lambda: replace(weak, verdict="aligned"),
               "自报 aligned 必须被拒绝（低覆盖块不得自称达标）", "必须为重算结果")
    must_raise(lambda: S.TextAlignmentRecord.from_dict(
        {**weak.to_dict(), "verdict": "aligned"}),
               "反序列化自报 aligned 同样必须被拒绝（不得比构造期宽松）",
               "必须为重算结果")
    must_raise(lambda: replace(al, residue_class="page_furniture"),
               "没有残差却自报残差分类必须被拒绝", "residue_class")
    must_raise(lambda: replace(al, residue=((0, 3, "page_furniture"),),
                               residue_class="page_furniture"),
               "残差与 char_map 不一致必须被拒绝")

    # 零长度块不得产生 aligned / 可引证。
    zero = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=1, evidence_block_id=_BLOCK_ID,
        block_char_length=0, char_map=())
    check(zero.coverage == 0.0, "零长度块 coverage 必须为 0.0")
    check(zero.verdict == "unaligned", "零长度块不得是 aligned/partially_aligned")
    check(zero.is_citable() is False, "零长度块不得可引证")
    must_raise(lambda: S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=2, evidence_block_id=_BLOCK_ID,
        block_char_length=0, residue=((0, 1, "unexplained"),)),
        "零长度块带残差必须被拒绝")

    # unexplained 残差必须阻塞：**即使覆盖率已经达到冻结阈值**（0.90）也不得 aligned。
    # §七 P1-E：分区必须完整，因此残差段是与 char_map **不相交**的尾部区间。
    unexplained = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=3, evidence_block_id=_BLOCK_ID,
        block_char_length=10, residue=((9, 10, "unexplained"),),
        char_map=((0, 9, 1, 0, 0, 0),))
    check(unexplained.residue_class == "unexplained", "unexplained 必须被识别")
    check(unexplained.coverage == 0.9,
          "该反例的覆盖率确实已达冻结阈值 0.90（阻塞不可能来自覆盖率）")
    check(unexplained.verdict != "aligned", "存在 unexplained 残差时不得 aligned")
    check(unexplained.is_citable() is False, "存在 unexplained 残差时不得可引证")

    # char_map 自身的一致性。
    must_raise(lambda: S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=4, evidence_block_id=_BLOCK_ID,
        block_char_length=10, char_map=((0, 12, 1, 0, 0, 0),)),
        "char_map 越出块长度必须被拒绝")
    must_raise(lambda: S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=5, evidence_block_id=_BLOCK_ID,
        block_char_length=10, char_map=((0, 5, 2, 0, 0, 0),)),
        "char_map 页码与记录页码不符必须被拒绝")
    must_raise(lambda: S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=6, evidence_block_id=_BLOCK_ID,
        block_char_length=10, char_map=((5, 9, 1, 0, 0, 0), (0, 4, 1, 0, 0, 0))),
        "char_map 逆序必须被拒绝")

    # `ALIGN_MIN=None` 没有旁路：对任意覆盖率都不得产出 aligned。
    for cov in (0.0, 0.01, 0.5, 0.99, 1.0):
        check(S.compute_alignment_verdict(cov, None, None) != "aligned",
              f"阈值未裁决时 coverage={cov} 不得是 aligned")

    # 冻结阈值（0.90）下 gate 必须真的放行，并且必须真的区分三态（证明 fail-closed
    # 来自阈值与 unexplained 残差，而不是写死的 False）。
    original = V.ALIGN_MIN
    try:
        V.ALIGN_MIN = 0.90
        decided = S.TextAlignmentRecord.create(
            page_layout_id=pl.page_layout_id,
            evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=7,
            evidence_block_id=_BLOCK_ID, block_char_length=10,
            char_map=((0, 10, 1, 0, 0, 0),))
        check(decided.verdict == "aligned", "达到冻结阈值且无残差时必须重算为 aligned")
        check(decided.is_citable() is True, "达到冻结阈值且无残差时必须可引证")
        below = S.TextAlignmentRecord.create(
            page_layout_id=pl.page_layout_id,
            evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=8,
            evidence_block_id=_BLOCK_ID, block_char_length=100,
            char_map=((0, 50, 1, 0, 0, 0),), residue=((50, 100, "unexplained"),))
        check(below.verdict == "unaligned",
              "未达冻结阈值时必须是 unaligned（不得停留在 partially_aligned）")
        check(below.is_citable() is False, "未达冻结阈值时不得可引证")
        partial = S.TextAlignmentRecord.create(
            page_layout_id=pl.page_layout_id,
            evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=10,
            evidence_block_id=_BLOCK_ID, block_char_length=10,
            residue=((9, 10, "unexplained"),), char_map=((0, 9, 1, 0, 0, 0),))
        check(partial.coverage == 0.9 and partial.verdict == "partially_aligned"
              and partial.is_citable() is False,
              "达到冻结阈值但含 unexplained 必须是 partially_aligned 且不可引证")
        # 精确边界：0.899999 不得被四舍五入放行，0.90 必须放行。
        check(S.compute_alignment_verdict(0.899999, None, 0.90) == "unaligned"
              and S.compute_alignment_verdict(0.90, None, 0.90) == "aligned",
              "阈值边界必须是闭区间下界 0.90（0.899999 不得放行）")
    finally:
        V.ALIGN_MIN = original
    check(V.ALIGN_MIN == 0.90, "测试必须还原 ALIGN_MIN 为生产冻结值 0.90")
    check(al.is_citable() is True,
          "还原后生产阈值下的达标记录必须仍可引证（阈值已冻结，不存在 fail-closed 复归）")


# ---------------------------------------------------------------------------
# F. OutlineSpan 引用与闭合资格
# ---------------------------------------------------------------------------

def _test_span_closure():
    pl = _make_page_layout()
    span = _make_span(pl)
    al = _make_alignment(pl)

    # alignment 记录缺失 / 重复 / 多余 / 错绑。
    must_raise(lambda: span.verify_alignment_closure(()),
               "缺少 alignment 记录必须被拒绝", "缺少")
    must_raise(lambda: span.verify_alignment_closure((al, al)),
               "重复提供同一 alignment 记录必须被拒绝", "重复")
    other = _make_alignment(pl, block_index=1)
    check(other.alignment_id != al.alignment_id, "不同块的 alignment 必须不同 id")
    must_raise(lambda: span.verify_alignment_closure((al, other)),
               "提供 span 未引用的多余记录必须被拒绝", "多余")
    foreign = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version="set-other000000",
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=_BLOCK_LEN, char_map=((0, _BLOCK_LEN, 1, 0, 0, 0),))
    must_raise(lambda: span.verify_alignment_closure((foreign,)),
               "错绑到别的 evidence set 的记录必须被拒绝", "另一个 evidence set")

    # component Evidence ↔ alignment 记录必须一对一。
    mismatched = replace(span, component_evidence_refs=(("ev-other", 0, 9),),
                         span_id=S.derive_span_id(
                             span_locator=span.span_locator,
                             schema_version=span.schema_version,
                             document_id=span.document_id,
                             document_version=span.document_version,
                             node_id=span.node_id, role=span.role,
                             unassigned_reason=span.unassigned_reason,
                             page_range=span.page_range, char_range=span.char_range,
                             layout_line_refs=span.layout_line_refs,
                             component_evidence_refs=(("ev-other", 0, 9),),
                             alignment_ids=span.alignment_ids,
                             is_fallback=span.is_fallback,
                             fallback_derivation=span.fallback_derivation,
                             is_cross_heading=span.is_cross_heading,
                             confidence=span.confidence,
                             normalized_text=span.normalized_text))
    must_raise(lambda: mismatched.verify_alignment_closure((al,)),
               "component Evidence 与 alignment 记录不一对一必须被拒绝")

    # P1-A 身份闭合：document_id / document_version 必须**显式**进入 span 身份，
    # 手工把它们换成另一份文档的值必须被"派生身份不一致"拒绝（不得只查非空字符串）。
    must_raise(lambda: replace(span, document_id="doc-9999"),
               "手工替换 OutlineSpan.document_id 必须被拒绝", "span_id 与派生身份")
    must_raise(lambda: replace(span, document_version="sha256-0000000000000000"),
               "手工替换 OutlineSpan.document_version 必须被拒绝", "span_id 与派生身份")
    # 上游对象级核对：span 的文档身份必须与 outline / PageLayout 一致。
    other_pl = _make_page_layout(document_id="doc-9999")
    must_raise(lambda: span.verify_upstream(outline=_make_outline(other_pl),
                                            layout=other_pl),
               "跨文档的 outline 必须被对象级拒绝", "document_outline_locator")
    other_outline = _make_outline(pl)
    wrong_oloc = S.derive_document_outline_locator(
        page_layout_id=other_pl.page_layout_id, document_id=other_pl.document_id,
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)
    must_raise(lambda: replace(span, document_outline_locator=wrong_oloc),
               "手工替换 OutlineSpan.document_outline_locator 必须被拒绝",
               "span_locator 与派生定位身份")

    # 冻结阈值下：记录完全闭合且 aligned 时必须可引证（§七.7 正例）。
    check(al.verdict == "aligned" and span.alignment_citable((al,)) is True,
          "冻结阈值下闭合且 aligned 的记录必须可引证（§七.7）")
    check(span.alignment_ids == (al.alignment_id,),
          "span 必须引用裁决后的记录（verdict 参与 alignment_id）")

    # 阈值降低后构造的记录不得**凭常量复原**在生产阈值下追溯合格：verdict 是构造期
    # 冻结的判定结果，只有重新走反序列化（确定性重算）才可能被重新检验，而那时
    # 生产阈值会把它判为 unaligned 并拒绝该载荷。
    original = V.ALIGN_MIN
    try:
        V.ALIGN_MIN = 0.40
        lowered = S.TextAlignmentRecord.create(
            page_layout_id=pl.page_layout_id,
            evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
            block_index=9, evidence_block_id=_BLOCK_ID, block_char_length=10,
            residue=((4, 10, "page_furniture"),), char_map=((0, 4, 1, 0, 0, 0),))
        check(lowered.coverage == 0.4 and lowered.verdict == "aligned"
              and lowered.is_citable() is True,
              "阈值降到 0.40 时同一记录必须改判 aligned（阈值确实参与判定）")
        lowered_payload = lowered.to_dict()
    finally:
        V.ALIGN_MIN = original
    check(V.ALIGN_MIN == 0.90, "测试必须还原 ALIGN_MIN 为生产冻结值 0.90")
    check(al.verdict == "aligned" and al.is_citable() is True,
          "生产阈值下的记录不受临时降阈值影响（verdict 在构造期冻结）")
    must_raise(lambda: S.TextAlignmentRecord.from_dict(lowered_payload),
               "低阈值下生成的载荷不得在生产阈值下追溯合格（反序列化必须重算拒绝）",
               "必须为重算结果")

    # 零长度 char_range。
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=span.node_id,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_anchor(2), end_anchor=_anchor(3), normalized_text="x" * 10,
        layout_line_refs=((1, 2), (1, 3)), char_range=(5, 5)),
        "零长度 char_range 必须被拒绝", "正长度")
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=span.node_id,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_anchor(2), end_anchor=_anchor(3), normalized_text="",
        layout_line_refs=((1, 2), (1, 3))),
        "空正文必须被拒绝")
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=span.node_id,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_anchor(2), end_anchor=_anchor(3), normalized_text="x" * 10,
        layout_line_refs=((1, 2),)), "行引用与起止锚点不符必须被拒绝")
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=span.node_id,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_anchor(3), end_anchor=_anchor(2), normalized_text="x" * 10,
        layout_line_refs=((1, 3), (1, 2))), "起止锚点倒置必须被拒绝")
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=span.node_id,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_anchor(2), end_anchor=_anchor(3), normalized_text="x" * 10,
        layout_line_refs=()), "空行引用必须被拒绝", "layout_line_refs")

    # 显式未归属必须给出登记原因（不得只写"未归属"）。
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=None,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="unassigned",
        start_anchor=_anchor(6), end_anchor=_anchor(6),
        normalized_text=_line_text(6), layout_line_refs=((1, 6),)),
        "未归属缺原因必须被拒绝", "未归属")
    must_raise(lambda: S.OutlineSpan.create(
        document_outline_locator=span.document_outline_locator, node_id=None,
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="unassigned",
        start_anchor=_anchor(6), end_anchor=_anchor(6),
        normalized_text=_line_text(6), layout_line_refs=((1, 6),),
        unassigned_reason="随便写的"), "未归属用自造原因必须被拒绝", "合法值")

    # 闭合资格真值表：低置信 / 跨标题 / fallback / unassigned 都不得闭合集合。
    def _variant(al_id, **kw):
        """按 kw 覆盖默认值构造 span（`node_id` / `role` 允许被覆盖，避免重复传参）。"""
        base = dict(
            document_outline_locator=span.document_outline_locator,
            node_id=span.node_id, document_id=_DOC_ID,
            document_version=_DOC_VERSION,
            evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
            start_anchor=_anchor(2), end_anchor=_anchor(3),
            normalized_text=_line_text(2) + _line_text(3),
            layout_line_refs=((1, 2), (1, 3)),
            component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN),),
            alignment_ids=(al_id,))
        base.update(kw)
        return S.OutlineSpan.create(**base)

    shapes = (
        ("普通 body span", {}),
        ("is_fallback", dict(is_fallback=True, fallback_derivation="整块兜底")),
        ("is_cross_heading", dict(is_cross_heading=True)),
        ("confidence=0", dict(confidence=0.0)),
        ("confidence=0.1", dict(confidence=0.1)),
        ("role=unassigned", dict(node_id=None, role="unassigned",
                                 unassigned_reason="no_heading_context")),
    )
    original_conf = V.SPAN_CONFIDENCE_MIN
    original_min = V.ALIGN_MIN
    try:
        # A/B 真值表第一格：**当前**阶段若阈值未裁决，任何形态都不得具备资格。
        # 用真值表判定而不是写死 None，A 阶段必然进入本分支，B 阶段则跳过。
        if not sp.ab_gate_truth_table()["threshold_decided"]:
            for label, kw in shapes:
                v = _variant(al.alignment_id, **kw)
                check(v.is_structurally_eligible() is False,
                      f"阈值未裁决时「{label}」局部结构资格必须为 False")
                check(v.can_support_set_complete(alignment_records=(al,),
                                                 boundary_verified=True) is False,
                      f"阈值未裁决时「{label}」不得支持 set_complete")

        # A/B 真值表第二格：门槛裁决后逐条验证真值表（证明 gate 是门槛，不是写死的
        # False）。这里**主动**把常量推到已裁决状态，因此无论当前处于哪一阶段都能
        # 覆盖到 B 分支；`finally` 保证还原。
        V.SPAN_CONFIDENCE_MIN = 0.5
        V.ALIGN_MIN = 0.9
        gate_b = sp.ab_gate_truth_table()
        check(gate_b["threshold_decided"] is True
              and gate_b["stage"] == "threshold_enabled"
              and gate_b["completion_enabled"] is True
              and gate_b["set_complete_supported"] is True,
              "阈值裁决后 A/B 真值表必须整体翻到 threshold_enabled")
        # 记录必须在阈值裁决之后重建：verdict 是构造期冻结的判定结果。
        decided_al = _make_alignment(pl)
        check(decided_al.is_citable() is True,
              "裁决阈值后新建的达标记录必须可引证")
        rows = [
            ("普通 body span", dict(confidence=1.0), True, True),
            ("低置信 span", dict(confidence=0.1), False, False),
            ("置信度恰等于门槛", dict(confidence=0.5), True, True),
            ("fallback span", dict(is_fallback=True,
                                   fallback_derivation="整块兜底"), False, False),
            ("跨标题 span", dict(is_cross_heading=True), False, False),
            ("零置信 span", dict(confidence=0.0), False, False),
        ]
        for label, kw, want_eligible, want_complete in rows:
            v = _variant(decided_al.alignment_id, **kw)
            check(v.is_structurally_eligible() is want_eligible,
                  f"门槛裁决后「{label}」局部结构资格必须为 {want_eligible}")
            check(v.can_support_set_complete(
                alignment_records=(decided_al,),
                boundary_verified=True) is want_complete,
                f"门槛裁决后「{label}」set_complete 资格必须为 {want_complete}")
            if want_complete:
                check(v.can_support_set_complete(
                    alignment_records=(decided_al,),
                    boundary_verified=False) is False,
                    f"「{label}」边界未核验时必须不得闭合")
                check(v.can_support_set_complete(
                    alignment_records=(decided_al,),
                    boundary_verified=True) is True,
                    f"「{label}」边界已核验且记录可引证时必须可闭合")

        # 未归属 / 无 component Evidence 的 span 在任何阈值下都不得闭合。
        unassigned_v = S.OutlineSpan.create(
            document_outline_locator=span.document_outline_locator, node_id=None,
            document_id=_DOC_ID, document_version=_DOC_VERSION,
            evidence_set_version=_EVIDENCE_SET_VERSION, role="unassigned",
            start_anchor=_anchor(6), end_anchor=_anchor(6),
            normalized_text=_line_text(6), layout_line_refs=((1, 6),),
            unassigned_reason="below_last_heading")
        check(unassigned_v.is_structurally_eligible() is False,
              "未归属 span 不得具备局部结构资格")
        check(unassigned_v.can_support_set_complete(
            alignment_records=(), boundary_verified=True) is False,
            "未归属 span 不得支持 set_complete")
        bare = S.OutlineSpan.create(
            document_outline_locator=span.document_outline_locator,
            node_id=span.node_id, document_id=_DOC_ID,
            document_version=_DOC_VERSION,
            evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
            start_anchor=_anchor(2), end_anchor=_anchor(2),
            normalized_text=_line_text(2), layout_line_refs=((1, 2),))
        check(bare.can_support_set_complete(alignment_records=(),
                                            boundary_verified=True) is False,
              "无 component Evidence 的 span 不得支持 set_complete")
    finally:
        V.SPAN_CONFIDENCE_MIN = original_conf
        V.ALIGN_MIN = original_min
    check(V.SPAN_CONFIDENCE_MIN == original_conf and V.ALIGN_MIN == 0.90,
          "测试必须还原两个阈值（SPAN 恢复原值、ALIGN_MIN 仍为冻结值 0.90）")
    check(sp.ab_gate_truth_table()["threshold_decided"]
          is (V.SPAN_CONFIDENCE_MIN is not None),
          "还原后 A/B 真值表必须与常量重新一致（monkeypatch 不得渗漏）")

    # 不得保留无上下文的便利判定方法。
    check(not hasattr(S.OutlineSpan, "can_support_set_complete_naive"),
          "不得保留无上下文便利方法")
    for name in ("can_support_set_complete",):
        import inspect
        sig = inspect.signature(getattr(S.OutlineSpan, name))
        for p in list(sig.parameters)[1:]:
            check(sig.parameters[p].default is inspect.Parameter.empty,
                  f"{name} 的参数 {p} 不得有默认值")


# ---------------------------------------------------------------------------
# G. TableObject provenance 与 cell_grid
# ---------------------------------------------------------------------------

def _table_with(pl, **overrides):
    outline = _make_outline(pl)
    base = dict(
        page_layout_id=pl.page_layout_id,
        document_outline_id=outline.outline_id,
        document_outline_locator=outline.outline_locator,
        node_id=outline.nodes[1].node_id, document_id=pl.document_id,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, table_index_on_page=0,
        table_bbox=(100.0, 300.0, 260.0, 300.0 + 3 * _H), title="示例表题",
        title_source="caption_line", unit="万元",
        structure_class="ordinary_business_table",
        structure_evidence=("columnar", "data"), column_count=2,
        header_rows=(_table_rows()[0],), body_rows=_table_rows()[1:],
        cell_grid=_make_table(pl).cell_grid,
        component_span_ids=(_make_span(pl).span_id,),
        alignment_ids=(_make_alignment(pl).alignment_id,),
        component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN),))
    base.update(overrides)
    return S.TableObject.create(**base)


def _test_table_provenance_and_grid():
    pl = _make_page_layout()
    table = _make_table(pl)
    al = _make_alignment(pl)
    span = _make_span(pl)

    # 缺 component provenance。
    must_raise(lambda: _table_with(pl, component_span_ids=()),
               "缺 component span 的表格必须被拒绝", "component_span_ids")
    must_raise(lambda: _table_with(pl, alignment_ids=()),
               "缺 alignment 的表格必须被拒绝", "alignment_ids")
    must_raise(lambda: _table_with(pl, structure_evidence=("data",)),
               "缺列边界证据的表格必须被拒绝", "columnar")
    must_raise(lambda: _table_with(pl, structure_evidence=()),
               "无结构信号的表格必须被拒绝", "structure_evidence")
    must_raise(lambda: _table_with(pl, structure_evidence=("columnar", "自造信号")),
               "非既有词汇的结构信号必须被拒绝")
    must_raise(lambda: _table_with(pl, header_rows=()),
               "无物理表头的表格必须被拒绝", "header_rows")
    must_raise(lambda: _table_with(pl, body_rows=()),
               "只有表头的区域不得算表格", "body_rows")
    must_raise(lambda: _table_with(pl, column_count=1),
               "列数 < 2 必须被拒绝")

    # provenance 必须是**对象级闭合**：字符串集合入口已不存在（不得再靠传任意
    # 字符串证明对象存在）。
    import inspect as _inspect
    params = set(_inspect.signature(S.TableObject.verify_provenance).parameters)
    check("spans" in params and "span_ids" not in params,
          "provenance 必须只接受真实对象集合（不得再有 span_ids 字符串集合入口）")

    must_raise(lambda: table.verify_provenance(spans=(), alignment_records=(al,)),
               "缺 component span 必须被拒绝", "component OutlineSpan")
    extra_span = _make_span(_make_page_layout(document_id="doc-9999"))
    must_raise(lambda: table.verify_provenance(
        spans=(span, extra_span), alignment_records=(al,)),
        "多余 span 必须被拒绝", "非 component")
    must_raise(lambda: table.verify_provenance(spans=(span,), alignment_records=()),
               "缺 alignment 记录必须被拒绝", "缺少引用")
    must_raise(lambda: table.verify_provenance(
        spans=(span,), alignment_records=(al, _make_alignment(pl, block_index=1))),
        "多余 alignment 记录必须被拒绝", "未引用")
    foreign = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id, evidence_set_version="set-other000000",
        page_number=1, block_index=0, evidence_block_id=_BLOCK_ID,
        block_char_length=_BLOCK_LEN, char_map=((0, _BLOCK_LEN, 1, 0, 0, 0),))
    must_raise(lambda: table.verify_provenance(
        spans=(span,), alignment_records=(foreign,)),
        "错绑 evidence set 的记录必须被拒绝", "另一个 evidence set")
    # 跨文档 / 跨 outline 的 span 必须被对象级拒绝（不得只比字符串集合）。
    must_raise(lambda: table.verify_provenance(
        spans=(_make_span(_make_page_layout(document_id="doc-9999")),),
        alignment_records=(al,)),
        "跨文档 span 必须被拒绝", "另一个文档")
    # 同一 Evidence ID 但**字符区间不同**也算不闭合。
    same_id_other_range = replace(
        span, component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN - 1),),
        span_id=S.derive_span_id(
            span_locator=span.span_locator, schema_version=span.schema_version,
            document_id=span.document_id, document_version=span.document_version,
            node_id=span.node_id, role=span.role,
            unassigned_reason=span.unassigned_reason, page_range=span.page_range,
            char_range=span.char_range, layout_line_refs=span.layout_line_refs,
            component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN - 1),),
            alignment_ids=span.alignment_ids, is_fallback=span.is_fallback,
            fallback_derivation=span.fallback_derivation,
            is_cross_heading=span.is_cross_heading, confidence=span.confidence,
            normalized_text=span.normalized_text))
    must_raise(lambda: table.verify_provenance(
        spans=(same_id_other_range,), alignment_records=(al,)),
        "同一 Evidence ID 但字符区间不同必须被拒绝", "不闭合")
    # 正常闭合路径必须通过（反例不得靠"全拒绝"通过）。
    check(table.verify_provenance(spans=(span,), alignment_records=(al,)) is None,
          "完整闭合的 provenance 必须通过（正例）")
    # 冻结阈值 0.90 下的正例：夹具记录全覆盖且无残差 ⇒ aligned ⇒ 可引用。
    check(al.verdict == "aligned" and al.is_citable() is True,
          "冻结阈值 0.90 下全覆盖且无残差的夹具记录必须 aligned 且可引用")
    check(table.is_evidence_backed(spans=(span,), alignment_records=(al,)) is True,
          "完整闭合且全部可引用时 is_evidence_backed 必须为 True")

    # cell_grid：重叠 / 越界 / 行不连续 / 与行文本错绑。
    grid = _make_table(pl).cell_grid
    must_raise(lambda: _table_with(pl, cell_grid=grid + (
        S.TableCell(row=0, column=0, rowspan=1, colspan=1, text="重叠",
                    bbox=(100.0, 300.0, 180.0, 312.0), source_locator=(1, 0, 0)),)),
        "网格重叠必须被拒绝", "重叠")
    must_raise(lambda: _table_with(pl, cell_grid=(
        S.TableCell(row=0, column=1, rowspan=1, colspan=2, text="越界",
                    bbox=(100.0, 300.0, 260.0, 312.0), source_locator=(1, 0, 0)),) + grid[1:]),
        "单元格列越界必须被拒绝", "越出 column_count")
    must_raise(lambda: _table_with(pl, cell_grid=grid[:4]),
               "网格行数与行分区不符必须被拒绝")
    must_raise(lambda: _table_with(pl, cell_grid=(
        replace(grid[0], text="错绑"),) + grid[1:]),
        "单元格与行文本错绑必须被拒绝", "cells")
    must_raise(lambda: _table_with(pl, body_rows=(
        S.TableRow(row_index=5, kind="body", cells=("营业收入", "100"), label=None),)),
        "行 row_index 不连续必须被拒绝", "连续递增")
    must_raise(lambda: _table_with(pl, body_rows=(
        S.TableRow(row_index=1, kind="header", cells=("营业收入", "100"), label=None),)),
        "body_rows 里放表头行必须被拒绝")
    must_raise(lambda: _table_with(pl, cell_grid=(
        replace(grid[0], source_locator=(9, 0, 0)),) + grid[1:]),
        "单元格来源页不等于本表页必须被拒绝", "来源页")
    must_raise(lambda: _table_with(pl, cell_grid=(
        replace(grid[0], colspan=0),) + grid[1:]),
        "colspan < 1 必须被拒绝")
    must_raise(lambda: _table_with(pl, cell_grid=(
        replace(grid[0], rowspan=9),) + grid[1:]),
        "rowspan 越出行数必须被拒绝")
    must_raise(lambda: S.TableCell(
        row=0, column=0, rowspan=1, colspan=1, text="x",
        bbox=(100.0, 300.0, 100.0, 312.0), source_locator=(1, 0, 0)),
        "单元格零宽 bbox 必须被拒绝", "bbox")
    must_raise(lambda: S.TableCell(
        row=0, column=0, rowspan=1, colspan=1, text="x",
        bbox=(100.0, 300.0, 180.0, 312.0), source_locator=(0, 0, 0)),
        "单元格来源页码 0 必须被拒绝")
    must_raise(lambda: _table_with(pl, continuation_locators=(table.table_locator,)),
               "续表指向自身定位必须被拒绝", "自身定位")
    must_raise(lambda: _table_with(pl, continuation_of_locator=table.table_locator),
               "continuation_of 指向自身定位必须被拒绝", "自身定位")
    must_raise(lambda: _table_with(pl, continuation_locators=(table.table_id,)),
               "续表关系引用 revision id 必须被拒绝", "稳定定位")
    must_raise(lambda: _table_with(pl, continuation_of_locator=table.table_id),
               "continuation_of 引用 revision id 必须被拒绝", "稳定定位")
    must_raise(lambda: _table_with(pl, continuation_locators=(
        "loc-to-aaaaaaaaaaaaaaaa", "loc-to-aaaaaaaaaaaaaaaa")),
        "续表定位重复必须被拒绝", "重复")
    must_raise(lambda: _table_with(pl, title=None, title_source="caption_line"),
               "title 与 title_source 不一致必须被拒绝")

    # rowspan / colspan 的**正例**：合法合并单元格必须可构造（反例不得靠"拒绝一切
    # 合并"通过）。语义：`TableRow.cells` 只列从本行**起始**的单元格，rowspan 续接到
    # 本行的单元格不在本行重复。
    def _merged_grid(spec):
        """按 (row, col) 显式布局构造网格；某格为 None 表示它被上一行的 rowspan 覆盖。"""
        out = []
        for r, row in enumerate(spec):
            for c, span in enumerate(row):
                if span is None:
                    continue
                rs, cs = span
                x0 = 100.0 + sum(80.0 for _ in range(c))
                y0 = 300.0 + r * _H
                out.append(S.TableCell(
                    row=r, column=c, rowspan=rs, colspan=cs,
                    text=f"c{r}{c}",
                    bbox=(x0, y0, x0 + cs * 80.0, y0 + rs * _H),
                    source_locator=(1, r, c)))
        return tuple(out)

    # 合法 colspan：第 0 行整行合并成 1 格（2 列），第 1–2 行正常两列。
    span_rows = (
        S.TableRow(row_index=0, kind="header", cells=("c00",), label=None),
        S.TableRow(row_index=1, kind="body", cells=("c10", "c11"), label=None),
        S.TableRow(row_index=2, kind="body", cells=("c20", "c21"), label=None),
    )
    colspan_grid = _merged_grid([[(1, 2)], [(1, 1), (1, 1)], [(1, 1), (1, 1)]])
    merged = _table_with(pl, header_rows=(span_rows[0],), body_rows=span_rows[1:],
                         cell_grid=colspan_grid)
    check(len(merged.cell_grid) == 5, "合法 colspan 必须可构造（5 个物理单元格）")
    check(_table_with(pl, header_rows=(span_rows[0],), body_rows=span_rows[1:],
                      cell_grid=colspan_grid).table_id == merged.table_id,
          "合法 colspan 表格身份必须可复现")

    # 合法 rowspan：第 0 列在第 0 行合并 2 行；第 1 行只列从本行起始的单元格。
    rspan_rows = (
        S.TableRow(row_index=0, kind="header", cells=("c00", "c01"), label=None),
        S.TableRow(row_index=1, kind="body", cells=("c11",), label=None),
        S.TableRow(row_index=2, kind="body", cells=("c20", "c21"), label=None),
    )
    rowspan_grid = _merged_grid([[(2, 1), (1, 1)], [None, (1, 1)], [(1, 1), (1, 1)]])
    rowspan_ok = _table_with(pl, header_rows=(rspan_rows[0],),
                             body_rows=rspan_rows[1:], cell_grid=rowspan_grid)
    check(rowspan_ok.all_rows() == rspan_rows, "合法 rowspan 必须可构造")
    check(rowspan_ok.all_rows()[1].cells == ("c11",),
          "rowspan 续接的单元格不得在后续行重复")

    # 合法 rowspan + colspan 组合：左上 2×2 合并块。
    combo_rows = (
        S.TableRow(row_index=0, kind="header", cells=("c00", "c02"), label=None),
        S.TableRow(row_index=1, kind="body", cells=("c12",), label=None),
        S.TableRow(row_index=2, kind="body", cells=("c20", "c21", "c22"), label=None),
    )
    combo_grid = _merged_grid(
        [[(2, 2), None, (1, 1)], [None, None, (1, 1)], [(1, 1), (1, 1), (1, 1)]])
    combo = _table_with(pl, column_count=3, header_rows=(combo_rows[0],),
                        body_rows=combo_rows[1:], cell_grid=combo_grid,
                        table_bbox=(100.0, 300.0, 340.0, 300.0 + 3 * _H))
    check(combo.column_count == 3, "rowspan + colspan 组合必须可构造")

    # rowspan 造成的**空洞**必须被全局活跃网格检出。
    hole_rows = (
        S.TableRow(row_index=0, kind="header", cells=("c00", "c01"), label=None),
        S.TableRow(row_index=1, kind="body", cells=("c10",), label=None),
        S.TableRow(row_index=2, kind="body", cells=("c20", "c21"), label=None),
    )
    hole_grid = _merged_grid([[(1, 1), (1, 1)], [(1, 1)], [(1, 1), (1, 1)]])
    must_raise(lambda: _table_with(pl, header_rows=(hole_rows[0],),
                                   body_rows=hole_rows[1:], cell_grid=hole_grid),
               "rowspan 造成的网格空洞必须被拒绝", "空洞")
    # rowspan 造成的**重叠**必须被检出。
    overlap_rows = (
        S.TableRow(row_index=0, kind="header", cells=("c00", "c01"), label=None),
        S.TableRow(row_index=1, kind="body", cells=("c10", "c11"), label=None),
        S.TableRow(row_index=2, kind="body", cells=("c20", "c21"), label=None),
    )
    overlap_grid = _merged_grid(
        [[(2, 1), (1, 1)], [(1, 1), (1, 1)], [(1, 1), (1, 1)]])
    must_raise(lambda: _table_with(pl, header_rows=(overlap_rows[0],),
                                   body_rows=overlap_rows[1:],
                                   cell_grid=overlap_grid),
               "rowspan 造成的网格重叠必须被拒绝", "重叠")

    # 单元格 bbox 必须落在表格 bbox 内（此处单元格 bbox 与表的 y 范围不匹配）。
    tall_cell = replace(grid[0], bbox=(100.0, 300.0, 180.0, 400.0))
    must_raise(lambda: _table_with(pl, cell_grid=(tall_cell,) + grid[1:]),
               "单元格 bbox 越出表格 bbox 必须被拒绝", "必须落在表格 bbox")

    # 续表链条必须**可构造且自洽**（上一轮的实现里链条不可能同时一致）。
    second = _table_with(pl, table_index_on_page=1,
                         continuation_of_locator=table.table_locator)
    chained = _table_with(pl, continuation_locators=(second.table_locator,))
    check(second.continuation_of_locator == chained.table_locator,
          "续表链条必须自洽：后表指向的表必须就是前表")
    check(chained.continuation_locators == (second.table_locator,),
          "续表定位必须原样保留")
    check(chained.table_locator == table.table_locator,
          "续表关系不得改变 table_locator（定位与内容无关）")
    check(chained.table_id != table.table_id,
          "续表关系属于规范形：它变化时 table_id 必须变化")
    check(second.table_id != table.table_id, "不同页内序号的表必须不同 id")

    # 表体 / 表题 / 合计 / 单位 / 来源范围任一变化 ⇒ 内容身份必须变化。
    rows = _table_rows()
    variants = {
        "企业名": (rows[1], replace(rows[1], cells=("甲公司", "100"))),
        "比例": (rows[2], replace(rows[2], cells=("净利润", "21"))),
        "表头": (rows[0], replace(rows[0], cells=("项目", "金额万元"))),
        "合计行": (None, S.TableRow(row_index=3, kind="total", cells=("合计", "120"),
                                    label="合计")),
    }
    for label, (old, new) in variants.items():
        if label == "合计行":
            alt = _table_with(pl, total_rows=(new,),
                              table_bbox=(100.0, 300.0, 260.0, 300.0 + 4 * _H),
                              cell_grid=(_make_table(pl).cell_grid + (
                    S.TableCell(row=3, column=0, rowspan=1, colspan=1, text="合计",
                                bbox=(100.0, 300.0 + 3 * _H, 180.0, 312.0 + 3 * _H),
                                source_locator=(1, 0, 0)),
                    S.TableCell(row=3, column=1, rowspan=1, colspan=1, text="120",
                                bbox=(180.0, 300.0 + 3 * _H, 260.0, 312.0 + 3 * _H),
                                source_locator=(1, 0, 1)),)))
        else:
            new_rows = tuple(new if r is old else r for r in rows)
            alt = _make_table(pl, rows=new_rows)
        check(alt.table_id != table.table_id,
              f"表格「{label}」变化必须改变 table_id")
        if label == "合计行":
            # 新增合计行同时扩展了物理几何（bbox 必须容纳第 4 行），而 table_locator
            # **刻意**绑定 bbox，因此这一条必须随之变化 —— 定位随几何变、内容身份随
            # 内容变，两者不得混为一谈。
            check(alt.table_locator != table.table_locator,
                  "合计行扩展表格几何时 table_locator 必须随之变化")
        else:
            check(alt.table_locator == table.table_locator,
                  f"表格「{label}」变化不得改变 table_locator")
    check(_table_with(pl, unit="元").table_id != table.table_id,
          "单位变化必须改变 table_id")
    check(_table_with(pl, title="另一个表题").table_id != table.table_id,
          "表题变化必须改变 table_id")
    page2_grid = tuple(replace(c, source_locator=(2, c.row, 0))
                       for c in _make_table(pl).cell_grid)
    check(_table_with(pl, page_number=2, cell_grid=page2_grid).table_id
          != table.table_id, "来源页变化必须改变 table_id")
    check(_table_with(pl, page_number=2, cell_grid=page2_grid).table_locator
          != table.table_locator, "来源页变化必须改变 table_locator")

    # 仅空白规范化差异必须保持身份稳定。
    spaced = _table_rows()
    ws_rows = (replace(spaced[0], cells=("项目 ", " 金额")),
               replace(spaced[1], cells=("营业收入", "100 ")),
               replace(spaced[2], cells=(" 净利润", "20")))
    check(_make_table(pl, rows=ws_rows).table_id == table.table_id,
          "仅空白差异必须保持 table_id 稳定")
    check(canonical_text(" 营业  收入 ") == "营业 收入",
          "canonical_text 必须折叠首尾与连续空白（`' '.join(text.split())`）")

    # 表格不得是金额权威，也不得携带业务解释字段。
    check(table.is_financial_authority() is False, "表格不得自称金额权威")
    for fname in S.TableRow.__dataclass_fields__:
        check("semantic" not in fname and "credit" not in fname
              and "financial" not in fname,
              f"TableRow 不得含业务解释字段 {fname}")
    for fname in S.TableObject.__dataclass_fields__:
        check("semantic" not in fname, f"TableObject 不得含业务解释字段 {fname}")


# ---------------------------------------------------------------------------
# I. TS1.2 引用绑定信任边界（独立反例）
# ---------------------------------------------------------------------------

def _ref_occ(span_id, **kw):
    """落在真实正文 span 上的 occurrence（默认第 0 处「详见」）。"""
    base = dict(source_ref=f"span:{span_id}", page_number=1,
                line_index=_REF_SPAN_LINE, char_start=_REF_TEXT_1_START,
                char_end=len(_REF_TEXT_1), occurrence_index=0,
                reference_marker="详见", reference_kind="cross_reference",
                declared_target=_line_text(_REF_SPAN_LINE),
                normalized_text=_REF_TEXT_1)
    base.update(kw)
    return S.ReferenceOccurrence(**base)


def _test_reference_trust_boundary():
    """§四 逐条反例：核验上下文不得进入内容身份；已解析边必须落到**真实对象**上。"""
    pl = _make_page_layout()
    outline = _make_outline(pl)
    a, a1, b = outline.nodes
    oloc = outline.outline_locator
    ref_span = _make_ref_span(pl)
    # TS1.3：occurrence 的 (页, 行) 必须定位一条**真实 LayoutLine**，字符区间是
    # 行内区间；因此核验上下文必须交出真实 PageLayout，只交 span 对象不够。
    ctx_used = S.ReferenceValidationContext(spans=(ref_span,), layout=pl)

    resolved = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
        resolution_evidence="正文互指文本与目标标题一致",
        occurrence=_ref_occ(ref_span.span_id))

    # (1) 上下文里多出**没有任何边使用**的目标：JSON / 指纹 / 定位 / id 必须逐字节不变。
    unused_table = _make_table(pl, page_number=4, table_index_on_page=0)
    unused_span = _make_ref_span(pl, node_id=b.node_id)
    check(unused_span.span_id != ref_span.span_id,
          "未被使用的第二个 span 夹具必须与已使用的不是同一对象")
    ctx_wide = S.ReferenceValidationContext(
        spans=(ref_span, unused_span), tables=(unused_table,), layout=pl)
    narrow = _outline_with(pl, (a, a1, b), edges=(resolved,), ctx=ctx_used)
    wide = _outline_with(pl, (a, a1, b), edges=(resolved,), ctx=ctx_wide)
    check(canonical_json(narrow.to_dict()) == canonical_json(wide.to_dict()),
          "多出未被任何边使用的核验目标不得改变 outline 的 wire format")
    check(narrow.content_fingerprint == wide.content_fingerprint
          and narrow.outline_id == wide.outline_id
          and narrow.outline_locator == wide.outline_locator,
          "多出未被任何边使用的核验目标不得改变内容指纹与身份")
    # 核验上下文既不在 wire format 里，也不在指纹里。
    for key in ("reference_context", "reference_targets", "verified_references",
                "context_fingerprint"):
        check(key not in wide.to_dict(), f"outline wire format 不得出现 {key!r} 字段")
    check(ctx_wide.fingerprint() not in canonical_json(wide.to_dict()),
          "核验上下文指纹不得出现在 outline 内容里（核验上下文不是文档内容）")
    check(ctx_used.fingerprint() != ctx_wide.fingerprint(),
          "不同的核验上下文必须得到不同指纹（否则结论不可复核）")

    # (2)(3) 伪造的 toc / 表格目标字符串：不得靠字符串清单或同值回填冒充对象存在。
    check(not hasattr(S, "ReferenceTargetRegistry")
          and not hasattr(S, "REFERENCE_TARGET_SETS"),
          "字符串清单注册表必须已删除：字符串清单不是对象存在证明")
    # TS1.3 §四.1：`toc_to_body` 的**永久结构禁令**已删除，本层有类型化的真实
    # TOC 来源对象 `TocSource`。但"字符串 toc id + 自报文本"仍然不是对象：
    # `ReferenceEdge.create` 不再拒绝它（边层只是声明），拒绝发生在对象级核验入口。
    forged_toc = "toc:toc-does-not-exist"
    forged_toc_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=forged_toc,
        to_ref=f"node:{a.node_id}", edge_kind="toc_to_body",
        resolution_evidence="自报已解析",
        occurrence=S.ReferenceOccurrence(
            source_ref=forged_toc, page_number=1, line_index=0,
            char_start=0, char_end=len("目录项 第一节标题"),
            occurrence_index=0, reference_marker="第一节标题",
            reference_kind="toc_to_body", declared_target="第一节标题",
            normalized_text="目录项 第一节标题"))
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(forged_toc_edge,),
        ctx=S.ReferenceValidationContext(layout=pl)),
        "伪造 toc 字符串不得产生 resolved 边（字符串清单不是对象存在证明）",
        "TocSource")
    forged_loc = f"table:{S.TABLE_LOCATOR_PREFIX}0123456789abcdef"
    forged_table_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=forged_loc,
        to_ref=f"table:{S.TABLE_LOCATOR_PREFIX}fedcba9876543210",
        edge_kind="table_continuation", resolution_evidence="自报表头一致")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(forged_table_edge,),
        ctx=S.ReferenceValidationContext(tables=(unused_table,))),
        "伪造的 table locator 不得靠同值字符串回填产生 resolved 边",
        "真实 TableObject")

    # (4) 真实 TableObject 但 locator / revision 不匹配：必须被拒绝。
    same_loc_a = _make_table(pl, page_number=2, table_index_on_page=1)
    same_loc_b = _make_table(pl, page_number=2, table_index_on_page=1, unit="元")
    check(same_loc_a.table_locator == same_loc_b.table_locator
          and same_loc_a.table_id != same_loc_b.table_id,
          "同一 table_locator 的两个 revision 夹具必须成立")
    must_raise(lambda: S.ReferenceValidationContext(tables=(same_loc_a, same_loc_b)),
               "同一 table_locator 对应两个 revision 必须 fail-closed（歧义不得消解）",
               "歧义")
    mismatch_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc,
        from_ref=f"table:{S.TABLE_LOCATOR_PREFIX}ffffffffffffffff",
        to_ref=f"table:{same_loc_a.table_locator}",
        edge_kind="table_continuation", resolution_evidence="自报表头一致")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(mismatch_edge,),
        ctx=S.ReferenceValidationContext(tables=(same_loc_a,))),
        "真实 TableObject 的 locator 与边引用的不一致时必须被拒绝",
        "真实 TableObject")
    other_pl = _make_page_layout(document_id="doc-other")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(mismatch_edge,),
        ctx=S.ReferenceValidationContext(tables=(_make_table(other_pl),))),
        "上下文中的真实表格属于另一文档时必须被拒绝")

    # (5)(6)(7) occurrence 必须落在真实来源的页码 / 行号 / 字符区间上。
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), ctx=ctx_used, edges=(
            S.ReferenceEdge.create(
                document_outline_locator=oloc,
                from_ref=f"span:{ref_span.span_id}",
                to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
                resolution_evidence="自报已解析",
                occurrence=_ref_occ(ref_span.span_id, page_number=999)),)),
        "occurrence 页码超出真实来源必须被拒绝", "页码")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), ctx=ctx_used, edges=(
            S.ReferenceEdge.create(
                document_outline_locator=oloc,
                from_ref=f"span:{ref_span.span_id}",
                to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
                resolution_evidence="自报已解析",
                occurrence=_ref_occ(ref_span.span_id, line_index=999)),)),
        "occurrence 行号超出真实来源必须被拒绝", "行号")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), ctx=ctx_used, edges=(
            S.ReferenceEdge.create(
                document_outline_locator=oloc,
                from_ref=f"span:{ref_span.span_id}",
                to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
                resolution_evidence="自报已解析",
                occurrence=_ref_occ(ref_span.span_id, char_start=1,
                                    char_end=1 + len(_REF_TEXT_1))),)),
        "occurrence 字符区间与真实来源原文不一致必须被拒绝")

    # (8) marker / declared_target 只存在于**调用方自报文本**里，真实来源里没有：
    #     自报文本自洽（甚至能通过自证入口）也不构成证明。
    forged_occ = S.ReferenceOccurrence(
        source_ref=f"span:{ref_span.span_id}", page_number=1,
        line_index=_REF_SPAN_LINE, char_start=0, char_end=len("详见伪造目标"),
        occurrence_index=0, reference_marker="详见", reference_kind="cross_reference",
        declared_target="伪造目标", normalized_text="详见伪造目标")
    check(forged_occ.verify_source_text(forged_occ.normalized_text) is None,
          "「把自报文本喂回自证入口」必然通过——这正是它不得作为证明的原因")
    must_raise(lambda: forged_occ.verify_source_text(_REF_TEXT_1 + "；另" + _REF_TEXT_1),
               "marker / declared_target 只在自报文本中的 occurrence 必须被真实原文拒绝")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), ctx=ctx_used, edges=(
            S.ReferenceEdge.create(
                document_outline_locator=oloc,
                from_ref=f"span:{ref_span.span_id}",
                to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
                resolution_evidence="self asserted", occurrence=forged_occ),)),
        "自报 normalized_text + resolution_evidence 不得让已解析边通过对象级核验")

    # (9)(10)(11) 定位身份与修订身份的归属（实测值见下方相等/不等断言）。
    re_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=None, edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=_ref_occ(ref_span.span_id))
    check(re_open.edge_locator == resolved.edge_locator
          and re_open.edge_id != resolved.edge_id,
          "同一 occurrence 未解析 → 已解析：locator 必须相同、edge_id 必须不同")
    retargeted = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=f"node:{a1.node_id}", edge_kind="cross_reference",
        resolution_evidence="改为指向另一标题",
        occurrence=_ref_occ(ref_span.span_id))
    check(retargeted.edge_locator == resolved.edge_locator
          and retargeted.edge_id != resolved.edge_id,
          "同一 occurrence 换目标：locator 必须相同、edge_id 必须不同")
    shifted = _ref_occ(ref_span.span_id, char_start=_REF_TEXT_2_START,
                       char_end=_REF_TEXT_2_START + len(_REF_TEXT_1),
                       occurrence_index=1)
    second = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
        resolution_evidence="正文互指文本与目标标题一致", occurrence=shifted)
    check(second.edge_locator != resolved.edge_locator
          and second.edge_id != resolved.edge_id,
          "两处真实引用位置不同：locator 与 edge_id 都必须不同")

    # (12)(13) 正例必须仍然通过（防止"一律拒绝已解析"也能满足上面的反例）。
    pc = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{a1.node_id}", edge_kind="parent_child",
        resolution_evidence="标题编号与缩进一致")
    pc_outline = _outline_with(pl, (a, a1, b), edges=(pc,))
    check(pc_outline.verify_references().is_verified(pc.edge_id),
          "合法的 parent_child 必须仍能通过对象级核验")
    ok = _outline_with(pl, (a, a1, b), edges=(resolved, second), ctx=ctx_used)
    vr = ok.verify_references(ctx_used)
    check(vr.is_verified(resolved.edge_id) and vr.is_verified(second.edge_id),
          "合法的 span → node 引用必须仍能通过对象级核验")
    check(vr.unresolved_edge_ids() == (),
          "两条都已核验时未解析清单必须为空")
    t1 = _make_table(pl, table_index_on_page=0)
    t2 = _make_table(pl, table_index_on_page=1, page_number=2,
                     continuation_of_locator=t1.table_locator)
    t1 = _make_table(pl, table_index_on_page=0,
                     continuation_locators=(t2.table_locator,))
    cont = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"table:{t1.table_locator}",
        to_ref=f"table:{t2.table_locator}", edge_kind="table_continuation",
        resolution_evidence="表头签名与列边界一致")
    ctx_t = S.ReferenceValidationContext(tables=(t1, t2))
    cont_outline = _outline_with(pl, (a, a1, b), edges=(cont,), ctx=ctx_t)
    check(cont_outline.verify_references(ctx_t).is_verified(cont.edge_id),
          "合法的 table → table 续表引用必须仍能通过对象级核验")

    # (14) round-trip 不依赖任何被序列化下来的核验上下文。
    d = ok.to_dict()
    check("reference_context" not in d and "reference_targets" not in d
          and "context_fingerprint" not in d,
          "wire format 不得携带核验上下文")
    must_raise(lambda: S.DocumentOutline.from_dict(d),
               "无法自证的已解析边在没有核验上下文时必须拒绝反序列化")
    rt = S.DocumentOutline.from_dict(d, reference_context=ctx_used)
    check(canonical_json(rt.to_dict()) == canonical_json(d)
          and rt.outline_id == ok.outline_id
          and rt.content_fingerprint == ok.content_fingerprint,
          "带核验上下文反序列化必须还原逐字节相同的内容与身份")
    rt_wide = S.DocumentOutline.from_dict(d, reference_context=ctx_wide)
    check(rt_wide.outline_id == rt.outline_id
          and canonical_json(rt_wide.to_dict()) == canonical_json(rt.to_dict()),
          "换一组等价的核验上下文不得改变反序列化后的内容身份")

    # (15) 旧 wire format 必须被显式拒绝，不得静默按新版本解释。
    must_raise(lambda: S.DocumentOutline.from_dict({**d, "schema_version": "do-3"}),
               "旧 outline wire format do-3 必须要求显式迁移", "旧 wire format")
    must_raise(lambda: S.DocumentOutline.from_dict({**d, "schema_version": "do-2"}),
               "旧 outline wire format do-2 必须要求显式迁移", "旧 wire format")
    edge_dict = resolved.to_dict()
    must_raise(lambda: S.ReferenceEdge.from_dict({**edge_dict, "schema_version": "res-2"}),
               "旧 reference edge wire format res-2 必须要求显式迁移", "旧 wire format")
    must_raise(lambda: S.ReferenceEdge.from_dict({**edge_dict, "schema_version": "res-1"}),
               "旧 reference edge wire format res-1 必须要求显式迁移", "旧 wire format")
    # 上一轮的 do-3 wire format 含 `reference_targets`；即使调用方手工塞回来也必须被拒。
    must_raise(lambda: S.DocumentOutline.from_dict(
        {**d, "reference_targets": []}),
        "手工塞回 reference_targets 的旧格式必须被拒绝", "未知字段")


# ---------------------------------------------------------------------------
# H. 通用规则与公司无关
# ---------------------------------------------------------------------------

_PAGE_NUMBER_LITERAL_RE = re.compile(r"page_number\s*[=!]=\s*\d")
_TABLE_INDEX_LITERAL_RE = re.compile(r"table_index_on_page\s*[=!]=\s*\d")
# 注意 `\b`：`table_index_on_page < 0` 这类合法下界检查不得被误判成"固定页码规则"。
_LEADING_DIGIT_PREFIX_RE = re.compile(r"\bpage\s*[<>]=?\s*\d")
# "公司无关"指**规则**与公司无关：生产代码不得把 company_id 与字面量比较。
_COMPANY_LITERAL_RE = re.compile(r"company_id\s*[=!]=\s*['\"]")


def _test_generic_and_company_agnostic():
    # 禁用词表由**本测试**定义，不读取生产代码提供的清单。
    forbidden = ("300750", "宁德时代", "CATL", "catl", "gold", "answer_key",
                 "fixed_page")
    check(len(forbidden) == 7, "禁用词表必须由测试自己定义")
    for fname in ("__init__.py", "versions.py", "schema.py", "canonical.py"):
        src = _read_module(fname)
        for token in forbidden:
            check(token not in src, f"{fname} 不得出现 {token!r}")
        check(not _PAGE_NUMBER_LITERAL_RE.search(src),
              f"{fname} 不得把 page_number 与固定数字比较")
        check(not _TABLE_INDEX_LITERAL_RE.search(src),
              f"{fname} 不得把 table_index_on_page 与固定数字比较")
        check(not _LEADING_DIGIT_PREFIX_RE.search(src),
              f"{fname} 不得把 page 与固定数字比较")
        check("宁德" not in src and "股份" not in src and "有限公司" not in src,
              f"{fname} 不得内联公司名")

    # 「公司无关」= 算法 / 规则不得随公司变化，**不是**"不同公司的存储身份必须相等"。
    # 上一轮把后者写成了断言（"不同 company_id 必须得到相同 PageLayout 身份"），
    # 那是错的：身份范围必须闭合到 company，否则两个公司的同名文档会共用一条
    # 不可变身份，无法区分。
    # 生产代码中不得出现按 company 分支的规则（这是真正的"公司无关"）。
    check(not _COMPANY_LITERAL_RE.search(
        "\n".join(_read_module(n) for n in
                  ("__init__.py", "versions.py", "schema.py", "canonical.py"))),
        "生产代码不得按 company_id 字面量分支（规则必须与公司无关）")

    pl_a = _make_page_layout(company_id="company-A")
    pl_b = _make_page_layout(company_id="company-B")
    check(pl_a.company_id != pl_b.company_id, "夹具必须使用不同 company_id")
    check(pl_a.page_layout_id != pl_b.page_layout_id,
          "不同公司的 PageLayout 必须是不同的不可变身份（身份范围闭合到公司）")
    check(pl_a.page_layout_locator != pl_b.page_layout_locator,
          "不同公司的 PageLayout 必须是不同的稳定定位")
    diff = {k for k in pl_a.to_dict() if pl_a.to_dict()[k] != pl_b.to_dict()[k]}
    check(diff == {"company_id", "page_layout_id", "page_layout_locator"},
          f"两个公司的 layout 只有公司派生身份可以不同，实际差异 {sorted(diff)}")

    # 同一公司 / 同一文档 / 同一内容 ⇒ 身份必须逐字节确定性地重现。
    check(_make_page_layout(company_id="company-A").page_layout_id
          == pl_a.page_layout_id, "同公司同内容必须得到同一 page_layout_id")
    check(_make_page_layout(company_id="company-A").page_layout_locator
          == pl_a.page_layout_locator, "同公司同内容必须得到同一 page_layout_locator")

    table_a = _make_table(pl_a)
    table_b = _make_table(pl_b)
    check(table_a.table_id != table_b.table_id,
          "不同公司的表格必须是不同的内容身份（身份经 page_layout 间接绑定公司）")
    check(table_a.content_fingerprint != table_b.content_fingerprint,
          "不同公司的表格内容指纹必须不同（page_layout_id 进入指纹）")
    check(_make_table(pl_a).table_id == table_a.table_id,
          "同公司同内容必须得到同一 table_id")
    span_a = _make_span(pl_a)
    span_b = _make_span(pl_b)
    check(span_a.span_id != span_b.span_id,
          "不同公司的 span 必须是不同的身份（document_outline_locator 间接绑定公司）")
    check(_make_span(pl_a).span_id == span_a.span_id,
          "同公司同内容必须得到同一 span_id")
    check(_make_profile().profile_id == _make_profile().profile_id,
          "profile 身份必须可复现（契约派生规则不随公司变化）")

    # 结构规则不得依赖固定页码：同一内容换到第 2 页必须只是另一个定位，规则不变。
    page2 = _mk_page(page_number=2)
    check(page2.page_number == 2, "第 2 页夹具必须可用")
    check(len(page2.lines) == _LINE_COUNT, "换页不得改变结构规则")


# ---------------------------------------------------------------------------


def main() -> dict:
    """逐个反例组执行；**任何一组崩溃都不得掩盖其余组**（否则反例集自身失真）。"""
    groups = (
        _test_mutation_matrix,
        _test_document_identity_and_versions,
        _test_layout_self_consistency,
        _test_outline_graph,
        _test_alignment_self_report,
        _test_span_closure,
        _test_table_provenance_and_grid,
        _test_reference_trust_boundary,
        _test_generic_and_company_agnostic,
    )
    for fn in groups:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 崩溃（该组后续反例未执行）："
                f"{type(e).__name__}: {e}")
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

"""TS1：`document_structure` 公共类型、两种身份、版本生效与不变量的确定性验收。

覆盖（全部为纯离线、无 LLM / 网络 / 数据库 / PDF 引擎）：

A. 类型与常量清单
   1. 18 个公共类型存在且为 `frozen=True` 的 dataclass；
   2. TS7A 的 5 个延迟类型**不存在**于任何 TS1 模块；
   3. 12 个强制版本常量集中定义且形状合法（`<prefix>-<n>`）；
   4. 版本字面量不散落：`schema.py` / `__init__.py` / `canonical.py` 内不出现任何
      版本字面量；
   5. `ALIGN_MIN` **已冻结为 `0.90`**（用户 + Codex，2026-09-17，单一文本对齐阈值）；
      `SPAN_CONFIDENCE_MIN` 仍为 `None`（未裁决，fail-closed）。

B. 两种身份（稳定定位 vs 不可变 revision）
   6. 每个版本化类型的 `*_locator` 与 `*_id` 形状不同、取值不同；
   7. 同一输入重复派生 → 同一身份；无 run_id / 时间 / 随机成分；
   8. 内容变化 → `*_id` 必变、`*_locator` 不变；
   9. 任一版本常量变化 → 该类型身份变化（逐类型验证）；
  10. 派生函数可被外部复算（不依赖实现自报）；
  11. `company_id` / `document_id` **必须**进入 layout 身份：同一 PDF 属于不同公司或
      不同文档时，存储身份必须不同（"公司无关"指算法/规则不随公司变化，**不是**指
      不同公司的存储身份必须相等）。上一轮"不同 company_id 必须得到相同 PageLayout
      身份"的主张是错的，已在本轮删除。

C. 严格序列化
  12. 全部类型 round-trip：`to_dict -> from_dict` 等于原对象；
  13. 未知字段被拒；缺必填字段被拒；类型错误被拒；非法枚举被拒；
  14. 旧 wire format 版本被显式识别并拒绝（不得静默当成新版本）；
  15. 未知版本被拒；
  16. `canonical_json` 与 `harness.topic_schema` 侧实现逐字节等价（两独立实现互证）。

D. 构造期不变量（fail-closed）
  17. PageLayout：页数/页序/尺寸/文本层/文档身份一致性；
  18. LayoutLine / LayoutSpan：行内字符区间、spans 升序不重叠、家具标记一致性；
  19. OutlineNode / DocumentOutline：层级、路径、ordinal、child_ids、父子对称、指纹；
  20. NavigationSynopsis：available/unavailable 与 snippets 的一致性；
  21. TextAlignmentRecord：coverage / residue_class / verdict 为确定性重算结果；
  22. OutlineSpan：身份字段、锚点、content_fingerprint、component Evidence 必须可对齐；
  23. TableObject：结构信号、列边界证据、行序、列数、单元格网格、续表自指；
  24. ReferenceEdge：端点类型按 kind 规定、is_resolved 与证据/原因码一致性。

E. 已裁决语义（防回归）
  25. `structure_class` 不是 authority verdict；金额权威只来自 FinancialSnapshot /
      FinancialFactPack；
  26. 后代只作候选；TS1 类型无任何 `superseded*` 字段；
  27. 表格层无业务解释字段（`TableRow` 无 `semantics`）；
  28. 表结构信号词表复用既有原语，不另造同义名；
  29. 无公司特例 token；TS1 模块不 import 数据库 / 网络 / LLM / PDF 引擎 / 子进程。

**反例与对抗性用例**（变异矩阵、越界几何、悬空引用、自报覆盖率等）在
`evals/test_tree_structure_adversarial.py`；本文件只保证正常路径与既有轮次已覆盖的
性质不回退。本文件的断言只读取被测对象的公开行为（是否抛错、身份是否变化、能否
往返），不读取实现自报的任何"结论字段"作为通过依据。
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import re
from dataclasses import replace

import document_structure
from harness.table_structure import table_body_signals

from document_structure import schema as S
from document_structure import span_policy as sp
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, canonical_json

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent
_TS1_MODULES = ("__init__.py", "versions.py", "schema.py", "canonical.py")


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


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_module(name: str) -> str:
    return (_PKG_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 固定装置（全部满足不变量；不使用任何公司特例字段 / 固定业务页码或表号）
# ---------------------------------------------------------------------------

_DOC_SHA = _sha("pdf-bytes-placeholder")
_CONTRACT_SHA = _sha("contract-placeholder")
_DOC_VERSION = "sha256-" + _DOC_SHA[:16]
_DOC_ID = "doc-0001"
_COMPANY_ID = "company-A"
_EVIDENCE_SET_VERSION = "set-0123456789ab"
_BLOCK_ID = "ev-block-0001"
_BLOCK_LEN = 9
_X0, _Y0, _H = 50.0, 100.0, 12.0
_LINE_COUNT = 7


def _line_text(i: int) -> str:
    return f"第{i}行正文样例文本"


# TS1.3：正文引用所在的那**一行真实版式**承载两处互指文字。occurrence 的字符区间
# 自本轮起是**行内**区间（`(page_number, line_index)` 精确定位一条真实 `LayoutLine`），
# 因此 span 的 `normalized_text` 必须与它自己的 `layout_line_refs` 逐字符一致——
# 旧夹具把 "详见" 当成 span 文本的前缀、与真实行文本不一致，在新语义下只能作为
# 反例，无法再作为正例。
_REF_SPAN_LINE = 5
_REF_TEXT_1 = "详见" + _line_text(_REF_SPAN_LINE)
_REF_TEXT_1_START = 0
_REF_TEXT_2_START = len(_REF_TEXT_1) + 2   # 中间隔 "；另" 两个字符
_REF_SPAN_TEXT = _REF_TEXT_1 + "；另" + _REF_TEXT_1


def _mk_line(line_index, text, y0, cuts):
    spans = []
    for (a, b, font, size, bold) in cuts:
        spans.append(S.LayoutSpan(
            text=text[a:b], bbox=(_X0 + a * 6.0, y0, _X0 + b * 6.0, y0 + _H),
            font=font, size=size, is_bold=bold, char_start=a, char_end=b))
    return S.LayoutLine(
        line_index=line_index, bbox=(_X0, y0, _X0 + len(text) * 6.0, y0 + _H),
        spans=tuple(spans), text=text, is_furniture=False, furniture_kind=None,
        reading_order=line_index, column_index=0)


def _mk_page(page_number=1, line_count=_LINE_COUNT):
    lines = []
    for i in range(line_count):
        # 第 `_REF_SPAN_LINE` 行承载真实的互指文本（两处「详见」），见上方说明。
        t = _REF_SPAN_TEXT if i == _REF_SPAN_LINE else _line_text(i)
        lines.append(_mk_line(i, t, _Y0 + i * _H, [(0, len(t), "SimSun", 10.5, False)]))
    return S.LayoutPage(page_number=page_number, width=600.0, height=800.0, rotation=0,
                        lines=tuple(lines), has_text_layer=True)


def _make_page_layout(company_id=_COMPANY_ID, document_id=_DOC_ID):
    return S.PageLayout.create(
        document_id=document_id, document_version=_DOC_VERSION, company_id=company_id,
        source_file_sha256=_DOC_SHA, pages=(_mk_page(),))


def _anchor(line_index, chars=8):
    y = _Y0 + line_index * _H
    return (1, line_index, (_X0, y, _X0 + chars * 6.0, y + _H))


def _outline_locator(pl, document_id=None):
    """outline 定位身份：默认取 layout 自己的 document_id（身份必须闭合到文档）。"""
    return S.derive_document_outline_locator(
        page_layout_id=pl.page_layout_id,
        document_id=pl.document_id if document_id is None else document_id,
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)


# 未解析 cross_reference 边引用的一段**真实来源文本**：第 5 行第 0 字符起的
# "详见第5行正文样例文本"。TS1.4 起**未解析边**的 occurrence 也必须落到真实
# `LayoutLine` 上，因此来源类型只能是本层有真实原文载体的 `span:`（节点标题行
# 才可能承载 `node:` 来源；本夹具的节点锚点都落在普通正文行上），字符区间必须是
# 第 5 行原文的行内切片。
_OPEN_REF_TEXT = _REF_TEXT_1
_OPEN_REF_MARKER = "详见"
_OPEN_REF_TARGET = _line_text(_REF_SPAN_LINE)


def _make_occurrence(span_id, **kw):
    """一条绑定到真实正文 span 的 occurrence（落在第 5 行第 0 字符起的行内区间）。"""
    base = dict(source_ref=f"span:{span_id}", page_number=1,
                line_index=_REF_SPAN_LINE, char_start=_REF_TEXT_1_START,
                char_end=len(_OPEN_REF_TEXT), occurrence_index=0,
                reference_marker=_OPEN_REF_MARKER, reference_kind="cross_reference",
                declared_target=_OPEN_REF_TARGET, normalized_text=_OPEN_REF_TEXT)
    base.update(kw)
    return S.ReferenceOccurrence(**base)


def _make_alignment(pl=None, block_index=0):
    pl = pl or _make_page_layout()
    return S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=block_index, evidence_block_id=_BLOCK_ID,
        block_char_length=_BLOCK_LEN, residue=(),
        char_map=((0, _BLOCK_LEN, 1, 0, 0, 0),))


def _make_refusal(pl=None):
    """TS3 §五 的**拒绝终态**夹具：精确 0.8998 < 0.90，而展示值量化后为 0.9。

    这正是"先四舍五入再判阈值"会出错的那个块：`als-1` 只能表示展示值 0.9，
    于是产物里会出现"显示 0.900 却按冻结规则不可引用"的自相矛盾条目。新版本把
    它的正式终态定为一个 typed 的 `AlignmentRefusalRecord`。
    """
    pl = pl or _make_page_layout()
    return S.AlignmentRefusalRecord.create(
        page_layout_id=pl.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=0, evidence_block_id=_BLOCK_ID, block_char_length=10000,
        matched_chars=8998, residue=((8998, 10000, "column_reorder"),),
        char_map=((0, 8998, 1, 0, 0, 0),),
        refusal_reason="quantization_boundary_refused")


def _make_unassigned_span(pl, oloc):
    return S.OutlineSpan.create(
        document_outline_locator=oloc, node_id=None, document_id=pl.document_id,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        role="unassigned", start_anchor=_anchor(6), end_anchor=_anchor(6),
        normalized_text=_line_text(6), layout_line_refs=((1, 6),),
        unassigned_reason="below_last_heading", confidence=1.0)


def _nodes(pl, oloc):
    """标题树节点 (a, a1, b)：a1 是 a 的子节点，b 与 a 同级。"""
    a = S.OutlineNode.create(
        document_outline_locator=oloc, parent_id=None, title="第一节标题",
        title_normalized="第一节标题", structural_path=("第一节标题",),
        source_anchor=_anchor(0), ordinal=0)
    a1 = S.OutlineNode.create(
        document_outline_locator=oloc, parent_id=a.node_id, title="第一节第一小节",
        title_normalized="第一节第一小节",
        structural_path=("第一节标题", "第一节第一小节"),
        source_anchor=_anchor(2), ordinal=0)
    b = S.OutlineNode.create(
        document_outline_locator=oloc, parent_id=None, title="第二节标题",
        title_normalized="第二节标题", structural_path=("第二节标题",),
        source_anchor=_anchor(4), ordinal=1)
    return replace(a, child_ids=(a1.node_id,)), a1, b


def _outline_ctx(pl, ref_span=None):
    """带真实 `PageLayout`（必要时再带来源 span）的核验上下文。

    TS1.4：含真实 occurrence 的边（**无论已解析还是未解析**）都必须在真实版式上
    核验来源，因此这类 outline 的构造 / 反序列化必须显式交出本上下文。
    """
    spans = () if ref_span is None else (ref_span,)
    return S.ReferenceValidationContext(spans=spans, layout=pl)


def _make_outline(pl=None):
    pl = pl or _make_page_layout()
    oloc = _outline_locator(pl)
    a, a1, b = _nodes(pl, oloc)
    ref_span = _make_ref_span(pl)
    edge_ok = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{a1.node_id}", edge_kind="parent_child",
        resolution_evidence="标题编号与缩进一致")
    edge_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=None, edge_kind="cross_reference",
        resolution_evidence=None, reason_code="no_textual_evidence",
        occurrence=_make_occurrence(ref_span.span_id))
    return S.DocumentOutline.create(
        document_id=pl.document_id, document_version=_DOC_VERSION,
        page_layout_id=pl.page_layout_id, nodes=(a, a1, b),
        edges=(edge_ok, edge_open), unassigned=(_make_unassigned_span(pl, oloc),),
        candidate_sources=("body_numbering",),
        reference_context=_outline_ctx(pl, ref_span))


def _make_span(pl=None):
    pl = pl or _make_page_layout()
    outline = _make_outline(pl)
    node = outline.nodes[1]
    al = _make_alignment(pl)
    return S.OutlineSpan.create(
        document_outline_locator=_outline_locator(pl), node_id=node.node_id,
        document_id=pl.document_id, document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
        start_anchor=_anchor(2), end_anchor=_anchor(3),
        normalized_text=_line_text(2) + _line_text(3),
        layout_line_refs=((1, 2), (1, 3)),
        component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN),),
        alignment_ids=(al.alignment_id,), confidence=1.0)


# TS1.2：**真实正文来源** span。引用文字必须真的出现在它的原文里 —— resolved 边的
# occurrence 只能落在这样的真实文本载体上，不得由只含标题的 OutlineNode 虚构正文
# occurrence。第 5 行不在其它夹具的占用范围内（节点 0/2/4、正文 2–3、未归属 6）。
# （`_REF_SPAN_*` 常量与第 5 行的真实行文本的定义见文件上方 `_mk_page` 之前。）


def _make_ref_span(pl=None, **overrides):
    """node a1 下的一段真实正文 span（第 5 行，含两处「详见」）。

    只依赖 `_nodes` / `_outline_locator`（**不**调用 `_make_outline`）：后者需要本
    span 作为来源对象的核验上下文，互相调用会成环。
    """
    pl = pl or _make_page_layout()
    nodes = _nodes(pl, _outline_locator(pl))
    kwargs = dict(
        document_outline_locator=_outline_locator(pl),
        node_id=nodes[1].node_id, document_id=pl.document_id,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        role="body", start_anchor=_anchor(_REF_SPAN_LINE),
        end_anchor=_anchor(_REF_SPAN_LINE), normalized_text=_REF_SPAN_TEXT,
        layout_line_refs=((1, _REF_SPAN_LINE),), confidence=1.0)
    kwargs.update(overrides)
    return S.OutlineSpan.create(**kwargs)


def _make_synopsis():
    span_id = "os-0123456789abcdef"
    return S.NavigationSynopsis.available(node_id="on-0123456789abcdef", snippets=(
        S.SynopsisSnippet(span_id=span_id, snippet_index=0, char_start=0,
                          char_end=len(_line_text(2)), text=_line_text(2)),))


def _make_profile():
    entry = S.AspectNavigationEntry(
        aspect_id="asp-1", question_id="q-1", topic_id="t-1",
        content_role="paragraph_and_table", display_tier="required_body",
        nav_keys=("营业收入",), parent_keys=("主营业务",),
        subject_head="主营业务收入构成",
        expected_forms=("表格",),
        derivation=("contract.aspects[asp-1].nav_key", "rules/anp-1#key-derivation"))
    return S.AspectNavigationProfile.create(
        contract_version="v2", contract_fingerprint=_CONTRACT_SHA, entries=(entry,))


def _table_rows():
    return (S.TableRow(row_index=0, kind="header", cells=("项目", "金额"), label=None),
            S.TableRow(row_index=1, kind="body", cells=("营业收入", "100"), label=None),
            S.TableRow(row_index=2, kind="body", cells=("净利润", "20"), label=None))


def _make_table(pl=None, rows=None, page_number=1, **overrides):
    """单页物理表格片段（每个 TableObject 只代表一页上的物理表格）。

    `page_number` 同时决定单元格的来源页：单页片段不存在跨页单元格。
    """
    pl = pl or _make_page_layout()
    outline = _make_outline(pl)
    span = _make_span(pl)
    al = _make_alignment(pl)
    rows = rows or _table_rows()
    grid = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row.cells):
            y = 300.0 + r * _H
            grid.append(S.TableCell(
                row=r, column=c, rowspan=1, colspan=1, text=text,
                bbox=(100.0 + c * 80.0, y, 180.0 + c * 80.0, y + _H),
                source_locator=(page_number, r, 0)))
    kwargs = dict(
        page_layout_id=pl.page_layout_id, document_outline_id=outline.outline_id,
        document_outline_locator=outline.outline_locator,
        node_id=outline.nodes[1].node_id, document_id=pl.document_id,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=page_number, table_index_on_page=0,
        table_bbox=(100.0, 300.0, 260.0, 300.0 + len(rows) * _H),
        title="示例表题", title_source="caption_line", unit="万元",
        structure_class="ordinary_business_table",
        structure_evidence=("columnar", "data"), column_count=2,
        header_rows=(rows[0],), body_rows=rows[1:], cell_grid=tuple(grid),
        component_span_ids=(span.span_id,), alignment_ids=(al.alignment_id,),
        component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN),))
    kwargs.update(overrides)
    return S.TableObject.create(**kwargs)


#: `TableObjectV4` 的**派生**字段（`create()` 自算，调用方不得提供）。
_TABLE_V4_DERIVED = ("table_locator", "table_id", "content_fingerprint",
                     "structure_fingerprint", "provenance_fingerprint")


def _make_table_v4():
    """当前 wire 版本的 `TableObjectV4`（`to-4`）最小合法夹具。

    TS5 之后 `TableObject` 这个名字指的是**历史** `to-3` reader；当前记录类型是
    `TableObjectV4`。版本登记表（`V.VERSIONED_OBJECT_SCHEMA_FIELDS`）里
    `TableObject` 这一项说的是当前版本常量 `TABLE_SCHEMA_VERSION`，因此夹具必须是
    真正的 `to-4` 对象——拿 `to-3` 顶替会让"当前版本必须分类为 current"这条性质
    测的是别的东西。

    本夹具只经生产类型与生产 `create()` 进入（无旁路）：2×2 的有表头网格，
    一格一条真实来源片段，来源片段自身也由 `TableCellSourceRef.create()` 派生身份。
    它是**合成**的，只用于 wire/版本性质，不主张任何真实文档内容。
    """
    sha = "a" * 64
    texts = (("项目", "金额"), ("营业收入", "100"))
    cells: list = []
    for r, row in enumerate(texts):
        for c, text in enumerate(row):
            comp = f"synthetic-component-{r}-{c}"
            bbox = (100.0 + c * 80.0, 300.0 + r * 20.0,
                    180.0 + c * 80.0, 320.0 + r * 20.0)
            ref = TS.TableCellSourceRef.create(
                interval=TS.TableSourceInterval(
                    page_number=1, line_index=r, span_index=c,
                    span_char_range=(0, len(text)), bbox=bbox),
                component_id=comp, component_locator=comp,
                evidence_block_id="synthetic-block",
                evidence_char_range=(0, len(text)),
                terminal_kind="alignment",
                terminal_id=f"synthetic-align-{r}-{c}",
                terminal_locator=f"synthetic-align-{r}-{c}",
                terminal_schema_version=V.VERSION_CONSTANTS["ALIGN_SCHEMA_VERSION"],
                verdict="aligned")
            cells.append(TS.TableCellV4.create(
                row=r, column=c, rowspan=1, colspan=1, bbox=bbox,
                blocks=(TS.TableCellBlock.create(
                    block_index=0, role="line", text=text,
                    source_ref_ids=(ref.source_ref_id,)),),
                source_refs=(ref,)))
    rows = tuple(
        TS.TableRowV4(
            row_index=r, role="header" if r == 0 else "body", label=None,
            is_repeated_header=False,
            cells=tuple(cell for cell in cells if cell.row == r))
        for r in range(len(texts)))
    boundary = "synthetic-boundary-1"
    owner = TS.TableOwnerRef(
        owner_kind="outline_node",
        node_id="synthetic-node-1", outline_locator="synthetic-node-1",
        source_boundary_kind="ts4_body_disposition",
        source_boundary_locator=boundary, source_boundary_id=boundary,
        source_boundary_start_page=1, source_boundary_end_page=1,
        unassigned_ref_kind=None, unassigned_ref_locator=None,
        unassigned_ref_id=None)
    return TS.TableObjectV4.create(
        schema_version=V.VERSION_CONSTANTS["TABLE_SCHEMA_VERSION"],
        table_builder_version=V.VERSION_CONSTANTS["TABLE_BUILDER_VERSION"],
        cell_schema_version=V.VERSION_CONSTANTS["TABLE_CELL_SCHEMA_VERSION"],
        geometry_version=V.VERSION_CONSTANTS["TABLE_GEOMETRY_VERSION"],
        geometry_settings_fingerprint=sha,
        classification_profile_version=V.VERSION_CONSTANTS[
            "TABLE_CLASSIFICATION_PROFILE_VERSION"],
        classification_profile_file_fingerprint=sha,
        classification_profile_content_fingerprint=sha,
        cell_block_profile_version=V.VERSION_CONSTANTS[
            "TABLE_CELL_BLOCK_PROFILE_VERSION"],
        cell_block_profile_file_fingerprint=sha,
        cell_block_profile_content_fingerprint=sha,
        document_id="synthetic-doc", document_version=_DOC_VERSION,
        evidence_set_version=_EVIDENCE_SET_VERSION,
        page_layout_id="synthetic-layout", outline_id="synthetic-outline",
        verified_span_snapshot_id="synthetic-vss",
        upstream_dependency_fingerprint=sha,
        page_number=1, page_bbox=(0.0, 0.0, 600.0, 800.0),
        source_order_index=0, owner=owner,
        structure_kind="headered_grid",
        structure_class="ordinary_business_table",
        structure_state="complete", structure_state_reason=None,
        header_absence_reason=None, missing_or_uncertain_fields=(),
        column_count=2, title_blocks=(), unit_text=None, unit_blocks=(),
        note_blocks=(), rows=rows,
        source_refs=tuple(ref for cell in cells for ref in cell.source_refs),
        continuation_anchor_locator=None, continuation_candidate_locators=())


def _versioned_instances(pl):
    outline = _make_outline(pl)
    return {
        "PageLayout": pl,
        "DocumentOutline": outline,
        "OutlineSpan": _make_span(pl),
        # 登记表里的 `TableObject` 指当前版本 `to-4`，因此这里必须是
        # `TableObjectV4`（历史 `to-3` 由 `_make_table` 单独覆盖）。
        "TableObject": _make_table_v4(),
        "NavigationSynopsis": _make_synopsis(),
        "TextAlignmentRecord": _make_alignment(pl),
        "AlignmentRefusalRecord": _make_refusal(pl),
        "AspectNavigationProfile": _make_profile(),
        "ReferenceEdge": outline.edges[0],
    }


def _all_fixtures():
    pl = _make_page_layout()
    outline = _make_outline(pl)
    rows = _table_rows()
    return (pl.pages[0].lines[0].spans[0], pl.pages[0].lines[0], pl.pages[0], pl,
            outline.nodes[0], outline, _make_synopsis().snippets[0], _make_synopsis(),
            _make_profile().entries[0], _make_profile(), _make_alignment(pl),
            _make_span(pl), rows[0], _make_table(pl).cell_grid[0], _make_table(pl),
            outline.edges[0])


# ---------------------------------------------------------------------------
# A. 类型与常量
# ---------------------------------------------------------------------------


def _test_types_and_constants():
    names = [c.__name__ for c in S.PUBLIC_TYPES]
    # 20 → 21：TS3 §五 新增对齐**拒绝终态** `AlignmentRefusalRecord`（失败也必须
    # 是一个 typed、版本化、可读回的正式对象，不能表示为"记录缺失"）。
    check(len(S.PUBLIC_TYPES) == 21, f"公共类型应为 21 个，得到 {len(names)}")
    check("AlignmentRefusalRecord" in names,
          "缺少 TS3 §五 新增的对齐拒绝终态公共类型")
    for required in ("ReferenceOccurrence", "ReferenceValidationContext",
                     "VerifiedReferences"):
        check(required in names, f"缺少 TS1.2 引用绑定新增公共类型 {required}")
    # TS1.3 §四.3/§四.4：目录项的**真实来源身份**必须是类型化对象（由真实
    # LayoutLine 重算），而不是"只包字符串的假 Toc 对象"或字符串注册表。
    check("TocSource" in names, "缺少 TS1.3 新增的真实 TOC 来源对象 TocSource")
    check("TocSource" in V.RUNTIME_ONLY_TYPE_NAMES,
          "TocSource 是运行时核验输入，不得进入任何 wire format 版本登记")
    # P1-2：字符串清单注册表必须**不存在**——它不是对象存在证明。
    check(not hasattr(S, "ReferenceTargetRegistry"),
          "ReferenceTargetRegistry 字符串清单注册表必须已删除")
    check(not hasattr(S, "REFERENCE_TARGET_SETS"),
          "REFERENCE_TARGET_SETS 字符串清单必须已删除")
    check(not hasattr(S.DocumentOutline, "reference_targets"),
          "DocumentOutline 不得再持久化 reference_targets")
    check(not hasattr(S.DocumentOutline, "resolved_edges"),
          "DocumentOutline.resolved_edges 自证入口必须已删除")
    check(len(set(names)) == len(names), "公共类型名不得重复")
    for required in ("PageLayout", "DocumentOutline", "OutlineSpan", "TableObject",
                     "TableCell", "ReferenceEdge", "TextAlignmentRecord",
                     "NavigationSynopsis", "AspectNavigationProfile"):
        check(required in names, f"缺少公共类型 {required}")
    for cls in S.PUBLIC_TYPES:
        check(dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen,
              f"{cls.__name__} 必须是 frozen dataclass")

    pl = _make_page_layout()
    instances = _versioned_instances(pl)
    check(set(instances) == set(V.VERSIONED_OBJECT_SCHEMA_FIELDS),
          "版本化类型夹具必须与登记表一致")
    for typename, (field, constant) in V.VERSIONED_OBJECT_SCHEMA_FIELDS.items():
        obj = instances.get(typename)
        if obj is None:
            check(False, f"缺少 {typename} 夹具")
            continue
        check(field in type(obj).__dataclass_fields__, f"{typename} 必须有 {field} 字段")
        check(getattr(obj, field) == V.VERSION_CONSTANTS[constant],
              f"{typename}.{field} 必须等于 {constant}")
        check(V.classify_schema_version(constant, getattr(obj, field)) == "current",
              f"{typename} 的版本必须被分类为 current")

    # 子结构类型不得自带版本（同一事实不得两处版本）。
    for typename in V.SUBSTRUCTURE_TYPE_NAMES:
        cls = next((c for c in S.PUBLIC_TYPES if c.__name__ == typename), None)
        if not check(cls is not None, f"子结构类型 {typename} 必须存在"):
            continue
        check("schema_version" not in cls.__dataclass_fields__,
              f"子结构类型 {typename} 不得自带 schema_version")

    # 8 → 9：TS3 §五 新增 `ALIGN_REFUSAL_SCHEMA_VERSION`（`AlignmentRefusalRecord` 的
    # wire 版本）。登记表与常量表必须同步增长，否则"新增版本化类型却忘了登记"。
    check(len(V.SCHEMA_VERSION_CONSTANT_NAMES) == 9, "schema 版本常量应为 9 个")
    check(len(V.VERSIONED_OBJECT_SCHEMA_FIELDS) == 9, "版本化类型应为 9 个")
    for name in V.MANDATED_VERSION_CONSTANT_NAMES:
        check(name in V.VERSION_CONSTANTS, f"缺失指令强制版本常量 {name}")

    sc = S.self_check()
    check(sc["deferred_to_ts7a_present"] == [], "TS7A 延迟类型不得提前实现")
    check(sc["versioned_types_missing"] == [], "登记的类型必须都有 schema_version 字段")
    check(sc["versioned_types_undeclared"] == [], "有 schema_version 的类型必须都登记")
    check(sc["superseded_version_fields"] == [], "TS1 类型不得含 superseded 字段")
    check(V.ALIGN_MIN == 0.90 and isinstance(V.ALIGN_MIN, float),
          "ALIGN_MIN 必须是已冻结的单一文本对齐阈值 0.90（用户 + Codex 2026-09-17）")
    # A/B 真值表口径：阶段由 `SPAN_CONFIDENCE_MIN` **是否裁决**派生，测试只校验
    # 三方（常量 / self_check / 真值表）互相一致，不写死当前处在哪一阶段。
    gate = sp.ab_gate_truth_table()
    check(gate["stage"] == ("threshold_enabled"
                            if V.SPAN_CONFIDENCE_MIN is not None
                            else "distribution_only"),
          "A/B 真值表阶段必须由 SPAN_CONFIDENCE_MIN 的实际取值派生")
    check(sc["align_min_decided"] is True
          and sc["span_confidence_min_decided"] is (V.SPAN_CONFIDENCE_MIN is not None),
          "ALIGN_MIN 恒为已裁决；SPAN_CONFIDENCE_MIN 的裁决状态必须与常量一致")
    check(sc["align_min"] == V.ALIGN_MIN
          and sc["span_confidence_min"] == V.SPAN_CONFIDENCE_MIN,
          "self_check 必须自报与常量一致的阈值")
    check(gate["completion_enabled"] is gate["threshold_decided"]
          and gate["set_complete_supported"] is gate["threshold_decided"],
          "完成资格 / set_complete 支持必须与阈值裁决同源")
    vs = V.self_check()
    check(vs["missing_mandated"] == [] and vs["malformed_literals"] == [],
          "版本常量清单必须完整且字面量形状合法")

    # 枚举白名单的非空与去重（空词表会让"封闭校验"退化为空操作）。
    for label, values in (("SPAN_ROLES", S.SPAN_ROLES), ("ROW_KINDS", S.ROW_KINDS),
                          ("TITLE_SOURCES", S.TITLE_SOURCES),
                          ("FURNITURE_KINDS", S.FURNITURE_KINDS),
                          ("ALIGNMENT_VERDICTS", S.ALIGNMENT_VERDICTS),
                          ("UNASSIGNED_REASONS", S.UNASSIGNED_REASONS),
                          ("UNRESOLVED_EDGE_REASONS", S.UNRESOLVED_EDGE_REASONS),
                          ("STRUCTURE_CLASSES", S.STRUCTURE_CLASSES),
                          ("REF_KINDS", S.REF_KINDS),
                          ("EDGE_KINDS", S.EDGE_KINDS),
                          ("CANDIDATE_SOURCES", S.CANDIDATE_SOURCES),
                          ("TABLE_STRUCTURE_SIGNALS", S.TABLE_STRUCTURE_SIGNALS)):
        check(len(values) >= 2, f"{label} 词表过短，封闭校验会退化")
        check(len(set(values)) == len(values), f"{label} 词表不得有重复项")
    check(len(S.EDGE_ENDPOINT_KINDS) == len(S.EDGE_KINDS),
          "每种边类型都必须规定端点类型")
    for kind, (from_kinds, to_kinds) in S.EDGE_ENDPOINT_KINDS.items():
        check(kind in S.EDGE_KINDS, f"{kind} 必须在 EDGE_KINDS 内")
        for k in from_kinds + to_kinds:
            check(k in S.REF_KINDS, f"{kind} 的端点类型 {k} 必须在 REF_KINDS 内")
    # 子结构类型与运行时类型不得重叠，且运行时类型不得携带任何 wire format 版本。
    check(set(V.SUBSTRUCTURE_TYPE_NAMES) & set(V.RUNTIME_ONLY_TYPE_NAMES) == set(),
          "子结构类型与运行时类型不得重叠")
    for typename in V.RUNTIME_ONLY_TYPE_NAMES:
        cls = next((c for c in S.PUBLIC_TYPES if c.__name__ == typename), None)
        if not check(cls is not None, f"运行时类型 {typename} 必须存在"):
            continue
        check("schema_version" not in cls.__dataclass_fields__,
              f"运行时类型 {typename} 不得携带 wire format 版本")


# ---------------------------------------------------------------------------
# B. 两种身份
# ---------------------------------------------------------------------------


def _test_identity():
    pl = _make_page_layout()
    check(pl.page_layout_locator.startswith("loc-pl-"), "layout locator 形状错误")
    check(pl.page_layout_id.startswith("pl-"), "layout revision id 形状错误")
    check(pl.page_layout_id != pl.page_layout_locator, "两种身份必须不同")
    check(_make_page_layout().page_layout_id == pl.page_layout_id,
          "同一输入必须产生同一 page_layout_id")
    check(_make_page_layout().page_layout_locator == pl.page_layout_locator,
          "同一输入必须产生同一 page_layout_locator")

    # 内容变化 → revision id 必变；locator 不受页面内容影响。
    empty_page = S.LayoutPage(page_number=1, width=600.0, height=800.0, rotation=0,
                              lines=(), has_text_layer=False)
    pl_other = S.PageLayout.create(
        document_id=_DOC_ID, document_version=_DOC_VERSION, company_id=_COMPANY_ID,
        source_file_sha256=_DOC_SHA, pages=(empty_page,))
    check(pl_other.page_layout_id != pl.page_layout_id,
          "页面内容变化必须改变 page_layout_id")
    check(pl_other.page_layout_locator == pl.page_layout_locator,
          "页内容变化不得改变定位身份")

    # 版本变化 → 身份变化。
    _pl_base = dict(company_id=_COMPANY_ID, document_id=_DOC_ID,
                    document_version=_DOC_VERSION, engine=V.LAYOUT_ENGINE,
                    engine_version=V.LAYOUT_ENGINE_VERSION,
                    schema_version=V.LAYOUT_SCHEMA_VERSION,
                    normalization_version=V.NORMALIZATION_VERSION)
    bumped = S.derive_page_layout_locator(**{**_pl_base, "engine_version": "pymupdf-99"})
    check(bumped != pl.page_layout_locator, "引擎版本变化必须改变定位身份")
    bumped2 = S.derive_page_layout_locator(
        **{**_pl_base, "normalization_version": "norm-99"})
    check(bumped2 != pl.page_layout_locator, "归一化版本变化必须改变定位身份")

    # company_id / document_id **必须**进入 layout 身份（TS1.1 P1-A 收口）：
    # 同一份 PDF 属于不同公司 / 不同文档，是不同的存储事实，身份必须不同。
    pl_b = _make_page_layout(company_id="company-B")
    check(pl_b.company_id != pl.company_id, "夹具必须使用不同的 company_id")
    check(pl_b.page_layout_id != pl.page_layout_id,
          "company_id 必须进入 page_layout_id（不同公司不得共享同一存储身份）")
    check(pl_b.page_layout_locator != pl.page_layout_locator,
          "company_id 必须进入 page_layout_locator")
    pl_d = _make_page_layout(document_id="doc-9999")
    check(pl_d.document_id != pl.document_id, "夹具必须使用不同的 document_id")
    check(pl_d.page_layout_id != pl.page_layout_id,
          "document_id 必须进入 page_layout_id")
    check(pl_d.page_layout_locator != pl.page_layout_locator,
          "document_id 必须进入 page_layout_locator")
    # 上游身份变了，下游 outline 必须随之变化（经 page_layout_id 间接绑定公司）。
    check(_make_outline(pl_b).outline_id != _make_outline(pl).outline_id,
          "不同公司必须得到不同 outline 身份（经 page_layout_id 间接绑定）")

    # 身份字段被篡改时必须被构造期重算比对拦下。
    raises(lambda: replace(pl, page_count=2), SchemaValidationError, "page_count",
           "篡改 page_count 必须被拒绝")
    raises(lambda: replace(pl, page_layout_id="pl-ffffffffffffffff"),
           SchemaValidationError, "page_layout_id", "伪造 page_layout_id 必须被拒绝")
    raises(lambda: replace(pl, page_layout_locator="loc-pl-ffffffffffffffff"),
           SchemaValidationError, "page_layout_locator",
           "伪造 page_layout_locator 必须被拒绝")

    outline = _make_outline(pl)
    check(outline.outline_id.startswith("do-"), "outline revision id 形状错误")
    check(outline.outline_locator.startswith("loc-do-"), "outline locator 形状错误")
    check(outline.outline_id == "do-" + outline.content_fingerprint[:16],
          "outline_id 必须等于内容指纹前 16 位")
    check(len(outline.content_fingerprint) == 64, "内容指纹必须是完整 sha256")

    span = _make_span(pl)
    check(span.span_locator.startswith("loc-os-") and span.span_id.startswith("os-"),
          "span 两种身份形状必须区分")
    table = _make_table(pl)
    check(table.table_locator.startswith("loc-to-") and table.table_id.startswith("to-"),
          "table 两种身份形状必须区分")
    al = _make_alignment(pl)
    check(al.alignment_locator.startswith("loc-al-") and al.alignment_id.startswith("al-"),
          "alignment 两种身份形状必须区分")
    syn = _make_synopsis()
    check(syn.synopsis_locator.startswith("loc-ns-") and syn.synopsis_id.startswith("ns-"),
          "synopsis 两种身份形状必须区分")
    prof = _make_profile()
    check(prof.profile_locator.startswith("loc-anp-")
          and prof.profile_id.startswith("anp-"), "profile 两种身份形状必须区分")
    check(outline.edges[0].edge_locator.startswith("loc-re-")
          and outline.edges[0].edge_id.startswith("re-"), "edge 两种身份形状必须区分")

    # 每一种身份都必须能被外部独立复算。
    check(S.derive_page_layout_id(
        page_layout_locator=pl.page_layout_locator,
        source_file_sha256=pl.source_file_sha256,
        pages=pl.pages) == pl.page_layout_id, "derive_page_layout_id 必须可外部复算")
    check(S.derive_node_id(
        document_outline_locator=outline.nodes[0].document_outline_locator,
        structural_path=outline.nodes[0].structural_path,
        source_anchor=outline.nodes[0].source_anchor,
        title=outline.nodes[0].title) == outline.nodes[0].node_id,
        "derive_node_id 必须可外部复算")
    check(S.derive_document_outline_id(
        outline_locator=outline.outline_locator,
        document_version=outline.document_version, nodes=outline.nodes,
        edges=outline.edges, unassigned=outline.unassigned,
        candidate_sources=outline.candidate_sources) == outline.outline_id,
        "derive_document_outline_id 必须可外部复算")
    check(S.derive_alignment_id(
        alignment_locator=al.alignment_locator, schema_version=al.schema_version,
        evidence_block_id=al.evidence_block_id, block_char_length=al.block_char_length,
        verdict=al.verdict, coverage=al.coverage, residue_class=al.residue_class,
        residue=al.residue, char_map=al.char_map,
        matched_chars=al.matched_chars) == al.alignment_id,
        "derive_alignment_id 必须可外部复算（含精确分子）")
    check(S.derive_span_id(
        span_locator=span.span_locator, schema_version=span.schema_version,
        document_id=span.document_id, document_version=span.document_version,
        node_id=span.node_id, role=span.role,
        unassigned_reason=span.unassigned_reason, page_range=span.page_range,
        char_range=span.char_range, layout_line_refs=span.layout_line_refs,
        component_evidence_refs=span.component_evidence_refs,
        alignment_ids=span.alignment_ids, is_fallback=span.is_fallback,
        fallback_derivation=span.fallback_derivation,
        is_cross_heading=span.is_cross_heading, confidence=span.confidence,
        normalized_text=span.normalized_text) == span.span_id,
        "derive_span_id 必须可外部复算")
    check(S.derive_table_locator(
        page_layout_id=table.page_layout_id, page_number=table.page_number,
        table_index_on_page=table.table_index_on_page, table_bbox=table.table_bbox,
        table_builder_version=table.table_builder_version) == table.table_locator,
        "derive_table_locator 必须可外部复算")
    for _edge in (outline.edges[0], outline.edges[1]):
        check(S.derive_reference_edge_locator(
            document_outline_locator=_edge.document_outline_locator,
            from_ref=_edge.from_ref, to_ref=_edge.to_ref, edge_kind=_edge.edge_kind,
            occurrence=_edge.occurrence) == _edge.edge_locator,
            f"derive_reference_edge_locator 必须可外部复算（{_edge.edge_kind}）")
    check(S.derive_synopsis_locator(
        node_id=syn.node_id, synopsis_version=syn.synopsis_version,
        source_span_ids=syn.source_span_ids) == syn.synopsis_locator,
        "derive_synopsis_locator 必须可外部复算")
    check(S.derive_profile_locator(
        contract_version=prof.contract_version,
        contract_fingerprint=prof.contract_fingerprint,
        rule_version=prof.rule_version) == prof.profile_locator,
        "derive_profile_locator 必须可外部复算")


def _test_version_enters_identity():
    """逐类型验证：任一版本常量变化必然改变该类型的身份。"""
    pl = _make_page_layout()
    outline = _make_outline(pl)
    span = _make_span(pl)
    table = _make_table(pl)
    al = _make_alignment(pl)
    syn = _make_synopsis()
    prof = _make_profile()

    check(S.derive_page_layout_locator(
        company_id=_COMPANY_ID, document_id=_DOC_ID, document_version=_DOC_VERSION,
        engine=V.LAYOUT_ENGINE, engine_version=V.LAYOUT_ENGINE_VERSION,
        schema_version="pl-99",
        normalization_version=V.NORMALIZATION_VERSION) != pl.page_layout_locator,
        "LAYOUT_SCHEMA_VERSION 必须进入 layout 身份")
    check(S.derive_page_layout_locator(
        company_id=_COMPANY_ID, document_id=_DOC_ID,
        document_version="sha256-ffffffffffffffff", engine=V.LAYOUT_ENGINE,
        engine_version=V.LAYOUT_ENGINE_VERSION,
        schema_version=V.LAYOUT_SCHEMA_VERSION,
        normalization_version=V.NORMALIZATION_VERSION) != pl.page_layout_locator,
        "document_version 必须进入 layout 身份")

    check(S.derive_document_outline_locator(
        page_layout_id=pl.page_layout_id, document_id=_DOC_ID,
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version="do-99") != outline.outline_locator,
        "OUTLINE_SCHEMA_VERSION 必须进入 outline 身份")
    check(S.derive_document_outline_locator(
        page_layout_id=pl.page_layout_id, document_id=_DOC_ID,
        algorithm_version="oa-99",
        schema_version=V.OUTLINE_SCHEMA_VERSION) != outline.outline_locator,
        "OUTLINE_ALGORITHM_VERSION 必须进入 outline 身份")
    check(S.derive_document_outline_locator(
        page_layout_id=pl.page_layout_id, document_id="doc-9999",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION) != outline.outline_locator,
        "document_id 必须进入 outline 定位身份")

    base_span = dict(
        span_locator=span.span_locator, schema_version=span.schema_version,
        document_id=span.document_id, document_version=span.document_version,
        node_id=span.node_id, role=span.role,
        unassigned_reason=span.unassigned_reason, page_range=span.page_range,
        char_range=span.char_range, layout_line_refs=span.layout_line_refs,
        component_evidence_refs=span.component_evidence_refs,
        alignment_ids=span.alignment_ids, is_fallback=span.is_fallback,
        fallback_derivation=span.fallback_derivation,
        is_cross_heading=span.is_cross_heading, confidence=span.confidence,
        normalized_text=span.normalized_text)
    check(S.derive_span_id(**{**base_span, "schema_version": "os-99"}) != span.span_id,
          "SPAN_SCHEMA_VERSION 必须进入 span 身份")
    check(S.derive_span_id(**{**base_span, "normalized_text": span.normalized_text + "改"})
          != span.span_id, "span 正文变化必须改变 span 身份")
    check(S.derive_span_locator(
        document_outline_locator=span.document_outline_locator,
        evidence_set_version=span.evidence_set_version,
        start_anchor=span.start_anchor, end_anchor=span.end_anchor,
        span_builder_version="sb-99") != span.span_locator,
        "SPAN_BUILDER_VERSION 必须进入 span 身份")

    base_al = dict(
        alignment_locator=al.alignment_locator, schema_version=al.schema_version,
        evidence_block_id=al.evidence_block_id, block_char_length=al.block_char_length,
        verdict=al.verdict, coverage=al.coverage, residue_class=al.residue_class,
        residue=al.residue, char_map=al.char_map, matched_chars=al.matched_chars)
    check(S.derive_alignment_id(**{**base_al, "schema_version": "als-99"})
          != al.alignment_id, "ALIGN_SCHEMA_VERSION 必须进入 alignment 身份")
    # TS3 §五：精确分子进入 alignment 身份（同一展示 coverage、不同精确比值不得
    # 共享 alignment_id）。
    check(S.derive_alignment_id(**{**base_al,
                                   "matched_chars": al.matched_chars - 1})
          != al.alignment_id, "精确分子 matched_chars 必须进入 alignment 身份")
    # 追加式：`als-1`（无精确分子）的 alignment_id 与本次改动前逐位一致 ——
    # 旧载荷的身份**不得**被静默改写。
    legacy_payload = {k: v for k, v in base_al.items() if k != "matched_chars"}
    check(S.derive_alignment_id(**legacy_payload)
          == S.derive_alignment_id(**{**legacy_payload, "matched_chars": None}),
          "省略 matched_chars 与显式 None 得到同一身份（als-1 载荷身份不变）")
    check(S.derive_alignment_id(**{**base_al, "evidence_block_id": "ev-other"})
          != al.alignment_id, "源 Evidence 身份必须进入 alignment 身份")
    check(S.derive_alignment_locator(
        page_layout_id=al.page_layout_id, evidence_set_version=al.evidence_set_version,
        aligner_version="al-99", page_number=al.page_number,
        block_index=al.block_index) != al.alignment_locator,
        "ALIGNER_VERSION 必须进入 alignment 身份")

    # §七.10 / §七.11：TS2 最终关闭轮把 ALIGNER_VERSION 由 `al-1` 升为 `al-2`
    # （**对齐规则语义**变了，wire 字段一个都没变）。这必须改变 alignment 身份，
    # 但**不得**改变 PageLayout 身份。
    #
    # TS3 收口轮（§九）：对齐器的**输入信任边界**又变了（正式集合入口改为要求权威
    # `EvidenceSetSnapshot` 精确相等），因此再次升版到 `al-3`；`al-1`/`al-2` 都登记为
    # legacy，绝不与当前版本共用同一个版本号。
    check(V.ALIGNER_VERSION == "al-3",
          "TS3 收口轮后 ALIGNER_VERSION 必须为 al-3")
    check(V.legacy_versions("ALIGNER_VERSION") == ("al-1", "al-2"),
          "al-1 / al-2 必须都登记为历史算法版本")
    # TS3 §五：本轮**确实**改了 alignment 的 wire 字段（新增精确分子 `matched_chars`，
    # verdict 改由精确整数比值推导），按"改变 wire format 必须升版"升到 `als-2`；
    # 收口轮把**公共分区不变量**变成构造/反序列化期强制（§七 P1-E），同一版本下
    # 持久化语义不得静默改变 ⇒ 再升到 `als-3`。`als-1`/`als-2` 登记为**只读兼容**。
    check(V.ALIGN_SCHEMA_VERSION == "als-3",
          "TS3 收口轮后 ALIGN_SCHEMA_VERSION 必须为 als-3")
    check(V.legacy_versions("ALIGN_SCHEMA_VERSION") == ("als-1", "als-2"),
          "als-1 / als-2 必须登记为 legacy（只读兼容），不得当成未知版本")
    check(V.classify_schema_version("ALIGN_SCHEMA_VERSION", "als-1") == "legacy",
          "als-1 被识别为 legacy 而不是 current/unknown")
    check(V.classify_schema_version("ALIGN_SCHEMA_VERSION", "als-2") == "legacy",
          "als-2 被识别为 legacy（分区语义变化后不得继续作为写路径版本）")
    check(V.ALIGN_REFUSAL_SCHEMA_VERSION == "alr-2",
          "拒绝终态有自己的 wire 版本 alr-2（收口轮的分区不变量同样适用于拒绝终态）")
    check(V.legacy_versions("ALIGN_REFUSAL_SCHEMA_VERSION") == ("alr-1",),
          "alr-1 必须登记为 legacy（只读兼容）")
    check(V.VERSIONED_OBJECT_SCHEMA_FIELDS["AlignmentRefusalRecord"]
          == ("schema_version", "ALIGN_REFUSAL_SCHEMA_VERSION"),
          "拒绝终态必须登记为版本化持久化类型")
    check(V.NORMALIZATION_VERSION == "norm-1",
          "归一化原语未变，NORMALIZATION_VERSION 必须保持 norm-1")
    old_locator = S.derive_alignment_locator(
        page_layout_id=al.page_layout_id, evidence_set_version=al.evidence_set_version,
        aligner_version="al-1", page_number=al.page_number,
        block_index=al.block_index)
    new_locator = S.derive_alignment_locator(
        page_layout_id=al.page_layout_id, evidence_set_version=al.evidence_set_version,
        aligner_version=V.ALIGNER_VERSION, page_number=al.page_number,
        block_index=al.block_index)
    check(new_locator == al.alignment_locator and old_locator != al.alignment_locator,
          "旧算法版本 → 当前版本必须改变 alignment 定位身份"
          "（旧载荷不可能被静默按新规则读取）")
    check(S.derive_alignment_id(**{**base_al, "alignment_locator": old_locator})
          != al.alignment_id,
          "旧算法版本 → 当前版本必须改变 alignment revision 身份")
    check(S.derive_alignment_id(**{**base_al, "alignment_locator": new_locator})
          == al.alignment_id,
          "同一当前定位身份下 revision 身份必须可被外部复算重现")

    pl_src = _read_module("schema.py")
    pl_fn = pl_src.split("def derive_page_layout_locator(", 1)[1].split("\ndef ", 1)[0]
    check("aligner_version" not in pl_fn and "ALIGNER_VERSION" not in pl_fn,
          "PageLayout 定位派生实现不得引用 ALIGNER_VERSION（本轮 PageLayout 身份必须不变）")
    original_aligner = V.ALIGNER_VERSION
    try:
        V.ALIGNER_VERSION = "al-99"
        other_pl = _make_page_layout()
    finally:
        V.ALIGNER_VERSION = original_aligner
    check(other_pl.page_layout_id == pl.page_layout_id
          and other_pl.page_layout_locator == pl.page_layout_locator,
          "改变 ALIGNER_VERSION 不得改变 PageLayout 身份（§七.11）")
    check(S.derive_alignment_locator(
        page_layout_id=pl.page_layout_id, evidence_set_version=al.evidence_set_version,
        aligner_version=V.ALIGNER_VERSION, page_number=al.page_number,
        block_index=al.block_index) == al.alignment_locator,
        "恢复版本后 alignment 定位身份必须可复算重现")

    check(S.derive_table_id(
        table_locator=table.table_locator,
        content_fingerprint="0" * 64) != table.table_id,
        "内容指纹必须进入 table 身份")
    check(S.derive_synopsis_id(
        synopsis_locator=syn.synopsis_locator, schema_version="nss-99",
        status=syn.status, reason_code=syn.reason_code,
        snippets=syn.snippets) != syn.synopsis_id,
        "SYNOPSIS_SCHEMA_VERSION 必须进入 synopsis 身份")
    check(S.derive_synopsis_locator(
        node_id=syn.node_id, synopsis_version="ns-99",
        source_span_ids=syn.source_span_ids) != syn.synopsis_locator,
        "SYNOPSIS_VERSION 必须进入 synopsis 身份")
    check(S.derive_profile_id(
        profile_locator=prof.profile_locator, schema_version="anps-99",
        entries=prof.entries) != prof.profile_id,
        "PROFILE_SCHEMA_VERSION 必须进入 profile 身份")
    check(S.derive_profile_locator(
        contract_version=prof.contract_version,
        contract_fingerprint=prof.contract_fingerprint,
        rule_version="anp-99") != prof.profile_locator,
        "PROFILE_RULE_VERSION 必须进入 profile 身份")
    edge = outline.edges[0]

    def _edge_id_kwargs(e, **overrides):
        """从真实边取出 edge_id 的全量输入（TS1.2：含 from_ref / to_ref / is_resolved）。"""
        kw = dict(edge_locator=e.edge_locator, schema_version=e.schema_version,
                  reference_edge_version=e.reference_edge_version,
                  from_ref=e.from_ref, to_ref=e.to_ref, is_resolved=e.is_resolved,
                  resolution_evidence=e.resolution_evidence,
                  reason_code=e.reason_code, occurrence=e.occurrence)
        kw.update(overrides)
        return kw

    check(S.derive_reference_edge_id(**_edge_id_kwargs(edge, schema_version="res-99"))
          != edge.edge_id, "REFERENCE_EDGE_SCHEMA_VERSION 必须进入 edge 身份")
    check(S.derive_reference_edge_id(**_edge_id_kwargs(edge, reference_edge_version="re-99"))
          != edge.edge_id, "REFERENCE_EDGE_VERSION 必须进入 edge 身份")
    # TS1.2：解析结果本身必须进入 edge 身份（定位身份不得随之改变）。
    check(S.derive_reference_edge_id(**_edge_id_kwargs(edge, to_ref="node:other"))
          != edge.edge_id, "目标身份必须进入 edge 身份")
    check(S.derive_reference_edge_id(**_edge_id_kwargs(edge, is_resolved=not edge.is_resolved))
          != edge.edge_id, "解析状态必须进入 edge 身份")
    # occurrence 必须进入 edge 身份：位置不同的两条真实引用不得得到同一身份。
    open_edge = outline.edges[1]
    check(open_edge.occurrence is not None, "未解析 cross_reference 边必须带 occurrence")
    shifted_occ = replace(open_edge.occurrence, char_start=1,
                          char_end=1 + len(open_edge.occurrence.normalized_text))
    check(S.derive_reference_edge_id(
        **_edge_id_kwargs(open_edge, occurrence=shifted_occ)) != open_edge.edge_id,
        "occurrence 位置必须进入 edge 身份（不得把两处引用并成一条）")
    check(S.derive_reference_edge_locator(
        document_outline_locator=open_edge.document_outline_locator,
        from_ref=open_edge.from_ref, to_ref=open_edge.to_ref,
        edge_kind=open_edge.edge_kind, occurrence=shifted_occ)
        != open_edge.edge_locator,
        "occurrence 位置必须进入 edge 定位身份")


# ---------------------------------------------------------------------------
# C. 序列化
# ---------------------------------------------------------------------------


def _test_serialization():
    # TS1.4：`DocumentOutline` 携带真实 occurrence 的边（无论已解析还是未解析）在
    # 反序列化时必须显式提供核验上下文，因此往返要带上与构造时同一份上下文。
    outline_ctx = _outline_ctx(_make_page_layout(), _make_ref_span())
    for obj in _all_fixtures():
        name = type(obj).__name__
        d = obj.to_dict()
        if not check(isinstance(d, dict), f"{name}.to_dict 必须返回 dict"):
            continue
        check(d.get("schema_type") == name, f"{name}.to_dict 必须带 schema_type")
        back = (S.DocumentOutline.from_dict(d, reference_context=outline_ctx)
                if name == "DocumentOutline" else type(obj).from_dict(d))
        check(back == obj, f"{name} 序列化往返必须相等")
        check(back.to_dict() == d, f"{name} 往返后字典必须逐键相等")

    pl = _make_page_layout()
    d = pl.to_dict()
    raises(lambda: S.PageLayout.from_dict({**d, "未知字段": 1}),
           SchemaValidationError, "未知字段", "未知字段必须被拒绝")
    raises(lambda: S.PageLayout.from_dict(
        {k: v for k, v in d.items() if k != "source_file_sha256"}),
        SchemaValidationError, "缺必填字段", "缺必填字段必须被拒绝")
    raises(lambda: S.PageLayout.from_dict({**d, "page_count": "3"}),
           SchemaValidationError, "必须为 int", "类型错误必须被拒绝")
    # 引擎版本可合法演进（不 pin 到常量），但换了引擎版本就是另一个 layout：
    # 定位身份重算必然失败，因此"偷换引擎版本"仍然 fail-closed。
    raises(lambda: S.PageLayout.from_dict({**d, "engine_version": "pymupdf-99"}),
           SchemaValidationError, "page_layout_locator",
           "偷换引擎版本必须因定位身份重算失败而被拒绝")
    raises(lambda: S.PageLayout.from_dict({**d, "normalization_version": "norm-99"}),
           SchemaValidationError, "当前版本", "归一化版本不符必须被拒绝")
    raises(lambda: S.PageLayout.from_dict({**d, "source_file_sha256": "abc"}),
           SchemaValidationError, "sha256", "非法 sha256 必须被拒绝")
    raises(lambda: S.PageLayout.from_dict({**d, "company_id": ""}),
           SchemaValidationError, "company_id", "空 company_id 必须被拒绝")

    # 跨类型字典混淆（判别符不匹配）必须被拒。
    od = _make_outline(pl).to_dict()
    raises(lambda: S.PageLayout.from_dict(od), SchemaValidationError, "跨类型字典混淆",
           "跨类型字典混淆必须被拒绝")
    raises(lambda: S.DocumentOutline.from_dict(d), SchemaValidationError,
           "跨类型字典混淆", "反方向跨类型混淆同样必须被拒绝")

    # 旧 wire format 必须被显式识别并拒绝（不得静默解释为新版本）。
    for constant, legacy in V.LEGACY_SCHEMA_VERSIONS.items():
        check(V.classify_schema_version(constant, legacy[0]) == "legacy",
              f"{constant} 的旧值 {legacy[0]} 必须分类为 legacy")
        check(V.legacy_versions(constant) == legacy,
              f"{constant} 的旧版本清单不得丢失")
    check(V.classify_schema_version("LAYOUT_SCHEMA_VERSION",
                                    V.LAYOUT_SCHEMA_VERSION) == "current",
          "当前版本必须分类为 current")
    check(V.classify_schema_version("LAYOUT_SCHEMA_VERSION", "pl-99") == "unknown",
          "未知版本必须分类为 unknown")
    raises(lambda: S.PageLayout.from_dict({**d, "schema_version": "pl-1"}),
           SchemaValidationError, "旧 wire format",
           "旧 schema 版本必须给出显式迁移错误")
    raises(lambda: S.PageLayout.from_dict({**d, "schema_version": "pl-99"}),
           SchemaValidationError, "未知版本", "未知 schema 版本必须被拒绝")
    raises(lambda: S.PageLayout.from_dict({**d, "schema_version": ""}),
           SchemaValidationError, "schema_version", "空 schema 版本必须被拒绝")
    for typename, key, legacy in (("DocumentOutline", "do-1", "旧 wire format"),
                                  ("TableObject", "to-3", "旧 wire format"),
                                  ("AspectNavigationProfile", "anps-1", "旧 wire format")):
        obj = {"DocumentOutline": _make_outline(pl), "TableObject": _make_table_v4(),
               "AspectNavigationProfile": _make_profile()}[typename]
        raises(lambda o=obj, k=key: type(o).from_dict({**o.to_dict(),
                                                       "schema_version": k}),
               SchemaValidationError, legacy, f"{typename} 旧版本必须被拒绝")
    # 两个方向都要拦：历史 `to-3` 不得被当前 reader 静默当成 `to-4`（上面那条），
    # 更早的 `to-1` 也不得被**历史** reader 当成它接受的那一版 `to-3`。
    # 历史 reader 只认自己的那一版，并显式指向当前实现的迁移路径。
    raises(lambda: S.TableObject.from_dict(
        {**_make_table(pl).to_dict(), "schema_version": "to-1"}),
        SchemaValidationError, "必须由当前实现",
        "更早的 to-1 不得被历史 reader 当成 to-3（须显式迁移）")
    ad = _make_alignment(pl).to_dict()
    raises(lambda: S.TextAlignmentRecord.from_dict({**ad, "schema_version": "als-99"}),
           SchemaValidationError, "未知版本", "alignment 未知版本必须被拒绝")

    # §七.12：旧 **算法/规则** 版本的载荷必须 fail-closed，且必须给出专门的
    # "已退役的旧规则版本" 错误，而不是被静默按当前版本的判定规则解释。
    check(V.classify_schema_version("ALIGNER_VERSION", "al-1") == "legacy",
          "al-1 必须被登记为 legacy 算法版本")
    check(V.legacy_versions("ALIGNER_VERSION") == ("al-1", "al-2"),
          "al-1 / al-2 必须出现在 ALIGNER_VERSION 的旧版本清单里")
    check(V.classify_schema_version("ALIGNER_VERSION", V.ALIGNER_VERSION) == "current",
          "当前 ALIGNER_VERSION 必须分类为 current")
    old_version, current_version = V.legacy_versions("ALIGNER_VERSION")[0], \
        V.ALIGNER_VERSION
    raises(lambda: S.TextAlignmentRecord.from_dict({**ad, "aligner_version": old_version}),
           SchemaValidationError, "已退役的旧规则版本",
           "旧算法版本载荷必须给出显式退役错误（不得静默按当前版本解释）")
    for legacy_aligner in V.legacy_versions("ALIGNER_VERSION"):
        raises(lambda v=legacy_aligner: S.TextAlignmentRecord.from_dict(
            {**ad, "aligner_version": v}),
               SchemaValidationError, "已退役的旧规则版本",
               f"旧算法版本 {legacy_aligner} 一律显式退役（不得静默升级）")
    check(old_version != current_version
          and current_version not in V.legacy_versions("ALIGNER_VERSION"),
          "当前算法版本不在自己的历史清单里（不得自我降级）")
    raises(lambda: S.TextAlignmentRecord.from_dict({**ad, "aligner_version": "al-99"}),
           SchemaValidationError, "必须为当前版本",
           "未知 aligner 版本必须被拒绝（不得当成 legacy 也不得放行）")
    raises(lambda: S.TextAlignmentRecord.from_dict(
        {**ad, "aligner_version": V.ALIGNER_VERSION, "verdict": "unaligned"}),
           SchemaValidationError, "verdict", "§七.9 调用方篡改 verdict 必须被拒绝")
    sd = _make_span(pl).to_dict()
    raises(lambda: S.OutlineSpan.from_dict({**sd, "schema_version": "os-1"}),
           SchemaValidationError, "旧 wire format", "span 旧版本必须被拒绝")
    yd = _make_synopsis().to_dict()
    raises(lambda: S.NavigationSynopsis.from_dict({**yd, "schema_version": "nss-99"}),
           SchemaValidationError, "未知版本", "synopsis 未知版本必须被拒绝")
    ed = _make_outline(pl).edges[0].to_dict()
    raises(lambda: S.ReferenceEdge.from_dict({**ed, "schema_version": "res-99"}),
           SchemaValidationError, "未知版本", "edge 未知版本必须被拒绝")

    # 规范化工具与 harness 侧实现逐字节等价（两个独立实现互相印证，非自证）。
    try:
        from harness.topic_schema import canonical_json as harness_canonical
    except ImportError:  # pragma: no cover - 依赖可用性
        _results["skipped"] += 1
    else:
        for sample in ({"b": [1, 2, {"d": "中文"}], "a": (3, 4)},
                       {"x": 1.0, "y": "a  b"},
                       {"nested": [{"k": None}, {"z": True}]},
                       {"f": frozenset({"z", "y"})}):
            check(canonical_json(sample) == harness_canonical(sample),
                  f"canonical_json 必须与 harness 侧逐字节一致: {sample}")


# ---------------------------------------------------------------------------
# D. 构造期不变量（正常路径）
# ---------------------------------------------------------------------------


def _test_invariants():
    pl = _make_page_layout()
    check(len(pl.pages) == 1 and len(pl.pages[0].lines) == _LINE_COUNT,
          "页面行夹具必须完整")
    t = _line_text(0)
    line = _mk_line(0, t, _Y0, [(0, 3, "SimSun", 10.5, False),
                                (3, len(t), "SimSun", 10.5, False)])
    check(line.spans[1].text == t[3:], "跨两个 span 的行文本切片必须自洽")

    # 对齐记录：三者都是确定性重算结果。
    al = _make_alignment(pl)
    check(al.coverage == S.compute_alignment_coverage(al.block_char_length, al.char_map),
          "coverage 必须等于重算值")
    check(al.residue_class == S.compute_alignment_residue_class(al.residue),
          "residue_class 必须等于重算值")
    check(al.verdict == S.compute_alignment_verdict(al.coverage, al.residue_class,
                                                    V.ALIGN_MIN),
          "verdict 必须等于重算值")
    check(S.compute_alignment_coverage(10, ((0, 10, 1, 0, 0, 0),)) == 1.0,
          "全覆盖必须重算为 1.0")
    check(S.compute_alignment_coverage(0, ()) == 0.0, "零长度块必须重算为 0.0")
    check(S.compute_alignment_coverage(10, ((0, 2, 1, 0, 0, 0),)) == 0.2,
          "部分覆盖必须按已映射字符数重算")
    check(S.compute_alignment_residue_class(()) is None, "无残差必须重算为 None")
    check(S.compute_alignment_residue_class(
        ((0, 1, "page_furniture"), (1, 2, "unexplained"))) == "unexplained",
        "记录级残差分类必须取最高严重度")
    check(S.compute_alignment_verdict(0.0, None, None) == "unaligned",
          "零覆盖率必须重算为 unaligned")
    check(S.compute_alignment_verdict(1.0, None, None) == "partially_aligned",
          "阈值未裁决（通用函数的兼容档）时不得产出 aligned")
    check(S.compute_alignment_verdict(1.0, None, 0.90) == "aligned",
          "达到冻结阈值且无 unexplained 时必须重算为 aligned")
    check(S.compute_alignment_verdict(0.899999, None, 0.90) == "unaligned",
          "低于冻结阈值必须重算为 unaligned（0.899999 < 0.90，不得四舍五入放行）")
    check(S.compute_alignment_verdict(0.90, None, 0.90) == "aligned",
          "恰好达到冻结阈值必须重算为 aligned（阈值是闭区间下界）")
    check(S.compute_alignment_verdict(0.95, "unexplained", 0.90) == "partially_aligned",
          "达到阈值但含 unexplained 时必须重算为 partially_aligned")
    check(S.compute_alignment_verdict(0.80, "unexplained", 0.90) == "unaligned",
          "低于阈值时即使含 unexplained 也必须重算为 unaligned（低阈值优先）")
    check(S.compute_alignment_verdict(1.0, "page_furniture", 0.90) == "aligned",
          "达到阈值且残差主类不是 unexplained 时必须重算为 aligned")

    check(al.verdict == "aligned" and al.is_citable() is True,
          "冻结阈值 0.90 下全覆盖且无残差的记录必须可引证")

    # §七.8：partial / unaligned 一律不可引证（两种非 aligned 状态各给一个真实记录）。
    low = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=1, evidence_block_id=_BLOCK_ID, block_char_length=10,
        residue=((5, 10, "page_furniture"),), char_map=((0, 5, 1, 0, 0, 0),))
    check(low.coverage == 0.5 and low.verdict == "unaligned"
          and low.is_citable() is False,
          "低于冻结阈值的记录必须 unaligned 且不可引证")
    part = S.TextAlignmentRecord.create(
        page_layout_id=pl.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=1, evidence_block_id=_BLOCK_ID, block_char_length=10,
        residue=((9, 10, "unexplained"),), char_map=((0, 9, 1, 0, 0, 0),))
    check(part.coverage == 0.9 and part.verdict == "partially_aligned"
          and part.is_citable() is False,
          "达到冻结阈值但含 unexplained 的记录必须 partially_aligned 且不可引证")
    check(low.alignment_locator == part.alignment_locator
          and low.alignment_id != part.alignment_id,
          "未达阈值与含 unexpl. 的记录必须可区分（revision 身份必须不同）")

    # ---- TS3 §五：精确阈值 + 拒绝终态 ----
    check(S.compute_alignment_verdict_exact(8998, 10000, None, 0.90) == "unaligned",
          "精确 0.8998 < 0.90 判 unaligned（不得先四舍五入再判阈值）")
    check(S.compute_alignment_verdict_exact(9000, 10000, None, 0.90) == "aligned",
          "精确 0.9000 达到阈值判 aligned")
    check(S.compute_alignment_verdict(
        S.quantize(0.8998), None, 0.90) == "aligned",
        "对照：量化展示值 0.9 在同一真值表下本会判 aligned（差异即拒绝的理由）")
    refusal = _make_refusal(pl)
    check(refusal.schema_version == V.ALIGN_REFUSAL_SCHEMA_VERSION,
          "拒绝终态携带自己的 wire 版本")
    check((refusal.matched_chars, refusal.block_char_length) == (8998, 10000)
          and refusal.exact_coverage == 8998 / 10000,
          "拒绝终态携带精确分子与分母（不是展示值）")
    check(refusal.coverage == 0.9 and refusal.exact_coverage < 0.90,
          "拒绝终态的展示值不低于阈值、精确比值低于阈值")
    check(refusal.is_citable() is False,
          "拒绝终态恒不可引用")
    check(S.AlignmentRefusalRecord.from_dict(refusal.to_dict()).to_dict()
          == refusal.to_dict(), "拒绝终态可逐字段读回一致")
    check(S.derive_alignment_refusal_id(
        alignment_locator=refusal.alignment_locator,
        schema_version=refusal.schema_version,
        evidence_block_id=refusal.evidence_block_id,
        block_char_length=refusal.block_char_length,
        matched_chars=refusal.matched_chars, coverage=refusal.coverage,
        residue_class=refusal.residue_class, residue=refusal.residue,
        char_map=refusal.char_map,
        refusal_reason=refusal.refusal_reason) == refusal.refusal_id,
        "derive_alignment_refusal_id 必须可外部复算")
    check(S.derive_alignment_refusal_locator(
        page_layout_id=refusal.page_layout_id,
        evidence_set_version=refusal.evidence_set_version,
        aligner_version=refusal.aligner_version,
        page_number=refusal.page_number,
        block_index=refusal.block_index) == refusal.alignment_locator,
        "拒绝终态与对齐记录共享同一个定位槽位")
    # 拒绝理由必须真的成立（不得当作通用出口）
    raises(lambda: S.AlignmentRefusalRecord.create(
        page_layout_id=pl.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=0, evidence_block_id=_BLOCK_ID, block_char_length=10,
        matched_chars=10, residue=(), char_map=((0, 10, 1, 0, 0, 0),),
        refusal_reason="quantization_boundary_refused"),
        SchemaValidationError, "拒绝理由不成立",
        "完全对齐的块不得被拒绝（拒绝理由必须成立）")
    raises(lambda: S.AlignmentRefusalRecord.create(
        page_layout_id=pl.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=0, evidence_block_id=_BLOCK_ID, block_char_length=10,
        matched_chars=1, residue=((1, 10, "unexplained"),),
        char_map=((0, 1, 1, 0, 0, 0),),
        refusal_reason="quantization_boundary_refused"),
        SchemaValidationError, "拒绝理由不成立",
        "无量化张力的低覆盖块不得被拒绝")
    raises(lambda: S.AlignmentRefusalRecord.from_dict(
        {**refusal.to_dict(), "refusal_reason": "whatever"}),
        SchemaValidationError, "refusal_reason",
        "拒绝原因码是封闭集合")

    span = _make_span(pl)
    # 局部/集合资格**不得**写成"恒为 False"的硬断言：那会把"升到 B 阶段"误报成
    # 回归，从而掩盖真实缺陷。这里改为从单一 A/B 真值表派生期望值 —— A 阶段
    # （阈值未裁决）恒 False（fail-closed），B 阶段（阈值已裁决）由 span 自身的
    # 置信度与 `SPAN_CONFIDENCE_MIN` 的比较决定：
    #   eligible  = 非 fallback / 已归属 / 不跨标题 / confidence>0 / confidence>=阈值
    #   complete  = eligible ∧ 有 component Evidence ∧ alignment 全部可引证
    gate = sp.ab_gate_truth_table()
    expect_eligible = (gate["threshold_decided"] and span.confidence > 0
                       and span.confidence >= V.SPAN_CONFIDENCE_MIN)
    check(span.is_structurally_eligible() is expect_eligible,
          f"局部结构资格必须等于真值表口径（阶段 {gate['stage']}，"
          f"confidence={span.confidence}）")
    expect_complete = (expect_eligible
                       and bool(span.component_evidence_refs)
                       and span.alignment_citable((_make_alignment(pl),)))
    check(span.can_support_set_complete(alignment_records=(_make_alignment(pl),),
                                        boundary_verified=True) is expect_complete,
          f"set_complete 资格必须等于真值表口径（阶段 {gate['stage']}）")
    if not gate["threshold_decided"]:
        check(expect_eligible is False,
              "A 阶段任何 span 都不得具备局部结构资格（fail-closed）")
    check(span.alignment_citable((_make_alignment(pl),)) is True,
          "全部对齐记录为 aligned 时 alignment_citable 必须为 True（§七.7）")
    span.verify_alignment_closure((_make_alignment(pl),))
    check(True, "闭合的 alignment 记录必须通过校验")

    unassigned = _make_outline(pl).unassigned[0]
    check(unassigned.role == "unassigned" and unassigned.node_id is None,
          "显式未归属 span 的 role/node_id 必须自洽")
    check(unassigned.unassigned_reason == "below_last_heading",
          "未归属必须保留原因")
    check(bool(unassigned.layout_line_refs), "未归属 span 必须保留来源行定位")
    check(unassigned.char_range == (0, len(_line_text(6))),
          "未归属 span 必须保留来源字符范围")
    check(unassigned.is_structurally_eligible() is False,
          "未归属 span 不得具备局部结构资格")

    table = _make_table(pl)
    check(table.is_financial_authority() is False, "TableObject 不得自称金额权威")
    check(table.verify_provenance(spans=(_make_span(pl),),
                                  alignment_records=(_make_alignment(pl),)) is None,
          "闭合的 provenance 必须通过校验（对象级闭合）")
    check(table.is_evidence_backed(spans=(_make_span(pl),),
                                   alignment_records=(_make_alignment(pl),)) is True,
          "全部对齐记录为 aligned 时 is_evidence_backed 必须为 True（§七.7）")
    check(len(table.all_rows()) == 3, "all_rows 必须覆盖全部行")
    check(all(r.row_index == i for i, r in enumerate(table.all_rows())),
          "all_rows 必须按 row_index 连续")
    check(len(table.cell_grid) == 6, "cell_grid 必须覆盖 3 行 2 列")
    check(table.unit == "万元" and table.title_source == "caption_line",
          "表格必须携带单位与表题来源")

    outline = _make_outline(pl)
    # P1-2/P1-3：outline 不再自证"已解析"，"引用已验证"只能由对象级核验结论承载。
    # TS1.4：未解析边的**来源**也必须落到真实版式上，因此核验必须交出真实上下文。
    ctx = _outline_ctx(pl, _make_ref_span(pl))
    verified = outline.verify_references(ctx)
    check(len(verified.verified_edge_ids()) == 1, "必须恰好一条已通过对象级核验的边")
    check(len(verified.unresolved_edge_ids()) == 1, "必须恰好一条未解析边")
    check(verified.verified_edge_ids()[0] == outline.edges[0].edge_id,
          "通过核验的必须正是那条 parent_child 边")
    check(verified.is_verified(outline.edges[1].edge_id) is False,
          "未解析边不得出现在核验结论里")
    check(verified.resolver_version == V.REFERENCE_RESOLVER_VERSION,
          "核验结论必须携带核验器版本（可审计）")
    check(verified.context_fingerprint == ctx.fingerprint(),
          "核验结论必须携带本次核验上下文的指纹（不得用另一组上下文冒充）")
    check(verified.outline_locator == outline.outline_locator
          and verified.outline_id == outline.outline_id,
          "核验结论必须绑定被核验的 outline")
    check(len(outline.unresolved_edges()) == 1, "必须恰好一条未解析边")
    check(outline.unresolved_edges()[0].reason_code == "no_textual_evidence",
          "未解析边必须保留原因码")
    unresolved = outline.unresolved_edges()[0]
    check(unresolved.occurrence is not None
          and (unresolved.occurrence.page_number, unresolved.occurrence.line_index)
          == (1, _REF_SPAN_LINE),
          "未解析边必须保留原始引用的字符级出现位置")
    check(unresolved.occurrence.declared_target == _OPEN_REF_TARGET,
          "未解析边必须保留原文声明的目标文字（不得丢掉原始引用文字）")
    check(unresolved.to_ref is None,
          "未解析边不得声称目标 id")
    check(outline.node_by_id(outline.nodes[1].node_id) is outline.nodes[1],
          "node_by_id 必须按 id 精确查表")
    check(outline.span_by_id(outline.unassigned[0].span_id) is outline.unassigned[0],
          "span_by_id 必须按 id 精确查表")
    check(outline.node_by_id("on-does-not-exist") is None,
          "悬空 id 必须返回 None（不得猜）")

    syn = _make_synopsis()
    check(syn.is_navigation_only() is True, "NavigationSynopsis 只作导航")
    check(syn.status == "available" and syn.reason_code is None,
          "available 简介不得带原因码")
    unavail = S.NavigationSynopsis.unavailable(node_id="on-1", reason_code="empty_text")
    check(unavail.snippets == () and unavail.reason_code == "empty_text",
          "unavailable 简介必须为空且带原因码")
    prof = _make_profile()
    check(prof.is_profile() is True and prof.produces_coverage() is False,
          "AspectNavigationProfile 不得产生 coverage")


# ---------------------------------------------------------------------------
# E. 已裁决语义锁定 + 无公司特例
# ---------------------------------------------------------------------------

# 本清单**定义在测试内**，不读取生产代码提供的任何"禁用词表"：否则被审对象可以靠
# 修改自己的清单让扫描永远为空。
FORBIDDEN_TOKENS = ("300750", "宁德时代", "CATL", "catl", "gold", "answer_key",
                    "fixed_page")
BANNED_IMPORTS = ("sqlite3", "requests", "httpx", "urllib", "socket", "openai",
                  "anthropic", "chromadb", "bocha", "fitz", "pymupdf", "psycopg",
                  "sqlalchemy", "subprocess")


def _test_locked_semantics():
    from document_structure import schema as X  # noqa: PLC0415

    check(X.DESCENDANTS_ARE_CANDIDATES_ONLY is True, "后代只作候选必须为 True")
    check(X.SUPERSEDED_VERSION_SAME_LOGICAL_DOCUMENT_ONLY is True,
          "superseded_version 限同一逻辑文档必须为 True")
    check(X.TABLE_STRUCTURE_CLASS_IS_AUTHORITY_VERDICT is False,
          "结构分类不得是 authority verdict")
    check(X.AMOUNT_AUTHORITY_SOURCES == ("FinancialSnapshot", "FinancialFactPack"),
          "金额权威来源必须只有 FinancialSnapshot / FinancialFactPack")

    for cls in S.PUBLIC_TYPES:
        for fname in cls.__dataclass_fields__:
            check("superseded" not in fname,
                  f"{cls.__name__} 不得携带 superseded 字段（本层不定义）")
    check("semantics" not in S.TableRow.__dataclass_fields__,
          "TableRow 不得含 semantics 业务解释字段")
    for name in S.DEFERRED_TO_TS7A:
        check(not hasattr(S, name), f"延迟类型 {name} 不得在 TS1 实现")

    # 无参数便利方法不得声称能判定集合闭合（P1-5）。
    import inspect  # noqa: PLC0415
    sig = inspect.signature(S.OutlineSpan.can_support_set_complete)
    params = [p for p in sig.parameters if p != "self"]
    check(len(params) == 2, "can_support_set_complete 必须要求 alignment 与边界上下文")
    for p in params:
        check(sig.parameters[p].default is inspect.Parameter.empty,
              f"can_support_set_complete 的参数 {p} 不得有默认值（不得便利化）")
    check(not hasattr(S.OutlineSpan, "can_support_set_complete_without_context"),
          "不得保留无上下文的便利判定方法")


def _test_no_company_specifics():
    for fname in _TS1_MODULES:
        src = _read_module(fname)
        for token in FORBIDDEN_TOKENS:
            check(token not in src, f"{fname} 不得出现公司/答案专用 token {token!r}")
        for mod in BANNED_IMPORTS:
            check(f"import {mod}" not in src and f"from {mod}" not in src,
                  f"{fname} 不得 import {mod}（本层无 I/O、网络、LLM、子进程）")

    literals = V.all_version_literals()
    check(len(literals) >= 12, "版本字面量清单过短")
    for fname in ("__init__.py", "schema.py", "canonical.py"):
        src = _read_module(fname)
        for lit in literals:
            check(f'"{lit}"' not in src,
                  f"{fname} 不得内联版本字面量 {lit!r}（必须引用 versions）")

    # 结构信号词表必须复用既有原语输出词汇（不得另造同义名）。
    observed = set()
    for sample in ("单位：万元", "项目  2024年  2023年", "营业收入  100  90",
                   "合计  120  100", "（续表）"):
        observed.update(table_body_signals(sample))
    for sig in S.TABLE_STRUCTURE_SIGNALS:
        check(sig in observed, f"结构信号 {sig!r} 必须来自既有原语的输出词汇")

    # 公司无关性的**正确**含义（TS1.1 P1-A 收口）：算法与规则不得随公司变化，
    # 而不是"不同公司的存储身份必须相等"。同一份 PDF 属于不同公司是两个不同的
    # 存储事实，身份必须不同；同时同一公司重复派生必须逐位稳定。
    pl_a = _make_page_layout(company_id="company-A")
    pl_b = _make_page_layout(company_id="company-B")
    check(pl_a.page_layout_id != pl_b.page_layout_id
          and pl_a.page_layout_locator != pl_b.page_layout_locator,
          "不同 company_id 必须得到不同 layout 存储身份（公司无关指规则，不指身份）")
    check(_make_page_layout(company_id="company-A").page_layout_id
          == pl_a.page_layout_id,
          "同一 company_id 重复派生必须逐位稳定（不得含时间/随机成分）")
    # 规则本身不得按公司分支：TS1 模块内不得出现 company_id 字面量比较。
    _company_branch = re.compile(r"company_id\s*(==|!=)\s*[\"']")
    for fname in _TS1_MODULES:
        check(not _company_branch.search(_read_module(fname)),
              f"{fname} 不得按 company_id 字面量分支（算法必须公司无关）")
    e1 = S.AspectNavigationEntry(
        aspect_id="asp-1", question_id="q-1", topic_id="t-1",
        content_role="paragraph_and_table", display_tier="required_body",
        nav_keys=("营业收入",), parent_keys=(), expected_forms=(),
        subject_head="主营业务收入构成",
        derivation=("contract.aspects[asp-1].nav_key",))
    p1 = S.AspectNavigationProfile.create(contract_version="v2",
                                          contract_fingerprint=_CONTRACT_SHA,
                                          entries=(e1,))
    p2 = S.AspectNavigationProfile.create(contract_version="v2",
                                          contract_fingerprint=_CONTRACT_SHA,
                                          entries=(e1,))
    check(p1.profile_id == p2.profile_id, "profile 身份必须可由同一 Contract 复现")
    check(p1.profile_locator == p2.profile_locator, "profile 定位身份必须可复现")
    check("company" not in p1.profile_locator.lower(),
          "profile 定位身份不得掺入公司信息")


# ---------------------------------------------------------------------------


def main() -> dict:
    _test_types_and_constants()
    _test_identity()
    _test_version_enters_identity()
    _test_serialization()
    _test_invariants()
    _test_locked_semantics()
    _test_no_company_specifics()
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

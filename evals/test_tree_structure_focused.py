"""TS1.1 窄范围收口：focused 反例与正例。

按指令 §七 的顺序：**先写本文件的反例，再修改实现**。四组覆盖：

A. canonical JSON 禁止 NaN / ±Inf（P2）
   `canonical_json` 必须 `allow_nan=False`；`to_json_value` 必须在**任何嵌套层级**
   立即抛结构校验错误（dict / list / tuple / set / 嵌套 dataclass），
   不得只靠上层逐字段检查。

B. 公司 / 文档 / 结构身份闭合（P1-A）
   `PageLayout.page_layout_locator` 必须覆盖 company_id / document_id /
   document_version / engine / engine_version / schema 版本 / 归一化版本；
   `DocumentOutline` 必须覆盖 document_id 并经 `page_layout_id` 间接绑定公司；
   `OutlineSpan.span_id` 必须覆盖 document_id；下游对象必须做**对象级**上游核对，
   而不是非空字符串检查。**删除**上一轮"不同 company_id 必须得到相同 PageLayout
   身份"的错误主张。

C. ReferenceOccurrence 与引用目标绑定（P1-B）
   正式边必须绑定真实 occurrence；同一来源/目标/类型但 occurrence 位置不同必须
   得到不同 locator 与 id（不得把"多个 详见 / 如下表"并成一条）；未解析边不得
   编造目标 id；table / toc 端点必须有调用方显式提供的正式对象集合才能标为 resolved；
   `TableObject.continuation_*` 与 `ReferenceEdge.table_continuation` 必须互相印证。

D. TableObject provenance / 网格 / 续表（P1-C）
   每个 TableObject 是**单页物理表格片段**；provenance 必须是对象级闭合；
   rowspan / colspan 按"本行起始单元格"语义；单元格 bbox 必须落在表 bbox 内、
   来源页必须等于本表页；续表必须方向一致、不得自指、不得跨文档。

本模块只做 focused 判定；全量回归在 `evals.test_tree_structure_schema` 与
`evals.test_tree_structure_adversarial`。断言只读取被测对象的公开行为。
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import replace

from document_structure import schema as S
from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    sha256_canonical,
    to_json_value,
)

from evals.test_tree_structure_schema import (
    _BLOCK_ID,
    _BLOCK_LEN,
    _COMPANY_ID,
    _DOC_ID,
    _DOC_SHA,
    _DOC_VERSION,
    _EVIDENCE_SET_VERSION,
    _H,
    _LINE_COUNT,
    _REF_SPAN_LINE,
    _REF_SPAN_TEXT,
    _REF_TEXT_1,
    _REF_TEXT_1_START,
    _REF_TEXT_2_START,
    _anchor,
    _line_text,
    _make_alignment,
    _make_outline,
    _make_page_layout,
    _make_ref_span,
    _make_span,
    _make_table,
    _outline_locator,
    _read_module,
    _table_rows,
)

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
        _results["details"].append(f"FAIL {msg} —— 异常信息不含 {substr!r}：{e}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 SchemaValidationError：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出异常（错误对象可构造）")
    return False


def must_not_raise(fn, msg):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(f"FAIL {msg} —— 正常运行被拒绝：{type(e).__name__}: {e}")
        return False
    _results["passed"] += 1
    _results["details"].append("PASS " + msg)
    return True


class _NaNCarrier:
    """带 `to_dict` 的假 dataclass，用于验证"嵌套 dataclass"这一层。"""

    def __init__(self, value):
        self._value = value

    def to_dict(self) -> dict:
        return {"schema_type": "NaNCarrier", "value": self._value}


# ---------------------------------------------------------------------------
# A. canonical JSON 禁止 NaN / ±Inf
# ---------------------------------------------------------------------------


def _test_canonical_json_nan_inf():
    bad_values = (("NaN", float("nan")), ("+Inf", float("inf")),
                  ("-Inf", float("-inf")))
    shapes = {
        "顶层": lambda v: v,
        "dict": lambda v: {"x": v},
        "list": lambda v: [1, v],
        "tuple": lambda v: (1.0, v),
        "set": lambda v: {1.0, v},
        "frozenset": lambda v: frozenset({1.0, v}),
        "多层嵌套": lambda v: {"a": [{"b": (0, [v])}]},
        "嵌套 dataclass": lambda v: {"m": _NaNCarrier(v)},
        "dict 键值双路径": lambda v: {"n": [{"m": {"deep": [v]}}]},
    }
    for label, v in bad_values:
        for shape, mk in shapes.items():
            must_raise(lambda mk=mk, v=v: canonical_json(mk(v)),
                       f"canonical_json 必须在「{shape}」层拒绝 {label}", "有限实数")
            must_raise(lambda mk=mk, v=v: sha256_canonical(mk(v)),
                       f"sha256_canonical 必须在「{shape}」层拒绝 {label}", "有限实数")
            must_raise(lambda mk=mk, v=v: to_json_value(mk(v)),
                       f"to_json_value 必须在「{shape}」层拒绝 {label}", "有限实数")

    # 即使绕过 to_json_value 直接调用 dumps，也不得产出非标准 JSON。
    src = _read_module("canonical.py")
    check("allow_nan=False" in src,
          "canonical_json 必须显式 allow_nan=False（不得依赖默认值）")
    check(not re.search(r"allow_nan\s*=\s*True", src), "不得允许 NaN 进入 JSON")

    # 正常有限浮点必须仍然确定性可序列化，且与标准 JSON 解析器兼容。
    finite = {"a": 1.0, "b": [0.5, -2.25], "c": {"d": 3.125}}
    text = canonical_json(finite)
    check(json.loads(text) == {"a": 1.0, "b": [0.5, -2.25], "c": {"d": 3.125}},
          "有限浮点的规范 JSON 必须可被标准解析器读回")
    check(canonical_json(finite) == canonical_json(finite),
          "相同输入的规范 JSON 必须逐字节稳定")
    check("NaN" not in text and "Infinity" not in text,
          "有限输入不得产出 NaN/Infinity 字面量")
    check(canonical_json({"a": 1.0}) == '{"a":1.0}',
          "有限浮点的规范形必须保持既有形状（不得为拒绝 NaN 而改写正常输出）")
    check(canonical_json({"z": [1, 2], "a": "中文"}) == '{"a":"中文","z":[1,2]}',
          "sort_keys / ensure_ascii / 分隔符语义不得改变")

    # 量化器与 verdict 的既有有限性检查不得回退。
    must_raise(lambda: S.quantize(float("nan")), "quantize(NaN) 必须被拒绝")
    must_raise(lambda: S.compute_alignment_verdict(float("nan"), None, None),
               "compute_alignment_verdict(NaN) 必须被拒绝")


# ---------------------------------------------------------------------------
# B. 公司 / 文档 / 结构身份闭合
# ---------------------------------------------------------------------------

_COMPANY_LITERAL_BRANCH_RE = re.compile(r"company_id\s*(==|!=)\s*[\"']")


def _test_identity_closure():
    pl = _make_page_layout()
    outline = _make_outline(pl)
    span = _make_span(pl)

    # 1) 同一 PDF、不同 company_id ⇒ 存储身份必须不同（"公司无关"指算法/规则，
    #    不是指不同公司的存储身份必须相等）。
    pl_b = _make_page_layout(company_id="company-B")
    check(pl_b.company_id != pl.company_id, "夹具必须使用不同 company_id")
    check(pl_b.page_layout_locator != pl.page_layout_locator,
          "不同 company_id 必须得到不同 page_layout_locator")
    check(pl_b.page_layout_id != pl.page_layout_id,
          "不同 company_id 必须得到不同 page_layout_id")

    # 2) 同一 PDF、不同 document_id ⇒ 存储身份必须不同。
    pl_d = _make_page_layout(document_id="doc-9999")
    check(pl_d.document_id != pl.document_id, "夹具必须使用不同 document_id")
    check(pl_d.page_layout_locator != pl.page_layout_locator,
          "不同 document_id 必须得到不同 page_layout_locator")
    check(pl_d.page_layout_id != pl.page_layout_id,
          "不同 document_id 必须得到不同 page_layout_id")

    # 3) 同一 company/document/内容 ⇒ 确定性不变。
    check(_make_page_layout().page_layout_locator == pl.page_layout_locator
          and _make_page_layout().page_layout_id == pl.page_layout_id,
          "同一输入必须确定性复现同一身份")

    # 4) locator 必须覆盖指令列出的全部七项。
    base = dict(company_id=_COMPANY_ID, document_id=_DOC_ID,
                document_version=_DOC_VERSION, engine=V.LAYOUT_ENGINE,
                engine_version=V.LAYOUT_ENGINE_VERSION,
                schema_version=V.LAYOUT_SCHEMA_VERSION,
                normalization_version=V.NORMALIZATION_VERSION)
    for field, alt in (("company_id", "company-B"), ("document_id", "doc-9999"),
                       ("document_version", "sha256-0000000000000000"),
                       ("engine", "pdfplumber"),
                       ("engine_version", "pymupdf-99"),
                       ("schema_version", "pl-99"),
                       ("normalization_version", "norm-99")):
        check(S.derive_page_layout_locator(**{**base, field: alt})
              != pl.page_layout_locator,
              f"page_layout_locator 必须覆盖 {field}")

    # 5) 手工替换 company / document 必须被构造期拦下。
    must_raise(lambda: replace(pl, company_id="company-B"),
               "手工替换 PageLayout.company_id 必须被拒绝", "page_layout_locator")
    must_raise(lambda: replace(pl, document_id="doc-9999"),
               "手工替换 PageLayout.document_id 必须被拒绝", "page_layout_locator")

    # 6) DocumentOutline 必须覆盖 document_id，并经 page_layout_id 间接绑定公司。
    oloc_other = S.derive_document_outline_locator(
        page_layout_id=pl.page_layout_id, document_id="doc-9999",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)
    check(oloc_other != outline.outline_locator,
          "outline 定位身份必须覆盖 document_id")
    outline_b = _make_outline(pl_b)
    check(outline_b.outline_id != outline.outline_id,
          "不同公司的同一 PDF 必须得到不同 outline 身份（经 page_layout_id 间接绑定）")
    must_raise(lambda: replace(outline, document_id="doc-9999"),
               "手工替换 DocumentOutline.document_id 必须被拒绝", "outline_locator")
    must_raise(lambda: replace(outline, document_version="sha256-0000000000000000"),
               "手工替换 DocumentOutline.document_version 必须被拒绝")

    # 7) OutlineSpan 的不可变身份必须显式覆盖 document_id。
    check(S.derive_span_id(
        span_locator=span.span_locator, schema_version=span.schema_version,
        document_id="doc-9999", document_version=span.document_version,
        node_id=span.node_id, role=span.role,
        unassigned_reason=span.unassigned_reason, page_range=span.page_range,
        char_range=span.char_range, layout_line_refs=span.layout_line_refs,
        component_evidence_refs=span.component_evidence_refs,
        alignment_ids=span.alignment_ids, is_fallback=span.is_fallback,
        fallback_derivation=span.fallback_derivation,
        is_cross_heading=span.is_cross_heading, confidence=span.confidence,
        normalized_text=span.normalized_text) != span.span_id,
        "span 不可变身份必须覆盖 document_id")
    must_raise(lambda: replace(span, document_id="doc-9999"),
               "手工替换 OutlineSpan.document_id 必须被拒绝", "span_id")

    # 8) 下游必须做对象级核对（不是非空字符串检查）。
    must_not_raise(lambda: outline.verify_upstream(layout=pl),
                   "文档 / layout 匹配时 outline 上游核对必须通过")
    must_raise(lambda: outline.verify_upstream(layout=pl_b),
               "上游 layout 属于另一公司时必须被拒绝", "page_layout_id")
    must_raise(lambda: outline.verify_upstream(layout=pl_d),
               "上游 layout 属于另一文档时必须被拒绝", "page_layout_id")
    must_not_raise(lambda: span.verify_upstream(outline=outline, layout=pl),
                   "文档 / layout / outline 匹配时 span 上游核对必须通过")
    must_raise(lambda: span.verify_upstream(outline=outline_b, layout=pl_b),
               "span 属于另一 outline 时必须被拒绝", "outline_locator")
    must_raise(lambda: span.verify_upstream(outline=outline, layout=pl_d),
               "span 属于另一文档时必须被拒绝")
    table = _make_table(pl)
    must_not_raise(lambda: table.verify_upstream(outline=outline, layout=pl),
                   "文档 / layout / outline 匹配时表格上游核对必须通过")
    must_raise(lambda: table.verify_upstream(outline=outline_b, layout=pl_b),
               "表格属于另一 outline 时必须被拒绝", "document_outline")

    # 9) 通用算法不得出现公司特例分支（公司无关的**规则**仍然必须成立）。
    for fname in ("canonical.py", "versions.py", "schema.py", "__init__.py"):
        src = _read_module(fname)
        check(not _COMPANY_LITERAL_BRANCH_RE.search(src),
              f"{fname} 不得按 company_id 字面量分支（算法必须公司无关）")
    check(S.derive_page_layout_locator(**{**base, "company_id": "甲公司"})
          != S.derive_page_layout_locator(**{**base, "company_id": "乙公司"}),
          "不同公司名必须只改变身份取值，不改变任何结构规则")


# ---------------------------------------------------------------------------
# C. ReferenceOccurrence 与引用目标绑定
# ---------------------------------------------------------------------------

_TOC_ITEM = "toc-item-1"


def _occ(node_id, **kw):
    """一条**声明用** occurrence（未解析边记录"解析器在原文里看到了什么"）。

    它只是声明。resolved 边必须在对象级核验中把 occurrence 落到**真实文本载体**上
    （见 `_span_occ`），自报的 `normalized_text` / `resolution_evidence` 单独不构成证明。
    """
    text = "详见第一节标题"
    base = dict(source_ref=f"node:{node_id}", page_number=1, line_index=5,
                char_start=0, char_end=len(text), occurrence_index=0,
                reference_marker="详见", reference_kind="cross_reference",
                declared_target="第一节标题", normalized_text=text)
    base.update(kw)
    return S.ReferenceOccurrence(**base)


def _span_occ(span_id, **kw):
    """落在**真实正文 span** 上的 occurrence（默认第 0 处「详见」）。"""
    base = dict(source_ref=f"span:{span_id}", page_number=1,
                line_index=_REF_SPAN_LINE,
                char_start=_REF_TEXT_1_START, char_end=len(_REF_TEXT_1),
                occurrence_index=0, reference_marker="详见",
                reference_kind="cross_reference",
                declared_target=_line_text(_REF_SPAN_LINE),
                normalized_text=_REF_TEXT_1)
    base.update(kw)
    return S.ReferenceOccurrence(**base)


def _toc_occ(**kw):
    """目录项来源的**声明用** occurrence（源只是 `toc:<字符串>`，不是真实 `TocSource`）。

    TS1.3 起本层有类型化的真实 TOC 来源对象 `TocSource`（见 §E）；本夹具刻意只用
    字符串 id，用来验证"字符串不是对象存在证明"仍然成立。
    """
    text = "目录项 第一节标题 1"
    base = dict(source_ref=f"toc:{_TOC_ITEM}", page_number=1, line_index=0,
                char_start=0, char_end=len(text), occurrence_index=0,
                reference_marker="第一节标题", reference_kind="toc_to_body",
                declared_target="第一节标题", normalized_text=text)
    base.update(kw)
    return S.ReferenceOccurrence(**base)


def _outline_with(pl, nodes, edges=(), unassigned=None,
                  sources=("body_numbering",), ctx=None):
    base = _make_outline(pl)
    return S.DocumentOutline.create(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        page_layout_id=pl.page_layout_id, nodes=nodes, edges=edges,
        unassigned=base.unassigned if unassigned is None else unassigned,
        candidate_sources=tuple(sources), reference_context=ctx)


def _test_reference_occurrence_and_binding():
    pl = _make_page_layout()
    outline = _make_outline(pl)
    a, a1, b = outline.nodes
    oloc = outline.outline_locator
    ref_span = _make_ref_span(pl)
    # TS1.3：occurrence 必须落到**真实 LayoutLine** 上，因此核验上下文必须交出
    # 真实 PageLayout；只交 span 对象不足以核验页码 / 行号 / 字符区间。
    ctx = S.ReferenceValidationContext(spans=(ref_span,), layout=pl)

    # -- occurrence 自身的不变量 -------------------------------------------------
    good = _occ(b.node_id)
    check(good.char_end - good.char_start == len(good.normalized_text),
          "occurrence 字符区间必须为正长度且等于该区间归一文本长度")
    must_raise(lambda: _occ(b.node_id, char_start=3, char_end=3),
               "零长度 occurrence 区间必须被拒绝", "正长度")
    must_raise(lambda: _occ(b.node_id, char_end=len("详见第一节标题") + 1),
               "字符区间与归一文本长度不符（offset 错位）必须被拒绝")
    must_raise(lambda: _occ(b.node_id, reference_marker="如下表"),
               "marker 未出现在该区间归一文本中必须被拒绝", "必须出现在")
    must_raise(lambda: _occ(b.node_id, reference_kind="不存在的类型"),
               "非法 reference_kind 必须被拒绝")
    must_raise(lambda: _occ(b.node_id, page_number=0),
               "occurrence 页码 0 必须被拒绝")
    must_raise(lambda: _occ(b.node_id, normalized_text=""),
               "空归一文本必须被拒绝")
    must_raise(lambda: _occ(b.node_id, declared_target=""),
               "空 declared_target 必须被拒绝")
    must_raise(lambda: _occ(b.node_id, occurrence_index=-1),
               "负 occurrence_index 必须被拒绝")
    must_raise(lambda: _occ(b.node_id, source_ref="bogus:1"),
               "非法 source_ref 必须被拒绝")
    # 提供真实来源原文时必须能逐字符核对（不得只做长度检查）。
    must_not_raise(lambda: good.verify_source_text("详见第一节标题后缀"),
                   "来源原文一致时 occurrence 偏移核对必须通过")
    must_not_raise(lambda: good.verify_source_text("详见第一节标题"),
                   "来源原文恰为区间文本时 occurrence 偏移核对必须通过")
    must_raise(lambda: good.verify_source_text("详见第二节标题xx"),
               "来源原文不一致时必须被拒绝")

    # -- resolved 边必须绑定 occurrence，且来源必须是真实文本载体 ----------------
    occ = _span_occ(ref_span.span_id)
    kwargs = dict(document_outline_locator=oloc,
                  from_ref=f"span:{ref_span.span_id}",
                  to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
                  resolution_evidence="正文互指文本与目标标题一致")
    resolved = S.ReferenceEdge.create(**kwargs, occurrence=occ)
    check(resolved.is_resolved and resolved.occurrence == occ,
          "resolved 边必须携带 occurrence")
    must_raise(lambda: S.ReferenceEdge.create(**kwargs),
               "resolved cross_reference 缺 occurrence 必须被拒绝", "occurrence")

    # -- 同一来源/类型，occurrence 位置不同 ⇒ 不得合并 ---------------------------
    shifted = _span_occ(ref_span.span_id, char_start=_REF_TEXT_2_START,
                        char_end=_REF_TEXT_2_START + len(_REF_TEXT_1),
                        occurrence_index=1)
    other_pos = S.ReferenceEdge.create(**kwargs, occurrence=shifted)
    check(other_pos.edge_locator != resolved.edge_locator,
          "同一来源的两处真实引用必须得到不同 edge_locator")
    check(other_pos.edge_id != resolved.edge_id,
          "同一来源的两处真实引用必须得到不同 edge_id")
    both = _outline_with(pl, (a, a1, b), edges=(resolved, other_pos), ctx=ctx)
    check(len(both.edges) == 2,
          "同一来源同一目标的两次真实互指必须在同一边集合中共存（不得并成一条）")

    # -- occurrence 必须真的属于该边的来源 --------------------------------------
    must_raise(lambda: S.ReferenceEdge.create(
        **{**kwargs, "from_ref": f"node:{a.node_id}", "to_ref": f"node:{a1.node_id}"},
        occurrence=occ),
        "occurrence.source_ref 与 from_ref 不一致必须被拒绝", "source_ref")
    must_raise(lambda: S.ReferenceEdge.create(
        **kwargs, occurrence=_span_occ(ref_span.span_id,
                                       reference_kind="toc_to_body")),
        "occurrence.reference_kind 与 edge_kind 不一致必须被拒绝", "reference_kind")

    # -- 未解析边：必须带 occurrence 与原因码，且不得编造目标 id ------------------
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence"),
        "未解析 cross_reference 缺 occurrence 必须被拒绝", "occurrence")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}",
        to_ref=f"node:{a.node_id}", edge_kind="cross_reference",
        resolution_evidence=None, reason_code="no_textual_evidence",
        occurrence=good),
        "未解析边不得声称目标 id（不得编造不存在的目标）", "不得声称")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence",
        occurrence=_occ(b.node_id, declared_target=None)),
        "未解析边缺原始声明目标必须被拒绝", "declared_target")
    open_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=_occ(b.node_id))
    check(open_edge.to_ref is None
          and open_edge.occurrence.declared_target == "第一节标题",
          "未解析边必须保留原始声明目标而不编造 id")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{b.node_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="自造原因码", occurrence=_occ(b.node_id)),
        "未解析边用自造原因码必须被拒绝")

    # -- P1-3：定位身份不得随解析结果漂移 ---------------------------------------
    # 同一个 occurrence 由"未解析"变为"已解析"：locator 必须逐字符相同。
    re_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=None, edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=occ)
    check(re_open.edge_locator == resolved.edge_locator,
          "同一 occurrence 未解析 → 已解析必须保持同一 edge_locator")
    check(re_open.edge_id != resolved.edge_id,
          "同一 occurrence 未解析 → 已解析必须得到不同 edge_id")
    # 同一个 occurrence 换目标：locator 相同，revision id 必须不同。
    retargeted = S.ReferenceEdge.create(
        **{**kwargs, "to_ref": f"node:{a1.node_id}"}, occurrence=occ)
    check(retargeted.edge_locator == resolved.edge_locator,
          "同一 occurrence 换目标必须保持同一 edge_locator（定位不得承载解析结果）")
    check(retargeted.edge_id != resolved.edge_id,
          "同一 occurrence 换目标必须得到不同 edge_id")
    # 定位身份不得包含 to_ref / resolution_evidence / reason_code / is_resolved。
    check(resolved.edge_locator == S.derive_reference_edge_locator(
        document_outline_locator=oloc, from_ref=f"span:{ref_span.span_id}",
        to_ref=None, edge_kind="cross_reference", occurrence=occ),
        "edge_locator 不得包含 to_ref（换目标不得改变定位身份）")

    # -- 正式构造路径必须要求真实对象上下文 --------------------------------------
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(resolved,)),
               "已解析边的端点无法在 outline 自持对象内证明时必须要求核验上下文",
               "ReferenceValidationContext")
    ok_outline = _outline_with(pl, (a, a1, b), edges=(resolved,), ctx=ctx)
    vr = ok_outline.verify_references(ctx)
    check(vr.is_verified(resolved.edge_id),
          "提供真实来源 span 后 cross_reference 必须通过对象级核验")
    check(vr.unresolved_edge_ids() == (),
          "本 outline 只有一条已解析边时未解析清单必须为空")
    check(vr.context_fingerprint == ctx.fingerprint()
          and vr.resolver_version == V.REFERENCE_RESOLVER_VERSION,
          "核验结论必须记录核验器版本与上下文指纹")

    # -- toc_to_body：TS1.3 起本层**有**类型化的真实 TOC 来源对象（`TocSource`），
    #    因此不再有"一律不得 resolved"的结构禁令；但"字符串 toc id"仍然不是对象。
    #    （真实对象级可达路径的正反例见 §E `_test_ts13_...`。）
    #    核验上下文**交出真实 PageLayout** 仍然不够：字符串 toc id 依然不是对象。
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(S.ReferenceEdge.create(
            document_outline_locator=oloc, from_ref=f"toc:{_TOC_ITEM}",
            to_ref=f"node:{a.node_id}", edge_kind="toc_to_body",
            resolution_evidence="目录项页码与正文标题一致", occurrence=_toc_occ()),),
        ctx=S.ReferenceValidationContext(layout=pl)),
        "字符串 toc id 不得只凭自报文本与证据产生 resolved 边（即使交出了真实版式）",
        "TocSource")
    toc_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{_TOC_ITEM}", to_ref=None,
        edge_kind="toc_to_body", resolution_evidence=None,
        reason_code="target_object_not_available", occurrence=_toc_occ())
    check(toc_open.to_ref is None
          and toc_open.occurrence.declared_target == "第一节标题",
          "未解析的 toc_to_body 必须保留原文声明目标而不编造目标 id")
    # TS1.4 P1-1：未解析**不等于**来源 occurrence 可以自报。字符串 toc id 配自报
    # 文本与页/行坐标，即使交出了真实版式，也不构成"来源真的在目录页上存在"。
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(toc_open,),
        ctx=S.ReferenceValidationContext(layout=pl)),
        "未解析的 toc_to_body 只有自报 toc id / 文本时必须被拒绝（未解析不等于来源可自报）",
        "TocSource")

    # -- parent_child 是树内关系：可无文本 occurrence，但必须真实且可核验 ---------
    pc = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{a1.node_id}", edge_kind="parent_child",
        resolution_evidence="标题编号与缩进一致")
    check(pc.occurrence is None, "parent_child 允许省略文本 occurrence")
    must_raise(lambda: S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=None, edge_kind="parent_child",
        resolution_evidence=None, reason_code="no_textual_evidence"),
        "parent_child 不得自报未解析（树内关系必须为已解析）", "parent_child")
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(
        S.ReferenceEdge.create(
            document_outline_locator=oloc, from_ref=f"node:{b.node_id}",
            to_ref=f"node:{a1.node_id}", edge_kind="parent_child",
            resolution_evidence="自报父子"),)),
        "parent_child 与实际父子关系不符必须被拒绝", "父子关系")
    must_raise(lambda: _outline_with(pl, (replace(a, child_ids=()), a1, b),
                                     edges=(pc,)),
               "parent_child 与父节点 child_ids 不对称时必须被拒绝", "child_ids")
    pc_outline = _outline_with(pl, (a, a1, b), edges=(pc,))
    check(pc_outline.verify_references().is_verified(pc.edge_id),
          "合法的 parent_child 必须仍能通过（不得一律拒绝已解析）")

    # -- TableObject.continuation_* 与 ReferenceEdge 必须互相印证 -----------------
    t1 = _make_table(pl, table_index_on_page=0)
    t2 = _make_table(pl, table_index_on_page=1, page_number=2,
                     continuation_of_locator=t1.table_locator)
    t1 = _make_table(
        pl, table_index_on_page=0,
        continuation_locators=(t2.table_locator,))
    cont_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc,
        from_ref=f"table:{t1.table_locator}", to_ref=f"table:{t2.table_locator}",
        edge_kind="table_continuation",
        resolution_evidence="表头签名与列边界一致")
    must_not_raise(lambda: t1.verify_continuation_edges((cont_edge,)),
                   "续表出边与表格自报的续接目标一致时必须通过")
    must_not_raise(lambda: t2.verify_continuation_edges((cont_edge,)),
                   "续表入边与表格自报的前置一致时必须通过")
    must_raise(lambda: _make_table(pl, table_index_on_page=0)
               .verify_continuation_edges((cont_edge,)),
               "表格自报无续表关系但图中有出边时必须被拒绝", "续表")
    must_raise(lambda: _make_table(pl, table_index_on_page=1, page_number=2)
               .verify_continuation_edges((cont_edge,)),
               "图中有入边但表格自报无前置时必须被拒绝", "入边")
    must_raise(lambda: t1.verify_continuation_edges(()),
               "表格自报续表但图中无对应边时必须被拒绝", "续表")
    # 续表关系的证据是**对象级**的（两个真实 TableObject 互相印证），不是一段
    # 自报文本：没有真实表对象时不得构造。
    must_raise(lambda: _outline_with(pl, (a, a1, b), edges=(cont_edge,)),
               "没有真实表对象时 table_continuation 不得标为 resolved",
               "ReferenceValidationContext")
    must_raise(lambda: _outline_with(
        pl, (a, a1, b), edges=(cont_edge,),
        ctx=S.ReferenceValidationContext(tables=(t1,))),
        "续表的另一端不在真实对象上下文中时必须被拒绝")
    ok_cont = _outline_with(
        pl, (a, a1, b), edges=(cont_edge,),
        ctx=S.ReferenceValidationContext(tables=(t1, t2)))
    check(ok_cont.verify_references(
        S.ReferenceValidationContext(tables=(t1, t2))).is_verified(cont_edge.edge_id),
        "两端都交出真实 TableObject 后 table_continuation 必须可解析")


# ---------------------------------------------------------------------------
# D. TableObject provenance / 网格 / 续表
# ---------------------------------------------------------------------------

_GRID_TEXTS = (("项目", "金额"), ("营业收入", "100"), ("净利润", "20"))


def _bound_layout(width=600.0, height=800.0, y_base=300.0):
    """构造与表格几何一致的 PageLayout（每行两个 span，落在对应单元格 bbox 内）。

    TS1.4：本组同时要用 `_make_outline(layout)` / `_make_span(layout)`，而共享夹具的
    未解析边带真实 occurrence（落在第 `_REF_SPAN_LINE` 行）。未解析边的来源也必须
    落到真实 `LayoutLine` 上，因此本版式必须是一整页**真实**版式：前 `len(_GRID_TEXTS)`
    行是表格网格行，其余行沿用共享夹具正文（第 `_REF_SPAN_LINE` 行承载真实互指文本）。
    """
    lines = []
    for r in range(_LINE_COUNT):
        i = r
        y = y_base + i * _H
        if i < len(_GRID_TEXTS):
            left, right = _GRID_TEXTS[i]
            text = f"{left} {right}"
            s0 = S.LayoutSpan(text=left, bbox=(100.0, y, 180.0, y + _H), font="SimSun",
                              size=10.5, is_bold=False, char_start=0,
                              char_end=len(left))
            s1 = S.LayoutSpan(text=right, bbox=(180.0, y, 260.0, y + _H),
                              font="SimSun", size=10.5, is_bold=False,
                              char_start=len(left) + 1,
                              char_end=len(left) + 1 + len(right))
            bbox = (100.0, y, 260.0, y + _H)
        else:
            text = _REF_SPAN_TEXT if i == _REF_SPAN_LINE else _line_text(i)
            s0 = S.LayoutSpan(text=text, bbox=(100.0, y, 100.0 + len(text) * 6.0,
                                               y + _H),
                              font="SimSun", size=10.5, is_bold=False,
                              char_start=0, char_end=len(text))
            s1 = None
            bbox = (100.0, y, 100.0 + len(text) * 6.0, y + _H)
        spans = (s0,) if s1 is None else (s0, s1)
        lines.append(S.LayoutLine(line_index=i, bbox=bbox, spans=spans, text=text,
                                  is_furniture=False, furniture_kind=None,
                                  reading_order=i, column_index=0))
    page = S.LayoutPage(page_number=1, width=width, height=height, rotation=0,
                        lines=tuple(lines), has_text_layer=True)
    return S.PageLayout.create(document_id=_DOC_ID, document_version=_DOC_VERSION,
                               company_id=_COMPANY_ID, source_file_sha256=_DOC_SHA,
                               pages=(page,))


def _bound_table(layout, **overrides):
    """在给定 layout 上构造几何一致的 3 行 2 列单页表格。"""
    outline = _make_outline(layout)
    span = _make_span(layout)
    al = _make_alignment(layout)
    grid = []
    for r, pair in enumerate(_GRID_TEXTS):
        for c, text in enumerate(pair):
            y = 300.0 + r * _H
            grid.append(S.TableCell(
                row=r, column=c, rowspan=1, colspan=1, text=text,
                bbox=(100.0 + c * 80.0, y, 180.0 + c * 80.0, y + _H),
                source_locator=(1, r, c)))
    kwargs = dict(
        page_layout_id=layout.page_layout_id,
        document_outline_id=outline.outline_id,
        document_outline_locator=outline.outline_locator,
        node_id=outline.nodes[1].node_id, document_id=_DOC_ID,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, table_index_on_page=0,
        table_bbox=(100.0, 300.0, 260.0, 300.0 + 3 * _H),
        title="示例表题", title_source="caption_line", unit="万元",
        structure_class="ordinary_business_table",
        structure_evidence=("columnar", "data"), column_count=2,
        header_rows=(S.TableRow(row_index=0, kind="header", cells=_GRID_TEXTS[0],
                                label=None),),
        body_rows=(S.TableRow(row_index=1, kind="body", cells=_GRID_TEXTS[1],
                              label=None),
                   S.TableRow(row_index=2, kind="body", cells=_GRID_TEXTS[2],
                              label=None)),
        cell_grid=tuple(grid), component_span_ids=(span.span_id,),
        alignment_ids=(al.alignment_id,),
        component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN),))
    kwargs.update(overrides)
    return S.TableObject.create(**kwargs)


def _test_table_provenance_grid_continuation():
    pl = _make_page_layout()
    layout = _bound_layout()
    table = _bound_table(layout)
    outline = _make_outline(layout)
    span = _make_span(layout)
    al = _make_alignment(layout)

    # -- 单页物理片段模型 --------------------------------------------------------
    check(not hasattr(S.TableObject, "page_range")
          or "page_range" not in S.TableObject.__dataclass_fields__,
          "TableObject 必须是单页片段：不得再携带 page_range")
    check("page_number" in S.TableObject.__dataclass_fields__,
          "TableObject 必须以单一 page_number 表达物理边界")
    check(all(c.source_locator[0] == table.page_number
              for c in table.cell_grid),
          "单页片段的全部单元格必须来自本表页")

    # -- verify_layout：正例与负例 ----------------------------------------------
    must_not_raise(lambda: table.verify_layout(layout),
                   "几何一致时 verify_layout 必须通过")
    must_raise(lambda: table.verify_layout(_make_page_layout()),
               "layout 身份不符时必须被拒绝", "page_layout_id")
    must_raise(lambda: _make_table(pl).verify_layout(pl),
               "单元格 bbox 未覆盖其来源排版片段时必须被拒绝", "来源")
    oversize = _bound_table(layout,
                            table_bbox=(100.0, 300.0, 900.0, 300.0 + 3 * _H))
    must_raise(lambda: oversize.verify_layout(layout),
               "表格 bbox 越出页面 bbox 时必须被拒绝", "页面")

    # -- 单元格几何：bbox 必须落在表 bbox 内 ------------------------------------
    bad_bbox = tuple(replace(c, bbox=(400.0, c.bbox[1], 480.0, c.bbox[3]))
                     if c.row == 0 and c.column == 0 else c
                     for c in table.cell_grid)
    must_raise(lambda: _bound_table(layout, cell_grid=bad_bbox),
               "单元格 bbox 越出表格 bbox 必须被拒绝", "bbo")

    # -- 单元格来源页必须等于本表页 ---------------------------------------------
    bad_page = tuple(replace(c, source_locator=(2, c.source_locator[1],
                                                c.source_locator[2]))
                     for c in table.cell_grid)
    must_raise(lambda: _bound_table(layout, cell_grid=bad_page),
               "单元格来源页与本表页不符必须被拒绝", "来源页")

    # -- provenance 必须是对象级闭合，不得再以任意字符串集合自证 ------------------
    params = set(inspect.signature(S.TableObject.verify_provenance).parameters)
    check("spans" in params and "span_ids" not in params,
          "provenance 必须只接受真实对象集合（不得再有 span_ids 字符串集合入口）")
    must_not_raise(lambda: table.verify_provenance(spans=(span,),
                                                   alignment_records=(al,)),
                   "正常闭合路径必须通过（正例）")
    must_raise(lambda: table.verify_provenance(
        spans=(_make_span(_make_page_layout()),), alignment_records=(al,)),
        "伪造的同一个 id 字符串不得替代真实对象", "OutlineSpan")
    must_raise(lambda: table.verify_provenance(spans=(), alignment_records=(al,)),
               "缺 component span 必须被拒绝")

    # document_id 已进入 span_id，因此"同为该 id 但属于别的文档"无法被伪造；
    # 只能构造确实属于另一文档的 span，provenance 必须拒绝。
    other_doc_span = _make_span(_make_page_layout(document_id="doc-9999"))
    check(other_doc_span.document_id != span.document_id
          and other_doc_span.span_id != span.span_id,
          "跨文档 span 必须连身份都不同（document_id 已进入 span_id）")
    must_raise(lambda: table.verify_provenance(spans=(other_doc_span,),
                                               alignment_records=(al,)),
               "来自另一文档的 span 必须被拒绝（对象级核对）", "另一个文档")

    foreign_al = S.TextAlignmentRecord.create(
        page_layout_id=_make_page_layout().page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=0,
        evidence_block_id=_BLOCK_ID, block_char_length=_BLOCK_LEN,
        char_map=((0, _BLOCK_LEN, 1, 0, 0, 0),))
    must_raise(lambda: table.verify_provenance(
        spans=(span,), alignment_records=(foreign_al,)),
        "来自另一 page_layout 的 alignment 必须被拒绝", "page_layout")
    foreign_set = S.TextAlignmentRecord.create(
        page_layout_id=layout.page_layout_id,
        evidence_set_version="set-other000000", page_number=1, block_index=0,
        evidence_block_id=_BLOCK_ID, block_char_length=_BLOCK_LEN,
        char_map=((0, _BLOCK_LEN, 1, 0, 0, 0),))
    must_raise(lambda: table.verify_provenance(
        spans=(span,), alignment_records=(foreign_set,)),
        "来自另一 evidence set 的 alignment 必须被拒绝")

    # 同一 Evidence ID 但字符区间不同 ⇒ Evidence 引用必须精确闭合。
    ranges_span = replace(
        span, component_evidence_refs=((_BLOCK_ID, 1, _BLOCK_LEN),),
        span_id=S.derive_span_id(
            span_locator=span.span_locator, schema_version=span.schema_version,
            document_id=span.document_id, document_version=span.document_version,
            node_id=span.node_id, role=span.role,
            unassigned_reason=span.unassigned_reason, page_range=span.page_range,
            char_range=span.char_range, layout_line_refs=span.layout_line_refs,
            component_evidence_refs=((_BLOCK_ID, 1, _BLOCK_LEN),),
            alignment_ids=span.alignment_ids, is_fallback=span.is_fallback,
            fallback_derivation=span.fallback_derivation,
            is_cross_heading=span.is_cross_heading, confidence=span.confidence,
            normalized_text=span.normalized_text))
    check(ranges_span.span_id != span.span_id, "字符区间不同的 span 必须不同身份")
    must_raise(lambda: table.verify_provenance(
        spans=(ranges_span,), alignment_records=(al,)),
        "同一 Evidence ID 但字符区间不同必须被拒绝", "字符区间")

    # -- rowspan / colspan 正例与反例 -------------------------------------------
    def _grid_3col(cells, texts, column_count=3):
        rows = tuple(S.TableRow(row_index=i, kind=("header" if i == 0 else "body"),
                                cells=t, label=None)
                     for i, t in enumerate(texts))
        return dict(column_count=column_count, header_rows=(rows[0],),
                    body_rows=rows[1:], cell_grid=tuple(cells),
                    table_bbox=(100.0, 300.0, 340.0, 300.0 + 3 * _H))

    def _cell(r, c, rs, cs, text):
        y = 300.0 + r * _H
        return S.TableCell(row=r, column=c, rowspan=rs, colspan=cs, text=text,
                           bbox=(100.0 + c * 80.0, y, 100.0 + (c + cs) * 80.0, y + rs * _H),
                           source_locator=(1, r, 0))

    # 合法 rowspan（第 0 行跨 2 行，第 1 行不再重复该单元格）
    rowspan_cells = (_cell(0, 0, 2, 1, "甲"), _cell(0, 1, 1, 1, "乙"),
                     _cell(0, 2, 1, 1, "丙"), _cell(1, 1, 1, 1, "丁"),
                     _cell(1, 2, 1, 1, "戊"), _cell(2, 0, 1, 1, "己"),
                     _cell(2, 1, 1, 1, "庚"), _cell(2, 2, 1, 1, "辛"))
    must_not_raise(lambda: _bound_table(
        layout, **_grid_3col(rowspan_cells, (("甲", "乙", "丙"), ("丁", "戊"),
                                             ("己", "庚", "辛")))),
        "合法 rowspan（后续行不重复被续接的单元格）必须可构造")

    # 合法 colspan
    colspan_cells = (_cell(0, 0, 1, 2, "表头"), _cell(0, 2, 1, 1, "丙"),
                     _cell(1, 0, 1, 1, "甲"), _cell(1, 1, 1, 1, "乙"),
                     _cell(1, 2, 1, 1, "丙2"), _cell(2, 0, 1, 1, "己"),
                     _cell(2, 1, 1, 1, "庚"), _cell(2, 2, 1, 1, "辛"))
    must_not_raise(lambda: _bound_table(
        layout, **_grid_3col(colspan_cells, (("表头", "丙"), ("甲", "乙", "丙2"),
                                             ("己", "庚", "辛")))),
        "合法 colspan 必须可构造")

    # rowspan + colspan 组合
    combo_cells = (_cell(0, 0, 2, 2, "合并"), _cell(0, 2, 1, 1, "丙"),
                   _cell(1, 2, 1, 1, "丙2"), _cell(2, 0, 1, 1, "己"),
                   _cell(2, 1, 1, 1, "庚"), _cell(2, 2, 1, 1, "辛"))
    must_not_raise(lambda: _bound_table(
        layout, **_grid_3col(combo_cells, (("合并", "丙"), ("丙2",),
                                           ("己", "庚", "辛")))),
        "rowspan + colspan 组合必须可构造")

    # rowspan 造成空洞
    hole_cells = (_cell(0, 0, 2, 2, "合并"), _cell(0, 2, 1, 1, "丙"),
                  _cell(2, 0, 1, 1, "己"), _cell(2, 1, 1, 1, "庚"),
                  _cell(2, 2, 1, 1, "辛"))
    must_raise(lambda: _bound_table(
        layout, **_grid_3col(hole_cells, (("合并", "丙"), (), ("己", "庚", "辛")))),
        "rowspan 造成空洞必须被拒绝", "空洞")
    # rowspan 造成重叠
    overlap_cells = tuple(rowspan_cells) + (_cell(1, 0, 1, 1, "重复"),)
    must_raise(lambda: _bound_table(
        layout, **_grid_3col(overlap_cells, (("甲", "乙", "丙"), ("重复", "丁", "戊"),
                                             ("己", "庚", "辛")))),
        "rowspan 造成重叠必须被拒绝", "重叠")
    # 「本行起始单元格 colspan 之和 = column_count」这条错误规则必须已删除。
    src = _read_module("schema.py")
    check("列跨度之和" not in src,
          "必须删除「本行起始单元格列跨度之和等于 column_count」这一错误规则")

    # -- 续表：方向、自指、跨文档 ------------------------------------------------
    # 前置片段在第 1 页、后继片段在第 2 页，两页各自是独立的单页物理片段。
    t1 = _make_table(pl, table_index_on_page=0)
    t2 = _make_table(pl, table_index_on_page=1, page_number=2,
                     continuation_of_locator=t1.table_locator)
    t1 = _make_table(pl, table_index_on_page=0,
                     continuation_locators=(t2.table_locator,))
    must_not_raise(lambda: t1.verify_continuation_fragments((t1, t2)),
                   "同文档两页单页片段构成的续表必须可核验（前置侧）")
    must_not_raise(lambda: t2.verify_continuation_fragments((t1, t2)),
                   "同文档两页单页片段构成的续表必须可核验（后继侧）")
    must_raise(lambda: t1.verify_continuation_fragments((t1,)),
               "续表目标片段缺失时必须被拒绝", "缺失")

    # 自指：continuation_* 指向自身必须在构造期即被拒绝。
    #
    # 这里必须用 **legacy** builder 版本：本模块的 `_make_table` 构造的是历史
    # `to-3` 的 `TableObject`，其 locator 由 `tb-1` 派生。TS5 之后
    # `TABLE_BUILDER_VERSION` 已升到 `tb-2`（新的 `TableObjectV4` 用），拿它来复算
    # 历史对象的 locator 会得到一个**不同**的定位串——自指反例就退化成"指向了另一张
    # 表"，测试看起来还在跑，但已经不再验证自指这条性质。
    self_loc = S.derive_table_locator(
        page_layout_id=pl.page_layout_id, page_number=1, table_index_on_page=0,
        table_bbox=(100.0, 300.0, 260.0, 300.0 + 3 * _H),
        table_builder_version=V.LEGACY_TABLE_BUILDER_VERSION)
    check(self_loc == t1.table_locator,
          "自指反例用的定位串必须**恰好**是该表自己的 locator（否则验的不是自指）")
    must_raise(lambda: _make_table(pl, continuation_locators=(self_loc,)),
               "续表指向自身必须在构造期被拒绝", "自身")

    # 页序倒置：前置与后继都必须严格早于／晚于本表页。
    must_raise(lambda: _make_table(pl, table_index_on_page=1, page_number=1,
                                   continuation_of_locator=t1.table_locator)
               .verify_continuation_fragments((t1,)),
               "前置片段不在更早页时必须被拒绝（页序倒置）", "页序倒置")
    must_raise(lambda: _make_table(pl, table_index_on_page=1, page_number=2,
                                   continuation_locators=(t1.table_locator,))
               .verify_continuation_fragments((t1,)),
               "后继片段不在更晚页时必须被拒绝（页序倒置）", "页序倒置")

    # 跨文档：定位只用于导航，不得指向另一文档的片段。
    foreign_layout = _make_page_layout(document_id="doc-9999")
    foreign = _make_table(foreign_layout, table_index_on_page=1, page_number=2)
    cross = _make_table(pl, table_index_on_page=0,
                        continuation_locators=(foreign.table_locator,))
    foreign = _make_table(foreign_layout, table_index_on_page=1, page_number=2,
                          continuation_of_locator=cross.table_locator)
    must_raise(lambda: cross.verify_continuation_fragments((cross, foreign)),
               "续表指向另一文档的片段必须被拒绝", "跨文档")

    # -- 冻结阈值（0.90）下的正例：完整闭合的正常 provenance 路径仍必须为 True ----
    check(V.ALIGN_MIN == 0.90,
          "TS2 最终关闭轮后 ALIGN_MIN 必须已由用户 + Codex 冻结为 0.90")
    check(al.verdict == "aligned" and al.is_citable() is True,
          "冻结阈值 0.90 下全覆盖且无残差的记录必须 aligned 且可引证")
    check(table.is_evidence_backed(spans=(span,), alignment_records=(al,)) is True,
          "完整闭合且全部可引证时必须为 True（正例）")

    # -- 反例：阈值确实进入 verdict，并由此进入 alignment / span 身份 -------------
    # coverage = quantize(5/9) ≈ 0.556；残差主类是 `page_furniture`（**不是**
    # `unexplained`），因此它只用来说明"低于阈值 ⇒ unaligned 不可引用"，不涉及残差类别。
    half_kwargs = dict(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1,
        block_index=0, evidence_block_id=_BLOCK_ID, block_char_length=_BLOCK_LEN,
        residue=((5, _BLOCK_LEN, "page_furniture"),),
        char_map=((0, 5, 1, 0, 0, 0),))
    half = S.TextAlignmentRecord.create(**half_kwargs)
    check(half.residue_class == "page_furniture",
          "反例前置条件：残差主类必须是 page_furniture（非 unexplained）")
    check(half.verdict == "unaligned" and half.is_citable() is False,
          f"冻结阈值 0.90 下低于阈值必须 unaligned 且不可引用（得到 {half.verdict}）")

    def _rebind(alignment):
        """把共享夹具的 body span 与表格重新绑定到给定 alignment 上。"""
        bound_span = S.OutlineSpan.create(
            document_outline_locator=_outline_locator(layout),
            node_id=_make_outline(layout).nodes[1].node_id,
            document_id=layout.document_id, document_version=_DOC_VERSION,
            evidence_set_version=_EVIDENCE_SET_VERSION, role="body",
            start_anchor=_anchor(2), end_anchor=_anchor(3),
            normalized_text=_line_text(2) + _line_text(3),
            layout_line_refs=((1, 2), (1, 3)),
            component_evidence_refs=((_BLOCK_ID, 0, _BLOCK_LEN),),
            alignment_ids=(alignment.alignment_id,), confidence=1.0)
        bound_table = _bound_table(layout,
                                   component_span_ids=(bound_span.span_id,),
                                   alignment_ids=(alignment.alignment_id,))
        return bound_span, bound_table

    half_span, half_table = _rebind(half)
    check(half_table.is_evidence_backed(
        spans=(half_span,), alignment_records=(half,)) is False,
        "低于冻结阈值时 is_evidence_backed 必须为 False（不可引用）")

    original_min = V.ALIGN_MIN
    try:
        V.ALIGN_MIN = 0.50
        lowered = S.TextAlignmentRecord.create(**half_kwargs)
        check(lowered.verdict == "aligned" and lowered.is_citable() is True,
              f"阈值降到 0.50 时同一记录必须改判 aligned 且可引用（得到 {lowered.verdict}）")
        check(lowered.alignment_locator == half.alignment_locator
              and lowered.alignment_id != half.alignment_id,
              "阈值只改变 revision 身份（alignment_id），不改变稳定定位身份")
        lowered_span, lowered_table = _rebind(lowered)
        check(lowered_span.span_id != half_span.span_id,
              "alignment 身份变了，引用它的 span 身份必须随之改变（不得跨阈值复用旧 span）")
        check(lowered_table.is_evidence_backed(
            spans=(lowered_span,), alignment_records=(lowered,)) is True,
            "阈值降低后同一闭合 provenance 必须转为 True（阈值确实参与判定）")
    finally:
        V.ALIGN_MIN = original_min
    check(V.ALIGN_MIN == 0.90, "测试必须还原 ALIGN_MIN 为生产冻结值 0.90")
    check(span.span_id != half_span.span_id,
          "半覆盖记录派生出的 span 必须与共享夹具 span 不同（身份可区分）")


# ---------------------------------------------------------------------------
# E. TS1.3：引用 occurrence 坐标与 TOC 可达性定点修复
# ---------------------------------------------------------------------------
#
# 三组定点：
#   P1-A 取消"没有核验上下文就只检查端点存在性"的旁路：所有 `is_resolved=True`
#        的边都必须走同一对象级核验入口，`create()` 与 `from_dict()` 共用它；
#   P1-B occurrence 的 `(page_number, line_index)` 必须精确定位一条**真实
#        `LayoutLine`**，`char_start` / `char_end` 是该 `LayoutLine.text` **行内**的
#        字符区间（不再是整个 span 全文的全局偏移），切片必须**逐字符**相等；
#   P1-C 取消 `toc_to_body` 的永久结构禁令：目录项必须绑定真实 `TocSource` 对象
#        （由真实 PageLayout 上的真实 LayoutLine 重算身份），证据不足时保持未解析。
#
# 本组只用真实构造与真实核验，不扫描函数名 / 注释 / 常量（唯一两条结构性断言
# 在 §六 之外，用 `hasattr` 明确标注）。

_TS13_X0, _TS13_Y0, _TS13_H = 40.0, 60.0, 12.0
_TS13_W, _TS13_HT = 620.0, 800.0

# 三页真实版式：正文两页（各含页码 furniture）+ 目录一页。
_P1_LINES = (
    ("第一节标题 详见第二节标题", None),   # 0  node A 标题行（标题区域 = 前 5 字符）
    ("第一段正文内容", None),              # 1  span S_ML 首行
    ("参见第一节标题", None),              # 2  span S_ML 次行（引用所在行）
    ("第三段正文内容", None),              # 3
    ("第二节标题", None),                  # 4  node B 标题行
    ("第三段正文内容", None),              # 5  span S_XP 首页行
    ("第 1 页", "page_number"),            # 6  页码 furniture
)
_P2_LINES = (
    ("参见第二节标题", None),              # 0  span S_XP 次页行（引用所在行）
    ("第四节正文内容", None),              # 1
    ("第三节标题", None),                  # 2  node C 标题行
    ("第五节正文内容", None),              # 3
    ("第六节标题", None),                  # 4  node D 标题行
    ("第 2 页", "page_number"),            # 5  页码 furniture
)
_P3_LINES = (
    ("目 录", None),                       # 0
    ("第一节标题 ......... 1", None),      # 1  → node A，页标签 1 → 物理页 1
    ("第二节标题 ......... 2", None),      # 2  → node B，页标签 2 → 物理页 2（与 B 的页不符）
    ("第三节标题 ......... 2", None),      # 3  → node C，页标签 2 → 物理页 2
    ("第六节标题 ......... 2", None),      # 4  → node D，页标签 2 → 物理页 2
    ("第六节标题 ......... 9", None),      # 5  页标签 9：真实版式上无任何页可证明
    ("第 3 页", "page_number"),            # 6  页码 furniture
)
_TS13_LINES = {1: _P1_LINES, 2: _P2_LINES, 3: _P3_LINES}


def _ts13_text(page_number, line_index):
    return _TS13_LINES[page_number][line_index][0]


def _ts13_line(page_number, line_index):
    text, furniture = _TS13_LINES[page_number][line_index]
    y = _TS13_Y0 + line_index * _TS13_H
    bbox = (_TS13_X0, y, _TS13_X0 + len(text) * 6.0, y + _TS13_H)
    sp = S.LayoutSpan(text=text, bbox=bbox, font="SimSun", size=10.5, is_bold=False,
                      char_start=0, char_end=len(text))
    return S.LayoutLine(line_index=line_index, bbox=bbox, spans=(sp,), text=text,
                        is_furniture=furniture is not None, furniture_kind=furniture,
                        reading_order=line_index, column_index=0)


def _ts13_layout(document_id=_DOC_ID):
    pages = []
    for pn in (1, 2, 3):
        pages.append(S.LayoutPage(
            page_number=pn, width=_TS13_W, height=_TS13_HT, rotation=0,
            lines=tuple(_ts13_line(pn, i) for i in range(len(_TS13_LINES[pn]))),
            has_text_layer=True))
    return S.PageLayout.create(
        document_id=document_id, document_version=_DOC_VERSION, company_id=_COMPANY_ID,
        source_file_sha256=_DOC_SHA, pages=tuple(pages))


def _ts13_anchor(page_number, line_index, chars=None):
    """锚点 bbox 落在真实 LayoutLine 内（`chars=None` 时等于整行）。"""
    text = _ts13_text(page_number, line_index)
    n = len(text) if chars is None else chars
    y = _TS13_Y0 + line_index * _TS13_H
    return (page_number, line_index, (_TS13_X0, y, _TS13_X0 + n * 6.0, y + _TS13_H))


def _ts13_oloc(layout):
    return S.derive_document_outline_locator(
        page_layout_id=layout.page_layout_id, document_id=layout.document_id,
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)


def _ts13_nodes(oloc):
    def mk(title, page_number, line_index, ordinal, chars=None):
        return S.OutlineNode.create(
            document_outline_locator=oloc, parent_id=None, title=title,
            title_normalized=title, structural_path=(title,),
            source_anchor=_ts13_anchor(page_number, line_index, chars=chars),
            ordinal=ordinal)
    return (mk("第一节标题", 1, 0, 0, chars=5), mk("第二节标题", 1, 4, 1),
            mk("第三节标题", 2, 2, 2), mk("第六节标题", 2, 4, 3))


def _ts13_spans(oloc, nodes):
    """两段真实正文 span：S_ML 同页两行、S_XP 跨页两行（引用都**不在**首行）。"""
    owner = nodes[0].node_id
    ml = S.OutlineSpan.create(
        document_outline_locator=oloc, node_id=owner, document_id=_DOC_ID,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        role="body", start_anchor=_ts13_anchor(1, 1), end_anchor=_ts13_anchor(1, 2),
        normalized_text=_ts13_text(1, 1) + _ts13_text(1, 2),
        layout_line_refs=((1, 1), (1, 2)), confidence=1.0)
    xp = S.OutlineSpan.create(
        document_outline_locator=oloc, node_id=owner, document_id=_DOC_ID,
        document_version=_DOC_VERSION, evidence_set_version=_EVIDENCE_SET_VERSION,
        role="body", start_anchor=_ts13_anchor(1, 5), end_anchor=_ts13_anchor(2, 0),
        normalized_text=_ts13_text(1, 5) + _ts13_text(2, 0),
        layout_line_refs=((1, 5), (2, 0)), confidence=1.0)
    return ml, xp


def _ts13_toc(layout, line_index, label):
    """由**真实**目录页 LayoutLine 重算身份的目录项来源对象。"""
    text = _ts13_text(3, line_index)
    return S.TocSource.create(
        layout=layout, page_number=3, line_index=line_index, char_start=0,
        char_end=len(text), declared_page_label=label)


def _ts13_occ(source_ref, page_number, line_index, char_start, char_end, text,
              marker, declared_target, kind="cross_reference", **kw):
    base = dict(source_ref=source_ref, page_number=page_number, line_index=line_index,
                char_start=char_start, char_end=char_end, occurrence_index=0,
                reference_marker=marker, reference_kind=kind,
                declared_target=declared_target, normalized_text=text)
    base.update(kw)
    return S.ReferenceOccurrence(**base)


def _ts13_outline(layout, nodes, edges=(), unassigned=(), ctx=None):
    return S.DocumentOutline.create(
        document_id=layout.document_id, document_version=_DOC_VERSION,
        page_layout_id=layout.page_layout_id, nodes=nodes, edges=edges,
        unassigned=unassigned, candidate_sources=("toc_page",), reference_context=ctx)


def _ts13_wire(layout, nodes, edges=(), unassigned=(), sources=("toc_page",)):
    """按派生身份手工拼出 wire format（用于 from_dict 反例）。"""
    loc = _ts13_oloc(layout)
    src = tuple(sources)
    fp = S.derive_outline_content_fingerprint(
        outline_locator=loc, document_version=_DOC_VERSION, nodes=nodes, edges=edges,
        unassigned=unassigned, candidate_sources=src)
    return {
        "schema_type": "DocumentOutline", "outline_locator": loc,
        "outline_id": "do-" + fp[:16], "document_id": layout.document_id,
        "document_version": _DOC_VERSION, "page_layout_id": layout.page_layout_id,
        "algorithm_version": V.OUTLINE_ALGORITHM_VERSION,
        "schema_version": V.OUTLINE_SCHEMA_VERSION,
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "unassigned": [s.to_dict() for s in unassigned],
        "candidate_sources": list(src), "content_fingerprint": fp,
    }


def _test_ts13_reference_coordinates_and_toc():
    layout = _ts13_layout()
    other_layout = _ts13_layout(document_id="doc-other")
    oloc = _ts13_oloc(layout)
    a, b, c, d = nodes = _ts13_nodes(oloc)
    ml, xp = _ts13_spans(oloc, nodes)
    toc_a = _ts13_toc(layout, 1, "1")     # 第一节标题 → 页标签 1
    toc_b = _ts13_toc(layout, 2, "2")     # 第二节标题 → 页标签 2
    toc_c = _ts13_toc(layout, 3, "2")     # 第三节标题 → 页标签 2
    toc_d = _ts13_toc(layout, 4, "2")     # 第六节标题 → 页标签 2
    toc_9 = _ts13_toc(layout, 5, "9")     # 页标签 9：真实版式上无对应页码
    ctx_ml = S.ReferenceValidationContext(spans=(ml,), layout=layout)
    ctx_xp = S.ReferenceValidationContext(spans=(xp,), layout=layout)
    ctx_toc = S.ReferenceValidationContext(
        toc_sources=(toc_a, toc_b, toc_c, toc_d, toc_9), layout=layout)

    # -- §六.1 / §六.2：原始伪造 occurrence 必须在构造边界直接失败 ----------------
    forged = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="self asserted",
        occurrence=_ts13_occ(f"node:{a.node_id}", 999, 999, 0, len("详见伪造目标"),
                             "详见伪造目标", "详见", "伪造目标"))
    check(forged.is_resolved and forged.occurrence.page_number == 999,
          "§六.1 伪造 occurrence 的边在**边层**仍只是声明（is_resolved 只是声明）")
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(forged,)),
               "§六.1 page=999/line=999 的伪造 node→node occurrence 必须在 "
               "DocumentOutline.create 边界被拒绝（不得等调用者事后 verify）",
               "ReferenceValidationContext")
    must_raise(lambda: S.DocumentOutline.from_dict(
        _ts13_wire(layout, nodes, edges=(forged,))),
        "§六.2 同一伪造对象经 from_dict 也必须被拒绝", "ReferenceValidationContext")

    # -- §六.4 / §六.5 / §六.6 / §六.7 / §六.8：行内坐标语义 --------------------
    ok_ml_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="正文互指文字落在真实 LayoutLine 上",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 2, 0, len(_ts13_text(1, 2)),
                             _ts13_text(1, 2), "参见", "第一节标题"))
    ok_ml = _ts13_outline(layout, nodes, edges=(ok_ml_edge,), ctx=ctx_ml)
    check(ok_ml.verify_references(ctx_ml).is_verified(ok_ml_edge.edge_id),
          "§六.4 span 覆盖两行、occurrence 位于第二行时必须通过")

    ok_xp_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{xp.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="跨页 span 的引用出现在后一页",
        occurrence=_ts13_occ(f"span:{xp.span_id}", 2, 0, 0, len(_ts13_text(2, 0)),
                             _ts13_text(2, 0), "参见", "第二节标题"))
    ok_xp = _ts13_outline(layout, nodes, edges=(ok_xp_edge,), ctx=ctx_xp)
    check(ok_xp.verify_references(ctx_xp).is_verified(ok_xp_edge.edge_id),
          "§六.5 span 跨两页、occurrence 位于后一页时必须通过")

    off_refs = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="自报落在该 span 的行上",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 3, 0, len(_ts13_text(1, 3)),
                             _ts13_text(1, 3), _ts13_text(1, 3), None))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(off_refs,), ctx=ctx_ml),
               "§六.6 occurrence 所在行不属于 span.layout_line_refs 时必须被拒绝",
               "layout_line_refs")

    bad_slice = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="自报文本与真实行不符",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 2, 0, len("详见第一节标题"),
                             "详见第一节标题", "详见", "第一节标题"))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(bad_slice,), ctx=ctx_ml),
               "§六.7 occurrence 页/行存在但字符切片与真实 LayoutLine 不一致时必须被拒绝",
               "逐字符")

    n1, n2 = len(_ts13_text(1, 1)), len(_ts13_text(1, 2))
    global_off = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="用 span 全文全局偏移冒充行内偏移",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 2, n1, n1 + n2,
                             _ts13_text(1, 2), "参见", "第一节标题"))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(global_off,), ctx=ctx_ml),
               "§六.8 用 span 全局偏移冒充行内偏移必须被拒绝", "不足")

    # -- §六.9 / §六.10 / §六.11：正例（防止"一律拒绝已解析边"也能过关） ---------
    node_occ = _ts13_occ(f"node:{a.node_id}", 1, 0, 0, len(_ts13_text(1, 0)),
                         _ts13_text(1, 0), "详见", "第二节标题")
    node_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{a.node_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="标题行内的互指文字落在真实 LayoutLine 上",
        occurrence=node_occ)
    ctx_layout_only = S.ReferenceValidationContext(layout=layout)
    ok_node = _ts13_outline(layout, nodes, edges=(node_edge,), ctx=ctx_layout_only)
    check(ok_node.verify_references(ctx_layout_only).is_verified(node_edge.edge_id),
          "§六.9 合法的 node 标题 occurrence 必须通过")

    # §六.10：`parent_child` 是纯树内关系，不带文本 occurrence，因此**空上下文**即可
    # 核验（TS1.4 只让"带真实 occurrence 的来源"必须落到真实版式上）。
    base_pl = _make_page_layout()
    base_outline = _make_outline(base_pl)
    plain_outline = _outline_with(base_pl, base_outline.nodes,
                                  edges=(base_outline.edges[0],))
    check(plain_outline.verify_references().is_verified(base_outline.edges[0].edge_id),
          "§六.10 合法 parent_child（无文本 occurrence）在无核验上下文时必须继续通过")

    pl0 = _make_page_layout()
    t1 = _make_table(pl0, table_index_on_page=0)
    t2 = _make_table(pl0, table_index_on_page=1, page_number=2,
                     continuation_of_locator=t1.table_locator)
    t1 = _make_table(pl0, table_index_on_page=0,
                     continuation_locators=(t2.table_locator,))
    cont_edge = S.ReferenceEdge.create(
        document_outline_locator=_make_outline(pl0).outline_locator,
        from_ref=f"table:{t1.table_locator}", to_ref=f"table:{t2.table_locator}",
        edge_kind="table_continuation", resolution_evidence="表头签名与列边界一致")
    ctx_tbl = S.ReferenceValidationContext(tables=(t1, t2))
    ok_cont = S.DocumentOutline.create(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        page_layout_id=pl0.page_layout_id, nodes=_make_outline(pl0).nodes,
        edges=(cont_edge,), unassigned=_make_outline(pl0).unassigned,
        candidate_sources=("body_numbering",), reference_context=ctx_tbl)
    check(ok_cont.verify_references(ctx_tbl).is_verified(cont_edge.edge_id),
          "§六.11 合法 table_continuation 必须继续通过")

    # -- §六.3：核验上下文的 PageLayout 必须与本 outline 的对象身份一致 ----------
    must_raise(lambda: _ts13_outline(
        layout, nodes, edges=(ok_ml_edge,),
        ctx=S.ReferenceValidationContext(spans=(ml,), layout=other_layout)),
        "§六.3 提供错误 PageLayout 的核验上下文必须被拒绝", "PageLayout")

    # -- §六.12 ~ §六.15：TOC 的对象级可达路径 ----------------------------------
    toc_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_a.toc_source_id}",
        to_ref=f"node:{a.node_id}", edge_kind="toc_to_body",
        resolution_evidence="目录项来源在真实 LayoutLine 上，标题与页码映射一致",
        occurrence=_ts13_occ(f"toc:{toc_a.toc_source_id}", 3, 1, 0,
                             len(_ts13_text(3, 1)), _ts13_text(3, 1),
                             "第一节标题", "第一节标题", kind="toc_to_body"))
    ok_toc = _ts13_outline(layout, nodes, edges=(toc_edge,), ctx=ctx_toc)
    check(ok_toc.verify_references(ctx_toc).is_verified(toc_edge.edge_id),
          "§六.12 真实 PageLayout 目录行 → 真实 OutlineNode 的 toc_to_body "
          "必须存在 resolved 正例（TS2/TS3 不需要再改公共 schema）")

    forged_toc_ref = "toc:toc-ffffffffffffffff"
    forged_toc_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=forged_toc_ref,
        to_ref=f"node:{a.node_id}", edge_kind="toc_to_body",
        resolution_evidence="自报与真实目录项同值",
        occurrence=_ts13_occ(forged_toc_ref, 3, 1, 0, len(_ts13_text(3, 1)),
                             _ts13_text(3, 1), "第一节标题", "第一节标题",
                             kind="toc_to_body"))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(forged_toc_edge,),
                                     ctx=ctx_toc),
               "§六.13 伪 toc source id + 相同自报文本必须被拒绝", "TocSource")

    toc_title_bad = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_c.toc_source_id}",
        to_ref=f"node:{d.node_id}", edge_kind="toc_to_body",
        resolution_evidence="自报标题一致",
        occurrence=_ts13_occ(f"toc:{toc_c.toc_source_id}", 3, 3, 0,
                             len(_ts13_text(3, 3)), _ts13_text(3, 3),
                             "第三节标题", "第三节标题", kind="toc_to_body"))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(toc_title_bad,),
                                     ctx=ctx_toc),
               "§六.14 目录行声明标题与目标节点标题不一致时必须被拒绝", "标题")

    toc_page_bad = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_b.toc_source_id}",
        to_ref=f"node:{b.node_id}", edge_kind="toc_to_body",
        resolution_evidence="自报页码映射一致",
        occurrence=_ts13_occ(f"toc:{toc_b.toc_source_id}", 3, 2, 0,
                             len(_ts13_text(3, 2)), _ts13_text(3, 2),
                             "第二节标题", "第二节标题", kind="toc_to_body"))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(toc_page_bad,),
                                     ctx=ctx_toc),
               "§六.15 目录项页标签映射到与目标节点真实页不符的物理页时必须被拒绝",
               "物理页")

    toc_no_map = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_9.toc_source_id}",
        to_ref=f"node:{d.node_id}", edge_kind="toc_to_body",
        resolution_evidence="自报页码可映射",
        occurrence=_ts13_occ(f"toc:{toc_9.toc_source_id}", 3, 5, 0,
                             len(_ts13_text(3, 5)), _ts13_text(3, 5),
                             "第六节标题", "第六节标题", kind="toc_to_body"))
    must_raise(lambda: _ts13_outline(layout, nodes, edges=(toc_no_map,), ctx=ctx_toc),
               "§六.15 页标签在真实版式上无法证明为任何物理页码时必须被拒绝",
               "toc_page_label_mapping_unproven")
    # 同上但由调用方显式保持未解析：必须可构造，并出现在未解析清单里。
    toc_kept_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_9.toc_source_id}",
        to_ref=None, edge_kind="toc_to_body", resolution_evidence=None,
        reason_code="toc_page_label_mapping_unproven",
        occurrence=_ts13_occ(f"toc:{toc_9.toc_source_id}", 3, 5, 0,
                             len(_ts13_text(3, 5)), _ts13_text(3, 5),
                             "第六节标题", "第六节标题", kind="toc_to_body"))
    open_outline = _ts13_outline(layout, nodes, edges=(toc_kept_open,), ctx=ctx_toc)
    check(open_outline.verify_references(ctx_toc).unresolved_edge_ids()
          == (toc_kept_open.edge_id,),
          "§六.15 页码映射无法证明时 toc_to_body 必须能保持未解析并给出稳定原因码")
    # 同一目录项的第二个正例：页标签 2 → 物理页 2，目标 node C 正在第 2 页。
    toc_edge_c = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_c.toc_source_id}",
        to_ref=f"node:{c.node_id}", edge_kind="toc_to_body",
        resolution_evidence="目录项来源在真实 LayoutLine 上，标题与页码映射一致",
        occurrence=_ts13_occ(f"toc:{toc_c.toc_source_id}", 3, 3, 0,
                             len(_ts13_text(3, 3)), _ts13_text(3, 3),
                             "第三节标题", "第三节标题", kind="toc_to_body"))
    toc_edge_d = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_d.toc_source_id}",
        to_ref=f"node:{d.node_id}", edge_kind="toc_to_body",
        resolution_evidence="目录项来源在真实 LayoutLine 上，标题与页码映射一致",
        occurrence=_ts13_occ(f"toc:{toc_d.toc_source_id}", 3, 4, 0,
                             len(_ts13_text(3, 4)), _ts13_text(3, 4),
                             "第六节标题", "第六节标题", kind="toc_to_body"))
    toc_multi = _ts13_outline(layout, nodes, edges=(toc_edge_c, toc_edge_d),
                              ctx=ctx_toc)
    vr_toc = toc_multi.verify_references(ctx_toc)
    check(vr_toc.is_verified(toc_edge_c.edge_id)
          and vr_toc.is_verified(toc_edge_d.edge_id),
          "§六.12 TOC 来源身份必须由真实 LayoutLine 重算（不同目录行 → 不同来源）")

    # -- §六.16：上下文多出未使用的 proof 不得改变 Outline 身份 ------------------
    # 表格 proof 刻意取**本组版式之外**的一份真实 TableObject：它不被任何边使用，
    # 只用来证明"多出的 proof 不改变身份"（TS1.4 起 `_make_outline` 也必须在真实版式
    # 上核验来源，因此不能拿本组三页版式去套另一套夹具的正文行）。
    wide_ctx = S.ReferenceValidationContext(
        spans=(ml, xp), tables=(_make_table(_make_page_layout()),),
        toc_sources=(toc_a, toc_b, toc_c, toc_d, toc_9), layout=layout)
    narrow = _ts13_outline(layout, nodes, edges=(ok_ml_edge,), ctx=ctx_ml)
    wide = _ts13_outline(layout, nodes, edges=(ok_ml_edge,), ctx=wide_ctx)
    check(canonical_json(narrow.to_dict()) == canonical_json(wide.to_dict())
          and narrow.outline_id == wide.outline_id
          and narrow.content_fingerprint == wide.content_fingerprint
          and narrow.outline_locator == wide.outline_locator,
          "§六.16 核验上下文增加未使用的 layout / span / table / toc proof "
          "不得改变 Outline JSON / fingerprint / ID")
    for key in ("reference_context", "toc_sources", "context_fingerprint",
                "page_layout"):
        check(key not in wide.to_dict(),
              f"§六.16 wire format 不得出现核验上下文相关字段 {key!r}")

    # -- §六.17 / §六.18：定位身份与修订身份的归属 -------------------------------
    re_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=ok_ml_edge.occurrence)
    check(re_open.edge_locator == ok_ml_edge.edge_locator
          and re_open.edge_id != ok_ml_edge.edge_id,
          "§六.17 同一 occurrence 未解析 → 已解析：locator 相同、edge ID 不同")

    first_line_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="另一处真实引用",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 1, 0, len(_ts13_text(1, 1)),
                             _ts13_text(1, 1), _ts13_text(1, 1), None))
    check(first_line_edge.edge_locator != ok_ml_edge.edge_locator
          and first_line_edge.edge_id != ok_ml_edge.edge_id,
          "§六.18 两个不同 LayoutLine 的 occurrence：locator 与 edge ID 均不同")
    ok_both = _ts13_outline(layout, nodes, edges=(ok_ml_edge, first_line_edge),
                            ctx=ctx_ml)
    vr_both = ok_both.verify_references(ctx_ml)
    check(vr_both.is_verified(ok_ml_edge.edge_id)
          and vr_both.is_verified(first_line_edge.edge_id),
          "§六.18 同一 span 内两行的两处真实引用必须都能被核验")

    # -- §六.19：schema 版本与 legacy 行为 --------------------------------------
    check(V.classify_schema_version("REFERENCE_EDGE_SCHEMA_VERSION", "res-3")
          == "legacy",
          "§六.19 旧 occurrence 坐标语义版本 res-3 必须被登记为 legacy")
    check(V.classify_schema_version("REFERENCE_EDGE_SCHEMA_VERSION",
                                    V.REFERENCE_EDGE_SCHEMA_VERSION) == "current",
          "§六.19 当前 reference edge 版本必须被分类为 current")
    must_raise(lambda: S.ReferenceEdge.from_dict(
        {**ok_ml_edge.to_dict(), "schema_version": "res-3"}),
        "§六.19 旧格式不得静默按新坐标语义读取", "旧 wire format")

    # -- §五：VerifiedReferences 的信任边界 -------------------------------------
    vr_ml = narrow.verify_references(ctx_ml)
    check(hasattr(S.VerifiedReferences, "assert_reproducible"),
          "§五.3 下游必须能用 Outline + context 重算比对（不得只做 isinstance）")
    must_not_raise(lambda: vr_ml.assert_reproducible(narrow, ctx_ml),
                   "§五.3 结论在相同的 Outline + context 上必须可重算重现")
    must_raise(lambda: vr_ml.assert_reproducible(narrow, wide_ctx),
               "§五.3 换成另一组上下文后结论必须无法重现（指纹不同）")
    check(vr_ml.outline_locator == narrow.outline_locator
          and vr_ml.outline_id == narrow.outline_id
          and vr_ml.verified_edge_ids() == (ok_ml_edge.edge_id,)
          and vr_ml.unresolved_edge_ids() == (),
          "§五.2 结论必须完整绑定 outline 身份与本次核验的全部 edge IDs")

    # -- 结构性断言（非行为断言，明确标注） --------------------------------------
    check(not hasattr(S, "_edge_statically_provable"),
          "P1-A 结构断言：纯字符串可证性旁路 `_edge_statically_provable` 必须已删除")
    check(S.OCCURRENCE_REQUIRED_EDGE_KINDS == ("cross_reference", "toc_to_body"),
          "P1-A 结构断言：需要真实 occurrence 的边类型必须包含 toc_to_body")
    check("toc" not in S.UNRESOLVABLE_REF_KINDS
          and "toc_to_body" in S.RESOLVABLE_EDGE_KINDS
          and "toc" in S.TEXT_SOURCE_REF_KINDS,
          "P1-C 结构断言：toc_to_body 的永久结构禁令必须已删除")


# ---------------------------------------------------------------------------
# F. TS1.4：未解析来源核验 + toc_to_body 目标节点锚点核验
# ---------------------------------------------------------------------------
#
# 两个 P1：
#   P1-1 `verify_references()` 对 `is_resolved=False` 直接登记未解析，**没有核验来源
#        occurrence**。于是 `page=999 / line=999` 这样自报的虚假 occurrence 可以进入
#        结构树。未解析只表示"**目标**尚未解析"，不表示"**来源**可以自报"。
#   P1-2 `_verify_toc_body_target()` 只比较目标节点**自报**的标题字符串与物理页码，
#        同页普通正文行上的节点可以冒充正文标题节点。
#
# 本组同时是"正例不得回归"的边界：合法 unresolved / 多行 span / 跨页 span /
# `parent_child` / `table_continuation` / 真实 TOC 正例都必须继续通过。

_TS14_TOL = 1e-9


def _ts14_outline(layout, nodes, edges=(), ctx=None, unassigned=()):
    return _ts13_outline(layout, nodes, edges=edges, unassigned=unassigned, ctx=ctx)


def _test_ts14_unresolved_sources_and_toc_anchors():
    layout = _ts13_layout()
    other_layout = _ts13_layout(document_id="doc-other")
    oloc = _ts13_oloc(layout)
    a, b, c, d = nodes = _ts13_nodes(oloc)
    ml, xp = _ts13_spans(oloc, nodes)
    toc_a = _ts13_toc(layout, 1, "1")
    toc_b = _ts13_toc(layout, 2, "2")
    toc_c = _ts13_toc(layout, 3, "2")
    toc_d = _ts13_toc(layout, 4, "2")
    toc_9 = _ts13_toc(layout, 5, "9")
    ctx_ml = S.ReferenceValidationContext(spans=(ml,), layout=layout)
    ctx_xp = S.ReferenceValidationContext(spans=(xp,), layout=layout)
    ctx_toc = S.ReferenceValidationContext(
        toc_sources=(toc_a, toc_b, toc_c, toc_d, toc_9), layout=layout)
    ctx_layout_only = S.ReferenceValidationContext(layout=layout)

    # -- §F.1：未解析边的 occurrence 同样必须落到真实 LayoutLine 上 --------------
    forged_open_occ = _ts13_occ(f"span:{ml.span_id}", 999, 999, 0,
                                len(_ts13_text(1, 2)), _ts13_text(1, 2),
                                "参见", "第一节标题")
    forged_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence", occurrence=forged_open_occ)
    check(not forged_open.is_resolved and forged_open.to_ref is None,
          "§F.1 未解析边在**边层**仍只是声明（无目标 id、带登记原因码）")
    must_raise(lambda: _ts14_outline(layout, nodes, edges=(forged_open,), ctx=ctx_ml),
               "§F.1 page=999 / line=999 的伪造 occurrence 在**未解析**边上也必须在 "
               "DocumentOutline.create 边界被拒绝（不得因为 is_resolved=False 就跳过"
               "来源核验）", "LayoutLine")

    # -- §F.2：同一对象经 from_dict 且缺少真实 context 必须同样被拒绝 ------------
    must_raise(lambda: S.DocumentOutline.from_dict(
        _ts13_wire(layout, nodes, edges=(forged_open,))),
        "§F.2 同一伪造对象经 from_dict 且缺少真实核验上下文时必须被拒绝"
        "（不得保留无上下文旁路）", "ReferenceValidationContext")

    # -- §F.3：来源真实、目标确实未解析 → 可以构造并登记在未解析清单里 -----------
    real_open = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}", to_ref=None,
        edge_kind="cross_reference", resolution_evidence=None,
        reason_code="no_textual_evidence",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 2, 0, len(_ts13_text(1, 2)),
                             _ts13_text(1, 2), "参见", "第一节标题"))
    real_open_outline = _ts14_outline(layout, nodes, edges=(real_open,), ctx=ctx_ml)
    vr_open = real_open_outline.verify_references(ctx_ml)
    check(vr_open.unresolved_edge_ids() == (real_open.edge_id,)
          and vr_open.verified_edge_ids() == ()
          and real_open.to_ref is None
          and real_open.occurrence.declared_target == "第一节标题",
          "§F.3 来源真实、目标确实未解析的引用必须可以构造，只登记为未解析"
          "（不猜目标、不因未解析就跳过来源核验）")

    # -- §F.4：未解析 toc 引用必须交出真实 TocSource + PageLayout ----------------
    forged_toc_ref = "toc:toc-ffffffffffffffff"
    forged_open_toc = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=forged_toc_ref, to_ref=None,
        edge_kind="toc_to_body", resolution_evidence=None,
        reason_code="toc_page_label_mapping_unproven",
        occurrence=_ts13_occ(forged_toc_ref, 3, 1, 0, len(_ts13_text(3, 1)),
                             _ts13_text(3, 1), "第一节标题", "第一节标题",
                             kind="toc_to_body"))
    must_raise(lambda: _ts14_outline(layout, nodes, edges=(forged_open_toc,),
                                     ctx=ctx_layout_only),
               "§F.4 未解析 toc_to_body 只有自报 toc id / 自报文本时必须被拒绝："
               "未解析也要真实 TocSource", "TocSource")
    must_raise(lambda: _ts14_outline(layout, nodes, edges=(forged_open_toc,)),
               "§F.4 同上但连 PageLayout 都没有时必须被拒绝", "核验上下文")
    real_open_toc = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_9.toc_source_id}",
        to_ref=None, edge_kind="toc_to_body", resolution_evidence=None,
        reason_code="toc_page_label_mapping_unproven",
        occurrence=_ts13_occ(f"toc:{toc_9.toc_source_id}", 3, 5, 0,
                             len(_ts13_text(3, 5)), _ts13_text(3, 5),
                             "第六节标题", "第六节标题", kind="toc_to_body"))
    real_toc_outline = _ts14_outline(layout, nodes, edges=(real_open_toc,),
                                     ctx=ctx_toc)
    check(real_toc_outline.verify_references(ctx_toc).unresolved_edge_ids()
          == (real_open_toc.edge_id,),
          "§F.4 真实 TocSource + 真实 PageLayout 的未解析 toc_to_body 必须可构造")

    # -- §F.5：toc_to_body 的目标节点必须通过对象级锚点核验 ----------------------
    bad_anchor_node = S.OutlineNode.create(
        document_outline_locator=oloc, parent_id=None, title="第三节标题",
        title_normalized="第三节标题", structural_path=("第三节标题",),
        source_anchor=_ts13_anchor(2, 1), ordinal=2)
    bad_nodes = (a, b, bad_anchor_node)
    check("第三节标题" not in _ts13_text(2, 1),
          "反例前置：第 2 页第 1 行是普通正文行，不含目标标题")
    toc_to_bad_anchor = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_c.toc_source_id}",
        to_ref=f"node:{bad_anchor_node.node_id}", edge_kind="toc_to_body",
        resolution_evidence="目录标题与页标签映射都自报一致",
        occurrence=_ts13_occ(f"toc:{toc_c.toc_source_id}", 3, 3, 0,
                             len(_ts13_text(3, 3)), _ts13_text(3, 3),
                             "第三节标题", "第三节标题", kind="toc_to_body"))
    must_raise(lambda: _ts14_outline(layout, bad_nodes,
                                     edges=(toc_to_bad_anchor,), ctx=ctx_toc),
               "§F.5 目录标题与物理页码都对得上、但目标 node 的标题锚点落在"
               "**不含该标题**的普通正文行时必须被拒绝（目标节点不得自证）", "标题")

    y_c = _TS13_Y0 + 2 * _TS13_H
    bad_bbox_node = S.OutlineNode.create(
        document_outline_locator=oloc, parent_id=None, title="第三节标题",
        title_normalized="第三节标题", structural_path=("第三节标题",),
        source_anchor=(2, 2, (340.0, y_c, 370.0, y_c + _TS13_H)), ordinal=2)
    toc_to_bad_bbox = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_c.toc_source_id}",
        to_ref=f"node:{bad_bbox_node.node_id}", edge_kind="toc_to_body",
        resolution_evidence="目录标题与页标签映射都自报一致",
        occurrence=_ts13_occ(f"toc:{toc_c.toc_source_id}", 3, 3, 0,
                             len(_ts13_text(3, 3)), _ts13_text(3, 3),
                             "第三节标题", "第三节标题", kind="toc_to_body"))
    must_raise(lambda: _ts14_outline(layout, (a, b, bad_bbox_node),
                                     edges=(toc_to_bad_bbox,), ctx=ctx_toc),
               "§F.5 目标节点标题锚点 bbox 不落在真实标题行 bbox 内时必须被拒绝",
               "bbox")

    # 同一锚点规则必须对 `node:` **来源**同样生效（单一实现，不是两套逻辑）。
    bad_node_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"node:{bad_anchor_node.node_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="自报落在该节点标题行上",
        occurrence=_ts13_occ(f"node:{bad_anchor_node.node_id}", 2, 1, 0,
                             len(_ts13_text(2, 1)), _ts13_text(2, 1),
                             "第四节", "第四节正文内容"))
    must_raise(lambda: _ts14_outline(layout, bad_nodes, edges=(bad_node_edge,),
                                     ctx=ctx_layout_only),
               "§F.5 同一对象级锚点规则必须对 node: 来源同样生效", "标题")

    # -- §F.6：真实 TOC → 真实标题行（标题 / bbox / 页标签全一致）必须通过 -------
    toc_edge_c = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"toc:{toc_c.toc_source_id}",
        to_ref=f"node:{c.node_id}", edge_kind="toc_to_body",
        resolution_evidence="目录项来源在真实 LayoutLine 上，标题锚点与页标签映射一致",
        occurrence=_ts13_occ(f"toc:{toc_c.toc_source_id}", 3, 3, 0,
                             len(_ts13_text(3, 3)), _ts13_text(3, 3),
                             "第三节标题", "第三节标题", kind="toc_to_body"))
    ok_toc_outline = _ts14_outline(layout, nodes, edges=(toc_edge_c,), ctx=ctx_toc)
    vr_toc = ok_toc_outline.verify_references(ctx_toc)
    check(vr_toc.is_verified(toc_edge_c.edge_id),
          "§F.6 目录项指向真实标题行、标题 / bbox / 页标签全部一致时必须继续通过")

    # -- §F.7：多行 / 跨页 span 正例不得回归 ------------------------------------
    ok_ml_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{ml.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="正文互指文字落在真实 LayoutLine 上",
        occurrence=_ts13_occ(f"span:{ml.span_id}", 1, 2, 0, len(_ts13_text(1, 2)),
                             _ts13_text(1, 2), "参见", "第一节标题"))
    check(_ts14_outline(layout, nodes, edges=(ok_ml_edge,), ctx=ctx_ml)
          .verify_references(ctx_ml).is_verified(ok_ml_edge.edge_id),
          "§F.7 span 覆盖两行、occurrence 位于末行时必须继续通过")
    ok_xp_edge = S.ReferenceEdge.create(
        document_outline_locator=oloc, from_ref=f"span:{xp.span_id}",
        to_ref=f"node:{b.node_id}", edge_kind="cross_reference",
        resolution_evidence="跨页 span 的引用出现在后一页",
        occurrence=_ts13_occ(f"span:{xp.span_id}", 2, 0, 0, len(_ts13_text(2, 0)),
                             _ts13_text(2, 0), "参见", "第二节标题"))
    check(_ts14_outline(layout, nodes, edges=(ok_xp_edge,), ctx=ctx_xp)
          .verify_references(ctx_xp).is_verified(ok_xp_edge.edge_id),
          "§F.7 span 跨两页、occurrence 位于后一页时必须继续通过")

    # -- §F.8：parent_child / table_continuation 正例不得回归 --------------------
    base_pl = _make_page_layout()
    base_outline = _make_outline(base_pl)
    pc_edge = base_outline.edges[0]
    pc_outline = _outline_with(base_pl, base_outline.nodes, edges=(pc_edge,))
    check(pc_outline.verify_references().is_verified(pc_edge.edge_id),
          "§F.8 合法 parent_child（无文本 occurrence）不得回归")

    pl0 = _make_page_layout()
    t1 = _make_table(pl0, table_index_on_page=0)
    t2 = _make_table(pl0, table_index_on_page=1, page_number=2,
                     continuation_of_locator=t1.table_locator)
    t1 = _make_table(pl0, table_index_on_page=0,
                     continuation_locators=(t2.table_locator,))
    cont_edge = S.ReferenceEdge.create(
        document_outline_locator=_make_outline(pl0).outline_locator,
        from_ref=f"table:{t1.table_locator}", to_ref=f"table:{t2.table_locator}",
        edge_kind="table_continuation", resolution_evidence="表头签名与列边界一致")
    ctx_tbl = S.ReferenceValidationContext(tables=(t1, t2))
    ok_cont = S.DocumentOutline.create(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        page_layout_id=pl0.page_layout_id, nodes=_make_outline(pl0).nodes,
        edges=(cont_edge,), unassigned=_make_outline(pl0).unassigned,
        candidate_sources=("body_numbering",), reference_context=ctx_tbl)
    check(ok_cont.verify_references(ctx_tbl).is_verified(cont_edge.edge_id),
          "§F.8 合法 table_continuation 不得回归")

    # -- §F.9：VerifiedReferences 的可重现信任边界 -----------------------------
    must_not_raise(lambda: vr_toc.assert_reproducible(ok_toc_outline, ctx_toc),
                   "§F.9 正确 Outline + context 上结论必须可重算重现")
    must_raise(lambda: vr_toc.assert_reproducible(
        ok_toc_outline, S.ReferenceValidationContext(toc_sources=(toc_a,),
                                                     layout=layout)),
        "§F.9 换成缺少该目录项来源的 context 后结论必须无法重现")
    must_raise(lambda: vr_toc.assert_reproducible(
        ok_toc_outline, S.ReferenceValidationContext(toc_sources=(toc_c,),
                                                     layout=other_layout)),
        "§F.9 换成另一份 PageLayout 后结论必须无法重现")

    # -- §F.10：create() 与 from_dict() 不得一严一宽 ----------------------------
    created = _ts14_outline(layout, nodes, edges=(real_open,), ctx=ctx_ml)
    loaded = S.DocumentOutline.from_dict(
        _ts13_wire(layout, nodes, edges=(real_open,)), reference_context=ctx_ml)
    check(canonical_json(created.to_dict()) == canonical_json(loaded.to_dict()),
          "§F.10 create() 与 from_dict() 对同一真实来源未解析边必须给出同一规范形")
    must_raise(lambda: _ts14_outline(layout, nodes, edges=(forged_open,), ctx=ctx_ml),
               "§F.10 create 必须拒绝伪造来源 occurrence", "LayoutLine")
    must_raise(lambda: S.DocumentOutline.from_dict(
        _ts13_wire(layout, nodes, edges=(forged_open,)), reference_context=ctx_ml),
        "§F.10 from_dict 对同一伪造来源必须同样拒绝（不得比 create 宽松）",
        "LayoutLine")
    must_raise(lambda: _ts14_outline(layout, nodes, edges=(real_open,)),
               "§F.10 create 未解析边不得存在无上下文旁路", "核验上下文")
    must_raise(lambda: S.DocumentOutline.from_dict(
        _ts13_wire(layout, nodes, edges=(real_open,))),
        "§F.10 from_dict 未解析边不得存在无上下文旁路", "核验上下文")

    # -- 结构性断言（非行为断言，明确标注） --------------------------------------
    check(hasattr(S, "_verify_node_anchor"),
          "P1-2 结构断言：节点锚点核验必须提取为可复用的对象级函数")


# ---------------------------------------------------------------------------


def main() -> dict:
    """逐个 focused 组执行；任何一组崩溃都不得掩盖其余组。"""
    groups = (
        _test_canonical_json_nan_inf,
        _test_identity_closure,
        _test_reference_occurrence_and_binding,
        _test_table_provenance_grid_continuation,
        _test_ts13_reference_coordinates_and_toc,
        _test_ts14_unresolved_sources_and_toc_anchors,
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

"""TS3 标题树构建器（`document_structure/outline_builder.py`）反例与确定性测试。

覆盖 `TREE_STRUCTURE_IMPLEMENTATION_PLAN.md` §13.2 与 TS3 任务书 §五 要求的面：

A. **主路径与来源边界**：S3 正文排版标题是唯一主路径；S1（目录页）与 S2（PDF
   bookmarks）只能佐证，**绝不能**创建 `OutlineNode`；S1/S2 都为空时树仍能建立。
B. **全页扫描**：页中的三级 / 四级 / 五级小标题（`（一）` / `1、` / `（1）`）必须
   被识别，不是只扫页首。编号层级**复用** `harness.heading_structure`。
C. **假阳性守卫**：年份（`2025年`）、金额 / 比例、表格数据行、页眉 / 页脚 / 页码、
   普通短句、以及"只因字号较大但无标题结构证据"的正文**都不得**成为节点。
D. **层级与身份**：编号深度是主要层级依据；跳级被夹紧并留痕；同名标题在不同父路径
   下不合并；同页同名不同 anchor 不合并；跨页标题与下一页正文层级正确。
E. **目录与页标签**：目录项在真实正文锚点上才对应；目录声明的页标签必须能唯一映射
   到真实物理页，否则 `toc_to_body` 保持 unresolved 并给出稳定 reason；**不得**猜
   最近页；声明页与正文锚点冲突必须留痕。
F. **信任边界 fail-closed**：PDF SHA 与 `PageLayout` 不一致即拒绝；页数不一致即拒绝；
   越界书签即拒绝；伪造的 node 标题 / 锚点在引用核验期失败。
G. **确定性与顺序无关**：相同输入连续两次构建逐字节一致；书签输入顺序不改变结果。
H. **内容不得静默消失**：每个非家具行恰好属于一个桶；低置信正文不得删除、也不得
   强升为节点；未采纳候选全部可追溯。
I. **TS3 边界**：不生成 `cross_reference` / `table_continuation`；`OutlineSpan` 正文
   边界明确标为 `pending_ts4`；不修改 `heading_structure.py`（编号层级序足够）。
J. **公司无关**：非 300750 fixture 通过；生产模块无公司名 / 公司代码 / 固定页码 /
   表号 / gold 特例；无 DB / 网络 / LLM / 非确定源。
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import pathlib
import shutil
import tempfile
import tokenize

import fitz

import document_structure
from document_structure import layout_builder as LB
from document_structure import outline_builder as OB
from document_structure import schema as S
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, canonical_json
from document_structure.normalization import tight

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent

#: TS3 生产模块（允许出现"读取 PDF"的库依赖，但**不**允许 DB / 网络 / LLM / 随机器）。
_TS3_MODULES = ("aligner.py", "outline_builder.py")

_FORBIDDEN_TOKENS = ("300750", "宁德时代", "CATL", "catl", "gold", "answer_key",
                     "fixed_page")

_BANNED_IMPORTS = (
    "sqlite3", "requests", "httpx", "urllib", "socket", "openai", "anthropic",
    "chromadb", "bocha", "psycopg", "sqlalchemy", "subprocess", "random",
    "uuid", "tempfile", "datetime", "time",
)

_A4 = (595.0, 842.0)

#: 正文行（长度足够让版式构建器的"文本层过稀"质量门通过）。
_BODY = (
    "本公司报告期内经营情况稳定，主营业务收入保持增长。",
    "公司持续推进技术研发与产能建设，详见后文说明。",
    "报告期内未发生对经营产生重大影响的事项。",
)

#: 真实目录项的形态：标题 + 点线填充 + 页标签（三条真实 PDF 全部如此）。
_TOC_LEADER = " .........."


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def plan(msg):
    _results["details"].append("SKIP " + msg)
    _results["skipped"] += 1


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:  # noqa: PERF203
        if substr in str(e):
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{e}）")
    except Exception as e:  # noqa: BLE001
        return check(False, f"{msg}（异常类型 {type(e).__name__} 非 {exc.__name__}：{e}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


def _read_module(name: str) -> str:
    return (_PKG_DIR / name).read_text(encoding="utf-8")


def _imported_modules(source: str) -> set:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


# ---------------------------------------------------------------------------
# 合成 PDF 工厂
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _tmpdir():
    d = tempfile.mkdtemp(prefix="ts3_ob_")
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _body(y0: float = 250.0, step: float = 22.0, tag: str = "") -> list:
    return [(72.0, y0 + i * step, t + tag, 11.0, False)
            for i, t in enumerate(_BODY)]


def _make_pdf(path: pathlib.Path, pages: list, toc=None) -> pathlib.Path:
    """生成电子 PDF。

    `pages` 每项为 `(items, label)`；item = `(x, y, text, size, bold)`。
    `label=None` 表示该页不印页码。**页码策略由调用方显式给出**：版式层的页码识别
    要求"标签 − 物理页"的众数偏移覆盖 ≥5 页，因此测试 fixture 必须自洽。
    """
    doc = fitz.open()
    for items, label in pages:
        page = doc.new_page(width=_A4[0], height=_A4[1])
        for item in items:
            x, y, text, size = item[0], item[1], item[2], item[3]
            bold = item[4] if len(item) > 4 else False
            page.insert_text((x, y), text, fontsize=size,
                             fontname="hebo" if bold else "china-s")
        if label is not None:
            page.insert_text((520.0, 800.0), label, fontsize=9, fontname="china-s")
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()
    return path


def _labeled(pages: list, start_offset: int = 1) -> list:
    """给物理页补齐自洽页码：第 n 页（n>=2）印 `n - start_offset`。"""
    out = []
    for index, (items, label) in enumerate(pages):
        if index == 0:
            out.append((items, None))
            continue
        out.append((items, label if label is not None else str(index + 1 - start_offset)))
    return out


def _ctx_of(path: pathlib.Path, company_id: str = "999999",
            document_id: str = "SYNTH_DOC_2026"):
    layout = LB.build_page_layout(
        path, LB.LayoutBuildContext(company_id=company_id, document_id=document_id))
    return layout, OB.load_source_context(
        path, layout, company_id=company_id, document_id=document_id)


def _basic_pages() -> list:
    """主 fixture：8 页；封面无页码，第 2..8 页印 1..7（偏移 1 自洽）。

    第 3 页（印"2"）承载 1–5 级编号标题；第 4 页（印"3"）承载第二节。
    """
    pages = [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "目录", 16.0, False),
          (72.0, 100.0, "第一节 管理层讨论与分析" + _TOC_LEADER + " 2", 11.0, False),
          (72.0, 122.0, "第二节 公司治理" + _TOC_LEADER + " 3", 11.0, False)]
         + _body(160.0, tag="T"), "1"),
        ([(72.0, 72.0, "第一节 管理层讨论与分析", 16.0, False),
          (72.0, 100.0, "一、主要业务", 14.0, False),
          (72.0, 128.0, "（一）主营业务分析", 12.5, False),
          (72.0, 156.0, "1、主要会计数据", 12.0, False),
          (72.0, 184.0, "（1）营业收入构成", 11.5, False)]
         + _body(230.0, tag="A"), "2"),
        ([(72.0, 72.0, "第二节 公司治理", 16.0, False)] + _body(120.0, tag="B"), "3"),
        ([(72.0, 72.0, "Executive Summary", 16.0, True)] + _body(120.0, tag="C"), "4"),
        (_body(120.0, tag="D"), "5"),
        (_body(120.0, tag="E"), "6"),
        (_body(120.0, tag="F"), "7"),
    ]
    return _labeled(pages)


def _fake_rows() -> list:
    """构造多栏数据行（表格区域排除用）。"""
    rows = []
    for i in range(4):
        y = 400.0 + i * 20.0
        rows.append((72.0, y, "项目%d" % i, 11.0, False))
        rows.append((280.0, y, "1,234.56", 11.0, False))
        rows.append((500.0, y, "12.34%", 11.0, False))
    return rows


def _nodes(res) -> list:
    return list(res.outline.nodes)


def _titles(res) -> list:
    return [n.title for n in res.outline.nodes]


def _accepted_audit(res) -> list:
    return [c for c in res.candidates if c.accepted]


def _reasons_for(res, page: int, line: int) -> list:
    for item in res.candidates:
        if item.page_number == page and item.line_index == line:
            return list(item.reason_codes)
    return []


def _tree_view(outline) -> dict:
    """树与边的**语义视图**：剥掉由 outline locator 派生的 id / 引用前缀。

    用来比较"同一份版式、不同来源清单"两次构建的树是否真的相同 —— 身份字段
    （`outline_locator` / `outline_id` 及其派生的 `node_id` / `edge_id`）只有在
    locator 相同时才可直接比较，因此这里按**结构语义**（层级、父子、锚点、
    边类型与解析结论）比较，而不是按内容寻址 id 比较。
    """
    nodes = []
    for node in outline.nodes:
        nodes.append({
            "level": node.level, "title": node.title,
            "title_normalized": node.title_normalized,
            "structural_path": list(node.structural_path),
            "source_anchor": [node.source_anchor[0], node.source_anchor[1],
                              list(node.source_anchor[2])],
            "ordinal": node.ordinal,
            "parent_path": None if node.parent_id is None else
            [n.structural_path for n in outline.nodes
             if n.node_id == node.parent_id][0],
            "child_paths": sorted(
                [n.structural_path for n in outline.nodes if n.node_id in node.child_ids]),
        })
    edges = sorted(
        (edge.edge_kind,
         edge.from_ref.split(":", 1)[0] + ":" + str(edge.occurrence is not None),
         edge.is_resolved, edge.reason_code or "",
         (edge.occurrence.page_number, edge.occurrence.line_index)
         if edge.occurrence is not None else (-1, -1),
         (None if edge.to_ref is None else edge.to_ref.split(":", 1)[0]))
        for edge in outline.edges)
    return {"nodes": nodes, "edges": edges}


def _code_only(source: str) -> str:
    """去掉注释后的源码。

    禁令对象是**规则**，而模块自己的边界注释会把这些词当作"禁止出现"来列举
    （"不得出现公司名 / 案例号 / 固定页码 / 表号 / 答案关键词"）。注释不参与执行，
    因此按执行代码判断，注释里的列举不会造成误伤、也不会掩盖真实规则。
    """
    return "\n".join(
        tok.string for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type != tokenize.COMMENT)


def _passed_as_argument(source: str, token: str) -> bool:
    """`token` 是否被**直接当作某个调用的实参**传进去。

    这是"该边类型有没有真的进入构造路径"的口径：不生成一类边时，模块只会在文字
    说明（文档字符串、`ok(...)` 断言、`Report` 计数）里提到它的名字，而不会把这个
    字面量交给 `ReferenceEdge(...)` 之类的调用。按整份源码做子串扫描会把边界叙述
    误判成实现，因此这里只认 AST 上的**直接实参**。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and arg.value == token:
                return True
    return False


def _built_outlines() -> list:
    """用主 fixture 真实构建一次，供边的**行为**断言使用。"""
    with _tmpdir() as d:
        pdf = _make_pdf(d / "edges.pdf", _basic_pages())
        _, ctx = _ctx_of(pdf)
        return [OB.build_document_outline(ctx)]


# ---------------------------------------------------------------------------
# A. 主路径与来源边界
# ---------------------------------------------------------------------------

def _test_two_documents_without_bookmarks() -> None:
    """两份**都没有书签**的独立文档（不同身份、不同标题结构）都不得退化。

    「退化」在这里有明确含义：节点数变少、层级被压平、正文行被丢掉、或者必须
    靠 S2 才能建树。两份文档一次构建，逐份用**结构期望**核对，而不是只数总数。
    """
    with _tmpdir() as d:
        # 文档一：1–3 级编号标题；文档二：编号与排版（bold）混用，层级形状不同。
        page_set_a = _labeled([
            ([(120.0, 120.0, "甲公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一节 经营分析", 16.0, False),
              (72.0, 100.0, "一、收入结构", 14.0, False),
              (72.0, 128.0, "（一）产品收入", 12.5, False)] + _body(160.0, tag="A"), "1"),
            ([(72.0, 72.0, "第二节 风险因素", 16.0, False)] + _body(120.0, tag="B"), "2"),
            (_body(120.0, tag="C"), "3"),
            (_body(120.0, tag="D"), "4"),
            (_body(120.0, tag="E"), "5"),
        ])
        # 文档二：另一套编号形态（章 / 节混用）+ 一条**粗体拉丁**无编号标题
        # （排版路径采纳）。粗体只用拉丁字符：合成 PDF 的 CJK 粗体字形无法被文本层
        # 还原，那是 fixture 的字体限制，不是标题识别的行为。
        page_set_b = _labeled([
            ([(120.0, 120.0, "乙公司 2026 年公司债券年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一章 发行人概况", 16.0, False),
              (72.0, 100.0, "一、基本信息", 14.0, False),
              (72.0, 128.0, "（一）主要财务指标", 12.5, False)]
             + _body(160.0, tag="P"), "1"),
            ([(72.0, 72.0, "第二章 财务信息", 16.0, False),
              (72.0, 100.0, "一、会计数据", 14.0, False),
              (72.0, 128.0, "Risk Factors", 16.0, True)] + _body(170.0, tag="Q"), "2"),
            (_body(120.0, tag="R"), "3"),
            (_body(120.0, tag="S"), "4"),
            (_body(120.0, tag="T"), "5"),
        ])
        expectations = (
            (page_set_a, "SYNTH_DOC_NOBM_A",
             ("第一节 经营分析", "一、收入结构", "（一）产品收入", "第二节 风险因素")),
            (page_set_b, "SYNTH_DOC_NOBM_B",
             ("第一章 发行人概况", "一、基本信息", "（一）主要财务指标",
              "第二章 财务信息", "一、会计数据", "Risk Factors")),
        )
        for index, (pages, document_id, expected) in enumerate(expectations, start=1):
            pdf = _make_pdf(d / f"nobm{index}.pdf", pages)
            layout, ctx = _ctx_of(pdf, document_id=document_id)
            check(ctx.bookmarks == (),
                  f"A9.{index} 文档 {document_id} 确实没有书签（S2 为空集）")
            check(not ctx.layout.__dict__.get("bookmarks"),
                  f"A9.{index}b 书签不在 PageLayout 上（信任边界：版式层不含 S2）")
            res = OB.build_document_outline(ctx)
            titles = _titles(res)
            missing = [t for t in expected if t not in titles]
            check(not missing,
                  f"A10.{index} 无书签文档的标题一个不少（缺失 {missing}）")
            check(len(_nodes(res)) == len(expected),
                  f"A11.{index} 节点数等于真实标题数 "
                  f"（{len(_nodes(res))} vs {len(expected)}）")
            levels = [n.level for n in _nodes(res)]
            check(min(levels) == 0 and max(levels) >= 2
                  and len(set(levels)) >= 3,
                  f"A12.{index} 层级未被压平（levels={levels}）")
            counts = res.line_assignment_counts()
            check(sum(counts.values()) == res.stats["non_furniture_lines"],
                  f"A13.{index} 无书签文档的非家具行同样守恒"
                  f"（{sum(counts.values())} vs {res.stats['non_furniture_lines']}）")
            check(res.stats["bookmarks"] == 0,
                  f"A14.{index} 统计里如实报告 0 条书签（不假装有 S2）")


def _test_s3_is_the_primary_path() -> None:
    with _tmpdir() as d:
        # (1) S1 与 S2 都为空：没有目录页、没有书签，仍必须由正文标题建立树。
        pages = _labeled([
            ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一节 管理层讨论与分析", 16.0, False),
              (72.0, 100.0, "一、主要业务", 14.0, False)] + _body(160.0, tag="A"), "1"),
            (_body(120.0, tag="B"), "2"),
            (_body(120.0, tag="C"), "3"),
            (_body(120.0, tag="D"), "4"),
            (_body(120.0, tag="E"), "5"),
            (_body(120.0, tag="F"), "6"),
        ])
        p = _make_pdf(d / "no_sources.pdf", pages)
        layout, ctx = _ctx_of(p)
        check(ctx.bookmarks == (), "A1 合成的无书签 PDF：S2 为空集（不是「忽略」）")
        res = OB.build_document_outline(ctx)
        check(len(_nodes(res)) >= 2,
              f"A2 S1/S2 均为空仍由正文标题建立树（{len(_nodes(res))} 个节点）")
        check("一、主要业务" in _titles(res), "A3 无目录、无书签时正文标题仍被采纳")
        check(res.toc_sources == (), "A4 无目录页时不产出任何 TocSource")

        # (2) 目录项找不到真实正文锚点：不得建节点，必须记为 toc_only_candidate。
        pages2 = _labeled([
            ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "目录", 16.0, False),
              (72.0, 100.0, "第八节 不存在的章节" + _TOC_LEADER + " 5", 11.0, False),
              (72.0, 122.0, "第九节 另一个不存在的章节" + _TOC_LEADER + " 6", 11.0, False),
              (72.0, 144.0, "第十节 依然不存在" + _TOC_LEADER + " 7", 11.0, False)]
             + _body(180.0, tag="T"), "1"),
            ([(72.0, 72.0, "第一节 真实章节", 16.0, False)] + _body(120.0, tag="A"), "2"),
            (_body(120.0, tag="B"), "3"),
            (_body(120.0, tag="C"), "4"),
            (_body(120.0, tag="D"), "5"),
            (_body(120.0, tag="E"), "6"),
        ])
        p2 = _make_pdf(d / "toc_only.pdf", pages2)
        layout2, ctx2 = _ctx_of(p2, document_id="SYNTH_DOC_TOC_ONLY")
        res2 = OB.build_document_outline(ctx2)
        titles2 = _titles(res2)
        check("第八节 不存在的章节" not in titles2,
              "A5 目录项无真实正文锚点时**不得**创建节点")
        reasons = {code for item in res2.candidates for code in item.reason_codes}
        check("toc_only_candidate" in reasons,
              "A6 无锚点的目录项记为 toc_only_candidate（可追溯）")
        check("第一节 真实章节" in titles2, "A7 同一文档内真实正文标题仍被采纳")

        # (3) 目录页含"目录"时页面不被丢弃：该页每一行都有归属。
        keys = {(a.page_number, a.line_index) for a in res2.line_assignments}
        toc_lines = {ln.line_index for page in layout2.pages
                     if page.page_number == 2 for ln in page.lines
                     if not ln.is_furniture}
        check(toc_lines and all((2, i) in keys for i in toc_lines),
              "A8 含「目录」的页面未被丢弃：其非家具行全部进入逐行记账")


def _test_bookmarks_cannot_create_nodes() -> None:
    with _tmpdir() as d:
        pages = _basic_pages()
        toc = [[1, "第一节 管理层讨论与分析", 3], [2, "一、主要业务", 3],
               [1, "第一百节 书签里的幻影", 6]]
        with_bookmarks = _make_pdf(d / "bm.pdf", pages, toc=toc)
        layout, ctx_real = _ctx_of(with_bookmarks, document_id="SYNTH_DOC_BM")
        # 同一份 PDF、同一个 PageLayout：只把 S2 换成空集（"文件里没有书签"的等价情形）
        ctx_empty = OB.OutlineSourceContext(
            layout=ctx_real.layout, pdf_sha256=ctx_real.pdf_sha256,
            company_id=ctx_real.company_id, document_id=ctx_real.document_id,
            document_version=ctx_real.document_version, bookmarks=())
        check(len(ctx_real.bookmarks) == 3,
              "B1 合成 PDF 的 3 条书签被显式读入（S2 非空）")
        check(ctx_empty.bookmarks == (), "B2 对照上下文 S2 为空集")
        check(ctx_empty.layout.page_layout_id == ctx_real.layout.page_layout_id,
              "B2b 对照上下文与真实上下文共用同一份 PageLayout 身份")
        res_a = OB.build_document_outline(ctx_real)
        res_b = OB.build_document_outline(ctx_empty)
        check("第一百节 书签里的幻影" not in _titles(res_a),
              "B3 书签**不得**直接创建节点（幻影标题不在树里）")
        check("第一百节 书签里的幻影" not in _titles(res_b),
              "B3b 有书签与无书签两种情形都不建节点")
        # 树（节点身份 / 层级 / 父子 / 锚点）与边结构必须逐项一致
        check(_tree_view(res_a.outline) == _tree_view(res_b.outline),
              "B4 S2 的有无不改变已由 S3 确定的树与边结构")
        check([n.node_id for n in _nodes(res_a)] == [n.node_id for n in _nodes(res_b)],
              "B4b node_id 逐个一致（S2 不参与节点身份）")
        ja = res_a.outline.to_dict()
        jb = res_b.outline.to_dict()
        differing = sorted(k for k in ja if canonical_json(ja[k]) != canonical_json(jb[k]))
        check(set(differing) <= {"candidate_sources", "outline_id", "outline_locator",
                                 "content_fingerprint"},
              f"B5 两种情形的规范 JSON 只在「声明用到的来源」上不同（{differing}）")
        check("nodes" not in differing and "edges" not in differing,
              "B5b nodes / edges 逐字节一致（差异只属于来源声明）")
        reasons = {code for item in res_a.candidates for code in item.reason_codes}
        check("bookmark_only_candidate" in reasons,
              "B6 无正文锚点的书签记为 bookmark_only_candidate")
        check(any(item.kind == "bookmark_only_candidate" for item in res_a.pending),
              "B7 该类书签同时进入 pending（显式待审，不得静默忽略）")
        check(not any(item.kind == "bookmark_only_candidate" for item in res_b.pending),
              "B7b 无书签情形不会凭空产生书签待审项")

        # 顺序无关：打乱书签输入顺序不改变树与身份。
        shuffled = OB.OutlineSourceContext(
            layout=ctx_real.layout, pdf_sha256=ctx_real.pdf_sha256,
            company_id=ctx_real.company_id, document_id=ctx_real.document_id,
            document_version=ctx_real.document_version,
            bookmarks=tuple(reversed(ctx_real.bookmarks)))
        res_shuffled = OB.build_document_outline(shuffled)
        check(canonical_json(res_shuffled.outline.to_dict())
              == canonical_json(res_a.outline.to_dict()),
              "B8 书签输入顺序改变不改变最终 JSON 与 node_id")


# ---------------------------------------------------------------------------
# B. 全页扫描与编号层级
# ---------------------------------------------------------------------------

def _test_mid_page_subheadings() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "basic.pdf", _basic_pages())
        layout, ctx = _ctx_of(p)
        res = OB.build_document_outline(ctx)
        titles = _titles(res)
        check("（一）主营业务分析" in titles, "C1 页中三级小标题（一）被识别")
        check("1、主要会计数据" in titles, "C2 页中四级小标题 1、被识别")
        check("（1）营业收入构成" in titles, "C3 页中五级小标题（1）被识别")
        levels = {n.title: n.level for n in _nodes(res)}
        check(levels.get("第一节 管理层讨论与分析") == 0
              and levels.get("一、主要业务") == 1
              and levels.get("（一）主营业务分析") == 2
              and levels.get("1、主要会计数据") == 3
              and levels.get("（1）营业收入构成") == 4,
              f"C4 1–5 级层级由编号深度确定（实际 {levels}）")
        mid = [n for n in _nodes(res) if n.source_anchor[1] > 0]
        check(len(mid) >= 3,
              f"C5 三级及更深小标题**不在页首**（锚点行号 > 0 的有 {len(mid)} 个）")
        check(all(n.level == len(n.structural_path) - 1 for n in _nodes(res)),
              "C6 level 与 structural_path 长度一致（公共不变式）")
        check(all(n.structural_path[-1] == n.title_normalized for n in _nodes(res)),
              "C7 structural_path 末段等于 title_normalized（公共不变式）")


def _test_false_positive_guards() -> None:
    # 守卫本身（纯形态，不依赖 fixture）
    guard_cases = (
        ("2025年", False, "year_like_line"),
        ("2024", False, "year_like_line"),
        ("2025年第3季度", False, "year_like_line"),
        ("1,234.56", False, "amount_or_ratio_line"),
        ("12.34%", False, "amount_or_ratio_line"),
        ("- 3 -", False, "page_label_like_line"),
        ("第 3 页", False, "page_label_like_line"),
        ("公司实现营业收入100万元，同比增长5%。", False, "sentence_final"),
    )
    for text, numbered, expected in guard_cases:
        got = OB.false_positive_reasons(text, numbered=numbered)
        check(expected in got,
              f"D1 守卫命中：{text!r} → {expected}（实际 {list(got)}）")
    # 真实编号标题不得被任何守卫命中
    for text in ("第一节 管理层讨论与分析", "一、主要业务", "（一）主营业务分析",
                 "1、主要会计数据", "（1）营业收入构成"):
        got = OB.false_positive_reasons(text, numbered=True)
        check(got == (), f"D2 真实标题不命中守卫：{text!r}（实际 {list(got)}）")
    check("title_too_long" in OB.false_positive_reasons("甲" * 61, numbered=True),
          "D3 超过公共 title 上限（60）的行不成标题（不截断）")

    # 端到端：fixture 里的干扰行都不得成为节点
    with _tmpdir() as d:
        pages = _basic_pages()
        pages[2] = (pages[2][0] + [
            (72.0, 600.0, "2025年", 15.0, False),
            (72.0, 622.0, "1,234.56", 15.0, False),
            (72.0, 644.0, "12.34%", 15.0, False),
            (72.0, 666.0, "公司实现营业收入100万元，同比增长5%。", 13.0, False),
        ], pages[2][1])
        pages[3] = (pages[3][0] + _fake_rows(), pages[3][1])
        p = _make_pdf(d / "fp.pdf", pages)
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_FP")
        res = OB.build_document_outline(ctx)
        titles = _titles(res)
        for bad in ("2025年", "1,234.56", "12.34%",
                    "公司实现营业收入100万元，同比增长5%。", "项目0", "12.34%"):
            check(bad not in titles,
                  f"D4 干扰行不得成为节点：{bad!r}")
        # 页眉 / 页码 / 家具行永远不是节点
        furniture_keys = {(page.page_number, ln.line_index)
                          for page in layout.pages for ln in page.lines
                          if ln.is_furniture}
        node_keys = {(n.source_anchor[0], n.source_anchor[1]) for n in _nodes(res)}
        check(not (node_keys & furniture_keys),
              "D5 节点锚点不得落在页眉 / 页脚 / 页码等家具行上")
        check(bool(furniture_keys), "D6 fixture 确实存在家具行（守卫有对象）")


def _test_heading_structure_reuse() -> None:
    src = _read_module("outline_builder.py")
    check("from harness.heading_structure import" in src,
          "E1 编号层级复用 harness.heading_structure（不另写一套）")
    check("leading_heading_level" in src and "iter_heading_spans" in src,
          "E2 同时复用 leading_heading_level 与 iter_heading_spans")
    # 未修改 harness：编号序仍覆盖 1–5 级且拒绝年份
    import harness.heading_structure as HS
    seq = [("第三节 管理层讨论与分析", 1), ("一、主要业务", 2), ("（一）主营业务分析", 3),
           ("1、主要会计数据", 4), ("（1）营业收入构成", 5)]
    for text, level in seq:
        check(HS.leading_heading_level(text) == level,
              f"E3[reuse] {text!r} → 层级 {level}")
    for text in ("2025年", "2025年年度报告",
                 "公司实现营业收入100万元，同比增长5%。"):
        check(HS.leading_heading_level(text) is None,
              f"E4[reuse] {text!r} 不构成编号层级")
    check(not _numbering_patterns(),
          f"E5 生产模块没有另写编号层级正则（只来自 harness）：{_numbering_patterns()}")


#: 编号 / 层级字形：出现在正则里就意味着"又写了一套编号层级规则"。
_NUMBERING_GLYPHS = ("、", "节", "章", "（一）", "（1）")


def _numbering_patterns() -> list:
    """生产模块里 `re.compile(...)` 字面量中带编号层级字形的模式（必须为空）。"""
    found = []
    for node in ast.walk(ast.parse(_read_module("outline_builder.py"))):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name != "compile":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any(glyph in arg.value for glyph in _NUMBERING_GLYPHS):
                    found.append(arg.value)
    return found


# ---------------------------------------------------------------------------
# C. 层级与身份
# ---------------------------------------------------------------------------

def _test_identity_and_levels() -> None:
    with _tmpdir() as d:
        # 同名标题出现在不同父路径下 → 不得合并。
        # 同名标题出现在同页不同 anchor → 不得合并。
        pages = _labeled([
            ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一节 甲部分", 16.0, False),
              (72.0, 100.0, "一、共同标题", 14.0, False),
              (72.0, 128.0, "第二节 乙部分", 16.0, False),
              (72.0, 156.0, "一、共同标题", 14.0, False),
              (72.0, 184.0, "二、重复标题", 14.0, False),
              (72.0, 212.0, "二、重复标题", 14.0, False)] + _body(260.0, tag="A"), "1"),
            (_body(120.0, tag="B"), "2"),
            (_body(120.0, tag="C"), "3"),
            (_body(120.0, tag="D"), "4"),
            (_body(120.0, tag="E"), "5"),
        ])
        p = _make_pdf(d / "ident.pdf", pages)
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_IDENT")
        res = OB.build_document_outline(ctx)
        nodes = [n for n in _nodes(res) if n.title == "一、共同标题"]
        check(len(nodes) == 2, f"F1 同名标题两条都进树（{len(nodes)}）")
        check(len({n.node_id for n in nodes}) == 2,
              "F2 同名标题在不同父路径下**不得**合并（node_id 不同）")
        check({n.parent_id for n in nodes} != {None}
              and len({n.parent_id for n in nodes}) == 2,
              "F3 两者的 parent 不同（父路径不同）")
        check(len({n.structural_path for n in nodes}) == 2,
              "F4 structural_path 不同（完整路径进身份）")
        dup = [n for n in _nodes(res) if n.title == "二、重复标题"]
        check(len(dup) == 2 and len({n.node_id for n in dup}) == 2,
              "F5 同页同名但不同 source anchor 不得合并")
        check({n.source_anchor[1] for n in dup} == {4, 5},
              f"F6 两条的锚点行号确实不同（{sorted(n.source_anchor[1] for n in dup)}）")
        check(len({n.node_id for n in _nodes(res)}) == len(_nodes(res)),
              "F7 全部 node_id 唯一")
        anchors = [(n.source_anchor[0], n.source_anchor[1]) for n in _nodes(res)]
        check(anchors == sorted(anchors) and len(set(anchors)) == len(anchors),
              "F8 节点按文档顺序排列且锚点严格递增")


def _test_level_conflicts_have_reasons() -> None:
    with _tmpdir() as d:
        pages = _labeled([
            ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一节 顶层", 16.0, False),
              # 跳级：从 1 级直接跳到 4 级（1、）
              (72.0, 100.0, "1、跳级小标题", 13.0, False),
              # 深编号（（一）= 3 级）却用最大字号 → 编号与字号冲突
              (72.0, 128.0, "（一）深编号大字号", 20.0, False)] + _body(180.0, tag="A"), "1"),
            (_body(120.0, tag="B"), "2"),
            (_body(120.0, tag="C"), "3"),
            (_body(120.0, tag="D"), "4"),
            (_body(120.0, tag="E"), "5"),
        ])
        p = _make_pdf(d / "lvl.pdf", pages)
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_LVL")
        res = OB.build_document_outline(ctx)
        levels = {n.title: n.level for n in _nodes(res)}
        check(levels.get("1、跳级小标题") == 1,
              f"G1 编号跳级被夹紧到父级下一层（实际 {levels.get('1、跳级小标题')}）")
        jump_reasons = _reasons_for(res, 2, 1)
        check("level_jump_clamped" in jump_reasons,
              f"G2 跳级有稳定 reason（实际 {jump_reasons}）")
        conflict = _reasons_for(res, 2, 2)
        check("numbering_style_conflict" in conflict,
              f"G3 编号与字号冲突有稳定 reason（实际 {conflict}）")
        # §三 P1-A：`（一）`（声明 3 级）落在**当前局部结构域**里唯一比它浅的祖先
        # `第一节`（声明 1 级）之下 ⇒ 有效深度 1，与同域内已出现的声明 4 级标题同级。
        # 关键点不是这个数字，而是：**层级只由编号的声明层级决定** —— 该行用的是全文
        # 最大字号（20.0，比第 1 节还大），如果字号参与层级判定，它会被抬到 0。
        check(levels.get("（一）深编号大字号") == 1,
              f"G4 冲突时层级仍以编号深度为准（实际 "
              f"{levels.get('（一）深编号大字号')}；全文字号最大的行没有被字号抬高）")

        # 无编号标题：字号秩只作辅助证据
        style_nodes = [n for n in _nodes(res) if n.title == "Executive Summary"]
        if style_nodes:
            check(True, "G5 无编号标题可由排版证据采纳（本 fixture 含）")


def _test_unnumbered_style_path() -> None:
    with _tmpdir() as d:
        pages = _labeled([
            ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一节 甲", 16.0, False),
              (72.0, 100.0, "Executive Summary", 16.0, True),
              # 只因字号较大、非粗体、无编号、无 S1/S2 佐证 → 不得采纳
              (72.0, 140.0, "Large But Plain Body Line", 18.0, False),
              # 无编号、短行、非粗体 → 无结构证据
              (72.0, 170.0, "Plain Short Line", 11.0, False)] + _body(220.0, tag="A"), "1"),
            (_body(120.0, tag="B"), "2"),
            (_body(120.0, tag="C"), "3"),
            (_body(120.0, tag="D"), "4"),
            (_body(120.0, tag="E"), "5"),
        ])
        p = _make_pdf(d / "style.pdf", pages)
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_STYLE")
        res = OB.build_document_outline(ctx)
        titles = _titles(res)
        check("Executive Summary" in titles,
              "H1 无编号标题由排版证据（粗体 + 字号）采纳")
        check("Large But Plain Body Line" not in titles,
              "H2 只因字号较大但无标题结构证据的正文**不得**成为节点")
        check("Plain Short Line" not in titles, "H3 无编号无证据的短行不得成为节点")
        style_audit = [c for c in _accepted_audit(res)
                       if c.text == "Executive Summary"]
        check(style_audit and "style_only_accepted" in style_audit[0].reason_codes,
              "H4 排版路径采纳留痕 style_only_accepted")
        check(all(c.confidence if hasattr(c, "confidence") else True
                  for c in res.candidates),
              "H5 审计记录没有「执行者自报 confidence」字段")


def _test_cross_page_levels() -> None:
    with _tmpdir() as d:
        pages = _labeled([
            ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
            ([(72.0, 72.0, "第一节 甲", 16.0, False)] + _body(120.0, tag="A"), "1"),
            # 跨页标题：某页最后一行是三级小标题，正文在下一页继续
            ([*_body(120.0, tag="B"), (72.0, 700.0, "（一）跨页小标题", 12.5, False)], "2"),
            (_body(72.0, tag="C"), "3"),
            (_body(120.0, tag="D"), "4"),
            (_body(120.0, tag="E"), "5"),
        ])
        p = _make_pdf(d / "cross.pdf", pages)
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_CROSS")
        res = OB.build_document_outline(ctx)
        node = next((n for n in _nodes(res) if n.title == "（一）跨页小标题"), None)
        check(node is not None, "I1 跨页三级小标题被识别")
        if node is not None:
            check(node.source_anchor[0] == 3,
                  f"I2 节点锚定在标题真实所在页（{node.source_anchor[0]}）")
            # 该页与父级之间没有 1 级标题：编号跳级被夹紧到父级下一层，并留痕。
            check(node.level == 1 and node.level == len(node.structural_path) - 1,
                  f"I3 跨页标题层级按「父级下一层」夹紧且自洽（level={node.level}）")
            jump = _reasons_for(res, 3, node.source_anchor[1])
            check("level_jump_clamped" in jump,
                  f"I3b 跨页标题的跳级被显式留痕（{jump}）")
            parent = next((n for n in _nodes(res) if n.node_id == node.parent_id), None)
            check(parent is not None and parent.title == "第一节 甲",
                  "I4 父节点是最近的可信前序低层级标题")
            nxt = [a for a in res.line_assignments if a.page_number == 4
                   and a.assignment == "accepted_heading"]
            check(not nxt, "I5 下一页正文不得被强升为节点（保持普通正文）")
            check(all(a.assignment == "ordinary_content" for a in res.line_assignments
                      if a.page_number == 4),
                  "I6 下一页正文行全部归 ordinary_content（等待 TS4 切分）")


# ---------------------------------------------------------------------------
# D. 目录、页标签与引用边
# ---------------------------------------------------------------------------

def _test_toc_and_page_labels() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "basic2.pdf", _basic_pages())
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_TOC")
        res = OB.build_document_outline(ctx)
        check(res.stats["toc_pages"], "J1 目录页被识别（含「目录」标题行）")
        check(res.stats["toc_entries"] == 2,
              f"J2 目录项被解析（{res.stats['toc_entries']}）")
        check(len(res.toc_sources) == 2, "J3 每条目录项有真实 TocSource（绑定真实行）")
        reasons = {c.edge_kind: c for c in res.outline.edges}
        check("parent_child" in reasons, "J4 生成 parent_child 边")
        check("table_continuation" not in reasons,
              "J5 **不得**生成 TS5 才负责的 table_continuation")
        check(res.stats["cross_reference_edges"] == 0,
              "J6 **不得**生成 TS3 无真实载体可用的 cross_reference 边")
        toc_edges = [e for e in res.outline.edges if e.edge_kind == "toc_to_body"]
        check(len(toc_edges) == 2, f"J7 每条目录项对应一条 toc_to_body 边（{len(toc_edges)}）")
        # 页标签"2"映射到的物理页确实承载该标题 → 允许 resolved；否则必须 unresolved
        by_label = {}
        for entry in res.toc_sources:
            by_label[entry.declared_page_label] = entry
        resolved = {e.from_ref for e in toc_edges if e.is_resolved}
        check(resolved <= {f"toc:{s.toc_source_id}" for s in res.toc_sources},
              "J8 已解析边的来源都是真实 TocSource")
        for edge in toc_edges:
            if edge.is_resolved:
                check(edge.resolution_evidence is not None
                      and "node_anchor_verified" in edge.resolution_evidence,
                      "J9 已解析边必须给出对象级核验证据（含节点锚点核验）")
                check(edge.to_ref is not None, "J10 已解析边必须有真实目标")
            else:
                check(edge.reason_code in S.UNRESOLVED_EDGE_REASONS,
                      f"J11 未解析边 reason 属于封闭词表（{edge.reason_code}）")
                check(edge.to_ref is None, "J12 未解析边不得自报目标")
        check(S.ReferenceValidationContext is not None, "J13 核验上下文由公共类型提供")
        # 逐条复核：目录声明的页标签能否唯一映射到真实物理页
        for entry in res.toc_sources:
            physical = S._resolve_page_label(layout, entry.declared_page_label)
            check(physical is not None,
                  f"J14 页标签 {entry.declared_page_label!r} 能由真实页码家具唯一映射"
                  f"（→ 物理页 {physical}）")


def _test_toc_page_conflict_no_guessing() -> None:
    with _tmpdir() as d:
        # 目录声明"2"，但真实第 3 页承载标题；第 3 页实际印的是"3"。
        pages = _basic_pages()
        pages[1] = ([(72.0, 72.0, "目录", 16.0, False),
                      (72.0, 100.0, "第一节 管理层讨论与分析" + _TOC_LEADER + " 99",
                       11.0, False),
                      (72.0, 122.0, "第二节 公司治理" + _TOC_LEADER + " 2", 11.0, False)]
                     + _body(160.0, tag="T"), "1")
        p = _make_pdf(d / "conflict.pdf", pages)
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_CONFLICT")
        res = OB.build_document_outline(ctx)
        check(all(not e.is_resolved for e, _n in res.resolved_edges),
              "K1 声明页与正文锚点冲突时**不得**猜最近页")
        codes = {reason for _e, reason in res.unresolved_edges}
        check(codes and codes <= set(S.UNRESOLVED_EDGE_REASONS),
              f"K2 冲突保持 unresolved 且 reason 稳定（{sorted(codes)}）")
        conflict_flags = [c.page_conflict for c in res.candidates if c.accepted]
        check(any(conflict_flags),
              "K3 冲突候选项被显式留痕（page_conflict=True）")
        check(res.stats["edges_toc_to_body"] >= 2,
              "K4 目录项都产出了边（只是未解析）")


# ---------------------------------------------------------------------------
# E. 信任边界 fail-closed
# ---------------------------------------------------------------------------

def _test_source_context_fail_closed() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "sha.pdf", _basic_pages())
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_SHA")
        # (1) SHA 不一致 → 拒绝
        raises(lambda: OB.OutlineSourceContext(
            layout=layout, pdf_sha256="0" * 64, company_id="999999",
            document_id=layout.document_id,
            document_version=layout.document_version),
            SchemaValidationError, "sha256",
            "L1 PDF SHA 与 PageLayout 不一致时拒绝构造来源上下文")
        # (2) document_id / document_version 身份不闭合 → 拒绝
        raises(lambda: OB.OutlineSourceContext(
            layout=layout, pdf_sha256=layout.source_file_sha256,
            company_id="999999", document_id="OTHER_DOC",
            document_version=layout.document_version),
            SchemaValidationError, "document_id",
            "L2 document_id 与 PageLayout 不闭合时拒绝")
        raises(lambda: OB.OutlineSourceContext(
            layout=layout, pdf_sha256=layout.source_file_sha256,
            company_id="999999", document_id=layout.document_id,
            document_version="sha256-deadbeef"),
            SchemaValidationError, "document_version",
            "L3 document_version 与 PageLayout 不闭合时拒绝")
        # (3) 越界书签 → 拒绝
        raises(lambda: OB.OutlineSourceContext(
            layout=layout, pdf_sha256=layout.source_file_sha256,
            company_id="999999", document_id=layout.document_id,
            document_version=layout.document_version,
            bookmarks=(OB.BookmarkEntry("越界", 0, 999, 0),)),
            SchemaValidationError, "超出",
            "L4 书签页码超出 PageLayout 页数时拒绝")
        # (4) 用另一份 PDF 求 sha → 拒绝（不得拿别的文件的哈希冒充）
        other = _make_pdf(d / "other.pdf", _labeled([
            ([(120.0, 120.0, "另一份文件", 20.0, False)], None),
            (_body(120.0, tag="X"), "1"), (_body(120.0, tag="Y"), "2"),
            (_body(120.0, tag="Z"), "3"), (_body(120.0, tag="W"), "4"),
            (_body(120.0, tag="V"), "5"),
        ]))
        raises(lambda: OB.load_source_context(other, layout, company_id="999999",
                                             document_id=layout.document_id),
               SchemaValidationError, "sha256",
               "L5 拿另一份 PDF 读书签时因 SHA 不符被拒绝")
        # (5) 缺文件 → 拒绝
        raises(lambda: OB.load_source_context(d / "missing.pdf", layout,
                                             company_id="999999",
                                             document_id=layout.document_id),
               SchemaValidationError, "不存在",
               "L6 PDF 不存在时拒绝，不得降级为「没有书签」")
        # (6) 非上下文类型 → 拒绝（纯构建不得自行找文件）
        raises(lambda: OB.build_document_outline(layout),
               SchemaValidationError, "OutlineSourceContext",
               "L7 build_document_outline 拒绝非来源上下文输入（不自行找文件）")


def _test_tampered_nodes_fail_closed() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "tamper.pdf", _basic_pages())
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_TAMPER")
        res = OB.build_document_outline(ctx)
        node = _nodes(res)[0]
        # (1) 篡改 title（不改 node_id）→ from_dict 拒绝（身份不符）
        blob = res.outline.to_dict()
        for item in blob["nodes"]:
            if item["node_id"] == node.node_id:
                item["title"] = "伪造标题"
        raises(lambda: S.DocumentOutline.from_dict(blob),
               SchemaValidationError, "node_id",
               "M1 篡改 node title 后 from_dict fail-closed")
        # (2) 篡改 source_anchor 页码 / 行号 → 拒绝
        for field_name, value in (("page", "source_anchor"), ("line", "source_anchor")):
            blob2 = res.outline.to_dict()
            for item in blob2["nodes"]:
                if item["node_id"] == node.node_id:
                    anchor = list(item["source_anchor"])
                    anchor[0 if field_name == "page" else 1] = 999
                    item["source_anchor"] = anchor
            raises(lambda b=blob2: S.DocumentOutline.from_dict(b),
                   SchemaValidationError, "node_id",
                   f"M2 篡改 node {field_name} 后 from_dict fail-closed")
        # (3) 伪造锚点：标题不在锚点行的文本里 → 引用核验期失败
        line = layout.line_at(node.source_anchor[0], node.source_anchor[1])
        fake = S.OutlineNode.create(
            document_outline_locator=res.outline.outline_locator,
            parent_id=None, title="凭空捏造的标题", title_normalized="凭空捏造的标题",
            structural_path=("凭空捏造的标题",),
            source_anchor=(node.source_anchor[0], node.source_anchor[1],
                           tuple(line.bbox)))
        locator_value = S.derive_document_outline_locator(
            page_layout_id=layout.page_layout_id, document_id=layout.document_id,
            algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
            schema_version=V.OUTLINE_SCHEMA_VERSION)
        occ = S.ReferenceOccurrence(
            source_ref=S.edge_ref("node", fake.node_id),
            page_number=fake.source_anchor[0], line_index=fake.source_anchor[1],
            char_start=0, char_end=len("凭空捏造的标题"),
            occurrence_index=0, reference_marker="凭空捏造的标题",
            reference_kind="cross_reference", declared_target="第一节",
            normalized_text="凭空捏造的标题")
        edge = S.ReferenceEdge.create(
            document_outline_locator=locator_value,
            from_ref=S.edge_ref("node", fake.node_id),
            to_ref=S.edge_ref("node", node.node_id),
            edge_kind="cross_reference",
            resolution_evidence="claimed:verified", occurrence=occ)
        raises(lambda: S.DocumentOutline.create(
            document_id=layout.document_id, document_version=layout.document_version,
            page_layout_id=layout.page_layout_id, nodes=(fake, *tuple(_nodes(res))),
            edges=(edge,), unassigned=(), candidate_sources=(),
            reference_context=S.ReferenceValidationContext(layout=layout)),
            SchemaValidationError, "",
            "M3 伪造锚点（标题不在锚点行原文里）在引用核验期 fail-closed")


# ---------------------------------------------------------------------------
# F. 确定性与守恒
# ---------------------------------------------------------------------------

def _test_determinism_and_conservation() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "det.pdf", _basic_pages())
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_DET")
        res1 = OB.build_document_outline(ctx)
        res2 = OB.build_document_outline(ctx)
        check(canonical_json(res1.outline.to_dict())
              == canonical_json(res2.outline.to_dict()),
              "N1 相同输入连续构建两次的规范 JSON 逐字节一致")
        check(res1.outline.outline_id == res2.outline.outline_id,
              "N2 outline_id 一致")
        check(canonical_json([c.to_dict() for c in res1.candidates])
              == canonical_json([c.to_dict() for c in res2.candidates]),
              "N3 候选审计记录逐字节一致")
        check(canonical_json([a.to_dict() for a in res1.line_assignments])
              == canonical_json([a.to_dict() for a in res2.line_assignments]),
              "N4 行归属记账逐字节一致")
        # 守恒：每个非家具行恰好一个桶
        non_furniture = sum(1 for page in layout.pages for ln in page.lines
                            if not ln.is_furniture)
        check(len(res1.line_assignments) == non_furniture,
              f"N5 逐行记账覆盖全部非家具行（{len(res1.line_assignments)} vs "
              f"{non_furniture}）")
        counts = res1.line_assignment_counts()
        check(sum(counts.values()) == non_furniture,
              f"N6 各桶之和守恒（{counts}）")
        check(set(counts) <= {"accepted_heading", "ordinary_content",
                              "toc_candidate", "bookmark_candidate",
                              "unassigned_ambiguous"},
              f"N7 桶名属于约定的守恒词表（{sorted(counts)}）")
        res1.assert_conserved(layout)
        check(True, "N8 assert_conserved 在真实 fixture 上通过")
        # 未被采纳的候选全部可追溯；没有静默消失
        check(all(c.node_id is None for c in res1.candidates if not c.accepted),
              "N9 未采纳候选不携带 node_id")
        check(all(c.node_id is not None for c in res1.candidates if c.accepted),
              "N10 采纳候选都可追溯到 node_id")
        audit_nodes = {c.node_id for c in res1.candidates if c.accepted}
        check(audit_nodes == {n.node_id for n in _nodes(res1)},
              "N11 审计里的采纳集合与正式节点集合一致")
        # N12：正式 `unassigned` **不是**空占位。本 fixture 没有歧义候选，因此它
        # 应当为空；但"为空"必须是**因为确实没有待归属候选**，而不是因为实现从不写。
        ambiguous = [a for a in res1.line_assignments
                     if a.assignment == "unassigned_ambiguous"]
        check(res1.outline.unassigned == () and not ambiguous,
              "N12 无歧义候选时正式 unassigned 为空，且没有 unassigned_ambiguous 行")
        check(res1.stats["unassigned_spans"] == len(res1.outline.unassigned),
              "N12b stats 里的 unassigned 计数与正式对象一致（不得是 sidecar 计数）")
        check(len(res1.outline.unassigned) == 0
              or all(s.role == "unassigned" for s in res1.outline.unassigned),
              "N12c 正式 unassigned 只承载 role='unassigned' 的 OutlineSpan")
        check(res1.stats["pending_ts4_span_builder"] is True,
              "N13 span 正文边界显式标为 pending_ts4")


def _ambiguous_pages() -> list:
    """第 7 页（印 "6"）放一条**孤立**的编号行：与正文同级、未居中、无目录 / 书签。

    它拿到的**唯一**证据是自己那一行的 `standalone_line`（本行完整、未折行、也不落在
    表格 / 网格区域内）；上一行的真实行尾不是句末标点、且与它之间没有段落间距，因此
    连 `paragraph_boundary` 都没有 —— 主+辅 = 1 < 2，未达采纳门槛，必须进入正式
    `unassigned`。
    """
    pages = [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 管理层讨论与分析", 16.0, False)] + _body(160.0, tag="T"),
         "1"),
        ([(72.0, 72.0, "第一节 管理层讨论与分析", 16.0, False)] + _body(160.0, tag="A"),
         "2"),
        (_body(120.0, tag="B"), "3"),
        (_body(120.0, tag="C"), "4"),
        (_body(120.0, tag="D"), "5"),
        ([(72.0, 120.0, "报告期内未发生对经营产生重大影响的事项", 11.0, False),
          (72.0, 133.0, "（三）其他重要事项", 11.0, False)]
         + _body(160.0, tag="E"), "6"),
    ]
    return _labeled(pages)


def _test_formal_unassigned() -> None:
    """P1-B：歧义候选必须进**正式** `DocumentOutline.unassigned`，并参与内容指纹。

    四件事必须成立：

    1. 歧义候选进入正式对象（`role="unassigned"` + 封闭词表内的登记原因），且与
       `unassigned_ambiguous` 行一一对应 —— 不能只写在 sidecar；
    2. 正式 `unassigned` 参与 `content_fingerprint` / `outline_id`：删掉这条 span
       后重新构建，指纹必须改变（"未归属状态"是身份的一部分）；
    3. 篡改正式 unassigned（改文本 / 改锚点 / 改原因 / 增删）必须读回 fail-closed
       或使指纹改变；
    4. sidecar 只是人工查阅副本：改它不改变任何正式状态。
    """
    with _tmpdir() as d:
        p = _make_pdf(d / "amb.pdf", _ambiguous_pages())
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_AMB")
        check(layout.document_id == "SYNTH_DOC_AMB", "N14 fixture 文档身份闭合")
        res = OB.build_document_outline(ctx)
        spans = list(res.outline.unassigned)
        check(len(spans) == 1,
              f"N15 孤立编号候选进入正式 unassigned（{len(spans)} 条）")
        if not spans:
            return
        span = spans[0]
        check(span.role == "unassigned" and span.node_id is None,
              "N15b 正式 unassigned span 的 role / node_id 自洽")
        check(span.unassigned_reason in S.UNASSIGNED_REASONS,
              f"N15c 登记原因属于公共封闭词表（{span.unassigned_reason!r}）")
        check(span.evidence_set_version
              == V.OUTLINE_UNASSIGNED_EVIDENCE_SET_VERSION,
              "N15d 未归属 span 使用声明式证据集版本（不冒用任何真实 evidence set）")
        line = layout.line_at(span.start_anchor[0], span.start_anchor[1])
        check(line is not None
              and span.normalized_text in tight(line.text)
              and tuple(span.start_anchor[2]) == tuple(line.bbox),
              "N15e 正式 unassigned 可回溯到真实版式行（文本 + bbox）")
        ambiguous = [a for a in res.line_assignments
                     if a.assignment == "unassigned_ambiguous"]
        check(len(ambiguous) == 1
              and span.span_id in ambiguous[0].reason,
              "N15f 行归属记帐指向**同一个** span_id（正式状态可对账）")
        check(res.stats["unassigned_spans"] == 1,
              "N15g stats 的 unassigned 计数与正式对象一致")

        # 2) 正式 unassigned 参与身份：删掉它，指纹必须改变。
        node_list = tuple(_nodes(res))
        without = S.DocumentOutline.create(
            document_id=layout.document_id, document_version=layout.document_version,
            page_layout_id=layout.page_layout_id, nodes=node_list,
            edges=tuple(res.outline.edges), unassigned=(),
            candidate_sources=tuple(res.outline.candidate_sources),
            reference_context=S.ReferenceValidationContext(
                layout=layout, toc_sources=tuple(res.toc_sources)))
        check(without.content_fingerprint != res.outline.content_fingerprint
              and without.outline_id != res.outline.outline_id,
              "N16 删除正式 unassigned 后 content_fingerprint / outline_id 改变")
        check(canonical_json(res.outline.to_dict())
              == canonical_json(S.DocumentOutline.from_dict(
                  res.outline.to_dict()).to_dict()),
              "N16b 正式 unassigned 参与规范化序列化与读回")
        blob = res.outline.to_dict()
        for name, mutate in (
                ("文本", lambda b: b["unassigned"][0].__setitem__(
                    "normalized_text", "被篡改")),
                ("锚点", lambda b: b["unassigned"][0]["start_anchor"].__setitem__(
                    0, 999)),
                ("原因", lambda b: b["unassigned"][0].__setitem__(
                    "unassigned_reason", "below_last_heading")),
                ("删除", lambda b: b["unassigned"].pop(0)),
                ("增加", lambda b: b["unassigned"].append(
                    json.loads(json.dumps(b["unassigned"][0])))),
        ):
            bad = json.loads(json.dumps(blob))
            mutate(bad)
            try:
                mutated = S.DocumentOutline.from_dict(bad)
                rejected = False
                changed = (mutated.content_fingerprint
                           != res.outline.content_fingerprint)
            except SchemaValidationError:
                rejected, changed = True, None
            check(rejected or changed,
                  f"N17 篡改正式 unassigned 的{name}必须 fail-closed 或改变指纹"
                  f"（rejected={rejected}, changed={changed}）")

        # 4) sidecar 不是权威载体：改它不影响任何正式状态。
        diag = OB.line_structure_diagnostic(res, layout)
        check(diag["counts"].get("formal_unassigned") == 1,
              f"N18 诊断逐行给出结构归属（{diag['counts']}）")
        tampered = json.loads(json.dumps(diag))
        for row in tampered["lines"]:
            if row["structure_state"] == "formal_unassigned":
                row["structure_state"] = "heading_node"
                row["unassigned_span_id"] = None
        check(canonical_json(res.outline.to_dict())
              == canonical_json(OB.build_document_outline(ctx).outline.to_dict()),
              "N18b sidecar 被篡改不影响正式 outline（正式状态不以 sidecar 为准）")
        again = OB.build_document_outline(ctx)
        check([s.span_id for s in again.outline.unassigned]
              == [s.span_id for s in res.outline.unassigned],
              "N18c 同输入两次构建得到相同 unassigned span 身份（确定性）")


# ---------------------------------------------------------------------------
# §二 通用反例面：编号**不是**标题证据
#
# 这一组把 §二 要求"必须给出通用反例"的每一类都做成真实版式反例，且**不**使用任何
# 公司名 / 业务词 / 页码 / 表号 / 答案关键词：全部是合成文档里的形态样本。
# 反例与正例（小字号小标题）放在**同一份文档**里，字号完全相同 —— 因此"接受 / 未
# 接受"的差别只能来自结构证据组合，不可能来自某个字号阈值。
# ---------------------------------------------------------------------------

#: 反例样本行：(y, 文本)。全部 size=11.0、x0=72.0，与正文**同字号同缩进**。
_COUNTEREXAMPLE_LINES = (
    (72.0, "（一）张三，男，1975年生，现任本公司董事"),      # 人员履历列表
    (94.0, "（二）李四，女，1978年生，现任本公司监事"),
    (116.0, "（三）王五，男，1970年生，现任本公司高管"),
    (152.0, "（一）识别风险因素并评估影响程度"),              # 风险控制 / 步骤列表
    (174.0, "（二）制定应对措施并落实责任人"),
    (196.0, "（三）定期复核措施有效性评估结果"),
    (232.0, "1、本次交易指公司收购标的公司股权的行为"),        # 定义 / 条件条款
    (254.0, "2、关联方指对公司具有控制关系的法人主体"),
    (290.0, "（2）="),                                        # 只含编号和符号
    (312.0, "（1）净资产收益率=净利润/净资产"),                # 公式 / 残缺表达式
    (356.0, "（三）公司持续推进产业结构优化并加强内部控制体系建设"),  # 上一句未结束的续写
)

#: 表格区域：同一基线上起点不同的编号单元格行。
_COUNTEREXAMPLE_TABLE = (
    (72.0, 460.0, "（1）营业收入", 11.0, False),
    (280.0, 460.0, "1,234.56", 11.0, False),
    (500.0, 460.0, "12.34%", 11.0, False),
    (72.0, 482.0, "（2）营业成本", 11.0, False),
    (280.0, 482.0, "987.65", 11.0, False),
    (500.0, 482.0, "8.76%", 11.0, False),
    (72.0, 504.0, "项目合计", 11.0, False),
    (280.0, 504.0, "2,222.21", 11.0, False),
    (500.0, 504.0, "21.10%", 11.0, False),
)

#: 正例：**小字号、无加粗、无目录 / 书签**的小标题，只靠"同级编号版式类 + 段落边界"确认。
_SMALL_HEADINGS = ("（一）行业竞争格局", "（二）技术发展趋势")


def _counterexample_pages() -> list:
    """第 3 页（印 "2"）放两个小字号小标题；第 4 页（印 "3"）放 §二 全部反例形态。"""
    pages = [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_body(120.0, tag="S"), "1"),
        ([(72.0, 72.0, _SMALL_HEADINGS[0], 11.0, False)]
         + _body(130.0, tag="U")
         + [(72.0, 230.0, _SMALL_HEADINGS[1], 11.0, False)]
         + _body(260.0, tag="V"), "2"),
        ([(72.0, y, text, 11.0, False) for y, text in _COUNTEREXAMPLE_LINES]
         + list(_COUNTEREXAMPLE_TABLE), "3"),
        (_body(120.0, tag="W"), "4"),
        (_body(120.0, tag="X"), "5"),
        (_body(120.0, tag="Y"), "6"),
    ]
    return _labeled(pages)


def _audit_for(res, page: int, line: int):
    for item in res.candidates:
        if item.page_number == page and item.line_index == line:
            return item
    return None


def _evidence_dict(record) -> dict:
    """把审计记录的 `evidence` 元组解析成可断言的真值表字段。"""
    out: dict = {}
    for chunk in record.evidence:
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        out[key] = value
    return out


def _line_matches(res, page: int, line: int, text: str) -> bool:
    record = _audit_for(res, page, line)
    return record is not None and record.text == text


def _test_general_counterexamples() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "ce.pdf", _counterexample_pages())
        layout, ctx = _ctx_of(p, document_id="SYNTH_DOC_CE")
        res = OB.build_document_outline(ctx)
        base = res.stats["body_font_size"]
        check(base == 11.0, f"Q0 正文基准字号由版式众数得到（{base}）")

        # --- 逐行定位反例 / 正例的真实 (页, 行) -----------------------------
        hits: dict = {}
        for page in layout.pages:
            for ln in page.lines:
                if ln.is_furniture:
                    continue
                text = ln.text.strip()
                if text in hits:
                    continue
                hits[text] = (page.page_number, ln.line_index)
        missing = [t for t in list(_SMALL_HEADINGS)
                   + [t for _y, t in _COUNTEREXAMPLE_LINES]
                   + [t for _x, _y, t, _s, _b in _COUNTEREXAMPLE_TABLE]
                   if t not in hits]
        check(not missing, f"Q1 全部反例 / 正例样本都定位到真实版式行（缺 {missing}）")
        if missing:
            return
        titles = _titles(res)

        # --- 1) 只有编号和符号的行：硬守卫拒绝 -------------------------------
        pn, li = hits["（2）="]
        check(res.stats["false_positive_reason_distribution"]
              .get("numbering_only_line", 0) >= 1,
              "Q2 只含编号和符号的行命中 numbering_only_line 守卫")
        check(_line_matches(res, pn, li, "（2）=")
              and "numbering_only_line" in
              _audit_for(res, pn, li).reason_codes,
              "Q2b 该行有候选记录且带显式拒绝原因（不是被静默忽略）")
        check("（2）=" not in titles, "Q2c 只含编号和符号的行不成标题")

        # --- 2) 人员履历 / 定义条款 / 风险步骤列表 / 公式 --------------------
        never_headings = (
            "（一）张三，男，1975年生，现任本公司董事",
            "（二）李四，女，1978年生，现任本公司监事",
            "（三）王五，男，1970年生，现任本公司高管",
            "（一）识别风险因素并评估影响程度",
            "（二）制定应对措施并落实责任人",
            "（三）定期复核措施有效性评估结果",
            "1、本次交易指公司收购标的公司股权的行为",
            "2、关联方指对公司具有控制关系的法人主体",
            "（1）净资产收益率=净利润/净资产",
            "（1）营业收入",
            "（2）营业成本",
        )
        for text in never_headings:
            check(text not in titles, f"Q3 编号反例不成标题：{text!r}")

        # --- 3) 上一句未结束的正文续写：折行守卫 ------------------------------
        pn, li = hits["（三）公司持续推进产业结构优化并加强内部控制体系建设"]
        record = _audit_for(res, pn, li)
        check(record is not None
              and "wrapped_paragraph_line" in record.reason_codes,
              "Q4 续写正文行命中 wrapped_paragraph_line 守卫"
              f"（{None if record is None else record.reason_codes}）")

        # --- 4) 表格区域内的编号行 -------------------------------------------
        pn, li = hits["（1）营业收入"]
        record = _audit_for(res, pn, li)
        check(record is not None and "table_cell_line" in record.reason_codes,
              "Q5 表格区域里的编号单元格行命中 table_cell_line 守卫"
              f"（{None if record is None else record.reason_codes}）")

        # --- 5) 每个反例都有**明确状态**：拒绝 或 正式 unassigned -------------
        for text in never_headings:
            pn, li = hits[text]
            state = [a.assignment for a in res.line_assignments
                     if a.page_number == pn and a.line_index == li]
            check(state and state[0] in ("ordinary_content", "unassigned_ambiguous"),
                  f"Q6 反例行落在明确状态（{text!r} → {state}）")

        # --- 6) 有效小标题**不因字号小被误删** -------------------------------
        for text in _SMALL_HEADINGS:
            check(text in titles,
                  f"Q7 小字号小标题仍被采纳（未被保守策略误删）：{text!r}")
        pn, li = hits[_SMALL_HEADINGS[0]]
        head = _audit_for(res, pn, li)
        check(head is not None and head.accepted,
              "Q8 小标题在候选审计里标记为 accepted")
        if head is not None:
            ev = _evidence_dict(head)
            check(float(ev["font_size"]) == base and ev["bold"] == "False",
                  f"Q8b 该小标题与正文**同字号、非加粗**（font_size={ev['font_size']}）")
            check(ev["primary_evidence"] == "standalone_line"
                  and ev["supporting_evidence"] == "paragraph_boundary",
                  "Q8c 采纳依据是候选**自身**的「完整独立标题行 + 段落边界」，"
                  "不含字号 / 居中 / 目录，也不含任何候选间版式相似"
                  f"（primary={ev['primary_evidence']!r}, "
                  f"support={ev['supporting_evidence']!r}）")
            check(ev["toc_matches"] == "0" and ev["bookmark_matches"] == "0",
                  "Q8d 该小标题没有目录 / 书签佐证（正文自身结构足以确认）")

        # --- 7) 字号不是判据：反例与正例同字号 --------------------------------
        ce = _audit_for(res, *hits["（一）张三，男，1975年生，现任本公司董事"])
        check(ce is not None and head is not None
              and float(_evidence_dict(ce)["font_size"])
              == float(_evidence_dict(head)["font_size"]),
              "Q9 反例与正例字号完全相同 —— 分流不可能来自字号阈值")
        if ce is not None:
            ev_ce = _evidence_dict(ce)
            check("size_above_body" not in ev_ce["primary_evidence"],
                  "Q9b 反例也没有字号抬升证据（差别只在版式类 / 上下文）")

        # --- 8) 未采纳候选进入正式 unassigned 且原因在封闭词表内 --------------
        spans = list(res.outline.unassigned)
        check(res.stats["unassigned_spans"] == len(spans) and len(spans) >= 1,
              f"Q10 歧义编号候选进入正式 unassigned（{len(spans)} 条）")
        check(all(s.role == "unassigned" and s.node_id is None
                  and s.unassigned_reason in S.UNASSIGNED_REASONS
                  for s in spans),
              "Q10b 正式 unassigned 的 role / node_id / 登记原因全部自洽")
        anchors = {(s.start_anchor[0], s.start_anchor[1]) for s in spans}
        check(hits["（一）张三，男，1975年生，现任本公司董事"] in anchors,
              "Q10c 人员履历行的歧义状态确实由正式 unassigned 承载"
              "（span 锚点就是该真实版式行）")

        # --- 9) 守恒口径与"仅编号无结构证据"计数 -----------------------------
        check(res.stats["accepted_numbering_only"] == 0,
              f"Q11 接受集合里没有任何「证据不足」的候选"
              f"（accepted_numbering_only={res.stats['accepted_numbering_only']}）")
        check(res.stats["accepted_subheading_without_size_signal"] >= 2,
              "Q11b 统计口径如实报告「无字号信号的小标题」接受数"
              f"（{res.stats['accepted_subheading_without_size_signal']}）")
        counts = res.stats["line_assignment_counts"]
        check(counts.get("accepted_heading", 0) == len(titles),
              f"Q12 接受标题数与节点数一致（{counts.get('accepted_heading')} vs "
              f"{len(titles)}）")

        # --- 10) 判定与公司 / 业务词无关：换文档身份不改变结论 ----------------
        layout2, ctx2 = _ctx_of(p, company_id="123456",
                                document_id="SYNTH_DOC_CE_OTHER")
        res2 = OB.build_document_outline(ctx2)
        check(_titles(res2) == titles,
              "Q13 换公司 / 文档身份后标题判定逐条不变（判定与公司无关）")


# ---------------------------------------------------------------------------
# G. 公司无关与依赖边界
# ---------------------------------------------------------------------------

def _test_company_independence() -> None:
    # 非 300750 fixture：公司代码 / 文档标识都是合成的
    with _tmpdir() as d:
        p = _make_pdf(d / "acme.pdf", _basic_pages())
        layout, ctx = _ctx_of(p, company_id="123456", document_id="ACME_BOND_2027")
        res = OB.build_document_outline(ctx)
        check(len(_nodes(res)) >= 5,
              f"O1 非 300750 fixture 正常产出标题树（{len(_nodes(res))} 个节点）")
        check(layout.company_id == "123456", "O2 fixture 公司代码为合成的 123456")
        check("ACME_BOND_2027" in res.outline.document_id, "O3 文档身份闭合到 fixture")

    for name in _TS3_MODULES:
        src = _read_module(name)
        for token in _FORBIDDEN_TOKENS:
            check(token not in src, f"O4[{name}] 无公司 / 答案专用 token：{token!r}")
        imported = _imported_modules(src)
        banned = sorted(imported & set(_BANNED_IMPORTS))
        check(not banned, f"O5[{name}] 无非确定 / 越界依赖（实际 {banned}）")
        code = _code_only(src)
        for token in ("fixed_page", "answer_key", "表号"):
            check(token not in code, f"O6[{name}] 执行代码无 {token!r}")
    # 不硬编码冻结阈值
    check("ALIGN_MIN" not in _read_module("outline_builder.py")
          or "V.ALIGN_MIN" in _read_module("outline_builder.py"),
          "O7 构建器不硬编码对齐阈值（如使用则只经 versions 常量）")
    # 生产核心不得反向依赖 evaluation
    for name in _TS3_MODULES:
        imported = _imported_modules(_read_module(name))
        check("evaluation" not in imported and "evals" not in imported,
              f"O8[{name}] 生产核心不反向 import evaluation / evals")


def _test_version_and_scope_boundaries() -> None:
    src = _read_module("outline_builder.py")
    # 生产模块自带的自检必须全绿：否则"实现"与"它自己声称的行为"已经矛盾。
    report = OB.self_check()
    failed_checks = [c for c in report["checks"] if not c.startswith("PASS ")]
    check(report["ok"] and not failed_checks,
          f"P0 生产模块自检全绿（失败项 {failed_checks}）")
    check(OB.OUTLINE_BUILDER_VERSION == V.OUTLINE_ALGORITHM_VERSION,
          "P1 构建器版本与公共 OUTLINE_ALGORITHM_VERSION 同源")
    check("version_literal" not in src and '"oa-1"' not in src,
          "P2 生产模块不含版本字面量（版本字面量只在 versions.py）")
    check("cross_reference" in src and "pending_ts4" in src,
          "P3 cross_reference 只登记为 pending_ts4（不产出边）")
    check(not _passed_as_argument(src, "table_continuation"),
          "P4 生产模块不把 table_continuation 传进任何构造（属 TS5，不生成）")
    built = _built_outlines()
    check(built and all(edge.edge_kind in ("parent_child", "toc_to_body")
                        for res in built for edge in res.outline.edges),
          "P4b 实际产出的边只有 parent_child / toc_to_body")
    check(all(edge.edge_kind in S.EDGE_KINDS
              for res in built for edge in res.outline.edges),
          "P4c 产出的边类型都属于公共 EDGE_KINDS")
    check("OutlineSpan" not in src or "pending_ts4" in src,
          "P5 OutlineSpan 正文边界属 TS4，本轮不实现")
    # 唯一生产实现：评测侧必须是同一对象（不是第二份副本）
    import evaluation.run_tree_layout_acceptance as RA
    import document_structure.aligner as AL
    check(RA.classify_residue is AL.classify_residue,
          "P6 评测侧对齐原语与生产核心是同一对象（无第二份副本）")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> dict:
    groups = (
        _test_s3_is_the_primary_path,
        _test_two_documents_without_bookmarks,
        _test_bookmarks_cannot_create_nodes,
        _test_mid_page_subheadings,
        _test_false_positive_guards,
        _test_heading_structure_reuse,
        _test_identity_and_levels,
        _test_level_conflicts_have_reasons,
        _test_unnumbered_style_path,
        _test_cross_page_levels,
        _test_toc_and_page_labels,
        _test_toc_page_conflict_no_guessing,
        _test_source_context_fail_closed,
        _test_tampered_nodes_fail_closed,
        _test_determinism_and_conservation,
        _test_formal_unassigned,
        _test_general_counterexamples,
        _test_company_independence,
        _test_version_and_scope_boundaries,
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
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

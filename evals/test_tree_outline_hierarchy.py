"""TS3 标题层级 / 局部结构域 / 正式未归属的**反例**测试（任务书 §三、§四、§五）。

本模块只做一件事：把任务书要求的反例固化成**可执行**断言，覆盖：

A. §三 P1-A 层级：
   - 缺上层的连续 `1、2、3、4` 必须仍是 **siblings**（不得因第一个标题被压缩成
     顶层后，把后续同级标题挂到它下面）；
   - 首次缺级可以确定性压缩，但**同一 declared_level 必须保持同一有效深度**；
   - `一、→（一）→1、→（1）` 正常嵌套；
   - 编号体系切换 / 章节重置时才重建局部映射；同一文档不同章节重新从 `1、`
     开始时**不串树**。
B. §四 P1-B 反循环自证：
   - 跨页、多行的阿拉伯数字人员履历（`7、某先生，47岁……` / `8、某先生……`）
     不得成为标题；
   - `二、重要缺陷：是指……` 等定义正文不得成为标题；
   - 普通风险列表 / 产品列表不得成为标题，而真正的小标题仍被接受。
C. §五 P1-C 正式未归属：
   - 可定位但未成为正式节点的候选**全部**进入 `DocumentOutline.unassigned`；
   - 细分原因至少覆盖 5 类，且**不得**压成 `boundary_ambiguous`；
   - 未归属 span 只是结构候选，不是 Evidence-backed material。

修订说明（`hq-2` 标题资格 profile）：`level_layout`（同级候选共享版式类）已从**主证据**
降格为**辅证据**，并且只在**同页或相邻页这一辅助邻近窗口**（`NEARBY_PAGE_WINDOW_PAGES`——
它**不是**已验证的结构域，旧名 `LOCAL_DOMAIN_PAGE_SPAN` 已按诚实命名删除）内出借 ——
出借方必须是一条**自证成立**的候选（自己带字号抬升 / 居中 / 加粗 / 目录书签佐证），
受借方必须与它同层、同版式类。"整份文档里另有某条候选人长得像"因此永远无法单独
给一条候选提供标题资格。一条编号候选的标题资格改由**它自己**的结构事实支撑：强主证据
（字号抬升 / 居中 / 目录书签佐证），或弱主证据 `standalone_line`（本行形态完整、未折行、
未被截断、且**不是通用表格 / 网格区域的成员**——仅仅邻近表格不影响它）。

本模块的 D / E / F / G / H 组断言按这一新口径固化。D5 / E4 直接检查"两条候选之间只有
版式相似"这条路径**不存在**（两条履历行 / 两条定义行的 `primary_evidence` 均为空）。
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import shutil
import tempfile

import fitz

import document_structure
from document_structure import layout_builder as LB
from document_structure import outline_builder as OB
from document_structure import schema as S
from document_structure.canonical import canonical_json
from document_structure.normalization import tight

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent

_A4 = (595.0, 842.0)

#: 正文行：**全部短于 `COLUMN_RIGHT_MIN_TIGHT_LEN`**，这样"文档正文栏右边界"由本
#: 模块显式加入的 `_LONG` 决定，不会被短行稀释（众数必须可预测，否则测试会随
#: fixture 微调而漂移）。
_BODY = (
    "本公司报告期内经营稳定，主营业务收入保持增长。",
    "公司持续推进技术研发与产能建设，详见后文说明。",
    "报告期内未发生对经营产生重大影响的事项。",
)
#: 长正文行（≥ `COLUMN_RIGHT_MIN_TIGHT_LEN`）：用来把"文档正文栏右边界"钉在一个
#: 明确的位置上，供折行判据（`page_right_edges` / `document_column_right`）使用。
_LONG = "报告期内公司主营业务保持稳定增长态势，各项经营指标均符合年度计划安排。"

#: 目录项的点线填充（真实 PDF 形态）。
_TOC_LEADER = " .........."


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


@contextlib.contextmanager
def _tmpdir():
    d = tempfile.mkdtemp(prefix="ts3_hier_")
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _body(y0: float = 250.0, step: float = 22.0, tag: str = "",
          long_line: bool = False) -> list:
    items = [(72.0, y0 + i * step, t + tag, 11.0, False)
             for i, t in enumerate(_BODY)]
    if long_line:
        items.append((72.0, y0 + len(_BODY) * step, _LONG + tag, 11.0, False))
    return items


def _make_pdf(path: pathlib.Path, pages: list, toc=None) -> pathlib.Path:
    """生成电子 PDF；`pages` 每项 = `(items, label)`，item = `(x, y, 文本, 字号, 粗体)`。"""
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


def _ctx_of(path: pathlib.Path, company_id: str = "999999",
            document_id: str = "SYNTH_DOC_2026"):
    layout = LB.build_page_layout(
        path, LB.LayoutBuildContext(company_id=company_id, document_id=document_id))
    return layout, OB.load_source_context(
        path, layout, company_id=company_id, document_id=document_id)


def _build(pages: list, toc=None):
    """构建一次，返回 `(layout, context, result, candidates)`。"""
    with _tmpdir() as d:
        pdf = _make_pdf(d / "hier.pdf", pages, toc=toc)
        layout, ctx = _ctx_of(pdf)
        result = OB.build_document_outline(ctx)
        cands = OB.scan_heading_candidates(ctx)
    return layout, ctx, result, cands


def _titles(res) -> list:
    return [n.title for n in res.outline.nodes]


def _levels(res) -> list:
    return [n.level for n in res.outline.nodes]


def _parent_title(res, node) -> str:
    if node.parent_id is None:
        return ""
    for other in res.outline.nodes:
        if other.node_id == node.parent_id:
            return other.title
    return "?"


def _unassigned(res) -> list:
    return list(res.outline.unassigned)


def _unassigned_reasons(res) -> dict:
    counts: dict = {}
    for span in res.outline.unassigned:
        counts[span.unassigned_reason] = counts.get(span.unassigned_reason, 0) + 1
    return counts


def _cand_for(cands, title: str):
    for cand in cands:
        if tight(cand.title) == tight(title):
            return cand
    return None


def _reason_for(res, title: str) -> str:
    """该标题（正文候选）在正式 `unassigned` 里的原因码；不是未归属返回 ''。

    只认**精确相等**的归一文本：目录项 `第三节 环境与社会责任` 的归一文本里也含有
    `环境与社会责任`，用子串匹配会把两个来源的原因互相冒充。
    """
    flat = tight(title)
    for span in res.outline.unassigned:
        if flat and flat == span.normalized_text:
            return span.unassigned_reason
    return ""


def _node_titled(res, title: str):
    flat = tight(title)
    for node in res.outline.nodes:
        if node.title_normalized == flat:
            return node
    return None


def _tree_view(outline) -> dict:
    """树的**语义视图**（不含由 locator 派生的 id），用于两次构建的比较。"""
    view = []
    for node in outline.nodes:
        view.append({
            "title": node.title, "level": node.level,
            "path": list(node.structural_path), "ordinal": node.ordinal,
            "parent_path": None if node.parent_id is None else
            [n.structural_path for n in outline.nodes
             if n.node_id == node.parent_id][0],
        })
    return view


# ---------------------------------------------------------------------------
# fixture：合成文档
# ---------------------------------------------------------------------------

def _pages_siblings() -> list:
    """§三(3)：缺失上层后的连续 `1、2、3、4`（同一编号体系）。

    四个标题自身相对正文基准都有字号抬升（所以它们**不是**靠"和邻居长得像"
    被接受的），但声明的层级（`1、` = 4 级）比当前局部结构域深 —— 首个被确定性
    压缩成顶层之后，`2、3、4` **不得**被挂到它下面。
    """
    heads = ("1、研发投入情况", "2、主要子公司情况", "3、员工构成情况",
             "4、诉讼与仲裁事项")
    items = []
    y = 80.0
    for head in heads:
        items.append((104.0, y, head, 12.0, False))
        y += 26.0
        items.extend(_body(y))
        y += len(_BODY) * 22.0 + 18.0
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (items, "1"),
        (_body(120.0, long_line=True), "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_nested() -> list:
    """§三(1)(2)(4)：`第一节 → 一、→（一）→1、→（1）` 正常嵌套；
    随后 `二、` 重新建立局部映射，其下的 `1、` 是**新**域的第一项。"""
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 公司业务", 16.0, False),
          (72.0, 100.0, "一、主要业务", 14.0, False)] + _body(140.0)
         + [(72.0, 230.0, "（一）主营业务分析", 12.5, False)] + _body(260.0)
         + [(72.0, 350.0, "1、主要会计数据", 12.0, False)] + _body(380.0)
         + [(72.0, 470.0, "（1）营业收入构成", 11.6, False)] + _body(500.0), "1"),
        ([(72.0, 72.0, "二、期间费用", 14.0, False)] + _body(110.0)
         + [(72.0, 200.0, "1、销售费用", 12.0, False)] + _body(230.0), "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_chapter_restart() -> list:
    """§三(4)：章节重置后重新从 `1、` 开始 —— 不得串到上一章的 `1、` 下面。"""
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 经营情况", 16.0, False),
          (72.0, 104.0, "1、主营业务收入", 12.0, False)] + _body(140.0)
         + [(72.0, 230.0, "2、主营业务成本", 12.0, False)] + _body(260.0), "1"),
        ([(72.0, 72.0, "第二节 财务情况", 16.0, False),
          (72.0, 104.0, "1、资产结构", 12.0, False)] + _body(140.0)
         + [(72.0, 230.0, "2、负债结构", 12.0, False)] + _body(260.0), "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


#: §四(2)：跨页、多行的阿拉伯数字人员履历。两条**等长**且各自独占一页：该页正文栏
#: 最右边缘由它们自己决定，从而构成"已排到正文栏边缘的折行续写行"这一单行版面事实。
_RESUME_A = "7、某先生，47岁，中国国籍，2020年起任本公司独立董事，"
_RESUME_B = "8、某先生，47岁，中国国籍，2021年起任本公司监事职务，"


def _pages_resume() -> list:
    """§四(2)：履历正文不得成为标题。

    两条履历行**各自**排到了所在页正文栏最右边缘（折行续写行），而且它们不在同一
    页、也不与任何同层编号行相邻 —— 因此既不是"证据不足"、也不是"跳级"、也不是
    "同层列表上下文"，只能靠"和另一条候选版式相似"互相证明。按编号声明的层级在当前
    局部结构域里是合法的（栈里已有同编号体系的前序标题）。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 公司治理", 16.0, False)] + _body(110.0), "1"),
        ([(72.0, 72.0, "一、董事及高级管理人员", 14.0, False)] + _body(110.0)
         + [(72.0, 200.0, "（一）董事", 12.5, False)] + _body(230.0)
         + [(72.0, 320.0, "1、董事基本情况", 12.0, False)] + _body(350.0), "2"),
        ([(75.0, 100.0, _RESUME_A, 11.0, False)], "3"),
        ([(75.0, 100.0, _RESUME_B, 11.0, False)], "4"),
        (_body(120.0, long_line=True), "5"),
        (_body(120.0, long_line=True), "6"),
        (_body(120.0, long_line=True), "7"),
    ]


def _pages_definition() -> list:
    """§四：定义正文（`二、重要缺陷：是指……`）不得成为标题。

    第 2 页的定义行出现在**任何已采纳标题之前**（跳级 → `hierarchy_conflict`）；
    第 3 页的两条定义行同版式类（互为 `level_layout` 唯一主证据），必须因为
    **自身文字形态**就是普通编号正文而降级；同页 `四、缺陷认定标准说明` 与任何
    候选都不同版式类（x0 不同）→ 证据不足。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(80.0, 100.0, "一、重大缺陷：是指可能严重影响财务报告使用者决策的缺陷",
           11.0, False)] + _body(140.0), "1"),
        ([(72.0, 72.0, "第一节 内部控制评价", 16.0, False)]
         + [(72.0, 110.0, "二、重要缺陷：是指内部控制设计或运行中存在的缺陷",
             11.0, False)] + _body(140.0)
         + [(72.0, 230.0, "三、一般缺陷：是指除重要缺陷以外的其他控制缺陷",
             11.0, False)] + _body(260.0)
         + [(84.0, 350.0, "四、缺陷认定标准说明", 11.0, False)] + _body(380.0),
         "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_list_vs_heading() -> list:
    """§四：普通风险列表（同层编号正文成串）不得成为标题；真正的小标题仍被接受。"""
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 风险因素", 16.0, False),
          (72.0, 100.0, "一、主要风险说明", 14.0, False)]
         + [(72.0, 130.0, "（一）市场需求波动风险", 11.0, False),
            (72.0, 152.0, "（二）原材料价格波动风险", 11.0, False),
            (72.0, 174.0, "（三）技术迭代风险", 11.0, False),
            (72.0, 196.0, "（四）汇率波动风险", 11.0, False)]
         + _body(230.0), "1"),
        ([(104.0, 72.0, "（一）行业竞争格局", 12.5, False)] + _body(110.0)
         + [(104.0, 200.0, "（二）技术发展趋势", 12.5, False)] + _body(230.0),
         "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_insufficient_evidence() -> list:
    """§三.7：证据不足但**可能是**标题的编号候选必须进入 `formal_unassigned`。

    `1、内部控制评价范围` 处在正文栏左边界、字号等于正文基准、行前没有段落间距（上一
    行距它只有一行的行距）、也没有同层同版式的候选 —— 它**没有任何辅证据**，只有自己的
    弱主证据 `standalone_line`。按 `hq-1` 这不够（主+辅 < 2），因此 fail-closed 到正式
    未归属，而不是"静默消失"，也不是"为提高召回强升为节点"。
    """
    # 上一行的**真实行尾**不是句末标点，且与目标行之间没有段落间距 —— 因此目标行拿不到
    # `paragraph_boundary`（判据只看行尾字符与真实垂直间距，不看文本里句号出现在哪里）。
    tight_body = [(72.0, y, text, 11.0, False)
                  for y, text in ((130.0, _BODY[0]), (143.0, _BODY[1]),
                                  (156.0, "报告期内未发生对经营产生重大影响的事项"))]
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 内部控制评价", 16.0, False),
          (72.0, 96.0, "一、风险评估", 14.0, False),
          (72.0, 118.0, "（一）风险识别", 12.5, False)]
         + tight_body
         + [(72.0, 169.0, "1、内部控制评价范围", 11.0, False)]
         + _body(195.0), "1"),
        (_body(120.0, long_line=True), "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_generic_subheading() -> list:
    """§五(1)：紧邻同层编号说明正文之后的**被抬升**编号小标题必须被召回。"""
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "第一节 募集资金使用情况", 16.0, False)]
         + [(72.0, 104.0, "1、截至报告期末募集资金使用情况说明", 11.0, False)]
         + _body(130.0)
         + [(72.0, 220.0, "2、募集资金承诺项目情况", 12.0, False)] + _body(250.0)
         + [(72.0, 340.0, "3、母公司资产负债表", 12.0, False)] + _body(370.0),
         "1"),
        (_body(120.0, long_line=True), "2"),
        (_body(120.0, long_line=True), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_toc_bookmark() -> list:
    """§五(3)：目录项 / 书签有真实行但未成为节点时，必须进入正式未归属。"""
    body_line = "环境与社会责任"
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "目录", 16.0, False),
          (72.0, 100.0, "第一节 经营情况" + _TOC_LEADER + " 2", 11.0, False),
          (72.0, 122.0, "第二节 财务情况" + _TOC_LEADER + " 3", 11.0, False),
          (72.0, 144.0, "第三节 环境与社会责任" + _TOC_LEADER + " 4",
           11.0, False)] + _body(180.0, long_line=True), "1"),
        ([(72.0, 72.0, "第一节 经营情况", 16.0, False)] + _body(110.0), "2"),
        ([(72.0, 72.0, "第二节 财务情况", 16.0, False)]
         + [(72.0, 104.0, body_line, 11.0, False)] + _body(130.0), "3"),
        (_body(120.0, long_line=True), "4"),
        (_body(120.0, long_line=True), "5"),
    ]


def _pages_unassigned_order() -> list:
    """§五：两条书签各自落到一条真实正文行（都进正式 unassigned）。

    这样反转书签顺序时，若 unassigned 按**到达顺序**落盘，两条 span 的次序就会交换。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        ([(72.0, 72.0, "目录", 16.0, False),
          (72.0, 100.0, "第一节 经营情况" + _TOC_LEADER + " 2", 11.0, False),
          (72.0, 122.0, "第二节 财务情况" + _TOC_LEADER + " 3", 11.0, False),
          (72.0, 144.0, "第三节 环境情况" + _TOC_LEADER + " 3", 11.0, False)]
         + _body(180.0, long_line=True), "1"),
        ([(72.0, 72.0, "第一节 经营情况", 16.0, False),
          (72.0, 104.0, "研发投入情况", 11.0, False)] + _body(130.0), "2"),
        ([(72.0, 72.0, "第二节 财务情况", 16.0, False),
          (72.0, 104.0, "人员构成情况", 11.0, False)] + _body(130.0), "3"),
    ]


# ---------------------------------------------------------------------------
# A. §三 层级
# ---------------------------------------------------------------------------

def _test_missing_upper_siblings_stay_siblings() -> None:
    """缺失上层后的连续 `1、2、3、4`：全部是顶层 siblings，且各自留痕 `level_jump_clamped`。"""
    layout, _ctx, res, cands = _build(_pages_siblings())
    titles = _titles(res)
    check(titles == ["1、研发投入情况", "2、主要子公司情况", "3、员工构成情况",
                     "4、诉讼与仲裁事项"],
          f"A1 四个同级标题全部被采纳（实际 {titles}）")
    check(_levels(res) == [0, 0, 0, 0],
          f"A2 四个标题的有效层级**全部相同**（实际 {_levels(res)}）")
    check(all(n.parent_id is None for n in res.outline.nodes),
          "A3 四个标题的父节点都是 None（没有把 2/3/4 挂到 1 下面）")
    check([list(n.structural_path) for n in res.outline.nodes]
          == [[n.title_normalized] for n in res.outline.nodes],
          "A4 四个标题的 structural_path 都是单段（不构成父子链）")
    jumps = [r for r in res.candidates
             if r.accepted and "level_jump_clamped" in r.reason_codes]
    check(len(jumps) == 4, f"A5 四条都留下 level_jump_clamped 痕迹（实际 {len(jumps)}）")
    # 声明层级确实比局部结构域深（否则上面的压缩断言没有意义）。
    declared = {c.declared_level for c in cands if c.title in titles}
    check(declared == {3}, f"A6 四条候选的 declared_level 同为 3（实际 {declared}）")
    check(all(n.level == len(n.structural_path) - 1 for n in res.outline.nodes),
          "A7 level 与 structural_path 长度自洽")


def _test_clamped_stack_key_would_nest_siblings() -> None:
    """反例捕获力：**拿压缩后的有效层级当栈键**这一失效模式会让同级标题串成父子。

    这是一个最小模型（不是对历史实现的复述）：只要栈里存的是"压缩后的层级"，
    第二个同级标题就会被挂到第一个下面 —— 正是任务书 §三(3) 禁止的形态。
    """
    def _levels_with_clamped_key(declared_levels: list) -> list:
        stack: list = []
        out: list = []
        for declared in declared_levels:
            while stack and stack[-1] >= declared:
                stack.pop()
            level = len(stack)
            out.append(level)
            stack.append(min(declared, level) if declared > level else declared)
        return out

    broken = _levels_with_clamped_key([3, 3, 3, 3])
    check(broken == [0, 1, 2, 3],
          f"A8 失效模式模型确实把同级标题串成父子（{broken}）")
    _layout, _ctx, res, _c = _build(_pages_siblings())
    check(_levels(res) == [0, 0, 0, 0] and broken != _levels(res),
          "A9 真实构建的层级与失效模式不同（同级标题保持 siblings）")


def _test_standard_nesting_levels() -> None:
    """`第一节 → 一、→（一）→1、→（1）`：层级 0..4 与父子链完全对齐。"""
    _layout, _ctx, res, _c = _build(_pages_nested())
    titles = _titles(res)
    expect = ["第一节 公司业务", "一、主要业务", "（一）主营业务分析",
              "1、主要会计数据", "（1）营业收入构成", "二、期间费用",
              "1、销售费用"]
    check(titles == expect, f"B1 正常嵌套顺序与层级（实际 {titles}）")
    check(_levels(res) == [0, 1, 2, 3, 4, 1, 2],
          f"B2 有效层级为 0..4 后回到 1/2（实际 {_levels(res)}）")
    parents = [_parent_title(res, n) for n in res.outline.nodes]
    check(parents == ["", "第一节 公司业务", "一、主要业务", "（一）主营业务分析",
                      "1、主要会计数据", "第一节 公司业务", "二、期间费用"],
          f"B3 父子链正确（实际 {parents}）")
    check(all(n.level == len(n.structural_path) - 1 for n in res.outline.nodes),
          "B4 structural_path 与 level 自洽")
    # `二、` 之后重新出现的 `1、` 属于**新**的局部结构域：不得挂到 `1、主要会计数据` 下面。
    new_first = _node_titled(res, "1、销售费用")
    old_first = _node_titled(res, "1、主要会计数据")
    check(new_first is not None and new_first.parent_id != old_first.node_id,
          "B5 新的 `1、` 序列不串到前一个 `1、` 下面")


def _test_chapter_restart_does_not_crosslink() -> None:
    """章节重置后重新从 `1、` 开始：第二章的两条必须挂第二章标题，而不是第一章。"""
    _layout, _ctx, res, _c = _build(_pages_chapter_restart())
    titles = _titles(res)
    check(titles == ["第一节 经营情况", "1、主营业务收入", "2、主营业务成本",
                     "第二节 财务情况", "1、资产结构", "2、负债结构"],
          f"C1 章节结构与顺序（实际 {titles}）")
    check(_levels(res) == [0, 1, 1, 0, 1, 1],
          f"C2 重置后层级重建（实际 {_levels(res)}）")
    parents = [_parent_title(res, n) for n in res.outline.nodes]
    check(parents == ["", "第一节 经营情况", "第一节 经营情况",
                      "", "第二节 财务情况", "第二节 财务情况"],
          f"C3 第二章的 `1、/2、` 挂在第二章标题下（实际 {parents}）")
    ch2_first = _node_titled(res, "1、资产结构")
    check(ch2_first is not None and list(ch2_first.structural_path)
          == [tight("第二节 财务情况"), tight("1、资产结构")],
          f"C4 同编号体系在章节重置处重建局部映射"
          f"（实际 {list(ch2_first.structural_path) if ch2_first else None}）")


# ---------------------------------------------------------------------------
# B. §四 反循环自证 / 普通编号正文
# ---------------------------------------------------------------------------

def _test_resume_lines_are_not_headings() -> None:
    """跨页、多行的阿拉伯数字人员履历：不得成为标题，且必须进入正式未归属。"""
    check(len(tight(_RESUME_A)) == len(tight(_RESUME_B)),
          "D0 fixture 两条履历行等长（所在页的最右边缘由它们自己决定）")
    _layout, _ctx, res, cands = _build(_pages_resume())
    for text in (_RESUME_A, _RESUME_B):
        node = _node_titled(res, text)
        check(node is None, f"D1 履历行不是标题节点：{text[:12]}…")
        check(_reason_for(res, text) == OB.UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
              f"D2 履历行进入正式未归属且原因是普通编号正文：{text[:12]}…")
    flagged = [_cand_for(cands, t) for t in (_RESUME_A, _RESUME_B)]
    check(all(c is not None and c.wrap_flagged for c in flagged),
          "D3 两条履历行都排到了本页正文栏最右边缘（折行续写行，单行版面事实）")
    check(all(OB.AMBIGUOUS_WRAPPED_PROSE_REASON in c.soft_reasons for c in flagged),
          "D4 两条都被折行普通编号正文规则显式降级（不是静默丢弃）")
    check(all(not c.primary_evidence and not c.strong_primary_evidence
              for c in flagged),
          "D5 两条**自身没有任何主证据**：标题资格不能由候选之间的版式相似提供"
          "（§二.1 取消全文件互证）")
    check(all("level_layout" not in c.supporting_evidence for c in flagged),
          "D5b 局部结构域里也没有自证成立的出借方 —— 版式相似连辅证据都提供不了")
    # 先建立合法局部结构域：栈里已有同编号体系的前序标题，因此这不是"跳级"。
    check(_reason_for(res, _RESUME_A) != OB.UNASSIGNED_HIERARCHY_CONFLICT,
          "D6 履历行未归属原因不是 hierarchy_conflict（局部结构域合法）")


def _test_definition_prose_is_not_a_heading() -> None:
    """`二、重要缺陷：是指……` 等定义正文：不得成为标题。

    `四、缺陷认定标准说明` 是**名称形态**（无定义冒号、短行、行首内缩）的编号行：按
    `hq-1` 它有自己的弱主证据 `standalone_line`（本行完整、未折行、不落在网格区域内）
    加 `column_indent` / `paragraph_boundary` 两条辅证据，因此被采纳 —— 这正是 §二.2
    要求的"标题资格由候选自身的结构证据支撑"，不是靠与邻居相似。
    """
    _layout, _ctx, res, cands = _build(_pages_definition())
    check(_titles(res) == ["第一节 内部控制评价", "四、缺陷认定标准说明"],
          f"E1 定义正文不被采纳，名称形态的编号小标题被采纳（实际 {_titles(res)}）")
    for text, expect in (
        ("二、重要缺陷：是指内部控制设计或运行中存在的缺陷",
         OB.UNASSIGNED_NUMBERED_LIST_AMBIGUITY),
        ("三、一般缺陷：是指除重要缺陷以外的其他控制缺陷",
         OB.UNASSIGNED_NUMBERED_LIST_AMBIGUITY),
        ("一、重大缺陷：是指可能严重影响财务报告使用者决策的缺陷",
         OB.UNASSIGNED_HIERARCHY_CONFLICT),
    ):
        check(_node_titled(res, text) is None, f"E2 定义正文不是标题：{text[:10]}…")
        check(_reason_for(res, text) == expect,
              f"E3 {text[:10]}… 的正式未归属原因 = {expect}"
              f"（实际 {_reason_for(res, text)!r}）")
    pair = [_cand_for(cands, t) for t in
            ("二、重要缺陷：是指内部控制设计或运行中存在的缺陷",
             "三、一般缺陷：是指除重要缺陷以外的其他控制缺陷")]
    check(all(c is not None and not c.strong_primary_evidence
              and "level_layout" not in c.supporting_evidence for c in pair),
          "E4 两条定义行之间没有任何「版式相似互证」证据（§二.1）")
    check(all(not c.wrap_flagged for c in pair),
          "E5 它们并没有排到栏右边缘 —— 降级原因不能靠折行规则解释")
    check(all(c.soft_reasons for c in pair),
          "E6 它们被**自身文字形态**（子句链 / 定义冒号+定义谓词）显式降级")
    named = _cand_for(cands, "四、缺陷认定标准说明")
    check(named is not None and named.accepted
          and "standalone_line" in named.primary_evidence
          and not named.strong_primary_evidence,
          "E7 名称形态的编号行靠**自身**的弱主证据被采纳（不靠邻居）")


def _test_list_run_vs_real_headings() -> None:
    """普通风险列表不得成为标题；对照页里真正的小标题必须仍被接受。"""
    _layout, _ctx, res, cands = _build(_pages_list_vs_heading())
    list_items = ("（一）市场需求波动风险", "（二）原材料价格波动风险",
                  "（三）技术迭代风险", "（四）汇率波动风险")
    for text in list_items:
        check(_node_titled(res, text) is None, f"F1 列表项不是标题：{text}")
        check(_reason_for(res, text) == OB.UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
              f"F2 列表项进入正式未归属（原因 = 同层编号列表）：{text}")
    for text in ("（一）行业竞争格局", "（二）技术发展趋势"):
        node = _node_titled(res, text)
        check(node is not None, f"F3 对照页的真实小标题被接受：{text}")
    check(_levels(res)[-2:] == [2, 2],
          "F4 对照页两条小标题是同级（声明层级 2 且前面已有 `第一节`/`一、` 两级，"
          f"因此有效深度为 2；实际 {_levels(res)[-2:]}）")
    flagged = _cand_for(cands, list_items[0])
    check(flagged is not None
          and OB.AMBIGUOUS_LIST_RUN_REASON in flagged.soft_reasons,
          "F5 列表项的降级原因是同层编号列表上下文（可执行细分原因）")
    real = _cand_for(cands, "（二）技术发展趋势")
    check(real is not None and real.accepted and not real.soft_reasons,
          "F6 真正的小标题没有被列表规则误伤")


def _test_generic_subheadings_are_recalled() -> None:
    """§五(1) / §三.6：正文尺寸、左齐、单行的通用小标题必须**靠自身证据**被召回。

    `2、募集资金承诺项目情况` 与 `3、母公司资产负债表` 是真实的正文小标题形态；它们
    **没有**字号抬升，也不是居中 —— 在旧模型里只能靠"和同类候选版式相似"取得资格。
    在 `hq-1` 下它们靠自己的 `standalone_line`（本行完整、未折行、不在网格区域内）
    加 `paragraph_boundary` / `column_indent` 被召回，`level_layout` 只是局部辅证据。
    """
    _layout, _ctx, res, cands = _build(_pages_generic_subheading())
    for text in ("1、截至报告期末募集资金使用情况说明",
                 "2、募集资金承诺项目情况", "3、母公司资产负债表"):
        node = _node_titled(res, text)
        check(node is not None, f"G1 通用小标题形态被召回：{text}")
    prose = _cand_for(cands, "1、截至报告期末募集资金使用情况说明")
    lifted = _cand_for(cands, "2、募集资金承诺项目情况")
    check(prose is not None and not prose.rejected,
          "G2 触发条件存在：前一条是同层编号正文（未被硬拒）")
    check(prose is not None and not prose.strong_primary_evidence
          and "standalone_line" in prose.primary_evidence,
          "G3 前一条的资格来自**自身**的弱主证据，不是与邻居版式相似")
    check(lifted is not None and lifted.accepted,
          "G4 被抬升的编号小标题仍被采纳（自身有单行版面事实）")
    check(lifted is not None and lifted.font_size > prose.font_size,
          "G5 字号抬升是**额外**的强主证据（不是召回的唯一依据）")
    check(lifted is not None and "size_above_body" in lifted.strong_primary_evidence
          and lifted.strong_primary_evidence,
          "G5b 被抬升的那条自己带强主证据（可与邻居无关地成立）")
    check(_levels(res)[-2:] == [1, 1],
          f"G6 被召回的两条在同一局部结构域下保持同级（实际 {_levels(res)[-2:]}）")


# ---------------------------------------------------------------------------
# C. §五 正式未归属
# ---------------------------------------------------------------------------

def _test_insufficient_evidence_stays_explicit() -> None:
    """§三.7：证据不足的编号候选进入正式未归属，原因精确，且没有静默消失。"""
    _layout, _ctx, res, cands = _build(_pages_insufficient_evidence())
    text = "1、内部控制评价范围"
    cand = _cand_for(cands, text)
    check(cand is not None and not cand.rejected,
          "J1 反例候选存在且没有被硬拒（降级路径可执行）")
    check(cand is not None and tuple(cand.primary_evidence) == ("standalone_line",)
          and not cand.supporting_evidence,
          f"J2 它只有自身的弱主证据、没有任何辅证据"
          f"（实际 primary={cand.primary_evidence if cand else None} / "
          f"supporting={cand.supporting_evidence if cand else None}）")
    check(_node_titled(res, text) is None,
          "J3 证据不足的候选不得被强升为正式节点")
    check(_reason_for(res, text) == OB.UNASSIGNED_INSUFFICIENT_EVIDENCE,
          f"J4 它进入正式 unassigned 且原因是 insufficient_heading_evidence"
          f"（实际 {_reason_for(res, text)!r}）")
    span = next((s for s in res.outline.unassigned
                 if s.normalized_text == tight(text)), None)
    check(span is not None and cand is not None
          and span.start_anchor[:2] == (cand.page_number, cand.line_index)
          and span.start_anchor == span.end_anchor,
          "J5 未归属 span 锚在那一行真实版式行上（不是旁路统计）")


def _test_formal_unassigned_reasons_distinct() -> None:
    """五类细分原因都可达，且不允许退化成笼统的 `boundary_ambiguous`。"""
    _layout, _ctx, res, _c = _build(_pages_toc_bookmark(),
                                    toc=[[1, "环境与社会责任", 4]])
    reasons = _unassigned_reasons(res)
    check(reasons.get(OB.UNASSIGNED_TOC_UNMATCHED, 0) >= 1,
          f"H1 目录项未对应到节点 → toc_unmatched（实际 {reasons}）")
    check(reasons.get(OB.UNASSIGNED_BOOKMARK_UNMATCHED, 0) >= 1,
          f"H2 书签有真实 landing 行但未成节点 → bookmark_unmatched（实际 {reasons}）")
    check(_reason_for(res, "环境与社会责任") == OB.UNASSIGNED_BOOKMARK_UNMATCHED,
          "H3 书签的 landing 正文行本身带着正式未归属 span（不是只写 sidecar）")
    for span in res.outline.unassigned:
        check(span.unassigned_reason in OB.UNASSIGNED_FORMAL_REASONS,
              f"H4 未归属原因在封闭词表内：{span.unassigned_reason}")
    check("boundary_ambiguous" not in reasons,
          "H5 不得把未归属压成一个笼统的 boundary_ambiguous")
    seen = set()
    for pages, toc in ((_pages_definition(), None), (_pages_resume(), None),
                       (_pages_list_vs_heading(), None),
                       (_pages_insufficient_evidence(), None)):
        _l, _c2, other, _cc = _build(pages, toc=toc)
        seen |= set(_unassigned_reasons(other))
    check(OB.UNASSIGNED_FORMAL_REASONS[0] in seen
          and OB.UNASSIGNED_NUMBERED_LIST_AMBIGUITY in seen
          and OB.UNASSIGNED_HIERARCHY_CONFLICT in seen,
          f"H6 正文侧三类原因都可达（实际 {sorted(seen)}）")
    check(set(seen) <= set(OB.UNASSIGNED_FORMAL_REASONS),
          f"H7 正文侧原因也全部在封闭词表内（实际 {sorted(seen)}）")


def _test_unassigned_is_not_evidence_backed() -> None:
    """未归属 span 只是**结构候选**：不得被表述为 Evidence-backed material。"""
    _layout, _ctx, res, _c = _build(_pages_resume())
    spans = _unassigned(res)
    check(spans, "I1 反例文档确实产生了正式未归属 span")
    for span in spans:
        check(span.node_id is None, "I2 未归属 span 不挂正式节点")
        check(span.role == "unassigned", f"I3 role=unassigned（实际 {span.role}）")
        check(span.confidence < 1.0,
              f"I4 confidence 表示证据充分度且必然未达门槛（{span.confidence}）")
        check(span.layout_line_refs and span.start_anchor == span.end_anchor,
              "I5 未归属 span 是能回溯到真实版式行的单行 span")
    # 四态视图里，这些行是 formal_unassigned，不是 body_under_node / heading_node。
    diag = OB.line_structure_diagnostic(res, _layout)
    states = {(item["page_number"], item["line_index"]): item["structure_state"]
              for item in diag["lines"]}
    for span in spans:
        key = span.layout_line_refs[0]
        check(states.get(key) == OB.LINE_STRUCTURE_FORMAL_UNASSIGNED,
              f"I6 四态归属 = formal_unassigned：{key}")


def _test_determinism_and_input_order_invariance() -> None:
    """相同输入两次构建逐字节一致；书签输入顺序变化不改变父子关系。"""
    import dataclasses
    pages = _pages_nested()
    with _tmpdir() as d:
        pdf = _make_pdf(d / "order.pdf", pages,
                        toc=[[1, "第一节 公司业务", 2], [1, "一、主要业务", 2]])
        layout, ctx = _ctx_of(pdf)
        first = OB.build_document_outline(ctx)
        second = OB.build_document_outline(ctx)
        check(canonical_json(first.outline.to_dict())
              == canonical_json(second.outline.to_dict()),
              "J1 相同输入连续两次构建逐字节一致")
        check(len(ctx.bookmarks) == 2, f"J2 fixture 有两条书签（实际 {len(ctx.bookmarks)}）")
        reversed_ctx = dataclasses.replace(
            ctx, bookmarks=tuple(reversed(ctx.bookmarks)))
        third = OB.build_document_outline(reversed_ctx)
        check(_tree_view(first.outline) == _tree_view(third.outline),
              "J3 书签输入顺序不改变父子关系与层级")
        check([list(n.structural_path) for n in first.outline.nodes]
              == [list(n.structural_path) for n in third.outline.nodes],
              "J4 顺序变化也不改变 structural_path")
    check(_titles(first) == _titles(second),
          "J5 节点标题序列在两次构建中一致")

    # §五 P1-C：正式 unassigned 由**目录项 + 书签**两个独立来源汇合而成。若按到达
    # 顺序落盘，反转书签就会交换 span 次序 —— 成员集合一样，`outline_id` /
    # `content_fingerprint` 却变了，等于让同一份文档有两个身份。反例要求 fixture
    # 至少有**两条**书签能落到真实正文行上（这样交换才可能发生）。
    pages = _pages_unassigned_order()
    with _tmpdir() as d:
        pdf = _make_pdf(d / "tocbm.pdf", pages,
                        toc=[[1, "第一节 经营情况", 2],
                             [1, "第二节 财务情况", 3],
                             [2, "研发投入情况", 2],
                             [2, "人员构成情况", 3],
                             [1, "第三节 环境情况", 3]])
        layout, ctx = _ctx_of(pdf)
        normal = OB.build_document_outline(ctx)
        bookmark_unmatched = [s for s in normal.outline.unassigned
                              if s.unassigned_reason
                              == OB.UNASSIGNED_BOOKMARK_UNMATCHED]
        check(len(ctx.bookmarks) >= 2 and len(bookmark_unmatched) >= 2,
              f"J6 fixture 至少两条书签且至少两条落到真实正文行"
              f"（书签 {len(ctx.bookmarks)} / bookmark_unmatched "
              f"{len(bookmark_unmatched)}）")
        flipped = OB.build_document_outline(dataclasses.replace(
            ctx, bookmarks=tuple(reversed(ctx.bookmarks))))
        check(canonical_json(normal.outline.to_dict())
              == canonical_json(flipped.outline.to_dict()),
              "J7 反转书签输入顺序后 outline JSON 逐字节一致（含正式 unassigned "
              "次序）")
        check(normal.outline.outline_id == flipped.outline.outline_id,
              "J8 反转书签输入顺序不改变 outline_id（成员集合相同 ⇒ 身份相同）")
        check([s.span_id for s in normal.outline.unassigned]
              == [s.span_id for s in flipped.outline.unassigned],
              "J9 正式 unassigned 按版式位置规范排序，不随来源到达顺序变化")
        check(list(normal.outline.unassigned)
              == list(OB.canonical_unassigned_order(normal.outline.unassigned))
              and len(normal.outline.unassigned)
              == len(normal.outline.to_dict()["unassigned"]),
              "J10 落盘顺序就是规范顺序，且与序列化条数一致"
              f"（{len(normal.outline.unassigned)} 条）")


def main() -> dict:
    groups = (
        _test_missing_upper_siblings_stay_siblings,
        _test_clamped_stack_key_would_nest_siblings,
        _test_standard_nesting_levels,
        _test_chapter_restart_does_not_crosslink,
        _test_resume_lines_are_not_headings,
        _test_definition_prose_is_not_a_heading,
        _test_list_run_vs_real_headings,
        _test_generic_subheadings_are_recalled,
        _test_insufficient_evidence_stays_explicit,
        _test_formal_unassigned_reasons_distinct,
        _test_unassigned_is_not_evidence_backed,
        _test_determinism_and_input_order_invariance,
    )
    for fn in groups:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 抛出异常（该组断言未执行）："
                f"{type(e).__name__}: {e}")
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

"""TS3 §三 标题资格与表格污染反例（`hq-4` 资格 profile + `trg-3` 表格区域资格）。

本模块把任务书 §三 点名的十类反例固化成**可执行**断言，覆盖：

1. 两条**相距很远**、字号与行首相同的普通编号表格行，不得互相给对方标题资格；
2. 同一页密集表格行 `1、国家持 / 2、国有法 / 3、其他内` 不得进入 `OutlineNode`；
3. 股东权益表行 `一、上年期 / （一）综合 / 四、本期期` 不得进入 `OutlineNode`；
4. 固定资产表行 `一、账面原值 / 二、累计折旧 / 三、减值准备 / 四、账面价值` 不得
   **仅因全文件同版式**进入 `OutlineNode`；
5. 同一段短标题文字既作独立标题行、又作表格单元格时：标题行可采纳，单元格不可；
6. `四、主营业务分析` / `（一）资产结构分析` 等合法正大小标题仍须凭**自身**结构证据
   通过，不得为了把误收清零而整体降级；
7. 证据不足但可能真是标题的候选必须进入 `formal_unassigned`，不得静默消失；
8. 已有的 siblings / 四态守恒 / 对齐分区 / 证据集合口径不得回归；
9. 独立验收门必须能抓到**手工构造**的表格片段 `OutlineNode`，不再只查逗号子句与定义句；
10. 独立验收门在缺 `PageLayout` / 缺候选审计时 fail-closed，不得返回 clean。

**为什么"旧实现会失败"要在这份文件里复刻而不是切回旧代码**：任务书 §五 明令不得
`git checkout` / `restore`。因此这里把被删除的"全文件候选互证"规则按 §一 的原文复刻
成只读函数 `_legacy_global_peer_accepts`，对**同一份**候选审计事实做第二次判定：
"旧规则会接受、新规则不接受"因此是可执行断言，而不是对旧代码的回忆。

合成 PDF 夹具复用 `evals.test_tree_outline_hierarchy`（**只复用不复制**）。

§四 追加的返修反例（W 组）覆盖表格边界资格：`trg-3` 把通用表格区域拆成
`inside_table`（有成员资格证明）与 `adjacent_to_table`（仅邻近）两层状态。每条反例都
同时断言"旧实现会采纳 / 拒绝"与"新实现相反"。

§二/§三 收口轮追加的 X 组覆盖**安全穿透规则本身**：`hq-4` 取消"两项版式强证据"这条
穿透路径，并把来源面收窄为**对象级验证过的目录项正文 landing** —— `inside_table` 的
唯一穿透依据。生产侧（`safe_table_override`）与独立验收侧（`structural_review_findings`）
采用**同一条**资格，两侧结论分叉时显式阻断（`safe_override_mismatch` /
`unsafe_override_mismatch` / `toc_landing_unverified_accepted`）。

**书签的信任边界（`hq-4` 的关键收窄）**：书签来自 PDF 大纲，**不在** `PageLayout` 里，
复核方能够重算的只有"声明页码 + 标题 ↔ 目标行"两条；它的来源对象身份（条目序号）
无从重建。因此 `hq-4` 起书签：

- **不**参与标题资格（`toc_or_bookmark` 退出证据词表，来源证据面收窄为
  `toc_body_landing`）；
- **不**使 `safe_table_override()` 为真；
- **不**使复核侧 `available` 为真；
- 只作导航候选 / 审计信息，并带确定性原因码 `bookmark_navigation_only`。

`hq-3`（及更早的 `hq-1` / `hq-2`）与 `trg-1` / `trg-2` 登记为 legacy：只显式拒绝、
要求重算。`trg-3` 的几何判据本轮**未变**，因此表格区域资格版本保持 `trg-3`。
"""

from __future__ import annotations

import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import evals.test_tree_outline_hierarchy as H  # noqa: E402  合成 PDF 夹具（复用）

from document_structure import outline_builder as OB  # noqa: E402
from document_structure import schema as S  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.canonical import SchemaValidationError  # noqa: E402
from document_structure.normalization import tight  # noqa: E402

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


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
    except exc as error:
        if substr in str(error):
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{error}）")
    except Exception as error:  # noqa: BLE001
        return check(False,
                     f"{msg}（异常类型 {type(error).__name__} 非 {exc.__name__}：{error}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


# ---------------------------------------------------------------------------
# 被删除的「全文件候选互证」规则的可执行复刻（仅供证明旧实现会犯错）
# ---------------------------------------------------------------------------

#: 旧实现沿用的同级版式类容差（与 `LAYOUT_CLASS_*` 同值，但旧实现**不限页距**）。
_LEGACY_SIZE_TOL_PT = 0.01
_LEGACY_X0_TOL_PT = 3.0
#: 旧实现的采纳门槛：主证据 ≥1 且 主+辅 ≥ 此值。
_LEGACY_MIN_EVIDENCE = 2


def _legacy_global_peer_accepts(candidate, peers) -> bool:
    """旧实现：**整份文档里**任何同 `declared_level`、字号与行首相近的候选，都会给当前
    候选加上 `level_layout` **主证据**，再与一条普通辅证据拼成采纳资格（§一 点名的
    循环自证入口：`level_layout + paragraph_boundary` 即被采纳）。

    复刻只读 `HeadingCandidate` 上**生产侧真实保留**的字段；`supporting` 去掉
    `level_layout` / `heading_context`（这两个是 `hq-1` 判定期才追加的），因此比较的是
    同一份扫描期事实。
    """
    primary = list(candidate.primary_evidence)
    supporting = [code for code in candidate.supporting_evidence
                  if code not in ("level_layout", "heading_context")]
    for other in peers:
        if other is candidate or other.rejected or not other.numbered:
            continue
        if other.declared_level != candidate.declared_level:
            continue
        if abs(other.font_size - candidate.font_size) > _LEGACY_SIZE_TOL_PT:
            continue
        if abs(other.x0 - candidate.x0) > _LEGACY_X0_TOL_PT:
            continue
        primary.append("level_layout")
        break
    return (len(primary) >= 1
            and len(primary) + len(supporting) >= _LEGACY_MIN_EVIDENCE)


# ---------------------------------------------------------------------------
# 通用网格块：表格行标签 / 被列宽截断的单元格
# ---------------------------------------------------------------------------

_LABEL_X0 = 72.0
_VALUE_X0 = (300.0, 480.0)
#: 行标签基线相对数值行**高** 4pt：> `TABLE_ROW_BASELINE_TOL_PT`(3.0)，因此标签行不与
#: 数值行共享基线（不是硬拒绝的 `table_cell_line`），但仍落在同一个水平带里（y 区间
#: 重叠 > `TABLE_REGION_ROW_OVERLAP_TOL_PT`）—— 这正是 §一 描述的真实形态：PDF 版式
#: 引擎把每个单元格输出为独立单 span 行，行标签与数值行并不落在同一条精确基线上。
_LABEL_BASELINE_LIFT_PT = 4.0


def _grid(labels, *, y0: float = 400.0, step: float = 20.0) -> list:
    """通用网格块：每行两个数值单元格 + 一个以句末标点结尾的文字列。

    第三列以 `。` 结尾，模拟真实表格里的文字列；它同时让**下一行**的行标签拿到
    `paragraph_boundary`（判据是上一行的**真实行尾**字符），因此旧实现需要的
    "一条普通辅证据"确实存在 —— 反例不是因为"旧实现连辅证据都没有"才通过的。
    """
    items: list = []
    for index, label in enumerate(labels):
        y = y0 + index * step
        items.append((_LABEL_X0, y - _LABEL_BASELINE_LIFT_PT, label, 11.0, False))
        items.append((_VALUE_X0[0], y, "1,234.56", 11.0, False))
        items.append((_VALUE_X0[1], y, "详见附注。", 11.0, False))
    return items


def _filler(tag: str = "") -> list:
    """无网格的普通正文页（同时把「文档正文栏右边界」钉在可预测的位置上）。"""
    return H._body(120.0, tag=tag, long_line=True)


def _section_head() -> list:
    """章节前言：给网格行标签一条**真实存在**的祖先链。

    没有它，孤立的编号行在树装配那一遍会被判成跳级（`hierarchy_conflict`），未归属
    原因就不再区分"证据不足"与"层级不明" —— 反例的可判读性会下降。三行祖先分别是
    `第一节` / `一、` / `（一）`，字号 16 / 14 / 12.5，与网格行标签（11.0）**不构成
    同级版式类**，因此不会给表行出借任何东西。
    """
    return [(72.0, 60.0, "第一节 主要财务数据", 16.0, False),
            (72.0, 82.0, "一、总体情况", 14.0, False),
            (72.0, 104.0, "（一）主要指标", 12.5, False)]


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _pages_far_apart() -> list:
    """§三.1：两页相距很远、字号与行首完全相同的普通编号表格行。

    第 3 页（印 "2"）与第 9 页（印 "8"）各有一个网格，行标签分别是 `1、国家持` 与
    `2、国有法` —— 同 `declared_level`、同字号、同行首，唯独相隔 6 页。旧实现的全文件
    搜索会把它们配成一对并互相授予 `level_layout` 主证据。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B") + _grid(["1、国家持"]), "2"),
        (_filler("C"), "3"),
        (_filler("D"), "4"),
        (_filler("E"), "5"),
        (_filler("F"), "6"),
        (_filler("G"), "7"),
        (_filler("H") + _grid(["2、国有法"]), "8"),
        (_filler("I"), "9"),
    ]


def _pages_dense_labels() -> list:
    """§三.2：同一页密集表格行 `1、国家持 / 2、国有法 / 3、其他内`。"""
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B")
         + _grid(["1、国家持", "2、国有法", "3、其他内"]), "2"),
        (_filler("C"), "3"),
        (_filler("D"), "4"),
    ]


def _pages_equity_rows() -> list:
    """§三.3：股东权益表行 `一、上年期 / （一）综合 / 四、本期期`。"""
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B")
         + _grid(["一、上年期", "（一）综合", "四、本期期"]), "2"),
        (_filler("C"), "3"),
        (_filler("D"), "4"),
    ]


#: §三.4 的「出借方」目标标题。
_ASSET_LENDER = "四、主营业务分析"
#: 目录行的点线填充（与真实目录项同形：标题 + ≥3 个填充字符 + 空格 + 页标签）。
_TOC_LEADER = " .........."
#: 只作对照的目录项标题（正文里**不存在**这些行）：它们让目录页满足
#: `TOC_MIN_ENTRIES`(3)，同时自身在版式上不可解析 —— 不得因此被编造出任何节点。
_TOC_DECOY_TITLES = ("（九）某未收录事项", "（十）另一未收录事项")


def _asset_toc_tail_pages() -> list:
    """接在 `_pages_asset_rows()` 之后的**目录页与补页**（`hq-4` 的出借方来源）。

    `hq-4` 起书签**不再**是来源证据（§二.2），因此"出借方"必须有一条复核方能重算的
    来源对象链。合成夹具里最省的形态就是一条**真实目录项**：目录页 + 页标签 furniture
    + 目标行文本一致 + `TocSource` 身份可重建 —— 这正是 `toc_body_landing` 的四段。

    页标签必须与物理页保持同一偏移：`_pages_asset_rows()` 已用掉 "1".."4"（物理页
    2..5），这里续 "5" / "6"（物理页 6 / 7）。页码 furniture 要求至少 5 个同偏移页，
    因此补一页正文凑够 6 个编号页。

    目录项声明 "2"：按众数偏移 −1，它唯一映射到物理页 3 —— 即资产表所在页，
    `四、主营业务分析` 就排在那页上。
    """
    toc_lines = [(72.0, 72.0, "目录", 16.0, False)]
    for index, (title, label) in enumerate(
            ((_ASSET_LENDER, "2"),
             (_TOC_DECOY_TITLES[0], "2"),
             (_TOC_DECOY_TITLES[1], "2"))):
        toc_lines.append((72.0, 104.0 + index * 22.0,
                          title + _TOC_LEADER + " " + label, 11.0, False))
    return [
        (_filler("T"), "5"),
        (toc_lines, "6"),
    ]


def _pages_asset_rows() -> list:
    """§三.4：固定资产表行 + 一条**同页、同层、同版式**的自证成立标题（「出借方」）。

    `四、主营业务分析` 在**同一页**上、同 `declared_level`（1）、同字号（11.0）、同行首
    （x0=72），并且自己有强主证据。它在旧实现下确实会给四条表行出借 `level_layout`
    辅证据 —— 断言因此更强：**连域内出借也无法**让表行取得资格，因为 `level_layout`
    不是主证据。

    本夹具**不带**任何来源佐证（既无目录项也无书签），因此 J1–J10 完全按"候选自身
    版式事实"判定；出借方的那条来源由 `_asset_toc_tail_pages()` 单独接上（J11–J13）。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B")
         + [(72.0, 300.0, "下表列示报告期内的相关构成。", 11.0, False)]
         + _grid(["一、账面原值", "二、累计折旧", "三、减值准备", "四、账面价值"])
         + [(72.0, 620.0, _ASSET_LENDER, 11.0, False)],
         "2"),
        (_filler("C"), "3"),
        (_filler("D"), "4"),
    ]


def _pages_label_band() -> list:
    """§三.4 补充 / 判据 E：固定资产附注表的**纯标签带**（实测形态，非合成想象）。

    真实 2025 年年度报告第 181 页的固定资产附注表把 `一、账面原值` / `二、累计折旧` /
    `三、减值准备` / `四、账面价值` 排成一条**左侧悬出**（x0 比表内行标签更靠左）的标签
    带：带的上方与下方都是同一张表的多字段行，带内只有行标签，没有任何值列内容（有的
    分期金额为零，值列不排任何东西）。单行几何（判据 A–D）一条都不命中：
      - 本行自己所在的行只有一个字段（不是多字段行）→ A 不命中；
      - 本行起点（62.3）不落在网格列锚点（72 / 300 / 480）的 3pt 容差内 → B 不命中；
      - 本行没有同列折行的伴侣（带内行距 20pt > 4pt 片段间距）→ C 不命中；
      - 下方最近的多字段行距本行 128pt（带内是行标签，不是值列）→ D 不命中。
    带内标签的编号层级刻意**不同**（2 / 4 / 5），因此不会因为"紧邻同层编号行"被
    `numbered_list_run_context` 挡下 —— 反例必须由判据 E 自己挡下，不能靠另一条规则。
    """
    band = [(62.3, 240.0, "一、账面原值", 11.0, False),
            (80.3, 260.0, "1.期初余额", 11.0, False),
            (80.3, 280.0, "（1）其中：新增", 11.0, False),
            (62.3, 300.0, "二、累计折旧", 11.0, False),
            (80.3, 320.0, "1.期初余额", 11.0, False),
            (80.3, 340.0, "（1）本期计提", 11.0, False),
            (62.3, 360.0, "三、减值准备", 11.0, False),
            (80.3, 380.0, "1.期初余额", 11.0, False),
            (62.3, 400.0, "四、账面价值", 11.0, False)]
    upper = [(72.0, 200.0, "项目", 11.0, False),
             (300.0, 200.0, "1,234.56", 11.0, False),
             (480.0, 200.0, "1,234.56", 11.0, False)]
    lower = [(72.0, 440.0, "项目", 11.0, False),
             (300.0, 440.0, "1,234.56", 11.0, False),
             (480.0, 440.0, "1,234.56", 11.0, False)]
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B") + upper + band + lower, "2"),
        (_filler("C"), "3"),
        (_filler("D"), "4"),
    ]


def _pages_same_text_two_places() -> list:
    """§三.5：同一段短标题文字既作**独立标题行**、又作**表格单元格**。

    第 3 页（印 "2"）的 `五、其他重要事项` 是独立标题行（字号抬升、自成一段）；第 4 页
    （印 "3"）的同一个字符串是网格第一列的行标签。前者应通过，后者不得通过 —— 而且
    后者不得因为"文本与标题完全相同"被反向当作证据。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_section_head(), "1"),
        ([(72.0, 72.0, "五、其他重要事项", 12.5, False)]
         + H._body(110.0, tag="B", long_line=True), "2"),
        (_filler("C") + _grid(["五、其他重要事项"]), "3"),
        (_filler("D"), "4"),
    ]


def _pages_real_subheadings() -> list:
    """§三.6：与正文**同字号**、位于正文栏左边界、无目录书签佐证的普通小标题。

    它们没有任何版式提升、也没有来源佐证，唯一支撑是**自身的完整独立标题行形态**
    （`standalone_line`）加上段落边界 —— 属于 §二.2 允许的"候选自身结构证据"。
    为了把误收清零而把它们整体降级是不允许的。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head(), "2"),
        # 小标题之间用**不带 tag 的整段正文**隔开：`H._body(tag=...)` 会把 tag 追到行尾，
        # 行尾就不再是句末标点，`paragraph_boundary` 的真实判据（上一行**真实行尾**是
        # 句末标点，或存在真实垂直间距）会因此失效。
        ([(72.0, 72.0, "四、主营业务分析", 11.0, False)]
         + H._body(104.0)
         + [(72.0, 74.0 + 3 * 22.0 + 24.0, "（一）资产结构分析", 11.0, False)]
         + H._body(320.0, tag="C", long_line=True), "3"),
        (_filler("D"), "4"),
        (_filler("E"), "5"),
        (_filler("F"), "6"),
    ]


# ---------------------------------------------------------------------------
# 构建与定位工具
# ---------------------------------------------------------------------------

def _build(pages: list, toc=None):
    """构建一次，返回 `(layout, result, candidates)`。"""
    with H._tmpdir() as d:
        pdf = H._make_pdf(d / "hq.pdf", pages, toc=toc)
        layout, ctx = H._ctx_of(pdf)
        result = OB.build_document_outline(ctx)
        cands = OB.scan_heading_candidates(ctx)
    return layout, result, cands


def _hits(layout, texts) -> dict:
    """把待查文本映射到真实 `(页, 行)`；同一文本取**第一个**出现的行。"""
    out: dict = {}
    for page in layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            out.setdefault(tight(line.text), (page.page_number, line.line_index))
    return {text: out[tight(text)] for text in texts if tight(text) in out}


def _titles(result) -> list:
    return [n.title for n in result.outline.nodes]


def _cand_at(cands, page: int, line: int):
    for cand in cands:
        if cand.page_number == page and cand.line_index == line:
            return cand
    return None


def _evidence_dict(record) -> dict:
    """把持久化审计记录的 `evidence` 元组解析成可断言的真值表字段。"""
    out: dict = {}
    for chunk in record.evidence:
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            out[key] = value
    return out


def _unassigned_at(result, page: int, line: int):
    for span in result.outline.unassigned:
        if span.start_anchor[:2] == (page, line):
            return span
    return None


def _grid_label_keys(layout, labels) -> dict:
    hits = _hits(layout, labels)
    missing = [text for text in labels if text not in hits]
    return hits, missing


# ---------------------------------------------------------------------------
# G. §三.1 全文件候选互证
# ---------------------------------------------------------------------------

def _test_far_apart_peer_only() -> None:
    labels = ("1、国家持", "2、国有法")
    layout, result, cands = _build(_pages_far_apart())
    hits, missing = _grid_label_keys(layout, labels)
    check(not missing, f"G1 两条相距很远的表格行都定位到真实版式行（缺 {missing}）")
    if missing:
        return
    titles = _titles(result)
    for text in labels:
        page, line = hits[text]
        cand = _cand_at(cands, page, line)
        check(cand is not None and not cand.rejected,
              f"G2 {text!r} 是**非硬拒绝**的编号候选（旧实现正是从这条路进来的）")
        if cand is None:
            continue
        check(cand.table_region_reason in OB.TABLE_REGION_REASONS,
              f"G3 {text!r} 被通用表格区域资格命中"
              f"（{cand.table_region_reason}）")
        check(cand.primary_evidence == (),
              f"G4 {text!r} 没有任何**自身**主证据（primary={cand.primary_evidence}）")
        check("level_layout" not in cand.primary_evidence,
              f"G5 {text!r} 的主证据里没有 `level_layout`（§二.1 取消全文件互证）")
        check("level_layout" not in cand.supporting_evidence,
              f"G6 {text!r} 的辅证据里也没有 `level_layout`"
              f"（域外出借方一律不参与，{cand.supporting_evidence}）")
        check(text not in titles, f"G7 {text!r} 没有进入正式标题树")
        check(_legacy_global_peer_accepts(cand, cands),
              f"G8 **旧实现会采纳** {text!r}（复刻的全文件互证规则命中）"
              f"—— 证明这条反例确实是新判据挡下的")
        span = _unassigned_at(result, page, line)
        check(span is not None
              and span.unassigned_reason in S.UNASSIGNED_REASONS,
              f"G9 {text!r} 进入正式 `formal_unassigned` 并带合法原因"
              f"（{None if span is None else span.unassigned_reason}）")


# ---------------------------------------------------------------------------
# H. §三.2 / §三.3 / §三.4 表内行标签
# ---------------------------------------------------------------------------

def _check_rows_are_not_nodes(fixture, labels, prefix: str,
                              expect_legacy_hits: int) -> None:
    """一组表格行标签：不得进入标题树；必须显式未归属；旧实现至少命中给定条数。"""
    layout, result, cands = _build(fixture)
    hits, missing = _grid_label_keys(layout, labels)
    check(not missing, f"{prefix}1 全部表内标签都定位到真实版式行（缺 {missing}）")
    if missing:
        return
    titles = _titles(result)
    legacy_hits = 0
    for text in labels:
        page, line = hits[text]
        cand = _cand_at(cands, page, line)
        check(text not in titles, f"{prefix}2 表内标签不成节点：{text!r}")
        check(cand is not None
              and cand.table_region_reason in OB.TABLE_REGION_REASONS,
              f"{prefix}3 表内标签被表格区域资格命中：{text!r}")
        if cand is None:
            continue
        check(cand.accepted is False,
              f"{prefix}4 表内标签未被采纳（accepted={cand.accepted}）：{text!r}")
        check(cand.primary_evidence == (),
              f"{prefix}5 表内标签自身主证据为空：{text!r}"
              f"（{cand.primary_evidence}）")
        if _legacy_global_peer_accepts(cand, cands):
            legacy_hits += 1
        span = _unassigned_at(result, page, line)
        check(span is not None
              and span.unassigned_reason == OB.UNASSIGNED_INSUFFICIENT_EVIDENCE,
              f"{prefix}6 表内标签 fail-closed 到 `insufficient_heading_evidence`"
              f"（未归属原因取「证据不足」而非「层级不明 / 目录书签未归属」）：{text!r}"
              f"（{None if span is None else span.unassigned_reason}）")
    check(legacy_hits == expect_legacy_hits,
          f"{prefix}7 旧实现会采纳其中 {legacy_hits} 条（期望 {expect_legacy_hits}）"
          f"—— 反例确实打在旧规则的采纳路径上")
    check(all(node.title not in labels for node in result.outline.nodes),
          f"{prefix}8 四条表行没有任何一条以任何形态进入节点")


def _test_dense_share_table_rows() -> None:
    _check_rows_are_not_nodes(_pages_dense_labels(),
                              ("1、国家持", "2、国有法", "3、其他内"),
                              "H", expect_legacy_hits=3)


def _test_equity_table_rows() -> None:
    # 旧实现只在**同一声明层级**之间互证：`一、上年期`（声明层级 1）与
    # `四、本期期`（1）会配成一对；`（一）综合`（2）没有同层同伴，因此旧规则也不采纳
    # 它。反例因此是 2 条 —— 「股东权益表的行标签会互相授予标题资格」这件事本身被证伪。
    _check_rows_are_not_nodes(_pages_equity_rows(),
                              ("一、上年期", "（一）综合", "四、本期期"),
                              "I", expect_legacy_hits=2)


def _test_fixed_asset_table_rows() -> None:
    labels = ("一、账面原值", "二、累计折旧", "三、减值准备", "四、账面价值")
    _check_rows_are_not_nodes(_pages_asset_rows(), labels, "J",
                              expect_legacy_hits=len(labels))
    layout, result, cands = _build(
        _pages_asset_rows() + _asset_toc_tail_pages())
    hits, missing = _grid_label_keys(layout, labels)
    if missing:
        return
    check(_ASSET_LENDER in _titles(result),
          f"J11 同页的同层同版式自证标题仍被采纳（出借方本身合法）："
          f"{_ASSET_LENDER!r}")
    lender = _cand_at(cands, *_hits(layout, (_ASSET_LENDER,))[_ASSET_LENDER])
    check(lender is not None
          and OB.TOC_BODY_LANDING_EVIDENCE in lender.primary_evidence
          and lender.strong_primary_evidence,
          f"J11b 出借方的强主证据来自**可重建的来源对象链**（`hq-4`：书签已退出资格）："
          f"primary={None if lender is None else lender.primary_evidence}")
    check(lender is not None
          and all(title not in _titles(result) for title in _TOC_DECOY_TITLES),
          f"J11c 正文里不存在的目录项**不**被编造出节点：{_TOC_DECOY_TITLES}")
    # §二.1 的关键断言：**域内**出借发生了，但资格仍然不成立 ——
    # `level_layout` 既不能单独成为主证据，也不能与一条普通辅证据拼出资格。
    lent = [text for text in labels
            if "level_layout" in
            (_cand_at(cands, *hits[text]).supporting_evidence
             if _cand_at(cands, *hits[text]) is not None else ())]
    check(len(lent) == len(labels),
          f"J12 四条表行都从同页自证标题处**域内**借到了 `level_layout` 辅证据"
          f"（借到 {len(lent)}/{len(labels)}）")
    check(all(text not in _titles(result) for text in labels),
          "J13 即使域内借到了 `level_layout`，四条表行仍不得成为节点"
          "（辅证据永远不能单独构成资格）")


# ---------------------------------------------------------------------------
# Q. §三.4 补充：附件表**纯标签带**（判据 E 的单行几何盲区）
# ---------------------------------------------------------------------------

def _test_label_band_rows() -> None:
    """固定资产附注表的纯标签带：单行几何（判据 A–D）对其一条都不命中。

    §三.4 点名的四条表行在真实 2025 年年度报告里正是这种形态：整条标签带夹在同一张表的
    两行多字段行之间，带内只有行标签、没有值列内容。A–D 的失败原因见 `_pages_label_band`
    的 docstring。断言分两层：通用层（`_check_rows_are_not_nodes`，与 H/I/J 同一口径）与
    判据层（**必须由 E 挡下**，不能是靠 A–D 或另一条软理由顺带挡下的）。
    """
    labels = ("一、账面原值", "二、累计折旧", "三、减值准备", "四、账面价值")
    _check_rows_are_not_nodes(_pages_label_band(), labels, "Q",
                              expect_legacy_hits=len(labels))
    layout, result, cands = _build(_pages_label_band())
    hits, missing = _grid_label_keys(layout, labels)
    if missing:
        return
    check(all((_cand_at(cands, *hits[text]) is not None
               and _cand_at(cands, *hits[text]).table_region_reason
               == OB.TABLE_REGION_LABEL_BAND_REASON) for text in labels),
          "Q9 四条表行是被**判据 E（两行同族多字段行之间的纯标签带）**挡下的，"
          "不是被 A–D 的单行几何顺带挡下的"
          f"（实得 {[None if _cand_at(cands, *hits[t]) is None else _cand_at(cands, *hits[t]).table_region_reason for t in labels]}）")
    # 带内**全部**行标签（含 4 / 5 级子标签）都属同一区域：判据 E 认的是"整条带"，
    # 真实材料里被误收的 `（1）计提` / `（1）处置` 就是这一档。
    page = next(page for page in layout.pages
                if page.page_number == hits[labels[0]][0])
    grid_rows = [line for line in page.lines
                 if not line.is_furniture and tight(line.text) == "项目"]
    check(len(grid_rows) == 2,
          f"Q10 标签带上下各有一行多字段行（实得 {len(grid_rows)}）")
    if len(grid_rows) != 2:
        return
    band_lines = [line for line in page.lines if not line.is_furniture
                  and line.bbox[1] > grid_rows[0].bbox[3]
                  and line.bbox[3] < grid_rows[1].bbox[1]]
    check(len(band_lines) == 9,
          f"Q10b 标签带在真实版式里是 9 行（实得 {len(band_lines)}："
          f"{[line.text for line in band_lines]}）")
    misses = []
    for line in band_lines:
        cand = _cand_at(cands, page.page_number, line.line_index)
        if cand is None \
                or cand.table_region_reason != OB.TABLE_REGION_LABEL_BAND_REASON:
            misses.append((line.text,
                           None if cand is None else cand.table_region_reason))
    check(not misses, f"Q11 带内 9 行**全部**由判据 E 命中（漏 {misses}）")
    check(all(line.text not in _titles(result) for line in band_lines),
          "Q12 带内 9 行没有任何一条进入正式标题树")


# ---------------------------------------------------------------------------
# K. §三.5 同一段文字的两处出现
# ---------------------------------------------------------------------------

def _test_same_text_heading_and_cell() -> None:
    text = "五、其他重要事项"
    layout, result, cands = _build(_pages_same_text_two_places())
    occurrences = [(page.page_number, line.line_index)
                   for page in layout.pages for line in page.lines
                   if not line.is_furniture and tight(line.text) == tight(text)]
    check(len(occurrences) == 2,
          f"K1 该短标题在真实版式里出现两次（标题行 + 表内单元格，实得 "
          f"{len(occurrences)}）")
    if len(occurrences) != 2:
        return
    heading_key, cell_key = occurrences[0], occurrences[1]
    nodes = [n for n in result.outline.nodes if n.title == text]
    check(len(nodes) == 1,
          f"K2 同名节点恰好一个（不是两个、也不是零个，实得 {len(nodes)}）")
    if len(nodes) != 1:
        return
    check(nodes[0].source_anchor[:2] == heading_key,
          f"K3 该节点锚在**独立标题行**上（{nodes[0].source_anchor[:2]} vs "
          f"{heading_key}）")
    head_cand = _cand_at(cands, *heading_key)
    cell_cand = _cand_at(cands, *cell_key)
    check(head_cand is not None and head_cand.accepted,
          "K4 独立标题行被采纳")
    check(head_cand is not None and "size_above_body" in head_cand.primary_evidence,
          f"K5 独立标题行凭**自身**字号抬升通过"
          f"（primary={None if head_cand is None else head_cand.primary_evidence}）")
    check(cell_cand is not None and not cell_cand.accepted,
          "K6 表内单元格未被采纳（同一段文字不构成反向证据）")
    check(cell_cand is not None
          and cell_cand.table_region_reason in OB.TABLE_REGION_REASONS,
          f"K7 表内单元格被表格区域资格命中"
          f"（{None if cell_cand is None else cell_cand.table_region_reason}）")
    check(_unassigned_at(result, *cell_key) is not None,
          "K8 表内单元格显式进入正式未归属（不得静默消失）")


# ---------------------------------------------------------------------------
# L. §三.6 合法正大小标题仍须保留
# ---------------------------------------------------------------------------

def _test_real_subheadings_are_kept() -> None:
    labels = ("四、主营业务分析", "（一）资产结构分析")
    layout, result, cands = _build(_pages_real_subheadings())
    hits, missing = _grid_label_keys(layout, labels)
    check(not missing, f"L1 合法小标题都定位到真实版式行（缺 {missing}）")
    if missing:
        return
    for text in labels:
        page, line = hits[text]
        cand = _cand_at(cands, page, line)
        check(text in _titles(result), f"L2 合法小标题仍被采纳：{text!r}")
        check(cand is not None and cand.accepted, f"L3 审计记录标记为 accepted：{text!r}")
        if cand is None:
            continue
        check(cand.primary_evidence == ("standalone_line",),
              f"L4 {text!r} 的通过依据是**自身的完整独立标题行形态**"
              f"（primary={cand.primary_evidence}）")
        check("level_layout" not in cand.primary_evidence
              and "level_layout" not in cand.supporting_evidence,
              f"L5 {text!r} 的资格里不含任何候选间版式相似（{cand.supporting_evidence}）")
        check(cand.table_region_reason is None,
              f"L6 {text!r} 不在表格 / 网格区域内（{cand.table_region_reason}）")


# ---------------------------------------------------------------------------
# M. §三.7 未归属显化
# ---------------------------------------------------------------------------

def _test_insufficient_evidence_stays_visible() -> None:
    labels = ("1、国家持", "2、国有法", "3、其他内")
    layout, result, cands = _build(_pages_dense_labels())
    hits, missing = _grid_label_keys(layout, labels)
    if missing:
        check(False, f"M1 夹具缺行：{missing}")
        return
    spans = [s for s in result.outline.unassigned
             if s.start_anchor[:2] in {hits[t] for t in labels}]
    check(len(spans) == len(labels),
          f"M1 全部证据不足的候选都进入正式未归属（{len(spans)}/{len(labels)}）")
    check(all(s.role == "unassigned" and s.node_id is None for s in spans),
          "M2 未归属 span 的 role / node_id 自洽（未归属不是节点）")
    check(all(s.unassigned_reason in S.UNASSIGNED_REASONS for s in spans),
          "M3 未归属原因取自公共封闭词表")
    check(all(s.start_anchor[:2] == s.end_anchor[:2] for s in spans),
          "M4 未归属 span 是**精确定位**的单行锚点，不是整页猜测")
    # §二.4：不得被直接称作「真标题漏收」 —— 这些候选没有独立人工 gold。
    # 表内标签走的软原因是 `table_region_candidate`，它映射到
    # `insufficient_heading_evidence`（证据不足），**不是** `numbered_list_ambiguity`
    # （编号列表歧义）。这个区分必须落到实处，否则"待人工复核候选"的计数会串味。
    findings = OB.structural_review_findings(result)
    check(findings["counts"]["numbered_list_ambiguity"] == 0,
          f"M5 表内标签**不**被记成 `numbered_list_ambiguity`（编号列表歧义），"
          f"而是走 `table_region_candidate` → `insufficient_heading_evidence`"
          f"（实得 {findings['counts']['numbered_list_ambiguity']}）")
    check(all(s.unassigned_reason == OB.UNASSIGNED_INSUFFICIENT_EVIDENCE
              for s in spans),
          "M5b 未归属原因确实是「证据不足」这一独立档位，与目录 / 书签未归属不同档")
    check(findings["counts"]["unsafe_table_override_accepted"] == 0
          and findings["counts"]["adjacent_heading_rejected"] == 0
          and findings["counts"]["fragmented_cell_accepted"] == 0
          and findings["counts"]["accepted_without_intrinsic_primary_evidence"] == 0
          and findings["counts"]["global_peer_only_accepted"] == 0,
          "M5c 表内标签没有成为节点，因此不构成任何「已采纳」类误收"
          "（独立误收类均为 0）")
    check(findings["layout_available"] is True and not findings["verification_gaps"],
          "M5d 本次复核拿到了 `PageLayout`，四类误收的 0 是真复核出来的"
          "（不是 fail-closed 缺数据导致的假 0）")
    scope = findings["scope"]
    check("待人工复核候选" in scope,
          "M6 复核门自述统一使用「待人工复核候选」这一措辞")
    check("不是" in scope.split("真标题漏收")[0][-12:],
          "M6b 自述里唯一出现「真标题漏收」的地方是**显式否定**，"
          "没有把它当结论用来描述无 gold 的候选")


# ---------------------------------------------------------------------------
# N. §三.8 无回归口径
# ---------------------------------------------------------------------------

def _test_no_regression_invariants() -> None:
    # 证据词表本身：`level_layout` 不是主证据；采纳门槛仍是主 ≥1 且 主+辅 ≥2。
    check("level_layout" not in OB.HEADING_PRIMARY_EVIDENCE,
          "N1 `level_layout` 不在主证据词表里（§二.1 的可执行形式）")
    check("level_layout" in OB.HEADING_SUPPORTING_EVIDENCE,
          "N2 `level_layout` 只作辅证据")
    check(OB.MIN_HEADING_EVIDENCE == 2,
          f"N3 采纳门槛未放宽（主+辅 ≥ {OB.MIN_HEADING_EVIDENCE}）")
    check(set(OB.HEADING_STRONG_PRIMARY_EVIDENCE)
          <= set(OB.HEADING_PRIMARY_EVIDENCE),
          "N4 强主证据是主证据的子集")
    check("standalone_line" not in OB.HEADING_STRONG_PRIMARY_EVIDENCE,
          "N5 `standalone_line` 仍是弱主证据（表内候选拿不到强主证据）")
    check(OB.NEARBY_PAGE_WINDOW_PAGES == 1,
          f"N6 `level_layout` 的出借窗口诚实命名为「同页或相邻页」的**辅助邻近窗口**"
          f"（{OB.NEARBY_PAGE_WINDOW_PAGES}）")
    check(not hasattr(OB, "LOCAL_DOMAIN_PAGE_SPAN"),
          "N6b 旧名称 `LOCAL_DOMAIN_PAGE_SPAN`（自称已验证的「局部结构域」）已删除："
          "±1 页窗口只能证明「同页或相邻页」，不构成任何已验证结构域")

    # 确定性 + 四态守恒 + siblings 不变量（换文档身份不改变结论）。
    pages = _pages_equity_rows()
    layout, result, cands = _build(pages)
    layout2, result2, _ = _build(pages)
    check([n.title for n in result.outline.nodes]
          == [n.title for n in result2.outline.nodes],
          "N7 同一输入连续两次构建的节点序列一致（确定性）")
    diagnostic = OB.line_structure_diagnostic(result, layout)
    counts = diagnostic["counts"]
    non_furniture = sum(1 for page in layout.pages for line in page.lines
                        if not line.is_furniture)
    check(diagnostic["four_state_sum"] == non_furniture
          and diagnostic["total_lines"] == non_furniture
          and diagnostic["conserved"],
          f"N8 四态守恒：四态之和 {diagnostic['four_state_sum']} = 逐行记录 "
          f"{diagnostic['total_lines']} = 非家具行 {non_furniture}")
    check(set(counts) == set(OB.LINE_STRUCTURE_STATES),
          f"N9 四态键恰为封闭词表 {sorted(OB.LINE_STRUCTURE_STATES)}"
          f"（实际 {sorted(counts)}）")
    check(diagnostic["heading_node_without_node_id"] == 0
          and diagnostic["formal_unassigned_without_span_id"] == 0,
          "N9b 两处「孤儿」计数为 0：heading_node 必带 node_id、"
          "formal_unassigned 必带 span_id")
    by_id = {n.node_id: n for n in result.outline.nodes}
    bad_nesting = []
    for node in result.outline.nodes:
        if node.parent_id is None:
            continue
        parent = by_id.get(node.parent_id)
        child_cand = _cand_at(cands, *node.source_anchor[:2])
        parent_cand = (None if parent is None
                       else _cand_at(cands, *parent.source_anchor[:2]))
        if child_cand is None or parent_cand is None:
            continue
        if child_cand.declared_level is not None \
                and child_cand.declared_level == parent_cand.declared_level:
            bad_nesting.append((parent.title, node.title))
    check(not bad_nesting,
          f"N10 同一编号体系内声明层级相同的标题没有被挂成父子（{bad_nesting}）")

    # 对齐分区不变量（TS2 / P1-E）：合法载荷通过，空洞与交叠一律 fail-closed。
    good = dict(block_char_length=100,
                char_map=((0, 30, 1, 0, 0, 0), (30, 60, 1, 1, 0, 0)),
                residue=((60, 100, "unexplained"),), matched_chars=60)
    check(S.validate_alignment_partition(**good) == 60,
          "N11 对齐分区校验器对合法载荷返回**重算**出的匹配字符数")
    raises(lambda: S.validate_alignment_partition(
        **{**good, "char_map": ((0, 30, 1, 0, 0, 0),), "residue": ((70, 100, "unexplained"),)}),
        SchemaValidationError, "空洞",
        "N12 分区有空洞仍 fail-closed（未降低校验强度）")
    raises(lambda: S.validate_alignment_partition(
        **{**good, "residue": ((50, 100, "unexplained"),)}),
        SchemaValidationError, "重叠",
        "N13 char_map 与 residue 交叠仍 fail-closed")
    raises(lambda: S.validate_alignment_partition(
        **{**good, "matched_chars": 99}),
        SchemaValidationError, "必须精确等于",
        "N14 `matched_chars` 自报虚高仍 fail-closed")

    # TS2 冻结阈值：精确真值表在 ALIGN_MIN 边界上不得漂移。
    align_min = V.ALIGN_MIN
    check(align_min == 0.90, f"N15 冻结的 `ALIGN_MIN` 仍是 0.90（{align_min}）")
    check(S.compute_alignment_verdict_exact(90, 100, None, align_min) == "aligned",
          "N16 恰好等于阈值判为 aligned（精确有理数比较）")
    # 精确有理数比较：0.89999 量化到记录位后**看似**达到 0.90，但按冻结阈值它不可引用。
    # 这正是 `als-2` 携带精确分子 / 分母要消除的那段分歧。
    check(S.compute_alignment_verdict_exact(89999, 100000, None, align_min)
          == "unaligned",
          "N17 阈值下方（量化后看似达标的 0.89999）判为 unaligned"
          "（先量化再比较的旧分歧不再出现）")
    check(S.compute_alignment_verdict_exact(90, 100, "unexplained", align_min)
          == "partially_aligned",
          "N18 恰好达阈值但残余未解释时判为 partially_aligned（残余分类不被绕过）")
    check(S.compute_alignment_verdict_exact(0, 100, None, align_min) == "unaligned"
          and S.compute_alignment_verdict_exact(0, 0, None, align_min) == "unaligned",
          "N19 空匹配 / 空块一律 unaligned（零分母不得被判成达标）")
    raises(lambda: S.compute_alignment_verdict_exact(100, 0, None, align_min),
           SchemaValidationError, "不得大于块长度",
           "N20 分子大于分母一律 fail-closed（不得靠异常路径静默降级）")


# ---------------------------------------------------------------------------
# O. §三.9 独立验收门必须能抓到手工构造的表格片段节点
# ---------------------------------------------------------------------------

def _stub_result(*, nodes, candidates, unassigned=(), layout=None):
    outline = types.SimpleNamespace(nodes=tuple(nodes), unassigned=tuple(unassigned))
    return types.SimpleNamespace(outline=outline, candidates=tuple(candidates),
                                 layout=layout)


def _stub_node(node_id, title, page, line):
    # `structural_path` 是**真实** `OutlineNode` 的字段（正式 schema 必填）。桩对象必须
    # 与它同形，否则调用方读不到路径时会以为是"没有路径"，而不是"桩不完整"。
    return types.SimpleNamespace(
        node_id=node_id, title=title, parent_id=None,
        structural_path=(title,),
        source_anchor=(page, line, (72.0, 100.0, 400.0, 112.0)))


_DEFAULT_SCOPE = "<default-scope>"


def _stub_cand(page, line, text, *, primary, supporting=(), accepted=True,
               node_id="n1", declared=0, table_scope=_DEFAULT_SCOPE,
               bookmarks=0, soft=(), landing="", accepted_by=None):
    """手工构造的候选审计记录（含**持久化的** `table_scope`）。

    `table_scope=None` 表示记录**没有**持久化这一字段（用于 fail-closed 反例）。
    `landing` 是 §二/§三 新增的 `source_landing` 载荷（空串 = 记录里**没有**这个字段，
    即生产侧没有自报任何可复核的来源 landing）。
    `accepted_by` 是 `hq-4` 新增的**持久化**放行依据字段（`toc_landing` / `numbering` /
    `style` / `-`）：没有它，"这条节点是不是靠来源 landing 放行的"只能靠猜。
    """
    if table_scope is _DEFAULT_SCOPE:
        table_scope = getattr(OB, "TABLE_SCOPE_NONE", "none")
    evidence = ["toc_matches=0", f"bookmark_matches={bookmarks}",
                "primary_evidence=" + (",".join(primary) if primary else "-"),
                "supporting_evidence=" + (",".join(supporting) if supporting else "-")]
    if accepted_by is not None:
        evidence.append(f"accepted_by={accepted_by}")
    if table_scope is not None:
        evidence.append(f"table_scope={table_scope}")
    if soft:
        evidence.append("soft_reasons=" + ",".join(soft))
    if landing:
        evidence.append(f"{OB.SOURCE_LANDING_FIELD}={landing}")
    return types.SimpleNamespace(
        kind="heading", page_number=page, line_index=line, text=text,
        accepted=accepted, node_id=node_id, declared_level=declared,
        applied_level=declared, evidence=tuple(evidence), reason_codes=())


def _test_acceptor_finds_handmade_table_fragment() -> None:
    """§三.9：一条**手工构造**的、生产侧谎称有 `standalone_line` 的表内标签节点。

    独立验收门必须**自己**从持久化版式重算区域事实，因此它抓到的是几何，不是形态
    词表 —— 这正是它"不再只查逗号子句与定义句"的可执行证明。
    """
    layout, _, _ = _build(_pages_dense_labels())
    page, line = _hits(layout, ("2、国有法",))["2、国有法"]
    findings = OB.structural_review_findings(_stub_result(
        nodes=[_stub_node("n1", "2、国有法", page, line)],
        candidates=[_stub_cand(page, line, "2、国有法",
                               primary=("standalone_line",),
                               supporting=("paragraph_boundary",),
                               table_scope=OB.TABLE_SCOPE_INSIDE)],
        layout=layout))
    check(findings["counts"]["unsafe_table_override_accepted"] == 1
          and findings["inside_table_accepted"] == 1,
          f"O1 手工构造的表内标签节点被独立验收门识别为**不安全穿透**"
          f"（unsafe={findings['counts']['unsafe_table_override_accepted']} / "
          f"inside={findings['inside_table_accepted']}）")
    check(findings["counts"]["accepted_without_intrinsic_primary_evidence"] == 0,
          "O2 该节点的 primary_evidence 里**确实**有 `standalone_line`："
          "表内误收不是靠「没有主证据」抓到的，而是靠独立重算的几何事实")
    check(findings["clean"] is False,
          "O3 存在表格区域误收时 clean 必须为 False")
    entry = findings["unsafe_table_override_accepted"][0]
    check(entry["geometry"]["own_row_multi"] is True
          or entry["geometry"]["baseline_row"] is True
          or entry["geometry"]["band_bracketed"] is True
          or entry["geometry"]["row_multi_anchor"] is True,
          f"O4 判定来自独立重算的几何事实（{entry['geometry']}）")
    check(entry["verified_strong"] == [],
          "O5 生产侧声称的弱主证据 `standalone_line` 不算强主证据（不得自证清白）")

    # 对照：把同一个文本放到**独立标题行**上，验收门不得再报误收。
    layout2, _, _ = _build(_pages_same_text_two_places())
    page2, line2 = _hits(layout2, ("五、其他重要事项",))["五、其他重要事项"]
    clean = OB.structural_review_findings(_stub_result(
        nodes=[_stub_node("n2", "五、其他重要事项", page2, line2)],
        candidates=[_stub_cand(page2, line2, "五、其他重要事项",
                               primary=("size_above_body",), node_id="n2")],
        layout=layout2))
    check(clean["counts"]["unsafe_table_override_accepted"] == 0
          and clean["counts"]["fragmented_cell_accepted"] == 0
          and clean["inside_table_accepted"] == 0,
          "O6 对照：同样的文字作为独立标题行时不被判为表格区域误收")

    # 截断单元格片段：与**紧随其后的同列行**构成垂直片段，两行右端都止于同页网格
    # 第二列起点之前（列宽截断），且两行都落在该网格的行距范围内。行距 15pt 是几何
    # 事实推导出来的：行高 13.2pt，因此 `0 ≤ following.top − line.bottom ≤ 4.0` 要求
    # 基线差落在 [13.2, 17.2] 内。
    fragment_pages = [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_filler("B")
         + [(72.0, 400.0, "1、货币资金", 11.0, False),
            (72.0, 415.0, "其中：库存现金", 11.0, False)]
         + _grid(["1、货币资金"], y0=440.0), "2"),
        (_filler("C"), "3"),
    ]
    layout3, result3, cands3 = _build(fragment_pages)
    hits3 = _hits(layout3, ("1、货币资金", "其中：库存现金"))
    if "1、货币资金" in hits3:
        page3, line3 = hits3["1、货币资金"]
        frag = OB.structural_review_findings(_stub_result(
            nodes=[_stub_node("n3", "1、货币资金", page3, line3)],
            candidates=[_stub_cand(page3, line3, "1、货币资金",
                                   primary=("standalone_line",), node_id="n3",
                                   table_scope=OB.TABLE_SCOPE_INSIDE)],
            layout=layout3))
        check(frag["counts"]["fragmented_cell_accepted"] == 1,
              f"O7 被列宽截断的单元格片段被独立验收门识别"
              f"（{frag['counts']['fragmented_cell_accepted']}）")
        check(OB._independent_fragment_fact(layout3, page3, line3) is True,
              "O8 该片段事实由独立重算得到（不读生产侧结论）")
    else:
        check(False, "O7 片段夹具未能定位目标行")


# ---------------------------------------------------------------------------
# W6. §三.4 表内采纳节点一律阻断；不得相信生产侧自报的 scope / 安全结论
# ---------------------------------------------------------------------------

def _test_acceptor_blocks_inside_table_overrides() -> None:
    """§三.4：表格区域内的采纳节点一律进入**阻断**复核，且不得相信生产侧自报。"""
    layout, _, _ = _build(_pages_valueless_row_label())
    page, line = _hits(layout, ("（1）本期减少",))["（1）本期减少"]
    node = _stub_node("n1", "（1）本期减少", page, line)

    # （1）伪造的"安全穿透"：生产侧声称表内可采纳（只有 `centered` 一项强证据），
    # 独立验收方必须自己重算强证据，把"两项版式巧合"与"来源佐证"区分开。
    forged = OB.structural_review_findings(_stub_result(
        nodes=[node],
        candidates=[_stub_cand(page, line, "（1）本期减少",
                               primary=("centered",),
                               supporting=("column_indent",),
                               table_scope=OB.TABLE_SCOPE_INSIDE)],
        layout=layout))
    check(forged["counts"]["unsafe_table_override_accepted"] == 1,
          f"W7.1 只有一项强证据的表内采纳节点被判为**不安全穿透**"
          f"（{forged['counts']['unsafe_table_override_accepted']}）")
    check(forged["inside_table_accepted"] == 1,
          f"W7.2 表内采纳节点计数（{forged['inside_table_accepted']}）")
    check(forged["safe_table_override_accepted"] == [],
          "W7.3 伪造的安全穿透不被接受（复核门不读生产侧自报结论）")
    check(forged["counts"]["adjacent_heading_rejected"] == 0,
          "W7.4 该节点不是「邻近被拒」类：两类计数不得互相串味")
    check(forged["clean"] is False,
          "W7.5 存在不安全穿透时 clean 必须为 False（§三.4 的阻断语义）")

    # （2）持久化候选信息里**没有** `table_scope`：无法区分 inside / adjacent → fail-closed。
    no_scope = OB.structural_review_findings(_stub_result(
        nodes=[node],
        candidates=[_stub_cand(page, line, "（1）本期减少",
                               primary=("standalone_line",), table_scope=None)],
        layout=layout))
    kinds = [gap["kind"] for gap in no_scope["verification_gaps"]]
    check("table_scope_unavailable" in kinds,
          f"W7.6 缺 `table_scope` 时显式登记缺口（{kinds}）")
    check(no_scope["clean"] is False,
          "W7.7 无法区分 inside / adjacent 时 clean 必须为 False")

    # （3）持久化 scope 与独立重算矛盾：生产侧谎称「只是邻近」。
    mismatch = OB.structural_review_findings(_stub_result(
        nodes=[node],
        candidates=[_stub_cand(page, line, "（1）本期减少",
                               primary=("standalone_line",),
                               table_scope=OB.TABLE_SCOPE_ADJACENT)],
        layout=layout))
    kinds = [gap["kind"] for gap in mismatch["verification_gaps"]]
    check("table_scope_mismatch" in kinds,
          f"W7.8 自报 scope 与独立重算矛盾时显式登记缺口（{kinds}）")
    check(mismatch["clean"] is False,
          "W7.9 scope 矛盾时 clean 必须为 False")

    # （4）真·邻近标题：独立重算为 adjacent，且没有被错误地追加表格区域软理由。
    adj_layout, adj_result, adj_cands = _build(_pages_heading_after_table())
    adj_page, adj_line = _hits(adj_layout, ("（二）某业务小节标题",))["（二）某业务小节标题"]
    adj_cand = _cand_at(adj_cands, adj_page, adj_line)
    ok = OB.structural_review_findings(adj_result, adj_layout)
    check(ok["counts"]["adjacent_heading_rejected"] == 0,
          f"W7.10 邻近表格的正式标题不被计成「邻近被拒」"
          f"（{ok['counts']['adjacent_heading_rejected']}）")
    check(adj_cand is not None
          and adj_cand.table_scope == OB.TABLE_SCOPE_ADJACENT,
          "W7.11 该标题独立重算后仍是 `adjacent_to_table`")
    check(ok["inside_table_accepted"] == len(ok["safe_table_override_accepted"]),
          "W7.12 不变式：表内采纳节点数 == 逐条安全穿透清单长度"
          f"（{ok['inside_table_accepted']} vs "
          f"{len(ok['safe_table_override_accepted'])}）")


# ---------------------------------------------------------------------------
# P. §三.10 独立验收门的 fail-closed
# ---------------------------------------------------------------------------

def _test_acceptor_fails_closed() -> None:
    layout, _, _ = _build(_pages_dense_labels())
    page, line = _hits(layout, ("3、其他内",))["3、其他内"]
    cand = _stub_cand(page, line, "3、其他内", primary=("standalone_line",))

    # （1）缺 `PageLayout`：区域 / 片段复核无法进行 → 不得返回 clean。
    no_layout = OB.structural_review_findings(_stub_result(
        nodes=[_stub_node("n1", "3、其他内", page, line)],
        candidates=[cand], layout=None))
    check(no_layout["layout_available"] is False,
          "P1 缺 `PageLayout` 时复核门自报 `layout_available=False`")
    check(no_layout["clean"] is False,
          "P2 缺 `PageLayout` 时 clean 必须为 False（fail-closed）")
    kinds = [gap["kind"] for gap in no_layout["verification_gaps"]]
    check("page_layout_unavailable" in kinds
          and "region_verification_skipped" in kinds,
          f"P3 缺口被显式登记（{kinds}）")
    check(no_layout["counts"]["unsafe_table_override_accepted"] == 0,
          "P4 复核**未执行**时不得假装「没有误收」：缺口本身即失败（不是静默通过）")

    # （2）缺候选审计：无法复核任何标题资格来源。
    no_audit = OB.structural_review_findings(_stub_result(
        nodes=[_stub_node("n1", "3、其他内", page, line)],
        candidates=[], layout=layout))
    check(no_audit["clean"] is False
          and any(gap["kind"] == "candidate_audit_unavailable"
                  for gap in no_audit["verification_gaps"]),
          "P5 候选审计为空时同样 fail-closed（不得报 clean）")

    # （3）二者都齐备时才可能 clean —— 用一份真正干净的替身验证门不是永假。
    ok = OB.structural_review_findings(_stub_result(
        nodes=[], candidates=[_stub_cand(page, line, "x", primary=("centered",),
                                         accepted=False, node_id=None)],
        layout=layout))
    check(ok["clean"] is True and not ok["verification_gaps"],
          f"P6 数据齐备且无任何复核项时 clean=True（门不是恒假，"
          f"gaps={ok['verification_gaps']}）")


# ---------------------------------------------------------------------------
# W. §四 表格边界资格返修（`trg-2` 两层状态 + `hq-2` 收紧穿透）
# ---------------------------------------------------------------------------

#: 表格单元格字号：与正文（11.0）**不同族**。真实材料上被误收的承诺正文正是这种形态
#: （正文 10.56 / 单元格 9.00）。两条承诺正文的**行首**落在该表第二列的列锚点上，
#: 长行因此恰好落在页宽中心附近 —— 旧实现凭 `centered` 单项强主证据无条件穿透。
_W_CELL = 9.0
#: 「字号略高于正文」的单元格字号（正文 11.0）。旧实现凭 `size_above_body` 单项穿透。
_W_LIFT = 11.5
#: 宽表第二列行首：长行（约 22 个全角字符）中心落在页宽中心 ±12pt 内。
_W_X0 = 190.0
_W_THIRD = 460.0
#: 承诺正文（**被列宽截断的上半行**形态：无句末标点，行尾停在半句）。
_W_PROMISE = "1、本公司及本公司控制的除某上市公司及其控股子"
#: 紧随其后的同列续写行（含句末标点，因此自身不是候选）。
_W_PROMISE_TAIL = "公司以停止经营相竞争业务的方式避免同业竞争。"
#: 表格单元格里「字号略高于正文」的编号行。
_W_LIFTED_CELL = "1、详见附注说明"

_W_LIFT_LENDER = "2、详见后文说明"


def _wide_row(y0: float, *, size: float = _W_CELL, first: str = "某股东",
              second: str = "长期有效", third: str = "详见附注。") -> list:
    """宽表的一条**多字段行**：左列标签 + 第二列（页宽中心附近）+ 第三列说明。

    三列都排在同一个字号上（真实表格的单元格字号），因此它是候选行首的**列锚点**来源。
    """
    return [(72.0, y0, first, size, False),
            (_W_X0, y0, second, size, False),
            (_W_THIRD, y0, third, size, False)]


def _legacy_strong_bypass_accepts(candidate) -> bool:
    """旧实现（`trg-1` + `hq-1`）：落在表格区域内的候选只要带**任一**强主证据，就不再
    追加 `table_region_candidate` 软理由，于是照常按"主证据 ≥1 且 主+辅 ≥2"被采纳。

    这里按同一份**候选自身**证据事实复刻那次判定（不读其它候选、不读任何生产侧结论）：
    把 `table_region_candidate` 这条软理由去掉后，证据算术是否成立。
    """
    primary = list(candidate.primary_evidence)
    supporting = [code for code in candidate.supporting_evidence
                  if code not in ("level_layout", "heading_context")]
    strong = [code for code in primary
              if code in ("size_above_body", "centered", "bold_majority")]
    if candidate.toc_matches or candidate.bookmark_matches:
        strong.append(_LEGACY_SOURCE_EVIDENCE)
    return bool(strong) and len(primary) >= 1 \
        and len(primary) + len(supporting) >= 2


def _pages_wide_cell_centered() -> list:
    """§四.1 / §四.3：宽表中央单元格里的编号承诺正文（**页宽居中**）+ 同列截断续写。

    第 4 页（印 "3"）是一张宽表：左列标签（72）、第二列（190）、第三列说明（460）都是
    9.0pt 单元格。表格中段是一条**值列只有承诺正文**的长单元格：正文行排在第二列行首
    （190），无句末标点（因此不是硬拒绝行），且因为行长恰好跨过页宽中心而被判
    `centered`。上一行是 200 的多字段行、下一行是 320 的多字段行 —— 该正文行离最近的
    列锚点多字段行 18.8pt（> `TABLE_REGION_PROXIMITY_PT`、≤ `TABLE_REGION_EXTENDED_PT`）。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B"), "2"),
        (_wide_row(200.0, first="承诺方", second="承诺内容", third="承诺期限。")
         + [(_W_X0, 290.0, _W_PROMISE, _W_CELL, False),
            (_W_X0, 302.0, _W_PROMISE_TAIL, _W_CELL, False)]
         + _wide_row(320.0), "3"),
        (_filler("D"), "4"),
    ]


def _pages_lifted_cell() -> list:
    """§四.2 / §四.4：单元格字号**略高于正文**的编号行 + 同层同版式的「出借方」。

    第 3 页（印 "2"）有一条同 `declared_level`、同字号、同行首、且**自身带强主证据**的
    普通候选（`2、详见后文说明`，11.5pt 排在 190），它会给第 4 页的目标行出借
    `level_layout` 辅证据；目标行是 11.5pt 的表格单元格（正文 11.0），因此拿到
    `size_above_body` + `column_indent` + `level_layout` 三条证据。

    第 2 页带 `_section_head()` 祖先链：目标行是 `1、`（声明层级 3），需要栈深 ≥ 3 才能
    让它的正式未归属原因落在「证据不足」而不是「跳级」上 —— 否则这条反例证明的就不再
    是"表格成员资格挡下了它"。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_section_head() + _filler("A"), "1"),
        ([(_W_X0, 60.0, _W_LIFT_LENDER, _W_LIFT, False)] + _filler("B"), "2"),
        (_wide_row(240.0, size=_W_LIFT, first="某项目", second="本期金额")
         + [(_W_X0, 290.0, _W_LIFTED_CELL, _W_LIFT, False)]
         + _wide_row(320.0, size=_W_LIFT), "3"),
        (_filler("D"), "4"),
    ]


def _pages_heading_after_table() -> list:
    """§四.5 / §四.7 / §四.8：表格末行之后紧跟正式标题，再跟正文与同级标题，再跟新表。

    第 4 页（印 "3"）的结构刻意与任务书 §三.3 的名录完全同形：

        [上一张表的最后一行]（9.0pt 单元格，行尾 `详见附注。`）
        （二）某业务小节标题              ← 与上一行垂直间距 0.8pt（远小于 8pt）
        该小节的正文说明……
        （三）下一同级小节标题
        [紧接着出现的新表：多字段行 + 一条 9.0pt 的编号行标签]

    标题与表格单元格的字号**不同族**（11.0 vs 9.0）：这是"仅因贴着表格就拒绝标题"与
    "表格正文误收"之间的分界线。旧实现（判据 B 只看行首与距离、不看字号）会把两条标题
    一并判成表内候选。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B"), "2"),
        (_wide_row(200.0, first="承诺方", second="承诺内容", third="承诺期限。")
         + _wide_row(212.0)
         + [(72.0, 226.0, "（二）某业务小节标题", 11.0, False),
            (72.0, 250.0, "本小节说明报告期内该项业务的履行情况。", 11.0, False),
            (72.0, 272.0, "报告期内未发生重大变化。", 11.0, False),
            (72.0, 300.0, "（三）下一同级小节标题", 11.0, False)]
         + _wide_row(316.0)
         + [(72.0, 346.0, "1、某明细项目", _W_CELL, False)], "3"),
        (_filler("D"), "4"),
    ]


def _pages_valueless_row_label() -> list:
    """§四.10：值列为空、离列锚点多字段行 12–15pt 的表格行标签（`（1）处置` 的同形）。

    第 4 页（印 "3"）的 `（1）本期减少` 行是**只有行标签**的表格行：值列本期为零，
    版式引擎不排任何金额。它离上一多字段行 14.8pt、离下一多字段行 12.6pt ——
    两条距离都大于 `TABLE_REGION_PROXIMITY_PT`(8.0)、小于 `TABLE_REGION_EXTENDED_PT`(30.0)。
    旧实现的判据 B / D 因此一条都不命中，它会凭"独立标题行形态"进入正式标题树。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B"), "2"),
        ([(72.0, 150.0, "1、某明细项目", 13.0, False)]
         + _wide_row(200.0, first="承诺方", second="承诺内容", third="承诺期限。")
         + [(72.0, 226.0, "（1）本期减少", _W_CELL, False)]
         + _wide_row(250.0), "3"),
        (_filler("D"), "4"),
    ]


#: §四.11 的「穿透依据」目标：**书签**命中的标题，几何上紧贴 / 落在表格区域内。
#:
#: `hq-4` 起书签**不再**是穿透依据（§二.2）。这条夹具因此刻意做成**最强形态**：书签
#: 声明的物理页（4）**就是**标题真实所在页，标题也**逐字相同** —— 两条可复核条件全部
#: 成立。即便如此，书签仍是"PDF 大纲里的一条自报记录"，不在 `PageLayout` 里，复核方
#: 无权重建它的身份，因此它只作导航候选（`bookmark_navigation_only`），不穿透表格。
#: `hq-3` 下这两条条件成立就足以放行 —— 那正是本轮取消的弱依据。
_SAFE_TOC = [[1, "（五）某重要事项", 4]]


def _pages_toc_backed_inside_table() -> list:
    """§四.11 / §二.2：**书签命中**、且几何上落在表格区域内的标题。

    `hq-4` 起它**不得**穿透 `inside_table`。书签与目录项的区别是决定性的：目录项在真实
    版式上有载体（真实行 + 真实字符区间 + 页标签 furniture），复核方能逐字段重建身份；
    书签只有"声明的页码 + 标题"两条自报事实，无法证明"这一行就是那个书签的目标"。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B"), "2"),
        (_wide_row(200.0, first="承诺方", second="承诺内容", third="承诺期限。")
         + [(_W_X0, 226.0, "（五）某重要事项", _W_CELL, False)]
         + _wide_row(250.0), "3"),
        (_filler("D"), "4"),
    ]


#: §四 返修追加的第二个漏收反例：**两张不同的表**之间的小节标题。
_W_BAND_HEADING = "（二）某业务小节标题"


def _pages_band_across_two_tables() -> list:
    """判据 E 的「**同一表格对象的**结构行闭合」反例（实测 2024/2025 年报的形态）。

    真实年报里同一页上常常排着**两张**列锚点完全相同的表（比如上下两段都是
    `项目 / 本期金额` 两列），中间夹着正常的**正文小节标题**。判据 E 原本只要求
    "上下两条多字段行同族"，于是**相距几百点、根本不是同一张表**的两行也会把中间
    整页圈成"标签带"，带内的真实小标题被判成表格成员 —— 表格成员要过 §三.2 的
    安全穿透资格，普通小标题过不了，于是一批**无歧义**的小节标题被误拒。

    第 4 页（印 "3"）的版式：

        y=200  项目 | 本期金额              ← 上一张表的结构行（列锚点 72 / 300）
        y=250  期初余额                       ┐
        y=270  本期增加                       │ 带内只有行标签，没有值列内容
        y=290  期末余额                       ┘
        y=330  其他项目 | 1,234.56            ← **另一张表**的结构行（列锚点 72 / 150）
        y=380  本期减少                       ┐
        y=400  累计折旧                       │ 带内只有行标签
        y=420  账面价值                       ┘
        y=460  （二）某业务小节标题           ← 目标：正文栏左边界、正文同字号（11.0）
        y=520  项目 | 本期金额                ← 下面又一张表的结构行（列锚点 72 / 300）

    两条**同族**行（y=200 与 y=520，列锚点逐列相同）之间夹着 y=330 那条多字段行，
    因此它们不是同一张表的结构行闭合：中间的正文（含本页的小节标题）不属于任何
    标签带。判据 A–D 对本行一条都不命中（本行独占一行、字号 11.0 与三张表的单元格
    9.0 不同族、上下最近的多字段行距离都 > `TABLE_REGION_COLUMN_ANCHOR_REACH_PT`、
    没有同列折行伴侣），所以它能不能进入正式树**只**由判据 E 的闭合条件决定。
    """
    upper = [(72.0, 200.0, "项目", _W_CELL, False),
             (300.0, 200.0, "本期金额", _W_CELL, False)]
    upper_band = [(72.0, 250.0, "期初余额", _W_CELL, False),
                  (72.0, 270.0, "本期增加", _W_CELL, False),
                  (72.0, 290.0, "期末余额", _W_CELL, False)]
    interior = [(72.0, 330.0, "其他项目", _W_CELL, False),
                (150.0, 330.0, "1,234.56", _W_CELL, False)]
    lower_band = [(72.0, 380.0, "本期减少", _W_CELL, False),
                  (72.0, 400.0, "累计折旧", _W_CELL, False),
                  (72.0, 420.0, "账面价值", _W_CELL, False)]
    lower = [(72.0, 520.0, "项目", _W_CELL, False),
             (300.0, 520.0, "本期金额", _W_CELL, False)]
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_filler("A"), "1"),
        (_section_head() + _filler("B"), "2"),
        (upper + upper_band + interior + lower_band
         + [(72.0, 460.0, _W_BAND_HEADING, 11.0, False)]
         + lower, "3"),
        (_filler("D"), "4"),
    ]


def _scene_of(pages: list, text: str):
    """构建一次夹具，返回 `(cand, result, titles, layout)`；缺行时返回 `(None, ...)`。"""
    layout, result, cands = _build(pages)
    hits = _hits(layout, (text,))
    if text not in hits:
        return None, result, _titles(result), layout
    page, line = hits[text]
    return _cand_at(cands, page, line), result, _titles(result), layout


def _layout_strong_form(candidate) -> bool:
    """反例是否带**单项版式强主证据**（居中 / 字号略高）。"""
    return bool(candidate.centered
                or "size_above_body" in candidate.primary_evidence)


def _standalone_withheld_form(candidate) -> bool:
    """反例是否落在"旧实现会无条件授予 `standalone_line`"的形态上。

    旧实现只看本行是否完整、未折行、未截断 —— 与它在不在表格里无关，因此表格行标签
    同样拿到弱主证据 `standalone_line`（`hq-1` 的独立标题行形态）。新实现（`hq-2`）
    对表格成员**抽掉**这条弱主证据，这正是两条规则在这一形态上的分歧点。
    """
    return (candidate.table_scope == OB.TABLE_SCOPE_INSIDE
            and "standalone_line" not in candidate.primary_evidence
            and not candidate.wrap_flagged
            and not candidate.wrap_continuation)


def _legacy_standalone_accepts(candidate) -> bool:
    """旧实现在**这一形态**上不是靠强证据穿透，而是根本没被表格判据拦住。

    旧判据 B 只在 `TABLE_REGION_PROXIMITY_PT`(8.0) 内命中、判据 D 只在
    `TABLE_REGION_FAMILY_TOL_PT` 内闭合成带；两条距离都够不着时它不会被追加
    `table_region_candidate`，于是按普通编号标题走"主证据 ≥1 且 主+辅 ≥2"被采纳。
    这里按同一份**候选自身**证据事实复刻那次判定（不读生产侧结论）：把旧实现无条件
    授予的弱主证据 `standalone_line` 补回去。
    """
    primary = list(candidate.primary_evidence)
    if "standalone_line" not in primary:
        primary.append("standalone_line")
    supporting = list(candidate.supporting_evidence)
    return len(primary) >= 1 and len(primary) + len(supporting) >= 2


def _legacy_label_band_hits(layout, page_number: int, line_index: int) -> bool:
    """旧实现（判据 E **没有**「同一表格对象的结构行闭合」这一条）会不会把该行圈进带里。

    复刻的是**同一份几何事实**与**同一条判据**，唯一去掉的是本轮新增的闭合条件 ——
    "旧实现命中、新实现不命中"因此是可执行断言，而不是对旧代码的回忆。判据的其余部分
    （同族列锚点、带内无值列内容、带内行数下限、字号成员资格）与生产侧逐字一致。
    """
    page = next((p for p in layout.pages
                 if p.page_number == page_number), None)
    if page is None:
        return False
    lines = [line for line in page.lines if not line.is_furniture]
    multi = [row for row in OB._layout_rows(lines) if row["multi"]]
    line = next((item for item in lines if item.line_index == line_index), None)
    if line is None:
        return False
    for upper in multi:
        for lower in multi:
            if lower["y0"] <= upper["y1"]:
                continue
            if len(upper["columns"]) != len(lower["columns"]):
                continue
            if abs(upper["columns"][0] - lower["columns"][0]) \
                    > OB.TABLE_REGION_FAMILY_TOL_PT:
                continue
            if any(abs(a - b) > OB.TABLE_REGION_FAMILY_TOL_PT
                   for a, b in zip(upper["columns"], lower["columns"])):
                continue
            # 旧实现没有这一条：夹在上下两行之间的**其它**多字段行不算数，
            # 因此相距很远的两个表格对象也照样闭合成"标签带"。
            value_edge = upper["columns"][1]
            band = [item for item in lines
                    if item.bbox[1] > upper["y1"]
                    and item.bbox[3] < lower["y0"]]
            if len(band) < OB.TABLE_REGION_BAND_MIN_LINES:
                continue
            if any(item.bbox[0] >= value_edge for item in band):
                continue
            if not any(OB._row_size_member(item, upper) for item in band):
                continue
            if line in band:
                return True
    return False


def _check_inside_unsafe(text: str, fixture, prefix: str, *,
                         form=_layout_strong_form,
                         legacy=_legacy_strong_bypass_accepts,
                         form_msg="单项版式强证据",
                         legacy_msg="强证据无条件穿透表格区域软理由") -> None:
    """§四.1–§四.4 / §四.9 的统一断言：表内强证据单项（或两项版式巧合）不得穿透。"""
    cand, result, titles, _ = _scene_of(fixture, text)
    check(cand is not None, f"{prefix}1 反例行定位到候选：{text!r}")
    if cand is None:
        return
    check(form(cand),
          f"{prefix}2 反例候选确实带**{form_msg}**"
          f"（centered={cand.centered} primary={cand.primary_evidence}）")
    check(legacy(cand),
          f"{prefix}3 **旧实现会采纳**它（{legacy_msg}）：{text!r}")
    check(cand.accepted is False and text not in titles,
          f"{prefix}4 它不进入正式标题树、也不被采纳：{text!r}")
    check(cand.table_scope == OB.TABLE_SCOPE_INSIDE,
          f"{prefix}5 新实现把它判为 `inside_table`（表格**成员资格**证明），"
          f"不是邻近（scope={cand.table_scope}）")
    check(OB.safe_table_override(cand) is False,
          f"{prefix}6 新实现的安全穿透资格不成立（单项强证据 + 普通辅证据）")
    span = _unassigned_at(result, *((cand.page_number, cand.line_index)))
    check(span is not None
          and span.unassigned_reason == OB.UNASSIGNED_INSUFFICIENT_EVIDENCE,
          f"{prefix}7 它 fail-closed 到正式 `formal_unassigned`（证据不足）"
          f"（{None if span is None else span.unassigned_reason}）")


def _test_centered_cell_prose_rejected() -> None:
    """§四.1 / §四.3：宽表中央单元格的编号承诺正文（centered + column_indent）。"""
    _check_inside_unsafe(_W_PROMISE, _pages_wide_cell_centered(), "W1")


def _test_lifted_cell_rejected() -> None:
    """§四.2 / §四.4：单元格字号略高于正文（size_above_body + column_indent + level_layout）。"""
    _check_inside_unsafe(_W_LIFTED_CELL, _pages_lifted_cell(), "W2")


def _test_heading_after_table_restored() -> None:
    """§四.5 / §四.7 / §四.8：表后正式标题 + 正文 + 同级标题 + 新表。"""
    pages = _pages_heading_after_table()
    cand, result, titles, _ = _scene_of(pages, "（二）某业务小节标题")
    check(cand is not None, "W3.1 表后标题定位到候选")
    if cand is None:
        return
    check("（二）某业务小节标题" in titles,
          f"W3.2 表后正式标题回到正式树（titles={titles}）")
    check(cand.accepted is True, "W3.3 该标题被采纳")
    check(cand.table_scope == OB.TABLE_SCOPE_ADJACENT,
          f"W3.4 它被判为 `adjacent_to_table`（仅邻近、不是成员）："
          f"scope={cand.table_scope}")
    check(cand.table_region_reason == OB.TABLE_REGION_ADJACENT_REASON
          and cand.table_region_reason not in OB.TABLE_REGION_INSIDE_REASONS
          and OB.AMBIGUOUS_TABLE_REGION_REASON not in cand.soft_reasons,
          f"W3.5 邻近表格只登记「仅邻近」这一状态、**不**追加表格区域软理由，"
          f"也不得借用任何成员资格判据码"
          f"（table_region_reason={cand.table_region_reason} / "
          f"soft={cand.soft_reasons}）")
    check("standalone_line" in cand.primary_evidence,
          f"W3.6 它凭自身的完整独立标题行形态通过"
          f"（primary={cand.primary_evidence}）")
    check(OB.safe_table_override(cand) is False,
          "W3.7 `adjacent_to_table` 不适用安全穿透资格："
          "它本来就不是表格成员，无需穿透")
    sibling, result2, titles2, _ = _scene_of(pages, "（三）下一同级小节标题")
    check(sibling is not None and "（三）下一同级小节标题" in titles2,
          "W3.8 紧随其后、中间隔着正文的同级标题同样进入正式树")
    if sibling is None:
        return
    # 候选对象不带 `node_id`（那是审计记录的字段）：按来源锚点从正式树里取节点。
    by_anchor = {(n.source_anchor[0], n.source_anchor[1]): n
                 for n in result2.outline.nodes
                 if n.source_anchor is not None}
    node = by_anchor.get((cand.page_number, cand.line_index))
    sib_node = by_anchor.get((sibling.page_number, sibling.line_index))
    check(node is not None and sib_node is not None
          and node.parent_id is not None
          and node.parent_id == sib_node.parent_id,
          "W3.9 两条标题是**同级 siblings**（同一个父节点）")
    table_cell, _, titles3, _ = _scene_of(pages, "1、某明细项目")
    check(table_cell is not None and table_cell.table_scope == OB.TABLE_SCOPE_INSIDE
          and "1、某明细项目" not in titles3,
          f"W3.10 标题后紧跟的新表内容不进入正式树（scope="
          f"{None if table_cell is None else table_cell.table_scope}）")


def _test_valueless_row_label_rejected() -> None:
    """§四.10：值列为空、离列锚点 12–15pt 的表格行标签。"""
    _check_inside_unsafe("（1）本期减少", _pages_valueless_row_label(), "W4",
                         form=_standalone_withheld_form,
                         legacy=_legacy_standalone_accepts,
                         form_msg="完整独立标题行形态（旧实现无条件授予 "
                                  "`standalone_line`，新实现因表格成员资格抽掉它）",
                         legacy_msg="旧判据够不着 12–15pt 的间距，它根本没被表格判据拦住")


def _test_bookmark_backed_inside_table_rejected() -> None:
    """§二.2（`hq-4`）：书签命中的表内标题**不得**穿透；书签只作导航候选。

    这是本轮 P1-A 的**核心反例**，刻意取最强形态：书签声明的物理页就是标题真实所在页、
    标题与目标行文本逐字相同 —— 两条可复核条件全部成立，`hq-3` 据此放行。`hq-4` 仍拒绝：
    书签来自 PDF 大纲，不在 `PageLayout` 里，**没有可重建的来源对象身份**（序号 / 条目
    身份无从复算），因此"我读到了一模一样的标题和页码"不等于"来源身份已重建"。
    """
    layout, result, cands = _build(_pages_toc_backed_inside_table(), _SAFE_TOC)
    hits = _hits(layout, ("（五）某重要事项",))
    check("（五）某重要事项" in hits, "W5.1 反例目标定位到真实版式行")
    if "（五）某重要事项" not in hits:
        return
    cand = _cand_at(cands, *hits["（五）某重要事项"])
    check(cand is not None and cand.table_scope == OB.TABLE_SCOPE_INSIDE,
          f"W5.2 该标题几何上确实落在表格区域内"
          f"（scope={None if cand is None else cand.table_scope}）")
    check(cand is not None and cand.bookmark_matches,
          f"W5.3 书签命中确实存在（这是反例成立的前提："
          f"{None if cand is None else cand.bookmark_matches}）")
    check(_legacy_two_style_override(cand) is True,
          "W5.4 **旧实现（`hq-2`）会放行**它：来源证据码由「书签命中」这一召回事实授予")
    check(cand is not None and OB.safe_table_override(cand, layout=layout) is False,
          "W5.5 新实现即使拿到**真实版式**也不放行：书签不是可重建的来源对象，"
          "两条可复核条件全部成立也只是导航候选（§二.1 / §二.2）")
    landings = OB.verified_source_landings(cand, layout) if cand else ()
    check(landings and all(entry.get("kind") == "bookmark"
                           and entry.get("navigation_only") is True
                           and entry.get("navigation_reason")
                           == OB.BOOKMARK_NAVIGATION_ONLY_REASON
                           for entry in landings),
          f"W5.6 书签命中**逐条**登记为导航候选，带确定性原因码 "
          f"`{OB.BOOKMARK_NAVIGATION_ONLY_REASON}`（landings={landings}）")
    check(not any(entry.get("kind") == "toc" for entry in landings),
          "W5.6b 来源 landing 里没有任何 `kind=\"toc\"` 条目："
          "书签命中无法补上目录项对象链")
    check(cand is not None and cand.accepted is False
          and "（五）某重要事项" not in _titles(result),
          f"W5.7 该标题不进入正式树（accepted="
          f"{None if cand is None else cand.accepted}）")
    check(cand is not None
          and (cand.page_number, cand.line_index)
          not in {(item["page_number"], item["line_index"])
                  for item in OB.structural_review_findings(
                      result, layout)["safe_table_override_accepted"]},
          "W5.8 复核门也不把它列为安全穿透项")
    findings = OB.structural_review_findings(result, layout)
    check(findings["counts"]["unsafe_table_override_accepted"] == 0,
          f"W5.9 同一份产物里没有不安全穿透"
          f"（unsafe={findings['counts']['unsafe_table_override_accepted']}）")
    check(findings["counts"]["adjacent_heading_rejected"] == 0,
          "W5.10 邻近表格的标题没有被自动拒绝")
    # §三.5 / §三.6：复核侧的**六类对账桶**必须把这两类东西分开 —— 书签进
    # `bookmark_navigation_only`，被拒的正文候选进 `body_heading_unassigned`，
    # 二者**不得**合并成一句"真标题漏收"。
    reconciliation = OB.toc_body_reconciliation(result, layout)
    check(len(reconciliation["bookmark_navigation_only"]) == len(_SAFE_TOC),
          f"W5.11 书签来源全部登记在 `bookmark_navigation_only`"
          f"（{len(reconciliation['bookmark_navigation_only'])} 条）")
    check(any(item["title"] == "（五）某重要事项"
              for item in reconciliation["body_heading_unassigned"]),
          "W5.12 被拒的表内候选登记在 `body_heading_unassigned`，与书签来源是**不同桶**")
    check(not reconciliation["toc_body_unassigned"]
          and not reconciliation["toc_body_resolved"],
          "W5.13 这份产物里没有目录页，因此两个 TOC 桶都为空"
          "（不得把书签算成目录来源）")
    check("toc_source_only" not in reconciliation,
          "W5.14 `tocr-1` 的 `toc_source_only` 桶已被废除：同一份输入状态现在按 "
          "`toc_body_unassigned` 记账（改名变语义，不是换标签）")
    # ── W5.15/16 **反例⑤**：**只有 Bookmark** 命中的行 ──
    # 本夹具**没有目录页、没有目录项**，唯一来源是书签（且书签声明的页与标题逐字
    # 都对得上 —— 最强形态）。它必须只作导航候选：不进 `toc_body_unassigned`（它不是
    # "目录已闭合到正文行"）、不进 `toc_body_resolved`（它没有落地身份）。
    bookmark_titles = {entry[1] for entry in _SAFE_TOC}
    check(not (bookmark_titles & {item["title"]
                                  for item in reconciliation["toc_body_unassigned"]}),
          f"W5.15 **反例⑤**：只有书签来源的行**不**进 `toc_body_unassigned`"
          f"（{sorted(bookmark_titles)}）—— 书签不在 `PageLayout` 里，不构成"
          f"「目录项已闭合到正文 landing 行」")
    check(not reconciliation["toc_body_resolved"]
          and bookmark_titles <= {item["title"]
                                  for item in reconciliation["bookmark_navigation_only"]},
          "W5.16 **反例⑤**：书签命中**不**使任何目录项落地（`toc_body_resolved` 为空），"
          "它自己只登记在 `bookmark_navigation_only`——即不得被放行")


def _test_label_band_closed_by_one_table() -> None:
    """判据 E 的「同一表格对象的结构行闭合」：两张不同的表不得圈住中间的小节标题。"""
    pages = _pages_band_across_two_tables()
    cand, result, titles, layout = _scene_of(pages, _W_BAND_HEADING)
    check(cand is not None, f"W7.1 反例标题定位到候选：{_W_BAND_HEADING!r}")
    if cand is None:
        return
    check(_legacy_label_band_hits(layout, cand.page_number, cand.line_index),
          "W7.2 **旧实现**（判据 E 无闭合条件）确实把该标题圈进了"
          "「两条相距很远的同族多字段行之间的标签带」—— 反例打在被修的那条路径上")
    check(cand.table_scope != OB.TABLE_SCOPE_INSIDE,
          f"W7.3 新实现不再把它判成表格成员（scope={cand.table_scope}、"
          f"reason={cand.table_region_reason}）")
    check(cand.table_region_reason != OB.TABLE_REGION_LABEL_BAND_REASON,
          f"W7.4 成员资格判据 E 不再命中（reason={cand.table_region_reason}）")
    check(cand.accepted is True and _W_BAND_HEADING in titles,
          f"W7.5 两张表之间的无歧义小节标题恢复为**正式节点**"
          f"（accepted={cand.accepted}、titles={titles}）")
    check(cand.table_scope in (OB.TABLE_SCOPE_ADJACENT, OB.TABLE_SCOPE_NONE)
          and cand.table_region_reason not in OB.TABLE_REGION_INSIDE_REASONS,
          f"W7.6 它与上下两张表的关系**不是** `inside_table`：标题与上一张表的末行之间"
          f"隔着正文、与下一张表之间也隔着正文，连邻近窗口都够不上"
          f"（scope={cand.table_scope}、reason={cand.table_region_reason}）")
    check("standalone_line" in cand.primary_evidence,
          f"W7.7 它凭自身的完整独立标题行形态通过"
          f"（primary={cand.primary_evidence}）")
    check(OB.safe_table_override(cand) is False,
          "W7.8 它不是表格成员，因此不需要（也不适用）安全穿透资格")
    span = _unassigned_at(result, cand.page_number, cand.line_index)
    check(span is None,
          f"W7.9 它不在正式未归属里（{None if span is None else span.unassigned_reason}）")
    check(len([title for title in titles if title == _W_BAND_HEADING]) == 1,
          f"W7.10 它只对应**一个**正式节点（不重复建节点）：titles={titles}")


# ---------------------------------------------------------------------------
# X. §二/§三 收口：安全穿透只认**对象级验证过**的来源 landing
# ---------------------------------------------------------------------------
#
# 上一轮（`hq-2` / `trg-2`）允许"≥2 项相互独立的版式强证据"穿透 `inside_table`。
# 本轮（§二）把这条路彻底取消：**已经被证明属于表格的候选**仍然可能是居中、放大或
# 加粗的表头 / 强调单元格，两项样式无法证明它不是表格内容。因此 `inside_table` 的
# 唯一穿透依据是**对象级验证过的目录 / 书签正文 landing**，并且生产侧与独立验收侧
# 必须采用**同一条**资格（§三）。

_X_HEADING = "（五）某重要事项"
#: 目录行的点线填充（与真实目录行同形；`parse_toc_entry` 要求 ≥ 下限个填充字符）。
_X_TOC_LEADER = " .........."


def _source_landing_payload(record):
    """取回候选审计记录里**已解析**的来源 landing 载荷（缺失 / 空串 / 非法 → `None`）。

    与生产侧 `_parse_source_landing` 同义，但测试侧刻意不复用它：这里要断言的是"落盘
    的那串载荷本身可解析"，用生产侧解析器会把"解析器与载荷同时坏掉"变成静默通过。
    """
    field = OB.SOURCE_LANDING_FIELD + "="
    for chunk in record.evidence:
        if chunk.startswith(field):
            raw = chunk[len(field):]
            if not raw:
                return None
            try:
                payload = json.loads(raw)
            except ValueError:
                return None
            return payload if isinstance(payload, list) and payload else None
    return None


def _hq_cand(*, primary=(), supporting=(), scope=OB.TABLE_SCOPE_INSIDE,
             reason=OB.TABLE_REGION_ANCHOR_MEMBER_REASON, page=4, line=3,
             text="（五）示例标题", numbered=True, declared=3):
    """只带**证据码**的候选：安全穿透资格的真值表断言只用候选自身事实。"""
    return OB.HeadingCandidate(
        page_number=page, line_index=line, line_text=text, title=text,
        title_normalized=tight(text), numbered=numbered, declared_level=declared,
        font_size=11.0, bold=False, centered="centered" in primary,
        style_path=False, body_recurrence=0, toc_matches=(), bookmark_matches=(),
        rejected=(), primary_evidence=tuple(primary),
        supporting_evidence=tuple(supporting), table_scope=scope,
        table_region_reason=reason)


#: `hq-3` 及以前的来源证据码。"`hq-4` 起生产侧**不再产出**它（语义面收窄为
#: `toc_body_landing`：只有对象级验证过的**目录项** landing 才是来源证据）。这里保留
#: 这个字面量，只用于**复刻旧实现**的放行判定，证明反例确实打在旧规则的采纳路径上。
_LEGACY_SOURCE_EVIDENCE = "toc_or_bookmark"


def _legacy_source_evidence(candidate) -> bool:
    """旧实现（`hq-3` 及以前）在这一候选上是否会授予来源证据码。

    旧实现的授予条件就是"归一标题命中了同名目录项**或**同名书签"——即**召回**事实本身；
    它**不看**来源对象的身份能不能由 `PageLayout` 重建，这正是 `hq-4` 取消的那一条。
    这里按候选自身保留的 `toc_matches` / `bookmark_matches` 复刻，不读任何生产侧结论。
    """
    if _LEGACY_SOURCE_EVIDENCE in candidate.primary_evidence:
        return True
    if candidate.rejected:
        return False
    return bool(candidate.toc_matches or candidate.bookmark_matches)


def _legacy_two_style_override(candidate) -> bool:
    """旧实现（`hq-2`）的 `safe_table_override`：来源佐证，**或** ≥2 项版式强证据。

    与旧实现逐字同形（含 `wrap_flagged` / `wrap_continuation` / 标签列片段三条排除）。
    复刻它是为了把"旧实现会放行哪些表内候选"变成**可执行**断言，而不是对旧代码的回忆
    （§五 明令不得 `git checkout` / `restore`）。

    `hq-4` 起"来源佐证"这个成员不再由生产侧产出，因此这里必须由**召回事实**
    （`toc_matches` / `bookmark_matches`）复刻旧实现的授予条件 —— 否则反例的
    "旧实现会放行"半边会退化成恒假断言，反例就不再打在旧规则的采纳路径上。
    """
    strong = set(candidate.strong_primary_evidence)
    if _legacy_source_evidence(candidate):
        strong.add(_LEGACY_SOURCE_EVIDENCE)
    if _LEGACY_SOURCE_EVIDENCE in strong:
        return True
    if len(strong - {_LEGACY_SOURCE_EVIDENCE}) < 2:
        return False
    if candidate.wrap_flagged or candidate.wrap_continuation:
        return False
    if candidate.table_region_reason == OB.TABLE_REGION_LABEL_COLUMN_REASON:
        return False
    return True


def _two_style_pair(prefix: str, primary, *, msg: str) -> None:
    """§六.1–§六.4：任意两项 / 三项版式强证据都**不得**穿透 `inside_table`。"""
    cand = _hq_cand(primary=primary, supporting=("column_indent",))
    check(_legacy_two_style_override(cand) is True,
          f"{prefix}1 **旧实现会放行**它（{msg}）：{primary}")
    check(OB.safe_table_override(cand) is False,
          f"{prefix}2 新实现不放行：两项版式强证据证明不了「它不是表格内容」（§二）")


def _test_two_style_evidence_never_overrides() -> None:
    """§六.1–§六.5：版式强证据无论几项、怎么组合都不得穿透 `inside_table`。"""
    _two_style_pair("X1", ("size_above_body", "centered"),
                    msg="`size_above_body + centered`")
    _two_style_pair("X2", ("size_above_body", "bold_majority"),
                    msg="`size_above_body + bold_majority`")
    _two_style_pair("X3", ("centered", "bold_majority"),
                    msg="`centered + bold_majority`")
    _two_style_pair("X4", ("size_above_body", "centered", "bold_majority"),
                    msg="三项版式强证据")
    # §六.5：多项普通 supporting 证据同样不参与穿透。
    many_support = _hq_cand(primary=("centered",),
                            supporting=("column_indent", "paragraph_boundary",
                                        "heading_context", "level_layout"))
    check(_legacy_two_style_override(many_support) is False
          and OB.safe_table_override(many_support) is False,
          "X5 单项版式强证据 + 多项普通 supporting（含 `level_layout`）不穿透："
          "普通辅证据不参与穿透计数（新旧实现在此一致，作为下界的回归锚点）")


def _test_self_reported_source_without_object() -> None:
    """§六.6：自报来源证据码但**没有**真实来源对象 → 拒绝（fail-closed）。"""
    cand = _hq_cand(primary=(_LEGACY_SOURCE_EVIDENCE,),
                    supporting=("column_indent",))
    check(_legacy_two_style_override(cand) is True,
          f"X6.1 **旧实现会放行**：它只看见证据码里写着 "
          f"`{_LEGACY_SOURCE_EVIDENCE}`（召回事实 = 来源事实）")
    check(OB.safe_table_override(cand) is False,
          "X6.2 新实现不放行：证据码不是来源对象，自报的字符串无法穿透（§二）")
    check(_LEGACY_SOURCE_EVIDENCE not in OB.HEADING_PRIMARY_EVIDENCE
          and _LEGACY_SOURCE_EVIDENCE not in OB.HEADING_STRONG_PRIMARY_EVIDENCE,
          f"X6.3 `hq-4` 起 `{_LEGACY_SOURCE_EVIDENCE}` **退出**资格词表：一条挂着旧"
          f"证据码的 `hq-3` 载荷不得按新语义被读成「来源已验证」")


def _pages_inside_table_heading() -> list:
    """一条**几何上确实落在表格区域内**的标题（来源 landing 的正反例共用）。

    第 4 页（印 "3"）的宽表中间夹着 `（五）某重要事项`（9.0pt 单元格字号，落在第二列
    列锚点上）—— 判据 B 命中，`inside_table`。它能否进入正式树**只**由来源 landing
    决定。封面无页码，第 2..5 页印 "1".."4"，因此本页页标签是 "3"。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_section_head() + _filler("A"), "1"),
        (_filler("B"), "2"),
        (_wide_row(200.0, first="承诺方", second="承诺内容", third="承诺期限。")
         + [(_W_X0, 226.0, _X_HEADING, _W_CELL, False)]
         + _wide_row(250.0), "3"),
        (_filler("D"), "4"),
    ]


def _pages_toc_landing(declared_label: str) -> list:
    """目录页（印 "2"）登记一条目录项，声明的页标签为 `declared_label`。

    正文标题在第 4 页（印 "3"）。`declared_label="3"` ⇒ 页标签映射指向标题所在物理页；
    `declared_label="2"` ⇒ 映射到目录页自己，**指向错误页**。

    文档必须够长：页码 furniture 规则要求"页标签 − 物理页"的众数偏移至少覆盖
    `PAGE_NUMBER_MIN_PAGES`(5) 页（`layout_builder._detect_furniture`），封面不编号时
    至少要 6 个编号页。少于这个长度时 `_resolve_page_label` 一定返回 `None`，正例就
    测不到"页标签映射成立"这一条。这里放 7 页（编号 "1".."6"）。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_section_head() + _filler("A"), "1"),
        ([(72.0, 72.0, "目录", 16.0, False),
          (72.0, 100.0, _X_HEADING + _X_TOC_LEADER + " " + declared_label,
           11.0, False)] + _filler("B"), "2"),
        (_wide_row(200.0, first="承诺方", second="承诺内容", third="承诺期限。")
         + [(_W_X0, 226.0, _X_HEADING, _W_CELL, False)]
         + _wide_row(250.0), "3"),
        (_filler("D"), "4"),
        (_filler("E"), "5"),
        (_filler("F"), "6"),
    ]


def _rejected_inside_table(prefix: str, pages, *, toc=None) -> None:
    """来源 landing 不成立时：表内候选不得被采纳、不得进入树、不得穿透。"""
    layout, result, cands = _build(pages, toc)
    hits = _hits(layout, (_X_HEADING,))
    check(_X_HEADING in hits, f"{prefix}1 反例标题定位到真实版式行")
    if _X_HEADING not in hits:
        return
    cand = _cand_at(cands, *hits[_X_HEADING])
    check(cand is not None and cand.table_scope == OB.TABLE_SCOPE_INSIDE,
          f"{prefix}2 该行几何上确实是表格成员（scope="
          f"{None if cand is None else cand.table_scope}）")
    if cand is None:
        return
    check(cand.strong_primary_evidence == (),
          f"{prefix}3 `hq-4` 起这条候选**自身**不带任何强主证据：来源证据码只授予"
          f"「对象级验证过的目录项 landing」（primary={cand.primary_evidence}、"
          f"强={cand.strong_primary_evidence}）")
    check(_legacy_two_style_override(cand) is True,
          f"{prefix}4 **旧实现会放行**它：它把「同名目录项 / 书签命中」这一**召回**事实"
          f"当成来源佐证（`{_LEGACY_SOURCE_EVIDENCE}`）")
    check(OB.safe_table_override(cand, layout=layout) is False
          and OB.verified_source_landings(cand, layout) == (),
          f"{prefix}5 新实现即使拿到**真实版式**也不放行：来源 landing 没有通过对象级"
          f"验证（页标签映射 / 目标行 / 对象身份三条之一不成立，§二 / §三）")
    check(cand.accepted is False and _X_HEADING not in _titles(result),
          f"{prefix}6 它不进入正式标题树")


def _test_toc_landing_wrong_page_rejected() -> None:
    """§六.7：目录 landing 指向**错误页** → 拒绝。"""
    _rejected_inside_table("X7", _pages_toc_landing("2"))


def _test_bookmark_landing_wrong_page_rejected() -> None:
    """§六.8：书签 landing 身份不符（声明物理页 ≠ 标题所在页）→ 拒绝。"""
    _rejected_inside_table("X8", _pages_inside_table_heading(),
                           toc=[[1, _X_HEADING, 3]])


def _test_verified_toc_landing_allows_override() -> None:
    """§六.9：真实目录 landing、对象身份与目标行全部一致 → 允许穿透并逐条留痕。"""
    layout, result, cands = _build(_pages_toc_landing("3"))
    hits = _hits(layout, (_X_HEADING,))
    check(_X_HEADING in hits, "X9.1 正例标题定位到真实版式行")
    if _X_HEADING not in hits:
        return
    cand = _cand_at(cands, *hits[_X_HEADING])
    check(cand is not None and cand.table_scope == OB.TABLE_SCOPE_INSIDE
          and cand.accepted is True and _X_HEADING in _titles(result),
          "X9.2 表内标题凭**已验证的目录 landing** 进入正式树")
    if cand is None:
        return
    record = next((r for r in result.candidates
                   if getattr(r, "kind", None) == "heading"
                   and (r.page_number, r.line_index)
                   == (cand.page_number, cand.line_index)), None)
    fields = _evidence_dict(record) if record is not None else {}
    payload = _source_landing_payload(record) if record is not None else None
    check(payload is not None and payload[0].get("kind") == "toc",
          f"X9.3 候选审计**持久化**了来源 landing 载荷"
          f"（字段 {OB.SOURCE_LANDING_FIELD!r}）：旧实现只写 "
          f"`{_LEGACY_SOURCE_EVIDENCE}` 字符串，复核方无从独立重算（payload={payload}）")
    findings = OB.structural_review_findings(result, layout)
    safe = findings.get("safe_table_override_accepted") or []
    entry = next((item for item in safe
                  if (item["page_number"], item["line_index"])
                  == (cand.page_number, cand.line_index)), None)
    check(entry is not None,
          f"X9.4 复核门把它逐条列为安全穿透项（不是只给数量）："
          f"{[(i['page_number'], i['line_index']) for i in safe]}")
    if entry is not None:
        landing = entry.get("landing") or {}
        computed = [e for e in (landing.get("entries") or [])
                    if e.get("kind") == "toc" and e.get("recomputed") is True]
        check(landing.get("reported") is True and landing.get("available") is True
              and computed and not landing.get("problems")
              and landing.get("bookmark_identity_recheckable") is True,
              f"X9.5 逐条证据里含**独立重算**的 landing 结果"
              f"（landing={entry.get('landing')}）")
    check((findings.get("counts") or {}).get("safe_override_mismatch") == 0
          and (findings.get("counts") or {}).get("unsafe_override_mismatch") == 0,
          f"X9.6 生产侧与复核侧结论一致（无 mismatch）："
          f"{findings.get('counts')}")


def _bookmark_landing_payload(layout, page: int, line: int, title: str) -> str:
    """构造一条**可被复核侧重算通过**的书签 landing 载荷（规范 JSON）。

    书签是最小可复核单元：声明物理页 == 候选所在页 + 标题 == 目标行真实文本。这里用
    **真实版式**上的那一行算 `title_normalized`，因此载荷内容与版式自洽。
    """
    target = layout.line_at(page, line)
    return json.dumps([{
        "kind": "bookmark", "ordinal": 0, "title": title,
        "title_normalized": OB.normalize_toc_title(target.text),
        "declared_level": 0, "bookmark_page": page,
        "landing_page": page, "landing_line": line}], ensure_ascii=False)


def _test_bookmark_payload_never_reviews_available() -> None:
    """§二.2（`hq-4`）：**两条可复核条件全部成立**的书签载荷也不得使复核侧 `available`。

    这是最低层的反例：不经过生产侧，直接把一条"页码与标题都对得上真实行"的书签载荷喂给
    复核侧。`hq-3` 下它会通过（复核侧只重算页码 + 标题两条），`hq-4` 下不行 —— 书签的
    来源对象身份（PDF 大纲条目序号）不在 `PageLayout` 里，**没有可重建的对象**。
    """
    layout, _, _ = _build(_pages_inside_table_heading())
    page, line = _hits(layout, (_X_HEADING,))[_X_HEADING]
    good = _bookmark_landing_payload(layout, page, line, _X_HEADING)
    base = _stub_cand(page, line, _X_HEADING, primary=(), accepted=False,
                      node_id=None, table_scope=OB.TABLE_SCOPE_INSIDE)

    facts = OB._independent_source_landing_facts(
        layout, types.SimpleNamespace(
            page_number=page, line_index=line, evidence=(f"{OB.SOURCE_LANDING_FIELD}={good}",)),
        toc_pages=())
    check(facts["reported"] is True and facts["available"] is False
          and facts["bookmark_identity_recheckable"] is False,
          f"X18.1 页码与标题都对得上真实行的书签载荷：`reported=True` 但 "
          f"`available=False`（`hq-3` 下这两条成立即放行）")
    check(len(facts["navigation_only"]) == 1
          and facts["navigation_only"][0]["navigation_reason"]
          == OB.BOOKMARK_NAVIGATION_ONLY_REASON
          and not facts["navigation_only"][0].get("ordinal"),
          f"X18.2 它只被登记为导航候选：带确定性原因码，且复核侧**不**采信自报的 "
          f"`ordinal`（{facts['navigation_only']}）")
    check(not facts["entries"],
          "X18.3 `entries`（已复核的来源对象）为空：书签不能充当来源对象")

    # 错误标题、伪造序号各自单独构成拒绝理由，不是靠"两条里错了一条"顺带拒绝。
    wrong_title = json.dumps([{
        "kind": "bookmark", "ordinal": 7, "title": "（六）另一个标题",
        "title_normalized": OB.normalize_toc_title("（六）另一个标题"),
        "declared_level": 0, "bookmark_page": page,
        "landing_page": page, "landing_line": line}], ensure_ascii=False)
    wrong = OB._independent_source_landing_facts(
        layout, types.SimpleNamespace(
            page_number=page, line_index=line,
            evidence=(f"{OB.SOURCE_LANDING_FIELD}={wrong_title}",)),
        toc_pages=())
    check(wrong["available"] is False and not wrong["navigation_only"]
          and any("标题不一致" in problem for problem in wrong["problems"]),
          f"X18.4 标题与目标行不一致的书签被逐条记因拒绝（problems={wrong['problems']}）")
    wrong_page = json.dumps([{
        "kind": "bookmark", "ordinal": 0, "title": _X_HEADING,
        "title_normalized": OB.normalize_toc_title(_X_HEADING),
        "declared_level": 0, "bookmark_page": page - 1,
        "landing_page": page, "landing_line": line}], ensure_ascii=False)
    bad = OB._independent_source_landing_facts(
        layout, types.SimpleNamespace(
            page_number=page, line_index=line,
            evidence=(f"{OB.SOURCE_LANDING_FIELD}={wrong_page}",)),
        toc_pages=())
    check(bad["available"] is False and not bad["navigation_only"]
          and any("物理页码" in problem for problem in bad["problems"]),
          f"X18.5 声明物理页与候选所在页不符的书签被逐条记因拒绝"
          f"（problems={bad['problems']}）")

    # 旧证据码（`hq-3` 载荷）在复核侧**不得**被当成来源类型读懂。
    legacy = json.dumps([{"kind": _LEGACY_SOURCE_EVIDENCE, "title": _X_HEADING}],
                        ensure_ascii=False)
    old = OB._independent_source_landing_facts(
        layout, types.SimpleNamespace(
            page_number=page, line_index=line,
            evidence=(f"{OB.SOURCE_LANDING_FIELD}={legacy}",)),
        toc_pages=())
    check(old["available"] is False
          and any("未知来源类型" in problem for problem in old["problems"]),
          f"X18.6 `hq-3` 的旧来源类型不按新语义读取（fail-closed，"
          f"problems={old['problems']}）")


def _test_override_mismatch_is_blocking() -> None:
    """§六.10 / §六.11：两侧结论分叉必须**显式**记录，不得静默。"""
    layout, _, _ = _build(_pages_inside_table_heading())
    page, line = _hits(layout, (_X_HEADING,))[_X_HEADING]

    # (a) 生产侧自报"凭已验证目录项 landing 采纳"（`accepted_by=toc_landing`，证据码里
    #     也挂着 `toc_body_landing`），但审计记录里**没有**任何可复核的载荷 → 复核侧
    #     重建不出对象级目录项 landing。这就是 `hq-4` 新增的**阻断**计数。
    forged = OB.structural_review_findings(_stub_result(
        nodes=[_stub_node("n1", _X_HEADING, page, line)],
        candidates=[_stub_cand(page, line, _X_HEADING,
                               primary=(OB.TOC_BODY_LANDING_EVIDENCE,), bookmarks=1,
                               accepted_by="toc_landing",
                               table_scope=OB.TABLE_SCOPE_INSIDE)],
        layout=layout))
    checks = forged.get("counts") or {}
    check(checks.get("toc_landing_unverified_accepted") == 1,
          f"X10.1 生产侧自报「凭来源 landing 放行」、复核侧重建不出来 → "
          f"`toc_landing_unverified_accepted` 阻断（counts={checks}）")
    check(forged["clean"] is False
          and (forged.get("safe_table_override_accepted") or []) == [],
          "X10.2 该分叉不得以「非阻断 audit 项」收场：clean=False 且安全穿透清单为空")
    check(any(item["kind"] == "toc_landing_unverified"
              for item in forged.get("verification_gaps", ())),
          f"X10.3 复核门显式登记 `toc_landing_unverified` 缺口，要求以 `hq-4` 重算"
          f"（gaps={[g['kind'] for g in forged.get('verification_gaps', ())]}）")
    check(checks.get("unsafe_table_override_accepted") == 1
          and forged["inside_table_accepted"] == 1,
          f"X10.4 它同时计入不安全穿透（阻断），不是只留痕"
          f"（unsafe={checks.get('unsafe_table_override_accepted')}）")
    check((forged.get("toc_landing_unverified_accepted") or [{}])[0]
          .get("landing", {}).get("available") is False,
          "X10.5 逐条明细带复核侧**自己重算**的 landing 结论（不是只给一个数量）")

    # (a2) 同一形态、但生产侧只自报书签：书签命中**不**触发 `safe_override_mismatch`
    #      （它不是穿依据），仍然计入不安全穿透。
    bookmark_only = OB.structural_review_findings(_stub_result(
        nodes=[_stub_node("n1", _X_HEADING, page, line)],
        candidates=[_stub_cand(page, line, _X_HEADING, primary=(), bookmarks=1,
                               table_scope=OB.TABLE_SCOPE_INSIDE)],
        layout=layout))
    b_checks = bookmark_only.get("counts") or {}
    check(b_checks.get("toc_landing_unverified_accepted") == 0
          and b_checks.get("unsafe_table_override_accepted") == 1,
          f"X10.6 只自报书签命中的表内节点：不新增 `toc_landing_unverified`，"
          f"但仍是不安全穿透（counts={b_checks}）")

    # (b) 反方向：生产侧**未采纳**（以「表内候选」降级）、复核侧却能重算出成立的对象级
    #     **目录项** landing —— 显式记录，不得静默分叉。
    real_layout, real_result, _ = _build(_pages_toc_landing("3"))
    real_page, real_line = _hits(real_layout, (_X_HEADING,))[_X_HEADING]
    record = next(
        (r for r in real_result.candidates
         if getattr(r, "kind", None) == "heading"
         and (r.page_number, r.line_index) == (real_page, real_line)), None)
    payload = _evidence_dict(record).get(OB.SOURCE_LANDING_FIELD) if record else None
    check(bool(payload),
          f"X11.0 生产侧确实为已验证的目录项 landing 持久化了载荷（{payload!r}）")
    softened = OB.structural_review_findings(_stub_result(
        nodes=[],
        candidates=[_stub_cand(
            real_page, real_line, _X_HEADING,
            primary=(OB.TOC_BODY_LANDING_EVIDENCE,), accepted=False,
            node_id=None, table_scope=OB.TABLE_SCOPE_INSIDE,
            soft=(OB.AMBIGUOUS_TABLE_REGION_REASON,), landing=payload or "")],
        layout=real_layout))
    soft_checks = softened.get("counts") or {}
    check(soft_checks.get("unsafe_override_mismatch") == 1,
          f"X11.1 生产侧判 unsafe、复核侧复算 safe → 显式 `unsafe_override_mismatch`"
          f"（counts={soft_checks}）")
    check((softened.get("unsafe_override_mismatch") or [{}])[0]
          .get("landing", {}).get("available") is True,
          "X11.2 该分叉条目里带**复核侧重算**出来的 landing 证据（不是只给一个数量）")
    check(softened["clean"] is False,
          "X11.3 两侧分叉时不得返回 clean")

    # (c) 缺 PageLayout / 缺候选审计 / 缺来源上下文都必须 fail-closed。
    gap = OB.structural_review_findings(_stub_result(
        nodes=[], candidates=[], layout=None))
    kinds = {item.get("kind") for item in gap.get("verification_gaps", ())}
    check("page_layout_unavailable" in kinds
          and "candidate_audit_unavailable" in kinds and not gap["clean"],
          f"X12 缺 `PageLayout` / 候选审计 → fail-closed，不得返回 clean（gaps={kinds}）")


def _test_version_closure_hq4_trg3() -> None:
    """§六.13 / §六.14：`hq-3` 旧载荷不得按新语义读取；`trg-3` 几何判据未变。"""
    check(V.HEADING_QUALIFICATION_PROFILE_VERSION == "hq-4",
          f"X13.1 `hq` 资格语义再次改变（书签退出资格与穿透，来源面收窄为对象级"
          f"目录项 landing）⇒ 必须再升版："
          f"{V.HEADING_QUALIFICATION_PROFILE_VERSION}")
    check(V.legacy_versions("HEADING_QUALIFICATION_PROFILE_VERSION")
          == ("hq-1", "hq-2", "hq-3"),
          f"X13.2 `hq-3` 必须登记为 legacy（只显式拒绝 / 要求重算）："
          f"{V.legacy_versions('HEADING_QUALIFICATION_PROFILE_VERSION')}")
    check(V.classify_schema_version("HEADING_QUALIFICATION_PROFILE_VERSION",
                                    "hq-3") == "legacy",
          "X13.3 `hq-3` 被识别为 legacy，而不是 current / unknown")
    check(V.classify_schema_version("HEADING_QUALIFICATION_PROFILE_VERSION",
                                    "hq-4") == "current",
          "X13.3b 当前 `hq-4` 被识别为 current")
    check(V.TABLE_REGION_QUALIFICATION_VERSION == "trg-3",
          f"X13.4 `trg-3` 的几何判据本轮**未变**（`hq-4` 改的是来源证据与穿透依据，"
          f"不是成员资格判据）⇒ 不得借升版掩盖几何变更："
          f"{V.TABLE_REGION_QUALIFICATION_VERSION}")
    check(V.legacy_versions("TABLE_REGION_QUALIFICATION_VERSION")
          == ("trg-1", "trg-2"),
          f"X13.5 `trg-1` 历史状态保持、`trg-2` 加入 legacy："
          f"{V.legacy_versions('TABLE_REGION_QUALIFICATION_VERSION')}")
    check(V.classify_schema_version("TABLE_REGION_QUALIFICATION_VERSION",
                                    "trg-2") == "legacy",
          "X13.6 `trg-2` 被识别为 legacy，而不是 current / unknown")
    check(V.classify_schema_version("TABLE_REGION_QUALIFICATION_VERSION",
                                    V.TABLE_REGION_QUALIFICATION_VERSION)
          == "current"
          and V.classify_schema_version("TABLE_REGION_QUALIFICATION_VERSION",
                                        "trg-9") == "unknown",
          "X13.7 当前版本仍分类为 current，未知版本仍为 unknown（登记改名不改语义）")


def _test_unsafe_override_regression_anchors() -> None:
    """§六.15 / §六.16：既有结论不回归（邻近表格的标题通过、表内污染不进入树）。"""
    for text, fixture, prefix in ((_W_PROMISE, _pages_wide_cell_centered(), "X14"),
                                  (_W_LIFTED_CELL, _pages_lifted_cell(), "X15"),
                                  ("（1）本期减少",
                                   _pages_valueless_row_label(), "X16")):
        cand, result, titles, _ = _scene_of(fixture, text)
        check(cand is not None and cand.accepted is False and text not in titles,
              f"{prefix} 表内污染候选仍然不进入正式树：{text!r}")
    cand, result, titles, _ = _scene_of(_pages_heading_after_table(),
                                        "（二）某业务小节标题")
    check(cand is not None and cand.accepted is True
          and "（二）某业务小节标题" in titles,
          "X17 `adjacent_to_table` 的正式标题继续通过（§三.3 不得自动拒绝）")


# ---------------------------------------------------------------------------
# Y. §四 P1-B：**目录来源**与**正文 landing** 的分离
# ---------------------------------------------------------------------------

#: §四 P1-B 的正文一级标题形态：**无编号**、与正文**同字号**、自成一行的正文 landing
#: （真实材料里的"致股东的信"就是这样：既不居中、字号也不高于正文、更没有编号）。
_Y_ROOT = "某前言"
#: 第二个正文一级标题：用来证明"同名小标题分属不同章节"由**完整来源路径**区分。
_Y_ROOT2 = "某后记"
#: 普通正文小标题：**有编号**、与正文同字号，目录项指向它。
_Y_SUB = "（一）某小节标题"
#: 目录项指向的目标是**普通表格内容**（多字段表格行的一格）——不得因此变成正文节点。
_Y_CELL = "（二）某表格事项"
#: 目录项指向的目标在正文里**不存在**——必须留在未解析，不得编造节点。
_Y_DECOY = "（三）某未收录事项"
#: 目录项指向一条**真实存在**的正文长行（长度超过 `MAX_UNNUMBERED_TITLE_LEN`，因此它
#: 自身不是标题候选）。它是"目录来源成立、目标行真实存在，但目标行**没有被采纳**"的
#: 诚实形态：归 `toc_source_only`，而**不是**"真标题漏收"。
_Y_LONG_TITLE = ("本公司董事会及全体董事保证本报告内容不存在任何虚假记载误导性陈述"
                 "或重大遗漏并对其负责")
_Y_TOC_LEADER = " .........."
#: 长标题目录项用的短填充：目录行必须整行排在页面内（`layout_out_of_page` 是硬不变量，
#: 越过页面右下角会直接拒绝整份版式）。填充仍 ≥ `TOC_MIN_LEADER_CHARS`(3)。
_Y_TOC_LEADER_SHORT = " ..."
#: 判"长标题"的门槛：取生产侧的 `MAX_UNNUMBERED_TITLE_LEN`（**不**另立一个数字），
#: 这样"目标行太长 ⇒ 不是无编号标题候选"这一事实由被测常量本身决定。
_Y_LONG_MIN_TIGHT = OB.MAX_UNNUMBERED_TITLE_LEN


def _y_toc_lines() -> list:
    """目录页：7 条真实目录项。声明的页标签一律是**真实页标签**，由 `PageLayout` 的
    页码 furniture 唯一映射到物理页（不做偏移猜测）。

    其中 `（一）某小节标题` 出现**两次**、标题逐字相同，只有声明的页标签不同 —— 这是
    §四 P1-B(3)"目标行"那一段的判据来源：同名的两条目录项由**完整来源路径 + 真实物理
    页**区分，任何一侧对不上就不授予 landing。
    """
    entries = ((_Y_ROOT, "3"), (_Y_SUB, "3"), (_Y_CELL, "4"),
               (_Y_SUB, "5"), (_Y_ROOT2, "5"), (_Y_DECOY, "3"),
               (_Y_LONG_TITLE, "6"))
    items: list = [(72.0, 72.0, "目录", 16.0, False)]
    for index, (title, label) in enumerate(entries):
        items.append((72.0, 104.0 + index * 22.0,
                      _y_toc_line(title, label), 11.0, False))
    return items


def _y_toc_line(title: str, label: str) -> str:
    """一条目录来源行的**完整原文**（标题 + 填充 + 页标签）。

    长标题用更短的填充且不再夹空格 —— 目录行整行必须排在页面内（越界会触发
    `layout_out_of_page` 硬不变量）。两条形态都满足 `parse_toc_entry` 的结构要求：
    ≥ `TOC_MIN_LEADER_CHARS`(3) 个填充字符紧接页标签。
    """
    if len(tight(title)) > _Y_LONG_MIN_TIGHT:
        return title + _Y_TOC_LEADER_SHORT + label
    return title + _Y_TOC_LEADER + " " + label


def _pages_toc_body_split() -> list:
    """§四 P1-B 的完整夹具：封面 + 目录页 + 四页正文（正文页标签 "3".."6"）。

    - 第 4 页（印 "3"）：`某前言`（无编号正文 landing）+ 正文 + `（一）某小节标题`；
    - 第 5 页（印 "4"）：网格表格，第一列行标签**逐字等于**一条目录项标题；
    - 第 6 页（印 "5"）：`某后记` + 正文 + **与上一章逐字同名**的 `（一）某小节标题`；
    - 第 7 页（印 "6"）：正文 + 目录项指向的长正文行。

    封面不编号、其余 6 页印 "1".."6"：页码 furniture 的众数偏移因此是 −1，且满足
    `PAGE_NUMBER_MIN_PAGES`(5)，`_resolve_page_label` 才能把 "3"/"4"/"5"/"6" 唯一
    映射到物理页 4/5/6/7。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_section_head() + _filler("A"), "1"),
        (_y_toc_lines(), "2"),
        (_filler("B")
         + [(72.0, 240.0, _Y_ROOT, 11.0, False)]
         + H._body(280.0, tag="R")
         + [(72.0, 400.0, _Y_SUB, 11.0, False)]
         + H._body(440.0, tag="S", long_line=True), "3"),
        (_filler("C") + _grid([_Y_CELL]), "4"),
        (_filler("D")
         + [(72.0, 240.0, _Y_ROOT2, 11.0, False)]
         + H._body(280.0, tag="U")
         + [(72.0, 400.0, _Y_SUB, 11.0, False)]
         + H._body(440.0, tag="V", long_line=True), "5"),
        (_filler("E", ) + [(72.0, 240.0, _Y_LONG_TITLE, 11.0, False)], "6"),
    ]


def _y_scene():
    """构建 Y 夹具一次，返回 `(layout, result, candidates)`。"""
    return _build(_pages_toc_body_split())


def _y_node(result, title: str):
    for node in result.outline.nodes:
        if node.title == title:
            return node
    return None


def _y_nodes(result, title: str) -> list:
    return [node for node in result.outline.nodes if node.title == title]


def _y_payload(result, page: int, line: int):
    """取某条**正文候选**在候选审计里持久化的来源 landing 载荷（复核方的唯一输入）。"""
    record = next((r for r in result.candidates
                   if getattr(r, "kind", None) == "heading"
                   and (r.page_number, r.line_index) == (page, line)), None)
    if record is None:
        return None
    return _source_landing_payload(record)


def _legacy_unnumbered_landing_accepts(candidate) -> bool:
    """旧实现（`hq-3`）**必然漏收**无编号的正文 landing。

    `hq-3` 的证据装配整段写在 `if numbered ...` 分支里：一条没有编号的正文行即使逐字
    命中真实目录项，也拿不到任何主证据，只能靠 `style_path`（加粗 + 字号高于正文）——
    而真实材料里的"致股东的信"这类独立首页**既不居中也不放大**。这就是 §四 P1-B 点名
    的"真标题漏收"的真实成因；本函数把它变成**可执行**断言，而不是对旧代码的回忆。
    """
    if candidate.numbered:
        return False
    return bool(candidate.style_path and not candidate.rejected)


def _test_toc_source_body_landing_separation() -> None:
    """§四 P1-B：目录来源行 ≠ 正文 landing；只有真实正文行能成为节点。"""
    layout, result, cands = _y_scene()
    toc_pages, entries = OB.collect_toc_entries(layout)
    check(len(toc_pages) == 1 and len(entries) == 7,
          f"Y1.0 夹具自检：目录页 {toc_pages}、目录项 {len(entries)} 条")
    hits = _hits(layout, (_Y_ROOT, _Y_ROOT2, _Y_SUB, _Y_CELL))
    check(set(hits) == {_Y_ROOT, _Y_ROOT2, _Y_SUB, _Y_CELL},
          f"Y1.1 四类目标行都在真实版式上定位到了（hits={sorted(hits)}）")

    # ── Y2：无编号的正文 landing 必须进树（旧实现必然漏收） ──
    root_cand = _cand_at(cands, *hits[_Y_ROOT]) if _Y_ROOT in hits else None
    check(root_cand is not None and root_cand.numbered is False
          and root_cand.style_path is False,
          "Y2.1 `某前言` 是**无编号、无字号提升**的正文行（真实材料里这类一级标题的形态）")
    if root_cand is not None:
        check(_legacy_unnumbered_landing_accepts(root_cand) is False,
              "Y2.2 **旧实现漏收**它：`hq-3` 的证据装配只在编号分支里，无编号行"
              "即使逐字命中真实目录项也拿不到主证据")
        check(root_cand.accepted is True
              and OB.TOC_BODY_LANDING_EVIDENCE in root_cand.primary_evidence
              and root_cand.navigation_level == 0,
              f"Y2.3 它凭**已验证目录项 landing** 取得标题资格，层级来自目录路径深度"
              f"（primary={root_cand.primary_evidence}、"
              f"navigation_level={root_cand.navigation_level}）")
    root = _y_node(result, _Y_ROOT)
    check(root is not None and root.level == 0 and root.parent_id is None
          and _Y_ROOT in _titles(result),
          "Y2.4 它进入正式树，层级 0、无父节点")

    # ── Y3：同名小标题凭**完整来源路径**挂到正确父节点 ──
    subs = _y_nodes(result, _Y_SUB)
    check(len(subs) == 2,
          f"Y3.1 两章里的同名小标题都进了树（实际 {len(subs)} 条）")
    sub1 = _cand_at(cands, *hits[_Y_SUB]) if _Y_SUB in hits else None
    if sub1 is not None:
        check(sub1.navigation_level == sub1.declared_level == 2,
              f"Y3.2 它的**导航层级**取自目录项声明的路径深度，且与正文自身的编号层级"
              f"一致：navigation_level={sub1.navigation_level} / "
              f"declared_level={sub1.declared_level}")
        check(_legacy_unnumbered_landing_accepts(sub1) is False,
              "Y3.3 旧实现在这条**有编号**的小标题上留下了半边：它靠 `style_only`"
              "之外的路径也拿不到来源证据（作为对照锚点）")
    if root is not None and len(subs) == 2:
        child = next((n for n in subs if n.source_anchor[0] == hits[_Y_ROOT][0]), None)
        check(child is not None and child.parent_id == root.node_id
              and child.structural_path == (_Y_ROOT, _Y_SUB) and child.level == 1,
              f"Y3.4 第一章的小标题挂在 `{_Y_ROOT}` 下、路径 = "
              f"{None if child is None else child.structural_path}")

    # ── Y4：同名但**页映射不符**的目录项不得授予 landing ──
    if sub1 is not None:
        verified = OB.verified_toc_landings(
            layout=layout, page_number=sub1.page_number,
            line_index=sub1.line_index, landing_title=sub1.title_normalized,
            toc_matches=sub1.toc_matches)
        check(len(sub1.toc_matches) == 2,
              f"Y4.1 该行**召回**到两条同名目录项（章节不同）：{len(sub1.toc_matches)}")
        check(len(verified) == 1
              and verified[0]["kind"] == "toc"
              and verified[0]["declared_page_label"] == "3"
              and verified[0]["physical_page"] == sub1.page_number,
              f"Y4.2 **只有页标签真正映射到本页**的那一条通过对象级验证："
              f"{[(e['declared_page_label'], e['physical_page']) for e in verified]}")

    # ── Y5：同名目录项指向**普通表格内容** ⇒ 不得成为正文节点 ──
    cell_cand = _cand_at(cands, *hits[_Y_CELL]) if _Y_CELL in hits else None
    check(cell_cand is not None and _Y_CELL not in _titles(result),
          "Y5.1 与目录项逐字同名的**表格行标签**没有进入正文树")
    if cell_cand is not None:
        check(cell_cand.table_scope == OB.TABLE_SCOPE_INSIDE
              and cell_cand.table_region_reason == OB.TOC_LANDING_TABLE_CELL_REASON,
              f"Y5.2 它几何上就是多字段表格行的一格"
              f"（{cell_cand.table_scope} / {cell_cand.table_region_reason}）")
        check(OB.toc_landing_target_is_body_line(
            cell_cand.table_scope, cell_cand.table_region_reason) is False,
              "Y5.3 §四 P1-B(3) 的目标行判据把它排除在正文行之外")
        check(OB.TOC_BODY_LANDING_EVIDENCE not in cell_cand.primary_evidence
              and cell_cand.accepted is False,
              f"Y5.4 目录项指向它**不**授予来源证据（primary="
              f"{cell_cand.primary_evidence}）")
        check(OB.toc_landing_target_is_body_line(
            OB.TABLE_SCOPE_INSIDE, OB.TABLE_REGION_ANCHOR_MEMBER_REASON) is True
              and OB.toc_landing_target_is_body_line(
                  OB.TABLE_SCOPE_ADJACENT, OB.TABLE_REGION_ADJACENT_REASON) is True
              and OB.toc_landing_target_is_body_line(
                  OB.TABLE_SCOPE_NONE, None) is True,
              "Y5.5 判据只排除判据 A：列锚点成员 / 仅邻近 / 无关行都**不**被排除"
              "（相邻表格的真实标题不得因邻近被拒，§三.3）")

    # ── Y6：目录来源行本身不得进正文树 ──
    titles = _titles(result)
    check("目录" not in titles,
          "Y6.1 目录页标题行不进正文树")
    toc_line_texts = [item[2] for item in _y_toc_lines()[1:]]
    check(not any(text in titles for text in toc_line_texts),
          "Y6.2 目录**来源行**（标题 + 点线填充 + 页标签的整行）不进正文树")
    toc_page_number = toc_pages[0]
    toc_candidates = [c for c in cands if c.page_number == toc_page_number]
    check(toc_candidates and all("toc_page_line" in c.rejected
                                 for c in toc_candidates),
          "Y6.3 目录页上的行全部以 `toc_page_line` 显式拒绝（不是静默丢弃）")

    # ── Y7：同名标题分属两章 —— 由来源路径与坐标区分 ──
    if len(subs) == 2:
        anchors = sorted(n.source_anchor[:2] for n in subs)
        check(len(set(anchors)) == 2,
              f"Y7.1 两个同名节点锚在不同的真实行上：{anchors}")
        check(len({n.structural_path for n in subs}) == 2
              and len({n.node_id for n in subs}) == 2,
              f"Y7.2 它们由**完整来源路径**区分："
              f"{[n.structural_path for n in subs]}")
        ids = []
        for node in subs:
            payload = _y_payload(result, *node.source_anchor[:2])
            entry = (payload or [{}])[0]
            ids.append((entry.get("toc_source_id"), entry.get("physical_page")))
        check(len(set(ids)) == 2 and all(i[0] for i in ids),
              f"Y7.3 两条 landing 的 `toc_source_id` 与目标物理页各不相同：{ids}")

    # ── Y8：真实正文 landing 的来源锚点 / 父节点 / 层级 / 路径可复算 ──
    if root is not None:
        page_number, line_index = root.source_anchor[:2]
        line = layout.line_at(page_number, line_index)
        check(line is not None and tight(line.text) == tight(_Y_ROOT),
              f"Y8.1 节点来源锚点指向真实 `LayoutLine`："
              f"{(page_number, line_index)} -> {None if line is None else line.text!r}")
        payload = _y_payload(result, page_number, line_index)
        check(payload is not None and payload[0].get("kind") == "toc",
              f"Y8.2 候选审计持久化了目录项 landing 载荷（可独立重算）：{payload}")
        if payload is not None:
            entry = payload[0]
            check(entry.get("declared_depth") == root.level,
                  f"Y8.3 声明深度与节点层级一致："
                  f"{entry.get('declared_depth')} vs {root.level}")
            # 复核方**自己**用真实版式重建来源对象身份（不采信生产侧结论）。
            rebuilt = OB.TocSource.create(
                layout=layout, page_number=entry["source_page"],
                line_index=entry["source_line"], char_start=entry["char_start"],
                char_end=entry["char_end"],
                declared_page_label=entry["declared_page_label"])
            rebuilt.verify_against_layout(layout)
            source_line = layout.line_at(entry["source_page"], entry["source_line"])
            check(rebuilt.toc_source_id == entry["toc_source_id"]
                  and source_line is not None
                  and rebuilt.entry_text == source_line.text,
                  f"Y8.4 复核方从载荷重算出的 `TocSource` 身份与生产侧一致，且它的来源"
                  f"行就是目录页上的真实行：{rebuilt.toc_source_id} == "
                  f"{entry.get('toc_source_id')}")
            check(entry.get("landing_page") == page_number
                  and entry.get("landing_line") == line_index,
                  "Y8.5 landing 记录的目标行就是节点的真实来源行")

    # ── Y9：六类对账桶逐条可分 ──
    rec = OB.toc_body_reconciliation(result, layout)
    resolved = {(item["title"], item["physical_page"])
                for item in rec["toc_body_resolved"]}
    unresolved = {(item["title"], item["declared_page_label"])
                  for item in rec["toc_target_unresolved"]}
    body_unassigned_toc = {item["title"] for item in rec["toc_body_unassigned"]}
    check(resolved == {(_Y_ROOT, 4), (_Y_SUB, 4), (_Y_SUB, 6), (_Y_ROOT2, 6)},
          f"Y9.1 **反例②**：同一份输入里正常生成 `OutlineNode` 的目录项进 "
          f"`toc_body_resolved`（四段闭合、**不阻断**）：{sorted(resolved)}")
    check(unresolved == {(_Y_CELL, "4"), (_Y_DECOY, "3")},
          f"Y9.2 **反例③④**：`toc_target_unresolved` = 声明页标签无法唯一映射 / 映射页上"
          f"没有文本一致的正文行 / 该页上只有同名的**普通表格内容**行 —— "
          f"**不得**与 `toc_body_unassigned` 合并：{sorted(unresolved)}")
    check(body_unassigned_toc == {_Y_LONG_TITLE},
          f"Y9.3 **反例①**：`toc_body_unassigned` = 真实来源 + 页标签唯一闭合 + 合格正文"
          f" landing 行、但**没有**对应 `OutlineNode`：{sorted(body_unassigned_toc)}")
    check(_Y_LONG_TITLE not in _titles(result),
          f"Y9.3b **不得为清零该状态而自动把正文行加入树**：`{_Y_LONG_TITLE[:12]}…` 仍然"
          f"不在正式节点集合里（只有既有标题资格规则本身足以采纳时才能生成节点）")
    check(rec["bookmark_navigation_only"] == [],
          "Y9.4 本夹具无书签 ⇒ `bookmark_navigation_only` 为空（它不是收容桶）")
    unassigned = {(item["page_number"], item["line_index"])
                  for item in rec["body_heading_unassigned"]}
    check(cell_cand is not None
          and (cell_cand.page_number, cell_cand.line_index) in unassigned,
          "Y9.5 `body_heading_unassigned` 收的是**广义正文候选**（含被拒的表格行标签），"
          "与目录来源行是不同对象")
    # ── Y9.7：`toc_body_unassigned` 必须逐条保留**可复核身份** ──
    long_item = next((i for i in rec["toc_body_unassigned"]
                      if i["title"] == _Y_LONG_TITLE), None)
    check(long_item is not None and long_item.get("blocking") is True,
          f"Y9.7 `toc_body_unassigned` 逐条自带阻断标记（`blocking=True`）："
          f"{None if long_item is None else long_item.get('blocking')}")
    required = ("toc_source_id", "toc_source_locator", "toc_builder_version",
                "char_start", "char_end", "entry_text", "declared_page_label",
                "physical_page", "landing_line", "landing_text",
                "landing_candidate_rejections", "unassigned_span_ids",
                "toc_source_rebuildable", "action_required")
    missing = [k for k in required
               if long_item is None or k not in long_item]
    check(not missing,
          f"Y9.8 它带齐**来源身份 / 声明页标签 / 物理页 / landing 坐标与正文文本 / "
          f"候选拒绝原因 / 可关联未归属 span**：缺 {sorted(missing)}")
    check(long_item is not None
          and long_item.get("toc_source_rebuildable") is True
          and long_item.get("physical_page") == 7
          and _Y_LONG_TITLE in (long_item.get("landing_text") or ""),
          f"Y9.9 来源身份**可由复核侧从同一份持久化版式重建**，且物理页 / landing 文本"
          f"与真实行一致："
          f"（rebuildable={None if long_item is None else long_item.get('toc_source_rebuildable')}、"
          f"physical_page={None if long_item is None else long_item.get('physical_page')}）")
    if long_item is not None and long_item.get("toc_source_id"):
        rebuilt = OB.TocSource.create(
            layout=layout, page_number=long_item["source_page"],
            line_index=long_item["source_line"], char_start=long_item["char_start"],
            char_end=long_item["char_end"],
            declared_page_label=long_item["declared_page_label"])
        rebuilt.verify_against_layout(layout)
        check(rebuilt.toc_source_id == long_item["toc_source_id"]
              and rebuilt.toc_source_locator == long_item["toc_source_locator"],
              f"Y9.10 **复核侧自己**从持久化版式重建出的 `TocSource` 身份与对账输出一致"
              f"（不采信生产侧结论）：{rebuilt.toc_source_id} == "
              f"{long_item['toc_source_id']}")
    check("toc_source_only" not in rec,
          "Y9.11 `tocr-1` 的 `toc_source_only` 桶名不再存在：同一份输入状态现在按 "
          "`toc_body_unassigned` **阻断**记账（fail-open 已废除）")
    cell_reason = next((item["why"] for item in rec["toc_target_unresolved"]
                        if item["title"] == _Y_CELL), "")
    check(OB.TOC_LANDING_TABLE_CELL_REASON in cell_reason,
          f"Y9.6 表格内容那一类未解析项带**确定性判据码**：{cell_reason}")


def _test_reconciliation_names_legacy_miss() -> None:
    """§四 P1-B(7)：`toc_body_unassigned` / `body_heading_unassigned` 必须**分开**记账。

    旧实现把这两类东西写成同一句"真标题漏收"。这里用同一份夹具证明：同一条目录项在
    "目标行存在但未被采纳"与"目标未解析"两种状态下分别落进**不同**的桶，任何一桶都
    不冒充另一桶。
    """
    layout, result, _cands = _y_scene()
    rec = OB.toc_body_reconciliation(result, layout)
    keys = set(rec)
    check(keys == set(OB.TOC_BODY_RECONCILIATION_BUCKETS)
          and keys == {"toc_body_resolved", "toc_body_unassigned",
                       "toc_target_unresolved", "bookmark_navigation_only",
                       "body_heading_unassigned"},
          f"Y10.1 六类桶的名字与本轮状态机逐字一致，且与桶清单常量逐字符相等："
          f"{sorted(keys)}")
    check("toc_source_only" not in keys,
          "Y10.1b `tocr-1` 的 `toc_source_only` 桶名已被**废除**（不是换标签："
          "同一份输入状态在 `tocr-2` 下是阻断项）")
    blocking = {item["title"] for item in rec["toc_body_unassigned"]}
    unresolved_titles = {item["title"] for item in rec["toc_target_unresolved"]}
    check(not (blocking & unresolved_titles),
          f"Y10.2 同一目录项不会同时落进两类桶（`toc_target_unresolved` 表示**没有形成"
          f"有效正文 landing**，不得与 `toc_body_unassigned` 合并）："
          f"{blocking} / {unresolved_titles}")
    # 与 M6b 同一条约定：`why` 里出现「真标题漏收」的**唯一**合法位置是**显式否定**
    # （"它**不是**「真标题漏收」"）。断言"这个词一次都不许出现"会把否定句一起判失败，
    # 断言"可以随便出现"又等于没测；这里断言的是它**从不被当成结论使用**。
    whys = [item.get("why", "") for bucket in rec.values() for item in bucket]
    asserted = [w for w in whys
                if "真标题漏收" in w and "不是" not in w.split("真标题漏收")[0][-12:]]
    check(not asserted,
          f"Y10.3 对账输出里「真标题漏收」只作为**被否定**的旧措辞出现，从不被当成"
          f"结论：{asserted}")
    check(OB.toc_body_reconciliation(result, None) == {
        "toc_body_resolved": [], "toc_body_unassigned": [],
        "toc_target_unresolved": [], "bookmark_navigation_only": [],
        "body_heading_unassigned": []},
        "Y10.4 缺 `PageLayout` 时六类桶全空（fail-closed，不猜）")


# ---------------------------------------------------------------------------
# §四 P1-B(2)：目录项声明的**页目标**未闭合 ≠ 该正文标题漏收
# ---------------------------------------------------------------------------

#: 正文里**真实存在**且已进树的一级标题（同一条标题在目录里有两条来源行）。
_X2_ROOT = "某前言事项"
#: 目录项指向的目标在正文里**根本不存在**——必须留在未解析且**不得**带同名节点事实。
_X2_ABSENT = "某不存在的章节"
_X2_LEADER = " .........."


def _pages_unresolved_toc_with_existing_node() -> list:
    """目录页上有三条来源行，只有第一条能闭合到物理页。

    - 第 1 条：`某前言事项` 声明页标签 "3" → 物理页 4 → 文本一致的真实正文行 → 正式节点；
    - 第 2 条：**同一条标题**声明页标签 "99"（版式上不存在这个页标签）→ 页目标**未闭合**，
      而树上**另有**同名节点 → 必须留在 `toc_target_unresolved` 并逐条带出该节点；
    - 第 3 条：`某不存在的章节` 声明页标签 "99" → 未闭合，且树上**没有**同名节点 →
      不得凭空给出任何"已进树"的字段（fail-closed，不猜）。

    第 2 条是"目录项的页目标没闭合"与"这个正文标题没进树"必须被分开陈述的**反例锚点**：
    旧措辞只写"目标未解析"，读产物的人会把它读成「真标题漏收」。
    """
    return [
        ([(120.0, 120.0, "某公司 2026 年年度报告", 20.0, False)], None),
        (_section_head() + _filler("A"), "1"),
        ([(72.0, 72.0, "目录", 16.0, False),
          (72.0, 100.0, _X2_ROOT + _X2_LEADER + " 3", 11.0, False),
          (72.0, 122.0, _X2_ROOT + _X2_LEADER + " 99", 11.0, False),
          (72.0, 144.0, _X2_ABSENT + _X2_LEADER + " 99", 11.0, False)]
         + _filler("B"), "2"),
        (_filler("C")
         + [(72.0, 240.0, _X2_ROOT, 11.0, False)]
         + H._body(280.0, tag="R", long_line=True), "3"),
        (_filler("D"), "4"),
        (_filler("E"), "5"),
        (_filler("F"), "6"),
    ]


def _test_unresolved_page_target_is_not_missing_heading() -> None:
    """§四 P1-B(2)：`toc_target_unresolved` 说的是**这条目录项声明的页目标**没闭合。

    它**不是**"这个正文标题漏收"。两者若不分开陈述，读产物的人会把"目标未解析 N 条"
    读成"N 个正文标题没进树"——这正是 P1-B 要消灭的混淆。本组用三条真实的目录来源行
    把这条件钉成可执行断言。
    """
    layout, result, _cands = _build(_pages_unresolved_toc_with_existing_node())
    recon = OB.toc_body_reconciliation(result, layout)

    node = _y_node(result, _X2_ROOT)
    check(node is not None,
          f"Z1.1 夹具自检：`{_X2_ROOT}` 凭真实目录项 landing 进树（否则本组无从证明）")
    if node is None:
        return

    unresolved = {(i["title"], i["declared_page_label"]): i
                  for i in recon["toc_target_unresolved"]}
    check(set(unresolved) == {(_X2_ROOT, "99"), (_X2_ABSENT, "99")},
          f"Z1.2 页标签无法映射的两条目录来源行都留在未解析（实际 {sorted(unresolved)}）")

    # ── Z2：页目标没闭合的那条**照样**留在未解析桶，不因为同名节点被改判 ──
    same = unresolved.get((_X2_ROOT, "99"))
    resolved_titles = {i["title"] for i in recon["toc_body_resolved"]}
    blocking_titles = {i["title"] for i in recon["toc_body_unassigned"]}
    check(same is not None
          and _X2_ROOT not in blocking_titles,
          f"Z2.1 **反例⑥**：同名正文节点**不**给目录项任何落地身份 —— 它既不在 "
          f"`toc_body_resolved`，也不在 `toc_body_unassigned`"
          f"（blocking={sorted(blocking_titles)}）：同名节点位于其他页 / 其他章节时，"
          f"不得冒充本 TOC landing 已闭合，反向也不得把未闭合的页目标说成「漏收」")
    check(same is not None and (same.get("same_title_node_ids") or []) == [node.node_id],
          f"Z2.2 逐条带出**已进树**的同名节点 id："
          f"{None if same is None else same.get('same_title_node_ids')} / 期望 {node.node_id}")
    check(same is not None
          and (same.get("same_title_node_paths") or []) == [list(node.structural_path)],
          f"Z2.3 同时带出它的结构路径（同名也要能定位到**哪一个**节点）："
          f"{None if same is None else same.get('same_title_node_paths')}")
    why = (same or {}).get("why", "")
    check("不得" in why and "漏收" in why and "页目标" in why,
          f"Z2.4 措辞必须写明『本条只说明**目录项声明的页目标**未闭合、不得据此宣称该"
          f"正文标题漏收』：{why}")

    # ── Z3：树上没有同名节点时**不得**凭空给出"已进树"字段（fail-closed） ──
    absent = unresolved.get((_X2_ABSENT, "99"))
    check(absent is not None
          and "same_title_node_ids" not in absent
          and "same_title_node_paths" not in absent,
          f"Z3.1 无同名节点⇒不得给出任何『已进树』字段："
          f"{None if absent is None else sorted(absent)}")
    check(absent is not None and "页目标" in absent.get("why", ""),
          f"Z3.2 未解析的措辞锚在**目录项声明的页目标**上，不与正文标题混为一谈")

    # ── Z4：`toc_body_resolved` 只认**页标签也成立**的那一条来源行 ──
    closed = [i for i in recon["toc_body_resolved"] if i["title"] == _X2_ROOT]
    check(len(closed) == 1 and closed[0]["declared_page_label"] == "3"
          and closed[0]["physical_page"] == node.source_anchor[0]
          and closed[0]["node_id"] == node.node_id,
          f"Z4.1 只有声明页标签「3」的那条来源行四段闭合，且指向同一个节点："
          f"{[(i['declared_page_label'], i['physical_page'], i['node_id']) for i in closed]}")
    y_ok = all("真标题漏收" not in i.get("why", "")
               or "不是" in i["why"].split("真标题漏收")[0][-12:]
               for bucket in recon.values() for i in bucket)
    check(y_ok, "Z4.2 未解析项的措辞里「真标题漏收」只作**被否定**的旧措辞出现")

    # ── Z5：生产侧与独立验收侧必须从**同一份持久化 PageLayout** 重算出相同状态 ──
    # "两侧各自从真实版式重算"不能只是口头承诺：把同一份 `PageLayout` 独立喂给对账
    # 函数（生产侧构建结果 + 显式传入的版式），逐桶逐字段必须完全相等；再独立重建
    # `TocSource` 身份，确认对账输出里的来源身份不是自报字符串。
    again = OB.toc_body_reconciliation(result, layout)
    check(again == recon,
          "Z5.1 同一份持久化版式、同一份构建结果 ⇒ 对账输出**逐字段可复算**"
          "（两侧不是两条会分叉的代码路径）")
    check(isinstance(recon.get("toc_body_resolved"), list)
          and isinstance(recon.get("toc_body_unassigned"), list)
          and isinstance(recon.get("toc_target_unresolved"), list)
          and isinstance(recon.get("bookmark_navigation_only"), list)
          and isinstance(recon.get("body_heading_unassigned"), list),
          "Z5.2 六个桶都是列表（消费方按显式桶名取，不靠 `len()` 猜形状）")
    for item in recon["toc_body_resolved"]:
        check(item.get("node_id") and item.get("node_path"),
              f"Z5.3 `toc_body_resolved` 逐条带节点身份（可对账到正式树）："
              f"{item.get('node_id')}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _run(test) -> None:
    """执行单个测试组；组内抛出的异常记为一条 FAIL，而不是中断整份模块。"""
    try:
        test()
    except Exception as error:  # noqa: BLE001
        check(False, f"{test.__name__} 抛出 {type(error).__name__}: {error}")


def main() -> dict:
    _results["passed"] = _results["failed"] = _results["skipped"] = 0
    _results["details"] = []
    for test in (_test_far_apart_peer_only,
                 _test_dense_share_table_rows,
                 _test_equity_table_rows,
                 _test_fixed_asset_table_rows,
                 _test_label_band_rows,
                 _test_same_text_heading_and_cell,
                 _test_real_subheadings_are_kept,
                 _test_insufficient_evidence_stays_visible,
                 _test_no_regression_invariants,
                 _test_acceptor_finds_handmade_table_fragment,
                 _test_acceptor_blocks_inside_table_overrides,
                 _test_acceptor_fails_closed,
                 _test_centered_cell_prose_rejected,
                 _test_lifted_cell_rejected,
                 _test_heading_after_table_restored,
                 _test_valueless_row_label_rejected,
                 _test_bookmark_backed_inside_table_rejected,
                 _test_label_band_closed_by_one_table,
                 _test_two_style_evidence_never_overrides,
                 _test_self_reported_source_without_object,
                 _test_toc_landing_wrong_page_rejected,
                 _test_bookmark_landing_wrong_page_rejected,
                 _test_verified_toc_landing_allows_override,
                 _test_bookmark_payload_never_reviews_available,
                 _test_override_mismatch_is_blocking,
                 _test_version_closure_hq4_trg3,
                 _test_unsafe_override_regression_anchors,
                 _test_toc_source_body_landing_separation,
                 _test_reconciliation_names_legacy_miss,
                 _test_unresolved_page_target_is_not_missing_heading):
        _run(test)
    return _results


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=1))

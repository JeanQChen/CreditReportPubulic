"""Eval: M930-3 —— **读取计划**（`harness/read_plan.py`，`rpo-1`）与 **有界续读**（`tim-2`）。

用法: python -X utf8 -m evals.test_m930_3_read_plan

本批修的是**消费侧**断点，不是导航：读集的**集合**一字不改，改的只是「先读谁」与
「读满一次算不算读完」。这个模块把两件事钉死：

1. **读取计划只做置换，且依据公司无关。**
   - 排序量只有三个既有结构量：逐节点**自有准入正文字符数**、Contract 键的**完整标签段**
     命中数、**文档序**。没有词表、公司名、页码、表号或答案关键词。
   - **不因标题措辞降级**：标题里写「概述」而自有正文 145 字的节点，仍排在全部短残句与
     空容器之前（同簇里另放一个「业务味」标题但自有正文 4 字的节点作反例）。
   - 输出与入参**同集合**；`body_measure_available` 为假时**不重排**，如实记
     `body_measure_unavailable`。
   - `ReadPlan` 上**没有任何支持 / 充分性字段**：它只排序，不得被读成「这一栏已获支持」，
     也不得被读成「这一栏已完整」。

2. **复用键必须区分「同一集合、不同顺序」。**
   消费侧按 `node_ids` 顺序读到 `max_spans` 即停，因此顺序是**语义**的一部分。
   键再加三轴（计划身份 / 两个上限 / 续读位），任一轴不同即不复用。

3. **续读（`tim-2`）不越顶、不重不漏、fail-closed。**
   首轮入参与 `v7` 逐字相同（不带 `span_cursor`）；游标只能落在**本次读集内**且必须
   严格前进；越界 / 错节点 / 与 `evidence_ids` 同用一律 `INVALID_ARGUMENTS`。
   `over_max_spans` 条目如实交出续读读数；**不得**因为看到它就盲目重调，也**不得**
   把它当成「被挡下的具名候选」。

合成树部分只测机制（真实 `DocumentOutline` + 真实 `NavigationIndex` + 真实节点 id），
工具部分只在真文档上跑（样本或 Evidence 库缺失时如实 skip，不伪造通过）。
不调 LLM、不联网、不写任何库。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import navigation as NAV          # noqa: E402
from document_structure import schema as S                # noqa: E402
from document_structure import versions as V              # noqa: E402
from evaluation import business_material_readback as RB   # noqa: E402
from harness import read_plan as RPO                      # noqa: E402
from harness import source_manifest as SM                 # noqa: E402
from harness import topic_runtime as TR                   # noqa: E402
from harness import tree_tools as TT                      # noqa: E402
from tools import contracts as C                          # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "data/samples/300750/announcements"
EVIDENCE_DB = REPO / "data/evidence.db"
#: 工具侧测试用的真文档（一份即可：续读是位置机制，与哪一份无关）。
TOOL_DOC = "NDSD_2025_year"

#: 合成树：路径 → (自有准入正文字符数, 该节点的 span 数)。行号即文档顺序。
#:
#: 形状刻意做成「实质业务分支排在靠后、模板勾选与短残句排在前」——正是本批要修的那个
#: 排列。数字只用来区分「一节自带多少字」，不来自任何答案。
_SYNTH: tuple[tuple[tuple[str, ...], int, int], ...] = (
    (("四、主营业务分析",), 8, 0),                       # 容器（自有 8 字模板行）
    (("四、主营业务分析", "（5）营业成本构成"), 4, 1),      # 模板勾选（短残句）
    (("四、主营业务分析", "（6）公司实物销售收入是否大于劳务收入"), 72, 1),
    (("四、主营业务分析", "（7）研发投入"), 8, 1),
    (("四、主营业务分析", "1、概述"), 145, 1),             # 「概述」也有实质正文：不得被降级
    (("四、主营业务分析", "2、主要产品及其用途"), 44, 1),
    (("四、主营业务分析", "（1）动力业务"), 1321, 2),       # ← 原先被挤死的并列业务分支
    (("四、主营业务分析", "（2）储能业务"), 869, 1),
    (("四、主营业务分析", "（3）新兴领域"), 1436, 1),
    (("四、主营业务分析", "（4）供应链及产能"), 317, 1),
    (("四、主营业务分析", "1、主要业务"), 441, 1),
    (("四、主营业务分析", "3、费用"), 0, 1),               # 空容器
    # 同簇反例：标题「业务味」，自有正文只有 4 字 —— 按量落短残句档，按标题则会被提前。
    (("四、主营业务分析", "（8）主要业务变化情况"), 4, 1),
)

#: Contract 侧键（合成，只用来测「命中只影响次序」）。
_TOPIC_NAV_KEYS = ("主营业务", "主要业务")
_TOPIC_PARENT_KEYS = ()
#: 只让 `1、主要业务` 一个节点完整标签段命中（其余节点命中为 0）。
_SEGMENT_KEYS = ("主要业务",)

#: 首轮一次能消费的 span 位置数（与演示现场同值，只作批大小，不作充分性阈值）。
_MAX_SPANS = 6

_SESSIONS: dict = {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _synth_tree(*, with_body_measure: bool = True):
    """合成真实 `DocumentOutline` + 真实 `NavigationIndex`；返回 `(index, {路径: node_id})`。"""
    loc = S.derive_document_outline_locator(
        page_layout_id="pl-readplan00000001", document_id="DOC-READ-PLAN",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)

    def _anchor(line: int):
        y = 10.0 + line * 12.0
        return (1, line, (10.0, y, 200.0, y + 10.0))

    node_id_of = {
        path: S.derive_node_id(document_outline_locator=loc, structural_path=path,
                               source_anchor=_anchor(i), title=path[-1])
        for i, (path, _body, _spans) in enumerate(_SYNTH)}
    nodes = []
    siblings: dict[tuple[str, ...], int] = {}
    for i, (path, _body, _spans) in enumerate(_SYNTH):
        parent_key = path[:-1]
        ordinal = siblings.get(parent_key, 0)
        siblings[parent_key] = ordinal + 1
        nodes.append(S.OutlineNode.create(
            document_outline_locator=loc,
            parent_id=None if not parent_key else node_id_of[parent_key],
            title=path[-1], title_normalized=path[-1], structural_path=path,
            source_anchor=_anchor(i),
            child_ids=tuple(node_id_of[p] for p, _b, _s in _SYNTH if p[:-1] == path),
            ordinal=ordinal))
    outline = S.DocumentOutline.create(
        document_id="DOC-READ-PLAN", document_version="sha256-readplan00000",
        page_layout_id="pl-readplan00000001", nodes=tuple(nodes))

    by_id = {n.structural_path: n.node_id for n in outline.nodes}
    # 简介**必须逐字符来自该节点**（抽取式）：这里只给标题文本本身，安全且与排序无关。
    synopses = tuple(
        S.NavigationSynopsis.available(node_id=n.node_id, snippets=(
            S.SynopsisSnippet(span_id="os-" + _sha("syn:" + n.node_id)[:16],
                              snippet_index=0, char_start=0,
                              char_end=len(n.title_normalized),
                              text=n.title_normalized),))
        for n in outline.nodes)

    span_node_ids: list[str] = []
    for path, _body, spans in _SYNTH:
        span_node_ids.extend([by_id[path]] * spans)
    body_counts = {by_id[path]: body for path, body, _s in _SYNTH} \
        if with_body_measure else None
    index = NAV.NavigationIndex(outline, synopses, span_node_ids=span_node_ids,
                                body_char_counts=body_counts)
    return index, by_id


def _container_children(by_id) -> tuple[str, ...]:
    root = by_id[("四、主营业务分析",)]
    return tuple(path for path, _b, _s in _SYNTH if path != ("四、主营业务分析",)), root


def _plan(index, node_ids):
    return RPO.plan_read_order(index, node_ids, topic_nav_keys=_TOPIC_NAV_KEYS,
                               topic_parent_keys=_TOPIC_PARENT_KEYS)


# ---------------------------------------------------------------------------
# 1. 读取计划：只置换、按量排序、不因标题降级
# ---------------------------------------------------------------------------

def _check_plan_order(check, details) -> None:
    index, by_id = _synth_tree()
    paths, _root = _container_children(by_id)
    # 导航原序**刻意**把模板勾选与短残句排在最前（本批要修的排列）。
    nav_order = tuple(by_id[p] for p in (
        ("四、主营业务分析", "（5）营业成本构成"),
        ("四、主营业务分析", "（6）公司实物销售收入是否大于劳务收入"),
        ("四、主营业务分析", "（7）研发投入"),
        ("四、主营业务分析", "2、主要产品及其用途"),
        ("四、主营业务分析", "1、概述"),
        ("四、主营业务分析", "3、费用"),
        ("四、主营业务分析", "（1）动力业务"),
        ("四、主营业务分析", "（2）储能业务"),
        ("四、主营业务分析", "（3）新兴领域"),
        ("四、主营业务分析", "（4）供应链及产能"),
        ("四、主营业务分析", "1、主要业务"),
        ("四、主营业务分析", "（8）主要业务变化情况"),
    ))
    assert set(nav_order) == {by_id[p] for p in paths}, "合成读集必须覆盖全部子节点"
    plan = _plan(index, nav_order)

    check(plan.status == "ordered" and plan.reordered,
          "有正文量且次序可改时，计划真的重排（status=ordered）")
    check(set(plan.ordered_node_ids) == set(nav_order)
          and len(plan.ordered_node_ids) == len(nav_order),
          "计划与入参读集**同集合同基数**（只做置换，一字不增不减）")

    first6 = plan.ordered_node_ids[:_MAX_SPANS]
    substantive = {by_id[p] for p, body, _s in _SYNTH
                   if body >= RPO.READ_PLAN_MIN_SUBSTANTIVE_BODY}
    check(substantive.issubset(set(first6)),
          f"全部 {len(substantive)} 个实质业务小节都进入首轮 {_MAX_SPANS} 个读取名额"
          f"（并列分支不再被模板勾选挤死）")
    check(not (set(first6) - substantive),
          "首轮名额没有被任何短残句 / 空容器占掉")

    # 原先被挤死的三个并列业务分支：现在都在首轮。
    for title in ("（1）动力业务", "（2）储能业务", "（3）新兴领域"):
        check(by_id[("四、主营业务分析", title)] in first6,
              f"{title} 取得首轮读取机会")

    # 「概述」不得因措辞被降级。
    overview = by_id[("四、主营业务分析", "1、概述")]
    row = next(r for r in plan.rows if r.node_id == overview)
    check(row.substance_tier == 0 and row.body_chars == 145,
          "标题写「概述」而自有正文 145 字的节点仍是**实质档**（不因措辞降级）")
    check(overview in first6, "该「概述」落在首轮名额内（不被误排除）")

    # 同簇反例：标题像业务、正文只有 4 字 → 按量排到实质档之后。
    fake = by_id[("四、主营业务分析", "（8）主要业务变化情况")]
    fake_row = next(r for r in plan.rows if r.node_id == fake)
    check(fake_row.substance_tier == 1,
          "标题像业务但自有正文 4 字的节点落**短残句档**（判据是按量，不是按标题）")
    check(fake not in first6, "该节点不因标题措辞抢到首轮名额")
    check(fake_row.rank > max(r.rank for r in plan.rows
                             if r.substance_tier == 0),
          "它的名次排在全部实质业务小节**之后**")

    # 容器排在最后。
    container = by_id[("四、主营业务分析", "3、费用")]
    check(next(r for r in plan.rows if r.node_id == container).substance_tier == 2,
          "自有正文为 0 的容器落最后一档")

    # 逐档内按量降序。
    tiers = [r.substance_tier for r in plan.rows]
    check(tiers == sorted(tiers), "逐行按实质档升序（tier 0 全在最前）")
    for tier in (0, 1, 2):
        bodies = [r.body_chars for r in plan.rows if r.substance_tier == tier]
        check(bodies == sorted(bodies, reverse=True),
              f"第 {tier} 档内部按自有正文量降序")

    check(plan.rule_version == RPO.READ_PLAN_RULE_VERSION,
          "计划带规则版本（换规则即换版本）")
    check(RPO.READ_PLAN_MIN_SUBSTANTIVE_BODY == NAV._SUPPLEMENT_MIN_BODY_CHARS,
          "实质档阈值**逐字复用**既有 `anp-7`/`anp-9` 判据的那个常量，不另定数字")

    # 计划身份随依据变化（不是常量）。
    index_b, by_id_b = _synth_tree()
    plan_same = _plan(index_b, nav_order)
    check(plan_same.plan_id == plan.plan_id,
          "同一读集 + 同一索引 ⇒ 同一个 plan_id（同 topic 各栏目可共用一次真实结果）")
    check(plan_same.ordered_node_ids == plan.ordered_node_ids,
          "同一读集 + 同一索引 ⇒ 同一个次序（纯函数）")


def _check_plan_set_and_status(check, details) -> None:
    index, by_id = _synth_tree()
    paths, _root = _container_children(by_id)
    ids = [by_id[p] for p in paths]

    shuffled = list(reversed(ids))
    plan_a = _plan(index, ids)
    plan_b = _plan(index, shuffled)
    check(plan_a.ordered_node_ids == plan_b.ordered_node_ids,
          "**同一集合、不同入参顺序**得到同一个计划次序（计划是集合的纯函数）")
    check(plan_a.plan_id == plan_b.plan_id,
          "同一集合、不同入参顺序得到同一个 plan_id（换顺序不换计划身份）")

    one = _plan(index, ids[:1])
    check(one.status == "not_needed" and one.ordered_node_ids == tuple(ids[:1]),
          "只有一个节点的读集记 `not_needed`，次序原样交回")
    empty = _plan(index, ())
    check(empty.status == "not_needed" and empty.ordered_node_ids == (),
          "空读集记 `not_needed`（不制造节点）")

    blind, by_id_blind = _synth_tree(with_body_measure=False)
    blind_ids = [by_id_blind[p] for p in paths]
    nav_order = tuple(blind_ids)
    blind_plan = _plan(blind, nav_order)
    check(blind_plan.status == "body_measure_unavailable",
          "没有逐节点正文量时如实记 `body_measure_unavailable`（不猜）")
    check(blind_plan.ordered_node_ids == nav_order and not blind_plan.reordered,
          "该状态下**不重排**：次序逐字交回导航原序")

    try:
        _plan(index, ("on-not-on-this-tree",))
        check(False, "读集含未定位节点应抛错")
    except NAV.NavigationError:
        check(True, "读集含不在树上的节点时 fail-closed（不得对未定位节点排序）")

    # 计划产物里**不得**出现任何支持 / 充分性词汇。
    payload = json.dumps(plan_a.to_dict(), ensure_ascii=False)
    forbidden = ("support", "sufficien", "complete", "coverage", "aspect_id",
                 "authority", "证明", "支持", "完整")
    hit = [w for w in forbidden if w in payload]
    check(not hit,
          f"`ReadPlan` 只排序：产物里没有任何支持/充分性/栏目词汇（命中 {hit}）")


def _check_relevance_is_tiebreak_only(check, details) -> None:
    """Contract 键只决定**同量同档**时先读谁，绝不改变档位、也不产生支持判定。"""
    loc = S.derive_document_outline_locator(
        page_layout_id="pl-readplan00000002", document_id="DOC-READ-PLAN-KEYS",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)
    paths = (("一、总述",), ("一、总述", "1、主要业务"), ("一、总述", "2、其他事项"))
    anchors = {p: (1, i, (10.0, 10.0 + i * 12.0, 200.0, 20.0 + i * 12.0))
               for i, p in enumerate(paths)}
    node_id_of = {p: S.derive_node_id(document_outline_locator=loc,
                                      structural_path=p, source_anchor=anchors[p],
                                      title=p[-1]) for p in paths}
    siblings: dict = {}
    nodes = []
    for p in paths:
        parent = p[:-1]
        ordinal = siblings.get(parent, 0)
        siblings[parent] = ordinal + 1
        nodes.append(S.OutlineNode.create(
            document_outline_locator=loc,
            parent_id=None if not parent else node_id_of[parent],
            title=p[-1], title_normalized=p[-1], structural_path=p,
            source_anchor=anchors[p],
            child_ids=tuple(node_id_of[q] for q in paths if q[:-1] == p),
            ordinal=ordinal))
    outline = S.DocumentOutline.create(
        document_id="DOC-READ-PLAN-KEYS", document_version="sha256-readplankeys00",
        page_layout_id="pl-readplan00000002", nodes=tuple(nodes))
    by_id = {n.structural_path: n.node_id for n in outline.nodes}
    synopses = tuple(
        S.NavigationSynopsis.available(node_id=n.node_id, snippets=(
            S.SynopsisSnippet(span_id="os-" + _sha("k:" + n.node_id)[:16],
                              snippet_index=0, char_start=0,
                              char_end=len(n.title_normalized),
                              text=n.title_normalized),))
        for n in outline.nodes)
    # 两节**自有正文量完全相同**（都是 200），只有标题不同。
    body_counts = {by_id[("一、总述", "1、主要业务")]: 200,
                   by_id[("一、总述", "2、其他事项")]: 200,
                   by_id[("一、总述",)]: 0}
    index = NAV.NavigationIndex(outline, synopses, body_char_counts=body_counts)

    # 文档序：命中键的节点**在后**。若无相关性平局规则，它会因文档序排后。
    ids = (by_id[("一、总述", "2、其他事项")],
           by_id[("一、总述", "1、主要业务")])
    plan = RPO.plan_read_order(index, ids, topic_nav_keys=_SEGMENT_KEYS)
    rows = {r.node_id: r for r in plan.rows}
    hit = by_id[("一、总述", "1、主要业务")]
    miss = by_id[("一、总述", "2、其他事项")]
    check(rows[hit].relevance_hits == 1 and rows[miss].relevance_hits == 0,
          "完整标签段命中数与现场一致（既有 `seg_v1` 判据，不另建词表）")
    check(plan.ordered_node_ids == (hit, miss),
          "同为实质档、同为 200 字时，命中 Contract 键的先读（相关性只作次序平局）")
    check(rows[hit].substance_tier == rows[miss].substance_tier == 0,
          "相关性**不改变档位**：两节同为实质档")

    no_keys = RPO.plan_read_order(index, ids)
    check(no_keys.ordered_node_ids == (hit, miss),
          "不给键时退回文档序（`1、主要业务` 在文档里靠前，故先读；相关性缺席不改变"
          "末一档平局规则、也不让次序变成随机或崩溃）")


def _check_topic_keys_are_topic_level(check, details) -> None:
    """计划键必须取 **topic 级**并集：否则同 topic 各栏目各算一个 `plan_id`，复用当场失效。"""
    class _Entry:
        def __init__(self, aspect_id, topic_id, nav_keys, parent_keys):
            self.aspect_id = aspect_id
            self.topic_id = topic_id
            self.nav_keys = nav_keys
            self.parent_keys = parent_keys

    class _Profile:
        def __init__(self, entries):
            self.entries = tuple(entries)

    profile = _Profile([
        _Entry("a1", "t_business", ("主营业务", "主要业务"), ("业务",)),
        _Entry("a2", "t_business", ("主要业务", "产品"), ("业务", "经营")),
        _Entry("a3", "t_other", ("主营业务", "另一主题"), ("别的键",)),
    ])
    nav_keys, parent_keys = RPO.topic_plan_keys(profile, "t_business")
    check(nav_keys == ("主营业务", "主要业务", "产品"),
          f"topic 级并集去重且按码点有序（得到 {nav_keys}）")
    check(parent_keys == ("业务", "经营"),
          f"父键同样取本 topic 并集（得到 {parent_keys}）")
    check("另一主题" not in nav_keys and "别的键" not in parent_keys,
          "别的 topic 的键**不得**混进本 topic 的键并集")
    check(RPO.topic_plan_keys(profile, "t_business")
          == RPO.topic_plan_keys(profile, "t_business"),
          "键并集是纯函数（同 profile 同 topic 稳定）")
    check(RPO.topic_plan_keys(profile, "t_absent") == ((), ()),
          "topic 不在 profile 里时交出空键集（不借用别的 topic 的键）")


# ---------------------------------------------------------------------------
# 2. 复用键：同一集合不同顺序不得误复用
# ---------------------------------------------------------------------------

def _check_reuse_key(check, details) -> None:
    index, by_id = _synth_tree()
    paths, _root = _container_children(by_id)
    ids = tuple(by_id[p] for p in paths)

    key = SM.SourceDocumentKey(company_id="c", document_id="d",
                               document_version="dv", evidence_set_version="es")
    plan_a = _plan(index, ids)
    plan_b = RPO.plan_read_order(
        index, tuple(reversed(ids)),
        topic_nav_keys=("不存在于任何标题的键",), topic_parent_keys=())
    check(plan_b.plan_id != plan_a.plan_id,
          "次序/依据变了，计划身份就变（plan_id 由排序结果与依据派生）")

    full = dict(plan_id=plan_a.plan_id, max_spans=6, max_chars_per_span=1200,
                cursor_key=None)
    base = TR.read_set_reuse_key(key, ids, **full)
    check(base == TR.read_set_reuse_key(key, ids, **full),
          "同一四轴键重复计算稳定")
    check(TR.read_set_reuse_key(key, ids, plan_id=plan_b.plan_id, max_spans=6,
                                max_chars_per_span=1200, cursor_key=None) != base,
          "**同一集合、不同消费计划** ⇒ 不同的复用键（不得误复用另一批 span）")
    check(TR.read_set_reuse_key(key, ids, plan_id=plan_a.plan_id, max_spans=5,
                                max_chars_per_span=1200, cursor_key=None) != base,
          "`max_spans` 不同 ⇒ 不复用（批大小是语义的一部分）")
    check(TR.read_set_reuse_key(key, ids, plan_id=plan_a.plan_id, max_spans=6,
                                max_chars_per_span=900, cursor_key=None) != base,
          "`max_chars_per_span` 不同 ⇒ 不复用")
    check(TR.read_set_reuse_key(
        key, ids, plan_id=plan_a.plan_id, max_spans=6, max_chars_per_span=1200,
        cursor_key="n1@0") != base,
          "续读位不同 ⇒ 不复用（首读与续读交出的是不同批 span）")
    check(TR.read_set_reuse_key(key, ids, plan_id=plan_a.plan_id, max_spans=6,
                                max_chars_per_span=1200,
                                cursor_key="n1@0") != TR.read_set_reuse_key(
        key, ids, plan_id=plan_a.plan_id, max_spans=6, max_chars_per_span=1200,
        cursor_key="n1@7"),
          "不同续读位之间也不互相复用")

    # 缺省四轴（单文档退化）保留改前语义，但那种形态下顺序**不入键**——
    # 这正是本批要消除的形态，故运行时**必须**带四轴；这里钉住的是「谁在用哪种形态」。
    legacy = TR.read_set_reuse_key(key, ids)
    check(legacy == TR.read_set_reuse_key(key, tuple(reversed(ids))),
          "缺省形态与改前逐字等价（只作单文档退化的兼容读视图）")

    src = Path(TR.__file__).read_text(encoding="utf-8")
    check("plan_id=plan.plan_id" in src
          and "max_spans=_max_spans" in src
          and "cursor_key=None" in src,
          "运行时**确实**按四轴调用（不是留着兼容形态当摆设）")
    check(src.count("read_set_cache.setdefault(") == 1,
          "复用缓存写入点唯一")


# ---------------------------------------------------------------------------
# 3. 续读读数与 fail-closed（纯函数面）
# ---------------------------------------------------------------------------

def _check_cursor_readout(check, details) -> None:
    class _R:
        def __init__(self, data):
            self.data = data

    none_read = TR._read_cursor_from_tool_result(_R({}))
    check(none_read == {"next_cursor": None, "unread_span_positions": 0,
                        "total_span_positions": 0, "resumed_at_span_position": 0},
          "没有截断条目时续读读数全零（**不得**从「没读到」反推一个位置）")

    other = TR._read_cursor_from_tool_result(_R({"skipped": [
        {"reason": "over_max_chars_per_span", "span_id": "os-1"},
        {"reason": "piece_parent_not_selected", "span_id": "os-2"}]}))
    check(other["next_cursor"] is None and other["unread_span_positions"] == 0,
          "非截断的 skipped 不产生续读位")

    got = TR._read_cursor_from_tool_result(_R({"skipped": [
        {"reason": "over_max_chars_per_span", "span_id": "os-1"},
        {"reason": "over_max_spans", "limit": 6,
         "next_cursor": {"node_id": "on-x", "span_index": 2},
         "unread_span_positions": 5, "total_span_positions": 11,
         "resumed_at_span_position": 0}]}))
    check(got["next_cursor"] == {"node_id": "on-x", "span_index": 2}
          and got["unread_span_positions"] == 5
          and got["total_span_positions"] == 11
          and got["resumed_at_span_position"] == 0,
          "截断条目上的四个续读读数逐字读出")

    malformed = TR._read_cursor_from_tool_result(_R({"skipped": [
        {"reason": "over_max_spans", "next_cursor": "not-a-dict",
         "unread_span_positions": None}]}))
    check(malformed["next_cursor"] is None and malformed["unread_span_positions"] == 0,
          "读数形状不对时退回零值（不猜、不崩、不编造位置）")

    # 截断条目**不是**「被挡下的具名候选」。
    over = [{"reason": "over_max_spans", "limit": 6, "next_cursor": None,
             "unread_span_positions": 0, "total_span_positions": 6,
             "resumed_at_span_position": 0}]
    check(TR._unattempted_from_skipped(over) == (),
          "`over_max_spans` 不带具名候选，**不得**被冒充成「预算未读的候选」")
    named = [{"reason": "over_max_chars_per_span", "span_id": "os-9"},
             {"reason": "piece_parent_not_selected", "span_id": "os-10",
              "evidence_id": "eb-1"}]
    check(TR._unattempted_from_skipped(named) == ("os-9", "os-10"),
          "真正被上界挡下的具名候选仍如实登记（反例证明上一条不是「不管什么都返回空」）")


# ---------------------------------------------------------------------------
# 4. 真工具入口：首轮逐字兼容 + 游标 fail-closed + 不重不漏
# ---------------------------------------------------------------------------

def _session(document_id: str):
    if document_id in _SESSIONS:
        return _SESSIONS[document_id]
    pdf = SAMPLES / f"{document_id}.pdf"
    if not pdf.exists() or not EVIDENCE_DB.exists():
        _SESSIONS[document_id] = None
        return None
    _index, _snapshot, _outline, _blocks, live = RB.build_index(REPO, pdf)
    session = TT.TreeInspectionSession(live)
    session._ensure()
    _SESSIONS[document_id] = session
    return session


def _tool_arguments(session, node_ids, **overrides) -> dict:
    spec = TT.TREE_INSPECT_SPEC
    props = set(spec.input_schema["properties"])
    args = {k: v for k, v in session.document_identity().items() if k in props}
    args.update({"need_id": "need-readplan", "aspect_id": "a", "topic_id": "t",
                 "node_ids": list(node_ids)})
    args.update(overrides)
    return {k: v for k, v in args.items() if k in props or k == "span_cursor"}


def _check_tool_cursor(check, details) -> None:
    session = _session(TOOL_DOC)
    if session is None:
        check(True, "真文档或 Evidence 库缺失：工具侧续读用例**如实 skip**")
        return

    # 找 span 最多的几个节点，凑一个够长的读集（只为位置机制，不关心是哪几节）。
    nodes = sorted((n for n in session.node_ids() if session._spans_by_node.get(n)),
                   key=lambda n: (-len(session._spans_by_node[n]), n))
    if len(nodes) < 2:
        check(True, "该文档上没有两个带 span 的节点：续读位置用例如实 skip")
        return
    ids = tuple(nodes[:4])
    positions = tuple((n, s) for n in ids for s in session._spans_by_node.get(n, ()))
    if len(positions) < 6:
        check(False, f"该读集只有 {len(positions)} 个 span 位置，无法截断成两轮："
                     f"续读位置用例**必须**能在真文档上跑（不得静默 skip）")
        return

    spec = TT.TREE_INSPECT_SPEC
    check("span_cursor" not in spec.input_schema.get("required", ()),
          "`span_cursor` 是**可选**入参：首轮调用与 `v7` 逐字相同")
    check(spec.version == TT.TREE_INSPECT_TOOL_VERSION,
          "ToolSpec 版本与公开常量一致")

    # 首轮：不带 cursor，arguments 里不得凭空多出该键。
    args = _tool_arguments(session, ids, max_spans=2)
    check("span_cursor" not in args, "首轮 arguments 里没有 `span_cursor` 键")
    check(C.validate_arguments(spec, args) == [],
          "首轮参数通过公共校验（新增可选字段不破坏旧调用）")
    first = session.inspect(args)

    over = [s for s in (first.data.get("skipped") or ())
            if s.get("reason") == "over_max_spans"]
    check(bool(over), "读满 `max_spans` 时如实登记 `over_max_spans`（截断不得被读成零命中）")
    if not over:
        return
    entry = over[0]
    check(set(entry) >= {"next_cursor", "unread_span_positions",
                         "total_span_positions", "resumed_at_span_position"},
          "截断条目带上四个续读读数（位置、未读数、总数、本轮起点）")
    check(entry.get("unread_span_positions", 0) > 0,
          "截断时未读位置数 > 0（真的还有没读到的）")

    cursor = entry["next_cursor"]
    if cursor is None:
        check(True, "本读集的 span 位置恰好被首轮读完：位置续读用例如实 skip")
        return
    check(cursor["node_id"] in set(ids),
          "续读位落在**本次读集内**的节点上")

    # 游标三种 fail-closed。
    bad_node = {"node_id": "on-not-in-this-read-set", "span_index": 0}
    r_bad = session.inspect(_tool_arguments(session, ids, max_spans=2,
                                            span_cursor=bad_node))
    check(r_bad.status != "SUCCESS" and r_bad.error_code == "INVALID_ARGUMENTS",
          "游标指向读集外的节点 ⇒ fail-closed（续读不得扩大读集）")
    r_range = session.inspect(_tool_arguments(
        session, ids, max_spans=2,
        span_cursor={"node_id": cursor["node_id"],
                     "span_index": len(positions) + 500}))
    check(r_range.status != "SUCCESS" and r_range.error_code == "INVALID_ARGUMENTS",
          "游标越界 ⇒ fail-closed（顺序位置必须真实存在）")
    r_mixed = C.validate_arguments(spec, dict(
        _tool_arguments(session, ids, max_spans=2, span_cursor=cursor),
        evidence_ids=["eb-x"]))
    check(bool(r_mixed),
          "游标与 `evidence_ids` 同用 ⇒ 参数层即被拒（互斥 selector 不被绕过）")

    # 续读真的往后读：两轮并集 = 位置集合，且不重不漏。
    second = session.inspect(_tool_arguments(session, ids, max_spans=2,
                                             span_cursor=cursor))
    check(second.status in ("SUCCESS", "PARTIAL", "EMPTY"),
          f"续读调用返回正常状态（得到 {second.status}）")
    read_ids: list[str] = []
    for result in (first, second):
        for cand in (result.data.get("candidates") or ()):
            sid = str(((cand.get("material") or {}).get("span") or {}).get("span_id")
                      or cand.get("span_id") or "")
            if sid:
                read_ids.append(sid)
    check(len(read_ids) == len(set(read_ids)),
          "首轮与续读**不重复**同一 span（同一位置不会被读两次）")
    check(not (set(read_ids) - {s for _n, s in positions}),
          "两轮读到的 span 全部落在本次读集的位置集合内（不越读）")
    resumed = TR._read_cursor_from_tool_result(second)
    check(resumed["resumed_at_span_position"] > 0
          or not (second.data.get("skipped") or ()),
          "续读轮的起点位置 > 0（证明它真的从截断处往后，而不是从头再来）")

    # 结构上界：轮数上界由工具自报的位置总数推出 ⇒ 有限步终止。
    total = entry.get("total_span_positions") or 0
    check(total >= len(positions),
          "工具自报的位置总数 ≥ 本读集的实际位置数（每轮至少前进一位 ⇒ 轮数有界）")


def _check_stop_reason_mapping(check, details) -> None:
    """停止原因 → 顶层 gap 原因：值必须落在闭集内，且四种止步**互不冒充**。"""
    r = TR._continuation_stop_gap_reason
    check(r("budget_exhausted") == "tree_budget_exhausted",
          "预算耗尽 ⇒ `tree_budget_exhausted`（不是「查过且没有」）")
    check(r("fatal_error") == "tree_structure_unavailable"
          and r("tool_retryable_error") == "tree_structure_unavailable",
          "工具失败（致命 / 可重试且重试已耗尽）⇒ `tree_structure_unavailable`")
    check(r("rounds_cap") == "tree_material_bounded_out"
          and r("cursor_not_advancing") == "tree_material_bounded_out"
          and r(None) == "tree_material_bounded_out",
          "轮数到顶 / 游标不前进 / 没有停止原因 ⇒ 有界止步（默认档）")
    domain = ("budget_exhausted", "fatal_error", "tool_retryable_error",
              "rounds_cap", "cursor_not_advancing", None)
    unmapped = [s for s in domain if r(s) not in TR.RUNTIME_GAP_REASONS]
    check(not unmapped,
          f"每个停止原因的顶层原因都在 `RUNTIME_GAP_REASONS` 闭集内；越界者={unmapped}")
    check(len({r("budget_exhausted"), r("tool_retryable_error"),
               r("rounds_cap")}) == 3,
          "预算截断 / 工具失败 / 有界止步**不**塌成同一个原因"
          "（塌了就读不出是哪一种止步）")


def _check_failure_and_reuse_wiring(check, details) -> None:
    """运行时接线：工具失败不得被读成「读完」；复用必须交回首读的停止原因。

    这一组是**结构性**检查（读运行时的控制流），不是行为注入：本批没有为一次树失败另造
    替身 rig，因此「失败分支真的被走到」只由下面的顺序与唯一性条件保证，不由一次真实
    失败运行证明。作为补偿，条件取得很紧：`complete = True` 全文只有一处，且必须落在
    `next_cursor is None` 分支内 —— 失败返回恰好既不带候选也不带续读位，只要消费路径上
    多一个赋值点，就会把失败冒充成读完。
    """
    src = Path(TR.__file__).read_text(encoding="utf-8")
    i_fatal = src.index('if result.status == "FATAL_ERROR":')
    i_retry = src.index('if result.status == "RETRYABLE_ERROR":')
    i_consume = src.index(
        "tree_materials, tree_gaps, skipped = _materials_from_tool_result(result)")
    check(i_fatal < i_retry < i_consume,
          "致命失败与可重试失败都在**消费本轮结果之前**止步"
          "（否则一次失败会以「空读」的形态混进材料与缺口）")
    check('"tool_status": result.status' in src
          and '"retryable": True' in src and '"retryable": False' in src,
          "两种失败各自记下工具状态与 `retryable` 标志（失败与致命可分辨）")

    check(src.count("complete = True") == 1, "「读完」全文只有一个赋值点")
    i_done = src.index("complete = True")
    check("if _next_cursor is None:" in src[:i_done]
          and src.rindex("if _next_cursor is None:", 0, i_done) > i_done - 400,
          "「读完」只成立在 `next_cursor is None` 那一支里")

    check('"stop_reason": _stop_reason})' in src,
          "缓存条目记录首读的停止原因（复用侧才有因可查）")
    check('"stop_reason": _cached.get("stop_reason")' in src,
          "复用事件把首读停止原因原样交回")
    check('if not _cached["complete"]:' in src and '"reused_read_set": True' in src,
          "复用一份**没读完**的读数时补一条带 `reused_read_set` 标记的 typed gap")
    check(src.count("read_set_cache.setdefault(") == 1, "复用缓存写入点唯一")

    loop = src[src.index("while True:"):]
    loop = loop[:loop.index("if not complete and")]
    check(loop.count("can_afford(tool_calls=1)") == 1
          and loop.index("can_afford(tool_calls=1)") < loop.index("_round += 1"),
          "续读在**推进轮次之前**查额度：额度不够就停在这一轮，不留事后越顶")
    check('_hard_cap = max(1, _cursor_info["total_span_positions"])' in src
          and "if _cursor_identity in _seen_cursors:" in src,
          "轮数上界由工具自报的位置总数推出，另配游标不前进守卫（有限步终止）")

    # 工具侧：批大小仍是**调用方给的参数**，没有把 6 全局放大成 N。
    spec = TT.TREE_INSPECT_SPEC
    props = spec.input_schema["properties"]
    check("max_spans" in props and props["max_spans"].get("type") == "integer"
          and "6" not in json.dumps(props["max_spans"]),
          "`max_spans` 仍是由调用方传入的整数参数（工具内没有写死 6，也没有全局放大）")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    _check_plan_order(check, details)
    _check_plan_set_and_status(check, details)
    _check_relevance_is_tiebreak_only(check, details)
    _check_topic_keys_are_topic_level(check, details)
    _check_reuse_key(check, details)
    _check_cursor_readout(check, details)
    _check_tool_cursor(check, details)
    _check_stop_reason_mapping(check, details)
    _check_failure_and_reuse_wiring(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

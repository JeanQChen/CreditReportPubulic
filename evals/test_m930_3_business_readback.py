"""Eval: M930-3 目标一 —— 主营业务材料包读回页的**逐 (栏目, 文档) 状态判定**。

用法: python -m evals.test_m930_3_business_readback

覆盖 `evaluation/business_material_readback.py` 的判据面，只测机制、不测内容：

1. **按章分轴**：`located_not_read`（「已定位但未读取」）只由**本栏所在那一章**里的
   未读带料位置触发。年报里同批探针词会在财务报告等**别章**命中「24、收入」
   「（二）收入确认」这类节点——把别章的未读一起算进头条，「主营业务这一节到底
   读到没有」就被淹没了。别章的未读**照样列出来**（`chapter_scope`），不藏、不新增
   第 8 态。
2. **章节判不出时不抹事实**：本栏导航没有选中任何读根（读集为空）时
   `own_chapter is None`，此时按**最保守**口径把未读带料位置全算"本章"——不能因为
   章节无从确定，就把"实料一个字没读"从头条上抹掉。
3. **定位位置逐位带标记**：已读/未读 · 本章/别章 · 章节未定 · **命中了哪个申报字段词** ·
   名下多少字符没进准入正文。带上命中词是有意的：客户/供应商两栏的强定位全部由
   申报字段里的「关联方」三字命中「十三、关联方及关联交易」——**关联方交易**不得
   冒充客户集中度披露，而真正的披露「（8）主要销售客户和主要供应商情况」那一刻是
   **弱定位且未读**。把词摆出来，这种"命中词对、材料错"才看得见。
4. **单元格投影不丢字段**：`_cell_summary` / `_matrix` 必须把章与两组未读清单带到
   读回页；摘要裁剪丢字段会让读回页回退成"判不出章节"的假象。
5. **真实文档**：两份年报的 `main_business` 本章未读必须为**空**、别章未读必须
   **被列出**，且「报告期内公司从事的主要业务 / 1、主要业务」的主体正文真的在读集里
   （`anp-7` 补读的落点）；收入/成本/毛利四栏必须仍如实记「表格拒发」。

不测内容：断言里的字串是**样本事实**，只出现在测试中，不参与任何生产规则。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import business_material_readback as RB  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 0. 合成夹具：只喂 `_state_for` 真正读的四样东西
# ---------------------------------------------------------------------------

class _FakeIndex:
    """只实现 `ancestors_of`——`_own_chapter` 唯一的依赖。"""

    def __init__(self, chains: dict[str, tuple[str, ...]]) -> None:
        self._chains = chains
        self.normalized = {n: f"《{n}》" for chain in chains.values() for n in chain}

    def ancestors_of(self, node_id: str) -> tuple[str, ...]:
        return self._chains.get(node_id, ())

    def normalized_title(self, node_id: str) -> str:
        return self.normalized.get(node_id, node_id)


class _FakeDecision:
    def __init__(self, selected_node_id=None, read_root_node_ids=()) -> None:
        self.selected_node_id = selected_node_id
        self.read_root_node_ids = tuple(read_root_node_ids)


def _loc(node_id: str, *, strong: bool = True, chapter: str | None = "ch1",
         title: str | None = None) -> dict:
    return {
        "node_id": node_id,
        "title": title or f"T-{node_id}",
        "strong": strong,
        "top_chapter_id": chapter,
        "path_titles": [chapter or "?", node_id],
        "locate_tiers": ["label_segment"],
        "matched_terms": ["甲"],
        "subject_adjacency": 0,
    }


def _facts(node_id: str, *, admitted=0, table_residue=0, body_residue=0) -> dict:
    return {
        "node_id": node_id,
        "admitted_chars": admitted,
        "table_residue_chars": table_residue,
        "body_residue_chars": body_residue,
        "residue_chars": table_residue + body_residue,
    }


def _text(node_id: str, body: str, *, title_like=False, form_like=False) -> dict:
    return {"span_id": "os-" + node_id, "node_id": node_id,
            "page_range": [1, 1], "char_length": len(body), "text": body,
            "form_like": form_like, "title_like": title_like}


def _run_state(located, read_nodes, read_texts, node_facts, *, own_chapter,
               fact_state="unqualified"):
    return RB._state_for(
        located=located, read_nodes=set(read_nodes), read_texts=read_texts,
        node_facts={f["node_id"]: f for f in node_facts},
        entry_decision={"status": "selected", "fallback_reason": None},
        fact_state=fact_state, own_chapter=own_chapter)


# ---------------------------------------------------------------------------
# 1. `_own_chapter`：本栏该读的那一章
# ---------------------------------------------------------------------------

def _own_chapter(check, details) -> None:
    #: 选中节点 → 最顶层祖先。`ancestors_of` 与生产同序：**由近及远**（父、祖父、…），
    #: 因此"最顶层祖先"是最后一个，不是第一个。
    index = _FakeIndex({
        "n2": ("n1", "ch1"),
        "n1": ("ch1",),
        "ch1": (),
    })
    check(RB._own_chapter(index, _FakeDecision(selected_node_id="n2")) == "ch1",
          "`_own_chapter`：选中节点一层层上溯到**最顶层祖先**")

    #: 选中节点自己就是顶层章节时返回它自己（`ancestors_of` 为空不等于"判不出"）。
    check(RB._own_chapter(index, _FakeDecision(selected_node_id="ch1")) == "ch1",
          "`_own_chapter`：选中节点即顶层章节时**就是这一章**，不是「判不出」")

    #: 降级：没有选中节点 → 退而用第一个读根；仍要上溯（读根常常是中层节点）。
    check(RB._own_chapter(index, _FakeDecision(selected_node_id=None,
                                               read_root_node_ids=("n2",))) == "ch1",
          "`_own_chapter`：降级时用第一个**读根**并上溯到顶层章节")

    #: 连读根都没有（读集为空）→ 如实返回 `None`，**不编**一个章节出来。
    check(RB._own_chapter(index, _FakeDecision()) is None,
          "`_own_chapter`：既无选中节点又无读根时返回 `None`（不编章节）")

    #: 读根列表只取**第一个**：顺序即导航给出的优先序，不做"选最合适的那个"。
    check(RB._own_chapter(index, _FakeDecision(
        read_root_node_ids=("ch1", "n1"))) == "ch1",
        "`_own_chapter`：降级时只取**第一个**读根，不另立择优判据")
    details.append("NOTE §1 `_own_chapter` 四面：上溯 / 自身即顶层 / 降级用读根 / 判不出")


# ---------------------------------------------------------------------------
# 2. `_state_for`：按章分轴
# ---------------------------------------------------------------------------

def _chapter_split(check, details) -> None:
    #: 本章无未读、别章有未读带料 → **不得**记「已定位但未读取」，但别章清单必须非空。
    state, observed, scope = _run_state(
        located=[_loc("here", chapter="ch1"), _loc("far", chapter="ch9")],
        read_nodes=["here"],
        read_texts=[_text("here", "本公司主营动力电池的研发、生产与销售，覆盖材料与回收。")],
        node_facts=[_facts("here", admitted=120), _facts("far", admitted=300)],
        own_chapter="ch1")
    check(state != "located_not_read",
          "按章分轴：**本章**没有未读带料位置时，别章的未读**不得**顶成本栏的头条")
    check([b["node_id"] for b in scope["unread_out_of_chapter"]] == ["far"],
          "按章分轴：别章的未读带料位置必须**逐条列在** `unread_out_of_chapter`（不藏）")
    check(scope["unread_in_chapter"] == [] and scope["own_chapter_id"] == "ch1",
          "按章分轴：本章未读清单为空，且 `own_chapter_id` 如实带出")
    check(scope["unread_out_of_chapter"][0]["material_chars"] == 300,
          "按章分轴：别章清单带**名下未入准入正文的字符数**，供人判断它值不值得读")
    details.append(f"NOTE §2a 本章无 / 别章有：state={state} observed={observed}")

    #: 本章有未读带料 → 头条就是「已定位但未读取」，别章清单一并带出。
    state, _observed, scope = _run_state(
        located=[_loc("here", chapter="ch1"), _loc("far", chapter="ch9")],
        read_nodes=[],
        read_texts=[],
        node_facts=[_facts("here", admitted=200), _facts("far", admitted=300)],
        own_chapter="ch1")
    check(state == "located_not_read",
          "按章分轴：**本章**有未读带料位置时头条仍是「已定位但未读取」")
    check([b["node_id"] for b in scope["unread_in_chapter"]] == ["here"]
          and [b["node_id"] for b in scope["unread_out_of_chapter"]] == ["far"],
          "按章分轴：本章/别章两侧**各自**成清单，互不串味")

    #: 未读位置但**名下没料**（空标题）不算缺口：本章无未读 → 不记 located_not_read。
    _state, _observed, scope = _run_state(
        located=[_loc("empty_title", chapter="ch1")],
        read_nodes=[],
        read_texts=[_text("somebody", "本公司主营动力电池的研发、生产与销售。")],
        node_facts=[_facts("empty_title", admitted=0)],
        own_chapter="ch1")
    check(scope["unread_in_chapter"] == [],
          "按章分轴：未读但**名下没有实质内容**的标题不算缺口（阈值 `MIN_MATERIAL_CHARS`）")

    #: 章节判不出（读集为空）→ 最保守：未读带料位置全算"本章"，头条不得被抹掉。
    state, _observed, scope = _run_state(
        located=[_loc("far1", chapter="ch9"), _loc("far2", chapter="ch9")],
        read_nodes=[],
        read_texts=[],
        node_facts=[_facts("far1", admitted=140), _facts("far2", admitted=61)],
        own_chapter=None)
    check(state == "located_not_read" and scope["own_chapter_id"] is None,
          "按章分轴：章节**判不出**时按最保守口径计数——实料没读这件事不得从头条消失")
    check(len(scope["unread_in_chapter"]) == 2
          and scope["unread_out_of_chapter"] == [],
          "按章分轴：章节判不出时全部未读都进 `unread_in_chapter`"
          "（`own_chapter is None` 即『全算本章』）")

    #: 三元素返回：调用方漏接 scope 会在这里炸，而不是悄悄丢字段。
    triple = _run_state(located=[], read_nodes=[], read_texts=[],
                        node_facts=[], own_chapter="ch1")
    check(isinstance(triple, tuple) and len(triple) == 3
          and set(triple[2]) >= {"own_chapter_id", "unread_in_chapter",
                                 "unread_out_of_chapter", "note"},
          "`_state_for` 返回 `(state, observed, scope)` 三元组，scope 四个键齐全")
    details.append("NOTE §2b 本章有/判不出/空标题三面已断言")


# ---------------------------------------------------------------------------
# 3. 标记与"命中词对≠材料对"
# ---------------------------------------------------------------------------

def _marks(check, details) -> None:
    marks = RB._loc_marks({"is_read": True, "in_own_chapter": True,
                           "chapter_determined": True, "material_chars": 42,
                           "matched_terms": ["收入"]})
    check("已读" in marks and "本章" in marks and "42" in marks
          and "「收入」" in marks,
          "`_loc_marks`：已读 / 本章 / **命中词** / 未入正文字符数四件都在")

    marks = RB._loc_marks({"is_read": False, "in_own_chapter": False,
                           "chapter_determined": True, "material_chars": 1581,
                           "matched_terms": ["关联方"]})
    check("**未读**" in marks and "**别章**" in marks and "「关联方」" in marks,
          "`_loc_marks`：未读与别章都必须**加粗显形**，不靠读者自己推")

    marks = RB._loc_marks({"is_read": False, "in_own_chapter": True,
                           "chapter_determined": False, "material_chars": 11,
                           "matched_terms": []})
    check("章节未定" in marks and "本章" not in marks,
          "`_loc_marks`：章节判不出时记「章节未定」，**不得**冒充「本章」")

    #: `_strong_term_note`：强定位**只**由少数几个申报字段词命中时显形。
    note = RB._strong_term_note({"located": [
        {"strong": True, "matched_terms": ["关联方"]},
        {"strong": True, "matched_terms": ["关联方"]},
    ]})
    check(note is not None and "关联方" in note,
          "`_strong_term_note`：一个词命中一遍整章时**显式**把词摆出来")
    check(RB._strong_term_note({"located": [
        {"strong": True, "matched_terms": ["甲"]},
        {"strong": True, "matched_terms": ["乙", "丙"]},
    ]}) is None,
        "`_strong_term_note`：命中词多于 `FEW_TERMS_MAX` 时**不**发这条提示（不是判据，只是提示）")
    check(RB._strong_term_note({"located": [
        {"strong": False, "matched_terms": ["关联方"]},
    ]}) is None,
        "`_strong_term_note`：只数**强定位**；弱定位（近似标题）不进这条提示")

    #: 未解决问题一句话必须说清"这是**本章**的缺口"，别章的另列。
    cell = {"states_observed": ["located_not_read"],
            "chapter_scope": {"unread_out_of_chapter": [{"title": "x"}]}}
    issues = RB._open_issues(cell)
    check("本章" in issues and "别章" in issues,
          "`_open_issues`：区分「**本章**定位到的原文未被读取」与「别章另有 N 处」（两种都不藏）")
    details.append("NOTE §3 命中词显形与两轴未解决措辞已断言")


# ---------------------------------------------------------------------------
# 4. 单元格投影：字段不得在摘要裁剪时丢
# ---------------------------------------------------------------------------

def _projection(check, details) -> None:
    cell = {
        "state": "partial_body", "state_label": "正文片段不完整",
        "states_observed": ["partial_body"],
        "located": [_loc("n1", chapter="ch1")],
        "read_texts": [_text("n1", "本公司主营动力电池的研发、生产与销售。")],
        "navigation": {"read_root_titles": ["1主要业务"], "fallback_reason": None,
                       "status": "selected", "supplement_root_titles": []},
        "required_fields": ["业务板块"], "content_role": "paragraph",
        "fact_eligibility": "unqualified",
        "own_chapter_id": "ch1", "own_chapter_title": "《ch1》",
        "chapter_scope": {"own_chapter_id": "ch1", "unread_in_chapter": [],
                          "unread_out_of_chapter": [{"node_id": "n9"}]},
    }
    summary = RB._cell_summary("D1", cell)
    check(summary["own_chapter_id"] == "ch1"
          and summary["own_chapter_title"] == "《ch1》"
          and summary["chapter_scope"]["unread_out_of_chapter"],
          "`_cell_summary`：章与两组未读清单必须**带过**——丢了读回页就回退成「判不出章节」的假象")

    #: 矩阵只渲染 `BUSINESS_GROUPS` 里登记的栏目 × `DOCUMENT_ORDER` 里的文档：
    #: 两者都得用**登记在册**的值，否则矩阵是空的（这条判据本身也是回归的一部分）。
    aspect_id = RB.BUSINESS_GROUPS[0][1][0]
    document_id = RB.DOCUMENT_ORDER[0]
    doc_results = {document_id: {"aspects": {aspect_id: cell}}}
    rows = RB._matrix(doc_results)
    found = [c for row in rows for c in row["cells"]
             if c["document_id"] == document_id]
    check(len(found) == 1 and found[0]["unread_in_chapter"] == []
          and found[0]["unread_out_of_chapter"] == [{"node_id": "n9"}]
          and found[0]["own_chapter_id"] == "ch1",
          "`_matrix`：矩阵单元格也带章与两组未读清单（两个出口用同一批判据）")
    details.append("NOTE §4 摘要/矩阵两个出口的字段面已断言")


# ---------------------------------------------------------------------------
# 5. 缺口台账：表体在文档里存在 ≠ 这一栏拿到了它
# ---------------------------------------------------------------------------

def _table_ledger(check, details) -> None:
    #: 台账的栏目必须都在**矩阵登记册**里：否则读回页会出现"矩阵没有、台账有"的栏目。
    grouped = {a for _n, ids in RB.BUSINESS_GROUPS for a in ids}
    check(set(RB.TABLE_GAP_ASPECTS) <= grouped,
          "缺口台账 `TABLE_GAP_ASPECTS` 必须全部登记在 `BUSINESS_GROUPS` 里（不得另立栏目）")

    aspect_id = RB.TABLE_GAP_ASPECTS[0]
    document_id = RB.DOCUMENT_ORDER[0]
    cell = {
        "state": "table_not_released", "state_label": "表格拒发",
        "read_node_ids": ["n1"], "located": [{"node_id": "n2"}],
    }
    diagnostics = [
        {"document_id": document_id, "node_id": "n1", "page_number": 24,
         "landing": "table_inside", "node_title": "（1） 营业收入构成",
         "slice_chars": 298, "component_count": 46, "admitted": False,
         "span_ids": [], "text": "表内文字不得出海",
         "rejection_reason": {"verdicts": ["aligned"], "layer": "span",
                              "admission_reasons": [], "refusal_reasons": []}},
        {"document_id": document_id, "node_id": "n9", "page_number": 99,
         "landing": "table_inside", "node_title": "无关节点",
         "slice_chars": 999, "component_count": 9, "admitted": False,
         "span_ids": [], "text": "无关",
         "rejection_reason": {"verdicts": ["aligned"], "layer": "span",
                              "admission_reasons": [], "refusal_reasons": []}},
    ]
    ledger = RB._table_gap_ledger({document_id: {"aspects": {aspect_id: cell}}},
                                 diagnostics)
    check(len(ledger) == 1 and ledger[0]["aspect_id"] == aspect_id
          and ledger[0]["document_id"] == document_id,
          "缺口台账：逐 (栏目, 文档) 一条，栏目不在矩阵登记册时不出现")
    #: 只收**本栏覆盖到**的节点：全局诊断里别处的表体不得算进这一栏。
    check([r["node_id"] for r in ledger[0]["table_landings"]] == ["n1"]
          and ledger[0]["table_landing_chars"] == 298,
          "缺口台账：只归档**本栏读集/定位覆盖到**的表体落地，别处的表体不算这一栏的账")
    #: **结构性**守住"扁平表内字符串不算修好表格"：台账只带位置与判词，不带表体文字。
    leaks = [k for r in ledger[0]["table_landings"] for k in r
             if "text" in k or "excerpt" in k]
    check(not leaks,
          "缺口台账：**不得**夹带表体文字（只有页码/落地/判词/字符数/坐标）"
          "——扁平表内字符串不算表格修复")
    check(len(ledger[0]["still_missing"]) >= 4,
          "缺口台账：`still_missing` 必须把四条缺口逐条列出（表对象信道读到什么 / "
          "span 层拒发仍成立 / 禁止扁平化冒充 / 数字权威未做）")
    joined = " ".join(ledger[0]["still_missing"])
    check("span" in joined and "数字权威" in joined and "可核查检索" in joined,
          "缺口台账：缺口关键词都在（可核对，不是一句『缺表格』了事）")
    #: 信道数据**整块缺失**时（旧产物 / 未传 `--pack`）也必须安全降级：只能记「系统能力
    #: 未取得」，**不得**因为读不到信道就倒推出「来源里没有这张表」。
    check("可核查检索" in joined and "系统能力未取得" in joined
          and "来源缺口" in joined,
          "缺口台账：信道读数缺失时降级为「系统能力未取得 + 需可核查检索」，"
          "不得倒推成来源缺口")
    #: **勘误回归**：旧台账断言「本批**没有**把 TableObjectV4 链接到主营业务材料包上」。
    #: 工作区的表对象信道（`harness/table_object_release.py` → `table_object_materials.py`
    #: → `tree_tools.release_tables_for`）已经落地，那句话已不成立；留着它会让读回页
    #: 把「已进 Pack 的合格表材料」读成「这条链还没接」。
    check("TableObjectV4" not in joined and "没有**把它接到" not in joined,
          "缺口台账：**不得**再写「表对象链本批没有接到主营业务材料包上」"
          "——那句话已被工作区实现证伪，留着就是把已进 Pack 的表材料读成未接线")
    details.append("NOTE §5 缺口台账四面：登记册一致 / 只算本栏 / 不带表体 / 四缺口齐且无陈旧断言")


# ---------------------------------------------------------------------------
# 6. 真实文档：两份年报的 main_business 与收入成本四栏
# ---------------------------------------------------------------------------

#: 样本事实（只在测试里出现，不参与任何生产规则）：年报「主要业务」一节的**主体正文**
#: 开头。`anp-7` 补读要修的正是它——它原先一个字都没进读集。
_SUBJECT_BODY_NEEDLE = "主要从事动力电池、储能电池的研发、生产、销售"

#: 收入 / 成本 / 毛利四栏：本批**必须**仍如实记「表格拒发」，不得因别处拿到经营材料
#: 就改口称主营业务材料包已合格。
_TABLE_ASPECTS = (
    "company_business_main.revenue_breakdown",
    "company_business_main.cost_gross_margin",
    "company_business_main.period_unit_caliber",
    "company_business_main.industry_chain_position",
)

_INDEX_CACHE: dict = {}


def _real_doc(document_id: str):
    """真实文档的 `(index, snapshot, nav_profile, aspects_by_id)`；缺件返回 `None`（如实 skip）。"""
    if document_id in _INDEX_CACHE:
        return _INDEX_CACHE[document_id]
    pdf = REPO / "data/samples/300750/announcements" / f"{document_id}.pdf"
    if not pdf.exists() or not (REPO / "data/evidence.db").exists():
        _INDEX_CACHE[document_id] = None
        return None
    try:
        _req, aspects_by_id, nav_profile, _c = RB.build_requirements(REPO)
        index, snapshot, _outline, _blocks, _live = RB.build_index(REPO, pdf)
    except Exception:  # noqa: BLE001 —— 环境缺件如实 skip，不猜
        _INDEX_CACHE[document_id] = None
        return None
    _INDEX_CACHE[document_id] = (index, snapshot, nav_profile, aspects_by_id)
    return _INDEX_CACHE[document_id]


def _judge(index, snapshot, nav_profile, aspects_by_id, entry, aspect):
    """**逐字复刻**生产 `run()` 的调用序：定位 → 打标记 → 判态。"""
    from document_structure import navigation as NAV

    decision = NAV.navigate(index, entry, profile=nav_profile)
    read_nodes = set(decision.read_node_ids)
    located = RB.locate(index, entry, aspect)
    facts = RB._node_facts(snapshot)
    own = RB._own_chapter(index, decision)
    for loc in located:
        row = facts.get(loc["node_id"]) or {}
        loc["is_read"] = RB._is_read(loc, read_nodes)
        loc["chapter_determined"] = own is not None
        loc["in_own_chapter"] = own is None or loc["top_chapter_id"] == own
        loc["material_chars"] = row.get("admitted_chars", 0) + \
            row.get("residue_chars", 0)
    state, observed, scope = RB._state_for(
        located=located, read_nodes=read_nodes,
        read_texts=RB._read_texts(snapshot, read_nodes), node_facts=facts,
        entry_decision={"status": decision.status,
                        "fallback_reason": decision.fallback_reason},
        fact_state=None, own_chapter=own)
    return decision, read_nodes, located, state, observed, scope


def _real_documents(check, details) -> int:
    skipped = 0
    for document_id in ("NDSD_2025_year", "NDSD_2024_year"):
        built = _real_doc(document_id)
        if built is None:
            details.append(f"SKIP {document_id}：缺少样本或 Evidence 库（不猜）")
            skipped += 1
            continue
        index, snapshot, nav_profile, aspects_by_id = built
        entries = {e.aspect_id: e for e in nav_profile.entries}

        aspect_id = "company_business_main.main_business"
        entry, aspect = entries.get(aspect_id), aspects_by_id.get(aspect_id)
        if entry is None or aspect is None:
            details.append(f"SKIP {document_id}：profile/Contract 里没有 {aspect_id}")
            skipped += 1
            continue
        _dec, read_nodes, _located, _st, _obs, scope = _judge(
            index, snapshot, nav_profile, aspects_by_id, entry, aspect)

        check(scope["own_chapter_id"] is not None,
              f"{document_id} `main_business`：本栏**有**所在章（导航选中了读根），"
              f"不是「判不出」——实得 {scope['own_chapter_id']}")
        check(scope["unread_in_chapter"] == [],
              f"{document_id} `main_business`：本章未读带料位置为**空**"
              f"（`anp-7` 补读的落点）——实得 {len(scope['unread_in_chapter'])} 处")
        check(bool(scope["unread_out_of_chapter"]),
              f"{document_id} `main_business`：别章（财务报告）的强定位未读**被列出**，不藏"
              f"——实得 {len(scope['unread_out_of_chapter'])} 处")
        check(all(b["top_chapter_id"] != scope["own_chapter_id"]
                  for b in scope["unread_out_of_chapter"]),
              f"{document_id} `main_business`：`unread_out_of_chapter` 里**没有**本章节点"
              "（两侧清单不许串味）")

        #: 主体正文真的在读集里：这是本轮返修的**业务落点**，不是"读到了一些字"。
        titles = "".join(index.normalized_title(n) for n in read_nodes)
        check("主要业务" in titles,
              f"{document_id} `main_business`：读集里有「主要业务」这一节（标题面）")
        blob = "".join(t["text"] for t in RB._read_texts(snapshot, read_nodes))
        check(_SUBJECT_BODY_NEEDLE in blob,
              f"{document_id} `main_business`：**「主要业务」主体正文逐字在读集里**"
              "（不是只有标题）")
        details.append(
            f"NOTE {document_id} main_business：章="
            f"{index.normalized_title(scope['own_chapter_id'])} "
            f"本章未读={len(scope['unread_in_chapter'])} "
            f"别章未读={len(scope['unread_out_of_chapter'])} "
            f"读集节点={len(read_nodes)}")

        #: 收入 / 成本 / 毛利四栏：本批必须**仍**如实记「表格拒发」。
        for table_aspect in _TABLE_ASPECTS:
            a_entry, a_aspect = entries.get(table_aspect), \
                aspects_by_id.get(table_aspect)
            if a_entry is None or a_aspect is None:
                continue
            _d, _r, _l, a_state, _o, _s = _judge(
                index, snapshot, nav_profile, aspects_by_id, a_entry, a_aspect)
            check(a_state == "table_not_released",
                  f"{document_id} `{table_aspect}`：**表格拒发**必须如实保留"
                  f"（实得 `{a_state}`）——不得因别处拿到经营材料就改口称材料包合格")
    return skipped


# ---------------------------------------------------------------------------

def _support_judgment(check, details) -> None:
    """轴二**逐份**判读：栏级结论只回答「责任份（锚）支持了没有」。

    旧口径是「按优势序取**第一个**出现的状态」——注释写的是取最弱，代码取的是最强，
    于是「锚这一份不支持、别的一份支持」会被读成「本栏已被支持」。新口径不取最好也不取
    最差：逐 (栏目, 文档) 各自读自己的读集，再按**来源角色**判读。下面两侧成对断言：
    同样的两个状态，只把 **哪一份** 持有它调换，结论必须跟着调换。
    """
    d25, d24, dkz = RB.DOCUMENT_ORDER

    def cells(**by_doc):
        return {d: {"document_id": d, "support_state": s}
                for d, s in by_doc.items()}

    all_ok = RB._column_support_verdict(cells(
        **{d25: "support_established", d24: "support_established",
           dkz: "support_established"}))
    check(all_ok["verdict"] == "required_support_met_all"
          and all_ok["anchor_supported"] is True
          and all_ok["established_documents"] == sorted([d25, d24, dkz]),
          "三份都逐字支持 ⇒ `required_support_met_all`（仍须逐项复核共享读集）")

    anchor_only = RB._column_support_verdict(cells(
        **{d25: "support_established", d24: "support_absent",
           dkz: "support_no_text"}))
    check(anchor_only["verdict"] == "required_support_met_anchor_only"
          and anchor_only["non_anchor_established"] == [],
          "锚支持、其余不支持 ⇒ `required_support_met_anchor_only`"
          "（「锚支持」与「三份都支持」不是一件事）")

    #: **本条就是本轮要修的误读现场**：历史份支持、**锚**不支持。旧口径（按优势序取第一个
    #: 出现的状态）会报「原文支持已建立」，读回的人因此看不到「本期主语料没有支持」。
    history_only = RB._column_support_verdict(cells(
        **{d25: "support_absent", d24: "support_established",
           dkz: "support_absent"}))
    check(history_only["verdict"] == "support_from_non_anchor_only"
          and history_only["anchor_supported"] is False
          and history_only["non_anchor_established"] == [d24],
          "只有历史份支持、锚不支持 ⇒ `support_from_non_anchor_only`"
          "（历史份的支持不得冒充本期事实）——旧口径在这里会报「已建立」")

    swapped = RB._column_support_verdict(cells(
        **{d25: "support_established", d24: "support_absent",
           dkz: "support_absent"}))
    check(swapped["verdict"] == "required_support_met_anchor_only"
          and history_only["verdict"] != swapped["verdict"],
          "同一对状态、只把持有它的**那一份**换掉，结论必须跟着换"
          "（判读挂在来源责任上，不挂在状态本身的强弱上）")

    none_ok = RB._column_support_verdict(cells(
        **{d25: "support_absent", d24: "support_absent", dkz: "support_absent"}))
    check(none_ok["verdict"] == "required_support_missing"
          and none_ok["weakest"] == "support_absent",
          "三份都不支持 ⇒ `required_support_missing`；`weakest` 由闭集**末位**取，"
          "与栏级判读是两条不同的读法（都不是唯一结论）")
    check(RB._column_support_verdict({})["verdict"] == "not_judged"
          and RB._column_support_verdict({})["label"]
          == RB.COLUMN_SUPPORT_VERDICTS["not_judged"],
          "无可判读的逐份状态 ⇒ `not_judged`（不猜成「不支持」）")

    shared_ok = RB._column_support_verdict(cells(
        **{d25: "support_established_shared", d24: "support_absent", dkz: "support_absent"}))
    check(shared_ok["verdict"] == "required_support_met_anchor_only",
          "`support_established_shared` 与 `support_established` 同属「已建立」一侧，"
          "但标签本身保留「须逐项复核」的限定")

    for d, duty in RB.DOCUMENT_DUTY.items():
        if duty["is_anchor"]:
            check(d in RB.DOCUMENT_ORDER and duty["source_role"] == "current_state_source",
                  f"锚登记为当前状态来源且在三份之内：{d}")
    check(sum(1 for duty in RB.DOCUMENT_DUTY.values() if duty["is_anchor"]) == 1,
          "锚恰有一份（多锚会让「责任份支持了没有」无唯一答案）")
    check(set(RB.DOCUMENT_DUTY) == set(RB.DOCUMENT_ORDER),
          "逐份责任登记与三份材料一一对应（没有第四种角色、也没有漏登的一份）")
    details.append("NOTE §6 轴二逐份判读六面：全支持 / 仅锚 / 仅历史（旧口径误读现场）/ "
                   "换份即换结论 / 全不支持 / 判不出")


def _table_channel(check, details) -> None:
    """**表对象信道**与 span 层是两条信道：逐栏读数不得互相顶替。

    这一节测的是**机制**：读关系怎么标、Pack 侧 aspect 归属不一致时怎么记、
    以及**不得**按节点归属断言「本栏没有这张表」。
    """
    #: 三档读关系与三档去向必须是**闭集**且逐条有标签：漏一个标签会在渲染时露成英文键名。
    check(set(RB.HOST_READ_RELATIONS) == {"read_root", "read_node", "located_only"}
          and set(RB.HOST_READ_RELATIONS) <= set(RB.HOST_READ_RELATION_LABELS),
          "表对象信道：宿主读关系是**闭集**且逐档有中文标签")
    check(tuple(RB.TABLE_CHANNEL_STATES) == (
        "material_ready", "released_not_material", "not_released")
          and set(RB.TABLE_CHANNEL_STATES) <= set(RB.TABLE_CHANNEL_STATE_LABELS),
          "表对象信道：对象去向是**三档闭集**（已成材料 / 已放行未成材料 / 未获放行）")

    #: 合成一份文档级信道清点：一个对象落在读根、一个落在仅定位、一个未获放行。
    def _obj(rid, page, title, *, host, state, reason=None, relation_node=None):
        return {"release_id": rid, "local_proof_id": "tlpp-" + rid,
                "table_id": "to4-" + rid, "table_locator": "loc-to4-" + rid,
                "host_evidence_id": host, "host_evidence_ids": [host],
                "page_number": page, "table_title": title,
                "unit_text": "单位：万元",
                "structure_kind": "matrix", "structure_class": "financial",
                "structure_state": "complete",
                "body_row_count": 5, "column_count": 7, "cell_count": 30,
                "all_columns_supported": True, "cross_block": False,
                "numeric_authority": False,
                "table_channel_state": state, "material_refusal_reason": reason,
                "node_ids": [relation_node or host],
                "node_attribution_reason": "component_or_span_node"}

    channel = {
        "release_rule_version": "gto-3", "material_version": "gtm-1",
        "release_batch_state": "accounted", "document_qualified": False,
        "block_count": 3, "object_count": 2, "material_count": 2,
        "refusal_count": 1,
        "material_refusal_reason_counts": {}, "release_refusal_reason_counts": {},
        "released_object_count": 2, "released_not_material_count": 0,
        "unreleased_candidate_count": 1,
        "records": [
            _obj("gtr-root", 19, "1）营业收入整体情况", host="n-root",
                 state="material_ready"),
            _obj("gtr-loc", 57, "教育程度", host="n-loc", state="material_ready"),
        ],
        "unreleased_candidates": [
            {"host_evidence_id": "", "table_id": "to4-bad", "table_locator":
             "loc-to4-bad", "page_number": 57,
             "table_title": "", "structure_state": "partial",
             "release_refusal_reason": "structure_not_complete",
             "defect_codes": ["structure_not_complete", "header_row_absent"],
             "unproven_cell_count": 4, "node_ids": [],
             "node_attribution_reason": "refusal_record_carries_no_host_block"},
        ],
        "by_node": {
            "n-root": [{"release_id": "gtr-root", "local_proof_id": "tlpp-gtr-root",
                        "table_id": "to4-gtr-root",
                        "table_locator": "loc-to4-gtr-root",
                        "host_evidence_id": "n-root",
                        "host_evidence_ids": ["n-root"],
                        "page_number": 19, "table_title": "1）营业收入整体情况",
                        "unit_text": "单位：万元", "structure_kind": "matrix",
                        "structure_class": "financial",
                        "structure_state": "complete", "body_row_count": 5,
                        "column_count": 7, "cell_count": 30,
                        "all_columns_supported": True, "cross_block": False,
                        "numeric_authority": False,
                        "table_channel_state": "material_ready",
                        "material_refusal_reason": None, "node_ids": ["n-root"],
                        "node_attribution_reason": "component_or_span_node"}],
            "n-loc": [{"release_id": "gtr-loc", "local_proof_id": "tlpp-gtr-loc",
                       "table_id": "to4-gtr-loc",
                       "table_locator": "loc-to4-gtr-loc",
                       "host_evidence_id": "n-loc", "host_evidence_ids": ["n-loc"],
                       "page_number": 57, "table_title": "教育程度",
                       "unit_text": "单位：万元", "structure_kind": "matrix",
                       "structure_class": "financial",
                       "structure_state": "complete", "body_row_count": 5,
                       "column_count": 7, "cell_count": 30,
                       "all_columns_supported": True, "cross_block": False,
                       "numeric_authority": False,
                       "table_channel_state": "material_ready",
                       "material_refusal_reason": None, "node_ids": ["n-loc"],
                       "node_attribution_reason": "component_or_span_node"}],
        },
        "unreleased_by_node": {},
        "node_titles": {"n-root": "（1） 营业收入构成", "n-loc": "1、员工数量及教育程度"},
        "note": RB.TABLE_CHANNEL_NOTE,
    }
    cell_channel = RB.build_table_channel_for(
        channel, read_nodes={"n-root", "n-heard"}, read_root_nodes={"n-root"},
        located_nodes={"n-root", "n-loc"})

    #: 读关系必须**逐条**分清：读根 / 仅定位，两档不能合并成「都在节点集里」。
    got = {o["release_id"]: o["host_read_relation"]
           for o in cell_channel["objects"]}
    check(got == {"gtr-root": "read_root", "gtr-loc": "located_only"},
          f"表对象信道：逐条标出宿主读关系（实得 {got}）——"
          "「本栏读到的节点上」与「只是标题定位到」不是一件事")
    check(cell_channel["material_ready_by_host_relation"]
          == {"read_root": 1, "located_only": 1},
          "表对象信道：已成材料按读关系**分开**计数，读的人不必自己分辨")
    check(cell_channel["located_only_object_count"] == 1,
          "表对象信道：单列「仅定位」的对象数——这一档是候选，不得撑本栏覆盖")
    check(cell_channel["release_refusal_reason_counts"] == {"structure_not_complete": 1},
          "表对象信道：未获放行候选带 typed 理由计数（挡下 ≠ 不存在）")
    check("宿主节点来自组件归属" in (cell_channel["host_attribution_caveat"] or "")
          and "以 Pack 侧 aspect 归属为准" in cell_channel["host_attribution_caveat"],
          "表对象信道：**逐栏**带宿主归属免责——按节点认表在真实现场两个方向都会错")

    #: 拒发账**归不到节点**：它**不**按本栏收窄，但**也**不得被丢掉——丢在本栏就是
    #: 把「挡下」读成「不存在」。逐条必须标出作用域是整份文档。
    blocked = cell_channel["unreleased_candidates"]
    check(len(blocked) == 1 and blocked[0]["scope"] == "document"
          and blocked[0]["matched_node_id"] is None
          and blocked[0]["host_read_relation"] is None,
          "表对象信道：逐表拒发账**按整份文档**带出，明确不归本栏（不猜归属、也不丢掉）")
    check(cell_channel.get("unreleased_scope") == "document"
          and "不可判定" in (cell_channel.get("unreleased_scope_note") or ""),
          "表对象信道：作用域逐栏逐字写出来，读的人不会把整份账读成本栏账")
    check(blocked[0]["defect_codes"] == ["structure_not_complete", "header_row_absent"],
          "表对象信道：拒发候选带逐条缺陷码（不只一个主理由，缺陷要列全）")

    #: 收窄口径必须是**显式传入的那三样**：没传进来的节点不得被收进来。
    elsewhere = _obj("gtr-elsewhere", 88, "同一章别的标题下的表", host="n-elsewhere",
                     state="material_ready")
    channel_with_elsewhere = {
        **channel,
        "by_node": {**channel["by_node"], "n-elsewhere": [elsewhere]},
    }
    narrowed = RB.build_table_channel_for(
        channel_with_elsewhere, read_nodes={"n-root"}, read_root_nodes={"n-root"},
        located_nodes={"n-root", "n-loc"})
    check("gtr-elsewhere" not in {o["release_id"] for o in narrowed["objects"]},
          "表对象信道：未传入的节点**不得**进入本栏读数（不按章节外扩、不按相似度补）")
    check({o["release_id"] for o in narrowed["objects"]} == {"gtr-root", "gtr-loc"},
          "表对象信道：收窄后只剩传入节点上的对象（读根 N／读集 N／仅定位 N）")

    # -- Pack 侧去向 ------------------------------------------------------
    import tempfile
    aspect_id = RB.TABLE_GAP_ASPECTS[0]
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "material_pack.json"
        pack.write_text(json.dumps({
            "sections": {"company": {
                "table_object_material_ids": ["m-1"],
                "entries": [
                    {"material_id": "m-1", "topic_id": "company_business",
                     "material_type": "table_context",
                     "document_identity": {"document_id": "D1",
                                           "document_version": "v1"},
                     "locator": {"page": 19, "table_title": "1）营业收入整体情况"},
                     "content_qualification": {"kind": "table_object"},
                     "aspect_ids_from_aspect_results": [],
                     "disposition": {"aspect_ids": [aspect_id],
                                     "admission_state": "admitted",
                                     "retention_state": "retained",
                                     "reason_code": "ok"},
                     "in_writer_manifest": True,
                     "writer_manifest_query": "checked",
                     "text_chars": 611, "text_status": "resolved"},
                    {"material_id": "m-2", "topic_id": "company_business",
                     "material_type": "narrative",
                     "document_identity": {"document_id": "D1"},
                     "locator": {"page": 20},
                     "content_qualification": {"kind": "text"},
                     "disposition": {"aspect_ids": [aspect_id]},
                     "in_writer_manifest": True},
                ]}}}, ensure_ascii=False), encoding="utf-8")
        loaded = RB.load_run_tables(pack)
    check(loaded["available"] and loaded["entry_count"] == 2
          and loaded["table_entry_count"] == 1,
          "Pack 去向：只认**表对象材料**（正文材料不进这一列），逐节条目数照实计")
    material = loaded["materials"][0]
    #: **两条 aspect 归属来源都带出**，并给出一致性布尔：本页不替任何一条说话。
    check(material["aspect_ids_from_aspect_results"] == []
          and material["disposition_aspect_ids"] == [aspect_id]
          and material["aspect_attribution_agrees"] is False,
          "Pack 去向：aspect 归属两条来源**都带出**，不一致时逐条显形"
          "（只取一条会让另一条上的绑定凭空消失）")
    check(loaded["by_key"].get(("D1", aspect_id))
          and loaded["by_key"][("D1", aspect_id)][0]["in_writer_manifest"] is True,
          "Pack 去向：按 (document_id, aspect_id) 索引取得到，且带上「进 Writer 清单」")

    #: 产物缺失 → `available=False`，**不**是空材料表：那会被读成「本栏没有表材料」。
    check(RB.load_run_tables(None)["available"] is False
          and RB.load_run_tables(Path("does-not-exist.json"))["available"] is False,
          "Pack 去向：产物不可得时记 `available=False`，**不**记成「没有材料」")

    # -- 台账：Pack 侧才是「进没进 Pack」的口径 ---------------------------
    #: 三条分支各喂一个 (aspect, 文档)，逐条读回它们**只**留下今天仍为真的缺口。
    docs = RB.DOCUMENT_ORDER
    aspects = RB.TABLE_GAP_ASPECTS

    #: 分支一：表材料**已进 Pack**，但 Pack 的 aspect 结果没把它记到本栏名下。
    cell_packed = {
        "state": "table_not_released", "state_label": "表格拒发",
        "read_node_ids": ["n-root"], "located": [{"node_id": "n-root"}],
        "table_channel": cell_channel,
        "pack_tables": [{**material, "material_id": "m-1"}],
        "pack_tables_available": True,
    }
    #: 分支二：本栏**只**在「仅定位」节点上看到已放行对象，Pack 侧没有本栏表材料。
    located_only_channel = {**channel,
                            "by_node": {"n-loc": channel["by_node"]["n-loc"]}}
    cell_located = {
        "state": "table_not_released", "state_label": "表格拒发",
        "read_node_ids": ["n-root"], "located": [{"node_id": "n-loc"}],
        "table_channel": RB.build_table_channel_for(
            located_only_channel, read_nodes={"n-root"},
            read_root_nodes={"n-root"}, located_nodes={"n-root", "n-loc"}),
        "pack_tables": [], "pack_tables_available": True,
    }
    #: 分支三：本栏连**被挡下的**候选都没有，Pack 侧也没有本栏表材料。
    #: **注意作用域**：`gto-3` 的拒发账是**整份文档**的（拒绝记录不带宿主块身份，
    #: `build_table_channel_for` 因此**不**按节点筛它）。所以"本栏连候选都没有"这句
    #: 只在**本文档一条拒发都没有**时成立；本文档有拒发时，只能说"本栏没有已定位对象，
    #: 本文档另有 N 条未获放行的表（归不到本栏）"——那是分支二，不是这一条。
    clean_channel = {**channel, "objects": [], "unreleased_candidates": []}
    cell_empty = {
        "state": "table_not_released", "state_label": "表格拒发",
        "read_node_ids": ["n-other"], "located": [],
        "table_channel": RB.build_table_channel_for(
            clean_channel, read_nodes={"n-other"}, read_root_nodes={"n-other"}),
        "pack_tables": [], "pack_tables_available": True,
    }
    #: 分支三的反面：本文档**有**拒发（作用域＝整份文档）、本栏没有已定位对象。
    cell_doc_scope = {
        "state": "table_not_released", "state_label": "表格拒发",
        "read_node_ids": ["n-other"], "located": [],
        "table_channel": RB.build_table_channel_for(
            channel, read_nodes={"n-other"}, read_root_nodes={"n-other"}),
        "pack_tables": [], "pack_tables_available": True,
    }
    #: 分支四：**真**绑定缺口——材料在 Pack 里，但两条 aspect 轴在本栏都读不到。
    cell_truegap_material = {**material, "aspect_ids_from_aspect_results": [],
                             "disposition_aspect_ids": [], "aspect_ids": [],
                             "aspect_attribution_agrees": True}
    cell_truegap = {
        "state": "table_not_released", "state_label": "表格拒发",
        "read_node_ids": ["n-root"], "located": [{"node_id": "n-root"}],
        "table_channel": cell_channel,
        "pack_tables": [cell_truegap_material], "pack_tables_available": True,
    }
    ledger = RB._table_gap_ledger({
        docs[0]: {"aspects": {aspects[0]: cell_packed, aspects[1]: cell_truegap}},
        docs[1]: {"aspects": {aspects[0]: cell_located}},
        docs[2]: {"aspects": {aspects[0]: cell_empty, aspects[1]: cell_doc_scope}},
    }, [])
    by_key_ledger = {(r["document_id"], r["aspect_id"]): r for r in ledger}
    check(len(by_key_ledger) == 5,
          f"台账：逐 (栏目, 文档) 各一条（实得 {len(by_key_ledger)}）")
    joined = {k: " ".join(r["still_missing"]) for k, r in by_key_ledger.items()}

    packed = joined[(docs[0], aspects[0])]
    #: 表材料的栏目归属**只**在 RMD `aspect_ids` 上——这是设计（`AspectResearchResult.material_ids`
    #: 按设计不收表材料），台账必须**如实讲清是哪条轴**，而不是报成缺口。
    check("`AspectResearchResult.material_ids` **按设计**不收表对象材料" in packed
          and "必须取 RMD `aspect_ids`" in packed,
          "台账：表材料只有 RMD 一条归属轴时必须**如实记为设计并指路**"
          "（读的人才知道该取哪条轴，而不是以为链路断了）")
    check("绑定缺口" not in packed and "不是研究侧取材缺口" not in packed,
          "台账：**不得**把「表材料只有 RMD 一条轴」报成绑定缺口"
          "——那是把两条轴各管一段读成链路断裂")
    check("本栏的表材料进了 Pack" not in packed or "没有**一份" not in packed,
          "台账：材料已在 Writer 清单里，就**不得**再报「没进 Writer 清单」")

    truegap = joined[(docs[0], aspects[1])]
    check("**两条轴上都读不到本栏**" in truegap and "真的绑定缺口" in truegap,
          "台账：两条 aspect 轴**都**读不到时才是真的绑定缺口，必须报出来"
          "（否则「材料进了 Pack 但没栏目认领」会静默漏掉）")

    located = joined[(docs[1], aspects[0])]
    #: 本条最容易写错的一句话：按节点认表在真实现场两个方向都会错。
    check("**不能**反过来按节点归属断言「本栏没有这张表」" in located
          and "host_attribution_caveat" in located,
          "台账：宿主归属对不上时，**不得**断言「本栏没有这张表」，且必须带免责指路")
    check("去向未对上" in located and "要查的是 Pack 侧绑定与派发，不是来源" in located,
          "台账：这一分支要查的是 **Pack 侧绑定与派发**，不是来源")
    check("全部落在「仅定位」节点上（1 张）" in located,
          "台账：按宿主归属对不上时，逐条给出「仅定位」张数，供人核对")

    empty = joined[(docs[2], aspects[0])]
    check("可核查检索" in empty and "系统能力未取得" in empty
          and "不按「来源缺口」记" in empty,
          "台账：连候选都没有时，**不得**直接下「来源没有这张表」"
          "——先要可核查检索，取得之前按系统能力未取得记")

    #: 反面：本文档**有**拒发（`gto-3` 的账是整份文档的）⇒ 不能再说"本栏连候选都没有"，
    #: 也不能反过来说"本栏有候选"。两侧都不可断言，那种读法本身就要写出来。
    doc_scope = joined[(docs[2], aspects[1])]
    check("系统能力缺陷" in doc_scope and "作用域＝整份文档" in doc_scope,
          "台账：文档级拒发的账**按整份文档**带出，并如实记**系统能力缺陷**"
          "（不得把它读成本栏的候选）")
    #: 这一支**两侧都不可断言**，故判据是「两句话都写出来了」而不是任一句。只写一侧
    #: （无论是「本栏有候选」还是「本栏没有候选」）都会把那批拒发读成本栏的事实。
    check("本栏读集与定位集里**没有**已定位的表对象" in doc_scope
          and "归不到本栏" in doc_scope
          and "不能据此断言「本栏有候选」" in doc_scope
          and "不能断言「本栏没有候选」" in doc_scope,
          "台账：文档级拒发在场时，**既**不得断言本栏有候选、**也**不得断言没有候选")
    for key, text in joined.items():
        check("表对象" in text and "span 层的表内/表邻落地仍是**拒发**" in text,
              f"台账（{key[1]} · {key[0]}）：两条信道**各自留档**，"
              "span 层的拒发结论不随信道走通而消失")
        check("数字权威" in text and "路径 A 预验证" in text,
              f"台账（{key[1]} · {key[0]}）：表里数字要成合格事实仍须**路径 A 预验证**，本批不做")

    #: 结构性守住「两条信道不得合并」：读回源码里 span 层与表对象信道各说各的。
    src = (REPO / "evaluation" / "business_material_readback.py").read_text(
        encoding="utf-8")
    check("表对象信道" in src and "host_attribution_caveat" in src
          and "material_ready_by_host_relation" in src,
          "读回源码：表对象信道的三样读数（信道名 / 免责 / 读关系计数）都在")


def _located_not_in_pack(check, details) -> None:
    """`/3` 新增一列：本栏**已定位但未进 Pack**的表对象，逐条带 typed 原因。

    三件事**必须分开**：放行门挡下 / 材料化挡下 / 已成材料但本次 Pack 没走到它。
    另有两档是**不可判定**（Pack 产物不可得、材料身份不可比）——它们**不得**被读成
    「没进 Pack」，更**不得**被读成「来源里没有这张表」。
    """
    reasons = set(RB.LOCATED_NOT_IN_PACK_REASONS)
    check(reasons == {"not_released", "released_not_material", "not_in_run_pack",
                      "pack_unavailable", "identity_unavailable"},
          f"未进 Pack 列：typed 原因是**闭集**（实得 {sorted(reasons)}）")
    check(reasons <= set(RB.LOCATED_NOT_IN_PACK_LABELS),
          "未进 Pack 列：逐档有中文标签（漏标签会在渲染时露成英文键名）")
    check(RB.SCHEMA_VERSION == "business-material-readback/3",
          f"未进 Pack 列：本列的引入即 `/3` 版本（实得 {RB.SCHEMA_VERSION}）")

    def _rec(rid, *, material_id, state="material_ready", reason=None,
             page=19, title="1）营业收入整体情况", node="n-root") -> dict:
        return {"release_id": rid, "local_proof_id": "tlpp-" + rid,
                "table_id": "to4-" + rid, "table_locator": "loc-to4-" + rid,
                "material_id": material_id, "host_evidence_id": node,
                "page_number": page, "table_title": title,
                "structure_state": "complete", "column_count": 7,
                "host_read_relation": "read_root",
                "matched_node_title": "（1） 营业收入构成",
                "table_channel_state": state, "material_refusal_reason": reason}

    channel = {
        "release_rule_version": "gto-3", "material_version": "gtm-1",
        "objects": [
            _rec("gtr-in", material_id="mat-gtb-aaa"),
            _rec("gtr-out", material_id="mat-gtb-bbb", page=20,
                 title="（5） 营业成本构成"),
            _rec("gtr-nomaterial", material_id="mat-gtb-ccc",
                 state="released_not_material", reason="no_numeric_body_row",
                 page=25, title="1）营业收入及营业成本整体情况"),
            _rec("gtr-noident", material_id=None, page=50,
                 title="表 5-10发行人主营业务收入构成表"),
        ],
        "unreleased_candidates": [
            {"host_evidence_id": "", "table_id": "to4-rejected",
             "table_locator": "loc-to4-rejected", "page_number": 52,
             "table_title": "表 5-11发行人主营业务成本构成表",
             "structure_state": "partial",
             "release_refusal_reason": "structure_not_complete",
             "defect_codes": ["structure_not_complete", "header_row_absent"],
             "unproven_cell_count": 6,
             "node_ids": [], "scope": "document"},
        ],
        "by_node": {}, "unreleased_by_node": {}, "node_titles": {},
    }
    pack = [{"material_id": "mat-gtb-aaa"}]

    column = RB.located_not_in_pack(channel, pack, pack_available=True)
    got = {r["release_id"]: r["reason"] for r in column["rows"]}
    check(column["in_pack_count"] == 1 and "gtr-in" not in got,
          "未进 Pack 列：Pack 里能对上的对象**逐条不列**（只单列计数）")
    check(got.get("gtr-out") == "not_in_run_pack",
          "未进 Pack 列：已成材料但本次 Pack 没走到它 ⇒ `not_in_run_pack`"
          "（成因在派发域／预算，不在来源）")
    check(got.get("gtr-nomaterial") == "released_not_material",
          "未进 Pack 列：已放行但成不了材料 ⇒ **独立**档，不与「Pack 没走到」混")
    check(got.get("gtr-noident") == "identity_unavailable",
          "未进 Pack 列：拿不到材料身份 ⇒ **不可判定**，不得断言「没进 Pack」")
    check(any(r["reason"] == "not_released"
              and r["reason_detail"] == "structure_not_complete"
              and r["scope"] == "document"
              and r["defect_codes"][1] == "header_row_absent"
              for r in column["rows"]),
          "未进 Pack 列：未获放行的表也要列，带逐表证明 typed 理由与逐条缺陷码，"
          "且逐条标出**作用域＝整份文档**（挡下 ≠ 不存在，也不归本栏）")
    check(column["count"] == 4
          and column["reason_counts"] == {
              "not_in_run_pack": 1, "released_not_material": 1,
              "identity_unavailable": 1, "not_released": 1},
          f"未进 Pack 列：五档计数逐档可见（实得 {column['reason_counts']}）")

    #: **反例**：页号与表题都相同、只有材料身份不同 ⇒ **不得**按页号/表题模糊配对。
    lookalike = RB.located_not_in_pack(
        {"objects": [_rec("gtr-look", material_id="mat-gtb-zzz")],
         "unreleased_candidates": []},
        [{"material_id": "mat-gtb-aaa"}], pack_available=True)
    check(lookalike["reason_counts"].get("not_in_run_pack") == 1
          and lookalike["in_pack_count"] == 0,
          "未进 Pack 列：配对**只按材料身份**——页号/表题相同也不算对上"
          "（否则两张不同的表会被认成同一张）")

    #: Pack 不可得 ⇒ 一律**不可判定**，且**不得**出现 `not_in_run_pack`。
    unavailable = RB.located_not_in_pack(
        {"objects": [_rec("gtr-out", material_id="mat-gtb-bbb")],
         "unreleased_candidates": []}, [], pack_available=False)
    check(unavailable["available"] is False
          and unavailable["reason_counts"] == {"pack_unavailable": 1}
          and "not_in_run_pack" not in unavailable["reason_counts"],
          "未进 Pack 列：Pack 产物不可得 ⇒ `pack_unavailable`，**不得**记成没进 Pack")

    #: 免责必须写清「未进 Pack ≠ 来源里没有这张表」，且不得读成数字权威。
    note = RB.LOCATED_NOT_IN_PACK_NOTE
    check("来源里没有这张表" in note and "可核查检索" in note,
          "未进 Pack 列：免责写清「未进 Pack ≠ 来源里没有这张表」（那要可核查检索）")
    check("numeric_authority=False" in note and "reading_material=True" in note,
          "未进 Pack 列：本列不判资格、不产材料、不给数字授权")
    check("只在" in note or "只回答" in note,
          "未进 Pack 列：免责写清本列只答「卡在哪一步」，不答「这张表是不是本栏的」")

    #: **两个出口**（JSON 与 Markdown）必须是同一份判读：Markdown 里能读到逐条原因。
    cell_full = {
        "state": "table_not_released", "state_label": "表格拒发",
        "states_observed": ["table_not_released"],
        "located": [{"node_id": "n-root"}], "read_texts": [],
        "navigation": {"read_root_titles": ["（1） 营业收入构成"],
                       "fallback_reason": None, "status": "selected",
                       "supplement_root_titles": []},
        "table_channel": channel,
        "located_not_in_pack": column,
        "pack_tables": pack, "pack_tables_available": True,
        "fact_eligibility": "unqualified",
        "chapter_scope": {}, "own_chapter_id": None, "own_chapter_title": None,
    }
    rows = RB._matrix({RB.DOCUMENT_ORDER[0]: {
        "aspects": {RB.TABLE_GAP_ASPECTS[0]: cell_full}}})
    target = [row for row in rows if row["aspect_id"] == RB.TABLE_GAP_ASPECTS[0]]
    check(len(target) == 1 and target[0]["cells"]
          and target[0]["cells"][0]["located_not_in_pack"] is column,
          "未进 Pack 列：矩阵单元格**原样带过**同一份判读（不各算一套）")
    md = RB.render_matrix_md({"navigation_profile_rule_version": "np-1",
                              "matrix": rows})
    check("已定位但未进 Pack" in md
          and "已成材料但本次 Pack 里没有" in md,
          "未进 Pack 列：Markdown 出口也有这一列，且用**中文档名**（不漏成英文键名）")
    #: 逐条留痕的判据取自**夹具自己**的读数：只钉字面量会变成「改夹具就得改断言」，
    #: 而真正要守的是「Markdown 逐条把表题与 typed 理由带出来，不是只给一个计数」。
    unreleased = channel["unreleased_candidates"][0]
    check(unreleased["table_title"] and unreleased["table_title"] in md
          and unreleased["release_refusal_reason"] in md
          and all(code in md for code in unreleased["defect_codes"]),
          "未进 Pack 列：Markdown 逐条带出表题与 typed 理由"
          "（只给计数等于漏记）")
    details.append("NOTE 未进 Pack 列：五档 typed 原因 / 两档不可判定 / 反例 / 两出口均已断言")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    _own_chapter(check, details)
    _chapter_split(check, details)
    _marks(check, details)
    _projection(check, details)
    _table_ledger(check, details)
    _table_channel(check, details)
    _located_not_in_pack(check, details)
    _support_judgment(check, details)
    skipped += _real_documents(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

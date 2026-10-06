"""Eval: M930-3 v7 —— tree inspection 工具的**图侧单通道**合格表 + Pack 材料返回分支。

用法: python -X utf8 -m evals.test_m930_3_tree_table_branch

§0.18 W8 把目标表的**来源**换成一条单通道：同一次 graph-side 读数 → 逐表完整证明
（`gto-3` / `tlp-1`）→ 复用既有工具 / Pack / Writer 投递。本模块钉的是这条通道在
**工具面**上的行为（v6 时代的 `tobj-*` / `tom-1` 版本已不是生产调用方）。

覆盖（全部结构性、零公司名/页码/关键词规则）：

- **B1 人口有界**：只有**本次已定位的宿主块**里的已放行表进人口；同一文档里别的块即使
  有合格表也不返回（不按整篇文档扫表）。反例是**纯人口**效果：另一张表既没被拒、也没
  被截断，只是没被定位。
- **B2 三套身份**：放行身份（`release_id`）/ 逐表证明身份（`local_proof_id`）/ 表身份
  （`table_id`）与**材料身份**（`material_id`）互不顶替；对象本体不带材料侧的键。
- **B3 材料可解析**：工具返回的 ref 由**同一份构建结果**的 resolver 解析；解析出的字节
  哈希与材料记录一致；信封逐字声明「是阅读材料 / 不是数字权威 / 不是财务权威」，且正文
  是这张表的逐字原文（渲染分隔符逐字写在读视图里）。
- **B4 逐格读视图**：材料读视图给的是**逐格列表**（业务行标签 + 指标列路径 + 逐字原值 +
  格状态），不是"一行一根字符串"的压平文本；表头格不进列表；格坐标只给中立坐标，
  不在这里产出 `loc-1` 定位对象（那套词表只有一处，在写作侧）。
- **B5 声明读数**：单位逐字取自图侧读数；期间**读不到**时带 typed 缺席原因，不是猜一个
  期间出来（后果 —— 只出自表的数字不得写入正文 —— 由写作侧 fail-closed 承担）。
- **B6 文档级被拒 ≠ 每张表都被否决**（§0.19 换轴）：文档级拒发逐字在账上、`document_qualified`
  为假，但一张已独立完整证明的表**仍**经工具→Pack 返回。
- **B7 拒绝逐条留痕且**人口口径写明是**文档级**：图侧没放行（`gtr-*` 逐表证明未过）与
  放行了却成不了材料（`gtm-1` 材料门）是两本账；拒绝**不**因为"与本次 selector 无关"而
  消失 —— 无关页面上的整文档问题仍留在账上。
- **B8 上界如实登记**：超过工具级上界只减少返回（对象与材料一起不返回），截断写进
  `skipped`（未返回 ≠ 该块没有表）。
- **B9 没有对象可证明**：快照从未诞生 ⇒ `no_table_objects`、零放行、零逐表拒绝、
  `accounting_balanced=False`。这**不是**"某张表不合格"。
- **B10 真实世界的主要情形**：结构不完整的表**一律**拒发。冻结的真实读数里四份演示文档
  的每一张表都是 `partial` + 零表头行，因此这条通道在真实文档上**必须**给出"零放行表"。
  本检查用同一个 reason 字面量把这个结局钉死在夹具上（反例，不是"缺口被绕过"的证明）。
  真实文档上的逐表读数由 `scripts/run_m930_3_cited_chain.py` 的读回产物承载 —— 那条路径
  要建整份文档的图侧快照（分钟级），不适合放进回归套件。

夹具的真伪边界（必须先说清）
----------------------------------------------------
**表对象是真的**：全部经 `evals/test_graph_table_release` 的夹具构造，走生产
`TS.TableCellSourceRef.create()` / `TableCellV4.create()` / `TableObjectV4.create()`，
`tc-3` 的每一条构造期不变量都真的跑过；本模块**复用同一个接缝**（`_FixtureSource`）
而不是另造一份，免得"两张表的夹具像不像"变成一个迟早会漂移的隐式约定。

Evidence 块是**合成**的：块 id 与字符区间取自 `FIX._ref`（`gtr-blk-<页>-<行>`），块文本是
那一行各格文本的拼接（因此每格声明的区间都真落在它自己那段文本内）。它们不主张任何真实
文档内容。

只读：不写任何文件、不建库、不联网、不调 LLM。
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_graph_table_release as FIX  # noqa: E402
from harness import graph_table_materials as GTM   # noqa: E402
from harness import graph_table_release as GTR     # noqa: E402
from harness import topic_schema as TS             # noqa: E402
from harness import tree_materials as TM           # noqa: E402
from harness import tree_tools as TT               # noqa: E402

REPO = Path(__file__).resolve().parent.parent

COMPANY = "c_demo"
#: 本模块自带的期望字面量（不复用生产常量当期望，否则常量被改时测试跟着绿）。
_GTM_AXIS = "gtm-1"
_GTO_AXIS = "gto-3"
_ENVELOPE_KIND = "graph-table-material-v1"
_OBJECT_KIND = "released_graph_table"
_OBJECT_POPULATION = "located_hosts"
_REFUSAL_POPULATION = "document"
_PERIOD_ABSENCE = "graph_side_has_no_period_reading"
_CELLS_KEY = "cells"
_QUALIFICATION_KEYS = frozenset({
    "reading_material", "numeric_authority", "financial_authority_claimed",
    "permitted_use", "exclusions",
})


@dataclass
class _Block:
    evidence_block_id: str
    text: str
    page_number: int = 1
    block_index: int = 0
    company_id: str = COMPANY
    document_id: str = FIX._DOC_ID
    document_version: str = FIX._DOC_VERSION
    evidence_set_version: str = FIX._EVIDENCE_SET_VERSION
    evidence_type: str = "text"
    structured_payload: dict | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _blocks_for(tables) -> list[_Block]:
    """合成表 → 生产链要读的 Evidence 块（块 id / 区间由 `FIX._ref` 决定）。

    同一条块 id 的文本 = 那一行**全部**格文本以制表符拼接：每格声明的
    `evidence_char_range` 都落在这一段里（`_verify_cell_sources` 的第四条判据），且块文本
    逐字含有那些格文本 —— 不是编一段"长度够"的占位串。
    """
    texts: dict[str, list[str]] = {}
    meta: dict[str, tuple[int, int]] = {}
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    bid = ref.evidence_block_id
                    texts.setdefault(bid, [])
                    if cell.text not in texts[bid]:
                        texts[bid].append(cell.text)
                    meta[bid] = (ref.interval.page_number, ref.interval.line_index)
    return [_Block(bid, "\t".join(items), page_number=meta[bid][0],
                   block_index=meta[bid][1])
            for bid, items in sorted(texts.items(), key=lambda kv: meta[kv[0]])]


class _StubSession:
    """只提供 `release_tables_for` 真正依赖的协作方（块序 + current 绑定 + 图侧来源）。

    人口、放行、材料化**全部**走生产代码（`graph_release_batch` / `graph_table_batch` /
    `release_tables_for` 都是 `TreeInspectionSession` 的方法）；本类只把"这一份文档的
    图侧来源"换成一个带自洽范围读数的夹具（与 `evals/test_graph_table_release` 用的是
    **同一个**接缝），并补上夹具没有的标题路径：读不到导航就是空，不编造。
    """

    def __init__(self, blocks, *, tables=(), source=None,
                 document_id: str = FIX._DOC_ID) -> None:
        self._blocks = list(blocks)
        self._document_id = document_id
        self._binding = TM.CurrentEvidenceBinding(
            company_id=COMPANY, document_id=FIX._DOC_ID,
            document_version=FIX._DOC_VERSION,
            evidence_set_version=FIX._EVIDENCE_SET_VERSION, is_current=True)
        self._fixture_source = (source if source is not None
                                else FIX._source(list(tables)))
        self._graph_release_batch = None
        self._graph_table_batch = None
        self._table_resolver = None

    def ordered_evidence_blocks(self) -> list[_Block]:
        return sorted(self._blocks, key=lambda b: (b.page_number, b.block_index))

    def document_identity(self) -> dict:
        return {"document_id": self._document_id}

    def live_table_source(self):
        return self._fixture_source

    def _table_heading_paths(self, blocks) -> dict:
        # 夹具没有标题树：材料的 `section_path` 因此是空串（读不到 ≠ 编一个）。
        # 表对象与材料的两套身份都不取自这里。
        return {}

    release_tables_for = TT.TreeInspectionSession.release_tables_for
    graph_release_batch = TT.TreeInspectionSession.graph_release_batch
    graph_table_batch = TT.TreeInspectionSession.graph_table_batch
    table_resolver = TT.TreeInspectionSession.table_resolver
    # 静态方法要包一层 `staticmethod`：直接赋函数会把它变成实例方法（多收一个 self）。
    _table_object_view = staticmethod(TT.TreeInspectionSession._table_object_view)


# 表 A：三行两列（一行表头 + 两行数据）。**带逐字单位**，因此"单位读数"有正例。
_GRID_A = (("项目", "本期金额"), ("营业收入", "100"), ("营业成本", "80"))
# 表 B：两行两列，故意与 A 不同形，使"两张表的返回可区分"不靠字符串巧合。
_GRID_B = (("项目", "占比"), ("甲类", "10.0%"))
# 表 C：与 A 同形但**在另一页**，用于上界截断与"另一页的表"两条。
_GRID_C = (("项目", "期末金额"), ("货币资金", "500"))

_UNIT_A = "单位：万元"


def _table_a():
    return FIX._table(grid=_GRID_A, page=30, unit_text=_UNIT_A)


def _table_b():
    return FIX._table(grid=_GRID_B, page=31)


def _table_c():
    return FIX._table(grid=_GRID_C, page=32, unit_text=_UNIT_A)


def _cell_key(table) -> tuple:
    """表的第一条可引用片段键（`(页, 行, 列)`）—— 反例注入点用它指认"同一段字符"。"""
    for row in table.rows:
        for cell in row.cells:
            for ref in cell.source_refs:
                return (ref.interval.page_number, ref.interval.line_index,
                        ref.interval.span_index)
    raise AssertionError("夹具表没有任何可引用片段")


# ---------------------------------------------------------------------------
# B1 人口有界
# ---------------------------------------------------------------------------

def _check_population(check, details) -> None:
    a, b = _table_a(), _table_b()
    session = _StubSession(_blocks_for([a, b]), tables=[a, b])
    objects, refusals, materials, skipped = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])

    check([o["table_id"] for o in objects] == [a.table_id],
          "本次已定位块里的已放行表被返回（且只返回这一张）")
    check(b.table_id not in {o["table_id"] for o in objects},
          "同一份文档里**未定位**块上的表不返回（块在文档里，只是本次没有定位到它）")
    check(refusals == [] and skipped == [],
          "B 的不返回是**纯人口**效果：它既没被拒、也没被截断（不是被筛掉的结果）")
    check(all(o["population_scope"] == _OBJECT_POPULATION for o in objects),
          "表对象逐条写明人口口径是 located_hosts")
    check(objects[0]["host_evidence_id"] == "gtr-blk-30-0"
          and objects[0]["node_ids"] == ["n1"],
          "对象带定位它的宿主块与 node（导航回指）")

    material = materials[0]
    check(len(materials) == 1 and material["material_type"] == "table_context"
          and material["payload_ref"]["version"] == _GTM_AXIS,
          "每张被返回的对象在 table_materials 里有**恰一份**材料，解析语义版本是 gtm-1")
    check(material["release_id"] == objects[0]["release_id"]
          and material["material_id"] != objects[0]["release_id"]
          and material["material_id"] != objects[0]["table_id"]
          and material["material_id"].startswith("mat-gtb-"),
          "材料以放行身份回指对象，但材料身份既不是放行身份也不是表身份")


# ---------------------------------------------------------------------------
# B2 三套身份互不顶替
# ---------------------------------------------------------------------------

def _check_identity(check, details) -> None:
    a, b = _table_a(), _table_b()
    session = _StubSession(_blocks_for([a, b]), tables=[a, b])
    objects, _r, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-1", "node_id": "n7"}])
    if not objects:
        check(False, "身份断言需要至少一个表对象")
        return
    obj = objects[0]
    check(obj["kind"] == _OBJECT_KIND,
          "返回体用类型标记区分表对象与正文材料")
    check(len({obj["release_id"], obj["local_proof_id"], obj["table_id"]}) == 3
          and obj["table_locator"].startswith("loc-to4-")
          and obj["release_id"].startswith("gtr-")
          and obj["local_proof_id"].startswith("tlpp-"),
          "放行身份 / 逐表证明身份 / 表身份是**三条**正交身份，都要在")
    check(all(key not in obj for key in ("material_id", "material", "content_hash",
                                         "payload_ref", "authority_assessment")),
          "对象本体**不带**材料侧的键（两条账不混在一处）")
    check(obj["release_rule_version"] == _GTO_AXIS
          and obj["structure_state"] == "complete"
          and obj["column_count"] == 2 and obj["row_count"] == 3
          and obj["body_row_count"] == 2 and obj["cell_count"] == 6,
          "对象带放行规则版本与逐表计数（可复核、可升版）")
    check(obj["content_qualification"]["reading_material"] is True
          and obj["content_qualification"]["numeric_authority"] is False
          and obj["content_qualification"]["financial_authority_claimed"] is False,
          "表对象显式声明：是阅读材料，不是数字权威，不是财务权威")
    check(obj["local_proof_summary"]["locally_proven"] is True
          and obj["local_proof_summary"]["residual_char_count"] == 0
          and obj["local_proof_summary"]["defects"] == [],
          "对象带逐表证明读数：本表自证通过、零残余、零缺陷")
    check(obj["evidence_block_ids"] == ["gtr-blk-30-0", "gtr-blk-30-1",
                                        "gtr-blk-30-2"],
          "对象逐条列出这张表引用的 Evidence 块（跨块表不会漏列）")
    check(len(materials) == 1 and materials[0]["node_ids"] == ["n7"],
          "材料与对象共用同一次定位的导航回指")


# ---------------------------------------------------------------------------
# B3 材料可解析 + 信封逐字
# ---------------------------------------------------------------------------

def _resolved_envelope(session, material) -> dict | None:
    ref = TS.MaterialPayloadRef.from_dict(material["payload_ref"])
    resolved = session.table_resolver.resolve(ref)
    if resolved is None:
        return None
    return json.loads(resolved.payload_bytes.decode("utf-8"))


def _check_material_envelope(check, details) -> None:
    a, b = _table_a(), _table_b()
    session = _StubSession(_blocks_for([a, b]), tables=[a, b])
    _o, _r, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    material = materials[0]
    ref = TS.MaterialPayloadRef.from_dict(material["payload_ref"])
    resolved = session.table_resolver.resolve(ref)
    check(resolved is not None,
          "工具返回的材料 ref 由**同一份构建结果**的解析器解析（同一份字节）")
    if resolved is None:
        return
    check(hashlib.sha256(resolved.payload_bytes).hexdigest() == material["content_hash"],
          "材料记录的内容哈希与解析出的字节一致")
    envelope = json.loads(resolved.payload_bytes.decode("utf-8"))
    check(envelope["envelope_kind"] == _ENVELOPE_KIND
          and envelope["material_payload_version"] == 1
          and envelope["object_type"] == "table_context"
          and envelope["authority_identity"] == material["source_identity"],
          "信封种类 / 载荷版本 / 对象类型 / 权威身份逐字自洽")
    check(envelope["evidence_id"] == "gtr-blk-30-0"
          and envelope["source_content_hash"]
          == hashlib.sha256(_blocks_for([a])[0].text.encode("utf-8")).hexdigest()
          and envelope["source_content_hash_scope"] == "host_block_only",
          "信封的来源哈希锚在**宿主块**上，并把它的作用域逐字写明（跨块正文另有逐条来源）")
    policy = envelope["reading_policy"]
    check(set(policy) == _QUALIFICATION_KEYS and policy["reading_material"] is True
          and policy["numeric_authority"] is False
          and policy["financial_authority_claimed"] is False
          and policy["permitted_use"] == "navigable_reading_material_only",
          "信封逐字声明：是阅读材料、不是数字权威、不是财务权威（三条都进 payload 哈希）")
    check("no_numeric_authority" in policy["exclusions"]
          and "no_computation_by_llm" in policy["exclusions"]
          and "not_a_document_level_qualification" in policy["exclusions"],
          "排除项逐条带出（读的人不必从缺陷去猜这一份读不出什么）")
    check(envelope["content"]["text"]
          == "\n".join([_UNIT_A, "项目\t本期金额", "营业收入\t100", "营业成本\t80"]),
          "材料正文是这张表的逐字原文（含逐字单位；分隔符是渲染，逐字写在读视图里）")
    check(envelope["content"]["structured_payload"]["rendering"]
          == {"cell_separator": "\t", "row_separator": "\n",
              "value_text_rendering": "whitespace_collapsed"},
          "渲染用的分隔符与归一化方式逐字进读视图（正文与格子里的原值可比）")
    check(envelope["graph_table"]["release_id"] == material["release_id"]
          and envelope["graph_table"]["host_evidence_id"] == "gtr-blk-30-0"
          and envelope["graph_table"]["host_evidence_ids"]
          == ["gtr-blk-30-0", "gtr-blk-30-1", "gtr-blk-30-2"],
          "信封里三套身份与宿主集合都在（材料不必回头问工具）")


# ---------------------------------------------------------------------------
# B4 逐格读视图
# ---------------------------------------------------------------------------

def _check_cells(check, details) -> None:
    a, b = _table_a(), _table_b()
    session = _StubSession(_blocks_for([a, b]), tables=[a, b])
    _o, _r, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    envelope = _resolved_envelope(session, materials[0])
    if envelope is None:
        check(False, "逐格断言需要能解析的材料")
        return
    view = envelope["content"]["structured_payload"]
    cells = view[_CELLS_KEY]
    check(len(cells) == 4 and all(
        {"row_index", "column_index", "row_label", "column_header",
         "column_header_path", "value_text", "value_text_raw", "cell_state",
         "value_text_rendering"} <= set(cell) for cell in cells),
        "读视图给的是**逐格**列表（一行一根字符串的压平文本不是合格原始表的读法）")
    check(all(cell["row_role"] != "header" for cell in cells),
          "表头格不进逐格列表（表头文字里的数字不得自己授权自己）")
    index = {(cell["row_index"], cell["column_index"]): cell for cell in cells}
    check(index[(1, 0)]["row_label"] == "营业收入"
          and index[(1, 0)]["row_label_source"] == "lead_cell"
          and index[(1, 1)]["column_header"] == "本期金额"
          and index[(1, 1)]["column_header_path"] == ["本期金额"]
          and index[(1, 1)]["value_text"] == "100"
          and index[(1, 1)]["cell_state"] == "citable",
          "同一格里同时给出业务行标签、指标列路径与逐字原值（数字要落回具体的格）")
    check(view["header_rows"] == [["项目", "本期金额"]]
          and view["column_count"] == 2,
          "表头行逐字留在读视图里（列标签的来源，不是数字的来源）")
    check(view["cell_owner"] == "evidence:gtr-blk-30-0"
          and view["table_ref"] == a.table_id,
          "格坐标只给中立的归属载体与表引用（loc-1 词表不在 harness 里）")
    check(all("cell_locator" not in cell for cell in cells),
          "harness **不**产出 loc-1 定位对象（同一件事只有一个名字，由写作侧渲染）")
    check(view["cell_number_authority"] == "not_granted"
          and view["numeric_authority"] is False
          and view["numeric_authority_reason"] == view["numeric_authority_reason"]
          and view["numeric_authority_reason"] != "",
          "逐格读数**没有**取得格级数字权威，并逐字写明理由")
    check(view["permitted_use"] == "navigable_reading_material_only"
          and "no_support_from_cell_values" in view["exclusions"],
          "格值不得直接充当事实支撑（要支撑走预验证权威事实或合格 Claim）")


# ---------------------------------------------------------------------------
# B5 声明读数（单位正例 + 期间缺席）
# ---------------------------------------------------------------------------

def _check_declarations(check, details) -> None:
    a, b = _table_a(), _table_b()
    session = _StubSession(_blocks_for([a, b]), tables=[a, b])
    _o, _r, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    envelope = _resolved_envelope(session, materials[0])
    if envelope is None:
        check(False, "声明断言需要能解析的材料")
        return
    view = envelope["content"]["structured_payload"]
    check(view["unit"] == _UNIT_A and view["unit_text"] == _UNIT_A
          and view["declaration_reading"]["unit"]
          == {"value": _UNIT_A, "source": "table.unit_text"},
          "单位逐字取自图侧读数，并写出来路")
    check(view["scope"] == "" and view["declaration_reading"]["scope"]
          == {"value": "", "source": "table.title_blocks"},
          "表题读不到时声明读数是空串（空串是**结论**，另由 declaration_reading 写明来路）")
    check(view["period"] == ""
          and view["declaration_reading"]["period"]["source"] is None
          and view["declaration_reading"]["period"]["absence"] == _PERIOD_ABSENCE,
          "期间读不到时带 typed 缺席原因，**不**猜一个期间出来")
    check(all(set(entry) >= {"value", "source"}
              for entry in view["declaration_reading"].values()),
          "每个声明读数都带 value 与 source 两栏（读不到与读成空串不会混成一个）")


# ---------------------------------------------------------------------------
# B6 文档级被拒 ≠ 每张表都被否决
# ---------------------------------------------------------------------------

def _check_document_refusal_does_not_veto(check, details) -> None:
    a, b = _table_a(), _table_b()
    source = FIX._refused_source([a, b])
    session = _StubSession(_blocks_for([a, b]), source=source)
    batch = session.graph_release_batch()
    check(batch["batch_state"] == "accounted"
          and batch["document_qualified"] is False
          and isinstance(batch.get("document_refusal"), dict)
          and batch["document_refusal"].get("refusal_kind"),
          "文档级被拒时批次仍是 accounted：文档级终态逐字在账上，不冒充'没有对象可证明'")
    check(batch["released_table_count"] == 2 and batch["refused_table_count"] == 0
          and batch["accounting_balanced"] is True,
          "文档级被拒**不**自动否决两张已独立完整证明的表（§0.19 的判据换轴）")
    objects, refusals, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    check(len(objects) == 1 and len(materials) == 1
          and objects[0]["table_id"] == a.table_id,
          "被拒文档里一张自证通过的表仍经工具→Pack 返回")
    check(refusals == [],
          "本次落点上没有未通过自身证明的表 ⇒ 零逐表拒绝")
    qualified = _StubSession(_blocks_for([a, b]),
                             source=FIX._source([a, b])).graph_release_batch()
    check(qualified["document_qualified"] is True
          and qualified["batch_id"] != batch["batch_id"],
          "批次身份绑定文档级终态：同一批表在两种文档终态下不是同一次签发")


# ---------------------------------------------------------------------------
# B7 拒绝逐条留痕 + 人口口径
# ---------------------------------------------------------------------------

def _check_refusals(check, details) -> None:
    # 7a：图侧就没放行（`tlp-1` 逐表证明未过）。反例注入点只改**读数**：把 B 自己主张的
    # 一段字符判成别的表所有 ⇒ B 的矩形里出现跨表重叠字符 ⇒ 该表拒发。
    a, b = _table_a(), _table_b()
    b_key = _cell_key(b)
    source = FIX._source([a, b], foreign_owners={b_key: "other_table"})
    session = _StubSession(_blocks_for([a, b]), source=source)
    objects, refusals, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    check([o["table_id"] for o in objects] == [a.table_id],
          "同一份文档里另一张表未通过自身证明，不影响这张表的返回")
    check(len(refusals) == 1 and refusals[0]["released"] is False
          and refusals[0]["table_id"] == b.table_id,
          "未通过自身证明的表逐条留下拒绝记录（不是静默丢弃）")
    check(refusals[0]["population_scope"] == _REFUSAL_POPULATION
          and refusals[0]["reason"] in refusals[0]["problems"]
          and len(refusals[0]["problems"]) == len(set(refusals[0]["problems"])),
          "拒绝记录逐条带 typed 缺陷，并写明人口口径是**文档级**")
    check("scope_cross_table_overlap" in refusals[0]["problems"]
          and refusals[0]["release_rule_version"] == _GTO_AXIS,
          "跨表重叠字符使该表拒发，缺陷逐条在册（不得用重叠优先级吞字符）")
    check(all("numeric_authority" not in r and "release_id" not in r
              for r in refusals),
          "拒绝记录不携带任何权威 / 身份声明")
    check(refusals[0]["table_id"] != a.table_id
          and "gtr-blk-30-0" not in str(refusals[0].get("evidence_block_ids") or []),
          "拒绝**不**因为'与本次 selector 无关'而消失（无关表的问题仍留在账上）")
    check(len(materials) == 1 and materials[0]["release_id"] == objects[0]["release_id"],
          "被拒的表不产出材料，被放行的表恰产出一份")

    # 7b：放行了却成不了材料（`gtm-1` 材料门）。C 在文档里、逐表证明通过，但它的来源块
    # **不在**本次块集合里 ⇒ 来源无从回查 ⇒ typed 材料拒绝（另一本账）。
    c = _table_c()
    source2 = FIX._source([a, c])
    session2 = _StubSession(_blocks_for([a]), source=source2)
    objects2, refusals2, materials2, _s2 = session2.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    check(len(objects2) == 1 and objects2[0]["table_id"] == a.table_id,
          "同文档里另一张表进不了材料，不影响这张表的返回")
    check(len(refusals2) == 1
          and refusals2[0].get("kind") == "graph_table_material_refusal"
          and refusals2[0]["reason"] == "source_block_unavailable"
          and refusals2[0]["population_scope"] == _REFUSAL_POPULATION,
          "放行了却成不了材料是**另一本账**：typed 材料拒绝逐条留痕")
    check(len(materials2) == 1
          and materials2[0]["release_id"] == objects2[0]["release_id"],
          "材料只有被放行**且**成得了材料的那一张")


# ---------------------------------------------------------------------------
# B8 上界如实登记
# ---------------------------------------------------------------------------

def _check_cap(check, details) -> None:
    a, c = _table_a(), _table_c()
    session = _StubSession(_blocks_for([a, c]), tables=[a, c])
    objects, _r, materials, skipped = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"},
         {"parent_evidence_id": "gtr-blk-32-0", "node_id": "n2"}], max_objects=1)
    check(len(objects) == 1 and len(materials) == 1,
          "超过工具级上界只减少返回（对象与材料一起不返回）")
    check(any(s.get("reason") == "over_max_table_objects" for s in skipped),
          "上界截断如实登记在 skipped（未返回 ≠ 该块没有表）")
    envelope = _resolved_envelope(session, materials[0])
    check(envelope is not None
          and envelope["content"]["structured_payload"]["row_count"] == 3,
          "被返回的对象内部原文未被截断")


# ---------------------------------------------------------------------------
# B9 没有对象可证明
# ---------------------------------------------------------------------------

def _check_no_objects(check, details) -> None:
    session = _StubSession([], source=FIX._unbuilt_source())
    batch = session.graph_release_batch()
    check(batch["batch_state"] == "no_table_objects"
          and batch["declared_table_count"] is None
          and batch["accounting_balanced"] is False
          and batch["released"] == [] and batch["refusals"] == [],
          "快照从未诞生 ⇒ no_table_objects：零放行、零逐表拒绝，读数逐字带出")
    check(isinstance(batch.get("document_refusal"), dict)
          and batch["document_refusal"].get("refusal_kind")
          == "final_material_build_failed",
          "文档级拒发读数逐字进入批次（不自造一个'某张表不合格'）")
    objects, refusals, materials, skipped = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    check((objects, refusals, materials, skipped) == ([], [], [], []),
          "没有对象可证明时，本次落点既没有对象也没有拒绝记录（两者都不该被编出来）")


# ---------------------------------------------------------------------------
# B10 真实文档：同一条通道跑一遍，结论逐条报出
# ---------------------------------------------------------------------------

def _check_partial_refused(check, details) -> None:
    """B10 真实世界的**主要**情形：结构不完整的表一律拒发（fail-closed）。

    冻结的真实读数（`evaluation/results/tree_table_ts5_20260919T195556Z/`）里，四份演示
    文档的**每一张**表都是 `structure_state="partial"`、零表头行、`structure_state_reason`
    是 `cell_provenance_incomplete`、`header_absence_reason` 是 `key_value_form_without_header`。
    因此这条路径在真实文档上**必须**给出"零放行表"，而不是放行一张读不全的表。

    本检查用**同一个** reason 字面量在夹具上钉死这个结局：`tlp-1` 的两个缺陷（结构不是
    complete、表头行缺席）使该表拒发、不产出材料、对象也不从工具面返回。它是**反例**，
    不是"缺口被绕过"的证明。
    """
    a = FIX._table(grid=_GRID_A, page=30, unit_text=_UNIT_A,
                   structure_state="partial",
                   structure_state_reason="cell_provenance_incomplete")
    session = _StubSession(_blocks_for([a]), source=FIX._source([a]))
    batch = session.graph_release_batch()
    check(batch["batch_state"] == "accounted" and batch["released"] == []
          and batch["refused_table_count"] == 1,
          "结构不是 complete 的表在真实文档上会**全部**走到这条路上：零放行")
    objects, refusals, materials, _s = session.release_tables_for(
        [{"parent_evidence_id": "gtr-blk-30-0", "node_id": "n1"}])
    check((objects, materials) == ([], []),
          "未通过自身证明的表**不得**从工具面返回，也不得产出材料")
    check(len(refusals) == 1
          and refusals[0]["reason"] == "structure_not_complete"
          and "cell_provenance_incomplete" == refusals[0]["structure_state_reason"],
          "拒发理由与图侧读到的结构原因逐字带出（不是一句笼统的'不合格'）")
    steps = refusals[0]["local_proof"]["steps"]
    check(steps["constructed_complete"] is False
          and steps["self_proven"] is True
          and steps["in_pack"] == "out_of_layer"
          and steps["writer_received"] == "out_of_layer"
          and steps["reader_presented"] == "out_of_layer"
          and steps["numeric_authority_granted"] is False,
          "七步验收停在『已构造但不完整』：③以后逐项是 out_of_layer，"
          "不得把没跑到的步骤报成已通过")


def _check_grid_reuse(check, details) -> None:
    """夹具自身的一致性：两张表被真的建成两张**不同身份**的表。"""
    a, b = _table_a(), _table_b()
    check(a.table_id != b.table_id and a.table_locator != b.table_locator,
          "夹具的两张表身份不同（否则'另一张表不返回'的断言没有意义）")
    check(_blocks_for([a, b])[0].text == "项目\t本期金额",
          "块文本逐字含有那一行的格文本（不是编一段长度够的占位串）")
    check(_blocks_for([a])[0].evidence_block_id != "gtr-blk-0",
          "块 id 是文档级身份（带页），两张表不会折进同一条块 id")


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

    _check_grid_reuse(check, details)
    _check_population(check, details)
    _check_identity(check, details)
    _check_material_envelope(check, details)
    _check_cells(check, details)
    _check_declarations(check, details)
    _check_document_refusal_does_not_veto(check, details)
    _check_refusals(check, details)
    _check_cap(check, details)
    _check_no_objects(check, details)
    _check_partial_refused(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    import json as _json
    import time as _time

    _t0 = _time.time()
    _out = main()
    _out["seconds"] = round(_time.time() - _t0, 1)
    print(_json.dumps(_out, ensure_ascii=False, indent=2))

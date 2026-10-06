"""只读审计：`company_business` 逐 (栏目, 文档) 取材矩阵 + 面向人的材料包读回页。

本模块**只读**、**不改运行时**、**不产 Pack 材料**。它回答两件事：

1. 对三份上传材料（2024 年报 / 2025 年报 / 2026 募集说明书）的每一个
   `company_business` 栏目，从**原 PDF 章节/原文**一路追到 Pack 保留材料与 Writer
   收到情况，并落成 7 态之一（`STATE_LABELS`）。
2. 把同一批事实按**业务结构**（主营业务与产品 / 经营模式及产业链 / 收入成本毛利 /
   客户与供应商）重排成一张人可以读的回读页；表格只能进「只读诊断视图」，带来源
   定位与拒发理由，**不得冒充** Pack 材料 / 数字事实 / 可发布正文。

为什么要一个**独立定位器**：本页是判据，不是复述。若它复用导航的候选/读根选择逻辑，
机制漏读时它也会一起漏，于是「材料不存在」与「机制没去读」永远分不开。因此定位器只
用 Contract 声明文本（`requirement_text` 主体头 + `required_fields`）在标题树上做
**标签段相等 / 主体邻接**匹配，完全不看导航决策；两侧结果对不上时，差额就是结论本身。

守恒口径：**材料份数 / 来源登记数 / 标题命中数 / Pack retained 状态都不能替代栏目覆盖**。
逐 (栏目, 文档) 的状态由「定位到的原文位置 × 是否真被读取 × 读到的文本完整性」决定。

用法::

    python -X utf8 -m evaluation.business_material_readback [--repo DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

#: `/2`：新增**表对象信道**清点。`/1` 只有 span 层一条信道，于是「这一栏的表格」被
#: 读成单一结论，而真实供给是两条信道并行的（见 `TABLE_CHANNEL_NOTE`）。本次另加：
#: 逐 (栏目, 文档) 的已放行对象 / 已成材料 / typed 拒绝原因，以及可选的 run 侧去向
#: （`material_pack.json` 里的 `table_context` 材料）。判据一字未减：能读 ≠ 数字有权威。
#: `/3`：轴三增列「本栏**已定位但未进 Pack**的表对象」逐条 ＋ typed 原因
#: （`located_not_in_pack`）。`/2` 只有栏级计数（放行几条、Pack 几条），读的人无法回答
#: 「这一栏定位到的那张表，到底卡在哪一步」——放行门？材料化？还是本次 Pack 没走到它？
#: 本次把这三态**逐对象**拆开并各带 typed 原因。判据一字未减：能读 ≠ 数字有权威，
#: 「未进 Pack」也 ≠「来源里没有这张表」。
SCHEMA_VERSION = "business-material-readback/3"
TOPIC_ID = "company_business"

#: 只读诊断视图里，表体原文的**展示上限**（超出只在 JSON 里留全量 + 摘要指纹）。
DIAGNOSTIC_EXCERPT_CHARS = 1200
#: 「短到就是一个标题/字段名」的长度阈值（与导航的短标题口径同族，独立声明）。
TITLE_LIKE_MAX_CHARS = 40
#: 一个未读位置至少要带这么多字符，才算"漏读了实料"而不是"漏读了一个空标题"。
MIN_MATERIAL_CHARS = 20

#: 逐 (栏目, 文档) 的七个状态。顺序即业务口径的**优势序**（前 = 越好）。
#:
#: `table_not_released` 排在 `partial_body` **之前**（M930-3 anp-9 之后收紧）：本栏
#: 读集里存在**表体拒发残差**时，它是这一栏最该被看见的事实。原先正文残差一出现就把
#: 头条换成「正文片段不完整」——一旦别处（或上提补读）拿到经营正文，表体拒发这件事
#: 就从头条上消失了，而那正是演示主营业务内容门**不得**被绕过的那条缺口。两个状态
#: 同时成立时仍然都记在 `states_observed` 里，不藏事实；这里只改谁当头。
STATES = (
    "complete_material",
    "material_complete_fact_not_qualified",
    "table_not_released",
    "partial_body",
    "title_or_form_only",
    "located_not_read",
    "not_navigated",
)
STATE_LABELS = {
    "complete_material": "取得完整相关材料",
    "material_complete_fact_not_qualified": "完整材料存在但事实尚未取得资格",
    "partial_body": "正文片段不完整",
    "table_not_released": "表格拒发",
    "title_or_form_only": "只读到标题或勾选行",
    "located_not_read": "已定位但未读取",
    "not_navigated": "未导航到",
}
STATE_RANK = {code: len(STATES) - i for i, code in enumerate(STATES)}

#: 逐 aspect 的**原文支持**判据（与"材料到达"**不同的另一条轴**）。
#:
#: 七个 `STATES` 回答的是「这一栏的材料到手了没有」；它**不**回答「这一栏的业务要求
#: 有没有被实质支持」。只证明"选中的读集里有正文、且该读集内没有未读残差"是不够的：
#: 六项经营模式同时命中同一节「经营模式」时，六个单元格都会显示"取得完整相关材料"，
#: 但那是**一段材料**，不是六项各自的支撑。因此本轴单独判，且**分开展示、分开计数**。
#:
#: 判据只有原文包含（两侧同口径规范化；不做分词、不做语义推断）：
#: 主体头（`requirement_text` 括号前的头部）与至少一个 `required_fields` 项是否
#: **逐字**出现在本栏自己读集里的**实质**正文（非标题 / 非勾选行）中。
SUPPORT_STATES = (
    "support_established",
    "support_established_shared",
    "support_partial",
    "support_absent",
    "support_no_text",
)
SUPPORT_LABELS = {
    "support_established": "原文支持已建立（非共享读集）",
    "support_established_shared": "共同读集内逐项可回溯，须逐项复核",
    "support_partial": "仅部分声明词出现在读集正文",
    "support_absent": "读集正文里没有本栏主体与声明字段",
    "support_no_text": "无可判读正文",
}
#: 本轴**不**给出"通过/不通过"的单值结论：`support_established*` 仍可能来自一段被多栏
#: 共享的正文，是否算"该栏取得"由审阅侧按 Contract 的 `coverage_rules` 判。
SUPPORT_CAVEAT = (
    "本轴是机械的**原文包含**判据（两侧同口径规范化，不做分词、不做语义推断）："
    "它只回答「本栏自己读到的实质正文里，逐字出现过本栏的主体与声明字段吗」。"
    "`原文支持已建立` 不等于该栏业务要求已满足；`共同读集内逐项可回溯` 尤其只说明"
    "同一节正文里出现了这些词，**不等于**六项各自的支撑已分别成立。"
)

#: 本栏目未达 covered 的**逐条 typed 原因**的人读措辞（闭集取自 runtime
#: `harness.topic_runtime.UNMET_COLUMN_REASONS`，本页只给措辞，**不**另立一套码）。
#:
#: 五条彼此不可互推，因此本页**逐条**陈列、**不**合并成一句「覆盖门未通过」。最要紧的一条是
#: `budget_blocked_dispatch`：它说的是「**这份语料本轮没被检索过**」，与「查了没有」是两件事——
#: 把它写成「上传语料里没有」是本批要修掉的读法。
UNMET_COLUMN_LABELS = {
    "budget_blocked_dispatch": "本轮预算阻止派发（责任要求查、导航给了候选，但预算不足以发出调用）",
    "dispatched_no_candidate": "已派发但未命中（调用真实发生、导航也给过结论，没有可读候选）",
    "material_found_but_unsupported": "命中但不支持本栏目（材料拿到了、没有合格事实以它背书本栏）",
    "table_material_unqualified": "表未获资格（本栏声明了表格槽位，本轮没有合格表材料）",
    "search_audit_incomplete": "检索审计不完整（查过但合格未命中的条件未全部成立）",
}
#: 逐条原因缺失时的**唯一**措辞。它不是「没有问题」：产物里没有这一键，只能说明这一轮
#: 没有可读的 typed 原因（旧版本产物，或本节不走 topic 研究相位）。
UNMET_COLUMN_UNAVAILABLE = "不可判定（本产物未带逐条栏目未达原因）"

#: 面向人的回读页分组（业务结构，不是材料 ID 列表）。
BUSINESS_GROUPS = (
    ("主营业务与产品", (
        "company_business_main.main_business",
        "company_business_main.products_solutions",
        "company_business_main.app_scenarios",
    )),
    ("经营模式及产业链", (
        "company_business_model.procurement_mode",
        "company_business_model.production_mode",
        "company_business_model.sales_mode",
        "company_business_model.tech_route",
        "company_business_model.cost_structure",
        "company_business_model.cost_competitiveness",
        "company_business_main.industry_chain_position",
    )),
    ("收入成本毛利", (
        "company_business_main.revenue_breakdown",
        "company_business_main.cost_gross_margin",
        "company_business_main.period_unit_caliber",
    )),
    ("客户与供应商", (
        "company_customer_concentration.customer_current_concentration",
        "company_customer_concentration.customer_concentration_change",
        "company_customer_concentration.customer_anonymity",
        "company_supplier_concentration.supplier_current_concentration",
        "company_supplier_concentration.supplier_concentration_change",
    )),
)

#: 三份材料的来源角色（**不得**因为同系列就互相排除）。
DOCUMENT_ROLES = {
    "NDSD_2025_year": "本系列最新一期年报；本期主语料",
    "NDSD_2024_year": "同系列上期年报；保留跨期数据与差异",
    "NDSD_KCZ_2026": "独立第三来源（募集说明书）；不因两份年报入集而排除",
}
DOCUMENT_ORDER = ("NDSD_2025_year", "NDSD_2024_year", "NDSD_KCZ_2026")

#: 逐份的**来源角色**（取自真实源集选择，`source_role` 三档；见源集 `selection` 的
#: `reason_code` / `source_role` 字段）。`is_anchor` 指该份是不是本来源集唯一的
#: `current_state_source`（当前状态锚）。
#:
#: 为什么这一轴决定「本栏的支持算不算数」，而不是按标签取最好/最差：三份上传文档的来源类
#: **相同**（`UPLOADED_DOCUMENT_SOURCE_CLASS`），因此逐份的 `retrieval_required` 也相同
#: （见 `harness/topic_runtime.py::derive_aspect_source_responsibility`：责任由**来源类**判，
#: 不由 document_id 判）。真正逐份不同的是**角色**：只有锚是本期主语料，历史份的支持
#: **不得**冒充本期事实（源集选择里写明的那条纪律），第三来源支持的是它自己声明的范围。
#: 所以逐 (栏目, 文档) 的支持必须逐份读，栏级结论只回答「**责任份**支持了没有」。
DOCUMENT_DUTY = {
    "NDSD_2025_year": {
        "source_role": "current_state_source", "is_anchor": True,
        "duty": "本系列最新一期年报：本期主语料，唯一可承担集合完整性证明（set_complete）的一份",
    },
    "NDSD_2024_year": {
        "source_role": "history_and_conflict_source", "is_anchor": False,
        "duty": "同系列上期年报：供历史分期、变化与冲突核对；**不得**用它冒充较新材料的当前事实",
    },
    "NDSD_KCZ_2026": {
        "source_role": "topic_participating_source", "is_anchor": False,
        "duty": "独立第三来源（募集说明书）：按它自己声明的范围参与检索与支撑，不冒充年报",
    },
}

#: 栏级「原文支持责任」判读（逐 (栏目, 文档) 判完再汇总，**不**跨份塌成单一标签）。
COLUMN_SUPPORT_VERDICTS = {
    "required_support_met_all": "锚与非锚来源**逐份**都逐字支持（仍须逐项复核共享读集）",
    "required_support_met_anchor_only": "锚（本期主语料）逐字支持，另有来源未支持",
    "support_from_non_anchor_only": "只有非锚来源（历史/第三来源）逐字支持，锚未支持"
                                    "——不得据此冒充本期事实",
    "required_support_missing": "三份都没有本栏主体与声明字段的逐字支持",
    "not_judged": "无可判读的逐份支持状态",
}


def _column_support_verdict(cells: dict) -> dict:
    """把**逐 (栏目, 文档)** 的支持状态汇总成一条责任判读（不取最好、也不取最差）。

    它回答的是「**责任份**支持了没有」，而不是「三份里最好/最差的一份长什么样」：

    - 锚（`current_state_source`）逐字支持 ⇒ 本栏的本期支持成立（`met_anchor*`）；
    - 只有非锚来源支持 ⇒ `support_from_non_anchor_only`：这是**必须显式说出**的一档——
      历史份/第三来源的支持存在，但它不得冒充本期事实，与「锚也支持」是两件事；
    - 三份都不支持 ⇒ `required_support_missing`。

    最弱一份（`weakest`）与逐份状态**并列**返回，只作旁证展示，不参与判读——把最弱一份
    当成栏级结论与把最强一份当成栏级结论是同一类误读（都是拿一份替代三份）。
    """
    per_doc = {d: c.get("support_state") for d, c in cells.items()}
    established = {d for d, s in per_doc.items()
                   if s in ("support_established", "support_established_shared")}
    anchor = next((d for d, duty in DOCUMENT_DUTY.items() if duty["is_anchor"]), None)
    weakest = None
    if per_doc:
        present = {s for s in per_doc.values() if s}
        for code in reversed(SUPPORT_STATES):   # 闭集**末位 = 最弱**
            if code in present:
                weakest = code
                break
    if not per_doc or not any(per_doc.values()):
        verdict = "not_judged"
    elif anchor in established and established == set(per_doc):
        verdict = "required_support_met_all"
    elif anchor in established:
        verdict = "required_support_met_anchor_only"
    elif established:
        verdict = "support_from_non_anchor_only"
    else:
        verdict = "required_support_missing"
    return {"verdict": verdict, "label": COLUMN_SUPPORT_VERDICTS[verdict],
            "anchor_document_id": anchor, "anchor_supported": anchor in established,
            "established_documents": sorted(established),
            "non_anchor_established": sorted(established - {anchor}),
            "weakest": weakest, "weakest_label": SUPPORT_LABELS.get(weakest, "（未判）"),
            "per_document": per_doc}

#: 表内/表邻内容在 span 层的落地名（只读诊断视图的来源）。
TABLE_LANDINGS = ("table_inside", "table_adjacency")


# ---------------------------------------------------------------------------
# 表对象信道（第二条、与 span 层**不同**的供给信道）
# ---------------------------------------------------------------------------
#
#: 本页为「表对象信道」单开一段。它与上面那条 span 层的表内/表邻落地**不是**同一件事：
#
#   * span 层回答「这段表内文字有没有成为 `role="body"` 的 `OutlineSpan`」——表内落地
#     `table_inside` / `table_adjacency` 按设计**不**成为正文 span，所以那一侧永远是
#     「未准入」。这正是 `table_not_released` 这一态**唯一**测到的事实。
#   * 表对象信道回答「这一栏的宿主块里有没有**已放行的表对象**、它有没有成为 Pack 材料」。
#     它走的是**图侧单通道**：`document_structure/live_table_source.py`（组合根签发）→
#     `harness/graph_table_release.py`（`gto-3` 逐表完整证明放行）→
#     `harness/graph_table_materials.py`（材料信封，`reading_material=True` /
#     `numeric_authority=False`）→ `harness/tree_tools.py::release_tables_for`
#     （按**本次已定位的宿主块**取对象）。
#
# **`v6` 及以前的 `harness/table_object_release.py` / `table_object_materials.py` 路径
# 已退役**：`table_object_release` 不再被生产调用（只保留历史 run 的只读兼容与它自己的
# 回归），因此本页读的身份是放行 `release_id`（`gtr-*`），不再是 `released_object_id`
# （`tobj-*`）。把两者当同一个键会让本页读到的对象与真实派发**不是同一份重切结果**。
#
# 两条信道必须分开记：只读 span 层会得出「表被拒发」，而同一栏的表对象可能**已经在
# Pack 里、已经在 Writer 清单里**。把两者合并会同时造成两种误读——把「这条信道不归那个
# 分类器管」读成「读失败」，以及把「表能读」读成「表里的数字有权威」。
TABLE_CHANNEL_NOTE = (
    "本段是**表对象信道**的只读清点，与上面的 span 层表内/表邻落地是**两条不同的信道**："
    "span 层只说明「表内文字没有成为 `role=\"body\"` 的正文 span」（按设计就该如此），"
    "**不**说明「表没有被放行」。表对象的放行走 `gto-3` 的**逐表完整证明**"
    "（`harness/graph_table_release.py`），材料化走 `harness/graph_table_materials.py`"
    "（`reading_material=True`、`numeric_authority=False`、`permitted_use="
    "navigable_reading_material_only`）。两条**正交**声明："
    "「这张表能读」与「表里的数字能以它为准」各自独立，后者在本页一律不成立。"
    "另有一条**不得读错**的边界：`not_released` 是「这张表没通过它**自己**的证明」，"
    "**不是**「来源里没有这张表」——后者要可核查检索才能下结论。"
)

#: 表对象在本页的三种去向（互斥、可穷尽；缺一条就是漏记）。
TABLE_CHANNEL_STATES = (
    "material_ready",        # 已放行且已成为 Pack 材料（本页只判到「成为材料」这一步）
    "released_not_material",  # 已放行，但成不了材料（typed 拒绝原因逐条带出）
    "not_released",          # 未获放行（typed 拒绝原因逐条带出）
)
TABLE_CHANNEL_STATE_LABELS = {
    "material_ready": "已放行并成为材料",
    "released_not_material": "已放行但成不了材料",
    "not_released": "未获放行",
}

#: **拒发账的作用域**（逐字带进每一栏）。`gto-3` 的逐表拒绝记录带 `table_id` / 页 /
#: 逐格缺陷，**不带**宿主块身份 —— 没被证明到能定来源那一步，自然也没有块。因此
#: 「这张表归哪一栏」在当前 wire 上**不可判定**。本页因此把整份文档的拒发账原样带进
#: 每一栏并逐条标注，**不**按页号或表题猜一栏：猜出来的归属会让读的人以为「本栏查过
#: 这张表」，而实际根本没查。这也**不**等于「来源里没有这张表」。
UNRELEASED_SCOPE_NOTE = (
    "逐表拒发账的作用域是**整份文档**，不是本栏。原因：`gto-3` 的拒绝记录不带宿主块身份，"
    "「这张表归哪个节点」在当前 wire 上不可判定；本页因此**不猜归属**，只把整份账原样带出。"
    "读法上限：`not_released` 只说明「这张表没通过它自己的逐表完整证明」，"
    "**既不**说明「本栏没有这张表」，也**不**说明「来源里没有这张表」。"
)


class ReadbackError(RuntimeError):
    """本审计模块的显式失败（不作静默兜底）。"""


# ---------------------------------------------------------------------------
# 只读装载
# ---------------------------------------------------------------------------

def _tight(text: str) -> str:
    return "".join(text.split())


def build_table_channel(live, snapshot, blocks, *, node_titles: dict) -> dict:
    """一份文档的**表对象信道**清点：逐块给出已放行对象、是否成为材料、typed 拒绝原因。

    用 runtime 的**同一条**路径建会话（`harness.tree_tools.TreeInspectionSession`），
    因此这里读到的对象/材料与真实派发时**是同一份重切结果**，不是本页另算的一套。

    四条不得含混的边界：

    1. 这里**只**清点「求解出什么、什么成为材料」。本页**不**产材料、不写 Pack、
       不判数字权威——`numeric_authority=False` 照旧，表里的数字不因「能读」而获得权威。
    2. `released_not_material` 与「块里没有表」是不同事实：前者带 typed 拒绝原因，
       后者根本没有条目。两者都不等于「来源里没有这张表」（那要可核查检索才能下结论）。
    3. 逐块归属取**组件 → 节点**（`snapshot.components` 上的 `node_id`），与表内落地
       同源；标题只作阅读坐标，不参与资格判定。
    4. **未获放行的表归不到节点。** `gto-3` 的拒绝记录只带 `table_id` / 页 / 逐格
       缺陷，**不带宿主块身份**（表没被证明到能定来源那一步）。因此本页**如实**留
       `node_ids=[]` 并标 `node_attribution_reason`，**不**按页号或表题去猜一个宿主
       ——猜出来的归属会让人以为「本栏查过这张表」，而实际根本没查。
    """
    from harness import graph_table_materials as GTM
    from harness import tree_tools as TT

    session = TT.TreeInspectionSession(live)
    session._ensure()
    batch = session.graph_table_batch()
    release_batch = session.graph_release_batch()
    identity = dict(batch.identity or {})

    nodes_by_block: dict = {}
    for component in snapshot.components:
        if component.node_id:
            nodes_by_block.setdefault(component.evidence_block_id, set()).add(
                component.node_id)

    by_node: dict = {}
    records: list[dict] = []
    for record_obj in batch.records:
        release_id = str(record_obj.get("release_id") or "")
        hosts = [str(b) for b in batch.release_hosts.get(release_id, ())]
        qualification = dict(record_obj.get("content_qualification") or {})
        material = batch.material_for_release(record_obj)
        node_ids = sorted({n for host in hosts for n in nodes_by_block.get(host, ())})
        record = {
            #: 三套身份（放行 / 逐表证明 / 表本身），互不顶替：本页把「本栏定位到的对象」
            #: 与「run 现场 Pack 里的材料」对上号，用的是 `release_id` 这一条 —— 它是
            #: `tree_tools.release_tables_for` 现在唯一认的键。
            "release_id": release_id,
            "local_proof_id": str(record_obj.get("local_proof_id") or ""),
            "table_id": str(record_obj.get("table_id") or ""),
            "table_locator": str(record_obj.get("table_locator") or ""),
            "host_evidence_id": hosts[0] if hosts else "",
            "host_evidence_ids": hosts,
            "page_number": int(record_obj.get("page_number") or 0),
            "table_title": str(record_obj.get("title_text") or ""),
            "unit_text": str(record_obj.get("unit_text") or ""),
            "structure_kind": str(record_obj.get("structure_kind") or ""),
            "structure_class": str(record_obj.get("structure_class") or ""),
            "structure_state": str(record_obj.get("structure_state") or ""),
            "body_row_count": int(record_obj.get("body_row_count") or 0),
            "column_count": int(record_obj.get("column_count") or 0),
            "cell_count": int(record_obj.get("cell_count") or 0),
            "all_columns_supported": bool(record_obj.get("all_columns_supported")),
            "cross_block": len(record_obj.get("evidence_block_ids") or ()) > 1,
            #: 正交的第二条声明原值（本页只如实抄出，绝不翻转）。
            "numeric_authority": qualification.get("numeric_authority"),
            #: 材料身份（内容寻址，`mat-gtb-…`）。它是本页把「本栏定位到的对象」与
            #: 「run 现场 Pack 里的材料」对上号的**唯一**可核对键；拿不到时如实留 `None`，
            #: 于是那一行在「已定位但未进 Pack」里记 `identity_unavailable`（不可判定），
            #: **不**按页号/表题做模糊配对（那会把两张不同的表认成同一张）。
            "material_id": (None if material is None
                            else (str(getattr(material, "material_id", "") or "")
                                  or None)),
            "table_channel_state": ("material_ready" if material is not None
                                    else "released_not_material"),
            "material_refusal_reason": (None if material is not None
                                        else batch.refusal_for_release(record_obj)),
            "node_ids": node_ids,
            "node_attribution_reason": ("component_or_span_node" if node_ids
                                        else "host_block_has_no_node_attribution"),
        }
        records.append(record)
        for node_id in record["node_ids"]:
            by_node.setdefault(node_id, []).append(record)
    for node_id, rows in by_node.items():
        rows.sort(key=lambda r: (r["page_number"] or 0, r["table_title"]))

    # **未获放行**的表（`gto-3` 逐表证明未过）：它们是「求解到了一张像表的东西、逐表证明
    # 挡下」的证据，不是「这里没有表」。逐条留 typed 原因与页/逐格缺陷，否则人类会把
    # 「挡下」读成「不存在」。
    unreleased: list[dict] = []
    for refusal in batch.release_refusals:
        unreleased.append({
            "host_evidence_id": "",
            "table_id": str(refusal.get("table_id") or ""),
            "table_locator": str(refusal.get("table_locator") or ""),
            "page_number": int(refusal.get("page_number") or 0),
            "table_title": "",
            "structure_state": str(refusal.get("structure_state") or ""),
            "structure_state_reason": str(
                refusal.get("structure_state_reason") or ""),
            "cell_count": int(refusal.get("cell_count") or 0),
            "citable_cell_count": int(refusal.get("citable_cell_count") or 0),
            "unproven_cell_count": int(refusal.get("unproven_cell_count") or 0),
            "unproven_fragment_keys": [list(k) for k in
                                       (refusal.get("unproven_fragment_keys") or ())],
            "cell_problem_examples": [dict(e) for e in
                                      (refusal.get("cell_problem_examples") or ())],
            "cell_problem_examples_truncated": bool(
                refusal.get("cell_problem_examples_truncated")),
            "typed_gap_detail_codes": list(
                refusal.get("typed_gap_detail_codes") or ()),
            "gap_basis": refusal.get("gap_basis"),
            "defect_codes": list(refusal.get("problems") or ()),
            "release_refusal_reason": str(refusal.get("reason") or ""),
            "node_ids": [],
            "node_attribution_reason": "refusal_record_carries_no_host_block",
        })
    unreleased_by_node: dict = {}

    refusal_reasons: dict = {}
    for row in records:
        reason = row.get("material_refusal_reason")
        if reason:
            refusal_reasons[reason] = refusal_reasons.get(reason, 0) + 1
    release_refusal_reasons: dict = {}
    for row in unreleased:
        reason = row["release_refusal_reason"]
        release_refusal_reasons[reason] = release_refusal_reasons.get(reason, 0) + 1

    return {
        "release_rule_version": str(identity.get("release_rule_version") or ""),
        "material_version": str(identity.get("material_version") or
                                GTM.GRAPH_TABLE_MATERIAL_VERSION),
        #: 文档级终态与逐表结果**同时**带出：一条「文档被拒、某张表仍被独立证明」的批次
        #: 如果隐去这一栏，读回就会读成「整份文档都过了」。
        "release_batch_id": str(identity.get("release_batch_id") or ""),
        "release_batch_state": str(identity.get("release_batch_state") or ""),
        "document_qualified": bool(identity.get("document_qualified")),
        "document_refusal": release_batch.get("document_refusal"),
        "declared_table_count": int(
            release_batch.get("declared_table_count") or 0),
        "block_count": len(blocks),
        "object_count": len(batch.records),
        "material_count": len(batch.materials),
        "refusal_count": len(batch.refusals),
        "material_refusal_reason_counts": refusal_reasons,
        "release_refusal_reason_counts": release_refusal_reasons,
        "released_object_count": len(records),
        "released_not_material_count": len(records) - len(batch.materials),
        "unreleased_candidate_count": len(unreleased),
        "records": records,
        "unreleased_candidates": unreleased,
        "by_node": by_node,
        "unreleased_by_node": unreleased_by_node,
        "node_titles": {n: node_titles.get(n, "") for n in by_node},
        "note": TABLE_CHANNEL_NOTE,
    }


#: 宿主节点与本栏的**读关系**（优势序）。它只描述事实「这张表的宿主块归到了哪个节点、
#: 那个节点本栏读没读」，**不**表示「这张表就是本栏的表」——见下面那条 typed 免责。
HOST_READ_RELATIONS = ("read_root", "read_node", "located_only")
HOST_READ_RELATION_LABELS = {
    "read_root": "宿主节点是本栏导航读根",
    "read_node": "宿主节点在本栏读集内（非读根）",
    "located_only": "宿主节点只被本栏标题定位到（未读）",
}

#: **宿主归属免责**（typed；逐栏原样带出，不省略）。这是本页读出来的**现场事实**：
#: 表块的宿主节点取自 `snapshot.components` 的组件归属，而**承接表的那个组件常常挂在
#: 表**前面**的一个标题下**。真实现场里「（1）营业收入构成」下的表，宿主节点被判成
#: 「（4）供应链及产能」；「表 5-10发行人主营业务收入构成表」被判成「（二）主营业务情况」。
#: 因此：
#:   * 宿主节点**不在**本栏读集里 ⇒ **不能**据此判「本栏没有这张表」，只能记
#:     「按宿主归属查不到」；
#:   * 宿主节点**在**本栏读集里 ⇒ 也**不**自动等于「这张表服务本栏」。
#: 要判「本栏的表进没进 Pack」，唯一可核对的口径是 Pack 侧的 aspect 归属
#: （`disposition.aspect_ids` 与 `aspect_results`），不是本页的节点归属。
HOST_ATTRIBUTION_CAVEAT = (
    "表块的宿主节点来自组件归属，**与表实际所属标题不一定一致**。真实现场两例（可在本页"
    "同一节里对读）：NDSD_KCZ_2026 p50 的 span 层把「表 5-10发行人主营业务收入构成表」"
    "落在节点「1、主营业务收入分析」，而表对象信道把它归到更粗的「（二）主营业务情况」；"
    "NDSD_2025_year p24 标题为「1）营业收入整体情况」的表被判到节点「（4）供应链及产能」。"
    "因此本栏的「宿主节点读关系」只作**导航坐标**记录：它既不能证明「本栏拿到了这张表」，"
    "也不能证明「本栏没有这张表」。判「本栏的表进没进 Pack / 有没有到 Writer」"
    "以 Pack 侧 aspect 归属为准。")


def build_table_channel_for(
        channel: dict, *, read_nodes: set[str],
        read_root_nodes: set[str] | None = None,
        located_nodes: set[str] | None = None) -> dict:
    """把一份文档的信道清点**按栏目收窄**，逐条标出宿主节点与本栏的**读关系**。

    参与收窄的节点集刻意**只取三样**，逐样含义分明：

    * `read_root_nodes`：本栏的导航读根——本栏就是从这里开始读的；
    * `read_nodes`：本栏读集（含读根）。它**很宽**：读根下的整片节点都算读过；
    * `located_nodes`：本栏经 `locate()` 标题匹配到的节点。它**只是候选**，
      本项研究一贯的纪律是「标题/简介相似度只生成候选」，所以这一档**最弱**。

    **本函数不下「这张表是本栏的」这个结论。** 它给的是事实（宿主节点是什么、读关系
    是什么）+ 一条 typed 免责（`host_attribution_caveat`）。原因见那条免责：组件归属会
    把表挂到**表前面的**标题上，逐栏按节点认表在两个方向上都会错。
    计数按读关系**分开**给出，读的人不必自己分辨。

    **已放行**的对象按上面那三样节点集收窄；**逐表证明未过**的账则**不收窄** —— 它
    归不到节点（见 `UNRELEASED_SCOPE_NOTE`），来了就整份带出并逐条标 `scope="document"`。
    """
    read_root = set(read_root_nodes or ())
    read_all = set(read_nodes or ())
    located = set(located_nodes or ())

    def relation(node_id: str) -> str:
        if node_id in read_root:
            return "read_root"
        if node_id in read_all:
            return "read_node"
        return "located_only"

    nodes = read_all | located
    seen: set[str] = set()
    rows: list[dict] = []
    for node_id in sorted(nodes):
        for record in channel["by_node"].get(node_id, ()):
            if record["release_id"] in seen:
                continue
            seen.add(record["release_id"])
            rows.append({**record, "matched_node_id": node_id,
                         "matched_node_title": channel["node_titles"].get(node_id, ""),
                         "host_read_relation": relation(node_id)})
    rows.sort(key=lambda r: (HOST_READ_RELATIONS.index(r["host_read_relation"]),
                             r["page_number"] or 0, r["table_title"]))

    # **逐表证明未过**的账**不按本栏收窄**：`gto-3` 的拒绝记录不带宿主块身份，本栏收窄
    # 需要的正是「这份文档的宿主块落在哪个节点」这条归属，而它此刻不存在。所以这里**不**
    # 猜归属、也**不**把账丢掉 —— 整份文档的拒发账**原样带出**，逐条标 `scope="document"`，
    # 读的人看到的是「这份文档有 N 张表被逐表证明挡下」，不是「本栏没有表」。
    # 丢掉它（哪怕只在本栏）就是把「挡下」读成「不存在」，正是 `TABLE_CHANNEL_NOTE` 禁的事。
    seen_refusals: set[str] = set()
    blocked: list[dict] = []
    for row in (channel.get("unreleased_candidates") or ()):
        key = str(row.get("table_id") or row.get("table_locator") or "")
        if not key or key in seen_refusals:
            continue
        seen_refusals.add(key)
        blocked.append({**row, "matched_node_id": None, "matched_node_title": "",
                        "host_read_relation": None, "scope": "document"})
    blocked.sort(key=lambda r: (r["page_number"] or 0, r["table_locator"] or ""))

    counts: dict = {state: 0 for state in TABLE_CHANNEL_STATES}
    for row in rows:
        counts[row["table_channel_state"]] += 1
    #: 逐读关系的 `material_ready` 计数：读的人一眼看出「本栏读到的节点上有几张表已成形」。
    ready_by_relation: dict = {}
    for row in rows:
        if row["table_channel_state"] != "material_ready":
            continue
        key = row["host_read_relation"]
        ready_by_relation[key] = ready_by_relation.get(key, 0) + 1
    refusal_reasons: dict = {}
    for row in rows:
        reason = row.get("material_refusal_reason")
        if reason:
            refusal_reasons[reason] = refusal_reasons.get(reason, 0) + 1
    blocked_reasons: dict = {}
    for row in blocked:
        reason = row["release_refusal_reason"]
        blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
    return {
        "release_rule_version": channel["release_rule_version"],
        "material_version": channel["material_version"],
        "objects": rows,
        "unreleased_candidates": blocked,
        #: 拒发账的作用域**逐字**写出来：它**是整份文档的**，不是本栏的。读的人因此不会
        #: 把「这份文档有 N 张表被挡下」误读成「本栏有 N 张表被挡下」。
        "unreleased_scope": "document",
        "unreleased_scope_note": UNRELEASED_SCOPE_NOTE,
        "state_counts": {k: v for k, v in counts.items() if v},
        "material_ready_by_host_relation": ready_by_relation,
        "host_read_relations": list(HOST_READ_RELATIONS),
        "host_read_relation_labels": dict(HOST_READ_RELATION_LABELS),
        "located_only_object_count": sum(
            1 for r in rows if r["host_read_relation"] == "located_only"),
        "state_labels": dict(TABLE_CHANNEL_STATE_LABELS),
        "material_refusal_reason_counts": refusal_reasons,
        "release_refusal_reason_counts": blocked_reasons,
        "host_attribution_caveat": HOST_ATTRIBUTION_CAVEAT,
        "note": TABLE_CHANNEL_NOTE,
    }


def load_run_tables(pack_path: Path | None) -> dict:
    """从 run 的 `material_pack.json` 读**表对象材料**的落点（逐材料一条）。

    这是本页唯一能回答「Pack/Writer 去向」的数据源：它来自运行现场，不是本页再算一遍。
    产物缺这一文件时返回 `available=False`——于是「已进 Pack / 已进 Writer 清单」**不会**
    被凭空断言，单元格如实写「产物不可得」而不是写「没有」。

    只认表对象材料（两条判据取**并集**，任一命中即为表对象）：节级
    `table_object_material_ids` 一览，或逐条 `content_qualification.kind == "table_object"`。
    正文材料不在此列——它们走 span 信道，另有逐栏记录。

    **两条 aspect 归属来源都带出**：`aspect_ids_from_aspect_results`（Pack 的 aspect 结果
    自己记的）与 `disposition_aspect_ids`（处置记录 = RMD 记的）。本函数**不替任何一条说话**：
    `by_key` 用并集建索引以便**读得到**，每行同时带 `aspect_attribution_agrees`。

    对**表对象材料**而言两条**本来就该不同**：`AspectResearchResult.material_ids` 按设计不收
    表材料（引用索引唯一性），表材料的栏目归属只在 RMD `aspect_ids` 上。两列都带出是为了让
    读的人看清「哪条轴上有它」，**不是**为了把两列的差异报成缺口。
    """
    if pack_path is None or not Path(pack_path).exists():
        return {"available": False, "path": None, "materials": [], "by_key": {},
                "entry_count": 0, "table_entry_count": 0}
    path = Path(pack_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    materials: list[dict] = []
    entry_count = 0
    for section_id, section in (payload.get("sections") or {}).items():
        if not isinstance(section, dict):
            continue
        table_ids = set(section.get("table_object_material_ids") or ())
        for row in (section.get("entries") or ()):
            if not isinstance(row, dict):
                continue
            entry_count += 1
            qualification = row.get("content_qualification")
            kind = (qualification.get("kind")
                    if isinstance(qualification, dict) else None)
            if row.get("material_id") not in table_ids and kind != "table_object":
                continue
            locator = row.get("locator") or {}
            disposition = row.get("disposition") or {}
            from_aspect_results = sorted(
                str(x) for x in (row.get("aspect_ids_from_aspect_results") or ()))
            from_disposition = sorted(
                str(x) for x in (disposition.get("aspect_ids") or ()))
            materials.append({
                "material_id": row.get("material_id"),
                "section_id": section_id,
                "topic_id": row.get("topic_id"),
                "document_id": (row.get("document_identity") or {}).get("document_id"),
                "document_version": (row.get("document_identity")
                                     or {}).get("document_version"),
                "material_type": row.get("material_type"),
                "page_number": locator.get("page"),
                "table_title": str(locator.get("table_title") or ""),
                "text_chars": row.get("text_chars"),
                "text_status": row.get("text_status"),
                "aspect_ids_from_aspect_results": from_aspect_results,
                "disposition_aspect_ids": from_disposition,
                "aspect_attribution_agrees": from_aspect_results == from_disposition,
                "aspect_ids": sorted(set(from_aspect_results) | set(from_disposition)),
                "in_writer_manifest": row.get("in_writer_manifest"),
                "writer_manifest_query": row.get("writer_manifest_query"),
                "admission_state": disposition.get("admission_state"),
                "retention_state": disposition.get("retention_state"),
                "reason_code": disposition.get("reason_code"),
            })
    by_key: dict = {}
    for material in materials:
        for aspect_id in material["aspect_ids"]:
            by_key.setdefault((material["document_id"], aspect_id), []).append(material)
    return {"available": True, "path": str(path), "materials": materials,
            "by_key": by_key, "entry_count": entry_count,
            "table_entry_count": len(materials)}


#: 「本栏**已定位但未进 Pack**的表对象」的逐条 typed 原因。互斥、可穷尽：
#: 一条「已定位」的对象若没进 Pack，必落在下面五档之一；落在哪一档决定了该找谁修。
LOCATED_NOT_IN_PACK_REASONS = (
    # 放行门挡下：`gto-3` 的**逐表完整证明**判定它还不是一张合格表。
    "not_released",
    # 已放行，但材料化挡下（本页材料信道给出逐条 `material_refusal_reason`）。
    "released_not_material",
    # 已放行、已成材料，但**本次运行现场的 Pack 里没有它**：派发域 / 预算 / 未走到 Pack。
    "not_in_run_pack",
    # 没给 `material_pack.json` ⇒ 不可判定（**不得**写成「没进 Pack」）。
    "pack_unavailable",
    # 两侧材料身份不可比（本页拿不到 `material_id`）⇒ 不可判定，且**不做**模糊配对。
    "identity_unavailable",
)
LOCATED_NOT_IN_PACK_LABELS = {
    "not_released": "未获放行（放行门挡下）",
    "released_not_material": "已放行但成不了材料",
    "not_in_run_pack": "已成材料但本次 Pack 里没有",
    "pack_unavailable": "不可判定（Pack 产物不可得）",
    "identity_unavailable": "不可判定（材料身份不可比）",
}

LOCATED_NOT_IN_PACK_NOTE = (
    "逐条只回答「本栏收窄范围内（读根 / 读集 / 仅定位三样节点集）查得到的表对象，"
    "在这一步卡在哪里」。四条边界不得含混：①「已定位」**不**表示「这张表是本栏的表」"
    "（见 `host_attribution_caveat`）；②「未进 Pack」是**本次运行现场**的事实，"
    "**不**等于「来源里没有这张表」——那要可核查检索才能下结论；③本列**不**判资格、"
    "**不**产材料、**不**给数字授权（表对象一律 `reading_material=True` / "
    "`numeric_authority=False`）。`material_id` 是这里唯一可核对的对象↔材料键；"
    "拿不到时如实记不可判定，**不**按页号或表题做模糊配对。"
    "④`not_released` 那一档的作用域是**整份文档**，不是本栏——逐表拒发记录不带宿主块"
    "身份，「这张表归哪个节点」不可判定，本页**不猜**，只把整份账原样带出（见 "
    "`UNRELEASED_SCOPE_NOTE`）。")


def located_not_in_pack(channel: dict, pack_materials,
                        *, pack_available: bool) -> dict:
    """本栏**已定位但未进 Pack**的表对象，逐条带 typed 原因（`/3` 起）。

    两个输入都来自现场、不在本函数里另算一套：
    `channel` 是 `build_table_channel_for` 收窄后的栏目读数（对象 ＋ 未放行候选），
    `pack_materials` 是 `load_run_tables` 从运行现场 `material_pack.json` 读出的表对象材料。

    配对**只按** `material_id`（内容寻址身份）。拿不到身份或 Pack 不可得时记不可判定档，
    **不**退化成「按页号 + 表题猜」——那会把两张不同的表认成同一张，正是本页要防的读法。
    """
    pack_ids = {str(m.get("material_id")) for m in (pack_materials or ())
                if m.get("material_id")}
    rows: list[dict] = []
    in_pack = 0

    for record in (channel.get("objects") or ()):
        state = record.get("table_channel_state")
        if state == "material_ready":
            material_id = record.get("material_id")
            if not pack_available:
                reason, detail = "pack_unavailable", None
            elif material_id is None:
                reason, detail = "identity_unavailable", None
            elif material_id in pack_ids:
                in_pack += 1
                continue
            else:
                reason, detail = "not_in_run_pack", material_id
        else:
            reason = "released_not_material"
            detail = record.get("material_refusal_reason")
        rows.append({**_not_in_pack_view(record), "reason": reason,
                     "reason_label": LOCATED_NOT_IN_PACK_LABELS[reason],
                     "reason_detail": detail})

    for record in (channel.get("unreleased_candidates") or ()):
        rows.append({
            "release_id": None,
            "local_proof_id": None,
            "table_id": record.get("table_id"),
            "table_locator": record.get("table_locator"),
            "material_id": None,
            "host_evidence_id": None,
            "page_number": record.get("page_number"),
            "table_title": record.get("table_title"),
            "structure_state": record.get("structure_state"),
            "column_count": None,
            "host_read_relation": record.get("host_read_relation"),
            "matched_node_title": record.get("matched_node_title"),
            #: 这一行**不在**本栏收窄范围内（它归不到节点）；标出来，否则读的人会以为
            #: 「本栏查过这张表」。
            "scope": record.get("scope") or "document",
            "reason": "not_released",
            "reason_label": LOCATED_NOT_IN_PACK_LABELS["not_released"],
            "reason_detail": record.get("release_refusal_reason"),
            "defect_codes": list(record.get("defect_codes") or ()),
            "unproven_cell_count": record.get("unproven_cell_count"),
        })

    rows.sort(key=lambda r: (
        LOCATED_NOT_IN_PACK_REASONS.index(r["reason"]),
        HOST_READ_RELATIONS.index(r["host_read_relation"])
        if r["host_read_relation"] in HOST_READ_RELATIONS else len(HOST_READ_RELATIONS),
        r["page_number"] or 0, r["table_title"] or ""))
    counts: dict = {}
    for row in rows:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    return {
        "available": bool(pack_available),
        "pack_available": bool(pack_available),
        "count": len(rows),
        "in_pack_count": in_pack,
        "reason_counts": counts,
        "reason_labels": dict(LOCATED_NOT_IN_PACK_LABELS),
        "rows": rows,
        "note": LOCATED_NOT_IN_PACK_NOTE,
    }


def _not_in_pack_view(record: dict) -> dict:
    """把一条信道记录裁成「未进 Pack」列要展示的字段（不复制判据、不丢身份）。"""
    return {key: record.get(key) for key in (
        "release_id", "local_proof_id", "table_id", "table_locator",
        "material_id", "host_evidence_id", "page_number", "table_title",
        "structure_state", "column_count", "host_read_relation",
        "matched_node_title")}


def build_index(repo: Path, pdf: Path):
    """重建某份材料的 live 只读视图：`(index, snapshot, outline, blocks, live)`。"""
    from document_structure import live_span_source as LSS
    from document_structure import navigation as NAV
    from document_structure import synopsis as SY
    from evidence import store as estore

    company = pdf.parents[1].name
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc_version = "sha256-" + sha[:16]
    evidence_db = (repo / "data" / "evidence.db").resolve()
    saved = estore._db_path
    estore._db_path = evidence_db
    try:
        set_version = estore.current_evidence_set_ro(
            evidence_db, company, pdf.stem, doc_version)
        live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
            company_id=company, document_id=pdf.stem, document_version=doc_version,
            raw_pdf_path=str(pdf), raw_pdf_sha256=sha,
            expected_current_evidence_set_version=set_version))
    finally:
        estore._db_path = saved
    snapshot, outline = live.snapshot, live.document_outline
    synopses = SY.build_navigation_synopses(
        node_ids=sorted({n.node_id for n in outline.nodes}), spans=snapshot.spans,
        coverages=snapshot.coverages, dispositions=snapshot.dispositions,
        policy=live.qualification_policy)
    index = NAV.NavigationIndex(
        outline, synopses, span_node_ids=[s.node_id for s in snapshot.spans],
        # `anp-7` 主体补读的输入之一：逐节点**自有**导航可采信正文字符数（只给计数，
        # 不给正文——索引仍拿不到任何证据内容）。读回页报的"读集"因此是**这条机制
        # 真正读到的**集合，补读进来的那几节也在这里逐条可查。
        body_char_counts=SY.build_navigation_body_chars(snapshot.spans))
    blocks = {b.evidence_block_id: b for b in live.evidence_blocks}
    return index, snapshot, outline, blocks, live


def build_requirements(repo: Path):
    """由冻结 Contract + demo scope 投影出 `company_business` 的 aspect 需求集。"""
    from contracts.loader_v2 import load_contract_v2
    from document_structure import navigation as NAV
    from planning import demo_scope as SC
    from planning import schema as PS

    profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
    contract = load_contract_v2(str(repo / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_business_readback", company_id="300750", company_name="audit",
        credit_type="general", report_as_of="2026-12-31", contract_version="v2")
    source_inputs = {
        "case_input_id": "case_business_readback", "document_id": "NDSD_2025_year",
        "document_version": "sha256-audit", "raw_pdf_sha256": "0" * 64,
        "current_evidence_set_version": "ev-audit",
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest_scope = SC.build_scope_input_manifest(profile, business, source_inputs)
    projection = SC.project_contract_v2_scope(contract, profile, manifest_scope)
    requirements = {r.topic_id: r for r in projection.requirements}
    requirement = requirements[TOPIC_ID]
    by_id = {a.aspect_id: a for a in contract.all_aspects()}
    nav_profile = NAV.build_navigation_profile(
        requirement.aspects, contract_version=requirement.contract_version,
        contract_fingerprint=requirement.contract_fingerprint,
        ancestor_labels=NAV.contract_ancestor_labels(contract),
        sibling_keys=NAV.contract_sibling_keys(contract))
    return requirement, by_id, nav_profile, contract


# ---------------------------------------------------------------------------
# 独立定位器（不看导航决策）
# ---------------------------------------------------------------------------

def probe_terms(aspect) -> tuple[str, ...]:
    """该栏目的**探针词**：Contract 声明的主体头 + 声明字段（纯 Contract 文本）。"""
    from document_structure import navigation as NAV

    out: list[str] = []
    head = NAV.aspect_subject_head(aspect.requirement_text)
    for raw in (head,) + tuple(aspect.required_fields):
        key = NAV.normalize_navigation_text(raw)
        if key and key not in out:
            out.append(key)
    return tuple(out)


def locate(index, entry, aspect) -> list[dict]:
    """独立定位：标题树上哪些节点**关于这一栏**（标签段相等 / 主体邻接）。

    两级证据都只读标题文本与 Contract 声明，不看导航的候选、读根或读集：

    - `label_segment`：节点（或其某层祖先）的**完整标签段**与某个探针词相等——最强。
    - `subject_adjacent`：节点（或其某层祖先）的标题与主体头的**最长公共子串**达到
      导航规则里同一个门槛（`_TOPIC_SEGMENT_MIN_SHARED`）。这一级正是机制「零候选」
      时应当补读、却停在那里判「材料不存在」的那一批。
    """
    from document_structure import navigation as NAV

    threshold = NAV._TOPIC_SEGMENT_MIN_SHARED  # noqa: SLF001 - 门槛沿用同一条已评审常量
    terms = probe_terms(aspect)
    head = NAV.aspect_subject_head(aspect.requirement_text)
    hits: dict[str, dict] = {}
    for node_id in index.node_ids:
        node = index.node(node_id)
        if node is None:  # pragma: no cover - 索引自持集合一致
            continue
        chain = (node_id,) + tuple(index.ancestors_of(node_id))
        matched_terms: list[str] = []
        for term in terms:
            if any(term in index.label_segments(a) for a in chain):
                matched_terms.append(term)
        #: 第二层：导航自己那条**已评审**的贴标签判据（`NAV_ROOT_ANCHOR_RULE_ID`，
        #: 短标题的前/后缀与整键夹带）。`采购模式` 落不到 `经营模式` 上（二者只共享
        #: 「模式」），所以这一层仍不足以覆盖全部相关章节——它只把"字段名直接贴在标题上"
        #: 的那些收进来，剩下的缺口由 `subject_adjacent` 层如实报出，不靠猜补齐。
        anchored: list[str] = []
        for ancestor in chain:
            anchored.extend(NAV.label_anchored_keys(index, terms, ancestor))
        anchored = sorted(set(anchored))
        adjacency = NAV.subject_adjacency(index, head, node_id) if head else 0
        tiers: list[str] = []
        if matched_terms:
            tiers.append("label_segment")
        if anchored:
            tiers.append("label_anchored")
        if head and adjacency >= threshold:
            tiers.append("subject_adjacent")
        if not tiers:
            continue
        hits[node_id] = {
            "node_id": node_id,
            "title": node.title,
            "level": node.level,
            "structural_path": list(node.structural_path),
            #: 该位置所在的**顶层章节**（最远的一层祖先；没有祖先就是它自己）。
            #: 读回页用它把"本栏该读的那一章里漏读了"与"别的章节里也有相关披露"分开：
            #: 同一批探针词会在年报财务报告里命中「24、收入」这类**别章**节点，混在一起
            #: 会让"主营业务那一节读到了没有"被别处的未读淹没。
            "top_chapter_id": chain[-1],
            "path_titles": [index.normalized_title(a)
                            for a in reversed(index.ancestors_of(node_id))],
            "locate_tiers": tiers,
            "strong": bool(matched_terms or anchored),
            "matched_terms": matched_terms,
            "anchored_keys": anchored,
            "subject_adjacency": adjacency,
            "probe_terms": list(terms),
        }
    return [hits[k] for k in sorted(hits, key=lambda n: index.document_order(n))]


# ---------------------------------------------------------------------------
# 块级完整性（材料读到什么程度）
# ---------------------------------------------------------------------------

#: 「未读残差」里算**正文**的那些落地（标题/版式碎片不算"正文不完整"）。
BODY_RESIDUE_LANDINGS = ("body_span", "body_unassigned", "formal_unassigned",
                         "outside_body", "alignment_residue_unmapped")


def _node_facts(snapshot) -> dict:
    """按**标题节点**聚合：读到多少准入正文字符、未读残差是什么。

    粒度必须是节点，不是 Evidence 块：一个大块常被若干标题共用，按块算会把邻章的
    表格残差记到本栏目头上——那正是"把别人家的表算成我的缺口"的误判形态。
    """
    facts: dict[str, dict] = {}
    for component in snapshot.components:
        node_id = component.node_id
        if not node_id:
            continue
        row = facts.setdefault(node_id, {
            "node_id": node_id,
            "admitted_chars": 0,
            "residue_by_landing": {},
            "evidence_block_ids": [],
            "span_ids": [],
            "component_count": 0,
        })
        row["component_count"] += 1
        if component.evidence_block_id not in row["evidence_block_ids"]:
            row["evidence_block_ids"].append(component.evidence_block_id)
        if component.span_id and component.span_id not in row["span_ids"]:
            row["span_ids"].append(component.span_id)
        start, end = component.evidence_char_range
        length = max(0, int(end) - int(start))
        if component.admitted:
            row["admitted_chars"] += length
        else:
            landing = component.landing or "unknown"
            row["residue_by_landing"][landing] = \
                row["residue_by_landing"].get(landing, 0) + length
    for row in facts.values():
        row["table_residue_chars"] = sum(
            v for k, v in row["residue_by_landing"].items() if k in TABLE_LANDINGS)
        row["body_residue_chars"] = sum(
            v for k, v in row["residue_by_landing"].items()
            if k in BODY_RESIDUE_LANDINGS)
        row["residue_chars"] = sum(row["residue_by_landing"].values())
    return facts


def _form_like(text: str) -> bool:
    """文本是否**只是一条勾选行 / 表单行**（纯字符判据，无词表）。"""
    from harness import tree_materials as TM

    body = text.strip()
    if body == "":
        return False
    markers = sum(1 for ch in body if TM.is_selection_marker(ch))
    if markers == 0:
        return False
    #: 勾选标记足够密集，且去掉标记后剩下的都是标签词/「是否」这类空壳。
    stripped = "".join(ch for ch in body if not TM.is_selection_marker(ch))
    stripped = stripped.replace("是否", "").replace("□", "").strip(" ：:、,，。.")
    return markers >= 1 and len(stripped) <= TITLE_LIKE_MAX_CHARS


def _read_texts(snapshot, node_ids: set[str]) -> list[dict]:
    """给定节点集合下**已准入**的正文 span 文本（机制真正读到的正文）。"""
    out: list[dict] = []
    for span in snapshot.spans:
        if span.node_id not in node_ids:
            continue
        if span.role != "body" or not span.normalized_text:
            continue
        text = span.normalized_text
        out.append({
            "span_id": span.span_id,
            "node_id": span.node_id,
            "page_range": list(span.page_range),
            "char_length": len(text),
            "text": text,
            "form_like": _form_like(text),
            "title_like": len(text.strip()) <= TITLE_LIKE_MAX_CHARS,
        })
    return out


# ---------------------------------------------------------------------------
# 逐 (栏目, 文档) 状态判定
# ---------------------------------------------------------------------------

def _state_for(*, located: list[dict], read_nodes: set[str],
               read_texts: list[dict], node_facts: dict,
               entry_decision: dict, fact_state: str | None,
               own_chapter: str | None = None) -> tuple[str, list[str], dict]:
    """把一个 (栏目, 文档) 判成 7 态之一，并给出**全部**成立过的状态（不藏事实）。

    判据只看三件事，没有一件是"份数"：

    1. **定位到的位置有没有被读**（`located` × `read_nodes`）——「已定位但未读取」；
    2. **读到的正文是不是只有标题/勾选行**（`title_like` / `form_like`）；
    3. **定位到的节点下有多少字符没进任何准入正文 span，那些残差是什么**——残差全是
       表内/表邻落地 → 「表格拒发」；残差是正文落地 → 「正文片段不完整」。残差为零且
       读到了实质正文 → 「取得完整相关材料」。

    第三件里有一处**必须按章节分开**的判据：第 1 条的"未读强定位"要在**本栏自己的
    那一章**里成立才算这一栏的缺口。年报里同一批探针词会在第八节/第十节财务报告里
    命中「24、收入」「（二）收入确认」这类**别章**节点；把别章的未读一起算进头条，
    「主营业务这一节到底读到没有」就被淹没了。所以本条按 `own_chapter` 一分为二：
    本章的未读驱动头条，别章的未读**照样列出来**（返回值的第三个元素），只是不顶掉
    本章的结论，也**不新增第 8 态**。

    返回 `(state, observed, scope)`，`scope` 里带本章/别章两侧的未读位置清单。
    """
    observed: list[str] = []
    strong = [loc for loc in located if loc.get("strong")]
    strong_ids = {loc["node_id"] for loc in strong}
    unread_strong = [loc for loc in strong if not _is_read(loc, read_nodes)]

    def _material_chars(node_id: str) -> int:
        row = node_facts.get(node_id)
        if row is None:
            return 0
        return row["admitted_chars"] + row["residue_chars"]

    #: 只有**真带了内容**的未读位置才算数：一个空标题节点没被读不是缺口。
    unread_strong_with_material = [
        loc for loc in unread_strong
        if _material_chars(loc["node_id"]) >= MIN_MATERIAL_CHARS]

    def _in_own_chapter(loc: dict) -> bool:
        #: 判不出章节（fallback 且连读根都没有）时**保守**当作"本章"：不能因为章节
        #: 无从确定，就把"实料一个字没读"这件事从头条上抹掉。
        if own_chapter is None:
            return True
        return loc.get("top_chapter_id") == own_chapter

    unread_here = [loc for loc in unread_strong_with_material if _in_own_chapter(loc)]
    unread_elsewhere = [loc for loc in unread_strong_with_material
                        if not _in_own_chapter(loc)]

    #: 残差与"读到了什么"只按**本栏目自己的读集**计。这是唯一不需要外部判断的锚点：
    #: 问题不是"哪些章节属于这一栏"，而是"这一栏真读进来的那几节，读全了没有"——
    #: 表体缺没缺，看的就是读集里的未读残差，不掺任何别家的表。
    table_residue = sum(node_facts[nid]["table_residue_chars"]
                        for nid in read_nodes if nid in node_facts)
    body_residue = sum(node_facts[nid]["body_residue_chars"]
                       for nid in read_nodes if nid in node_facts)
    own_texts = [t for t in read_texts if t["node_id"] in read_nodes]
    substantive = [t for t in own_texts if not t["title_like"] and not t["form_like"]]

    if unread_here:
        observed.append("located_not_read")
    if table_residue > 0:
        observed.append("table_not_released")
    if body_residue > 0:
        observed.append("partial_body")
    if own_texts and not substantive:
        observed.append("title_or_form_only")
    if substantive and not unread_here and body_residue == 0 \
            and table_residue == 0:
        observed.append("complete_material")
        #: 只有**有事实资格判据**（run 的 writable_facts）时才敢说这一态；
        #: 没有判据时如实留 `not_judged_here`，不替审核侧下结论。
        if fact_state == "unqualified":
            observed.append("material_complete_fact_not_qualified")

    def _brief(loc: dict) -> dict:
        return {"node_id": loc["node_id"], "title": loc["title"],
                "top_chapter_id": loc.get("top_chapter_id"),
                "path_titles": list(loc.get("path_titles") or ()),
                "material_chars": _material_chars(loc["node_id"])}

    scope = {
        "own_chapter_id": own_chapter,
        "unread_in_chapter": [_brief(loc) for loc in unread_here],
        "unread_out_of_chapter": [_brief(loc) for loc in unread_elsewhere],
        "note": ("`unread_out_of_chapter` 里的位置**没有被藏起来**：它们是同批探针词在"
                 "**别章**命中的位置，不是本栏所在那一章的缺口。看本栏材料到没到手，"
                 "要看本章；别章命中另列，供人复核其相关性。"),
    }

    #: 头号状态先判「已定位到实料却没读」——这是最该被看见的事实，且它不是"部分",
    #: 因为那份材料**一个字都没进过读集**。
    if unread_here:
        return "located_not_read", observed, scope
    #: 读集为空、且没有任何带内容的强定位位置：连"该读哪儿"都没定位到 → 未导航到。
    #: 弱定位（近似标题）不进这个判据：2 字公共子串能把半棵树变成"相关标题"。
    if not read_nodes and not any(_material_chars(nid) >= MIN_MATERIAL_CHARS
                                  for nid in strong_ids):
        return "not_navigated", observed or ["not_navigated"], scope
    #: 否则按"材料到手程度"取最好的一态；其余同时成立的状态全在 observed 里，不藏。
    for code in STATES:
        if code in observed:
            return code, observed, scope
    return "not_navigated", observed or ["not_navigated"], scope


def _is_read(loc: dict, read_nodes: set[str]) -> bool:
    """该位置是否落在**已读**范围里（自身或其某层祖先在读集内）。"""
    if loc["node_id"] in read_nodes:
        return True
    return any(node_id in read_nodes for node_id in (loc.get("structural_path") or ()))


def _own_chapter(index, decision) -> str | None:
    """本栏**该读的那一章**：导航选中节点的最顶层祖先。

    它只回答"这一栏落在哪一章"，**不**回答"这一栏该读哪些节"——后者是导航的事，
    这里不复制导航判据。降级（fallback）没有选中节点时，退而用第一个读根；再没有
    就读集也空着，返回 `None`，读回页如实记 `null` 而不是编一个章节出来。
    """
    candidates = (decision.selected_node_id,) + tuple(
        getattr(decision, "read_root_node_ids", ()) or ())
    for node_id in candidates:
        if not node_id:
            continue
        chain = tuple(index.ancestors_of(node_id))
        return chain[-1] if chain else node_id
    return None


# ---------------------------------------------------------------------------
# 逐 aspect 的原文支持（与"材料到达"分开的另一条轴）
# ---------------------------------------------------------------------------

def _support_for(*, entry, aspect, read_texts: list[dict],
                 read_nodes: set[str], shared: bool) -> dict:
    """本栏读集里的**实质正文**是否逐字带出主体头与至少一个声明字段。

    输入只有本栏自己的读集文本与冻结 Contract 的声明文本，两侧用**同一套**规范化
    口径（`normalize_navigation_text`）比较——与导航的贴标签判据同族口径。它不产
    任何"覆盖"结论，也不改变任何运行时判定；它只是把"这一段正文对得上这一栏吗"
    这件事从"读集里有正文"里**分离**出来，好让人看得见两者不是一回事。
    """
    from document_structure import navigation as NAV

    substantive = [t for t in read_texts
                   if t["node_id"] in read_nodes
                   and not t["title_like"] and not t["form_like"]]
    if not substantive:
        return {"state": "support_no_text", "label": SUPPORT_LABELS["support_no_text"],
                "subject_head": entry.subject_head, "subject_hit": False,
                "field_hits": [], "field_misses": list(aspect.required_fields),
                "evidence": None, "shared_read_set": shared,
                "caveat": SUPPORT_CAVEAT}

    blob = NAV.normalize_navigation_text("".join(t["text"] for t in substantive))
    subject = entry.subject_head or ""
    field_hits = [f for f in aspect.required_fields
                  if NAV.normalize_navigation_text(f) in blob]
    field_misses = [f for f in aspect.required_fields if f not in field_hits]
    subject_hit = bool(subject) and subject in blob

    if subject_hit and field_hits:
        code = "support_established_shared" if shared else "support_established"
    elif subject_hit or field_hits:
        code = "support_partial"
    else:
        code = "support_absent"

    #: 把命中的那一段原文留在产物里：判据是机械包含，人必须能复核它到底命中了什么。
    evidence = None
    if subject_hit or field_hits:
        needle = subject if subject_hit else ""
        for text in substantive:
            body = NAV.normalize_navigation_text(text["text"])
            if (needle and needle in body) or any(
                    NAV.normalize_navigation_text(f) in body for f in field_hits):
                evidence = {"span_id": text["span_id"], "node_id": text["node_id"],
                            "page_range": text["page_range"],
                            "char_length": text["char_length"],
                            "excerpt": _clip(text["text"], 240)}
                break
    return {"state": code, "label": SUPPORT_LABELS[code],
            "subject_head": subject, "subject_hit": subject_hit,
            "field_hits": field_hits, "field_misses": field_misses,
            "evidence": evidence, "shared_read_set": shared,
            "caveat": SUPPORT_CAVEAT}


# ---------------------------------------------------------------------------
# 表格只读诊断视图
# ---------------------------------------------------------------------------

def table_diagnostics(snapshot, blocks, *, document_id: str,
                      node_titles: dict[str, str] | None = None,
                      scope_node_ids: set[str] | None = None) -> list[dict]:
    """表内/表邻内容的**只读诊断**登记：原文 + 精确定位 + 拒发理由。

    这不是 Pack 材料、不是数字事实、不是可发布正文。它存在的唯一理由是把
    「表体确实在原 PDF 里、只是没被签发出正式材料」与「原 PDF 根本没有这张表」
    分开——两者在收入/成本栏目上的结论完全不同。

    `scope_node_ids` 给出时只登记**落在这些标题节点上**的表内内容（本页只对主营业
    务相关章节负责）；整个文档的表内内容不属于这张回读页，登记进来只会淹没结论。
    """
    node_titles = node_titles or {}
    #: 表体在 span 层是**逐片段**落地的（这张收入成本表被切成 66 个 1–57 字符的表内片段），
    #: 逐片段展示等于把表打散到不可读。因此按 (块, 标题节点, 落地) 归并成一条，展示其
    #: **并集区间**的原文；片段数与被省略的间隙一并留档。
    groups: dict[tuple, dict] = {}
    for component in snapshot.components:
        if component.landing not in TABLE_LANDINGS:
            continue
        if scope_node_ids is not None and component.node_id not in scope_node_ids:
            continue
        block = blocks.get(component.evidence_block_id)
        if block is None:
            raise ReadbackError(
                f"表内组件 {component.component_id!r} 的父块 "
                f"{component.evidence_block_id!r} 不在同次证据集里（不得降级）")
        key = (component.evidence_block_id, component.node_id, component.landing)
        row = groups.setdefault(key, {
            "view_kind": "只读诊断（表内/表邻）",
            "not_pack_material": True,
            "not_numeric_fact": True,
            "not_publishable_prose": True,
            "document_id": document_id,
            "evidence_block_id": component.evidence_block_id,
            "node_id": component.node_id,
            "node_title": node_titles.get(component.node_id or "", None),
            "landing": component.landing,
            "page_number": block.page_number,
            "block_index": block.block_index,
            "block_tight_chars": len(_tight(block.text)),
            "component_count": 0,
            "component_locators": [],
            "ranges": [],
            "admitted_any": False,
            "admission_reasons": [],
            "verdicts": [],
            "refusal_reasons": [],
            "span_ids": [],
        })
        start, end = (int(component.evidence_char_range[0]),
                      int(component.evidence_char_range[1]))
        row["component_count"] += 1
        row["component_locators"].append(component.component_locator)
        row["ranges"].append([start, end])
        row["admitted_any"] = row["admitted_any"] or bool(component.admitted)
        for bucket, value in (("admission_reasons", component.admission_reason),
                              ("verdicts", component.verdict),
                              ("refusal_reasons", component.refusal_reason)):
            if value is not None and value not in row[bucket]:
                row[bucket].append(value)
        if component.span_id and component.span_id not in row["span_ids"]:
            row["span_ids"].append(component.span_id)

    out: list[dict] = []
    for key in sorted(groups, key=lambda k: (groups[k]["page_number"],
                                             groups[k]["block_index"],
                                             groups[k]["node_id"] or "")):
        row = groups[key]
        block = blocks[row["evidence_block_id"]]
        text = _tight(block.text)
        union = _merge_ranges(row.pop("ranges"))
        slice_text = "".join(text[s:e] for s, e in union)
        covered = sum(e - s for s, e in union)
        span = (union[0][0], union[-1][1]) if union else (0, 0)
        row.update({
            "evidence_char_range": list(span),
            "merged_ranges": [list(r) for r in union],
            "merged_range_count": len(union),
            "slice_chars": covered,
            "gap_chars_in_span": max(0, (span[1] - span[0]) - covered),
            "text": slice_text,
            "text_excerpt": slice_text[:DIAGNOSTIC_EXCERPT_CHARS],
            "truncated_in_excerpt": len(slice_text) > DIAGNOSTIC_EXCERPT_CHARS,
            "content_fingerprint": hashlib.sha256(
                slice_text.encode("utf-8")).hexdigest(),
            "admitted": row.pop("admitted_any"),
            "rejection_reason": {
                "layer": "span_landing",
                "verdicts": row["verdicts"],
                "refusal_reasons": row["refusal_reasons"],
                "admission_reasons": row["admission_reasons"],
                "detail": ("表内内容被 span 层落地为 table_inside：它不构成正文 span，"
                           "因此不进入树材料人口（`harness/tree_materials` 只按 span_id "
                           "索引组件，table_inside 的 span_id 为空）。这不是「查无此表」。"),
            },
        })
        row["span_ids"] = row["span_ids"]
        out.append(row)
    return out


def _merge_ranges(ranges: list) -> list:
    """把若干半开区间并成不相交的升序区间（相邻/重叠即合并）。"""
    if not ranges:
        return []
    ordered = sorted((int(s), int(e)) for s, e in ranges if e > s)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(r) for r in merged]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _sections(doc_results: dict) -> dict:
    """把逐文档结果按**业务结构**分组，供人读回读页（单元格是裁剪过的摘要）。"""
    groups = []
    for name, aspect_ids in BUSINESS_GROUPS:
        rows = []
        for aspect_id in aspect_ids:
            cells = []
            for document_id in DOCUMENT_ORDER:
                cell = doc_results.get(document_id, {}).get("aspects", {}).get(aspect_id)
                if cell is None:
                    continue
                cells.append(_cell_summary(document_id, cell))
            rows.append({"group": name, "aspect_id": aspect_id, "cells": cells})
        groups.append({"group": name, "rows": rows})
    return {"groups": groups}


def _cell_summary(document_id: str, cell: dict) -> dict:
    body = [t for t in cell["read_texts"]
            if not t["title_like"] and not t["form_like"]]
    return {
        "document_id": document_id,
        "state": cell["state"],
        "state_label": cell["state_label"],
        "states_observed": cell["states_observed"],
        "located_count": len(cell["located"]),
        "located": cell["located"],
        "substantive_read_chars": sum(t["char_length"] for t in body),
        "substantive_read_texts": body,
        "read_root_titles": cell["navigation"]["read_root_titles"],
        "fallback_reason": cell["navigation"]["fallback_reason"],
        "navigation": cell["navigation"],
        "required_fields": cell["required_fields"],
        "content_role": cell["content_role"],
        "fact_eligibility": cell.get("fact_eligibility", "not_judged_here"),
        "support": cell.get("support"),
        "shared_read_set_with": cell.get("shared_read_set_with", ()),
        "own_chapter_id": cell.get("own_chapter_id"),
        "own_chapter_title": cell.get("own_chapter_title"),
        "chapter_scope": cell.get("chapter_scope"),
        # 本栏未达 covered 的**逐条** typed 原因（`failure-diagnostics/3` 起）。它是「材料到达」
        # 诊断里唯一能分开「本轮没查」「查了没命中」「材料到了但话对不上栏目」「表未获资格」
        # 的那一层；缺失时是**不可判定**，不是空。
        "column_unmet": _column_unmet_render(cell),
        #: `business-material-readback/3`：本栏已定位但**未进 Pack**的表对象（逐条 ＋
        #: typed 原因）。它与 `table_channel` 的分段计数**不重复**：那一段答「有几条」，
        #: 这一列答「是哪一条、卡在哪一档」。
        "located_not_in_pack": cell.get("located_not_in_pack"),
    }


def load_fact_states(diagnostics_path: Path | None) -> dict:
    """从 run 的 `failure_diagnostics.json` 读每个 aspect 的**事实资格**判据与栏目未达原因。

    没有这个文件时返回空字典——于是「完整材料存在但事实尚未取得资格」这一态**不会**
    被凭空断言，单元格如实记 `fact_eligibility="not_judged_here"`。

    `column_unmet` 是**本栏目未达 covered 的逐条 typed 原因**（`failure-diagnostics/3` 起）。
    它来自运行现场的 `TopicRuntimeResult.gaps`，是本页唯一能区分「本轮预算没让检索发生」
    「派发了没命中」「材料到了话对不上栏目」「表未获资格」「查过但未命中条件不成立」的地方。
    产物里没有这一键（旧版本产物、或本节不走 topic 研究相位）时**不编造**：留 `None`，
    由渲染侧如实写「不可判定」，**不得**写成「本栏目没有问题」。
    """
    if diagnostics_path is None or not Path(diagnostics_path).exists():
        return {}
    payload = json.loads(Path(diagnostics_path).read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for section in (payload.get("sections") or {}).values():
        for row in (section.get("aspects") or ()):
            aspect_id = row.get("aspect_id")
            if aspect_id:
                out[str(aspect_id)] = {
                    "writable_facts": row.get("writable_facts"),
                    "authority_status": row.get("authority_status"),
                    "column_unmet": row.get("column_unmet"),
                    "state": ("qualified" if (row.get("writable_facts") or 0) > 0
                              else "unqualified"),
                }
    return out


def run(repo: Path, out_dir: Path, *,
        fact_states: dict | None = None,
        pack_path: Path | None = None) -> dict:
    from document_structure import navigation as NAV

    fact_states = fact_states or {}
    #: Pack / Writer 去向**只**来自运行现场产物；没有它时那一列如实写「不可得」，
    #: 而不是写「没有」。本页不自己去建 Pack、不替 Writer 清单说话。
    run_tables = load_run_tables(pack_path)
    requirement, aspects_by_id, nav_profile, _contract = build_requirements(repo)
    entries = {e.aspect_id: e for e in nav_profile.entries}
    aspect_order = sorted(entries)

    doc_results: dict[str, dict] = {}
    diagnostics: list[dict] = []
    for document_id in DOCUMENT_ORDER:
        pdf = repo / "data/samples/300750/announcements" / f"{document_id}.pdf"
        if not pdf.exists():
            raise ReadbackError(f"缺少材料 {pdf}")
        index, snapshot, outline, blocks, live = build_index(repo, pdf)
        node_facts = _node_facts(snapshot)
        node_titles = {n.node_id: n.title for n in outline.nodes}
        #: 本份文档的**表对象信道**清点（走 runtime 同一条 `TreeInspectionSession`）。
        #: 它与下面的 span 层落地**不是**同一条信道：span 层说的是「这块表内文字有没有
        #: 成为 `role="body"` 的 `OutlineSpan`」，这里说的是「这个宿主块里有没有**已放行**
        #: 的表对象、它有没有成为 Pack 材料」。两条都记，不得互相顶替。
        table_channel = build_table_channel(live, snapshot, blocks,
                                           node_titles=node_titles)
        aspects: dict[str, dict] = {}
        related_nodes: set[str] = set()
        for aspect_id in aspect_order:
            entry = entries[aspect_id]
            aspect = aspects_by_id[aspect_id]
            decision = NAV.navigate(index, entry, profile=nav_profile)
            read_nodes = set(decision.read_node_ids)
            located = locate(index, entry, aspect)
            related_nodes |= {loc["node_id"] for loc in located if loc.get("strong")}
            read_texts = _read_texts(snapshot, read_nodes)
            fact = fact_states.get(aspect_id, {})
            own_chapter = _own_chapter(index, decision)
            #: 逐位置把三件事显式标出来：**读没读**、**在不在本章**、**名下有多少字符
            #: 没进准入正文**。这三个标记是只读事实，判据与 `_state_for` 同一套。
            for loc in located:
                row = node_facts.get(loc["node_id"]) or {}
                loc["is_read"] = _is_read(loc, read_nodes)
                loc["chapter_determined"] = own_chapter is not None
                loc["in_own_chapter"] = (own_chapter is None
                                         or loc.get("top_chapter_id") == own_chapter)
                loc["material_chars"] = row.get("admitted_chars", 0) + \
                    row.get("residue_chars", 0)
            state, observed, scope = _state_for(
                located=located, read_nodes=read_nodes, read_texts=read_texts,
                node_facts=node_facts,
                entry_decision={"status": decision.status,
                                "fallback_reason": decision.fallback_reason},
                fact_state=fact.get("state"),
                own_chapter=own_chapter)
            located_ids = {loc["node_id"] for loc in located}
            #: 本栏的**表对象信道**读数：逐条列出「宿主节点落在本栏读集 ∪ 定位集里」的已放行
            #: 对象与未放行候选，并逐条标出**宿主节点与本栏的读关系**（读根 ＞ 读集 ＞ 仅定位）。
            #: 它**不**判「这张表是不是本栏的」——宿主归属会把表挂到表前面的标题上，两个方向
            #: 都会错；逐栏免责见 `host_attribution_caveat`，Pack 侧 aspect 归属才是可核对口径。
            cell_channel = build_table_channel_for(
                table_channel, read_nodes=read_nodes,
                read_root_nodes=set(decision.read_root_node_ids),
                located_nodes=located_ids)
            aspects[aspect_id] = {
                "aspect_id": aspect_id,
                "question_id": aspect.question_id,
                "requirement_text": aspect.requirement_text,
                "required_fields": list(aspect.required_fields),
                "content_role": getattr(aspect, "content_role", None),
                "state": state,
                "state_label": STATE_LABELS[state],
                "states_observed": observed,
                "fact_eligibility": fact.get("state", "not_judged_here"),
                "fact_eligibility_basis": fact or None,
                # 逐条 typed 栏目未达原因原样带进单元格（渲染在 `_column_unmet_render`）。
                # 产物没带这一键时这里是 `None`：渲染侧由此写「不可判定」，不写「（无）」。
                "column_unmet": fact.get("column_unmet"),
                "navigation": {
                    "status": decision.status,
                    "fallback_reason": decision.fallback_reason,
                    "nav_keys": list(entry.nav_keys),
                    "parent_keys": list(entry.parent_keys),
                    "subject_head": entry.subject_head,
                    "read_root_node_ids": list(decision.read_root_node_ids),
                    "read_root_titles": [
                        index.normalized_title(n)
                        for n in decision.read_root_node_ids],
                    "supplement_root_node_ids": list(
                        getattr(decision, "supplement_root_node_ids", ())),
                    "supplement_root_titles": [
                        index.normalized_title(n)
                        for n in getattr(decision, "supplement_root_node_ids", ())],
                    "read_node_count": len(decision.read_node_ids),
                    "unread_total": decision.unread_total,
                    "discarded_candidates": [
                        {"node_id": c.node_id, "title": c.title,
                         "score": c.score, "discard_reason": c.discard_reason}
                        for c in decision.ranked
                        if c.discard_reason is not None],
                },
                "located": located,
                "chapter_scope": scope,
                "own_chapter_id": own_chapter,
                "own_chapter_title": (index.normalized_title(own_chapter)
                                      if own_chapter else None),
                "read_texts": read_texts,
                "read_node_ids": sorted(read_nodes),
                "node_facts": {nid: node_facts[nid] for nid in sorted(located_ids)
                               if nid in node_facts},
                #: 表对象信道：候选 / 放行 / 材料三段 + Pack·Writer 去向。与 `state`
                #: （span 层材料到达）**分开**：一栏可以是「span 层拒发」**同时**
                #: 「表对象已进 Pack」，那不是矛盾，是两条信道各说各的。
                "table_channel": cell_channel,
                #: 轴三的**新增一列**（`/3`）：本栏已定位但**未进 Pack**的表对象，逐条带
                #: typed 原因。它把 `/2` 的栏级计数拆到逐对象，回答「那张表卡在哪一步」。
                "located_not_in_pack": located_not_in_pack(
                    cell_channel,
                    run_tables["by_key"].get((document_id, aspect_id), []),
                    pack_available=run_tables["available"]),
                "pack_tables": run_tables["by_key"].get((document_id, aspect_id), []),
                "pack_tables_available": run_tables["available"],
            }

        #: 「六项不能因共同命中一段就算取得」（Contract 的并列子项判据）：把**读集逐字
        #: 相同**的栏目显式列出来——它们拿到的是同一段材料，不能各自记一次覆盖。
        by_signature: dict[tuple, list[str]] = {}
        for aspect_id, cell in aspects.items():
            signature = tuple(cell["read_node_ids"])
            if signature:
                by_signature.setdefault(signature, []).append(aspect_id)
        shared_groups: list[dict] = []
        for signature, members in sorted(by_signature.items()):
            if len(members) < 2:
                continue
            for aspect_id in members:
                aspects[aspect_id]["shared_read_set_with"] = [
                    m for m in members if m != aspect_id]
            shared_groups.append({
                "read_node_ids": list(signature),
                "read_root_titles": [index.normalized_title(n) for n in signature[:1]],
                "aspects": sorted(members),
                "note": ("这些栏目读到的是**同一段**材料：它们各自的实质支撑必须独立判定，"
                         "不得因共同命中一段就算六项都取得。"),
            })

        #: **第二条轴**：材料到手（`state`）与原文支持（`support`）分开判、分开计数。
        #: 共享读集里的每一栏仍然**逐栏**判自己的主体 / 字段（判据逐字见 `_support_for`），
        #: 但状态上显式标注它处在共享读集里——这样"六项都显示材料充分"不会再读成
        #: "六项都被支持"。
        support_counts: dict = {}
        for aspect_id, cell in aspects.items():
            support = _support_for(
                entry=entries[aspect_id], aspect=aspects_by_id[aspect_id],
                read_texts=cell["read_texts"], read_nodes=set(cell["read_node_ids"]),
                shared=bool(cell.get("shared_read_set_with")))
            cell["support"] = support
            support_counts[support["state"]] = support_counts.get(support["state"], 0) + 1

        doc_results[document_id] = {
            "document_id": document_id,
            "document_role": DOCUMENT_ROLES.get(document_id, ""),
            "nodes": len(outline.nodes),
            "spans": len(snapshot.spans),
            "components": len(snapshot.components),
            "evidence_blocks": len(blocks),
            "landing_counts": _component_landing_counts(snapshot),
            "related_node_count": len(related_nodes),
            "shared_read_groups": shared_groups,
            "support_counts": support_counts,
            #: 表对象信道的**文档级**总账（逐对象一条，含未放行候选）。逐栏读数在
            #: `aspects[*]["table_channel"]`；这里是它不过滤的母集，供对账用。
            "table_channel": table_channel,
            "table_channel_summary": {
                "release_rule_version": table_channel["release_rule_version"],
                "material_version": table_channel["material_version"],
                "block_count": table_channel["block_count"],
                "object_count": table_channel["object_count"],
                "material_count": table_channel["material_count"],
                "released_not_material_count":
                    table_channel["released_not_material_count"],
                "unreleased_candidate_count":
                    table_channel["unreleased_candidate_count"],
                "material_refusal_reason_counts":
                    table_channel["material_refusal_reason_counts"],
                "release_refusal_reason_counts":
                    table_channel["release_refusal_reason_counts"],
                "note": table_channel["note"],
            },
            "aspects": aspects,
        }
        diagnostics.extend(table_diagnostics(
            snapshot, blocks, document_id=document_id, node_titles=node_titles,
            scope_node_ids=related_nodes))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "topic_id": TOPIC_ID,
        "navigation_profile_rule_version": nav_profile.rule_version,
        "documents": {k: {kk: vv for kk, vv in v.items() if kk != "aspects"}
                      for k, v in doc_results.items()},
        "matrix": _matrix(doc_results),
        "sections": _sections(doc_results),
        "table_diagnostic_counts": _landing_counts(diagnostics),
        "table_diagnostic_total": len(diagnostics),
        "table_gap_ledger": _table_gap_ledger(doc_results, diagnostics),
        "table_channel_states": list(TABLE_CHANNEL_STATES),
        "table_channel_state_labels": dict(TABLE_CHANNEL_STATE_LABELS),
        "table_channel_note": TABLE_CHANNEL_NOTE,
        #: `business-material-readback/3` 新增一列的词汇表与免责（逐栏明细见
        #: `sections` / `matrix` 里的 `located_not_in_pack`）。
        "located_not_in_pack_reasons": list(LOCATED_NOT_IN_PACK_REASONS),
        "located_not_in_pack_reason_labels": dict(LOCATED_NOT_IN_PACK_LABELS),
        "located_not_in_pack_note": LOCATED_NOT_IN_PACK_NOTE,
        "run_pack_tables": {
            "available": run_tables["available"],
            "path": run_tables["path"],
            "entry_count": run_tables["entry_count"],
            "table_entry_count": run_tables["table_entry_count"],
            "note": ("Pack / Writer 去向**只**读自运行现场的 `material_pack.json`；"
                     "本页不建 Pack、不替 Writer 清单说话。产物不可得时那一列写"
                     "「不可得」而不是「没有」。"),
        },
        "fact_eligibility_source": ("run failure_diagnostics（writable_facts）"
                                    if fact_states else
                                    "未提供：本页不替审核侧判事实资格"),
        "state_vocabulary": list(STATES),
        "state_labels": dict(STATE_LABELS),
        "support_vocabulary": list(SUPPORT_STATES),
        "support_labels": dict(SUPPORT_LABELS),
        "support_caveat": SUPPORT_CAVEAT,
        "support_tally": _support_tally(doc_results),
        "note": ("本页是只读审计：它不产 Pack 材料，也不改变任何运行时判定。"
                 "**表内内容要分两条信道读，不得合并**："
                 "（一）span 层的表内/表邻落地只进「只读诊断视图」，"
                 "**不是** Pack 材料 / 数字事实 / 可发布正文；"
                 "（二）**表对象信道**的已放行对象（`gto-3` 逐表完整证明，"
                 "`harness/graph_table_release.py`）"
                 "经材料信封（`reading_material=True` / `numeric_authority=False`）成为"
                 "Pack 阅读材料，逐条去向以 `run_pack_tables` 与逐栏 `pack_tables` 为准。"
                 "**两条都不能充当数字权威**：需要计算或引用的数字仍须走 "
                 "`FinancialSnapshot` / `FinancialFactPack` 的结构校验。"
                 "`state`（材料到达）与 `support`（原文支持）是**两条不同的轴**："
                 "前者不构成业务覆盖结论。"),
    }
    _write(out_dir, payload, doc_results, diagnostics)
    return payload


def _component_landing_counts(snapshot) -> dict:
    counts: dict = {}
    for component in snapshot.components:
        key = f"{component.landing}|{component.admission_reason}"
        counts[key] = counts.get(key, 0) + 1
    return counts


#: 收入 / 成本 / 毛利三栏（外加产业链位置）：本批**必须**仍如实记「表格拒发」。
TABLE_GAP_ASPECTS = (
    "company_business_main.revenue_breakdown",
    "company_business_main.cost_gross_margin",
    "company_business_main.period_unit_caliber",
    "company_business_main.industry_chain_position",
)


def _table_gap_ledger(doc_results: dict, diagnostics: list[dict]) -> list[dict]:
    """收入 / 成本 / 毛利这几栏**两条信道各读到什么、离可用还差什么**——逐栏逐文档。

    **两条信道分开记，不得互相顶替**：

    * `table_landings` 是 **span 层**的读数（`landing=table_inside/table_adjacency`、
      `admitted=False`、没有 `role="body"` 的 `OutlineSpan` 盖住它们）。它回答的是
      「这段表内文字有没有成为准入正文」，**不是**「这张表进没进 Pack」。
    * `table_channel` 是 **表对象信道**的读数：本栏自有节点集里有哪些**已放行**对象、
      哪些成了 Pack 材料、哪些被 typed 理由挡下，以及 Pack / Writer 去向。

    因此本清单里**没有**「表对象信道尚未接线」这一条——那是过去的状态，今天已不成立。
    `still_missing` 只留今天仍然为真的缺口。
    """
    by_key: dict[tuple[str, str], list[dict]] = {}
    for row in diagnostics:
        by_key.setdefault((row["document_id"], row["node_id"]), []).append(row)

    out: list[dict] = []
    for aspect_id in TABLE_GAP_ASPECTS:
        for document_id in DOCUMENT_ORDER:
            cell = doc_results.get(document_id, {}).get("aspects", {}).get(aspect_id)
            if cell is None:
                continue
            own = set(cell.get("read_node_ids") or ())
            own |= {loc["node_id"] for loc in cell.get("located") or ()}
            rows: list[dict] = []
            seen: set[tuple] = set()
            for node_id in own:
                for row in by_key.get((document_id, node_id), ()):
                    key = (row["page_number"], row["landing"],
                           tuple(row["span_ids"]), row["slice_chars"])
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "page_number": row["page_number"],
                        "landing": row["landing"],
                        "node_id": node_id,
                        "node_title": row["node_title"],
                        "slice_chars": row["slice_chars"],
                        "component_count": row["component_count"],
                        "admitted": row["admitted"],
                        "verdicts": list(row["rejection_reason"]["verdicts"]),
                        "span_ids": list(row["span_ids"]),
                    })
            rows.sort(key=lambda r: (r["page_number"], -r["slice_chars"]))
            channel = cell.get("table_channel") or {}
            channel_objects = list(channel.get("objects") or ())
            channel_blocked = list(channel.get("unreleased_candidates") or ())
            ready_by_rel = channel.get("material_ready_by_host_relation") or {}
            host_read_ready = ready_by_rel.get("read_root", 0) + ready_by_rel.get(
                "read_node", 0)
            pack_tables = list(cell.get("pack_tables") or ())
            in_writer = [m for m in pack_tables if m.get("in_writer_manifest")]
            #: **判「本栏的表进没进 Pack」以 Pack 侧 aspect 归属为准**，不以本页的节点归属为准
            #: （宿主块会被判到表**前面**的标题上，两个方向都会错；逐栏免责见
            #: `table_channel.host_attribution_caveat`）。节点归属只作导航坐标记录。
            still_missing: list[str] = []
            if pack_tables:
                pass  # Pack 侧已有材料：下面按 Writer 清单与 aspect 归属逐条判。
            elif not channel_objects and not channel_blocked:
                still_missing.append(
                    "本栏读集与定位集里**没有**任何表对象候选（连被挡下的候选都没有），"
                    "且 Pack 侧 aspect 归属下也没有本栏的表材料。这不等于来源里没有这张表"
                    "——要下那个结论必须完成**可核查检索**；在取得该结论之前，本栏按"
                    "「系统能力未取得」记，不按「来源缺口」记。")
            elif not channel_objects:
                still_missing.append(
                    f"本栏读集与定位集里**没有**已定位的表对象；本文档另有 "
                    f"{len(channel_blocked)} 条**未获放行**的表（逐条 typed 理由见 "
                    "`table_channel.unreleased_candidates`，**作用域＝整份文档**：拒发记录"
                    "不带宿主块身份，归不到本栏）。放行门挡下的是「这张表还不合格」，"
                    "**不是**「这里没有表」；在构造侧修好之前，本栏按**系统能力缺陷**记。"
                    "注意本栏**既**不能据此断言「本栏有候选」"
                    "、**也**不能断言「本栏没有候选」——两句话在这一支上都不成立，"
                    "归不到本栏的账**不能**拿来替本栏说话。")
            else:
                still_missing.append(
                    f"本栏读集与定位集里有 {len(channel_objects)} 条已放行对象，但 Pack 侧 "
                    "aspect 归属下**没有**本栏的表材料（或该产物不可得）。"
                    "注意：**不能**反过来按节点归属断言「本栏没有这张表」——宿主块会被判到"
                    "表前面的标题上（见 `host_attribution_caveat`）；本栏按「去向未对上」记，"
                    "要查的是 Pack 侧绑定与派发，不是来源。")
            if pack_tables and not in_writer:
                still_missing.append(
                    f"本栏的 {len(pack_tables)} 份表材料进了 Pack，但**没有**一份在 Writer 的"
                    "精确材料清单里：要查的是清单构造，不是研究侧取材。")
            if pack_tables:
                #: `AspectResearchResult.material_ids` **按设计**不收表对象材料（那一列是引用索引与
                #: 覆盖/集合门的判据面；表材料与正文材料同宿主块，混进去会让按
                #: `authority.evidence_id` 建的引用索引在同一块上不唯一）。表材料的栏目归属走
                #: `table_material_aspects` → RMD `aspect_ids` 这条**独立轴**
                #: （`harness/topic_runtime.py` 的 `build_material_dispositions`，
                #: 入参名 `extra_material_aspects`）。
                #:
                #: 所以 `aspect_ids_from_aspect_results` 为空是**预期行为**，本页**不得**把它读成
                #: 绑定缺口——那正是把「两条轴各管一段」读成「这条链没接上」。
                #: **真**缺口只有一种：两条轴在本栏都读不到。
                if not any(m["aspect_ids"] for m in pack_tables):
                    still_missing.append(
                        "本栏的表材料在 RMD `aspect_ids` 与 `aspect_ids_from_aspect_results` "
                        "**两条轴上都读不到本栏**：材料进了 Pack，却没有任何一个栏目认领它。"
                        "这是真的绑定缺口，要查的是派发与处置记录的构造（不是来源）。")
                elif not any(m["aspect_ids_from_aspect_results"] for m in pack_tables):
                    still_missing.append(
                        f"本栏 {len(pack_tables)} 份表材料的栏目归属**只**落在 RMD "
                        "`aspect_ids` 上，`aspect_ids_from_aspect_results` 一律为空。"
                        "这不缺——`AspectResearchResult.material_ids` **按设计**不收表对象材料"
                        "（那一列是引用索引与覆盖/集合门的判据面；表材料与正文材料同宿主块，"
                        "混进去会让按 `authority.evidence_id` 建的引用索引在同一块上不唯一）。"
                        "记在这里是为了让读的人**知道该取哪条轴**：读表材料的栏目归属必须取 "
                        "RMD `aspect_ids`；只按 `aspect_results[].material_ids` 取的消费方"
                        "**看不到这些表**，那是取错轴，不是缺口。")
            if channel_objects and not host_read_ready:
                still_missing.append(
                    "本栏**读到的节点**（读根与读集）上没有任何已成材料的表对象；已成材料的"
                    f"对象全部落在「仅定位」节点上（{ready_by_rel.get('located_only', 0)} 张）。"
                    "这条只说「按宿主归属对不上」，**不**说本栏没有表——见上面的宿主归属免责。")
            #: `business-material-readback/3`：逐条回答「本栏定位到的那张表卡在哪一步」。
            #: 这条缺口**必须**进 `still_missing`——只写在表里而不进缺口清单，读的人会以为
            #: 「Pack 不可得」和「Pack 没走到它」是同一种情况。
            not_in_pack = cell.get("located_not_in_pack") or {}
            _bad = {k: v for k, v in (not_in_pack.get("reason_counts") or {}).items()
                    if k in ("not_in_run_pack", "pack_unavailable", "identity_unavailable")}
            if _bad:
                still_missing.append(
                    "本栏**已定位但未进 Pack** 的表对象 "
                    f"{not_in_pack.get('count', 0)} 条，其中 "
                    + "／".join(f"{LOCATED_NOT_IN_PACK_LABELS[k]} {v} 条"
                                for k, v in sorted(_bad.items()))
                    + "。`not_in_run_pack` 的成因在**本次派发域／预算／是否走到 Pack**"
                    "（本栏定位到了、也已成材料，但运行现场的 Pack 里没有它）；"
                    "`pack_unavailable` 与 `identity_unavailable` 是**不可判定**，"
                    "**不得**读成「没有进 Pack」，更**不得**读成「来源里没有这张表」"
                    "（那要可核查检索）。逐条见 `located_not_in_pack`。")
            still_missing.extend([
                "span 层的表内/表邻落地仍是**拒发**：`admitted=False`、没有 `role=\"body\"` "
                "的 `OutlineSpan` 覆盖它们。这条**不**随表对象信道走通而消失——它是"
                "「表内文字没有变成准入正文」，与「表对象进了 Pack」是两件事。",
                "本轮**没有**、也**不允许**用「把表内文字读成一段正文」来充当表格修复："
                "只读诊断视图里的每一行都自报 `not_pack_material` / `not_numeric_fact` / "
                "`not_publishable_prose`，任何正文、表格或结论都不得引用。",
                "表的数字要成为**可用事实**还需各自的数字权威（Fact Registry 侧的资格与 "
                "authority）——表对象材料一律 `numeric_authority=False`，故 `fact_eligibility` "
                "如实留 `not_judged_here` 或 `unqualified`。把表内数字变成合格事实需要"
                "路径 A 预验证（触及冻结的 Contract / SourcePolicy），不在本批范围内。",
            ])
            out.append({
                "aspect_id": aspect_id,
                "document_id": document_id,
                "state": cell["state"],
                "state_label": cell["state_label"],
                "table_landings": rows,
                "table_landing_chars": sum(r["slice_chars"] for r in rows),
                "table_channel": channel,
                #: 轴三新增一列（`/3`）：本栏已定位但未进 Pack 的逐条对象 ＋ typed 原因。
                "located_not_in_pack": not_in_pack,
                "pack_tables": pack_tables,
                "still_missing": still_missing,
            })
    return out


def _landing_counts(rows: list[dict]) -> dict:
    counts: dict = {}
    for row in rows:
        key = (row["document_id"], row["landing"])
        counts[str(key)] = counts.get(str(key), 0) + 1
    return counts


def _support_tally(doc_results: dict) -> dict:
    """两条轴**逐文档分开展示**的计数。刻意不合成任何单一分数或"通过率"。"""
    out: dict = {}
    for document_id in DOCUMENT_ORDER:
        doc = doc_results.get(document_id)
        if doc is None:
            continue
        reach: dict = {}
        support: dict = {}
        for cell in doc["aspects"].values():
            reach[cell["state"]] = reach.get(cell["state"], 0) + 1
            key = cell["support"]["state"]
            support[key] = support.get(key, 0) + 1
        out[document_id] = {"material_reach": reach,
                            "original_text_support": support}
    return out


def _matrix(doc_results: dict) -> list[dict]:
    out: list[dict] = []
    for _name, aspect_ids in BUSINESS_GROUPS:
        for aspect_id in aspect_ids:
            cells = []
            for document_id in DOCUMENT_ORDER:
                cell = doc_results.get(document_id, {}).get("aspects", {}).get(aspect_id)
                if cell is None:
                    continue
                cells.append({
                    "document_id": document_id,
                    "state": cell["state"],
                    "state_label": cell["state_label"],
                    "states_observed": cell["states_observed"],
                    "located_count": len(cell["located"]),
                    "substantive_read_chars": sum(
                        t["char_length"] for t in cell["read_texts"]
                        if not t["title_like"] and not t["form_like"]),
                    "read_root_titles": cell["navigation"]["read_root_titles"],
                    "fallback_reason": cell["navigation"]["fallback_reason"],
                    "support_state": (cell.get("support") or {}).get("state"),
                    "support_label": (cell.get("support") or {}).get("label"),
                    "supplement_root_titles": cell["navigation"].get(
                        "supplement_root_titles", []),
                    "own_chapter_id": cell.get("own_chapter_id"),
                    "own_chapter_title": cell.get("own_chapter_title"),
                    "unread_in_chapter": (cell.get("chapter_scope")
                                          or {}).get("unread_in_chapter", []),
                    "unread_out_of_chapter": (cell.get("chapter_scope")
                                              or {}).get("unread_out_of_chapter", []),
                    # 逐条 typed 栏目未达原因：JSON 与 Markdown 必须是**同一份**判读，因此这里
                    # 与 `_cell_summary` 走同一个渲染器，不各写一套。
                    "column_unmet": _column_unmet_render(cell),
                    #: **轴三 · 表对象信道**（与上面两轴不同的一条供给路径）：逐条对象、
                    #: 未放行候选、以及 Pack / Writer 去向。它**不**参与轴一/轴二的判读。
                    "table_channel_objects": list(
                        (cell.get("table_channel") or {}).get("objects") or ()),
                    "table_channel_unreleased": list(
                        (cell.get("table_channel") or {}).get(
                            "unreleased_candidates") or ()),
                    "material_ready_by_host_relation": dict(
                        (cell.get("table_channel") or {}).get(
                            "material_ready_by_host_relation") or {}),
                    "host_attribution_caveat": (
                        cell.get("table_channel") or {}).get("host_attribution_caveat"),
                    #: 轴三新增一列（`/3`）：已定位但未进 Pack 的**逐条**表对象 ＋ typed 原因。
                    "located_not_in_pack": cell.get("located_not_in_pack"),
                    "pack_tables": list(cell.get("pack_tables") or ()),
                    "pack_tables_available": bool(cell.get("pack_tables_available")),
                })
            #: 轴二的栏级判读随矩阵一起落进 JSON（不只写在 Markdown 里）：读回页与
            #: JSON 必须是**同一份**判读，否则两个消费方各读各的口径。
            out.append({"aspect_id": aspect_id, "cells": cells,
                        "support_judgment": _column_support_verdict(
                            {c["document_id"]: c for c in cells})})
    return out


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def _write(out_dir: Path, payload: dict, doc_results: dict,
           diagnostics: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "business_material_matrix.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "business_material_matrix.md").write_text(
        render_matrix_md(payload), encoding="utf-8")
    (out_dir / "business_readback.md").write_text(
        render_readback_md(payload, doc_results, diagnostics), encoding="utf-8")
    (out_dir / "table_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "per_document_aspects.json").write_text(
        json.dumps({k: v["aspects"] for k, v in doc_results.items()},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def render_matrix_md(payload: dict) -> str:
    lines = [f"# company_business 逐栏目取材矩阵（{SCHEMA_VERSION}）", ""]
    lines.append(f"- 导航 profile 规则版本：`{payload['navigation_profile_rule_version']}`")
    lines.append("- **轴一 · 材料到达**（优势序，前为好）："
                 + " ＞ ".join(f"{STATE_LABELS[c]}({c})" for c in STATES))
    lines.append("- **轴二 · 原文支持**：" + " ＞ ".join(
        f"{SUPPORT_LABELS[c]}({c})" for c in SUPPORT_STATES))
    lines.append("")
    lines.append("> 材料份数 / 来源登记数 / 标题命中数 / Pack retained 状态**都不能**替代栏目覆盖。"
                 "逐 (栏目, 文档) 的「材料到达」由「定位到的原文位置 × 是否真被读取 × 读到的"
                 "文本完整性」决定；它**不**构成业务覆盖结论。")
    lines.append("> " + SUPPORT_CAVEAT)
    lines.append("")
    lines.append("## 轴一 · 材料到达")
    lines.append("")
    header = "| 栏目 | " + " | ".join(DOCUMENT_ORDER) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(DOCUMENT_ORDER) + 1))
    for row in payload["matrix"]:
        cells = {c["document_id"]: c for c in row["cells"]}
        rendered = []
        for document_id in DOCUMENT_ORDER:
            cell = cells.get(document_id)
            rendered.append("—" if cell is None
                            else f"{cell['state_label']}`{cell['state']}`"
                                 f"（定位 {cell['located_count']}）")
        lines.append(f"| `{row['aspect_id']}` | " + " | ".join(rendered) + " |")
    lines.append("")
    lines.append("## 轴二 · 原文支持（逐 aspect 单独判）")
    lines.append("")
    lines.append(header)
    lines.append("|" + "---|" * (len(DOCUMENT_ORDER) + 1))
    for row in payload["matrix"]:
        cells = {c["document_id"]: c for c in row["cells"]}
        rendered = []
        for document_id in DOCUMENT_ORDER:
            cell = cells.get(document_id)
            rendered.append("—" if cell is None
                            else f"{(cell.get('support_label') or '（未判）')}"
                                 f"`{cell.get('support_state') or '-'}`")
        lines.append(f"| `{row['aspect_id']}` | " + " | ".join(rendered) + " |")
    lines.append("")
    lines.append("## 逐单元格明细")
    for row in payload["matrix"]:
        lines.append("")
        lines.append(f"### `{row['aspect_id']}`")
        for cell in row["cells"]:
            lines.append(f"- **{cell['document_id']}**：{cell['state_label']}"
                         f"（`{cell['state']}`）")
            lines.append(f"  - 成立过的状态：{cell['states_observed']}")
            lines.append(f"  - 独立定位到的位置数：{cell['located_count']}")
            lines.append(f"  - 读根：{cell['read_root_titles'] or '（无）'}")
            if cell.get("supplement_root_titles"):
                lines.append(f"  - **补读根**（`ANP-7` 主体补读）："
                             f"{cell['supplement_root_titles']}")
            if cell["fallback_reason"]:
                lines.append(f"  - fallback：`{cell['fallback_reason']}`")
            lines.append(f"  - 本栏所在章：{cell.get('own_chapter_title') or '（判不出）'}"
                         f"｜本章未读带料位置 {len(cell.get('unread_in_chapter') or [])} 处"
                         f"｜别章强定位未读带料位置 "
                         f"{len(cell.get('unread_out_of_chapter') or [])} 处")
            for loc in cell.get("unread_out_of_chapter") or []:
                lines.append(f"    - 别章：`{' / '.join(loc['path_titles'])}` → "
                             f"**{loc['title']}**（{loc['material_chars']} 字符）")
    lines.append("")
    lines.append("## 轴三 · 表对象信道（与上面两轴**不同**的一条供给路径）")
    lines.append("")
    lines.append("> 上面两轴说的是「表内**文字**有没有成为准入正文」；这一轴说的是"
                 "「那张**表**有没有被放行、有没有成为 Pack 材料、有没有进 Writer 精确材料清单」。"
                 "两条路径互不顶替：一栏可以同时是「span 层拒发」与「表对象已进 Pack」。")
    lines.append(">")
    lines.append("> 判据与逐条理由见 `table_gap_ledger` / `per_document_aspects.json` 的 "
                 "`table_channel`；`table_not_released` 一档是**span 层专有**的判断，"
                 "**不**代表表对象信道未放行。")
    lines.append("")
    lines.append(f"| 栏目 | " + " | ".join(DOCUMENT_ORDER) + " |")
    lines.append("|" + "---|" * (len(DOCUMENT_ORDER) + 1))
    for row in payload["matrix"]:
        cells = {c["document_id"]: c for c in row["cells"]}
        rendered = []
        for document_id in DOCUMENT_ORDER:
            cell = cells.get(document_id)
            if cell is None:
                rendered.append("—")
                continue
            objects = cell.get("table_channel_objects") or []
            blocked = cell.get("table_channel_unreleased") or []
            by_rel = cell.get("material_ready_by_host_relation") or {}
            packed = cell.get("pack_tables") or []
            writer = [m for m in packed if m.get("in_writer_manifest")]
            if not cell.get("pack_tables_available"):
                tail = "｜Pack 不可得"
            elif packed:
                tail = f"｜**Pack {len(packed)}／Writer {len(writer)}**"
            else:
                tail = "｜Pack 0"
            if not objects and not blocked:
                rendered.append(f"宿主归属查不到表对象{tail}")
                continue
            rendered.append(f"宿主读关系 读根 {by_rel.get('read_root', 0)}／"
                            f"读集 {by_rel.get('read_node', 0)}／"
                            f"仅定位 {by_rel.get('located_only', 0)}"
                            f"（放行 {len(objects)}／未放行 {len(blocked)}）{tail}")
        lines.append(f"| `{row['aspect_id']}` | " + " | ".join(rendered) + " |")
    lines.append("")
    lines.append("## 轴三之二 · 本栏**已定位但未进 Pack**的表对象（逐条 ＋ typed 原因）")
    lines.append("")
    lines.append("> " + LOCATED_NOT_IN_PACK_NOTE)
    lines.append("")
    lines.append(f"| 栏目 | " + " | ".join(DOCUMENT_ORDER) + " |")
    lines.append("|" + "---|" * (len(DOCUMENT_ORDER) + 1))
    for row in payload["matrix"]:
        cells = {c["document_id"]: c for c in row["cells"]}
        rendered = []
        for document_id in DOCUMENT_ORDER:
            cell = cells.get(document_id)
            if cell is None:
                rendered.append("—")
                continue
            column = cell.get("located_not_in_pack")
            if not column:
                rendered.append("（未判）")
                continue
            counts = column.get("reason_counts") or {}
            if not column.get("count") and column.get("in_pack_count"):
                rendered.append(f"已定位 {column['in_pack_count']} 条**全部**进 Pack")
            elif not column.get("count"):
                rendered.append("无（已定位范围内没有表对象）")
            else:
                rendered.append(f"**{column['count']}** 条（"
                                + "／".join(
                                    f"{LOCATED_NOT_IN_PACK_LABELS[k]} {v}"
                                    for k, v in sorted(
                                        counts.items(),
                                        key=lambda kv: LOCATED_NOT_IN_PACK_REASONS.index(kv[0])))
                                + "）")
        lines.append(f"| `{row['aspect_id']}` | " + " | ".join(rendered) + " |")
    lines.append("")
    lines.append("### 逐条明细（只列未进 Pack 的那些）")
    for row in payload["matrix"]:
        for cell in row["cells"]:
            column = cell.get("located_not_in_pack") or {}
            if not column.get("rows"):
                continue
            lines.append("")
            lines.append(f"- `{row['aspect_id']}` × **{cell['document_id']}**"
                         f"（Pack {'可得' if column.get('pack_available') else '不可得'}）")
            for item in column["rows"]:
                scope = ("" if item.get("scope") != "document"
                         else "｜**作用域＝整份文档**（拒发记录不带宿主块，归不到本栏）")
                lines.append(
                    f"  - p{item.get('page_number')} "
                    f"「{item.get('table_title') or '（无表题）'}」"
                    f"｜放行 `{(item.get('release_id') or '（未放行）')[:16]}`"
                    f"｜表 `{(item.get('table_id') or '（无）')[:16]}`"
                    f"｜材料 `{item.get('material_id') or '（无）'}`"
                    f"｜宿主读关系 `{item.get('host_read_relation')}`"
                    f"｜**{item.get('reason_label')}**"
                    f"`{item.get('reason')}`"
                    + (f"（`{item['reason_detail']}`）" if item.get("reason_detail") else "")
                    #: 逐条缺陷码与 `render_readback_md` 那一支**同一批判据**：只给一个主理由
                    #: 等于把「这张表除了主因还有哪些没通过」的账吃掉。两个出口必须一样全。
                    + (f"｜缺陷 `{'/'.join(item['defect_codes'])}`"
                       if item.get("defect_codes") else "")
                    + scope)
    lines.append("")
    return "\n".join(lines)


def render_readback_md(payload: dict, doc_results: dict,
                       diagnostics: list[dict]) -> str:
    lines = ["# 主营业务材料包读回页（按业务结构）", ""]
    lines.append(f"- 规则版本：`{payload['navigation_profile_rule_version']}`")
    lines.append("- 视图性质：**只读**。表内内容分两条信道读：**span 层**的表内/表邻落地"
                 "只出现在「只读诊断视图」；**表对象信道**的已放行对象经材料信封"
                 "（`reading_material=True` / `numeric_authority=False`）成为 Pack 阅读材料。"
                 "**两条都不冒充数字事实 / 可发布正文。**")
    lines.append("")
    lines.append("## 三份材料的来源角色")
    for document_id in DOCUMENT_ORDER:
        info = payload["documents"].get(document_id, {})
        lines.append(f"- **{document_id}**：{info.get('document_role', '')}"
                     f"（标题节点 {info.get('nodes')} / span {info.get('spans')} / "
                     f"Evidence 块 {info.get('evidence_blocks')}）")
    lines.append("")
    lines.append("## 共享读集：同一段材料支撑了多个栏目")
    lines.append("")
    lines.append("> 判据：**读集逐字相同**。这些栏目的实质支撑必须**独立**判定——"
                 "不得因为共同命中一段就把六项都算取得。")
    lines.append("")
    any_shared = False
    for document_id in DOCUMENT_ORDER:
        for group in payload["documents"].get(document_id, {}).get(
                "shared_read_groups", ()):
            any_shared = True
            lines.append(f"- **{document_id}**：读根 `{group['read_root_titles']}`"
                         f"（读集 {len(group['read_node_ids'])} 个节点）"
                         f"同时支撑 {len(group['aspects'])} 个栏目：")
            for aspect_id in group["aspects"]:
                lines.append(f"  - `{aspect_id}`")
    if not any_shared:
        lines.append("- （本批未发现逐字相同的读集）")
    lines.append("")
    lines.append("## 逐栏目结论：两条轴分开看")
    lines.append("")
    lines.append("> **这两条轴回答两个不同的问题，必须分开读。**")
    lines.append("> - **轴一 · 材料到达**：这一栏的材料**到手了没有**"
                 "（材料充分／部分／未取得）。")
    lines.append("> - **轴二 · 原文支持**：这一栏**自己读到的那几节正文**里，"
                 "逐字出现过它的主体与声明字段吗。")
    lines.append("> ")
    lines.append("> **轴一不构成业务覆盖结论**：六项经营模式同时命中同一节正文时，"
                 "轴一在六个单元格上都会显示「取得完整相关材料」——那是**一段**材料，"
                 "不是六项各自的支撑。逐项支持必须按轴二逐项复核。")
    lines.append("> ")
    lines.append("> **轴一按章计**：「已定位但未读取」只由**本栏所在那一章**里的未读"
                 "带料位置触发。年报里同批探针词会在财务报告等**别章**命中"
                 "「24、收入」「（二）收入确认」这类节点；这些位置**逐条列在**"
                 "「别章强定位未读」里，不藏，但不顶掉本章的结论——否则"
                 "「主营业务这一节到底读到没有」会被别处的未读淹没。")
    lines.append("> " + SUPPORT_CAVEAT)
    lines.append("")
    lines.append("| 栏目 | 轴一 · 材料到达 | 轴二 · 逐份支持责任 | 2025 年报 | 2024 年报 | 募集说明书 |")
    lines.append("|---|---|---|---|---|---|")
    verdict_counts: dict = {}
    support_verdict_counts: dict = {}
    support_by_document: dict = {d: {} for d in DOCUMENT_ORDER}
    for row in payload["matrix"]:
        cells = {c["document_id"]: c for c in row["cells"]}
        verdict = _verdict(cells)
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        rendered = [cells[d]["state_label"] if d in cells else "—"
                    for d in DOCUMENT_ORDER]
        #: 轴二**逐份**判：先按 (栏目, 文档) 各自读自己的读集支持状态，再按**责任份**
        #: （锚 = 本期主语料）给一条栏级判读。不取最好、也不取最差——两者都是拿一份
        #: 替代三份；「只有历史份支持」是一档**必须显式说出**的结论。
        judgment = row.get("support_judgment") or _column_support_verdict(cells)
        support_verdict_counts[judgment["verdict"]] = (
            support_verdict_counts.get(judgment["verdict"], 0) + 1)
        for _d, _s in judgment["per_document"].items():
            if _s:
                support_by_document[_d][_s] = support_by_document[_d].get(_s, 0) + 1
        lines.append(f"| `{row['aspect_id']}` | **{verdict}** | "
                     f"**{judgment['label']}** | "
                     + " | ".join(rendered) + " |")
    lines.append("")
    lines.append(f"- 轴一（材料到达）分布：{verdict_counts}")
    lines.append("- 轴二（逐份支持责任）分布："
                 + json.dumps(support_verdict_counts, ensure_ascii=False)
                 + "｜判读口径：" + "；".join(
                     f"`{k}` = {v}" for k, v in COLUMN_SUPPORT_VERDICTS.items()))
    lines.append("- 轴二逐份原文支持状态（**分份列出，不合成一个数**）："
                 + json.dumps(support_by_document, ensure_ascii=False))
    lines.append("")

    for group in payload["sections"]["groups"]:
        lines.append(f"## {group['group']}")
        lines.append("")
        for row in group["rows"]:
            lines.append(f"### `{row['aspect_id']}`")
            for cell in row["cells"]:
                document_id = cell["document_id"]
                lines.append(f"#### {document_id} — {cell['state_label']}"
                             f"（`{cell['state']}`）")
                _duty = DOCUMENT_DUTY.get(document_id, {})
                lines.append(f"- 期间/披露日期：{DOCUMENT_ROLES.get(document_id, '')}")
                lines.append(
                    f"- 来源角色：`{_duty.get('source_role', '（未登记）')}`"
                    f"｜锚={'是' if _duty.get('is_anchor') else '否'}"
                    f"｜责任：{_duty.get('duty', '（未登记）')}")
                lines.append(f"- 声明字段：{cell['required_fields']}｜"
                             f"content_role：`{cell['content_role']}`｜"
                             f"事实资格：`{cell['fact_eligibility']}`")
                lines.append(f"- 导航：status=`{cell['navigation']['status']}` "
                             f"fallback=`{cell['navigation']['fallback_reason']}` "
                             f"读根={cell['read_root_titles'] or '（无）'}")
                if cell.get("supplement_root_titles"):
                    lines.append(f"- **主体补读根**（`anp-7` 有界补读）："
                                 f"{cell['supplement_root_titles']}")
                scope = cell.get("chapter_scope") or {}
                if cell.get("own_chapter_id"):
                    lines.append(f"- 本栏所在章："
                                 f"{cell.get('own_chapter_title')}"
                                 f"（`{cell.get('own_chapter_id')}`）")
                else:
                    lines.append("- 本栏所在章：**判不出**——本栏导航**没有选中任何读根**"
                                 "（读集为空）。此时不编一个章节出来：下面"
                                 "「本章未读」按**最保守**口径列出全部未读带料位置。")
                here = scope.get("unread_in_chapter") or []
                elsewhere = scope.get("unread_out_of_chapter") or []
                _here_label = "本章" if cell.get("own_chapter_id") else "（章节未定）"
                lines.append(f"  - **{_here_label}**未读的带料位置 {len(here)} 处"
                             + ("：" + "；".join(
                                 f"`{' / '.join(b['path_titles'])}` → "
                                 f"**{b['title']}**（{b['material_chars']} 字符未入正文）"
                                 for b in here[:8]) if here else "（本章无遗漏）"))
                lines.append(f"  - **别章**强定位未读的带料位置 {len(elsewhere)} 处"
                             + ("（**不是**本栏所在章的缺口，逐条列出供复核）："
                                + "；".join(
                                    f"`{' / '.join(b['path_titles'])}` → "
                                    f"**{b['title']}**（{b['material_chars']} 字符）"
                                    for b in elsewhere[:8])
                                if elsewhere else "（无）"))
                if elsewhere:
                    lines.append("    - 说明：同批探针词会在财务报告等**别章**命中"
                                 "「24、收入」「（二）收入确认」这类节点。"
                                 "它们照样列在这里，不藏；但本栏材料到没到手，"
                                 "判据是**本章**那一行。")
                if cell.get("shared_read_set_with"):
                    lines.append(f"- ⚠ **共享读集**：本栏与 "
                                 f"{len(cell['shared_read_set_with'])} 个栏目"
                                 f"（{', '.join('`' + a + '`' for a in cell['shared_read_set_with'])}）"
                                 "读到的是**逐字相同**的节点集合")
                support = cell.get("support")
                if support:
                    lines.append(
                        f"- 轴二 · 原文支持：**{support['label']}**"
                        f"（`{support['state']}`）"
                        f"｜主体 `{support['subject_head']}` "
                        f"命中={support['subject_hit']}"
                        f"｜声明字段命中 "
                        f"{support['field_hits'] or '（无）'}"
                        f"｜未命中 {support['field_misses'] or '（无）'}")
                    if support.get("evidence"):
                        ev = support["evidence"]
                        lines.append(f"  - 命中出处：`{ev['node_id']}` "
                                     f"p{ev['page_range'][0]}–{ev['page_range'][1]}"
                                     f"（{ev['char_length']} 字符）"
                                     f"{_clip(ev['excerpt'], 160)}")
                if cell["located"]:
                    lines.append("- 独立定位到的原 PDF 位置（逐位标注 "
                                 "**已读/未读 · 本章/别章 · 名下未入正文的字符数**）：")
                    for loc in cell["located"][:12]:
                        lines.append(f"  - `{' / '.join(loc['path_titles'])}` → "
                                     f"**{loc['title']}**"
                                     f"（tier={loc['locate_tiers']}, "
                                     f"邻接={loc['subject_adjacency']}"
                                     f"｜{_loc_marks(loc)}）")
                    if len(cell["located"]) > 12:
                        lines.append(f"  - …另有 {len(cell['located']) - 12} 处")
                    note = _strong_term_note(cell)
                    if note:
                        lines.append(f"  - ⚠ {note}")
                else:
                    lines.append("- 独立定位到的原 PDF 位置：**无**")
                body = cell["substantive_read_texts"]
                if body:
                    lines.append(f"- 读到的实质正文 {len(body)} 段，"
                                 f"合计 {cell['substantive_read_chars']} 字符：")
                    for text in body[:4]:
                        page = text["page_range"]
                        lines.append(f"  - p{page[0]}–{page[1]}（{text['char_length']} 字符）："
                                     f"{_clip(text['text'], 160)}")
                    if len(body) > 4:
                        lines.append(f"  - …另有 {len(body) - 4} 段")
                else:
                    lines.append("- 读到的实质正文：**0 段**"
                                 "（读到的只是标题 / 勾选行）")
                lines.append(f"- 未解决问题：{_open_issues(cell)}")
                lines.append("")
    del doc_results

    lines.append("## 收入 / 成本 / 毛利三栏：两条信道各读到什么")
    lines.append("")
    lines.append("> **两条信道分开读，不得互相顶替**：")
    lines.append("> * **span 层**回答「这段表内文字有没有成为 `role=\"body\"` 的准入正文」——"
                 "它**不是**在回答「这张表进没进 Pack」；")
    lines.append("> * **表对象信道**回答「这张表有没有被放行、有没有成为 Pack 材料、有没有进 "
                 "Writer 精确材料清单」——它走的是另一条供给路径。")
    lines.append(">")
    lines.append("> 一栏可以同时是「span 层拒发」与「表对象已进 Pack」："
                 "那不是矛盾，是两条信道各说各的。")
    lines.append("")
    _pack_side = payload.get("run_pack_tables") or {}
    if _pack_side.get("available"):
        lines.append(f"- Pack 去向读数：读自 `{_pack_side['path']}`"
                     f"（逐节条目 {_pack_side['entry_count']} 条，其中表对象材料 "
                     f"{_pack_side['table_entry_count']} 份）。")
    else:
        lines.append("- Pack 去向读数：**产物不可得**——本次没有传 `--pack` 或该文件不存在。"
                     "此时下表里的「Pack/Writer 去向」一律写「不可得」，"
                     "**不**写「没有」。")
    lines.append("")
    for row in payload.get("table_gap_ledger") or ():
        lines.append(f"### `{row['aspect_id']}` · {row['document_id']} — "
                     f"{row['state_label']}（`{row['state']}`）")
        channel = row.get("table_channel") or {}
        objects = list(channel.get("objects") or ())
        blocked = list(channel.get("unreleased_candidates") or ())
        lines.append(f"- **表对象信道**：读集内已放行对象 {len(objects)} 条"
                     f"；未获放行候选 {len(blocked)} 条"
                     f"｜放行规则版本 `{channel.get('release_rule_version')}`"
                     f"｜材料信封版本 `{channel.get('material_version')}`")
        lines.append("  - 已成材料的对象按**宿主节点读关系**分（这只说「宿主块归到了哪儿、"
                     "那个节点本栏读没读」，**不**说「这张表是本栏的」）：")
        ready_by_rel = channel.get("material_ready_by_host_relation") or {}
        for rel in channel.get("host_read_relations") or ():
            lines.append(f"    - `{rel}`（{(channel.get('host_read_relation_labels') or {}).get(rel, '')}）"
                         f"：{ready_by_rel.get(rel, 0)} 张已成材料")
        lines.append(f"  - **宿主归属免责**：{channel.get('host_attribution_caveat')}")
        for obj in objects:
            state_label = (channel.get("state_labels") or {}).get(
                obj["table_channel_state"], obj["table_channel_state"])
            tail = ("" if obj["table_channel_state"] == "material_ready"
                    else f"｜成不了材料的类型化理由：`{obj['material_refusal_reason']}`")
            lines.append(f"  - p{obj['page_number']}｜**{state_label}**｜"
                         f"宿主读关系 `{obj['host_read_relation']}`｜"
                         f"「{obj['table_title']}」｜表体 {obj['body_row_count']} 行 × "
                         f"{obj['column_count']} 列｜结构 `{obj['structure_state']}`"
                         f"｜跨块补读={obj['cross_block']}"
                         f"｜宿主节点 `{obj['matched_node_id']}` "
                         f"{obj['matched_node_title']}{tail}")
        for cand in blocked:
            lines.append(f"  - p{cand['page_number']}｜**未获放行**｜"
                         f"宿主读关系 `{cand['host_read_relation']}`｜"
                         f"「{cand['table_title']}」｜放行门理由："
                         f"`{cand['release_refusal_reason']}`"
                         f"｜宿主节点 `{cand['matched_node_id']}` "
                         f"{cand['matched_node_title']}")
        if blocked:
            lines.append(f"  - 未获放行的 typed 理由分布："
                         f"{channel.get('release_refusal_reason_counts')}")
        pack_tables = list(row.get("pack_tables") or ())
        if not row.get("pack_tables_available", True):
            lines.append("- **Pack / Writer 去向**：不可得（未提供运行现场 `material_pack.json`）")
        elif not pack_tables:
            lines.append("- **Pack / Writer 去向**：本栏在本份材料的 Pack 里**没有**表对象材料"
                          "（注意：这与「本栏没有已放行对象」是两件事，见上一行）")
        else:
            for material in pack_tables:
                lines.append(f"  - Pack 材料 `{material['material_id']}`｜"
                             f"p{material['page_number']}｜「{material['table_title']}」｜"
                             f"准入={material['admission_state']}/"
                             f"{material['retention_state']}｜"
                             f"进 Writer 精确材料清单="
                             f"{material['in_writer_manifest']}"
                             f"（查询={material['writer_manifest_query']}）｜"
                             f"aspect 归属（Pack 结果）="
                             f"{material['aspect_ids_from_aspect_results']}"
                             f"／（处置记录）={material['disposition_aspect_ids']}"
                             f"｜两者一致={material['aspect_attribution_agrees']}")
            if any(not m["aspect_attribution_agrees"] for m in pack_tables):
                lines.append("  - **读法**：上面有材料的 aspect 归属在「Pack aspect 结果」与"
                             "「处置记录」之间**不一致**。对**表对象材料**这是**预期**，不是缺口："
                             "`AspectResearchResult.material_ids` 按设计不收表材料"
                             "（那一列是引用索引与覆盖/集合门的判据面，表材料与正文材料同宿主块，"
                             "混进去会让按 `authority.evidence_id` 建的引用索引在同一块上不唯一），"
                             "表材料的栏目归属只在处置记录（RMD）`aspect_ids` 这条轴上。"
                             "两条都原样列出是为了让人看清该取哪条轴；**只有**两条轴在本栏"
                             "**都**读不到时才是真的绑定缺口。")
        #: `business-material-readback/3` 新增一列：本栏**已定位但未进 Pack**的表对象。
        #: 它与上一段**不重复**：上一段答「Pack 里有什么」，这一段答「本栏定位到的表
        #: 卡在哪一步、为什么没到 Pack」。两段都必须逐条留痕，缺一段就分不清
        #: 「放行门挡下」/「材料化挡下」/「Pack 没走到它」。
        column = row.get("located_not_in_pack") or {}
        if column:
            lines.append(f"- **本栏已定位但未进 Pack 的表对象**："
                         f"{column.get('count', 0)} 条"
                         f"（其中本次 Pack 里能对上的 {column.get('in_pack_count', 0)} 条，"
                         f"不列在此）"
                         f"｜Pack {'可得' if column.get('pack_available') else '**不可得**'}")
            for item in column.get("rows") or ():
                scope = ("" if item.get("scope") != "document"
                         else "｜**作用域＝整份文档**（拒发记录不带宿主块，本栏归不到它）")
                lines.append(
                    f"  - p{item.get('page_number')}｜"
                    f"「{item.get('table_title') or '（无表题）'}」｜"
                    f"放行 `{(item.get('release_id') or '（未放行）')[:16]}`｜"
                    f"表 `{(item.get('table_id') or '（无）')[:16]}`｜"
                    f"材料 `{item.get('material_id') or '（无）'}`｜"
                    f"宿主读关系 `{item.get('host_read_relation')}`｜"
                    f"**{item.get('reason_label')}** `{item.get('reason')}`"
                    + (f"（`{item['reason_detail']}`）" if item.get("reason_detail") else "")
                    + (f"｜缺陷 `{'/'.join(item['defect_codes'])}`"
                       if item.get("defect_codes") else "")
                    + scope)
            if column.get("count") and not column.get("rows"):
                lines.append("  - （原因分布见上一行的 typed 计数；逐条留痕缺失即漏记）")
            if (column.get("reason_counts") or {}).get("not_released"):
                lines.append("  - **读法**：`not_released` 说的是「这张表没通过它**自己**的"
                             "逐表完整证明」（`gto-3`）。它**不**说明「本栏没有这张表」，"
                             "也**不**说明「来源里没有这张表」——后者要可核查检索才能下结论。"
                             "逐表拒发账**按整份文档**给出：拒绝记录不带宿主块身份，"
                             "「这张表归哪个节点」在当前 wire 上不可判定，本页**不猜**。")
            if (column.get("reason_counts") or {}).get("not_in_run_pack"):
                lines.append("  - **读法**：`not_in_run_pack` 说的是「本栏定位到、也已成材料，"
                             "但**本次运行现场**的 Pack 里没有它」——成因在派发域／预算／"
                             "是否走到 Pack，**不**等于「来源里没有这张表」。")
            if (column.get("reason_counts") or {}).get("pack_unavailable"):
                lines.append("  - **读法**：`pack_unavailable` 是**不可判定**，"
                             "**不得**读成「没有进 Pack」。")
        if row["table_landings"]:
            lines.append(f"- **span 层**：本栏定位/读集覆盖到的表体落地 "
                         f"{len(row['table_landings'])} 处，合计 "
                         f"{row['table_landing_chars']} 字符（全部为拒发）：")
            for land in row["table_landings"]:
                lines.append(f"  - p{land['page_number']}｜`{land['landing']}`｜"
                             f"{land['slice_chars']} 字符 / {land['component_count']} 个表内片段"
                             f"｜判词={land['verdicts']}｜admitted={land['admitted']}"
                             f"｜节点 `{land['node_id']}` {land['node_title']}")
        else:
            lines.append("- **span 层**：本栏定位/读集**没有**覆盖到任何表体落地")
        lines.append("- 还缺什么（缺口清单，不是判据）：")
        for item in row["still_missing"]:
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("## 表格只读诊断视图（未签发表格）")
    lines.append("")
    lines.append("> 本节的每一行都是**只读诊断**：原文 + 精确定位 + 拒发理由。"
                 "它**不是** Pack 材料、**不是**数字事实、**不是**可发布正文；"
                 "任何正文、表格或结论都不得引用本节内容。")
    lines.append("")
    rows = diagnostics
    lines.append(f"- 表内/表邻登记总数（限主营业务相关标题节点）：{len(rows)}")
    for key, count in sorted(payload["table_diagnostic_counts"].items()):
        lines.append(f"  - {key}：{count}")
    lines.append("")
    #: 只在正文里展开**含数字且长度可观**的表体片段，其余留在 `table_diagnostics.json`。
    substantive = sorted(
        (r for r in rows
         if r["slice_chars"] >= 200 and any(ch.isdigit() for ch in r["text"])),
        key=lambda r: -r["slice_chars"])
    lines.append(f"- 其中像表体（≥200 字符且含数字）的片段：{len(substantive)} 条，"
                 "按字符数从大到小展示前 20 条；全量见 `table_diagnostics.json`。")
    lines.append("")
    shown = 0
    for row in substantive:
        if shown >= 20:
            break
        shown += 1
        lines.append(f"### {row['document_id']} p{row['page_number']} "
                     f"（{row['landing']}，{row['slice_chars']} 字符，"
                     f"{row['component_count']} 个表内片段）")
        lines.append(f"- 定位：父块 `{row['evidence_block_id']}`｜块内紧坐标 "
                     f"{row['evidence_char_range']}（并集 {row['merged_range_count']} 段，"
                     f"段间空隙 {row['gap_chars_in_span']} 字符）")
        lines.append(f"- 所属标题节点：`{row['node_id']}` "
                     f"{(row['node_title'] or '')}")
        lines.append(f"- span 层落地：landing=`{row['landing']}` "
                     f"admitted=`{row['admitted']}` "
                     f"reasons=`{row['rejection_reason']['admission_reasons']}`"
                     f"｜span_id={row['span_ids']}")
        lines.append(f"- 拒发理由（{row['rejection_reason']['layer']}）："
                     f"verdict={row['rejection_reason']['verdicts']} "
                     f"refusal={row['rejection_reason']['refusal_reasons']}")
        lines.append(f"- 视图性质：not_pack_material="
                     f"{row['not_pack_material']}, not_numeric_fact="
                     f"{row['not_numeric_fact']}, not_publishable_prose="
                     f"{row['not_publishable_prose']}")
        lines.append("")
        lines.append("```text")
        lines.append(row["text_excerpt"])
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _verdict(cells: dict) -> str:
    """**轴一 · 材料到达**结论：材料充分／部分／未取得——判据是**读到的实质正文字符**，不是份数。

    名字里带"到达"是有意的：它只说材料到没到手，**不是**业务覆盖结论，也**不**回答
    "这一栏的业务要求有没有被实质支持"（那是轴二 `_support_for` 的问题）。
    """
    if not cells:
        return "未取得"
    best = max(STATE_RANK[c["state"]] for c in cells.values())
    if best >= STATE_RANK["complete_material"]:
        return "材料充分"
    if any(c.get("substantive_read_chars", 0) > 0 for c in cells.values()):
        return "部分"
    return "未取得"


def _clip(text: str, limit: int) -> str:
    body = text.replace("\n", " ").strip()
    return body if len(body) <= limit else body[:limit] + "…"


#: 强定位若**只**由这么少的几个不同申报字段词命中，读回页就显式把词列出来——
#: 一个词命中一整章，和"这一栏有多处独立位置"不是一回事。
FEW_TERMS_MAX = 2


def _strong_term_note(cell: dict) -> str | None:
    """本栏的强定位是否**只**靠少数几个申报字段词命中；是就把词摆出来。

    通用判据，不含任何公司/栏目特例：只看 `matched_terms` 的**去重集合大小**。
    一个「关联方」把整章关联方交易抬成强定位时，读者必须看得见这一点。
    """
    terms: set[str] = set()
    strong = 0
    for loc in cell.get("located") or ():
        if not loc.get("strong"):
            continue
        strong += 1
        terms |= set(loc.get("matched_terms") or ())
    if strong == 0 or not terms or len(terms) > FEW_TERMS_MAX:
        return None
    return (f"本栏 {strong} 处强定位**只**由 {len(terms)} 个申报字段词命中："
            + "、".join(f"「{t}」" for t in sorted(terms))
            + "——命中词对不等于材料对，须逐条核对上列路径。")


def _loc_marks(loc: dict) -> str:
    """一个定位位置的四件只读事实：读没读、在不在本章、**哪几个探针词命中**、名下多少字符没进准入正文。

    带上探针词是有意的：客户/供应商两栏的强定位全部由 Contract 申报字段里的
    「关联方」三个字命中「十三、关联方及关联交易」——那是**关联方交易**，不是
    客户集中度披露。把命中词摆出来，这种"命中词对、材料错"才看得见；
    真正的披露「（8）主要销售客户和主要供应商情况」此刻是**弱定位且未读**。
    """
    marks = []
    marks.append("已读" if loc.get("is_read") else "**未读**")
    if not loc.get("chapter_determined", True):
        marks.append("**章节未定**（本栏未导航到）")
    else:
        marks.append("本章" if loc.get("in_own_chapter") else "**别章**")
    if loc.get("matched_terms"):
        marks.append("命中词 " + "、".join(f"「{t}」" for t in loc["matched_terms"]))
    marks.append(f"{loc.get('material_chars', 0)} 字符未入正文")
    return " · ".join(marks)


def _column_unmet_render(cell: dict) -> dict:
    """把一个单元格的 typed 栏目未达原因渲染成**逐条**人读行（不合并成一句）。

    返回值有 `available` / `typed` / `lines` 三个键：

      * `available=False`：本产物没有这一键（旧版本产物，或本节不走 topic 研究相位）。
        此时 `lines` 里是**一句不可判定**，**不是**「（无）」——「（无）」会被读成
        「本栏目没有问题」，而真相是「本轮没读回这一层」；
      * `available=True` 且 `typed` 为空：运行时确实没有命中闭集里的任何一条。这是**结论**
        （runtime 自己就是这么写的），但仍与「不可判定」分开。
    """
    raw = cell.get("column_unmet")
    if isinstance(raw, dict) and {"lines", "typed", "available"} <= set(raw):
        # 已经渲染过的形状（`_cell_summary` / `_matrix` 的产物）：**幂等**返回。
        # 少了这一支，拿摘要当单元格再渲染一次就会把已渲染的 dict 当原始诊断读——
        # `typed_reasons` 取不到 ⇒ 每一栏都印成「运行侧未命中任何一条 typed 原因」，
        # 那是一句**凭空多出来的结论**，比不印更坏。
        return raw
    if not isinstance(raw, dict) or not raw:
        return {"available": False, "typed": [],
                "lines": [f"栏目未达原因：{UNMET_COLUMN_UNAVAILABLE}"]}
    if raw.get("available") is False:
        detail = str(raw.get("detail") or "") or "本节没有 typed 栏目未达原因可读"
        return {"available": False, "typed": [],
                "lines": [f"栏目未达原因：不可判定（{detail}）"]}
    typed = [str(r) for r in (raw.get("typed_reasons") or ())]
    lines = [f"栏目未达原因：{UNMET_COLUMN_LABELS.get(code, code)}（`{code}`）"
             for code in typed]
    if not lines:
        lines = ["栏目未达原因：运行侧未命中任何一条 typed 原因"
                 "（**不是**「材料一定够」：见本页各栏的覆盖/资格读数）"]
    closed = [str(c) for c in (raw.get("closed_set") or ())]
    missing = [c for c in closed if c not in typed]
    if missing:
        # 闭集里**没命中**的那些也写出来：读者才分得清「这一栏命中了两条」与「这一栏只判了
        # 两条、其余四类根本没参与判定」（后者是产物残缺，前者是结论）。
        lines.append("未命中的其余闭集原因（它们**参与过**判定，只是不成立）："
                     + "、".join(f"`{c}`" for c in missing))
    return {"available": True, "typed": typed, "lines": lines}


def _open_issues(cell: dict) -> str:
    issues = []
    if "located_not_read" in cell["states_observed"]:
        issues.append("**本章**定位到的原文未被读取")
    scope = cell.get("chapter_scope") or {}
    if scope.get("unread_out_of_chapter"):
        issues.append(f"别章另有 {len(scope['unread_out_of_chapter'])} 处强定位未读"
                      "（见上列，非本章缺口）")
    if "table_not_released" in cell["states_observed"]:
        issues.append("表体拒发（见只读诊断视图）")
    if "partial_body" in cell["states_observed"]:
        issues.append("正文片段不完整")
    if "title_or_form_only" in cell["states_observed"]:
        issues.append("只读到标题/勾选行")
    if "not_navigated" in cell["states_observed"]:
        issues.append("未导航到")
    # typed 栏目未达原因**逐条**接在状态之后：状态说「这一栏到哪一步」，原因说「为什么停在
    # 那一步」。少了这一半，「未导航到」会被读成「语料里没有」。
    issues.extend(_column_unmet_render(cell)["lines"])
    return "；".join(issues) if issues else "（无）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--out", default=None)
    parser.add_argument("--diagnostics", default=None,
                        help="run 的 failure_diagnostics.json；给了才判事实资格")
    parser.add_argument("--pack", default=None,
                        help=("run 的 material_pack.json；给了才列 Pack/Writer 去向。"
                              "不给时那一列如实写「不可得」，**不**写「没有」"))
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    out_dir = Path(args.out).resolve() if args.out else (
        repo / "evaluation" / "results" / "m930_3_business_readback")
    fact_states = load_fact_states(Path(args.diagnostics) if args.diagnostics else None)
    payload = run(repo, out_dir, fact_states=fact_states,
                  pack_path=Path(args.pack) if args.pack else None)
    print(f"{SCHEMA_VERSION} → {out_dir}")
    for row in payload["matrix"]:
        cells = {c["document_id"]: c for c in row["cells"]}
        rendered = " | ".join(
            f"{d[:9]}:{cells[d]['state_label']}" if d in cells else f"{d[:9]}:—"
            for d in DOCUMENT_ORDER)
        print(f"  {row['aspect_id']:<62} {rendered}")
    print(f"  表内/表邻诊断登记：{payload['table_diagnostic_total']} 条")
    _pack = payload["run_pack_tables"]
    print(f"  Pack 去向：{'读自 ' + str(_pack['path']) if _pack['available'] else '不可得'}"
          f"（表对象材料 {_pack['table_entry_count']} 份）")
    for document_id in DOCUMENT_ORDER:
        summary = (payload["documents"].get(document_id) or {}).get(
            "table_channel_summary") or {}
        if not summary:
            continue
        print(f"  {document_id} 表对象信道：对象 {summary['object_count']} / "
              f"材料 {summary['material_count']} / 已放行未成材料 "
              f"{summary['released_not_material_count']} / 未获放行 "
              f"{summary['unreleased_candidate_count']}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

"""§0.18 W8 单通道：图侧逐表证明的合格表 → 正式 Pack 材料（`gtm-1`）+ 独立重切解析器。

**为什么要有这一份，而不是继续用 `tom-1`。**

`tom-1`（`harness.table_object_materials`）的正文读视图是 `target_body_row_texts` 一类的
**压平整行文本**：它把一行表体读成**一个字符串**。对「这一段原文是不是这张表」它够用，
对**数字**它不够用——§0.19 要求业务表数字「逐项核对业务行、指标列、事件或期间、单位、
口径、原值与**格级来源**」，而一行字符串里没有「格」这个对象，也就没有格级来源。把压平的
整行文本交给写作侧，等于用**诊断平铺文本**冒充合格原始表。

因此本模块产出的读视图带**真正的逐格列表**（`:data:`CELLS_KEY`）：每格给出
`row_index` / `column_index` / `row_label` / `column_header` / `column_header_path` /
`value_text` / `value_text_raw` / `cell_state`。写作侧据此才可能既写出业务行与指标列、
又让每个数字落回**具体的格**。

**两个来源的差别只有一处，且是判定轴的差别**：`tom-1` 从 Evidence 块原文**当场重解**
表对象（`tobj-*` 的文本侧求解，文档级一票否决）；`gtm-1` 只接受 `gto-3` **逐表独立证明**
过的放行记录（`document_structure.table_local_proof` 的 `tlp-1` 判据）。判据只有一处：
本模块**不**重判一张表合不合格，它只在放行记录之上做「这份证明能不能变成一份正式材料」
的复核，缺一条即 typed 拒绝。

**三条身份/权威边界**（与 `tom-1` 同源、不得混用）。

1. **阅读材料 ≠ 数字权威。** 信封里逐字写着 `reading_material=True` /
   `numeric_authority=False` / `financial_authority_claimed=False`，且这组声明**进 payload
   哈希**（读的人删不掉、改不了）。格级来源只说明「这个数字出自这张表的这一格」，
   它**不**把该数字升格成一条 `Fact`，也不给任何权威身份。要升格只能走路径 A 预验证
   （触及冻结 Contract / SourcePolicy，须单独裁决），本模块不做、也不宣称做过。
2. **表身份 ≠ 材料身份。** 放行记录自带 `table_id`（内容 + 结构 + 溯源）与
   `release_id`（快照 + 表 + 证明 + 片段）；材料侧另有自己的 `material_id`
   （内容寻址，含 payload 哈希）。三者都在 payload 里逐字保留。
3. **「有表材料」≠「Contract 事实已取得」。** 表进 Pack 只说明这一栏**读得到这张表**；
   Contract 必需事实是否取得仍由事实链判定。:data:`GRAPH_TABLE_EXCLUSIONS` 逐项写明。

**独立重切**：:class:`GraphTablePayloadResolver` 从不采信返回体自报的字段，它在构造期从
**放行记录 + Evidence 块原文**重算 payload 字节并建索引；`resolve` 只按重算出来的哈希查表，
命中后逐项复核 authority_identity / created_dependency_fingerprint / locator。因此
「改写材料字段让 ref 指向旧哈希」只会得到 dangling（上层 fail-closed），不会静默通过。

**不在本模块里做的事**（写下来是为了让它们不可被顺手加进来）：

* **不构造 `loc-1` 定位对象**。格级 locator 的 wire 词表在
  `sections.narrative_schema`（`table_cell` 变体），harness 不得反向 import 写作侧，
  也不得自己抄一份词表——那正是「同一个事实有两个名字」。本模块只给出**中立的格坐标**
  （`cell_owner` / `table_ref` / `row_index` / `column_index`），由写作侧按自己的 wire 渲染。
* **不构造单位 / 期间 / 口径的「声明视图」**。它只被写作侧的 `sentence_check` 消费，
  因此由写作侧从本模块给出的**逐字**读数（`unit_text` / `header_rows` / `title_text`）
  渲染。harness 侧只负责把图侧真实读到的东西逐字带出来。
* **不重判表的资格**，也**不放行任何未通过逐表证明的表**：非放行记录一律 typed 拒绝。

纯转换模块：不 I/O、不建库、不写库、不调 LLM/网络，不持有 live capability。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from document_structure import table_local_proof as TLP
from harness import graph_table_release as GTR
from harness import topic_schema as TS
from harness import tree_materials as TM
from harness.topic_store import MaterialPayloadRecord

#: 信封种类（版本化；与 `tree-material-payload-v1` / `table-object-material-v1` 是不同信封，
#: 不得互认）。
GRAPH_TABLE_MATERIAL_ENVELOPE_KIND = "graph-table-material-v1"

#: 公共信封版本键的值：`topic_store._validate_envelope` 与
#: `sections.material_context._decode_envelope` 都严格比对**整数** 1。
MATERIAL_PAYLOAD_VERSION = 1

#: 解析语义版本（`payload_ref.version` / resolver 只认这一个值）。它与 `tom-1`、`tmr-2`
#: 是**不同解析语义**，因此版本必须自成一体：本 resolver 对别的版本返回 None（不越界代答），
#: 别的 resolver 对本版本返回 dangling（它没有这份 payload 行）。
GRAPH_TABLE_MATERIAL_VERSION = "gtm-1"

#: 材料类型（复用 `TS.MATERIAL_TYPES` 的既有成员，不新增类型）。
GRAPH_TABLE_MATERIAL_TYPE = "table_context"

#: 逐格列表在读视图里的键。写作侧的 `sentence_check` 按**同一**个键取格
#: （`sections.sentence_check.TABLE_CELLS_KEY`），两处不得各写一份字面量。
CELLS_KEY = "cells"

#: 一份图侧表材料未能成为 Pack 材料的**封闭**原因（typed 审计；每一条都对应一个可复算判据）。
#:
#: 这些**不是** Contract 缺口：缺口另有 Contract 依据与检索范围（`not_used` 不是 gap）。
#:
#: **不重复 `tlp-1` 的缺陷词表**：逐表证明为什么没过，由放行记录的 `local_proof.defects`
#: 逐条带出（`document_structure.table_local_proof` 是那件事的唯一判据）。本表只判
#: 「那份证明能不能变成一份材料」。
GRAPH_TABLE_MATERIAL_REASONS = (
    # 记录不是放行记录（逐表证明未过）：具体缺陷随记录带出，本层不另立判据。
    "table_local_proof_not_passed",
    # 记录没有声明自己是阅读材料（形态就不是「只读」）。
    "not_reading_material",
    # 记录声称了数字 / 财务权威：本通道不放行任何金额权威。
    "numeric_authority_claimed",
    # 声明的结构终态不是 `complete`（放行谓词要求它，走到这里即记录被改写过）。
    "structure_state_not_complete",
    # 放行记录身份与复算不符（记录被改写过，或片段键被换过）。
    "release_id_mismatch",
    # 没有任何数据行（只有表头）：一张没有数据行的表不构成可写材料。
    "empty_body",
    # 没有任何**可引用格**（每条 cell 来源都不可引用）：数字无从按格授权。
    "no_citable_cells",
    # 可引用格的来源块不在这份文档的块集合里（跨文档 / 凭空块 id / 块被删）。
    "source_block_unavailable",
    # 来源块的 company/document/version/evidence_set 与显式 current 绑定不一致
    # （跨版本拼接 = 把不同版本的文本读成一张表）。
    "source_binding_mismatch",
    # 来源块区间越出该块自身文本（声明与原文对不上）。
    "source_span_out_of_range",
    # 来源块在场，但权威**确定性重算**不为 authoritative（不采信自填 verdict）。
    "authority_not_authoritative",
)

#: 图侧表材料的**允许用途**（封闭词表，只有一条）。它只在「这一栏读得到这张表」这件事上
#: 说话：既不是数字权威，也不能凭表体里的数字替 Contract 事实作证。
#:
#: 直接**别名**放行侧的同一个字面量，不另抄一份：两个模块说的是同一条用途，抄第二遍就等于
#: 给同一件事准备了两个名字。
GRAPH_TABLE_PERMITTED_USE = GTR.GRAPH_TABLE_PERMITTED_USE

#: 为什么读视图里的 `period` 是空的：**不是**这张表没有期间，而是图侧根本没有任何一处在读
#: 表的期间 —— `TS5.TableObjectV4` 只有 `unit_text`（没有 period 字段），`tlp-1` 的表题读数
#: 也只读文本；仓内唯一与期间有关的原语 `SRS.PERIOD_QUALIFICATION_MARKERS` 是
#: `("报告期",)`，方向相反（判"有没有把断言限定到明确期间"）。**不**为此新写一个期间识别器：
#: 那需要枚举日期写法，正是门规禁止的答案关键词专用规则。
#:
#: 后果是**真**的、也是该有的：`sections.sentence_check` 的 `table_declaration` 轴要求
#: 「数字只能出自表」时该表必须同时声明单位 / 期间 / 口径；期间读不到 ⇒ 该轴硬失败 ⇒
#: **表格数字不得写入正文**。这与"格级数字权威需路径 A 预验证、本批不做"是一致的，
#: 不是遗漏。该缺口按系统能力缺陷如实上报（不是来源缺口）。
GRAPH_TABLE_PERIOD_ABSENCE_REASON = "graph_side_has_no_period_reading"

#: 图侧表材料的**排除**（封闭词表）：逐条写明「这一份读不出什么」，供读者与下游分流。
GRAPH_TABLE_EXCLUSIONS = (
    # 不得作金额 / 占比 / 比率的权威来源（要数字走财务快照与结构校核）。
    "no_numeric_authority",
    # 格值不得直接充当事实支撑（要支撑走预验证权威事实或合格 Claim）。
    "no_support_from_cell_values",
    # 需要计算时由 Python/Decimal 做结构校核，模型不得自算后写入正文。
    "no_computation_by_llm",
    # 表材料在场**不等于** Contract 必需事实已取得（两者是不同的账）。
    "not_a_substitute_for_contract_fact",
    # 逐表证明通过**不等于**文档级资格已取得（`gto-3` 里两者是两条轴）。
    "not_a_document_level_qualification",
)

#: `value_text` 的排版读法（封闭词表，只有一条）：把该格自己的原文里的**空白串**折叠成
#: 单个空格。理由是逐格数字核对要拿它去比正文 token，而正文里的「1,234.56 万元」与
#: 单元格里的两行「1,234.56」/「万元」必须在**同一**归一化下才可比。逐字原文不丢：
#: 同一格里另有 `value_text_raw`。
VALUE_TEXT_RENDERING = "whitespace_collapsed"

#: `content.text` 的行内 / 行间渲染（进结构化读视图，使「这是渲染而不是 PDF 自己的字节」
#: 在任何消费方那里读得到）。
CELL_SEPARATOR = "\t"
ROW_SEPARATOR = "\n"

#: 行标签的读法来源（封闭词表；逐格带出，读的人不必猜）。
ROW_LABEL_SOURCE_ROW = "row_label"
ROW_LABEL_SOURCE_LEAD_CELL = "lead_cell"


class GraphTableMaterialError(TS.SchemaValidationError):
    """图侧表材料构建 / 解析的 fail-closed 错误（越权调用、非法输入、身份漂移）。"""


# ---------------------------------------------------------------------------
# 1. 放行记录 → 读视图（逐字原文 + 逐格结构读法）
# ---------------------------------------------------------------------------

def _rows(record: Mapping) -> list[dict]:
    return [r for r in (record.get("rows") or ()) if isinstance(r, Mapping)]


def _cells_of(row: Mapping) -> list[dict]:
    return [c for c in (row.get("cells") or ()) if isinstance(c, Mapping)]


def _row_label_of(row: Mapping) -> tuple[str, str]:
    """业务行标签 + 它的读法来源：``(标签, 来源)``。

    第一顺位是 TS5 自己读出的 `row.label`（图侧对「这一行的标题是什么」的正式读数）。
    它为空时退回**该行首格**的文本——但只在首格确实处在第 0 列时才算（第 0 列才是表的
    引导列；把第 3 列的文本当行标签是编造）。两条都取不到就是空串：这一行的格**没有**
    业务行标签，它的数字因此不得按表授权（不是猜一个标签出来）。
    """
    label = str(row.get("label") or "").strip()
    if label:
        return label, ROW_LABEL_SOURCE_ROW
    for cell in _cells_of(row):
        if int(cell.get("column") or 0) == 0:
            text = str(cell.get("text") or "").strip()
            if text:
                return " ".join(text.split()), ROW_LABEL_SOURCE_LEAD_CELL
            break
    return "", ""


def _column_header_paths(header_rows: Sequence, column_count: int) -> dict[int, tuple[str, ...]]:
    """逐列的**表头层文本**（自上而下，去掉空格项）：`{列号: (层文本, ...)}`。

    表头行是放行记录里逐字给出的（`header_rows`，跨列 cell 在其覆盖的每列重复写出），
    本函数**只**做「同一列的各层文本按层序取出」这一件事：不重建逻辑表头、不猜期间、
    不合并同义层。「指标列」在多层表头下本来就是一条**路径**（层 1 说指标、层 2 说期间），
    只取末层会漏掉指标，只取首层会漏掉期间，因此路径整条带出，由核对侧逐层要求。
    """
    paths: dict[int, list[str]] = {c: [] for c in range(int(column_count))}
    for header in header_rows or ():
        if not isinstance(header, (list, tuple)):
            continue
        for column in range(int(column_count)):
            text = " ".join(str(header[column]).split()) if column < len(header) else ""
            if text:
                paths[column].append(text)
    return {c: tuple(texts) for c, texts in paths.items()}


def _cell_view(*, row: Mapping, cell: Mapping, column_count: int,
               header_paths: Mapping) -> dict:
    """一个放行记录的格 → 读视图里的**一格**（逐字原值 + 中立格坐标 + 图侧读数）。

    这里**不**产出 `loc-1` 定位对象：格级 locator 的 wire 词表在写作侧，本层只给坐标
    （`owner` / `table_ref` 在视图层给出，`row_index` / `column_index` 在这里给出）。
    """
    row_index = int(row.get("row_index") or 0)
    column_index = int(cell.get("column") or 0)
    row_label, label_source = _row_label_of(row)
    raw = str(cell.get("text") or "")
    path = tuple(str(x) for x in (header_paths.get(column_index) or ()))
    return {
        "row_index": row_index,
        "column_index": column_index,
        "row_role": str(row.get("role") or ""),
        "rowspan": int(cell.get("rowspan") or 1),
        "colspan": int(cell.get("colspan") or 1),
        "row_label": row_label,
        "row_label_source": label_source,
        "column_header": path[-1] if path else "",
        "column_header_path": list(path),
        "value_text": " ".join(raw.split()),
        "value_text_raw": raw,
        "value_text_rendering": VALUE_TEXT_RENDERING,
        "cell_state": str(cell.get("cell_state") or ""),
        "citable_source_count": int(cell.get("citable_source_count") or 0),
        "non_citable_source_count": int(cell.get("non_citable_source_count") or 0),
        "fragment_keys": [list(k) for k in (cell.get("fragment_keys") or ())],
    }


def graph_table_cells(record: Mapping) -> list[dict]:
    """放行记录 → 逐格读视图（**只取数据行**）。

    表头行的格**不**进这一列表，理由不是省事：表头格没有业务行标签，若把它也算成一格
    「可引用格」，一张表的表头文字里的数字（期间、单位、口径）就会自己授权自己。
    表头原样留在视图的 `header_rows` 里，那是列标签的来源，不是数字的来源。
    """
    column_count = int(record.get("column_count") or 0)
    header_paths = _column_header_paths(record.get("header_rows"), column_count)
    out: list[dict] = []
    for row in _rows(record):
        if str(row.get("role") or "") == "header":
            continue
        for cell in _cells_of(row):
            out.append(_cell_view(row=row, cell=cell, column_count=column_count,
                                  header_paths=header_paths))
    return out


def graph_table_text(record: Mapping) -> str:
    """表材料的**正文读视图**（逐字：表题 / 单位 / 表头行 / 数据行）。

    行内以 :data:`CELL_SEPARATOR`、行间以 :data:`ROW_SEPARATOR` 拼接。这两个分隔符是
    **渲染**而不是 PDF 自己的字节，因此逐字写在结构化读视图里（`rendering`）。格自己的
    原文一个字符都不改：`cell.text` 原样进这一行。
    """
    parts: list[str] = []
    title = str(record.get("title_text") or "").strip()
    if title:
        parts.append(title)
    unit = str(record.get("unit_text") or "").strip()
    if unit:
        parts.append(unit)
    for header in record.get("header_rows") or ():
        line = CELL_SEPARATOR.join(str(x) for x in header)
        if line.strip():
            parts.append(line)
    for row in _rows(record):
        if str(row.get("role") or "") == "header":
            continue
        line = CELL_SEPARATOR.join(str(c.get("text") or "") for c in _cells_of(row))
        if line.strip():
            parts.append(line)
    return ROW_SEPARATOR.join(parts)


def graph_table_reading_view(record: Mapping, *, host_evidence_id: str,
                             host_evidence_ids: Sequence[str]) -> dict:
    """结构化读视图（逐字来自放行记录，不重排）：表身份 + 逐字读数 + **逐格列表**。

    它随正文一起进 payload 哈希，并在信封里**显式**带上阅读材料与数字权威两条声明，
    使「这份材料是什么」在任何消费方那里读得到、且改不掉。

    `unit` 逐字取自放行记录的 `unit_text`（读不到就是空串 = **结论**，不是缺失）；
    `period` / `scope` **不在这里合成** —— 单位 / 期间 / 口径是写作侧的**声明视图**，
    由写作侧从本视图的逐字读数（`header_rows` / `title_text`）渲染，理由见模块头。
    """
    table_id = str(record.get("table_id") or "")
    cells = graph_table_cells(record)
    return {
        "envelope_kind": GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
        "material_version": GRAPH_TABLE_MATERIAL_VERSION,
        "release_rule_version": str(record.get("release_rule_version") or ""),
        "release_schema_version": str(record.get("release_schema_version") or ""),
        # 三套身份互不顶替：放行记录 / 逐表证明 / 表自身。
        "release_id": str(record.get("release_id") or ""),
        "local_proof_id": str(record.get("local_proof_id") or ""),
        "table_id": table_id,
        "table_locator": str(record.get("table_locator") or ""),
        "table_schema_version": str(record.get("table_schema_version") or ""),
        # 格级定位的**中立坐标**：归属载体 + 表引用。`owner` 就是材料的来源身份
        # （`evidence:<宿主块>`，与 `authority_source_identity` 同域），不是另起一个名字。
        "cell_owner": f"evidence:{host_evidence_id}",
        "table_ref": table_id,
        "page_number": int(record.get("page_number") or 0),
        "host_evidence_id": str(host_evidence_id),
        "host_evidence_ids": [str(x) for x in (host_evidence_ids or ())],
        "evidence_block_ids": [str(x) for x in (record.get("evidence_block_ids") or ())],
        "title_text": str(record.get("title_text") or ""),
        "unit_text": str(record.get("unit_text") or ""),
        "note_text": str(record.get("note_text") or ""),
        "owner": dict(record.get("owner") or {}),
        # 图侧自己声明的"我这一项读出不确定"：逐字带出，读的人不必从缺陷去猜。
        "missing_or_uncertain_fields":
            [str(x) for x in (record.get("missing_or_uncertain_fields") or ())],
        "header_absence_reason": record.get("header_absence_reason"),
        "structure_state_reason": record.get("structure_state_reason"),
        # 逐字读数：单位 / 范围（写作侧的声明视图会读这三个顶层键）。
        "unit": str(record.get("unit_text") or ""),
        "period": "",
        "scope": str(record.get("title_text") or ""),
        # 每个声明读数的**来路**（读不到就逐字写明是哪一样读不到）。有了这一栏，
        # 「声明是空的」与「声明是空串」不会混成一个：前者是缺口，后者是结论。
        "declaration_reading": {
            "unit": {"value": str(record.get("unit_text") or ""),
                     "source": "table.unit_text"},
            "period": {"value": "", "source": None,
                       "absence": GRAPH_TABLE_PERIOD_ABSENCE_REASON},
            "scope": {"value": str(record.get("title_text") or ""),
                      "source": "table.title_blocks"},
        },
        # 格级数字**没有**取得任何权威：这张表读得到、这一格读得到、这个数字出自这一格，
        # 三件事都不等于这个数字可以写进正文。要升格只能走路径 A 预验证（本批不做）。
        "cell_number_authority": "not_granted",
        "numeric_authority_reason": TLP.NUMERIC_AUTHORITY_REASON,
        "structure_kind": str(record.get("structure_kind") or ""),
        "structure_class": str(record.get("structure_class") or ""),
        "structure_state": str(record.get("structure_state") or ""),
        "column_count": int(record.get("column_count") or 0),
        "header_rows": [[str(x) for x in h] for h in (record.get("header_rows") or ())],
        "row_count": int(record.get("row_count") or 0),
        "body_row_count": int(record.get("body_row_count") or 0),
        "cell_count": int(record.get("cell_count") or 0),
        "fragment_keys": [list(k) for k in (record.get("fragment_keys") or ())],
        "unproven_fragment_keys": [list(k) for k in
                                   (record.get("unproven_fragment_keys") or ())],
        "unsupported_columns": list(record.get("unsupported_columns") or ()),
        "all_columns_supported": bool(record.get("all_columns_supported")),
        # 逐格列表（`CELLS_KEY` = 写作侧取格的同一个键）。
        CELLS_KEY: cells,
        "citable_cell_count": sum(1 for c in cells if c["cell_state"] == GTR.CELL_STATE_CITABLE),
        "unproven_cell_count": sum(1 for c in cells
                                   if c["cell_state"] == GTR.CELL_STATE_UNPROVEN_ONLY),
        "not_citable_cell_count": sum(1 for c in cells
                                      if c["cell_state"] == GTR.CELL_STATE_NOT_CITABLE),
        "no_source_cell_count": sum(1 for c in cells
                                    if c["cell_state"] == GTR.CELL_STATE_NO_SOURCE),
        "rendering": {"cell_separator": CELL_SEPARATOR, "row_separator": ROW_SEPARATOR,
                      "value_text_rendering": VALUE_TEXT_RENDERING},
        # 阅读材料 / 数字权威：两条**独立**声明（阅读材料可以是真，数字权威必须为假）。
        "reading_material": True,
        "numeric_authority": False,
        "financial_authority_claimed": False,
        "permitted_use": GRAPH_TABLE_PERMITTED_USE,
        "exclusions": list(GRAPH_TABLE_EXCLUSIONS),
    }


# ---------------------------------------------------------------------------
# 2. 放行记录 → 材料（构建期逐项复核，不采信自报）
# ---------------------------------------------------------------------------

def compute_graph_table_material_id(*, payload_hash: str, release_id: str,
                                    host_evidence_id: str, document_version: str,
                                    evidence_set_version: str) -> str:
    """材料身份规范形 → ``material_id``（不含 run_id / 时间戳 / call_id / 页码）。

    与 `tree_materials.compute_tree_material_id` 同族，但把**放行记录身份**（`release_id`
    = 快照 + 表 + 逐表证明 + 片段键）放进身份：同一个 `table_id` 在不同快照上放行是两份记录，
    同一份放行结果重算仍是同一份材料（内容 + 宿主块 + payload 哈希全部相同）。页码不进身份。
    """
    identity = [
        GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
        GRAPH_TABLE_MATERIAL_VERSION,
        GRAPH_TABLE_MATERIAL_TYPE,
        str(release_id),
        str(host_evidence_id),
        str(document_version),
        str(evidence_set_version),
        str(payload_hash),
    ]
    return "mat-gtb-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8")).hexdigest()[:32]


def graph_table_dependency_fingerprint(*, release_batch: Mapping,
                                       blocks: Sequence = ()) -> str:
    """材料**创建期**依赖指纹：确定性、内容寻址、不含 run_id / 时间戳 / 页码。

    与 `tree_materials.tree_material_dependency_fingerprint` 同族：它记录「这份表材料是在
    哪一组放行规则版本 + 哪一份 current Evidence 内容上算出来的」。同一份文档重跑得到同一
    指纹（⇒ 同一 payload 哈希 ⇒ 同一 material_id），因此可以安全参与 Pack 内容身份。

    **批次身份进指纹**：批次身份由 `gto-3` 绑定文档级终态与逐表成员，因此「文档已取得资格」
    与「文档级被拒但逐表独立证明通过」算出来的材料**不是**同一份 —— 这两种情形下的读数
    不可互换。页码与块序不进指纹：它们只是阅读坐标，换一次分页不该让材料身份变。
    """
    ordered = _ordered(blocks)
    payload = {
        "envelope_kind": GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
        "material_version": GRAPH_TABLE_MATERIAL_VERSION,
        "release_rule_version": str(release_batch.get("release_rule_version") or ""),
        "release_schema_version": str(release_batch.get("release_schema_version") or ""),
        "release_batch_id": str(release_batch.get("batch_id") or ""),
        "document_id": str(release_batch.get("document_id") or ""),
        "document_version": str(release_batch.get("document_version") or ""),
        "blocks": [
            {
                "evidence_block_id": str(getattr(b, "evidence_block_id", "") or ""),
                "content_hash": str(getattr(b, "content_hash", "") or ""),
            }
            for b in ordered
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _ordered(blocks: Sequence) -> list:
    return sorted(blocks, key=lambda b: (int(getattr(b, "page_number", 0) or 0),
                                         int(getattr(b, "block_index", 0) or 0)))


def _block_map(blocks: Sequence) -> dict:
    out: dict = {}
    for block in blocks or ():
        block_id = str(getattr(block, "evidence_block_id", "") or "")
        if block_id:
            out[block_id] = block
    return out


def _authority_for(block, *, binding) -> TS.EvidenceAuthorityAssessment:
    """权威由来源块的既有字段**确定性重算**（不采信自报，也不新造判据）。

    与 `tree_materials` / `topic_materials` / `table_object_materials` 里那三处的胶水同形：
    判据只有 `TS.recompute_authority_verdict` 一处，这里只负责把块的字段摆成它的输入。
    本模块**不**引用那三处的私有函数（它们分属另两条通道），胶水就地写一份，免得
    「哪条通道的胶水是正统」变成一个迟早会漂移的隐式约定。
    """
    placeholder = TS.EvidenceAuthorityAssessment(
        evidence_id=str(getattr(block, "evidence_block_id", "") or ""),
        document_id=str(getattr(block, "document_id", "") or ""),
        document_version=str(getattr(block, "document_version", "") or ""),
        company_id=str(getattr(block, "company_id", "") or ""),
        is_current_document=bool(binding.is_current),
        is_current_set=bool(binding.is_current),
        page=int(getattr(block, "page_number", 0) or 0),
        block_range=(int(getattr(block, "block_index", 0) or 0),
                     int(getattr(block, "block_index", 0) or 0)),
        fetched_inspected_nonempty=bool(getattr(block, "text", "")),
        content_hash=str(getattr(block, "content_hash", "") or ""),
        verdict="rejected",  # 占位；下面确定性重算
        reason="",
        validator_version=binding.validator_version,
    )
    verdict = TS.recompute_authority_verdict(placeholder)
    return TS.EvidenceAuthorityAssessment(
        evidence_id=placeholder.evidence_id,
        document_id=placeholder.document_id,
        document_version=placeholder.document_version,
        company_id=placeholder.company_id,
        is_current_document=placeholder.is_current_document,
        is_current_set=placeholder.is_current_set,
        page=placeholder.page,
        block_range=placeholder.block_range,
        fetched_inspected_nonempty=placeholder.fetched_inspected_nonempty,
        content_hash=placeholder.content_hash,
        verdict=verdict,
        reason="" if binding.is_current else "非 current document/set",
        validator_version=binding.validator_version,
    )


def citable_source_refs(record: Mapping) -> list[dict]:
    """放行记录里**可引用**的逐格来源（按行序 / 列序 / 来源序，确定性）。

    一条来源就是一个 `(格, 片段, Evidence 块区间)` 三元关系。它是本通道回查「这张表的
    这一格出自原文哪一段」的**唯一**依据：不看压平正文，也不看别的块。
    """
    out: list[dict] = []
    for row in _rows(record):
        for cell in _cells_of(row):
            for source in (cell.get("sources") or ()):
                if not isinstance(source, Mapping) or source.get("citable") is not True:
                    continue
                out.append({
                    "row_index": int(row.get("row_index") or 0),
                    "column_index": int(cell.get("column") or 0),
                    "evidence_block_id": str(source.get("evidence_block_id") or ""),
                    "evidence_char_range": [int(x) for x in
                                            (source.get("evidence_char_range") or (0, 0))],
                    "fragment_key": [int(x) for x in (source.get("fragment_key") or ())],
                    "verdict": str(source.get("verdict") or ""),
                })
    return out


def _verify_cell_sources(record: Mapping, *, block_map: Mapping,
                         binding) -> tuple[list[dict], str]:
    """逐条复核可引用来源：``(逐条声明, "")`` 或 ``([], 原因)``。

    四条判据，任一不成立即 fail-closed（**不**降格成「部分来源」继续放行）：

    1. 至少有一条可引用来源（一条都没有 ⇒ 这张表的数字无从按格授权）；
    2. 每条来源的块必须在这份文档的块集合里；
    3. 每个块的 company/document/version/evidence_set 必须与**显式 current 绑定**逐项一致
       —— 跨版本、跨 set 的拼接会把不同版本的文本读成一张表；
    4. ``0 <= lo <= hi <= 该块文本长度`` —— 声明与原文对不上就是身份漂移。
    """
    refs = citable_source_refs(record)
    if not refs:
        return [], "no_citable_cells"
    for ref in refs:
        block = block_map.get(ref["evidence_block_id"])
        if block is None:
            return [], "source_block_unavailable"
        for attr in ("company_id", "document_id", "document_version",
                     "evidence_set_version"):
            if str(getattr(block, attr, "") or "") != str(getattr(binding, attr, "") or ""):
                return [], "source_binding_mismatch"
        lo, hi = ref["evidence_char_range"][:2]
        length = len(str(getattr(block, "text", "") or ""))
        if not (0 <= int(lo) <= int(hi) <= length):
            return [], "source_span_out_of_range"
    return refs, ""


def _host_ids(refs: Sequence[Mapping]) -> tuple[str, ...]:
    """本表的宿主 Evidence 块（可引用来源所在块，按文档序去重）。

    「这张表属于哪一块 Evidence」不是任意挑一个：它属于**每一条**承载了它可引用片段的块。
    首元素另有用途（材料定位的锚点），按（页号, 块序）确定，因此可复现、不依赖集合迭代顺序。
    """
    seen: list[str] = []
    for ref in refs:
        block_id = str(ref.get("evidence_block_id") or "")
        if block_id and block_id not in seen:
            seen.append(block_id)
    return tuple(seen)


def _locator_for(record: Mapping, *, host_block, host_blocks: Sequence,
                 refs: Sequence[Mapping], heading_path: Sequence[str]) -> TS.EvidenceLocator:
    """材料的定位：锚在**首个**宿主块上，区间覆盖全部宿主块的块序。

    这是定位，不是来源真值：来源真值逐条在 `cell_sources` 里（每条的块 / 区间 / 片段键）。
    只看这一个 locator 会把跨块的表的正文误当成单块内容，因此信封里另有
    `source_content_hash_scope` 逐字写明哈希的作用域。
    """
    indexes = [int(getattr(b, "block_index", 0) or 0) for b in host_blocks] or [0]
    ends = [int(r["evidence_char_range"][1]) for r in refs] or [0]
    return TS.EvidenceLocator(
        document_id=str(getattr(host_block, "document_id", "") or ""),
        document_version=str(getattr(host_block, "document_version", "") or ""),
        # 导航溯源：来自标题树所在节点；表块常常没有正文 span，取不到就是空串（不编造）。
        section_path=" / ".join(str(x) for x in (heading_path or ())),
        page=int(getattr(host_block, "page_number", 0) or 0),
        table_title=str(record.get("title_text") or "").strip() or None,
        block_range=(min(indexes), max(indexes)),
        # 片段语义：content.text 是**这张表**的原文（不是整块 Evidence），offset 记它在
        # 归一块坐标下的精确结束界；宿主块的完整 content_hash 因此保持不变。
        offset=max(ends),
    )


def build_graph_table_material(record: Mapping, *, blocks: Sequence, binding,
                               heading_paths: Mapping | None = None,
                               dependency_fingerprint: str = ""
                               ) -> tuple[tuple | None, str]:
    """一条**放行记录** → ``((material, payload_record, host_ids), "")`` 或 ``(None, reason)``。

    逐项复核（顺序即优先级；任一不成立即落 typed 原因，不静默丢弃、不降格使用）：

    1. 记录确实是**放行**记录（`released is True`）——逐表证明没过的表一律不成为材料，
       具体缺陷由放行记录的 `local_proof.defects` 带出，本层不另立判据；
    2. 阅读材料三条声明（阅读为真、数字与财务权威均为假）；
    3. 结构终态为 `complete`（放行谓词已经要求它，走到这里还不成立即记录被改写过）；
    4. 放行记录身份**外部复算**一致（片段键被换过就拒绝）；
    5. 有数据行（只有表头的表不构成可写材料）；
    6. **逐条**复核可引用来源（`_verify_cell_sources`）：至少一条、块在场、版本/set 同绑定、
       区间不越出各自块；
    7. 每个宿主块的权威重算均为 ``authoritative``。
    """
    if not isinstance(record, Mapping):
        raise GraphTableMaterialError(
            f"图侧表材料只接受放行记录映射，得到 {type(record).__name__}")
    if record.get("released") is not True:
        return None, "table_local_proof_not_passed"
    qualification = record.get("content_qualification") or {}
    if not isinstance(qualification, Mapping) \
            or qualification.get("reading_material") is not True:
        return None, "not_reading_material"
    if qualification.get("numeric_authority") is not False \
            or qualification.get("financial_authority_claimed") is not False:
        return None, "numeric_authority_claimed"
    if str(record.get("structure_state") or "") != "complete":
        return None, "structure_state_not_complete"
    # 身份**外部复算**：不采信记录自报的 `release_id`。片段键被换过、快照被换过、
    # 逐表证明被换过，都在这里被拦下。
    try:
        recomputed_release_id = GTR.validate_release_id(record)
    except GTR.GraphTableReleaseError:
        return None, "release_id_mismatch"
    if recomputed_release_id != str(record.get("release_id") or ""):
        return None, "release_id_mismatch"
    if not GTR.record_is_reading_material(record):
        return None, "table_local_proof_not_passed"
    if int(record.get("body_row_count") or 0) < 1:
        return None, "empty_body"

    block_map = _block_map(blocks)
    refs, source_reason = _verify_cell_sources(record, block_map=block_map, binding=binding)
    if source_reason:
        return None, source_reason
    host_ids = _host_ids(refs)
    host_blocks = [block_map[host_id] for host_id in host_ids]
    # 锚点：按（页号, 块序）确定的首个宿主块。
    host_block = _ordered(host_blocks)[0]
    host_evidence_id = str(getattr(host_block, "evidence_block_id", "") or "")
    for block in host_blocks:
        if _authority_for(block, binding=binding).verdict != "authoritative":
            return None, "authority_not_authoritative"

    paths = dict(heading_paths or {})
    locator = _locator_for(record, host_block=host_block, host_blocks=host_blocks,
                           refs=refs, heading_path=tuple(paths.get(host_evidence_id, ())))
    source_identity = f"evidence:{host_evidence_id}"
    authority = _authority_for(host_block, binding=binding)
    envelope = {
        "material_payload_version": MATERIAL_PAYLOAD_VERSION,
        "envelope_kind": GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
        "object_type": GRAPH_TABLE_MATERIAL_TYPE,
        "authority_identity": source_identity,
        "document_identity": {
            "company_id": str(getattr(host_block, "company_id", "") or ""),
            "document_id": str(getattr(host_block, "document_id", "") or ""),
            "document_version": str(getattr(host_block, "document_version", "") or ""),
            "evidence_set_version": str(getattr(host_block, "evidence_set_version", "") or ""),
        },
        "locator": locator.to_dict(),
        "evidence_id": host_evidence_id,
        # `source_content_hash` 的**作用域就是锚点块**（`source_content_hash_scope` 逐字写明）。
        # 跨块表的正文不止这一段，它另由 `cell_sources` 逐条给出块 / 区间 / 片段键 ——
        # 只看这一个哈希会把跨块正文误当成单块内容。
        "source_content_hash": str(getattr(host_block, "content_hash", "") or ""),
        "source_content_hash_scope": "host_block_only",
        "cell_sources": [dict(r) for r in refs],
        "host_evidence_ids": [str(x) for x in host_ids],
        "content": {
            "text": graph_table_text(record),
            "structured_payload": graph_table_reading_view(
                record, host_evidence_id=host_evidence_id,
                host_evidence_ids=host_ids),
            "evidence_type": str(getattr(host_block, "evidence_type", "") or ""),
        },
        # 阅读材料 / 数字权威两条声明与排除项都在这里，**进 payload 哈希**。
        "reading_policy": {
            "reading_material": True,
            "numeric_authority": False,
            "financial_authority_claimed": False,
            "permitted_use": GRAPH_TABLE_PERMITTED_USE,
            "exclusions": list(GRAPH_TABLE_EXCLUSIONS),
        },
        "created_dependency_fingerprint": dependency_fingerprint,
        "graph_table": {
            "material_version": GRAPH_TABLE_MATERIAL_VERSION,
            "current_evidence": binding.to_dict(),
            "release_rule_version": str(record.get("release_rule_version") or ""),
            "release_schema_version": str(record.get("release_schema_version") or ""),
            "release_id": str(record.get("release_id") or ""),
            "local_proof_id": str(record.get("local_proof_id") or ""),
            "table_id": str(record.get("table_id") or ""),
            "table_locator": str(record.get("table_locator") or ""),
            "final_material_snapshot_id": str(record.get("final_material_snapshot_id") or ""),
            "upstream_dependency_fingerprint":
                str(record.get("upstream_dependency_fingerprint") or ""),
            "host_evidence_id": host_evidence_id,
            "host_evidence_ids": [str(x) for x in host_ids],
            "host_evidence": {
                "page_number": int(getattr(host_block, "page_number", 0) or 0),
                "block_index": int(getattr(host_block, "block_index", 0) or 0),
                "heading_path": [str(x) for x in (paths.get(host_evidence_id, ()) or ())],
            },
            # 复核过的**逐条**来源（含每条的块 / 区间 / 片段键）：材料侧回查的来源真值。
            "cell_sources": [dict(r) for r in refs],
        },
    }
    payload_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    payload_ref = TS.MaterialPayloadRef(
        object_type=GRAPH_TABLE_MATERIAL_TYPE,
        authority_identity=source_identity,
        version=GRAPH_TABLE_MATERIAL_VERSION,
        content_hash=payload_hash,
        locator=locator,
        created_dependency_fingerprint=dependency_fingerprint,
    )
    material = TS.ResearchMaterial(
        material_id=compute_graph_table_material_id(
            payload_hash=payload_hash,
            release_id=str(record.get("release_id") or ""),
            host_evidence_id=host_evidence_id,
            document_version=str(getattr(host_block, "document_version", "") or ""),
            evidence_set_version=str(getattr(host_block, "evidence_set_version", "") or "")),
        material_type=GRAPH_TABLE_MATERIAL_TYPE,
        source_identity=source_identity,
        locator=locator,
        payload_ref=payload_ref,
        content_hash=payload_hash,
        authority_assessment=authority,
    )
    payload_record = _record(payload_hash=payload_hash, source_identity=source_identity,
                             locator=locator, block=host_block, payload_bytes=payload_bytes,
                             dependency_fingerprint=dependency_fingerprint)
    return (material, payload_record, host_ids), ""


def _record(*, payload_hash: str, source_identity: str, locator, block, payload_bytes: bytes,
            dependency_fingerprint: str):
    """payload 记录（与 R2 `MaterialPayloadRecord` 同形；本通道**不落盘**，只作读回面）。"""
    return MaterialPayloadRecord(
        payload_id=payload_hash,
        object_type=GRAPH_TABLE_MATERIAL_TYPE,
        authority_identity=source_identity,
        version=GRAPH_TABLE_MATERIAL_VERSION,
        locator_json=json.dumps(locator.to_dict(), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")),
        source_content_hash=str(getattr(block, "content_hash", "") or ""),
        payload_hash=payload_hash,
        payload_bytes=payload_bytes,
        created_dependency_fingerprint=dependency_fingerprint,
    )


# ---------------------------------------------------------------------------
# 3. 一份文档的图侧表材料批次 + 独立重切解析器
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GraphTableMaterialBatch:
    """一份文档的**逐表放行结果**、它们产出的材料与逐条 typed 拒绝。

    `records`（放行记录）与 `materials` 不是一一对应：未获材料的放行记录只出现在
    `records` 与 `refusals` 里（`records` 是「图侧放行了什么」，`materials` 是「什么成为了
    Pack 材料」）；被 `gto-3` 拒发的表**只**出现在 `release_refusals` 里，连放行记录都没有。
    """

    records: tuple
    materials: tuple
    payload_records: tuple
    refusals: tuple
    release_refusals: tuple
    material_release_ids: tuple
    identity: dict
    #: ``release_id → 宿主 Evidence 块 id 元组``（放行记录与材料的宿主归属**同一份**事实，
    #: 不在读的时候二次反查——反查是漂移的入口）。
    release_hosts: Mapping = field(default_factory=dict)

    def material_for_release(self, record: Mapping):
        """该放行记录对应的材料（未成为材料 → ``None``）。"""
        rid = str(record.get("release_id") or "")
        for release_id, material in zip(self.material_release_ids, self.materials):
            if release_id == rid:
                return material
        return None

    def refusal_for_release(self, record: Mapping) -> str | None:
        rid = str(record.get("release_id") or "")
        for refusal in self.refusals:
            if str(refusal.get("release_id") or "") == rid:
                return str(refusal.get("reason") or "")
        return None

    def records_for_hosts(self, block_ids: Sequence[str]) -> tuple:
        """宿主块与本次已定位块**相交**的放行记录（按文档序，确定性）。

        一张表属于**每一条**承载了它可引用片段的块；因此「本次定位到了其中任意一条」就该把
        这张表算进人口。这里**不**挑一个主宿主来判（那会让同一张表因为 selector 落在哪一块
        而时进时不出）。
        """
        hosts = {str(b) for b in (block_ids or ())}
        return tuple(r for r in self.records
                     if hosts.intersection(self.release_hosts.get(
                         str(r.get("release_id") or ""), ())))

    def materials_for_hosts(self, block_ids: Sequence[str]) -> tuple:
        """本次已定位块里那些表的材料（与 :meth:`records_for_hosts` **同一**人口）。"""
        wanted = {str(r.get("release_id") or "") for r in self.records_for_hosts(block_ids)}
        return tuple(m for rid, m in zip(self.material_release_ids, self.materials)
                     if rid in wanted)

    def refusal_reason_counts(self) -> dict:
        counts: dict = {}
        for refusal in tuple(self.refusals) + tuple(self.release_refusals):
            reason = str(refusal.get("reason") or "")
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "envelope_kind": GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
            "material_version": GRAPH_TABLE_MATERIAL_VERSION,
            "released_record_count": len(self.records),
            "material_count": len(self.materials),
            "refusal_count": len(self.refusals),
            "release_refusal_count": len(self.release_refusals),
            "refusal_reason_counts": self.refusal_reason_counts(),
            "refusals": [dict(r) for r in self.refusals],
            "release_refusals": [dict(r) for r in self.release_refusals],
            "identity": dict(self.identity),
        }


def build_graph_table_material_batch(*, release_batch: Mapping, blocks: Sequence,
                                     binding, heading_paths: Mapping | None = None,
                                     dependency_fingerprint: str = ""
                                     ) -> GraphTableMaterialBatch:
    """`gto-3` 放行批次 + 本份文档的 Evidence 块 → 图侧表材料批次（纯函数，不写库）。

    **求解**只在下游（resolver 必须能重算每一个 payload）；**人口有界**由调用方（工具侧）
    负责——只把已定位块里的表交出去。两条 `gto-3` 的放行状态都收：

    * `accounted`：逐表结果在 `released` / `refusals` 里，逐条判「能不能材料化」；
    * `no_table_objects`（快照从未诞生）：**零**材料、零放行记录，文档级拒发读数逐字带出
      ——「没有对象可证明」不是「某张表不合格」。
    """
    if not isinstance(release_batch, Mapping):
        raise GraphTableMaterialError(
            f"图侧表材料批次只接受放行批次映射，得到 {type(release_batch).__name__}")
    rule_version = str(release_batch.get("release_rule_version") or "")
    if rule_version != GTR.GRAPH_TABLE_RELEASE_RULE_VERSION:
        raise GraphTableMaterialError(
            f"放行批次规则版本必须是 {GTR.GRAPH_TABLE_RELEASE_RULE_VERSION!r}，"
            f"得到 {rule_version!r}（fail-closed：不静默按新轴解释旧批次）")
    if not isinstance(binding, TM.CurrentEvidenceBinding):
        raise GraphTableMaterialError(
            f"build_graph_table_material_batch 需要 CurrentEvidenceBinding，得到 "
            f"{type(binding).__name__}")
    ordered = _ordered(blocks)
    dependency_fingerprint = str(dependency_fingerprint or "") or \
        graph_table_dependency_fingerprint(release_batch=release_batch, blocks=ordered)
    paths = dict(heading_paths or {})

    records: list[dict] = []
    materials: list = []
    payload_records: list = []
    refusals: list[dict] = []
    material_release_ids: list[str] = []
    release_hosts: dict[str, tuple] = {}
    for record in (release_batch.get("released") or ()):
        if not isinstance(record, Mapping):
            raise GraphTableMaterialError("放行批次里的放行成员不是 object（不接受自报字段）")
        record = dict(record)
        records.append(record)
        # 宿主归属先算出来：**不管**这条记录最终成不成为材料，人口判据读的都是同一份事实。
        refs = citable_source_refs(record)
        release_hosts[str(record.get("release_id") or "")] = _host_ids(refs)
        built, reason = build_graph_table_material(
            record, blocks=ordered, binding=binding, heading_paths=paths,
            dependency_fingerprint=dependency_fingerprint)
        if built is None:
            refusals.append({
                "kind": "graph_table_material_refusal",
                "table_id": str(record.get("table_id") or ""),
                "release_id": str(record.get("release_id") or ""),
                "local_proof_id": str(record.get("local_proof_id") or ""),
                "page_number": int(record.get("page_number") or 0),
                "title_text": str(record.get("title_text") or ""),
                "structure_state": str(record.get("structure_state") or ""),
                "reason": reason,
                "release_rule_version": str(record.get("release_rule_version") or ""),
            })
            continue
        material, payload_record, host_ids = built
        materials.append(material)
        payload_records.append(payload_record)
        material_release_ids.append(str(record.get("release_id") or ""))
        release_hosts[str(record.get("release_id") or "")] = tuple(host_ids)
    # `gto-3` 的**放行**拒绝（逐表证明未过）：逐条透传，读的人要能分清
    # 「图侧就没放行」与「放行了但成不了材料」——两者是不同的账。
    release_refusals = [dict(r) for r in (release_batch.get("refusals") or ())
                        if isinstance(r, Mapping)]
    identity = {
        "envelope_kind": GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
        "material_version": GRAPH_TABLE_MATERIAL_VERSION,
        "release_rule_version": rule_version,
        "release_schema_version": str(release_batch.get("release_schema_version") or ""),
        "release_batch_id": str(release_batch.get("batch_id") or ""),
        "release_batch_state": str(release_batch.get("batch_state") or ""),
        "document_id": str(release_batch.get("document_id") or ""),
        "document_version": str(release_batch.get("document_version") or ""),
        "document_qualified": bool(release_batch.get("document_qualified")),
        "block_count": len(ordered),
        "released_record_count": len(records),
        "material_count": len(materials),
        "refusal_count": len(refusals),
        "release_refusal_count": len(release_refusals),
        "dependency_fingerprint": dependency_fingerprint,
    }
    return GraphTableMaterialBatch(
        records=tuple(records), materials=tuple(materials),
        payload_records=tuple(payload_records), refusals=tuple(refusals),
        release_refusals=tuple(release_refusals),
        material_release_ids=tuple(material_release_ids), identity=identity,
        release_hosts=release_hosts)


class GraphTablePayloadResolver:
    """`harness.topic_schema.PayloadResolver` 的图侧表材料实现（独立重切，不采信自报字段）。

    - 非本版本 / 非本类型 → ``None``（不越界代答，也不与 `tom-1` / R2 的 `table_context` 抢答）；
    - 本版本但重算不出这个哈希 → ``None``（dangling；上层 fail-closed）；
    - 命中 → 逐项复核 authority_identity / created_dependency_fingerprint / locator，
      任一不符立即 ``GraphTableMaterialError``（不把损坏伪装成「未找到」）。
    """

    def __init__(self, batch: GraphTableMaterialBatch) -> None:
        if not isinstance(batch, GraphTableMaterialBatch):
            raise GraphTableMaterialError(
                "GraphTablePayloadResolver 只接受 GraphTableMaterialBatch")
        self._batch = batch
        self._by_hash: dict = {
            record.payload_hash: (material, record)
            for material, record in zip(batch.materials, batch.payload_records)}

    @property
    def batch(self) -> GraphTableMaterialBatch:
        return self._batch

    def resolve(self, payload_ref) -> TS.ResolvedPayload | None:
        if payload_ref.object_type != GRAPH_TABLE_MATERIAL_TYPE:
            return None
        if payload_ref.version != GRAPH_TABLE_MATERIAL_VERSION:
            return None
        entry = self._by_hash.get(payload_ref.content_hash)
        if entry is None:
            return None
        material, record = entry
        if record.authority_identity != payload_ref.authority_identity:
            raise GraphTableMaterialError(
                f"payload {record.payload_id} authority_identity 与 ref 不符")
        if record.created_dependency_fingerprint != payload_ref.created_dependency_fingerprint:
            raise GraphTableMaterialError(
                f"payload {record.payload_id} created_dependency_fingerprint 与 ref 不符")
        if material.locator.to_dict() != payload_ref.locator.to_dict():
            raise GraphTableMaterialError(
                f"payload {record.payload_id} locator 与 ref 不一致（重切结果为准）")
        return TS.ResolvedPayload(
            object_type=record.object_type,
            authority_identity=record.authority_identity,
            version=record.version,
            locator=material.locator,
            content_hash=record.payload_hash,
            payload_bytes=record.payload_bytes,
        )


__all__ = [
    "GRAPH_TABLE_MATERIAL_ENVELOPE_KIND",
    "MATERIAL_PAYLOAD_VERSION",
    "GRAPH_TABLE_MATERIAL_VERSION",
    "GRAPH_TABLE_MATERIAL_TYPE",
    "GRAPH_TABLE_MATERIAL_REASONS",
    "GRAPH_TABLE_PERMITTED_USE",
    "GRAPH_TABLE_PERIOD_ABSENCE_REASON",
    "GRAPH_TABLE_EXCLUSIONS",
    "CELLS_KEY",
    "VALUE_TEXT_RENDERING",
    "CELL_SEPARATOR",
    "ROW_SEPARATOR",
    "ROW_LABEL_SOURCE_ROW",
    "ROW_LABEL_SOURCE_LEAD_CELL",
    "GraphTableMaterialError",
    "graph_table_text",
    "graph_table_cells",
    "graph_table_reading_view",
    "graph_table_dependency_fingerprint",
    "compute_graph_table_material_id",
    "citable_source_refs",
    "build_graph_table_material",
    "GraphTableMaterialBatch",
    "build_graph_table_material_batch",
    "GraphTablePayloadResolver",
]

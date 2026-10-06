# -*- coding: utf-8 -*-
"""Eval: M930-3 §0.18 W8 —— `harness/graph_table_materials.py`（`gtm-1`）的聚焦正反例。

用法: python -X utf8 -m evals.test_m930_3_graph_table_materials

本模块补的是一条**新生产模块**（`gtm-1`：图侧逐表证明过的表 → 正式 Pack 材料）的
直接回归。此前它只在 `evals.test_m930_3_tree_table_branch` 里被**工具面**间接走到；
模块自身的判据（逐格读视图、声明读数、身份构成、依赖指纹、十条 typed 拒绝、独立重切
解析器）在登记之前没有任何一条是被单独钉住的。

覆盖（分组；期望值一律用**本模块自带字面量**，不复用生产常量当期望）：

- **V1 版本与封闭词表**：信封种类 / 载荷版本 / 解析语义版本 / 材料类型 / 逐格键 /
  取值渲染 / 期间缺席原因，以及材料门**自己的**十条拒绝码与五条排除项。
- **V2 读视图**：逐字表题 / 单位 / 表头行 / 数据行；**逐格**列表（表头格不进列表、
  行标签只认第 0 列、指标列给整条路径、逐字原值与渲染值分开）；声明读数逐项带**来路**，
  期间读不到时是 typed 缺席而**不是**猜一个期间；格级数字**没有**权威。
- **V3 身份**：材料身份内容寻址（前缀、确定性、`release_id` 参与、页码**不**参与）。
- **V4 依赖指纹**：确定性；批次身份参与（文档级终态不同 ⇒ 不同材料）；块序与页码
  **不**参与；块内容参与。
- **V5 批次**：非本轴放行批次 / 非 `CurrentEvidenceBinding` 一律 fail-closed；快照从未
  诞生 ⇒ 零记录零材料；`records` 与 `materials` **不是**一一对应；宿主归属对每条记录
  都算（含未材料化的那些）。
- **V6 逐条 typed 拒绝**：九条可由夹具构造的原因逐条命中，且**不**互相顶替
  （先命中的那条就是原因）。
- **V7 独立重切解析器**：只认本版本、只按重算哈希命中；改写材料字段 → dangling 或
  typed error，**不**静默通过。

夹具的真伪边界（必须先说清）
--------------------------------------------------------------------------
**表对象与放行记录是真的**：复用 `evals/test_graph_table_release` 的接缝
（`_FixtureSource` + 生产 `TS.TableObjectV4.create()`），因此 `gto-3` 的放行判据、
`tc-3` 的构造期不变量都真的跑过。Evidence 块是**合成**的（复用
`evals/test_m930_3_tree_table_branch._blocks_for`），只保证"每格声明的区间都真落在
它自己那段块文本内"，**不**主张任何真实文档内容。

只读：不写任何文件、不建库、不联网、不调 LLM。
"""

from __future__ import annotations

import copy
import inspect
import json
import sys
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_graph_table_release as FIX           # noqa: E402
from evals import test_m930_3_tree_table_branch as BR       # noqa: E402
from harness import graph_table_materials as GTM            # noqa: E402
from harness import graph_table_release as GTR              # noqa: E402
from harness import topic_schema as TS                      # noqa: E402
from harness import tree_materials as TM                    # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 0. 本模块自带真值表（**不复用生产常量作为期望**）
# ---------------------------------------------------------------------------

_ENVELOPE_KIND = "graph-table-material-v1"
_MATERIAL_PAYLOAD_VERSION = 1
_MATERIAL_AXIS = "gtm-1"
_MATERIAL_TYPE = "table_context"
_CELLS_KEY = "cells"
_VALUE_TEXT_RENDERING = "whitespace_collapsed"
_PERIOD_ABSENCE = "graph_side_has_no_period_reading"
_PERMITTED_USE = "navigable_reading_material_only"
_CELL_SEPARATOR = "\t"
_ROW_SEPARATOR = "\n"

#: 材料门**自己的**封闭拒绝码（与 `gto-3` 的放行理由码是**两本账**，不得互认）。
_MATERIAL_REASONS = (
    "table_local_proof_not_passed",
    "not_reading_material",
    "numeric_authority_claimed",
    "structure_state_not_complete",
    "release_id_mismatch",
    "empty_body",
    "no_citable_cells",
    "source_block_unavailable",
    "source_binding_mismatch",
    "source_span_out_of_range",
    "authority_not_authoritative",
)
_RELEASE_REASONS = ("released", "table_local_proof_not_passed")
_ROW_LABEL_SOURCES = ("row_label", "lead_cell")
_EXCLUSIONS = (
    "no_numeric_authority",
    "no_support_from_cell_values",
    "no_computation_by_llm",
    "not_a_substitute_for_contract_fact",
    "not_a_document_level_qualification",
)
#: 读视图里**不得**出现的权威字段名（**只看键名**，子串匹配，大小写不敏感）：材料只说
#: "能读"。为什么不连值一起看：这张表的表头本来就可能写「本期金额」——那是来源自己的
#: 文字，把它一并禁掉等于禁止材料如实带出原文。
_FORBIDDEN_AUTHORITY_VALUE_FIELDS = (
    "amount", "金额", "currency", "numeric_value", "number_value", "figure",
    "authority_amount", "money",
)

_GRID = (("项目", "本期金额"), ("营业收入", "100"), ("营业成本", "80"))
_GRID_B = (("项目", "占比"), ("甲类", "10.0%"))
_UNIT = "单位：万元"
_TITLE = "（1）营业收入构成"


def _all_keys(obj) -> set[str]:
    """一个嵌套读视图里的**全部键名**（只看键，不看值）。"""
    out: set[str] = set()
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            out.add(str(key))
            out |= _all_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            out |= _all_keys(item)
    return out


#: ``release_id → 它的 `gto-3` 放行批次``：材料化要走**生产**批次构建（依赖指纹由生产
#: 自己算），而依赖指纹的输入之一是批次身份——夹具不能自己编一个 64 位 hex 冒充它。
_BATCHES: dict = {}


def _released(table, *, source=None):
    """一张合格表 → 它的 `gto-3` **放行记录**（走生产放行判据，不自造记录）。"""
    src = source if source is not None else FIX._source([table])
    batch = GTR.release_graph_tables(src)
    assert batch["batch_state"] == "accounted" and len(batch["released"]) == 1, batch
    record = batch["released"][0]
    _BATCHES[str(record["release_id"])] = batch
    return record


def _binding(**overrides):
    fields = {"company_id": BR.COMPANY, "document_id": FIX._DOC_ID,
              "document_version": FIX._DOC_VERSION,
              "evidence_set_version": FIX._EVIDENCE_SET_VERSION, "is_current": True}
    fields.update(overrides)
    return TM.CurrentEvidenceBinding(**fields)


def _batch_for(record, blocks, *, released=None, binding=None):
    """该放行记录所属的**生产**材料批次（依赖指纹 / 身份全由生产算，夹具不代算）。"""
    source_batch = _BATCHES[str(record.get("release_id") or "")]
    if released is not None:
        source_batch = {**source_batch, "released": list(released)}
    return GTM.build_graph_table_material_batch(
        release_batch=source_batch, blocks=blocks, binding=binding or _binding())


def _fixture():
    """最小正例现场：一张带逐字单位的两行表 + 它自己的 Evidence 块 + current 绑定。"""
    table = FIX._table(grid=_GRID, page=30, unit_text=_UNIT)
    record = _released(table)
    blocks = BR._blocks_for([table])
    return table, record, blocks


def _built(record, blocks, *, binding=None, fingerprint=""):
    return GTM.build_graph_table_material(
        record, blocks=blocks, binding=binding or _binding(),
        dependency_fingerprint=fingerprint or "")


def _structured(batch):
    """正例材料的**结构化读视图**（信封里 `content.structured_payload`）。

    走**生产**批次构建的结果，不是在夹具里再拼一遍：这样"读视图里有什么"与
    "Pack 里收到什么"是同一份字节。
    """
    assert batch.materials, "正例应当产出材料"
    envelope = json.loads(batch.payload_records[0].payload_bytes.decode("utf-8"))
    return envelope, envelope["content"]["structured_payload"]


def _check_versions(check, details) -> None:
    """V1 版本与封闭词表。"""
    check(GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND == _ENVELOPE_KIND,
          f"信封种类必须恰为 {_ENVELOPE_KIND}，得到 "
          f"{GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND}")
    check(GTM.MATERIAL_PAYLOAD_VERSION == _MATERIAL_PAYLOAD_VERSION,
          "公共信封版本必须是**整数** 1（`topic_store._validate_envelope` 严格比对）")
    check(GTM.GRAPH_TABLE_MATERIAL_VERSION == _MATERIAL_AXIS,
          f"解析语义版本必须恰为 {_MATERIAL_AXIS}，得到 "
          f"{GTM.GRAPH_TABLE_MATERIAL_VERSION}")
    check(GTM.GRAPH_TABLE_MATERIAL_TYPE == _MATERIAL_TYPE,
          f"材料类型必须复用既有成员 {_MATERIAL_TYPE}")
    check(_MATERIAL_TYPE in TS.MATERIAL_TYPES,
          "材料类型必须是 `MATERIAL_TYPES` 的既有成员（不新增类型）")
    check(GTM.CELLS_KEY == _CELLS_KEY,
          f"逐格键必须恰为 {_CELLS_KEY} —— 写作侧 `sentence_check` 按同一个键取格")
    check(GTM.VALUE_TEXT_RENDERING == _VALUE_TEXT_RENDERING,
          "取值渲染词表只有一条（空白折叠），且逐格带出，读的人不必猜")
    check(GTM.GRAPH_TABLE_PERIOD_ABSENCE_REASON == _PERIOD_ABSENCE,
          "期间读不到的**缺席原因**必须是 typed 串，不是空串——空串会被读成「没有期间」")
    check(GTM.GRAPH_TABLE_PERMITTED_USE == GTR.GRAPH_TABLE_PERMITTED_USE
          == _PERMITTED_USE,
          "允许用途必须与放行侧**是同一个**字面量（抄第二遍就是同一件事两个名字）")
    check(tuple(GTM.GRAPH_TABLE_MATERIAL_REASONS) == _MATERIAL_REASONS,
          "材料门的拒绝码必须恰为本模块自带的十一条，得到 "
          f"{tuple(GTM.GRAPH_TABLE_MATERIAL_REASONS)}")
    check(len(set(GTM.GRAPH_TABLE_MATERIAL_REASONS))
          == len(GTM.GRAPH_TABLE_MATERIAL_REASONS),
          "拒绝码闭集不得有重复（重复码会让两条判据在读回时不可分）")
    check(set(GTM.GRAPH_TABLE_MATERIAL_REASONS) & set(_RELEASE_REASONS)
          == {"table_local_proof_not_passed"},
          "两本账只允许**一个**同名码相交（并已显式登记）；其余必须各说各的")
    check(not [r for r in GTM.GRAPH_TABLE_MATERIAL_REASONS
               if r in _RELEASE_REASONS and r != "table_local_proof_not_passed"],
          "材料门**不得**产出放行门的理由码（`released` 不是「成不了材料」的原因）")
    check(tuple(GTM.GRAPH_TABLE_EXCLUSIONS) == _EXCLUSIONS,
          "排除项必须恰为本模块自带的五条（逐条写明这一份读不出什么）")
    check(set(GTM.CELL_SEPARATOR + GTM.ROW_SEPARATOR) == {_CELL_SEPARATOR, _ROW_SEPARATOR},
          "行内 / 行间渲染分隔符必须是逐字声明的两个字符，且渲染源进读视图")
    check(tuple(GTR.CELL_STATES) == FIX._CELL_STATES,
          "逐格状态词表必须与放行侧同一份事实（读视图的逐格状态直接取自它，"
          "各写一份必然漂移）")
    check(set(_FORBIDDEN_AUTHORITY_VALUE_FIELDS) ==
          set(FIX._FORBIDDEN_VALUE_FIELD_HINTS),
          "「不得出现的权威字段名」清单必须与放行侧同一份（两处各写一份就会一边松一边紧）")


def _check_reading_view(check, details) -> None:
    """V2 读视图：逐字读数 + 逐格结构 + 声明来路 + 数字无权。"""
    table, record, blocks = _fixture()
    envelope, view = _structured(_batch_for(record, blocks))

    check(view["title_text"] == "",
          "表题逐字带出；夹具的表没有表题块 ⇒ 空串是**结论**（图侧读到的是空），"
          "不是这个字段缺失")
    check(view["unit"] == _UNIT and view["unit_text"] == _UNIT,
          f"单位逐字取自图侧读数（实得 {view['unit']!r}）")
    check(view["header_rows"] == [["项目", "本期金额"]],
          f"表头行逐字带出（实得 {view['header_rows']}）")
    check(view["cell_owner"].startswith("evidence:")
          and view["cell_owner"].endswith(view["host_evidence_id"]),
          "格级中立坐标的归属载体就是来源身份（不另起一个名字）")
    check(view["table_ref"] == view["table_id"],
          "格级坐标的表引用就是表身份（不另起一个名字）")
    check(view["rendering"] == {"cell_separator": _CELL_SEPARATOR,
                                "row_separator": _ROW_SEPARATOR,
                                "value_text_rendering": _VALUE_TEXT_RENDERING},
          "渲染分隔符与取值渲染逐字写在读视图里（「这是渲染」读得到）")

    cells = view[_CELLS_KEY]
    check(isinstance(cells, list) and cells and all(isinstance(c, dict) for c in cells),
          "读视图给的是**逐格列表**（不是把一行压成一个字符串）")
    check(all(str(c["row_role"]) != "header" for c in cells),
          "表头格**不进**逐格列表：否则表头里的数字会自己授权自己")
    check(len(cells) == 2 * 2,
          f"两个数据行 × 两列 = 四格（实得 {len(cells)}）")
    labels = {c["row_label"] for c in cells}
    check(labels == {"营业收入", "营业成本"},
          f"业务行标签逐格带出（实得 {labels}）")
    check({c["row_label_source"] for c in cells} <= set(_ROW_LABEL_SOURCES),
          "行标签的**读法来源**逐格带出（读的人不必猜标签是怎么来的）")
    path = {c["column_index"]: tuple(c["column_header_path"]) for c in cells}
    check(path.get(0) == ("项目",) and path.get(1) == ("本期金额",),
          f"指标列给的是**整条表头路径**（实得 {path}）——多层表头下只取末层会漏指标")
    check(all(c["column_header"] == (c["column_header_path"] or [""])[-1] for c in cells),
          "单值 `column_header` 恰是路径末层（两个字段不得互相矛盾）")
    check(all(c["cell_state"] in GTR.CELL_STATES for c in cells),
          "逐格状态落在闭集内")
    check(sum(c["citable_source_count"] for c in cells) > 0,
          "正例至少有一格带可引用来源（否则它不该成为材料）")
    check(view["citable_cell_count"] + view["unproven_cell_count"]
          + view["not_citable_cell_count"] + view["no_source_cell_count"] == len(cells),
          "四档逐格计数恰好划分全部格（既不重复也不遗漏）")

    text = envelope["content"]["text"]
    lines = text.split(_ROW_SEPARATOR)
    check(lines[0] == _UNIT and lines[1] == _CELL_SEPARATOR.join(("项目", "本期金额")),
          f"正文读视图按声明的渲染逐字拼出单位与表头（实得 {lines[:2]}）")
    check(lines[2] == _CELL_SEPARATOR.join(("营业收入", "100")),
          "数据行逐字进正文，格自己的原文一个字符都不改")
    check([str(c["value_text_raw"]) for c in cells if c["column_index"] == 1]
          == ["100", "80"],
          "逐字原值在逐格列表里另有一份（渲染值不覆盖原文）")

    #: 声明读数：每一项都带**来路**；读不到是 typed 缺席，不是猜一个值。
    decl = view["declaration_reading"]
    check(decl["unit"]["value"] == _UNIT and decl["unit"]["source"] == "table.unit_text",
          "单位声明的来路逐字写出（读的人知道它是从哪读来的）")
    check(decl["period"]["value"] == "" and decl["period"]["source"] is None
          and decl["period"]["absence"] == _PERIOD_ABSENCE,
          "期间读不到时是 **typed 缺席**（带原因），不是猜一个期间出来")
    check(view["period"] == "",
          "顶层 `period` 与声明读数一致（两处不得一说缺席、一说有值）")
    check(decl["scope"]["value"] == view["scope"],
          "口径声明的取值与顶层 `scope` 一致")

    check(view["cell_number_authority"] == "not_granted",
          "格级数字**没有**取得任何权威（能读 ≠ 能以它为准）")
    check(view["reading_material"] is True and view["numeric_authority"] is False
          and view["financial_authority_claimed"] is False,
          "两条**正交**声明：是阅读材料为真、数字与财务权威必须为假")
    check(tuple(view["exclusions"]) == _EXCLUSIONS,
          "排除项逐条进读视图（读的人不必去别处找）")
    check(view["material_version"] == _MATERIAL_AXIS
          and view["envelope_kind"] == _ENVELOPE_KIND,
          "读视图自带版本与信封种类（材料自证它是哪一版）")

    keys = _all_keys(view)
    hits = sorted(k for k in keys
                  if any(hint in k.lower() for hint in _FORBIDDEN_AUTHORITY_VALUE_FIELDS))
    check(not hits,
          f"读视图的**键名**里不得出现金额/币种一类的权威字段名（实得 {hits}）——"
          "值里的「本期金额」是来源自己的表头文字，不属于本检查")

    #: 行标签只认**第 0 列**：别的列的文本不得被当成本行的业务标签（走公开读视图）。
    def _labels(record: dict) -> list[tuple[str, str]]:
        return sorted((c["row_label"], c["row_label_source"])
                      for c in GTM.graph_table_cells(record))

    off_column = {"column_count": 3, "header_rows": [["项目", "本期金额", "占比"]],
                  "rows": [{"row_index": 1, "role": "body", "label": "  ",
                            "cells": [{"column": 3, "text": " 100 ",
                                       "cell_state": "citable"}]}]}
    check(_labels(off_column) == [("", "")],
          "第 0 列以外的文本**不得**被当成行标签（那是编造一个标签出来）")
    lead_only = {"column_count": 1, "header_rows": [["项目"]],
                 "rows": [{"row_index": 1, "role": "body", "label": "  ",
                           "cells": [{"column": 0, "text": " 营业收入 ",
                                      "cell_state": "citable"}]}]}
    check(_labels(lead_only) == [("营业收入", "lead_cell")],
          "第 0 列的文本可以退回作行标签，且**来源**如实标 `lead_cell`")
    read_label = {"column_count": 1, "header_rows": [["项目"]],
                  "rows": [{"row_index": 1, "role": "body", "label": "本期发生额",
                            "cells": [{"column": 0, "text": "别的",
                                       "cell_state": "citable"}]}]}
    check(_labels(read_label) == [("本期发生额", "row_label")],
          "图侧自己读出的行标签优先于首格文本（不拿首格顶替正式读数）")


def _check_identity(check, details) -> None:
    """V3 材料身份：内容寻址、`release_id` 参与、页码不参与。"""
    table, record, blocks = _fixture()
    batch = _batch_for(record, blocks)
    check(len(batch.materials) == 1 and not batch.refusals,
          "正例应当成为材料（生产批次构建）")
    material, payload_record = batch.materials[0], batch.payload_records[0]
    check(material.material_id.startswith("mat-gtb-"),
          f"材料身份前缀（实得 {material.material_id[:12]!r}）")
    check(material.material_id != record["release_id"]
          and material.material_id != record["table_id"],
          "材料身份与放行身份 / 表身份**互不顶替**")
    check(material.payload_ref.version == _MATERIAL_AXIS
          and material.payload_ref.content_hash == material.content_hash,
          "payload ref 的版本与内容哈希与材料一致（ref 指向的就是这一份字节）")

    again = _batch_for(record, copy.deepcopy(blocks))
    check(len(again.materials) == 1
          and again.materials[0].material_id == material.material_id
          and again.payload_records[0].payload_bytes == payload_record.payload_bytes,
          "同一份放行记录 + 同一份块 ⇒ 同一份材料身份与同一份字节（确定性）")

    #: 同一份 payload，只有 `release_id` 不同 ⇒ 材料身份必须不同。
    other_id = GTM.compute_graph_table_material_id(
        payload_hash=material.content_hash, release_id="gtr-someone-else",
        host_evidence_id=material.source_identity.split(":", 1)[1],
        document_version=FIX._DOC_VERSION,
        evidence_set_version=FIX._EVIDENCE_SET_VERSION)
    check(other_id != material.material_id,
          "`release_id` 参与材料身份：同一张表在另一份放行记录上是**另一份**材料")
    check(GTM.compute_graph_table_material_id(
        payload_hash=material.content_hash + "x", release_id=record["release_id"],
        host_evidence_id=material.source_identity.split(":", 1)[1],
        document_version=FIX._DOC_VERSION,
        evidence_set_version=FIX._EVIDENCE_SET_VERSION) != material.material_id,
        "payload 哈希参与材料身份（内容变了身份必变）")

    params = set(inspect.signature(GTM.compute_graph_table_material_id).parameters)
    check(params == {"payload_hash", "release_id", "host_evidence_id",
                     "document_version", "evidence_set_version"},
          f"材料身份的**全部**输入就是这五项（实得 {sorted(params)}）——"
          "页码 / 时间戳 / run_id / call_id 都**不得**进身份")


def _check_fingerprint(check, details) -> None:
    """V4 依赖指纹：批次身份参与；块序与页码**不**参与。"""
    table, record, blocks = _fixture()
    batch = GTR.release_graph_tables(FIX._source([table]))
    fp = GTM.graph_table_dependency_fingerprint(release_batch=batch, blocks=blocks)
    check(fp == GTM.graph_table_dependency_fingerprint(release_batch=dict(batch),
                                                       blocks=list(reversed(blocks))),
          "块**顺序**不进指纹（指纹按（页号, 块序）排序后算，换一次列出顺序不该变身份）")
    moved = [type(b)(**{**b.__dict__, "page_number": b.page_number + 100})
             for b in blocks]
    check(GTM.graph_table_dependency_fingerprint(release_batch=batch, blocks=moved) == fp,
          "**页码**不进指纹（页码只是阅读坐标，换一次分页不该让材料身份变）")
    check(GTM.graph_table_dependency_fingerprint(
        release_batch={**batch, "batch_id": "gtr-batch-someone-else"}, blocks=blocks)
        != fp,
        "**批次身份**进指纹：文档级终态 + 逐表成员不同 ⇒ 材料不是同一份")
    changed = [type(b)(**{**b.__dict__, "text": b.text + "x"}) for b in blocks]
    check(GTM.graph_table_dependency_fingerprint(release_batch=batch, blocks=changed)
          != fp,
          "**块内容**进指纹（内容变了，依赖就不是同一组）")


def _check_batch(check, details) -> None:
    """V5 批次：入口 fail-closed、两条终态、记录与材料不一一对应、宿主归属齐备。"""
    table, record, blocks = _fixture()
    batch = GTR.release_graph_tables(FIX._source([table]))
    binding = _binding()

    built = GTM.build_graph_table_material_batch(
        release_batch=batch, blocks=blocks, binding=binding)
    check(built.identity["material_version"] == _MATERIAL_AXIS
          and built.identity["release_rule_version"] == GTR.GRAPH_TABLE_RELEASE_RULE_VERSION,
          "批次身份同时绑定材料版本与**放行**规则版本（两条轴都读得到）")
    check(built.identity["release_batch_id"] == str(batch["batch_id"]),
          "批次身份绑定放行批次身份（否则两次不同签发会算成同一份材料）")
    check(built.identity["document_qualified"] == bool(batch["document_qualified"]),
          "批次身份逐字带出**文档级**终态（与逐表结果分开）")
    check(len(built.records) == 1 and len(built.materials) == 1
          and not built.refusals and not built.release_refusals,
          "正例批次：一条放行记录、一份材料、零拒绝")

    material = built.material_for_release(built.records[0])
    check(material is not None and material.material_id == built.materials[0].material_id,
          "`material_for_release` 按放行身份取到材料（配对只按身份，不按页号/表题）")
    check(built.material_for_release({"release_id": "gtr-nope"}) is None
          and built.refusal_for_release({"release_id": "gtr-nope"}) is None,
          "未知放行身份**不得**代答（返回 None，不猜一张表出来）")
    check(built.refusal_for_release(built.records[0]) is None,
          "已成材料的记录没有材料化拒绝原因（两本账不得同时命中）")

    hosts = built.release_hosts[str(built.records[0]["release_id"])]
    check(bool(hosts) and all(h in {b.evidence_block_id for b in blocks} for h in hosts),
          f"宿主归属逐条来自块集合（实得 {hosts}）")
    check(built.records_for_hosts(list(hosts)) == tuple(built.records),
          "按宿主块取记录：相交即入人口（一张表属于**每一条**承载它可引用片段的块）")
    check(built.records_for_hosts(["gtr-blk-999-999"]) == ()
          and built.records_for_hosts([]) == (),
          "无关块 / 空块集 ⇒ 空人口（不按整篇文档兜底）")
    check(built.materials_for_hosts(list(hosts))
          == tuple(built.materials) == built.materials_for_hosts(["gtr-blk-999-999"] * 0
                                                                 + list(hosts)),
          "材料人口与记录人口**同源**（两处各算一套迟早漂移）")

    #: 快照从未诞生 ⇒ 零记录零材料，但**不是**「某张表不合格」。
    unbuilt = FIX._unbuilt_source()
    empty = GTM.build_graph_table_material_batch(
        release_batch=GTR.release_graph_tables(unbuilt), blocks=blocks, binding=binding)
    check(not empty.records and not empty.materials and not empty.refusals,
          "快照从未诞生 ⇒ 零放行记录、零材料、**零材料化拒绝**"
          "（「没有对象可证明」不是「某张表不合格」）")
    check(empty.identity["release_batch_state"] == "no_table_objects"
          and empty.identity["document_qualified"] is False,
          "批次身份逐字带出 `no_table_objects` 与文档级终态")
    check(empty.to_dict()["released_record_count"] == 0
          and empty.to_dict()["envelope_kind"] == _ENVELOPE_KIND,
          "批次读回面自证信封种类与零记录")

    #: 入口 fail-closed：非本轴放行批次 / 非 current 绑定一律拒（不静默按新轴解释旧批次）。
    for bad, msg in (
            ({**batch, "release_rule_version": "gto-2"}, "被取代的放行规则轴"),
            ({**batch, "release_rule_version": None}, "空的放行规则轴"),
            (None, "非映射的放行批次"),
    ):
        try:
            GTM.build_graph_table_material_batch(release_batch=bad, blocks=blocks,
                                                 binding=binding)
        except GTM.GraphTableMaterialError:
            check(True, f"入口 fail-closed：{msg} ⇒ 抛 GraphTableMaterialError")
        except Exception as exc:  # noqa: BLE001
            check(False, f"入口 fail-closed：{msg} 抛了 {type(exc).__name__}：{exc}")
        else:
            check(False, f"入口 fail-closed：{msg} **未**被拒")
    try:
        GTM.build_graph_table_material_batch(release_batch=batch, blocks=blocks,
                                             binding={"is_current": True})
    except GTM.GraphTableMaterialError:
        check(True, "入口 fail-closed：非 `CurrentEvidenceBinding` ⇒ 抛错（不按鸭子类型放行）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"入口 fail-closed：非绑定对象抛了 {type(exc).__name__}：{exc}")
    else:
        check(False, "入口 fail-closed：非 `CurrentEvidenceBinding` **未**被拒")


def _check_typed_refusals(check, details) -> None:
    """V6 逐条 typed 拒绝：每条各自命中，且**不**互相顶替。"""
    table, record, blocks = _fixture()
    binding = _binding()

    def refused(mutate, blocks_override=None, binding_override=None):
        candidate = copy.deepcopy(record)
        mutate(candidate)
        _built_result, reason = GTM.build_graph_table_material(
            candidate, blocks=(blocks if blocks_override is None else blocks_override),
            binding=binding_override or binding)
        return _built_result, reason

    cases = (
        ("table_local_proof_not_passed",
         lambda r: r.__setitem__("released", False), None, None,
         "记录不是放行记录（逐表证明未过）"),
        ("not_reading_material",
         lambda r: r["content_qualification"].__setitem__("reading_material", False),
         None, None, "记录没声明自己是阅读材料"),
        ("numeric_authority_claimed",
         lambda r: r["content_qualification"].__setitem__("numeric_authority", True),
         None, None, "记录声称了数字权威"),
        ("structure_state_not_complete",
         lambda r: r.__setitem__("structure_state", "partial"), None, None,
         "结构终态不是 complete（放行谓词要求它，走到这里即被改写）"),
        ("release_id_mismatch",
         lambda r: r.__setitem__("table_id", r["table_id"] + "x"), None, None,
         "表身份被换过 ⇒ 放行身份外部复算不符"),
        ("empty_body",
         lambda r: r.__setitem__("body_row_count", 0), None, None,
         "没有数据行（只有表头）"),
        ("no_citable_cells",
         lambda r: [c.__setitem__("sources", []) for row in r["rows"]
                    for c in row["cells"]], None, None,
         "一格可引用来源都没有（数字无从按格授权）"),
        ("source_block_unavailable",
         lambda r: None, [], None,
         "来源块不在这份文档的块集合里（跨文档 / 凭空块 id）"),
        ("source_binding_mismatch",
         lambda r: None, None, {"document_version": "sha256-deadbeef"},
         "来源块版本与显式 current 绑定不一致"),
    )
    for reason, mutate, blocks_override, binding_override, msg in cases:
        binding_for_case = (_binding(**binding_override) if binding_override
                            else None)
        got, got_reason = refused(mutate, blocks_override, binding_for_case)
        check(got is None and got_reason == reason,
              f"逐表材料拒绝：{msg} ⇒ `{reason}`（实得 {got_reason!r}）")

    #: 区间越出块文本（声明与原文对不上）——**不是**上面任何一条。
    table2 = FIX._table(grid=_GRID_B, page=31)
    record2 = _released(table2)
    long_blocks = BR._blocks_for([table2])
    truncated = [type(b)(**{**b.__dict__, "text": b.text[:1]}) for b in long_blocks]
    got, got_reason = GTM.build_graph_table_material(record2, blocks=truncated,
                                                     binding=binding)
    check(got is None and got_reason == "source_span_out_of_range",
          f"来源区间越出块自身文本 ⇒ `source_span_out_of_range`（实得 {got_reason!r}）")

    #: 每个宿主块的权威重算必须都是 authoritative；非 current 绑定 ⇒ 逐块权威判否。
    got, got_reason = GTM.build_graph_table_material(
        record, blocks=blocks, binding=_binding(is_current=False))
    check(got is None and got_reason == "authority_not_authoritative",
          f"来源块在场但权威重算不为 authoritative ⇒ `authority_not_authoritative`"
          f"（实得 {got_reason!r}）")

    #: 未材料化的记录**必须**出现在批次拒绝里，且带放行身份（否则读回分不清两条账）。
    changed = copy.deepcopy(record)
    changed["body_row_count"] = 0
    batch = GTR.release_graph_tables(FIX._source([table]))
    batch = {**batch, "released": [changed]}
    built = GTM.build_graph_table_material_batch(release_batch=batch, blocks=blocks,
                                                 binding=binding)
    check(not built.materials and len(built.records) == 1
          and len(built.refusals) == 1,
          "被材料门挡下时：放行记录**仍在**、材料为零、拒绝一条（三本账分开）")
    refusal = built.refusals[0]
    check(refusal["reason"] == "empty_body"
          and refusal["release_id"] == record["release_id"]
          and refusal["table_id"] == record["table_id"]
          and refusal["kind"] == "graph_table_material_refusal",
          "材料化拒绝逐条带放行身份与表身份（读回能指回是哪一条记录）")
    check(built.refusal_for_release(changed) == "empty_body"
          and built.material_for_release(changed) is None,
          "两个取用面在未材料化时一致：有拒绝原因、无材料")
    check(built.release_hosts[str(record["release_id"])],
          "**未材料化**的记录也算宿主归属（人口判据读的是同一份事实）")
    check(built.refusal_reason_counts() == {"empty_body": 1},
          f"拒绝计数逐码可见（实得 {built.refusal_reason_counts()}）")


def _check_resolver(check, details) -> None:
    """V7 独立重切解析器：只认本版本、只按**重算**哈希命中。"""
    table, record, blocks = _fixture()
    built = _batch_for(record, blocks)
    resolver = GTM.GraphTablePayloadResolver(built)
    material = built.materials[0]
    resolved = resolver.resolve(material.payload_ref)
    check(resolved is not None
          and resolved.payload_bytes == built.payload_records[0].payload_bytes
          and resolved.content_hash == material.content_hash
          and resolved.version == _MATERIAL_AXIS,
          "解析器按重算哈希命中，并交回**同一份字节**（工具返回与 runtime 提交同源）")
    check(resolver.resolve(TS.MaterialPayloadRef(
        object_type=_MATERIAL_TYPE, authority_identity=material.source_identity,
        version="tom-1", content_hash=material.content_hash,
        locator=material.locator,
        created_dependency_fingerprint=material.payload_ref.created_dependency_fingerprint,
    )) is None,
        "别的解析语义版本 ⇒ None（不越界代答：`tom-1` 不是这一版）")
    check(resolver.resolve(TS.MaterialPayloadRef(
        object_type=_MATERIAL_TYPE, authority_identity=material.source_identity,
        version=_MATERIAL_AXIS, content_hash="0" * 64, locator=material.locator,
        created_dependency_fingerprint=material.payload_ref.created_dependency_fingerprint,
    )) is None,
        "哈希重算不出这个 ref ⇒ None（dangling；上层 fail-closed）")
    tampered = TS.MaterialPayloadRef(
        object_type=_MATERIAL_TYPE, authority_identity="evidence:gtr-blk-9-9",
        version=_MATERIAL_AXIS, content_hash=material.content_hash,
        locator=material.locator,
        created_dependency_fingerprint=material.payload_ref.created_dependency_fingerprint,
    )
    try:
        got = resolver.resolve(tampered)
    except GTM.GraphTableMaterialError:
        check(True, "来源身份与材料不符 ⇒ typed error（不把损坏伪装成「未找到」）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"来源身份不符时抛了 {type(exc).__name__}：{exc}")
    else:
        check(got is None,
              "来源身份不符 ⇒ 不命中（要么 typed error、要么 None，不得静默交出字节）")


_GROUPS = (
    ("V1 版本与封闭词表", _check_versions),
    ("V2 读视图", _check_reading_view),
    ("V3 材料身份", _check_identity),
    ("V4 依赖指纹", _check_fingerprint),
    ("V5 批次", _check_batch),
    ("V6 逐条 typed 拒绝", _check_typed_refusals),
    ("V7 独立重切解析器", _check_resolver),
)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg) -> bool:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")
        return bool(cond)

    for name, fn in _GROUPS:
        try:
            fn(check, details)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL: 【{name}】整组异常 {type(exc).__name__}：{exc}")
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

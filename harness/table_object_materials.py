"""M930-3 第二批：合格表对象 → 正式 Pack 材料（`table_context`）+ 独立重切解析器。

**这份模块解决的是「只以表格存在的栏目拿不到任何材料」这个结构性缺口**：树材料人口只收
`role=="body"` 的正文 span，表内内容天然不进人口，因此 `营业收入构成` / `营业成本构成` /
`毛利率` 这类栏目在本通道上永远零材料。表对象（`harness.table_object_release`，`tobj-1`）
把「已定位块里已放行的表」交了出来，本模块负责把它们**变成 Pack 里可读的正式材料**。

三条身份/权威边界（不得混用，也不得靠约定俗成）：

1. **阅读材料 ≠ 数字权威。** 材料信封里逐字写着 ``reading_material=True`` /
   ``numeric_authority=False`` / ``financial_authority_claimed=False``，并且这组声明**进
   payload 哈希**——读的人删不掉、改不了。需要计算或引用的数字仍必须走
   `FinancialSnapshot` / `FinancialFactPack` 与 Python/Decimal 结构校核，**模型不得自算**。
2. **表对象身份 ≠ 正文材料身份。** 表对象自带 ``table_content_id``（内容寻址）与
   ``released_object_id``（内容 + 出现位置）；正文材料身份是 span 派生的。两者都在
   payload 里逐字保留，材料侧另有自己的 ``material_id``（内容寻址，含 payload 哈希）。
3. **「有表材料」≠「Contract 事实已取得」。** 表对象进 Pack 只说明这一栏**读得到表**；
   Contract 必需事实是否取得，仍由事实链（预验证权威事实 / 合格 Claim）判定。
   ``TABLE_OBJECT_EXCLUSIONS`` 把这一条逐项写明。

**独立重切**：:class:`TableObjectPayloadResolver` 从不采信返回体自报的字段，它在构造期从
**Evidence 块原文**重解一遍表对象、重算 payload 字节并建索引；``resolve`` 只按重算出来的
哈希查表，命中后逐项复核 authority_identity / dependency_fingerprint / locator。因此
「改写对象字段让 ref 指向旧哈希」只会得到 dangling（上层 fail-closed），不会静默通过。

**人口仍有界**：批次的**求解**覆盖本份文档的块（解析索引必须完整），但工具侧只把
**本次派发已定位的宿主块**的对象交出去（见 `harness.tree_tools.located_table_hosts` 与
`release_tables_for`）。人口口径**不是**"正文候选落在的块"，而是条 selector 语义下的定位结果：
选中的 Evidence 块、或选中节点在 `snapshot.components` 上的归属块。因此「块里有表、但没有
可成材料的正文」不再把它挡在 Pack 之外；而「与本次派发无关的块」依旧一个都不进。
「解析索引覆盖全篇」与「Pack 只收已定位块的表」是两件事，不得互相冒充。

纯转换模块：不 I/O、不建库、不写库、不调 LLM/网络，不持有 live capability。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from harness import table_object_release as TOR
from harness import topic_schema as TS
from harness import tree_materials as TM
from harness.topic_store import MaterialPayloadRecord

#: 信封种类（版本化；与 `tree-material-payload-v1` 是不同信封，不得互认）。
TABLE_OBJECT_MATERIAL_ENVELOPE_KIND = "table-object-material-v1"

#: 现有公共信封版本键的值：`topic_store._validate_envelope` 与
#: `sections.material_context._decode_envelope` 都严格比对**整数** 1。
MATERIAL_PAYLOAD_VERSION = 1

#: 解析语义版本（`payload_ref.version` / resolver 只认这一个值）。表对象材料与
#: `tmr-2` 的 span 材料、R2 的 `evidence_span`/`table_context` 是**不同解析语义**，
#: 因此版本必须自成一体：R2 resolver 对本版本返回 dangling（它没有这份 payload 行），
#: 本 resolver 对别的版本返回 None（不越界代答）。
TABLE_OBJECT_MATERIAL_VERSION = "tom-1"

#: 材料类型（复用 `TS.MATERIAL_TYPES` 的既有成员，不新增类型）。
TABLE_OBJECT_MATERIAL_TYPE = "table_context"

#: 表对象未能成为材料的**封闭**原因（typed 审计；每一条都对应一个可复算判据）。
#:
#: 这些**不是** Contract 缺口：缺口另有 Contract 依据与检索范围（`not_used` 不是 gap）。
TABLE_OBJECT_MATERIAL_REASONS = (
    # 对象没有声明自己是阅读材料（形态就不是「只读」）。
    "not_reading_material",
    # 对象声称了数字/财务权威：本通道不放行任何金额权威。
    "numeric_authority_claimed",
    # 表题形态退化（单位行 / 勾选残句 / 页码 / 期间碎片）：可读，但**不得**当具名表材料。
    "title_not_verifiable",
    # 没有任何表体行：一张没有数据行的表不构成可写材料。
    "empty_body",
    # 内容身份 / 出现位置身份与对象自己声明的字段对不上（对象被改写过）。
    "content_id_mismatch",
    "released_object_id_mismatch",
    # 宿主 Evidence 块不在本份文档的块集合里（无法回查来源）。
    "host_block_unavailable",
    # 宿主块的 company/document/version/set 与显式 current Evidence 绑定不一致。
    "host_binding_mismatch",
    # 权威确定性重算不为 authoritative（不采信自填 verdict）。
    "authority_not_authoritative",
    # 对象没有可用的**逐块**来源声明（`tobj-2` 之前的记录、或几何被改写过）：
    # 「不知道这段原文来自哪几块的哪些字符」的表不得成为材料。
    "source_span_unresolved",
    # 声明的某个来源块不在这份文档的块集合里（跨文档 / 凭空块 id / 块被删）。
    "source_block_unavailable",
    # 某个来源块的 company/document/version/evidence_set 与显式 current 绑定不一致
    # （跨版本拼接 = 把不同版本的文本读成一张表）。
    "source_binding_mismatch",
    # 某个来源块的区间**越出该块自身文本**（`tobj-1` 的真实缺陷形态）。
    "source_span_out_of_range",
)

#: 表对象材料的**允许用途**（封闭词表，只有一条）。它只在「这一栏读得到这张表」这件事上
#: 说话：它既不是数字权威，也不能凭表体里的数字替 Contract 事实作证。
TABLE_OBJECT_PERMITTED_USE = "navigable_reading_material_only"

#: 表对象的**排除**（封闭词表）：逐条写明「这一份读不出什么」，供读者与下游分流。
TABLE_OBJECT_EXCLUSIONS = (
    # 不得作金额 / 占比 / 比率的权威来源（要数字走财务快照与结构校核）。
    "no_numeric_authority",
    # 表内单元格值不得直接充当事实支撑（要支撑走预验证权威事实或合格 Claim）。
    "no_support_from_cell_values",
    # 需要计算时由 Python/Decimal 做结构校核，模型不得自算后写入正文。
    "no_computation_by_llm",
    # 表材料在场**不等于** Contract 必需事实已取得（两者是不同的账）。
    "not_a_substitute_for_contract_fact",
    # 不得因表题未获复核（退化表题）就把它当成具名表材料。
    "no_named_table_from_degraded_title",
)


class TableObjectMaterialError(TS.SchemaValidationError):
    """表对象材料构建/解析的 fail-closed 错误（越权调用、非法输入、身份漂移）。"""


# ---------------------------------------------------------------------------
# 1. 表对象 → 读视图（逐字原文 + 结构读法）
# ---------------------------------------------------------------------------

def table_object_text(obj: Mapping) -> str:
    """表对象的**逐字原文**（表题 / 单位 / 表头层 / 折行层 / 表体 / 合计行）。

    这是材料的正文读视图：不重排、不折算、不翻译、不补全。表内的非表体行（残句 / 表外
    说明 / 页脚页码）**不进正文**——它们是同一结构区里的其它行，另有自己的键留档，
    混进正文等于把表读成一片散文。
    """
    parts: list[str] = []
    title = str(obj.get("target_table_title") or "").strip()
    if title:
        parts.append(title)
    unit = str(obj.get("target_unit") or "").strip()
    if unit:
        parts.append(unit)
    parts.extend(str(line) for line in (obj.get("target_header_lines") or ()))
    parts.extend(str(line) for line in (obj.get("target_header_wrap_lines") or ()))
    parts.extend(str(row) for row in (obj.get("target_body_row_texts") or ()))
    closure = str(obj.get("target_closure_row") or "").strip()
    if closure:
        parts.append(closure)
    return "\n".join(parts)


def table_object_reading_view(obj: Mapping) -> dict:
    """结构读视图（逐字来自对象，不重排）：表题 / 单位 / 表头层 / 表体行 / 合计行。

    它随正文一起进 payload 哈希，并在信封里**显式**带上阅读材料与数字权威两条声明，
    使「这份材料是什么」在任何消费方那里都读得到、且改不掉。
    """
    return {
        "envelope_kind": TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
        "material_version": TABLE_OBJECT_MATERIAL_VERSION,
        "release_rule_version": str(obj.get("release_rule_version") or ""),
        "release_schema_version": str(obj.get("release_schema_version") or ""),
        # 两条正交的**表对象**身份（内容寻址 / 内容+出现位置）。
        "table_content_id": str(obj.get("content_id") or ""),
        "released_object_id": str(obj.get("released_object_id") or ""),
        "table_title": str(obj.get("target_table_title") or ""),
        "title_kind": str(obj.get("target_title_kind") or ""),
        "title_verifiable": bool(obj.get("target_title_verifiable")),
        "unit": str(obj.get("target_unit") or ""),
        "header_lines": [str(line) for line in (obj.get("target_header_lines") or ())],
        "header_wrap_lines": [str(line) for line in (obj.get("target_header_wrap_lines") or ())],
        "header_layer_count": int(obj.get("target_header_layer_count") or 0),
        "body_row_texts": [str(row) for row in (obj.get("target_body_row_texts") or ())],
        "body_row_count": int(obj.get("target_body_rows") or 0),
        "closure_row": str(obj.get("target_closure_row") or ""),
        "column_count": int(obj.get("target_column_count") or 0),
        "column_upper_bound": int(obj.get("target_column_upper_bound") or 0),
        "lead_column_labelled": bool(obj.get("lead_column_labelled")),
        "structure_state": str(obj.get("structure_state") or ""),
        "end_boundary": str(obj.get("target_end_boundary") or ""),
        "closed": bool(obj.get("target_closed")),
        # 跨块续读：表题/表头在本块、表体在紧邻的下一块（有界一块）。它是**读法**的一部分，
        # 因此进正文读视图：读者要能知道这张表是被续读拼起来的。
        "continuation_block_read": bool(obj.get("continuation_block_read")),
        # **来源真值**（`tobj-2`）：逐来源块的精确区间与各块长度。跨块对象的正文**不是**
        # 单块片段，这一条使「哪几块、每块哪一段」在任何消费方那里读得到、改不掉。
        "source_spans": [dict(s) for s in (obj.get("target_source_spans") or ())],
        "source_span_state": str(obj.get("target_source_span_state") or ""),
        # 同一结构区里的非表体行：逐字留档，**不构成**事实、不构成数字权威。
        "other_region_lines": [str(x) for x in (obj.get("target_other_region_lines") or ())],
        "residue_lines": [str(x) for x in (obj.get("target_residue_lines") or ())],
        # 阅读材料 / 数字权威：两条**独立**声明（阅读材料可以是真，数字权威必须为假）。
        "reading_material": True,
        "numeric_authority": False,
        "financial_authority_claimed": False,
        "permitted_use": TABLE_OBJECT_PERMITTED_USE,
        "exclusions": list(TABLE_OBJECT_EXCLUSIONS),
    }


# ---------------------------------------------------------------------------
# 2. 表对象 → 材料（构建期逐项复核，不采信自报）
# ---------------------------------------------------------------------------

def compute_table_object_material_id(*, payload_hash: str, released_object_id: str,
                                     host_evidence_id: str, document_version: str,
                                     evidence_set_version: str) -> str:
    """材料身份规范形 → ``material_id``（不含 run_id / 时间戳 / call_id / 页码）。

    与 `tree_materials.compute_tree_material_id` 同族，但把**表对象的出现位置身份**放进
    身份：同一张表在两处出现是两份记录（`released_object_id` 不同），同一处出现两次仍是
    一份材料（内容 + 宿主块 + payload 哈希全部相同）。页码不进身份。
    """
    identity = [
        TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
        TABLE_OBJECT_MATERIAL_VERSION,
        TABLE_OBJECT_MATERIAL_TYPE,
        str(released_object_id),
        str(host_evidence_id),
        str(document_version),
        str(evidence_set_version),
        str(payload_hash),
    ]
    return "mat-tob-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8")).hexdigest()[:32]


def table_object_dependency_fingerprint(*, binding, blocks: Sequence = ()) -> str:
    """材料**创建期**依赖指纹：确定性、内容寻址、不含 run_id / 时间戳 / 页码。

    与 `tree_materials.tree_material_dependency_fingerprint` 同族：它记录"这份表材料是在
    哪一组放行规则版本 + 哪一份 current Evidence 内容上算出来的"。同一份文档重跑得到同一
    指纹（⇒ 同一 payload 哈希 ⇒ 同一 material_id），因此可以安全参与 Pack 内容身份。

    **页码与块序不进指纹**：它们只是阅读坐标，换一次分页不该让材料身份变。
    """
    payload = {
        "envelope_kind": TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
        "material_version": TABLE_OBJECT_MATERIAL_VERSION,
        "release_rule_version": TOR.RELEASE_RULE_VERSION,
        "release_schema_version": TOR.RELEASE_SCHEMA_VERSION,
        "current_evidence": binding.to_dict(),
        "host_blocks": [
            {
                "evidence_block_id": str(getattr(b, "evidence_block_id", "") or ""),
                "content_hash": str(getattr(b, "content_hash", "") or ""),
            }
            for b in _ordered(blocks)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _block_map(*, block=None, blocks=None) -> dict:
    """``block`` / ``blocks`` → ``{块 id: 块}``（两种入参形态都收，便于单块调用点）。"""
    out: dict = {}
    if blocks is not None:
        items = (blocks.items() if isinstance(blocks, Mapping)
                 else ((str(getattr(b, "evidence_block_id", "") or ""), b) for b in blocks))
        for key, value in items:
            block_id = str(key or "") or str(getattr(value, "evidence_block_id", "") or "")
            if block_id:
                out[block_id] = value
    if block is not None:
        block_id = str(getattr(block, "evidence_block_id", "") or "")
        if block_id:
            out.setdefault(block_id, block)
    return out


def _verify_source_spans(obj: Mapping, *, block_map: Mapping,
                         binding) -> tuple[list[dict], str]:
    """逐来源块复核（`tobj-2` 的来源真实性门）：``(逐块声明, "")`` 或 ``([], 原因)``。

    五条逐块判据，任一不成立即 fail-closed（**不**降格成「部分来源」继续放行）：

    1. 对象必须带**非空**的 ``target_source_spans``，且宿主块在其中；
    2. **声明必须齐全**：对象自称跨块（``target_source_span_state == multi_block``）时至少
       两条区间；自称读过续块（``continuation_block_read``）时 ``continuation_into_block_id``
       必须在声明里。否则「漏掉一块」的残缺来源会被当成完整来源收下 —— 表体取自续块、
       却只声明宿主块，正是 `tobj-1` 那类来源失真换一种走法；
    3. 每个声明的块必须在这份文档的块集合里；
    4. 每个块的 company/document/version/evidence_set 必须与**显式 current 绑定**
       逐项一致 —— 跨版本、跨 set 的拼接会把不同版本的文本读成一张表；
    5. ``0 <= lo <= hi <= 该块文本长度`` —— 这一条正是 `tobj-1` 越界声明会被截下来的位置。

    返回的逐块声明**保留对象自己填的区间**（而不是重算出来的），复核的是「它说的对不对」，
    不是「它说的是不是我重算的」；重算一致性由内容身份与出现位置身份两道门另行保证。
    """
    spans = [s for s in (obj.get("target_source_spans") or ()) if isinstance(s, Mapping)]
    host_id = str((obj.get("source_locator") or {}).get("evidence_block_id") or "")
    if not spans or not any(str(s.get("evidence_block_id") or "") == host_id for s in spans):
        return [], "source_span_unresolved"
    declared = {str(s.get("evidence_block_id") or "") for s in spans}
    if str(obj.get("target_source_span_state") or "") == TOR.SOURCE_SPAN_STATE_MULTI \
            and len(spans) < 2:
        return [], "source_span_unresolved"
    if obj.get("continuation_block_read") is True:
        into = str(obj.get("continuation_into_block_id") or "")
        if not into or into not in declared:
            return [], "source_span_unresolved"
    out: list[dict] = []
    for span in spans:
        block_id = str(span.get("evidence_block_id") or "")
        block = block_map.get(block_id)
        if block is None:
            return [], "source_block_unavailable"
        for attr in ("company_id", "document_id", "document_version",
                     "evidence_set_version"):
            if str(getattr(block, attr, "") or "") != str(getattr(binding, attr, "") or ""):
                return [], "source_binding_mismatch"
        lo, hi = (span.get("char_range") or (0, 0))[:2]
        length = len(str(getattr(block, "text", "") or ""))
        if not (0 <= int(lo) <= int(hi) <= length):
            return [], "source_span_out_of_range"
        out.append({
            "evidence_block_id": block_id,
            "char_range": [int(lo), int(hi)],
            "block_text_length": length,
            "content_hash": str(getattr(block, "content_hash", "") or ""),
            "page_number": int(getattr(block, "page_number", 0) or 0),
            "block_index": int(getattr(block, "block_index", 0) or 0),
            "document_version": str(getattr(block, "document_version", "") or ""),
            "evidence_set_version": str(getattr(block, "evidence_set_version", "") or ""),
        })
    return out, ""


def _locator_for(obj: Mapping, block, heading_path: Sequence[str]) -> TS.EvidenceLocator:
    block_id = str(getattr(block, "evidence_block_id", "") or "")
    if str((obj.get("source_locator") or {}).get("evidence_block_id") or "") != block_id:
        raise TableObjectMaterialError(
            "表对象的 source_locator.evidence_block_id 与宿主块不一致（对象被改写过）")
    char_range = (obj.get("source_locator") or {}).get("char_range") or (0, 0)
    return TS.EvidenceLocator(
        document_id=str(getattr(block, "document_id", "") or ""),
        document_version=str(getattr(block, "document_version", "") or ""),
        # 导航溯源：来自标题树所在节点；表块常常没有正文 span，取不到就是空串（不编造）。
        section_path=" / ".join(str(x) for x in (heading_path or ())),
        page=int(getattr(block, "page_number", 0) or 0),
        # 表题的定位槽（只有复核过表题的对象才会走到这里，因此这里一定有真表题）。
        table_title=str(obj.get("target_table_title") or "") or None,
        block_range=(int(getattr(block, "block_index", 0) or 0),
                     int(getattr(block, "block_index", 0) or 0)),
        # 片段语义：content.text 是**块内片段**（这张表的原文），offset 记它在宿主块归一块
        # 坐标下的精确结束界；宿主块的完整 source_content_hash 因此保持不变。
        offset=int(char_range[1]),
    )


def _authority_for(block, *, binding) -> TS.EvidenceAuthorityAssessment:
    """权威由宿主块的既有字段**确定性重算**（不采信对象自报，也不新造判据）。"""
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


def build_table_object_material(obj: Mapping, *, block=None, blocks=None, binding,
                                heading_path=(),
                                dependency_fingerprint: str = ""
                                ) -> tuple[tuple | None, str]:
    """一个**已放行**表对象 → ``((material, payload_record), "")`` 或 ``(None, reason)``。

    ``block`` 是**宿主块**；``blocks`` 是这份文档的**全部**块（``{块 id: 块}`` 或块序列）。
    跨块对象的正文来自不止一块，因此来源门需要整份块集合才能复核；只给宿主块时，凡声明
    了多来源块的对象一律落 ``source_block_unavailable``（fail-closed，不按「只看到一块」
    就当它单块）。

    逐项复核（顺序即优先级；任一不成立即落 typed 原因，不静默丢弃、不降格使用）：

    1. 阅读材料声明（``reading_material=True`` / 数字与财务权威均为 False）；
    2. 表题可复核（退化表题不得当具名表材料）；
    3. 表体非空；
    4. 表对象自己的两条身份与重算一致（对象被改写过就拒绝）；
    5. 宿主块在场、且与显式 current Evidence 绑定逐项一致；
    6. **逐来源块**复核（``_verify_source_spans``）：声明齐全、块在场、版本/set 同绑定、
       区间不越出各自块 —— 跨块来源不得被读成单块；
    7. 每一块的权威重算均为 ``authoritative``。
    """
    block_map = _block_map(block=block, blocks=blocks)
    for key in ("reading_material", "numeric_authority", "financial_authority_claimed"):
        if key not in obj:
            return None, "not_reading_material"
    if obj.get("reading_material") is not True:
        return None, "not_reading_material"
    if obj.get("numeric_authority") is not False \
            or obj.get("financial_authority_claimed") is not False:
        return None, "numeric_authority_claimed"
    if obj.get("target_title_verifiable") is not True:
        return None, "title_not_verifiable"
    body_rows = [str(x) for x in (obj.get("target_body_row_texts") or ())]
    if not body_rows:
        return None, "empty_body"
    recomputed_content_id = TOR.table_content_id(
        title=str(obj.get("target_table_title") or ""),
        unit=str(obj.get("target_unit") or ""),
        header_lines=tuple(obj.get("target_header_lines") or ()),
        wrap_lines=tuple(obj.get("target_header_wrap_lines") or ()),
        body_row_texts=tuple(body_rows),
        closure_row=str(obj.get("target_closure_row") or ""))
    if recomputed_content_id != str(obj.get("content_id") or ""):
        return None, "content_id_mismatch"
    source_locator = dict(obj.get("source_locator") or {})
    if TOR.released_object_id(content_id=recomputed_content_id,
                              source_locator=source_locator) \
            != str(obj.get("released_object_id") or ""):
        return None, "released_object_id_mismatch"

    block_id = str(getattr(block, "evidence_block_id", "") or "")
    if not block_id or str(source_locator.get("evidence_block_id") or "") != block_id:
        return None, "host_block_unavailable"
    for attr in ("company_id", "document_id", "document_version", "evidence_set_version"):
        if str(getattr(block, attr, "") or "") != str(getattr(binding, attr, "") or ""):
            return None, "host_binding_mismatch"
    # **来源真实性门**（`tobj-2`）：逐来源块复核。跨块对象的正文来自不止一块，
    # 因此每一块都要在同一份文档、同一个 document_version / evidence_set 下、
    # 且区间落在该块自身文本之内 —— 否则「跨块文本被当成单块来源」会从这里漏过去。
    source_blocks, span_reason = _verify_source_spans(
        obj, block_map=block_map, binding=binding)
    if span_reason:
        return None, span_reason
    for declared in source_blocks:
        other = block_map.get(declared["evidence_block_id"])
        if _authority_for(other, binding=binding).verdict != "authoritative":
            return None, "authority_not_authoritative"
    authority = _authority_for(block, binding=binding)
    if authority.verdict != "authoritative":
        return None, "authority_not_authoritative"

    locator = _locator_for(obj, block, heading_path)
    source_identity = f"evidence:{block_id}"
    envelope = {
        "material_payload_version": MATERIAL_PAYLOAD_VERSION,
        "envelope_kind": TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
        "object_type": TABLE_OBJECT_MATERIAL_TYPE,
        "authority_identity": source_identity,
        "document_identity": {
            "company_id": block.company_id,
            "document_id": block.document_id,
            "document_version": block.document_version,
            "evidence_set_version": block.evidence_set_version,
        },
        "locator": locator.to_dict(),
        "evidence_id": block_id,
        # `source_content_hash` 的**作用域就是宿主块**（`source_content_hash_scope` 逐字写明）。
        # 跨块对象的正文不止这一段，它另由 `source_spans` 逐块给出 id / 区间 / 哈希 ——
        # 只看这一个哈希会把跨块正文误当成单块内容。
        "source_content_hash": str(getattr(block, "content_hash", "") or ""),
        "source_content_hash_scope": "host_block_only",
        "source_span_state": (TOR.SOURCE_SPAN_STATE_MULTI if len(source_blocks) > 1
                              else TOR.SOURCE_SPAN_STATE_SINGLE),
        "source_spans": [dict(s) for s in source_blocks],
        "content": {
            "text": table_object_text(obj),
            "structured_payload": table_object_reading_view(obj),
            "evidence_type": str(getattr(block, "evidence_type", "") or ""),
        },
        # 阅读材料 / 数字权威两条声明与排除项都在这里，**进 payload 哈希**。
        "reading_policy": {
            "reading_material": True,
            "numeric_authority": False,
            "financial_authority_claimed": False,
            "permitted_use": TABLE_OBJECT_PERMITTED_USE,
            "exclusions": list(TABLE_OBJECT_EXCLUSIONS),
        },
        "created_dependency_fingerprint": dependency_fingerprint,
        "table_object_material": {
            "material_version": TABLE_OBJECT_MATERIAL_VERSION,
            "current_evidence": binding.to_dict(),
            "host_evidence": {
                "evidence_block_id": block_id,
                "page_number": int(getattr(block, "page_number", 0) or 0),
                "block_index": int(getattr(block, "block_index", 0) or 0),
                "heading_path": [str(x) for x in (heading_path or ())],
            },
            "release_rule_version": str(obj.get("release_rule_version") or ""),
            "release_schema_version": str(obj.get("release_schema_version") or ""),
            "table_content_id": recomputed_content_id,
            "released_object_id": str(obj.get("released_object_id") or ""),
            "source_locator": source_locator,
            # 复核过的**逐块**来源（含每块 content_hash）：材料侧回查的来源真值。
            "source_spans": [dict(s) for s in source_blocks],
            "source_span_state": (TOR.SOURCE_SPAN_STATE_MULTI
                                  if len(source_blocks) > 1
                                  else TOR.SOURCE_SPAN_STATE_SINGLE),
        },
    }
    payload_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    payload_ref = TS.MaterialPayloadRef(
        object_type=TABLE_OBJECT_MATERIAL_TYPE,
        authority_identity=source_identity,
        version=TABLE_OBJECT_MATERIAL_VERSION,
        content_hash=payload_hash,
        locator=locator,
        created_dependency_fingerprint=dependency_fingerprint,
    )
    material = TS.ResearchMaterial(
        material_id=compute_table_object_material_id(
            payload_hash=payload_hash,
            released_object_id=str(obj.get("released_object_id") or ""),
            host_evidence_id=block_id,
            document_version=str(block.document_version),
            evidence_set_version=str(block.evidence_set_version)),
        material_type=TABLE_OBJECT_MATERIAL_TYPE,
        source_identity=source_identity,
        locator=locator,
        payload_ref=payload_ref,
        content_hash=payload_hash,
        authority_assessment=authority,
    )
    record = _record(
        payload_hash=payload_hash, source_identity=source_identity, locator=locator,
        block=block, payload_bytes=payload_bytes,
        dependency_fingerprint=dependency_fingerprint)
    return (material, record), ""


def _record(*, payload_hash: str, source_identity: str, locator, block, payload_bytes: bytes,
            dependency_fingerprint: str):
    """payload 记录（与 R2 `MaterialPayloadRecord` 同形；本通道**不落盘**，只作读回面）。"""
    return MaterialPayloadRecord(
        payload_id=payload_hash,
        object_type=TABLE_OBJECT_MATERIAL_TYPE,
        authority_identity=source_identity,
        version=TABLE_OBJECT_MATERIAL_VERSION,
        locator_json=json.dumps(locator.to_dict(), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")),
        source_content_hash=str(getattr(block, "content_hash", "") or ""),
        payload_hash=payload_hash,
        payload_bytes=payload_bytes,
        created_dependency_fingerprint=dependency_fingerprint,
    )


# ---------------------------------------------------------------------------
# 3. 一份文档的表对象批次 + 独立重切解析器
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableObjectMaterialBatch:
    """一份文档的**全部**已放行表对象、它们产出的材料与逐条 typed 拒绝。

    ``objects`` 与 ``materials`` 不是一一对应：未获资格的对象只出现在 ``objects`` 与
    ``refusals`` 里（`objects` 是「求解出了什么」，`materials` 是「什么成为了 Pack 材料」）。
    """

    objects: tuple
    materials: tuple
    payload_records: tuple
    refusals: tuple
    material_object_ids: tuple
    identity: dict
    #: ``released_object_id → 宿主 Evidence 块 id``（对象与材料的宿主归属**同一份**事实，
    #: 不在读的时候二次反查——反查是漂移的入口）。
    object_hosts: Mapping = field(default_factory=dict)

    def material_for_object(self, obj: Mapping):
        """该对象对应的材料（未成为材料 → ``None``）。"""
        oid = str(obj.get("released_object_id") or "")
        for object_id, material in zip(self.material_object_ids, self.materials):
            if object_id == oid:
                return material
        return None

    def refusal_for_object(self, obj: Mapping) -> str | None:
        oid = str(obj.get("released_object_id") or "")
        for refusal in self.refusals:
            if str(refusal.get("released_object_id") or "") == oid:
                return str(refusal.get("reason") or "")
        return None

    def objects_for_hosts(self, block_ids: Sequence[str]) -> tuple:
        hosts = {str(b) for b in (block_ids or ())}
        return tuple(o for o in self.objects
                     if str(o.get("host_evidence_id") or "") in hosts)

    def refusals_for_hosts(self, block_ids: Sequence[str]) -> tuple:
        hosts = {str(b) for b in (block_ids or ())}
        return tuple(r for r in self.refusals
                     if str(r.get("evidence_block_id") or "") in hosts)

    def material_ids_for_hosts(self, block_ids: Sequence[str]) -> tuple:
        hosts = {str(b) for b in (block_ids or ())}
        hosts_by_object = dict(self.object_hosts or {})
        return tuple(
            material.material_id
            for object_id, material in zip(self.material_object_ids, self.materials)
            if str(hosts_by_object.get(object_id, "")) in hosts)

    def refusal_reason_counts(self) -> dict:
        counts: dict = {}
        for refusal in self.refusals:
            reason = str(refusal.get("reason") or "")
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "envelope_kind": TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
            "material_version": TABLE_OBJECT_MATERIAL_VERSION,
            "object_count": len(self.objects),
            "material_count": len(self.materials),
            "refusal_count": len(self.refusals),
            "refusal_reason_counts": self.refusal_reason_counts(),
            "refusals": [dict(r) for r in self.refusals],
            "identity": dict(self.identity),
        }


def _release_refusal(res: Mapping, host_id: str, page_number: int) -> dict:
    """放行门拒绝记录（typed reason + 定位 + 形状证据；不含任何权威声明）。"""
    return {
        "kind": "table_object_refusal",
        "evidence_block_id": str(host_id),
        "page_number": int(page_number),
        "reason": res.get("reason"),
        "target_table_title": res.get("target_table_title"),
        "target_title_kind": res.get("target_title_kind"),
        "target_start": res.get("target_start"),
        "target_end": res.get("target_end"),
        "target_end_boundary": res.get("target_end_boundary"),
        "target_open_at_block_end": res.get("target_open_at_block_end"),
        "target_spans_block_boundary": res.get("target_spans_block_boundary"),
        "target_source_spans": [dict(s) for s in (res.get("target_source_spans") or ())],
        "target_source_span_state": res.get("target_source_span_state"),
        "target_header_decision": res.get("target_header_decision"),
        "target_header_layer_count": res.get("target_header_layer_count"),
        "body_evidence": res.get("body_evidence"),
        "release_rule_version": res.get("release_rule_version"),
    }


def _ordered(blocks: Sequence) -> list:
    return sorted(blocks, key=lambda b: (int(getattr(b, "page_number", 0) or 0),
                                         int(getattr(b, "block_index", 0) or 0)))


def build_table_object_batch(*, blocks: Sequence, binding,
                             block_heading_paths: Mapping | None = None,
                             dependency_fingerprint: str = ""
                             ) -> TableObjectMaterialBatch:
    """一份文档的 Evidence 块 → 表对象批次（求解全篇、逐条判资格；纯函数，不写库）。

    **求解**覆盖本份文档的全部块（解析索引必须完整，否则 `resolve` 会把真实 payload 读成
    dangling）；**人口有界**由调用方（工具侧）负责——只把已定位块的对象交出去。
    """
    if not isinstance(binding, TM.CurrentEvidenceBinding):
        raise TableObjectMaterialError(
            f"build_table_object_batch 需要 CurrentEvidenceBinding，得到 "
            f"{type(binding).__name__}")
    heading_paths = dict(block_heading_paths or {})
    ordered = _ordered(blocks)
    dependency_fingerprint = str(dependency_fingerprint or "") \
        or table_object_dependency_fingerprint(binding=binding, blocks=ordered)
    by_id: dict = {str(getattr(b, "evidence_block_id", "") or ""): b for b in ordered}
    raw: list[dict] = []
    for index, block in enumerate(ordered):
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        for res in TOR.release_table_objects(
                getattr(block, "text", None),
                block_id=str(getattr(block, "evidence_block_id", "") or ""),
                next_block_text=(getattr(nxt, "text", None) if nxt is not None else None),
                next_block_id=(str(getattr(nxt, "evidence_block_id", "") or "")
                               if nxt is not None else "")):
            raw.append({**res,
                        "_host_evidence_id": str(getattr(block, "evidence_block_id", "") or ""),
                        "_host_page_number": int(getattr(block, "page_number", 0) or 0)})

    objects: list[dict] = []
    materials: list = []
    records: list = []
    refusals: list[dict] = []
    material_object_ids: list[str] = []
    object_hosts: dict[str, str] = {}
    for res in TOR.suppress_continued_duplicates(raw):
        host_id = str(res.get("_host_evidence_id") or "")
        page_number = int(res.get("_host_page_number") or 0)
        if not res.get("released"):
            refusals.append(_release_refusal(res, host_id, page_number))
            continue
        obj = TOR.attach_locator(res, document_id=str(binding.document_id),
                                 evidence_block_id=host_id, page_number=page_number)
        obj.pop("_host_evidence_id", None)
        obj.pop("_host_page_number", None)
        obj["kind"] = "qualified_table_object"
        obj["host_evidence_id"] = host_id
        obj["host_page_number"] = page_number
        obj["node_ids"] = []
        objects.append(obj)
        object_hosts[str(obj.get("released_object_id") or "")] = host_id
        built, reason = build_table_object_material(
            obj, block=by_id[host_id], blocks=by_id, binding=binding,
            heading_path=tuple(heading_paths.get(host_id, ())),
            dependency_fingerprint=dependency_fingerprint)
        if built is None:
            refusals.append({
                "kind": "table_object_material_refusal",
                "evidence_block_id": host_id,
                "page_number": page_number,
                "released_object_id": str(obj.get("released_object_id") or ""),
                "target_table_title": str(obj.get("target_table_title") or ""),
                "target_title_kind": str(obj.get("target_title_kind") or ""),
                "reason": reason,
                "release_rule_version": str(obj.get("release_rule_version") or ""),
            })
            continue
        material, record = built
        materials.append(material)
        records.append(record)
        material_object_ids.append(str(obj.get("released_object_id") or ""))
    identity = {
        "envelope_kind": TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
        "material_version": TABLE_OBJECT_MATERIAL_VERSION,
        "release_rule_version": TOR.RELEASE_RULE_VERSION,
        "document_id": str(binding.document_id),
        "document_version": str(binding.document_version),
        "evidence_set_version": str(binding.evidence_set_version),
        "block_count": len(ordered),
        "object_count": len(objects),
        "material_count": len(materials),
        "refusal_count": len(refusals),
        "dependency_fingerprint": dependency_fingerprint,
    }
    return TableObjectMaterialBatch(
        objects=tuple(objects), materials=tuple(materials),
        payload_records=tuple(records), refusals=tuple(refusals),
        material_object_ids=tuple(material_object_ids), identity=identity,
        object_hosts=object_hosts)


class TableObjectPayloadResolver:
    """`harness.topic_schema.PayloadResolver` 的表对象实现（独立重切，不采信自报字段）。

    - 非本版本 / 非本类型 → ``None``（不越界代答，也不与 R2 的 `table_context` 抢答）；
    - 本版本但重算不出这个哈希 → ``None``（dangling；上层 fail-closed）；
    - 命中 → 逐项复核 authority_identity / created_dependency_fingerprint / locator，
      任一不符立即 ``TableObjectMaterialError``（不把损坏伪装成「未找到」）。
    """

    def __init__(self, batch: TableObjectMaterialBatch) -> None:
        if not isinstance(batch, TableObjectMaterialBatch):
            raise TableObjectMaterialError(
                "TableObjectPayloadResolver 只接受 TableObjectMaterialBatch")
        self._batch = batch
        self._by_hash: dict = {
            record.payload_hash: (material, record)
            for material, record in zip(batch.materials, batch.payload_records)}

    @property
    def batch(self) -> TableObjectMaterialBatch:
        return self._batch

    def resolve(self, payload_ref) -> TS.ResolvedPayload | None:
        if payload_ref.object_type != TABLE_OBJECT_MATERIAL_TYPE:
            return None
        if payload_ref.version != TABLE_OBJECT_MATERIAL_VERSION:
            return None
        entry = self._by_hash.get(payload_ref.content_hash)
        if entry is None:
            return None
        material, record = entry
        if record.authority_identity != payload_ref.authority_identity:
            raise TableObjectMaterialError(
                f"payload {record.payload_id} authority_identity 与 ref 不符")
        if record.created_dependency_fingerprint != payload_ref.created_dependency_fingerprint:
            raise TableObjectMaterialError(
                f"payload {record.payload_id} created_dependency_fingerprint 与 ref 不符")
        if material.locator.to_dict() != payload_ref.locator.to_dict():
            raise TableObjectMaterialError(
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
    "TABLE_OBJECT_MATERIAL_ENVELOPE_KIND",
    "MATERIAL_PAYLOAD_VERSION",
    "TABLE_OBJECT_MATERIAL_VERSION",
    "TABLE_OBJECT_MATERIAL_TYPE",
    "TABLE_OBJECT_MATERIAL_REASONS",
    "TABLE_OBJECT_PERMITTED_USE",
    "TABLE_OBJECT_EXCLUSIONS",
    "TableObjectMaterialError",
    "table_object_text",
    "table_object_reading_view",
    "table_object_dependency_fingerprint",
    "compute_table_object_material_id",
    "build_table_object_material",
    "TableObjectMaterialBatch",
    "build_table_object_batch",
    "TableObjectPayloadResolver",
]

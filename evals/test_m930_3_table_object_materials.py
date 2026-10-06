"""Eval: M930-3 第二批 —— 合格表对象 → 正式 Pack 材料（`table_context`）+ 独立重切解析器。

用法: python -X utf8 -m evals.test_m930_3_table_object_materials

覆盖（全部结构性、零公司/页码/关键词）：

- **阅读材料 ≠ 数字权威**：材料信封里两条声明（`reading_material=True` /
  `numeric_authority=False`）与排除项都在 payload 哈希里，读的人删不掉；
- **表对象身份 ≠ 材料身份**：`table_content_id`（内容寻址）/ `released_object_id`
  （内容 + 出现位置）逐字保留，材料另有自己的内容寻址 `material_id`；页码不进任何身份；
- **逐项复核**：对象被改写（体行 / 两条身份 / 权威声明）一律落 typed 原因，不静默降格；
- **退化表题不是具名表材料**：`title_not_verifiable` 的单列放行；
- **独立重切**：resolver 只按**重算出来的**哈希查表；非本版本 / 非本类型返回 None
  （不与 R2 的 `table_context` 抢答）；身份或 locator 漂移 fail-closed；
- **人口有界**：求解覆盖全篇（解析索引要完整），但交出去的对象按宿主块过滤；
- **跨块续表**：一张表只产**一份**材料（被续读的那块里的同体对象不另立材料）；
- 真实文档（样本 + Evidence 库缺失时如实 skip）：2024 年报的营业收入/营业成本表成为
  可读材料，正文逐字、数字权威显式否决。

不调 LLM、不联网、不写任何库。
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import table_object_materials as TOM   # noqa: E402
from harness import table_object_release as TOR     # noqa: E402
from harness import topic_schema as TS              # noqa: E402
from harness import tree_materials as TM            # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "data/samples/300750/announcements"

COMPANY = "c_demo"
DOC = "doc_demo"
VER = "v1"
SET = "es1"


@dataclass
class _Block:
    evidence_block_id: str
    text: str
    page_number: int = 1
    block_index: int = 0
    company_id: str = COMPANY
    document_id: str = DOC
    document_version: str = VER
    evidence_set_version: str = SET
    evidence_type: str = "text"
    structured_payload: dict | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _binding(*, is_current: bool = True, company: str = COMPANY) -> TM.CurrentEvidenceBinding:
    return TM.CurrentEvidenceBinding(
        company_id=company, document_id=DOC, document_version=VER,
        evidence_set_version=SET, is_current=is_current)


TABLE = ("表 1-1某构成表\n"
         "单位：千元\n"
         "项目  金额  占比\n"
         "甲类  1,234  10.0%\n"
         "乙类  2,345  20.0%\n"
         "合计  3,579  30.0%\n")
PROSE = "公司在本报告期内主要经营情况保持稳定，未发生重大变化。\n"
HEADER_TAIL = ("1）某整体情况\n"
               "单位：千元\n"
               "  项目  收入  成本  毛利率\n"
               " 分业务\n"
               "\n"
               "19\n")
BODY = ("电气机械及器材  356,519,551  268,494,348  24.69%\n"
        "采选冶炼行业  5,493,003  5,024,611  8.53%\n"
        "电池矿产资源  5,978,096  5,493,003  8.83%\n")


def _blocks() -> list[_Block]:
    return [
        _Block("blk_a", TABLE, page_number=10, block_index=0),
        _Block("blk_b", PROSE, page_number=11, block_index=0),
        _Block("blk_c", HEADER_TAIL, page_number=19, block_index=0),
        _Block("blk_d", BODY, page_number=20, block_index=0),
    ]


def _batch(blocks=None, *, binding=None, heading=()):
    blocks = blocks if blocks is not None else _blocks()
    paths = {b.evidence_block_id: heading for b in blocks} if heading else {}
    return TOM.build_table_object_batch(
        blocks=blocks, binding=binding or _binding(), block_heading_paths=paths)


def _check_material_shape(check, details) -> None:
    batch = _batch()
    check(len(batch.materials) == 2, "两块各有表 ⇒ 两份材料（散文块不产材料）")
    material = batch.materials[0]
    check(material.material_type == TOM.TABLE_OBJECT_MATERIAL_TYPE == "table_context",
          "表对象材料复用既有 material_type=table_context（不新造类型）")
    check(material.payload_ref.version == TOM.TABLE_OBJECT_MATERIAL_VERSION
          and material.payload_ref.object_type == "table_context",
          "payload_ref 带本通道自己的解析语义版本（不与 tmr-2 / R2 混用）")
    check(material.source_identity.startswith("evidence:")
          and material.source_identity == "evidence:blk_a",
          "来源身份落在 Evidence 域（与 authority 同域）")
    check(material.locator.to_dict() == material.payload_ref.locator.to_dict()
          and material.locator.page == 10,
          "材料 locator 与 ref locator 逐字相同，页码只作阅读坐标")
    check(material.authority_assessment.verdict == "authoritative"
          and material.authority_assessment.content_hash
          == hashlib.sha256(TABLE.encode("utf-8")).hexdigest(),
          "权威由宿主块确定性重算（来源层 content_hash = 宿主块哈希）")

    resolved = TOM.TableObjectPayloadResolver(batch).resolve(material.payload_ref)
    check(resolved is not None, "独立重切解析器能解析自己产出的 ref")
    if resolved is None:
        return
    envelope = json.loads(resolved.payload_bytes.decode("utf-8"))
    check(hashlib.sha256(resolved.payload_bytes).hexdigest()
          == material.payload_ref.content_hash,
          "解析出的字节重算哈希 == payload_ref.content_hash")
    check(envelope["material_payload_version"] == 1
          and envelope["envelope_kind"] == TOM.TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
          "信封版本键与信封种类都与公共门的要求一致")
    check(envelope["evidence_id"] == "blk_a"
          and envelope["source_content_hash"] == material.authority_assessment.content_hash,
          "信封带 evidence_id 与来源层 content_hash（材料侧回查的两个锚点）")
    text = envelope["content"]["text"]
    check(text.startswith("表 1-1某构成表") and "乙类  2,345  20.0%" in text
          and "合计  3,579  30.0%" in text,
          "正文读视图是表题/单位/表头/表体的**逐字原文**（不重排、不折算）")
    check(text.splitlines()[0] == batch.objects[0]["target_table_title"]
          and text.splitlines()[1] == "单位：千元",
          "正文首两行就是对象自己的表题与单位（同一个来源，不另造表题）")
    structured = envelope["content"]["structured_payload"]
    check(structured["reading_material"] is True
          and structured["numeric_authority"] is False
          and structured["financial_authority_claimed"] is False,
          "结构读视图里阅读材料=真、数字/财务权威=假（两条独立声明）")
    check(structured["permitted_use"] == TOM.TABLE_OBJECT_PERMITTED_USE
          and set(structured["exclusions"]) == set(TOM.TABLE_OBJECT_EXCLUSIONS),
          "允许用途与排除项随材料一起到达消费方（封闭词表）")
    check(envelope["reading_policy"]["numeric_authority"] is False
          and envelope["reading_policy"]["exclusions"],
          "信封层同样写明阅读材料与排除项（进 payload 哈希，删不掉）")
    check(structured["table_content_id"] == batch.objects[0]["content_id"]
          and structured["released_object_id"] == batch.objects[0]["released_object_id"],
          "表对象的**两条正交身份**逐字保留在材料里")
    check("no_numeric_authority" in structured["exclusions"]
          and "not_a_substitute_for_contract_fact" in structured["exclusions"],
          "排除项逐条写明：无数字权威、表材料在场不等于 Contract 事实已取得")


def _check_identity_axes(check, details) -> None:
    batch = _batch()
    obj = batch.objects[0]
    material = batch.materials[0]
    check(material.material_id != obj["content_id"]
          and material.material_id != obj["released_object_id"],
          "材料身份既不等于内容身份、也不等于出现位置身份（三条身份不混用）")
    check(material.material_id.startswith("mat-tob-")
          and obj["released_object_id"] != obj["content_id"],
          "材料身份与表对象身份各有自己的规范形")
    # 页码不进身份：同一块的同一张表，页号变了，两条表对象身份都不变。
    shifted_locator = dict(obj["source_locator"])
    shifted_locator["page_number"] = 999
    check(TOR.released_object_id(content_id=obj["content_id"],
                                 source_locator=shifted_locator)
          == obj["released_object_id"],
          "页码不进出现位置身份（同一处的同一张表换了页号仍是同一条记录）")
    check(obj["source_locator"]["page_number"] == 10
          and material.locator.page == 10,
          "页码只落在 locator / 材料 locator 里作阅读坐标")
    # 幂等：同一份块、同一个绑定 ⇒ 同一份材料身份。
    again = _batch()
    check(again.materials[0].material_id == material.material_id
          and again.materials[0].content_hash == material.content_hash,
          "同一输入重跑得到同一材料身份与同一 payload 哈希（内容寻址、跨 run 幂等）")
    # 依赖指纹：确定性、内容寻址、**页码与块序不进指纹**。
    fingerprint = batch.identity["dependency_fingerprint"]
    check(len(fingerprint) == 64 and batch.identity["dependency_fingerprint"]
          == again.identity["dependency_fingerprint"],
          "依赖指纹是 64 位 sha256 且跨重跑稳定（可安全参与 Pack 内容身份）")
    repaged = _batch([_Block("blk_a", TABLE, page_number=77, block_index=5),
                      _Block("blk_b", PROSE, page_number=78, block_index=0),
                      _Block("blk_c", HEADER_TAIL, page_number=79, block_index=0),
                      _Block("blk_d", BODY, page_number=80, block_index=0)])
    check(repaged.identity["dependency_fingerprint"] == fingerprint,
          "换一次分页/块序不改变依赖指纹（页码不是判据）")
    changed = _batch([_Block("blk_a", TABLE.replace("1,234", "1,235"),
                             page_number=10, block_index=0),
                      _Block("blk_b", PROSE, page_number=11, block_index=0),
                      _Block("blk_c", HEADER_TAIL, page_number=19, block_index=0),
                      _Block("blk_d", BODY, page_number=20, block_index=0)])
    check(changed.identity["dependency_fingerprint"] != fingerprint,
          "来源内容变了 ⇒ 依赖指纹跟着变（内容寻址，不是常量）")
    # 另一张表 ⇒ 另一份材料。
    other = _batch([_Block("blk_a", TABLE, page_number=10, block_index=0),
                    _Block("blk_e", TABLE.replace("1-1", "1-2"), page_number=30,
                           block_index=0)])
    check(other.materials[0].material_id != other.materials[1].material_id,
          "两张不同的表是两份材料")
    check(other.materials[0].payload_ref.locator.offset
          == int(other.objects[0]["source_locator"]["char_range"][1]),
          "locator.offset 记的是这张表在宿主块归一块坐标下的精确结束界")


def _check_refusals(check, details) -> None:
    blocks = _blocks()
    batch = _batch(blocks)
    # 1) 数字权威声明：对象自称有数字权威 ⇒ 逐条拒绝，且**不**降格成普通正文材料。
    objects = [dict(o) for o in batch.objects]
    tampered = dict(objects[0], numeric_authority=True)
    built, reason = TOM.build_table_object_material(
        tampered, block=blocks[0], binding=_binding())
    check(built is None and reason == "numeric_authority_claimed",
          "自称数字权威的对象被拒绝（本通道不放行任何金额权威）")
    # 2) 阅读材料声明缺失 / 为假。
    for label, patch in (("not_reading_material", {"reading_material": False}),
                         ("missing", {"reading_material": None})):
        built, reason = TOM.build_table_object_material(
            dict(objects[0], **patch), block=blocks[0], binding=_binding())
        check(built is None and reason == "not_reading_material",
              f"阅读材料声明不成立的形态（{label}）被拒绝")
    # 3) 体行被改写 ⇒ 内容身份对不上。
    built, reason = TOM.build_table_object_material(
        dict(objects[0], target_body_row_texts=["甲类  9,999  99.9%"]),
        block=blocks[0], binding=_binding())
    check(built is None and reason == "content_id_mismatch",
          "体行被改写的对象被拒绝（内容身份重算对不上）")
    # 4) 出现位置身份被改写。
    built, reason = TOM.build_table_object_material(
        dict(objects[0], released_object_id="deadbeef"), block=blocks[0],
        binding=_binding())
    check(built is None and reason == "released_object_id_mismatch",
          "出现位置身份被改写的对象被拒绝")
    # 5) 退化表题 ⇒ 不是具名表材料（保留在对象与拒绝记录里）。
    degraded = dict(objects[0], target_title_verifiable=False)
    built, reason = TOM.build_table_object_material(
        degraded, block=blocks[0], binding=_binding())
    check(built is None and reason == "title_not_verifiable",
          "表题未经复核的对象不得成为具名表材料")
    # 6) 空体表。
    built, reason = TOM.build_table_object_material(
        dict(objects[0], target_body_row_texts=[], content_id=TOR.table_content_id(
            title=objects[0]["target_table_title"], unit=objects[0]["target_unit"],
            header_lines=objects[0]["target_header_lines"],
            wrap_lines=objects[0]["target_header_wrap_lines"], body_row_texts=[],
            closure_row=objects[0]["target_closure_row"])),
        block=blocks[0], binding=_binding())
    check(built is None and reason == "empty_body"
          and "empty_body" in TOM.TABLE_OBJECT_MATERIAL_REASONS,
          "没有表体行的表不构成可写材料（typed 原因在闭集内）")
    # 7) 宿主块不属于本绑定（跨公司 / 跨版本 / 跨 set）。
    foreign = _Block("blk_a", TABLE, company_id="c_other")
    built, reason = TOM.build_table_object_material(
        objects[0], block=foreign, binding=_binding())
    check(built is None and reason == "host_binding_mismatch",
          "宿主块与显式 current Evidence 绑定不一致 ⇒ 拒绝（fail-closed）")
    # 8) 权威重算不为 authoritative。
    built, reason = TOM.build_table_object_material(
        objects[0], block=blocks[0], binding=_binding(is_current=False))
    check(built is None and reason == "authority_not_authoritative",
          "非 current 绑定 ⇒ 权威重算失败，材料不产出（不采信自报 verdict）")
    check(set(TOM.TABLE_OBJECT_MATERIAL_REASONS)
          >= {"not_reading_material", "numeric_authority_claimed", "title_not_verifiable",
              "empty_body", "content_id_mismatch", "released_object_id_mismatch",
              "host_block_unavailable", "host_binding_mismatch",
              "authority_not_authoritative"},
          "拒绝原因是封闭集合，且逐条对应一个可复算判据")
    # 批次侧的拒绝也逐条留痕（不是静默丢弃）。
    current = _batch([_Block("blk_a", TABLE, page_number=10, block_index=0)],
                     binding=_binding(is_current=False))
    check(current.materials == () and len(current.refusals) == 1
          and current.refusals[0]["reason"] == "authority_not_authoritative"
          and current.refusals[0]["kind"] == "table_object_material_refusal",
          "批次里未获资格的对象逐条留下 typed 拒绝记录")
    check(current.refusal_reason_counts() == {"authority_not_authoritative": 1},
          "拒绝原因直方图由记录本身派生（可读回）")


def _check_source_verification(check, details) -> None:
    """`tobj-2` 的**逐来源块**复核门（材料侧）：越界 / 错版本 / 漏一块 / 相邻无关块 / 合法跨块。

    `tobj-1` 的跨块对象把拼接偏移写进单块 locator（真实三份文档 15/15 越界），材料侧当时
    无从发现 —— 这一组用例钉住现在**逐块**可复核：声明必须齐全、块必须在场且同版本同 set、
    区间必须落在**各自**块内。任一不成立即 fail-closed，**不**降格成「部分来源」放行。
    """
    blocks = [_Block("blk_c", HEADER_TAIL, page_number=19, block_index=0),
              _Block("blk_d", BODY, page_number=20, block_index=0)]
    batch = _batch(blocks)
    obj = next((dict(o) for o in batch.objects if o.get("continuation_block_read")), None)
    check(obj is not None, "来源复核：先取一个合法跨块对象（正例存在才谈反例）")
    if obj is None:
        return
    spans = [dict(s) for s in obj["target_source_spans"]]
    check(len(spans) == 2, "合法跨块对象带两条逐块区间（宿主块 + 续块）")

    # 正例：合法跨块 ⇒ 产材料，且读视图逐块写明来源（各带自己那一块的页与长度）。
    printed = batch.identity["dependency_fingerprint"]
    built, reason = TOM.build_table_object_material(
        obj, block=blocks[0], blocks=blocks, binding=_binding(),
        dependency_fingerprint=printed)
    check(built is not None and reason == "",
          f"合法跨块对象产出材料（reason={reason!r}）")
    if built is not None:
        material = built[0]
        envelope = json.loads(
            TOM.TableObjectPayloadResolver(batch).resolve(
                material.payload_ref).payload_bytes.decode("utf-8"))
        structured = envelope["content"]["structured_payload"]
        check(envelope.get("source_span_state") == TOR.SOURCE_SPAN_STATE_MULTI
              and len(envelope.get("source_spans") or ()) == 2,
              "读视图逐块登记来源（跨块不被写成一个块）")
        check([int(s.get("page_number") or 0) for s in (envelope.get("source_spans") or ())]
              == [19, 20],
              "逐块来源各带**自己那一块**的页码（19 表头页 / 20 续页）")
        check(all(s.get("content_hash") for s in (envelope.get("source_spans") or ())),
              "逐块来源各带内容哈希（哪一块、哪一版，可回指）")
        check(structured["continuation_block_read"] is True
              and structured["source_span_state"] == TOR.SOURCE_SPAN_STATE_MULTI,
              "对象读视图同样写明跨块与逐块状态")
        check(envelope.get("source_content_hash_scope") == "host_block_only"
              and len((envelope["content"].get("structured_payload") or {})
                      .get("source_spans") or ()) == 2,
              "跨块对象的哈希作用域被显式限定在宿主块（不谎称整表单一来源）")
        # 反例：相邻无关块（散文块）**不**进入来源，也不进正文 —— 跨块只按声明取，
        # 绝不「把相邻块一并算进来」。
        ids = {str(s.get("evidence_block_id") or "")
               for s in (envelope.get("source_spans") or ())}
        check(ids == {"blk_c", "blk_d"} and PROSE.strip() not in envelope["content"]["text"],
              "相邻无关块既不进逐块来源也不进正文（跨块不是「接上就放行」）")

    # 反例①越界：把续块的区间改到超出该块长度 ⇒ 逐块复核失败（`tobj-1` 的真实形态）。
    wide = [dict(spans[0]),
            dict(spans[1], char_range=[0, len(BODY) + 531])]
    built, reason = TOM.build_table_object_material(
        dict(obj, target_source_spans=wide), block=blocks[0], blocks=blocks,
        binding=_binding())
    check(built is None and reason == "source_span_out_of_range",
          "逐块区间越出**它自己那一块** ⇒ 拒绝（不再有「声明了不存在的字符」还能入包）")
    # 反例②错版本：续块换版本 ⇒ 逐块比对不通过（拼接不同版本的文本不是一张表）。
    other_ver = [blocks[0],
                 _Block("blk_d", BODY, page_number=20, block_index=0, document_version="v2")]
    built, reason = TOM.build_table_object_material(
        obj, block=other_ver[0], blocks=other_ver, binding=_binding())
    check(built is None and reason == "source_binding_mismatch",
          "续块版本与绑定不符 ⇒ 拒绝（逐块比对 document/version/set）")
    # 反例③漏一块：跨块对象只声明宿主块（表体其实取自续块）⇒ 声明不齐，拒绝。
    built, reason = TOM.build_table_object_material(
        dict(obj, target_source_spans=[spans[0]]), block=blocks[0], blocks=blocks,
        binding=_binding())
    check(built is None and reason == "source_span_unresolved",
          "跨块对象**漏掉续块** ⇒ 拒绝（残缺来源不冒充完整来源）")
    # 反例④状态与条数不符：自称跨块却只有一条区间 ⇒ 同样按声明不齐拒绝。
    built, reason = TOM.build_table_object_material(
        dict(obj, target_source_spans=[spans[0]],
             target_source_span_state=TOR.SOURCE_SPAN_STATE_MULTI),
        block=blocks[0], blocks=blocks, binding=_binding())
    check(built is None and reason == "source_span_unresolved",
          "自称跨块但只有一条区间 ⇒ 拒绝（状态与声明必须自洽）")
    # 反例⑤声明了不在场/不在本集合的块 ⇒ 来源不可核，拒绝。
    built, reason = TOM.build_table_object_material(
        dict(obj, target_source_spans=spans + [{"evidence_block_id": "blk_zzz",
                                                "char_range": [0, 5],
                                                "block_text_length": 5}]),
        block=blocks[0], blocks=blocks, binding=_binding())
    check(built is None and reason == "source_block_unavailable",
          "声明了块集合里不存在的块 ⇒ 拒绝（来源不可核）")
    # 反例⑥宿主块不在声明里 ⇒ 来源无法定位，拒绝。
    built, reason = TOM.build_table_object_material(
        dict(obj, target_source_spans=[spans[1]]), block=blocks[0], blocks=blocks,
        binding=_binding())
    check(built is None and reason == "source_span_unresolved"
          and "source_span_unresolved" in TOM.TABLE_OBJECT_MATERIAL_REASONS,
          "宿主块不在逐块声明里 ⇒ 拒绝（typed 原因在闭集内）")
    check(set(TOM.TABLE_OBJECT_MATERIAL_REASONS)
          >= {"source_span_unresolved", "source_block_unavailable",
              "source_binding_mismatch", "source_span_out_of_range"},
          "四个来源复核原因是封闭集合的一部分（逐条对应一个可复算判据）")


def _check_scope_and_continuation(check, details) -> None:
    batch = _batch()
    check(len(batch.objects_for_hosts(["blk_b"])) == 0
          and len(batch.objects_for_hosts(["blk_a"])) == 1,
          "按宿主块取对象：散文块取不到表（人口由定位结果决定）")
    check(batch.material_ids_for_hosts(["blk_a"]) == (batch.materials[0].material_id,)
          and batch.material_ids_for_hosts(["blk_b"]) == (),
          "材料的宿主归属与对象的宿主归属同源（不二次反查）")
    check(batch.objects_for_hosts(["blk_a", "blk_b"])[0]["host_evidence_id"] == "blk_a",
          "交出去的对象带宿主块 id（可审计回指）")
    check(batch.identity["block_count"] == 4
          and batch.identity["material_count"] == len(batch.materials) == 2
          and batch.identity["object_count"] == len(batch.objects)
          and batch.identity["refusal_count"] == len(batch.refusals),
          "批次身份如实登记块数 / 对象数 / 材料数 / 拒绝数（求解覆盖全篇，人口由调用方过滤）")
    # 跨块续表：表题在本块、表体在下一块 ⇒ 一份材料，且读视图写明「被续读过」。
    cont = _batch([_Block("blk_c", HEADER_TAIL, page_number=19, block_index=0),
                   _Block("blk_d", BODY, page_number=20, block_index=0)])
    continued = [o for o in cont.objects if o.get("continuation_block_read")]
    check(len(continued) == 1 and len(cont.materials) == 1,
          "跨块续表只产一份材料（被续读那块里的同体对象不另立材料）")
    if cont.materials:
        envelope = json.loads(
            TOM.TableObjectPayloadResolver(cont).resolve(
                cont.materials[0].payload_ref).payload_bytes.decode("utf-8"))
        structured = envelope["content"]["structured_payload"]
        check(structured["continuation_block_read"] is True,
              "读视图写明这张表是跨块续读拼起来的")
        body = envelope["content"]["text"].splitlines()
        check(all(row in body for row in BODY.splitlines()),
              "续读进来的表体逐字进正文（不截断、不改写）")
        check(cont.materials[0].locator.page == 19,
              "续表材料的 locator 指向**表头所在页**（不指向续读块）")


def _check_resolver_boundary(check, details) -> None:
    batch = _batch()
    resolver = TOM.TableObjectPayloadResolver(batch)
    ref = batch.materials[0].payload_ref
    check(resolver.resolve(ref) is not None, "本通道 ref 可解析")
    # 非本类型 / 非本版本：返回 None，绝不代答（R2 的 table_context 由 R2 自己解析）。
    for label, ref2 in (
            ("其它类型", TS.MaterialPayloadRef(
                object_type="evidence_span", authority_identity=ref.authority_identity,
                version=TOM.TABLE_OBJECT_MATERIAL_VERSION, content_hash=ref.content_hash,
                locator=ref.locator,
                created_dependency_fingerprint=ref.created_dependency_fingerprint)),
            # 另一套解析语义（span 材料读法 `tmr-*`）的版本：本解析器只认自己那一种，
            # 因此这里取**当前**的 `TREE_MATERIAL_RESOLVER_VERSION`——写死一个旧号会在
            # 读法升号后变成一句过期的陈述。
            ("另一套解析语义版本", TS.MaterialPayloadRef(
                object_type="table_context", authority_identity=ref.authority_identity,
                version=TS.TREE_MATERIAL_RESOLVER_VERSION, content_hash=ref.content_hash,
                locator=ref.locator,
                created_dependency_fingerprint=ref.created_dependency_fingerprint))):
        check(resolver.resolve(ref2) is None, f"{label} 的 ref 返回 None（不越界代答）")
    # 哈希对不上 ⇒ dangling（None），不把改写伪装成「找到了」。
    dangling = TS.MaterialPayloadRef(
        object_type="table_context", authority_identity=ref.authority_identity,
        version=TOM.TABLE_OBJECT_MATERIAL_VERSION,
        content_hash=hashlib.sha256(b"not-this").hexdigest(), locator=ref.locator,
        created_dependency_fingerprint=ref.created_dependency_fingerprint)
    check(resolver.resolve(dangling) is None, "重算不出的哈希 ⇒ dangling")
    # 身份漂移 ⇒ fail-closed（不伪装成 dangling）。
    for label, field_name, drifted in (
            ("authority_identity", "authority_identity", "evidence:blk_zzz"),
            ("dependency_fingerprint", "created_dependency_fingerprint", "dep-other")):
        hostile = TS.MaterialPayloadRef(
            object_type="table_context", authority_identity=ref.authority_identity,
            version=TOM.TABLE_OBJECT_MATERIAL_VERSION, content_hash=ref.content_hash,
            locator=ref.locator,
            created_dependency_fingerprint=ref.created_dependency_fingerprint)
        object.__setattr__(hostile, field_name, drifted)
        try:
            resolver.resolve(hostile)
            check(False, f"{label} 漂移应 fail-closed")
        except TOM.TableObjectMaterialError:
            check(True, f"{label} 漂移 fail-closed（不伪装成「未找到」）")
    check(len(TOM.TableObjectPayloadResolver(batch)._by_hash) == len(batch.materials),
          "解析索引来自重切结果（一份材料一个哈希，不含未放行对象）")
    check(ref.version == TOM.TABLE_OBJECT_MATERIAL_VERSION
          and ref.object_type == "table_context"
          and ref.content_hash == batch.materials[0].content_hash,
          "本通道 payload_ref 通过公共 MaterialPayloadRef 校验（版本是具名串，不是裸 1）")


def _check_real_document(check, details) -> int:
    evidence_db = REPO / "data/evidence.db"
    pdf = SAMPLES / "NDSD_2024_year.pdf"
    if not pdf.exists() or not evidence_db.exists():
        details.append("SKIP 缺 2024 年报样本或 data/evidence.db（真实文档断言未执行）")
        return 1
    from document_structure import live_span_source as LSS
    from evidence import store as estore

    estore._db_path = evidence_db.resolve()
    company = pdf.parents[1].name
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    version = "sha256-" + sha[:16]
    set_version = estore.current_evidence_set_ro(evidence_db.resolve(), company,
                                                pdf.stem, version)
    if not isinstance(set_version, str) or not set_version:
        details.append("SKIP Evidence 库里没有该文档的 current set（不猜值）")
        return 1
    live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
        company_id=company, document_id=pdf.stem, document_version=version,
        raw_pdf_path=str(pdf), raw_pdf_sha256=sha,
        expected_current_evidence_set_version=set_version))
    binding = TM.evidence_binding_from_snapshot(live.evidence_snapshot)
    batch = TOM.build_table_object_batch(blocks=live.evidence_blocks, binding=binding)
    hit = [o for o in batch.objects
           if str(o.get("target_table_title") or "").startswith("1）营业收入及营业成本整体情况")]
    check(len(hit) == 1, "2024 年报：营业收入/营业成本表出现在表对象批次里")
    if not hit:
        return 0
    material = batch.material_for_object(hit[0])
    check(material is not None, "该表在批次里产出了正式材料（不再只是诊断对象）")
    if material is None:
        return 0
    resolved = TOM.TableObjectPayloadResolver(batch).resolve(material.payload_ref)
    check(resolved is not None, "真实表材料的 ref 可被独立重切解析")
    if resolved is None:
        return 0
    envelope = json.loads(resolved.payload_bytes.decode("utf-8"))
    text = envelope["content"]["text"]
    check(material.material_type == "table_context"
          and material.material_id.startswith("mat-tob-"),
          "真实表材料是 table_context 且身份自成本通道规范形")
    check("电气机械及器材" in text and "采选冶炼行业" in text,
          "真实表体逐字进入材料正文（Writer 读得到真实行）")
    check(envelope["content"]["structured_payload"]["numeric_authority"] is False
          and envelope["reading_policy"]["numeric_authority"] is False,
          "真实表材料仍显式否决数字权威（只作阅读材料）")
    check(len(hit[0]["target_body_row_texts"]) >= 8,
          "真实跨块表的表体行完整（不截断）")
    material_hashes = {m.payload_ref.content_hash for m in batch.materials}
    check(len(material_hashes) == len(batch.materials),
          "真实文档里每份表材料的 payload 哈希唯一（没有两张表被压成一份）")
    return 0


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

    _check_material_shape(check, details)
    _check_identity_axes(check, details)
    _check_refusals(check, details)
    _check_source_verification(check, details)
    _check_scope_and_continuation(check, details)
    _check_resolver_boundary(check, details)
    skipped += _check_real_document(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

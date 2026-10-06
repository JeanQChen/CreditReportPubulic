# -*- coding: utf-8 -*-
"""TS5 S1–S9：`to-4` 线格式、身份 DAG、信任边界与 profile 真值表（计划 §19.12.1）。

覆盖（全部离线：只读本地 PDF 与**只读** `data/evidence.db`；无网络、无 LLM、无写入）：

- **S1** §19.12.1-1：`V.TABLE_RECORD_PUBLIC_TYPES` 十类（to-4 / cell / final span /
  decision / relation / binding / coverage / gap / conservation / final snapshot）
  逐类 `to_dict → from_dict` 往返、`canonical_json` 逐字节相同、线格式字段集精确等于
  dataclass 字段集，schema 版本逐类钉死，`create()` 由非派生字段独立重建；
- **S2** §19.12.1-2：未知字段、缺字段、错误 enum、错误顺序、重复成员、非法 hash、
  跨类型字典混淆逐项走 `from_dict` 的正式拒绝路径；
- **S3** §19.12.1-3/8：`to-3` / `tb-1` / `tc-1` 历史版本被**显式**拒绝并要求迁移；
  未知版本与旧 `harness.table_structure.table_object_id` 形状不得冒充 to-4 身份；
- **S4** §19.12.1-4/5：raw `SpanBuildSnapshot`、JSON、同名伪 wrapper 进入
  `build_final_material_snapshot` 一律拒绝；缺裁决阈值时同样 fail-closed；未签发的
  同形 final wrapper 不取得 TS6 资格；final capability 的 `to_dict` / `copy` /
  `deepcopy` / `pickle` 全部拒绝，`testing` 域不得冒充 `live` / `pinned_acceptance`；
- **S5** §19.12.1-6/7：上游依赖束指纹可由 roots 独立复现且对**每一个**上游身份字段
  敏感（PDF / layout / outline / Evidence / alignment / policy / 版本 / settings）；
  篡改 layout / TS4 快照 / outline 身份后下游 ID 全部重算仍被复核器拒绝；owner 跨根
  被拒绝；版本身份被换在 wire 层即拒绝；同一正式输入双次构建逐字节相同；
- **S6** §19.12.1-9：身份 DAG 无环、拓扑序按边独立复算、TableObject 不含任何下游
  ID 字段、表 id 与下游无关、改下游必改 final 内容指纹、final snapshot 身份不进入
  上游束也不被成员回指；
- **S7** §19.12.1-11：`tpr-1` 注册表 current / 字节漂移 / 内容指纹漂移 / 版本与种类
  漂移真值表；四类 structure class 五行真值表；五类 cell block role；冲突与未闭合
  一律 fail-closed；页码 / 表号 / 公司名扰动不改变分类；任意 class 均不产生 authority；
  profile 资产不得含公司名与固定表号（注入即被正式拒绝）；
- **S8** §19.12.1-12：`TableOwnerRef` 两分支恰一成立，node / unassigned 的对象级
  回查通过；两分支都缺、同时存在、未登记引用种类、boundary 页范围倒置、表页越界
  均被拒绝；
- **S9** §19.12.2A-7 的线格式半边：alignment / refusal 两类终态齐备且身份形状闭合、
  refusal 侧不存在对齐 ID；非 aligned 终态（offset-unverifiable / residue）不得成为
  任何 cell source ref。

**只读与性能**：`TG.extract_table_geometry` 在本次进程内按 `(layout capability, pages)`
记忆化（`_install_geometry_memo`）。它是对**同一** capability 的纯函数，复核器读到的
仍是同一份报告，因此不削弱任何断言，只是把重复的 pdfplumber 扫描降为一次。
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import pickle
import shutil
import tempfile
import time
from pathlib import Path

from evidence import store as _estore

from document_structure import final_material_builder as FMB
from document_structure import final_verifier as FV
from document_structure import span_schema as SS
from document_structure import table_classification as TC
from document_structure import table_geometry as TG
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.aligner import AlignmentTerminal, align_evidence_set_verified
from document_structure.canonical import SchemaValidationError, canonical_json
from document_structure.evidence_gateway import bind_current_evidence_authority
from document_structure.layout_builder import build_verified_page_layout
from document_structure.span_builder import build_span_snapshot, issue_live_ts3_handoff
from document_structure.span_verifier import verify_span_snapshot
from evals import tree_stage_env as STAGE

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


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
    except exc as e:
        text = str(e)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text[:400]!r}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


# ---------------------------------------------------------------------------
# 0. 真实现场（只读）
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_DB_PATH = _REPO / "data" / "evidence.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
_DOC_KEY = "NDSD_2024_year"
_POLICIES = _REPO / "document_structure" / "policies"

_CTX: dict = {}

#: 每个内容寻址类型的**派生**字段（`create()` 自己算，不得由调用方提供）。
_DERIVED = {
    "TableObjectV4": ("table_locator", "table_id", "content_fingerprint",
                      "structure_fingerprint", "provenance_fingerprint"),
    "FinalOutlineSpan": ("span_locator", "span_id", "content_fingerprint"),
    "TableRangeDecision": ("decision_locator", "decision_id"),
    "TableRelation": ("relation_locator", "relation_id", "content_fingerprint"),
    "FinalComponentBinding": ("binding_locator", "binding_id"),
    "TableCitableCoverage": ("coverage_locator", "coverage_id"),
    "TableStructureGap": ("gap_locator", "gap_id"),
    "FinalMaterialConservation": ("conservation_locator", "conservation_id"),
    "FinalMaterialStructureSnapshot": ("snapshot_locator", "snapshot_id",
                                       "content_fingerprint"),
    "FinalNavigationSynopsis": ("synopsis_locator", "synopsis_id"),
}


def _db_identity(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _trust_doc(key: str = _DOC_KEY) -> dict:
    doc = json.loads(_TRUST.read_text(encoding="utf-8"))["documents"][key]
    return {"pdf": _REPO / doc["source_pdf_relpath"],
            "identity": doc["expected_identity"]}


def _recreate(obj, **override):
    """用**非派生**字段重跑 `create()`：派生 locator / 指纹 / identity 因此自洽。

    这正是 §19.12.1-6 要求的那种篡改：改完上游身份后把下游身份**全部重算**，
    使对象内部完全自洽，再交给复核器。
    """
    derived = _DERIVED[type(obj).__name__]
    payload = {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)
               if f.name not in derived}
    payload.update(override)
    return type(obj).create(**payload)


def _resort(members) -> tuple:
    """按 wire 层的登记排序键重排成员（改动身份后仍拼得出合法快照）。"""
    return tuple(sorted(members, key=TS._member_sort_key))


def _rebase_synopses(synopses, old_spans, new_spans) -> tuple:
    """把 final 简介的来源 / 片段 ID 重指向**重算后**的 final span。

    `fms-1` 构造期强制"简介来源必须在本 snapshot 的 final_spans 内且同节点"
    （§19.0.1 方案 C 的成员闭合），因此任何改到 final span 身份的"自洽篡改"都必须
    同步重算简介——否则连篡改件都拼不出来，反例就测不到复核器那一层。
    """
    remap = {o.span_id: n.span_id for o, n in zip(old_spans, new_spans)}
    by_id = {n.span_id: n for n in new_spans}
    out = []
    for syn in synopses:
        snippets = []
        for index, sn in enumerate(syn.snippets):
            target = by_id[remap[sn.final_span_id]]
            snippets.append(TS.FinalSynopsisSnippet(
                final_span_id=target.span_id,
                final_span_locator=target.span_locator,
                final_span_schema_version=target.schema_version,
                snippet_index=index,
                char_start=sn.char_start, char_end=sn.char_end, text=sn.text))
        source_ids: list = []
        for sid in syn.source_final_span_ids:
            moved = remap.get(sid, sid)
            if moved not in source_ids:
                source_ids.append(moved)
        out.append(TS.FinalNavigationSynopsis.create(
            node_id=syn.node_id, schema_version=syn.schema_version,
            synopsis_version=syn.synopsis_version, status=syn.status,
            reason_code=syn.reason_code, snippets=tuple(snippets),
            source_final_span_ids=tuple(source_ids)))
    return tuple(out)


def _recreate_all(final, **override):
    """快照与**全部**成员一起重算（成员身份互相咬合：改一处必须全改）。

    快照构造期强制"成员的上游依赖束与快照一致"，因此任何针对快照级字段的篡改
    都必须把每个携带该字段的成员同步重算，否则连非法快照都拼不出来。
    简介还必须随重算后的 final span **重新闭合**（见 `_rebase_synopses`）。
    """
    kwargs = {}
    span_pairs = None
    for field in dataclasses.fields(final):
        value = getattr(final, field.name)
        if isinstance(value, tuple) and value and \
                type(value[0]).__name__ in _DERIVED:
            recreated = [_recreate(m, **override) for m in value]
            if field.name == "final_spans":
                span_pairs = (value, recreated)
            kwargs[field.name] = _resort(recreated)
        elif type(value).__name__ in _DERIVED:
            kwargs[field.name] = _recreate(value, **override)
    if span_pairs is not None:
        kwargs["synopses"] = _rebase_synopses(final.synopses, *span_pairs)
    return _recreate(final, **kwargs, **override)


#: 线格式对 dataclass 字段名的**登记别名**（`to_dict` 用的是这些名字）。
_WIRE_RENAMES = {
    "TableRelation": {"source_endpoint": "source",
                      "target_endpoint": "target"},
}
#: 线格式里的**附加**键（无同名 dataclass 字段，属身份载荷的显式标记）。
_WIRE_EXTRA_KEYS = {
    "TableStructureGap": {"scope"},
    "FinalMaterialConservation": {"scope"},
}


def _install_geometry_memo():
    """把 `TG.extract_table_geometry` 换成本进程内的记忆化包装；返回原函数。"""
    original = TG.extract_table_geometry
    memo: dict = {}

    def _memo(layout_capability, *, pages=None):
        key = (id(layout_capability),
               None if pages is None else tuple(pages))
        if key not in memo:
            memo[key] = original(layout_capability, pages=pages)
        return memo[key]

    TG.extract_table_geometry = _memo
    return original


def _live_chain() -> dict:
    """跑一次真实链路并缓存（布局 → 对齐 → 交接 → TS4 快照 → TS5 快照）。"""
    if "final" in _CTX or "error" in _CTX:
        return _CTX
    try:
        doc = _trust_doc()
        ident = doc["identity"]
        authority = bind_current_evidence_authority()
        layout = build_verified_page_layout(
            doc["pdf"], company_id=ident["company_id"],
            document_id=ident["document_id"])
        alignment = align_evidence_set_verified(layout, authority)
        handoff = issue_live_ts3_handoff(layout, alignment, authority)
        snap = build_span_snapshot(handoff, stage=STAGE.current_stage())
        verified = verify_span_snapshot(snap, handoff)
        final = FMB.build_final_material_snapshot(verified)
        _CTX.update({"doc": doc, "authority": authority, "layout": layout,
                     "alignment": alignment, "handoff": handoff,
                     "ts4": snap, "verified": verified, "final": final})
    except Exception as error:  # noqa: BLE001
        _CTX["error"] = f"{type(error).__name__}: {error}"
    return _CTX


def _roots():
    if "roots" not in _CTX:
        _CTX["roots"] = FV._Roots(
            _CTX["verified"], TC.load_table_profile_bundle(),
            TG.extract_table_geometry(_CTX["handoff"].layout_capability))
    return _CTX["roots"]


def _upstream_values(roots=None) -> dict:
    roots = _roots() if roots is None else roots
    return {name: getter(roots)
            for name, getter in FV.UPSTREAM_FIELD_SOURCES.items()}


def _gate(ctx, msg="真实链路必须成立"):
    if ctx.get("error"):
        check(False, f"{msg} —— 现场链路失败：{ctx['error']}")
        return False
    if "final" not in ctx:
        check(False, f"{msg} —— 现场链路未产出 TS5 快照")
        return False
    return True


# ---------------------------------------------------------------------------
# S1 §19.12.1-1：十类线格式往返
# ---------------------------------------------------------------------------

def _sample_objects(final) -> dict:
    table = final.tables[0]
    return {
        "TableObjectV4": table,
        "TableCellV4": table.rows[0].cells[0],
        "FinalOutlineSpan": final.final_spans[0],
        "TableRangeDecision": final.decisions[0],
        "TableRelation": final.relations[0],
        "FinalComponentBinding": final.bindings[0],
        "TableCitableCoverage": final.coverages[0],
        "TableStructureGap": final.gaps,
        "FinalMaterialConservation": final.conservation,
        "FinalMaterialStructureSnapshot": final,
        "FinalNavigationSynopsis": final.synopses[0],
    }


def _test_s1(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]
    for name, seq in (("tables", final.tables), ("final_spans", final.final_spans),
                      ("decisions", final.decisions),
                      ("relations", final.relations),
                      ("bindings", final.bindings),
                      ("coverages", final.coverages)):
        check(len(seq) >= 2,
              f"S1 现场 {name} 至少两条（本组需要顺序 / 重复反例）：{len(seq)}")
    samples = _sample_objects(final)
    check(tuple(samples) == tuple(V.TABLE_RECORD_PUBLIC_TYPES),
          "S1 样本覆盖的十类与 `V.TABLE_RECORD_PUBLIC_TYPES` 同序同集")
    check(len(final.tables) == final.table_count and
          len(final.final_spans) == final.final_span_count and
          len(final.bindings) == final.component_count,
          f"S1 快照计数字段与实测一致（tables={len(final.tables)} "
          f"spans={len(final.final_spans)} bindings={len(final.bindings)}）")

    for name in V.TABLE_RECORD_PUBLIC_TYPES:
        obj = samples[name]
        cls = type(obj)
        check(cls.__name__ == name,
              f"S1 {name} 样本类型正确（得到 {cls.__name__}）")
        blob = obj.to_dict()
        check(blob.get("schema_type") == name,
              f"S1 {name}.to_dict 带 schema_type={name!r}")
        declared = {f.name for f in dataclasses.fields(obj)} | {"schema_type"}
        aliases = _WIRE_RENAMES.get(name, {})
        expected_wire = ((declared - set(aliases)) | set(aliases.values())
                         | _WIRE_EXTRA_KEYS.get(name, set()))
        check(set(blob) == expected_wire,
              f"S1 {name} 线格式字段集精确等于 dataclass 字段集（按登记别名 "
              f"{aliases} 与附加键 {sorted(_WIRE_EXTRA_KEYS.get(name, set()))} "
              f"归一；多 {sorted(set(blob) - expected_wire)} / 少 "
              f"{sorted(expected_wire - set(blob))}）")
        field, constant = V.TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS[name]
        check(blob[field] == V.VERSION_CONSTANTS[constant],
              f"S1 {name}.{field} 钉死为 {V.VERSION_CONSTANTS[constant]!r}")
        read_back = cls.from_dict(blob)
        check(read_back == obj,
              f"S1 {name} from_dict 往返后 dataclass 逐字段相等")
        check(canonical_json(read_back.to_dict()) == canonical_json(blob),
              f"S1 {name} 往返后 canonical JSON 逐字节相同")
        check(cls.from_dict(json.loads(json.dumps(blob))) == obj,
              f"S1 {name} 经 JSON 文本往返仍逐字段相等")
        if name in _DERIVED:
            rebuilt = _recreate(obj)
            check(canonical_json(rebuilt.to_dict()) == canonical_json(blob),
                  f"S1 {name} 由非派生字段 `create()` 重建后逐字节相同")
            check(TS.TABLE_RECORD_TYPES[name] is cls,
                  f"S1 {name} 的 wire 登记类型与类本身一致")

    table = samples["TableObjectV4"]
    blob = table.to_dict()
    check(all(set(r) >= {"row_index", "role", "cells"} for r in blob["rows"]),
          "S1 to-4 线格式内嵌 row 结构完整")
    cells = [c for r in blob["rows"] for c in r["cells"]]
    check(bool(cells) and all(set(c) >= {"row", "column", "rowspan", "colspan",
                                         "bbox", "text", "blocks", "source_refs"}
                              for c in cells),
          "S1 to-4 线格式内嵌 cell 结构完整")
    cell_refs = [x for c in cells for x in c["source_refs"]]
    table_refs = list(blob["source_refs"])
    check(bool(cell_refs) and bool(table_refs) and
          all(set(x) >= {"source_ref_id", "interval", "component_id", "citable",
                         "verdict", "citable_reason"} for x in cell_refs),
          "S1 to-4 线格式内嵌 cell source ref 结构完整（含 interval）")
    check(all(set(x) >= {"source_ref_id", "interval", "component_id", "citable"}
              for x in table_refs),
          "S1 to-4 表格级 source ref 结构完整")
    blocks = [b for c in cells for b in c["blocks"]]
    check(bool(blocks) and all({"block_index", "role", "text", "source_ref_ids"}
                               <= set(b) for b in blocks),
          "S1 to-4 线格式内嵌 cell block 结构完整")


# ---------------------------------------------------------------------------
# S2 §19.12.1-2：严格拒绝
# ---------------------------------------------------------------------------

def _test_s2(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]
    table = final.tables[0]
    base = table.to_dict()

    mutated = dict(base)
    mutated["table_object_id"] = "harness-table-1"
    raises(lambda: TS.TableObjectV4.from_dict(mutated), SchemaValidationError,
           "含未知字段", "S2 未知字段（旧 harness 身份字段名）被拒绝")

    missing = dict(base)
    missing.pop("table_id")
    raises(lambda: TS.TableObjectV4.from_dict(missing), SchemaValidationError,
           "缺必填字段: table_id", "S2 缺字段被拒绝")

    wrong_enum = dict(base)
    wrong_enum["structure_kind"] = "grid"
    raises(lambda: TS.TableObjectV4.from_dict(wrong_enum), SchemaValidationError,
           "structure_kind 必须属于", "S2 错误 enum（structure_kind）被拒绝")

    wrong_state = dict(base)
    wrong_state["structure_state"] = "ready"
    raises(lambda: TS.TableObjectV4.from_dict(wrong_state),
           SchemaValidationError, "structure_state 必须属于",
           "S2 错误 enum（structure_state）被拒绝")

    reordered = dict(base)
    reordered["rows"] = list(reversed(base["rows"]))
    raises(lambda: TS.TableObjectV4.from_dict(reordered), SchemaValidationError,
           "row_index 必须等于其序号", "S2 成员顺序错误（rows 逆序）被拒绝")

    dup = dict(base)
    dup["source_refs"] = list(base["source_refs"]) + [base["source_refs"][-1]]
    raises(lambda: TS.TableObjectV4.from_dict(dup), SchemaValidationError,
           "不得重复", "S2 重复成员（表格级 source_ref_id 末位重复）被拒绝")

    bad_hash = dict(base)
    bad_hash["content_fingerprint"] = "0" * 64
    raises(lambda: TS.TableObjectV4.from_dict(bad_hash), SchemaValidationError,
           "table_id 与派生身份不一致",
           "S2 非法 hash（content_fingerprint 漂移）被拒绝：内容指纹本身进入"
           "身份载荷，因此身份闸先于指纹闸拦下")
    raises(lambda: TS._check_fingerprint("TableObjectV4", "content_fingerprint",
                                         "0" * 64, table.content_payload()),
           SchemaValidationError, "与载荷重算不一致",
           "S2 逐字段指纹闸独立复算：指纹与自载载荷不符即拒绝")
    check({"content_fingerprint", "structure_fingerprint",
           "provenance_fingerprint"} <= set(table.identity_payload()),
          "S2 三种指纹都进入 to-4 身份载荷（改内容必改身份）")
    check(not ({"content_fingerprint", "structure_fingerprint",
                "provenance_fingerprint"} & set(table.locator_payload())),
          "S2 locator 载荷不含任何指纹（内容修订不得改变定位身份）")

    short_hash = dict(base)
    short_hash["provenance_fingerprint"] = "abc"
    raises(lambda: TS.TableObjectV4.from_dict(short_hash), SchemaValidationError,
           "provenance_fingerprint", "S2 形状非法的 hash 被拒绝")

    identity_drift = dict(base)
    identity_drift["table_id"] = TS.identity("to4", {"not": "the table"})
    raises(lambda: TS.TableObjectV4.from_dict(identity_drift),
           SchemaValidationError, "table_id 与派生身份不一致",
           "S2 table_id 与身份载荷重算不一致被拒绝")

    locator_drift = dict(base)
    locator_drift["table_locator"] = TS.locator("to4", {"not": "the table"})
    raises(lambda: TS.TableObjectV4.from_dict(locator_drift),
           SchemaValidationError, "table_locator 与派生定位不一致",
           "S2 table_locator 与定位载荷重算不一致被拒绝")

    cross = final.decisions[0].to_dict()
    raises(lambda: TS.TableObjectV4.from_dict(cross), SchemaValidationError,
           "schema_type 必须为", "S2 跨类型字典混淆（decision → to-4）被拒绝")
    cross2 = final.relations[0].to_dict()
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(cross2),
           SchemaValidationError, "schema_type 必须为",
           "S2 跨类型字典混淆（relation → final snapshot）被拒绝")

    check(table.table_id == TS.identity("to4", table.identity_payload()),
          "S2 to-4 `table_id` 独立复算等于身份载荷的 identity")
    check(table.table_locator == TS.locator("to4", table.locator_payload()),
          "S2 to-4 `table_locator` 独立复算等于定位载荷的 locator")

    # 同一批反例在 final snapshot 上也必须成立
    fbase = final.to_dict()
    fdup = dict(fbase)
    fdup["coverages"] = list(fbase["coverages"]) + [fbase["coverages"][-1]]
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(fdup),
           SchemaValidationError, "coverages 不得重复",
           "S2 final snapshot 重复成员（末位重复以避开排序检查）被拒绝")
    forder = dict(fbase)
    forder["relations"] = list(reversed(fbase["relations"]))
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(forder),
           SchemaValidationError, "relations 必须按固定顺序排列",
           "S2 final snapshot 成员顺序错误被拒绝")
    fmiss = dict(fbase)
    fmiss.pop("conservation")
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(fmiss),
           SchemaValidationError, "conservation 必须为对象",
           "S2 final snapshot 缺 conservation 被拒绝")
    fcount = dict(fbase)
    fcount["table_count"] = fbase["table_count"] + 1
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(fcount),
           SchemaValidationError, "table_count 必须等于 tables 长度",
           "S2 final snapshot 计数字段与实测不符被拒绝")
    fcomponent = dict(fbase)
    fcomponent["component_count"] = fbase["component_count"] - 1
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(fcomponent),
           SchemaValidationError, "component_count 必须等于 bindings 长度",
           "S2 final snapshot component 计数不得与 bindings 不等")
    fmix = dict(fbase)
    fmix["upstream_dependency_fingerprint"] = "b" * 64
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(fmix),
           SchemaValidationError, "不得混合两次构建的成员",
           "S2 final snapshot 与成员的上游依赖束必须同一（不得混构建）")
    fupstream = dict(fbase)
    fupstream["upstream_dependency_fingerprint"] = "not-a-sha256"
    raises(lambda: TS.FinalMaterialStructureSnapshot.from_dict(fupstream),
           SchemaValidationError, "upstream_dependency_fingerprint",
           "S2 final snapshot 上游束指纹形状非法被拒绝")


# ---------------------------------------------------------------------------
# S3 §19.12.1-3/8：历史版本与旧身份
# ---------------------------------------------------------------------------

def _test_s3(ctx):
    if not _gate(ctx):
        return
    table = ctx["final"].tables[0]
    base = table.to_dict()

    check(V.classify_schema_version("TABLE_SCHEMA_VERSION",
                                    V.LEGACY_TABLE_SCHEMA_VERSION) == "legacy",
          f"S3 `{V.LEGACY_TABLE_SCHEMA_VERSION}` 被登记为 legacy（须显式迁移）")
    check(V.classify_schema_version("TABLE_SCHEMA_VERSION", "to-9") == "unknown",
          "S3 未登记的 to-9 被登记为 unknown")
    check(V.classify_schema_version("TABLE_SCHEMA_VERSION",
                                    V.TABLE_SCHEMA_VERSION) == "current",
          f"S3 current 为 {V.TABLE_SCHEMA_VERSION!r}")
    check(set(V.TABLE_RECORD_PUBLIC_TYPES) == set(TS.TABLE_RECORD_TYPES),
          "S3 current 公开类型表与 wire 层登记表同集（历史 TableObject 不在其中）")
    check("TableObject" not in V.TABLE_RECORD_PUBLIC_TYPES and
          "TableObject" not in TS.TABLE_RECORD_TYPES and
          "TableObject" not in V.RUNTIME_ONLY_TYPE_NAMES,
          "S3 历史 `TableObject` 不是 current 记录类型（两条路径互不引用）")

    legacy = dict(base)
    legacy["schema_version"] = V.LEGACY_TABLE_SCHEMA_VERSION
    raises(lambda: TS.TableObjectV4.from_dict(legacy), SchemaValidationError,
           "旧 wire format 版本", "S3 to-3 载荷不得被静默解释成 to-4")

    unknown = dict(base)
    unknown["schema_version"] = "to-9"
    raises(lambda: TS.TableObjectV4.from_dict(unknown), SchemaValidationError,
           "未知版本", "S3 未知 wire 版本被拒绝")

    legacy_builder = dict(base)
    legacy_builder["table_builder_version"] = V.LEGACY_TABLE_BUILDER_VERSION
    raises(lambda: TS.TableObjectV4.from_dict(legacy_builder),
           SchemaValidationError, "table_builder_version 必须为当前版本",
           f"S3 `{V.LEGACY_TABLE_BUILDER_VERSION}` builder 版本不得冒充 current")

    legacy_cell = dict(base)
    legacy_cell["cell_schema_version"] = V.LEGACY_TABLE_CELL_SCHEMA_VERSION
    raises(lambda: TS.TableObjectV4.from_dict(legacy_cell),
           SchemaValidationError, "cell_schema_version 必须为当前版本",
           f"S3 `{V.LEGACY_TABLE_CELL_SCHEMA_VERSION}` cell 版本不得冒充 current")

    bogus_table = dict(base)
    bogus_table["table_id"] = "harness.table_structure.table_object_id|legacy-1"
    raises(lambda: TS.TableObjectV4.from_dict(bogus_table),
           SchemaValidationError, "table_id 与派生身份不一致",
           "S3 旧 harness 身份形状不得冒充 to-4 identity")

    check(base["table_locator"].startswith("loc-to4-") and
          base["table_id"].startswith("to4-"),
          "S3 to-4 locator/id 命名空间与历史 harness 身份不共享前缀")
    check(V.LEGACY_TABLE_SCHEMA_VERSION not in V.TABLE_RECORD_PUBLIC_TYPES and
          V.TABLE_SCHEMA_VERSION != V.LEGACY_TABLE_SCHEMA_VERSION,
          "S3 legacy 与 current wire 版本是两个不同常量")


# ---------------------------------------------------------------------------
# S4 §19.12.1-4/5：正式入口与能力对象
# ---------------------------------------------------------------------------

def _fake_verified_span():
    """与真实 wrapper **同名**但不经签发路径的对象（字段一条都没有）。"""
    return type("VerifiedSpanSnapshot", (object,), {})()


def _issue_testing_capability(snapshot, verified):
    """用正式签发器把一个 `testing` 域的 final wrapper 登记为能力对象。

    本模块验证的是 **wrapper 的运行时行为**（不可序列化 / 签发域隔离 / 消费入口），
    因此不重跑一遍真实复核；登记表按对象身份判定，伪造副本一律无效。
    """
    wrapper = FV.VerifiedFinalMaterialStructureSnapshot(
        snapshot=snapshot, ts4=verified, scope="testing",
        source_kind=verified.source_kind,
        issuer_version=V.VERIFIED_FINAL_MATERIAL_ISSUER_VERSION,
        verification_fingerprint="a" * 64, problems=())
    return SS._issue_capability(wrapper, FV.CAPABILITY_KIND, "testing")


def _test_s4(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]
    verified = ctx["verified"]

    raises(lambda: FMB.build_final_material_snapshot(verified.snapshot),
           SchemaValidationError, "正式输入必须是 VerifiedSpanSnapshot",
           "S4 raw `SpanBuildSnapshot` 不得进入正式构建入口")
    raises(lambda: FMB.build_final_material_snapshot(verified.snapshot.to_dict()),
           SchemaValidationError, "正式输入必须是 VerifiedSpanSnapshot",
           "S4 raw JSON（dict）不得进入正式构建入口")
    raises(lambda: FMB.build_final_material_snapshot(
        type("VerifiedSpanSnapshot", (object,), {})()),
        SchemaValidationError, "不是本进程由正式签发路径产生的实例",
        "S4 同名伪 wrapper 不得进入正式构建入口（签发登记表拦下）")
    raises(lambda: SS.issued_capability(_fake_verified_span(),
                                        "VerifiedSpanSnapshot"),
           SchemaValidationError, "不是本进程由正式签发路径产生的实例",
           "S4 同名伪 wrapper 不取得任何 TS5 输入资格")

    original_min = V.SPAN_CONFIDENCE_MIN
    try:
        V.SPAN_CONFIDENCE_MIN = None
        raises(lambda: FMB.build_final_material_snapshot(verified),
               SchemaValidationError, "distribution_only",
               "S4 缺少裁决阈值时正式入口必须 fail-closed（不得降级放行）")
    finally:
        V.SPAN_CONFIDENCE_MIN = original_min
    check(V.SPAN_CONFIDENCE_MIN is not None and
          FMB.build_final_material_snapshot(verified).snapshot_id ==
          final.snapshot_id,
          "S4 阈值恢复后同一正式输入仍可构建（拒绝来自阈值而非输入本身）")

    # final capability：同形不取得资格
    wrapper = FV.VerifiedFinalMaterialStructureSnapshot(
        snapshot=final, ts4=verified, scope="testing", source_kind="live",
        issuer_version=V.VERIFIED_FINAL_MATERIAL_ISSUER_VERSION,
        verification_fingerprint="0" * 64, problems=())
    raises(lambda: FV.assert_final_material_capability(wrapper),
           SchemaValidationError, "不是本进程由正式签发路径产生的实例",
           "S4 未签发的同形 final wrapper 不取得 TS6 消费资格")
    raises(lambda: FV.assert_final_material_capability(final),
           SchemaValidationError, "不是本进程由正式签发路径产生的实例",
           "S4 raw final snapshot 不取得 TS6 消费资格")
    raises(lambda: FV.assert_final_material_capability(final.to_dict()),
           SchemaValidationError, "不是本进程由正式签发路径产生的实例",
           "S4 raw final JSON 不取得 TS6 消费资格")
    raises(lambda: FV.assert_final_material_capability(verified.snapshot),
           SchemaValidationError, "不是本进程由正式签发路径产生的实例",
           "S4 raw TS4 快照不取得 TS6 消费资格")

    issued = _issue_testing_capability(final, verified)
    check(FV.assert_final_material_capability(issued) is issued,
          "S4 经正式签发器登记的能力对象可被 TS6 入口接受")
    raises(lambda: FV.assert_final_material_capability(
        issued, ("live", "pinned_acceptance")),
        SchemaValidationError, "不在允许集合",
        "S4 testing 域能力不得冒充 live / pinned_acceptance")
    check(SS.capability_scope(issued) == "testing",
          "S4 `capability_scope` 如实报告签发域（只读诊断）")
    check(SS.issued_capability(issued, FV.CAPABILITY_KIND) is issued,
          "S4 签发登记按对象身份可回查")

    raises(lambda: issued.to_dict(), FV.FinalVerificationError,
           "不得序列化", "S4 final capability 不得序列化")
    raises(lambda: copy.copy(issued), FV.FinalVerificationError,
           "不可 copy", "S4 final capability 不得 copy")
    raises(lambda: copy.deepcopy(issued), FV.FinalVerificationError,
           "不可 deepcopy", "S4 final capability 不得 deepcopy")
    raises(lambda: pickle.dumps(issued), FV.FinalVerificationError,
           "不可 pickle", "S4 final capability 不得 pickle")
    raises(lambda: pickle.dumps({"v": issued}), FV.FinalVerificationError,
           "不可 pickle", "S4 final capability 嵌进容器同样不得 pickle")
    raises(lambda: issued.__reduce__(), FV.FinalVerificationError,
           "不可 pickle", "S4 final capability 的 reduce 协议被拒绝")

    check(len(FV.verifier_independence_problems()) == 0,
          f"S4 复核器独立性自证为空：{FV.verifier_independence_problems()}")
    wrapper_name = "VerifiedFinalMaterialStructureSnapshot"
    check(wrapper_name not in V.TABLE_RECORD_PUBLIC_TYPES and
          wrapper_name not in TS.TABLE_RECORD_TYPES and
          wrapper_name not in V.RUNTIME_ONLY_TYPE_NAMES,
          "S4 final wrapper 不是任何 wire 类型（wire 表恰为十个公开记录类型 + "
          f"三个 runtime-only：{V.RUNTIME_ONLY_TYPE_NAMES}）")
    check(SS.ISSUER_SCOPES == ("live", "pinned_acceptance", "testing"),
          f"S4 签发域封闭为三域：{SS.ISSUER_SCOPES}")
    check(FV.CAPABILITY_KIND in SS.CAPABILITY_KINDS and
          "VerifiedSpanSnapshot" in SS.CAPABILITY_KINDS,
          f"S4 final / TS4 能力种类都已登记：{SS.CAPABILITY_KINDS}")
    raises(lambda: SS._issue_capability(final, "NotARegisteredKind", "testing"),
           SchemaValidationError, "未登记的能力种类",
           "S4 未登记的能力种类在签发入口即被拒绝")
    raises(lambda: SS._issue_capability(final, FV.CAPABILITY_KIND, "prod"),
           SchemaValidationError, "未登记的签发域",
           "S4 未登记的签发域在签发入口即被拒绝")


# ---------------------------------------------------------------------------
# S5 §19.12.1-6/7：上游身份篡改与双构建决定性
# ---------------------------------------------------------------------------

def _test_s5(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]
    verified = ctx["verified"]
    roots = _roots()

    values = _upstream_values(roots)
    expected = TS.upstream_dependency_fingerprint(values)
    check(expected == final.upstream_dependency_fingerprint,
          "S5 上游依赖束指纹可由经验证 roots 逐字段独立复现")
    check(FV._recompute_upstream_fingerprint(roots) == expected,
          "S5 复核器的束指纹重算与 wire 层独立复算一致")
    check(set(FV.UPSTREAM_FIELD_SOURCES) == set(TS.UPSTREAM_DEPENDENCY_FIELDS),
          "S5 复核器的上游字段表与 wire 层精确同集")
    check(len(TS.UPSTREAM_DEPENDENCY_FIELDS) >= 30,
          f"S5 上游依赖束字段数（{len(TS.UPSTREAM_DEPENDENCY_FIELDS)}）"
          "覆盖 PDF/layout/outline/Evidence/alignment/policy/版本/settings")
    for required in ("document_id", "document_version", "evidence_set_version",
                     "page_layout_id", "outline_id", "alignment_id",
                     "verified_span_snapshot_id", "span_build_snapshot_schema_version",
                     "handoff_identity", "qualification_policy_key",
                     "qualification_policy_fingerprint", "table_geometry_version",
                     "geometry_settings_fingerprint",
                     "classification_profile_content_fingerprint",
                     "cell_block_profile_content_fingerprint",
                     "table_classification_profile_version",
                     "table_cell_block_profile_version"):
        check(required in values,
              f"S5 上游依赖束登记了 {required}（PDF/layout/outline/Evidence/"
              "alignment/policy/版本/settings 八类根读数）")
    insensitive = []
    for name in TS.UPSTREAM_DEPENDENCY_FIELDS:
        probe = dict(values)
        probe[name] = "SENSITIVITY-PROBE"
        if TS.upstream_dependency_fingerprint(probe) == expected:
            insensitive.append(name)
    check(insensitive == [],
          f"S5 束指纹对**每一个**上游身份字段敏感（不敏感字段：{insensitive}）")
    raises(lambda: TS.upstream_dependency_fingerprint(
        {k: v for k, v in values.items() if k != "page_layout_id"}),
        SchemaValidationError, "上游依赖束缺字段",
        "S5 少登记一项上游身份即换依赖束，必须在构造期拒绝")
    raises(lambda: TS.upstream_dependency_fingerprint({**values, "extra": 1}),
        SchemaValidationError, "上游依赖束含未登记字段",
        "S5 多登记一项上游身份同样被拒绝")

    # --- 篡改 1：layout 身份（快照 + 全部表 + 全部 final span 同步） ----------
    # "下游全部重算"的篡改**不会**改变自载的依赖束指纹（它仍由真实 roots 派生），
    # 因此这里被**逐字段根闸**拦下；"指纹造假"由篡改 4 单独触发。表与 final
    # span 都带 page_layout_id / outline_id，改动必须把带根身份的成员全部重算，
    # 否则连快照构造期都过不了（跨树拼接拒绝）。
    stray_layout = TS.identity("pl", {"not": "the real layout"})
    spans1 = [_recreate(s, page_layout_id=stray_layout)
              for s in final.final_spans]
    tampered = _recreate(
        final, page_layout_id=stray_layout,
        tables=_resort(_recreate(t, page_layout_id=stray_layout)
                       for t in final.tables),
        final_spans=_resort(spans1),
        synopses=_rebase_synopses(final.synopses, final.final_spans, spans1))
    check(tampered.snapshot_id != final.snapshot_id and
          tampered.tables[0].table_id != final.tables[0].table_id,
          "S5 篡改后下游 locator/id 确实全部重算（不再是原身份）")
    raises(lambda: FV.verify_final_material_snapshot(verified, tampered),
           FV.FinalVerificationError, "与经验证 roots 不一致",
           "S5 篡改 layout 身份、下游全部重算后仍被逐字段根闸拒绝")

    # --- 篡改 2：TS4 快照身份（快照 + 全部表同步） ----------------------------
    stray_ts4 = TS.identity("spn", {"not": "the real snapshot"})
    tampered2 = _recreate(
        final, verified_span_snapshot_id=stray_ts4,
        tables=_resort(_recreate(t, verified_span_snapshot_id=stray_ts4)
                       for t in final.tables))
    raises(lambda: FV.verify_final_material_snapshot(verified, tampered2),
           FV.FinalVerificationError, "与经验证 roots 不一致",
           "S5 篡改 TS4 快照身份、下游全部重算后仍被逐字段根闸拒绝")

    # --- 篡改 3：outline 身份（快照 + 全部表 + 全部 final span 同步） ---------
    stray_outline = TS.identity("do", {"not": "the real outline"})
    spans3 = [_recreate(s, outline_id=stray_outline)
              for s in final.final_spans]
    tampered3 = _recreate(
        final, outline_id=stray_outline,
        tables=_resort(_recreate(t, outline_id=stray_outline)
                       for t in final.tables),
        final_spans=_resort(spans3),
        synopses=_rebase_synopses(final.synopses, final.final_spans, spans3))
    raises(lambda: FV.verify_final_material_snapshot(verified, tampered3),
           FV.FinalVerificationError, "与经验证 roots 不一致",
           "S5 篡改 outline 身份、下游全部重算后仍被逐字段根闸拒绝")

    # --- 篡改 4：依赖束指纹本身（伪造"自洽"的假根） --------------------------
    # 快照的构造期**不**校验依赖束指纹能否由 roots 复现（那是复核者的职责），
    # 因此这里必须由复核者用 roots 独立重算后再比对。
    fake = "0" * 64
    tampered4 = _recreate_all(final, upstream_dependency_fingerprint=fake)
    check(tampered4.upstream_dependency_fingerprint == fake and
          tampered4.tables[0].upstream_dependency_fingerprint == fake and
          tampered4.decisions[0].upstream_dependency_fingerprint == fake and
          tampered4.snapshot_id != final.snapshot_id,
          "S5 伪造的依赖束指纹在快照内**完全自洽**（全部成员同步重算），"
          "只能由复核者识破")
    raises(lambda: FV.verify_final_material_snapshot(verified, tampered4),
           FV.FinalVerificationError, "不可由经验证的 roots 复现",
           "S5 依赖束指纹造假被复核者的 roots 复现闸拒绝")

    # --- 篡改 5：owner 指向别的标题树 / 不存在的节点（跨根） ------------------
    node_tables = [t for t in final.tables
                   if t.owner.owner_kind == "outline_node"]
    check(bool(node_tables),
          f"S5 现场存在 outline_node owner 的表（跨根反例前置）："
          f"{len(node_tables)} 张")
    if node_tables:
        table = node_tables[0]
        cross_owner = dataclasses.replace(
            table.owner, outline_locator="loc-do-0000000000000000")
        cross_snap = _recreate(
            final, tables=_resort(_recreate(t, owner=cross_owner)
                                  if t.table_locator == table.table_locator else t
                                  for t in final.tables))
        raises(lambda: FV.verify_final_material_snapshot(verified, cross_snap),
               FV.FinalVerificationError, "不属于本文档标题树",
               "S5 owner 跨根（指向非本文档 outline）被拒绝")

        orphan_owner = dataclasses.replace(
            table.owner, node_id="n-0000000000000000")
        orphan_snap = _recreate(
            final, tables=_resort(_recreate(t, owner=orphan_owner)
                                  if t.table_locator == table.table_locator else t
                                  for t in final.tables))
        raises(lambda: FV.verify_final_material_snapshot(verified, orphan_snap),
               FV.FinalVerificationError, "不在经验证的标题树中",
               "S5 owner 指向不在经验证标题树中的节点被拒绝")

    # --- 篡改 6：版本身份（wire 层与复核层双闸） -----------------------------
    raises(lambda: _recreate(
        final, builder_version=V.TS5_FINAL_SPAN_BUILDER_VERSION),
        SchemaValidationError, "builder_version 必须为当前版本",
        "S5 final snapshot 的 builder 版本被换成 span builder 版本即被拒绝")
    raises(lambda: _recreate(final, final_span_schema_version="fos-0"),
           SchemaValidationError, "final_span_schema_version 必须为当前版本",
           "S5 final span schema 版本被换在 wire 层即被拒绝")
    check(V.FINAL_MATERIAL_BUILDER_VERSION != V.TS5_FINAL_SPAN_BUILDER_VERSION and
          V.FINAL_MATERIAL_BUILDER_VERSION != V.SPAN_BUILDER_VERSION,
          "S5 final 构建者版本与上游构建者版本互不相同（不得混用）")

    # --- 双构建逐字节相同 ----------------------------------------------------
    again = FMB.build_final_material_snapshot(verified)
    check(again.snapshot_id == final.snapshot_id,
          "S5 同输入双构建的 snapshot id 相同")
    check(again.content_fingerprint == final.content_fingerprint,
          "S5 同输入双构建的 content fingerprint 相同")
    check(canonical_json(again.to_dict()) == canonical_json(final.to_dict()),
          "S5 同输入双构建的 canonical JSON 逐字节相同")
    check([t.table_locator for t in again.tables] ==
          [t.table_locator for t in final.tables],
          "S5 同输入双构建的表格顺序相同")
    check([b.binding_locator for b in again.bindings] ==
          [b.binding_locator for b in final.bindings],
          "S5 同输入双构建的 binding 顺序相同")
    check(len(again.conservation.layers) == len(final.conservation.layers) and
          all(a.to_dict() == b.to_dict()
              for a, b in zip(again.conservation.layers,
                              final.conservation.layers)),
          "S5 同输入双构建的守恒四层逐项相同")
    check([t.owner.to_dict() for t in again.tables] ==
          [t.owner.to_dict() for t in final.tables],
          "S5 同输入双构建的 owner 归属逐表相同")


# ---------------------------------------------------------------------------
# S6 §19.12.1-9：身份 DAG
# ---------------------------------------------------------------------------

_DOWNSTREAM_NAMES = ("TableRelation", "TableRangeDecision",
                     "FinalComponentBinding", "TableCitableCoverage",
                     "FinalMaterialStructureSnapshot")

#: to-4 身份载荷里**不属于**定位载荷的那 12 个键（版本 / 依赖束 / 三类指纹）。
_EXPECT_ID_ONLY = {
    "table_locator", "schema_version", "table_builder_version",
    "cell_schema_version", "geometry_settings_fingerprint",
    "evidence_set_version", "outline_id", "verified_span_snapshot_id",
    "upstream_dependency_fingerprint", "content_fingerprint",
    "structure_fingerprint", "provenance_fingerprint"}


def _test_s6(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]

    check(TS.identity_dag_problems() == [],
          f"S6 身份 DAG 自检无问题：{TS.identity_dag_problems()}")
    try:
        TS.assert_identity_dag_acyclic()
        check(True, "S6 `assert_identity_dag_acyclic` 通过")
    except Exception as exc:  # noqa: BLE001
        check(False, f"S6 `assert_identity_dag_acyclic` 抛出：{exc}")

    order = TS.identity_dag_topological_order()
    edges = TS.IDENTITY_DAG_EDGES
    check(set(order) == set(TS.IDENTITY_DAG_NODES) and
          len(order) == len(TS.IDENTITY_DAG_NODES),
          "S6 拓扑序覆盖全部 DAG 节点且不重不漏")
    check(order[-1] == "final_snapshot" and order[0] == "upstream_dependency",
          f"S6 拓扑序起点为上游束、终点为 final snapshot：{order}")
    bad = [(node, dep) for node in order for dep in edges.get(node, ())
           if order.index(dep) > order.index(node)]
    check(bad == [], f"S6 拓扑序按边独立复算成立（违反的边：{bad}）")
    check(all(dep in TS.IDENTITY_DAG_NODES
              for deps in edges.values() for dep in deps),
          "S6 DAG 边只引用已登记节点")
    check(set(edges["table_object"]) == {"upstream_dependency"},
          f"S6 table_object 只依赖上游束：{edges['table_object']}")
    check(set(edges["final_snapshot"]) == set(TS.IDENTITY_DAG_NODES) -
          {"final_snapshot"},
          "S6 final_snapshot 依赖其余全部节点（终端节点）")
    check(set(edges["upstream_dependency"]) == set(),
          "S6 上游束是唯一的根（不依赖任何下游）")

    table_fields = {f.name for f in dataclasses.fields(TS.TableObjectV4)}
    downstream = set()
    for name in _DOWNSTREAM_NAMES:
        for field in dataclasses.fields(getattr(TS, name)):
            if field.name.endswith("_id") or field.name.endswith("_locator"):
                downstream.add(field.name)
    # document_id / page_layout_id / outline_id / verified_span_snapshot_id 同时
    # 出现在下游类型的字段表里，但它们由**上游**派生（见 UPSTREAM_DEPENDENCY_FIELDS），
    # 不属于"表回指下游身份"，必须先扣除再判泄漏。
    downstream_ids = downstream - set(TS.UPSTREAM_DEPENDENCY_FIELDS)
    leaked = sorted(table_fields &
                    (downstream_ids - {"table_id", "table_locator"}))
    check(leaked == [],
          f"S6 TableObject 不得含下游 ID/locator 字段（泄漏：{leaked}）")
    identity_keys = set(final.tables[0].identity_payload())
    locator_keys = set(final.tables[0].locator_payload())
    check(identity_keys <= table_fields and locator_keys <= table_fields,
          f"S6 to-4 身份/定位载荷只由本类型字段构成（越界键："
          f"{sorted((identity_keys | locator_keys) - table_fields)}）")
    check(identity_keys.isdisjoint(downstream_ids - {"table_locator"}) and
          locator_keys.isdisjoint(downstream_ids - {"table_id", "table_locator"}),
          "S6 to-4 身份/定位载荷不引用任何下游身份字段（改下游不改 table id）")
    check(len(identity_keys) == 16 and len(locator_keys) == 7 and
          identity_keys - locator_keys == _EXPECT_ID_ONLY,
          f"S6 身份载荷 = 定位载荷(7) + 版本/依赖/三指纹(12)："
          f"越界 {sorted((identity_keys - locator_keys) - _EXPECT_ID_ONLY)} / "
          f"缺项 {sorted(_EXPECT_ID_ONLY - (identity_keys - locator_keys))}")
    check(identity_keys & locator_keys == {
              "document_id", "document_version", "page_layout_id",
              "geometry_version"},
          "S6 身份与定位载荷的交集恰为四个上游物理标识"
          "（profile/classification 变化不得改变 locator）")

    # final snapshot 的内容指纹必须覆盖**全部**成员的 id（含 relation）
    payload = final._content_payload()
    check(set(payload) == {"upstream_dependency_fingerprint", "member_ids",
                           "final_versions"},
          f"S6 final 内容载荷分区固定：{sorted(payload)}")
    check(TS.sha256_canonical(payload) == final.content_fingerprint,
          "S6 final 内容指纹独立复算等于自载值")
    ids = payload["member_ids"]
    check(ids == sorted(ids), "S6 member_ids 按 canonical 排序（跨机稳定）")
    check(len(set(ids)) == len(ids), "S6 member_ids 不得重复")
    check(final.tables[0].table_id in ids and
          final.relations[0].relation_id in ids and
          final.final_spans[0].span_id in ids and
          final.decisions[0].decision_id in ids and
          final.bindings[0].binding_id in ids and
          final.coverages[0].coverage_id in ids and
          final.gaps.gap_id in ids and
          final.conservation.conservation_id in ids,
          "S6 member_ids 覆盖全部六类成员 + gap + conservation（Relation→FinalSnapshot）")
    # 方案 C：final 简介是 fms-1 的**成员**，必须进入成员身份与内容指纹。
    check(final.synopses[0].synopsis_id in ids,
          "S6 member_ids 必须纳入 final synopsis（TS5 独立类型也是快照成员）")
    dropped_syn = {**payload,
                   "member_ids": [x for x in ids
                                  if x != final.synopses[0].synopsis_id]}
    check(TS.sha256_canonical(dropped_syn) != final.content_fingerprint,
          "S6 摘掉一条 final synopsis id 即改变 final 内容指纹"
          "（Synopsis→FinalSnapshot）")
    check(final.synopses[0].synopsis_id ==
          TS.identity("fns", final.synopses[0].identity_payload()),
          "S6 final 简介 id 只由自身身份载荷派生（nss-3/ns-3 独立轴）")
    dropped = {**payload, "member_ids": [x for x in ids
                                         if x != final.relations[0].relation_id]}
    check(TS.sha256_canonical(dropped) != final.content_fingerprint,
          "S6 摘掉一条 relation id 即改变 final 内容指纹（单向传播）")
    dropped_tbl = {**payload,
                   "member_ids": [x for x in ids
                                  if x != final.tables[0].table_id]}
    check(TS.sha256_canonical(dropped_tbl) != final.content_fingerprint,
          "S6 摘掉一条 table id 即改变 final 内容指纹（Table→FinalSnapshot）")
    check(final.tables[0].table_id ==
          TS.identity("to4", final.tables[0].identity_payload()),
          "S6 表 id 只由自身身份载荷派生（与 relation / 快照无关）")

    forbidden = {final.snapshot_locator, final.snapshot_id,
                 final.content_fingerprint}
    leaks = []
    for member in (*final.tables, *final.final_spans, *final.decisions,
                   *final.relations, *final.bindings, *final.coverages):
        blob = canonical_json(member.to_dict())
        for token in forbidden:
            if token and token in blob:
                leaks.append((type(member).__name__, token))
    check(leaks == [],
          f"S6 成员不得反向引用 final snapshot 身份（回指：{leaks[:4]}）")
    FV._check_no_embedded_identity(final)
    check(True, "S6 复核器的回指闸对真实快照同样放行（两侧一致）")
    bundle_blob = canonical_json(sorted(str(v) for v in
                                        _upstream_values().values()))
    check(final.content_fingerprint not in bundle_blob and
          final.snapshot_id not in bundle_blob,
          "S6 final snapshot 身份不得出现在上游依赖束取值里")


# ---------------------------------------------------------------------------
# S7 §19.12.1-11：profile 注册表、分类与权威
# ---------------------------------------------------------------------------

def _copy_policies(dest: str) -> None:
    for name in os.listdir(_POLICIES):
        shutil.copy2(_POLICIES / name, os.path.join(dest, name))


def _tamper_registry(dest: str, mutate) -> None:
    path = os.path.join(dest, TC.PROFILE_REGISTRY_FILE)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    mutate(data)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def _clone_frozen(cls, source, **override):
    """绕过 `__post_init__` 复制一个 frozen dataclass（**只用于构造反例**）。

    profile 资产的正式构造路径禁止"同一组内两条模式给出不同 class"、也禁止同一
    order 上的重复规则，因此这两类冲突反例必须由这一层显式伪造；本组同时验证
    正式构造路径确实拒绝它们。
    """
    values = {f.name: getattr(source, f.name) for f in dataclasses.fields(cls)}
    values.update(override)
    clone = object.__new__(cls)
    for key, value in values.items():
        object.__setattr__(clone, key, value)
    return clone


def _conflicting_classification_profile(profile):
    """伪造一个"同一组内两条模式给出不同 class"的 profile（规则冲突反例）。"""
    first = profile.note_subtree_patterns[0]
    token = (first.tokens or first.any_tokens or first.all_tokens)[0]
    rival = _clone_frozen(TC.HeadingPattern, first, order=2,
                          pattern_key="conflict_probe",
                          structure_class="financial_main_statement")
    return _clone_frozen(
        TC.TableClassificationProfile, profile,
        note_subtree_patterns=(first, rival)), token


def _conflicting_cell_block_profile(profile):
    """伪造一个"同一 order 上两条规则同时成立"的 cell block profile。

    正式资产禁止同 order 重复（`rules` 必须恰为 5 条且 order 逐位固定），因此这里
    只能复制一条**已登记**规则（`line`）来制造运行期冲突——不能用未登记 rule_key，
    因为那会被 `CellBlockRule.__post_init__` 直接拒绝。
    """
    source = [r for r in profile.rules if r.rule_key == "line"][0]
    rival = _clone_frozen(TC.CellBlockRule, source)
    return _clone_frozen(TC.TableCellBlockProfile, profile,
                         rules=tuple(profile.rules) + (rival,))


def _test_s7(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]

    bundle = TC.load_table_profile_bundle()
    check(bundle.classification.profile_version ==
          V.TABLE_CLASSIFICATION_PROFILE_VERSION and
          bundle.cell_block.profile_version ==
          V.TABLE_CELL_BLOCK_PROFILE_VERSION,
          "S7 两份 profile 版本为 current（tcp-1 / tcbp-1）")
    check(bundle.registry_version == TC.TABLE_PROFILE_REGISTRY_VERSION and
          bundle.registry_file_sha256 not in (bundle.classification_file_sha256,
                                              bundle.cell_block_file_sha256),
          "S7 注册表版本 current 且注册表身份与两份 profile 身份彼此独立")
    check(TC.self_check()["problems"] == [],
          f"S7 只读资产机械自检无问题：{TC.self_check()['problems']}")

    profile = bundle.classification
    block_profile = bundle.cell_block

    check(len(TS.TABLES_STRUCTURE_CLASSES) == 4 and
          set(TS.TABLES_STRUCTURE_CLASSES) ==
          {"financial_main_statement", "note_table",
           "ordinary_business_table", "unclassified"},
          f"S7 structure class 恰四类：{TS.TABLES_STRUCTURE_CLASSES}")
    check(len(TS.CELL_BLOCK_ROLES) == 5 and
          set(TS.CELL_BLOCK_ROLES) == set(TC.CELL_BLOCK_RULE_KEYS),
          f"S7 cell block role 恰五类且与规则键封闭表一致：{TS.CELL_BLOCK_ROLES}")
    check({r.role for r in block_profile.rules} == set(TS.CELL_BLOCK_ROLES),
          "S7 五类 role 都能被 profile 规则产出")
    check(all(r.structure_class in TS.TABLES_STRUCTURE_CLASSES
              for r in profile.priority),
          "S7 优先级表每个 structure_class 都在封闭四类内")
    check([r.when for r in profile.priority] ==
          list(TC.CLASSIFICATION_PRIORITY_WHEN_CODES),
          "S7 五行优先级真值表的 when 序列与登记表一致")
    check(profile.conflict_rule == "fail_closed_unclassified" and
          block_profile.conflict_rule == "fail_closed_unclassified" and
          block_profile.unknown_signal_rule == "unclassified",
          "S7 分类与 cell block 的冲突/未知信号规则都是 fail-closed")

    # 五行真值
    note_title = profile.note_subtree_patterns[0].tokens[0]
    main_title = profile.main_statement_patterns[0].tokens[0]
    truth = (
        (("unassigned_boundary", False, (), True, False), "unclassified",
         "owner_path_unavailable", "owner 未解析 → 行 1"),
        (("outline_node", True, (), True, False), "unclassified",
         "owner_path_unavailable", "owner_kind=outline_node 但路径不可用 → 行 1"),
        (("unassigned_boundary", True, (), False, False), "unclassified",
         "no_rule_matched", "已验证未归属边界但不在正文内 → 行 5"),
        (("outline_node", True, (note_title,), True, True), "note_table",
         "ancestor_path_note_pattern", "祖先路径命中附注子树 → 行 2"),
        (("outline_node", True, ("其他", main_title), True, True),
         "financial_main_statement", "nearest_heading_main_statement_pattern",
         "最近标题命中主表模式 → 行 3"),
        (("outline_node", True, ("其他",), True, True),
         "ordinary_business_table", "inside_verified_body",
         "已验证正文内且无模式命中 → 行 4"),
        (("unassigned_boundary", True, (), True, False),
         "ordinary_business_table", "inside_verified_body",
         "已验证未归属边界（无路径可言）→ 行 4"),
        (("outline_node", True, ("其他",), False, True), "unclassified",
         "no_rule_matched", "无正文、无模式 → 行 5"),
    )
    for args, want_class, want_code, label in truth:
        got_class, code = TC.classify_structure_class(
            profile, TC.TableClassificationInputs(*args))
        check((got_class, code) == (want_class, want_code),
              f"S7 {label}（期望 {(want_class, want_code)}，得到 "
              f"{(got_class, code)}）")
        check(code in TC.STRUCTURE_CLASS_RATIONALE_CODES,
              f"S7 {label} 的理由码在封闭表内（{code!r}）")

    # 冲突 → unclassified/pattern_conflict（不得"取第一条"）
    conflicting, token = _conflicting_classification_profile(profile)
    raises(lambda: TC.TableClassificationProfile.from_dict(
        conflicting.to_dict()), SchemaValidationError,
        "structure_class 必须为 'note_table'",
        "S7 正式构造路径禁止同组模式给出不同 class（冲突反例必须显式伪造）")
    control, control_code = TC.classify_structure_class(
        profile, TC.TableClassificationInputs(
            "outline_node", True, (token,), True, True))
    check((control, control_code) == ("note_table", "ancestor_path_note_pattern"),
          f"S7 对照组：同一标题在正式 profile 下稳定判为 note_table"
          f"（得到 {(control, control_code)}）")
    got, code = TC.classify_structure_class(
        conflicting, TC.TableClassificationInputs(
            "outline_node", True, (token,), True, True))
    check((got, code) == ("unclassified", "pattern_conflict"),
          f"S7 同一行内多条模式给出不同 class → unclassified/pattern_conflict"
          f"（得到 {(got, code)}）")

    # 页码 / 表号 / 单位 / 公司名扰动不改变分类
    plain, _ = TC.classify_structure_class(
        profile, TC.TableClassificationInputs(
            "outline_node", True, ("其他", main_title), True, True))
    noisy, _ = TC.classify_structure_class(
        profile, TC.TableClassificationInputs(
            "outline_node", True, ("第 5 页", "表 3-1", "其他",
                                   "单位：元 " + main_title), True, True))
    other_co, other_code = TC.classify_structure_class(
        profile, TC.TableClassificationInputs(
            "outline_node", True, ("其他", "某公司股份有限公司" + main_title),
            True, True))
    check(plain == noisy == other_co == "financial_main_statement",
          f"S7 页码/表号/单位/公司名扰动不改变分类（{plain!r} / {noisy!r} / "
          f"{other_co!r} / {other_code!r}）")
    normalized = TC.normalize_heading_title(
        profile, "某公司股份有限公司" + main_title)
    check(main_title in normalized or normalized.endswith(main_title),
          f"S7 标题归一化后仍含模式 token（{normalized!r}）")

    # profile 资产：公司无关 + 禁止字面形状
    blob = canonical_json(profile.to_dict()) + canonical_json(
        block_profile.to_dict())
    for token in ("300750", "CATL", "宁德时代", "宁德"):
        check(token not in blob, f"S7 profile 资产不得含 {token!r}")
    raw = json.loads((_POLICIES / "table_classification_profile_v1.json")
                     .read_text(encoding="utf-8"))
    for pattern in raw["main_statement_patterns"]:
        key = "tokens" if pattern["kind"] != "contains_all_with_any" \
            else "any_tokens"
        poisoned = json.loads(json.dumps(raw))
        target = [p for p in poisoned["main_statement_patterns"]
                  if p["pattern_key"] == pattern["pattern_key"]][0]
        target[key] = ["300750"]
        raises(lambda p=poisoned: TC.TableClassificationProfile.from_dict(p),
               SchemaValidationError, "命中禁止字面形状",
               "S7 profile 模式 token 注入公司代码即被正式拒绝"
               f"（{pattern['pattern_key']}/{key}）")
        break
    for bad in ("300750", "CATL", "123456", "12345678"):
        poisoned = json.loads(json.dumps(raw))
        poisoned["note_subtree_patterns"][0]["tokens"] = [bad]
        raises(lambda p=poisoned: TC.TableClassificationProfile.from_dict(p),
               SchemaValidationError, "命中禁止字面形状",
               f"S7 附注模式 token 注入 {bad!r} 被正式拒绝（反硬编码门）")

    # 未归属边界 owner 的表必须 unclassified（构造期硬约束）。现场 14 张表全部是
    # outline_node owner，因此这里用**经验证终态里的** unassigned disposition
    # 合成一张未归属边界表（只读探针，不改动现场对象）。
    roots = _roots()
    unassigned = [d for d in roots.dispositions if d.range_kind == "unassigned"]
    check(bool(unassigned),
          f"S7 经验证终态中存在未归属 disposition（{len(unassigned)} 条）")
    boundary_tables = [t for t in final.tables
                       if t.owner.owner_kind == "unassigned_boundary"]
    check(all(t.structure_class == "unclassified" for t in boundary_tables),
          "S7 已验证 unassigned owner 的表全部为 unclassified")
    if unassigned:
        reference = final.tables[0].owner
        synthetic = TS.TableOwnerRef(
            owner_kind="unassigned_boundary",
            node_id=None,
            outline_locator=None,
            source_boundary_kind=reference.source_boundary_kind,
            source_boundary_locator=reference.source_boundary_locator,
            source_boundary_id=reference.source_boundary_id,
            source_boundary_start_page=reference.source_boundary_start_page,
            source_boundary_end_page=reference.source_boundary_end_page,
            unassigned_ref_kind="ts4_disposition",
            unassigned_ref_locator=unassigned[0].disposition_locator,
            unassigned_ref_id=unassigned[0].disposition_id)
        probe = _recreate(final.tables[0], owner=synthetic,
                          structure_class="unclassified")
        check(probe.owner.owner_kind == "unassigned_boundary" and
              probe.structure_class == "unclassified",
              "S7 未归属边界表可合法构造（owner 带已验证 disposition 引用）")
        for cls_name in ("ordinary_business_table", "note_table",
                         "financial_main_statement"):
            raises(lambda name=cls_name: _recreate(
                final.tables[0], owner=synthetic, structure_class=name),
                SchemaValidationError,
                "structure_class 必须为 'unclassified'",
                f"S7 未归属边界表不得凭表内文字猜成 {cls_name}")

    # cell block role 真值表
    def _signals(text="甲公司", line_group_count=1, own_line_group=False,
                 followed_by_other_block_in_same_cell=False, size_ratio=None,
                 bold_ratio=None, unclosed_inputs=False):
        return TC.evaluate_cell_block_signals(
            text=text, line_group_count=line_group_count,
            own_line_group=own_line_group,
            followed_by_other_block_in_same_cell=
            followed_by_other_block_in_same_cell,
            size_ratio=size_ratio, bold_ratio=bold_ratio,
            unclosed_inputs=unclosed_inputs, profile=block_profile)

    role_truth = (
        (_signals(text="甲公司主要会计数据", own_line_group=True,
                  followed_by_other_block_in_same_cell=True, size_ratio=1.6),
         "heading", "rule_heading", "行组独立 + 字号偏大 → heading"),
        (_signals(text="- 甲公司", own_line_group=False),
         "list_item", "rule_list_item", "以列表标记开头 → list_item"),
        (_signals(text="甲公司主营业务收入。", line_group_count=2),
         "paragraph", "rule_paragraph", "多行组 + 句末标点 → paragraph"),
        (_signals(text="甲公司", own_line_group=False),
         "line", "rule_line", "单行组无其它证据 → line"),
        (_signals(text="甲公司", line_group_count=0),
         "unclassified", "rule_unclassified", "无规则成立 → 兜底 unclassified"),
        (_signals(unclosed_inputs=True), "unclassified", "unclosed_inputs",
         "输入未闭合 → fail-closed"),
    )
    produced = set()
    for signals, want_role, want_code, label in role_truth:
        role, code = TC.classify_cell_block_role(block_profile, signals)
        produced.add(role)
        check((role, code) == (want_role, want_code),
              f"S7 cell block {label}（期望 {(want_role, want_code)}，得到 "
              f"{(role, code)}）")
        check(code in TC.CELL_BLOCK_ROLE_RATIONALE_CODES,
              f"S7 cell block {label} 的理由码在封闭表内（{code!r}）")
    check(produced == set(TS.CELL_BLOCK_ROLES),
          f"S7 现场探针可产出全部五类 role：{sorted(produced)}")
    conflicting_block = _conflicting_cell_block_profile(block_profile)
    raises(lambda: TC.TableCellBlockProfile.from_dict(
        conflicting_block.to_dict()), SchemaValidationError,
        "rules 必须恰为 5 条",
        "S7 正式构造路径禁止同 order 重复规则（冲突反例必须显式伪造）")
    role, code = TC.classify_cell_block_role(
        conflicting_block, _signals(text="甲公司", own_line_group=False))
    check((role, code) == ("unclassified", "rule_conflict"),
          f"S7 同一 order 多条规则成立 → unclassified/rule_conflict"
          f"（得到 {(role, code)}）")
    check(TC.classify_cell_block_role(
        block_profile, _signals(text="甲公司", own_line_group=False))[0] == "line",
        "S7 同一信号在正式 profile 下不冲突（冲突只来自伪造规则表）")

    # 任意 class / role 均不产生财务权威
    check(all(t.is_financial_authority() is False for t in final.tables),
          "S7 现场全部表的 `is_financial_authority()` 恒为 False")
    check(final.is_financial_authority() is False,
          "S7 final snapshot 的 `is_financial_authority()` 同样为 False")
    authority_probes = 0
    for cls_name in TS.TABLES_STRUCTURE_CLASSES:
        for kind in TS.STRUCTURE_KINDS:
            try:
                probe = _recreate(final.tables[0], structure_class=cls_name,
                                  structure_kind=kind)
            except SchemaValidationError:
                continue  # 组合本身不合法（如 headerless_grid 缺缺省原因）
            authority_probes += 1
            check(probe.is_financial_authority() is False,
                  f"S7 structure_class={cls_name!r} / structure_kind={kind!r} 时 "
                  "authority 仍为 False")
    check(authority_probes >= 8,
          f"S7 四类 structure_class × 四类 structure_kind 的权威探针充分"
          f"（{authority_probes} 组合法组合，全部 False）")

    # 真值表：current / 字节漂移 / 内容指纹漂移 / 版本与种类漂移
    base_dir = tempfile.mkdtemp(prefix="ts5_profile_drift_",
                                dir=str(_REPO / "evals"))
    try:
        _copy_policies(base_dir)
        check(TC.load_table_profile_bundle(profiles_dir=base_dir) is not None,
              "S7 对照组：逐字节复制后的注册表可正常加载")

        profile_path = Path(base_dir) / "table_classification_profile_v1.json"
        raw2 = json.loads(profile_path.read_text(encoding="utf-8"))
        raw2["conflict_rule"] = "fail_closed_unclassified "
        profile_path.write_text(json.dumps(raw2, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "的文件字节漂移",
               "S7 profile 文件字节漂移被拒绝（raw bytes SHA256 不匹配）")

        _copy_policies(base_dir)
        _tamper_registry(base_dir, lambda d: d["profiles"][
            "table-classification-v1"].__setitem__("profile_fingerprint",
                                                   "0" * 64))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "的内容指纹漂移",
               "S7 内容指纹漂移被拒绝（canonical 内容指纹不匹配）")

        _copy_policies(base_dir)
        _tamper_registry(base_dir,
                         lambda d: d.__setitem__("registry_version", "tpr-0"))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "registry_version 必须为",
               "S7 未知 registry_version 被拒绝")

        _copy_policies(base_dir)
        _tamper_registry(base_dir, lambda d: d["profiles"][
            "table-classification-v1"].__setitem__("profile_kind",
                                                   "unknown_kind"))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "profile_kind 必须属于",
               "S7 未知 profile_kind 被拒绝")

        _copy_policies(base_dir)
        _tamper_registry(base_dir,
                         lambda d: d.__setitem__("default_profile_key",
                                                 "table-cell-block-v1"))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "default_profile_key 必须指向",
               "S7 default_profile_key 指向非 classification 条目被拒绝")

        _copy_policies(base_dir)
        _tamper_registry(base_dir, lambda d: d["profiles"][
            "table-classification-v1"].__setitem__(
            "file", os.path.join("..", "x.json")))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "必须为目录内文件名",
               "S7 注册表指向目录外文件被拒绝")

        _copy_policies(base_dir)
        _tamper_registry(base_dir,
                         lambda d: d.__setitem__("schema_type",
                                                 "OtherRegistry"))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "schema_type 必须为",
               "S7 注册表 schema_type 不符被拒绝")

        _copy_policies(base_dir)
        os.remove(os.path.join(base_dir, "table_cell_block_profile_v1.json"))
        raises(lambda: TC.load_table_profile_bundle(profiles_dir=base_dir),
               SchemaValidationError, "只读资产不存在",
               "S7 缺失 profile 资产被拒绝")
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# S8 §19.12.1-12：TableOwnerRef
# ---------------------------------------------------------------------------

def _owner_payload(owner, **override) -> dict:
    """按线格式伪造一个 owner（保留 schema_type，只替换被指定的字段）。"""
    payload = owner.to_dict()
    payload.update(override)
    return payload


def _wire_ref(ref, **override) -> dict:
    """按 `TableCellSourceRef._identity_payload()` 的形状重算身份后再伪造。"""
    payload = ref.to_dict()
    payload.pop("schema_type", None)
    payload.pop("source_ref_id", None)
    payload.update(override)
    payload["source_ref_id"] = TS.identity("tcsr", payload)
    payload["schema_type"] = "TableCellSourceRef"
    return payload


def _test_s8(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]
    roots = _roots()

    node_owners = [t.owner for t in final.tables
                   if t.owner.owner_kind == "outline_node"]
    unassigned_owners = [t.owner for t in final.tables
                         if t.owner.owner_kind == "unassigned_boundary"]
    check(bool(node_owners),
          f"S8 现场存在 outline_node owner 的表（node={len(node_owners)} "
          f"unassigned={len(unassigned_owners)}）")
    check(set(o.owner_kind for o in node_owners + unassigned_owners)
          <= set(TS.OWNER_KINDS),
          "S8 owner_kind 全部落在封闭两分支内")

    sample = node_owners[0]
    check(TS.TableOwnerRef.from_dict(sample.to_dict()) == sample,
          "S8 参考 owner 往返后逐字段相等")
    check(bool(sample.node_id) and bool(sample.outline_locator) and
          sample.unassigned_ref_kind is None and
          sample.unassigned_ref_locator is None and
          sample.unassigned_ref_id is None,
          "S8 outline_node 分支：node_id/locator 齐备且不携带 unassigned ref")

    # 未归属分支：现场 14 张表全为 outline_node owner，因此由**经验证终态里的**
    # unassigned disposition 合成一个未归属 owner（只读探针，不改动现场对象）。
    unassigned = [d for d in roots.dispositions if d.range_kind == "unassigned"]
    check(bool(unassigned),
          f"S8 经验证终态中存在未归属 disposition（{len(unassigned)} 条）")
    check(unassigned_owners == [],
          f"S8 现场未归属 owner 的表数（{len(unassigned_owners)}）"
          "——未归属分支由经验证 disposition 合成验证")
    boundary = TS.TableOwnerRef(
        owner_kind="unassigned_boundary",
        node_id=None,
        outline_locator=None,
        source_boundary_kind=sample.source_boundary_kind,
        source_boundary_locator=sample.source_boundary_locator,
        source_boundary_id=sample.source_boundary_id,
        source_boundary_start_page=sample.source_boundary_start_page,
        source_boundary_end_page=sample.source_boundary_end_page,
        unassigned_ref_kind="ts4_disposition",
        unassigned_ref_locator=unassigned[0].disposition_locator,
        unassigned_ref_id=unassigned[0].disposition_id)
    check(boundary.node_id is None and boundary.outline_locator is None,
          "S8 unassigned_boundary 分支的 node_id/outline_locator 必须为 None")
    check(boundary.unassigned_ref_kind in TS.UNASSIGNED_REF_KINDS and
          bool(boundary.unassigned_ref_locator) and
          bool(boundary.unassigned_ref_id),
          f"S8 unassigned_boundary 分支给出已登记的引用"
          f"（{boundary.unassigned_ref_kind!r}）")
    check(boundary.source_boundary_kind in TS.SOURCE_BOUNDARY_KINDS and
          boundary.source_boundary_end_page >=
          boundary.source_boundary_start_page >= 1,
          "S8 source boundary 种类已登记且页范围有序有效")

    # 对象级回查：owner 指向的 node 必须在**经验证**标题树中
    node_ids = {n.node_id for n in roots.outline.nodes}
    check(all(o.node_id in node_ids for o in node_owners),
          f"S8 全部 outline_node owner 的 node_id 都在经验证标题树中"
          f"（树内 {len(node_ids)} 个节点）")
    check(all(o.outline_locator == roots.outline.outline_locator
              for o in node_owners),
          "S8 全部 outline_node owner 属于本文档标题树")
    check(all(t.owner.source_boundary_end_page >= t.page_number >=
              t.owner.source_boundary_start_page for t in final.tables),
          "S8 每张表都落在其 owner 的 source boundary 页范围内")
    check(all(t.owner.source_boundary_locator and t.owner.source_boundary_id
              for t in final.tables),
          "S8 每张表的 owner 都带可回查的 boundary locator/id（不得自报字符串）")

    # 反例
    raises(lambda: TS.TableOwnerRef.from_dict(_owner_payload(sample,
                                                            node_id=None)),
           SchemaValidationError, "outline_node 分支必须给出 node_id",
           "S8 `node_id=None` 且无 unassigned ref 被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(sample, outline_locator=None)),
        SchemaValidationError, "outline_node 分支必须给出 outline_locator",
        "S8 outline_node 分支缺 outline_locator 被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(sample, unassigned_ref_kind="ts4_disposition",
                       unassigned_ref_locator="loc-x",
                       unassigned_ref_id="x")),
        SchemaValidationError, "outline_node 分支不得携带 unassigned_ref_kind",
        "S8 两分支同时存在被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(boundary, unassigned_ref_kind="not_registered")),
        SchemaValidationError, "unassigned_boundary 分支必须给出已登记的",
        "S8 unassigned_boundary 未登记引用种类被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(boundary, unassigned_ref_locator=None)),
        SchemaValidationError,
        "unassigned_boundary 分支必须给出 unassigned_ref_locator",
        "S8 unassigned_boundary 缺引用 locator 被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(boundary, node_id="n-999")),
        SchemaValidationError,
        "unassigned_boundary 分支的 node_id/outline_locator 必须为 None",
        "S8 unassigned_boundary 携带 node_id 被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(sample, source_boundary_kind="guessed")),
        SchemaValidationError, "source_boundary_kind 必须属于",
        "S8 未登记的 source boundary 种类被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(sample, source_boundary_start_page=9,
                       source_boundary_end_page=1)),
        SchemaValidationError, "source boundary 的结束页不得早于起始页",
        "S8 source boundary 页范围倒置被拒绝")
    raises(lambda: TS.TableOwnerRef.from_dict(
        _owner_payload(sample, owner_kind="orphan")),
        SchemaValidationError, "owner_kind 必须属于",
        "S8 orphan（第三种 owner_kind）被拒绝")

    # 表的物理页被移出 owner boundary：构造期即拒绝
    table = final.tables[0]
    end = table.owner.source_boundary_end_page
    raises(lambda: _recreate(table, page_number=end + 1),
           SchemaValidationError,
           "owner 的 source boundary 必须包含该表的物理页",
           "S8 表页不在 owner source boundary 内被拒绝")

    # 复核器一侧：owner 的**对象级**回查（不是自报字符串）
    def _table_problems(owner) -> str:
        snapshot = _recreate(final, tables=_resort(
            _recreate(t, owner=owner,
                      structure_class="unclassified"
                      if owner.owner_kind == "unassigned_boundary"
                      else t.structure_class)
            if t.table_locator == table.table_locator else t
            for t in final.tables))
        try:
            FV._check_tables(snapshot, roots)
        except FV.FinalVerificationError as error:
            return str(error)
        return ""

    check(_table_problems(boundary) == "",
          "S8 复核器接受以经验证 unassigned disposition 为 owner 的未归属表"
          f"（得到 {_table_problems(boundary)[:160]!r}）")
    stray_locator = "loc-dr-0000000000000000"
    stray_ref = dataclasses.replace(boundary,
                                    unassigned_ref_locator=stray_locator)
    problems = _table_problems(stray_ref)
    check("不在经验证的终态中" in problems,
          f"S8 复核器拒绝指向不存在未归属边界的 owner（得到 {problems[:160]!r}）")
    mismatch = dataclasses.replace(boundary, unassigned_ref_id="dr-0000000000000000")
    problems = _table_problems(mismatch)
    check("指的不是同一个对象" in problems,
          f"S8 复核器拒绝 locator 与 id 指向不同对象的 owner（得到 "
          f"{problems[:160]!r}）")
    stray_boundary = dataclasses.replace(boundary,
                                         source_boundary_locator=stray_locator)
    problems = _table_problems(stray_boundary)
    check("不是经验证的 TS4 disposition" in problems,
          f"S8 复核器拒绝不能回查的 source boundary（得到 {problems[:160]!r}）")


# ---------------------------------------------------------------------------
# S9 §19.12.2A-7 线格式半边：terminal 与 cell source ref
# ---------------------------------------------------------------------------

def _test_s9(ctx):
    if not _gate(ctx):
        return
    final = ctx["final"]
    roots = _roots()

    terminals = roots.terminals
    kinds: dict = {}
    for terminal in terminals:
        kinds[terminal.terminal_kind] = kinds.get(terminal.terminal_kind, 0) + 1
    check(len(terminals) > 0, f"S9 现场终态非空（{kinds}）")
    check(set(kinds) <= set(TS.TERMINAL_KINDS),
          f"S9 终态种类全部登记：{sorted(kinds)}")
    check(len(kinds) == 2 and kinds.get("refusal", 0) >= 1,
          f"S9 现场同时存在 alignment 与 refusal 两类终态（{kinds}）")
    check(kinds.get("alignment", 0) >= 1,
          f"S9 现场存在 alignment 终态（{kinds}）")

    fields = {f.name for f in dataclasses.fields(AlignmentTerminal)}
    check("alignment_id" not in fields,
          "S9 终态视图结构上**没有** alignment_id 字段（refusal 无从伪造对齐 ID）")
    check({"terminal_kind", "terminal_id", "terminal_locator",
           "terminal_schema_version", "verdict", "refusal_reason",
           "residue_class", "char_map", "residue"} <= fields,
          f"S9 终态字段齐备：{sorted(fields)}")
    identity_widths = {len(t.identity()) for t in terminals}
    check(identity_widths == {7},
          f"S9 全部终态的 identity 宽度固定（{identity_widths}）")
    check(all(t.terminal_kind in TS.TERMINAL_KINDS and t.terminal_id and
              t.terminal_locator and t.terminal_schema_version and
              t.aligner_version == V.ALIGNER_VERSION and
              t.partition_validator_version ==
              V.ALIGNMENT_PARTITION_VALIDATOR_VERSION
              for t in terminals),
          "S9 全部终态带版本化身份，且 aligner/partition 版本为 current")
    bad_pairs = [(t.terminal_kind, t.verdict, t.refusal_reason)
                 for t in terminals
                 if (t.terminal_kind == "alignment") !=
                 (t.verdict is not None and t.refusal_reason is None)]
    check(bad_pairs == [],
          f"S9 终态种类与其字段互相印证（反例 {bad_pairs[:2]}）")
    refusal_versions = {t.terminal_schema_version for t in terminals
                        if t.terminal_kind == "refusal"}
    align_versions = {t.terminal_schema_version for t in terminals
                      if t.terminal_kind == "alignment"}
    check(refusal_versions == {V.ALIGN_REFUSAL_SCHEMA_VERSION} and
          align_versions == {V.ALIGN_SCHEMA_VERSION},
          f"S9 两类终态各自绑定的 wire 版本不同：refusal={refusal_versions} "
          f"alignment={align_versions}")
    check(all(t.block_char_length >= 0 and t.matched_chars >= 0
              for t in terminals),
          "S9 终态的字符长度字段形状合法")

    refs = []
    for table in final.tables:
        refs.extend(table.source_refs)
        for row in table.rows:
            for cell in row.cells:
                refs.extend(cell.source_refs)
    check(bool(refs), f"S9 现场存在 cell source ref（{len(refs)} 条）")
    ref_fields = {f.name for f in dataclasses.fields(TS.TableCellSourceRef)}
    check({"source_ref_id", "interval", "component_id", "component_locator",
           "evidence_block_id", "evidence_char_range", "terminal_kind",
           "terminal_id", "terminal_locator", "alignment_id", "verdict",
           "refusal_reason", "citable", "citable_reason"} <= ref_fields,
          f"S9 cell source ref 字段齐备：{sorted(ref_fields)}")
    # 逐段可引用性必须**如实记账**（不是"全部可引用"）：可引用 ⟺ 理由为
    # aligned_fragment_closed 且真落在 aligned 终态上；不可引用者必须给出已登记
    # 理由，并且不得伪称 aligned_fragment_closed。
    citable_refs = [r for r in refs if r.citable]
    non_citable_refs = [r for r in refs if not r.citable]
    check(bool(citable_refs) and bool(non_citable_refs),
          f"S9 现场同时存在可引用与不可引用的 cell source ref"
          f"（citable={len(citable_refs)} non_citable={len(non_citable_refs)}）")
    check(all(r.citable == (r.citable_reason == TS.CITABLE_REASON_TRUE)
              for r in refs),
          "S9 citable ⟺ citable_reason 为 aligned_fragment_closed（逐段自洽）")
    check(all(r.citable_reason in TS.CITABLE_REASONS
              for r in non_citable_refs),
          f"S9 不可引用理由全部在封闭表内："
          f"{sorted({r.citable_reason for r in non_citable_refs})}")
    check(all(r.verdict == "aligned" and r.terminal_kind == "alignment" and
              r.refusal_reason is None for r in citable_refs),
          "S9 可引用片段必须落在 aligned 终态上且不带 refusal_reason")
    check(all(r.alignment_id == r.terminal_id
              for r in refs if r.terminal_kind == "alignment") and
          all(r.alignment_id is None
              for r in refs if r.terminal_kind == "refusal"),
          "S9 alignment 终态派生 alignment_id == terminal_id，refusal 终态为 None")
    terminal_ids = {t.terminal_id for t in terminals}
    check(all(r.terminal_id in terminal_ids for r in refs),
          f"S9 全部 cell source ref 的 terminal_id 都指向经验证终态"
          f"（{len(terminal_ids)} 个终态）")
    check(all(r.interval.page_number >= 1 and r.interval.line_index >= 0 and
              r.interval.span_index >= 0 and len(r.interval.bbox) == 4
              for r in refs),
          "S9 cell source ref 的 interval 全部指向具体 (页, 行, 片段, bbox)")

    # 线格式半边：不可引用 / 伪造对齐 ID 在 to-4 内即被拒绝（不靠消费者自查）
    ref = citable_refs[0]
    raises(lambda: TS.TableCellSourceRef.from_dict(
        _wire_ref(ref, verdict="unaligned", citable=True,
                  citable_reason=TS.CITABLE_REASON_TRUE)),
        SchemaValidationError, "只有 aligned 终态的片段可引用",
        "S9 非 aligned 片段不得标记为可引用")
    raises(lambda: TS.TableCellSourceRef.from_dict(
        _wire_ref(ref, citable=False, citable_reason="verdict_not_aligned")),
        SchemaValidationError, "aligned 终态不得以",
        "S9 aligned 片段不得以'非对齐'为由声明不可引用（理由必须自洽）")
    raises(lambda: TS.TableCellSourceRef.from_dict(
        _wire_ref(ref, terminal_kind="refusal")),
        SchemaValidationError, "伪造 alignment ID",
        "S9 refusal 终态不得伪造 alignment ID（alignment_id 必须为 None）")

    aligned = {c.component_id for c in roots.components
               if getattr(c, "verdict", None) == "aligned"}
    non_aligned = {c.component_id for c in roots.components
                   if getattr(c, "verdict", None) != "aligned"}
    component_ids = {c.component_id for c in roots.components}
    check(bool(aligned) and bool(non_aligned),
          f"S9 现场 component 同时存在 aligned 与非 aligned"
          f"（aligned={len(aligned)} non_aligned={len(non_aligned)}）")
    unknown = sorted({r.component_id for r in refs} - component_ids)
    check(unknown == [],
          f"S9 全部 cell source ref 的 component 都在 TS4 终态中（未知 "
          f"{unknown[:3]}）")
    bad = [(r.source_ref_id, r.component_id, r.verdict)
           for r in citable_refs if r.component_id not in aligned]
    check(bad == [],
          f"S9 可引用片段一律落在 aligned component 上（反例 {bad[:2]}）")
    bad = [(r.source_ref_id, r.verdict, r.citable_reason)
           for r in refs
           if not r.citable and r.citable_reason == TS.CITABLE_REASON_TRUE]
    check(bad == [],
          f"S9 不可引用片段不得声称 aligned_fragment_closed（反例 {bad[:2]}）")

    check(all(cov.cell_refs and all(cov.cell_refs)
              for cov in final.coverages),
          "S9 coverage 的 cell_refs 非空且不含空串")
    check(all(iv.reason in TS.CITABLE_REASONS
              for cov in final.coverages for iv in cov.intervals),
          "S9 coverage 区间与不可引用理由码均在封闭表内")
    check(all(reason in TS.CITABLE_REASONS
              for cov in final.coverages
              for reason in cov.non_citable_reasons),
          "S9 coverage 的 non_citable_reasons 全部登记")
    non_citable = [iv for cov in final.coverages for iv in cov.intervals
                   if not iv.citable]
    check(all(iv.reason != TS.CITABLE_REASON_TRUE for iv in non_citable),
          f"S9 不可引用区间不得声称 aligned_fragment_closed"
          f"（{len(non_citable)} 条）")


# ---------------------------------------------------------------------------
# 分组执行（缺一环即显式 FAIL，不静默跳过）
# ---------------------------------------------------------------------------

def _run_group(name: str, fn, ctx) -> None:
    started = time.time()
    try:
        fn(ctx)
    except Exception as exc:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 分组异常：{type(exc).__name__}: {exc}")
    _results["details"].append(f"__{name}__ {time.time() - started:.1f}s")


def main() -> dict:
    started = time.time()
    # 只读前置：`bind_current_evidence_authority` 读的是 service 预检过的同一个绑定点
    # （`evidence.store._db_path`）。测试等价地先绑定，结束后恢复原值，并断言库文件
    # 前后逐字节不变。
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _db_identity(_DB_PATH) if _DB_PATH.exists() else None
    original_extract = _install_geometry_memo()
    try:
        _estore._db_path = _DB_PATH
        ctx = _live_chain()
        _run_group("S1", _test_s1, ctx)
        _run_group("S2", _test_s2, ctx)
        _run_group("S3", _test_s3, ctx)
        _run_group("S4", _test_s4, ctx)
        _run_group("S5", _test_s5, ctx)
        _run_group("S6", _test_s6, ctx)
        _run_group("S7", _test_s7, ctx)
        _run_group("S8", _test_s8, ctx)
        _run_group("S9", _test_s9, ctx)
    finally:
        TG.extract_table_geometry = original_extract
        _estore._db_path = previous_db_path
        after = _db_identity(_DB_PATH) if _DB_PATH.exists() else None
        check(before is not None and before == after,
              "只读前置：`data/evidence.db` 在测试前后必须逐项不变（size/"
              f"mtime_ns/sha256）：{before} vs {after}")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

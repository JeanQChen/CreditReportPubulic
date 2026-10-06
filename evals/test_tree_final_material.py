# -*- coding: utf-8 -*-
"""TS5 §19.12.1-10 / §19.12.4-7 / §19.12.6-7 / §19.12.7-1,5：终端 final material 的
**唯一正式链**、**复核器独立性**（声明级 AST + 运行期 monkeypatch + 自洽篡改反例）、
final synopsis 只引用 final span、四层守恒零差额，以及只读前置。

全部离线：只读本地 PDF 与**只读** `data/evidence.db` `data/financial_v2.db`；
无网络、无 LLM、无 Bocha、不写任何文件。

**两条链（`fmc-2` 起）**：真实文档的 `layout_text` 层带着实测的未解释残余与重复
归属，公开入口据此 fail-closed **拒绝签发**终端能力——这是本轮要的结果，不是缺陷。
因此本模块有两条链，且每条只测它该测的东西：

- `_chain()`：真实文档（`pinned` 信任清单里的那份）。测**内容判据**——简介范围
  （G5）、四层守恒的独立复算与 fail-closed 拒绝文本（G6）、篡改拒绝（G4）、只读
  （G7）。这条链上 `ctx["wrapper"] is None`，`ctx["refusal"]` 保留完整拒绝文本。
- `_capability_chain()`：合成 fixture（`fmc-2` 下**唯一**守恒资格成立的样本，经验收
  runner 的正式入口 `build_document` 构造）。测**签发机制**——唯一链 / 能力身份与
  域隔离（G1）、builder 全被换掉后照常签发（G3）、以及 G4 / G6 里需要"一个真的已
  签发的 wrapper"才有意义的反例。

任一条链上"把断言改成实际输出"都是禁止的：G1/G3 若没有能签发的链，正确的做法是
停下来报告，而不是把这两组改成断言拒绝（那会把终端能力机制整块覆盖删掉）。

覆盖（矩阵条目 → 分组）：

- **§19.12.7-1**（`G1-unique-chain`，走 `_capability_chain()`）：`VerifiedSpanSnapshot → builder → verifier →
  verified final wrapper` 每一环都是类型化对象；`final_verifier` 是终端能力**唯一**
  签发者（全包 AST 扫描 `_issue_capability` 的 kind 实参）；builder 侧不存在任何签发
  路径；raw `fms-1` 快照 / `to_dict()` / 鸭子仿造 / 字段逐项仿造 / TS4 能力跨种类
  冒充 / 越域消费 / 未登记域一律 `CapabilityError`，且反例都断言**具体错误文本**；
  builder 入口只接受 TS4 能力（raw 快照 / `None` / dict / 同名仿造类都被拒）。
- **§19.12.1-10(a)**（`G2-independence-ast`）：`verifier_independence_problems()` 对真实
  源码返回空，且对各类毒源确实报错（证其不是"永远返回空"的空转）；测试另用**自己写的**
  AST 走查断言复核器既不 import 也不引用 `table_builder` / `final_material_builder` 及
  其构建入口（含 `from . import X` 与"把模块名当字符串传给 Call"两种形状），
  并断言复核器模块对象上没有任何 builder 句柄、import 表闭集。
- **§19.12.1-10(b)**（`G3-builder-monkeypatch`，走 `_capability_chain()`）：把 `final_material_builder` 与
  `table_builder` 的**全部**构建入口（含私有 `_build_all`）换成"被调用即抛"，先用
  直接调用证明 patch 有效，再要求真实快照的复核照常签发，且
  `verification_fingerprint` 与 patch 前**逐字节相同**、调用计数为 0。
- **§19.12.1-10(c)**（`G4-tamper-rejected`）：改一个字符后**重算全部派生 locator /
  指纹 / 身份**（`create()`）再装配 `fms-1`（派生字段同样重算），断言篡改件自身 wire
  往返等值、成员只差一个对象、其余成员仍是同一对象——即"内部完全自洽"，复核器仍必须
  拒绝，理由是**从 roots 独立重算**得到的 `normalized_text 重算不等`；删除一条 final
  span 的自洽变体必须以 `应当产出 final span，但快照里没有` 被拒；已登记为能力对象的
  输入快照必须被"建造者与复核者分离"闸拦下。（文本篡改走**公开**入口
  `verify_final_material_snapshot`；删除变体与简介变体为控制墙钟成本走生产校验函数
  `_check_final_spans` / `_check_synopses`——公开入口逐条调用的正是它们，断言仍是
  类型 + 文本双精确。）
- **§19.12.4-7**（`G5-synopsis-scope`）：final synopsis 的来源与片段都必须是本快照内
  `fos-` 身份、属于**本节点**、且抽取式回指（逐字符相等）；测试**独立复算**每个节点的
  reason（只读 TS4 dispositions 的 `range_kind`）与简介逐节点一致；"只有正式表格、没有
  可引用段落"的节点使用新的 `table_material_available_no_text_synopsis`，
  legacy `table_only_pending_ts5` 一次都不出现；把其中一个节点的 reason 改回 legacy
  （仍是**另一个类型**的登记词表值、身份自洽）后必须被拒绝。并断言简介维度**确实**
  进入 snapshot 身份 / 内容指纹（`table_schema.member_ids()`，见
  `test_tree_table_schema` 的 S6）：漏掉这一维，改写一条简介就改不动 `snapshot_id`。
- **§19.12.6-7**（`G6-four-layer-conservation`）：层种类/顺序/分项/计量类别与测试自带
  字面量逐项一致；层 1 从 `decisions` + TS4 dispositions 独立复算、层 2 从 `bindings`
  独立复算、层 4 从 TS4 components 独立复算、层 3 的 `cell_source_ref` 与
  `caption_unit_note_ref` 从真实来源片段独立复算**精确字符数**；四层
  `sum(terms) == total`、`residual_gap == total - 其余四项`（含
  `registered_deferred`）、任何层不得出现 `layout_text_overlap` 或坐标越界；层 3
  的问题码必须全部落在登记形状内，且本样本上**确实**报出
  `layout_text_gap_interval_overlap` 与 `layout_text_residual_in_table`；
  每层 `balanced` 必须等于公开的 `conservation_layer_eligible` 判定（不再自写成
  "算术相等"）；聚合问题恰为四层问题的并集；真实文档必须被 fail-closed 拒绝且拒绝
  文本点名到层、带出实测 `residual_gap`；复核器的独立重算在守恒资格成立的合成链上
  返回空、在真实快照上**报出**层 3 差额，且层 1 / 2 / 4 与记录值零差额；并用一个
  自洽的不平衡变体证明这条重算**确实会报告**分项差额。
- **§19.12.7-5**（`G7-read-only`）：两个库（含 `-wal` / `-shm` 附属文件）的
  size / mtime_ns / sha256 在本模块前后逐项不变。

所有 `check` 都设计成"对应生产逻辑被移除即失败"：真值表 / 守恒分项 / reason 复算都用
测试自带的字面量与根读数重算，反例一律断言具体错误类型 + 具体错误文本。
"""

from __future__ import annotations

import ast
import hashlib
import json
import time
from dataclasses import fields
from pathlib import Path

from evidence import store as _estore

from evals import tree_stage_env as STAGE
from document_structure import aligner as AL
from document_structure import canonical as C
from document_structure import final_material_builder as FMB
from document_structure import final_verifier as FV
from document_structure import layout_builder as LB
from document_structure import schema as S
from document_structure import span_builder as SB
from document_structure import span_schema as SS
from document_structure import span_verifier as SV
from document_structure import table_builder as TB
from document_structure import table_geometry as TG
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError
from document_structure.evidence_gateway import bind_current_evidence_authority

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
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


def _capture(fn, exc):
    """跑一次并返回 `(异常或 None, 文本)`；用于"一次调用断言多处文本"。"""
    try:
        fn()
    except exc as e:
        return e, str(e)
    except Exception as e:  # noqa: BLE001
        return e, f"__WRONG_TYPE__:{type(e).__name__}: {e}"
    return None, ""


def check_aggregate(bad, total, msg, sample: int = 3):
    bad = list(bad)
    return check(not bad and total > 0,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


def check_aggregate_allow_empty(bad, total, msg, sample: int = 3):
    bad = list(bad)
    return check(not bad,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


# ---------------------------------------------------------------------------
# 0. 生产常量与测试自带真值表
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
_TRUST_KEY = "NDSD_2024_year"
_DS_DIR = _REPO / "document_structure"

_VERIFIER_VERSION = "vfmi-1"
_MAX_PROBLEMS = 40
_CAPABILITY_KIND = "VerifiedFinalMaterialStructureSnapshot"

#: 复核器**不得**触碰的生产模块与符号（§19.11.1 的声明）。
_FORBIDDEN_MODULES = ("table_builder", "final_material_builder")
_FORBIDDEN_SYMBOLS = (
    "build_final_material_snapshot", "build_tables", "build_table",
    "plan_decisions", "build_adornment_relations", "_assemble",
)
#: 复核器允许的 import 闭集（stdlib 与登记的 document_structure 叶子模块）。
_ALLOWED_IMPORTS = frozenset((
    "__future__", "ast", "hashlib", "json", "os", "sys", "typing",
    "canonical", "normalization", "schema", "span_policy", "span_schema",
    "table_classification", "table_geometry", "table_schema", "versions",
))

#: §19.7.1 八类裁决 → 三目标真值表。**测试自带**字面量。
_TARGET_OF = {
    "absorbed_as_caption": "table",
    "absorbed_as_unit": "table",
    "absorbed_as_note": "table",
    "absorbed_as_table_body": "table",
    "kept_as_paragraph": "final_span",
    "absorbed_into_body": "final_span",
    "unsupported_table_structure": "none",
    "unresolved_geometry": "none",
}

#: §19.9.3 四层守恒：层种类顺序与各层分项。**测试自带**字面量。
#:
#: `fmc-2`：`layout_text` 层新增 `registered_deferred`（被正式 typed gap 延期的
#: 字符）与 `non_semantic_whitespace`（整段恰为空白的精确范围）。
#:
#: `registered_deferred` 与 `residual_gap` 的区别是**资格**而非口径——只有"恰好被
#: 一个终态为 pending/rejected 的 component 覆盖、且缺口台账里有与它精确对应的
#: typed gap"的字符才允许计入；`non_semantic_whitespace` 的判据只有"区间原文
#: `.strip() == \"\"`"一条。任何一条不成立仍必须留在 `residual_gap` 并阻止本层
#: balanced——两项都不是兜底项。
_LAYERS = (
    ("disposition",
     ("absorbed_to_table", "final_paragraph", "pending_or_unsupported")),
    ("component", ("final_span", "table_object", "pending", "rejected")),
    ("layout_text", ("cell_source_ref", "caption_unit_note_ref",
                     "final_paragraph_ref", "registered_deferred",
                     "non_semantic_whitespace", "residual_gap")),
    ("evidence_interval", ("preserved", "missing", "duplicated")),
)

#: 分项 → 计量类别。**测试自带**字面量。
_TERM_KIND = {
    "absorbed_to_table": "count", "final_paragraph": "count",
    "pending_or_unsupported": "count", "final_span": "count",
    "table_object": "count", "pending": "count", "rejected": "count",
    "cell_source_ref": "character", "caption_unit_note_ref": "character",
    "final_paragraph_ref": "character", "registered_deferred": "character",
    "non_semantic_whitespace": "character",
    "residual_gap": "character",
    "preserved": "interval", "missing": "interval", "duplicated": "interval",
}

#: 层 3（`layout_text`）**允许出现**的问题码前缀。**测试自带**字面量，取自
#: `final_material_builder._layout_text_layer` 的产出点；出现登记表之外的形状即
#: 失败（不是"只要不报就通过"）。注意这几个码是**如实披露的缺陷**，其中
#: `layout_text_gap_interval_overlap` / `layout_text_residual*` 正是真实文档不满足
#: 守恒资格的原因，不是可以顺手抹掉的噪声。
#:
#: `layout_text_gap_interval_overlap`（`fmc-3` 起）与
#: `layout_text_duplicate_claim` 都已登记：前者是"同一段字符被**两条**区域型 typed
#: gap 同时覆盖，按 §19.5.3 的顺序不猜、留在残余并记账"；后者是"同一片段上同范围
#: 同类别的主张出现两次"。两者都是诚实披露，出现即已进入本表，不允许落到表外。
_LAYER3_PROBLEM_PREFIXES = (
    "layout_text_ref_out_of_span",
    "layout_text_hit_out_of_span",
    "layout_text_duplicate_claim",
    "layout_text_overlap",
    "layout_text_residual_in_table",
    "layout_text_residual",
    "layout_text_gap_interval_overlap",
)

_LEGACY_TABLE_ONLY_REASON = "table_only_pending_ts5"
_NEW_TABLE_ONLY_REASON = "table_material_available_no_text_synopsis"
#: final 简介里**允许没有来源 span** 的理由码（`final_verifier._NO_SOURCE_SYNOPSIS_REASONS`
#: 的测试侧镜像）。legacy `table_only_pending_ts5` **不在**其中：它是**另一个类型**的
#: 词表值，出现即已是错误，不能顺手当成"合法的无来源理由"放行。
_NO_SOURCE_REASONS = ("no_span", _NEW_TABLE_ONLY_REASON)

_STATE: dict = {}
#: 合成 fixture 的链（能签发终端能力）；只有 G1 / G3 与 G4 / G6 的能力相关断言用它。
_CAP_STATE: dict = {}


# ---------------------------------------------------------------------------
# 1. 只读现场：一次真实现场，全部组共用
# ---------------------------------------------------------------------------

def _file_identity(path: Path) -> dict | None:
    if not path.exists():
        return None
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _store_identity(path: Path) -> dict:
    """库文件本身 + 可能存在的 `-wal` / `-shm` 附属文件的身份。"""
    out = {"db": _file_identity(path)}
    for suffix in ("-wal", "-shm"):
        out[suffix] = _file_identity(Path(str(path) + suffix))
    return out


def _identity_bundle() -> dict:
    return {"evidence": _store_identity(_EVIDENCE_DB),
            "financial": _store_identity(_FIN_DB)}


def _chain() -> dict:
    """真实现场：PDF → PageLayout → Outline → SpanSnapshot → verified →
    final material snapshot → verified final wrapper。

    `fmc-2` 起，真实文档的 `layout_text` 层带着实测的未解释残余与重复主张，公开
    入口据此**拒绝签发**终端能力（详见 §八 报告）。因此这里如实记录拒绝文本，
    而不是把它当成异常咽掉：本模块在真实链上测的是**内容判据**（简介范围 / 四层
    守恒 / 篡改拒绝 / 只读），签发**机制**由 `_capability_chain()` 的合成 fixture
    承担——它才是 `fmc-2` 下守恒资格成立的那一份。
    """
    if _STATE:
        return _STATE
    trust = json.loads(_TRUST.read_text(encoding="utf-8"))
    doc = trust["documents"][_TRUST_KEY]
    pdf = _REPO / doc["source_pdf_relpath"]
    ident = doc["expected_identity"]

    from sections import service
    service._prepare_stores(service.ServiceConfig(
        fin_db=str(_FIN_DB), ev_db=str(_EVIDENCE_DB)))
    authority = bind_current_evidence_authority()
    vlayout = LB.build_verified_page_layout(
        pdf, company_id=ident["company_id"], document_id=ident["document_id"])
    alignment = AL.align_evidence_set_verified(vlayout, authority)
    handoff = SB.issue_live_ts3_handoff(vlayout, alignment, authority)
    snap = SB.build_span_snapshot(handoff, stage=STAGE.current_stage())
    verified = SV.verify_span_snapshot(snap, handoff)
    final = FMB.build_final_material_snapshot(verified)
    try:
        wrapper = FV.verify_final_material_snapshot(verified, final)
        refusal = None
    except FV.FinalMaterialConservationError as exc:
        wrapper = None
        refusal = str(exc)
    _STATE.update({"snap": snap, "verified": verified, "final": final,
                   "wrapper": wrapper, "refusal": refusal,
                   "handoff": handoff, "ident": ident,
                   "policy": verified.handoff.qualification_policy,
                   "roots": None})
    return _STATE


def _roots(ctx) -> FV._Roots:
    """复核器视角的根读数（只从经验证的交接取）。缓存复用。"""
    if ctx["roots"] is None:
        profiles = FV.load_table_profile_bundle()
        geometry = TG.extract_table_geometry(
            ctx["verified"].handoff.layout_capability)
        ctx["roots"] = FV._Roots(ctx["verified"], profiles, geometry)
    return ctx["roots"]


#: 合成 fixture：`fmc-2` 下**唯一**能走完到签发的文档（见 `_capability_chain`）。
_CAPABILITY_KEY = "FIXTURE_BOND_2026__non_300750_ts4"


def _capability_chain() -> dict:
    """能**签发**终端能力的一条链（合成 fixture，经正式验收入口构造）。

    `fmc-2` 起，真实文档的 `layout_text` 层带着实测的未解释残余与重复主张，
    公开入口据此 fail-closed **拒绝签发**——这是本轮要的结果，不是缺陷。但 G1
    （唯一链 / 能力身份与域隔离）与 G3（builder 被换掉后复核照常签发）测的是
    **签发机制本身**，它们必须有一条能签发的链，否则这两组只能改成断言"拒绝"，
    那会把"能力机制"整块覆盖删掉。

    因此这里用合成 fixture：它是 `fmc-2` 下唯一守恒资格成立的文档（`residual_gap=0`
    且 19 个字符由 typed gap 精确延期）。构造走**验收 runner 的正式入口**
    `build_document`，不经任何旁路；并当场断言它确实 `issued`，否则本函数立刻失败
    而不是把"没有能力对象"带到下游。
    """
    if _CAP_STATE:
        return _CAP_STATE
    import evaluation.run_tree_table_acceptance as RR  # noqa: PLC0415

    built = RR.build_document(inputs=RR.bind_inputs(),
                              document_key=_CAPABILITY_KEY)
    verified = built["verified_ts4"]
    final = built["final"]
    wrapper = built["wrapper"]
    if wrapper is None:
        raise AssertionError(
            f"合成 fixture {_CAPABILITY_KEY!r} 在 `fmc-2` 下未能签发终端能力"
            f"（refusal_reason={built['refusal_reason']!r}）：G1/G3 需要一条能签发的"
            "链；若连 fixture 都被拒，应停下来报告而不是改断言")
    _CAP_STATE.update({
        "snap": verified.snapshot, "verified": verified, "final": final,
        "wrapper": wrapper, "handoff": built["handoff"], "ident": None,
        "policy": verified.handoff.qualification_policy, "roots": None,
    })
    return _CAP_STATE


# ---------------------------------------------------------------------------
# 2. 自洽重建件（派生字段一律交给生产 create 重算）
# ---------------------------------------------------------------------------

def _all_fields(obj) -> dict:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def _forge(obj, **over):
    """绕过 `__post_init__` 造一个"只改了指定字段"的对象（**仅**用于复核器独立性反例）。

    公开路径（构造器 / `create()` / `from_dict`）要么重算派生字段、要么直接拒绝，
    因此"复核器自己有没有独立重算版本轴 / 来源轴"这件事**只能**用旁路对象来问：
    这里造出来的对象除被改的那个字段外与原件逐字段相同。被改的若恰好是 schema
    层会独立校验的字段（version 轴 / 成员闭合），`to_dict()` 自己也会拒——那不是
    缺陷，是第二道闸；两道闸都要单独断言。
    """
    forged = object.__new__(type(obj))
    for f in fields(obj):
        object.__setattr__(forged, f.name,
                           over.get(f.name, getattr(obj, f.name)))
    return forged


def _rebuild_span(span, **over):
    kw = _all_fields(span)
    kw.update(over)
    return TS.FinalOutlineSpan.create(**kw)


def _rebuild_snapshot(snapshot, **over):
    kw = _all_fields(snapshot)
    kw.update(over)
    return TS.FinalMaterialStructureSnapshot.create(**kw)


def _rebuild_layer(layer, term, delta):
    """把一个分项改掉的自洽层：`balanced` / `problems` 与分项自洽。"""
    kind = _TERM_KIND[term]
    terms = []
    for item in layer.terms:
        if item.term != term:
            terms.append(item)
            continue
        terms.append(TS.ConservationTerm(
            term=term, term_kind=kind,
            count=(item.count + delta if kind == "count" else 0),
            char_count=(item.char_count + delta if kind == "character" else 0),
            interval_length=(item.interval_length + delta
                             if kind == "interval" else 0)))
    field = ("count" if kind == "count" else
             "char_count" if kind == "character" else "interval_length")
    total = getattr(layer.total, field)
    summed = sum(getattr(item, field) for item in terms)
    return TS.ConservationLayer(
        layer_kind=layer.layer_kind, terms=tuple(terms), total=layer.total,
        balanced=(summed == total),
        problems=(() if summed == total
                  else (f"layer_unbalanced:{layer.layer_kind}",)))


def _layer_of(snapshot, kind):
    for layer in snapshot.conservation.layers:
        if layer.layer_kind == kind:
            return layer
    return None


def _term_value(layer, term):
    for item in layer.terms:
        if item.term == term:
            kind = _TERM_KIND[term]
            return (item.count if kind == "count" else
                    item.char_count if kind == "character" else
                    item.interval_length)
    return None


def _total_value(layer) -> int:
    """该层的合计值（口径由第一个分项的语义类别决定）。"""
    kind = _TERM_KIND[layer.terms[0].term]
    return (layer.total.count if kind == "count" else
            layer.total.char_count if kind == "character" else
            layer.total.interval_length)


def _layer_values(layer) -> dict:
    return {item.term: _term_value(layer, item.term) for item in layer.terms}


# ---------------------------------------------------------------------------
# 3. 区间算术（层 3 的独立复算）
# ---------------------------------------------------------------------------

def _merged(ranges):
    out: list = []
    for lo, hi in sorted(tuple(x) for x in ranges):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _union_len(ranges) -> int:
    return sum(hi - lo for lo, hi in _merged(ranges))


def _clamp(ranges, hi_limit: int):
    out = []
    for lo, hi in ranges:
        lo = max(0, int(lo))
        hi = min(int(hi), hi_limit)
        if hi > lo:
            out.append((lo, hi))
    return out


def _intersect(a_ranges, b_ranges) -> int:
    """两组区间**并集**的交集长度（先各自归并再求交）。"""
    a = _merged(a_ranges)
    b = _merged(b_ranges)
    total = 0
    for lo_a, hi_a in a:
        for lo_b, hi_b in b:
            lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
            if hi > lo:
                total += hi - lo
    return total


def _layout_span_lengths(ctx) -> dict:
    """`(page, line, span) → 片段字符长度`（跳过家具行），与 builder 的坐标域一致。"""
    out: dict = {}
    for page in ctx["handoff"].page_layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            for si, span in enumerate(line.spans):
                out[(page.page_number, line.line_index, si)] = \
                    int(span.char_end) - int(span.char_start)
    return out


# ---------------------------------------------------------------------------
# G1. 唯一正式链（§19.12.7-1）
# ---------------------------------------------------------------------------

def _ts5_issuers() -> dict:
    """全包 AST 扫描：谁以 `_issue_capability` 签发 TS5 终端能力种类。"""
    issuers: dict = {}
    for path in sorted(_DS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            callee = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if callee != "_issue_capability":
                continue
            kinds = []
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    kinds.append(arg.value)
                elif isinstance(arg, ast.Name) and \
                        arg.id in ("CAPABILITY_KIND", "KIND", "ISSUER_KIND"):
                    kinds.append("*CAPABILITY_KIND*")
            if any(k == _CAPABILITY_KIND or k == "*CAPABILITY_KIND*"
                   for k in kinds):
                issuers.setdefault(path.stem, []).append(node.lineno)
    return issuers


def _g1_unique_chain(ctx) -> None:
    """唯一正式链 / 能力身份与域隔离。

    `fmc-2` 起真实文档被 fail-closed 拒绝签发，因此本组测的是**签发机制**，
    必须在能签发的那条链上跑（合成 fixture）；真实文档侧的守恒/内容判据由
    G5/G6/G7 承担。见 `_capability_chain` 的说明。
    """
    ctx = _capability_chain()
    snap = ctx["snap"]
    verified = ctx["verified"]
    final = ctx["final"]
    wrapper = ctx["wrapper"]

    check(type(verified).__name__ == "VerifiedSpanSnapshot",
          "链的第 1 环是 TS4 能力对象 `VerifiedSpanSnapshot`，得到 "
          f"{type(verified).__name__}")
    check(isinstance(final, TS.FinalMaterialStructureSnapshot),
          "链的第 2 环是 `fms-1` 快照（builder 的唯一产出类型）")
    check(isinstance(wrapper, FV.VerifiedFinalMaterialStructureSnapshot),
          "链的第 3 环是 `vfmi-1` 终端能力（复核器的唯一产出类型）")
    check(FV.VERIFIER_VERSION == _VERIFIER_VERSION and
          wrapper.issuer_version == _VERIFIER_VERSION,
          f"复核域版本恒为 {_VERIFIER_VERSION!r}（实得 "
          f"{wrapper.issuer_version!r}）")
    check(FV.CAPABILITY_KIND == _CAPABILITY_KIND and
          FMB.CAPABILITY_KIND == _CAPABILITY_KIND,
          "builder 与 verifier 声明的终端能力种类一致（不存在第二套终端能力）")
    check(FV.MAX_PROBLEMS == _MAX_PROBLEMS and FV.MAX_PROBLEMS > 0,
          f"复核问题清单上限恒为 {_MAX_PROBLEMS}")
    check(wrapper.snapshot is final and wrapper.verified_span is verified,
          "终端能力持有的是**当次**快照与 TS4 能力本身（不是副本、不是伪造件）")
    check(wrapper.issuer_scope == verified.issuer_scope and
          wrapper.issuer_scope in SS.ISSUER_SCOPES,
          f"终端能力的签发域继承 TS4 能力（{wrapper.issuer_scope!r}）")
    check(wrapper.source_kind == verified.source_kind,
          "终端能力的 source_kind 与 TS4 能力一致")
    check(SS.CAPABILITY_KINDS.count(_CAPABILITY_KIND) == 1 and
          _CAPABILITY_KIND in SS.CAPABILITY_KINDS,
          "TS5 终端能力种类恰登记一次（没有重复登记的第二套终端能力）")
    check(tuple(SS.ISSUER_SCOPES) == ("live", "pinned_acceptance", "testing"),
          f"签发域恰为三个且严格隔离（{SS.ISSUER_SCOPES}）")

    # 唯一签发者：AST 扫描全包。
    issuers = _ts5_issuers()
    check(list(issuers) == ["final_verifier"] and
          len(issuers.get("final_verifier", ())) == 1,
          f"TS5 终端能力的唯一签发者是 `final_verifier`（实得 {issuers}）")
    check("_issue_capability" not in
          (_DS_DIR / "final_material_builder.py").read_text(encoding="utf-8"),
          "builder 侧没有 `_issue_capability`：不得自行签发终端能力")
    check("_issue_capability" not in
          (_DS_DIR / "table_builder.py").read_text(encoding="utf-8"),
          "table_builder 侧同样没有签发路径")

    # 能力身份与指纹的独立复算。
    identity = wrapper.identity()
    check(set(identity) == {"issuer_scope", "source_kind", "issuer_version",
                            "snapshot_id", "snapshot_content_fingerprint",
                            "verified_span_snapshot_id",
                            "verification_fingerprint"},
          f"终端能力身份字段恰为登记的七项（实得 {sorted(identity)}）")
    check(identity["snapshot_id"] == final.snapshot_id and
          identity["snapshot_content_fingerprint"] ==
          final.content_fingerprint and
          identity["verified_span_snapshot_id"] ==
          verified.snapshot.snapshot_id and
          identity["issuer_version"] == _VERIFIER_VERSION and
          identity["source_kind"] == verified.source_kind,
          "身份字典的每一项都来自当次对象（不是自报常量）")
    payload = [_VERIFIER_VERSION, final.snapshot_id, final.content_fingerprint,
               final.upstream_dependency_fingerprint,
               verified.snapshot.snapshot_id,
               verified.snapshot.content_fingerprint,
               verified.verification_fingerprint,
               verified.handoff.handoff_identity,
               wrapper.issuer_scope, verified.source_kind]
    expected_fp = hashlib.sha256(
        C.canonical_json(payload).encode("utf-8")).hexdigest()
    check(wrapper.verification_fingerprint == expected_fp,
          "复核指纹 = sha256(canonical 十项载荷：版本 / 快照身份 / 上游指纹 / "
          "TS4 能力身份 / handoff 身份 / 域 / 来源种类)，可被独立复算")
    check(wrapper.verification_fingerprint != final.content_fingerprint and
          wrapper.verification_fingerprint != final.snapshot_id,
          "复核指纹与构建域身份不是同一个值（复核域独立于构建域）")
    check(wrapper.is_document_blocked() is False and
          FV.blocking_gap_histogram(final) == {} and
          final.blocking_gap_count == 0,
          "本样本无阻断性缺口：`is_document_blocked()` 恒为 False")

    # 只有经正式签发路径产生的对象才被承认。
    forged = FV.VerifiedFinalMaterialStructureSnapshot(
        snapshot=final, ts4=verified, scope=wrapper.issuer_scope,
        source_kind=wrapper.source_kind, issuer_version=_VERIFIER_VERSION,
        verification_fingerprint=wrapper.verification_fingerprint, problems=())
    duck = type("Duck", (), {
        "snapshot": final, "verified_span": verified,
        "issuer_scope": wrapper.issuer_scope,
        "issuer_version": _VERIFIER_VERSION,
        "source_kind": wrapper.source_kind,
        "verification_fingerprint": wrapper.verification_fingerprint,
        "is_document_blocked": lambda self: False,
        "identity": lambda self: dict(identity),
    })()
    nonce = "不是本进程由正式签发路径产生的实例"
    check(FV.assert_final_material_capability(wrapper) is wrapper,
          "已签发终端能力可通过 `assert_final_material_capability`（正向对照）")
    raises(lambda: FV.assert_final_material_capability(final),
           SS.CapabilityError, nonce,
           "raw `fms-1` 快照不得当能力消费（必须经复核器签发）")
    raises(lambda: FV.assert_final_material_capability(
        json.dumps({"snapshot_id": final.snapshot_id})),
        SS.CapabilityError, nonce,
        "序列化字符串同样不得当能力消费")
    raises(lambda: FV.assert_final_material_capability(forged),
           SS.CapabilityError, nonce,
           "字段逐项仿造的 wrapper 不得当能力消费（登记按对象身份）")
    raises(lambda: FV.assert_final_material_capability(duck),
           SS.CapabilityError, nonce,
           "鸭子类型仿造件不得当能力消费")
    raises(lambda: FV.assert_final_material_capability(verified),
           SS.CapabilityError, "登记的种类为",
           "TS4 能力不得跨种类冒充 TS5 终端能力")
    other = [s for s in SS.ISSUER_SCOPES if s != wrapper.issuer_scope]
    if other:
        raises(lambda: FV.assert_final_material_capability(
            wrapper, scopes=(other[0],)),
            SS.CapabilityError, "不在允许集合",
            f"签发域严格隔离：{wrapper.issuer_scope!r} 的能力不得用于 "
            f"{other[0]!r}")
    else:
        check(False, "签发域严格隔离：找不到第二个签发域用于反例")
    check(FV.assert_final_material_capability(
        wrapper, scopes=(wrapper.issuer_scope,)) is wrapper,
        "本域内的消费路径正常（隔离不是一律拒绝）")
    raises(lambda: FV.assert_final_material_capability(
        wrapper, scopes=("not_a_scope",)),
        SchemaValidationError, "允许集合含未登记签发域",
        "未登记的签发域不许写成允许集合（fail-closed）")
    if wrapper.issuer_scope != "testing":
        raises(lambda: FV.assert_final_material_capability(
            wrapper, scopes=("testing",)),
            SS.CapabilityError, "不在允许集合",
            "把正式域能力标成 testing 域消费同样被拒（域不可改签）")
    check(FV.final_material_problems(wrapper) == wrapper.recheck_problems,
          "`final_material_problems` 的只读诊断与 wrapper 自报问题一致")
    raises(lambda: FV.final_material_problems(final),
           SS.CapabilityError, nonce,
           "诊断入口同样只接受已签发能力")

    # builder 的入口只接受 TS4 能力。
    bad_input = "正式输入必须是 VerifiedSpanSnapshot"
    raises(lambda: FMB.build_final_material_snapshot(snap),
           FMB.FinalMaterialBuildError, bad_input,
           "raw TS4 `SpanBuildSnapshot` 不得进入 final builder")
    raises(lambda: FMB.build_final_material_snapshot(None),
           FMB.FinalMaterialBuildError, bad_input,
           "`None` 不得进入 final builder")
    raises(lambda: FMB.build_final_material_snapshot(final.to_dict()),
           FMB.FinalMaterialBuildError, bad_input,
           "快照 dict 不得进入 final builder")
    raises(lambda: FMB.build_final_material_snapshot(wrapper),
           FMB.FinalMaterialBuildError, bad_input,
           "已复核的 wrapper 不得反向进入 builder（复核者是终点，不是上游）")
    fake_cls = type("VerifiedSpanSnapshot", (), {})
    raises(lambda: FMB.build_final_material_snapshot(fake_cls()),
           SchemaValidationError, nonce,
           "同名仿造类不得绕过 TS4 能力检查（名字对不上不算，登记对不上才拦）")
    check(V.TS5_FINAL_SPAN_BUILDER_VERSION == "sbf-1" and
          final.final_span_schema_version == V.FINAL_SPAN_SCHEMA_VERSION,
          "final span 一律使用新身份 sbf-1（不复用 TS4 的 sb-7）")
    check(all(span.span_id != span.ts4_source_span_locator
              for span in final.final_spans),
          "final span 身份与 TS4 源 span locator 不是同一个字符串"
          "（新身份不是复制）")

    _results["details"].append(
        f"__chain__ key={_CAPABILITY_KEY} snapshot={final.snapshot_id} "
        f"spans={len(final.final_spans)} "
        f"synopses={len(final.synopses)} tables={len(final.tables)} "
        f"components={final.component_count} "
        f"wrapper_fp={wrapper.verification_fingerprint[:16]} "
        f"scope={wrapper.issuer_scope!r} stage={STAGE.current_stage()!r}"
        f" | 真实现场（{_TRUST_KEY}）在上位模块 G6 读，且如实记录拒绝文本")


# ---------------------------------------------------------------------------
# G2. 复核器独立性（§19.12.1-10(a)，声明级）
# ---------------------------------------------------------------------------

_AST_CACHE: dict = {}


def _tree_of(path: Path) -> ast.Module:
    key = str(path)
    if key not in _AST_CACHE:
        _AST_CACHE[key] = ast.parse(path.read_text(encoding="utf-8"))
    return _AST_CACHE[key]


def _mentions_forbidden(name: str) -> bool:
    return any(m in name for m in _FORBIDDEN_MODULES)


def _independent_import_guard(tree: ast.Module) -> tuple:
    """测试**自己**的导入 / 引用走查（刻意覆盖生产守卫的死角）：

    `import X`、`from X import Y`、`from . import X`（module 为 None）、kind 别名、
    属性访问、裸名、以及"把模块名当字符串实参喂给 Call"（动态 import 的形状）。
    """
    problems: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _mentions_forbidden(alias.name.rsplit(".", 1)[-1]):
                    problems.append(f"import {alias.name}")
                if alias.asname in _FORBIDDEN_SYMBOLS:
                    problems.append(f"import {alias.name} as {alias.asname}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            leaf = module.rsplit(".", 1)[-1] if module else ""
            if leaf and _mentions_forbidden(leaf):
                problems.append(f"from {module} import")
            for alias in node.names:
                if _mentions_forbidden(alias.name):
                    problems.append(f"from {module or '.'} import {alias.name}")
                if alias.name in _FORBIDDEN_SYMBOLS:
                    problems.append(f"from {module or '.'} import {alias.name}")
                if alias.asname in _FORBIDDEN_SYMBOLS:
                    problems.append(
                        f"from {module or '.'} import {alias.name} as "
                        f"{alias.asname}")
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_SYMBOLS:
                problems.append(f".{node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_SYMBOLS:
                problems.append(f"name {node.id}")
        elif isinstance(node, ast.Call):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and \
                        isinstance(sub.value, str) and \
                        _mentions_forbidden(sub.value):
                    problems.append(f"字符串实参 {sub.value!r}")
    return tuple(sorted(set(problems)))


def _builder_handles(module) -> list:
    """模块对象上任何指向 builder 模块 / 构建入口的句柄。"""
    bad: list = []
    for name in dir(module):
        try:
            value = getattr(module, name)
        except Exception:  # noqa: BLE001
            continue
        owner = getattr(value, "__name__", None)
        origin = getattr(value, "__module__", None)
        hit = None
        for token in (owner, origin):
            if isinstance(token, str) and \
                    token.rsplit(".", 1)[-1] in _FORBIDDEN_MODULES:
                hit = token
                break
        if hit is None and isinstance(owner, str) and \
                owner in _FORBIDDEN_SYMBOLS:
            hit = owner
        if hit is not None:
            bad.append(f"{name}->{hit}")
    return bad


def _import_closure(tree: ast.Module) -> tuple:
    """模块的 import 闭集，用于证明扫描确实落在真源码上。"""
    seen: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level >= 1 and not module:
                seen.extend(alias.name for alias in node.names)
            else:
                seen.append(module.rsplit(".", 1)[-1])
    return tuple(sorted(set(seen)))


def _g2_independence_ast(ctx) -> None:
    verifier_path = Path(FV.__file__).resolve()
    source = verifier_path.read_text(encoding="utf-8")
    check(verifier_path.parent == _DS_DIR and
          verifier_path.name == "final_verifier.py",
          f"复核器源码位于 document_structure/final_verifier.py"
          f"（{verifier_path}）")
    check("def verify_final_material_snapshot" in source and
          "def verifier_independence_problems" in source,
          "扫描的确实是复核器真源码（两个关键入口都在）")

    check(FV.verifier_independence_problems() == (),
          "生产守卫 `verifier_independence_problems()` 对真实源码返回空")
    check(tuple(FV.FORBIDDEN_DELEGATION_MODULES) == _FORBIDDEN_MODULES,
          f"登记的禁忌模块恰为 {list(_FORBIDDEN_MODULES)}"
          f"（实得 {FV.FORBIDDEN_DELEGATION_MODULES}）")
    check(tuple(FV.FORBIDDEN_DELEGATION_SYMBOLS) == _FORBIDDEN_SYMBOLS,
          f"登记的禁忌符号恰为 {list(_FORBIDDEN_SYMBOLS)}"
          f"（实得 {FV.FORBIDDEN_DELEGATION_SYMBOLS}）")

    # 生产守卫非空转：各类毒源必须被抓到（否则它就是"永远返回空"）。
    poison = (
        ("import final_material_builder\n", "不得"),
        ("from .table_builder import build_tables\n", "不得"),
        ("x = build_final_material_snapshot\n", "不得引用构建入口名"),
        ("y = FMB.build_tables\n", "不得引用构建入口属性"),
        ("z = plan_decisions\n", "不得引用构建入口名"),
        ("w = _assemble\n", "不得引用构建入口名"),
    )
    missed = [(src, want) for src, want in poison
              if not any(want in p
                         for p in FV.verifier_independence_problems(src))]
    check_aggregate(missed, len(poison),
                    "生产守卫对各类毒源确实报错（证其不是永远返回空的空转）")
    check(FV.verifier_independence_problems("import ast\nx = 1\n") == (),
          "生产守卫对无害源码返回空（不误报）")

    # 测试自己写的走查：必须与生产守卫同结论，并**额外**覆盖两种死角形状。
    tree = _tree_of(verifier_path)
    problems = _independent_import_guard(tree)
    check_aggregate_allow_empty(
        problems, sum(1 for _ in ast.walk(tree)),
        "测试独立 AST 走查：`final_verifier.py` 既不 import 也不引用 "
        "table_builder / final_material_builder 及其构建入口（含 `from . import X` "
        "与字符串实参两种形状）")
    blind = ("from . import table_builder\n"
             "import importlib\n"
             "m = importlib.import_module('document_structure.table_builder')\n")
    got = _independent_import_guard(ast.parse(blind))
    check(any("table_builder" in p for p in got) and len(got) >= 2,
          f"测试走查自身非空转：能同时抓住 `from . import table_builder` 与"
          f"字符串动态 import（实得 {list(got)}）")
    check(_independent_import_guard(ast.parse("import ast\nimport json\n")) == (),
          "测试走查不误报（对无害源码返回空）")

    closure = _import_closure(tree)
    stray = [name for name in closure if name not in _ALLOWED_IMPORTS]
    check_aggregate_allow_empty(
        stray, len(closure),
        f"复核器的 import 表落在登记闭集内（实得闭集 {list(closure)}）")
    check("table_classification" in closure and "table_geometry" in closure and
          "table_schema" in closure,
          "闭集里能看到 wire 层与几何 / 画像叶子模块（证明闭集扫描有内容）")

    handles = _builder_handles(FV)
    check(not handles,
          f"复核器模块对象上没有任何 builder 模块 / 构建入口句柄（{handles}）")
    check(not hasattr(FV, "TB") and not hasattr(FV, "FMB"),
          "复核器没有 `TB` / `FMB` 之类 builder 别名")
    check(not hasattr(FV, "build_tables") and
          not hasattr(FV, "build_final_material_snapshot"),
          "复核器自身不导出任何 builder 入口名")

    check(FV.self_check()["problems"] == [],
          f"复核器自检问题清单为空（{FV.self_check()['problems']}）")
    check(set(FV.UPSTREAM_FIELD_SOURCES) == set(TS.UPSTREAM_DEPENDENCY_FIELDS),
          "复核器的上游依赖字段表与 wire 层精确同集"
          "（缺一即无法独立复现上游指纹）")
    check(len(FV.UPSTREAM_FIELD_SOURCES) ==
          len(TS.UPSTREAM_DEPENDENCY_FIELDS) > 30,
          f"上游依赖字段数为 {len(TS.UPSTREAM_DEPENDENCY_FIELDS)}（同集且非空）")
    check(FMB.load_table_profile_bundle is FV.load_table_profile_bundle,
          "builder 与 verifier 复用的是**同一个**画像加载器叶子函数"
          "（共享叶子不构成委托）")


# ---------------------------------------------------------------------------
# G3. 运行期反例：builder 全被换掉后复核照样成立（§19.12.1-10(b)）
# ---------------------------------------------------------------------------

def _g3_builder_monkeypatch(ctx) -> None:
    """builder 全被换掉后复核照常签发（§19.12.1-10(b)）。

    "照常签发"必须有一条真的会签发的链才有意义，因此改用 `_capability_chain()`
    的合成 fixture；真实文档在 `fmc-2` 下先被守恒资格门拒绝，无法承担该对照。
    """
    ctx = _capability_chain()
    verified = ctx["verified"]
    final = ctx["final"]
    baseline = ctx["wrapper"]
    targets = [(FMB, "build_final_material_snapshot"),
               (FMB, "build_final_material_with_audit"),
               (FMB, "_build_all"),
               (TB, "build_tables"), (TB, "build_table"),
               (TB, "plan_decisions"), (TB, "build_adornment_relations")]
    saved = []
    for mod, name in targets:
        if hasattr(mod, name):
            saved.append((mod, name, getattr(mod, name)))
    calls: list = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError("复核路径不得调用 builder")

    try:
        for mod, name, _original in saved:
            setattr(mod, name, _boom)
        check(len(saved) == len(targets) and
              all(getattr(mod, name) is _boom for mod, name, _ in saved),
              f"全部 {len(targets)} 个 builder 入口（含私有 `_build_all`）都已换成"
              "抛异常件")
        raises(lambda: FMB.build_final_material_snapshot(verified),
               AssertionError, "复核路径不得调用 builder",
               "patch 有效（对照 1）：public final builder 入口被调用即抛")
        raises(lambda: TB.build_tables(None),
               AssertionError, "复核路径不得调用 builder",
               "patch 有效（对照 2）：table_builder 入口被调用即抛")
        calls.clear()

        again = FV.verify_final_material_snapshot(verified, final)
        check(isinstance(again, FV.VerifiedFinalMaterialStructureSnapshot),
              "builder 全被换掉后，真实快照的复核**照常签发**终端能力")
        check(again.verification_fingerprint ==
              baseline.verification_fingerprint,
              "两次复核的 `verification_fingerprint` 逐字节相同"
              "（复核结果不依赖 builder 是否可用）")
        check(again.identity() == baseline.identity(),
              "两次复核的身份字典逐字节相同")
        check(again.recheck_problems == baseline.recheck_problems,
              "两次复核记录的诚实缺口一致（复核路径确定性）")
        check(not calls,
              f"复核全程一次都没有触达 builder（调用计数 {len(calls)}）")
        check(again.snapshot is final and again.verified_span is verified,
              "第二次复核持有的仍是同一批对象（未重建）")
    finally:
        for mod, name, original in saved:
            setattr(mod, name, original)
        ok = all(getattr(mod, name) is not _boom for mod, name, _ in saved)
        check(ok, "finally 恢复：被 patch 的 builder 入口全部还原")


# ---------------------------------------------------------------------------
# G4. 自洽篡改反例：重算全部 ID / hash 后仍被拒（§19.12.1-10(c)）
# ---------------------------------------------------------------------------

def _g4_tamper_rejected(ctx) -> None:
    verified = ctx["verified"]
    final = ctx["final"]
    referenced = set()
    for syn in final.synopses:
        referenced.update(syn.source_final_span_ids)
        for snippet in syn.snippets:
            referenced.add(snippet.final_span_id)
    candidates = [sp for sp in final.final_spans
                  if sp.span_id not in referenced
                  and len(sp.normalized_text) >= 8]
    if not candidates:
        check(False, "需要一个未被任何简介引用的 final span 作为篡改靶子")
        return
    victim = max(candidates, key=lambda sp: len(sp.normalized_text))
    text = victim.normalized_text
    at = len(text) // 2
    replacement = "Z" if text[at] != "Z" else "Y"
    new_text = text[:at] + replacement + text[at + 1:]

    tampered_span = _rebuild_span(victim, normalized_text=new_text)
    check(tampered_span.span_id != victim.span_id,
          "改一个字符后 final span 身份（`fos-`）确实变化")
    check(tampered_span.span_locator == victim.span_locator,
          "定位（页 / 行 / 边界）未变时 locator 不变——身份变化来自内容指纹")
    check(tampered_span.normalized_text == new_text and
          len(new_text) == len(text),
          "篡改件文本长度不变（可引用区间与片段坐标仍然有效）")
    check(tampered_span.content_fingerprint != victim.content_fingerprint,
          "篡改件的内容指纹已按新文本重算")
    check(tampered_span.citable_intervals == victim.citable_intervals and
          tampered_span.boundary_factors == victim.boundary_factors and
          tampered_span.confidence == victim.confidence,
          "可引用区间 / 12 个边界因子 / 置信度原样保留（不是粗糙重造）")
    check(TS.FinalOutlineSpan.from_dict(tampered_span.to_dict()) ==
          tampered_span,
          "篡改件自身 wire 往返逐字节等值（schema 层查不出问题）")

    spans = tuple(tampered_span if sp.span_id == victim.span_id else sp
                  for sp in final.final_spans)
    tampered = _rebuild_snapshot(final, final_spans=spans)
    check(tampered.snapshot_id != final.snapshot_id and
          tampered.content_fingerprint != final.content_fingerprint,
          "篡改快照的全部派生身份 / 指纹都已重算（不是沿用旧 hash）")
    check(tampered.final_span_count == len(tampered.final_spans) ==
          final.final_span_count and
          tampered.component_count == final.component_count and
          tampered.table_count == final.table_count,
          "篡改快照的成员数量与各项计数自洽")
    check(tampered.upstream_dependency_fingerprint ==
          final.upstream_dependency_fingerprint and
          tampered.verified_span_snapshot_id ==
          final.verified_span_snapshot_id and
          tampered.document_id == final.document_id,
          "篡改快照的上游依赖束与 TS4 能力绑定未变（只改了成员内容）")
    check(TS.FinalMaterialStructureSnapshot.from_dict(tampered.to_dict()) ==
          tampered,
          "篡改快照自身 wire 往返逐字节等值（内部完全自洽）")
    same = sum(1 for a, b in zip(final.final_spans, tampered.final_spans)
               if a is b)
    check(same == len(final.final_spans) - 1,
          f"篡改只替换一个成员对象（其余 {same} 个仍是同一对象）")

    err, message = _capture(
        lambda: FV.verify_final_material_snapshot(verified, tampered),
        FV.FinalVerificationError)
    check(isinstance(err, FV.FinalVerificationError),
          f"自洽篡改的文本快照被公开复核入口拒绝（得到 {type(err).__name__}）")
    check("的 normalized_text 重算不等" in message,
          "拒绝理由来自**从 roots 独立重算**的字段比对（normalized_text 重算"
          f"不等）：{message[:110]!r}")
    check("final span：" in message,
          "拒绝信息落在 final span 组（不是被别的组抢先拒绝）")

    # 删除一条 final span 的自洽变体（计数同步减一）。
    kept = tuple(sp for sp in final.final_spans
                 if sp.span_id != victim.span_id)
    deleted = _rebuild_snapshot(final, final_spans=kept,
                                final_span_count=len(kept))
    check(len(deleted.final_spans) == len(final.final_spans) - 1 and
          deleted.final_span_count == len(kept) and
          TS.FinalMaterialStructureSnapshot.from_dict(deleted.to_dict()) ==
          deleted,
          "删除变体自身也是自洽的（身份重算 + wire 往返等值）")
    raises(lambda: FV._check_final_spans(deleted, _roots(ctx)),
           FV.FinalVerificationError, "应当产出 final span，但快照里没有",
           "删除 final span 的自洽变体被拒绝（不得静默少一段正文；走生产校验"
           "函数 `_check_final_spans`，与公开入口内是同一段代码）")

    # 建造者 / 复核者分离：输入快照本身不得已是能力对象。
    # 该反例需要一个**真的已签发的 wrapper** 才有意义（传 `None` 会在类型闸上
    # 以别的原因通过，等于空转），因此取自能签发的那条合成链。
    cap = _capability_chain()
    check(cap["wrapper"] is not None, "反例需要一个已签发的 wrapper")
    raises(lambda: FV.verify_final_material_snapshot(cap["verified"],
                                                     cap["wrapper"]),
           FV.FinalVerificationError,
           "待复核对象必须为 FinalMaterialStructureSnapshot",
           "已复核的 wrapper 不得再被当作待复核快照传入")
    registered = _rebuild_snapshot(final)
    SS._issue_capability(registered, "VerifiedSpanSnapshot", "testing")
    check(SS.capability_scope(registered) == "testing",
          "反例构造：把一个同形快照登记为能力对象（模拟旁路登记）")
    raises(lambda: FV._check_root_bundle(registered, _roots(ctx)),
           FV.FinalVerificationError, "不得本身已是能力对象",
           "已是能力对象的输入快照被建造者 / 复核者分离闸拦下")

    _results["details"].append(
        f"__tamper__ victim={victim.span_id} chars={len(text)} "
        f"new_span={tampered_span.span_id} new_snap={tampered.snapshot_id} "
        f"deleted_snap={deleted.snapshot_id} others_same={same}")


# ---------------------------------------------------------------------------
# G5. final synopsis 只引用 final span（§19.12.4-7）
# ---------------------------------------------------------------------------

def _g5_synopsis_scope(ctx) -> None:
    snap = ctx["snap"]
    final = ctx["final"]
    node_ids = sorted({n.node_id
                       for n in ctx["handoff"].document_outline.nodes})
    got_ids = [syn.node_id for syn in final.synopses]
    check(got_ids == node_ids,
          f"final 简介恰好覆盖全部节点且每节点一份、按 node_id 升序"
          f"（{len(got_ids)} vs {len(node_ids)}）")

    bad_source = []
    bad_node = []
    bad_extract = []
    for syn in final.synopses:
        for sid in syn.source_final_span_ids:
            span = final.final_span_by_id(sid)
            if not sid.startswith("fos-") or span is None:
                bad_source.append((syn.node_id, sid))
            elif span.node_id != syn.node_id:
                bad_node.append((syn.node_id, sid))
        for snippet in syn.snippets:
            span = final.final_span_by_id(snippet.final_span_id)
            if span is None or not snippet.final_span_id.startswith("fos-"):
                bad_source.append((syn.node_id, snippet.final_span_id))
                continue
            if span.node_id != syn.node_id:
                bad_node.append((syn.node_id, snippet.final_span_id))
            elif snippet.char_end > len(span.normalized_text) or \
                    span.normalized_text[snippet.char_start:snippet.char_end] \
                    != snippet.text:
                bad_extract.append((syn.node_id, snippet.span_id))
    check_aggregate_allow_empty(
        bad_source, len(final.synopses),
        "每条 final 简介的来源 / 片段都指向本快照内 `fos-` 身份的 final span")
    check_aggregate_allow_empty(
        bad_node, len(final.synopses),
        "简介来源的 final span 必须属于**本节点**（不得跨节点借正文）")
    check_aggregate_allow_empty(
        bad_extract, len(final.synopses),
        "简介片段必须是抽取式回指（与 final span 原文逐字符相等）")
    check(all(syn.status == "available" for syn in final.synopses
              if syn.snippets),
          "有片段即状态为 available（片段与可用状态同向）")
    check(all(syn.snippets for syn in final.synopses
              if syn.status == "available"),
          "状态为 available 即必有片段（不存在空可用简介）")
    check(all(syn.source_final_span_ids == () and not syn.snippets
              for syn in final.synopses
              if syn.reason_code in _NO_SOURCE_REASONS),
          "无来源理由码的节点既不声明来源也不携带片段"
          "（表格不得伪装成 final span）")

    blobs = C.canonical_json([syn.to_dict() for syn in final.synopses])
    leaked = [x for x in
              [t.table_id for t in final.tables] +
              [c.coverage_id for c in final.coverages] +
              [d.decision_id for d in final.decisions] +
              [b.binding_id for b in final.bindings] +
              [r.source_ref_id for t in final.tables for r in t.source_refs]
              if x in blobs]
    check(not leaked,
          "final 简介载荷里没有任何 table / coverage / decision / binding / "
          f"cell 来源片段身份（表格不得伪装成正文来源；实得 {leaked[:3]}）")
    check(_LEGACY_TABLE_ONLY_REASON not in blobs,
          f"TS5 final 简介里不出现 legacy 理由码 "
          f"`{_LEGACY_TABLE_ONLY_REASON}`（那是还在等 TS5 的历史语义）")

    # 独立复算每个节点的 reason（只读 TS4 dispositions 的 range_kind）。
    counts: dict = {}
    for dispo in snap.dispositions:
        if dispo.node_id is None:
            continue
        bucket = counts.setdefault(dispo.node_id, {})
        bucket[dispo.range_kind] = bucket.get(dispo.range_kind, 0) + 1
    expected: dict = {}
    for node_id in node_ids:
        bucket = counts.get(node_id, {})
        regular = bucket.get("regular", 0)
        table = (bucket.get("table_inside", 0) +
                 bucket.get("table_adjacency", 0))
        empty = bucket.get("empty", 0)
        if regular > 0:
            citable = [sp for sp in final.final_spans
                       if sp.node_id == node_id and sp.normalized_text
                       and sp.is_citable()]
            expected[node_id] = None if citable else "no_span"
        elif table > 0:
            expected[node_id] = _NEW_TABLE_ONLY_REASON
        elif empty > 0:
            expected[node_id] = "empty_text"
        else:
            expected[node_id] = "no_span"

    mismatch = []
    table_only = []
    for syn in final.synopses:
        want = expected[syn.node_id]
        if want is None:
            if syn.reason_code not in (None, "length_exceeded"):
                mismatch.append((syn.node_id, syn.reason_code, want))
            if syn.reason_code is None and syn.status != "available":
                mismatch.append((syn.node_id, syn.status, "available"))
        else:
            if syn.reason_code != want:
                mismatch.append((syn.node_id, syn.reason_code, want))
            if syn.status != "synopsis_unavailable":
                mismatch.append((syn.node_id, syn.status,
                                 "synopsis_unavailable"))
            if syn.reason_code in _NO_SOURCE_REASONS \
                    and syn.source_final_span_ids:
                mismatch.append((syn.node_id, "source_final_span_ids", ()))
            if syn.snippets:
                mismatch.append((syn.node_id, "snippets", ()))
            if syn.reason_code == _NEW_TABLE_ONLY_REASON:
                table_only.append(syn)
    check_aggregate_allow_empty(
        mismatch, len(final.synopses),
        "逐节点独立复算 reason_code / status / 来源 / 片段：与简介逐项一致")
    hist: dict = {}
    for value in expected.values():
        hist[value] = hist.get(value, 0) + 1
    _results["details"].append(
        f"__reason__ expected_hist={hist} "
        f"length_exceeded="
        f"{sum(1 for s in final.synopses if s.reason_code == 'length_exceeded')} "
        f"with_snippets={sum(1 for s in final.synopses if s.snippets)}")
    check(len(table_only) > 0 and hist.get(_NEW_TABLE_ONLY_REASON, 0) > 0,
          f"现场存在只有正式表格、没有可引用段落的节点，且使用新理由码"
          f"（{len(table_only)} 个）")
    check(all(syn.reason_code == _NEW_TABLE_ONLY_REASON and
              syn.source_final_span_ids == () and not syn.snippets
              for syn in table_only),
          "这些节点为 synopsis_unavailable、无来源 span、无片段"
          "（表格材料不得伪造一条正文简介）")
    legacy = [syn.node_id for syn in final.synopses
              if syn.reason_code == _LEGACY_TABLE_ONLY_REASON]
    check(not legacy,
          f"TS5 final 简介里 legacy 理由码一次都不出现（{legacy[:3]}）")
    # §19.0.1 方案 C：两套词表分属两个类型。final 专属值必须在 final 词表里、
    # 且**不得**出现在 TS4 词表里；legacy 值反之。这正是"两代简介不能合并"的证据。
    check(_NEW_TABLE_ONLY_REASON in TS.FINAL_SYNOPSIS_REASON_CODES,
          f"final 专属理由码 {_NEW_TABLE_ONLY_REASON!r} 必须在 "
          f"FINAL_SYNOPSIS_REASON_CODES 里")
    check(_NEW_TABLE_ONLY_REASON not in S.SYNOPSIS_REASON_CODES,
          f"final 专属理由码 {_NEW_TABLE_ONLY_REASON!r} 不得出现在 TS4 词表里")
    check(_LEGACY_TABLE_ONLY_REASON in S.SYNOPSIS_REASON_CODES,
          f"TS4 专属理由码 {_LEGACY_TABLE_ONLY_REASON!r} 必须在 TS4 词表里")
    check(_LEGACY_TABLE_ONLY_REASON not in TS.FINAL_SYNOPSIS_REASON_CODES,
          f"TS4 专属理由码 {_LEGACY_TABLE_ONLY_REASON!r} 不得出现在 final 词表里")
    # final 简介必须是**独立类型**，不是 TS4 类型的实例。
    check(all(isinstance(syn, TS.FinalNavigationSynopsis)
              for syn in final.synopses)
          and not any(isinstance(syn, S.NavigationSynopsis)
                      for syn in final.synopses),
          "fms-1 的 synopses 必须全部是 FinalNavigationSynopsis，"
          "且一个都不是 TS4 NavigationSynopsis")
    check(all(syn.schema_version == V.FINAL_SYNOPSIS_SCHEMA_VERSION
              and syn.synopsis_version == V.FINAL_SYNOPSIS_VERSION
              for syn in final.synopses),
          "每条 final 简介都必须走 nss-3 / ns-3 版本轴")

    # §19.0.1 方案 C：把一条 final 简介换成 TS4 类型（哪怕字段形状"看起来一样"），
    # `fms-1` 必须拒绝——两个类型不能互相冒充。
    if not table_only:
        return
    hit = table_only[0]
    forged_ts4 = S.NavigationSynopsis.unavailable(
        node_id=hit.node_id, reason_code=_LEGACY_TABLE_ONLY_REASON,
        source_span_ids=())
    raises(lambda: _rebuild_snapshot(
               final, synopses=tuple(
                   forged_ts4 if syn.synopsis_id == hit.synopsis_id else syn
                   for syn in final.synopses)),
           SchemaValidationError, "FinalNavigationSynopsis",
           "TS4 简介不得顶替 final snapshot 里的 final 简介"
           "（fms-1 只接受 FinalNavigationSynopsis）")
    # 反向：final 类型不得接受 TS4 专属理由码。构造期即失败，不存在"写进去再说"。
    raises(lambda: TS.FinalNavigationSynopsis.unavailable(
               node_id=hit.node_id, reason_code=_LEGACY_TABLE_ONLY_REASON),
           SchemaValidationError, "reason_code",
           "final 简介不得使用 TS4 专属的 legacy 理由码")
    # §19.12.4-7：final 简介片段引用 `os-*`（TS4 span 身份）必须被拒。
    raises(lambda: TS.FinalSynopsisSnippet(
               final_span_id="os-0123456789abcdef", final_span_locator="loc-os-x",
               final_span_schema_version=V.SPAN_SCHEMA_VERSION,
               snippet_index=0, char_start=0, char_end=2, text="ab"),
           SchemaValidationError, "final_span_id",
           "final 片段不得引用 TS4 `os-*` span（只有 `fos-*` 是合法来源）")

    # 自洽篡改：把一条 final 简介改写成**另一个** final 理由码（`no_span`），
    # 身份自洽、wire 自洽，因此只有"独立复算 reason"能把 it 拦下。
    forged = TS.FinalNavigationSynopsis.unavailable(
        node_id=hit.node_id, reason_code="no_span")
    check(forged.reason_code == "no_span" and
          forged.status == hit.status and
          forged.synopsis_id != hit.synopsis_id and
          forged.synopsis_locator == hit.synopsis_locator and
          TS.FinalNavigationSynopsis.from_dict(forged.to_dict()) == forged,
          "篡改简介：换一个合法 final 理由码、身份已重算、定位不变、"
          "wire 往返自洽")
    synopses = tuple(forged if syn.synopsis_id == hit.synopsis_id else syn
                     for syn in final.synopses)
    tampered = _rebuild_snapshot(final, synopses=synopses)
    check(len(tampered.synopses) == len(final.synopses) and
          sum(1 for a, b in zip(final.synopses, tampered.synopses)
              if a is b) == len(final.synopses) - 1 and
          TS.FinalMaterialStructureSnapshot.from_dict(tampered.to_dict()) ==
          tampered,
          "篡改快照自身自洽：只替换一条简介、其余简介仍是同一对象、"
          "wire 往返逐字节等值")
    check((len(tampered.tables), len(tampered.final_spans),
           len(tampered.bindings), len(tampered.decisions),
           len(tampered.coverages)) ==
          (len(final.tables), len(final.final_spans), len(final.bindings),
           len(final.decisions), len(final.coverages)),
          "除简介外全部成员逐类保持（这一个变体只动简介维度）")
    raises(lambda: FV._check_synopses(tampered, _roots(ctx)),
           FV.FinalVerificationError, "的 synopsis reason_code 与独立复算不符",
           "表格-only 节点改回 legacy 理由码后必须被拒绝（新理由码是强制的，"
           "不是可选措辞；走生产校验函数 `_check_synopses`，与公开入口内是"
           "同一段代码）")
    # 身份维度：简介是成员，改一条简介必须改快照身份。
    #
    # `content_fingerprint` 只由 `member_ids()` 派生，所以"改一处即改身份"这条
    # 性质**逐维度**成立才算成立。简介维度曾经被漏掉：改写一条 final synopsis
    # 后 `snapshot_id` / `content_fingerprint` 逐字节不变，只看指纹的消费者看不见
    # 这次改动（`_check_synopses` 仍会拦，但那是"重新复核"而不是"身份变了"）。
    # 这里两件事都断言：成员集合确实含简介，且篡改件身份确实变了。
    members = final.member_ids()
    check(all(s.synopsis_id in members and s.synopsis_locator in members
              for s in final.synopses),
          "简介的 ID 与定位都在 `member_ids()` 里（漏一个维度，该维度上的"
          "篡改就对指纹隐身）")
    check(tampered.snapshot_id != final.snapshot_id and
          tampered.content_fingerprint != final.content_fingerprint,
          "只改一条简介也会改 `snapshot_id` 与 `content_fingerprint`"
          "（简介不是一个可以静默改写的旁路维度）")
    _results["details"].append(
        f"__synopsis_identity__ snapshot_id_changed="
        f"{tampered.snapshot_id != final.snapshot_id} "
        f"content_fingerprint_changed="
        f"{tampered.content_fingerprint != final.content_fingerprint} "
        f"member_ids_include_synopses="
        f"{any(s.synopsis_id in final.member_ids() for s in final.synopses)}")

    # 版本轴与来源轴的篡改：正常路径**构造不出来**（`__post_init__` 就拒），
    # 因此用旁路对象问"复核器自己有没有独立重算这两条轴"。若复核器改成采信成员
    # 自报的 `schema_version` / `source_final_span_ids`，下面三条会全部变成"没抛"。
    #
    # 两道闸**不是同一道**：wire 层（`to_dict` / schema 成员校验）会先按代际拒一次，
    # 复核器的 `_revalidate`（`from_dict(obj.to_dict())`）再独立重算一次。下面两条都
    # 断言，"改字段蒙混"才不是靠其中一道侥幸挡住。
    def _with_synopsis(member):
        """把一条简介换进**旁路**快照（绕过 `create` 的成员闭合检查）。

        走生产 `create`（`_rebuild_snapshot`）时，"来源悬空"那一条会先被 schema 层
        的成员闭合挡下，`_check_synopses` 根本到不了——那样测的是 schema，不是
        复核器。这里刻意绕过它，把问题直接交给复核器自己。

        按 `node_id` 对位（一个快照内 node_id 唯一且升序，见复核器自身断言）：
        被换进去的简介身份按构造被**重算过**，拿 `synopsis_id` 对位会找不到人。
        """
        return _forge(final, synopses=tuple(
            member if s.node_id == member.node_id else s
            for s in final.synopses))

    _forged_wire = _forge(hit, schema_version="nss-2")
    check(isinstance(_forged_wire, TS.FinalNavigationSynopsis)
          and _forged_wire.schema_version == "nss-2",
          "旁路件仍是 `FinalNavigationSynopsis` 实例、版本字段确已被改成 TS4 轴"
          "（挡它的既不是类型，也不是 `__post_init__`——后者已被绕过）")
    raises(lambda: _rebuild_snapshot(
               final, synopses=tuple(
                   _forged_wire if s.node_id == _forged_wire.node_id else s
                   for s in final.synopses)).to_dict(),
           SchemaValidationError, "final synopsis 的 schema_version 必须为",
           "同一个篡改件在 wire 层（`fms-1` 的 `to_dict` 逐成员校验）先被 schema "
           "拒（schema 层与复核器是两道独立的闸）")
    raises(lambda: FV._check_synopses(
               _with_synopsis(_forge(hit, schema_version="nss-2")), _roots(ctx)),
           FV.FinalVerificationError, "schema_version 为未知版本 'nss-2'",
           "把 final 简介的 wire 版本改回 TS4 轴（`nss-2`）后复核器独立重算即拒绝")
    raises(lambda: FV._check_synopses(
               _with_synopsis(_forge(hit, synopsis_version="ns-2")), _roots(ctx)),
           FV.FinalVerificationError, "synopsis_version 必须为 'ns-3'",
           "把 final 简介的算法版本改回 TS4 轴（`ns-2`）后复核器独立重算即拒绝")
    with_snippets = next((s for s in final.synopses if s.snippets), None)
    if with_snippets is not None:
        # 悬空来源必须**自洽地**悬空：`source_final_span_ids` 必须等于片段序，而
        # 片段身份又进简介的派生定位/身份（`table_schema` 的成员内自洽）。所以这里
        # 不手改字段，而是把改过的片段**交回生产 `create` 重算派生字段**——这样
        # `_revalidate`（`from_dict(to_dict())`）能过，问题才真正落到复核器"按本
        # 快照解析来源"那一步。schema 层只看形状（`fos-` + hex），判不了身份是否存在。
        bogus = tuple(f"fos-{i:016x}" for i in range(len(with_snippets.snippets)))
        _fields = _all_fields(with_snippets)
        _fields.update({
            "snippets": tuple(
                TS.FinalSynopsisSnippet(
                    final_span_id=sid, final_span_locator="loc-" + sid,
                    final_span_schema_version=sn.final_span_schema_version,
                    snippet_index=sn.snippet_index,
                    char_start=sn.char_start, char_end=sn.char_end, text=sn.text)
                for sn, sid in zip(with_snippets.snippets, bogus)),
            "source_final_span_ids": bogus})
        _dangling = TS.FinalNavigationSynopsis.create(**_fields)
        check(all(s.final_span_id in bogus for s in _dangling.snippets)
              and _dangling.source_final_span_ids == bogus,
              "悬空件的成员内自洽（片段序与来源列表一致、派生身份已重算），"
              "因此挡它的只能是复核器按本快照的解析")
        raises(lambda: FV._check_synopses(
                   _with_synopsis(_dangling), _roots(ctx)),
               FV.FinalVerificationError, "本快照以外的 final span",
               "把 final 简介的来源与片段身份一起改成快照里不存在的 `fos-` 身份后"
               "（成员内自洽，schema 层无从判断），复核器按本快照独立解析即拒绝"
               "（来源不得悬空）")


# ---------------------------------------------------------------------------
# G6. 四层守恒各自零差额（§19.12.6-7）
# ---------------------------------------------------------------------------

def _recompute_disposition(ctx) -> tuple:
    final = ctx["final"]
    verified = ctx["verified"]
    counts = {"absorbed_to_table": 0, "final_paragraph": 0,
              "pending_or_unsupported": 0}
    unknown = []
    for decision in final.decisions:
        target = _TARGET_OF.get(decision.decision)
        if target is None:
            unknown.append(decision.decision)
        elif target == "table":
            counts["absorbed_to_table"] += 1
        elif target == "final_span":
            counts["final_paragraph"] += 1
        else:
            counts["pending_or_unsupported"] += 1
    table_dispositions = [d for d in verified.snapshot.dispositions
                          if d.range_kind in TS.TABLE_RANGE_KINDS]
    return counts, len(table_dispositions), unknown, len(final.decisions)


def _recompute_component(ctx) -> tuple:
    final = ctx["final"]
    verified = ctx["verified"]
    counts = {"final_span": 0, "table_object": 0, "pending": 0, "rejected": 0}
    unknown = []
    for binding in final.bindings:
        if binding.admission not in counts:
            unknown.append(binding.admission)
            continue
        counts[binding.admission] += 1
    return counts, len(final.bindings), unknown, \
        len(verified.snapshot.components)


def _recompute_evidence_interval(ctx) -> tuple:
    final = ctx["final"]
    verified = ctx["verified"]
    by_component: dict = {}
    for binding in final.bindings:
        by_component.setdefault(binding.component_id, []).append(binding)
    counts = {"preserved": 0, "missing": 0, "duplicated": 0}
    total = 0
    for component in verified.snapshot.components:
        lo, hi = component.evidence_char_range
        length = int(hi) - int(lo)
        total += length
        mine = by_component.get(component.component_id, ())
        if not mine:
            counts["missing"] += length
        elif len(mine) > 1:
            counts["preserved"] += length
            counts["duplicated"] += (len(mine) - 1) * length
        elif tuple(mine[0].evidence_char_range) != \
                tuple(component.evidence_char_range):
            counts["missing"] += length
        else:
            counts["preserved"] += length
    return counts, total, [], len(verified.snapshot.components)


def _recompute_layout_claims(ctx) -> dict:
    """层 3 两个来源片段分项的独立复算（**精确字符数**，含坐标域裁剪）。"""
    final = ctx["final"]
    lengths = _layout_span_lengths(ctx)
    cell_ranges: dict = {}
    adorn_ranges: dict = {}
    unknown_refs = []
    for table in final.tables:
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    iv = ref.interval
                    key = (iv.page_number, iv.line_index, iv.span_index)
                    cell_ranges.setdefault(key, []).append(
                        tuple(iv.span_char_range))
        for blocks in (table.title_blocks, table.unit_blocks,
                       table.note_blocks):
            for block in blocks:
                for rid in block.source_ref_ids:
                    ref = table.source_ref_by_id(rid)
                    if ref is None:
                        unknown_refs.append(rid)
                        continue
                    iv = ref.interval
                    key = (iv.page_number, iv.line_index, iv.span_index)
                    adorn_ranges.setdefault(key, []).append(
                        tuple(iv.span_char_range))
    cell_total = 0
    for key, ranges in cell_ranges.items():
        limit = lengths.get(key)
        if limit is None:
            unknown_refs.append(key)
            continue
        cell_total += _union_len(_clamp(ranges, limit))
    adorn_total = 0
    for key, ranges in adorn_ranges.items():
        limit = lengths.get(key)
        if limit is None:
            unknown_refs.append(key)
            continue
        clipped = _clamp(ranges, limit)
        adorn_total += (_union_len(clipped) -
                        _intersect(clipped,
                                   _clamp(cell_ranges.get(key, ()), limit)))
    return {"cell_source_ref": cell_total,
            "caption_unit_note_ref": adorn_total,
            "unknown": unknown_refs,
            "keys": len(cell_ranges), "adorn_keys": len(adorn_ranges)}


def _g6_four_layer_conservation(ctx) -> None:
    final = ctx["final"]
    wrapper = ctx["wrapper"]

    check(tuple(layer.layer_kind for layer in final.conservation.layers) ==
          tuple(kind for kind, _ in _LAYERS),
          f"守恒恰为四层且按固定顺序 {tuple(k for k, _ in _LAYERS)}")
    check(TS.CONSERVATION_LAYER_KINDS == tuple(k for k, _ in _LAYERS) and
          TS.CONSERVATION_LAYER_TERMS == {k: v for k, v in _LAYERS} and
          TS.CONSERVATION_TERM_KIND_OF == _TERM_KIND,
          "测试自带的层 / 分项 / 计量类别字面量与 wire 词表逐项一致")
    check(TS.CONSERVATION_TERM_KINDS == ("count", "interval", "character"),
          f"分项计量类别恰为 {TS.CONSERVATION_TERM_KINDS}")
    check(TS.TABLE_RANGE_KINDS == ("table_inside", "table_adjacency") and
          TB.TABLE_DISPOSITION_KINDS == TS.TABLE_RANGE_KINDS,
          "表范围种类与 builder 侧同集（层 1 的合计口径一致）")
    check(TS.TABLE_DECISION_TARGETS == _TARGET_OF and
          set(TS.TABLE_DECISION_KINDS) == set(_TARGET_OF),
          "测试自带的裁决真值表与 wire 层 `TABLE_DECISION_TARGETS` 逐项一致")
    reverse: dict = {}
    for decision, target in _TARGET_OF.items():
        reverse.setdefault(target, []).append(decision)
    check(TS.DECISION_TARGET_OF == {k: tuple(v) for k, v in reverse.items()},
          "wire 层的逆查视图可由真值表独立复算（没有第二份手写映射）")
    check(set(FMB.ABSORBED_DECISIONS) | set(FMB.KEPT_DECISIONS) |
          set(FMB.STRUCTURAL_DECISIONS) == set(_TARGET_OF),
          "builder 的三分集合穷尽且互斥八类裁决"
          "（本次样本未出现的两类也不得豁免）")

    independent = {
        "disposition": _recompute_disposition(ctx),
        "component": _recompute_component(ctx),
        "evidence_interval": _recompute_evidence_interval(ctx),
    }
    claims = _recompute_layout_claims(ctx)
    layer3 = _layer_of(final, "layout_text")
    independent["layout_text"] = (
        {"cell_source_ref": claims["cell_source_ref"],
         "caption_unit_note_ref": claims["caption_unit_note_ref"]},
        _total_value(layer3), [], 0)

    problems: list = []
    for layer in final.conservation.layers:
        kind = layer.layer_kind
        want_counts, want_total, unknown, count_of = independent[kind]
        if set(_layer_values(layer)) != set(dict(_LAYERS)[kind]):
            problems.append((kind, "term_set", sorted(_layer_values(layer))))
            continue
        values = _layer_values(layer)
        total = _total_value(layer)
        if unknown:
            problems.append((kind, "unknown", unknown[:3]))
        if sum(values.values()) != total:
            problems.append((kind, "sum", (sum(values.values()), total)))
        # `balanced` 不再是"算术相等"的同义词：`fmc-2` 起它与公开的
        # `conservation_layer_eligible` 同语义（算术守恒 **且** 层内无问题
        # **且** 零必需项为零）。因此这里判的是**共享资格函数本身**，而不是
        # 另写一份等价条件——否则本组会在"算平但有问题码"的层上报假差异。
        eligible = TS.conservation_layer_eligible(kind, values, int(total),
                                                  layer.problems)
        if layer.balanced is not eligible:
            problems.append((kind, "balanced", (layer.balanced, eligible)))
        for name, value in want_counts.items():
            if values.get(name) != value:
                problems.append((kind, name, (values.get(name), value)))
        if count_of and total != want_total:
            problems.append((kind, "total", (total, want_total)))
    check_aggregate_allow_empty(
        problems, sum(len(layer.terms) for layer in final.conservation.layers),
        "四层守恒的每个分项都由测试**独立复算**（层 1 ← decisions + TS4 "
        "dispositions；层 2 ← bindings + components；层 3 ← 真实来源片段并集长度；"
        "层 4 ← components 的 Evidence 区间）")
    check(not claims["unknown"],
          f"层 3 复算覆盖全部来源片段与坐标（未识别 {claims['unknown'][:3]}）")
    check(claims["keys"] > 0 and claims["adorn_keys"] > 0,
          f"层 3 复算确有内容（cell 坐标组 {claims['keys']} / 表级块坐标组 "
          f"{claims['adorn_keys']}）")

    residual = _term_value(layer3, "residual_gap")
    total3 = _total_value(layer3)
    # `registered_deferred` 与 `non_semantic_whitespace` 同样是"已被解释的占用"：
    # 前者是带 typed gap 的精确延期，后者是原文整段恰为空白的精确范围。因此它们与
    # 三类来源主张一起从总额里扣除，剩下的才是真残余——残余不因新增分项而变小，
    # 只是"原本被混算成残余的空白/延期"回到了它们各自该在的项里。
    used = (claims["cell_source_ref"] + claims["caption_unit_note_ref"] +
            _term_value(layer3, "final_paragraph_ref") +
            _term_value(layer3, "registered_deferred") +
            _term_value(layer3, "non_semantic_whitespace"))
    check(sum(_layer_values(layer3).values()) == total3,
          "层 3 的六项之和恰等于合计（" + " + ".join(
              f"{k}={v}" for k, v in _layer_values(layer3).items()) +
          f" = {total3}）")
    check(residual == total3 - used,
          "层 3 的 `residual_gap` 恰为**表相关区域内没有任何正式主张**的字符数"
          f"（{residual} = {total3} - {used}，已扣除 registered_deferred "
          f"{_term_value(layer3, 'registered_deferred')} 与 "
          f"non_semantic_whitespace "
          f"{_term_value(layer3, 'non_semantic_whitespace')}）")
    check(residual > 0 and
          any(p.startswith("layout_text_residual_in_table:")
              for p in layer3.problems),
          "残留字符 > 0 且被**如实披露**为问题码（不得静默算平："
          f"residual={residual}, problems={list(layer3.problems)[:2]}）")

    layer_problems: list = []
    for layer in final.conservation.layers:
        layer_problems.extend(layer.problems)
    check(sorted(set(layer_problems)) == list(final.conservation.problems),
          "聚合的守恒问题恰好是四层问题的并集（不过滤、不补充、不去重丢失）")
    clean = [layer.layer_kind for layer in final.conservation.layers
             if layer.layer_kind in ("disposition", "component",
                                     "evidence_interval")
             and (layer.problems or not layer.balanced)]
    check(not clean, f"层 1 / 2 / 4 各自零差额且零问题（不一致处 {clean}）")
    layer4 = _layer_of(final, "evidence_interval")
    check(layer4.balanced and
          _term_value(layer4, "missing") == 0 and
          _term_value(layer4, "duplicated") == 0 and
          _term_value(layer4, "preserved") == _total_value(layer4) > 0,
          "层 4 全量 preserved、missing / duplicated 为零（非空样本上可证伪）")
    fired = {p.split(":", 1)[0] for p in layer3.problems}
    odd = sorted(fired - set(_LAYER3_PROBLEM_PREFIXES))
    check(not odd,
          f"层 3 的问题码全部落在登记形状内（越出登记表的 {odd}；"
          f"本次实得 {sorted(fired)}）")
    overlap = [p for p in layer3.problems
               if p.startswith("layout_text_overlap")]
    check(not overlap,
          f"层 3 没有 `layout_text_overlap`（同一片段不得被两类同时主张；"
          f"实得 {overlap[:3]}）")
    bounds = [p for p in layer3.problems
              if p.startswith("layout_text_ref_out_of_span:")
              or p.startswith("layout_text_hit_out_of_span:")]
    check(not bounds,
          f"层 3 没有坐标越界主张（主张区间一律落在片段内；实得 {bounds[:3]}）")
    # 本样本上**确实**报出了那份诚实披露。P1-2（列边界不得切开同一个真实 LayoutSpan）
    # 修掉上游成因后，同一片段上"两张表各用同一个 `(0, n)` 区间主张同一段字符"的情形
    # 在本样本上不再产生；取而代之的是 `layout_text_gap_interval_overlap`——同一段字符
    # 被两条区域型 typed gap 同时覆盖。两者都是"不猜、留在残余、如实记账"，因此这一条
    # 断言的是**当前真实的成因**在册，而不是某个特定码名。
    # `layout_text_duplicate_claim` 的**非空转**证明由
    # `evals/test_tree_table_conservation.py` 的"重复认领"合成反例承担（那里能用夹具
    # 直接造出重复主张并断言码名），本处不重复。
    check("layout_text_gap_interval_overlap" in fired
          and "layout_text_residual_in_table" in fired,
          "同一段字符被两条区域型缺口同时覆盖、以及区域内仍有未解释字符，"
          "都被如实报为登记在册的问题码"
          f"（这正是本样本不满足守恒资格的原因；实得 {sorted(fired)}）")

    # 真实文档侧：守恒资格**不**成立，公开入口必须 fail-closed、如实报出层与
    # 问题数，并且**不得**产出终端能力。签发侧的对照（复核问题为空、照常签发）
    # 走守恒资格成立的合成链。
    roots = _roots(ctx)
    cap = _capability_chain()
    cap_wrapper = cap["wrapper"]
    check(FV.final_material_problems(cap_wrapper) ==
          cap_wrapper.recheck_problems,
          "`final_material_problems` 与 wrapper 的复核问题同源（签发侧对照）")
    check(not cap_wrapper.recheck_problems,
          "复核器**独立重算**的四层守恒在守恒资格成立的样本上零差额"
          f"（recheck_problems 应为空，实得 {list(cap_wrapper.recheck_problems)[:3]}）")
    check(FV._recheck_conservation(cap["final"], _roots(cap)) == (),
          "复核器的守恒重算函数对**守恒资格成立**的合成快照返回空（反向对照，"
          "证明该函数不是恒报错）")

    check(wrapper is None and ctx["refusal"] is not None,
          f"真实文档（{_TRUST_KEY}）在 `fmc-2` 下**未**取得终端能力"
          f"（wrapper={wrapper!r}）")
    check(final.conservation.balanced is False,
          "真实文档的文档级 `balanced` 为 False（守恒资格不成立）")
    refusal = ctx["refusal"] or ""
    check("的最终材料守恒资格不成立" in refusal and
          "layout_text 层不成立" in refusal and
          "不签发 final capability（fail-closed）" in refusal,
          "拒绝文本点名到**层**并声明 fail-closed，不是笼统拒绝："
          f"{refusal[:160]!r}")
    check(f"residual_gap={residual}" in refusal,
          f"拒绝文本同时带出实测残余额（residual_gap={residual}）")

    recheck = FV._recheck_conservation(final, roots)
    check(bool(recheck),
          "复核器的守恒重算对真实快照**报出**层 3 差额（非空转）")
    off_layer = tuple(p for p in recheck
                      if "disposition 层" in p or "component 层" in p
                      or "evidence_interval 层" in p)
    check(not off_layer,
          "复核器重算在**层 1 / 2 / 4** 与记录值零差额（差异只可能来自层 3；"
          f"不一致处 {list(off_layer)[:2]}）")

    # 非空转：把一个分项改掉的自洽不平衡变体，独立重算必须报出来。
    tampered_layers = tuple(
        _rebuild_layer(layer, "pending_or_unsupported", -1)
        if layer.layer_kind == "disposition" else layer
        for layer in final.conservation.layers)
    bad_layer = tampered_layers[0]
    check(bad_layer.balanced is False and
          bad_layer.problems == ("layer_unbalanced:disposition",),
          "不平衡变体自身自洽（分项之和 != 合计，问题码同步置位）")

    class _Probe:
        """只带复核函数所需字段的探针（纯反例输入，不构造 wire 对象）。

        `tables` 是必需的：`fmc-2` 起层 3 由复核器**独立重算**，它要读快照里的
        表来重建"表相关文本域"的主张；`fmc-3` 起它还要读 `gaps`——层 3 的第二条
        `registered_deferred` 资格路径是**从缺口台账的 `source_intervals` 反推**的。
        缺任何一个，测的就不是生产那条路径。
        """

        def __init__(self, layers, tables):
            self.conservation = type("C", (), {"layers": layers})()
            self.decisions = final.decisions
            self.bindings = final.bindings
            self.tables = tables
            self.gaps = final.gaps

    reported = FV._recheck_conservation(
        _Probe(tampered_layers, final.tables), roots)
    check(bool(reported) and any("pending_or_unsupported" in p
                                 for p in reported),
          "复核器的独立重算**确实会报告**分项差额（非空转；实得 "
          f"{list(reported)[:2]}）")
    # 反向对照：层 1 / 2 / 4 原样（只把层 3 换成守恒资格成立的合成链那一份读数
    # 不可行——探针的 decisions / bindings 来自真实快照）。因此这里只断言"被改的
    # 那一项确实出现在报告里"，而不是断言整份报告为空。
    untouched = [p for p in reported
                 if "disposition 层" in p and
                 "pending_or_unsupported" not in p]
    check(not untouched,
          "报告里的层 1 差额**只**指向被改的分项（未经改动的分项不报；"
          f"实得 {untouched[:2]}）")

    _results["details"].append(
        f"__conservation__ layer1={_layer_values(final.conservation.layers[0])} "
        f"layer2={_layer_values(final.conservation.layers[1])} "
        f"layer3={_layer_values(layer3)} "
        f"layer4={_layer_values(layer4)} "
        f"layer3_problems={len(layer3.problems)} "
        f"claims={claims['cell_source_ref']}/{claims['caption_unit_note_ref']}")


# ---------------------------------------------------------------------------
# G7. 只读前置（§19.12.7-5）
# ---------------------------------------------------------------------------

def _g7_read_only(ctx) -> None:
    before = ctx["db_before"]
    now = _identity_bundle()
    check(now == before,
          "正式链跑完后两个库（含 -wal / -shm）的 size / mtime_ns / sha256 "
          "与链之前逐项相同")
    check(now["evidence"]["db"] is not None and
          now["financial"]["db"] is not None and
          now["evidence"]["db"]["size"] > 0 and
          now["financial"]["db"]["size"] > 0,
          "两个库都存在且非空（只读的确实是真实 repo 资产）")
    check(now["evidence"]["db"]["sha256"] != now["financial"]["db"]["sha256"],
          "Evidence 库与 Financial 库不是同一个文件（身份不混淆）")
    check(Path(_estore._db_path).resolve() == _EVIDENCE_DB.resolve(),
          f"链路读的是 repo 的 `data/evidence.db`"
          f"（{Path(_estore._db_path).resolve()}）")
    check(_identity_bundle() == before,
          "连续两次读取得到同一 identity（测量本身无副作用）")
    check(len(before) == 2 and set(before) == {"evidence", "financial"},
          "只读前置覆盖 evidence 与 financial 两个库")


# ---------------------------------------------------------------------------
# G8 续接关系的独立重证（§八(11)：合法续接是正例，跨表误链接是负例）
# ---------------------------------------------------------------------------

def _continuation_endpoint(table) -> "TS.TableEndpointRef":
    return TS.TableEndpointRef(
        table_locator=table.table_locator, table_id=table.table_id,
        document_id=table.document_id, page_layout_id=table.page_layout_id,
        outline_id=table.outline_id, page_number=table.page_number)


def _continuation_relation(base, nxt, *, proof_ids=None, kind="continued_by"):
    ids = tuple(sorted({base.table_locator, nxt.table_locator,
                        base.table_id, nxt.table_id}
                       | set(TS.CONTINUATION_PROOF_KINDS)))
    return TS.TableRelation.create(
        schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
        relation_builder_version=TS.TABLE_RELATION_BUILDER_VERSION,
        upstream_dependency_fingerprint=base.upstream_dependency_fingerprint,
        relation_kind=kind, source_endpoint=_continuation_endpoint(base),
        target_endpoint=_continuation_endpoint(nxt),
        relation_proof_ids=ids if proof_ids is None else proof_ids)


def _continuation_snapshot(ctx, *, nxt_over=None, tables_extra=(),
                           proof_ids=None, kind="continued_by"):
    """真实表 + 派生续表 + 一条 `continued_by` 的旁路快照（`_forge` 只用于问复核器）。

    基表取页号最大的真实表：`page+1` 上不可能有任何真实表，于是"中间夹了别的表"
    在构造上不成立，正例才是干净的。
    """
    final = ctx["final"]
    base = max(final.tables, key=lambda t: (t.page_number, t.source_order_index))
    x0, y0, x1, y1 = base.page_bbox
    over = {"table_id": base.table_id[:-4] + "-ctl",
            "table_locator": base.table_locator + "-ctl",
            "page_number": base.page_number + 1,
            "page_bbox": (x0, y0 + 4.0, x1, y1 + 4.0)}
    over.update(nxt_over or {})
    nxt = _forge(base, **over)
    rel = _continuation_relation(base, nxt, proof_ids=proof_ids, kind=kind)
    snap = _forge(final, tables=tuple(final.tables) + (nxt,) + tuple(tables_extra),
                  relations=tuple(final.relations) + (rel,))
    return base, nxt, rel, snap


def _g8_continuation_reproof(ctx) -> None:
    roots = _roots(ctx)
    final = ctx["final"]

    # 前置（§19.5.1 相邻冻结范围裁定落地后改写）：真实现场**已经**出现 `continued_by`
    # ——图侧不再只靠合成夹具提供续接样本。因此这里不再断"现场一条都没有"（那是裁定
    # 落地前的旧读数），改为把这些**真实**续接逐条推上同一条独立重证路径：六条证明
    # 条件必须齐备，且原样快照过复核器的关系重证不得报问题。现场若一条都没有，则如实
    # 记 INFO（重证路径只由下面的合成正例覆盖），**不**据此判失败。
    real = tuple(r for r in final.relations if r.relation_kind == "continued_by")
    for rel in real:
        named = tuple(p for p in rel.relation_proof_ids
                      if p in TS.CONTINUATION_PROOF_KINDS)
        check(set(named) == set(TS.CONTINUATION_PROOF_KINDS),
              "G8 前置：真实现场的每条 `continued_by` 必须把六条证明条件全部列为"
              f"待重证项（{rel.relation_id} 实得 {sorted(set(named))}）")
    err_real, text_real = _capture(
        lambda: FV._check_relations(final, roots), FV.FinalVerificationError)
    check(err_real is None,
          f"G8 前置：真实现场的 {len(real)} 条 `continued_by` 在原样快照上必须通过"
          f"独立重证（得到 {type(err_real).__name__}: {text_real[:300]}）")
    if real:
        _results["details"].append(
            f"INFO 真实现场 {_TRUST_KEY} 的 `continued_by` 条数 = {len(real)}"
            f"（表 {len(final.tables)} 张 / 关系 {len(final.relations)} 条），"
            f"已逐条走独立重证并通过")
    else:
        _results["details"].append(
            f"INFO 真实现场 {_TRUST_KEY} 没有 `continued_by`"
            f"（重证路径只由合成正例覆盖）")

    # 正例：六条条件全部成立 ⇒ 必须通过独立重证。
    base, nxt, rel, snap = _continuation_snapshot(ctx)
    err, text = _capture(lambda: FV._check_relations(snap, roots),
                         FV.FinalVerificationError)
    check(err is None,
          f"G8 正例：六条证明条件全部成立的续接必须通过独立重证"
          f"（得到 {type(err).__name__}: {text[:400]}）")
    check(rel.relation_proof_ids
          == tuple(sorted(set(rel.relation_proof_ids))),
          "G8 正例：关系证明项按字典序（schema 已强制，此处复核构造件本身）")

    def _mislink(name, want, **kw):
        _, _, _, bad = _continuation_snapshot(ctx, **kw)
        _, text2 = _capture(lambda: FV._check_relations(bad, roots),
                            FV.FinalVerificationError)
        check(want in text2, f"G8 负例：{name}（得到 {text2[:300]!r}）")

    # 邻接性：目标表不在紧邻的下一页。
    _mislink("目标表不在紧邻下一页时不得判为续接",
             "adjacent_page_or_explicit_occurrence",
             nxt_over={"page_number": base.page_number + 2})
    # 身份（第一道闸，构造期）：跨文档的 `continued_by` **造不出来**——关系类型自己
    # 就拒绝跨文档合成逻辑大表。这一条断言的是"更早的那道闸"，不是复核器的重证。
    _, cross = _capture(
        lambda: _continuation_relation(
            base, _forge(base, table_id=base.table_id[:-4] + "-x",
                         table_locator=base.table_locator + "-x",
                         page_number=base.page_number + 1,
                         document_id=base.document_id + "-other")),
        SchemaValidationError)
    check(cross != "" and "same document" in cross,
          f"G8 负例：跨文档的续接关系在构造期即被拒绝（得到 {cross[:200]!r}）")
    # 身份（第二道闸，复核期）：文档身份是**五元组**（含版本 / Evidence 集 / 版式 /
    # 标题树）。关系本身照常构造（端点自洽，因此**不**触发第一道闸），只把快照里的
    # 目标表改成另一个版式身份——这样被问到的**只有**复核器的 `same_document_identity`。
    base_id, nxt_id, rel_id, _snap_id = _continuation_snapshot(ctx)
    forged_nxt = _forge(nxt_id, page_layout_id=base_id.page_layout_id + "-other")
    bad = _forge(final, tables=tuple(final.tables) + (forged_nxt,),
                 relations=(rel_id,))
    _, text_bad = _capture(lambda: FV._check_relations(bad, roots),
                           FV.FinalVerificationError)
    check("same_document_identity" in text_bad,
          "G8 负例：同一文档号但版式身份不同的表不得判为续接——关系照常构造，"
          "被问到的只有复核器的五元组身份判据"
          f"（得到 {text_bad[:300]!r}）")
    # 表头/列带/单位：同页相邻但形状不同。
    _mislink("表头单位不兼容的同页相邻表不得判为续接",
             "compatible_column_header_unit",
             nxt_over={"unit_text": (base.unit_text or "") + "亿"})
    # 中间夹了别的结构（页号是整数，因此这条必然同时不满足邻接性，如实并列）。
    other = _forge(
        max(final.tables, key=lambda t: (t.page_number, t.source_order_index)),
        table_id="to4-intervening", table_locator="loc-to4-intervening",
        page_number=base.page_number + 1,
        page_bbox=(base.page_bbox[0], base.page_bbox[1] + 9.0,
                   base.page_bbox[2], base.page_bbox[3] + 9.0))
    far = _forge(
        max(final.tables, key=lambda t: (t.page_number, t.source_order_index)),
        table_id="to4-far", table_locator="loc-to4-far",
        page_number=base.page_number + 2,
        page_bbox=(base.page_bbox[0], base.page_bbox[1] + 18.0,
                   base.page_bbox[2], base.page_bbox[3] + 18.0))
    rel_far = _continuation_relation(base, far)
    bad_between = _forge(final,
                         tables=tuple(final.tables) + (other, far),
                         relations=tuple(final.relations) + (rel_far,))
    _, text3 = _capture(lambda: FV._check_relations(bad_between, roots),
                        FV.FinalVerificationError)
    check("no_intervening_structure" in text3,
          f"G8 负例：中间夹了别的表时不得判为续接（得到 {text3[:300]!r}）")
    check(len(bad_between.tables) == len(final.tables) + 2,
          "G8 负例：中间夹表夹具确实把两张派生表放进了快照")

    # 声明不完整：条件名是**待重证项**，少一条即拒。
    _mislink("只声明部分证明条件时不得判为续接", "缺",
             proof_ids=("adjacent_page_or_explicit_occurrence",))
    # 链结构：一个表两个后继。
    base2, nxt2, rel2, snap2 = _continuation_snapshot(ctx)
    third = _forge(nxt2, table_id=nxt2.table_id + "-2",
                   table_locator=nxt2.table_locator + "-2",
                   page_number=nxt2.page_number, page_bbox=nxt2.page_bbox)
    rel3 = _continuation_relation(base2, third)
    two_succ = _forge(final, tables=tuple(final.tables) + (nxt2, third),
                      relations=tuple(final.relations) + (rel2, rel3))
    _, text4 = _capture(lambda: FV._check_relations(two_succ, roots),
                        FV.FinalVerificationError)
    check("后继" in text4,
          f"G8 负例：一个表有两个后继时链结构不成立（得到 {text4[:300]!r}）")

    # 跨表误链接：端点指向本快照以外的表。关系件照常构造（改**端点**会让
    # `relation_locator` 与端点定位不一致，被 schema 自己先拒——那是另一道闸），
    # 改为把目标表从快照的 `tables` 里拿掉：端点的可回查性当场失效。
    _, nxt6, rel6, snap6 = _continuation_snapshot(ctx)
    kept = tuple(t for t in snap6.tables if t.table_id != nxt6.table_id)
    without_target = _forge(snap6, tables=kept, table_count=len(kept))
    _, text5 = _capture(lambda: FV._check_relations(without_target, roots),
                        FV.FinalVerificationError)
    check("本快照以外的表" in text5,
          f"G8 负例：续接端点指向快照以外的表必须被拒（得到 {text5[:300]!r}）")

    # 条件名不得出现在别的关系种类上（防御性分支：生产路径不会产生，但不得放行）。
    ref = base.source_refs[0]
    caption = TS.TableRelation.create(
        schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
        relation_builder_version=TS.TABLE_RELATION_BUILDER_VERSION,
        upstream_dependency_fingerprint=base.upstream_dependency_fingerprint,
        relation_kind="caption_of",
        source_endpoint=TS.ComponentEndpointRef(
            component_id=ref.component_id,
            component_locator=ref.component_locator,
            evidence_block_id=ref.evidence_block_id,
            evidence_char_range=ref.evidence_char_range,
            terminal_kind=ref.terminal_kind, terminal_id=ref.terminal_id,
            role="caption", table_id=base.table_id),
        target_endpoint=_continuation_endpoint(base),
        relation_proof_ids=tuple(sorted(
            {"same_document_identity", ref.source_ref_id, base.table_id,
             base.table_locator})))
    bad_kind = _forge(final,
                      relations=tuple(final.relations) + (caption,))
    _, text6 = _capture(lambda: FV._check_relations(bad_kind, roots),
                        FV.FinalVerificationError)
    check("只允许出现在 continued_by" in text6,
          f"G8 负例：证明条件名只允许出现在 continued_by 关系上"
          f"（得到 {text6[:300]!r}）")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------

_GROUPS = (
    ("G1-unique-chain", _g1_unique_chain),
    ("G2-independence-ast", _g2_independence_ast),
    ("G3-builder-monkeypatch", _g3_builder_monkeypatch),
    ("G4-tamper-rejected", _g4_tamper_rejected),
    ("G5-synopsis-scope", _g5_synopsis_scope),
    ("G6-four-layer-conservation", _g6_four_layer_conservation),
    ("G7-read-only", _g7_read_only),
    ("G8-continuation-reproof", _g8_continuation_reproof),
)


def _run_group(name, fn, ctx) -> None:
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
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _identity_bundle()
    ctx = None
    try:
        _estore._db_path = _EVIDENCE_DB
        ctx = _chain()
        ctx["db_before"] = before
        for name, fn in _GROUPS:
            _run_group(name, fn, ctx)
    except Exception as exc:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL 真实现场链 / 运行器异常：{type(exc).__name__}: {exc}")
    finally:
        _estore._db_path = previous_db_path
        check(before == _identity_bundle(),
              "只读前置：`data/evidence.db` 与 `data/financial_v2.db`（含 "
              "-wal / -shm）在本模块前后逐项不变（size / mtime_ns / sha256）")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

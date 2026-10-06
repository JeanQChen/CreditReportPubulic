# -*- coding: utf-8 -*-
"""TS5 §19.9.3 / §19.12.2A-8 / §19.12.6-1,2,3,4,5,6,8：守恒、逐跳来源回查与终态等集。

全部离线：只读本地 PDF 与**只读** `data/evidence.db` `data/financial_v2.db`；
无网络、无 LLM、无 Bocha、不写任何文件。

覆盖（矩阵条目 → 分组）：

- **§19.9.3 wire 层契约**（`K1-wire-contract`）：四层守恒的种类、顺序、分项与计量
  类别与**测试自带字面量**逐项一致；分项表**跨层不相交**（"不得跨层相加"首先要求
  同名分项不可能同时属于两层）；`CONSERVATION_TOTAL_TERM` 不得登记为分项；
  `ConservationTerm` 的"只填本类量"约束、`ConservationLayer` 的分项顺序 /
  `balanced == 实测` / "不平衡必须留问题码"、`FinalMaterialConservation` 的四层
  固定顺序与"不平衡层必须留问题码"逐条用构造期反例证明。
- **§19.12.2A-8 + §19.12.6-3**（`K2-three-level-catches`）：删一个 component、
  重复一个 component、改一个 Evidence 子区间端点、从账本删掉一个 rejected 来源、
  删一条 decision、删一条 source fragment、删一条 gap——**每一类扰动**都在三级
  （层重算 / 复核器 `_recheck_conservation` / 公开入口 `verify_final_material_snapshot`）
  中被抓出；并断言对应生产判据**不是空转**（同一路径对**未扰动**的真实 bindings
  不报第 2 / 第 4 层不符）。注意真实件本身在第 3 层带着实测的未解释残余，公开入口
  据此 fail-closed 拒绝签发——这是**正确**结果，本模块因此不再断言"真实件被签发"，
  而是把拒绝本身当作待核对的证据（见 K2 的对照组与 K7）。
- **§19.12.6-1**（`K3-per-hop-provenance`）：逐 cell source ref 的四跳回查
  （LayoutSpan → TS4 component → Evidence range → alignment/refusal 终态），
  每一跳都从**根读数**独立解析并逐项比较，`source_ref_id` 由身份载荷独立复算。
- **§19.12.6-2**（`K4-mixed-citability`）：aligned / non-citable 混合表不得被整表
  授权——`all_cells_citable` 必须等于实测、`citable_char_count` 独立复算、
  `non_citable_reasons` 必须等于实测理由集；把一条区间翻面而不改理由集即被构造期
  拒绝；非空转地用一条"全 citable"覆盖对上一张含 non-citable 片段的表证明复核器
  **确实会报**。
- **§19.12.6-4 / -5 / -6**（`K5-exact-set-landing`）：bindings 与 TS4 components
  精确等集且每个只出现一次；终态 ∈ `COMPONENT_LANDING_ADMISSIONS[landing]` 且四类
  终态构成划分；继承 / formal / body-unassigned 以外的 landing **不得**拿到
  `table_object`（尤其 `body_span` 仍只允许 final_span/pending，"regular body span
  不被偷换"）；进入表的 component 必须有真实 cell source ref 且逐个可回查。
- **§19.12.6-8**（`K6-gap-honesty`）：pending 的 component 必须留下缺口、缺口
  不得引用本快照以外的对象、阻断标记必须与种类同源、`blocking_entry_count` 必须
  为实测值；删除一条 pending 缺口即被复核器拒绝。
- **§19.9.3 `fmc-2` 受控扩展 → `fmc-3` 资格后继**（`K7-fmc2-vocabulary`）：
  `registered_deferred` 与 `non_semantic_whitespace` 只在 current 词表里出现
  （legacy 副本没有它们）；`conservation_layer_eligible` 是算术 / `problems` 空 /
  必须为零的分项为零三条合取；legacy 载荷（`fmc-1` 与作为上一代 current 的
  `fmc-2`）走 current reader 一律 fail-closed、走显式历史入口可读回且标
  `historical_only`，且附注必须点名当前版本与资格差异；延期资格函数对合法键入账，
  对无归属 / 范围错配 / 重复归属 / 已被消费 / 错 gap 五类情形逐条拒绝；
  `non_semantic_whitespace` 的唯一判据是「区间原文整段恰为空白」，且它**只能**从
  原本落入 `residual_gap` 的区间取值——有凭据的延期优先于它，正文残余 /
  无 typed gap / 重复认领三类情形仍逐条阻止 `balanced`（见第 6 节的三组反例）。
- **§六 `tsg-2` 精确缺口源区间**（`K8-gap-source-intervals`）：真实件上 449 条缺口、
  4131 段区间，独立复核器逐条接受且 `registered_deferred` 由独立重算得到同一个值
  （正例，说明这条资格路径不是空转）；指错片段 / 越界 / 写宽覆盖已被 cell 消费的字符 /
  两条缺口重复消费同一段字符，各自被独立复核器指名拒绝；同一缺口内部的重复与重叠在
  构造期即被挡；源区间参与快照内容指纹；文档版本与经验证 roots 不符时被驳回。

每个 `check` 都设计成"对应生产逻辑被移除即失败"：真值表 / 守恒分项 / 理由集都用测试
自带的字面量与根读数重算，反例一律断言**具体错误类型 + 具体错误文本**。

样本取 `NDSD_KCZ_2026`（三份真实 PDF 中最小且含 6 张表），与
`test_tree_final_material`（`NDSD_2024_year`）不同份，两条真实链互为对照。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

from evidence import store as _estore

from evals import tree_stage_env as STAGE
from document_structure import aligner as AL
from document_structure import final_material_builder as FMB
from document_structure import final_verifier as FV
from document_structure import layout_builder as LB
from document_structure import span_builder as SB
from document_structure import span_verifier as SV
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
# 0. 测试自带真值表（与 wire 层逐项比对，不复用生产常量作为期望）
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
#: §19.13：三份真实 PDF 中最小且含表的一份；与 `test_tree_final_material`
#: （`NDSD_2024_year`）刻意不同份，两条真实链互为对照。
_TRUST_KEY = "NDSD_KCZ_2026"

#: §19.9.3 的四层守恒。**测试自带**字面量（顺序即契约）。
#:
#: `fmc-2`：`layout_text` 层新增 `registered_deferred`（已被正式 typed gap 延期的
#: 字符）与 `non_semantic_whitespace`（整段恰为空白的精确范围）。
#:
#: `registered_deferred` 与 `residual_gap` 的区别是**资格**而非口径：只有"恰好被一个
#: 终态为 pending/rejected 的 component 覆盖、且缺口台账里有与它精确对应的 typed
#: gap"的字符才允许计入；任何一条不成立仍必须留在 `residual_gap` 并阻止本层
#: balanced。`non_semantic_whitespace` 的判据只有一条且只读原文（`.strip() == ""`）。
#: 两项都**不是**兜底项，本模块下面用构造期反例逐条证明这一点。
_LAYERS = (
    ("disposition", ("absorbed_to_table", "final_paragraph",
                     "pending_or_unsupported")),
    ("component", ("final_span", "table_object", "pending", "rejected")),
    ("layout_text", ("cell_source_ref", "caption_unit_note_ref",
                     "final_paragraph_ref", "registered_deferred",
                     "non_semantic_whitespace", "residual_gap")),
    ("evidence_interval", ("preserved", "missing", "duplicated")),
)
_TOTAL = "total"
_TERM_KIND = {
    "absorbed_to_table": "count", "final_paragraph": "count",
    "pending_or_unsupported": "count",
    "final_span": "count", "table_object": "count", "pending": "count",
    "rejected": "count",
    "cell_source_ref": "character", "caption_unit_note_ref": "character",
    "final_paragraph_ref": "character", "registered_deferred": "character",
    "non_semantic_whitespace": "character",
    "residual_gap": "character",
    "preserved": "interval", "missing": "interval", "duplicated": "interval",
}
_KIND_FIELD = {"count": "count", "character": "char_count",
               "interval": "interval_length"}

#: §19.9.1 的 11 类 landing → 允许终态（**测试自带**真值表）。
_LANDING_ADMISSIONS = {
    "body_span": ("final_span", "pending"),
    "table_inside": ("table_object", "final_span", "pending"),
    "table_adjacency": ("table_object", "final_span", "pending"),
    "body_unassigned": ("table_object", "pending"),
    "body_empty": ("rejected", "pending"),
    "heading_node": ("rejected", "pending"),
    "formal_unassigned": ("table_object", "pending"),
    "non_content": ("rejected", "pending"),
    "outside_body": ("rejected", "pending"),
    "alignment_offset_unverifiable": ("pending",),
    "alignment_residue_unmapped": ("pending",),
}
#: 进入表**必须**有真实 cell proof 的三类"非表内" landing（§19.12.6-5）。
_NON_TABLE_LANDINGS = ("body_unassigned", "formal_unassigned")
_ADMISSIONS = ("final_span", "table_object", "pending", "rejected")

_STATE: dict = {}


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

    `fmc-2` 起，公开复核入口在守恒资格不成立时**拒绝签发**。本样本是真实文档，
    它的 `layout_text` 层带着实测未解释残余（详见只读探针与 §八 报告），因此在
    这里被 fail-closed 拒绝是**正确行为**，不是本模块要修的东西：本模块证明的是
    守恒/回查/落点/缺口这四类**结构**判据本身不空转。因此这里如实记录两条结果
    ——结构复核路径的返回（`wrapper` 或拒绝文本）——而不是把拒绝当成异常咽掉。
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
    _STATE.update({"verified": verified, "final": final, "wrapper": wrapper,
                   "refusal": refusal,
                   "handoff": handoff, "ident": ident, "roots": None})
    return _STATE


def _roots(ctx) -> "FV._Roots":
    if ctx["roots"] is None:
        profiles = FV.load_table_profile_bundle()
        geometry = TG.extract_table_geometry(
            ctx["verified"].handoff.layout_capability)
        ctx["roots"] = FV._Roots(ctx["verified"], profiles, geometry)
    return ctx["roots"]


# ---------------------------------------------------------------------------
# 2. 通用自洽重建件（派生字段一律交给生产 create 重算）
# ---------------------------------------------------------------------------

def _all_fields(obj) -> dict:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def _rebuild_snapshot(snapshot, **over):
    kw = _all_fields(snapshot)
    kw.update(over)
    return TS.FinalMaterialStructureSnapshot.create(**kw)


def _rebuild_conservation(conservation, **over):
    kw = _all_fields(conservation)
    kw.update(over)
    return TS.FinalMaterialConservation.create(**kw)


def _construct_conservation(conservation, **over):
    """**直接**构造（不经 `create`）：用于"派生字段被自报"的反例。

    `create` 的职责就是**覆盖**派生字段（locator / id / 指纹都由类型自己的载荷方法
    重算），所以"改一个 locator 看它是否被拒"必须绕开 `create`——否则测的是
    "`create` 会纠正"而不是"改值会被拒"。
    """
    kw = _all_fields(conservation)
    kw.update(over)
    return TS.FinalMaterialConservation(**kw)


def _term(term: str, value: int) -> "TS.ConservationTerm":
    kind = _TERM_KIND[term]
    return TS.ConservationTerm(
        term=term, term_kind=kind,
        count=(value if kind == "count" else 0),
        char_count=(value if kind == "character" else 0),
        interval_length=(value if kind == "interval" else 0))


def _total_term(layer_kind: str, value: int) -> "TS.ConservationTerm":
    kind = _TERM_KIND[_LAYERS_BY_KIND[layer_kind][0]]
    return TS.ConservationTerm(
        term=_TOTAL, term_kind=kind,
        count=(value if kind == "count" else 0),
        char_count=(value if kind == "character" else 0),
        interval_length=(value if kind == "interval" else 0))


_LAYERS_BY_KIND = {k: v for k, v in _LAYERS}

#: 允许**完全没有锚点**的缺口条目。缺口按定义必须指向某个具体对象（页 / 表 /
#: component / disposition）——"有缺口"不能是一句泛泛的话。唯一的例外是**文档级**
#: 缺口：它描述的是"整份文档的布局文本残余没有被表格消化"这类没有单一对象可指的
#: 事实。这里逐项显式列出：出现**新的**无锚点缺口码即测试失败，必须由人判断它是
#: 文档级事实还是"把定位丢了"。当前列表由只读探针在 `NDSD_KCZ_2026` 上实测得到，
#: 且下面断言它在本样本中确实出现（不是空转豁免）。
#:
#: 本样本上实测出现的两项都是第 3 层守恒的问题码（`_build_gap_entries` 把守恒层问题码
#: 原样登记为文档级 `provenance_incomplete`）：
#:
#: - `layout_text_residual_in_table`：表相关文本域里仍有未解释字符——它描述的是"整片
#:   区域内没人认领的字符"这类没有单一对象可指的事实；
#: - `layout_text_gap_interval_overlap`（`fmc-3` 起）：同一段字符被**两条**区域型 typed
#:   gap 同时覆盖，按 §19.5.3 的顺序**不猜**，留在 `residual_gap` 并记账。它同样是两份
#:   主张之间的冲突（谁都不比谁更该认领），没有单一 component / table 可以指。
#:   它**不**携带 `source_intervals`：这条缺口只声明"这里存在覆盖歧义"，不主张任何字符，
#:   因此一个字符既不会被它"洗白"，也不会被它二次主张。
#:
#: `layout_text_duplicate_claim` 曾经也在本表里（同一 `(页, 行, 片段)` 上两张表用同一个
#: `(0, n)` 区间主张同一段字符）。TS5 的 P1-2（列边界不得切开同一个真实 LayoutSpan）
#: 修掉上游成因后，本样本上不再产生任何重复主张——一个跨列的片段现在成为**一个**
#: `colspan` 单元格，而不是两个各占一半、区间完全相同的单元格。它因此从"实测出现"
#: 的名单里退出；下面的非空转断言会盯着这一点：哪天它回来了，这里必须重新登记。
_DOCUMENT_SCOPED_GAP_CODES = {
    ("provenance_incomplete", "layout_text_residual_in_table"),
    ("provenance_incomplete", "layout_text_gap_interval_overlap"),
}


def _layer(layer_kind: str, values: dict, total: int,
           problems=()) -> "TS.ConservationLayer":
    """构造一个自洽的层。

    `balanced` 不再由本帮助函数"自报"，而是调**生产的**共用资格函数
    `TS.conservation_layer_eligible` 求值——构造期会独立重算同一语义并拒绝不一致
    的载荷，所以这里用生产的判据而不是本地重写一遍算术比较。
    """
    terms = tuple(_term(name, int(values.get(name, 0)))
                  for name in _LAYERS_BY_KIND[layer_kind])
    total_term = _total_term(layer_kind, int(total))
    problems = tuple(problems)
    balanced = TS.conservation_layer_eligible(
        layer_kind, values, int(total), problems)
    return TS.ConservationLayer(
        layer_kind=layer_kind, terms=terms, total=total_term, balanced=balanced,
        problems=problems if (problems or balanced)
        else (f"layer_unbalanced:{layer_kind}",))


def _layer_of(snapshot, kind):
    for layer in snapshot.conservation.layers:
        if layer.layer_kind == kind:
            return layer
    return None


def _term_value(layer, term) -> int:
    for item in layer.terms:
        if item.term == term:
            return getattr(item, _KIND_FIELD[_TERM_KIND[term]])
    return None


def _total_value(layer) -> int:
    return getattr(layer.total,
                   _KIND_FIELD[_TERM_KIND[_LAYERS_BY_KIND[layer.layer_kind][0]]])


def _layer_values(layer) -> dict:
    return {item.term: _term_value(layer, item.term) for item in layer.terms}


class _ConservationProbe:
    """只带复核函数所需字段的探针（纯反例输入，不构造 wire 对象）。

    `final_verifier._recheck_conservation` 读 `conservation` / `decisions` /
    `bindings` / `tables`，并在 `fmc-3` 起复核第 3 层时要读 `gaps`（它**从载荷反推**
    缺口台账的 `source_intervals`，不复用 builder 的区间计划）。因此这里如实提供
    这五处只读视图——除被本组刻意扰动的那一处，其余一律取自**真实快照**，所以探针
    仍走生产代码的**同一条**重算路径，而不是"拿一个空壳让复核器过不去"。少给一处
    视图会立刻 `AttributeError`（本组就是这么发现 `gaps` 被新增进来的）。
    """

    def __init__(self, final, conservation, decisions, bindings):
        self.tables = final.tables
        self.conservation = conservation
        self.decisions = decisions
        self.bindings = bindings
        self.gaps = final.gaps


def _recheck_real(ctx, roots) -> tuple:
    """对**未扰动**的真实 bindings 走同一条重算路径，返回问题码。"""
    final = ctx["final"]
    return tuple(FV._recheck_conservation(
        _ConservationProbe(final, final.conservation, final.decisions,
                           final.bindings), roots))


def _count_mismatches(problems) -> tuple:
    """真实件上**由数量差异**引起的问题码（第 2 / 第 4 层）。

    真实件本身在**第 3 层**就有实测问题（表相关文本域里的未解释残余），因此"重算
    返回空"不再是可用的对照。本组扰动全部打在 component / evidence 这两层上，所以
    对照要问的是：真实件上**这两层**是否干净。第 3 层的既有问题在扰动前后都在，
    不构成本组差异的来源。
    """
    return tuple(p for p in problems
                 if "component 层" in p or "evidence_interval 层" in p)


# ---------------------------------------------------------------------------
# K1. §19.9.3 wire 层守恒契约
# ---------------------------------------------------------------------------

def _k1_wire_contract(ctx) -> None:
    check(TS.CONSERVATION_LAYER_KINDS == tuple(k for k, _ in _LAYERS),
          f"四层守恒的种类与顺序恰为 {tuple(k for k, _ in _LAYERS)}")
    check(TS.CONSERVATION_LAYER_TERMS == {k: v for k, v in _LAYERS},
          "每层的分项表与测试自带字面量逐项一致（含固定顺序）")
    check(TS.CONSERVATION_TERM_KIND_OF == _TERM_KIND,
          "分项 → 计量类别表与测试自带字面量逐项一致")
    check(TS.CONSERVATION_TERM_KINDS == ("count", "interval", "character"),
          f"计量类别恰为 {TS.CONSERVATION_TERM_KINDS}")
    check(TS.CONSERVATION_TOTAL_TERM == _TOTAL,
          f"合计名恰为 {_TOTAL!r}")

    # 跨层不相交：这是"不得跨层相加"的**结构前提**——同名分项若同时属于两层，
    # 任一"合计"都可以被另一层的数字凑平。
    seen: dict = {}
    dupes = []
    for kind, terms in _LAYERS:
        for term in terms:
            if term in seen:
                dupes.append((term, seen[term], kind))
            seen[term] = kind
    check(not dupes, f"四层的分项名**两两不相交**（跨层串用 {dupes[:3]}）")
    check(_TOTAL not in seen and _TOTAL not in TS.CONSERVATION_TERM_KIND_OF and
          all(_TOTAL not in terms for _k, terms in _LAYERS),
          f"合计名 {_TOTAL!r} 既不是任何一层的分项，也不登记为分项，"
          "更不得混入任何一层的分项表")

    # 分项：只允许填自己那一类量。
    for term in ("absorbed_to_table", "final_span", "preserved"):
        kind = _TERM_KIND[term]
        wrong = {"count": "char_count", "character": "count",
                 "interval": "count"}[kind]
        kwargs = {"term": term, "term_kind": kind, "count": 0,
                  "char_count": 0, "interval_length": 0}
        kwargs[_KIND_FIELD[kind]] = 1
        kwargs[wrong] = 1
        raises(lambda kw=kwargs: TS.ConservationTerm(**kw),
               SchemaValidationError, "只能填对应的量",
               f"分项 {term!r}（{kind}）填了另一类量即被构造期拒绝")
        # 未知分项：查不到类别 → fail-closed（借 total 之外的自由名塞分项不可能）。
        raises(lambda: TS.ConservationTerm(
            term="not_a_registered_term", term_kind="count",
            count=1, char_count=0, interval_length=0),
            SchemaValidationError, "语义类别",
            "未登记的分项名被构造期拒绝（没有自由分项的口子）")
    for kind, terms in _LAYERS:
        for name in terms:
            assert _TERM_KIND[name] == TS.CONSERVATION_TERM_KIND_OF[name]

    # 层：分项顺序固定；顺序错即拒绝。
    real_layer = _layer_of(ctx["final"], "component")
    swapped = tuple(reversed(real_layer.terms))
    raises(lambda: TS.ConservationLayer(
        layer_kind="component", terms=swapped, total=real_layer.total,
        balanced=real_layer.balanced, problems=real_layer.problems),
        SchemaValidationError, "分项必须恰为",
        "层分项顺序被打乱即被构造期拒绝（顺序本身是契约）")
    raises(lambda: TS.ConservationLayer(
        layer_kind="not_a_layer", terms=real_layer.terms,
        total=real_layer.total, balanced=True, problems=()),
        SchemaValidationError, "layer_kind 必须属于",
        "未登记的层名被构造期拒绝")
    # 不平衡必须留问题码（不得静默）。
    raised = _capture(lambda: TS.ConservationLayer(
        layer_kind="component",
        terms=tuple(_term(n, 0) for n in _LAYERS_BY_KIND["component"]),
        total=_total_term("component", 5), balanced=False, problems=()),
        SchemaValidationError)
    check(raised[0] is not None and "不平衡必须留下问题码" in raised[1],
          "不平衡层不给问题码即被构造期拒绝（不得静默凑平）")
    # balanced 必须等于**资格函数**的判定结果（自报无效）：两个方向都试。
    #
    # `fmc-2` 起判据不再只是算术：`problems` 非空或 `residual_gap` 非 0 都会让资格
    # 为假。因此"自报 False 而实测为真"的反例必须让三者全部成立（否则拒绝理由会变
    # 成资格本身不成立，测不到自报这一条）。
    zero_terms = tuple(_term(n, 0) for n in _LAYERS_BY_KIND["component"])
    raised = _capture(lambda: TS.ConservationLayer(
        layer_kind="component", terms=zero_terms,
        total=_total_term("component", 0), balanced=False, problems=()),
        SchemaValidationError)
    check(raised[0] is not None and "必须等于分层守恒资格函数的判定结果" in raised[1],
          "`balanced` 自报 False 而资格函数判真即被构造期拒绝（自报无效）")
    raised = _capture(lambda: TS.ConservationLayer(
        layer_kind="component", terms=zero_terms,
        total=_total_term("component", 0), balanced=True, problems=("x",)),
        SchemaValidationError)
    check(raised[0] is not None and "必须等于分层守恒资格函数的判定结果" in raised[1],
          "`balanced` 自报 True 而本层带问题码即被构造期拒绝"
          "（「`problems` 非空却 `balanced=true`」在本构造器下不可能出现）")

    # 守恒对象：四层固定顺序；不平衡层必须留问题码。
    conservation = ctx["final"].conservation
    check(tuple(x.layer_kind for x in conservation.layers) ==
          tuple(k for k, _ in _LAYERS),
          "真实守恒对象的四层顺序与契约一致")
    check(conservation.balanced == all(x.balanced for x in conservation.layers),
          "`balanced` 恰为四层 `balanced` 的合取（不得整表自报）")
    three = conservation.layers[:3]
    raises(lambda: _rebuild_conservation(conservation, layers=three),
           SchemaValidationError, "四层守恒必须恰为",
           "少一层即被构造期拒绝（四层不得省略）")
    # 层自己**带了**问题码（因此能通过层闸），但对象层不留问题码：对象闸必须抓出。
    #
    # `fmc-2` 之后层闸严格更强：`balanced=False` 的层**必须**带问题码（否则构造期即
    # 拒绝，见上一条），所以"不平衡层 + 空文档问题"这一组合到不了对象闸，先撞上的是
    # "层内问题码必须并入文档级 problems"。这里测的就是**实际先触发**的那道闸，
    # 而不是继续断言一段在现行判据下不可达的文案。
    unbalanced_layer = _layer("disposition", {"absorbed_to_table": 0,
                                              "final_paragraph": 0,
                                              "pending_or_unsupported": 0}, 3)
    check(not unbalanced_layer.balanced and bool(unbalanced_layer.problems),
          "反例层自身不平衡且带问题码（否则构造期就已经拒绝，到不了对象闸）")
    raised = _capture(lambda: _rebuild_conservation(
        conservation, layers=(unbalanced_layer, *conservation.layers[1:]),
        problems=()), SchemaValidationError)
    check(raised[0] is not None and "层内问题码必须并入文档级 problems" in raised[1],
          "整对象层面：不平衡层的问题码不得被文档级 `problems` 吞掉"
          "（把文档级留空即被拒绝，层与对象两道闸同源）")
    # 把层问题码如实并入文档级之后，对象本身是**合法**的——证明上一条的拒绝只来自
    # 那个被吞掉的并集，而不是"不平衡层一律不可构造"。
    merged = tuple(sorted(set(conservation.problems)
                          | set(unbalanced_layer.problems)))
    balanced_ok = _rebuild_conservation(
        conservation, layers=(unbalanced_layer, *conservation.layers[1:]),
        problems=merged)
    check(not balanced_ok.balanced and
          unbalanced_layer.problems[0] in balanced_ok.problems,
          "非空转对照：同一个不平衡层在**如实并入**问题码后对象可构造，"
          "但文档级 `balanced` 仍为假（不平衡不会被并集洗掉）")
    raises(lambda: _construct_conservation(
        conservation, conservation_locator="loc-fmc-deadbeefdeadbeef"),
        SchemaValidationError, "conservation_locator 与派生定位不一致",
        "守恒 locator 被改即被构造期拒绝（派生字段不可自报）")
    raises(lambda: _construct_conservation(
        conservation, conservation_id="fmc-deadbeefdeadbeef"),
        SchemaValidationError, "conservation_id 与派生身份不一致",
        "守恒 id 被改即被构造期拒绝")
    check(_rebuild_conservation(
        conservation, conservation_locator="loc-fmc-deadbeefdeadbeef"
    ).conservation_locator == conservation.conservation_locator,
        "`create` 会**覆盖**被自报的 locator（派生字段只有一条来路）")


# ---------------------------------------------------------------------------
# K2. §19.12.2A-8 + §19.12.6-3：三级（层 / 复核重算 / 公开入口）抓扰动
# ---------------------------------------------------------------------------

def _k2_three_level_catches(ctx) -> None:
    final = ctx["final"]
    roots = _roots(ctx)
    dropped = final.bindings[0].component_id

    # --- 1. 删一个 component binding ------------------------------------
    fewer = tuple(b for b in final.bindings if b.component_id != dropped)
    reported_layer = FV._recheck_conservation(
        _ConservationProbe(final, final.conservation,final.decisions, fewer), roots)
    check(any("component 层" in p or "evidence_interval 层" in p
              for p in reported_layer),
          f"层 2 / 层 4 重算抓出「删掉一个 component」"
          f"（{list(reported_layer)[:2]}）")
    real_mismatch = _count_mismatches(_recheck_real(ctx, roots))
    check(not real_mismatch,
          "同一路径对**未扰动**的真实 bindings 不报第 2 / 第 4 层不符"
          f"（证明上面的差异来自被删的 component；实得 {real_mismatch[:1]}）")
    raises(lambda: FV.verify_final_material_snapshot(
        ctx["verified"],
        _rebuild_snapshot(final, bindings=fewer,
                          component_count=len(fewer))),
        FV.FinalVerificationError, "精确等集",
        "公开入口：删一个 component binding 的**自洽**变体被复核器拒绝")

    # --- 2. 重复一个 component binding ----------------------------------
    twice = tuple(final.bindings) + (final.bindings[0],)
    probe = _ConservationProbe(final, final.conservation,final.decisions,
                               tuple(sorted(
                                   twice, key=TS._member_sort_key)))
    reported_layer = FV._recheck_conservation(probe, roots)
    check(any("component 层" in p or "evidence_interval 层" in p
              for p in reported_layer),
          f"层 2 / 层 4 重算抓出「重复一个 component」"
          f"（{list(reported_layer)[:2]}）")
    raises(lambda: FV.verify_final_material_snapshot(
        ctx["verified"], _rebuild_snapshot(
            final, bindings=tuple(sorted(twice, key=TS._member_sort_key)),
            component_count=len(twice))),
        (FV.FinalVerificationError, SchemaValidationError), "不得重复",
        "公开入口：重复一个 component binding 被拒绝（不得重复或增补口径）")

    # --- 3. 改一个 Evidence 子区间端点 ----------------------------------
    victim = next(b for b in final.bindings
                  if b.admission == "table_object"
                  and b.evidence_char_range[1] - b.evidence_char_range[0] > 1)
    lo, hi = victim.evidence_char_range
    moved = TS.FinalComponentBinding.create(
        **{**_all_fields(victim), "evidence_char_range": (lo, hi - 1)})
    swapped = tuple(sorted(
        (moved if b.component_id == victim.component_id else b
         for b in final.bindings), key=TS._member_sort_key))
    reported_layer = FV._recheck_conservation(
        _ConservationProbe(final, final.conservation,final.decisions, swapped), roots)
    check(any("evidence_interval 层" in p for p in reported_layer),
          f"层 4 重算抓出「改一个 Evidence 子区间端点」（"
          f"{list(reported_layer)[:2]}）")
    raises(lambda: FV.verify_final_material_snapshot(
        ctx["verified"], _rebuild_snapshot(final, bindings=swapped)),
        FV.FinalVerificationError, "区间与 TS4 不一致",
        "公开入口：改一个 Evidence 子区间端点的**自洽**变体被复核器拒绝")

    # --- 4. 把 rejected 来源从账本删除 ----------------------------------
    rejected = [b for b in final.bindings if b.admission == "rejected"]
    if rejected:
        drop_id = rejected[0].component_id
        ledger_cut = tuple(b for b in final.bindings
                           if b.component_id != drop_id)
        reported_layer = FV._recheck_conservation(
            _ConservationProbe(final, final.conservation,final.decisions, ledger_cut),
            roots)
        check(any("component 层" in p or "evidence_interval 层" in p
                  for p in reported_layer),
              "层 2 / 层 4 重算抓出「把 rejected 来源从账本删除」"
              f"（{list(reported_layer)[:2]}）")
        raises(lambda: FV.verify_final_material_snapshot(
            ctx["verified"], _rebuild_snapshot(
                final, bindings=ledger_cut,
                component_count=len(ledger_cut))),
            FV.FinalVerificationError, "精确等集",
            "公开入口：从账本删掉一条 rejected 来源被复核器拒绝"
            "（rejected 是终端账本项，不是删除权）")
    else:
        check(False, "样本 `NDSD_KCZ_2026` 必须至少有一条 rejected binding"
                     "（否则本条反例无从构造）")

    # --- 5. 删一条 decision -------------------------------------------
    fewer_dec = tuple(final.decisions[1:])
    reported_layer = FV._recheck_conservation(
        _ConservationProbe(final, final.conservation,fewer_dec, final.bindings), roots)
    check(any("disposition 层" in p for p in reported_layer),
          f"层 1 重算抓出「删一条 decision」（{list(reported_layer)[:2]}）")
    raises(lambda: FV.verify_final_material_snapshot(
        ctx["verified"], _rebuild_snapshot(final, decisions=fewer_dec)),
        FV.FinalVerificationError, "", "公开入口：删一条 decision 被复核器拒绝")

    # --- 6. 删一条 cell source fragment --------------------------------
    #
    # 与第 1/2/4/5 类不同，**片段**这一类扰动没有"自洽的删减版本"：表级
    # `source_refs` 必须恰为全部被消费片段的并集，而每个 cell 的 fragments 又必须
    # 拼出该 cell 的 text。删一个片段必然二选一地违反其中一条，因此在**装配期**就被
    # 拒绝——比复核器更早，是更强的 fail-closed，不是"绕过了复核器"。
    table = final.tables[0]
    # 必须删一条**被 cell 消费**的片段：删表级块（标题/单位/注释）的片段会先撞上
    # "引用了未知 source_ref_id"，那是另一条判据，与本条要证的不是同一件事。
    cell_ref_ids = {x.source_ref_id for c in table.all_cells
                    for x in c.source_refs}
    cut_ref = next(r.source_ref_id for r in table.source_refs
                   if r.source_ref_id in cell_ref_ids)
    fewer_refs = tuple(r for r in table.source_refs
                       if r.source_ref_id != cut_ref)
    check(len(fewer_refs) == len(table.source_refs) - 1 and bool(fewer_refs),
          f"反例可构造：表 {table.table_id} 至少有两个来源片段")
    raises(lambda: TS.TableObjectV4.create(
        **{**_all_fields(table), "source_refs": fewer_refs}),
        SchemaValidationError, "并集",
        "删一条 cell source fragment：表级 source_refs 必须仍是全部被消费片段的并集"
        "（游离 / 缺失片段在装配期即被拒绝）")
    # 非空转对照：真实表对象原样重建**不**报错（证明上一条差异只来自被删片段）。
    check(TS.TableObjectV4.create(**_all_fields(table)).table_id == table.table_id,
          "非空转对照：真实表对象原样重建通过（上一条的拒绝来自被删片段本身）")

    # 第 7 类（删一条缺口）属 §19.12.6-8，见 `K6-gap-honesty`，此处不重复。

    # 非空转对照：同一路径对**真实件**的裁决必须只有一种来源。
    #
    # `fmc-2` 起公开入口在守恒资格不成立时拒绝签发；本样本是真实文档，其
    # `layout_text` 层带着实测未解释残余，因此被 fail-closed 拒绝是**正确**结果。
    # 对照组因此不再是"真实件被签发"，而是两件更精确的事：
    #   (a) 结构复核路径（`_recheck_conservation`）对真实 bindings **不报第 2 / 第 4
    #       层不符** —— 上面的第 1/2/4/5 类扰动抓到的差异确实来自扰动本身（真实件上
    #       仍存在的第 3 层问题在扰动前后都在，不构成差异来源）；
    #   (b) 公开入口对真实件的拒绝**确实是守恒资格**这一条，且逐层列出残余数，
    #       不是别的门（阻断缺口 / 表检查 / 绑定等集）先拒绝的。
    if ctx["wrapper"] is not None:
        check(FV.final_material_problems(ctx["wrapper"]) == (),
              "对照组：同一复核路径对真实件零问题（差异确实来自扰动）")
    else:
        check(bool(ctx["refusal"]) and "layout_text 层不成立" in ctx["refusal"]
              and "最终材料守恒资格不成立" in ctx["refusal"],
              "对照组：公开入口对真实件的拒绝来自**守恒资格**这一条，并逐层列出"
              f"残余（截断：{str(ctx['refusal'])[:120]!r}）")
        real_mismatch = _count_mismatches(_recheck_real(ctx, roots))
        check(not real_mismatch,
              "对照组：同一路径对未扰动的真实 bindings 不报第 2 / 第 4 层不符"
              f"（拒绝不是结构数量判据造成的；实得 {real_mismatch[:1]}）")


# ---------------------------------------------------------------------------
# K3. §19.12.6-1：逐 cell source ref 的四跳回查
# ---------------------------------------------------------------------------

def _layout_spans(ctx) -> dict:
    out: dict = {}
    for page in ctx["handoff"].page_layout.pages:
        for line in page.lines:
            for si, span in enumerate(line.spans):
                out[(page.page_number, line.line_index, si)] = span
    return out


def _k3_per_hop_provenance(ctx) -> None:
    final = ctx["final"]
    roots = _roots(ctx)
    spans = _layout_spans(ctx)
    components = {c.component_id: c for c in roots.components}

    problems: list = []
    checked = 0
    hop2_refs = 0
    hop3_refs = 0
    hop4_refs = 0
    for table in final.tables:
        for ref in table.source_refs:
            checked += 1
            key = (ref.interval.page_number, ref.interval.line_index,
                   ref.interval.span_index)
            # 第 1 跳：真实 LayoutSpan 存在且区间落在**片段内**。
            span = spans.get(key)
            if span is None:
                problems.append((table.table_id, ref.source_ref_id, "no_span"))
                continue
            lo, hi = ref.interval.span_char_range
            upper = int(span.char_end) - int(span.char_start)
            if not (0 <= lo < hi <= upper):
                problems.append((ref.source_ref_id, "char_range", (lo, hi, upper)))
            if tuple(ref.interval.bbox) != tuple(span.bbox):
                problems.append((ref.source_ref_id, "bbox"))
            # 第 2 跳：TS4 component 真实存在且身份/区间闭合。
            hop2_refs += 1
            component = components.get(ref.component_id)
            if component is None:
                problems.append((ref.source_ref_id, "no_component"))
            else:
                if ref.component_locator != component.component_locator:
                    problems.append((ref.source_ref_id, "component_locator"))
                elo, ehi = ref.evidence_char_range
                clo, chi = component.evidence_char_range
                if not (clo <= elo < ehi <= chi):
                    problems.append((ref.source_ref_id, "evidence_range",
                                     ((elo, ehi), (clo, chi))))
                # 第 3 跳：Evidence 区间落在同一 block 的原生字符域内。
                hop3_refs += 1
                text = roots.block_text(ref.evidence_block_id)
                if text is None:
                    problems.append((ref.source_ref_id, "no_block",
                                     ref.evidence_block_id))
                elif ehi > len(text):
                    problems.append((ref.source_ref_id, "block_range",
                                     (ehi, len(text))))
            # 第 4 跳：alignment / refusal 终态真值表。
            hop4_refs += 1
            if ref.terminal_kind == "alignment":
                if ref.alignment_id != ref.terminal_id:
                    problems.append((ref.source_ref_id, "alignment_id"))
                if ref.verdict != "aligned" and ref.citable:
                    problems.append((ref.source_ref_id, "citable_not_aligned"))
            elif ref.alignment_id is not None:
                problems.append((ref.source_ref_id, "forced_alignment_id"))
            if ref.citable != (ref.citable_reason == TS.CITABLE_REASON_TRUE):
                problems.append((ref.source_ref_id, "citable_reason"))
            # 身份：由**身份载荷**独立复算。
            want = TS.identity("tcsr", ref._identity_payload())
            if want != ref.source_ref_id:
                problems.append((ref.source_ref_id, "identity", want))
    check_aggregate(problems, checked,
                    "每个 cell source ref 的四跳来源回查"
                    "（LayoutSpan 存在且区间/bbox 闭合 → TS4 component 身份与"
                    "区间闭合 → Evidence 区间落在本 block 字符域内 → "
                    "alignment/refusal 终态真值表），并独立复算 `source_ref_id`")
    check(checked > 0 and hop2_refs == checked and hop3_refs == checked and
          hop4_refs == checked,
          f"四跳逐条都真正走完（refs={checked}，复合 {hop2_refs} 条）")

    # 表级块（title/unit/note）引用的片段也必须可解析（不得悬空）。
    dangling: list = []
    blocks_checked = 0
    for table in final.tables:
        known = {r.source_ref_id for r in table.source_refs}
        for group in (table.title_blocks, table.unit_blocks, table.note_blocks):
            for blk in group:
                for rid in blk.source_ref_ids:
                    blocks_checked += 1
                    if rid not in known or table.source_ref_by_id(rid) is None:
                        dangling.append((table.table_id, rid))
    check_aggregate_allow_empty(
        dangling, blocks_checked,
        "表级块（表题/单位/表注）引用的来源片段全部在 `table.source_refs` 中"
        "可解析（无悬空引用）")
    check(blocks_checked > 0,
          f"表级块确有引用的来源片段（{blocks_checked} 条，"
          "否则上一条是空转）")

    # 负例：把 bbox 改掉的自洽 ref 被构造期拒绝（逐跳闭合不是自报的）。
    ref0 = final.tables[0].source_refs[0]
    bad_bbox = tuple(float(x) for x in ref0.interval.bbox)
    raises(lambda: TS.TableSourceInterval(
        page_number=ref0.interval.page_number,
        line_index=ref0.interval.line_index, span_index=ref0.interval.span_index,
        span_char_range=(ref0.interval.span_char_range[1],
                         ref0.interval.span_char_range[0]),
        bbox=bad_bbox),
        SchemaValidationError, "0 <= start < end",
        "倒序的 `span_char_range` 被构造期拒绝（区间方向是契约）")
    raises(lambda: TS.TableCellSourceRef(
        **{**_all_fields(ref0), "alignment_id": None}
        if ref0.terminal_kind == "alignment" else
        {**_all_fields(ref0), "alignment_id": "al-自报"}),
        SchemaValidationError, "alignment",
        "alignment 终态缺 `alignment_id`（或 refusal 终态伪造 `alignment_id`）"
        "被构造期拒绝")


# ---------------------------------------------------------------------------
# K4. §19.12.6-2：混合可引用性的表不得被整表授权
# ---------------------------------------------------------------------------

def _k4_mixed_citability(ctx) -> None:
    final = ctx["final"]
    problems: list = []
    checked = 0
    mixed = 0
    for coverage in final.coverages:
        checked += 1
        table = final.table_by_id(coverage.table_id)
        if table is None:
            problems.append((coverage.coverage_id, "no_table"))
            continue
        # 逐条来源片段给区间，不是整表 bool。
        want_refs = tuple(sorted({r.source_ref_id for r in table.source_refs}))
        if tuple(coverage.cell_refs) != want_refs:
            problems.append((coverage.coverage_id, "cell_refs"))
        if len(coverage.intervals) != len(want_refs):
            problems.append((coverage.coverage_id, "interval_count"))
        measured = tuple(sorted({iv.reason for iv in coverage.intervals
                                 if not iv.citable}))
        if tuple(coverage.non_citable_reasons) != measured:
            problems.append((coverage.coverage_id, "non_citable_reasons",
                             (tuple(coverage.non_citable_reasons), measured)))
        want_all = all(iv.citable for iv in coverage.intervals)
        if coverage.all_cells_citable != want_all:
            problems.append((coverage.coverage_id, "all_cells_citable"))
        want_chars = sum(iv.char_range[1] - iv.char_range[0]
                         for iv in coverage.intervals if iv.citable)
        if coverage.citable_char_count != want_chars:
            problems.append((coverage.coverage_id, "citable_char_count",
                             (coverage.citable_char_count, want_chars)))
        # 整表可引用 == 该表**全部**来源片段可引用，二者必须同值。
        refs_all = all(r.citable for r in table.source_refs)
        refs_any = any(r.citable for r in table.source_refs)
        if coverage.all_cells_citable != refs_all:
            problems.append((coverage.coverage_id, "whole_table_authority",
                             (coverage.all_cells_citable, refs_all)))
        if not refs_any and coverage.citable_char_count != 0:
            problems.append((coverage.coverage_id, "authority_without_ref"))
        if not refs_all and coverage.citable_char_count == 0 and refs_any:
            problems.append((coverage.coverage_id, "mixed_collapsed_to_zero"))
        if not refs_all:
            mixed += 1
        # 财务权威：TableObject 恒 False，且与 coverage 无关。
        if table.is_financial_authority():
            problems.append((table.table_id, "financial_authority"))
        for iv in coverage.intervals:
            if iv.citable and iv.reason != TS.CITABLE_REASON_TRUE:
                problems.append((coverage.coverage_id, "citable_reason",
                                 iv.reason))
            if not iv.citable and iv.reason not in TS.CITABLE_REASONS:
                problems.append((coverage.coverage_id, "unregistered_reason",
                                 iv.reason))
    check_aggregate(problems, checked,
                    "逐 cell 覆盖：`all_cells_citable` / `citable_char_count` / "
                    "`non_citable_reasons` / `cell_refs` 全部由测试独立复算"
                    "（不得以整表 bool 取代逐片段判定）")
    check(checked == len(final.tables) and checked > 0,
          f"每张表都有一一对应的 coverage（{checked} 张）")

    # 非空转：把一条 citable 区间翻面而不改理由集 → 构造期即拒绝。
    all_citable = [c for c in final.coverages if c.all_cells_citable]
    coverage = all_citable[0] if all_citable else final.coverages[0]
    first = coverage.intervals[0]
    flipped = TS.CitableInterval(
        citable=not first.citable,
        reason=(TS.CITABLE_REASONS[1] if first.citable
                else TS.CITABLE_REASON_TRUE),
        char_range=first.char_range,
        # 翻成可引用时必须绑定具体 cell source ref（不得只引整表）。
        cell_ref=first.cell_ref or tuple(coverage.cell_refs)[0])
    raises(lambda: TS.TableCitableCoverage.create(
        **{**_all_fields(coverage),
           "intervals": (flipped,) + tuple(coverage.intervals[1:])}),
        SchemaValidationError, "必须等于实测非可引用区间的理由集",
        "把一条区间翻面而不改 `non_citable_reasons` 即被构造期拒绝"
        "（理由集不得自报）")
    check(coverage.all_cells_citable and
          coverage.citable_char_count ==
          sum(iv.char_range[1] - iv.char_range[0] for iv in coverage.intervals),
          f"样本中至少一张表被逐条判定为**全部**可引用（{mixed} 张混合表；"
          "该对照件证明上一条判据不是恒真）")
    if mixed:
        _results["details"].append(
            f"__mixed_tables__ {mixed}/{checked} 张表含 non-citable 片段"
            "（逐 cell 判定确实生效）")


# ---------------------------------------------------------------------------
# K5. §19.12.6-4 / -5 / -6：精确等集、终态真值表与 regular body span
# ---------------------------------------------------------------------------

def _k5_exact_set_landing(ctx) -> None:
    final = ctx["final"]
    roots = _roots(ctx)
    components = {c.component_id: c for c in roots.components}

    check(len(final.bindings) == len(components) == final.component_count,
          f"bindings 与 TS4 components 精确等集且只出现一次"
          f"（{len(final.bindings)} == {len(components)} == "
          f"{final.component_count}）")
    ids = [b.component_id for b in final.bindings]
    check(len(set(ids)) == len(ids) and
          {b.component_id for b in final.bindings} == set(components),
          "每个 component 恰好一条 binding，集合等于全部 TS4 components")
    buckets = {a: 0 for a in _ADMISSIONS}
    for b in final.bindings:
        buckets[b.admission] = buckets.get(b.admission, 0) + 1
    values = _layer_values(_layer_of(final, "component"))
    check(values == buckets and
          sum(buckets.values()) == len(final.bindings),
          f"层 2 的四个分项恰为四类终态的实测计数（{values} == {buckets}）"
          "——四类终态构成对全部 component 的**划分**")

    problems: list = []
    non_table_landing_with_table = 0
    body_span_in_table: list = []
    for b in final.bindings:
        allowed = _LANDING_ADMISSIONS.get(b.component_landing)
        if allowed is None:
            problems.append((b.component_id, "unknown_landing",
                             b.component_landing))
            continue
        if b.admission not in allowed:
            problems.append((b.component_id, "admission", b.admission))
        if b.component_landing == "body_span" and b.admission == "table_object":
            body_span_in_table.append(b.component_id)
        if b.component_landing in _NON_TABLE_LANDINGS and \
                b.admission == "table_object":
            non_table_landing_with_table += 1
            # §19.12.6-5：进入表**必须**有真实 cell proof，且保留原 landing。
            if not b.cell_source_ref_ids:
                problems.append((b.component_id, "table_without_cell_proof"))
            component = components.get(b.component_id)
            if component is None or b.component_landing != component.landing:
                problems.append((b.component_id, "landing_not_preserved"))
            table = final.table_by_id(b.table_id)
            if table is None:
                problems.append((b.component_id, "table_not_in_snapshot"))
            else:
                known = {r.source_ref_id for r in table.source_refs}
                stray = [r for r in b.cell_source_ref_ids if r not in known]
                if stray:
                    problems.append((b.component_id, "cell_ref_stray", stray[:2]))
        if b.admission == "final_span":
            span = final.final_span_by_id(b.final_span_id)
            if span is None:
                problems.append((b.component_id, "final_span_missing"))
            elif span.node_id is None:
                problems.append((b.component_id, "final_span_no_node"))
            if b.table_id is not None or b.cell_source_ref_ids:
                problems.append((b.component_id, "final_span_extra_target"))
        if b.admission == "pending" and not b.gap_codes:
            problems.append((b.component_id, "pending_without_gap_code"))
        if b.admission == "rejected" and b.gap_codes:
            problems.append((b.component_id, "rejected_with_gap_code"))
        component = components.get(b.component_id)
        if component is not None:
            if b.component_landing != component.landing:
                problems.append((b.component_id, "landing drift"))
            if b.disposition_id != component.disposition_id:
                problems.append((b.component_id, "disposition_id drift"))
    check_aggregate(problems, len(final.bindings),
                    "每条 binding 的终态落在 `COMPONENT_LANDING_ADMISSIONS"
                    "[landing]` 内、landing/disposition 与 TS4 逐条一致、"
                    "`table_object` 必有真实 cell proof、`final_span` 必解析到"
                    "本快照的 final span、`pending` 必留缺口码")
    check(not body_span_in_table,
          f"§19.12.6-6：`body_span` 的 component **一次**都没有拿到 "
          f"`table_object`（regular body span 未被偷换；实得 "
          f"{body_span_in_table[:3]}）")
    check(_LANDING_ADMISSIONS["body_span"] == ("final_span", "pending") and
          TS.COMPONENT_LANDING_ADMISSIONS["body_span"] ==
          _LANDING_ADMISSIONS["body_span"],
          "wire 层的 `body_span` 终态集与测试自带真值表同为 "
          "(final_span, pending)")
    # 反例：body_span 拿 table_object 被构造期拒绝（不是"样本里恰好没有"）。
    body = next((c for c in roots.components if c.landing == "body_span"), None)
    check(body is not None, "TS4 根里存在 `body_span` component（反例可构造）")
    if body is not None:
        target = final.tables[0]
        # 以一条**真实** binding 为模板（版本字段取自生产值），只换 component 与终态：
        # 这样反例只差"body_span 拿到 table_object"这一件事。
        template = _all_fields(next(b for b in final.bindings
                                    if b.admission == "table_object"))
        template.update({
            "component_id": body.component_id,
            "component_locator": body.component_locator,
            "component_landing": "body_span", "admission": "table_object",
            "admission_reason": "absorbed_into_table",
            "table_id": target.table_id, "final_span_id": None,
            "cell_source_ref_ids": (target.source_refs[0].source_ref_id,),
            "disposition_id": body.disposition_id,
            "evidence_char_range": tuple(body.evidence_char_range),
            "gap_codes": ()})
        raises(lambda: TS.FinalComponentBinding.create(**template),
               SchemaValidationError, "只允许终态",
               "§19.12.6-6：`body_span` → `table_object` 在构造期即被拒绝"
               "（「样本里没有」不足以证明这条门）")

    # §19.12.6-5：正式表内 landing 之外的既有 landing 必须**原样保留**。
    lands = sorted({b.component_landing for b in final.bindings})
    _results["details"].append(
        f"__landings__ {lands}（继承/formal 进表 {non_table_landing_with_table} 条）")
    check(set(lands) <= set(_LANDING_ADMISSIONS),
          f"真实件只出现已登记的 landing（{lands[:3]}...）")


# ---------------------------------------------------------------------------
# K6. §19.12.6-8：缺口的诚实性
# ---------------------------------------------------------------------------

def _k6_gap_honesty(ctx) -> None:
    final = ctx["final"]
    roots = _roots(ctx)
    gaps = final.gaps
    components = {c.component_id for c in roots.components}

    keys = [(e.gap_kind, e.detail_code, e.page_number or 0, e.table_id or "",
             e.component_id or "", e.disposition_id or "")
            for e in gaps.entries]
    check(keys == sorted(keys) and len(set(keys)) == len(keys),
          f"缺口条目按固定键升序且不重复（{len(keys)} 条）")
    measured = sum(1 for e in gaps.entries if e.blocks_document_capability)
    check(measured == gaps.blocking_entry_count == final.blocking_gap_count,
          f"`blocking_entry_count` 与 `snapshot.blocking_gap_count` 均为实测值"
          f"（{measured}）")
    check(gaps.blocks_document_capability == (measured > 0),
          "缺口台账的阻断属性由阻断条目数派生（不得独立自报）")

    problems: list = []
    for e in gaps.entries:
        if e.gap_kind not in TS.TABLE_GAP_KINDS:
            problems.append((e.gap_kind, "unregistered"))
            continue
        if e.blocks_document_capability != (e.gap_kind in TS.GAP_BLOCKING_KINDS):
            problems.append((e.gap_kind, "blocking_flag"))
        if not isinstance(e.detail_code, str) or e.detail_code == "":
            problems.append((e.gap_kind, "detail_code"))
        if e.component_id is not None and e.component_id not in components:
            problems.append((e.component_id, "stray_component"))
        if e.table_id is not None and final.table_by_id(e.table_id) is None:
            problems.append((e.table_id, "stray_table"))
        anchored = any(x is not None for x in
                       (e.page_number, e.table_id, e.component_id,
                        e.disposition_id))
        scoped = (e.gap_kind, e.detail_code) in _DOCUMENT_SCOPED_GAP_CODES
        if not anchored and not scoped:
            problems.append((e.gap_kind, e.detail_code, "no_anchor"))
    check_aggregate(problems, len(gaps.entries),
                    "每条缺口：种类已登记、阻断标记与种类同源、`detail_code` "
                    "非空、引用的 component/table 都在本快照内、且有具体锚点"
                    "（页/表/component/disposition 至少一项，不是泛泛的一句"
                    "「有缺口」）")
    used_scoped = {(e.gap_kind, e.detail_code) for e in gaps.entries} & \
        _DOCUMENT_SCOPED_GAP_CODES
    check(used_scoped == set(_DOCUMENT_SCOPED_GAP_CODES),
          f"被豁免锚点的文档级缺口码在本样本中确实出现（{sorted(used_scoped)}）"
          f"——否则该豁免是空转的")
    check(set(TS.GAP_BLOCKING_KINDS) <= set(TS.TABLE_GAP_KINDS) and
          tuple(TS.DOCUMENT_BLOCKING_REASONS) == tuple(TS.GAP_BLOCKING_KINDS),
          "阻断型缺口必须来自已登记种类，且与 `DOCUMENT_BLOCKING_REASONS` 同集"
          "（不得自行放宽）")

    pending = {b.component_id for b in final.bindings if b.admission == "pending"}
    covered = {e.component_id for e in gaps.entries if e.component_id is not None}
    check(not (pending - covered),
          f"每个 pending 的 component 都留下结构缺口"
          f"（缺 {sorted(pending - covered)[:3]}）")
    check_aggregate_allow_empty([], len(pending),
                                f"pending component 与缺口的对应关系"
                                f"（pending={len(pending)}）")
    # 非空转：删掉一条覆盖 pending 的缺口 → 复核器报"必须留下结构缺口"。
    if pending & covered:
        target = next(cid for cid in sorted(pending & covered))
        kept = tuple(e for e in gaps.entries if e.component_id != target)
        thinner = TS.TableStructureGap.create(
            **{**_all_fields(gaps), "entries": kept,
               "blocking_entry_count":
                   sum(1 for e in kept if e.blocks_document_capability)})
        raised = _capture(lambda: FV.verify_final_material_snapshot(
            ctx["verified"], _rebuild_snapshot(
                final, gaps=thinner, blocking_gap_count=0)),
            FV.FinalVerificationError)
        check(raised[0] is not None and
              "必须留下结构缺口" in raised[1],
              f"删掉 component {target[:18]!r} 的缺口后复核器报"
              f"`必须留下结构缺口`（非空转的取舍证明）")
        # 缺口对象自身的身份也由 create 重算，篡改件内部自洽。
        check(thinner.gap_id == TS.identity("tsg", thinner.identity_payload()),
              "被删一类缺口后重建的台账自身身份自洽（差异只来自被删条目）")
    else:
        _results["skipped"] += 1
        _results["details"].append(
            "SKIP 删 pending 缺口反例：本样本没有 pending component")
    # 反例：悬空引用被构造期/复核器拒绝。
    if gaps.entries:
        head = gaps.entries[0]
        stray = TS.TableGapEntry(
            gap_kind=head.gap_kind, detail_code=head.detail_code,
            page_number=head.page_number, table_id=None,
            component_id="comp-not-in-this-snapshot", disposition_id=None,
            blocks_document_capability=head.blocks_document_capability)
        entries = tuple(sorted(
            (stray,) + tuple(gaps.entries[1:]),
            key=lambda e: (e.gap_kind, e.detail_code, e.page_number or 0,
                           e.table_id or "", e.component_id or "",
                           e.disposition_id or "")))
        strays = TS.TableStructureGap.create(
            **{**_all_fields(gaps), "entries": entries,
               "blocking_entry_count":
                   sum(1 for e in entries if e.blocks_document_capability)})
        raised = _capture(lambda: FV.verify_final_material_snapshot(
            ctx["verified"], _rebuild_snapshot(
                final, gaps=strays,
                blocking_gap_count=strays.blocking_entry_count)),
            FV.FinalVerificationError)
        check(raised[0] is not None and "本快照以外的 component" in raised[1],
              "缺口引用本快照以外的 component 被复核器拒绝"
              "（缺口不得成为悬空引用）")


# ---------------------------------------------------------------------------
# K7. §19.9.3 `fmc-2` 受控扩展 + `fmc-3` 资格后继：词表、资格与历史读回
# ---------------------------------------------------------------------------

def _k7_deferred_vocabulary(ctx) -> None:
    """`fmc-3` 的**必要**测试：词表沿革、资格判据、旧载荷 fail-closed 与历史读回。

    只覆盖"受控扩展 wire 词表"与"资格后继升版"这两次裁决真正改变的事，不重复 K1
    已有的契约项：

    1. 新分项**只**在 current 层出现，legacy 副本里没有它（两份词表不能互相冒充）；
    2. `conservation_layer_eligible` 是真值表的三条合取——算术 / `problems` 空 /
       必须为零的分项为零，任缺其一即不成立；
    3. legacy 载荷走 current reader 一律 fail-closed，且理由是**版本**而不是形状；
    4. 同一份 legacy 载荷走**显式历史入口**可以读回，并标成 `historical_only`；
    5. `registered_deferred` 的资格函数：合法者恰好计数，无归属 / 多归属 /
       已被消费 / 缺口种类不可延期四种情形必须拒绝（拒绝理由逐条可诊断）；
    6. `non_semantic_whitespace` 的唯一判据、进入顺序与三类反例（见第 6 节）。
    """
    # --- 1. 词表：新分项只在 current 层 ---------------------------------
    check(TS.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION == "fmc-3",
          "current 守恒 wire 版本恰为 `fmc-3`（`registered_deferred` 的资格集合变了，"
          "同一份载荷的含义随之改变，因此必须升版而不是原位改语义）")
    check(V.legacy_versions("FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION")
          == ("fmc-1", "fmc-2"),
          "`fmc-1` / `fmc-2` 都被登记为 legacy（因此 current reader 会把它们判成旧版本）")
    check("registered_deferred" in TS.CONSERVATION_LAYER_TERMS["layout_text"]
          and TS.CONSERVATION_LAYER_TERMS["layout_text"][-1] == "residual_gap",
          "current `layout_text` 分项含 `registered_deferred` 且以 `residual_gap` 收尾")
    check("registered_deferred"
          not in TS.LEGACY_CONSERVATION_LAYER_TERMS["layout_text"]
          and "registered_deferred" not in TS.LEGACY_CONSERVATION_TERM_KIND_OF,
          "`fmc-1` 冻结副本里**没有** `registered_deferred`"
          "（两份词表不得互相冒充）")
    check(TS.CONSERVATION_DEFERRED_GAP_KINDS and
          not (set(TS.CONSERVATION_DEFERRED_GAP_KINDS)
               & set(TS.GAP_BLOCKING_KINDS)),
          f"可延期缺口种类与阻断种类不相交"
          f"（{len(TS.CONSERVATION_DEFERRED_GAP_KINDS)} 类可延期："
          f"{TS.CONSERVATION_DEFERRED_GAP_KINDS}）")

    # --- 2. 资格判据：三条合取的真值表 ---------------------------------
    base = {"cell_source_ref": 0, "caption_unit_note_ref": 0,
            "final_paragraph_ref": 0, "registered_deferred": 0,
            "non_semantic_whitespace": 0, "residual_gap": 0}
    ok = dict(base, cell_source_ref=10)
    check(TS.conservation_layer_eligible("layout_text", ok, 10, ()),
          "算术守恒 + 无问题 + 残余为 0 ⇒ 资格成立")
    check(not TS.conservation_layer_eligible("layout_text", ok, 10, ("x",)),
          "`problems` 非空 ⇒ 资格不成立（不得因为算术凑平就放行）")
    check(not TS.conservation_layer_eligible(
        "layout_text", dict(base, cell_source_ref=10, residual_gap=1), 11, ()),
        "算术守恒但 `residual_gap` 非 0 ⇒ 资格不成立")
    check(not TS.conservation_layer_eligible(
        "layout_text", dict(base, cell_source_ref=10, registered_deferred=1),
        12, ()),
        "算术不守恒 ⇒ 资格不成立")
    deferred_layer = _layer("layout_text", {"registered_deferred": 7,
                                            "cell_source_ref": 3}, 10)
    check(deferred_layer.balanced and not deferred_layer.problems,
          "`registered_deferred` 计入后算术守恒 ⇒ 该层资格成立"
          "（它确实是「已解释」的记账位，不是兜底项）")
    residual_layer = _layer("layout_text", {"registered_deferred": 7,
                                            "residual_gap": 1}, 8,
                            problems=("layout_text_residual:p1#unowned",))
    check(not residual_layer.balanced,
          "同一层里只要还剩 1 个未解释字符，资格即不成立"
          "（把残余记进 `registered_deferred` 也救不回来）")

    # --- 3/4. 旧载荷：current reader fail-closed，历史入口可读回 ---------
    legacy_payload = _legacy_conservation_payload()
    raises(lambda: TS.FinalMaterialConservation.from_dict(legacy_payload),
           SchemaValidationError, "为旧 wire format 版本，不得静默解释为新版本",
           "`fmc-1` 载荷走 current reader 一律 fail-closed，且理由是**版本**")
    legacy = TS.read_legacy_conservation(legacy_payload)
    check(legacy["status"] == "historical_only" and
          legacy["schema_version"] == "fmc-1",
          "同一份 `fmc-1` 载荷走**显式历史入口**可以读回，并标成 `historical_only`")
    legacy_layout = next(r for r in legacy["layers"]
                         if r["layer_kind"] == "layout_text")
    check("registered_deferred" not in legacy_layout["values"] and
          legacy_layout["arithmetic_balanced"] and
          legacy_layout["values"]["residual_gap"] == 5,
          "历史读回按**当时的**四项分项表校验，并独立重算算术"
          f"（{legacy_layout['values']}）")
    check("residual_gap" in legacy["note"]
          and "被当作 current 结论" in legacy["note"]
          and TS.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION in legacy["note"],
          "历史读回的说明明确禁止把旧 `balanced` 当 current 结论，"
          "并指名 current 结论只能由当前版本重算得出")
    # 两个方向都不静默：legacy 只认 legacy，**current 也不被 legacy 入口收下**。
    raises(lambda: TS.read_legacy_conservation(dict(
        legacy_payload,
        schema_version=TS.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION)),
        SchemaValidationError, "本入口只受理",
        "历史入口反过来拒绝 current 载荷（两个方向都不静默）")
    # `fmc-2` 是**上一代** current：它已不在 current 位，但仍能走历史入口读回，且
    # 读回的附注必须点出"分项名相同、资格较窄"这个唯一区别（否则它会被当成 current）。
    near = TS.read_legacy_conservation(_legacy_conservation_payload("fmc-2"))
    check(near["status"] == "historical_only"
          and "资格较窄" in near["note"]
          and TS.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION in near["note"],
          "上一代 current（`fmc-2`）走历史入口可读回，且附注点名资格差异"
          f"（{near['note']}）")

    # --- 5. `registered_deferred` 的资格函数 ----------------------------
    defer_code = TS.CONSERVATION_DEFERRED_GAP_KINDS[0]
    blocking_code = TS.GAP_BLOCKING_KINDS[0]
    check(blocking_code not in TS.CONSERVATION_DEFERRED_GAP_KINDS,
          f"阻断型缺口 {blocking_code!r} 不属于可延期种类（前提成立）")

    def _binding(admission, gap_codes, reason="synthetic_reason"):
        return SimpleNamespace(admission=admission, admission_reason=reason,
                               gap_codes=tuple(gap_codes))

    bindings = {
        "comp-a": _binding("pending", (defer_code,)),
        "comp-b": _binding("rejected", (defer_code,)),
        "comp-c": _binding("pending", (blocking_code,)),
        "comp-d": _binding("table_object", (defer_code,)),
        "comp-e": _binding("pending", ()),
    }
    judge = FMB._deferred_rejection_reason
    check(judge([(0, 10, "comp-a")], bindings, 2, 8) is None,
          "合法：区间恰被一个 pending component 覆盖，且它带可延期 typed gap "
          "⇒ 允许计入 `registered_deferred`")
    check(judge([(0, 10, "comp-b")], bindings, 2, 8) is None,
          "合法：终态为 `rejected` 的 component 同样可以承担延期")
    check(judge([], bindings, 2, 8) == "unowned",
          "拒绝：无 component 覆盖 ⇒ 落入 `residual_gap`（拒绝理由 `unowned`）")
    check(judge([(0, 4, "comp-a")], bindings, 2, 8) == "unowned",
          "拒绝：范围错配（hit 只覆盖区间一半）⇒ 拒绝理由 `unowned`")
    check(judge([(0, 10, "comp-a"), (0, 10, "comp-b")], bindings, 2, 8)
          == "multi_owner",
          "拒绝：重复归属（两个 component 都覆盖）⇒ 拒绝理由 `multi_owner`")
    check(judge([(0, 10, "comp-c")], bindings, 2, 8)
          == f"owner_ungapped:{'synthetic_reason'}",
          "拒绝：错 gap（只有阻断型缺口码）⇒ 不得延期")
    check(judge([(0, 10, "comp-d")], bindings, 2, 8)
          == "owner_consumed:table_object",
          "拒绝：owner 终态为 `table_object`（文本已被别处消费）⇒ 不得延期")
    check(judge([(0, 10, "comp-e")], bindings, 2, 8)
          == "owner_ungapped:synthetic_reason",
          "拒绝：无 gap 码 ⇒ 不得延期")
    check(judge([(0, 10, "comp-missing")], {}, 2, 8) == "no_binding",
          "拒绝：覆盖者没有正式 binding ⇒ 不得延期（无凭据不算已解释）")

    # --- 6. `non_semantic_whitespace`：判据、顺序与反例 ------------------
    check("non_semantic_whitespace"
          in TS.CONSERVATION_LAYER_TERMS["layout_text"]
          and TS.CONSERVATION_LAYER_TERMS["layout_text"][-1] == "residual_gap"
          and TS.CONSERVATION_TERM_KIND_OF["non_semantic_whitespace"]
          == "character",
          "current `layout_text` 分项含 `non_semantic_whitespace`"
          "（计量类别为字符），且仍以 `residual_gap` 收尾")
    check("non_semantic_whitespace"
          not in TS.LEGACY_CONSERVATION_LAYER_TERMS["layout_text"]
          and "non_semantic_whitespace"
          not in TS.LEGACY_CONSERVATION_TERM_KIND_OF,
          "`fmc-1` 冻结副本里**没有** `non_semantic_whitespace`"
          "（两份词表不得互相冒充）")
    check(TS.FMC2_ADDED_TERMS == ("registered_deferred",
                                  "non_semantic_whitespace"),
          f"`fmc-2` 相对 `fmc-1` 新增的分项恰为 {TS.FMC2_ADDED_TERMS}"
          "（新增项逐项登记，词表不得再有第三项）")
    check(TS.CONSERVATION_ZERO_REQUIRED_TERMS == ("residual_gap",),
          f"必须为零的分项恰为 {TS.CONSERVATION_ZERO_REQUIRED_TERMS}"
          "（新增项**不得**混进「必须为零」的名单：它不是零值项，而是按判据精确"
          "计数的记账位）")

    # 判据本身：只有"整段恰为空白"才算。这里刻意包含三个边界——
    # `"　"`（全角空格，Python 认作空白 ⇒ 算）、`"​"`（零宽空格，**不**算，
    # 它是不可见但非空白的字符 ⇒ 必须留在残余）、非字符串（None/整数）一律不算。
    for blank in ("", " ", "   ", "\n\t ", " \r\n ", "　"):
        check(TS.is_non_semantic_whitespace(blank) is True,
              f"整段恰为空白 ⇒ 判据成立：{blank!r}")
    for solid in ("正文", "a", " x ", "。", "​", None, 5):
        check(TS.is_non_semantic_whitespace(solid) is False,
              f"含非空白字符（或根本不是字符串）⇒ 判据不成立：{solid!r}")

    # 纯空白正例：3 个空格，无任何主张、无 component 覆盖 ⇒ 精确计入新分项，
    # 既不报残余，也不改变总量。
    blank_run = _layout_layer_probe(((0, "   ", (), ()),))
    check(blank_run[0]["non_semantic_whitespace"] == 3
          and blank_run[0]["residual_gap"] == 0 and blank_run[1] == 3
          and blank_run[2] == (),
          f"纯空白区间**精确计数**进 `non_semantic_whitespace`、不报残余、"
          f"总量不变：{blank_run}")
    check(TS.conservation_layer_eligible("layout_text", blank_run[0],
                                         blank_run[1], blank_run[2]),
          "只剩非语义空白时该层资格成立（新分项不是零值项，但它使"
          "『本无信息的空白』不再冒充『未解释残余』）")

    # 顺序：有凭据的延期优先于非语义空白。这证明新分项**只能**从原本落入
    # `residual_gap` 的区间取值——它不可能把一段已被 typed gap 解释的文本改记成空白。
    owner_hit = ((0, 3, "comp-a"),)
    deferred_blank = _layout_layer_probe(
        ((0, "   ", (), owner_hit),),
        bindings=(SimpleNamespace(component_id="comp-a", admission="pending",
                                  admission_reason="synthetic_reason",
                                  gap_codes=(defer_code,)),))
    check(deferred_blank[0]["registered_deferred"] == 3
          and deferred_blank[0]["non_semantic_whitespace"] == 0,
          f"同一个空白区间只要有合法 typed gap 归属就仍记 `registered_deferred`"
          f"（延期优先，新分项不得截胡）：{deferred_blank[0]}")

    # 反例一：正文残余。判据是"整段空白"，不是"空白占比高"。
    prose = _layout_layer_probe(((0, "正文残余", (), ()),))
    check(prose[0]["residual_gap"] == 4
          and prose[0]["non_semantic_whitespace"] == 0,
          f"正文残余**不**进 `non_semantic_whitespace`：{prose[0]}")
    check(any(p.startswith("layout_text_residual") and p.endswith("#unowned")
              for p in prose[2]),
          f"正文残余如实留下未解释残余问题码（理由可诊断）：{prose[2]}")
    check(not TS.conservation_layer_eligible("layout_text", prose[0],
                                             prose[1], prose[2]),
          "只要有 1 个非空白未解释字符，该层资格即不成立")

    # 反例二：**无 typed gap** 的覆盖者。区间被一个正式终态为 pending 的 component
    # 完整覆盖，但该 binding 一个缺口码都没有 ⇒ 拒绝理由 `owner_ungapped:*`
    # （"错 gap"走的是同一条分支，其判据在第 5 节逐条断言）。正文仍进 `residual_gap`。
    gapless = _layout_layer_probe(
        ((0, "被阻断的正文", (), ((0, 6, "comp-e"),)),),
        bindings=(SimpleNamespace(component_id="comp-e", admission="pending",
                                  admission_reason="synthetic_reason",
                                  gap_codes=()),))
    check(gapless[0]["residual_gap"] == 6
          and gapless[0]["registered_deferred"] == 0
          and gapless[0]["non_semantic_whitespace"] == 0,
          f"无 typed gap 的覆盖者所辖正文仍进 `residual_gap`：{gapless[0]}")
    check(any("owner_ungapped" in p for p in gapless[2]),
          f"拒绝理由指名 owner 缺可延期缺口：{gapless[2]}")
    check(not TS.conservation_layer_eligible("layout_text", gapless[0],
                                             gapless[1], gapless[2]),
          "无 typed gap 的未解释字符阻止该层 balanced")

    # 反例三：重复认领。**纯空白不能把重复认领洗成 balanced**——同一片段上同范围
    # 同类别的主张出现两次照样留问题码，而问题码非空即资格不成立，与字符被记在
    # 哪一项无关。（"越界"走 `layout_text_ref_out_of_span`，同样只经问题码这一条路。）
    dup = _layout_layer_probe((
        (0, "   ", (), ()),
        (1, "重复认领", ((0, 4), (0, 4)), ()),
    ))
    dup_values = dup[0]
    check(dup_values["non_semantic_whitespace"] == 3
          and dup_values["residual_gap"] == 0
          and dup_values["cell_source_ref"] == 4,
          f"两行各归其位（空白 3 → 空白项，被认领的 4 → `cell_source_ref`）："
          f"{dup_values}")
    check(any("layout_text_duplicate_claim" in p for p in dup[2]),
          f"同一片段上的重复认领仍如实留下问题码：{dup[2]}")
    check(not TS.conservation_layer_eligible("layout_text", dup_values,
                                             dup[1], dup[2]),
          "纯空白 + 无残余**也**不能放行带重复认领的层"
          "（新增分项不是能把问题码洗掉的兜底项）")

    # 真实件上的方向性断言：新增项没有把真实未解释残余吞掉。这一条也是本模块
    # `_chain()` 里"公开入口拒绝签发"的成因（该样本的第三层确有实测残余）。
    real_values = _layer_values(_layer_of(ctx["final"], "layout_text"))
    check(real_values["residual_gap"] > 0
          and real_values["non_semantic_whitespace"] >= 0,
          f"真实样本上未解释残余仍非零、新分项没有吞掉它："
          f"residual_gap={real_values['residual_gap']}，"
          f"non_semantic_whitespace={real_values['non_semantic_whitespace']}")


#: 第三层合成的固定寻址（单页单行单片段）。
_PROBE_PAGE = 7
_PROBE_TABLE_BBOX = (0.0, 0.0, 100.0, 20.0)
#: 合成表的身份。**不是**可省的装饰：`_layout_text_scan` 的来源主张归属读数
#: `claims_of` 逐条带表 id（§0.19 逐表范围证明要能问出"这个字符是谁主张的"），
#: 生产的 `build_table_scope_readings` 传的是真表、必带 `table_id`。探针只喂
#: 一个不带身份的表就会在第一条 cell 主张上直接 `AttributeError`——那是夹具失真，
#: 不是生产缺陷。删掉这一项等于把归属轴静默旁路。
_PROBE_TABLE_ID = "probe-table-1"


def _layout_layer_probe(specs, bindings=()) -> tuple:
    """最小合成输入上跑**生产的**第三层分区（`FMB._layout_text_layer`）。

    `specs` 每项是一行：`(line_index, text, cell_refs, hits)`。`cell_refs` 是落在
    **同一个 cell** 上的来源片段范围（把同一个范围写两次就是"重复认领"）；`hits`
    是该行上 component 的 layout hit，形如 `(起始, 结束, component_id)`。

    `dispositions` 刻意留空，因此真实几何入口 `ctx.line_at` 不在本探针的路径上——
    `ctx=None` 就是这个事实的断言：哪天第三层开始读它，这里会立刻报错，而不会被
    静默替换成另一条路。片段进域的途径是 bbox 与表矩形相交，与真实件同一条件。

    `_GapIntervalPlan.empty()` 也是断言：本探针只测 `fmc-2` 的 component 路径与残余
    分支，**不**给 typed gap 区间路径送料。哪天这两条路径的边界被改坏，这里的期望值
    会直接失配，而不是被一条顺手的缺口区间悄悄兜住。
    """
    lines = []
    cells = []
    hits_by_component: dict = {}
    for line_index, text, cell_refs, hits in specs:
        span = SimpleNamespace(text=text, char_start=0, char_end=len(text),
                               bbox=(0.0, float(line_index), 100.0,
                                     float(line_index) + 1.0))
        lines.append(SimpleNamespace(line_index=line_index, is_furniture=False,
                                     spans=(span,)))
        if cell_refs:
            cells.append(SimpleNamespace(source_refs=tuple(
                SimpleNamespace(interval=SimpleNamespace(
                    page_number=_PROBE_PAGE, line_index=line_index,
                    span_index=0, span_char_range=(a, b)))
                for (a, b) in cell_refs)))
        for (a, b, component_id) in hits:
            hits_by_component.setdefault(component_id, []).append(
                SimpleNamespace(page_number=_PROBE_PAGE, line_index=line_index,
                                layout_span_index=0, span_char_range=(a, b)))
    components = tuple(
        # landing 刻意**不是** `body_span`：`body_span` 的 hit 会把该片段直接划进
        # `final_paragraph_ref`（那是"正文段落"，不是本探针要测的残余分支）。
        SimpleNamespace(component_id=cid, landing="table_inside",
                        layout_hits=tuple(items))
        for cid, items in hits_by_component.items())
    root = SimpleNamespace(
        components=components, dispositions=(),
        page_layout=SimpleNamespace(pages=(SimpleNamespace(
            page_number=_PROBE_PAGE, lines=tuple(lines)),)))
    table = SimpleNamespace(
        table_id=_PROBE_TABLE_ID,
        page_number=_PROBE_PAGE, page_bbox=_PROBE_TABLE_BBOX,
        rows=(SimpleNamespace(cells=tuple(cells)),) if cells else (),
        title_blocks=(), unit_blocks=(), note_blocks=())
    scan = FMB._layout_text_scan(root, None, (table,))
    binding_of = {b.component_id: b for b in bindings}
    return FMB._layout_text_layer(scan, binding_of, FMB._GapIntervalPlan.empty())


def _legacy_conservation_payload(version: str = "fmc-1") -> dict:
    """自带的 legacy 守恒载荷（按**该版本当时的**分项表生成）。

    `fmc-1`：四项分项，没有 `registered_deferred` / `non_semantic_whitespace`。
    `fmc-2`：分项名与 current 逐项相同，但资格较窄。

    取值刻意让**每一层**算术守恒、`layout_text` 的 `residual_gap` 非 0 且层内
    无问题码：这正是旧口径下"看起来 balanced"的载荷，也是新语义下必须拒绝读入的
    那种载荷——旧口径无法表达"这 5 个字符到底是被延期还是没人管"。
    """
    terms_by_kind = TS.LEGACY_CONSERVATION_TERMS_BY_VERSION[version]
    kind_of = TS.LEGACY_CONSERVATION_TERM_KIND_BY_VERSION[version]
    layers = []
    for kind, terms in terms_by_kind.items():
        values = {}
        for name in terms:
            values[name] = 5 if name == "residual_gap" else 0
        values[terms[0]] = 10
        total = sum(values.values())

        def _term(name, value):
            term_kind = kind_of[name]
            payload = {"term": name, "term_kind": term_kind,
                       "count": 0, "char_count": 0, "interval_length": 0}
            payload[_KIND_FIELD[term_kind]] = value
            return payload

        layers.append({
            "layer_kind": kind,
            "terms": [_term(name, values[name]) for name in terms],
            "total": _term(terms[0], total),
            "balanced": True,
            "problems": [],
        })
    return {
        "schema_type": "FinalMaterialConservation",
        "scope": "document",
        "conservation_locator": "loc-fmc-0123456789abcdef",
        "conservation_id": "fmc-0123456789abcdef",
        "schema_version": version,
        "upstream_dependency_fingerprint": "0" * 64,
        "layers": layers,
        "problems": [],
    }


# ---------------------------------------------------------------------------
# K8. §六 `tsg-2`：精确缺口源区间的正例与失败关闭组
# ---------------------------------------------------------------------------

def _forged_gaps(gaps, edits):
    """把若干条缺口换成改造版并重建台账（身份与派生字段交给 `create` 重算）。

    `edits`：`gaps.entries` 下标 → 替换后的 `TableGapEntry`。排序键与
    `blocking_entry_count` 都按改造后的结果重算，因此变体本身**内部自洽**——
    下面每一条失败都不来自"台账自相矛盾"，而只来自被改坏的那个区间。
    """
    entries = tuple(sorted(
        (edits.get(i, e) for i, e in enumerate(gaps.entries)),
        key=lambda e: (e.gap_kind, e.detail_code, e.page_number or 0,
                       e.table_id or "", e.component_id or "",
                       e.disposition_id or "")))
    return TS.TableStructureGap.create(
        **{**_all_fields(gaps), "entries": entries,
           "blocking_entry_count":
               sum(1 for e in entries if e.blocks_document_capability)})


def _replace_intervals(entry, intervals):
    return TS.TableGapEntry(
        gap_kind=entry.gap_kind, detail_code=entry.detail_code,
        page_number=entry.page_number, table_id=entry.table_id,
        component_id=entry.component_id, disposition_id=entry.disposition_id,
        blocks_document_capability=entry.blocks_document_capability,
        source_intervals=tuple(intervals))


def _gap_recheck_text(gaps_obj, final, roots) -> str:
    """在给定台账上跑**两条独立复核路径**并把诊断文本拼起来。

    两条路径各自独立实现，不读 builder 的缺口区间计划：`_check_gaps` 对着当次
    PageLayout 逐条重算区间（片段是否存在 / 是否越界），`_recheck_layout_text_layer`
    从载荷反推覆盖索引、重算第三层守恒。任一条报出的问题码都足以说明该台账不可读回。
    """
    snapshot = _rebuild_snapshot(
        final, gaps=gaps_obj,
        blocking_gap_count=gaps_obj.blocking_entry_count)
    texts: list = []
    try:
        FV._check_gaps(snapshot, roots)
    except FV.FinalVerificationError as exc:
        texts.append(str(exc))
    _counts, _total, problems = FV._recheck_layout_text_layer(snapshot, roots)
    texts.extend(problems)
    return " | ".join(texts)


def _k8_gap_source_intervals(ctx) -> None:
    """`tsg-2` 的精确源区间：真实件上是**部分正例 + 如实的负结果**（§八(8)(9)）。

    正例部分证明这条资格路径在真实文档上真的在跑，且台账本身**形状正确**：
    449 条缺口、4131 段内容寻址区间，每条区间都对得上本份 PageLayout 的真实片段、
    正长度、不重复、页锚点自洽；复核器给出的问题码里**没有任何**"指向不存在的
    片段 / 越出片段 / 覆盖了不该覆盖的字符"——即区间没有写偏、写宽。

    负结果部分同样必须如实记录（§七）：真实件上每一个"无人认领"的原子区间都同时被
    **两条种类不同**的 typed gap 覆盖（`unresolved_geometry` 的
    `candidate_rejected:candidate_straddles_frozen_range` × `unsupported_table_structure`
    的候选/处置缺口）。复核器按设计**不在两条不同的类型化终态之间二选一**，因此这些
    字符如实留在 `residual_gap` 并记 `缺口区间重叠` / `未解释残余`。这是 §六 的终态
    (4)"留在残留并阻止签发"，不是"没有 typed gap"，也不是可以靠放宽判据消除的假阳性；
    本组用独立判据把这两点分别钉住：问题码**只**可能是重叠类，且每个 `未解释残余`
    片段在本组独立建立的覆盖索引里确实被 ≥2 条区间覆盖。

    失败关闭组再逐条证明它**不可能**被用来洗白：指错片段 / 越界 / 写宽覆盖已消费字符 /
    两条缺口重复消费同一段字符，各自被独立复核器指名拒绝；同一缺口内部的重复与重叠
    则在构造期就被挡住。最后一条证明区间**入了快照签名**，因此"自报区间"不成立。
    """
    final = ctx["final"]
    roots = _roots(ctx)
    gaps = final.gaps
    lines: dict = {}
    for page in roots.page_layout.pages:
        for line in page.lines:
            lines[(page.page_number, line.line_index)] = line

    indexed = [(i, e) for i, e in enumerate(gaps.entries) if e.source_intervals]
    interval_count = sum(len(e.source_intervals) for _i, e in indexed)
    check(len(indexed) > 0 and interval_count > 0,
          f"真实样本上存在 `tsg-2` 精确源区间（{len(indexed)} 条缺口、"
          f"{interval_count} 段区间）——否则本组的正例与失败关闭组都无从构造")
    check(all(e.gap_kind in TS.CONSERVATION_DEFERRED_GAP_KINDS
              for _i, e in indexed),
          "只有**可延期种类**的缺口才携带源区间"
          "（阻断型缺口不得用区间把自己说成已延期）")
    page_numbers = {page.page_number for page in roots.page_layout.pages}
    check(all(iv.page_number in page_numbers
              for _i, e in indexed for iv in e.source_intervals),
          "每条源区间都锚定在本份 PageLayout 的真实页上（区间的域是本份文档）")
    anchored = [(i, e) for i, e in indexed if e.page_number is not None]
    misplaced = [(e.detail_code, e.page_number, iv.page_number)
                 for _i, e in anchored for iv in e.source_intervals
                 if iv.page_number != e.page_number]
    check(not misplaced,
          f"缺口身份带页锚点时，区间页号必须与它同源（不一致 {len(misplaced)} 项："
          f"{misplaced[:3]}）；只有身份不带页锚点的处置型缺口"
          f"（{len(indexed) - len(anchored)} 条）才由区间自己锚页")
    bad: list = []
    for _i, e in indexed:
        seen = set()
        for iv in e.source_intervals:
            line = lines.get(iv.fragment_key[:2])
            if line is None or iv.fragment_key[2] >= len(line.spans):
                bad.append((e.detail_code, iv.fragment_key))
                continue
            text = line.spans[iv.fragment_key[2]].text
            a, b = iv.span_char_range
            if b > len(text) or b <= a:
                bad.append((e.detail_code, iv.fragment_key, (a, b), len(text)))
            if iv.sort_key() in seen:
                bad.append((e.detail_code, iv.fragment_key, "duplicated"))
            seen.add(iv.sort_key())
    check_aggregate(bad, interval_count,
                    "每条源区间都对得上**本份** PageLayout 的真实片段、为正长度且"
                    "不重复（内容寻址而不是自报坐标）")

    # --- 正例 1：区间没有写偏 / 写宽（形状类问题一条都不许有） ----------------
    by_frag: dict = {}
    for i, e in indexed:
        for iv in e.source_intervals:
            by_frag.setdefault(iv.fragment_key, []).append(
                (int(iv.span_char_range[0]), int(iv.span_char_range[1]), i))
    overlapping = {fk for fk, ivs in by_frag.items()
                   if any(x[0] < y[1] and y[0] < x[1] for x, y in _pairs(ivs))}

    counts, _total, problems = FV._recheck_layout_text_layer(final, roots)
    real = _layer_values(_layer_of(final, "layout_text"))
    interval_problems = [p for p in problems if "缺口区间" in p]
    shape_problems = [p for p in interval_problems
                      if not p.startswith("缺口区间重叠:")]
    check(not shape_problems,
          "真实件的源区间没有写偏或写宽：复核器的区间类问题里**没有**"
          "『指向不存在的片段 / 越出片段 / 覆盖了不该覆盖的字符』"
          f"（实得 {shape_problems[:3]}）")
    named = {p[len("缺口区间重叠:"):].rsplit("#", 1)[0] for p in interval_problems}
    check(interval_problems and named == {str(fk) for fk in overlapping},
          "复核器的区间类问题**只**是设计内的『两条缺口争同一段字符』，且它点名的片段"
          "与本组独立扫出的重叠片段集合**逐项相同**"
          f"（问题 {len(interval_problems)} 项、片段 {len(named)} 个 / "
          f"独立扫出 {len(overlapping)} 个）")

    # --- 正例 2：第三层由复核器独立重算得到同一个值 -------------------------
    check(counts == real and counts["registered_deferred"] > 0
          and counts["residual_gap"] > 0,
          f"`fmc-3` 的区间资格路径在真实件上确实生效且与 builder 逐项一致："
          f"registered_deferred={counts['registered_deferred']}、"
          f"residual_gap={counts['residual_gap']}"
          f"（区间路径没有把残余洗成 0）")

    # --- 负结果：如实留下的残余**有** typed gap，只是两条争用 ---------------
    residual_problems = [p for p in problems if p.startswith("未解释残余")]
    residual_frags = {p.split(":", 1)[1].rsplit("#", 1)[0]
                      for p in residual_problems}
    residual_reasons = {p.rsplit("#", 1)[1] for p in residual_problems}
    check(residual_problems and residual_frags <= {str(fk) for fk in overlapping},
          "每一个如实留下的 `未解释残余` 片段**确实被 ≥2 条 typed gap 争用**"
          f"（{len(residual_problems)} 项问题、{len(residual_frags)} 个片段，"
          "全部落在独立扫出的重叠片段集合内）——它们不是『没有 typed gap』，"
          "而是『两条种类不同的类型化终态争同一段字符』")
    check(bool(residual_reasons)
          and residual_reasons <= {"unowned", "multi_owner"},
          "这些残余字符的拒绝理由都是**结构性**的（无 component 覆盖 / 覆盖者多于一个），"
          f"不是某个可自报的口径或『有凭据的延期』（实得 {sorted(residual_reasons)}）")

    i0, e0 = indexed[0]
    iv0 = e0.source_intervals[0]
    span_text = lines[iv0.fragment_key[:2]].spans[iv0.fragment_key[2]].text

    # --- (9a) 指向不存在的排版片段 ---------------------------------------
    bogus_line = max(li for (pg, li) in lines if pg == iv0.page_number) + 7
    bogus = TS.TableGapSourceInterval(
        page_number=iv0.page_number, line_index=bogus_line, span_index=0,
        span_char_range=(0, 1))
    text = _gap_recheck_text(
        _forged_gaps(gaps, {i0: _replace_intervals(e0, [bogus])}), final, roots)
    check("缺口源区间指向不存在的排版片段" in text,
          f"源区间指向不存在的行/片段时复核器指名拒绝（{text[:160]!r}）")

    # --- (9b) 越出片段字符范围 -------------------------------------------
    over = TS.TableGapSourceInterval(
        page_number=iv0.page_number, line_index=iv0.line_index,
        span_index=iv0.span_index,
        span_char_range=(0, len(span_text) + 1))
    text = _gap_recheck_text(
        _forged_gaps(gaps, {i0: _replace_intervals(e0, [over])}), final, roots)
    check("缺口源区间越出片段字符范围" in text,
          f"源区间越出片段长度时复核器指名拒绝（{text[:160]!r}）")

    # --- (9c) 写宽：把缺口区间挪到已被 cell 正式消费的字符上 ---------------
    #     生产件里缺口区间**不可能**落在这里（上面的形状类断言已证明），所以这条反例
    #     只能靠伪造：直接借一条真实 cell 的来源区间当"写宽"的目标。
    claimed_ranges: dict = {}
    for table in final.tables:
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    iv = ref.interval
                    claimed_ranges.setdefault(
                        (iv.page_number, iv.line_index, iv.span_index),
                        []).append(tuple(iv.span_char_range))
    usable = sorted(fk for fk in claimed_ranges
                    if fk[:2] in lines and fk[2] < len(lines[fk[:2]].spans))
    check(bool(usable),
          f"样本 {_TRUST_KEY} 上存在被 cell 正式消费、且能对上本份 PageLayout 的"
          "真实片段（否则本反例无从构造）")
    # 伪造区间必须落在**宿主缺口自己的页**上（条目页锚点与区间的域同源），因此挑一条
    # 页锚点为空、或本页就有 cell 消费片段的缺口作为宿主。
    by_page: dict = {}
    for fk in usable:
        by_page.setdefault(fk[0], fk)
    host = next(((i, e) for i, e in indexed
                 if e.page_number is None or e.page_number in by_page), None)
    if host is None:
        check(False, f"样本 {_TRUST_KEY} 上存在页锚点可与 cell 消费片段对齐的带区间缺口"
                     "（否则本反例无从构造）")
    else:
        hi_, eh = host
        witness = (by_page[eh.page_number] if eh.page_number is not None
                   else by_page[sorted(by_page)[0]])
        wa, wb = sorted(claimed_ranges[witness])[0]
        wide = TS.TableGapSourceInterval(
            page_number=witness[0], line_index=witness[1],
            span_index=witness[2], span_char_range=(wa, wb))
        text = _gap_recheck_text(
            _forged_gaps(gaps, {hi_: _replace_intervals(eh, [wide])}), final, roots)
        check("缺口区间覆盖了不该覆盖的字符" in text,
              "把缺口区间挪到**已被 cell 消费**的字符上时复核器指名拒绝"
              "（缺口区间必须逐字符对上无人认领的原子）"
              f"（{text[:160]!r}）")

    # --- (9d) 两条缺口重复消费同一段字符 ---------------------------------
    pair = next(((a, b) for a, b in _pairs(indexed)
                 if b[1].page_number == a[1].page_number), None)
    if pair is None:
        check(False, "样本 `NDSD_KCZ_2026` 必须存在同页的两条带区间缺口"
                     "（否则'重复消费'反例无从构造）")
    else:
        (i, e), (j, f) = pair
        doubled = _forged_gaps(gaps, {j: _replace_intervals(f, e.source_intervals)})
        text = _gap_recheck_text(doubled, final, roots)
        check("缺口区间重叠" in text,
              "两条缺口覆盖同一段字符时复核器拒绝（不得重复消费；"
              f"一个字符只能有一个 typed 终态）（{text[:160]!r}）")
        # 非空转的**方向**断言：重复主张不得让 `registered_deferred` 变多。
        after, _t, _p = FV._recheck_layout_text_layer(
            _rebuild_snapshot(final, gaps=doubled,
                              blocking_gap_count=doubled.blocking_entry_count),
            roots)
        check(after["registered_deferred"] <= counts["registered_deferred"],
              f"重复主张不会把同一段字符计两次"
              f"（{after['registered_deferred']} <= "
              f"{counts['registered_deferred']}）")

    # --- (9e) 同一缺口内部的重复与重叠：构造期即挡 ------------------------
    dup = _capture(lambda: _replace_intervals(e0, [iv0, iv0]),
                   SchemaValidationError)
    check(dup[0] is not None and "不得重复" in dup[1],
          f"同一缺口内重复登记同一段区间在构造期被拒（{dup[1][:120]!r}）")
    nudged = TS.TableGapSourceInterval(
        page_number=iv0.page_number, line_index=iv0.line_index,
        span_index=iv0.span_index,
        span_char_range=(iv0.span_char_range[0] + 1, iv0.span_char_range[1] + 1))
    overlap = _capture(lambda: _replace_intervals(e0, [iv0, nudged]),
                       SchemaValidationError)
    check(overlap[0] is not None and "上重叠" in overlap[1],
          f"同一缺口内同片段上互相重叠的两段区间在构造期被拒"
          f"（{overlap[1][:120]!r}）")

    # --- (9f) 错误文档 / 版本：区间随宿主缺口一起被根束驳回 ----------------
    raised = _capture(lambda: FV._check_root_bundle(
        _rebuild_snapshot(final, document_version="dv-other"),
        roots), FV.FinalVerificationError)
    check(raised[0] is not None and "document_version" in raised[1],
          "源区间所属快照的文档版本与经验证 roots 不一致时被驳回"
          f"（{raised[1][:160]!r}）")

    # --- 区间入签名：改一个区间即改快照内容指纹 ---------------------------
    forged = _rebuild_snapshot(
        final, gaps=_forged_gaps(gaps, {i0: _replace_intervals(e0, [over])}),
        blocking_gap_count=gaps.blocking_entry_count)
    check(forged.content_fingerprint != final.content_fingerprint,
          "源区间参与快照内容指纹：改一段区间即改身份"
          "（区间不是不入签名的自报字段）")


def _pairs(items):
    """`items` 的两两组合（保序，只取 i < j）。"""
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            yield items[a], items[b]


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------

_GROUPS = (
    ("K1-wire-contract", _k1_wire_contract),
    ("K2-three-level-catches", _k2_three_level_catches),
    ("K3-per-hop-provenance", _k3_per_hop_provenance),
    ("K4-mixed-citability", _k4_mixed_citability),
    ("K5-exact-set-landing", _k5_exact_set_landing),
    ("K6-gap-honesty", _k6_gap_honesty),
    ("K7-fmc2-vocabulary", _k7_deferred_vocabulary),
    ("K8-gap-source-intervals", _k8_gap_source_intervals),
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
    try:
        _estore._db_path = _EVIDENCE_DB
        ctx = _chain()
        for name, fn in _GROUPS:
            _run_group(name, fn, ctx)
    finally:
        _estore._db_path = previous_db_path
        after = _identity_bundle()
        check(before == after,
              "只读前置：`data/evidence.db` 与 `data/financial_v2.db`"
              "（含 -wal / -shm）在本模块前后逐项不变（size/mtime_ns/sha256）")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

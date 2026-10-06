# -*- coding: utf-8 -*-
"""TS4 T13–T19：正文范围切分 / 五类处置 / 零 span 节点 / 末节点边界 / confidence 与
资格策略阶段 / TS3 unassigned 继承引用 / `sb-1`–当前 TS4 正文版本矩阵（计划 §18.13）。

覆盖（全部离线：只读本地 PDF 与**只读** `data/evidence.db`；无网络、无 LLM、无写入）：

- **T13** §18.6.1 run 切分：表格 `inside` / `adjacent` / 空行 / 非 `body_under_node`
  行四类切断各自生效，跨页 run 合法；
- **T14** §18.9.2 五类处置互斥且完备；`regular` 绑定 `span_id`，其余四类恒为 `None`；
- **T15** 节点零 span 合法：内容全在表格范围内的节点不产 span、有
  `BodyRangeDisposition(range_kind="table_inside")`、简介为 `table_only_pending_ts5`；
- **T16** §18.6.3 末节点不越界：只有真正抵达文档末的 run 才用 `document_end`（0.90），
  真实边界（formal-unassigned / non-content / 表格 / 空行）处必须停住，且不产生
  `below_last_heading`；表格邻接边界含当前正文算法（`sb-7`）的**表格邻接闭包**
  （前导向上 + 尾部向下）其成员资格由
  `test_tree_span_table_adjacency.verified_closure` 用几何独立重算复核；
- **T17** §18.8.5/§18.8.6：逐边界成因正例、未知成因拒绝、Evidence 数量不影响候选分值、
  A/B 策略与快照身份、历史 A 快照在 B 常量下仍按 `policy_id` 复核但 completion 恒 False、
  策略 authority 指纹的非循环重算与敏感性（approval/frozen 槽位为空）；
- **T18** §18.12.1/§18.12.2：冻结产物的旧 unassigned 对象 os-4 round-trip 等值、ID 精确
  进入 `inherited_unassigned_span_ids`、不出现在任何新 span/component/coverage/synopsis；
- **T19** §18.11.2③：`sb-1`（TS3 unassigned 与历史正文）与当前 TS4 正文版本互不
  冒充；已退役的 TS4 正文版本（`TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS`）对象仍可
  os-4 round-trip 且身份独立，但塞进快照一律由 TS4 复核层结构性拒绝（不得静默改签）；
  no-evidence-set 夹具只做 Layout/Outline 核验并显式登记 gap。

真实现场只跑一份文档（`NDSD_KCZ_2026`：§18.12.1 中它贡献 21/232 个旧 unassigned）。
其余三份的旧对象以**冻结产物**（`evaluation/results/..._p2final_20260918T130000Z/`，
只读）为权威锚点：四份文档的 unassigned 合计 232（1 / 97 / 113 / 21）。

每个 `check` 都设计成"对应生产逻辑被移除即失败"：四类切断用**独立重算**的合法行集合
核对；span 文本用 `canonical_text` 逐行重建；置信度用策略因子表重算；继承引用用冻结
产物逐 ID 核对；复核层反例一律走"自洽篡改 + 独立重建"的正式拒绝路径。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path

from evidence import store as _estore

from evals import tree_stage_env as STAGE
from document_structure import outline_builder as OB
from document_structure import schema as S
from document_structure import span_builder as SB
from document_structure import span_policy as SP
from document_structure import span_schema as SS
from document_structure import span_verifier as SV
from document_structure import synopsis as SY
from document_structure import versions as V
from document_structure.aligner import align_evidence_set_verified
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    canonical_text,
    sha256_canonical,
)
from document_structure.evidence_gateway import bind_current_evidence_authority
from document_structure.layout_builder import build_verified_page_layout
from document_structure.normalization import tight
from document_structure.span_schema import BoundaryFactor, SpanQualificationPolicy

#: TS4-A P1-A 反例模块：本文件复用它的**独立重算**原语（闭包成员资格的几何判据），
#: 以便 T16 的右边界模型能覆盖表格前导闭包，而不必再写第二套判定。
from evals import test_tree_span_table_adjacency as TA  # noqa: E402

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


# ---------------------------------------------------------------------------
# 0. 真实现场（只读）与冻结产物锚点
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_DB_PATH = _REPO / "data" / "evidence.db"
_PDF = _REPO / "data" / "samples" / "300750" / "announcements" / "NDSD_KCZ_2026.pdf"
_COMPANY_ID = "300750"
_DOCUMENT_ID = "NDSD_KCZ_2026"
_TS3_RUN = (_REPO / "evaluation" / "results"
            / "tree_structure_ts3_outline_ts3_outline_tocr2_closure_p2final_20260918T130000Z")

#: §18.12.1 的四份文档 unassigned 数（合计 232）。本轮现场只重建 `NDSD_KCZ_2026`，
#: 其余三份以冻结产物为权威锚点：因此这里既断言**冻结侧**逐份数量，也断言合计 232。
_FROZEN_UNASSIGNED = (
    ("FIXTURE_BOND_2026", 1),
    ("NDSD_2024_year", 97),
    ("NDSD_2025_year", 113),
    ("NDSD_KCZ_2026", 21),
)

_FROZEN_CACHE: dict = {}
_CHAIN: dict = {}


def _db_identity(path: Path) -> dict:
    """库/PDF 的三元身份（大小 / mtime_ns / sha256）：运行前后必须逐项不变。"""
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _frozen_unassigned(document_id: str) -> list:
    """冻结 TS3 产物里的 unassigned span 原始 JSON（只读；带缓存）。"""
    if document_id not in _FROZEN_CACHE:
        path = _TS3_RUN / document_id / "document_outline.json"
        _FROZEN_CACHE[document_id] = json.loads(
            path.read_text(encoding="utf-8"))["unassigned"]
    return _FROZEN_CACHE[document_id]


def _frozen_json(*parts: str) -> dict:
    return json.loads((_TS3_RUN.joinpath(*parts)).read_text(encoding="utf-8"))


class _FactsView:
    """`_collect_line_facts` 的**只读投影**：只暴露它真正读取的两个字段。

    生产路径由 `_build_span_input` 提供完整 capability；本文件验证的是"逐行事实的
    派生"这一纯函数，因此用只含这两个字段的视图喂入**同一实现**，避免为同一份输入
    再付一次完整重建（`_build_span_input` 会重跑 outline）的代价。字段名与
    `SpanBuildInput` 一致，命名冲突会立刻 AttributeError 而不是静默通过。
    """

    __slots__ = ("page_layout", "structure_snapshot")

    def __init__(self, page_layout, structure_snapshot) -> None:
        self.page_layout = page_layout
        self.structure_snapshot = structure_snapshot


class _State:
    """`_LineFact.state` 的最小投影：内部纯函数只读 `body_attachment`。"""

    __slots__ = ("body_attachment",)

    def __init__(self, body_attachment=None) -> None:
        self.body_attachment = body_attachment


def _fact(page: int, line: int, kind: str, *, node_id="n-1",
          attachment="preceding_heading", table_scope=None, table_reason=None):
    """构造一条内部逐行事实（只用于 `_split_runs` / 边界成因真值表的正反例）。"""
    return SB._LineFact(
        key=(page, line), page=None, line=None, state=_State(attachment),
        kind=kind, node_id=node_id, table_scope=table_scope,
        table_reason=table_reason)


def _distinct_scopes():
    """表格范围内存的两种取值（与 `outline_builder` 的常量名对表）。"""
    return (OB.TABLE_SCOPE_INSIDE, OB.TABLE_SCOPE_ADJACENT)


def _scope_of(scopes: dict, key) -> str | None:
    entry = scopes.get(key)
    if entry is None:
        return None
    return entry[0] if entry[0] in _distinct_scopes() else None


def _chain() -> dict:
    """跑一次真实链路并缓存（布局 → 对齐 → 交接 → 快照）。只读 PDF 与只读库。"""
    if _CHAIN:
        return _CHAIN
    authority = bind_current_evidence_authority()
    layout = build_verified_page_layout(
        _PDF, company_id=_COMPANY_ID, document_id=_DOCUMENT_ID)
    alignment = align_evidence_set_verified(layout, authority)
    # 本模块的基线是**TS4-A 版**产物（含 A 版交接）：进入 B 之后必须显式把阶段拨回 A
    # （`evals.tree_stage_env`，退出即逐字还原），而不是绕过生产代码的阶段门。
    with STAGE.simulated_a_environment():
        handoff = SB.issue_live_ts3_handoff(layout, alignment, authority)
        snapshot = SB.build_span_snapshot(handoff, stage="distribution_only")
    _CHAIN.update({
        "authority": authority, "layout_capability": layout, "alignment": alignment,
        "handoff": handoff, "snapshot": snapshot,
    })
    _prepare(_CHAIN)
    return _CHAIN


def _prepare(ctx: dict) -> None:
    """由受信交接派生本文件需要的只读索引（不改动任何生产对象）。"""
    snap = ctx["snapshot"]
    handoff = ctx["handoff"]
    layout = handoff.layout_capability.layout
    ctx["layout"] = layout
    ctx["outline"] = handoff.document_outline
    ctx["structure"] = handoff.structure_snapshot
    ctx["policy"] = snap.qualification_policy
    ctx["facts"] = SB._collect_line_facts(
        _FactsView(layout, handoff.structure_snapshot))
    ctx["runs"] = SB._split_runs(ctx["facts"])
    ctx["fact_index"] = {f.key: i for i, f in enumerate(ctx["facts"])}
    ctx["scopes"] = OB.table_region_scopes(layout)
    ctx["line_by_key"] = {(p.page_number, line.line_index): (p, line)
                          for p in layout.pages for line in p.lines}
    ctx["state_by_key"] = {(s.page_number, s.line_index): s
                           for s in handoff.structure_snapshot.line_states}
    ctx["d_by_key"] = {(d.start_page, d.start_line): d for d in snap.dispositions}
    ctx["span_by_id"] = {s.span_id: s for s in snap.spans}


# ---------------------------------------------------------------------------
# T13 §18.6.1：四类切断各自生效 + 跨页 run 合法
# ---------------------------------------------------------------------------

def _test_t13(ctx):
    snap = ctx["snapshot"]
    facts = ctx["facts"]
    scopes = ctx["scopes"]
    line_by_key = ctx["line_by_key"]
    state_by_key = ctx["state_by_key"]

    # (1) **独立重算**"哪些行允许出现在 span 里"：必须恰为
    #     body_under_node ∧ preceding_heading ∧ 不在表格范围内 ∧ tight(text) != ""。
    #     调用方不复用 `_collect_line_facts` 的结论，只读版式行 + 结构终态 + 表格范围。
    allowed = set()
    excluded = {"table_inside": 0, "table_adjacency": 0, "empty": 0, "non_body": 0}
    for key, state in state_by_key.items():
        scope = _scope_of(scopes, key)
        text = tight(line_by_key[key][1].text)
        if state.state != "body_under_node":
            excluded["non_body"] += 1
            continue
        if scope == OB.TABLE_SCOPE_INSIDE:
            excluded["table_inside"] += 1
            continue
        if scope == OB.TABLE_SCOPE_ADJACENT:
            excluded["table_adjacency"] += 1
            continue
        if text == "":
            excluded["empty"] += 1
            continue
        if state.body_attachment != "preceding_heading":
            excluded["non_body"] += 1
            continue
        allowed.add(key)

    for name in ("table_inside", "table_adjacency", "non_body"):
        check(excluded.get(name, 0) > 0,
              f"T13 切断类别 {name!r} 在本文档中确有真实样本（否则对应断言是空的）："
              f"{excluded}")
    absent = [name for name in ("table_inside", "table_adjacency", "empty", "non_body")
              if excluded.get(name, 0) <= 0]
    check(absent in ([], ["empty"]),
          f"T13 唯一允许在本文档中缺席的切断类别是 empty 空行（本文档没有空正文行；"
          f"空行切分由 (3) 合成序列与 T14 的处置真值表覆盖；其余任何类别缺席都说明"
          f"切分逻辑退化）：{excluded}")
    check(len(allowed) > 100,
          f"T13 合法正文行数必须显著大于 0：{len(allowed)}")

    illegal = [(sp.span_id, key) for sp in snap.spans
               for key in sp.layout_line_refs if key not in allowed]
    check(not illegal,
          "T13 任何 span 的行引用都不得包含四类切断行（表格内 / 表格邻接 / 空行 / "
          f"非 body_under_node 行）：{illegal[:5]}")

    # (2) run 切分自身的结构不变量：源序覆盖、段内键一致、相邻键必不同。
    flat = [f.key for run in ctx["runs"] for f in run]
    check(flat == [f.key for f in facts],
          f"T13 run 切分必须不重不漏地覆盖全部逐行事实且保持源序（{len(flat)} 行）")
    check(all(SB._run_merge_key(run[-1]) == SB._run_merge_key(run[0])
              for run in ctx["runs"]),
          "T13 同一 run 内的合并键必须一致")
    check(all(SB._run_merge_key(ctx["runs"][i][-1])
              != SB._run_merge_key(ctx["runs"][i + 1][0])
              for i in range(len(ctx["runs"]) - 1)),
          "T13 相邻 run 的合并键必须不同（否则它们本该是一个 run）")
    check(len(ctx["runs"]) > len(snap.spans),
          f"T13 真实文档的 run 数必须多于 span 数（家具/表格/空行确实切断了）："
          f"{len(ctx['runs'])} vs {len(snap.spans)}")

    # (3) 合成事实序列：四类切断各自生效（每类都给出一个"必须断开"的边界），
    #     并证明**跨页**（页 1 行 6 → 页 2 行 0）不是切断条件。
    inside = _fact(1, 2, "table_inside", table_scope=OB.TABLE_SCOPE_INSIDE,
                   table_reason=OB.TABLE_REGION_OWN_ROW_REASON)
    adjacent = _fact(1, 3, "table_adjacency",
                     table_scope=OB.TABLE_SCOPE_ADJACENT,
                     table_reason=OB.TABLE_REGION_ADJACENT_REASON)
    seq = (
        _fact(1, 0, "heading_node", node_id="n-0"),
        _fact(1, 1, "regular"),
        inside,
        adjacent,
        _fact(1, 4, "empty"),
        _fact(1, 5, "non_content", node_id=None),
        _fact(1, 6, "regular"),
        _fact(2, 0, "regular"),
    )
    got = [(run[0].key, run[-1].key, run[0].kind) for run in SB._split_runs(seq)]
    expected = [
        ((1, 0), (1, 0), "heading_node"),
        ((1, 1), (1, 1), "regular"),
        ((1, 2), (1, 2), "table_inside"),
        ((1, 3), (1, 3), "table_adjacency"),
        ((1, 4), (1, 4), "empty"),
        ((1, 5), (1, 5), "non_content"),
        ((1, 6), (2, 0), "regular"),
    ]
    check(got == expected,
          f"T13 四类切断各自生效、且跨页 run 合法（合成序列）：{got}")
    cross_page = [run for run in SB._split_runs(seq)
                  if (run[0].key[0], run[-1].key[0]) == (1, 2)]
    check(len(cross_page) == 1 and len(cross_page[0]) == 2,
          "T13 跨页 run 必须被当作**同一个** run（页边界不是切断条件）")

    # (4) 真实现场：确实存在跨页 run，且其行引用严格升序不重复。
    real_cross = [sp for sp in snap.spans if sp.page_range[0] != sp.page_range[1]]
    check(len(real_cross) >= 1,
          f"T13 真实文档中存在跨页 regular run（{len(real_cross)} 个）")
    check(all(tuple(sorted(sp.layout_line_refs)) == sp.layout_line_refs
              and len(set(sp.layout_line_refs)) == len(sp.layout_line_refs)
              for sp in real_cross),
          "T13 跨页 run 的行引用仍按 (页, 行) 严格升序且不重复")


# ---------------------------------------------------------------------------
# T14 §18.9.2：五类处置互斥且完备
# ---------------------------------------------------------------------------

def _expected_disposition_starts(run):
    """该正文 run 应有的处置记录**起点**（本模块对 W1 分段规则的独立重算）。

    W1（`sb-8`）之后规则是：`table_inside` 跨页时在构造期按页分段，每段一条单页记录，
    段起点即该页在 run 内的**首帧**；其余 kind（含 `table_adjacency` / `unassigned` /
    `empty`）仍是一条，起点即 run 首帧。这里按同一规则独立算一遍，用来核对构造结果
    —— 而不是从产物反推期望（那样任何产出都会被自身证明为对）。
    """
    if run[0].kind != "table_inside":
        return [run[0].key]
    starts: list = []
    for frame in run:
        if not starts or starts[-1][0] != frame.key[0]:
            starts.append(frame.key)
    return starts


def _test_t14(ctx):
    snap = ctx["snapshot"]
    state_by_key = ctx["state_by_key"]
    d_by_key = ctx["d_by_key"]
    span_by_id = ctx["span_by_id"]

    body_runs = [run for run in ctx["runs"] if run[0].kind in set(SB._BODY_KINDS)]
    reg_runs = [run for run in body_runs if run[0].kind == "regular"]
    non_reg_runs = [run for run in body_runs if run[0].kind != "regular"]
    check(len(snap.spans) == len(reg_runs),
          f"T14 每个 regular run 恰有一个 span：{len(snap.spans)} vs {len(reg_runs)}")

    # (0) 处置基数：regular 一 run 一条，非 regular 一 run 一条**每页段**一条。
    expected_total = len(reg_runs) + sum(len(_expected_disposition_starts(r))
                                         for r in non_reg_runs)
    check(len(snap.dispositions) == expected_total,
          f"T14 处置基数必须等于 regular run 数 + 非 regular run 的页段数："
          f"{len(snap.dispositions)} vs {expected_total}")
    expected_starts: list = []
    for run in body_runs:
        expected_starts.extend(_expected_disposition_starts(run))
    check(sorted((d.start_page, d.start_line) for d in snap.dispositions)
          == sorted(expected_starts),
          f"T14 处置起点集必须恰为「每 run 每页段首帧」："
          f"缺 {sorted(set(expected_starts) - {(d.start_page, d.start_line) for d in snap.dispositions})[:5]} / "
          f"多 {sorted({(d.start_page, d.start_line) for d in snap.dispositions} - set(expected_starts))[:5]}")
    cross_inside = [r for r in non_reg_runs
                    if r[0].kind == "table_inside"
                    and r[0].key[0] != r[-1].key[0]]
    check(len(snap.dispositions) - len(body_runs)
          == sum(len(_expected_disposition_starts(r)) - 1 for r in non_reg_runs),
          f"T14 多出来的处置必须恰好来自按页分段的跨页 `table_inside` run："
          f"{len(snap.dispositions) - len(body_runs)} 条 vs "
          f"{len(cross_inside)} 个跨页表内 run（共 "
          f"{sum(len(_expected_disposition_starts(r)) - 1 for r in non_reg_runs)} 个页段增量）")
    check(not cross_inside
          or all(len(_expected_disposition_starts(r)) == len({f.key[0] for f in r})
                 for r in cross_inside),
          f"T14 每个跨页 `table_inside` run 的页段数必须等于它跨的页数（{len(cross_inside)} 个）")

    # (1) 互斥：处置区间两两不重叠，且按起点严格升序。
    spans_sorted = sorted((d.start_page, d.start_line, d.end_page, d.end_line)
                          for d in snap.dispositions)
    check(all(spans_sorted[i][2:4] < spans_sorted[i + 1][0:2]
              for i in range(len(spans_sorted) - 1)),
          "T14 处置区间必须两两不重叠（五类互斥）")

    # (2) 完备：处置覆盖的行数恰为全部 body_under_node 行。
    body_lines = [key for key, state in state_by_key.items()
                  if state.state == "body_under_node"]
    covered = sum(d.line_count for d in snap.dispositions)
    check(covered == len(body_lines),
          f"T14 处置必须恰好覆盖全部 body_under_node 行：{covered} vs {len(body_lines)}")
    check(set(d.range_kind for d in snap.dispositions) <= set(SS.BODY_RANGE_KINDS),
          "T14 range_kind 必须全部落在 §18.9.2 的五类词表内")

    # (3) regular 有 span_id；其余四类恒为 None（空值真值表的可观测形式）。
    reg = [d for d in snap.dispositions if d.range_kind == "regular"]
    non_reg = [d for d in snap.dispositions if d.range_kind != "regular"]
    check(all(isinstance(d.span_id, str) and d.span_id != "" for d in reg),
          f"T14 regular 处置必须绑定非空 span_id（{len(reg)} 条）")
    check(all(d.span_id is None for d in non_reg),
          f"T14 其余四类处置必须 span_id is None（{len(non_reg)} 条）")
    check(len(set(d.range_kind for d in non_reg)) >= 2,
          f"T14 非 regular 处置至少出现两类（真实样本）："
          f"{sorted(set(d.range_kind for d in non_reg))}")

    # (4) regular 处置与 run / span **逐项**对应；文本由版式行独立重建。
    problems = []
    for run in reg_runs:
        d = d_by_key.get(run[0].key)
        if d is None or d.range_kind != "regular":
            problems.append(("缺 regular 处置", run[0].key))
            continue
        if (d.end_page, d.end_line) != run[-1].key:
            problems.append(("终点不符", run[0].key, (d.end_page, d.end_line),
                             run[-1].key))
        if d.line_count != len(run):
            problems.append(("行数不符", run[0].key, d.line_count, len(run)))
        if d.tight_char_count != sum(len(tight(f.line.text)) for f in run):
            problems.append(("tight 字符数不符", run[0].key))
        span = span_by_id.get(d.span_id)
        if span is None:
            problems.append(("span 不存在", d.span_id))
            continue
        if tuple(span.layout_line_refs) != tuple(f.key for f in run):
            problems.append(("行引用与 run 不符", span.span_id))
        text = " ".join(canonical_text(f.line.text) for f in run)
        if span.normalized_text != text:
            problems.append(("规范化文本与逐行重建不符", span.span_id))
        if span.char_range != (0, len(text)):
            problems.append(("char_range 不符", span.span_id, span.char_range))
        if span.node_id != run[0].node_id:
            problems.append(("节点归属不符", span.span_id))
        if span.confidence != d.confidence:
            problems.append(("span 与处置的 confidence 不一致", span.span_id))
    check(not problems,
          "T14 regular 处置与 run / span 必须逐项对应（终点 / 行数 / tight 字符数 / "
          f"行引用 / 规范化文本 / 节点 / confidence）：{problems[:5]}")

    # (5) 非 regular 处置与 run 逐项对应（含 node_id / table_scope / unassigned_reason
    #     的空值真值表）。W1 之后一个 run 可能对应**多条**记录（跨页 `table_inside`
    #     按页分段），因此对应关系是"run ↔ 它的页段记录序列"：逐段查空值真值表，
    #     整条 run 查范围并集 / 行数与 tight 字符数的加法律。
    problems = []
    for run in non_reg_runs:
        starts = _expected_disposition_starts(run)
        segs = [d_by_key.get(start) for start in starts]
        missing = [start for start, d in zip(starts, segs) if d is None]
        if missing:
            problems.append(("缺处置", run[0].key, missing[:3]))
            continue
        for d in segs:
            if d.range_kind != run[0].kind:
                problems.append(("类别不符", run[0].key, d.range_kind, run[0].kind))
            if run[0].kind == "unassigned":
                if d.node_id is not None or d.unassigned_reason is None:
                    problems.append(("unassigned 必须无 node 且有原因",
                                     d.disposition_id))
                elif d.unassigned_reason not in S.UNASSIGNED_REASONS:
                    problems.append(("原因码未登记", d.unassigned_reason))
            elif d.unassigned_reason is not None:
                problems.append(("非 unassigned 不得带原因", d.disposition_id))
            if run[0].kind == "empty" and d.node_id != run[0].node_id:
                problems.append(("empty 的节点归属不符", d.disposition_id))
            if run[0].kind in ("table_inside", "table_adjacency"):
                if (d.table_scope != run[0].table_scope
                        or d.table_reason != run[0].table_reason):
                    problems.append(("表范围证据不符", d.disposition_id))
                # W1 的分段不变式**只覆盖 `table_inside`**（本步指令原文与
                # §20.9.10.1 的 W1 行都只要求它）：`table_inside` 分段后的每一条都必须
                # 是**单页**记录，否则它仍会撞上 §19.5.1 的单页不变式而在冻结范围通道里
                # 拿不到唯一物理矩形。
                #
                # 跨页的 `table_adjacency` **不在** W1 范围内，见下面的
                # `t14_cross_page_table_adjacency`：它不是"通过"，是**如实记下的剩余项**
                # （`final_material_builder` 的两处 `start_page != end_page: continue`
                # 同样会跳过它）。把它写成 FAIL 或把它悄悄放行，都是把剩余项说成别的
                # 东西；这里只对本步承诺覆盖的那一类 fail-closed。
                if run[0].kind == "table_inside" and d.start_page != d.end_page:
                    problems.append(("表范围记录必须单页", d.disposition_id,
                                     (d.start_page, d.end_page)))
        # 整条 run 的范围并集 = 分段序列的首尾拼接；行数 / tight 字符数必须可加。
        if (segs[0].start_page, segs[0].start_line) != run[0].key:
            problems.append(("起点不符", run[0].key,
                             (segs[0].start_page, segs[0].start_line)))
        if (segs[-1].end_page, segs[-1].end_line) != run[-1].key:
            problems.append(("终点不符", run[0].key,
                             (segs[-1].end_page, segs[-1].end_line), run[-1].key))
        if sum(d.line_count for d in segs) != len(run):
            problems.append(("行数不符", run[0].key,
                             sum(d.line_count for d in segs), len(run)))
        if sum(d.tight_char_count for d in segs) \
                != sum(len(tight(f.line.text)) for f in run):
            problems.append(("tight 字符数不符", run[0].key))
        if len(segs) > 1:
            pages = [d.start_page for d in segs]
            if pages != sorted(pages) or len(set(pages)) != len(pages):
                problems.append(("页段页序不符", run[0].key, pages))
            for left, right in zip(segs, segs[1:]):
                if (left.end_page, left.end_line) >= (right.start_page,
                                                      right.start_line):
                    problems.append(("页段互相重叠", run[0].key))
    check(not problems,
          f"T14 非 regular 处置必须与 run 的页段序列逐项对应（类别 / 范围并集 / "
          f"加法律 / 空值真值表）：{problems[:5]}")

    # W1 的**正面读数**：跨页 `table_inside` 记录必须为 0（这是本步唯一的实现承诺）。
    check(not [d for d in snap.dispositions
               if d.range_kind == "table_inside"
               and d.start_page != d.end_page],
          "T14 跨页 `table_inside` 处置记录必须为 0（W1 在构造期按页分段的正面读数）")

    # W1 **范围之外**的剩余项：跨页 `table_adjacency` 仍会留在产物里。如实记下每条读数
    # 并单独报出——既不写成 FAIL（W1 从未承诺覆盖它，本步指令原文只要求
    # `table_inside`），也不静默放过（这些字符在 `final_material_builder` 的两处
    # `start_page != end_page: continue` 下仍会被跳过，属 W10 的剩余阻断点）。
    cross_adj = [d for d in snap.dispositions
                 if d.range_kind == "table_adjacency"
                 and d.start_page != d.end_page]
    _results["t14_cross_page_table_adjacency"] = [
        {"disposition_id": d.disposition_id, "pages": [d.start_page, d.end_page],
         "line_count": d.line_count, "tight_char_count": d.tight_char_count,
         "table_scope": d.table_scope, "table_reason": d.table_reason}
        for d in cross_adj]
    _results["details"].append(
        "__T14_RESIDUAL__ 跨页 `table_adjacency`（W1 范围外，W10 剩余阻断点）："
        f"{_results['t14_cross_page_table_adjacency']}")

    # (6) 与正文层守恒桶交叉核对：五桶行数必须能由处置重算得出。
    by_kind = {}
    for d in snap.dispositions:
        by_kind[d.range_kind] = by_kind.get(d.range_kind, 0) + d.line_count
    layer = {b.bucket: b.line_count for b in snap.conservation.body_layer}
    expected = {
        "regular": by_kind.get("regular", 0),
        "table_adjacency_provisional": by_kind.get("table_adjacency", 0),
        "table_inside": by_kind.get("table_inside", 0),
        "unassigned": by_kind.get("unassigned", 0),
        "empty": by_kind.get("empty", 0),
    }
    check(layer == expected,
          f"T14/§18.9.2 正文层五桶行数必须由处置重算得出：{layer} vs {expected}")

    doc_layer = {b.bucket: b.line_count for b in snap.conservation.document_layer}
    four = {}
    for state in state_by_key.values():
        four[state.state] = four.get(state.state, 0) + 1
    check(doc_layer == {"heading": four.get("heading_node", 0),
                        "formal_unassigned": four.get("formal_unassigned", 0),
                        "non_content": four.get("non_content", 0),
                        "body": four.get("body_under_node", 0)},
          f"T14/§18.9.1 文档层四桶必须与结构终态逐行重算一致：{doc_layer} vs {four}")


# ---------------------------------------------------------------------------
# T15：节点零 span 合法
# ---------------------------------------------------------------------------

def _test_t15(ctx):
    snap = ctx["snapshot"]
    policy = ctx["policy"]
    node_ids = sorted({n.node_id for n in ctx["outline"].nodes})
    span_nodes = {sp.node_id for sp in snap.spans}
    zero = [nid for nid in node_ids if nid not in span_nodes]
    check(len(zero) >= 1,
          f"T15 真实文档存在零 span 节点（{len(zero)} / {len(node_ids)} 个节点）")

    syn_by_node = {s.node_id: s for s in snap.synopses}
    check(set(syn_by_node) == set(node_ids),
          f"T15 每个真实 outline 节点恰有一份简介（含零正文节点）："
          f"{len(syn_by_node)} vs {len(node_ids)}")

    # "节点内容全在表格范围内"：该节点的全部处置都是表范围（调用方按真实处置筛选，
    # 不构造理想数据）。
    table_only = []
    for nid in zero:
        kinds = [d.range_kind for d in snap.dispositions if d.node_id == nid]
        if kinds and all(k in ("table_inside", "table_adjacency") for k in kinds):
            table_only.append(nid)
    check(len(table_only) >= 1,
          f"T15 存在「内容全在表格范围内」的节点（{len(table_only)} 个）")

    problems = []
    for nid in table_only:
        kinds = [d.range_kind for d in snap.dispositions if d.node_id == nid]
        if "table_inside" not in kinds:
            problems.append((nid, "缺 table_inside 处置", kinds))
        if any(sp.node_id == nid for sp in snap.spans):
            problems.append((nid, "不得有 span"))
        syn = syn_by_node[nid]
        if syn.status != "synopsis_unavailable" \
                or syn.reason_code != "table_only_pending_ts5":
            problems.append((nid, syn.status, syn.reason_code))
        if syn.snippets != () or syn.source_span_ids != ():
            problems.append((nid, "不得有片段 / 来源 span"))
    check(not problems,
          "T15 全表格节点必须无 span、有 table_inside 处置、简介为 "
          f"table_only_pending_ts5 且无片段：{problems[:3]}")

    # 构造：把"节点内容全在表格范围内"直接交给简介构建器（不依赖真实文档恰好命中）。
    syn = SY.build_node_synopsis(
        node_id="node-table-only", spans=(), coverages={},
        kind_counts={"table_inside": 3, "empty": 2}, policy=policy)
    check(syn.status == "synopsis_unavailable"
          and syn.reason_code == "table_only_pending_ts5"
          and syn.snippets == () and syn.source_span_ids == (),
          f"T15 构造的全表格节点 → 无 span 且 table_only_pending_ts5："
          f"{syn.status}/{syn.reason_code}")
    check(SY.unavailable_reason(
        node_id="n", kind_counts={"empty": 1}, has_admissible_span=False,
        has_citable_coverage=False, snippet_count=0) == "empty_text",
        "T15 只有空行的节点必须给 empty_text（与全表格互斥）")
    check(SY.unavailable_reason(
        node_id="n", kind_counts={}, has_admissible_span=False,
        has_citable_coverage=False, snippet_count=0) == "no_span",
        "T15 无任何处置范围的节点必须给 no_span")
    check(SY.unavailable_reason(
        node_id="n", kind_counts={"unassigned": 2}, has_admissible_span=False,
        has_citable_coverage=False, snippet_count=0) == "no_span",
        "T15 只有 unassigned 范围的节点必须给 no_span（不得同义扩写为表格原因）")


# ---------------------------------------------------------------------------
# T16 §18.6.3：末节点不越界
# ---------------------------------------------------------------------------

def _test_t16(ctx):
    snap = ctx["snapshot"]
    facts = ctx["facts"]
    state_by_key = ctx["state_by_key"]
    scopes = ctx["scopes"]
    line_by_key = ctx["line_by_key"]

    # (1) `below_last_heading` 必须被彻底删除：词表、真值表、处置、序列化四处都没有。
    check("below_last_heading" not in SS.BOUNDARY_CAUSES_LEFT
          and "below_last_heading" not in SS.BOUNDARY_CAUSES_RIGHT,
          "T16 边界成因词表不得再含 below_last_heading")
    check("below_last_heading" not in set(SB._LEFT_CAUSE_BY_PREDECESSOR.values())
          and "below_last_heading" not in set(SB._RIGHT_CAUSE_BY_SUCCESSOR.values()),
          "T16 左右边界成因真值表不得再含 below_last_heading")
    check(all(d.unassigned_reason != "below_last_heading"
              for d in snap.dispositions),
          "T16 处置的 unassigned_reason 不得出现 below_last_heading")
    check("below_last_heading" not in canonical_json(snap.to_dict()),
          "T16 快照序列化结果中不得出现 below_last_heading（含任何嵌套字段）")

    # (2) 独立重算每个 span 的**右边界成因**：run 必须在真实边界处停止。
    successor_cause = {"heading_node": "next_heading",
                       "formal_unassigned": "formal_unassigned",
                       "non_content": "non_content"}
    keys = sorted(state_by_key)
    pos = {key: i for i, key in enumerate(keys)}
    # 后继的表格关系有**两个**真实来源：冻结 `trg-3` 本来就判定的区域状态，以及
    # 当前正文算法（`sb-7`）的表格邻接闭包（前导向上 + 尾部向下）。后者**不采信构建器自己的
    # 分类**：只有独立几何重算证明合格的行才算真实边界；声称吸收而重算不成立的行会被记成
    # 越界停止并单独报错。两个方向**共用**一个重算入口，行按方向各自被证明。
    #
    # 尾部方向的判据是**物理邻接**，与这一行是不是满栏正文无关（满栏表注与普通正文
    # 段落都会合格），因此这里**不得**再把"满栏"当成越界证据。
    closure, over_absorbed = TA.verified_closure(facts)
    check(not over_absorbed,
          "T16 被吸收的表格邻接行必须经独立几何重算成立（不得吞掉跨页 / 跨节点 / "
          "栏不交叠 / 间距超阈值 / 夹着家具行 / 无 inside_table 区域的行）："
          f"{over_absorbed[:5]}")
    problems = []
    observed = {}
    closure_boundaries = 0
    for d in snap.dispositions:
        if d.range_kind != "regular":
            continue
        end_key = (d.end_page, d.end_line)
        index = pos[end_key]
        if index == len(keys) - 1:
            expected = "document_end"
        else:
            nxt = keys[index + 1]
            state = state_by_key[nxt]
            scope = _scope_of(scopes, nxt)
            if state.state != "body_under_node":
                expected = successor_cause[state.state]
            elif state.body_attachment != "preceding_heading":
                # 后继是"未归属"正文行：它同样是一个真实边界（run 必须在此停止），
                # 但真值表里**没有**它能用的原因——出现即说明边界被判错。
                expected = "unassigned_successor"
            elif scope == OB.TABLE_SCOPE_INSIDE:
                expected = "table_inside"
            elif scope == OB.TABLE_SCOPE_ADJACENT:
                expected = "table_adjacency"
            elif nxt in closure:
                # 表格邻接闭包行（冻结 scope 为 none）：独立重算已证明它紧邻同一
                # 节点的已证明表格区域（在上方或在下方），因此它同样是真实右边界。
                expected = "table_adjacency"
                closure_boundaries += 1
            elif tight(line_by_key[nxt][1].text) == "":
                expected = "empty"
            else:
                expected = "regular_successor"
        observed[expected] = observed.get(expected, 0) + 1
        if expected != d.right_boundary_cause:
            problems.append((d.disposition_id, d.right_boundary_cause, expected))
    check(not problems,
          "T16 右边界成因必须等于按真实版式逐行重算的边界类别（run 在真实边界停止）："
          f"{problems[:5]}")
    check(observed.get("unassigned_successor", 0) == 0
          and observed.get("regular_successor", 0) == 0,
          "T16 不得出现「后继是未归属行 / 同节点正文行」的越界停止："
          f"{ {k: v for k, v in observed.items() if k.endswith('_successor')} }")
    check(len(closure) > 0,
          "T16 表格邻接闭包的独立重算必须非空（否则上面的边界模型是空转的）")
    _results["t16"] = {"closure_boundaries": closure_boundaries,
                       "closure_recomputed": len(closure),
                       "absorbed": sum(1 for f in facts
                                       if f.table_scope == TA.CLOSURE_SCOPE),
                       "observed": dict(sorted(observed.items()))}
    # 只有**真正以正文行结尾**的文档才存在 `document_end` run：本文档是否如此由版式
    # 独立判定（末行是家具行时不该有 document_end），因此这里断言的是一个双向条件，
    # 而不是"必须出现"。观察到的取值同时写进消息，便于人工核对现场。
    expected_document_end = 1 if facts[-1].kind == "regular" else 0
    check(observed.get("document_end", 0) == expected_document_end,
          f"T16 document_end 只属于真正抵达文档末的 regular run："
          f"末行类别={facts[-1].kind!r} → 期望 {expected_document_end}，"
          f"实际 {observed.get('document_end', 0)}")
    check(sum(1 for k, v in observed.items()
              if k not in ("document_end",)) >= 2,
          f"T16 真实边界（非文档末）至少出现两类：{observed}")

    # (3) `document_end` 只属于"真的抵达文档末"的 run：反向核对（只有 regular 范围
    #     才携带边界成因，故非 regular 范围不参与本项）。
    last_regular_end = [d for d in snap.dispositions
                        if d.range_kind == "regular"
                        and (d.end_page, d.end_line) == keys[-1]]
    check(len(last_regular_end) == expected_document_end
          and all(d.right_boundary_cause == "document_end"
                  for d in last_regular_end),
          f"T16 结束于文档末行的 regular 范围必须存在且恰给 document_end："
          f"{[(d.disposition_id, d.right_boundary_cause) for d in last_regular_end]}")
    # 0.90 **不是**上界（两侧都取 1.00 档时为 1.00），因此这里断言的是取值来源而非
    # 数值上界：任何 >0.90 的 regular 范围都必须两侧**同时**是 1.00 档（preceding_heading
    # / next_heading）；否则说明 min(...) 被绕过、某个低因子档被抬高。
    inflated = [(d.disposition_id, d.left_boundary_cause, d.right_boundary_cause,
                 d.confidence)
                for d in snap.dispositions
                if d.range_kind == "regular" and d.confidence > 0.90
                and not (d.left_boundary_cause == "preceding_heading"
                         and d.right_boundary_cause == "next_heading")]
    check(not inflated,
          f"T16 confidence >0.90 只允许来自「左 preceding_heading ∧ 右 next_heading」两档"
          f"同为 1.00：{inflated[:3]}")

    # (4) 真值表本身：中间位置永不给 document_end；末尾位置必须给 document_end。
    mid = (_fact(1, 0, "regular"), _fact(1, 1, "empty"))
    check(SB._right_boundary_cause(mid, 0) != "document_end",
          "T16 中间位置的右边界成因不得是 document_end（越界判定）")
    check(SB._right_boundary_cause(mid, 1) == "document_end",
          "T16 只有真正位于事实序列末尾的位置才给 document_end")
    check(SB._right_boundary_cause((_fact(1, 0, "regular"),), 0) == "document_end",
          "T16 单事实序列的末尾即文档末")


# ---------------------------------------------------------------------------
# T17 §18.8.5 / §18.8.6：因子、成因穷尽、Evidence 数量、A/B 阶段与 authority 指纹
# ---------------------------------------------------------------------------

def _test_t17(ctx):
    policy = ctx["policy"]
    snap = ctx["snapshot"]
    trusted = snap.trusted_input

    # (1) 因子表就是 §18.8.5 的冻结值（逐项，读的是快照内嵌的策略全文）。
    got = [(bf.side, bf.cause, bf.factor) for bf in policy.factor_entries]
    check(got == list(SP.TS4_A_FACTOR_VALUES),
          f"T17 快照内嵌因子表必须逐项等于 §18.8.5 冻结值（{len(got)} 项）")

    # (2) 每一种左右 boundary cause 都有正例（合成事实序列，逐成因）。
    #     真值表是 `行类别 → 成因`，因此键是 kind、值是 cause；逐项给出正例。
    for kind, cause in SB._LEFT_CAUSE_BY_PREDECESSOR.items():
        seq = (_fact(1, 0, kind,
                     node_id=(None if kind in ("formal_unassigned", "non_content")
                              else "n-1")),
               _fact(1, 1, "regular"))
        check(SB._left_boundary_cause(seq, 1) == cause,
              f"T17 左边界成因正例：前驱类别 {kind!r} → {cause!r}")
    for kind, cause in SB._RIGHT_CAUSE_BY_SUCCESSOR.items():
        seq = (_fact(1, 0, "regular"), _fact(1, 1, kind, node_id="n-1"))
        check(SB._right_boundary_cause(seq, 0) == cause,
              f"T17 右边界成因正例：后继类别 {kind!r} → {cause!r}")
    check(SB._right_boundary_cause((_fact(1, 0, "regular"),), 0) == "document_end",
          "T17 右边界成因正例：文档末 → 'document_end'")

    # (3) 真值表必须与词表**互为穷尽**（多一个没有生产者的原因码也算不一致）。
    check(set(SB._LEFT_CAUSE_BY_PREDECESSOR.values()) == set(SS.BOUNDARY_CAUSES_LEFT),
          "T17 左边界成因真值表的取值集合必须恰等于 BOUNDARY_CAUSES_LEFT")
    check(set(SB._RIGHT_CAUSE_BY_SUCCESSOR.values()) | {"document_end"}
          == set(SS.BOUNDARY_CAUSES_RIGHT),
          "T17 右边界成因真值表的取值集合必须恰等于 BOUNDARY_CAUSES_RIGHT")

    # (4) 未知 cause 一律拒绝（表外失败必须 fail-closed，不得猜一个默认值）。
    raises(lambda: SB._left_boundary_cause((_fact(1, 0, "regular"), _fact(1, 1, "regular")), 1),
           SB.SpanBuildError, "不在左边界成因真值表内",
           "T17 未登记的左边界前驱必须被拒绝")
    raises(lambda: SB._right_boundary_cause(
        (_fact(1, 0, "regular"), _fact(1, 1, "unassigned", attachment="before_first_heading")), 0),
        SB.SpanBuildError, "不在右边界成因真值表内",
        "T17 未登记的右边界后继必须被拒绝")
    raises(lambda: SB._left_boundary_cause((_fact(1, 0, "heading_node"),), 0),
           SB.SpanBuildError, "不存在合法的左边界成因",
           "T17 事实序列开头不得作为左边界成因")
    check(policy.factor_for("right", "document_end") == 0.90
          and policy.factor_for("left", "preceding_heading") == 1.00,
          "T17 §18.8.5 的取值抽查（document_end=0.90 / preceding_heading=1.00）")

    # (5) Evidence 数量不影响候选分值：同一 (左, 右) 成因组合内 confidence 恒等，
    #     且 confidence 恰为 quantize(min(左因子, 右因子))。
    reg = [d for d in snap.dispositions if d.range_kind == "regular"]
    groups: dict = {}
    for d in reg:
        groups.setdefault((d.left_boundary_cause, d.right_boundary_cause),
                          set()).add(d.confidence)
    check(all(len(v) == 1 for v in groups.values()),
          f"T17 同一对边界成因的 confidence 必须唯一（与 Evidence 数量无关）："
          f"{ {k: sorted(v) for k, v in groups.items()} }")
    check(len(groups) >= 3,
          f"T17 真实文档至少出现 3 种边界成因组合：{len(groups)}")
    problems = []
    for d in reg:
        expected = S.quantize(min(policy.factor_for("left", d.left_boundary_cause),
                                  policy.factor_for("right", d.right_boundary_cause)))
        if d.confidence != expected:
            problems.append((d.disposition_id, d.confidence, expected))
        span = ctx["span_by_id"].get(d.span_id)
        if span is not None and span.confidence != expected:
            problems.append((span.span_id, span.confidence, expected))
    check(not problems,
          f"T17 confidence 必须恰为 quantize(min(左因子, 右因子))：{problems[:3]}")
    ref_counts = [len(ctx["span_by_id"][d.span_id].component_evidence_refs)
                  for d in reg if d.span_id in ctx["span_by_id"]]
    check(len(set(ref_counts)) >= 2 and max(ref_counts) > 1,
          f"T17 真实文档内 span 的 Evidence 引用条数确有差异"
          f"（{min(ref_counts)}..{max(ref_counts)}），而 confidence 只由成因决定")

    # (6) 非循环 authority 指纹：由固定目录里的**文件字节**独立重算。
    registry = json.loads((SP.POLICY_DIR / SP.REGISTRY_FILENAME)
                          .read_text(encoding="utf-8"))
    entry = registry["policies"][policy.policy_key]
    entry_sha = sha256_canonical(dict(entry))
    record = json.loads((SP.POLICY_DIR / entry["file"]).read_text(encoding="utf-8"))
    dist_sha = sha256_canonical(record)
    provider = V.QUALIFICATION_POLICY_PROVIDER_VERSION
    recomputed = sha256_canonical((provider, entry_sha, policy.policy_id, dist_sha,
                                   None, None))
    check(recomputed == trusted.qualification[12],
          "T17 快照绑定的 provider authority 指纹必须能由固定目录文件字节独立重算"
          "（非循环：不采信任何对象自报值）")
    altered = {
        "distribution": sha256_canonical((provider, entry_sha, policy.policy_id,
                                          "f" * 64, None, None)),
        "approval": sha256_canonical((provider, entry_sha, policy.policy_id,
                                      dist_sha, "0" * 64, None)),
        "frozen": sha256_canonical((provider, entry_sha, policy.policy_id,
                                    dist_sha, None, "0" * 64)),
        "provider": sha256_canonical((provider + "-next", entry_sha, policy.policy_id,
                                      dist_sha, None, None)),
    }
    check(len(set(altered.values()) | {recomputed}) == 5,
          "T17 authority 指纹的任一输入（distribution / approval / frozen / provider "
          "版本）单独改变都必须得到不同的指纹")
    check(list(trusted.qualification[7:11]) == [None, None, None, None],
          "T17 TS4-A 的 qualification[7:11]（approval / frozen / machine index / "
          "review attestation 四个保留槽位）必须为空："
          f"{list(trusted.qualification[7:11])}")
    check(trusted.qualification[12] == recomputed
          and len(trusted.qualification[12]) == 64
          and trusted.qualification[11] == V.QUALIFICATION_POLICY_PROVIDER_VERSION,
          "T17 qualification[11] 必须为 provider 版本、[12] 必须为 64 位 authority 指纹"
          f"：{(trusted.qualification[11], trusted.qualification[12][:8])}")

    # (7) 调整一个因子 → 身份必变，且被注册表钉住的指纹拒绝。
    adjusted_factors = tuple(
        BoundaryFactor(side=bf.side, cause=bf.cause,
                       factor=(0.89 if bf.cause == "document_end" else bf.factor))
        for bf in policy.factor_entries)
    adjusted = SpanQualificationPolicy.create(
        policy_key=policy.policy_key, stage="distribution_only",
        span_confidence_min=None, factor_entries=adjusted_factors,
        completion_enabled=False, set_complete_supported=False,
        max_snippets_per_node=policy.max_snippets_per_node,
        max_snippet_chars=policy.max_snippet_chars,
        min_snippet_chars=policy.min_snippet_chars,
        max_total_snippet_chars=policy.max_total_snippet_chars,
        sentence_terminators=tuple(policy.sentence_terminators),
        closing_quotes=tuple(policy.closing_quotes))
    check(adjusted.policy_id != policy.policy_id
          and adjusted.policy_fingerprint != policy.policy_fingerprint,
          "T17 调整任一因子都会改变策略身份（policy_id 与指纹）")
    raises(lambda: SP.policy_provider_authority_fingerprint(adjusted),
           SP.PolicyResolutionError, "指纹",
           "T17 调整因子后的策略必须被注册表钉住的指纹拒绝")

    # (8) 复核层也必须走同一道门：换掉快照自载策略 → 复核拒绝。
    tampered = _clone(snap)
    object.__setattr__(tampered, "qualification_policy", adjusted)
    check(canonical_json(tampered.to_dict()) != canonical_json(snap.to_dict()),
          "T17 篡改体与原始快照必须确实不同（否则下面的反例没有意义）")
    raises(lambda: SV.verify_span_snapshot(tampered, ctx["handoff"]),
           SchemaValidationError, "指纹",
           "T17 快照自载策略的 authority 指纹不符时，正式复核必须拒绝")

    # (9) A/B：以 `versions.SPAN_CONFIDENCE_MIN` 为唯一输入，历史 A 快照在 B 环境下
    #     仍可按 policy_id 复核，但 completion 恒 False。
    saved = V.SPAN_CONFIDENCE_MIN
    # 模拟 B 阈值的取值：当前已是 B 就沿用**正式已批准值**（不得临时另编一个阈值），
    # 仍是 A 才临时拨到 0.80 来观察翻转。两条路径下断言完全相同。
    probe = 0.80 if saved is None else float(saved)
    try:
        V.SPAN_CONFIDENCE_MIN = probe
        table_b = SP.ab_gate_truth_table()
        check(table_b["stage"] == "threshold_enabled"
              and table_b["span_confidence_min"] == probe
              and table_b["completion_enabled"] and table_b["set_complete_supported"],
              f"T17 B 常量发布后 A/B 真值表必须整体翻转：{table_b}")
        # 无论进入 B 的方式是"临时拨常量"还是"本轮本来就已裁决"，A 条目的阶段与当前
        # 阶段不符，都被**同一条**解析门拒绝（`resolve_qualification_policy` 的
        # 阶段一致性检查，异常类型为 `PolicyResolutionError`，其基类是
        # `SchemaValidationError`）。子串取两者共有的那一段，避免断言挂在具体取值上。
        raises(SP.resolve_distribution_policy, SP.PolicyResolutionError,
               "与当前 A/B 阶段",
               "T17 B 阶段不得再解析 A 的分布口径策略（fail-closed）")
        verified_b = SV.verify_span_snapshot(snap, ctx["handoff"])
        check(verified_b.snapshot.snapshot_id == snap.snapshot_id
              and verified_b.issuer_scope == "live",
              "T17 历史的 A 快照在 B 环境里仍必须能复核成功（按 policy_id 对表）")
        check(SV.is_completion_eligible(verified_b, snap.spans[0].span_id) is False,
              "T17 历史 A 快照的 completion 必须恒为 False（分布口径不解锁完成）")
        b_policy = SpanQualificationPolicy.create(
            policy_key="span-qualification-threshold-v1",
            stage="threshold_enabled", span_confidence_min=probe,
            factor_entries=tuple(policy.factor_entries), completion_enabled=True,
            set_complete_supported=True,
            max_snippets_per_node=policy.max_snippets_per_node,
            max_snippet_chars=policy.max_snippet_chars,
            min_snippet_chars=policy.min_snippet_chars,
            max_total_snippet_chars=policy.max_total_snippet_chars,
            sentence_terminators=tuple(policy.sentence_terminators),
            closing_quotes=tuple(policy.closing_quotes))
        check(b_policy.policy_id != policy.policy_id
              and b_policy.policy_fingerprint != policy.policy_fingerprint,
              "T17 A/B 策略必须得到不同的 policy_id 与策略指纹")
        b_snapshot = _snapshot_with(snap, policy=b_policy)
        check(b_snapshot.snapshot_id != snap.snapshot_id
              and b_snapshot.snapshot_locator != snap.snapshot_locator,
              "T17 快照身份必须绑定 policy 版本（A/B 的 snapshot_id 与 locator 均不同）")
        check(b_snapshot.qualification_policy.stage == "threshold_enabled"
              and b_snapshot.span_builder_version == snap.span_builder_version
              and b_snapshot.spans == snap.spans,
              "T17 B 与 A 只差资格策略：正文算法版本与 span 集合不变")
        raises(lambda: SP.registry_entry_record(b_policy.policy_key),
               SP.PolicyResolutionError, "未登记",
               "T17 B 的策略记录尚未在固定资产目录登记（本轮不得伪造 B 资产）")
    finally:
        V.SPAN_CONFIDENCE_MIN = saved
    check(V.SPAN_CONFIDENCE_MIN == saved
          and SP.ab_gate_truth_table()["stage"] == (
              "distribution_only" if saved is None else "threshold_enabled"),
          f"T17 受控改写必须逐字还原到进入时的阶段，原值 {saved!r}，"
          f"得到 {V.SPAN_CONFIDENCE_MIN!r}")


# ---------------------------------------------------------------------------
# T18 §18.12.1 / §18.12.2：TS3 unassigned span 的继承引用
# ---------------------------------------------------------------------------

def _test_t18(ctx):
    snap = ctx["snapshot"]

    # (1) 冻结产物：232 个旧对象必须 os-4 round-trip 等值（逐份 + 合计）。
    problems = []
    counts = []
    frozen_ids: dict = {}
    for document_id, expected_count in _FROZEN_UNASSIGNED:
        entries = _frozen_unassigned(document_id)
        counts.append((document_id, len(entries)))
        if len(entries) != expected_count:
            problems.append((document_id, "数量不符", len(entries), expected_count))
        ids = []
        for index, entry in enumerate(entries):
            span = S.OutlineSpan.from_dict(entry)
            if canonical_json(span.to_dict()) != canonical_json(entry):
                problems.append((document_id, index, "round-trip 不等价"))
            if span.role != "unassigned" or span.node_id is not None:
                problems.append((document_id, index, "角色 / 归属不符"))
            if span.span_builder_version != V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION:
                problems.append((document_id, index, span.span_builder_version))
            if span.schema_version != V.SPAN_SCHEMA_VERSION:
                problems.append((document_id, index, span.schema_version))
            if span.is_fallback:
                problems.append((document_id, index, "旧 unassigned 不得是 fallback"))
            ids.append(span.span_id)
        if len(set(ids)) != len(ids):
            problems.append((document_id, "ID 重复"))
        frozen_ids[document_id] = ids
    check(not problems,
          f"T18 冻结产物的旧 unassigned 对象必须逐个 os-4 round-trip 等值且非 fallback："
          f"{problems[:5]}")
    total = sum(count for _doc, count in counts)
    check(total == 232,
          f"T18 四份冻结产物的旧 unassigned 合计必须为 232（§18.12.1）："
          f"{counts} → {total}")

    # (2) 现场重建：`NDSD_KCZ_2026` 的旧对象（重建后的 `DocumentOutline.unassigned`
    #     是这些对象的权威持有者）必须与冻结产物逐 ID 相同，并精确进入继承清单。
    frozen_kcz = sorted(frozen_ids[_DOCUMENT_ID])
    live_ids = sorted(s.span_id for s in ctx["outline"].unassigned)
    check(live_ids == frozen_kcz,
          f"T18 现场重建的 {_DOCUMENT_ID} unassigned ID 必须与冻结产物逐 ID 相同："
          f"{len(live_ids)} vs {len(frozen_kcz)}")
    check(list(snap.inherited_unassigned_span_ids) == frozen_kcz,
          f"T18 旧 ID 必须**精确**进入 inherited_unassigned_span_ids（按字典序、不重不漏）："
          f"{len(snap.inherited_unassigned_span_ids)} vs {len(frozen_kcz)}")

    # (3) 旧对象不得被复制进任何新集合。
    old = set(frozen_ids[_DOCUMENT_ID])
    span_hits = [sp.span_id for sp in snap.spans if sp.span_id in old]
    cov_hits = [c.span_id for c in snap.coverages if c.span_id in old]
    syn_hits = [n.node_id for n in snap.synopses if set(n.source_span_ids) & old]
    check(not (span_hits or cov_hits or syn_hits),
          "T18 旧 ID 不得出现在任何新 span / coverage / synopsis（不得被复制成新正文"
          f"材料）：{span_hits[:2]} {cov_hits[:2]} {syn_hits[:2]}")
    # 旧 ID 在 component 里**唯一**的合法出现方式是 `formal_unassigned` 落点（"引用锚点"
    # 而非"材料边界"）：不得以 body_span 落点被当成新正文材料使用。
    comp_hits = [(c.landing, c.span_id) for c in snap.components
                 if c.span_id in old]
    check(all(landing == "formal_unassigned" for landing, _sid in comp_hits),
          f"T18 旧 ID 只允许以 formal_unassigned 落点出现（{len(comp_hits)} 条）："
          f"{sorted(set(landing for landing, _s in comp_hits))}")
    check(all(sid in set(frozen_kcz) for _landing, sid in comp_hits),
          "T18 component 引用的 unassigned span_id 必须落在继承清单内（不得自造）："
          f"{sorted({sid for _l, sid in comp_hits} - set(frozen_kcz))[:3]}")

    # (4) 新旧 ID 无交集；新正文 span 一律非 fallback（C10 的双集合断言）。
    new_ids = [sp.span_id for sp in snap.spans]
    check(len(set(new_ids)) == len(new_ids),
          f"T18 新正文 span 的 ID 必须唯一：{len(new_ids)}")
    check(not (set(new_ids) & old),
          f"T18 新旧 ID 集合必须无交集（{len(new_ids)} 个新 / {len(old)} 个旧）")
    check(all(not sp.is_fallback and sp.fallback_derivation is None
              and not sp.is_cross_heading for sp in snap.spans),
          "T18/C10 新正文 span 一律不得是 fallback / cross-heading")
    check(all(sp.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION
              for sp in snap.spans),
          "T18 新正文 span 必须全部使用 TS4 正文算法版本")


# ---------------------------------------------------------------------------
# T19 §18.11.2③：sb-1 / 当前 TS4 正文版本 / 已退役版本 的版本矩阵
# ---------------------------------------------------------------------------

def _as_builder_version(span, version: str):
    """把同一个 span 的**内容**换成另一个算法版本的身份（重derive，不改内容）。"""
    locator_value = S.derive_span_locator(
        document_outline_locator=span.document_outline_locator,
        evidence_set_version=span.evidence_set_version,
        start_anchor=span.start_anchor, end_anchor=span.end_anchor,
        span_builder_version=version)
    span_id_value = S.derive_span_id(
        span_locator=locator_value, schema_version=span.schema_version,
        document_id=span.document_id, document_version=span.document_version,
        node_id=span.node_id, role=span.role,
        unassigned_reason=span.unassigned_reason, page_range=span.page_range,
        char_range=span.char_range, layout_line_refs=span.layout_line_refs,
        component_evidence_refs=span.component_evidence_refs,
        alignment_ids=span.alignment_ids, is_fallback=span.is_fallback,
        fallback_derivation=span.fallback_derivation,
        is_cross_heading=span.is_cross_heading, confidence=span.confidence,
        normalized_text=span.normalized_text)
    return dataclasses.replace(span, span_locator=locator_value,
                               span_id=span_id_value, span_builder_version=version)


def _clone(obj):
    """浅拷贝一个冻结 wire 对象（绕过 `__post_init__`），用于构造自洽篡改体。"""
    clone = object.__new__(type(obj))
    for name in obj.__dataclass_fields__:
        object.__setattr__(clone, name, getattr(obj, name))
    return clone


def _snapshot_with(snap, *, policy=None, spans=None, inherited=None):
    """用同一批内容重新定型一个快照（`create` 会重算指纹与身份）。"""
    return SS.SpanBuildSnapshot.create(
        qualification_policy=(snap.qualification_policy if policy is None else policy),
        document_id=snap.document_id, document_version=snap.document_version,
        page_layout_id=snap.page_layout_id, outline_id=snap.outline_id,
        alignment_schema_version=snap.alignment_schema_version,
        alignment_id=snap.alignment_id,
        structure_snapshot_id=snap.structure_snapshot_id,
        trusted_input=snap.trusted_input, dispositions=snap.dispositions,
        spans=(snap.spans if spans is None else spans),
        inherited_unassigned_span_ids=(snap.inherited_unassigned_span_ids
                                       if inherited is None else inherited),
        components=snap.components, coverages=snap.coverages,
        conservation=snap.conservation, synopses=snap.synopses,
        terminal_count=snap.terminal_count)


def _test_t19(ctx):
    snap = ctx["snapshot"]

    # (1) 旧（no-evidence-set）夹具的 sb-1 unassigned 仍可 os-4 round-trip。
    fixture = _frozen_unassigned("FIXTURE_BOND_2026")
    check(len(fixture) == 1
          and fixture[0]["evidence_set_version"] == "no-evidence-set"
          and fixture[0]["span_builder_version"] == "sb-1"
          and fixture[0]["schema_version"] == "os-4",
          f"T19 no-evidence-set 夹具仍是 os-4/sb-1 且 evidence_set_version="
          f"'no-evidence-set'：{ {k: fixture[0][k] for k in ('schema_version', 'span_builder_version', 'evidence_set_version')} }")
    fixture_span = S.OutlineSpan.from_dict(fixture[0])
    check(canonical_json(fixture_span.to_dict()) == canonical_json(fixture[0])
          and fixture_span.span_builder_version == "sb-1",
          "T19 冻结的 sb-1 unassigned 必须 os-4 round-trip 等值")

    # (2) 已归属正文的 sb-1：由现场 span 重新按 sb-1 定型（内容不变、身份随版本变），
    #     必须仍能 round-trip；且同一材料的 sb-1 / 当前 TS4 版本身份必不相同。
    source = snap.spans[0]
    sb1 = _as_builder_version(source, V.SPAN_BUILDER_VERSION)
    check(V.SPAN_BUILDER_VERSION == "sb-1"
          and sb1.span_builder_version == "sb-1",
          f"T19 既有 TS3 正文算法常量仍为 sb-1：{V.SPAN_BUILDER_VERSION!r}")
    check(canonical_json(S.OutlineSpan.from_dict(sb1.to_dict()).to_dict())
          == canonical_json(sb1.to_dict()),
          "T19 已归属的 sb-1 正文 span 必须能被 os-4 读层 round-trip")
    check(sb1.span_id != source.span_id and sb1.span_locator != source.span_locator,
          "T19 同一正文材料在 sb-1 / 当前 TS4 版本下必须是不同身份（版本进入身份）")
    check(V.SPAN_BUILDER_VERSION in S.SPAN_BUILDER_VERSIONS
          and V.TS4_BODY_SPAN_BUILDER_VERSION in S.SPAN_BUILDER_VERSIONS,
          f"T19 os-4 兼容读层必须同时接受 TS3 与当前 TS4 正文版本："
          f"{S.SPAN_BUILDER_VERSIONS}")

    # (3) TS3 重建仍写 sb-1（unassigned 一律 sb-1）。
    check(all(s.span_builder_version == V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION
              for s in ctx["outline"].unassigned),
          "T19 TS3 重建产生的 unassigned span 必须仍写 sb-1")
    # (3-b) 版本矩阵：三个轴互不相同；已退役的 TS4 正文版本仍在册（历史产物可读），
    #       但**不在**当前值上（不得静默把历史产物重解释成当前算法）。
    check(V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION == "sb-1"
          and V.TS4_BODY_SPAN_BUILDER_VERSION != V.SPAN_BUILDER_VERSION
          and V.TS4_BODY_SPAN_BUILDER_VERSION
          != V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION
          and V.TS4_BODY_SPAN_BUILDER_VERSION
          not in V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS
          and "sb-2" in V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS,
          f"T19 版本矩阵：unassigned={V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION!r} / "
          f"TS3 正文={V.SPAN_BUILDER_VERSION!r} / "
          f"TS4 正文={V.TS4_BODY_SPAN_BUILDER_VERSION!r} / "
          f"退役={V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS}")

    # (4) TS4 快照的新正文只接受当前 TS4 正文版本（结构性拒绝）。
    check(all(sp.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION
              for sp in snap.spans),
          f"T19 快照的新正文 span 必须全部为 "
          f"{V.TS4_BODY_SPAN_BUILDER_VERSION!r}（{len(snap.spans)} 个）")
    raises(lambda: _snapshot_with(snap, spans=(sb1,) + snap.spans[1:]),
           SchemaValidationError, "必须使用 TS4 正文算法",
           "T19 把历史 assigned sb-1 塞进快照的 spans 必须被拒绝")
    # (4-b) 退役的 TS4 正文版本：对象仍可读、身份独立，但塞进当前 TS4 快照必须被
    #       结构性拒绝——版本升级不是"顺手改签"，历史产物也不会被重新解释。
    for retired in V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS:
        legacy_span = _as_builder_version(source, retired)
        check(legacy_span.span_builder_version == retired
              and legacy_span.span_id != source.span_id
              and legacy_span.span_locator != source.span_locator
              and canonical_json(S.OutlineSpan.from_dict(legacy_span.to_dict()).to_dict())
              == canonical_json(legacy_span.to_dict()),
              f"T19 退役 TS4 正文版本 {retired!r} 的对象必须仍可读且身份独立")
        raises(lambda s=legacy_span: _snapshot_with(snap, spans=(s,) + snap.spans[1:]),
               SchemaValidationError, "必须使用 TS4 正文算法",
               f"T19 退役版本 {retired!r} 的正文对象塞进当前 TS4 快照必须被拒绝"
               f"（不得静默重解释）")
    unassigned_current = _as_builder_version(
        S.OutlineSpan.from_dict(fixture[0]), V.TS4_BODY_SPAN_BUILDER_VERSION)
    check(unassigned_current.span_builder_version
          == V.TS4_BODY_SPAN_BUILDER_VERSION
          and unassigned_current.role == "unassigned",
          f"T19 由旧夹具派生的 unassigned "
          f"{V.TS4_BODY_SPAN_BUILDER_VERSION} 对象（用于下面的反例）")
    raises(lambda: _snapshot_with(snap, spans=(unassigned_current,) + snap.spans[1:]),
           SchemaValidationError, "role 必须为 'body'",
           "T19 把 unassigned 对象塞进快照的 spans 必须被拒绝")

    # (5) 正式复核层：自洽篡改（成员换掉 + 全部指纹同步重算）仍必须被独立重建发现。
    tampered = _clone(snap)
    object.__setattr__(tampered, "spans", (sb1,) + snap.spans[1:])
    check(canonical_json(tampered.to_dict()) != canonical_json(snap.to_dict()),
          "T19 篡改体必须与原始快照不同（否则反例没有意义）")
    raises(lambda: SV.verify_span_snapshot(tampered, ctx["handoff"]),
           SV.SpanVerificationError, "独立重建结果与待复核快照不等",
           "T19 历史 assigned sb-1 混入 spans 后，TS4 复核必须拒绝")

    tampered = _clone(snap)
    inherited = tuple(sorted(snap.inherited_unassigned_span_ids
                             + (unassigned_current.span_id,)))
    object.__setattr__(tampered, "inherited_unassigned_span_ids", inherited)
    check(sha256_canonical(_content_payload(tampered))
          != tampered.content_fingerprint,
          "T19 继承清单被改动后，其自载 content_fingerprint 必然不再自洽")
    raises(lambda: SV.verify_span_snapshot(tampered, ctx["handoff"]),
           SV.SpanVerificationError, "不等",
           "T19 由旧夹具派生的 unassigned 对象混入继承清单后，TS4 复核必须拒绝")

    # (6) 旧 no-evidence-set 夹具只做 Layout/Outline 核验，并显式登记 TS4 待办 gap。
    manifest = _frozen_json("FIXTURE_BOND_2026", "manifest.json")
    validation = _frozen_json("FIXTURE_BOND_2026", "structure_validation.json")
    check(manifest["alignment"]["evidence_set_version"] is None
          and manifest["alignment"]["terminals_emitted"] == 0
          and manifest["alignment"]["block_count"] == 0,
          f"T19 夹具没有任何 Evidence 成员与对齐终态：{manifest['alignment']}")
    check(manifest["stats"]["pending_ts4_span_builder"] is True
          and validation["ok"] is True
          and validation["checks"]
          and all(c.startswith("PASS") for c in validation["checks"]),
          "T19 夹具的核验只覆盖 Layout/Outline 且全部通过（"
          f"{len(validation['checks'])} 项）")
    check(not any("span" in name for name in manifest["artifacts"]),
          f"T19 夹具产物清单中不得出现任何 span 级产物：{manifest['artifacts']}")
    check(not any("span" in path.lower()
                  for path in _frozen_index_paths()
                  if path.startswith("FIXTURE_BOND_2026/")),
          "T19 冻结 run 目录里该夹具没有任何 span 产物（缺口必须显式，不得伪造）")


def _content_payload(snap) -> dict:
    """快照内容指纹的载荷（与 `SpanBuildSnapshot._content_payload` 同构，独立重算）。"""
    return {
        "qualification_policy": snap.qualification_policy.to_dict(),
        "dispositions": [d.to_dict() for d in snap.dispositions],
        "spans": [s.to_dict() for s in snap.spans],
        "inherited_unassigned_span_ids": list(snap.inherited_unassigned_span_ids),
        "components": [c.to_dict() for c in snap.components],
        "coverages": [c.to_dict() for c in snap.coverages],
        "conservation": snap.conservation.to_dict(),
        "synopses": [n.to_dict() for n in snap.synopses],
        "terminal_count": snap.terminal_count,
    }


def _frozen_index_paths() -> list:
    index = _frozen_json("artifact_index.json")
    return [entry["path"] for entry in index["files"]]


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
    # 只读前置：`bind_current_evidence_authority` 走的是 service 里预检过的同一个绑定
    # 点（`evidence.store._db_path`）。测试等价地先绑定，并在结束后恢复原值。
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _db_identity(_DB_PATH)
    try:
        _estore._db_path = _DB_PATH
        ctx = _chain()
        _run_group("T13", _test_t13, ctx)
        _run_group("T14", _test_t14, ctx)
        _run_group("T15", _test_t15, ctx)
        _run_group("T16", _test_t16, ctx)
        _run_group("T17", _test_t17, ctx)
        _run_group("T18", _test_t18, ctx)
        _run_group("T19", _test_t19, ctx)
    finally:
        _estore._db_path = previous_db_path
        after = _db_identity(_DB_PATH)
        check(before == after,
              f"只读前置：`data/evidence.db` 在测试前后必须逐项不变（size/mtime_ns/"
              f"sha256）：{before} vs {after}")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

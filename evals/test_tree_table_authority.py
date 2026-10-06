# -*- coding: utf-8 -*-
"""TS5 §19.12.6-9 / -10 + §19.12.3-7 + §19.12.7-2 / -4：权威边界与反硬编码门。

覆盖（全部离线：只读本地 PDF 与**只读** `data/evidence.db` `data/financial_v2.db`；
无网络、无 LLM、无 Bocha、无写入）：

- **§19.12.6-9** 财务主表（`financial_main_statement`）的 `TableObject` 数字权威**恒**
  `False`：真实现场 14 张表 + 四个 `structure_class` 的逐类合成变体 + snapshot 级
  常量，全部 `is_financial_authority() is False`；并用 AST 证明该方法的函数体
  **就是** `return False`（不可由 class / 证据 / 调用方参数解锁）；
- **§19.12.6-10** 单张表或单条简介**永不**单独证明 `set_complete`：
  `TableObjectV4` / `FinalOutlineSpan` / `FinalNavigationSynopsis` 三类对象**没有**
  该能力（AST 全包扫描证明全包只有 `OutlineSpan` 一处定义，且其签名强制要求
  alignment 记录 + `boundary_verified`，**没有**无参便利版本）；`CAPABILITY_KINDS`
  六类里没有任何"完备"能力；策略开关只作前置门（本卷 `True/True`，正因如此
  屏障必须是**结构性**的）；
- **§19.12.3-7** 公司名 / 比例 / 单位 / 表头 / 合计 / 任何片段：改一处必须**改变身份**
  或被**拒绝**。用真实表逐项构造自洽变体，核对 locator（物理层）与
  content/structure/provenance 三指纹（语义层）的**分层**敏感度；单位缺省表
  用 schema 契约的精确拒绝覆盖；片段改动走 `TableCellSourceRef.create` +
  块/cell/行自底向上重建，并另给零长度与同 LayoutSpan 重叠两条拒绝反例；
- **§19.12.7-2** 无第二套运行时：六份 TS5 生产模块的 AST 导入表只有 stdlib 允许集
  与 `document_structure.*` 白名单，且**没有任何** Router / Harness / Retriever /
  ToolRegistry / LLM 客户端 / 网络 / Bocha / 第二 DB 客户端；再以"导入这六个模块
  实际新增的 `sys.modules`"作运行时复核；
- **§19.12.7-4** 无案例字面形状：六份模块 + 三份 `policies/table_*.json` 中不得出现
  公司名 / 证券代码 / 固定页码 / 表号 / Evidence ID / 答案关键词。生产分支里的
  命中一律失败；唯一允许的是 `table_classification._FORBIDDEN_PROFILE_TOKEN_PATTERNS`
  这一**拒绝名单**本身与 `self_check` 里的合成反例（且它们必须不在任何分支测试中）。
  机械门本身用真实校验路径验证：案例形状 token 走 `HeadingPattern.from_dict`
  与 `_check_token_is_generic` 必须被拒，而**全部已发布 profile token 必须通过**。

每个 `check` 都设计成"对应生产逻辑被移除即失败"：权威断言用 AST 函数体核对而不是
调用一次；身份敏感度用 `sha256_canonical` / `identity` / `locator` **独立重算**
载荷与指纹；反例一律断言具体错误类型 + 具体错误文本。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import re
import sys
import time
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

#: `document_structure` 全栈被导入**之前**的模块表（`document_structure/__init__.py`
#: 会即时装载全部 TS5 生产模块，因此必须在**第一次**触碰该包之前冻结）。
_PRE_TS5_MODULES = frozenset(sys.modules)

from evidence import store as _estore  # noqa: E402

from evals import tree_stage_env as STAGE  # noqa: E402
from document_structure import canonical as C  # noqa: E402
from document_structure.canonical import SchemaValidationError  # noqa: E402
from document_structure import table_schema as TS  # noqa: E402
from document_structure import table_builder as TB  # noqa: E402
from document_structure import table_classification as TC  # noqa: E402
from document_structure import table_geometry as TG  # noqa: E402
from document_structure import final_material_builder as FMB  # noqa: E402
from document_structure import final_verifier as FV  # noqa: E402
from document_structure import versions as V  # noqa: E402

#: 装载六份 TS5 生产模块（含 `document_structure` 包的其余层）**实际新增**的模块表。
#: 冻结点在 `sections.service` 之前：后者会拖进 LLM SDK 等上位组件，不属于 TS5 闭包。
_TS5_IMPORT_DELTA = frozenset(sys.modules) - _PRE_TS5_MODULES

# 以下只为测试自身的真实现场需要（同属 document_structure，但不是 TS5 生产模块）。
from document_structure import aligner as AL  # noqa: E402
from document_structure import layout_builder as LB  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_schema as SS  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure.evidence_gateway import (  # noqa: E402
    bind_current_evidence_authority,
)
from document_structure.schema import OutlineSpan  # noqa: E402

_TS5_MODULES = ("table_schema", "table_geometry", "table_classification",
                "table_builder", "final_material_builder", "final_verifier")
_TS5_MODULE_OBJS = {"table_schema": TS, "table_geometry": TG,
                    "table_classification": TC, "table_builder": TB,
                    "final_material_builder": FMB, "final_verifier": FV}
_TS5_FULLNAMES = frozenset(f"document_structure.{n}" for n in _TS5_MODULES)

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


def check_aggregate(bad, total, msg, sample: int = 3):
    """聚合式断言：逐项收集反例，异常集合非空（或总数为 0）即失败。"""
    bad = list(bad)
    return check(not bad and total > 0,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


def check_aggregate_allow_empty(bad, total, msg, sample: int = 3):
    bad = list(bad)
    return check(not bad,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


# ---------------------------------------------------------------------------
# 0. 只读真实现场（唯一正式输入：VerifiedSpanSnapshot）
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"
_TRUST = _REPO / "evals" / "fixtures" / "tree_structure" / "ts4_trust_roots.json"
_TRUST_KEY = "NDSD_2024_year"
_DS_DIR = _REPO / "document_structure"
_POLICY_DIR = _DS_DIR / "policies"

_STATE: dict = {}
_AST_CACHE: dict = {}


def _db_identity(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _chain() -> dict:
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
    _STATE.update({"snap": snap, "verified": verified, "final": final,
                   "handoff": handoff, "ident": ident,
                   "policy": verified.handoff.qualification_policy})
    return _STATE


def _module_source(name: str) -> str:
    return (_DS_DIR / f"{name}.py").read_text(encoding="utf-8")


def _module_ast(name: str) -> ast.Module:
    if name not in _AST_CACHE:
        _AST_CACHE[name] = ast.parse(_module_source(name))
    return _AST_CACHE[name]


# ---------------------------------------------------------------------------
# 1. 生产分支静态扫描的公共件（AST + 字面量）
# ---------------------------------------------------------------------------

#: 案例专属字面量：公司名 / 证券代码 / 文档身份 / 夹具名。任何生产分支出现即失败。
_CASE_TOKENS = (
    "300750", "300,750", "CATL", "Contemporary Amperex",
    "宁德时代", "NDSD", "KCZ", "FIXTURE_BOND",
)

#: 答案关键词（财务主表/附注**行项目**名）。分类只允许**章节标题**级 token，
#: 行项目名一旦进入生产分支就是按答案挖数。
_CASE_ANSWER_KEYWORDS = (
    "营业收入", "净利润", "毛利率", "营业成本", "研发费用", "货币资金",
    "应收账款", "存货", "固定资产", "资产总计", "负债合计", "所有者权益",
    "前五名", "市占率", "产能利用率",
)

#: 固定页码 / 表号形状。
_TABLE_NUMBER_PATTERNS = (
    r"表\s*\d", r"[Tt]able\s*\d", r"第\s*\d+\s*页", r"[Pp]age\s*\d+",
)

#: 具体实例身份形状（`<kind>-<hex16>` / `loc-<kind>-<hex16>`）：只允许出现
#: **种类前缀**，不允许出现任何一次真实构建的具体身份。
_INSTANCE_ID_PATTERN = r"^(?:loc-)?[a-z]{2,5}-[0-9a-f]{16}$"

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "harness", "router", "retriever", "tool_registry", "toolregistry",
    "openai", "anthropic", "llm", "bocha", "requests", "httpx", "urllib",
    "socket", "aiohttp", "subprocess", "sqlite3", "sections", "prompt",
)

#: 运行时增量子串禁忌集：与静态 AST 检查**同源**，但去掉三组**既有**闭包——
#: 探针证明 `harness`*（`outline_builder` 复用 `harness.heading_structure` 的编号
#: 层级）、`sqlite3`*（`evidence.store`）与 `urllib.parse`（`evidence.store`）
#: 是**装载 `document_structure` / `evidence` 自身**带进来的，不是 TS5 新增的
#: 第二运行时。静态检查仍用全量集：任何 TS5 模块**直接**导入它们都会失败。
_RUNTIME_FORBIDDEN_SUBSTRINGS = tuple(
    s for s in _FORBIDDEN_IMPORT_SUBSTRINGS
    if s not in ("harness", "sqlite3", "urllib"))

#: 运行时增量里允许出现的"既有闭包"标量模块名。
_NON_TS5_LEGACY_SCALARS = frozenset((
    "harness", "evidence", "sqlite3", "sqlite3.dbapi2", "_sqlite3",
    "urllib", "urllib.parse",
))


def _is_legacy_closure(modname: str) -> bool:
    """`document_structure` / `evidence` 既有闭包之外，一律不算"登记在案"。"""
    return (modname in _NON_TS5_LEGACY_SCALARS
            or modname.startswith("harness.")
            or modname.startswith("evidence.")
            or modname == "document_structure.evidence_gateway")

_ALLOWED_STDLIB = frozenset((
    "__future__", "ast", "dataclasses", "hashlib", "io", "json", "math", "os",
    "re", "sys", "typing",
))

#: 允许的第三方根包**只有**几何/PDF 提取库（与 TS4 PageLayout 同一实现）：
#: 任何**新增**的第三方导入都会失败，除非在此显式登记。
_ALLOWED_THIRD_PARTY = frozenset(("pdfplumber", "pymupdf", "fitz",
                                  "pdfminer", "numpy", "PIL"))

_ALLOWED_INTERNAL = frozenset((
    "versions", "canonical", "schema", "span_schema", "span_policy", "aligner",
    "normalization", "synopsis", "table_schema", "table_geometry",
    "table_classification", "table_builder", "final_material_builder",
    "final_verifier",
))

_FORBIDDEN_CALL_NAMES = frozenset((
    "urlopen", "http_get", "http_post", "chat_completion", "call_llm",
    "invoke_llm", "run_harness", "run_agent", "retrieve", "retriever_search",
    "tool_call", "call_tool", "bocha_search", "web_search", "search_web",
    "dispatch_tool",
))


def _string_constants(tree: ast.Module) -> list:
    """全部字符串字面量节点，附带"所在函数 / 赋值目标 / 是否在分支测试内"。"""
    test_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            for sub in ast.walk(node.test):
                test_nodes.add(id(sub))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append({"node": node, "value": node.value,
                        "branch": id(node) in test_nodes})
    return out


def _assign_regions(tree: ast.Module) -> dict:
    """`赋值目标 -> (起行, 止行)`，用于识别"声明式门名单"区域。"""
    out = {}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for t in targets:
            out[t.id] = (node.lineno, getattr(node, "end_lineno", node.lineno))
    return out


def _enclosing_functions(tree: ast.Module) -> dict:
    """`节点 id -> 最近的函数名`（用于判定命中是否落在 self_check 反例区）。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                out.setdefault(id(sub), node.name)
    out[id(tree)] = None
    return out


def _identifier_nodes(tree: ast.Module) -> list:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.append(("Name", node.id, node.lineno))
        elif isinstance(node, ast.Attribute):
            out.append(("Attribute", node.attr, node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(("FunctionDef", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append(("ClassDef", node.name, node.lineno))
        elif isinstance(node, ast.arg):
            out.append(("arg", node.arg, node.lineno))
    return out


def _page_literal_compares(tree: ast.Module) -> list:
    """把"页码类名字 与 数字字面量"直接比较的节点找出来（固定页码形状）。"""

    def name_of(o):
        if isinstance(o, ast.Name):
            return o.id
        if isinstance(o, ast.Attribute):
            return o.attr
        if isinstance(o, ast.Subscript) and isinstance(o.slice, ast.Constant) \
                and isinstance(o.slice.value, str):
            return o.slice.value
        return None

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        pageish = any((name_of(o) or "").lower().find("page") >= 0
                      for o in operands)
        literal = any(isinstance(o, ast.Constant)
                      and isinstance(o.value, (int, float))
                      and not isinstance(o.value, bool) for o in operands)
        if pageish and literal:
            bad.append((node.lineno, [name_of(o) for o in operands]))
    return bad


# ---------------------------------------------------------------------------
# 2. 身份敏感度用的重建件（自洽变体：全部派生字段由生产 create 重算）
# ---------------------------------------------------------------------------

_DERIVED_TO4 = ("table_locator", "table_id", "content_fingerprint",
                "structure_fingerprint", "provenance_fingerprint")


def _non_derived(table) -> dict:
    return {f.name: getattr(table, f.name) for f in fields(table)
            if f.name not in _DERIVED_TO4}


def _rebuild(base, **over) -> "TS.TableObjectV4":
    kw = _non_derived(base)
    kw.update(over)
    return TS.TableObjectV4.create(**kw)


def _rebuild_block(block, text=None, source_ref_ids=None):
    return TS.TableCellBlock.create(
        block_index=block.block_index, role=block.role,
        text=block.text if text is None else text,
        source_ref_ids=(block.source_ref_ids if source_ref_ids is None
                        else tuple(source_ref_ids)))


def _rebuild_cell(cell, blocks=None, source_refs=None):
    # `unproven_fragments` **必须一起搬运**（`tc-3`）：现场存在"只有缺口、没有来源片段"的
    # 合法缺值格（正文为空、缺口逐条在册，§19.12.2A(7)）。搬运时漏掉它，重建出来的对象
    # 就退化成"空来源 + 空缺口"，被 schema 判成"自报文本"——那是**夹具失真**，不是现场
    # 缺陷。此处不复制、不放大任何缺口，只把原格自带的缺口账原样带过。
    return TS.TableCellV4.create(
        row=cell.row, column=cell.column, rowspan=cell.rowspan,
        colspan=cell.colspan, bbox=cell.bbox,
        blocks=tuple(cell.blocks) if blocks is None else tuple(blocks),
        source_refs=(tuple(cell.source_refs) if source_refs is None
                     else tuple(source_refs)),
        unproven_fragments=tuple(cell.unproven_fragments),
        schema_version=cell.schema_version)


def _rebuild_row(row, role=None, cells=None):
    return TS.TableRowV4(row_index=row.row_index,
                         role=row.role if role is None else role,
                         label=row.label,
                         is_repeated_header=row.is_repeated_header,
                         cells=tuple(row.cells) if cells is None else tuple(cells))


def _rich_table(tables):
    """挑一张结构最丰富的真实表（标题/注释块最多，其次来源片段最多）。"""
    return max(tables, key=lambda t: (len(t.title_blocks) + len(t.note_blocks)
                                      + len(t.unit_blocks), len(t.source_refs)))


def _first_body_cell_row(table):
    return next(r for r in table.rows if r.role == "body" and r.cells)


def _replace_ref(table, old_ref, new_ref) -> dict:
    """把一条来源片段在**全部消费者**（cell 块 / 表级块 / 表级并集）里整体换掉。"""

    def fix_blocks(blocks):
        out = []
        for b in blocks:
            ids = tuple(new_ref.source_ref_id if r == old_ref.source_ref_id else r
                        for r in b.source_ref_ids)
            out.append(_rebuild_block(b, source_ref_ids=ids))
        return out

    def fix_cell(cell):
        refs = tuple(new_ref if x.source_ref_id == old_ref.source_ref_id else x
                     for x in cell.source_refs)
        return _rebuild_cell(cell, blocks=fix_blocks(cell.blocks),
                             source_refs=refs)

    return {
        "rows": tuple(_rebuild_row(r, cells=[fix_cell(c) for c in r.cells])
                      for r in table.rows),
        "source_refs": tuple(
            new_ref if x.source_ref_id == old_ref.source_ref_id else x
            for x in table.source_refs),
        "title_blocks": tuple(fix_blocks(table.title_blocks)),
        "unit_blocks": tuple(fix_blocks(table.unit_blocks)),
        "note_blocks": tuple(fix_blocks(table.note_blocks)),
    }


def _ref_payload(ref) -> dict:
    return {
        "interval": ref.interval.to_dict(),
        "component_id": ref.component_id,
        "component_locator": ref.component_locator,
        "evidence_block_id": ref.evidence_block_id,
        "evidence_char_range": list(ref.evidence_char_range),
        "terminal_kind": ref.terminal_kind,
        "terminal_id": ref.terminal_id,
        "terminal_locator": ref.terminal_locator,
        "terminal_schema_version": ref.terminal_schema_version,
        "alignment_id": ref.alignment_id,
        "verdict": ref.verdict,
        "refusal_reason": ref.refusal_reason,
        "citable": ref.citable,
        "citable_reason": ref.citable_reason,
    }


def _clone_ref(ref, char_range=None):
    return TS.TableCellSourceRef.create(
        interval=ref.interval, component_id=ref.component_id,
        component_locator=ref.component_locator,
        evidence_block_id=ref.evidence_block_id,
        evidence_char_range=(ref.evidence_char_range if char_range is None
                             else char_range),
        terminal_kind=ref.terminal_kind, terminal_id=ref.terminal_id,
        terminal_locator=ref.terminal_locator,
        terminal_schema_version=ref.terminal_schema_version,
        verdict=ref.verdict, refusal_reason=ref.refusal_reason)


def _identity_mismatch(fn) -> str:
    """跑一次构造，返回"派生身份不一致"的具体错误文本（或空串）。"""
    try:
        fn()
    except SchemaValidationError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"<{type(e).__name__}: {e}>"
    return ""


# ---------------------------------------------------------------------------
# G1 §19.12.6-9：财务主表的 TableObject 数字权威恒 False
# ---------------------------------------------------------------------------

def _authority_definitions() -> list:
    """六份 TS5 模块里 `is_financial_authority` 的定义点与函数体形状。"""
    found = []
    for name in _TS5_MODULES:
        for node in ast.walk(_module_ast(name)):
            if isinstance(node, ast.FunctionDef) and \
                    node.name == "is_financial_authority":
                body = [n for n in node.body
                        if not (isinstance(n, ast.Expr)
                                and isinstance(n.value, ast.Constant)
                                and isinstance(n.value.value, str))]
                constant_false = (
                    len(body) == 1 and isinstance(body[0], ast.Return)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value is False)
                found.append({"module": name, "line": node.lineno,
                              "constant_false": constant_false,
                              "argc": len(node.args.args)})
    return found


def _g1_authority(ctx) -> None:
    tables = ctx["final"].tables
    defs = _authority_definitions()
    check_aggregate([d for d in defs if not d["constant_false"]], len(defs),
                    "全部 `is_financial_authority` 定义的函数体**就是** `return "
                    "False`（任何条件分支都是越权）")
    check(len(defs) == 2 and {d["module"] for d in defs} == {"table_schema"},
          f"TS5 只在 table_schema 里定义两处权威常量（TableObjectV4 + final "
          f"snapshot），得到 {[(d['module'], d['line']) for d in defs]}")
    check_aggregate([d for d in defs if d["argc"] != 1], len(defs),
                    "权威方法只接受 self：不存在“调用方可传 flag 解锁”的签名")

    bad = [t.table_id for t in tables if t.is_financial_authority() is not False]
    check_aggregate(bad, len(tables),
                    "真实现场每张表的 `is_financial_authority()` 恒为 False")
    check(ctx["final"].is_financial_authority() is False,
          "final snapshot 级 `is_financial_authority()` 同样恒为 False")
    check(TS.self_check()["table_object_is_financial_authority"] is False,
          "`table_schema.self_check()` 自报 `table_object_is_financial_authority"
          "` 为 False")

    rich = _rich_table(tables)
    by_class = {}
    for cls in TS.TABLES_STRUCTURE_CLASSES:
        try:
            by_class[cls] = _rebuild(rich, structure_class=cls)
        except SchemaValidationError as e:
            by_class[cls] = e
    bad_cls = [f"{c}:{v}" for c, v in by_class.items()
               if isinstance(v, Exception) or v.is_financial_authority() is not False]
    check_aggregate(bad_cls, len(by_class),
                    "四个 structure_class（含 financial_main_statement）的逐类变体"
                    "权威仍恒为 False")
    fms = by_class.get("financial_main_statement")
    check(fms is not None and not isinstance(fms, Exception)
          and fms.structure_class == "financial_main_statement"
          and fms.is_financial_authority() is False,
          "财务主表变体：structure_class 确实改写成功，且权威仍为 False"
          "（分类不是权威授予）")

    keys = okeys = sorted(rich.to_dict().keys())
    check(not [k for k in keys if "authority" in k or "fact" in k],
          f"TableObjectV4.to_dict() 载荷里没有任何 authority/fact 字段（实得 "
          f"{[k for k in keys if 'authority' in k or 'fact' in k]}）")
    fin_names = [n for n in dir(TS.TableObjectV4) if "financial" in n.lower()]
    check(fin_names == ["is_financial_authority"],
          f"TableObjectV4 上与财务相关的公开名字只有 is_financial_authority，"
          f"得到 {fin_names}")
    raises(lambda: rich.is_financial_authority(True), TypeError, "",
           "权威方法不接受任何“解锁”参数（传参即 TypeError）")
    _results["details"].append(
        f"__authority__ tables={len(tables)} evidence_backed="
        f"{sum(1 for t in tables if t.is_evidence_backed())} "
        f"defs={[(d['module'], d['line']) for d in defs]} "
        f"classes={sorted(by_class)}")


def _g1_authority_negatives(ctx) -> None:
    tables = ctx["final"].tables
    rich = _rich_table(tables)
    fms = _rebuild(rich, structure_class="financial_main_statement")
    check(fms.table_id != rich.table_id,
          "财务主表分类确实改变了 table_id（分类进入结构指纹 → 身份）")
    check(fms.content_fingerprint == rich.content_fingerprint
          and fms.provenance_fingerprint == rich.provenance_fingerprint,
          "改分类不动内容/来源指纹（分类只是标签层，不重写数字与来源）")
    check(fms.is_evidence_backed() == rich.is_evidence_backed()
          and fms.is_financial_authority() is False,
          "分类为财务主表既不解锁权威，也不改变 Evidence-backed 判定")
    check(fms.structure_state == rich.structure_state
          and fms.structure_state_reason == rich.structure_state_reason
          and fms.missing_or_uncertain_fields == rich.missing_or_uncertain_fields,
          "分类为财务主表不改变结构状态机（state/reason/缺字段）")

    raises(lambda: setattr(rich, "table_id", "to4-" + "0" * 16),
           FrozenInstanceError, "",
           "TableObjectV4 冻结：字段不可被就地改写对抗身份（FrozenInstanceError）")

    bad = []
    for name in _TS5_MODULES:
        for kind, ident, line in _identifier_nodes(_module_ast(name)):
            if ident in ("FinancialFactPack", "FinancialSnapshot",
                         "financial_fact_pack", "FinancialAuthority"):
                bad.append(f"{name}:{kind}:{ident}@{line}")
    check(not bad,
          f"TS5 生产代码不引用任何财务权威类型（{bad}）——文档字符串里的“不是 "
          f"FinancialSnapshot”式说明不计入")

    bad_any = []
    for t in tables:
        for name in ("to_financial_fact_pack", "as_financial_snapshot",
                     "authorize", "grant_authority"):
            if hasattr(t, name):
                bad_any.append(f"{t.table_id}:{name}")
    check(not bad_any,
          f"没有任何 TableObject 暴露“自授权威”的出口（{bad_any}）")


# ---------------------------------------------------------------------------
# G2 §19.12.6-10：单表 / 单条简介永不单独证明 set_complete
# ---------------------------------------------------------------------------

def _g2_no_alone_completion(ctx) -> None:
    final = ctx["final"]
    tables = final.tables
    spans = final.final_spans
    synopses = final.synopses
    policy = ctx["policy"]

    bad = [t.table_id for t in tables if hasattr(t, "can_support_set_complete")]
    check_aggregate(bad, len(tables),
                    "逐表核对：没有任何 TableObject 具备 set_complete 能力")
    bad_spans = [s.span_id for s in spans
                 if hasattr(s, "can_support_set_complete")]
    check_aggregate(bad_spans, len(spans),
                    "逐 final span 核对：没有任何 FinalOutlineSpan 具备 "
                    "set_complete 能力")
    bad_syn = [getattr(s, "synopsis_id", "?") for s in synopses
               if hasattr(s, "can_support_set_complete")]
    check_aggregate(bad_syn, len(synopses),
                    "逐简介核对：没有任何 FinalNavigationSynopsis 具备 "
                    "set_complete 能力")
    check(not hasattr(TS.FinalMaterialStructureSnapshot,
                      "can_support_set_complete"),
          "final snapshot 终端节点也不具备该能力（终端只汇总，不放行）")

    # AST：全包只有一处定义，且必须**不是** TS5 的表格/简介层。
    defs = []
    for path in sorted(_DS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for node in cls.body:
                if isinstance(node, ast.FunctionDef) and \
                        node.name == "can_support_set_complete":
                    defs.append((path.name, cls.name, node.lineno))
    check(defs == [("schema.py", "OutlineSpan", defs[0][2])] if defs else False,
          f"全包只有 OutlineSpan 一处定义 can_support_set_complete，得到 {defs}")
    sig = inspect.signature(OutlineSpan.can_support_set_complete)
    params = sig.parameters
    check("alignment_records" in params and "boundary_verified" in params
          and params["alignment_records"].default is inspect.Parameter.empty
          and params["boundary_verified"].default is inspect.Parameter.empty,
          f"该能力**必须**由调用方给出 alignment 记录与 boundary_verified（没有"
          f"无参便利版本）：{sig}")
    raises(lambda: OutlineSpan.can_support_set_complete(), TypeError, "",
           "无参调用该能力即 TypeError（刻意不提供便利判定）")

    comp_kinds = [k for k in SS.CAPABILITY_KINDS
                  if "complete" in k.lower() or "completion" in k.lower()]
    check(not comp_kinds,
          f"六类能力里没有任何“完备/放行”能力（实得 {comp_kinds}）")
    check(len(SS.CAPABILITY_KINDS) == 6
          and FMB.CAPABILITY_KIND in SS.CAPABILITY_KINDS,
          "能力表仍然是六类封闭集，final capability 已在其中")
    check(policy.completion_enabled is True and policy.set_complete_supported is True,
          "本卷冻结策略**已开启** completion / set_complete（正因如此屏障必须是"
          "结构性的：单表/单简介拿不到能力，而不是策略关着）")

    # `set_complete_supported` 只能作为**策略前置门**出现，不能成为对象能力。
    names = {}
    for name in _TS5_MODULES:
        for kind, ident, line in _identifier_nodes(_module_ast(name)):
            if "set_complete" in ident:
                names.setdefault(ident, []).append((name, kind, line))
    check(set(names) == {"set_complete_supported"},
          f"TS5 里唯一含 set_complete 的标识符就是策略字段，得到 {sorted(names)}")
    check(all(k == "Attribute" for hits in names.values() for _n, k, _l in hits),
          "该字段只以**属性访问**出现（不是函数/参数/类名 → 没有“单表放行”接口）")
    raisers = []
    for node in ast.walk(_module_ast("final_material_builder")):
        if isinstance(node, ast.Attribute) and \
                node.attr == "set_complete_supported":
            subject = node.value
            subject_name = getattr(subject, "id", None) or \
                getattr(subject, "attr", "") or ""
            if "policy" not in subject_name:
                raisers.append(node.lineno)
    check(not raisers,
          f"该字段只从 policy 对象上读取（行 {raisers}）")
    _results["details"].append(
        f"__alone-completion__ defs={defs} sig={sig} "
        f"capability_kinds={len(SS.CAPABILITY_KINDS)} "
        f"names={ {k: len(v) for k, v in names.items()} }")


# ---------------------------------------------------------------------------
# G3 §19.12.3-7：改一处必须改变身份或被拒绝
# ---------------------------------------------------------------------------

def _fingerprint_of(method, payload) -> str:
    return C.sha256_canonical(payload)


def _g3_identity_sensitivity(ctx) -> None:
    tables = ctx["final"].tables
    # 本组要逐个改动"表题"这一身份面，因此**优先**挑一张真的有表题的真实表；样本里若
    # 一张带表题的表都没有，该面在本样本上不可达，此时如实记录而不是伪造一个表题。
    # （表题必须由连续文本游程证明，证不出即记 gap —— 不证明表题是合法终态。）
    titled = [t for t in tables if t.title_blocks]
    base = _rich_table(titled) if titled else _rich_table(tables)
    if not titled:
        _results["details"].append(
            "__g3-title-surface__ 本次样本没有带已证明表题的真实表（§二：表题必须由"
            "连续文本游程证明，证不出即记 gap），`title_blocks[0].text` 改动面"
            "在本样本上不可达")

    # (a) 独立重算：四个载荷 → 四个已声明身份，逐字节相等。
    check(base.content_fingerprint == C.sha256_canonical(base.content_payload()),
          "content_fingerprint 由 content_payload 独立重算相等")
    check(base.structure_fingerprint == C.sha256_canonical(base.structure_payload()),
          "structure_fingerprint 由 structure_payload 独立重算相等")
    check(base.provenance_fingerprint
          == C.sha256_canonical(base.provenance_payload()),
          "provenance_fingerprint 由 provenance_payload 独立重算相等")
    check(base.table_locator == C.locator("to4", base.locator_payload()),
          "table_locator 由 locator_payload 独立重算相等")
    check(base.table_id == C.identity("to4", base.identity_payload()),
          "table_id 由 identity_payload 独立重算相等")

    # (b) 每个"改动面"都必须被某个载荷覆盖；改动后对应指纹必须变化。
    content = base.content_payload()
    structure = base.structure_payload()
    provenance = base.provenance_payload()
    locator_p = base.locator_payload()

    def _altered(payload, path, value):
        out = json.loads(json.dumps(payload, ensure_ascii=False))
        cur = out
        for key in path[:-1]:
            cur = cur[key]
        cur[path[-1]] = value
        return out

    cell0 = base.rows[0].cells[0]
    # 表题面只在**真的有表题**时才进入改动面清单：没有表题不是失败，而是合法终态
    # （§二），此时该面在本样本上不可达，跳过而不是伪造一个不存在的表题。
    title_fragment = (
        ("表头/标题片段（title_blocks[0].text）", content,
         ("title_blocks", 0, "text"), base.title_blocks[0].text + "（改）")
        if base.title_blocks else None)
    fragments = [
        ("比例/数字（body cell 文本）", content,
         ("rows", 0, "cells", 0, "text"), cell0.text + "7"),
        ("单位（unit_text）", content, ("unit_text",), "元"),
        *([title_fragment] if title_fragment else []),
        ("合计（末行 role）", content,
         ("rows", len(base.rows) - 1, "role"), "total"),
        ("公司名（document_id）", locator_p, ("document_id",),
         base.document_id + "_x"),
        ("页码（page_number）", locator_p, ("page_number",),
         base.page_number + 1),
        ("分类（structure_class）", structure, ("structure_class",),
         "financial_main_statement"),
        ("片段（source_refs[0].evidence_char_range）", provenance,
         ("source_refs", 0, "evidence_char_range"),
         [base.source_refs[0].evidence_char_range[0],
          base.source_refs[0].evidence_char_range[1] + 1]),
    ]
    bad_frag = []
    for label, payload, path, value in fragments:
        try:
            altered = _altered(payload, path, value)
        except (KeyError, IndexError, TypeError) as e:
            bad_frag.append(f"{label}:路径不可达 {e}")
            continue
        if altered == payload:
            bad_frag.append(f"{label}:改动未生效")
            continue
        if C.sha256_canonical(altered) == C.sha256_canonical(payload):
            bad_frag.append(f"{label}:指纹不变")
    check_aggregate(bad_frag, len(fragments),
                    "每一处改动面（公司名/比例/单位/表头/合计/片段/页码/分类）"
                    "都被对应身份载荷覆盖：改一处即改指纹")

    # (c) 真实 create 路径：改一处 → 身份必须变（并核对分层）。
    loc, cid, sid, pid = (base.table_locator, base.content_fingerprint,
                          base.structure_fingerprint, base.provenance_fingerprint)

    def _expect_change(label, new, *, locator_same=True, content_same=False,
                       structure_same=False, provenance_same=False):
        problem = []
        if new.table_id == base.table_id:
            problem.append("table_id 未变")
        if locator_same and new.table_locator != loc:
            problem.append("locator 不应变")
        if not locator_same and new.table_locator == loc:
            problem.append("locator 应变")
        if content_same and new.content_fingerprint != cid:
            problem.append("content 不应变")
        if structure_same and new.structure_fingerprint != sid:
            problem.append("structure 不应变")
        if provenance_same and new.provenance_fingerprint != pid:
            problem.append("provenance 不应变")
        check(not problem, f"{label}：改一处 → 身份改变且分层敏感度正确"
                           f"（新 id {new.table_id}）" if not problem
              else f"{label}：{'；'.join(problem)}")

    # 分类（结构层）
    _expect_change("§19.12.3-7 分类",
                   _rebuild(base, structure_class="financial_main_statement"),
                   content_same=True, provenance_same=True)
    # 单位（内容层）：本卷没有带单位的表 → 走 schema 契约的精确拒绝。
    raises(lambda: _rebuild(base, unit_text="元"), SchemaValidationError,
           "unit_text 与 unit_blocks 必须同时存在或同时缺省",
           "§19.12.3-7 单位：缺省表上单方面给 unit_text 被拒（内容层契约）")
    # 表头/标题片段（内容层）
    if base.title_blocks:
        new_tb = (TS.TableCellBlock.create(
            block_index=0, role=base.title_blocks[0].role,
            text=base.title_blocks[0].text + "（改）",
            source_ref_ids=base.title_blocks[0].source_ref_ids),
        ) + tuple(base.title_blocks[1:])
        _expect_change("§19.12.3-7 表头/标题片段",
                       _rebuild(base, title_blocks=new_tb),
                       structure_same=True, provenance_same=True)
    # 比例/数字（内容层）
    row = _first_body_cell_row(base)
    cells = list(row.cells)
    cells[0] = _rebuild_cell(cells[0], blocks=[
        _rebuild_block(cells[0].blocks[0], text=cells[0].blocks[0].text + "9")
    ] + list(cells[0].blocks[1:]))
    rows = list(base.rows)
    rows[base.rows.index(row)] = _rebuild_row(row, cells=cells)
    _expect_change("§19.12.3-7 比例/数字",
                   _rebuild(base, rows=tuple(rows)),
                   structure_same=True, provenance_same=True)
    # 合计（内容层）：末行改 role=total（真实表本卷没有 total 行）
    last = base.rows[-1]
    cells_last = list(last.cells)
    cells_last[0] = _rebuild_cell(cells_last[0], blocks=[
        _rebuild_block(cells_last[0].blocks[0],
                       text=cells_last[0].blocks[0].text + "（合计）")
    ] + list(cells_last[0].blocks[1:]))
    rows_total = list(base.rows)
    rows_total[-1] = _rebuild_row(last, role="total", cells=cells_last)
    _expect_change("§19.12.3-7 合计",
                   _rebuild(base, rows=tuple(rows_total)),
                   structure_same=True, provenance_same=True)
    # 任何片段（来源层）
    cell0 = base.all_cells[0]
    ref0 = cell0.source_refs[0]
    lo, hi = ref0.evidence_char_range
    widened = _clone_ref(ref0, char_range=(lo, hi + 1))
    over = _replace_ref(base, ref0, widened)
    _expect_change("§19.12.3-7 任何片段（来源区间）",
                   _rebuild(base, **over),
                   structure_same=True)
    check(_rebuild(base, **over).content_fingerprint != cid,
          "cell 载荷内嵌其来源片段 → 片段改动能同时进入内容指纹（内容/来源两层"
          "都覆盖片段，不是任一层漏掉）")
    # 公司名（物理层）
    _expect_change("§19.12.3-7 公司名（document_id）",
                   _rebuild(base, document_id=base.document_id + "_x"),
                   locator_same=False, content_same=True, structure_same=True,
                   provenance_same=True)
    # 页码（物理层，且必须仍在 owner boundary 内）
    owner = base.owner
    inside = [p for p in (base.page_number + 1, base.page_number - 1,
                          owner.source_boundary_start_page,
                          owner.source_boundary_end_page)
              if p != base.page_number
              and owner.source_boundary_start_page <= p
              <= owner.source_boundary_end_page]
    if inside:
        _expect_change("§19.12.3-7 页码（物理层）",
                       _rebuild(base, page_number=inside[0]),
                       locator_same=False, content_same=True,
                       structure_same=True, provenance_same=True)
    else:
        # 真实链里这张表的 owner boundary 只含本页，因此"第二个合法页码"在**它**身上
        # 不存在。这里不退化成 SKIP，而是换**另一张真实表**（其 owner boundary 跨页）
        # 验证"页号进入身份"；base 侧的越界拒绝由紧随其后的那条断言覆盖（不重复）。
        alt = next((t for t in tables
                    if t.table_id != base.table_id
                    and t.owner.source_boundary_end_page
                    > t.owner.source_boundary_start_page), None)
        if alt is None:
            _results["skipped"] += 1
            _results["details"].append(
                "SKIP §19.12.3-7 页码：本卷没有 owner boundary 跨页的真实表，"
                "也没有第二个合法页码可用")
        else:
            other = next(p for p in range(alt.owner.source_boundary_start_page,
                                          alt.owner.source_boundary_end_page + 1)
                         if p != alt.page_number)
            try:
                moved = _rebuild(alt, page_number=other)
                _probe, _why = (
                    (moved.table_id != alt.table_id
                     and moved.table_locator != alt.table_locator
                     and moved.content_fingerprint == alt.content_fingerprint
                     and moved.provenance_fingerprint == alt.provenance_fingerprint),
                    "")
            except Exception as _e:  # noqa: BLE001
                _probe, _why = False, f"{type(_e).__name__}: {_e}"
            check(_probe,
                  f"§19.12.3-7 页码（物理层）：跨页 boundary 的真实表 {alt.table_id} "
                  f"改页号 {alt.page_number}→{other} 后 id/locator 变、内容与来源"
                  f"指纹不变（页号只进物理层身份）—— {_why}")
    raises(lambda: _rebuild(base, page_number=owner.source_boundary_end_page + 50),
           SchemaValidationError, "owner 的 source boundary 必须包含该表的物理页",
           "§19.12.3-7 页码：移到 owner boundary 之外被拒（归属不可回查）")
    # 上游依赖束 / 版本轴（身份层，只有 id 变）
    for field_name, value in (("upstream_dependency_fingerprint", "f" * 64),
                              ("verified_span_snapshot_id", "vss-1"),
                              ("outline_id", "do-1")):
        _expect_change(f"§19.12.3-7 {field_name}",
                       _rebuild(base, **{field_name: value}),
                       content_same=True, structure_same=True,
                       provenance_same=True)

    # (d) from_dict 路径：改一处但保留旧派生字段 → 必须被拒（身份不一致或
    #     更早的 schema 契约），绝不允许静默接受漂移。
    raw0 = base.to_dict()
    cases = ((("unit_text",), "元", "unit_text 与 unit_blocks"),
             (("structure_class",), "financial_main_statement", "不一致"),
             (("document_id",), base.document_id + "_x", "不一致"),
             (("page_number",), base.page_number + 1,
              "owner 的 source boundary"),
             (("verified_span_snapshot_id",), "vss-1", "不一致"),
             (("upstream_dependency_fingerprint",), "f" * 64, "不一致"))
    rejects = []
    for path, value, expect in cases:
        raw = json.loads(json.dumps(raw0, ensure_ascii=False))
        raw[path[0]] = value
        text = _identity_mismatch(lambda r=raw: TS.TableObjectV4.from_dict(r))
        if expect not in text:
            rejects.append(f"{path[0]}:{text[:60] or '<未拒绝>'}")
    check_aggregate(rejects, len(cases),
                    "改一处但沿用旧派生字段走 from_dict 一律被拒：身份层改动报"
                    "“不一致”，内容层改动报 schema 契约（不得静默接受漂移）")

    # (e) 片段级拒绝反例（走真实 create）。
    raises(lambda: _clone_ref(ref0, char_range=(lo, lo)),
           SchemaValidationError, "必须满足 0 <= start < end",
           "§19.12.3-7 片段：零长度区间被拒")
    raises(lambda: _clone_ref(ref0, char_range=(hi, lo)),
           SchemaValidationError, "必须满足 0 <= start < end",
           "§19.12.3-7 片段：倒序区间被拒")
    siblings = _clone_ref(ref0, char_range=(lo + 1, hi + 5))
    check(siblings.source_ref_id != ref0.source_ref_id,
          "片段区间改变即改变 source_ref_id（逐段内容寻址）")
    one_block = [b for b in cell0.blocks if ref0.source_ref_id in b.source_ref_ids]
    overlap = None
    if one_block:
        ids = tuple(list(one_block[0].source_ref_ids) + [siblings.source_ref_id])
        overlap = _identity_mismatch(lambda: TS.TableCellV4.create(
            row=cell0.row, column=cell0.column, rowspan=cell0.rowspan,
            colspan=cell0.colspan, bbox=cell0.bbox,
            blocks=[_rebuild_block(one_block[0], source_ref_ids=ids)],
            source_refs=[ref0, siblings]))
    check(overlap is not None and "重叠" in overlap,
          f"同一 LayoutSpan 上的片段重叠被拒（{overlap}）")

    groups = {}
    for t in tables:
        for r in t.source_refs:
            key = (id(t), r.interval.page_number, r.interval.line_index,
                   r.interval.span_index)
            groups[key] = groups.get(key, 0) + 1
    check_aggregate_allow_empty(
        [k for k, v in groups.items() if v > 1], len(groups),
        "真实现场每条来源片段都是其 (页,行,片段) 组的唯一成员（逐段互斥的自然结果）")
    _results["details"].append(
        f"__identity__ base={base.table_id} title={len(base.title_blocks)} "
        f"note={len(base.note_blocks)} refs={len(base.source_refs)} "
        f"groups={len(groups)} overlap_reject={overlap!r}")


# ---------------------------------------------------------------------------
# G4 §19.12.7-4：机械反硬编码门（profile 只允许公司无关字面量）
# ---------------------------------------------------------------------------

def _g4_profile_genericity(ctx) -> None:
    guard = list(TC._FORBIDDEN_PROFILE_TOKEN_PATTERNS)
    check(len(guard) >= 4
          and any("300750" in pattern for pattern, _why in guard),
          f"分类器登记了机械反硬编码名单（{len(guard)} 条，含案例形态）："
          f"{[why for _p, why in guard]}")

    rejected = []
    for bad_token in ("300750", "300750000", "123456", "2024", "000001",
                      "CATL", "catl", "evb-abc", "to4-abc", "sc-1",
                      "gold-x", "node-y", "loc-z", "evidence-1"):
        text = ""
        try:
            TC._check_token_is_generic("T", "tokens[0]", bad_token)
        except SchemaValidationError as e:
            text = str(e)
        if "命中禁止字面形状" not in text:
            rejected.append(f"{bad_token}:{text[:50] or '<未拒绝>'}")
    check_aggregate(rejected, 14,
                    "证券代码 / 六位数字 / 纯数字 / 内部身份前缀 / 案例形态 token "
                    "一律被机械门拒绝")

    accepted = []
    for good in ("资产负债表", "利润表", "现金流量表", "所有者权益变动表",
                 "财务报表附注", "货币计量单位", "（1）", "1、", "-"):
        try:
            TC._check_token_is_generic("T", "tokens[0]", good)
        except SchemaValidationError as e:
            accepted.append(f"{good}:{e}")
    check_aggregate(accepted, 9,
                    "公司无关的章节标题级 token 正常通过机械门（门不是一律拒绝）")

    # 走真实 schema 校验路径（from_dict），不是只调辅助函数。
    pattern = {"schema_type": "HeadingPattern", "order": 1,
               "pattern_key": "poisoned_tokens", "kind": "contains_any",
               "structure_class": "financial_main_statement",
               "tokens": ["300750"], "all_tokens": [], "any_tokens": []}
    raises(lambda: TC.HeadingPattern.from_dict(pattern), SchemaValidationError,
           "命中禁止字面形状",
           "§19.12.7-4 contains_any 里塞证券代码 → 真实 from_dict 路径拒绝")
    pattern2 = dict(pattern, pattern_key="poisoned_any",
                    kind="contains_all_with_any", tokens=[],
                    all_tokens=["资产负债表"], any_tokens=["CATL"])
    raises(lambda: TC.HeadingPattern.from_dict(pattern2), SchemaValidationError,
           "命中禁止字面形状",
           "§19.12.7-4 contains_all_with_any 的 any_tokens 同样被拒")

    bundle = TC.load_table_profile_bundle()
    check(len(bundle.classification.main_statement_patterns) > 0
          and len(bundle.classification.note_subtree_patterns) > 0,
          "已发布分类 profile 正常装载（含主表与附注子树模式）")
    check(len(bundle.settings_fingerprint()) == 64,
          "profile bundle 的设置指纹为 64 位 sha256")
    check(TC.self_check()["problems"] == [],
          f"分类器 self_check 无问题（{TC.self_check()['problems']}）")

    tokens = []

    def _collect(profile):
        for group in ("main_statement_patterns", "note_subtree_patterns",
                      "priority_rows", "subject_strip_rules", "rows"):
            for pat in getattr(profile, group, ()) or ():
                for field_name in ("tokens", "all_tokens", "any_tokens"):
                    for tok in getattr(pat, field_name, ()) or ():
                        tokens.append((group, field_name, tok))
    _collect(bundle.classification)
    bad_tok = []
    for group, field_name, tok in tokens:
        try:
            TC._check_token_is_generic("Profile", f"{group}.{field_name}", tok)
        except SchemaValidationError as e:
            bad_tok.append(f"{tok}:{e}")
    check_aggregate(bad_tok, len(tokens),
                    "已发布的每一个分类 token 都通过机械门（公司无关）")
    ok = []
    for rule in bundle.cell_block.rules:
        for attr in ("requires_all", "requires_any"):
            for sig in getattr(rule, attr, ()) or ():
                ok.append(sig)
    check_aggregate([s for s in ok if s not in TC.BLOCK_SIGNAL_NAMES], len(ok),
                    "cell block 规则引用的信号名全部在封闭信号表内")

    # profile 原始文本不得含任何案例字符串。
    hits = []
    for path in sorted(_POLICY_DIR.glob("table_*.json")):
        text = path.read_text(encoding="utf-8")
        for tok in _CASE_TOKENS:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
        for pat in _TABLE_NUMBER_PATTERNS:
            if re.search(pat, text):
                hits.append(f"{path.name}:{pat}")
    check(not hits, f"三份 policies/table_*.json 不含任何案例字符串/表号（{hits}）")
    _results["details"].append(
        f"__profile__ tokens={len(tokens)} signals={len(ok)} "
        f"guard={len(guard)} patterns="
        f"{len(bundle.classification.main_statement_patterns)}"
        f"+{len(bundle.classification.note_subtree_patterns)}")


# ---------------------------------------------------------------------------
# G5 §19.12.7-2：TS5 不引入第二套运行时
# ---------------------------------------------------------------------------

def _g5_no_second_runtime(ctx) -> None:
    bad_imports = []
    for name in _TS5_MODULES:
        tree = _module_ast(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    full = alias.name
                    root = full.split(".")[0]
                    if root not in _ALLOWED_STDLIB and \
                            root not in _ALLOWED_THIRD_PARTY:
                        bad_imports.append(f"{name}:{full}")
                    if any(s in full.lower()
                           for s in _FORBIDDEN_IMPORT_SUBSTRINGS):
                        bad_imports.append(f"{name}:{full}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                parts = mod.split(".")
                if node.level >= 1 and not mod:
                    # `from . import versions as V`：module 为 None，别名即子模块名。
                    for alias in node.names:
                        if alias.name not in _ALLOWED_INTERNAL:
                            bad_imports.append(f"{name}:. import {alias.name}")
                elif node.level >= 1:
                    if mod not in _ALLOWED_INTERNAL:
                        bad_imports.append(f"{name}:.{mod}")
                elif parts[0] in _ALLOWED_STDLIB or \
                        parts[0] in _ALLOWED_THIRD_PARTY:
                    pass
                elif parts[0] == "document_structure":
                    if len(parts) == 1:
                        for alias in node.names:
                            if alias.name not in _ALLOWED_INTERNAL:
                                bad_imports.append(f"{name}:{mod}.{alias.name}")
                    elif len(parts) == 2 and parts[1] in _ALLOWED_INTERNAL:
                        pass
                    else:
                        bad_imports.append(f"{name}:{mod}")
                else:
                    bad_imports.append(f"{name}:{mod}")
                if any(s in mod.lower() for s in _FORBIDDEN_IMPORT_SUBSTRINGS):
                    bad_imports.append(f"{name}:{mod}")
    check_aggregate(bad_imports, len(_TS5_MODULES),
                    "六份 TS5 模块的导入表只有 stdlib 允许集、几何/PDF 提取库与 "
                    "document_structure 白名单（无 Router/Harness/Retriever/"
                    "ToolRegistry/LLM/网络/Bocha/第二 DB）")

    bad_calls = []
    bad_names = []
    for name in _TS5_MODULES:
        tree = _module_ast(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                ident = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if ident in _FORBIDDEN_CALL_NAMES:
                    bad_calls.append(f"{name}:{ident}@{node.lineno}")
        for kind, ident, line in _identifier_nodes(tree):
            low = ident.lower()
            if any(s in low for s in ("harness", "bocha", "router",
                                      "retriever", "openai", "anthropic",
                                      "tool_registry", "llm_client")):
                bad_names.append(f"{name}:{kind}:{ident}@{line}")
    check_aggregate(bad_calls, len(_TS5_MODULES),
                    "六份 TS5 模块里没有任何联网 / LLM / 工具派发 / 检索调用")
    check(not bad_names, f"六份 TS5 模块里没有任何第二运行时的标识符（{bad_names}）")

    # 运行时复核：装载 TS5 所在全栈**实际新增**的模块表里不得出现第二运行时。
    delta = sorted(_TS5_IMPORT_DELTA)
    bad_delta = [m for m in delta
                 if any(s in m.lower()
                        for s in _RUNTIME_FORBIDDEN_SUBSTRINGS)]
    check(not bad_delta,
          f"装载 TS5 全栈实际新增 {len(delta)} 个模块，无一命中禁忌子串 "
          f"（Router/Harness-Runtime/Retriever/ToolRegistry/LLM/网络/Bocha/"
          f"第二 DB）（{bad_delta}）")
    # `harness.heading_structure` / `evidence.*` / `sqlite3` / `urllib.parse`
    # 是既有闭包（登记在案，见模块头 `_RUNTIME_FORBIDDEN_SUBSTRINGS` 注释），
    # 但**只允许**这一组：任何新的 harness/evidence 子模块都会失败。
    legacy = sorted(m for m in delta
                    if any(k in m.lower()
                           for k in ("harness", "sqlite", "evidence", "urllib")))
    unexpected = [m for m in legacy if not _is_legacy_closure(m)]
    check(not unexpected,
          f"新增表里 harness/evidence/sqlite/urllib 相关条目只有登记在案的既有闭包"
          f"（意外项 {unexpected}；登记项 {legacy}）")
    # 反向对照：白名单谓词本身必须**有效**（非空转）。
    check(_is_legacy_closure("harness.topic_schema")
          and _is_legacy_closure("evidence.store")
          and _is_legacy_closure("sqlite3")
          and not _is_legacy_closure("sections.harness_runner")
          and not _is_legacy_closure("document_structure.harness_bridge")
          and not _is_legacy_closure("llm.client"),
          "既有闭包谓词非空转：既有项放行、任何新 harness/evidence 桥接被拦")
    # 覆盖面：六份模块必须都已装载。
    #
    # 这里刻意**不**用"增量表必须含这六份"来判定：`_TS5_IMPORT_DELTA` 是相对
    # `_PRE_TS5_MODULES`（本模块导入前一刻的 `sys.modules`）算的，而 `run_evals`
    # 在**同一进程**里按序跑上百个模块——前序 TS5 模块（如 `test_tree_table_schema`）
    # 早已把 `document_structure` 全栈导进来，于是这六份落进基线、不在增量里。
    # 那是装载顺序，不是覆盖面缺失；独立的单进程运行同样必须通过。
    missing = sorted(_TS5_FULLNAMES - set(sys.modules))
    check(not missing,
          f"运行时复核覆盖面完整：六份 TS5 模块都已装载（缺 {missing}）")
    # 与顺序无关的那一半：**已装载的整条 `document_structure` 闭包**里不得出现第二
    # 运行时。比增量表更强——无论这六份是谁、在什么时候导进来的都覆盖到。
    closure = sorted(m for m in sys.modules if m.startswith("document_structure"))
    bad_closure = [m for m in closure
                   if any(s in m.lower()
                          for s in _RUNTIME_FORBIDDEN_SUBSTRINGS)]
    check(not bad_closure,
          f"已装载的 `document_structure` 闭包共 {len(closure)} 个模块，"
          f"无一命中禁忌子串（Router/Harness-Runtime/Retriever/ToolRegistry/"
          f"LLM/网络/Bocha/第二 DB）（{bad_closure}）")
    check(len(closure) > len(_TS5_MODULES) and not _is_legacy_closure(
              "document_structure.final_verifier"),
          "闭包覆盖面非空转：装载条目远多于六份模块本身，且既有闭包谓词"
          "只放行 harness/evidence/sqlite/urllib 那一组")
    stray = []
    for name, obj in _TS5_MODULE_OBJS.items():
        for ident in dir(obj):
            low = ident.lower()
            if any(s in low for s in ("harness", "bocha", "router",
                                      "retriever", "tool_registry")):
                stray.append(f"{name}.{ident}")
    check(not stray, f"六份模块对象上没有任何第二运行时属性（{stray}）")
    bad_files = [f"{n}:{o.__file__}" for n, o in _TS5_MODULE_OBJS.items()
                 if Path(o.__file__).parent != _DS_DIR]
    check(not bad_files, f"六份模块都位于 document_structure/ 内（{bad_files}）")

    # 反向对照：清单本身必须**有效**——真出现禁忌子串时必须被抓到。
    control = "harness" in "sections.harness_runner"
    check(control, "禁忌子串判定对 `sections.harness_runner` 有效（检查非空转）")
    check("llm" in "llm.client", "禁忌子串判定对 `llm.client` 有效")
    _results["details"].append(
        f"__no-second-runtime__ delta={len(delta)} sample={delta[:8]} "
        f"legacy={legacy} closure={len(closure)} "
        f"ts5_loaded={sorted(_TS5_FULLNAMES - set(missing))}")


# ---------------------------------------------------------------------------
# G6 §19.12.7-4：生产文件里没有案例字面形状
# ---------------------------------------------------------------------------

def _g6_no_case_literals(ctx) -> None:
    hits = []
    for name in _TS5_MODULES:
        tree = _module_ast(name)
        regions = _assign_regions(tree)
        funcs = _enclosing_functions(tree)
        for item in _string_constants(tree):
            value = item["value"]
            line = item["node"].lineno
            for token in _CASE_TOKENS:
                if token in value:
                    hits.append({"module": name, "line": line, "kind": "case",
                                 "token": token, "value": value[:40],
                                 "func": funcs.get(id(item["node"])),
                                 "branch": item["branch"],
                                 "blocklist": _in_regions(
                                     regions, "_FORBIDDEN_PROFILE_TOKEN_PATTERNS",
                                     line)})
            for token in _CASE_ANSWER_KEYWORDS:
                if token in value:
                    hits.append({"module": name, "line": line,
                                 "kind": "answer_keyword", "token": token,
                                 "value": value[:40],
                                 "func": funcs.get(id(item["node"])),
                                 "branch": item["branch"],
                                 "blocklist": False})
    bad_branch = [h for h in hits
                  if h["branch"] and not h["blocklist"]
                  and h["func"] != "self_check"]
    check(not bad_branch,
          f"§19.12.7-4 六份模块的**生产分支**里没有任何案例/答案字面量"
          f"（{bad_branch[:3]}）")
    bad_where = [h for h in hits
                 if not h["blocklist"] and h["func"] != "self_check"]
    check(not bad_where,
          f"§19.12.7-4 案例/答案字面量只允许出现在声明式门名单或 self_check "
          f"合成反例里（越界 {bad_where[:3]}）")
    check(len([h for h in hits if h["kind"] == "case"]) >= 1,
          "门名单里确实登记了案例形态（扫描不是空转）")
    shape_gate = [TB.prose_negative_gate([tok]) for tok in
                  ("货币资金", "营业收入", "净利润", "毛利率", "前五名")]
    check(all(x is None for x in shape_gate),
          f"内容反例门按**形状**而不是关键词判定：财务行项目名本身不被拒"
          f"（{shape_gate}）")
    _results["details"].append(
        f"__case-scan__ hits={len(hits)} "
        f"{[(h['module'], h['line'], h['kind'], h['token'], h['func']) for h in hits]}"[:400])

    shape_hits = []
    for name in _TS5_MODULES:
        tree = _module_ast(name)
        regions = _assign_regions(tree)
        for item in _string_constants(tree):
            value = item["value"]
            line = item["node"].lineno
            if re.match(_INSTANCE_ID_PATTERN, value):
                shape_hits.append(f"{name}:{line}:{value}")
            if _in_regions(regions, "_FORBIDDEN_PROFILE_TOKEN_PATTERNS", line):
                continue
            for pat in _TABLE_NUMBER_PATTERNS:
                if re.search(pat, value):
                    shape_hits.append(f"{name}:{line}:{pat}:{value[:30]}")
    check(not shape_hits,
          f"§19.12.7-4 六份模块里没有具体实例身份 / 固定表号 / 固定页码字面量"
          f"（{shape_hits[:4]}）")

    page_compares = []
    for name in _TS5_MODULES:
        page_compares.extend(f"{name}:{line}:{ops}"
                             for line, ops in _page_literal_compares(_module_ast(name)))
    check(not page_compares,
          f"§19.12.7-4 没有任何“页码类名字 == 数字字面量”的分支（{page_compares}）")

    vocab_hits = []
    for name in _TS5_MODULES:
        tree = _module_ast(name)
        regions = _assign_regions(tree)
        for item in _string_constants(tree):
            value = item["value"]
            line = item["node"].lineno
            if _in_regions(regions, "_FORBIDDEN_PROFILE_TOKEN_PATTERNS", line):
                continue
            if re.search(r"(?<![0-9A-Za-z])[0-9]{6,}(?![0-9A-Za-z])", value):
                vocab_hits.append(f"{name}:{line}:{value[:40]}")
    check(not vocab_hits,
          f"§19.12.7-4 六份模块里没有独立的 6 位以上数字字面量（页码/代码形状）"
          f"（{vocab_hits[:4]}）")
    _results["details"].append(
        f"__literal-scan__ shapes={len(shape_hits)} page_compares="
        f"{len(page_compares)} long_digits={len(vocab_hits)}")


def _in_regions(regions, key, line) -> bool:
    span = regions.get(key)
    return bool(span) and span[0] <= line <= span[1]


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------

_GROUPS = (
    ("G1-authority-constant", _g1_authority),
    ("G1-authority-negatives", _g1_authority_negatives),
    ("G2-no-alone-completion", _g2_no_alone_completion),
    ("G3-identity-sensitivity", _g3_identity_sensitivity),
    ("G4-profile-genericity", _g4_profile_genericity),
    ("G5-no-second-runtime", _g5_no_second_runtime),
    ("G6-no-case-literals", _g6_no_case_literals),
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
    before = {"evidence": _db_identity(_EVIDENCE_DB),
              "financial": _db_identity(_FIN_DB)}
    try:
        _estore._db_path = _EVIDENCE_DB
        ctx = _chain()
        for name, fn in _GROUPS:
            _run_group(name, fn, ctx)
    finally:
        _estore._db_path = previous_db_path
        after = {"evidence": _db_identity(_EVIDENCE_DB),
                 "financial": _db_identity(_FIN_DB)}
        check(before == after,
              "只读前置：`data/evidence.db` 与 `data/financial_v2.db` 在本模块前后"
              f"逐项不变（size/mtime_ns/sha256）")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

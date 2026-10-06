# -*- coding: utf-8 -*-
"""TS5 §19.12.7-1,-2,-3,-4 与 §19.12.1-13：正式链唯一性、泛化、方案 C 版本隔离。

全部离线：只读本地 PDF / 冻结产物 / **只读** `data/evidence.db`；无网络、无 LLM、
无 Bocha、不写任何文件（不改任何冻结产物、不建任何结果目录）。

覆盖（矩阵条目 → 分组）：

- **§19.12.7-1**（`F1-formal-chain`）：唯一正式链
  `VerifiedSpanSnapshot → final builder → final verifier → verified final wrapper`
  在 `run_tree_table_acceptance.build_document` 里按此顺序成立；`FinalMaterialStructure
  Snapshot` 的生产构造点**唯一**（AST 扫描：只有 `final_material_builder` 构造它）。
- **§19.12.7-2**（`F1-formal-chain`）：不调用第二 Router/Harness/Retriever/
  ToolRegistry，不联网、不上 LLM——由 AST 扫描 + `authority_isolation_problems()` +
  `verifier_independence_problems()` + `self_check()` 三路机械证明。
- **§19.12.7-3**（`F1-formal-chain`）：`non_300750` fixture 走**同一个**公共入口
  `build_document`；逐文档分支只允许是 `source_kind`（`versioned_fixture` /
  `historical_run`），执行链里不得出现任何文档键或公司 id 字面量。
- **§19.12.7-4**（`F1-formal-chain`）：生产文件 AST 扫描无公司名 / 证券代码 /
  文档键 / 表号 / Evidence 前缀 / gold 关键词；唯一的例外是**反硬编码门自身**
  （`table_classification._FORBIDDEN_PROFILE_TOKEN_PATTERNS`），且逐条比对。
- **§19.12.1-13 方案 C 版本隔离**（`F2-plan-c-isolation`）：
  1. 四份封存 `spn-1` 快照经 `from_dict` 读回、canonical round-trip、snapshot/
     content/input 指纹与**独立复核器**逐字不变（逐份，不是抽样）；
  3. `spn-1` / `nsv-1` 只接受 `nss-2` / `ns-2`，拒绝 `nss-3` / `ns-3` 与
     `FinalNavigationSynopsis` 载荷；
  4. `fms-1` 只接受 `nss-3` / `ns-3` 的 `FinalNavigationSynopsis`，拒绝 TS4
     `NavigationSynopsis` 对象；
  5. 两套五值 reason 词表各自**精确相等**，互相专属值混入即被构造期拒绝；
  6. `FinalNavigationSynopsis` 的每个 snippet 只解析到同一 final 快照的 `fos-*`；
     TS4 span id / 不存在成员 / TableObject id 一律拒绝；
  7. registry 登记两个 public wire type 与两条版本轴：同名不重复登记、不同版本轴
     不共用一个常量、两类型之间无继承 / 无 alias；
  8. 不存在 `spn-2`，且 TS4 容器的版本常量没有被 final 轴顶替。
- **§19.12.1-13 第 2 条 + §六第 13 条**（`F3-frozen-trust-root`）：信任根的四份
  快照身份必须来自**封存的 TS4-B `span_snapshot_index.json`**——本模块自带该运行
  目录的**路径与索引 sha256 字面量**，逐份比对"索引里的 ID ↔ TS5 信任锚里的 ID ↔
  当前代码在 TS4-B 口径下重算的 ID"。任一不一致即 fail-closed；只把信任锚改成
  漂移值，`build_document` 必须抛出 `StepFailure(field="ts4_snapshot_id")`。

本模块不复制生产判据：所有断言都用**测试自带字面量**与**根读数**重算，反例一律断言
具体错误类型 + 具体错误文本；每条门都配一个"真实件通过"的非空转对照。
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import re
import time
from pathlib import Path

from evidence import store as _estore

from document_structure import evidence_gateway as EG
from document_structure import final_material_builder as FMB
from document_structure import final_verifier as FV
from document_structure import schema as S
from document_structure import span_builder as SB
from document_structure import span_schema as SS
from document_structure import span_verifier as SV
from document_structure import synopsis as SY
from document_structure import table_classification as TC
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError
from document_structure.canonical import canonical_json, sha256_canonical
from evaluation import run_tree_span_acceptance as R
from evaluation import run_tree_table_acceptance as RR

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
    bad = list(bad)
    return check(not bad and total > 0,
                 f"{msg}（核查 {total} 项，异常 {len(bad)} 项：{bad[:sample]}）")


# ---------------------------------------------------------------------------
# 0. 测试自带真值表（**不复用生产常量作为期望**）
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"
_TS5_TRUST_ROOT = (_REPO / "evals" / "fixtures" / "tree_structure"
                   / "ts5_trust_roots_v2.json")
_TS5_FIXTURE_MANIFEST = (
    _REPO / "evals" / "fixtures" / "tree_structure" / "non_300750_ts5" / "manifest.json")

_FIXTURE_KEY = "FIXTURE_BOND_2026__non_300750_ts4"

#: §19.0.1 方案 C：两条**互不相交**的简介版本轴（`schema_version` / 生成规则版本）。
_TS4_SYNOPSIS_AXIS = ("nss-2", "ns-2")
_TS5_SYNOPSIS_AXIS = ("nss-3", "ns-3")
#: 冻结的 TS4 容器轴：`SpanBuildSnapshot` / `SynopsisSourceValidation`。
_TS4_CONTAINER_AXIS = ("spn-1", "nsv-1")
#: 两套 reason 词表（各五值，末位互为对方专属值）。
_TS4_REASONS = ("no_span", "empty_text", "length_exceeded", "alignment_failed",
                "table_only_pending_ts5")
_TS5_REASONS = ("no_span", "empty_text", "length_exceeded", "alignment_failed",
                "table_material_available_no_text_synopsis")
_TS4_EXCLUSIVE_REASON = "table_only_pending_ts5"
_TS5_EXCLUSIVE_REASON = "table_material_available_no_text_synopsis"

#: 封存的 TS4-B run（计划 §19 正文点名的那一份真实产物）与它的快照索引哈希。
#: 索引哈希是**字面量**：索引一旦被改写，本模块立即失败，而不是"读到一个新的真相"。
_TS4B_FROZEN_RUN_RELPATH = "evaluation/results/tree_span_ts4_ts4b_20260918T174908Z"
_TS4B_INDEX_SHA256 = "47d2b922566d0020c56602029d8a1b7d666e45bfb154a9c7db31262ce02c0aa6"

#: 四份封存快照的**原身份**（document_key → snapshot_id / content / input 指纹）。
#: 与 `span_snapshot_index.json` 逐字相同的字面量副本：它是"信任根必须等于什么"的
#: 唯一期望来源，**不**从任何 fixture 反读。
_FROZEN_SNAPSHOTS = (
    ("NDSD_2024_year", "sbs-2d81906d04e11b56",
     "6c7ebf1ce5b0aef44e02f50e990e8a9d25fd8d9474c02eaf357cf4e1266d65f4",
     "cc28facc3e53442c331632eb32724f989c932021c59b99352c4d3d1d6455431c"),
    ("NDSD_2025_year", "sbs-ad292e6392baf81e",
     "cd6bc2307c97d3d3dcf32d01be75143df431f48ebd92193ed9ed73055eafd7ce",
     "b47fd683769fb6f7bf385c0e75b8e212fc045deb2298ebfb11f7702bd32c1525"),
    ("NDSD_KCZ_2026", "sbs-d40fb919ae556c44",
     "09a49d408400b54eff1b361eb9378da67cee9f645418f8ab228594ba5bd1feb8",
     "208e84d33484bd06a11bdc261dbe343e39d45ca77d6e6a8ccfa47269ab50b002"),
    (_FIXTURE_KEY, "sbs-2f3ed93a3f08fd50",
     "33401f9b79b764a2a5ac506e1dafd2cf483978bb6061f0205a6ce3d97b384869",
     "09bb77741725cd3df2d270e3257ab5d78426a0f8a68a2c24e75cc8e84552e63c"),
)

#: TS5 首轮编码因**错误地全局升版**而算出的四个漂移 ID。它们**不是**信任根：
#: 任何一份出现在 `ts5_trust_roots.json` 或 fixture pin 里都是 P0。
_DRIFTED_SNAPSHOT_IDS = (
    "sbs-cce5cdcb945c0578", "sbs-ee68c35d71c2427f", "sbs-844f1a796b96af95",
    "sbs-8f0d81c59a6c1a55",
)

#: 执行链里**不得出现**的字面量：四个文档键 + 两个公司 id。它们只允许出现在
#: evaluation manifest、review 文案与测试断言里（§19.13），不得进入生产分支。
_DOCUMENT_KEY_LITERALS = tuple(key for key, *_ in _FROZEN_SNAPSHOTS)
_COMPANY_ID_LITERALS = ("300750", "555555")

#: §19.12.7-4 的生产文件扫描模式（公司名 / 证券代码 / 文档键 / 表号 / Evidence 前缀
#: / gold 关键词）。它们只在**生产模块**的字符串常量上生效——注释与文档字符串不是
#: 运行期数据，不参与判定（但会另做报告）。
_FORBIDDEN_PRODUCTION_LITERALS = (
    "300750", "CATL", "宁德时代", "NDSD", "FIXTURE_BOND",
    "表4-1", "表5-10", "evb-", "gold",
)

#: §19.12.7-4 的**逐条**豁免表：登记"为什么这个字面量不是公司专用分支"。这不是放宽
#: 扫描——每条豁免都由**生产常量**或**版本化夹具文件本身**佐证（见 `_f1_...`），
#: 且豁免按**字面量**生效、不按文件生效（F1 有反例证明）。
_FIXTURE_NAMING_EXEMPTIONS: tuple[tuple[str, str], ...] = (
    ("evals/fixtures/tree_structure/non_300750_ts4",
     "夹具固定根目录：与生产常量 `evidence_gateway.FIXTURE_ROOT_RELPATH` 逐字相同"),
    ("ts4_non_300750_positive_v1",
     "夹具 manifest 的 `fixture_kind`：夹具自称 non_300750，即反硬编码夹具本身"),
)

#: 反硬编码门自身的模式表：它**必须**写出这些形状，因此是唯一"有据可查"的来源。
_GUARD_PATTERNS = frozenset(
    pattern for pattern, _ in TC._FORBIDDEN_PROFILE_TOKEN_PATTERNS)


def _literal_is_explained(literal: str) -> bool:
    """命中是否逐条有据：反硬编码门自身的模式表，或登记在册的夹具命名。"""
    if literal in _GUARD_PATTERNS:
        return True
    return any(token in literal for token, _ in _FIXTURE_NAMING_EXEMPTIONS)


def _scan_forbidden_literals(source: str, where: str) -> list:
    """在生产源码文本里找命中禁止字面量的**运行期**字符串常量（豁免逐条比对）。"""
    hits: list = []
    for lineno, literal in _string_literals_of_source(source, where):
        for bad in _FORBIDDEN_PRODUCTION_LITERALS:
            if bad.lower() in literal.lower() and \
                    not _literal_is_explained(literal):
                hits.append((where, lineno, bad, literal[:60]))
    return hits

#: 第二条运行时：TS5 链不得出现这些模块 / 名字（§19.12.7-2）。
_FORBIDDEN_RUNTIME_MODULES = frozenset({
    "requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp",
    "openai", "anthropic", "litellm", "bocha", "selenium", "playwright",
    "router", "harness", "retriever", "tool_registry", "toolregistry",
})
_FORBIDDEN_RUNTIME_NAMES = frozenset({
    "Router", "Harness", "Retriever", "ToolRegistry", "call_llm",
    "query_bocha", "run_ocr",
})

#: TS5 生产侧的"聚合快照生产模块"白名单：只有它可以在生产代码里**构造**
#: `FinalMaterialStructureSnapshot`。`table_schema.py` 只定义类型、`final_verifier.py`
#: 只读它，都不构造。按**仓库相对路径**登记（扫描的键就是它）。
_SNAPSHOT_CONSTRUCTORS = frozenset({"document_structure/final_material_builder.py"})
_SNAPSHOT_CONSTRUCTOR_MODULE = "document_structure/final_material_builder.py"


# ---------------------------------------------------------------------------
# 1. 只读现场
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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


_STATE: dict = {}


def _ctx() -> dict:
    """真实现场：TS5 信任锚 → 上游 TS4/TS3 绑定 → fixture 文档走**公共入口**。

    刻意选版本化 `non_300750` fixture：它同时是 §19.12.7-3 的样本，且体量最小
    （68 个 component），让本模块的反例可以逐条构造而不是抽样。
    """
    if _STATE:
        return _STATE
    inputs = RR.bind_inputs()
    built = RR.build_document(inputs=inputs, document_key=_FIXTURE_KEY)
    _STATE.update({"inputs": inputs, "built": built})
    return _STATE


def _ts5_trust_root() -> dict:
    return _read_json(_TS5_TRUST_ROOT)


# ---------------------------------------------------------------------------
# F1. §19.12.7-1/-2/-3/-4：正式链唯一性、泛化与禁止项
# ---------------------------------------------------------------------------

def _ast_of(relpath: str) -> ast.Module:
    return ast.parse((_REPO / relpath).read_text(encoding="utf-8"))


def _production_modules() -> tuple:
    """§19.12.7-4 的扫描范围：代码指纹里登记的**生产 Python 模块**。"""
    return tuple(rel for rel in RR.CODE_FINGERPRINT_FILES
                 if rel.startswith("document_structure/") and rel.endswith(".py"))


def _ts5_chain_modules() -> tuple:
    """TS5 生产链模块（不含 TS4 冻结链）——第二条运行时的扫描范围。"""
    keep = ("table_schema.py", "table_geometry.py", "table_classification.py",
            "table_builder.py", "final_material_builder.py", "final_verifier.py")
    return tuple(rel for rel in _production_modules()
                 if rel.rsplit("/", 1)[-1] in keep)


def _string_literals_of_source(source: str, where: str = "<source>") -> list:
    """AST 取**运行期字符串常量**（跳过模块/类/函数文档字符串）。

    判据作用在**源码文本**上，因此既能扫真实文件，也能扫内存里合成的反例源码——
    反例与正式扫描共用同一条代码路径，"门是否空转"因此可被直接证明。
    """
    tree = ast.parse(source, filename=where)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def _string_literals(relpath: str) -> list:
    return _string_literals_of_source(
        (_REPO / relpath).read_text(encoding="utf-8"), relpath)


def _call_dotted(fn: ast.AST) -> str:
    """把`TS.FinalMaterialStructureSnapshot.create` 这类被调对象还原成点号全名。

    只看叶名会漏掉**类方法构造**（`.create(...)` 的叶是 `create`），这正是
    "生产构造点唯一"必须按全名判定的原因。
    """
    parts: list = []
    node = fn
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _chain_source(*names: str) -> str:
    return "\n".join(inspect.getsource(getattr(RR, n)) for n in names)


def _f1_formal_chain(ctx) -> None:
    built = ctx["built"]
    inputs = ctx["inputs"]

    # --- §19.12.7-1：唯一链 ------------------------------------------
    chain = ("SV.verify_span_snapshot(", "FMB.build_final_material_with_audit(",
             "FV.verify_final_material_snapshot(")
    src = inspect.getsource(RR.build_document)
    positions = [src.find(seg) for seg in chain]
    check(all(p >= 0 for p in positions) and positions == sorted(positions),
          f"唯一正式链在 `build_document` 里按序成立："
          f"VerifiedSpanSnapshot → final builder → final verifier → wrapper"
          f"（位置 {positions}）")
    check("FMB.build_final_material_snapshot(" not in src,
          "`build_document` 只走**带审计**的那一个入口（不得有第二条取快照的路径）")
    check(RR.STAGE == "TS4-B" and RR.STAGE_TOKEN == "threshold_enabled" and
          R.STAGE_TOKEN["TS4-B"] == "threshold_enabled",
          f"阶段边界写死为 TS4-B / threshold_enabled（{RR.STAGE_TOKEN!r}），"
          f"不是从环境变量读的")

    # 生产构造点唯一：AST 扫描全部生产模块，看谁**调用**聚合快照类型。
    # 判定走**点号全名**：`TS.FinalMaterialStructureSnapshot.create(...)` 的叶名是
    # `create`，只看叶名会漏掉真正的构造点。
    tracked = ("FinalMaterialStructureSnapshot", "build_final_material_snapshot",
               "build_final_material_with_audit")
    callers: dict = {}
    for rel in _production_modules():
        for node in ast.walk(_ast_of(rel)):
            if not isinstance(node, ast.Call):
                continue
            dotted = _call_dotted(node.func)
            hit = [n for n in tracked if n in dotted.split(".")]
            if hit:
                callers.setdefault(rel, set()).update(hit)
    check(set(callers) == set(_SNAPSHOT_CONSTRUCTORS),
          f"`FinalMaterialStructureSnapshot` 的生产构造点唯一"
          f"（实际 {sorted(callers)}，期望 {sorted(_SNAPSHOT_CONSTRUCTORS)}）")
    check("FinalMaterialStructureSnapshot" in
          callers.get(_SNAPSHOT_CONSTRUCTOR_MODULE, set()),
          "真正的 `FinalMaterialStructureSnapshot(...)` 调用就落在 final builder 里"
          "（复核器与类型定义模块都不构造它）")

    # --- §19.12.7-2：不接第二条运行时 / 不联网 / 不上 LLM ------------
    violations: list = []
    for rel in _ts5_chain_modules() + ("evaluation/run_tree_table_acceptance.py",):
        for node in ast.walk(_ast_of(rel)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.rsplit(".", 1)[-1].lower() in \
                            _FORBIDDEN_RUNTIME_MODULES:
                        violations.append((rel, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").rsplit(".", 1)[-1].lower() in \
                        _FORBIDDEN_RUNTIME_MODULES:
                    violations.append((rel, node.lineno, node.module))
            elif isinstance(node, ast.Name) and \
                    node.id in _FORBIDDEN_RUNTIME_NAMES:
                violations.append((rel, node.lineno, node.id))
    check(not violations,
          f"TS5 链与验收 runner 不 import / 不引用第二套 Router/Harness/Retriever/"
          f"ToolRegistry，也不 import 任何网络 / LLM / OCR 客户端（{violations[:3]}）")
    check(not RR.authority_isolation_problems(),
          "`authority_isolation_problems()`：TS5 生产链无财务权威类型 / 模块引用")
    check(not FV.verifier_independence_problems(),
          "`verifier_independence_problems()`：复核器不 import / 不调用公开 builder")
    self_check = RR.self_check()
    check(not self_check["problems"],
          f"runner 自检零问题（{self_check['problems'][:2]}）")
    check(self_check["mode"] == "pinned_threshold_enabled",
          "runner 自检报告的模式就是 pinned + threshold_enabled")

    # --- §19.12.7-3：泛化（同一入口） --------------------------------
    chain_src = _chain_source("bind_inputs", "build_document",
                              "build_all_documents")
    leaked = [lit for lit in _DOCUMENT_KEY_LITERALS + _COMPANY_ID_LITERALS
              if lit in chain_src]
    check(not leaked,
          f"执行链（`bind_inputs` / `build_document` / `build_all_documents`）"
          f"不含任何文档键或公司 id 字面量（命中 {leaked}）")
    declared_kinds = frozenset(
        entry["source_kind"]
        for entry in _ts5_trust_root()["documents"].values())
    check(declared_kinds == frozenset({"versioned_fixture", "historical_run"}),
          f"信任锚声明的 `source_kind` 恰为两种合法取值"
          f"（得到 {sorted(declared_kinds)}）")
    branch_values = frozenset(re.findall(
        r'entry\["source_kind"\] == "([a-z_]+)"', chain_src))
    check(chain_src.count('entry["source_kind"]') == 1 and
          branch_values and branch_values <= declared_kinds and
          "handoff.source_kind" in chain_src and
          "EG.FIXTURE_SOURCE_KIND" in chain_src,
          f"逐文档分支只由信任锚声明的 `source_kind` 决定：唯一的分支判据读的是信任锚"
          f"条目，比较值 {sorted(branch_values)} 全部取自信任锚声明的取值集合，"
          f"身份锚点用 `EG.FIXTURE_SOURCE_KIND`（不是 runner 自立的字面量）")
    check("frozen_document_dir_relpath" in chain_src and
          "historical_run" not in chain_src,
          "非 fixture 分支由信任锚的 `frozen_document_dir_relpath` 决定，"
          "不靠 'historical_run' 之类的文档名直写")
    order_src = _chain_source("build_all_documents")
    check("inputs.document_order" in order_src and
          len(order_src.splitlines()) <= 4,
          "`build_all_documents` 只遍历信任锚的固定 `document_order`，没有逐文档分支"
          "（四份文档共用同一 `build_document`；体量原因这里做静态证明，四份实跑由 "
          "§七 的正式验收覆盖）")

    # 版本化 non-300750 fixture 走的**就是**这个公共入口。
    check(built["document_key"] == _FIXTURE_KEY and
          built["entry"]["scope"] == "pinned_acceptance/versioned_fixture",
          "版本化非 300750 正向样本由同一个 `build_document` 构建（无专用分支）")
    check(built["verdict"] == "issued" and built["wrapper"] is not None,
          "fixture 文档走完正式链并**签发**了 final capability（`issued` 分支可达）")
    check(built["final"].table_count == built["entry"]["expected_table_count"] and
          built["final"].final_span_count ==
          built["entry"]["expected_final_span_count"],
          f"fixture 的 TS5 观测值与信任锚 pin 一致"
          f"（表 {built['final'].table_count} / final span "
          f"{built['final'].final_span_count}）")

    # --- §19.12.7-4：生产文件扫描 ------------------------------------
    # 豁免**逐条**可查：第一条与生产常量逐字相同，第二条由版本化夹具 manifest 自证。
    check(EG.FIXTURE_ROOT_RELPATH == _FIXTURE_NAMING_EXEMPTIONS[0][0],
          "豁免 1 的根目录与生产常量 `evidence_gateway.FIXTURE_ROOT_RELPATH` 逐字相同")
    fixture_kind = _read_json(
        _REPO / "evals" / "fixtures" / "tree_structure" /
        "non_300750_ts4" / "manifest.json")["fixture_kind"]
    check(fixture_kind == _FIXTURE_NAMING_EXEMPTIONS[1][0],
          f"豁免 2 的 `fixture_kind` 就是版本化夹具 manifest 自报的值"
          f"（{fixture_kind!r}）")

    hits: list = []
    for rel in _production_modules():
        hits.extend(_scan_forbidden_literals(
            (_REPO / rel).read_text(encoding="utf-8"), rel))
    check(not hits,
          f"生产模块的运行期字符串常量不含公司名 / 证券代码 / 文档键 / 表号 / "
          f"Evidence 前缀 / gold 关键词（{len(hits)} 条命中：{hits}）")

    # 非空转对照：同一条判据作用在**内存里合成的**源码上，必须分别命中与放行。
    probe_bad = 'COMPANY = "300750"\nif document_key == "NDSD_2024_year":\n    pass\n'
    bad_hits = _scan_forbidden_literals(probe_bad, "<probe:hardcoded>")
    check(len(bad_hits) == 2 and {h[2] for h in bad_hits} == {"300750", "NDSD"},
          f"反例源码里的公司专用分支与文档键分支都被抓出（{bad_hits}）")
    probe_exempt = ('ROOT = "evals/fixtures/tree_structure/non_300750_ts4"\n'
                    'KIND = "ts4_non_300750_positive_v1"\n')
    check(_scan_forbidden_literals(probe_exempt, "<probe:fixture>") == [],
          "登记在册的夹具命名放行（豁免确实生效，否则上一条会是假阳性）")
    probe_mixed = ('ROOT = "evals/fixtures/tree_structure/non_300750_ts4"\n'
                   'COMPANY = "300750"\n')
    mixed = _scan_forbidden_literals(probe_mixed, "<probe:mixed>")
    check(len(mixed) == 1 and mixed[0][2] == "300750",
          f"豁免按**字面量**生效而不是按文件生效：同一源码里合法的夹具路径旁边的"
          f"公司代码仍被抓出（{mixed}）")


# ---------------------------------------------------------------------------
# F2. §19.12.1-13 方案 C 版本隔离反例
# ---------------------------------------------------------------------------

def _frozen_snapshot_raw(document_key: str) -> dict:
    path = _REPO / _TS4B_FROZEN_RUN_RELPATH / "span_build_snapshots" / \
        f"{document_key}.json"
    return _read_json(path)


def _f2_plan_c_isolation(ctx) -> None:
    final = ctx["built"]["final"]
    inputs = ctx["inputs"]

    # --- 版本常量：两条轴互不相交 -------------------------------------
    check((V.SYNOPSIS_SCHEMA_VERSION, V.SYNOPSIS_VERSION) == _TS4_SYNOPSIS_AXIS,
          f"TS4 简介轴未被升版：{V.SYNOPSIS_SCHEMA_VERSION!r} / "
          f"{V.SYNOPSIS_VERSION!r}")
    check((V.FINAL_SYNOPSIS_SCHEMA_VERSION, V.FINAL_SYNOPSIS_VERSION) ==
          _TS5_SYNOPSIS_AXIS,
          f"final 简介轴是独立的一对：{V.FINAL_SYNOPSIS_SCHEMA_VERSION!r} / "
          f"{V.FINAL_SYNOPSIS_VERSION!r}")
    check((V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION,
           V.SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION) == _TS4_CONTAINER_AXIS,
          f"TS4 容器轴未被升级：{V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION!r} / "
          f"{V.SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION!r}")
    check(not (set(_TS4_SYNOPSIS_AXIS) & set(_TS5_SYNOPSIS_AXIS)),
          "两条版本轴的值集**不相交**（同一字符串不得有双重解释）")
    check(_TS4_REASONS[-1] not in _TS5_REASONS and
          _TS5_REASONS[-1] not in _TS4_REASONS,
          "两套 reason 词表各自含一个对方**禁止**的专属值")

    # --- §19.12.1-13-8：不存在 `spn-2`，TS4 容器常量没被顶替 -----------
    # 唯一允许写出这四个字符的地方就是本模块——它存在的意义正是断言该版本不存在。
    _SELF_REL = "evals/test_tree_table_formal_chain.py"
    spn2: list = []
    for rel in _production_modules() + tuple(
            rel for rel in RR.CODE_FINGERPRINT_FILES if rel.startswith("evals/")) \
            + ("evaluation/run_tree_table_acceptance.py",
               "evaluation/tree_table_acceptance_schema.py"):
        if rel == _SELF_REL or not (_REPO / rel).is_file():
            continue
        if "spn-2" in (_REPO / rel).read_text(encoding="utf-8"):
            spn2.append(rel)
    check(not spn2, f"仓库内不存在 `spn-2`（命中 {spn2}）")
    check(V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION == "spn-1" and
          "SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION" in V.VERSION_CONSTANTS and
          V.VERSION_CONSTANTS["SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION"] == "spn-1",
          "`SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION` 的值就是 `spn-1`（不得靠升级 TS4 "
          "容器让测试通过）")

    # --- §19.12.1-13-1：四份封存 spn-1 快照逐份读回 + 独立复核 ---------
    handoffs = inputs.bindings.handoffs
    problems: list = []
    index_ids: dict = {}
    for document_key, snapshot_id, content_fp, input_fp in _FROZEN_SNAPSHOTS:
        raw = _frozen_snapshot_raw(document_key)
        index_ids[document_key] = raw.get("snapshot_id")
        try:
            snap = SS.SpanBuildSnapshot.from_dict(raw)
        except Exception as e:  # noqa: BLE001
            problems.append((document_key, "from_dict", f"{type(e).__name__}: {e}"))
            continue
        if snap.snapshot_id != snapshot_id:
            problems.append((document_key, "snapshot_id", snap.snapshot_id))
        if snap.content_fingerprint != content_fp:
            problems.append((document_key, "content_fingerprint"))
        if snap.input_fingerprint != input_fp:
            problems.append((document_key, "input_fingerprint"))
        # canonical round-trip：读回 → 写出 → 再解析，身份与内容指纹不变。
        again = SS.SpanBuildSnapshot.from_dict(snap.to_dict())
        if again.snapshot_id != snap.snapshot_id or \
                canonical_json(again.to_dict()) != canonical_json(snap.to_dict()):
            problems.append((document_key, "canonical_round_trip"))
        # 独立复核器：以受信 handoff 为根重算，不接受快照自证。
        try:
            verified = SV.verify_span_snapshot(snap, handoffs[document_key])
        except Exception as e:  # noqa: BLE001
            problems.append((document_key, "verify",
                             f"{type(e).__name__}: {e}"))
            continue
        if verified.snapshot.snapshot_id != snap.snapshot_id:
            problems.append((document_key, "verified_snapshot_id",
                             verified.snapshot.snapshot_id))
        # 独立复核必须是**可复算**的：同一 (快照, 交接) 两次复核指纹相同。
        if SV.verify_span_snapshot(snap, handoffs[document_key]) \
                .verification_fingerprint != verified.verification_fingerprint:
            problems.append((document_key, "verification_not_recomputable"))
    check_aggregate(problems, len(_FROZEN_SNAPSHOTS),
                    "四份封存 `spn-1` 快照逐份：`from_dict` 读回、canonical "
                    "round-trip、snapshot/content/input 指纹与独立复核器全部逐字不变")

    # --- §19.12.1-13-3：spn-1 / nsv-1 只接受 nss-2 / ns-2 -------------
    fixture_raw = _frozen_snapshot_raw(_FIXTURE_KEY)
    raises(lambda: SS.SpanBuildSnapshot.from_dict(
        {**fixture_raw, "synopsis_version": _TS5_SYNOPSIS_AXIS[1]}),
        SchemaValidationError, "不得进入 TS4 容器",
        "`spn-1` 拒绝 `ns-3`（final 轴不得进入 TS4 容器）")
    raises(lambda: SS.SpanBuildSnapshot.from_dict(
        {**fixture_raw, "schema_version": _TS5_SYNOPSIS_AXIS[0]}),
        SchemaValidationError, "schema_version",
        "`spn-1` 拒绝 `nss-3`（容器版本是封闭集合）")
    frozen_container = SS.SpanBuildSnapshot.from_dict(fixture_raw)
    ts4_synopsis = next(iter(fixture_raw["synopses"]))
    check(ts4_synopsis["schema_type"] == "NavigationSynopsis",
          "封存快照里的简介就是 TS4 `NavigationSynopsis`（反例载体真实）")
    ts5_payload = {**ts4_synopsis, "schema_type": "FinalNavigationSynopsis",
                   "schema_version": _TS5_SYNOPSIS_AXIS[0]}
    raises(lambda: SS.SpanBuildSnapshot.from_dict(
        {**fixture_raw, "synopses": [ts5_payload]}),
        SchemaValidationError, "schema_type",
        "`spn-1` 的 synopses 逐条点名 TS4 reader：`FinalNavigationSynopsis` 载荷被拒"
        "（aggregate 决定合法 leaf，不按 child 版本猜）")
    check(bool(frozen_container.synopses) and
          all(type(n) is S.NavigationSynopsis for n in frozen_container.synopses),
          f"非空转对照：同一份封存载荷原样读回时 synopses 全是 TS4 "
          f"`NavigationSynopsis`（{len(frozen_container.synopses)} 条），"
          f"上一条的拒绝来自被替换的 schema_type")
    nsv = SS.SynopsisSourceValidation.create(
        node_id=fixture_raw["synopses"][0]["node_id"],
        synopsis_id=fixture_raw["synopses"][0]["synopsis_id"],
        snippet_checks=())
    nsv_raw = nsv.to_dict()
    check(nsv_raw["schema_version"] == "nsv-1" and
          nsv_raw["synopsis_version"] == _TS4_SYNOPSIS_AXIS[1],
          f"`nsv-1` 记录自报的轴恰为 {_TS4_CONTAINER_AXIS[1]!r} / "
          f"{_TS4_SYNOPSIS_AXIS[1]!r}")
    raises(lambda: SS.SynopsisSourceValidation.from_dict(
        {**nsv_raw, "synopsis_version": _TS5_SYNOPSIS_AXIS[1]}),
        SchemaValidationError, "不得进入 TS4 容器",
        "`nsv-1` 同样拒绝 `ns-3`（简介来源校验器也只在 TS4 轴上）")

    # --- §19.12.1-13-4：fms-1 只接受 nss-3/ns-3，拒绝 TS4 简介 ---------
    check(all(type(n).__name__ == "FinalNavigationSynopsis" for n in final.synopses)
          and final.synopses,
          f"final 快照的 synopses 全是 `FinalNavigationSynopsis`"
          f"（{len(final.synopses)} 条）")
    check(all(n.schema_version == _TS5_SYNOPSIS_AXIS[0] and
              n.synopsis_version == _TS5_SYNOPSIS_AXIS[1]
              for n in final.synopses),
          "每一条 final 简介自报的都是 `nss-3` / `ns-3`")
    ok_fields = {f.name: getattr(final, f.name)
                 for f in dataclasses.fields(final)}
    raises(lambda: TS.FinalMaterialStructureSnapshot.create(
        **{**ok_fields, "synopses": tuple(frozen_container.synopses)}),
        SchemaValidationError, "不得进入 fms-1",
        "`fms-1` 拒绝真正的 TS4 `NavigationSynopsis` 实例（类型本身即版本轴断言）")
    check(TS.FinalMaterialStructureSnapshot.create(
        **ok_fields).snapshot_id == final.snapshot_id,
        "非空转对照：真实 final 快照原样重建通过（上一条的拒绝来自被塞入的 TS4 简介）")
    spans_of_final = {s.span_id for s in final.final_spans}
    ts4_span_ids = {s.span_id for s in frozen_container.spans}
    check(spans_of_final and ts4_span_ids and
          not (spans_of_final & ts4_span_ids),
          f"final span（{len(spans_of_final)} 个 `fos-*`）与 TS4 span"
          f"（{len(ts4_span_ids)} 个 `os-*`）身份**完全不相交**")
    check(TS.FinalNavigationSynopsis is not S.NavigationSynopsis and
          not issubclass(TS.FinalNavigationSynopsis, S.NavigationSynopsis) and
          not issubclass(S.NavigationSynopsis, TS.FinalNavigationSynopsis),
          "两个简介类型之间**没有**继承关系（不得靠继承式适配合并）")

    # --- §19.12.1-13-5：两套词表各自精确相等，专属值互相拒绝 ----------
    check(tuple(S.SYNOPSIS_REASON_CODES) == _TS4_REASONS,
          f"TS4 词表恰为五值 {_TS4_REASONS}（得到 {S.SYNOPSIS_REASON_CODES}）")
    check(tuple(TS.FINAL_SYNOPSIS_REASON_CODES) == _TS5_REASONS,
          f"final 词表恰为五值 {_TS5_REASONS}"
          f"（得到 {TS.FINAL_SYNOPSIS_REASON_CODES}）")
    self_syn = SY.self_check()
    check("problems" in self_syn and not self_syn["problems"],
          f"`synopsis.self_check()` 零问题（{self_syn.get('problems', [])[:2]}）")
    ts4_node = ts4_synopsis
    if ts4_node.get("status") == "synopsis_unavailable":
        raises(lambda: S.NavigationSynopsis.from_dict(
            {**ts4_node, "reason_code": _TS5_EXCLUSIVE_REASON}),
            SchemaValidationError, "reason_code",
            "TS4 简介拒绝 final 专属的 `table_material_available_no_text_synopsis`")
    else:
        check(False, "反例载体：封存快照里必须存在一条 `synopsis_unavailable` 的 "
                     "TS4 简介（否则本条无法构造）")
    final_unavail = [n for n in final.synopses
                     if n.status == "synopsis_unavailable"]
    check(bool(final_unavail),
          f"final 快照里存在 `synopsis_unavailable` 的简介"
          f"（{len(final_unavail)} 条，反例可构造）")
    if final_unavail:
        victim = final_unavail[0].to_dict()
        raises(lambda: TS.FinalNavigationSynopsis.from_dict(
            {**victim, "reason_code": _TS4_EXCLUSIVE_REASON}),
            SchemaValidationError, "reason_code",
            "final 简介拒绝 TS4 专属的 `table_only_pending_ts5`")
        # 正向对照：final 词表的专属值必须**被本类型接受**——否则上一条可能只是
        # "这一整个 reason 字段被拒"，而不是"TS4 的专属值被拒"。
        node_obj = final_unavail[0]
        accepted = TS.FinalNavigationSynopsis.create(
            **{**{f.name: getattr(node_obj, f.name)
                  for f in dataclasses.fields(node_obj)},
               "reason_code": _TS5_EXCLUSIVE_REASON})
        check(accepted.reason_code == _TS5_EXCLUSIVE_REASON and
              accepted.status == "synopsis_unavailable",
              f"final 专属的 `{_TS5_EXCLUSIVE_REASON}` 被 final 类型接受"
              f"（同一节点换回 TS4 的 `{_TS4_EXCLUSIVE_REASON}` 则在上一条被拒——"
              f"两条合起来才说明词表是**互斥**的）")

    # --- §19.12.1-13-6：snippet 只解析到同一快照的 fos-* --------------
    fos_problems: list = []
    span_ids = spans_of_final
    for node in final.synopses:
        for sid in node.source_final_span_ids:
            if not sid.startswith(TS.FINAL_SPAN_ID_PREFIX):
                fos_problems.append((node.node_id, sid, "prefix"))
            elif sid not in span_ids:
                fos_problems.append((node.node_id, sid, "not_member"))
        for snippet in node.snippets:
            if snippet.final_span_id not in node.source_final_span_ids:
                fos_problems.append((node.node_id, snippet.final_span_id,
                                     "snippet_not_in_sources"))
    check_aggregate(fos_problems, sum(len(n.snippets) for n in final.synopses),
                    "每条 final 简介的来源与片段都指向**本快照内**的 `fos-*`")
    check(bool(final.final_spans),
          f"final 快照确有 final span（{len(final.final_spans)} 条，否则上一条空转）")
    if final_unavail:
        victim = final_unavail[0]
        raises(lambda: TS.FinalNavigationSynopsis.create(
            **{**{f.name: getattr(victim, f.name)
                   for f in __import__("dataclasses").fields(victim)},
               "source_final_span_ids":
                   (fixture_raw["spans"][0]["span_id"],)}),
            SchemaValidationError, TS.FINAL_SPAN_ID_PREFIX,
            "final 简介的来源塞进 TS4 `os-*` span id 即被构造期拒绝")

    # --- §19.12.1-13-7：registry 登记两个类型、两条版本轴 --------------
    ts4_types = V.VERSIONED_OBJECT_SCHEMA_FIELDS
    spans_types = V.SPAN_RECORD_PUBLIC_TYPES
    table_types = V.TABLE_RECORD_PUBLIC_TYPES
    check("NavigationSynopsis" in ts4_types and
          "NavigationSynopsis" not in spans_types and
          "NavigationSynopsis" not in table_types,
          "TS4 `NavigationSynopsis` 留在它原来那张登记表里，**不**被搬进 TS5 表"
          "（也不重复登记）")
    check("FinalNavigationSynopsis" in table_types and
          "FinalNavigationSynopsis" not in ts4_types and
          "FinalNavigationSynopsis" not in spans_types,
          "`FinalNavigationSynopsis` 只登记在 TS5 表里（另一个名字，不共用登记项）")
    check(not (set(spans_types) & set(table_types)),
          "TS4 与 TS5 两张 public wire type 表**无重名**")
    check(len(table_types) == 11 and len(set(table_types)) == 11,
          f"TS5 顶层 public wire type 恰为 11 项且无重复（得到 {len(table_types)}）")
    check(V.VERSIONED_OBJECT_SCHEMA_FIELDS["NavigationSynopsis"] ==
          ("schema_version", "SYNOPSIS_SCHEMA_VERSION") and
          V.TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS["FinalNavigationSynopsis"]
          == ("schema_version", "FINAL_SYNOPSIS_SCHEMA_VERSION"),
          "两个类型各自绑定**不同**的 schema 版本常量（同一常量不得双重解释）")
    all_fields = V.ALL_VERSIONED_OBJECT_SCHEMA_FIELDS
    merged = len(ts4_types) + len(spans_types) + len(table_types)
    check(len(all_fields) == merged,
          f"三张登记表合并后无重名（并集 {len(all_fields)} 项 = 三表之和 {merged}）")
    check(set(all_fields) == set(ts4_types) | set(spans_types) |
          set(table_types),
          "并集恰为三张登记表名字的全集（没有第四张隐式表）")
    by_constant: dict = {}
    for name, (field, constant) in all_fields.items():
        by_constant.setdefault(constant, []).append((name, field))
    check(by_constant["SYNOPSIS_SCHEMA_VERSION"] ==
          [("NavigationSynopsis", "schema_version")],
          "`SYNOPSIS_SCHEMA_VERSION` 只被 TS4 `NavigationSynopsis` 使用")
    check(by_constant["FINAL_SYNOPSIS_SCHEMA_VERSION"] ==
          [("FinalNavigationSynopsis", "schema_version")],
          "`FINAL_SYNOPSIS_SCHEMA_VERSION` 只被 `FinalNavigationSynopsis` 使用")
    # 两个类的**自报常量**也必须各自指向自己的轴。TS4 `NavigationSynopsis` 不自报
    # `SCHEMA_CONSTANT`（它的绑定只登记在 `VERSIONED_OBJECT_SCHEMA_FIELDS` 里），
    # 因此这里按类型逐个校验"自报了就必须与登记表一致"，并要求 final 侧确实自报。
    mismatched: list = []
    for name, (_field, constant) in \
            V.TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS.items():
        declared = getattr(getattr(TS, name, None), "SCHEMA_CONSTANT", None)
        if declared is not None and declared != constant:
            mismatched.append((name, declared, constant))
    check(not mismatched and
          TS.FinalNavigationSynopsis.SCHEMA_CONSTANT ==
          "FINAL_SYNOPSIS_SCHEMA_VERSION" and
          not hasattr(S.NavigationSynopsis, "SCHEMA_CONSTANT"),
          f"TS5 侧每个版本化类自报的 schema 常量都与登记表一致（{mismatched}）；"
          f"final 简介自报 `FINAL_SYNOPSIS_SCHEMA_VERSION`，而 TS4 "
          f"`NavigationSynopsis` 完全不自报常量——"
          f"两个类型不是同一个常量挂两个名字")
    registry_problems = V.check_version_registry(
        V.TABLE_RECORD_PUBLIC_TYPES,
        V.TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS,
        substructure_names=V.TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES,
        runtime_only_names=V.TABLE_RECORD_RUNTIME_ONLY_TYPE_NAMES)
    check(registry_problems == (),
          f"TS5 版本注册表双射自检零问题（{registry_problems[:2]}）")
    check("FINAL_SYNOPSIS_VERSION" in
          V.TABLE_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES and
          "SYNOPSIS_VERSION" not in
          V.TABLE_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES and
          V.FINAL_SYNOPSIS_VERSION == _TS5_SYNOPSIS_AXIS[1] and
          V.SYNOPSIS_VERSION == _TS4_SYNOPSIS_AXIS[1] and
          not (set(V.TABLE_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES) &
               set(V.SPAN_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES)),
          "final 的算法轴 `FINAL_SYNOPSIS_VERSION` 登记在 TS5 侧，"
          "TS4 的 `SYNOPSIS_VERSION` 不在其中，且 TS4 / TS5 两张算法轴表**无交集**"
          "（两条轴不互相顶替）")
    module_checks = {"span_schema": SS.self_check(), "table_schema": TS.self_check(),
                     "synopsis": SY.self_check()}
    failed_modules = [(k, d.get("problems"))
                      for k, d in module_checks.items() if d.get("problems")]
    check(all("problems" in d for d in module_checks.values()) and
          not failed_modules,
          f"span / table / synopsis 三个模块的自检均零问题（{failed_modules}）")


# ---------------------------------------------------------------------------
# F3. §19.12.1-13-2 + §六第 13 条：信任根必须等于封存 TS4-B 索引
# ---------------------------------------------------------------------------

def _f3_frozen_trust_root(ctx) -> None:
    index_path = _REPO / _TS4B_FROZEN_RUN_RELPATH / "span_snapshot_index.json"
    check(_sha256_file(index_path) == _TS4B_INDEX_SHA256,
          f"封存 TS4-B 的 `span_snapshot_index.json` 未被改写"
          f"（{_TS4B_INDEX_SHA256}）")
    index = _read_json(index_path)
    check(index["stage"] == "TS4-B",
          f"索引自报阶段为 TS4-B（得到 {index['stage']!r}）")
    by_key = {row["document_key"]: row for row in index["snapshots"]}
    trust = _ts5_trust_root()
    fixture_pin = _read_json(_TS5_FIXTURE_MANIFEST)

    problems: list = []
    for document_key, snapshot_id, content_fp, input_fp in _FROZEN_SNAPSHOTS:
        row = by_key.get(document_key)
        if row is None:
            problems.append((document_key, "missing_in_index"))
            continue
        if row["snapshot_id"] != snapshot_id:
            problems.append((document_key, "index_id", row["snapshot_id"]))
        if row["content_fingerprint"] != content_fp or \
                row["input_fingerprint"] != input_fp:
            problems.append((document_key, "index_fingerprints"))
        entry = trust["documents"].get(document_key) or {}
        if entry.get("ts4_snapshot_id") != snapshot_id:
            problems.append((document_key, "trust_root_id",
                             entry.get("ts4_snapshot_id")))
        if entry.get("ts4_component_count") != row["counts"]["components"] or \
                entry.get("ts4_disposition_count") != row["counts"]["dispositions"]:
            problems.append((document_key, "trust_root_counts"))
    check_aggregate(problems, len(_FROZEN_SNAPSHOTS),
                    "TS5 信任锚的四份 `ts4_snapshot_id` 与封存 TS4-B 索引"
                    "逐份、逐字相同（含 counts）")
    check(fixture_pin["expected_upstream"]["ts4_snapshot_id"] ==
          by_key[_FIXTURE_KEY]["snapshot_id"],
          "fixture 自己的 pin 也指向同一份封存快照（三处一致）")
    check(not (set(_DRIFTED_SNAPSHOT_IDS) &
               ({row["snapshot_id"] for row in index["snapshots"]} |
                {entry.get("ts4_snapshot_id")
                 for entry in trust["documents"].values()} |
                {fixture_pin["expected_upstream"]["ts4_snapshot_id"]})),
          f"因错误全局升版算出的四个漂移 ID {_DRIFTED_SNAPSHOT_IDS} 一处都不在"
          f"索引 / 信任锚 / fixture pin 里")

    # --- 重现：当前代码在 TS4-B 口径下重算出的 ID 就是封存 ID ----------
    handoffs = ctx["inputs"].bindings.handoffs
    recomputed: list = []
    for document_key, snapshot_id, content_fp, input_fp in _FROZEN_SNAPSHOTS:
        snap = SB._build_from_pinned_handoff(
            handoffs[document_key], stage=RR.STAGE_TOKEN)
        if snap.snapshot_id != snapshot_id:
            recomputed.append((document_key, "snapshot_id", snap.snapshot_id))
        if snap.content_fingerprint != content_fp:
            recomputed.append((document_key, "content_fingerprint"))
        if snap.input_fingerprint != input_fp:
            recomputed.append((document_key, "input_fingerprint"))
    check_aggregate(recomputed, len(_FROZEN_SNAPSHOTS),
                    "TS4 重新构建仍**逐份**产生封存索引中的相同 ID / 指纹"
                    "（final 版本常量没有改变 TS4 身份）")

    # --- fail-closed：信任锚与封存索引不一致时必须拒绝 ------------------
    inputs = ctx["inputs"]
    entry = inputs.entry(_FIXTURE_KEY)
    original = entry["ts4_snapshot_id"]
    try:
        entry["ts4_snapshot_id"] = _DRIFTED_SNAPSHOT_IDS[0]
        try:
            RR.build_document(inputs=inputs, document_key=_FIXTURE_KEY)
            check(False, "信任锚的四份快照 ID 与封存 TS4-B 索引不一致时，"
                         "`build_document` 必须 fail-closed（实际通过了）")
        except RR.StepFailure as e:
            check(e.field == "ts4_snapshot_id" and
                  _DRIFTED_SNAPSHOT_IDS[0] in str(e),
                  f"信任锚被改成漂移 ID 后 `build_document` 以 "
                  f"`StepFailure(field='ts4_snapshot_id')` 拒绝"
                  f"（field={e.field!r}）")
        except Exception as e:  # noqa: BLE001
            check(False, f"信任锚被改成漂移 ID 后必须抛 `StepFailure`，实际抛 "
                         f"{type(e).__name__}: {e}")
    finally:
        entry["ts4_snapshot_id"] = original
    check(inputs.entry(_FIXTURE_KEY)["ts4_snapshot_id"] == original,
          "反例只在内存里改信任锚：恢复后 pin 逐字回到原件")
    check(_sha256_file(_TS5_TRUST_ROOT) == RR.TRUST_ROOT_FILE_SHA256,
          "磁盘上的信任锚文件哈希仍等于 runner 内的字面量（反例没有落盘）")


_GROUPS = (
    ("F1-formal-chain", _f1_formal_chain),
    ("F2-plan-c-isolation", _f2_plan_c_isolation),
    ("F3-frozen-trust-root", _f3_frozen_trust_root),
)


def _run_group(name, fn, ctx) -> None:
    started = time.time()
    try:
        fn(ctx)
    except Exception as e:  # noqa: BLE001
        import traceback
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 分组异常：{type(e).__name__}: {e}\n"
            f"{traceback.format_exc()[-1200:]}")
    _results["details"].append(f"__{name}__ {time.time() - started:.1f}s")


def main() -> dict:
    started = time.time()
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _identity_bundle()
    try:
        _estore._db_path = _EVIDENCE_DB
        ctx = _ctx()
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

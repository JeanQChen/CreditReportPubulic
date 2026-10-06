# -*- coding: utf-8 -*-
"""TS4 §18.13 T40：**live 唯一正式链可达**（真实现场链 + 唯一性 spy + 跨域/仿造负例 + 只读有界）。

T40 的原文口径有三件事，缺一不可：

1. **实际签发 live 四级 capability** 并由**公开** builder/verifier 正向通过；
2. **spy 证明唯一性**：走的是现有 service 的 current-store 只读预检、正式 layout
   builder、正式 aligner、正式 TS3 builder，且**没有**第二套
   Router / Harness / Retriever / ToolRegistry / 结构算法；
3. 把 `pinned_acceptance` / `testing` 域的交接传给**需要 live** 的公开入口必须被拒绝；
   伪造 / 复制的交接必须被拒绝；运行前后 `data/evidence.db` 字节不变。

**本文件走的是哪条链（必须说清，两者 scope 与 issuer version 不同，不得混为一谈）**

- 正向：`live` 域**现场链** —— 真实电子 PDF 字节 + 真实 current Evidence：
  `sections.service._prepare_stores`（现有 service 的只读预检）
  → `bind_current_evidence_authority()`（`vea-1` / `live` / `current_store`）
  → `build_verified_page_layout()`（`vpli-1`）
  → `align_evidence_set_verified()`（`vai-1`）
  → `issue_live_ts3_handoff()`（`vth-1`）
  → `build_span_snapshot(stage="distribution_only")`
  → `verify_span_snapshot()`（`vss-2`：完成判定谓词变更由该快照版本常量承载）。
  本用例的对齐是**现场对齐**（正式 aligner 当场跑），**不是** TS4-A 真实验收里的
  "冻结 `normalization_alignment.json` rows 还原"（`vaip-1` / `pinned_acceptance`）。
  因此本文件证明的是 **live 可达性与唯一性**，**不是** TS4-A 真实验收；真实验收由
  `evaluation/run_tree_span_acceptance.py` 用冻结 rows 还原终态单独完成（§18.14.2-3）。
- 负向用的 `pinned_acceptance` 交接取自仓库内**版本化夹具**
  （`evals/fixtures/tree_structure/non_300750_ts4/`，`source_kind=versioned_fixture`，
  `vthf-1`），它**不碰** `data/*.db`，也**不是** `testing` 域。

**testing 域不可达（P2 契约缺口，如实记录，不弱化断言）**：`span_schema.ISSUER_SCOPES`
含 `testing`，但生产面**没有**任何 testing-scope 的 `VerifiedPageLayout` /
`VerifiedEvidenceSetAlignment` / `VerifiedCurrentEvidenceAuthority` issuer，因此
`_testing_issue_ts3_handoff(...)` 无法由真实对象进入（强行自签会污染同一 `id(obj)`
的登记项，反而制造出计划禁止的第二套签发面）。本文件因此用**同一道 scope 门的双向
拒绝**覆盖该要求：`pinned_acceptance` → live 公开入口被拒、live → `pinned_acceptance`
/ `testing` 入口被拒、且 pinned 上游不得进入 live 对齐 / 交接入口。

只读与卫生：不写 `data/*.db`、不写 `evaluation/results/**`、不连网络、不调 LLM、
不建临时文件；spy 与模块级 `_db_path` 一律在 `finally` 里还原（本模块在 `run_evals`
里是**同进程**运行的）。
"""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib
import json
import pathlib
import pickle
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from document_structure import aligner as AL  # noqa: E402
from document_structure import evidence_gateway as EG  # noqa: E402
from document_structure import layout_builder as LB  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_policy as SP  # noqa: E402
from document_structure import span_schema as SS  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.canonical import (  # noqa: E402
    SchemaValidationError,
    sha256_canonical,
)
from document_structure.evidence_gateway import (  # noqa: E402
    bind_current_evidence_authority,
)
from document_structure.schema import PageLayout  # noqa: E402
from document_structure.span_builder import SpanBuildError  # noqa: E402
from document_structure.span_schema import CapabilityError  # noqa: E402
from evals import tree_stage_env as STAGE  # noqa: E402

# ---------------------------------------------------------------------------
# 固定装置：冻结信任根 + 冻结的库身份
# ---------------------------------------------------------------------------

DB_PATH = REPO / "data" / "evidence.db"
TRUST_ROOTS_PATH = (REPO / "evals" / "fixtures" / "tree_structure"
                    / "ts4_trust_roots.json")
RESULTS_DIR = REPO / "evaluation" / "results"

#: 本批次正式 `data/evidence.db` 的冻结身份（三项）；运行前后必须逐字段相等。
FROZEN_DB_IDENTITY: dict = {
    "size": 2367488,
    "mtime_ns": 1788840715520128300,
    "sha256": "c413118f3c54ee0a68ae2205ff9704642c22e75ae9ac188a59c3e824727c3727",
}

#: live 正向用的真实文档（信任根指向的冻结 run 里的 Evidence-backed 文档之一）。
LIVE_DOCUMENT_KEY = "NDSD_2024_year"

#: 整条链的有界时间（秒）。离线机器上远低于此值；超界即视为"有界性"不成立。
CHAIN_TIME_BOUND_SECONDS = 1800.0

#: 第二套 runtime 的模块前缀：链窗口内**新增导入**任一前缀即视为存在第二套入口。
SECOND_RUNTIME_PREFIXES: tuple[str, ...] = (
    "harness.", "tools.", "routing.", "retrieval.", "llm.",
)

#: 第二套 runtime 的具名入口（若这些模块在链窗口开始前已被别的模块导入，则改为
#: 调用计次，断言它们在链窗口内**一次都没被调用**）。与导入守卫合起来构成完备覆盖：
#: 要么模块在窗口内才被导入（→ 被导入守卫抓住），要么早已导入（→ 被计次）。
SECOND_RUNTIME_ENTRIES: tuple[tuple[str, str], ...] = (
    ("harness.runtime", "run_question"),
    ("harness.runtime", "parse_answer"),
    ("harness.material_slice_runner", "run_material_slice"),
    ("harness.topic_materials", "build_material_result"),
    ("tools.registry", "ToolRegistry"),
)

#: 终态种类 → 正式 wire 类型名（与 `alignment_summary` 的直方图口径一致）。
TERMINAL_KIND_TO_TYPE: dict = {
    "alignment": "TextAlignmentRecord",
    "refusal": "AlignmentRefusalRecord",
}

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": [],
                  "timings": {}, "observed": {}}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def skip(msg):
    _results["skipped"] += 1
    _results["details"].append("SKIP " + msg)


def note(msg):
    """只记事实、不参与通过判定（例如"走的是哪条链"）。"""
    _results["details"].append("NOTE " + msg)


def raises(fn, exc, substr, msg):
    """断言 `fn()` 抛 `exc`（或其子类）且信息含 `substr`（空串时只查类型）。"""
    try:
        fn()
    except exc as error:
        text = str(error)
        if substr == "" or substr in text:
            return check(True, msg)
        return check(False, f"{msg} —— 异常信息不含 {substr!r}：{text!r}")
    except Exception as error:  # noqa: BLE001
        return check(False, f"{msg} —— 抛出 {type(error).__name__} 而非 "
                            f"{exc.__name__}：{error}")
    return check(False, f"{msg} —— 未抛出 {exc.__name__}")


def _try(fn):
    """执行一个生产调用：`(True, 值)` 或 `(False, 异常)`；不吞掉成功 / 失败的区别。"""
    try:
        return True, fn()
    except Exception as error:  # noqa: BLE001
        return False, error


def _read_json(path) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _db_identity() -> dict:
    stat = DB_PATH.stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns),
            "sha256": hashlib.sha256(DB_PATH.read_bytes()).hexdigest()}


def _results_listing() -> list:
    if not RESULTS_DIR.is_dir():
        return []
    return sorted(p.name for p in RESULTS_DIR.iterdir())


def _norm(path) -> str:
    """Windows 下路径大小写不敏感：比较前统一归一（只用于"是不是同一个文件"）。"""
    return str(pathlib.Path(path).resolve()).replace("\\", "/").lower()


# ---------------------------------------------------------------------------
# spy：调用计次 / capability 签发计次 / 链窗口导入记录
# ---------------------------------------------------------------------------

class _CallSpy:
    """对**已导入模块**的属性做调用计次；`restore()` 逐项还原原对象。

    本链按**模块命名空间**调用正式入口（如 `AL.align_evidence_set_verified(...)`），
    与生产代码在模块内按全局名字调用的解析结果相同（同一个函数对象），因此替换模块
    属性即可**真实**拦截到正式入口的每一次调用——不是"事后读一个自报计数"。
    """

    def __init__(self) -> None:
        self.counts: dict = {}
        self.watched: list = []
        self.unwatched: list = []
        self._patched: list = []

    @staticmethod
    def key(module, name: str) -> str:
        return f"{module.__name__}.{name}"

    def watch(self, module, name: str) -> bool:
        """若该模块确实有这个名字且可调用，就开始计次；返回是否装上。"""
        original = getattr(module, name, None)
        if original is None or not callable(original):
            self.unwatched.append(self.key(module, name))
            return False
        key = self.key(module, name)
        counts = self.counts

        def spy(*args, **kwargs):
            counts[key] = counts.get(key, 0) + 1
            return original(*args, **kwargs)

        functools.update_wrapper(spy, original)
        setattr(module, name, spy)
        self._patched.append((module, name, original))
        self.watched.append(key)
        return True

    def calls(self, module, name: str) -> int:
        return self.counts.get(self.key(module, name), 0)

    def restore(self) -> None:
        for module, name, original in reversed(self._patched):
            setattr(module, name, original)
        self._patched = []


class _CapabilitySpy:
    """按 `(kind, scope)` 统计**能力签发**（五个模块命名空间分别拦截）。"""

    def __init__(self) -> None:
        self.issued: dict = {}
        self._patched: list = []

    def install(self, namespaces) -> None:
        for module in namespaces:
            original = getattr(module, "_issue_capability", None)
            if original is None:
                continue
            issued = self.issued

            def spy(obj, kind, scope, _original=original, _issued=issued):
                _issued[(kind, scope)] = _issued.get((kind, scope), 0) + 1
                return _original(obj, kind, scope)

            setattr(module, "_issue_capability", spy)
            self._patched.append((module, original))

    def restore(self) -> None:
        for module, original in reversed(self._patched):
            setattr(module, "_issue_capability", original)
        self._patched = []


class _ImportRecorder:
    """`sys.meta_path` 上的只读记录器：只记录链窗口内**新**导入的模块全名。

    已导入的模块不会经过 meta_path，因此"窗口内被导入"与"早已导入"能被干净地区分；
    本记录器对解析结果零影响（永远返回 `None`，由后续 finder 正常处理）。
    """

    def __init__(self) -> None:
        self.names: list = []
        self.active = False

    def find_spec(self, fullname, path=None, target=None):
        if self.active:
            self.names.append(fullname)
        return None

    def install(self) -> None:
        sys.meta_path.insert(0, self)

    def restore(self) -> None:
        try:
            sys.meta_path.remove(self)
        except ValueError:  # pragma: no cover - 防御性
            pass
        self.active = False


class _ChainWindow:
    """链窗口：安装 / 还原全部 spy 与导入记录器（`finally` 保证还原）。"""

    #: 正式链入口（正向）：每一步都必须恰好被走到。
    OFFICIAL_ENTRIES: tuple[tuple[str, str], ...] = (
        ("sections.service", "_prepare_stores"),
        ("document_structure.layout_builder", "build_verified_page_layout"),
        ("document_structure.aligner", "align_evidence_set_verified"),
        ("document_structure.span_builder", "issue_live_ts3_handoff"),
        ("document_structure.span_builder", "build_span_snapshot"),
        ("document_structure.span_verifier", "verify_span_snapshot"),
    )

    #: 必须**零次**被调用的旧 / 旁路 / 跨域入口。
    FORBIDDEN_ENTRIES: tuple[tuple[str, str], ...] = (
        ("document_structure.layout_builder", "_issue_cross_checked_layout"),
        ("document_structure.aligner",
         "restore_pinned_alignment_from_frozen_rows"),
        ("document_structure.aligner", "_issue_pinned_alignment_from_frozen_rows"),
        ("document_structure.span_builder", "_issue_pinned_ts3_handoff"),
        ("document_structure.span_builder", "_issue_fixture_ts3_handoff"),
        ("document_structure.span_builder", "_testing_issue_ts3_handoff"),
        ("document_structure.span_builder", "_build_from_pinned_handoff"),
        ("document_structure.span_builder", "_testing_build_span_snapshot"),
    )

    #: 私有算法核：只允许由正式入口走到（共用一套实现，不得有第二套）。
    SHARED_CORE_ENTRIES: tuple[tuple[str, str], ...] = (
        ("document_structure.span_builder", "_build_core"),
        ("document_structure.span_builder", "_build_span_input"),
        ("document_structure.span_builder", "_build_snapshot"),
        ("document_structure.span_builder", "_rebuild_outline_structure"),
        ("document_structure.span_builder", "_assert_upstream_closure"),
        ("document_structure.span_verifier", "verify_outline_structure_snapshot"),
        ("document_structure.span_verifier", "is_completion_eligible"),
    )

    def __init__(self) -> None:
        self.calls = _CallSpy()
        self.caps = _CapabilitySpy()
        self.imports = _ImportRecorder()
        self.second_runtime_watched: list = []
        self._second_runtime_restore: list = []

    @staticmethod
    def _module(dotted: str):
        return importlib.import_module(dotted)

    def install(self) -> None:
        for dotted, name in (self.OFFICIAL_ENTRIES + self.FORBIDDEN_ENTRIES
                             + self.SHARED_CORE_ENTRIES):
            self.calls.watch(self._module(dotted), name)
        for dotted, name in SECOND_RUNTIME_ENTRIES:
            module = sys.modules.get(dotted)
            if module is None:
                continue
            target = getattr(module, name, None)
            if target is None:
                continue
            if isinstance(target, type):  # 类：拦 __init__，不破坏 isinstance
                self._watch_class_init(module, name, target)
            elif self.calls.watch(module, name):
                self.second_runtime_watched.append(f"{dotted}.{name}")
        self.caps.install((EG, LB, AL, SB, SV))
        self.imports.install()
        self.imports.active = True

    def _watch_class_init(self, module, name: str, cls) -> None:
        original = cls.__init__
        counts = self.calls.counts
        key = f"{module.__name__}.{name}"

        def spy(instance, *args, **kwargs):
            counts[key] = counts.get(key, 0) + 1
            return original(instance, *args, **kwargs)

        cls.__init__ = spy
        self._second_runtime_restore.append((cls, original))
        self.second_runtime_watched.append(key)

    def restore(self) -> None:
        self.imports.active = False
        self.imports.restore()
        self.caps.restore()
        self.calls.restore()
        for cls, original in reversed(self._second_runtime_restore):
            cls.__init__ = original
        self._second_runtime_restore = []


# ---------------------------------------------------------------------------
# 伪造品构造（只读真实对象、只在内存里造，绝不回写任何仓库文件）
# ---------------------------------------------------------------------------

def _fake_handoff(real, *, structure_snapshot=None, registered: bool = False):
    """复制真实交接的**全部字段**造一个同形对象（`object.__new__` 路径）。

    `registered=True` 时把它登记为 `live` 能力——这正是计划 §18.4.2 明列的"字段仿造
    + 把全部 ID 与 hash 同步重算"这一类攻击；资格是否成立由生产侧裁定。
    """
    clone = object.__new__(SB.VerifiedTS3Handoff)
    for name in SB.VerifiedTS3Handoff.__slots__:
        if name == "__weakref__":
            continue
        object.__setattr__(clone, name, getattr(real, name))
    if structure_snapshot is not None:
        object.__setattr__(clone, "_structure_snapshot", structure_snapshot)
    if registered:
        SS._issue_capability(clone, "VerifiedTS3Handoff", "live")
    return clone


def _structure_without_line(handoff, drop_key):
    """由真实结构终态造一份"字段完全自洽"的伪造结构快照（少一行）。"""
    bound = handoff.structure_snapshot
    states = tuple(state for state in bound.line_states
                   if (state.page_number, state.line_index) != drop_key)
    return SS.OutlineStructureSnapshot.create(
        outline_algorithm_version=bound.outline_algorithm_version,
        heading_profile_version=bound.heading_profile_version,
        table_region_version=bound.table_region_version,
        toc_reconciliation_version=bound.toc_reconciliation_version,
        normalization_version=bound.normalization_version,
        document_id=bound.document_id, document_version=bound.document_version,
        page_layout_id=bound.page_layout_id, outline_locator=bound.outline_locator,
        outline_id=bound.outline_id, line_states=states)


def _pinned_fixture_handoff():
    """`pinned_acceptance` / `versioned_fixture` 交接（仓库内版本化夹具，不碰 DB）。

    它只用于**负向**：证明 `pinned_acceptance` 域的对象进不了需要 live 的公开入口。
    """
    root = EG.load_fixture_root()
    fdir = EG.fixture_root_dir()
    layout = PageLayout.from_dict(_read_json(fdir / "page_layout.json"))
    pdf_bytes = (REPO / root["source_pdf"]["relpath"]).read_bytes()
    return SB._issue_fixture_ts3_handoff(
        raw_pdf=pdf_bytes, expected_layout=layout,
        company_id=root["company_id"], document_id=root["document_id"],
        fixture_root=root)


# ---------------------------------------------------------------------------
# 0. 冻结身份（运行前）
# ---------------------------------------------------------------------------

def _check_frozen_db_identity(before: dict) -> None:
    for name in ("size", "mtime_ns", "sha256"):
        got = before[name]
        want = FROZEN_DB_IDENTITY[name]
        check(got == want,
              f"T40 运行前 `data/evidence.db` 的 {name} 必须等于冻结身份："
              f"{got!r} vs {want!r}")
    note(f"冻结库身份（运行前）：size={before['size']} "
         f"mtime_ns={before['mtime_ns']} sha256={before['sha256'][:16]}…；"
         f"resolved={_norm(DB_PATH)}")


# ---------------------------------------------------------------------------
# 1. live 现场链（正向）
# ---------------------------------------------------------------------------

def _run_live_chain(trust: dict, window: _ChainWindow, service) -> dict:
    """走完整条 live 现场链并返回各步产物（任何一步失败即记 FAIL 并停止）。"""
    doc = trust["documents"][LIVE_DOCUMENT_KEY]
    expected_identity = doc["expected_identity"]
    expected_alignment = doc["expected_alignment"]
    pdf_path = REPO / doc["source_pdf_relpath"]
    company_id = expected_identity["company_id"]
    document_id = expected_identity["document_id"]
    out: dict = {"pdf_path": pdf_path, "expected_identity": expected_identity,
                 "expected_alignment": expected_alignment,
                 "company_id": company_id, "document_id": document_id}

    # -- 1) 现有 service 的只读依赖预检（**不改**库内容，只把 store 指向既有库）----
    cfg = service.ServiceConfig(fin_db=str(REPO / "data" / "financial_v2.db"),
                                ev_db=str(REPO / "data" / "evidence.db"))
    ok, err = _try(lambda: service._prepare_stores(cfg))
    if not check(ok, "T40 步骤1：`sections.service._prepare_stores` 只读预检通过"
                     + ("" if ok else f"：{type(err).__name__}: {err}")):
        out["error"] = f"_prepare_stores 失败：{err}"
        return out
    check(window.calls.calls(service, "_prepare_stores") == 1,
          "T40 步骤1：正式链恰好调用了一次 `_prepare_stores`（现有 service 预检），"
          f"实际 {window.calls.calls(service, '_prepare_stores')} 次")

    # -- 2) live 一级：只读 Evidence authority（无参入口）----------------------
    ok, authority = _try(bind_current_evidence_authority)
    if not check(ok, "T40 步骤2：`bind_current_evidence_authority()` 签发 live Evidence "
                     "authority" + ("" if ok else f"：{type(authority).__name__}: "
                                                   f"{authority}")):
        out["error"] = f"bind_current_evidence_authority 失败：{authority}"
        return out
    out["authority"] = authority
    check(authority.issuer_scope == "live"
          and authority.source_kind == "current_store",
          "T40 步骤2：authority 的 scope/source_kind 必须为 live/current_store，"
          f"得到 {authority.issuer_scope!r}/{authority.source_kind!r}")
    check(authority.issuer_version == V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION
          == "vea-1",
          "T40 步骤2：authority issuer_version 必须为 vea-1，得到 "
          f"{authority.issuer_version!r}")
    check(authority.provider_version == V.EVIDENCE_GATEWAY_PROVIDER_VERSION
          == "egp-1",
          "T40 步骤2：evidence gateway provider 必须为 egp-1，得到 "
          f"{authority.provider_version!r}")
    ident = authority.resolved_db_identity
    check(_norm(ident["resolved_path"]) == _norm(DB_PATH)
          and ident["size"] == FROZEN_DB_IDENTITY["size"]
          and int(ident["mtime_ns"]) == FROZEN_DB_IDENTITY["mtime_ns"]
          and ident["sha256"] == FROZEN_DB_IDENTITY["sha256"],
          "T40 步骤2：authority 绑定的库四项身份与冻结的工作区正式库逐项相等"
          f"（resolved={_norm(ident['resolved_path'])}）")

    # -- 3) live 二级：受信版式（只吃 PDF 字节）--------------------------------
    ok, vlayout = _try(lambda: LB.build_verified_page_layout(
        pdf_path, company_id=company_id, document_id=document_id))
    if not check(ok, "T40 步骤3：`build_verified_page_layout` 由真实 PDF 字节签发 "
                     f"live 版式（{doc['source_pdf_relpath']}）"
                     + ("" if ok else f"：{type(vlayout).__name__}: {vlayout}")):
        out["error"] = f"build_verified_page_layout 失败：{vlayout}"
        return out
    out["vlayout"] = vlayout
    layout = vlayout.layout
    check(vlayout.issuer_scope == "live" and vlayout.source_kind == "current_store"
          and vlayout.issuer_version == V.VERIFIED_PAGE_LAYOUT_ISSUER_VERSION
          == "vpli-1",
          "T40 步骤3：版式能力必须为 live/current_store/vpli-1，得到 "
          f"{vlayout.issuer_scope!r}/{vlayout.source_kind!r}/"
          f"{vlayout.issuer_version!r}")
    check(layout.source_file_sha256 == doc["source_pdf_sha256"]
          and layout.page_layout_id == expected_identity["page_layout_id"]
          and layout.document_version == expected_identity["document_version"]
          and layout.page_count == expected_identity["page_count"],
          "T40 步骤3：重建版式的 PDF sha / page_layout_id / document_version / "
          "page_count 与信任根钉住的期望身份逐项相等")

    # -- 4) live 三级：同次正式对齐（公开入口，无 scope 参数）-------------------
    ok, alignment = _try(lambda: AL.align_evidence_set_verified(vlayout, authority))
    if not check(ok, "T40 步骤4：`align_evidence_set_verified` 现场签发 live 对齐"
                     + ("" if ok else f"：{type(alignment).__name__}: {alignment}")):
        out["error"] = f"align_evidence_set_verified 失败：{alignment}"
        return out
    out["alignment"] = alignment
    check(alignment.issuer_scope == "live"
          and alignment.issuer_version == V.VERIFIED_ALIGNMENT_ISSUER_VERSION
          == "vai-1",
          "T40 步骤4：对齐能力必须为 live/vai-1，得到 "
          f"{alignment.issuer_scope!r}/{alignment.issuer_version!r}")
    check(alignment.page_layout is layout,
          "T40 步骤4：对齐能力绑定的版式与受信版式是**同一个对象**"
          "（两次链路的结果不得拼接）")
    check(len(alignment.terminals) == expected_alignment["terminals_emitted"],
          f"T40 步骤4：终态数必须为 {expected_alignment['terminals_emitted']}，得到 "
          f"{len(alignment.terminals)}")
    got_hist: dict = {}
    for terminal in alignment.terminals:
        key = (f"{TERMINAL_KIND_TO_TYPE[terminal.terminal_kind]}@"
               f"{terminal.terminal_schema_version}")
        got_hist[key] = got_hist.get(key, 0) + 1
    check(got_hist == expected_alignment["terminal_schema_versions"],
          "T40 步骤4：终态 schema 版本直方图必须为 "
          f"{expected_alignment['terminal_schema_versions']}，得到 {got_hist}")
    snapshot_expected = expected_alignment["evidence_set_snapshot"]
    check(alignment.evidence_snapshot.fingerprint == snapshot_expected["fingerprint"]
          and alignment.evidence_snapshot.evidence_set_version
          == snapshot_expected["evidence_set_version"]
          and alignment.evidence_snapshot.block_count
          == snapshot_expected["block_count"]
          and alignment.evidence_snapshot.status == snapshot_expected["status"]
          and alignment.evidence_snapshot.snapshot_version
          == snapshot_expected["snapshot_version"]
          and alignment.evidence_snapshot.gateway_version
          == snapshot_expected["gateway_version"],
          "T40 步骤4：权威 Evidence 快照的 fingerprint / 集合版本 / 成员数 / status "
          "/ snapshot_version / gateway_version 与信任根期望值逐项相等")
    check(EG.member_identity_sha256(alignment.evidence_snapshot.members)
          == expected_alignment["member_identity_sha256"],
          "T40 步骤4：权威成员规范身份哈希必须等于信任根钉住的 "
          "member_identity_sha256（逐块身份，不只是总数）")

    # -- 5) live 四级：正式 TS3 交接（内部重建 outline 与结构终态）--------------
    ok, handoff = _try(lambda: SB.issue_live_ts3_handoff(
        vlayout, alignment, authority))
    if not check(ok, "T40 步骤5：`issue_live_ts3_handoff` 签发 live TS3→TS4 交接"
                     + ("" if ok else f"：{type(handoff).__name__}: {handoff}")):
        out["error"] = f"issue_live_ts3_handoff 失败：{handoff}"
        return out
    out["handoff"] = handoff
    check(handoff.issuer_scope == "live" and handoff.source_kind == "current_store"
          and handoff.issuer_version == V.VERIFIED_TS3_HANDOFF_VERSION == "vth-1",
          "T40 步骤5：交接必须为 live/current_store/vth-1，得到 "
          f"{handoff.issuer_scope!r}/{handoff.source_kind!r}/"
          f"{handoff.issuer_version!r}")
    check(handoff.document_outline.outline_id == expected_identity["outline_id"]
          and handoff.document_outline.outline_locator
          == expected_identity["outline_locator"]
          and handoff.document_outline.schema_version
          == expected_identity["schema_version"],
          "T40 步骤5：交接内由受信 PDF 字节重建的 outline 身份（id / locator / "
          "schema 版本）与信任根期望值逐项相等")
    check(handoff.layout_capability is vlayout
          and handoff.alignment is alignment
          and handoff.evidence_authority is authority,
          "T40 步骤5：交接绑定的四级对象就是本次链路的同批对象（身份不得拼接）")
    fingerprints = handoff.authority_fingerprints()
    check(sorted(fingerprints) == sorted([
        "structure_provider_authority_fingerprint",
        "evidence_gateway_authority_fingerprint",
        "terminal_provider_authority_fingerprint",
        "policy_provider_authority_fingerprint"])
        and all(isinstance(v, str) and len(v) == 64 for v in fingerprints.values())
        and len(set(fingerprints.values())) == 4,
          "T40 步骤5：四项 provider authority 指纹必须齐备、各自 64 位且互不相同")
    check(len(handoff.evidence_blocks) == snapshot_expected["block_count"],
          f"T40 步骤5：交接携带的全量 Evidence 块数必须为 "
          f"{snapshot_expected['block_count']}，得到 {len(handoff.evidence_blocks)}")
    return out


# ---------------------------------------------------------------------------
# 2. 公开 builder / verifier 正向
# ---------------------------------------------------------------------------

def _check_public_build_and_verify(chain: dict) -> None:
    handoff = chain["handoff"]
    expected_identity = chain["expected_identity"]
    expected_alignment = chain["expected_alignment"]

    stage = STAGE.current_stage()
    ok, snap = _try(lambda: SB.build_span_snapshot(handoff, stage=stage))
    if not check(ok, f"T40 正向：公开 `build_span_snapshot(live_handoff, "
                     f"stage={stage!r})` 通过"
                     + ("" if ok else f"：{type(snap).__name__}: {snap}")):
        chain["error"] = f"build_span_snapshot 失败：{snap}"
        return
    chain["snapshot"] = snap

    conservation = snap.conservation
    check(conservation.conserved is True and conservation.gaps == (),
          "T40 正向：三层守恒必须 `conserved is True` 且零缺口，得到 "
          f"conserved={conservation.conserved} "
          f"gaps={[gap.reason_code for gap in conservation.gaps][:3]}")
    check(snap.alignment_schema_version == V.ALIGN_SCHEMA_VERSION,
          f"T40 正向：快照的 alignment_schema_version 必须为 "
          f"{V.ALIGN_SCHEMA_VERSION}，得到 {snap.alignment_schema_version!r}")
    check(len(snap.spans) > 0
          and all(span.role == "body"
                  and span.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION
                  and span.schema_version == V.SPAN_SCHEMA_VERSION
                  for span in snap.spans),
          "T40 正向：快照内每个新正文 span 必须是 body + 当前 TS4 正文算法版本 "
          f"({V.TS4_BODY_SPAN_BUILDER_VERSION}) + os-4，"
          f"实际 span 数 {len(snap.spans)}")

    inherited = tuple(snap.inherited_unassigned_span_ids)
    outline_unassigned = {span.span_id: span
                          for span in handoff.document_outline.unassigned}
    check(len(inherited) == expected_identity["formal_unassigned_count"],
          f"T40 正向：继承的 TS3 unassigned span 数必须为 "
          f"{expected_identity['formal_unassigned_count']}，得到 {len(inherited)}")
    check(set(inherited) == set(outline_unassigned),
          "T40 正向：继承 ID 必须精确指向真实大纲 `unassigned` 里的对象（不得自造）")
    check(all(span.span_builder_version
              == V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION
              and span.role == "unassigned"
              and span.evidence_set_version
              == V.OUTLINE_UNASSIGNED_EVIDENCE_SET_VERSION
              for span in outline_unassigned.values()),
          "T40 正向：被继承的历史对象必须仍是 sb-1 + role=unassigned + "
          "no-evidence-set（TS3 语义不被 TS4 改写）")
    check(not (set(inherited) & {span.span_id for span in snap.spans}),
          "T40 正向：继承 ID 与 TS4 新 span ID 必须无交集")

    policy = snap.qualification_policy
    if stage == "distribution_only":
        check(policy.stage == "distribution_only"
              and policy.span_confidence_min is None
              and policy.completion_enabled is False
              and policy.set_complete_supported is False,
              "T40 正向：TS4-A 快照必须绑定 distribution_only 策略（阈值 None、"
              f"completion/set_complete 为假），得到 stage={policy.stage!r} "
              f"scm={policy.span_confidence_min!r} "
              f"completion={policy.completion_enabled}")
    else:
        check(policy.stage == "threshold_enabled"
              and policy.span_confidence_min == V.SPAN_CONFIDENCE_MIN
              and policy.completion_enabled is True
              and policy.set_complete_supported is True,
              "T40 正向：TS4-B 快照必须绑定**冻结的** threshold_enabled 策略"
              f"（阈值与 SPAN_CONFIDENCE_MIN 同步），得到 stage={policy.stage!r} "
              f"scm={policy.span_confidence_min!r} vs {V.SPAN_CONFIDENCE_MIN!r} "
              f"completion={policy.completion_enabled}")
        check(policy.policy_fingerprint == SP.resolve_frozen_policy()
              .policy_fingerprint,
              "T40 正向：TS4-B 快照绑定的策略必须就是注册表钉住的冻结策略"
              "（不得由任意阈值临时构造）")

    trusted = snap.trusted_input
    check(trusted.structure[3] == V.OUTLINE_STRUCTURE_PROVIDER_VERSION,
          "T40 正向：封闭输入载荷 structure provider 必须为 osp-1，得到 "
          f"{trusted.structure[3]!r}")
    check(trusted.evidence[3] == V.EVIDENCE_GATEWAY_PROVIDER_VERSION,
          "T40 正向：封闭输入载荷 evidence provider 必须为 egp-1，得到 "
          f"{trusted.evidence[3]!r}")
    check(trusted.qualification[11] == V.QUALIFICATION_POLICY_PROVIDER_VERSION,
          "T40 正向：封闭输入载荷 policy provider 必须为 qpp-1，得到 "
          f"{trusted.qualification[11]!r}")
    handoff_identity = trusted.handoff
    check(handoff_identity[0] == "live" and handoff_identity[1] == "current_store"
          and handoff_identity[3] == V.VERIFIED_TS3_HANDOFF_VERSION
          and handoff_identity[5] == V.VERIFIED_PAGE_LAYOUT_ISSUER_VERSION
          and handoff_identity[7] == V.VERIFIED_ALIGNMENT_ISSUER_VERSION
          and handoff_identity[9] == V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION,
          "T40 正向：封闭输入载荷必须逐位记录本次 live 四级链"
          "（live/current_store + vth-1/vpli-1/vai-1/vea-1），得到 "
          f"{handoff_identity[0:4] + tuple(handoff_identity[i] for i in (5, 7, 9))}")
    check(snap.input_fingerprint == sha256_canonical(trusted.to_dict()),
          "T40 正向：`input_fingerprint` 必须可由封闭输入载荷独立复算（不得自报）")
    check(len(trusted.terminals) == expected_alignment["terminals_emitted"]
          and len({len(item) for item in trusted.terminals}) == 1,
          "T40 正向：封闭输入载荷必须携带全部 "
          f"{expected_alignment['terminals_emitted']} 条终态身份且形状一致")

    ok, verified = _try(lambda: SV.verify_span_snapshot(snap, handoff))
    if not check(ok, "T40 正向：公开 `verify_span_snapshot` 复核通过"
                     + ("" if ok else f"：{type(verified).__name__}: {verified}")):
        return
    chain["verified"] = verified
    check(verified.issuer_scope == "live"
          and verified.source_kind == "current_store"
          and verified.issuer_version == V.VERIFIED_SPAN_SNAPSHOT_VERSION,
          "T40 正向：复核结论的 scope/source_kind/issuer_version 必须回指 live 链，"
          f"得到 {verified.issuer_scope!r}/{verified.source_kind!r}/"
          f"{verified.issuer_version!r}")
    check(verified.snapshot.snapshot_id == snap.snapshot_id
          and verified.snapshot.content_fingerprint == snap.content_fingerprint
          and verified.handoff is handoff,
          "T40 正向：复核结论必须绑定被复核快照与当次受信交接本身")
    check(isinstance(verified.verification_fingerprint, str)
          and len(verified.verification_fingerprint) == 64,
          "T40 正向：复核指纹必须为 64 位十六进制串")
    note("T40 正向：`verify_span_snapshot` 会**独立重建**整条产物再做 canonical "
         "全等比较，因此这一次通过同时构成『可重复』证据；对齐为现场 live 对齐，"
         "**不是** TS4-A 真实验收的冻结 rows 还原（后者由 "
         "`evaluation/run_tree_span_acceptance.py` 以 vaip-1 单独完成）。"
         "本测试是 live 可达性证明，不是 TS4-A 真实验收。")

    ok, structure_report = _try(lambda: SV.verify_outline_structure_snapshot(handoff))
    check(ok and isinstance(structure_report, dict)
          and structure_report.get("ok") is True,
          "T40 正向：结构终态可由受信版式独立重建并与交接绑定值 canonical 全等"
          + ("" if ok else f"：{type(structure_report).__name__}: {structure_report}"))

    eligible = [span for span in snap.spans
                if SV.is_completion_eligible(verified, span.span_id)]
    if stage == "distribution_only":
        check(not eligible,
              "T40 正向：TS4-A 下任何 span 都不得取得完成资格，越权 span "
              f"{[s.span_id for s in eligible][:3]}")
    else:
        # 资格是**逐 span 的必要条件**，不是"阈值一开就完成"：
        # 取得资格的 span 必须自身达标（正式正文材料 + 置信度不低于冻结阈值）。
        below = [s.span_id for s in eligible
                 if s.confidence < policy.span_confidence_min]
        check(not below,
              f"T40 正向：TS4-B 下置信度低于 {policy.span_confidence_min} 的 span "
              f"不得取得完成资格，越权 span {below[:3]}")
        informal = [s.span_id for s in eligible
                    if s.role != "body" or s.is_fallback or s.is_cross_heading
                    or s.unassigned_reason is not None]
        check(not informal,
              "T40 正向：TS4-B 下 fallback / 跨标题 / 未归属 span 不得因阈值取得"
              f"完成资格，越权 span {informal[:3]}")
        check(all(SV.is_completion_eligible(verified, s.span_id) is False
                  for s in snap.spans
                  if s.confidence < policy.span_confidence_min),
              "T40 正向：TS4-B 下低于阈值的 span 必须一律不合格（阈值是必要条件）")
        note("T40 正向：TS4-B 的完成资格是**逐 span 必要条件**；本节只证明"
             "『阈值已启用且判定按阈值逐条执行』，本节**不**证明任何 topic / aspect "
             "的 set_complete——后者仍需覆盖闭合、可引用区间与 completion rules，"
             "不在本测试范围内。")


# ---------------------------------------------------------------------------
# 3. 负向：跨域 / 仿造 / 复制 / 阶段门
# ---------------------------------------------------------------------------

def _check_pinned_domain_isolation(chain: dict) -> None:
    """`pinned_acceptance` 域：既能自证可用，又进不了任何需要 live 的公开入口。"""
    ok, pinned = _try(_pinned_fixture_handoff)
    if not check(ok, "T40 负向：版本化夹具的 `pinned_acceptance` 交接可签发"
                     + ("" if ok else f"：{type(pinned).__name__}: {pinned}")):
        return
    check(pinned.issuer_scope == "pinned_acceptance"
          and pinned.source_kind == EG.FIXTURE_SOURCE_KIND
          and pinned.issuer_version == V.FIXTURE_TS3_HANDOFF_VERSION == "vthf-1",
          "T40 负向：夹具交接必须为 pinned_acceptance/versioned_fixture/vthf-1，"
          f"得到 {pinned.issuer_scope!r}/{pinned.source_kind!r}/"
          f"{pinned.issuer_version!r}")
    check(SS.capability_scope(pinned) == "pinned_acceptance",
          "T40 负向：夹具交接在签发登记表里的域必须是 pinned_acceptance")

    # 它在**自己的**域内是可用能力（否则"被拒"就只是因为对象坏了，不构成隔离证明）。
    ok, pinned_snap = _try(lambda: SB._build_from_pinned_handoff(
        pinned, stage=STAGE.current_stage()))
    check(ok, "T40 负向：该夹具交接在 pinned_acceptance 域内可构建（隔离是域问题，"
              "不是对象损坏）"
              + ("" if ok else f"：{type(pinned_snap).__name__}: {pinned_snap}"))

    raises(lambda: SB.build_span_snapshot(pinned, stage="distribution_only"),
           CapabilityError, "签发域为 'pinned_acceptance'",
           "T40 负向：pinned_acceptance 交接传**公开 live builder** 必须被拒")
    raises(lambda: SB._testing_build_span_snapshot(pinned,
                                                   stage="distribution_only"),
           CapabilityError, "不在允许集合 ('testing',)",
           "T40 负向：pinned_acceptance 交接传 testing wrapper 也必须被拒")
    if "snapshot" in chain:
        raises(lambda: SV.verify_span_snapshot(chain["snapshot"], pinned),
               SchemaValidationError, "",
               "T40 负向：用 pinned_acceptance 交接复核 **live** 快照必须被拒"
               "（交接与快照不同批）")

    # 反向：live 交接进不了 pinned / testing 专用入口。
    live_handoff = chain["handoff"]
    raises(lambda: SB._build_from_pinned_handoff(live_handoff,
                                                 stage="distribution_only"),
           CapabilityError, "不在允许集合 ('pinned_acceptance',)",
           "T40 负向：live 交接传 `_build_from_pinned_handoff` 必须被拒（双向隔离）")
    raises(lambda: SB._testing_build_span_snapshot(live_handoff,
                                                   stage="distribution_only"),
           CapabilityError, "不在允许集合 ('testing',)",
           "T40 负向：live 交接传 testing 入口必须被拒（testing 域不可由 live 冒充）")

    # pinned 上游不得进入需要 live 的公开入口（对齐 / 交接两级）。
    raises(lambda: AL.align_evidence_set_verified(pinned.layout_capability,
                                                  chain["authority"]),
           CapabilityError, "签发域为 'pinned_acceptance'",
           "T40 负向：pinned 版式能力传**公开 live 对齐入口**必须被拒")
    raises(lambda: SB.issue_live_ts3_handoff(pinned.layout_capability,
                                             chain["alignment"],
                                             chain["authority"]),
           CapabilityError, "签发域为 'pinned_acceptance'",
           "T40 负向：pinned 版式能力传**公开 live 交接入口**必须被拒")

    note("T40 负向：`testing` 域在本批次**不可构造**（生产面无 testing-scope 的 "
         "layout/alignment/authority issuer），因此以 pinned↔live 双向拒绝 + "
         "live→testing 入口拒绝覆盖同一道 scope 门；不自行签发 testing capability "
         "冒充该域（那会污染同一 id(obj) 的登记项，等于制造第二套签发面）")


def _check_forged_and_copied(chain: dict) -> None:
    live_handoff = chain["handoff"]
    snap = chain["snapshot"]

    # -- 复制 / 序列化：运行时能力不可 copy / deepcopy / pickle / 序列化 ---------
    for target, label in ((live_handoff, "live 交接"),
                          (chain["authority"], "Evidence authority"),
                          (chain["vlayout"], "受信版式"),
                          (chain["alignment"], "受信对齐"),
                          (chain["verified"], "复核结论")):
        raises(lambda t=target: copy.copy(t), SchemaValidationError, "不可 copy",
               f"T40 负向：{label} 不可 copy（副本未在签发登记表中）")
        raises(lambda t=target: copy.deepcopy(t), SchemaValidationError,
               "不可 deepcopy",
               f"T40 负向：{label} 不可 deepcopy")
        raises(lambda t=target: pickle.dumps(t), SchemaValidationError, "不可 pickle",
               f"T40 负向：{label} 不可 pickle")
        raises(lambda t=target: t.to_dict(), SchemaValidationError, "不得序列化",
               f"T40 负向：{label} 不得序列化（`to_dict` 必须拒绝）")

    # -- 字段仿造（未登记）-----------------------------------------------------
    fake = _fake_handoff(live_handoff)
    check(SS.capability_scope(fake) is None,
          "T40 负向：字段仿造的交接在签发登记表里**没有**资格（capability_scope None）")
    raises(lambda: SB.build_span_snapshot(fake, stage="distribution_only"),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "T40 负向：`object.__new__` + 字段仿造的交接传公开 builder 必须被拒")
    raises(lambda: SV.verify_span_snapshot(snap, fake),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "T40 负向：字段仿造的交接传公开 verifier 必须被拒")

    # -- 字段仿造（已登记 live，但结构终态被篡改且"自洽"）------------------------
    first = live_handoff.structure_snapshot.line_states[0]
    forged_structure = _structure_without_line(
        live_handoff, (first.page_number, first.line_index))
    check(forged_structure.structure_snapshot_id
          != live_handoff.structure_snapshot.structure_snapshot_id,
          "T40 负向：伪造结构快照的 ID 与真实值不同（它确实是一份不同的对象）")
    registered_fake = _fake_handoff(live_handoff,
                                    structure_snapshot=forged_structure,
                                    registered=True)
    check(SS.capability_scope(registered_fake) == "live",
          "T40 负向：构造出的伪造交接确实被登记为 live（最坏情形）")
    raises(lambda: SB.build_span_snapshot(registered_fake,
                                          stage="distribution_only"),
           SpanBuildError, "canonical 不相等",
           "T40 负向：**已登记 live 但结构终态被篡改**的交接仍必须由独立重建拒绝"
           "（同步重算全部 ID 与 hash 也过不了）")
    raises(lambda: SV.verify_span_snapshot(snap, registered_fake),
           SchemaValidationError, "",
           "T40 负向：同一伪造交接传公开 verifier 同样必须被拒")

    # -- 阶段门：只能新建**当前阶段**的快照（跨阶段一律 fail-closed）-----------
    other = ("threshold_enabled" if STAGE.current_stage() == "distribution_only"
             else "distribution_only")
    raises(lambda: SB.build_span_snapshot(live_handoff, stage=other),
           SpanBuildError, STAGE.current_stage(),
           f"T40 负向：当前阶段为 {STAGE.current_stage()!r} 时不得新建 {other!r} "
           f"快照（A/B 门 fail-closed，不得静默跨阶段）")


# ---------------------------------------------------------------------------
# 4. spy 唯一性
# ---------------------------------------------------------------------------

def _check_uniqueness(chain: dict, window: _ChainWindow, service) -> None:
    calls = window.calls
    expected = (
        (service, "_prepare_stores"),
        (LB, "build_verified_page_layout"),
        (AL, "align_evidence_set_verified"),
        (SB, "issue_live_ts3_handoff"),
        (SB, "build_span_snapshot"),
        (SV, "verify_span_snapshot"),
    )
    for module, name in expected:
        got = calls.calls(module, name)
        check(got == 1,
              f"T40 spy：正式入口 `{module.__name__}.{name}` 在 live 链内必须恰好被调用 "
              f"1 次，实际 {got} 次")
    for dotted, name in window.FORBIDDEN_ENTRIES:
        module = importlib.import_module(dotted)
        got = calls.calls(module, name)
        check(got == 0,
              f"T40 spy：旁路 / 历史入口 `{dotted}.{name}` 在 live 链内必须 "
              f"0 次调用，实际 {got} 次")
    check(not calls.unwatched,
          "T40 spy：所有待观察入口都必须真实装上计次（否则唯一性证明不成立），"
          f"未装上 {calls.unwatched}")

    check(calls.calls(SB, "_build_core") == 1,
          "T40 spy：三个 scope 共用的私有算法核 `_build_core` 恰好被公开 builder 走 "
          f"1 次（不存在第二套构建实现），实际 {calls.calls(SB, '_build_core')} 次")
    check(calls.calls(SB, "_rebuild_outline_structure") >= 2,
          "T40 spy：结构终态由受信版式**独立重建**（签发与复核各至少一次），实际 "
          f"{calls.calls(SB, '_rebuild_outline_structure')} 次")
    check(calls.calls(SV, "verify_outline_structure_snapshot") >= 1
          and calls.calls(SV, "is_completion_eligible") >= 1,
          "T40 spy：复核步骤（结构终态复算、完成资格判定）确实被走到")

    # capability 签发：恰好五级、恰好一次、全部 live。
    expect_caps = {
        ("VerifiedCurrentEvidenceAuthority", "live"): 1,
        ("VerifiedPageLayout", "live"): 1,
        ("VerifiedEvidenceSetAlignment", "live"): 1,
        ("VerifiedTS3Handoff", "live"): 1,
        ("VerifiedSpanSnapshot", "live"): 1,
    }
    check(window.caps.issued == expect_caps,
          "T40 spy：live 链内必须恰好签发五级 capability 各一次且全部为 live 域，"
          f"实际 {window.caps.issued}")
    check(sum(window.caps.issued.values()) == 5,
          "T40 spy：live 链内 capability 签发总次数必须为 5，实际 "
          f"{sum(window.caps.issued.values())}")

    # 第二套 runtime：导入守卫 + 具名入口计次（两者合起来完备）。
    forbidden_imports = sorted(
        name for name in window.imports.names
        if name.startswith(SECOND_RUNTIME_PREFIXES))
    check(not forbidden_imports,
          "T40 spy：live 链窗口内不得新导入任何第二套 runtime 模块"
          f"（harness/tools/routing/retrieval/llm），实际 {forbidden_imports[:5]}")
    second_calls = {key: n for key, n in window.calls.counts.items()
                    if key.split(".")[0] in ("harness", "tools", "routing",
                                             "retrieval")}
    check(not second_calls,
          f"T40 spy：第二套 runtime 入口在 live 链内必须 0 次调用，实际 {second_calls}")
    note("T40 spy：链窗口内新增导入的全名（诊断）= "
         f"{sorted(set(window.imports.names))[:12]}；"
         f"已装计次的第二套 runtime 入口 = {window.second_runtime_watched or '无'}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _guard(label: str, fn, *args) -> None:
    """跑一个检查段；段内意外异常记为一条 FAIL（保留其余检查结果，不整体崩）。"""
    try:
        fn(*args)
    except Exception as error:  # noqa: BLE001
        import traceback
        check(False, f"T40 {label} 段内出现未预期异常（这是测试自身或生产缺陷，"
                     f"必须显式报告）：{type(error).__name__}: {error}；"
                     f"{traceback.format_exc().splitlines()[-3:]}")


def main() -> dict:
    start = time.time()
    before = _db_identity()
    results_before = _results_listing()
    _check_frozen_db_identity(before)

    ok, trust = _try(lambda: _read_json(TRUST_ROOTS_PATH))
    if not check(ok, f"T40 信任根可读（{TRUST_ROOTS_PATH.relative_to(REPO)}）"
                     + ("" if ok else f"：{trust}")):
        return _results
    check(LIVE_DOCUMENT_KEY in trust.get("documents", {}),
          f"T40 信任根必须钉住 live 正向文档 {LIVE_DOCUMENT_KEY!r}")

    # 现有 service 必须在**窗口之前**导入：否则"窗口内新增导入"会把它的依赖树
    # 也算进第二套 runtime 的判定里。
    service_ok, service = _try(lambda: importlib.import_module("sections.service"))
    estore = importlib.import_module("evidence.store")
    fstore = importlib.import_module("financial_v2.store")
    saved_db_paths = [("evidence.store", estore, getattr(estore, "_db_path", None)),
                      ("financial_v2.store", fstore,
                       getattr(fstore, "_db_path", None))]

    window = _ChainWindow()
    chain: dict = {}
    try:
        window.install()
        chain_start = time.time()
        if not check(service_ok, f"T40 现有 service 模块必须可导入，实际 {service}"):
            return _results
        holder: list = []
        _guard("live 现场链", lambda: holder.append(
            _run_live_chain(trust, window, service)))
        chain = holder[0] if holder else {}
        if "error" not in chain and "handoff" in chain:
            _guard("正向公开 builder/verifier 链",
                   _check_public_build_and_verify, chain)
        _results["timings"]["live_chain"] = round(time.time() - chain_start, 2)
    finally:
        window.restore()
        for _label, module, value in saved_db_paths:
            module._db_path = value

    for label, module, value in saved_db_paths:
        check(getattr(module, "_db_path", None) == value,
              f"T40 卫生：测试结束后 `{label}._db_path` 必须还原为测试前的值"
              "（同进程运行不得污染其它模块）")

    if "error" not in chain and "handoff" in chain:
        _guard("spy 唯一性", _check_uniqueness, chain, window, service)

    # 负向：跨域 / 仿造 / 复制 / 阶段门（在 spy 窗口之外，避免污染正向计数）。
    if "error" not in chain and "handoff" in chain:
        _guard("跨域拒绝", _check_pinned_domain_isolation, chain)
        if "snapshot" in chain and "verified" in chain:
            _guard("仿造/复制拒绝", _check_forged_and_copied, chain)

    # 有界、只读、无产物。
    after = _db_identity()
    for name in ("size", "mtime_ns", "sha256"):
        check(after[name] == before[name],
              f"T40 只读：`data/evidence.db` 的 {name} 运行前后必须逐字段相等："
              f"{before[name]!r} -> {after[name]!r}")
    check(after == before,
          "T40 只读：`data/evidence.db` 三项身份运行前后整体相等（半个字节都不得改）")
    check(_results_listing() == results_before,
          "T40 卫生：本测试不得在 `evaluation/results/**` 下创建任何目录或文件；"
          f"前后清单差异 {sorted(set(_results_listing()) ^ set(results_before))[:5]}")
    elapsed = round(time.time() - start, 2)
    _results["timings"]["total"] = elapsed
    check(elapsed < CHAIN_TIME_BOUND_SECONDS,
          f"T40 有界：整条链必须在 {CHAIN_TIME_BOUND_SECONDS}s 内完成，实际 {elapsed}s")
    note(f"T40 有界/离线：全程无网络、无 LLM、无子进程、无临时文件；总耗时 {elapsed}s；"
         f"live 链耗时 {_results['timings'].get('live_chain')}s")
    return _results


if __name__ == "__main__":
    result = main()
    for line in result["details"]:
        if not line.startswith("PASS "):
            print(line)
    print(json.dumps({"passed": result["passed"], "failed": result["failed"],
                      "skipped": result["skipped"],
                      "timings": result["timings"]},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["failed"] == 0 else 1)

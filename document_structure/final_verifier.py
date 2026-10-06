# -*- coding: utf-8 -*-
"""TS5 §19.11.1：final material 的**独立复核层**（`vfmi-1`）。

本模块是 TS5 终端快照的**唯一**签发者，也是它唯一的资格来源。

## 独立性（硬约束）

它**不** `import`、不调用、不委托 `final_material_builder`（唯一 public builder）
或 `table_builder`（高层 assembly），也不调用它们的任何包装 / 私有入口。允许复用的
只有低层叶子原语：`canonical`、`versions`、`span_schema` 的封闭词表与能力登记、
`table_schema` 的 wire 类型、封闭词表、身份载荷与 DAG 断言、`table_geometry` 的
几何抽取、`table_classification` 的只读资产读取。

`verifier_independence_problems()` 用 AST 解析本文件自身，逐项断言上述禁止项；测试
还会用 monkeypatch 把两个 builder 入口换成"一调用就抛"的桩，要求复核仍然成功，
再用篡改过的快照要求它**必须**拒绝。这两件事一起才构成"独立"的证明：前者证明它
没有走那条路，后者证明它真的在算。

## 复核口径（重算什么、不重算什么）

从**经验证的 TS4 根**出发重新导出全部 TS5 final 对象并逐项比较：

- 上游依赖束指纹（逐字段从 roots 读数重算，字段表必须与
  `table_schema.UPSTREAM_DEPENDENCY_FIELDS` 精确同集）；
- member 集合的**精确等集**（binding ↔ TS4 components，decision ↔ 表 provisional
  disposition，coverage ↔ tables，synopsis ↔ outline nodes）；
- 每条 decision / binding / coverage / relation / gap / conservation / synopsis 的
  每一项派生值（含 §19.9.1 的终态真值表本身，而不只是它的理由串）；
- `sbf-1` final span 的成员、文本、区间、12 因子与阈值重算；
- 四层守恒的计数与合计；
- 身份 DAG 无环与"终端不得被回指"。

**不重算**表格自身的候选生成与几何装配——那属于 TS5 builder 的算法，且是
`TableObjectV4.__post_init__` 与 TS4 根交叉核对（owner 解析、cell 来源落到真实
LayoutSpan、`source_refs` 并集闭合、逐 cell 覆盖）的职责。本模块因此不宣称"重建了
整条 TS5 产物"，只宣称"独立重建了全部 final 对象并核对了表格与 TS4 根的一致性"。

另外，整份快照会过一次 `from_dict(to_dict())`：`object.__new__` 之类的旁路能跳过
`__post_init__`，重跑构造校验能把它们挡回去。
"""
from __future__ import annotations

import ast
import hashlib
import os
from typing import Any, Callable, Sequence

from . import span_policy as SP
from . import span_schema as SS
from . import table_geometry as TG
from . import table_schema as TS
from . import versions as V
from .canonical import SchemaValidationError, canonical_json
from .normalization import tight
from .schema import quantize
from .table_classification import load_table_profile_bundle

__all__ = [
    "FinalVerificationError",
    "FinalMaterialBlockedError",
    "FinalMaterialConservationError",
    "VerifiedFinalMaterialStructureSnapshot",
    "verify_final_material_snapshot",
    "assert_final_material_capability",
    "blocking_gap_histogram",
    "final_material_problems",
    "verifier_independence_problems",
    "self_check",
]

#: 本模块**禁止** import 或调用的生产模块（§19.11.1）。
FORBIDDEN_DELEGATION_MODULES: tuple[str, ...] = (
    "table_builder", "final_material_builder",
)

#: 本模块**禁止**引用的符号名（公开构建入口与其包装）。
FORBIDDEN_DELEGATION_SYMBOLS: tuple[str, ...] = (
    "build_final_material_snapshot", "build_tables", "build_table",
    "plan_decisions", "build_adornment_relations", "_assemble",
)

#: 动态引入入口名：**字符串**形式的模块名只有作为这些调用的实参才算"一扇门"。
#: 本模块自己的常量表里也出现同样的模块名字符串，因此不能对裸字符串做判断——
#: 必须看它是不是真的被喂给动态 import。
DYNAMIC_IMPORT_ENTRIES: tuple[str, ...] = (
    "import_module", "__import__", "load_module", "module_from_spec",
    "spec_from_file_location", "reload",
)

#: 复核域版本（`vfmi-1`）。
VERIFIER_VERSION = "vfmi-1"

#: 签发的 capability 种类。
CAPABILITY_KIND = "VerifiedFinalMaterialStructureSnapshot"

#: 复核的**最长**问题清单（超出即截断；`truncated` 会置位）。
MAX_PROBLEMS = 40


class FinalVerificationError(SchemaValidationError):
    """复核失败（类型不符 / 根不一致 / 重算不等 / 越权构造）。"""


class FinalMaterialConservationError(FinalVerificationError):
    """最终材料守恒**资格不成立**：不签发 final capability（fail-closed）。

    与 `FinalMaterialBlockedError` 的区别是"拒绝的理由"：后者是上游冻结范围与已验证
    几何互相矛盾这类**阻断性结构缺口**；本类是 TS5 自己算出的守恒不合格——存在未解释
    的残余字符或层内问题码。两者都不签发，但审计时不得互相冒充。
    """


class FinalMaterialBlockedError(FinalVerificationError):
    """该文档存在**阻断性**结构缺口：`upstream_table_scope_miss` / `root_identity_mismatch`。

    §19.3.2 T9 与 §19.9 的逐 landing 规则（`body_span`、`body_empty`、`heading_node`）
    四处都写死了同一句"阻断该文档 final capability"，且 §19.5.3 明确这类状态
    **不是可保留 P2**："必须先由上游 successor 明确修复/重建后才可继续"。因此本类
    缺口存在时**不签发** final capability——这是拒绝签发，不是把问题记在已签发的
    capability 上；后者会让"忽略一个 flag 就照常消费"成为默认路径。

    与它相对，`unsupported_table_structure` / `unresolved_geometry` /
    `visual_object_not_table` / `ambiguous_continuation` / `provenance_incomplete`
    是 §19.1.2 所说的"真实负面终态"：**不**阻断签发，作为诚实缺口随文档保留。
    """

    def __init__(self, message: str, *, blocking_kinds: dict) -> None:
        self.blocking_kinds = dict(blocking_kinds)
        super().__init__(message)


# ---------------------------------------------------------------------------
# 0. 独立性自证（AST）
# ---------------------------------------------------------------------------

def _dotted_matches(value: str, modules: tuple[str, ...]) -> bool:
    """`value` 是否就是这些模块之一（允许绝对点分形式 `pkg.sub.mod`）。"""
    return value in modules or any(value.endswith("." + m) for m in modules)


def _call_strings(call: ast.Call) -> list[str]:
    """一个调用实参里出现的字符串常量（穿透 `name=` 关键字与折叠拼接）。"""
    out: list[str] = []
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.append(arg.value)
    return out


def _called_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def verifier_independence_problems(source: str | None = None) -> tuple[str, ...]:
    """解析本文件（或给定源码），返回违反独立性约束的问题清单。

    这是**声明级**证明：它回答"复核路径里有没有一扇通向 builder 的门"。它与运行期
    的 monkeypatch 反例互补——前者挡"写了但没走"，后者挡"绕过去走了"。

    四条通道都要堵：

    1. `import a.b.table_builder`（绝对 import）；
    2. `from . import table_builder` / `from x import table_builder`（相对 import、
       以及把模块当符号 import——`ImportFrom.module` 在相对形式下是 `None`，只看
       `module` 会漏掉）；
    3. `from anywhere import build_tables|_assemble|...` 以及属性 / 裸名引用
       （`.build_tables` / `build_tables`）；
    4. `importlib.import_module("...table_builder")` 这类**字符串**动态引入。第 4 条
       必须只看"字符串是否被喂给动态 import 入口"：本模块自己的常量表里也写着同样的
       模块名字符串，对裸字符串判违规会让复核器自己报自己。
    """
    if source is None:
        with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
            source = fh.read()
    problems: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in FORBIDDEN_DELEGATION_MODULES:
                    problems.append(f"不得 import {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            if _dotted_matches(node.module or "", FORBIDDEN_DELEGATION_MODULES):
                problems.append(f"不得 from {node.module!r} import")
            for alias in node.names:
                # 相对形式 `from . import table_builder`：模块名落在 alias 上。
                if alias.name.rsplit(".", 1)[-1] in FORBIDDEN_DELEGATION_MODULES:
                    problems.append(f"不得 from {node.module!r} import {alias.name!r}")
                if alias.name in FORBIDDEN_DELEGATION_SYMBOLS:
                    problems.append(f"不得 import 构建入口 {alias.name!r}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_DELEGATION_SYMBOLS:
                problems.append(f"不得引用构建入口属性 .{node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_DELEGATION_SYMBOLS:
                problems.append(f"不得引用构建入口名 {node.id!r}")
        elif isinstance(node, ast.Call):
            if _called_name(node) in DYNAMIC_IMPORT_ENTRIES:
                for value in _call_strings(node):
                    if _dotted_matches(value, FORBIDDEN_DELEGATION_MODULES):
                        problems.append(f"不得以字符串动态引入 {value!r}")
    return tuple(sorted(set(problems)))


# ---------------------------------------------------------------------------
# 1. 运行时能力对象
# ---------------------------------------------------------------------------

class VerifiedFinalMaterialStructureSnapshot:
    """**已复核**的 TS5 终端能力（运行时对象，不可序列化、不可 copy / pickle）。

    它同时持有被复核的快照与当次使用的 TS4 能力，并记录签发域——因此"拿 `testing`
    域的结果冒充验收 / 生产"在报告层直接可见（`issuer_scope`）。
    """

    __slots__ = ("_snapshot", "_ts4", "_scope", "_source_kind", "_issuer_version",
                 "_verification_fingerprint", "_problems", "__weakref__")

    def __init__(self, *, snapshot, ts4, scope, source_kind, issuer_version,
                 verification_fingerprint, problems) -> None:
        self._snapshot = snapshot
        self._ts4 = ts4
        self._scope = scope
        self._source_kind = source_kind
        self._issuer_version = issuer_version
        self._verification_fingerprint = verification_fingerprint
        self._problems = tuple(problems)

    @property
    def snapshot(self) -> TS.FinalMaterialStructureSnapshot:
        return self._snapshot

    @property
    def verified_span(self):
        """本次复核所用的 TS4 能力（复核链路的根，供审计追溯）。"""
        return self._ts4

    @property
    def issuer_scope(self) -> str:
        return self._scope

    @property
    def source_kind(self) -> str:
        return self._source_kind

    @property
    def issuer_version(self) -> str:
        return self._issuer_version

    @property
    def verification_fingerprint(self) -> str:
        return self._verification_fingerprint

    @property
    def recheck_problems(self) -> tuple[str, ...]:
        """复核期记录到的**非致命**问题（例如诚实缺口），不构成放行依据。"""
        return self._problems

    def identity(self) -> dict:
        return {
            "issuer_scope": self._scope,
            "source_kind": self._source_kind,
            "issuer_version": self._issuer_version,
            "snapshot_id": self._snapshot.snapshot_id,
            "snapshot_content_fingerprint": self._snapshot.content_fingerprint,
            "verified_span_snapshot_id":
                self._snapshot.verified_span_snapshot_id,
            "verification_fingerprint": self._verification_fingerprint,
        }

    def is_document_blocked(self) -> bool:
        """是否存在**阻断该文档**的结构缺口（§19.4.3）。

        恒为 `False`：带阻断性缺口的快照在 `_refuse_if_blocked` 处就已被拒绝签发，
        能走到这里的 wrapper 必然没有阻断条目。保留该查询是为了让这个不变量在
        消费侧**可断言**（`assert not verified.is_document_blocked()`），而不是靠
        "我们记得不会发生"。
        """
        return self._snapshot.gaps.blocking_entry_count > 0

    def to_dict(self) -> dict:
        raise FinalVerificationError(
            "VerifiedFinalMaterialStructureSnapshot 是运行时能力，不得序列化"
            "（fail-closed）")

    def __copy__(self):
        raise FinalVerificationError(
            "VerifiedFinalMaterialStructureSnapshot 不可 copy")

    def __deepcopy__(self, memo):
        raise FinalVerificationError(
            "VerifiedFinalMaterialStructureSnapshot 不可 deepcopy")

    def __reduce__(self):
        raise FinalVerificationError(
            "VerifiedFinalMaterialStructureSnapshot 不可 pickle")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<VerifiedFinalMaterialStructureSnapshot scope={self._scope!r} "
                f"{self._snapshot.snapshot_id} "
                f"fp={self._verification_fingerprint[:12]}…>")


# ---------------------------------------------------------------------------
# 2. 问题累加器
# ---------------------------------------------------------------------------

class _Problems:
    """收集**全部**问题后一次性抛出（复核报告要能说清每一处，而不是只报第一处）。"""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, problem: str) -> None:
        self.items.append(problem)

    def fail(self, problem: str) -> None:
        self.items.append(problem)
        self.raise_if_any()

    def raise_if_any(self, prefix: str = "") -> None:
        if self.items:
            head = "; ".join(self.items[:MAX_PROBLEMS])
            tail = ("" if len(self.items) <= MAX_PROBLEMS
                    else f"（另有 {len(self.items) - MAX_PROBLEMS} 项）")
            raise FinalVerificationError(
                f"{prefix}独立复核未通过（{len(self.items)} 项）：{head}{tail}")


def _first_difference(a: Any, b: Any, path: str) -> str | None:
    """返回第一处差异的可读路径；完全相等返回 `None`。"""
    if type(a) is not type(b):
        return f"{path}: 类型不同 {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        if set(a) != set(b):
            only_a = sorted(set(a) - set(b))
            only_b = sorted(set(b) - set(a))
            return f"{path}: 键集不同（重算多 {only_a} / 快照多 {only_b}）"
        for key in a:
            found = _first_difference(a[key], b[key], f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: 长度不同 {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = _first_difference(x, y, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    if a != b:
        return f"{path}: {a!r} != {b!r}"
    return None


# ---------------------------------------------------------------------------
# 3. 根读数（只从**经验证的**交接取，绝不采信快照里的副本）
# ---------------------------------------------------------------------------

class _Roots:
    """一次复核所用的全部根读数；每个字段都来自验证链，不来自待复核快照。"""

    __slots__ = ("ts4", "scope", "snapshot", "handoff", "page_layout", "outline",
                 "structure", "evidence_snapshot", "evidence_blocks", "terminals",
                 "alignment", "policy", "profiles", "geometry", "components",
                 "dispositions", "spans", "blocks_by_id", "block_order",
                 "components_by_id")

    def __init__(self, ts4: Any, profiles: Any, geometry: Any) -> None:
        handoff = ts4.handoff
        self.ts4 = ts4
        self.scope = ts4.issuer_scope
        self.snapshot = ts4.snapshot
        self.handoff = handoff
        self.page_layout = handoff.page_layout
        self.outline = handoff.document_outline
        self.structure = handoff.structure_snapshot
        self.evidence_snapshot = handoff.evidence_snapshot
        self.evidence_blocks = tuple(handoff.evidence_blocks)
        self.terminals = tuple(handoff.alignment.terminals)
        self.alignment = handoff.alignment.alignment
        self.policy = handoff.qualification_policy
        self.profiles = profiles
        self.geometry = geometry
        self.components = tuple(self.snapshot.components)
        self.dispositions = tuple(self.snapshot.dispositions)
        self.spans = tuple(self.snapshot.spans)
        self.blocks_by_id = {b.evidence_block_id: b for b in self.evidence_blocks}
        self.block_order = {b.evidence_block_id: (b.page_number, b.block_index)
                            for b in self.evidence_blocks}
        self.components_by_id: dict = {}
        for c in self.components:
            self.components_by_id.setdefault(c.component_id, c)

    def block_text(self, block_id: str) -> str | None:
        block = self.blocks_by_id.get(block_id)
        if block is None:
            return None
        text = getattr(block, "text", None)
        return tight(text) if isinstance(text, str) else None

    def table_dispositions(self) -> tuple:
        return tuple(d for d in self.dispositions
                     if d.range_kind in TS.TABLE_RANGE_KINDS)

    def regular_dispositions_by_span(self) -> dict:
        out: dict = {}
        for d in self.dispositions:
            if d.range_kind == "regular" and d.span_id is not None:
                out.setdefault(d.span_id, []).append(d)
        return out


#: 上游依赖束的**字段 → 从 roots 读数**的取值表。键集必须与
#: `table_schema.UPSTREAM_DEPENDENCY_FIELDS` 精确相同（`self_check` 与测试强制）。
UPSTREAM_FIELD_SOURCES: dict[str, Callable[[_Roots], Any]] = {
    "document_id": lambda r: r.page_layout.document_id,
    "document_version": lambda r: r.page_layout.document_version,
    "evidence_set_version": lambda r: r.evidence_snapshot.evidence_set_version,
    "page_layout_id": lambda r: r.page_layout.page_layout_id,
    "outline_id": lambda r: r.outline.outline_id,
    "alignment_id": lambda r: _alignment_id_of(r.terminals),
    "verified_span_snapshot_id": lambda r: r.snapshot.snapshot_id,
    "verified_span_input_fingerprint": lambda r: r.snapshot.input_fingerprint,
    "verified_span_content_fingerprint": lambda r: r.snapshot.content_fingerprint,
    "verified_span_verification_fingerprint":
        lambda r: r.ts4.verification_fingerprint,
    "page_layout_schema_version": lambda r: r.page_layout.schema_version,
    "outline_schema_version": lambda r: r.outline.schema_version,
    "span_schema_version": lambda r: V.SPAN_SCHEMA_VERSION,
    "span_build_snapshot_schema_version":
        lambda r: r.snapshot.schema_version,
    "span_builder_version": lambda r: r.snapshot.span_builder_version,
    "handoff_identity": lambda r: r.handoff.handoff_identity,
    "table_schema_version": lambda r: V.TABLE_SCHEMA_VERSION,
    "table_builder_version": lambda r: V.TABLE_BUILDER_VERSION,
    "table_cell_schema_version": lambda r: V.TABLE_CELL_SCHEMA_VERSION,
    "final_span_schema_version": lambda r: V.FINAL_SPAN_SCHEMA_VERSION,
    "ts5_final_span_builder_version": lambda r: V.TS5_FINAL_SPAN_BUILDER_VERSION,
    "table_relation_schema_version": lambda r: V.TABLE_RELATION_SCHEMA_VERSION,
    "table_relation_builder_version":
        lambda r: V.TABLE_RELATION_BUILDER_VERSION,
    "table_range_decision_schema_version":
        lambda r: V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
    "final_component_binding_schema_version":
        lambda r: V.FINAL_COMPONENT_BINDING_SCHEMA_VERSION,
    "table_citable_coverage_schema_version":
        lambda r: V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION,
    "table_structure_gap_schema_version":
        lambda r: V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION,
    "final_material_conservation_schema_version":
        lambda r: V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION,
    "final_material_structure_schema_version":
        lambda r: V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION,
    "table_geometry_version": lambda r: r.geometry.settings.geometry_version,
    "geometry_settings_fingerprint":
        lambda r: r.geometry.settings.settings_fingerprint,
    "table_classification_profile_version":
        lambda r: r.profiles.classification.profile_version,
    "classification_profile_file_fingerprint":
        lambda r: r.profiles.classification_file_fingerprint(),
    "classification_profile_content_fingerprint":
        lambda r: r.profiles.classification_content_fingerprint(),
    "table_cell_block_profile_version":
        lambda r: r.profiles.cell_block.profile_version,
    "cell_block_profile_file_fingerprint":
        lambda r: r.profiles.cell_block_file_fingerprint(),
    "cell_block_profile_content_fingerprint":
        lambda r: r.profiles.cell_block_content_fingerprint(),
    "qualification_policy_key": lambda r: r.policy.policy_key,
    "qualification_policy_fingerprint": lambda r: r.policy.policy_fingerprint,
}


def _alignment_id_of(terminals: Sequence[Any]) -> str:
    """对齐终态集合的修订身份（与 TS4 同一口径**重算**，不采信自报字符串）。"""
    from .canonical import identity
    if not terminals:
        raise FinalVerificationError("没有对齐终态，无法确定 alignment_id")
    return identity("als", {
        "terminal_ids": sorted(t.terminal_id for t in terminals),
        "schema_version": V.ALIGN_SCHEMA_VERSION})


def _recompute_upstream_fingerprint(roots: _Roots) -> str:
    payload = {name: getter(roots) for name, getter in UPSTREAM_FIELD_SOURCES.items()}
    return TS.upstream_dependency_fingerprint(payload)


def _revalidate(obj: Any, typename: str) -> Any:
    """把 wire 对象重跑一次它自己的构造校验（挡 `object.__new__` 旁路）。"""
    cls = type(obj)
    if not hasattr(cls, "from_dict"):
        raise FinalVerificationError(f"{typename} 缺 from_dict，无法重新校验")
    try:
        return cls.from_dict(obj.to_dict())
    except SchemaValidationError as exc:
        raise FinalVerificationError(
            f"{typename} 重新校验失败（对象可能是旁路构造的）：{exc}")


# ---------------------------------------------------------------------------
# 4. 表格侧：从**表格自身**重算消费分区（不经过 table_builder）
# ---------------------------------------------------------------------------

def _consumed_of_table(table: Any) -> dict:
    """`component_id -> 该表真实消费的 Evidence 子区间`，只读表格自己的 cell。"""
    out: dict = {}
    for cell in table.all_cells:
        for ref in cell.source_refs:
            out.setdefault(ref.component_id, []).append(
                tuple(ref.evidence_char_range))
    return {cid: tuple(sorted(set(ranges))) for cid, ranges in out.items()}


def _ref_ids_of_table(table: Any) -> dict:
    out: dict = {}
    for ref in table.source_refs:
        out.setdefault(ref.component_id, set()).add(ref.source_ref_id)
    return {cid: tuple(sorted(ids)) for cid, ids in out.items()}


def _partition_problems(component: Any, ranges: Sequence) -> tuple[str, ...]:
    """§19.9.1 的分区硬门（本地重实现，不调用 builder 的同类函数）。

    被某个表消费的 Evidence 子区间必须**恰好铺满**该 component 的区间：无空洞、
    无重叠、无越界、无多余行。任一条不满足，该 component 就不是"被这张表干净吸收"。
    """
    lo, hi = component.evidence_char_range
    problems: list[str] = []
    if len(set(ranges)) != len(ranges):
        problems.append("range_duplicated")
    cursor = lo
    for start, end in sorted(ranges):
        if start < lo or end > hi:
            problems.append("range_out_of_component")
            break
        if start != cursor:
            problems.append("gap_or_overlap")
            break
        cursor = end
    else:
        if cursor != hi:
            problems.append("range_not_exact")
    return tuple(problems)


# ---------------------------------------------------------------------------
# 5. 逐层复核
# ---------------------------------------------------------------------------

def _check_root_bundle(snapshot: Any, roots: _Roots) -> None:
    p = _Problems()
    if not isinstance(snapshot, TS.FinalMaterialStructureSnapshot):
        raise FinalVerificationError(
            "待复核对象必须为 FinalMaterialStructureSnapshot，得到 "
            f"{type(snapshot).__name__}")
    if SS.capability_scope(snapshot) is not None:
        raise FinalVerificationError(
            "待复核的**输入**快照不得本身已是能力对象（建造者与复核者必须分离）")
    # 重跑构造校验：形状、封闭词表、版本钉死、成员顺序、身份载荷、DAG 回指。
    rebuilt = _revalidate(snapshot, "FinalMaterialStructureSnapshot")
    difference = _first_difference(rebuilt.to_dict(), snapshot.to_dict(),
                                   "FinalMaterialStructureSnapshot")
    if difference is not None:
        p.add(f"快照重新校验后与其自身不等：{difference}")
    expected_upstream = _recompute_upstream_fingerprint(roots)
    if snapshot.upstream_dependency_fingerprint != expected_upstream:
        p.add("upstream_dependency_fingerprint 不可由经验证的 roots 复现："
              f"{snapshot.upstream_dependency_fingerprint!r} != "
              f"{expected_upstream!r}（根被替换或指纹自洽造假）")
    for field, expected in (
            ("document_id", roots.page_layout.document_id),
            ("document_version", roots.page_layout.document_version),
            ("evidence_set_version",
             roots.evidence_snapshot.evidence_set_version),
            ("page_layout_id", roots.page_layout.page_layout_id),
            ("outline_id", roots.outline.outline_id),
            ("verified_span_snapshot_id", roots.snapshot.snapshot_id)):
        if getattr(snapshot, field) != expected:
            p.add(f"snapshot.{field} 与经验证 roots 不一致："
                  f"{getattr(snapshot, field)!r} != {expected!r}")
    if snapshot.builder_version != V.FINAL_MATERIAL_BUILDER_VERSION:
        p.add(f"builder_version 必须为 {V.FINAL_MATERIAL_BUILDER_VERSION!r}，"
              f"得到 {snapshot.builder_version!r}")
    if snapshot.final_span_schema_version != V.FINAL_SPAN_SCHEMA_VERSION:
        p.add("final_span_schema_version 与登记不符")
    try:
        TS.assert_identity_dag_acyclic()
    except SchemaValidationError as exc:
        p.add(f"identity DAG 非法：{exc}")
    p.raise_if_any("根与身份：")


def _check_tables(snapshot: Any, roots: _Roots) -> dict:
    """表格与 TS4 根的一致性；返回 `(consumed, ref_ids)` 供 binding 复核复用。"""
    p = _Problems()
    node_ids = {n.node_id for n in roots.outline.nodes}
    # 未归属边界的两条合法来源：TS4 的 `unassigned` disposition，或 TS3 的显式
    # 未归属 span。二者都必须**在经验证的终态里**存在，而不是自报的字符串。
    unassigned_dispositions = {
        d.disposition_locator: d.disposition_id
        for d in roots.dispositions if d.range_kind == "unassigned"}
    unassigned_spans = {
        s.span_locator: s.span_id
        for s in roots.spans if getattr(s, "unassigned_reason", None) is not None}
    consumed: dict = {}
    ref_ids: dict = {}
    seen_locators: set = set()
    for table in snapshot.tables:
        _revalidate(table, "TableObjectV4")
        if table.table_locator in seen_locators:
            p.add(f"table_locator 重复：{table.table_locator!r}")
        seen_locators.add(table.table_locator)
        if table.document_id != roots.page_layout.document_id or \
                table.page_layout_id != roots.page_layout.page_layout_id or \
                table.outline_id != roots.outline.outline_id:
            p.add(f"表 {table.table_id!r} 不属于本文档（跨树拼接拒绝）")
        if table.verified_span_snapshot_id != roots.snapshot.snapshot_id:
            p.add(f"表 {table.table_id!r} 绑定的 TS4 快照与本文档不一致")
        if table.upstream_dependency_fingerprint != \
                snapshot.upstream_dependency_fingerprint:
            p.add(f"表 {table.table_id!r} 的上游依赖束与快照不一致")
        if table.is_financial_authority():
            p.add(f"表 {table.table_id!r} 不得取得财务权威")
        # --- owner：恰好一个，且必须独立回查到**已验证**的标题节点或未归属边界。
        # 相邻标题字符串长得像**不是**归属证明（§19.4.2）。
        owner = table.owner
        _revalidate(owner, "TableOwnerRef")
        if owner.source_boundary_kind not in TS.SOURCE_BOUNDARY_KINDS:
            p.add(f"表 {table.table_id!r} 的 source_boundary_kind 未登记")
        if owner.owner_kind == "outline_node":
            if owner.node_id not in node_ids:
                p.add(f"表 {table.table_id!r} 的 owner 节点 {owner.node_id!r} "
                      "不在经验证的标题树中")
            if owner.outline_locator != roots.outline.outline_locator:
                p.add(f"表 {table.table_id!r} 的 owner 不属于本文档标题树")
            for name in ("unassigned_ref_kind", "unassigned_ref_locator",
                         "unassigned_ref_id"):
                if getattr(owner, name) is not None:
                    p.add(f"表 {table.table_id!r} 的 outline_node owner 不得携带 "
                          f"{name}")
        elif owner.owner_kind == "unassigned_boundary":
            if owner.unassigned_ref_kind == "ts4_disposition":
                resolved = unassigned_dispositions.get(
                    owner.unassigned_ref_locator)
            else:
                resolved = unassigned_spans.get(owner.unassigned_ref_locator)
            if resolved is None:
                p.add(f"表 {table.table_id!r} 的未归属 owner 边界 "
                      f"{owner.unassigned_ref_locator!r} 不在经验证的终态中"
                      f"（kind={owner.unassigned_ref_kind!r}）")
            elif resolved != owner.unassigned_ref_id:
                p.add(f"表 {table.table_id!r} 的未归属 owner 引用 id 与 locator "
                      "指的不是同一个对象")
        else:
            p.add(f"表 {table.table_id!r} 的 owner_kind {owner.owner_kind!r} "
                  "未登记")
        # source boundary 必须同样可回查（owner 与边界不得分叉）。
        if owner.source_boundary_kind == "ts4_body_disposition":
            if owner.source_boundary_locator not in unassigned_dispositions and \
                    owner.source_boundary_locator not in {
                        d.disposition_locator for d in roots.dispositions}:
                p.add(f"表 {table.table_id!r} 的 source boundary "
                      f"{owner.source_boundary_locator!r} 不是经验证的 TS4 "
                      "disposition")
        elif owner.source_boundary_kind == "ts3_unassigned_span":
            if owner.source_boundary_locator not in unassigned_spans:
                p.add(f"表 {table.table_id!r} 的 source boundary "
                      f"{owner.source_boundary_locator!r} 不是经验证的 TS3 "
                      "未归属 span")
        # --- cell provenance：每条来源片段必须落到真实 terminal 与真实 LayoutSpan。
        layout = roots.page_layout
        pages = {page.page_number: page for page in layout.pages}
        for ref in table.source_refs:
            iv = ref.interval
            page = pages.get(iv.page_number)
            if page is None:
                p.add(f"表 {table.table_id!r} 的来源片段落在不存在的页 "
                      f"{iv.page_number}")
                continue
            line = None
            for candidate in page.lines:
                if candidate.line_index == iv.line_index:
                    line = candidate
                    break
            if line is None or iv.span_index >= len(line.spans):
                p.add(f"表 {table.table_id!r} 的来源片段落在不存在的 LayoutSpan "
                      f"{iv.page_number}/{iv.line_index}/{iv.span_index}")
                continue
            span = line.spans[iv.span_index]
            start, end = iv.span_char_range
            if not (0 <= start < end <= len(span.text)):
                p.add(f"表 {table.table_id!r} 的来源片段区间 {iv.span_char_range} "
                      f"越出 LayoutSpan 文本长度 {len(span.text)}")
                continue
            if len(span.text) != (span.char_end - span.char_start):
                p.add(f"LayoutSpan {iv.page_number}/{iv.line_index}/"
                      f"{iv.span_index} 的字符域不自洽（版式层问题）")
            block_text = roots.block_text(ref.evidence_block_id)
            if block_text is None:
                p.add(f"表 {table.table_id!r} 的来源片段引用未知 Evidence block "
                      f"{ref.evidence_block_id!r}")
            else:
                lo, hi = ref.evidence_char_range
                if not (0 <= lo < hi <= len(block_text)):
                    p.add(f"表 {table.table_id!r} 的来源片段 Evidence 区间越界")
            if ref.citable and ref.citable_reason != TS.CITABLE_REASON_TRUE:
                p.add(f"表 {table.table_id!r} 的可引用片段理由必须为 "
                      f"{TS.CITABLE_REASON_TRUE!r}")
            if not ref.citable and ref.citable_reason not in TS.CITABLE_REASONS:
                p.add(f"表 {table.table_id!r} 的不可引用理由未登记")
            if ref.terminal_kind == "alignment" and ref.alignment_id is None:
                p.add("alignment terminal 必须派生 alignment_id")
        # --- 结构状态与缺字段码：未知类别一律不得成为"完整表"。
        if table.structure_kind not in TS.STRUCTURE_KINDS:
            p.add(f"表 {table.table_id!r} 的 structure_kind 未登记")
        if table.structure_state not in TS.STRUCTURE_STATES:
            p.add(f"表 {table.table_id!r} 的 structure_state 未登记")
        for code in table.missing_or_uncertain_fields:
            if code not in TS.MISSING_FIELD_CODES:
                p.add(f"表 {table.table_id!r} 的缺字段码 {code!r} 未登记")
        consumed[table.table_id] = _consumed_of_table(table)
        ref_ids[table.table_id] = _ref_ids_of_table(table)
    p.raise_if_any("表格与 TS4 根：")
    return {"consumed": consumed, "ref_ids": ref_ids}


# --- final span ------------------------------------------------------------

def _recompute_final_spans(roots: _Roots, snapshot: Any) -> dict:
    """独立重建 `sbf-1` final span 的期望集合（键：TS4 span_locator）。"""
    by_span: dict = {}
    for c in roots.components:
        if c.landing == "body_span" and c.span_id:
            by_span.setdefault(c.span_id, []).append(c)
    regular = roots.regular_dispositions_by_span()
    expected: dict = {}
    for span in roots.spans:
        members = by_span.get(span.span_id)
        if not members:
            continue
        disps = regular.get(span.span_id, ())
        if len(disps) != 1:
            expected[span.span_locator] = {"error": "regular_disposition_count",
                                           "count": len(disps)}
            continue
        d = disps[0]
        members = sorted(members,
                         key=lambda c: (roots.block_order.get(
                             c.evidence_block_id, (0, 0)),
                             c.evidence_char_range, c.component_id))
        pieces: list = []
        intervals: list = []
        ranges: list = []
        cursor = 0
        error = None
        for c in members:
            text = roots.block_text(c.evidence_block_id)
            if text is None:
                error = "unknown_evidence_block"
                break
            lo, hi = c.evidence_char_range
            if not (0 <= lo < hi <= len(text)):
                error = "evidence_range_out_of_block"
                break
            frag = text[lo:hi]
            citable = bool(c.admitted)
            reason = TS.CITABLE_REASON_TRUE if citable else \
                _citable_reason_of_admission(c.admission_reason)
            intervals.append({
                "citable": citable, "reason": reason,
                "char_range": (cursor, cursor + len(frag)),
                "cell_ref": (c.component_id if citable else None)})
            cursor += len(frag)
            pieces.append(frag)
            ranges.append((c.evidence_block_id, int(lo), int(hi)))
        if error is not None:
            expected[span.span_locator] = {"error": error}
            continue
        left, right = d.left_boundary_cause, d.right_boundary_cause
        if not isinstance(left, str) or not left or \
                not isinstance(right, str) or not right:
            expected[span.span_locator] = {"error": "boundary_cause_missing"}
            continue
        left_factor = roots.policy.factor_for("left", left)
        right_factor = roots.policy.factor_for("right", right)
        qualified = quantize(min(left_factor, right_factor)) >= \
            quantize(V.SPAN_CONFIDENCE_MIN)
        if not qualified:
            intervals = [{
                "citable": False,
                "reason": (iv["reason"] if not iv["citable"]
                           else "component_not_admitted"),
                "char_range": iv["char_range"],
                "cell_ref": (iv["cell_ref"] if not iv["citable"] else None)}
                for iv in intervals]
        expected[span.span_locator] = {
            "node_id": span.node_id,
            "source_boundary_id": d.disposition_id,
            "component_ids": tuple(sorted(c.component_id for c in members)),
            "evidence_char_ranges": tuple(sorted(ranges, key=lambda r: (r[0], r[1]))),
            "normalized_text": "".join(pieces),
            "citable_intervals": tuple(intervals),
            "confidence": quantize(left_factor * right_factor),
            "confidence_min": quantize(V.SPAN_CONFIDENCE_MIN),
            "start_page": int(span.start_anchor[0]),
            "start_line": int(span.start_anchor[1]),
            "end_page": int(span.end_anchor[0]),
            "end_line": int(span.end_anchor[1]),
            "qualified": qualified,
        }
    return expected


def _citable_reason_of_admission(admission_reason: str) -> str:
    mapping = {
        "verdict_not_aligned": "verdict_not_aligned",
        "refusal_record": "refusal_record",
        "offset_unverifiable": "offset_unverifiable",
        "boundary_inexact": "fragment_not_closed",
        "landing_not_body_span": "component_not_admitted",
        "residue_unmapped": "residue_unmapped",
    }
    reason = mapping.get(admission_reason)
    if reason is None:
        raise FinalVerificationError(
            f"未登记的 admission_reason {admission_reason!r}")
    return reason


def _check_final_spans(snapshot: Any, roots: _Roots) -> dict:
    p = _Problems()
    expected = _recompute_final_spans(roots, snapshot)
    by_ts4_locator: dict = {}
    for sp in snapshot.final_spans:
        _revalidate(sp, "FinalOutlineSpan")
        if sp.ts4_source_span_locator is None:
            p.add(f"final span {sp.span_id!r} 必须回指 TS4 源 span locator")
            continue
        if sp.ts4_source_span_locator in by_ts4_locator:
            p.add(f"TS4 span {sp.ts4_source_span_locator!r} 对应多条 final span")
        by_ts4_locator[sp.ts4_source_span_locator] = sp
    expected_span_ids = {sp.span_id for sp in snapshot.final_spans}
    span_of_component: dict = {}
    for locator_key, want in expected.items():
        got = by_ts4_locator.get(locator_key)
        if "error" in want:
            p.add(f"TS4 span {locator_key!r} 无法重建 final span：{want['error']}")
            continue
        if got is None:
            p.add(f"TS4 span {locator_key!r} 应当产出 final span，但快照里没有"
                  "（删除 final span 必须被拒绝）")
            continue
        for field in ("node_id", "source_boundary_id", "component_ids",
                      "evidence_char_ranges", "normalized_text",
                      "confidence", "confidence_min", "start_page", "start_line",
                      "end_page", "end_line"):
            want_value = want[field]
            got_value = getattr(got, field)
            if field == "evidence_char_ranges":
                want_value = tuple(tuple(x) for x in want_value)
                got_value = tuple(tuple(x) for x in got_value)
            elif field == "component_ids":
                want_value = tuple(want_value)
                got_value = tuple(got_value)
            if want_value != got_value:
                p.add(f"final span {locator_key!r} 的 {field} 重算不等："
                      f"{got_value!r} != {want_value!r}")
        got_intervals = tuple({
            "citable": iv.citable, "reason": iv.reason,
            "char_range": tuple(iv.char_range),
            "cell_ref": iv.cell_ref} for iv in got.citable_intervals)
        if got_intervals != want["citable_intervals"]:
            p.add(f"final span {locator_key!r} 的 citable_intervals 重算不等："
                  f"{got_intervals!r} != {want['citable_intervals']!r}")
        want_factors = tuple((s, c, quantize(f))
                             for (s, c, f) in SP.TS4_A_FACTOR_VALUES)
        got_factors = tuple((f.side, f.cause, quantize(f.factor))
                            for f in got.boundary_factors)
        if got_factors != want_factors:
            p.add(f"final span {locator_key!r} 的 12 个边界因子偏离冻结表")
        for cid in got.component_ids:
            if cid in span_of_component:
                p.add(f"component {cid!r} 同时进入两条 final span")
            span_of_component[cid] = got.span_id
        for cid in got.component_ids:
            component = roots.components_by_id.get(cid)
            if component is None:
                p.add(f"final span {got.span_id!r} 引用了未知 component {cid!r}")
            elif component.landing != "body_span":
                p.add(f"final span {got.span_id!r} 吸纳了非 body_span component "
                      f"{cid!r}（landing={component.landing!r}）")
    if set(expected_span_ids) != {sp.span_id for sp in snapshot.final_spans}:
        p.add("final span 集合不是精确等集")
    p.raise_if_any("final span：")
    return {"span_of_component": span_of_component,
            "expected": expected}


# --- decision --------------------------------------------------------------

def _check_decisions(snapshot: Any, roots: _Roots) -> dict:
    p = _Problems()
    expected_ids = {d.disposition_id for d in roots.table_dispositions()}
    seen: dict = {}
    for dec in snapshot.decisions:
        _revalidate(dec, "TableRangeDecision")
        if dec.disposition_id in seen:
            p.add(f"disposition {dec.disposition_id!r} 出现多条裁决")
        seen[dec.disposition_id] = dec
        if dec.disposition_id not in expected_ids:
            p.add(f"裁决引用了非表 provisional disposition {dec.disposition_id!r}"
                  "（不得只为部分范围裁决）")
        if dec.range_kind not in TS.TABLE_RANGE_KINDS:
            p.add(f"裁决 {dec.disposition_id!r} 的 range_kind 必须是 TS4 表范围")
        target = TS.TABLE_DECISION_TARGETS.get(dec.decision)
        if target is None:
            p.add(f"裁决 {dec.disposition_id!r} 的 decision 未登记")
            continue
        if dec.target_kind != target:
            p.add(f"裁决 {dec.disposition_id!r} 的 target_kind 必须为 {target!r}，"
                  f"得到 {dec.target_kind!r}")
        if target == "table" and dec.table_id is None:
            p.add(f"吸收到表的裁决 {dec.disposition_id!r} 必须给出 table_id")
        if target == "final_span" and dec.final_span_id is None:
            p.add(f"保留为段落的裁决 {dec.disposition_id!r} 必须给出 final_span_id")
        if target != "table" and dec.table_id is not None:
            p.add(f"裁决 {dec.disposition_id!r} 不得给出 table_id")
        if target != "final_span" and dec.final_span_id is not None:
            p.add(f"裁决 {dec.disposition_id!r} 不得给出 final_span_id")
        if dec.table_id is not None and \
                snapshot.table_by_id(dec.table_id) is None:
            p.add(f"裁决 {dec.disposition_id!r} 引用了本快照以外的 table")
        if dec.final_span_id is not None and \
                snapshot.final_span_by_id(dec.final_span_id) is None:
            p.add(f"裁决 {dec.disposition_id!r} 引用了本快照以外的 final span")
        # 裁决只单向引用已完成对象：它消费的来源必须属于**被裁决的那张表**。
        owned: set = set()
        if dec.table_id is not None:
            table = snapshot.table_by_id(dec.table_id)
            if table is not None:
                owned = {r.source_ref_id for r in table.source_refs}
        for rid in dec.source_ref_ids:
            if rid not in owned:
                p.add(f"裁决 {dec.disposition_id!r} 引用了未知 source_ref {rid!r}"
                      "（必须属于被裁决的那张表）")
        if dec.source_ref_ids == () and dec.evidence_char_range != (0, 0):
            p.add(f"裁决 {dec.disposition_id!r} 给出了证据区间却没有来源片段")
    missing = sorted(expected_ids - set(seen))
    if missing:
        p.add(f"以下表 provisional disposition 没有被裁决（不得静默跳过）："
              f"{missing[:8]}")
    p.raise_if_any("裁决：")
    return {"decision_of": seen}


# --- binding ---------------------------------------------------------------

def _check_bindings(snapshot: Any, roots: _Roots, facts: dict) -> dict:
    """逐 component 重算 §19.9.1 的终态真值表。"""
    p = _Problems()
    consumed = facts["consumed"]
    ref_ids = facts["ref_ids"]
    decisions = facts["decision_of"]
    span_of_component = facts["span_of_component"]

    tables_of: dict = {}
    ref_ids_of_component: dict = {}
    partition_problems: dict = {}
    for table_id, per_component in consumed.items():
        for cid, ranges in per_component.items():
            tables_of.setdefault(cid, []).append((table_id, ranges))
            ref_ids_of_component[(table_id, cid)] = \
                ref_ids.get(table_id, {}).get(cid, ())
    for cid, entries in tables_of.items():
        component = roots.components_by_id.get(cid)
        if component is None:
            p.add(f"表消费了非本快照的 component {cid!r}")
            continue
        for table_id, ranges in entries:
            problems = _partition_problems(component, ranges)
            if problems:
                partition_problems.setdefault(cid, []).extend(problems)
    for cid, problems in partition_problems.items():
        p.add(f"component {cid!r} 在表中的消费分区不合法："
              f"{sorted(set(problems))}")

    expected_component_ids = {c.component_id for c in roots.components}
    got_component_ids = {b.component_id for b in snapshot.bindings}
    if len(snapshot.bindings) != len(roots.components) or \
            got_component_ids != expected_component_ids:
        p.add("binding 必须与经验证 TS4 components **精确等集**："
              f"缺 {sorted(expected_component_ids - got_component_ids)[:8]} / 多 "
              f"{sorted(got_component_ids - expected_component_ids)[:8]}")

    reused = span_of_component
    for binding in snapshot.bindings:
        _revalidate(binding, "FinalComponentBinding")
        component = roots.components_by_id.get(binding.component_id)
        if component is None:
            continue
        if binding.component_locator != component.component_locator:
            p.add(f"binding {binding.component_id!r} 的 component_locator 与 TS4 "
                  "不一致")
        if binding.component_landing != component.landing:
            p.add(f"binding {binding.component_id!r} 的 landing 与 TS4 不一致")
        if binding.evidence_char_range != tuple(component.evidence_char_range):
            p.add(f"binding {binding.component_id!r} 的区间与 TS4 不一致")
        if binding.disposition_id != component.disposition_id:
            p.add(f"binding {binding.component_id!r} 的 disposition_id 与 TS4 "
                  "不一致")
        want = _expected_binding(component, tables_of.get(binding.component_id, ()),
                                 ref_ids_of_component,
                                 partition_problems.get(binding.component_id, ()),
                                 decisions, reused)
        for field in ("admission", "admission_reason", "table_id",
                      "final_span_id", "cell_source_ref_ids", "gap_codes"):
            if getattr(binding, field) != want[field]:
                p.add(f"binding {binding.component_id!r} 的 {field} 重算不等："
                      f"{getattr(binding, field)!r} != {want[field]!r}")
    # 源组件不得同时进入 final span 与 table。
    for cid in set(reused) & set(tables_of):
        p.add(f"component {cid!r} 同时进入 final span 与表（必须唯一去路）")
    p.raise_if_any("component 终态：")
    return {"tables_of": tables_of}


def _expected_binding(component: Any, claims: Sequence, ref_ids: dict,
                      partition: Sequence[str], decisions: dict,
                      span_of_component: dict) -> dict:
    """§19.9.1 的封闭真值表（本地重实现）。"""
    def pending(reason: str) -> dict:
        return {"admission": "pending", "admission_reason": reason,
                "table_id": None, "final_span_id": None,
                "cell_source_ref_ids": (), "gap_codes": (reason,)}

    def rejected(reason: str) -> dict:
        return {"admission": "rejected", "admission_reason": reason,
                "table_id": None, "final_span_id": None,
                "cell_source_ref_ids": (), "gap_codes": ()}

    def absorbed(table_id: str) -> dict:
        return {"admission": "table_object",
                "admission_reason": "absorbed_into_table", "table_id": table_id,
                "final_span_id": None,
                "cell_source_ref_ids": ref_ids.get((table_id,
                                                    component.component_id), ()),
                "gap_codes": ()}

    def as_final_span(reason: str, span_id: str) -> dict:
        return {"admission": "final_span", "admission_reason": reason,
                "table_id": None, "final_span_id": span_id,
                "cell_source_ref_ids": (), "gap_codes": ()}

    landing = component.landing
    decision = decisions.get(component.disposition_id)

    def clean_single_table():
        if len(claims) == 1 and not partition:
            table_id = claims[0][0]
            if ref_ids.get((table_id, component.component_id), ()):
                return table_id
        return None

    if landing == "body_span":
        if claims:
            return pending("upstream_table_scope_miss")
        span_id = span_of_component.get(component.component_id)
        if span_id is None:
            return pending("upstream_table_scope_miss")
        return as_final_span("rebuilt_as_final_span", span_id)

    if landing in ("table_inside", "table_adjacency"):
        if decision is None:
            return pending("unsupported_table_structure")
        table_id = clean_single_table()
        if table_id is not None:
            return absorbed(table_id)
        if claims or partition:
            return pending("provenance_incomplete")
        if decision.decision in TS.DECISION_TARGET_OF["table"]:
            return pending("provenance_incomplete")
        if decision.decision in TS.DECISION_TARGET_OF["final_span"]:
            span_id = span_of_component.get(component.component_id)
            if span_id is not None:
                return as_final_span("kept_as_final_paragraph", span_id)
            return pending("provenance_incomplete")
        if decision.decision == "unresolved_geometry":
            return pending("unresolved_geometry")
        return pending("unsupported_table_structure")

    if landing in ("body_unassigned", "formal_unassigned"):
        table_id = clean_single_table()
        if table_id is not None:
            return absorbed(table_id)
        return pending("provenance_incomplete")

    rejected_reason = {
        "body_empty": "empty_source_text",
        "heading_node": "structural_heading_only",
        "non_content": "non_content_region",
        "outside_body": "outside_formal_body",
    }.get(landing)
    if rejected_reason is not None:
        return rejected(rejected_reason)

    if landing in ("alignment_offset_unverifiable", "alignment_residue_unmapped"):
        return pending("provenance_incomplete")

    raise FinalVerificationError(f"未登记的 component landing {landing!r}")


# --- coverage --------------------------------------------------------------

def _check_coverages(snapshot: Any, roots: _Roots) -> None:
    p = _Problems()
    expected = {t.table_id for t in snapshot.tables}
    got = {c.table_id for c in snapshot.coverages}
    if len(snapshot.coverages) != len(snapshot.tables) or got != expected:
        p.add("coverage 必须与表精确一一对应（缺 "
              f"{sorted(expected - got)[:8]} / 多 {sorted(got - expected)[:8]}）")
    pages = {page.page_number: page for page in roots.page_layout.pages}
    for coverage in snapshot.coverages:
        _revalidate(coverage, "TableCitableCoverage")
        table = snapshot.table_by_id(coverage.table_id)
        if table is None:
            p.add(f"coverage 引用了未知表 {coverage.table_id!r}")
            continue
        if coverage.table_locator != table.table_locator:
            p.add(f"coverage 的 table_locator 与表不一致（{coverage.table_id!r}）")
        want_refs = tuple(sorted({r.source_ref_id for r in table.source_refs}))
        if tuple(coverage.cell_refs) != want_refs:
            p.add(f"coverage 的 cell_refs 必须等于该表全部真实来源片段"
                  f"（{coverage.table_id!r}）")
        if len(coverage.intervals) != len(want_refs):
            p.add(f"coverage 必须逐条来源片段给区间（{coverage.table_id!r}）")
        cursor = 0
        want_intervals: list = []
        for ref in table.source_refs:
            page = pages.get(ref.interval.page_number)
            span = None
            if page is not None:
                for line in page.lines:
                    if line.line_index == ref.interval.line_index and \
                            ref.interval.span_index < len(line.spans):
                        span = line.spans[ref.interval.span_index]
                        break
            if span is None:
                p.add(f"coverage 引用的 LayoutSpan 不存在"
                      f"（{coverage.table_id!r}）")
                continue
            start, end = ref.interval.span_char_range
            want_intervals.append({
                "citable": ref.citable, "reason": ref.citable_reason,
                "char_range": (cursor, cursor + (end - start)),
                "cell_ref": ref.source_ref_id})
            cursor += end - start
        got_intervals = tuple({
            "citable": iv.citable, "reason": iv.reason,
            "char_range": tuple(iv.char_range),
            "cell_ref": iv.cell_ref} for iv in coverage.intervals)
        if tuple(want_intervals) != got_intervals:
            p.add(f"coverage 的区间/可引用性重算不等（{coverage.table_id!r}）："
                  f"{got_intervals!r} != {tuple(want_intervals)!r}")
        for iv in coverage.intervals:
            if iv.citable and iv.reason != TS.CITABLE_REASON_TRUE:
                p.add(f"coverage 可引用区间的理由必须为 "
                      f"{TS.CITABLE_REASON_TRUE!r}")
            if not iv.citable and iv.reason not in TS.CITABLE_REASONS:
                p.add("coverage 不可引用区间的理由未登记")
        want_reasons = tuple(sorted({iv.reason for iv in coverage.intervals
                                     if not iv.citable}))
        if tuple(coverage.non_citable_reasons) != want_reasons:
            p.add(f"coverage 的 non_citable_reasons 重算不等"
                  f"（{coverage.table_id!r}）")
        blocks = {r.evidence_block_id for r in table.source_refs}
        lo = min(r.evidence_char_range[0] for r in table.source_refs)
        hi = max(r.evidence_char_range[1] for r in table.source_refs)
        want_envelope = (int(lo), int(hi)) if len(blocks) == 1 else (0, 0)
        if tuple(coverage.evidence_char_range) != want_envelope:
            p.add(f"coverage 的 evidence_char_range 重算不等"
                  f"（{coverage.table_id!r}）："
                  f"{tuple(coverage.evidence_char_range)!r} != {want_envelope!r}")
    p.raise_if_any("逐 cell 覆盖：")


# --- relation --------------------------------------------------------------

def _endpoint_targets(snapshot: Any) -> dict:
    return {
        "table_object": {t.table_id for t in snapshot.tables},
        "final_span": {s.span_id for s in snapshot.final_spans},
        "component": {b.component_id for b in snapshot.bindings},
    }


def _continuation_header_text(table: Any) -> tuple:
    """表头行的 cell 文本序列（没有表头行就是空元组）。"""
    for row in table.rows:
        if row.role == "header":
            return tuple(c.text for c in row.cells)
    return ()


def _continuation_column_starts(table: Any) -> tuple:
    """每个列索引的列带起点（该列全部 cell 里最小的左边界）。

    与 builder 侧的同名量必须**各自独立**算出来（复核器不得调用 builder 的实现）：
    列带起点而不是逐 cell 矩形，是因为 borderless 路径的 cell 矩形逐行不同
    （首行缩进、右对齐数字），拿逐 cell 矩形比较会把上下续表一律判成不兼容。
    """
    starts: dict = {}
    for row in table.rows:
        for cell in row.cells:
            starts[cell.column] = min(starts.get(cell.column, cell.bbox[0]),
                                      cell.bbox[0])
    return tuple(sorted(starts.items()))


def _continuation_pair_ok(a: Any, b: Any, tables: Sequence[Any]) -> dict:
    """**独立重证** `continued_by` 的六条证明条件（键取自 `CONTINUATION_PROOF_KINDS`）。

    复核器不读 builder 的中间量、不调用 builder 的判定函数：一切输入都来自快照自身的
    `TableObjectV4` 字段与本文档真实表集合。第 6 条（单一后继且无环）需要整条链的视野，
    因此由调用方在收集完所有 `continued_by` 边之后统一判定并在本函数里回填。
    """
    tol = TG.BORDERLESS_CLUSTER_PARAMS["column_tolerance"]
    sa = _continuation_column_starts(a)
    sb = _continuation_column_starts(b)
    bands_ok = bool(sa) and tuple(c for c, _ in sa) == tuple(c for c, _ in sb) \
        and all(abs(x - y) <= tol for (_, x), (_, y) in zip(sa, sb))
    return {
        "same_document_identity": (
            a.document_id, a.document_version, a.evidence_set_version,
            a.page_layout_id, a.outline_id) == (
            b.document_id, b.document_version, b.evidence_set_version,
            b.page_layout_id, b.outline_id),
        "adjacent_page_or_explicit_occurrence": (
            b.page_number == a.page_number + 1),
        "compatible_column_header_unit": (
            a.column_count == b.column_count
            and a.unit_text == b.unit_text
            and _continuation_header_text(a) == _continuation_header_text(b)
            and bands_ok),
        "source_order_closure": (
            (b.page_number, tuple(b.page_bbox))
            > (a.page_number, tuple(a.page_bbox))),
        "no_intervening_structure": not any(
            a.page_number < other.page_number < b.page_number
            for other in tables
            if other.table_id not in (a.table_id, b.table_id)),
        # 由 `_check_continuation_chain` 回填。
        "bidirectional_acyclic_single_successor": True,
    }


def _check_continuation_chain(pairs: Sequence[tuple]) -> tuple:
    """单一后继 + 单一前驱 + 无环（§19.8.2 证明 6 的全局部分）。"""
    succ: dict = {}
    pred: dict = {}
    for a_id, b_id in pairs:
        succ.setdefault(a_id, []).append(b_id)
        pred.setdefault(b_id, []).append(a_id)
    problems: list = []
    for a_id, targets in sorted(succ.items()):
        if len(targets) != 1:
            problems.append(f"表 {a_id!r} 有 {len(targets)} 个后继（必须恰好一个）")
        elif len(pred.get(targets[0], [])) != 1:
            problems.append(f"表 {targets[0]!r} 有 "
                            f"{len(pred[targets[0]])} 个前驱（必须恰好一个）")
    for start in sorted(succ):
        seen = {start}
        cur = start
        while True:
            nxt = succ.get(cur, [])
            if not nxt:
                break
            cur = nxt[0]
            if cur in seen:
                problems.append(f"续接链存在环：{start!r} → … → {cur!r}")
                break
            seen.add(cur)
    return tuple(problems)


def _check_continuation_relations(snapshot: Any, p: "_Problems") -> None:
    """`continued_by` 的独立重证：证明条件名是**待重证的条件**，不是自证凭据。

    `_proof_ids` 把六条条件名一并写进 `relation_proof_ids`，复核器因此不能把它们当成
    "可回查的对象句柄"（它们回查不到任何对象），也不能因为 builder 声明了它们就放行。
    正确读法是：**声明必须是完整的六条**，且六条必须在本文档的快照字段上**逐条重算成立**。
    """
    tables = tuple(snapshot.tables)
    by_id = {t.table_id: t for t in tables}
    pairs: list = []
    for relation in snapshot.relations:
        if relation.relation_kind != "continued_by":
            continue
        kinds = tuple(pid for pid in relation.relation_proof_ids
                      if pid in TS.CONTINUATION_PROOF_KINDS)
        missing = tuple(sorted(set(TS.CONTINUATION_PROOF_KINDS) - set(kinds)))
        if missing:
            p.add(f"continued_by 必须把全部证明条件列为待重证项"
                  f"（{relation.relation_id!r} 缺 {list(missing)}）")
        for side, endpoint in (("source", relation.source_endpoint),
                               ("target", relation.target_endpoint)):
            if getattr(endpoint, "endpoint_kind", None) != "table":
                p.add(f"continued_by 的 {side} 端点必须是 table 端点"
                      f"（{relation.relation_id!r}）")
        a = by_id.get(getattr(relation.source_endpoint, "table_id", None))
        b = by_id.get(getattr(relation.target_endpoint, "table_id", None))
        if a is None or b is None:
            continue  # 端点自证问题已由端点检查记录，不在这里重复。
        failed = [name for name, ok in _continuation_pair_ok(a, b, tables).items()
                  if not ok]
        if failed:
            p.add(f"continued_by 的证明条件在独立重证中不成立"
                  f"（{relation.relation_id!r}：{failed}）")
        pairs.append((a.table_id, b.table_id))
    for problem in _check_continuation_chain(pairs):
        p.add(f"续接链结构不成立：{problem}")


def _check_relations(snapshot: Any, roots: _Roots) -> None:
    p = _Problems()
    targets = _endpoint_targets(snapshot)
    disposition_ids = {d.disposition_id for d in roots.dispositions}
    disposition_locators = {d.disposition_locator for d in roots.dispositions}
    source_refs = {r.source_ref_id for t in snapshot.tables
                   for r in t.source_refs}
    occurrences = set()
    for edge in roots.outline.edges:
        occ = getattr(edge, "occurrence", None)
        if occ is None:
            continue
        occurrences.add(getattr(edge, "edge_id", None))
    for relation in snapshot.relations:
        _revalidate(relation, "TableRelation")
        if relation.relation_kind not in TS.TABLE_RELATION_KINDS:
            p.add(f"关系种类 {relation.relation_kind!r} 未登记")
        if relation.relation_kind == "reconciles_with":
            p.add("reconciles_with 是未来能力，TS5 不得启用")
        for side, endpoint in (("source", relation.source_endpoint),
                               ("target", relation.target_endpoint)):
            kind = getattr(endpoint, "endpoint_kind", None)
            if kind not in TS.ENDPOINT_REF_BY_KIND:
                p.add(f"关系端点的 endpoint_kind {kind!r} 未登记")
                continue
            _revalidate(endpoint, f"{kind} endpoint")
            # `table` 是**端点种类**（`TableEndpointRef.endpoint_kind`），
            # `table_object` 是 `_endpoint_targets()` 里的**目标集合名**。两者不是
            # 同一个词表：把这一行写成 `table_object` 会让 table 端点整段跳过本快照
            # 检查（`caption_of` / `continued_by` 的表端点因此永远不被回查）。
            if kind == "table":
                if endpoint.table_id not in targets["table_object"]:
                    p.add(f"关系 {side} 端点引用了本快照以外的表"
                          f"（{relation.relation_id!r}）")
            elif kind == "final_span":
                if endpoint.span_id not in targets["final_span"]:
                    p.add(f"关系 {side} 端点引用了本快照以外的 final span"
                          f"（{relation.relation_id!r}）")
            elif kind == "component":
                if endpoint.component_id not in targets["component"]:
                    p.add(f"关系 {side} 端点引用了本快照以外的 component"
                          f"（{relation.relation_id!r}）")
            elif kind == "reference_occurrence":
                if getattr(endpoint, "owning_source_ref", None) not in source_refs:
                    p.add(f"reference occurrence 端点必须挂在本文档真实来源片段上"
                          f"（{relation.relation_id!r}）")
                if occurrences and getattr(endpoint, "edge_id", None) \
                        not in occurrences:
                    p.add(f"reference occurrence 端点必须回指真实的未解析边"
                          f"（{relation.relation_id!r}）")
        for proof_id in relation.relation_proof_ids:
            if proof_id in TS.CONTINUATION_PROOF_KINDS:
                # 这一项是**待重证的证明条件名**（见 `_check_continuation_relations`），
                # 它不是一个可回查的对象句柄。它只允许出现在 `continued_by` 上：别的
                # 关系种类拿条件名当证明引用，就是无可回查的自我声明。
                if relation.relation_kind != "continued_by":
                    p.add(f"证明条件名 {proof_id!r} 只允许出现在 continued_by 关系上"
                          f"（{relation.relation_id!r}）")
                continue
            resolved = (
                proof_id in targets["table_object"]
                or proof_id in targets["final_span"]
                or proof_id in targets["component"]
                or proof_id in source_refs
                # verified roots 里的 TS4 对象同样是合法证明句柄（它们在本轮
                # 复核中已被逐字段重算），否则"表前 span 与表同 source boundary"
                # 这类证明就无处落地。
                or proof_id in disposition_ids
                or proof_id in disposition_locators
                or any(proof_id == getattr(t, "table_locator", None)
                       for t in snapshot.tables)
                or any(proof_id == getattr(s, "span_locator", None)
                       for s in snapshot.final_spans)
                or any(proof_id == getattr(b, "component_locator", None)
                       for b in snapshot.bindings)
                or any(proof_id == getattr(t.owner, "node_id", None)
                       for t in snapshot.tables)
                or any(proof_id == getattr(edge, "edge_id", None)
                       for edge in roots.outline.edges)
                or any(proof_id == getattr(getattr(edge, "occurrence", None),
                                           "occurrence_index", None)
                       for edge in roots.outline.edges))
            if not resolved:
                p.add(f"关系证明 {proof_id!r} 无法回查到真实对象"
                      f"（{relation.relation_id!r}）")
        if not relation.relation_proof_ids:
            p.add(f"关系 {relation.relation_id!r} 必须给出可回查的证明引用")
    _check_continuation_relations(snapshot, p)
    p.raise_if_any("typed 关系：")


# --- conservation ----------------------------------------------------------

def _recheck_conservation(snapshot: Any, roots: _Roots) -> tuple[str, ...]:
    """独立重算**全部四层**守恒，返回问题（不自行降级、不自行放行）。

    §19.9.3 的第 3 层（`layout_text`）在 `fmc-2` 起由本模块独立重算：它读 PageLayout
    与快照里的表/绑定，重新切分"表相关文本域"，并独立判定每段字符是 cell / caption
    单元 / 段落 / **有凭据的延期** / 未解释残余。复核因此不依赖 builder 的中间量。
    """
    p = _Problems()
    recorded = {layer.layer_kind: layer for layer in snapshot.conservation.layers}
    if set(recorded) != set(TS.CONSERVATION_LAYER_KINDS):
        p.add("守恒必须恰为四层且种类齐全")
        return tuple(p.items)

    # 层 1：disposition
    table_dispositions = roots.table_dispositions()
    counts = {"absorbed_to_table": 0, "final_paragraph": 0,
              "pending_or_unsupported": 0}
    for dec in snapshot.decisions:
        if dec.decision in TS.DECISION_TARGET_OF["table"]:
            counts["absorbed_to_table"] += 1
        elif dec.decision in TS.DECISION_TARGET_OF["final_span"]:
            counts["final_paragraph"] += 1
        else:
            counts["pending_or_unsupported"] += 1
    _compare_layer(p, recorded["disposition"], counts, len(table_dispositions))

    # 层 2：component
    counts = {"final_span": 0, "table_object": 0, "pending": 0, "rejected": 0}
    for binding in snapshot.bindings:
        counts[binding.admission] = counts.get(binding.admission, 0) + 1
    _compare_layer(p, recorded["component"], counts, len(snapshot.bindings))

    # 层 3：layout_text（**独立重算**：只读 roots 的 PageLayout 与快照里的表/绑定，
    # 不调用 builder 的任何函数；见 `_recheck_layout_text_layer`）。
    counts, total, layer3_problems = _recheck_layout_text_layer(snapshot, roots)
    _compare_layer(p, recorded["layout_text"], counts, total, layer3_problems)

    # 层 4：evidence interval
    counts = {"preserved": 0, "missing": 0, "duplicated": 0}
    by_component: dict = {}
    for binding in snapshot.bindings:
        by_component.setdefault(binding.component_id, []).append(binding)
    total = 0
    for component in roots.components:
        lo, hi = component.evidence_char_range
        length = int(hi) - int(lo)
        total += length
        mine = by_component.get(component.component_id, ())
        if not mine:
            counts["missing"] += length
            continue
        if len(mine) > 1:
            counts["preserved"] += length
            counts["duplicated"] += (len(mine) - 1) * length
            continue
        if tuple(mine[0].evidence_char_range) != tuple(component.evidence_char_range):
            counts["missing"] += length
            continue
        counts["preserved"] += length
    _compare_layer(p, recorded["evidence_interval"], counts, total)
    return tuple(p.items)


def _compare_layer(p: _Problems, layer: Any, want: dict, total: int,
                   recomputed_problems: Sequence[str] = ()) -> None:
    kind = TS.conservation_layer_unit(layer.layer_kind)
    got = {}
    for term in layer.terms:
        got[term.term] = (term.count if kind == "count" else
                          term.char_count if kind == "character" else
                          term.interval_length)
    for name, value in want.items():
        if name not in got:
            p.add(f"{layer.layer_kind} 层缺分项 {name!r}")
        elif got[name] != value:
            p.add(f"{layer.layer_kind} 层分项 {name!r} 重算不等："
                  f"{got[name]} != {value}")
    recorded_total = (layer.total.count if kind == "count" else
                      layer.total.char_count if kind == "character" else
                      layer.total.interval_length)
    if recorded_total != total:
        p.add(f"{layer.layer_kind} 层的合计重算不等：{recorded_total} != {total}")
    for problem in recomputed_problems:
        p.add(f"{layer.layer_kind} 层独立重算发现问题：{problem}")
    # `balanced` 的判据是**共用**的资格函数，不是"分项之和相等"：算术守恒之外还要
    # 问题码为空、且必须为零的分项为 0。判据里的问题码是**记录值 ∪ 本层独立重算值**
    # ——只信载荷里那份，就会出现"重算出重叠、却被记成 balanced"的漏洞。
    problems = tuple(layer.problems) + tuple(recomputed_problems)
    if layer.balanced != TS.conservation_layer_eligible(
            layer.layer_kind, got, recorded_total, problems):
        p.add(f"{layer.layer_kind} 层的 balanced 与分层守恒资格函数不一致"
              f"（算术和={sum(got.values())}，合计={recorded_total}，"
              f"问题码={len(problems)}）")


def _bbox_overlap_area(a: Sequence[float], b: Sequence[float]) -> float:
    ax0, ay0, ax1, ay1 = (float(x) for x in a)
    bx0, by0, bx1, by1 = (float(x) for x in b)
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return w * h if w > 0.0 and h > 0.0 else 0.0


def _recheck_layout_text_layer(snapshot: Any, roots: _Roots) -> tuple:
    """独立重算 §19.9.3 第 3 层，返回 `(分项, 合计, 问题码)`。

    语义与 builder 逐字一致（同一个坐标域、同两条 `registered_deferred` 资格、同一条
    `non_semantic_whitespace` 判据、同样计入"片段内主张越界 / 重复主张 / 两类同时
    主张"），但**代码路径独立**：只读 `roots.page_layout` / `roots.components` /
    `roots.dispositions` 与待复核快照里的 tables / bindings / **gaps 的源区间**。
    它与 builder 的分歧正是复核要发现的东西。

    `fmc-3` 的第二条资格路径（typed gap 的 `source_intervals` 精确覆盖）在这里是
    **从载荷反推**的：本函数不调用 builder 的缺口区间计划，而是逐条读
    `snapshot.gaps.entries[].source_intervals`、按"区间包含该原子"建立覆盖索引。因此
    一个把区间写宽（顺手多覆盖几个已消费字符）、写重（一个字符被两条缺口覆盖）或写偏
    （覆盖到 component 已延期的区间）的台账，都会在本函数里与守恒层对不上而失败。

    `non_semantic_whitespace` 的判据来自 `table_schema.is_non_semantic_whitespace`
    ——那是**守恒词表**的一部分（"什么叫非语义空白"是 wire 语义，不是 builder 的实现
    细节），因此共用它不构成对 builder 的依赖；除它之外的切分与计数全部在本函数里
    独立完成。
    """
    problems: list = []
    # 缺口台账里**可延期种类**的源区间索引（独立反推，不读 builder 内部结构）。
    gap_intervals: list = []
    for entry in snapshot.gaps.entries:
        if entry.gap_kind not in TS.CONSERVATION_DEFERRED_GAP_KINDS:
            continue
        for iv in entry.source_intervals:
            gap_intervals.append((_gap_key(entry), iv.fragment_key,
                                  (int(iv.span_char_range[0]),
                                   int(iv.span_char_range[1]))))
    claims: dict = {}
    for table in snapshot.tables:
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    iv = ref.interval
                    claims.setdefault(
                        (iv.page_number, iv.line_index, iv.span_index),
                        []).append((tuple(iv.span_char_range), "cell"))
        for blocks in (table.title_blocks, table.unit_blocks, table.note_blocks):
            for blk in blocks:
                for rid in blk.source_ref_ids:
                    ref = table.source_ref_by_id(rid)
                    if ref is None:
                        problems.append(f"表 {table.table_id!r} 的块引用了未知来源 {rid!r}")
                        continue
                    iv = ref.interval
                    claims.setdefault(
                        (iv.page_number, iv.line_index, iv.span_index),
                        []).append((tuple(iv.span_char_range), "adorn"))

    paragraph_keys: set = set()
    hits_of: dict = {}
    for comp in roots.components:
        for hit in comp.layout_hits:
            key = (hit.page_number, hit.line_index, hit.layout_span_index)
            hits_of.setdefault(key, []).append(
                (int(hit.span_char_range[0]), int(hit.span_char_range[1]),
                 comp.component_id))
            if comp.landing == "body_span":
                paragraph_keys.add(key)
    binding_of = {b.component_id: b for b in snapshot.bindings}

    in_table_region: set = set()
    lines: dict = {}
    for page in roots.page_layout.pages:
        for line in page.lines:
            lines[(page.page_number, line.line_index)] = line
    for d in roots.dispositions:
        if d.range_kind not in TS.TABLE_RANGE_KINDS or d.start_page != d.end_page:
            continue
        for li in range(d.start_line, d.end_line + 1):
            line = lines.get((d.start_page, li))
            if line is None or line.is_furniture:
                continue
            for si in range(len(line.spans)):
                in_table_region.add((d.start_page, li, si))
    boxes_by_page: dict = {}
    for table in snapshot.tables:
        boxes_by_page.setdefault(table.page_number, []).append(
            tuple(table.page_bbox))

    domain: set = set(claims) | set(paragraph_keys) | set(in_table_region)
    for page in roots.page_layout.pages:
        boxes = boxes_by_page.get(page.page_number, ())
        if not boxes:
            continue
        for line in page.lines:
            if line.is_furniture:
                continue
            for si, span in enumerate(line.spans):
                key = (page.page_number, line.line_index, si)
                if key in in_table_region:
                    domain.add(key)
                    continue
                if any(_bbox_overlap_area(span.bbox, box) > 0.0 for box in boxes):
                    domain.add(key)
                    in_table_region.add(key)

    counts = {"cell_source_ref": 0, "caption_unit_note_ref": 0,
              "final_paragraph_ref": 0, "registered_deferred": 0,
              "non_semantic_whitespace": 0, "residual_gap": 0}
    total = 0
    free_atoms: dict = {}
    for page in roots.page_layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            for si, span in enumerate(line.spans):
                key = (page.page_number, line.line_index, si)
                if key not in domain:
                    continue
                base = int(span.char_start)
                lo, hi = 0, int(span.char_end) - base
                total += hi - lo
                marks = claims.get(key, ())
                if any(x < lo or x > hi for (rng, _k) in marks for x in rng):
                    problems.append(f"主张越界:{key}")
                if len(marks) != len({(tuple(rng), k) for (rng, k) in marks}):
                    problems.append(f"重复主张:{key}")
                local_hits: list = []
                for (hs, he, cid) in hits_of.get(key, ()):
                    if hs < lo or he > hi:
                        problems.append(f"layout hit 越界:{key}")
                        continue
                    local_hits.append((hs, he, cid))
                bounds = sorted({lo, hi}
                                | {x for (rng, _k) in marks for x in rng
                                   if lo <= x <= hi}
                                | {x for (hs, he, _c) in local_hits
                                   for x in (hs, he)})
                for a, b in zip(bounds, bounds[1:]):
                    if b <= a:
                        continue
                    classes = {k for (rng, k) in marks
                               if rng[0] <= a and b <= rng[1]}
                    if len(classes) > 1:
                        problems.append(f"同一片段被两类同时主张:{key}")
                    if "cell" in classes:
                        counts["cell_source_ref"] += b - a
                    elif "adorn" in classes:
                        counts["caption_unit_note_ref"] += b - a
                    elif key in paragraph_keys:
                        counts["final_paragraph_ref"] += b - a
                    else:
                        reason = _deferred_rejection_reason(
                            local_hits, binding_of, a, b)
                        if reason is None:
                            counts["registered_deferred"] += b - a
                            continue
                        covering = tuple(sorted({
                            entry_key for (entry_key, fragment_key, rng)
                            in gap_intervals
                            if fragment_key == key
                            and rng[0] <= a and b <= rng[1]}))
                        free_atoms.setdefault(key, []).append((a, b))
                        if len(covering) == 1:
                            counts["registered_deferred"] += b - a
                            continue
                        if len(covering) > 1:
                            problems.append(f"缺口区间重叠:{key}#{len(covering)}")
                        if TS.is_non_semantic_whitespace(span.text[a:b]):
                            counts["non_semantic_whitespace"] += b - a
                            continue
                        counts["residual_gap"] += b - a
                        problems.append(f"未解释残余:{key}#{reason}")
    # 逐条复核缺口的源区间：它**只能**覆盖"本来就无人认领"的字符。任何落在
    # cell / 表题 / 段落 主张里、或落在 component 已正式延期的区间里、或越出片段、
    # 或只盖住原子区间一部分的缺口区间，都在这里失败——这正是 §六"同一字符不得同时
    # 被 gap、TableObject 与 FinalSpan 消费"与"不得粗粒度整页延期"的独立判据。
    for entry_key, fragment_key, rng in gap_intervals:
        line = lines.get(fragment_key[:2])
        span = (line.spans[fragment_key[2]]
                if line is not None and fragment_key[2] < len(line.spans)
                else None)
        if span is None:
            problems.append(f"缺口区间指向不存在的排版片段:{entry_key}#{fragment_key}")
            continue
        lo = 0
        hi = int(span.char_end) - int(span.char_start)
        if rng[0] < lo or rng[1] > hi:
            problems.append(f"缺口区间越出片段:{entry_key}#{fragment_key}{rng}")
            continue
        covered = _free_atoms_cover(free_atoms.get(fragment_key, ()), rng)
        if covered is not True:
            problems.append(
                f"缺口区间覆盖了不该覆盖的字符:{entry_key}#{fragment_key}{rng}"
                f"（{covered}）")
    return counts, total, tuple(problems)


def _free_atoms_cover(atoms: Sequence[tuple], rng: tuple) -> Any:
    """`[rng)` 是否被 `atoms`（互不重叠的原子区间）**完整**覆盖。

    返回 `True` 表示完整覆盖；否则返回一句说明（未覆盖到的第一段），供问题码使用。
    完整覆盖而不是"有交集即算"：缺口区间必须逐字符对上"无人认领"的原子，任何半覆盖
    都意味着它顺手圈进了已被别处消费的字符。
    """
    pointer = rng[0]
    for a, b in sorted(atoms):
        if b <= pointer:
            continue
        if a > pointer:
            return f"未覆盖 {pointer}..{a}"
        pointer = max(pointer, b)
        if pointer >= rng[1]:
            return True
    if pointer >= rng[1]:
        return True
    return f"未覆盖 {pointer}..{rng[1]}"


def _deferred_rejection_reason(local_hits: Sequence[tuple], binding_of: dict,
                               a: int, b: int) -> str | None:
    """`registered_deferred` 的资格判定（复核侧独立实现，与 builder 同一语义）。

    返回 `None` 表示资格成立。四条件：覆盖区间者**恰好一个**、其终态为
    `pending`/`rejected`、其 binding 带 typed gap 码且属于 `CONSERVATION_DEFERRED_GAP_KINDS`、
    区间由该 component 的 `LayoutHit` 直接算出。
    """
    owners = {cid for (hs, he, cid) in local_hits if hs <= a and b <= he}
    if not owners:
        return "unowned"
    if len(owners) > 1:
        return "multi_owner"
    binding = binding_of.get(next(iter(owners)))
    if binding is None:
        return "no_binding"
    if binding.admission not in ("pending", "rejected"):
        return f"owner_consumed:{binding.admission}"
    if not any(code in TS.CONSERVATION_DEFERRED_GAP_KINDS
               for code in binding.gap_codes):
        return f"owner_ungapped:{binding.admission_reason}"
    return None


# --- gap / synopsis / 其它不变量 -------------------------------------------

def _gap_key(entry: Any) -> tuple:
    return (entry.gap_kind, entry.detail_code, entry.page_number or 0,
            entry.table_id or "", entry.component_id or "",
            entry.disposition_id or "")


def _check_gaps(snapshot: Any, roots: _Roots) -> None:
    p = _Problems()
    gaps = snapshot.gaps
    _revalidate(gaps, "TableStructureGap")
    keys = [_gap_key(e) for e in gaps.entries]
    if keys != sorted(keys):
        p.add("结构缺口必须按封闭排序键升序")
    if len(set(keys)) != len(keys):
        p.add("结构缺口不得重复（同一 key 只留一条）")
    blocking = 0
    node_ids = {n.node_id for n in roots.outline.nodes}
    _page_lines: dict = {}
    for _page in roots.page_layout.pages:
        for _line in _page.lines:
            _page_lines[(_page.page_number, _line.line_index)] = _line
    for entry in gaps.entries:
        _revalidate(entry, "TableGapEntry")
        if entry.gap_kind not in TS.TABLE_GAP_KINDS:
            p.add(f"缺口种类 {entry.gap_kind!r} 未登记")
            continue
        expected_blocking = entry.gap_kind in TS.GAP_BLOCKING_KINDS
        if entry.blocks_document_capability != expected_blocking:
            p.add(f"缺口 {entry.gap_kind!r} 的阻断标记必须为 "
                  f"{expected_blocking}（不得自行放宽）")
        blocking += 1 if entry.blocks_document_capability else 0
        if entry.table_id is not None and \
                snapshot.table_by_id(entry.table_id) is None:
            p.add(f"缺口引用了本快照以外的表 {entry.table_id!r}")
        if entry.component_id is not None and \
                entry.component_id not in roots.components_by_id:
            p.add(f"缺口引用了本快照以外的 component {entry.component_id!r}")
        # `tsg-2` 的精确源区间必须能对着**本份** PageLayout 逐条重算（§六）。指向不存在
        # 的页/行/片段、或越出片段字符范围的区间，在这里失败关闭——"内容寻址、可回读"
        # 指的就是这件事，而不是调用方自报一个哈希。
        for iv in entry.source_intervals:
            line = _page_lines.get(iv.fragment_key[:2])
            if line is None or iv.fragment_key[2] >= len(line.spans):
                p.add(f"缺口源区间指向不存在的排版片段：{_gap_key(entry)}"
                      f"#{iv.fragment_key}")
                continue
            span_text = line.spans[iv.fragment_key[2]].text
            a, b = int(iv.span_char_range[0]), int(iv.span_char_range[1])
            if a < 0 or b > len(span_text) or b <= a:
                p.add(f"缺口源区间越出片段字符范围：{_gap_key(entry)}"
                      f"#{iv.fragment_key}{iv.span_char_range}")
    if gaps.blocking_entry_count != blocking:
        p.add(f"blocking_entry_count 必须为实测值：{gaps.blocking_entry_count} "
              f"!= {blocking}")
    if snapshot.blocking_gap_count != gaps.blocking_entry_count:
        p.add("snapshot.blocking_gap_count 必须等于缺口的阻断条目数")
    # 被记为 pending 的 binding 必须留下缺口；阻断缺口必须留下如实记录。
    pending_ids = {b.component_id for b in snapshot.bindings
                   if b.admission == "pending"}
    gap_component_ids = {e.component_id for e in gaps.entries
                         if e.component_id is not None}
    missing = sorted(pending_ids - gap_component_ids)
    if missing:
        p.add(f"pending 的 component 必须留下结构缺口（缺 {missing[:8]}）")
    if not node_ids:
        p.add("经验证标题树不得为空（synopsis 无从生成）")
    p.raise_if_any("结构缺口：")


#: §18.8.6 情形 1/2（TS5 的 `table_material_available_no_text_synopsis`）：
#: 这两类理由**不得**声明任何来源 span（否则就是"把表格伪装成 final span"）。
#:
#: §19.0.1 方案 C：这里**只**列 final 轴自己的值。TS4 的 `table_only_pending_ts5`
#: 属于另一个类型、另一个词表，在这个模块里没有意义；把它列进来会让"两代简介"
#: 在同一张表里被接受。
_NO_SOURCE_SYNOPSIS_REASONS: tuple[str, ...] = (
    "no_span", "table_material_available_no_text_synopsis")


def _expected_synopsis_sources(snapshot: Any, node_id: Any) -> tuple:
    """独立复算：该节点**可采信** final span 的来源序 id 列表（§18.8.6）。"""
    picked = [s for s in snapshot.final_spans
              if getattr(s, "node_id", None) == node_id
              and isinstance(getattr(s, "normalized_text", None), str)
              and s.normalized_text != ""
              and s.is_citable()]
    picked.sort(key=lambda s: (s.start_page, s.start_line, s.span_locator))
    return tuple(s.span_id for s in picked)


def _expected_synopsis_reason(snapshot: Any, roots: _Roots,
                              node_id: Any) -> str | None:
    """独立复算 §18.8.6 / §19.10 的 `reason_code` 前四步（首个命中，全函数）。

    `None` 表示已越过情形 4：究竟是 `length_exceeded` 还是 `available` 取决于
    `ns-3` 的片段选择，本模块不重写那套算法，只锁住合法取值域。
    """
    counts: dict = {}
    for d in roots.dispositions:
        if getattr(d, "node_id", None) != node_id:
            continue
        kind = getattr(d, "range_kind", None)
        counts[kind] = counts.get(kind, 0) + 1
    regular = counts.get("regular", 0)
    table = counts.get("table_inside", 0) + counts.get("table_adjacency", 0)
    empty = counts.get("empty", 0)
    if regular == 0:
        if table == 0 and empty == 0:
            return "no_span"
        if table > 0:
            # §19.10：TS5 之后表格已成正式材料，不得再返回 `table_only_pending_ts5`。
            return "table_material_available_no_text_synopsis"
        return "empty_text"
    sources = _expected_synopsis_sources(snapshot, node_id)
    if not sources:
        return "no_span"
    if not any(snapshot.final_span_by_id(sid).is_citable() for sid in sources):
        return "alignment_failed"
    return None


def _check_synopses(snapshot: Any, roots: _Roots) -> None:
    p = _Problems()
    node_ids = sorted({n.node_id for n in roots.outline.nodes})
    got = [getattr(s, "node_id", None) for s in snapshot.synopses]
    if got != sorted(got):
        p.add("synopses 必须按 node_id 升序")
    if len(set(got)) != len(got):
        p.add("同一节点不得出现两条 final synopsis")
    if set(got) != set(node_ids):
        p.add("final synopsis 必须覆盖全部 affected 节点且不得越界：缺 "
              f"{sorted(set(node_ids) - set(got))[:8]} / 多 "
              f"{sorted(set(got) - set(node_ids))[:8]}")
    for syn in snapshot.synopses:
        # ① 类型与版本轴：**由本模块独立断言**，不采信成员自报的 schema_version。
        #    两代简介的版本轴不相交，因此"把 TS4 简介塞进 fms-1"在这里直接失败。
        if not isinstance(syn, TS.FinalNavigationSynopsis):
            p.add(f"synopses 成员必须是 FinalNavigationSynopsis，"
                  f"得到 {type(syn).__name__}"
                  f"（TS4 NavigationSynopsis 不得进入 final snapshot）")
            continue
        if syn.schema_version != V.FINAL_SYNOPSIS_SCHEMA_VERSION:
            p.add(f"final synopsis schema_version 必须为 "
                  f"{V.FINAL_SYNOPSIS_SCHEMA_VERSION!r}，"
                  f"得到 {syn.schema_version!r}")
        if syn.synopsis_version != V.FINAL_SYNOPSIS_VERSION:
            p.add(f"final synopsis synopsis_version 必须为 "
                  f"{V.FINAL_SYNOPSIS_VERSION!r}，得到 {syn.synopsis_version!r}")
        # ② 身份与内容指纹独立重算（挡 `object.__new__` 旁路与手改字段）。
        if syn.synopsis_locator != syn.derive_locator():
            p.add("final synopsis 的 synopsis_locator 与派生定位不一致")
        if syn.synopsis_id != syn.derive_id():
            p.add("final synopsis 的 synopsis_id 与派生身份不一致")
        _revalidate(syn, "FinalNavigationSynopsis")
        reason = getattr(syn, "reason_code", None)
        if reason is not None and reason not in _synopsis_reason_codes():
            p.add(f"synopsis reason_code {reason!r} 未登记")
        node_id = getattr(syn, "node_id", None)
        source_spans = tuple(getattr(syn, "source_final_span_ids", ()) or ())
        snippets = tuple(getattr(syn, "snippets", ()) or ())
        status = getattr(syn, "status", None)
        expected_reason = _expected_synopsis_reason(snapshot, roots, node_id)
        if expected_reason is None:
            # 情形 5/6 的分界取决于片段选择（`ns-3` 配额与长度约束），本模块不
            # 重写那套选择算法；但把"越过情形 4 之后"的合法取值锁成这一对。
            if reason not in (None, "length_exceeded"):
                p.add(f"节点 {node_id!r} 的 synopsis 已越过情形 4，reason_code 只能"
                      f"为 None（available）或 'length_exceeded'，得到 {reason!r}")
        elif reason != expected_reason:
            p.add(f"节点 {node_id!r} 的 synopsis reason_code 与独立复算不符："
                  f"{reason!r} != {expected_reason!r}")
        if status == "available" and not snippets:
            p.add(f"节点 {node_id!r} 的 synopsis 标记为 available 却没有片段")
        if status == "synopsis_unavailable" and snippets:
            p.add(f"节点 {node_id!r} 的 synopsis 标记为不可用却携带片段")
        # §18.8.6：情形 1/2 的 `source_span_ids` 必须为 `()`；情形 3/4/5 则**必须**
        # 恰为该节点全部可采信 final span 的来源序 id 列表——"不可用仍携带来源"是
        # 刻意的可审计缺口（`length_exceeded` 的极短节点即属此类），不是缺陷。
        if reason in _NO_SOURCE_SYNOPSIS_REASONS:
            if source_spans:
                p.add(f"节点 {node_id!r} 的 synopsis（{reason}）不得声明来源 span")
        elif not snippets:
            expected_sources = _expected_synopsis_sources(snapshot, node_id)
            if source_spans != expected_sources:
                p.add(f"节点 {node_id!r} 的 synopsis 来源列表与独立复算不符："
                      f"{source_spans[:4]} != {expected_sources[:4]}")
        for span_id in source_spans:
            span = snapshot.final_span_by_id(span_id)
            if span is None:
                p.add(f"synopsis 引用了本快照以外的 final span {span_id!r}")
            elif span.node_id != node_id:
                p.add(f"synopsis 引用了不属于本节点的 final span {span_id!r}")
        for snippet in snippets:
            span = snapshot.final_span_by_id(
                getattr(snippet, "final_span_id", None))
            if span is None:
                p.add("synopsis 片段引用了本快照以外的 final span")
                continue
            if span.node_id != node_id:
                p.add("synopsis 片段引用了不属于本节点的 final span")
            if snippet.final_span_id not in source_spans:
                p.add("synopsis 片段必须挂在已声明的来源 span 上")
            # 片段自己携带的来源 locator / wire 版本必须与解析到的 span 一致：
            # 否则"引用的是哪一份 span、按哪版解释"就无法复核。
            if snippet.final_span_locator != span.span_locator:
                p.add("synopsis 片段的 final_span_locator 与来源 span 不一致")
            if snippet.final_span_schema_version != span.schema_version:
                p.add("synopsis 片段的 final_span_schema_version 与来源 span 不一致")
            text = span.normalized_text
            if snippet.char_end > len(text):
                p.add(f"synopsis 片段越出 final span 文本范围"
                      f"（{snippet.char_start}:{snippet.char_end} vs {len(text)}）")
                continue
            # 抽取式回指：片段必须与 final span 原文**逐字符相等**，不得改写。
            if text[snippet.char_start:snippet.char_end] != snippet.text:
                p.add("final synopsis 必须是抽取式回指（片段与原文不等）")
    p.raise_if_any("final synopsis：")


def _synopsis_reason_codes() -> tuple:
    """final 轴的封闭词表（`nss-3` / `ns-3`）。

    §19.0.1 方案 C：刻意**不**读 TS4 的 `SYNOPSIS_REASON_CODES`。读它会引入
    `table_only_pending_ts5`，从而让"TS5 之后还在等 TS5"通过验证。
    """
    return tuple(TS.FINAL_SYNOPSIS_REASON_CODES)


def _check_no_embedded_identity(snapshot: Any) -> None:
    """成员不得把 snapshot 的身份写回自己（无环的第二道闸，逐成员独立复算）。"""
    forbidden = {snapshot.snapshot_locator, snapshot.snapshot_id,
                 snapshot.content_fingerprint}
    for name in ("tables", "final_spans", "decisions", "relations", "bindings",
                 "coverages"):
        for member in getattr(snapshot, name):
            blob = canonical_json(member.to_dict())
            for token in forbidden:
                if token and token in blob:
                    raise FinalVerificationError(
                        f"{name} 的成员反向引用了 final snapshot 的 {token!r}"
                        "（身份 DAG 不得回指终端节点）")


# ---------------------------------------------------------------------------
# 6. 唯一公开复核入口
# ---------------------------------------------------------------------------

def verify_final_material_snapshot(verified_span: Any, snapshot: Any
                                   ) -> VerifiedFinalMaterialStructureSnapshot:
    """**唯一**公开 TS5 复核入口：独立重建 final 对象后 canonical 全比较。

    只有全部复核通过才签发 capability。任一不符即抛 `FinalVerificationError`——
    不存在"带问题返回"的路径（那会变成实际上的放行）。
    """
    SS.issued_capability(verified_span, "VerifiedSpanSnapshot")
    scope = verified_span.issuer_scope
    if scope not in SS.ISSUER_SCOPES:
        raise FinalVerificationError(f"未登记的签发域 {scope!r}")
    if verified_span.issuer_version != V.VERIFIED_SPAN_SNAPSHOT_VERSION:
        raise FinalVerificationError(
            f"TS4 能力的签发域版本必须为 "
            f"{V.VERIFIED_SPAN_SNAPSHOT_VERSION!r}，得到 "
            f"{verified_span.issuer_version!r}")
    problems = verifier_independence_problems()
    if problems:
        raise FinalVerificationError(
            "复核器自身违反独立性约束，拒绝签发：" + "；".join(problems))
    profiles = load_table_profile_bundle()
    geometry = TG.extract_table_geometry(verified_span.handoff.layout_capability)
    roots = _Roots(verified_span, profiles, geometry)

    _check_root_bundle(snapshot, roots)
    facts = _check_tables(snapshot, roots)
    facts.update(_check_final_spans(snapshot, roots))
    facts.update(_check_decisions(snapshot, roots))
    facts.update(_check_bindings(snapshot, roots, facts))
    _check_coverages(snapshot, roots)
    _check_relations(snapshot, roots)
    _check_no_embedded_identity(snapshot)
    conservation_problems = _recheck_conservation(snapshot, roots)
    _check_gaps(snapshot, roots)
    _check_synopses(snapshot, roots)
    _refuse_if_blocked(snapshot)
    _refuse_if_conservation_ineligible(snapshot)

    fingerprint = _verification_fingerprint(snapshot, roots, scope)
    verified = VerifiedFinalMaterialStructureSnapshot(
        snapshot=snapshot, ts4=verified_span, scope=scope,
        source_kind=verified_span.source_kind, issuer_version=VERIFIER_VERSION,
        verification_fingerprint=fingerprint,
        problems=conservation_problems)
    return SS._issue_capability(verified, CAPABILITY_KIND, scope)


def blocking_gap_histogram(snapshot: Any) -> dict:
    """**独立**统计阻断性缺口（不复用 builder 的计数，只读条目自身字段）。

    供 runner 在签发被拒时写出诚实原因；也供 §19.17.4-14/15 的关闭门读回。
    """
    hist: dict = {}
    for entry in getattr(snapshot.gaps, "entries", ()):
        if getattr(entry, "blocks_document_capability", False):
            kind = entry.gap_kind
            hist[kind] = hist.get(kind, 0) + 1
    return hist


def _refuse_if_blocked(snapshot: Any) -> None:
    """阻断性缺口存在时**拒绝签发**（`FinalMaterialBlockedError`）。

    放在 `_check_gaps` 之后、构造 wrapper 之前：拒绝发生在能力对象诞生之前，
    因此不存在"已签发但带病"的中间态。
    """
    hist = blocking_gap_histogram(snapshot)
    if not hist:
        return
    detail = "、".join(f"{k}×{hist[k]}" for k in sorted(hist))
    raise FinalMaterialBlockedError(
        f"文档 {snapshot.document_id!r} 存在阻断性结构缺口（{detail}）："
        "§19.5.3 要求先由上游 successor 明确修复/重建，不得作为可保留 P2，"
        "因此不签发 final capability（fail-closed）",
        blocking_kinds=hist)


def _refuse_if_conservation_ineligible(snapshot: Any) -> None:
    """守恒资格不成立时**拒绝签发**（`FinalMaterialConservationError`）。

    `fmc-2` 起，"`problems` 非空 / 未解释残余非零 / 某层算术不守恒"不再是**只记录**
    的观察项：它们使 `conservation.balanced` 为假，而本函数据此在能力对象诞生之前
    拒绝签发。判据是共用的 `TS.conservation_eligible`，复核层不另立口径。

    拒绝理由逐层列出，并对 `layout_text` 层给出剩余未解释字符数——这是"文档能否
    进入下一阶段"的**内容**依据，不是重新解释预算。
    """
    conservation = snapshot.conservation
    if TS.conservation_eligible(conservation):
        return
    detail: list = []
    if tuple(conservation.problems):
        detail.append(f"文档级问题 {len(conservation.problems)} 条"
                      f"（如 {conservation.problems[0]}）")
    for layer in conservation.layers:
        values = TS.conservation_layer_values(layer)
        if TS.conservation_layer_eligible(
                layer.layer_kind, values,
                TS.conservation_term_value(layer.total), layer.problems):
            continue
        detail.append(f"{layer.layer_kind} 层不成立"
                      f"（本层问题 {len(layer.problems)} 条，"
                      f"residual_gap={values.get('residual_gap', 0)}，"
                      f"non_semantic_whitespace="
                      f"{values.get('non_semantic_whitespace', 0)}）")
    raise FinalMaterialConservationError(
        f"文档 {snapshot.document_id!r} 的最终材料守恒资格不成立"
        f"（{'；'.join(detail)}）：未解释的残余与层内问题必须先被解释或由上游修复，"
        "不得通过改名 / 排除 / 兜底项凑成 balanced，因此不签发 final capability"
        "（fail-closed）")


def _verification_fingerprint(snapshot: Any, roots: _Roots, scope: str) -> str:
    payload = [
        VERIFIER_VERSION, snapshot.snapshot_id, snapshot.content_fingerprint,
        snapshot.upstream_dependency_fingerprint,
        roots.snapshot.snapshot_id, roots.snapshot.content_fingerprint,
        roots.ts4.verification_fingerprint, roots.handoff.handoff_identity,
        scope, roots.ts4.source_kind,
    ]
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 7. 下游消费接口
# ---------------------------------------------------------------------------

def assert_final_material_capability(obj: Any,
                                     scopes: Sequence[str] | None = None) -> Any:
    """TS6 / TS7A / TS7B 的唯一合法入口检查：只接受已签发的能力对象。

    原始 TS4 快照、raw final JSON、或"字段长得一样"的自造对象一律拒绝。
    """
    return SS.issued_capability(obj, CAPABILITY_KIND, scopes)


def final_material_problems(verified: Any) -> tuple:
    """只读诊断：复核期记录到的诚实缺口清单（不构成放行依据）。"""
    assert_final_material_capability(verified)
    return tuple(verified.recheck_problems)


# ---------------------------------------------------------------------------
# 8. 自检与 CLI
# ---------------------------------------------------------------------------

def self_check() -> dict:
    problems: list[str] = list(verifier_independence_problems())
    if VERIFIER_VERSION != "vfmi-1":
        problems.append("复核域版本必须为 vfmi-1")
    if CAPABILITY_KIND not in SS.CAPABILITY_KINDS:
        problems.append(f"{CAPABILITY_KIND!r} 未登记在 CAPABILITY_KINDS")
    if set(UPSTREAM_FIELD_SOURCES) != set(TS.UPSTREAM_DEPENDENCY_FIELDS):
        missing = sorted(set(TS.UPSTREAM_DEPENDENCY_FIELDS) -
                         set(UPSTREAM_FIELD_SOURCES))
        extra = sorted(set(UPSTREAM_FIELD_SOURCES) -
                       set(TS.UPSTREAM_DEPENDENCY_FIELDS))
        problems.append(f"上游依赖字段表必须与 wire 层精确同集：缺 {missing} / "
                        f"多 {extra}")
    if __import__("os").path.basename(__file__) != "final_verifier.py":
        problems.append("模块文件名必须为 final_verifier.py")
    for name in ("to_dict", "__copy__", "__deepcopy__", "__reduce__"):
        if not callable(getattr(VerifiedFinalMaterialStructureSnapshot, name, None)):
            problems.append(f"VerifiedFinalMaterialStructureSnapshot 缺 {name}")
    if "__weakref__" not in VerifiedFinalMaterialStructureSnapshot.__slots__:
        problems.append("VerifiedFinalMaterialStructureSnapshot.__slots__ "
                        "必须含 __weakref__（否则无法登记为能力对象）")
    return {
        "module": "document_structure.final_verifier",
        "verifier_version": VERIFIER_VERSION,
        "capability_kind": CAPABILITY_KIND,
        "forbidden_modules": FORBIDDEN_DELEGATION_MODULES,
        "forbidden_symbols": FORBIDDEN_DELEGATION_SYMBOLS,
        "upstream_field_count": len(UPSTREAM_FIELD_SOURCES),
        "problems": problems,
    }


def _main(argv: Sequence[str]) -> int:
    import json
    import sys
    out = self_check()
    sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True,
                                indent=2) + "\n")
    return 1 if out["problems"] else 0


if __name__ == "__main__":  # pragma: no cover - 诊断入口
    import sys

    raise SystemExit(_main(sys.argv[1:]))

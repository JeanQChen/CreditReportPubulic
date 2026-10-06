# -*- coding: utf-8 -*-
"""TS4-A 的正式复核层（计划 §18.10 / §18.12 / §18.13）。

本模块**独立重算**整条 TS4 产物，再与待复核快照做 canonical 全等比较。因此：

- "删掉一个快照成员""多加一个成员""改一个字段再把所有 ID 与 hash 同步重算一遍"
  都过不了——它们能骗过快照**自身**的自证，却骗不过一次真正的重建；
- 复核路径**不**依赖当前 A/B 阶段：历史的 A 快照在未来的 B 环境里仍可核验（否则
  升阶段会把已冻结的历史产物全部作废），但它永远拿不到完成资格；
- `VerifiedSpanSnapshot` 是运行时能力，不可序列化、不可 copy / pickle。凭"字段
  长得一样"造不出它。

完成资格（`is_completion_eligible`）的阶段只由**快照自载的策略记录**决定（§18.1.1）：

- `distribution_only`（TS4-A）/ 未开启完成 / 阈值为 `None` ⇒ 一律 `False`，
  与全局常量无关，因此历史 A 快照在未来 B 环境里仍然恒假；
- `threshold_enabled`（TS4-B）⇒ 读**该快照自载的、身份与 authority 指纹绑定的**
  冻结策略阈值，再逐条检验该 span 的边界置信度与可引用覆盖。这一支**只**新增
  "资产身份 → 判定"的读取逻辑：TS4-B 只需冻结策略资产并重跑，不新增完成判据的
  核心业务逻辑（见 `evals/test_tree_span_completion.py` §7 的真值表）。
"""

from __future__ import annotations

import math
from typing import Any

from document_structure import span_builder as SB
from document_structure import span_policy as SP
from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    sha256_canonical,
)
from document_structure.span_schema import (
    SpanBuildSnapshot,
    SpanCitableCoverage,
    SpanEvidenceComponent,
    SpanQualificationPolicy,
    TrustedBuildInput,
    _issue_capability,
    effective_citable_intervals,
    interval_length,
    invert_intervals,
    issued_capability,
    merge_intervals,
    partition_problems,
    subtract_intervals,
)

__all__ = [
    "SpanVerificationError",
    "VerifiedSpanSnapshot",
    "verify_outline_structure_snapshot",
    "verify_span_snapshot",
    "is_completion_eligible",
    "self_check",
]


class SpanVerificationError(SchemaValidationError):
    """复核失败（类型不符 / 重建不等 / 指纹不符 / 越权的完成判定）。"""


# ---------------------------------------------------------------------------
# 1. 运行时能力对象
# ---------------------------------------------------------------------------

class VerifiedSpanSnapshot:
    """**已复核**的 TS4 span 快照能力（运行时对象，不可序列化）。

    它同时持有被复核的快照与当次使用的受信交接，并记录交接的签发域——因此
    "拿 `testing` 域的结果冒充验收/生产"在报告层可直接被看出来（`issuer_scope`）。
    """

    __slots__ = ("_snapshot", "_handoff", "_scope", "_source_kind",
                 "_issuer_version", "_verification_fingerprint", "__weakref__")

    def __init__(self, *, snapshot, handoff, scope, source_kind, issuer_version,
                 verification_fingerprint) -> None:
        self._snapshot = snapshot
        self._handoff = handoff
        self._scope = scope
        self._source_kind = source_kind
        self._issuer_version = issuer_version
        self._verification_fingerprint = verification_fingerprint

    @property
    def snapshot(self) -> SpanBuildSnapshot:
        return self._snapshot

    @property
    def handoff(self):
        return self._handoff

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

    def identity(self) -> dict:
        return {
            "issuer_scope": self._scope,
            "source_kind": self._source_kind,
            "issuer_version": self._issuer_version,
            "snapshot_id": self._snapshot.snapshot_id,
            "snapshot_content_fingerprint": self._snapshot.content_fingerprint,
            "handoff_identity": self._handoff.handoff_identity,
            "verification_fingerprint": self._verification_fingerprint,
        }

    def to_dict(self) -> dict:
        raise SpanVerificationError(
            "VerifiedSpanSnapshot 是运行时能力，不得序列化（fail-closed）")

    def __copy__(self):
        raise SpanVerificationError("VerifiedSpanSnapshot 不可 copy")

    def __deepcopy__(self, memo):
        raise SpanVerificationError("VerifiedSpanSnapshot 不可 deepcopy")

    def __reduce__(self):
        raise SpanVerificationError("VerifiedSpanSnapshot 不可 pickle")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<VerifiedSpanSnapshot scope={self._scope!r} "
                f"{self._snapshot.snapshot_id} "
                f"fp={self._verification_fingerprint[:12]}…>")


# ---------------------------------------------------------------------------
# 2. 差异定位（复核失败必须能说清"哪一项、第几个"）
# ---------------------------------------------------------------------------

def _first_difference(a: Any, b: Any, path: str) -> str | None:
    """返回第一处差异的可读路径；完全相等返回 `None`。"""
    if type(a) is not type(b):
        return f"{path}: 类型不同 {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        if set(a) != set(b):
            only_a = sorted(set(a) - set(b))
            only_b = sorted(set(b) - set(a))
            return f"{path}: 键集不同（重建多 {only_a} / 快照多 {only_b}）"
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


def _revalidate_trusted_input(raw: Any) -> TrustedBuildInput:
    """把快照里的封闭输入载荷**重新过一遍**它自己的构造校验。

    快照是冻结的 wire 对象，但 `object.__new__` 之类的旁路可以跳过 `__post_init__`。
    复核方因此不能假定"字段形状一定合法"，必须自己重建一次那个对象。
    """
    if not isinstance(raw, TrustedBuildInput):
        raise SpanVerificationError(
            f"trusted_input 必须为 TrustedBuildInput，得到 {type(raw).__name__}")
    rebuilt = TrustedBuildInput(
        raw.page_layout, raw.outline, raw.structure, raw.evidence, raw.terminals,
        raw.qualification, raw.rules, raw.handoff)
    if canonical_json(rebuilt.to_dict()) != canonical_json(raw.to_dict()):
        raise SpanVerificationError("trusted_input 的重新校验结果与其自身不一致")
    return rebuilt


# ---------------------------------------------------------------------------
# 3. 结构终态复核
# ---------------------------------------------------------------------------

def verify_outline_structure_snapshot(handoff) -> dict:
    """由受信版式重算结构终态，并与交接绑定的结构快照做 canonical 全等比较。"""
    issued_capability(handoff, "VerifiedTS3Handoff")
    rebuilt_outline, rebuilt_structure = SB._rebuild_outline_structure(
        handoff.layout_capability)
    bound = handoff.structure_snapshot
    problems: list = []
    difference = _first_difference(rebuilt_structure.to_dict(), bound.to_dict(),
                                   "OutlineStructureSnapshot")
    if difference is not None:
        problems.append(f"结构终态重建结果与绑定值不等：{difference}")
    if rebuilt_outline.outline_id != handoff.document_outline.outline_id:
        problems.append("重建出的大纲与交接绑定的不是同一份")
    return {
        "ok": not problems,
        "structure_snapshot_id": bound.structure_snapshot_id,
        "content_fingerprint": bound.content_fingerprint,
        "rebuilt_structure_snapshot_id": rebuilt_structure.structure_snapshot_id,
        "line_state_count": len(bound.line_states),
        "problem_count": len(problems),
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# 4. 正式复核入口
# ---------------------------------------------------------------------------

def verify_span_snapshot(snapshot: SpanBuildSnapshot, handoff) -> VerifiedSpanSnapshot:
    """**唯一**公开 TS4 复核入口：独立重建后 canonical 全等比较。

    复核路径从快照**自己载明**的策略出发（并只把它对回固定目录钉住的指纹），因此
    与当前 A/B 阶段无关；上游对象则一律取自受信交接，不采信快照里的副本。
    """
    issued_capability(handoff, "VerifiedTS3Handoff")
    if not isinstance(snapshot, SpanBuildSnapshot):
        raise SpanVerificationError(
            f"待复核对象必须为 SpanBuildSnapshot，得到 {type(snapshot).__name__}")
    # 1) 快照的封闭输入载荷必须先过它自己的构造校验，再取其记录的策略指纹。
    trusted = _revalidate_trusted_input(snapshot.trusted_input)
    expected_policy_fp = trusted.qualification[12]
    if not isinstance(expected_policy_fp, str) or len(expected_policy_fp) != 64:
        raise SpanVerificationError(
            "快照未记录合法的策略 provider authority 指纹（fail-closed）")
    # 2) 独立的输入指纹校验：不许"只把两个指纹字段改成自洽值"。
    recomputed_input_fp = sha256_canonical(trusted.to_dict())
    if snapshot.input_fingerprint != recomputed_input_fp:
        raise SpanVerificationError(
            f"input_fingerprint 不可复现：{snapshot.input_fingerprint!r} != "
            f"{recomputed_input_fp!r}（fail-closed）")
    # 3) 用受信交接重建整条产物，策略取快照自载的那一份。
    inp = SB._build_span_input(
        handoff, policy=snapshot.qualification_policy,
        expected_policy_authority_fingerprint=expected_policy_fp)
    rebuilt = SB._build_snapshot(inp)
    # 4) canonical 全等：任一成员被增删改（哪怕把所有 ID 与 hash 同步重算）都在此暴露。
    difference = _first_difference(rebuilt.to_dict(), snapshot.to_dict(),
                                   "SpanBuildSnapshot")
    if difference is not None:
        raise SpanVerificationError(
            f"独立重建结果与待复核快照不等（不得仅凭快照自证通过）：{difference}")
    if rebuilt.content_fingerprint != snapshot.content_fingerprint:
        raise SpanVerificationError("content_fingerprint 不可复现（fail-closed）")
    fingerprint = sha256_canonical([
        V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION, snapshot.snapshot_id,
        snapshot.input_fingerprint, snapshot.content_fingerprint,
        handoff.handoff_identity, handoff.issuer_scope, handoff.source_kind,
        handoff.issuer_version, expected_policy_fp,
    ])
    verified = VerifiedSpanSnapshot(
        snapshot=snapshot, handoff=handoff, scope=handoff.issuer_scope,
        source_kind=handoff.source_kind, issuer_version=V.VERIFIED_SPAN_SNAPSHOT_VERSION,
        verification_fingerprint=fingerprint)
    return _issue_capability(verified, "VerifiedSpanSnapshot", handoff.issuer_scope)


# ---------------------------------------------------------------------------
# 5. 完成资格
# ---------------------------------------------------------------------------

def _completion_threshold(verified: VerifiedSpanSnapshot) -> float | None:
    """快照自载的**完成阈值**；任一条件不成立返回 `None`（fail-closed）。

    这里只回答"这份快照是否自载了一份身份合法、可据以判定的冻结策略"。阶段由策略
    记录自身决定，**不**读 `versions.SPAN_CONFIDENCE_MIN`：全局常量只描述"当前构建
    处于哪一阶段"，历史快照必须按它自己那一版解释，否则升阶段就会改写历史结论。
    """
    snapshot = verified.snapshot
    if not isinstance(snapshot, SpanBuildSnapshot):
        return None
    policy = getattr(snapshot, "qualification_policy", None)
    if not isinstance(policy, SpanQualificationPolicy):
        return None
    if policy.stage != "threshold_enabled":
        return None
    if not policy.completion_enabled or not policy.set_complete_supported:
        return None
    threshold = policy.span_confidence_min
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        return None
    threshold = float(threshold)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        return None
    # 策略身份必须与快照记录的 provider authority 指纹逐字一致：自造策略、被替换过
    # 的策略、注册表条目被换掉的策略都拿不到同一个指纹。
    trusted = getattr(snapshot, "trusted_input", None)
    qualification = getattr(trusted, "qualification", None)
    if not isinstance(qualification, (list, tuple)) or len(qualification) <= 12:
        return None
    recorded = qualification[12]
    if not isinstance(recorded, str) or len(recorded) != 64:
        return None
    try:
        expected = SP.policy_provider_authority_fingerprint(policy)
    except Exception:  # noqa: BLE001 - 策略解析的任何异常都不得变成放行
        return None
    if expected != recorded:
        return None
    return threshold


def _is_formal_body_material(span: Any) -> bool:
    """该 span 是否是**正式正文材料**（非 fallback / 跨标题 / 未归属 / 非正文角色）。"""
    if getattr(span, "role", None) != "body":
        return False
    if getattr(span, "is_fallback", None) is not False:
        return False
    if getattr(span, "is_cross_heading", None) is not False:
        return False
    if getattr(span, "node_id", None) is None:
        return False
    if getattr(span, "unassigned_reason", None) is not None:
        return False
    if getattr(span, "span_builder_version", None) != V.TS4_BODY_SPAN_BUILDER_VERSION:
        return False
    return isinstance(getattr(span, "normalized_text", None), str)


def _covering_components_resolve(coverage: Any, span: Any,
                                 components_by_id: dict) -> bool:
    """`covering_component_ids` 必须逐个解析为该 span 的、已准入的正文落点组件。"""
    ids = getattr(coverage, "covering_component_ids", None)
    if not isinstance(ids, (list, tuple)) or not ids:
        return False
    if len(set(ids)) != len(ids):
        return False
    for component_id in ids:
        component = components_by_id.get(component_id)
        if not isinstance(component, SpanEvidenceComponent):
            return False
        if not component.admitted or component.landing != "body_span":
            return False
        if component.span_id != span.span_id:
            return False
    return True


def _coverage_supports_completion(coverage: Any, span: Any,
                                  components_by_id: dict) -> bool:
    """该 span 的覆盖是否**完整且自洽**。

    所有派生值都从基础区间（归一化 / 可引用 / 不可引用 / 未覆盖四分类）**重算**，
    绝不采信载荷里自报的派生结果；缺口指"必需内容没被可引用区间覆盖"，
    而不是"有效区间没铺到域尾"（§18.8.3）。
    """
    if not isinstance(coverage, SpanCitableCoverage):
        return False
    if getattr(coverage, "span_id", None) != span.span_id:
        return False
    if getattr(coverage, "span_builder_version", None) \
            != V.TS4_BODY_SPAN_BUILDER_VERSION:
        return False
    if tuple(getattr(coverage, "problems", ()) or ()):
        return False
    length = len(span.normalized_text)
    if getattr(coverage, "span_local_length", None) != length:
        return False
    for name in ("normalization_only_intervals", "citable_source_intervals",
                 "non_citable_source_intervals", "uncovered_source_intervals"):
        value = getattr(coverage, name, None)
        if not isinstance(value, (list, tuple)):
            return False
        try:
            if tuple(merge_intervals(value)) != tuple(value):
                return False
        except Exception:  # noqa: BLE001 - 非法区间一律不放行
            return False
    norm = tuple(getattr(coverage, "normalization_only_intervals"))
    citable = tuple(getattr(coverage, "citable_source_intervals"))
    non_citable = tuple(getattr(coverage, "non_citable_source_intervals"))
    uncovered = tuple(getattr(coverage, "uncovered_source_intervals"))
    try:
        if partition_problems((("normalization_only", norm), ("citable_source", citable),
                               ("non_citable_source", non_citable),
                               ("uncovered_source", uncovered)), length):
            return False
        required = invert_intervals(norm, length)
        effective = effective_citable_intervals(norm, citable, non_citable,
                                                uncovered, length)
    except Exception:  # noqa: BLE001 - 重算失败即不放行
        return False
    # 不可引用 / 未覆盖区间必须**都为空**：任一非空即成缺口。
    if non_citable or uncovered:
        return False
    # 必需内容不得为空（否则"全覆盖"是空断言）。
    if not required:
        return False
    # 自报派生值与重算结果必须逐字一致。
    if tuple(getattr(coverage, "required_content_intervals", ())) != tuple(required):
        return False
    if tuple(getattr(coverage, "effective_citable_intervals", ())) != tuple(effective):
        return False
    for name, intervals in (
            ("normalization_only_chars", norm),
            ("required_content_chars", tuple(required)),
            ("citable_source_chars", citable),
            ("non_citable_source_chars", non_citable),
            ("uncovered_source_chars", uncovered),
            ("effective_citable_chars", tuple(effective))):
        if getattr(coverage, name, None) != interval_length(intervals):
            return False
    # 必需内容必须 100% 落在可引用区间里，且有效区间不得短于必需内容。
    if subtract_intervals(required, citable):
        return False
    if subtract_intervals(required, effective):
        return False
    return _covering_components_resolve(coverage, span, components_by_id)


def is_completion_eligible(verified: VerifiedSpanSnapshot, span_id: str) -> bool:
    """某个 span 是否具备**完成**资格（`set_complete` 的前置）。

    唯一完成判据（§18.8.2 / §18.10）。TS4-A 恒为 `False`：分布口径策略
    `completion_enabled` 为假。该判定只看**快照自载的策略记录**，因此历史的 A 快照
    无论被放在 A 还是 B 环境里复核，结论都是 `False`。

    `threshold_enabled`（TS4-B）分支要求同时成立：快照自载策略经注册表钉住的
    authority 指纹校验、`span_id` 与覆盖唯一存在、边界置信度达到该策略阈值、
    span 是正式正文材料、覆盖四分类闭合且必需内容被可引用区间**全覆盖**、
    `covering_component_ids` 逐个解析为已准入的正文落点组件。任一条件不成立即
    `False`——本判据对"不合格"永不抛异常，只对**越权调用**抛异常。
    """
    issued_capability(verified, "VerifiedSpanSnapshot")
    if not isinstance(span_id, str) or span_id == "":
        raise SpanVerificationError("span_id 必须为非空字符串")
    threshold = _completion_threshold(verified)
    if threshold is None:
        return False
    snapshot = verified.snapshot
    try:
        spans = tuple(getattr(snapshot, "spans", ()))
        coverages = tuple(getattr(snapshot, "coverages", ()))
        components = tuple(getattr(snapshot, "components", ()))
    except Exception:  # noqa: BLE001 - 敌意成员一律不放行
        return False
    matched_spans = [s for s in spans if getattr(s, "span_id", None) == span_id]
    if len(matched_spans) != 1:
        return False
    matched_coverages = [c for c in coverages
                         if getattr(c, "span_id", None) == span_id]
    if len(matched_coverages) != 1:
        return False
    span = matched_spans[0]
    confidence = getattr(span, "confidence", None)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    if not math.isfinite(float(confidence)) or float(confidence) < threshold:
        return False
    if not _is_formal_body_material(span):
        return False
    components_by_id: dict = {}
    for component in components:
        component_id = getattr(component, "component_id", None)
        if component_id is not None and component_id not in components_by_id:
            components_by_id[component_id] = component
    return _coverage_supports_completion(matched_coverages[0], span,
                                         components_by_id)


# ---------------------------------------------------------------------------
# 6. 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    problems: list[str] = []
    if not isinstance(V.VERIFIED_SPAN_SNAPSHOT_VERSION, str) \
            or V.VERIFIED_SPAN_SNAPSHOT_VERSION == "":
        problems.append("versions.VERIFIED_SPAN_SNAPSHOT_VERSION 缺失")
    table = SP.ab_gate_truth_table()
    # 完成资格只在**当前阶段**是 A 时才必须关闭；B 阶段开的是"可判定"，不是任何
    # span / aspect 的自动完成（阈值只是必要条件）。
    if table["stage"] == "distribution_only":
        if table["completion_enabled"]:
            problems.append("本轮（TS4-A）不得开启完成资格")
        if V.SPAN_CONFIDENCE_MIN is not None:
            problems.append(
                f"TS4-A 要求 SPAN_CONFIDENCE_MIN 保持 None，得到 "
                f"{V.SPAN_CONFIDENCE_MIN!r}")
    elif not (V.SPAN_CONFIDENCE_MIN is not None and table["completion_enabled"]
              and table["set_complete_supported"]):
        problems.append(
            f"TS4-B 要求 SPAN_CONFIDENCE_MIN 为有限阈值且开启完成判定，得到 "
            f"{V.SPAN_CONFIDENCE_MIN!r}")
    for name in ("to_dict", "__copy__", "__deepcopy__", "__reduce__"):
        if not callable(getattr(VerifiedSpanSnapshot, name, None)):
            problems.append(f"VerifiedSpanSnapshot 缺 {name}")
    if "__weakref__" not in VerifiedSpanSnapshot.__slots__:
        problems.append("VerifiedSpanSnapshot.__slots__ 必须含 __weakref__"
                        "（否则无法登记为能力对象）")
    return {
        "verifier_version": V.VERIFIED_SPAN_SNAPSHOT_VERSION,
        "stage": table["stage"],
        "completion_enabled": table["completion_enabled"],
        "span_confidence_min": V.SPAN_CONFIDENCE_MIN,
        "problems": problems,
    }

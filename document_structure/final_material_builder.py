# -*- coding: utf-8 -*-
"""TS5 §19.3–§19.10：final material 层的**唯一**正式构建器（`fmb-2`）。

唯一 public 入口是 `build_final_material_snapshot(verified_span)`。它只接受**已签发**
的 `VerifiedSpanSnapshot`：不接受 JSON、目录路径、裸 dict 或自报字段，也不重读 PDF
路径。几何一律从 `handoff.layout_capability.source_bytes` 就地抽取（TOCTOU-safe：
字节在签发时已被保留，扫描后路径换内容也影响不了这里）。

职责（§19.11.1）：

- §19.3.2 全部根交叉核对（wrapper issuer/scope/version、TS4 快照身份、PageLayout /
  DocumentOutline / 结构终态身份、Evidence 快照与成员、alignment terminal 集合、
  TS4-B 冻结策略（键 / 指纹 / 阈值 / 12 因子）、raw PDF SHA256、geometry settings）；
- `sbf-1` final paragraph span：由真实 TS4 components **重新生成身份**，不复用
  `os-4` / `sb-7`，并重算 12 个边界因子与 0.85 必要阈值；
- §19.7.1 的 8 类 decision 落地（`plan_decisions` 的草稿 → 完整 `TableRangeDecision`）；
- §19.9.1 的 11 类 component landing **唯一封闭映射** → `FinalComponentBinding`；
- §19.9.2 的逐 source ref `TableCitableCoverage`；
- §19.9.3 的四层守恒（分别验证，不得跨层相加）；
- §19.8 `introduces` / `explains` / `references` 关系与结构缺口台账；
- §19.10 `ns-3` final synopsis 与终端 `FinalMaterialStructureSnapshot`。

**不在这里**：capability 签发（见 `final_verifier`）、验收 runner、人工 review。
本模块不写任何 DB，不调用 LLM / 网络 / OCR，也不构造第二套 Router / Harness。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import span_policy as SP
from . import span_schema as SS
from . import synopsis as SY
from . import table_builder as TB
from . import table_geometry as TG
from . import table_schema as TS
from . import versions as V
from .canonical import (
    SchemaValidationError,
    canonical_json,
    canonical_text,
    identity,
    locator,
)
from .normalization import tight
from .schema import quantize
from .span_schema import BoundaryFactor
from .table_classification import load_table_profile_bundle

#: 本构建器的组装规则版本（`fmb-2`）。
BUILDER_VERSION = V.FINAL_MATERIAL_BUILDER_VERSION

#: final capability 的签发域版本（`vfmi-1`）。
ISSUER_VERSION = V.VERIFIED_FINAL_MATERIAL_ISSUER_VERSION

#: final capability 的种类（登记在 `span_schema.CAPABILITY_KINDS`）。
CAPABILITY_KIND = "VerifiedFinalMaterialStructureSnapshot"

#: final span 的构造算法版本（`sbf-1`）。
FINAL_SPAN_BUILDER_VERSION = V.TS5_FINAL_SPAN_BUILDER_VERSION

#: §19.7.1 的八类裁决按**目标**三分（直接取自 wire 层真值表，不另立字符串前缀）：
#: 目标为 table 的"被吸收"四种、目标为 final_span 的"保留为正文"两种、目标为
#: none 的两种结构裁决。三组必须**穷尽且互斥** `TABLE_DECISION_KINDS`（`self_check`
#: 强制）。本批 `plan_decisions` 只产出 absorbed / unsupported / unresolved，
#: 但另两种仍必须被映射穷尽，否则拒绝 —— 不得靠"样本里没出现"来豁免。
ABSORBED_DECISIONS: tuple[str, ...] = tuple(
    d for d, t in TS.TABLE_DECISION_TARGETS.items() if t == "table")
KEPT_DECISIONS: tuple[str, ...] = tuple(
    d for d, t in TS.TABLE_DECISION_TARGETS.items() if t == "final_span")
STRUCTURAL_DECISIONS: tuple[str, ...] = tuple(
    d for d, t in TS.TABLE_DECISION_TARGETS.items() if t == "none")

#: §19.9.1：landing → rejected 理由的**唯一**映射（与 wire 层封闭集合逐键一致）。
_REJECTED_REASON_OF_LANDING: dict[str, str] = {
    "body_empty": "empty_source_text",
    "heading_node": "structural_heading_only",
    "non_content": "non_content_region",
    "outside_body": "outside_formal_body",
}

#: 无可核验 layout fragment 的两种 landing：永远 pending / 不可引用。
_NO_FRAGMENT_LANDINGS: tuple[str, ...] = (
    "alignment_offset_unverifiable", "alignment_residue_unmapped",
)

#: `SpanEvidenceComponent.admission_reason` → `CitableInterval.reason` 的**全函数**
#: 封闭映射。`aligned_projected` 是被准入的那一项（`aligned_fragment_closed`），
#: 其余六项各归其因。映射必须覆盖 `COMPONENT_ADMISSION_REASONS` 全集。
_CITABLE_REASON_OF_ADMISSION: dict[str, str] = {
    "verdict_not_aligned": "verdict_not_aligned",
    "refusal_record": "refusal_record",
    "offset_unverifiable": "offset_unverifiable",
    "boundary_inexact": "fragment_not_closed",
    "landing_not_body_span": "component_not_admitted",
    "residue_unmapped": "residue_unmapped",
}


class FinalMaterialBuildError(SchemaValidationError):
    """final material 构建失败（fail-closed，不产出可误用的半成品）。"""


def _err(message: str) -> None:
    raise FinalMaterialBuildError(message)


def _require(cond: bool, message: str) -> None:
    if not cond:
        _err(message)


def _overlap_area(a: Sequence[float], b: Sequence[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0.0 or h <= 0.0:
        return 0.0
    return w * h


def _tight_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if not isinstance(text, str):
        _err(f"Evidence block {getattr(block, 'evidence_block_id', '?')!r} "
             "的 text 必须为字符串")
    return tight(text)


def _alignment_id_of(terminals: Sequence[Any]) -> str:
    """对齐终态集合的**不可变修订身份**（与 TS4 同一口径重算）。

    `EvidenceSetAlignment` 本身没有 `alignment_id`：它是 TS4 由全部终态 id 定型排序后
    派生出来的身份。TS5 因此**重算**它，而不是采信快照里自报的那个字符串。
    """
    if not terminals:
        _err("没有对齐终态，无法确定 alignment_id（fail-closed）")
    ids = sorted(t.terminal_id for t in terminals)
    return identity("als", {"terminal_ids": ids,
                            "schema_version": V.ALIGN_SCHEMA_VERSION})


def _same_scope(obj: Any, kind: str, scope: str) -> None:
    """要求 `obj` 是本进程签发的 `kind` 能力，且签发域与 wrapper 一致。"""
    SS.issued_capability(obj, kind)
    got = SS.capability_scope(obj)
    if got != scope:
        _err(f"{kind} 的签发域为 {got!r}，与 wrapper 的 {scope!r} 不一致"
             "（live / pinned_acceptance / testing 严格隔离，fail-closed）")


# ---------------------------------------------------------------------------
# 1. 运行时根束（不序列化、无 wire 版本）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinalMaterialBuildInput:
    """一次构建所用的**全部**已核验根（运行时只读，不是 wire 类型）。

    每个字段都由 `_read_roots` 逐项交叉核对后才填：调用方无法只提供其中一部分就
    构造出正式输入，也无法把一个"看起来同形"的对象塞进正式链路。
    """

    issuer_scope: str
    source_kind: str
    issuer_version: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    outline_locator: str
    structure_snapshot_id: str
    alignment_id: str
    verified_span_snapshot_id: str
    verified_span_input_fingerprint: str
    verified_span_content_fingerprint: str
    verified_span_verification_fingerprint: str
    handoff_identity: str
    page_layout_schema_version: str
    outline_schema_version: str
    span_schema_version: str
    span_build_snapshot_schema_version: str
    span_builder_version: str
    source_file_sha256: str
    page_layout: Any
    outline: Any
    structure_snapshot: Any
    evidence_blocks: tuple
    terminals: tuple
    components: tuple
    dispositions: tuple
    spans: tuple
    policy: Any
    profiles: Any
    geometry: Any
    upstream_dependency_fingerprint: str
    blocks_by_id: dict = field(default_factory=dict, repr=False, compare=False)
    block_order: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("evidence_blocks", "terminals", "components", "dispositions",
                     "spans"):
            if not isinstance(getattr(self, name), tuple):
                _err(f"FinalMaterialBuildInput.{name} 必须为元组")
        for name in ("upstream_dependency_fingerprint", "source_file_sha256"):
            if len(getattr(self, name)) != 64:
                _err(f"FinalMaterialBuildInput.{name} 必须为 sha256")


# ---------------------------------------------------------------------------
# 2. §19.3.2 根交叉核对
# ---------------------------------------------------------------------------

def _check_policy(snapshot: Any, handoff: Any) -> None:
    policy = handoff.qualification_policy
    _require(isinstance(policy, SS.SpanQualificationPolicy),
             "qualification_policy 必须为 SpanQualificationPolicy")
    _require(V.SPAN_CONFIDENCE_MIN is not None,
             "当前阶段没有裁决 0.85 必要阈值：TS5 不得在 distribution_only 下放行")
    _require(policy.stage == SP.ab_gate_truth_table()["stage"],
             f"冻结策略阶段 {policy.stage!r} 与当前 A/B 阶段不一致")
    _require(bool(policy.completion_enabled) and bool(policy.set_complete_supported),
             "TS5 需要同时开启 completion 与 set_complete 的冻结策略")
    _require(policy.span_confidence_min is not None and
             quantize(policy.span_confidence_min) == quantize(V.SPAN_CONFIDENCE_MIN),
             "冻结策略的必要阈值必须等于已裁决的 SPAN_CONFIDENCE_MIN："
             f"{policy.span_confidence_min!r} != {V.SPAN_CONFIDENCE_MIN!r}")
    _require(policy.policy_key == SP.current_policy_key(),
             f"当前阶段策略键必须为 {SP.current_policy_key()!r}，"
             f"得到 {policy.policy_key!r}")
    got = tuple((f.side, f.cause, quantize(f.factor)) for f in policy.factor_entries)
    expected = tuple((s, c, quantize(f)) for (s, c, f) in SP.TS4_A_FACTOR_VALUES)
    _require(got == expected,
             "冻结策略的 12 个边界因子偏离已批准表（不得只改单项 / 换序 / 增删）")
    _require(handoff.policy_provider_authority_fingerprint ==
             SP.policy_provider_authority_fingerprint(policy),
             "策略的 provider authority 指纹与现场重算不一致")
    _require(canonical_json(policy.to_dict()) ==
             canonical_json(snapshot.qualification_policy.to_dict()),
             "handoff 的冻结策略与 TS4 快照内嵌策略不一致（不得混用两次构建）")


def _read_roots(verified_span: Any) -> FinalMaterialBuildInput:
    """§19.3.2：从唯一正式输入读出并交叉核对全部根对象。"""
    _require(type(verified_span).__name__ == "VerifiedSpanSnapshot",
             "正式输入必须是 VerifiedSpanSnapshot（不得旁路构造）")
    SS.issued_capability(verified_span, "VerifiedSpanSnapshot")
    scope = verified_span.issuer_scope
    _require(scope in SS.ISSUER_SCOPES,
             f"issuer_scope 必须属于 {SS.ISSUER_SCOPES}，得到 {scope!r}")
    _require(verified_span.issuer_version == V.VERIFIED_SPAN_SNAPSHOT_VERSION,
             "TS4 wrapper 的签发域版本与登记不符")
    snapshot = verified_span.snapshot
    handoff = verified_span.handoff

    for obj, kind in ((handoff, "VerifiedTS3Handoff"),
                      (handoff.layout_capability, "VerifiedPageLayout"),
                      (handoff.alignment, "VerifiedEvidenceSetAlignment"),
                      (handoff.evidence_authority,
                       "VerifiedCurrentEvidenceAuthority")):
        _same_scope(obj, kind, scope)

    page_layout = handoff.page_layout
    outline = handoff.document_outline
    structure_snapshot = handoff.structure_snapshot
    alignment_cap = handoff.alignment
    alignment = alignment_cap.alignment
    evidence_snapshot = handoff.evidence_snapshot
    evidence_blocks = tuple(handoff.evidence_blocks)
    terminals = tuple(alignment_cap.terminals)

    _require(alignment_cap.page_layout.page_layout_id ==
             page_layout.page_layout_id,
             "alignment 的 PageLayout 与 handoff 的不是同一个")
    _require(alignment_cap.evidence_snapshot.evidence_set_version ==
             evidence_snapshot.evidence_set_version,
             "alignment 的 Evidence 快照与 handoff 的不是同一次快照")

    # --- TS4 快照身份 ---------------------------------------------------
    _require(snapshot.document_id == page_layout.document_id,
             "TS4 快照与 PageLayout 的 document_id 不一致")
    _require(snapshot.document_id == outline.document_id,
             "TS4 快照与 DocumentOutline 的 document_id 不一致")
    _require(snapshot.document_version == page_layout.document_version,
             "TS4 快照与 PageLayout 的 document_version 不一致")
    _require(snapshot.document_version == outline.document_version,
             "TS4 快照与 DocumentOutline 的 document_version 不一致")
    _require(snapshot.page_layout_id == page_layout.page_layout_id,
             "TS4 快照的 page_layout_id 与现场版式不一致")
    _require(snapshot.outline_id == outline.outline_id,
             "TS4 快照的 outline_id 与现场标题树不一致")
    alignment_id = _alignment_id_of(terminals)
    _require(snapshot.alignment_id == alignment_id,
             "TS4 快照的 alignment_id 与现场对齐终态集合重算结果不一致"
             f"（{snapshot.alignment_id!r} != {alignment_id!r}）")
    _require(snapshot.structure_snapshot_id ==
             structure_snapshot.structure_snapshot_id,
             "TS4 快照的 structure_snapshot_id 与现场结构终态不一致")
    _require(outline.page_layout_id == page_layout.page_layout_id,
             "DocumentOutline 不属于本 PageLayout")
    _require(structure_snapshot.page_layout_id == page_layout.page_layout_id,
             "结构终态不属于本 PageLayout")
    _require(structure_snapshot.outline_id == outline.outline_id,
             "结构终态不属于本标题树")
    _require(structured_snapshot_verified(snapshot),
             "TS4 快照不是当前 spn-1 / sb-7 的产物（fail-closed）")

    # --- Evidence 成员与 terminal 集合 ----------------------------------
    evidence_set_version = evidence_snapshot.evidence_set_version
    _require(snapshot.document_id == evidence_snapshot.document_id,
             "TS4 快照与 Evidence 快照的 document_id 不一致")
    _require(snapshot.document_version == evidence_snapshot.document_version,
             "TS4 快照与 Evidence 快照的 document_version 不一致")
    block_ids = [b.evidence_block_id for b in evidence_blocks]
    _require(len(set(block_ids)) == len(block_ids), "Evidence blocks 不得重复")
    _require(all(b.evidence_set_version == evidence_set_version
                 for b in evidence_blocks),
             "Evidence blocks 不属于同一次 Evidence 集合")
    member_ids = {m.evidence_id for m in evidence_snapshot.members}
    _require(member_ids == set(block_ids),
             "Evidence 快照成员与现场 blocks 不是同一个集合（fail-closed）")
    _require(snapshot.terminal_count == len(terminals),
             "TS4 快照的 terminal_count 与现场 terminal 集合长度不一致")
    _require(sorted(t.evidence_block_id for t in terminals) == sorted(block_ids),
             "alignment terminal 集合与 Evidence blocks 不是一一对应")

    # --- TS4-B 冻结策略 -------------------------------------------------
    _check_policy(snapshot, handoff)

    # --- TS4 内部终态（components / dispositions / spans） --------------
    for c in snapshot.components:
        _require(c.schema_version == V.SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION,
                 "component 的 schema 版本不是当前 spc-1")
        _require(c.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION,
                 "component 不是当前 TS4 正文算法的产物")
    for d in snapshot.dispositions:
        _require(d.schema_version == V.BODY_RANGE_DISPOSITION_SCHEMA_VERSION,
                 "disposition 的 schema 版本不是当前 spd-1")
        _require(d.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION,
                 "disposition 不是当前 TS4 正文算法的产物")
    for sp in snapshot.spans:
        _require(sp.document_id == page_layout.document_id,
                 "TS4 span 不属于本文档")
        _require(sp.document_version == page_layout.document_version,
                 "TS4 span 不属于本文档版本")

    # --- raw PDF 字节与几何口径（TOCTOU-safe） --------------------------
    raw = bytes(handoff.layout_capability.source_bytes)
    _require(bool(raw), "VerifiedPageLayout.source_bytes 必须为非空字节")
    measured_pdf = hashlib.sha256(raw).hexdigest()
    _require(measured_pdf == page_layout.source_file_sha256,
             "受信字节的 SHA256 与 PageLayout 自报值不一致（fail-closed）")
    geometry = TG.extract_table_geometry(handoff.layout_capability)
    _require(geometry.page_layout_id == page_layout.page_layout_id,
             "几何报告的 page_layout_id 与现场版式不一致")
    _require(geometry.source_file_sha256 == page_layout.source_file_sha256,
             "几何报告绑定的 PDF 字节与现场版式不一致")
    _require(geometry.settings.geometry_version == V.TABLE_GEOMETRY_VERSION,
             "几何 settings 的版本不是当前 tgeo-1")

    # --- profile 资产 ---------------------------------------------------
    profiles = load_table_profile_bundle()
    _require(profiles.classification.profile_version ==
             V.TABLE_CLASSIFICATION_PROFILE_VERSION,
             "classification profile 版本与登记不一致")
    _require(profiles.cell_block.profile_version ==
             V.TABLE_CELL_BLOCK_PROFILE_VERSION,
             "cell block profile 版本与登记不一致")

    upstream = TS.upstream_dependency_fingerprint({
        "document_id": page_layout.document_id,
        "document_version": page_layout.document_version,
        "evidence_set_version": evidence_set_version,
        "page_layout_id": page_layout.page_layout_id,
        "outline_id": outline.outline_id,
        "alignment_id": alignment_id,
        "verified_span_snapshot_id": snapshot.snapshot_id,
        "verified_span_input_fingerprint": snapshot.input_fingerprint,
        "verified_span_content_fingerprint": snapshot.content_fingerprint,
        "verified_span_verification_fingerprint":
            verified_span.verification_fingerprint,
        "page_layout_schema_version": page_layout.schema_version,
        "outline_schema_version": outline.schema_version,
        "span_schema_version": V.SPAN_SCHEMA_VERSION,
        "span_build_snapshot_schema_version": snapshot.schema_version,
        "span_builder_version": snapshot.span_builder_version,
        "handoff_identity": handoff.handoff_identity,
        "table_schema_version": V.TABLE_SCHEMA_VERSION,
        "table_builder_version": V.TABLE_BUILDER_VERSION,
        "table_cell_schema_version": V.TABLE_CELL_SCHEMA_VERSION,
        "final_span_schema_version": V.FINAL_SPAN_SCHEMA_VERSION,
        "ts5_final_span_builder_version": FINAL_SPAN_BUILDER_VERSION,
        "table_relation_schema_version": V.TABLE_RELATION_SCHEMA_VERSION,
        "table_relation_builder_version": V.TABLE_RELATION_BUILDER_VERSION,
        "table_range_decision_schema_version":
            V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
        "final_component_binding_schema_version":
            V.FINAL_COMPONENT_BINDING_SCHEMA_VERSION,
        "table_citable_coverage_schema_version":
            V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION,
        "table_structure_gap_schema_version":
            V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION,
        "final_material_conservation_schema_version":
            V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION,
        "final_material_structure_schema_version":
            V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION,
        "table_geometry_version": geometry.settings.geometry_version,
        "geometry_settings_fingerprint": geometry.settings.settings_fingerprint,
        "table_classification_profile_version":
            profiles.classification.profile_version,
        "classification_profile_file_fingerprint":
            profiles.classification_file_fingerprint(),
        "classification_profile_content_fingerprint":
            profiles.classification_content_fingerprint(),
        "table_cell_block_profile_version": profiles.cell_block.profile_version,
        "cell_block_profile_file_fingerprint":
            profiles.cell_block_file_fingerprint(),
        "cell_block_profile_content_fingerprint":
            profiles.cell_block_content_fingerprint(),
        "qualification_policy_key": handoff.qualification_policy.policy_key,
        "qualification_policy_fingerprint":
            handoff.qualification_policy.policy_fingerprint,
    })

    return FinalMaterialBuildInput(
        issuer_scope=scope, source_kind=verified_span.source_kind,
        issuer_version=verified_span.issuer_version,
        document_id=page_layout.document_id,
        document_version=page_layout.document_version,
        evidence_set_version=evidence_set_version,
        page_layout_id=page_layout.page_layout_id,
        outline_id=outline.outline_id, outline_locator=outline.outline_locator,
        structure_snapshot_id=structure_snapshot.structure_snapshot_id,
        alignment_id=alignment_id,
        verified_span_snapshot_id=snapshot.snapshot_id,
        verified_span_input_fingerprint=snapshot.input_fingerprint,
        verified_span_content_fingerprint=snapshot.content_fingerprint,
        verified_span_verification_fingerprint=
            verified_span.verification_fingerprint,
        handoff_identity=handoff.handoff_identity,
        page_layout_schema_version=page_layout.schema_version,
        outline_schema_version=outline.schema_version,
        span_schema_version=V.SPAN_SCHEMA_VERSION,
        span_build_snapshot_schema_version=snapshot.schema_version,
        span_builder_version=snapshot.span_builder_version,
        source_file_sha256=page_layout.source_file_sha256,
        page_layout=page_layout, outline=outline,
        structure_snapshot=structure_snapshot, evidence_blocks=evidence_blocks,
        terminals=terminals, components=tuple(snapshot.components),
        dispositions=tuple(snapshot.dispositions),
        spans=tuple(snapshot.spans),
        policy=handoff.qualification_policy, profiles=profiles,
        geometry=geometry, upstream_dependency_fingerprint=upstream,
        blocks_by_id={b.evidence_block_id: b for b in evidence_blocks},
        block_order={b.evidence_block_id: (b.page_number, b.block_index)
                     for b in evidence_blocks})


def structured_snapshot_verified(snapshot: Any) -> bool:
    """TS4 快照必须是当前 `spn-1` + `sb-7`，且内嵌策略版本一致。"""
    return (snapshot.schema_version == V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION
            and snapshot.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION
            and snapshot.qualification_policy_version ==
            V.SPAN_QUALIFICATION_POLICY_VERSION)


def _build_context(root: FinalMaterialBuildInput) -> TB.TableBuildContext:
    return TB.TableBuildContext(
        document_id=root.document_id, document_version=root.document_version,
        evidence_set_version=root.evidence_set_version,
        page_layout_id=root.page_layout_id, outline_id=root.outline_id,
        outline_locator=root.outline_locator,
        verified_span_snapshot_id=root.verified_span_snapshot_id,
        source_file_sha256=root.source_file_sha256,
        page_layout=root.page_layout, outline=root.outline,
        evidence_blocks=root.evidence_blocks, terminals=root.terminals,
        components=root.components, dispositions=root.dispositions,
        spans=root.spans, qualification_policy=root.policy,
        profiles=root.profiles, geometry=root.geometry,
        upstream_dependency_fingerprint=root.upstream_dependency_fingerprint)


# ---------------------------------------------------------------------------
# 3. `sbf-1` final paragraph span（§19.7.2）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FinalSpanBuild:
    spans: tuple
    span_of_component: dict
    problems: tuple


def _citable_of_component(component: Any) -> tuple:
    """逐 component 的可引用性：**只**由真实 alignment 终态决定。"""
    if component.admitted:
        return True, TS.CITABLE_REASON_TRUE, component.component_id
    reason = _CITABLE_REASON_OF_ADMISSION.get(component.admission_reason)
    _require(reason is not None,
             f"未登记的 admission_reason {component.admission_reason!r}"
             "（可引用性映射必须是全函数）")
    return False, reason, None


def _build_final_spans(root: FinalMaterialBuildInput) -> _FinalSpanBuild:
    by_span: dict = {}
    for c in root.components:
        if c.landing == "body_span":
            _require(isinstance(c.span_id, str) and c.span_id != "",
                     "body_span component 必须绑定 TS4 span_id")
            by_span.setdefault(c.span_id, []).append(c)

    regular_by_span: dict = {}
    for d in root.dispositions:
        if d.range_kind == "regular" and d.span_id is not None:
            regular_by_span.setdefault(d.span_id, []).append(d)

    problems: list = []
    out: list = []
    span_of_component: dict = {}

    for span in root.spans:
        members = by_span.get(span.span_id)
        if not members:
            continue
        disps = regular_by_span.get(span.span_id, ())
        _require(len(disps) == 1,
                 f"TS4 span {span.span_id!r} 必须恰有一条 regular disposition，"
                 f"得到 {len(disps)} 条（fail-closed）")
        d = disps[0]
        _require(d.node_id == span.node_id,
                 f"TS4 span {span.span_id!r} 与 disposition 的 node_id 不一致")
        left = d.left_boundary_cause
        right = d.right_boundary_cause
        if not isinstance(left, str) or left == "" or \
                not isinstance(right, str) or right == "":
            # 没有真实边界成因就无法重算冻结阈值 ⇒ 不得伪造因子，只留阻断缺口。
            problems.append("boundary_cause_missing:" + d.disposition_id)
            continue

        ordered = sorted(members,
                         key=lambda c: (root.block_order[c.evidence_block_id],
                                        c.evidence_char_range, c.component_id))
        pieces: list = []
        intervals: list = []
        ranges: list = []
        cursor = 0
        for c in ordered:
            block = root.blocks_by_id.get(c.evidence_block_id)
            _require(block is not None,
                     f"component {c.component_id!r} 引用了未知 Evidence block "
                     f"{c.evidence_block_id!r}（fail-closed）")
            text = _tight_text(block)
            lo, hi = c.evidence_char_range
            _require(0 <= lo < hi <= len(text),
                     f"component 的 Evidence 区间 {c.evidence_char_range} 越出 "
                     f"block {c.evidence_block_id!r} 的 {len(text)} 字符（fail-closed）")
            frag = text[lo:hi]
            _require(frag != "", "component 的 Evidence 片段不得为空")
            citable, reason, cell_ref = _citable_of_component(c)
            intervals.append(TS.CitableInterval(
                citable=citable, reason=reason,
                char_range=(cursor, cursor + len(frag)), cell_ref=cell_ref))
            cursor += len(frag)
            pieces.append(frag)
            ranges.append((c.evidence_block_id, int(lo), int(hi)))
        normalized_text = "".join(pieces)
        _require(cursor == len(normalized_text),
                 "final span 的区间必须精确铺满其规范化文本")

        # 重算冻结 TS4-B 的**聚合口径**：0.85 必要阈值比较的是 `min(左, 右)`；
        # `confidence` 字段本身按 `fos-1` 规格保存左因子 × 右因子。两者都必须在
        # 构造期就已量化（final span 的身份在两阶段装配里于 `__post_init__` 之前派生）。
        left_factor = root.policy.factor_for("left", left)
        right_factor = root.policy.factor_for("right", right)
        recomputed = quantize(min(left_factor, right_factor))
        confidence = quantize(left_factor * right_factor)
        qualified = recomputed >= quantize(V.SPAN_CONFIDENCE_MIN)
        if recomputed != quantize(span.confidence):
            problems.append("ts4_confidence_recompute_mismatch:"
                            + d.disposition_id)
        if not qualified:
            problems.append("final_span_threshold_not_reestablished:"
                            + d.disposition_id)
            # 无法重新建立冻结阈值 ⇒ 该 span 的全部区间一律不可引用。
            intervals = [TS.CitableInterval(
                citable=False,
                reason=(iv.reason if not iv.citable
                        else "component_not_admitted"),
                char_range=iv.char_range,
                cell_ref=(iv.cell_ref if not iv.citable else None))
                for iv in intervals]

        factors = tuple(BoundaryFactor(side=s, cause=c, factor=f)
                        for (s, c, f) in SP.TS4_A_FACTOR_VALUES)
        built = TS.FinalOutlineSpan.create(
            schema_version=V.FINAL_SPAN_SCHEMA_VERSION,
            span_builder_version=FINAL_SPAN_BUILDER_VERSION,
            document_id=root.document_id,
            document_version=root.document_version,
            evidence_set_version=root.evidence_set_version,
            page_layout_id=root.page_layout_id,
            outline_id=root.outline_id,
            upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
            node_id=span.node_id,
            source_boundary_id=d.disposition_id,
            ts4_source_span_locator=span.span_locator,
            ts4_source_disposition_id=d.disposition_id,
            start_page=int(span.start_anchor[0]),
            start_line=int(span.start_anchor[1]),
            end_page=int(span.end_anchor[0]),
            end_line=int(span.end_anchor[1]),
            component_ids=tuple(sorted(c.component_id for c in ordered)),
            evidence_char_ranges=tuple(sorted(ranges, key=lambda r: (r[0], r[1]))),
            boundary_factors=factors,
            confidence=confidence,
            confidence_min=quantize(V.SPAN_CONFIDENCE_MIN),
            normalized_text=normalized_text,
            citable_intervals=tuple(intervals))
        out.append(built)
        for c in ordered:
            span_of_component[c.component_id] = built.span_id

    out.sort(key=lambda s: s.span_locator)
    return _FinalSpanBuild(spans=tuple(out),
                           span_of_component=span_of_component,
                           problems=tuple(sorted(set(problems))))


# ---------------------------------------------------------------------------
# 4. decision 落地（§19.7.1）
# ---------------------------------------------------------------------------

def _decision_span_id(root: FinalMaterialBuildInput,
                      final_spans: Sequence[Any], d: Any) -> str:
    """`kept_as_paragraph` / `absorbed_into_body` 必须指向真实 final span。"""
    if isinstance(d.span_id, str) and d.span_id != "":
        for sp in final_spans:
            if sp.ts4_source_span_locator is None:
                continue
            for src in root.spans:
                if src.span_id == d.span_id and \
                        sp.ts4_source_span_locator == src.span_locator:
                    return sp.span_id
    _err(f"裁决要求绑定 final span，但该 disposition {d.disposition_id!r} "
         "没有可回查的 final span（不得凭 decision 自报）")


def _build_decisions(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                     built: TB.TableBuildResult,
                     final_spans: Sequence[Any]) -> tuple:
    table_kinds = [d for d in root.dispositions
                   if d.range_kind in TB.TABLE_DISPOSITION_KINDS]
    _require(len(built.decisions) == len(table_kinds),
             "每条表 provisional disposition 必须恰有一条裁决："
             f"{len(built.decisions)} != {len(table_kinds)}")
    table_by_id = {bt.table.table_id: bt.table for bt in built.tables}
    seen: set = set()
    out: list = []
    for draft in built.decisions:
        _require(draft.disposition_id not in seen,
                 f"disposition {draft.disposition_id!r} 出现多条裁决")
        seen.add(draft.disposition_id)
        d = ctx.disposition_by_id(draft.disposition_id)
        _require(d is not None,
                 f"裁决引用了未知 disposition {draft.disposition_id!r}")
        _require(draft.range_kind == d.range_kind and
                 draft.disposition_locator == d.disposition_locator,
                 "裁决与其 disposition 的位置 / 种类不一致")
        _require(draft.final_span_id is None and draft.evidence_char_range == (0, 0)
                 and draft.source_ref_ids == (),
                 "裁决草稿不得预置 final span / 区间 / 来源")
        if draft.target_kind == "final_span":
            span_id = _decision_span_id(root, final_spans, d)
            out.append(TS.TableRangeDecision.create(
                schema_version=V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
                upstream_dependency_fingerprint=
                    root.upstream_dependency_fingerprint,
                disposition_id=draft.disposition_id,
                disposition_locator=draft.disposition_locator,
                range_kind=draft.range_kind, decision=draft.decision,
                target_kind=draft.target_kind, table_id=None,
                final_span_id=span_id, evidence_char_range=draft.evidence_char_range,
                source_ref_ids=draft.source_ref_ids,
                rationale_code=draft.rationale_code))
            continue
        refs = TB.refs_in_disposition(ctx, d)
        if draft.target_kind == "table":
            # 裁决只**单向**引用已完成对象：它消费的来源必须是**被裁决的那张表**
            # 自己携带的来源片段。否则 `source_ref_ids` 会成为悬空引用。
            table = table_by_id.get(draft.table_id)
            owned = ({r.source_ref_id for r in table.source_refs}
                     if table is not None else set())
            refs = tuple(r for r in refs if r.source_ref_id in owned)
        else:
            # 保留为正文 / 无目标：final span 由真实 TS4 component 重建，不携带
            # table cell source ref，因此这里不得登记任何来源（否则即悬空引用）。
            refs = ()
        blocks = {r.evidence_block_id for r in refs}
        if refs and len(blocks) == 1:
            # 区间必须落在**单一** Evidence 字符域内；跨块时坐标空间不同，不得拼。
            lo = min(r.evidence_char_range[0] for r in refs)
            hi = max(r.evidence_char_range[1] for r in refs)
            char_range = (int(lo), int(hi))
            ref_ids = tuple(sorted({r.source_ref_id for r in refs}))
        else:
            char_range = (0, 0)
            ref_ids = ()
        out.append(TS.TableRangeDecision.create(
            schema_version=V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
            upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
            disposition_id=draft.disposition_id,
            disposition_locator=draft.disposition_locator,
            range_kind=draft.range_kind, decision=draft.decision,
            target_kind=draft.target_kind, table_id=draft.table_id,
            final_span_id=None, evidence_char_range=char_range,
            source_ref_ids=ref_ids, rationale_code=draft.rationale_code))
    out.sort(key=lambda x: x.decision_locator)
    return tuple(out)


def _mixed_block_dispositions(root: FinalMaterialBuildInput,
                              ctx: TB.TableBuildContext,
                              decisions: Sequence[Any]) -> list:
    """跨 Evidence block、因而无法表达为单一字符区间的 disposition。"""
    out: list = []
    for dec in decisions:
        if dec.evidence_char_range != (0, 0) or dec.source_ref_ids:
            continue
        d = ctx.disposition_by_id(dec.disposition_id)
        if d is None:
            continue
        blocks = {r.evidence_block_id for r in TB.refs_in_disposition(ctx, d)}
        if len(blocks) > 1:
            out.append((dec.disposition_id, len(blocks)))
    return out


# ---------------------------------------------------------------------------
# 5. component binding（§19.9.1，11 类 landing 唯一封闭映射）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Consumption:
    """每个 component 在各 table object 上的**实际消费片段**（真实 cell 分区）。"""

    tables: dict
    ref_ids: dict
    problems: dict


def _measure_consumption(ctx: TB.TableBuildContext,
                         tables: Sequence[Any]) -> _Consumption:
    """用**表自身已校验**的真实消费衡量，并对每个 component 做分区检查。

    `BuiltTable.consumed` 由逐 cell 装配产生，且 `TableObjectV4` 已在构造期证明
    `source_refs` 是"全部被消费片段的并集"。因此删掉任何一个 cell/来源片段都会
    同时改变这张表与守恒层，无法只在一处"抹平"。
    """
    tables_of: dict = {}
    ref_ids: dict = {}
    problems: dict = {}
    for bt in tables:
        consumed = {cid: tuple(sorted(set(ranges)))
                    for cid, ranges in bt.consumed.items()}
        for cid in consumed:
            tables_of.setdefault(cid, []).append(
                (bt.table.table_id, consumed[cid]))
            ids = {r.source_ref_id for r in bt.table.source_refs
                   if r.component_id == cid}
            ref_ids[(bt.table.table_id, cid)] = tuple(sorted(ids))
        for entry in TB.component_partition_problems(ctx, consumed):
            cid, _, code = entry.partition(":")
            problems.setdefault(cid, set()).add(code)
    return _Consumption(
        tables={k: tuple(sorted(v)) for k, v in tables_of.items()},
        ref_ids=ref_ids,
        problems={k: tuple(sorted(v)) for k, v in problems.items()})


def _pending(reason: str) -> dict:
    return {"admission": "pending", "admission_reason": reason, "table_id": None,
            "final_span_id": None, "cell_source_ref_ids": (),
            "gap_codes": (reason,)}


def _binding_for(consumed: _Consumption, component: Any,
                 decision_of: dict, span_of_component: dict) -> dict:
    """把一个 component 映射到它**唯一**的终态（§19.9.1 的封闭真值表）。"""
    cid = component.component_id
    landing = component.landing
    claims = consumed.tables.get(cid, ())
    probs = consumed.problems.get(cid, ())

    def rejected(reason: str) -> dict:
        return {"admission": "rejected", "admission_reason": reason,
                "table_id": None, "final_span_id": None,
                "cell_source_ref_ids": (), "gap_codes": ()}

    def absorbed(table_id: str) -> dict:
        return {"admission": "table_object",
                "admission_reason": "absorbed_into_table", "table_id": table_id,
                "final_span_id": None,
                "cell_source_ref_ids": consumed.ref_ids.get((table_id, cid), ()),
                "gap_codes": ()}

    def as_final_span(reason: str, span_id: str) -> dict:
        return {"admission": "final_span", "admission_reason": reason,
                "table_id": None, "final_span_id": span_id,
                "cell_source_ref_ids": (), "gap_codes": ()}

    def clean_single_table() -> str | None:
        if len(claims) == 1 and not probs:
            table_id = claims[0][0]
            if consumed.ref_ids.get((table_id, cid), ()):
                return table_id
        return None

    if landing == "body_span":
        if claims:
            # verified 表几何与冻结正文冲突：绝不改绑 table。
            return _pending("upstream_table_scope_miss")
        span_id = span_of_component.get(cid)
        if span_id is None:
            return _pending("upstream_table_scope_miss")
        return as_final_span("rebuilt_as_final_span", span_id)

    if landing in ("table_inside", "table_adjacency"):
        _require(component.disposition_id in decision_of,
                 f"{landing} component 必须绑定表 provisional disposition，"
                 f"得到 {component.disposition_id!r}")
        dec = decision_of[component.disposition_id]
        table_id = clean_single_table()
        if table_id is not None:
            return absorbed(table_id)
        if claims or probs:
            # 跨 table / 跨 span / 有未归属残余区间 / 子区间无法唯一切分：
            # 依 §19.9.1 末段一律 pending(provenance_incomplete)。
            return _pending("provenance_incomplete")
        if dec.decision in ABSORBED_DECISIONS:
            # 裁决说"被吸收"，但没有任何 table 真正消费到它：不得凭裁决绑定。
            return _pending("provenance_incomplete")
        if dec.decision in KEPT_DECISIONS:
            span_id = span_of_component.get(cid)
            if span_id is not None:
                return as_final_span("kept_as_final_paragraph", span_id)
            return _pending("provenance_incomplete")
        if dec.decision == "unresolved_geometry":
            return _pending("unresolved_geometry")
        return _pending("unsupported_table_structure")

    if landing in ("body_unassigned", "formal_unassigned"):
        table_id = clean_single_table()
        if table_id is not None:
            return absorbed(table_id)
        return _pending("provenance_incomplete")

    if landing in _REJECTED_REASON_OF_LANDING:
        return rejected(_REJECTED_REASON_OF_LANDING[landing])

    if landing in _NO_FRAGMENT_LANDINGS:
        return _pending("provenance_incomplete")

    _err(f"未登记的 component landing {landing!r}（11 类封闭映射不得扩张）")


def _build_bindings(root: FinalMaterialBuildInput, consumed: _Consumption,
                    decisions: Sequence[Any], span_of_component: dict) -> tuple:
    decision_of = {d.disposition_id: d for d in decisions}
    out: list = []
    for component in root.components:
        plan = _binding_for(consumed, component, decision_of, span_of_component)
        allowed = TS.COMPONENT_LANDING_ADMISSIONS[component.landing]
        _require(plan["admission"] in allowed,
                 f"landing={component.landing!r} 只允许终态 {allowed}，"
                 f"得到 {plan['admission']!r}")
        if plan["admission"] == "pending":
            allowed_reasons = TS.COMPONENT_LANDING_PENDING_REASONS[component.landing]
            _require(plan["admission_reason"] in allowed_reasons,
                     f"landing={component.landing!r} 的 pending 理由必须属于 "
                     f"{allowed_reasons}，得到 {plan['admission_reason']!r}")
        elif plan["admission"] == "rejected":
            allowed_reasons = TS.COMPONENT_LANDING_REJECTED_REASONS.get(
                component.landing, ())
            _require(plan["admission_reason"] in allowed_reasons,
                     f"landing={component.landing!r} 的 rejected 理由必须属于 "
                     f"{allowed_reasons}，得到 {plan['admission_reason']!r}")
        out.append(TS.FinalComponentBinding.create(
            schema_version=V.FINAL_COMPONENT_BINDING_SCHEMA_VERSION,
            upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
            component_id=component.component_id,
            component_locator=component.component_locator,
            component_landing=component.landing,
            admission=plan["admission"],
            admission_reason=plan["admission_reason"],
            table_id=plan["table_id"],
            final_span_id=plan["final_span_id"],
            cell_source_ref_ids=plan["cell_source_ref_ids"],
            disposition_id=component.disposition_id,
            evidence_char_range=component.evidence_char_range,
            gap_codes=tuple(sorted(set(plan["gap_codes"])))))
    out.sort(key=lambda b: b.binding_locator)
    _require(len(out) == len(root.components) and
             {b.component_id for b in out} ==
             {c.component_id for c in root.components},
             "component binding 必须与 verified TS4 components 精确等集"
             "（不得遗漏、重复或跨树拼接）")
    return tuple(out)


# ---------------------------------------------------------------------------
# 6. citable coverage（§19.9.2）
# ---------------------------------------------------------------------------

def _build_coverages(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                     tables: Sequence[Any]) -> tuple:
    out: list = []
    for table in tables:
        refs = list(table.source_refs)
        _require(bool(refs), f"表 {table.table_id!r} 必须至少一条真实来源片段")
        cursor = 0
        intervals: list = []
        for ref in refs:
            span = ctx.layout_span(ref.interval.page_number,
                                   ref.interval.line_index,
                                   ref.interval.span_index)
            _require(span is not None,
                     "coverage 引用了未知 LayoutSpan："
                     f"{ref.interval.page_number}/{ref.interval.line_index}/"
                     f"{ref.interval.span_index}（fail-closed）")
            # 片段区间是**片段内**偏移的真实子区间，不要求覆盖整段 `span.text`：
            # 右对齐补白、缩排空白与行末折行空隙在 Evidence 文本里根本不存在，
            # 它们既不是来源也不是缺口（见 §19.9.2 与 `TableSourceInterval`）。
            # 这里只守两条 fail-closed 不变量：区间落在片段内；区间之外**只**是
            # 非语义空白——用守恒层的**同一**判据，不得夹取真实字符。
            start_i, end_i = ref.interval.span_char_range
            _require(0 <= start_i < end_i <= len(span.text),
                     f"source ref 的片段区间 {ref.interval.span_char_range} 越出 "
                     f"LayoutSpan 文本长度 {len(span.text)}")
            _require(TS.is_non_semantic_whitespace(span.text[:start_i]) and
                     TS.is_non_semantic_whitespace(span.text[end_i:]),
                     f"source ref 的片段区间 {ref.interval.span_char_range} 之外"
                     f"仍有非语义非空白字符（LayoutSpan 文本长度 "
                     f"{len(span.text)}）：不得夹取")
            length = end_i - start_i
            intervals.append(TS.CitableInterval(
                citable=ref.citable, reason=ref.citable_reason,
                char_range=(cursor, cursor + length),
                cell_ref=ref.source_ref_id))
            cursor += length
        blocks = {r.evidence_block_id for r in refs}
        if len(blocks) == 1:
            envelope = (min(r.evidence_char_range[0] for r in refs),
                        max(r.evidence_char_range[1] for r in refs))
        else:
            # 跨 Evidence block 时坐标空间不同：不得拼出一个"看起来合法"的区间。
            envelope = (0, 0)
        out.append(TS.TableCitableCoverage.create(
            schema_version=V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION,
            upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
            table_id=table.table_id, table_locator=table.table_locator,
            cell_refs=tuple(sorted({r.source_ref_id for r in refs})),
            intervals=tuple(intervals),
            evidence_char_range=(int(envelope[0]), int(envelope[1])),
            non_citable_reasons=tuple(sorted(
                {iv.reason for iv in intervals if not iv.citable}))))
    out.sort(key=lambda x: x.coverage_locator)
    return tuple(out)


# ---------------------------------------------------------------------------
# 7. relation 补全（§19.8.1：`introduces` / `explains` / `references`）
# ---------------------------------------------------------------------------

def _table_endpoint(table: Any) -> Any:
    return TS.TableEndpointRef(
        table_locator=table.table_locator, table_id=table.table_id,
        document_id=table.document_id, page_layout_id=table.page_layout_id,
        outline_id=table.outline_id, page_number=table.page_number)


def _span_endpoint(span: Any) -> Any:
    return TS.FinalSpanEndpointRef(
        span_locator=span.span_locator, span_id=span.span_id,
        node_id=span.node_id, document_id=span.document_id,
        outline_id=span.outline_id, source_boundary_id=span.source_boundary_id)


def _span_top(root: FinalMaterialBuildInput, span: Any) -> float:
    """final span 的起始顶边 y（取自其 TS4 来源行，与表格同一坐标空间）。"""
    for candidate in root.spans:
        if candidate.span_locator == span.ts4_source_span_locator:
            return float(candidate.start_anchor[2][1])
    return 0.0


def _document_order(root: FinalMaterialBuildInput, tables: Sequence[Any],
                    final_spans: Sequence[Any]) -> list:
    """按物理源序排列 final span 与 table：`(页, 顶边 y, 类别序, locator)`。"""
    items: list = []
    for span in final_spans:
        items.append(((span.start_page, _span_top(root, span), 0, span.span_locator),
                      "final_span", span))
    for table in tables:
        items.append(((table.page_number, float(table.page_bbox[1]), 1,
                       table.table_locator), "table", table))
    items.sort(key=lambda x: x[0])
    return items


def _relation_proofs(*values: Any) -> tuple:
    return tuple(sorted({str(v) for v in values if v}))


def _adjacency_relations(root: FinalMaterialBuildInput, tables: Sequence[Any],
                         final_spans: Sequence[Any]) -> tuple:
    """`introduces` / `explains`：只看**紧邻**的、同 owner 节点的 final span。"""
    order = _document_order(root, tables, final_spans)
    out: list = []
    for index, (_key, kind, obj) in enumerate(order):
        if kind != "table":
            continue
        owner = obj.owner
        if owner.owner_kind != "outline_node":
            # 未归属边界的表没有可采信节点，不得凭相邻标题字符串自报归属。
            continue
        if index > 0:
            prev_kind, prev = order[index - 1][1], order[index - 1][2]
            if prev_kind == "final_span" and prev.node_id == owner.node_id:
                out.append(TS.TableRelation.create(
                    schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
                    relation_builder_version=V.TABLE_RELATION_BUILDER_VERSION,
                    upstream_dependency_fingerprint=
                        root.upstream_dependency_fingerprint,
                    relation_kind="introduces",
                    source_endpoint=_span_endpoint(prev),
                    target_endpoint=_table_endpoint(obj),
                    relation_proof_ids=_relation_proofs(
                        prev.span_id, prev.span_locator, prev.source_boundary_id,
                        obj.table_id, obj.table_locator, owner.node_id)))
        if index + 1 < len(order):
            nxt_kind, nxt = order[index + 1][1], order[index + 1][2]
            if nxt_kind == "final_span" and nxt.node_id == owner.node_id:
                out.append(TS.TableRelation.create(
                    schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
                    relation_builder_version=V.TABLE_RELATION_BUILDER_VERSION,
                    upstream_dependency_fingerprint=
                        root.upstream_dependency_fingerprint,
                    relation_kind="explains",
                    source_endpoint=_table_endpoint(obj),
                    target_endpoint=_span_endpoint(nxt),
                    relation_proof_ids=_relation_proofs(
                        obj.table_id, obj.table_locator, nxt.span_id,
                        nxt.span_locator, nxt.source_boundary_id, owner.node_id)))
    return tuple(out)


def _unresolved_cross_references(root: FinalMaterialBuildInput) -> list:
    """verified 未解析 `cross_reference` 边中，带真实 occurrence 的那些。"""
    out: list = []
    for edge in root.outline.edges:
        if getattr(edge, "edge_kind", None) != "cross_reference":
            continue
        if getattr(edge, "is_resolved", False):
            continue
        occ = getattr(edge, "occurrence", None)
        if occ is None:
            continue
        declared = getattr(occ, "declared_target", None)
        if not isinstance(declared, str) or declared.strip() == "":
            continue
        out.append((edge, occ, declared))
    return out


def _reference_relations(root: FinalMaterialBuildInput,
                         tables: Sequence[Any]) -> tuple:
    """`references`：目标必须由**本表自身的表题**在本轮独立证明。

    TS3 允许"来源 occurrence 已验证、目标尚未解析"。TS5 不要求上游预先虚构
    resolved target，而是用当前真实表题文本做**精确**（canonical）匹配，且必须
    **恰好**命中一个 `TableObjectV4`；命中零个或多个都只留缺口，不建立关系。
    """
    out: list = []
    for edge, occ, declared in _unresolved_cross_references(root):
        wanted = canonical_text(declared)
        matches = [t for t in tables
                   if t.title_blocks and
                   canonical_text(TS.CELL_BLOCK_SEPARATOR.join(
                       b.text for b in t.title_blocks)) == wanted]
        if len(matches) != 1:
            continue
        table = matches[0]
        payload = {
            "source_ref": occ.source_ref, "page_number": occ.page_number,
            "line_index": occ.line_index, "char_start": occ.char_start,
            "char_end": occ.char_end, "reference_marker": occ.reference_marker,
            "declared_target": declared,
        }
        endpoint = TS.ReferenceOccurrenceEndpointRef(
            occurrence_locator=locator("occ", payload),
            occurrence_id=identity("occ", payload),
            owning_source_ref=occ.source_ref, page_number=occ.page_number,
            line_index=occ.line_index,
            occurrence_char_range=(occ.char_start, occ.char_end),
            edge_locator=edge.edge_locator, edge_id=edge.edge_id,
            reference_marker=occ.reference_marker)
        out.append(TS.TableRelation.create(
            schema_version=V.TABLE_RELATION_SCHEMA_VERSION,
            relation_builder_version=V.TABLE_RELATION_BUILDER_VERSION,
            upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
            relation_kind="references", source_endpoint=endpoint,
            target_endpoint=_table_endpoint(table),
            relation_proof_ids=_relation_proofs(
                endpoint.occurrence_id, endpoint.occurrence_locator,
                endpoint.edge_id, endpoint.edge_locator, table.table_id,
                table.table_locator, wanted)))
    return tuple(out)


# ---------------------------------------------------------------------------
# 8. §19.9.3 四层守恒（分别验证，不得跨层相加）
# ---------------------------------------------------------------------------

def _total_kind(layer_kind: str) -> str:
    return TS.CONSERVATION_TERM_KIND_OF[TS.CONSERVATION_LAYER_TERMS[layer_kind][0]]


def _term(term: str, value: int) -> TS.ConservationTerm:
    kind = TS.CONSERVATION_TERM_KIND_OF[term]
    return TS.ConservationTerm(
        term=term, term_kind=kind,
        count=(value if kind == "count" else 0),
        char_count=(value if kind == "character" else 0),
        interval_length=(value if kind == "interval" else 0))


def _total_term(layer_kind: str, value: int) -> TS.ConservationTerm:
    kind = _total_kind(layer_kind)
    return TS.ConservationTerm(
        term=TS.CONSERVATION_TOTAL_TERM, term_kind=kind,
        count=(value if kind == "count" else 0),
        char_count=(value if kind == "character" else 0),
        interval_length=(value if kind == "interval" else 0))


def _layer(layer_kind: str, values: dict, total: int,
           problems: Sequence[str]) -> TS.ConservationLayer:
    """构造一层守恒。`balanced` **只能**由 `conservation_layer_eligible` 判定。

    builder 不是"先算算术再自报 balanced"：它把分项、合计与本层问题码交给与
    schema / 复核层 / 验收机**同一个**资格函数，因此"算术凑平但仍有未解释残余或
    问题码"不可能在这里被写成 `balanced=true`。
    """
    names = TS.CONSERVATION_LAYER_TERMS[layer_kind]
    terms = tuple(_term(name, int(values.get(name, 0))) for name in names)
    total_term = _total_term(layer_kind, int(total))
    layer_problems = tuple(sorted(set(problems)))
    balanced = TS.conservation_layer_eligible(
        layer_kind, {name: int(values.get(name, 0)) for name in names},
        int(total), layer_problems)
    if not balanced and not layer_problems:
        layer_problems = (f"layer_unbalanced:{layer_kind}",)
    return TS.ConservationLayer(
        layer_kind=layer_kind, terms=terms, total=total_term,
        balanced=balanced, problems=layer_problems)


def _disposition_layer(root: FinalMaterialBuildInput,
                       decisions: Sequence[Any]) -> tuple:
    table_kinds = [d for d in root.dispositions
                   if d.range_kind in TB.TABLE_DISPOSITION_KINDS]
    counts = {"absorbed_to_table": 0, "final_paragraph": 0,
              "pending_or_unsupported": 0}
    for d in decisions:
        if d.decision in ABSORBED_DECISIONS:
            counts["absorbed_to_table"] += 1
        elif d.decision in KEPT_DECISIONS:
            counts["final_paragraph"] += 1
        else:
            counts["pending_or_unsupported"] += 1
    problems: list = []
    if len(decisions) != len(table_kinds):
        problems.append(
            f"decision_count_mismatch:{len(decisions)}!={len(table_kinds)}")
    unaccounted = ({d.disposition_id for d in table_kinds} -
                   {d.disposition_id for d in decisions})
    if unaccounted:
        problems.append(f"disposition_without_decision:{len(unaccounted)}")
    return counts, len(table_kinds), problems


def _component_layer(root: FinalMaterialBuildInput,
                     bindings: Sequence[Any]) -> tuple:
    counts = {"final_span": 0, "table_object": 0, "pending": 0, "rejected": 0}
    for b in bindings:
        counts[b.admission] = counts.get(b.admission, 0) + 1
    problems: list = []
    if len(bindings) != len(root.components) or \
            {b.component_id for b in bindings} != \
            {c.component_id for c in root.components}:
        problems.append("component_binding_not_exact_set")
    for b in bindings:
        if b.admission == "table_object" and not b.cell_source_ref_ids:
            problems.append(f"table_object_binding_without_cell_ref:{b.component_id}")
        if b.admission == "final_span" and b.final_span_id is None:
            problems.append(f"final_span_binding_without_span:{b.component_id}")
    return counts, len(root.components), problems


#: 原子分区的类别（封闭）。判定优先级：来源主张 > component hit > 自由文本。
_ATOM_CELL = "cell"
_ATOM_ADORN = "adorn"
_ATOM_PARAGRAPH = "paragraph"
_ATOM_FREE = "free"


@dataclass(frozen=True)
class _ScanAtom:
    """表相关文本域内一个**原子字符区间**（最小不可再分单位）。"""

    fragment_key: tuple
    start: int
    end: int
    kind: str

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def plan_key(self) -> tuple:
        return (self.fragment_key[0], self.fragment_key[1],
                self.fragment_key[2], self.start, self.end)


@dataclass(frozen=True)
class _LayoutTextScan:
    """**表相关文本域**的原子分区（`fmc-3` 的唯一共用输入）。

    `atoms` 是把每个片段按**来源片段主张边界**与 **component layout hit 边界**同时
    切开后的最小区间序列；它们互不重叠且恰好覆盖该片段的完整字符数。守恒层与缺口
    区间计划读**同一份** `atoms`，因此"哪些字符算已经被主张"在两侧不可能各自漂移——
    这正是 `fmc-3` "缺口源区间必须能精确对上残余"的前提。
    """

    atoms: tuple
    total: int
    problems: tuple
    in_table_region: frozenset
    text_of: dict
    span_of: dict
    local_hits: dict
    #: 片段键 → 该片段上的全部来源主张 `(起, 止, 类别, 表 id)`，去重且排序。
    #: 类别 ∈ `cell` / `adorn`（与 `_ScanAtom.kind` 的两类来源主张同名）。守恒层**不读**
    #: 这一项（它只看 `atoms` 的类别）；它是 §0.19 逐表范围证明要读的**归属**读数：
    #: 「这个字符落在谁主张的区间里」必须能按表逐条问出来，而不是只知道"被某张表消费"。
    claims_of: dict


def _layout_text_scan(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                      tables: Sequence[Any]) -> _LayoutTextScan:
    """把一个文档**表相关文本域**内每个片段切成原子区间（唯一实现）。

    分区坐标域与 `TableSourceInterval.span_char_range` 一致：**片段内**偏移。越界的
    来源主张、重复主张、越界的 layout hit、以及"同一区间被两类来源同时主张"都在这里
    记问题码——既不静默裁剪，也不当作"已覆盖"。
    """
    claims: dict = {}
    claims_of: dict = {}
    cell_keys: set = set()
    adorn_keys: set = set()

    def _mark(kind: str, key: tuple, char_range: tuple, table_id: str) -> None:
        claims.setdefault(key, []).append((tuple(char_range), kind))
        if kind in ("cell", "adorn"):
            claims_of.setdefault(key, set()).add(
                (int(char_range[0]), int(char_range[1]), kind, table_id))

    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    key = (ref.interval.page_number, ref.interval.line_index,
                           ref.interval.span_index)
                    _mark("cell", key, ref.interval.span_char_range, table.table_id)
                    cell_keys.add(key)
        for blocks in (table.title_blocks, table.unit_blocks, table.note_blocks):
            for blk in blocks:
                for rid in blk.source_ref_ids:
                    ref = table.source_ref_by_id(rid)
                    if ref is None:
                        _err(f"表级块引用了未知来源片段 {rid!r}（fail-closed）")
                    key = (ref.interval.page_number, ref.interval.line_index,
                           ref.interval.span_index)
                    _mark("adorn", key, ref.interval.span_char_range, table.table_id)
                    adorn_keys.add(key)

    paragraph_keys: set = set()
    for comp in root.components:
        if comp.landing != "body_span":
            continue
        for hit in comp.layout_hits:
            paragraph_keys.add((hit.page_number, hit.line_index,
                                hit.layout_span_index))

    # 每个片段的 component layout hit。
    #
    # 坐标域：`LayoutHit.span_char_range` 是**片段内**偏移（与
    # `TableSourceInterval.span_char_range` 同一个域，实测满足
    # `len(range) == len(component.evidence_char_range)`；它的行内对应量是
    # `LayoutHit.line_char_range`，两者相差恰为 `LayoutSpan.char_start`）。
    # 因此这里**不做**任何偏移换算，直接与片段内 `[0, len(text))` 比较。
    hits_of: dict = {}
    for comp in root.components:
        for hit in comp.layout_hits:
            hits_of.setdefault(
                (hit.page_number, hit.line_index, hit.layout_span_index),
                []).append((int(hit.span_char_range[0]),
                            int(hit.span_char_range[1]),
                            comp.component_id))

    # 表相关区域：表 provisional 范围（单页）∪ 已成立表的物理矩形。
    in_table_region: set = set()
    for d in root.dispositions:
        if d.range_kind not in TB.TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != d.end_page:
            continue
        for li in range(d.start_line, d.end_line + 1):
            line = ctx.line_at(d.start_page, li)
            if line is None or line.is_furniture:
                continue
            for si in range(len(line.spans)):
                in_table_region.add((d.start_page, li, si))
    boxes_by_page: dict = {}
    for table in tables:
        boxes_by_page.setdefault(table.page_number, []).append(
            tuple(table.page_bbox))

    domain: set = set(cell_keys) | set(adorn_keys) | set(paragraph_keys)
    domain |= in_table_region
    for page in root.page_layout.pages:
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
                if any(_overlap_area(span.bbox, box) > 0.0 for box in boxes):
                    domain.add(key)
                    in_table_region.add(key)

    atoms: list = []
    problems: list = []
    total = 0
    text_of: dict = {}
    span_of: dict = {}
    local_hits_of: dict = {}
    for page in root.page_layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            for si, span in enumerate(line.spans):
                key = (page.page_number, line.line_index, si)
                if key not in domain:
                    continue
                # 分区坐标域与 `TableSourceInterval.span_char_range` 一致：**片段内**。
                base = int(span.char_start)
                lo, hi = 0, int(span.char_end) - base
                total += hi - lo
                text_of[key] = span.text
                span_of[key] = span
                marks = claims.get(key, ())
                # 越界的主张一律记账：既不静默裁剪，也不当作"已覆盖"。
                if any(x < lo or x > hi for (rng, _k) in marks for x in rng):
                    problems.append(f"layout_text_ref_out_of_span:{key}")
                # 同一片段上完全相同的（范围, 类别）出现两次 = 重复归属。
                if len(marks) != len({(tuple(rng), k) for (rng, k) in marks}):
                    problems.append(f"layout_text_duplicate_claim:{key}")
                local_hits: list = []
                for (hs, he, cid) in hits_of.get(key, ()):
                    if hs < lo or he > hi:
                        problems.append(f"layout_text_hit_out_of_span:{key}")
                        continue
                    local_hits.append((hs, he, cid))
                local_hits_of[key] = tuple(local_hits)
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
                        problems.append(f"layout_text_overlap:{key}")
                    if "cell" in classes:
                        kind = _ATOM_CELL
                    elif "adorn" in classes:
                        kind = _ATOM_ADORN
                    elif key in paragraph_keys:
                        kind = _ATOM_PARAGRAPH
                    else:
                        kind = _ATOM_FREE
                    atoms.append(_ScanAtom(fragment_key=key, start=a, end=b,
                                           kind=kind))
    return _LayoutTextScan(atoms=tuple(atoms), total=total,
                           problems=tuple(sorted(set(problems))),
                           in_table_region=frozenset(in_table_region),
                           text_of=text_of, span_of=span_of,
                           local_hits=local_hits_of,
                           claims_of={k: tuple(sorted(v))
                                      for k, v in claims_of.items()})


# ---------------------------------------------------------------------------
# 3.2 §0.19 逐表范围读数（**分区判据仍在本模块**；读数不是资格结论）
# ---------------------------------------------------------------------------

#: 逐表范围读数里，一个原子在本表范围内的**归属类**（封闭）。
#:
#: - `table`：被**本表**的 cell / 表题单位注释来源主张（唯一归属）；
#: - `other_table`：被**另一张表**主张（跨表重叠——不得用任意优先级吞掉）；
#: - `paragraph`：被已成立的正文段落 span 主张（本表矩形内有别人的正文）；
#: - `none`：无人主张。
SCOPE_OWNERS: tuple[str, ...] = ("table", "other_table", "paragraph", "none")


@dataclass(frozen=True)
class ScopeAtomReading:
    """本表范围内一个**原子字符区间**的逐条归属读数（只读）。

    `owners` 逐条保留覆盖本原子的来源主张 `(起, 止, 类别, 表 id)`：读的人据此能问出
    「这个字符是谁主张的」，而不是只知道"被某张表消费过"。`in_region` 说的是它是否落在
    本表的**物理矩形**内；`is_whitespace` 用 `table_schema` 的**唯一**实现判定（与守恒层
    同一判据，不另立一套）。
    """

    fragment_key: tuple
    start: int
    end: int
    kind: str
    text: str
    owners: tuple
    owner_kind: str
    in_region: bool
    is_whitespace: bool

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class TableScopeReading:
    """一张表的**范围读数**（§0.19 的输入；**不是**资格结论，也不放行任何东西）。

    它把「本表矩形内有哪些字符、各由谁主张、哪些无人主张」逐条摊开，供
    `document_structure.table_local_proof` 做逐表完整证明。两张表的读数互不替代：
    同一片段可以同时出现在两张表的读数里（那时 `owner_kind` 会如实显示跨表重叠），
    **不得**由任何一方按优先级把它吞掉。
    """

    table_id: str
    table_locator: str
    page_number: int
    page_bbox: tuple
    #: 本表物理矩形内（同页、非 furniture、与 `page_bbox` 有正重叠）的全部片段键。
    region_fragment_keys: tuple
    #: 本表自己的来源主张 `(页, 行, 片段, 起, 止, 类别)`，去重排序。
    claims: tuple
    #: 本表矩形内的全部原子（按 `(片段, 起, 止)` 排序）。
    atoms: tuple
    #: 与本表片段/主张相关的同一份扫描问题码（不另起一套判据）。
    scan_problems: tuple

    def atoms_with(self, owner_kind: str) -> tuple:
        return tuple(a for a in self.atoms if a.owner_kind == owner_kind)

    def unowned_atoms(self) -> tuple:
        """本表范围内**无人主张**的原子（残余与已延期都从这里出）。"""
        return self.atoms_with("none")

    def region_text(self) -> str:
        """本表矩形内原子的逐字正文（按片段与偏移拼接；不重排、不补全）。"""
        return "".join(a.text for a in self.atoms)


def _problem_key(problem: str) -> tuple:
    """扫描问题码尾巴上的片段键 `"<码>:<页, 行, 片段>"` → 三元组（解析不出就给空元组）。

    只作**归属比较**用：问题码的形状由 `_layout_text_scan` 唯一决定，本函数不改写它，
    也不把解析失败当成"没命中"以外的任何意思。
    """
    tail = problem.split(":", 1)[1] if ":" in problem else ""
    try:
        parts = tuple(int(x) for x in tail.strip().strip("()").split(","))
    except ValueError:
        return ()
    return parts if len(parts) == 3 else ()


def _scope_owner_kind(owners: Sequence[tuple], *, table_id: str, kind: str) -> str:
    """一个原子的归属类（多主张时**不取优先级**，冲突另有代码）。

    覆盖它的来源主张非空 ⇒ 按主张判（`table` / `other_table`）；无主张但原子类别为
    `paragraph` ⇒ 它落在已成立正文段落的 hit 里（`paragraph`）；其余为 `none`。
    """
    tables = {str(o[3]) for o in owners}
    if tables:
        return "table" if tables == {table_id} else "other_table"
    return "paragraph" if kind == _ATOM_PARAGRAPH else "none"


def build_table_scope_readings(verified_span: Any) -> tuple[TableScopeReading, ...]:
    """§0.19 逐表范围读数：**同一份图侧来源**上，逐表摊开区域归属。

    分区判据只有一处——本函数复用 `_layout_text_scan`（守恒层消费的**同一份**原子分区），
    因此「哪些字符算已被主张」在守恒层与逐表证明之间不可能各自漂移。三项读数：

    1. **区域**：同页、非 furniture、与本表 `page_bbox` 有正重叠的片段（`span_of` 的
       真实 `LayoutSpan.bbox` 判重叠，不做行级粗判）；
    2. **主张**：本表 cell 与表题/单位/注释块的来源主张（`claims_of`），逐条带表 id；
    3. **原子**：区域内每个原子的归属类与逐字文本。

    它**不**读守恒结果、不读缺口台账、不看 `structure_state`：读写数的人自己判。也**不**
    减任何东西——`partial` 表照样得到一份读数（读数是诚实的，资格另判）。
    """
    root = _read_roots(verified_span)
    ctx = _build_context(root)
    built = TB.build_tables(ctx)
    tables = tuple(sorted((bt.table for bt in built.tables),
                          key=lambda t: t.table_locator))
    scan = _layout_text_scan(root, ctx, tables)
    readings: list[TableScopeReading] = []
    for table in tables:
        box = tuple(float(v) for v in table.page_bbox)
        region_keys: set = set()
        page = None
        for p in root.page_layout.pages:
            if p.page_number == table.page_number:
                page = p
                break
        if page is not None:
            for line in page.lines:
                if line.is_furniture:
                    continue
                for si, span in enumerate(line.spans):
                    key = (page.page_number, line.line_index, si)
                    if key not in scan.span_of:
                        continue
                    if _overlap_area(scan.span_of[key].bbox, box) > 0.0:
                        region_keys.add(key)
        # 本表的来源主张（含**矩形之外**的片段：表可以经处置正当吸收矩形外的表题/单位）。
        claims: set = set()
        scope_keys = set(region_keys)
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    iv = ref.interval
                    key = (iv.page_number, iv.line_index, iv.span_index)
                    claims.add((iv.page_number, iv.line_index, iv.span_index,
                                int(iv.span_char_range[0]),
                                int(iv.span_char_range[1]), "cell"))
                    scope_keys.add(key)
        for blocks in (table.title_blocks, table.unit_blocks, table.note_blocks):
            for blk in blocks:
                for rid in blk.source_ref_ids:
                    ref = table.source_ref_by_id(rid)
                    if ref is None:
                        continue
                    iv = ref.interval
                    claims.add((iv.page_number, iv.line_index, iv.span_index,
                                int(iv.span_char_range[0]),
                                int(iv.span_char_range[1]), "adorn"))
                    scope_keys.add((iv.page_number, iv.line_index, iv.span_index))
        atoms: list[ScopeAtomReading] = []
        for atom in scan.atoms:
            key = atom.fragment_key
            if key not in scope_keys:
                continue
            owners = tuple(o for o in scan.claims_of.get(key, ())
                           if o[0] <= atom.start and atom.end <= o[1])
            in_region = key in region_keys
            atoms.append(ScopeAtomReading(
                fragment_key=key, start=atom.start, end=atom.end, kind=atom.kind,
                text=scan.text_of[key][atom.start:atom.end],
                owners=owners,
                owner_kind=_scope_owner_kind(owners, table_id=table.table_id,
                                             kind=atom.kind),
                in_region=in_region,
                is_whitespace=bool(TS.is_non_semantic_whitespace(
                    scan.text_of[key][atom.start:atom.end]))))
        atoms.sort(key=lambda a: (a.fragment_key, a.start, a.end))
        # 只取**本表范围键**上的扫描问题。比较用**解析出的元组**，不用子串包含：
        # `"(1, 2, 3)" in "...(1, 2, 30)..."` 会让一张表的缺陷被记到另一张表上。
        relevant = sorted(
            p for p in scan.problems
            if _problem_key(p) in scope_keys)
        readings.append(TableScopeReading(
            table_id=table.table_id, table_locator=table.table_locator,
            page_number=table.page_number, page_bbox=box,
            region_fragment_keys=tuple(sorted(region_keys)),
            claims=tuple(sorted(claims)), atoms=tuple(atoms),
            scan_problems=tuple(relevant)))
    return tuple(readings)


def table_scope_reading_for(verified_span: Any,
                            table_id: str) -> TableScopeReading:
    """按表身份取**一张**表的范围读数（找不到即 fail-closed，不返回空读数）。"""
    readings = {r.table_id: r for r in build_table_scope_readings(verified_span)}
    reading = readings.get(str(table_id))
    if reading is None:
        _err(f"表 {table_id!r} 不在本次图侧构建的成员集中，无法取范围读数"
             "（fail-closed；不得用空读数冒充「无残余」）")
    return reading


@dataclass(frozen=True)
class _GapIntervalPlan:
    """typed gap 的**精确覆盖区间**计划（§六）。

    `by_entry`：缺口身份键（与 `_build_gap_entries` 的 `TableGapEntry` 身份字段逐项
    相同）→ 该缺口精确覆盖的真实 LayoutSpan 字符区间，写入
    `TableGapEntry.source_intervals`。它让"某段字符已被**这个** typed gap 正式延期"
    成为可回读、可对着当次 PageLayout 重算的载荷事实，而不是一句自报。

    `covering`：**仅**"区域型"缺口（候选拒绝 / 处置未决）的原子归属索引，用于
    `layout_text` 层新增的 `fmc-3` 资格路径：一个原子区间**恰好被一条**可延期种类的
    区域型缺口覆盖时才计 `registered_deferred`；被两条同时覆盖则**不猜**，留在
    `residual_gap` 并记 `layout_text_gap_interval_overlap`。

    component 型缺口**不进入** `covering`：它们的精确区间就是 component 自己的
    `LayoutHit`，`fmc-2` 既有资格路径已经逐字使用它；再进本索引只会让同一字符被两条
    路径同时主张，那是放宽而不是收紧。同理，阻断性种类（`upstream_table_scope_miss` /
    `root_identity_mismatch`）也不进入 `covering`：它们表示**尚未解释的上游冲突**，
    不是一次正当延期。
    """

    by_entry: dict
    covering: dict
    deferrable: frozenset

    @staticmethod
    def empty() -> "_GapIntervalPlan":
        return _GapIntervalPlan({}, {}, frozenset())


def _merge_claimed_atoms(atoms: Sequence[_ScanAtom]) -> tuple:
    """把一组原子区间按片段合并成 `TableGapSourceInterval` 元组。

    只在**首尾相接**时合并：两份被同一缺口覆盖但中间隔着未覆盖字符的区间不得被粘成
    一段——那会把"这里没有覆盖"涂掉。
    """
    by_fragment: dict = {}
    for atom in atoms:
        by_fragment.setdefault(atom.fragment_key, set()).add(
            (atom.start, atom.end))
    out: list = []
    for fragment_key in sorted(by_fragment):
        merged: list = []
        for a, b in sorted(by_fragment[fragment_key]):
            if merged and merged[-1][1] == a:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        for a, b in merged:
            out.append(TS.TableGapSourceInterval(
                page_number=fragment_key[0], line_index=fragment_key[1],
                span_index=fragment_key[2], span_char_range=(a, b)))
    return tuple(out)


def _gap_interval_plan(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                       built: TB.TableBuildResult, decisions: Sequence[Any],
                       bindings: Sequence[Any],
                       scan: _LayoutTextScan) -> _GapIntervalPlan:
    """算出"哪些 typed gap 精确覆盖哪些真实字符"（§六）。

    只用一个判据决定一个原子区间能不能被缺口覆盖：它**本来就会落入
    `residual_gap`**。也就是说，本计划**不可能**把任何已经被 cell / 表题 / 段落 /
    component hit 消费的字符记进缺口——它不是新的兜底项，而是给"原本无人认领的字符"
    补一条精确的、有类型的终态记录。取不到类型化终态的（没有覆盖它的区域型缺口、或
    只有阻断性种类、或同时被两条覆盖）就**照旧留在残余里**，绝不改名。
    """
    binding_of: dict = {b.component_id: b for b in bindings}

    def _reason(atom: _ScanAtom) -> str | None:
        return _deferred_rejection_reason(
            scan.local_hits.get(atom.fragment_key, ()), binding_of,
            atom.start, atom.end)

    residual = tuple(a for a in scan.atoms
                     if a.kind == _ATOM_FREE and _reason(a) is not None)
    if not residual:
        return _GapIntervalPlan.empty()

    by_page: dict = {}
    by_line: dict = {}
    for atom in residual:
        by_page.setdefault(atom.fragment_key[0], []).append(atom)
        by_line.setdefault(atom.fragment_key[:2], []).append(atom)

    groups: dict = {}

    def _claim(entry_key: tuple, atoms: Sequence[_ScanAtom]) -> None:
        if not atoms:
            return
        groups.setdefault(entry_key, set()).update(atoms)

    # (a) 候选拒绝：矩形内的自由原子。矩形按**真实 LayoutSpan 的 bbox**判定重叠，
    #     与"这个候选覆盖了哪些排版片段"同一口径（不是行级粗判）。
    for (page_number, bbox, reason, _blocks) in built.rejected:
        kind = _rejection_gap_kind(root, ctx, reason, page_number, bbox)
        box = tuple(float(v) for v in bbox)
        picked = [atom for atom in by_page.get(page_number, ())
                  if _overlap_area(scan.span_of[atom.fragment_key].bbox, box) > 0.0]
        _claim((kind, f"candidate_rejected:{reason}", page_number, None, None,
                None), picked)

    # (b) 处置未决：该 disposition 行范围内的自由原子（只处理单页范围；跨页范围没有
    #     逐页行区间，按"该缺口不对应具体字符区间"如实留空，不猜也不整页覆盖）。
    decision_of = {d.disposition_id: d for d in decisions}
    for d in root.dispositions:
        if d.range_kind not in TB.TABLE_DISPOSITION_KINDS:
            continue
        if d.start_page != d.end_page:
            continue
        decision = decision_of.get(d.disposition_id)
        if decision is None:
            continue
        if decision.decision == "unresolved_geometry":
            entry_key = ("unresolved_geometry", "disposition_unresolved_geometry",
                         None, None, None, d.disposition_id)
        elif decision.decision == "unsupported_table_structure":
            entry_key = ("unsupported_table_structure",
                         "disposition_unsupported_structure",
                         None, None, None, d.disposition_id)
        else:
            continue
        picked: list = []
        for li in range(d.start_line, d.end_line + 1):
            picked.extend(by_line.get((d.start_page, li), ()))
        _claim(entry_key, picked)

    by_entry: dict = {}
    covering: dict = {}
    deferrable: set = set()
    for entry_key in sorted(groups, key=lambda k: tuple(str(x) for x in k)):
        atoms = groups[entry_key]
        by_entry[entry_key] = _merge_claimed_atoms(sorted(
            atoms, key=lambda a: (a.fragment_key, a.start, a.end)))
        if entry_key[0] not in TS.CONSERVATION_DEFERRED_GAP_KINDS:
            continue
        deferrable.add(entry_key)
        for atom in atoms:
            covering.setdefault(atom.plan_key, set()).add(entry_key)
    return _GapIntervalPlan(
        by_entry=by_entry,
        covering={k: tuple(sorted(v)) for k, v in covering.items()},
        deferrable=frozenset(deferrable))


def _layout_text_layer(scan: _LayoutTextScan, binding_of: dict,
                       plan: _GapIntervalPlan) -> tuple:
    """Layout 文本守恒：**表相关文本域**内每个原子区间的字符恰好归一类。

    `registered_deferred` 有**两条**互不重叠的资格路径，缺一即落入 `residual_gap`：

    (1) `fmc-2` 的 component 路径（四条同时成立）：

        1. 覆盖该区间的 component **恰好一个**（`owners == 1`）；
        2. 该 component 的正式终态是 `pending` 或 `rejected`（不是 `final_span` /
           `table_object`——那种情况下这段文本已被别处消费，应走 cell / paragraph）；
        3. 该 binding 携带**typed gap 码**且至少一个属于
           `CONSERVATION_DEFERRED_GAP_KINDS`；
        4. 该区间的字符范围可由该 component 的 `LayoutHit.span_char_range`（正式来源）
           重新计算，且 hit 必须完整落在本片段内（越界记
           `layout_text_hit_out_of_span`）。

    (2) `fmc-3` 新增的 typed gap 路径：该原子区间**恰好被一条**可延期种类的区域型
        缺口（候选拒绝 / 处置未决）的 `source_intervals` 覆盖。被两条覆盖时记
        `layout_text_gap_interval_overlap` 并**留在残余里**——不猜。

    两条路径的覆盖集合**按构造互斥**：路径 (1) 只在 component hit 覆盖该区间时成立，
    而缺口区间计划里的区域型原子只取"路径 (1) 不成立"的那些。因此一个字符不可能同时
    被两条路径主张，也不可能既被 gap 覆盖又被 cell / paragraph 消费。

    任何一条不成立都进 `residual_gap` 并留下可诊断的问题码。因此"已延期"与"没人管"
    在载荷里是两项不同的量，且残余无法通过改名 / 排除 / 兜底项变成 0。
    """
    counts = {"cell_source_ref": 0, "caption_unit_note_ref": 0,
              "final_paragraph_ref": 0, "registered_deferred": 0,
              "non_semantic_whitespace": 0, "residual_gap": 0}
    problems: list = list(scan.problems)
    for atom in scan.atoms:
        n = atom.length
        if atom.kind == _ATOM_CELL:
            counts["cell_source_ref"] += n
            continue
        if atom.kind == _ATOM_ADORN:
            counts["caption_unit_note_ref"] += n
            continue
        if atom.kind == _ATOM_PARAGRAPH:
            counts["final_paragraph_ref"] += n
            continue
        key = atom.fragment_key
        reason = _deferred_rejection_reason(
            scan.local_hits.get(key, ()), binding_of, atom.start, atom.end)
        if reason is None:
            counts["registered_deferred"] += n
            continue
        cover = plan.covering.get(atom.plan_key, ())
        if len(cover) == 1 and cover[0] in plan.deferrable:
            counts["registered_deferred"] += n
            continue
        if len(cover) > 1:
            problems.append(f"layout_text_gap_interval_overlap:{key}")
        # 有凭据的延期优先于"非语义空白"：前者信息量更高，且这条顺序让新增分项
        # **只能**从原本落入 `residual_gap` 的区间里取值。判据是 `table_schema` 里的
        # **唯一**实现（builder 与 verifier 共用同一份"什么算非语义空白"，因此两者
        # 不可能各自漂移）。
        if TS.is_non_semantic_whitespace(scan.text_of[key][atom.start:atom.end]):
            counts["non_semantic_whitespace"] += n
            continue
        counts["residual_gap"] += n
        prefix = ("layout_text_residual_in_table" if key in scan.in_table_region
                  else "layout_text_residual")
        problems.append(f"{prefix}:{key}#{reason}")
    return counts, scan.total, tuple(sorted(set(problems)))


def _deferred_rejection_reason(local_hits: Sequence[tuple], binding_of: dict,
                               a: int, b: int) -> str | None:
    """区间 `[a, b)`（片段内）能否计为 `registered_deferred`。

    返回 `None` 表示资格成立；否则返回**拒绝理由**（写进问题码，便于复核与反例）。
    判定只看两样正式来源：component 的 `LayoutHit.span_char_range` 与 binding 的
    正式终态 / typed gap 码——不读表格、不读候选、不读自报字段。
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


def _evidence_interval_layer(root: FinalMaterialBuildInput,
                             bindings: Sequence[Any]) -> tuple:
    by_component: dict = {}
    for b in bindings:
        by_component.setdefault(b.component_id, []).append(b)
    counts = {"preserved": 0, "missing": 0, "duplicated": 0}
    problems: list = []
    total = 0
    for component in root.components:
        lo, hi = component.evidence_char_range
        length = int(hi) - int(lo)
        total += length
        mine = by_component.get(component.component_id, ())
        if not mine:
            counts["missing"] += length
            problems.append(f"evidence_missing:{component.component_id}")
            continue
        if len(mine) > 1:
            counts["preserved"] += length
            counts["duplicated"] += (len(mine) - 1) * length
            problems.append(f"evidence_duplicated:{component.component_id}")
            continue
        if mine[0].evidence_char_range != component.evidence_char_range:
            counts["missing"] += length
            problems.append(f"evidence_interval_mismatch:{component.component_id}")
            continue
        counts["preserved"] += length
    return counts, total, tuple(sorted(set(problems)))


def _build_conservation(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                        tables: Sequence[Any], decisions: Sequence[Any],
                        bindings: Sequence[Any], scan: _LayoutTextScan,
                        plan: _GapIntervalPlan) -> TS.FinalMaterialConservation:
    """四层守恒。

    `layout_text` 层消费的是**已经算好的**原子分区与缺口区间计划：`conservation` 与
    `gaps` 必须看见同一份分区，否则"缺口精确覆盖了哪些字符"与"哪些字符仍无人认领"
    会在两条代码路径上各自漂移。输入 DAG 因此是
    `tables/final spans → decisions → bindings/coverages → relations →
    layout scan → gap interval plan → conservation/gaps → synopses → snapshot`。
    """
    binding_of = {b.component_id: b for b in bindings}
    pairs = (
        ("disposition",) + _disposition_layer(root, decisions),
        ("component",) + _component_layer(root, bindings),
        ("layout_text",) + _layout_text_layer(scan, binding_of, plan),
        ("evidence_interval",) + _evidence_interval_layer(root, bindings),
    )
    layers: list = []
    all_problems: list = []
    for layer_kind, counts, total, problems in pairs:
        layer = _layer(layer_kind, counts, total, problems)
        layers.append(layer)
        # 文档级 problems 取自**构造完成后**的层：`_layer` 可能为"算术凑平但资格不成立"
        # 补一条 `layer_unbalanced:*`，那条码也必须出现在文档级，否则文档级 problems
        # 就可以"没看见"层内问题（`FinalMaterialConservation` 会拒绝这种载荷）。
        all_problems.extend(layer.problems)
    _require([x.layer_kind for x in layers] == list(TS.CONSERVATION_LAYER_KINDS),
             "四层守恒必须按固定顺序且恰为四层")
    return TS.FinalMaterialConservation.create(
        schema_version=V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION,
        upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
        layers=tuple(layers), problems=tuple(sorted(set(all_problems))))


# ---------------------------------------------------------------------------
# 9. 结构缺口台账（§19.4.3）
# ---------------------------------------------------------------------------

def _gap(kind: str, detail: str, *, page: int | None = None,
         table_id: str | None = None, component_id: str | None = None,
         disposition_id: str | None = None,
         intervals: Sequence[TS.TableGapSourceInterval] = ()) -> TS.TableGapEntry:
    return TS.TableGapEntry(
        gap_kind=kind, detail_code=detail, page_number=page, table_id=table_id,
        component_id=component_id, disposition_id=disposition_id,
        blocks_document_capability=(kind in TS.GAP_BLOCKING_KINDS),
        source_intervals=tuple(intervals))


def _candidate_has_text(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                        page_number: int, bbox: Sequence[float]) -> bool:
    page = ctx.page(page_number)
    if page is None:
        return False
    for line in page.lines:
        if line.is_furniture:
            continue
        if _overlap_area(line.bbox, bbox) <= 0.0:
            continue
        for span in line.spans:
            if span.text.strip() and _overlap_area(span.bbox, bbox) > 0.0:
                return True
    return False


def _candidate_geometry_evidence(ctx: TB.TableBuildContext, page_number: int,
                                 bbox: Sequence[float]) -> dict:
    """`(页, 矩形)` 上**全部**几何候选的合并结构证据（与枚举顺序无关）。

    同一矩形可能由多个通道（ruled / text / 冻结范围）各产生一个候选，这里取并集：
    "有没有可闭合的表状网格""有没有图元拓扑""形状尺寸"，都是候选**自带**的真实几何，
    不是调用方自报字段。
    """
    want = tuple(quantize(float(v)) for v in bbox)
    ev = {"closed_grid": False, "edge_count": 0, "intersection_count": 0,
          "row_count": 0, "column_count": 0, "cell_count": 0}
    for cand in ctx.geometry.candidates:
        if cand.page_number != page_number:
            continue
        if tuple(quantize(float(v)) for v in cand.bbox) != want:
            continue
        ev["closed_grid"] = ev["closed_grid"] or bool(cand.closed_grid)
        ev["edge_count"] = max(ev["edge_count"], int(cand.edge_count))
        ev["intersection_count"] = max(ev["intersection_count"],
                                       int(cand.intersection_count))
        ev["row_count"] = max(ev["row_count"], int(cand.row_count))
        ev["column_count"] = max(ev["column_count"], int(cand.column_count))
        ev["cell_count"] = max(ev["cell_count"], int(cand.cell_count))
    return ev


def _page_spanning(page: Any, bbox: Sequence[float]) -> bool:
    """候选矩形是否覆盖了页面的大部分（"整页回退框"）。"""
    if page is None:
        return False
    try:
        width = float(page.width)
        height = float(page.height)
    except (AttributeError, TypeError, ValueError):
        return False
    if width <= 0.0 or height <= 0.0:
        return False
    ratio = TB.PAGE_SPANNING_RATIO
    return ((float(bbox[2]) - float(bbox[0])) >= ratio * width
            and (float(bbox[3]) - float(bbox[1])) >= ratio * height)


def _candidate_gap_kind(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                        page_number: int, bbox: Sequence[float]) -> str:
    """一个被拒绝的候选到底是什么——按**结构证据**分类（§四）。

    旧实现只问"矩形里有没有文字"：有文字 ⇒ `unsupported_table_structure`，没文字 ⇒
    `visual_object_not_table`。组织结构图、流程图这类视觉对象**通常带文字**，于是
    `visual_object_not_table` 这个诚实终态在真实文档上根本不可能出现。现在改为四条
    结构证据共同判定，文字只是其中之一：

    1. 能不能重建出**可闭合的表状网格**（≥2 列 ≥2 行且有真实 cell）；
    2. 文字有没有形成稳定的行列对齐（同上，由真实 PageLayout 重建，不是自报）；
    3. 有没有**图元拓扑**（线条 / 交点）——节点框与连接线属于此类；
    4. 该矩形是不是"整页回退框"（pdfplumber 找不到表时的整页文本框）。

    判定顺序（**不猜**）：

    - 有表状网格 ⇒ 它确实长得像表，拒绝另有原因 ⇒ `unsupported_table_structure`；
    - 有图元拓扑而无表状网格、且不是整页回退 ⇒ 可证是**非表格视觉对象** ⇒
      `visual_object_not_table`（这也是 §四.6 要求可达的终态）；
    - 有图元拓扑**又**是整页回退 ⇒ 图元与整页文本混在一起，无法可靠区分 ⇒
      `unresolved_geometry`（§四.5：不得猜）；
    - 完全无文字（纯图元、无网格）⇒ `visual_object_not_table`；
    - 其余（有真实文字、无网格、无拓扑）⇒ `unsupported_table_structure`：
      它是"有真实范围但不足以建表"的诚实结果，**不**因为"有文字"就被当成表，
      也不因为"没网格"就被当成视觉对象。
    """
    text_present = _candidate_has_text(root, ctx, page_number, bbox)
    ev = _candidate_geometry_evidence(ctx, page_number, bbox)
    table_like = (ev["column_count"] >= 2 and ev["row_count"] >= 2
                  and ev["cell_count"] > 0)
    topology = ev["edge_count"] > 0 or ev["intersection_count"] > 0
    spanning = _page_spanning(ctx.page(page_number), bbox)
    if table_like:
        return "unsupported_table_structure"
    if topology and not spanning:
        return "visual_object_not_table"
    if topology and spanning:
        return "unresolved_geometry"
    if not text_present:
        return "visual_object_not_table"
    return "unsupported_table_structure"


#: `_build_final_spans` 的问题码 → 缺口种类（**全函数**；未登记即 fail-closed）。
#:
#: 三者语义不同，**不得**一律记成 `root_identity_mismatch`：
#: - `final_span_threshold_not_reestablished`：§19.7.2 明确"无法证明边界时保留
#:   pending/non-citable 和 gap"。冻结 0.85 阈值下自然会有弱边界 span 落下来，这是
#:   合法的负面终态，**不**阻断文档；
#: - `boundary_cause_missing` / `ts4_confidence_recompute_mismatch`：TS4 记录自称的
#:   内容与真实来源/冻结策略不符——这才是 §19.9 的 `root_identity_mismatch`。
FINAL_SPAN_PROBLEM_GAP_KINDS: dict = {
    "final_span_threshold_not_reestablished": "provenance_incomplete",
    "boundary_cause_missing": "root_identity_mismatch",
    "ts4_confidence_recompute_mismatch": "root_identity_mismatch",
}

#: 候选拒绝理由 → 缺口种类（**全函数**）。`blocks_document` 与它必须一致。
#:
#: 只有"几何覆盖了已冻结的 regular body span"是 §19.5.3 点名的 scope miss，其余
#: 一律是 TS5 自己在冻结范围内**无法建表**的诚实结果：
#: - `outside_frozen_table_scope`：浮在普通正文/视觉对象上，由 `_candidate_gap_kind`
#:   按结构证据在 `visual_object_not_table` / `unresolved_geometry` /
#:   `unsupported_table_structure` 之间裁决；
#: - `candidate_straddles_frozen_range` / `candidate_not_uniquely_mapped`：§19.5.3
#:   的裁决顺序在这些情形下只允许 `unresolved_geometry`，不得选"第一个"；
#: - 其余（`grid_*` / `*_below_min` / 装配期 `UnsupportedStructureError` / 散文反例门 /
#:   同片段重复主张）都是"有真实范围但不足以建表"，同样由 `_candidate_gap_kind` 裁决。
CANDIDATE_REJECTION_GAP_KINDS: dict = {
    "geometry_overlaps_frozen_regular_span": "upstream_table_scope_miss",
    "no_geometric_grid_evidence": None,        # 没有形状，不主张几何
    "outside_frozen_table_scope": None,        # 由 `_candidate_has_text` 决定
    "candidate_straddles_frozen_range": "unresolved_geometry",
    "candidate_not_uniquely_mapped": "unresolved_geometry",
    "grid_not_reconstructible": None,
    "grid_not_closed": None,
    "column_count_below_min": None,
    "row_count_below_min": None,
    # 装配期（门之后）的拒绝理由：见 `TB.build_tables`。
    "unsupported_table_structure": None,
    "prose_negative_gate": None,
    "cell_too_long": None,
    # 与另一个已成立的表主张同一批真实 LayoutSpan 片段（§六：同一个字符不得被两张
    # 表同时消费）。按结构证据分类：这里确实有真实文字，只是不能再成为第二张表。
    "candidate_overlaps_accepted_table": None,
}


def _rejection_gap_kind(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                        reason: str, page_number: int,
                        bbox: Sequence[float]) -> str:
    """候选拒绝理由 → 缺口种类（**唯一**映射实现）。

    `_build_gap_entries`（写台账）与 `_gap_interval_plan`（算精确区间）必须得到同一个
    种类，否则"这条缺口覆盖了哪些字符"会与"这条缺口是什么"对不上。因此两处都只调用
    本函数，不各自展开映射。
    """
    _require(reason in CANDIDATE_REJECTION_GAP_KINDS,
             f"未登记的候选拒绝理由 {reason!r}（缺口种类映射必须是全函数）")
    kind = CANDIDATE_REJECTION_GAP_KINDS[reason]
    if kind is None:
        # 范围真实但不足以建表：按**结构证据**（网格 / 行列对齐 / 图元拓扑 / 整页
        # 回退）区分"非表格视觉对象""未决几何"与"结构不足"，不以"有没有文字"二分。
        kind = _candidate_gap_kind(root, ctx, page_number, bbox)
    return kind


def _build_gap_entries(root: FinalMaterialBuildInput, ctx: TB.TableBuildContext,
                       built: TB.TableBuildResult, decisions: Sequence[Any],
                       bindings: Sequence[Any], final_span_problems: Sequence[str],
                       multi_block: Sequence[str],
                       conservation_problems: Sequence[str],
                       unproven_reference_count: int,
                       plan: _GapIntervalPlan | None = None) -> list:
    """缺口台账。

    `plan`（`tsg-2` / §六）给出"这条缺口**精确覆盖**了哪些真实 LayoutSpan 字符"。
    只有"区域型"缺口（候选拒绝 / 处置未决）会被填上区间：它们的覆盖范围正是此前
    `residual_gap` 里那批"无人认领"的字符。component 型缺口的精确区间是 component
    自己的 `LayoutHit`（`fmc-2` 既有路径），其余缺口（final span / 多块 / 引用未证 /
    守恒问题码汇总）本来就不对应任何具体字符区间，如实留空元组。
    """
    def _intervals(key: tuple) -> tuple:
        if plan is None:
            return ()
        return plan.by_entry.get(key, ())

    entries: list = []
    for (page_number, bbox, reason, blocks_document) in built.rejected:
        kind = _rejection_gap_kind(root, ctx, reason, page_number, bbox)
        _require(blocks_document == (kind in TS.GAP_BLOCKING_KINDS),
                 f"候选拒绝理由 {reason!r} 的 blocks_document 与缺口种类 {kind!r} "
                 "不一致（阻断性必须由种类单点决定）")
        key = (kind, f"candidate_rejected:{reason}", page_number, None, None, None)
        entries.append(_gap(kind, f"candidate_rejected:{reason}",
                            page=page_number, intervals=_intervals(key)))

    for (first, second) in built.ambiguous_continuations:
        # 两端各留一条（detail 里带上对方 id），使两个 table 都被显式记账。
        entries.append(_gap("ambiguous_continuation",
                            f"ambiguous_continuation:{second}", table_id=first))
        entries.append(_gap("ambiguous_continuation",
                            f"ambiguous_continuation:{first}", table_id=second))

    for decision in decisions:
        if decision.decision == "unresolved_geometry":
            key = ("unresolved_geometry", "disposition_unresolved_geometry",
                   None, None, None, decision.disposition_id)
            entries.append(_gap("unresolved_geometry",
                                "disposition_unresolved_geometry",
                                disposition_id=decision.disposition_id,
                                intervals=_intervals(key)))
        elif decision.decision == "unsupported_table_structure":
            key = ("unsupported_table_structure",
                   "disposition_unsupported_structure",
                   None, None, None, decision.disposition_id)
            entries.append(_gap("unsupported_table_structure",
                                "disposition_unsupported_structure",
                                disposition_id=decision.disposition_id,
                                intervals=_intervals(key)))

    for disposition_id, block_count in _mixed_block_dispositions(root, ctx,
                                                                 decisions):
        entries.append(_gap("provenance_incomplete",
                            f"mixed_block_disposition_range:{block_count}",
                            disposition_id=disposition_id))

    # 每个 component 自己的 typed gap：`pending` 与 `rejected` 都要登记（今天 `rejected`
    # 恒为空码，但台账必须与 §19.9.3 第 3 层的资格判据**同一口径**，否则"缺口身份与
    # component 精确对应"会随实现漂移）。detail 前缀区分两种终态，身份字段一致。
    for binding in bindings:
        if binding.admission not in ("pending", "rejected"):
            continue
        for code in binding.gap_codes:
            entries.append(_gap(code,
                                f"binding_{binding.admission}:"
                                f"{binding.admission_reason}",
                                table_id=binding.table_id,
                                component_id=binding.component_id,
                                disposition_id=binding.disposition_id))

    for table_id in multi_block:
        entries.append(_gap("provenance_incomplete",
                            "multi_block_table_coverage_envelope_unavailable",
                            table_id=table_id))

    for problem in final_span_problems:
        code, _, disposition_id = problem.partition(":")
        kind = FINAL_SPAN_PROBLEM_GAP_KINDS.get(code)
        _require(kind is not None,
                 f"未登记的 final span 问题码 {code!r}（缺口种类映射必须是全函数）")
        entries.append(_gap(kind, code, disposition_id=disposition_id or None))

    for problem in conservation_problems:
        entries.append(_gap("provenance_incomplete", problem.split(":", 1)[0]))

    if unproven_reference_count:
        entries.append(_gap("provenance_incomplete",
                            "reference_target_not_proven"))
    return entries


def _dedupe_gaps(entries: Sequence[TS.TableGapEntry],
                 root: FinalMaterialBuildInput) -> TS.TableStructureGap:
    """同一身份键只留一条；**区间**必须逐条相同，否则 fail-closed。

    同一身份键出现多条时（例如同一页上多个候选因同一理由被拒），它们的
    `source_intervals` 来自同一个缺口区间计划，因此必然逐项相同。若不同，说明
    "哪条缺口覆盖哪些字符"这件事在两条路径上已经不一致——那是 P1，必须当场拒绝，
    绝不能靠 `setdefault` 静默留下其中一条。
    """
    pool: dict = {}
    for entry in entries:
        key = (entry.gap_kind, entry.detail_code, entry.page_number or 0,
               entry.table_id or "", entry.component_id or "",
               entry.disposition_id or "")
        prior = pool.get(key)
        if prior is None:
            pool[key] = entry
            continue
        _require(prior.source_intervals == entry.source_intervals,
                 f"同一缺口身份 {key!r} 出现两份不同的覆盖区间"
                 "（缺口区间计划与台账必须逐项一致）")
    ordered = tuple(pool[k] for k in sorted(pool))
    blocking = sum(1 for e in ordered if e.blocks_document_capability)
    return TS.TableStructureGap.create(
        schema_version=V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION,
        upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
        entries=ordered, blocking_entry_count=blocking)


# ---------------------------------------------------------------------------
# 10. synopsis（§19.10）
# ---------------------------------------------------------------------------

def _build_synopses(root: FinalMaterialBuildInput,
                    final_spans: Sequence[Any]) -> tuple:
    node_ids = sorted({n.node_id for n in root.outline.nodes})
    return SY.build_final_navigation_synopses(
        node_ids=node_ids, final_spans=tuple(final_spans),
        dispositions=root.dispositions, policy=root.policy)


# ---------------------------------------------------------------------------
# 11. 唯一 public 入口
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _BuildOutcome:
    """一次构建的全部产物（快照 + 审计 + 根与 context，仅供诊断投影使用）。"""

    snapshot: TS.FinalMaterialStructureSnapshot
    built: TB.TableBuildResult
    root: FinalMaterialBuildInput
    ctx: TB.TableBuildContext


def _build_all(verified_span: Any) -> _BuildOutcome:
    """§19.3.1：由唯一正式输入构建终端 `fms-1` 快照。

    身份 DAG（无环，§19.4.4）：
    `roots → upstream fingerprint → tables/final spans → decisions →
    bindings/coverages → relations → layout scan → gap interval plan →
    conservation/gaps → synopses → snapshot`。
    每一步只引用**已经完成**的上游对象；本函数不产生任何回指。

    `layout scan` 与 `gap interval plan` 之所以排在 `conservation` **之前**：`fmc-3` 的
    `registered_deferred` 新资格路径要求守恒层能看见"哪些 typed gap 精确覆盖了哪些
    字符"。计划本身只读冻结范围 / 表 / 绑定（不读守恒、不读缺口台账），因此这个顺序
    仍然是 DAG，且守恒与台账消费的是**同一份**分区与区间。

    候选审计**不进入**任何 content fingerprint：它记录的是"哪些候选被拒、为什么"，
    属于诊断投影，不是身份对象。
    """
    root = _read_roots(verified_span)
    ctx = _build_context(root)
    built = TB.build_tables(ctx)

    span_build = _build_final_spans(root)
    final_spans = span_build.spans
    decisions = _build_decisions(root, ctx, built, final_spans)
    tables = tuple(sorted((bt.table for bt in built.tables),
                          key=lambda t: t.table_locator))
    consumption = _measure_consumption(ctx, tuple(built.tables))
    bindings = _build_bindings(root, consumption, decisions,
                               span_build.span_of_component)
    coverages = _build_coverages(root, ctx, tables)
    reference_relations = _reference_relations(root, tables)
    relations = tuple(sorted(
        list(built.relations) +
        list(_adjacency_relations(root, tables, final_spans)) +
        list(reference_relations),
        key=lambda r: r.relation_locator))
    scan = _layout_text_scan(root, ctx, tables)
    plan = _gap_interval_plan(root, ctx, built, decisions, bindings, scan)
    conservation = _build_conservation(root, ctx, tables, decisions, bindings,
                                       scan, plan)

    multi_block = [c.table_id for c in coverages
                   if c.evidence_char_range == (0, 0)]
    unproven = len(_unresolved_cross_references(root)) - len(reference_relations)
    gaps = _dedupe_gaps(_build_gap_entries(
        root, ctx, built, decisions, bindings, span_build.problems, multi_block,
        conservation.problems, unproven, plan), root)
    synopses = _build_synopses(root, final_spans)
    _require(len(synopses) == len({s.node_id for s in synopses}),
             "同一节点不得出现两条 final synopsis")

    snapshot = TS.FinalMaterialStructureSnapshot.create(
        schema_version=V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION,
        builder_version=BUILDER_VERSION,
        final_span_schema_version=V.FINAL_SPAN_SCHEMA_VERSION,
        document_id=root.document_id,
        document_version=root.document_version,
        evidence_set_version=root.evidence_set_version,
        page_layout_id=root.page_layout_id,
        outline_id=root.outline_id,
        verified_span_snapshot_id=root.verified_span_snapshot_id,
        upstream_dependency_fingerprint=root.upstream_dependency_fingerprint,
        tables=tables, final_spans=final_spans, decisions=decisions,
        relations=relations, bindings=bindings, coverages=coverages,
        gaps=gaps, conservation=conservation, synopses=tuple(synopses),
        component_count=len(bindings), table_count=len(tables),
        final_span_count=len(final_spans),
        blocking_gap_count=gaps.blocking_entry_count)
    return _BuildOutcome(snapshot=snapshot, built=built, root=root, ctx=ctx)


def build_final_material_snapshot(verified_span: Any
                                  ) -> TS.FinalMaterialStructureSnapshot:
    """正式入口：只返回终端快照（供生产链与既有调用方使用）。"""
    return _build_all(verified_span).snapshot


def build_final_material_with_audit(verified_span: Any) -> tuple:
    """`(snapshot, audit_rows)`：一次构建同时给出快照与 §19.14.1 候选审计。

    供 evaluation runner 使用，避免为审计再建一次（第二次构建会掩盖"两次结果是否
    一致"这件事——一致性应由 `evals/test_tree_table_*` 的确定性用例显式断言）。
    审计行**不参与**任何指纹。
    """
    outcome = _build_all(verified_span)
    return outcome.snapshot, outcome.built.audit


# ---------------------------------------------------------------------------
# 12. self-check 与 CLI
# ---------------------------------------------------------------------------

def landing_mapping_table() -> dict:
    """§19.9.1 的 11 类 landing → 允许终态（只读诊断，不参与判定）。"""
    return {landing: TS.COMPONENT_LANDING_ADMISSIONS[landing]
            for landing in SS.COMPONENT_LANDINGS}


def self_check() -> dict:
    """只读自检：不接触任何仓库资产，不构建正式快照。"""
    problems: list = []
    if BUILDER_VERSION != "fmb-2":
        problems.append(f"builder 版本必须为 fmb-2，得到 {BUILDER_VERSION!r}")
    if ISSUER_VERSION != "vfmi-1":
        problems.append(f"issuer 版本必须为 vfmi-1，得到 {ISSUER_VERSION!r}")
    if CAPABILITY_KIND not in SS.CAPABILITY_KINDS:
        problems.append(f"final capability {CAPABILITY_KIND!r} 未登记在 "
                        "CAPABILITY_KINDS")
    if FINAL_SPAN_BUILDER_VERSION != "sbf-1":
        problems.append("final span 算法版本必须为 sbf-1")
    if len(SS.COMPONENT_LANDINGS) != 11:
        problems.append("component landing 必须恰为 11 类，得到 "
                        f"{len(SS.COMPONENT_LANDINGS)}")
    if set(landing_mapping_table()) != set(SS.COMPONENT_LANDINGS):
        problems.append("landing 映射必须覆盖全部 11 类")
    for landing in SS.COMPONENT_LANDINGS:
        allowed = TS.COMPONENT_LANDING_ADMISSIONS[landing]
        if not set(allowed) <= set(TS.BINDING_ADMISSIONS):
            problems.append(f"{landing} 的允许终态含未登记值")
        for key, reasons in (
                ("pending", TS.COMPONENT_LANDING_PENDING_REASONS.get(landing, ())),
                ("rejected",
                 TS.COMPONENT_LANDING_REJECTED_REASONS.get(landing, ()))):
            if key in allowed and not reasons:
                problems.append(f"{landing} 允许 {key} 但没有登记理由")
        for reason in allowed:
            if reason not in TS.BINDING_ADMISSIONS:
                problems.append(f"{landing} 的终态 {reason!r} 未登记")
    if set(_REJECTED_REASON_OF_LANDING) != set(
            TS.COMPONENT_LANDING_REJECTED_REASONS):
        problems.append("rejected 映射必须与 wire 层的封闭集合精确一致")
    for landing, reason in _REJECTED_REASON_OF_LANDING.items():
        if reason not in TS.COMPONENT_LANDING_REJECTED_REASONS[landing]:
            problems.append(f"{landing} → {reason!r} 不在登记理由内")
    covered = set(_CITABLE_REASON_OF_ADMISSION) | {"aligned_projected"}
    if covered != set(SS.COMPONENT_ADMISSION_REASONS):
        problems.append("可引用理由映射必须覆盖全部 admission_reason：缺 "
                        f"{sorted(set(SS.COMPONENT_ADMISSION_REASONS) - covered)}")
    if set(_CITABLE_REASON_OF_ADMISSION.values()) - set(TS.CITABLE_REASONS):
        problems.append("可引用理由映射产出了未登记的理由码")
    if TS.CONSERVATION_TOTAL_TERM != "total":
        problems.append("合计分项名必须为 total")
    for layer_kind in TS.CONSERVATION_LAYER_KINDS:
        if _total_kind(layer_kind) not in TS.CONSERVATION_TERM_KINDS:
            problems.append(f"{layer_kind} 层的合计语义类别未登记")
    groups = (set(ABSORBED_DECISIONS), set(KEPT_DECISIONS),
              set(STRUCTURAL_DECISIONS))
    union = set().union(*groups)
    if union != set(TS.TABLE_DECISION_KINDS):
        problems.append("decision 三分类必须穷尽 TABLE_DECISION_KINDS：缺 "
                        f"{sorted(set(TS.TABLE_DECISION_KINDS) - union)} / 多 "
                        f"{sorted(union - set(TS.TABLE_DECISION_KINDS))}")
    if sum(len(g) for g in groups) != len(union):
        problems.append("absorbed / kept / structural 三组必须互斥")
    if set(ABSORBED_DECISIONS) != set(TS.DECISION_TARGET_OF["table"]) or \
            set(KEPT_DECISIONS) != set(TS.DECISION_TARGET_OF["final_span"]) or \
            set(STRUCTURAL_DECISIONS) != set(TS.DECISION_TARGET_OF["none"]):
        problems.append("decision 三分与 wire 层逆查视图不一致")
    return {
        "module": "document_structure.final_material_builder",
        "builder_version": BUILDER_VERSION,
        "issuer_version": ISSUER_VERSION,
        "capability_kind": CAPABILITY_KIND,
        "final_span_builder_version": FINAL_SPAN_BUILDER_VERSION,
        "component_landing_count": len(SS.COMPONENT_LANDINGS),
        "component_landing_admissions": landing_mapping_table(),
        "binding_admissions": TS.BINDING_ADMISSIONS,
        "conservation_layers": TS.CONSERVATION_LAYER_KINDS,
        "table_schema_version": V.TABLE_SCHEMA_VERSION,
        "table_builder_version": V.TABLE_BUILDER_VERSION,
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

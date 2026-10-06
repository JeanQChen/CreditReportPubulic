# -*- coding: utf-8 -*-
"""TS4-A 的**唯一**正式组合根：受信 TS3 交接 → 不可变 span 快照（计划 §18.3–§18.9）。

三条不可让步的边界：

1. **唯一生产入口**。公开 builder 只接受 `VerifiedTS3Handoff` 与 `stage`；它**不接受**
   provider / gateway / `db_path` / policy path / plain `PageLayout` / plain
   `DocumentOutline` / plain `EvidenceSetAlignment`，也**不接受**调用者装配的
   `SpanBuildInput`。签名里根本没有这些字段——不是"校验之后再忽略"。
2. **资格来自本进程签发登记表**，不来自字段。`object.__new__`、`copy` / `deepcopy` /
   `pickle`、字段仿造、"把全部 ID 与 hash 同步重算一遍"都拿不到资格（§18.4.2）。
3. **一切上游事实都从受信根对象重算**：行状态来自由同一份受信 PDF 字节重建、并已
   canonical 比对过的结构终态；表格范围由本模块调用冻结的
   `OB.table_region_scopes(page_layout)` 重算；`block_char_length` 由**真实 Evidence
   文本**重算。没有任何调用方自报的 `line_states` / `table_scopes` / `node_by_line`。

三个签发域（`live` / `pinned_acceptance` / `testing`）严格隔离：公开
`build_span_snapshot` 只收 `live`；验收经不导出的 `_build_from_pinned_handoff`；
测试经 `_testing_build_span_snapshot`。三者**共用同一个私有纯算法核**，不存在第二套
实现。

TS4-A 恒为分布口径：`stage="distribution_only"`、`threshold=None`、
`completion_enabled=False`、`set_complete_supported=False`。本轮
`SPAN_CONFIDENCE_MIN` 保持 `None`，因此本模块可以产出可核验的导航简介，但**永不**
支持 `set_complete`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from document_structure import outline_builder as OB
from document_structure import span_policy as SP
from document_structure import span_schema as SS
from document_structure import synopsis as SY
from document_structure import versions as V
from document_structure.aligner import (
    EvidenceBlockInput,
    EvidenceSetSnapshot,
    VerifiedEvidenceSetAlignment,
    _align_verified,
    _is_non_ascending_char_map,
    _issue_pinned_alignment_from_frozen_rows,
    _member_of,
    assert_members_exact,
)
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    canonical_text,
    identity,
    sha256_canonical,
)
from document_structure.evidence_gateway import (
    FIXTURE_SOURCE_KIND,
    VerifiedCurrentEvidenceAuthority,
    _issue_fixture_evidence_authority,
    _issue_pinned_evidence_authority,
    current_set_query_contract_identity,
    readonly_primitive_identity,
)
from document_structure.layout_builder import (
    VerifiedPageLayout,
    _issue_cross_checked_layout,
)
from document_structure.normalization import tight
from document_structure.schema import (
    AlignmentRefusalRecord,
    DocumentOutline,
    OutlineSpan,
    PageLayout,
    TextAlignmentRecord,
    derive_span_content_fingerprint,
    derive_span_id,
    derive_span_locator,
    quantize,
)
from document_structure.span_schema import (
    BODY_ATTACHMENTS,
    BODY_LAYER_BUCKETS,
    BODY_RANGE_KINDS,
    COMPONENT_LANDINGS,
    DOCUMENT_LAYER_BUCKETS,
    STRUCTURE_SNAPSHOT_COUNT_KEYS,
    BodyRangeDisposition,
    ConservationGap,
    EvidenceConservationRow,
    LandingCount,
    LayerBucket,
    LayoutHit,
    LineStructureState,
    OutlineStructureSnapshot,
    SpanBuildSnapshot,
    SpanCitableCoverage,
    SpanConservation,
    SpanEvidenceComponent,
    SpanQualificationPolicy,
    TrustedBuildInput,
    _issue_capability,
    derive_line_identity,
    interval_length,
    intersect_intervals,
    invert_intervals,
    issued_capability,
    merge_intervals,
    partition_problems,
    subtract_intervals,
)

__all__ = [
    "SpanBuildError",
    "VerifiedTS3Handoff",
    "build_span_snapshot",
    "issue_live_ts3_handoff",
    "self_check",
]
# `SpanBuildInput` **不在** `__all__` 里：它是构建器内部的输入 capability（只能由
# `_build_span_input` 的私有工厂令牌创建，不可序列化 / copy / pickle），不是公开
# API。把它列在 `__all__` 会让"调用者自行构造组合根输入"看起来是被许可的旁路。


class SpanBuildError(SchemaValidationError):
    """TS4 正式构建失败（上游不闭合 / 表外原因 / 身份冲突 / 调用者越权）。"""


# ---------------------------------------------------------------------------
# 1. 模块私有工厂令牌与 `SpanBuildInput`
# ---------------------------------------------------------------------------

_FACTORY_TOKEN: object = object()
"""`SpanBuildInput` 的私有工厂令牌；不参与序列化、哈希或任何身份计算。"""


@dataclass(frozen=True, init=False)
class SpanBuildInput:
    """仅由 `_build_span_input()` 创建的非序列化内部 capability（§18.3.2）。

    它不是公开 wire input，也**不是**来源证明：只是一次正式调用内部把"已经核验过的
    真实对象"交给纯算法核的短生命周期容器。因此没有 `to_dict` / `from_dict`，
    `copy` / `deepcopy` / `pickle` 全部显式拒绝，`init=False` 让 `replace` 也不可用。
    """

    page_layout: PageLayout
    document_outline: DocumentOutline
    structure_snapshot: OutlineStructureSnapshot
    evidence_snapshot: EvidenceSetSnapshot
    evidence_blocks: tuple
    alignment_records: tuple
    alignment_refusals: tuple
    alignment_terminals: tuple
    qualification_policy: SpanQualificationPolicy
    policy_authority_fingerprint: str
    handoff: "VerifiedTS3Handoff"
    _factory_capability: Any

    @classmethod
    def _make(cls, *, token: object, **kwargs: Any) -> "SpanBuildInput":
        if token is not _FACTORY_TOKEN:
            raise SpanBuildError(
                "SpanBuildInput 只能由组合根内部工厂创建；调用者构造的输入不进入"
                "正式链路（fail-closed）")
        obj = object.__new__(cls)
        for name, value in kwargs.items():
            object.__setattr__(obj, name, value)
        object.__setattr__(obj, "_factory_capability", _FACTORY_TOKEN)
        return obj

    def to_dict(self) -> dict:
        raise SpanBuildError(
            "SpanBuildInput 是内部 capability，不得序列化（fail-closed）")

    def __copy__(self):
        raise SpanBuildError("SpanBuildInput 不可 copy")

    def __deepcopy__(self, memo):
        raise SpanBuildError("SpanBuildInput 不可 deepcopy")

    def __reduce__(self):
        raise SpanBuildError("SpanBuildInput 不可 pickle")


# ---------------------------------------------------------------------------
# 2. 受信 TS3 交接（`VerifiedTS3Handoff`）
# ---------------------------------------------------------------------------

class VerifiedTS3Handoff:
    """**已签发**的 TS3→TS4 交接能力（运行时对象，不可序列化）。

    它同时绑定：同次正式链路签发的 `VerifiedPageLayout` 与
    `VerifiedEvidenceSetAlignment`、由同一份受信 PDF 字节确定性重建的
    `DocumentOutline` / 结构终态、由正式 current Evidence authority 取得的
    `EvidenceSetSnapshot` 与**全量** blocks，以及解析出的版本化资格策略。

    注意它**持有受信版式能力对象本身**（`_layout_capability`），而不是只拿一份
    `PageLayout`：因此"把 `SpanBuildInput.page_layout` 换成另一个同形对象"不会让
    任何一步通过——资格取自签发登记表，字段只是字段。
    """

    __slots__ = ("_layout_capability", "_document_outline", "_structure_snapshot",
                 "_evidence_authority", "_evidence_snapshot", "_evidence_blocks",
                 "_alignment", "_policy", "_scope", "_source_kind",
                 "_issuer_version", "_policy_provider_authority_fingerprint",
                 "_structure_provider_authority_fingerprint",
                 "_evidence_gateway_authority_fingerprint",
                 "_terminal_provider_authority_fingerprint", "_handoff_identity",
                 "__weakref__")

    def __init__(self, *, layout_capability, document_outline, structure_snapshot,
                 evidence_authority, evidence_snapshot, evidence_blocks, alignment,
                 policy, scope, source_kind, issuer_version,
                 policy_provider_authority_fingerprint,
                 structure_provider_authority_fingerprint,
                 evidence_gateway_authority_fingerprint,
                 terminal_provider_authority_fingerprint,
                 handoff_identity) -> None:
        self._layout_capability = layout_capability
        self._document_outline = document_outline
        self._structure_snapshot = structure_snapshot
        self._evidence_authority = evidence_authority
        self._evidence_snapshot = evidence_snapshot
        self._evidence_blocks = tuple(evidence_blocks)
        self._alignment = alignment
        self._policy = policy
        self._scope = scope
        self._source_kind = source_kind
        self._issuer_version = issuer_version
        self._policy_provider_authority_fingerprint = (
            policy_provider_authority_fingerprint)
        self._structure_provider_authority_fingerprint = (
            structure_provider_authority_fingerprint)
        self._evidence_gateway_authority_fingerprint = (
            evidence_gateway_authority_fingerprint)
        self._terminal_provider_authority_fingerprint = (
            terminal_provider_authority_fingerprint)
        self._handoff_identity = handoff_identity

    # -- 只读访问 ---------------------------------------------------------

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
    def handoff_identity(self) -> str:
        return self._handoff_identity

    @property
    def layout_capability(self) -> VerifiedPageLayout:
        return self._layout_capability

    @property
    def page_layout(self) -> PageLayout:
        return self._layout_capability.layout

    @property
    def document_outline(self) -> DocumentOutline:
        return self._document_outline

    @property
    def structure_snapshot(self) -> OutlineStructureSnapshot:
        return self._structure_snapshot

    @property
    def evidence_authority(self) -> VerifiedCurrentEvidenceAuthority:
        return self._evidence_authority

    @property
    def evidence_snapshot(self) -> EvidenceSetSnapshot:
        return self._evidence_snapshot

    @property
    def evidence_blocks(self) -> tuple:
        return self._evidence_blocks

    @property
    def alignment(self) -> VerifiedEvidenceSetAlignment:
        return self._alignment

    @property
    def qualification_policy(self) -> SpanQualificationPolicy:
        return self._policy

    @property
    def policy_provider_authority_fingerprint(self) -> str:
        return self._policy_provider_authority_fingerprint

    def authority_fingerprints(self) -> dict:
        """四项 provider authority 指纹（全部由组合根重算，不采信对象自报）。"""
        return {
            "structure_provider_authority_fingerprint":
                self._structure_provider_authority_fingerprint,
            "evidence_gateway_authority_fingerprint":
                self._evidence_gateway_authority_fingerprint,
            "terminal_provider_authority_fingerprint":
                self._terminal_provider_authority_fingerprint,
            "policy_provider_authority_fingerprint":
                self._policy_provider_authority_fingerprint,
        }

    def identity(self) -> dict:
        return {
            "issuer_scope": self._scope,
            "source_kind": self._source_kind,
            "issuer_version": self._issuer_version,
            "handoff_identity": self._handoff_identity,
            "page_layout_id": self.page_layout.page_layout_id,
            "outline_id": self._document_outline.outline_id,
            "structure_snapshot_id": self._structure_snapshot.structure_snapshot_id,
            "evidence_set_version": self._evidence_snapshot.evidence_set_version,
            "snapshot_fingerprint": self._evidence_snapshot.fingerprint,
            "alignment_authority_fingerprint":
                self._alignment.authority_fingerprint,
            "policy_id": self._policy.policy_id,
            "authority_fingerprints": self.authority_fingerprints(),
        }

    # -- 反自证 -----------------------------------------------------------

    def to_dict(self) -> dict:
        raise SpanBuildError(
            "VerifiedTS3Handoff 是运行时能力，不得序列化；从磁盘读回的对象"
            "不是本进程签发的那一个（fail-closed）")

    def __copy__(self):
        raise SpanBuildError("VerifiedTS3Handoff 不可 copy：副本未在签发登记表中")

    def __deepcopy__(self, memo):
        raise SpanBuildError("VerifiedTS3Handoff 不可 deepcopy：副本未在签发登记表中")

    def __reduce__(self):
        raise SpanBuildError(
            "VerifiedTS3Handoff 不可 pickle：反序列化出的对象不是本进程签发的那一个")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<VerifiedTS3Handoff scope={self._scope!r} "
                f"{self.page_layout.document_id} "
                f"identity={self._handoff_identity[:12]}…>")


# ---------------------------------------------------------------------------
# 3. 四个内部受信 provider（模块私有；**不作为入口参数、不导出**）
# ---------------------------------------------------------------------------
#
# 它们只是"组合根内部把已核验对象取回来"的具名步骤，不是可替换的扩展点：正式
# builder/verifier 的公开签名里没有它们的字段，测试替身也只能经 `_testing_*`
# 私有 factory 构造。

class _OutlineStructureSnapshotProvider:
    """`osp-1`：由受信版式 + 真实大纲**重建**结构终态（§18.3.3）。"""

    version = V.OUTLINE_STRUCTURE_PROVIDER_VERSION


class _ReadonlyCurrentEvidenceGateway:
    """`egp-1`：按已登记数据库身份重开只读连接并取快照与全量成员。"""

    version = V.EVIDENCE_GATEWAY_PROVIDER_VERSION

    def load_snapshot_and_blocks(
            self, authority: VerifiedCurrentEvidenceAuthority, *, company_id: str,
            document_id: str, document_version: str) -> tuple:
        snapshot, blocks = authority.load_snapshot_and_blocks(
            company_id=company_id, document_id=document_id,
            document_version=document_version)
        ordered = tuple(sorted(
            blocks, key=lambda b: (b.page_number, b.block_index)))
        return snapshot, ordered


class _AlignmentTerminalProvider:
    """`atp-1`：从**受信同次对齐结果**取得全量 typed terminal（不收手造元组）。"""

    version = V.ALIGNMENT_TERMINAL_PROVIDER_VERSION


class _QualificationPolicyProvider:
    """`qpp-1`：固定资产 registry 解析版本化策略；不接受调用者注入记录。

    两个方向**刻意分开**，因为它们的正确性条件不同：

    - `current_for_new_build(stage)` 用于**新建**快照：它受 A/B 阶段门约束，因此
      TS4-A 只能新建 `distribution_only` 快照，TS4-B 只能新建 `threshold_enabled` 快照。
    - `for_verification(policy)` 用于**复核**已有快照：它只把快照里已载明的策略记录
      对回固定目录钉住的指纹，**不看**当前阶段。历史的 A 快照因此在未来的 B 环境里
      仍然可核验（否则升到 B 会把已冻结的历史产物全部作废），但它的
      `completion_enabled=False` 会让它永远得不到完成资格。
    """

    version = V.QUALIFICATION_POLICY_PROVIDER_VERSION

    def current_for_new_build(self, stage: str) -> SpanQualificationPolicy:
        table = SP.ab_gate_truth_table()
        if stage != table["stage"]:
            raise SpanBuildError(
                f"本轮只允许新建 {table['stage']!r} 阶段的快照；得到 stage={stage!r}"
                f"（不得静默跨阶段，fail-closed）")
        if table["stage"] == "distribution_only":
            policy = SP.resolve_distribution_policy()
        else:
            # TS4-B：frozen current policy —— 由 approval record 确定性派生，且必须与
            # 磁盘上的 frozen 记录、注册表钉住的指纹逐字段一致。
            policy = SP.resolve_frozen_policy()
        # 记录自身只对自身阶段负责；"当前究竟处于哪一阶段"在这里单点强制（§18.3.2(9)）。
        SP.assert_current_policy(policy)
        if policy.stage != stage:
            raise SpanBuildError("策略记录自身的 stage 与请求的阶段不一致")
        return policy

    def for_verification(self, policy: SpanQualificationPolicy
                         ) -> SpanQualificationPolicy:
        if not isinstance(policy, SpanQualificationPolicy):
            raise SpanBuildError("待复核的策略必须为 SpanQualificationPolicy")
        _assert_policy_self_consistent(policy)
        if policy.policy_key not in SP.policy_registry_summary()["policy_keys"]:
            raise SpanBuildError(
                f"策略键 {policy.policy_key!r} 未登记在固定资产 registry 中；"
                f"不得复核来路不明的策略记录（fail-closed）")
        # 指纹由固定目录的字节重算：只看记录自报的 policy_fingerprint 不足以证明它
        # 就是注册表钉住的那一份。
        SP.policy_provider_authority_fingerprint(policy)
        return policy


def _assert_policy_self_consistent(policy: SpanQualificationPolicy) -> None:
    """策略记录**自身**的阶段一致性（与当前 A/B 环境无关，故复核路径同样可用）。"""
    if policy.stage == "distribution_only":
        if policy.span_confidence_min is not None or policy.completion_enabled \
                or policy.set_complete_supported:
            raise SpanBuildError(
                "distribution_only 策略不得携带阈值或开启 completion / set_complete"
                "（fail-closed）")
    elif policy.stage == "threshold_enabled":
        if policy.span_confidence_min is None or not policy.completion_enabled \
                or not policy.set_complete_supported:
            raise SpanBuildError(
                "threshold_enabled 策略必须携带阈值并开启 completion / set_complete"
                "（fail-closed）")
    else:
        raise SpanBuildError(f"未登记的策略阶段 {policy.stage!r}（fail-closed）")


# ---------------------------------------------------------------------------
# 4. 共享重建核：由受信版式的**同一份字节**重建 outline 与结构终态
# ---------------------------------------------------------------------------

def _rebuild_outline_structure(layout_capability: VerifiedPageLayout
                               ) -> tuple[DocumentOutline, OutlineStructureSnapshot]:
    """由受信版式绑定的原始字节重建 `(outline, structure_snapshot)`。

    只接受**已签发**的 `VerifiedPageLayout`：字节来自签发时保留的那一份，因此
    "先验版式、再从磁盘按路径重读"的 TOCTOU 窗口不存在。
    """
    issued_capability(layout_capability, "VerifiedPageLayout")
    layout = layout_capability.layout
    if layout.schema_version != V.LAYOUT_SCHEMA_VERSION:
        raise SpanBuildError(
            f"真实版式 schema_version 必须为 {V.LAYOUT_SCHEMA_VERSION!r}，"
            f"得到 {layout.schema_version!r}")
    context = OB.load_source_context_from_bytes(
        layout_capability.source_bytes, layout, company_id=layout.company_id,
        document_id=layout.document_id, source_label="<受信版式绑定的 PDF 字节>")
    result = OB.build_document_outline(context)
    outline = result.outline
    if outline.schema_version != V.OUTLINE_SCHEMA_VERSION:
        raise SpanBuildError(
            f"重建出的大纲 schema_version 必须为 {V.OUTLINE_SCHEMA_VERSION!r}，"
            f"得到 {outline.schema_version!r}")
    outline.verify_upstream(layout=layout)
    diagnosis = OB.line_structure_diagnostic(result, layout)
    if not diagnosis["conserved"]:
        raise SpanBuildError(
            "逐行四态诊断自身不守恒（四态之和 != 非家具行数）；"
            "结构终态不得由不守恒的诊断派生（fail-closed）")
    line_states = _line_states_from_diagnostic(diagnosis, layout)
    snapshot = OutlineStructureSnapshot.create(
        outline_algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        heading_profile_version=V.HEADING_QUALIFICATION_PROFILE_VERSION,
        table_region_version=V.TABLE_REGION_QUALIFICATION_VERSION,
        toc_reconciliation_version=V.TOC_BODY_RECONCILIATION_VERSION,
        normalization_version=V.NORMALIZATION_VERSION,
        document_id=layout.document_id, document_version=layout.document_version,
        page_layout_id=layout.page_layout_id, outline_locator=outline.outline_locator,
        outline_id=outline.outline_id, line_states=line_states)
    return outline, snapshot


def _line_states_from_diagnostic(diagnosis: dict, layout: PageLayout) -> tuple:
    """诊断投影 → `LineStructureState`（**只**取四态、节点归属与正文附着方式）。

    诊断里"正文切到哪里"的推断一律不进快照：`body_attachment` 之外的启发式字段不得
    被 TS4 当成边界权威；正式正文边界由 TS4 自己按 §18.6 判定。
    """
    vocab = OB.LINE_STRUCTURE_STATES
    by_key = {(p.page_number, line.line_index): (p, line)
              for p in layout.pages for line in p.lines}
    out: list = []
    for record in diagnosis["lines"]:
        key = (record["page_number"], record["line_index"])
        entry = by_key.get(key)
        if entry is None:
            raise SpanBuildError(f"诊断行 {key} 不在真实版式中（fail-closed）")
        _page, line = entry
        if line.is_furniture:
            raise SpanBuildError(f"家具行 {key} 不得进入结构终态（fail-closed）")
        state = record["structure_state"]
        if state not in vocab:
            raise SpanBuildError(
                f"诊断行的 structure_state={state!r} 不属于 {vocab}（fail-closed）")
        attachment = record["body_attachment"]
        if state != "body_under_node" and attachment is not None:
            raise SpanBuildError(
                f"行 {key} 的 state={state!r} 不得携带 body_attachment（fail-closed）")
        if state == "body_under_node" and attachment not in BODY_ATTACHMENTS:
            raise SpanBuildError(
                f"行 {key} 的 body_attachment 必须属于 {BODY_ATTACHMENTS}，"
                f"得到 {attachment!r}")
        node_id = (record["node_id"] if state == "heading_node"
                   else (record["owning_node_id"] if state == "body_under_node"
                         else None))
        out.append(LineStructureState(
            page_number=key[0], line_index=key[1],
            line_identity=derive_line_identity(
                document_version=layout.document_version, page_number=key[0],
                line_index=key[1], text=line.text, bbox=line.bbox),
            state=state, node_id=node_id,
            body_attachment=(attachment if state == "body_under_node" else None),
            reason_code=str(record["line_assignment"])))
    out.sort(key=lambda ls: (ls.page_number, ls.line_index))
    return tuple(out)


# ---------------------------------------------------------------------------
# 5. 签发入口
# ---------------------------------------------------------------------------

def _structure_provider_authority_fingerprint(*, scope: str, source_kind: str,
                                              issuer_version: str, layout: PageLayout,
                                              outline: DocumentOutline) -> str:
    """§18.3.5 表内 `structure` provider authority 载荷（组合根重算）。"""
    return sha256_canonical([
        V.OUTLINE_STRUCTURE_PROVIDER_VERSION, scope, source_kind, issuer_version,
        layout.source_file_sha256,
        sha256_canonical(layout.to_dict()),
        sha256_canonical(outline.to_dict()),
        V.OUTLINE_ALGORITHM_VERSION,
        V.HEADING_QUALIFICATION_PROFILE_VERSION,
        V.TABLE_REGION_QUALIFICATION_VERSION,
        V.TOC_BODY_RECONCILIATION_VERSION,
    ])


def _evidence_gateway_authority_fingerprint(
        *, scope: str, source_kind: str, issuer_version: str,
        resolved_db_identity: dict, snapshot: EvidenceSetSnapshot,
        member_identity_sha256: str) -> str:
    """§18.3.5 表内 `evidence` provider authority 载荷（组合根重算）。"""
    return sha256_canonical([
        V.EVIDENCE_GATEWAY_PROVIDER_VERSION, scope, source_kind, issuer_version,
        dict(resolved_db_identity), readonly_primitive_identity(),
        current_set_query_contract_identity(), snapshot.fingerprint,
        member_identity_sha256,
    ])


def _terminal_provider_authority_fingerprint(
        *, scope: str, source_kind: str, issuer_version: str,
        alignment: VerifiedEvidenceSetAlignment) -> str:
    """§18.3.5 表内 `terminals` provider authority 载荷（组合根重算）。"""
    return sha256_canonical([
        V.ALIGNMENT_TERMINAL_PROVIDER_VERSION, scope, source_kind, issuer_version,
        alignment.input_fingerprint, alignment.terminal_set_sha256,
        V.ALIGNER_VERSION, V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        alignment.authority_fingerprint,
    ])


def _handoff_identity(*, scope: str, source_kind: str, issuer_version: str,
                      layout: PageLayout, outline: DocumentOutline,
                      snapshot: EvidenceSetSnapshot, alignment,
                      structure_fp: str, evidence_fp: str, terminal_fp: str,
                      policy_fp: str) -> str:
    """handoff 自身的确定身份（不引用自身，故无循环定义）。"""
    return sha256_canonical([
        scope, source_kind, issuer_version,
        layout.page_layout_id, sha256_canonical(layout.to_dict()),
        outline.outline_id, sha256_canonical(outline.to_dict()),
        snapshot.evidence_set_version, snapshot.fingerprint,
        alignment.authority_fingerprint,
        structure_fp, evidence_fp, terminal_fp, policy_fp,
    ])


def _issue_ts3_handoff(*, layout_capability, verified_alignment, evidence_authority,
                       scope: str, source_kind: str, issuer_version: str
                       ) -> VerifiedTS3Handoff:
    """三个 scope 共享的唯一签发核（只在这一层分流 gate，之后算法完全一致）。"""
    issued_capability(layout_capability, "VerifiedPageLayout", (scope,))
    issued_capability(verified_alignment, "VerifiedEvidenceSetAlignment", (scope,))
    issued_capability(evidence_authority, "VerifiedCurrentEvidenceAuthority", (scope,))
    layout = layout_capability.layout
    if verified_alignment.page_layout is not layout:
        raise SpanBuildError(
            "受信对齐结果绑定的版式与本交接的受信版式不是同一个对象；"
            "不允许把两次链路的结果拼在一起（fail-closed）")
    outline, structure_snapshot = _rebuild_outline_structure(layout_capability)
    snapshot, blocks = _ReadonlyCurrentEvidenceGateway().load_snapshot_and_blocks(
        evidence_authority, company_id=layout.company_id,
        document_id=layout.document_id, document_version=layout.document_version)
    if verified_alignment.evidence_snapshot.fingerprint != snapshot.fingerprint:
        raise SpanBuildError(
            "受信对齐结果绑定的 Evidence 快照与本交接重新取得的不一致；"
            "证据集在两份结果之间发生了变化（fail-closed）")
    terminals = _assert_upstream_closure(
        layout=layout, document_outline=outline,
        structure_snapshot=structure_snapshot, evidence_snapshot=snapshot,
        evidence_blocks=blocks, terminals=verified_alignment.terminals)
    policy = _QualificationPolicyProvider().current_for_new_build(
        SP.ab_gate_truth_table()["stage"])
    policy_fp = SP.policy_provider_authority_fingerprint(policy)
    structure_fp = _structure_provider_authority_fingerprint(
        scope=scope, source_kind=source_kind, issuer_version=issuer_version,
        layout=layout, outline=outline)
    member_sha = sha256_canonical([list(m) for m in snapshot.member_identities()])
    evidence_fp = _evidence_gateway_authority_fingerprint(
        scope=scope, source_kind=source_kind, issuer_version=issuer_version,
        resolved_db_identity=evidence_authority.resolved_db_identity,
        snapshot=snapshot, member_identity_sha256=member_sha)
    terminal_fp = _terminal_provider_authority_fingerprint(
        scope=scope, source_kind=source_kind, issuer_version=issuer_version,
        alignment=verified_alignment)
    hid = _handoff_identity(
        scope=scope, source_kind=source_kind, issuer_version=issuer_version,
        layout=layout, outline=outline, snapshot=snapshot,
        alignment=verified_alignment, structure_fp=structure_fp,
        evidence_fp=evidence_fp, terminal_fp=terminal_fp, policy_fp=policy_fp)
    obj = VerifiedTS3Handoff(
        layout_capability=layout_capability, document_outline=outline,
        structure_snapshot=structure_snapshot, evidence_authority=evidence_authority,
        evidence_snapshot=snapshot, evidence_blocks=blocks,
        alignment=verified_alignment, policy=policy, scope=scope,
        source_kind=source_kind, issuer_version=issuer_version,
        policy_provider_authority_fingerprint=policy_fp,
        structure_provider_authority_fingerprint=structure_fp,
        evidence_gateway_authority_fingerprint=evidence_fp,
        terminal_provider_authority_fingerprint=terminal_fp,
        handoff_identity=hid)
    _ = terminals
    return _issue_capability(obj, "VerifiedTS3Handoff", scope)


def issue_live_ts3_handoff(layout_capability: VerifiedPageLayout,
                           verified_alignment: VerifiedEvidenceSetAlignment,
                           evidence_authority: VerifiedCurrentEvidenceAuthority
                           ) -> VerifiedTS3Handoff:
    """**唯一**生产 TS3→TS4 交接签发入口（同次正式链路）。

    内部调用冻结的 TS3 builder 重建 `DocumentOutline` 与结构终态；**不接受** plain
    `DocumentOutline` / plain 结构快照 / 调用者给定的 provider。四步（版式 / Evidence
    权威 / 对齐 / 交接）中任何身份不一致一律 fail-closed。
    """
    return _issue_ts3_handoff(
        layout_capability=layout_capability, verified_alignment=verified_alignment,
        evidence_authority=evidence_authority, scope="live",
        source_kind="current_store",
        issuer_version=V.VERIFIED_TS3_HANDOFF_VERSION)


def _issue_pinned_ts3_handoff(*, raw_pdf, expected_layout: PageLayout,
                              company_id: str, document_id: str, root_lock: dict,
                              frozen_rows: tuple | None = None
                              ) -> VerifiedTS3Handoff:
    """**验收专用**（不导出）：`pinned_acceptance/historical_run` 交接。

    版式由**同一份冻结字节**重建并与冻结产物 canonical 全比较后才签发；Evidence
    权威只绑定根锁里锁定的库身份与文件 sha256。

    `frozen_rows`（§18.14.2-3）：真实文档的逐块对齐**终态**来自冻结
    `normalization_alignment.json` 的 `rows`，由 `from_dict` 还原并逐项重核，**禁止**
    重跑 `align_block` / `align_evidence_set`。缺省 `None` 时保持原来的当场对齐路径。
    """
    if not isinstance(root_lock, dict):
        raise SpanBuildError("root_lock 必须为对象")
    locked = root_lock.get("pdf_sha256")
    if not isinstance(locked, str) or len(locked) != 64:
        raise SpanBuildError("根锁必须提供 64 位 pdf_sha256")
    layout_capability = _issue_cross_checked_layout(
        raw_pdf, company_id=company_id, document_id=document_id,
        expected_layout=expected_layout, scope="pinned_acceptance",
        source_kind="historical_run",
        issuer_version=V.PINNED_PAGE_LAYOUT_ISSUER_VERSION,
        root_identity={"locked_pdf_sha256": locked,
                       "trust_root_file_sha256": root_lock.get("fixture_file_sha256")})
    if layout_capability.layout.source_file_sha256 != locked:
        raise SpanBuildError(
            "重建版式的 source_file_sha256 与根锁锁定的 pdf_sha256 不一致（fail-closed）")
    authority = _issue_pinned_evidence_authority(root_lock)
    if frozen_rows is None:
        alignment = _align_verified(
            layout_capability, authority, scope="pinned_acceptance",
            issuer_version=V.PINNED_ALIGNMENT_ISSUER_VERSION)
    else:
        alignment = _issue_pinned_alignment_from_frozen_rows(
            layout_capability, authority, rows=frozen_rows,
            issuer_version=V.PINNED_ALIGNMENT_ISSUER_VERSION)
    return _issue_ts3_handoff(
        layout_capability=layout_capability, verified_alignment=alignment,
        evidence_authority=authority, scope="pinned_acceptance",
        source_kind="historical_run",
        issuer_version=V.PINNED_TS3_HANDOFF_VERSION)


def _issue_fixture_ts3_handoff(*, raw_pdf, expected_layout: PageLayout,
                               company_id: str, document_id: str, fixture_root: dict
                               ) -> VerifiedTS3Handoff:
    """**验收专用**（不导出）：`pinned_acceptance/versioned_fixture` 交接（§18.14.2-9）。

    与 `_issue_pinned_ts3_handoff` 共用同一套私有核：版式由**同一份冻结字节**重建并
    canonical 全比较；Evidence 只由夹具 issuer 从版本化成员文件取；对齐由正式 aligner
    在同一核内跑出。**不写**任何 `data/*.db`，也**不**是 `testing` 域。
    """
    if not isinstance(fixture_root, dict):
        raise SpanBuildError("fixture_root 必须为对象")
    locked = fixture_root.get("source_pdf_sha256")
    if not isinstance(locked, str) or len(locked) != 64:
        raise SpanBuildError("夹具根必须提供 64 位 source_pdf_sha256")
    layout_capability = _issue_cross_checked_layout(
        raw_pdf, company_id=company_id, document_id=document_id,
        expected_layout=expected_layout, scope="pinned_acceptance",
        source_kind=FIXTURE_SOURCE_KIND,
        issuer_version=V.FIXTURE_PAGE_LAYOUT_ISSUER_VERSION,
        root_identity={"locked_pdf_sha256": locked,
                       "trust_root_file_sha256":
                           fixture_root.get("fixture_file_sha256")})
    if layout_capability.layout.source_file_sha256 != locked:
        raise SpanBuildError(
            "夹具重建版式的 source_file_sha256 与夹具根锁定的 source_pdf_sha256 "
            "不一致（fail-closed）")
    authority = _issue_fixture_evidence_authority(fixture_root)
    alignment = _align_verified(
        layout_capability, authority, scope="pinned_acceptance",
        issuer_version=V.FIXTURE_ALIGNMENT_ISSUER_VERSION)
    return _issue_ts3_handoff(
        layout_capability=layout_capability, verified_alignment=alignment,
        evidence_authority=authority, scope="pinned_acceptance",
        source_kind=FIXTURE_SOURCE_KIND,
        issuer_version=V.FIXTURE_TS3_HANDOFF_VERSION)


def _testing_issue_ts3_handoff(layout_capability, verified_alignment,
                               evidence_authority, *, document_outline=None,
                               structure_snapshot=None) -> VerifiedTS3Handoff:
    """**测试专用**（不导出）：`testing` 域的交接，可注入伪造结构以做反例。

    注入只改变本域内的结构事实，**不能**把 `testing` 冒充 `live` /
    `pinned_acceptance`：那三个 scope 的门在 `issued_capability` 里独立。
    """
    handoff = _issue_ts3_handoff(
        layout_capability=layout_capability, verified_alignment=verified_alignment,
        evidence_authority=evidence_authority, scope="testing",
        source_kind="unit_test",
        issuer_version=V.TESTING_TS3_HANDOFF_VERSION)
    if document_outline is None and structure_snapshot is None:
        return handoff
    forged = VerifiedTS3Handoff(
        layout_capability=handoff.layout_capability,
        document_outline=(handoff.document_outline if document_outline is None
                          else document_outline),
        structure_snapshot=(handoff.structure_snapshot if structure_snapshot is None
                            else structure_snapshot),
        evidence_authority=handoff.evidence_authority,
        evidence_snapshot=handoff.evidence_snapshot,
        evidence_blocks=handoff.evidence_blocks, alignment=handoff.alignment,
        policy=handoff.qualification_policy, scope="testing",
        source_kind="unit_test", issuer_version=V.TESTING_TS3_HANDOFF_VERSION,
        policy_provider_authority_fingerprint=(
            handoff.policy_provider_authority_fingerprint),
        structure_provider_authority_fingerprint=(
            handoff._structure_provider_authority_fingerprint),
        evidence_gateway_authority_fingerprint=(
            handoff._evidence_gateway_authority_fingerprint),
        terminal_provider_authority_fingerprint=(
            handoff._terminal_provider_authority_fingerprint),
        handoff_identity=handoff.handoff_identity)
    return _issue_capability(forged, "VerifiedTS3Handoff", "testing")


# ---------------------------------------------------------------------------
# 6. §18.3.2 的闭合要求（缺一项即 `SpanBuildError`）
# ---------------------------------------------------------------------------

def _assert_upstream_closure(*, layout: PageLayout, document_outline: DocumentOutline,
                             structure_snapshot: OutlineStructureSnapshot,
                             evidence_snapshot: EvidenceSetSnapshot,
                             evidence_blocks: tuple, terminals: tuple) -> tuple:
    """逐条执行 §18.3.2 的闭合要求，返回全量 terminal。"""
    # -1 结构快照与真实版式 / 大纲闭合：文档身份、节点存在、非家具行集合相等。
    if (structure_snapshot.document_id != layout.document_id
            or structure_snapshot.document_version != layout.document_version
            or structure_snapshot.page_layout_id != layout.page_layout_id):
        raise SpanBuildError("结构快照的文档身份与真实版式不一致（fail-closed）")
    if (structure_snapshot.outline_id != document_outline.outline_id
            or structure_snapshot.outline_locator != document_outline.outline_locator):
        raise SpanBuildError("结构快照绑定的 outline 身份与真实大纲不一致（fail-closed）")
    if structure_snapshot.outline_algorithm_version != document_outline.algorithm_version:
        raise SpanBuildError("结构快照的 outline_algorithm_version 与真实大纲不一致")
    real_lines = {(p.page_number, line.line_index)
                  for p in layout.pages for line in p.lines if not line.is_furniture}
    snapshot_lines = {(s.page_number, s.line_index)
                      for s in structure_snapshot.line_states}
    if snapshot_lines != real_lines:
        missing = sorted(real_lines - snapshot_lines)[:5]
        extra = sorted(snapshot_lines - real_lines)[:5]
        raise SpanBuildError(
            f"结构快照的非家具行集合与真实版式不闭合：缺 {missing} / 多 {extra}"
            f"（fail-closed）")
    node_ids = {n.node_id for n in document_outline.nodes}
    for state in structure_snapshot.line_states:
        if state.node_id is not None and state.node_id not in node_ids:
            raise SpanBuildError(
                f"结构快照引用了真实大纲中不存在的 node_id={state.node_id!r}"
                f"（fail-closed）")
    # -2 schema 版本
    if document_outline.schema_version != V.OUTLINE_SCHEMA_VERSION:
        raise SpanBuildError("真实大纲 schema_version 必须为 do-4（fail-closed）")
    if layout.schema_version != V.LAYOUT_SCHEMA_VERSION:
        raise SpanBuildError("真实版式 schema_version 必须为 pl-3（fail-closed）")
    # -3 公司 / 文档 / 版本三者一致
    if (document_outline.document_id != layout.document_id
            or document_outline.document_version != layout.document_version):
        raise SpanBuildError("真实大纲的文档身份与真实版式不一致（fail-closed）")
    if document_outline.page_layout_id != layout.page_layout_id:
        raise SpanBuildError("真实大纲绑定的 page_layout_id 与真实版式不一致")
    evidence_snapshot.assert_bound_to(layout)
    # -4 快照为 current，且成员逐位置精确
    evidence_snapshot.assert_current()
    assert_members_exact(tuple(_member_of(b) for b in evidence_blocks),
                         evidence_snapshot)
    # -5 每个块自证身份、绑定版式、版本一致
    for block in evidence_blocks:
        if not isinstance(block, EvidenceBlockInput):
            raise SpanBuildError(
                f"Evidence 成员必须为 EvidenceBlockInput，得到 "
                f"{type(block).__name__}（fail-closed）")
        block.assert_identity()
        block.assert_bound_to(layout)
        if block.evidence_set_version != evidence_snapshot.evidence_set_version:
            raise SpanBuildError(
                f"块 {block.evidence_block_id!r} 的 evidence_set_version 与权威快照"
                f"不一致（fail-closed）")
    # -6 全终态闭合：每个块恰好一个终态
    block_ids = sorted(b.evidence_block_id for b in evidence_blocks)
    terminal_ids = [t.evidence_block_id for t in terminals]
    if block_ids != sorted(terminal_ids):
        raise SpanBuildError(
            "终态集合与 Evidence 块集合不完全相等（缺失 / 多余一律拒绝，fail-closed）")
    if len(set(terminal_ids)) != len(terminal_ids):
        raise SpanBuildError("每个块必须恰好一个终态；出现重复（fail-closed）")
    # -7 / -8 逐终态重核：版本、真实长度、存储序
    text_by_id = {b.evidence_block_id: b.text for b in evidence_blocks}
    for terminal in terminals:
        if terminal.evidence_set_version != evidence_snapshot.evidence_set_version:
            raise SpanBuildError("终态 evidence_set_version 与权威快照不一致")
        if terminal.aligner_version != V.ALIGNER_VERSION:
            raise SpanBuildError("终态 aligner_version 必须为 al-3（fail-closed）")
        expected_schema = (V.ALIGN_SCHEMA_VERSION
                           if terminal.terminal_kind == "alignment"
                           else V.ALIGN_REFUSAL_SCHEMA_VERSION)
        if terminal.terminal_schema_version != expected_schema:
            raise SpanBuildError(
                f"终态 schema_version 必须为 {expected_schema!r}，得到 "
                f"{terminal.terminal_schema_version!r}（fail-closed）")
        text = text_by_id.get(terminal.evidence_block_id)
        if text is None:
            raise SpanBuildError("终态不对应任何 Evidence 块（fail-closed）")
        expected_len = len(tight(text))
        if terminal.block_char_length != expected_len:
            raise SpanBuildError(
                f"终态 {terminal.terminal_id!r} 自报 block_char_length="
                f"{terminal.block_char_length} 与按真实 Evidence 文本重算的 "
                f"{expected_len} 不一致；自报长度不得覆盖真实文本（fail-closed）")
        if _is_non_ascending_char_map(terminal):
            raise SpanBuildError(
                f"终态 {terminal.terminal_id!r} 的 char_map 存储序非升序；"
                f"TS4 正式输入边界拒绝乱序终态（fail-closed）")
    return tuple(sorted(terminals, key=lambda t: t.sort_key))


# ---------------------------------------------------------------------------
# 7. 构建入口（公开 builder 只收 live；验收 / 测试走各自 wrapper）
# ---------------------------------------------------------------------------

def build_span_snapshot(handoff: VerifiedTS3Handoff, *, stage: str
                        ) -> SpanBuildSnapshot:
    """**唯一**公开 TS4 builder：受信交接 + 阶段 → 不可变 span 快照。

    `stage` 是本次**新建**快照的阶段声明；它必须与
    `versions.SPAN_CONFIDENCE_MIN` 派生的 A/B 真值表一致。本函数只接受 `live`。
    """
    return _build_core(handoff, stage=stage, allowed_scopes=("live",))


def _build_from_pinned_handoff(handoff: VerifiedTS3Handoff, *, stage: str
                               ) -> SpanBuildSnapshot:
    """**验收专用**（不导出）：`pinned_acceptance` 域的构建。"""
    return _build_core(handoff, stage=stage, allowed_scopes=("pinned_acceptance",))


def _testing_build_span_snapshot(handoff: VerifiedTS3Handoff, *, stage: str
                                 ) -> SpanBuildSnapshot:
    """**测试专用**（不导出）：`testing` 域的构建。"""
    return _build_core(handoff, stage=stage, allowed_scopes=("testing",))


def _build_core(handoff: VerifiedTS3Handoff, *, stage: str,
                allowed_scopes: Sequence[str]) -> SpanBuildSnapshot:
    """三个 scope 共用的私有构建核（**唯一**算法实现）。"""
    issued_capability(handoff, "VerifiedTS3Handoff", tuple(allowed_scopes))
    return _build_snapshot(_build_span_input(handoff, stage=stage))


def _build_span_input(handoff: VerifiedTS3Handoff, *, stage: str | None = None,
                      policy: SpanQualificationPolicy | None = None,
                      expected_policy_authority_fingerprint: str | None = None
                      ) -> SpanBuildInput:
    """把受信交接解析成内部 capability；失败抛 `SpanBuildError`（§18.3.3）。

    `stage` 与 `policy` 恰给一个：**新建**走 `stage`（受 A/B 阶段门约束），
    **复核**走 `policy`（取快照自己载明的策略，只对固定目录钉住的指纹，不受当前
    阶段约束）。二者都必须与交接绑定的策略是同一版本。
    """
    if (stage is None) == (policy is None):
        raise SpanBuildError("必须恰给出 stage（新建）或 policy（复核）之一")
    issued_capability(handoff, "VerifiedTS3Handoff")
    layout_capability = handoff.layout_capability
    layout = layout_capability.layout
    # 结构终态必须能由受信版式的同一份字节**独立重建**并 canonical 全等。
    outline, structure_snapshot = _rebuild_outline_structure(layout_capability)
    _require_canonical_equal("DocumentOutline", outline.to_dict(),
                             handoff.document_outline.to_dict())
    _require_canonical_equal("OutlineStructureSnapshot", structure_snapshot.to_dict(),
                             handoff.structure_snapshot.to_dict())
    snapshot, blocks = _ReadonlyCurrentEvidenceGateway().load_snapshot_and_blocks(
        handoff.evidence_authority, company_id=layout.company_id,
        document_id=layout.document_id, document_version=layout.document_version)
    _require_canonical_equal("EvidenceSetSnapshot", snapshot.to_dict(),
                             handoff.evidence_snapshot.to_dict())
    if tuple(b.evidence_block_id for b in blocks) != tuple(
            b.evidence_block_id for b in handoff.evidence_blocks):
        raise SpanBuildError("重新取得的 Evidence 成员集合与交接绑定的不一致")
    terminals = _assert_upstream_closure(
        layout=layout, document_outline=handoff.document_outline,
        structure_snapshot=handoff.structure_snapshot,
        evidence_snapshot=handoff.evidence_snapshot, evidence_blocks=blocks,
        terminals=handoff.alignment.terminals)
    _assert_handoff_authority_recomputes(handoff, layout=layout, snapshot=snapshot)
    provider = _QualificationPolicyProvider()
    if policy is None:
        # 新建：策略由**当前**阶段解析，且必须与交接绑定的是同一版本。
        policy = provider.current_for_new_build(stage)
        if policy.policy_id != handoff.qualification_policy.policy_id:
            raise SpanBuildError(
                "组合根解析出的策略与交接绑定的策略不是同一版本（fail-closed）")
        expected_policy_fp = handoff.policy_provider_authority_fingerprint
    else:
        # 复核：策略取自被复核的快照，只对固定目录钉住的指纹——因此历史的 A 快照在
        # 未来的 B 环境里仍可核验，而无需（也无法）让交接改绑到历史策略。
        policy = provider.for_verification(policy)
        expected_policy_fp = expected_policy_authority_fingerprint
        if expected_policy_fp is None:
            raise SpanBuildError("复核路径必须给出快照自载的策略 authority 指纹")
    authority_fp = SP.policy_provider_authority_fingerprint(policy)
    if authority_fp != expected_policy_fp:
        raise SpanBuildError(
            "策略 provider authority 指纹与期望值不一致；策略记录或注册表已被"
            "改动（fail-closed）")
    records: list = []
    refusals: list = []
    for block_alignment in handoff.alignment.alignment.blocks:
        if block_alignment.record is not None:
            if not isinstance(block_alignment.record, TextAlignmentRecord):
                raise SpanBuildError("alignment 终态类型不符（fail-closed）")
            records.append(block_alignment.record)
        if block_alignment.refusal_record is not None:
            if not isinstance(block_alignment.refusal_record, AlignmentRefusalRecord):
                raise SpanBuildError("refusal 终态类型不符（fail-closed）")
            refusals.append(block_alignment.refusal_record)
    if len(records) + len(refusals) != len(terminals):
        raise SpanBuildError("终态必须恰为 record 或 refusal 之一（fail-closed）")
    return SpanBuildInput._make(
        token=_FACTORY_TOKEN, page_layout=layout,
        document_outline=handoff.document_outline,
        structure_snapshot=handoff.structure_snapshot,
        evidence_snapshot=handoff.evidence_snapshot, evidence_blocks=blocks,
        alignment_records=tuple(records), alignment_refusals=tuple(refusals),
        alignment_terminals=terminals, qualification_policy=policy,
        policy_authority_fingerprint=authority_fp, handoff=handoff)


def _assert_handoff_authority_recomputes(handoff: VerifiedTS3Handoff, *,
                                         layout: PageLayout,
                                         snapshot: EvidenceSetSnapshot) -> None:
    """§18.3.5 载荷里的三个 provider authority 指纹必须能被**独立重算**（fail-closed）。

    这三个指纹此前只被"抄进" `TrustedBuildInput`，没有与重算值比对。后果是：私改交接
    对象的私有字段（`object.__setattr__(handoff, "_structure_provider_authority_"
    "fingerprint", …)`）会让载荷与交接**自洽**，于是构建能过、而复核又用同一份被改过
    的交接去重建，也能过——载荷里就留下了一个谁都没验过的来源指纹。

    三者都只由交接自己绑定的对象派生（版式 / 大纲 / 证据快照 / 对齐结果 + 签发域三元
    组），与"当前是 A 还是 B 阶段"无关，因此在新建与复核两条路径上都可重算。交接自身
    身份（`handoff_identity`）**不**在此重算：它含策略指纹，而复核路径的策略取自被复核
    的快照（§18.10 要求历史 A 快照在 B 环境里仍可核验），对它会误伤历史产物。

    同时把交接登记的签发域与它自报的 `issuer_scope` 对齐：登记表里的域在签发后不可改，
    因此这一步能抓住"把 pinned 交接的域字段改成 live"这类改写。
    """
    issued_capability(handoff, "VerifiedTS3Handoff", (handoff.issuer_scope,))
    scope, source_kind, issuer_version = (
        handoff.issuer_scope, handoff.source_kind, handoff.issuer_version)
    member_sha = sha256_canonical([list(m) for m in snapshot.member_identities()])
    pairs = (
        ("structure", _structure_provider_authority_fingerprint(
            scope=scope, source_kind=source_kind, issuer_version=issuer_version,
            layout=layout, outline=handoff.document_outline),
         handoff._structure_provider_authority_fingerprint),
        ("evidence", _evidence_gateway_authority_fingerprint(
            scope=scope, source_kind=source_kind, issuer_version=issuer_version,
            resolved_db_identity=handoff.evidence_authority.resolved_db_identity,
            snapshot=snapshot, member_identity_sha256=member_sha),
         handoff._evidence_gateway_authority_fingerprint),
        ("terminals", _terminal_provider_authority_fingerprint(
            scope=scope, source_kind=source_kind, issuer_version=issuer_version,
            alignment=handoff.alignment),
         handoff._terminal_provider_authority_fingerprint),
    )
    for name, recomputed, recorded in pairs:
        if not isinstance(recorded, str) or recomputed != recorded:
            raise SpanBuildError(
                f"交接载荷的 `{name}` provider authority 指纹不可独立重算："
                f"重算值 {recomputed!r} != 交接记录 {recorded!r}（fail-closed）")


def _require_canonical_equal(what: str, mine: Any, theirs: Any) -> None:
    if canonical_json(mine) != canonical_json(theirs):
        raise SpanBuildError(
            f"{what} 与交接绑定的那一个 canonical 不相等；"
            f"仅身份字符串相同不足以通过（fail-closed）")


# ---------------------------------------------------------------------------
# 8. 逐行事实与正文范围切分（§18.6）
# ---------------------------------------------------------------------------

_KIND_HEADING = "heading_node"
_KIND_FORMAL = "formal_unassigned"
_KIND_NON_CONTENT = "non_content"
_KIND_TABLE_INSIDE = "table_inside"
_KIND_TABLE_ADJACENT = "table_adjacency"
_KIND_UNASSIGNED = "unassigned"
_KIND_EMPTY = "empty"
_KIND_REGULAR = "regular"

#: 正文行的五个类别（**只有**这些行属于正文域 `B_body`）。
_BODY_KINDS: tuple = (_KIND_REGULAR, _KIND_TABLE_INSIDE, _KIND_TABLE_ADJACENT,
                      _KIND_UNASSIGNED, _KIND_EMPTY)

#: run 起点之前**允许**出现的行类别 → 左边界成因（穷尽；表外一律 fail-closed）。
_LEFT_CAUSE_BY_PREDECESSOR: dict = {
    _KIND_HEADING: "preceding_heading",
    _KIND_TABLE_INSIDE: "resume_after_table_inside",
    _KIND_TABLE_ADJACENT: "resume_after_table_adjacency",
    _KIND_EMPTY: "resume_after_empty",
    _KIND_NON_CONTENT: "resume_after_non_content",
}

#: run 终点之后**允许**出现的行类别 → 右边界成因（穷尽）。
_RIGHT_CAUSE_BY_SUCCESSOR: dict = {
    _KIND_HEADING: "next_heading",
    _KIND_TABLE_INSIDE: "table_inside",
    _KIND_TABLE_ADJACENT: "table_adjacency",
    _KIND_EMPTY: "empty",
    _KIND_FORMAL: "formal_unassigned",
    _KIND_NON_CONTENT: "non_content",
}

#: `body_attachment` → `unassigned_reason`（只允许 `schema.UNASSIGNED_REASONS` 内既有值）。
_UNASSIGNED_REASON_BY_ATTACHMENT: dict = {
    "before_first_heading": "no_heading_context",
    "after_formal_unassigned_boundary": "boundary_ambiguous",
}

#: `range_kind` → §18.9.2 的正文层桶名。
_BODY_BUCKET_BY_KIND: dict = {
    "regular": "regular",
    "table_adjacency": "table_adjacency_provisional",
    "table_inside": "table_inside",
    "unassigned": "unassigned",
    "empty": "empty",
}

#: 结构四态 → §18.9.1 的文档层桶名。
_DOC_BUCKET_BY_STATE: dict = {
    "heading_node": "heading",
    "formal_unassigned": "formal_unassigned",
    "non_content": "non_content",
    "body_under_node": "body",
}

#: 正文行类别 → §18.8.1 的组件落点。
_LANDING_BY_KIND: dict = {
    _KIND_TABLE_INSIDE: "table_inside",
    _KIND_TABLE_ADJACENT: "table_adjacency",
    _KIND_UNASSIGNED: "body_unassigned",
    _KIND_EMPTY: "body_empty",
    _KIND_HEADING: "heading_node",
    _KIND_FORMAL: "formal_unassigned",
    _KIND_NON_CONTENT: "non_content",
}


class _LineFact:
    """一次构建中逐行的**受信事实**（只由结构终态、真实版式与表格范围派生）。"""

    __slots__ = ("key", "page", "line", "state", "kind", "node_id",
                 "table_scope", "table_reason")

    def __init__(self, *, key, page, line, state, kind, node_id, table_scope,
                 table_reason) -> None:
        self.key = key
        self.page = page
        self.line = line
        self.state = state
        self.kind = kind
        self.node_id = node_id
        self.table_scope = table_scope
        self.table_reason = table_reason


class _WalkFailure(Exception):
    """单段投影的**软失败**（转成 `offset_unverifiable` / `boundary_inexact`）。"""


def _collect_line_facts(inp: SpanBuildInput) -> tuple:
    """把结构终态、真实版式与 `table_region_scopes` 合并成逐行事实序列。

    表格范围由本模块调用冻结的 `OB.table_region_scopes(page_layout)` **重算**，不采信
    任何调用方传入的 scope 表。

    **未决契约歧义（本批不自行裁决，已上报）**：本函数先判
    `body_attachment != "preceding_heading"` → `unassigned`，**再**判表格范围；而
    §18.6.1-1 的**有序**项目列表把"表格范围"排在附着方式之前（`inside_table` →
    `T_inside_table` 的条目本身不带附着条件），§18.7.1 对 `inside_table` 的表述同样
    不带前置条件，§18.6.1-2 则把 `body_attachment == "preceding_heading"` 写成 **run**
    的成立条件。计划没有为这两个维度规定优先级，两种读法在真实文档上会给出不同的分桶
    （KCZ 实测约 1141 行落在这两个读法的差集里）。在此歧义被裁决前，本函数保持不变，
    不按任何一种读法"顺手改对"。
    """
    layout = inp.page_layout
    scopes = OB.table_region_scopes(layout)
    by_key = {(p.page_number, line.line_index): (p, line)
              for p in layout.pages for line in p.lines}
    facts: list = []
    for state in inp.structure_snapshot.line_states:
        key = (state.page_number, state.line_index)
        entry = by_key.get(key)
        if entry is None:
            raise SpanBuildError(f"结构终态行 {key} 不在真实版式中（fail-closed）")
        page, line = entry
        table_scope = table_reason = None
        if state.state == "body_under_node":
            if state.body_attachment != "preceding_heading":
                kind = _KIND_UNASSIGNED
            else:
                scope_entry = scopes.get(key)
                if scope_entry is not None and scope_entry[0] == "inside_table":
                    kind = _KIND_TABLE_INSIDE
                    table_scope, table_reason = scope_entry
                elif scope_entry is not None and scope_entry[0] == "adjacent_to_table":
                    kind = _KIND_TABLE_ADJACENT
                    table_scope, table_reason = scope_entry
                elif tight(line.text) == "":
                    kind = _KIND_EMPTY
                else:
                    kind = _KIND_REGULAR
        else:
            kind = state.state
        facts.append(_LineFact(
            key=key, page=page, line=line, state=state, kind=kind,
            node_id=state.node_id, table_scope=table_scope,
            table_reason=table_reason))
    facts.sort(key=lambda f: f.key)
    return _apply_table_closure(tuple(facts))


# ---------------------------------------------------------------------------
# 8.1 表格 provisional 邻接闭包（§18.7 表格范围的**前导**延伸；TS5 才最终裁决）
# ---------------------------------------------------------------------------
#
# 真实电子 PDF 把一张表排版成 `表题行 → 单位行 → 表头 / 表体行`：表体行是判据 A 的
# `inside_table`，单位行落在 `TABLE_REGION_PROXIMITY_PT`(8pt) 内成为
# `adjacent_to_table`，而**表题行离表体常常远于 8pt** —— 于是它既不是成员也不是邻近，
# 只看单行状态就会被判成普通正文，进而进简介、进材料库（TS4-A 真实产物里
# `NDSD_KCZ_2026` 有 29 行这种表题形态的普通 `body` 行，其中 21 行进了简介）。
#
# 本节给出一个**有限、可复核**的 provisional 闭包：从**已经由几何证明的**表格区域
# （`inside_table` / `adjacent_to_table`）出发，沿**真实源序**向上吸收紧邻的表格前导
# 行。它的输入只有真实 `PageLayout` 的 `bbox`、冻结 `trg-3` 的区域状态、真实源序、
# 节点归属与结构边界；**没有任何文字判据** —— "以'表'字开头"这类形态信息在本节的
# 任何分支里都不参与判定，形态至多是事后诊断统计。
#
# 吸收是"**改桶**"而不是删除或改写：原文 / 页码 / 行号 / 几何 / 所属节点全部保留，
# 行只从 `regular` 变成 `table_adjacency` + `table_scope="none"`（+ 冻结的
# `table_row_adjacent` 原因码）。因此
#   * 不产生 `OutlineSpan`、不进简介、不计入可引用覆盖（它不再是正文材料）；
#   * 守恒桶改记为 `table_adjacency_provisional`（行数与 tight 字符数守恒不破）；
#   * TS5 的 `TableRangeDecision` 用同一批定位读回全文与精确位置，做
#     `absorbed_as_caption` / `kept_as_paragraph` / `unresolved_geometry` 的**最终**裁决。
# 本轮**不**把任何表题预判成 `table_caption` span：闭包产物一律是 provisional 处置。
#
# 边界（逐条都是显式停止条件；宁可留下 bounded provisional，也不吞掉整段正文）：
#   * 只向上、**连续**：一旦某条候选不合格就立即停止，绝不越过它继续上溯；
#   * 单个已证明区域的上溯上限 `_TABLE_LEADING_CAP` 行；
#   * 候选必须仍是 `regular`（标题 / 空白 / `unassigned` / `formal_unassigned` /
#     其它表格范围一律切断闭包）；
#   * 候选与区域必须**同节点**且该区域**有明确节点归属**（跨节点 / 无归属不吸收）；
#   * 候选贴正文栏左边界（≤ 最左正文行 + 容差）⇒ 满栏正文，切断；
#   * 候选字号大于正文基准字号 ⇒ 标题 / 大字号前导，切断；
#   * 候选取向**满栏 / 近满栏**（宽度 ≥ 声明栏宽上界的 `_TABLE_LEADING_PROSE_FRAC`）
#     ⇒ 正文，切断。**这一条取代了旧的"与该行上一行同左边界 ⇒ 续行"机械规则**：
#     真实年报的"表题 / 单位行 / 表注"几乎总排在同一条缩进边界上，机械按左边界切断会
#     把整段前留成正式正文（TS4-A 真实产物里三处"单位行"就是这样漏走的）。**同左边界
#     不是停止条件**；宽度才是正文的**负证明**。
#   * **折行续行**：候选的**上一条**（源序相邻、非家具行）是**同节点、同左边界**、
#     且**自身对该区域没有任何锚定证据**的 `regular` 行 ⇒ 候选只是它的续行，切断。
#     这一条与上一条正交：宽度负责挡住**满栏**正文，这一条挡住**窄的**正文折行残段
#     —— 折行残段宽度很窄，窄表格区域的水平中心又极易与它对齐，单靠"居中"这一条
#     版式特征不足以证明它是表格前导。判据仍然是纯几何 + 源序 + 已证明的区域归属：
#     "上一行自己有没有资格被当成前导"用的就是本闭包吸收候选的同一判据本身，
#     因此**同左边界仍然不是停止条件**（真实表题 / 单位行与上一行同边界时，上一行
#     自身也满足该判据 —— 例如同为前导行、或就是同一条连续前导区域的上一行）。
#   * 候选要么是**退化碎片**（宽度装不下 `_TABLE_LEADING_MIN_EM` 个字宽 —— 装不下正文
#     文字，也不可能是真材料），要么至少满足一条**几何**标记（加粗 / 与表格水平中心
#     对齐 / 落在表格右半且窄排 / 整行落在**区域右侧**的窄排行）。满栏正文行这四条
#     一条都满足不了 —— 这就是"普通正文不会被吞"的负证明本身，而不是靠文字形态过滤
#     或事后删片段。

#: 单个已证明表格区域向上吸收前导行的上限。
_TABLE_LEADING_CAP = 3
#: 前导行水平中心与表格水平中心的对齐容差（pt）。
_TABLE_LEADING_CENTER_TOL_PT = 3.0
#: 候选贴正文栏左边界（满栏正文）的容差（pt）。
_TABLE_LEADING_BODY_LEFT_TOL_PT = 3.0
#: 候选字号大于正文基准字号的容差（pt）。
_TABLE_LEADING_SIZE_TOL_PT = 0.5
#: 窄排上限：相对**表格宽度**（`inside_table` 成员的水平跨度）。
_TABLE_LEADING_NARROW_FRAC = 0.35
#: 窄排下限：至少这么多个字宽（排除纯页码 / 装饰性短线）。
_TABLE_LEADING_MIN_EM = 3.0
#: "候选已是满栏 / 近满栏正文"的宽度比例：相对**声明栏宽上界**
#: （`prose_width`，全部非家具行的最大水平跨度）。这一条是纯几何的**负证明**：
#: 正文栏宽本身由真实 `bbox` 给出，不含任何文字判据。
_TABLE_LEADING_PROSE_FRAC = 0.7
#: 单个已证明表格区域**向下**暂存"表后相邻行"的上限（与前导上限各自独立计数）。
_TABLE_TRAILING_CAP = 3
#: 尾部候选相对**本页本栏局部行距**的倍数上限：候选与区域尾行（或上一候选）的垂直
#: 间距至多这么多个局部行距。`2.0` 的语义是"至多隔一个空行的间距"。
#:
#: 这个因子**不是**按样本反推的常数：`1.5 / 2.0 / 3.0` 在四份冻结版式（三份真实年报
#: + 一份夹具）上给出**完全相同**的走查结果 —— 真正的绑定条件是物理邻接的其余分量
#: （同页 / 同栏交叠 / 源序直接相邻），间距阈值从来不是分界线。选 `2.0` 是因为它的
#: 语义可以直接说出来（"至多一个空行"），而不是因为它"刚好通过"。
#:
#: 局部行距本身由真实 `bbox` 与类别快照确定性派生（见
#: `_table_closure_local_pitch`），与页码 / 公司 / 表号 / 坐标常量无关；本常量与那条
#: 派生规则一起构成 TS4 正文算法身份的一部分（该算法由
#: `TS4_BODY_SPAN_BUILDER_VERSION` 具名，当前为 `sb-8`）。
_TABLE_TRAILING_PITCH_FACTOR = 2.0


# ---------------------------------------------------------------------------
# 8.1.1 两个方向共用的基础原语
# ---------------------------------------------------------------------------
#
# 前导（向上）与尾部（向下）**共用**同一份"改桶前的类别快照"与同一批已证明区域，
# 也共用 `_TABLE_LEADING_BODY_LEFT_TOL_PT` / `_TABLE_LEADING_PROSE_FRAC` 这两个几何
# 分量。区别在**判据结构**：前导行必须靠几何标记自证身份，然后用"贴左边界 **或**
# 满栏"这个**析取负证明**切断正文；尾部候选不许要求任何标记（真实表注与表后正文在
# 字号 / 左边界 / 行距上可以完全一致），改用**物理邻接**判据（同页 / 同栏交叠 /
# 源序直接相邻 / 局部垂直间距）与 `_table_closure_band` / `_table_closure_overlap`
# / `_table_closure_vertical_gap` / `_table_closure_local_pitch` 这一组原语，
# 见 §8.1.2。
# 共用基准是刻意的：一套向上规则和一套向下规则若各自重算基准，同一份文档会出现两套
# 互不一致的"什么算正文"，而"表题 / 单位行 / 表后表注"本就是同一族版式现象。

def _table_closure_baseline(order: list, kinds: dict):
    """两个方向共用的**版式基准**：`(doc_left, prose_width, body_size)`。

    `kinds` 必须是**改桶之前**的类别快照：闭包只改 `kind`，若用实时类别重算基准，
    先跑的闭包就会改变后跑闭包看到的正文样本，基准随应用顺序漂移。

    无正文行时返回 `None`（此时两个方向都无从判定，闭包整体不生效）。
    """
    regular = [fact for fact in order if kinds[fact.key] == _KIND_REGULAR]
    if not regular:
        return None
    doc_left = min(float(fact.line.bbox[0]) for fact in regular)
    prose_width = max(float(fact.line.bbox[2]) - float(fact.line.bbox[0])
                      for fact in order)
    sizes: dict = {}
    for fact in regular:
        size = round(OB._line_size(fact.line), 1)
        sizes[size] = sizes.get(size, 0) + 1
    body_size = sorted(sizes.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return doc_left, prose_width, body_size


def _table_closure_regions(order: list, kinds: dict) -> tuple:
    """**已证明的**表格区域：极大连续表格类别段，且至少含一行 `inside_table`、
    区域有明确的节点归属。

    判据全部来自冻结 `trg-3` 的版式判定（`table_region_scopes`）与源序，不看文字。
    """
    runs: list = []
    current: list = []
    for fact in order:
        if kinds[fact.key] in (_KIND_TABLE_INSIDE, _KIND_TABLE_ADJACENT):
            current.append(fact)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return tuple(run for run in runs
                 if run[0].node_id is not None
                 and any(kinds[fact.key] == _KIND_TABLE_INSIDE for fact in run))


def _table_closure_flush_left(fact: _LineFact, *, doc_left: float) -> bool:
    """候选**贴正文栏左边界**（满栏正文的必要条件之一）。"""
    return float(fact.line.bbox[0]) <= doc_left + _TABLE_LEADING_BODY_LEFT_TOL_PT


def _table_closure_wide(fact: _LineFact, *, prose_width: float) -> bool:
    """候选**宽达声明栏宽上界的 `_TABLE_LEADING_PROSE_FRAC`**。"""
    width = float(fact.line.bbox[2]) - float(fact.line.bbox[0])
    return width >= _TABLE_LEADING_PROSE_FRAC * prose_width


def _table_closure_band(region: list, kinds: dict) -> tuple:
    """区域中**已证明成员**（`inside_table`）的水平跨度 `(x0, x2)`。

    用 `inside_table` 而不是整个表格类别段：`adjacent_to_table` / 区域内的 provisional
    行只是"邻近"，不能扩张表格的水平证据范围。区域一定含至少一条 `inside_table`
    （`_table_closure_regions` 的前置条件）。
    """
    inner = [fact for fact in region if kinds[fact.key] == _KIND_TABLE_INSIDE]
    return (min(float(fact.line.bbox[0]) for fact in inner),
            max(float(fact.line.bbox[2]) for fact in inner))


def _table_closure_overlap(fact: _LineFact, *, band: tuple) -> float:
    """候选与区域水平跨度的**交叠宽度**（≤ 0 即"不在同一正文栏"）。

    正文栏身份不用任何栏检测器：它就是"这一行的水平范围与已证明表格区域**相交**"。
    分栏排版的另一栏与窄表格的水平范围不相交，因此不会被建立邻接。**不看文字。**
    """
    return (min(float(fact.line.bbox[2]), band[1])
            - max(float(fact.line.bbox[0]), band[0]))


def _table_closure_vertical_gap(previous: _LineFact, fact: _LineFact) -> float:
    """两行盒沿**阅读方向**的垂直间距（垂直重叠记 `0`）。

    取两个方向的**盒间距的较大者**，因此与 y 轴朝向无关（真实 `PageLayout` 与合成版式
    夹具的 y 朝向相反，两者都必须得到同一个几何量）：行盒不重叠时它就是两行之间的
    真实空白高度，重叠时是 `0`。**不看文字。**
    """
    return max(0.0,
               float(fact.line.bbox[1]) - float(previous.line.bbox[3]),
               float(previous.line.bbox[1]) - float(fact.line.bbox[3]))


def _table_closure_local_pitch(order: list, index: dict, kinds: dict, *,
                               page: int, band: tuple, tail: _LineFact) -> float:
    """**本页本栏的局部行距**：同页相邻行起点间距的中位数。

    样本优先级（逐级确定性，全部只读 `bbox` / 页码 / 改桶前类别快照）：

    1. 同页、与 `band` 水平交叠的**改桶前正文行**（`regular`）—— "本页本栏的正文行距"；
    2. 同页全部非家具行（正文样本不足时）；
    3. 区域尾行的行高（同页只剩表格行时）。

    只要判据只有版式量，**没有**任何固定页码 / 公司 / 表号 / 坐标常量，也没有按样本
    反推的数值。返回值为正；样本完全退化时返回 `0.0`，此时只有垂直重叠的行能通过
    间距检查（确定性、fail-closed，不放大范围）。
    """
    def _median(rows: list):
        rows = sorted(rows, key=lambda fact: index[fact.key])
        deltas = sorted(
            abs(float(second.line.bbox[1]) - float(first.line.bbox[1]))
            for first, second in zip(rows, rows[1:])
            if int(second.state.page_number) == int(first.state.page_number))
        deltas = [delta for delta in deltas if delta > 0.0]
        return deltas[len(deltas) // 2] if deltas else None

    same_page = [fact for fact in order
                 if int(fact.state.page_number) == page]
    body = [fact for fact in same_page
            if kinds[fact.key] == _KIND_REGULAR
            and _table_closure_overlap(fact, band=band) > 0.0]
    pitch = _median(body)
    if pitch is None:
        pitch = _median(same_page)
    if pitch is not None:
        return pitch
    return abs(float(tail.line.bbox[3]) - float(tail.line.bbox[1]))


def _table_closure_frame(facts: tuple):
    """两个方向共用的**一次性帧**：源序、改桶前类别快照、已证明区域、版式基准。

    帧在闭包开始时算**一次**，之后所有判定都读它 —— 闭包的应用顺序因此不影响任何
    基准量。无正文行时返回 `None`。
    """
    order = [fact for fact in facts if not fact.line.is_furniture]
    if not order:
        return None
    kinds = {fact.key: fact.kind for fact in order}
    baseline = _table_closure_baseline(order, kinds)
    if baseline is None:
        return None
    index = {fact.key: position for position, fact in enumerate(order)}
    return ((order, index, kinds, _table_closure_regions(order, kinds))
            + tuple(baseline))


def _table_closure_rebucket(facts: tuple, claimed: dict) -> tuple:
    """**改桶**：把认领到的行改成既有的 provisional 表格邻近签名。

    就地更新 `_LineFact` 的 `kind` / `table_scope` / `table_reason`，其余字段
    （含 `line` 对象本身）一个字节都不动 —— 原文 / 页码 / 行号 / 几何 / 节点 /
    结构状态全部保留，TS5 据此做最终裁决。
    """
    if not claimed:
        return facts
    for fact in facts:
        if fact.key in claimed:
            fact.kind = _KIND_TABLE_ADJACENT
            fact.table_scope = OB.TABLE_SCOPE_NONE
            fact.table_reason = OB.TABLE_REGION_ADJACENT_REASON
    return facts


def _table_leading_fragment(fact: _LineFact, *, size: float) -> bool:
    """**退化碎片**：宽度装不下 `_TABLE_LEADING_MIN_EM` 个字宽。

    它既装不下正文文字，也不可能是真材料（页码 / 装饰短线 / 被截断的边角），因此
    不构成"这一行是正文"的证据 —— 但它**只**在已经通过类别、节点、正文栏左边界与
    字号守卫之后才生效（见 `_apply_table_leading_closure` 的条件顺序）。
    """
    width = float(fact.line.bbox[2]) - float(fact.line.bbox[0])
    return width < _TABLE_LEADING_MIN_EM * size


def _table_leading_marks(fact: _LineFact, *, center_x: float, in_x0: float,
                         in_x2: float, size: float, prose_width: float) -> bool:
    """表格前导的**几何**标记（四条任一即可；**不看任何文字**）。

    * 加粗；
    * 水平中心与表格区域中心对齐（表题常见）；
    * 落在表格区域**右半**且相对区域宽度窄排；
    * 整行落在表格区域**右侧**且相对**栏宽**窄排（窄表右侧的单位行 / 表注形态：
      区域的水平证据只覆盖左列，行的左边界因此落在 `in_x2` 之外）。

    第 3、4 条的窄排下限 `width >= _TABLE_LEADING_MIN_EM * size` 让"装不下字宽的
    碎片"不能靠窄排取得标记 —— 它们由 `_table_leading_fragment` 单独处理。
    """
    x0 = float(fact.line.bbox[0])
    x2 = float(fact.line.bbox[2])
    width = x2 - x0
    if any(span.is_bold for span in fact.line.spans):
        return True
    if abs((x0 + x2) / 2.0 - center_x) <= _TABLE_LEADING_CENTER_TOL_PT:
        return True
    narrow = width >= _TABLE_LEADING_MIN_EM * size
    if narrow and x0 >= center_x \
            and width <= _TABLE_LEADING_NARROW_FRAC * (in_x2 - in_x0):
        return True
    return (narrow and x0 >= in_x2 and prose_width > 0.0
            and width <= _TABLE_LEADING_NARROW_FRAC * prose_width)


def _table_leading_anchored(fact: _LineFact, *, center_x: float, in_x0: float,
                            in_x2: float, prose_width: float) -> bool:
    """该行是否**锚定**到表格区域：退化碎片，或至少一条几何标记。

    这正是"这一行有资格被当成表格前导"的判据本身（见
    `_apply_table_leading_closure` 的走查条件）。**不看任何文字。**
    """
    size = OB._line_size(fact.line)
    return (_table_leading_fragment(fact, size=size)
            or _table_leading_marks(fact, center_x=center_x, in_x0=in_x0,
                                    in_x2=in_x2, size=size,
                                    prose_width=prose_width))


def _absorb_table_leading(frame) -> dict:
    """向上走查：认领紧邻区域**上方**的表格前导行（条件顺序见 §8.1 文件头）。

    只读数入帧里的改桶前类别快照与几何量，**不看任何文字**。
    """
    order, index, kinds, regions, doc_left, prose_width, body_size = frame
    absorbed: dict = {}
    for region in regions:
        node_id = region[0].node_id
        inner = [fact for fact in region if kinds[fact.key] == _KIND_TABLE_INSIDE]
        in_x0 = min(float(fact.line.bbox[0]) for fact in inner)
        in_x2 = max(float(fact.line.bbox[2]) for fact in inner)
        center_x = (in_x0 + in_x2) / 2.0
        step = index[region[0].key] - 1
        taken = 0
        while step >= 0 and taken < _TABLE_LEADING_CAP:
            fact = order[step]
            if kinds[fact.key] != _KIND_REGULAR or fact.node_id != node_id:
                break
            if _table_closure_flush_left(fact, doc_left=doc_left):
                break
            size = OB._line_size(fact.line)
            if size > body_size + _TABLE_LEADING_SIZE_TOL_PT:
                break
            if _table_closure_wide(fact, prose_width=prose_width):
                break
            # 折行续行：**上一条**（源序相邻、非家具行）是与本行同节点、同左边界、
            # 且自身对该区域**没有任何锚定证据**的正文行 —— 那么本行只是它的续行，
            # 闭包必须停在这里。**同左边界本身不是停止条件**（真实表题 / 单位行几乎
            # 总与上一行同边界）；停止只由"上一行是普通正文"决定。
            previous = order[step - 1] if step >= 1 else None
            if previous is not None and previous.kind == _KIND_REGULAR \
                    and previous.node_id == node_id \
                    and abs(float(previous.line.bbox[0])
                            - float(fact.line.bbox[0])) \
                    <= _TABLE_LEADING_BODY_LEFT_TOL_PT \
                    and not _table_leading_anchored(
                        previous, center_x=center_x, in_x0=in_x0, in_x2=in_x2,
                        prose_width=prose_width):
                break
            if not _table_leading_anchored(fact, center_x=center_x, in_x0=in_x0,
                                           in_x2=in_x2, prose_width=prose_width):
                break
            absorbed[fact.key] = fact
            taken += 1
            step -= 1
    return absorbed


# ---------------------------------------------------------------------------
# 8.1.2 表格 provisional 邻接闭包（§18.7 表格范围的**尾部**延伸；TS5 才最终裁决）
# ---------------------------------------------------------------------------
#
# 前导闭包只向上吸收表题 / 单位行；真实电子 PDF 的表格**下方**还常常跟着"表后表注"，
# 它与表格有直接源序关系，却既不是 `inside_table` 成员也不在 8pt 邻近内 —— 于是它被
# 判成普通正文，进 span、进简介、进材料库。
#
# 本轮**不判断"这一定是表注"**：TS4 交付的是一个**有限、被结构边界终止**的 provisional
# 段，由 TS5 的不可变 `TableRangeDecision` 在 `absorbed_as_note` / `kept_as_paragraph`
# / `absorbed_into_body` / `unresolved_geometry` 之间裁决。TS4 若在这里把它永久定性成
# 表注，就等于替 TS5 做了决定，而 TS5 的入口只接受 `table_inside` /
# `table_adjacency` 两种 disposition —— 这正是本轮必须由 TS4 完成的原因。
#
# 因此**进入 provisional 的判据只有物理邻接**，与这一行的内容是不是表注**无关**：
# 与已证明表格区域存在可靠物理邻接关系的有限正文范围先进入 `table_adjacency`，
# 普通正文因此被暂时暂存**不是内容丢失** —— 原文与精确定位完整保留、范围严格有界、
# TS5 能确定性恢复为正文，且它不生成 span、不进 `NavigationSynopsis`、不可引用、
# 不参与 completion。反过来，只要"避免暂存普通正文"被当成理由，真实表注就会被
# 永久留在 TS4 正式正文层 —— 这是本轮要消除的失效模式。
#
# 走查（每个已证明区域独立进行，从区域**尾行**沿真实源序向下）。起始资格（§四.1，
# 全部满足才可能进入 provisional）：
#   * 区域含至少一条真实 `inside_table`（区域定义即如此，且水平跨度只由
#     `inside_table` 成员算出 —— `table_adjacency` 成员不扩张表格的水平证据范围）；
#   * 与区域**同节点**；
#   * 与区域尾行**同一物理页**；
#   * 与区域水平跨度**存在交叠**（`_table_closure_overlap > 0`：这就是"同一正文栏"，
#     分栏排版的另一栏与窄表格不相交）；
#   * 与上一行在**真实源序**上**直接相邻**（中间夹着家具行 —— 页码 / 页眉页脚 ——
#     即不成立）；
#   * 与区域尾行的垂直间距不超过 `_TABLE_TRAILING_PITCH_FACTOR` × 本页本栏**局部
#     行距**（`_table_closure_local_pitch`，纯版式量确定性派生）。
# 满足物理邻接时，**第一条候选即进入 provisional —— 不得因为它是满栏文本而排除**。
#
# 后续连续行（§四.2）：同页、同节点、同栏（同上交叠判据）、源序连续、与上一候选的
# 垂直间距仍在局部阈值内、中间没有 heading / 家具 / `unassigned` / 新的表格类别段 /
# 其它结构边界，且总数不超过固定、版本化的小上限 `_TABLE_TRAILING_CAP`。
# **跨页内容一律停止**：本轮没有已批准的 typed continuation 关系，不做猜测。
#
# 停止语义（§四.3）：**任何**停止（结构边界、跨页、栏不交叠、间距超阈值、达到上限）
# 都**只**把已经通过物理邻接检查的 staged prefix 保留为 provisional。不合格的当前行
# 不进入 provisional，也不越过它继续下探。这里**没有**"命中内容形态即丢弃整个候选段"
# 的语义：那种语义会让一条满栏表注（例如"说明：…"）连同它前面已经成立的候选一起
# 退回正式正文 —— 正是本轮要消除的失效模式。满栏与不是满栏在起始资格与续行判据上
# 完全同权。
#
# 与前导不同，尾部候选**不要求**任何几何标记：真实表注与表后正文段落在字号 / 左边界 /
# 行距上与正文可以完全一致（实测四组真实表注的宽度与正文栏宽只差 2.9pt），任何"必须
# 带标记"的判据都会把真实表注漏回正式正文。因此尾部的判据是**物理邻接**，而不是
# 前导那种"几何标记 + 正文负证明"的组合。
#
# 所有谓词都是几何 / 源序 / 类别量：**没有任何文字判据** —— 不看"注：/ 资料来源 /
# 说明 / 备注"，不用 `startswith()`，不读 `line.text`，不匹配公司名 / 页码 / 表号。
# 文字形态至多是测试与离线验收里的**诊断**标签。

def _absorb_table_trailing(frame, facts: tuple, claimed: dict) -> dict:
    """向下走查：认领与已证明区域**物理邻接**的有限正文范围（判据见 §8.1.2 文件头）。

    `claimed` 是前导闭包已经认领的行：它们已经是 provisional，尾部闭包必须在此封口。

    任何停止都**保留**已经暂存的 prefix（§四.3）：返回值里不含被拒绝的行，但也
    不会因为拒绝而清空此前成立的部分。**不看任何文字。**
    """
    order, index, kinds, regions = frame[:4]
    raw = {fact.key: position for position, fact in enumerate(facts)}
    staged: dict = {}
    for region in regions:
        inner = [fact for fact in region
                 if kinds[fact.key] == _KIND_TABLE_INSIDE]
        if not inner:
            continue                              # 无已证明成员：不建立邻接
        band = _table_closure_band(region, kinds)
        tail = region[-1]
        node_id = tail.node_id
        page = int(tail.state.page_number)
        limit = _TABLE_TRAILING_PITCH_FACTOR * _table_closure_local_pitch(
            order, index, kinds, page=page, band=band, tail=tail)
        previous = tail
        step = index[tail.key] + 1
        taken = 0
        while step < len(order) and taken < _TABLE_TRAILING_CAP:
            fact = order[step]
            if kinds[fact.key] != _KIND_REGULAR or fact.key in claimed \
                    or fact.node_id != node_id:
                break                             # 非正文行 / 已认领 / 跨节点
            if int(fact.state.page_number) != page:
                break                             # 跨页：无 typed continuation，停止
            if raw[fact.key] - raw[order[step - 1].key] != 1:
                break                             # 中间夹着家具行
            if _table_closure_overlap(fact, band=band) <= 0.0:
                break                             # 不在同一正文栏
            if _table_closure_vertical_gap(previous, fact) > limit:
                break                             # 超过本页本栏局部行距阈值
            staged[fact.key] = fact
            previous = fact
            taken += 1
            step += 1
    return staged


def _apply_table_closure(facts: tuple) -> tuple:
    """生产入口：前导（向上）+ 尾部（向下）两个方向的 provisional 邻接闭包。

    纯改桶：就地更新 `_LineFact` 的 `kind` / `table_scope` / `table_reason`，其余字段
    （含 `line` 对象本身）一个字节都不动。区域、源序、基准与停止条件全部由真实
    `PageLayout` 几何与冻结 `trg-3` 状态派生，逐条可复核。
    """
    frame = _table_closure_frame(facts)
    if frame is None:
        return facts
    claimed = _absorb_table_leading(frame)
    claimed.update(_absorb_table_trailing(frame, facts, claimed))
    return _table_closure_rebucket(facts, claimed)


def _apply_table_leading_closure(facts: tuple) -> tuple:
    """**只**做前导闭包：单独核验与前导回归口径（生产链路用 `_apply_table_closure`）。"""
    frame = _table_closure_frame(facts)
    if frame is None:
        return facts
    return _table_closure_rebucket(facts, _absorb_table_leading(frame))


def _apply_table_trailing_closure(facts: tuple) -> tuple:
    """**只**做尾部闭包：单独核验口径。

    这一条要证明的是"尾部暂存是独立于前导的机制"——它不依赖前导闭包的产物（未认领
    集合为空），而后自己从区域尾行向下走查。
    """
    frame = _table_closure_frame(facts)
    if frame is None:
        return facts
    return _table_closure_rebucket(
        facts, _absorb_table_trailing(frame, facts, {}))


def _run_merge_key(fact: _LineFact) -> tuple:
    """run 的合并键（源序连续之外，键变化即切断，§18.6.1）。"""
    if fact.kind == _KIND_REGULAR:
        return (fact.kind, fact.node_id)
    if fact.kind in (_KIND_TABLE_INSIDE, _KIND_TABLE_ADJACENT):
        return (fact.kind, fact.table_scope, fact.table_reason)
    if fact.kind == _KIND_UNASSIGNED:
        return (fact.kind, fact.state.body_attachment)
    return (fact.kind, fact.node_id)


def _split_runs(facts: tuple) -> tuple:
    """按 §18.6.1 切分出极大正文范围（家具行不参与，故跨页 run 允许）。"""
    runs: list = []
    for fact in facts:
        if runs and _run_merge_key(runs[-1][-1]) == _run_merge_key(fact):
            runs[-1].append(fact)
        else:
            runs.append([fact])
    return tuple(tuple(r) for r in runs)


def _left_boundary_cause(facts: tuple, index: int) -> str:
    if index == 0:
        raise SpanBuildError(
            "正文范围之前没有任何非家具行：不存在合法的左边界成因（fail-closed）")
    cause = _LEFT_CAUSE_BY_PREDECESSOR.get(facts[index - 1].kind)
    if cause is None:
        raise SpanBuildError(
            f"正文范围的前驱行类别 {facts[index - 1].kind!r} 不在左边界成因真值表内；"
            f"表外原因一律拒绝（fail-closed）")
    return cause


def _right_boundary_cause(facts: tuple, index: int) -> str:
    if index == len(facts) - 1:
        return "document_end"
    cause = _RIGHT_CAUSE_BY_SUCCESSOR.get(facts[index + 1].kind)
    if cause is None:
        raise SpanBuildError(
            f"正文范围的后继行类别 {facts[index + 1].kind!r} 不在右边界成因真值表内；"
            f"表外原因一律拒绝（fail-closed）")
    return cause


# ---------------------------------------------------------------------------
# 9. §18.5.2 走查与 §18.5.3 `L_l → S` 投影
# ---------------------------------------------------------------------------

def _walk_segment(line_by_key: dict, *, page_number: int, line_index: int,
                  span_index: int, char_offset: int, char_start: int,
                  char_end: int, evidence_tight: str) -> tuple:
    """§18.5.2 的四步走查；任一不成立抛 `_WalkFailure`。

    走查**只依赖真实版式与 Evidence 文本**，与 run 无关：因此"某段落在标题行上"这类
    情形同样被完整核验，不会因为不在任何 run 里就跳过。

    返回 `(LayoutHit, line_start, line_stop)`，其中 `line_start/line_stop` 是
    `LayoutLine.text` 内的偏移（域 `L_l`）。
    """
    entry = line_by_key.get((page_number, line_index))
    if entry is None:
        raise _WalkFailure("段落在不属于真实版式的行上")
    page, line = entry
    if not (0 <= span_index < len(line.spans)):
        raise _WalkFailure("layout_span_index 越界")
    layout_span = line.spans[span_index]
    raw = layout_span.text
    if not (0 <= char_offset < len(raw)) or raw[char_offset].isspace():
        raise _WalkFailure("step1 命中失败：char_offset 越界或落在空白上")
    width = char_end - char_start
    if width <= 0:
        raise _WalkFailure("char_map 段长度必须为正")
    seen = 0
    position = char_offset
    while position < len(raw) and seen < width:
        if not raw[position].isspace():
            seen += 1
        position += 1
    if seen != width:
        raise _WalkFailure("step2 走读失败：本 span 内的非空白字符不足")
    span_stop = position
    walked = raw[char_offset:span_stop]
    if tight(walked) != evidence_tight[char_start:char_end]:
        raise _WalkFailure("step3 等值失败：走读片段与 Evidence tight 片段不等")
    line_start = layout_span.char_start + char_offset
    line_stop = layout_span.char_start + span_stop
    if not (0 <= line_start < line_stop <= len(line.text)) \
            or line.text[line_start:line_stop] != walked:
        raise _WalkFailure("step4 行内定位失败：真实切片与走读片段不等")
    hit = LayoutHit(
        page_number=page.page_number, line_index=line.line_index,
        layout_span_index=span_index,
        span_char_range=(char_offset, span_stop),
        line_char_range=(line_start, line_stop),
        bbox=tuple(float(x) for x in line.bbox))
    return hit, line_start, line_stop


class _RunProjector:
    """一个正文 run 的 `L_l → S` 投影器（§18.5.3）。"""

    __slots__ = ("normalized_text", "offsets", "canonical", "raw")

    def __init__(self, run: tuple) -> None:
        self.raw: dict = {}
        self.canonical: dict = {}
        self.offsets: dict = {}
        offset = 0
        for fact in run:
            text = canonical_text(fact.line.text)
            self.raw[fact.key] = fact.line.text
            self.canonical[fact.key] = text
            self.offsets[fact.key] = offset
            offset += len(text) + 1
        self.normalized_text = " ".join(
            self.canonical[f.key] for f in run)
        if offset - 1 != len(self.normalized_text):
            raise SpanBuildError(
                f"span 规范化文本长度与逐行重构不一致：{offset - 1} != "
                f"{len(self.normalized_text)}（fail-closed）")

    def to_span_local(self, key: tuple, line_start: int, line_stop: int) -> tuple:
        """`L_l → S`（§18.5.3-3/-4）；端点跨出本 run 即 `_WalkFailure`。"""
        text = self.canonical.get(key)
        offset = self.offsets.get(key)
        raw_line = self.raw.get(key)
        if text is None or offset is None or raw_line is None:
            raise _WalkFailure("行不在本 run 内")
        if not (0 <= line_start < line_stop <= len(raw_line)):
            raise _WalkFailure("行内区间越界")
        # §18.5.3-3/-4：两端都必须走**同一个**闭式
        # `Lline_to_S(line_raw, o) = len(canonical_text(line_raw[:o]))`。
        # 端点绝不能写成 `len(prefix) + len(segment)`：`canonical_text` 会去掉
        # 首尾空白，于是"落在空白之后的段"会凭空少掉一个连接空格，使区间整体
        # 左移一位并**丢掉最后一个真实字符**。
        s_start = offset + len(canonical_text(raw_line[:line_start]))
        s_stop = offset + len(canonical_text(raw_line[:line_stop]))
        # 行内区间被规范化后可能整体塌成空串（例如只由空白组成）：此时**没有**可
        # 引用的 span 本地区间，属于 `boundary_inexact`，不得退化成零长度区间。
        if s_start == s_stop:
            raise _WalkFailure("行内区间规范化后为空，无法给出正长度的 span 本地区间")
        if not (offset <= s_start < s_stop <= offset + len(text)):
            raise _WalkFailure("行内区间跨出本 run 的本地域")
        # §18.5.3-4「真实切片不等」：`S` 与 `L_l` 的差异**只**允许是空白折叠，
        # 因此比较的是 tight 形态。用逐字面量比较会让"落在空白之后的段"被整体
        # 判成 `boundary_inexact`，而 §18.5.3-3 明确禁止加"前一字符必须为空白"
        # 这类伪条件。
        if tight(text[s_start - offset:s_stop - offset]) \
                != tight(raw_line[line_start:line_stop]):
            raise _WalkFailure("真实切片不等：`S` 区间与行内走读片段的非空白内容不一致")
        return s_start, s_stop


def _normalization_only_intervals(normalized_text: str) -> tuple:
    """所有由空白折叠 / 行连接产生的空格位置（§18.5.3-2，域 `S`）。"""
    out: list = []
    start = None
    for i, ch in enumerate(normalized_text):
        if ch == " ":
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(normalized_text)))
    return tuple(out)


# ---------------------------------------------------------------------------
# 10. 纯构建核
# ---------------------------------------------------------------------------

class _SegmentProjection:
    """一个 char_map 段的走查结果（全局一次；run 只决定 `S` 投影与归属）。"""

    __slots__ = ("terminal", "segment", "hit", "line_start", "line_stop",
                 "failure", "s_range")

    def __init__(self, terminal, segment, hit, line_start, line_stop, failure):
        self.terminal = terminal
        self.segment = segment
        self.hit = hit
        self.line_start = line_start
        self.line_stop = line_stop
        self.failure = failure
        self.s_range = None


class _BlockTally:
    """单条 Evidence 的守恒分账（§18.9.3）。"""

    __slots__ = ("mapped", "unverifiable", "residue", "landing_chars",
                 "landing_segments")

    def __init__(self) -> None:
        self.mapped: list = []
        self.unverifiable: list = []
        self.residue: list = []
        self.landing_chars: dict = {}
        self.landing_segments: dict = {}

    def landing(self, name: str, chars: int) -> None:
        self.landing_chars[name] = self.landing_chars.get(name, 0) + chars
        self.landing_segments[name] = self.landing_segments.get(name, 0) + 1


def _build_snapshot(inp: SpanBuildInput) -> SpanBuildSnapshot:
    """**唯一**纯构建核：由内部 capability 产出不可变快照。"""
    layout = inp.page_layout
    outline = inp.document_outline
    policy = inp.qualification_policy
    # 阶段一致性只看策略记录**自身**：新建路径已在入口处被 A/B 门挡住，而复核路径
    # 需要让历史的 A 快照在未来的 B 环境里仍然可核验。
    _assert_policy_self_consistent(policy)
    text_by_id = {b.evidence_block_id: b.text for b in inp.evidence_blocks}
    line_by_key = {(p.page_number, line.line_index): (p, line)
                   for p in layout.pages for line in p.lines}
    unassigned_span_by_line: dict = {}
    for span in outline.unassigned:
        for ref in span.layout_line_refs:
            unassigned_span_by_line[(ref[0], ref[1])] = span.span_id

    facts = _collect_line_facts(inp)
    runs = _split_runs(facts)
    fact_index = {f.key: i for i, f in enumerate(facts)}
    run_of_key: dict = {}
    for run in runs:
        for fact in run:
            run_of_key[fact.key] = run

    # -- ① 全局走查：每个 char_map 段只走读一次（与 run 无关）-------------
    projections: list = []
    for terminal in inp.alignment_terminals:
        evidence_tight = tight(text_by_id[terminal.evidence_block_id])
        for segment in terminal.char_map:
            bs, be = int(segment[0]), int(segment[1])
            try:
                hit, line_start, line_stop = _walk_segment(
                    line_by_key, page_number=int(segment[2]),
                    line_index=int(segment[3]), span_index=int(segment[4]),
                    char_offset=int(segment[5]), char_start=bs, char_end=be,
                    evidence_tight=evidence_tight)
                projections.append(_SegmentProjection(
                    terminal, segment, hit, line_start, line_stop, None))
            except _WalkFailure as failure:
                projections.append(_SegmentProjection(
                    terminal, segment, None, None, None, str(failure)))

    # -- ② 逐 regular run 的 `L_l → S` 投影 ------------------------------
    run_projectors: dict = {}
    for run in runs:
        if run[0].kind != _KIND_REGULAR:
            continue
        projector = _RunProjector(run)
        run_projectors[id(run)] = projector
        keys = {f.key for f in run}
        for projection in projections:
            if projection.hit is None:
                continue
            key = (int(projection.segment[2]), int(projection.segment[3]))
            if key not in keys:
                continue
            try:
                projection.s_range = projector.to_span_local(
                    key, projection.line_start, projection.line_stop)
            except _WalkFailure:
                projection.s_range = None

    # -- ③ span 定型（`span_id` 依赖 component refs，故先定型引用）--------
    spans: list = []
    dispositions: list = []
    span_by_run: dict = {}
    for run in runs:
        if run[0].kind == _KIND_REGULAR:
            span_obj = _build_run_span(inp, run, projections, facts, fact_index,
                                       policy)
            spans.append(span_obj)
            span_by_run[id(run)] = span_obj
            dispositions.append(_build_regular_disposition(
                run, span_obj, facts, fact_index))
        elif run[0].kind in _BODY_KINDS:
            # 跨页 `table_inside` 在这里按页展开成多条单页处置记录（W1）。
            dispositions.extend(_build_non_regular_disposition(run))
    dispositions.sort(key=lambda d: (d.start_page, d.start_line))
    spans.sort(key=lambda s: (s.start_anchor[0], s.start_anchor[1]))
    disposition_id_by_run_page = _disposition_id_by_run_page(runs, dispositions)

    # -- ④ 组件：全局一遍，落点由"段所在行的类别"与走查结果共同决定 -------
    components: list = []
    tally_by_block = {b.evidence_block_id: _BlockTally()
                      for b in inp.evidence_blocks}
    for projection in projections:
        tally = tally_by_block[projection.terminal.evidence_block_id]
        components.append(_build_component(
            inp=inp, projection=projection, run_of_key=run_of_key,
            span_by_run=span_by_run,
            disposition_id_by_run_page=disposition_id_by_run_page,
            unassigned_span_by_line=unassigned_span_by_line, tally=tally))
    for terminal in inp.alignment_terminals:
        tally = tally_by_block[terminal.evidence_block_id]
        for residue in terminal.residue:
            start, end = int(residue[0]), int(residue[1])
            residue_class = residue[2]
            tally.residue.append((start, end, residue_class))
            tally.landing("alignment_residue_unmapped", end - start)
            components.append(_build_residue_component(
                terminal, start, end, residue_class))
    components.sort(key=_component_sort_key)

    # -- TS3 unassigned span 只做精确引用，不复制进新材料集合 ---------------
    inherited = tuple(sorted(s.span_id for s in outline.unassigned))
    clash = {s.span_id for s in spans} & set(inherited)
    if clash:
        raise SpanBuildError(
            f"TS4 产生的 span_id 与 TS3 unassigned span_id 相撞：{sorted(clash)}；"
            f"立即停止，不得静默合并或覆盖（版本冲突，fail-closed）")

    coverages = tuple(sorted(
        (_build_coverage(span_obj, components) for span_obj in spans),
        key=lambda c: c.span_id))
    conservation = _build_conservation(inp, facts, dispositions, spans, components,
                                       tally_by_block, text_by_id)
    # `build_navigation_synopses` 要求节点集合严格升序且唯一：`outline.nodes` 的顺序是
    # 树遍历顺序（可重复、非字典序），因此这里显式按 `node_id` 排序后再交给它。
    node_ids = sorted({n.node_id for n in outline.nodes})
    if len(node_ids) != len(outline.nodes):
        raise SpanBuildError(
            f"真实大纲的 node_id 不唯一：{len(outline.nodes)} 个节点只有 "
            f"{len(node_ids)} 个不同 node_id（fail-closed）")
    synopses = SY.build_navigation_synopses(
        node_ids=node_ids, spans=spans, coverages=coverages,
        dispositions=dispositions, policy=policy)

    return SpanBuildSnapshot.create(
        qualification_policy=policy, document_id=layout.document_id,
        document_version=layout.document_version,
        page_layout_id=layout.page_layout_id, outline_id=outline.outline_id,
        alignment_schema_version=V.ALIGN_SCHEMA_VERSION,
        alignment_id=_alignment_id_of(inp),
        structure_snapshot_id=inp.structure_snapshot.structure_snapshot_id,
        trusted_input=_compose_trusted_input(inp), dispositions=tuple(dispositions),
        spans=tuple(spans), inherited_unassigned_span_ids=inherited,
        components=tuple(components), coverages=coverages,
        conservation=conservation, synopses=synopses,
        terminal_count=len(inp.alignment_terminals))


def _component_sort_key(component: SpanEvidenceComponent) -> tuple:
    return (component.evidence_block_id, component.evidence_char_range[0],
            component.evidence_char_range[1], component.landing)


def _disposition_id_by_position(dispositions: list, key: tuple) -> str:
    for disposition in dispositions:
        if (disposition.start_page, disposition.start_line) == key:
            return disposition.disposition_id
    raise SpanBuildError(  # pragma: no cover - 上面刚按同一批 run 构造过
        f"找不到起点为 {key} 的正文范围处置记录（fail-closed）")


def _alignment_id_of(inp: SpanBuildInput) -> str:
    """对齐终态集合的**不可变修订身份**（由全部 record / refusal id 定型排序重算）。"""
    if not inp.alignment_records and not inp.alignment_refusals:
        raise SpanBuildError("没有对齐终态，无法确定 alignment_id（fail-closed）")
    ids = sorted([r.alignment_id for r in inp.alignment_records]
                 + [r.refusal_id for r in inp.alignment_refusals])
    return identity("als", {"terminal_ids": ids,
                            "schema_version": V.ALIGN_SCHEMA_VERSION})


# ---------------------------------------------------------------------------
# 11. span 定型
# ---------------------------------------------------------------------------

def _build_run_span(inp: SpanBuildInput, run: tuple, projections: list,
                    facts: tuple, fact_index: dict, policy
                    ) -> OutlineSpan:
    """把一个 regular run 定型为正式 `OutlineSpan`。

    `span_id` 依赖 `component_evidence_refs`，而 refs 又依赖投影结果，因此这里分两步：
    先投影并择出准入集合，再定型 span。**不**先造 span 再"回填"引用。
    """
    first, last = run[0], run[-1]
    node_id = first.node_id
    if node_id is None:
        raise SpanBuildError("regular run 必须有 node_id（fail-closed）")
    projector = _RunProjector(run)
    normalized_text = projector.normalized_text
    if normalized_text == "":
        raise SpanBuildError("regular run 的规范化文本不得为空（fail-closed）")
    keys = {f.key for f in run}
    left = _left_boundary_cause(facts, fact_index[first.key])
    right = _right_boundary_cause(facts, fact_index[last.key])
    confidence = quantize(min(policy.factor_for("left", left),
                              policy.factor_for("right", right)))
    admitted: list = []
    for projection in projections:
        if projection.hit is None or projection.s_range is None:
            continue
        key = (int(projection.segment[2]), int(projection.segment[3]))
        if key not in keys:
            continue
        if projection.terminal.verdict != "aligned":
            continue
        admitted.append(projection)
    admitted.sort(key=lambda p: (p.terminal.evidence_block_id,
                                 int(p.segment[0]), int(p.segment[1])))
    refs: list = []
    alignment_ids: list = []
    for projection in admitted:
        ref = (projection.terminal.evidence_block_id, int(projection.segment[0]),
               int(projection.segment[1]))
        if ref in refs:
            raise SpanBuildError(f"component_evidence_refs 出现重复：{ref}")
        refs.append(ref)
        if projection.terminal.terminal_id not in alignment_ids:
            alignment_ids.append(projection.terminal.terminal_id)
    anchor_start = (first.page.page_number, first.line.line_index,
                    tuple(float(x) for x in first.line.bbox))
    anchor_end = (last.page.page_number, last.line.line_index,
                  tuple(float(x) for x in last.line.bbox))
    locator_value = derive_span_locator(
        document_outline_locator=inp.document_outline.outline_locator,
        evidence_set_version=inp.evidence_snapshot.evidence_set_version,
        start_anchor=anchor_start, end_anchor=anchor_end,
        span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION)
    line_refs = tuple(f.key for f in run)
    span_id_value = derive_span_id(
        span_locator=locator_value, schema_version=V.SPAN_SCHEMA_VERSION,
        document_id=inp.page_layout.document_id,
        document_version=inp.page_layout.document_version, node_id=node_id,
        role="body", unassigned_reason=None,
        page_range=(anchor_start[0], anchor_end[0]),
        char_range=(0, len(normalized_text)), layout_line_refs=line_refs,
        component_evidence_refs=tuple(refs), alignment_ids=tuple(alignment_ids),
        is_fallback=False, fallback_derivation=None, is_cross_heading=False,
        confidence=confidence, normalized_text=normalized_text)
    return OutlineSpan(
        span_locator=locator_value, span_id=span_id_value,
        document_outline_locator=inp.document_outline.outline_locator,
        node_id=node_id, document_id=inp.page_layout.document_id,
        document_version=inp.page_layout.document_version,
        evidence_set_version=inp.evidence_snapshot.evidence_set_version,
        role="body", schema_version=V.SPAN_SCHEMA_VERSION,
        span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION,
        unassigned_reason=None, start_anchor=anchor_start, end_anchor=anchor_end,
        page_range=(anchor_start[0], anchor_end[0]),
        char_range=(0, len(normalized_text)), layout_line_refs=line_refs,
        component_evidence_refs=tuple(refs), alignment_ids=tuple(alignment_ids),
        is_fallback=False, fallback_derivation=None, is_cross_heading=False,
        confidence=confidence, normalized_text=normalized_text,
        content_fingerprint=derive_span_content_fingerprint(normalized_text))


def _build_regular_disposition(run: tuple, span_obj: OutlineSpan, facts: tuple,
                               fact_index: dict) -> BodyRangeDisposition:
    first, last = run[0], run[-1]
    return BodyRangeDisposition.create(
        range_kind="regular", node_id=span_obj.node_id, unassigned_reason=None,
        table_scope=None, table_reason=None, start_page=first.key[0],
        start_line=first.key[1], end_page=last.key[0], end_line=last.key[1],
        line_count=len(run),
        tight_char_count=sum(tight_char_count_of(f.line.text) for f in run),
        left_boundary_cause=_left_boundary_cause(facts, fact_index[first.key]),
        right_boundary_cause=_right_boundary_cause(facts, fact_index[last.key]),
        confidence=span_obj.confidence, span_id=span_obj.span_id)


def _page_segments(run: tuple) -> tuple:
    """把一条正文范围按**页**切成连续的页段（保持 run 内原序）。

    §18.6.1 明确允许跨页 run（"家具行不参与，故跨页 run 允许"）。切分只在
    `table_inside` 上生效：表格对象是**单页**对象（§19.4.2），跨页范围没有唯一物理
    矩形，因此"一条跨页处置"在处置层是**上游缺一个可回查矩形**的状态，必须在这里
    变成逐页的单页处置，而不是让下游各自去猜。

    切分判据只有一条：相邻两帧的 `key[0]`（页号）是否相同。这保证每段仍是**连续**
    的源序片段，段内 `start_line <= end_line`。

    同一页在一条 run 里出现两次（行走序非按页单调）时**拒绝**：那意味着"按页分段"
    会把同一页的字符拆到两条处置记录里，或让两条记录的页内行区间互相覆盖 —— 两种
    都不是本函数能诚实表达的状态，故 fail-closed。
    """
    segments: list = []
    for frame in run:
        if segments and segments[-1][-1].key[0] == frame.key[0]:
            segments[-1].append(frame)
        else:
            segments.append([frame])
    pages = [seg[0].key[0] for seg in segments]
    if len(pages) != len(set(pages)):
        raise SpanBuildError(
            f"同一条正文范围内同一页出现两次（页序 {pages}）；按页分段会把同一页的"
            f"字符拆进两条处置记录（fail-closed）")
    return tuple(tuple(seg) for seg in segments)


def _build_non_regular_disposition(run: tuple) -> list:
    """只处理**正文域**内的非 regular 范围（T / A / U / E，§18.9.2）。

    返回**处置记录列表**：`table_inside` 跨页时在构造期按页分段，每段各得一条单页
    处置记录（页序即 run 序，`disposition_id` 由 `create` 的内容身份派生，不写死）。
    单页 run 退化为恰好一条，逐字段与分段前一致。

    `table_adjacency` / `unassigned` / `empty` 与 `regular` 一样**不**按页切分：它们
    不参与 §19.5.1 的冻结范围通道，切分不会解开任何下游门，只会凭空多出记录。
    """
    first, last = run[0], run[-1]
    common = dict(
        start_page=first.key[0], start_line=first.key[1], end_page=last.key[0],
        end_line=last.key[1], line_count=len(run),
        tight_char_count=sum(tight_char_count_of(f.line.text) for f in run))
    if first.kind == _KIND_TABLE_INSIDE:
        out: list = []
        for segment in _page_segments(run):
            head, tail = segment[0], segment[-1]
            out.append(BodyRangeDisposition.create(
                range_kind="table_inside", node_id=head.node_id,
                unassigned_reason=None, table_scope=head.table_scope,
                table_reason=head.table_reason,
                start_page=head.key[0], start_line=head.key[1],
                end_page=tail.key[0], end_line=tail.key[1],
                line_count=len(segment),
                tight_char_count=sum(
                    tight_char_count_of(f.line.text) for f in segment)))
        return out
    if first.kind == _KIND_TABLE_ADJACENT:
        return [BodyRangeDisposition.create(
            range_kind="table_adjacency", node_id=first.node_id,
            unassigned_reason=None, table_scope=first.table_scope,
            table_reason=first.table_reason, **common)]
    if first.kind == _KIND_UNASSIGNED:
        reason = _UNASSIGNED_REASON_BY_ATTACHMENT.get(first.state.body_attachment)
        if reason is None:
            raise SpanBuildError(
                f"无法把 body_attachment={first.state.body_attachment!r} 映射到已登记的"
                f" unassigned_reason（fail-closed）")
        return [BodyRangeDisposition.create(
            range_kind="unassigned", node_id=None, unassigned_reason=reason,
            table_scope=None, table_reason=None, **common)]
    if first.kind == _KIND_EMPTY:
        return [BodyRangeDisposition.create(
            range_kind="empty", node_id=first.node_id, unassigned_reason=None,
            table_scope=None, table_reason=None, **common)]
    raise SpanBuildError(
        f"非 regular 正文范围不得由 {first.kind!r} 构造（fail-closed）")


def _disposition_id_by_run_page(runs: tuple, dispositions: list) -> dict:
    """非 regular run → `{页号: 该页所属处置记录的 disposition_id}`。

    只有 `table_inside` 会跨页展开成**多条**记录，因此只有它需要按页定位：逐页取该页
    **首帧**（分段保证每页恰好属于一段，且该段起点就是该页的首帧）。

    其余非 regular kind（`table_adjacency` / `unassigned` / `empty`）**不**按页分段，
    整条 run 仍然恰好一条处置记录，各页都引用**它**——这与分段前"每个 run 一个 id"的
    行为逐字节一致，也避免对这些 kind 凭空要求一条"起点在页 2"的记录（那不存在）。

    `_disposition_id_by_position` 仍是唯一的定位实现（同一条 `(start_page, start_line)`
    判据），本函数只决定"问几次、问哪些位置"。
    """
    out: dict = {}
    for run in runs:
        if run[0].kind not in _BODY_KINDS or run[0].kind == _KIND_REGULAR:
            continue
        if run[0].kind == _KIND_TABLE_INSIDE:
            by_page: dict = {}
            for frame in run:
                page = frame.key[0]
                if page not in by_page:
                    by_page[page] = _disposition_id_by_position(dispositions,
                                                               frame.key)
            out[id(run)] = by_page
        else:
            one = _disposition_id_by_position(dispositions, run[0].key)
            out[id(run)] = {frame.key[0]: one for frame in run}
    return out


def tight_char_count_of(text: str) -> int:
    """逐行 tight 字符数（守恒口径；空格的差异不参与守恒）。"""
    return len(tight(text))


# ---------------------------------------------------------------------------
# 12. 组件
# ---------------------------------------------------------------------------

def _build_component(*, inp: SpanBuildInput, projection: _SegmentProjection,
                     run_of_key: dict, span_by_run: dict,
                     disposition_id_by_run_page: dict, unassigned_span_by_line: dict,
                     tally: _BlockTally) -> SpanEvidenceComponent:
    """一个 char_map 段的组件：判定顺序固定，先命中者生效（§18.5.2 + §18.8.1）。"""
    terminal = projection.terminal
    segment = projection.segment
    bs, be = int(segment[0]), int(segment[1])
    key = (int(segment[2]), int(segment[3]))
    verdict = terminal.verdict if terminal.verdict is not None else "refused"
    refusal_reason = terminal.refusal_reason if verdict == "refused" else None
    hit = projection.hit
    run = run_of_key.get(key)
    span_local = None
    span_id_value = None
    disposition_id_value = None
    node_id = None
    if hit is None:
        landing, admission_reason = ("alignment_offset_unverifiable",
                                     "offset_unverifiable")
    elif run is None:
        # 落在不属于任何正文范围的行上（例如家具行）：诚实记为 body 之外。
        landing, admission_reason = "outside_body", "landing_not_body_span"
    elif run[0].kind == _KIND_REGULAR:
        span_obj = span_by_run[id(run)]
        span_id_value = span_obj.span_id
        node_id = span_obj.node_id
        landing = "body_span"
        span_local = projection.s_range
        if span_local is None:
            admission_reason = "boundary_inexact"
        elif verdict == "refused":
            admission_reason = "refusal_record"
        elif verdict != "aligned":
            admission_reason = "verdict_not_aligned"
        else:
            admission_reason = "aligned_projected"
    else:
        landing = _LANDING_BY_KIND[run[0].kind]
        admission_reason = "landing_not_body_span"
        node_id = run[0].node_id
        if landing == "formal_unassigned":
            unassigned_id = unassigned_span_by_line.get(key)
            if unassigned_id is None:
                # 该行虽被诊断为 formal_unassigned，却不在正式 unassigned 集合里；
                # 不得凭空引用一个不存在的材料单元。
                landing = "outside_body"
                node_id = None
            else:
                span_id_value = unassigned_id
                node_id = None
        if landing != "formal_unassigned":
            # 分段后按**该片段所在页**取处置记录：跨页 `table_inside` 的页 2 组件必须
            # 指向页 2 那条单页处置，而不是整条 run 的首段（恒假引用）。
            disposition_id_value = disposition_id_by_run_page.get(
                id(run), {}).get(key[0])
    admitted = admission_reason == "aligned_projected"
    # §18.9.3 的 Evidence 三分法必须**恰好铺满**该块：走查成功者计入 mapped，
    # 走查失败者计入 unverifiable，二者与 residue 一起是 `[0, block_char_length)` 的
    # 合法划分。把失败段混进 mapped 会让"不可核验"悄悄消失，因此这里严格二分。
    #
    # 落点直方图**只**统计落位成功的段：`EvidenceConservationRow` 自身的不变式是
    # `Σ landing.char_count == |mapped| + residue_chars`，若给不可核验段也记一次落点，
    # 该和式会被多算一倍。不可核验段只进 `unverifiable_intervals`。
    if hit is None:
        tally.unverifiable.append((bs, be))
    else:
        tally.mapped.append((bs, be))
        tally.landing(landing, be - bs)
    return SpanEvidenceComponent.create(
        evidence_block_id=terminal.evidence_block_id,
        terminal_kind=terminal.terminal_kind, terminal_id=terminal.terminal_id,
        verdict=verdict, evidence_char_range=(bs, be), landing=landing,
        admitted=admitted, admission_reason=admission_reason,
        refusal_reason=refusal_reason,
        span_local_char_range=(span_local if admitted else None),
        node_id=node_id, span_id=span_id_value,
        disposition_id=disposition_id_value,
        layout_hits=((hit,) if hit is not None else ()))


def _build_residue_component(terminal, start: int, end: int,
                             residue_class: str) -> SpanEvidenceComponent:
    """残差段的组件：`alignment_residue_unmapped` / `residue_unmapped`（永不准入）。"""
    verdict = terminal.verdict if terminal.verdict is not None else "refused"
    return SpanEvidenceComponent.create(
        evidence_block_id=terminal.evidence_block_id,
        terminal_kind=terminal.terminal_kind, terminal_id=terminal.terminal_id,
        verdict=verdict, evidence_char_range=(start, end),
        landing="alignment_residue_unmapped", admitted=False,
        admission_reason="residue_unmapped",
        refusal_reason=(terminal.refusal_reason if verdict == "refused" else None),
        residue_class=residue_class, layout_hits=())


# ---------------------------------------------------------------------------
# 13. 覆盖与守恒
# ---------------------------------------------------------------------------

def _build_coverage(span_obj: OutlineSpan, components: tuple) -> SpanCitableCoverage:
    """§18.8.2/§18.8.3：四分类只在**区间并集**上分账，禁止跨 Evidence 相加。"""
    length = len(span_obj.normalized_text)
    norm = merge_intervals(_normalization_only_intervals(span_obj.normalized_text))
    required = invert_intervals(norm, length)
    admitted: list = []
    projected: list = []
    covering: list = []
    for component in components:
        if component.span_id != span_obj.span_id \
                or component.landing != "body_span":
            continue
        if component.span_local_char_range is None:
            continue
        projected.append(component.span_local_char_range)
        if component.admitted:
            admitted.append(component.span_local_char_range)
            covering.append(component.component_id)
    admitted_merged = merge_intervals(admitted)
    projected_merged = merge_intervals(projected)
    citable = intersect_intervals(admitted_merged, required)
    rejected = subtract_intervals(projected_merged, admitted_merged)
    non_citable = intersect_intervals(rejected, required)
    uncovered = subtract_intervals(
        required, merge_intervals(list(citable) + list(non_citable)))
    return SpanCitableCoverage.create(
        span_id=span_obj.span_id, span_local_length=length,
        normalization_only_intervals=norm, citable_source_intervals=citable,
        non_citable_source_intervals=non_citable,
        uncovered_source_intervals=uncovered,
        covering_component_ids=tuple(sorted(covering)))


def _build_conservation(inp: SpanBuildInput, facts: tuple, dispositions: list,
                        spans: list, components: list, tally_by_block: dict,
                        text_by_id: dict) -> SpanConservation:
    """§18.9 的三层守恒（文档层 / 正文域层 / 逐条 Evidence 独立分区层）。

    三层的口径各不相同，因此**不能**互相替代：文档层与正文域层各自按行计数、
    Evidence 层按**每条 Evidence 自己的**字符域独立分区（跨 Evidence 禁止相加）。
    """
    gaps: list = []

    def add_gap(layer: str, reason: str, source_identity: str, interval, line_ref,
                detail: str) -> None:
        gaps.append(ConservationGap(
            gap_id=identity("gap", {
                "layer": layer, "reason_code": reason,
                "source_identity": source_identity,
                "interval": (list(interval) if interval is not None else None),
                "line_ref": (list(line_ref) if line_ref is not None else None),
                "detail": detail}),
            layer=layer, reason_code=reason, source_identity=source_identity,
            interval=interval, line_ref=line_ref, detail=detail))

    # -- 文档层：非家具行按四态分桶 ---------------------------------------
    doc_counts = {b: [0, 0] for b in DOCUMENT_LAYER_BUCKETS}
    for fact in facts:
        bucket = _DOC_BUCKET_BY_STATE[fact.state.state]
        doc_counts[bucket][0] += 1
        doc_counts[bucket][1] += tight_char_count_of(fact.line.text)
    document_layer = tuple(
        LayerBucket(bucket=b, line_count=doc_counts[b][0],
                    tight_char_count=doc_counts[b][1])
        for b in DOCUMENT_LAYER_BUCKETS)

    # -- 正文域层：正文范围处置按 range_kind 分桶 -------------------------
    body_counts = {b: [0, 0] for b in BODY_LAYER_BUCKETS}
    for disposition in dispositions:
        bucket = _BODY_BUCKET_BY_KIND[disposition.range_kind]
        body_counts[bucket][0] += disposition.line_count
        body_counts[bucket][1] += disposition.tight_char_count
    body_layer = tuple(
        LayerBucket(bucket=b, line_count=body_counts[b][0],
                    tight_char_count=body_counts[b][1])
        for b in BODY_LAYER_BUCKETS)

    # -- 层间一致性 1：正文域层合计必须恰为文档层的 body 桶 ---------------
    doc_body = {b.bucket: b for b in document_layer}["body"]
    body_lines = sum(b.line_count for b in body_layer)
    body_chars = sum(b.tight_char_count for b in body_layer)
    if (body_lines, body_chars) != (doc_body.line_count, doc_body.tight_char_count):
        add_gap(
            "body", "layer_sum_mismatch", "body_layer", None, None,
            f"正文域层合计 (行 {body_lines}, 字符 {body_chars}) 与文档层 body 桶 "
            f"(行 {doc_body.line_count}, 字符 {doc_body.tight_char_count}) 不相等；"
            f"正文范围未覆盖全部正文行或多算了行")

    # -- 层间一致性 2：每个 span 恰有一份 regular 处置，且反之亦然 --------
    span_ids = {s.span_id for s in spans}
    regular_by_span: dict = {}
    for disposition in dispositions:
        if disposition.range_kind == "regular":
            regular_by_span[disposition.span_id] = disposition
    for span in spans:
        if span.span_id not in regular_by_span:
            add_gap(
                "body", "span_without_regular_disposition", span.span_id,
                span.char_range, span.start_anchor[:2],
                f"span {span.span_id} 没有任何 regular 正文范围处置记录")
    for span_id_value, disposition in regular_by_span.items():
        if span_id_value not in span_ids:
            add_gap(
                "body", "regular_disposition_without_span", span_id_value,
                None, (disposition.start_page, disposition.start_line),
                f"regular 处置引用了不存在的 span_id={span_id_value!r}")

    # -- 层间一致性 3：组件与处置/span 的引用必须落到实处 -----------------
    # 可引用目标包含**继承来的** TS3 unassigned span：`formal_unassigned` 行上的组件
    # 只做精确引用，不复制进 TS4 新材料集合，因此它们不在这批 `spans` 里。
    known_span_ids = span_ids | {s.span_id for s in inp.document_outline.unassigned}
    disposition_ids = {d.disposition_id for d in dispositions}
    for component in components:
        if component.span_id is not None and component.span_id not in known_span_ids:
            add_gap(
                "evidence", "component_target_missing", component.component_id,
                component.evidence_char_range, None,
                f"组件引用了不存在的 span_id={component.span_id!r}")
        if component.disposition_id is not None \
                and component.disposition_id not in disposition_ids:
            add_gap(
                "evidence", "component_target_missing", component.component_id,
                component.evidence_char_range, None,
                f"组件引用了不存在的 disposition_id={component.disposition_id!r}")

    # -- Evidence 层：逐条独立分区 ----------------------------------------
    rows: list = []
    for block in inp.evidence_blocks:
        rows.append(_conserve_block(block, tally_by_block[block.evidence_block_id],
                                    text_by_id))
    rows.sort(key=lambda r: r.evidence_id)
    gaps.sort(key=lambda g: g.gap_id)
    return SpanConservation.create(
        document_layer=document_layer, body_layer=body_layer,
        evidence_layer=tuple(rows), gaps=tuple(gaps))


def _conserve_block(block: EvidenceBlockInput, tally: _BlockTally,
                    text_by_id: dict) -> EvidenceConservationRow:
    """单条 Evidence 的独立守恒行。

    三分法与落点直方图的两条不变式都由冻结的 `EvidenceConservationRow` 自身强制
    （`check_interval_partition` + 落点合计），因此这里**先**用同一个
    `partition_problems` 复算一次，只是为了在失败时能报出具体的 `evidence_id`，
    而不是抛一条没有来源的通用断言。
    """
    length = len(tight(text_by_id[block.evidence_block_id]))
    mapped = merge_intervals(tally.mapped)
    unverifiable = merge_intervals(tally.unverifiable)
    residue = tuple(sorted(tally.residue))
    tiling = partition_problems(
        (("mapped_char_map_landings", mapped),
         ("alignment_offset_unverifiable", unverifiable),
         ("residue_unmapped", [(a, b) for (a, b, _c) in residue])),
        length)
    if tiling:
        raise SpanBuildError(
            f"Evidence {block.evidence_block_id} 的三分法不是 [0,{length}) 的合法划分："
            f"{'；'.join(tiling)}（fail-closed）")
    order = {name: i for i, name in enumerate(COMPONENT_LANDINGS)}
    landings = tuple(
        LandingCount(landing=name, segment_count=tally.landing_segments[name],
                     char_count=tally.landing_chars[name])
        for name in sorted(tally.landing_chars, key=lambda n: order[n]))
    residue_chars = sum(b - a for (a, b, _c) in residue)
    if sum(lc.char_count for lc in landings) != interval_length(mapped) + residue_chars:
        raise SpanBuildError(
            f"Evidence {block.evidence_block_id} 的落点直方图合计与已映射/残差字符数"
            f"不等（fail-closed）")
    return EvidenceConservationRow(
        evidence_id=block.evidence_block_id, block_char_length=length,
        mapped_intervals=mapped, unverifiable_intervals=unverifiable,
        residue_intervals=residue, landing_counts=landings, conserved=True,
        problems=())


# ---------------------------------------------------------------------------
# 14. §18.3.5 的封闭输入载荷
# ---------------------------------------------------------------------------

def _compose_trusted_input(inp: SpanBuildInput) -> TrustedBuildInput:
    """按 §18.3.5 的固定字段与顺序装配封闭输入载荷（不得增删换序）。"""
    layout = inp.page_layout
    outline = inp.document_outline
    snapshot = inp.evidence_snapshot
    structure = inp.structure_snapshot
    policy = inp.qualification_policy
    handoff = inp.handoff
    layout_capability = handoff.layout_capability
    alignment = handoff.alignment
    authority = handoff.evidence_authority
    members = tuple(tuple(m) for m in snapshot.member_identities())
    member_sha = sha256_canonical([list(m) for m in members])
    terminal_fp = handoff._terminal_provider_authority_fingerprint
    ordered = tuple(sorted(inp.alignment_terminals, key=lambda t: t.sort_key))
    terminals = tuple(tuple(t.identity()) + (terminal_fp,) for t in ordered)
    factors = tuple(item for bf in policy.factor_entries
                    for item in (bf.side, bf.cause, bf.factor))
    factor_fp = sha256_canonical([bf.to_dict() for bf in policy.factor_entries])
    # §18.8.5：A 阶段四槽**全部为 None**；B 阶段四槽**全部为 64 位 sha256**并与仓库内
    # approval record 精确一致。判定依据是**策略记录自身**的阶段，因此历史 A 快照在 B
    # 环境下重算出的四槽仍为 None，其 input_fingerprint 逐位不变。
    (approval_fp, bound_aggregate, bound_distribution,
     bound_attestation) = SP.qualification_binding_slots(policy)
    return TrustedBuildInput(
        page_layout=(layout.page_layout_id, layout.schema_version,
                     layout.engine_version, layout.normalization_version,
                     layout.source_file_sha256,
                     sha256_canonical(layout.to_dict())),
        outline=(outline.outline_locator, outline.outline_id,
                 outline.schema_version, outline.algorithm_version,
                 V.HEADING_QUALIFICATION_PROFILE_VERSION,
                 V.TABLE_REGION_QUALIFICATION_VERSION,
                 V.TOC_BODY_RECONCILIATION_VERSION,
                 sha256_canonical(outline.to_dict())),
        structure=(structure.structure_snapshot_id, structure.schema_version,
                   structure.content_fingerprint,
                   _OutlineStructureSnapshotProvider.version,
                   handoff._structure_provider_authority_fingerprint),
        evidence=(snapshot.fingerprint, snapshot.snapshot_version,
                  snapshot.gateway_version,
                  _ReadonlyCurrentEvidenceGateway.version,
                  handoff._evidence_gateway_authority_fingerprint, member_sha,
                  members),
        terminals=terminals,
        qualification=(policy.policy_id, policy.schema_version,
                       policy.policy_version, policy.stage,
                       policy.span_confidence_min, factors, factor_fp,
                       approval_fp, bound_aggregate, bound_distribution,
                       bound_attestation,
                       _QualificationPolicyProvider.version,
                       inp.policy_authority_fingerprint),
        rules=(V.TS4_BODY_SPAN_BUILDER_VERSION, V.NORMALIZATION_VERSION,
               V.ALIGNER_VERSION, V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
               V.SYNOPSIS_VERSION),
        handoff=(handoff.issuer_scope, handoff.source_kind,
                 handoff.handoff_identity, handoff.issuer_version,
                 layout_capability.authority_fingerprint,
                 layout_capability.issuer_version,
                 alignment.authority_fingerprint, alignment.issuer_version,
                 authority.authority_fingerprint, authority.issuer_version))


# ---------------------------------------------------------------------------
# 15. 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """本模块的机械自检（供 `evals.test_tree_span_builder` 与 verifier 调用）。"""
    problems: list[str] = []
    for name in ("TS4_BODY_SPAN_BUILDER_VERSION", "VERIFIED_TS3_HANDOFF_VERSION",
                 "PINNED_TS3_HANDOFF_VERSION", "TESTING_TS3_HANDOFF_VERSION",
                 "OUTLINE_STRUCTURE_PROVIDER_VERSION",
                 "EVIDENCE_GATEWAY_PROVIDER_VERSION",
                 "ALIGNMENT_TERMINAL_PROVIDER_VERSION",
                 "QUALIFICATION_POLICY_PROVIDER_VERSION",
                 "VERIFIED_PAGE_LAYOUT_ISSUER_VERSION",
                 "PINNED_PAGE_LAYOUT_ISSUER_VERSION",
                 "PINNED_ALIGNMENT_ISSUER_VERSION",
                 "VERIFIED_ALIGNMENT_ISSUER_VERSION"):
        value = getattr(V, name, None)
        if not isinstance(value, str) or value == "":
            problems.append(f"versions.{name} 缺失或为空")
    table = SP.ab_gate_truth_table()
    if table["stage"] == "distribution_only" and V.SPAN_CONFIDENCE_MIN is not None:
        problems.append(
            f"TS4-A 要求 SPAN_CONFIDENCE_MIN 保持 None，得到 "
            f"{V.SPAN_CONFIDENCE_MIN!r}")
    if sorted(_BODY_BUCKET_BY_KIND) != sorted(BODY_RANGE_KINDS):
        problems.append("range_kind 覆盖不完整")
    if sorted(_BODY_BUCKET_BY_KIND.values()) != sorted(BODY_LAYER_BUCKETS):
        problems.append("正文层桶名与 BODY_LAYER_BUCKETS 不是双射")
    if sorted(_DOC_BUCKET_BY_STATE) != sorted(STRUCTURE_SNAPSHOT_COUNT_KEYS):
        problems.append("结构状态覆盖不完整")
    if sorted(_DOC_BUCKET_BY_STATE.values()) != sorted(DOCUMENT_LAYER_BUCKETS):
        problems.append("文档层桶名与 DOCUMENT_LAYER_BUCKETS 不是双射")
    if set(_LEFT_CAUSE_BY_PREDECESSOR.values()) != set(SS.BOUNDARY_CAUSES_LEFT):
        problems.append("左边界成因真值表未覆盖全部允许值")
    if set(_RIGHT_CAUSE_BY_SUCCESSOR.values()) | {"document_end"} \
            != set(SS.BOUNDARY_CAUSES_RIGHT):
        problems.append("右边界成因真值表未覆盖全部允许值")
    if set(_LANDING_BY_KIND) != {
            _KIND_TABLE_INSIDE, _KIND_TABLE_ADJACENT, _KIND_UNASSIGNED,
            _KIND_EMPTY, _KIND_HEADING, _KIND_FORMAL, _KIND_NON_CONTENT}:
        problems.append("正文行类别 → 落点映射不完整")
    for landing in _LANDING_BY_KIND.values():
        if landing not in COMPONENT_LANDINGS:
            problems.append(f"落点 {landing!r} 不在 COMPONENT_LANDINGS")
    if not isinstance(_FACTORY_TOKEN, object):
        problems.append("工厂令牌缺失")  # pragma: no cover - 防御性
    return {
        "span_builder_version": V.TS4_BODY_SPAN_BUILDER_VERSION,
        "stage": table["stage"],
        "span_confidence_min": V.SPAN_CONFIDENCE_MIN,
        "completion_enabled": table["completion_enabled"],
        "boundary_cause_count": (len(SS.BOUNDARY_CAUSES_LEFT)
                                 + len(SS.BOUNDARY_CAUSES_RIGHT)),
        "problems": problems,
    }

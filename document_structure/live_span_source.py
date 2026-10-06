"""M930-2 §5.4：**同次 live tree capability 组合根**。

Demo Backbone 不把历史 TS4 pinned artifact 恢复成运行时 authority（那是验收私有
入口，见 `span_builder._issue_pinned_ts3_handoff` / `evaluation/run_tree_span_acceptance.py`）。
正式链路改为：**在同一进程内**由原始 PDF 与 current Evidence 重新签发一份新的 live
span capability，并把它连同可持久化的 identity projection 一起交给树材料 / Tool
adapter 消费。

本模块只调用既有公开生产入口，且顺序固定：

```text
build_verified_page_layout(raw_pdf, declared identities)
  → bind_current_evidence_authority()
  → align_evidence_set_verified(verified_layout, evidence_authority)
  → issue_live_ts3_handoff(layout, alignment, evidence_authority)
  → build_span_snapshot(handoff, frozen stage)
  → verify_span_snapshot(snapshot, handoff)
```

组合根**不** init / migrate / 写任何 DB：Evidence Store 的绑定是
`sections.service._prepare_stores` 的只读预检职责，`bind_current_evidence_authority`
只读 `evidence.store._db_path`，本模块不碰它。

反自证边界：`VerifiedTS3Handoff` / `VerifiedSpanSnapshot` 是 runtime-only 能力，
`LiveVerifiedSpanSource` 只**持有**它们，不复制、不序列化、不从 JSON 重建。需要
重新证明 authority 时只能从 raw PDF / current Evidence 重走上面的链并比较 identity
projection（`reprove_live_span_source`），**不得**从 JSON 构造 "verified=true"。
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
from typing import Any

from document_structure import aligner as AL
from document_structure import evidence_gateway as EG
from document_structure import layout_builder as LB
from document_structure import span_builder as SB
from document_structure import span_policy as SP
from document_structure import span_verifier as SV
from document_structure import versions as V
from document_structure.schema import SchemaValidationError


class LiveSpanSourceError(SchemaValidationError):
    """live span 组合失败（身份不符 / 版本漂移 / 越界使用运行时能力）。"""


#: 组合根只签发 `live` 域；`pinned_acceptance` / `testing` 不得流入 M930 正式链路。
LIVE_ISSUER_SCOPE = "live"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nonempty(name: str, value: Any) -> str:
    if not isinstance(value, str) or value == "":
        raise LiveSpanSourceError(f"{name} 必须为非空字符串，得到 {value!r}")
    return value


def _sha256_hex(name: str, value: Any) -> str:
    _nonempty(name, value)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise LiveSpanSourceError(f"{name} 必须为 64 位小写十六进制 sha256，得到 {value!r}")
    return value


# ---------------------------------------------------------------------------
# 1. 公开输入
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class LiveSpanBuildRequest:
    """一次 live span 重建请求（**声明的**身份，不是自证）。

    `raw_pdf_sha256` / `document_version` / `expected_current_evidence_set_version`
    都是调用方**事先声明**的值：组合根读盘后逐项复核，不一致即 fail-closed。声明值
    给不出来就不该构建——"先构建再回头看看拿到的是什么"不是可接受的用法。
    """

    company_id: str
    document_id: str
    document_version: str
    raw_pdf_path: str
    raw_pdf_sha256: str
    expected_current_evidence_set_version: str

    def __post_init__(self) -> None:
        _nonempty("company_id", self.company_id)
        _nonempty("document_id", self.document_id)
        _nonempty("document_version", self.document_version)
        _nonempty("raw_pdf_path", self.raw_pdf_path)
        _sha256_hex("raw_pdf_sha256", self.raw_pdf_sha256)
        _nonempty("expected_current_evidence_set_version",
                  self.expected_current_evidence_set_version)


# ---------------------------------------------------------------------------
# 2. 运行时 capability 包装（只持有，不复制、不序列化）
# ---------------------------------------------------------------------------

class LiveVerifiedSpanSource:
    """**同次签发**的 live 树材料来源（运行时对象，不可序列化）。

    它持有的两个能力对象仍是 `span_schema` 签发登记表里的那一个实例；本类不签发
    新的能力种类，因此"自造一个 `LiveVerifiedSpanSource`"拿不到任何资格——每一次
    对外取用都会重新要求内层能力仍在本进程签发登记表中。
    """

    __slots__ = ("_handoff", "_verified", "_request_identity", "__weakref__")

    def __init__(self, *, handoff: SB.VerifiedTS3Handoff,
                 verified: SV.VerifiedSpanSnapshot,
                 request_identity: dict) -> None:
        self._handoff = handoff
        self._verified = verified
        self._request_identity = dict(request_identity)

    # -- 资格复核 ---------------------------------------------------------

    def _assert_live(self) -> None:
        from document_structure.span_schema import issued_capability
        issued_capability(self._handoff, "VerifiedTS3Handoff", (LIVE_ISSUER_SCOPE,))
        issued_capability(self._verified, "VerifiedSpanSnapshot", (LIVE_ISSUER_SCOPE,))

    # -- 只读访问（每次取用都先复核资格）-----------------------------------

    @property
    def verified_snapshot(self) -> SV.VerifiedSpanSnapshot:
        self._assert_live()
        return self._verified

    @property
    def snapshot(self):
        """真实 `SpanBuildSnapshot`（可持久化的 plain 快照）。"""
        self._assert_live()
        return self._verified.snapshot

    @property
    def handoff(self) -> SB.VerifiedTS3Handoff:
        self._assert_live()
        return self._handoff

    @property
    def page_layout(self):
        self._assert_live()
        return self._handoff.page_layout

    @property
    def document_outline(self):
        self._assert_live()
        return self._handoff.document_outline

    @property
    def evidence_snapshot(self):
        self._assert_live()
        return self._handoff.evidence_snapshot

    @property
    def evidence_blocks(self) -> tuple:
        self._assert_live()
        return self._handoff.evidence_blocks

    @property
    def qualification_policy(self):
        self._assert_live()
        return self._handoff.qualification_policy

    def spans(self) -> tuple:
        self._assert_live()
        return self._verified.snapshot.spans

    def span_by_id(self, span_id: str):
        self._assert_live()
        for span in self._verified.snapshot.spans:
            if span.span_id == span_id:
                return span
        return None

    def component_by_id(self, component_id: str):
        self._assert_live()
        for component in self._verified.snapshot.components:
            if component.component_id == component_id:
                return component
        return None

    def coverage_by_span_id(self, span_id: str):
        self._assert_live()
        for coverage in self._verified.snapshot.coverages:
            if coverage.span_id == span_id:
                return coverage
        return None

    # -- 可持久化身份 -----------------------------------------------------

    def version_identities(self) -> dict:
        """本次签发实际使用的版本（由**对象自身**读出后与权威常量复核）。"""
        self._assert_live()
        return _collect_version_identities(
            self._handoff, self._verified, self._request_identity)

    def identity_projection(self) -> dict:
        """可持久化的完整身份投影（纯 JSON 安全值，不含任何运行时能力）。

        它是"这次签发**是什么**"的记录，不是"这次签发**有效**"的证明：读回它不会
        产生任何资格，重新证明只能重走 live chain（`reprove_live_span_source`）。
        """
        self._assert_live()
        handoff = self._handoff
        verified = self._verified
        snapshot = verified.snapshot
        return {
            "request": dict(self._request_identity),
            "layout": handoff.layout_capability.identity(),
            "evidence_authority": handoff.evidence_authority.identity(),
            "alignment": handoff.alignment.identity(),
            "handoff": handoff.identity(),
            "verified_snapshot": verified.identity(),
            "snapshot": {
                "snapshot_locator": snapshot.snapshot_locator,
                "snapshot_id": snapshot.snapshot_id,
                "schema_version": snapshot.schema_version,
                "document_id": snapshot.document_id,
                "document_version": snapshot.document_version,
                "page_layout_id": snapshot.page_layout_id,
                "outline_id": snapshot.outline_id,
                "alignment_id": snapshot.alignment_id,
                "structure_snapshot_id": snapshot.structure_snapshot_id,
                "input_fingerprint": snapshot.input_fingerprint,
                "content_fingerprint": snapshot.content_fingerprint,
                "span_ids": [s.span_id for s in snapshot.spans],
                "component_count": len(snapshot.components),
                "terminal_count": snapshot.terminal_count,
            },
            "versions": self.version_identities(),
        }

    # -- 反自证 -----------------------------------------------------------

    def to_dict(self) -> dict:
        raise LiveSpanSourceError(
            "LiveVerifiedSpanSource 是运行时来源，不得序列化；持久化请用"
            " identity_projection()，从 JSON 恢复资格的做法不存在（fail-closed）")

    def __copy__(self):
        raise LiveSpanSourceError("LiveVerifiedSpanSource 不可 copy")

    def __deepcopy__(self, memo):
        raise LiveSpanSourceError("LiveVerifiedSpanSource 不可 deepcopy")

    def __reduce__(self):
        raise LiveSpanSourceError("LiveVerifiedSpanSource 不可 pickle")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<LiveVerifiedSpanSource "
                f"{self._request_identity.get('document_id')!r} "
                f"{self._request_identity.get('document_version')!r}>")


# ---------------------------------------------------------------------------
# 3. 版本漂移闸门
# ---------------------------------------------------------------------------

def _check_version(name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise LiveSpanSourceError(
            f"{name} 版本漂移：构建产物为 {actual!r}，当前权威常量为 {expected!r}；"
            f"live span 组合根不得在版本不一致时继续（fail-closed）")


def _collect_version_identities(handoff, verified, request_identity: dict) -> dict:
    """由对象自身读出版本，并逐项对回 `document_structure.versions` 权威常量。"""
    layout = handoff.page_layout
    outline = handoff.document_outline
    snapshot = verified.snapshot
    _check_version("page_layout.schema_version", layout.schema_version,
                   V.LAYOUT_SCHEMA_VERSION)
    _check_version("document_outline.schema_version", outline.schema_version,
                   V.OUTLINE_SCHEMA_VERSION)
    _check_version("document_outline.algorithm_version", outline.algorithm_version,
                   V.OUTLINE_ALGORITHM_VERSION)
    _check_version("span_build_snapshot.schema_version", snapshot.schema_version,
                   V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION)
    _check_version("span_build_snapshot.span_builder_version",
                   snapshot.span_builder_version,
                   V.TS4_BODY_SPAN_BUILDER_VERSION)
    _check_version("span_build_snapshot.synopsis_version", snapshot.synopsis_version,
                   V.SYNOPSIS_VERSION)
    _check_version("span_build_snapshot.qualification_policy_version",
                   snapshot.qualification_policy_version,
                   V.SPAN_QUALIFICATION_POLICY_VERSION)
    _check_version("span_build_snapshot.alignment_schema_version",
                   snapshot.alignment_schema_version, V.ALIGN_SCHEMA_VERSION)
    _check_version("qualification_policy.stage", snapshot.qualification_policy.stage,
                   SP.ab_gate_truth_table()["stage"])
    _check_version("verified_page_layout.issuer_version",
                   handoff.layout_capability.issuer_version,
                   V.VERIFIED_PAGE_LAYOUT_ISSUER_VERSION)
    _check_version("verified_evidence_set_alignment.issuer_version",
                   handoff.alignment.issuer_version,
                   V.VERIFIED_ALIGNMENT_ISSUER_VERSION)
    _check_version("verified_current_evidence_authority.issuer_version",
                   handoff.evidence_authority.issuer_version,
                   V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION)
    _check_version("verified_ts3_handoff.issuer_version", handoff.issuer_version,
                   V.VERIFIED_TS3_HANDOFF_VERSION)
    _check_version("verified_span_snapshot.issuer_version", verified.issuer_version,
                   V.VERIFIED_SPAN_SNAPSHOT_VERSION)
    return {
        "layout_schema_version": layout.schema_version,
        "outline_schema_version": outline.schema_version,
        "outline_algorithm_version": outline.algorithm_version,
        "span_snapshot_schema_version": snapshot.schema_version,
        "span_builder_version": snapshot.span_builder_version,
        "synopsis_version": snapshot.synopsis_version,
        "qualification_policy_version": snapshot.qualification_policy_version,
        "qualification_policy_id": snapshot.qualification_policy.policy_id,
        "qualification_policy_stage": snapshot.qualification_policy.stage,
        "alignment_schema_version": snapshot.alignment_schema_version,
        "aligner_version": V.ALIGNER_VERSION,
        "alignment_partition_validator_version":
            V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        "verified_page_layout_issuer_version":
            handoff.layout_capability.issuer_version,
        "verified_alignment_issuer_version": handoff.alignment.issuer_version,
        "verified_evidence_authority_version":
            handoff.evidence_authority.issuer_version,
        "verified_ts3_handoff_version": handoff.issuer_version,
        "verified_span_snapshot_version": verified.issuer_version,
    }


# ---------------------------------------------------------------------------
# 4. 声明的身份 → 实际构建结果（逐项 fail-closed）
# ---------------------------------------------------------------------------

def _assert_declared_identity(request: LiveSpanBuildRequest, handoff, verified
                              ) -> None:
    layout = handoff.page_layout
    if layout.company_id != request.company_id:
        raise LiveSpanSourceError(
            f"构建出的 company_id={layout.company_id!r} 与声明 "
            f"{request.company_id!r} 不一致（fail-closed）")
    if layout.document_id != request.document_id:
        raise LiveSpanSourceError(
            f"构建出的 document_id={layout.document_id!r} 与声明 "
            f"{request.document_id!r} 不一致（fail-closed）")
    if layout.source_file_sha256 != request.raw_pdf_sha256:
        raise LiveSpanSourceError(
            f"原 PDF 哈希漂移：构建版式为 {layout.source_file_sha256!r}，声明为 "
            f"{request.raw_pdf_sha256!r}（fail-closed）")
    if layout.document_version != request.document_version:
        raise LiveSpanSourceError(
            f"document version 漂移：构建版式为 {layout.document_version!r}，声明为 "
            f"{request.document_version!r}（fail-closed）")
    if handoff.document_outline.document_version != request.document_version:
        raise LiveSpanSourceError(
            "大纲的 document_version 与声明的 document_version 不一致（fail-closed）")
    snapshot = handoff.evidence_snapshot
    if snapshot.evidence_set_version != request.expected_current_evidence_set_version:
        raise LiveSpanSourceError(
            f"current Evidence set 漂移：当前集合为 "
            f"{snapshot.evidence_set_version!r}，请求声明为 "
            f"{request.expected_current_evidence_set_version!r}（fail-closed）")
    if verified.issuer_scope != LIVE_ISSUER_SCOPE:
        raise LiveSpanSourceError(
            f"live 组合根只接受 live 域的复核结果，得到 {verified.issuer_scope!r}")
    if handoff.issuer_scope != LIVE_ISSUER_SCOPE:
        raise LiveSpanSourceError(
            f"live 组合根只接受 live 域的交接，得到 {handoff.issuer_scope!r}")
    if handoff.page_layout.page_layout_id != handoff.alignment.page_layout.page_layout_id:
        raise LiveSpanSourceError("交接绑定的版式与对齐绑定的版式不是同一份（fail-closed）")


def _request_identity(request: LiveSpanBuildRequest) -> dict:
    return {
        "company_id": request.company_id,
        "document_id": request.document_id,
        "document_version": request.document_version,
        "raw_pdf_path": request.raw_pdf_path,
        "raw_pdf_sha256": request.raw_pdf_sha256,
        "expected_current_evidence_set_version":
            request.expected_current_evidence_set_version,
    }


# ---------------------------------------------------------------------------
# 5. 公开入口
# ---------------------------------------------------------------------------

def _read_declared_pdf(request: LiveSpanBuildRequest) -> bytes:
    """按**声明哈希**读盘：哈希不符直接拒，且后续重建只用这一份字节。"""
    path = pathlib.Path(request.raw_pdf_path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise LiveSpanSourceError(
            f"无法读取声明的 raw PDF {path}：{e}") from e
    actual = _sha256_bytes(data)
    if actual != request.raw_pdf_sha256:
        raise LiveSpanSourceError(
            f"raw PDF 哈希与声明不符：磁盘为 {actual!r}，声明为 "
            f"{request.raw_pdf_sha256!r}（fail-closed，拒绝按未声明的源构建）")
    return data


def build_live_verified_span_snapshot(request: LiveSpanBuildRequest
                                      ) -> LiveVerifiedSpanSource:
    """**M930 唯一** live 树能力组合根：由声明的 raw PDF 重建同次 live span 快照。

    只调用 §5.4 列出的六个公开生产入口，绝不调用 `_issue_pinned_*` 等验收私有
    入口；不 init / migrate / 写任何 DB。任何声明身份漂移或版本漂移均 fail-closed。
    """
    if not isinstance(request, LiveSpanBuildRequest):
        raise LiveSpanSourceError(
            f"组合根只接受 LiveSpanBuildRequest，得到 {type(request).__name__}")
    data = _read_declared_pdf(request)

    layout_capability = LB.build_verified_page_layout(
        data, company_id=request.company_id, document_id=request.document_id,
        document_version=request.document_version)
    evidence_authority = EG.bind_current_evidence_authority()
    alignment = AL.align_evidence_set_verified(layout_capability, evidence_authority)
    handoff = SB.issue_live_ts3_handoff(layout_capability, alignment,
                                        evidence_authority)
    # 阶段由唯一真值表派生（`versions.SPAN_CONFIDENCE_MIN` 是它的唯一输入），
    # 不接受调用方指定：跨阶段新建快照在 `_QualificationPolicyProvider` 里已经被拒。
    stage = SP.ab_gate_truth_table()["stage"]
    snapshot = SB.build_span_snapshot(handoff, stage=stage)
    verified = SV.verify_span_snapshot(snapshot, handoff)

    _assert_declared_identity(request, handoff, verified)
    identity = _request_identity(request)
    source = LiveVerifiedSpanSource(handoff=handoff, verified=verified,
                                    request_identity=identity)
    # 版本漂移闸门：任一 layout/alignment/outline/span/policy 版本与权威常量不一致
    # 都在**返回给调用方之前**失败。
    _collect_version_identities(handoff, verified, identity)
    return source


def reprove_live_span_source(request: LiveSpanBuildRequest,
                             projection: dict) -> LiveVerifiedSpanSource:
    """从 raw PDF / current Evidence **重走** live chain，并要求 identity 与冻结投影相等。

    这是"重新证明 authority"的唯一合法方式：读回 JSON 不产生资格，只有再签发一次
    并逐项比较才成立。不一致（源被替换、Evidence 集合变了、版本升了）即 fail-closed。
    """
    if not isinstance(projection, dict):
        raise LiveSpanSourceError("冻结投影必须为对象")
    for key in ("request", "handoff", "verified_snapshot", "snapshot", "versions"):
        if key not in projection:
            raise LiveSpanSourceError(f"冻结投影缺必要分组 {key!r}（fail-closed）")
    fresh = build_live_verified_span_snapshot(request)
    current = fresh.identity_projection()
    if current != projection:
        drift = [k for k in sorted(set(current) | set(projection))
                 if current.get(k) != projection.get(k)]
        raise LiveSpanSourceError(
            f"重新签发的 live span identity 与冻结投影不一致，漂移分组：{drift}"
            f"（fail-closed；不得用旧投影继续）")
    return fresh


def validate_identity_projection(projection: dict) -> dict:
    """只读结构校验：投影是否为**结构完整**的 live identity 记录。

    它**不**给出任何运行时资格——返回值只说明"这份记录看起来是我方写出的形状"。
    """
    if not isinstance(projection, dict):
        raise LiveSpanSourceError("identity projection 必须为对象")
    required = ("request", "layout", "evidence_authority", "alignment", "handoff",
                "verified_snapshot", "snapshot", "versions")
    missing = [k for k in required if k not in projection]
    if missing:
        raise LiveSpanSourceError(f"identity projection 缺字段：{missing}")
    _nonempty("handoff.issuer_scope", projection["handoff"].get("issuer_scope"))
    if projection["handoff"]["issuer_scope"] != LIVE_ISSUER_SCOPE:
        raise LiveSpanSourceError(
            f"identity projection 不是 live 域："
            f"{projection['handoff']['issuer_scope']!r}（fail-closed）")
    return {"ok": True, "groups": len(required),
            "span_count": len(projection["snapshot"].get("span_ids", ()))}


__all__ = [
    "LIVE_ISSUER_SCOPE", "LiveSpanBuildRequest", "LiveSpanSourceError",
    "LiveVerifiedSpanSource", "build_live_verified_span_snapshot",
    "reprove_live_span_source", "validate_identity_projection",
]

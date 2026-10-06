"""FinancialFactPack 持久化投影（M930-2 §5.10）：只读、可指纹化、可离线复算的财务权威工件。

**本模块不产生财务权威，也不改变财务权威。** 权威仍是
`sections.financial_worker.build_fact_pack_for_task` 产出的 `FinancialFactPack`；本模块只把它
投影成一个带身份、带指纹、带「选了哪些 fact / 排除了哪些 fact 及原因」的持久化 artifact。

硬边界（§5.10 + CLAUDE.md）：
- **不把 TableObject 数字转成 FinancialFact**：本模块只消费已存在的 `FinancialFact`，
  不读文档结构、不读 TableObject、不做任何数值换算（见 `assert_no_table_conversion`）；
- **不回写 FinancialSnapshot**：projection 只读，绝不写财务库、绝不切 current；
- **Evidence-backed 财务附注另存**：附注输入走**独立**的 `ValidatedEvidenceNoteFactSet` /
  `EvidenceNoteGap`（恰有其一，§十一），不得混入本 artifact 冒充普通财务事实——附注的数字
  来自 Evidence 背书，本 artifact 的数字来自 financial_v2 计算，来源权威不同。本批未实现
  附注**抽取**，因此正式结果本轮只会产出 `EvidenceNoteGap`（原因如实写明是"未实现"）；
- **Decimal 一律用规范字符串**（`str(Decimal)`，精确、可往返），绝不用 float 承载金额。
"""
from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, Sequence

from financial_v2 import period_basis as fpb
from harness import structured_provenance as SP
from harness import topic_schema as TS
from harness.schema import CitationRef

__all__ = [
    "FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION",
    "FINANCIAL_PACK_PROJECTION_VERSION",
    "ARTIFACT_REPLAY_MODES",
    "FINANCIAL_ARTIFACT_PRODUCER_KIND",
    "EVIDENCE_NOTE_SCHEMA_VERSION",
    "EVIDENCE_NOTE_GAP_REASONS",
    "EVIDENCE_NOTE_GAP_FORBIDDEN_WORDING",
    "FinancialArtifactError",
    "FinancialFactProjection",
    "ExcludedFinancialFact",
    "FinancialSnapshotIdentity",
    "FinancialPackArtifact",
    "EvidenceNoteFact",
    "ValidatedEvidenceNoteFactSet",
    "EvidenceNoteGap",
    "validate_evidence_note_fact_set",
    "FinancialSnapshotAuthoritySource",
    "FinancialDbAuthoritySource",
    "FINANCIAL_AUTHORITY_READONLY_QUERY_VERSION",
    "ArtifactReplayReport",
    "build_financial_pack_artifact",
    "verify_financial_pack_artifact",
    "assert_no_table_conversion",
]

#: artifact 结构版本。字段增删/语义变化必须递增。
#: `ffpa-2`（M930-3 返修 P3）：`FinancialFactProjection` 新增 `period_basis` 与 `period_label`
#: 两个字段（事实自己携带期间口径与期间表达），因此字段形状变了，`ffpa-1` 载荷不得被当前
#: reader 静默重解释。
#: `ffpa-3`（M930-3 §二）：`FinancialFactProjection` 再增五个字段 —— `formula_version`、
#: `citations`（完整引用集）、`derived_from` / `input_periods` / `input_raw_texts`（派生事实的
#: 两期输入血缘）。一条事实的数值可以是**多条**权威记录的确定性函数（Δpp），此时它必须把
#: **两条**可回查引用与两期原值一起带过界；旧 reader 会把这样一条事实读成「单期单引用」，
#: 那正是「单期间字段冒充双期间」，因此必须换版本号而不是让读者静默重解释。
FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION = "ffpa-3"
#: 投影规则版本（本模块「如何把 FactPack 投影成 artifact」的规则版本）。
#: `ffp-proj-2`：投影把事实的 `period_basis` / `period_label` 原样带过去（不再只带 `period`），
#: 下游读不到口径就无法把「期间量」和「时点量」分开呈现。
#: `ffp-proj-3`：投影再带上完整引用集与派生血缘；`citations` 非空时逐条校验为合法
#: `CitationRef`，`citation` 必须是该集合的成员（不得另立一条主引用）。
FINANCIAL_PACK_PROJECTION_VERSION = "ffp-proj-3"
#: replay 模式：live=重新独立校验 snapshot/current/health；offline_replay=只校验冻结 hash。
ARTIFACT_REPLAY_MODES = ("live", "offline_replay")
#: 财务权威的 producer_kind（§5.10 / writing_spec 的封闭取值）。
FINANCIAL_ARTIFACT_PRODUCER_KIND = "financial_workflow"
#: artifact_id 前缀（确定性身份，不含 run_id / 时间 / 路径）。
_ARTIFACT_ID_PREFIX = "ffpa_"

#: 附注事实 / 附注缺口的 schema 版本（§十一）。附注走**独立**输入状态，绝不混进
#: `FinancialPackArtifact`：财务主权威来自 financial_v2 计算，附注来自 Evidence 背书，
#: 来源权威不同，混在一起会让读者分不清哪个数字来自哪条来源。
EVIDENCE_NOTE_SCHEMA_VERSION = "evn-1"

#: `EvidenceNoteGap` 的**封闭**原因集合。
EVIDENCE_NOTE_GAP_REASONS = (
    # 本批未实现附注抽取（诚实声明：是"没实现"，不是"没披露"）
    "note_extraction_not_implemented",
    # 纳入范围内没有合格附注 span（已查范围与条件见审计字段）
    "no_admissible_note_span",
    # 候选 span 无 Evidence 背书 / 无法精确定位到 span
    "note_span_not_evidence_backed",
)

#: 附注缺口文案的**禁用措辞**：不得暗示"公司未披露"（我们只能说自己没查到）。
EVIDENCE_NOTE_GAP_FORBIDDEN_WORDING = ("未披露", "不存在", "没有披露")


class FinancialArtifactError(Exception):
    """artifact 构建/校验失败（fail-closed）。"""


# ---------------------------------------------------------------------------
# 投影对象（全部只读；Decimal 只以规范字符串出现）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinancialFactProjection:
    """一条被选中财务事实的只读投影。`value_text` 是 Decimal 的规范字符串。"""

    fact_id: str
    kind: str
    label: str
    code: str
    period: str
    value_text: str | None
    display: str
    unit: str
    status: str
    reason_code: str | None
    note: str
    citation: dict
    #: 期间口径（`end` 时点 / `flow` 期间，`financial_v2.period_basis`，`pb-1`）。它随投影一起
    #: 过界，因为下游要把「本报告期的量」和「期末时点的量」分开呈现——只带 `period` 时，一个
    #: 流量指标会被挂在期间末日的列名下面，读者会把它读成时点值。
    period_basis: str = ""
    #: 该事实的期间表达（如 `2025年度` / `2025年末`）。空串表示上游没有给出可解析的期间记号，
    #: 下游一律退回 `period` 逐字呈现，不发明措辞。
    period_label: str = ""
    #: 产出该事实的公式版本（派生事实为 `ddf-1`；不由公式产出时为空串）。
    formula_version: str = ""
    #: **完整**的权威引用集（`citation` 是它的首/主成员）。空元组 = 「就是 `citation` 那一条」。
    #: 只有当事实的数值是**多条**权威记录的确定性函数（Δpp 派生事实）时才有多条。
    citations: tuple[dict, ...] = ()
    #: 派生事实的两期输入血缘（事实 id / 期间记号 / 未舍入原值的规范字符串）。三者同长；
    #: 非派生事实全为空元组。血缘**不进**数字授权面：读者可见的数值只能是 `display`。
    derived_from: tuple[str, ...] = ()
    input_periods: tuple[str, ...] = ()
    input_raw_texts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.fact_id:
            raise FinancialArtifactError("FinancialFactProjection.fact_id 必须非空")
        if self.period_basis and self.period_basis not in fpb.PERIOD_BASES:
            raise FinancialArtifactError(
                f"fact {self.fact_id!r} 的 period_basis={self.period_basis!r} 不在"
                f" {list(fpb.PERIOD_BASES)} 内（口径是封闭取值，不得自造）")
        if self.citations:
            if len(self.citations) < 2:
                raise FinancialArtifactError(
                    f"fact {self.fact_id!r} 的 citations 只有 {len(self.citations)} 条："
                    "多引用集只用于「数值是多条权威记录的函数」的派生事实，"
                    "单引用事实必须留空（空 = 就是 citation 那一条）")
            if dict(self.citation) not in [dict(c) for c in self.citations]:
                raise FinancialArtifactError(
                    f"fact {self.fact_id!r} 的 citation 不在它自己的 citations 集内："
                    "主引用必须是被声明的完整引用集的一员，不得另立一条")
        if self.derived_from or self.input_periods or self.input_raw_texts:
            if not (len(self.derived_from) == len(self.input_periods) == len(self.input_raw_texts)):
                raise FinancialArtifactError(
                    f"fact {self.fact_id!r} 的派生血缘长度不一致："
                    f"derived_from={len(self.derived_from)} "
                    f"input_periods={len(self.input_periods)} "
                    f"input_raw_texts={len(self.input_raw_texts)}")
            if len(self.input_periods) != 2:
                raise FinancialArtifactError(
                    f"fact {self.fact_id!r} 声明了 {len(self.input_periods)} 期输入血缘："
                    "本批只批准两期输入，多期血缘不得冒充")
            if self.period != f"{self.input_periods[1]}|{self.input_periods[0]}":
                raise FinancialArtifactError(
                    f"fact {self.fact_id!r} 声明了双期血缘，但它的 period={self.period!r} "
                    "不是这两期的复合记号（本期|上期）：单期间字段不得冒充双期间")
        if self.value_text is not None:
            try:  # 规范字符串必须可精确往返为 Decimal（拒绝 float 化 / 科学计数法丢失精度）
                Decimal(self.value_text)
            except InvalidOperation as e:
                raise FinancialArtifactError(
                    f"fact {self.fact_id!r} 的 value_text={self.value_text!r} 不是合法 Decimal "
                    f"规范字符串：{e}") from e

    def value_decimal(self) -> Decimal | None:
        return None if self.value_text is None else Decimal(self.value_text)

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "kind": self.kind, "label": self.label,
            "code": self.code, "period": self.period, "value_text": self.value_text,
            "display": self.display, "unit": self.unit, "status": self.status,
            "reason_code": self.reason_code, "note": self.note,
            "citation": dict(self.citation),
            "period_basis": self.period_basis, "period_label": self.period_label,
            "formula_version": self.formula_version,
            "citations": [dict(c) for c in self.citations],
            "derived_from": list(self.derived_from),
            "input_periods": list(self.input_periods),
            "input_raw_texts": list(self.input_raw_texts),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialFactProjection":
        if not isinstance(d, dict):
            raise FinancialArtifactError("FinancialFactProjection 必须是对象")
        return cls(
            fact_id=d["fact_id"], kind=d["kind"], label=d["label"], code=d["code"],
            period=d["period"], value_text=d.get("value_text"), display=d["display"],
            unit=d["unit"], status=d["status"], reason_code=d.get("reason_code"),
            note=d.get("note", ""), citation=dict(d.get("citation") or {}),
            period_basis=d.get("period_basis", ""), period_label=d.get("period_label", ""),
            formula_version=d.get("formula_version", ""),
            citations=tuple(dict(c) for c in (d.get("citations") or ())),
            derived_from=tuple(d.get("derived_from") or ()),
            input_periods=tuple(d.get("input_periods") or ()),
            input_raw_texts=tuple(d.get("input_raw_texts") or ()))


@dataclass(frozen=True)
class ExcludedFinancialFact:
    """一条**被排除**的财务事实：带封闭原因（status/reason_code）与是否 required。

    只登记 FactPack 自己记录下来的缺口（`gaps` / `diagnostic_gaps`）。上游 `build_fact_pack`
    在选材阶段的更早过滤（如 currency/scope 不符、amount 为空、科目不在 include 列表）**没有**
    留下 per-fact 记录，故本 artifact 不为其编造原因——那属于 `projection_notes` 的显式声明。
    """

    fact_id: str
    formula_id: str | None
    label: str
    kind: str
    period: str
    status: str
    reason_code: str | None
    required: bool

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "formula_id": self.formula_id, "label": self.label,
            "kind": self.kind, "period": self.period, "status": self.status,
            "reason_code": self.reason_code, "required": self.required,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExcludedFinancialFact":
        if not isinstance(d, dict):
            raise FinancialArtifactError("ExcludedFinancialFact 必须是对象")
        return cls(
            fact_id=d["fact_id"], formula_id=d.get("formula_id"), label=d.get("label", ""),
            kind=d.get("kind", ""), period=d.get("period", ""), status=d.get("status", ""),
            reason_code=d.get("reason_code"), required=bool(d.get("required")))


@dataclass(frozen=True)
class FinancialSnapshotIdentity:
    """快照身份 + 健康事实（字段为**观测值**；`authoritative` 由字段重算，不采信自报）。"""

    snapshot_id: str
    company_id: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    exists: bool
    is_current: bool
    validity: str | None
    report_blocked: bool
    quarantined: bool

    def authoritative(self) -> bool:
        """与 `harness.structured_provenance.SnapshotAuthority.authoritative()` 同一判据。"""
        return (self.exists and self.is_current and self.validity == "valid"
                and not self.report_blocked and not self.quarantined)

    @classmethod
    def from_authority(cls, authority: SP.SnapshotAuthority, *,
                       company_id: str, as_of_date: str, scope: str, currency: str,
                       purpose: str, snapshot_id: str) -> "FinancialSnapshotIdentity":
        return cls(
            snapshot_id=snapshot_id, company_id=company_id, as_of_date=as_of_date,
            scope=scope, currency=currency, purpose=purpose,
            exists=bool(authority.exists), is_current=bool(authority.is_current),
            validity=authority.validity, report_blocked=bool(authority.report_blocked),
            quarantined=bool(authority.quarantined))

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id, "company_id": self.company_id,
            "as_of_date": self.as_of_date, "scope": self.scope, "currency": self.currency,
            "purpose": self.purpose, "exists": self.exists, "is_current": self.is_current,
            "validity": self.validity, "report_blocked": self.report_blocked,
            "quarantined": self.quarantined, "authoritative": self.authoritative(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialSnapshotIdentity":
        if not isinstance(d, dict):
            raise FinancialArtifactError("FinancialSnapshotIdentity 必须是对象")
        return cls(
            snapshot_id=d["snapshot_id"], company_id=d["company_id"],
            as_of_date=d["as_of_date"], scope=d["scope"], currency=d["currency"],
            purpose=d["purpose"], exists=bool(d.get("exists")),
            is_current=bool(d.get("is_current")), validity=d.get("validity"),
            report_blocked=bool(d.get("report_blocked")),
            quarantined=bool(d.get("quarantined")))


# ---------------------------------------------------------------------------
# artifact
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinancialPackArtifact:
    """task-bound 的只读财务权威投影（内容是 FactPack 的确定性函数）。"""

    schema_version: str
    artifact_id: str
    task_id: str
    projection_id: str
    contract_version: str
    contract_fingerprint: str
    producer_kind: str
    fact_selection_rule_version: str
    projection_version: str
    snapshot: FinancialSnapshotIdentity
    periods: tuple[str, ...]
    statements_available: tuple[str, ...]
    period_note: dict
    facts: tuple[FinancialFactProjection, ...]
    selected_fact_ids: tuple[str, ...]
    excluded: tuple[ExcludedFinancialFact, ...]
    gaps: tuple[dict, ...]
    diagnostic_gaps: tuple[dict, ...]
    projection_notes: tuple[str, ...]
    content_fingerprint: str = ""
    _raw: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION:
            raise FinancialArtifactError(
                f"artifact.schema_version 必须为 {FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION!r}，"
                f"得到 {self.schema_version!r}")
        if not self.task_id or not self.projection_id:
            raise FinancialArtifactError("artifact.task_id / projection_id 必须非空")
        if not self.fact_selection_rule_version:
            raise FinancialArtifactError(
                "artifact.fact_selection_rule_version 必须非空：选材规则版本由**产出 FactPack 的"
                "选材代码**决定，本投影不得替它发明版本号")
        if self.producer_kind != FINANCIAL_ARTIFACT_PRODUCER_KIND:
            raise FinancialArtifactError(
                f"artifact.producer_kind 必须为 {FINANCIAL_ARTIFACT_PRODUCER_KIND!r}，"
                f"得到 {self.producer_kind!r}（不许用 Harness Pack 冒充财务主权威）")
        ids = tuple(f.fact_id for f in self.facts)
        if len(set(ids)) != len(ids):
            raise FinancialArtifactError("artifact.facts 含重复 fact_id")
        if tuple(self.selected_fact_ids) != ids:
            raise FinancialArtifactError(
                "artifact.selected_fact_ids 必须与 facts 的 fact_id 序列严格一致"
                "（声明被选中的事实不得与实际内容脱节）")
        # 同一条 fact 不得既「选中」又「排除」。
        overlap = set(ids) & {e.fact_id for e in self.excluded}
        if overlap:
            raise FinancialArtifactError(
                f"artifact 中同一条 fact 同时被选中与排除：{sorted(overlap)}")
        if any(f.citation.get("snapshot_id") != self.snapshot.snapshot_id for f in self.facts):
            raise FinancialArtifactError(
                "artifact 内存在 citation.snapshot_id 与本快照身份不符的事实")

    # -- 身份 / 指纹 ------------------------------------------------------

    def content_body(self) -> dict:
        """内容规范形：不含 artifact_id / content_fingerprint / replay 模式（后三者是身份或运行态）。"""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id, "projection_id": self.projection_id,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "producer_kind": self.producer_kind,
            "fact_selection_rule_version": self.fact_selection_rule_version,
            "projection_version": self.projection_version,
            "snapshot": self.snapshot.to_dict(),
            "periods": list(self.periods),
            "statements_available": list(self.statements_available),
            "period_note": dict(self.period_note),
            "facts": [f.to_dict() for f in self.facts],
            "selected_fact_ids": list(self.selected_fact_ids),
            "excluded": [e.to_dict() for e in self.excluded],
            "gaps": [dict(g) for g in self.gaps],
            "diagnostic_gaps": [dict(g) for g in self.diagnostic_gaps],
            "projection_notes": list(self.projection_notes),
        }

    def compute_content_fingerprint(self) -> str:
        return TS.sha256_canonical(self.content_body())

    def compute_artifact_id(self) -> str:
        return _ARTIFACT_ID_PREFIX + self.compute_content_fingerprint()[:24]

    def verify(self) -> None:
        """重算指纹与 artifact_id（fail-closed）。"""
        expected_fp = self.compute_content_fingerprint()
        if self.content_fingerprint != expected_fp:
            raise FinancialArtifactError(
                f"artifact.content_fingerprint 与内容不符：声明 {self.content_fingerprint!r}，"
                f"重算 {expected_fp!r}")
        expected_id = _ARTIFACT_ID_PREFIX + expected_fp[:24]
        if self.artifact_id != expected_id:
            raise FinancialArtifactError(
                f"artifact.artifact_id 与内容指纹不符：声明 {self.artifact_id!r}，"
                f"重算 {expected_id!r}")

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict:
        body = self.content_body()
        body["artifact_id"] = self.artifact_id
        body["content_fingerprint"] = self.content_fingerprint
        return body

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialPackArtifact":
        if not isinstance(d, dict):
            raise FinancialArtifactError("FinancialPackArtifact 必须是对象")
        snap = d.get("snapshot")
        if not isinstance(snap, dict):
            raise FinancialArtifactError("artifact.snapshot 必须是对象")
        return cls(
            schema_version=d["schema_version"], artifact_id=d.get("artifact_id", ""),
            task_id=d["task_id"], projection_id=d["projection_id"],
            contract_version=d["contract_version"],
            contract_fingerprint=d["contract_fingerprint"],
            producer_kind=d["producer_kind"],
            fact_selection_rule_version=d["fact_selection_rule_version"],
            projection_version=d.get("projection_version", FINANCIAL_PACK_PROJECTION_VERSION),
            snapshot=FinancialSnapshotIdentity.from_dict(snap),
            periods=tuple(d.get("periods") or ()),
            statements_available=tuple(d.get("statements_available") or ()),
            period_note=dict(d.get("period_note") or {}),
            facts=tuple(FinancialFactProjection.from_dict(x) for x in (d.get("facts") or ())),
            selected_fact_ids=tuple(d.get("selected_fact_ids") or ()),
            excluded=tuple(ExcludedFinancialFact.from_dict(x) for x in (d.get("excluded") or ())),
            gaps=tuple(dict(g) for g in (d.get("gaps") or ())),
            diagnostic_gaps=tuple(dict(g) for g in (d.get("diagnostic_gaps") or ())),
            projection_notes=tuple(d.get("projection_notes") or ()),
            content_fingerprint=d.get("content_fingerprint", ""))


# ---------------------------------------------------------------------------
# 构建（只读投影）
# ---------------------------------------------------------------------------

#: 上游选材阶段**没有** per-fact 记录的过滤（诚实声明，不为其编造 fact id/原因）。
_UPSTREAM_UNRECORDED_FILTER_NOTE = (
    "excluded 仅登记 FactPack 自身记录的缺口（gaps/diagnostic_gaps）。上游选材更早的过滤"
    "（currency/scope 不符、amount 为空、科目未纳入、期间不在本期范围）不留下 per-fact 记录，"
    "故本 artifact 不为其编造 fact id 或原因；那些事实从未进入 FactPack。")


def _refs_of(fact: Any) -> tuple[Any, ...]:
    """该事实声明的**完整**引用集（`citations`），逐条校验为合法 `CitationRef`。

    空 `citations` 不是「没有引用」：它表示这条事实的引用就是它自己的 `citation`（上游
    `FinancialFact` 的规则）。非空时必须每条都是 `CitationRef`——派生事实的引用集若混进一个
    映射/字符串，下游「引用逐条等于权威事实自己给出的引用」这条就无从核对。
    """
    refs = tuple(getattr(fact, "citations", ()) or ())
    for ref in refs:
        if not isinstance(ref, CitationRef):
            raise FinancialArtifactError(
                f"fact {getattr(fact, 'fact_id', '?')!r} 的 citations 含非 CitationRef 项"
                f"（{type(ref).__name__}）：引用集只能由权威引用组成")
    return refs


def _projection_of(fact: Any) -> FinancialFactProjection:
    value: Decimal | None = getattr(fact, "value", None)
    citation = getattr(fact, "citation", None)
    if not isinstance(citation, CitationRef):
        raise FinancialArtifactError(
            f"fact {getattr(fact, 'fact_id', '?')!r} 缺合法 CitationRef"
            f"（财务事实必须保留结构化引用，不得脱离权威链）")
    return FinancialFactProjection(
        fact_id=fact.fact_id, kind=fact.kind, label=fact.label, code=fact.code,
        period=fact.period,
        # Decimal → 规范字符串（精确往返）；None 保持 None，不用 0 冒充缺失值。
        value_text=None if value is None else str(value),
        display=fact.display, unit=fact.unit, status=fact.status,
        reason_code=fact.reason_code, note=fact.note,
        period_basis=getattr(fact, "period_basis", ""),
        period_label=getattr(fact, "period_label", ""),
        formula_version=getattr(fact, "formula_version", ""),
        # harness.schema.CitationRef 是纯 dataclass（无 to_dict）；用 asdict 无损转换，
        # 全部字段（含 snapshot_id / formula_id / formula_version / period）原样保留，不重造引用。
        citation=dataclasses.asdict(citation),
        # 完整引用集与派生血缘：逐条转换，缺字段的单引用事实留空（空 = 就是 citation 那一条）。
        # `citations` 里每一条都必须是合法 `CitationRef`——派生事实的引用集不得混入非引用对象。
        citations=tuple(dataclasses.asdict(c) for c in _refs_of(fact)),
        derived_from=tuple(str(x) for x in (getattr(fact, "derived_from", ()) or ())),
        input_periods=tuple(str(x) for x in (getattr(fact, "input_periods", ()) or ())),
        input_raw_texts=tuple(str(x) for x in (getattr(fact, "input_raw_texts", ()) or ())))


def _excluded_of(pack: Any) -> tuple[ExcludedFinancialFact, ...]:
    out: list[ExcludedFinancialFact] = []
    for required, group in ((True, pack.gaps), (False, pack.diagnostic_gaps)):
        for g in group:
            fact_id = g.get("fact_id")
            if not fact_id:
                raise FinancialArtifactError(
                    f"FactPack 缺口缺 fact_id，无法登记为 excluded：{g!r}")
            out.append(ExcludedFinancialFact(
                fact_id=fact_id, formula_id=g.get("formula_id"), label=g.get("label", ""),
                kind=g.get("kind", ""), period=g.get("period", ""),
                status=g.get("status", ""), reason_code=g.get("reason_code"),
                required=required))
    return tuple(out)


def build_financial_pack_artifact(pack: Any, *, task_id: str, projection_id: str,
                                  contract_version: str, contract_fingerprint: str,
                                  fact_selection_rule_version: str,
                                  authority: SP.SnapshotAuthority) -> FinancialPackArtifact:
    """把 `FinancialFactPack` + 快照健康事实投影成只读 artifact。

    `fact_selection_rule_version` 必须由**调用方**给出（产出该 FactPack 的选材代码的版本）；
    本投影不得替别人的选材算法发明版本号。`authority` 必须是本次**独立查询**得到的
    `SnapshotAuthority`（不得由调用方手填布尔）。
    """
    for name, value in (("task_id", task_id), ("projection_id", projection_id),
                        ("contract_version", contract_version),
                        ("contract_fingerprint", contract_fingerprint),
                        ("fact_selection_rule_version", fact_selection_rule_version)):
        if not isinstance(value, str) or value == "":
            raise FinancialArtifactError(f"build_financial_pack_artifact 的 {name} 必须为非空字符串")
    if not isinstance(authority, SP.SnapshotAuthority):
        raise FinancialArtifactError(
            "authority 必须为独立查询得到的 harness.structured_provenance.SnapshotAuthority"
            "（不接受调用方自报布尔）")
    if pack is None:
        raise FinancialArtifactError("build_financial_pack_artifact 需要 FinancialFactPack")

    notes = [_UPSTREAM_UNRECORDED_FILTER_NOTE]
    if not authority.authoritative():
        # 不 fail-closed 拒绝：投影**必须**能如实记录一份不健康/非 current 的快照，
        # 否则缺口就被隐藏了。但 artifact 里必须显式写明，绝不静默当权威。
        notes.append(
            "快照当前**非权威**（exists/is_current/validity/report_blocked/quarantined 之一不满足）："
            "本 artifact 只作如实记录，不得作为正文金额权威。")

    facts = tuple(_projection_of(f) for f in pack.facts)
    artifact = FinancialPackArtifact(
        schema_version=FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION, artifact_id="",
        task_id=task_id, projection_id=projection_id,
        contract_version=contract_version, contract_fingerprint=contract_fingerprint,
        producer_kind=FINANCIAL_ARTIFACT_PRODUCER_KIND,
        fact_selection_rule_version=fact_selection_rule_version,
        projection_version=FINANCIAL_PACK_PROJECTION_VERSION,
        snapshot=FinancialSnapshotIdentity.from_authority(
            authority, company_id=pack.company_id, as_of_date=pack.as_of_date,
            scope=pack.scope, currency=pack.currency, purpose=pack.purpose,
            snapshot_id=pack.snapshot_id),
        periods=tuple(pack.periods), statements_available=tuple(pack.statements_available),
        period_note=dict(pack.period_note), facts=facts,
        selected_fact_ids=tuple(f.fact_id for f in facts),
        excluded=_excluded_of(pack), gaps=tuple(dict(g) for g in pack.gaps),
        diagnostic_gaps=tuple(dict(g) for g in pack.diagnostic_gaps),
        projection_notes=tuple(notes))
    fingerprint = artifact.compute_content_fingerprint()
    artifact_id = _ARTIFACT_ID_PREFIX + fingerprint[:24]
    final = _replace(artifact, content_fingerprint=fingerprint, artifact_id=artifact_id)
    final.verify()
    return final


def _replace(obj: Any, **changes: Any) -> Any:
    return dataclasses.replace(obj, **changes)


# ---------------------------------------------------------------------------
# §十一 财务附注的**独立**输入状态
#
# 附注数字来自 Evidence 背书（文档结构层），与 financial_v2 计算出的财务主权威**来源不同**。
# 因此这里给出两个**互斥**的类型：要么拿到一组已核验的附注事实，要么拿到一个 task-bound 的
# 附注缺口。两者都绝不并入 `FinancialPackArtifact`——合并会让来源权威变模糊。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceNoteFact:
    """一条已核验的附注事实：必须精确落在**一个** OutlineSpan 上（不是整块 Evidence）。

    `evidence_id` + `evidence_char_range` + `span_id` 三个定位器同时给出：前者是来源锚点，
    后两者把范围收窄到具体 span，因而「同一 Evidence 多 span」在此不可能含糊通过。
    """

    fact_id: str
    label: str
    value_text: str | None
    display: str
    evidence_id: str
    evidence_char_range: tuple[int, int]
    span_id: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.fact_id or not self.evidence_id or not self.span_id:
            raise FinancialArtifactError(
                "EvidenceNoteFact 的 fact_id / evidence_id / span_id 必须非空")
        start, end = self.evidence_char_range
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)
                or start < 0 or end <= start):
            raise FinancialArtifactError(
                f"EvidenceNoteFact {self.fact_id!r} 的 evidence_char_range 必须为 "
                f"0 <= start < end 的整数区间，得到 {self.evidence_char_range!r}")
        if self.value_text is not None:
            try:
                Decimal(self.value_text)
            except InvalidOperation as e:
                raise FinancialArtifactError(
                    f"附注事实 {self.fact_id!r} 的 value_text 不是合法 Decimal 规范字符串：{e}") from e

    def span_ref(self) -> tuple[str, int, int]:
        return (self.evidence_id, self.evidence_char_range[0], self.evidence_char_range[1])

    def to_dict(self) -> dict:
        return {"fact_id": self.fact_id, "label": self.label,
                "value_text": self.value_text, "display": self.display,
                "evidence_id": self.evidence_id,
                "evidence_char_range": list(self.evidence_char_range),
                "span_id": self.span_id, "note": self.note}

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceNoteFact":
        if not isinstance(d, dict):
            raise FinancialArtifactError("EvidenceNoteFact 必须是对象")
        rng = d.get("evidence_char_range") or ()
        return cls(fact_id=d["fact_id"], label=d.get("label", ""),
                   value_text=d.get("value_text"), display=d.get("display", ""),
                   evidence_id=d["evidence_id"],
                   evidence_char_range=(rng[0], rng[1]) if len(rng) == 2 else (0, 0),
                   span_id=d["span_id"], note=d.get("note", ""))


@dataclass(frozen=True)
class ValidatedEvidenceNoteFactSet:
    """task-bound 的**已核验**附注事实集合（与 `FinancialPackArtifact` 分离）。"""

    schema_version: str
    task_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    facts: tuple[EvidenceNoteFact, ...]
    searched_scope: tuple[str, ...]
    searched_need_ids: tuple[str, ...]
    validation_rule_version: str

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_NOTE_SCHEMA_VERSION:
            raise FinancialArtifactError(
                f"ValidatedEvidenceNoteFactSet.schema_version 必须为 "
                f"{EVIDENCE_NOTE_SCHEMA_VERSION!r}")
        if not self.facts:
            raise FinancialArtifactError(
                "ValidatedEvidenceNoteFactSet 不得为空：拿不到附注时应改用 EvidenceNoteGap，"
                "空集合会被误读为「查过且确无附注」")
        for name in ("task_id", "company_id", "contract_version",
                     "contract_fingerprint", "validation_rule_version"):
            if not getattr(self, name):
                raise FinancialArtifactError(f"ValidatedEvidenceNoteFactSet.{name} 必须非空")
        ids = tuple(f.fact_id for f in self.facts)
        if len(set(ids)) != len(ids):
            raise FinancialArtifactError("ValidatedEvidenceNoteFactSet 含重复 fact_id")

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "task_id": self.task_id,
                "company_id": self.company_id, "report_as_of": self.report_as_of,
                "contract_version": self.contract_version,
                "contract_fingerprint": self.contract_fingerprint,
                "facts": [f.to_dict() for f in self.facts],
                "searched_scope": list(self.searched_scope),
                "searched_need_ids": list(self.searched_need_ids),
                "validation_rule_version": self.validation_rule_version}


@dataclass(frozen=True)
class EvidenceNoteGap:
    """task-bound 的财务附注**输入缺口**（独立于财务 FactPack 的普通缺口）。

    「独立」是硬约束：普通财务缺口说的是"某个指标没算出来"，本缺口说的是"本轮未取得附注
    输入"。两者混用会让读者把"模型没实现附注抽取"误读成"公司没披露附注"。
    """

    schema_version: str
    task_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    searched_scope: tuple[str, ...]
    searched_need_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    detail: str

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_NOTE_SCHEMA_VERSION:
            raise FinancialArtifactError(
                f"EvidenceNoteGap.schema_version 必须为 {EVIDENCE_NOTE_SCHEMA_VERSION!r}")
        for name in ("task_id", "company_id", "contract_version", "contract_fingerprint",
                     "detail"):
            if not getattr(self, name):
                raise FinancialArtifactError(f"EvidenceNoteGap.{name} 必须非空")
        if not self.reason_codes:
            raise FinancialArtifactError("EvidenceNoteGap.reason_codes 必须非空（缺原因=不可审）")
        unknown = [r for r in self.reason_codes if r not in EVIDENCE_NOTE_GAP_REASONS]
        if unknown:
            raise FinancialArtifactError(
                f"EvidenceNoteGap 含未登记原因码 {unknown}（允许 {EVIDENCE_NOTE_GAP_REASONS}）")
        # 「本批未实现抽取」是唯一允许"没查过任何范围"的原因：其余原因都意味着真的查过。
        not_implemented = "note_extraction_not_implemented" in self.reason_codes
        if not not_implemented and (not self.searched_scope or not self.searched_need_ids):
            raise FinancialArtifactError(
                "EvidenceNoteGap 声称已查过（非 note_extraction_not_implemented）时必须绑定"
                "真实 searched_scope 与 searched_need_ids；否则就是无据的「未取得」")
        for word in EVIDENCE_NOTE_GAP_FORBIDDEN_WORDING:
            if word in self.detail:
                raise FinancialArtifactError(
                    f"EvidenceNoteGap.detail 不得出现 {word!r}：我们只能声明"
                    f"「本轮已纳入范围内未取得」，不得替公司声明「未披露」")

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "task_id": self.task_id,
                "company_id": self.company_id, "report_as_of": self.report_as_of,
                "contract_version": self.contract_version,
                "contract_fingerprint": self.contract_fingerprint,
                "searched_scope": list(self.searched_scope),
                "searched_need_ids": list(self.searched_need_ids),
                "reason_codes": list(self.reason_codes), "detail": self.detail}


def validate_evidence_note_fact_set(
        note_set: ValidatedEvidenceNoteFactSet,
        admissible_span_refs: Sequence[tuple[str, int, int]]) -> tuple[str, ...]:
    """逐条核验附注事实确实落在**已准入**的 span 上；返回错误列表（空=通过）。

    `admissible_span_refs` 必须由调用方从真实的 span/材料解析器给出（不是自报）。任何一条
    附注事实的 (evidence_id, start, end) 不在其中 → 该事实不成立，fail-closed。
    """
    admitted = {(e, s, t) for (e, s, t) in admissible_span_refs}
    errors: list[str] = []
    for fact in note_set.facts:
        if fact.span_ref() not in admitted:
            errors.append(
                f"附注事实 {fact.fact_id!r} 的 span 定位 {fact.span_ref()} 不在已准入 span 集合内")
    return tuple(errors)


# ---------------------------------------------------------------------------
# replay：独立复核（live）或冻结 hash 复核（pure offline）
# ---------------------------------------------------------------------------

class FinancialSnapshotAuthoritySource(Protocol):
    """快照权威的独立来源（composition root 注入；artifact 自身不得自证）。

    两个方法都是同一件事：`authority_for` 按快照身份查（artifact 是**先**用权威建出来的，
    构建阶段必须能查），`query` 按 artifact 查（复核阶段用）。独立来源两处都应答得上。
    """

    def authority_for(self, target: Any) -> SP.SnapshotAuthority: ...

    def query(self, artifact: FinancialPackArtifact) -> SP.SnapshotAuthority: ...


@dataclass(frozen=True)
class ArtifactReplayReport:
    """replay 结论。`snapshot_authority_reverified=False` 时必须 `mode="offline_replay"`。"""

    mode: str
    artifact_id: str
    content_fingerprint_ok: bool
    snapshot_authority_reverified: bool
    snapshot_authoritative: bool | None
    identity_consistent: bool
    facts_unchanged: bool
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ARTIFACT_REPLAY_MODES:
            raise FinancialArtifactError(f"未知 replay 模式 {self.mode!r}（允许 {ARTIFACT_REPLAY_MODES}）")
        if self.mode == "offline_replay" and self.snapshot_authority_reverified:
            raise FinancialArtifactError(
                "offline_replay 不得声称已复核快照权威（那是 live 模式的结论）")
        if self.mode == "live" and not self.snapshot_authority_reverified:
            raise FinancialArtifactError("live 模式必须真的复核过快照权威")

    def passed(self) -> bool:
        if not (self.content_fingerprint_ok and self.identity_consistent and self.facts_unchanged):
            return False
        if self.mode == "live":
            return self.snapshot_authoritative is True
        return True  # offline 只保证冻结内容自洽，不声称快照仍然健康

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "artifact_id": self.artifact_id,
            "content_fingerprint_ok": self.content_fingerprint_ok,
            "snapshot_authority_reverified": self.snapshot_authority_reverified,
            "snapshot_authoritative": self.snapshot_authoritative,
            "identity_consistent": self.identity_consistent,
            "facts_unchanged": self.facts_unchanged,
            "passed": self.passed(), "notes": list(self.notes),
        }


def verify_financial_pack_artifact(
        artifact: FinancialPackArtifact, *,
        authority_source: FinancialSnapshotAuthoritySource | None = None) -> ArtifactReplayReport:
    """复核 artifact。

    - 给了 `authority_source` → **live**：重新独立查询快照，要求仍然 current+健康，
      且 artifact 记录的快照身份与实时查询逐字段一致；否则 fail-closed。
    - 没给 → **offline_replay**：只校验冻结内容（指纹 / 身份自洽 / 事实与选中清单一致），
      并在报告里明确 `snapshot_authority_reverified=False`，绝不暗示快照仍然有效。
    """
    try:
        artifact.verify()
        fingerprint_ok = True
        failure: str | None = None
    except FinancialArtifactError as e:
        fingerprint_ok = False
        failure = str(e)

    facts_unchanged = (tuple(f.fact_id for f in artifact.facts)
                       == tuple(artifact.selected_fact_ids))
    identity_consistent = (artifact.snapshot.snapshot_id != ""
                           and artifact.task_id != ""
                           and artifact.projection_id != "")

    if authority_source is None:
        return ArtifactReplayReport(
            mode="offline_replay", artifact_id=artifact.artifact_id,
            content_fingerprint_ok=fingerprint_ok,
            snapshot_authority_reverified=False, snapshot_authoritative=None,
            identity_consistent=identity_consistent, facts_unchanged=facts_unchanged,
            notes=(("指纹校验失败：" + failure,) if failure else ())
            + ("纯离线复算：只校验冻结内容，**未**复核快照 current/health；"
               "不得据此声称财务权威仍有效",))

    live = authority_source.query(artifact)
    if not isinstance(live, SP.SnapshotAuthority):
        raise FinancialArtifactError(
            "authority_source.query 必须返回 SnapshotAuthority（不接受自报布尔/字典）")
    fresh = FinancialSnapshotIdentity(
        snapshot_id=artifact.snapshot.snapshot_id,
        company_id=artifact.snapshot.company_id, as_of_date=artifact.snapshot.as_of_date,
        scope=artifact.snapshot.scope, currency=artifact.snapshot.currency,
        purpose=artifact.snapshot.purpose, exists=bool(live.exists),
        is_current=bool(live.is_current), validity=live.validity,
        report_blocked=bool(live.report_blocked), quarantined=bool(live.quarantined))
    notes: list[str] = []
    identity_fields = ("exists", "is_current", "validity", "report_blocked", "quarantined")
    drift = [name for name in identity_fields
             if getattr(fresh, name) != getattr(artifact.snapshot, name)]
    if drift:
        notes.append(
            f"快照健康状态自构建以来已漂移：{drift}；artifact 记录={artifact.snapshot.to_dict()}，"
            f"实时={fresh.to_dict()}")
    passed_identity = identity_consistent and not drift
    return ArtifactReplayReport(
        mode="live", artifact_id=artifact.artifact_id,
        content_fingerprint_ok=fingerprint_ok,
        snapshot_authority_reverified=True,
        snapshot_authoritative=fresh.authoritative(),
        identity_consistent=passed_identity, facts_unchanged=facts_unchanged,
        notes=tuple(notes)
        + (("指纹校验失败：" + failure,) if failure else ()))


# ---------------------------------------------------------------------------
# 负向约束（可测试）
# ---------------------------------------------------------------------------

def assert_no_table_conversion(module_path: str | Path | None = None) -> None:
    """断言本模块**从不**把 TableObject 数字转成 FinancialFact（源码级负向证明）。

    检查两件事：不得引用文档结构层（TableObject 所在层）、不得出现把表格数值投影成
    FinancialFact 的调用。这是「本批不许越界」的显式断言，供测试与自检调用。
    """
    import ast
    path = Path(module_path) if module_path else Path(__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_modules = ("document_structure", "table_object", "TableObject")
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in ("document_structure",):
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in ("document_structure",):
                hits.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Attribute) and node.attr in forbidden_modules:
            hits.append(f"attribute {node.attr}")
        elif isinstance(node, ast.Name) and node.id in forbidden_modules:
            hits.append(f"name {node.id}")
    if hits:
        raise FinancialArtifactError(
            f"{path.name} 引用了文档结构/TableObject 层：{hits}；"
            f"财务 artifact 不得从 TableObject 造 FinancialFact（§5.10）")


# ---------------------------------------------------------------------------
# 读适配器（composition root 用；只读，不 init/不迁移/不写财务库）
# ---------------------------------------------------------------------------

#: 只读权威查询层的版本（进入审计与停止报告：证明读的是哪一套只读口径）。
FINANCIAL_AUTHORITY_READONLY_QUERY_VERSION = "fin-ro-auth-1"


def _readonly_conn(fin_db: str | Path) -> sqlite3.Connection:
    """**连接级强制只读**打开既有财务库：URI `mode=ro` + `PRAGMA query_only=ON`。

    - `mode=ro` 由 SQLite 自身保证打开即只读，且文件不存在时**报错而非建库**（
      因此绝不创建父目录、库文件、WAL 或 SHM，也绝不跑 migration）；
    - `query_only=ON` 让任何写语句在这条连接上直接失败——写保护不靠调用方自觉，
      也不靠"事后比对 hash 没变"。
    """
    p = Path(fin_db).expanduser().resolve()
    if not p.is_file():
        raise FinancialArtifactError(
            f"financial_v2 库不存在（只读打开，绝不创建）: {p}")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _readonly_snapshot_authority(fin_db: str | Path, target: Any) -> SP.SnapshotAuthority:
    """用只读 SQL **独立**取得并重算快照权威五项（**绝不**由 artifact 自证）。

    逐项来源与既有只读查询层同一口径，但**不依赖** `financial_v2.store` 的模块级绑定：

    - `exists`               = `financial_snapshot` 里是否有该 snapshot_id 行；
    - `validity`             = `snapshot_validity` 里该快照最后一条事件的 status；
    - `is_current`           = `current_snapshot` 指针指向它，且它不是 stale/superseded
                               （与 `financial_v2.snapshots.current_snapshot` 同一语义）；
    - `report_blocked`       = `financial_snapshot.report_blocked` 布尔列；
    - `quarantined`          = `quarantine` 表里是否有 (financial_snapshot, snapshot_id)。

    本函数不调用 `fstore.init_db()` / `fstore._get_conn()`，也不读写 `fstore._db_path`。
    """
    conn = _readonly_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT report_blocked FROM financial_snapshot WHERE snapshot_id=?",
            (target.snapshot_id,)).fetchone()
        exists = row is not None
        validity = None
        if exists:
            vrow = conn.execute(
                "SELECT status FROM snapshot_validity WHERE snapshot_id=? "
                "ORDER BY event_at DESC, rowid DESC LIMIT 1",
                (target.snapshot_id,)).fetchone()
            validity = vrow["status"] if vrow is not None else None
        pointer = conn.execute(
            "SELECT c.snapshot_id AS sid FROM current_snapshot c "
            "JOIN financial_snapshot s ON s.snapshot_id = c.snapshot_id "
            "WHERE c.company_id=? AND c.scope=? AND c.currency=? AND c.as_of_date=? "
            "AND c.purpose=?",
            (target.company_id, target.scope, target.currency,
             target.as_of_date, target.purpose)).fetchone()
        current_id = pointer["sid"] if pointer is not None else None
        if current_id == target.snapshot_id and validity in ("stale", "superseded"):
            # 与 `snapshots.current_snapshot` 同一语义：指针指向 stale/superseded 不算 current。
            current_id = None
        quarantined = False
        if exists:
            quarantined = conn.execute(
                "SELECT 1 FROM quarantine WHERE object_type='financial_snapshot' "
                "AND object_id=? LIMIT 1", (target.snapshot_id,)).fetchone() is not None
        return SP.SnapshotAuthority(
            exists=exists,
            is_current=(target.snapshot_id == current_id),
            validity=validity,
            report_blocked=bool(row["report_blocked"]) if exists else False,
            quarantined=quarantined)
    finally:
        conn.close()


@dataclass(frozen=True)
class FinancialDbAuthoritySource:
    """从 financial_v2 库**只读**独立查询快照权威。

    刻意**不**调用 `fstore.init_db()`：那会执行 DDL。本模块是只读投影，绝不改财务库；
    库必须由 composition root 事先准备好。
    """

    fin_db: str

    def authority_for(self, target: Any) -> SP.SnapshotAuthority:
        """按快照身份独立查询权威。

        `target` 只要有 `snapshot_id/company_id/scope/currency/as_of_date/purpose` 即可
        （`FinancialFactPack` 与 `FinancialPackArtifact` 都满足）——artifact 是**先**用
        authority 构建出来的，所以这里不能只接受 artifact，否则构建阶段就无法独立查询。
        """
        return _readonly_snapshot_authority(self.fin_db, target)

    def query(self, artifact: FinancialPackArtifact) -> SP.SnapshotAuthority:
        return self.authority_for(artifact.snapshot)

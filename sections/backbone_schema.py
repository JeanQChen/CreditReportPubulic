"""M930-1：Demo Backbone 的状态、报告版本与治理快照类型（唯一所有者）。

职责边界（M930-1 任务书 §五 / §八）：

- 本模块**只**拥有下列 11 个公共 wire 类型：
  ``RunProgressSnapshot`` / ``ReportVersionIdentity`` / ``ProcessCompletionStatus`` /
  ``PreviewAvailabilityStatus`` / ``SystemAssuranceStatus`` / ``HumanAcceptanceStatus`` /
  ``ReportStatusSnapshot`` / ``FormalPhaseClosureSnapshot`` / ``DesignSurfaceRecord`` /
  ``DesignSurfaceMatrix`` / ``DemoBackboneRunManifest``。
- 本模块**不重定义**任何 scope / projection / run-identity 类型；需要时只
  **引用** ``planning.demo_scope_schema`` 的类型与其对象 ID（``planning → sections``
  反向依赖被禁止，``sections → planning`` 是允许方向）。
- 本批**不实现** Reviewer、Controller、Narrative、Writer：本模块只有类型与纯函数，
  不调用 LLM、不读库、不写文件。

状态分离规则（结构上保证，不靠约定）：

1. 报告形成**前**只允许 run-bound 的 ``RunProgressSnapshot``（它没有 report_version 字段，
   无法伪造版本）；
2. 不得为了凑满四个状态而编造空报告版本：``ReportStatusSnapshot`` 必须携带一个真实的
   ``ReportVersionIdentity``；
3. 报告形成后四个状态共享同一个 report version（``ReportStatusSnapshot`` 只持有
   一个 ``ReportVersionIdentity``）；
4. ``HumanAcceptanceStatus`` 默认 ``NOT_REVIEWED``；wire 完整支持未来**独立人工流程**
   写入的 ``ACCEPTED`` / ``REJECTED``，但本批不提供任何把它改成非默认值的业务 API
   （没有任何按其它三轴自动推导人工状态的入口）；
5. ``FormalPhaseClosureSnapshot`` 只读、不进 report version、不作 Assurance 输入、
   不由 runtime 派生或修改（``derived_by_runtime`` 必须为 False）；其来源文档必须携带
   规范相对路径 + 64 位 SHA256，且指纹覆盖这些 hash；
6. 四个状态是四个独立类型，**不**互相自动映射；
7. ``preview available`` ≠ ``system assurance passed``；
8. ``system assurance passed`` ≠ human acceptance；
9. report version 排除 run/时间/路径、ReviewIssue、AssuranceResult、人工状态、
   正式阶段关闭与 UI 字段（封闭白名单 + 反例测试逐项断言）；
10. 正文/Pack/关键版本变化必须改变 report version（逐类内容依赖各有反例）；
11. 操作性 run identity 变化**不得**改变 report version。

顶层身份 DAG（任务书 P1-3）：``DemoBackboneRunManifest`` 在构造、反序列化与 artifact
严格读回三条路径上都核验 ``run_identity`` 与 ``scope_manifest`` 的 run_id / attempt /
job_id / scope_input_fingerprint，以及与 report version 的 company / report_as_of /
plan_id / projection_id / task / Pack 关联 —— 不是字符串前缀比较。

CLI::

    python -m sections.backbone_schema
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from harness.topic_schema import canonical_json, sha256_canonical
from planning import demo_scope_schema as DSS

# ---------------------------------------------------------------------------
# 版本与封闭词表
# ---------------------------------------------------------------------------

BACKBONE_SCHEMA_VERSION = "demo-backbone-v1"
RUN_PROGRESS_SCHEMA_VERSION = "demo-run-progress-v1"
#: M930-3A（P16）：Section 侧 identity 集需要完整表达 Claim Binding Gate 与 Claim
#: entailment 的规则版本，因此指纹白名单与 schema marker 同期前进。
#: M930-3 任务二（P17）：报告身份此前**漏绑**两类内容依赖——各节 `SectionDraft` 身份，
#: 以及"组装报告排除自身版本字段后的规范载荷"（只改 Draft ID / 只改非 Markdown 载荷
#: 都曾不改变 `report_version`）。v3 白名单把两者补齐，读回时独立重算。
#: v1 / v2 身份对象**只读**——不得升级为 v3，也不得用 v3 白名单重算它们的指纹。
REPORT_VERSION_SCHEMA_VERSION = "demo-report-version-v3"
LEGACY_REPORT_VERSION_SCHEMA_VERSIONS = ("demo-report-version-v1",
                                         "demo-report-version-v2")
REPORT_STATUS_SCHEMA_VERSION = "demo-report-status-v1"
FORMAL_CLOSURE_SCHEMA_VERSION = "demo-formal-closure-v1"
DESIGN_SURFACE_SCHEMA_VERSION = "demo-design-surface-v1"

#: 流程是否完成（与预览、系统审核、人工状态彼此独立）。
PROCESS_COMPLETION_STATUSES = ("NOT_STARTED", "RUNNING", "COMPLETED", "FAILED", "BLOCKED")
#: 预览是否可用。`AVAILABLE` 不蕴含系统审核通过；`PARTIAL` 表示只有部分内容可预览。
PREVIEW_AVAILABILITY_STATUSES = ("UNAVAILABLE", "PARTIAL", "AVAILABLE")
#: 系统 Assurance 结果。`PASSED` 不蕴含人工确认；`BLOCKED` 表示存在阻断问题而非普通失败。
SYSTEM_ASSURANCE_STATUSES = ("NOT_RUN", "PASSED", "FAILED", "BLOCKED")
#: 人工最终确认状态词的**完整**封闭词表（wire 必须能读写未来独立人工流程的结论）。
#: 本批不提供任何产生非默认值的业务 API；默认值见 ``HUMAN_ACCEPTANCE_DEFAULT``。
HUMAN_ACCEPTANCE_STATUSES = ("NOT_REVIEWED", "ACCEPTED", "REJECTED")
#: 人工状态的唯一默认值（本批没有任何自动产生其它取值的前置条件）。
HUMAN_ACCEPTANCE_DEFAULT = "NOT_REVIEWED"
#: 四个嵌套状态类型的 wire 键（封闭）：只有这两个键合法，且 ``detail`` 可缺省。
NESTED_STATUS_WIRE_KEYS = frozenset({"status", "detail"})
NESTED_STATUS_OPTIONAL_KEYS = frozenset({"detail"})
#: 正式阶段关闭状态（只读治理快照）。
FORMAL_PHASE_CLOSURE_STATUSES = ("open", "closed", "blocked")

#: 三条独立完成轴（里程碑 §3）。与三条轴**不同名**，避免把设计面覆盖当成正式关闭。
DESIGN_AXES = ("design_surface_coverage", "demo_content_coverage", "formal_phase_closure")
DESIGN_SURFACE_STATUSES = ("demonstrated", "partial", "gap", "not_in_scope", "unavailable")
DESIGN_SURFACE_EVIDENCE_KINDS = ("artifact", "status", "gap", "none")

#: 设计面 ID 封闭词表（M930-0 实施计划 §3 轴 / §6 能力面 / §9 面板推导）。
DESIGN_SURFACE_IDS = (
    "ds.input_identity",
    "ds.scope_partition",
    "ds.plan_task_projection",
    "ds.run_identity",
    "ds.tree_material",
    "ds.table_capability",
    "ds.financial_authority",
    "ds.note_fact",
    "ds.external_funnel",
    "ds.event_negative",
    "ds.local_narrative",
    "ds.gap_visibility",
    "ds.claim_citation",
    "ds.independent_review",
    "ds.deterministic_assurance",
    "ds.state_separation",
    "ds.artifact_integrity",
    "ds.run_manifest_versions",
)

# ---------------------------------------------------------------------------
# report version 指纹白名单（闭集合）
# ---------------------------------------------------------------------------

#: 进入 `ReportVersionIdentity` 内容指纹的字段（封闭白名单；逐项对应实施计划 §4.6）。
#:
#: 每类内容依赖都是**显式 typed 字段**，不靠前缀或字符串包含关系间接表达：
#:   * scope 身份：``profile_fingerprint`` / ``scope_input_fingerprint`` / ``projection_id``；
#:   * 业务身份：``plan_id`` / ``job_id`` / ``company_id`` / ``report_as_of``；
#:   * 选中任务：``selected_task_ids``；
#:   * 材料包：``topic_pack_ids``；
#:   * 财务权威：``financial_fact_pack_artifact_id``；
#:   * 四类冻结资产：contract / source_policy / writing_spec / presentation_profile
#:     的 fingerprint，加上它们的聚合 ``dependency_fingerprint``；
#:   * 正文：``body_fingerprint``；
#:   * 各节 `SectionDraft` 身份：``section_draft_ids``（计划 §4.6「纳入各节 Draft 身份」）；
#:   * 组装报告的规范载荷：``assembled_payload_fingerprint``（排除 `report_version` /
#:     `report_id` / `version_identity` / `generated_at` 后逐字段重算，见
#:     `sections.narrative_schema.ASSEMBLED_PAYLOAD_INCLUDED_FIELDS`）；
#:   * 工件 schema 版本：narrative / claim / table / writer / assembler；
#:   * 判定链规则版本：``claim_binding_gate_version`` / ``claim_entailment_rules_version``；
#:   * 生成策略版本：``prompt_version`` / ``model_policy_id``。
REPORT_VERSION_FINGERPRINT_FIELDS = (
    "profile_fingerprint",
    "scope_input_fingerprint",
    "projection_id",
    "plan_id",
    "job_id",
    "company_id",
    "report_as_of",
    "selected_task_ids",
    "topic_pack_ids",
    "financial_fact_pack_artifact_id",
    "contract_fingerprint",
    "source_policy_fingerprint",
    "writing_spec_fingerprint",
    "presentation_profile_fingerprint",
    "dependency_fingerprint",
    "body_fingerprint",
    "section_draft_ids",
    "assembled_payload_fingerprint",
    "narrative_schema_version",
    "claim_schema_version",
    "table_schema_version",
    "writer_schema_version",
    "assembler_schema_version",
    "claim_binding_gate_version",
    "claim_entailment_rules_version",
    "prompt_version",
    "model_policy_id",
)

#: report version 中必须为 64 位 sha256 hex 的字段。
REPORT_VERSION_SHA256_FIELDS = (
    "profile_fingerprint",
    "scope_input_fingerprint",
    "contract_fingerprint",
    "source_policy_fingerprint",
    "writing_spec_fingerprint",
    "presentation_profile_fingerprint",
    "dependency_fingerprint",
    "body_fingerprint",
    "assembled_payload_fingerprint",
)

#: report version 中必须为非空字符串的字段。
REPORT_VERSION_TEXT_FIELDS = (
    "projection_id",
    "plan_id",
    "job_id",
    "company_id",
    "report_as_of",
    "narrative_schema_version",
    "claim_schema_version",
    "table_schema_version",
    "writer_schema_version",
    "assembler_schema_version",
    "claim_binding_gate_version",
    "claim_entailment_rules_version",
    "prompt_version",
    "model_policy_id",
)

#: **绝不**允许进入 report version 的字段（反例测试逐项断言）。
REPORT_VERSION_FORBIDDEN_FIELDS = (
    "run_id",
    "attempt",
    "started_at",
    "generated_at",
    "created_at",
    "updated_at",
    "results_root",
    "results_dir",
    "run_dir",
    "results_path",
    "report_path",
    "artifact_root_id",
    "manifest_id",
    "review_issue_ids",
    "review_issues",
    "review_attestation",
    "assurance_result",
    "assurance_result_id",
    "assurance_status",
    "human_acceptance_status",
    "human_status",
    "formal_phase_closure",
    "formal_closure",
    "ui_state",
    "ui_panel_state",
)

#: RunManifest 指纹排除的自身身份/运行时间字段。
RUN_MANIFEST_FINGERPRINT_EXCLUDE = ("manifest_id", "manifest_fingerprint", "created_at")

#: `FormalPhaseClosureSnapshot` 指纹排除的自身身份字段。
FORMAL_CLOSURE_FINGERPRINT_EXCLUDE = ("snapshot_id", "snapshot_fingerprint")

#: 一个治理阶段的记录键（封闭；`note` 可缺省）。
FORMAL_PHASE_KEYS = ("phase_id", "status", "source_document", "recorded_at", "note")
FORMAL_PHASE_REQUIRED_KEYS = ("phase_id", "status", "source_document", "recorded_at")

#: 一条来源治理文档记录的键（封闭）：规范仓库相对路径 + 64 位 SHA256 内容哈希。
#: 只有路径不足以绑定"当时读到的就是这份文档"，必须同时携带内容哈希。
FORMAL_SOURCE_DOCUMENT_KEYS = ("path", "sha256")


def _assert_allowlists_disjoint() -> None:
    """白名单与禁入名单必须不相交（模块导入即校验，杜绝静默泄漏）。"""
    leak = sorted(set(REPORT_VERSION_FINGERPRINT_FIELDS) & set(REPORT_VERSION_FORBIDDEN_FIELDS))
    if leak:
        raise AssertionError(f"report version 白名单含被禁字段: {leak}")
    if len(set(DESIGN_SURFACE_IDS)) != len(DESIGN_SURFACE_IDS):
        raise AssertionError("DESIGN_SURFACE_IDS 含重复 ID")
    if len(set(REPORT_VERSION_FINGERPRINT_FIELDS)) != len(REPORT_VERSION_FINGERPRINT_FIELDS):
        raise AssertionError("report version 白名单含重复字段")
    # 白名单的每一类依赖都必须被某个具体校验分组覆盖，防止新增字段悄悄绕过校验。
    typed = (set(REPORT_VERSION_SHA256_FIELDS) | set(REPORT_VERSION_TEXT_FIELDS)
             | {"selected_task_ids", "topic_pack_ids", "section_draft_ids",
                "financial_fact_pack_artifact_id"})
    uncovered = sorted(set(REPORT_VERSION_FINGERPRINT_FIELDS) - typed)
    if uncovered:
        raise AssertionError(f"report version 字段缺显式校验分组: {uncovered}")
    if typed - set(REPORT_VERSION_FINGERPRINT_FIELDS):
        raise AssertionError("report version 校验分组含白名单外字段")


_assert_allowlists_disjoint()


# ---------------------------------------------------------------------------
# 严格解码辅助（沿用仓库既有 idiom；不新建第二套序列化框架）
# ---------------------------------------------------------------------------

class BackboneSchemaError(ValueError):
    """Demo Backbone wire 校验失败（strict decode / fail-closed）。"""


def _err(msg: str) -> None:
    raise BackboneSchemaError(msg)


def _reject_unknown(d: Any, allowed: set[str], typename: str,
                    *, optional: frozenset[str] = frozenset()) -> dict:
    """闭合解码的**唯一**规则：先挡未知字段，再要求 ``allowed - optional`` 必须存在。

    ``optional`` 默认空集，因此既有调用点的语义逐字节不变；只有明确声明"键合法但可
    缺省"的类型（四个嵌套状态的 ``detail``）才传入它。
    """
    if not isinstance(d, dict):
        _err(f"{typename} 必须为 dict，得到 {type(d).__name__}")
    extra = sorted(set(d) - allowed)
    if extra:
        _err(f"{typename} 含未知字段: {extra}")
    missing = sorted(k for k in allowed if k not in d and k not in optional)
    if missing:
        _err(f"{typename} 缺必填字段: {missing}")
    return d


def _str(d: dict, key: str, typename: str, *, none: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        if none:
            return None
        _err(f"{typename}.{key} 必填")
    if not isinstance(v, str) or v == "":
        _err(f"{typename}.{key} 必须为非空字符串，得到 {v!r}")
    return v


def _str_or_empty(d: dict, key: str, typename: str, default: str = "") -> str:
    v = d.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        _err(f"{typename}.{key} 必须为字符串，得到 {type(v).__name__}")
    return v


def _bool(d: dict, key: str, typename: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        _err(f"{typename}.{key} 必须为 bool，得到 {type(v).__name__}")
    return v


def _int(d: dict, key: str, typename: str) -> int:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        _err(f"{typename}.{key} 必须为 int，得到 {type(v).__name__}")
    return v


def _enum(v: Any, allowed: tuple[str, ...], typename: str, key: str) -> str:
    if v not in allowed:
        _err(f"{typename}.{key} 非法: {v!r}（允许 {allowed}）")
    return v


def _str_tuple(d: dict, key: str, typename: str, *, allow_empty: bool = True
               ) -> tuple[str, ...]:
    v = d.get(key)
    if not isinstance(v, list):
        _err(f"{typename}.{key} 必须为 list[str]，得到 {type(v).__name__}")
    if not v and not allow_empty:
        _err(f"{typename}.{key} 不能为空")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or x == "":
            _err(f"{typename}.{key} 含非法元素 {x!r}")
        out.append(x)
    return tuple(out)


def _sha256(d: dict, key: str, typename: str) -> str:
    v = _str(d, key, typename)
    if len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
        _err(f"{typename}.{key} 必须为 64 位小写 hex，得到 {v!r}")
    return v


def _body(d: dict, allowed: tuple[str, ...], typename: str) -> dict:
    """按封闭白名单取规范体（顺序固定，供确定性指纹使用）。"""
    return {k: d[k] for k in allowed}


def _rel_path(v: Any, typename: str, key: str) -> str:
    """仓库相对 POSIX 路径；复用 ``planning.demo_scope_schema`` 的唯一实现。"""
    try:
        return DSS.require_rel_asset_path(v, typename, key)
    except DSS.SchemaValidationError as exc:
        _err(str(exc))
        raise  # pragma: no cover - _err 必然抛出


def _source_document_record(rec: Any) -> tuple[str, str]:
    """校验一条来源治理文档记录，返回 ``(path, sha256)``。

    未知键、缺键、空值、非法路径、非法哈希一律 fail-closed。
    """
    typename = "FormalSourceDocument"
    if not isinstance(rec, dict):
        _err(f"{typename} 必须为 dict，得到 {type(rec).__name__}")
    d = _reject_unknown(rec, set(FORMAL_SOURCE_DOCUMENT_KEYS), typename)
    path = _rel_path(d["path"], typename, "path")
    digest = d["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 \
            or any(c not in "0123456789abcdef" for c in digest):
        _err(f"{typename}.sha256 必须为 64 位小写 sha256 hex，得到 {digest!r}")
    return path, digest


# ---------------------------------------------------------------------------
# 1. 四个 report-bound 状态（四个独立类型，互不自动映射）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessCompletionStatus:
    """流程是否完成。与预览可用性、系统 Assurance、人工确认彼此独立。"""

    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        _enum(self.status, PROCESS_COMPLETION_STATUSES, "ProcessCompletionStatus", "status")
        if not isinstance(self.detail, str):
            _err("ProcessCompletionStatus.detail 必须为字符串")

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Any) -> "ProcessCompletionStatus":
        d = _reject_unknown(d, set(NESTED_STATUS_WIRE_KEYS), "ProcessCompletionStatus",
                            optional=NESTED_STATUS_OPTIONAL_KEYS)
        return cls(status=_enum(d.get("status"), PROCESS_COMPLETION_STATUSES,
                                "ProcessCompletionStatus", "status"),
                   detail=_str_or_empty(d, "detail", "ProcessCompletionStatus"))


@dataclass(frozen=True)
class PreviewAvailabilityStatus:
    """预览是否可用。``AVAILABLE`` **不**蕴含系统 Assurance 通过。"""

    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        _enum(self.status, PREVIEW_AVAILABILITY_STATUSES, "PreviewAvailabilityStatus", "status")
        if not isinstance(self.detail, str):
            _err("PreviewAvailabilityStatus.detail 必须为字符串")

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Any) -> "PreviewAvailabilityStatus":
        d = _reject_unknown(d, set(NESTED_STATUS_WIRE_KEYS), "PreviewAvailabilityStatus",
                            optional=NESTED_STATUS_OPTIONAL_KEYS)
        return cls(status=_enum(d.get("status"), PREVIEW_AVAILABILITY_STATUSES,
                                "PreviewAvailabilityStatus", "status"),
                   detail=_str_or_empty(d, "detail", "PreviewAvailabilityStatus"))


@dataclass(frozen=True)
class SystemAssuranceStatus:
    """系统 Assurance 结果。``PASSED`` **不**蕴含人工确认。"""

    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        _enum(self.status, SYSTEM_ASSURANCE_STATUSES, "SystemAssuranceStatus", "status")
        if not isinstance(self.detail, str):
            _err("SystemAssuranceStatus.detail 必须为字符串")

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Any) -> "SystemAssuranceStatus":
        d = _reject_unknown(d, set(NESTED_STATUS_WIRE_KEYS), "SystemAssuranceStatus",
                            optional=NESTED_STATUS_OPTIONAL_KEYS)
        return cls(status=_enum(d.get("status"), SYSTEM_ASSURANCE_STATUSES,
                                "SystemAssuranceStatus", "status"),
                   detail=_str_or_empty(d, "detail", "SystemAssuranceStatus"))


@dataclass(frozen=True)
class HumanAcceptanceStatus:
    """人工最终确认状态。

    - wire 完整支持三个取值 ``NOT_REVIEWED`` / ``ACCEPTED`` / ``REJECTED``，以便未来
      **独立人工流程** 写入其结论并可严格读回；
    - 默认值恒为 ``HUMAN_ACCEPTANCE_DEFAULT``（``NOT_REVIEWED``）；
    - 本批不提供任何把它改成非默认值的业务 API，也不存在按其它三轴自动推导人工状态的
      入口；实施方不得代替用户或 Codex 填写、批准或 seal 人工验收。
    """

    status: str = HUMAN_ACCEPTANCE_DEFAULT
    detail: str = ""

    def __post_init__(self) -> None:
        _enum(self.status, HUMAN_ACCEPTANCE_STATUSES, "HumanAcceptanceStatus", "status")
        if not isinstance(self.detail, str):
            _err("HumanAcceptanceStatus.detail 必须为字符串")

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Any) -> "HumanAcceptanceStatus":
        d = _reject_unknown(d, set(NESTED_STATUS_WIRE_KEYS), "HumanAcceptanceStatus",
                            optional=NESTED_STATUS_OPTIONAL_KEYS)
        return cls(status=_enum(d.get("status"), HUMAN_ACCEPTANCE_STATUSES,
                                "HumanAcceptanceStatus", "status"),
                   detail=_str_or_empty(d, "detail", "HumanAcceptanceStatus"))


# ---------------------------------------------------------------------------
# 2. ReportVersionIdentity
# ---------------------------------------------------------------------------

def derive_report_version(content_fingerprint: str) -> str:
    if len(content_fingerprint) != 64:
        _err("report content_fingerprint 必须为 64 位 hex")
    return "rv_" + content_fingerprint[:24]


def _canonical_id_tuple(values: Any, what: str, *, prefix: str | None,
                        allow_empty: bool) -> tuple[str, ...]:
    """把 ID 序列规范化为**排序去重后**的 tuple；重复即拒绝（不静默吞掉）。"""
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
        _err(f"{what} 必须为 ID 序列，得到 {type(values).__name__}")
    out: list[str] = []
    for v in values:
        if not isinstance(v, str) or v == "":
            _err(f"{what} 含非法元素 {v!r}")
        if prefix is not None and not v.startswith(prefix):
            _err(f"{what} 的元素必须以 {prefix!r} 开头，得到 {v!r}")
        out.append(v)
    if len(set(out)) != len(out):
        _err(f"{what} 含重复 ID")
    if not out and not allow_empty:
        _err(f"{what} 不能为空")
    return tuple(sorted(out))


@dataclass(frozen=True)
class ReportVersionIdentity:
    """绑定一次报告内容版本的身份。

    - 只由 ``REPORT_VERSION_FINGERPRINT_FIELDS`` 派生 ``content_fingerprint`` 与
      ``report_version``；白名单**逐项对应实施计划 §4.6** 的每一类内容依赖；
    - 正文 / Pack / 任务 / 财务权威 / 四类冻结资产 / 工件 schema / prompt 与 model
      policy 任一变化 → report_version 变化；
    - 各节 `SectionDraft` 身份（``section_draft_ids``）与组装报告规范载荷
      （``assembled_payload_fingerprint``，排除自身版本字段后逐字段重算）同样是内容
      依赖：改 Draft ID 或改"不出现在 Markdown 里但属于报告身份"的字段都会改变
      `report_version`；
    - 操作性 run identity 变化（run_id / attempt / 时间 / 路径）→ report_version 不变，
      且这些字段**根本不存在**于本类型与 ``build`` 签名中。
    """

    schema_version: str
    report_version: str
    content_fingerprint: str
    profile_fingerprint: str
    scope_input_fingerprint: str
    projection_id: str
    plan_id: str
    job_id: str
    company_id: str
    report_as_of: str
    selected_task_ids: tuple[str, ...]
    topic_pack_ids: tuple[str, ...]
    financial_fact_pack_artifact_id: str | None
    contract_fingerprint: str
    source_policy_fingerprint: str
    writing_spec_fingerprint: str
    presentation_profile_fingerprint: str
    dependency_fingerprint: str
    body_fingerprint: str
    section_draft_ids: tuple[str, ...]
    assembled_payload_fingerprint: str
    narrative_schema_version: str
    claim_schema_version: str
    table_schema_version: str
    writer_schema_version: str
    assembler_schema_version: str
    claim_binding_gate_version: str
    claim_entailment_rules_version: str
    prompt_version: str
    model_policy_id: str

    def __post_init__(self) -> None:
        if self.schema_version != REPORT_VERSION_SCHEMA_VERSION:
            if self.schema_version in LEGACY_REPORT_VERSION_SCHEMA_VERSIONS:
                _err(
                    f"ReportVersionIdentity 拒绝 legacy {self.schema_version!r}："
                    "v1 / v2 身份对象只经 load_legacy_report_version_for_audit 只读回放，"
                    "不得升级为 v3，也不得用 v3 白名单重算它们的指纹")
            _err(f"ReportVersionIdentity.schema_version 必须为 {REPORT_VERSION_SCHEMA_VERSION!r}")
        for key in REPORT_VERSION_SHA256_FIELDS:
            v = getattr(self, key)
            if not isinstance(v, str) or len(v) != 64 \
                    or any(c not in "0123456789abcdef" for c in v):
                _err(f"ReportVersionIdentity.{key} 必须为 64 位小写 sha256 hex，得到 {v!r}")
        _sha256({"v": self.content_fingerprint}, "v", "ReportVersionIdentity.content_fingerprint")
        for key in REPORT_VERSION_TEXT_FIELDS:
            v = getattr(self, key)
            if not isinstance(v, str) or v == "":
                _err(f"ReportVersionIdentity.{key} 必须为非空字符串，得到 {v!r}")
        if not self.projection_id.startswith("proj_"):
            _err("ReportVersionIdentity.projection_id 必须为 M930 投影的 'proj_' ID")
        if not self.plan_id.startswith("dplan_"):
            _err("ReportVersionIdentity.plan_id 必须为 M930 投影的 'dplan_' 计划 ID")
        if self.selected_task_ids != _canonical_id_tuple(
                self.selected_task_ids, "ReportVersionIdentity.selected_task_ids",
                prefix="dtask_", allow_empty=False):
            _err("ReportVersionIdentity.selected_task_ids 必须为排序去重后的 dtask_ ID 序列")
        if self.topic_pack_ids != _canonical_id_tuple(
                self.topic_pack_ids, "ReportVersionIdentity.topic_pack_ids",
                prefix=None, allow_empty=True):
            _err("ReportVersionIdentity.topic_pack_ids 必须为排序去重后的 ID 序列")
        if self.section_draft_ids != _canonical_id_tuple(
                self.section_draft_ids, "ReportVersionIdentity.section_draft_ids",
                prefix="sdraft_", allow_empty=False):
            _err("ReportVersionIdentity.section_draft_ids 必须为排序去重后的 sdraft_ ID 序列")
        if self.financial_fact_pack_artifact_id is not None and (
                not isinstance(self.financial_fact_pack_artifact_id, str)
                or self.financial_fact_pack_artifact_id == ""):
            _err("ReportVersionIdentity.financial_fact_pack_artifact_id 必须为非空字符串或 None")
        computed = self.compute_content_fingerprint()
        if self.content_fingerprint != computed:
            _err("ReportVersionIdentity.content_fingerprint 与当前内容不一致"
                 f"（声明 {self.content_fingerprint} ≠ 计算 {computed}）")
        if self.report_version != derive_report_version(computed):
            _err("ReportVersionIdentity.report_version 必须由 content_fingerprint 派生")

    def fingerprint_body(self) -> dict:
        """按封闭白名单取规范体（顺序固定，供确定性指纹使用）。"""
        body = {k: getattr(self, k) for k in REPORT_VERSION_FINGERPRINT_FIELDS}
        for key in ("selected_task_ids", "topic_pack_ids", "section_draft_ids"):
            body[key] = list(body[key])
        return body

    def compute_content_fingerprint(self) -> str:
        body = self.fingerprint_body()
        leak = sorted(set(body) & set(REPORT_VERSION_FORBIDDEN_FIELDS))
        if leak:
            _err(f"report version 指纹体含被禁字段: {leak}")
        missing = sorted(set(REPORT_VERSION_FINGERPRINT_FIELDS) - set(body))
        if missing:
            _err(f"report version 白名单字段缺失: {missing}")
        return sha256_canonical(body)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "report_version": self.report_version,
            "content_fingerprint": self.content_fingerprint,
            **self.fingerprint_body(),
        }

    @classmethod
    def build(cls, *, profile_fingerprint: str, scope_input_fingerprint: str,
              projection_id: str, plan_id: str, job_id: str, company_id: str,
              report_as_of: str, selected_task_ids: tuple[str, ...],
              topic_pack_ids: tuple[str, ...] = (),
              financial_fact_pack_artifact_id: str | None = None,
              contract_fingerprint: str, source_policy_fingerprint: str,
              writing_spec_fingerprint: str, presentation_profile_fingerprint: str,
              dependency_fingerprint: str, body_fingerprint: str,
              section_draft_ids: tuple[str, ...],
              assembled_payload_fingerprint: str,
              narrative_schema_version: str, claim_schema_version: str,
              table_schema_version: str, writer_schema_version: str,
              assembler_schema_version: str, claim_binding_gate_version: str,
              claim_entailment_rules_version: str, prompt_version: str,
              model_policy_id: str) -> "ReportVersionIdentity":
        tasks = _canonical_id_tuple(selected_task_ids,
                                    "ReportVersionIdentity.selected_task_ids",
                                    prefix="dtask_", allow_empty=False)
        packs = _canonical_id_tuple(topic_pack_ids, "ReportVersionIdentity.topic_pack_ids",
                                    prefix=None, allow_empty=True)
        drafts = _canonical_id_tuple(
            section_draft_ids, "ReportVersionIdentity.section_draft_ids",
            prefix="sdraft_", allow_empty=False)
        body = {
            "profile_fingerprint": profile_fingerprint,
            "scope_input_fingerprint": scope_input_fingerprint,
            "projection_id": projection_id,
            "plan_id": plan_id,
            "job_id": job_id,
            "company_id": company_id,
            "report_as_of": report_as_of,
            "selected_task_ids": list(tasks),
            "topic_pack_ids": list(packs),
            "financial_fact_pack_artifact_id": financial_fact_pack_artifact_id,
            "contract_fingerprint": contract_fingerprint,
            "source_policy_fingerprint": source_policy_fingerprint,
            "writing_spec_fingerprint": writing_spec_fingerprint,
            "presentation_profile_fingerprint": presentation_profile_fingerprint,
            "dependency_fingerprint": dependency_fingerprint,
            "body_fingerprint": body_fingerprint,
            "section_draft_ids": list(drafts),
            "assembled_payload_fingerprint": assembled_payload_fingerprint,
            "narrative_schema_version": narrative_schema_version,
            "claim_schema_version": claim_schema_version,
            "table_schema_version": table_schema_version,
            "writer_schema_version": writer_schema_version,
            "assembler_schema_version": assembler_schema_version,
            "claim_binding_gate_version": claim_binding_gate_version,
            "claim_entailment_rules_version": claim_entailment_rules_version,
            "prompt_version": prompt_version,
            "model_policy_id": model_policy_id,
        }
        if set(body) != set(REPORT_VERSION_FINGERPRINT_FIELDS):
            _err("report version 规范体与白名单不一致: "
                 f"{sorted(set(body) ^ set(REPORT_VERSION_FINGERPRINT_FIELDS))}")
        fp = sha256_canonical(body)
        return cls(
            schema_version=REPORT_VERSION_SCHEMA_VERSION,
            report_version=derive_report_version(fp),
            content_fingerprint=fp,
            selected_task_ids=tasks,
            topic_pack_ids=packs,
            section_draft_ids=drafts,
            **{k: v for k, v in body.items()
               if k not in ("selected_task_ids", "topic_pack_ids", "section_draft_ids")},
        )

    @classmethod
    def from_dict(cls, d: Any) -> "ReportVersionIdentity":
        # current reader 先看 marker：legacy v1 载荷必须另走 legacy reader，不得被宽松读成 v2。
        if isinstance(d, dict) and d.get("schema_version") in LEGACY_REPORT_VERSION_SCHEMA_VERSIONS:
            _err(f"ReportVersionIdentity current reader 拒绝 legacy "
                 f"{d.get('schema_version')!r}：只经 load_legacy_report_version_for_audit "
                 "只读回放，禁止升级或按 v3 白名单重算")
        allowed = set(REPORT_VERSION_FINGERPRINT_FIELDS) | {
            "schema_version", "report_version", "content_fingerprint"}
        d = _reject_unknown(d, allowed, "ReportVersionIdentity")
        return cls(
            schema_version=_str(d, "schema_version", "ReportVersionIdentity"),
            report_version=_str(d, "report_version", "ReportVersionIdentity"),
            content_fingerprint=_sha256(d, "content_fingerprint", "ReportVersionIdentity"),
            profile_fingerprint=_sha256(d, "profile_fingerprint", "ReportVersionIdentity"),
            scope_input_fingerprint=_sha256(d, "scope_input_fingerprint",
                                            "ReportVersionIdentity"),
            projection_id=_str(d, "projection_id", "ReportVersionIdentity"),
            plan_id=_str(d, "plan_id", "ReportVersionIdentity"),
            job_id=_str(d, "job_id", "ReportVersionIdentity"),
            company_id=_str(d, "company_id", "ReportVersionIdentity"),
            report_as_of=_str(d, "report_as_of", "ReportVersionIdentity"),
            selected_task_ids=_canonical_id_tuple(
                _str_tuple(d, "selected_task_ids", "ReportVersionIdentity"),
                "ReportVersionIdentity.selected_task_ids", prefix="dtask_", allow_empty=False),
            topic_pack_ids=_canonical_id_tuple(
                _str_tuple(d, "topic_pack_ids", "ReportVersionIdentity"),
                "ReportVersionIdentity.topic_pack_ids", prefix=None, allow_empty=True),
            section_draft_ids=_canonical_id_tuple(
                _str_tuple(d, "section_draft_ids", "ReportVersionIdentity"),
                "ReportVersionIdentity.section_draft_ids", prefix="sdraft_",
                allow_empty=False),
            financial_fact_pack_artifact_id=_str(
                d, "financial_fact_pack_artifact_id", "ReportVersionIdentity", none=True),
            contract_fingerprint=_sha256(d, "contract_fingerprint", "ReportVersionIdentity"),
            source_policy_fingerprint=_sha256(d, "source_policy_fingerprint",
                                              "ReportVersionIdentity"),
            writing_spec_fingerprint=_sha256(d, "writing_spec_fingerprint",
                                             "ReportVersionIdentity"),
            presentation_profile_fingerprint=_sha256(
                d, "presentation_profile_fingerprint", "ReportVersionIdentity"),
            dependency_fingerprint=_sha256(d, "dependency_fingerprint", "ReportVersionIdentity"),
            body_fingerprint=_sha256(d, "body_fingerprint", "ReportVersionIdentity"),
            assembled_payload_fingerprint=_sha256(
                d, "assembled_payload_fingerprint", "ReportVersionIdentity"),
            narrative_schema_version=_str(d, "narrative_schema_version",
                                           "ReportVersionIdentity"),
            claim_schema_version=_str(d, "claim_schema_version", "ReportVersionIdentity"),
            table_schema_version=_str(d, "table_schema_version", "ReportVersionIdentity"),
            writer_schema_version=_str(d, "writer_schema_version", "ReportVersionIdentity"),
            assembler_schema_version=_str(d, "assembler_schema_version",
                                          "ReportVersionIdentity"),
            claim_binding_gate_version=_str(d, "claim_binding_gate_version",
                                            "ReportVersionIdentity"),
            claim_entailment_rules_version=_str(d, "claim_entailment_rules_version",
                                                "ReportVersionIdentity"),
            prompt_version=_str(d, "prompt_version", "ReportVersionIdentity"),
            model_policy_id=_str(d, "model_policy_id", "ReportVersionIdentity"),
        )


#: v1 report version 身份对象的字段闭集（**无**两个判定链版本键；不升级、不重算）。
#: **逐字固定**：不再由 current 白名单做差集派生——current 白名单继续前进时，差集派生
#: 会把新键悄悄算进 v1，等于用新口径重算旧身份（这正是本轴禁止的事）。
LEGACY_REPORT_VERSION_V1_FIELDS = (
    "profile_fingerprint",
    "scope_input_fingerprint",
    "projection_id",
    "plan_id",
    "job_id",
    "company_id",
    "report_as_of",
    "selected_task_ids",
    "topic_pack_ids",
    "financial_fact_pack_artifact_id",
    "contract_fingerprint",
    "source_policy_fingerprint",
    "writing_spec_fingerprint",
    "presentation_profile_fingerprint",
    "dependency_fingerprint",
    "body_fingerprint",
    "narrative_schema_version",
    "claim_schema_version",
    "table_schema_version",
    "writer_schema_version",
    "assembler_schema_version",
    "prompt_version",
    "model_policy_id",
)

#: v2 report version 身份对象的字段闭集（= v1 + 两个判定链版本键；**无** v3 的
#: `section_draft_ids` / `assembled_payload_fingerprint`）。同样逐字固定。
LEGACY_REPORT_VERSION_V2_FIELDS = tuple(sorted(
    LEGACY_REPORT_VERSION_V1_FIELDS
    + ("claim_binding_gate_version", "claim_entailment_rules_version")))

#: `schema_version` marker → 该版本身份的字段闭集（legacy reader 的唯一分派表）。
LEGACY_REPORT_VERSION_FIELDS_BY_MARKER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("demo-report-version-v1", LEGACY_REPORT_VERSION_V1_FIELDS),
    ("demo-report-version-v2", LEGACY_REPORT_VERSION_V2_FIELDS),
)

#: 每个 legacy 版本里需要转成 list 才能重算指纹的序列字段。
_LEGACY_SEQUENCE_FIELDS = ("selected_task_ids", "topic_pack_ids", "section_draft_ids")


@dataclass(frozen=True)
class LegacyReportVersionIdentity:
    """某一版 legacy report version 身份对象的**只读**视图（v1 / v2）。

    v1 白名单里没有 Claim Binding Gate / Claim entailment 规则版本字段；v2 里没有
    v3 新增的 `section_draft_ids` / `assembled_payload_fingerprint`。因此用 v3 白名单
    重算它们的指纹必然得到不同的值。本类型只按**载荷自己那一版**的字段集重算并核对
    指纹，不提供 ``build``（无法构造新的 legacy 对象），也不参与任何 current 决策。
    """

    payload: dict
    schema_version: str
    report_version: str
    content_fingerprint: str
    fields: tuple[tuple[str, Any], ...]
    fields_spec: tuple[str, ...]

    #: 初版 legacy marker（保留类属性：外部的"这是哪一版"判断不必改）。
    LEGACY_SCHEMA_VERSION = "demo-report-version-v1"

    def field(self, key: str) -> Any:
        for k, v in self.fields:
            if k == key:
                return v
        _err(f"LegacyReportVersionIdentity[{self.schema_version}] 无字段 {key!r}")

    def recompute_fingerprint(self) -> str:
        """按**该 legacy 版本自己的字段集**重算内容指纹（不掺入更新版本的键）。"""
        body = {}
        for key in self.fields_spec:
            v = self.field(key)
            body[key] = list(v) if key in _LEGACY_SEQUENCE_FIELDS else v
        return sha256_canonical(body)

    def to_dict(self) -> dict:
        return dict(self.payload)


#: 旧名字保留：`LegacyReportVersionIdentityV1` 现在等价于 v1 的只读视图（同一实现）。
LegacyReportVersionIdentityV1 = LegacyReportVersionIdentity


def _legacy_fields_for(marker: str) -> tuple[str, ...]:
    for name, fields in LEGACY_REPORT_VERSION_FIELDS_BY_MARKER:
        if name == marker:
            return fields
    _err(f"不是登记的 legacy report version 载荷：schema_version={marker!r}"
         f"（current {REPORT_VERSION_SCHEMA_VERSION} 载荷不得经 legacy reader 读回）")


def load_legacy_report_version_for_audit(d: Any) -> LegacyReportVersionIdentity:
    """把 `demo-report-version-v1` / `-v2` 载荷**只读**还原（current reader 拒绝它们）。"""
    if not isinstance(d, dict):
        _err("legacy ReportVersionIdentity 载荷必须为 dict")
    marker = d.get("schema_version")
    if not isinstance(marker, str) or marker == "":
        _err("legacy ReportVersionIdentity 载荷必须显式携带 schema_version marker"
             "（不得缺省成某一版）")
    fields_spec = _legacy_fields_for(marker)
    typename = f"LegacyReportVersionIdentity[{marker}]"
    allowed = set(fields_spec) | {
        "schema_version", "report_version", "content_fingerprint"}
    payload = _reject_unknown(d, allowed, typename)
    missing = [k for k in fields_spec if k not in payload]
    if missing:
        _err(f"{typename} 缺字段 {missing}（legacy 载荷必须带齐该版白名单）")
    view = LegacyReportVersionIdentity(
        payload=payload,
        schema_version=marker,
        report_version=_str(payload, "report_version", typename),
        content_fingerprint=_sha256(payload, "content_fingerprint", typename),
        fields=tuple((k, payload[k]) for k in fields_spec),
        fields_spec=fields_spec,
    )
    computed = view.recompute_fingerprint()
    if view.content_fingerprint != computed:
        _err(f"{typename}.content_fingerprint 与 {marker} 字段集不一致"
             f"（声明 {view.content_fingerprint} ≠ 按该版白名单计算 {computed}）")
    return view


# ---------------------------------------------------------------------------
# 3. RunProgressSnapshot（报告形成前唯一允许的状态）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunProgressSnapshot:
    """报告形成**前**唯一允许的状态：只绑定一次运行尝试。

    它**没有** report_version 字段，因此无法用空报告版本凑出四个 report-bound 状态。
    """

    schema_version: str
    run_id: str
    attempt: int
    phase_id: str
    phase_status: str
    detail: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != RUN_PROGRESS_SCHEMA_VERSION:
            _err(f"RunProgressSnapshot.schema_version 必须为 {RUN_PROGRESS_SCHEMA_VERSION!r}")
        if not isinstance(self.run_id, str) or not self.run_id:
            _err("RunProgressSnapshot.run_id 必须为非空字符串")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            _err("RunProgressSnapshot.attempt 必须为 >= 1 的整数")
        if not self.phase_id:
            _err("RunProgressSnapshot.phase_id 必须为非空字符串")
        _enum(self.phase_status, PROCESS_COMPLETION_STATUSES, "RunProgressSnapshot", "phase_status")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "phase_id": self.phase_id,
            "phase_status": self.phase_status,
            "detail": self.detail,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "RunProgressSnapshot":
        d = _reject_unknown(d, set(cls.__dataclass_fields__), "RunProgressSnapshot")
        return cls(
            schema_version=_str(d, "schema_version", "RunProgressSnapshot"),
            run_id=_str(d, "run_id", "RunProgressSnapshot"),
            attempt=_int(d, "attempt", "RunProgressSnapshot"),
            phase_id=_str(d, "phase_id", "RunProgressSnapshot"),
            phase_status=_enum(d.get("phase_status"), PROCESS_COMPLETION_STATUSES,
                               "RunProgressSnapshot", "phase_status"),
            detail=_str_or_empty(d, "detail", "RunProgressSnapshot"),
            updated_at=_str_or_empty(d, "updated_at", "RunProgressSnapshot"),
        )


# ---------------------------------------------------------------------------
# 4. ReportStatusSnapshot（报告形成后，四个状态共享同一 report version）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportStatusSnapshot:
    """报告形成后的四状态绑定。必须携带真实 ``ReportVersionIdentity``。

    四个状态是四个独立类型且**不**互相自动映射：preview available ≠ assurance passed，
    assurance passed ≠ human acceptance。人工状态默认 ``NOT_REVIEWED``；wire 必须能读回
    未来独立人工流程写入的 ``ACCEPTED`` / ``REJECTED``，但本批**不提供任何**把它改成
    非默认值的业务 API（没有任何按其它三轴自动推导人工状态的入口）。
    """

    schema_version: str
    report_version_identity: ReportVersionIdentity
    process_completion: ProcessCompletionStatus
    preview_availability: PreviewAvailabilityStatus
    system_assurance: SystemAssuranceStatus
    human_acceptance: HumanAcceptanceStatus

    def __post_init__(self) -> None:
        if self.schema_version != REPORT_STATUS_SCHEMA_VERSION:
            _err(f"ReportStatusSnapshot.schema_version 必须为 {REPORT_STATUS_SCHEMA_VERSION!r}")
        if not isinstance(self.report_version_identity, ReportVersionIdentity):
            _err("ReportStatusSnapshot 必须绑定真实的 ReportVersionIdentity"
                 "（不得编造空报告版本来凑满四个状态）")
        for name, typ in (("process_completion", ProcessCompletionStatus),
                          ("preview_availability", PreviewAvailabilityStatus),
                          ("system_assurance", SystemAssuranceStatus),
                          ("human_acceptance", HumanAcceptanceStatus)):
            if not isinstance(getattr(self, name), typ):
                _err(f"ReportStatusSnapshot.{name} 必须为 {typ.__name__}")
        # 四轴互不推导：这里**不**校验人工状态是否为默认值，否则未来独立人工流程
        # 写回的 ACCEPTED / REJECTED 无法读回（wire 隔离与业务 API 隔离是两件事）。

    @property
    def report_version(self) -> str:
        return self.report_version_identity.report_version

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "report_version": self.report_version,
            "report_version_identity": self.report_version_identity.to_dict(),
            "process_completion": self.process_completion.to_dict(),
            "preview_availability": self.preview_availability.to_dict(),
            "system_assurance": self.system_assurance.to_dict(),
            "human_acceptance": self.human_acceptance.to_dict(),
        }

    @classmethod
    def build(cls, report_version_identity: ReportVersionIdentity,
              *, process_completion: ProcessCompletionStatus,
              preview_availability: PreviewAvailabilityStatus,
              system_assurance: SystemAssuranceStatus,
              human_acceptance: HumanAcceptanceStatus | None = None) -> "ReportStatusSnapshot":
        return cls(
            schema_version=REPORT_STATUS_SCHEMA_VERSION,
            report_version_identity=report_version_identity,
            process_completion=process_completion,
            preview_availability=preview_availability,
            system_assurance=system_assurance,
            human_acceptance=human_acceptance or HumanAcceptanceStatus(),
        )

    @classmethod
    def from_dict(cls, d: Any) -> "ReportStatusSnapshot":
        d = _reject_unknown(d, set(cls.__dataclass_fields__) | {"report_version"},
                            "ReportStatusSnapshot")
        identity = ReportVersionIdentity.from_dict(d.get("report_version_identity") or {})
        declared = d.get("report_version")
        # 四个状态必须绑定**同一个** report version：wire 上声明的版本与所绑定的
        # identity 不一致即 fail-closed（不得静默忽略声明的版本字符串）。
        if not isinstance(declared, str) or declared == "":
            _err("ReportStatusSnapshot.report_version 缺失")
        if declared != identity.report_version:
            _err("ReportStatusSnapshot.report_version 与所绑定的 ReportVersionIdentity "
                 f"不一致（声明 {declared} ≠ identity {identity.report_version}）")
        return cls(
            schema_version=_str(d, "schema_version", "ReportStatusSnapshot"),
            report_version_identity=identity,
            process_completion=ProcessCompletionStatus.from_dict(
                d.get("process_completion") or {}),
            preview_availability=PreviewAvailabilityStatus.from_dict(
                d.get("preview_availability") or {}),
            system_assurance=SystemAssuranceStatus.from_dict(d.get("system_assurance") or {}),
            human_acceptance=HumanAcceptanceStatus.from_dict(d.get("human_acceptance") or {}),
        )


# ---------------------------------------------------------------------------
# 5. FormalPhaseClosureSnapshot（只读治理快照）
# ---------------------------------------------------------------------------

def derive_formal_closure_id(snapshot_fingerprint: str) -> str:
    if len(snapshot_fingerprint) != 64:
        _err("formal closure snapshot_fingerprint 必须为 64 位 hex")
    return "fpc_" + snapshot_fingerprint[:24]


@dataclass(frozen=True)
class FormalPhaseClosureSnapshot:
    """正式阶段关闭状态：独立只读治理快照。

    不进 report version、不作 Assurance 输入、不由 runtime 派生或修改
    （``derived_by_runtime`` 必须为 False）。每份来源治理文档都以**规范仓库相对路径
    + 64 位 SHA256** 登记，阶段记录只引用已登记文档，快照指纹覆盖这些哈希 —— 因此
    "同一路径、文档内容变了"必然改变快照身份。
    """

    schema_version: str
    snapshot_id: str
    snapshot_fingerprint: str
    phases: tuple[dict, ...]
    source_documents: tuple[dict, ...]
    derived_by_runtime: bool
    recorded_at: str

    def __post_init__(self) -> None:
        if self.schema_version != FORMAL_CLOSURE_SCHEMA_VERSION:
            _err(f"FormalPhaseClosureSnapshot.schema_version 必须为 "
                 f"{FORMAL_CLOSURE_SCHEMA_VERSION!r}")
        if self.derived_by_runtime:
            _err("FormalPhaseClosureSnapshot.derived_by_runtime 必须为 False："
                 "正式阶段关闭是独立只读治理快照，不由 runtime 派生或修改")
        if not self.phases:
            _err("FormalPhaseClosureSnapshot.phases 不能为空")
        if not self.source_documents:
            _err("FormalPhaseClosureSnapshot.source_documents 不能为空（必须可回指治理文档）")
        registered: dict[str, str] = {}
        prev_path = ""
        for rec in self.source_documents:
            path, digest = _source_document_record(rec)
            if path in registered:
                _err(f"FormalPhaseClosureSnapshot.source_documents 含重复 path: {path!r}")
            if path <= prev_path:
                _err("FormalPhaseClosureSnapshot.source_documents 必须按 path 升序且唯一")
            prev_path = path
            registered[path] = digest
        seen: set[str] = set()
        for p in self.phases:
            if not isinstance(p, dict):
                _err(f"FormalPhaseRecord 必须为 dict，得到 {type(p).__name__}")
            extra = sorted(set(p) - set(FORMAL_PHASE_KEYS))
            if extra:
                _err(f"FormalPhaseRecord 含未知字段: {extra}")
            missing = [k for k in FORMAL_PHASE_REQUIRED_KEYS if k not in p]
            if missing:
                _err(f"FormalPhaseRecord 缺必填字段: {missing}")
            _enum(p["status"], FORMAL_PHASE_CLOSURE_STATUSES, "FormalPhaseRecord", "status")
            if p["phase_id"] in seen:
                _err(f"FormalPhaseClosureSnapshot 含重复 phase_id: {p['phase_id']!r}")
            seen.add(p["phase_id"])
            if p["source_document"] not in registered:
                _err(f"FormalPhaseRecord({p['phase_id']}).source_document 必须引用"
                     f" source_documents 中**已登记**的文档，得到 {p['source_document']!r}")
        computed = self.compute_snapshot_fingerprint()
        if self.snapshot_fingerprint != computed:
            _err("FormalPhaseClosureSnapshot.snapshot_fingerprint 与当前内容不一致"
                 f"（声明 {self.snapshot_fingerprint} ≠ 计算 {computed}）")
        if self.snapshot_id != derive_formal_closure_id(computed):
            _err("FormalPhaseClosureSnapshot.snapshot_id 必须由 snapshot_fingerprint 派生")

    def fingerprint_body(self) -> dict:
        """来源文档记录（含 SHA256）整体进入指纹 —— 文档内容变化必然改变身份。"""
        return {
            "phases": [dict(p) for p in self.phases],
            "source_documents": [dict(r) for r in self.source_documents],
            "derived_by_runtime": self.derived_by_runtime,
            "recorded_at": self.recorded_at,
        }

    def compute_snapshot_fingerprint(self) -> str:
        return sha256_canonical(self.fingerprint_body())

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            **self.fingerprint_body(),
        }

    @classmethod
    def build(cls, *, phases: tuple[dict, ...], source_documents: tuple[dict, ...],
              recorded_at: str) -> "FormalPhaseClosureSnapshot":
        records = tuple(
            {"path": _source_document_record(r)[0], "sha256": _source_document_record(r)[1]}
            for r in source_documents)
        body = {
            "phases": [dict(p) for p in phases],
            "source_documents": [dict(r) for r in records],
            "derived_by_runtime": False,
            "recorded_at": recorded_at,
        }
        fp = sha256_canonical(body)
        return cls(
            schema_version=FORMAL_CLOSURE_SCHEMA_VERSION,
            snapshot_id=derive_formal_closure_id(fp),
            snapshot_fingerprint=fp,
            phases=tuple(dict(p) for p in phases),
            source_documents=records,
            derived_by_runtime=False,
            recorded_at=recorded_at,
        )

    @classmethod
    def from_dict(cls, d: Any) -> "FormalPhaseClosureSnapshot":
        d = _reject_unknown(d, set(cls.__dataclass_fields__), "FormalPhaseClosureSnapshot")
        docs = []
        for rec in _as_list(d.get("source_documents"), "FormalPhaseClosureSnapshot",
                            "source_documents"):
            path, digest = _source_document_record(rec)
            docs.append({"path": path, "sha256": digest})
        return cls(
            schema_version=_str(d, "schema_version", "FormalPhaseClosureSnapshot"),
            snapshot_id=_str(d, "snapshot_id", "FormalPhaseClosureSnapshot"),
            snapshot_fingerprint=_sha256(d, "snapshot_fingerprint",
                                         "FormalPhaseClosureSnapshot"),
            phases=tuple(dict(p) for p in _as_list(d.get("phases"), "FormalPhaseClosureSnapshot",
                                                    "phases")),
            source_documents=tuple(docs),
            derived_by_runtime=_bool(d, "derived_by_runtime", "FormalPhaseClosureSnapshot"),
            recorded_at=_str_or_empty(d, "recorded_at", "FormalPhaseClosureSnapshot"),
        )


def _as_list(v: Any, typename: str, key: str) -> list:
    if not isinstance(v, list):
        _err(f"{typename}.{key} 必须为 list，得到 {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# 6. DesignSurfaceRecord / DesignSurfaceMatrix
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DesignSurfaceRecord:
    """一条设计面覆盖记录：证明某层设计面由真实产物/状态或显式 gap 承载。"""

    surface_id: str
    axis: str
    title: str
    status: str
    evidence_kind: str
    evidence_refs: tuple[str, ...] = ()
    limitation: str = ""

    def __post_init__(self) -> None:
        _enum(self.surface_id, DESIGN_SURFACE_IDS, "DesignSurfaceRecord", "surface_id")
        _enum(self.axis, DESIGN_AXES, "DesignSurfaceRecord", "axis")
        _enum(self.status, DESIGN_SURFACE_STATUSES, "DesignSurfaceRecord", "status")
        _enum(self.evidence_kind, DESIGN_SURFACE_EVIDENCE_KINDS, "DesignSurfaceRecord",
              "evidence_kind")
        if not self.title:
            _err("DesignSurfaceRecord.title 不能为空")
        if self.status == "demonstrated" and not self.evidence_refs:
            _err(f"DesignSurfaceRecord({self.surface_id}) 声明 demonstrated 时必须给出可回查"
                 " evidence_refs（不得用静态占位冒充已展示）")
        if self.evidence_kind == "none" and self.status == "demonstrated":
            _err(f"DesignSurfaceRecord({self.surface_id}) evidence_kind=none 不能声明 demonstrated")

    def to_dict(self) -> dict:
        return {
            "surface_id": self.surface_id,
            "axis": self.axis,
            "title": self.title,
            "status": self.status,
            "evidence_kind": self.evidence_kind,
            "evidence_refs": list(self.evidence_refs),
            "limitation": self.limitation,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DesignSurfaceRecord":
        d = _reject_unknown(d, set(cls.__dataclass_fields__) | {"evidence_refs"},
                            "DesignSurfaceRecord")
        return cls(
            surface_id=_str(d, "surface_id", "DesignSurfaceRecord"),
            axis=_str(d, "axis", "DesignSurfaceRecord"),
            title=_str(d, "title", "DesignSurfaceRecord"),
            status=_str(d, "status", "DesignSurfaceRecord"),
            evidence_kind=_str(d, "evidence_kind", "DesignSurfaceRecord"),
            evidence_refs=_str_tuple(d, "evidence_refs", "DesignSurfaceRecord"),
            limitation=_str_or_empty(d, "limitation", "DesignSurfaceRecord"),
        )


def derive_design_surface_matrix_id(matrix_fingerprint: str) -> str:
    if len(matrix_fingerprint) != 64:
        _err("design surface matrix_fingerprint 必须为 64 位 hex")
    return "dsm_" + matrix_fingerprint[:24]


@dataclass(frozen=True)
class DesignSurfaceMatrix:
    """设计面覆盖矩阵：三条独立轴上的真实状态，缺项必须可见。"""

    schema_version: str
    matrix_fingerprint: str
    records: tuple[DesignSurfaceRecord, ...]

    def __post_init__(self) -> None:
        if self.schema_version != DESIGN_SURFACE_SCHEMA_VERSION:
            _err(f"DesignSurfaceMatrix.schema_version 必须为 {DESIGN_SURFACE_SCHEMA_VERSION!r}")
        if not self.records:
            _err("DesignSurfaceMatrix.records 不能为空")
        ids = [r.surface_id for r in self.records]
        if len(set(ids)) != len(ids):
            _err("DesignSurfaceMatrix 含重复 surface_id")
        for r in self.records:
            if not isinstance(r, DesignSurfaceRecord):
                _err("DesignSurfaceMatrix.records 必须全部为 DesignSurfaceRecord")
        computed = self.compute_matrix_fingerprint()
        if self.matrix_fingerprint != computed:
            _err("DesignSurfaceMatrix.matrix_fingerprint 与当前内容不一致")

    def compute_matrix_fingerprint(self) -> str:
        return sha256_canonical([r.to_dict() for r in self.records])

    def by_axis(self) -> dict[str, tuple[DesignSurfaceRecord, ...]]:
        return {axis: tuple(r for r in self.records if r.axis == axis) for axis in DESIGN_AXES}

    def status_counts(self) -> dict[str, int]:
        return {s: sum(1 for r in self.records if r.status == s)
                for s in DESIGN_SURFACE_STATUSES}

    def missing_surface_ids(self) -> tuple[str, ...]:
        """尚未出现在矩阵中的设计面 ID（缺项必须显式可见，不得默认补齐）。"""
        present = {r.surface_id for r in self.records}
        return tuple(s for s in DESIGN_SURFACE_IDS if s not in present)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "matrix_fingerprint": self.matrix_fingerprint,
            "records": [r.to_dict() for r in self.records],
        }

    @classmethod
    def build(cls, records: tuple[DesignSurfaceRecord, ...]) -> "DesignSurfaceMatrix":
        fp = sha256_canonical([r.to_dict() for r in records])
        return cls(schema_version=DESIGN_SURFACE_SCHEMA_VERSION,
                   matrix_fingerprint=fp, records=tuple(records))

    @classmethod
    def from_dict(cls, d: Any) -> "DesignSurfaceMatrix":
        d = _reject_unknown(d, set(cls.__dataclass_fields__), "DesignSurfaceMatrix")
        return cls(
            schema_version=_str(d, "schema_version", "DesignSurfaceMatrix"),
            matrix_fingerprint=_sha256(d, "matrix_fingerprint", "DesignSurfaceMatrix"),
            records=tuple(DesignSurfaceRecord.from_dict(x) for x in
                          _as_list(d.get("records"), "DesignSurfaceMatrix", "records")),
        )


# ---------------------------------------------------------------------------
# 7. DemoBackboneRunManifest
# ---------------------------------------------------------------------------

def derive_run_manifest_id(manifest_fingerprint: str) -> str:
    if len(manifest_fingerprint) != 64:
        _err("run manifest_fingerprint 必须为 64 位 hex")
    return "dbm_" + manifest_fingerprint[:24]


def _assert_run_identity_dag(run_identity: DSS.DemoRunIdentity,
                             scope_manifest: DSS.ResolvedDemoScopeManifest,
                             report_version_identity: "ReportVersionIdentity | None") -> None:
    """闭合顶层身份 DAG：run identity、已解析 scope manifest 与 report version 必须互证。

    这是**唯一**的实现，构造（``build``）、反序列化（``from_dict`` 经 ``__post_init__``）
    与 artifact 严格读回三条路径都走它，不得各自维护一套较弱的检查。
    逐字段相等比较，**不是**字符串前缀/包含关系比较。
    """
    pairs = (
        ("run_id", run_identity.run_id, scope_manifest.run_id),
        ("attempt", run_identity.attempt, scope_manifest.attempt),
        ("job_id", run_identity.job_id, scope_manifest.job_id),
        ("scope_input_fingerprint", run_identity.scope_input_fingerprint,
         scope_manifest.scope_input_fingerprint),
    )
    for field, left, right in pairs:
        if left != right:
            _err(f"顶层身份 DAG 断裂：run_identity.{field} ≠ scope_manifest.{field}"
                 f"（{left!r} ≠ {right!r}）")
    if report_version_identity is None:
        return
    scope_pairs = (
        ("company_id", report_version_identity.company_id, scope_manifest.company_id),
        ("report_as_of", report_version_identity.report_as_of, scope_manifest.report_as_of),
        ("plan_id", report_version_identity.plan_id, scope_manifest.plan_id),
        ("projection_id", report_version_identity.projection_id,
         scope_manifest.projection_id),
        # report version 必须绑定**同一条** scope 输入链与同一个 job：只比投影/计划
        # 身份不足以排除"另一条 job 的 RVI 挂到本 manifest 上"。
        ("scope_input_fingerprint", report_version_identity.scope_input_fingerprint,
         scope_manifest.scope_input_fingerprint),
        ("job_id", report_version_identity.job_id, scope_manifest.job_id),
    )
    for field, left, right in scope_pairs:
        if left != right:
            _err(f"顶层身份 DAG 断裂：report_version_identity.{field} ≠ "
                 f"scope_manifest.{field}（{left!r} ≠ {right!r}）")
    if report_version_identity.selected_task_ids != tuple(sorted(scope_manifest.task_ids)):
        _err("顶层身份 DAG 断裂：report_version_identity.selected_task_ids 必须等于 "
             "scope_manifest.task_ids 的排序去重结果")
    # Topic Pack 必须是**精确等集**（AGENTS.md 完整 Pack 集门）：subset 会让"少一个 Pack
    # 仍然生成完整报告"成为合法状态，缺失的 Pack 必须表现为显式 gap/block，而不是被静默略过。
    exact_packs = tuple(sorted(scope_manifest.pack_ids))
    if report_version_identity.topic_pack_ids != exact_packs:
        missing = sorted(set(exact_packs) - set(report_version_identity.topic_pack_ids))
        extra = sorted(set(report_version_identity.topic_pack_ids) - set(exact_packs))
        _err("顶层身份 DAG 断裂：report_version_identity.topic_pack_ids 必须精确等集于 "
             f"scope_manifest.pack_ids（缺 {missing}，多 {extra}）")
    if (report_version_identity.financial_fact_pack_artifact_id
            != scope_manifest.financial_fact_pack_artifact_id):
        _err("顶层身份 DAG 断裂：report_version_identity.financial_fact_pack_artifact_id ≠ "
             "scope_manifest.financial_fact_pack_artifact_id")


@dataclass(frozen=True)
class DemoBackboneRunManifest:
    """一次 Demo Backbone 运行的 artifact 根 manifest。

    - 只**引用** ``planning`` 的运行身份与已解析 scope manifest，不重定义它们；
    - ``artifact_index_sha256`` 由 artifact 协议在写完索引后给出；索引不含 manifest 哈希，
      因此不存在自引用或哈希环；
    - ``manifest_id`` 只由规范体（排除自身身份字段与运行时间）派生。
    """

    schema_version: str
    manifest_id: str
    manifest_fingerprint: str
    run_identity: DSS.DemoRunIdentity
    scope_manifest: DSS.ResolvedDemoScopeManifest
    artifact_index_sha256: str
    formal_closure: FormalPhaseClosureSnapshot
    design_surfaces: DesignSurfaceMatrix
    report_version_identity: ReportVersionIdentity | None = None
    status: ReportStatusSnapshot | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != BACKBONE_SCHEMA_VERSION:
            _err(f"DemoBackboneRunManifest.schema_version 必须为 {BACKBONE_SCHEMA_VERSION!r}")
        if not isinstance(self.run_identity, DSS.DemoRunIdentity):
            _err("DemoBackboneRunManifest.run_identity 必须为 planning.DemoRunIdentity")
        if not isinstance(self.scope_manifest, DSS.ResolvedDemoScopeManifest):
            _err("DemoBackboneRunManifest.scope_manifest 必须为 "
                 "planning.ResolvedDemoScopeManifest")
        if not isinstance(self.formal_closure, FormalPhaseClosureSnapshot):
            _err("DemoBackboneRunManifest.formal_closure 必须为 FormalPhaseClosureSnapshot")
        if not isinstance(self.design_surfaces, DesignSurfaceMatrix):
            _err("DemoBackboneRunManifest.design_surfaces 必须为 DesignSurfaceMatrix")
        # 报告形成前后一致：要么两者都有，要么两者都无（不得伪造空版本）。
        if (self.report_version_identity is None) != (self.status is None):
            _err("DemoBackboneRunManifest 的 report_version_identity 与 status 必须同时存在或"
                 "同时为 None（不得编造空报告版本）")
        if self.report_version_identity is not None:
            if not isinstance(self.report_version_identity, ReportVersionIdentity):
                _err("report_version_identity 类型非法")
            if not isinstance(self.status, ReportStatusSnapshot):
                _err("status 类型非法")
            if self.status.report_version != self.report_version_identity.report_version:
                _err("DemoBackboneRunManifest.status 必须绑定同一 report version")
        # 顶层身份 DAG：构造 / 反序列化 / artifact 严格读回共用同一实现。
        _assert_run_identity_dag(self.run_identity, self.scope_manifest,
                                 self.report_version_identity)
        _sha256({"v": self.artifact_index_sha256}, "v", "DemoBackboneRunManifest"
                ".artifact_index_sha256")
        computed = self.compute_manifest_fingerprint()
        if self.manifest_fingerprint != computed:
            _err("DemoBackboneRunManifest.manifest_fingerprint 与当前内容不一致")
        if self.manifest_id != derive_run_manifest_id(computed):
            _err("DemoBackboneRunManifest.manifest_id 必须由 manifest_fingerprint 派生")

    def manifest_fingerprint_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_identity": self.run_identity.to_dict(),
            "scope_manifest": self.scope_manifest.to_dict(),
            "artifact_index_sha256": self.artifact_index_sha256,
            "formal_closure": self.formal_closure.to_dict(),
            "design_surfaces": self.design_surfaces.to_dict(),
            "report_version_identity": (self.report_version_identity.to_dict()
                                        if self.report_version_identity else None),
            "status": self.status.to_dict() if self.status else None,
        }

    def compute_manifest_fingerprint(self) -> str:
        body = self.manifest_fingerprint_body()
        leak = sorted(set(body) & set(RUN_MANIFEST_FINGERPRINT_EXCLUDE))
        if leak:
            _err(f"run manifest 指纹体含自身身份字段: {leak}")
        return sha256_canonical(body)

    def to_dict(self) -> dict:
        return {
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "created_at": self.created_at,
            **self.manifest_fingerprint_body(),
        }

    @classmethod
    def build(cls, *, run_identity: DSS.DemoRunIdentity,
              scope_manifest: DSS.ResolvedDemoScopeManifest, artifact_index_sha256: str,
              formal_closure: FormalPhaseClosureSnapshot,
              design_surfaces: DesignSurfaceMatrix,
              report_version_identity: ReportVersionIdentity | None = None,
              status: ReportStatusSnapshot | None = None,
              created_at: str = "") -> "DemoBackboneRunManifest":
        # 先做类型/前置校验再计算指纹：非法入参必须 fail-closed，不能先崩在
        # ``to_dict`` 这类属性访问上（否则错误类型会掩盖真实原因）。
        if not isinstance(run_identity, DSS.DemoRunIdentity):
            _err("DemoBackboneRunManifest.run_identity 必须为 planning.DemoRunIdentity")
        if not isinstance(scope_manifest, DSS.ResolvedDemoScopeManifest):
            _err("DemoBackboneRunManifest.scope_manifest 必须为 "
                 "planning.ResolvedDemoScopeManifest")
        if not isinstance(formal_closure, FormalPhaseClosureSnapshot):
            _err("DemoBackboneRunManifest.formal_closure 必须为 FormalPhaseClosureSnapshot")
        if not isinstance(design_surfaces, DesignSurfaceMatrix):
            _err("DemoBackboneRunManifest.design_surfaces 必须为 DesignSurfaceMatrix")
        if (report_version_identity is None) != (status is None):
            _err("DemoBackboneRunManifest 的 report_version_identity 与 status 必须同时存在或"
                 "同时为 None（不得编造空报告版本）")
        if report_version_identity is not None:
            if not isinstance(report_version_identity, ReportVersionIdentity):
                _err("report_version_identity 必须为 ReportVersionIdentity")
            if not isinstance(status, ReportStatusSnapshot):
                _err("status 必须为 ReportStatusSnapshot")
            if status.report_version != report_version_identity.report_version:
                _err("DemoBackboneRunManifest.status 必须绑定同一 report version")
        # 计算指纹**之前**就核验身份 DAG：不得先算出一个"合法"的 manifest 指纹再发现问题
        # （否则失败路径会留下可被误引用的指纹）。
        _assert_run_identity_dag(run_identity, scope_manifest, report_version_identity)
        body = {
            "schema_version": BACKBONE_SCHEMA_VERSION,
            "run_identity": run_identity.to_dict(),
            "scope_manifest": scope_manifest.to_dict(),
            "artifact_index_sha256": artifact_index_sha256,
            "formal_closure": formal_closure.to_dict(),
            "design_surfaces": design_surfaces.to_dict(),
            "report_version_identity": (report_version_identity.to_dict()
                                        if report_version_identity else None),
            "status": status.to_dict() if status else None,
        }
        fp = sha256_canonical(body)
        return cls(
            schema_version=BACKBONE_SCHEMA_VERSION,
            manifest_id=derive_run_manifest_id(fp),
            manifest_fingerprint=fp,
            run_identity=run_identity,
            scope_manifest=scope_manifest,
            artifact_index_sha256=artifact_index_sha256,
            formal_closure=formal_closure,
            design_surfaces=design_surfaces,
            report_version_identity=report_version_identity,
            status=status,
            created_at=created_at,
        )

    @classmethod
    def from_dict(cls, d: Any) -> "DemoBackboneRunManifest":
        d = _reject_unknown(d, set(cls.__dataclass_fields__), "DemoBackboneRunManifest")
        rvi = d.get("report_version_identity")
        st = d.get("status")
        return cls(
            schema_version=_str(d, "schema_version", "DemoBackboneRunManifest"),
            manifest_id=_str(d, "manifest_id", "DemoBackboneRunManifest"),
            manifest_fingerprint=_sha256(d, "manifest_fingerprint", "DemoBackboneRunManifest"),
            run_identity=DSS.DemoRunIdentity.from_dict(
                _as_dict(d.get("run_identity"), "DemoBackboneRunManifest", "run_identity")),
            scope_manifest=DSS.ResolvedDemoScopeManifest.from_dict(
                _as_dict(d.get("scope_manifest"), "DemoBackboneRunManifest", "scope_manifest")),
            artifact_index_sha256=_sha256(d, "artifact_index_sha256",
                                          "DemoBackboneRunManifest"),
            formal_closure=FormalPhaseClosureSnapshot.from_dict(
                _as_dict(d.get("formal_closure"), "DemoBackboneRunManifest", "formal_closure")),
            design_surfaces=DesignSurfaceMatrix.from_dict(
                _as_dict(d.get("design_surfaces"), "DemoBackboneRunManifest", "design_surfaces")),
            report_version_identity=(ReportVersionIdentity.from_dict(
                _as_dict(rvi, "DemoBackboneRunManifest", "report_version_identity"))
                if rvi is not None else None),
            status=(ReportStatusSnapshot.from_dict(
                _as_dict(st, "DemoBackboneRunManifest", "status")) if st is not None else None),
            created_at=_str_or_empty(d, "created_at", "DemoBackboneRunManifest"),
        )


def _as_dict(v: Any, typename: str, key: str) -> dict:
    if not isinstance(v, dict):
        _err(f"{typename}.{key} 必须为 dict，得到 {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """确定性自检：不读库、不写文件、不调 LLM / 网络。"""
    checks: list[dict] = []

    def _chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def _raises(fn) -> bool:
        """调用必须以 ``BackboneSchemaError`` fail-closed。"""
        try:
            fn()
        except BackboneSchemaError:
            return True
        return False

    plan_id = "dplan_" + "0" * 24
    rvi_kwargs = dict(
        profile_fingerprint="1" * 64,
        scope_input_fingerprint="2" * 64,
        projection_id="proj_" + "0" * 24,
        plan_id=plan_id,
        job_id="job_demo_backbone_0001",
        company_id="company_fixture_a",
        report_as_of="2026-06-30",
        selected_task_ids=("dtask_b", "dtask_a"),
        topic_pack_ids=("pack_2", "pack_1"),
        financial_fact_pack_artifact_id="ffp_" + "0" * 24,
        contract_fingerprint="a" * 64,
        source_policy_fingerprint="3" * 64,
        writing_spec_fingerprint="b" * 64,
        presentation_profile_fingerprint="4" * 64,
        dependency_fingerprint="5" * 64,
        body_fingerprint="c" * 64,
        section_draft_ids=("sdraft_b", "sdraft_a"),
        assembled_payload_fingerprint="f" * 64,
        narrative_schema_version="narrative-v1",
        claim_schema_version="claim-v1",
        table_schema_version="table-v1",
        writer_schema_version="writer-v1",
        assembler_schema_version="assembler-v1",
        claim_binding_gate_version="cbg-1",
        claim_entailment_rules_version="cer-1",
        prompt_version="prompt-v1",
        model_policy_id="demo_model_policy_v1",
    )
    rvi = ReportVersionIdentity.build(**rvi_kwargs)
    _chk("report_version.derived", rvi.report_version.startswith("rv_"), rvi.report_version)
    _chk("report_version.ids_normalized",
         rvi.selected_task_ids == ("dtask_a", "dtask_b")
         and rvi.topic_pack_ids == ("pack_1", "pack_2")
         and rvi.section_draft_ids == ("sdraft_a", "sdraft_b"), "")
    _chk("report_version.roundtrip",
         ReportVersionIdentity.from_dict(rvi.to_dict()).to_dict() == rvi.to_dict(), "")

    # (10) 逐类内容依赖变化 → report version 变化（每类一个反例）
    for field, value in (
            ("topic_pack_ids", ("pack_3",)),
            ("body_fingerprint", "d" * 64),
            ("scope_input_fingerprint", "9" * 64),
            ("selected_task_ids", ("dtask_c",)),
            ("financial_fact_pack_artifact_id", "ffp_" + "1" * 24),
            ("contract_fingerprint", "e" * 64),
            ("prompt_version", "prompt-v2"),
            ("model_policy_id", "demo_model_policy_v2"),
            ("writer_schema_version", "writer-v2"),
            ("assembler_schema_version", "assembler-v2"),
            ("presentation_profile_fingerprint", "6" * 64),
            ("claim_binding_gate_version", "cbg-2"),
            ("claim_entailment_rules_version", "cer-2"),
            ("section_draft_ids", ("sdraft_c",)),
            ("assembled_payload_fingerprint", "0" * 64),
    ):
        changed = ReportVersionIdentity.build(**{**rvi_kwargs, field: value})
        _chk(f"report_version.change_changes_version:{field}",
             changed.report_version != rvi.report_version, "")

    # (10b) legacy 身份对象只读：current reader 拒 v1 / v2 载荷；legacy reader 按**逐字
    #       固定**的该版字段集重算（绝不掺入 v3 新键）。
    def _legacy_payload(fields: tuple[str, ...], marker: str) -> dict:
        source = rvi.to_dict()
        body = {k: (list(source[k]) if k in ("selected_task_ids", "topic_pack_ids")
                    else source[k]) for k in fields}
        content = sha256_canonical(body)
        return {**body, "schema_version": marker, "content_fingerprint": content,
                "report_version": derive_report_version(content)}

    v1_payload = _legacy_payload(LEGACY_REPORT_VERSION_V1_FIELDS,
                                 LegacyReportVersionIdentity.LEGACY_SCHEMA_VERSION)
    v2_payload = _legacy_payload(LEGACY_REPORT_VERSION_V2_FIELDS,
                                 "demo-report-version-v2")
    _chk("report_version.current_reader_rejects_v1",
         _raises(lambda: ReportVersionIdentity.from_dict(v1_payload)), "")
    _chk("report_version.current_reader_rejects_v2",
         _raises(lambda: ReportVersionIdentity.from_dict(v2_payload)), "")
    _chk("report_version.v1_readonly_view",
         load_legacy_report_version_for_audit(v1_payload).recompute_fingerprint()
         == v1_payload["content_fingerprint"]
         and len(LEGACY_REPORT_VERSION_V1_FIELDS)
         == len(REPORT_VERSION_FINGERPRINT_FIELDS) - 4, "")
    _chk("report_version.v2_readonly_view",
         load_legacy_report_version_for_audit(v2_payload).recompute_fingerprint()
         == v2_payload["content_fingerprint"]
         and len(LEGACY_REPORT_VERSION_V2_FIELDS)
         == len(REPORT_VERSION_FINGERPRINT_FIELDS) - 2, "")
    _chk("report_version.legacy_field_sets_pinned",
         set(LEGACY_REPORT_VERSION_V1_FIELDS) < set(LEGACY_REPORT_VERSION_V2_FIELDS)
         and not (set(LEGACY_REPORT_VERSION_V2_FIELDS)
                  & {"section_draft_ids", "assembled_payload_fingerprint"}), "")
    _chk("report_version.legacy_reader_rejects_current",
         _raises(lambda: load_legacy_report_version_for_audit(rvi.to_dict())), "")

    # (9) 白名单与禁入名单不相交
    _chk("report_version.allowlist_disjoint",
         not (set(REPORT_VERSION_FINGERPRINT_FIELDS) & set(REPORT_VERSION_FORBIDDEN_FIELDS)), "")

    # (1) 报告形成前只允许 run-bound 快照；它没有 report_version 字段
    prog = RunProgressSnapshot(
        schema_version=RUN_PROGRESS_SCHEMA_VERSION, run_id="demo_backbone_" + "0" * 12,
        attempt=1, phase_id="preflight", phase_status="RUNNING")
    _chk("progress.run_bound_only",
         RunProgressSnapshot.from_dict(prog.to_dict()).to_dict() == prog.to_dict()
         and "report_version" not in prog.to_dict(), "")

    # (3)(6)(7)(8) 四状态共享同一 report version 且互不自动映射
    snap = ReportStatusSnapshot.build(
        rvi,
        process_completion=ProcessCompletionStatus("COMPLETED"),
        preview_availability=PreviewAvailabilityStatus("AVAILABLE"),
        system_assurance=SystemAssuranceStatus("FAILED"),
    )
    _chk("status.four_states_share_one_version",
         snap.report_version == rvi.report_version
         and snap.to_dict()["preview_availability"]["status"] == "AVAILABLE"
         and snap.to_dict()["system_assurance"]["status"] == "FAILED", "")
    _chk("status.human_defaults_not_reviewed",
         snap.human_acceptance.status == HUMAN_ACCEPTANCE_DEFAULT, "")
    # (4) 四状态词表完整：wire 可读回未来独立人工流程写回的结论
    _chk("status.preview_vocabulary",
         PREVIEW_AVAILABILITY_STATUSES == ("UNAVAILABLE", "PARTIAL", "AVAILABLE"), "")
    _chk("status.assurance_vocabulary",
         SYSTEM_ASSURANCE_STATUSES == ("NOT_RUN", "PASSED", "FAILED", "BLOCKED"), "")
    _chk("status.human_vocabulary_roundtrip",
         all(HumanAcceptanceStatus.from_dict(HumanAcceptanceStatus(s).to_dict()).status == s
             for s in HUMAN_ACCEPTANCE_STATUSES), str(HUMAN_ACCEPTANCE_STATUSES))
    _chk("status.preview_not_equals_assurance",
         PreviewAvailabilityStatus("AVAILABLE").status
         != SystemAssuranceStatus("PASSED").status, "")
    _chk("status.statuses_are_distinct_types",
         len({type(snap.process_completion), type(snap.preview_availability),
              type(snap.system_assurance), type(snap.human_acceptance)}) == 4, "")

    # (5) 正式阶段关闭只读、与 report version 正交
    closure = FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "open",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-09-20"},),
        source_documents=({"path": "V2_TODO.md", "sha256": "7" * 64},),
        recorded_at="2026-09-20")
    _chk("formal_closure.read_only_orthogonal",
         closure.derived_by_runtime is False
         and "report_version" not in closure.to_dict()
         and FormalPhaseClosureSnapshot.from_dict(closure.to_dict()).to_dict()
         == closure.to_dict(), closure.snapshot_id)
    _chk("formal_closure.not_an_assurance_input",
         "assurance" not in json.dumps(closure.to_dict(), ensure_ascii=False).lower(), "")
    # 同一路径、文档内容（SHA256）变化 → 快照身份必须变化
    moved = FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "open",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-09-20"},),
        source_documents=({"path": "V2_TODO.md", "sha256": "8" * 64},),
        recorded_at="2026-09-20")
    _chk("formal_closure.doc_hash_change_changes_identity",
         moved.snapshot_id != closure.snapshot_id, "")

    matrix = DesignSurfaceMatrix.build((
        DesignSurfaceRecord(surface_id="ds.state_separation", axis="design_surface_coverage",
                            title="四状态与治理快照分离", status="demonstrated",
                            evidence_kind="artifact", evidence_refs=("backbone_schema.py",)),
    ))
    _chk("design_surface.missing_visible",
         len(matrix.missing_surface_ids()) == len(DESIGN_SURFACE_IDS) - 1
         and matrix.status_counts()["demonstrated"] == 1,
         f"missing={len(matrix.missing_surface_ids())}")

    ok = all(c["ok"] for c in checks)
    return {
        "module": "sections.backbone_schema",
        "ok": ok,
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main(sys.argv[1:]))

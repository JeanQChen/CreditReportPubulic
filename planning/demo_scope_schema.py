"""M930-1：DemoScope / 运行身份 / Contract v2 受控投影的**唯一**公共 wire 所有权。

本模块只定义下列五个公共 wire 类型，不得在别处重复定义：

1. ``DemoScopeProfile``            —— 公司无关、版本化的「选什么」策略；
2. ``DemoScopeInputManifest``      —— 研究**前**可知的稳定业务输入身份；
3. ``DemoRunIdentity``             —— 一次运行尝试的操作性身份；
4. ``DemoPlanningProjection``      —— Contract v2 的受控投影（plan/task/requirement）；
5. ``ResolvedDemoScopeManifest``   —— 运行**后**的版本化 run record。

依赖方向与边界（M930-1 任务书 §五 / 实施计划 §4.4）：

- ``planning/demo_scope.py`` 只实现 build/load/project/verify 函数并 import 本模块类型；
- ``sections/backbone_schema.py`` 只持有报告/状态/artifact 类并**引用**本模块对象 ID，
  不得重复定义 scope/projection/run-identity 类型；
- **禁止 ``planning → sections`` 反向依赖**。本模块只 import 纯声明式 schema：
  ``contracts.schema_v2``（无 I/O）、``planning.schema``（同包）、``harness.topic_schema``
  （纯声明式、无 I/O / 无 runtime / 无 Router / 无 Store）。绝不 import
  ``harness.runtime`` / ``harness.topic_store`` / 任何 ``sections.*``。

身份设计要点：

- 三层身份严格分离。``run_id/attempt/started_at/results_root`` 只属于
  ``DemoRunIdentity``，绝不进入 ``scope_input_fingerprint`` / ``projection_id`` /
  ``plan_id`` / ``task_id``。
- ``job_id`` 是现行 ``ReportPlan``/Section Store 的**稳定报告任务身份**，进入
  ``scope_input_fingerprint`` 与 ``plan_id``。
- 所有指纹用封闭字段白名单 + 规范 JSON；任何未知/缺失/漂移都 fail-closed。
- 后置 runtime output（Pack / ExternalSnapshot / gap）只能进入
  ``ResolvedDemoScopeManifest``，不得回填并改变 input manifest / plan / task identity。

本模块不 I/O、不写库、不调 LLM / 网络 / Router / ToolRegistry，只被
``planning/demo_scope.py``、``sections/backbone_schema.py`` 与离线测试引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts import schema_v2 as CS2
from planning import schema as PS
from harness.topic_schema import (
    SchemaValidationError,
    TopicAspectRequirementSnapshot,
    TopicResearchRequirement,
    canonical_json,
    sha256_canonical,
)

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

DEMO_SCOPE_PROFILE_SCHEMA_VERSION = "demo-scope-profile-v1"
DEMO_SCOPE_INPUT_MANIFEST_SCHEMA_VERSION = "demo-scope-input-v1"
DEMO_RUN_IDENTITY_SCHEMA_VERSION = "demo-run-identity-v1"
DEMO_PLANNING_PROJECTION_SCHEMA_VERSION = "demo-planning-projection-v1"
RESOLVED_DEMO_SCOPE_MANIFEST_SCHEMA_VERSION = "resolved-demo-scope-v1"

# ID 派生规则版本（显式版本化；不改动全局 `derive_plan_id()` / `derive_task_id()` 语义）。
DEMO_PROJECTION_ID_RULE_VERSION = "demo-projection-rule-v1"
DEMO_PLAN_ID_RULE_VERSION = "demo-plan-rule-v1"
DEMO_TASK_ID_RULE_VERSION = "demo-task-rule-v1"
DEMO_PLANNER_VERSION = "demo-backbone-planner-v1"

# 投影只接受 Contract v2；`standard_v2.yaml`（v1）必须 fail-closed。
REQUIRED_CONTRACT_VERSION = CS2.CONTRACT_VERSION_V2

# 四类冻结资产（依赖校验的最小闭合集合）。
SCOPE_ASSET_KEYS = ("contract", "source_policy", "writing_spec", "presentation_profile")

# 资产生命周期状态（与冻结资产一致的 fail-closed 三态）。
LIFECYCLE_STATUSES = ("candidate", "approved", "frozen")

# 研究**前**必须冻结的底稿 policy / algorithm 版本键（封闭集合，不允许任意语义 dict）。
SUBSTRATE_DEPENDENCY_KEYS = (
    "layout_algorithm",
    "layout_policy",
    "alignment_algorithm",
    "alignment_policy",
    "outline_algorithm",
    "outline_policy",
    "span_algorithm",
    "span_policy",
    "evidence_store",
)

# ---------------------------------------------------------------------------
# 指纹字段白名单（闭集合；任何未列出的字段都不进指纹）
# ---------------------------------------------------------------------------

# profile 指纹排除的生命周期/指纹自身字段（镜像冻结资产的 `_FREEZE_EXCLUDE_KEYS`）。
# 公开导出：`planning/demo_scope.py` 必须以同一排除集计算源 YAML 的指纹，
# 否则同一 profile 会算出两个指纹。
PROFILE_FINGERPRINT_EXCLUDE = ("status", "created_at", "approved_at", "frozen_at",
                               "profile_fingerprint")

# `scope_input_fingerprint` 的封闭白名单：只含研究前可知的稳定业务输入。
# 显式排除 schema_version：wire 版本变化由 `code_fingerprint` 表达，
# 不改变同一业务输入的 scope 身份。
SCOPE_INPUT_FINGERPRINT_FIELDS = (
    "profile_id",
    "profile_version",
    "profile_fingerprint",
    "job_id",
    "case_input_id",
    "company_id",
    "company_name",
    "credit_type",
    "report_as_of",
    "document_id",
    "document_version",
    "raw_pdf_sha256",
    "current_evidence_set_version",
    "substrate_dependency_versions",
    "financial_snapshot_id",
    "external_policy_snapshot_id",
    "budget_policy_id",
    "budget_policy_version",
    "model_policy_id",
    "code_fingerprint",
    "selected_section_ids",
    "selected_topic_ids",
    "selected_question_ids",
    "selected_aspect_ids",
    "out_of_scope_section_ids",
    "out_of_scope_topic_ids",
    "out_of_scope_question_ids",
    "out_of_scope_aspect_ids",
)

# 绝不允许进入 `scope_input_fingerprint` 的操作性字段（反例测试逐项断言）。
SCOPE_INPUT_FORBIDDEN_FIELDS = (
    "run_id",
    "attempt",
    "started_at",
    "generated_at",
    "updated_at",
    "call_id",
    "request_id",
    "results_root",
    "results_dir",
    "run_dir",
    "artifact_root_id",
    "report_version",
    "manifest_id",
)

# `projection_id` 的封闭白名单（**不含** report_plan / requirements，避免身份环）。
PROJECTION_ID_FIELDS = (
    "projection_rule_version",
    "profile_fingerprint",
    "scope_input_fingerprint",
    "contract_asset",
    "contract_version",
    "contract_fingerprint",
    "source_policy_id",
    "source_policy_version",
    "source_policy_fingerprint",
    "writing_spec_id",
    "writing_spec_version",
    "writing_spec_fingerprint",
    "presentation_profile_id",
    "presentation_profile_version",
    "presentation_profile_fingerprint",
    "dependency_fingerprint",
)

# `ResolvedDemoScopeManifest` 的 run 指纹排除自身身份字段。
_RESOLVED_MANIFEST_FINGERPRINT_EXCLUDE = ("manifest_id", "manifest_fingerprint")

# 显式 gap 记录的封闭键（不是自由 dict）。
RESOLVED_GAP_KEYS = (
    "gap_id",
    "target_kind",
    "target_id",
    "reason_code",
    "searched_scope",
    "impact",
    "suggested_material_type",
)

GAP_TARGET_KINDS = ("section", "topic", "question", "aspect", "document",
                    "table", "external", "financial_note")

# 投影中每条 aspect 的写作用法快照键（WritingSpec role 不可变快照）。
ASPECT_WRITING_BINDING_KEYS = (
    "aspect_id",
    "section_id",
    "topic_id",
    "question_id",
    "producer_kind",
    "execution_path",
    "kind",
    "display_tier",
    "content_role",
    "missing_policy",
    "blocking_policy",
    "impact_scope",
    "requirement_required",
    "writing_spec_role",
    "writing_spec_subsection_id",
    "writing_spec_gap_display_policy",
    "writing_spec_citation_granularity",
    "writing_spec_table_schema",
)


# ---------------------------------------------------------------------------
# canonical 序列化 / 严格解码助手
#
# canonical JSON 与 sha256 直接复用 `harness.topic_schema`（不新建第二套序列化框架）；
# 下列 from_dict 助手只镜像该模块既有的 fail-closed 风格。
# ---------------------------------------------------------------------------

def _unknown(d: Any, allowed: set[str], typename: str) -> dict:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"{typename}.from_dict 需要 dict，得到 {type(d).__name__}")
    extra = set(d) - allowed
    if extra:
        raise SchemaValidationError(f"{typename} 含未知字段: {sorted(extra)}")
    return d


def _str(d: dict, key: str, typename: str, *, none: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        if none:
            return None
        raise SchemaValidationError(f"{typename} 缺必填字段: {key}")
    if not isinstance(v, str):
        raise SchemaValidationError(f"{typename}.{key} 必须为字符串，得到 {type(v).__name__}")
    if v == "":
        raise SchemaValidationError(f"{typename}.{key} 必须为非空字符串")
    return v


def _str_or_empty(d: dict, key: str, typename: str) -> str:
    v = d.get(key, "")
    if v is None:
        return ""
    if not isinstance(v, str):
        raise SchemaValidationError(f"{typename}.{key} 必须为字符串，得到 {type(v).__name__}")
    return v


def _bool(d: dict, key: str, typename: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise SchemaValidationError(f"{typename}.{key} 必须为 bool，得到 {v!r}")
    return v


def _int(d: dict, key: str, typename: str) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        raise SchemaValidationError(f"{typename}.{key} 必须为 int，得到 {v!r}")
    return v


def _str_tuple(d: dict, key: str, typename: str,
               default: tuple[str, ...] = ()) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        return default
    if not isinstance(v, list):
        raise SchemaValidationError(f"{typename}.{key} 必须为 list[str]，得到 {type(v).__name__}")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or x == "":
            raise SchemaValidationError(f"{typename}.{key} 含非法元素: {x!r}")
        out.append(x)
    return tuple(out)


def _dict_of_str(d: dict, key: str, typename: str) -> dict[str, str]:
    v = d.get(key, {})
    if not isinstance(v, dict):
        raise SchemaValidationError(f"{typename}.{key} 必须为 dict，得到 {type(v).__name__}")
    out: dict[str, str] = {}
    for k, x in v.items():
        if not isinstance(k, str) or k == "":
            raise SchemaValidationError(f"{typename}.{key} 含非法键 {k!r}")
        if not isinstance(x, str) or x == "":
            raise SchemaValidationError(f"{typename}.{key}[{k}] 必须为非空字符串")
        out[k] = x
    return out


def _dict_of_str_tuple(d: dict, key: str, typename: str) -> dict[str, tuple[str, ...]]:
    v = d.get(key, {})
    if not isinstance(v, dict):
        raise SchemaValidationError(f"{typename}.{key} 必须为 dict，得到 {type(v).__name__}")
    out: dict[str, tuple[str, ...]] = {}
    for k, x in v.items():
        if not isinstance(k, str) or k == "":
            raise SchemaValidationError(f"{typename}.{key} 含非法键 {k!r}")
        if not isinstance(x, list) or not x:
            raise SchemaValidationError(f"{typename}.{key}[{k}] 必须为非空 list[str]")
        vals: list[str] = []
        for y in x:
            if not isinstance(y, str) or y == "":
                raise SchemaValidationError(f"{typename}.{key}[{k}] 含非法元素 {y!r}")
            vals.append(y)
        out[k] = tuple(vals)
    return out


def _closed_dict(d: dict, key: str, typename: str, allowed: tuple[str, ...],
                 required: tuple[str, ...]) -> dict:
    v = d.get(key)
    if not isinstance(v, dict):
        raise SchemaValidationError(f"{typename}.{key} 必须为 dict，得到 {type(v).__name__}")
    extra = set(v) - set(allowed)
    if extra:
        raise SchemaValidationError(f"{typename}.{key} 含未知字段: {sorted(extra)}")
    missing = [k for k in required if k not in v]
    if missing:
        raise SchemaValidationError(f"{typename}.{key} 缺必填字段: {missing}")
    return dict(v)


def validate_substrate_dependency_versions(d: Any) -> dict[str, str]:
    """底稿版本字典必须是封闭键集，且每项非空字符串。"""
    if not isinstance(d, dict):
        raise SchemaValidationError(f"substrate_dependency_versions 必须为 dict，得到 {type(d).__name__}")
    out: dict[str, str] = {}
    for k, v in d.items():
        if k not in SUBSTRATE_DEPENDENCY_KEYS:
            raise SchemaValidationError(
                f"substrate_dependency_versions 未知键 {k!r}（允许 {SUBSTRATE_DEPENDENCY_KEYS}）")
        if not isinstance(v, str) or v == "":
            raise SchemaValidationError(f"substrate_dependency_versions[{k}] 必须为非空字符串")
        out[k] = v
    missing = [k for k in SUBSTRATE_DEPENDENCY_KEYS if k not in out]
    if missing:
        raise SchemaValidationError(f"substrate_dependency_versions 缺键: {missing}")
    return dict(sorted(out.items()))


def _is_sha256_hex(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _require_sha256(v: str, typename: str, key: str) -> str:
    if not _is_sha256_hex(v):
        raise SchemaValidationError(f"{typename}.{key} 必须为 64 位 sha256 hex，得到 {v!r}")
    return v


def require_rel_asset_path(v: str, typename: str, key: str) -> str:
    """冻结资产路径必须是仓库相对 POSIX 路径（拒绝绝对路径 / ``..`` / 反斜杠 / 盘符）。

    这样 profile 指纹跨机器稳定，且不可能指向仓库外文件。
    """
    if not isinstance(v, str) or v == "":
        raise SchemaValidationError(f"{typename}.{key} 必须为非空字符串")
    if "\\" in v:
        raise SchemaValidationError(f"{typename}.{key} 必须使用 POSIX 分隔符，得到 {v!r}")
    if v.startswith("/") or (len(v) > 1 and v[1] == ":"):
        raise SchemaValidationError(f"{typename}.{key} 不得为绝对路径，得到 {v!r}")
    parts = v.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise SchemaValidationError(f"{typename}.{key} 含非法路径段，得到 {v!r}")
    return v


def _assert_disjoint(a: tuple[str, ...], b: tuple[str, ...],
                     typename: str, level: str) -> None:
    dup = sorted(set(a) & set(b))
    if dup:
        raise SchemaValidationError(f"{typename} 的 {level} selected/out_of_scope 重叠: {dup}")
    for name, seq in (("selected", a), ("out_of_scope", b)):
        if len(set(seq)) != len(seq):
            raise SchemaValidationError(f"{typename} 的 {level} {name} 含重复 ID")


# ---------------------------------------------------------------------------
# 1. DemoScopeProfile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoScopeProfile:
    """公司无关、版本化的 Demo 范围选择策略（载体：templates/demo_scopes/*.yaml）。

    - selected / out_of_scope 在 section/topic/question/aspect 四级都必须精确、互斥；
      「是否恰好覆盖冻结 Contract」由 ``planning.demo_scope.verify_demo_projection()``
      对冻结 Contract 校验（本类型只保证自洽）。
    - 每个被选中的 ID 都必须有 ``selection_reason_by_id`` 与 ``design_surface_ids_by_id``。
    - ``company_independent`` 恒为 True：profile 不得含公司名/证券代码/固定页码/表号/
      Evidence ID/gold/答案关键词特判。
    """

    schema_version: str
    profile_id: str
    profile_version: str
    status: str
    created_at: str
    approved_at: str | None
    frozen_at: str | None
    contract_asset: str
    contract_version: str
    contract_fingerprint: str
    source_policy_asset: str
    source_policy_id: str
    source_policy_version: str
    source_policy_fingerprint: str
    writing_spec_asset: str
    writing_spec_id: str
    writing_spec_version: str
    writing_spec_fingerprint: str
    presentation_profile_asset: str
    presentation_profile_id: str
    presentation_profile_version: str
    presentation_profile_fingerprint: str
    selected_section_ids: tuple[str, ...]
    selected_topic_ids: tuple[str, ...]
    selected_question_ids: tuple[str, ...]
    selected_aspect_ids: tuple[str, ...]
    out_of_scope_section_ids: tuple[str, ...]
    out_of_scope_topic_ids: tuple[str, ...]
    out_of_scope_question_ids: tuple[str, ...]
    out_of_scope_aspect_ids: tuple[str, ...]
    selection_reason_by_id: dict[str, str]
    design_surface_ids_by_id: dict[str, tuple[str, ...]]
    material_capabilities: tuple[str, ...]
    fallback_policy_id: str
    budget_policy_id: str
    budget_policy_version: str
    company_independent: bool
    profile_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != DEMO_SCOPE_PROFILE_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"DemoScopeProfile.schema_version 必须为 {DEMO_SCOPE_PROFILE_SCHEMA_VERSION!r}")
        if self.status not in LIFECYCLE_STATUSES:
            raise SchemaValidationError(f"DemoScopeProfile.status 非法: {self.status!r}")
        if self.contract_version != REQUIRED_CONTRACT_VERSION:
            raise SchemaValidationError(
                f"DemoScopeProfile 只接受 Contract {REQUIRED_CONTRACT_VERSION!r}，"
                f"得到 {self.contract_version!r}")
        for key, val in (("contract_fingerprint", self.contract_fingerprint),
                         ("source_policy_fingerprint", self.source_policy_fingerprint),
                         ("writing_spec_fingerprint", self.writing_spec_fingerprint),
                         ("presentation_profile_fingerprint",
                          self.presentation_profile_fingerprint)):
            _require_sha256(val, "DemoScopeProfile", key)
        for key in ("contract_asset", "source_policy_asset", "writing_spec_asset",
                    "presentation_profile_asset"):
            require_rel_asset_path(getattr(self, key), "DemoScopeProfile", key)
        if not self.company_independent:
            raise SchemaValidationError("DemoScopeProfile.company_independent 必须为 True")
        for level, sel, out in (("section", self.selected_section_ids, self.out_of_scope_section_ids),
                                ("topic", self.selected_topic_ids, self.out_of_scope_topic_ids),
                                ("question", self.selected_question_ids, self.out_of_scope_question_ids),
                                ("aspect", self.selected_aspect_ids, self.out_of_scope_aspect_ids)):
            if not sel:
                raise SchemaValidationError(f"DemoScopeProfile 的 selected {level} 不能为空")
            _assert_disjoint(sel, out, "DemoScopeProfile", level)
        selected_ids = (set(self.selected_section_ids) | set(self.selected_topic_ids)
                        | set(self.selected_question_ids) | set(self.selected_aspect_ids))
        if set(self.selection_reason_by_id) != selected_ids:
            raise SchemaValidationError(
                "DemoScopeProfile.selection_reason_by_id 的键必须恰好等于全部 selected ID 集合"
                f"（差集 {sorted(selected_ids ^ set(self.selection_reason_by_id))[:8]}）")
        if set(self.design_surface_ids_by_id) != selected_ids:
            raise SchemaValidationError(
                "DemoScopeProfile.design_surface_ids_by_id 的键必须恰好等于全部 selected ID 集合")
        if not self.material_capabilities:
            raise SchemaValidationError("DemoScopeProfile.material_capabilities 不能为空")
        if not self.fallback_policy_id or not self.budget_policy_id or not self.budget_policy_version:
            raise SchemaValidationError("DemoScopeProfile 的 policy ID/version 不能为空")
        declared = self.profile_fingerprint
        _require_sha256(declared, "DemoScopeProfile", "profile_fingerprint")
        computed = self.compute_profile_fingerprint()
        if declared != computed:
            raise SchemaValidationError(
                f"DemoScopeProfile.profile_fingerprint 与当前内容不一致（声明 {declared} "
                f"≠ 计算 {computed}）")

    def profile_fingerprint_body(self) -> dict:
        """profile 指纹规范体（排除生命周期元数据与指纹自身）。"""
        d = self.to_dict()
        return {k: v for k, v in d.items() if k not in PROFILE_FINGERPRINT_EXCLUDE}

    def compute_profile_fingerprint(self) -> str:
        return compute_demo_scope_profile_fingerprint(self.profile_fingerprint_body())

    def selected_id_sets(self) -> dict[str, tuple[str, ...]]:
        return {
            "section": self.selected_section_ids,
            "topic": self.selected_topic_ids,
            "question": self.selected_question_ids,
            "aspect": self.selected_aspect_ids,
        }

    def out_of_scope_id_sets(self) -> dict[str, tuple[str, ...]]:
        return {
            "section": self.out_of_scope_section_ids,
            "topic": self.out_of_scope_topic_ids,
            "question": self.out_of_scope_question_ids,
            "aspect": self.out_of_scope_aspect_ids,
        }

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "status": self.status,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "frozen_at": self.frozen_at,
            "contract_asset": self.contract_asset,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_asset": self.source_policy_asset,
            "source_policy_id": self.source_policy_id,
            "source_policy_version": self.source_policy_version,
            "source_policy_fingerprint": self.source_policy_fingerprint,
            "writing_spec_asset": self.writing_spec_asset,
            "writing_spec_id": self.writing_spec_id,
            "writing_spec_version": self.writing_spec_version,
            "writing_spec_fingerprint": self.writing_spec_fingerprint,
            "presentation_profile_asset": self.presentation_profile_asset,
            "presentation_profile_id": self.presentation_profile_id,
            "presentation_profile_version": self.presentation_profile_version,
            "presentation_profile_fingerprint": self.presentation_profile_fingerprint,
            "selected_section_ids": list(self.selected_section_ids),
            "selected_topic_ids": list(self.selected_topic_ids),
            "selected_question_ids": list(self.selected_question_ids),
            "selected_aspect_ids": list(self.selected_aspect_ids),
            "out_of_scope_section_ids": list(self.out_of_scope_section_ids),
            "out_of_scope_topic_ids": list(self.out_of_scope_topic_ids),
            "out_of_scope_question_ids": list(self.out_of_scope_question_ids),
            "out_of_scope_aspect_ids": list(self.out_of_scope_aspect_ids),
            "selection_reason_by_id": dict(self.selection_reason_by_id),
            "design_surface_ids_by_id": {
                k: list(v) for k, v in self.design_surface_ids_by_id.items()},
            "material_capabilities": list(self.material_capabilities),
            "fallback_policy_id": self.fallback_policy_id,
            "budget_policy_id": self.budget_policy_id,
            "budget_policy_version": self.budget_policy_version,
            "company_independent": self.company_independent,
            "profile_fingerprint": self.profile_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DemoScopeProfile":
        allowed = set(cls.__dataclass_fields__)
        d = _unknown(d, allowed, "DemoScopeProfile")
        return cls(
            schema_version=_str(d, "schema_version", "DemoScopeProfile"),
            profile_id=_str(d, "profile_id", "DemoScopeProfile"),
            profile_version=_str(d, "profile_version", "DemoScopeProfile"),
            status=_str(d, "status", "DemoScopeProfile"),
            created_at=_str(d, "created_at", "DemoScopeProfile"),
            approved_at=_str(d, "approved_at", "DemoScopeProfile", none=True),
            frozen_at=_str(d, "frozen_at", "DemoScopeProfile", none=True),
            contract_asset=_str(d, "contract_asset", "DemoScopeProfile"),
            contract_version=_str(d, "contract_version", "DemoScopeProfile"),
            contract_fingerprint=_str(d, "contract_fingerprint", "DemoScopeProfile"),
            source_policy_asset=_str(d, "source_policy_asset", "DemoScopeProfile"),
            source_policy_id=_str(d, "source_policy_id", "DemoScopeProfile"),
            source_policy_version=_str(d, "source_policy_version", "DemoScopeProfile"),
            source_policy_fingerprint=_str(d, "source_policy_fingerprint", "DemoScopeProfile"),
            writing_spec_asset=_str(d, "writing_spec_asset", "DemoScopeProfile"),
            writing_spec_id=_str(d, "writing_spec_id", "DemoScopeProfile"),
            writing_spec_version=_str(d, "writing_spec_version", "DemoScopeProfile"),
            writing_spec_fingerprint=_str(d, "writing_spec_fingerprint", "DemoScopeProfile"),
            presentation_profile_asset=_str(d, "presentation_profile_asset", "DemoScopeProfile"),
            presentation_profile_id=_str(d, "presentation_profile_id", "DemoScopeProfile"),
            presentation_profile_version=_str(d, "presentation_profile_version", "DemoScopeProfile"),
            presentation_profile_fingerprint=_str(
                d, "presentation_profile_fingerprint", "DemoScopeProfile"),
            selected_section_ids=_str_tuple(d, "selected_section_ids", "DemoScopeProfile"),
            selected_topic_ids=_str_tuple(d, "selected_topic_ids", "DemoScopeProfile"),
            selected_question_ids=_str_tuple(d, "selected_question_ids", "DemoScopeProfile"),
            selected_aspect_ids=_str_tuple(d, "selected_aspect_ids", "DemoScopeProfile"),
            out_of_scope_section_ids=_str_tuple(d, "out_of_scope_section_ids", "DemoScopeProfile"),
            out_of_scope_topic_ids=_str_tuple(d, "out_of_scope_topic_ids", "DemoScopeProfile"),
            out_of_scope_question_ids=_str_tuple(d, "out_of_scope_question_ids", "DemoScopeProfile"),
            out_of_scope_aspect_ids=_str_tuple(d, "out_of_scope_aspect_ids", "DemoScopeProfile"),
            selection_reason_by_id=_dict_of_str(d, "selection_reason_by_id", "DemoScopeProfile"),
            design_surface_ids_by_id=_dict_of_str_tuple(
                d, "design_surface_ids_by_id", "DemoScopeProfile"),
            material_capabilities=_str_tuple(d, "material_capabilities", "DemoScopeProfile"),
            fallback_policy_id=_str(d, "fallback_policy_id", "DemoScopeProfile"),
            budget_policy_id=_str(d, "budget_policy_id", "DemoScopeProfile"),
            budget_policy_version=_str(d, "budget_policy_version", "DemoScopeProfile"),
            company_independent=_bool(d, "company_independent", "DemoScopeProfile"),
            profile_fingerprint=_str(d, "profile_fingerprint", "DemoScopeProfile"),
        )


def compute_demo_scope_profile_fingerprint(body: dict) -> str:
    """对 profile 规范体求 sha256（调用方须先移除生命周期/指纹字段）。"""
    return sha256_canonical(body)


# ---------------------------------------------------------------------------
# 2. DemoScopeInputManifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoScopeInputManifest:
    """研究**前**可知的稳定业务输入身份（创建后不可变）。

    - 只引用 profile 的 id/version/fingerprint，不拷贝 profile 全字段；
    - ``job_id`` 是现行 ``ReportPlan``/Section Store 的稳定报告任务身份；
    - 不得预造尚未构建的 PageLayout / Alignment / Outline / Span 实际 ID
      （那些是运行输出，只能出现在 ``ResolvedDemoScopeManifest``）。
    """

    schema_version: str
    profile_id: str
    profile_version: str
    profile_fingerprint: str
    job_id: str
    case_input_id: str
    company_id: str
    company_name: str
    credit_type: str
    report_as_of: str
    document_id: str
    document_version: str
    raw_pdf_sha256: str
    current_evidence_set_version: str
    substrate_dependency_versions: dict[str, str]
    financial_snapshot_id: str | None
    external_policy_snapshot_id: str | None
    budget_policy_id: str
    budget_policy_version: str
    model_policy_id: str
    code_fingerprint: str
    selected_section_ids: tuple[str, ...]
    selected_topic_ids: tuple[str, ...]
    selected_question_ids: tuple[str, ...]
    selected_aspect_ids: tuple[str, ...]
    out_of_scope_section_ids: tuple[str, ...]
    out_of_scope_topic_ids: tuple[str, ...]
    out_of_scope_question_ids: tuple[str, ...]
    out_of_scope_aspect_ids: tuple[str, ...]
    scope_input_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != DEMO_SCOPE_INPUT_MANIFEST_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"DemoScopeInputManifest.schema_version 必须为 "
                f"{DEMO_SCOPE_INPUT_MANIFEST_SCHEMA_VERSION!r}")
        _require_sha256(self.profile_fingerprint, "DemoScopeInputManifest", "profile_fingerprint")
        _require_sha256(self.raw_pdf_sha256, "DemoScopeInputManifest", "raw_pdf_sha256")
        _require_sha256(self.code_fingerprint, "DemoScopeInputManifest", "code_fingerprint")
        validate_substrate_dependency_versions(self.substrate_dependency_versions)
        for level, sel, out in (("section", self.selected_section_ids, self.out_of_scope_section_ids),
                                ("topic", self.selected_topic_ids, self.out_of_scope_topic_ids),
                                ("question", self.selected_question_ids, self.out_of_scope_question_ids),
                                ("aspect", self.selected_aspect_ids, self.out_of_scope_aspect_ids)):
            if not sel:
                raise SchemaValidationError(f"DemoScopeInputManifest 的 selected {level} 不能为空")
            _assert_disjoint(sel, out, "DemoScopeInputManifest", level)
        declared = self.scope_input_fingerprint
        _require_sha256(declared, "DemoScopeInputManifest", "scope_input_fingerprint")
        computed = self.compute_scope_input_fingerprint()
        if declared != computed:
            raise SchemaValidationError(
                "DemoScopeInputManifest.scope_input_fingerprint 与当前内容不一致"
                f"（声明 {declared} ≠ 计算 {computed}）")

    def fingerprint_body(self) -> dict:
        """按封闭白名单取指纹体；任何未列出的字段都不进指纹。"""
        full = self.to_dict()
        missing = [k for k in SCOPE_INPUT_FINGERPRINT_FIELDS if k not in full]
        if missing:
            raise SchemaValidationError(f"scope_input_fingerprint 白名单字段缺失: {missing}")
        leaked = [k for k in SCOPE_INPUT_FORBIDDEN_FIELDS if k in SCOPE_INPUT_FINGERPRINT_FIELDS]
        if leaked:
            raise SchemaValidationError(f"scope_input_fingerprint 白名单含被禁字段: {leaked}")
        return {k: full[k] for k in SCOPE_INPUT_FINGERPRINT_FIELDS}

    def compute_scope_input_fingerprint(self) -> str:
        return sha256_canonical(self.fingerprint_body())

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_fingerprint": self.profile_fingerprint,
            "job_id": self.job_id,
            "case_input_id": self.case_input_id,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "credit_type": self.credit_type,
            "report_as_of": self.report_as_of,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "raw_pdf_sha256": self.raw_pdf_sha256,
            "current_evidence_set_version": self.current_evidence_set_version,
            "substrate_dependency_versions": dict(self.substrate_dependency_versions),
            "financial_snapshot_id": self.financial_snapshot_id,
            "external_policy_snapshot_id": self.external_policy_snapshot_id,
            "budget_policy_id": self.budget_policy_id,
            "budget_policy_version": self.budget_policy_version,
            "model_policy_id": self.model_policy_id,
            "code_fingerprint": self.code_fingerprint,
            "selected_section_ids": list(self.selected_section_ids),
            "selected_topic_ids": list(self.selected_topic_ids),
            "selected_question_ids": list(self.selected_question_ids),
            "selected_aspect_ids": list(self.selected_aspect_ids),
            "out_of_scope_section_ids": list(self.out_of_scope_section_ids),
            "out_of_scope_topic_ids": list(self.out_of_scope_topic_ids),
            "out_of_scope_question_ids": list(self.out_of_scope_question_ids),
            "out_of_scope_aspect_ids": list(self.out_of_scope_aspect_ids),
            "scope_input_fingerprint": self.scope_input_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DemoScopeInputManifest":
        d = _unknown(d, set(cls.__dataclass_fields__), "DemoScopeInputManifest")
        return cls(
            schema_version=_str(d, "schema_version", "DemoScopeInputManifest"),
            profile_id=_str(d, "profile_id", "DemoScopeInputManifest"),
            profile_version=_str(d, "profile_version", "DemoScopeInputManifest"),
            profile_fingerprint=_str(d, "profile_fingerprint", "DemoScopeInputManifest"),
            job_id=_str(d, "job_id", "DemoScopeInputManifest"),
            case_input_id=_str_or_empty(d, "case_input_id", "DemoScopeInputManifest"),
            company_id=_str(d, "company_id", "DemoScopeInputManifest"),
            company_name=_str_or_empty(d, "company_name", "DemoScopeInputManifest"),
            credit_type=_str(d, "credit_type", "DemoScopeInputManifest"),
            report_as_of=_str(d, "report_as_of", "DemoScopeInputManifest"),
            document_id=_str(d, "document_id", "DemoScopeInputManifest"),
            document_version=_str(d, "document_version", "DemoScopeInputManifest"),
            raw_pdf_sha256=_str(d, "raw_pdf_sha256", "DemoScopeInputManifest"),
            current_evidence_set_version=_str(d, "current_evidence_set_version",
                                              "DemoScopeInputManifest"),
            substrate_dependency_versions=validate_substrate_dependency_versions(
                d.get("substrate_dependency_versions")),
            financial_snapshot_id=_str(d, "financial_snapshot_id", "DemoScopeInputManifest",
                                       none=True),
            external_policy_snapshot_id=_str(d, "external_policy_snapshot_id",
                                             "DemoScopeInputManifest", none=True),
            budget_policy_id=_str(d, "budget_policy_id", "DemoScopeInputManifest"),
            budget_policy_version=_str(d, "budget_policy_version", "DemoScopeInputManifest"),
            model_policy_id=_str(d, "model_policy_id", "DemoScopeInputManifest"),
            code_fingerprint=_str(d, "code_fingerprint", "DemoScopeInputManifest"),
            selected_section_ids=_str_tuple(d, "selected_section_ids", "DemoScopeInputManifest"),
            selected_topic_ids=_str_tuple(d, "selected_topic_ids", "DemoScopeInputManifest"),
            selected_question_ids=_str_tuple(d, "selected_question_ids", "DemoScopeInputManifest"),
            selected_aspect_ids=_str_tuple(d, "selected_aspect_ids", "DemoScopeInputManifest"),
            out_of_scope_section_ids=_str_tuple(d, "out_of_scope_section_ids",
                                                "DemoScopeInputManifest"),
            out_of_scope_topic_ids=_str_tuple(d, "out_of_scope_topic_ids",
                                              "DemoScopeInputManifest"),
            out_of_scope_question_ids=_str_tuple(d, "out_of_scope_question_ids",
                                                 "DemoScopeInputManifest"),
            out_of_scope_aspect_ids=_str_tuple(d, "out_of_scope_aspect_ids",
                                               "DemoScopeInputManifest"),
            scope_input_fingerprint=_str(d, "scope_input_fingerprint", "DemoScopeInputManifest"),
        )


# ---------------------------------------------------------------------------
# 3. DemoRunIdentity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoRunIdentity:
    """一次运行尝试的操作性身份（``run_id/attempt/started_at/results_root``）。

    这些字段只用于轨迹、checkpoint 所属运行与 artifact 目录，**绝不**进入
    ``scope_input_fingerprint`` / ``projection_id`` / ``plan_id`` / ``task_id`` /
    Pack current key / ``report_version``。
    """

    schema_version: str
    run_id: str
    attempt: int
    started_at: str
    results_root: str
    job_id: str
    scope_input_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != DEMO_RUN_IDENTITY_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"DemoRunIdentity.schema_version 必须为 {DEMO_RUN_IDENTITY_SCHEMA_VERSION!r}")
        if self.attempt < 1:
            raise SchemaValidationError("DemoRunIdentity.attempt 必须 >= 1")
        _require_sha256(self.scope_input_fingerprint, "DemoRunIdentity",
                        "scope_input_fingerprint")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "results_root": self.results_root,
            "job_id": self.job_id,
            "scope_input_fingerprint": self.scope_input_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DemoRunIdentity":
        d = _unknown(d, set(cls.__dataclass_fields__), "DemoRunIdentity")
        return cls(
            schema_version=_str(d, "schema_version", "DemoRunIdentity"),
            run_id=_str(d, "run_id", "DemoRunIdentity"),
            attempt=_int(d, "attempt", "DemoRunIdentity"),
            started_at=_str(d, "started_at", "DemoRunIdentity"),
            results_root=_str(d, "results_root", "DemoRunIdentity"),
            job_id=_str(d, "job_id", "DemoRunIdentity"),
            scope_input_fingerprint=_str(d, "scope_input_fingerprint", "DemoRunIdentity"),
        )


# ---------------------------------------------------------------------------
# 4. DemoPlanningProjection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoPlanningProjection:
    """Contract v2 的受控投影：现行 ``ReportPlan`` / ``SectionTask`` +
    每 topic 的 ``TopicResearchRequirement`` + 不可变 aspect 政策快照。

    ``projection_id`` 只由 ``PROJECTION_ID_FIELDS``（不含 report_plan / requirements）
    派生，因此 plan_id / task_id 可以引用它而不会形成身份环。
    """

    schema_version: str
    projection_id: str
    projection_rule_version: str
    profile_id: str
    profile_version: str
    profile_fingerprint: str
    scope_input_fingerprint: str
    job_id: str
    company_id: str
    report_as_of: str
    contract_asset: str
    contract_version: str
    contract_fingerprint: str
    source_policy_id: str
    source_policy_version: str
    source_policy_fingerprint: str
    writing_spec_id: str
    writing_spec_version: str
    writing_spec_fingerprint: str
    presentation_profile_id: str
    presentation_profile_version: str
    presentation_profile_fingerprint: str
    dependency_fingerprint: str
    plan_id: str
    report_plan: PS.ReportPlan
    requirements: tuple[TopicResearchRequirement, ...]
    aspect_writing_bindings: tuple[dict, ...]
    selected_section_ids: tuple[str, ...]
    selected_topic_ids: tuple[str, ...]
    selected_question_ids: tuple[str, ...]
    selected_aspect_ids: tuple[str, ...]
    out_of_scope_section_ids: tuple[str, ...]
    out_of_scope_topic_ids: tuple[str, ...]
    out_of_scope_question_ids: tuple[str, ...]
    out_of_scope_aspect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != DEMO_PLANNING_PROJECTION_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"DemoPlanningProjection.schema_version 必须为 "
                f"{DEMO_PLANNING_PROJECTION_SCHEMA_VERSION!r}")
        if self.projection_rule_version != DEMO_PROJECTION_ID_RULE_VERSION:
            raise SchemaValidationError(
                f"DemoPlanningProjection.projection_rule_version 必须为 "
                f"{DEMO_PROJECTION_ID_RULE_VERSION!r}")
        if self.contract_version != REQUIRED_CONTRACT_VERSION:
            raise SchemaValidationError(
                f"DemoPlanningProjection 只接受 Contract {REQUIRED_CONTRACT_VERSION!r}")
        if not self.projection_id.startswith("proj_"):
            raise SchemaValidationError("DemoPlanningProjection.projection_id 必须以 'proj_' 开头")
        if self.projection_id != self.compute_projection_id():
            raise SchemaValidationError(
                f"DemoPlanningProjection.projection_id 与规范体不一致（声明 {self.projection_id} "
                f"≠ 计算 {self.compute_projection_id()}）")
        if not self.plan_id.startswith("dplan_"):
            raise SchemaValidationError("DemoPlanningProjection.plan_id 必须以 'dplan_' 开头")
        if self.report_plan.plan_id != self.plan_id:
            raise SchemaValidationError(
                "DemoPlanningProjection.report_plan.plan_id 必须等于 projection.plan_id")
        if self.report_plan.job_id != self.job_id:
            raise SchemaValidationError(
                "DemoPlanningProjection.report_plan.job_id 必须等于 projection.job_id")
        task_ids = [t.task_id for t in self.report_plan.section_tasks]
        if len(set(task_ids)) != len(task_ids):
            raise SchemaValidationError("DemoPlanningProjection 的 SectionTask.task_id 重复")
        if len(set(t.section_id for t in self.report_plan.section_tasks)) != len(task_ids):
            raise SchemaValidationError("DemoPlanningProjection 的 SectionTask.section_id 重复")
        if set(task_ids) != set(self.task_ids()):
            raise SchemaValidationError("DemoPlanningProjection 的 task 集合与 report_plan 不一致")
        # requirement 身份是 (task_id, topic_id) 对：一个 SectionTask 可以承载多个 Topic，
        # Pack identity key 同样按 (task_id, …, topic_id) 区分，因此只禁止整对重复。
        req_keys = [(r.task_id, r.topic_id) for r in self.requirements]
        if len(set(req_keys)) != len(req_keys):
            raise SchemaValidationError(
                "DemoPlanningProjection 的 requirement (task_id, topic_id) 重复")
        req_tasks = [r.task_id for r in self.requirements]
        unknown = sorted(set(req_tasks) - set(task_ids))
        if unknown:
            raise SchemaValidationError(
                f"DemoPlanningProjection 的 requirement.task_id 不在 report_plan 中: {unknown}")
        aspect_ids = [b["aspect_id"] for b in self.aspect_writing_bindings]
        if len(set(aspect_ids)) != len(aspect_ids):
            raise SchemaValidationError("DemoPlanningProjection.aspect_writing_bindings 含重复 aspect_id")
        if set(aspect_ids) != set(self.selected_aspect_ids):
            raise SchemaValidationError(
                "DemoPlanningProjection.aspect_writing_bindings 必须恰好覆盖 selected_aspect_ids")
        for b in self.aspect_writing_bindings:
            _unknown(b, set(ASPECT_WRITING_BINDING_KEYS), "AspectWritingBinding")

    def projection_id_body(self) -> dict:
        d = self.to_dict()
        missing = [k for k in PROJECTION_ID_FIELDS if k not in d]
        if missing:
            raise SchemaValidationError(f"projection_id 白名单字段缺失: {missing}")
        return {k: d[k] for k in PROJECTION_ID_FIELDS}

    def compute_projection_id(self) -> str:
        return derive_demo_projection_id(self.projection_id_body())

    def task_ids(self) -> tuple[str, ...]:
        return tuple(t.task_id for t in self.report_plan.section_tasks)

    def requirement_by_topic(self) -> dict[str, TopicResearchRequirement]:
        return {r.topic_id: r for r in self.requirements}

    def requirement_by_task(self) -> dict[str, TopicResearchRequirement]:
        out: dict[str, TopicResearchRequirement] = {}
        for r in self.requirements:
            for t in self.report_plan.section_tasks:
                if t.section_id == r.section_id:
                    out[r.topic_id] = r
                    break
        return out

    def section_task_by_id(self, section_id: str) -> PS.SectionTask:
        for t in self.report_plan.section_tasks:
            if t.section_id == section_id:
                return t
        raise SchemaValidationError(f"DemoPlanningProjection 无 section {section_id!r}")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "projection_rule_version": self.projection_rule_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_fingerprint": self.profile_fingerprint,
            "scope_input_fingerprint": self.scope_input_fingerprint,
            "job_id": self.job_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "contract_asset": self.contract_asset,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_id": self.source_policy_id,
            "source_policy_version": self.source_policy_version,
            "source_policy_fingerprint": self.source_policy_fingerprint,
            "writing_spec_id": self.writing_spec_id,
            "writing_spec_version": self.writing_spec_version,
            "writing_spec_fingerprint": self.writing_spec_fingerprint,
            "presentation_profile_id": self.presentation_profile_id,
            "presentation_profile_version": self.presentation_profile_version,
            "presentation_profile_fingerprint": self.presentation_profile_fingerprint,
            "dependency_fingerprint": self.dependency_fingerprint,
            "plan_id": self.plan_id,
            "report_plan": report_plan_to_dict(self.report_plan),
            "requirements": [r.to_dict() for r in self.requirements],
            "aspect_writing_bindings": [dict(b) for b in self.aspect_writing_bindings],
            "selected_section_ids": list(self.selected_section_ids),
            "selected_topic_ids": list(self.selected_topic_ids),
            "selected_question_ids": list(self.selected_question_ids),
            "selected_aspect_ids": list(self.selected_aspect_ids),
            "out_of_scope_section_ids": list(self.out_of_scope_section_ids),
            "out_of_scope_topic_ids": list(self.out_of_scope_topic_ids),
            "out_of_scope_question_ids": list(self.out_of_scope_question_ids),
            "out_of_scope_aspect_ids": list(self.out_of_scope_aspect_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DemoPlanningProjection":
        d = _unknown(d, set(cls.__dataclass_fields__), "DemoPlanningProjection")
        return cls(
            schema_version=_str(d, "schema_version", "DemoPlanningProjection"),
            projection_id=_str(d, "projection_id", "DemoPlanningProjection"),
            projection_rule_version=_str(d, "projection_rule_version", "DemoPlanningProjection"),
            profile_id=_str(d, "profile_id", "DemoPlanningProjection"),
            profile_version=_str(d, "profile_version", "DemoPlanningProjection"),
            profile_fingerprint=_str(d, "profile_fingerprint", "DemoPlanningProjection"),
            scope_input_fingerprint=_str(d, "scope_input_fingerprint", "DemoPlanningProjection"),
            job_id=_str(d, "job_id", "DemoPlanningProjection"),
            company_id=_str(d, "company_id", "DemoPlanningProjection"),
            report_as_of=_str(d, "report_as_of", "DemoPlanningProjection"),
            contract_asset=_str(d, "contract_asset", "DemoPlanningProjection"),
            contract_version=_str(d, "contract_version", "DemoPlanningProjection"),
            contract_fingerprint=_str(d, "contract_fingerprint", "DemoPlanningProjection"),
            source_policy_id=_str(d, "source_policy_id", "DemoPlanningProjection"),
            source_policy_version=_str(d, "source_policy_version", "DemoPlanningProjection"),
            source_policy_fingerprint=_str(d, "source_policy_fingerprint", "DemoPlanningProjection"),
            writing_spec_id=_str(d, "writing_spec_id", "DemoPlanningProjection"),
            writing_spec_version=_str(d, "writing_spec_version", "DemoPlanningProjection"),
            writing_spec_fingerprint=_str(d, "writing_spec_fingerprint", "DemoPlanningProjection"),
            presentation_profile_id=_str(d, "presentation_profile_id", "DemoPlanningProjection"),
            presentation_profile_version=_str(d, "presentation_profile_version",
                                              "DemoPlanningProjection"),
            presentation_profile_fingerprint=_str(d, "presentation_profile_fingerprint",
                                                  "DemoPlanningProjection"),
            dependency_fingerprint=_str(d, "dependency_fingerprint", "DemoPlanningProjection"),
            plan_id=_str(d, "plan_id", "DemoPlanningProjection"),
            report_plan=report_plan_from_dict(
                # _closed_dict 返回内层 dict（不是包装层），因此无需再取键。
                _closed_dict({"v": d.get("report_plan")}, "v", "DemoPlanningProjection",
                             ("plan_id", "job_id", "company_id", "company_name",
                              "credit_type", "report_as_of", "template_id",
                              "input_fingerprint", "contract_fingerprint", "planner_version",
                              "section_tasks", "created_at"),
                             ("plan_id", "job_id", "company_id", "company_name", "credit_type",
                              "report_as_of", "template_id", "input_fingerprint",
                              "contract_fingerprint", "planner_version", "section_tasks"))),
            requirements=tuple(TopicResearchRequirement.from_dict(x) for x in _as_list(
                d.get("requirements"), "DemoPlanningProjection", "requirements")),
            aspect_writing_bindings=tuple(
                dict(b) for b in _as_list(d.get("aspect_writing_bindings"),
                                          "DemoPlanningProjection", "aspect_writing_bindings")),
            selected_section_ids=_str_tuple(d, "selected_section_ids", "DemoPlanningProjection"),
            selected_topic_ids=_str_tuple(d, "selected_topic_ids", "DemoPlanningProjection"),
            selected_question_ids=_str_tuple(d, "selected_question_ids", "DemoPlanningProjection"),
            selected_aspect_ids=_str_tuple(d, "selected_aspect_ids", "DemoPlanningProjection"),
            out_of_scope_section_ids=_str_tuple(d, "out_of_scope_section_ids",
                                                "DemoPlanningProjection"),
            out_of_scope_topic_ids=_str_tuple(d, "out_of_scope_topic_ids",
                                              "DemoPlanningProjection"),
            out_of_scope_question_ids=_str_tuple(d, "out_of_scope_question_ids",
                                                 "DemoPlanningProjection"),
            out_of_scope_aspect_ids=_str_tuple(d, "out_of_scope_aspect_ids",
                                               "DemoPlanningProjection"),
        )


def _as_list(v: Any, typename: str, key: str) -> list:
    if not isinstance(v, list):
        raise SchemaValidationError(f"{typename}.{key} 必须为 list，得到 {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# 5. ResolvedDemoScopeManifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedDemoScopeManifest:
    """运行**后**的版本化 run record。

    只**引用**前序对象（input manifest / projection / plan / task），并把本轮的
    runtime output（live tree IDs / Pack IDs / ExternalSnapshot IDs / FinancialFactPack
    artifact ID / 显式 gap）追加进来。它不得回填或改变 input manifest、plan、task 与
    Pack current identity。其指纹只用于 artifact/run 身份。
    """

    schema_version: str
    manifest_id: str
    manifest_fingerprint: str
    run_id: str
    attempt: int
    job_id: str
    company_id: str
    report_as_of: str
    scope_input_fingerprint: str
    projection_id: str
    plan_id: str
    task_ids: tuple[str, ...]
    section_ids: tuple[str, ...]
    document_id: str
    document_version: str
    current_evidence_set_version: str
    live_page_layout_ids: tuple[str, ...]
    live_alignment_ids: tuple[str, ...]
    live_outline_ids: tuple[str, ...]
    live_span_snapshot_ids: tuple[str, ...]
    pack_ids: tuple[str, ...]
    external_snapshot_ids: tuple[str, ...]
    financial_fact_pack_artifact_id: str | None
    gaps: tuple[dict, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RESOLVED_DEMO_SCOPE_MANIFEST_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"ResolvedDemoScopeManifest.schema_version 必须为 "
                f"{RESOLVED_DEMO_SCOPE_MANIFEST_SCHEMA_VERSION!r}")
        _require_sha256(self.scope_input_fingerprint, "ResolvedDemoScopeManifest",
                        "scope_input_fingerprint")
        if not self.plan_id.startswith("dplan_"):
            raise SchemaValidationError("ResolvedDemoScopeManifest.plan_id 必须以 'dplan_' 开头")
        if not self.projection_id.startswith("proj_"):
            raise SchemaValidationError(
                "ResolvedDemoScopeManifest.projection_id 必须以 'proj_' 开头")
        for name, ids in (("task_ids", self.task_ids), ("section_ids", self.section_ids),
                          ("live_page_layout_ids", self.live_page_layout_ids),
                          ("live_alignment_ids", self.live_alignment_ids),
                          ("live_outline_ids", self.live_outline_ids),
                          ("live_span_snapshot_ids", self.live_span_snapshot_ids),
                          ("pack_ids", self.pack_ids),
                          ("external_snapshot_ids", self.external_snapshot_ids)):
            if len(set(ids)) != len(ids):
                raise SchemaValidationError(f"ResolvedDemoScopeManifest.{name} 含重复 ID")
        for g in self.gaps:
            _unknown(g, set(RESOLVED_GAP_KEYS), "ResolvedGap")
            missing = [k for k in RESOLVED_GAP_KEYS if k not in g]
            if missing:
                raise SchemaValidationError(f"ResolvedGap 缺必填字段: {missing}")
            if g["target_kind"] not in GAP_TARGET_KINDS:
                raise SchemaValidationError(f"ResolvedGap.target_kind 非法: {g['target_kind']!r}")
        if self.manifest_id != derive_resolved_manifest_id(self.manifest_fingerprint):
            raise SchemaValidationError(
                "ResolvedDemoScopeManifest.manifest_id 必须由 manifest_fingerprint 派生")
        computed = self.compute_manifest_fingerprint()
        if self.manifest_fingerprint != computed:
            raise SchemaValidationError(
                "ResolvedDemoScopeManifest.manifest_fingerprint 与当前内容不一致"
                f"（声明 {self.manifest_fingerprint} ≠ 计算 {computed}）")

    def compute_manifest_fingerprint(self) -> str:
        d = self.to_dict()
        body = {k: v for k, v in d.items() if k not in _RESOLVED_MANIFEST_FINGERPRINT_EXCLUDE}
        return sha256_canonical(body)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "job_id": self.job_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "scope_input_fingerprint": self.scope_input_fingerprint,
            "projection_id": self.projection_id,
            "plan_id": self.plan_id,
            "task_ids": list(self.task_ids),
            "section_ids": list(self.section_ids),
            "document_id": self.document_id,
            "document_version": self.document_version,
            "current_evidence_set_version": self.current_evidence_set_version,
            "live_page_layout_ids": list(self.live_page_layout_ids),
            "live_alignment_ids": list(self.live_alignment_ids),
            "live_outline_ids": list(self.live_outline_ids),
            "live_span_snapshot_ids": list(self.live_span_snapshot_ids),
            "pack_ids": list(self.pack_ids),
            "external_snapshot_ids": list(self.external_snapshot_ids),
            "financial_fact_pack_artifact_id": self.financial_fact_pack_artifact_id,
            "gaps": [dict(g) for g in self.gaps],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResolvedDemoScopeManifest":
        d = _unknown(d, set(cls.__dataclass_fields__), "ResolvedDemoScopeManifest")
        return cls(
            schema_version=_str(d, "schema_version", "ResolvedDemoScopeManifest"),
            manifest_id=_str(d, "manifest_id", "ResolvedDemoScopeManifest"),
            manifest_fingerprint=_str(d, "manifest_fingerprint", "ResolvedDemoScopeManifest"),
            run_id=_str(d, "run_id", "ResolvedDemoScopeManifest"),
            attempt=_int(d, "attempt", "ResolvedDemoScopeManifest"),
            job_id=_str(d, "job_id", "ResolvedDemoScopeManifest"),
            company_id=_str(d, "company_id", "ResolvedDemoScopeManifest"),
            report_as_of=_str(d, "report_as_of", "ResolvedDemoScopeManifest"),
            scope_input_fingerprint=_str(d, "scope_input_fingerprint",
                                         "ResolvedDemoScopeManifest"),
            projection_id=_str(d, "projection_id", "ResolvedDemoScopeManifest"),
            plan_id=_str(d, "plan_id", "ResolvedDemoScopeManifest"),
            task_ids=_str_tuple(d, "task_ids", "ResolvedDemoScopeManifest"),
            section_ids=_str_tuple(d, "section_ids", "ResolvedDemoScopeManifest"),
            document_id=_str(d, "document_id", "ResolvedDemoScopeManifest"),
            document_version=_str(d, "document_version", "ResolvedDemoScopeManifest"),
            current_evidence_set_version=_str(d, "current_evidence_set_version",
                                              "ResolvedDemoScopeManifest"),
            live_page_layout_ids=_str_tuple(d, "live_page_layout_ids",
                                            "ResolvedDemoScopeManifest"),
            live_alignment_ids=_str_tuple(d, "live_alignment_ids", "ResolvedDemoScopeManifest"),
            live_outline_ids=_str_tuple(d, "live_outline_ids", "ResolvedDemoScopeManifest"),
            live_span_snapshot_ids=_str_tuple(d, "live_span_snapshot_ids",
                                              "ResolvedDemoScopeManifest"),
            pack_ids=_str_tuple(d, "pack_ids", "ResolvedDemoScopeManifest"),
            external_snapshot_ids=_str_tuple(d, "external_snapshot_ids",
                                             "ResolvedDemoScopeManifest"),
            financial_fact_pack_artifact_id=_str(d, "financial_fact_pack_artifact_id",
                                                 "ResolvedDemoScopeManifest", none=True),
            gaps=tuple(_closed_dict({"g": g}, "g", "ResolvedGap", RESOLVED_GAP_KEYS,
                                    ("gap_id", "target_kind", "target_id", "reason_code",
                                     "searched_scope", "impact", "suggested_material_type"))
                       for g in _as_list(d.get("gaps"), "ResolvedDemoScopeManifest", "gaps")),
        )


# ---------------------------------------------------------------------------
# ID / 指纹派生（显式版本化；不改动全局 derive_plan_id / derive_task_id）
# ---------------------------------------------------------------------------

def derive_demo_projection_id(body: dict) -> str:
    """``projection_id`` 派生（封闭白名单规范体）。"""
    if set(body) != set(PROJECTION_ID_FIELDS):
        raise SchemaValidationError(
            f"projection_id 规范体必须恰为 {PROJECTION_ID_FIELDS}，得到 {sorted(body)}")
    return "proj_" + sha256_canonical(body)[:24]


def compute_demo_dependency_fingerprint(contract_fingerprint: str, source_policy_fingerprint: str,
                                        writing_spec_fingerprint: str,
                                        presentation_profile_fingerprint: str) -> str:
    """四类冻结资产的总依赖指纹。"""
    for name, v in (("contract_fingerprint", contract_fingerprint),
                    ("source_policy_fingerprint", source_policy_fingerprint),
                    ("writing_spec_fingerprint", writing_spec_fingerprint),
                    ("presentation_profile_fingerprint", presentation_profile_fingerprint)):
        _require_sha256(v, "DemoDependencyFingerprint", name)
    return sha256_canonical({
        "contract_fingerprint": contract_fingerprint,
        "source_policy_fingerprint": source_policy_fingerprint,
        "writing_spec_fingerprint": writing_spec_fingerprint,
        "presentation_profile_fingerprint": presentation_profile_fingerprint,
    })


def derive_demo_plan_id(projection_id: str, scope_input_fingerprint: str,
                        profile_fingerprint: str, contract_fingerprint: str,
                        job_id: str, company_id: str, report_as_of: str) -> str:
    """``plan_id`` 派生：纳入 profile/scope/contract 指纹 + job_id + company/report_as_of。

    不含 run_id / attempt / time / path。同一 job 与相同业务输入跨 run 幂等；
    不同 job 或不同 Demo scope 必然得到不同 plan_id。
    """
    return "dplan_" + sha256_canonical({
        "rule_version": DEMO_PLAN_ID_RULE_VERSION,
        "projection_id": projection_id,
        "scope_input_fingerprint": scope_input_fingerprint,
        "profile_fingerprint": profile_fingerprint,
        "contract_fingerprint": contract_fingerprint,
        "job_id": job_id,
        "company_id": company_id,
        "report_as_of": report_as_of,
    })[:24]


def derive_demo_task_id(projection_id: str, plan_id: str, section_id: str) -> str:
    """``task_id`` 派生：projection + section scope。

    前缀 ``dtask_`` 与旧 v1 ``derive_task_id()`` 的 ``task_`` 明确区分，避免同一
    Contract 下不同 Demo scope 共享 task/current-Pack 命名空间。
    """
    return "dtask_" + sha256_canonical({
        "rule_version": DEMO_TASK_ID_RULE_VERSION,
        "projection_id": projection_id,
        "plan_id": plan_id,
        "section_id": section_id,
    })[:24]


def derive_resolved_manifest_id(manifest_fingerprint: str) -> str:
    _require_sha256(manifest_fingerprint, "ResolvedDemoScopeManifest", "manifest_fingerprint")
    return "dsm_" + manifest_fingerprint[:24]


# ---------------------------------------------------------------------------
# ReportPlan 序列化助手（旧 wire 不改动；此处只做无损往返）
# ---------------------------------------------------------------------------

def report_plan_to_dict(plan: PS.ReportPlan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "job_id": plan.job_id,
        "company_id": plan.company_id,
        "company_name": plan.company_name,
        "credit_type": plan.credit_type,
        "report_as_of": plan.report_as_of,
        "template_id": plan.template_id,
        "input_fingerprint": plan.input_fingerprint,
        "contract_fingerprint": plan.contract_fingerprint,
        "planner_version": plan.planner_version,
        "section_tasks": [PS.section_task_to_dict(t) for t in plan.section_tasks],
        "created_at": plan.created_at,
    }


def report_plan_from_dict(d: dict) -> PS.ReportPlan:
    return PS.ReportPlan(
        plan_id=d["plan_id"],
        job_id=d["job_id"],
        company_id=d["company_id"],
        company_name=d["company_name"],
        credit_type=d["credit_type"],
        report_as_of=d["report_as_of"],
        template_id=d["template_id"],
        input_fingerprint=d["input_fingerprint"],
        contract_fingerprint=d["contract_fingerprint"],
        planner_version=d["planner_version"],
        section_tasks=tuple(PS.section_task_from_dict(t) for t in d.get("section_tasks") or []),
        created_at=d.get("created_at", ""),
    )


# ---------------------------------------------------------------------------
# 便捷重导出（不新增类型；供 sections/backbone_schema.py 引用 planning 对象 ID）
# ---------------------------------------------------------------------------

__all__ = [
    "DEMO_SCOPE_PROFILE_SCHEMA_VERSION",
    "DEMO_SCOPE_INPUT_MANIFEST_SCHEMA_VERSION",
    "DEMO_RUN_IDENTITY_SCHEMA_VERSION",
    "DEMO_PLANNING_PROJECTION_SCHEMA_VERSION",
    "RESOLVED_DEMO_SCOPE_MANIFEST_SCHEMA_VERSION",
    "DEMO_PROJECTION_ID_RULE_VERSION",
    "DEMO_PLAN_ID_RULE_VERSION",
    "DEMO_TASK_ID_RULE_VERSION",
    "DEMO_PLANNER_VERSION",
    "REQUIRED_CONTRACT_VERSION",
    "SCOPE_ASSET_KEYS",
    "PROFILE_FINGERPRINT_EXCLUDE",
    "SUBSTRATE_DEPENDENCY_KEYS",
    "SCOPE_INPUT_FINGERPRINT_FIELDS",
    "SCOPE_INPUT_FORBIDDEN_FIELDS",
    "RESOLVED_GAP_KEYS",
    "GAP_TARGET_KINDS",
    "ASPECT_WRITING_BINDING_KEYS",
    "DemoScopeProfile",
    "DemoScopeInputManifest",
    "DemoRunIdentity",
    "DemoPlanningProjection",
    "ResolvedDemoScopeManifest",
    "compute_demo_scope_profile_fingerprint",
    "compute_demo_dependency_fingerprint",
    "derive_demo_projection_id",
    "derive_demo_plan_id",
    "derive_demo_task_id",
    "derive_resolved_manifest_id",
    "report_plan_to_dict",
    "report_plan_from_dict",
    "validate_substrate_dependency_versions",
    "require_rel_asset_path",
    "canonical_json",
    "sha256_canonical",
    "SchemaValidationError",
    "TopicResearchRequirement",
    "TopicAspectRequirementSnapshot",
]

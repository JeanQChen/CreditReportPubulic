"""M930-1：DemoScope 的 load / build / project / resolve / verify 实现。

职责边界（M930-1 任务书 §五 / §七）：

- 本模块**只**实现函数并 import ``planning.demo_scope_schema`` 的类型；
  scope/projection/run-identity 类型的唯一所有者是 ``planning.demo_scope_schema``。
- 本模块**不 import 任何 ``sections.*``**（``planning → sections`` 反向依赖被禁止）。
  因此四类冻结资产的 version/fingerprint 由本模块直接以 YAML + ``contracts.schema_v2``
  的 ``content_fingerprint`` / ``frozen_asset_errors`` 校验；WritingSpec 只读投影所需的
  9 个冻结字段（``WRITING_SPEC_MAPPING_FIELDS``，逐字段与
  ``sections.writing_spec._MAPPING_FIELDS`` 一致），不新建第二套解析框架。
  ``verify_demo_projection()`` 额外的 typed ``writing_spec`` 由调用方以参数传入。
- 本模块不写库、不写文件、不调 LLM / 网络 / Router / ToolRegistry，全部为纯函数。
- 只接受 Contract v2；Contract v1（``templates/contracts/standard_v2.yaml``）fail-closed。
- 不改动全局 ``planning.schema.derive_plan_id()`` / ``derive_task_id()`` 语义；本模块
  使用 ``demo_scope_schema`` 中显式版本化的 ``proj_`` / ``dplan_`` / ``dtask_`` / ``dsm_``
  派生规则，从而不与历史 ``plan_`` / ``task_`` 命名空间冲突。

CLI::

    python -m planning.demo_scope --self-check
    python -m planning.demo_scope --fingerprint [PROFILE_PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from contracts import schema_v2 as CS2
from contracts.loader_v2 import load_contract_v2
from harness import topic_schema as TS
from planning import schema as PS
from planning.demo_scope_schema import (
    ASPECT_WRITING_BINDING_KEYS,
    DEMO_PLANNER_VERSION,
    DEMO_PLANNING_PROJECTION_SCHEMA_VERSION,
    DEMO_PROJECTION_ID_RULE_VERSION,
    DEMO_RUN_IDENTITY_SCHEMA_VERSION,
    DEMO_SCOPE_INPUT_MANIFEST_SCHEMA_VERSION,
    DEMO_SCOPE_PROFILE_SCHEMA_VERSION,
    GAP_TARGET_KINDS,
    PROFILE_FINGERPRINT_EXCLUDE,
    REQUIRED_CONTRACT_VERSION,
    RESOLVED_DEMO_SCOPE_MANIFEST_SCHEMA_VERSION,
    RESOLVED_GAP_KEYS,
    SCOPE_INPUT_FORBIDDEN_FIELDS,
    SCOPE_INPUT_FINGERPRINT_FIELDS,
    SUBSTRATE_DEPENDENCY_KEYS,
    SchemaValidationError,
    DemoPlanningProjection,
    DemoRunIdentity,
    DemoScopeInputManifest,
    DemoScopeProfile,
    ResolvedDemoScopeManifest,
    compute_demo_dependency_fingerprint,
    compute_demo_scope_profile_fingerprint,
    derive_demo_plan_id,
    derive_demo_projection_id,
    derive_demo_task_id,
    derive_resolved_manifest_id,
    report_plan_to_dict,
    require_rel_asset_path,
    validate_substrate_dependency_versions,
)
from harness.topic_schema import (
    DerivedFromScope,
    EvidenceAuthorityPolicy,
    EvidenceRequirementRef,
    SourceClassGroup,
    SourcePolicyRef,
    TopicAspectRequirementSnapshot,
    TopicResearchRequirement,
    canonical_json,
    sha256_canonical,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PROFILE_PATH = "templates/demo_scopes/interview_backbone_v1.yaml"

# ---------------------------------------------------------------------------
# 源文件（Profile YAML）的封闭键集
#
# 说明：YAML 是**配置源**，不是第二个 wire 类型。加载后一切身份都落在
# ``DemoScopeProfile``（唯一 wire 类型）上，并由 ``profile_fingerprint`` 承诺。
# ---------------------------------------------------------------------------

PROFILE_SOURCE_KEYS = (
    "schema_version",
    "profile_id",
    "profile_version",
    "status",
    "created_at",
    "approved_at",
    "frozen_at",
    "assets",
    "selected_sections",
    "selected_topics",
    "material_capabilities",
    "fallback_policy_id",
    "budget_policy_id",
    "budget_policy_version",
    "company_independent",
    "profile_fingerprint",
)

ASSET_BLOCK_KEYS = ("contract", "source_policy", "writing_spec", "presentation_profile")
_ASSET_BLOCK_META_KEYS = ("asset", "fingerprint")

SELECTED_SECTION_KEYS = ("section_id", "selection_reason", "design_surface_ids")
SELECTED_TOPIC_KEYS = ("topic_id", "section_id", "selection_reason", "design_surface_ids")

# 受控投影直接读取的 WritingSpec 冻结映射字段（与 sections.writing_spec._MAPPING_FIELDS 一致）。
WRITING_SPEC_MAPPING_FIELDS = (
    "subsection_id", "aspect_id", "role", "content_role", "table_schema",
    "citation_granularity", "gap_display_policy", "display_tier",
    "period_language_policy",
)
WRITING_SPEC_ROLES = ("primary", "secondary_reference")

# 投影内 evidence_requirements 的记录键（封闭；``authority`` 展平为 one-of 组列表）。
PLANNED_EVIDENCE_REQUIREMENT_KEYS = (
    "requirement_id",
    "evidence_kind",
    "minimum_sources",
    "source_classes",
    "required_fields",
    "authority_required_any_of",
)

# ``build_scope_input_manifest()`` 的 ``source_inputs`` 封闭键集。
SOURCE_INPUT_KEYS = (
    "case_input_id",
    "document_id",
    "document_version",
    "raw_pdf_sha256",
    "current_evidence_set_version",
    "substrate_dependency_versions",
    "external_policy_snapshot_id",
    "budget_policy_id",
    "budget_policy_version",
    "model_policy_id",
    "code_fingerprint",
)

# ``build_demo_run_identity()`` 的 ``run_request`` 封闭键集。
RUN_REQUEST_KEYS = ("run_id", "attempt", "started_at")

# ``resolve_demo_scope_manifest()`` 的 ``runtime_outputs`` 封闭键集。
RUNTIME_OUTPUT_KEYS = (
    "live_page_layout_ids",
    "live_alignment_ids",
    "live_outline_ids",
    "live_span_snapshot_ids",
    "pack_ids",
    "external_snapshot_ids",
    "financial_fact_pack_artifact_id",
    "gaps",
)

def _err(msg: str) -> None:    raise SchemaValidationError(msg)


def _require_dict(v: Any, what: str) -> dict:
    if not isinstance(v, dict):
        _err(f"{what} 必须为 dict，得到 {type(v).__name__}")
    return v


def _reject_source_unknown(d: dict, allowed: tuple[str, ...], what: str) -> None:
    extra = sorted(set(d) - set(allowed))
    if extra:
        _err(f"{what} 含未知字段: {extra}")
    missing = sorted(k for k in allowed if k not in d)
    if missing:
        _err(f"{what} 缺必填字段: {missing}")


def _req_str(d: dict, key: str, what: str, *, allow_none: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        if allow_none:
            return None
        _err(f"{what}.{key} 必填")
    if not isinstance(v, str) or v == "":
        _err(f"{what}.{key} 必须为非空字符串，得到 {v!r}")
    return v


def _str_list(v: Any, what: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not v:
        _err(f"{what} 必须为非空 list[str]")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or x == "":
            _err(f"{what} 含非法元素 {x!r}")
        out.append(x)
    return tuple(out)


def _resolve_repo_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else (REPO_ROOT / rel_or_abs)


def _load_yaml_doc(path: Path, what: str) -> dict:
    if not path.is_file():
        _err(f"{what} 不存在: {path}")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        _err(f"{what} YAML 解析失败: {e}")
    return _require_dict(doc, what)


# ---------------------------------------------------------------------------
# 四类冻结资产校验（不 import sections.*）
# ---------------------------------------------------------------------------

def load_frozen_asset_doc(rel_asset_path: str, what: str) -> dict:
    """读取并校验一个冻结资产 YAML（lifecycle + content_sha256 自证）。"""
    rel = require_rel_asset_path(rel_asset_path, what, "asset")
    doc = _load_yaml_doc(_resolve_repo_path(rel), what)
    lc = CS2.lifecycle_errors(doc)
    if lc:
        _err(f"{what} 生命周期非法: {lc}")
    fa = CS2.frozen_asset_errors(doc)
    if fa:
        _err(f"{what} 冻结自证失败: {fa}")
    return doc


def verify_frozen_assets(assets_block: dict, *, asset_docs: dict[str, dict] | None = None
                         ) -> dict[str, dict]:
    """校验 Profile 声明的四类冻结资产（version/fingerprint 缺失或漂移一律拒绝）。

    返回 ``{asset_kind: doc}``。每个资产块除 ``asset`` / ``fingerprint`` 外的键，都必须在
    资产文档中以相同值出现（即声明的 version 必须真的匹配冻结文档）。
    """
    _reject_source_unknown(assets_block, ASSET_BLOCK_KEYS, "profile.assets")
    docs: dict[str, dict] = {}
    for kind in ASSET_BLOCK_KEYS:
        block = _require_dict(assets_block[kind], f"profile.assets.{kind}")
        _reject_source_unknown(
            block, tuple(sorted(k for k in block)), f"profile.assets.{kind}")
        for meta in _ASSET_BLOCK_META_KEYS:
            if meta not in block:
                _err(f"profile.assets.{kind} 缺 {meta}")
        identity = {k: v for k, v in block.items() if k not in _ASSET_BLOCK_META_KEYS}
        if not identity:
            _err(f"profile.assets.{kind} 必须声明至少一个 identity/version 字段")
        doc = (asset_docs or {}).get(kind)
        if doc is None:
            doc = load_frozen_asset_doc(block["asset"], f"冻结资产 {kind}")
        if doc.get("status") != "frozen":
            _err(f"冻结资产 {kind} status 必须为 'frozen'，得到 {doc.get('status')!r}")
        for k, v in identity.items():
            if doc.get(k) != v:
                _err(f"冻结资产 {kind} 声明 {k}={v!r}，资产实际为 {doc.get(k)!r}（fail-closed）")
        declared_fp = block["fingerprint"]
        actual_fp = CS2.content_fingerprint(doc)
        if declared_fp != actual_fp:
            _err(f"冻结资产 {kind} fingerprint 漂移：声明 {declared_fp} ≠ 实际 {actual_fp}")
        docs[kind] = doc
    return docs


# ---------------------------------------------------------------------------
# Profile 加载
# ---------------------------------------------------------------------------

def _writing_spec_mappings(doc: dict) -> list[dict]:
    """只读投影所需的 WritingSpec 冻结映射（逐条 9 个冻结字段，缺失/多余一律拒绝）。"""
    raw = doc.get("mappings")
    if not isinstance(raw, list) or not raw:
        _err("WritingSpec.mappings 必须为非空 list")
    out: list[dict] = []
    for m in raw:
        m = _require_dict(m, "WritingSpec.mapping")
        _reject_source_unknown(m, WRITING_SPEC_MAPPING_FIELDS, "WritingSpec.mapping")
        if m["role"] not in WRITING_SPEC_ROLES:
            _err(f"WritingSpec.mapping.role 非法: {m['role']!r}")
        out.append(dict(m))
    return out


def _primary_mapping_by_aspect(mappings: list[dict]) -> dict[str, dict]:
    primary: dict[str, dict] = {}
    for m in mappings:
        if m["role"] != "primary":
            continue
        aid = m["aspect_id"]
        if aid in primary:
            _err(f"WritingSpec 对 aspect {aid!r} 声明了多个 primary mapping")
        primary[aid] = m
    return primary


def _index_contract(contract) -> dict[str, Any]:
    """把冻结 Contract v2 索引为四层 ID 全集 + 归属关系 + 对象映射。"""
    if contract.contract_version != REQUIRED_CONTRACT_VERSION:
        _err(f"只接受 Contract {REQUIRED_CONTRACT_VERSION!r}，得到 {contract.contract_version!r}")
    sections: list[str] = []
    topics: list[str] = []
    questions: list[str] = []
    aspects: list[str] = []
    topic_of_question: dict[str, str] = {}
    section_of_topic: dict[str, str] = {}
    question_of_aspect: dict[str, str] = {}
    topic_of_aspect: dict[str, str] = {}
    topic_obj: dict[str, Any] = {}
    question_obj: dict[str, Any] = {}
    aspect_obj: dict[str, Any] = {}
    section_obj: dict[str, Any] = {}
    for s in contract.sections:
        if s.section_id in section_obj:
            _err(f"Contract 的 section_id 重复: {s.section_id}")
        sections.append(s.section_id)
        section_obj[s.section_id] = s
        for t in s.topics:
            if t.topic_id in topic_obj:
                _err(f"Contract 的 topic_id 重复: {t.topic_id}")
            topics.append(t.topic_id)
            topic_obj[t.topic_id] = t
            section_of_topic[t.topic_id] = s.section_id
            for q in t.questions:
                if q.question_id in question_obj:
                    _err(f"Contract 的 question_id 重复: {q.question_id}")
                questions.append(q.question_id)
                question_obj[q.question_id] = q
                topic_of_question[q.question_id] = t.topic_id
                if not q.aspects:
                    _err(f"Contract question {q.question_id!r} 无 aspect")
                for a in q.aspects:
                    if a.aspect_id in aspect_obj:
                        _err(f"Contract 的 aspect_id 重复: {a.aspect_id}")
                    aspects.append(a.aspect_id)
                    aspect_obj[a.aspect_id] = a
                    question_of_aspect[a.aspect_id] = q.question_id
                    topic_of_aspect[a.aspect_id] = t.topic_id
    return {
        "sections": tuple(sections), "topics": tuple(topics),
        "questions": tuple(questions), "aspects": tuple(aspects),
        "section_obj": section_obj, "topic_obj": topic_obj,
        "question_obj": question_obj, "aspect_obj": aspect_obj,
        "section_of_topic": section_of_topic,
        "topic_of_question": topic_of_question,
        "question_of_aspect": question_of_aspect,
        "topic_of_aspect": topic_of_aspect,
    }


def _topic_aspects(topic) -> list:
    """一个 Topic 的冻结 aspect 集合（``TopicV2`` 无 ``all_aspects()``，在此显式展开）。"""
    return [a for q in topic.questions for a in q.aspects]


def _manifest_id_sets(manifest: DemoScopeInputManifest) -> dict[str, tuple[str, ...]]:
    return {
        "section": manifest.selected_section_ids,
        "topic": manifest.selected_topic_ids,
        "question": manifest.selected_question_ids,
        "aspect": manifest.selected_aspect_ids,
    }


def _manifest_out_of_scope_id_sets(
        manifest: DemoScopeInputManifest) -> dict[str, tuple[str, ...]]:
    return {
        "section": manifest.out_of_scope_section_ids,
        "topic": manifest.out_of_scope_topic_ids,
        "question": manifest.out_of_scope_question_ids,
        "aspect": manifest.out_of_scope_aspect_ids,
    }


def _profile_fingerprint_body(body: dict) -> dict:
    """按与 ``DemoScopeProfile`` 完全相同的排除集取 profile 指纹体。"""
    return {k: v for k, v in body.items() if k not in PROFILE_FINGERPRINT_EXCLUDE}


def _expand_partition(index: dict, sel_sections: tuple[str, ...],
                      sel_topics: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """由「已选 section + 已选 topic」展开四级精确 ID 集合。

    规则（公司无关、由冻结 Contract 决定）：

    - 选中的 section 必须存在，且其**全部 topic** 进入范围；
    - 选中的 topic 必须存在，且其**全部 question / aspect** 进入范围；
    - 未选中的部分构成 out-of-scope，二者严格互补，不得重叠或遗漏。
    """
    if not sel_sections:
        _err("selected_sections 不能为空")
    if not sel_topics:
        _err("selected_topics 不能为空")
    for sid in sel_sections:
        if sid not in index["section_obj"]:
            _err(f"selected_sections 含未知 section_id: {sid!r}")
    for tid in sel_topics:
        if tid not in index["topic_obj"]:
            _err(f"selected_topics 含未知 topic_id: {tid!r}")
        if index["section_of_topic"][tid] not in sel_sections:
            _err(f"selected_topics {tid!r} 的父 section "
                 f"{index['section_of_topic'][tid]!r} 未被选中（父子归属不一致）")
    sel_topic_set = set(sel_topics)
    sel_question_set = {q for q, t in index["topic_of_question"].items() if t in sel_topic_set}
    sel_aspect_set = {a for a, t in index["topic_of_aspect"].items() if t in sel_topic_set}
    return {
        "selected_section_ids": tuple(s for s in index["sections"] if s in set(sel_sections)),
        "selected_topic_ids": tuple(t for t in index["topics"] if t in sel_topic_set),
        "selected_question_ids": tuple(q for q in index["questions"] if q in sel_question_set),
        "selected_aspect_ids": tuple(a for a in index["aspects"] if a in sel_aspect_set),
        "out_of_scope_section_ids": tuple(s for s in index["sections"] if s not in set(sel_sections)),
        "out_of_scope_topic_ids": tuple(t for t in index["topics"] if t not in sel_topic_set),
        "out_of_scope_question_ids": tuple(
            q for q in index["questions"] if q not in sel_question_set),
        "out_of_scope_aspect_ids": tuple(
            a for a in index["aspects"] if a not in sel_aspect_set),
    }


def _profile_body_from_source(doc: dict, *, profile_path: Path) -> dict:
    """校验源 YAML，展开选择，返回**不含** ``profile_fingerprint`` 的完整 profile 体。"""
    _reject_source_unknown(doc, PROFILE_SOURCE_KEYS, f"DemoScopeProfile 源 {profile_path.name}")
    if doc["schema_version"] != DEMO_SCOPE_PROFILE_SCHEMA_VERSION:
        _err(f"profile.schema_version 必须为 {DEMO_SCOPE_PROFILE_SCHEMA_VERSION!r}")
    if doc["company_independent"] is not True:
        _err("profile.company_independent 必须为 True（生产 profile 不得含公司特判）")

    assets = verify_frozen_assets(_require_dict(doc["assets"], "profile.assets"))
    contract = load_contract_v2(str(_resolve_repo_path(
        require_rel_asset_path(doc["assets"]["contract"]["asset"], "profile", "asset"))))
    index = _index_contract(contract)

    raw_sections = doc["selected_sections"]
    raw_topics = doc["selected_topics"]
    if not isinstance(raw_sections, list) or not raw_sections:
        _err("profile.selected_sections 必须为非空 list")
    if not isinstance(raw_topics, list) or not raw_topics:
        _err("profile.selected_topics 必须为非空 list")

    sel_sections: list[str] = []
    sel_section_reason: dict[str, str] = {}
    sel_section_surfaces: dict[str, tuple[str, ...]] = {}
    for item in raw_sections:
        item = _require_dict(item, "profile.selected_sections[]")
        _reject_source_unknown(item, SELECTED_SECTION_KEYS, "profile.selected_sections[]")
        sid = _req_str(item, "section_id", "selected_sections")
        if sid in sel_section_reason:
            _err(f"profile.selected_sections 重复 section_id: {sid!r}")
        sel_sections.append(sid)
        sel_section_reason[sid] = _req_str(item, "selection_reason", f"selected_sections[{sid}]")
        sel_section_surfaces[sid] = _str_list(
            item["design_surface_ids"], f"selected_sections[{sid}].design_surface_ids")

    sel_topics: list[str] = []
    sel_topic_reason: dict[str, str] = {}
    sel_topic_surfaces: dict[str, tuple[str, ...]] = {}
    for item in raw_topics:
        item = _require_dict(item, "profile.selected_topics[]")
        _reject_source_unknown(item, SELECTED_TOPIC_KEYS, "profile.selected_topics[]")
        tid = _req_str(item, "topic_id", "selected_topics")
        if tid in sel_topic_reason:
            _err(f"profile.selected_topics 重复 topic_id: {tid!r}")
        sel_topics.append(tid)
        sel_topic_reason[tid] = _req_str(item, "selection_reason", f"selected_topics[{tid}]")
        sel_topic_surfaces[tid] = _str_list(
            item["design_surface_ids"], f"selected_topics[{tid}].design_surface_ids")
        declared_parent = _req_str(item, "section_id", f"selected_topics[{tid}]")
        actual_parent = index["section_of_topic"].get(tid)
        if actual_parent is not None and declared_parent != actual_parent:
            _err(f"selected_topics[{tid}].section_id={declared_parent!r} 与冻结 Contract 的 "
                 f"父 section {actual_parent!r} 不一致")

    part = _expand_partition(index, tuple(sel_sections), tuple(sel_topics))

    # 理由 / 设计面：section 与 topic 显式声明；其下 question / aspect 由所属 topic 继承。
    reason_by_id: dict[str, str] = {}
    surfaces_by_id: dict[str, tuple[str, ...]] = {}
    for sid in part["selected_section_ids"]:
        reason_by_id[sid] = sel_section_reason[sid]
        surfaces_by_id[sid] = sel_section_surfaces[sid]
    for tid in part["selected_topic_ids"]:
        reason_by_id[tid] = sel_topic_reason[tid]
        surfaces_by_id[tid] = sel_topic_surfaces[tid]
    for qid in part["selected_question_ids"]:
        owner = index["topic_of_question"][qid]
        reason_by_id[qid] = sel_topic_reason[owner]
        surfaces_by_id[qid] = sel_topic_surfaces[owner]
    for aid in part["selected_aspect_ids"]:
        owner = index["topic_of_aspect"][aid]
        reason_by_id[aid] = sel_topic_reason[owner]
        surfaces_by_id[aid] = sel_topic_surfaces[owner]

    contract_block = doc["assets"]["contract"]
    sp_block = doc["assets"]["source_policy"]
    ws_block = doc["assets"]["writing_spec"]
    pp_block = doc["assets"]["presentation_profile"]
    return {
        "schema_version": DEMO_SCOPE_PROFILE_SCHEMA_VERSION,
        "profile_id": doc["profile_id"],
        "profile_version": doc["profile_version"],
        "status": doc["status"],
        "created_at": doc["created_at"],
        "approved_at": doc["approved_at"],
        "frozen_at": doc["frozen_at"],
        "contract_asset": contract_block["asset"],
        "contract_version": contract_block["contract_version"],
        "contract_fingerprint": CS2.content_fingerprint(assets["contract"]),
        "source_policy_asset": sp_block["asset"],
        "source_policy_id": sp_block["policy_id"],
        "source_policy_version": sp_block["policy_version"],
        "source_policy_fingerprint": CS2.content_fingerprint(assets["source_policy"]),
        "writing_spec_asset": ws_block["asset"],
        "writing_spec_id": ws_block["writing_spec_id"],
        "writing_spec_version": ws_block["schema_version"],
        "writing_spec_fingerprint": CS2.content_fingerprint(assets["writing_spec"]),
        "presentation_profile_asset": pp_block["asset"],
        "presentation_profile_id": pp_block["presentation_profile_id"],
        "presentation_profile_version": pp_block["schema_version"],
        "presentation_profile_fingerprint": CS2.content_fingerprint(assets["presentation_profile"]),
        "selection_reason_by_id": reason_by_id,
        "design_surface_ids_by_id": {k: list(v) for k, v in surfaces_by_id.items()},
        "material_capabilities": list(_str_list(doc["material_capabilities"],
                                                "profile.material_capabilities")),
        "fallback_policy_id": doc["fallback_policy_id"],
        "budget_policy_id": doc["budget_policy_id"],
        "budget_policy_version": doc["budget_policy_version"],
        "company_independent": True,
        # wire/JSON 形态：tuple 字段在解码边界一律以 list 表达（_str_tuple 只接受 list）。
        **{k: list(v) for k, v in part.items()},
    }


def compute_profile_fingerprint_from_source(path: str | Path = DEFAULT_PROFILE_PATH) -> str:
    """只计算源 YAML 展开后的 profile 指纹（**只读**、不做 pass/fail 断言）。

    这是给 profile 作者用的指纹打印器；校验入口 ``load_demo_scope_profile()`` 始终
    fail-closed，不提供任何绕过开关。
    """
    profile_path = Path(path)
    doc = _load_yaml_doc(profile_path, "DemoScopeProfile 源")
    body = _profile_body_from_source(doc, profile_path=profile_path)
    return compute_demo_scope_profile_fingerprint(_profile_fingerprint_body(body))


def load_demo_scope_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> DemoScopeProfile:
    """加载并**严格校验** DemoScope Profile。

    fail-closed：源 YAML 未知/缺失字段、公司相关分支、未知 ID、父子归属不一致、
    四类冻结资产 version/fingerprint 缺失或漂移、Contract v1、指纹不匹配，全部拒绝。
    """
    profile_path = Path(path)
    doc = _load_yaml_doc(profile_path, "DemoScopeProfile 源")
    body = _profile_body_from_source(doc, profile_path=profile_path)
    declared = doc.get("profile_fingerprint")
    if not isinstance(declared, str) or declared == "":
        _err("profile.profile_fingerprint 缺失")
    computed = compute_demo_scope_profile_fingerprint(_profile_fingerprint_body(body))
    if declared != computed:
        _err("profile.profile_fingerprint 与展开后内容不一致（tamper/漂移 fail-closed）："
             f"声明 {declared} ≠ 计算 {computed}")
    return DemoScopeProfile.from_dict({**body, "profile_fingerprint": declared})


# ---------------------------------------------------------------------------
# 输入身份（研究前可知）
# ---------------------------------------------------------------------------

def build_scope_input_manifest(profile: DemoScopeProfile, business_input: PS.ReportJobInput,
                               source_inputs: dict) -> DemoScopeInputManifest:
    """构建 ``DemoScopeInputManifest``：只含研究前已知的稳定业务输入身份。

    - ``business_input`` 必须是现行 ``PS.ReportJobInput``；其 ``contract_version`` 必须为
      ``'v2'``，否则 fail-closed（Contract v1 拒绝）；
    - ``source_inputs`` 为封闭键集，提供文档/Evidence set/底稿算法版本/财务快照/外部快照/
      预算与模型 policy/代码指纹；
    - ``run_id`` / ``attempt`` / 时间 / 路径 / call id **不得**进入
      ``scope_input_fingerprint``（白名单外，且 ``SCOPE_INPUT_FORBIDDEN_FIELDS`` 逐项断言）。
    """
    if not isinstance(business_input, PS.ReportJobInput):
        _err(f"business_input 必须为 planning.schema.ReportJobInput，得到 "
             f"{type(business_input).__name__}")
    if business_input.contract_version != REQUIRED_CONTRACT_VERSION:
        _err(f"business_input.contract_version 必须为 {REQUIRED_CONTRACT_VERSION!r}，得到 "
             f"{business_input.contract_version!r}（Contract v1 拒绝）")
    if not business_input.job_id.strip():
        _err("business_input.job_id 必须为非空稳定报告任务身份")
    _require_dict(source_inputs, "source_inputs")
    extra = sorted(set(source_inputs) - set(SOURCE_INPUT_KEYS))
    if extra:
        _err(f"source_inputs 含未知字段: {extra}")
    missing = sorted(set(SOURCE_INPUT_KEYS) - set(source_inputs))
    if missing:
        _err(f"source_inputs 缺必填字段: {missing}")

    for forbidden in SCOPE_INPUT_FORBIDDEN_FIELDS:
        if forbidden in source_inputs:
            _err(f"source_inputs 不得含操作性字段 {forbidden!r}"
                 "（运行身份不得进入 scope 输入身份）")

    body = {
        "schema_version": DEMO_SCOPE_INPUT_MANIFEST_SCHEMA_VERSION,
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_fingerprint": profile.profile_fingerprint,
        "job_id": business_input.job_id,
        "case_input_id": source_inputs["case_input_id"],
        "company_id": business_input.company_id,
        "company_name": business_input.company_name,
        "credit_type": business_input.credit_type,
        "report_as_of": business_input.report_as_of,
        "document_id": source_inputs["document_id"],
        "document_version": source_inputs["document_version"],
        "raw_pdf_sha256": source_inputs["raw_pdf_sha256"],
        "current_evidence_set_version": source_inputs["current_evidence_set_version"],
        "substrate_dependency_versions": validate_substrate_dependency_versions(
            source_inputs["substrate_dependency_versions"]),
        # 研究前已选定的财务快照身份来自现行 ReportJobInput；不预造尚未构建的 runtime ID。
        "financial_snapshot_id": business_input.financial_snapshot_id,
        "external_policy_snapshot_id": source_inputs["external_policy_snapshot_id"],
        "budget_policy_id": source_inputs["budget_policy_id"],
        "budget_policy_version": source_inputs["budget_policy_version"],
        "model_policy_id": source_inputs["model_policy_id"],
        "code_fingerprint": source_inputs["code_fingerprint"],
        **{f"selected_{level}_ids": list(v)
           for level, v in profile.selected_id_sets().items()},
        **{f"out_of_scope_{level}_ids": list(v)
           for level, v in profile.out_of_scope_id_sets().items()},
    }
    body["scope_input_fingerprint"] = sha256_canonical(
        {k: body[k] for k in SCOPE_INPUT_FINGERPRINT_FIELDS})
    return DemoScopeInputManifest.from_dict(body)


def build_demo_run_identity(run_request: dict, input_manifest: DemoScopeInputManifest,
                            results_root: str) -> DemoRunIdentity:
    """构建 ``DemoRunIdentity``（一次运行尝试的操作性身份）。"""
    _require_dict(run_request, "run_request")
    extra = sorted(set(run_request) - set(RUN_REQUEST_KEYS))
    if extra:
        _err(f"run_request 含未知字段: {extra}")
    missing = sorted(set(RUN_REQUEST_KEYS) - set(run_request))
    if missing:
        _err(f"run_request 缺必填字段: {missing}")
    if not isinstance(results_root, str) or results_root == "":
        _err("results_root 必须为非空字符串")
    return DemoRunIdentity(
        schema_version=DEMO_RUN_IDENTITY_SCHEMA_VERSION,
        run_id=run_request["run_id"],
        attempt=run_request["attempt"],
        started_at=run_request["started_at"],
        results_root=results_root,
        job_id=input_manifest.job_id,
        scope_input_fingerprint=input_manifest.scope_input_fingerprint,
    )


# ---------------------------------------------------------------------------
# Contract v2 受控投影
# ---------------------------------------------------------------------------

def _authority_policy(raw: dict) -> EvidenceAuthorityPolicy:
    groups = []
    for g in raw.get("required_any_of") or []:
        groups.append(SourceClassGroup(
            source_classes=tuple(g["source_classes"]),
            min_grade=g.get("min_grade"),
            kind=g.get("kind"),
        ))
    return EvidenceAuthorityPolicy(
        required_any_of=tuple(groups),
        supplemental_only=tuple(raw.get("supplemental_only") or ()),
        inference_lineage_required=bool((raw.get("inference_lineage") or {}).get("required", False)),
    )


def _evidence_refs(aspect, er_registry: dict, contract_fingerprint: str,
                   contract_schema_version: str) -> tuple[EvidenceRequirementRef, ...]:
    refs: list[EvidenceRequirementRef] = []
    for er_id in aspect.evidence_requirement_ids:
        raw = er_registry.get(er_id)
        if not isinstance(raw, dict):
            _err(f"aspect {aspect.aspect_id!r} 引用了未注册的 evidence_requirement {er_id!r}")
        refs.append(EvidenceRequirementRef(
            requirement_id=er_id,
            contract_sha256=contract_fingerprint,
            requirement_fingerprint=sha256_canonical(raw),
            schema_version=contract_schema_version,
            source_classes=tuple(raw.get("source_classes") or ()),
            authority=_authority_policy(raw["authority"]) if "authority" in raw else None,
        ))
    return tuple(refs)


def _planned_evidence_requirements(question, er_registry: dict) -> tuple[dict, ...]:
    """一个 Question 的 evidence_requirements = 其全部 aspect 引用的 ER 并集（去重保序）。

    ``QuestionV2`` 自身不携带 ``evidence_requirement_ids``，因此只能由其 aspects 派生；
    这里不做任何推断或补全，未注册的 ER 直接 fail-closed。
    """
    seen: dict[str, dict] = {}
    for a in question.aspects:
        for er_id in a.evidence_requirement_ids:
            raw = er_registry.get(er_id)
            if not isinstance(raw, dict):
                _err(f"aspect {a.aspect_id!r} 引用了未注册的 evidence_requirement {er_id!r}")
            if er_id in seen:
                continue
            seen[er_id] = {
                "requirement_id": er_id,
                "evidence_kind": raw["evidence_kind"],
                "minimum_sources": raw["minimum_sources"],
                "source_classes": tuple(raw.get("source_classes") or ()),
                "required_fields": tuple(raw.get("required_fields") or ()),
                "authority_required_any_of": tuple(
                    dict(g) for g in (raw.get("authority") or {}).get("required_any_of") or ()),
            }
    return tuple(seen[k] for k in sorted(seen))


def _aspect_snapshot(aspect, profile: DemoScopeProfile, er_registry: dict,
                     contract_doc: dict) -> TopicAspectRequirementSnapshot:
    frozen_body = {
        "aspect_id": aspect.aspect_id,
        "question_id": aspect.question_id,
        "topic_id": aspect.topic_id,
        "requirement_text": aspect.requirement_text,
        "kind": aspect.kind,
        "producer_kind": aspect.producer_kind,
        "execution_path": aspect.execution_path,
        "required_fields": list(aspect.required_fields),
        "coverage_rules": list(aspect.coverage_rules),
        "complete_set_rule": aspect.complete_set_rule,
        "evidence_requirement_ids": list(aspect.evidence_requirement_ids),
        "source_policy_ref": {
            "policy_id": profile.source_policy_id,
            "policy_version": profile.source_policy_version,
            "content_fingerprint": profile.source_policy_fingerprint,
        },
        "time_scope": aspect.time_scope,
        "display_tier": aspect.display_tier,
        "content_role": aspect.content_role,
        "missing_policy": aspect.missing_policy,
        "blocking_policy": list(aspect.blocking_policy),
        "applicability_policy": aspect.applicability_policy,
        "impact_scope": list(aspect.impact_scope),
        "output_destination": aspect.output_destination,
        "derived_from": list(aspect.derived_from),
        "business_review_status": aspect.business_review_status,
        "business_review_reason": aspect.business_review_reason,
        "derived_from_scope": aspect.derived_from_scope,
        "transmission_layers": list(aspect.transmission_layers),
        "transmission_channel": aspect.transmission_channel,
    }
    canonical_fingerprint = sha256_canonical(frozen_body)
    dfs = aspect.derived_from_scope
    dependency_fingerprint = sha256_canonical({
        "aspect_canonical_fingerprint": canonical_fingerprint,
        "contract_fingerprint": profile.contract_fingerprint,
        "source_policy_fingerprint": profile.source_policy_fingerprint,
        "writing_spec_fingerprint": profile.writing_spec_fingerprint,
        "presentation_profile_fingerprint": profile.presentation_profile_fingerprint,
    })
    return TopicAspectRequirementSnapshot(
        aspect_id=aspect.aspect_id,
        question_id=aspect.question_id,
        topic_id=aspect.topic_id,
        requirement_text=aspect.requirement_text,
        kind=aspect.kind,
        producer_kind=aspect.producer_kind,
        execution_path=aspect.execution_path,
        required_fields=tuple(aspect.required_fields),
        coverage_rules=tuple(aspect.coverage_rules),
        complete_set_rule=aspect.complete_set_rule,
        evidence_requirement_ids=_evidence_refs(
            aspect, er_registry, profile.contract_fingerprint,
            contract_doc["schema_version"]),
        source_policy_ref=SourcePolicyRef(
            policy_id=profile.source_policy_id,
            policy_version=profile.source_policy_version,
            content_fingerprint=profile.source_policy_fingerprint,
        ),
        time_scope=aspect.time_scope,
        display_tier=aspect.display_tier,
        content_role=aspect.content_role,
        missing_policy=aspect.missing_policy,
        blocking_policy=tuple(aspect.blocking_policy),
        applicability_policy=aspect.applicability_policy,
        impact_scope=tuple(aspect.impact_scope),
        output_destination=aspect.output_destination,
        derived_from=tuple(aspect.derived_from),
        business_review_status=aspect.business_review_status,
        business_review_reason=aspect.business_review_reason,
        derived_from_scope=DerivedFromScope(
            include_sections=tuple(dfs["include_sections"]),
            exclude_producer_kinds=tuple(dfs.get("exclude_producer_kinds") or ()),
            exclude_display_tiers=tuple(
                dfs.get("exclude_display_tiers") or ("diagnostic_only",)),
            exclude_aspect_ids=tuple(dfs.get("exclude_aspect_ids") or ()),
            exclude_terminal_states=tuple(dfs.get("exclude_terminal_states") or (
                "NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED")),
        ) if dfs is not None else None,
        transmission_layers=tuple(aspect.transmission_layers),
        transmission_channel=aspect.transmission_channel,
        # freshness_window 由后续 topic runtime 依 source policy 的
        # freshness_windows_days 与 time_scope 派生；M930-1 只投影 Contract v2 的
        # 冻结字段，不发明映射，故显式留空。
        freshness_window=None,
        contract_version=REQUIRED_CONTRACT_VERSION,
        contract_sha256=profile.contract_fingerprint,
        canonical_fingerprint=canonical_fingerprint,
        dependency_fingerprint=dependency_fingerprint,
    )


def _union_impact_scope(question) -> tuple[str, ...]:
    """Question 的 impact_scope = 其 aspect 的 impact_scope 并集（去重保序）。"""
    out: list[str] = []
    for a in question.aspects:
        for v in a.impact_scope:
            if v not in out:
                out.append(v)
    return tuple(out)


def _blocking_rules(aspects: list) -> tuple[PS.ResolvedBlockingRule, ...]:
    out: list[PS.ResolvedBlockingRule] = []
    for a in aspects:
        for policy in a.blocking_policy:
            if policy == "NONE":
                continue
            out.append(PS.ResolvedBlockingRule(
                rule_id=f"{a.aspect_id}:blocking",
                scope_id=a.aspect_id,
                outcome=policy,
                applies=True,
            ))
    return tuple(out)


def project_contract_v2_scope(contract, profile: DemoScopeProfile,
                              input_manifest: DemoScopeInputManifest
                              ) -> DemoPlanningProjection:
    """把冻结 Contract v2 受控投影为现行 ``ReportPlan``/``SectionTask`` wire +
    ``TopicResearchRequirement`` + aspect 政策快照。

    - 只用既有 ``ReportPlan`` / ``SectionTask`` / ``PlannedQuestion`` wire，不改旧 schema；
    - ``plan_id`` 纳入 ``job_id`` + company/report_as_of；``run_id``/时间/路径不进入；
    - 同 job + 同业务输入跨 run 得到**逐字节相同**的 projection/plan/task 身份；
      仅改 job_id 必须改变 plan/task 身份；
    - 不写 Section Store / Topic Store / 任何数据库。
    """
    if contract.contract_version != REQUIRED_CONTRACT_VERSION:
        _err(f"只接受 Contract {REQUIRED_CONTRACT_VERSION!r}，得到 {contract.contract_version!r}")
    contract_fingerprint = CS2.content_fingerprint(contract.raw)
    if contract_fingerprint != profile.contract_fingerprint:
        _err("Contract fingerprint 与 Profile 声明不一致（依赖漂移 fail-closed）："
             f"实际 {contract_fingerprint} ≠ Profile {profile.contract_fingerprint}")
    if input_manifest.profile_fingerprint != profile.profile_fingerprint:
        _err("input_manifest.profile_fingerprint 与 Profile 不一致")
    if input_manifest.profile_id != profile.profile_id:
        _err("input_manifest.profile_id 与 Profile 不一致")
    for level in ("section", "topic", "question", "aspect"):
        if _manifest_id_sets(input_manifest)[level] != profile.selected_id_sets()[level]:
            _err(f"input_manifest 的 selected {level} 集合与 Profile 不一致")
        if (_manifest_out_of_scope_id_sets(input_manifest)[level]
                != profile.out_of_scope_id_sets()[level]):
            _err(f"input_manifest 的 out_of_scope {level} 集合与 Profile 不一致")

    index = _index_contract(contract)
    _verify_partition(index, profile.selected_id_sets(), profile.out_of_scope_id_sets())

    er_registry = contract.raw["evidence_requirements"]
    mapping_doc = load_frozen_asset_doc(profile.writing_spec_asset, "WritingSpec")
    if CS2.content_fingerprint(mapping_doc) != profile.writing_spec_fingerprint:
        _err("WritingSpec fingerprint 与 Profile 声明不一致")
    primary = _primary_mapping_by_aspect(_writing_spec_mappings(mapping_doc))
    for aid in profile.selected_aspect_ids:
        if aid not in primary:
            _err(f"WritingSpec 缺少 aspect {aid!r} 的 primary mapping")

    dependency_fingerprint = compute_demo_dependency_fingerprint(
        profile.contract_fingerprint, profile.source_policy_fingerprint,
        profile.writing_spec_fingerprint, profile.presentation_profile_fingerprint)
    projection_id = derive_demo_projection_id({
        "projection_rule_version": DEMO_PROJECTION_ID_RULE_VERSION,
        "profile_fingerprint": profile.profile_fingerprint,
        "scope_input_fingerprint": input_manifest.scope_input_fingerprint,
        "contract_asset": profile.contract_asset,
        "contract_version": profile.contract_version,
        "contract_fingerprint": profile.contract_fingerprint,
        "source_policy_id": profile.source_policy_id,
        "source_policy_version": profile.source_policy_version,
        "source_policy_fingerprint": profile.source_policy_fingerprint,
        "writing_spec_id": profile.writing_spec_id,
        "writing_spec_version": profile.writing_spec_version,
        "writing_spec_fingerprint": profile.writing_spec_fingerprint,
        "presentation_profile_id": profile.presentation_profile_id,
        "presentation_profile_version": profile.presentation_profile_version,
        "presentation_profile_fingerprint": profile.presentation_profile_fingerprint,
        "dependency_fingerprint": dependency_fingerprint,
    })
    plan_id = derive_demo_plan_id(
        projection_id, input_manifest.scope_input_fingerprint, profile.profile_fingerprint,
        profile.contract_fingerprint, input_manifest.job_id, input_manifest.company_id,
        input_manifest.report_as_of)

    # §5.2.1 裁决 6：依赖 dict 只由唯一公共 factory 构造，不手写键、也不复制键数字面量；
    # factory 内部按 `DEPENDENCY_VERSION_KEYS` 自证精确键集（缺键/未知键/空值都在此处
    # fail-closed），因此键集前进（如 15→18）无需改动本处。
    dependency_versions = TS.build_current_dependency_versions(
        contract_version=profile.contract_version,
        source_policy_version=profile.source_policy_version)

    bindings: list[dict] = []
    requirements: list[TopicResearchRequirement] = []
    section_tasks: list[PS.SectionTask] = []
    for sid in profile.selected_section_ids:
        section = index["section_obj"][sid]
        sel_topics = [t for t in section.topics
                      if t.topic_id in set(profile.selected_topic_ids)]
        if not sel_topics:
            _err(f"selected section {sid!r} 没有任何 selected topic（分区不一致）")
        task_id = derive_demo_task_id(projection_id, plan_id, sid)
        questions: list[PS.PlannedQuestion] = []
        output_requirements: list[dict] = []
        section_aspects: list = []
        for t in sel_topics:
            topic_aspects: list = []
            for q in t.questions:
                section_aspects.extend(q.aspects)
                topic_aspects.extend(q.aspects)
                questions.append(PS.PlannedQuestion(
                    question_id=q.question_id,
                    question=q.question,
                    priority=q.priority,
                    topic_id=t.topic_id,
                    required_aspects=tuple(a.aspect_id for a in q.aspects),
                    evidence_requirements=_planned_evidence_requirements(q, er_registry),
                    calculation_requirements=(),
                    analysis_requirements=(),
                    # 现行 PlannedQuestion wire：missing_policy 为 str，
                    # blocking_policy / impact_scope 为 tuple[str, ...]；冻结 Contract 逐字段原样携带。
                    missing_policy=q.missing_policy,
                    blocking_policy=tuple(q.blocking_policy),
                    # QuestionV2 无 impact_scope 字段：按冻结 Contract 逐 aspect 取并集
                    # （去重保序），不发明取值。
                    impact_scope=_union_impact_scope(q),
                ))
                for a in q.aspects:
                    output_requirements.append(dict(primary[a.aspect_id]))
            requirements.append(TopicResearchRequirement(
                task_id=task_id,
                company_id=input_manifest.company_id,
                report_as_of=input_manifest.report_as_of,
                contract_version=profile.contract_version,
                contract_fingerprint=profile.contract_fingerprint,
                source_policy_version=profile.source_policy_version,
                section_id=sid,
                topic_id=t.topic_id,
                question_ids=tuple(q.question_id for q in t.questions),
                aspects=tuple(_aspect_snapshot(a, profile, er_registry, contract.raw)
                              for a in topic_aspects),
                allowed_capabilities=tuple(section.allowed_capabilities),
                dependency_versions=dict(dependency_versions),
            ))
        for a in section_aspects:
            m = primary[a.aspect_id]
            bindings.append({
                "aspect_id": a.aspect_id,
                "section_id": sid,
                "topic_id": a.topic_id,
                "question_id": a.question_id,
                "producer_kind": a.producer_kind,
                "execution_path": a.execution_path,
                "kind": a.kind,
                "display_tier": a.display_tier,
                "content_role": a.content_role,
                "missing_policy": a.missing_policy,
                "blocking_policy": ",".join(a.blocking_policy),
                "impact_scope": ",".join(a.impact_scope),
                "requirement_required": True,
                "writing_spec_role": m["role"],
                "writing_spec_subsection_id": m["subsection_id"],
                "writing_spec_gap_display_policy": m["gap_display_policy"],
                "writing_spec_citation_granularity": m["citation_granularity"],
                "writing_spec_table_schema": m["table_schema"] or "",
            })
        section_tasks.append(PS.SectionTask(
            task_id=task_id,
            plan_id=plan_id,
            section_id=sid,
            title=section.title,
            purpose=section.purpose,
            research_policy=section.research_policy,
            topic_ids=tuple(t.topic_id for t in sel_topics),
            questions=tuple(questions),
            output_requirements=tuple(output_requirements),
            evaluation_rule_ids=(),
            allowed_capabilities=tuple(section.allowed_capabilities),
            blocking_rules=_blocking_rules(section_aspects),
            dependency_versions=dict(dependency_versions),
        ))

    report_plan = PS.ReportPlan(
        plan_id=plan_id,
        job_id=input_manifest.job_id,
        company_id=input_manifest.company_id,
        company_name=input_manifest.company_name,
        credit_type=input_manifest.credit_type,
        report_as_of=input_manifest.report_as_of,
        template_id=profile.contract_asset,
        input_fingerprint=input_manifest.scope_input_fingerprint,
        contract_fingerprint=profile.contract_fingerprint,
        planner_version=DEMO_PLANNER_VERSION,
        section_tasks=tuple(section_tasks),
        # 跨 run 的 projection 身份必须逐字节稳定：创建时间属于运行身份
        # （DemoRunIdentity.started_at），不进入 plan。
        created_at="",
    )
    return DemoPlanningProjection(
        schema_version=DEMO_PLANNING_PROJECTION_SCHEMA_VERSION,
        projection_id=projection_id,
        projection_rule_version=DEMO_PROJECTION_ID_RULE_VERSION,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_fingerprint=profile.profile_fingerprint,
        scope_input_fingerprint=input_manifest.scope_input_fingerprint,
        job_id=input_manifest.job_id,
        company_id=input_manifest.company_id,
        report_as_of=input_manifest.report_as_of,
        contract_asset=profile.contract_asset,
        contract_version=profile.contract_version,
        contract_fingerprint=profile.contract_fingerprint,
        source_policy_id=profile.source_policy_id,
        source_policy_version=profile.source_policy_version,
        source_policy_fingerprint=profile.source_policy_fingerprint,
        writing_spec_id=profile.writing_spec_id,
        writing_spec_version=profile.writing_spec_version,
        writing_spec_fingerprint=profile.writing_spec_fingerprint,
        presentation_profile_id=profile.presentation_profile_id,
        presentation_profile_version=profile.presentation_profile_version,
        presentation_profile_fingerprint=profile.presentation_profile_fingerprint,
        dependency_fingerprint=dependency_fingerprint,
        plan_id=plan_id,
        report_plan=report_plan,
        requirements=tuple(requirements),
        aspect_writing_bindings=tuple(bindings),
        **{f"selected_{level}_ids": v
           for level, v in profile.selected_id_sets().items()},
        **{f"out_of_scope_{level}_ids": v
           for level, v in profile.out_of_scope_id_sets().items()},
    )


def _verify_partition(index: dict, selected: dict[str, tuple[str, ...]],
                      out_of_scope: dict[str, tuple[str, ...]]) -> None:
    """selected / out_of_scope 必须是冻结 Contract 四层 ID 全集的精确无重叠覆盖。"""
    for level, universe in (("section", index["sections"]), ("topic", index["topics"]),
                            ("question", index["questions"]), ("aspect", index["aspects"])):
        sel = selected[level]
        out = out_of_scope[level]
        if len(set(sel)) != len(sel) or len(set(out)) != len(out):
            _err(f"{level} selected/out_of_scope 含重复 ID")
        overlap = sorted(set(sel) & set(out))
        if overlap:
            _err(f"{level} selected/out_of_scope 重叠: {overlap[:8]}")
        unknown = sorted((set(sel) | set(out)) - set(universe))
        if unknown:
            _err(f"{level} 含未知 ID（不在冻结 Contract）: {unknown[:8]}")
        missing = sorted(set(universe) - set(sel) - set(out))
        if missing:
            _err(f"{level} 分区不完备，遗漏 ID: {missing[:8]}")


def verify_demo_projection(projection: DemoPlanningProjection, contract,
                           writing_spec: Any | None = None) -> None:
    """对冻结 Contract（可选：typed WritingSpec）核验投影，任何漂移 fail-closed。"""
    if contract.contract_version != REQUIRED_CONTRACT_VERSION:
        _err("verify_demo_projection 只接受 Contract v2")
    contract_fingerprint = CS2.content_fingerprint(contract.raw)
    if contract_fingerprint != projection.contract_fingerprint:
        _err("projection.contract_fingerprint 与传入 Contract 不一致")
    index = _index_contract(contract)
    _verify_partition(index, {
        "section": projection.selected_section_ids, "topic": projection.selected_topic_ids,
        "question": projection.selected_question_ids, "aspect": projection.selected_aspect_ids,
    }, {
        "section": projection.out_of_scope_section_ids,
        "topic": projection.out_of_scope_topic_ids,
        "question": projection.out_of_scope_question_ids,
        "aspect": projection.out_of_scope_aspect_ids,
    })

    sel_sections = set(projection.selected_section_ids)
    sel_topics = set(projection.selected_topic_ids)
    sel_questions = set(projection.selected_question_ids)
    sel_aspects = set(projection.selected_aspect_ids)
    # §5.2.1 裁决 6：同 build 入口，唯一 factory 按 `DEPENDENCY_VERSION_KEYS` 构造精确键集。
    dependency_versions = TS.build_current_dependency_versions(
        contract_version=projection.contract_version,
        source_policy_version=projection.source_policy_version)
    for aid in projection.selected_aspect_ids:
        if index["question_of_aspect"][aid] not in sel_questions:
            _err(f"父子归属不一致：selected aspect {aid!r} 的 question 未被选中")
        if index["section_of_topic"][index["topic_of_aspect"][aid]] not in sel_sections:
            _err(f"父子归属不一致：selected aspect {aid!r} 的 section 未被选中")
    for qid in projection.selected_question_ids:
        if index["section_of_topic"][index["topic_of_question"][qid]] not in sel_sections:
            _err(f"父子归属不一致：selected question {qid!r} 的 section 未被选中")

    er_registry = contract.raw["evidence_requirements"]
    contract_schema_version = contract.raw["schema_version"]
    req_by_topic = projection.requirement_by_topic()
    if set(req_by_topic) != set(projection.selected_topic_ids):
        _err("投影的 requirement topic 集合必须恰好等于 selected_topic_ids")
    task_by_section = {t.section_id: t for t in projection.report_plan.section_tasks}
    if set(task_by_section) != sel_sections:
        _err("report_plan 的 section 集合必须恰好等于 selected_section_ids")

    for topic_id, req in req_by_topic.items():
        section_id = index["section_of_topic"][topic_id]
        task = task_by_section[section_id]
        if req.task_id != task.task_id:
            _err(f"requirement({topic_id}).task_id 必须等于其 section 的 task_id")
        if req.section_id != section_id:
            _err(f"requirement({topic_id}).section_id 与 Contract 父 section 不一致")
        want_q = [q.question_id for q in index["topic_obj"][topic_id].questions]
        if list(req.question_ids) != want_q:
            _err(f"requirement({topic_id}).question_ids 与冻结 Contract 不一致")
        want_a = [a.aspect_id for a in _topic_aspects(index["topic_obj"][topic_id])]
        got_a = [s.aspect_id for s in req.aspects]
        if got_a != want_a:
            _err(f"requirement({topic_id}).aspects 必须精确等于该 Topic 的冻结 requirement 集合")
        for snap in req.aspects:
            a = index["aspect_obj"][snap.aspect_id]
            for f in ("aspect_id", "question_id", "topic_id", "requirement_text", "kind",
                      "producer_kind", "execution_path", "complete_set_rule", "time_scope",
                      "display_tier", "content_role", "missing_policy", "output_destination",
                      "business_review_status"):
                if getattr(snap, f) != getattr(a, f):
                    _err(f"aspect {snap.aspect_id!r} 的 {f} 与冻结 Contract 漂移")
            for f in ("required_fields", "coverage_rules", "blocking_policy", "impact_scope",
                      "derived_from", "transmission_layers"):
                if tuple(getattr(snap, f)) != tuple(getattr(a, f)):
                    _err(f"aspect {snap.aspect_id!r} 的 {f} 与冻结 Contract 漂移")
            if snap.applicability_policy != a.applicability_policy:
                _err(f"aspect {snap.aspect_id!r} 的 applicability_policy 与冻结 Contract 漂移")
            if tuple(r.requirement_id for r in snap.evidence_requirement_ids) != \
                    tuple(a.evidence_requirement_ids):
                _err(f"aspect {snap.aspect_id!r} 的 evidence_requirement_ids 与冻结 Contract 漂移")
            for ref in snap.evidence_requirement_ids:
                raw = er_registry[ref.requirement_id]
                if ref.contract_sha256 != contract_fingerprint:
                    _err(f"evidence ref {ref.requirement_id} 的 contract_sha256 漂移")
                if ref.requirement_fingerprint != sha256_canonical(raw):
                    _err(f"evidence ref {ref.requirement_id} 的 requirement_fingerprint 漂移")
                if ref.schema_version != contract_schema_version:
                    _err(f"evidence ref {ref.requirement_id} 的 schema_version 漂移")
                want_sc = tuple(raw.get("source_classes") or ())
                if ref.source_classes != want_sc:
                    _err(f"evidence ref {ref.requirement_id} 的 source_classes 漂移")
                if ("authority" in raw) != (ref.authority is not None):
                    _err(f"evidence ref {ref.requirement_id} 的 authority 存在性与冻结 Contract 漂移")
            if snap.source_policy_ref.policy_id != projection.source_policy_id or \
                    snap.source_policy_ref.policy_version != projection.source_policy_version or \
                    snap.source_policy_ref.content_fingerprint != \
                    projection.source_policy_fingerprint:
                _err(f"aspect {snap.aspect_id!r} 的 source_policy_ref 漂移")
            if snap.contract_version != REQUIRED_CONTRACT_VERSION:
                _err(f"aspect {snap.aspect_id!r} 的 contract_version 漂移")
            if snap.contract_sha256 != contract_fingerprint:
                _err(f"aspect {snap.aspect_id!r} 的 contract_sha256 漂移")
            if not snap.canonical_fingerprint:
                _err(f"aspect {snap.aspect_id!r} 缺 canonical_fingerprint")
            if not snap.dependency_fingerprint:
                _err(f"aspect {snap.aspect_id!r} 缺 dependency_fingerprint")
        if req.dependency_fingerprint() != TS.compute_dependency_fingerprint(
                projection.contract_fingerprint, projection.source_policy_version,
                req.dependency_versions):
            _err(f"requirement({topic_id}) 的 dependency_fingerprint 漂移")
        if TS.validate_dependency_versions(req.dependency_versions) != \
                dict(sorted(req.dependency_versions.items())):
            _err(f"requirement({topic_id}) 的 dependency_versions 非规范形")

    bindings = {b["aspect_id"]: b for b in projection.aspect_writing_bindings}
    if set(bindings) != sel_aspects:
        _err("aspect_writing_bindings 必须恰好覆盖 selected_aspect_ids")
    for aid, b in bindings.items():
        if set(b) != set(ASPECT_WRITING_BINDING_KEYS):
            _err(f"aspect_writing_bindings[{aid}] 字段集非法")
        a = index["aspect_obj"][aid]
        for f in ("producer_kind", "execution_path", "kind", "display_tier", "content_role",
                  "missing_policy"):
            if b[f] != getattr(a, f):
                _err(f"binding({aid}).{f} 与冻结 Contract 漂移")
        if b["blocking_policy"] != ",".join(a.blocking_policy):
            _err(f"binding({aid}).blocking_policy 漂移")
        if b["topic_id"] != a.topic_id or b["question_id"] != a.question_id:
            _err(f"binding({aid}) 的父子归属漂移")
        if b["requirement_required"] is not True:
            _err(f"binding({aid}) 必须属于本 Topic 的冻结 requirement 集合")
        if b["writing_spec_role"] not in WRITING_SPEC_ROLES:
            _err(f"binding({aid}).writing_spec_role 非法")

    plan = projection.report_plan
    if plan.plan_id != derive_demo_plan_id(
            projection.projection_id, projection.scope_input_fingerprint,
            projection.profile_fingerprint, projection.contract_fingerprint,
            projection.job_id, projection.company_id, projection.report_as_of):
        _err("report_plan.plan_id 与显式版本化派生规则不一致")
    if plan.input_fingerprint != projection.scope_input_fingerprint:
        _err("report_plan.input_fingerprint 必须等于 scope_input_fingerprint")
    if plan.created_at != "":
        _err("report_plan.created_at 必须为空：时间属于运行身份，不得进入跨 run 投影身份")
    for t in plan.section_tasks:
        if t.task_id != derive_demo_task_id(projection.projection_id, plan.plan_id, t.section_id):
            _err(f"task_id 与显式版本化派生规则不一致: {t.section_id}")
        for topic_id in t.topic_ids:
            if index["section_of_topic"][topic_id] != t.section_id:
                _err(f"SectionTask.topic_ids 含不属于本 section 的 topic {topic_id!r}")
        if set(t.topic_ids) != {r.topic_id for r in req_by_topic.values()
                                if r.section_id == t.section_id}:
            _err(f"SectionTask({t.section_id}).topic_ids 与投影 requirement 不一致")
        # 任务级 Contract 字段逐项重新派生：title / purpose / research_policy /
        # allowed_capabilities / blocking_rules / dependency_versions / questions
        # 任何漂移都 fail-closed（missing-blocking policy 必须逐项保留）。
        section = index["section_obj"][t.section_id]
        sel_topics_here = [tp for tp in section.topics if tp.topic_id in sel_topics]
        if not sel_topics_here:
            _err(f"selected section {t.section_id!r} 没有任何 selected topic（分区不一致）")
        if t.title != section.title or t.purpose != section.purpose:
            _err(f"SectionTask({t.section_id}) 的 title/purpose 与冻结 Contract 漂移")
        if t.research_policy != section.research_policy:
            _err(f"SectionTask({t.section_id}).research_policy 与冻结 Contract 漂移")
        if tuple(t.allowed_capabilities) != tuple(section.allowed_capabilities):
            _err(f"SectionTask({t.section_id}).allowed_capabilities 与冻结 Contract 漂移")
        section_aspects = [a for tp in sel_topics_here for a in _topic_aspects(tp)]
        if tuple(t.blocking_rules) != tuple(_blocking_rules(section_aspects)):
            _err(f"SectionTask({t.section_id}).blocking_rules 与冻结 Contract 漂移"
                 "（missing-blocking policy 必须逐项保留）")
        if dict(t.dependency_versions) != dict(sorted(dependency_versions.items())):
            _err(f"SectionTask({t.section_id}).dependency_versions 非规范形或与投影漂移")
        want_question_ids = [q.question_id for tp in sel_topics_here for q in tp.questions]
        if [q.question_id for q in t.questions] != want_question_ids:
            _err(f"SectionTask({t.section_id}).questions 与冻结 Contract 不一致")
        for pq in t.questions:
            fq = index["question_obj"][pq.question_id]
            if pq.question != fq.question or pq.priority != fq.priority \
                    or pq.topic_id != fq.topic_id:
                _err(f"PlannedQuestion({pq.question_id}) 与冻结 Contract 漂移")
            if tuple(pq.required_aspects) != tuple(a.aspect_id for a in fq.aspects):
                _err(f"PlannedQuestion({pq.question_id}).required_aspects 与冻结 Contract 漂移")
            if pq.missing_policy != fq.missing_policy \
                    or tuple(pq.blocking_policy) != tuple(fq.blocking_policy) \
                    or tuple(pq.impact_scope) != tuple(_union_impact_scope(fq)):
                _err(f"PlannedQuestion({pq.question_id}) 的 policy / impact_scope 与冻结 "
                     "Contract 漂移")
            if tuple(pq.evidence_requirements) != tuple(
                    _planned_evidence_requirements(fq, er_registry)):
                _err(f"PlannedQuestion({pq.question_id}).evidence_requirements 与冻结 "
                     "Contract 漂移")

    if writing_spec is not None:
        ws_maps = [dict(m) for m in writing_spec.mappings]
        for m in ws_maps:
            _reject_source_unknown(m, WRITING_SPEC_MAPPING_FIELDS, "WritingSpec.mapping")
        ws_primary = _primary_mapping_by_aspect(ws_maps)
        if writing_spec.writing_spec_id != projection.writing_spec_id:
            _err("传入 WritingSpec 的 id 与投影不一致")
        if writing_spec.schema_version != projection.writing_spec_version:
            _err("传入 WritingSpec 的 schema_version 与投影不一致")
        contract_aspects = {a.aspect_id for s in contract.sections for a in s.all_aspects()}
        if not contract_aspects <= set(ws_primary):
            _err("WritingSpec 的 primary 映射必须覆盖全部冻结 Contract aspect，缺少: "
                 f"{sorted(contract_aspects - set(ws_primary))[:8]}")
        for aid in projection.selected_aspect_ids:
            if aid not in ws_primary:
                _err(f"WritingSpec 缺 aspect {aid!r} 的 primary mapping")
            b = bindings[aid]
            m = ws_primary[aid]
            for key, field in (("writing_spec_subsection_id", "subsection_id"),
                               ("writing_spec_gap_display_policy", "gap_display_policy"),
                               ("writing_spec_citation_granularity", "citation_granularity"),
                               ("writing_spec_table_schema", "table_schema")):
                if b[key] != (m[field] or ""):
                    _err(f"binding({aid}).{key} 与传入 WritingSpec 漂移")


# ---------------------------------------------------------------------------
# 运行后 manifest
# ---------------------------------------------------------------------------

def resolve_demo_scope_manifest(run_identity: DemoRunIdentity,
                                input_manifest: DemoScopeInputManifest,
                                projection: DemoPlanningProjection,
                                runtime_outputs: dict) -> ResolvedDemoScopeManifest:
    """把本轮 runtime output 追加为版本化 run record。

    只能**引用**前序对象；不得回填或改变 input manifest / plan / task / Pack current
    identity（本函数不修改任何入参对象，只读它们）。
    """
    _require_dict(runtime_outputs, "runtime_outputs")
    extra = sorted(set(runtime_outputs) - set(RUNTIME_OUTPUT_KEYS))
    if extra:
        _err(f"runtime_outputs 含未知字段: {extra}")
    missing = sorted(set(RUNTIME_OUTPUT_KEYS) - set(runtime_outputs))
    if missing:
        _err(f"runtime_outputs 缺必填字段: {missing}")
    if run_identity.job_id != input_manifest.job_id:
        _err("run_identity.job_id 必须等于 input_manifest.job_id")
    if run_identity.scope_input_fingerprint != input_manifest.scope_input_fingerprint:
        _err("run_identity.scope_input_fingerprint 必须等于 input_manifest")
    if projection.scope_input_fingerprint != input_manifest.scope_input_fingerprint:
        _err("projection.scope_input_fingerprint 必须等于 input_manifest")
    if projection.plan_id != projection.report_plan.plan_id:
        _err("projection.plan_id 与 report_plan 不一致")

    gaps: list[dict] = []
    for g in runtime_outputs["gaps"]:
        g = _require_dict(g, "runtime_outputs.gaps[]")
        _reject_source_unknown(g, RESOLVED_GAP_KEYS, "runtime_outputs.gaps[]")
        if g["target_kind"] not in GAP_TARGET_KINDS:
            _err(f"gap.target_kind 非法: {g['target_kind']!r}")
        gaps.append(dict(g))

    body = {
        "schema_version": RESOLVED_DEMO_SCOPE_MANIFEST_SCHEMA_VERSION,
        "run_id": run_identity.run_id,
        "attempt": run_identity.attempt,
        "job_id": input_manifest.job_id,
        "company_id": input_manifest.company_id,
        "report_as_of": input_manifest.report_as_of,
        "scope_input_fingerprint": input_manifest.scope_input_fingerprint,
        "projection_id": projection.projection_id,
        "plan_id": projection.plan_id,
        "task_ids": [t.task_id for t in projection.report_plan.section_tasks],
        "section_ids": list(projection.selected_section_ids),
        "document_id": input_manifest.document_id,
        "document_version": input_manifest.document_version,
        "current_evidence_set_version": input_manifest.current_evidence_set_version,
        "live_page_layout_ids": list(runtime_outputs["live_page_layout_ids"]),
        "live_alignment_ids": list(runtime_outputs["live_alignment_ids"]),
        "live_outline_ids": list(runtime_outputs["live_outline_ids"]),
        "live_span_snapshot_ids": list(runtime_outputs["live_span_snapshot_ids"]),
        "pack_ids": list(runtime_outputs["pack_ids"]),
        "external_snapshot_ids": list(runtime_outputs["external_snapshot_ids"]),
        "financial_fact_pack_artifact_id": runtime_outputs["financial_fact_pack_artifact_id"],
        "gaps": list(gaps),
    }
    fingerprint = sha256_canonical(body)
    return ResolvedDemoScopeManifest.from_dict({
        **body,
        "manifest_fingerprint": fingerprint,
        "manifest_id": derive_resolved_manifest_id(fingerprint),
    })


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------

def _business_input(job_id: str = "job_demo_backbone_0001") -> PS.ReportJobInput:
    return PS.ReportJobInput(
        job_id=job_id,
        company_id="company_fixture_a",
        company_name="Fixture A",
        credit_type="general",
        report_as_of="2026-06-30",
        contract_version=REQUIRED_CONTRACT_VERSION,
    )


def _source_inputs() -> dict:
    return {
        "case_input_id": "case_fixture_a_v1",
        "document_id": "doc_fixture_a",
        "document_version": "v1",
        "raw_pdf_sha256": "0" * 64,
        "current_evidence_set_version": "es_fixture_a_v1",
        "substrate_dependency_versions": {k: f"{k}-v1" for k in SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": "demo_budget_v1",
        "budget_policy_version": "v1",
        "model_policy_id": "demo_model_policy_v1",
        "code_fingerprint": "1" * 64,
    }


def self_check() -> dict:
    """确定性自检：不写文件、不碰数据库、不调 LLM / 网络。"""
    checks: list[dict] = []

    def _chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    profile = load_demo_scope_profile()
    _chk("profile.load", True,
         f"{profile.profile_id}@{profile.profile_version} fingerprint="
         f"{profile.profile_fingerprint[:16]}…")
    _chk("profile.status_frozen", profile.status == "frozen", profile.status)
    _chk("profile.company_independent", profile.company_independent is True, "")

    contract = load_contract_v2(str(_resolve_repo_path(profile.contract_asset)))
    index = _index_contract(contract)
    _verify_partition(index, profile.selected_id_sets(), profile.out_of_scope_id_sets())
    total = {level: len(index[level + "s"]) for level in ("section", "topic", "question",
                                                          "aspect")}
    sel = {level: len(v) for level, v in profile.selected_id_sets().items()}
    out = {level: len(v) for level, v in profile.out_of_scope_id_sets().items()}
    conservation = all(sel[level] + out[level] == total[level] for level in total)
    _chk("profile.partition_conservation", conservation,
         f"selected={sel} out_of_scope={out} contract={total}")

    manifest = build_scope_input_manifest(profile, _business_input(), _source_inputs())
    run_a = build_demo_run_identity(
        {"run_id": "demo_backbone_" + "0" * 12, "attempt": 1,
         "started_at": "2026-09-20T00:00:00Z"}, manifest, "evaluation/results")
    run_b = build_demo_run_identity(
        {"run_id": "demo_backbone_" + "1" * 12, "attempt": 2,
         "started_at": "2026-09-21T12:34:56Z"}, manifest, "evaluation/results")
    _chk("run.run_identity_differs", run_a.to_dict() != run_b.to_dict(), "")
    _chk("run.scope_input_fingerprint_stable",
         run_a.scope_input_fingerprint == run_b.scope_input_fingerprint
         == manifest.scope_input_fingerprint, "")

    proj_a = project_contract_v2_scope(contract, profile, manifest)
    proj_b = project_contract_v2_scope(contract, profile, manifest)
    _chk("projection.deterministic",
         canonical_json(proj_a.to_dict()) == canonical_json(proj_b.to_dict()), "")
    _chk("projection.everything_but_run_identity_identical",
         run_a.to_dict() != run_b.to_dict()
         and proj_a.projection_id == proj_b.projection_id
         and proj_a.plan_id == proj_b.plan_id
         and proj_a.task_ids() == proj_b.task_ids(),
         f"projection_id={proj_a.projection_id} plan_id={proj_a.plan_id}")
    verify_demo_projection(proj_a, contract)
    _chk("projection.verify", True, f"tasks={len(proj_a.task_ids())}")

    other_job = build_scope_input_manifest(profile, _business_input("job_demo_backbone_0002"),
                                           _source_inputs())
    proj_c = project_contract_v2_scope(contract, profile, other_job)
    _chk("identity.job_change_changes_plan_and_task",
         proj_c.plan_id != proj_a.plan_id and proj_c.task_ids() != proj_a.task_ids()
         and proj_c.projection_id != proj_a.projection_id,
         f"plan_id={proj_c.plan_id}")

    resolved = resolve_demo_scope_manifest(run_a, manifest, proj_a, {
        "live_page_layout_ids": ("lay_0001",),
        "live_alignment_ids": ("aln_0001",),
        "live_outline_ids": ("out_0001",),
        "live_span_snapshot_ids": ("span_0001",),
        "pack_ids": ("pack_0001",),
        "external_snapshot_ids": (),
        "financial_fact_pack_artifact_id": None,
        "gaps": ({"gap_id": "gap_0001", "target_kind": "external", "target_id": "x",
                  "reason_code": "not_obtained_in_searched_scope",
                  "searched_scope": "searched_scope_v1", "impact": "MODERATE",
                  "suggested_material_type": "external_snapshot"},),
    })
    _chk("resolved.manifest_identities", resolved.plan_id == proj_a.plan_id
         and resolved.task_ids == proj_a.task_ids()
         and resolved.scope_input_fingerprint == manifest.scope_input_fingerprint,
         f"manifest_id={resolved.manifest_id}")

    ok = all(c["ok"] for c in checks)
    return {
        "module": "planning.demo_scope",
        "ok": ok,
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
        "checks": checks,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m planning.demo_scope",
        description="M930-1 DemoScope 加载/投影/身份自检（只读；不写库、不调 LLM/网络）")
    parser.add_argument("--self-check", action="store_true",
                        help="运行确定性自检")
    parser.add_argument("--fingerprint", nargs="?", const=DEFAULT_PROFILE_PATH,
                        default=None, metavar="PROFILE_PATH",
                        help="只打印源 YAML 展开后的 profile 指纹")
    args = parser.parse_args(argv)
    if args.fingerprint is not None:
        print(compute_profile_fingerprint_from_source(args.fingerprint))
        return 0
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_main(sys.argv[1:]))

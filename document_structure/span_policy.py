"""TS4 资格策略解析层（计划 §18.3.5 / §18.4.4 / §18.8.5 / §18.8.6 / §18.14.4）。

本模块是**唯一**能把磁盘上的版本化策略解析成受信 `SpanQualificationPolicy` 的入口：

- 路径**硬编码**为 `document_structure/policies/`（正式 builder/verifier **不接受**
  provider、`policy path` 或调用者自造的策略对象）；
- 注册表**钉住**每个策略的指纹，加载时必须逐字比对；不符即 fail-closed；
- 阶段（A/B）由 `versions.SPAN_CONFIDENCE_MIN` **单点**决定，本模块只按该单点派生
  真值表，不得出现第二处"是否已裁决"的判断。

两个阶段的**当前口径**（`versions.SPAN_CONFIDENCE_MIN` 是唯一输入）：

- TS4-A：`stage == "distribution_only"`、`span_confidence_min is None`、
  `completion_enabled is False`、`set_complete_supported is False`。可产出**可核验的
  导航简介**，但**永不**支持 `set_complete`；
- TS4-B：只有 sealed `review_attestation.json` 经人工门批准后，才由 `--export-b-assets`
  **确定性导出** approval / frozen 两份记录；B 的 frozen policy 才是 `threshold_enabled`
  的当前口径。阈值只是**必要条件**：`"支持完成判定"` 不等于任何 span / aspect
  自动完成，也不得由阶段开关自动取得 `set_complete`。

历史 A 产物在 B 环境下仍必须能按**其内嵌 policy identity** 读回、重建和验证
（§18.3.2(9)、§18.14.5），其 `completion` 恒为 False；本模块**不得**用当前全局常量
去重新解释历史记录——那件事只在 `assert_current_policy`（新建入口）发生。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    sha256_canonical,
)
from document_structure.span_schema import (
    BOUNDARY_CAUSES_LEFT,
    BOUNDARY_CAUSES_RIGHT,
    BoundaryFactor,
    POLICY_STAGES,
    SpanQualificationPolicy,
)

__all__ = [
    "POLICY_DIR", "REGISTRY_FILENAME", "DISTRIBUTION_POLICY_FILENAME",
    "DEFAULT_POLICY_KEY", "FROZEN_POLICY_KEY", "TS4_A_FACTOR_VALUES",
    "TS4_A_SNIPPET_DEFAULTS",
    "REGISTRY_KEYS", "REGISTRY_POLICY_KEYS", "APPROVAL_RECORD_FILENAME",
    "FROZEN_RECORD_FILENAME", "APPROVAL_RECORD_SCHEMA_TYPE",
    "APPROVAL_RECORD_SCHEMA_VERSION", "APPROVAL_RECORD_KEYS",
    "APPROVAL_REVIEW_CHECK_IDS", "APPROVAL_REVIEW_VERDICTS", "APPROVAL_ROLES",
    "ab_gate_truth_table",
    "resolve_distribution_policy", "resolve_qualification_policy",
    "resolve_frozen_policy", "load_approval_record", "assert_current_policy",
    "current_policy_key",
    "qualification_binding_slots", "approval_authority_fingerprint",
    "derived_frozen_policy", "serialize_policy_asset", "registry_document",
    "register_frozen_policy_entry",
    "registry_fingerprint", "registry_entry_record",
    "policy_provider_authority_fingerprint", "assert_distribution_only",
    "policy_registry_summary", "PolicyResolutionError", "self_check",
]


class PolicyResolutionError(SchemaValidationError):
    """策略解析失败（缺文件 / 注册表不符 / 指纹不符 / 阶段不符）。"""


# ---------------------------------------------------------------------------
# 1. 固定路径与固定文件名（**不接受**调用者传入路径）
# ---------------------------------------------------------------------------

POLICY_DIR: Path = Path(__file__).resolve().parent / "policies"
REGISTRY_FILENAME: str = "registry_v1.json"
DISTRIBUTION_POLICY_FILENAME: str = "span_qualification_distribution_v1.json"
DEFAULT_POLICY_KEY: str = "span-qualification-distribution-v1"

#: TS4-B 的两份记录（§18.3.5：A 只绑定 distribution，B 绑定三份）。它们**只能**由
#: runner 的 `--export-b-assets` 从 sealed `review_attestation.json` 确定性导出，
#: 不得手写；A 阶段的 policy authority **不因它们存在而改变**（A 的两槽恒为 None）。
APPROVAL_RECORD_FILENAME: str = "span_qualification_approval_v1.json"
FROZEN_RECORD_FILENAME: str = "span_qualification_frozen_v1.json"
#: B 阶段 frozen policy 在注册表里的键（与 A 键并列，**追加式**登记）。
FROZEN_POLICY_KEY: str = "span-qualification-frozen-v1"

#: approval record 的封闭形状（§18.14 人工门 → TS4-B 资产）。
APPROVAL_RECORD_SCHEMA_TYPE: str = "SpanQualificationApproval"
APPROVAL_RECORD_SCHEMA_VERSION: str = "sqaa-1"
APPROVAL_RECORD_KEYS: tuple[str, ...] = (
    "schema_type", "approval_schema_version",
    "review_attestation_relpath", "review_attestation_sha256",
    "a_run_id", "a_run_identity", "a_run_manifest_sha256", "a_root_identity",
    "a_machine_index_identity", "a_machine_artifact_index_sha256",
    "a_manual_review_sha256", "a_policy_decision_sha256",
    "a_aggregate_snapshot_sha256", "a_confidence_distribution_sha256",
    "decision", "review_verdict", "reviewer_roles",
    "factor_entries", "threshold", "policy_constants",
    "frozen_policy_key", "frozen_policy_fingerprint",
    "approval_authority_fingerprint",
)

#: §18.14.4 的 8 项人工验收清单 ID（固定顺序、恰好各一条 verdict）。
APPROVAL_REVIEW_CHECK_IDS: tuple[str, ...] = (
    "main_business_body_complete",
    "core_competitiveness_subheadings_separated",
    "subsidiary_materials_not_leaked",
    "financial_note_text_and_tables_isolated",
    "cross_heading_evidence_split_into_components",
    "table_caption_and_unit_lines_absent_from_snippets",
    "non_300750_positive_fixture_and_legacy_negative",
    "all_conservation_and_coverage_gaps_listed",
)
APPROVAL_REVIEW_VERDICTS: tuple[str, ...] = ("pass", "fail", "not_reviewed")
#: 批准身份：用户 + Codex 双签，缺一不可。
APPROVAL_ROLES: tuple[str, ...] = ("user", "codex")

#: §18.8.5 的边界成因 → 因子表。**唯一**权威数值来源；策略记录由它构造，
#: 因此"改一个因子"必然改指纹，而指纹被注册表钉死。
TS4_A_FACTOR_VALUES: tuple[tuple[str, str, float], ...] = (
    ("left", "preceding_heading", 1.00),
    ("left", "resume_after_empty", 0.90),
    ("left", "resume_after_table_inside", 0.85),
    ("left", "resume_after_table_adjacency", 0.75),
    ("left", "resume_after_non_content", 0.75),
    ("right", "next_heading", 1.00),
    ("right", "document_end", 0.90),
    ("right", "empty", 0.90),
    ("right", "table_inside", 0.85),
    ("right", "table_adjacency", 0.75),
    ("right", "formal_unassigned", 0.75),
    ("right", "non_content", 0.75),
)

#: §18.8.6 的简介抽取常量。终止符**最长优先**（`……` 必须先于 `。` 匹配）。
TS4_A_SNIPPET_DEFAULTS: dict[str, Any] = {
    "max_snippets_per_node": 3,
    "max_snippet_chars": 120,
    "min_snippet_chars": 20,
    "max_total_snippet_chars": 300,
    "sentence_terminators": ("……", "。", "！", "？", ".", "!", "?", ";", "；"),
    "closing_quotes": ("”", "’", "\"", "'"),
}

#: 注册表顶层键（未知键拒绝）。
REGISTRY_KEYS: tuple[str, ...] = (
    "schema_type", "registry_version", "default_policy_key", "policies",
)

#: 注册表内每个策略条目的键（未知键拒绝）。
REGISTRY_POLICY_KEYS: tuple[str, ...] = ("file", "policy_fingerprint")


# ---------------------------------------------------------------------------
# 2. A/B 真值表：以 `versions.SPAN_CONFIDENCE_MIN` 为**唯一**输入
# ---------------------------------------------------------------------------

def ab_gate_truth_table() -> dict:
    """由 `versions.SPAN_CONFIDENCE_MIN` 派生四项判定（A/B 真值表的唯一实现）。

    - TS4-A（`None`）：`distribution_only` / 不得完成 / 不得 `set_complete`；
    - TS4-B（已裁决）：`threshold_enabled` / 允许完成 / 允许 `set_complete`。

    本轮（TS4-A）**必须**得到第一行；测试在受控 `try/finally` 里临时改写该常量以
    证明真值表确实以它为唯一输入，而不是被写死在别处。
    """
    v = V.SPAN_CONFIDENCE_MIN
    decided = v is not None
    stage = "threshold_enabled" if decided else "distribution_only"
    if stage not in POLICY_STAGES:  # pragma: no cover - 词表由 span_schema 封闭
        raise PolicyResolutionError(f"未登记的阶段 {stage!r}")
    if decided and not isinstance(v, (int, float)):
        raise PolicyResolutionError(f"SPAN_CONFIDENCE_MIN 必须为实数或 None，得到 {v!r}")
    return {
        "span_confidence_min": v,
        "threshold_decided": decided,
        "stage": stage,
        "completion_enabled": decided,
        "set_complete_supported": decided,
        "threshold_required": decided,
    }


def assert_distribution_only(policy: SpanQualificationPolicy) -> None:
    """TS4-A 前置门：任何非 `distribution_only` 的策略都不得进入本批次的正式链路。"""
    table = ab_gate_truth_table()
    if table["stage"] != "distribution_only":
        raise PolicyResolutionError(
            "当前已进入 threshold_enabled 阶段，TS4-A 的 distribution-only 链路不得"
            "再被当作正式口径使用")
    if policy.stage != "distribution_only":
        raise PolicyResolutionError(
            f"策略 {policy.policy_key!r} 的阶段为 {policy.stage!r}，TS4-A 只接受 "
            "distribution_only")
    if policy.span_confidence_min is not None:
        raise PolicyResolutionError("distribution_only 策略的 span_confidence_min 必须为 None")
    if policy.completion_enabled or policy.set_complete_supported:
        raise PolicyResolutionError("distribution_only 策略不得开启 completion / set_complete")


def assert_current_policy(policy: SpanQualificationPolicy) -> None:
    """**当前**阶段的单点门（§18.3.2(9)）：只用于**新建**入口，不用于历史读回。

    这是"当前轮次"与"历史记录"之间唯一允许的那道绑定：策略记录自身只对**自身阶段**
    自洽负责，究竟是 A 还是 B 由 `versions.SPAN_CONFIDENCE_MIN` 单点决定，并在此处
    强制。历史 A 记录因此永远不会被当前全局常量重新解释——它走的是复核路径
    （`span_builder._QualificationPolicyProvider.for_verification`），不经本函数。
    """
    table = ab_gate_truth_table()
    if policy.stage != table["stage"]:
        raise PolicyResolutionError(
            f"策略 {policy.policy_key!r} 的阶段 {policy.stage!r} 与当前阶段 "
            f"{table['stage']!r} 不一致（当前阶段由 versions.SPAN_CONFIDENCE_MIN="
            f"{table['span_confidence_min']!r} 单点派生；fail-closed）")
    if table["threshold_decided"]:
        if policy.span_confidence_min is None:
            raise PolicyResolutionError(
                "threshold_enabled 阶段的策略必须携带有限阈值（fail-closed）")
        if float(policy.span_confidence_min) != float(table["span_confidence_min"]):
            raise PolicyResolutionError(
                f"策略 {policy.policy_key!r} 的阈值 "
                f"{policy.span_confidence_min!r} 与 SPAN_CONFIDENCE_MIN="
                f"{table['span_confidence_min']!r} 不一致（阈值必须与 frozen policy "
                "同步发布，fail-closed）")
        if not policy.completion_enabled or not policy.set_complete_supported:
            raise PolicyResolutionError(
                "threshold_enabled 阶段的策略必须同时开启 completion 与 "
                "set_complete_supported（fail-closed）")
    else:
        assert_distribution_only(policy)


# ---------------------------------------------------------------------------
# 3. 策略构造（唯一构造点；不得由调用者拼装）
# ---------------------------------------------------------------------------

def _distribution_policy() -> SpanQualificationPolicy:
    table = ab_gate_truth_table()
    if table["stage"] != "distribution_only":
        raise PolicyResolutionError(
            "已裁决阈值：分布口径策略不得在 threshold_enabled 阶段构造")
    factors = tuple(BoundaryFactor(side=side, cause=cause, factor=factor)
                    for (side, cause, factor) in TS4_A_FACTOR_VALUES)
    _assert_factor_table_complete(factors)
    return SpanQualificationPolicy.create(
        policy_key=DEFAULT_POLICY_KEY,
        stage="distribution_only",
        span_confidence_min=None,
        factor_entries=factors,
        completion_enabled=False,
        set_complete_supported=False,
        **TS4_A_SNIPPET_DEFAULTS,
    )


def _assert_factor_table_complete(factors: tuple[BoundaryFactor, ...]) -> None:
    """边界成因**穷尽**且顺序固定（§18.8.5 的"穷尽真值表"）。"""
    expected = ([(("left", c)) for c in BOUNDARY_CAUSES_LEFT]
                + [(("right", c)) for c in BOUNDARY_CAUSES_RIGHT])
    got = [(bf.side, bf.cause) for bf in factors]
    if got != expected:
        raise PolicyResolutionError(
            f"边界因子表必须穷尽且按固定词表顺序，期望 {expected}，得到 {got}")


def derived_frozen_policy(approval: dict) -> SpanQualificationPolicy:
    """由 approval record **确定性派生** frozen current policy（§18.8.5 / §18.14）。

    派生是**纯函数**：同样的 approval 必然得到同一份 policy（同 `policy_id`、同
    `policy_fingerprint`）。磁盘上的 `span_qualification_frozen_v1.json` 必须与它
    逐字段等值——因此"只改文件、只改自报指纹"都过不了 `resolve_frozen_policy`。
    """
    factors = tuple(BoundaryFactor(side=row["side"], cause=row["cause"],
                                   factor=row["factor"])
                    for row in approval["factor_entries"])
    _assert_factor_table_complete(factors)
    constants = approval["policy_constants"]
    return SpanQualificationPolicy.create(
        policy_key=FROZEN_POLICY_KEY,
        stage="threshold_enabled",
        span_confidence_min=float(approval["threshold"]),
        factor_entries=factors,
        completion_enabled=True,
        set_complete_supported=True,
        **{name: constants[name] for name in (
            "max_snippets_per_node", "max_snippet_chars", "min_snippet_chars",
            "max_total_snippet_chars")},
        sentence_terminators=tuple(constants["sentence_terminators"]),
        closing_quotes=tuple(constants["closing_quotes"]),
    )


# ---------------------------------------------------------------------------
# 4. 注册表解析（固定路径；指纹钉死）
# ---------------------------------------------------------------------------

def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise PolicyResolutionError(f"{what}不存在：{path}")
    try:
        raw = path.read_bytes()
    except OSError as e:  # pragma: no cover - 文件系统级错误
        raise PolicyResolutionError(f"{what}不可读：{path}（{e}）") from e
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PolicyResolutionError(f"{what}不是合法 UTF-8 JSON：{path}（{e}）") from e
    if not isinstance(data, dict):
        raise PolicyResolutionError(f"{what}顶层必须为对象，得到 {type(data).__name__}")
    return data


def _registry_document(data: dict) -> dict:
    """校验一份候选注册表文档（不读文件），供追加登记在**写入前**预检。"""
    unknown = set(data) - set(REGISTRY_KEYS)
    if unknown:
        raise PolicyResolutionError(f"策略注册表含未知键：{sorted(unknown)}")
    if data.get("schema_type") != "SpanQualificationRegistry":
        raise PolicyResolutionError(
            f"策略注册表 schema_type 必须为 'SpanQualificationRegistry'，"
            f"得到 {data.get('schema_type')!r}")
    if data.get("registry_version") != V.SPAN_QUALIFICATION_POLICY_VERSION:
        raise PolicyResolutionError(
            f"策略注册表 registry_version 必须为 "
            f"{V.SPAN_QUALIFICATION_POLICY_VERSION!r}，得到 {data.get('registry_version')!r}")
    policies = data.get("policies")
    if not isinstance(policies, dict) or not policies:
        raise PolicyResolutionError("策略注册表的 policies 必须为非空对象")
    for key, entry in policies.items():
        if not isinstance(key, str) or key == "":
            raise PolicyResolutionError(f"策略键必须为非空字符串，得到 {key!r}")
        if not isinstance(entry, dict):
            raise PolicyResolutionError(f"策略条目 {key!r} 必须为对象")
        unknown_e = set(entry) - set(REGISTRY_POLICY_KEYS)
        if unknown_e:
            raise PolicyResolutionError(f"策略条目 {key!r} 含未知键：{sorted(unknown_e)}")
        for k in REGISTRY_POLICY_KEYS:
            if k not in entry:
                raise PolicyResolutionError(f"策略条目 {key!r} 缺字段 {k!r}")
        fname = entry["file"]
        if not isinstance(fname, str) or "/" in fname or "\\" in fname:
            raise PolicyResolutionError(
                f"策略条目 {key!r} 的 file 必须为同目录文件名，得到 {fname!r}")
        if not isinstance(entry["policy_fingerprint"], str):
            raise PolicyResolutionError(f"策略条目 {key!r} 的指纹必须为字符串")
    default_key = data.get("default_policy_key")
    if default_key not in policies:
        raise PolicyResolutionError(
            f"default_policy_key={default_key!r} 未在 policies 中登记")
    return data


def _registry() -> dict:
    return _registry_document(_read_json(POLICY_DIR / REGISTRY_FILENAME, "策略注册表"))


#: `policies/` 目录的**既有**序列化约定：`indent=2`、`sort_keys`、UTF-8（非 ASCII 原样）、
#: CRLF 行尾、末尾换行。它由同目录既有资产自身的字节定义，不由本模块"发明"：
#: `serialize_policy_asset` 必须能把既有文件原样还原（见 `register_frozen_policy_entry`）。
_REGISTRY_NEWLINE: str = "\r\n"


def serialize_policy_asset(payload: dict) -> bytes:
    """按 `policies/` 目录的既有约定序列化一份策略资产（含注册表）。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return text.replace("\n", _REGISTRY_NEWLINE).encode("utf-8")


def registry_document() -> dict:
    """已校验的注册表**原文**（只读；供 runner 展示追加前后身份）。"""
    return _registry()


def register_frozen_policy_entry(frozen_policy: SpanQualificationPolicy) -> dict:
    """把 B 的 frozen 条目**追加**登记进 `registry_v1.json`（只增不改）。

    四道自证缺一不可：

    1. `serialize_policy_asset` 能把**既有**注册表字节原样还原 —— 否则追加会顺手
       重写历史字节，直接拒绝；
    2. `FROZEN_POLICY_KEY` 此前**不存在** —— 不得覆盖 / 重写任何历史 policy ID；
    3. 写入后去掉新条目，注册表与写入前 canonical 全等 —— 既有 A entry 与
       `default_policy_key` 逐字段未变；
    4. 写入后的文档能过同一套注册表校验。
    """
    path = POLICY_DIR / REGISTRY_FILENAME
    original = path.read_bytes()
    registry = _registry()
    if serialize_policy_asset(registry) != original:
        raise PolicyResolutionError(
            "既有 registry_v1.json 的字节与 policies/ 目录序列化约定不一致；"
            "追加登记会改写历史字节，拒绝（fail-closed）")
    if FROZEN_POLICY_KEY in registry["policies"]:
        raise PolicyResolutionError(
            f"策略注册表已登记 {FROZEN_POLICY_KEY!r}；追加登记不得覆盖既有条目，"
            "也不得重写历史 policy ID（fail-closed）")
    before = dict(registry["policies"])
    updated = {
        "schema_type": registry["schema_type"],
        "registry_version": registry["registry_version"],
        "default_policy_key": registry["default_policy_key"],
        "policies": dict(before),
    }
    updated["policies"][FROZEN_POLICY_KEY] = {
        "file": FROZEN_RECORD_FILENAME,
        "policy_fingerprint": frozen_policy.policy_fingerprint,
    }
    _registry_document(updated)  # 写入前预检：新文档必须自身合法
    path.write_bytes(serialize_policy_asset(updated))
    after = _registry()
    if FROZEN_POLICY_KEY not in after["policies"]:
        raise PolicyResolutionError("追加登记后注册表未包含 B 条目（fail-closed）")
    after_without = {k: v for k, v in after["policies"].items()
                     if k != FROZEN_POLICY_KEY}
    if canonical_json(after_without) != canonical_json(before):
        raise PolicyResolutionError(
            "追加登记改动了既有策略条目（fail-closed）")
    if after["default_policy_key"] != registry["default_policy_key"]:
        raise PolicyResolutionError("追加登记改动了 default_policy_key（fail-closed）")
    return after


def registry_fingerprint() -> str:
    """注册表**文件内容**的规范指纹（供 run_manifest 记录与索引校验）。"""
    data = _registry()
    return sha256_canonical(data)


def current_policy_key() -> str:
    """当前阶段在注册表里的策略键（A → distribution；B → frozen）。"""
    table = ab_gate_truth_table()
    return DEFAULT_POLICY_KEY if table["stage"] == "distribution_only" \
        else FROZEN_POLICY_KEY


def resolve_qualification_policy(policy_key: str | None = None) -> SpanQualificationPolicy:
    """解析并**钉住**一个版本化资格策略（固定目录，无调用者路径）。

    `policy_key=None` 表示"**当前**阶段的策略"（A → distribution，B → frozen）。
    解析出的记录还必须与当前阶段一致；历史阶段的记录只能走复核路径
    （`span_builder` 的 `for_verification`），不得经由本入口被当成当前口径。
    """
    table = ab_gate_truth_table()
    registry = _registry()
    key = current_policy_key() if policy_key is None else policy_key
    entry = registry["policies"].get(key)
    if entry is None:
        raise PolicyResolutionError(f"策略注册表未登记策略 {key!r}")
    data = _read_json(POLICY_DIR / entry["file"], f"策略文件 {entry['file']}")
    policy = SpanQualificationPolicy.from_dict(data)
    if policy.policy_key != key:
        raise PolicyResolutionError(
            f"策略文件的 policy_key={policy.policy_key!r} 与注册键 {key!r} 不一致")
    if policy.policy_fingerprint != entry["policy_fingerprint"]:
        raise PolicyResolutionError(
            f"策略 {key!r} 的指纹与注册表钉住值不一致："
            f"{policy.policy_fingerprint!r} != {entry['policy_fingerprint']!r}"
            "（策略被改动，须显式重新冻结并更新注册表）")
    if policy.stage != table["stage"]:
        raise PolicyResolutionError(
            f"策略 {key!r} 的阶段 {policy.stage!r} 与当前 A/B 阶段 "
            f"{table['stage']!r}（由 versions.SPAN_CONFIDENCE_MIN="
            f"{table['span_confidence_min']!r} 单点派生）不一致"
            "（策略文件须按当前阶段重新冻结；历史记录只能走复核路径）")
    # 注册表钉指纹只能证明"文件没被悄悄改"，不能证明"冻结时写进去的就是 §18.8.5 的表"。
    # 因此这里再把**因子表与简介常量**逐项对回代码中的已冻结规格：两者不一致即
    # fail-closed，而不是"按文件为准"地静默采用一份偏离规格的策略。
    got_factors = [(bf.side, bf.cause, bf.factor) for bf in policy.factor_entries]
    if got_factors != list(TS4_A_FACTOR_VALUES):
        raise PolicyResolutionError(
            "策略的边界因子表偏离 §18.8.5 已冻结规格："
            f"{got_factors} != {list(TS4_A_FACTOR_VALUES)}")
    for name, expected_value in TS4_A_SNIPPET_DEFAULTS.items():
        got_value = getattr(policy, name)
        if isinstance(expected_value, tuple):
            got_value = tuple(got_value)
        if got_value != expected_value:
            raise PolicyResolutionError(
                f"策略的 {name} 偏离 §18.8.6 已冻结规格："
                f"{got_value!r} != {expected_value!r}")
    return policy


def resolve_distribution_policy() -> SpanQualificationPolicy:
    """TS4-A 正式入口：解析分布口径策略，并断言本轮确实停在 A。"""
    policy = resolve_qualification_policy(DEFAULT_POLICY_KEY)
    assert_distribution_only(policy)
    return policy


# ---------------------------------------------------------------------------
# 3b. TS4-B：approval / frozen 记录（§18.8.5 / §18.14.4）
# ---------------------------------------------------------------------------
#
# 这两份记录**只能**由 runner 的 `--export-b-assets` 从 sealed attestation 导出。本模块
# 对它们的态度与对策略文件一致：**不采信任何自报字段**。`approval_authority_fingerprint`
# 每次由 payload（去掉自身字段）重算；frozen 记录必须与由 approval **确定性派生**出来的
# 那一份 policy 逐字段等值。因此"改一个因子并同步重算所有 ID/hash"仍然过不了。

_SHA256_HEX = "0123456789abcdef"


def _need_sha256(value: Any, what: str) -> str:
    if not isinstance(value, str) or len(value) != 64 \
            or any(ch not in _SHA256_HEX for ch in value):
        raise PolicyResolutionError(f"{what} 必须为 64 位小写十六进制 sha256，得到 {value!r}")
    return value


def _need_finite_number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyResolutionError(f"{what} 必须为实数，得到 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise PolicyResolutionError(f"{what} 必须为有限实数，得到 {value!r}")
    if not 0.0 <= number <= 1.0:
        raise PolicyResolutionError(f"{what} 必须位于 [0,1]，得到 {value!r}")
    return number


def approval_authority_fingerprint(record: dict) -> str:
    """§18.3.5 的唯一非循环公式：`sha256_canonical(payload 去掉自身字段)`。"""
    payload = {k: v for k, v in record.items()
               if k != "approval_authority_fingerprint"}
    return sha256_canonical(payload)


def load_approval_record() -> dict:
    """读取并**独立重算** approval record（固定目录；不接受调用者路径）。

    任一字段缺失 / 多余 / 类型不符，或 8 项 verdict 非全 pass、批准身份不是
    `user + codex`、decision 非 `approve`、因子表与 §18.8.5 批准表不一致、阈值非有限或
    与 `versions.SPAN_CONFIDENCE_MIN` 不一致、自报指纹与重算值不一致 —— 全部 fail-closed。
    """
    data = _read_json(POLICY_DIR / APPROVAL_RECORD_FILENAME, "审批记录")
    unknown = sorted(set(data) - set(APPROVAL_RECORD_KEYS))
    missing = sorted(set(APPROVAL_RECORD_KEYS) - set(data))
    if unknown:
        raise PolicyResolutionError(f"审批记录含未知字段：{unknown}（fail-closed）")
    if missing:
        raise PolicyResolutionError(f"审批记录缺字段：{missing}（fail-closed）")
    if data["schema_type"] != APPROVAL_RECORD_SCHEMA_TYPE:
        raise PolicyResolutionError(
            f"审批记录 schema_type 必须为 {APPROVAL_RECORD_SCHEMA_TYPE!r}，"
            f"得到 {data['schema_type']!r}")
    if data["approval_schema_version"] != APPROVAL_RECORD_SCHEMA_VERSION:
        raise PolicyResolutionError(
            f"审批记录 approval_schema_version 必须为 "
            f"{APPROVAL_RECORD_SCHEMA_VERSION!r}，得到 "
            f"{data['approval_schema_version']!r}")
    # 人工门：只有 decision=approve + 8 项全 pass + 双签身份才取得批准资格。
    if data["decision"] != "approve":
        raise PolicyResolutionError(
            f"审批记录的 decision 必须为 'approve'，得到 {data['decision']!r}"
            "（其余 decision 不得取得 approval eligibility）")
    verdicts = data["review_verdict"]
    if not isinstance(verdicts, dict) or sorted(verdicts) != sorted(
            APPROVAL_REVIEW_CHECK_IDS):
        raise PolicyResolutionError(
            f"审批记录的 review_verdict 必须**恰好**覆盖 8 个固定 check ID，得到 "
            f"{sorted(verdicts) if isinstance(verdicts, dict) else verdicts!r}"
            "（fail-closed）")
    not_pass = sorted(k for k, v in verdicts.items() if v != "pass")
    if not_pass:
        raise PolicyResolutionError(
            f"审批记录的 review_verdict 必须全部为 'pass'，未通过项：{not_pass}"
            "（任一非 pass 即不得导出 B 资产）")
    if list(data["reviewer_roles"]) != list(APPROVAL_ROLES):
        raise PolicyResolutionError(
            f"审批记录的 reviewer_roles 必须恰为 {list(APPROVAL_ROLES)}，"
            f"得到 {data['reviewer_roles']!r}")
    # 因子表：逐项、按序、恰好一致（缺失 / 重复 / 多余 / 改单项 / 换序全部拒绝）。
    raw_factors = data["factor_entries"]
    if not isinstance(raw_factors, list):
        raise PolicyResolutionError("审批记录的 factor_entries 必须为数组")
    got: list[tuple[str, str, float]] = []
    for row in raw_factors:
        if not isinstance(row, dict) or sorted(row) != ["cause", "factor", "side"]:
            raise PolicyResolutionError(
                f"审批记录的因子项必须恰含 side/cause/factor，得到 {row!r}")
        got.append((row["side"], row["cause"], round(
            _need_finite_number(row["factor"],
                                f"因子 ({row['side']},{row['cause']})"), V.FLOAT_PRECISION)))
    if got != [(s, c, round(f, V.FLOAT_PRECISION))
               for (s, c, f) in TS4_A_FACTOR_VALUES]:
        raise PolicyResolutionError(
            "审批记录的边界因子表偏离 §18.8.5 已批准表（不得只改单项 / 换序 / 增删）："
            f"{got} != {list(TS4_A_FACTOR_VALUES)}")
    threshold = _need_finite_number(data["threshold"], "审批记录的 threshold")
    if V.SPAN_CONFIDENCE_MIN is None or threshold != float(V.SPAN_CONFIDENCE_MIN):
        raise PolicyResolutionError(
            "审批记录的 threshold 必须与该阶段发布的 SPAN_CONFIDENCE_MIN 一致："
            f"{threshold!r} != {V.SPAN_CONFIDENCE_MIN!r}（threshold 非批准值即拒绝）")
    constants = data["policy_constants"]
    if not isinstance(constants, dict):
        raise PolicyResolutionError("审批记录的 policy_constants 必须为对象")
    if data["frozen_policy_key"] != FROZEN_POLICY_KEY:
        raise PolicyResolutionError(
            f"审批记录的 frozen_policy_key 必须为 {FROZEN_POLICY_KEY!r}，"
            f"得到 {data['frozen_policy_key']!r}")
    for name, expected_value in TS4_A_SNIPPET_DEFAULTS.items():
        got_value = constants.get(name)
        if isinstance(expected_value, tuple):
            got_value = tuple(got_value) if isinstance(got_value, list) else got_value
        if got_value != expected_value:
            raise PolicyResolutionError(
                f"审批记录的 policy_constants[{name!r}] 偏离 §18.8.6 已冻结规格："
                f"{got_value!r} != {expected_value!r}")
    for field, what in (
            ("review_attestation_sha256", "审批记录的 review_attestation_sha256"),
            ("a_run_manifest_sha256", "审批记录的 a_run_manifest_sha256"),
            ("a_machine_artifact_index_sha256",
             "审批记录的 a_machine_artifact_index_sha256"),
            ("a_manual_review_sha256", "审批记录的 a_manual_review_sha256"),
            ("a_policy_decision_sha256", "审批记录的 a_policy_decision_sha256"),
            ("a_aggregate_snapshot_sha256",
             "审批记录的 a_aggregate_snapshot_sha256"),
            ("a_confidence_distribution_sha256",
             "审批记录的 a_confidence_distribution_sha256"),
            ("a_machine_index_identity", "审批记录的 a_machine_index_identity"),
            ("frozen_policy_fingerprint", "审批记录的 frozen_policy_fingerprint")):
        _need_sha256(data[field], what)
    for field in ("review_attestation_relpath", "a_run_id"):
        if not isinstance(data[field], str) or data[field] == "":
            raise PolicyResolutionError(f"审批记录的 {field} 必须为非空字符串")
    for field in ("a_run_identity", "a_root_identity"):
        if not isinstance(data[field], dict) or not data[field]:
            raise PolicyResolutionError(f"审批记录的 {field} 必须为非空对象")
    # 自报指纹必须与**重算**值一致（不得只信任记录内自报字符串）。
    recomputed = approval_authority_fingerprint(data)
    if data["approval_authority_fingerprint"] != recomputed:
        raise PolicyResolutionError(
            "审批记录的 approval_authority_fingerprint 与重算值不一致："
            f"{data['approval_authority_fingerprint']!r} != {recomputed!r}"
            "（provider 不采信自报值，fail-closed）")
    # frozen 记录必须恰是由本 approval 派生出的那一份 policy。
    derived = derived_frozen_policy(data)
    if data["frozen_policy_fingerprint"] != derived.policy_fingerprint:
        raise PolicyResolutionError(
            "审批记录的 frozen_policy_fingerprint 与由它派生的 frozen policy 不一致："
            f"{data['frozen_policy_fingerprint']!r} != {derived.policy_fingerprint!r}"
            "（fail-closed）")
    return data


def resolve_frozen_policy() -> SpanQualificationPolicy:
    """TS4-B 正式入口：解析 frozen current policy，并断言它就是**当前**口径。

    三道门缺一不可：(1) 注册表把 `FROZEN_POLICY_KEY` 的指纹钉死；(2) 磁盘上的 frozen
    记录与由 approval record **确定性派生**的 policy 逐字段等值；(3) `assert_current_policy`。
    """
    approval = load_approval_record()
    derived = derived_frozen_policy(approval)
    registry = _registry()
    entry = registry["policies"].get(FROZEN_POLICY_KEY)
    if entry is None:
        raise PolicyResolutionError(
            f"策略注册表未登记策略 {FROZEN_POLICY_KEY!r}（frozen policy 必须与 "
            "approval 同时追加登记，fail-closed）")
    if entry["file"] != FROZEN_RECORD_FILENAME:
        raise PolicyResolutionError(
            f"策略注册表把 {FROZEN_POLICY_KEY!r} 指向 {entry['file']!r}，"
            f"必须为 {FROZEN_RECORD_FILENAME!r}")
    if entry["policy_fingerprint"] != derived.policy_fingerprint:
        raise PolicyResolutionError(
            f"策略 {FROZEN_POLICY_KEY!r} 的指纹与注册表钉住值不一致："
            f"{derived.policy_fingerprint!r} != {entry['policy_fingerprint']!r}"
            "（策略被改动，须显式重新冻结并更新注册表）")
    frozen_data = _read_json(POLICY_DIR / FROZEN_RECORD_FILENAME, "冻结策略记录")
    expected = derived.to_dict()
    for name in sorted(set(frozen_data) | set(expected)):
        got_value = frozen_data.get(name)
        want_value = expected.get(name)
        if isinstance(want_value, list):
            got_value = list(got_value) if isinstance(got_value, list) else got_value
        if canonical_json(got_value) != canonical_json(want_value):
            raise PolicyResolutionError(
                f"冻结策略记录的 {name!r} 与由 approval 派生出的 frozen policy 不等："
                f"{got_value!r} != {want_value!r}（不得只信任记录内自报 fingerprint，"
                "fail-closed）")
    assert_current_policy(derived)
    return derived


def qualification_binding_slots(policy: SpanQualificationPolicy) -> tuple:
    """qualification 载荷的四个绑定槽（§18.8.5 末段）。

    A 阶段四者**全部为 None**；B 阶段四者**全部为 64 位 sha256** 且与仓库内 approval
    record 精确一致。判定只依据**策略记录自身**的阶段，因此历史 A 快照在 B 环境下
    重算出的四槽仍为 None，其身份逐位不变。
    """
    if policy.stage == "distribution_only":
        return (None, None, None, None)
    if policy.stage != "threshold_enabled":
        raise PolicyResolutionError(f"未登记的策略阶段 {policy.stage!r}（fail-closed）")
    approval = load_approval_record()
    derived = derived_frozen_policy(approval)
    if derived.policy_key != policy.policy_key \
            or derived.policy_fingerprint != policy.policy_fingerprint:
        raise PolicyResolutionError(
            "该 threshold_enabled 策略不是由仓库内 approval record 派生出的 frozen "
            "policy（fail-closed）")
    return (approval_authority_fingerprint(approval),
            approval["a_aggregate_snapshot_sha256"],
            approval["a_confidence_distribution_sha256"],
            approval["review_attestation_sha256"])


# ---------------------------------------------------------------------------
# 4. 逐 policy 的稳定 authority 记录（§18.3.5）
# ---------------------------------------------------------------------------
#
# 组合根**不接受**调用者传入的记录，也不采信任何对象自报的指纹：这里的每个
# sha256 都由固定目录里的文件字节重算。registry entry 的 canonical 内容同样重算，
# 因此"把策略文件换掉再同步改注册表里的指纹"仍然过不了这一关（entry 变了，
# `registry_entry_sha256` 就变了，历史 A 的 authority 指纹也就不再复现）。

def registry_entry_record(policy_key: str | None = None) -> dict:
    """解析出一个 policy 在固定目录里的**记录级**证据（只读，无调用者路径）。

    A 条目**只**绑定 registry entry 与 distribution 记录，另外两槽恒为 `None`；B 条目
    绑定三份记录。判定只看**该条目是不是 B 条目**（`FROZEN_POLICY_KEY`），不看当前
    全局常量：因此新增 B 条目 / approval / frozen 记录**不会**改变历史 A 的 provider
    authority 指纹（§18.3.5）。
    """
    registry = _registry()
    key = current_policy_key() if policy_key is None else policy_key
    entry = registry["policies"].get(key)
    if entry is None:
        raise PolicyResolutionError(f"策略注册表未登记策略 {key!r}")
    data = _read_json(POLICY_DIR / entry["file"], f"策略文件 {entry['file']}")
    approval_sha = None
    frozen_sha = None
    if key == FROZEN_POLICY_KEY:
        approval_sha = sha256_canonical(
            _read_json(POLICY_DIR / APPROVAL_RECORD_FILENAME, "审批记录"))
        frozen_sha = sha256_canonical(
            _read_json(POLICY_DIR / FROZEN_RECORD_FILENAME, "冻结策略记录"))
    return {
        "policy_key": key,
        "registry_entry": dict(entry),
        "registry_entry_sha256": sha256_canonical(dict(entry)),
        "distribution_record_sha256": sha256_canonical(data),
        "approval_record_sha256": approval_sha,
        "frozen_record_sha256": frozen_sha,
    }


def policy_provider_authority_fingerprint(policy: SpanQualificationPolicy) -> str:
    """§18.3.5 的逐 policy 稳定公式（provider 每次重算，不采信记录自报值）。

    `sha256_canonical((provider_version, registry_entry_sha256, policy_id,
    distribution_record_sha256, approval_record_sha256_or_null,
    frozen_record_sha256_or_null))`。
    """
    record = registry_entry_record(policy.policy_key)
    if record["registry_entry"]["policy_fingerprint"] != policy.policy_fingerprint:
        raise PolicyResolutionError(
            f"策略 {policy.policy_key!r} 的指纹与注册表钉住值不一致："
            f"{policy.policy_fingerprint!r} != "
            f"{record['registry_entry']['policy_fingerprint']!r}")
    if policy.policy_key != record["policy_key"]:  # pragma: no cover - 防御性
        raise PolicyResolutionError("策略键与解析出的注册记录不一致")
    return sha256_canonical((
        V.QUALIFICATION_POLICY_PROVIDER_VERSION,
        record["registry_entry_sha256"],
        policy.policy_id,
        record["distribution_record_sha256"],
        record["approval_record_sha256"],
        record["frozen_record_sha256"],
    ))


def policy_registry_summary() -> dict:
    """注册表与当前阶段的只读摘要（供 run_manifest 与测试使用）。

    `default_policy_key` 是注册表**文件里声明**的键（A 条目，追加 B 条目不改动它）；
    `current_policy_key` 是**当前阶段**该用的键。二者在 B 阶段刻意不同：注册表默认值
    不随阶段漂移，阶段口径由 `versions.SPAN_CONFIDENCE_MIN` 单点决定。
    """
    table = ab_gate_truth_table()
    registry = _registry()
    return {
        "registry_version": registry["registry_version"],
        "default_policy_key": registry["default_policy_key"],
        "current_policy_key": current_policy_key(),
        "policy_keys": sorted(registry["policies"]),
        "registry_fingerprint": registry_fingerprint(),
        "pinned_fingerprints": {
            k: registry["policies"][k]["policy_fingerprint"]
            for k in sorted(registry["policies"])},
        "stage": table["stage"],
        "span_confidence_min": table["span_confidence_min"],
        "completion_enabled": table["completion_enabled"],
        "set_complete_supported": table["set_complete_supported"],
    }


# ---------------------------------------------------------------------------
# 5. 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    problems: list[str] = []
    table = ab_gate_truth_table()

    factor_sides = [(s, c) for (s, c, _f) in TS4_A_FACTOR_VALUES]
    expected = ([(("left", c)) for c in BOUNDARY_CAUSES_LEFT]
                + [(("right", c)) for c in BOUNDARY_CAUSES_RIGHT])
    if factor_sides != expected:
        problems.append(f"边界因子表未穷尽或顺序不符：{factor_sides} != {expected}")
    if len({s for (s, _c, _f) in TS4_A_FACTOR_VALUES}) != 2:
        problems.append("边界因子表必须同时覆盖 left 与 right")
    for (_s, _c, f) in TS4_A_FACTOR_VALUES:
        if not (0.0 <= f <= 1.0):
            problems.append(f"因子 {f} 越出 [0,1]")

    terms = tuple(TS4_A_SNIPPET_DEFAULTS["sentence_terminators"])
    if terms != tuple(sorted(terms, key=len, reverse=True)):
        problems.append("sentence_terminators 必须按长度降序（最长优先匹配）")
    if not set(TS4_A_SNIPPET_DEFAULTS["closing_quotes"]) <= {
            "”", "’", "\"", "'"}:
        problems.append("closing_quotes 含未登记字符")
    for name in ("max_snippets_per_node", "max_snippet_chars", "min_snippet_chars",
                 "max_total_snippet_chars"):
        if not isinstance(TS4_A_SNIPPET_DEFAULTS[name], int):
            problems.append(f"{name} 必须为 int")

    if table["stage"] == "distribution_only":
        if table["threshold_decided"] or table["completion_enabled"] \
                or table["set_complete_supported"]:
            problems.append("distribution_only 阶段的真值表不得开启完成 / set_complete")
        if V.SPAN_CONFIDENCE_MIN is not None:
            problems.append("distribution_only 阶段要求 SPAN_CONFIDENCE_MIN is None")

    try:
        policy = resolve_qualification_policy()
    except PolicyResolutionError as e:
        problems.append(f"{current_policy_key()} 阶段策略解析失败：{e}")
        policy = None
    if policy is not None:
        if policy.stage != table["stage"]:
            problems.append(
                f"当前策略阶段必须为 {table['stage']!r}，得到 {policy.stage!r}")
        if policy.policy_key != current_policy_key():
            problems.append("当前策略键与阶段不符")
        if table["stage"] == "distribution_only":
            if policy.completion_enabled or policy.set_complete_supported:
                problems.append("分布策略不得开启 completion / set_complete")
        elif policy.span_confidence_min != table["span_confidence_min"] \
                or not policy.completion_enabled \
                or not policy.set_complete_supported:
            problems.append(
                "threshold_enabled 阶段的策略必须携带与 SPAN_CONFIDENCE_MIN 相同的"
                "阈值并开启 completion / set_complete")

    return {
        # `default_policy_key` 是**注册表文件声明**的默认键（A 条目，追加 B 条目不改动
        # 它）；`current_policy_key` 才是**当前阶段**该用的键。二者在 B 阶段刻意不同，
        # 二者都不得被写成对方（否则自检会对外声称"默认键是 frozen 键"）。
        "default_policy_key": _registry()["default_policy_key"],
        "current_policy_key": current_policy_key(),
        "stage": table["stage"],
        "span_confidence_min": table["span_confidence_min"],
        "factor_entry_count": len(TS4_A_FACTOR_VALUES),
        "snippet_limits": {
            "max_snippets_per_node": TS4_A_SNIPPET_DEFAULTS["max_snippets_per_node"],
            "max_snippet_chars": TS4_A_SNIPPET_DEFAULTS["max_snippet_chars"],
            "min_snippet_chars": TS4_A_SNIPPET_DEFAULTS["min_snippet_chars"],
            "max_total_snippet_chars": TS4_A_SNIPPET_DEFAULTS["max_total_snippet_chars"],
        },
        "policy_fingerprint": (policy.policy_fingerprint if policy is not None else None),
        "problems": problems,
    }


def _main(argv: list[str]) -> int:
    if "--validate-only" not in argv:
        print("用法：python -m document_structure.span_policy --validate-only")
        return 2
    result = self_check()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result["problems"] else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))

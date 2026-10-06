# -*- coding: utf-8 -*-
"""TS5 §19.4.1 / §19.14：**evaluation-only** 版本轴、严格 schema 与 review check ID。

本模块只服务 TS5 验收链（runner / validate-only / seal-review / artifact tests），
**不进入生产 wire registry**，也不被任何生产模块 import。它是这六个版本轴与全部
evaluation 产物的**唯一定义处**：runner 内不得出现裸版本字符串，也不得各自复制一份
字段表。

## 六个版本轴（§19.4.1）

| 常量 | current | 绑定语义 |
|---|---|---|
| `TS5_ACCEPTANCE_RUNNER_VERSION` | `tar-1` | runner 的机器验收语义 |
| `TS5_TRUST_ROOT_SCHEMA_VERSION` | `ttr-2` | trust-roots 文件严格字段、固定排序、file/content hash |
| `TS5_RUN_MANIFEST_SCHEMA_VERSION` | `trm-2` | run identity、输入根、版本束、命令模式、create-only |
| `TS5_ARTIFACT_INDEX_SCHEMA_VERSION` | `tai-1` | machine artifact 完整成员集合与逐文件 hash |
| `TS5_REVIEW_SCHEMA_VERSION` | `trv-1` | 人工 review / check IDs / reviewer roles / decision |
| `TS5_REVIEW_ATTESTATION_SCHEMA_VERSION` | `tra-1` | seal 身份、machine/review hashes、verifier / DB 结论 |

## 口径

- **拒绝优于解释**：未知字段、缺失字段、重复项、错误类型、错误 enum、版本漂移一律
  抛出 `AcceptanceSchemaError`；本模块没有"尽力读取"路径，也没有默认值填充。
- **顺序是语义**：`document_order`、`REQUIRED_REVIEW_CHECK_IDS`、
  `MACHINE_ARTIFACT_MEMBERS` 都是**固定顺序**，重排即拒绝。
- **自报不作数**：`derive_review_decision` 只从两个角色的逐项 verdict 与机器门结论
  派生 decision；文件里自报的 `decision` 与派生值不一致即拒绝（§19.14.2）。
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# 1. evaluation-only 版本轴（§19.4.1）
# ---------------------------------------------------------------------------

TS5_ACCEPTANCE_RUNNER_VERSION = "tar-1"
TS5_TRUST_ROOT_SCHEMA_VERSION = "ttr-2"
TS5_RUN_MANIFEST_SCHEMA_VERSION = "trm-2"
TS5_ARTIFACT_INDEX_SCHEMA_VERSION = "tai-1"
TS5_REVIEW_SCHEMA_VERSION = "trv-1"
TS5_REVIEW_ATTESTATION_SCHEMA_VERSION = "tra-1"

#: 已退役、**只读**的旧轴（`trm-1` / `ttr-1`）。current reader 一律 fail-closed 拒绝
#: 它们；需要审计旧 run 时必须走下面的显式历史入口（`read_legacy_run_manifest` /
#: `read_legacy_trust_root`）。保留字面量而不是删掉，是为了让"读回历史产物"与"按
#: current 语义静默解释旧载荷"在代码层就是两件事（与生产侧 `fmc-1` 同一约定）。
LEGACY_TRUST_ROOT_SCHEMA_VERSION = "ttr-1"
LEGACY_RUN_MANIFEST_SCHEMA_VERSION = "trm-1"
LEGACY_TRUST_ROOT_RELPATH = "evals/fixtures/tree_structure/ts5_trust_roots.json"
LEGACY_TRUST_ROOT_KIND = "ts5_trust_root_v1"

#: 轴名 → current 值。runner、manifest、attestation 都引用这一张表；任何一处漂移都
#: 由 §4 的 `check_version_bundle` 变成 fail-closed。
EVALUATION_VERSION_AXES: dict[str, str] = {
    "acceptance_runner": TS5_ACCEPTANCE_RUNNER_VERSION,
    "trust_root_schema": TS5_TRUST_ROOT_SCHEMA_VERSION,
    "run_manifest_schema": TS5_RUN_MANIFEST_SCHEMA_VERSION,
    "artifact_index_schema": TS5_ARTIFACT_INDEX_SCHEMA_VERSION,
    "review_schema": TS5_REVIEW_SCHEMA_VERSION,
    "review_attestation_schema": TS5_REVIEW_ATTESTATION_SCHEMA_VERSION,
}

#: `trm-1` 时代的六轴（只有两条被本升版改动）。历史读回时用它校验旧载荷里的
#: `versions` 束——拿 current 轴去比会把历史产物误判成"版本漂移"。
LEGACY_EVALUATION_VERSION_AXES: dict[str, str] = {
    **EVALUATION_VERSION_AXES,
    "trust_root_schema": LEGACY_TRUST_ROOT_SCHEMA_VERSION,
    "run_manifest_schema": LEGACY_RUN_MANIFEST_SCHEMA_VERSION,
}

#: 版本字面量的唯一合法形状（与生产 `versions.VERSION_LITERAL_PATTERN` 同形，但本模块
#: 不 import 生产包：evaluation schema 不得把生产模块拖进验收语义）。
_VERSION_LITERAL = re.compile(r"^[a-z]{2,12}-[0-9]{1,3}$")
_SHA256_LITERAL = re.compile(r"^[0-9a-f]{64}$")

#: evaluation 产物的目录前缀（create-only）。前缀变了历史 run 的"不可覆盖"结论就不成立。
RUN_DIR_PREFIX = "tree_table_ts5_"

#: 独立 verifier 的两种诚实终态（§19.3.3 / §19.17.4-14、-15）：签发 capability，
#: 或因阻断性缺口**拒发**（`FinalMaterialBlockedError`）。拒发是正确结果，不是运行失败。
VERIFIER_VERDICTS: tuple[str, ...] = ("issued", "refused")

#: `trm-2` 新增的**封闭**拒发原因词表（`upstream_roots[*].refusal_reason_codes`）。
#:
#: 刻意**不**新增第三种 verdict：`refused` 仍是"没签发 capability"这一个终态，拒发的
#: **原因**由本词表承载。两种原因在语义上互斥于"谁的问题"：
#:
#: - `blocking_structure_gap`：上游冻结范围与已验证几何互相矛盾
#:   （`FinalMaterialBlockedError`）。此时 `refusal_blocking_kinds` 必须非空。
#: - `final_conservation_ineligible`：TS5 自己的四层守恒资格不成立
#:   （`FinalMaterialConservationError`）。它**不是**结构缺口，因此
#:   `refusal_blocking_kinds` 必须为空——两者混装会让"文档被上游阻断"与"TS5 算不平"
#:   互相冒充。
#:
#: 两者可以同时出现（既被结构缺口阻断、守恒也不成立）；那时以结构缺口为准，
#: `refusal_blocking_kinds` 非空。
REFUSAL_REASON_CODES: tuple[str, ...] = (
    "blocking_structure_gap",
    "final_conservation_ineligible",
)

#: §19.4.3 的结构缺口种类（封闭）。**刻意在本模块复制一份**：本模块不得 import 生产包
#: （否则生产重构可以静默改变验收语义）。两处一致性由 `evals/test_tree_table_artifacts.py`
#: 的 `self_check()` 对照生产 `table_schema` 断言；漂移即测试失败。
TABLE_GAP_KINDS: tuple[str, ...] = (
    "unsupported_table_structure", "unresolved_geometry",
    "upstream_table_scope_miss", "visual_object_not_table",
    "ambiguous_continuation", "provenance_incomplete",
    "root_identity_mismatch",
)

#: 其中必须阻断 final capability 的缺口（§19.5.3 / §19.17.4）。只有这两种能让
#: verifier 拒发；其余五种是诚实负面终态，不得阻断签发（否则每份真实文档都会被拒）。
BLOCKING_GAP_KINDS: tuple[str, ...] = (
    "upstream_table_scope_miss", "root_identity_mismatch",
)


class AcceptanceSchemaError(ValueError):
    """evaluation schema 的**唯一**拒绝类型：字段/类型/enum/版本/顺序任一不合法。"""


# ---------------------------------------------------------------------------
# 2. 严格读取原语
# ---------------------------------------------------------------------------

def _err(what: str, msg: str) -> "AcceptanceSchemaError":
    return AcceptanceSchemaError(f"{what}: {msg}")


def exact_fields(d: Any, fields: Sequence[str], what: str) -> dict:
    """精确字段集：**未知、缺失、重复**一律拒绝（顺序不参与判定）。"""
    if not isinstance(d, dict):
        raise _err(what, f"必须为 JSON 对象，得到 {type(d).__name__}")
    keys = list(d.keys())
    if len(set(keys)) != len(keys):
        raise _err(what, "存在重复键")
    unknown = sorted(set(keys) - set(fields))
    if unknown:
        raise _err(what, f"含未知字段 {unknown}（evaluation schema 不做尽力读取）")
    missing = sorted(set(fields) - set(keys))
    if missing:
        raise _err(what, f"缺字段 {missing}")
    return d


def need_str(d: dict, key: str, what: str, *, allow_empty: bool = False) -> str:
    v = d[key]
    if not isinstance(v, str) or (not allow_empty and v == ""):
        raise _err(what, f"{key} 必须为非空字符串，得到 {v!r}")
    return v


def need_str_or_none(d: dict, key: str, what: str) -> str | None:
    v = d[key]
    if v is None:
        return None
    if not isinstance(v, str) or v == "":
        raise _err(what, f"{key} 必须为非空字符串或 None，得到 {v!r}")
    return v


def need_int(d: dict, key: str, what: str, *, lo: int | None = None,
             hi: int | None = None) -> int:
    v = d[key]
    if not isinstance(v, int) or isinstance(v, bool):
        raise _err(what, f"{key} 必须为 int，得到 {v!r}")
    if lo is not None and v < lo:
        raise _err(what, f"{key} 不得小于 {lo}，得到 {v}")
    if hi is not None and v > hi:
        raise _err(what, f"{key} 不得大于 {hi}，得到 {v}")
    return v


def need_bool(d: dict, key: str, what: str) -> bool:
    v = d[key]
    if not isinstance(v, bool):
        raise _err(what, f"{key} 必须为 bool，得到 {v!r}")
    return v


def need_enum(d: dict, key: str, what: str, allowed: Sequence[str]) -> str:
    v = d[key]
    if not isinstance(v, str) or v not in allowed:
        raise _err(what, f"{key} 必须属于 {tuple(allowed)}，得到 {v!r}")
    return v


def need_version(d: dict, key: str, what: str, expected: str) -> str:
    """

    先按字面量形状判定，再与 **current 值**比较：这样"打错的字符串"与"被退役的旧
    版本"得到不同的报错，run 也能说清自己为什么不可比（§19.4.1 末段）。
    """
    v = d[key]
    if not isinstance(v, str) or not _VERSION_LITERAL.match(v):
        raise _err(what, f"{key} 不是合法版本字面量，得到 {v!r}")
    if v != expected:
        raise _err(what, f"{key} 版本漂移：必须为 {expected!r}，得到 {v!r}"
                         "（任何版本变化都必须使用新 run_id 与新版本）")
    return v


def need_sha256(d: dict, key: str, what: str) -> str:
    v = d[key]
    if not isinstance(v, str) or not _SHA256_LITERAL.match(v):
        raise _err(what, f"{key} 必须为 64 位小写十六进制 sha256，得到 {v!r}")
    return v


def need_str_list(d: dict, key: str, what: str, *, ordered: bool = False,
                  allow_empty: bool = False) -> list:
    """字符串数组字段的严格读取。

    `allow_empty` 只用于**语义上可以合法为空**的字段。`trm-2` 里有两处：
    `upstream_roots[*].refusal_blocking_kinds` 与 `refusal_reason_codes`——verifier
    正常签发 capability 时两者都必须为空（§19.3.3），只有拒发时才非空。若不允许为空，
    "签发"这一支在本模块里根本走不通，契约会出现自我矛盾。
    """
    v = d[key]
    if not isinstance(v, list) or (not v and not allow_empty):
        raise _err(what, f"{key} 必须为{'可空' if allow_empty else '非空'}字符串数组")
    for i, item in enumerate(v):
        if not isinstance(item, str) or item == "":
            raise _err(what, f"{key}[{i}] 必须为非空字符串，得到 {item!r}")
    if len(set(v)) != len(v):
        raise _err(what, f"{key} 不得重复")
    if ordered and v != sorted(v):
        raise _err(what, f"{key} 必须按升序固定排列")
    return list(v)


def need_exact_sequence(d: dict, key: str, what: str, expected: Sequence[str]) -> list:
    """**精确序列**：成员集合与顺序都必须逐项相等。"""
    got = need_str_list(d, key, what)
    if list(got) != list(expected):
        raise _err(what, f"{key} 必须恰为固定序列 {list(expected)}，得到 {got}")
    return list(got)


def check_version_bundle(versions: Any, what: str, *,
                         axes: dict | None = None) -> dict:
    """版本束必须与给定六轴**键集相同、取值相同**（默认 current 轴）。

    `axes` 只在**显式历史入口**里被换成历史轴：一份 `trm-1` 载荷的版本束记录的是
    当时的两条轴，用 current 轴去比会把"历史产物"误判成"版本漂移"。current 路径
    永远走默认值。
    """
    want = EVALUATION_VERSION_AXES if axes is None else axes
    exact_fields(versions, tuple(want), f"{what}.versions")
    for axis, value in want.items():
        need_version(versions, axis, f"{what}.versions", value)
    return dict(versions)


# ---------------------------------------------------------------------------
# 3. trust root（`ttr-1`）
# ---------------------------------------------------------------------------

#: current 信任锚：**版本化 successor**（`ttr-2`）。`ttr-1` 的那份
#: （`LEGACY_TRUST_ROOT_RELPATH`）**不得**被覆盖或改写，也不得再被 current reader 读取：
#: 它的 `expected_document_blocked` 是"结构缺口"轴上的值，与 `trm-2` 的 capability 轴
#: 不是同一件事，静默读回会把两个轴混成一个。
TRUST_ROOT_RELPATH = "evals/fixtures/tree_structure/ts5_trust_roots_v2.json"
TRUST_ROOT_KIND = "ts5_trust_root_v2"

TRUST_ROOT_FIELDS: tuple[str, ...] = (
    "fixture_kind",
    "trust_root_schema_version",
    "trust_root_content_note",
    "ts4_trust_root_relpath",
    "ts4_trust_root_file_sha256",
    "ts4_trust_root_content_fingerprint",
    "ts4_frozen_run_relpath",
    "watched_db_relpaths",
    "document_order",
    "documents",
    "expected_versions",
    "review_check_ids",
    "reviewer_roles",
)

#: 每份文档的封闭字段。三种 scope 共用同一张表：**不允许**某种 scope 少给字段。
DOCUMENT_FIELDS: tuple[str, ...] = (
    "document_key",
    "document_id",
    "company_id",
    "scope",
    "source_kind",
    "source_pdf_relpath",
    "source_pdf_sha256",
    "source_pdf_size",
    "frozen_document_dir_relpath",
    "fixture_root_relpath",
    "ts4_snapshot_id",
    "ts4_component_count",
    "ts4_disposition_count",
    "expected_table_count",
    "expected_final_span_count",
    "expected_blocking_gap_count",
    "expected_verifier_issues_capability",
    "expected_document_blocked",
)

#: 允许的文档 scope（`pinned_acceptance/historical_run` 与 `pinned_acceptance/versioned_fixture`）。
DOCUMENT_SCOPES: tuple[str, ...] = (
    "pinned_acceptance/historical_run",
    "pinned_acceptance/versioned_fixture",
)
#: 允许的输入种类。
DOCUMENT_SOURCE_KINDS: tuple[str, ...] = ("historical_run", "versioned_fixture")

REVIEWER_ROLES: tuple[str, ...] = ("user", "codex")
REVIEW_VERDICTS: tuple[str, ...] = ("pass", "fail", "not_reviewed")
REVIEW_DECISIONS: tuple[str, ...] = ("approve", "reject", "needs_changes")

#: §19.14.2 的 10 项人工检查 ID：**固定顺序、不得漏项、不得自由新增同义项**。
REQUIRED_REVIEW_CHECK_IDS: tuple[str, ...] = (
    "no_false_positive_table",
    "table_parts_and_boundaries_correct",
    "merged_multiline_cells_traceable",
    "surrounding_text_relations_correct",
    "continuation_identity_correct",
    "internal_cell_headings_retained",
    "nonfinancial_tables_use_generic_path",
    "financial_authority_separated",
    "explicit_gaps_are_honest",
    "final_conservation_zero",
)


def validate_document_entry(entry: Any, what: str) -> dict:
    exact_fields(entry, DOCUMENT_FIELDS, what)
    need_str(entry, "document_key", what)
    need_str(entry, "document_id", what)
    need_str(entry, "company_id", what)
    scope = need_enum(entry, "scope", what, DOCUMENT_SCOPES)
    kind = need_enum(entry, "source_kind", what, DOCUMENT_SOURCE_KINDS)
    # scope 与 source_kind 必须成对，不得交叉组合。
    if (scope == "pinned_acceptance/versioned_fixture") != (kind == "versioned_fixture"):
        raise _err(what, f"scope {scope!r} 与 source_kind {kind!r} 不成对")
    need_str(entry, "source_pdf_relpath", what)
    need_sha256(entry, "source_pdf_sha256", what)
    need_int(entry, "source_pdf_size", what, lo=1)
    need_str_or_none(entry, "frozen_document_dir_relpath", what)
    need_str_or_none(entry, "fixture_root_relpath", what)
    if kind == "historical_run":
        if entry["frozen_document_dir_relpath"] is None:
            raise _err(what, "historical_run 文档必须固定 frozen_document_dir_relpath")
        if entry["fixture_root_relpath"] is not None:
            raise _err(what, "historical_run 文档不得携带 fixture_root_relpath")
    else:
        if entry["fixture_root_relpath"] is None:
            raise _err(what, "versioned_fixture 文档必须固定 fixture_root_relpath")
        if entry["frozen_document_dir_relpath"] is not None:
            raise _err(what, "versioned_fixture 文档不得携带 frozen_document_dir_relpath")
    need_str(entry, "ts4_snapshot_id", what)
    for key in ("ts4_component_count", "ts4_disposition_count",
                "expected_table_count", "expected_final_span_count",
                "expected_blocking_gap_count"):
        need_int(entry, key, what, lo=0)
    need_bool(entry, "expected_verifier_issues_capability", what)
    need_bool(entry, "expected_document_blocked", what)
    return dict(entry)


def _validate_trust_root(d: Any, *, what: str, schema_version: str, kind: str,
                         axes: dict) -> dict:
    exact_fields(d, TRUST_ROOT_FIELDS, what)
    if d["fixture_kind"] != kind:
        raise _err(what, f"fixture_kind 必须为 {kind!r}，得到 "
                         f"{d['fixture_kind']!r}")
    need_version(d, "trust_root_schema_version", what, schema_version)
    need_str(d, "trust_root_content_note", what)
    need_str(d, "ts4_trust_root_relpath", what)
    need_sha256(d, "ts4_trust_root_file_sha256", what)
    need_sha256(d, "ts4_trust_root_content_fingerprint", what)
    need_str(d, "ts4_frozen_run_relpath", what)
    need_str_list(d, "watched_db_relpaths", what, ordered=True)
    order = need_str_list(d, "document_order", what)
    docs = d["documents"]
    if not isinstance(docs, dict) or not docs:
        raise _err(what, "documents 必须为非空对象")
    if list(docs.keys()) != sorted(docs.keys()):
        raise _err(what, "documents 的键必须按升序固定排列")
    if set(docs.keys()) != set(order):
        raise _err(what, f"document_order 与 documents 键集不一致：{order} vs "
                         f"{sorted(docs.keys())}")
    seen_ids: set = set()
    for key in order:
        entry = validate_document_entry(docs[key], f"{what}.documents[{key}]")
        if entry["document_key"] != key:
            raise _err(what, f"{key} 的 document_key 自相矛盾："
                             f"{entry['document_key']!r}")
        if entry["document_id"] in seen_ids:
            raise _err(what, f"document_id {entry['document_id']!r} 出现两次"
                             "（同一文档不得被两份记录重复验收）")
        seen_ids.add(entry["document_id"])
    check_version_bundle(d["expected_versions"], what, axes=axes)
    need_exact_sequence(d, "review_check_ids", what, REQUIRED_REVIEW_CHECK_IDS)
    if list(d["reviewer_roles"]) != list(REVIEWER_ROLES):
        raise _err(what, f"reviewer_roles 必须恰为 {list(REVIEWER_ROLES)}，得到 "
                         f"{d['reviewer_roles']}")
    return dict(d)


def validate_trust_root(d: Any) -> dict:
    """current（`ttr-2`）信任锚的严格校验。`ttr-1` 载荷不得走本函数。"""
    return _validate_trust_root(
        d, what="ts5_trust_roots", schema_version=TS5_TRUST_ROOT_SCHEMA_VERSION,
        kind=TRUST_ROOT_KIND, axes=EVALUATION_VERSION_AXES)


def read_legacy_trust_root(d: Any) -> dict:
    """`ttr-1` 信任锚的**显式历史入口**（只读、不可当作 current）。

    历史那份**不得**被覆盖或改写，也不得被 current reader 静默读取：它的
    `expected_document_blocked` 是"结构缺口"轴上的值，而 `trm-2` 的
    `document_blocked` 是 capability 轴，两者对同一份文档可以取相反的值。
    """
    out = _validate_trust_root(
        d, what="ts5_trust_roots(legacy ttr-1)",
        schema_version=LEGACY_TRUST_ROOT_SCHEMA_VERSION,
        kind=LEGACY_TRUST_ROOT_KIND, axes=LEGACY_EVALUATION_VERSION_AXES)
    return {
        "status": "historical_only",
        "schema_version": LEGACY_TRUST_ROOT_SCHEMA_VERSION,
        "trust_root": out,
        "note": ("`ttr-1` 的历史读回：`expected_document_blocked` 表达的是『结构缺口』"
                 "轴，`trm-2` 的 `document_blocked` 表达的是『capability 可否供下游"
                 "使用』轴；两轴不得互相冒充，因此其取值**不得**被当作 current 期望。"),
    }


# ---------------------------------------------------------------------------
# 4. machine artifact membership（`tai-1`）
# ---------------------------------------------------------------------------

#: 每份文档各自产出的成员（`<模板>` 由文档键替换）。
PER_DOCUMENT_MEMBERS: tuple[tuple[str, str], ...] = (
    ("final_material_structure_snapshots/{document_key}.json", "final_snapshot"),
)

#: run 级机器产物（§19.14.1）。**不含**待人工填写的 `manual_review.md` /
#: `review_decision.json`：machine index 不得把人工模板当机器产物。
RUN_LEVEL_MEMBERS: tuple[str, ...] = (
    "run_manifest.json",
    "machine_artifact_index.json",
    "machine_artifact_index_check.json",
    "database_immutability.json",
    "table_candidate_audit.jsonl",
    "table_objects.json",
    "table_objects.md",
    "table_preview.md",
    "table_cell_fragments.jsonl",
    "table_range_decisions.json",
    "final_component_bindings.jsonl",
    "table_relations.json",
    "table_terminal_ledger.json",
    "table_citable_coverage.json",
    "final_spans.json",
    "final_navigation_synopsis.json",
    "table_gaps.json",
    "visual_object_gaps.json",
    "final_conservation.json",
    "authority_separation.json",
    "final_snapshot_index.json",
    "table_acceptance_matrix.json",
    "table_acceptance_matrix.md",
    "before_after.md",
)

#: 人工模板：**必须存在**于 run 目录，但**不得**进入 machine index。
HUMAN_TEMPLATE_MEMBERS: tuple[str, ...] = ("manual_review.md", "review_decision.json")

#: 只由 seal 步骤追加、**绝不**进 machine index 的封存产物。
SEAL_ONLY_MEMBERS: tuple[str, ...] = ("review_attestation.json",)

#: **索引内部成员**：索引与索引核验自身。它们属于 `tai-1` 声明的成员集合（必须存在
#: 且被 `tai-1` 重算覆盖），但**不得**被索引登记哈希：一个文件不可能包含自己的
#: sha256。沿用 TS3 冻结索引的同一约定（`artifact_index.json` /
#: `artifact_index_check.json` 不含自身）。两者的**内容**由 `--validate-only` 与
#: `--seal-review` 重新解析 + `tra-1` attestation 绑定其 sha256 来约束。
INDEX_INTERNAL_MEMBERS: tuple[str, ...] = (
    "machine_artifact_index.json",
    "machine_artifact_index_check.json",
)

#: 磁盘上**刻意不参与索引扫描**的文件：索引内部成员 + 人工模板 + seal-only 产物。
NOT_SCANNED_BY_INDEX: tuple[str, ...] = tuple(
    INDEX_INTERNAL_MEMBERS + HUMAN_TEMPLATE_MEMBERS + SEAL_ONLY_MEMBERS)


def expected_machine_members(document_order: Sequence[str]) -> tuple[str, ...]:
    """按固定顺序展开本次 run 的 machine artifact 成员集合（`tai-1` 全集）。"""
    out = list(RUN_LEVEL_MEMBERS)
    for document_key in document_order:
        for template, _role in PER_DOCUMENT_MEMBERS:
            out.append(template.format(document_key=document_key))
    return tuple(sorted(out))


def indexable_members(document_order: Sequence[str]) -> tuple[str, ...]:
    """索引**实际逐文件登记**的成员集合 = 全集 − `INDEX_INTERNAL_MEMBERS`。"""
    return tuple(sorted(set(expected_machine_members(document_order))
                        - set(INDEX_INTERNAL_MEMBERS)))


def validate_artifact_index(d: Any, *, document_order: Sequence[str],
                            what: str = "machine_artifact_index") -> dict:
    fields = ("schema_type", "artifact_index_schema_version", "run_id",
              "run_dir_relpath", "members_expected", "index_internal_members",
              "files")
    exact_fields(d, fields, what)
    if d["schema_type"] != "TS5MachineArtifactIndex":
        raise _err(what, "schema_type 必须为 'TS5MachineArtifactIndex'")
    need_version(d, "artifact_index_schema_version", what,
                 TS5_ARTIFACT_INDEX_SCHEMA_VERSION)
    need_str(d, "run_id", what)
    need_str(d, "run_dir_relpath", what)
    if list(d["index_internal_members"]) != list(INDEX_INTERNAL_MEMBERS):
        raise _err(what, "index_internal_members 必须恰为 "
                         f"{list(INDEX_INTERNAL_MEMBERS)}")
    expected = indexable_members(document_order)
    if list(d["members_expected"]) != list(expected):
        raise _err(what, "members_expected 与 current `tai-1` 可索引成员集合不一致："
                         f"{d['members_expected']} vs {list(expected)}")
    files = d["files"]
    if not isinstance(files, list) or not files:
        raise _err(what, "files 必须为非空数组")
    row_fields = ("path", "sha256", "size", "role")
    seen: list = []
    for i, row in enumerate(files):
        exact_fields(row, row_fields, f"{what}.files[{i}]")
        need_str(row, "path", f"{what}.files[{i}]")
        need_sha256(row, "sha256", f"{what}.files[{i}]")
        need_int(row, "size", f"{what}.files[{i}]", lo=0)
        need_str(row, "role", f"{what}.files[{i}]")
        seen.append(row["path"])
    if seen != sorted(seen):
        raise _err(what, "files 必须按 path 升序固定排列")
    if len(set(seen)) != len(seen):
        raise _err(what, "files 不得重复")
    if set(seen) != set(expected):
        raise _err(what, f"files 成员集合与 members_expected 不一致：缺 "
                         f"{sorted(set(expected) - set(seen))} / 多 "
                         f"{sorted(set(seen) - set(expected))}")
    return dict(d)


def validate_artifact_index_check(d: Any, *, document_order: Sequence[str],
                                  what: str = "machine_artifact_index_check"
                                  ) -> dict:
    fields = ("schema_type", "artifact_index_schema_version", "run_id",
              "run_dir_relpath", "members_recomputed", "index_internal_members",
              "not_scanned_by_index", "scan_file_count",
              "index_file_count", "missing", "extra", "hash_mismatch", "ok")
    exact_fields(d, fields, what)
    if d["schema_type"] != "TS5MachineArtifactIndexCheck":
        raise _err(what, "schema_type 必须为 'TS5MachineArtifactIndexCheck'")
    need_version(d, "artifact_index_schema_version", what,
                 TS5_ARTIFACT_INDEX_SCHEMA_VERSION)
    need_str(d, "run_id", what)
    need_str(d, "run_dir_relpath", what)
    if list(d["index_internal_members"]) != list(INDEX_INTERNAL_MEMBERS):
        raise _err(what, "index_internal_members 必须恰为 "
                         f"{list(INDEX_INTERNAL_MEMBERS)}")
    if list(d["not_scanned_by_index"]) != sorted(NOT_SCANNED_BY_INDEX):
        raise _err(what, "not_scanned_by_index 必须恰为 "
                         f"{sorted(NOT_SCANNED_BY_INDEX)}")
    expected = indexable_members(document_order)
    if list(d["members_recomputed"]) != list(expected):
        raise _err(what, "members_recomputed 与 current `tai-1` 可索引成员集合不一致")
    for key in ("missing", "extra", "hash_mismatch"):
        v = d[key]
        if not isinstance(v, list):
            raise _err(what, f"{key} 必须为数组")
        for i, item in enumerate(v):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise _err(what, f"{key}[{i}] 必须为含 path 的对象")
    need_int(d, "scan_file_count", what, lo=0)
    need_int(d, "index_file_count", what, lo=0)
    ok = need_bool(d, "ok", what)
    if ok != (not d["missing"] and not d["extra"] and not d["hash_mismatch"]):
        raise _err(what, "ok 必须由 missing/extra/hash_mismatch 三者派生（不得自报）")
    if d["scan_file_count"] != d["index_file_count"]:
        raise _err(what, "scan_file_count 与 index_file_count 必须相等（成员集合"
                         "必须与磁盘实际文件一一对应；多一个文件即不等）")
    if d["index_file_count"] != len(expected):
        raise _err(what, f"index_file_count 必须等于可索引成员数 {len(expected)}，"
                         f"得到 {d['index_file_count']}")
    return dict(d)


# ---------------------------------------------------------------------------
# 5. run manifest（`trm-1`）
# ---------------------------------------------------------------------------

RUN_MANIFEST_FIELDS: tuple[str, ...] = (
    "schema_type",
    "run_manifest_schema_version",
    "acceptance_runner_version",
    "artifact_index_schema_version",
    "review_schema_version",
    "review_attestation_schema_version",
    "trust_root_schema_version",
    "versions",
    "run_id",
    "run_id_source",
    "run_dir_relpath",
    "generated_at_utc",
    "command_mode",
    "create_only",
    "trust_root",
    "upstream_roots",
    "documents",
    "machine_artifact_members",
    "human_template_members",
    "expected_blocking_summary",
    "manual_review_required",
)


def _validate_manifest(d: Any, *, document_order: Sequence[str], what: str,
                       schema_version: str, trust_root_schema_version: str,
                       axes: dict, with_reason_codes: bool) -> dict:
    exact_fields(d, RUN_MANIFEST_FIELDS, what)
    if d["schema_type"] != "TS5RunManifest":
        raise _err(what, "schema_type 必须为 'TS5RunManifest'")
    for key, expected in (
            ("run_manifest_schema_version", schema_version),
            ("acceptance_runner_version", TS5_ACCEPTANCE_RUNNER_VERSION),
            ("artifact_index_schema_version", TS5_ARTIFACT_INDEX_SCHEMA_VERSION),
            ("review_schema_version", TS5_REVIEW_SCHEMA_VERSION),
            ("review_attestation_schema_version",
             TS5_REVIEW_ATTESTATION_SCHEMA_VERSION),
            ("trust_root_schema_version", trust_root_schema_version)):
        need_version(d, key, what, expected)
    check_version_bundle(d["versions"], what, axes=axes)
    run_id = need_str(d, "run_id", what)
    if not re.match(r"^[A-Za-z0-9_.-]{4,120}$", run_id):
        raise _err(what, f"run_id 形状非法：{run_id!r}")
    need_enum(d, "run_id_source", what, ("explicit", "generated"))
    relpath = need_str(d, "run_dir_relpath", what)
    if not relpath.startswith(f"evaluation/results/{RUN_DIR_PREFIX}"):
        raise _err(what, f"run_dir_relpath 必须以 evaluation/results/{RUN_DIR_PREFIX}"
                         f" 开头，得到 {relpath!r}")
    if not relpath.rstrip("/").endswith(run_id):
        raise _err(what, "run_dir_relpath 必须以其 run_id 结尾")
    need_str(d, "generated_at_utc", what)
    need_enum(d, "command_mode", what, ("run",))
    if d["create_only"] is not True:
        raise _err(what, "create_only 必须为 True（run 目录已存在即拒绝）")
    tr = d["trust_root"]
    exact_fields(tr, ("relpath", "file_sha256", "content_fingerprint",
                      "ts4_trust_root_relpath", "ts4_trust_root_file_sha256",
                      "ts4_frozen_run_relpath"), f"{what}.trust_root")
    need_str(tr, "relpath", f"{what}.trust_root")
    need_sha256(tr, "file_sha256", f"{what}.trust_root")
    need_sha256(tr, "content_fingerprint", f"{what}.trust_root")
    need_str(tr, "ts4_trust_root_relpath", f"{what}.trust_root")
    need_sha256(tr, "ts4_trust_root_file_sha256", f"{what}.trust_root")
    need_str(tr, "ts4_frozen_run_relpath", f"{what}.trust_root")
    roots = d["upstream_roots"]
    if not isinstance(roots, list) or not roots:
        raise _err(what, "upstream_roots 必须为非空数组")
    root_fields = ("document_key", "document_id", "source_kind", "scope",
                   "upstream_dependency_fingerprint", "verified_span_snapshot_id",
                   "final_snapshot_locator", "final_snapshot_id",
                   "final_content_fingerprint", "verifier_verdict",
                   "verifier_verification_fingerprint",
                   *(("refusal_reason_codes",) if with_reason_codes else ()),
                   "refusal_blocking_kinds", "capability_issued")
    root_keys: list = []
    issued_count = 0
    for i, root in enumerate(roots):
        where = f"{what}.upstream_roots[{i}]"
        exact_fields(root, root_fields, where)
        need_str(root, "document_key", where)
        need_str(root, "document_id", where)
        need_enum(root, "source_kind", where, DOCUMENT_SOURCE_KINDS)
        need_enum(root, "scope", where, DOCUMENT_SCOPES)
        need_sha256(root, "upstream_dependency_fingerprint", where)
        need_str(root, "verified_span_snapshot_id", where)
        need_str(root, "final_snapshot_locator", where)
        need_str(root, "final_snapshot_id", where)
        need_sha256(root, "final_content_fingerprint", where)
        # 独立 verifier 的两种诚实终态：签发 capability，或**拒发**。拒发不是运行失败，
        # 必须可被如实记录，因此指纹改为"签发时必填"。`trm-2` 起拒发的**原因**由
        # `refusal_reason_codes` 承载（封闭词表），两种原因在"谁的问题"上互斥：
        # 结构缺口必须给出阻断性缺口种类，纯守恒拒发必须为空。
        verdict = need_enum(root, "verifier_verdict", where, VERIFIER_VERDICTS)
        fp = need_str_or_none(root, "verifier_verification_fingerprint", where)
        reasons = need_str_list(root, "refusal_reason_codes", where,
                                ordered=True, allow_empty=True) \
            if with_reason_codes else []
        for j, reason in enumerate(reasons):
            if reason not in REFUSAL_REASON_CODES:
                raise _err(where, f"refusal_reason_codes[{j}] 必须属于 "
                                  f"{REFUSAL_REASON_CODES}，得到 {reason!r}")
        kinds = need_str_list(root, "refusal_blocking_kinds", where,
                              ordered=True, allow_empty=True)
        for j, kind in enumerate(kinds):
            if kind not in TABLE_GAP_KINDS:
                raise _err(where, f"refusal_blocking_kinds[{j}] 必须属于 "
                                  f"TABLE_GAP_KINDS，得到 {kind!r}")
        need_bool(root, "capability_issued", where)
        if verdict == "issued":
            issued_count += 1
            if fp is None:
                raise _err(where, "verifier_verdict='issued' 时必须给出 "
                                  "verifier_verification_fingerprint")
            need_sha256(root, "verifier_verification_fingerprint", where)
            if reasons:
                raise _err(where, "verifier_verdict='issued' 时 refusal_reason_codes "
                                  "必须为空（签发就是没有拒发原因）")
            if kinds:
                raise _err(where, "verifier_verdict='issued' 时 "
                                  "refusal_blocking_kinds 必须为空")
            if root["capability_issued"] is not True:
                raise _err(where, "verifier_verdict='issued' 时 capability_issued "
                                  "必须为 True")
        else:
            if fp is not None:
                raise _err(where, "verifier_verdict='refused' 时不得给出 "
                                  "verifier_verification_fingerprint")
            if with_reason_codes:
                if not reasons:
                    raise _err(where, "verifier_verdict='refused' 时必须给出至少一个 "
                                      "refusal_reason_codes")
                # 两轴必须各归各位：结构缺口 ⇒ 必须有阻断性缺口种类；纯守恒拒发 ⇒
                # 必须为空。二者是**充要**关系，不允许"守恒拒发顺手带上几个 gap
                # kind"。
                if ("blocking_structure_gap" in reasons) != bool(kinds):
                    raise _err(where, "refusal_blocking_kinds 非空当且仅当 "
                                      "refusal_reason_codes 含 "
                                      f"'blocking_structure_gap'：reasons={reasons} "
                                      f"kinds={kinds}")
            elif not kinds:
                # `trm-1` 的历史口径：拒发一律要求非空的阻断缺口种类（旧契约无法表达
                # 纯守恒拒发，因此那种载荷根本不该以 `trm-1` 出现）。
                raise _err(where, "verifier_verdict='refused' 时必须给出至少一个 "
                                  "refusal_blocking_kinds（`trm-1` 历史口径）")
            for kind in kinds:
                if kind not in BLOCKING_GAP_KINDS:
                    raise _err(where, f"refusal_blocking_kinds 只允许阻断性缺口 "
                                      f"{BLOCKING_GAP_KINDS}，得到 {kind!r}")
            if root["capability_issued"] is not False:
                raise _err(where, "verifier_verdict='refused' 时 capability_issued "
                                  "必须为 False")
        root_keys.append(root["document_key"])
    if root_keys != list(document_order):
        raise _err(what, f"upstream_roots 必须按 document_order 固定排序："
                         f"{root_keys} vs {list(document_order)}")
    docs = d["documents"]
    if not isinstance(docs, list) or len(docs) != len(document_order):
        raise _err(what, "documents 必须与 document_order 等长")
    doc_keys: list = []
    doc_issued = 0
    doc_blocked = 0
    for i, doc in enumerate(docs):
        where = f"{what}.documents[{i}]"
        exact_fields(doc, ("document_key", "document_id", "scope", "source_kind",
                           "verifier_verdict", "capability_issued",
                           "table_count", "final_span_count", "decision_count",
                           "relation_count", "binding_count", "coverage_count",
                           "synopsis_count", "blocking_gap_count",
                           "document_blocked", "conservation_balanced"), where)
        need_str(doc, "document_key", where)
        need_str(doc, "document_id", where)
        need_enum(doc, "scope", where, DOCUMENT_SCOPES)
        need_enum(doc, "source_kind", where, DOCUMENT_SOURCE_KINDS)
        need_enum(doc, "verifier_verdict", where, VERIFIER_VERDICTS)
        need_bool(doc, "capability_issued", where)
        for key in ("table_count", "final_span_count", "decision_count",
                    "relation_count", "binding_count", "coverage_count",
                    "synopsis_count", "blocking_gap_count"):
            need_int(doc, key, where, lo=0)
        need_bool(doc, "document_blocked", where)
        need_bool(doc, "conservation_balanced", where)
        # `document_blocked` 在 `trm-2` 里是 **capability 轴**，不是结构缺口轴：它只
        # 回答"这份文档的 final capability 能不能供下游使用"，因此拒发 ⇒ True、签发 ⇒
        # False，两种原因（结构缺口 / 守恒不合格）一视同仁。**结构** gap 状态另有其轴：
        # 逐文档的 `blocking_gap_count` 与上游根上的 `refusal_reason_codes` /
        # `refusal_blocking_kinds`。两轴不得互相冒充——"守恒算不平"不是结构缺口，
        # 它不得把 `blocking_gap_count` 抬高，反之亦然。
        verdict = doc["verifier_verdict"]
        want_issued = verdict == "issued"
        if doc["capability_issued"] is not want_issued:
            raise _err(where, f"verifier_verdict={verdict!r} 与 capability_issued="
                              f"{doc['capability_issued']} 不一致")
        if doc["document_blocked"] is want_issued:
            raise _err(where, f"verifier_verdict={verdict!r} 与 document_blocked="
                              f"{doc['document_blocked']} 不一致")
        if doc["blocking_gap_count"] > 0 and verdict != "refused":
            raise _err(where, "blocking_gap_count > 0 时 verifier 必须拒发")
        if want_issued:
            doc_issued += 1
        else:
            doc_blocked += 1
        doc_keys.append(doc["document_key"])
    if doc_keys != list(document_order):
        raise _err(what, "documents 必须按 document_order 固定排序")
    if list(d["machine_artifact_members"]) != list(
            expected_machine_members(document_order)):
        raise _err(what, "machine_artifact_members 与 current `tai-1` 成员集合不一致")
    if list(d["human_template_members"]) != list(HUMAN_TEMPLATE_MEMBERS):
        raise _err(what, "human_template_members 必须恰为 "
                         f"{list(HUMAN_TEMPLATE_MEMBERS)}")
    summary = d["expected_blocking_summary"]
    exact_fields(summary, ("documents_blocked", "documents_capability_issued",
                           "total_blocking_gaps"),
                 f"{what}.expected_blocking_summary")
    for key in ("documents_blocked", "documents_capability_issued",
                "total_blocking_gaps"):
        need_int(summary, key, f"{what}.expected_blocking_summary", lo=0)
    # 摘要必须与逐文档记录**逐项一致**，且覆盖全部文档：每份文档要么签发、要么被
    # 拒发，两者之和恰为文档数——不允许"少收一份"被记成成功。
    if summary["documents_capability_issued"] != doc_issued:
        raise _err(what, "expected_blocking_summary.documents_capability_issued "
                         f"必须等于逐文档签发数 {doc_issued}")
    if summary["documents_blocked"] != doc_blocked:
        raise _err(what, "expected_blocking_summary.documents_blocked 必须等于"
                         f"逐文档拒发数 {doc_blocked}")
    if doc_issued + doc_blocked != len(document_order):
        raise _err(what, "每份文档必须恰为『签发』或『拒发』之一")
    if summary["documents_capability_issued"] + summary["documents_blocked"] \
            != len(document_order):
        raise _err(what, "expected_blocking_summary 必须覆盖全部文档")
    total_blocking = sum(doc["blocking_gap_count"] for doc in docs)
    if summary["total_blocking_gaps"] != total_blocking:
        raise _err(what, "expected_blocking_summary.total_blocking_gaps 必须等于"
                         f"逐文档阻断缺口总数 {total_blocking}")
    if d["manual_review_required"] is not True:
        raise _err(what, "manual_review_required 必须为 True（实施方不得宣布关闭）")
    return dict(d)


def validate_run_manifest(d: Any, *, document_order: Sequence[str],
                          what: str = "run_manifest") -> dict:
    """current（`trm-2`）run manifest 的严格校验。

    `trm-2` 相对 `trm-1` 的两处变化：

    1. 上游根新增封闭字段 `refusal_reason_codes`：`refused` 的**原因**必须显式给出，
       不再靠"`refusal_blocking_kinds` 非空"暗示。纯守恒拒发
       （`final_conservation_ineligible`）因此可以被如实记录，而不再会让契约自我矛盾。
    2. `documents[*].document_blocked` 明确为 **capability 轴**（拒发 ⇒ True），
       结构缺口轴由 `blocking_gap_count` 与上游根的 refusal 字段承载。

    `trm-1` 载荷**不得**走本函数（`need_version` 会以"版本漂移"拒绝）；审计历史 run
    走 `read_legacy_run_manifest`。
    """
    return _validate_manifest(
        d, document_order=document_order, what=what,
        schema_version=TS5_RUN_MANIFEST_SCHEMA_VERSION,
        trust_root_schema_version=TS5_TRUST_ROOT_SCHEMA_VERSION,
        axes=EVALUATION_VERSION_AXES, with_reason_codes=True)


def read_legacy_run_manifest(d: Any, *, document_order: Sequence[str],
                             what: str = "run_manifest") -> dict:
    """`trm-1` run manifest 的**显式历史入口**（只读、不可当作 current）。

    current reader（`validate_run_manifest`）对 `trm-1` 一律 fail-closed 拒绝：旧载荷
    无法区分"因结构缺口拒发"与"因守恒不合格拒发"，按 `trm-2` 语义读会把后者的
    `refusal_blocking_kinds=[]` 错读成"契约违规"或反过来把前者读成"守恒问题"。需要
    审计历史 run 时走本函数：它按**当时的**字段表与两条旧轴校验形状，把结果标成
    `historical_only`。它**不**返回 current 语义的结论，也**不**改写任何历史产物。
    """
    out = _validate_manifest(
        d, document_order=document_order, what=what,
        schema_version=LEGACY_RUN_MANIFEST_SCHEMA_VERSION,
        trust_root_schema_version=LEGACY_TRUST_ROOT_SCHEMA_VERSION,
        axes=LEGACY_EVALUATION_VERSION_AXES, with_reason_codes=False)
    return {
        "status": "historical_only",
        "schema_version": LEGACY_RUN_MANIFEST_SCHEMA_VERSION,
        "manifest": out,
        "note": ("`trm-1` 的历史读回：拒发只有『阻断性结构缺口』一种可表达能力，"
                 "『守恒资格不成立』既无法表达也无法与本项区分，因此其 "
                 "`verifier_verdict` / `document_blocked` **不得**被当作 current "
                 "结论；current 结论只能由 `trm-2` 重跑得出。"),
    }


# ---------------------------------------------------------------------------
# 6. review schema（`trv-1`）
# ---------------------------------------------------------------------------

REVIEW_DECISION_FIELDS: tuple[str, ...] = (
    "schema_type",
    "review_schema_version",
    "run_id",
    "final_snapshot_ids",
    "machine_gates_passed",
    "check_ids",
    "reviewer_roles",
    "reviews",
    "decision",
)


def _review_row_ok(row: Any) -> bool:
    return (isinstance(row, dict)
            and isinstance(row.get("verdict"), str)
            and row["verdict"] == "pass")


def derive_review_decision(d: dict) -> str:
    """从两个角色的逐项 verdict 与机器门结论**确定性派生** decision。

    `approve` 的必要且充分条件：机器门全过、两个角色对 10 项**全部** `pass`，
    且不存在任何 `fail` / `not_reviewed`。其余情形一律 `needs_changes`；显式
    `reject` 必须由至少一项 `fail` 支撑（否则自相矛盾，由调用方拒绝）。
    """
    if not d["machine_gates_passed"]:
        return "needs_changes"
    rows = d["reviews"]
    verdicts = [r["verdict"] for r in rows]
    if any(v == "fail" for v in verdicts):
        return "reject"
    if any(v != "pass" for v in verdicts):
        return "needs_changes"
    return "approve"


def validate_review_decision(d: Any, *, run_id: str,
                             final_snapshot_ids: Sequence[str],
                             what: str = "review_decision") -> dict:
    exact_fields(d, REVIEW_DECISION_FIELDS, what)
    if d["schema_type"] != "TS5ReviewDecision":
        raise _err(what, "schema_type 必须为 'TS5ReviewDecision'")
    need_version(d, "review_schema_version", what, TS5_REVIEW_SCHEMA_VERSION)
    if need_str(d, "run_id", what) != run_id:
        raise _err(what, f"run_id 与 run 不符：{d['run_id']!r} != {run_id!r}")
    if list(d["final_snapshot_ids"]) != list(final_snapshot_ids):
        raise _err(what, "final_snapshot_ids 必须与该 run 的 final snapshot 固定序列"
                         "逐项相等")
    need_bool(d, "machine_gates_passed", what)
    need_exact_sequence(d, "check_ids", what, REQUIRED_REVIEW_CHECK_IDS)
    if list(d["reviewer_roles"]) != list(REVIEWER_ROLES):
        raise _err(what, f"reviewer_roles 必须恰为 {list(REVIEWER_ROLES)}")
    reviews = d["reviews"]
    if not isinstance(reviews, list):
        raise _err(what, "reviews 必须为数组")
    row_fields = ("reviewer_role", "check_id", "verdict", "concise_observation",
                  "artifact_refs")
    seen: list = []
    for i, row in enumerate(reviews):
        exact_fields(row, row_fields, f"{what}.reviews[{i}]")
        role = need_enum(row, "reviewer_role", f"{what}.reviews[{i}]", REVIEWER_ROLES)
        cid = need_enum(row, "check_id", f"{what}.reviews[{i}]",
                        REQUIRED_REVIEW_CHECK_IDS)
        need_enum(row, "verdict", f"{what}.reviews[{i}]", REVIEW_VERDICTS)
        need_str(row, "concise_observation", f"{what}.reviews[{i}]",
                 allow_empty=True)
        refs = row["artifact_refs"]
        if not isinstance(refs, list):
            raise _err(what, f"reviews[{i}].artifact_refs 必须为数组")
        for j, ref in enumerate(refs):
            if not isinstance(ref, str) or ref == "":
                raise _err(what, f"reviews[{i}].artifact_refs[{j}] 必须为非空字符串")
        seen.append((role, cid))
    expected_pairs = [(role, cid) for role in REVIEWER_ROLES
                      for cid in REQUIRED_REVIEW_CHECK_IDS]
    if seen != expected_pairs:
        raise _err(what, "reviews 必须恰为 (reviewer_role × check_id) 的完全笛卡尔积"
                         f"，且按固定顺序：得到 {len(seen)} 行，期望 "
                         f"{len(expected_pairs)} 行")
    decision = need_enum(d, "decision", what, REVIEW_DECISIONS)
    derived = derive_review_decision(d)
    if decision != derived:
        raise _err(what, f"自报 decision {decision!r} 与派生值 {derived!r} 不一致"
                         "（decision 不得自报）")
    return dict(d)


def manual_review_projection(d: dict) -> str:
    """`manual_review.md` 只是 review JSON 的**人读投影**，不能反向授权。"""
    lines = [
        "# TS5 人工验收（人读投影）",
        "",
        "> 本文件由 `review_decision.json` **派生**，不构成授权。唯一授权文件是同目录的",
        "> `review_decision.json` 与封存后的 `review_attestation.json`。",
        "",
        f"- run_id: `{d['run_id']}`",
        f"- check IDs: {len(d['check_ids'])} 项（fixed order）",
        f"- 机器门: {'pass' if d['machine_gates_passed'] else 'fail'}",
        f"- 自报 decision: `{d['decision']}`",
        f"- 派生 decision: `{derive_review_decision(d)}`",
        "",
        "| check_id | " + " | ".join(REVIEWER_ROLES) + " |",
        "|---|" + "---|" * len(REVIEWER_ROLES),
    ]
    by_pair = {(r["reviewer_role"], r["check_id"]): r for r in d["reviews"]}
    for cid in d["check_ids"]:
        cells = []
        for role in REVIEWER_ROLES:
            row = by_pair[(role, cid)]
            cells.append(f"{row['verdict']} — {row['concise_observation']}")
        lines.append(f"| {cid} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def empty_review_decision(*, run_id: str, final_snapshot_ids: Sequence[str],
                          machine_gates_passed: bool = False) -> dict:
    """实施方只能生成**全 `not_reviewed`** 的空模板。

    `machine_gates_passed` 由调用方传入**重算**出来的机器门结论；它**不能**由执行者
    自报，而且即使为真，逐项 verdict 仍是 `not_reviewed`，`decision` 仍派生为
    `needs_changes`——机器门永远不能代替人工裁决。
    """
    reviews = [{"reviewer_role": role, "check_id": cid,
                "verdict": "not_reviewed", "concise_observation": "",
                "artifact_refs": []}
               for role in REVIEWER_ROLES
               for cid in REQUIRED_REVIEW_CHECK_IDS]
    d = {
        "schema_type": "TS5ReviewDecision",
        "review_schema_version": TS5_REVIEW_SCHEMA_VERSION,
        "run_id": run_id,
        "final_snapshot_ids": list(final_snapshot_ids),
        "machine_gates_passed": bool(machine_gates_passed),
        "check_ids": list(REQUIRED_REVIEW_CHECK_IDS),
        "reviewer_roles": list(REVIEWER_ROLES),
        "reviews": reviews,
        "decision": "needs_changes",
    }
    d["decision"] = derive_review_decision(d)
    return d


# ---------------------------------------------------------------------------
# 7. review attestation（`tra-1`）
# ---------------------------------------------------------------------------

ATTESTATION_FIELDS: tuple[str, ...] = (
    "schema_type",
    "review_attestation_schema_version",
    "run_id",
    "run_dir_relpath",
    "sealed_at_utc",
    "identity",
    "conclusions",
)


def review_attestation_identity(*, trust_root_file_sha256: str,
                                trust_root_content_fingerprint: str,
                                run_manifest_sha256: str,
                                artifact_index_sha256: str,
                                artifact_index_check_sha256: str,
                                review_decision_sha256: str,
                                runner_version: str,
                                issuer_version: str,
                                final_fingerprints: dict) -> dict:
    """`tra-1` 的身份载荷：**只**含已绑定字节与版本，不含 timestamp。"""
    return {
        "acceptance_runner_version": runner_version,
        "verifier_issuer_version": issuer_version,
        "trust_root_file_sha256": trust_root_file_sha256,
        "trust_root_content_fingerprint": trust_root_content_fingerprint,
        "run_manifest_sha256": run_manifest_sha256,
        "artifact_index_sha256": artifact_index_sha256,
        "artifact_index_check_sha256": artifact_index_check_sha256,
        "review_decision_sha256": review_decision_sha256,
        "final_snapshot_fingerprints": dict(sorted(final_fingerprints.items())),
    }


def validate_review_attestation(d: Any, *, run_id: str, run_dir_relpath: str,
                                what: str = "review_attestation") -> dict:
    exact_fields(d, ATTESTATION_FIELDS, what)
    if d["schema_type"] != "TS5ReviewAttestation":
        raise _err(what, "schema_type 必须为 'TS5ReviewAttestation'")
    need_version(d, "review_attestation_schema_version", what,
                 TS5_REVIEW_ATTESTATION_SCHEMA_VERSION)
    if need_str(d, "run_id", what) != run_id:
        raise _err(what, "run_id 与 run 不符")
    if need_str(d, "run_dir_relpath", what) != run_dir_relpath:
        raise _err(what, "run_dir_relpath 与 run 不符")
    need_str(d, "sealed_at_utc", what)
    ident = d["identity"]
    ident_fields = ("acceptance_runner_version", "verifier_issuer_version",
                    "trust_root_file_sha256", "trust_root_content_fingerprint",
                    "run_manifest_sha256", "artifact_index_sha256",
                    "artifact_index_check_sha256", "review_decision_sha256",
                    "final_snapshot_fingerprints")
    exact_fields(ident, ident_fields, f"{what}.identity")
    need_version(ident, "acceptance_runner_version", f"{what}.identity",
                 TS5_ACCEPTANCE_RUNNER_VERSION)
    need_str(ident, "verifier_issuer_version", f"{what}.identity")
    for key in ("trust_root_file_sha256", "trust_root_content_fingerprint",
                "run_manifest_sha256", "artifact_index_sha256",
                "artifact_index_check_sha256", "review_decision_sha256"):
        need_sha256(ident, key, f"{what}.identity")
    fps = ident["final_snapshot_fingerprints"]
    if not isinstance(fps, dict) or not fps:
        raise _err(what, "final_snapshot_fingerprints 必须为非空对象")
    if list(fps.keys()) != sorted(fps.keys()):
        raise _err(what, "final_snapshot_fingerprints 的键必须按升序固定排列")
    for key, value in fps.items():
        if not isinstance(key, str) or key == "":
            raise _err(what, "final_snapshot_fingerprints 的键必须为非空字符串")
        if not isinstance(value, str) or not _SHA256_LITERAL.match(value):
            raise _err(what, f"final_snapshot_fingerprints[{key!r}] 必须为 sha256")
    concl = d["conclusions"]
    concl_fields = ("trust_root_rechecked", "review_decision_schema_ok",
                    "artifact_index_recomputed_ok", "final_verifier_ok",
                    "database_immutability_ok", "machine_gates_passed")
    exact_fields(concl, concl_fields, f"{what}.conclusions")
    for key in concl_fields:
        need_bool(concl, key, f"{what}.conclusions")
    return dict(d)


# ---------------------------------------------------------------------------
# 8. self-check 与 CLI
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """只读自检：版本字面量、固定序列、笛卡尔积与派生规则。"""
    problems: list = []
    if len(EVALUATION_VERSION_AXES) != 6:
        problems.append(f"版本轴必须恰为 6 条，得到 {len(EVALUATION_VERSION_AXES)}")
    for axis, value in EVALUATION_VERSION_AXES.items():
        if not _VERSION_LITERAL.match(value):
            problems.append(f"版本轴 {axis} 的字面量非法：{value!r}")
    if len(REQUIRED_REVIEW_CHECK_IDS) != 10:
        problems.append("review check IDs 必须恰为 10 项")
    if len(set(REQUIRED_REVIEW_CHECK_IDS)) != 10:
        problems.append("review check IDs 不得重复")
    if list(REVIEWER_ROLES) != ["user", "codex"]:
        problems.append("reviewer_roles 必须恰为 ['user', 'codex']")
    if REVIEW_DECISIONS != ("approve", "reject", "needs_changes"):
        problems.append("decision 词表必须为 (approve, reject, needs_changes)")
    members = expected_machine_members(("A", "B", "C", "D"))
    indexable = indexable_members(("A", "B", "C", "D"))
    if len(members) != len(set(members)):
        problems.append("machine artifact 成员不得重复")
    if set(members) & set(HUMAN_TEMPLATE_MEMBERS):
        problems.append("人工模板不得进入 machine index")
    if set(members) & set(SEAL_ONLY_MEMBERS):
        problems.append("seal-only 产物不得进入 machine index")
    if set(RUN_LEVEL_MEMBERS) & set(SEAL_ONLY_MEMBERS):
        problems.append("seal-only 产物不得出现在 run 级机器产物表里")
    if set(members) - set(indexable) != set(INDEX_INTERNAL_MEMBERS):
        problems.append("可索引成员集合必须恰为全集去掉 INDEX_INTERNAL_MEMBERS")
    if not set(INDEX_INTERNAL_MEMBERS) <= set(members):
        problems.append("索引内部成员必须仍属于 `tai-1` 声明的成员集合")
    if set(NOT_SCANNED_BY_INDEX) & set(indexable):
        problems.append("不参与索引扫描的文件不得出现在可索引成员集合里")
    for name in RUN_LEVEL_MEMBERS:
        if not name.startswith("table_") and not name.endswith((".md", ".json", ".jsonl")):
            problems.append(f"机器产物名 {name!r} 形状可疑")
    # 派生规则：只有"机器门全过 + 两角色全 pass"才 approve。
    def _rows(verdict: str) -> list:
        return [{"reviewer_role": r, "check_id": c, "verdict": verdict,
                 "concise_observation": "", "artifact_refs": []}
                for r in REVIEWER_ROLES for c in REQUIRED_REVIEW_CHECK_IDS]
    base = {"machine_gates_passed": True, "reviews": _rows("pass")}
    if derive_review_decision(base) != "approve":
        problems.append("双角色全 pass 且机器门全过必须派生 approve")
    for verdict, expected in (("not_reviewed", "needs_changes"), ("fail", "reject")):
        probe = dict(base, reviews=_rows(verdict))
        if derive_review_decision(probe) != expected:
            problems.append(f"全 {verdict} 必须派生 {expected}")
    gate_fail = dict(base, machine_gates_passed=False)
    if derive_review_decision(gate_fail) != "needs_changes":
        problems.append("机器门未过时不得派生 approve")
    if empty_review_decision(run_id="r", final_snapshot_ids=["x"])["decision"] \
            != "needs_changes":
        problems.append("空模板必须派生 needs_changes（实施方不得代填 approve）")
    # 缺口词表与阻断集合的形状：阻断集合必须是全集的真子集，且拒发只允许阻断集合。
    if not set(BLOCKING_GAP_KINDS) < set(TABLE_GAP_KINDS):
        problems.append("BLOCKING_GAP_KINDS 必须是 TABLE_GAP_KINDS 的真子集")
    if len(set(TABLE_GAP_KINDS)) != len(TABLE_GAP_KINDS):
        problems.append("TABLE_GAP_KINDS 不得重复")
    if VERIFIER_VERDICTS != ("issued", "refused"):
        problems.append("verifier verdict 词表必须为 (issued, refused)")
    if REFUSAL_REASON_CODES != ("blocking_structure_gap",
                                "final_conservation_ineligible"):
        problems.append("拒发原因词表必须恰为 (blocking_structure_gap, "
                        "final_conservation_ineligible)")
    if LEGACY_RUN_MANIFEST_SCHEMA_VERSION == TS5_RUN_MANIFEST_SCHEMA_VERSION \
            or LEGACY_TRUST_ROOT_SCHEMA_VERSION == TS5_TRUST_ROOT_SCHEMA_VERSION:
        problems.append("legacy 轴必须与 current 轴不同（否则历史读回入口没有意义）")
    if LEGACY_TRUST_ROOT_RELPATH == TRUST_ROOT_RELPATH:
        problems.append("版本化 successor 必须是**另一个**文件：不得就地覆盖旧信任锚")
    return {
        "problems": problems,
        "version_axes": dict(EVALUATION_VERSION_AXES),
        "review_check_id_count": len(REQUIRED_REVIEW_CHECK_IDS),
        "machine_artifact_member_count": len(members),
        "indexable_member_count": len(indexable),
        "index_internal_members": list(INDEX_INTERNAL_MEMBERS),
        "not_scanned_by_index": sorted(NOT_SCANNED_BY_INDEX),
        "per_document_member_count": len(PER_DOCUMENT_MEMBERS),
        "human_template_members": list(HUMAN_TEMPLATE_MEMBERS),
        "seal_only_members": list(SEAL_ONLY_MEMBERS),
        # 供 `evals/test_tree_table_artifacts.py` 对照生产 `table_schema` 断言镜像一致。
        "table_gap_kinds": list(TABLE_GAP_KINDS),
        "blocking_gap_kinds": list(BLOCKING_GAP_KINDS),
        "verifier_verdicts": list(VERIFIER_VERDICTS),
        "refusal_reason_codes": list(REFUSAL_REASON_CODES),
        "trust_root_relpath": TRUST_ROOT_RELPATH,
        "legacy_trust_root_relpath": LEGACY_TRUST_ROOT_RELPATH,
        "legacy_version_axes": dict(LEGACY_EVALUATION_VERSION_AXES),
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TS5 evaluation schema 自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["problems"] else 1


if __name__ == "__main__":  # pragma: no cover - 诊断入口
    raise SystemExit(_main())

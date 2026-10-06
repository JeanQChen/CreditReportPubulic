# -*- coding: utf-8 -*-
"""TS5 `TableObject` 真实验收 runner（**评测专用**，只读；计划 §19.14）。

本脚本是 TS5 编码批的**真实验收 runner**。它把已封存的 TS4-B 链当作**只读上游信任
锚**，在仓库内**等价重建** 三份真实 Evidence-backed 文档 + 一个版本化非 300750 正向
fixture 的 TS5 final material 快照，落盘 §19.14.1 规定的机器产物，供用户 + Codex 做
**唯一后置人工门**裁决（`trv-1` / `tra-1`）。

硬约束（违反即本 run 无效）：

- **只读**。不写 `data/*.db`、不改冻结产物、不重跑对齐、不联网、不上 LLM；运行前后
  `data/evidence.db` 的 `size` / `mtime_ns` / `sha256` 必须**逐字段相等**。
- **唯一正式输入是版本化信任锚（current `ttr-2`）**。runner **没有** `--pdf` /
  `--ev-db` / `--layout` / `--candidate` 参数：任何输入都必须出现在信任锚里，任何
  漂移一律 fail-closed。`ttr-1` 那一份只保留作历史读回，current 链不再读它。
- **不重跑对齐**。上游 TS3 终态由 TS4 runner 的 pinned issuer 从冻结
  `normalization_alignment.json` 的 `rows` 还原；`align_block` / `align_evidence_set`
  不得在本脚本中被调用。
- **TS4 快照身份是 pin**。每份文档重建出的 `SpanBuildSnapshot.snapshot_id` 必须与信任
  锚固定值逐字相等；阶段漂移（`distribution_only` ↔ `threshold_enabled`）即 fail-closed。
- **构建完成不等于 TS5 通过**：产物一律标注 `manual_review_required`，
  `manual_review.md` 与 `review_decision.json` 只创建**空白模板**，不替用户 / Codex
  填写任何裁决；`--seal-review` 只按 `tra-1` 封存**已填写**的 verdict。
- **拒发是正确结果**：`FinalMaterialBlockedError` 不当作运行失败，而是如实记为
  `verifier_verdict="refused"` 与 `refusal_blocking_kinds`（§19.3.3 / §19.17.4）。

用法::

    python -m evaluation.run_tree_table_acceptance --validate-only <run目录>
    python -m evaluation.run_tree_table_acceptance --run-id tree_table_ts5_<UTC>
    python -m evaluation.run_tree_table_acceptance --seal-review <已有结果目录>

不传 `--run-id` 时按 UTC 时间戳生成；**绝不覆盖**任何历史结果目录。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import re
import shutil
import sys
from typing import Any, Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # 允许 `python evaluation/run_tree_table_acceptance.py`
    sys.path.insert(0, str(REPO_ROOT))

from document_structure import evidence_gateway as EG                # noqa: E402
from document_structure import final_material_builder as FMB         # noqa: E402
from document_structure import final_verifier as FV                  # noqa: E402
from document_structure import schema as S                           # noqa: E402
from document_structure import span_builder as SB                    # noqa: E402
from document_structure import span_verifier as SV                   # noqa: E402
from document_structure import table_schema as TS                    # noqa: E402
from document_structure import versions as V                         # noqa: E402
from document_structure.canonical import canonical_json              # noqa: E402
from document_structure.canonical import sha256_canonical            # noqa: E402
from evaluation import run_tree_span_acceptance as R                 # noqa: E402
from evaluation import tree_table_acceptance_schema as SCH           # noqa: E402

# ---------------------------------------------------------------------------
# 1. 常量：版本轴、路径、固定顺序、写入边界
# ---------------------------------------------------------------------------

DEFAULT_RESULTS_ROOT = "evaluation/results"

TRUST_ROOT_RELPATH = SCH.TRUST_ROOT_RELPATH
TRUST_ROOT_KIND = SCH.TRUST_ROOT_KIND
#: TS5 信任锚**自身**的 sha256。它是整条链的起点：信任锚内部的一切自洽（目录内
#: manifest/index/check 互相一致）都不构成信任。任何对该文件的合法修改都必须同时
#: 更新本字面量，否则 fail-closed。
#: （`ttr-1` 那一份 `ts5_trust_roots.json` 的字面量是
#: `1ef1cf15d890f81be10c6914e56f81a565db677b29ea9a59259bd36ced81ead7`，它现在只作为
#: **历史读回**的可比对锚存在，current 链不再读它。旧文件不得被覆盖或改写。）
TRUST_ROOT_FILE_SHA256 = (
    "05e4c8d2bf78acfe22e8ee63270ba929c144855a40d2b34fafbd2e43817cdaa8")

#: §19.13 的固定样本目录名。**只是输出目录名**，不进任何生产分支。
MATRIX_SNAPSHOT_DIRNAME = "final_material_structure_snapshots"

#: `trm-2` 的两种拒发原因（字面量在 runner 侧只出现一次；词表本身由
#: `tree_table_acceptance_schema.REFUSAL_REASON_CODES` 定义，一致性由 `self_check()`
#: 断言）。两条轴：结构缺口轴必须带阻断性缺口种类，守恒轴必须为空。
REFUSAL_BLOCKING_STRUCTURE_GAP = "blocking_structure_gap"
REFUSAL_FINAL_CONSERVATION_INELIGIBLE = "final_conservation_ineligible"

#: 阶段 token：TS5 只在上游 `threshold_enabled`（TS4-B）口径上成立。写死为 TS4-B 的
#: token 而不是读环境变量，是为了让"验收用了哪个阶段"成为 run identity 的一部分。
STAGE = "TS4-B"
STAGE_TOKEN = R.STAGE_TOKEN[STAGE]

#: 受监管库：与信任锚 `watched_db_relpaths` 必须逐项相等（在 `load_trust_root` 里断言）。
WATCHED_DB_RELPATHS: tuple[str, ...] = (R.EVIDENCE_DB_RELPATH,)

#: 不参与索引扫描的文件（索引/核验自身 + 人工模板 + seal-only）。与 evaluation schema
#: 的 `NOT_SCANNED_BY_INDEX` 必须逐项相等（在 `self_check()` 里断言）。
EXCLUDED_FROM_MACHINE_INDEX: tuple[str, ...] = SCH.NOT_SCANNED_BY_INDEX

#: 代码指纹参与文件（§19.14.1）。**逐文件显式列出**：不得用目录通配或"全部测试"代替。
CODE_FINGERPRINT_FILES: tuple[str, ...] = (
    "document_structure/__init__.py",
    "document_structure/versions.py",
    "document_structure/canonical.py",
    "document_structure/schema.py",
    "document_structure/evidence_gateway.py",
    "document_structure/span_schema.py",
    "document_structure/span_builder.py",
    "document_structure/span_verifier.py",
    "document_structure/synopsis.py",
    "document_structure/table_schema.py",
    "document_structure/table_geometry.py",
    "document_structure/table_classification.py",
    "document_structure/table_builder.py",
    "document_structure/final_material_builder.py",
    "document_structure/final_verifier.py",
    "document_structure/policies/registry_v1.json",
    "document_structure/policies/table_profile_registry_v1.json",
    "document_structure/policies/table_classification_profile_v1.json",
    "document_structure/policies/table_cell_block_profile_v1.json",
    "evaluation/tree_table_acceptance_schema.py",
    "evaluation/run_tree_table_acceptance.py",
    "evals/run_evals.py",
    "evals/fixtures/tree_structure/ts5_trust_roots.json",
    "evals/fixtures/tree_structure/ts5_trust_roots_v2.json",
    "evals/fixtures/tree_structure/ts4_trust_roots.json",
    "evals/fixtures/tree_structure/non_300750_ts5/manifest.json",
    # §19.12 的 TS5 测试矩阵逐文件登记。
    "evals/test_tree_table_schema.py",
    "evals/test_tree_table_geometry.py",
    "evals/test_tree_table_builder.py",
    "evals/test_tree_table_cells.py",
    "evals/test_tree_table_relations.py",
    "evals/test_tree_table_continuation.py",
    "evals/test_tree_table_conservation.py",
    "evals/test_tree_table_authority.py",
    "evals/test_tree_final_material.py",
    "evals/test_tree_table_formal_chain.py",
    "evals/test_tree_table_artifacts.py",
)

#: 每个机器产物的 **role 标签**（含该产物的 schema / algorithm 版本）。`tai-1` 的逐
#: 文件记录只有 `path/sha256/size/role` 四个字段，因此"这一项是什么、哪个版本"必须由
#: `role` 承载，而不是靠读者去猜文件名（§19.14.1）。
MEMBER_ROLES: dict[str, str] = {
    "run_manifest.json": f"run_manifest@{SCH.TS5_RUN_MANIFEST_SCHEMA_VERSION}/"
                         f"runner={SCH.TS5_ACCEPTANCE_RUNNER_VERSION}",
    "database_immutability.json": "database_immutability@sqlite-ro",
    "table_candidate_audit.jsonl": f"table_candidate_audit@{V.TABLE_BUILDER_VERSION}"
                                  f"/tcp-1/tgeo-1",
    "table_objects.json": f"table_objects@{V.TABLE_SCHEMA_VERSION}/{V.TABLE_BUILDER_VERSION}",
    "table_objects.md": "table_objects_projection@md",
    "table_preview.md": "table_preview@md/rendering-only",
    "table_cell_fragments.jsonl": f"table_cell_fragments@{V.TABLE_CELL_SCHEMA_VERSION}/"
                                  f"{V.TABLE_CELL_BLOCK_PROFILE_VERSION}",
    "table_range_decisions.json": f"table_range_decisions@{V.TABLE_RANGE_DECISION_SCHEMA_VERSION}",
    "final_component_bindings.jsonl": f"final_component_bindings@{V.FINAL_COMPONENT_BINDING_SCHEMA_VERSION}",
    "table_relations.json": f"table_relations@{V.TABLE_RELATION_SCHEMA_VERSION}/"
                            f"{V.TABLE_RELATION_BUILDER_VERSION}",
    "table_terminal_ledger.json": f"table_terminal_ledger@{V.TABLE_CELL_SCHEMA_VERSION}",
    "table_citable_coverage.json": f"table_citable_coverage@{V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION}",
    "final_spans.json": f"final_spans@{V.FINAL_SPAN_SCHEMA_VERSION}/"
                        f"{V.TS5_FINAL_SPAN_BUILDER_VERSION}",
    # 该成员装的是 **TS5** `FinalNavigationSynopsis`（`nss-3`/`ns-3`）；TS4 的
    # `nss-2`/`ns-2` 由各条目内的 `ts4_schema_version`/`ts4_synopsis_version` 另记，
    # role 不得拿 TS4 版本号冒充本成员版本（方案 C 的两条版本轴互不代填）。
    "final_navigation_synopsis.json": f"final_synopsis@{V.FINAL_SYNOPSIS_SCHEMA_VERSION}/"
                                      f"{V.FINAL_SYNOPSIS_VERSION}",
    "table_gaps.json": f"table_gaps@{V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION}",
    "visual_object_gaps.json": f"visual_object_gaps@{V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION}",
    "final_conservation.json": f"final_conservation@{V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION}",
    "authority_separation.json": "authority_separation@evaluation-only",
    "final_snapshot_index.json": f"final_snapshot_index@{V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION}",
    "table_acceptance_matrix.json": "table_acceptance_matrix@trv-1",
    "table_acceptance_matrix.md": "table_acceptance_matrix_projection@md",
    "before_after.md": "before_after@md/evaluation-only",
    "machine_artifact_index.json": f"machine_artifact_index@{SCH.TS5_ARTIFACT_INDEX_SCHEMA_VERSION}",
    "machine_artifact_index_check.json":
        f"machine_artifact_index_check@{SCH.TS5_ARTIFACT_INDEX_SCHEMA_VERSION}",
}

#: §19.13 的固定真实纵向样本表：**声明要求**（能力 / 样本 / 人工确认项 + 真实定位子与
#: 必备元素/关系/缺口）。`capability`/`sample`/`must_confirm` 三行文字与冻结的 §19.13
#: 表**逐字一致**；`locator` 与各项要求只使用冻结文本已经断言的内容——冻结文本没有断
#: 言的东西（对象个数、结构类、relation 种类）一律**不写**，不得由实施方补一个"看起来
#: 合理"的期望值。**只**出现在验收产物与人工 review 文案里，绝不进入生产分支。
#:
#: 判据不是这里写的任何值，而是 `derive_required_sample_verdicts` 从真实
#: `TableObject` / gap 台账 / 守恒层**重算**的结果；本表只声明"要求什么"。
REQUIRED_SAMPLE_DECLARATIONS: tuple[dict, ...] = (
    {"capability": "普通非财务表", "sample": "NDSD_KCZ_2026 p23 表4-1",
     "must_confirm": "表题、单位、多层 header、merged cell、body、total、表后备注",
     "document_key": "NDSD_KCZ_2026", "requirement_kind": "table_object",
     "locator": {"page_numbers": (23,)},
     "expected_object_count": 1,
     "required_elements": ("title", "unit", "header", "body", "total", "note")},
    {"capability": "主营业务与续表", "sample": "NDSD_KCZ_2026 p50–52",
     "must_confirm": "表5-10～5-13 四表不误合并；表5-11 跨页 continuation 正确",
     "document_key": "NDSD_KCZ_2026", "requirement_kind": "table_object",
     "locator": {"page_numbers": (50, 51, 52)},
     "expected_object_count": 4,
     "required_elements": ("body",),
     "required_relations": ("continued_by",)},
    {"capability": "主要子公司", "sample": "NDSD_KCZ_2026 p40–41",
     "must_confirm": "跨页、长文本/merged cell、前导说明 relation，无治理污染",
     "document_key": "NDSD_KCZ_2026", "requirement_kind": "table_object",
     "locator": {"page_numbers": (40, 41)},
     "required_elements": ("body",)},
    {"capability": "表内小标题/key-value form", "sample": "NDSD_KCZ_2026 p20",
     "must_confirm": "两个小标题及其后续全文、内部 blocks、精确位置、components 均完整",
     "document_key": "NDSD_KCZ_2026", "requirement_kind": "table_object",
     "locator": {"page_numbers": (20,), "structure_kind": "key_value_form"},
     "required_elements": ("internal_heading", "body")},
    {"capability": "视觉对象负例", "sample": "NDSD_KCZ_2026 p43 组织结构图",
     "must_confirm": "有“表”字但无可靠网格，不建 TableObject，显式 visual-object gap",
     "document_key": "NDSD_KCZ_2026", "requirement_kind": "typed_gap",
     "locator": {"page_numbers": (43,)},
     "required_gap_kinds": ("visual_object_not_table",),
     "forbid_table_object": True},
    {"capability": "任职情况", "sample": "NDSD_2024_year p50",
     "must_confirm": "两类任职表按普通非财务表处理，不误归财务权威",
     "document_key": "NDSD_2024_year", "requirement_kind": "table_object",
     "locator": {"page_numbers": (50,)},
     "expected_object_count": 2,
     "required_elements": ("body",)},
    {"capability": "募集资金", "sample": "NDSD_2025_year p88–91",
     "must_confirm": "募投项目表、续页、监管文字和表后说明分离并关联",
     "document_key": "NDSD_2025_year", "requirement_kind": "table_object",
     "locator": {"page_numbers": (88, 89, 90, 91)},
     "required_elements": ("body", "note"),
     "required_relations": ("continued_by",)},
    {"capability": "财务主表", "sample": "NDSD_2025_year p111 起",
     "must_confirm": "结构可回查；金额 authority 永远不由 TableObject 授予",
     "document_key": "NDSD_2025_year", "requirement_kind": "table_object",
     "locator": {"page_numbers": (111,)},
     "required_elements": ("body",)},
    {"capability": "财务附注", "sample": "NDSD_2025_year p164“货币资金”",
     "must_confirm": "文字说明与表格分读，typed relation 可回查，note-table 权威不越界",
     "document_key": "NDSD_2025_year", "requirement_kind": "table_object",
     "locator": {"page_numbers": (164,)},
     "required_elements": ("body", "note")},
    {"capability": "泛化正例", "sample": "non_300750_ts5 versioned fixture",
     "must_confirm": "同一 schema/builder/verifier/守恒，无公司和页码分支",
     "document_key": "FIXTURE_BOND_2026__non_300750_ts4",
     "requirement_kind": "document_invariants",
     "locator": {"scope": "document"},
     "required_flags": ("conservation_eligible", "no_blocking_gap",
                        "has_table_object", "no_page_or_company_branch")},
)
# 金额 authority 的机器判据由运行级 `authority_separation_ok` 门与人工问题 8 承担；
# 「财务主表」样本行只声明**结构**要求，不重复一份"权威已被隔离"的自报结论。

#: 声明只允许这三种要求形态（各有独立的派生器）。
_REQUIREMENT_KINDS: tuple[str, ...] = ("table_object", "typed_gap",
                                       "document_invariants")

#: §19.13 的 10 个人工问题（与 `trv-1` 的 10 个 check ID **一一对应、顺序相同**）。
REVIEW_QUESTIONS: tuple[str, ...] = (
    "是否存在普通正文/视觉对象伪表",
    "表题、单位、header/body/subtotal/total 是否错位、丢失或串到相邻表",
    "merged cells 与多行 cell 是否读得懂且能回查",
    "表前说明、表注、表后分析是否分开保存并通过 relation 组合",
    "continuation 是否只连接同一逻辑表",
    "表内小标题及后续段落是否完整",
    "任职、募集资金、子公司、主营等非财务表是否走通用机制",
    "财务主表是否仍无金额 authority",
    "每个 unsupported/unresolved gap 是否诚实可见",
    "final span/table/component 守恒是否为零差额",
)
if len(REVIEW_QUESTIONS) != len(SCH.REQUIRED_REVIEW_CHECK_IDS):  # pragma: no cover
    raise RuntimeError("REVIEW_QUESTIONS 必须与 REQUIRED_REVIEW_CHECK_IDS 一一对应")

#: 金额权威的禁止字段形状（`authority_separation.json` 的机械判据）。
_FINANCIAL_FIELD_PATTERNS: tuple[str, ...] = (
    "amount", "currency", "monetary", "balance", "fact_id", "is_financial",
    "authority_source", "financial_snapshot",
)
#: TS5 生产模块中**禁止**出现的财务权威类型与模块（AST 判据）。
_FORBIDDEN_AUTHORITY_SYMBOLS: tuple[str, ...] = ("FinancialSnapshot", "FinancialFactPack")
_FORBIDDEN_AUTHORITY_MODULES: tuple[str, ...] = ("financial_v2", "financial_worker",
                                                 "financial_schema")

#: 生产模块里**禁止出现**的内容字面量（负向清单：出现即违规）。它不是"按公司分叉"
#: 的规则，而是"任何公司都不许被写死"的机械判据——命中的字面量本身就是证据。
_FORBIDDEN_CONTENT_TOKENS: tuple[str, ...] = ("宁德", "时代新能源", "CATL")


# ---------------------------------------------------------------------------
# 2. 原语（复用 TS4 runner 的既有约定；不复制实现）
# ---------------------------------------------------------------------------

sha256_file = R.sha256_file
StepFailure = R.StepFailure


def _read_json(path: pathlib.Path, what: str) -> Any:
    return R._read_json(path, what)


def _write_json(path: pathlib.Path, payload: Any) -> None:
    R._write_json(path, payload)


def _write_json_lines(path: pathlib.Path, rows) -> None:
    R._write_json_lines(path, rows)


def _as_dict(obj: Any) -> Any:
    """typed 对象 → canonical dict（优先 `to_dict()`；不做静默降级）。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if not callable(to_dict):
        raise StepFailure(0, f"{type(obj).__name__} 既不是 dict 也没有 to_dict()"
                             f"（fail-closed）")
    return to_dict()


def _histogram(values) -> dict:
    out: dict = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _table_inventory(final) -> list:
    """每张 TableObject 的**真实**身份与结构事实（pin 漂移的人工裁决读回）。

    只读快照对象自身字段，不读任何自报的 `passed` / 计数；新增或消失的对象都能在
    这份清单里逐条对上，从而回答"对象新增/删除原因"。
    """
    rows = []
    for table in sorted(final.tables, key=lambda t: t.table_locator):
        rows.append({
            "table_id": table.table_id,
            "table_locator": table.table_locator,
            "page_number": table.page_number,
            "page_bbox": list(table.page_bbox),
            "source_order_index": table.source_order_index,
            "structure_kind": table.structure_kind,
            "structure_class": table.structure_class,
            "structure_state": table.structure_state,
            "structure_state_reason": table.structure_state_reason,
            "missing_or_uncertain_fields": list(table.missing_or_uncertain_fields),
            "row_count": len(table.rows),
            "column_count": table.column_count,
            "title_block_count": len(table.title_blocks),
            "unit_block_count": len(table.unit_blocks),
            "note_block_count": len(table.note_blocks),
            "source_ref_count": len(table.source_refs),
            "continuation_anchor_locator": table.continuation_anchor_locator,
            "continuation_candidate_locators":
                list(table.continuation_candidate_locators),
            "content_fingerprint": table.content_fingerprint,
        })
    return rows


def _conservation_layer_rows(final) -> list:
    """四层守恒的**真实**数值与问题（逐层独立重算，不读任何自报的 `balanced`）。

    `recomputed_eligible` 与 builder / schema / verifier 共用
    `conservation_layer_eligible` 这一条语义；此处只负责把原始数值与问题摆出来，
    让"算术相等但 `problems` 非空"这种必须判否的情形在产物里看得见。
    """
    rows = []
    for layer in final.conservation.layers:
        values = TS.conservation_layer_values(layer)
        total = TS.conservation_term_value(layer.total)
        rows.append({
            "layer_kind": layer.layer_kind,
            "values": dict(values),
            "total": total,
            "sum_of_values": sum(values.values()),
            "zero_required_terms": {name: int(values.get(name, 0))
                                    for name in TS.CONSERVATION_ZERO_REQUIRED_TERMS
                                    if name in values},
            "problems": list(layer.problems),
            "recorded_balanced": bool(layer.balanced),
            "recomputed_eligible": bool(TS.conservation_layer_eligible(
                layer.layer_kind, values, total, layer.problems)),
        })
    return rows


def _document_machine_gates(sample: dict) -> list:
    """一份文档的机器门：只从**真实产物**重算，不读取任何自报字段。

    三类门：

    - `trust_anchor_pin`：信任锚的 `expected_*` 是 pin 而非自证。漂移在
      `build_document` 里被如实记为失败门而**不**中止 run，因此这里逐字段重述一遍，
      让"哪一项漂了、声明的值是什么、观测到的值是什么"成为产物的一部分。
    - `conservation`：四层守恒资格（含 `problems` 为空与 `residual_gap` 为 0）。
    - `independent_verification`：**已签发能力与守恒资格必须互相一致**。它不要求
      文档必须 `issued`（拒发是正确结果），它只拒绝两种自相矛盾：资格成立却被拒发、
      资格不成立却签发了能力。
    """
    gates: list = []
    declared = sample["declared_expectations"]
    observed = sample["observed_expectations"]
    mismatched = {item["field"]: item for item in sample["pin_mismatches"]}
    for field in declared:
        item = mismatched.get(field)
        gates.append({
            "gate_id": f"pinned_expectation.{field}",
            "gate_class": "trust_anchor_pin",
            "status": "fail" if item is not None else "pass",
            "declared": declared[field],
            "observed": observed.get(field),
            "artifact_refs": ["run_manifest.json", "final_conservation.json"],
            "detail": item["reason"] if item is not None
                      else "观测值与信任锚 pin 逐项相等",
        })
    final = sample["final"]
    layers = _conservation_layer_rows(final)
    document_problems = list(final.conservation.problems)
    eligible = bool(TS.conservation_eligible(final.conservation))
    gates.append({
        "gate_id": "final_conservation_eligible",
        "gate_class": "conservation",
        "status": "pass" if eligible else "fail",
        "declared": True,
        "observed": eligible,
        "artifact_refs": ["final_conservation.json"],
        "detail": (f"document problems={len(document_problems)}；逐层重算见 layers；"
                   f"`balanced` 不再是可自报的布尔值"),
        "layers": layers,
    })
    for row in layers:
        gates.append({
            "gate_id": f"conservation_layer.{row['layer_kind']}",
            "gate_class": "conservation",
            "status": "pass" if row["recomputed_eligible"] else "fail",
            "declared": True,
            "observed": row["recomputed_eligible"],
            "artifact_refs": ["final_conservation.json"],
            "detail": (f"sum(values)={row['sum_of_values']} total={row['total']} "
                       f"problems={len(row['problems'])} "
                       f"recorded_balanced={row['recorded_balanced']}；"
                       f"`problems` 非空或 `residual_gap` 非零时不得 balanced"),
        })
    wrapper = sample["wrapper"]
    issued = wrapper is not None
    blocking = bool(final.gaps.blocking_entry_count)
    expected_issued = eligible and not blocking
    consistent = (issued == expected_issued)
    gates.append({
        "gate_id": "verifier_capability_consistent_with_conservation",
        "gate_class": "independent_verification",
        "status": "pass" if consistent else "fail",
        "declared": expected_issued,
        "observed": issued,
        "artifact_refs": ["run_manifest.json", "final_conservation.json",
                          "table_gaps.json"],
        "detail": (f"verdict={sample['verdict']} "
                   f"refusal_reason={sample['refusal_reason']} "
                   f"blocking_kinds={list(sample['refusal_blocking_kinds'])} "
                   f"conservation_eligible={eligible} "
                   f"blocking_gap_count={final.blocking_gap_count}"),
    })
    return gates


#: 「要求元素 → `TableObject.missing_or_uncertain_fields` 代码」的映射。声明要求某个
#: 元素而该对象上**不存在**它时，只有同时满足「对象把它显式登记为缺失/不确定」与
#: 「台账里存在一条绑定到该对象的 typed gap」两条，才允许记 `pass`（即"可复核的
#: typed gap"）；不在本映射里的元素**不允许**缺席。
_REQUIRED_ELEMENT_FIELD_CODE: dict[str, str] = {
    "title": "title",
    "unit": "unit",
    "header": "header",
    "subtotal": "subtotal",
    "total": "total",
    "continuation": "continuation",
    "column_semantics": "column_semantics",
    "row_boundary": "row_boundary",
    "column_boundary": "column_boundary",
    "cell_interior": "cell_interior",
}


def _table_element_probes() -> dict:
    """要求元素 → 只读**真实对象自身字段**的判据。

    这张表的键集**就是**元素词表（`REQUIRED_ELEMENT_VOCABULARY`），因此"声明里写了
    一个没人实现的要求元素"与"实现了一个没人能声明的元素"这两种漂移都不可能出现。
    """

    def _roles(table):
        return {row.role for row in table.rows}

    def _cells(table):
        return [cell for row in table.rows for cell in row.cells]

    return {
        "title": lambda t: bool(t.title_blocks),
        "unit": lambda t: t.unit_text is not None or bool(t.unit_blocks),
        "note": lambda t: bool(t.note_blocks),
        "header": lambda t: "header" in _roles(t),
        "body": lambda t: "body" in _roles(t),
        "subtotal": lambda t: "subtotal" in _roles(t),
        "total": lambda t: "total" in _roles(t),
        "merged_cell": lambda t: any(int(cell.rowspan) > 1 or int(cell.colspan) > 1
                                     for cell in _cells(t)),
        "internal_heading": lambda t: any(block.role == "heading"
                                          for cell in _cells(t)
                                          for block in cell.blocks),
        # 由 relation 门单独判定，见 `_derive_table_object_verdict`。
        "preceding_relation": lambda t: True,
        "structurally_complete": lambda t: t.structure_state == "complete",
    }


_TABLE_ELEMENT_PROBES: dict = _table_element_probes()

#: 声明里**允许**出现的要求元素（= `_TABLE_ELEMENT_PROBES` 的键集）。
REQUIRED_ELEMENT_VOCABULARY: tuple[str, ...] = tuple(_TABLE_ELEMENT_PROBES)

#: 表示"对象文本域**未被完整证成**"的结构状态原因（**全函数**登记）。
#:
#: 与 `document_structure.table_builder._structure_state` 产出的原因集合**逐项对应**：
#: 只有这三条说的是"有一行 / 一格 / 一处网格被截断或没有闭合"，`header_not_proven` 与
#: `accounting_sections_not_proven` 说的是"某类分节没有证据"，不是截断，因此不进本表。
CONSERVATION_TRUNCATION_REASONS: tuple[str, ...] = (
    "cell_provenance_incomplete",
    "row_boundary_truncated",
    "grid_closure_not_proven",
)

#: 截断 / 结构未闭合的状态原因 → 由它**不得**被判定为"已观测"的要求元素（§八(7)）。
#:
#: `partial` 对象可以留作诊断，但**被截断的关键行 / 单元格 / 注释不得作为必需样本通过
#: 对象**：三条原因都表示"该对象的文本域没有被完整证成"，因此由真实单元格承载的元素
#: 与对象上的**文本块类**元素（表题 / 单位 / 备注）都不成立——一个尾部被切掉、内部来源
#: 没有覆盖完整 LayoutSpan 的对象，不得因为"块列表非空"就冒充"这些内容已证明存在"。
#: 未登记的原因（例如合成测试里的 `synthetic_field_not_proven` / `header_not_proven`）
#: 不在本表内：它们不表示截断，因此不改变既有判据。
_TRUNCATION_UNPROVEN_ELEMENTS: dict = {
    # cell 的内部来源没有覆盖完整 LayoutSpan/fragment（对象自己记了 `cell_interior`）。
    "cell_provenance_incomplete": frozenset({
        "title", "unit", "note", "header", "body", "subtotal", "total",
        "merged_cell", "internal_heading", "structurally_complete"}),
    # 行边界被跨越的行切断：落在截断行上的正文行与会计分节不成立。
    "row_boundary_truncated": frozenset({
        "title", "unit", "note", "header", "body", "subtotal", "total",
        "structurally_complete"}),
    # 网格闭合性未证明：跨行跨列的"合并格"与"结构完整"都不成立。
    "grid_closure_not_proven": frozenset({
        "merged_cell", "structurally_complete"}),
}
#: 两张表必须**同集**：登记了却没写屏蔽规则（或反之）都会让某类截断悄悄漏过闸门。
assert set(_TRUNCATION_UNPROVEN_ELEMENTS) == set(CONSERVATION_TRUNCATION_REASONS), \
    "截断原因登记表与闸门表必须同集"
assert all(blocked <= set(_TABLE_ELEMENT_PROBES)
           for blocked in _TRUNCATION_UNPROVEN_ELEMENTS.values()), \
    "闸门只能屏蔽要求元素词表内的元素（词表外的东西没有判据可屏蔽）"


def _table_element_observed(table, element: str) -> bool:
    """要求元素在**真实对象**上是否成立（只读对象自身字段）。

    截断 / 未闭合的对象先过 `_TRUNCATION_UNPROVEN_ELEMENTS` 这道闸：处于该状态的元素
    一律**不成立**，再去看块列表/角色是否非空——否则一个被截断的对象会靠"残留的块"通过
    必需样本。
    """
    probe = _TABLE_ELEMENT_PROBES.get(element)
    if probe is None:
        raise StepFailure(0, f"未登记的要求元素 {element!r}（缺登记即 fail-closed，"
                             f"不得静默当作满足）", field="required_element")
    if element in _TRUNCATION_UNPROVEN_ELEMENTS.get(
            table.structure_state_reason or "", ()):
        return False
    return bool(probe(table))


def _gap_rows_for_table(final, table_id: str) -> list:
    return [{
        "gap_kind": entry.gap_kind,
        "detail_code": entry.detail_code,
        "page_number": entry.page_number,
        "table_id": entry.table_id,
        "component_id": entry.component_id,
        "disposition_id": entry.disposition_id,
        "blocks_document_capability": entry.blocks_document_capability,
    } for entry in final.gaps.entries if entry.table_id == table_id]


def _endpoint_identity(ref) -> dict:
    """端点的**可回查身份**：只取该端点类型真实拥有的字段，不臆造统一 id。"""
    if ref is None:
        return None
    identity = {"endpoint_type": type(ref).__name__}
    for name in ("table_id", "table_locator", "component_id", "component_locator",
                 "span_id", "span_locator", "node_id", "occurrence_id",
                 "occurrence_locator", "page_number"):
        if hasattr(ref, name):
            identity[name] = getattr(ref, name)
    return identity


def _relation_rows_for_table(final, table_id: str) -> list:
    rows = []
    for relation in final.relations:
        endpoints = [_endpoint_identity(relation.source_endpoint),
                     _endpoint_identity(relation.target_endpoint)]
        if not any(endpoint and endpoint.get("table_id") == table_id
                   for endpoint in endpoints):
            continue
        rows.append({
            "relation_kind": relation.relation_kind,
            "relation_locator": relation.relation_locator,
            "source_endpoint": endpoints[0],
            "target_endpoint": endpoints[1],
            "relation_proof_ids": list(relation.relation_proof_ids),
            "content_fingerprint": relation.content_fingerprint,
        })
    return rows


def _table_evidence(table, final) -> dict:
    """一条裁决必须绑定的**真实身份与边界**（§19.13：对象、指纹、边界、状态）。"""
    roles: dict = {}
    for row in table.rows:
        roles[row.role] = roles.get(row.role, 0) + 1
    return {
        "document_id": table.document_id,
        "document_version": table.document_version,
        "table_locator": table.table_locator,
        "table_id": table.table_id,
        "page_number": table.page_number,
        "page_bbox": list(table.page_bbox),
        "source_order_index": table.source_order_index,
        "content_fingerprint": table.content_fingerprint,
        "structure_fingerprint": table.structure_fingerprint,
        "provenance_fingerprint": table.provenance_fingerprint,
        "structure_kind": table.structure_kind,
        "structure_class": table.structure_class,
        "structure_state": table.structure_state,
        "structure_state_reason": table.structure_state_reason,
        "header_absence_reason": table.header_absence_reason,
        "missing_or_uncertain_fields": list(table.missing_or_uncertain_fields),
        "row_count": len(table.rows),
        "column_count": table.column_count,
        "row_role_histogram": dict(sorted(roles.items())),
        "title_block_count": len(table.title_blocks),
        "unit_block_count": len(table.unit_blocks),
        "note_block_count": len(table.note_blocks),
        "source_ref_count": len(table.source_refs),
        "source_ref_ranges": [list(ref.interval.span_char_range)
                              for ref in table.source_refs],
        "continuation_anchor_locator": table.continuation_anchor_locator,
        "continuation_candidate_locators":
            list(table.continuation_candidate_locators),
        "typed_gaps": _gap_rows_for_table(final, table.table_id),
        "relations": _relation_rows_for_table(final, table.table_id),
    }


def _element_deferral(table, element: str, evidence: dict):
    """要求元素缺席时，是否存在**可复核的显式 typed 声明**允许它缺席。

    返回 `None` 表示"不允许缺席"（该样本这一条判 fail）。允许缺席只有两种形态：

    - `header`：对象显式给出**登记过的** `header_absence_reason`（例如
      `key_value_form_without_header`），这是对象自己对"为什么没有表头"的 typed
      声明，可回查；
    - 其余元素：对象把它写进 `missing_or_uncertain_fields`，**且**台账里存在一条绑定
      到**该对象**的 typed gap。

    两者都要求"对象自己声明 + 台账有痕"，因此不存在"什么都不说就算通过"的路径。
    """
    code = _REQUIRED_ELEMENT_FIELD_CODE.get(element)
    if code is None or code not in table.missing_or_uncertain_fields:
        return None
    typed = [gap for gap in evidence["typed_gaps"]
             if gap["gap_kind"] in TS.TABLE_GAP_KINDS]
    if code == "header":
        reason = table.header_absence_reason
        if reason not in TS.HEADER_ABSENCE_REASONS:
            return None
        return {"field_code": code, "header_absence_reason": reason,
                "typed_gap_kinds": sorted({gap["gap_kind"] for gap in typed})}
    if not typed:
        return None
    return {"field_code": code,
            "typed_gap_kinds": sorted({gap["gap_kind"] for gap in typed})}


def _derive_table_object_verdict(decl: dict, sample: dict) -> dict:
    """从真实 `TableObject` 派生裁决：对象、元素、typed gap 三条各自独立判。"""
    final = sample["final"]
    locator = decl["locator"]
    pages = tuple(locator.get("page_numbers") or (locator["page_number"],))
    candidates = [table for table in final.tables
                  if table.page_number in pages
                  and (locator.get("structure_kind") is None
                       or table.structure_kind == locator["structure_kind"])
                  and (locator.get("structure_class") is None
                       or table.structure_class == locator["structure_class"])]
    declared_count = decl.get("expected_object_count")
    failures: list = []
    if declared_count is None:
        # 冻结文本没有断言对象**个数**：不得替它默认一个数，否则失败信息会把
        # "样本没被满足"说成"个数不对"。只要求该能力在声明定位子上确有一个对象。
        if not candidates:
            failures.append("required_object_absent:0")
    elif len(candidates) != int(declared_count):
        failures.append(f"required_object_count:{declared_count}->{len(candidates)}")
    if len({table.table_id for table in candidates}) != len(candidates):
        failures.append("locator_resolves_to_repeated_object_identity")
    if len({table.table_locator for table in candidates}) != len(candidates):
        failures.append("locator_resolves_to_repeated_object_locator")
    per_object: list = []
    for table in sorted(candidates, key=lambda t: (t.page_number,
                                                   t.source_order_index)):
        evidence = _table_evidence(table, final)
        element_results = {}
        for element in decl.get("required_elements", ()):
            if _table_element_observed(table, element):
                element_results[element] = {"observed": True, "deferred": None}
                continue
            deferred = _element_deferral(table, element, evidence)
            if deferred is None:
                element_results[element] = {"observed": False, "deferred": None}
                code = _REQUIRED_ELEMENT_FIELD_CODE.get(element)
                reason = ("required_element_absent_without_reviewable_gap"
                          if code is not None else
                          "required_element_absent_and_has_no_gap_code")
                failures.append(f"{reason}:{element}:{table.table_id}")
                continue
            element_results[element] = {"observed": False, "deferred": deferred}
        for element in decl.get("required_relations", ()):
            kinds = {row["relation_kind"] for row in evidence["relations"]}
            if element in kinds:
                element_results[f"relation:{element}"] = {"observed": True,
                                                          "deferred": None}
                continue
            gap_kinds = {gap["gap_kind"] for gap in evidence["typed_gaps"]
                         if gap["gap_kind"] in TS.TABLE_GAP_KINDS}
            deferred = None
            if "continuation" in table.missing_or_uncertain_fields \
                    and "ambiguous_continuation" in gap_kinds:
                deferred = {"field_code": "continuation",
                            "typed_gap_kinds": sorted(gap_kinds)}
            if deferred is None:
                element_results[f"relation:{element}"] = {"observed": False,
                                                          "deferred": None}
                failures.append(f"required_relation_absent:{element}:"
                                f"{table.table_id}")
                continue
            element_results[f"relation:{element}"] = {"observed": False,
                                                      "deferred": deferred}
        evidence["required_elements"] = element_results
        per_object.append(evidence)
    return {
        "status": "fail" if failures else "pass",
        "failure_reasons": failures,
        "observed_objects": per_object,
        "observed_object_count": len(candidates),
        # 定位子命中零个对象时，光看失败理由分不清"能力缺失"与"定位子对不上真实页码/
        # 结构"。这里附上**整份文档**的真实对象清单（不进判定），好让审阅者对着真实
        # 产物裁决——而不是由实施方把期望值改成"看起来对得上"。
        "document_table_inventory": (_table_inventory(final) if not candidates
                                     else None),
    }


def _derive_typed_gap_verdict(decl: dict, sample: dict) -> dict:
    """要求"这里**不该**有 TableObject，而应有一条确切的 typed gap"。"""
    final = sample["final"]
    locator = decl["locator"]
    pages = tuple(locator.get("page_numbers") or (locator["page_number"],))
    failures: list = []
    if decl.get("forbid_table_object", True):
        intruders = [table.table_id for table in final.tables
                     if table.page_number in pages]
        if intruders:
            failures.append(f"unexpected_table_object:{sorted(intruders)}")
    required = tuple(decl.get("required_gap_kinds", ()))
    for kind in required:
        if kind not in TS.TABLE_GAP_KINDS:
            raise StepFailure(0, f"未登记的 gap kind {kind!r}（fail-closed）",
                              field="required_gap_kinds")
        matched = [entry for entry in final.gaps.entries
                   if entry.gap_kind == kind
                   and entry.page_number in pages]
        if not matched:
            failures.append(f"required_typed_gap_absent:{kind}")
    observed = [{
        "gap_kind": entry.gap_kind,
        "detail_code": entry.detail_code,
        "page_number": entry.page_number,
        "table_id": entry.table_id,
        "component_id": entry.component_id,
        "blocks_document_capability": entry.blocks_document_capability,
    } for entry in final.gaps.entries if entry.page_number in pages]
    return {
        "status": "fail" if failures else "pass",
        "failure_reasons": failures,
        "observed_gaps": observed,
        "observed_gap_count": len(observed),
        # 要求缺口缺失时，给出**整份文档**的真实缺口种类分布（不进判定）：用来分辨
        # "这一页根本不是视觉对象"与"链路从不产出该类缺口"。
        "document_gap_kind_histogram": (
            _histogram(entry.gap_kind for entry in final.gaps.entries)
            if failures else None),
    }


def _document_flag_probes() -> dict:
    """文档级不变量 → 从**真实产物**重算的判据（键集即词表，不会与登记漂移）。"""
    return {
        "conservation_eligible":
            lambda s: bool(TS.conservation_eligible(s["final"].conservation)),
        "capability_issued": lambda s: s["wrapper"] is not None,
        "no_blocking_gap": lambda s: not bool(s["final"].gaps.blocking_entry_count),
        "no_page_or_company_branch":
            lambda s: not _page_or_company_literals_in_production(),
        "has_table_object": lambda s: s["final"].table_count > 0,
    }


_DOCUMENT_FLAG_PROBES: dict = _document_flag_probes()

#: 声明里**允许**出现的文档级不变量（= `_DOCUMENT_FLAG_PROBES` 的键集）。
REQUIRED_DOCUMENT_FLAGS: tuple[str, ...] = tuple(_DOCUMENT_FLAG_PROBES)


def _derive_document_invariants_verdict(decl: dict, sample: dict) -> dict:
    """文档级不变量：守恒资格、逐文档无公司/页码分支、构建器与复核域同源。"""
    final = sample["final"]
    failures: list = []
    observed: dict = {}
    for flag in decl.get("required_flags", ()):
        probe = _DOCUMENT_FLAG_PROBES.get(flag)
        if probe is None:
            raise StepFailure(0, f"未登记的文档级不变量 {flag!r}（fail-closed）",
                              field="required_flags")
        value = bool(probe(sample))
        observed[flag] = value
        if not value:
            failures.append(f"document_invariant_failed:{flag}")
    return {
        "status": "fail" if failures else "pass",
        "failure_reasons": failures,
        "observed_flags": observed,
        "observed_document_id": final.document_id,
        "observed_table_count": final.table_count,
        "observed_final_span_count": final.final_span_count,
        "observed_blocking_gap_count": final.blocking_gap_count,
    }


def derive_required_sample_verdicts(samples: list) -> list:
    """§19.13 固定真实样本的**派生**裁决。

    执行者**没有**任何输入 verdict 可以自报：每个样本只有"声明要求 + 定位子"，
    裁决一律从该文档的真实 `TableObject` / gap 台账 / 守恒层重算。派生器读不到
    `passed`，因此"照抄自报值"在这条路径上不可能发生。
    """
    by_key = {sample["document_key"]: sample for sample in samples}
    rows: list = []
    for decl in REQUIRED_SAMPLE_DECLARATIONS:
        document_key = decl["document_key"]
        sample = by_key.get(document_key)
        if sample is None:
            raise StepFailure(0, f"声明样本指向不存在的 document_key：{document_key!r}"
                                 f"（fail-closed）", field="required_sample_matrix")
        kind = decl["requirement_kind"]
        if kind == "table_object":
            derived = _derive_table_object_verdict(decl, sample)
        elif kind == "typed_gap":
            derived = _derive_typed_gap_verdict(decl, sample)
        elif kind == "document_invariants":
            derived = _derive_document_invariants_verdict(decl, sample)
        else:  # pragma: no cover - `_declaration_problems` 已在自检里拦掉
            raise StepFailure(0, f"未登记的 requirement_kind {kind!r}（fail-closed）",
                              field="required_sample_matrix")
        rows.append({
            "capability": decl["capability"],
            "sample": decl["sample"],
            "must_confirm": decl["must_confirm"],
            "document_key": document_key,
            "document_id": sample["final"].document_id,
            "requirement_kind": kind,
            "declared_requirement": {
                "locator": dict(decl["locator"]),
                "expected_object_count": decl.get("expected_object_count"),
                "required_elements": list(decl.get("required_elements", ())),
                "required_relations": list(decl.get("required_relations", ())),
                "required_gap_kinds": list(decl.get("required_gap_kinds", ())),
                "required_flags": list(decl.get("required_flags", ())),
                "forbid_table_object": decl.get("forbid_table_object"),
            },
            "status": derived["status"],
            "failure_reasons": list(derived["failure_reasons"]),
            "observed": {k: v for k, v in derived.items()
                         if k not in ("status", "failure_reasons")},
            "artifact_refs": ["table_objects.json", "table_objects.md",
                              "table_gaps.json", "table_relations.json",
                              "final_conservation.json"],
            "verdict_source": "derived_from_formal_artifacts",
            "self_reported_verdict_accepted": False,
        })
    return rows


def _declaration_problems(document_order: Sequence[str]) -> list:
    """`REQUIRED_SAMPLE_DECLARATIONS` 的**契约**自检。

    声明的错法都很安静：`document_key` 打错要到真跑 40 分钟后才炸，要求元素/关系/缺口
    写成未登记的值要到派生时才炸，`locator` 少写一页会让裁决落在错误的结构范围上。
    因此在跑之前先把这些逐条查掉——一个 typo 的成本不应该是一次真跑的时长。
    """
    problems: list = []
    seen: set = set()
    for decl in REQUIRED_SAMPLE_DECLARATIONS:
        label = f"{decl.get('capability')!r}@{decl.get('document_key')!r}"
        for field in ("capability", "sample", "must_confirm"):
            if not decl.get(field):
                problems.append(f"{label} 缺少 {field} 文案")
        key = (decl.get("capability"), decl.get("document_key"))
        if key in seen:
            problems.append(f"{label} 的 (capability, document_key) 重复")
        seen.add(key)
        if decl.get("document_key") not in document_order:
            problems.append(f"{label} 的 document_key 不在信任锚 document_order 里")
        kind = decl.get("requirement_kind")
        if kind not in _REQUIREMENT_KINDS:
            problems.append(f"{label} 的 requirement_kind {kind!r} 未登记")
        locator = decl.get("locator")
        if not isinstance(locator, dict) or not locator:
            problems.append(f"{label} 缺少 locator")
        elif "page_numbers" not in locator and "scope" not in locator:
            problems.append(f"{label} 的 locator 必须给出 page_numbers 或 scope")
        elif not locator.get("page_numbers") and "scope" not in locator:
            problems.append(f"{label} 的 locator.page_numbers 不得为空")
        for element in decl.get("required_elements", ()):
            if element not in REQUIRED_ELEMENT_VOCABULARY:
                problems.append(f"{label} 的要求元素 {element!r} 未登记")
        for relation in decl.get("required_relations", ()):
            if relation not in TS.TABLE_RELATION_KINDS:
                problems.append(f"{label} 的要求关系 {relation!r} 未登记")
        for gap_kind in decl.get("required_gap_kinds", ()):
            if gap_kind not in TS.TABLE_GAP_KINDS:
                problems.append(f"{label} 的要求缺口 {gap_kind!r} 未登记")
        for flag in decl.get("required_flags", ()):
            if flag not in REQUIRED_DOCUMENT_FLAGS:
                problems.append(f"{label} 的文档级不变量 {flag!r} 未登记")
        count = decl.get("expected_object_count")
        if count is not None and (not isinstance(count, int)
                                  or isinstance(count, bool) or count < 1):
            problems.append(f"{label} 的 expected_object_count 必须为正整数或省略")
        if kind == "table_object" and count is None \
                and not decl.get("required_elements"):
            problems.append(f"{label} 既未声明对象个数也未声明要求元素："
                            f"该声明的裁决是空的")
    if not REQUIRED_SAMPLE_DECLARATIONS:
        problems.append("固定样本声明不得为空")
    return problems


def _truncate(text: str, limit: int = 160) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


# ---------------------------------------------------------------------------
# 3. 信任锚（current `ttr-2`）与上游绑定
# ---------------------------------------------------------------------------

class Inputs:
    """一次验收所需的全部**已核验**输入（只在内存里；不含任何自报派生结果）。"""

    def __init__(self, *, trust_root: dict, ts4_trust_root: dict,
                 bindings: Any, fixture_pin: dict | None, notes: list) -> None:
        self.trust_root = trust_root
        self.ts4_trust_root = ts4_trust_root
        self.bindings = bindings
        self.fixture_pin = fixture_pin
        self.notes = notes

    @property
    def document_order(self) -> tuple:
        return tuple(self.trust_root["document_order"])

    def entry(self, document_key: str) -> dict:
        return self.trust_root["documents"][document_key]

    def handoff(self, document_key: str) -> Any:
        return self.bindings.handoffs[document_key]


def load_trust_root() -> dict:
    """§19.14：先对**信任锚文件本身**成立（字面量 sha256），再做 current 轴的严格解析。

    `ttr-1` 的旧文件走 `tree_table_acceptance_schema.read_legacy_trust_root`，**不**
    经过本入口：它的 `expected_document_blocked` 在另一条轴上。
    """
    path = REPO_ROOT / TRUST_ROOT_RELPATH
    if not path.is_file():
        raise StepFailure(1, f"仓库内 TS5 信任锚缺失：{path}（fail-closed）",
                          field="trust_root")
    actual = sha256_file(path)
    if actual != TRUST_ROOT_FILE_SHA256:
        raise StepFailure(
            1, f"TS5 信任锚自身 sha256 与脚本内字面量不符：{TRUST_ROOT_FILE_SHA256} "
               f"-> {actual}；根锚一旦变化，历史结论全部不可比（fail-closed）",
            field="trust_root_file_sha256")
    raw = _read_json(path, "TS5 信任锚")
    try:
        data = SCH.validate_trust_root(raw)
    except SCH.AcceptanceSchemaError as e:
        raise StepFailure(
            1, f"TS5 信任锚不符合 `{SCH.TS5_TRUST_ROOT_SCHEMA_VERSION}`：{e}"
               f"（fail-closed）", field="trust_root") from e
    # 注入字段必须**在**严格解析之后：信任锚是封闭字段表，多一个键即拒绝。
    data["trust_root_file_sha256"] = actual
    data["trust_root_content_fingerprint"] = sha256_canonical(raw)
    if tuple(data["watched_db_relpaths"]) != tuple(WATCHED_DB_RELPATHS):
        raise StepFailure(
            1, f"信任锚的 watched_db_relpaths {data['watched_db_relpaths']} 与本 runner "
               f"实际监管的 {list(WATCHED_DB_RELPATHS)} 不一致（不得虚报未打开的库）",
            field="watched_db_relpaths")
    return data


def _verify_ts4_trust_root(tr: dict) -> dict:
    """§19.14：TS5 的上游**根**就是已封存的 TS4 信任锚，必须逐字节按 pin 核验。"""
    relpath = tr["ts4_trust_root_relpath"]
    path = REPO_ROOT / relpath
    if not path.is_file():
        raise StepFailure(2, f"上游 TS4 信任锚缺失：{path}（fail-closed）",
                          field="ts4_trust_root_relpath")
    actual = sha256_file(path)
    if actual != tr["ts4_trust_root_file_sha256"]:
        raise StepFailure(
            2, f"上游 TS4 信任锚 sha256 与信任锚 pin 不符："
               f"{tr['ts4_trust_root_file_sha256']} -> {actual}（fail-closed）",
            field="ts4_trust_root_file_sha256")
    if actual != R.TRUST_ROOT_FILE_SHA256:
        raise StepFailure(
            2, f"上游 TS4 信任锚与本 runner 复用的 TS4 runner 内字面量不符："
               f"{R.TRUST_ROOT_FILE_SHA256} -> {actual}；两条链的根必须是同一份"
               f"（fail-closed）", field="ts4_trust_root_file_sha256")
    raw = _read_json(path, "TS4 信任锚")
    content_fingerprint = sha256_canonical(raw)
    if content_fingerprint != tr["ts4_trust_root_content_fingerprint"]:
        raise StepFailure(
            2, f"上游 TS4 信任锚的**内容指纹**与信任锚 pin 不符："
               f"{tr['ts4_trust_root_content_fingerprint']} -> {content_fingerprint}"
               f"（fail-closed）", field="ts4_trust_root_content_fingerprint")
    if raw.get("frozen_run_relpath") != tr["ts4_frozen_run_relpath"]:
        raise StepFailure(
            2, f"上游冻结 run 与本 runner 记录的不一致："
               f"{tr['ts4_frozen_run_relpath']!r} -> {raw.get('frozen_run_relpath')!r}"
               f"（fail-closed）", field="ts4_frozen_run_relpath")
    return {"relpath": relpath, "file_sha256": actual,
            "content_fingerprint": content_fingerprint,
            "frozen_run_relpath": raw.get("frozen_run_relpath")}


def _verify_fixture_pin(entry: dict) -> dict:
    """§19.14：版本化非 300750 正向 fixture 的 **TS5 侧 pin**。

    TS5 不为验收另建第二套 fixture 运行时：真正被消费的成员仍只存在于
    `non_300750_ts4/`，由 `evidence_gateway.FIXTURE_ROOT_RELPATH` 指向并经
    `load_fixture_root()` 逐成员核验后签发。本函数只证明 TS5 侧记录的
    **就是那一份**——pin 声明的运行时根、manifest 路径与 sha256 必须逐项成立。
    """
    step = 3
    relpath = entry["fixture_root_relpath"]
    pin_path = REPO_ROOT / relpath / "manifest.json"
    if not pin_path.is_file():
        raise StepFailure(step, f"TS5 fixture pin 缺失：{pin_path}（fail-closed）",
                          document_id=entry["document_id"], field="fixture_root_relpath")
    pin = _read_json(pin_path, "TS5 fixture pin")
    if pin.get("trust_root_schema_version") != SCH.TS5_TRUST_ROOT_SCHEMA_VERSION:
        raise StepFailure(
            step, f"fixture pin 的 trust_root_schema_version 必须为 "
                  f"{SCH.TS5_TRUST_ROOT_SCHEMA_VERSION!r}，得到 "
                  f"{pin.get('trust_root_schema_version')!r}（fail-closed）",
            document_id=entry["document_id"], field="trust_root_schema_version")
    runtime_root = EG.FIXTURE_ROOT_RELPATH
    if pin.get("ts4_fixture_root_relpath") != runtime_root:
        raise StepFailure(
            step, f"fixture pin 指向的运行时 fixture 根 {pin.get('ts4_fixture_root_relpath')!r} "
                  f"与本仓库 `FIXTURE_ROOT_RELPATH` {runtime_root!r} 不一致"
                  f"（fail-closed）", document_id=entry["document_id"],
            field="ts4_fixture_root_relpath")
    runtime_manifest = EG.fixture_root_dir() / EG.FIXTURE_MANIFEST_FILENAME
    if not runtime_manifest.is_file():
        raise StepFailure(step, f"运行时 fixture manifest 缺失：{runtime_manifest}"
                                f"（fail-closed）", document_id=entry["document_id"],
                          field="fixture_manifest")
    runtime_sha = sha256_file(runtime_manifest)
    if pin.get("ts4_fixture_manifest_sha256") != runtime_sha:
        raise StepFailure(
            step, f"运行时 fixture manifest 的 sha256 与 pin 不符："
                  f"{pin.get('ts4_fixture_manifest_sha256')} -> {runtime_sha}"
                  f"（fail-closed）", document_id=entry["document_id"],
            field="ts4_fixture_manifest_sha256")
    if pin.get("document_id") != entry["document_id"] \
            or str(pin.get("company_id")) != str(entry["company_id"]):
        raise StepFailure(step, "fixture pin 的 document_id / company_id 与信任锚不一致"
                                "（fail-closed）", document_id=entry["document_id"],
                          field="document_id")
    upstream = (pin.get("expected_upstream") or {})
    if upstream.get("ts4_snapshot_id") != entry["ts4_snapshot_id"]:
        raise StepFailure(
            step, f"fixture pin 的 ts4_snapshot_id 与信任锚不一致："
                  f"{entry['ts4_snapshot_id']!r} -> {upstream.get('ts4_snapshot_id')!r}"
                  f"（fail-closed）", document_id=entry["document_id"],
            field="ts4_snapshot_id")
    expected_ts5 = (pin.get("expected_ts5") or {})
    pairs = (("table_count", entry["expected_table_count"]),
             ("final_span_count", entry["expected_final_span_count"]),
             ("blocking_gap_count", entry["expected_blocking_gap_count"]),
             ("verifier_issues_capability", entry["expected_verifier_issues_capability"]),
             ("document_blocked", entry["expected_document_blocked"]))
    for key, value in pairs:
        if expected_ts5.get(key) != value:
            raise StepFailure(
                step, f"fixture pin 的 expected_ts5.{key} 与信任锚不一致："
                      f"{value!r} -> {expected_ts5.get(key)!r}（fail-closed）",
                document_id=entry["document_id"], field=f"expected_ts5.{key}")
    return {"relpath": relpath, "manifest_sha256": sha256_file(pin_path),
            "runtime_fixture_root_relpath": runtime_root,
            "runtime_manifest_sha256": runtime_sha,
            "member_identity_sha256": pin.get("member_identity_sha256"),
            "manifest": pin}


def bind_inputs() -> Inputs:
    """§19.14：1) TS5 根锚 → 2) 上游 TS4 根锚 → 3) 上游 TS3/TS4 交接 → 4) 逐文档 pin。

    任一步失败即 `StepFailure`，且**不产生任何结果目录**。
    """
    trust_root = load_trust_root()
    ts4_trust_root = _verify_ts4_trust_root(trust_root)
    try:
        bindings = R.bind_all()
    except R.StepFailure as e:
        raise StepFailure(3, f"上游 TS4/TS3 绑定失败：{e.report()}（fail-closed）",
                          field="upstream_binding") from e
    except Exception as e:  # noqa: BLE001 - 任一步失败都必须点名
        raise StepFailure(3, f"上游 TS4/TS3 绑定失败：{type(e).__name__}: {e}"
                             f"（fail-closed）", field="upstream_binding") from e

    fixture_pin: dict | None = None
    for key in trust_root["document_order"]:
        entry = trust_root["documents"][key]
        document_id = entry["document_id"]
        handoff = bindings.handoffs.get(key)
        if handoff is None:
            raise StepFailure(4, f"上游未签发 {key} 的 TS3 交接（fail-closed）",
                              document_id=document_id, field="handoff")
        layout = handoff.page_layout
        if layout.document_id != document_id:
            raise StepFailure(
                4, f"交接的 document_id 与信任锚不符：{document_id!r} -> "
                   f"{layout.document_id!r}（fail-closed）", document_id=document_id,
                field="document_id")
        if str(layout.company_id) != str(entry["company_id"]):
            raise StepFailure(
                4, f"交接的 company_id 与信任锚不符：{entry['company_id']!r} -> "
                   f"{layout.company_id!r}（fail-closed）", document_id=document_id,
                field="company_id")
        if handoff.issuer_scope != "pinned_acceptance":
            raise StepFailure(
                4, f"上游交接的签发域必须为 pinned_acceptance，得到 "
                   f"{handoff.issuer_scope!r}（fail-closed）", document_id=document_id,
                field="issuer_scope")
        if entry["source_kind"] == "versioned_fixture":
            if handoff.source_kind != EG.FIXTURE_SOURCE_KIND:
                raise StepFailure(
                    4, f"fixture 文档的 source_kind 必须为 {EG.FIXTURE_SOURCE_KIND!r}，"
                       f"得到 {handoff.source_kind!r}（fail-closed）",
                    document_id=document_id, field="source_kind")
            fixture_pin = _verify_fixture_pin(entry)
        else:
            if handoff.source_kind == EG.FIXTURE_SOURCE_KIND:
                raise StepFailure(
                    4, f"历史 run 文档不得来自 fixture 根（得到 "
                       f"{handoff.source_kind!r}）（fail-closed）",
                    document_id=document_id, field="source_kind")
            frozen_dir = REPO_ROOT / str(entry["frozen_document_dir_relpath"])
            if not frozen_dir.is_dir():
                raise StepFailure(4, f"冻结文档目录缺失：{frozen_dir}（fail-closed）",
                                  document_id=document_id,
                                  field="frozen_document_dir_relpath")
        pdf_path = REPO_ROOT / entry["source_pdf_relpath"]
        if not pdf_path.is_file():
            raise StepFailure(4, f"受信原 PDF 缺失：{pdf_path}（fail-closed）",
                              document_id=document_id, field="source_pdf_relpath")
        pdf_sha = sha256_file(pdf_path)
        if pdf_sha != entry["source_pdf_sha256"]:
            raise StepFailure(
                4, f"原 PDF 的 sha256 与信任锚不一致：{entry['source_pdf_sha256']} -> "
                   f"{pdf_sha}（fail-closed）", document_id=document_id,
                field="source_pdf_sha256")
        pdf_size = pdf_path.stat().st_size
        if pdf_size != entry["source_pdf_size"]:
            raise StepFailure(
                4, f"原 PDF 大小与信任锚不一致：{entry['source_pdf_size']} -> {pdf_size}"
                   f"（fail-closed）", document_id=document_id, field="source_pdf_size")

    if set(bindings.handoffs) != set(trust_root["document_order"]):
        raise StepFailure(
            4, f"上游交接集合与信任锚的 document_order 不一致："
               f"{sorted(bindings.handoffs)} != {sorted(trust_root['document_order'])}"
               f"（fail-closed）", field="document_order")
    notes = list(bindings.notes)
    notes.append(f"TS5 正式输入只有 `{SCH.TS5_TRUST_ROOT_SCHEMA_VERSION}` 信任锚"
                 f"（`{TRUST_ROOT_RELPATH}`）：runner 不接受任何 --pdf / --ev-db / "
                 f"--layout / raw snapshot 参数。")
    notes.append("上游 TS4 快照在本 run 内用 pinned issuer 重建，其 snapshot_id 与信任锚"
                 "固定值逐字比较；阶段固定为 threshold_enabled。")
    return Inputs(trust_root=trust_root, ts4_trust_root=ts4_trust_root,
                  bindings=bindings, fixture_pin=fixture_pin, notes=notes)


# ---------------------------------------------------------------------------
# 4. 逐文档构建：TS4 上游 pin → TS5 builder → 独立 verifier
# ---------------------------------------------------------------------------

def build_document(*, inputs: Inputs, document_key: str) -> dict:
    """重建一份文档的 TS4 上游并把 TS5 链跑到底；返回内存结果（不落盘）。"""
    entry = inputs.entry(document_key)
    document_id = entry["document_id"]
    handoff = inputs.handoff(document_key)

    try:
        ts4 = SB._build_from_pinned_handoff(handoff, stage=STAGE_TOKEN)
    except Exception as e:  # noqa: BLE001
        raise StepFailure(5, f"pinned TS4 域构建失败：{type(e).__name__}: {e}"
                             f"（fail-closed）", document_id=document_id,
                          field="ts4_span_snapshot") from e
    # —— 上游 pin：快照身份与规模必须逐项等于信任锚固定值（阶段/输入漂移即失败）。
    if ts4.snapshot_id != entry["ts4_snapshot_id"]:
        raise StepFailure(
            5, f"TS4 快照身份与信任锚 pin 不符：{entry['ts4_snapshot_id']} -> "
               f"{ts4.snapshot_id}（上游阶段性漂移，fail-closed）",
            document_id=document_id, field="ts4_snapshot_id")
    for field, actual, expected in (
            ("ts4_component_count", len(ts4.components), entry["ts4_component_count"]),
            ("ts4_disposition_count", len(ts4.dispositions),
             entry["ts4_disposition_count"])):
        if actual != expected:
            raise StepFailure(5, f"TS4 快照的 {field} 与信任锚 pin 不符：{expected} -> "
                                 f"{actual}（fail-closed）", document_id=document_id,
                              field=field)
    try:
        verified_ts4 = SV.verify_span_snapshot(ts4, handoff)
    except Exception as e:  # noqa: BLE001
        raise StepFailure(5, f"TS4 独立复核失败（不得仅凭快照自证通过）："
                             f"{type(e).__name__}: {e}（fail-closed）",
                          document_id=document_id, field="verify_span_snapshot") from e

    try:
        final, audit = FMB.build_final_material_with_audit(verified_ts4)
    except Exception as e:  # noqa: BLE001
        raise StepFailure(6, f"TS5 final material 构建失败：{type(e).__name__}: {e}"
                             f"（fail-closed）", document_id=document_id,
                          field="build_final_material_snapshot") from e

    refusal_kinds: list = []
    refusal_reasons: list = []
    wrapper = None
    try:
        wrapper = FV.verify_final_material_snapshot(verified_ts4, final)
    except FV.FinalMaterialBlockedError as e:
        # 拒发是**正确结果**，不是运行失败：如实记录阻断缺口种类与计数。
        refusal_kinds = sorted(e.blocking_kinds)
        refusal_reasons = [REFUSAL_BLOCKING_STRUCTURE_GAP]
    except FV.FinalMaterialConservationError:
        # 守恒资格不成立而拒发：同样不当作运行失败，但**必须**与阻断缺口分开记录，
        # 否则"文档被上游矛盾阻断"与"TS5 自己算不平"会互相冒充。`trm-2` 起这件事由
        # 封闭的 `refusal_reason_codes` 承载，因此本支**不**产生任何阻断缺口种类。
        refusal_reasons = [REFUSAL_FINAL_CONSERVATION_INELIGIBLE]
    except Exception as e:  # noqa: BLE001
        raise StepFailure(6, f"TS5 独立复核失败（不得仅凭 snapshot 自证通过）："
                             f"{type(e).__name__}: {e}（fail-closed）",
                          document_id=document_id,
                          field="verify_final_material_snapshot") from e
    verdict = "issued" if wrapper is not None else "refused"
    refusal_reason_codes = sorted(set(refusal_reasons))

    # —— 期望结果 pin：同时挡住"静默缩表"与"引入阻断性缺口"两种退化。
    observed = {
        "table_count": final.table_count,
        "final_span_count": final.final_span_count,
        "blocking_gap_count": final.blocking_gap_count,
        "verifier_issues_capability": verdict == "issued",
        # `document_blocked` 是 **capability 轴**（`trm-2`）：只回答"final capability
        # 能否供下游使用"，因此拒发一律 True、签发一律 False，与拒发**原因**无关。
        # 结构缺口状态另有其轴：`blocking_gap_count`（本字典里就是它）与 manifest
        # 上游根上的 `refusal_reason_codes` / `refusal_blocking_kinds`。两轴不得互相
        # 冒充——"守恒算不平"不是结构缺口，它不得抬高 `blocking_gap_count`；反过来
        # "被上游阻断"也不得被记成"守恒不合格"。
        "document_blocked": wrapper is None,
    }
    declared = {
        "table_count": entry["expected_table_count"],
        "final_span_count": entry["expected_final_span_count"],
        "blocking_gap_count": entry["expected_blocking_gap_count"],
        "verifier_issues_capability": entry["expected_verifier_issues_capability"],
        "document_blocked": entry["expected_document_blocked"],
    }
    # `expected_*` 是 **pin 而非自证**：实施方不得为了通过而改写它们，也不得在失败时
    # 让整个 run 消失。因此这里把每一个不符**如实记为一条失败的机器门**并继续产出
    # 真实产物（before/after、对象增删明细），把"是否接受这个漂移"交回人工裁决。
    pin_mismatches = []
    for field, actual in observed.items():
        if actual == declared[field]:
            continue
        pin_mismatches.append({
            "gate_id": f"pinned_expectation.{field}",
            "document_key": document_key,
            "document_id": document_id,
            "field": field,
            "declared": declared[field],
            "observed": actual,
            "status": "fail",
            "reason": ("TS5 观测结果与信任锚 pinned 期望不符；`expected_*` 是 pin 而非"
                       "自证，漂移必须由人工 review 重新裁决"),
        })
    if pin_mismatches:
        pin_mismatches.append({
            "gate_id": "pinned_expectation.table_inventory",
            "document_key": document_key,
            "document_id": document_id,
            "field": "table_inventory",
            "declared": entry["expected_table_count"],
            "observed": final.table_count,
            "status": "fail" if final.table_count != entry["expected_table_count"]
                      else "pass",
            "reason": "对象增删明细（新增/删除的 TableObject 及原因）见下表；"
                      "本表只读真实产物，不读取任何自报字段",
            "table_inventory": _table_inventory(final),
        })
    if wrapper is not None and wrapper.is_document_blocked():  # pragma: no cover - 不变量
        raise StepFailure(6, "已签发 capability 的 wrapper 不得同时 report blocked"
                             "（fail-closed）", document_id=document_id,
                          field="is_document_blocked")
    if wrapper is not None:
        if not FV.assert_final_material_capability(wrapper):  # pragma: no cover
            raise StepFailure(6, "capability 未被复核域登记（fail-closed）",
                              document_id=document_id, field="capability")
        if wrapper.issuer_scope != "pinned_acceptance":
            raise StepFailure(6, f"TS5 能力的签发域必须为 pinned_acceptance，得到 "
                                 f"{wrapper.issuer_scope!r}（fail-closed）",
                              document_id=document_id, field="issuer_scope")
    return {
        "document_key": document_key,
        "entry": entry,
        "handoff": handoff,
        "ts4": ts4,
        "verified_ts4": verified_ts4,
        "final": final,
        "wrapper": wrapper,
        "audit": list(audit),
        "verdict": verdict,
        "refusal_blocking_kinds": refusal_kinds,
        "refusal_reason_codes": refusal_reason_codes,
        "refusal_reason": (refusal_reason_codes[0] if refusal_reason_codes
                           else None),
        "pin_mismatches": pin_mismatches,
        "declared_expectations": declared,
        "observed_expectations": observed,
    }


def build_all_documents(inputs: Inputs) -> list:
    return [build_document(inputs=inputs, document_key=key)
            for key in inputs.document_order]


# ---------------------------------------------------------------------------
# 5. §19.14.1 的机器产物投影（全部是**验收投影**，不是生产接口）
# ---------------------------------------------------------------------------

def build_versions_payload() -> dict:
    """TS5 的**算法/设置版本束**（`run_manifest.versions` 之外的自报值一律拒绝）。"""
    return {
        "evaluation": dict(SCH.EVALUATION_VERSION_AXES),
        "table": {
            "table_schema": V.TABLE_SCHEMA_VERSION,
            "table_builder": V.TABLE_BUILDER_VERSION,
            "table_cell_schema": V.TABLE_CELL_SCHEMA_VERSION,
            "table_geometry": V.TABLE_GEOMETRY_VERSION,
            "table_relation_schema": V.TABLE_RELATION_SCHEMA_VERSION,
            "table_relation_builder": V.TABLE_RELATION_BUILDER_VERSION,
            "table_range_decision_schema": V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
            "table_citable_coverage_schema": V.TABLE_CITABLE_COVERAGE_SCHEMA_VERSION,
            "table_structure_gap_schema": V.TABLE_STRUCTURE_GAP_SCHEMA_VERSION,
            "table_classification_profile": V.TABLE_CLASSIFICATION_PROFILE_VERSION,
            "table_cell_block_profile": V.TABLE_CELL_BLOCK_PROFILE_VERSION,
            "legacy_table_schema": V.LEGACY_TABLE_SCHEMA_VERSION,
            "legacy_table_builder": V.LEGACY_TABLE_BUILDER_VERSION,
        },
        "final": {
            "final_material_structure_schema":
                V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION,
            "final_material_builder": V.FINAL_MATERIAL_BUILDER_VERSION,
            "final_material_conservation_schema":
                V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION,
            "final_component_binding_schema": V.FINAL_COMPONENT_BINDING_SCHEMA_VERSION,
            "final_span_schema": V.FINAL_SPAN_SCHEMA_VERSION,
            "ts5_final_span_builder": V.TS5_FINAL_SPAN_BUILDER_VERSION,
            "final_verifier": FV.VERIFIER_VERSION,
        },
        "synopsis": {
            # TS4 轴（冻结）与 TS5 final 轴（本批新增）是**两条**独立版本轴，
            # 各自记录：产物里必须能一眼看出某份简介属于哪一代。
            "synopsis_schema": V.SYNOPSIS_SCHEMA_VERSION,
            "synopsis": V.SYNOPSIS_VERSION,
            "final_synopsis_schema": V.FINAL_SYNOPSIS_SCHEMA_VERSION,
            "final_synopsis": V.FINAL_SYNOPSIS_VERSION,
        },
        "upstream": {
            "layout_schema": V.LAYOUT_SCHEMA_VERSION,
            "layout_engine": V.LAYOUT_ENGINE_VERSION,
            "outline_schema": V.OUTLINE_SCHEMA_VERSION,
            "outline_algorithm": V.OUTLINE_ALGORITHM_VERSION,
            "toc_body_reconciliation": V.TOC_BODY_RECONCILIATION_VERSION,
            "alignment_schema": V.ALIGN_SCHEMA_VERSION,
            "aligner": V.ALIGNER_VERSION,
            "normalization": V.NORMALIZATION_VERSION,
            "evidence_set_snapshot": V.EVIDENCE_SET_SNAPSHOT_VERSION,
            "span_schema": V.SPAN_SCHEMA_VERSION,
            "span_builder": V.SPAN_BUILDER_VERSION,
            "ts4_body_span_builder": V.TS4_BODY_SPAN_BUILDER_VERSION,
            "verified_span_snapshot": V.VERIFIED_SPAN_SNAPSHOT_VERSION,
            "verified_ts3_handoff": V.VERIFIED_TS3_HANDOFF_VERSION,
            "pinned_ts3_handoff": V.PINNED_TS3_HANDOFF_VERSION,
            "pinned_alignment_issuer": V.PINNED_ALIGNMENT_ISSUER_VERSION,
            "pinned_evidence_authority": V.PINNED_EVIDENCE_AUTHORITY_VERSION,
            "table_region_qualification": V.TABLE_REGION_QUALIFICATION_VERSION,
            "heading_qualification_profile": V.HEADING_QUALIFICATION_PROFILE_VERSION,
        },
    }


def build_table_objects(samples: list, *, document_order: Sequence[str]) -> dict:
    rows: list = []
    for sample in samples:
        snapshot = sample["final"]
        for index, table in enumerate(snapshot.tables):
            rows.append({"document_key": sample["document_key"],
                         "document_id": snapshot.document_id,
                         "table_index": index,
                         "table": _as_dict(table)})
    return {
        "manual_review_required": True,
        "artifact": "table_objects.json",
        "authority_note": (
            "`TableObject` 是 Evidence-backed material/navigation object，"
            "**不是** `FinancialSnapshot`；它不携带任何金额权威。"),
        "schema_version": V.TABLE_SCHEMA_VERSION,
        "builder_version": V.TABLE_BUILDER_VERSION,
        "document_order": list(document_order),
        "counts": {sample["document_key"]: sample["final"].table_count
                   for sample in samples},
        "structure_kind_histogram": _histogram(
            t.structure_kind for s in samples for t in s["final"].tables),
        "structure_class_histogram": _histogram(
            t.structure_class for s in samples for t in s["final"].tables),
        "structure_state_histogram": _histogram(
            t.structure_state for s in samples for t in s["final"].tables),
        "owner_kind_histogram": _histogram(
            t.owner.owner_kind for s in samples for t in s["final"].tables),
        "table_count": len(rows),
        "tables": rows,
    }


def build_table_objects_md(samples: list) -> str:
    lines = [
        "# TS5 TableObject 清单（人读投影）",
        "",
        "> 本文件由内存中的 `FinalMaterialStructureSnapshot` **派生**；唯一授权产物是同目录",
        "> 的 `table_objects.json` 与 `final_material_structure_snapshots/*.json`。",
        "",
    ]
    for sample in samples:
        snapshot = sample["final"]
        lines.append(f"## {sample['document_key']}（{snapshot.document_id}）")
        lines.append("")
        lines.append(f"- snapshot: `{snapshot.snapshot_id}`")
        lines.append(f"- table_count: {snapshot.table_count} / "
                     f"final_span_count: {snapshot.final_span_count} / "
                     f"blocking_gap_count: {snapshot.blocking_gap_count}")
        lines.append("")
        if not snapshot.tables:
            lines.append("_本文件未产出任何 TableObject（诚实负面终态，见 `table_gaps.json`）。_")
            lines.append("")
            continue
        for table in snapshot.tables:
            owner = table.owner
            lines.append(f"### `{table.table_id}`（p{table.page_number}）")
            lines.append("")
            lines.append(f"- locator: `{table.table_locator}`")
            lines.append(f"- structure: kind=`{table.structure_kind}` "
                         f"class=`{table.structure_class}` state=`{table.structure_state}`"
                         + (f"（{table.structure_state_reason}）"
                            if table.structure_state_reason else ""))
            if table.header_absence_reason:
                lines.append(f"- header_absence_reason: `{table.header_absence_reason}`")
            lines.append(f"- 尺寸: {len(table.rows)} 行 × {table.column_count} 列")
            lines.append(f"- owner: kind=`{owner.owner_kind}` node=`{owner.node_id}` "
                         f"boundary=`{owner.source_boundary_kind}"
                         f"/{owner.source_boundary_id}`")
            lines.append(f"- title blocks: {len(table.title_blocks)} / unit: "
                         f"`{_truncate(table.unit_text or '', 60)}` / note blocks: "
                         f"{len(table.note_blocks)}")
            lines.append(f"- source_refs: {len(table.source_refs)}；"
                         f"continuation candidates: "
                         f"{len(table.continuation_candidate_locators)}")
            if table.missing_or_uncertain_fields:
                lines.append("- missing_or_uncertain_fields: "
                             f"`{list(table.missing_or_uncertain_fields)}`")
            lines.append(f"- fingerprints: structure=`{table.structure_fingerprint[:16]}…` "
                         f"provenance=`{table.provenance_fingerprint[:16]}…` "
                         f"content=`{table.content_fingerprint[:16]}…`")
            lines.append("")
            for row in table.rows:
                cells = " | ".join(_truncate(cell.text, 40) for cell in row.cells)
                repeat = "（重复表头）" if row.is_repeated_header else ""
                lines.append(f"  - r{row.row_index} `{row.role}`{repeat} "
                             f"{_md_escape(row.label or '')}"
                             + (f" ⟶ {_md_escape(cells)}" if cells else ""))
            lines.append("")
    return "\n".join(lines) + "\n"


def build_table_preview_md(samples: list) -> str:
    """栅格预览：**渲染**，不是权威。空 cell 与 merged cell 以占位符显示。"""
    lines = [
        "# TS5 表格栅格预览（渲染，非权威）",
        "",
        "> 本文件只是 `table_objects.json` 的**可读渲染**：merged cell 用 `↳` 占位，",
        "> 换行折叠为 ` / `，超长文本截断。任何判断必须以 `table_objects.json` 与",
        "> `final_material_structure_snapshots/*.json` 为准。",
        "",
    ]
    for sample in samples:
        lines.append(f"## {sample['document_key']}")
        lines.append("")
        if not sample["final"].tables:
            lines.append("_无 TableObject。_")
            lines.append("")
            continue
        for table in sample["final"].tables:
            lines.append(f"### `{table.table_id}`（p{table.page_number}，"
                         f"{table.structure_kind}）")
            lines.append("")
            if table.title_blocks:
                lines.append("**表题**：" + " ".join(
                    _truncate(b.text, 120) for b in table.title_blocks))
                lines.append("")
            if table.unit_text:
                lines.append(f"**单位**：{_truncate(table.unit_text, 120)}")
                lines.append("")
            occupied: dict = {}
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    cells.append(cell)
                for cell in cells:
                    occupied[(cell.row, cell.column)] = cell
            columns = max((c for _, c in occupied), default=-1) + 1
            header = "| " + " | ".join(f"c{i}" for i in range(columns)) + " |"
            lines.append(header)
            lines.append("|" + "---|" * columns)
            for row in table.rows:
                by_col = {cell.column: cell for cell in row.cells}
                out = []
                for col in range(columns):
                    cell = by_col.get(col)
                    if cell is None:
                        out.append(" ")
                    else:
                        out.append(_md_escape(_truncate(cell.text, 60)))
                lines.append("| " + " | ".join(out) + " |")
            lines.append("")
            if table.note_blocks:
                for block in table.note_blocks:
                    lines.append(f"> 表注 `{block.role}`：{_truncate(block.text, 160)}")
                lines.append("")
    return "\n".join(lines) + "\n"


def build_table_cell_fragments(samples: list) -> list:
    """逐 cell / 逐内部 block 的片段行（含表题 / 单位 / 表注的非 cell 片段）。"""
    rows: list = []
    part_of = (("title_blocks", "title"), ("unit_blocks", "unit"),
               ("note_blocks", "note"))
    for sample in samples:
        snapshot = sample["final"]
        for table in snapshot.tables:
            for row in table.rows:
                for cell in row.cells:
                    for block in cell.blocks:
                        rows.append({
                            "document_key": sample["document_key"],
                            "table_id": table.table_id,
                            "table_locator": table.table_locator,
                            "owner_part": "cell",
                            "row_index": row.row_index,
                            "row_role": row.role,
                            "column": cell.column,
                            "rowspan": cell.rowspan,
                            "colspan": cell.colspan,
                            "block_index": block.block_index,
                            "block_role": block.role,
                            "text": block.text,
                            "source_ref_ids": list(block.source_ref_ids),
                            "block_content_fingerprint": block.content_fingerprint,
                            "cell_schema_version": cell.schema_version,
                            "cell_source_ref_count": len(cell.source_refs),
                        })
            for attr, part in part_of:
                for block in getattr(table, attr):
                    rows.append({
                        "document_key": sample["document_key"],
                        "table_id": table.table_id,
                        "table_locator": table.table_locator,
                        "owner_part": part,
                        "row_index": None,
                        "row_role": None,
                        "column": None,
                        "rowspan": None,
                        "colspan": None,
                        "block_index": block.block_index,
                        "block_role": block.role,
                        "text": block.text,
                        "source_ref_ids": list(block.source_ref_ids),
                        "block_content_fingerprint": block.content_fingerprint,
                        "cell_schema_version": None,
                        "cell_source_ref_count": None,
                    })
    rows.sort(key=lambda r: (r["document_key"], r["table_id"],
                             -1 if r["row_index"] is None else r["row_index"],
                             -1 if r["column"] is None else r["column"],
                             r["owner_part"], r["block_index"]))
    return rows


def build_terminal_ledger(samples: list) -> dict:
    """每个 TableObject 的 **source_ref 台账**：terminal 身份、可引用性与使用处。

    §19.9.1：cell provenance 必须落到真实 terminal source。本台账把"哪些 ref 真正
    落在表上"与"它们各自可否被引用"分开列出，而不是只给一个总数。
    """
    documents: list = []
    for sample in samples:
        snapshot = sample["final"]
        tables: list = []
        for table in snapshot.tables:
            usage: dict = {}
            for attr, part in (("title_blocks", "title"), ("unit_blocks", "unit"),
                               ("note_blocks", "note")):
                for block in getattr(table, attr):
                    for ref_id in block.source_ref_ids:
                        usage.setdefault(ref_id, set()).add(f"{part}:{block.block_index}")
            for row in table.rows:
                for cell in row.cells:
                    for ref in cell.source_refs:
                        usage.setdefault(ref.source_ref_id, set()).add(
                            f"cell:{row.row_index}:{cell.column}")
            ledger = []
            for ref in table.source_refs:
                row = _as_dict(ref)
                row["used_by"] = sorted(usage.get(ref.source_ref_id, ()))
                ledger.append(row)
            ledger.sort(key=lambda r: r["source_ref_id"])
            tables.append({
                "table_id": table.table_id,
                "table_locator": table.table_locator,
                "page_number": table.page_number,
                "source_ref_count": len(ledger),
                "referenced_ref_count": sum(1 for r in ledger if r["used_by"]),
                "orphan_ref_count": sum(1 for r in ledger if not r["used_by"]),
                "terminal_kind_histogram": _histogram(r["terminal_kind"] for r in ledger),
                "verdict_histogram": _histogram(r["verdict"] for r in ledger),
                "citable_count": sum(1 for r in ledger if r["citable"]),
                "non_citable_count": sum(1 for r in ledger if not r["citable"]),
                "citable_reason_histogram": _histogram(
                    r["citable_reason"] for r in ledger),
                "refusal_reason_histogram": _histogram(
                    r["refusal_reason"] for r in ledger if r["refusal_reason"]),
                "refs": ledger,
            })
        documents.append({
            "document_key": sample["document_key"],
            "document_id": snapshot.document_id,
            "table_count": snapshot.table_count,
            "tables": tables,
        })
    return {
        "manual_review_required": True,
        "artifact": "table_terminal_ledger.json",
        "scope": ("每个 TableObject 的 source_ref 台账：terminal_kind / terminal 身份 / "
                  "alignment_id / verdict / 逐 cell 可引用性判定，以及每个 ref 的实际"
                  "使用处（表题 / 单位 / 表注 / 具体 cell）"),
        "documents": documents,
    }


def build_citable_coverage(samples: list) -> dict:
    documents: list = []
    for sample in samples:
        snapshot = sample["final"]
        coverages = []
        for coverage in snapshot.coverages:
            row = _as_dict(coverage)
            row["interval_count"] = len(coverage.intervals)
            row["citable_interval_count"] = sum(
                1 for interval in coverage.intervals if interval.citable)
            row["non_citable_interval_count"] = sum(
                1 for interval in coverage.intervals if not interval.citable)
            coverages.append(row)
        documents.append({
            "document_key": sample["document_key"],
            "document_id": snapshot.document_id,
            "coverage_count": len(coverages),
            "coverages": coverages,
        })
    return {
        "manual_review_required": True,
        "artifact": "table_citable_coverage.json",
        "scope": "每个 TableObject 的可引用覆盖：逐 cell ref 的区间、证据字符范围与不可引用原因",
        "documents": documents,
    }


def build_final_spans(samples: list) -> dict:
    documents = []
    for sample in samples:
        snapshot = sample["final"]
        documents.append({
            "document_key": sample["document_key"],
            "document_id": snapshot.document_id,
            "final_span_count": snapshot.final_span_count,
            "spans": [_as_dict(span) for span in snapshot.final_spans],
        })
    return {
        "manual_review_required": True,
        "artifact": "final_spans.json",
        "schema_version": V.FINAL_SPAN_SCHEMA_VERSION,
        "builder_version": V.TS5_FINAL_SPAN_BUILDER_VERSION,
        "scope": "§19.7 的 final outline span（表外正文与表前/表后说明的唯一材料单位）",
        "documents": documents,
    }


def build_final_navigation_synopsis(samples: list) -> dict:
    documents = []
    for sample in samples:
        snapshot = sample["final"]
        synopses = [_as_dict(item) for item in snapshot.synopses]
        documents.append({
            "document_key": sample["document_key"],
            "document_id": snapshot.document_id,
            "synopsis_count": len(synopses),
            "status_histogram": _histogram(s.get("status") for s in synopses),
            "reason_code_histogram": _histogram(s.get("reason_code")
                                                for s in synopses
                                                if s.get("reason_code")),
            "synopses": synopses,
        })
    return {
        "manual_review_required": True,
        "artifact": "final_navigation_synopsis.json",
        "schema_version": V.FINAL_SYNOPSIS_SCHEMA_VERSION,
        "synopsis_version": V.FINAL_SYNOPSIS_VERSION,
        "ts4_schema_version": V.SYNOPSIS_SCHEMA_VERSION,
        "ts4_synopsis_version": V.SYNOPSIS_VERSION,
        "scope": ("final synopsis 只是 TS6/Topic 导航接口，原文回指由片段承载；"
                  "它**不是**证据，也不授权任何 claim"),
        "documents": documents,
    }


def build_table_gaps(samples: list) -> dict:
    entries: list = []
    for sample in samples:
        for entry in sample["final"].gaps.entries:
            row = _as_dict(entry)
            row["document_key"] = sample["document_key"]
            entries.append(row)
    entries.sort(key=lambda r: (r["document_key"], r["gap_kind"],
                                r["detail_code"] or "",
                                r["page_number"] if r["page_number"] is not None else -1,
                                r["table_id"] or "", r["component_id"] or "",
                                r["disposition_id"] or ""))
    blocking = [e for e in entries if e["blocks_document_capability"]]
    return {
        "manual_review_required": True,
        "artifact": "table_gaps.json",
        "scope": "§19.4.3 的七类结构缺口（封闭词表）；阻断性缺口只允许两种",
        "gap_kinds": list(TS.TABLE_GAP_KINDS),
        "blocking_gap_kinds": list(TS.GAP_BLOCKING_KINDS),
        "gap_kind_histogram": _histogram(e["gap_kind"] for e in entries),
        "detail_code_histogram": _histogram(e["detail_code"] for e in entries),
        "blocking_detail_histogram": _histogram(e["detail_code"] for e in blocking),
        "entry_count": len(entries),
        "blocking_entry_count": len(blocking),
        "per_document_counts": {sample["document_key"]:
                                len(sample["final"].gaps.entries) for sample in samples},
        "entries": entries,
    }


def build_visual_object_gaps(samples: list) -> dict:
    entries: list = []
    for sample in samples:
        for entry in sample["final"].gaps.entries:
            if entry.gap_kind != "visual_object_not_table":
                continue
            row = _as_dict(entry)
            row["document_key"] = sample["document_key"]
            entries.append(row)
    entries.sort(key=lambda r: (r["document_key"],
                                r["page_number"] if r["page_number"] is not None else -1,
                                r["detail_code"] or ""))
    return {
        "manual_review_required": True,
        "artifact": "visual_object_gaps.json",
        "scope": ("§19.13 视觉对象负例：有“表”字但无可靠网格的视觉对象（组织结构图等）"
                  "必须**不建** TableObject，并在此显式留痕"),
        "gap_kind": "visual_object_not_table",
        "entry_count": len(entries),
        "detail_code_histogram": _histogram(e["detail_code"] for e in entries),
        "page_histogram": _histogram(e["page_number"] for e in entries),
        "per_document_counts": _histogram(e["document_key"] for e in entries),
        "entries": entries,
        "not_a_positive_result": ("本产物的存在**不是**能力通过：它只证明负面终态被诚实"
                                  "记录；同一能力必须另有正向结构样本（§19.13）。"),
    }


def build_final_conservation(samples: list) -> dict:
    documents = []
    for sample in samples:
        snapshot = sample["final"]
        conservation = snapshot.conservation
        layers = [_as_dict(layer) for layer in conservation.layers]
        documents.append({
            "document_key": sample["document_key"],
            "document_id": snapshot.document_id,
            "conservation_id": conservation.conservation_id,
            "layer_kind_histogram": _histogram(layer["layer_kind"] for layer in layers),
            # 逐层与整篇都用**共用资格函数重算**：算术守恒、`problems` 为空、
            # `residual_gap` 为 0 三者缺一即不 balanced，也不 eligible。
            "balanced": all(TS.conservation_layer_eligible(
                layer.layer_kind, TS.conservation_layer_values(layer),
                TS.conservation_term_value(layer.total), layer.problems)
                for layer in conservation.layers),
            "conservation_eligible": bool(TS.conservation_eligible(conservation)),
            "problem_count": len(conservation.problems),
            "problems": list(conservation.problems),
            "layers": layers,
        })
    return {
        "manual_review_required": True,
        "artifact": "final_conservation.json",
        "scope": ("§19.9.3 的四级守恒（disposition / component / layout_text / "
                  "evidence_interval）；`balanced` 由 `conservation_layer_eligible` "
                  "重算，不含任何自报布尔值"),
        "schema_version": V.FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION,
        "all_documents_balanced": all(d["balanced"] for d in documents),
        "documents": documents,
    }


def authority_isolation_problems() -> list:
    """AST 判据：TS5 生产链不得引入任何财务权威类型 / 模块。"""
    import ast  # noqa: PLC0415 - 只在本函数内使用，避免顶层 import 噪声
    problems: list = []
    ts5_modules = ("table_schema.py", "table_geometry.py",
                   "table_classification.py", "table_builder.py",
                   "final_material_builder.py", "final_verifier.py")
    files = [rel for rel in CODE_FINGERPRINT_FILES
             if rel.startswith("document_structure/")
             and rel.rsplit("/", 1)[-1] in ts5_modules]
    for rel in files:
        path = REPO_ROOT / rel
        if not path.is_file():  # pragma: no cover - 由代码指纹先一步拦截
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:  # pragma: no cover
            problems.append(f"{rel} 无法解析：{e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.rsplit(".", 1)[-1] in _FORBIDDEN_AUTHORITY_MODULES:
                        problems.append(f"{rel} 不得 import {alias.name!r}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").rsplit(".", 1)[-1]
                if module in _FORBIDDEN_AUTHORITY_MODULES:
                    problems.append(f"{rel} 不得 from {node.module!r} import")
                for alias in node.names:
                    if alias.name in _FORBIDDEN_AUTHORITY_SYMBOLS:
                        problems.append(f"{rel} 不得 import {alias.name!r}")
            elif isinstance(node, ast.Name):
                if node.id in _FORBIDDEN_AUTHORITY_SYMBOLS:
                    problems.append(f"{rel} 不得引用财务权威名 {node.id!r}")
    return sorted(set(problems))


def _negative_token_table_lines(tree) -> dict:
    """模块级 `_FORBIDDEN_*` 表的元素行号 → 表名。

    这些表里写的**就是**"禁止出现的形态"（例如 `_FORBIDDEN_PROFILE_TOKEN_PATTERNS`
    里的 `(r"(?i)\\b(?:300750|CATL)\\b", "已登记的案例形态")`）。把这种字面量报成
    "生产里使用了 300750"是对代码库的假陈述：那个守卫的存在恰恰是为了**拒绝**该形态。

    因此扫描跳过"负向表自身的元素行"，其余任何位置出现同一字面量仍然照报。豁免范围
    只有一条：**名字以 `_FORBIDDEN_` 开头的模块级赋值（含 AnnAssign）里的字符串常量
    行**。把真实分支藏进这种表会立刻破坏该表的用途，而豁免清单一并进入机器门证据，
    因此这个口子是可核对、可审计的，不是静默放过。
    """
    import ast  # noqa: PLC0415 - 只在本地检查中使用
    out: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        name = next((n for n in names if n.startswith("_FORBIDDEN_")), None)
        if name is None or node.value is None:
            continue
        for literal in ast.walk(node.value):
            if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                out.setdefault(literal.lineno, name)
    return out


def _page_or_company_literals_in_production() -> list:
    """AST 判据：TS5 生产模块里**不得**出现公司/证券代码/固定页码/固定表号的字面量。

    这是**负向**检查：列出的 token 是"禁止出现"的，因此不存在按公司或页码分叉的
    生产分支；命中的字面量本身就是违规证据。它不生成任何内容，也不参与判断任何
    文档该不该被受理。

    唯一豁免是"负向表自身的元素行"（见 `_negative_token_table_lines`）：一个**禁止**
    300750 的守卫不得被读成**使用**了 300750。豁免逐条进入 `_literal_scan_exemptions`
    供审阅者对账。
    """
    import ast  # noqa: PLC0415 - 只在本地检查中使用
    problems: list = []
    ts5_modules = ("table_schema.py", "table_geometry.py",
                   "table_classification.py", "table_builder.py",
                   "final_material_builder.py", "final_verifier.py")
    files = [rel for rel in CODE_FINGERPRINT_FILES
             if rel.startswith("document_structure/")
             and rel.rsplit("/", 1)[-1] in ts5_modules]
    for rel in files:
        path = REPO_ROOT / rel
        if not path.is_file():  # pragma: no cover
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:  # pragma: no cover
            problems.append(f"{rel} 无法解析：{e}")
            continue
        exempt = _negative_token_table_lines(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.lineno in exempt:
                continue
            text = node.value
            if any(token in text for token in _FORBIDDEN_CONTENT_TOKENS):
                problems.append(f"{rel}:{node.lineno} 含公司字面量 {text!r}")
            elif re.fullmatch(r"\d{6}", text):
                problems.append(f"{rel}:{node.lineno} 含证券代码形状字面量 {text!r}")
            elif re.fullmatch(r"(第\d+页|\d+页|表\d+(-\d+)?|p\d+)", text):
                problems.append(f"{rel}:{node.lineno} 含固定页码/表号字面量 {text!r}")
    return sorted(set(problems))


def _literal_scan_exemptions() -> list:
    """字面量扫描**实际**豁免掉的每一行（进机器门证据，供审阅者对账）。"""
    import ast  # noqa: PLC0415 - 只在本地检查中使用
    ts5_modules = ("table_schema.py", "table_geometry.py",
                   "table_classification.py", "table_builder.py",
                   "final_material_builder.py", "final_verifier.py")
    rows: list = []
    for rel in CODE_FINGERPRINT_FILES:
        if not (rel.startswith("document_structure/")
                and rel.rsplit("/", 1)[-1] in ts5_modules):
            continue
        path = REPO_ROOT / rel
        if not path.is_file():  # pragma: no cover
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for lineno, name in sorted(_negative_token_table_lines(tree).items()):
            rows.append({"file": rel, "line": lineno, "declaration": name})
    return rows


def build_authority_separation(samples: list) -> dict:
    """§19.6.3 / §19.17：`TableObject` 不取得任何金额权威（机械可判、非声明）。"""
    field_names = sorted(f.name for f in dataclasses.fields(TS.TableObjectV4))
    suspicious = sorted(
        name for name in field_names
        if any(pattern in name.lower() for pattern in _FINANCIAL_FIELD_PATTERNS))
    problems = authority_isolation_problems()
    return {
        "manual_review_required": True,
        "artifact": "authority_separation.json",
        "table_object_type": "TableObjectV4",
        "table_object_schema_version": V.TABLE_SCHEMA_VERSION,
        "table_object_builder_version": V.TABLE_BUILDER_VERSION,
        "amount_authority_sources": list(S.AMOUNT_AUTHORITY_SOURCES),
        "table_object_field_names": field_names,
        "financial_authority_field_patterns": list(_FINANCIAL_FIELD_PATTERNS),
        "financial_authority_fields_present": suspicious,
        "financial_authority_claimed": False,
        "financial_fact_pack_constructed": False,
        "financial_snapshot_constructed": False,
        "forbidden_authority_modules": list(_FORBIDDEN_AUTHORITY_MODULES),
        "forbidden_authority_symbols": list(_FORBIDDEN_AUTHORITY_SYMBOLS),
        "isolation_problems": problems,
        "document_table_counts": {sample["document_key"]: sample["final"].table_count
                                  for sample in samples},
        "statement": (
            "`TableObject` 只承载结构、定位、可比对的文本与 provenance；金额权威一律来自 "
            "`FinancialSnapshot` / `FinancialFactPack`。本 run 未构造、未引用、未声称任何"
            "财务权威对象，也未 import `financial_v2` / `financial_worker`。"),
        "ok": not suspicious and not problems,
    }


def build_final_snapshot_index(samples: list, *, results_dir: pathlib.Path) -> dict:
    entries = []
    for sample in samples:
        snapshot = sample["final"]
        relpath = f"{MATRIX_SNAPSHOT_DIRNAME}/{sample['document_key']}.json"
        path = results_dir / relpath
        entries.append({
            "document_key": sample["document_key"],
            "document_id": snapshot.document_id,
            "relpath": relpath,
            "snapshot_locator": snapshot.snapshot_locator,
            "snapshot_id": snapshot.snapshot_id,
            "content_fingerprint": snapshot.content_fingerprint,
            "upstream_dependency_fingerprint": snapshot.upstream_dependency_fingerprint,
            "verified_span_snapshot_id": snapshot.verified_span_snapshot_id,
            "verifier_verdict": sample["verdict"],
            "verification_fingerprint": (sample["wrapper"].verification_fingerprint
                                         if sample["wrapper"] is not None else None),
            "refusal_blocking_kinds": list(sample["refusal_blocking_kinds"]),
            "table_count": snapshot.table_count,
            "final_span_count": snapshot.final_span_count,
            "decision_count": len(snapshot.decisions),
            "relation_count": len(snapshot.relations),
            "binding_count": len(snapshot.bindings),
            "coverage_count": len(snapshot.coverages),
            "synopsis_count": len(snapshot.synopses),
            "component_count": snapshot.component_count,
            "blocking_gap_count": snapshot.blocking_gap_count,
            "file_sha256": sha256_file(path) if path.is_file() else None,
        })
    return {
        "manual_review_required": True,
        "artifact": "final_snapshot_index.json",
        "schema_version": V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION,
        "builder_version": V.FINAL_MATERIAL_BUILDER_VERSION,
        "document_order": [sample["document_key"] for sample in samples],
        "snapshots": entries,
    }


def build_machine_gates(*, samples: list, immutability: dict) -> dict:
    """本 run 的机器门：每一项都由**真实产物重算**，没有任何输入 verdict 可自报。

    `passed` 只表示"机器侧没有发现失败门"。它**不等于**验收通过：人工 review 未完成
    时 `review_decision.decision` 恒为 `needs_changes`。
    """
    required_rows = derive_required_sample_verdicts(samples)
    run_level: list = [
        {
            "gate_id": "database_immutability_ok",
            "gate_class": "read_only_isolation",
            "status": "pass" if immutability["unchanged"] else "fail",
            "declared": True,
            "observed": bool(immutability["unchanged"]),
            "artifact_refs": ["database_immutability.json"],
            "detail": (f"changed={immutability['changed']} "
                       f"watched_missing={immutability['watched_missing']}"),
        },
        {
            "gate_id": "authority_separation_ok",
            "gate_class": "authority_isolation",
            "status": "pass" if not authority_isolation_problems() else "fail",
            "declared": True,
            "observed": not authority_isolation_problems(),
            "artifact_refs": ["authority_separation.json"],
            "detail": (f"TS5 生产模块中的财务权威违规："
                       f"{authority_isolation_problems()[:3]}"),
        },
        {
            "gate_id": "no_company_or_fixed_locator_literals",
            "gate_class": "generality_isolation",
            "status": "pass" if not _page_or_company_literals_in_production()
                      else "fail",
            "declared": True,
            "observed": not _page_or_company_literals_in_production(),
            "artifact_refs": ["authority_separation.json"],
            "detail": ("TS5 生产模块中的公司/证券代码/固定页码表号字面量（负向判据）："
                       f"{_page_or_company_literals_in_production()[:3]}"),
            # 豁免逐行列出：一个"禁止 300750"的守卫不得被读成"使用了 300750"，但豁免
            # 本身必须可对账——审阅者能看见到底跳过了哪几行、在哪张表里。
            "exempted_negative_token_declarations": _literal_scan_exemptions(),
        },
    ]
    for row in required_rows:
        run_level.append({
            "gate_id": f"required_sample.{row['capability']}",
            "gate_class": "required_sample_matrix",
            "status": row["status"],
            "declared": True,
            "observed": row["status"] == "pass",
            "artifact_refs": list(row["artifact_refs"]),
            "failure_reasons": list(row["failure_reasons"]),
            "detail": (f"{row['document_key']} / {row['sample']}；裁决来源="
                       f"{row['verdict_source']}（执行者无自报入口）"),
        })
    documents: list = []
    for sample in samples:
        gates = _document_machine_gates(sample)
        failed = [gate["gate_id"] for gate in gates if gate["status"] != "pass"]
        documents.append({
            "document_key": sample["document_key"],
            "document_id": sample["final"].document_id,
            "verifier_verdict": sample["verdict"],
            "refusal_reason": sample["refusal_reason"],
            "gates": gates,
            "failed_gate_ids": failed,
        })
    failed_ids = [f"{doc['document_key']}:{gate['gate_id']}"
                  for doc in documents for gate in doc["gates"]
                  if gate["status"] != "pass"]
    failed_ids += [f"run:{gate['gate_id']}" for gate in run_level
                   if gate["status"] != "pass"]
    return {
        "machine_gates_version": "tgm-1",
        "run_level_gates": run_level,
        "documents": documents,
        "required_sample_matrix": required_rows,
        "failed_gate_ids": failed_ids,
        "failed_gate_count": len(failed_ids),
        "passed": not failed_ids,
        "note": ("全部机器门由真实产物重算（pin 漂移、四层守恒资格、能力签发与资格"
                 "一致性、逐样本派生裁决、只读隔离与通用性），执行者没有可自报的"
                 "入口；`passed=false` 时 `review_decision.machine_gates_passed` "
                 "必须为 `false` 且 `decision` 恒为 `needs_changes`。"),
    }


def build_acceptance_matrix(samples: list, *, run_id: str,
                            machine_gates: dict | None = None) -> dict:
    """§19.13 的 10 项人工检查 × 机器证据索引（**不代填任何 verdict**）。"""
    per_doc = {sample["document_key"]: sample for sample in samples}
    evidence = {
        "no_false_positive_table": {
            "metric": "候选准入门（unresolved / rejected 全量留痕）",
            "artifact_refs": ["table_candidate_audit.jsonl", "table_objects.json",
                              "table_preview.md"],
            "counts": {key: per_doc[key]["audit_outcome_histogram"] for key in per_doc},
        },
        "table_parts_and_boundaries_correct": {
            "metric": "title/unit/note blocks + range decisions",
            "artifact_refs": ["table_objects.json", "table_range_decisions.json",
                              "table_preview.md"],
            "counts": {key: {
                "tables": per_doc[key]["final"].table_count,
                "decisions": len(per_doc[key]["final"].decisions),
                "range_kind_histogram": per_doc[key]["range_kind_histogram"],
            } for key in per_doc},
        },
        "merged_multiline_cells_traceable": {
            "metric": "cell fragments（rowspan/colspan + 内部 block）",
            "artifact_refs": ["table_cell_fragments.jsonl", "table_preview.md",
                              "table_terminal_ledger.json"],
            "counts": {key: per_doc[key]["cell_fragment_stats"] for key in per_doc},
        },
        "surrounding_text_relations_correct": {
            "metric": "typed relation 端点与种类",
            "artifact_refs": ["table_relations.json", "final_spans.json"],
            "counts": {key: {
                "relations": len(per_doc[key]["final"].relations),
                "relation_kind_histogram": per_doc[key]["relation_kind_histogram"],
            } for key in per_doc},
        },
        "continuation_identity_correct": {
            "metric": "continued_by relation + continuation 锚点/候选",
            "artifact_refs": ["table_relations.json", "table_objects.json"],
            "counts": {key: per_doc[key]["continuation_stats"] for key in per_doc},
        },
        "internal_cell_headings_retained": {
            "metric": "key_value_form / 表内小标题 block 保留",
            "artifact_refs": ["table_objects.json", "table_cell_fragments.jsonl",
                              "table_objects.md"],
            "counts": {key: {
                "key_value_form_tables": per_doc[key]["structure_kind_histogram"].get(
                    "key_value_form", 0),
                "block_role_histogram": per_doc[key]["block_role_histogram"],
            } for key in per_doc},
        },
        "nonfinancial_tables_use_generic_path": {
            "metric": "同一 builder/schema/verifier；非财务表计数",
            "artifact_refs": ["table_objects.json", "authority_separation.json"],
            "counts": {key: {
                "tables": per_doc[key]["final"].table_count,
                "structure_class_histogram": per_doc[key]["structure_class_histogram"],
            } for key in per_doc},
        },
        "financial_authority_separated": {
            "metric": "TableObject 字段不得含金额权威字段名",
            "artifact_refs": ["authority_separation.json"],
            "counts": {"financial_authority_fields_present": "见 authority_separation.json",
                       "ok": "见 authority_separation.json"},
        },
        "explicit_gaps_are_honest": {
            "metric": "七类 gap 的逐条留痕与阻断分类",
            "artifact_refs": ["table_gaps.json", "visual_object_gaps.json",
                              "database_immutability.json"],
            "counts": {key: {
                "gap_count": len(per_doc[key]["final"].gaps.entries),
                "gap_kind_histogram": per_doc[key]["gap_kind_histogram"],
                "blocking_gap_count": per_doc[key]["final"].blocking_gap_count,
            } for key in per_doc},
        },
        "final_conservation_zero": {
            "metric": "三级守恒层 balanced 与 problems 为空",
            "artifact_refs": ["final_conservation.json"],
            "counts": {key: per_doc[key]["conservation_summary"] for key in per_doc},
        },
    }
    rows = []
    for check_id, question in zip(SCH.REQUIRED_REVIEW_CHECK_IDS, REVIEW_QUESTIONS):
        payload = evidence[check_id]
        rows.append({
            "check_id": check_id,
            "review_question": question,
            "machine_metric": payload["metric"],
            "artifact_refs": payload["artifact_refs"],
            "machine_evidence": payload["counts"],
            "manual_verdict_by_role": {role: "not_reviewed"
                                       for role in SCH.REVIEWER_ROLES},
        })
    required_rows = (machine_gates or {}).get("required_sample_matrix") \
        or derive_required_sample_verdicts(samples)
    return {
        "manual_review_required": True,
        "artifact": "table_acceptance_matrix.json",
        "run_id": run_id,
        "review_schema_version": SCH.TS5_REVIEW_SCHEMA_VERSION,
        "document_order": [sample["document_key"] for sample in samples],
        "review_check_ids": list(SCH.REQUIRED_REVIEW_CHECK_IDS),
        "reviewer_roles": list(SCH.REVIEWER_ROLES),
        "rows": rows,
        # §19.13 的固定真实样本：**声明要求 + 派生裁决**。裁决由
        # `derive_required_sample_verdicts` 从真实产物重算，不存在"执行者自报通过"
        # 的字段；`self_reported_verdict_accepted` 恒为 `false`。
        "required_sample_matrix": [dict(item) for item in required_rows],
        "machine_gates": machine_gates,
        "note": ("本表只把**机器证据的读回位置**摆出来，方便两位审查人独立作答；"
                 "`manual_verdict_by_role` 永远是 `not_reviewed`，实施方不得代填。"
                 "`required_sample_matrix` 的 `status` 是派生裁决，不是人工结论。"),
    }


def build_acceptance_matrix_md(matrix: dict) -> str:
    lines = [
        "# TS5 人工验收矩阵（机器证据索引，非裁决）",
        "",
        f"- run_id: `{matrix['run_id']}`",
        f"- review schema: `{matrix['review_schema_version']}`",
        f"- reviewer roles: {', '.join(matrix['reviewer_roles'])}",
        "",
        "> 本文件只列出每项检查的**机器证据读回位置**；verdict 必须由用户与 Codex 在",
        "> `review_decision.json` 中独立填写，实施方不得代填。",
        "",
        "| # | check_id | 人工问题 | 机器指标 | 读回产物 |",
        "|---|---|---|---|---|",
    ]
    for index, row in enumerate(matrix["rows"], start=1):
        lines.append(f"| {index} | `{row['check_id']}` | {row['review_question']} | "
                     f"{row['machine_metric']} | "
                     f"{', '.join('`' + a + '`' for a in row['artifact_refs'])} |")
    gates = matrix.get("machine_gates") or {}
    lines += [
        "",
        "## 机器门（由真实产物重算，非自报）",
        "",
        f"- `passed`: **{gates.get('passed')}**",
        f"- 失败门数: {gates.get('failed_gate_count')}",
        f"- 失败门: {', '.join('`' + g + '`' for g in gates.get('failed_gate_ids', [])) or '（无）'}",
        "",
        "> `passed=false` 时 `review_decision.machine_gates_passed` 必须为 `false`，",
        "> 且 `decision` 恒为 `needs_changes`。机器门**不能**代替人工 verdict。",
        "",
        "## §19.13 固定真实纵向样本（声明要求 + 派生裁决）",
        "",
        "| 能力 | 验收样本 | 定位（真实） | 派生裁决 | 失败原因 |",
        "|---|---|---|---|---|",
    ]
    for item in matrix["required_sample_matrix"]:
        locator = item["declared_requirement"].get("locator") or {}
        place = ", ".join(f"{k}={v}" for k, v in sorted(locator.items())
                          if v is not None) or "（文档级）"
        reasons = "; ".join(item["failure_reasons"]) or "—"
        lines.append(f"| {item['capability']} | {item['sample']} | `{place}` | "
                     f"**{item['status']}** | {_md_escape(reasons)} |")
    lines += ["", "| 能力 | 必须人工确认 |", "|---|---|"]
    for item in matrix["required_sample_matrix"]:
        lines.append(f"| {item['capability']} | {item['must_confirm']} |")
    return "\n".join(lines) + "\n"


def build_before_after(samples: list) -> str:
    """TS4 → TS5 的 component landing 迁移投影（§19.9.1 的"前后"读回）。"""
    lines = [
        "# TS4 → TS5：component landing 迁移（人读投影）",
        "",
        "> 本文件由内存中的 TS4 快照与 TS5 final snapshot **派生**；它回答「TS5 把原先的",
        "> component 分到了哪里」，不构成任何放行依据。",
        "",
    ]
    for sample in samples:
        ts4 = sample["ts4"]
        final = sample["final"]
        before = _histogram(c.landing for c in ts4.components)
        after = _histogram(b.component_landing for b in final.bindings)
        issued = _histogram(b.admission for b in final.bindings)
        lines.append(f"## {sample['document_key']}（{final.document_id}）")
        lines.append("")
        lines.append(f"- TS4 快照: `{ts4.snapshot_id}`（{len(ts4.components)} components / "
                     f"{len(ts4.dispositions)} dispositions / {len(ts4.spans)} spans）")
        lines.append(f"- TS5 快照: `{final.snapshot_id}`（{final.table_count} tables / "
                     f"{final.final_span_count} final spans / "
                     f"{len(final.bindings)} bindings / {final.blocking_gap_count} "
                     f"blocking gaps）")
        lines.append(f"- verifier: `{sample['verdict']}`"
                     + (f"（阻断缺口 {sample['refusal_blocking_kinds']}）"
                        if sample["refusal_blocking_kinds"] else ""))
        lines.append("")
        lines.append("| component landing | TS4 前 | TS5 后 |")
        lines.append("|---|---:|---:|")
        for landing in TS.COMPONENT_LANDINGS:
            lines.append(f"| `{landing}` | {before.get(landing, 0)} | "
                         f"{after.get(landing, 0)} |")
        lines.append(f"| **合计** | **{sum(before.values())}** | **{sum(after.values())}** |")
        lines.append("")
        lines.append(f"- TS5 admission 分布: `{issued}`")
        lines.append(f"- 守恒: "
                     + "; ".join(
                         f"{layer.layer_kind}="
                         + ("eligible" if TS.conservation_layer_eligible(
                             layer.layer_kind,
                             TS.conservation_layer_values(layer),
                             TS.conservation_term_value(layer.total),
                             layer.problems) else "INELIGIBLE")
                         for layer in final.conservation.layers))
        lines.append("")
    return "\n".join(lines) + "\n"


def build_table_range_decisions(samples: list) -> dict:
    return {
        "manual_review_required": True,
        "artifact": "table_range_decisions.json",
        "schema_version": V.TABLE_RANGE_DECISION_SCHEMA_VERSION,
        "scope": "§19.7.1：一条 provisional range 恰好一个 decision",
        "documents": [{
            "document_key": sample["document_key"],
            "document_id": sample["final"].document_id,
            "decision_count": len(sample["final"].decisions),
            "decisions": [_as_dict(d) for d in sample["final"].decisions],
        } for sample in samples],
    }


def build_component_binding_rows(samples: list) -> list:
    rows: list = []
    for sample in samples:
        for binding in sample["final"].bindings:
            row = _as_dict(binding)
            row["document_key"] = sample["document_key"]
            rows.append(row)
    rows.sort(key=lambda r: (r["document_key"], r["component_id"]))
    return rows


def build_relation_rows(samples: list) -> dict:
    documents = []
    for sample in samples:
        relations = []
        for relation in sample["final"].relations:
            # 端点 kind 已由 `TableRelation.to_dict()` 的 `source`/`target`
            # （各自含 `endpoint_kind`）承载，这里不再另加同义字段。
            relations.append(_as_dict(relation))
        documents.append({
            "document_key": sample["document_key"],
            "document_id": sample["final"].document_id,
            "relation_count": len(relations),
            "relation_kind_histogram": _histogram(r["relation_kind"] for r in relations),
            "relations": relations,
        })
    return {
        "manual_review_required": True,
        "artifact": "table_relations.json",
        "schema_version": V.TABLE_RELATION_SCHEMA_VERSION,
        "builder_version": V.TABLE_RELATION_BUILDER_VERSION,
        "allowed_relation_kinds": list(TS.TABLE_RELATION_KINDS),
        "future_only_relation_kinds": list(TS.FUTURE_ONLY_RELATION_KINDS),
        "endpoint_types": {kind: {"source": pair[0], "targets": list(pair[1])}
                           for kind, pair in TS.RELATION_ENDPOINT_TYPES.items()},
        "documents": documents,
    }


def build_candidate_audit_rows(samples: list) -> list:
    rows: list = []
    for sample in samples:
        for row in sample["audit"]:
            payload = _as_dict(row)
            payload["document_key"] = sample["document_key"]
            rows.append(payload)
    rows.sort(key=lambda r: (r["document_key"], r["page_number"], r["bbox"],
                             r["candidate_source"], r["strategy"], r["outcome"],
                             r["table_id"] or ""))
    return rows


def _cell_fragment_stats(sample: dict) -> dict:
    cells = 0
    multiline = 0
    merged = 0
    blocks = 0
    roles: dict = {}
    for table in sample["final"].tables:
        for row in table.rows:
            for cell in row.cells:
                cells += 1
                if "\n" in cell.text:
                    multiline += 1
                if cell.rowspan > 1 or cell.colspan > 1:
                    merged += 1
                if len(cell.blocks) > 1:
                    blocks += 1
                for block in cell.blocks:
                    roles[block.role] = roles.get(block.role, 0) + 1
    return {"cells": cells, "multiline_cells": multiline, "merged_cells": merged,
            "cells_with_multiple_blocks": blocks,
            "block_role_histogram": dict(sorted(roles.items()))}


def _continuation_stats(sample: dict) -> dict:
    anchors = 0
    candidates = 0
    relations = 0
    for table in sample["final"].tables:
        if table.continuation_anchor_locator:
            anchors += 1
        candidates += len(table.continuation_candidate_locators)
    for relation in sample["final"].relations:
        if relation.relation_kind == "continued_by":
            relations += 1
    return {"tables_with_anchor": anchors, "candidate_locators": candidates,
            "continued_by_relations": relations}


def _conservation_summary(sample: dict) -> dict:
    """四层守恒的读回投影：数值一律按**本层单位**取值，不用 `count` 冒充字符数。"""
    final = sample["final"]
    layers = {}
    for layer in final.conservation.layers:
        values = TS.conservation_layer_values(layer)
        total = TS.conservation_term_value(layer.total)
        layers[layer.layer_kind] = {
            "recorded_balanced": bool(layer.balanced),
            "recomputed_eligible": bool(TS.conservation_layer_eligible(
                layer.layer_kind, values, total, layer.problems)),
            "unit": TS.conservation_layer_unit(layer.layer_kind),
            "total": total,
            "sum_of_values": sum(values.values()),
            "problem_count": len(layer.problems),
            "problems": list(layer.problems),
            "terms": dict(values),
        }
    return {
        "layers": layers,
        "problem_count": len(final.conservation.problems),
        "all_balanced": all(v["recorded_balanced"] for v in layers.values()),
        "conservation_eligible": bool(TS.conservation_eligible(final.conservation)),
    }


def _decorate(samples: list) -> list:
    """给每个样本挂上生成"读回投影"所需的派生统计（只在内存里）。"""
    out = []
    for sample in samples:
        row = dict(sample)
        row["audit_outcome_histogram"] = _histogram(r.outcome for r in sample["audit"])
        row["audit_reason_histogram"] = _histogram(
            r.reason for r in sample["audit"] if r.reason)
        row["structure_kind_histogram"] = _histogram(
            t.structure_kind for t in sample["final"].tables)
        row["structure_class_histogram"] = _histogram(
            t.structure_class for t in sample["final"].tables)
        row["range_kind_histogram"] = _histogram(
            d.range_kind for d in sample["final"].decisions)
        row["relation_kind_histogram"] = _histogram(
            r.relation_kind for r in sample["final"].relations)
        row["gap_kind_histogram"] = _histogram(
            e.gap_kind for e in sample["final"].gaps.entries)
        row["cell_fragment_stats"] = _cell_fragment_stats(sample)
        row["block_role_histogram"] = row["cell_fragment_stats"]["block_role_histogram"]
        row["continuation_stats"] = _continuation_stats(sample)
        row["conservation_summary"] = _conservation_summary(sample)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 6. 落盘：create-only、索引、核验
# ---------------------------------------------------------------------------

def _json_members(samples: list, *, run_id: str, results_dir: pathlib.Path,
                  machine_gates: dict | None = None) -> dict:
    return {
        "table_objects.json": build_table_objects(
            samples, document_order=[s["document_key"] for s in samples]),
        "table_range_decisions.json": build_table_range_decisions(samples),
        "table_relations.json": build_relation_rows(samples),
        "table_terminal_ledger.json": build_terminal_ledger(samples),
        "table_citable_coverage.json": build_citable_coverage(samples),
        "final_spans.json": build_final_spans(samples),
        "final_navigation_synopsis.json": build_final_navigation_synopsis(samples),
        "table_gaps.json": build_table_gaps(samples),
        "visual_object_gaps.json": build_visual_object_gaps(samples),
        "final_conservation.json": build_final_conservation(samples),
        "authority_separation.json": build_authority_separation(samples),
        "final_snapshot_index.json": build_final_snapshot_index(
            samples, results_dir=results_dir),
        "table_acceptance_matrix.json": build_acceptance_matrix(
            samples, run_id=run_id, machine_gates=machine_gates),
    }


def _md_members(samples: list, *, matrix: dict) -> dict:
    return {
        "table_objects.md": build_table_objects_md(samples),
        "table_preview.md": build_table_preview_md(samples),
        "table_acceptance_matrix.md": build_acceptance_matrix_md(matrix),
        "before_after.md": build_before_after(samples),
    }


def _jsonl_members(samples: list) -> dict:
    return {
        "table_candidate_audit.jsonl": build_candidate_audit_rows(samples),
        "table_cell_fragments.jsonl": build_table_cell_fragments(samples),
        "final_component_bindings.jsonl": build_component_binding_rows(samples),
    }


def _member_role(relpath: str) -> str:
    role = MEMBER_ROLES.get(relpath)
    if role is None:
        if relpath.startswith(MATRIX_SNAPSHOT_DIRNAME + "/"):
            return (f"final_material_structure_snapshot@"
                    f"{V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION}/"
                    f"{V.FINAL_MATERIAL_BUILDER_VERSION}")
        raise StepFailure(0, f"机器产物 {relpath!r} 没有登记 role（fail-closed）",
                          field="MEMBER_ROLES")
    return role


def _snapshot_of(sample: dict) -> dict:
    payload = sample["final"].to_dict()
    if not isinstance(payload, dict):  # pragma: no cover - schema 保证
        raise StepFailure(0, "FinalMaterialStructureSnapshot.to_dict() 必须返回对象")
    return payload


def build_machine_index(*, run_id: str, run_dir_relpath: str, document_order: Sequence[str],
                        results_dir: pathlib.Path) -> dict:
    """`machine_artifact_index.json`（`tai-1`）：逐文件 sha256/size/role。

    索引与核验自身**不在**登记范围内：一个文件不可能包含自己的 sha256（沿用 TS3 冻结
    索引的同一约定）。二者的内容由 `--validate-only` 重新解析、由 `tra-1` attestation
    绑定 sha256 来约束。
    """
    files = []
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(results_dir).as_posix()
        if rel in EXCLUDED_FROM_MACHINE_INDEX:
            continue
        files.append({"path": rel, "sha256": sha256_file(path),
                      "size": path.stat().st_size, "role": _member_role(rel)})
    members = list(SCH.indexable_members(document_order))
    if [row["path"] for row in files] != members:
        raise StepFailure(
            0, f"磁盘上的可索引文件集合与 `tai-1` 成员集合不一致："
               f"缺 {sorted(set(members) - {r['path'] for r in files})} / "
               f"多 {sorted({r['path'] for r in files} - set(members))}（fail-closed）",
            field="machine_artifact_index.members")
    return {
        "schema_type": "TS5MachineArtifactIndex",
        "artifact_index_schema_version": SCH.TS5_ARTIFACT_INDEX_SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir_relpath": run_dir_relpath,
        "members_expected": members,
        "index_internal_members": list(SCH.INDEX_INTERNAL_MEMBERS),
        "files": files,
    }


def verify_machine_index(results_dir: pathlib.Path, index: dict,
                         document_order: Sequence[str]) -> dict:
    """按索引**独立重算**（缺文件 / 多文件 / 错 hash），**只读**、不写任何东西。"""
    listed = {row["path"]: row for row in index.get("files", [])}
    missing: list = []
    extra: list = []
    hash_mismatch: list = []
    scan_count = 0
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(results_dir).as_posix()
        if rel in EXCLUDED_FROM_MACHINE_INDEX:
            continue
        scan_count += 1
        row = listed.get(rel)
        if row is None:
            extra.append({"path": rel, "size": path.stat().st_size,
                          "sha256": sha256_file(path)})
            continue
        digest = sha256_file(path)
        if row.get("size") != path.stat().st_size or row.get("sha256") != digest:
            hash_mismatch.append({"path": rel, "index_sha256": row.get("sha256"),
                                  "actual_sha256": digest,
                                  "index_size": row.get("size"),
                                  "actual_size": path.stat().st_size})
    for rel in sorted(listed):
        if not (results_dir / rel).is_file():
            missing.append({"path": rel, "index_sha256": listed[rel].get("sha256")})
    expected = list(SCH.indexable_members(document_order))
    ok = (not missing and not extra and not hash_mismatch
          and scan_count == len(listed) == len(expected)
          and list(listed) == sorted(listed))
    return {
        "schema_type": "TS5MachineArtifactIndexCheck",
        "artifact_index_schema_version": SCH.TS5_ARTIFACT_INDEX_SCHEMA_VERSION,
        "run_id": index.get("run_id"),
        "run_dir_relpath": index.get("run_dir_relpath"),
        "members_recomputed": expected,
        "index_internal_members": list(SCH.INDEX_INTERNAL_MEMBERS),
        "not_scanned_by_index": sorted(SCH.NOT_SCANNED_BY_INDEX),
        "scan_file_count": scan_count,
        "index_file_count": len(listed),
        "missing": missing,
        "extra": extra,
        "hash_mismatch": hash_mismatch,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# 7. run manifest（current `trm-2`）与 review 模块（`trv-1` / `tra-1`）
# ---------------------------------------------------------------------------

def _run_identity(run_id: str, run_id_source: str, generated_at: str) -> dict:
    return {"run_id": run_id, "run_id_source": run_id_source,
            "generated_at_utc": generated_at, "acceptance_runner_version":
                SCH.TS5_ACCEPTANCE_RUNNER_VERSION}


def run_id_identity(run_id: str, *, source: str) -> dict:
    return R.run_id_identity(run_id, source=source)


def build_run_manifest(*, inputs: Inputs, samples: list, run_id: str,
                       run_id_source: str, run_dir_relpath: str,
                       generated_at: str) -> dict:
    trust_root = inputs.trust_root
    roots = []
    documents = []
    for sample in samples:
        entry = sample["entry"]
        final = sample["final"]
        wrapper = sample["wrapper"]
        roots.append({
            "document_key": sample["document_key"],
            "document_id": final.document_id,
            "source_kind": entry["source_kind"],
            "scope": entry["scope"],
            "upstream_dependency_fingerprint": final.upstream_dependency_fingerprint,
            "verified_span_snapshot_id": final.verified_span_snapshot_id,
            "final_snapshot_locator": final.snapshot_locator,
            "final_snapshot_id": final.snapshot_id,
            "final_content_fingerprint": final.content_fingerprint,
            "verifier_verdict": sample["verdict"],
            "verifier_verification_fingerprint":
                wrapper.verification_fingerprint if wrapper is not None else None,
            # 拒发的**原因**（封闭词表）。`issued` 时为空；`refused` 时非空，且
            # "结构缺口 ⇒ 带阻断性缺口种类 / 纯守恒拒发 ⇒ 不带"是充要关系（由
            # `trm-2` 的 schema 校验强制）。
            "refusal_reason_codes": list(sample["refusal_reason_codes"]),
            "refusal_blocking_kinds": list(sample["refusal_blocking_kinds"]),
            "capability_issued": wrapper is not None,
        })
        documents.append({
            "document_key": sample["document_key"],
            "document_id": final.document_id,
            "scope": entry["scope"],
            "source_kind": entry["source_kind"],
            "verifier_verdict": sample["verdict"],
            "capability_issued": wrapper is not None,
            "table_count": final.table_count,
            "final_span_count": final.final_span_count,
            "decision_count": len(final.decisions),
            "relation_count": len(final.relations),
            "binding_count": len(final.bindings),
            "coverage_count": len(final.coverages),
            "synopsis_count": len(final.synopses),
            "blocking_gap_count": final.blocking_gap_count,
            # capability 轴：拒发 ⇒ True（final capability 不可供下游使用）。
            # 结构缺口轴见 `blocking_gap_count` 与上游根的 refusal 字段。
            "document_blocked": wrapper is None,
            # 守恒资格是**重算**出来的（算术守恒 + 逐层 problems 为空 +
            # `residual_gap` 为 0），不是把各层自报的 `balanced` 相加。
            "conservation_balanced": bool(TS.conservation_eligible(final.conservation)),
        })
    document_order = [sample["document_key"] for sample in samples]
    issued = sum(1 for doc in documents if doc["capability_issued"])
    blocked = len(documents) - issued
    manifest = {
        "schema_type": "TS5RunManifest",
        "run_manifest_schema_version": SCH.TS5_RUN_MANIFEST_SCHEMA_VERSION,
        "acceptance_runner_version": SCH.TS5_ACCEPTANCE_RUNNER_VERSION,
        "artifact_index_schema_version": SCH.TS5_ARTIFACT_INDEX_SCHEMA_VERSION,
        "review_schema_version": SCH.TS5_REVIEW_SCHEMA_VERSION,
        "review_attestation_schema_version": SCH.TS5_REVIEW_ATTESTATION_SCHEMA_VERSION,
        "trust_root_schema_version": SCH.TS5_TRUST_ROOT_SCHEMA_VERSION,
        "versions": dict(SCH.EVALUATION_VERSION_AXES),
        "run_id": run_id,
        "run_id_source": run_id_source,
        "run_dir_relpath": run_dir_relpath,
        "generated_at_utc": generated_at,
        "command_mode": "run",
        "create_only": True,
        "manual_review_required": True,
        "trust_root": {
            "relpath": TRUST_ROOT_RELPATH,
            "file_sha256": trust_root["trust_root_file_sha256"],
            "content_fingerprint": trust_root["trust_root_content_fingerprint"],
            "ts4_trust_root_relpath": trust_root["ts4_trust_root_relpath"],
            "ts4_trust_root_file_sha256": trust_root["ts4_trust_root_file_sha256"],
            "ts4_frozen_run_relpath": trust_root["ts4_frozen_run_relpath"],
        },
        "upstream_roots": roots,
        "documents": documents,
        "machine_artifact_members": list(SCH.expected_machine_members(document_order)),
        "human_template_members": list(SCH.HUMAN_TEMPLATE_MEMBERS),
        "expected_blocking_summary": {
            "documents_blocked": blocked,
            "documents_capability_issued": issued,
            "total_blocking_gaps": sum(doc["blocking_gap_count"] for doc in documents),
        },
    }
    return manifest


def build_review_template(*, run_id: str, final_snapshot_ids: Sequence[str],
                          machine_gates_passed: bool = False) -> dict:
    """人工模板：逐项 verdict 全空，`machine_gates_passed` 取**重算**结果。

    该字段只影响 `derive_review_decision` 的下界：`passed=false` 时 `decision` 恒为
    `needs_changes`；`passed=true` 时因为逐项仍是 `not_reviewed`，`decision` 仍然是
    `needs_changes`。两种情况下实施方都不能代填任何 verdict。
    """
    return SCH.empty_review_decision(run_id=run_id,
                                     final_snapshot_ids=final_snapshot_ids,
                                     machine_gates_passed=machine_gates_passed)


def build_review_manual_md(*, inputs: Inputs, samples: list, manifest: dict,
                           run_id: str, generated_at: str) -> str:
    """§19.14.2 的**空白**人工验收模板（人读投影）。实施方不得代填任何 verdict。"""
    lines = [
        "# TS5 人工验收（空白模板，未裁决）",
        "",
        "> **本文件不是验收结论。** 实施方只生成空白模板：全部 check 都是",
        "> `not_reviewed`，`decision` 派生为 `needs_changes`。唯一授权文件是同目录的",
        "> `review_decision.json`；填写后由 `--seal-review` 按 `tra-1` 封存为",
        "> `review_attestation.json`。",
        "",
        f"- run_id: `{run_id}`",
        f"- generated_at_utc: `{generated_at}`",
        f"- acceptance runner: `{SCH.TS5_ACCEPTANCE_RUNNER_VERSION}`",
        f"- trust root: `{TRUST_ROOT_RELPATH}` "
        f"file=`{inputs.trust_root['trust_root_file_sha256'][:16]}…` "
        f"content=`{inputs.trust_root['trust_root_content_fingerprint'][:16]}…`",
        f"- 上游冻结 run: `{inputs.trust_root['ts4_frozen_run_relpath']}`",
        f"- 上游 TS4 信任根: `{inputs.trust_root['ts4_trust_root_relpath']}`",
        "",
        "## 逐文档机器结果（**不是**人工裁决）",
        "",
        "| document_key | scope | TS4 snapshot | TS5 snapshot | tables | final spans | "
        "blocking gaps | verifier |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for sample in samples:
        final = sample["final"]
        lines.append(
            f"| `{sample['document_key']}` | `{sample['entry']['scope']}` | "
            f"`{sample['ts4'].snapshot_id}` | `{final.snapshot_id}` | "
            f"{final.table_count} | {final.final_span_count} | "
            f"{final.blocking_gap_count} | `{sample['verdict']}` |")
    lines += [
        "",
        "## 10 项人工检查（每项必须由 user 与 codex 各自给出 verdict）",
        "",
    ]
    for index, (check_id, question) in enumerate(
            zip(SCH.REQUIRED_REVIEW_CHECK_IDS, REVIEW_QUESTIONS), start=1):
        lines.append(f"{index}. `{check_id}` — {question}")
    lines += [
        "",
        "## 允许的取值",
        "",
        f"- `verdict ∈ {list(SCH.REVIEW_VERDICTS)}`（`not_reviewed` 视为**未完成**）",
        f"- `decision ∈ {list(SCH.REVIEW_DECISIONS)}`，且必须与派生值一致",
        "- `approve` 的必要且充分条件：机器门全过 + 两角色对 10 项全部 `pass`",
        "",
        "## 机器门（本 run 自证部分）",
        "",
        f"- `upstream_roots` 全部签发: "
        f"{sum(1 for r in manifest['upstream_roots'] if r['capability_issued'])}/"
        f"{len(manifest['upstream_roots'])}",
        f"- 阻断性缺口合计: {manifest['expected_blocking_summary']['total_blocking_gaps']}",
        "",
        "## 本批交付方**未**做的声明",
        "",
        "- 未提交 TS5 实现，未进入 TS6/TS7/R3，未生成报告正文；",
        "- 未宣布 TS5 关闭；未代替用户或 Codex 填写任何 verdict。",
        "",
    ]
    return "\n".join(lines) + "\n"


def review_attestation_identity(*, inputs: Inputs, manifest: dict, results_dir: pathlib.Path,
                                samples: list) -> dict:
    fingerprints = {sample["document_key"]: sample["final"].content_fingerprint
                    for sample in samples}
    return SCH.review_attestation_identity(
        trust_root_file_sha256=inputs.trust_root["trust_root_file_sha256"],
        trust_root_content_fingerprint=inputs.trust_root[
            "trust_root_content_fingerprint"],
        run_manifest_sha256=sha256_file(results_dir / "run_manifest.json"),
        artifact_index_sha256=sha256_file(results_dir / "machine_artifact_index.json"),
        artifact_index_check_sha256=sha256_file(
            results_dir / "machine_artifact_index_check.json"),
        review_decision_sha256=sha256_file(results_dir / "review_decision.json"),
        runner_version=SCH.TS5_ACCEPTANCE_RUNNER_VERSION,
        issuer_version=FV.VERIFIER_VERSION,
        final_fingerprints=fingerprints)


def seal_review(target: pathlib.Path) -> int:
    """§19.14.2-4：复核通过后 **create-only** 追加 `review_attestation.json`。"""
    run_dir = pathlib.Path(target)
    if not run_dir.is_dir():
        raise StepFailure(0, f"结果目录不存在：{run_dir}（fail-closed）",
                          field="run_dir")
    attestation_path = run_dir / "review_attestation.json"
    if attestation_path.exists():
        raise StepFailure(0, f"该 run 已存在 review_attestation.json：{attestation_path}；"
                             f"重复 seal 一律拒绝，不得覆盖后重签同一 run（fail-closed）",
                          field="review_attestation.json")
    manifest = _read_json(run_dir / "run_manifest.json", "run_manifest.json")
    # 1) 信任根与上游：完整重跑一遍绑定（含 TS4 上游 pin 与逐文档期望值）。
    #    `document_order` 取自**信任锚**而非 manifest 自身的 upstream_roots——否则
    #    "少收一份文档"的 manifest 会用自己的根集自证一致（循环依赖，§19.15.3）。
    inputs = bind_inputs()
    document_order = list(inputs.trust_root["document_order"])
    try:
        SCH.validate_run_manifest(manifest, document_order=document_order)
    except SCH.AcceptanceSchemaError as e:
        raise StepFailure(
            0, f"run_manifest 不符合 `{SCH.TS5_RUN_MANIFEST_SCHEMA_VERSION}`：{e}"
               f"（fail-closed）", field="run_manifest") from e
    run_id = manifest["run_id"]
    if inputs.trust_root["trust_root_file_sha256"] \
            != manifest["trust_root"]["file_sha256"]:
        raise StepFailure(0, "信任根文件哈希与 run_manifest 记录不一致（fail-closed）",
                          field="trust_root.file_sha256")
    # 2) 逐文档**重新构建并复核**：tamper、上游漂移与 verifier 回归都会在此暴露。
    samples = _decorate(build_all_documents(inputs))
    _assert_manifest_agrees(manifest, samples)
    # 3) 索引与 DB：重算，不信任磁盘上的自报值。
    index = _read_json(run_dir / "machine_artifact_index.json",
                       "machine_artifact_index.json")
    try:
        SCH.validate_artifact_index(index, document_order=document_order)
    except SCH.AcceptanceSchemaError as e:
        raise StepFailure(0, f"machine_artifact_index 不符合 `tai-1`：{e}"
                             f"（fail-closed）", field="machine_artifact_index") from e
    check = verify_machine_index(run_dir, index, document_order)
    if not check["ok"]:
        raise StepFailure(
            0, f"机器产物索引核验失败：missing={check['missing'][:3]} "
               f"extra={check['extra'][:3]} hash_mismatch={check['hash_mismatch'][:3]}"
               f"（fail-closed）", field="machine_artifact_index")
    immutability = _read_json(run_dir / "database_immutability.json",
                              "database_immutability.json")
    if not immutability.get("unchanged"):
        raise StepFailure(0, "database_immutability 记录的不是「未变化」（fail-closed）",
                          field="database_immutability")
    # 4) review schema：decision 必须由逐项 verdict 派生，且不得自报。
    decision = _read_json(run_dir / "review_decision.json", "review_decision.json")
    final_snapshot_ids = [sample["final"].snapshot_id for sample in samples]
    try:
        SCH.validate_review_decision(decision, run_id=run_id,
                                     final_snapshot_ids=final_snapshot_ids)
    except SCH.AcceptanceSchemaError as e:
        raise StepFailure(0, f"review_decision 不符合 `trv-1`：{e}（fail-closed）",
                          field="review_decision") from e
    identity = review_attestation_identity(inputs=inputs, manifest=manifest,
                                           results_dir=run_dir, samples=samples)
    attestation = {
        "schema_type": "TS5ReviewAttestation",
        "review_attestation_schema_version":
            SCH.TS5_REVIEW_ATTESTATION_SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir_relpath": manifest["run_dir_relpath"],
        "sealed_at_utc": _utc_now(),
        "identity": identity,
        "conclusions": {
            "trust_root_rechecked": True,
            "review_decision_schema_ok": True,
            "artifact_index_recomputed_ok": True,
            "final_verifier_ok": all(sample["wrapper"] is not None
                                     or sample["verdict"] == "refused"
                                     for sample in samples),
            "database_immutability_ok": bool(immutability.get("unchanged")),
            "machine_gates_passed": bool(decision["machine_gates_passed"]),
        },
    }
    try:
        SCH.validate_review_attestation(attestation, run_id=run_id,
                                        run_dir_relpath=manifest["run_dir_relpath"])
    except SCH.AcceptanceSchemaError as e:  # pragma: no cover - 自造对象必须自洽
        raise StepFailure(0, f"生成的 attestation 不符合 `tra-1`：{e}",
                          field="review_attestation") from e
    _write_json(attestation_path, attestation)
    print(json.dumps({"sealed": str(attestation_path), "run_id": run_id,
                      "decision": decision["decision"],
                      "review_schema_version": SCH.TS5_REVIEW_SCHEMA_VERSION},
                     ensure_ascii=False, indent=2))
    return 0


def _assert_manifest_agrees(manifest: dict, samples: list) -> None:
    """重建结果必须与 `run_manifest.json` 逐项一致（不许"文件说一套、链跑一套"）。"""
    by_key = {sample["document_key"]: sample for sample in samples}
    for root in manifest["upstream_roots"]:
        sample = by_key[root["document_key"]]
        final = sample["final"]
        pairs = (
            ("final_snapshot_id", root["final_snapshot_id"], final.snapshot_id),
            ("final_content_fingerprint", root["final_content_fingerprint"],
             final.content_fingerprint),
            ("verified_span_snapshot_id", root["verified_span_snapshot_id"],
             final.verified_span_snapshot_id),
            ("upstream_dependency_fingerprint",
             root["upstream_dependency_fingerprint"],
             final.upstream_dependency_fingerprint),
            ("verifier_verdict", root["verifier_verdict"], sample["verdict"]),
            ("refusal_reason_codes", root["refusal_reason_codes"],
             sample["refusal_reason_codes"]),
            ("refusal_blocking_kinds", root["refusal_blocking_kinds"],
             sample["refusal_blocking_kinds"]),
            ("capability_issued", root["capability_issued"],
             sample["wrapper"] is not None),
        )
        for field, declared, actual in pairs:
            if declared != actual:
                raise StepFailure(
                    0, f"{root['document_key']} 的 run_manifest.{field} 与本次重建不符："
                       f"{declared!r} -> {actual!r}（fail-closed）",
                    document_id=final.document_id, field=field)


# ---------------------------------------------------------------------------
# 8. run 执行 / validate-only
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_run_id() -> str:
    return SCH.RUN_DIR_PREFIX.rstrip("_") + "_" + datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def execute_run(*, run_id: str | None, results_root: pathlib.Path,
                generated_at: str) -> int:
    """跑一次完整 TS5 验收；失败时不留半成品目录。**绝不覆盖**历史目录。"""
    # 受监管库的**运行前**身份：必须在任何绑定 / 构建之前取，否则"前后相等"是同义反复。
    db_before = R.snapshot(R._dump_watched_dbs())
    code = code_fingerprint()
    if code["missing"]:
        raise StepFailure(
            0, f"代码指纹文件缺失（`CODE_FINGERPRINT_FILES` 必须逐文件可读）："
               f"{code['missing']}（fail-closed）", field="code_fingerprint")
    run_id_source = "generated" if run_id is None else "explicit"
    final_run_id = run_id or _default_run_id()
    if not re.match(r"^[A-Za-z0-9_.-]{4,120}$", final_run_id):
        raise StepFailure(0, f"run_id 形状非法：{final_run_id!r}（fail-closed）",
                          field="run_id")
    if not final_run_id.startswith(SCH.RUN_DIR_PREFIX):
        raise StepFailure(0, f"run_id 必须以 {SCH.RUN_DIR_PREFIX!r} 开头（fail-closed）",
                          field="run_id")
    results_dir = results_root / final_run_id
    if results_dir.exists():
        raise StepFailure(0, f"结果目录已存在，create-only 拒绝覆盖：{results_dir}"
                             f"（fail-closed）", field="run_dir")
    run_dir_relpath = results_dir.relative_to(REPO_ROOT).as_posix() \
        if results_dir.is_relative_to(REPO_ROOT) else str(results_dir)

    inputs = bind_inputs()
    if inputs.trust_root["document_order"] != list(
            sorted(inputs.trust_root["document_order"])):
        raise StepFailure(0, "信任锚的 document_order 必须为升序固定序（fail-closed）",
                          field="document_order")
    samples = _decorate(build_all_documents(inputs))
    created = False
    try:
        results_dir.mkdir(parents=True)
        created = True
        # —— 版本束 / 输入根 / 身份（先算后写；任何自报值都不参与）。
        manifest = build_run_manifest(
            inputs=inputs, samples=samples, run_id=final_run_id,
            run_id_source=run_id_source, run_dir_relpath=run_dir_relpath,
            generated_at=generated_at)
        try:
            SCH.validate_run_manifest(
                manifest, document_order=[s["document_key"] for s in samples])
        except SCH.AcceptanceSchemaError as e:  # pragma: no cover - 自造对象必须自洽
            raise StepFailure(
                0, f"生成的 run_manifest 不符合 "
                   f"`{SCH.TS5_RUN_MANIFEST_SCHEMA_VERSION}`：{e}",
                field="run_manifest") from e

        # —— final 快照**先落盘并读回**：`final_snapshot_index.json` 的
        #    `file_sha256` 按定义是**磁盘上那份快照文件**的哈希，所以它必须排在
        #    `_json_members`（含该索引）之前。顺序写反时索引会记下 `null`，而
        #    `validate_only` 在文件已存在的前提下重算得到真实哈希，逐字节比较必然
        #    判成"内容 tamper"——产物自己的字段与自己比不出等于。
        for sample in samples:
            path = results_dir / MATRIX_SNAPSHOT_DIRNAME \
                / f"{sample['document_key']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = _snapshot_of(sample)
            _write_json(path, payload)
            written = _read_json(path, "刚写入的 final material 快照")
            if canonical_json(written) != canonical_json(payload):
                raise StepFailure(0, f"{sample['document_key']} 的 final 快照读回与 "
                                     f"canonical JSON 不相等（fail-closed）",
                                  document_id=sample["final"].document_id,
                                  field="final_snapshot_readback")
            recomputed = TS.FinalMaterialStructureSnapshot.from_dict(written)
            if recomputed.content_fingerprint != sample["final"].content_fingerprint:
                raise StepFailure(0, f"{sample['document_key']} 读回的 final 快照 "
                                     f"content_fingerprint 不可复现（fail-closed）",
                                  document_id=sample["final"].document_id,
                                  field="content_fingerprint")

        # —— 机器门：必须**先于**索引与人工模板算出来，因为
        #    `table_acceptance_matrix.json` 要带上它、`review_decision.json` 的
        #    `machine_gates_passed` 取的就是这份重算结果（不是自报值）。
        #    受监管库的**运行后**身份在这里先取：所有绑定/构建/复核都已走完，
        #    之后的落盘不碰任何库，所以此处即终态。
        db_after = R.snapshot(R._dump_watched_dbs())
        immutability = R.build_database_immutability(before=db_before, after=db_after)
        if not immutability["unchanged"]:
            raise StepFailure(
                0, f"运行前后受监管库身份发生变化（P0，fail-closed）："
                   f"changed={immutability['changed']}；"
                   f"watched_missing={immutability['watched_missing']}",
                field="database_immutability")
        machine_gates = build_machine_gates(samples=samples,
                                            immutability=immutability)
        _write_json(results_dir / "database_immutability.json", immutability)

        # —— §19.14.1 的机器产物（除索引/核验/run_manifest 外先写）。
        json_members = _json_members(samples, run_id=final_run_id,
                                     results_dir=results_dir,
                                     machine_gates=machine_gates)
        # 上面的顺序是**前提**而不是巧合：取不到文件就 fail-closed，不让 `null`
        # 混进产物、更不留到只读回读时才以别的名义暴露。
        hashless = [entry["relpath"]
                    for entry in json_members["final_snapshot_index.json"]["snapshots"]
                    if not entry["file_sha256"]]
        if hashless:
            raise StepFailure(0, f"final_snapshot_index 取不到快照文件的 sha256："
                                 f"{hashless[:3]}（快照必须先于该索引落盘，"
                                 f"fail-closed）", field="final_snapshot_index")
        md_members = _md_members(samples, matrix=json_members["table_acceptance_matrix.json"])
        jsonl_members = _jsonl_members(samples)
        for relpath, payload in sorted(json_members.items()):
            _write_json(results_dir / relpath, payload)
        for relpath, text in sorted(md_members.items()):
            (results_dir / relpath).write_text(text, encoding="utf-8")
        for relpath, rows in sorted(jsonl_members.items()):
            _write_json_lines(results_dir / relpath, rows)

        # —— 受监管库的**二次**运行后身份：上面那次是为了算机器门而取的（覆盖
        #    绑定/构建/复核），这一次覆盖**投影写盘**阶段。两次都必须与运行前逐字段
        #    相等；只查一次会让"写盘路径碰了库"落在检查之外。
        db_after = R.snapshot(R._dump_watched_dbs())
        immutability = R.build_database_immutability(before=db_before, after=db_after)
        if not immutability["unchanged"]:
            raise StepFailure(
                0, f"运行前后受监管库身份发生变化（P0，fail-closed）："
                   f"changed={immutability['changed']}；"
                   f"watched_missing={immutability['watched_missing']}",
                field="database_immutability")
        _write_json(results_dir / "database_immutability.json", immutability)

        # —— 人工模板：**空白**，且**不进**机器索引。必须在索引扫描前落盘。
        (results_dir / "manual_review.md").write_text(
            build_review_manual_md(inputs=inputs, samples=samples, manifest=manifest,
                                   run_id=final_run_id, generated_at=generated_at),
            encoding="utf-8")
        _write_json(results_dir / "review_decision.json",
                    build_review_template(
                        run_id=final_run_id,
                        final_snapshot_ids=[s["final"].snapshot_id for s in samples],
                        machine_gates_passed=bool(machine_gates["passed"])))

        # —— run_manifest 在模板之后写：索引要登记它的 sha256。
        _write_json(results_dir / "run_manifest.json", manifest)
        index = build_machine_index(
            run_id=final_run_id, run_dir_relpath=run_dir_relpath,
            document_order=[s["document_key"] for s in samples],
            results_dir=results_dir)
        try:
            SCH.validate_artifact_index(
                index, document_order=[s["document_key"] for s in samples])
        except SCH.AcceptanceSchemaError as e:  # pragma: no cover
            raise StepFailure(0, f"生成的 machine_artifact_index 不符合 `tai-1`：{e}",
                              field="machine_artifact_index") from e
        _write_json(results_dir / "machine_artifact_index.json", index)
        check = verify_machine_index(results_dir, index,
                                     [s["document_key"] for s in samples])
        try:
            SCH.validate_artifact_index_check(
                check, document_order=[s["document_key"] for s in samples])
        except SCH.AcceptanceSchemaError as e:  # pragma: no cover
            raise StepFailure(0, f"生成的 machine_artifact_index_check 不符合 "
                                 f"`tai-1`：{e}", field="machine_artifact_index_check") from e
        _write_json(results_dir / "machine_artifact_index_check.json", check)
        if not check["ok"]:
            raise StepFailure(0, f"机器产物索引核验失败：missing={check['missing'][:3]} "
                                 f"extra={check['extra'][:3]} "
                                 f"hash_mismatch={check['hash_mismatch'][:3]}"
                                 f"（fail-closed）", field="machine_artifact_index")
        print(json.dumps({
            "run_id": final_run_id,
            "run_dir_relpath": run_dir_relpath,
            "documents": [{"document_key": d["document_key"],
                           "verifier_verdict": d["verifier_verdict"],
                           "table_count": d["table_count"],
                           "final_span_count": d["final_span_count"],
                           "blocking_gap_count": d["blocking_gap_count"]}
                          for d in manifest["documents"]],
            "machine_artifact_member_count":
                len(manifest["machine_artifact_members"]),
            "indexed_file_count": len(index["files"]),
            "manual_review_required": True,
        }, ensure_ascii=False, indent=2))
        return 0
    except BaseException:
        if created and not (results_dir / "review_attestation.json").exists():
            shutil.rmtree(results_dir, ignore_errors=True)
        raise


def validate_only(target: pathlib.Path) -> int:
    """只读复核一个已存在的 run：schema、成员集合、逐文件 hash、上游与重建等价。"""
    run_dir = pathlib.Path(target)
    if not run_dir.is_dir():
        raise StepFailure(0, f"结果目录不存在：{run_dir}（fail-closed）", field="run_dir")
    problems: list = []

    # 1) 信任根与上游：完整重跑绑定（含 TS4 上游 pin 与逐文档期望值）。
    #    `document_order` 取自**信任锚**——不以 manifest 自报的根集为基准，否则
    #    "少收一份文档"的 manifest 会自我一致（循环依赖，§19.15.3）。
    inputs = bind_inputs()
    order: list = list(inputs.trust_root["document_order"])

    # 2) run_manifest 与其声明的身份。
    manifest = _read_json(run_dir / "run_manifest.json", "run_manifest.json")
    try:
        SCH.validate_run_manifest(manifest, document_order=order)
    except SCH.AcceptanceSchemaError as e:
        problems.append(f"run_manifest 不符合 "
                        f"`{SCH.TS5_RUN_MANIFEST_SCHEMA_VERSION}`：{e}")
    for field, declared in (
            ("file_sha256", inputs.trust_root["trust_root_file_sha256"]),
            ("content_fingerprint",
             inputs.trust_root["trust_root_content_fingerprint"]),
            ("ts4_trust_root_relpath", inputs.trust_root["ts4_trust_root_relpath"]),
            ("ts4_trust_root_file_sha256",
             inputs.trust_root["ts4_trust_root_file_sha256"]),
            ("ts4_frozen_run_relpath", inputs.trust_root["ts4_frozen_run_relpath"])):
        if manifest.get("trust_root", {}).get(field) != declared:
            problems.append(f"run_manifest.trust_root.{field} 与实际信任根不一致："
                            f"{manifest.get('trust_root', {}).get(field)!r} -> "
                            f"{declared!r}")

    # 3) 逐文档重建并复核：这条链与 `execute_run` **同源**，因此"文件说一套、
    #    实际跑另一套"会被逐字段比较发现。
    samples = _decorate(build_all_documents(inputs))
    try:
        _assert_manifest_agrees(manifest, samples)
    except StepFailure as e:
        problems.append(f"重建结果与 run_manifest 不符：{e.report()}")
    if [s["document_key"] for s in samples] != list(order):
        problems.append(f"重建的文档序 {[s['document_key'] for s in samples]} 与 "
                        f"run_manifest 的 {order} 不一致")

    # 4) 索引：重算成员集合与逐文件 hash（缺 / 多 / 改都算）。
    index = _read_json(run_dir / "machine_artifact_index.json",
                       "machine_artifact_index.json")
    try:
        SCH.validate_artifact_index(index, document_order=order)
    except SCH.AcceptanceSchemaError as e:
        problems.append(f"machine_artifact_index 不符合 `tai-1`：{e}")
    check = verify_machine_index(run_dir, index, order)
    if not check["ok"]:
        problems.append(f"machine_artifact_index 核验失败：missing="
                        f"{[row['path'] for row in check['missing']][:5]} extra="
                        f"{[row['path'] for row in check['extra']][:5]} "
                        f"hash_mismatch="
                        f"{[row['path'] for row in check['hash_mismatch']][:5]}")
    stored_check = _read_json(run_dir / "machine_artifact_index_check.json",
                              "machine_artifact_index_check.json")
    try:
        SCH.validate_artifact_index_check(stored_check, document_order=order)
    except SCH.AcceptanceSchemaError as e:
        problems.append(f"machine_artifact_index_check 不符合 `tai-1`：{e}")
    if stored_check != check:
        problems.append("磁盘上的 machine_artifact_index_check.json 与本次重算不等"
                        "（自报不作数）")

    # 5) 逐产物 schema / 内容读回：与本次重建的投影**逐字节**比较。
    derived_json = _json_members(samples, run_id=manifest.get("run_id", ""),
                                 results_dir=run_dir)
    derived_md = _md_members(samples,
                             matrix=derived_json["table_acceptance_matrix.json"])
    derived_jsonl = _jsonl_members(samples)
    for relpath, payload in sorted(derived_json.items()):
        actual = _read_json(run_dir / relpath, relpath)
        if canonical_json(actual) != canonical_json(payload):
            problems.append(f"{relpath} 与本次重建的投影不等（内容 tamper）")
    for relpath, text in sorted(derived_md.items()):
        actual = (run_dir / relpath).read_text(encoding="utf-8")
        if actual != text:
            problems.append(f"{relpath} 与本次重建的投影不等（内容 tamper）")
    for relpath, rows in sorted(derived_jsonl.items()):
        actual = (run_dir / relpath).read_text(encoding="utf-8")
        expected_text = "".join(canonical_json(row) + "\n" for row in rows)
        if actual != expected_text:
            problems.append(f"{relpath} 与本次重建的投影不等（内容 tamper）")
    for sample in samples:
        relpath = f"{MATRIX_SNAPSHOT_DIRNAME}/{sample['document_key']}.json"
        actual = _read_json(run_dir / relpath, relpath)
        if canonical_json(actual) != canonical_json(_snapshot_of(sample)):
            problems.append(f"{relpath} 与本次重建的 final 快照不等（内容 tamper）")
    stored_manifest = _read_json(run_dir / "run_manifest.json", "run_manifest.json")
    fresh_manifest = build_run_manifest(
        inputs=inputs, samples=samples, run_id=stored_manifest.get("run_id", ""),
        run_id_source=stored_manifest.get("run_id_source", ""),
        run_dir_relpath=stored_manifest.get("run_dir_relpath", ""),
        generated_at=stored_manifest.get("generated_at_utc", ""))
    if canonical_json(fresh_manifest) != canonical_json(stored_manifest):
        problems.append("run_manifest.json 与本次重建的 run manifest 不等")

    # 6) DB 不可变性记录：重读，并把当前身份与记录比较。
    immutability = _read_json(run_dir / "database_immutability.json",
                              "database_immutability.json")
    if not immutability.get("unchanged"):
        problems.append("database_immutability.json 记录的不是「未变化」")
    # 6b) 机器门与 §19.13 派生裁决：**从本次重建的真实产物重算**，与落盘的那份
    #     逐字段比较。执行者在这里没有自报入口，篡改矩阵必然对不上。
    stored_matrix = _read_json(run_dir / "table_acceptance_matrix.json",
                               "table_acceptance_matrix.json")
    fresh_gates = build_machine_gates(samples=samples, immutability=immutability)
    if canonical_json(stored_matrix.get("machine_gates")) \
            != canonical_json(fresh_gates):
        problems.append("table_acceptance_matrix.json 的 machine_gates 与本次重建的"
                        "重算结果不等（裁决不可复现或已被改写）")
    decision_path_early = run_dir / "review_decision.json"
    if decision_path_early.exists():
        stored_decision = _read_json(decision_path_early, "review_decision.json")
        if bool(stored_decision.get("machine_gates_passed")) != bool(
                fresh_gates["passed"]):
            problems.append("review_decision.machine_gates_passed 与重算的机器门结论"
                            "不一致")
    recorded_after = {str(REPO_ROOT / row["relpath"]): (row.get("after") or {})
                      for row in immutability.get("databases", [])}
    now = R.snapshot(R._dump_watched_dbs())
    report = R.immutability_report(recorded_after, now)
    if not report["unchanged"]:
        problems.append(f"受监管库自该 run 以来已变化：changed={report['changed']}")

    # 7) review：如已封存则必须仍然自洽。
    attestation_path = run_dir / "review_attestation.json"
    if attestation_path.exists():
        attestation = _read_json(attestation_path, "review_attestation.json")
        try:
            SCH.validate_review_attestation(
                attestation, run_id=manifest.get("run_id", ""),
                run_dir_relpath=manifest.get("run_dir_relpath", ""))
        except SCH.AcceptanceSchemaError as e:
            problems.append(f"review_attestation 不符合 `tra-1`：{e}")
        for relpath, key in (("run_manifest.json", "run_manifest_sha256"),
                             ("machine_artifact_index.json",
                              "artifact_index_sha256"),
                             ("machine_artifact_index_check.json",
                              "artifact_index_check_sha256"),
                             ("review_decision.json", "review_decision_sha256")):
            declared = attestation.get("identity", {}).get(key)
            actual = sha256_file(run_dir / relpath)
            if declared != actual:
                problems.append(f"封存后 {relpath} 已变化：{declared} -> {actual}"
                                "（attestation 失效）")
    decision_path = run_dir / "review_decision.json"
    if decision_path.exists():
        decision = _read_json(decision_path, "review_decision.json")
        try:
            SCH.validate_review_decision(
                decision, run_id=manifest.get("run_id", ""),
                final_snapshot_ids=[s["final"].snapshot_id for s in samples])
        except SCH.AcceptanceSchemaError as e:
            problems.append(f"review_decision 不符合 `trv-1`：{e}")

    report_payload = {
        "artifact": "validate_only_report",
        "run_id": manifest.get("run_id"),
        "run_dir_relpath": manifest.get("run_dir_relpath"),
        "schema_versions": dict(SCH.EVALUATION_VERSION_AXES),
        "documents": [{"document_key": s["document_key"],
                       "ts4_snapshot_id": s["ts4"].snapshot_id,
                       "final_snapshot_id": s["final"].snapshot_id,
                       "verifier_verdict": s["verdict"],
                       "table_count": s["final"].table_count,
                       "final_span_count": s["final"].final_span_count,
                       "blocking_gap_count": s["final"].blocking_gap_count}
                      for s in samples],
        "index_check": {k: check[k] for k in
                        ("ok", "scan_file_count", "index_file_count",
                         "missing", "extra", "hash_mismatch")},
        "attestation_present": attestation_path.exists(),
        "problems": problems,
        "ok": not problems,
    }
    print(json.dumps(report_payload, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def code_fingerprint() -> dict:
    """TS5 生产 / 评测代码的**内容指纹**（逐文件 sha256，不是版本号自报）。"""
    entries: list = []
    for rel in CODE_FINGERPRINT_FILES:
        path = REPO_ROOT / rel
        entries.append([rel, sha256_file(path) if path.is_file() else None])
    return {
        "files": {rel: digest for rel, digest in entries},
        "fingerprint": sha256_canonical(entries),
        "missing": [rel for rel, digest in entries if digest is None],
        "file_count": len(entries),
    }


# ---------------------------------------------------------------------------
# 9. self-check 与 CLI
# ---------------------------------------------------------------------------

def self_check() -> dict:
    problems: list = []
    if tuple(EXCLUDED_FROM_MACHINE_INDEX) != tuple(SCH.NOT_SCANNED_BY_INDEX):
        problems.append("EXCLUDED_FROM_MACHINE_INDEX 必须与 evaluation schema 的 "
                        "NOT_SCANNED_BY_INDEX 逐项相等")
    if SCH.RUN_DIR_PREFIX != "tree_table_ts5_":
        problems.append(f"run 目录前缀必须为 'tree_table_ts5_'，得到 "
                        f"{SCH.RUN_DIR_PREFIX!r}")
    members = SCH.expected_machine_members(("A", "B", "C", "D"))
    indexable = SCH.indexable_members(("A", "B", "C", "D"))
    if len(members) - len(indexable) != len(SCH.INDEX_INTERNAL_MEMBERS):
        problems.append("可索引成员集合必须是全集去掉索引内部成员")
    for name in indexable:
        try:
            _member_role(name)
        except StepFailure:
            problems.append(f"机器产物 {name!r} 未登记 role")
    for name in MEMBER_ROLES:
        if name not in members:
            problems.append(f"MEMBER_ROLES 里的 {name!r} 不属于 `tai-1` 成员集合")
    code = code_fingerprint()
    if code["missing"]:
        problems.append(f"代码指纹文件缺失：{code['missing']}")
    if len(REVIEW_QUESTIONS) != len(SCH.REQUIRED_REVIEW_CHECK_IDS):
        problems.append("REVIEW_QUESTIONS 必须与 REQUIRED_REVIEW_CHECK_IDS 等长")
    if tuple(WATCHED_DB_RELPATHS) != (R.EVIDENCE_DB_RELPATH,):
        problems.append("WATCHED_DB_RELPATHS 必须恰为 (data/evidence.db,)")
    problems.extend(authority_isolation_problems())
    problems.extend(FV.verifier_independence_problems())
    try:
        # 声明的 document_key 必须真的在信任锚里：一个 typo 不该花掉一次真跑的时长。
        problems.extend(_declaration_problems(load_trust_root()["document_order"]))
    except StepFailure as e:
        problems.append(f"固定样本声明自检无法读取信任锚（{e}）")
    for axis, value in SCH.EVALUATION_VERSION_AXES.items():
        if value not in _all_version_strings():
            problems.append(f"版本轴 {axis}={value} 未出现在 TS5 版本束里")
    # runner 里的两条拒发原因字面量必须就是 `trm-2` 词表里的那两条：词表由 schema
    # 模块定义，runner 不得各自复制一份还可能漂移的值。
    if (REFUSAL_BLOCKING_STRUCTURE_GAP,
            REFUSAL_FINAL_CONSERVATION_INELIGIBLE) \
            != tuple(SCH.REFUSAL_REASON_CODES):
        problems.append("runner 的拒发原因字面量与 `trm-2` 词表不一致："
                        f"{SCH.REFUSAL_REASON_CODES}")
    if TRUST_ROOT_RELPATH == SCH.LEGACY_TRUST_ROOT_RELPATH:
        problems.append("current 信任锚必须指向版本化 successor，不得就地复用旧文件")
    return {
        "problems": problems,
        "acceptance_runner_version": SCH.TS5_ACCEPTANCE_RUNNER_VERSION,
        "trust_root_relpath": TRUST_ROOT_RELPATH,
        "trust_root_file_sha256": TRUST_ROOT_FILE_SHA256,
        "legacy_trust_root_relpath": SCH.LEGACY_TRUST_ROOT_RELPATH,
        "refusal_reason_codes": list(SCH.REFUSAL_REASON_CODES),
        "mode": "pinned_threshold_enabled",
        "code_fingerprint_file_count": len(CODE_FINGERPRINT_FILES),
        "code_fingerprint_missing": code["missing"],
        "code_fingerprint": code["fingerprint"],
        "machine_artifact_member_count": len(members),
        "indexable_member_count": len(indexable),
        "review_check_id_count": len(SCH.REQUIRED_REVIEW_CHECK_IDS),
        "verifier_version": FV.VERIFIER_VERSION,
        "watched_db_relpaths": list(WATCHED_DB_RELPATHS),
    }


def _all_version_strings() -> set:
    out: set = set()

    def _walk(value):
        if isinstance(value, str):
            out.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item)

    _walk(build_versions_payload())
    return out


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TS5 TableObject 真实验收 runner（evaluation-only，只读）")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run-id", default=None,
                        help="opaque run 标签；不传则按 UTC 时间戳生成")
    parser.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--validate-only", default=None, metavar="RUN_DIR",
                        help="只读复核一个已存在的 run 目录")
    parser.add_argument("--seal-review", default=None, metavar="RUN_DIR",
                        help="按 `tra-1` 封存已填写的人工 review")
    args = parser.parse_args(argv)
    results_root = pathlib.Path(args.results_root)
    if not results_root.is_absolute():
        results_root = REPO_ROOT / results_root
    try:
        if args.self_check:
            report = self_check()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if not report["problems"] else 1
        if args.seal_review:
            return seal_review(pathlib.Path(args.seal_review))
        if args.validate_only:
            return validate_only(pathlib.Path(args.validate_only))
        return execute_run(run_id=args.run_id, results_root=results_root,
                           generated_at=_utc_now())
    except StepFailure as e:
        print(f"FAILED {e.report()}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(_main())

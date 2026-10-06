"""TS4-A 真实 span 验收（**评测专用**，只读；计划 §18.14）。

本脚本是 TS4-A 编码批的**真实验收 runner**。它把冻结 TS3 产物当作**只读信任锚**，
在仓库内**等价重建** 三份真实 Evidence-backed 文档 + 一个版本化非 300750 正向
fixture 的 TS4 span 快照，落盘机器产物，供用户 + Codex 做**唯一后置人工门**裁决。

硬约束（违反即本 run 无效）：

- **只读**。不写 `data/*.db`、不改冻结产物、不重跑对齐、不联网、不上 LLM；运行前后
  `data/evidence.db` 的 `size` / `mtime_ns` / `sha256` 必须**逐字段相等**。
- **不重跑对齐**。三份真实文档的终态来自冻结 `normalization_alignment.json` 的 `rows`，
  由 `from_dict` 还原（计划 §18.14.2-3 / A2 裁决）；`align_block` / `align_evidence_set`
  一律不得在本脚本中被调用。
- **验收投影不是权威**。冻结产物里的 `line_structure_assignment` / `formal_unassigned` /
  表格范围 / 组件 / 覆盖**一律不得进入正式输入**；正式输入只经 `evidence_gateway` /
  `span_builder` 的 pinned issuer 取得。
- **TS4-A 不得支持 `set_complete`**：`SPAN_CONFIDENCE_MIN is None`、`stage=distribution_only`、
  `threshold=None`、`completion_enabled=False`。TS4-A 的构建阶段**不创建**
  `span_qualification_approval_v1.json` / `span_qualification_frozen_v1.json`。
- **构建完成不等于 TS4 通过**：产物一律标注 `manual_review_required`，`manual_review.md` 与
  `policy_decision.json` 只创建**空白模板**，不替用户 / Codex 填写任何裁决。
- **TS4-B 的两份资产只能从已封存 attestation 导出**：`--export-b-assets <A目录>` 复核封存
  事实后**确定性派生** approval / frozen 记录并**追加**登记注册表；它不构建 span、不改 A
  目录、不覆盖既有 B 资产，且**不**宣称任何 topic / aspect 已完成。

用法::

    python -m evaluation.run_tree_span_acceptance --validate-only
    python -m evaluation.run_tree_span_acceptance --run-id tree_span_ts4_ts4a_<UTC>
    python -m evaluation.run_tree_span_acceptance --seal-review <已有 TS4-A 结果目录>
    python -m evaluation.run_tree_span_acceptance --export-b-assets <已封存 TS4-A 结果目录>

不传 `--run-id` 时按 UTC 时间戳生成；**绝不覆盖**任何历史结果目录。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import pathlib
import shutil
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # 允许 `python evaluation/run_tree_span_acceptance.py`
    sys.path.insert(0, str(REPO_ROOT))

from document_structure import evidence_gateway as EG              # noqa: E402
from document_structure import layout_builder as LB                # noqa: E402
from document_structure import schema as S                         # noqa: E402
from document_structure import span_builder as SB                  # noqa: E402
from document_structure import span_policy as SP                   # noqa: E402
from document_structure import span_verifier as SV                 # noqa: E402
from document_structure import synopsis as SY                      # noqa: E402
from document_structure import versions as V                       # noqa: E402
from document_structure.canonical import canonical_json            # noqa: E402
from document_structure.canonical import sha256_canonical          # noqa: E402

DEFAULT_RESULTS_ROOT = "evaluation/results"
TRUST_ROOT_RELPATH = "evals/fixtures/tree_structure/ts4_trust_roots.json"
#: 根锁**自身**的 sha256。它在脚本里是字面量、在仓库里是被指纹覆盖的文件：
#: "同目录 manifest/index/check 互相一致"不构成信任，本常量才是链条的起点。
TRUST_ROOT_FILE_SHA256 = (
    "a19a1b286cc9fca48f4c6289150c4413a86a6d2c896fa708ace9bcfc15a6ccb1")
TRUST_ROOT_KIND = "ts4_trust_root_v1"

FROZEN_RUN_MANIFEST_SHA256 = (
    "c418f74cf44f12e38ce0dd86099ca0386228f8d98ea00f3d5cfef37354b466a4")
FROZEN_ARTIFACT_INDEX_SHA256 = (
    "e3a26ddbb5dce6f6516558881f07bb248f4061a9f7bd6f5b8241884466cfe15e")

EVIDENCE_DB_RELPATH = "data/evidence.db"
FINANCIAL_DB_RELPATH = "data/financial_v2.db"

#: 本 runner 运行前后必须逐字段相等的库（§18.14.2-5 / §18.14.3）。只列**本链真正绑定
#: 的那一个**：把从未被本 runner 打开的库也写进"受监管"，等于让产物声明一件它并没有
#: 真正看守的事。
WATCHED_DB_RELPATHS: tuple[str, ...] = (EVIDENCE_DB_RELPATH,)

#: evaluation-only 常量（计划 §18.14.3）。它们**不是**生产 wire 类型，但进入代码指纹。
POLICY_DECISION_REVIEW_SCHEMA_VERSION = "pd-1"
REVIEW_ATTESTATION_SCHEMA_VERSION = "ra-1"

#: `PolicyDecisionV1(pd-1)` 的封闭字段（未知 / 缺失 / 重复一律拒绝）。
POLICY_DECISION_FIELDS: tuple[str, ...] = (
    "schema_type", "review_schema_version", "decision",
    "a_run_identity", "a_root_identity", "a_machine_index_identity",
    "a_aggregate_snapshot_sha256", "a_confidence_distribution_sha256",
    "factor_entries", "threshold", "review_verdict", "reviewer_roles",
    "manual_review_sha256", "policy_decision_sha256_note",
)
POLICY_DECISION_DECISIONS: tuple[str, ...] = ("approve", "reject", "needs_changes")
REVIEW_VERDICTS: tuple[str, ...] = ("pass", "fail", "not_reviewed")
REVIEWER_ROLES: tuple[str, ...] = ("user", "codex")

#: §18.14.4 的 8 项人工验收清单 ID（固定顺序、恰好各一条 verdict）。
REVIEW_CHECK_IDS: tuple[str, ...] = (
    "main_business_body_complete",
    "core_competitiveness_subheadings_separated",
    "subsidiary_materials_not_leaked",
    "financial_note_text_and_tables_isolated",
    "cross_heading_evidence_split_into_components",
    "table_caption_and_unit_lines_absent_from_snippets",
    "non_300750_positive_fixture_and_legacy_negative",
    "all_conservation_and_coverage_gaps_listed",
)

#: §18.14.2-8 的 fail-closed 负例分类（生产代码没有同名 typed 常量，见汇报）。
NEGATIVE_MISSING_EVIDENCE_SET = "missing_evidence_set"

#: 三份真实 Evidence-backed 文档（company_id, document_id, 冻结 run 内的目录名）。
REAL_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("300750", "NDSD_2024_year"),
    ("300750", "NDSD_2025_year"),
    ("300750", "NDSD_KCZ_2026"),
)
#: 冻结 run 里**无 Evidence 集**的文档：只作 fail-closed 负例。
NEGATIVE_DOCUMENT_ID = "FIXTURE_BOND_2026"
NEGATIVE_DOCUMENT_KEY = NEGATIVE_DOCUMENT_ID + "__frozen_run_no_evidence_set"
#: 版本化非 300750 正向 fixture 的文档键（与负例同名文档必须区分 scope）。
FIXTURE_DOCUMENT_KEY = NEGATIVE_DOCUMENT_ID + "__non_300750_ts4"

#: 正向样本的**固定文档序**（聚合产物按此序，不接受目录遍历顺序）。
POSITIVE_DOCUMENT_ORDER: tuple[str, ...] = (
    "NDSD_2024_year", "NDSD_2025_year", "NDSD_KCZ_2026", FIXTURE_DOCUMENT_KEY)

SNAPSHOT_DIRNAME = "span_build_snapshots"

#: 代码指纹参与文件（§18.14.1，**逐文件显式列出**：不得用目录通配或"全部测试"代替）。
CODE_FINGERPRINT_FILES: tuple[str, ...] = (
    "document_structure/__init__.py",
    "document_structure/versions.py",
    "document_structure/canonical.py",
    "document_structure/schema.py",
    "document_structure/outline_builder.py",
    "document_structure/layout_builder.py",
    "document_structure/aligner.py",
    "document_structure/evidence_gateway.py",
    "document_structure/span_schema.py",
    "document_structure/span_policy.py",
    "document_structure/span_builder.py",
    "document_structure/span_verifier.py",
    "document_structure/synopsis.py",
    "evidence/store.py",
    "sections/service.py",
    "evaluation/run_tree_span_acceptance.py",
    "evals/run_evals.py",
    "evals/test_tree_span_schema.py",
    "evals/test_tree_span_input.py",
    "evals/test_tree_span_coords.py",
    "evals/test_tree_span_builder.py",
    "evals/test_tree_span_verifier.py",
    "evals/test_tree_synopsis.py",
    "evals/test_tree_span_conservation.py",
    "evals/test_tree_span_artifacts.py",
    "evals/test_tree_span_fixture.py",
    "evals/test_tree_span_completion.py",
    "evals/test_tree_span_formal_chain.py",
    "evals/test_tree_page_layout.py",
    "evals/test_tree_structure_schema.py",
    "evals/test_tree_structure_adversarial.py",
    "evals/fixtures/tree_structure/ts4_trust_roots.json",
    # 版本化正向 fixture：manifest **逐项列出**的成员文件，逐个显式登记。
    "evals/fixtures/tree_structure/non_300750_ts4/manifest.json",
    "evals/fixtures/tree_structure/non_300750_ts4/page_layout.json",
    "evals/fixtures/tree_structure/non_300750_ts4/document_outline.json",
    "evals/fixtures/tree_structure/non_300750_ts4/evidence_set.json",
    # TS4-A 固定列两份策略资产（TS4-B 才另列 approval / frozen 记录）。
    "document_structure/policies/registry_v1.json",
    "document_structure/policies/span_qualification_distribution_v1.json",
)

#: TS3 冻结索引里**不含自身**的两个文件（沿用同一约定）。
_FROZEN_INDEX_INTERNAL_FILES: tuple[str, ...] = (
    "artifact_index.json", "artifact_index_check.json")

#: 机器产物清单里**刻意排除**的文件（索引不含自身；人工模板不进机器索引）。
EXCLUDED_FROM_MACHINE_INDEX: tuple[str, ...] = (
    "machine_artifact_index.json",
    "machine_artifact_index_check.json",
    "manual_review.md",
    "policy_decision.json",
    "review_attestation.json",
)


# ---------------------------------------------------------------------------
# 0. 既有 runner 约定的原语（sha256_file / snapshot / immutability_report）
# ---------------------------------------------------------------------------

def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def snapshot(paths) -> dict:
    """受监管文件的 sha256 / size / mtime_ns（缺失即显式记录，不用 None 假装一致）。"""
    out: dict = {}
    for path in paths:
        path = pathlib.Path(path)
        if not path.exists():
            out[str(path)] = {"missing": True}
            continue
        st = path.stat()
        out[str(path)] = {
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "sha256": sha256_file(path) if path.is_file() else None,
        }
    return out


def immutability_report(before: dict, after: dict) -> dict:
    """逐字段比较两份快照；`changed` 为空且无 watched_missing 才算不变。"""
    if set(before) != set(after):
        raise RuntimeError("immutability_report 的两份快照必须覆盖同一组路径")
    changed = sorted(k for k in before if before[k] != after[k])
    watched_missing = sorted(k for k, v in before.items() if v.get("missing"))
    return {"unchanged": not changed and not watched_missing,
            "changed": changed, "watched_missing": watched_missing}


def _dump_watched_dbs() -> list:
    """受监管库的**仓库相对**路径（`snapshot()` 用绝对路径做键，产物里必须可读）。"""
    return [REPO_ROOT / rel for rel in WATCHED_DB_RELPATHS]


def build_database_immutability(*, before: dict, after: dict) -> dict:
    """`database_immutability.json`（计划 §18.14.3 机器阶段产物之一）。

    它**先于** machine index 写盘，因此会被索引进 `machine_artifact_index.json`——
    run manifest 里那句"运行前后的逐字段身份见 database_immutability.json"必须真的
    指得到一个文件。逐字段列出 size / mtime_ns / sha256，任一项不等即本 run 无效。
    """
    report = immutability_report(before, after)
    databases = []
    for rel in WATCHED_DB_RELPATHS:
        key = str(REPO_ROOT / rel)
        databases.append({
            "relpath": rel,
            "before": before.get(key),
            "after": after.get(key),
            "unchanged": before.get(key) == after.get(key),
        })
    return {
        "manual_review_required": True,
        "artifact": "database_immutability.json",
        "scope": "本 run 运行前后受监管库文件的逐字段身份（size / mtime_ns / sha256）",
        "access": ("只读：`mode=ro` + `query_only`；不 init、不 migrate、不修改任何 store "
                   "的 `_db_path`"),
        "fields": ["size", "mtime_ns", "sha256"],
        "databases": databases,
        "unchanged": report["unchanged"],
        "changed": report["changed"],
        "watched_missing": report["watched_missing"],
        "note": ("`changed` / `watched_missing` 记录的是**绝对路径**；非空即本 run 无效"
                 "（fail-closed）。这里只监管本链真正绑定的库，不虚报未打开的库。"),
    }


def code_fingerprint() -> dict:
    """TS4 生产 / 评测代码的**内容指纹**（逐文件 sha256，不是版本号自报）。"""
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


def _read_json(path: pathlib.Path, what: str) -> Any:
    if not pathlib.Path(path).is_file():
        raise StepFailure(0, f"{what}不存在：{path}")
    raw = pathlib.Path(path).read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise StepFailure(0, f"{what}不是合法 UTF-8 JSON：{path}（{e}）") from e


def _write_json(path: pathlib.Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def _write_json_lines(path: pathlib.Path, rows) -> None:
    """`jsonl`：每行一个规范 JSON（无美化、按 token 序），便于逐行复核。"""
    text = "".join(canonical_json(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. fail-closed 的步骤化失败（必须点名：第几步 / 哪个文档 / 哪个字段）
# ---------------------------------------------------------------------------

class StepFailure(RuntimeError):
    """§18.14.2 绑定链任一步失败：fail-closed，且必须能说清"哪一步、哪个字段"。"""

    def __init__(self, step: int, message: str, *, document_id: str | None = None,
                 field: str | None = None) -> None:
        self.step = step
        self.document_id = document_id
        self.field = field
        self.message = message
        super().__init__(self.report())

    def report(self) -> str:
        where = []
        if self.document_id is not None:
            where.append(f"document={self.document_id}")
        if self.field is not None:
            where.append(f"field={self.field}")
        tail = (" " + " ".join(where)) if where else ""
        return f"[STEP {self.step}]{tail} {self.message}"


def _need_step_sha256(step: int, what: str, path: pathlib.Path, expected: str,
                      **kw) -> str:
    if not path.is_file():
        raise StepFailure(step, f"{what} 缺失：{path}（fail-closed）", **kw)
    actual = sha256_file(path)
    if actual != expected:
        raise StepFailure(
            step, f"{what} 的 sha256 与信任锚不一致：{expected} -> {actual}"
                  f"（fail-closed）", **kw)
    return actual


# ---------------------------------------------------------------------------
# 2. §18.14.2 冻结输入绑定（1–9 条）
# ---------------------------------------------------------------------------

class Bindings:
    """一次验收所需的全部**已核验**输入（只在内存里；不含任何自报派生结果）。"""

    def __init__(self, *, trust_root: dict, frozen_run_dir: pathlib.Path,
                 documents: dict, handoffs: dict, negative: dict,
                 layout_restores: dict, evidence_cross_checks: dict,
                 fixture: dict, notes: list) -> None:
        self.trust_root = trust_root
        self.frozen_run_dir = frozen_run_dir
        self.documents = documents          # document_key -> 只读身份投影
        self.handoffs = handoffs            # document_key -> VerifiedTS3Handoff（正向）
        self.negative = negative
        self.layout_restores = layout_restores
        self.evidence_cross_checks = evidence_cross_checks
        self.fixture = fixture
        self.notes = notes


def load_trust_root() -> dict:
    """§18.14.2-1：先对**根锁文件本身**成立，再做目录内闭合。"""
    path = REPO_ROOT / TRUST_ROOT_RELPATH
    step = 1
    if not path.is_file():
        raise StepFailure(step, f"仓库内信任锚缺失：{path}（fail-closed）",
                          field="trust_root")
    actual = sha256_file(path)
    if actual != TRUST_ROOT_FILE_SHA256:
        raise StepFailure(
            step, f"信任锚自身 sha256 与脚本内字面量不符：{TRUST_ROOT_FILE_SHA256} "
                  f"-> {actual}；根锚一旦变化，历史结论全部不可比（fail-closed）",
            field="trust_root_file_sha256")
    data = _read_json(path, "信任锚")
    if not isinstance(data, dict) or data.get("fixture_kind") != TRUST_ROOT_KIND:
        raise StepFailure(step, f"信任锚 fixture_kind 必须为 {TRUST_ROOT_KIND!r}",
                          field="fixture_kind")
    data["trust_root_file_sha256"] = actual
    return data


def _frozen_documents_identity(trust_root: dict, artifact_index: dict,
                               document_id: str) -> dict:
    """把 `artifact_index.json` 里该文档的全部成员投影成 `{相对路径: {sha256,size}}`。"""
    entries = artifact_index.get("files")
    if not isinstance(entries, list):
        raise StepFailure(1, "artifact_index.json 缺 files 列表", field="files")
    prefix = f"{document_id}/"
    out: dict = {}
    for entry in entries:
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel.startswith(prefix):
            continue
        out[rel[len(prefix):]] = {"sha256": entry.get("sha256"),
                                  "size": entry.get("size")}
    if not out:
        raise StepFailure(1, f"artifact_index.json 未登记 {document_id} 的任何成员",
                          document_id=document_id, field="artifact_index.files")
    return out


def _first_differing_path(mine: Any, theirs: Any, path: str = "$") -> str:
    """定位两份 JSON 的第一处差异（只用于**报错点名**，不参与判定）。"""
    if type(mine) is not type(theirs):
        return f"{path} (类型 {type(theirs).__name__} -> {type(mine).__name__})"
    if isinstance(mine, dict):
        for key in sorted(set(mine) | set(theirs)):
            if key not in theirs:
                return f"{path}.{key} (冻结侧缺失)"
            if key not in mine:
                return f"{path}.{key} (重建侧缺失)"
            if mine[key] != theirs[key]:
                return _first_differing_path(mine[key], theirs[key], f"{path}.{key}")
        return path
    if isinstance(mine, list):
        if len(mine) != len(theirs):
            return f"{path} (长度 {len(theirs)} -> {len(mine)})"
        for index, (a, b) in enumerate(zip(mine, theirs)):
            if a != b:
                return _first_differing_path(a, b, f"{path}[{index}]")
        return path
    return f"{path} ({theirs!r} -> {mine!r})"


def _restore_layout_and_outline(*, step: int, document_id: str,
                                doc_dir: pathlib.Path, locked: dict,
                                expected_identity: dict,
                                expected_alignment: dict) -> tuple:
    """§18.14.2-2：`from_dict` 还原 PageLayout，并读回冻结 DocumentOutline 的 raw JSON。

    `DocumentOutline` **刻意不在此处实例化**：它的 reference occurrence 校验需要真实
    `ReferenceValidationContext`（`toc_sources` 并不在冻结文件里，无法离线重建），强行
    `from_dict` 会在合法产物上 fail-closed。正确的相等性判据是**重建后的对象**与冻结
    文件做 canonical 全对象比较（§18.14.2-6），见 `_issue_real_document_handoff`。

    `formal_unassigned.json` **只**做交叉核对，**不**参与构造。
    """
    for name, entry in sorted(locked["artifacts"].items()):
        _need_step_sha256(step, f"{document_id}/artifacts/{name}",
                          doc_dir / name, entry["sha256"], document_id=document_id,
                          field=name)
        actual_size = (doc_dir / name).stat().st_size
        if actual_size != entry["size"]:
            raise StepFailure(step, f"冻结产物大小与信任锚不符：{actual_size} != "
                                    f"{entry['size']}（fail-closed）",
                              document_id=document_id, field=name)
    layout_raw = _read_json(doc_dir / "page_layout.json", "冻结 page_layout.json")
    try:
        layout = S.PageLayout.from_dict(layout_raw)
    except Exception as e:  # noqa: BLE001 - 还原失败必须点名
        raise StepFailure(step, f"冻结 page_layout.json 无法还原为 PageLayout：{e!r}",
                          document_id=document_id, field="page_layout") from e
    outline_raw = _read_json(doc_dir / "document_outline.json",
                             "冻结 document_outline.json")
    if not isinstance(outline_raw, dict):
        raise StepFailure(step, "冻结 document_outline.json 顶层必须为对象",
                          document_id=document_id, field="document_outline")
    checks = {
        "company_id": (layout.company_id, expected_identity.get("company_id")),
        "document_id": (layout.document_id, expected_identity.get("document_id")),
        "document_version": (layout.document_version,
                             expected_identity.get("document_version")),
        "page_layout_id": (layout.page_layout_id,
                           expected_identity.get("page_layout_id")),
        "page_layout_locator": (layout.page_layout_locator,
                                expected_identity.get("page_layout_locator")),
        "page_count": (len(layout.pages), expected_identity.get("page_count")),
        "outline_id": (outline_raw.get("outline_id"),
                       expected_identity.get("outline_id")),
        "outline_locator": (outline_raw.get("outline_locator"),
                            expected_identity.get("outline_locator")),
        "outline_content_fingerprint": (
            outline_raw.get("content_fingerprint"),
            expected_identity.get("outline_content_fingerprint")),
        "outline_schema_version": (outline_raw.get("schema_version"),
                                   expected_identity.get("schema_version")),
        "outline_algorithm_version": (
            outline_raw.get("algorithm_version"),
            expected_identity.get("outline_builder_version")),
        "outline_page_layout_id": (outline_raw.get("page_layout_id"),
                                   layout.page_layout_id),
        "outline_document_version": (outline_raw.get("document_version"),
                                     layout.document_version),
    }
    for field, (mine, theirs) in checks.items():
        if mine != theirs:
            raise StepFailure(step, f"冻结产物的 {field} 与信任锚不一致："
                                    f"{theirs!r} -> {mine!r}（fail-closed）",
                              document_id=document_id, field=field)
    # §18.14.2-2：用 lock 指向的原 PDF 做**只读回读校验**（正式 layout verifier）。
    readback = LB.validate_layout_readback(layout)
    if not readback.get("ok"):
        raise StepFailure(step, f"冻结 PageLayout 未通过只读回读校验："
                                f"{readback.get('issues', [])[:5]}（fail-closed）",
                          document_id=document_id, field="page_layout_readback")
    # `formal_unassigned.json` 只做**恢复后交叉核对**。
    formal = _read_json(doc_dir / "formal_unassigned.json", "冻结 formal_unassigned.json")
    formal_rows = formal.get("rows") if isinstance(formal, dict) else None
    if not isinstance(formal_rows, list):
        formal_rows = formal.get("unassigned") if isinstance(formal, dict) else None
    if isinstance(formal_rows, list):
        declared_count = expected_identity.get("formal_unassigned_count")
        if declared_count is not None and len(formal_rows) != declared_count:
            raise StepFailure(step, f"formal_unassigned.json 条数 {len(formal_rows)} 与信任锚 "
                                    f"{declared_count} 不一致（fail-closed）",
                              document_id=document_id, field="formal_unassigned_count")
        frozen_unassigned = outline_raw.get("unassigned")
        if not isinstance(frozen_unassigned, list) or len(frozen_unassigned) != len(
                formal_rows):
            raise StepFailure(
                step, f"冻结 document_outline.json 的 unassigned 条数 "
                      f"{None if not isinstance(frozen_unassigned, list) else len(frozen_unassigned)}"
                      f" 与冻结 formal_unassigned.json 的 {len(formal_rows)} 不一致"
                      f"（交叉核对失败，fail-closed）",
                document_id=document_id, field="outline.unassigned")
    return layout, outline_raw


def _cross_check_alignment_numbers(*, step: int, document_id: str,
                                   frozen: dict, expected: dict) -> None:
    """§18.14.2-3：核对 `terminals_missing_count == 0` / `block_count` / schema 版本。"""
    pairs = (
        ("block_count", frozen.get("block_count"), expected.get("block_count")),
        ("terminals_missing_count", frozen.get("terminals_missing_count"), 0),
        ("evidence_set_version", frozen.get("evidence_set_version"),
         expected.get("evidence_set_version")),
        ("terminal_schema_versions", frozen.get("terminal_schema_versions"),
         expected.get("terminal_schema_versions")),
    )
    for field, mine, theirs in pairs:
        if mine != theirs:
            raise StepFailure(step, f"冻结 normalization_alignment.json 的 {field} "
                                    f"与信任锚不一致：{theirs!r} -> {mine!r}"
                                    f"（fail-closed）", document_id=document_id,
                              field=field)
    rows = frozen.get("rows")
    if not isinstance(rows, list) or len(rows) != expected.get("block_count"):
        raise StepFailure(step, "冻结 rows 的条数与 block_count 不一致（fail-closed）",
                          document_id=document_id, field="rows")


def _cross_check_evidence_identity(*, step: int, document_id: str, handoff,
                                   expected: dict,
                                   declared_fields: set | None = None) -> dict:
    """§18.14.2-4：把 pinned issuer 取到的权威集合身份对回冻结产物记录。

    `declared_fields` 给定时，**只**比对来源真正声明的字段；未声明的字段被显式记为
    `not_declared_by_source`（不用 None 假装已核对）。
    """
    snap = handoff.evidence_snapshot
    expected_snap = expected.get("evidence_set_snapshot") or {}
    member_sha = EG.member_identity_sha256(snap.members)
    pairs = (
        ("evidence_set_version", snap.evidence_set_version,
         expected.get("evidence_set_version")),
        ("block_count", snap.block_count, expected.get("block_count")),
        ("fingerprint", snap.fingerprint, expected_snap.get("fingerprint")),
        ("snapshot_version", snap.snapshot_version, expected_snap.get("snapshot_version")),
        ("status", snap.status, expected_snap.get("status")),
        ("member_identity_sha256", member_sha,
         expected.get("member_identity_sha256")),
    )
    not_declared: list = []
    checked: list = []
    for field, mine, theirs in pairs:
        if declared_fields is not None and field not in declared_fields:
            not_declared.append(field)
            continue
        checked.append(field)
        if mine != theirs:
            raise StepFailure(step, f"权威 Evidence 集合的 {field} 与冻结产物记录的"
                                    f"不一致：{theirs!r} -> {mine!r}（fail-closed）",
                              document_id=document_id, field=f"evidence.{field}")
    if len(handoff.evidence_blocks) != snap.block_count:
        raise StepFailure(step, "EvidenceBlockInput 数与本集合成员数不一致（fail-closed）",
                          document_id=document_id, field="evidence_blocks")
    authority = handoff.evidence_authority
    if authority.issuer_scope != "pinned_acceptance":
        raise StepFailure(step, f"Evidence 权威的签发域必须为 pinned_acceptance，得到 "
                                f"{authority.issuer_scope!r}（fail-closed）",
                          document_id=document_id, field="issuer_scope")
    return {
        "issuer_scope": authority.issuer_scope,
        "source_kind": authority.source_kind,
        "issuer_version": authority.issuer_version,
        "provider_version": authority.provider_version,
        "authority_fingerprint": authority.authority_fingerprint,
        "trust_root_file_sha256": authority.trust_root_file_sha256,
        "resolved_db_identity": authority.resolved_db_identity,
        "snapshot_version": snap.snapshot_version,
        "evidence_set_version": snap.evidence_set_version,
        "block_count": snap.block_count,
        "fingerprint": snap.fingerprint,
        "member_identity_sha256": member_sha,
        "cross_checked_fields": checked,
        "not_declared_by_source": not_declared,
    }


def _negative_missing_evidence_set(*, root_lock: dict, doc_dir: pathlib.Path,
                                   locked: dict, expected_identity: dict,
                                   expected_alignment: dict) -> dict:
    """§18.14.2-8：冻结 run 内**无 Evidence 集**的文档只能是 typed fail-closed 负例。

    它**不得**生成 `SpanBuildSnapshot`，也不得被计作正向第四文档。这里先独立核对
    冻结产物的"0 block / evidence_set_version=null"，再尝试签发 pinned 交接并要求
    它**必须失败**；一次"意外成功"或"未分类的失败"同样 fail-closed。
    """
    step = 8
    declared_zero = (expected_alignment.get("block_count") == 0
                     and expected_alignment.get("evidence_set_version") is None
                     and not expected_alignment.get("terminal_schema_versions"))
    if not declared_zero:
        raise StepFailure(
            step, "信任锚没有把该文档声明为『0 block 且无 evidence_set_version』；"
                  "负例前提不成立，不得按负例处理（fail-closed）",
            document_id=NEGATIVE_DOCUMENT_ID, field="expected_alignment")
    pdf_path, pdf_relpath, expected_pdf_sha = _locked_pdf_path(
        locked=locked, trust_root=root_lock, expected_identity=expected_identity,
        document_id=NEGATIVE_DOCUMENT_ID)
    layout, _ = _restore_layout_and_outline(
        step=step, document_id=NEGATIVE_DOCUMENT_ID, doc_dir=doc_dir, locked=locked,
        expected_identity=expected_identity, expected_alignment=expected_alignment)
    locked_root = dict(root_lock)
    locked_root["pdf_sha256"] = expected_pdf_sha
    locked_root["fixture_file_sha256"] = root_lock["trust_root_file_sha256"]
    raised: BaseException | None = None
    probes: list = []
    try:
        # 主探针：走**生产路径**（`frozen_rows=None`），由 Evidence 权威自己拒绝
        # "没有 status='current' 的 evidence set"；不得注入人为空输入来制造失败。
        SB._issue_pinned_ts3_handoff(
            raw_pdf=pdf_path, expected_layout=layout,
            company_id=expected_identity["company_id"],
            document_id=NEGATIVE_DOCUMENT_ID, root_lock=locked_root)
    except Exception as e:  # noqa: BLE001 - 负例就是"必须抛"
        raised = e
    if raised is None:
        raise StepFailure(
            step, "无 Evidence 集的文档**没有** fail-closed，而是签发出了 pinned 交接；"
                  "缺源被当成可对齐输入（P0，fail-closed）",
            document_id=NEGATIVE_DOCUMENT_ID, field="negative_branch")
    kind = _classify_negative(raised)
    probes.append({"probe": "production_path_frozen_rows_none",
                   "raised_exception_type": type(raised).__name__,
                   "raised_message": str(raised),
                   "classified_as": kind})
    if kind == NEGATIVE_MISSING_EVIDENCE_SET:
        # 次探针：即使传入冻结 rows（此处为空），也必须同样拒绝。
        try:
            SB._issue_pinned_ts3_handoff(
                raw_pdf=pdf_path, expected_layout=layout,
                company_id=expected_identity["company_id"],
                document_id=NEGATIVE_DOCUMENT_ID, root_lock=locked_root,
                frozen_rows=())
            probes.append({"probe": "frozen_rows_empty", "raised_exception_type": None,
                           "raised_message": None, "classified_as": "unexpected_success"})
            raise StepFailure(
                step, "传入空冻结 rows 时**没有** fail-closed（P0，fail-closed）",
                document_id=NEGATIVE_DOCUMENT_ID, field="negative_branch")
        except StepFailure:
            raise
        except Exception as e2:  # noqa: BLE001
            probes.append({"probe": "frozen_rows_empty",
                           "raised_exception_type": type(e2).__name__,
                           "raised_message": str(e2),
                           "classified_as": _classify_negative(e2)})
    if kind != NEGATIVE_MISSING_EVIDENCE_SET:
        raise StepFailure(
            step, f"负例以**未分类**的方式失败：{type(raised).__name__}: {raised}；"
                  f"未分类失败不得被当作预期负例（fail-closed）",
            document_id=NEGATIVE_DOCUMENT_ID, field="negative_classification")
    return {
        "document_key": NEGATIVE_DOCUMENT_KEY,
        "document_id": NEGATIVE_DOCUMENT_ID,
        "scope": "pinned_acceptance/historical_run",
        "source_pdf_relpath": pdf_relpath,
        "source_pdf_sha256": expected_pdf_sha,
        "negative_kind": kind,
        "expected_by_trust_root": {
            "block_count": expected_alignment.get("block_count"),
            "evidence_set_version": expected_alignment.get("evidence_set_version"),
        },
        "raised_exception_type": type(raised).__name__,
        "raised_message": str(raised),
        "raised_message_sha256": sha256_canonical(str(raised)),
        "probes": probes,
        "span_build_snapshot_created": False,
        "counted_as_positive_document": False,
    }


def _classify_negative(exc: BaseException) -> str:
    """把负例失败分类（**窄口径**：只有确属缺源才允许被记住为预期负例）。"""
    message = str(exc)
    signatures = (
        "冻结 rows 必须为非空序列",
        "没有 status='current' 的 evidence set",
        "空集合不得被表述为对齐输入",
        "一个成员都没有",
    )
    for signature in signatures:
        if signature in message:
            return NEGATIVE_MISSING_EVIDENCE_SET
    return "unclassified"


def _issue_real_document_handoff(*, doc_dir: pathlib.Path, locked: dict,
                                 expected_identity: dict, expected_alignment: dict,
                                 root_lock: dict) -> tuple:
    """三份真实文档：还原终态 → pinned 交接（禁止重跑对齐）。"""
    document_id = expected_identity["document_id"]
    layout, outline_raw = _restore_layout_and_outline(
        step=2, document_id=document_id, doc_dir=doc_dir, locked=locked,
        expected_identity=expected_identity, expected_alignment=expected_alignment)
    frozen = _read_json(doc_dir / "normalization_alignment.json",
                        "冻结 normalization_alignment.json")
    if not isinstance(frozen, dict):
        raise StepFailure(3, "冻结 normalization_alignment.json 顶层必须为对象",
                          document_id=document_id, field="normalization_alignment")
    _cross_check_alignment_numbers(step=3, document_id=document_id, frozen=frozen,
                                   expected=expected_alignment)
    pdf_path, pdf_relpath, expected_pdf_sha = _locked_pdf_path(
        locked=locked, trust_root=root_lock, expected_identity=expected_identity,
        document_id=document_id)
    locked_root = dict(root_lock)
    locked_root["pdf_sha256"] = expected_pdf_sha
    locked_root["fixture_file_sha256"] = root_lock["trust_root_file_sha256"]
    try:
        # §18.14.2-3：终态由冻结 rows 还原（issuer `vaip-1`）；**不重跑** align_*。
        handoff = SB._issue_pinned_ts3_handoff(
            raw_pdf=pdf_path, expected_layout=layout,
            company_id=expected_identity["company_id"], document_id=document_id,
            root_lock=locked_root, frozen_rows=tuple(frozen["rows"]))
    except Exception as e:  # noqa: BLE001 - 任一步失败都必须点名
        raise StepFailure(
            4, f"pinned 交接签发失败（版式 / Evidence 权威 / 对齐终态 / 结构终态之一"
               f"未闭合）：{type(e).__name__}: {e}（fail-closed）",
            document_id=document_id, field="pinned_handoff") from e
    cross = _cross_check_evidence_identity(step=4, document_id=document_id,
                                           handoff=handoff, expected=expected_alignment)
    # §18.14.2-6：**重建的** canonical outline 必须与冻结产物**全对象相等**。
    rebuilt_outline = handoff.document_outline.to_dict()
    if canonical_json(rebuilt_outline) != canonical_json(outline_raw):
        raise StepFailure(
            6, f"由原 PDF 重建的 DocumentOutline 与冻结产物不等（首个差异字段："
               f"{_first_differing_path(rebuilt_outline, outline_raw)}）；"
               f"canonical 全对象相等性不成立（fail-closed）",
            document_id=document_id, field="document_outline_canonical_equality")
    return handoff, layout, {
        "page_layout_id": layout.page_layout_id,
        "source_pdf_relpath": pdf_relpath,
        "source_pdf_sha256": expected_pdf_sha,
        "outline_id": outline_raw.get("outline_id"),
        "outline_unassigned_count": len(outline_raw.get("unassigned") or []),
        "outline_canonical_equality_with_frozen": True,
        "outline_rebuilt_content_fingerprint":
            handoff.document_outline.content_fingerprint,
        "frozen_alignment_rows": len(frozen["rows"]),
        "alignment_issuer_version": handoff.alignment.issuer_version,
        "handoff_issuer_version": handoff.issuer_version,
        "handoff_scope": handoff.issuer_scope,
        "handoff_source_kind": handoff.source_kind,
        "handoff_identity": handoff.handoff_identity,
        "structure_snapshot_id": handoff.structure_snapshot.structure_snapshot_id,
        "structure_content_fingerprint":
            handoff.structure_snapshot.content_fingerprint,
        "policy_id": handoff.qualification_policy.policy_id,
        "policy_authority_fingerprint":
            handoff.policy_provider_authority_fingerprint,
        "authority_fingerprints": handoff.authority_fingerprints(),
        "evidence_cross_check": cross,
    }


def _locked_pdf_path(*, locked: dict, trust_root: dict, expected_identity: dict,
                     document_id: str) -> tuple:
    """原 PDF 的**受信定位**：路径只是定位符，`sha256` / `size` 在信任锚里。

    三份真实文档用锁内 `source_pdf_*`；冻结 run 内的无 Evidence 集负例用信任锚顶层的
    `fixture_bond_pdf_*`。两条记录都必须与 `expected_identity.input_sha256` 自洽。
    """
    rel = locked.get("source_pdf_relpath")
    locked_sha = locked.get("source_pdf_sha256")
    locked_size = locked.get("source_pdf_size")
    if rel is None:
        rel = trust_root.get("fixture_bond_pdf_relpath")
        locked_sha = trust_root.get("fixture_bond_pdf_sha256")
        locked_size = trust_root.get("fixture_bond_pdf_size")
    if not isinstance(rel, str) or rel == "":
        raise StepFailure(2, "信任锚未给出原 PDF 的受信路径（fail-closed）",
                          document_id=document_id, field="source_pdf_relpath")
    if locked_sha != expected_identity.get("input_sha256"):
        raise StepFailure(
            2, f"信任锚内部不自洽：source_pdf_sha256 {locked_sha!r} != "
               f"expected_identity.input_sha256 "
               f"{expected_identity.get('input_sha256')!r}（fail-closed）",
            document_id=document_id, field="source_pdf_sha256")
    path = pathlib.Path(rel)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        raise StepFailure(2, f"受信原 PDF 缺失：{path}（fail-closed）",
                          document_id=document_id, field="source_pdf_relpath")
    actual_sha = sha256_file(path)
    if actual_sha != locked_sha:
        raise StepFailure(
            2, f"原 PDF 的 sha256 与信任锚不一致：{locked_sha} -> {actual_sha}"
               f"（fail-closed）", document_id=document_id, field="source_pdf_sha256")
    actual_size = path.stat().st_size
    if actual_size != locked_size:
        raise StepFailure(
            2, f"原 PDF 大小与信任锚不一致：{locked_size} -> {actual_size}"
               f"（fail-closed）", document_id=document_id, field="source_pdf_size")
    return path, rel, actual_sha


def _bind_fixture_document(*, trust_root: dict) -> tuple:
    """§18.14.2-9：版本化非 300750 正向 fixture 的 pinned/versioned_fixture 交接。

    **不是** testing scope：它由 fixture-root issuer 逐成员哈希核验后签发，走与三份
    真实文档相同的 TS4 私有纯构建 / 验证核；且**不写**任何 `data/*.db`。
    """
    step = 9
    try:
        fixture_root = EG.load_fixture_root()
    except Exception as e:  # noqa: BLE001
        raise StepFailure(step, f"正向 fixture manifest 不可读：{type(e).__name__}: {e}",
                          field="fixture_manifest") from e
    # `fixture_stage` 声明的是这份夹具**当初被冻结/批准时**的阶段，不是本次运行的阶段：
    # TS4-B 要用同一份夹具（其 manifest 与 SHA 已被信任锚钉死、不得改写）走 B 口径重跑。
    # 因此这里只拒绝未登记的值，不要求它与当前阶段相等；两者都逐字写入产物。
    if fixture_root.get("fixture_stage") not in ("TS4-A", "TS4-B"):
        raise StepFailure(step, f"fixture_stage 必须属于 ('TS4-A', 'TS4-B')，得到 "
                                f"{fixture_root.get('fixture_stage')!r}（fail-closed）",
                          field="fixture_stage")
    members = fixture_root.get("members")
    if not isinstance(members, list) or not members:
        raise StepFailure(step, "fixture manifest 的 members 必须为非空列表",
                          field="members")
    member_identity = []
    for member in members:
        relpath = member.get("relpath")
        path = EG.fixture_root_dir() / str(relpath)
        _need_step_sha256(step, f"fixture 成员 {relpath}", path, member.get("sha256"),
                          field=f"members[{relpath}]")
        if path.stat().st_size != member.get("size"):
            raise StepFailure(step, f"fixture 成员 {relpath} 大小不符（fail-closed）",
                              field=f"members[{relpath}]")
        member_identity.append({"relpath": relpath, "role": member.get("role"),
                                "sha256": member.get("sha256"),
                                "size": member.get("size")})
    source_pdf = fixture_root.get("source_pdf") or {}
    pdf_path = REPO_ROOT / str(source_pdf.get("relpath") or "")
    _need_step_sha256(step, "fixture 原 PDF", pdf_path,
                      fixture_root.get("source_pdf_sha256"), field="source_pdf")
    if pdf_path.stat().st_size != source_pdf.get("size"):
        raise StepFailure(step, "fixture 原 PDF 大小与 manifest 不符（fail-closed）",
                          field="source_pdf.size")
    layout_path = EG.fixture_root_dir() / "page_layout.json"
    try:
        expected_layout = S.PageLayout.from_dict(
            _read_json(layout_path, "fixture page_layout.json"))
    except StepFailure:
        raise
    except Exception as e:  # noqa: BLE001
        raise StepFailure(step, f"fixture page_layout.json 无法还原为 PageLayout：{e!r}",
                          field="page_layout") from e
    for field, mine in (("page_layout_id", expected_layout.page_layout_id),
                        ("document_version", expected_layout.document_version),
                        ("company_id", expected_layout.company_id),
                        ("document_id", expected_layout.document_id)):
        if mine != fixture_root.get(field):
            raise StepFailure(step, f"fixture 成员版式的 {field} 与 manifest 不一致："
                                    f"{fixture_root.get(field)!r} -> {mine!r}"
                                    f"（fail-closed）", field=field)
    company_id = fixture_root["company_id"]
    document_id = fixture_root["document_id"]
    try:
        handoff = SB._issue_fixture_ts3_handoff(
            raw_pdf=pdf_path, expected_layout=expected_layout, company_id=company_id,
            document_id=document_id, fixture_root=fixture_root)
    except Exception as e:  # noqa: BLE001
        raise StepFailure(
            step, f"versioned_fixture 交接签发失败：{type(e).__name__}: {e}"
                  f"（fail-closed）", field="fixture_handoff") from e
    if (handoff.issuer_scope != "pinned_acceptance"
            or handoff.source_kind != EG.FIXTURE_SOURCE_KIND):
        raise StepFailure(
            step, f"fixture 交接必须为 pinned_acceptance/{EG.FIXTURE_SOURCE_KIND}，得到 "
                  f"{handoff.issuer_scope!r}/{handoff.source_kind!r}（fail-closed）",
            field="handoff_scope")
    expected_alignment = {
        "block_count": fixture_root.get("evidence_block_count"),
        "evidence_set_version": fixture_root.get("evidence_set_version"),
        "member_identity_sha256": fixture_root.get("member_identity_sha256"),
    }
    # fixture manifest 只声明这三个字段；其余（fingerprint / snapshot_version /
    # status）**不假装已核对**，显式记为 not_declared_by_source。
    cross = _cross_check_evidence_identity(
        step=step, document_id=document_id, handoff=handoff, expected=expected_alignment,
        declared_fields={"block_count", "evidence_set_version",
                         "member_identity_sha256"})
    declared = fixture_root.get("ts4_a_binding") or {}
    return handoff, expected_layout, {
        "document_key": FIXTURE_DOCUMENT_KEY,
        "document_id": document_id,
        "company_id": company_id,
        "document_version": expected_layout.document_version,
        "fixture_kind": fixture_root.get("fixture_kind"),
        "fixture_stage": fixture_root.get("fixture_stage"),
        "fixture_manifest_sha256": fixture_root.get("fixture_file_sha256"),
        "fixture_manifest_relpath": f"{EG.FIXTURE_ROOT_RELPATH}/"
                                    f"{EG.FIXTURE_MANIFEST_FILENAME}",
        "members": member_identity,
        "member_identity_sha256": fixture_root.get("member_identity_sha256"),
        "source_pdf_relpath": source_pdf.get("relpath"),
        "source_pdf_sha256": fixture_root.get("source_pdf_sha256"),
        "declared_binding_state": declared.get("binding_state"),
        "declared_binding_note": declared.get("note"),
        "page_layout_id": expected_layout.page_layout_id,
        "handoff_issuer_version": handoff.issuer_version,
        "handoff_scope": handoff.issuer_scope,
        "handoff_source_kind": handoff.source_kind,
        "handoff_identity": handoff.handoff_identity,
        "structure_snapshot_id": handoff.structure_snapshot.structure_snapshot_id,
        "structure_content_fingerprint":
            handoff.structure_snapshot.content_fingerprint,
        "policy_id": handoff.qualification_policy.policy_id,
        "policy_authority_fingerprint":
            handoff.policy_provider_authority_fingerprint,
        "authority_fingerprints": handoff.authority_fingerprints(),
        "evidence_cross_check": cross,
    }


def bind_all() -> Bindings:
    """§18.14.2-1…9 全链绑定。任一步失败即 `StepFailure`，不产生任何结果目录。"""
    trust_root = load_trust_root()
    frozen_relpath = trust_root.get("frozen_run_relpath")
    if not isinstance(frozen_relpath, str) or frozen_relpath == "":
        raise StepFailure(1, "信任锚缺 frozen_run_relpath", field="frozen_run_relpath")
    frozen_run_dir = REPO_ROOT / frozen_relpath
    if not frozen_run_dir.is_dir():
        raise StepFailure(1, f"冻结 run 目录不存在：{frozen_run_dir}（fail-closed）",
                          field="frozen_run_relpath")
    # —— 第 1 步：pinned root、manifest / index 的哈希与读回（T34 / T35）。
    pinned = trust_root.get("pinned_root_policy") or {}
    _need_step_sha256(1, "冻结 run_manifest.json", frozen_run_dir / "run_manifest.json",
                      FROZEN_RUN_MANIFEST_SHA256, field="run_manifest.json")
    _need_step_sha256(1, "冻结 artifact_index.json",
                      frozen_run_dir / "artifact_index.json",
                      FROZEN_ARTIFACT_INDEX_SHA256, field="artifact_index.json")
    if pinned.get("run_manifest.json") != FROZEN_RUN_MANIFEST_SHA256:
        raise StepFailure(1, "信任锚的 pinned_root_policy 与脚本内字面量不一致",
                          field="pinned_root_policy.run_manifest.json")
    if pinned.get("artifact_index.json") != FROZEN_ARTIFACT_INDEX_SHA256:
        raise StepFailure(1, "信任锚的 pinned_root_policy 与脚本内字面量不一致",
                          field="pinned_root_policy.artifact_index.json")
    trusted_files = trust_root.get("trusted_file_sha256") or {}
    for name, entry in sorted(trusted_files.items()):
        _need_step_sha256(1, f"冻结 run 顶层文件 {name}", frozen_run_dir / name,
                          entry.get("sha256"), field=name)
        if (frozen_run_dir / name).stat().st_size != entry.get("size"):
            raise StepFailure(1, f"冻结 run 顶层文件 {name} 大小与信任锚不符",
                              field=name)
    artifact_index = _read_json(frozen_run_dir / "artifact_index.json",
                                "冻结 artifact_index.json")
    if not isinstance(artifact_index, dict) or not isinstance(
            artifact_index.get("files"), list):
        raise StepFailure(1, "冻结 artifact_index.json 结构不符（缺 files 列表）",
                          field="artifact_index.files")
    index_by_path = {e.get("path"): e for e in artifact_index["files"]
                     if isinstance(e, dict)}
    for name, entry in sorted(trusted_files.items()):
        if name in _FROZEN_INDEX_INTERNAL_FILES:
            # 索引**不含自身**（沿用 TS3 约定）：只按信任锚核哈希，不要求在索引里登记。
            continue
        row = index_by_path.get(name)
        if row is None:
            raise StepFailure(1, f"artifact_index.json 未登记冻结 run 顶层文件 {name}",
                              field=name)
        if row.get("sha256") != entry.get("sha256"):
            raise StepFailure(
                1, f"artifact_index.json 与信任锚对 {name} 的哈希不一致（目录内自洽"
                   f"不构成信任，fail-closed）", field=name)
    documents = {}
    handoffs: dict = {}
    layout_restores: dict = {}
    evidence_cross_checks: dict = {}
    notes: list = []
    for company_id, document_id in REAL_DOCUMENTS:
        locked = (trust_root.get("documents") or {}).get(document_id)
        if not isinstance(locked, dict):
            raise StepFailure(1, f"信任锚未固定文档 {document_id}（fail-closed）",
                              document_id=document_id, field="documents")
        expected_identity = locked.get("expected_identity") or {}
        if expected_identity.get("company_id") != company_id:
            raise StepFailure(1, "信任锚里的 company_id 与脚本内文档表不一致",
                              document_id=document_id, field="company_id")
        expected_alignment = locked.get("expected_alignment") or {}
        frozen_dir_identity = _frozen_documents_identity(
            trust_root, artifact_index, document_id)
        declared = locked.get("artifacts") or {}
        for name in sorted(declared):
            if name not in frozen_dir_identity:
                raise StepFailure(1, f"artifact_index.json 未登记 {document_id}/{name}",
                                  document_id=document_id, field=name)
            if frozen_dir_identity[name]["sha256"] != declared[name]["sha256"]:
                raise StepFailure(
                    1, f"{document_id}/{name} 的索引哈希与信任锚不一致（fail-closed）",
                    document_id=document_id, field=name)
        doc_dir = frozen_run_dir / document_id
        handoff, layout, restore = _issue_real_document_handoff(
            doc_dir=doc_dir, locked=locked, expected_identity=expected_identity,
            expected_alignment=expected_alignment, root_lock=trust_root)
        handoffs[document_id] = handoff
        layout_restores[document_id] = restore
        evidence_cross_checks[document_id] = restore["evidence_cross_check"]
        documents[document_id] = {
            "document_key": document_id,
            "document_id": document_id,
            "company_id": company_id,
            "scope": "pinned_acceptance/historical_run",
            "frozen_document_dir": str(frozen_run_dir / document_id),
            "source_pdf_relpath": restore["source_pdf_relpath"],
            "source_pdf_sha256": restore["source_pdf_sha256"],
        }
    # —— 第 8 步：无 Evidence 集的冻结 fixture 只能是 typed fail-closed 负例。
    negative_locked = (trust_root.get("documents") or {}).get(NEGATIVE_DOCUMENT_ID)
    if not isinstance(negative_locked, dict):
        raise StepFailure(8, f"信任锚未固定负例文档 {NEGATIVE_DOCUMENT_ID}（fail-closed）",
                          document_id=NEGATIVE_DOCUMENT_ID, field="documents")
    negative = _negative_missing_evidence_set(
        root_lock=trust_root, doc_dir=frozen_run_dir / NEGATIVE_DOCUMENT_ID,
        locked=negative_locked,
        expected_identity=negative_locked.get("expected_identity") or {},
        expected_alignment=negative_locked.get("expected_alignment") or {})
    # —— 第 9 步：版本化非 300750 正向 fixture。
    fixture_handoff, _, fixture_restore = _bind_fixture_document(trust_root=trust_root)
    handoffs[FIXTURE_DOCUMENT_KEY] = fixture_handoff
    layout_restores[FIXTURE_DOCUMENT_KEY] = fixture_restore
    evidence_cross_checks[FIXTURE_DOCUMENT_KEY] = fixture_restore["evidence_cross_check"]
    documents[FIXTURE_DOCUMENT_KEY] = {
        "document_key": FIXTURE_DOCUMENT_KEY,
        "document_id": fixture_restore["document_id"],
        "company_id": fixture_restore["company_id"],
        "scope": f"pinned_acceptance/{EG.FIXTURE_SOURCE_KIND}",
        "frozen_document_dir": None,
        "source_pdf_relpath": fixture_restore["source_pdf_relpath"],
    }
    expected_keys = set(POSITIVE_DOCUMENT_ORDER)
    if set(handoffs) != expected_keys:
        raise StepFailure(
            9, f"正向交接集合与固定文档序不一致：{sorted(set(handoffs))} != "
               f"{sorted(expected_keys)}（fail-closed）", field="document_order")
    notes.append("三份真实文档的终态由冻结 normalization_alignment.json 的 rows 还原，"
                 "未调用 align_block / align_evidence_set。")
    notes.append("冻结 run 内的 line_structure_assignment / formal_unassigned / 表格范围 / "
                 "组件 / 覆盖一律未进入正式输入。")
    return Bindings(trust_root=trust_root, frozen_run_dir=frozen_run_dir,
                    documents=documents, handoffs=handoffs, negative=negative,
                    layout_restores=layout_restores,
                    evidence_cross_checks=evidence_cross_checks,
                    fixture=fixture_restore, notes=notes)


# ---------------------------------------------------------------------------
# 3. 正向样本的构建与独立复核
# ---------------------------------------------------------------------------

def build_positive(*, bindings: Bindings, document_key: str, stage: str,
                   results_dir: pathlib.Path) -> dict:
    """构建 + 独立复核 + 落盘一份**完整 canonical** `SpanBuildSnapshot`。"""
    handoff = bindings.handoffs[document_key]
    document_id = handoff.page_layout.document_id
    try:
        snapshot = SB._build_from_pinned_handoff(handoff, stage=STAGE_TOKEN[stage])
    except Exception as e:  # noqa: BLE001
        raise StepFailure(7, f"pinned 域构建失败：{type(e).__name__}: {e}（fail-closed）",
                          document_id=document_id, field="build_span_snapshot") from e
    structure_report = SV.verify_outline_structure_snapshot(handoff)
    if not structure_report["ok"]:
        raise StepFailure(6, f"结构终态复核失败：{structure_report['problems'][:3]}"
                             f"（fail-closed）", document_id=document_id,
                          field="outline_structure_snapshot")
    try:
        verified = SV.verify_span_snapshot(snapshot, handoff)
    except Exception as e:  # noqa: BLE001
        raise StepFailure(7, f"独立重建复核失败（不得仅凭快照自证通过）："
                             f"{type(e).__name__}: {e}（fail-closed）",
                          document_id=document_id, field="verify_span_snapshot") from e
    # 完成资格：TS4-A 恒为 False（`distribution_only`）；TS4-B 才可判定，但"可判定"
    # **不是**任何 span / aspect 的自动完成，也不等于 `set_complete`。
    completion_samples = {}
    for span in snapshot.spans:
        eligible = SV.is_completion_eligible(verified, span.span_id)
        if stage == "TS4-A" and eligible:
            raise StepFailure(
                7, "TS4-A 出现了具备完成资格的 span；本轮不得支持 set_complete"
                   "（P0，fail-closed）", document_id=document_id,
                field="is_completion_eligible")
        completion_samples[span.span_id] = eligible
    canonical = snapshot.to_dict()
    snapshot_path = results_dir / SNAPSHOT_DIRNAME / f"{document_key}.json"
    _write_json(snapshot_path, canonical)
    written = _read_json(snapshot_path, "刚写入的 span 快照")
    if canonical_json(written) != canonical_json(canonical):
        raise StepFailure(7, "落盘后的 span 快照读回与 canonical JSON 不相等",
                          document_id=document_id, field="span_build_snapshot_readback")
    if SV.SpanBuildSnapshot.from_dict(written).content_fingerprint != \
            snapshot.content_fingerprint:
        raise StepFailure(7, "读回的 span 快照 content_fingerprint 不可复现",
                          document_id=document_id, field="content_fingerprint")
    synopsis_validations = []
    span_registry = {s.span_id: s for s in snapshot.spans}
    coverage_registry = {c.span_id: c for c in snapshot.coverages}
    for synopsis in snapshot.synopses:
        validation = SY.verify_synopsis_sources(
            synopsis, span_registry=span_registry,
            coverage_registry=coverage_registry,
            verified_policy=snapshot.qualification_policy)
        synopsis_validations.append(validation.to_dict())
    return {
        "document_key": document_key,
        "document_id": document_id,
        "snapshot": snapshot,
        "verified_identity": verified.identity(),
        "structure_report": structure_report,
        "synopsis_validations": synopsis_validations,
        "snapshot_path": snapshot_path,
        "snapshot_size": snapshot_path.stat().st_size,
        "snapshot_file_sha256": sha256_file(snapshot_path),
        "completion_eligible_span_count": sum(1 for v in completion_samples.values() if v),
        "completion_eligible_span_ids": sorted(
            sid for sid, v in completion_samples.items() if v),
    }


STAGE_TOKEN = {"TS4-A": "distribution_only", "TS4-B": "threshold_enabled"}


# ---------------------------------------------------------------------------
# 4. 机器产物（全部是**验收投影**）
# ---------------------------------------------------------------------------

#: 固定直方图分档（与 factor 取值**无关**：档位是常量，不随因子表变化）。
BAND_WIDTH = 0.05


def _quantize_confidence(value: float) -> float:
    return round(float(value), V.FLOAT_PRECISION)


def _band_label(low: float) -> str:
    high = min(1.0, round(low + BAND_WIDTH, V.FLOAT_PRECISION))
    return f"{low:.2f}-{high:.2f}"


CONFIDENCE_BANDS: tuple[tuple[float, float, str], ...] = tuple(
    (round(i * BAND_WIDTH, V.FLOAT_PRECISION),
     min(1.0, round((i + 1) * BAND_WIDTH, V.FLOAT_PRECISION)),
     _band_label(round(i * BAND_WIDTH, V.FLOAT_PRECISION)))
    for i in range(int(round(1.0 / BAND_WIDTH))))


def _feature_row(document_key: str, disposition) -> dict:
    """一个 disposition 的**冻结 feature 行**（人工门按此重放）。"""
    return {
        "document_key": document_key,
        "disposition_id": disposition.disposition_id,
        "disposition_locator": disposition.disposition_locator,
        "range_kind": disposition.range_kind,
        "node_id": disposition.node_id,
        "unassigned_reason": disposition.unassigned_reason,
        "table_scope": disposition.table_scope,
        "table_reason": disposition.table_reason,
        "start_page": disposition.start_page,
        "start_line": disposition.start_line,
        "end_page": disposition.end_page,
        "end_line": disposition.end_line,
        "line_count": disposition.line_count,
        "tight_char_count": disposition.tight_char_count,
        "left_boundary_cause": disposition.left_boundary_cause,
        "right_boundary_cause": disposition.right_boundary_cause,
        "confidence": disposition.confidence,
        "span_id": disposition.span_id,
        "problems": list(disposition.problems),
    }


def build_confidence_distribution(*, stage: str, policy, samples: list) -> dict:
    """`confidence_distribution.json`（纯函数：由 (document_key, snapshot) 序列复现）。

    `--seal-review` 会用完全相同的入参重算本载荷并比对文件 SHA256，因此它必须是
    **确定性的**：固定文档序、固定分档、固定排序键，不含时间戳与路径。
    """
    histogram: dict[str, int] = {}
    bands: list[dict] = []
    band_rows: list[dict] = []
    cross: dict[tuple, dict] = {}
    feature_rows: list[dict] = []
    per_document: list[dict] = []
    for document_key, snapshot in samples:
        regular = [d for d in snapshot.dispositions if d.range_kind == "regular"]
        rows = [_feature_row(document_key, d) for d in snapshot.dispositions]
        feature_rows.extend(rows)
        for row in rows:
            if row["range_kind"] != "regular":
                continue
            key = str(_quantize_confidence(row["confidence"]))
            histogram[key] = histogram.get(key, 0) + 1
            pair = (row["left_boundary_cause"], row["right_boundary_cause"])
            entry = cross.setdefault(pair, {"left_boundary_cause": pair[0],
                                            "right_boundary_cause": pair[1],
                                            "disposition_count": 0, "line_count": 0,
                                            "tight_char_count": 0})
            entry["disposition_count"] += 1
            entry["line_count"] += row["line_count"]
            entry["tight_char_count"] += row["tight_char_count"]
        per_document.append({
            "document_key": document_key,
            "document_id": snapshot.document_id,
            "disposition_count": len(snapshot.dispositions),
            "regular_disposition_count": len(regular),
            "span_count": len(snapshot.spans),
        })
    for low, high, label in CONFIDENCE_BANDS:
        selected = [r for r in feature_rows
                    if r["range_kind"] == "regular"
                    and _band_membership(r["confidence"], low, high)]
        bands.append({
            "band": label, "low": low, "high": high,
            "disposition_count": len(selected),
            "line_count": sum(r["line_count"] for r in selected),
            "tight_char_count": sum(r["tight_char_count"] for r in selected),
        })
    cross_rows = [cross[k] for k in sorted(cross)]
    return {
        "manual_review_required": True,
        "artifact": "confidence_distribution.json",
        "stage": stage,
        "policy_id": policy.policy_id,
        "policy_key": policy.policy_key,
        "policy_version": policy.policy_version,
        "policy_fingerprint": policy.policy_fingerprint,
        "span_confidence_min": policy.span_confidence_min,
        "completion_enabled": policy.completion_enabled,
        "set_complete_supported": policy.set_complete_supported,
        "band_width": BAND_WIDTH,
        "document_order": [key for key, _ in samples],
        "per_document": per_document,
        "confidence_histogram": histogram,
        "bands": bands,
        "band_note": ("分档是**常量**（与边界因子取值无关）；它描述的是分档内的分布，"
                      "不是『是否达标』的判定。TS4-A 没有阈值。"),
        "boundary_cause_cross_tab": cross_rows,
        "cross_tab_note": ("该交叉表只统计**边界成因配对**的出现次数与体量，完全不含"
                           "因子数值：任何人改动 factor 表都不会改变这张表。"),
        "disposition_feature_rows": feature_rows,
        "feature_row_set_fingerprint": sha256_canonical(feature_rows),
        "regular_disposition_count": sum(1 for r in feature_rows
                                         if r["range_kind"] == "regular"),
    }


def _band_membership(value: float, low: float, high: float) -> bool:
    quantized = _quantize_confidence(value)
    if high >= 1.0:
        return low <= quantized <= 1.0
    return low <= quantized < high


def build_snapshot_aggregate(*, stage: str, samples: list, negative: dict,
                             fixture: dict) -> dict:
    """`span_snapshot_aggregate.json`：**固定文档序**的聚合投影。"""
    entries = []
    for document_key, snapshot in samples:
        entries.append({
            "document_key": document_key,
            "document_id": snapshot.document_id,
            "document_version": snapshot.document_version,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_locator": snapshot.snapshot_locator,
            "content_fingerprint": snapshot.content_fingerprint,
            "input_fingerprint": snapshot.input_fingerprint,
            "span_count": len(snapshot.spans),
            "disposition_count": len(snapshot.dispositions),
            "component_count": len(snapshot.components),
            "coverage_count": len(snapshot.coverages),
            "synopsis_count": len(snapshot.synopses),
            "terminal_count": snapshot.terminal_count,
            "inherited_unassigned_span_count": len(
                snapshot.inherited_unassigned_span_ids),
            "conserved": snapshot.conservation.conserved,
            "conservation_gap_count": len(snapshot.conservation.gaps),
        })
    return {
        "manual_review_required": True,
        "artifact": "span_snapshot_aggregate.json",
        "stage": stage,
        "document_order": [key for key, _ in samples],
        "documents": entries,
        "document_count": len(entries),
        "totals": {
            "span_count": sum(e["span_count"] for e in entries),
            "disposition_count": sum(e["disposition_count"] for e in entries),
            "component_count": sum(e["component_count"] for e in entries),
            "coverage_count": sum(e["coverage_count"] for e in entries),
            "synopsis_count": sum(e["synopsis_count"] for e in entries),
            "terminal_count": sum(e["terminal_count"] for e in entries),
            "conservation_gap_count": sum(e["conservation_gap_count"] for e in entries),
        },
        "negative_document": negative,
        "versioned_fixture_identity": {
            "document_key": FIXTURE_DOCUMENT_KEY,
            "fixture_manifest_sha256": fixture.get("fixture_manifest_sha256"),
            "member_identity_sha256": fixture.get("member_identity_sha256"),
        },
        "aggregate_fingerprint": sha256_canonical(entries),
    }


def _span_snapshot_index_entry(*, document_key: str, built: dict) -> dict:
    snapshot = built["snapshot"]
    return {
        "document_key": document_key,
        "document_id": snapshot.document_id,
        "file": f"{SNAPSHOT_DIRNAME}/{document_key}.json",
        "size": built["snapshot_size"],
        "sha256": built["snapshot_file_sha256"],
        "snapshot_id": snapshot.snapshot_id,
        "content_fingerprint": snapshot.content_fingerprint,
        "input_fingerprint": snapshot.input_fingerprint,
        "counts": {
            "spans": len(snapshot.spans),
            "dispositions": len(snapshot.dispositions),
            "components": len(snapshot.components),
            "coverages": len(snapshot.coverages),
            "synopses": len(snapshot.synopses),
            "terminals": snapshot.terminal_count,
            "inherited_unassigned_span_ids":
                len(snapshot.inherited_unassigned_span_ids),
        },
        "verification_fingerprint": built["verified_identity"]["verification_fingerprint"],
        "issuer_scope": built["verified_identity"]["issuer_scope"],
        "source_kind": built["verified_identity"]["source_kind"],
    }


def build_structure_snapshot_index(bindings: Bindings, samples: list) -> dict:
    entries = []
    for document_key, _snapshot in samples:
        handoff = bindings.handoffs[document_key]
        structure = handoff.structure_snapshot
        report = SV.verify_outline_structure_snapshot(handoff)
        entries.append({
            "document_key": document_key,
            "document_id": structure.document_id,
            "document_version": structure.document_version,
            "structure_snapshot_id": structure.structure_snapshot_id,
            "content_fingerprint": structure.content_fingerprint,
            "line_state_count": len(structure.line_states),
            "page_layout_id": structure.page_layout_id,
            "outline_id": structure.outline_id,
            "verify_ok": report["ok"],
            "verify_problem_count": report["problem_count"],
            "verify_problems": report["problems"],
        })
    return {
        "manual_review_required": True,
        "artifact": "structure_snapshot_index.json",
        "document_order": [key for key, _ in samples],
        "documents": entries,
    }


def build_span_dispositions(samples: list) -> dict:
    return {
        "manual_review_required": True,
        "artifact": "span_dispositions.json",
        "document_order": [key for key, _ in samples],
        "documents": [{
            "document_key": key,
            "document_id": snapshot.document_id,
            "dispositions": [d.to_dict() for d in snapshot.dispositions],
        } for key, snapshot in samples],
    }


def build_span_coverage(samples: list) -> dict:
    return {
        "manual_review_required": True,
        "artifact": "span_coverage.json",
        "document_order": [key for key, _ in samples],
        "documents": [{
            "document_key": key,
            "document_id": snapshot.document_id,
            "coverages": [c.to_dict() for c in snapshot.coverages],
        } for key, snapshot in samples],
    }


def build_conservation(samples: list) -> dict:
    """三级守恒：逐条列出缺口（**不聚合、不掩盖**）。"""
    documents = []
    for key, snapshot in samples:
        conservation = snapshot.conservation
        documents.append({
            "document_key": key,
            "document_id": snapshot.document_id,
            "conserved": conservation.conserved,
            "conservation_id": conservation.conservation_id,
            "document_layer": [b.to_dict() for b in conservation.document_layer],
            "body_layer": [b.to_dict() for b in conservation.body_layer],
            "evidence_layer": [r.to_dict() for r in conservation.evidence_layer],
            "gaps": [g.to_dict() for g in conservation.gaps],
            "gap_count": len(conservation.gaps),
        })
    return {
        "manual_review_required": True,
        "artifact": "conservation.json",
        "document_order": [key for key, _ in samples],
        "documents": documents,
        "documents_not_conserved": sorted(
            d["document_key"] for d in documents if not d["conserved"]),
        "total_gap_count": sum(d["gap_count"] for d in documents),
    }


def build_synopsis(built_samples: list) -> dict:
    documents = []
    for built in built_samples:
        snapshot = built["snapshot"]
        documents.append({
            "document_key": built["document_key"],
            "document_id": snapshot.document_id,
            "synopses": [n.to_dict() for n in snapshot.synopses],
            "source_validations": built["synopsis_validations"],
            "validation_problem_count": sum(
                len(v.get("problems", [])) for v in built["synopsis_validations"]),
        })
    return {
        "manual_review_required": True,
        "artifact": "synopsis.json",
        "document_order": [d["document_key"] for d in documents],
        "documents": documents,
        "note": ("简介只用于**导航**，不作证据；每条来源都经 `verify_synopsis_sources` "
                 "独立重算（来源序、切片逐字、有效可引用区间）。"),
    }


def build_component_rows(built_samples: list) -> list:
    rows = []
    for built in built_samples:
        snapshot = built["snapshot"]
        for index, component in enumerate(snapshot.components):
            row = {"document_key": built["document_key"],
                   "document_id": snapshot.document_id, "component_index": index}
            row.update(component.to_dict())
            rows.append(row)
    return rows


def build_qualification_policy(stage: str, policy) -> dict:
    summary = SP.policy_registry_summary()
    fingerprint = SP.policy_provider_authority_fingerprint(policy)
    return {
        "manual_review_required": True,
        "artifact": "qualification_policy.json",
        "stage": stage,
        "policy": policy.to_dict(),
        "policy_provider_authority_fingerprint": fingerprint,
        "policy_registry_summary": summary,
        "factor_table_fingerprint": factor_table_fingerprint(policy),
        "threshold": policy.span_confidence_min,
        "completion_enabled": policy.completion_enabled,
        "set_complete_supported": policy.set_complete_supported,
        "ab_gate_truth_table": SP.ab_gate_truth_table(),
        "approval_authority_fingerprint": (
            SP.approval_authority_fingerprint(SP.load_approval_record())
            if stage == "TS4-B" else None),
        "note": ("TS4-A 唯一 distribution-only authority record：threshold=null、"
                 "completion/set_complete 均为假。任何声称 span 可支撑 `set_complete` "
                 "的解读都与本记录矛盾。"
                 if stage == "TS4-A" else
                 "TS4-B frozen current policy authority record：threshold 与 "
                 "`versions.SPAN_CONFIDENCE_MIN`、frozen 记录、人工门批准值三者一致。"
                 "`completion_enabled=True` 只表示**可以判定**，不表示任何 span / "
                 "aspect / `set_complete` 已经完成。"),
    }


def factor_table_fingerprint(policy) -> str:
    """冻结因子表的 canonical 指纹（**不**采信任何文件自报值）。

    形状与 `SpanQualificationPolicy.factor_entries` 的 canonical `to_dict()` 行一致，
    从而可与版本化 fixture manifest 里声明的 `factor_table_fingerprint` 直接比对。
    """
    return sha256_canonical([row.to_dict() for row in policy.factor_entries])


def build_versions_payload() -> dict:
    return {
        "span_build_snapshot_schema_version": V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION,
        "span_schema_version": V.SPAN_SCHEMA_VERSION,
        "span_builder_version": V.SPAN_BUILDER_VERSION,
        "ts4_body_span_builder_version": V.TS4_BODY_SPAN_BUILDER_VERSION,
        "outline_unassigned_span_builder_version":
            V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION,
        "synopsis_version": V.SYNOPSIS_VERSION,
        "synopsis_schema_version": V.SYNOPSIS_SCHEMA_VERSION,
        "span_qualification_policy_version": V.SPAN_QUALIFICATION_POLICY_VERSION,
        "span_qualification_policy_schema_version":
            V.SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION,
        "body_range_disposition_schema_version":
            V.BODY_RANGE_DISPOSITION_SCHEMA_VERSION,
        "span_evidence_component_schema_version":
            V.SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION,
        "span_citable_coverage_schema_version":
            V.SPAN_CITABLE_COVERAGE_SCHEMA_VERSION,
        "span_conservation_schema_version": V.SPAN_CONSERVATION_SCHEMA_VERSION,
        "outline_structure_snapshot_schema_version":
            V.OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
        "synopsis_source_validation_schema_version":
            V.SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION,
        "verified_span_snapshot_version": V.VERIFIED_SPAN_SNAPSHOT_VERSION,
        "outline_structure_provider_version": V.OUTLINE_STRUCTURE_PROVIDER_VERSION,
        "evidence_gateway_provider_version": V.EVIDENCE_GATEWAY_PROVIDER_VERSION,
        "alignment_terminal_provider_version": V.ALIGNMENT_TERMINAL_PROVIDER_VERSION,
        "qualification_policy_provider_version":
            V.QUALIFICATION_POLICY_PROVIDER_VERSION,
        "verified_page_layout_issuer_version": V.VERIFIED_PAGE_LAYOUT_ISSUER_VERSION,
        "verified_alignment_issuer_version": V.VERIFIED_ALIGNMENT_ISSUER_VERSION,
        "verified_current_evidence_authority_version":
            V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION,
        "verified_ts3_handoff_version": V.VERIFIED_TS3_HANDOFF_VERSION,
        "pinned_page_layout_issuer_version": V.PINNED_PAGE_LAYOUT_ISSUER_VERSION,
        "pinned_alignment_issuer_version": V.PINNED_ALIGNMENT_ISSUER_VERSION,
        "pinned_evidence_authority_version": V.PINNED_EVIDENCE_AUTHORITY_VERSION,
        "pinned_ts3_handoff_version": V.PINNED_TS3_HANDOFF_VERSION,
        "fixture_page_layout_issuer_version": V.FIXTURE_PAGE_LAYOUT_ISSUER_VERSION,
        "fixture_alignment_issuer_version": V.FIXTURE_ALIGNMENT_ISSUER_VERSION,
        "fixture_evidence_authority_version": V.FIXTURE_EVIDENCE_AUTHORITY_VERSION,
        "fixture_ts3_handoff_version": V.FIXTURE_TS3_HANDOFF_VERSION,
        "span_confidence_min": V.SPAN_CONFIDENCE_MIN,
        "aligner_version": V.ALIGNER_VERSION,
        "align_schema_version": V.ALIGN_SCHEMA_VERSION,
        "align_refusal_schema_version": V.ALIGN_REFUSAL_SCHEMA_VERSION,
        "outline_algorithm_version": V.OUTLINE_ALGORITHM_VERSION,
        "outline_schema_version": V.OUTLINE_SCHEMA_VERSION,
        "layout_schema_version": V.LAYOUT_SCHEMA_VERSION,
        "evidence_block_input_version": V.EVIDENCE_BLOCK_INPUT_VERSION,
        "evidence_set_snapshot_version": V.EVIDENCE_SET_SNAPSHOT_VERSION,
        "evidence_set_gateway_version": V.EVIDENCE_SET_GATEWAY_VERSION,
        "normalization_version": V.NORMALIZATION_VERSION,
        "heading_qualification_profile_version":
            V.HEADING_QUALIFICATION_PROFILE_VERSION,
        "table_region_qualification_version": V.TABLE_REGION_QUALIFICATION_VERSION,
        "toc_body_reconciliation_version": V.TOC_BODY_RECONCILIATION_VERSION,
        "policy_decision_review_schema_version":
            POLICY_DECISION_REVIEW_SCHEMA_VERSION,
        "review_attestation_schema_version": REVIEW_ATTESTATION_SCHEMA_VERSION,
    }


def build_machine_index(*, results_dir: pathlib.Path, run_id: str,
                        run_id_source: str, generated_at: str, stage: str,
                        code: dict, versions: dict, inputs: dict,
                        root_identity: dict, policy_identity: dict) -> dict:
    """`machine_artifact_index.json`（不含自身；篡改由逐文件重算发现）。"""
    files = []
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(results_dir).as_posix()
        if rel in EXCLUDED_FROM_MACHINE_INDEX:
            continue
        files.append({"path": rel, "size": path.stat().st_size,
                      "sha256": sha256_file(path)})
    rid = run_id_identity(run_id, source=run_id_source)
    payload = {
        "manual_review_required": True,
        "artifact": "machine_artifact_index.json",
        "scope": ("TS4-A 机器阶段产物清单（不含索引自身、人工模板与 attestation）；"
                  "篡改由逐文件重算发现"),
        "run_id": run_id,
        "run_id_identity": rid,
        "generated_at_utc": generated_at,
        "stage": stage,
        "versions": versions,
        "code_fingerprint": code,
        "inputs": inputs,
        "root_identity": root_identity,
        "policy_identity": policy_identity,
        "excluded_from_index": list(EXCLUDED_FROM_MACHINE_INDEX),
        "file_count": len(files),
        "files": files,
    }
    payload["index_identity"] = sha256_canonical({
        "run_id": run_id, "run_id_identity": rid, "generated_at_utc": generated_at,
        "stage": stage, "versions": versions, "code_fingerprint": code,
        "inputs": inputs, "root_identity": root_identity,
        "policy_identity": policy_identity, "files": files,
    })
    # 供 run_manifest 绑定的**非循环**身份：索引内容里除去 run_manifest.json 的条目。
    subset = [f for f in files if f["path"] != "run_manifest.json"]
    payload["machine_index_identity"] = sha256_canonical({
        "run_id": run_id, "run_id_identity": rid, "generated_at_utc": generated_at,
        "stage": stage, "versions": versions, "code_fingerprint": code,
        "inputs": inputs, "root_identity": root_identity,
        "policy_identity": policy_identity, "files": subset,
    })
    return payload


def verify_machine_index(results_dir: pathlib.Path, index: dict) -> dict:
    """按索引逐文件重算（缺文件 / 多文件 / 错 hash），**只读**。"""
    problems: list = []
    listed = {entry["path"]: entry for entry in index.get("files", [])}
    actual = set()
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(results_dir).as_posix()
        actual.add(rel)
        if rel in EXCLUDED_FROM_MACHINE_INDEX:
            continue
        entry = listed.get(rel)
        if entry is None:
            problems.append(f"机器索引未登记的文件：{rel}")
            continue
        if entry.get("size") != path.stat().st_size:
            problems.append(f"{rel} 大小不符：索引 {entry.get('size')} 实得 "
                            f"{path.stat().st_size}")
        digest = sha256_file(path)
        if entry.get("sha256") != digest:
            problems.append(f"{rel} SHA256 不符：索引 {entry.get('sha256')} 实得 "
                            f"{digest}")
    for rel in sorted(set(listed) - actual):
        problems.append(f"机器索引登记但文件缺失：{rel}")
    subset = [entry for entry in index.get("files", [])
              if entry["path"] != "run_manifest.json"]
    recomputed = sha256_canonical({
        "run_id": index.get("run_id"),
        "run_id_identity": index.get("run_id_identity"),
        "generated_at_utc": index.get("generated_at_utc"),
        "stage": index.get("stage"), "versions": index.get("versions"),
        "code_fingerprint": index.get("code_fingerprint"),
        "inputs": index.get("inputs"), "root_identity": index.get("root_identity"),
        "policy_identity": index.get("policy_identity"), "files": subset})
    if recomputed != index.get("machine_index_identity"):
        problems.append("machine_index_identity 不可复现（索引内容被改动）")
    return {"ok": not problems, "problems": problems,
            "file_count": len(actual), "index_file_count": len(listed)}


_RUN_ID_SEMANTICS = (
    "run_id 只是 opaque 标识；即使它长得像时间戳也不携带时间语义，"
    "run 的真实时刻一律取 generated_at_utc。")


def run_id_identity(run_id: str, *, source: str) -> dict:
    """`--run-id` 是 opaque 标签：只有 runner 自己生成的才带时间语义。"""
    return {
        "run_id": run_id,
        "source": source,
        "looks_like_timestamp": _looks_like_timestamp(run_id),
        "carries_utc_semantics": source == "generated_utc",
        "semantics": _RUN_ID_SEMANTICS,
    }


def _looks_like_timestamp(run_id: str) -> bool:
    import re as _re
    return bool(_re.search(r"\d{8}T\d{6}Z", run_id or ""))


# ---------------------------------------------------------------------------
# 5. 人工模板（**空白**；不替用户 / Codex 填写任何裁决）
# ---------------------------------------------------------------------------

def manual_review_template(*, run_id: str, generated_at: str, stage: str,
                           negative: dict, fixture: dict, policy=None,
                           qualification_binding: dict | None = None) -> str:
    """按**阶段**确定性生成**空白**人工验收模板：A / B 语义不得互相串用。

    - TS4-A：threshold 为 None、completion 恒为 False（不得支撑 `set_complete`），
      结论由用户 + Codex 填写后经 `--seal-review` 封存进 `review_attestation.json`；
    - TS4-B：写明真实阈值与 `threshold_enabled`（completion 判定能力**已启用**）、
      阈值只是**必要条件**、本 run 只**引用**已封存 A attestation 且**不再第二次
      seal**、本模板用于用户 + Codex 对 TS4-B 新真实产物作**独立**验收；
      空白模板仍不得被表述为已人工验收通过。
    """
    if stage == "TS4-A":
        if policy is not None and (policy.span_confidence_min is not None
                                   or policy.completion_enabled
                                   or policy.set_complete_supported):
            raise StepFailure(
                0, "TS4-A 的人工模板只接受 distribution_only 策略（fail-closed）",
                field="manual_review_template")
        header = [
            f"# TS4-A 人工验收清单（run `{run_id}`）",
            "",
            f"- 生成时刻（UTC）：{generated_at}",
            f"- 阶段：{stage}（`SPAN_CONFIDENCE_MIN is None`；completion 恒为 False，"
            f"**不得**支撑 `set_complete`）",
            "- 本文件由 runner 创建为**空白模板**；结论必须由用户 + Codex 逐项填写，"
            "并由 `--seal-review` 封存进 `review_attestation.json`。",
            "- **留空即未复核**；空模板不得被表述为通过。",
        ]
    elif stage == "TS4-B":
        if policy is None or policy.span_confidence_min is None \
                or not policy.completion_enabled or not policy.set_complete_supported:
            raise StepFailure(
                0, "TS4-B 的人工模板要求携带有限阈值且已开启 completion / "
                   "set_complete 的策略（fail-closed）",
                field="manual_review_template")
        binding = qualification_binding if isinstance(qualification_binding, dict) else {}
        sealed_sha = _sealed_sha256(binding.get("bound_review_attestation_sha256"))
        source_a_run_id = binding.get("source_a_run_id")
        if not isinstance(source_a_run_id, str) or not source_a_run_id.strip():
            raise StepFailure(
                0, "TS4-B 的人工模板必须绑定来源 TS4-A run 身份（fail-closed）",
                field="manual_review_template")
        header = [
            f"# TS4-B 人工验收清单（run `{run_id}`）",
            "",
            f"- 生成时刻（UTC）：{generated_at}",
            f"- 阶段：TS4-B（`SPAN_CONFIDENCE_MIN = "
            f"{float(policy.span_confidence_min):g}`；当前策略 `{policy.policy_key}` 为 "
            f"`threshold_enabled`，completion / set_complete 判定能力已启用）。",
            "- 阈值只是**必要条件**：它**不使任何** span / topic / aspect 自动完成；"
            "覆盖、gap、authority 与 `set_complete` 仍按事实与 completion rules 判定。",
            f"- 来源人工门：本 run 绑定 TS4-A 已封存的 `review_attestation.json`"
            f"（SHA256 `{sealed_sha}`，来源 A run `{source_a_run_id}`）。B run 只**引用**"
            f"该封存身份，**不进行第二次 seal**；`sealed=true` 不等于本 run 又产生了"
            f"一份 attestation。",
            "- **本 run 不接受 `--seal-review`**：TS4-B 的 approval / frozen 身份来自 "
            "TS4-A 的一次性封存；复核本 run 只需填写本文件与 `policy_decision.json`。",
            "- 本文件由 runner 创建为**空白模板**；TS4-B 的真实产物必须由用户 + Codex "
            "**独立**逐项验收。",
            "- **留空即未复核**；空模板不得被表述为已人工验收通过。",
        ]
    else:
        raise StepFailure(0, f"未登记的阶段 {stage!r}（fail-closed）",
                          field="manual_review_template")
    lines = header + [
        "",
        "## 1. 主营业务正文是否完整（无缺行、无错切）",
        "", "结论：（待填写：pass / fail / not_reviewed）", "证据：",
        "",
        "## 2. 核心竞争力下的子标题是否被正确分离",
        "（不并入父节点正文、不切断父节点正文）",
        "", "结论：（待填写）", "证据：",
        "",
        "## 3. 主要子公司材料是否**未**串入公司治理或财务风险部分",
        "", "结论：（待填写）", "证据：",
        "",
        "## 4. 财务附注文本与表格是否被隔离",
        "（`table_inside` 全量可见、无一段落入正文 span）",
        "", "结论：（待填写）", "证据：",
        "",
        "## 5. 跨标题 Evidence 是否被拆成多个 provenance 组件区间",
        "（每个落点各有一条组件记录，且**无任何** span 声称独占整条 Evidence）",
        "若真实样本中**未出现**此类 Evidence，必须明确写「未出现」并给出检索口径",
        "（不得以沉默代替结论）。",
        "", "结论：（待填写）", "检索口径：", "证据：",
        "",
        "## 6. 表题 / 单位行是否**未**出现在任何 snippet 中",
        "", "结论：（待填写）", "证据：",
        "",
        "## 7. 非 300750 正向 fixture 与旧无 Evidence 集负例",
        "前者是否走完与真实文档相同的 span/component/coverage/conservation/synopsis",
        "流程；后者是否仍 fail-closed。二者不得互相替代。",
        "",
        f"- 正向 fixture：`{fixture.get('document_id')}`"
        f"（manifest sha256 `{fixture.get('fixture_manifest_sha256')}`）",
        f"- 负例：`{negative.get('document_id')}`，分类 "
        f"`{negative.get('negative_kind')}`，未生成 SpanBuildSnapshot",
        "", "结论：（待填写）", "证据：",
        "",
        "## 8. 全部守恒缺口与不可引用缺口是否**逐条**诚实显示",
        "（不聚合、不掩盖）",
        "", "结论：（待填写）", "证据：",
        "",
        "---",
        "",
        "## 人工结论汇总（请**逐项**填写 `review_verdict`）",
        "",
        "| check_id | verdict（pass / fail / not_reviewed） |",
        "|---|---|",
    ]
    for check_id in REVIEW_CHECK_IDS:
        lines.append(f"| `{check_id}` | not_reviewed |")
    lines += [
        "",
        "## 签署",
        "",
        "- 用户：（待填写）",
        "- Codex：（待填写）",
        "- 日期（UTC）：（待填写）",
        "",
        "> 本文件只承载人工判读；机器产物（`confidence_distribution.json`、"
        "`span_snapshot_aggregate.json`、完整 raw snapshots）是判读的输入，不是结论。",
        "",
    ]
    return "\n".join(lines)


def policy_decision_template(*, run_id: str, generated_at: str, stage: str,
                             policy, run_identity: dict, root_identity: dict,
                             machine_index_identity: str,
                             aggregate_sha256: str, distribution_sha256: str) -> dict:
    """`PolicyDecisionV1(pd-1)` 的**空白**模板（封闭字段，未填处一律 null）。"""
    return {
        "schema_type": "PolicyDecisionV1",
        "review_schema_version": POLICY_DECISION_REVIEW_SCHEMA_VERSION,
        "decision": None,
        "a_run_identity": {
            "run_id": run_id,
            "generated_at_utc": generated_at,
            "stage": stage,
            "run_id_identity": run_identity,
            "machine_index_identity": machine_index_identity,
        },
        "a_root_identity": root_identity,
        "a_machine_index_identity": machine_index_identity,
        "a_aggregate_snapshot_sha256": aggregate_sha256,
        "a_confidence_distribution_sha256": distribution_sha256,
        "factor_entries": [
            {"side": bf.side, "cause": bf.cause, "factor": None}
            for bf in policy.factor_entries
        ],
        "threshold": None,
        "review_verdict": {check_id: "not_reviewed" for check_id in REVIEW_CHECK_IDS},
        "reviewer_roles": list(REVIEWER_ROLES),
        "manual_review_sha256": None,
        "policy_decision_sha256_note": (
            "decision=approve 要求：有限 threshold、完整 factor_entries（12 项全为实数）、"
            "8 项 review_verdict 全为 pass；其余 decision 不得取得 approval eligibility。"
            "本模板中的所有 null / not_reviewed 都是**未复核**，不是通过。"),
    }


# ---------------------------------------------------------------------------
# 6. 一次完整 TS4-A run
# ---------------------------------------------------------------------------

def _identity_payload(bindings: Bindings, stage: str) -> dict:
    return {
        "stage": stage,
        "trust_root": {
            "relpath": TRUST_ROOT_RELPATH,
            "file_sha256": bindings.trust_root["trust_root_file_sha256"],
            "fixture_kind": bindings.trust_root.get("fixture_kind"),
        },
        "frozen_run": {
            "relpath": bindings.trust_root.get("frozen_run_relpath"),
            "run_id": bindings.trust_root.get("run_id"),
            "run_manifest_sha256": FROZEN_RUN_MANIFEST_SHA256,
            "artifact_index_sha256": FROZEN_ARTIFACT_INDEX_SHA256,
            "frozen_versions": bindings.trust_root.get("frozen_versions"),
        },
        "documents": {key: bindings.documents[key] for key in POSITIVE_DOCUMENT_ORDER},
        "negative_document": bindings.negative,
        "non_300750_fixture": {
            "document_key": FIXTURE_DOCUMENT_KEY,
            "document_id": bindings.fixture.get("document_id"),
            "manifest_sha256": bindings.fixture.get("fixture_manifest_sha256"),
            "member_identity_sha256": bindings.fixture.get("member_identity_sha256"),
            "members": bindings.fixture.get("members"),
            "source_pdf_sha256": bindings.fixture.get("source_pdf_sha256"),
        },
        "evidence_cross_checks": bindings.evidence_cross_checks,
        "layout_restores": bindings.layout_restores,
    }


def _run_identity(run_id: str, run_id_source: str, generated_at: str,
                  stage: str) -> dict:
    return {"run_id": run_id, "run_id_source": run_id_source,
            "generated_at_utc": generated_at, "stage": stage}


def execute_run(*, run_id: str | None, run_id_source: str, results_root: pathlib.Path,
                generated_at: str, install_dir: pathlib.Path | None = None) -> int:
    """跑一次完整 TS4 验收（A 或 B）；失败时不留半成品目录。"""
    # 受监管库的**运行前**身份：必须在任何绑定 / 构建之前取，否则"前后相等"是同义反复。
    db_before = snapshot(_dump_watched_dbs())
    code = code_fingerprint()
    if code["missing"]:
        raise StepFailure(
            0, f"代码指纹文件缺失（`CODE_FINGERPRINT_FILES` 必须逐文件可读）："
               f"{code['missing']}（fail-closed）", field="code_fingerprint")
    stage, stage_error = _resolve_stage()
    if stage_error is not None:
        return stage_error
    if stage == "TS4-A":
        policy = SP.resolve_distribution_policy()
        SP.assert_distribution_only(policy)
    else:
        policy = SP.resolve_frozen_policy()
    # "当前究竟处于哪一阶段"在新建入口单点强制；记录自身只对自身阶段负责。
    SP.assert_current_policy(policy)
    bindings = bind_all()
    identity_inputs = _identity_payload(bindings, stage)
    run_identity = _run_identity(run_id or "", run_id_source, generated_at, stage)
    snapshot_dir = results_root / SNAPSHOT_DIRNAME
    created = False
    try:
        snapshot_dir.mkdir(parents=True)
        created = True
        built_samples = []
        for document_key in POSITIVE_DOCUMENT_ORDER:
            built_samples.append(build_positive(
                bindings=bindings, document_key=document_key, stage=stage,
                results_dir=results_root))
        samples = [(b["document_key"], b["snapshot"]) for b in built_samples]
        distribution = build_confidence_distribution(
            stage=stage, policy=policy, samples=samples)
        _write_json(results_root / "confidence_distribution.json", distribution)
        aggregate = build_snapshot_aggregate(
            stage=stage, samples=samples, negative=bindings.negative,
            fixture=bindings.fixture)
        _write_json(results_root / "span_snapshot_aggregate.json", aggregate)
        snapshot_index = {
            "manual_review_required": True,
            "artifact": "span_snapshot_index.json",
            "stage": stage,
            "document_order": [key for key, _ in samples],
            "snapshots": [_span_snapshot_index_entry(document_key=key, built=built)
                          for key, built in zip([k for k, _ in samples], built_samples)],
            "negative_document_key": NEGATIVE_DOCUMENT_KEY,
            "negative_document_snapshot_created": False,
        }
        _write_json(results_root / "span_snapshot_index.json", snapshot_index)
        _write_json(results_root / "qualification_policy.json",
                    build_qualification_policy(stage, policy))
        _write_json(results_root / "span_dispositions.json",
                    build_span_dispositions(samples))
        _write_json(results_root / "span_coverage.json", build_span_coverage(samples))
        _write_json(results_root / "conservation.json", build_conservation(samples))
        _write_json(results_root / "synopsis.json", build_synopsis(built_samples))
        _write_json(results_root / "structure_snapshot_index.json",
                    build_structure_snapshot_index(bindings, samples))
        _write_json_lines(results_root / "span_components.jsonl",
                          build_component_rows(built_samples))
        fixture_binding = {
            "document_key": FIXTURE_DOCUMENT_KEY,
            "declared_binding_state": bindings.fixture.get("declared_binding_state"),
            "declared_note": bindings.fixture.get("declared_binding_note"),
            "bound_run_id": run_id,
            "bound_generated_at_utc": generated_at,
            "machine_index_sha256": None,
            "span_snapshot_aggregate_sha256":
                sha256_file(results_root / "span_snapshot_aggregate.json"),
            "confidence_distribution_sha256":
                sha256_file(results_root / "confidence_distribution.json"),
            "trust_root_file_sha256": bindings.trust_root["trust_root_file_sha256"],
            "review_attestation_sha256": None,
            "note": ("上列身份是**本 run 应写回 fixture manifest 的值**；本 runner 只写"
                     "一个文件，故不就地改写仓库内 fixture（见 runner 汇报）。"),
        }
        # 受监管库的**运行后**身份：全部绑定 / 构建 / 复核都已走完，且之后的 manifest /
        # index 写盘不碰任何库，因此此处即终态。先判后写：只有成立的产物才落盘。
        db_after = snapshot(_dump_watched_dbs())
        immutability = build_database_immutability(before=db_before, after=db_after)
        if not immutability["unchanged"]:
            raise StepFailure(
                0, f"运行前后受监管库身份发生变化（P0，fail-closed）："
                   f"changed={immutability['changed']}；"
                   f"watched_missing={immutability['watched_missing']}",
                field="database_immutability")
        _write_json(results_root / "database_immutability.json", immutability)
        versions = build_versions_payload()
        manifest = _build_run_manifest(
            run_id=run_id, run_id_source=run_id_source, generated_at=generated_at,
            stage=stage, policy=policy, code=code, versions=versions,
            identity_inputs=identity_inputs, bindings=bindings,
            built_samples=built_samples, results_root=results_root,
            fixture_binding=fixture_binding)
        _write_json(results_root / "run_manifest.json", manifest)
        index = build_machine_index(
            results_dir=results_root, run_id=run_id, run_id_source=run_id_source,
            generated_at=generated_at, stage=stage, code=code, versions=versions,
            inputs=identity_inputs,
            root_identity=manifest["root_identity"],
            policy_identity=manifest["policy_identity"])
        _write_json(results_root / "machine_artifact_index.json", index)
        check = verify_machine_index(results_root, index)
        check_payload = {
            "manual_review_required": True,
            "artifact": "machine_artifact_index_check.json",
            "scope": "按 machine_artifact_index.json 逐文件重算的只读核验",
            "ok": check["ok"],
            "problems": check["problems"],
            "file_count": check["file_count"],
            "index_file_count": check["index_file_count"],
            "index_identity": index["index_identity"],
            "machine_index_identity": index["machine_index_identity"],
            "excluded_from_index": list(EXCLUDED_FROM_MACHINE_INDEX),
        }
        _write_json(results_root / "machine_artifact_index_check.json", check_payload)
        if not check["ok"]:
            raise StepFailure(0, f"机器产物索引核验失败：{check['problems'][:5]}"
                                 f"（fail-closed）", field="machine_artifact_index")
        # 人工模板：**空白**，且**不进**机器索引。
        (results_root / "manual_review.md").write_text(
            manual_review_template(run_id=run_id, generated_at=generated_at,
                                   stage=stage, negative=bindings.negative,
                                   fixture=bindings.fixture, policy=policy,
                                   qualification_binding=manifest.get(
                                       "qualification_binding")),
            encoding="utf-8")
        _write_json(results_root / "policy_decision.json",
                    policy_decision_template(
                        run_id=run_id, generated_at=generated_at, stage=stage,
                        policy=policy, run_identity=run_identity,
                        root_identity=manifest["root_identity"],
                        machine_index_identity=index["machine_index_identity"],
                        aggregate_sha256=sha256_file(
                            results_root / "span_snapshot_aggregate.json"),
                        distribution_sha256=sha256_file(
                            results_root / "confidence_distribution.json")))
        return 0
    except BaseException:
        if created and not (results_root / "review_attestation.json").exists():
            shutil.rmtree(results_root, ignore_errors=True)
        raise


def _resolve_stage() -> tuple[str, int | None]:
    """阶段**只**由 `SPAN_CONFIDENCE_MIN` 单点派生（不接受任何 CLI 开关覆盖）。

    TS4-B **不是**"把 A 产物重新解释一遍"：只有在 approval / frozen 资产齐备、且
    `resolve_frozen_policy()`（含注册表钉指纹、派生等值、当前阶段断言）全部通过时，
    才允许以新身份正式重跑。资产未就绪即 fail-closed，不得手写、不得沿用 A 产物。
    """
    table = SP.ab_gate_truth_table()
    stage = "TS4-A" if table["stage"] == "distribution_only" else "TS4-B"
    if stage == "TS4-A":
        if table["span_confidence_min"] is not None or table["completion_enabled"] \
                or table["set_complete_supported"]:
            print(canonical_json({"error": "TS4-A 真值表自相矛盾（fail-closed）",
                                  "ab_gate_truth_table": table}))
            return stage, 3
        return stage, None
    try:
        policy = SP.resolve_frozen_policy()
        SP.assert_current_policy(policy)
    except Exception as e:  # noqa: BLE001 - 任何解析异常都不得变成放行
        print(canonical_json({
            "error": "当前 SPAN_CONFIDENCE_MIN 已非 None（threshold_enabled），但 TS4-B "
                     "的 approval / frozen 资产未就绪或校验失败；TS4-B 必须以由 "
                     "sealed attestation 确定性导出的 frozen policy 新身份重跑，"
                     "不得用 A 产物重新解释，也不得手写 B 资产（fail-closed）",
            "span_confidence_min": table["span_confidence_min"],
            "exception": f"{type(e).__name__}: {e}",
            "ab_gate_truth_table": table}))
        return stage, 3
    return stage, None


#: `review_attestation_identity` 的两份**互斥**阶段说明（不得互相串用）。
_UNSEALED_IDENTITY_NOTE = (
    "人工门：用户 + Codex 填写 `manual_review.md` 与 `policy_decision.json` 后，以 "
    "`--seal-review <A目录>` 追加一次 `review_attestation.json`。在封存之前 "
    "approval_fingerprint 恒为 None，TS4-B approval/frozen asset 不得导出。")
_SEALED_IDENTITY_NOTE = (
    "来源人工门：本 run 所引用的 TS4-A `review_attestation.json` **已由用户 + Codex "
    "一次性封存**。`sealed=true` 只表示「本 run 使用的来源 TS4-A 人工裁决已经封存」，"
    "**不得**解释为「TS4-B 运行又产生了一份新 attestation」：B run 只**引用**其封存"
    "身份（attestation SHA256 + 来源 A run id），**不进行第二次 seal**。该状态仍**不**"
    "证明任何 topic / aspect / set_complete 已完成。")


def _sealed_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 \
            or any(ch not in "0123456789abcdef" for ch in value):
        raise StepFailure(
            0, "已封存 TS4-A attestation 的 SHA256 必须为 64 位小写十六进制，得到 "
               f"{value!r}（fail-closed）", field="review_attestation_identity")
    return value


def review_attestation_identity(stage: str, qualification_binding: dict | None) -> dict:
    """`run_manifest.review_attestation_identity` 的**唯一**构造点（A / B 同形不同值）。

    - TS4-A：本 run 尚未封存 → `sealed=false` / attestation SHA=null /
      `source_a_run_id`=null；A 阶段不得引用任何已封存身份；
    - TS4-B：`sealed=true` 只表示**本 run 使用的来源 TS4-A 人工裁决已经封存**，
      不得解释为"TS4-B 运行又产生了一份新 attestation"。缺 qualification binding /
      attestation SHA / 来源 A run identity 一律 fail-closed——**不得生成看似有效的
      B manifest**。
    """
    if stage == "TS4-A":
        if qualification_binding:
            raise StepFailure(
                0, "TS4-A 的 attestation 身份块不得携带 qualification binding：A 阶段"
                   "没有任何已封存来源可引用（fail-closed）",
                field="review_attestation_identity")
        return {
            "sealed": False,
            "review_attestation_schema_version": REVIEW_ATTESTATION_SCHEMA_VERSION,
            "review_attestation_sha256": None,
            "source_a_run_id": None,
            "policy_decision_review_schema_version":
                POLICY_DECISION_REVIEW_SCHEMA_VERSION,
            "note": _UNSEALED_IDENTITY_NOTE,
        }
    if stage != "TS4-B":
        raise StepFailure(0, f"未登记的阶段 {stage!r}（fail-closed）",
                          field="review_attestation_identity")
    if not isinstance(qualification_binding, dict) or not qualification_binding:
        raise StepFailure(
            0, "TS4-B 缺 qualification binding：无法确定本 run 引用的 TS4-A 已封存 "
               "attestation 身份（fail-closed，不得生成看似有效的 B manifest）",
            field="review_attestation_identity")
    sealed_sha = _sealed_sha256(
        qualification_binding.get("bound_review_attestation_sha256"))
    source_a_run_id = qualification_binding.get("source_a_run_id")
    if not isinstance(source_a_run_id, str) or not source_a_run_id.strip():
        raise StepFailure(
            0, "TS4-B 缺 source A run id：无法确定被批准并作为来源的 TS4-A run 身份，"
               f"得到 {source_a_run_id!r}（fail-closed）",
            field="review_attestation_identity")
    return {
        "sealed": True,
        "review_attestation_schema_version": REVIEW_ATTESTATION_SCHEMA_VERSION,
        "review_attestation_sha256": sealed_sha,
        "source_a_run_id": source_a_run_id,
        "policy_decision_review_schema_version": POLICY_DECISION_REVIEW_SCHEMA_VERSION,
        "note": _SEALED_IDENTITY_NOTE,
    }


def _build_run_manifest(*, run_id: str, run_id_source: str, generated_at: str,
                        stage: str, policy, code: dict, versions: dict,
                        identity_inputs: dict, bindings: Bindings,
                        built_samples: list, results_root: pathlib.Path,
                        fixture_binding: dict) -> dict:
    threshold = policy.span_confidence_min
    approval_fingerprint = None
    qualification_binding = None
    if (stage == "TS4-A") != (threshold is None):
        raise StepFailure(0, f"阶段与阈值的真值表不成立：stage={stage!r} / "
                             f"threshold={threshold!r}（A ⇔ threshold=None，fail-closed）",
                          field="stage_threshold")
    if stage == "TS4-A" and (policy.completion_enabled or policy.set_complete_supported):
        raise StepFailure(0, "TS4-A 不得开启 completion / set_complete（P0，fail-closed）",
                          field="completion_enabled")
    if stage == "TS4-A":
        for built in built_samples:
            if built["completion_eligible_span_count"]:
                raise StepFailure(
                    0, "TS4-A 出现具备完成资格的 span（P0，fail-closed）",
                    document_id=built["document_id"], field="completion_eligibility")
    else:
        # TS4-B：绑定与策略同步发布的 approval / frozen / attestation 三份身份。
        # 这些身份**只是**"当前口径已批准"的记录，不构成任何 topic / aspect 的完成结论。
        approval = SP.load_approval_record()
        approval_fingerprint = SP.approval_authority_fingerprint(approval)
        qualification_binding = {
            "approval_record_filename": SP.APPROVAL_RECORD_FILENAME,
            "approval_authority_fingerprint": approval_fingerprint,
            "approval_authority_fingerprint_recomputed": True,
            "frozen_record_filename": SP.FROZEN_RECORD_FILENAME,
            "frozen_policy_key": SP.FROZEN_POLICY_KEY,
            "frozen_policy_fingerprint": policy.policy_fingerprint,
            "bound_ts4a_snapshot_aggregate_sha256":
                approval["a_aggregate_snapshot_sha256"],
            "bound_distribution_sha256":
                approval["a_confidence_distribution_sha256"],
            "bound_review_attestation_sha256": approval["review_attestation_sha256"],
            "source_a_run_id": approval["a_run_id"],
            "source_attestation_relpath": approval["review_attestation_relpath"],
            "note": ("阈值只是**必要条件**：本 runner 不据此宣称任一 topic / aspect / "
                     "set_complete 已完成；覆盖、gap、authority 仍按事实判定。"),
        }
    return {
        "manual_review_required": True,
        "artifact": "run_manifest.json",
        "stage": stage,
        "run_id": run_id,
        "run_id_identity": run_id_identity(run_id, source=run_id_source),
        "generated_at_utc": generated_at,
        "ts4_scope": (
            "TS4-A：真实验收 runner（只读）。未进入 TS4-B / TS5 / TS6 / TS7。"
            "构建完成不等于 TS4 通过；关闭由用户 + Codex 决定。"
            if stage == "TS4-A" else
            "TS4-B：以 frozen policy 新身份重跑（只读）。未进入 TS5 / TS6 / TS7。"
            "阈值只是必要条件，构建完成不等于任何 topic / aspect 完成；"
            "关闭由用户 + Codex 决定。"),
        "stage_truth_table": SP.ab_gate_truth_table(),
        "qualification_policy": {
            "policy_id": policy.policy_id,
            "policy_key": policy.policy_key,
            "policy_version": policy.policy_version,
            "policy_schema_version": policy.schema_version,
            "policy_fingerprint": policy.policy_fingerprint,
            "stage": policy.stage,
            "threshold": threshold,
            "approval_fingerprint": approval_fingerprint,
            "policy_provider_authority_fingerprint":
                SP.policy_provider_authority_fingerprint(policy),
            "factor_table_fingerprint": factor_table_fingerprint(policy),
            "completion_enabled": policy.completion_enabled,
            "set_complete_supported": policy.set_complete_supported,
            "assertions": {
                "stage_TS4_A_iff_threshold_is_none": (stage == "TS4-A") == (
                    threshold is None),
                "threshold_approved_finite": (threshold is not None),
                "set_complete_supported": policy.set_complete_supported,
            },
        },
        "policy_identity": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "threshold": threshold,
            "approval_fingerprint": approval_fingerprint,
            "factor_table_fingerprint": factor_table_fingerprint(policy),
            "policy_provider_authority_fingerprint":
                SP.policy_provider_authority_fingerprint(policy),
            "registry_fingerprint": SP.registry_fingerprint(),
        },
        "qualification_binding": qualification_binding,
        "run_identity": _run_identity(run_id, run_id_source, generated_at, stage),
        "root_identity": {
            "trust_root_relpath": TRUST_ROOT_RELPATH,
            "trust_root_file_sha256": bindings.trust_root["trust_root_file_sha256"],
            "frozen_run_relpath": bindings.trust_root.get("frozen_run_relpath"),
            "frozen_run_id": bindings.trust_root.get("run_id"),
            "frozen_run_manifest_sha256": FROZEN_RUN_MANIFEST_SHA256,
            "frozen_artifact_index_sha256": FROZEN_ARTIFACT_INDEX_SHA256,
            "frozen_versions": bindings.trust_root.get("frozen_versions"),
            "non_300750_fixture_manifest_sha256":
                bindings.fixture.get("fixture_manifest_sha256"),
        },
        "review_attestation_identity": review_attestation_identity(
            stage, qualification_binding),
        "evidence_db_identity_at_run": {
            "relpath": EVIDENCE_DB_RELPATH,
            "note": "运行前后的逐字段身份见 database_immutability.json",
        },
        "machine_index": {
            "file": "machine_artifact_index.json",
            "check_file": "machine_artifact_index_check.json",
            "machine_index_identity_note": (
                "manifest 只绑定索引内容中**除去 run_manifest.json 条目**的部分"
                "（`machine_index_identity`），从而打破『索引含 manifest 哈希 / manifest "
                "含索引哈希』的循环；索引自身的 `index_identity` 另在索引内。"),
        },
        "span_snapshot_aggregate_sha256": sha256_file(
            results_root / "span_snapshot_aggregate.json"),
        "confidence_distribution_sha256": sha256_file(
            results_root / "confidence_distribution.json"),
        "span_snapshot_index_sha256": sha256_file(
            results_root / "span_snapshot_index.json"),
        "code_fingerprint": code,
        "versions": versions,
        "inputs": identity_inputs,
        "non_300750_fixture_binding": fixture_binding,
        "document_snapshot_summary": [
            {
                "document_key": built["document_key"],
                "document_id": built["document_id"],
                "snapshot_id": built["snapshot"].snapshot_id,
                "content_fingerprint": built["snapshot"].content_fingerprint,
                "input_fingerprint": built["snapshot"].input_fingerprint,
                "snapshot_file_sha256": built["snapshot_file_sha256"],
                "completion_eligible_span_count":
                    built["completion_eligible_span_count"],
            } for built in built_samples],
        "negative_example": bindings.negative,
        "notes": bindings.notes,
    }


# ---------------------------------------------------------------------------
# 7. `--seal-review`：create-once 封存（本批**不执行**，但代码必须在）
# ---------------------------------------------------------------------------

def _load_policy_decision(path: pathlib.Path) -> dict:
    """严格解析 `PolicyDecisionV1(pd-1)`：未知 / 缺失 / 重复字段一律拒绝。"""
    data = _read_json(path, "policy_decision.json")
    if not isinstance(data, dict):
        raise StepFailure(0, "policy_decision.json 顶层必须为对象",
                          field="policy_decision")
    unknown = sorted(set(data) - set(POLICY_DECISION_FIELDS))
    missing = sorted(set(POLICY_DECISION_FIELDS) - set(data))
    if unknown:
        raise StepFailure(0, f"policy_decision.json 含未知字段：{unknown}（fail-closed）",
                          field="policy_decision")
    if missing:
        raise StepFailure(0, f"policy_decision.json 缺字段：{missing}（fail-closed）",
                          field="policy_decision")
    if data["schema_type"] != "PolicyDecisionV1":
        raise StepFailure(0, "schema_type 必须为 'PolicyDecisionV1'",
                          field="schema_type")
    if data["review_schema_version"] != POLICY_DECISION_REVIEW_SCHEMA_VERSION:
        raise StepFailure(0, f"review_schema_version 必须为 "
                             f"{POLICY_DECISION_REVIEW_SCHEMA_VERSION!r}",
                          field="review_schema_version")
    if data["decision"] is not None and data["decision"] not in POLICY_DECISION_DECISIONS:
        raise StepFailure(0, f"decision 必须属于 {POLICY_DECISION_DECISIONS} 或为 null",
                          field="decision")
    verdicts = data["review_verdict"]
    if not isinstance(verdicts, dict):
        raise StepFailure(0, "review_verdict 必须为对象", field="review_verdict")
    if sorted(verdicts) != sorted(REVIEW_CHECK_IDS):
        raise StepFailure(
            0, f"review_verdict 必须**恰好**覆盖 8 个固定 check ID："
               f"{sorted(verdicts)} != {sorted(REVIEW_CHECK_IDS)}（fail-closed）",
            field="review_verdict")
    for check_id, verdict in verdicts.items():
        if verdict not in REVIEW_VERDICTS:
            raise StepFailure(0, f"review_verdict[{check_id}] 必须属于 "
                                 f"{REVIEW_VERDICTS}", field="review_verdict")
    if list(data["reviewer_roles"]) != list(REVIEWER_ROLES):
        raise StepFailure(0, f"reviewer_roles 必须恰为 {list(REVIEWER_ROLES)}",
                          field="reviewer_roles")
    if len(data["factor_entries"]) != len(SP.TS4_A_FACTOR_VALUES):
        raise StepFailure(0, "factor_entries 必须覆盖完整因子表（12 项）",
                          field="factor_entries")
    return data


def replay_feature_rows(rows, factor_entries, threshold: float | None) -> dict:
    """按 decision 的因子表重放**冻结 feature 行**（与 factor 取值无关的对照）。"""
    table: dict = {}
    problems: list = []
    for entry in factor_entries:
        factor = entry.get("factor")
        if factor is None:
            problems.append(f"factor_entries 缺值：({entry.get('side')},"
                            f"{entry.get('cause')})")
            continue
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            problems.append(f"factor_entries 的 factor 必须为实数：{entry!r}")
            continue
        table[(entry.get("side"), entry.get("cause"))] = float(factor)
    replayed = 0
    mismatches: list = []
    above_threshold = 0
    for row in rows:
        if row.get("range_kind") != "regular":
            continue
        left = table.get(("left", row.get("left_boundary_cause")))
        right = table.get(("right", row.get("right_boundary_cause")))
        if left is None or right is None:
            mismatches.append({
                "disposition_id": row.get("disposition_id"),
                "problem": "未登记的边界成因",
                "left_boundary_cause": row.get("left_boundary_cause"),
                "right_boundary_cause": row.get("right_boundary_cause"),
            })
            continue
        expected = round(min(left, right), V.FLOAT_PRECISION)
        replayed += 1
        if expected != row.get("confidence"):
            mismatches.append({
                "disposition_id": row.get("disposition_id"),
                "problem": "confidence 与按获批因子表重算值不一致",
                "recorded": row.get("confidence"), "recomputed": expected,
            })
        if threshold is not None and expected >= float(threshold):
            above_threshold += 1
    return {
        "rows_replayed": replayed,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:20],
        "threshold": threshold,
        "informational_threshold_projection": {
            "regular_dispositions_at_or_above_threshold": above_threshold,
            "note": ("这只是**审计投影**：TS4-A 快照的资格由其自载策略决定，"
                     "**不得**用阈值重新解释 A 产物，也不得据此宣称 `set_complete`。"),
        },
    }


def seal_review(target: pathlib.Path) -> int:
    """`--seal-review <same-run-dir>`：完整复核后 create-once 写 attestation。"""
    if not target.is_dir():
        print(canonical_json({"error": f"目标目录不存在：{target}"}))
        return 2
    attestation_path = target / "review_attestation.json"
    if attestation_path.exists():
        print(canonical_json({
            "error": f"该目录已封存（{attestation_path.name} 已存在）；"
                     f"重复 seal / 覆盖旧文件一律拒绝"}))
        return 2
    for name in ("run_manifest.json", "machine_artifact_index.json",
                 "confidence_distribution.json", "span_snapshot_aggregate.json",
                 "span_snapshot_index.json", "policy_decision.json",
                 "manual_review.md"):
        if not (target / name).is_file():
            print(canonical_json({"error": f"目标目录缺 {name}；不是完整 TS4-A 产物"}))
            return 2
    manifest = _read_json(target / "run_manifest.json", "run_manifest.json")
    if manifest.get("stage") != "TS4-A":
        print(canonical_json({"error": f"manifest 的 stage 不是 TS4-A："
                                       f"{manifest.get('stage')!r}"}))
        return 2
    stage, stage_error = _resolve_stage()
    if stage_error is not None:
        return stage_error
    code_now = code_fingerprint()
    manifest_code = manifest.get("code_fingerprint") or {}
    if canonical_json(code_now) != canonical_json(manifest_code):
        print(canonical_json({
            "error": "当前代码指纹与 A run manifest 不一致；不得用另一版代码复核旧产物",
            "missing": code_now["missing"],
            "differs": sorted(
                rel for rel in code_now["files"]
                if code_now["files"][rel] != manifest_code.get("files", {}).get(rel))}))
        return 2
    index = _read_json(target / "machine_artifact_index.json",
                       "machine_artifact_index.json")
    index_check = verify_machine_index(target, index)
    if not index_check["ok"]:
        print(canonical_json({"error": "机器索引核验失败",
                              "problems": index_check["problems"][:10]}))
        return 2
    # 人工门是**一次性**的：进入 B 之后不得再封存任何目录（否则等于给自己补发批准）。
    # 放在阶段/代码/索引三道拒绝之后，是为了不改变那些拒绝路径的既有语义与文案。
    if SP.ab_gate_truth_table()["stage"] != "distribution_only":
        print(canonical_json({
            "error": "当前已进入 threshold_enabled：人工门只在 TS4-A 阶段一次性封存，"
                     "不得再执行 --seal-review。TS4-B 的 approval / frozen 资产只能由"
                     "**已封存**的 review_attestation.json 确定性导出（fail-closed）。"}))
        return 2
    # 根校验**重新执行**一次 §18.14.2-1…9（不只看目录内部哈希）。
    try:
        bindings = bind_all()
    except StepFailure as e:
        print(canonical_json({"error": e.report()}))
        return 2
    snapshot_index = _read_json(target / "span_snapshot_index.json",
                                "span_snapshot_index.json")
    reverified = []
    samples = []
    for entry in snapshot_index.get("snapshots", []):
        document_key = entry.get("document_key")
        handoff = bindings.handoffs.get(document_key)
        if handoff is None:
            print(canonical_json({"error": f"索引里的 {document_key!r} 没有对应的"
                                           f"受信交接（fail-closed）"}))
            return 2
        path = target / entry["file"]
        if sha256_file(path) != entry.get("sha256"):
            print(canonical_json({"error": f"{entry['file']} 的 SHA256 与索引不符"}))
            return 2
        try:
            snapshot = SV.SpanBuildSnapshot.from_dict(
                _read_json(path, "raw span snapshot"))
            verified = SV.verify_span_snapshot(snapshot, handoff)
        except Exception as e:  # noqa: BLE001
            print(canonical_json({
                "error": f"{document_key} 的全对象复核失败：{type(e).__name__}: {e}"}))
            return 2
        if snapshot.content_fingerprint != entry.get("content_fingerprint"):
            print(canonical_json({"error": f"{document_key} 的 content_fingerprint "
                                           f"与索引不符"}))
            return 2
        reverified.append({"document_key": document_key,
                           "snapshot_id": snapshot.snapshot_id,
                           "verification_fingerprint":
                               verified.verification_fingerprint,
                           "file_sha256": entry.get("sha256")})
        samples.append((document_key, snapshot))
    decision = _load_policy_decision(target / "policy_decision.json")
    # "仅在**完整**复核后封存"：空白模板 / 仍有 not_reviewed 一律拒绝，避免把
    # create-once 的那一次机会烧在未复核的模板上。
    if decision.get("decision") is None:
        print(canonical_json({
            "error": "policy_decision.json 的 decision 仍为 null（空白模板）。"
                     "请先由用户 + Codex 完成 8 项复核并给出 decision 后再 seal；"
                     "create-once 机会不得烧在未复核的模板上"}))
        return 2
    unresolved = sorted(check_id for check_id, verdict in
                        decision["review_verdict"].items()
                        if verdict == "not_reviewed")
    if unresolved:
        print(canonical_json({
            "error": f"仍有 {len(unresolved)} 项复核未给出结论（not_reviewed）："
                     f"{unresolved}；完整复核后方可封存"}))
        return 2
    distribution = _read_json(target / "confidence_distribution.json",
                              "confidence_distribution.json")
    aggregate = _read_json(target / "span_snapshot_aggregate.json",
                           "span_snapshot_aggregate.json")
    policy = SP.resolve_distribution_policy()
    replayed_distribution = build_confidence_distribution(
        stage="TS4-A", policy=policy, samples=samples)
    replayed_distribution_sha = sha256_canonical(replayed_distribution)
    stored_distribution_sha = sha256_canonical(distribution)
    replayed_aggregate_sha = sha256_canonical(
        build_snapshot_aggregate(stage="TS4-A", samples=samples,
                                 negative=bindings.negative,
                                 fixture=bindings.fixture))
    problems: list = []
    if replayed_distribution_sha != stored_distribution_sha:
        problems.append("重算的 confidence_distribution 与 A 产物不一致")
    if replayed_aggregate_sha != sha256_canonical(aggregate):
        problems.append("重算的 span_snapshot_aggregate 与 A 产物不一致")
    replay = replay_feature_rows(
        distribution.get("disposition_feature_rows", []),
        decision.get("factor_entries", []), decision.get("threshold"))
    if replay["mismatch_count"]:
        problems.append(f"冻结 feature 行重放出现 {replay['mismatch_count']} 处不一致")
    all_checks_pass = all(v == "pass" for v in decision["review_verdict"].values())
    decision_value = decision.get("decision")
    threshold = decision.get("threshold")
    approve_ok = (decision_value == "approve"
                  and isinstance(threshold, (int, float))
                  and not isinstance(threshold, bool)
                  and all_checks_pass
                  and replay["mismatch_count"] == 0
                  and not problems)
    if decision_value == "approve" and not approve_ok:
        problems.append("decision=approve 但未满足『有限 threshold + 8 项全 pass + "
                        "feature 行可重放』；不得导出 TS4-B approval/frozen asset")
    manual_review_sha = sha256_file(target / "manual_review.md")
    decision_sha = sha256_file(target / "policy_decision.json")
    attestation = {
        "manual_review_required": True,
        "schema_type": "ReviewAttestation",
        "review_attestation_schema_version": REVIEW_ATTESTATION_SCHEMA_VERSION,
        "sealed_at_utc": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "TS4-A",
        "run_id": manifest.get("run_id"),
        "run_identity": manifest.get("run_identity"),
        "root_identity": manifest.get("root_identity"),
        "machine_artifact_index_sha256": sha256_file(
            target / "machine_artifact_index.json"),
        "machine_index_identity": index.get("machine_index_identity"),
        "manual_review_sha256": manual_review_sha,
        "policy_decision_sha256": decision_sha,
        "policy_decision_review_schema_version":
            decision["review_schema_version"],
        "decision": decision_value,
        "threshold": threshold,
        "review_verdict": decision["review_verdict"],
        "reviewer_roles": list(decision["reviewer_roles"]),
        "approval_eligible": approve_ok,
        "approval_record_export_allowed": approve_ok,
        "span_snapshot_aggregate_sha256": sha256_canonical(aggregate),
        "confidence_distribution_sha256": stored_distribution_sha,
        "replayed_confidence_distribution_sha256": replayed_distribution_sha,
        "replayed_distribution_matches": replayed_distribution_sha
            == stored_distribution_sha,
        "feature_row_replay": replay,
        "code_fingerprint_matches_a_run": True,
        "root_binding_reverified": True,
        "snapshot_reverification": reverified,
        "machine_index_check": index_check,
        "problems": problems,
    }
    _write_json(attestation_path, attestation)
    print(canonical_json({
        "sealed": True, "path": str(attestation_path),
        "decision": decision_value, "approval_eligible": approve_ok,
        "problems": problems}))
    return 0 if not problems else 1


# ---------------------------------------------------------------------------
# 8. `--export-b-assets <TS4_A_DIR>`：由 sealed attestation 确定性导出 TS4-B 资产
# ---------------------------------------------------------------------------
#
# §18.8.5：B 阶段的两份资产**只能**由一次性封存的 `review_attestation.json` 确定性
# 导出，不得手写成与 attestation 无关的平行事实。本模式因此只做三件事：
#
#   (1) **复核** A run 的封存事实（attestation 自洽、逐文件 SHA、机器索引、A 因子表、
#       aggregate / distribution 身份），并逐份 `from_dict` 读回 raw snapshot —— 证明
#       历史 A 快照在 **B 环境**仍可读、仍为 distribution-only、completion 恒 False；
#   (2) 由这些事实**确定性派生** approval / frozen 两份记录（create-once）；
#   (3) 把 B 条目**追加**登记进 `registry_v1.json`，并证明历史 A 条目、A 条目的
#       registry entry SHA 与 A provider authority 指纹在登记前后逐字节可复现。
#
# 本模式**不**构建 span、**不**重跑对齐、**不**改写 A 目录里的任何字节、**不**联网、
# **不**调用 `bind_all()`（绑定链的完整复核已由阶段 1 的封存完成，其结论在
# attestation 里逐项固化，本模式只复核这些结论与现场字节是否仍然一致）。

#: 封存事实里必须逐一成立、且取值固定的标志位（缺一项即拒绝导出）。
EXPORT_REQUIRED_ATTESTATION_FLAGS: tuple = (
    ("approval_eligible", True),
    ("approval_record_export_allowed", True),
    ("decision", "approve"),
    ("stage", "TS4-A"),
    ("replayed_distribution_matches", True),
    ("code_fingerprint_matches_a_run", True),
    ("root_binding_reverified", True),
)

#: 导出 A run 时**必须**齐备的文件（缺一即"不是完整 TS4-A 产物"）。
EXPORT_REQUIRED_A_FILES: tuple[str, ...] = (
    "run_manifest.json", "machine_artifact_index.json", "span_snapshot_index.json",
    "span_snapshot_aggregate.json", "confidence_distribution.json",
    "qualification_policy.json", "manual_review.md", "policy_decision.json",
)


def _export_need(condition: bool, message: str, *, field: str) -> None:
    if not condition:
        raise StepFailure(0, message, field=field)


def _derive_b_assets(target: pathlib.Path) -> dict:
    """复核封存事实 → 派生两份资产 → 追加登记。返回可审计报告（全部 fail-closed）。"""
    _export_need(target.is_dir(), f"目标目录不存在：{target}", field="target")
    attestation_path = target / "review_attestation.json"
    _export_need(
        attestation_path.is_file(),
        "该目录尚无 review_attestation.json：TS4-B 的 approval / frozen 资产只能由"
        "**已封存**的人工门结果确定性导出（不得手写平行事实，fail-closed）",
        field="review_attestation")
    for name in EXPORT_REQUIRED_A_FILES:
        _export_need((target / name).is_file(),
                     f"目标目录缺 {name}；不是完整 TS4-A 产物", field=name)
    # (0) 当前阶段必须是 threshold_enabled：approval record 的 threshold 必须等于
    # **已发布**的 SPAN_CONFIDENCE_MIN，否则导出的记录与常量自相矛盾。
    table = SP.ab_gate_truth_table()
    _export_need(
        table["stage"] == "threshold_enabled" and V.SPAN_CONFIDENCE_MIN is not None,
        "当前 versions.SPAN_CONFIDENCE_MIN 仍为 None（distribution_only）：TS4-B 的两份"
        "资产只能在该常量已裁决为有限阈值后导出（fail-closed）",
        field="span_confidence_min")
    attestation = _read_json(attestation_path, "review_attestation.json")
    # 封存身份：`sealed_at_utc` 由 `--seal-review` 一次性写入，"文件存在 + 时刻非空 +
    # 结论为 approve + 批准资格为真"共同构成封存事实（本模式不重跑那次封存）。
    _export_need(attestation.get("schema_type") == "ReviewAttestation"
                 and attestation.get("review_attestation_schema_version")
                 == REVIEW_ATTESTATION_SCHEMA_VERSION,
                 "attestation 的 schema_type / 版本不符（fail-closed）",
                 field="schema_type")
    _export_need(isinstance(attestation.get("sealed_at_utc"), str)
                 and attestation["sealed_at_utc"] != "",
                 "attestation 缺 sealed_at_utc：该目录未被一次性封存，不得导出 B 资产"
                 "（fail-closed）", field="sealed_at_utc")
    for field_name, expected in EXPORT_REQUIRED_ATTESTATION_FLAGS:
        _export_need(
            attestation.get(field_name) == expected,
            f"attestation 的 {field_name}={attestation.get(field_name)!r} 不等于 "
            f"{expected!r}：该 run 未取得批准资格，不得导出 TS4-B 资产（fail-closed）",
            field=field_name)
    _export_need(not attestation.get("problems"),
                 f"attestation 自带 problems={attestation.get('problems')}：有问题的封存"
                 "结果不得导出 B 资产（fail-closed）", field="problems")
    _export_need(bool((attestation.get("machine_index_check") or {}).get("ok")),
                 "attestation 的 machine_index_check 未通过：不得导出 B 资产（fail-closed）",
                 field="machine_index_check")
    _export_need((attestation.get("feature_row_replay") or {}).get("mismatch_count") == 0,
                 "attestation 的 feature 行重放存在不一致：不得导出 B 资产（fail-closed）",
                 field="feature_row_replay")
    verdicts = attestation.get("review_verdict")
    _export_need(isinstance(verdicts, dict)
                 and sorted(verdicts) == sorted(REVIEW_CHECK_IDS),
                 "attestation 的 review_verdict 未恰好覆盖 8 项人工清单（fail-closed）",
                 field="review_verdict")
    not_pass = sorted(k for k, v in verdicts.items() if v != "pass")
    _export_need(not not_pass,
                 f"attestation 的 review_verdict 存在非 pass 项：{not_pass}；"
                 "任一非 pass 即不得导出（fail-closed）", field="review_verdict")
    _export_need(list(attestation.get("reviewer_roles") or []) == list(REVIEWER_ROLES),
                 f"attestation 的 reviewer_roles 必须恰为 {list(REVIEWER_ROLES)}"
                 "（用户 + Codex 双签，fail-closed）", field="reviewer_roles")
    threshold = attestation.get("threshold")
    _export_need(
        not isinstance(threshold, bool) and isinstance(threshold, (int, float))
        and math.isfinite(float(threshold))
        and float(threshold) == float(V.SPAN_CONFIDENCE_MIN),
        f"attestation 的 threshold={threshold!r} 与当前 versions.SPAN_CONFIDENCE_MIN="
        f"{V.SPAN_CONFIDENCE_MIN!r} 不一致：threshold 必须为已裁决的有限值（fail-closed）",
        field="threshold")
    # (1) 逐文件复核：attestation 自报的 SHA 必须与现场字节一致（封存后不得被改动）。
    manual_review_path = target / "manual_review.md"
    decision_path = target / "policy_decision.json"
    index_path = target / "machine_artifact_index.json"
    manifest_path = target / "run_manifest.json"
    for path, claimed, field_name in (
            (manual_review_path, attestation.get("manual_review_sha256"),
             "manual_review_sha256"),
            (decision_path, attestation.get("policy_decision_sha256"),
             "policy_decision_sha256"),
            (index_path, attestation.get("machine_artifact_index_sha256"),
             "machine_artifact_index_sha256")):
        actual = sha256_file(path)
        _export_need(claimed == actual,
                     f"{path.name} 的 SHA256 与 attestation 不符：{claimed} != {actual}"
                     "（封存后被改动，fail-closed）", field=field_name)
    index = _read_json(index_path, "machine_artifact_index.json")
    index_check = verify_machine_index(target, index)
    _export_need(index_check["ok"],
                 f"机器索引核验失败：{index_check['problems'][:5]}（fail-closed）",
                 field="machine_artifact_index")
    index_identity = index.get("machine_index_identity")
    _export_need(index_identity == attestation.get("machine_index_identity"),
                 "机器索引身份与 attestation 不符（fail-closed）",
                 field="machine_index_identity")
    manifest = _read_json(manifest_path, "run_manifest.json")
    _export_need(manifest.get("stage") == "TS4-A",
                 f"manifest 的 stage 不是 TS4-A：{manifest.get('stage')!r}",
                 field="stage")
    _export_need(manifest.get("run_id") == attestation.get("run_id"),
                 "manifest 的 run_id 与 attestation 不符（fail-closed）", field="run_id")
    for field_name in ("run_identity", "root_identity"):
        _export_need(
            canonical_json(manifest.get(field_name))
            == canonical_json(attestation.get(field_name)),
            f"manifest 的 {field_name} 与 attestation 不符（fail-closed）",
            field=field_name)
    # (2) 人工裁决：policy_decision 必须与 attestation 逐项一致（复核结论未被改写）。
    decision = _load_policy_decision(decision_path)
    for field_name in ("decision", "threshold", "review_verdict", "reviewer_roles"):
        _export_need(
            canonical_json(decision.get(field_name))
            == canonical_json(attestation.get(field_name)),
            f"policy_decision 的 {field_name} 与 attestation 不符"
            "（封存后被改动，fail-closed）", field=field_name)
    _export_need(decision.get("a_machine_index_identity") == index_identity,
                 "policy_decision 记录的 A 机器索引身份与现场索引不符（fail-closed）",
                 field="a_machine_index_identity")
    # 注意：attestation 里的 aggregate / distribution 身份是**内容**身份（canonical），
    # 而人工裁决与 approval record 记的是同一文件的**字节** SHA256 —— 两者口径不同，
    # 因此分别核对：内容身份见本段之后与第 (3) 段，文件身份见第 (3) 段。
    got_decision_factors = [(row.get("side"), row.get("cause"), row.get("factor"))
                            for row in decision["factor_entries"]]
    _export_need(
        got_decision_factors == [(s, c, f) for (s, c, f) in SP.TS4_A_FACTOR_VALUES],
        f"policy_decision 的因子表偏离 §18.8.5 已批准表：{got_decision_factors}"
        "（不得只改单项 / 换序 / 增删，fail-closed）", field="factor_entries")
    a_run_identity = decision.get("a_run_identity")
    _export_need(isinstance(a_run_identity, dict) and a_run_identity,
                 "policy_decision 的 a_run_identity 必须为非空对象（fail-closed）",
                 field="a_run_identity")
    _export_need(
        a_run_identity.get("run_id") == manifest.get("run_id")
        and a_run_identity.get("generated_at_utc") == manifest.get("generated_at_utc")
        and a_run_identity.get("stage") == "TS4-A"
        and canonical_json(a_run_identity.get("run_id_identity"))
        == canonical_json(manifest.get("run_identity"))
        and a_run_identity.get("machine_index_identity") == index_identity,
        "policy_decision 的 a_run_identity 与 A run 的 manifest / 机器索引不符"
        "（fail-closed）", field="a_run_identity")
    _export_need(
        canonical_json(decision.get("a_root_identity"))
        == canonical_json(manifest.get("root_identity")),
        "policy_decision 的 a_root_identity 与 A run manifest 不符（fail-closed）",
        field="a_root_identity")
    # (3) A run 的 aggregate / distribution 身份：**文件** SHA256（§18.8.5 唯一口径），
    #     并与 manifest 及人工裁决里记录的值三方一致；内容身份另与 attestation 的
    #     canonical 记录一致（说明封存时的那次重放结论仍然成立）。
    aggregate_path = target / "span_snapshot_aggregate.json"
    distribution_path = target / "confidence_distribution.json"
    aggregate_sha = sha256_file(aggregate_path)
    distribution_sha = sha256_file(distribution_path)
    _export_need(aggregate_sha == decision.get("a_aggregate_snapshot_sha256"),
                 "A run 的 span_snapshot_aggregate.json 文件 SHA256 与人工裁决记录不符"
                 "（fail-closed）", field="a_aggregate_snapshot_sha256")
    _export_need(distribution_sha == decision.get("a_confidence_distribution_sha256"),
                 "A run 的 confidence_distribution.json 文件 SHA256 与人工裁决记录不符"
                 "（fail-closed）", field="a_confidence_distribution_sha256")
    _export_need(manifest.get("span_snapshot_aggregate_sha256") == aggregate_sha
                 and manifest.get("confidence_distribution_sha256") == distribution_sha,
                 "A run manifest 里记录的 aggregate / distribution 文件 SHA256 与现场不符"
                 "（fail-closed）", field="span_snapshot_aggregate_sha256")
    _export_need(
        sha256_canonical(_read_json(aggregate_path, "span_snapshot_aggregate.json"))
        == attestation.get("span_snapshot_aggregate_sha256"),
        "重算的 span_snapshot_aggregate 内容身份与 attestation 不符（fail-closed）",
        field="span_snapshot_aggregate_content")
    _export_need(
        sha256_canonical(_read_json(distribution_path, "confidence_distribution.json"))
        == attestation.get("confidence_distribution_sha256"),
        "重算的 confidence_distribution 内容身份与 attestation 不符（fail-closed）",
        field="confidence_distribution_content")
    # (4) A run 的 qualification policy：stage / threshold / completion 与因子表、
    #     简介常量必须就是 §18.8.5 / §18.8.6 的已冻结规格。
    a_policy_payload = _read_json(target / "qualification_policy.json",
                                  "qualification_policy.json")
    a_record = a_policy_payload.get("policy")
    _export_need(isinstance(a_record, dict),
                 "A run 的 qualification_policy.json 缺 policy 记录（fail-closed）",
                 field="qualification_policy")
    _export_need(a_policy_payload.get("stage") == "TS4-A"
                 and a_record.get("stage") == "distribution_only"
                 and a_record.get("span_confidence_min") is None
                 and a_record.get("completion_enabled") is False
                 and a_record.get("set_complete_supported") is False,
                 "A run 的策略记录不是 distribution-only（threshold=None、completion 全假）；"
                 "不得据此导出 B 资产（fail-closed）", field="a_policy_stage")
    got_factors = [(row.get("side"), row.get("cause"), row.get("factor"))
                   for row in (a_record.get("factor_entries") or [])]
    _export_need(
        got_factors == [(s, c, f) for (s, c, f) in SP.TS4_A_FACTOR_VALUES],
        f"A run 的因子表偏离 §18.8.5 已批准表：{got_factors}"
        "（fail-closed）", field="a_factor_entries")
    for name, expected_value in SP.TS4_A_SNIPPET_DEFAULTS.items():
        got_value = a_record.get(name)
        if isinstance(expected_value, tuple):
            got_value = tuple(got_value) if isinstance(got_value, list) else got_value
        _export_need(
            got_value == expected_value,
            f"A run 策略的 {name} 偏离 §18.8.6 已冻结规格：{got_value!r} != "
            f"{expected_value!r}（fail-closed）", field=f"a_policy_{name}")
    a_policy = __import__("document_structure.span_schema",
                          fromlist=["SpanQualificationPolicy"]
                          ).SpanQualificationPolicy.from_dict(a_record)
    # (5) 历史 A 快照在 **B 环境**下逐份读回：仍可 `from_dict`、内容身份一致、四槽
    #     全 None、completion 恒 False，且 A 的 provider authority 指纹可复现。
    snapshot_index = _read_json(target / "span_snapshot_index.json",
                                "span_snapshot_index.json")
    readback: list = []
    a_authority_before: str | None = None
    for entry in snapshot_index.get("snapshots", []):
        document_key = entry.get("document_key")
        snapshot_path = target / entry["file"]
        _export_need(sha256_file(snapshot_path) == entry.get("sha256"),
                     f"{entry['file']} 的 SHA256 与索引不符（fail-closed）",
                     field=f"snapshot_sha256[{document_key}]")
        snapshot = SV.SpanBuildSnapshot.from_dict(
            _read_json(snapshot_path, "raw span snapshot"))
        _export_need(snapshot.content_fingerprint == entry.get("content_fingerprint"),
                     f"{document_key} 的 content_fingerprint 与索引不符（fail-closed）",
                     field=f"content_fingerprint[{document_key}]")
        embedded = snapshot.qualification_policy
        _export_need(
            embedded.stage == "distribution_only"
            and embedded.span_confidence_min is None
            and embedded.completion_enabled is False
            and embedded.set_complete_supported is False,
            f"{document_key} 内嵌的策略不是 distribution-only；历史 A 快照不得被当前全局"
            "常量重新解释（fail-closed）", field=f"snapshot_policy[{document_key}]")
        slots = tuple(snapshot.trusted_input.qualification[7:11])
        _export_need(all(slot is None for slot in slots),
                     f"{document_key} 的 A 四槽必须全为 None（§18.8.5），得到 {slots!r}"
                     "（fail-closed）", field=f"qualification_slots[{document_key}]")
        recorded = snapshot.trusted_input.qualification[12]
        authority = SP.policy_provider_authority_fingerprint(embedded)
        _export_need(
            authority == recorded,
            f"{document_key} 的 A provider authority 指纹不可复现：{authority} != "
            f"{recorded}（A 条目或历史记录已被改动，fail-closed）",
            field=f"a_authority[{document_key}]")
        a_authority_before = authority
        readback.append({
            "document_key": document_key,
            "snapshot_id": snapshot.snapshot_id,
            "file_sha256": entry.get("sha256"),
            "content_fingerprint": snapshot.content_fingerprint,
            "stage": embedded.stage,
            "threshold": embedded.span_confidence_min,
            "completion_enabled": embedded.completion_enabled,
            "qualification_binding_slots": list(slots),
            "policy_provider_authority_fingerprint": authority,
        })
    _export_need(bool(readback), "A run 的 span_snapshot_index 为空（fail-closed）",
                 field="span_snapshot_index")
    registry_before = SP.registry_document()
    a_entry_before = dict(registry_before["policies"][SP.DEFAULT_POLICY_KEY])
    a_entry_sha_before = sha256_canonical(a_entry_before)
    # (6) 确定性派生 approval record（A run 身份 + 批准身份 + 12 因子 + threshold）。
    approval = {
        "schema_type": SP.APPROVAL_RECORD_SCHEMA_TYPE,
        "approval_schema_version": SP.APPROVAL_RECORD_SCHEMA_VERSION,
        "review_attestation_relpath": _repo_relpath(attestation_path),
        "review_attestation_sha256": sha256_file(attestation_path),
        "a_run_id": manifest.get("run_id"),
        "a_run_identity": a_run_identity,
        "a_run_manifest_sha256": sha256_file(manifest_path),
        "a_root_identity": decision.get("a_root_identity"),
        "a_machine_index_identity": index_identity,
        "a_machine_artifact_index_sha256": sha256_file(index_path),
        "a_manual_review_sha256": sha256_file(manual_review_path),
        "a_policy_decision_sha256": sha256_file(decision_path),
        "a_aggregate_snapshot_sha256": aggregate_sha,
        "a_confidence_distribution_sha256": distribution_sha,
        "decision": decision["decision"],
        "review_verdict": decision["review_verdict"],
        "reviewer_roles": list(decision["reviewer_roles"]),
        "factor_entries": [dict(row) for row in decision["factor_entries"]],
        "threshold": float(threshold),
        "policy_constants": {name: (list(value) if isinstance(value, tuple) else value)
                             for name, value in SP.TS4_A_SNIPPET_DEFAULTS.items()},
        "frozen_policy_key": SP.FROZEN_POLICY_KEY,
        "frozen_policy_fingerprint": None,
    }
    derived = SP.derived_frozen_policy(approval)
    approval["frozen_policy_fingerprint"] = derived.policy_fingerprint
    approval["approval_authority_fingerprint"] = SP.approval_authority_fingerprint(approval)
    _export_need(sorted(approval) == sorted(SP.APPROVAL_RECORD_KEYS),
                 f"派生的 approval record 字段集与规格不符：{sorted(approval)} != "
                 f"{sorted(SP.APPROVAL_RECORD_KEYS)}（fail-closed）", field="approval_keys")
    frozen_record = derived.to_dict()
    # (7) create-once 写入：已存在则必须与派生结果逐字节一致，否则拒绝（不得覆盖）。
    approval_path = SP.POLICY_DIR / SP.APPROVAL_RECORD_FILENAME
    frozen_path = SP.POLICY_DIR / SP.FROZEN_RECORD_FILENAME
    approval_bytes = SP.serialize_policy_asset(approval)
    frozen_bytes = SP.serialize_policy_asset(frozen_record)
    written = []
    for path, payload in ((approval_path, approval_bytes),
                          (frozen_path, frozen_bytes)):
        if path.is_file():
            existing = path.read_bytes()
            _export_need(
                existing == payload,
                f"{path.name} 已存在且与由 attestation 确定性派生的结果不一致；"
                "不覆盖既有的 B 资产（fail-closed）", field=path.name)
        else:
            path.write_bytes(payload)
            written.append(path.name)
    # 写回后必须能过 provider 自己的 fail-closed 校验（自报指纹一律重算）。
    loaded = SP.load_approval_record()
    _export_need(loaded["approval_authority_fingerprint"]
                 == approval["approval_authority_fingerprint"],
                 "写回后的 approval record 未通过 provider 校验（fail-closed）",
                 field="approval_authority_fingerprint")
    # (8) 追加登记 B 条目，并证明历史 A 条目逐字节未变、A 指纹仍可复现。
    registry_after = SP.register_frozen_policy_entry(derived)
    a_entry_after = dict(registry_after["policies"][SP.DEFAULT_POLICY_KEY])
    _export_need(
        sha256_canonical(a_entry_after) == a_entry_sha_before
        and canonical_json(a_entry_after) == canonical_json(a_entry_before),
        "追加登记改动了历史 A 条目（fail-closed）", field="registry_a_entry")
    _export_need(
        registry_after["default_policy_key"] == registry_before["default_policy_key"],
        "追加登记改动了 default_policy_key（fail-closed）",
        field="default_policy_key")
    frozen_policy = SP.resolve_frozen_policy()
    SP.assert_current_policy(frozen_policy)
    a_authority_after = SP.policy_provider_authority_fingerprint(a_policy)
    _export_need(
        a_authority_after == a_authority_before,
        f"追加登记改变了历史 A 的 provider authority 指纹：{a_authority_before} != "
        f"{a_authority_after}（§18.3.5 要求 A 指纹与 B 资产无关，fail-closed）",
        field="a_provider_authority")
    return {
        "exported": True,
        "stage": table["stage"],
        "span_confidence_min": V.SPAN_CONFIDENCE_MIN,
        "source_a_run": {
            "dir": str(target),
            "run_id": manifest.get("run_id"),
            "attestation_relpath": _repo_relpath(attestation_path),
            "attestation_sha256": sha256_file(attestation_path),
            "sealed_at_utc": attestation.get("sealed_at_utc"),
            "decision": attestation.get("decision"),
            "threshold": attestation.get("threshold"),
            "reviewer_roles": attestation.get("reviewer_roles"),
            "review_verdict": attestation.get("review_verdict"),
            "problems": attestation.get("problems"),
            "approval_eligible": attestation.get("approval_eligible"),
        },
        "assets": {
            "approval_record_path": _repo_relpath(approval_path),
            "approval_record_sha256": sha256_file(approval_path),
            "approval_authority_fingerprint": loaded["approval_authority_fingerprint"],
            "approval_authority_fingerprint_recomputed": True,
            "frozen_record_path": _repo_relpath(frozen_path),
            "frozen_record_sha256": sha256_file(frozen_path),
            "frozen_policy_key": SP.FROZEN_POLICY_KEY,
            "frozen_policy_id": frozen_policy.policy_id,
            "frozen_policy_fingerprint": frozen_policy.policy_fingerprint,
            "written_now": sorted(written),
            "created_once": True,
        },
        "registry": {
            "before_keys": sorted(registry_before["policies"]),
            "after_keys": sorted(registry_after["policies"]),
            "appended_key": SP.FROZEN_POLICY_KEY,
            "default_policy_key_unchanged": True,
            "a_entry_sha256_before": a_entry_sha_before,
            "a_entry_sha256_after": sha256_canonical(a_entry_after),
            "a_provider_authority_fingerprint_before": a_authority_before,
            "a_provider_authority_fingerprint_after": a_authority_after,
            "registry_fingerprint_after": SP.registry_fingerprint(),
        },
        "historical_a_readback": readback,
        "qualification_binding_slots": list(SP.qualification_binding_slots(frozen_policy)),
        "note": ("本模式只导出**口径**资产：它证明当前口径已获批准，"
                 "**不**证明任一 topic / aspect / `set_complete` 已完成。"),
    }


def _repo_relpath(path: pathlib.Path) -> str:
    resolved = pathlib.Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError as e:
        raise StepFailure(
            0, f"{resolved} 不在仓库内（{REPO_ROOT}）：B 资产必须绑定仓库内路径，"
               "以保证 relpath 可复现（fail-closed）", field="repo_relpath") from e


def export_b_assets(target: pathlib.Path) -> int:
    """`--export-b-assets <TS4_A_DIR>`：确定性导出 TS4-B 资产（fail-closed）。"""
    try:
        report = _derive_b_assets(target)
    except StepFailure as e:
        print(canonical_json({"error": e.report(), "step": e.step, "field": e.field}))
        return 2
    print(canonical_json(report))
    return 0


# ---------------------------------------------------------------------------
# 9. `--validate-only`（离线自检，不产生结果目录）
# ---------------------------------------------------------------------------

def validate_only() -> int:
    report: dict = {}
    report["evidence_gateway_self_check"] = EG.self_check()
    report["span_policy_self_check"] = SP.self_check()
    report["span_builder_self_check"] = SB.self_check()
    report["span_verifier_self_check"] = SV.self_check()
    report["synopsis_self_check"] = SY.self_check()
    report["versions_self_check"] = V.self_check()
    report["span_schema_self_check"] = __import__(
        "document_structure.span_schema", fromlist=["self_check"]).self_check()
    problems: list = []
    for name, payload in report.items():
        if isinstance(payload, dict) and payload.get("problems"):
            problems.append(f"{name}: {payload['problems'][:3]}")
    code = code_fingerprint()
    report["versions_payload_ok"] = bool(build_versions_payload())
    report["code_fingerprint"] = {"fingerprint": code["fingerprint"],
                                 "missing": code["missing"],
                                 "file_count": code["file_count"]}
    if code["missing"]:
        problems.append(f"代码指纹文件缺失：{code['missing']}")
    stage, stage_error = _resolve_stage()
    report["stage"] = stage
    if stage_error is not None:
        problems.append(
            f"当前阶段 {stage!r} 的资源未就绪（fail-closed，见上方 stage 报告）")
    policy = (SP.resolve_distribution_policy() if stage == "TS4-A"
              else SP.resolve_frozen_policy())
    factor_fingerprint = factor_table_fingerprint(policy)
    report["factor_table_fingerprint"] = factor_fingerprint
    report["policy_provider_authority_fingerprint"] = \
        SP.policy_provider_authority_fingerprint(policy)
    report["span_confidence_min"] = V.SPAN_CONFIDENCE_MIN
    report["current_policy_key"] = SP.current_policy_key()
    if stage == "TS4-B":
        # 只读预检：B 的两份资产必须齐备、可重算、且与注册表钉住的指纹一致。
        approval = SP.load_approval_record()
        report["approval_authority_fingerprint"] = \
            SP.approval_authority_fingerprint(approval)
        report["frozen_policy_key"] = SP.FROZEN_POLICY_KEY
        report["frozen_policy_fingerprint"] = policy.policy_fingerprint
        report["frozen_policy_derivation_ok"] = True
        report["qualification_binding_slots"] = list(
            SP.qualification_binding_slots(policy))
    # 只读预检：信任锚与两份夹具是否**可读且自洽**（不做完整绑定，不建结果目录）。
    try:
        trust_root = load_trust_root()
        report["trust_root_file_sha256"] = trust_root["trust_root_file_sha256"]
        report["frozen_run_relpath"] = trust_root.get("frozen_run_relpath")
        if not (REPO_ROOT / str(trust_root.get("frozen_run_relpath"))).is_dir():
            problems.append("冻结 run 目录不存在")
        _need_step_sha256(1, "冻结 run_manifest.json",
                          REPO_ROOT / str(trust_root["frozen_run_relpath"])
                          / "run_manifest.json", FROZEN_RUN_MANIFEST_SHA256,
                          field="run_manifest.json")
        _need_step_sha256(1, "冻结 artifact_index.json",
                          REPO_ROOT / str(trust_root["frozen_run_relpath"])
                          / "artifact_index.json", FROZEN_ARTIFACT_INDEX_SHA256,
                          field="artifact_index.json")
    except StepFailure as e:
        problems.append(e.report())
    try:
        fixture_root = EG.load_fixture_root()
        declared = fixture_root.get("factor_table_fingerprint")
        report["fixture_factor_table_fingerprint"] = declared
        report["fixture_factor_table_fingerprint_matches"] = \
            declared == factor_fingerprint
        report["fixture_document_id"] = fixture_root.get("document_id")
        report["fixture_stage"] = fixture_root.get("fixture_stage")
        if declared != factor_fingerprint:
            problems.append(
                f"fixture manifest 的 factor_table_fingerprint 与代码内冻结因子表不符："
                f"{declared} != {factor_fingerprint}（需裁决：夹具声明与 §18.8.5 表）")
        for member in fixture_root.get("members", []):
            path = EG.fixture_root_dir() / str(member.get("relpath"))
            _need_step_sha256(9, f"fixture 成员 {member.get('relpath')}", path,
                              member.get("sha256"),
                              field=f"members[{member.get('relpath')}]")
    except StepFailure as e:
        problems.append(e.report())
    report["problems"] = problems
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TS4-A 真实 span 验收（只读；计划 §18.14）")
    parser.add_argument("--run-id", default=None,
                        help="新 run_id（形如 tree_span_ts4_ts4a_<UTC>；不得与历史目录重复）")
    parser.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT,
                        help="结果根目录（默认 evaluation/results）")
    parser.add_argument("--validate-only", action="store_true",
                        help="只做离线自检与只读预检，不构建、不写任何文件")
    parser.add_argument("--seal-review", default=None, metavar="TS4_A_DIR",
                        help="对已有 TS4-A 结果目录执行一次 create-once 人工封存")
    parser.add_argument("--export-b-assets", default=None, metavar="TS4_A_DIR",
                        help="由**已封存**的 TS4-A 目录确定性导出 TS4-B 的 approval / "
                             "frozen 资产并追加登记注册表（只读 A 目录）")
    args = parser.parse_args(argv)

    if args.validate_only:
        return validate_only()

    if args.seal_review is not None:
        target = pathlib.Path(args.seal_review)
        if not target.is_absolute():
            target = REPO_ROOT / target
        return seal_review(target)

    if args.export_b_assets is not None:
        target = pathlib.Path(args.export_b_assets)
        if not target.is_absolute():
            target = REPO_ROOT / target
        return export_b_assets(target)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    generated_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id_source = "caller_supplied" if args.run_id else "generated_utc"
    stage, stage_error = _resolve_stage()
    if stage_error is not None:
        return stage_error
    stage_token = "ts4a" if stage == "TS4-A" else "ts4b"
    run_id = args.run_id or now_utc.strftime(
        f"tree_span_ts4_{stage_token}_%Y%m%dT%H%M%SZ")
    results_root = pathlib.Path(args.results_root)
    if not results_root.is_absolute():
        results_root = REPO_ROOT / results_root
    run_dir = results_root / run_id
    # 拒绝覆盖必须在**任何**构建 / 绑定之前发生。
    if run_dir.exists():
        raise SystemExit(f"结果目录已存在，拒绝覆盖历史结果：{run_dir}")

    try:
        return execute_run(run_id=run_id, run_id_source=run_id_source,
                           results_root=run_dir, generated_at=generated_at)
    except StepFailure as e:
        print(canonical_json({"error": e.report(), "step": e.step,
                              "document_id": e.document_id, "field": e.field}))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())

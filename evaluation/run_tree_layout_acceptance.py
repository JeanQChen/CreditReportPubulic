"""TS2 真实文档版式层验收与全量对齐分布诊断（**评测专用**）。

用途（指令 §七 / §十）：

1. 对三份真实电子 PDF 分别构建 `PageLayout`，落盘人工可复核的审计产物。
2. 对**全部**存量 `EvidenceBlock` 计算"同页 PageLayout 原文"的顺序连续片段覆盖率，
   给出分布、低覆盖归因与 ALIGN_MIN 候选材料。

硬约束：

- **只读**。`data/evidence.db` 只经 `evidence.store._open_readonly_conn()`（URI
  `?mode=ro` + `PRAGMA query_only=ON`）打开；不调用 `init_db()`、不做 migration、
  不 UPDATE / INSERT / DELETE，不改 `evidence.store._db_path`。
- 运行前后记录 PDF / Evidence DB / Financial DB 的 sha256、size、mtime，必须不变。
- **不抽样**。逐块计算；不静默排除表格页、极短块、低覆盖块、空文本块或难解析页。
- **TS3 起本脚本调用唯一生产对齐核心** `document_structure.aligner`（交付 A）：
  覆盖率度量、残差归因、非重叠 occurrence 分配、三态判定与
  `TextAlignmentRecord` 生成都在生产核心里。评测侧只做只读取数、汇总与落盘审计；
  本文件**不得**再带第二份算法副本，生产代码也**不得**反向 import 本脚本。
- 不写 `ALIGN_MIN`，不改任何生产常量。
- 底层只使用 `SequenceMatcher(autojunk=False)` 与 `normalization.tight()`
  （两者都在生产核心内）；不上 LLM、不联网、不调用 Router / Contract。

用法：

    python -m evaluation.run_tree_layout_acceptance --run-id ts2_<新id>

不传 `--run-id` 时用 UTC 时间戳生成新目录；**绝不覆盖**任何历史结果目录。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import pathlib
import sqlite3
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # 允许 `python evaluation/run_tree_layout_acceptance.py`
    sys.path.insert(0, str(REPO_ROOT))

from document_structure import aligner as A                   # noqa: E402
from document_structure import layout_builder as LB          # noqa: E402
from document_structure import schema as S                   # noqa: E402
from document_structure import versions as V                 # noqa: E402
from document_structure.canonical import canonical_json       # noqa: E402
from document_structure.normalization import (                # noqa: E402
    INVISIBLE_CODEPOINTS, tight,
)
from evidence.store import (                                  # noqa: E402
    _open_readonly_conn, current_evidence_set_ro, list_document_evidence_ro,
)

SAMPLE_DIR = REPO_ROOT / "data" / "samples" / "300750" / "announcements"
EVIDENCE_DB = REPO_ROOT / "data" / "evidence.db"
#: 财务库沿用仓库既有的 V2 库路径（见 financial_v2/*.py 的缺省 `data/financial_v2.db`）。
FINANCIAL_DB = REPO_ROOT / "data" / "financial_v2.db"
RESULTS_ROOT = REPO_ROOT / "evaluation" / "results"

#: 三份真实文档（company_id, document_id, 文件名）。页数由 DB 交叉核对。
DOCUMENTS = (
    ("300750", "NDSD_2024_year", "NDSD_2024_year.pdf"),
    ("300750", "NDSD_2025_year", "NDSD_2025_year.pdf"),
    ("300750", "NDSD_KCZ_2026", "NDSD_KCZ_2026.pdf"),
)

PERCENTILES = (("min", 0.0), ("p1", 1), ("p5", 5), ("p10", 10), ("p25", 25),
               ("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95),
               ("p98", 98), ("max", 100.0))
THRESHOLDS = (0.50, 0.70, 0.80, 0.85, 0.90, 0.95, 0.98, 0.99)

# 对齐算法与它的**全部常量**只有一份生产实现：`document_structure.aligner`
# （TS3 交付 A）。本脚本是评测侧调用方，只保留"只读 Evidence / 汇总 / 落盘审计
# 产物"的职责；覆盖率度量、残差归因、非重叠 occurrence 分配、三态判定与
# `TextAlignmentRecord` 生成一律调用生产核心。
#
# 下面的名字全部是**转发**（`RA.x is A.x` 恒为真，见 `evals/test_tree_aligner.py`
# 的同一性检查），**不是**第二份副本：副本一旦存在，评测就会与生产链分叉，
# 绿色也不再证明生产行为。`width_fold` 的删除裁决、`NORMALIZATION_VERSION`
# 依据、`INVISIBLE_CODEPOINTS` 白名单都随生产核心单一存放。
RESIDUE_CLASSES = A.RESIDUE_CLASSES
MAX_PIECE_LEN = A.MAX_PIECE_LEN
MAX_DISPLACEMENT_DP_LEN = A.MAX_DISPLACEMENT_DP_LEN
COLUMN_REORDER_MIN_PIECES = A.COLUMN_REORDER_MIN_PIECES
COLUMN_REORDER_MIN_PIECE_LEN = A.COLUMN_REORDER_MIN_PIECE_LEN
NUMERIC_LIKE_CHARS = A.NUMERIC_LIKE_CHARS
NUMERIC_CHAR_RATIO = A.NUMERIC_CHAR_RATIO
NUMERIC_PIECE_MIN_LEN = A.NUMERIC_PIECE_MIN_LEN
MAX_ALIGNMENT_SEARCH_NODES = A.MAX_ALIGNMENT_SEARCH_NODES
ENGINE_ARTIFACT_RULES = A.ENGINE_ARTIFACT_RULES
RECORD_REFUSAL_QUANTIZATION_BOUNDARY = A.RECORD_REFUSAL_QUANTIZATION_BOUNDARY


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(paths) -> dict:
    """记录受监管文件的 sha256 / size / mtime。

    文件缺失时记录 `{"missing": True}` 而**不是** `None`：`None` 会让
    `before == after` 平凡成立，把一个从未核对过的受监管文件伪装成"未变"。
    """
    out = {}
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


# ---------------------------------------------------------------------------
# 对齐原语（**转发**唯一生产实现，不是副本）
# ---------------------------------------------------------------------------
#
# 覆盖率度量、残差归因、非重叠 occurrence 分配、单块对齐与三态判定全部在
# `document_structure.aligner` 里。本文件**不再定义**任何一份副本：下面这些名字
# 只是指向生产核心**同一个对象**的别名，供本脚本自身与既有 TS2 回归访问。
# `RA.x is A.x` 恒为真（见 `evals/test_tree_aligner.py` 的同一性检查）——
# 一旦有人在这里重新定义一份"本地实现"，那条检查会立刻失败。
page_layout_text = A.page_layout_text
page_lines = A.page_lines
page_text_spans = A.page_text_spans
coverage_record = A.coverage_record
numeric_like_ratio = A.numeric_like_ratio
displaced_evidence = A.displaced_evidence
classify_residue = A.classify_residue
engine_artifact_subkind = A.engine_artifact_subkind
short_fragment = A.short_fragment
verdict_residue_class = A.verdict_residue_class
_dominant = A.dominant_residue_class


# ---------------------------------------------------------------------------
# 分布统计
# ---------------------------------------------------------------------------

def percentile(values: list, pct: float) -> float:
    """确定性最近秩百分位（不插值）：`ceil(pct/100 * n) - 1`。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    index = math.ceil(pct / 100.0 * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def distribution(values: list) -> dict:
    if not values:
        return {"count": 0}
    out = {"count": len(values)}
    for name, pct in PERCENTILES:
        out[name] = round(percentile(values, pct), 4)
    out["mean"] = round(sum(values) / len(values), 4)
    return out


def threshold_counts(values: list) -> dict:
    out = {}
    for t in THRESHOLDS:
        hits = sum(1 for v in values if v >= t)
        out[f"{t:.2f}"] = {
            "count": hits,
            "ratio": round(hits / len(values), 4) if values else 0.0,
        }
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_layouts(run_dir: pathlib.Path) -> tuple:
    layouts = {}
    per_document = {}
    for company_id, document_id, name in DOCUMENTS:
        path = SAMPLE_DIR / name
        if not path.exists():
            raise SystemExit(f"[ENVIRONMENT_DISCREPANCY] 缺少真实 PDF：{path}")
        layout, report = LB.build_page_layout_report(
            path, LB.LayoutBuildContext(company_id=company_id,
                                        document_id=document_id))
        layouts[document_id] = layout
        per_document[document_id] = report
        doc_dir = run_dir / document_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        furniture = LB.audit_furniture(layout)
        reading = LB.audit_reading_order(layout)
        summary = LB.layout_summary(layout)
        (doc_dir / "page_layout.json").write_text(
            canonical_json(layout.to_dict()), encoding="utf-8")
        (doc_dir / "layout_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        (doc_dir / "furniture_audit.json").write_text(
            json.dumps({**furniture, "evidence": report["furniture_evidence"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        (doc_dir / "reading_order_audit.json").write_text(
            json.dumps(reading, ensure_ascii=False, indent=1), encoding="utf-8")
        (doc_dir / "layout_preview.md").write_text(
            _preview(layout, furniture, reading), encoding="utf-8")
        (doc_dir / "manifest.json").write_text(json.dumps({
            "company_id": company_id,
            "document_id": document_id,
            "input_path": str(path),
            "input_sha256": layout.source_file_sha256,
            "input_size": path.stat().st_size,
            "page_count": layout.page_count,
            "document_version": layout.document_version,
            "page_layout_id": layout.page_layout_id,
            "page_layout_locator": layout.page_layout_locator,
            "page_layout_fingerprint": LB.layout_fingerprint(layout),
            "engine": layout.engine,
            "engine_version": layout.engine_version,
            "schema_version": layout.schema_version,
            "normalization_version": layout.normalization_version,
            "extraction_stats": report["extraction_stats"],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    return layouts, per_document


def _preview(layout: S.PageLayout, furniture: dict, reading: dict) -> str:
    lines = []
    lines.append(f"# PageLayout 人工复核预览（{layout.document_id}）\n")
    lines.append(f"- page_layout_id: `{layout.page_layout_id}`")
    lines.append(f"- 页数: {layout.page_count}")
    lines.append(f"- 引擎: {layout.engine} {layout.engine_version}")
    lines.append(f"- 规则版本: schema={layout.schema_version} "
                 f"normalization={layout.normalization_version}\n")
    lines.append("## 家具规则命中\n")
    lines.append("| 规则 | 持久化类型 | 页数 | 页比例 | 行数 |")
    lines.append("|---|---|---|---|---|")
    for rule, info in furniture["rules"].items():
        lines.append(f"| {rule} | {info['schema_kind']} | {info['page_count']} | "
                     f"{info['page_ratio']} | {info['line_count']} |")
    lines.append("")
    lines.append("## 代表位置（前 8 条，人工判断是否误判）\n")
    for rule, info in furniture["rules"].items():
        for sample in info["samples"][:8]:
            lines.append(
                f"- `{rule}` p{sample['page_number']} l{sample['line_index']} "
                f"bbox={sample['bbox']} → {sample['text']!r}")
    lines.append("")
    lines.append("## 阅读顺序\n")
    lines.append(f"- 多栏页: {reading['multi_column_pages'][:20]}")
    lines.append(f"- 顺序不确定页: {reading['reading_order_uncertain_pages'][:20]}")
    lines.append("")
    lines.append("## 首页前 12 行（原样，含家具）\n")
    if layout.pages:
        for line in layout.pages[0].lines[:12]:
            mark = f"[{line.furniture_kind}]" if line.is_furniture else ""
            lines.append(f"- c{line.column_index} {line.bbox} {mark} {line.text!r}")
    lines.append("")
    return "\n".join(lines)


#: 只读取数时**必须**一并取回的字段：身份闭合（`content_hash` /
#: `evidence_id` / 公司 / 文档 / 版本 / set / 类型）与内容（`text` /
#: `structured_payload`）缺一不可 —— `EvidenceBlockInput` 会重算身份并要求
#: 与自报值逐字符一致，少取一列就会 fail-closed，而不是悄悄对齐一个"差不多的块"。
EVIDENCE_COLUMNS = (
    "evidence_id", "content_hash", "page_number", "block_index", "text",
    "structured_payload", "evidence_set_version", "source_name",
    "evidence_type", "quality_flags", "builder_version",
)


def parse_structured_payload(raw):
    """DB 里的 `structured_payload` 是 JSON 文本（可空）。

    语义**复用**权威读取路径 `evidence.store._json_loads`：空 / 缺席 → `None`；
    文本 `"null"` 解析后也是 `None`。`EvidenceBlockInput.structured_payload` 的
    类型就是 `dict | None`，`evidence.ids.content_hash(text, None)` 正是生产侧
    "无结构化载荷"的正式算法 —— 因此把 `null` 判成"环境异常"会凭空造出第二套
    身份语义，反而让真实数据的身份无法与 DB 自报值闭合。

    真正**不是**对象（JSON 数组 / 标量）才是损坏载荷：那是调用方拿错列或数据被
    篡改，必须 fail-closed，不得降级成 `None`（降级等于用"没有载荷"的身份冒充）。
    """
    if raw is None or raw == "":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"[ENVIRONMENT_DISCREPANCY] structured_payload 不是合法 JSON：{e}")
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise SystemExit(
            "[ENVIRONMENT_DISCREPANCY] structured_payload 必须是 JSON 对象或 null"
            f"（实得 {type(payload).__name__}）")
    return payload


def authoritative_identity(row: dict, name: str, expected: str) -> str:
    """从**权威行本身**取身份字段；缺失 / 空 / 与查询键不符一律 fail-closed（§八）。

    旧写法 `row.get(name) or caller_value` 会在权威行缺字段时**静默补上调用方参数**，
    于是"这次对齐的是哪份文档/哪个版本"变成由调用方声明决定，而不是由证据来源决定。
    这里把该旁路关掉：行里没有就报环境差异，绝不用调用方参数补齐。
    """
    value = row.get(name)
    if not isinstance(value, str) or value.strip() == "":
        raise SystemExit(
            f"[ENVIRONMENT_DISCREPANCY] 权威 Evidence 行的 {name} 缺失或为空"
            f"（得到 {value!r}，document_id={row.get('document_id')!r} / "
            f"page={row.get('page_number')!r} / block={row.get('block_index')!r}）；"
            f"身份字段不得由调用方参数补齐（fail-closed）")
    if value != expected:
        raise SystemExit(
            f"[ENVIRONMENT_DISCREPANCY] 权威 Evidence 行的 {name}={value!r} 与查询键 "
            f"{expected!r} 不一致；不得用另一份文档/版本的行冒充本次对齐输入"
            f"（fail-closed）")
    return value


def evidence_block_input(company_id: str, document_id: str, version: str, row: dict):
    """把一行 Evidence 记录变成**完整身份**的生产输入对象（失败即 fail-closed）。

    身份全部取自**权威行本身**：`company_id` / `document_id` / `document_version`
    必须逐字符等于查询键（缺项或错配即报环境差异，不允许调用方补齐）；
    `content_hash` / `evidence_block_id` 由 `EvidenceBlockInput.__post_init__`
    用 `evidence.ids` 的正式接口重算并比对。
    """
    return A.EvidenceBlockInput(
        company_id=authoritative_identity(row, "company_id", company_id),
        document_id=authoritative_identity(row, "document_id", document_id),
        document_version=authoritative_identity(row, "document_version", version),
        evidence_set_version=row["evidence_set_version"],
        evidence_block_id=row["evidence_id"],
        content_hash=row["content_hash"],
        evidence_type=row["evidence_type"],
        page_number=row["page_number"],
        block_index=row["block_index"],
        text=row["text"] or "",
        structured_payload=parse_structured_payload(row.get("structured_payload")))


class ReadOnlyEvidenceGateway:
    """§八 **受信任只读** Evidence gateway：由只读 DB 原语产出权威 `EvidenceSetSnapshot`。

    成员清单**只**能来自库里的 current evidence set：本类不 import 数据库写路径，也不
    接受调用方传入的成员列表，因此"正式集合入口提交的那一份"不可能与库里那份分叉。
    非 current / 无 set / 空集合 / 证据类型越出封闭集合一律 fail-closed。
    """

    gateway_version = V.EVIDENCE_SET_GATEWAY_VERSION

    def __init__(self, db_path):
        self._db_path = db_path

    def load_snapshot(self, *, company_id: str, document_id: str,
                      document_version: str) -> "A.EvidenceSetSnapshot":
        set_version = current_evidence_set_ro(
            self._db_path, company_id, document_id, document_version)
        if not set_version:
            raise SystemExit(
                f"[ENVIRONMENT_DISCREPANCY] {document_id} 没有 status='current' 的 "
                f"evidence set；不得用调用方提交的清单冒充权威集合（fail-closed）")
        blocks = list_document_evidence_ro(
            self._db_path, company_id, document_id, document_version, set_version)
        if not blocks:
            raise SystemExit(
                f"[ENVIRONMENT_DISCREPANCY] {document_id} 的 current evidence set "
                f"{set_version!r} 里一个成员都没有；空集合不得被表述为对齐输入"
                f"（fail-closed）")
        members = tuple(sorted(
            (A.EvidenceSetMember(
                evidence_id=b.evidence_id, content_hash=b.content_hash,
                evidence_type=b.evidence_type, page_number=b.page_number,
                block_index=b.block_index) for b in blocks),
            key=lambda m: m.sort_key))
        return A.EvidenceSetSnapshot(
            company_id=company_id, document_id=document_id,
            document_version=document_version, evidence_set_version=set_version,
            status="current", members=members, block_count=len(members),
            fingerprint=A.snapshot_fingerprint(
                company_id=company_id, document_id=document_id,
                document_version=document_version,
                evidence_set_version=set_version, status="current",
                gateway_version=self.gateway_version, members=members),
            gateway_version=self.gateway_version)


def load_evidence(layouts: dict) -> list:
    """只读读取三份文档的全部 EvidenceBlock（不抽样、不排除）。"""
    conn = _open_readonly_conn(EVIDENCE_DB)
    if conn is None:
        raise SystemExit(f"[ENVIRONMENT_DISCREPANCY] 只读打开失败：{EVIDENCE_DB}")
    rows = []
    columns = ", ".join(("company_id", "document_id", "document_version")
                        + EVIDENCE_COLUMNS)
    try:
        conn.row_factory = sqlite3.Row
        for company_id, document_id, _ in DOCUMENTS:
            version = None
            for candidate in conn.execute(
                    "SELECT DISTINCT document_version FROM evidence_blocks "
                    "WHERE company_id=? AND document_id=?",
                    (company_id, document_id)).fetchall():
                if candidate[0] == layouts[document_id].document_version:
                    version = candidate[0]
            if version is None:
                raise SystemExit(
                    f"[ENVIRONMENT_DISCREPANCY] {document_id} 的 Evidence "
                    f"document_version 与磁盘 PDF 哈希不一致（不得静默跳过）")
            for row in conn.execute(
                    f"SELECT {columns} "
                    "FROM evidence_blocks WHERE company_id=? AND document_id=? "
                    "AND document_version=? ORDER BY page_number, block_index",
                    (company_id, document_id, version)).fetchall():
                rows.append((company_id, document_id, version, dict(row)))
    finally:
        conn.close()
    if not rows:
        raise SystemExit("[ENVIRONMENT_DISCREPANCY] 一份 EvidenceBlock 都没读到")
    return rows


def diagnose(layouts: dict, rows: list) -> list:
    """对全部存量 `EvidenceBlock` 调用**唯一生产对齐核心**，补齐评审视图。

    verdict / is_citable 由生产核心给出：`TextAlignmentRecord.__post_init__` 会
    重算 coverage / residue_class / verdict，调用方**无法**自报。本函数只把诊断量
    整理成可落盘、可复核的形态，并如实公开生产核心**拒绝产出记录**的块。
    """
    records = []
    for company_id, document_id, version, row in rows:
        layout = layouts[document_id]
        page_number = row["page_number"]
        # 身份由生产输入类型**重算**（公司 / 文档 / 版本 / set / 内容哈希 /
        # evidence_id 全部逐字符比对），评测侧不自报任何一条。
        block_input = evidence_block_input(company_id, document_id, version, row)
        alignment = A.align_block(layout, block_input)
        lines = page_lines(layout, page_number)
        rec = {
            "company_id": company_id,
            "document_id": document_id,
            "document_version": version,
            "evidence_set_version": row["evidence_set_version"],
            "evidence_id": row["evidence_id"],
            "content_hash": row["content_hash"],
            "page_number": page_number,
            "block_index": row["block_index"],
            "source_name": row["source_name"],
            "evidence_type": row["evidence_type"],
            "quality_flags": row["quality_flags"],
            "builder_version": row["builder_version"],
            "raw_char_count": len(row["text"] or ""),
            "normalized_block_len": alignment.block_char_length,
            "normalized_page_len": len(page_layout_text(layout, page_number)),
            "layout_line_count": len(lines),
            "empty_block": alignment.empty_block,
            "coverage": round(alignment.exact_coverage, 6),
            "matched_chars": alignment.matched_chars,
            "unmatched_chars": alignment.unmatched_chars,
            "residue_classes": dict(alignment.residue_char_counts),
            "residue_subkinds": dict(alignment.residue_subkind_counts),
            # 描述性主类（按字符数取；**只用于报告阅读**，不用于判定）。
            "residue_class": alignment.residue_class,
            # 门槛类：含任何 unexplained 即为 unexplained（**判定一律用它**）。
            "verdict_residue_class": alignment.verdict_residue_class,
            "dominant_class_masks_unexplained": (
                alignment.has_unexplained
                and alignment.residue_class != "unexplained"),
            "unexplained_chars": alignment.unexplained_chars,
            "unlocatable_chars": alignment.unlocatable_chars,
            "has_unexplained": alignment.has_unexplained,
            # 未解释字符按失败原因拆分（重复消费 / 搜索预算，必须单列）。
            "unexplained_duplicate_chars": alignment.unexplained_duplicate_chars,
            "unexplained_search_exhausted_chars":
                alignment.unexplained_search_exhausted_chars,
            # 敏感性对照：低信息量残差若改用宽松片段长度就会"被解释"的部分。
            "lenient_explained_chars": alignment.lenient_explained_chars,
            "unexplained_is_lenient_only": (
                alignment.has_unexplained
                and alignment.lenient_explained_chars
                == alignment.unexplained_chars),
            "residue_fragments": [
                {"class": segment.residue_class, "subkind": segment.subkind,
                 "span": [segment.start, segment.end],
                 "length": len(segment.text), "reason": segment.reason,
                 "sample": short_fragment(segment.text)}
                for segment in alignment.residue_segments[:6]],
            "page_layout_id": layout.page_layout_id,
            # 生产核心是否真正产出了正式记录；拒绝产出的块必须能被单独审计。
            "alignment_id": (None if alignment.record is None
                             else alignment.record.alignment_id),
            "char_map_segments": len(alignment.char_map),
            "record_refused": alignment.record_refusal is not None,
            "record_refusal_reason": alignment.record_refusal,
            # 判定语义**直接复用** TS1 冻结的 `compute_alignment_verdict`（在生产
            # 核心内部），不另起一套词表。`ALIGN_MIN` 是单一阈值 0.90：低于阈值
            # 一律 unaligned；达到阈值但门槛类含任何 unexplained 一律
            # partially_aligned；只有达到阈值且无 unexplained 才是 aligned 且可引用。
            "verdict_now": alignment.verdict,
            "is_citable_now": alignment.is_citable,
            "recheck_line": _recheck(layout, page_number,
                                     alignment.matched_chars, lines),
        }
        records.append(rec)
    return records


def _recheck(layout: S.PageLayout, page_number: int, matched_chars: int,
             lines: list) -> dict:
    """可复核定位：覆盖片段落在哪条真实版式行上（报告视图，不参与判定）。"""
    if not lines:
        return {"page_number": page_number, "line_index": None,
                "note": "该页无版式行"}
    cursor = 0
    for line in lines:
        cursor += len(tight(line.text))
        if cursor >= matched_chars and not line.is_furniture:
            return {"page_number": page_number, "line_index": line.line_index,
                    "line_bbox": list(line.bbox),
                    "line_text_head": short_fragment(line.text, 30)}
    last = lines[-1]
    return {"page_number": page_number, "line_index": last.line_index,
            "line_bbox": list(last.bbox),
            "line_text_head": short_fragment(last.text, 30)}


def summarize(records: list, layouts: dict) -> dict:
    by_doc = {}
    for company_id, document_id, _ in DOCUMENTS:
        subset = [r for r in records if r["document_id"] == document_id]
        by_doc[document_id] = _doc_summary(subset, document_id)
    values = [r["coverage"] for r in records]
    overall = _doc_summary(records, "ALL")
    overall["document_count"] = len(DOCUMENTS)
    overall["page_layout_ids"] = {d: layouts[d].page_layout_id for d in layouts}
    return {"overall": overall, "per_document": by_doc}


def _records_by_document(records: list) -> list:
    """按 `document_id` 分组（键序确定：按 `DOCUMENTS` 声明顺序）。"""
    groups = []
    for _, document_id, _ in DOCUMENTS:
        groups.append((document_id,
                       [r for r in records if r["document_id"] == document_id]))
    return groups


def _doc_summary(records: list, name: str) -> dict:
    values = [r["coverage"] for r in records]
    low = [r for r in records if r["coverage"] < 0.90]
    unattributed = [r for r in records if r["coverage"] < 0.90
                    and r["has_unexplained"]]
    classes: dict = {}
    subkinds: dict = {}
    for rec in records:
        for klass, chars in rec["residue_classes"].items():
            classes[klass] = classes.get(klass, 0) + chars
        for subkind, chars in rec["residue_subkinds"].items():
            subkinds[subkind] = subkinds.get(subkind, 0) + chars
    return {
        "name": name,
        "block_count": len(records),
        "empty_blocks": sum(1 for r in records if r["empty_block"]),
        "distribution": distribution(values),
        "thresholds": threshold_counts(values),
        "below_0_90": {
            "count": len(low),
            "ratio": round(len(low) / len(records), 4) if records else 0.0,
            "with_unexplained_residue": len(unattributed),
        },
        "unexplained": {
            "blocks": sum(1 for r in records if r["has_unexplained"]),
            "blocks_ratio": round(
                sum(1 for r in records if r["has_unexplained"]) / len(records), 4)
            if records else 0.0,
            "chars": sum(r["unexplained_chars"] for r in records),
            "unlocatable_chars": sum(r["unlocatable_chars"] for r in records),
            "blocks_lenient_only": sum(
                1 for r in records if r["unexplained_is_lenient_only"]),
            # P1-1：因"无法建立注入式非重叠映射"而判未解释的字符（重复文本被
            # 重复消费 / 源区间重叠）。必须单列，供裁决时区分"真缺字"与"消费冲突"。
            "duplicate_assignment_chars": sum(
                r["unexplained_duplicate_chars"] for r in records),
            "search_exhausted_chars": sum(
                r["unexplained_search_exhausted_chars"] for r in records),
        },
        # TS2 最终关闭轮：冻结阈值 0.90 下到底有多少块可引用。
        # 必须等于 `align_min_status()["formal"]["citable_blocks_now"]`（同一重算口径）。
        "citability_now": {
            "align_min": V.ALIGN_MIN,
            "align_min_frozen": V.ALIGN_MIN is not None,
            "citable_blocks": sum(1 for r in records if r["is_citable_now"]),
            "note": "冻结阈值 0.90 下 `is_citable()` 只对 aligned（达标且无 "
                    "unexplained）返回 True；部分/未对齐一律不可引用。",
        },
        "residue_chars_by_class": {k: classes[k] for k in sorted(classes)},
        # `residue_class` 只是描述性主类；含 unexplained 的块在判定时一律用
        # `verdict_residue_class`。两者不同的块数必须如实公开。
        "dominant_class_masks_unexplained": {
            "blocks": sum(1 for r in records
                          if r["dominant_class_masks_unexplained"]),
            "note": "residue_class 为按字符数取的描述性主类，可能被更长的温和类"
                    "掩盖；门槛判定一律用 verdict_residue_class（含任何 "
                    "unexplained 即为 unexplained），该字段不参与任何判定",
        },
        "low_coverage_by_verdict_class": {
            klass: sum(1 for r in low if r["verdict_residue_class"] == klass)
            for klass in sorted({r["verdict_residue_class"] for r in low})
        },
        "residue_chars_by_subkind": {k: subkinds[k] for k in sorted(subkinds)},
        "low_coverage_by_class": {
            klass: sum(1 for r in low if r["residue_class"] == klass)
            for klass in sorted({r["residue_class"] for r in low})
        },
        "worst": sorted(
            ({"coverage": r["coverage"], "page_number": r["page_number"],
              "block_index": r["block_index"], "evidence_id": r["evidence_id"],
              "residue_class": r["residue_class"],
              "verdict_residue_class": r["verdict_residue_class"],
              "unexplained_chars": r["unexplained_chars"],
              "recheck_line": r.get("recheck_line"),
              "reasons": [f["reason"] for f in r["residue_fragments"][:3]],
              "samples": [f["sample"] for f in r["residue_fragments"][:3]]}
             for r in records), key=lambda x: x["coverage"])[:10],
        # 未解释字符**最多**的块（附真实来源位置与简短归因，不泄露整段原文）。
        "most_unexplained": sorted(
            ({"coverage": r["coverage"], "page_number": r["page_number"],
              "block_index": r["block_index"], "evidence_id": r["evidence_id"],
              "residue_class": r["residue_class"],
              "verdict_residue_class": r["verdict_residue_class"],
              "unexplained_chars": r["unexplained_chars"],
              "block_len": r["normalized_block_len"],
              "subkinds": r["residue_subkinds"],
              "recheck_line": r.get("recheck_line"),
              "reasons": [f["reason"] for f in r["residue_fragments"][:2]],
              "samples": [f["sample"] for f in r["residue_fragments"][:2]]}
             for r in records if r["has_unexplained"]),
            key=lambda x: (-x["unexplained_chars"], x["page_number"],
                           x["block_index"]))[:10],
    }


def immutability_report(before: dict, after: dict) -> dict:
    """受监管文件的前后快照比对。

    `watched_missing` 单列缺失项：缺失文件不参与"未变"结论，也不得被当成通过。
    """
    missing = sorted(path for path, meta in before.items()
                     if not meta or meta.get("missing"))
    return {
        "before": before,
        "after": after,
        "watched_missing": missing,
        "all_watched_present": not missing,
        "unchanged": before == after and not missing,
        "note": "PDF / Evidence DB / Financial DB 的 sha256、size、mtime 必须不变；"
                "缺失的受监管文件单列，不计入通过",
    }


def _threshold_states(records: list, threshold: float) -> dict:
    """单个阈值下的**三态**统计（定义见 `align_min_status` 的文档串）。

    判定一律使用 `verdict_residue_class`（门槛类：含任何 `unexplained` 即为
    `unexplained`），**不是** `residue_class`（按字符数取的描述性主类）。逐块用
    `schema.compute_alignment_verdict` **重算** verdict，再按 verdict 计数。

    输出三条**阈值无关**的不变式（全部必须成立）：

    - `invariant_holds`：`aligned + partial + unaligned == total_blocks`；
    - `aligned_with_unexplained == 0`（含未解释残差者不得判 aligned）；
    - `below_threshold_but_not_unaligned == 0`（低于阈值者必须判 unaligned）。

    第四条不变式 `citable_blocks_now == aligned_blocks` **只对冻结阈值成立**：
    `is_citable_now` 由生产常量与 `V.ALIGN_MIN` 共同决定，因此在非冻结阈值的
    敏感度诊断里不存在该字段（否则会与 `aligned_blocks` 名实不符）。

    以及两条与业务定义的**互检**：`partial == above 中带 unexplained 的块数`、
    `aligned == above 中不带 unexplained 的块数`。
    """
    above = [r for r in records if r["coverage"] >= threshold]
    below = [r for r in records if r["coverage"] < threshold]
    aligned = 0
    partial = 0
    unaligned = 0
    aligned_with_unexplained = 0
    below_but_not_unaligned = 0
    for rec in records:
        verdict = S.compute_alignment_verdict(
            rec["coverage"], rec["verdict_residue_class"], threshold)
        if verdict == "aligned":
            aligned += 1
            if rec["has_unexplained"]:
                aligned_with_unexplained += 1
        elif verdict == "partially_aligned":
            partial += 1
        else:
            unaligned += 1
        if rec["coverage"] < threshold and verdict != "unaligned":
            below_but_not_unaligned += 1
    above_with_unexplained = sum(1 for r in above if r["has_unexplained"])
    above_chars = sum(r["normalized_block_len"] for r in above)
    above_unexplained = sum(r["unexplained_chars"] for r in above)
    char_ratio = above_unexplained / above_chars if above_chars else 0.0
    total = len(records)
    return {
        "total_blocks": total,
        "aligned_blocks": aligned,
        "aligned_ratio": round(aligned / total, 6) if total else 0.0,
        "partial_blocks": partial,
        "partial_ratio": round(partial / total, 6) if total else 0.0,
        "unaligned_blocks": unaligned,
        "unaligned_ratio": round(unaligned / total, 6) if total else 0.0,
        "above_threshold_blocks": len(above),
        "above_threshold_ratio": round(len(above) / total, 6) if total else 0.0,
        "below_threshold_blocks": len(below),
        "below_threshold_ratio": round(len(below) / total, 6) if total else 0.0,
        # 不变式 1：三态守恒。
        "invariant_holds": aligned + partial + unaligned == total,
        # 不变式 3（反例）：放行面内**不允许**"含未解释残差却被判 aligned"。
        "aligned_with_unexplained": aligned_with_unexplained,
        "no_aligned_with_unexplained": aligned_with_unexplained == 0,
        # 不变式 4（反例）：低于阈值者**必须**是 unaligned。
        "below_threshold_but_not_unaligned": below_but_not_unaligned,
        "no_below_threshold_but_not_unaligned": below_but_not_unaligned == 0,
        # 与业务定义（是否含 unexplained）的互检，两者必须一致。
        "partial_blocks_equals_above_with_unexplained":
            partial == above_with_unexplained,
        "aligned_blocks_equals_above_without_unexplained":
            aligned == len(above) - above_with_unexplained,
        "above_threshold_unexplained_block_count": above_with_unexplained,
        "above_threshold_unexplained_char_count": above_unexplained,
        "above_threshold_released_chars": above_chars,
        "above_threshold_unexplained_char_ratio": round(char_ratio, 6),
        "above_threshold_unexplained_only_low_information": sum(
            1 for r in above if r["has_unexplained"]
            and r["unexplained_is_lenient_only"]),
    }


#: 正式结果与诊断产物在键名上必须**不可混淆**：`formal` 是冻结阈值下的真实产物，
#: `diagnostic_threshold_table` 只是敏感度表（**非推荐、非裁决依据**）。
FROZEN_DECISION_SOURCE = "USER_CODEX_APPROVED_2026_09_17"


def width_fold_removal(records: list, current_run_dir=None) -> dict:
    """本轮删除 `width_fold` 规则造成的**真实分布变化**（与上一轮产物逐块比对）。

    口径（必须按**类别**比对，不得拿类别名去子类字典里查）：

    - 上一轮的 `residue_subkinds` 里出现过 `width_fold` 的块 = 受该规则影响的块；
    - `width_fold` 是 `engine_artifact` 下的子类（规则已删除）。删除后相关残差
      **只有**在原始未折叠字符能绑定到同页真实、两两不重叠的 occurrence 时，才可
      归为 `column_reorder`；该证明允许列序不同，**不等于同一逻辑位置**。剩下的
      差额按 `residue_classes` 增量归入 `unexplained`，分配不掉的记
      `blocks_otherwise_unchanged`（如实上报，不解释成"被放过"）。
    - `column_reorder` 是"残差已被结构性解释"的**类别**，不单独决定引用资格：
      每个受影响块是否可引用，只看它自己的 `coverage >= ALIGN_MIN`、
      `has_unexplained` 与最终 `verdict`（`aligned` 才可引用）。因此本函数必须
      **逐块派生真实终态**，不得写成"受影响块都不可引用"。

    返回：

    - `previous_run_dir` / `previous_run_is_last_rule_era`：比对基线必须是**仍在
      `width_fold` 规则下产出**的最近一份历史产物（不是本轮自己的中间产物），
      否则"删除前后"就退化成了自我比对；
    - `previous_blocks_with_width_fold_subkind` / `previous_width_fold_chars`：
      该基线产物中受该规则影响的块数与字符数；
    - `blocks_moved_to_unexplained` / `chars_moved_to_unexplained`：本轮归入
      `unexplained` 的块数与字符数；
    - `blocks_moved_to_column_reorder` / `chars_moved_to_column_reorder`：本轮
      可在同页真实、两两不重叠 occurrence 上被证实、归入 `column_reorder` 的
      块数与字符数；
    - `blocks_otherwise_unchanged`：既没进 `unexplained` 也没增加
      `column_reorder` 的块数；
    - `affected_block_count` / `affected_verdict_counts` / `affected_citable_count` /
      `affected_noncitable_count` / `affected_has_unexplained_count` /
      `affected_below_threshold_count` / `affected_invariants`：受影响块按**本轮
      实际 records 确定性重算**的终态统计与守恒不变式（不硬编码任何数字）；
    - `affected_blocks`：受影响块的逐块行（文档 / 物理页号 / 块序号 / Evidence id /
      coverage / verdict / 是否可引用 / 是否含未解释 / 归因桶 / 前后类别与子类），
      供与 `coverage_blocks.json` 逐块对账；
    - `typical_positions`：归入 `unexplained` 的块位置，最多 20 条。

    找不到历史产物时如实返回 `comparable=False`，不猜。
    """
    previous_dir, previous = load_rule_era_blocks(current_run_dir)
    if previous is None:
        return {
            "removed_rule": "width_fold",
            "comparable": False,
            "blocks_moved_to_unexplained": 0,
            "affected_block_count": 0,
            "affected_verdict_counts": {},
            "affected_citable_count": 0,
            "affected_noncitable_count": 0,
            "affected_has_unexplained_count": 0,
            "affected_below_threshold_count": 0,
            "affected_invariants": {"all_hold": False},
            "affected_blocks": [],
            "typical_positions": [],
            "note": "历史产物里找不到仍在 width_fold 规则下产出的逐块记录：本轮"
                    "不产出受影响块的逐块终态统计，也不产出‘由 width_fold 改判 "
                    "unexplained’的逐块位置。",
        }
    previous_by_id = {r["evidence_id"]: r for r in previous}
    affected = [r for r in previous
                if (r.get("residue_subkinds") or {}).get("width_fold", 0) > 0]
    moved, to_cr, unchanged = [], [], []
    for rec in records:
        old = previous_by_id.get(rec["evidence_id"])
        if old is None:
            continue
        old_wf = (old.get("residue_subkinds") or {}).get("width_fold", 0)
        if old_wf <= 0:
            continue
        old_classes = old.get("residue_classes") or {}
        new_classes = rec.get("residue_classes") or {}
        delta_unexpl = max(0, new_classes.get("unexplained", 0)
                           - old_classes.get("unexplained", 0))
        delta_cr = max(0, new_classes.get("column_reorder", 0)
                       - old_classes.get("column_reorder", 0))
        to_unexpl_chars = min(old_wf, delta_unexpl)
        to_cr_chars = min(old_wf - to_unexpl_chars, delta_cr)
        row = {
            "document_id": rec["document_id"],
            "page_number": rec["page_number"],
            "block_index": rec["block_index"],
            "evidence_id": rec["evidence_id"],
            "coverage": rec["coverage"],
            "verdict_now": rec.get("verdict_now"),
            "is_citable_now": bool(rec.get("is_citable_now")),
            "has_unexplained": bool(rec.get("has_unexplained",
                                            rec.get("unexplained_chars", 0) > 0)),
            "previous_width_fold_chars": old_wf,
            "chars_moved_to_unexplained": to_unexpl_chars,
            "chars_moved_to_column_reorder": to_cr_chars,
            "unexplained_chars_now": rec.get("unexplained_chars", 0),
            "old_classes": {k: old_classes[k] for k in sorted(old_classes)},
            "new_classes": {k: new_classes[k] for k in sorted(new_classes)},
            "old_subkinds": {k: old["residue_subkinds"][k]
                             for k in sorted(old["residue_subkinds"])},
            "new_subkinds": {k: rec["residue_subkinds"][k]
                             for k in sorted(rec["residue_subkinds"])},
        }
        if to_unexpl_chars > 0:
            row["attribution"] = "unexplained"
            moved.append(row)
        elif to_cr_chars > 0:
            row["attribution"] = "column_reorder"
            to_cr.append(row)
        else:
            row["attribution"] = "otherwise_unchanged"
            unchanged.append(row)
    moved.sort(key=lambda m: (-m["chars_moved_to_unexplained"], m["document_id"],
                              m["page_number"], m["block_index"]))

    # 受影响块的**真实终态**：逐块从本轮 records 的 coverage / has_unexplained /
    # verdict_now 确定性派生，绝不写死任何数字，也绝不把三个归因桶当成三个终态桶。
    affected_rows = sorted(moved + to_cr + unchanged,
                           key=lambda r: (r["document_id"], r["page_number"],
                                          r["block_index"], r["evidence_id"]))
    affected_count = len(affected_rows)
    verdict_counts: dict[str, int] = {}
    for row in affected_rows:
        verdict_counts[row["verdict_now"]] = verdict_counts.get(row["verdict_now"], 0) + 1
    citable_count = sum(1 for r in affected_rows if r["is_citable_now"])
    noncitable_count = sum(1 for r in affected_rows if not r["is_citable_now"])
    has_unexplained_count = sum(1 for r in affected_rows if r["has_unexplained"])
    below_threshold_count = sum(1 for r in affected_rows
                                if r["coverage"] < V.ALIGN_MIN)
    invariants = {
        "verdict_counts_sum_equals_affected":
            sum(verdict_counts.values()) == affected_count,
        "citable_plus_noncitable_equals_affected":
            citable_count + noncitable_count == affected_count,
        "citable_equals_aligned_verdict_count":
            citable_count == verdict_counts.get("aligned", 0),
        "affected_equals_previous_rule_era_blocks":
            affected_count == len(affected),
        # 反向不变式：`column_reorder` 不单独决定引用资格。
        "citable_with_unexplained_is_zero":
            sum(1 for r in affected_rows
                if r["is_citable_now"] and r["has_unexplained"]) == 0,
        "below_threshold_but_citable_is_zero":
            sum(1 for r in affected_rows
                if r["is_citable_now"] and r["coverage"] < V.ALIGN_MIN) == 0,
    }
    invariants["all_hold"] = all(invariants.values())
    return {
        "removed_rule": "width_fold",
        "comparable": True,
        "previous_run_dir": str(previous_dir),
        "previous_run_is_last_rule_era": True,
        "method": "按 residue_classes（类别→字符数）逐块增量归属，不按子类名跨类查询",
        "previous_blocks_with_width_fold_subkind": len(affected),
        "previous_width_fold_chars": sum(
            (r.get("residue_subkinds") or {}).get("width_fold", 0)
            for r in previous),
        "blocks_moved_to_unexplained": len(moved),
        "chars_moved_to_unexplained": sum(
            m["chars_moved_to_unexplained"] for m in moved),
        "blocks_moved_to_column_reorder": len(to_cr),
        "chars_moved_to_column_reorder": sum(
            m["chars_moved_to_column_reorder"] for m in to_cr),
        "blocks_otherwise_unchanged": len(unchanged),
        "affected_block_count": affected_count,
        "affected_verdict_counts": {k: verdict_counts[k]
                                    for k in sorted(verdict_counts)},
        "affected_citable_count": citable_count,
        "affected_noncitable_count": noncitable_count,
        "affected_has_unexplained_count": has_unexplained_count,
        "affected_below_threshold_count": below_threshold_count,
        "affected_invariants": invariants,
        "affected_blocks": affected_rows,
        "typical_positions": moved[:20],
        "note": (
            f"比对基线（规则生效时的最近一份产物）里有 {len(affected)} 个块 / "
            f"{sum((r.get('residue_subkinds') or {}).get('width_fold', 0) for r in previous)} "
            "个字符带 `width_fold` 子类（该子类属 `engine_artifact` 类）。删除该规则后"
            "按**同类别逐块增量**归属："
            f"{len(moved)} 个块 / {sum(m['chars_moved_to_unexplained'] for m in moved)} "
            "个字符归入 `unexplained`；"
            f"{len(to_cr)} 个块 / {sum(m['chars_moved_to_column_reorder'] for m in to_cr)} "
            "个字符归入 `column_reorder`；"
            f"{len(unchanged)} 个块在类别维度上两者皆未增加。\n\n"
            "删除 `width_fold` 后，相关残差只有在原始未折叠字符能够绑定到"
            "同页真实、两两不重叠的 occurrence 时，才可归为 `column_reorder`。"
            "该证明允许列序不同，不等于同一逻辑位置。\n\n"
            "`column_reorder` 是“残差已被结构性解释”的类别，但不会单独决定引用资格。"
            "块只有在 `coverage >= ALIGN_MIN`、且不存在 `unexplained`、最终 "
            "`verdict == \"aligned\"` 时才可引用。\n\n"
            f"受影响块的**真实终态**（逐块从本轮 records 重算）："
            f"`affected_block_count = {affected_count}`，"
            f"`affected_verdict_counts = {json.dumps({k: verdict_counts[k] for k in sorted(verdict_counts)}, ensure_ascii=False)}`，"
            f"`affected_citable_count = {citable_count}`，"
            f"`affected_noncitable_count = {noncitable_count}`，"
            f"`affected_has_unexplained_count = {has_unexplained_count}`，"
            f"`affected_below_threshold_count = {below_threshold_count}`；"
            f"守恒不变式 {json.dumps(invariants, ensure_ascii=False)}。"
            "逐块行见 `affected_blocks`（与 `coverage_blocks.json` 同 "
            "Evidence id 可逐条对账）。"),
    }


def align_min_status(summary: dict) -> dict:
    """`ALIGN_MIN` 的**冻结状态**与冻结阈值下的**正式三态结果**。

    阈值已由**用户 + Codex** 于 2026-09-17 批准为 `0.90`（见
    `document_structure.versions.ALIGN_MIN`）。本执行器**不推荐、不重选、不重开
    讨论**：`status` 恒为 `ALIGN_MIN_FROZEN`，`decision_source` 恒为
    `USER_CODEX_APPROVED_2026_09_17`。

    三态**正交**定义 —— 对每个块用 TS1 冻结的
    `schema.compute_alignment_verdict(coverage, verdict_residue_class, T)`
    **重算**（门槛类含任何 `unexplained` 即为 `unexplained`，见
    `verdict_residue_class`；**不得**用按字符数取的描述性主类）：

    - `aligned`：`coverage >= T` **且**没有 `unexplained` —— **唯一**可引用；
    - `partially_aligned`：`coverage >= T` 但仍有 `unexplained`；
    - `unaligned`：`coverage < T`。

    四条正式不变式（`formal["invariants"]`，全部必须为 True）：

    1. `aligned + partial + unaligned == total_blocks`；
    2. `citable_blocks_now == aligned`；
    3. `aligned_with_unexplained == 0`；
    4. `below_threshold_but_not_unaligned == 0`。

    `diagnostic_threshold_table` 是**非冻结阈值**下的敏感度诊断表：保留它是为了
    让人看到"换一个阈值会长什么样"，它**不是**推荐、**不参与**任何裁决，也不得
    被读作候选区间。
    """
    records = summary["_records"]
    per_doc = {name: subset for name, subset in _records_by_document(records)}
    frozen = V.ALIGN_MIN
    if frozen is None:
        raise SystemExit("ALIGN_MIN 未冻结：本轮的正式结果无法计算")
    table = []
    for key in sorted(summary["overall"]["thresholds"]):
        threshold = float(key)
        entry = {"align_min": threshold,
                 **_threshold_states(records, threshold)}
        below = [r for r in records if r["coverage"] < threshold]
        classes: dict = {}
        for rec in below:
            for klass, chars in rec["residue_classes"].items():
                classes[klass] = classes.get(klass, 0) + chars
        entry.update({
            "excluded_blocks": len(below),
            "excluded_dominant_classes": A.dominant_residue_class(classes),
            "excluded_residue_chars": {k: classes[k] for k in sorted(classes)},
            "excluded_unexplained_block_count": sum(
                1 for r in below if r["has_unexplained"]),
            "excluded_unexplained_char_count": sum(
                r["unexplained_chars"] for r in below),
        })
        table.append(entry)

    frozen_states = _threshold_states(records, frozen)
    # 可引用面是**冻结阈值**下的概念：`is_citable_now` 由生产常量的 verdict 与
    # `V.ALIGN_MIN` 一起决定，因此只在正式结果里统计，不进敏感度诊断表。
    citable_now = sum(1 for r in records if r.get("is_citable_now"))
    formal = {
        "align_min": frozen,
        "total_blocks": frozen_states["total_blocks"],
        "aligned_blocks_at_frozen_threshold": frozen_states["aligned_blocks"],
        "partial_blocks_at_frozen_threshold": frozen_states["partial_blocks"],
        "unaligned_blocks_at_frozen_threshold": frozen_states["unaligned_blocks"],
        "citable_blocks_now": citable_now,
        "aligned_with_unexplained": frozen_states["aligned_with_unexplained"],
        "below_threshold_but_not_unaligned":
            frozen_states["below_threshold_but_not_unaligned"],
        "above_threshold_unexplained_block_count":
            frozen_states["above_threshold_unexplained_block_count"],
        "above_threshold_unexplained_char_count":
            frozen_states["above_threshold_unexplained_char_count"],
        "above_threshold_unexplained_char_ratio":
            frozen_states["above_threshold_unexplained_char_ratio"],
        "invariants": {
            "three_state_conservation": frozen_states["invariant_holds"],
            "citable_blocks_now_equals_aligned":
                citable_now == frozen_states["aligned_blocks"],
            "aligned_with_unexplained_is_zero":
                frozen_states["no_aligned_with_unexplained"],
            "below_threshold_but_not_unaligned_is_zero":
                frozen_states["no_below_threshold_but_not_unaligned"],
        },
        "per_document": {
            name: {
                "total_blocks": states["total_blocks"],
                "aligned_blocks_at_frozen_threshold": states["aligned_blocks"],
                "partial_blocks_at_frozen_threshold": states["partial_blocks"],
                "unaligned_blocks_at_frozen_threshold": states["unaligned_blocks"],
                "citable_blocks_now": sum(
                    1 for r in subset if r.get("is_citable_now")),
                "invariant_holds": states["invariant_holds"],
            }
            for name, subset in sorted(per_doc.items())
            for states in (_threshold_states(subset, frozen),)
        },
    }
    formal["invariants"]["all_hold"] = all(formal["invariants"].values())
    return {
        "status": "ALIGN_MIN_FROZEN",
        "align_min": frozen,
        "align_min_frozen": True,
        "decision_source": FROZEN_DECISION_SOURCE,
        "decided_by_executor": False,
        "verdict_vocabulary": list(S.ALIGNMENT_VERDICTS),
        "verdict_source": "document_structure.schema.compute_alignment_verdict",
        "verdict_rules": [
            "coverage <= 0 ⇒ unaligned",
            "coverage < ALIGN_MIN ⇒ unaligned",
            "coverage >= ALIGN_MIN 且含 unexplained ⇒ partially_aligned",
            "coverage >= ALIGN_MIN 且无 unexplained ⇒ aligned（唯一可引用）",
        ],
        "state_definition": {
            "gate_class": "块级门槛类 verdict_residue_class：块内含**任何** "
                          "unexplained 残差即为 'unexplained'，否则取描述性主类；"
                          "判定一律用它，不用按字符数取的 residue_class",
            "aligned_blocks_at_frozen_threshold":
                "coverage >= ALIGN_MIN 且门槛类 != 'unexplained'（唯一可引用）",
            "partial_blocks_at_frozen_threshold":
                "coverage >= ALIGN_MIN 但仍有 unexplained（不可引用）",
            "unaligned_blocks_at_frozen_threshold": "coverage < ALIGN_MIN"
                                                    "（不可引用）",
            "invariant": "aligned + partial + unaligned == total_blocks",
            "counter_invariant": "aligned_with_unexplained == 0",
            "counter_invariant_2": "below_threshold_but_not_unaligned == 0",
        },
        "formal": formal,
        # 敏感度诊断表：**非推荐、非裁决依据**，仅为可读性保留。
        "diagnostic_threshold_table": table,
        "diagnostic_threshold_table_note":
            "非冻结阈值下的敏感度诊断表；ALIGN_MIN 已冻结为 "
            f"{frozen}，本表不构成推荐、不构成候选区间、不参与任何裁决。",
        "note": "ALIGN_MIN 已由用户 + Codex 于 2026-09-17 批准冻结为单一文本对齐"
                "阈值；表格结构由未来的 TableObject 路径承接，不设正文页/表格页"
                "两套阈值。执行器不得重开讨论，也不得自行宣布 TS2 关闭。",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="TS2 真实文档版式层验收与全量对齐分布诊断（只读）")
    parser.add_argument("--run-id", default=None, help="新 run_id（不得与历史目录重复）")
    parser.add_argument("--validate-only", action="store_true",
                        help="只做环境与自检，不构建、不写任何文件")
    args = parser.parse_args(argv)

    if args.validate_only:
        report = {"layout_self_check": LB.self_check()}
        print(canonical_json(report))
        return 0 if report["layout_self_check"]["ok"] else 1

    run_id = args.run_id or datetime.datetime.now(
        datetime.timezone.utc).strftime("ts2_%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / f"tree_structure_ts2_layout_{run_id}"
    if run_dir.exists():
        raise SystemExit(f"结果目录已存在，拒绝覆盖历史结果：{run_dir}")

    watch = [EVIDENCE_DB, FINANCIAL_DB] + [
        SAMPLE_DIR / name for _, _, name in DOCUMENTS]
    before = snapshot(watch)

    run_dir.mkdir(parents=True)
    layouts, per_document = build_layouts(run_dir)
    # 确定性复核：同一输入再建一次，逐字节比对
    determinism = {}
    for company_id, document_id, name in DOCUMENTS:
        again = LB.build_page_layout(
            SAMPLE_DIR / name,
            LB.LayoutBuildContext(company_id=company_id, document_id=document_id))
        determinism[document_id] = {
            "page_layout_id_equal": again.page_layout_id
            == layouts[document_id].page_layout_id,
            "fingerprint_equal": LB.layout_fingerprint(again)
            == LB.layout_fingerprint(layouts[document_id]),
            "json_equal": canonical_json(again.to_dict())
            == canonical_json(layouts[document_id].to_dict()),
        }

    rows = load_evidence(layouts)
    records = diagnose(layouts, rows)

    by_doc_records = {}
    for record in records:
        by_doc_records.setdefault(record["document_id"], []).append(record)
    for document_id, subset in by_doc_records.items():
        doc_dir = run_dir / document_id
        (doc_dir / "coverage_distribution.json").write_text(
            json.dumps(_doc_summary(subset, document_id),
                       ensure_ascii=False, indent=1), encoding="utf-8")
        (doc_dir / "low_coverage_diagnostics.json").write_text(
            json.dumps([r for r in subset if r["coverage"] < 0.90],
                       ensure_ascii=False, indent=1), encoding="utf-8")

    summary = {"_records": records, **summarize(records, layouts)}
    (run_dir / "coverage_summary.json").write_text(
        json.dumps({k: v for k, v in summary.items() if k != "_records"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "coverage_blocks.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")

    status = align_min_status(summary)

    # 修复前后分类对比（只对历史 TS2 产物的同一批块做只读比对）。
    #   - classification_diff.json：与**紧邻的上一次**（单步 diff）；
    #   - classification_diff_vs_baseline.json：与**最早的 TS2 基线**（修复前后）。
    comparison = compare_with_previous(records, run_dir, pick="newest")
    (run_dir / "classification_diff.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=1), encoding="utf-8")
    baseline = compare_with_previous(records, run_dir, pick="oldest")
    (run_dir / "classification_diff_vs_baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    removed = width_fold_removal(records, run_dir)
    (run_dir / "width_fold_removal.json").write_text(
        json.dumps({k: v for k, v in removed.items()
                    if k != "typical_positions"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "align_min_status.md").write_text(
        _align_min_md(status, summary, baseline, removed), encoding="utf-8")

    after = snapshot(watch)
    immutability = immutability_report(before, after)
    (run_dir / "database_immutability.json").write_text(
        json.dumps(immutability, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "run_dir": str(run_dir),
        "ts2_scope": "PageLayout 构建 + 全量对齐分布诊断；未进入 TS3",
        "ts2_1_scope": "对齐残差诊断 / 可引用三态统计 / validate-only 窄范围返修",
        # 历史事实（该轮把当时的 ALIGNER_VERSION 由 al-1 升到 al-2），**不是**当前版本
        # 陈述：当前值一律只在下方 `aligner_version` 字段（读 `versions.py`）。
        "ts2_final_closure_scope": "删除 width_fold 全页搜索 / 冻结 ALIGN_MIN=0.90 / "
                                   "当时的 ALIGNER_VERSION 由 al-1 升到 al-2 / "
                                   "三态生产语义修正",
        "align_min_status": status["status"],
        "align_min": status["align_min"],
        "decision_source": status["decision_source"],
        "aligner_version": V.ALIGNER_VERSION,
        "formal_three_state": {
            "aligned_blocks_at_frozen_threshold":
                status["formal"]["aligned_blocks_at_frozen_threshold"],
            "partial_blocks_at_frozen_threshold":
                status["formal"]["partial_blocks_at_frozen_threshold"],
            "unaligned_blocks_at_frozen_threshold":
                status["formal"]["unaligned_blocks_at_frozen_threshold"],
            "citable_blocks_now": status["formal"]["citable_blocks_now"],
            "aligned_with_unexplained":
                status["formal"]["aligned_with_unexplained"],
            "below_threshold_but_not_unaligned":
                status["formal"]["below_threshold_but_not_unaligned"],
        },
        "formal_invariants": status["formal"]["invariants"],
        "width_fold_removed": {
            "comparable": removed.get("comparable"),
            "previous_run_dir": removed.get("previous_run_dir"),
            "previous_run_is_last_rule_era":
                removed.get("previous_run_is_last_rule_era"),
            "previous_blocks_with_width_fold_subkind":
                removed.get("previous_blocks_with_width_fold_subkind"),
            "previous_width_fold_chars": removed.get("previous_width_fold_chars"),
            "blocks_moved_to_unexplained":
                removed.get("blocks_moved_to_unexplained"),
            "chars_moved_to_unexplained":
                removed.get("chars_moved_to_unexplained"),
            "blocks_moved_to_column_reorder":
                removed.get("blocks_moved_to_column_reorder"),
            "chars_moved_to_column_reorder":
                removed.get("chars_moved_to_column_reorder"),
            "blocks_otherwise_unchanged":
                removed.get("blocks_otherwise_unchanged"),
            "affected_block_count": removed.get("affected_block_count"),
            "affected_verdict_counts": removed.get("affected_verdict_counts"),
            "affected_citable_count": removed.get("affected_citable_count"),
            "affected_noncitable_count": removed.get("affected_noncitable_count"),
            "affected_has_unexplained_count":
                removed.get("affected_has_unexplained_count"),
            "affected_below_threshold_count":
                removed.get("affected_below_threshold_count"),
            "affected_invariants": removed.get("affected_invariants"),
        },
        "determinism": determinism,
        "documents": [{
            "document_id": document_id,
            "input_path": per_document[document_id]["source_name"],
            "input_sha256": layouts[document_id].source_file_sha256,
            "input_size": per_document[document_id]["started_from_bytes"],
            "page_count": layouts[document_id].page_count,
            "document_version": layouts[document_id].document_version,
            "page_layout_id": layouts[document_id].page_layout_id,
            "extraction_stats": per_document[document_id]["extraction_stats"],
        } for _, document_id, _ in DOCUMENTS],
        "immutability_ok": immutability["unchanged"],
        "watched_missing": immutability["watched_missing"],
        "previous_run_compared": comparison.get("previous_run_dir"),
        "baseline_run_compared": baseline.get("previous_run_dir"),
        "ts2_1_changed_block_count": comparison.get("changed_block_count"),
        "ts2_1_gate_changed_block_count": baseline.get("gate_changed_block_count"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({
        "run_dir": str(run_dir),
        "blocks": len(records),
        "immutability_ok": immutability["unchanged"],
        "watched_missing": immutability["watched_missing"],
        "determinism": determinism,
        "overall": {k: v for k, v in summary["overall"].items()
                    if k in ("block_count", "distribution", "below_0_90",
                             "unexplained", "citability_now",
                             "residue_chars_by_class",
                             "residue_chars_by_subkind")},
        "align_min": {
            "status": status["status"],
            "align_min": status["align_min"],
            "decision_source": status["decision_source"],
            "formal_three_state": {
                "aligned": status["formal"]["aligned_blocks_at_frozen_threshold"],
                "partially_aligned":
                    status["formal"]["partial_blocks_at_frozen_threshold"],
                "unaligned":
                    status["formal"]["unaligned_blocks_at_frozen_threshold"],
                "citable_blocks_now": status["formal"]["citable_blocks_now"],
                "aligned_with_unexplained":
                    status["formal"]["aligned_with_unexplained"],
                "below_threshold_but_not_unaligned":
                    status["formal"]["below_threshold_but_not_unaligned"],
            },
            "formal_invariants": status["formal"]["invariants"],
            "per_document": status["formal"]["per_document"],
        },
        "width_fold_removal": {
            k: v for k, v in removed.items() if k != "typical_positions"},
        "classification_diff": {
            k: v for k, v in comparison.items() if k != "changed_blocks"},
        "classification_diff_vs_baseline": {
            k: v for k, v in baseline.items() if k != "changed_blocks"},
    }, ensure_ascii=False, indent=1))
    return 0


def _history_run_dirs(current_run_dir=None) -> list:
    """历史上已写出 `coverage_blocks.json` 的 TS2 产物目录，按 mtime 升序。

    当前 run 目录**必须**排除（它此刻已写出自己的 `coverage_blocks.json`，
    否则会与自己比对）。
    """
    excluded = pathlib.Path(current_run_dir).resolve() if current_run_dir else None
    history = []
    for path in sorted(RESULTS_ROOT.glob("tree_structure_ts2_layout_*/")):
        if excluded is not None and path.resolve() == excluded:
            continue
        blocks = path / "coverage_blocks.json"
        if blocks.is_file():
            history.append((blocks.stat().st_mtime, path, blocks))
    history.sort(key=lambda item: item[0])
    return history


def load_rule_era_blocks(current_run_dir=None):
    """读取**仍在 `width_fold` 规则下产出**的最近一份历史逐块记录。

    删除规则后，"删除前后的真实分布变化"必须与**规则生效时**的产物比对，而不是与
    本轮自己的中间产物比对。因此在历史产物里从新到旧找第一份 `residue_subkinds`
    中出现过 `width_fold` 的记录集合。找不到时返回 `(None, None)`，如实上报。

    只读；不中断本轮；不猜。
    """
    for _, path, blocks_file in reversed(_history_run_dirs(current_run_dir)):
        try:
            records = json.loads(blocks_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if any("width_fold" in (r.get("residue_subkinds") or {})
               for r in records):
            return path, records
    return None, None


def load_previous_blocks(current_run_dir=None, pick: str = "newest"):
    """读取历史产物里的逐块记录。

    返回 `(previous_dir, records)`；无历史产物或产物损坏时返回 `(dir_or_None,
    None)` —— 只读、不中断本轮、不猜。
    """
    history = _history_run_dirs(current_run_dir)
    if not history:
        return None, None
    _, previous_dir, previous_file = history[-1 if pick == "newest" else 0]
    try:
        return previous_dir, json.loads(previous_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return previous_dir, None


def compare_with_previous(records: list, current_run_dir=None,
                          pick: str = "newest") -> dict:
    """与另一份 TS2 产物逐块比对分类变化（只读）。

    `pick="newest"` 比**紧邻的上一次**（单步 diff）；`pick="oldest"` 比**最早的
    一次**（即 TS2.1 返修前的基线，用于"修复前后分类对比"）。

    只为人工复核每轮的返修到底改了什么：按 `evidence_id` 对齐两次的
    `residue_class`，单列 `changed_blocks`，成因词表见 `_change_cause`。
    **判定相关**的变化单独统计：门槛类在 `unexplained` 与非 `unexplained` 之间的
    迁移（`changed_to_unexplained` 变严、`changed_from_unexplained` 变松 —— 变松
    的块必须人工复核）。旧产物没有门槛类字段时按 `has_unexplained` 等价重建。
    找不到历史产物时如实记录 `previous_run_dir=None`。
    """
    out = {"previous_run_dir": None, "comparable": False}
    previous_dir, previous = load_previous_blocks(current_run_dir, pick)
    if previous is not None:
        out["previous_run_dir"] = str(previous_dir)
        out["pick"] = pick
        current = {r["evidence_id"]: r for r in records}
        old = {r["evidence_id"]: r for r in previous}
        out["previous_block_count"] = len(previous)
        out["current_block_count"] = len(records)
        shared = sorted(set(current) & set(old))
        out["comparable"] = bool(shared)
        out["shared_blocks"] = len(shared)
        out["previous_only_blocks"] = sorted(set(old) - set(current))
        out["current_only_blocks"] = sorted(set(current) - set(old))
        class_changes: dict = {}
        gate_changes: dict = {}
        changes = []
        for evidence_id in shared:
            new_class = current[evidence_id]["residue_class"]
            old_class = old[evidence_id]["residue_class"]
            new_gate = _gate_class(current[evidence_id])
            old_gate = _gate_class(old[evidence_id])
            cov_delta = round(current[evidence_id]["coverage"]
                              - old[evidence_id]["coverage"], 6)
            gate_key = f"{old_gate} -> {new_gate}"
            if old_gate != new_gate:
                gate_changes[gate_key] = gate_changes.get(gate_key, 0) + 1
            if new_class == old_class and cov_delta == 0.0 \
                    and old_gate == new_gate:
                continue
            key = f"{old_class} -> {new_class}"
            class_changes[key] = class_changes.get(key, 0) + 1
            if len(changes) < 100:
                new_subkinds = current[evidence_id].get(
                    "residue_subkinds", {})
                changes.append({
                    "evidence_id": evidence_id,
                    "document_id": current[evidence_id]["document_id"],
                    "page_number": current[evidence_id]["page_number"],
                    "block_index": current[evidence_id]["block_index"],
                    "coverage_before": old[evidence_id]["coverage"],
                    "coverage_after": current[evidence_id]["coverage"],
                    "class_before": old_class,
                    "class_after": new_class,
                    "gate_class_before": old_gate,
                    "gate_class_after": new_gate,
                    "unexplained_chars_before":
                        old[evidence_id].get("unexplained_chars", 0),
                    "unexplained_chars_after":
                        current[evidence_id]["unexplained_chars"],
                    "subkinds_after": new_subkinds,
                    "cause": _change_cause(old[evidence_id],
                                           current[evidence_id]),
                })
        out["class_change_counts"] = dict(sorted(class_changes.items()))
        out["changed_block_count"] = sum(class_changes.values())
        out["changed_blocks"] = changes
        # **判定相关**的变化：门槛类在 unexplained 与非 unexplained 之间的迁移。
        out["gate_class_change_counts"] = dict(sorted(gate_changes.items()))
        out["gate_changed_block_count"] = sum(gate_changes.values())
        out["changed_to_unexplained"] = sum(
            n for k, n in gate_changes.items() if k.endswith("-> unexplained"))
        out["changed_from_unexplained"] = sum(
            n for k, n in gate_changes.items() if k.startswith("unexplained ->"))
    # 未解释字符按子类的整体迁移（不依赖历史产物是否存在）。
    subkinds: dict = {}
    for record in records:
        for subkind, chars in record["residue_subkinds"].items():
            subkinds[subkind] = subkinds.get(subkind, 0) + chars
    out["current_residue_chars_by_subkind"] = {
        k: subkinds[k] for k in sorted(subkinds)}
    out["retired_subkinds"] = [
        name for name in ("single_char", "symbol_only")
        if name not in subkinds]
    out["notes"] = [
        "P1-1：`single_char`/`symbol_only` 已删除，新增 `residue_duplicated` / "
        "`search_exhausted` 作为未解释的失败原因子类。",
        "P1-2 / TS2 最终关闭轮：只有 blank / invisible_codepoint 可以判 "
        "engine_artifact；`width_fold` 规则**已删除**，全角/半角差异无同位置证明"
        "时一律 unexplained。",
        "TS2 最终关闭轮：只有 `aligned`（达到冻结阈值 0.90 且无 unexplained）可引用。",
    ]
    return out


def _change_cause(old: dict, new: dict) -> str:
    """给一处分类变化标注成因（**只能由两次产物中的事实推出**，不猜）。

    成因词表（与 `notes` 中的说明一一对应）：
    `duplicate_assignment` / `search_exhausted`（P1-1 的失败原因子类）、
    `width_fold_rule_removed`（旧产物有 `width_fold` 残差子类、新产物没有 ——
    即本轮删除该白名单条目后的真实差异）、
    `width_fold_whitelist`（反向：新产物出现 `width_fold` 而旧产物没有，只可能
    出现在与**本轮之前**的产物比对时，用于指认"更早的产物带过这条规则"）、
    `stricter_to_unexplained`（变严）、
    `looser_from_unexplained_review_required`（变松，**必须人工复核**）、
    `dominant_class_changed`（仅描述性主类变化）。
    """
    subkinds = new.get("residue_subkinds", {})
    old_subkinds = old.get("residue_subkinds", {})
    if "residue_duplicated" in subkinds:
        return "duplicate_assignment"      # P1-1：重复消费同一源区间
    if "search_exhausted" in subkinds:
        return "search_exhausted"
    if "width_fold" not in subkinds and "width_fold" in old_subkinds:
        return "width_fold_rule_removed"   # TS2 最终关闭轮：删除该白名单条目
    if "width_fold" in subkinds and "width_fold" not in old_subkinds:
        return "width_fold_whitelist"      # 与更早（带该规则的）产物比对时的反向
    new_gate = _gate_class(new)
    old_gate = _gate_class(old)
    if new_gate == "unexplained" and old_gate != "unexplained":
        return "stricter_to_unexplained"
    if old_gate == "unexplained" and new_gate != "unexplained":
        return "looser_from_unexplained_review_required"
    return "dominant_class_changed"


def _gate_class(record: dict) -> str:
    """块的**门槛类**；旧产物没有该字段时按其 `has_unexplained` 等价重建。"""
    if "verdict_residue_class" in record:
        return record["verdict_residue_class"]
    return "unexplained" if record["has_unexplained"] else record["residue_class"]


def _align_min_md(status: dict, summary: dict, baseline: dict | None = None,
                  removed: dict | None = None) -> str:
    table = status["diagnostic_threshold_table"]
    formal = status["formal"]
    inv = formal["invariants"]
    lines = [
        "# ALIGN_MIN 冻结状态与全量三态诊断（TS2 最终关闭轮）\n",
        f"> 状态：**{status['status']}**",
        f"> `align_min = {status['align_min']}`，"
        f"`decision_source = {status['decision_source']}`",
        "> 阈值由**用户 + Codex** 于 2026-09-17 批准冻结；本执行器不推荐、不重选、",
        "> 不重开讨论，也不得自行宣布 TS2 关闭。\n",
        "## 生产判定规则（真值表，分支顺序即优先级）\n",
        f"> 判定语义直接复用 TS1 冻结的 `{status['verdict_source']}`，",
        f"> 词表 `{status['verdict_vocabulary']}`。\n",
        "| 条件 | verdict | 是否可引用 |",
        "|---|---|---|",
        "| `coverage <= 0` | `unaligned` | 否 |",
        f"| `coverage < {status['align_min']}` | `unaligned` | 否 |",
        f"| `coverage >= {status['align_min']}` **且含** `unexplained` | `partially_aligned` | **否** |",
        f"| `coverage >= {status['align_min']}` **且无** `unexplained` | `aligned` | **是（唯一）** |",
        "",
        "## 冻结阈值下的**正式**三态结果\n",
        f"- 阈值：`{formal['align_min']}`；块总数：{formal['total_blocks']}",
        f"- `aligned_blocks_at_frozen_threshold` = **{formal['aligned_blocks_at_frozen_threshold']}**",
        f"- `partial_blocks_at_frozen_threshold` = **{formal['partial_blocks_at_frozen_threshold']}**",
        f"- `unaligned_blocks_at_frozen_threshold` = **{formal['unaligned_blocks_at_frozen_threshold']}**",
        f"- `citable_blocks_now` = **{formal['citable_blocks_now']}**",
        f"- `aligned_with_unexplained` = **{formal['aligned_with_unexplained']}**（必须为 0）",
        f"- `below_threshold_but_not_unaligned` = "
        f"**{formal['below_threshold_but_not_unaligned']}**（必须为 0）",
        f"- above 面内仍含 `unexplained` 的块："
        f"{formal['above_threshold_unexplained_block_count']} 块 / "
        f"{formal['above_threshold_unexplained_char_count']} 字符"
        f"（字符占比 {formal['above_threshold_unexplained_char_ratio']:.5f}）"
        f" —— 这些块是 `partially_aligned`，**已被 fail-closed 隔离**，不阻塞 TS2。",
        "",
        "### 四条正式不变式（必须全部成立）\n",
        f"- `aligned + partial + unaligned == total_blocks` → "
        f"**{inv['three_state_conservation']}**",
        f"- `citable_blocks_now == aligned` → "
        f"**{inv['citable_blocks_now_equals_aligned']}**",
        f"- `aligned_with_unexplained == 0` → "
        f"**{inv['aligned_with_unexplained_is_zero']}**",
        f"- `below_threshold_but_not_unaligned == 0` → "
        f"**{inv['below_threshold_but_not_unaligned_is_zero']}**",
        f"- 汇总 `all_hold` = **{inv['all_hold']}**",
        "",
        "### 逐文档三态分布（冻结阈值下）\n",
        "| 文档 | 块数 | aligned | partially_aligned | unaligned | 可引用 | 三态守恒 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, states in sorted(formal["per_document"].items()):
        lines.append(
            f"| {name} | {states['total_blocks']} "
            f"| {states['aligned_blocks_at_frozen_threshold']} "
            f"| {states['partial_blocks_at_frozen_threshold']} "
            f"| {states['unaligned_blocks_at_frozen_threshold']} "
            f"| {states['citable_blocks_now']} "
            f"| {states['invariant_holds']} |")
    lines.append("")
    lines.append("## 敏感度诊断表（**非推荐、非裁决依据**）\n")
    lines.append(f"> {status['diagnostic_threshold_table_note']}")
    lines.append("> 保留此表的唯一目的是让人看到「换一个阈值会长什么样」；它不构成"
                 "候选区间，不得被引用为推荐。\n")
    lines.append("| T | aligned | 占比 | partial | 占比 | unaligned | 占比 "
                 "| above 面 unexpl. 块 | above 面 unexpl. 字符占比 "
                 "| 被排除块 | 被排除块主类 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for entry in table:
        lines.append(
            f"| {entry['align_min']:.2f} "
            f"| {entry['aligned_blocks']} "
            f"| {entry['aligned_ratio']:.4f} "
            f"| {entry['partial_blocks']} "
            f"| {entry['partial_ratio']:.4f} "
            f"| {entry['unaligned_blocks']} "
            f"| {entry['unaligned_ratio']:.4f} "
            f"| {entry['above_threshold_unexplained_block_count']} "
            f"| {entry['above_threshold_unexplained_char_ratio']:.5f} "
            f"| {entry['excluded_blocks']} "
            f"| {entry['excluded_dominant_classes']} |")
    lines.append("")
    lines.append(f"> 每个阈值下的守恒 `aligned + partial + unaligned == total`："
                 f"`{[e['invariant_holds'] for e in table]}`；"
                 f"反例不变式 `aligned_with_unexplained`（必须全为 0）："
                 f"`{[e['aligned_with_unexplained'] for e in table]}`；"
                 f"反例不变式 `below_threshold_but_not_unaligned`（必须全为 0）："
                 f"`{[e['below_threshold_but_not_unaligned'] for e in table]}`；"
                 f"互检 `partial == above 面带 unexplained 的块数`："
                 f"`{[e['partial_blocks_equals_above_with_unexplained'] for e in table]}`；"
                 f"互检 `aligned == above 面不带 unexplained 的块数`："
                 f"`{[e['aligned_blocks_equals_above_without_unexplained'] for e in table]}`。")
    lines.append("")
    if removed:
        lines.append("## `width_fold` 规则删除前后的真实分布变化\n")
        if not removed.get("comparable"):
            lines.append(f"- {removed['note']}")
        else:
            lines.append(
                f"- 比对基线（**规则生效时**最近一份产物，不是本轮中间产物）："
                f"`{removed['previous_run_dir']}`；其中出现 "
                f"`width_fold` 残差子类的块 **"
                f"{removed['previous_blocks_with_width_fold_subkind']}** 个、"
                f"共 **{removed['previous_width_fold_chars']}** 字符。")
            lines.append(
                f"- 本轮删除该规则后归入 `unexplained` 的块：**"
                f"{removed['blocks_moved_to_unexplained']}** 个、**"
                f"{removed['chars_moved_to_unexplained']}** 字符"
                f"（口径：{removed['method']}）。")
            lines.append(
                f"- 归入 `column_reorder` 的块：**"
                f"{removed['blocks_moved_to_column_reorder']}** 个、**"
                f"{removed['chars_moved_to_column_reorder']}** 字符。")
            lines.append(
                f"- 其余（既未进 `unexplained` 也未增加 `column_reorder`）：**"
                f"{removed['blocks_otherwise_unchanged']}** 个块。")
            lines.append("")
            lines.append("### 受影响块的**真实终态**（逐块从本轮 records 重算）\n")
            lines.append(
                f"- `affected_block_count = {removed['affected_block_count']}`")
            lines.append(
                f"- `affected_verdict_counts = "
                f"{json.dumps(removed['affected_verdict_counts'], ensure_ascii=False)}`"
                f"（合计 {sum(removed['affected_verdict_counts'].values())}）")
            lines.append(
                f"- `affected_citable_count = {removed['affected_citable_count']}`；"
                f"`affected_noncitable_count = {removed['affected_noncitable_count']}`")
            lines.append(
                f"- `affected_has_unexplained_count = "
                f"{removed['affected_has_unexplained_count']}`；"
                f"`affected_below_threshold_count = "
                f"{removed['affected_below_threshold_count']}`")
            lines.append(
                f"- 守恒与反向不变式："
                f"`{json.dumps(removed['affected_invariants'], ensure_ascii=False)}`")
            lines.append("")
            lines.append("| 终态 verdict | 块数 | 可引用 |")
            lines.append("|---|---|---|")
            for verdict in S.ALIGNMENT_VERDICTS:
                count = removed["affected_verdict_counts"].get(verdict, 0)
                lines.append(f"| `{verdict}` | {count} "
                             f"| {'是' if verdict == 'aligned' else '否'} |")
            lines.append("")
            lines.append(
                "受影响块中可引用的块数为 "
                f"**{removed['affected_citable_count']}**，不可引用为 "
                f"**{removed['affected_noncitable_count']}** —— 引用资格由每个块自己的 "
                "`coverage` / `unexplained` / `verdict` 决定；"
                "**必须逐块读取上表**，不得对受影响块作任何整体化结论。")
            lines.append("")
            lines.append("> 删除 `width_fold` 后，相关残差只有在原始未折叠字符能够绑定到"
                         "同页真实、两两不重叠的 occurrence 时，才可归为 "
                         "`column_reorder`。该证明允许列序不同，不等于同一逻辑位置。\n"
                         ">\n"
                         "> `column_reorder` 是“残差已被结构性解释”的类别，"
                         "但不会单独决定引用资格。块只有在 `coverage >= "
                         f"{V.ALIGN_MIN}`、且不存在 `unexplained`、"
                         "最终 `verdict == \"aligned\"` 时才可引用。")
            lines.append("")
            lines.append("逐块行（文档 / 页 / 块 / Evidence id / coverage / verdict / "
                         "是否可引用 / 是否含未解释 / 归因桶）见 `width_fold_removal.json` "
                         "的 `affected_blocks`，与 `coverage_blocks.json` 同 Evidence id "
                         "可逐条对账。")
            if removed["typical_positions"]:
                lines.append("")
                lines.append("典型位置（按归入 `unexplained` 的字符数降序，最多 20 条）：\n")
                lines.append("| 文档 | 页 | 块 | Evidence id | coverage "
                             "| 旧 width_fold 字符 | 归入 unexpl. 字符 "
                             "| 当前未解释字符 | 旧类别 | 新类别 |")
                lines.append("|---|---|---|---|---|---|---|---|---|---|")
                for item in removed["typical_positions"]:
                    lines.append(
                        f"| {item['document_id']} | {item['page_number']} "
                        f"| {item['block_index']} | {item['evidence_id'][:12]} "
                        f"| {item['coverage']} "
                        f"| {item['previous_width_fold_chars']} "
                        f"| {item['chars_moved_to_unexplained']} "
                        f"| {item['unexplained_chars_now']} "
                        f"| `{json.dumps(item['old_classes'], ensure_ascii=False)}` "
                        f"| `{json.dumps(item['new_classes'], ensure_ascii=False)}` |")
            else:
                lines.append("")
                lines.append(
                    "- **本轮没有任何块从 `width_fold` 归入 `unexplained`**"
                    "（典型位置列表为空，不是被省略）；受影响块的逐块终态见上表与 "
                    "`width_fold_removal.json` 的 `affected_blocks`。")
        lines.append("")
    overall = summary["overall"]
    lines.append("## 总体分布\n")
    lines.append(f"- 块总数：{overall['block_count']}")
    lines.append(f"- 覆盖率分布：`{json.dumps(overall['distribution'], ensure_ascii=False)}`")
    lines.append(f"- <0.90 的块：{overall['below_0_90']['count']} "
                 f"（{overall['below_0_90']['ratio']:.4f}），"
                 f"其中仍有 unexplained 的："
                 f"{overall['below_0_90']['with_unexplained_residue']}")
    lines.append(f"- 残差字符按类别："
                 f"`{json.dumps(overall['residue_chars_by_class'], ensure_ascii=False)}`")
    lines.append(f"- 未解释字符按失败原因：重复消费/非重叠映射失败 "
                 f"{overall['unexplained']['duplicate_assignment_chars']} 字符，"
                 f"搜索预算耗尽 "
                 f"{overall['unexplained']['search_exhausted_chars']} 字符，"
                 f"其中不可定位 {overall['unexplained']['unlocatable_chars']} 字符")
    lines.append("")
    lines.append("## 最差样本（不泄露整段原文）\n")
    for item in overall["worst"][:10]:
        line = (f"- cov={item['coverage']} p{item['page_number']} "
                f"b{item['block_index']} 主类={item['residue_class']} "
                f"门槛类={item['verdict_residue_class']} "
                f"未解释字符={item['unexplained_chars']} "
                f"{item['samples'][:2]}")
        recheck = item.get("recheck_line") or {}
        if recheck.get("page_number") is not None:
            line += (f" ｜覆盖落点：p{recheck.get('page_number')} "
                     f"line={recheck.get('line_index')} "
                     f"bbox={recheck.get('line_bbox')} "
                     f"head={recheck.get('line_text_head')!r}")
        lines.append(line)
    lines.append("")
    lines.append("## 未解释字符最多的块（覆盖落点 + 简短归因）\n")
    lines.append("> 位置来自 `PageLayout` 的真实版式行（物理页号 / `line_index` / "
                 "`bbox`），可回原文复核；样本只截取前 40 字符，不泄露整段原文。")
    lines.append("> **口径说明**：`recheck_line` 是块**已覆盖部分**所落的真实版式行"
                 "（沿用 TS2 的 `_recheck`）。未解释字符**本身**的精确原文位置在版式层"
                 "无法唯一确定 —— 这正是它被判 unexplained 的原因 —— 因此不得把该"
                 "落点读作「缺字就在这一行」，只能回到该页原文复核。")
    for item in overall["most_unexplained"][:10]:
        recheck = item.get("recheck_line") or {}
        lines.append(
            f"- {item['evidence_id'][:12]} p{item['page_number']} "
            f"b{item['block_index']} 未解释 "
            f"{item['unexplained_chars']}/{item['block_len']} 字符，cov="
            f"{item['coverage']}，门槛类={item['verdict_residue_class']}，"
            f"子类={json.dumps(item['subkinds'], ensure_ascii=False)}，"
            f"真实来源 p{recheck.get('page_number')} "
            f"line={recheck.get('line_index')} "
            f"bbox={recheck.get('line_bbox')} "
            f"head={recheck.get('line_text_head')!r}，"
            f"归因={item['reasons'][:1]}")
    lines.append("")
    lines.append("## 修复轮（TS2.1）的三个 P1 对结果的影响\n")
    lines.append("- **P1-1 非重叠 occurrence 分配**：不再允许两个片段绑定同一个源"
                 "区间。重复文本只有在页面里**确实出现足够多次**时才能被全部解释，"
                 "否则整段进 unexplained（子类 `residue_duplicated`）。")
    lines.append("- **P1-2 engine artifact 封闭白名单**："
                 "`single_char`（长度 ≤1 即 artifact）与 `symbol_only`（全是标点即 "
                 "artifact）两条宽泛规则**已删除**。现在只有 "
                 "`blank` / `invisible_codepoint` 两条、各有版本化依据的规则可以判 "
                 "artifact；缺一个汉字/数字/字母、未知符号差异一律 unexplained。")
    lines.append("- **TS2 最终关闭轮：删除 `width_fold` 白名单条目**。旧实现把残差 "
                 "`fold()` 之后在**本页任意位置**搜一次，命中即判 engine artifact ——"
                 "那是事后、全页任意搜索式消除残差：残差 `Ａ` 只要本页别处出现过 "
                 "`A`、残差 `１２３４` 只要别处出现过 `1234` 就会被「解释」掉。"
                 "本轮裁决为**完全禁止**，且**不得**用「唯一出现/最短长度/概率条件」"
                 "之类的附加条件把它换个写法留下来。无法在**同一位置**证成全角↔半角"
                 "对应的差异，一律 unexplained。")
    lines.append("- **P1-3 三态正交**：不再把全部 `coverage >= T` 的块统称可引用；"
                 "只有 `aligned`（达到冻结阈值且无 unexplained）计入可引用面，"
                 "含未解释残差的 `partially_aligned` 即使覆盖率很高也不算。")
    masking = overall["dominant_class_masks_unexplained"]
    lines.append(
        f"- **P1-3 实施期发现的缺陷（本执行器自曝）**：块级描述性主类 "
        f"`residue_class` 按字符数取，会被更长的温和类掩盖 —— 三份文档里有 "
        f"**{masking['blocks']}/{overall['block_count']}** 个块含未解释残差但主类"
        f"不是 `unexplained`。若直接用主类喂 `compute_alignment_verdict`，这些块"
        f"在 `coverage >= T` 时会被判 `aligned`，正是业务规则禁止的"
        f"「把含未解释残差的块升级为可引用」。已改为使用**门槛类** "
        f"`verdict_residue_class`（含任何 unexplained 即为 unexplained），并新增"
        f"反例不变式 `aligned_with_unexplained == 0`。"
        f"更早的 TS2 产物（含 `tree_structure_ts2_layout_ts2_1_*`）候选表"
        f"**带有该缺陷**，不得据其判断可引用面。")
    lines.append("")
    if baseline and baseline.get("comparable"):
        lines.append("## 修复前后分类对比（与最早的 TS2 基线逐块比对）\n")
        lines.append(f"- 基线产物：`{baseline['previous_run_dir']}`"
                     f"（{baseline['previous_block_count']} 块）"
                     f"→ 本轮（{baseline['current_block_count']} 块），"
                     f"共有 {baseline['shared_blocks']} 块可比，"
                     f"基线独有 {len(baseline['previous_only_blocks'])}，"
                     f"本轮独有 {len(baseline['current_only_blocks'])}。")
        lines.append(f"- 描述性主类变化："
                     f"`{json.dumps(baseline['class_change_counts'], ensure_ascii=False)}`"
                     f"，合计 {baseline['changed_block_count']} 块。")
        lines.append(f"- **判定相关**（门槛类在 unexplained 与非 unexplained 之间"
                     f"迁移）：`{json.dumps(baseline['gate_class_change_counts'], ensure_ascii=False)}`"
                     f"，合计 {baseline['gate_changed_block_count']} 块；"
                     f"其中变严（→ unexplained）{baseline['changed_to_unexplained']} 块，"
                     f"变松（unexplained → 其他）{baseline['changed_from_unexplained']} 块"
                     f"（变松者必须人工复核）。")
        by_cause: dict = {}
        for item in baseline.get("changed_blocks", []):
            by_cause[item["cause"]] = by_cause.get(item["cause"], 0) + 1
        lines.append(f"- 成因分布（只统计前 100 条明细）："
                     f"`{json.dumps(dict(sorted(by_cause.items())), ensure_ascii=False)}`")
        lines.append(f"- 已退役的子类：`{baseline['retired_subkinds']}`")
    lines.append("")
    lines.append("## 不可引用面的主要类型与对 TS3 的影响\n")
    lines.append("- 判断 `coverage < ALIGN_MIN` 时**不得**读作「内容缺失」：需要先看该块"
                 "残差的主类。`column_reorder` 表示块文本在页面上**存在**，只是单元格/"
                 "行的拼装顺序与版式行不同 —— 它同时标记了「逐字符连续匹配」这一度量"
                 "在表格页上本身失效。")
    lines.append("- 本轮**不新增任何 append-only Evidence Set**：只如实标识出需要后续"
                 "处理的块，不在本轮把它们并入任何可引用集合。")
    lines.append("- 表格重排不靠阈值承接：`ALIGN_MIN` 已冻结为**单一**文本对齐阈值，"
                 "**不设**正文页 / 表格页两套阈值；表格结构由未来的 `TableObject` "
                 "路径承接。对齐器必须对表格页采用按行/按单元格的匹配，而不能只靠提高"
                 "或调低 `ALIGN_MIN` 来筛。")
    lines.append("")
    lines.append("## 全角/半角支持的**前置条件**（不得回退为全页搜索）\n")
    lines.append("将来的正式 aligner 若支持全角/半角，必须满足：")
    lines.append("1. 在**两侧同步**施加**等长**变换（不是只变换残差一侧）；")
    lines.append("2. 变换由**精确字符映射表**驱动并**版本化**（纳入 "
                 "`VERSION_CONSTANTS`，改变 alignment 身份）；")
    lines.append("3. 对应关系必须在**同一位置**可核实地成立。")
    lines.append("**全页任意命中不得回来**：`normalization.fold()` 本轮仅作为确定性"
                 "原语保留，**不得**用于事后消除残差。")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

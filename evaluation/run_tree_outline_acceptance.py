"""TS3 真实文档标题树验收（**评测专用**，只读）。

用途（TS3 任务书 §六）：

1. 对三份真实电子 PDF（`NDSD_KCZ_2026` / `NDSD_2025_year` / `NDSD_2024_year`）
   与一个**非 300750 的独立 fixture**分别构建 `PageLayout` → `DocumentOutline`，
   落盘人工可复核的审计产物。
2. 与 TS2 冻结基线对**全部存量 EvidenceBlock** 逐块对账（交付 A 的等价性证明）。
   对账范围严格限定为**冻结产物真正存有**的字段；TS3 才引入的正式记录字段
   （`alignment_id` / `char_map_segments` / `record_refused` / `record_refusal_reason`）
   在冻结产物里根本不存在，不参与"有值 对 缺键"式差异，改由同一份重算的**自洽
   核验**证明（判定不得自报、char_map 与残差互补、量化边界拒发的算术真的成立）。

硬约束：

- **只读**。Evidence DB 只经 `evidence.store._open_readonly_conn()`（URI
  `?mode=ro` + `PRAGMA query_only=ON`）打开；不调用 `init_db()`、不做 migration、
  不 UPDATE / INSERT / DELETE，不改任何 DB 路径。运行前后记录受监管文件的
  sha256 / size / mtime，必须不变。
- **不抽样、不挑页**。三份 PDF 全页构建；不排除表格页、短页或难解析页。
- 构建算法只有一份生产实现 `document_structure.outline_builder`（交付 B）：来源
  上下文读取、标题候选扫描、假阳性守卫、层级装配、边生成、人工复核视图全在生产
  核心内。本脚本只做**只读取数、逐项复核、落盘审计**；生产代码不得反向 import 本
  脚本。对齐口径复用 TS2 评测侧的只读取数（`run_tree_layout_acceptance`），
  对齐算法本身仍来自唯一生产核心 `document_structure.aligner`（交付 A）。
- 真实产物一律标注 `manual_review_required`：**构建完成不等于标题树通过**，
  TS3 的关闭不由本脚本或执行者宣布。
- 不写 `ALIGN_MIN`，不改任何生产常量；不上 LLM、不联网、不调用 Router / Contract。

用法：

    python -m evaluation.run_tree_outline_acceptance --run-id ts3_outline_<新id>

不传 `--run-id` 时用 UTC 时间戳生成新目录；**绝不覆盖**任何历史结果目录。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # 允许 `python evaluation/run_tree_outline_acceptance.py`
    sys.path.insert(0, str(REPO_ROOT))

import fitz                                                       # noqa: E402

from document_structure import aligner as A                       # noqa: E402
from document_structure import layout_builder as LB               # noqa: E402
from document_structure import outline_builder as OB              # noqa: E402
from document_structure import schema as S                        # noqa: E402
from document_structure import versions as V                      # noqa: E402
from document_structure.canonical import canonical_json            # noqa: E402
from document_structure.normalization import tight                # noqa: E402
from evaluation import run_tree_layout_acceptance as RA           # noqa: E402

SAMPLE_DIR = REPO_ROOT / "data" / "samples" / "300750" / "announcements"
EVIDENCE_DB = REPO_ROOT / "data" / "evidence.db"
FINANCIAL_DB = REPO_ROOT / "data" / "financial_v2.db"
RESULTS_ROOT = REPO_ROOT / "evaluation" / "results"

#: 非 300750 的独立 fixture：**合成**公司代码与文档号，正文与标题全部自造。
#: 它不是任何真实主体，生产模块里也**没有**任何针对它的规则。
FIXTURE_COMPANY_ID = "555555"
FIXTURE_DOCUMENT_ID = "FIXTURE_BOND_2026"
FIXTURE_FILE_NAME = "fixture_non_300750_bond.pdf"

#: 三份真实文档（company_id, document_id, 文件名）。
REAL_DOCUMENTS = (
    ("300750", "NDSD_2024_year", "NDSD_2024_year.pdf"),
    ("300750", "NDSD_2025_year", "NDSD_2025_year.pdf"),
    ("300750", "NDSD_KCZ_2026", "NDSD_KCZ_2026.pdf"),
)

# 唯一生产实现（交付 A / 交付 B）的**转发**，不是副本：本评测侧与生产核心必须
# 是同一对象（`evals/test_tree_page_layout.py` 的 I9d 与
# `evals/test_tree_aligner.py` 的同一性检查会立刻发现副本）。
load_source_context = OB.load_source_context
scan_heading_candidates = OB.scan_heading_candidates
build_document_outline = OB.build_document_outline
outline_markdown = OB.outline_markdown
unassigned_markdown = OB.unassigned_markdown
collect_toc_entries = OB.collect_toc_entries
OUTLINE_BUILDER_VERSION = OB.OUTLINE_BUILDER_VERSION
align_block = A.align_block
EvidenceBlockInput = A.EvidenceBlockInput


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(paths) -> dict:
    """记录受监管文件的 sha256 / size / mtime（缺失即显式记录，不用 None 假装一致）。"""
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


def immutability_report(before: dict, after: dict) -> dict:
    changed = sorted(k for k in before
                     if before.get(k) != after.get(k))
    watched_missing = sorted(k for k, v in before.items() if v.get("missing"))
    return {"unchanged": not changed and not watched_missing,
            "changed": changed, "watched_missing": watched_missing}


# ---------------------------------------------------------------------------
# §十 真实产物的 artifact hash index（逐文件 SHA256 + 真实 UTC 时间 + 输入身份）
# ---------------------------------------------------------------------------

#: 参与**代码指纹**的生产 / 评测源文件（相对仓库根）。指纹是这些文件内容的
#: `(路径, sha256)` 规范序列的 sha256，因此"改了哪一版代码"在产物里可复核。
CODE_FINGERPRINT_FILES: tuple = (
    "document_structure/aligner.py",
    "document_structure/outline_builder.py",
    "document_structure/schema.py",
    "document_structure/versions.py",
    "document_structure/layout_builder.py",
    "evaluation/run_tree_outline_acceptance.py",
    "evaluation/run_tree_layout_acceptance.py",
    # 依赖清单：依赖版本变化同样应改变指纹（缺失时在 `missing` 里如实记录）。
    "requirements.txt",
)


def code_fingerprint() -> dict:
    """TS3 生产 / 评测代码的**内容指纹**（不是版本号自报，是逐文件内容哈希）。"""
    entries = []
    for rel in CODE_FINGERPRINT_FILES:
        path = REPO_ROOT / rel
        entries.append((rel, sha256_file(path) if path.is_file() else None))
    return {
        "files": dict(entries),
        "fingerprint": hashlib.sha256(canonical_json(
            [list(e) for e in entries]).encode("utf-8")).hexdigest(),
        "missing": [rel for rel, digest in entries if digest is None],
    }


def version_manifest() -> dict:
    """正式版本常量（全部经 `versions.py` 这一唯一来源读取，不写第二份字面量）。"""
    return {
        "outline_algorithm_version": V.OUTLINE_ALGORITHM_VERSION,
        "outline_schema_version": V.OUTLINE_SCHEMA_VERSION,
        "outline_builder_version": OUTLINE_BUILDER_VERSION,
        "aligner_version": V.ALIGNER_VERSION,
        "align_schema_version": V.ALIGN_SCHEMA_VERSION,
        "align_refusal_schema_version": V.ALIGN_REFUSAL_SCHEMA_VERSION,
        "alignment_partition_validator_version":
            V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        "evidence_block_input_version": V.EVIDENCE_BLOCK_INPUT_VERSION,
        "evidence_set_snapshot_version": V.EVIDENCE_SET_SNAPSHOT_VERSION,
        "evidence_set_gateway_version": V.EVIDENCE_SET_GATEWAY_VERSION,
        "normalization_version": V.NORMALIZATION_VERSION,
        "align_min": V.ALIGN_MIN,
        # §四：标题资格 profile 与表格区域资格都升过版 —— 旧载荷（legacy 清单里的
        # `hq-2` / `hq-3`）**不得**静默按新语义解释，因此当前版本号与 legacy 清单一起
        # 进 manifest。（`trg-3` 本轮不变：表格区域资格语义未动。）
        "heading_qualification_profile_version":
            V.HEADING_QUALIFICATION_PROFILE_VERSION,
        "table_region_qualification_version":
            V.TABLE_REGION_QUALIFICATION_VERSION,
        "legacy_heading_qualification_profile_versions":
            list(V.legacy_versions("HEADING_QUALIFICATION_PROFILE_VERSION")),
        "legacy_table_region_qualification_versions":
            list(V.legacy_versions("TABLE_REGION_QUALIFICATION_VERSION")),
        "legacy_aligner_versions": list(V.legacy_versions("ALIGNER_VERSION")),
        "legacy_align_schema_versions":
            list(V.legacy_versions("ALIGN_SCHEMA_VERSION")),
        "legacy_align_refusal_schema_versions":
            list(V.legacy_versions("ALIGN_REFUSAL_SCHEMA_VERSION")),
        "legacy_outline_algorithm_versions":
            list(V.legacy_versions("OUTLINE_ALGORITHM_VERSION")),
        "classification": {
            name: V.classify_schema_version(name, value)
            for name, value in (
                ("ALIGNER_VERSION", V.ALIGNER_VERSION),
                ("ALIGN_SCHEMA_VERSION", V.ALIGN_SCHEMA_VERSION),
                ("ALIGN_REFUSAL_SCHEMA_VERSION", V.ALIGN_REFUSAL_SCHEMA_VERSION),
                ("OUTLINE_ALGORITHM_VERSION", V.OUTLINE_ALGORITHM_VERSION),
                ("HEADING_QUALIFICATION_PROFILE_VERSION",
                 V.HEADING_QUALIFICATION_PROFILE_VERSION),
                ("TABLE_REGION_QUALIFICATION_VERSION",
                 V.TABLE_REGION_QUALIFICATION_VERSION),
            )},
    }


def run_id_identity(run_id: str, *, source: str = "generated_utc") -> dict:
    """`run_id` 的时间语义（§十）：带 `Z` 的时间语义**只能**来自真实 UTC。

    §十 的原文是"`run_id` 若带 `Z`，必须来自真实 UTC；否则把 `run_id` 明确视为不带
    时间语义的 opaque ID"。只按后缀 `Z` 判断是不够的：调用方可以用 `--run-id
    rework8_20260917T250000Z` 手写一个**看起来像时间戳**的标签，而那一刻的真实 UTC
    可能是 11:04 —— 于是产物里写着"有实际时间语义"，时间语义却是编出来的。因此这里
    看**来源**而不是看后缀：

    - `source="generated_utc"`（`main()` 自己按真实 UTC 打的时间戳）：带时间语义；
    - `source="caller_supplied"`（显式 `--run-id`）：一律 opaque，**即使后缀是 Z**，
      因为它无从校验；`looks_like_timestamp` 只如实记录"它长得像"，不当作语义。
    """
    looks_like_timestamp = bool(run_id) and run_id.endswith("Z")
    generated = source == "generated_utc"
    if generated:
        semantics = "按真实 UTC 时间戳生成（本 run 的时间语义来自生成时刻）"
    elif looks_like_timestamp:
        semantics = ("opaque ID：由调用方显式提供，后缀 Z 只是**看起来**像时间戳，"
                     "无从校验，不得据它推断生成时刻（生成时刻见 generated_at_utc）")
    else:
        semantics = "opaque ID，不带时间语义（不得据它推断生成时刻）"
    return {
        "run_id": run_id,
        "source": source,
        "looks_like_timestamp": looks_like_timestamp,
        "carries_utc_semantics": generated,
        "semantics": semantics,
    }


def artifact_hash_index(run_dir: pathlib.Path, *, run_id: str, generated_at: str,
                        versions: dict, code: dict, inputs: dict,
                        database: dict, run_id_source: str = "generated_utc") -> dict:
    """真实产物的**逐文件 hash index**（§十）：目录里每个文件一条。

    `generated_at` 必须是**真实 UTC** 时刻（由调用方在写盘时取一次，不在本函数内
    取，以免"索引里的时间"与"索引外的时间"分叉）。索引**不含自身**的哈希：它自己
    就是清单；对它的篡改由 `verify_artifact_index` 按逐文件重算发现。
    """
    files = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        st = path.stat()
        files.append({
            "path": path.relative_to(run_dir).as_posix(),
            "size": st.st_size,
            "sha256": sha256_file(path),
        })
    return {
        "manual_review_required": True,
        "artifact": "artifact_index.json",
        "scope": ("本 run 目录内每个文件的 SHA256 / 大小；索引不含自身，"
                  "篡改由逐文件重算发现"),
        "run_id": run_id,
        "run_id_identity": run_id_identity(run_id, source=run_id_source),
        "generated_at_utc": generated_at,
        "versions": versions,
        "code_fingerprint": code,
        "inputs": inputs,
        "database": database,
        "file_count": len(files),
        "files": files,
        "index_identity": hashlib.sha256(canonical_json({
            "run_id": run_id, "generated_at_utc": generated_at,
            "versions": versions, "code_fingerprint": code,
            "inputs": inputs, "files": files,
        }).encode("utf-8")).hexdigest(),
    }


#: 索引自身的产物：它们**不**进索引（索引无法含自身哈希），核验时也不当作"未登记
#: 的多余文件"。除这两份之外的任何文件都必须被索引登记。
_INDEX_INTERNAL_FILES: tuple = ("artifact_index.json",
                                "artifact_index_check.json")


def verify_artifact_index(run_dir: pathlib.Path, index: dict, *,
                          expected_versions: dict | None = None,
                          expected_inputs: dict | None = None) -> dict:
    """按索引**逐文件重算**核验（§十 的反例面）：缺文件 / 多文件 / 错 hash / 错版本。

    返回 `problems`（空列表 = 通过）。这是**只读**核验，不改任何文件。
    """
    problems: list = []
    listed = {entry["path"]: entry for entry in index.get("files", [])}
    actual = set()
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir).as_posix()
        actual.add(rel)
        if rel in _INDEX_INTERNAL_FILES:
            continue
        entry = listed.get(rel)
        if entry is None:
            problems.append(f"索引未登记的文件：{rel}")
            continue
        if entry.get("size") != path.stat().st_size:
            problems.append(
                f"{rel} 大小不符：索引 {entry.get('size')} 实得 {path.stat().st_size}")
        digest = sha256_file(path)
        if entry.get("sha256") != digest:
            problems.append(
                f"{rel} SHA256 不符：索引 {entry.get('sha256')} 实得 {digest}")
    for rel in sorted(set(listed) - actual):
        problems.append(f"索引登记但文件缺失：{rel}")
    if expected_versions is not None:
        for name, value in expected_versions.items():
            if index.get("versions", {}).get(name) != value:
                problems.append(
                    f"版本不符：{name} 索引 "
                    f"{index.get('versions', {}).get(name)!r} 期望 {value!r}")
    if expected_inputs is not None:
        if canonical_json(index.get("inputs")) != canonical_json(expected_inputs):
            problems.append("输入身份不符：索引里的 inputs 与本次输入不一致")
    stamp = index.get("generated_at_utc")
    if not isinstance(stamp, str) or "T" not in stamp or "Z" not in stamp:
        problems.append(
            f"generated_at_utc 必须为真实 UTC 的 ISO 形式（含 T 与 Z），得到 {stamp!r}")
    return {"ok": not problems, "problems": problems,
            "file_count": len(actual),
            "index_file_count": len(listed)}


# ---------------------------------------------------------------------------
# 非 300750 独立 fixture
# ---------------------------------------------------------------------------

#: fixture 正文行（自造，无任何真实主体信息）。
_FIXTURE_BODY = (
    "本期债券募集资金已按约定用途使用，未发生变更。",
    "报告期内发行人主营业务稳定，不存在重大不利变化。",
    "本期债券付息兑付正常，未发生违约或延迟支付情形。",
)

#: 目录项的**结构**形态：标题 + 点线填充 + 尾部页标签（与真实样本同形）。
_TOC_LEADER = " .........."


def _fixture_body(y0: float = 250.0, step: float = 22.0, tag: str = "") -> list:
    return [(72.0, y0 + i * step, text + tag, 11.0, False)
            for i, text in enumerate(_FIXTURE_BODY)]


def _fixture_pages() -> list:
    """9 页 fixture：封面无页码，第 2..9 页印 1..8（偏移 1 自洽）。

    刻意包含：目录页（3 条目录项，其中 1 条无真实正文锚点）、页中三级/四级/五级
    小标题、年份行 / 金额行 / 表格区域 / 页眉 / 页码 / 普通短句等假阳性诱饵、
    以及一条正文引用标记（只登记 `pending_ts4`）。
    """
    pages = [
        # p1 封面（无页码）
        ([(120.0, 120.0, "示例发行人 2026 年公司债券年度报告", 20.0, False)]
         + _fixture_body(200.0, tag="C1"), None),
        # p2 目录（印 1）：两条能对应真实正文，一条找不到锚点
        ([(72.0, 72.0, "目录", 16.0, False),
          (72.0, 100.0, "第一节 经营情况" + _TOC_LEADER + " 2", 11.0, False),
          (72.0, 122.0, "第二节 财务信息" + _TOC_LEADER + " 4", 11.0, False),
          (72.0, 144.0, "第九节 不存在的章节" + _TOC_LEADER + " 3", 11.0, False)]
         + _fixture_body(180.0, tag="T"), "1"),
        # p3 正文（印 2）：1–5 级编号标题 + 页中三四五级小标题
        ([(72.0, 72.0, "第一节 经营情况", 16.0, False),
          (72.0, 100.0, "一、主营业务", 14.0, False),
          (72.0, 128.0, "（一）业务构成", 12.5, False),
          (72.0, 156.0, "1、主要产品", 12.0, False),
          (72.0, 184.0, "（1）产品甲", 11.5, False)]
         + _fixture_body(215.0, tag="A")
         + [(72.0, 320.0, "（2）产品乙", 11.5, False),
            (72.0, 348.0, "2、主要客户", 12.0, False)]
         + _fixture_body(378.0, tag="A2"), "2"),
        # p4 正文（印 3）：假阳性诱饵（年份 / 金额 / 表格区域 / 普通短句）
        (_fixture_body(150.0, tag="B")
         + [(300.0, 300.0, "2025年", 11.0, False),
            (72.0, 340.0, "1,234.56", 11.0, False),
            (72.0, 400.0, "项目甲", 11.0, False),
            (280.0, 400.0, "1,234.56", 11.0, False),
            (500.0, 400.0, "12.34%", 11.0, False),
            (72.0, 420.0, "项目乙", 11.0, False),
            (280.0, 420.0, "2,345.67", 11.0, False),
            (500.0, 420.0, "23.45%", 11.0, False),
            (72.0, 440.0, "项目丙", 11.0, False),
            (280.0, 440.0, "3,456.78", 11.0, False),
            (500.0, 440.0, "34.56%", 11.0, False),
            (72.0, 500.0, "本期债券付息兑付正常，未发生违约情形。", 11.0, False)],
         "3"),
        # p5 正文（印 4）：第二节 + 两个下级标题
        ([(72.0, 72.0, "第二节 财务信息", 16.0, False),
          (72.0, 100.0, "一、主要会计数据", 14.0, False),
          (72.0, 128.0, "（一）资产负债情况", 12.5, False)]
         + _fixture_body(160.0, tag="C"), "4"),
        # p6 正文（印 5）：同名标题出现在**不同父路径**下（不得合并）
        ([(72.0, 72.0, "第三节 其他事项", 16.0, False),
          (72.0, 100.0, "一、主要业务", 14.0, False),
          (72.0, 128.0, "（一）业务构成", 12.5, False)]
         + _fixture_body(160.0, tag="D"), "5"),
        # p7 正文（印 6）：页中标题（不在页首）
        (_fixture_body(120.0, tag="E")
         + [(72.0, 330.0, "（一）成本分析", 12.5, False)]
         + _fixture_body(360.0, tag="E2"), "6"),
        # p8 正文（印 7）：引用标记（只登记 pending_ts4）
        (_fixture_body(150.0, tag="F")
         + [(72.0, 320.0, "相关明细详见第三节。", 11.0, False)]
         + _fixture_body(350.0, tag="F2"), "7"),
        # p9 正文（印 8）
        (_fixture_body(150.0, tag="G"), "8"),
    ]
    out = []
    for index, (items, label) in enumerate(pages):
        out.append((items, None if index == 0 else label))
    return out


def write_fixture_pdf(path: pathlib.Path) -> pathlib.Path:
    """生成**确定性**的非 300750 fixture PDF（同一输入两次生成逐字节一致）。"""
    doc = fitz.open()
    for items, label in _fixture_pages():
        page = doc.new_page(width=595.0, height=842.0)
        # 页眉（家具）+ 正文 + 页脚页码（家具）
        page.insert_text((300.0, 40.0), "FIXTURE BOND REPORT", fontsize=8,
                         fontname="china-s")
        for x, y, text, size, bold in items:
            page.insert_text((x, y), text, fontsize=size,
                             fontname="hebo" if bold else "china-s")
        if label is not None:
            page.insert_text((520.0, 800.0), label, fontsize=9, fontname="china-s")
        page.insert_text((72.0, 815.0), "FIXTURE FOOTER", fontsize=8,
                         fontname="china-s")
    # 书签（S2）：一条能对上真实正文标题；一条找不到任何正文锚点；一条层级 2。
    doc.set_toc([[1, "第一节 经营情况", 3],
                 [2, "一、主营业务", 3],
                 [1, "第九节 不存在的章节", 9]])
    doc.set_metadata({
        "title": "FIXTURE BOND REPORT", "author": "FIXTURE",
        "subject": "TS3 non-300750 fixture", "keywords": "fixture",
        "creator": "ts3-fixture", "producer": "ts3-fixture",
        "creationDate": "D:20260101000000Z", "modDate": "D:20260101000000Z",
    })
    # `no_new_id=True`：不让引擎每次生成新的 trailer `/ID`，否则同一份 fixture 两次
    # 生成会得到不同 sha256，"同一输入"就不再是同一输入（document_version 由文件
    # 哈希派生）。固定元数据 + 固定 /ID 之后，fixture 逐字节可复现。
    doc.save(str(path), no_new_id=True)
    doc.close()
    return path


# ---------------------------------------------------------------------------
# 逐文档构建 + 审计产物
# ---------------------------------------------------------------------------

def line_assignment_audit(result) -> dict:
    """**内部记账**视图：每个非家具行恰好落在一个构建器内部桶里。

    这份 sidecar **不是** §六 的正式四态（`line_structure_assignment.json` 才是）：
    正式四态由 `heading_node / formal_unassigned / body_under_node / non_content`
    构成，而这里的 `accepted_heading / ordinary_content / toc_candidate /
    bookmark_candidate / unassigned_ambiguous` 只是构建过程的分步记账。两者**都**必须
    守恒，但不得互相顶替。
    """
    counts = result.line_assignment_counts()
    return {
        "manual_review_required": True,
        "formal_four_states": False,
        "artifact": "line_assignment_buckets.json",
        "scope": ("构建器内部记账桶；**不是** §六 正式四态。正式四态见 "
                  "line_structure_assignment.json"),
        "buckets": list(counts),
        "counts": counts,
        "total_lines": len(result.line_assignments),
        "non_furniture_lines": result.stats["non_furniture_lines"],
        "conserved": len(result.line_assignments)
        == result.stats["non_furniture_lines"],
        "items": [item.to_dict() for item in result.line_assignments],
    }


def unassigned_content(result) -> dict:
    """未被采纳的候选 + 显式未归属行 + 待审项（逐项可追溯，不得只写在叙述里）。"""
    unaccepted = [c.to_dict() for c in result.candidates if not c.accepted]
    ambiguous = [item.to_dict() for item in result.line_assignments
                 if item.assignment == "unassigned_ambiguous"]
    by_reason: dict = {}
    for item in unaccepted:
        for code in item["reason_codes"]:
            by_reason.setdefault(code, []).append(
                {"kind": item["kind"], "page_number": item["page_number"],
                 "line_index": item["line_index"], "text": item["text"][:60]})
    return {
        "manual_review_required": True,
        "unaccepted_candidate_count": len(unaccepted),
        "unaccepted_by_reason": {k: by_reason[k] for k in sorted(by_reason)},
        "unassigned_ambiguous_count": len(ambiguous),
        "unassigned_ambiguous": ambiguous,
        "pending_ts4": [item.to_dict() for item in result.pending],
        "outline_unassigned_spans": len(result.outline.unassigned),
        "note": ("TS3 不删除低置信正文，也不把低置信正文强升为节点：未采纳的正文行"
                 "仍按 ordinary_content 守恒记账，等待 TS4 切分 OutlineSpan。"),
    }


def node_anchor_validation(result, layout) -> dict:
    """**节点标题 / bbox** 与真实版式行的归一化对账（对象级，可复核）。

    这一层回答的是"标题树里的每个标题，是否真能在 `PageLayout` 上逐字符复核"：
    原始行文本、`tight()` 归一后的行文本、节点 `title_normalized`、以及 bbox 是否
    与真实行 bbox **逐值相等**。任何一项不成立都记录为 mismatch（fail-closed）。

    §五 口径：本产物**只**校验节点锚点，因此文件名是 `node_anchor_validation.json`；
    它与 `normalization_alignment.json`（= 本文档全部 Evidence block 的正式对齐
    终态）是两件事，不得互相顶替。
    """
    items = []
    exact = contained = mismatch = 0
    bbox_equal = bbox_diff = 0
    for node in result.outline.nodes:
        page_number, line_index, bbox = node.source_anchor
        line = layout.line_at(page_number, line_index)
        if line is None:
            items.append({"node_id": node.node_id, "status": "line_missing",
                          "page_number": page_number, "line_index": line_index})
            mismatch += 1
            continue
        line_tight = tight(line.text)
        if line_tight == node.title_normalized:
            status = "exact"
            exact += 1
        elif node.title_normalized in line_tight:
            status = "contained"
            contained += 1
        else:
            status = "mismatch"
            mismatch += 1
        same_bbox = tuple(bbox) == tuple(line.bbox)
        bbox_equal += 1 if same_bbox else 0
        bbox_diff += 0 if same_bbox else 1
        items.append({
            "node_id": node.node_id, "status": status,
            "page_number": page_number, "line_index": line_index,
            "title_normalized": node.title_normalized,
            "line_text_tight": line_tight[:120],
            "bbox": list(bbox), "line_bbox": list(line.bbox),
            "bbox_equal_to_real_line": same_bbox,
        })
    return {
        "manual_review_required": True,
        "artifact": "node_anchor_validation.json",
        "scope": "只校验**节点标题 / bbox** 与真实版式行；不承载 Evidence 对齐终态",
        "normalization_version": V.NORMALIZATION_VERSION,
        "total_nodes": len(result.outline.nodes),
        "exact_match": exact, "contained_match": contained,
        "mismatch": mismatch,
        "bbox_equal": bbox_equal, "bbox_diff": bbox_diff,
        "all_titles_recheckable": mismatch == 0,
        "all_bboxes_equal_to_real_line": bbox_diff == 0,
        "items": items,
    }


def _member_identity_sha256(members) -> str:
    """权威成员**规范身份清单**的 sha256（§八，可重算、不含正文）。"""
    payload = json.dumps([m.identity for m in members], ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def authoritative_snapshot(gateway, layout):
    """用受信任只读 gateway 注入本文档的权威 `EvidenceSetSnapshot`（§八）。

    aligner **不自行查询数据库**：成员身份一律由这里注入。gateway 自报版本与快照
    记录版本的一致性由 `snapshot_from_gateway` 核对（不可核验即拒）。
    """
    return A.snapshot_from_gateway(
        gateway, company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version)


def normalization_alignment(layout, blocks, snapshot=None) -> dict:
    """本文档**全部** `EvidenceBlock` 的正式对齐终态（§五）。

    唯一入口是集合级的 `align_evidence_set`：它先做身份闭合（公司 / 文档 / 版本 /
    set / 内容哈希 / evidence_id 全部重算比对）、拒绝重复消费同一 `(页, 块)`、拒绝
    混用 set，并核对**每个块恰好一个正式终态**；本函数随后把每个终态**原样**落盘
    （`TextAlignmentRecord` 或 `AlignmentRefusalRecord` 的 wire 形式），使
    `normalization_alignment.json`、正式 schema 对象与 `alignment_summary` 三者
    可以**逐条互相对账**，而不是只给一个总数。

    §八：`snapshot` 是**受信任只读 gateway** 注入的权威 `EvidenceSetSnapshot`。有块
    却没有权威快照即 fail-closed（"提交清单自身自洽"不能证明"这是本文档当前证据集
    的全部成员"）；提交成员与快照的精确相等由 `align_evidence_set` 强制。

    fixture 文档在 Evidence DB 里没有块：此时如实写 0 条并说明原因（合成 fixture
    不是真实证据集），不得用别的文档的块来凑数，也不得把"没有块"写成"已对齐"。
    """
    if not blocks:
        return {
            "manual_review_required": True,
            "artifact": "normalization_alignment.json",
            "scope": "本文档全部 Evidence block 的正式对齐终态（逐块，可对账）",
            "page_layout_id": layout.page_layout_id,
            "company_id": layout.company_id,
            "document_id": layout.document_id,
            "document_version": layout.document_version,
            "evidence_set_version": None,
            "block_count": 0,
            "terminals_emitted": 0,
            "terminals_missing": [],
            "terminal_schema_versions": {},
            "rows": [],
            "alignment_summary": None,
            "note": ("该文档在 Evidence DB 里没有 EvidenceBlock（合成 fixture，不是"
                     "真实证据集），因此本文档的正式对齐终态为 0 条；这**不是**"
                     "对齐通过，也不是守恒的一部分。"),
        }
    versions = {b.evidence_set_version for b in blocks}
    if len(versions) != 1:
        raise SystemExit(
            f"[SCHEMA_CONFLICT] {layout.document_id} 的 Evidence 混用多个 "
            f"evidence_set_version：{sorted(versions)}；无法声明本文档所属证据集")
    declared = sorted(versions)[0]
    if not isinstance(snapshot, A.EvidenceSetSnapshot):
        raise SystemExit(
            f"[ENVIRONMENT_DISCREPANCY] {layout.document_id} 有 "
            f"{len(blocks)} 个 EvidenceBlock，但没有权威 EvidenceSetSnapshot"
            f"（受信任只读 gateway 未注入）；提交清单自身自洽不得作为证据集身份"
            f"（§八，fail-closed）")
    aligned = A.align_evidence_set(layout, blocks, snapshot=snapshot)
    report = A.alignment_terminal_report(aligned.blocks)
    rows = []
    for block in aligned.blocks:
        terminal = block.terminal
        rows.append({
            "page_number": block.page_number,
            "block_index": block.block_index,
            "evidence_block_id": block.evidence_block_id,
            "evidence_set_version": block.evidence_set_version,
            "page_layout_id": block.page_layout_id,
            "terminal_schema_type": (None if terminal is None
                                     else type(terminal).__name__),
            "terminal_schema_version": (None if terminal is None
                                        else terminal.schema_version),
            "verdict": block.verdict,
            "is_citable": block.is_citable,
            "matched_chars": block.matched_chars,
            "block_char_length": block.block_char_length,
            "exact_coverage": block.exact_coverage,
            "quantized_coverage": A.quantize(block.exact_coverage),
            "refusal_reason": block.record_refusal,
            # 终态的**完整 wire 形式**（不是摘要）：记录与拒绝记录都在这里逐字复核。
            "terminal": None if terminal is None else terminal.to_dict(),
        })
    return {
        "manual_review_required": True,
        "artifact": "normalization_alignment.json",
        "scope": "本文档全部 Evidence block 的正式对齐终态（逐块，可对账）",
        "page_layout_id": layout.page_layout_id,
        "company_id": layout.company_id,
        "document_id": layout.document_id,
        "document_version": layout.document_version,
        "evidence_set_version": declared,
        "block_count": len(rows),
        "terminals_emitted": report["terminals_emitted"],
        "terminals_missing": [r for r in report["rows"]
                             if not r["terminal_schema_type"]],
        # 计数是**派生值**，不是第二份真值：上面那张表才是真值，计数只为下游
        # 引用方便，避免各处各自 `len(...)` 后口径漂移。
        "terminals_missing_count": sum(
            1 for r in report["rows"] if not r["terminal_schema_type"]),
        "terminal_schema_versions": aligned.summary["terminal_schema_versions"],
        "alignment_summary": aligned.summary,
        # §八：正式终态所属的**权威证据集身份**（含全成员指纹）。它随产物落盘，
        # 使"旧的自报输入"与"新权威 Snapshot 输入"在下游可分辨。成员明细只落
        # **规范身份清单的 sha256**：完整清单可从权威 gateway 重算，产物里再存一份
        # 就等于制造第二份"证据集真值"，反而给下游留下分叉空间。
        "evidence_set_snapshot": snapshot.identity(),
        "member_identity_sha256": _member_identity_sha256(snapshot.members),
        "rows": rows,
    }


def formal_unassigned(result) -> dict:
    """正式 `DocumentOutline.unassigned` 的落盘视图（§三）。

    这些 `OutlineSpan(role="unassigned")` 是**正式对象的一部分**：它们进入
    `to_dict()` / `from_dict()`、参与 `outline_id` 与 `content_fingerprint` 的派生，
    篡改或删除任意一条都会改变 fingerprint（`_tamper_probe` 会逐项验证）。本产物
    只是把它们原样摊开供人工查阅，**不是**权威载体：sidecar 被改了不影响正式状态，
    正式对象被改了读回即 fail-closed。
    """
    outline = result.outline
    spans = [span.to_dict() for span in outline.unassigned]
    by_reason: dict = {}
    for span in outline.unassigned:
        key = span.unassigned_reason or "(空)"
        by_reason[key] = by_reason.get(key, 0) + 1
    return {
        "manual_review_required": True,
        "artifact": "formal_unassigned.json",
        "scope": ("正式 DocumentOutline.unassigned 的原样 wire 形式；sidecar 只是"
                  "人工查阅副本，不是权威载体"),
        "outline_id": outline.outline_id,
        "outline_locator": outline.outline_locator,
        "content_fingerprint": outline.content_fingerprint,
        "count": len(spans),
        "by_unassigned_reason": dict(sorted(by_reason.items())),
        "spans": spans,
        "node_count": len(outline.nodes),
    }


def _candidate_fields(record) -> dict:
    """把正式的 `CandidateAuditRecord.evidence`（`key=value` 字符串元组）解析成字段。

    审计记录是**公共类型**的对象，不暴露构建器内部的 `HeadingCandidate`。这里的字段
    只来自该客观记录里已经写下的证据事实，不重新判定、不补默认值。
    """
    fields: dict = {}
    for chunk in record.evidence:
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        fields[key] = value
    return fields


def _evidence_list(value: str) -> list:
    """证据字段是逗号分隔串，`-` 表示"没有"。"""
    return [] if value in ("", "-") else value.split(",")


def _evidence_profile(record) -> dict:
    """可从审计记录客观读出的候选画像（无自报置信度）。"""
    fields = _candidate_fields(record)
    return {
        "numbered": fields.get("numbered") == "True",
        "declared_level": record.declared_level,
        "font_size": float(fields.get("font_size", "0") or 0),
        "bold": fields.get("bold") == "True",
        "centered": fields.get("centered") == "True",
        "body_recurrence": int(fields.get("body_recurrence", "0") or 0),
        "toc_matches": int(fields.get("toc_matches", "0") or 0),
        "bookmark_matches": int(fields.get("bookmark_matches", "0") or 0),
        "primary_evidence": _evidence_list(fields.get("primary_evidence", "-")),
        "supporting_evidence": _evidence_list(
            fields.get("supporting_evidence", "-")),
        "soft_reasons": _evidence_list(fields.get("soft_reasons", "-")),
        "rejected": list(record.reason_codes),
    }


def heading_acceptance_stats(result) -> dict:
    """§二 要求的**分类计数**（不接受"只报总节点数"）。

    三件必须分开报的事：

    1. `numbering_only_accepted`：`primary_evidence` 为空的已接受候选 —— 编号
       序列**永远不是**充分标题证据，所以这个数**必须为 0**；
    2. `small_heading_accepted` / `small_heading_samples`：字号不大于正文基准、
       未加粗、且无 TOC / bookmark 支持，却仍被接受的候选 —— 这正是"有效小标题
       不因字号小被误删"的证明（判定由编号序列 + 版式 / 上下文结构支撑）；
    3. `accepted_navigation_matches` / `accepted_by_qualification_basis` /
       `rejected_by_reason`：接受面的**导航命中**构成、**正式资格依据**构成，
       以及拒绝面的原因分布（误报类型分布）。

       **导航与资格是两件事，字段名不得互相冒充**（本轮纠正）：
       - `accepted_navigation_matches` 只表示"该已采纳节点**同时命中过** TOC / Bookmark
         导航信息"，它由 `toc_matches` / `bookmark_matches` 计数得出。Bookmark **可以**
         出现在这里 —— 它确实是一份导航信息。
       - `accepted_by_qualification_basis` 只由正式 `primary_evidence` 得出，即"这条
         候选**凭什么**资格被采纳"。Bookmark **不得**单独出现在这里：`hq-4` 起书签不在
         `PageLayout` 里，复核方无法重建它的身份，因此它根本不进资格算术。
         非正式证据码（不在 `OB.HEADING_PRIMARY_EVIDENCE` 内）逐条记入
         `non_formal_qualification_basis`，必须为空。

    计数基数用**正式审计记录**（`result.candidates`），它与构建器内部候选一一对应，
    因此这些数字可与 `document_outline.json` / `outline_candidate_audit.json`
    逐条对账 —— 不是旁路统计。
    """
    accepted = [c for c in result.candidates if c.accepted]
    rejected = [c for c in result.candidates if not c.accepted]
    profiles = {id(c): _evidence_profile(c) for c in result.candidates}
    body_font = result.stats.get("body_font_size")
    numbering_only = [c for c in accepted if not profiles[id(c)]["primary_evidence"]]
    small = [c for c in accepted
             if not profiles[id(c)]["bold"]
             and not profiles[id(c)]["toc_matches"]
             and not profiles[id(c)]["bookmark_matches"]
             and body_font is not None
             and profiles[id(c)]["font_size"] <= body_font]
    # --- 导航命中：**只**说明"这条已采纳节点同时命中过哪类导航信息" ---
    # Bookmark 可以出现在这里（它确实是一份导航信息）。它**不**说明资格。
    navigation_matches = {"toc_only": 0, "bookmark_only": 0, "body_only": 0,
                          "toc_and_bookmark": 0}
    for c in accepted:
        toc = bool(profiles[id(c)]["toc_matches"])
        bm = bool(profiles[id(c)]["bookmark_matches"])
        if toc and bm:
            navigation_matches["toc_and_bookmark"] += 1
        elif toc:
            navigation_matches["toc_only"] += 1
        elif bm:
            navigation_matches["bookmark_only"] += 1
        else:
            navigation_matches["body_only"] += 1
    reasons: dict = {}
    for c in rejected:
        for code in c.reason_codes:
            reasons[code] = reasons.get(code, 0) + 1
    # --- 正式资格依据：**只**由 `primary_evidence` 得出 ---
    primary: dict = {}
    #: 不属于正式资格词表（`OB.HEADING_PRIMARY_EVIDENCE`）的证据码 → 命中次数。
    #: **必须为空**：它会把"导航信息"（尤其 Bookmark）冒充成"标题资格依据"。词表本身
    #: 由 `document_structure.outline_builder` 冻结，本函数不重复声明、也不放宽。
    non_formal: dict = {}
    for c in accepted:
        for kind in profiles[id(c)]["primary_evidence"]:
            primary[kind] = primary.get(kind, 0) + 1
            if kind not in OB.HEADING_PRIMARY_EVIDENCE:
                non_formal[kind] = non_formal.get(kind, 0) + 1
    return {
        "candidate_count": len(result.candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "numbering_only_accepted": len(numbering_only),
        "numbering_only_accepted_samples": [
            {"page_number": c.page_number, "line_index": c.line_index,
             "text": c.text[:80]} for c in numbering_only[:20]],
        "small_heading_accepted": len(small),
        "body_font_size": body_font,
        # §「导航 ≠ 资格」：两项分开记账。前者可以由 Bookmark 命中；后者**不得**
        # 由 Bookmark 单独构成 —— 书签不在 `PageLayout` 里，复核方重建不出它的身份。
        "accepted_navigation_matches": navigation_matches,
        "accepted_by_qualification_basis": dict(sorted(primary.items())),
        "non_formal_qualification_basis": dict(sorted(non_formal.items())),
        "accepted_by_qualification_basis_samples": [
            {"page_number": c.page_number, "line_index": c.line_index,
             "text": c.text[:80], "declared_level": c.declared_level,
             "primary_evidence": profiles[id(c)]["primary_evidence"],
             "toc_matches": profiles[id(c)]["toc_matches"],
             "bookmark_matches": profiles[id(c)]["bookmark_matches"]}
            for c in accepted
            if OB.TOC_BODY_LANDING_EVIDENCE in profiles[id(c)]["primary_evidence"]],
        "rejected_by_reason": dict(sorted(reasons.items())),
        "accepted_samples": [
            {"page_number": c.page_number, "line_index": c.line_index,
             "text": c.text[:80], **_evidence_profile(c)}
            for c in accepted[:20]],
        "small_heading_samples": [
            {"page_number": c.page_number, "line_index": c.line_index,
             "text": c.text[:80], **_evidence_profile(c)}
            for c in small[:30]],
        "rejected_samples": [
            {"page_number": c.page_number, "line_index": c.line_index,
             "text": c.text[:80], **_evidence_profile(c)}
            for c in rejected[:30]],
    }


def _tamper_probe(result, layout) -> dict:
    """篡改探针：节点标题 / 锚点页 / 锚点行 / bbox 被改后必须 fail-closed。

    两种篡改都要挡住：
      1. **直接改 JSON**（`from_dict`）——改完的 `node_id` 与派生身份不一致；
      2. **重算 id 的自洽伪造**（`OutlineNode.create` 换锚点后重建 outline）——
         节点的 `parent_child` occurrence 与节点锚点不再互相印证，或子节点
        `parent_id` 悬空，必须抛错而**不是**被接受。
    """
    out: dict = {"probes": []}
    base = result.outline.to_dict()

    def probe(name: str, mutate) -> None:
        payload = json.loads(json.dumps(base))
        mutate(payload)
        try:
            S.DocumentOutline.from_dict(payload)
            out["probes"].append({"name": name, "rejected": False})
        except Exception as e:  # noqa: BLE001 - 只记录拒绝与否
            out["probes"].append({"name": name, "rejected": True,
                                  "error": f"{type(e).__name__}: {str(e)[:160]}"})

    if base["nodes"]:
        probe("title", lambda p: p["nodes"][0].__setitem__("title", "被篡改的标题"))
        probe("anchor_page",
              lambda p: p["nodes"][0]["source_anchor"].__setitem__(0, 9999))
        probe("anchor_line",
              lambda p: p["nodes"][0]["source_anchor"].__setitem__(1, 9999))
        probe("anchor_bbox",
              lambda p: p["nodes"][0]["source_anchor"].__setitem__(
                  2, [1.0, 2.0, 3.0, 4.0]))
        probe("level", lambda p: p["nodes"][0].__setitem__("level", 3))

    out["unassigned_count"] = len(base["unassigned"])
    out["content_fingerprint"] = base["content_fingerprint"]

    # 自洽伪造：换锚点后重算 node_id 重建 outline。
    forged = None
    if len(result.outline.nodes) >= 2:
        target = result.outline.nodes[1]
        bad = S.OutlineNode.create(
            document_outline_locator=target.document_outline_locator,
            parent_id=target.parent_id, title=target.title,
            title_normalized=target.title_normalized,
            structural_path=target.structural_path,
            source_anchor=(target.source_anchor[0] + 1, target.source_anchor[1],
                           target.source_anchor[2]),
            child_ids=target.child_ids, ordinal=target.ordinal)
        nodes = tuple(bad if n.node_id == target.node_id else n
                      for n in result.outline.nodes)
        try:
            S.DocumentOutline.create(
                document_id=result.outline.document_id,
                document_version=result.outline.document_version,
                page_layout_id=result.outline.page_layout_id, nodes=nodes,
                edges=tuple(result.outline.edges),
                unassigned=tuple(result.outline.unassigned),
                candidate_sources=tuple(result.outline.candidate_sources),
                reference_context=S.ReferenceValidationContext(
                    layout=layout, toc_sources=tuple(result.toc_sources)))
            forged = {"rejected": False}
        except Exception as e:  # noqa: BLE001
            forged = {"rejected": True,
                      "error": f"{type(e).__name__}: {str(e)[:160]}"}
    out["forged_self_consistent_anchor"] = forged

    # 正式 unassigned 的**身份闭合**探针（§三）：删 / 改 / 增正式 unassigned 必须
    # 要么读回 fail-closed，要么让 `content_fingerprint` 改变 —— 两者都不发生就是
    # "正式状态没闭合"。sidecar 不在探针范围内：它不是权威载体。
    unassigned_probes = []
    for name, mutate in (
            ("unassigned_dropped", lambda p: p["unassigned"].pop(0)),
            ("unassigned_text_replaced",
             lambda p: p["unassigned"][0].__setitem__("normalized_text", "被篡改")),
            ("unassigned_anchor_moved",
             lambda p: p["unassigned"][0]["start_anchor"].__setitem__(0, 9999)),
            ("unassigned_reason_replaced",
             lambda p: p["unassigned"][0].__setitem__("unassigned_reason", "被篡改")),
            ("unassigned_added_new",
             lambda p: p["unassigned"].append(json.loads(json.dumps(
                 p["unassigned"][0] if p["unassigned"] else {})))),
    ):
        payload = json.loads(json.dumps(base))
        if not payload["unassigned"] and name != "unassigned_added_new":
            continue
        mutate(payload)
        entry = {"name": name, "rejected": False, "fingerprint_changed": None}
        try:
            mutated = S.DocumentOutline.from_dict(payload)
            entry["fingerprint_changed"] = (
                mutated.content_fingerprint != base["content_fingerprint"])
        except Exception as e:  # noqa: BLE001 - 只记录拒绝与否
            entry["rejected"] = True
            entry["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        entry["closed"] = bool(entry["rejected"] or entry["fingerprint_changed"])
        unassigned_probes.append(entry)
    out["unassigned_identity_probes"] = unassigned_probes
    out["unassigned_identity_closed"] = (
        bool(unassigned_probes) and all(p["closed"] for p in unassigned_probes))
    out["all_rejected"] = (all(p["rejected"] for p in out["probes"])
                           and forged is not None and forged["rejected"])
    return out


def structure_validation(result, layout, context) -> dict:
    """结构完整性 / 对象级复核（真实版式上逐项重算）。"""
    checks: list = []

    def ok(cond: bool, msg: str) -> bool:
        checks.append(("PASS " if cond else "FAIL ") + msg)
        return bool(cond)

    outline = result.outline

    # 1) 上游身份对象级核对
    try:
        outline.verify_upstream(layout=layout)
        ok(True, "outline 与上游 PageLayout 的对象级身份一致")
    except Exception as e:  # noqa: BLE001
        ok(False, f"upstream 身份核对失败：{type(e).__name__}: {e}")

    # 2) 行守恒：每个非家具行恰好一个桶
    try:
        result.assert_conserved(layout)
        ok(True, "非家具行记账守恒（无静默丢失、无重复记账）")
    except Exception as e:  # noqa: BLE001
        ok(False, f"行记账不守恒：{type(e).__name__}: {e}")

    # 3) 节点锚点逐个对象级复核
    bad_anchor = []
    for node in outline.nodes:
        page_number, line_index, bbox = node.source_anchor
        line = layout.line_at(page_number, line_index)
        if line is None or line.is_furniture:
            bad_anchor.append((node.node_id, "line_missing_or_furniture"))
            continue
        if node.title_normalized not in tight(line.text):
            bad_anchor.append((node.node_id, "title_not_in_line"))
        if tuple(bbox) != tuple(line.bbox):
            bad_anchor.append((node.node_id, "bbox_not_real_line"))
    ok(not bad_anchor,
       f"每个节点的 page/line/bbox/title 都能在真实版式上对象级复核"
       f"（异常 {len(bad_anchor)} 项）")

    # 4) 层级 / 父子 / 路径自洽
    by_id = {n.node_id: n for n in outline.nodes}
    errors = []
    for node in outline.nodes:
        if node.level != len(node.structural_path) - 1:
            errors.append((node.node_id, "level_path"))
        if node.structural_path[-1] != node.title_normalized:
            errors.append((node.node_id, "path_last"))
        if node.parent_id is not None:
            parent = by_id.get(node.parent_id)
            if parent is None:
                errors.append((node.node_id, "parent_missing"))
            elif parent.level >= node.level:
                errors.append((node.node_id, "parent_level"))
            elif node.node_id not in parent.child_ids:
                errors.append((node.node_id, "parent_child_ids"))
        children = [by_id[c] for c in node.child_ids if c in by_id]
        if any(child.parent_id != node.node_id for child in children):
            errors.append((node.node_id, "child_parent_symmetry"))
    ok(not errors, f"层级 / 父子 / 结构路径自洽（异常 {len(errors)} 项）")

    # 5) 身份唯一 / 同名标题不得被合并
    #
    # 注意口径：**完整 structural path 允许重复**。真实年报里同一父路径下的同名
    # 小节（例如现金流量表在正文与附注里各出现一次）本来就是两条不同的真实标题，
    # 只要它们的锚点与身份各自独立，就没有被合并。真正要挡的是"合并"：同一个
    # (标题, 父路径) 只留下一个节点而多个真实锚点消失，或同页同名只留一条。
    # 中间层级标题未被采纳时，两个不同小节会落在同一父路径下 —— 这是"不猜父节点"
    # 的必然结果，必须如实上报（`duplicate_path_groups`），不得为漂亮树补猜父节点。
    node_ids = [n.node_id for n in outline.nodes]
    ok(len(set(node_ids)) == len(node_ids), "node_id 唯一")

    by_path: dict = {}
    for node in outline.nodes:
        by_path.setdefault(tuple(node.structural_path), []).append(node)
    duplicate_groups = []
    merged = []
    for path, group in sorted(by_path.items()):
        if len(group) < 2:
            continue
        anchors = {(n.source_anchor[0], n.source_anchor[1]) for n in group}
        duplicate_groups.append({
            "structural_path": list(path), "count": len(group),
            "distinct_anchors": len(anchors),
            "node_ids": [n.node_id for n in group],
            "anchors": [[n.source_anchor[0], n.source_anchor[1]] for n in group],
        })
        if len(anchors) != len(group):
            merged.append(list(path))
    ok(not merged,
       f"重复的完整路径**未被合并**（重复组 {len(duplicate_groups)}，"
       f"其中锚点重复的组 {len(merged)}）")

    by_title_page: dict = {}
    for node in outline.nodes:
        key = (node.title_normalized, node.source_anchor[0])
        by_title_page.setdefault(key, []).append(node)
    same_page_merged = [
        {"title": key[0], "page_number": key[1], "lines": [n.source_anchor[1] for n in g]}
        for key, g in sorted(by_title_page.items()) if len(g) > 1
        and len({n.source_anchor[1] for n in g}) != len(g)]
    ok(not same_page_merged,
       f"同页同名标题未被合并（同页同名 {sum(1 for g in by_title_page.values() if len(g) > 1)} 组）")

    by_title_parents: dict = {}
    for node in outline.nodes:
        by_title_parents.setdefault(node.title_normalized, set()).add(
            tuple(node.structural_path[:-1]))
    cross_parent = sorted(t for t, parents in by_title_parents.items()
                          if len(parents) > 1)
    counts_by_title = {}
    for node in outline.nodes:
        counts_by_title[node.title_normalized] = \
            counts_by_title.get(node.title_normalized, 0) + 1
    ok(all(counts_by_title[t] >= len(by_title_parents[t]) for t in cross_parent),
       f"同名标题在不同父路径下各自独立成节点（{len(cross_parent)} 个标题跨父路径）")

    # 6) 引用对象级核验（含 toc_to_body 的页标签映射与节点锚点核验）
    verified = None
    try:
        ref_context = S.ReferenceValidationContext(
            layout=layout, toc_sources=tuple(result.toc_sources))
        verified = outline.verify_references(ref_context)
        ok(True, f"引用对象级核验通过：{len(verified.verified_edges)} 条已核验、"
                 f"{len(verified.unresolved_edges)} 条未解析")
    except Exception as e:  # noqa: BLE001
        ok(False, f"引用核验失败：{type(e).__name__}: {e}")

    # 7) 边类型封闭：不得出现 TS5 的 table_continuation
    kinds = sorted({edge.edge_kind for edge in outline.edges})
    ok(all(k in S.EDGE_KINDS for k in kinds), f"边类型属于公共 EDGE_KINDS：{kinds}")
    ok("table_continuation" not in kinds,
       "不生成 TS5 才负责的 table_continuation")
    ok("cross_reference" not in kinds,
       "不生成 TS3 无真实载体可用的 cross_reference 边（只登记 pending_ts4）")

    # 8) 确定性：同输入再建一次，逐字节一致
    again = OB.build_document_outline(
        OB.OutlineSourceContext(
            layout=context.layout, pdf_sha256=context.pdf_sha256,
            company_id=context.company_id, document_id=context.document_id,
            document_version=context.document_version,
            bookmarks=context.bookmarks))
    same_json = canonical_json(again.outline.to_dict()) \
        == canonical_json(outline.to_dict())
    ok(same_json, "同输入连续两次构建的 outline JSON 逐字节一致")

    # 9) 来源顺序无关：打乱书签顺序不改变 JSON / 身份
    shuffled = OB.OutlineSourceContext(
        layout=context.layout, pdf_sha256=context.pdf_sha256,
        company_id=context.company_id, document_id=context.document_id,
        document_version=context.document_version,
        bookmarks=tuple(reversed(context.bookmarks)))
    shuffled_result = OB.build_document_outline(shuffled)
    ok(canonical_json(shuffled_result.outline.to_dict())
       == canonical_json(outline.to_dict()),
       "候选 / 书签输入顺序改变不改变最终 JSON 与身份")

    return {
        "manual_review_required": True,
        "ok": all(c.startswith("PASS ") for c in checks),
        "checks": checks,
        "node_count": len(outline.nodes),
        "edge_count": len(outline.edges),
        "edge_kinds": kinds,
        "verified_edge_count": (None if verified is None
                                else len(verified.verified_edges)),
        # 人工复核观察项（不是失败项）：同一父路径下同名小节重复出现多少组、
        # 同名标题跨父路径多少组。它们是"不猜父节点 / 不合并"的公开代价。
        "duplicate_path_group_count": len(duplicate_groups),
        "duplicate_path_groups": duplicate_groups[:40],
        "same_page_same_title_group_count": sum(
            1 for g in by_title_page.values() if len(g) > 1),
        "cross_parent_same_title_count": len(cross_parent),
        "cross_parent_same_title_samples": cross_parent[:20],
        "tamper_probe": _tamper_probe(result, layout),
    }


def before_after(result, layout, evidence_block_count=None) -> str:
    """前 / 后对照：建树之前是什么、之后是什么，正文有没有少。"""
    outline = result.outline
    counts = result.line_assignment_counts()
    levels = result.stats["levels"]
    lines = [f"# 标题树前后对照（{outline.document_id}）", "",
             "**状态：manual_review_required**（构建完成不等于标题树通过）", "",
             "## 前：未建树时的产物形态", "",
             f"- PageLayout 非家具行：{result.stats['non_furniture_lines']} 行"
             f"（全部页，共 {result.stats['total_pages']} 页）",
             f"- 目录页：{result.stats['toc_pages']}，目录项："
             f"{result.stats['toc_entries']} 条",
             f"- PDF 书签（S2）：{result.stats['bookmarks']} 条",
             f"- 无 `DocumentOutline`：没有节点、没有父子关系、没有 `OutlineSpan`"]
    if evidence_block_count is not None:
        lines.append(f"- 存量 `EvidenceBlock`：{evidence_block_count} 块"
                     "（旧 `Evidence.section_path` **不**参与本树构建）")
    lines += ["", "## 后：标题树与记账", "",
              f"- 节点：{len(outline.nodes)}",
              "- 层级分布：" + "，".join(f"L{k}={v}" for k, v in levels.items()),
              f"- 边：{len(outline.edges)}（parent_child "
              f"{result.stats['edges_parent_child']} / toc_to_body "
              f"{result.stats['edges_toc_to_body']}：已解析 "
              f"{result.stats['edges_toc_to_body_resolved']}、未解析 "
              f"{result.stats['edges_toc_to_body_unresolved']}）",
              f"- `cross_reference` 边：0（登记待 TS4："
              f"{result.stats['cross_reference_pending_ts4']} 处引用标记）",
              f"- 正文基准字号：{result.stats['body_font_size']}", "",
              "## 行守恒（前 → 后逐桶，**内部记账**）", "",
              "> 下表是构建器内部记账桶，**不是** §六 的正式四态；"
              "正式四态见 `line_structure_assignment.json`（"
              "`heading_node` / `formal_unassigned` / `body_under_node` / "
              "`non_content`），两者都必须守恒。", "",
              "| 桶 | 行数 |", "|---|---|"]
    for name, count in counts.items():
        lines.append(f"| {name} | {count} |")
    lines.append(f"| 合计 | {len(result.line_assignments)} |")
    lines += ["",
              f"合计 = 非家具行数（{result.stats['non_furniture_lines']}）："
              f"**{'是' if len(result.line_assignments) == result.stats['non_furniture_lines'] else '否'}**"
              "。建树没有删除任何正文行：未采纳的候选与低置信正文仍按 "
              "`ordinary_content` / `unassigned_ambiguous` 守恒记账。", "",
              "## 本轮未做（避免把 pending 当成已完成）", "",
              "- `OutlineSpan` 正文边界与 synopsis：**TS4**（当前每个已采纳标题下的"
              "正文仍是待切分的 ordinary_content）",
              "- `TableObject`：**TS5**；`AspectNavigationProfile`：**TS6**；"
              "跨树聚合与接线：**TS7A / TS7B**", ""]
    return "\n".join(lines)


def frozen_distribution_by_document(baseline_rows) -> dict:
    """冻结 TS2 基线按文档 / 判定分组的分布（只读对账用）。"""
    out: dict = {}
    for row in baseline_rows:
        entry = out.setdefault(row["document_id"], {
            "block_count": 0, "verdicts": {}, "citable": 0})
        entry["block_count"] += 1
        entry["verdicts"][row["verdict_now"]] = \
            entry["verdicts"].get(row["verdict_now"], 0) + 1
        entry["citable"] += 1 if row["is_citable_now"] else 0
    for entry in out.values():
        entry["verdicts"] = dict(sorted(entry["verdicts"].items()))
    return out


def _landing_brief(landing) -> str:
    """把复核侧**自己重算**的来源 landing 事实压成一行可人工核对文本（§三）。

    缺失 / 空载荷一律显式写 `reported=False`：这一行存在的意义正是让"生产侧声称有来源、
    复核侧没重算出来"当场可见，不能因为字段缺失就打印成空串蒙混过去。
    """
    if not isinstance(landing, dict):
        return "（复核侧没有重算结果：字段缺失 → fail-closed）"
    if not landing.get("reported"):
        return "reported=False（生产侧没有持久化来源 landing 载荷）"
    entries = []
    for item in landing.get("entries") or ():
        if item.get("kind") == "toc":
            entries.append(f"toc:{item.get('toc_source_id')} "
                           f"源p{item.get('source_page')}l{item.get('source_line')} "
                           f"标签{item.get('declared_page_label')}"
                           f"→物理p{item.get('physical_page')}")
        else:
            entries.append(f"bookmark:声明p{item.get('bookmark_page')}"
                           f"→landing p{item.get('landing_page')}"
                           f"l{item.get('landing_line')}")
    # §二.2（`hq-4`）：书签条目**只**作导航候选 / 审计信息，逐条列出但**不**计入
    # `available`。这一行必须把两件事分开写，否则读产物的人会以为"有 bookmark 条目
    # 就等于来源已复核"。
    nav = [f"bookmark:声明p{item.get('bookmark_page')} "
           f"{item.get('title_normalized') or item.get('title')!r} "
           f"（{item.get('navigation_reason')}）"
           for item in (landing.get("navigation_only") or ())]
    return (f"reported=True available={landing.get('available')} "
            f"逐条={entries or '[]'} problems={landing.get('problems')} "
            f"书签身份可重算={landing.get('bookmark_identity_recheckable')}"
            + (f" 书签仅导航（**不**计入 available）={nav}" if nav else ""))


def manual_review(result, validation, alignment_doc, baseline_dist,
                  four_state, findings) -> str:
    """人工复核视图（§八）：逐份文档摊开**可被人工核对**的事实，而不是只给结论。

    `four_state` / `findings` 是 §六 的**正式**四态归属与结构复核门；本视图把它们
    摊开成可读项，并据此决定第 10 节能否写"未发现失败项"。
    """
    outline = result.outline
    stats = heading_acceptance_stats(result)
    counts = result.line_assignment_counts()
    levels = result.stats["levels"]
    lines = [f"# 人工复核：{outline.document_id}", "",
             "**状态：manual_review_required**（构建完成不等于标题树通过）", "",
             "## 1. 完整标题树", "",
             "（同一棵树的可读版本见 `outline_tree.md`；逐节点身份见 "
             "`document_outline.json`）", ""]
    for node in outline.nodes:
        indent = "  " * node.level
        lines.append(f"- {indent}`L{node.level}` p{node.source_anchor[0]} "
                     f"l{node.source_anchor[1]} {node.title}")
    lines += ["", "## 2. 标题层级分布", "",
              "| 层级 | 节点数 |", "|---|---|"]
    for level, count in levels.items():
        lines.append(f"| L{level} | {count} |")
    lines += [f"| 合计 | {len(outline.nodes)} |", "",
              f"- 正文基准字号：`{stats['body_font_size']}`",
              f"- 候选总数：{stats['candidate_count']}（接受 "
              f"{stats['accepted_count']}，拒绝 {stats['rejected_count']}）",
              f"- 接受面的**正式资格依据**构成"
              f"（只由 `primary_evidence` 得出）："
              f"`{stats['accepted_by_qualification_basis']}`",
              f"- 非正式资格依据证据码（不在 `OB.HEADING_PRIMARY_EVIDENCE` 内，"
              f"**必须为空**）：`{stats['non_formal_qualification_basis']}`", "",
              "## 3. 小标题保留情况（不因字号小而误删）", "",
              f"- 字号 ≤ 正文基准、未加粗、且无 TOC / bookmark 支持，**仍被接受**的"
              f"小标题：**{stats['small_heading_accepted']}** 条",
              "- 这些标题靠的是编号序列 + 版式 / 上下文结构证据，而不是字号或加粗。",
              ""]
    for item in stats["small_heading_samples"]:
        lines.append(f"- p{item['page_number']} l{item['line_index']} "
                     f"size={item['font_size']} numbered={item['numbered']} "
                     f"primary={item['primary_evidence']} "
                     f"support={item['supporting_evidence']} {item['text']!r}")
    lines += ["", "## 4. 仅编号、无其他结构证据的误报", "",
              f"- 编号序列**本身不构成**标题证据；`primary_evidence` 为空的已接受"
              f"候选：**{stats['numbering_only_accepted']}** 条（必须为 0）", ""]
    for item in stats["numbering_only_accepted_samples"]:
        lines.append(f"- p{item['page_number']} l{item['line_index']} {item['text']!r}")
    lines += ["", "拒绝原因分布（误报类型）：", "",
              "| 原因码 | 命中次数 |", "|---|---|"]
    for code, count in stats["rejected_by_reason"].items():
        lines.append(f"| {code} | {count} |")
    lines += ["", "## 5. ambiguous / unassigned", "",
              f"- 正式 `DocumentOutline.unassigned`：{len(outline.unassigned)} 条"
              f"（`formal_unassigned.json`）",
              f"- `unassigned_ambiguous` 行："
              f"{counts.get('unassigned_ambiguous', 0)} 行",
              f"- 未采纳候选：{stats['rejected_count']} 条"
              f"（`outline_candidate_audit.json` 逐条列出原因码）",
              f"- 待 TS4 切分：{len(result.pending)} 条", "",
              "## 6. 已采纳标题的**导航命中**与**正式资格依据**（两件事，分开记账）",
              "",
              "### 6.1 导航命中（`accepted_navigation_matches`）",
              "",
              "- 它**只**说明该已采纳节点同时命中过哪类导航信息。Bookmark **可以**出现"
              "在这里 —— 它确实是一份导航信息。",
              "- 它**不**说明这条标题凭什么被采纳。",
              ""]
    for name, value in stats["accepted_navigation_matches"].items():
        lines.append(f"- {name}: {value}")
    lines += ["", "### 6.2 正式资格依据（`accepted_by_qualification_basis`）", "",
              "- 它**只**由正式 `primary_evidence` 得出，回答「这条候选凭什么资格被"
              "采纳」。",
              "- Bookmark **不得**单独出现在这里：`hq-4` 起书签不在 `PageLayout` 里，"
              "复核方无法重建它的身份，因此它根本不进资格算术。",
              f"- 构成：`{stats['accepted_by_qualification_basis']}`",
              f"- 非正式证据码（必须为空）："
              f"`{stats['non_formal_qualification_basis']}`", "",
              f"### 6.3 凭 `{OB.TOC_BODY_LANDING_EVIDENCE}` 采纳的节点"
              f"（{len(stats['accepted_by_qualification_basis_samples'])} 条）", "",
              "- 这些正是「目录项 → 页标签 → 物理页 → 文本一致的正文 landing 行」四段"
              "闭合后取得资格的节点；它们的正式资格依据**必须**是 "
              f"`{OB.TOC_BODY_LANDING_EVIDENCE}`，而不是任何导航命中。", ""]
    for item in stats["accepted_by_qualification_basis_samples"]:
        lines.append(f"- p{item['page_number']} l{item['line_index']} "
                     f"L{item['declared_level']} "
                     f"primary={item['primary_evidence']} "
                     f"toc_matches={item['toc_matches']} "
                     f"bookmark_matches={item['bookmark_matches']} "
                     f"{item['text']!r}")
    lines += ["", "## 7. 每个 Evidence block 的正式 alignment / refusal 终态", ""]
    if alignment_doc["block_count"] == 0:
        lines += [f"- 本文档在 Evidence DB 里没有 EvidenceBlock：{alignment_doc['note']}",
                  ""]
    else:
        summary = alignment_doc["alignment_summary"]
        lines += [
            f"- 本文档块数：**{alignment_doc['block_count']}**",
            f"- 正式终态：**{alignment_doc['terminals_emitted']}**"
            f"（缺 {len(alignment_doc['terminals_missing'])}）",
            f"- 终态版本分布：`{alignment_doc['terminal_schema_versions']}`",
            f"- 判定分布：aligned {summary['aligned']} / partially_aligned "
            f"{summary['partially_aligned']} / unaligned {summary['unaligned']}；"
            f"可引用 {summary['citable_blocks']}",
            f"- 精确阈值：`matched_chars / block_char_length` 与 `ALIGN_MIN="
            f"{V.ALIGN_MIN}` 比较，**不先四舍五入**；"
            f"拒绝终态 {summary['records_refused']} 条",
            "- 逐块终态见 `normalization_alignment.json`（每行含终态的完整 wire "
            "形式），对账见 `alignment_summary.json`。", ""]
        for refusal in alignment_doc["rows"]:
            if refusal["refusal_reason"]:
                lines.append(
                    f"- 拒绝：p{refusal['page_number']} "
                    f"b{refusal['block_index']} exact="
                    f"`{refusal['matched_chars']}/{refusal['block_char_length']}` = "
                    f"{refusal['exact_coverage']!r}，展示值 "
                    f"{refusal['quantized_coverage']}，原因 "
                    f"`{refusal['refusal_reason']}`")
        lines.append("")
    lines += ["## 8. 769 条守恒（本文档部分）", ""]
    if baseline_dist and outline.document_id in baseline_dist:
        frozen = baseline_dist[outline.document_id]
        lines += [f"- 冻结 TS2 基线中本文档块数：**{frozen['block_count']}**",
                  f"- 本文档正式终态数：**{alignment_doc['terminals_emitted']}**",
                  f"- 相符：**{'是' if frozen['block_count'] == alignment_doc['terminals_emitted'] else '否'}**",
                  ""]
    else:
        lines += ["- 本文档不在三份真实文档的 769 冻结范围内（合成 fixture）。", ""]
    lines += ["## 9. TS2 冻结分布对账", ""]
    if baseline_dist and outline.document_id in baseline_dist:
        frozen = baseline_dist[outline.document_id]
        lines += ["| 判定 | 冻结 TS2 | 本轮（对齐算法同一实现） |", "|---|---|---|"]
        summary = alignment_doc["alignment_summary"] or {}
        for verdict in S.ALIGNMENT_VERDICTS:
            lines.append(f"| {verdict} | {frozen['verdicts'].get(verdict, 0)} | "
                         f"{summary.get(verdict, 0)} |")
        lines.append("")
    else:
        lines += ["- 无对应冻结分布（非三份真实文档）。", ""]
    lines += ["## 10. 正式四态逐行结构归属（§六）", "",
              "每条可消费正文行**确定性**落入四态之一（"
              "`line_structure_assignment.json`）：", "",
              "| 结构态 | 行数 |", "|---|---|"]
    for state in OB.LINE_STRUCTURE_STATES:
        lines.append(f"| {state} | {four_state['counts'].get(state, 0)} |")
    body_after_boundary = sum(
        1 for r in four_state["lines"]
        if r["body_attachment"] == "after_formal_unassigned_boundary")
    lines += [
        f"| 合计 | {four_state['four_state_sum']} |", "",
        f"- 非家具行数：**{four_state['non_furniture_lines']}**",
        f"- 四态守恒（四态之和 = 逐行记录数 = 非家具行数）："
        f"**{'是' if four_state['conserved'] else '否'}**",
        f"- `heading_node` 缺 `node_id`：{four_state['heading_node_without_node_id']}",
        f"- `formal_unassigned` 缺 `unassigned_span_id`："
        f"{four_state['formal_unassigned_without_span_id']}",
        f"- 落在 formal-unassigned 边界**之后**、不再继承更早标题的正文行："
        f"**{body_after_boundary}**（遇边界即清空当前标题，不静默继承）", "",
        "## 11. 结构复核门（同级误嵌套 / 正文误收 / 四类独立误收 / 待人工复核候选）",
        "",
        f"- 独立验收方版本：`{findings.get('acceptor_version')}`；"
        f"表格区域资格版本：`{findings.get('table_region_qualification_version')}`",
        f"- `PageLayout` 可用（独立复核前提）："
        f"**{findings.get('layout_available')}**；"
        f"fail-closed 缺口 {len(findings.get('verification_gaps', ()))} 项", ""]
    lines += ["| 复核项 | 数量 |", "|---|---|"]
    for name, value in findings["counts"].items():
        lines.append(f"| {name} | {value} |")
    lines.append("")
    if findings.get("review_counts"):
        lines += ["表格边界复核计数（§三.1 / §三.4；**阻断项非零即不得通过**，"
                  "安全穿透项必须逐条展示证据，见本节下方明细）：", "",
                  "| 复核项 | 数量 |", "|---|---|"]
        for name, value in findings["review_counts"].items():
            lines.append(f"| {name} | {value} |")
        lines.append("")
    if findings.get("audit_counts"):
        lines += ["审计项（**不计入失败**，逐条明细见本节下方，独立验收方每周转复核）：", "",
                  "| 审计项 | 数量 |", "|---|---|"]
        for name, value in findings["audit_counts"].items():
            lines.append(f"| {name} | {value} |")
        lines.append("")
    gate_problems = []
    if findings["counts"]["same_declared_level_nesting"]:
        gate_problems.append(
            f"同级误嵌套 {findings['counts']['same_declared_level_nesting']} 处：同一编号"
            f"体系内声明层级相同却挂成父子（§三禁止）")
    if findings["counts"]["prose_like_accepted"]:
        gate_problems.append(
            f"正文误收（子句标点形态）{findings['counts']['prose_like_accepted']} 条："
            f"人员履历 / 叙述句被当成标题")
    if findings["counts"]["definition_like_accepted"]:
        gate_problems.append(
            f"正文误收（定义冒号形态）"
            f"{findings['counts']['definition_like_accepted']} 条：定义句被当成标题")
    if findings["counts"]["accepted_without_node"]:
        gate_problems.append(
            f"{findings['counts']['accepted_without_node']} 条候选被采纳却没有正式节点")
    # 四种**新增独立误收类**（§二.4）：由独立验收方从持久化版式重新复核得出，
    # 不复用生产侧最终布尔结论。任一条非 0 都是硬失败。
    for name, label in (
        ("global_peer_only_accepted",
         "仅凭全文档同层级候选互证被采纳（§二.1 明令禁止）"),
        ("unsafe_table_override_accepted",
         "是表格**成员**（独立重算）却没通过安全穿透资格却被采纳"
         "（表内不安全穿透）"),
        ("fragmented_cell_accepted",
         "是表格标签列折行 / 截断片段却被当成独立标题行采纳"),
        ("adjacent_heading_rejected",
         "仅仅**邻近**表格就被拒绝的候选（§三.3 明令禁止）"),
        ("accepted_without_intrinsic_primary_evidence",
         "没有任何**候选自身**主证据却被采纳"),
        # §三：两侧结论分叉。**两个方向都是阻断项**，都要显式列出，不得静默分叉：
        # 生产侧判 safe / 复核侧复算不 safe 是漏放，生产侧判 unsafe / 复核侧复算 safe
        # 是漏收 —— 后者即使最终结论"更严"，也说明两侧规则不对称，必须人工裁决。
        ("safe_override_mismatch",
         "生产侧判「安全穿透」、独立复核侧复算**不成立**（两侧规则不对称，漏放）"),
        ("unsafe_override_mismatch",
         "生产侧判「不安全」未采纳、独立复核侧复算**成立**"
         "（两侧规则不对称，可能漏收一条真实标题）"),
        ("source_landing_unverified",
         "已采纳的表内标题缺少**可独立复核**的来源 landing 上下文（fail-closed）"),
        # §四 P1-A(2)（`hq-4`）：已采纳节点自报"凭来源 landing 采纳"、而独立复核侧在真实
        # 版式上**重建不出**任何对象级目录项 landing。这是"按书签 / 自报字符串放行"的
        # 确切形态，是**阻断**计数，不得降级成留痕。
        ("toc_landing_unverified_accepted",
         "已采纳节点自报「凭来源 landing 采纳」、独立复核侧在真实版式上重建不出"
         "（书签 / 自报字符串放行，fail-closed）"),
    ):
        if findings["counts"].get(name):
            gate_problems.append(
                f"{label}：{findings['counts'][name]} 条")
    # §三.4：表内采纳节点**总数**必须等于安全穿透清单的长度 —— 每一个落在表格内部的
    # 已采纳节点都必须在 `safe_table_override_accepted` 里**逐条**出现。差值本身即
    # 阻断失败（防止生产侧把不安全穿透漏报成 audit 项）。
    _inside = findings.get("inside_table_accepted")
    _safe = findings.get("safe_table_override_accepted") or []
    if _inside is not None and _inside != len(_safe):
        gate_problems.append(
            f"表内采纳节点 {_inside} 条，但安全穿透清单只有 {len(_safe)} 条："
            f"每条 `inside_table` 采纳节点都必须逐条给出安全穿透证据"
            f"（§三.4），缺口不得以聚合计数掩盖")
    # §四 P1-B(7)（`hq-4`）：过去这里把**两类完全不同的对象**合并成一句"真标题漏收"：
    # **目录来源行**在目录页上，是导航来源，它**不是**正文行；**正文 landing 行**才是
    # 页映射之后可能成为 `OutlineNode` 的真实行。现在改由 `toc_body_reconciliation` 的
    # 六个确定桶逐类输出。
    #
    # §tocr-2（本轮 fail-open 修复）：`toc_body_unassigned` 是**阻断**桶 —— 目录项已在
    # 真实版式上闭合到一条合格正文行、而该行不在树里，即「可能存在正文标题漏收」的
    # 直接证据。旧 `toc_source_only` 把同一状态写成"目录来源仅有导航价值、**不是**
    # 「真标题漏收」"，是 fail-open，已废除。
    recon = findings.get("toc_body_reconciliation") or {}
    #: §tocr-2 **版本门**：没有版本字段（`tocr-1` 五桶产物与更早的无版本产物都如此）
    #: 或版本不是当前值 ⇒ 这批桶**不得**按当前语义解释，且必须显式阻断 —— 否则一份
    #: 旧产物会被新验收器静默当成"当前版本、六桶皆空"而放行。
    recon_version = findings.get("toc_body_reconciliation_version")
    recon_version_current = recon_version == V.TOC_BODY_RECONCILIATION_VERSION
    if not recon_version_current:
        gate_problems.append(
            f"目录 / 正文 landing 对账的状态机版本不是当前版本：载荷声明 "
            f"{recon_version!r}，当前为 {V.TOC_BODY_RECONCILIATION_VERSION!r}"
            f"（legacy：{list(V.legacy_versions('TOC_BODY_RECONCILIATION_VERSION'))}）—— "
            f"这批桶**不得**按当前语义解释（`tocr-1` 的 `toc_source_only` 语义与本版"
            f"**相反**），必须显式重算后重新验收")
    resolved = recon.get("toc_body_resolved") or []
    body_unassigned_toc = recon.get("toc_body_unassigned") or []
    #: legacy 载荷里的同义桶。它**不**因为改了名字就变成留痕：只要它非空，同样阻断。
    legacy_source_only = recon.get("toc_source_only") or []
    unresolved = recon.get("toc_target_unresolved") or []
    nav_only = recon.get("bookmark_navigation_only") or []
    body_unassigned = recon.get("body_heading_unassigned") or []
    if body_unassigned_toc:
        gate_problems.append(
            f"正文 landing 未成为节点（`toc_body_unassigned`）"
            f"{len(body_unassigned_toc)} 条：目录项在真实版式上**已闭合到一条合格正文"
            f"行**（来源身份可重建、页标签唯一映射到物理页、该页上存在文本一致、"
            f"非家具、非普通表格内容的真实正文行），但该行**不是**正式 `OutlineNode` —— "
            f"这是「可能存在正文标题漏收」的**直接证据**，**不得**为清零它而自动把该"
            f"正文行加入树（逐条见下）")
    if legacy_source_only:
        gate_problems.append(
            f"legacy 载荷里的 `toc_source_only` 非空（{len(legacy_source_only)} 条）："
            f"它与当前的 `toc_body_unassigned` 描述**同一份输入状态**，语义相反，"
            f"因此**同样阻断**，不得按 `tocr-1` 的 fail-open 解读放行")
    if unresolved:
        with_node = [i for i in unresolved if i.get("same_title_node_ids")]
        extra = ""
        if with_node:
            # §四 P1-B(2)：`toc_target_unresolved` 说的是**这条目录项声明的页目标**
            # 没闭合，**不是**"这个正文标题漏收"。树上另有同名节点时逐条点出来，
            # 否则读产物的人会把两者混为一谈（本轮真实文档实测出现过）。
            extra = (f"；其中 {len(with_node)} 条的正文标题**已经**作为节点进树"
                     f"（逐条给出 `same_title_node_ids`）—— 它们**不是**「真标题漏收」，"
                     f"只是**目录页声明的页目标**未闭合")
        gate_problems.append(
            f"目录目标未解析（`toc_target_unresolved`）{len(unresolved)} 条：目录项声明的"
            f"页标签无法唯一映射到物理页 / 映射页上没有文本一致的真实正文行 / 该页上只有"
            f"同名的**普通表格内容**行 —— **不得**为它编造节点，也不得记为落地成功"
            f"{extra}（逐条见下）")
    # 正式 `unassigned` 里因"目录项 / 书签有真实 landing 行却没对应到节点"而未归属的 span。
    # 它们仍必须显形（否则会被静默吞掉），但**不得**再合并成"真标题漏收"：目录来源行在
    # 目录页上、书签在 PDF 大纲里，二者都不是正文标题。
    missed_total = (findings["counts"]["toc_unmatched"]
                    + findings["counts"]["bookmark_unmatched"])
    if missed_total:
        gate_problems.append(
            f"正式 `unassigned` 的目录 / 书签来源未归属：共 {missed_total} 条"
            f"（`toc_unmatched`={findings['counts']['toc_unmatched']} / "
            f"`bookmark_unmatched`={findings['counts']['bookmark_unmatched']}）—— "
            f"目录来源行**不是**正文标题、书签也不在 `PageLayout` 里，二者都"
            f"**不是**「真标题漏收」；分类对账见下方 `toc_body_reconciliation` 六桶")
    # §二.4：`numbered_list_ambiguity` **不得**被直接称作「真标题漏收」—— 这些候选
    # 没有独立人工 gold，本仓库无法证明它们是标题。统一改称「待人工复核候选」，
    # 但仍必须进入同一份 gate（漏掉它会让「某文档明明有 6 条待判，本文件却写自动
    # 检查无失败」，本轮实测出现过）。
    if findings["counts"]["numbered_list_ambiguity"]:
        gate_problems.append(
            f"待人工复核候选 {findings['counts']['numbered_list_ambiguity']} 条："
            f"局部结构证据不足（编号列表歧义）而未成为正式节点"
            f"（正文归属见 `formal_unassigned.json`；无独立人工 gold，"
            f"不得据此宣称漏收）")
    for gap in findings.get("verification_gaps", ()):
        gate_problems.append(
            f"独立验收 fail-closed：{gap.get('kind')} —— {gap.get('detail')}")
    for item in findings["same_declared_level_nesting"][:20]:
        lines.append(f"- 同级误嵌套：父 {item['parent']['title']!r} "
                     f"(p{item['parent']['page_number']} "
                     f"declared={item['parent']['declared_level']}) → 子 "
                     f"{item['child']['title']!r}（declared="
                     f"{item['child']['declared_level']}）")
    for key in ("prose_like_accepted", "definition_like_accepted"):
        for item in findings[key][:20]:
            lines.append(f"- 正文误收（{key}）：p{item['page_number']} "
                         f"l{item['line_index']} {item['title']!r} "
                         f"marks={item['marks']}")
    # §四 P1-B(2)(7)：目录来源 / 正文 landing 的**六类不同对象**逐条摊开。它们必须能被
    # 读产物的人分辨成六类，而不是一句"目录项 / 书签有 landing 却未成节点"。
    if (resolved or body_unassigned_toc or legacy_source_only or unresolved
            or nav_only or body_unassigned or not recon_version_current):
        lines += ["", "目录来源 / 正文 landing 对账（`toc_body_reconciliation`，状态机版本 "
                      f"`{recon_version!r}`（当前 "
                      f"`{V.TOC_BODY_RECONCILIATION_VERSION}`）；"
                      "**六类对象不同，不得互相冒充，也不得合并成一句「真标题漏收」**）：",
                  ""]
        if not recon_version_current:
            lines.append(
                f"- **版本门**：载荷版本 {recon_version!r} ≠ 当前 "
                f"{V.TOC_BODY_RECONCILIATION_VERSION!r} ⇒ 下列桶**不得**按当前语义解释，"
                f"必须显式重算（见本文件 gate 问题）")
        lines.append(
            f"- `toc_body_resolved`={len(resolved)}（目录项四段闭合且已成为正式节点）；"
            f"`toc_body_unassigned`={len(body_unassigned_toc)}"
            f"（**阻断**：目录项已闭合到合格正文行、该行**不在树里**）；"
            f"`toc_target_unresolved`={len(unresolved)}（目标在版式上无法确认）；"
            f"`bookmark_navigation_only`={len(nav_only)}（书签：只有导航价值）；"
            f"`body_heading_unassigned`={len(body_unassigned)}"
            f"（**正文**候选未采纳，与目录来源行是不同对象）")
        for item in resolved[:40]:
            lines.append(
                f"- 目录→正文已闭合：目录 p{item['source_page']} "
                f"l{item['source_line']} {item['title']!r} → 声明页标签 "
                f"{item['declared_page_label']!r} → 物理页 {item['physical_page']} "
                f"l{item['landing_line']} → 节点 {item['node_id']} "
                f"path={item['node_path']}")
        for item in body_unassigned_toc[:40]:
            lines.append(
                f"- **正文 landing 未成为节点（`toc_body_unassigned`，阻断）**：目录 "
                f"p{item['source_page']} l{item['source_line']} {item['title']!r} → "
                f"物理页 {item['physical_page']} l{item['landing_line']} "
                f"{item['landing_text']!r}；来源身份 "
                f"{item.get('toc_source_id')!r}"
                f"（可重建={item.get('toc_source_rebuildable')}）；"
                f"该行候选拒绝原因="
                f"{item.get('landing_candidate_rejections')}；"
                f"可关联未归属 span="
                f"{item.get('unassigned_span_ids')}")
        for item in legacy_source_only[:40]:
            lines.append(
                f"- **legacy `toc_source_only`（同样阻断）**：目录 "
                f"p{item['source_page']} l{item['source_line']} {item['title']!r} → "
                f"物理页 {item['physical_page']} l{item['landing_line']} "
                f"{item['landing_text']!r} —— 该桶在 `tocr-1` 里被写成「不是漏收」，"
                f"是 fail-open，本产物必须按 `toc_body_unassigned` 重新解释并阻断")
        for item in unresolved[:40]:
            # "同名正文节点已进树"这件事已经写在 `why` 里（对账侧自己产出），此处不再
            # 重复拼接，避免同一条产物里出现两遍同样的话。
            lines.append(
                f"- 目录目标未解析（`toc_target_unresolved`）：目录 "
                f"p{item['source_page']} l{item['source_line']} {item['title']!r} "
                f"声明页标签 {item['declared_page_label']!r} —— {item['why']}")
        for item in nav_only[:40]:
            lines.append(
                f"- 书签仅作导航（`bookmark_navigation_only`）：声明页 "
                f"p{item['declared_page']} {item['title']!r} —— "
                f"不在 `PageLayout` 里：不参与标题资格、不穿透 `inside_table`、"
                f"不使复核侧 `available` 为真")
        for item in body_unassigned[:40]:
            lines.append(
                f"- 正文候选未采纳（`body_heading_unassigned`）：p{item['page_number']} "
                f"l{item['line_index']} {item['title']!r} "
                f"reason={item['reason_codes']}")
    for key in ("toc_unmatched", "bookmark_unmatched", "numbered_list_ambiguity"):
        for item in findings[key][:20]:
            lines.append(f"- 未归属（{key}）：p{item['page_number']} "
                         f"l{item['line_index']} {item['text']!r}")
    # 四种新增误收类的**逐条明细**（与上面的正文误收同粒度），便于人工直接定位。
    for key in ("global_peer_only_accepted", "unsafe_table_override_accepted",
                "fragmented_cell_accepted", "adjacent_heading_rejected",
                "accepted_without_intrinsic_primary_evidence"):
        for item in findings.get(key, ())[:20]:
            lines.append(f"- 独立验收失败（{key}）：p{item['page_number']} "
                         f"l{item['line_index']} {item.get('title')!r} "
                         f"reason={item.get('why') or item.get('reason')}")
    # §三：两侧结论分叉也必须**逐条**摊开（含复核侧自己重算出来的 landing 事实），
    # 否则人工只看到一个计数，无法判断分叉在哪一条上、往哪个方向。
    for key in ("safe_override_mismatch", "unsafe_override_mismatch",
                "source_landing_unverified", "toc_landing_unverified_accepted"):
        for item in findings.get(key, ())[:20]:
            lines.append(f"- 两侧分叉（{key}）：p{item.get('page_number')} "
                         f"l{item.get('line_index')} {item.get('title')!r} "
                         f"复核侧重算 landing={_landing_brief(item.get('landing'))} "
                         f"—— {item.get('why')}")
    # §三.4：落在表格**内部**却通过安全穿透资格被采纳的候选。**不计入失败**，但必须
    # **逐条**输出证据（来源页行 / 独立复算的几何事实 / 独立复算的强证据 / 是否截断
    # 片段 / 为什么它不是表格单元格），不能只给数量 —— 这正是旧
    # `table_region_strong_primary_accepted` 只留计数、把误收藏在 audit 项里的教训。
    safe_items = findings.get("safe_table_override_accepted") or []
    if safe_items or findings.get("inside_table_accepted"):
        lines += ["", "审计项（**不计入失败**，但必须逐条展示证据）："
                      "表格内部的已采纳节点 / 安全穿透清单", ""]
        lines.append(f"- `inside_table_accepted` = "
                     f"{findings.get('inside_table_accepted')}；"
                     f"`safe_table_override_accepted` = {len(safe_items)}；"
                     f"`unsafe_table_override_accepted` = "
                     f"{len(findings.get('unsafe_table_override_accepted') or ())}")
        for item in safe_items:
            lines.append(
                f"- 安全穿透：p{item['page_number']} l{item['line_index']} "
                f"{item.get('title')!r} node={item.get('node_id')} "
                f"独立重算几何={item.get('geometry')} "
                f"**独立重算的对象级来源 landing**={_landing_brief(item.get('landing'))} "
                f"—— {item.get('why')}")
    if not gate_problems:
        lines.append("- （本门未发现同级误嵌套 / 正文误收 / 五类独立误收（含表格成员"
                     "不安全穿透、邻近表格误拒）/ 采纳丢节点；待人工复核候选与目录书签"
                     "未归属详见第 11 节计数）")
    lines.append("")
    lines += ["## 12. 仍需人工检查的真实问题", ""]
    problems = []
    # 失败项由**正式校验检查单**派生（与 manifest 的 `failed_checks` 同源同算法），
    # 不从别处"再算一遍"，避免两处口径漂移。
    failed_checks = [c for c in validation["checks"] if not c.startswith("PASS ")]
    if failed_checks:
        problems += [f"结构校验失败：{c}" for c in failed_checks]
    if stats["numbering_only_accepted"]:
        problems.append(
            f"仍有 {stats['numbering_only_accepted']} 条候选仅靠编号被接受（不合规）")
    probe = validation["tamper_probe"]
    if not probe["all_rejected"]:
        problems.append("篡改探针并非全部被拒（身份闭合存疑）")
    unassigned_probe = probe.get("unassigned_identity_probes") or []
    if unassigned_probe and not probe.get("unassigned_identity_closed"):
        problems.append("正式 unassigned 篡改既未读回 fail-closed 也未改变指纹")
    if alignment_doc["block_count"] and len(alignment_doc["terminals_missing"]):
        problems.append(
            f"{len(alignment_doc['terminals_missing'])} 个块缺正式终态（逐步守恒不成立）")
    for level, count in levels.items():
        if int(level) > 4 and count:
            problems.append(f"出现 L{level} 及更深层级（{count} 个），需人工确认")
    if not four_state["conserved"]:
        problems.append(
            f"四态守恒不成立：四态之和 {four_state['four_state_sum']} / 逐行记录 "
            f"{four_state['total_lines']} / 非家具行 {four_state['non_furniture_lines']}")
    # §六(5)：存在同级误嵌套 / 正文误收 / 待人工复核候选 / 目录来源未落地 / 目录目标未解析
    # 时，**不得**写"自动检查无失败"。
    problems += gate_problems
    if not problems:
        problems.append("（自动检查未发现失败项；标题树的**业务可用性**仍需人工判读）")
    lines += [f"- {p}" for p in problems]
    lines += ["", "> 本文件由只读评测脚本生成；它不宣布 TS3 关闭，也不代替人工判读。", ""]
    return "\n".join(lines)


def build_one(run_dir: pathlib.Path, *, company_id: str, document_id: str,
              pdf_path: pathlib.Path, label: str,
              layout=None, evidence_blocks=(), baseline_dist=None,
              snapshot=None) -> dict:
    """构建单份文档的 `PageLayout` → `DocumentOutline` 与全部审计产物。

    `snapshot`：§八 的**权威证据集快照**（受信任只读 gateway 注入）。有块时必填。
    """
    doc_dir = run_dir / document_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    if layout is None:
        layout = LB.build_page_layout(
            pdf_path, LB.LayoutBuildContext(company_id=company_id,
                                            document_id=document_id))
    (doc_dir / "page_layout.json").write_text(
        canonical_json(layout.to_dict()), encoding="utf-8")

    context = OB.load_source_context(pdf_path, layout, company_id=company_id,
                                     document_id=document_id)
    result = OB.build_document_outline(context)

    (doc_dir / "document_outline.json").write_text(
        json.dumps(result.outline.to_dict(), ensure_ascii=False, indent=1),
        encoding="utf-8")
    (doc_dir / "outline_tree.md").write_text(
        OB.outline_markdown(result, f"{document_id} 标题树（{label}）"),
        encoding="utf-8")
    (doc_dir / "outline_candidate_audit.json").write_text(
        json.dumps({"manual_review_required": True,
                    "candidate_count": len(result.candidates),
                    "accepted_count": sum(1 for c in result.candidates if c.accepted),
                    "items": [c.to_dict() for c in result.candidates]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    # §六 P1-D：**正式**四态逐行结构归属。旧桶视图降级为内部记账 sidecar，文件名
    # 与字段名都显式标注"不是正式四态"，避免下游拿旧桶冒充四态。
    four_state = OB.line_structure_diagnostic(result, layout)
    (doc_dir / "line_structure_assignment.json").write_text(
        json.dumps(four_state, ensure_ascii=False, indent=1), encoding="utf-8")
    assignments = line_assignment_audit(result)
    (doc_dir / "line_assignment_buckets.json").write_text(
        json.dumps(assignments, ensure_ascii=False, indent=1), encoding="utf-8")
    findings = OB.structural_review_findings(result)
    (doc_dir / "structural_review_findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    (doc_dir / "unassigned_content.json").write_text(
        json.dumps(unassigned_content(result), ensure_ascii=False, indent=1),
        encoding="utf-8")
    # §五：`normalization_alignment.json` 是**正式 Evidence 对齐终态**（逐块），
    # `node_anchor_validation.json` 只校验节点标题 / bbox；两者不得互相顶替。
    alignment_doc = normalization_alignment(layout, list(evidence_blocks),
                                            snapshot=snapshot)
    (doc_dir / "normalization_alignment.json").write_text(
        json.dumps(alignment_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (doc_dir / "alignment_summary.json").write_text(
        json.dumps({"manual_review_required": True,
                    "artifact": "alignment_summary.json",
                    "document_id": document_id,
                    "page_layout_id": layout.page_layout_id,
                    "evidence_set_version": alignment_doc["evidence_set_version"],
                    "aligner_version": V.ALIGNER_VERSION,
                    "align_min": V.ALIGN_MIN,
                    "align_schema_version": V.ALIGN_SCHEMA_VERSION,
                    "align_refusal_schema_version":
                        V.ALIGN_REFUSAL_SCHEMA_VERSION,
                    "block_count": alignment_doc["block_count"],
                    "terminals_emitted": alignment_doc["terminals_emitted"],
                    "terminals_missing": alignment_doc["terminals_missing"],
                    # §八：正式终态所属**权威证据集身份**（含全成员指纹）。
                    "evidence_set_snapshot":
                        alignment_doc.get("evidence_set_snapshot"),
                    "member_identity_sha256":
                        alignment_doc.get("member_identity_sha256"),
                    "summary": alignment_doc["alignment_summary"],
                    "refusals": [{"page_number": r["page_number"],
                                  "block_index": r["block_index"],
                                  "evidence_block_id": r["evidence_block_id"],
                                  "refusal_id": (r["terminal"] or {}).get("refusal_id"),
                                  "matched_chars": r["matched_chars"],
                                  "block_char_length": r["block_char_length"],
                                  "exact_coverage": r["exact_coverage"],
                                  "quantized_coverage": r["quantized_coverage"],
                                  "refusal_reason": r["refusal_reason"]}
                                 for r in alignment_doc["rows"]
                                 if r["refusal_reason"]]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (doc_dir / "node_anchor_validation.json").write_text(
        json.dumps(node_anchor_validation(result, layout), ensure_ascii=False,
                   indent=1), encoding="utf-8")
    (doc_dir / "formal_unassigned.json").write_text(
        json.dumps(formal_unassigned(result), ensure_ascii=False, indent=1),
        encoding="utf-8")
    validation = structure_validation(result, layout, context)
    (doc_dir / "structure_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=1), encoding="utf-8")
    (doc_dir / "before_after.md").write_text(
        before_after(result, layout,
                     evidence_block_count=alignment_doc["block_count"]),
        encoding="utf-8")
    (doc_dir / "manual_review.md").write_text(
        manual_review(result, validation, alignment_doc, baseline_dist,
                      four_state, findings),
        encoding="utf-8")
    acceptance = heading_acceptance_stats(result)

    stats = {k: v for k, v in result.stats.items() if not k.startswith("_")}
    manifest = {
        "manual_review_required": True,
        "label": label,
        "company_id": company_id,
        "document_id": document_id,
        "input_path": str(pdf_path),
        "input_sha256": layout.source_file_sha256,
        "input_size": pdf_path.stat().st_size,
        "page_count": layout.page_count,
        "document_version": layout.document_version,
        "page_layout_id": layout.page_layout_id,
        "page_layout_locator": layout.page_layout_locator,
        "outline_id": result.outline.outline_id,
        "outline_locator": result.outline.outline_locator,
        "outline_builder_version": OUTLINE_BUILDER_VERSION,
        "algorithm_version": result.outline.algorithm_version,
        "schema_version": result.outline.schema_version,
        # §四：该文档的标题资格 profile 与表格区域资格版本，随文档 manifest 落盘 ——
        # 读产物的人必须能看出它是按哪一版资格语义构建的（legacy 载荷不得静默按新
        # 语义解释：`hq-3` 及更早的书签放行语义在 `hq-4` 已作废）。
        "heading_qualification_profile_version":
            V.HEADING_QUALIFICATION_PROFILE_VERSION,
        "table_region_qualification_version":
            V.TABLE_REGION_QUALIFICATION_VERSION,
        "candidate_sources": list(result.outline.candidate_sources),
        "stats": stats,
        "structure_validation_ok": validation["ok"],
        # 人工复核观察项：重复的完整结构路径 / 同页同名 / 同名跨父路径。它们是
        # "不猜父节点、不合并同名标题"的公开代价，不是失败项，但必须被看见。
        "duplicate_path_group_count": validation["duplicate_path_group_count"],
        "same_page_same_title_group_count":
            validation["same_page_same_title_group_count"],
        "cross_parent_same_title_count":
            validation["cross_parent_same_title_count"],
        # §三：正式 unassigned 的数量与身份（不是 sidecar 的计数）。
        "formal_unassigned_count": len(result.outline.unassigned),
        "outline_content_fingerprint": result.outline.content_fingerprint,
        # §五：本文档 Evidence 对齐终态守恒（0 条表示该文档没有 EvidenceBlock，
        # 绝不表示"对齐通过"）。
        "alignment": {
            "evidence_set_version": alignment_doc["evidence_set_version"],
            "block_count": alignment_doc["block_count"],
            "terminals_emitted": alignment_doc["terminals_emitted"],
            "terminals_missing_count": len(alignment_doc["terminals_missing"]),
            "terminal_schema_versions":
                alignment_doc["terminal_schema_versions"],
            "align_schema_version": V.ALIGN_SCHEMA_VERSION,
            "align_refusal_schema_version": V.ALIGN_REFUSAL_SCHEMA_VERSION,
            "aligner_version": V.ALIGNER_VERSION,
            "align_min": V.ALIGN_MIN,
        },
        "failed_checks": [c for c in validation["checks"]
                          if not c.startswith("PASS ")],
        "artifacts": sorted([
            "alignment_summary.json", "before_after.md", "document_outline.json",
            "formal_unassigned.json", "line_assignment_buckets.json",
            "line_structure_assignment.json", "manifest.json",
            "manual_review.md", "node_anchor_validation.json",
            "normalization_alignment.json", "outline_candidate_audit.json",
            "outline_tree.md", "page_layout.json",
            "structural_review_findings.json", "structure_validation.json",
            "unassigned_content.json"]),
        # §六 P1-D：正式四态与结构复核门随 manifest 一起落盘，使"某份文档是否
        # 存在同级误嵌套 / 正文误收 / 待人工复核候选 / 目录来源未落地"在 run 级也可一眼
        # 看出（§四 P1-B(7)：目录来源行与正文 landing 分开计数，不再合并成"真标题漏收"）。
        "line_structure": {
            "artifact": "line_structure_assignment.json",
            "states": list(OB.LINE_STRUCTURE_STATES),
            "counts": four_state["counts"],
            "total_lines": four_state["total_lines"],
            "non_furniture_lines": four_state["non_furniture_lines"],
            "conserved": four_state["conserved"],
            "four_state_sum": four_state["four_state_sum"],
        },
        "structural_review_findings": {
            "artifact": "structural_review_findings.json",
            "clean": findings["clean"],
            "counts": findings["counts"],
            # §三.4：表内采纳复核计数。`inside_table_accepted` 必须等于
            # `safe_table_override_accepted`；`unsafe_table_override_accepted` /
            # `adjacent_heading_rejected` / `fragmented_cell_accepted` 必须为 0。
            "review_counts": findings.get("review_counts", {}),
            # §三.4：留痕项（**不计入** clean）：落在表格**内部**、且通过安全穿透资格
            # 的已采纳节点。它们必须**逐条**给出证据（见该文档 `manual_review.md`
            # 第 11 节「结构复核门」），不能只留计数 —— 旧 `table_region_strong_primary_accepted`
            # 正是只留计数，把普通承诺正文藏进了"非阻断"位置。
            "audit_counts": findings.get("audit_counts", {}),
            "layout_available": findings.get("layout_available"),
            "verification_gaps": len(findings.get("verification_gaps", ())),
            # §四 P1-B(7)：目录**来源行**与**正文 landing** 的六类对账计数，连同它的
            # **状态机版本**一起落盘。读 manifest 的人不必打开
            # `structural_review_findings.json` 就能看出"目录页上的来源行"和"正文标题
            # 候选"各有多少、其中多少已闭合，以及这批桶是按哪版语义产生的。
            #
            # 桶按**显式清单**取，不 `for name, items in recon.items()` —— 后者会在桶
            # dict 出现非桶键时静默把非列表值当成桶（`len("tocr-2") == 6`）。
            # 两个键都取**持久化载荷自身**的值（不是从当前常量现取）—— 验收方要能
            # 拿这份 manifest 核对"该文档的 findings 是按哪版语义、哪些桶算阻断产生
            # 的"。缺失 ⇒ 空值，run 级据此 fail-closed。
            "toc_body_reconciliation_version":
                findings.get("toc_body_reconciliation_version"),
            "toc_body_reconciliation_blocking_buckets":
                list(findings.get("toc_body_reconciliation_blocking_buckets") or ()),
            "toc_body_reconciliation_counts": {
                name: len((findings.get("toc_body_reconciliation") or {})
                          .get(name) or ())
                for name in OB.TOC_BODY_RECONCILIATION_BUCKETS
            },
        },
    }
    (doc_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"manifest": manifest, "result": result, "validation": validation,
            "layout": layout, "alignment": alignment_doc,
            "acceptance": acceptance, "four_state": four_state,
            "findings": findings}


# ---------------------------------------------------------------------------
# 与 TS2 冻结基线的逐块对账（交付 A 的等价性证明）
# ---------------------------------------------------------------------------

#: TS2 冻结基线的块数（三份真实 PDF 的 EvidenceBlock 总数，§一 既定输入）。
TS2_BASELINE_BLOCK_COUNT = 769

#: §六：TS2 基线是**冻结资产**，按**精确目录名 + 关键文件 SHA256**固定绑定。
#: 不得 glob 后取 latest / 字典序最大 / mtime 最新 / "第一个可解析目录" —— 那些
#: 规则会让"后来re-run 出的任意一份更晚目录"顶替已验收基线，等价于让基线可被
#: 事后重写。基线只服务 evaluation，**不进生产 runtime**。
TS2_BASELINE_RUN_DIR = "tree_structure_ts2_layout_ts2_final_20260917T170000Z"

#: 冻结基线里三个关键文件的 SHA256（不可变身份）。任一缺失 / 哈希不符即 fail-closed。
TS2_BASELINE_FILES = {
    "coverage_blocks.json":
        "a6528ff6c12203683eba1d21b4482e7e2f5451f6407f08ef647d3407f0fa09af",
    "run_manifest.json":
        "1c8ae720437b1bed090003d9f1407b41a0c1f00bf3d0bf4d524919ceb62857ea",
    "coverage_summary.json":
        "60a0b5fb8d3b0ecd68c20d8606be03ba236dea2371ee259eeea8db354d8bca10",
}

#: 逐块对账**要比较**的字段全集（身份 / 判定 / 覆盖率 / 残差 / char_map /
#: 可引用性）。实际比较哪些，由冻结产物**真正存了哪些**决定（见
#: `_comparable_fields`）：冻结 TS2 产物没写的字段不能拿"有值 对 缺键"当差异。
_EQUIVALENCE_FIELDS = (
    "evidence_id", "content_hash", "page_number", "block_index",
    "document_version", "evidence_set_version", "alignment_id",
    "verdict_now", "is_citable_now", "record_refused", "record_refusal_reason",
    "coverage", "matched_chars", "unmatched_chars", "normalized_block_len",
    "residue_classes", "residue_subkinds", "residue_class",
    "verdict_residue_class", "has_unexplained", "unexplained_chars",
    "unexplained_duplicate_chars", "unexplained_search_exhausted_chars",
    "char_map_segments", "page_layout_id",
)

#: TS3 才引入的**正式记录**字段：`alignment_id` / `char_map_segments` /
#: 量化边界拒发（`record_refused` / `record_refusal_reason`）。TS2 冻结产物与冻结
#: 实现都不产出它们（TS3 把它们从 TS2 的内联算法提为唯一生产实现时才成为正式
#: 记录字段），因此它们**无法**用"与冻结产物对账"证明，只能用 TS3 侧自洽核验
#: （`_ts3_record_consistency`）证明。字段是否真的缺席由产物本身判定，不靠本注释。
_TS3_RECORD_FIELDS = ("alignment_id", "char_map_segments",
                      "record_refused", "record_refusal_reason")


class FrozenBaselineError(RuntimeError):
    """冻结 TS2 基线不可用（缺失 / 哈希不符 / 身份不符）——一律 fail-closed。"""


def ts2_baseline_identity() -> dict:
    """冻结基线的**不可变身份**：目录名 + 关键文件 SHA256 + 块数（不读内容）。

    身份只有**一份定义**（`BaselineSpec.identity()`）：这里不再另写一份字典，否则
    "加载路径用的 spec"与"身份函数报的身份"会有两条可漂移的口径。
    """
    return ts2_baseline_spec().identity()


@dataclass(frozen=True)
class BaselineSpec:
    """一份**不可变**基线的绑定：精确目录名 + 关键文件 SHA256 + 块数 + 根目录。

    §十 要求"注册到 `run_evals` 的确定性测试必须在干净 checkout 可运行"，而真实的
    769 条 TS2 产物**不进 Git**。因此绑定语义被抽成一个可复用的 spec：真实 acceptance
    用 `TS2_BASELINE_SPEC`（本地真实产物，缺失即 fail-closed），确定性测试用
    `CANONICAL_BASELINE_SPEC`（受 Git 跟踪的极小 canonical fixture）。**两者走同一份
    加载与核验代码**，所以测试证明的正是真实路径上的绑定语义。
    """

    name: str
    root: pathlib.Path
    run_dir: str
    files: tuple            # ((相对文件名, sha256), ...)
    block_count: int
    scope: str

    def identity(self) -> dict:
        return {
            "name": self.name,
            "run_dir": self.run_dir,
            "files": dict(sorted(self.files)),
            "block_count": self.block_count,
            "selection_rule": ("按精确目录名 + 关键文件 SHA256 固定绑定；"
                               "不 glob、不看 mtime、不取 latest / 字典序最大"),
            "scope": self.scope,
        }


def load_baseline_spec(spec: BaselineSpec, root: pathlib.Path | None = None) -> tuple:
    """按 spec 读取**冻结绑定**的基线（只读；任何不符即 `FrozenBaselineError`）。

    这条路径**不扫描目录**：目录名写死、关键文件 SHA256 与块数逐项核对。因此后来
    新增的任意"更晚目录"都无法改变基线（反例见 `evals/test_tree_outline_baseline.py`）。
    """
    base = (root if root is not None else spec.root) / spec.run_dir
    if not base.is_dir():
        raise FrozenBaselineError(f"冻结基线目录缺失：{base}")
    digests = {}
    for name, expected in sorted(dict(spec.files).items()):
        path = base / name
        if not path.is_file():
            raise FrozenBaselineError(f"冻结基线缺关键文件：{path}")
        actual = sha256_file(path)
        digests[name] = {"expected_sha256": expected, "actual_sha256": actual,
                         "match": actual == expected}
        if actual != expected:
            raise FrozenBaselineError(
                f"冻结基线文件哈希不符：{path} 期望 {expected} 实得 {actual}")
    blocks_path = base / "coverage_blocks.json"
    records = json.loads(blocks_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise FrozenBaselineError(f"冻结基线 {blocks_path} 不是记录数组")
    if len(records) != spec.block_count:
        raise FrozenBaselineError(
            f"冻结基线块数不符：期望 {spec.block_count} 实得 {len(records)}")
    return blocks_path, records, {"identity": spec.identity(), "digests": digests}


def ts2_baseline_spec() -> BaselineSpec:
    """由**当下**的冻结常量构造真实 TS2 基线 spec。

    每次都从 `TS2_BASELINE_RUN_DIR` / `TS2_BASELINE_FILES` / `TS2_BASELINE_BLOCK_COUNT`
    与 `RA.RESULTS_ROOT` 现取，因此把三者之一改错（或把它 patched 到临时 root）都能被
    观察 —— 这正是反例测试需要单独触达"块数不符"分支的原因。
    """
    return BaselineSpec(
        name="ts2_frozen_real", root=RA.RESULTS_ROOT,
        run_dir=TS2_BASELINE_RUN_DIR,
        files=tuple(sorted(TS2_BASELINE_FILES.items())),
        block_count=TS2_BASELINE_BLOCK_COUNT,
        scope="evaluation-only（不得进入生产 runtime）",
    )


#: §十：**受 Git 跟踪**的 canonical 基线 fixture 根目录与它的清单文件。
#: 清单里的 SHA256 是"这一份 fixture"的事实，只用来让确定性测试在干净 checkout 下
#: 证明绑定语义；它**不是** TS2 基线的替代品，也不参与任何真实文档对账。
CANONICAL_BASELINE_ROOT = REPO_ROOT / "evals" / "fixtures" / "canonical_baseline"
CANONICAL_BASELINE_MANIFEST = CANONICAL_BASELINE_ROOT / "manifest.json"


def canonical_baseline_spec(manifest_path: pathlib.Path | None = None) -> BaselineSpec:
    """从**受 Git 跟踪**的 canonical 清单构造 spec（干净 checkout 下必然可用）。"""
    path = manifest_path or CANONICAL_BASELINE_MANIFEST
    if not path.is_file():
        raise FrozenBaselineError(f"canonical 基线清单缺失：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise FrozenBaselineError(f"canonical 清单 {path} 没有 files 哈希表")
    return BaselineSpec(
        name="canonical_tracked_fixture", root=path.parent,
        run_dir=payload["run_dir"],
        files=tuple(sorted(files.items())),
        block_count=int(payload["block_count"]),
        scope="evaluation-only deterministic fixture（受 Git 跟踪；不参与真实对账）",
    )


def _load_baseline() -> tuple:
    """读取**真实** TS2 冻结基线（本地完整产物，缺失 / 哈希不符即 fail-closed）。"""
    return load_baseline_spec(ts2_baseline_spec())


def _load_canonical_baseline() -> tuple:
    """读取**受 Git 跟踪**的 canonical 基线（确定性测试 & 干净 checkout 用）。"""
    return load_baseline_spec(canonical_baseline_spec())


def _ts3_record_consistency(real_layouts: dict, rows: list, snapshots: dict) -> dict:
    """TS3 正式记录的**自洽核验**（冻结产物里没有这些字段，不能靠对账证明）。

    入口是**集合级**的生产门 `align_evidence_set`，因此身份闭合、跨公司 / 跨文档 /
    跨版本拒绝、同 set 一致、同一 (页, 块) 不得重复消费、以及"每块恰好一个正式
    终态"都在生产核心里先 fail-closed；本函数只在其之上做逐块复核：

    1. 判定与可引用性**双方都不能自报**：块与记录两侧的 `verdict` / `is_citable`
       都必须与**用冻结的 `compute_alignment_verdict` 独立重算**的结果一致。重算
       的输入是记录语义实际使用的那一个 coverage（`quantize(exact)`），因此这条
       核验同时也是"TS3 没有另立判定表"的证明；
    2. `char_map` 自洽：段区间升序不重叠、落在块内、页码等于记录页码，且
       `sum(段长) == matched_chars`；记录里的 char_map 与生产核心算出的**同一份**；
    3. **量化边界拒绝终态**（`versions.ALIGN_REFUSAL_SCHEMA_VERSION`）必须真的成立：
       `exact < ALIGN_MIN <= quantize(exact)`（即量化语义本会说 `aligned`，而未量化的
       真实覆盖率低于阈值），因此该块 verdict 为 `unaligned`、不可引用、且**没有**
       正式对齐记录。这是"宁可如实拒绝，也不产出与冻结业务规则分叉的记录"；
    4. 其余块**有**当前 wire 版本（`versions.ALIGN_SCHEMA_VERSION`）的正式记录，且
       `alignment_id` 非空、`aligner_version` 等于 `versions.ALIGNER_VERSION`、
       `matched_chars` 与分母同时在场（精确比值可复算）。

    本函数**不写任何版本字面量**：所有版本断言都读 `document_structure.versions`
    的当前值（§二.5）。历史版本只出现在兼容性测试与 `LEGACY_*` 登记表里。
    """
    by_doc: dict = {}
    order: list = []
    for company_id, document_id, version, row in rows:
        if document_id not in by_doc:
            by_doc[document_id] = []
            order.append(document_id)
        by_doc[document_id].append((company_id, document_id, version, row))

    mismatches = []
    refused_detail = []
    checked = 0
    map_segments = 0
    sets = []
    for document_id in order:
        layout = real_layouts[document_id]
        group = by_doc[document_id]
        versions = {row["evidence_set_version"] for _, _, _, row in group}
        if len(versions) != 1:
            mismatches.append({"document_id": document_id, "problems": [
                f"同一文档混用多个 evidence_set_version：{sorted(versions)}"]})
            continue
        declared = sorted(versions)[0]
        snapshot = snapshots.get(document_id)
        if not isinstance(snapshot, A.EvidenceSetSnapshot):
            mismatches.append({"document_id": document_id, "problems": [
                "缺少权威 EvidenceSetSnapshot（受信任只读 gateway 未注入）；"
                "提交清单自身自洽不得作为证据集身份（§八 fail-closed）"]})
            continue
        blocks = [RA.evidence_block_input(c, d, v, r) for c, d, v, r in group]
        # 集合级生产门：身份 / 绑定 / 重复位置 / set 一致 / 终态守恒 /
        # **提交成员与权威快照精确相等**。
        aligned = A.align_evidence_set(layout, blocks, snapshot=snapshot)
        sets.append({
            "document_id": document_id, "evidence_set_version": declared,
            "page_layout_id": aligned.page_layout_id,
            "aligner_version": aligned.aligner_version,
            "blocks": len(aligned.blocks),
            "evidence_set_snapshot": aligned.snapshot_identity,
            "member_identity_sha256": _member_identity_sha256(snapshot.members),
            "summary": aligned.summary,
        })
        rows_by_key = {(r["page_number"], r["block_index"]): r
                       for _, _, _, r in group}
        for block in aligned.blocks:
            row = rows_by_key[(block.page_number, block.block_index)]
            checked += 1
            map_segments += len(block.char_map)
            where = {"document_id": document_id,
                     "page_number": row["page_number"],
                     "block_index": row["block_index"],
                     "evidence_id": row["evidence_id"]}
            problems = []

            # 0) 终态守恒：每个块恰好一个正式终态（记录 或 拒绝记录）。
            if block.record is not None and block.refusal_record is not None:
                problems.append("同时存在记录与拒绝记录（终态不唯一）")
            if block.terminal is None:
                problems.append("既没有正式记录也没有拒绝记录（静默丢失）")

            # 1) 判定 / 可引用性：块侧、记录侧 与 冻结公式独立重算 必须一致。
            #    重算输入与记录语义同一个 coverage（量化后）。
            quantized = A.quantize(block.exact_coverage)
            frozen = S.compute_alignment_verdict(
                quantized, block.verdict_residue_class, V.ALIGN_MIN)
            expected_citable = (V.ALIGN_MIN is not None and frozen == "aligned")
            if block.refusal_record is None:
                if block.verdict != frozen:
                    problems.append(
                        f"verdict {block.verdict!r} != 冻结重算 {frozen!r}")
                if bool(block.is_citable) != expected_citable:
                    problems.append(
                        f"is_citable {block.is_citable!r} != {expected_citable!r}")
            else:
                # 拒绝终态的 verdict 由 fail-closed 规则给定（正是为了不与"记录
                # 语义会说 aligned"分叉）；理由在第 3 条核验。
                if frozen != "aligned":
                    problems.append("被拒发的块在记录语义下并非 aligned，理由不成立")
            if block.record is not None:
                record = block.record
                if record.schema_version != V.ALIGN_SCHEMA_VERSION:
                    problems.append(
                        f"记录 schema_version={record.schema_version!r}")
                if record.verdict != frozen:
                    problems.append("正式记录 verdict 与冻结重算不一致")
                if record.is_citable() != expected_citable:
                    problems.append("正式记录 is_citable() 与冻结重算不一致")
                # §九：aligner 版本必须与**当前**权威版本一致。这里读 `versions.py`
                # 这一个来源，不得写第二份字面量 —— 否则版本升级后核验会拿旧版本号
                # 通过（或像本轮早期那样把新版本号判成失败），正式 terminal ID 就
                # 分不清"旧自报输入"与"新权威 Snapshot 输入"。
                if record.aligner_version != V.ALIGNER_VERSION:
                    problems.append(
                        f"记录 aligner_version={record.aligner_version!r} != "
                        f"当前权威 {V.ALIGNER_VERSION!r}（旧版本记录必须显式拒绝，"
                        f"不得静默当作本轮产物）")
                if V.classify_schema_version("ALIGNER_VERSION",
                                             record.aligner_version) == "legacy":
                    problems.append(
                        f"记录 aligner_version={record.aligner_version!r} 是旧版本："
                        f"只能只读兼容或显式拒绝，不得在 oa-2 / als-3 语义下复用")
                if not record.alignment_id:
                    problems.append("正式记录缺 alignment_id")
                # 精确比值必须可复算：matched_chars / block_char_length
                if record.matched_chars != block.matched_chars:
                    problems.append("记录 matched_chars 与生产核心不一致")
                if record.block_char_length != block.block_char_length:
                    problems.append("记录 block_char_length 与生产核心不一致")
                if record.exact_ratio() != (block.matched_chars,
                                            block.block_char_length):
                    problems.append("记录精确比值与 (matched, length) 不可复算")
                # 2) char_map 自洽
                if tuple(record.char_map) != tuple(block.char_map):
                    problems.append("记录 char_map 与生产核心算出的不是同一份")
                total = 0
                cursor = -1
                for seg in block.char_map:
                    if len(seg) != 6:
                        problems.append("char_map 段不是 6 元组")
                        break
                    if seg[0] < cursor or seg[1] <= seg[0] \
                            or seg[1] > block.block_char_length \
                            or seg[2] != row["page_number"]:
                        problems.append(f"char_map 段不自洽：{seg}")
                        break
                    cursor = seg[1]
                    total += seg[1] - seg[0]
                if total != block.matched_chars:
                    problems.append(f"char_map 覆盖 {total} != matched_chars "
                                    f"{block.matched_chars}")
            # 3) 拒绝终态：边界算术必须真的成立（fail-closed 的唯一情形）
            if block.refusal_record is not None:
                refusal = block.refusal_record
                if block.record is not None:
                    problems.append("拒绝的块却产出了正式记录")
                if not (block.exact_coverage < V.ALIGN_MIN <= quantized):
                    problems.append("拒绝理由不是量化边界算术"
                                    f"（exact={block.exact_coverage}, "
                                    f"quantized={quantized}）")
                if block.verdict != "unaligned" or block.is_citable:
                    problems.append("被拒的块必须 unaligned 且不可引用")
                if refusal.schema_version != V.ALIGN_REFUSAL_SCHEMA_VERSION:
                    problems.append(f"拒绝记录 schema_version={refusal.schema_version!r}")
                if refusal.refusal_reason not in S.ALIGNMENT_REFUSAL_REASONS:
                    problems.append(f"拒绝原因码未登记：{refusal.refusal_reason!r}")
                if refusal.exact_ratio() != (refusal.matched_chars,
                                             refusal.block_char_length):
                    problems.append("拒绝记录精确比值不可复算")
                if refusal.is_citable():
                    problems.append("拒绝记录不得可引用")
                # locator / 身份由**外部**重新派生一次（不采信记录自报）。
                expected_loc = S.derive_alignment_refusal_locator(
                    page_layout_id=refusal.page_layout_id,
                    evidence_set_version=refusal.evidence_set_version,
                    aligner_version=refusal.aligner_version,
                    page_number=refusal.page_number,
                    block_index=refusal.block_index)
                if refusal.alignment_locator != expected_loc:
                    problems.append("拒绝记录 locator 不可外部复算")
                expected_refusal_id = S.derive_alignment_refusal_id(
                    alignment_locator=refusal.alignment_locator,
                    schema_version=refusal.schema_version,
                    evidence_block_id=refusal.evidence_block_id,
                    block_char_length=refusal.block_char_length,
                    matched_chars=refusal.matched_chars,
                    coverage=refusal.coverage,
                    residue_class=refusal.residue_class,
                    residue=refusal.residue, char_map=refusal.char_map,
                    refusal_reason=refusal.refusal_reason)
                if refusal.refusal_id != expected_refusal_id:
                    problems.append("拒绝记录 refusal_id 不可外部复算")
                if refusal.coverage != A.quantize(refusal.exact_coverage):
                    problems.append("拒绝记录 coverage 不是精确比值的量化展示值")
                if refusal.evidence_block_id != block.evidence_block_id \
                        or refusal.page_layout_id != block.page_layout_id:
                    problems.append("拒绝记录身份与块 / 版式不一致")
                refused_detail.append({
                    **where, "refusal_id": refusal.refusal_id,
                    "exact_coverage": refusal.exact_coverage,
                    "quantized_coverage": quantized,
                    "matched_chars": refusal.matched_chars,
                    "block_char_length": refusal.block_char_length,
                    "record_refusal_reason": refusal.refusal_reason,
                    "verdict": block.verdict,
                    "is_citable": bool(block.is_citable)})

            if problems:
                mismatches.append({**where, "problems": problems})
    return {
        "manual_review_required": True,
        "blocks_checked": checked,
        "char_map_segments_total": map_segments,
        "documents": sets,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:20],
        "ok": not mismatches,
        "refusals": refused_detail,
    }


def _comparable_fields(previous: list) -> tuple:
    """按冻结产物**真正存了哪些键**划出可比字段与不可比字段。"""
    present = set()
    for rec in previous:
        if isinstance(rec, dict):
            present |= set(rec)
    comparable = [f for f in _EQUIVALENCE_FIELDS if f in present]
    unavailable = [{"field": f, "reason": (
        "TS3 才引入的正式记录字段：冻结 TS2 产物与冻结实现都不产出它，"
        "改由 _ts3_record_consistency 自洽核验，不参与逐块对账")
        if f in _TS3_RECORD_FIELDS else "冻结产物未存该字段"}
        for f in _EQUIVALENCE_FIELDS if f not in present]
    return comparable, unavailable


def ts2_alignment_equivalence(real_layouts: dict, baseline=None,
                              frozen: dict | None = None,
                              snapshots: dict | None = None) -> dict:
    """用**唯一生产对齐核心**重算全部块，与 TS2 最终基线逐块对账。

    逐块比较冻结产物**确实存有**的字段：Evidence 身份、`verdict`、`coverage`、
    `residue` / `residue_class`、`is_citable` 等；任何逐块差异都逐条列出（不能只
    比总数）。TS3 才引入的正式记录字段（`alignment_id` / char_map / 拒发）单独
    用 `_ts3_record_consistency` 做自洽核验，并在 `unavailable_fields` 里列明
    它们为什么不能参与对账。
    """
    if baseline is None:
        baseline_dir, baseline, frozen = _load_baseline()
    else:
        baseline_dir = RA.RESULTS_ROOT / TS2_BASELINE_RUN_DIR
        if frozen is None:
            frozen = {"identity": ts2_baseline_identity(), "digests": {}}

    rows = RA.load_evidence(real_layouts)
    records = RA.diagnose(real_layouts, rows)

    def key(rec: dict) -> tuple:
        return (rec["document_id"], rec["page_number"], rec["block_index"],
                rec["evidence_id"])

    current = {key(r): r for r in records}
    previous = {key(r): r for r in baseline}
    only_current = sorted(set(current) - set(previous))
    only_previous = sorted(set(previous) - set(current))
    comparable, unavailable = _comparable_fields(baseline)
    diffs = []
    for k in sorted(set(current) & set(previous)):
        cur, prev = current[k], previous[k]
        fields = [f for f in comparable
                  if canonical_json(cur.get(f)) != canonical_json(prev.get(f))]
        if fields:
            diffs.append({
                "block": {"document_id": k[0], "page_number": k[1],
                          "block_index": k[2], "evidence_id": k[3]},
                "fields": fields,
                "current": {f: cur.get(f) for f in fields},
                "baseline": {f: prev.get(f) for f in fields},
            })

    verdicts = {name: sum(1 for r in records if r["verdict_now"] == name)
                for name in S.ALIGNMENT_VERDICTS}
    citable = sum(1 for r in records if r["is_citable_now"])
    refused = [{"document_id": r["document_id"], "page_number": r["page_number"],
                "block_index": r["block_index"], "evidence_id": r["evidence_id"],
                "exact_coverage": r["coverage"],
                "record_refusal_reason": r["record_refusal_reason"],
                "verdict_now": r["verdict_now"], "is_citable_now": r["is_citable_now"]}
               for r in records if r["record_refused"]]
    consistency = _ts3_record_consistency(real_layouts, rows, snapshots or {})
    return {
        "manual_review_required": True,
        "comparable": True,
        "baseline_run_dir": str(baseline_dir),
        "baseline_frozen_identity": frozen,
        "aligner_version": V.ALIGNER_VERSION,
        "align_min": V.ALIGN_MIN,
        "block_count": len(records),
        "baseline_block_count": len(baseline),
        "only_in_current": [list(k) for k in only_current],
        "only_in_baseline": [list(k) for k in only_previous],
        "differing_block_count": len(diffs),
        "differing_blocks": diffs,
        "differing_field_histogram": {
            f: sum(1 for d in diffs if f in d["fields"]) for f in comparable
            if any(f in d["fields"] for d in diffs)},
        "identical": (not diffs and not only_current and not only_previous
                      and len(records) == len(baseline)),
        "verdict_counts": verdicts,
        "citable_blocks": citable,
        "record_refusals": refused,
        "expected_three_state": {"aligned": 522, "partially_aligned": 70,
                                 "unaligned": 177},
        "three_state_matches_ts2": (verdicts["aligned"] == 522
                                    and verdicts["partially_aligned"] == 70
                                    and verdicts["unaligned"] == 177),
        "compared_fields": comparable,
        "unavailable_fields": unavailable,
        "ts3_record_consistency": consistency,
        "note": ("TS3 只把 TS2 已验收的对齐算法提取为唯一生产实现；阈值、判定与"
                 "残差白名单一律不变，因此在冻结产物**存有的**字段上应当逐块零差异。"
                 "TS3 新增的正式记录字段（alignment_id / char_map / 量化边界拒发）"
                 "在冻结产物里根本不存在，不能拿「有值 对 缺键」算差异，改由 "
                 "ts3_record_consistency 证明其自洽。"),
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def evidence_conservation(documents, evidence_rows, baseline_rows) -> dict:
    """§五 的 **769 条守恒**：DB 块数 = 冻结基线块数 = 本轮正式终态数。

    三份真实文档逐份核对，再给合计；任何一处不等即 `conserved: false`。合成
    fixture 不在这个守恒里（它没有 EvidenceBlock），必须显式写明，不得混进合计。
    """
    db_counts: dict = {}
    for _, document_id, _, _ in evidence_rows:
        db_counts[document_id] = db_counts.get(document_id, 0) + 1
    frozen_counts: dict = {}
    for row in baseline_rows:
        frozen_counts[row["document_id"]] = \
            frozen_counts.get(row["document_id"], 0) + 1
    by_id = {b["manifest"]["document_id"]: b for b in documents}
    rows = []
    for _, document_id, _ in REAL_DOCUMENTS:
        alignment = by_id[document_id]["alignment"]
        db_blocks = db_counts.get(document_id, 0)
        frozen_blocks = frozen_counts.get(document_id, 0)
        missing = list(alignment["terminals_missing"])
        rows.append({
            "document_id": document_id,
            "db_blocks": db_blocks,
            "frozen_baseline_blocks": frozen_blocks,
            "formal_terminals": alignment["terminals_emitted"],
            "terminals_missing": len(missing),
            "terminals_missing_rows": missing,
            "evidence_set_version": alignment["evidence_set_version"],
            "terminal_schema_versions": alignment["terminal_schema_versions"],
            "conserved": (db_blocks == frozen_blocks
                          == alignment["terminals_emitted"]
                          and not missing),
        })
    total_db = sum(r["db_blocks"] for r in rows)
    total_frozen = sum(r["frozen_baseline_blocks"] for r in rows)
    total_terminals = sum(r["formal_terminals"] for r in rows)
    total_missing = sum(r["terminals_missing"] for r in rows)
    return {
        "manual_review_required": True,
        "artifact": "evidence_conservation.json",
        "scope": "三份真实文档的全部 EvidenceBlock（合成 fixture 不在其中）",
        "baseline_run_dir": TS2_BASELINE_RUN_DIR,
        "baseline_block_count": TS2_BASELINE_BLOCK_COUNT,
        "documents": rows,
        "total_db_blocks": total_db,
        "total_frozen_blocks": total_frozen,
        "total_formal_terminals": total_terminals,
        "total_terminals_missing": total_missing,
        "conserved": (total_db == total_frozen == total_terminals
                      == TS2_BASELINE_BLOCK_COUNT
                      and total_missing == 0
                      and all(r["conserved"] for r in rows)),
        "fixture_note": ("非 300750 合成 fixture 在 Evidence DB 里没有块，因此它的"
                         "正式对齐终态为 0 条；0 条是如实记录，不是「对齐通过」，"
                         "也不计入 769。"),
    }


def toc_reconciliation_rollup(documents) -> dict:
    """目录来源 / 正文 landing 对账的**轮级汇总 + 版本门**（单一来源）。

    run manifest 与顶层 `manual_review.md` 都消费本函数的返回值，因此两处的
    "版本 / 阻断桶 / 六桶计数"不可能各算一套。

    版本与阻断桶清单的**权威值**只来自 `versions.py` 与 `outline_builder.py`
    （本脚本不写 `"tocr-2"`、也不写桶名字面量）。每份文档的
    `structural_review_findings.json` 必须**自带**这两个键，且与权威值逐项相等；
    任一文档缺失 / 版本不同 / 阻断桶不同 ⇒ 本函数产出阻断问题，run 级生成与验收
    fail-closed（不得因为"六桶皆空"就放行一份按旧语义产生的载荷）。
    """
    authoritative_version = V.TOC_BODY_RECONCILIATION_VERSION
    authoritative_blocking = list(OB.TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS)
    counts: dict = {}
    per_document: list = []
    problems: list = []
    doc_versions: dict = {}
    doc_blocking: dict = {}
    for built in documents:
        m = built["manifest"]
        findings = built["findings"]
        doc_id = m["document_id"]
        declared_version = findings.get("toc_body_reconciliation_version")
        declared_blocking = list(
            findings.get("toc_body_reconciliation_blocking_buckets") or ())
        doc_versions[doc_id] = declared_version
        doc_blocking[doc_id] = declared_blocking
        recon = findings.get("toc_body_reconciliation") or {}
        doc_counts = {name: len(recon.get(name) or ())
                      for name in OB.TOC_BODY_RECONCILIATION_BUCKETS}
        for name, n in doc_counts.items():
            if n:
                counts[name] = counts.get(name, 0) + n
        # legacy 载荷里的同义桶（`tocr-1` 的 `toc_source_only`）：改名不变语义，
        # 非空即同样阻断，单独计数以便总览逐类点名。
        legacy_n = len(recon.get("toc_source_only") or ())
        if legacy_n:
            doc_counts["legacy_toc_source_only"] = legacy_n
            counts["legacy_toc_source_only"] = (
                counts.get("legacy_toc_source_only", 0) + legacy_n)
        # §四 P1-B(2)：`toc_target_unresolved` 里有多少条的**正文标题其实已经进树**。
        # 这个数必须单独带出来：否则总览只说"目标未解析 N 条"，读的人会把它读成
        # 「N 个正文标题漏收」（本轮真实文档实测出现过）。
        same_n = sum(1 for i in (recon.get("toc_target_unresolved") or ())
                     if i.get("same_title_node_ids"))
        if same_n:
            doc_counts["toc_target_unresolved_with_existing_node"] = same_n
            counts["toc_target_unresolved_with_existing_node"] = (
                counts.get("toc_target_unresolved_with_existing_node", 0) + same_n)
        per_document.append({
            "document_id": doc_id,
            "declared_version": declared_version,
            "declared_blocking_buckets": declared_blocking,
            "counts": doc_counts,
        })
        if declared_version is None:
            problems.append(
                f"{doc_id}：`structural_review_findings.json` **缺少** "
                f"`toc_body_reconciliation_version` —— 无法判定这批桶按哪版语义"
                f"产生（legacy 载荷），必须显式重算后重新验收")
        elif declared_version != authoritative_version:
            problems.append(
                f"{doc_id}：对账状态机版本 {declared_version!r} ≠ 当前 "
                f"{authoritative_version!r}（legacy "
                f"{list(V.legacy_versions('TOC_BODY_RECONCILIATION_VERSION'))}）—— "
                f"必须显式重算，不得静默按当前语义解释")
        if declared_blocking != authoritative_blocking:
            problems.append(
                f"{doc_id}：`toc_body_reconciliation_blocking_buckets`="
                f"{declared_blocking!r} ≠ 当前 {authoritative_blocking!r} —— "
                f"阻断口径不同，不得据此判定本轮是否放行")
    return {
        "version": authoritative_version,
        "blocking_buckets": authoritative_blocking,
        "legacy_versions":
            list(V.legacy_versions("TOC_BODY_RECONCILIATION_VERSION")),
        "counts": counts,
        "document_count": len(documents),
        "versions_by_document": doc_versions,
        "blocking_buckets_by_document": doc_blocking,
        "per_document": per_document,
        "all_documents_current": all(
            v == authoritative_version for v in doc_versions.values())
        and all(b == authoritative_blocking for b in doc_blocking.values()),
        "problems": problems,
    }


def run_manual_review(documents, conservation, equivalence, frozen,
                      rollup=None) -> str:
    """跨文档的人工复核总览（§八 的 769 守恒与 TS2 冻结分布对账在这里汇总）。"""
    #: §tocr-2：版本门与六桶汇总由 `toc_reconciliation_rollup` **单一来源**产出 ——
    #: 调用方（`main`）算一次并传入，run manifest 与本总览消费**同一份返回值**，
    #: 因此两处不可能各算一套。未传入时按同一函数现场计算（语义不变）。
    if rollup is None:
        rollup = toc_reconciliation_rollup(documents)
    recon_counts = rollup["counts"]
    lines = ["# TS3 标题树 / 对齐 人工复核总览", "",
             "**状态：manual_review_required**"
             "（构建完成不等于标题树通过；TS3 关闭不由实施方宣布）", "",
             f"- 冻结 TS2 基线：`{TS2_BASELINE_RUN_DIR}`"
             f"（按精确目录名 + 关键文件 SHA256 绑定，不 glob、不看 mtime）",
             f"- ALIGN_MIN：`{V.ALIGN_MIN}`；对齐 schema：`{V.ALIGN_SCHEMA_VERSION}`"
             f" + 拒绝终态 `{V.ALIGN_REFUSAL_SCHEMA_VERSION}`",
             # §四 P1-A(4)：标题资格语义再次变化 ⇒ profile 必须再升版，且在总览可见；
             # legacy 清单与当前版本一起列出，读产物的人不必打开 manifest 就能判断
             # 这份产物是不是按"书签可放行"的旧语义构建的。
             f"- 标题资格 profile：`{V.HEADING_QUALIFICATION_PROFILE_VERSION}`"
             f"（legacy：`{list(V.legacy_versions('HEADING_QUALIFICATION_PROFILE_VERSION'))}`）；"
             f"表格区域资格：`{V.TABLE_REGION_QUALIFICATION_VERSION}`"
             f"（legacy：`{list(V.legacy_versions('TABLE_REGION_QUALIFICATION_VERSION'))}`）", "",
             "## 逐份文档", "",
             "| 文档 | 页数 | 节点 | 层级 | 候选(接受/拒绝) | 仅编号被接受 | "
             "小标题被保留 | 正式 unassigned | 块/终态 |", "|---|---|---|---|---|---|---|---|---|"]
    for built in documents:
        m = built["manifest"]
        stats = m["stats"]
        a = built["alignment"]
        small = built["acceptance"]["small_heading_accepted"]
        lines.append(
            f"| {m['document_id']} | {m['page_count']} | {stats['nodes']} | "
            f"{'/'.join(f'L{k}={v}' for k, v in stats['levels'].items())} | "
            f"{built['acceptance']['accepted_count']}/"
            f"{built['acceptance']['rejected_count']} | "
            f"{built['acceptance']['numbering_only_accepted']} | {small} | "
            f"{m['formal_unassigned_count']} | {a['block_count']}/"
            f"{a['terminals_emitted']} |")
    lines += ["", "## 769 条守恒（§五）", "",
              "| 文档 | DB 块 | 冻结基线块 | 正式终态 | 缺终态 | 终态版本 | 守恒 |",
              "|---|---|---|---|---|---|---|"]
    for row in conservation["documents"]:
        lines.append(
            f"| {row['document_id']} | {row['db_blocks']} | "
            f"{row['frozen_baseline_blocks']} | {row['formal_terminals']} | "
            f"{row['terminals_missing']} | {row['terminal_schema_versions']} | "
            f"{'是' if row['conserved'] else '否'} |")
    lines += [f"| **合计** | {conservation['total_db_blocks']} | "
              f"{conservation['total_frozen_blocks']} | "
              f"{conservation['total_formal_terminals']} | "
              f"{conservation['total_terminals_missing']} | | "
              f"**{'是' if conservation['conserved'] else '否'}** |", "",
              f"- {conservation['fixture_note']}", "",
              "## TS2 冻结分布对账", "",
              f"- 冻结身份：`{json.dumps(frozen.get('identity', {}), ensure_ascii=False)}`",
              f"- 关键文件哈希：`{json.dumps({k: v.get('match') for k, v in (frozen.get('digests') or {}).items()}, ensure_ascii=False)}`",
              f"- 逐块对账：可比 {equivalence.get('comparable')}，"
              f"本文档块数 {equivalence.get('block_count')}，"
              f"差异块 {equivalence.get('differing_block_count')}，"
              f"三态与 TS2 相符：{equivalence.get('three_state_matches_ts2')}",
              f"- 冻结三态：`{equivalence.get('expected_three_state')}`；"
              f"本轮三态：`{equivalence.get('verdict_counts')}`",
              f"- TS3 正式记录自洽核验："
              f"{(equivalence.get('ts3_record_consistency') or {}).get('ok')}", ""]
    # §四 P1-B(7) / §tocr-2（P2-2）：**总览必须自带**对账版本、阻断桶与六桶汇总 ——
    # 读者不必打开 run manifest 或逐文档产物。数字与 manifest 同源（同一份 rollup）。
    #
    # 这里**不**把"非零"一律写成失败：六桶里只有阻断桶非空才阻断；其余桶非零是本轮
    # 真实状态，**必须如实显示**（`toc_target_unresolved` / `bookmark_navigation_only`
    # / `body_heading_unassigned` 都保留各自的语义），也不得写成"全部目录均已解析"或
    # "标题树不存在缺口"。
    _blocking = list(rollup["blocking_buckets"])
    _unassigned_n = recon_counts.get("toc_body_unassigned", 0)
    lines += ["## 目录来源 / 正文 landing 对账（六桶 · 版本门）", "",
              f"- 对账状态机版本：`{rollup['version']}`"
              f"（legacy：`{rollup['legacy_versions']}`）",
              f"- 阻断桶（唯一口径）：`{_blocking}` —— 只有这些桶非空才阻断标题树门，"
              f"其余桶非零是本轮**真实状态**，不等于失败",
              f"- 逐份文档声明的版本："
              f"`{json.dumps(rollup['versions_by_document'], ensure_ascii=False)}`"
              f"；双声明的阻断桶："
              f"`{json.dumps(rollup['blocking_buckets_by_document'], ensure_ascii=False)}`"
              f"；逐份与顶层一致："
              f"**{'是' if rollup['all_documents_current'] else '否'}**",
              "", "| 桶 | 条数（跨文档汇总） | 是否阻断 |", "|---|---|---|"]
    for _name in OB.TOC_BODY_RECONCILIATION_BUCKETS:
        lines.append(f"| `{_name}` | {recon_counts.get(_name, 0)} | "
                     f"{'**是**' if _name in _blocking else '否'} |")
    for _name in sorted(set(recon_counts)
                        - set(OB.TOC_BODY_RECONCILIATION_BUCKETS)):
        # 附注计数（legacy 同义桶 / "目标未解析但正文标题已进树"）：它们的语义与
        # 上方六桶不同，单独成行，且**不**改变阻断结论。
        lines.append(f"| `{_name}`（附注，非阻断） | {recon_counts[_name]} | 否 |")
    if _unassigned_n:
        lines.append(
            f"- **`toc_body_unassigned`（阻断桶）= {_unassigned_n}**："
            f"**本轮触发该阻断项** —— 存在目录项已闭合到合格正文行、该行却不是正式 "
            f"`OutlineNode` 的证据，标题树门**阻断**（逐条见上方问题清单）")
    else:
        # 条数为 0 时**保留该行**并显式说明"未触发"：删掉这一行会让人读成"这个阻断项
        # 不存在"。同时**不得**由此写成"目录已全部解析 / 标题树无缺口"—— 其余桶仍可能
        # 非零，那是另一批对象。
        lines.append(
            f"- **`toc_body_unassigned`（阻断桶）= 0**：**本轮未触发该阻断项**"
            f"（该桶仍是阻断口径的一部分，本行**不**表示「全部目录均已解析」，"
            f"也**不**表示「标题树不存在缺口」）")
    if any(recon_counts.get(n, 0) for n in ("toc_target_unresolved",
                                            "bookmark_navigation_only",
                                            "body_heading_unassigned")):
        # 三个非阻断桶的语义说明只写一次；逐桶只报条数 —— 否则同一段解释会重复三遍。
        lines.append(
            "- 非阻断桶逐项（**如实登记**，不作失败结论）："
            "`toc_target_unresolved` 指目录页声明的**页目标**未闭合、"
            "`bookmark_navigation_only` 指 PDF 大纲条目不在 `PageLayout` 里、"
            "`body_heading_unassigned` 指**正文候选**未采纳 —— 三者是**不同对象**。")
    for _name in ("toc_target_unresolved", "bookmark_navigation_only",
                  "body_heading_unassigned"):
        if _name in _blocking:
            continue
        _n = recon_counts.get(_name, 0)
        _same = recon_counts.get("toc_target_unresolved_with_existing_node", 0) \
            if _name == "toc_target_unresolved" else 0
        _extra = (f"（其中 {_same} 条的**正文标题已经作为节点进树**，只说明目录页声明"
                  f"的页目标未闭合，**不是**「真标题漏收」）" if _same else "")
        lines.append(f"- 非阻断桶 `{_name}` = {_n}{_extra}")
    if rollup["problems"]:
        lines += ["", "**版本门阻断（本轮不得按当前语义放行）**："]
        lines += [f"- {p}" for p in rollup["problems"]]
    lines += ["", "## 仍需人工检查的真实问题", ""]
    problems = []
    gate_problem_counts: dict = {}
    #: §四 P1-B(7)：目录来源 / 正文 landing 的六类对账桶（**跨文档汇总**）。它与
    #: `gate_problem_counts` 分开记：桶里的对象与"误收 / 漏收"不是同一批，混进同一个
    #: 计数会让读者又把它当成"真标题漏收"。
    #: §tocr-2：版本门与六桶汇总由 `toc_reconciliation_rollup` **单一来源**产出 ——
    #: 调用方算一次，run manifest 与本总览消费**同一份返回值**，因此两处不可能各算一套。
    recon_versions = [v for v in rollup["versions_by_document"].values()
                      if v != rollup["version"]]
    # §tocr-2 版本门：缺版本键 / 版本不同 / 阻断桶不同 ⇒ 在**问题清单**里也逐条点名
    # （与上方「版本门阻断」小节同源），确保"总览说没问题"不可能与载荷状态相反。
    problems += list(rollup["problems"])
    if not conservation["conserved"]:
        problems.append("769 条守恒未成立（见上表逐行差异）")
    if not equivalence.get("identical"):
        problems.append(
            f"与冻结基线存在 {equivalence.get('differing_block_count')} 个差异块"
            f"（字段直方图 {equivalence.get('differing_field_histogram')}）")
    for built in documents:
        m = built["manifest"]
        if built["acceptance"]["numbering_only_accepted"]:
            problems.append(
                f"{m['document_id']}：{built['acceptance']['numbering_only_accepted']}"
                f" 条候选仅靠编号被接受")
        if not m["structure_validation_ok"]:
            problems.append(f"{m['document_id']}：结构校验失败 {m['failed_checks']}")
        if m["company_id"] != FIXTURE_COMPANY_ID \
                and built["alignment"]["terminals_missing_count"]:
            problems.append(f"{m['document_id']}：有块缺正式终态")
        # §六(5)：**总览**同样不得在存在同级误嵌套 / 正文误收 / 待人工复核候选 /
        # 四态不守恒时写「自动检查无失败」—— 否则逐份产物说有问题、总览却说没问题。
        for name, count in (built["findings"]["counts"] or {}).items():
            if count:
                gate_problem_counts[name] = gate_problem_counts.get(name, 0) + count
        # §三.4：表内采纳节点总数必须等于安全穿透清单长度（总览同样不得只看聚合计数）。
        _inside = built["findings"].get("inside_table_accepted")
        _safe = built["findings"].get("safe_table_override_accepted") or []
        if _inside is not None and _inside != len(_safe):
            problems.append(
                f"{m['document_id']}：表内采纳节点 {_inside} 条 / 安全穿透清单 "
                f"{len(_safe)} 条（每条表内采纳节点都必须逐条给出安全穿透证据）")
        if _inside:
            review = built["findings"].get("review_counts") or {}
            problems.append(
                f"{m['document_id']}：表内采纳 `inside_table_accepted`={_inside}、"
                f"`safe_table_override_accepted`={len(_safe)}、"
                f"`unsafe_table_override_accepted`="
                f"{review.get('unsafe_table_override_accepted', 0)}"
                f"（**非失败项**，但每条安全穿透必须逐条人工读，证据见该文档 "
                f"`manual_review.md` 第 11 节「结构复核门」）")
        # §三：两侧分叉与"缺可复核来源 landing"必须在总览里**点名**，不能只在逐份产物里
        # 出现 —— 总览写"无失败"而逐份写"阻断"正是本轮要消除的形态。
        for key in ("safe_override_mismatch", "unsafe_override_mismatch",
                    "source_landing_unverified", "toc_landing_unverified_accepted"):
            count = (built["findings"]["counts"] or {}).get(key, 0)
            if count:
                problems.append(
                    f"{m['document_id']}：`{key}`={count} 条（生产侧与独立复核侧的表格"
                    f"穿透资格结论分叉 / 缺可独立复核的来源 landing 上下文 / 自报凭来源"
                    f"landing 采纳而复核侧重建不出）："
                    f"逐条证据见该文档 `manual_review.md` 第 11 节「结构复核门」")
        # §四 P1-B(7)：目录来源 / 正文 landing 的对账已在 `toc_reconciliation_rollup`
        # 里按文档汇总（版本门 + 六桶 + legacy 同义桶 + "正文标题其实已进树"计数），
        # 下方按类点名。此处不再各自累加，避免与 run manifest 各算一套。
        for gap in built["findings"].get("verification_gaps", ()):
            problems.append(
                f"{m['document_id']}：独立验收 fail-closed "
                f"（{gap.get('kind')}）—— 不得据此报 clean")
        if not built["four_state"]["conserved"]:
            problems.append(
                f"{m['document_id']}：正式四态不守恒（四态之和 "
                f"{built['four_state']['four_state_sum']} / 逐行 "
                f"{built['four_state']['total_lines']} / 非家具 "
                f"{built['four_state']['non_furniture_lines']}）")
    for name, count in sorted(gate_problem_counts.items()):
        if name == "numbered_list_ambiguity":
            # §二.4：无独立人工 gold，**不得**称「真标题漏收」。
            problems.append(
                f"待人工复核候选：编号列表歧义 {count} 条（已进入各文档正式 "
                f"`unassigned`，见 `formal_unassigned.json`；无独立人工 gold，"
                f"不得据此宣称漏收）")
        elif name in ("toc_unmatched", "bookmark_unmatched"):
            # §四 P1-B(7)：这两个是**正式 `unassigned` 的原因码计数**，名字里出现
            # "toc" / "bookmark" 指的是**来源**，不是"被漏收的正文标题"。
            problems.append(
                f"正式 `unassigned`（`{name}`）：{count} 条 —— 目录来源行**不是**正文标题、"
                f"书签也不在 `PageLayout` 里，二者都**不是**「真标题漏收」；"
                f"分类对账见下方 `toc_body_reconciliation` 六桶")
        elif name == "same_declared_level_nesting":
            problems.append(f"同级误嵌套：{count} 处（§三禁止）")
        elif name in ("prose_like_accepted", "definition_like_accepted"):
            problems.append(f"正文误收（{name}）：{count} 条")
        elif name in ("global_peer_only_accepted",
                      "unsafe_table_override_accepted",
                      "fragmented_cell_accepted", "adjacent_heading_rejected",
                      "accepted_without_intrinsic_primary_evidence"):
            problems.append(f"独立验收失败（{name}）：{count} 条")
        elif name == "accepted_without_node":
            problems.append(f"候选被采纳却没有正式节点：{count} 条")
    # §四 P1-B(7)：六桶在**总览**里逐类点名（`toc_body_resolved` 是已闭合的正例，不算
    # 问题，因此不进 `problems`；其余五类是必须被看见的未闭合面）。
    #
    # §tocr-2：**版本门优先** —— 只要任一份载荷声明的对账状态机版本不是当前值，这批桶
    # 就**不得**按当前语义解释，必须先显式重算。这条排在桶计数之前，避免读总览的人
    # 先看到"六桶皆空"就以为放行。
    if recon_versions:
        problems.append(
            f"目录 / 正文 landing 对账的状态机版本不是当前版本："
            f"{sorted(set(map(repr, recon_versions)))}（当前 "
            f"{V.TOC_BODY_RECONCILIATION_VERSION!r}；legacy "
            f"{list(V.legacy_versions('TOC_BODY_RECONCILIATION_VERSION'))}）—— "
            f"`tocr-1` 的 `toc_source_only` 与本版 `toc_body_unassigned` 描述同一份输入"
            f"状态而**语义相反**，因此这批桶必须显式重算后重新验收，不得静默按当前"
            f"语义解释")
    if recon_counts.get("toc_body_unassigned"):
        problems.append(
            f"**正文 landing 未成为节点（`toc_body_unassigned`）："
            f"{recon_counts['toc_body_unassigned']} 条** —— 目录项在真实版式上**已闭合到"
            f"一条合格正文行**（来源身份可重建、页标签唯一映射、该页上存在文本一致、"
            f"非家具、非普通表格内容的真实正文行），而该行**不是**正式 `OutlineNode`："
            f"这是「可能存在正文标题漏收」的**直接证据**，**阻断**本轮的 TS3 关闭门；"
            f"不得为清零它而自动把该正文行加入树（逐条身份见各文档 "
            f"`manual_review.md` 第 11 节「结构复核门」）")
    if recon_counts.get("legacy_toc_source_only"):
        problems.append(
            f"legacy `toc_source_only`：{recon_counts['legacy_toc_source_only']} 条 —— "
            f"它与当前 `toc_body_unassigned` 描述**同一份输入状态**，`tocr-1` 却把它写成"
            f"「目录来源仅有导航价值、不是漏收」，是 fail-open；本产物必须按 "
            f"`toc_body_unassigned` 重新解释并**同样阻断**")
    if recon_counts.get("toc_target_unresolved"):
        same_n = recon_counts.get("toc_target_unresolved_with_existing_node") or 0
        extra = ""
        if same_n:
            # §四 P1-B(2)：这一句是必需的 —— 否则"目标未解析 N 条"会被读成
            # "N 个正文标题没进树"。二者是**不同对象**。
            extra = (f"（其中 {same_n} 条的**正文标题已经作为节点进树**，只说明**目录页"
                     f"声明的页目标**未闭合，**不是**「真标题漏收」）")
        problems.append(
            f"目录目标未解析（`toc_target_unresolved`）："
            f"{recon_counts['toc_target_unresolved']} 条 —— 声明页标签无法唯一映射 / 目标行"
            f"在版式上不存在 / 该页上只有同名的普通表格内容行；**不得**为它编造节点"
            f"{extra}")
    if recon_counts.get("bookmark_navigation_only"):
        problems.append(
            f"书签仅作导航（`bookmark_navigation_only`）："
            f"{recon_counts['bookmark_navigation_only']} 条 —— PDF 大纲条目不在 "
            f"`PageLayout` 里：不参与标题资格、不穿透 `inside_table`、不使复核侧 "
            f"`available` 为真")
    if recon_counts.get("body_heading_unassigned"):
        problems.append(
            f"正文候选未采纳（`body_heading_unassigned`）："
            f"{recon_counts['body_heading_unassigned']} 条 —— **正文**候选（与目录来源行"
            f"是不同对象）；无独立人工 gold，不得据此宣称漏收")
    if not problems:
        problems.append("（自动检查未发现失败项；标题树的**业务可用性**仍需人工判读）")
    lines += [f"- {p}" for p in problems]
    if recon_counts.get("toc_body_resolved"):
        lines.append(
            f"- 目录→正文已闭合（`toc_body_resolved`，**非问题项**）："
            f"{recon_counts['toc_body_resolved']} 条（逐条见各文档 `manual_review.md` "
            f"第 6.3 节）")
    lines += ["", "> 本文件由只读评测脚本生成；它不宣布 TS3 关闭，也不代替人工判读。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="TS3 真实文档标题树验收（只读）")
    parser.add_argument("--run-id", default=None, help="新 run_id（不得与历史目录重复）")
    parser.add_argument("--validate-only", action="store_true",
                        help="只做模块自检，不构建、不写任何文件")
    args = parser.parse_args(argv)

    if args.validate_only:
        report = {"outline_self_check": OB.self_check(),
                  "aligner_self_check": A.self_check(),
                  "layout_self_check": LB.self_check()}
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if all(report[k]["ok"] for k in report) else 1

    # §十：`generated_at` 与 `run_id` 都取**同一刻**的真实 UTC。若调用方显式给了
    # `--run-id`，它是 opaque 标签（不一定带时间语义），由 `run_id_identity` 如实标注。
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    generated_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    # §十：只有**这里**打出来的 run_id 才带时间语义；显式 `--run-id` 一律 opaque
    # （即使它长得像时间戳），见 `run_id_identity`。
    run_id_source = "caller_supplied" if args.run_id else "generated_utc"
    run_id = args.run_id or now_utc.strftime("ts3_outline_%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / f"tree_structure_ts3_outline_{run_id}"
    if run_dir.exists():
        raise SystemExit(f"结果目录已存在，拒绝覆盖历史结果：{run_dir}")

    for _, _, name in REAL_DOCUMENTS:
        path = SAMPLE_DIR / name
        if not path.exists():
            raise SystemExit(f"[ENVIRONMENT_DISCREPANCY] 缺少真实 PDF：{path}")

    watch = [EVIDENCE_DB, FINANCIAL_DB] + [
        SAMPLE_DIR / name for _, _, name in REAL_DOCUMENTS]
    before = snapshot(watch)

    run_dir.mkdir(parents=True)
    fixture_path = write_fixture_pdf(run_dir / FIXTURE_FILE_NAME)
    fixture_sha_1 = sha256_file(fixture_path)

    # 版式只建一次：`load_evidence` 需要先有 `document_version` 才能与 DB 交叉核对，
    # 而同一份 PDF 的 `PageLayout` 是确定性的，重复构建只会白花时间。
    real_layouts = {}
    for company_id, document_id, name in REAL_DOCUMENTS:
        real_layouts[document_id] = LB.build_page_layout(
            SAMPLE_DIR / name, LB.LayoutBuildContext(company_id=company_id,
                                                     document_id=document_id))

    # §六：先固定冻结基线身份（fail-closed），再据此派生 per-document 分布对账。
    baseline_path, baseline_rows, frozen = _load_baseline()
    baseline_dist = frozen_distribution_by_document(baseline_rows)

    # §四/§五：本文档的 Evidence block 只从**只读** DB 取，身份全部重算；
    # 三份真实文档的块合起来必须恰为冻结基线的 769 条（守恒在产物里逐条对账）。
    evidence_rows = RA.load_evidence(real_layouts)
    blocks_by_doc: dict = {document_id: [] for _, document_id, _ in REAL_DOCUMENTS}
    for company_id, document_id, version, row in evidence_rows:
        blocks_by_doc[document_id].append(
            RA.evidence_block_input(company_id, document_id, version, row))

    # §八：权威证据集成员由**受信任只读 gateway** 注入（`?mode=ro` +
    # `PRAGMA query_only=ON`），aligner 不自行查库。少一个 / 多一个 / 身份不符
    # 都由正式集合入口 fail-closed，本脚本只负责把权威清单递进去。
    gateway = RA.ReadOnlyEvidenceGateway(EVIDENCE_DB)
    snapshots: dict = {}
    for company_id, document_id, name in REAL_DOCUMENTS:
        snapshots[document_id] = authoritative_snapshot(
            gateway, real_layouts[document_id])

    documents = []
    for company_id, document_id, name in REAL_DOCUMENTS:
        built = build_one(run_dir, company_id=company_id, document_id=document_id,
                          pdf_path=SAMPLE_DIR / name, label="真实 PDF",
                          layout=real_layouts[document_id],
                          evidence_blocks=tuple(blocks_by_doc[document_id]),
                          baseline_dist=baseline_dist,
                          snapshot=snapshots[document_id])
        documents.append(built)
    fixture_built = build_one(run_dir, company_id=FIXTURE_COMPANY_ID,
                              document_id=FIXTURE_DOCUMENT_ID,
                              pdf_path=fixture_path, label="非 300750 合成 fixture",
                              evidence_blocks=(), baseline_dist=baseline_dist)
    documents.append(fixture_built)
    fixture_sha_2 = sha256_file(fixture_path)

    equivalence = ts2_alignment_equivalence(real_layouts, baseline_rows, frozen,
                                            snapshots=snapshots)
    (run_dir / "ts2_alignment_equivalence.json").write_text(
        json.dumps(equivalence, ensure_ascii=False, indent=1), encoding="utf-8")

    # 769 条守恒：只统计三份**真实**文档（合成 fixture 没有 EvidenceBlock）。
    conservation = evidence_conservation(documents, evidence_rows, baseline_rows)
    (run_dir / "evidence_conservation.json").write_text(
        json.dumps(conservation, ensure_ascii=False, indent=1), encoding="utf-8")

    after = snapshot(watch)
    immutability = immutability_report(before, after)
    # §tocr-2 **版本门**（P2-1）：run manifest 的 `toc_body_reconciliation_*` 与顶层
    # `manual_review.md` 的对应小节**消费同一份返回值** —— 算一次，两处引用，故两处
    # 不可能各算一套。本脚本不写字面量版本号 / 桶名：权威值只来自 `versions.py` 与
    # `outline_builder.py` 的常量。
    recon_rollup = toc_reconciliation_rollup(documents)
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "manual_review_required": True,
        "run_id": run_id,
        "run_id_identity": run_id_identity(run_id, source=run_id_source),
        "generated_at_utc": generated_at,
        "run_dir": str(run_dir),
        "ts3_scope": ("交付 A：唯一生产对齐核心的等价性对账；"
                      "交付 B：DocumentOutline 构建 + typed 审计产物。"
                      "未进入 TS4–TS7、R3、P4。"),
        "outline_builder_version": OUTLINE_BUILDER_VERSION,
        "aligner_version": V.ALIGNER_VERSION,
        "align_min": V.ALIGN_MIN,
        # §五「版本进入依赖指纹」：正式对齐与拒绝终态的 schema 版本必须随产物落盘，
        # 否则读产物的人无法判断某条终态是按哪一版 wire 语义写出来的。
        "align_schema_version": V.ALIGN_SCHEMA_VERSION,
        "align_refusal_schema_version": V.ALIGN_REFUSAL_SCHEMA_VERSION,
        "evidence_block_input_version": V.EVIDENCE_BLOCK_INPUT_VERSION,
        "outline_unassigned_evidence_set_version":
            V.OUTLINE_UNASSIGNED_EVIDENCE_SET_VERSION,
        "normalization_version": V.NORMALIZATION_VERSION,
        "schema_version": V.OUTLINE_SCHEMA_VERSION,
        # §四：资格 profile 与表格区域资格必须进 run manifest，并连同 legacy 清单一起
        # 声明 —— legacy 载荷不得静默按新语义解释（`hq-4` 起书签不再放行表内穿透）。
        "heading_qualification_profile_version":
            V.HEADING_QUALIFICATION_PROFILE_VERSION,
        "table_region_qualification_version":
            V.TABLE_REGION_QUALIFICATION_VERSION,
        "legacy_heading_qualification_profile_versions":
            list(V.legacy_versions("HEADING_QUALIFICATION_PROFILE_VERSION")),
        "legacy_table_region_qualification_versions":
            list(V.legacy_versions("TABLE_REGION_QUALIFICATION_VERSION")),
        # §四 P1-B(7) / §tocr-2（P2-1）：对账的**版本 + 阻断桶清单 + 六桶计数**必须
        # 随 run manifest 落盘，且与每份文档 `structural_review_findings.json` 的声明
        # **逐项相等**。值全部来自 `toc_reconciliation_rollup`（其权威来源是
        # `versions.py` 与 `outline_builder.py` 的常量），本脚本不写字面量版本号/桶名。
        # 任一文档缺失版本键、版本不同、或阻断桶不同 ⇒ `all_documents_current` 为假、
        # `version_gate_problems` 逐条点名，本 run **不得**按当前语义放行。
        "toc_body_reconciliation_version": recon_rollup["version"],
        "toc_body_reconciliation_blocking_buckets":
            recon_rollup["blocking_buckets"],
        "toc_body_reconciliation_legacy_versions":
            recon_rollup["legacy_versions"],
        "toc_body_reconciliation_counts": recon_rollup["counts"],
        "toc_body_reconciliation_versions_by_document":
            recon_rollup["versions_by_document"],
        "toc_body_reconciliation_blocking_buckets_by_document":
            recon_rollup["blocking_buckets_by_document"],
        "toc_body_reconciliation_all_documents_current":
            recon_rollup["all_documents_current"],
        "toc_body_reconciliation_version_gate_problems":
            recon_rollup["problems"],
        "toc_body_reconciliation_blocking_nonempty":
            sorted(name for name in recon_rollup["blocking_buckets"]
                   if recon_rollup["counts"].get(name)),
        # §六：冻结基线的不可变身份（目录名 + 关键文件 SHA256 + 块数）。
        "ts2_frozen_baseline": frozen.get("identity"),
        "ts2_frozen_baseline_digests": frozen.get("digests"),
        "gate_status": "manual_review_required（构建完成不等于标题树通过；"
                       "TS3 关闭不由实施方宣布）",
        # 版本门不成立时，run 级状态直接标为阻断 —— 读者不必逐文档翻产物才知道
        # 这份载荷不是按当前对账语义产生的。
        "toc_body_reconciliation_gate_status": (
            "blocked（有文档缺失 / 版本不同 / 阻断桶不同：见 "
            "`toc_body_reconciliation_version_gate_problems`）"
            if recon_rollup["problems"] else "ok（版本与阻断桶逐份与顶层一致）"),
        "documents": [{
            "label": d["manifest"]["label"],
            "company_id": d["manifest"]["company_id"],
            "document_id": d["manifest"]["document_id"],
            "input_path": d["manifest"]["input_path"],
            "input_sha256": d["manifest"]["input_sha256"],
            "page_count": d["manifest"]["page_count"],
            "page_layout_id": d["manifest"]["page_layout_id"],
            "outline_id": d["manifest"]["outline_id"],
            "nodes": d["manifest"]["stats"]["nodes"],
            "levels": d["manifest"]["stats"]["levels"],
            "edges": d["manifest"]["stats"]["edges_total"],
            "candidate_sources": d["manifest"]["candidate_sources"],
            "structure_validation_ok": d["manifest"]["structure_validation_ok"],
            "failed_checks": d["manifest"]["failed_checks"],
            "duplicate_path_group_count":
                d["manifest"]["duplicate_path_group_count"],
            "same_page_same_title_group_count":
                d["manifest"]["same_page_same_title_group_count"],
            "cross_parent_same_title_count":
                d["manifest"]["cross_parent_same_title_count"],
            "line_assignment_counts":
                d["manifest"]["stats"]["line_assignment_counts"],
            "formal_unassigned_count": d["manifest"]["formal_unassigned_count"],
            "outline_content_fingerprint":
                d["manifest"]["outline_content_fingerprint"],
            "accepted_count": d["acceptance"]["accepted_count"],
            "rejected_count": d["acceptance"]["rejected_count"],
            "numbering_only_accepted":
                d["acceptance"]["numbering_only_accepted"],
            "small_heading_accepted":
                d["acceptance"]["small_heading_accepted"],
            # §「导航 ≠ 资格」：两项在**运行级清单**里也分开透传，读清单的人不必
            # 打开逐文档报告就能看出"导航命中"与"正式资格依据"是两件事。
            "accepted_navigation_matches":
                d["acceptance"]["accepted_navigation_matches"],
            "accepted_by_qualification_basis":
                d["acceptance"]["accepted_by_qualification_basis"],
            "non_formal_qualification_basis":
                d["acceptance"]["non_formal_qualification_basis"],
            "rejected_by_reason": d["acceptance"]["rejected_by_reason"],
            "alignment": d["manifest"]["alignment"],
        } for d in documents],
        "evidence_conservation": {
            "conserved": conservation["conserved"],
            "total_db_blocks": conservation["total_db_blocks"],
            "total_frozen_blocks": conservation["total_frozen_blocks"],
            "total_formal_terminals": conservation["total_formal_terminals"],
            "total_terminals_missing": conservation["total_terminals_missing"],
            "documents": conservation["documents"],
        },
        "fixture_determinism": {
            "sha256_first": fixture_sha_1,
            "sha256_second": fixture_sha_2,
            "byte_identical": fixture_sha_1 == fixture_sha_2,
        },
        "ts2_alignment_equivalence": {
            "comparable": equivalence["comparable"],
            "baseline_run_dir": equivalence.get("baseline_run_dir"),
            "block_count": equivalence.get("block_count"),
            "differing_block_count": equivalence.get("differing_block_count"),
            "differing_field_histogram":
                equivalence.get("differing_field_histogram"),
            "identical": equivalence.get("identical"),
            "verdict_counts": equivalence.get("verdict_counts"),
            "citable_blocks": equivalence.get("citable_blocks"),
            "three_state_matches_ts2": equivalence.get("three_state_matches_ts2"),
            "record_refusals": equivalence.get("record_refusals"),
            "compared_fields": equivalence.get("compared_fields"),
            "unavailable_fields": equivalence.get("unavailable_fields"),
            "ts3_record_consistency_ok":
                (equivalence.get("ts3_record_consistency") or {}).get("ok"),
        },
        "immutability_ok": immutability["unchanged"],
        "watched_missing": immutability["watched_missing"],
        "watched_changed": immutability["changed"],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "database_immutability.json").write_text(
        json.dumps(immutability, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "manual_review.md").write_text(
        run_manual_review(documents, conservation, equivalence, frozen,
                          recon_rollup),
        encoding="utf-8")

    # §十：artifact hash index **最后**写（它是"本 run 目录里有什么"的清单），并在
    # 写盘后立刻按索引逐文件重算核验一次 —— 索引自己出错也要在产物里看得见。
    inputs_identity = {
        "documents": [{
            "document_id": d["manifest"]["document_id"],
            "company_id": d["manifest"]["company_id"],
            "pdf_path": d["manifest"]["input_path"],
            "pdf_sha256": d["manifest"]["input_sha256"],
            "page_count": d["manifest"]["page_count"],
            "page_layout_id": d["manifest"]["page_layout_id"],
            "page_layout_locator": d["manifest"]["page_layout_locator"],
            "outline_id": d["manifest"]["outline_id"],
            "evidence_set_snapshot":
                (d["alignment"] or {}).get("evidence_set_snapshot"),
            "member_identity_sha256":
                (d["alignment"] or {}).get("member_identity_sha256"),
        } for d in documents],
        "ts2_frozen_baseline": frozen.get("identity"),
        "fixture_determinism": {
            "sha256_first": fixture_sha_1, "sha256_second": fixture_sha_2,
            "byte_identical": fixture_sha_1 == fixture_sha_2},
    }
    versions = version_manifest()
    code = code_fingerprint()
    index = artifact_hash_index(run_dir, run_id=run_id,
                                generated_at=generated_at, versions=versions,
                                code=code, inputs=inputs_identity,
                                run_id_source=run_id_source,
                                database={"before": before, "after": after,
                                          "immutability": immutability})
    (run_dir / "artifact_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    index_check = verify_artifact_index(run_dir, index,
                                        expected_versions=versions,
                                        expected_inputs=inputs_identity)
    (run_dir / "artifact_index_check.json").write_text(
        json.dumps({"manual_review_required": True,
                    "artifact": "artifact_index_check.json",
                    "scope": "按 artifact_index.json 逐文件重算的只读核验",
                    "generated_at_utc": generated_at,
                    "index_identity": index["index_identity"],
                    **index_check}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(json.dumps({
        "run_dir": str(run_dir),
        "manual_review_required": True,
        "documents": [{
            "document_id": d["manifest"]["document_id"],
            "pages": d["manifest"]["page_count"],
            "nodes": d["manifest"]["stats"]["nodes"],
            "levels": d["manifest"]["stats"]["levels"],
            "toc_entries": d["manifest"]["stats"]["toc_entries"],
            "bookmarks": d["manifest"]["stats"]["bookmarks"],
            "edges_toc_to_body_resolved":
                d["manifest"]["stats"]["edges_toc_to_body_resolved"],
            "edges_toc_to_body_unresolved":
                d["manifest"]["stats"]["edges_toc_to_body_unresolved"],
            "line_assignment_counts":
                d["manifest"]["stats"]["line_assignment_counts"],
            "structure_validation_ok": d["manifest"]["structure_validation_ok"],
            "failed_checks": d["manifest"]["failed_checks"],
            "duplicate_path_group_count":
                d["manifest"]["duplicate_path_group_count"],
            "same_page_same_title_group_count":
                d["manifest"]["same_page_same_title_group_count"],
            "cross_parent_same_title_count":
                d["manifest"]["cross_parent_same_title_count"],
            "formal_unassigned_count": d["manifest"]["formal_unassigned_count"],
            "accepted_count": d["acceptance"]["accepted_count"],
            "rejected_count": d["acceptance"]["rejected_count"],
            "numbering_only_accepted":
                d["acceptance"]["numbering_only_accepted"],
            "small_heading_accepted":
                d["acceptance"]["small_heading_accepted"],
            # §「导航 ≠ 资格」：两项在**运行级清单**里也分开透传，读清单的人不必
            # 打开逐文档报告就能看出"导航命中"与"正式资格依据"是两件事。
            "accepted_navigation_matches":
                d["acceptance"]["accepted_navigation_matches"],
            "accepted_by_qualification_basis":
                d["acceptance"]["accepted_by_qualification_basis"],
            "non_formal_qualification_basis":
                d["acceptance"]["non_formal_qualification_basis"],
            "alignment": d["manifest"]["alignment"],
        } for d in documents],
        "evidence_conservation": {
            "conserved": conservation["conserved"],
            "total_db_blocks": conservation["total_db_blocks"],
            "total_frozen_blocks": conservation["total_frozen_blocks"],
            "total_formal_terminals": conservation["total_formal_terminals"],
            "total_terminals_missing": conservation["total_terminals_missing"],
        },
        "fixture_determinism": fixture_sha_1 == fixture_sha_2,
        "ts2_alignment_equivalence": {
            "comparable": equivalence["comparable"],
            "baseline_run_dir": equivalence.get("baseline_run_dir"),
            "block_count": equivalence.get("block_count"),
            "differing_block_count": equivalence.get("differing_block_count"),
            "differing_field_histogram":
                equivalence.get("differing_field_histogram"),
            "verdict_counts": equivalence.get("verdict_counts"),
            "citable_blocks": equivalence.get("citable_blocks"),
            "three_state_matches_ts2": equivalence.get("three_state_matches_ts2"),
            "record_refusals": equivalence.get("record_refusals"),
            "compared_fields": equivalence.get("compared_fields"),
            "unavailable_fields": equivalence.get("unavailable_fields"),
            "ts3_record_consistency_ok":
                (equivalence.get("ts3_record_consistency") or {}).get("ok"),
        },
        "immutability_ok": immutability["unchanged"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

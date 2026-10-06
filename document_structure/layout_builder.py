"""TS2 确定性 PageLayout 构建器（真实电子 PDF → `PageLayout`）。

公开入口：`build_page_layout(pdf_path, context) -> PageLayout`。

设计约束（指令 §三 / §四 / §五，计划 §2.1）：

- **只读**。只打开电子 PDF 读取；不写源文件、不调 LLM / Contract / Router / 网络 /
  数据库（本模块不导入 sqlite3 / requests / subprocess / random / datetime）。
- **确定性**。所有参与哈希与持久化的浮点先 `round(x, FLOAT_PRECISION)`；排序使用
  **全序键**（几何 + 引擎原始次序），不依赖 dict 迭代顺序；不使用时间戳、run_id、
  输出路径、临时路径或随机数作为内容身份。
- **身份**沿用 TS1 既有规则：`document_version = "sha256-" + source_file_sha256[:16]`，
  locator / id 一律经 `PageLayout.create()` 派生，本模块不自行拼装。
- **不得因"目录/页眉/页脚"等词删除文本**。家具检测**只加标记**，原文一字不删；
  `PageLayout` 内仍保留全部家具行。
- **公司无关**。规则只使用版面几何、跨页重复率、数字形态与页码一致性；没有公司名、
  公司代码、固定页码、表号或样例专用分支。
- **fail-fast，不 OCR**。扫描件（无文本层）、严重空页、文本层过稀、加密、非 PDF、
  身份缺失一律以显式状态失败，绝不返回"空布局"冒充成功，也不裁剪越界内容。

规则版本由 `versions.LAYOUT_ENGINE` / `versions.LAYOUT_ENGINE_VERSION` /
`versions.NORMALIZATION_VERSION` 单一控制（`versions.py` 是本包唯一允许出现版本
字面量的文件）：本模块**不得**内联版本字面量。任何会改变构建结果（坐标、行集合、
家具判定、阅读顺序）的修改都必须提升这些常量之一。

TS2 只构建**版式层**。本模块不产出 `DocumentOutline` / 标题树 / `OutlineSpan` /
`TableObject`：那些属于 TS3 及以后。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
from dataclasses import dataclass, fields

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_json,
    is_finite,
    sha256_canonical,
)
from document_structure.normalization import (
    MAX_BAD_CHAR_RATIO,
    bad_char_ratio,
    tight,
)
from document_structure.schema import (
    FURNITURE_KINDS,
    LayoutLine,
    LayoutPage,
    LayoutSpan,
    PageLayout,
    quantize,
)
from document_structure.span_schema import _issue_capability

try:  # PyMuPDF 是本机唯一允许的 PDF 引擎；缺失即显式失败，不降级、不 OCR。
    import fitz as _fitz
except ImportError:  # pragma: no cover - 依赖缺失时由 engine_unavailable 显式报告
    _fitz = None

#: 引擎声明（进入 `PageLayout.engine` / locator）。
LAYOUT_ENGINE: str = V.LAYOUT_ENGINE
LAYOUT_ENGINE_VERSION: str = V.LAYOUT_ENGINE_VERSION

#: fail-fast 的显式状态词表。任何失败都必须带其中之一，不得返回空布局。
BUILD_FAILURE_STATUSES = (
    "engine_unavailable",   # 本机没有可用的 PyMuPDF
    "unreadable",           # 文件不存在 / 打不开 / 不是 PDF
    "encrypted",            # 需要口令（加密 PDF）
    "empty_document",       # 0 页
    "no_text_layer",        # 全文无可见文本（扫描件）—— 不 OCR
    "severe_empty_pages",   # 完全无文本的页面比例过高
    "quality_below_floor",  # 文本层过稀 / 坏字符比例过高
    "layout_out_of_page",   # 提取结果无法满足版式不变量（不裁剪、不伪造）
    "metadata_missing",     # 公司 / 文档身份缺失
    "version_mismatch",     # 调用方声明的 document_version 与文件哈希不符
)

#: 家具**规则**身份。任务要求的规则名是 `running_header` / `running_footer` /
#: `page_number`；而 `schema.FURNITURE_KINDS` 的**持久化**词表是
#: `header` / `footer` / `page_number` / `watermark` / `other`。
#: `schema.py` 本轮不得修改，因此规则名只活在构建器与审计里，写进 PageLayout 的
#: 是 schema 词表（映射关系显式声明，不隐式兼容）。
FURNITURE_RULE_IDS = ("running_header", "running_footer", "page_number")
FURNITURE_RULE_TO_KIND = {
    "running_header": "header",
    "running_footer": "footer",
    "page_number": "page_number",
}
KIND_TO_FURNITURE_RULE = {k: r for r, k in FURNITURE_RULE_TO_KIND.items()}

# --- 家具检测阈值（公司无关、文档无关、版本化） -----------------------------
#: 页眉带：行下沿 y1 <= height * 该比例。
HEADER_BAND_RATIO = 0.12
#: 页脚带：行上沿 y0 >= height * 该比例。
FOOTER_BAND_RATIO = 0.88
#: 跨页重复模板：至少出现在这么多页，且至少占全文这么多比例。
FURNITURE_REPEAT_MIN_PAGES = 5
FURNITURE_REPEAT_MIN_RATIO = 0.25
#: 模板里除数字占位符外必须至少有这么多个字符（`#`/`#,#` 之流不得成为家具）。
FURNITURE_MIN_TEMPLATE_LETTERS = 2

#: 页码候选：恰好一段数字、不超过这么多位，其余字符必须全部是装饰字符。
PAGE_NUMBER_MAX_DIGITS = 4
#: 页码一致性：众数偏移至少覆盖这么多页，且至少占全文这么多比例。
PAGE_NUMBER_MIN_PAGES = 5
PAGE_NUMBER_MIN_RATIO = 0.25
#: 页码必须**逐页变化**：跨页完全重复的常数（如固定在页脚的"2024"）不是页码。
PAGE_NUMBER_MIN_DISTINCT = 2
#: 页码装饰字符（纯排版装饰，不含任何自然语言同义词）。逗号/斜杠**不在**其中，
#: 因此表格数字 "1,234,567" / 分数形式不会被当成页码。
PAGE_NUMBER_DECORATION_CHARS = (
    "-—–‐·.~_"       # - — – ‐ · . ~ _
    "()（）[]【】"        # ( ) （ ） [ ] 【 】
    "<>《》、。：:"        # < > 《 》 、 。 ： :
    "第页共之"                                 # 第 页 共 之
    "　 "                                            # 表意空格 / 空格
)

# --- 阅读顺序阈值 -----------------------------------------------------------
#: 栏间空白至少这么宽才可能是分栏（表格列间距通常远小于此值）。
MIN_GUTTER_PT = 30.0
#: 每栏至少这么多行。
MIN_COLUMN_LINES = 6
#: 每栏的纵向跨度至少占页面高度的这个比例。
COLUMN_VERTICAL_SPAN_RATIO = 0.45
#: 每栏的横向宽度不超过页面宽度的这个比例。
MAX_COLUMN_WIDTH_RATIO = 0.62
#: 本构建器只做单栏 / 双栏；更多栏一律 fail-closed 为单栏 + 显式 uncertain 诊断。
MAX_COLUMNS = 2
#: 行排序的"同一行"容差：y0 落在同一个 1pt 桶内的行按 x 排序。
ROW_BUCKET_PT = 1.0

# --- 解析质量阈值 -----------------------------------------------------------
#: 完全无文本的页面比例上限（超过即 fail-fast）。
MAX_BLANK_PAGE_RATIO = 0.5
#: 平均每页可见字符数下限（低于即视为"文本层过稀"，不 OCR）。
#: 只在页数达到 `MIN_PAGES_FOR_SPARSE_CHECK` 时才判定：单页/双页的合法短文档
#: （封面、单页通知）无法与"劣质文本层"区分，此时不做密度断言，只由
#: `no_text_layer` 兜底——不得为了凑密度而伪造或补齐文本。
MIN_AVG_CHARS_PER_PAGE = 24.0
MIN_PAGES_FOR_SPARSE_CHECK = 5

#: PyMuPDF span flags 的粗体位。
_BOLD_FLAG = 16
_RE_DIGITS = re.compile(r"\d+")


class LayoutBuildError(SchemaValidationError):
    """版式构建失败：必须携带显式状态，绝不返回"空布局"冒充成功。"""

    def __init__(self, status: str, message: str) -> None:
        if status not in BUILD_FAILURE_STATUSES:
            raise SchemaValidationError(
                f"未知的构建失败状态 {status!r}，必须属于 {BUILD_FAILURE_STATUSES}")
        self.status = status
        super().__init__(f"[{status}] {message}")


@dataclass(frozen=True)
class LayoutBuildContext:
    """构建上下文：**只有内容身份**，没有 run 元数据。

    刻意**不**接受 `run_id` / `timestamp` / `output_path` / 随机种子之类的字段：
    身份必须只由"哪家公司的哪份文档 + 文件内容哈希 + 引擎与规则版本"决定。
    """

    company_id: str
    document_id: str
    declared_document_version: str | None = None

    def __post_init__(self) -> None:
        for name in ("company_id", "document_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                raise LayoutBuildError(
                    "metadata_missing",
                    f"{name} 必须为非空字符串（版式身份不得缺失），得到 {v!r}")
        if self.declared_document_version is not None and (
                not isinstance(self.declared_document_version, str)
                or not self.declared_document_version.strip()):
            raise LayoutBuildError(
                "metadata_missing",
                f"declared_document_version 若给出必须为非空字符串，"
                f"得到 {self.declared_document_version!r}")

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
            "document_id": self.document_id,
            "declared_document_version": self.declared_document_version,
        }


# ---------------------------------------------------------------------------
# 家具检测（只加标记，绝不删除）
# ---------------------------------------------------------------------------

def page_number_candidate(text: str) -> int | None:
    """页码形态判定：恰好一段 <=4 位数字，其余字符全部是装饰字符。

    只做**形态**判定，不做"跨页重复"判定——页码逐页变化，跨页重复恰恰**不能**
    作为页码证据（见 `_detect_furniture` 的众数偏移规则）。
    """
    compact = "".join(text.split())
    runs = _RE_DIGITS.findall(compact)
    if len(runs) != 1 or len(runs[0]) > PAGE_NUMBER_MAX_DIGITS:
        return None
    rest = _RE_DIGITS.sub("", compact)
    if any(ch not in PAGE_NUMBER_DECORATION_CHARS for ch in rest):
        return None
    return int(runs[0])


def furniture_template(text: str) -> str:
    """归一化模板：折叠空白后把每段数字换成 `#`，用于跨页重复比较。"""
    return _RE_DIGITS.sub("#", " ".join(text.split()))


def _band_of(bbox, height: float) -> str | None:
    """行落在页眉带 / 页脚带 / 都不是。页眉带看下沿（y1），页脚带看上沿（y0）。"""
    if bbox[3] <= height * HEADER_BAND_RATIO:
        return "top"
    if bbox[1] >= height * FOOTER_BAND_RATIO:
        return "bottom"
    return None


def _detect_furniture(pages: list, page_count: int) -> tuple:
    """返回 `(marks, evidence)`。

    `marks`: `{(page_number, line_position): rule_id}` —— 只加标记，行本身保留。
    `evidence`: 每条规则的判定依据（页数、比例、众数偏移、代表位置），供人工复核。
    """
    marks: dict = {}
    evidence: dict = {rule: {} for rule in FURNITURE_RULE_IDS}

    # --- 1. 页码：同一带内"标签 - 物理页"的众数偏移必须同时满足页数、比例、
    #        且标签必须逐页变化（排除跨页固定重复的常数） ---------------------
    for band in ("top", "bottom"):
        candidates: dict = {}
        for page in pages:
            hits = []
            for line in page["lines"]:
                if _band_of(line["bbox"], page["height"]) != band:
                    continue
                value = page_number_candidate(line["text"])
                if value is not None:
                    hits.append((line["engine_index"], value))
            if hits:
                candidates[page["page_number"]] = hits
        # 同一带内一页出现多个候选 → 该页歧义，**不参与证据也不被标记**（fail-closed）
        unambiguous = {p: h[0] for p, h in candidates.items() if len(h) == 1}
        offsets: dict = {}
        for page_number, (_, value) in unambiguous.items():
            key = value - page_number
            offsets[key] = offsets.get(key, 0) + 1
        info = {
            "band": band,
            "candidate_pages": len(candidates),
            "ambiguous_pages": sorted(p for p, h in candidates.items() if len(h) != 1),
            "offset_distribution": sorted(offsets.items(), key=lambda kv: (-kv[1], kv[0])),
            "modal_offset": None,
            "accepted_pages": 0,
            "distinct_labels": 0,
            "page_ratio": 0.0,
        }
        if offsets:
            offset, count = sorted(offsets.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            accepted = {p: v for p, (_, v) in unambiguous.items()
                        if v - p == offset}
            distinct = len(set(accepted.values()))
            ratio = count / page_count if page_count else 0.0
            info.update({
                "modal_offset": offset,
                "accepted_pages": count,
                "distinct_labels": distinct,
                "page_ratio": ratio,
            })
            if (count >= PAGE_NUMBER_MIN_PAGES
                    and ratio >= PAGE_NUMBER_MIN_RATIO
                    and distinct >= PAGE_NUMBER_MIN_DISTINCT):
                for page_number, (position, _) in unambiguous.items():
                    if page_number in accepted and (page_number, position) not in marks:
                        marks[(page_number, position)] = "page_number"
        evidence["page_number"].setdefault("bands", {})[band] = info

    # --- 2. 页眉 / 页脚：同带内跨页重复的模板 ---------------------------------
    for band, rule in (("top", "running_header"), ("bottom", "running_footer")):
        templates: dict = {}
        for page in pages:
            for line in page["lines"]:
                if (page["page_number"], line["engine_index"]) in marks:
                    continue
                if _band_of(line["bbox"], page["height"]) != band:
                    continue
                tpl = furniture_template(line["text"])
                templates.setdefault(tpl, set()).add(page["page_number"])
        accepted: list = []
        for tpl in sorted(templates):
            pages_hit = templates[tpl]
            letters = sum(1 for ch in tpl if ch != "#")
            ratio = len(pages_hit) / page_count if page_count else 0.0
            if (len(pages_hit) >= FURNITURE_REPEAT_MIN_PAGES
                    and ratio >= FURNITURE_REPEAT_MIN_RATIO
                    and letters >= FURNITURE_MIN_TEMPLATE_LETTERS):
                accepted.append({
                    "template": tpl,
                    "page_count": len(pages_hit),
                    "page_ratio": ratio,
                    "template_letters": letters,
                })
        accepted_templates = {a["template"] for a in accepted}
        if accepted_templates:
            for page in pages:
                for line in page["lines"]:
                    if (page["page_number"], line["engine_index"]) in marks:
                        continue
                    if _band_of(line["bbox"], page["height"]) != band:
                        continue
                    if furniture_template(line["text"]) in accepted_templates:
                        marks[(page["page_number"], line["engine_index"])] = rule
        evidence[rule] = {
            "band": band,
            "accepted_templates": accepted,
            "rejected_template_count": len(templates) - len(accepted),
            "min_pages": FURNITURE_REPEAT_MIN_PAGES,
            "min_ratio": FURNITURE_REPEAT_MIN_RATIO,
        }

    return marks, evidence


# ---------------------------------------------------------------------------
# 阅读顺序（确定性全序）
# ---------------------------------------------------------------------------

def _detect_columns(lines: list, width: float, height: float) -> dict:
    """单栏 / 双栏判定。**不依赖 dict 迭代顺序**：所有边界来自排序后的 x 集合。

    双栏必须同时满足：栏间空白 >= `MIN_GUTTER_PT`、两栏各 >= `MIN_COLUMN_LINES` 行、
    两栏行数之和等于全部行数（完全二分）、每栏宽度 <= `MAX_COLUMN_WIDTH_RATIO` 页宽、
    每栏纵向跨度 >= `COLUMN_VERTICAL_SPAN_RATIO` 页高。任何一条不满足就退回单栏，
    并把"存在分栏迹象但未重排"记为显式诊断（内容一律保留）。

    `wide_gutter_count` 只统计**能把全部行完全二分、且两侧各有 >= MIN_COLUMN_LINES
    行**的宽空白：单纯"页眉在左、页码在右"这种坐标分离不算分栏迹象，否则几乎每页
    都会被误标为 uncertain。
    """
    xs = sorted({ln["bbox"][0] for ln in lines} | {ln["bbox"][2] for ln in lines})
    loose = [
        (xs[i + 1] - xs[i], xs[i], xs[i + 1])
        for i in range(len(xs) - 1)
        if xs[i + 1] - xs[i] >= MIN_GUTTER_PT
        and xs[i] <= width * 0.85 and xs[i + 1] >= width * 0.15
    ]
    splits = []
    for gap, g0, g1 in loose:
        left = [ln for ln in lines if ln["bbox"][2] <= g0]
        right = [ln for ln in lines if ln["bbox"][0] >= g1]
        if len(left) < MIN_COLUMN_LINES or len(right) < MIN_COLUMN_LINES:
            continue
        if len(left) + len(right) != len(lines):
            continue
        splits.append((gap, g0, g1, left, right))
    info = {
        "column_count": 1,
        "gutter": None,
        "loose_gutter_count": len(loose),
        "wide_gutter_count": len(splits),
        "split": None,
        "reading_order_uncertain": bool(splits),
        "reason": "wide_gutter_not_reordered" if splits else "no_qualifying_gutter",
    }
    if len(lines) < 2 * MIN_COLUMN_LINES:
        info["reason"] = "too_few_lines"
        return info
    best = None
    for gap, g0, g1, left, right in splits:
        left_w = max(ln["bbox"][2] for ln in left) - min(ln["bbox"][0] for ln in left)
        right_w = max(ln["bbox"][2] for ln in right) - min(ln["bbox"][0] for ln in right)
        if left_w > width * MAX_COLUMN_WIDTH_RATIO:
            continue
        if right_w > width * MAX_COLUMN_WIDTH_RATIO:
            continue
        left_span = (max(ln["bbox"][3] for ln in left)
                     - min(ln["bbox"][1] for ln in left)) / height
        right_span = (max(ln["bbox"][3] for ln in right)
                      - min(ln["bbox"][1] for ln in right)) / height
        if left_span < COLUMN_VERTICAL_SPAN_RATIO:
            continue
        if right_span < COLUMN_VERTICAL_SPAN_RATIO:
            continue
        if best is None or gap > best[0]:
            best = (gap, g0, g1)
    if best is None:
        return info
    gap, g0, g1 = best
    info.update({
        "column_count": 2,
        "gutter": (g0, g1, gap),
        "split": (g0, g1),
        "reading_order_uncertain": False,
        "reason": "two_columns",
    })
    return info


def _order_lines(lines: list, width: float, height: float) -> tuple:
    """返回 `(ordered_lines, columns_info)`；行按确定性全序排列。

    全序键：`(column_index, floor(y0 / ROW_BUCKET_PT), x0, engine_index)`。
    最后一级 `engine_index` 保证"坐标完全相同"时也是**全序**（稳定 tie-break），
    使结果与 dict 迭代顺序、集合顺序完全无关。
    """
    columns = _detect_columns(lines, width, height)
    split = columns["split"]
    ordered = []
    for item in lines:
        if split is not None:
            column_index = 0 if item["bbox"][2] <= split[0] else 1
        else:
            column_index = 0
        ordered.append((column_index, item))
    ordered.sort(key=lambda pair: (
        pair[0],
        math.floor(pair[1]["bbox"][1] / ROW_BUCKET_PT),
        pair[1]["bbox"][0],
        pair[1]["engine_index"],
    ))
    return ordered, columns


# ---------------------------------------------------------------------------
# 提取
# ---------------------------------------------------------------------------

def _span_entries(engine_spans: list) -> list:
    """把引擎 span 转成确定性字典序列，丢弃**零长度文本** span（不丢任何字符）。"""
    out = []
    for sp in engine_spans:
        text = sp.get("text") or ""
        if text == "":
            continue
        out.append({
            "text": text,
            "bbox": tuple(quantize(v) for v in sp["bbox"]),
            "font": sp.get("font") or "unknown",
            "size": quantize(sp.get("size") or 0.0),
            "is_bold": bool(int(sp.get("flags") or 0) & _BOLD_FLAG),
        })
    return out


def _extract_document(doc) -> tuple:
    """逐页提取原始行。返回 `(pages, stats)`；**不做**任何家具或顺序判定。"""
    pages = []
    stats = {
        "engine_lines": 0,
        "dropped_blank_lines": 0,
        "dropped_empty_spans": 0,
        "split_newline_lines": 0,
        "non_text_blocks": 0,
        "violations": [],
    }
    for index in range(doc.page_count):
        page = doc[index]
        mediabox = page.mediabox
        width = quantize(mediabox.width)
        height = quantize(mediabox.height)
        try:
            rotation = int(page.rotation)
        except Exception:  # noqa: BLE001 - 引擎缺字段时按 0 处理并记录
            rotation = 0
        if rotation not in (0, 90, 180, 270):
            stats["violations"].append(
                f"page {index + 1} rotation={rotation} 不在允许取值内")
            rotation = 0
        lines = []
        engine_index = 0
        for block in page.get_text("dict").get("blocks", ()):
            if block.get("type") != 0:
                stats["non_text_blocks"] += 1
                continue
            for engine_line in block.get("lines", ()):
                stats["engine_lines"] += 1
                spans = _span_entries(engine_line.get("spans", ()))
                if not spans:
                    stats["dropped_blank_lines"] += 1
                    continue
                # 版式行不得含换行：按换行切分成多段（保留全部可见字符）
                groups = [[]]
                for sp in spans:
                    parts = sp["text"].split("\n")
                    if len(parts) == 1:
                        groups[-1].append(sp)
                        continue
                    stats["split_newline_lines"] += 1
                    for i, part in enumerate(parts):
                        if i:
                            groups.append([])
                        if part:
                            groups[-1].append({**sp, "text": part})
                line_bbox = tuple(quantize(v) for v in engine_line["bbox"])
                for group in groups:
                    if not group:
                        continue
                    text = "".join(sp["text"] for sp in group)
                    if text.strip() == "":
                        stats["dropped_blank_lines"] += 1
                        continue
                    lines.append({
                        "text": text,
                        "bbox": line_bbox,
                        "spans": group,
                        "engine_index": engine_index,
                    })
                    engine_index += 1
        for position, line in enumerate(lines):
            if line["bbox"][0] < -1.0 or line["bbox"][1] < -1.0:
                stats["violations"].append(
                    f"page {index + 1} line {position} bbox 越出页面（左上）")
            if line["bbox"][2] > width + 1.0 or line["bbox"][3] > height + 1.0:
                stats["violations"].append(
                    f"page {index + 1} line {position} bbox 越出页面（右下）")
            for span_index, sp in enumerate(line["spans"]):
                if not is_finite(sp["size"]) or sp["size"] <= 0:
                    stats["violations"].append(
                        f"page {index + 1} line {position} span {span_index} "
                        f"size={sp['size']!r}")
                if sp["bbox"][2] <= sp["bbox"][0] or sp["bbox"][3] <= sp["bbox"][1]:
                    stats["violations"].append(
                        f"page {index + 1} line {position} span {span_index} "
                        f"bbox 退化 {sp['bbox']!r}")
        pages.append({
            "page_number": index + 1,
            "width": width,
            "height": height,
            "rotation": rotation,
            "lines": lines,
            "text": "".join(line["text"] for line in lines),
        })
    return pages, stats


# ---------------------------------------------------------------------------
# 质量门（fail-fast，不 OCR）
# ---------------------------------------------------------------------------

def _quality_gate(pages: list, stats: dict) -> None:
    page_count = len(pages)
    if page_count == 0:
        raise LayoutBuildError("empty_document", "PDF 没有页面")
    if stats["violations"]:
        raise LayoutBuildError(
            "layout_out_of_page",
            f"提取结果不满足版式不变量，拒绝裁剪或伪造；前 3 条："
            f"{stats['violations'][:3]}（共 {len(stats['violations'])} 条）")
    total = "".join(page["text"] for page in pages)
    if not total.strip():
        raise LayoutBuildError(
            "no_text_layer",
            f"{page_count} 页均无可见文本（疑似扫描件）；本构建器不做 OCR")
    blank_pages = [page["page_number"] for page in pages if not page["text"].strip()]
    if len(blank_pages) / page_count > MAX_BLANK_PAGE_RATIO:
        raise LayoutBuildError(
            "severe_empty_pages",
            f"{len(blank_pages)}/{page_count} 页完全无文本层（上限 "
            f"{MAX_BLANK_PAGE_RATIO}）；前几页：{blank_pages[:5]}")
    average = len(total) / page_count
    if page_count >= MIN_PAGES_FOR_SPARSE_CHECK and average < MIN_AVG_CHARS_PER_PAGE:
        raise LayoutBuildError(
            "quality_below_floor",
            f"{page_count} 页平均可见字符 {average:.2f} 低于下限 "
            f"{MIN_AVG_CHARS_PER_PAGE}（文本层过稀，疑似劣质解析）；"
            f"本构建器不做 OCR 补救，也不补齐文本")
    ratio = bad_char_ratio(total)
    if ratio > MAX_BAD_CHAR_RATIO:
        raise LayoutBuildError(
            "quality_below_floor",
            f"坏字符（替换符/控制符/私用区）比例 {ratio:.4f} 超过上限 "
            f"{MAX_BAD_CHAR_RATIO}；拒绝把劣质解析当成版式层")


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _build_pages(pages: list, marks: dict) -> tuple:
    built = []
    for page in pages:
        ordered, _ = _order_lines(page["lines"], page["width"], page["height"])
        lines = []
        for position, (column_index, item) in enumerate(ordered):
            spans = []
            cursor = 0
            for sp in item["spans"]:
                start, end = cursor, cursor + len(sp["text"])
                spans.append(LayoutSpan(
                    text=sp["text"], bbox=sp["bbox"], font=sp["font"],
                    size=sp["size"], is_bold=sp["is_bold"],
                    char_start=start, char_end=end,
                ))
                cursor = end
            rule = marks.get((page["page_number"], item["engine_index"]))
            lines.append(LayoutLine(
                line_index=position,
                bbox=item["bbox"],
                spans=tuple(spans),
                text=item["text"],
                is_furniture=rule is not None,
                furniture_kind=(FURNITURE_RULE_TO_KIND[rule]
                                if rule is not None else None),
                reading_order=position,
                column_index=column_index,
            ))
        built.append(LayoutPage(
            page_number=page["page_number"],
            width=page["width"],
            height=page["height"],
            rotation=page["rotation"],
            lines=tuple(lines),
            has_text_layer=bool(lines),
        ))
    return tuple(built)


def build_report_from_bytes(data: bytes, context: LayoutBuildContext,
                            *, source_name: str | None = None) -> tuple:
    """由 PDF 字节构建，并**一并**返回构建期只读证据（不修改不可变的 PageLayout）。

    返回 `(layout, report)`。`report` 只承载"为什么这样判定"的审计材料，
    **不进入** `PageLayout` 的任何字段，也不参与身份哈希。
    """
    if _fitz is None:
        raise LayoutBuildError("engine_unavailable",
                               "本机没有可用的 PyMuPDF（fitz），拒绝降级到无版式解析")
    if not isinstance(data, (bytes, bytearray)):
        raise LayoutBuildError("unreadable",
                               f"输入必须是 PDF 字节，得到 {type(data).__name__}")
    raw = bytes(data)
    source_sha256 = hashlib.sha256(raw).hexdigest()
    document_version = "sha256-" + source_sha256[:16]
    declared = context.declared_document_version
    if declared is not None and declared != document_version:
        raise LayoutBuildError(
            "version_mismatch",
            f"调用方声明的 document_version={declared!r} 与文件内容哈希推出的 "
            f"{document_version!r} 不符；版式身份不得与源文件脱钩")
    try:
        doc = _fitz.open(stream=raw, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise LayoutBuildError(
            "unreadable",
            f"无法解析为 PDF（{source_name or '<bytes>'}）："
            f"{type(e).__name__}: {e}") from e
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise LayoutBuildError(
                "encrypted", f"{source_name or '<bytes>'} 为加密 PDF，需要口令")
        pages, stats = _extract_document(doc)
    finally:
        doc.close()
    _quality_gate(pages, stats)
    marks, evidence = _detect_furniture(pages, len(pages))
    built = _build_pages(pages, marks)
    layout = PageLayout.create(
        document_id=context.document_id,
        document_version=document_version,
        company_id=context.company_id,
        source_file_sha256=source_sha256,
        pages=built,
        engine=LAYOUT_ENGINE,
        engine_version=LAYOUT_ENGINE_VERSION,
    )
    report = {
        "source_name": source_name,
        "page_count": len(pages),
        "started_from_bytes": len(raw),
        "extraction_stats": {k: v for k, v in stats.items() if k != "violations"},
        "furniture_evidence": evidence,
        "thresholds": build_thresholds(),
    }
    return layout, report


def build_page_layout_from_bytes(data: bytes, context: LayoutBuildContext,
                                 *, source_name: str | None = None) -> PageLayout:
    """由 PDF 字节构建（路径包装与自检共用同一入口，保证确定性一致）。"""
    return build_report_from_bytes(data, context, source_name=source_name)[0]


def build_page_layout_report(pdf_path, context: LayoutBuildContext) -> tuple:
    """只读打开电子 PDF，返回 `(PageLayout, 构建期审计报告)`。"""
    path = pathlib.Path(pdf_path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise LayoutBuildError(
            "unreadable", f"无法读取 {path}：{type(e).__name__}: {e}") from e
    if not data:
        raise LayoutBuildError("unreadable", f"{path} 为空文件")
    return build_report_from_bytes(data, context, source_name=str(path))


def build_page_layout(pdf_path, context: LayoutBuildContext) -> PageLayout:
    """只读打开电子 PDF 并构建确定性 `PageLayout`（公开主入口）。"""
    return build_page_layout_report(pdf_path, context)[0]


def build_thresholds() -> dict:
    """所有生效阈值的**只读快照**（审计与人工复核用；不参与身份哈希）。"""
    return {
        "header_band_ratio": HEADER_BAND_RATIO,
        "footer_band_ratio": FOOTER_BAND_RATIO,
        "furniture_repeat_min_pages": FURNITURE_REPEAT_MIN_PAGES,
        "furniture_repeat_min_ratio": FURNITURE_REPEAT_MIN_RATIO,
        "furniture_min_template_letters": FURNITURE_MIN_TEMPLATE_LETTERS,
        "page_number_max_digits": PAGE_NUMBER_MAX_DIGITS,
        "page_number_min_pages": PAGE_NUMBER_MIN_PAGES,
        "page_number_min_ratio": PAGE_NUMBER_MIN_RATIO,
        "page_number_min_distinct": PAGE_NUMBER_MIN_DISTINCT,
        "min_gutter_pt": MIN_GUTTER_PT,
        "min_column_lines": MIN_COLUMN_LINES,
        "column_vertical_span_ratio": COLUMN_VERTICAL_SPAN_RATIO,
        "max_column_width_ratio": MAX_COLUMN_WIDTH_RATIO,
        "max_columns": MAX_COLUMNS,
        "row_bucket_pt": ROW_BUCKET_PT,
        "max_blank_page_ratio": MAX_BLANK_PAGE_RATIO,
        "min_avg_chars_per_page": MIN_AVG_CHARS_PER_PAGE,
        "min_pages_for_sparse_check": MIN_PAGES_FOR_SPARSE_CHECK,
        "max_bad_char_ratio": MAX_BAD_CHAR_RATIO,
        "engine": LAYOUT_ENGINE,
        "engine_version": LAYOUT_ENGINE_VERSION,
    }


# ---------------------------------------------------------------------------
# 审计（只读；输出供人工复核）
# ---------------------------------------------------------------------------

def layout_fingerprint(layout: PageLayout) -> str:
    """内容指纹：canonical JSON 的 sha256（与 locator / id 互补的独立复核手段）。"""
    return hashlib.sha256(canonical_json(layout.to_dict()).encode("utf-8")).hexdigest()


def layout_summary(layout: PageLayout) -> dict:
    pages_with_text = [p for p in layout.pages if p.has_text_layer]
    lines = [ln for page in layout.pages for ln in page.lines]
    return {
        "page_layout_locator": layout.page_layout_locator,
        "page_layout_id": layout.page_layout_id,
        "page_layout_fingerprint": layout_fingerprint(layout),
        "company_id": layout.company_id,
        "document_id": layout.document_id,
        "document_version": layout.document_version,
        "source_file_sha256": layout.source_file_sha256,
        "engine": layout.engine,
        "engine_version": layout.engine_version,
        "schema_version": layout.schema_version,
        "normalization_version": layout.normalization_version,
        "page_count": layout.page_count,
        "pages_with_text": len(pages_with_text),
        "empty_pages": [p.page_number for p in layout.pages if not p.has_text_layer],
        "rotated_pages": {str(p.page_number): p.rotation
                          for p in layout.pages if p.rotation != 0},
        "line_count": len(lines),
        "span_count": sum(len(ln.spans) for ln in lines),
        "furniture_line_count": sum(1 for ln in lines if ln.is_furniture),
        "body_line_count": sum(1 for ln in lines if not ln.is_furniture),
        "char_count": sum(len(ln.text) for ln in lines),
        "avg_lines_per_page": round(len(lines) / layout.page_count, 3)
        if layout.page_count else 0.0,
        "build_thresholds": build_thresholds(),
    }


def audit_furniture(layout: PageLayout) -> dict:
    """家具审计：按**规则名**统计页数、比例与代表位置（供人工复核误判）。"""
    lines = [ln for page in layout.pages for ln in page.lines]
    rules: dict = {}
    for rule in FURNITURE_RULE_IDS:
        kind = FURNITURE_RULE_TO_KIND[rule]
        hits = []
        for page in layout.pages:
            for ln in page.lines:
                if ln.furniture_kind == kind:
                    hits.append((page.page_number, ln.line_index, ln.bbox, ln.text))
        page_numbers = sorted({h[0] for h in hits})
        rules[rule] = {
            "schema_kind": kind,
            "line_count": len(hits),
            "page_count": len(page_numbers),
            "page_ratio": round(len(page_numbers) / layout.page_count, 4)
            if layout.page_count else 0.0,
            "pages": page_numbers,
            "samples": [{"page_number": p, "line_index": i,
                         "bbox": list(b), "text": t[:60]}
                        for p, i, b, t in hits[:5]],
        }
    furniture_lines = [ln for ln in lines if ln.is_furniture]
    return {
        "rule_ids": list(FURNITURE_RULE_IDS),
        "rules": rules,
        "totals": {
            "lines": len(lines),
            "furniture_lines": len(furniture_lines),
            "unmarked_lines": len(lines) - len(furniture_lines),
            "furniture_line_ratio": round(len(furniture_lines) / len(lines), 4)
            if lines else 0.0,
            "retained_furniture_chars": sum(len(ln.text) for ln in furniture_lines),
        },
        "policy": {
            "marks_only": True,
            "deletes_text": False,
            "note": "家具检测只加标记；家具行仍完整保留在 PageLayout.lines 内",
        },
    }


def audit_reading_order(layout: PageLayout) -> dict:
    """阅读顺序审计：逐页栏数、分栏迹象与"不确定"页（内容一律保留）。"""
    pages = []
    uncertain = []
    multi_column = []
    for page in layout.pages:
        raw = [{"bbox": ln.bbox, "engine_index": ln.line_index}
               for ln in page.lines]
        columns = _detect_columns(raw, page.width, page.height)
        orders = [ln.reading_order for ln in page.lines]
        entry = {
            "page_number": page.page_number,
            "line_count": len(page.lines),
            "column_count": columns["column_count"],
            "gutter": list(columns["gutter"][:2]) if columns["gutter"] else None,
            "loose_gutter_count": columns["loose_gutter_count"],
            "wide_gutter_count": columns["wide_gutter_count"],
            "reading_order_uncertain": columns["reading_order_uncertain"],
            "reason": columns["reason"],
            "reading_order_unique": len(set(orders)) == len(orders),
            "reading_order_consecutive": orders == list(range(len(orders))),
            "column_indexes": sorted({ln.column_index for ln in page.lines}),
        }
        pages.append(entry)
        if columns["reading_order_uncertain"]:
            uncertain.append(page.page_number)
        if columns["column_count"] > 1:
            multi_column.append(page.page_number)
    return {
        "pages": pages,
        "multi_column_pages": multi_column,
        "reading_order_uncertain_pages": uncertain,
        "thresholds": {
            "min_gutter_pt": MIN_GUTTER_PT,
            "min_column_lines": MIN_COLUMN_LINES,
            "column_vertical_span_ratio": COLUMN_VERTICAL_SPAN_RATIO,
            "max_column_width_ratio": MAX_COLUMN_WIDTH_RATIO,
            "row_bucket_pt": ROW_BUCKET_PT,
        },
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _self_check_pdf_bytes(pages: int = 6) -> bytes:
    """生成内存内的确定性合成 PDF（不落盘、不用临时文件）。"""
    if _fitz is None:  # pragma: no cover
        return b""
    doc = _fitz.open()
    for index in range(1, pages + 1):
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((72.0, 40.0), "SELF CHECK DOCUMENT HEADER", fontsize=9.0)
        for row in range(5):
            page.insert_text((72.0, 140.0 + row * 24.0),
                             f"self check body line {index}-{row}", fontsize=11.0)
        page.insert_text((300.0, 810.0), f"- {index} -", fontsize=9.0)
    data = doc.tobytes()
    doc.close()
    return data


def self_check() -> dict:
    """不读取任何输入文件、不写任何输出的确定性自检（计划 §2.5）。"""
    issues: list[str] = []
    if _fitz is None:  # pragma: no cover
        return {"ok": False, "issues": ["PyMuPDF 不可用"], "engine": LAYOUT_ENGINE}
    context = LayoutBuildContext(company_id="self-check", document_id="SELF_CHECK")
    try:
        data = _self_check_pdf_bytes()
        first = build_page_layout_from_bytes(data, context)
        second = build_page_layout_from_bytes(data, context)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "issues": [f"自检构建失败：{type(e).__name__}: {e}"],
                "engine": LAYOUT_ENGINE}
    if canonical_json(first.to_dict()) != canonical_json(second.to_dict()):
        issues.append("同一输入两次构建的 JSON 不一致")
    if first.page_layout_id != second.page_layout_id:
        issues.append("同一输入两次构建的 page_layout_id 不一致")
    if layout_fingerprint(first) != layout_fingerprint(second):
        issues.append("同一输入两次构建的 fingerprint 不一致")
    if first.document_version != "sha256-" + first.source_file_sha256[:16]:
        issues.append("document_version 未沿用既有身份规则")
    if len(first.furniture_lines("page_number")) != first.page_count:
        issues.append("合成样本的页码家具未被完整识别")
    if not first.furniture_lines("header"):
        issues.append("合成样本的 running header 未被识别")
    for page in first.pages:
        orders = [ln.reading_order for ln in page.lines]
        if orders != list(range(len(orders))):
            issues.append(f"第 {page.page_number} 页 reading_order 不连续")
        for ln in page.lines:
            if ln.text == "" or "\n" in ln.text:
                issues.append(f"第 {page.page_number} 页存在空行或含换行的行")
            if "".join(sp.text for sp in ln.spans) != ln.text:
                issues.append(f"第 {page.page_number} 页 span 文本与行文本不一致")
    return {
        "ok": not issues,
        "issues": issues,
        "engine": LAYOUT_ENGINE,
        "engine_version": LAYOUT_ENGINE_VERSION,
        "normalization_version": V.NORMALIZATION_VERSION,
        "schema_version": V.LAYOUT_SCHEMA_VERSION,
        "thresholds": build_thresholds(),
        "fail_fast_statuses": list(BUILD_FAILURE_STATUSES),
        "furniture_rules": list(FURNITURE_RULE_IDS),
        "persisted_schema_kinds": list(FURNITURE_KINDS),
        "context_fields": [f.name for f in fields(LayoutBuildContext)],
    }


# ---------------------------------------------------------------------------
# 真实 PDF 的只读回读校验（`--validate-only --pdf`）
# ---------------------------------------------------------------------------

#: `--validate-only` 下若调用方未给出身份，使用**明确的合成占位身份**：
#: 校验模式不持久化任何产物，占位身份只在报告里出现，且报告显式标注
#: `identity_is_synthetic=true`，不会被误当成真实文档身份。
VALIDATE_ONLY_COMPANY_ID = "validate-only"
VALIDATE_ONLY_DOCUMENT_ID = "VALIDATE_ONLY"


def validate_layout_readback(layout: PageLayout) -> dict:
    """对**已在内存中构建好**的 `PageLayout` 做只读回读校验。

    只检查版式层自洽，不做任何 IO、不写文件、不写数据库、不连线 TS3：
    物理页号从 1 起连续、每页 `reading_order` 唯一且连续、每行恰好出现一次、
    `span → line → page` 文本一致、行文本与其 span 拼接一致、行/跨度字符区间闭合、
    bbox 有限且在页内、身份字段沿用既有规则。
    """
    issues: list[str] = []
    expected_pages = list(range(1, layout.page_count + 1))
    if [p.page_number for p in layout.pages] != expected_pages:
        issues.append("物理页号不是从 1 起连续编号")
    if len(layout.pages) != layout.page_count:
        issues.append("pages 数量与 page_count 不一致")
    span_total = 0
    line_total = 0
    for page in layout.pages:
        width, height = page.width, page.height
        orders = [ln.reading_order for ln in page.lines]
        if len(set(orders)) != len(orders):
            issues.append(f"p{page.page_number}: reading_order 有重复")
        if sorted(orders) != list(range(len(orders))):
            issues.append(f"p{page.page_number}: reading_order 非连续 0..n-1")
        indexes = [ln.line_index for ln in page.lines]
        if len(set(indexes)) != len(indexes):
            issues.append(f"p{page.page_number}: line_index 有重复")
        for ln in page.lines:
            line_total += 1
            if "".join(sp.text for sp in ln.spans) != ln.text:
                issues.append(f"p{page.page_number}: span 文本与行文本不一致")
            if ln.bbox and any(not math.isfinite(v) for v in ln.bbox):
                issues.append(f"p{page.page_number}: bbox 含非有限值")
            elif ln.bbox:
                left, top, right, bottom = ln.bbox
                if not (0.0 <= left <= right <= width + 0.5
                        and 0.0 <= top <= bottom <= height + 0.5):
                    issues.append(f"p{page.page_number}: bbox 越出页面范围")
            for sp in ln.spans:
                span_total += 1
                if sp.char_start >= sp.char_end:
                    issues.append(f"p{page.page_number}: span 字符区间未闭合")
                if sp.text == "":
                    issues.append(f"p{page.page_number}: span 文本为空")
    if layout.document_version != "sha256-" + layout.source_file_sha256[:16]:
        issues.append("document_version 未沿用既有身份规则")
    if len(layout.source_file_sha256) != 64:
        issues.append("source_file_sha256 长度异常")
    if layout.page_count <= 0:
        issues.append("page_count 必须为正")
    return {
        "ok": not issues,
        "issues": issues[:50],
        "issue_count": len(issues),
        "page_count": layout.page_count,
        "line_count": line_total,
        "span_count": span_total,
        "page_layout_id": layout.page_layout_id,
        "page_layout_fingerprint": layout_fingerprint(layout),
        "document_version": layout.document_version,
        "company_id": layout.company_id,
        "document_id": layout.document_id,
        "writes": [],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit(report: dict, as_json: bool) -> None:
    if as_json:
        print(canonical_json(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))


def _main(argv=None) -> int:
    """`def _main(argv) -> int` + argparse（沿用既有 CLI 约定）。

    三个**互不退化**的入口：

    - `--self-check`：只跑合成内存自检，**不读取任何 PDF**；
    - `--validate-only --pdf <path>`：**真实读取**指定电子 PDF，在内存中构建并
      回读校验 `PageLayout`，**不写**布局文件 / 数据库 / 索引 / 结果目录；
      `--pdf` 不得被静默忽略，缺 `--pdf` 时是参数错误而不是退化成自检；
    - 默认（`--pdf` + `--company-id` + `--document-id`）：正常构建，行为不变。
    """
    parser = argparse.ArgumentParser(
        description="TS2 确定性 PageLayout 构建器（默认只读）")
    parser.add_argument("--validate-only", action="store_true",
                        help="真实读取 --pdf 指定的 PDF，在内存构建并校验 PageLayout；"
                             "不写任何文件")
    parser.add_argument("--self-check", action="store_true",
                        help="只跑合成内存自检（不读取任何 PDF，与 --validate-only 不同入口）")
    parser.add_argument("--pdf", help="要构建/校验的电子 PDF 路径")
    parser.add_argument("--company-id", help="公司身份（正常构建必填）")
    parser.add_argument("--document-id", help="文档身份（正常构建必填）")
    parser.add_argument("--document-version",
                        help="可选：调用方声明的 document_version（必须与文件哈希一致）")
    parser.add_argument("--out", help="PageLayout JSON 输出路径（只在 --pdf 时使用）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    if args.self_check:
        if args.validate_only:
            parser.error("--self-check 与 --validate-only 是两个不同入口，不得同时使用")
        if args.pdf:
            parser.error("--self-check 不读取任何 PDF：请用 --validate-only --pdf")
        if args.out:
            parser.error("--self-check 不写任何输出：不接受 --out")
        report = self_check()
        _emit(report, args.json)
        return 0 if report["ok"] else 1

    if args.validate_only:
        if not args.pdf:
            parser.error(
                "--validate-only 必须同时给出 --pdf <真实电子 PDF 路径>"
                "（只做合成自检请用 --self-check，不得静默退化）")
        if args.out:
            parser.error("--validate-only 不写任何文件：不接受 --out")
        context = LayoutBuildContext(
            company_id=args.company_id or VALIDATE_ONLY_COMPANY_ID,
            document_id=args.document_id or VALIDATE_ONLY_DOCUMENT_ID,
            declared_document_version=args.document_version)
        try:
            layout = build_page_layout(args.pdf, context)
        except LayoutBuildError as e:
            report = {
                "mode": "validate_only",
                "ok": False,
                "pdf": str(args.pdf),
                "failure_status": e.status,
                "failure": str(e),
                "writes": [],
                "note": "已显式 fail-closed；未写任何文件，未做 OCR，未猜测内容",
            }
            _emit(report, args.json)
            return 2
        report = {
            "mode": "validate_only",
            "pdf": str(args.pdf),
            "identity_is_synthetic": not (args.company_id and args.document_id),
            "note": "只读校验：真实读取 PDF 并在内存构建；不写布局文件 / 数据库 / "
                    "索引 / 结果目录",
            **validate_layout_readback(layout),
        }
        _emit(report, args.json)
        return 0 if report["ok"] else 1

    if not args.pdf:
        parser.error("必须给出 --pdf，或使用 --self-check / --validate-only --pdf")
    if not args.company_id or not args.document_id:
        parser.error("--pdf 构建必须同时给出 --company-id 与 --document-id")
    context = LayoutBuildContext(
        company_id=args.company_id, document_id=args.document_id,
        declared_document_version=args.document_version)
    layout = build_page_layout(args.pdf, context)
    summary = layout_summary(layout)
    if args.out:
        pathlib.Path(args.out).write_text(
            canonical_json(layout.to_dict()), encoding="utf-8")
        summary = {**summary, "written": str(args.out)}
    if args.json:
        print(canonical_json(summary))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------------------
# TS4：受信版式 capability（`VerifiedPageLayout`，计划 §18.3.2 / §18.11.2）
# ---------------------------------------------------------------------------
#
# TS4 正式链路的第一步要回答：**"这份 PageLayout 是刚从 PDF 字节重建出来的那一份
# 吗？"** 只比较 `page_layout_id` / `source_file_sha256` 回答不了：调用方可以保住
# PDF 哈希、改掉行与坐标、再把全部派生 ID 与指纹同步重算一遍，于是"身份看起来没变"
# 而版式已经换了（计划 §18.3.2 明列的反例）。因此资格**不来自字段**，而来自本进程
# 的签发登记表：只有 `build_verified_page_layout(raw_pdf, ...)` 从字节构建出来的
# `PageLayout` 才会被登记。
#
# 三条不放松的边界：
#
# 1. 公开签发入口只吃**PDF 字节**，**不接受**调用方给定的 `PageLayout`；
# 2. 历史/夹具路径会拿冻结产物做**对照**，但签发的永远是"由同一份字节重建出来的
#    那一份"，且必须与冻结产物 canonical 逐字节相等；
# 3. `to_dict` / copy / deepcopy / pickle 全部显式拒绝 —— 序列化出去再读回来，就
#    不再是"本进程签发的那一个"。


class VerifiedLayoutError(SchemaValidationError):
    """受信版式签发 / 使用失败（来源不符 / 未签发 / 跨 scope）。"""


class VerifiedPageLayout:
    """**已签发**的版式能力（运行时对象，不可序列化）。

    `layout` 属性给出真实 `PageLayout` 供组合根只读使用；能力本身不参与序列化，
    也不得被当作"权威证明字符串"传递。
    """

    __slots__ = ("_layout", "_source_bytes", "_scope", "_source_kind",
                 "_issuer_version", "_authority_fingerprint", "_root_identity",
                 "__weakref__")

    def __init__(self, *, layout: PageLayout, source_bytes: bytes, scope: str,
                 source_kind: str, issuer_version: str,
                 authority_fingerprint: str, root_identity: dict) -> None:
        self._layout = layout
        #: 构建本版式所用的**同一份** PDF 字节。TS4 handoff 必须由同一 raw source
        #: 重建大纲，因此这里保留字节本身，避免"先构建版式、后重新读盘"，从而排除
        #: 两次读取之间源文件被替换的窗口。字节不进入任何身份 payload（身份只用
        #: `source_file_sha256`）。
        self._source_bytes = source_bytes
        self._scope = scope
        self._source_kind = source_kind
        self._issuer_version = issuer_version
        self._authority_fingerprint = authority_fingerprint
        self._root_identity = dict(root_identity)

    # -- 只读访问 ---------------------------------------------------------

    @property
    def layout(self) -> PageLayout:
        """真实 `PageLayout`（只读使用；改动它不会改变已签发的身份）。"""
        return self._layout

    @property
    def source_bytes(self) -> bytes:
        """构建本版式所用的原始 PDF 字节（只读派生大纲 / 结构快照的唯一来源）。"""
        return self._source_bytes

    @property
    def issuer_scope(self) -> str:
        return self._scope

    @property
    def source_kind(self) -> str:
        return self._source_kind

    @property
    def issuer_version(self) -> str:
        return self._issuer_version

    @property
    def authority_fingerprint(self) -> str:
        return self._authority_fingerprint

    @property
    def root_identity(self) -> dict:
        return dict(self._root_identity)

    def identity(self) -> dict:
        """进入 handoff / input fingerprint 的身份（不含行与坐标明细）。"""
        return {
            "issuer_version": self._issuer_version,
            "scope": self._scope,
            "source_kind": self._source_kind,
            "page_layout_id": self._layout.page_layout_id,
            "page_layout_payload_sha256": sha256_canonical(self._layout.to_dict()),
            "source_file_sha256": self._layout.source_file_sha256,
            "company_id": self._layout.company_id,
            "document_id": self._layout.document_id,
            "document_version": self._layout.document_version,
            "root_identity": dict(self._root_identity),
            "authority_fingerprint": self._authority_fingerprint,
        }

    # -- 反自证 -----------------------------------------------------------

    def to_dict(self) -> dict:
        raise VerifiedLayoutError(
            "VerifiedPageLayout 是运行时能力，不得序列化；从磁盘读回的对象"
            "不是本进程签发的那一个（fail-closed）")

    def __copy__(self):
        raise VerifiedLayoutError("VerifiedPageLayout 不可 copy：副本未在签发登记表中")

    def __deepcopy__(self, memo):
        raise VerifiedLayoutError(
            "VerifiedPageLayout 不可 deepcopy：副本未在签发登记表中")

    def __reduce__(self):
        raise VerifiedLayoutError(
            "VerifiedPageLayout 不可 pickle：反序列化出的对象不是本进程签发的那一个")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<VerifiedPageLayout scope={self._scope!r} "
                f"{self._layout.document_id}/{self._layout.document_version} "
                f"fingerprint={self._authority_fingerprint[:12]}…>")


def _layout_authority_identity(*, scope: str, source_kind: str, layout: PageLayout,
                               issuer_version: str, root_identity: dict) -> str:
    """版式 authority 的封闭 canonical payload（字段不得增删或换序）。"""
    return sha256_canonical({
        "provider_version": V.OUTLINE_STRUCTURE_PROVIDER_VERSION,
        "scope": scope,
        "source_kind": source_kind,
        "issuer_version": issuer_version,
        "source_file_sha256": layout.source_file_sha256,
        "page_layout_id": layout.page_layout_id,
        "page_layout_payload_sha256": sha256_canonical(layout.to_dict()),
        "root_identity": dict(root_identity),
    })


def _context_for(company_id: str, document_id: str,
                 document_version: str | None = None) -> LayoutBuildContext:
    for name, value in (("company_id", company_id), ("document_id", document_id)):
        if not isinstance(value, str) or value == "":
            raise VerifiedLayoutError(f"{name} 必须为非空字符串，得到 {value!r}")
    return LayoutBuildContext(company_id=company_id, document_id=document_id,
                              declared_document_version=document_version)


def _rebuild_layout_from_bytes(raw_pdf, *, company_id: str, document_id: str,
                               document_version: str | None = None
                               ) -> tuple[PageLayout, bytes]:
    """由 PDF 字节重建 `PageLayout`，并跑一次只读回读校验。

    返回 `(layout, 读取到的原始字节)`：调用方需要把**同一份**字节交给后续的重建步骤，
    否则"两次读盘之间源文件被替换"就会成为一个不受检的窗口。
    """
    if isinstance(raw_pdf, (bytes, bytearray)):
        data = bytes(raw_pdf)
        source_name = "<bytes>"
    else:
        path = pathlib.Path(raw_pdf)
        try:
            data = path.read_bytes()
        except OSError as e:
            raise VerifiedLayoutError(f"无法读取 PDF {path}：{e}") from e
        source_name = str(path)
    layout = build_page_layout_from_bytes(
        data, _context_for(company_id, document_id, document_version),
        source_name=source_name)
    report = validate_layout_readback(layout)
    if not report["ok"]:
        raise VerifiedLayoutError(
            f"由 PDF 重建的 PageLayout 未通过只读回读校验（fail-closed）："
            f"{report['issues'][:5]}")
    return layout, data


def _issue_layout(layout: PageLayout, source_bytes: bytes, *, scope: str,
                  source_kind: str, issuer_version: str,
                  root_identity: dict) -> VerifiedPageLayout:
    fp = _layout_authority_identity(
        scope=scope, source_kind=source_kind, layout=layout,
        issuer_version=issuer_version, root_identity=root_identity)
    obj = VerifiedPageLayout(
        layout=layout, source_bytes=source_bytes, scope=scope,
        source_kind=source_kind, issuer_version=issuer_version,
        authority_fingerprint=fp, root_identity=root_identity)
    return _issue_capability(obj, "VerifiedPageLayout", scope)


def build_verified_page_layout(raw_pdf, *, company_id: str, document_id: str,
                               document_version: str | None = None) -> VerifiedPageLayout:
    """**唯一**生产版式签发入口：由 PDF 字节构建并签发 `VerifiedPageLayout`。

    `raw_pdf` 可以是一段 PDF 字节或一个 PDF 路径（两者都会被完整读取）；**不接受**
    调用方给定的 `PageLayout`。`company_id` / `document_id` 是业务标签，PDF 里没有，
    因此必须显式给出；`document_version` 由文件哈希推出，给出时只作**一致性声明**，
    不一致直接拒绝（沿用 `build_report_from_bytes` 的既有规则）。
    """
    layout, data = _rebuild_layout_from_bytes(
        raw_pdf, company_id=company_id, document_id=document_id,
        document_version=document_version)
    return _issue_layout(
        layout, data, scope="live", source_kind="current_store",
        issuer_version=V.VERIFIED_PAGE_LAYOUT_ISSUER_VERSION,
        root_identity={"resolved_pdf_sha256": layout.source_file_sha256})


def _issue_cross_checked_layout(raw_pdf, *, company_id: str, document_id: str,
                                expected_layout: PageLayout, scope: str,
                                source_kind: str, issuer_version: str,
                                root_identity: dict) -> VerifiedPageLayout:
    """**对照签发**：由字节重建，要求与冻结产物 canonical 逐字节相等后才签发。

    这是历史验收 / 版本化夹具的唯一路径。对照对象**只**用于比较，签发的永远是
    "由同一份字节重建出来的那一份"，因此"保住 PDF 哈希却篡改 PageLayout 并重算全部
    身份"在这里必然暴露（§18.3.2 反例三）。
    """
    if scope not in ("pinned_acceptance",):
        raise VerifiedLayoutError(f"对照签发只用于 pinned_acceptance，得到 {scope!r}")
    if not isinstance(expected_layout, PageLayout):
        raise VerifiedLayoutError(
            f"对照对象必须为 PageLayout，得到 {type(expected_layout).__name__}")
    for name in ("company_id", "document_id"):
        mine = getattr(expected_layout, name)
        theirs = company_id if name == "company_id" else document_id
        if mine != theirs:
            raise VerifiedLayoutError(
                f"冻结版式的 {name}={mine!r} 与请求的 {theirs!r} 不一致（fail-closed）")
    layout, data = _rebuild_layout_from_bytes(
        raw_pdf, company_id=company_id, document_id=document_id,
        document_version=expected_layout.document_version)
    if canonical_json(layout.to_dict()) != canonical_json(expected_layout.to_dict()):
        raise VerifiedLayoutError(
            "由同一份 PDF 字节重建的 PageLayout 与冻结产物的 canonical JSON 不相等；"
            "仅 source_file_sha256 相同不足以签发（禁止『保住 PDF 哈希却篡改版式』）"
            "（fail-closed）")
    return _issue_layout(layout, data, scope=scope, source_kind=source_kind,
                         issuer_version=issuer_version, root_identity=root_identity)


if __name__ == "__main__":
    raise SystemExit(_main())

"""TS2 版式层（`build_page_layout`）反例与确定性测试。

按指令 §九 的顺序：**先写本文件的反例与确定性测试，再实现**。十组覆盖：

A. 合成 PDF 的读取自洽：单页 / 连续多页完整读取，`span → line → page` 文本一致，
   bbox 有限且落在页面范围内，line / span 字符区间闭合，行不重复、不丢失。
B. 阅读顺序：单栏、双栏（先栏内自上而下、再栏间自左向右）、相同 y 的稳定
   tie-break、多栏处理不丢行。
C. 家具检测：running header / running footer / 阿拉伯页码 / 装饰页码；**年份、
   表格数字、正文数字不得误判为 page number**；家具被标记保留而不是删除。
D. 旋转页：`rotation` 记录与坐标空间一致（bbox 仍落在页面范围内）。
E. fail-fast：无文本层、严重空页、稀疏文本层、加密、非 PDF、元数据缺失
   —— 必须显式状态失败，不得 OCR，不得返回"空布局"冒充成功。
F. 确定性：同一输入连续两次构建的 JSON / ID / fingerprint 逐字节一致；文件
   内容变化使身份变化；run_id / 时间 / 输出路径变化不改变内容身份；调用方声明
   的 `document_version` 与文件哈希不符必须拒绝。
G. 公司无关：非 300750 fixture 正常通过；生产模块无公司名 / 公司代码 / 固定
   页码 / 表号 / gold 特例、无网络 / LLM / DB / 非确定源；未硬编码 `ALIGN_MIN`。
H. CLI：`--self-check` 与 `--validate-only --pdf` 是**两个互不退化**的入口；
   `--validate-only` 真实读取指定 PDF 并在内存构建 + 回读校验，但**不写**布局
   文件 / 数据库 / 索引 / 结果目录；缺 `--pdf` 是参数错误而不是退化成自检。
I. page-number furniture 能为后续 TOC 映射提供**真实物理页标签**，但本轮不伪造
   `DocumentOutline`。
J. 真实 PDF 全量构建（与合成 fixture **分开报告**）；真实 PDF 缺失时只记录
   `ENVIRONMENT_DISCREPANCY`，不得记为通过，也不得记为代码失败。
L.（TS2.1 P1-1）非重叠 occurrence 分配：重复文本只有页面**确实**出现足够次数时
   才允许解释；唯一可选区间互相重叠时不得重复消费；顺序颠倒允许判列重排；结果
   确定性。失败一律 `unexplained`（fail-closed）。
M.（TS2.1 P1-2 + TS2 最终关闭轮）`engine_artifact` 封闭白名单：只有 `blank` /
   `invisible_codepoint` 两条有版本化依据的规则；`width_fold`（全角/半角等价）
   **已按批准裁决删除** —— 残差 `Ａ` 只要本页别处出现过 `A`、残差 `１２３４` 只要
   别处出现过 `1234`，本轮一律 `unexplained`，不得事后全页搜索消除；缺少一个
   汉字 / 数字 / 英文字母 / 符号——哪怕只有 1 个字符——必须是 `unexplained`。
N.（TS2.1 P1-3 + TS2 最终关闭轮）正式三态与冻结阈值：`ALIGN_MIN` 已冻结为
   `0.90`，判定为 `coverage < 0.90 ⇒ unaligned`、`>= 0.90 且含 unexplained ⇒
   partially_aligned`、`>= 0.90 且无 unexplained ⇒ aligned`，只有 `aligned` 可引用；
   四条不变式（三态守恒 / `citable == aligned` / `aligned_with_unexplained == 0` /
   `below_threshold_but_not_unaligned == 0`）在每个阈值都必须成立。

本模块只做 PageLayout 层判定；不实现、不测试 TS3 的标题树 / OutlineSpan /
TableObject / Retriever / Pack / Writer。
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import pathlib
import shutil
import tempfile

import fitz

import document_structure
from document_structure import layout_builder as LB
from document_structure import schema as S
from document_structure import span_policy as sp
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, canonical_json

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent
_TS2_MODULES = ("normalization.py", "layout_builder.py")

_TS1_MODULES = ("__init__.py", "versions.py", "schema.py", "canonical.py")

_FORBIDDEN_TOKENS = ("300750", "宁德时代", "CATL", "catl", "gold", "answer_key",
                     "fixed_page")

# 生产模块不得出现的非确定 / 越界依赖（layout_builder 必须导入 fitz，故不在此列）。
_BANNED_IMPORTS = (
    "sqlite3", "requests", "httpx", "urllib", "socket", "openai", "anthropic",
    "chromadb", "bocha", "psycopg", "sqlalchemy", "subprocess", "random",
    "uuid", "tempfile", "datetime", "time",
)

_REAL_PDFS = (
    ("300750", "NDSD_2024_year", "NDSD_2024_year.pdf", 229),
    ("300750", "NDSD_2025_year", "NDSD_2025_year.pdf", 232),
    ("300750", "NDSD_KCZ_2026", "NDSD_KCZ_2026.pdf", 141),
)
_SAMPLE_DIR = _REPO_ROOT / "data" / "samples" / "300750" / "announcements"

_A4 = (595.0, 842.0)


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def plan(msg):
    _results["details"].append("SKIP " + msg)
    _results["skipped"] += 1


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:  # noqa: PERF203
        if substr in str(e):
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{e}）")
    except Exception as e:  # noqa: BLE001
        return check(False, f"{msg}（异常类型 {type(e).__name__} 非 {exc.__name__}：{e}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


def must_fail(fn, status, msg):
    """构建必须以指定显式状态 fail-fast。"""
    try:
        fn()
    except LB.LayoutBuildError as e:
        return check(e.status == status,
                     f"{msg}（状态 {e.status!r} vs 期望 {status!r}：{e}）")
    except Exception as e:  # noqa: BLE001
        return check(False, f"{msg}（异常类型 {type(e).__name__} 非 LayoutBuildError：{e}）")
    return check(False, f"{msg}（未 fail-fast）")


def _read_module(name: str) -> str:
    return (_PKG_DIR / name).read_text(encoding="utf-8")


def _imported_modules(source: str) -> set:
    """AST 级导入扫描（比子串匹配可靠：注释与字符串里的词不算依赖）。"""
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


# ---------------------------------------------------------------------------
# 合成 PDF 工厂
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _tmpdir():
    d = tempfile.mkdtemp(prefix="ts2_ev_")
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _make_pdf(path: pathlib.Path, pages_spec) -> pathlib.Path:
    """按 spec 生成电子 PDF。spec 每项：{size, rotation, items=[(x,y,text,size)]}。"""
    doc = fitz.open()
    for spec in pages_spec:
        w, h = spec.get("size", _A4)
        page = doc.new_page(width=w, height=h)
        for item in spec.get("items", ()):
            x, y, text = item[0], item[1], item[2]
            fsize = item[3] if len(item) > 3 else 11.0
            page.insert_text((x, y), text, fontsize=fsize)
        rot = spec.get("rotation", 0)
        if rot:
            page.set_rotation(rot)
    doc.save(str(path))
    doc.close()
    return path


def _body(page_number: int, lines: int = 6, x: float = 72.0, y0: float = 140.0,
          step: float = 22.0, prefix: str = "BODY"):
    return [(x, y0 + i * step, f"{prefix} line {page_number}-{i} of page")
            for i in range(lines)]


def _ctx(company_id: str = "999999", document_id: str = "SYNTH_DOC_2026",
         declared: str | None = None) -> "LB.LayoutBuildContext":
    return LB.LayoutBuildContext(company_id=company_id, document_id=document_id,
                                declared_document_version=declared)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_lines(layout):
    out = []
    for page in layout.pages:
        out.extend(page.lines)
    return out


# ---------------------------------------------------------------------------
# A. 读取自洽
# ---------------------------------------------------------------------------

def _test_synthetic_readback() -> None:
    with _tmpdir() as d:
        one = _make_pdf(d / "one.pdf", [{"items": _body(1, 4)}])
        layout = LB.build_page_layout(one, _ctx())
        check(isinstance(layout, S.PageLayout), "A1 单页 PDF 产出 PageLayout")
        check(layout.page_count == 1 and len(layout.pages) == 1,
              "A2 单页 page_count 与 pages 长度一致")
        check(layout.pages[0].page_number == 1 and layout.pages[0].has_text_layer,
              "A3 首页编号为 1 且 has_text_layer=True")
        check(len(layout.pages[0].lines) == 4, "A4 单页 4 行完整读取")

        many = _make_pdf(d / "many.pdf", [{"items": _body(i)} for i in (1, 2, 3)])
        mlayout = LB.build_page_layout(many, _ctx())
        check(mlayout.page_count == 3 and len(mlayout.pages) == 3,
              "A5 连续多页完整读取")
        check([p.page_number for p in mlayout.pages] == [1, 2, 3],
              "A6 页号从 1 起连续")
        check(all(len(p.lines) == 6 for p in mlayout.pages),
              "A7 每页行数一致（无丢失）")

        # span → line → page 文本一致性
        ok_span = True
        ok_gap = True
        for page in mlayout.pages:
            for line in page.lines:
                if "".join(sp.text for sp in line.spans) != line.text:
                    ok_span = False
                prev = 0
                for sp in line.spans:
                    if sp.text != line.text[sp.char_start:sp.char_end]:
                        ok_span = False
                    if line.text[prev:sp.char_start].strip() != "":
                        ok_gap = False
                    prev = sp.char_end
        check(ok_span, "A8 span 文本拼接逐字符等于行文本")
        check(ok_gap, "A9 span 之间只允许空白空隙")

        # 字符区间闭合
        ok_range = True
        for page in mlayout.pages:
            for line in page.lines:
                for sp in line.spans:
                    if not (0 <= sp.char_start < sp.char_end <= len(line.text)):
                        ok_range = False
                    if sp.char_end - sp.char_start != len(sp.text):
                        ok_range = False
        check(ok_range, "A10 line / span 字符区间闭合")

        # bbox 有限且在页内
        ok_bbox = True
        for page in mlayout.pages:
            for line in page.lines:
                boxes = [line.bbox] + [sp.bbox for sp in line.spans]
                for b in boxes:
                    if len(b) != 4 or any(not isinstance(v, float) for v in b):
                        ok_bbox = False
                        continue
                    x0, y0, x1, y1 = b
                    if not (x1 > x0 and y1 > y0):
                        ok_bbox = False
                    if x0 < -S.BBOX_PAGE_TOLERANCE_PT or y0 < -S.BBOX_PAGE_TOLERANCE_PT:
                        ok_bbox = False
                    if x1 > page.width + S.BBOX_PAGE_TOLERANCE_PT:
                        ok_bbox = False
                    if y1 > page.height + S.BBOX_PAGE_TOLERANCE_PT:
                        ok_bbox = False
        check(ok_bbox, "A11 bbox 有限、非退化且位于页面范围内")

        # 行不重复、不丢失：行文本集合与写入集合一致
        page = mlayout.pages[0]
        expected = {f"BODY line 1-{i} of page" for i in range(6)}
        check({ln.text for ln in page.lines} == expected,
              "A12 行既不重复也不丢失（集合相等）")
        check([ln.line_index for ln in page.lines] == list(range(len(page.lines))),
              "A13 line_index 等于页内位置")
        check([ln.reading_order for ln in page.lines]
              == list(range(len(page.lines))),
              "A14 reading_order 唯一且连续")

        # 每行恰好出现一次
        texts = [ln.text for ln in _all_lines(mlayout)]
        check(len(texts) == len(set(texts)) == 18, "A15 全文行恰好各出现一次")


# ---------------------------------------------------------------------------
# B. 阅读顺序
# ---------------------------------------------------------------------------

def _test_reading_order() -> None:
    with _tmpdir() as d:
        single = _make_pdf(d / "s.pdf", [{"items": _body(1, 5)}])
        layout = LB.build_page_layout(single, _ctx())
        page = layout.pages[0]
        ys = [ln.bbox[1] for ln in page.lines]
        check(ys == sorted(ys), "B1 单栏按几何自上而下排序")
        check(all(ln.column_index == 0 for ln in page.lines),
              "B2 单栏 column_index 全为 0")

        left = [(60.0, 100.0 + i * 60.0, f"LEFT {i}") for i in range(8)]
        right = [(330.0, 100.0 + i * 60.0, f"RIGHT {i}") for i in range(8)]
        two = _make_pdf(d / "two.pdf", [{"items": left + right}])
        l2 = LB.build_page_layout(two, _ctx())
        p2 = l2.pages[0]
        check(len(p2.lines) == 16, "B3 双栏页 16 行全部保留（无丢失）")
        order = [ln.text for ln in p2.lines]
        check(order == [f"LEFT {i}" for i in range(8)]
              + [f"RIGHT {i}" for i in range(8)],
              "B4 双栏：先左栏自上而下，再右栏自上而下")
        check([ln.column_index for ln in p2.lines] == [0] * 8 + [1] * 8,
              "B5 双栏 column_index 正确")
        check([ln.reading_order for ln in p2.lines] == list(range(16)),
              "B6 双栏 reading_order 连续且唯一")
        dt = LB.audit_reading_order(l2)
        check(dt["pages"][0]["column_count"] == 2, "B7 阅读顺序审计记录栏数")
        check(dt["pages"][0].get("reading_order_uncertain") is False,
              "B8 双栏审计未标 uncertain")

        # 相同 y 的稳定 tie-break
        tie_items = [(300.0, 400.0, "TIE B"), (72.0, 400.0, "TIE A"),
                     (180.0, 400.0, "TIE C")]
        tie = _make_pdf(d / "tie.pdf", [{"items": tie_items}])
        t = LB.build_page_layout(tie, _ctx())
        torder = [ln.text for ln in t.pages[0].lines]
        check(torder == ["TIE A", "TIE C", "TIE B"],
              f"B9 相同 y 按 x 稳定 tie-break（得到 {torder}）")
        t2 = LB.build_page_layout(tie, _ctx())
        check([ln.text for ln in t2.pages[0].lines] == torder,
              "B10 相同 y 的 tie-break 可重复")

        # 完全同坐标（同 y 同 x 的两次写入不得丢行、顺序稳定）
        dup = _make_pdf(d / "dup.pdf", [{"items": [(72.0, 200.0, "SAME"),
                                                    (72.0, 200.0, "SAME")]}])
        dp = LB.build_page_layout(dup, _ctx()).pages[0]
        check(len(dp.lines) == 2, "B11 完全同坐标的两次写入都不丢失")


# ---------------------------------------------------------------------------
# C. 家具检测
# ---------------------------------------------------------------------------

def _test_furniture() -> None:
    with _tmpdir() as d:
        n = 6
        # running header
        hdr = _make_pdf(d / "hdr.pdf", [
            {"items": [(72.0, 40.0, "ACME CORP ANNUAL REPORT")] + _body(p)}
            for p in range(1, n + 1)])
        lh = LB.build_page_layout(hdr, _ctx())
        kinds = [ln.furniture_kind for ln in _all_lines(lh) if ln.is_furniture]
        check(kinds.count("header") == n, "C1 running header 在每页都被标记")
        fh = LB.audit_furniture(lh)
        check(fh["rules"]["running_header"]["page_count"] == n,
              "C2 家具审计记录 running_header 页数与规则名")
        check(0.0 < fh["rules"]["running_header"]["page_ratio"] <= 1.0,
              "C3 家具审计给出发生页比例")

        # running footer（无数字 → 不得被当成页码）
        ftr = _make_pdf(d / "ftr.pdf", [
            {"items": [(72.0, 810.0, "CONFIDENTIAL DRAFT")] + _body(p)}
            for p in range(1, n + 1)])
        lf = LB.build_page_layout(ftr, _ctx())
        fk = [ln.furniture_kind for ln in _all_lines(lf) if ln.is_furniture]
        check(fk.count("footer") == n, "C4 running footer 被标记为 footer")
        check("page_number" not in fk,
              "C5 无数字的 running footer 不得被标记为 page_number")

        # 阿拉伯页码（裸数字）
        bare = _make_pdf(d / "bare.pdf", [
            {"items": [(300.0, 810.0, str(p))] + _body(p)}
            for p in range(1, n + 1)])
        lb = LB.build_page_layout(bare, _ctx())
        bk = [ln.furniture_kind for ln in _all_lines(lb) if ln.is_furniture]
        check(bk.count("page_number") == n, "C6 裸阿拉伯页码被识别")

        # 装饰页码
        dec = _make_pdf(d / "dec.pdf", [
            {"items": [(280.0, 810.0, f"- {p} -")] + _body(p)}
            for p in range(1, n + 1)])
        ld = LB.build_page_layout(dec, _ctx())
        dk = [ln.furniture_kind for ln in _all_lines(ld) if ln.is_furniture]
        check(dk.count("page_number") == n, "C7 装饰页码 `- N -` 被识别")
        labels = [S._page_label_of_furniture_text(ln.text)
                  for _, ln in ld.furniture_lines("page_number")]
        check(labels == [str(p) for p in range(1, n + 1)],
              f"C8 装饰页码提供正确的物理页标签（得到 {labels}）")

        # 中文装饰页码
        zh = _make_pdf(d / "zh.pdf", [
            {"items": [(280.0, 810.0, f"第 {p} 页")] + _body(p)}
            for p in range(1, n + 1)])
        lz = LB.build_page_layout(zh, _ctx())
        zk = [ln.text for _, ln in lz.furniture_lines("page_number")]
        check([S._page_label_of_furniture_text(t) for t in zk]
              == [str(p) for p in range(1, n + 1)],
              "C9 中文装饰页码 `第 N 页` 被识别")

        # 年份不得误判为页码（跨页完全重复的常数不是页码）
        yr = _make_pdf(d / "yr.pdf", [
            {"items": [(300.0, 810.0, "2024")] + _body(p)} for p in range(1, n + 1)])
        ly = LB.build_page_layout(yr, _ctx())
        yk = [ln.furniture_kind for ln in _all_lines(ly) if ln.is_furniture]
        check("page_number" not in yk,
              f"C10 跨页重复的年份不得误判为页码（得到 {yk}）")

        # 表格数字 / 正文数字不得误判
        mixed = _make_pdf(d / "mixed.pdf", [
            {"items": ([(300.0, 810.0, f"- {page} -")] + _body(page)
                       + [(300.0, 500.0, "1,234,567"), (300.0, 300.0, "9"),
                          (300.0, 620.0, "2024")])}
            for page in range(1, n + 1)])
        lm = LB.build_page_layout(mixed, _ctx())
        marked = {(ln.furniture_kind, ln.text) for page in lm.pages
                  for ln in page.lines if ln.is_furniture}
        check(("page_number", "1,234,567") not in marked,
              "C11 表格数字（多数字段）不得误判为页码")
        check(not any(t == "9" for _, t in marked),
              "C12 正文中间的单数字不得误判为家具")
        check(not any(t == "2024" for _, t in marked),
              "C13 正文中间的年份不得误判为家具")
        check(("page_number", "- 3 -") in marked,
              "C14 同一页的真实页码仍被识别")

        # 家具被保留而不是删除
        all_texts = [ln.text for ln in _all_lines(lm)]
        check(sum(1 for t in all_texts if t == "1,234,567") == n,
              "C15 家具检测只加标记，绝不删除原文（含未标记行）")
        check(sum(1 for t in all_texts if t == "2024") == n,
              "C16 未判定为家具的正文数字全部保留")
        check(len(all_texts) == n * (6 + 4),
              "C17 页内行数等于原始写入行数（无删除）")

        # 出现页数不足 → fail-closed，不标记
        few = _make_pdf(d / "few.pdf", [{"items": _body(p)} for p in range(1, 7)]
                        + [{"items": [(300.0, 810.0, "1")] + _body(7)}])
        lfew = LB.build_page_layout(few, _ctx())
        check(not lfew.furniture_lines("page_number"),
              "C18 样本页数不足时页码 fail-closed（不猜固定偏移）")


# ---------------------------------------------------------------------------
# D. 旋转页
# ---------------------------------------------------------------------------

def _test_rotation() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "rot.pdf", [{"rotation": 90, "items": _body(1, 3)}])
        layout = LB.build_page_layout(p, _ctx())
        page = layout.pages[0]
        check(page.rotation == 90, "D1 旋转页 rotation 被记录")
        check(page.rotation in S.ROTATIONS, "D2 rotation 属于允许取值")
        check(page.width == 595.0 and page.height == 842.0,
              f"D3 页面尺寸取未旋转的 MediaBox（得到 {page.width}x{page.height}）")
        check(len(page.lines) == 3, "D4 旋转页文本仍被完整读取")
        inside = True
        for ln in page.lines:
            for b in [ln.bbox] + [sp.bbox for sp in ln.spans]:
                if (b[0] < -S.BBOX_PAGE_TOLERANCE_PT
                        or b[1] < -S.BBOX_PAGE_TOLERANCE_PT
                        or b[2] > page.width + S.BBOX_PAGE_TOLERANCE_PT
                        or b[3] > page.height + S.BBOX_PAGE_TOLERANCE_PT):
                    inside = False
        check(inside, "D5 旋转页 bbox 落在页面范围内（坐标空间一致）")


# ---------------------------------------------------------------------------
# E. fail-fast
# ---------------------------------------------------------------------------

def _test_fail_fast() -> None:
    with _tmpdir() as d:
        blank = fitz.open()
        for _ in range(3):
            pg = blank.new_page(width=_A4[0], height=_A4[1])
            pg.draw_rect(fitz.Rect(50, 50, 300, 300), fill=(1, 1, 1))
        blank.save(str(d / "blank.pdf"))
        blank.close()
        must_fail(lambda: LB.build_page_layout(d / "blank.pdf", _ctx()),
                  "no_text_layer", "E1 无文本层 PDF 显式 fail-fast")

        specs = [{"items": _body(p, 8)} for p in (1, 2)] + [{} for _ in range(4)]
        sparse_pages = _make_pdf(d / "emptypages.pdf", specs)
        must_fail(lambda: LB.build_page_layout(sparse_pages, _ctx()),
                  "severe_empty_pages", "E2 严重空页比例过高 fail-fast")

        thin = _make_pdf(d / "thin.pdf",
                         [{"items": [(72.0, 200.0, "HELLO")]}
                          ] + [{"items": [(72.0, 200.0, "a")]} for _ in range(5)])
        must_fail(lambda: LB.build_page_layout(thin, _ctx()),
                  "quality_below_floor", "E3 解析质量不足（文本层过稀）fail-fast")

        enc = fitz.open()
        enc.new_page(width=_A4[0], height=_A4[1]).insert_text((72, 200), "SECRET")
        enc.save(str(d / "enc.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256,
                 owner_pw="o", user_pw="u")
        enc.close()
        must_fail(lambda: LB.build_page_layout(d / "enc.pdf", _ctx()),
                  "encrypted", "E4 加密 PDF 显式 fail-fast")

        (d / "notpdf.pdf").write_bytes(b"this is not a pdf at all")
        must_fail(lambda: LB.build_page_layout(d / "notpdf.pdf", _ctx()),
                  "unreadable", "E5 非 PDF 文件显式 fail-fast")
        must_fail(lambda: LB.build_page_layout(d / "missing.pdf", _ctx()),
                  "unreadable", "E6 文件不存在显式 fail-fast")

        good = _make_pdf(d / "good.pdf", [{"items": _body(1)}])
        must_fail(lambda: LB.build_page_layout(good, _ctx(document_id="")),
                  "metadata_missing", "E7 缺失 document_id 显式 fail-fast")
        must_fail(lambda: LB.build_page_layout(good, _ctx(company_id="")),
                  "metadata_missing", "E8 缺失 company_id 显式 fail-fast")
        check(LB.LayoutBuildError.__mro__[1] is SchemaValidationError,
              "E9 构建失败类型继承结构校验错误")
        check(set(LB.BUILD_FAILURE_STATUSES) >= {"no_text_layer",
                                                 "severe_empty_pages",
                                                 "quality_below_floor",
                                                 "encrypted", "unreadable",
                                                 "metadata_missing"},
              "E10 失败状态词表显式声明")

        # 质量谓词可直接验证（合成 U+FFFD 不可行，故单测谓词本身）
        check(LB.bad_char_ratio("��ab") == 0.5,
              "E11 坏字符比例谓词可判定")
        check(LB.bad_char_ratio("正常文本 abc 123") == 0.0,
              "E12 正常文本坏字符比例为 0")
        check(LB.bad_char_ratio("��") >= LB.MAX_BAD_CHAR_RATIO,
              "E13 坏字符比例阈值可触发")


# ---------------------------------------------------------------------------
# F. 确定性
# ---------------------------------------------------------------------------

def _test_determinism() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "det.pdf", [{"items": _body(i)} for i in (1, 2)])
        a = LB.build_page_layout(p, _ctx())
        b = LB.build_page_layout(p, _ctx())
        check(canonical_json(a.to_dict()) == canonical_json(b.to_dict()),
              "F1 同一输入连续两次构建 JSON 逐字节一致")
        check(a.page_layout_id == b.page_layout_id, "F2 两次构建 page_layout_id 一致")
        check(a.page_layout_locator == b.page_layout_locator,
              "F3 两次构建 locator 一致")
        check(S.derive_page_layout_id(
            page_layout_locator=a.page_layout_locator,
            source_file_sha256=a.source_file_sha256, pages=a.pages)
            == a.page_layout_id, "F4 page_layout_id 可由 locator 重算")
        check(S.derive_page_layout_locator(
            company_id=a.company_id, document_id=a.document_id,
            document_version=a.document_version, engine=a.engine,
            engine_version=a.engine_version, schema_version=a.schema_version,
            normalization_version=a.normalization_version)
            == a.page_layout_locator, "F4b locator 可由参数重算")
        check(LB.layout_fingerprint(a) == LB.layout_fingerprint(b),
              "F5 两次构建 fingerprint 一致")
        check(len(LB.layout_fingerprint(a)) == 64, "F6 fingerprint 为 sha256 十六进制")

        # 输出路径 / run 上下文不进入身份
        alt = d / "sub" / "deep" / "det_copy.pdf"
        alt.parent.mkdir(parents=True)
        shutil.copyfile(p, alt)
        c = LB.build_page_layout(alt, _ctx())
        check(c.source_file_sha256 == a.source_file_sha256
              and c.page_layout_locator == a.page_layout_locator
              and c.page_layout_id == a.page_layout_id
              and canonical_json(c.to_dict()) == canonical_json(a.to_dict()),
              "F7 输出路径不同不改变内容身份")

        # 文件内容变化 → 身份变化
        p2 = _make_pdf(d / "det2.pdf", [{"items": _body(i)} for i in (1, 2, 3)])
        e = LB.build_page_layout(p2, _ctx())
        check(e.source_file_sha256 != a.source_file_sha256,
              "F8 文件内容变化改变 source_file_sha256")
        check(e.document_version != a.document_version
              and e.page_layout_locator != a.page_layout_locator
              and e.page_layout_id != a.page_layout_id,
              "F9 文件内容变化改变 document_version / locator / id")
        check(a.document_version == "sha256-" + a.source_file_sha256[:16],
              "F10 document_version 沿用既有身份规则")

        # 调用方声明的版本必须与文件哈希一致
        must_fail(lambda: LB.build_page_layout(
            p, _ctx(declared="sha256-0000000000000000")),
            "version_mismatch", "F11 声明的 document_version 与文件不符时拒绝")
        ok_ctx = _ctx(declared=a.document_version)
        check(LB.build_page_layout(p, ok_ctx).page_layout_id == a.page_layout_id,
              "F12 声明版本与文件一致时构建通过")

        # run_id / 时间 / 输出路径不得成为入参
        for bad in ("run_id", "timestamp", "output_path", "run_dir", "seed"):
            try:
                LB.LayoutBuildContext(company_id="1", document_id="D", **{bad: "x"})
                check(False, f"F13 构建上下文不接受 {bad}")
            except TypeError:
                check(True, f"F13 构建上下文不接受 {bad}")

        check(LB.self_check().get("ok") is True, "F14 self_check 通过")
        check(isinstance(LB.self_check(), dict), "F15 self_check 返回结构化结果")


# ---------------------------------------------------------------------------
# G. 公司无关
# ---------------------------------------------------------------------------

def _test_company_independence() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "other.pdf", [
            {"items": [(72.0, 40.0, "ZZZ HOLDINGS PLC 2026 INTERIM REPORT")]
             + [(72.0, 140.0 + i * 30.0, f"Item {i} of the interim report")
                for i in range(6)]
             + [(300.0, 810.0, f"- {n} -")]}
            for n in range(1, 7)])
        layout = LB.build_page_layout(
            p, LB.LayoutBuildContext(company_id="888888",
                                     document_id="OTHER_ISSUER_2026"))
        check(layout.page_count == 6 and layout.company_id == "888888",
              "G1 非 300750 fixture 正常构建")
        check(len(layout.furniture_lines("page_number")) == 6,
              "G2 非 300750 fixture 的页码同样被识别")
        check(len(layout.furniture_lines("header")) == 6,
              "G3 非 300750 fixture 的页眉同样被识别")

        for name in _TS2_MODULES:
            src = _read_module(name)
            hits = [t for t in _FORBIDDEN_TOKENS if t in src]
            check(not hits, f"G4 {name} 无公司名 / 代码 / gold / 固定页码特例（{hits}）")
        for name in _TS2_MODULES:
            src = _read_module(name)
            hits = sorted(_imported_modules(src) & set(_BANNED_IMPORTS))
            check(not hits, f"G5 {name} 无非确定 / 网络 / LLM / DB 依赖（{hits}）")
            check("getenv" not in src and "environ" not in src,
                  f"G6 {name} 不读取环境变量")
        for name in _TS2_MODULES:
            src = _read_module(name)
            check("ALIGN_MIN" not in src.replace("ALIGN_MIN_", ""),
                  f"G7 {name} 不硬编码 ALIGN_MIN")
        check(V.ALIGN_MIN == 0.90,
              "G8 ALIGN_MIN 已由用户 + Codex 冻结为单一阈值 0.90")
        check(isinstance(V.ALIGN_MIN, float),
              "G8b ALIGN_MIN 冻结值必须是 float（不得是 None / bool）")
        # G9：`SPAN_CONFIDENCE_MIN` 的**阶段口径**由单一真值表派生，不再写死 None。
        # 硬编码 "必须仍是 None" 会把"升到 B 阶段"变成一次测试失败，从而无法区分
        # "实现坏了"和"阶段换了"；派生式断言在 A/B 两阶段都成立，且仍能抓住
        # "改了阈值却忘了同步完成资格开关"这类真实缺陷。
        gate = sp.ab_gate_truth_table()
        check(gate["stage"] == ("threshold_enabled"
                                if V.SPAN_CONFIDENCE_MIN is not None
                                else "distribution_only"),
              "G9 SPAN_CONFIDENCE_MIN 的实际取值必须与 A/B 真值表阶段一致")
        check(gate["threshold_decided"] is (V.SPAN_CONFIDENCE_MIN is not None),
              "G9b 阈值开关必须由 SPAN_CONFIDENCE_MIN 派生，不得写死")
        check(gate["completion_enabled"] is gate["threshold_decided"]
              and gate["set_complete_supported"] is gate["threshold_decided"],
              "G9c 完成资格与 set_complete 支持必须与阈值同源")
        check(not any("ALIGN_MIN_DECIDED" in _read_module(n)
                      for n in _TS1_MODULES + _TS2_MODULES),
              "G10 生产模块不得引入 ALIGN_MIN_DECIDED 放行开关")

        # 规则名与 schema 词表的关系必须显式（不得偷偷改 schema 词表）
        check(LB.FURNITURE_RULE_IDS == ("running_header", "running_footer",
                                        "page_number"),
              "G11 规则名使用任务要求的 running_* 词表")
        check(all(k in S.FURNITURE_KINDS
                  for k in LB.FURNITURE_RULE_TO_KIND.values()),
              "G12 持久化的家具类型仍落在 schema 词表内（未改 schema）")
        check(S.FURNITURE_KINDS == ("header", "footer", "page_number",
                                    "watermark", "other"),
              "G13 schema 家具词表未被本轮改动")

        # 规则版本复用既有版本常量，不得内联版本字面量
        src = _read_module("layout_builder.py")
        check(f'"{V.LAYOUT_ENGINE_VERSION}"' not in src.replace(
            "'" + V.LAYOUT_ENGINE_VERSION + "'", ""),
              "G14 layout_builder 不内联引擎版本字面量")
        check("LAYOUT_ENGINE_VERSION" in src or "LAYOUT_ENGINE" in src,
              "G15 layout_builder 引用既有版本常量")


# ---------------------------------------------------------------------------
# H. CLI --validate-only 无副作用
# ---------------------------------------------------------------------------

def _snapshot(paths):
    out = {}
    for p in paths:
        if p.is_dir():
            out[str(p)] = sorted(x.name for x in p.iterdir())
        elif p.exists():
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns, _sha256(p))
        else:
            out[str(p)] = None
    return out


def _quiet_main(argv) -> int:
    """调用 CLI 但不把自检输出灌进评测报告（stdout 与 stderr 都吞掉）。"""
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return LB._main(argv)


def _quiet_main_report(argv) -> tuple:
    """调用 CLI 并返回 `(退出码, stdout 文本)`，用于检查真实读取了哪份 PDF。"""
    import io
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(
                io.StringIO()):
            rc = LB._main(argv)
    except SystemExit as e:  # argparse 的参数错误
        return e.code, out.getvalue()
    return rc, out.getvalue()


def _test_cli_validate_only() -> None:
    """H（P2）：`--self-check` 与 `--validate-only --pdf` 是**两个不同入口**。"""
    db_dir = _REPO_ROOT / "data"
    watch = [db_dir / "evidence.db", db_dir / "financial_v2.db",
             _REPO_ROOT / "evaluation" / "results"]
    before = _snapshot(watch)

    rc = _quiet_main(["--self-check"])
    check(rc == 0, f"H1 --self-check 退出码为 0（得到 {rc}）")
    check(before == _snapshot(watch), "H2 --self-check 无副作用")

    # `--validate-only` 缺 --pdf：必须是参数错误，不得静默退化成 self-check。
    rc, _ = _quiet_main_report(["--validate-only"])
    check(rc not in (0, None), f"H3 --validate-only 缺 --pdf 是参数错误（rc={rc}）")
    rc, _ = _quiet_main_report(["--self-check", "--validate-only"])
    check(rc not in (0, None), f"H4 --self-check 与 --validate-only 不得同时用（rc={rc}）")
    rc, _ = _quiet_main_report(["--self-check", "--pdf", "x.pdf"])
    check(rc not in (0, None), f"H5 --self-check 不接受 --pdf（不得静默忽略，rc={rc}）")

    with _tmpdir() as d:
        pdf = _make_pdf(d / "real.pdf",
                        [{"items": [(72.0, 40.0, "ACME HEADER")] + _body(n, 6)
                          + [(300.0, 810.0, f"- {n} -")]} for n in range(1, 7)])
        # H6~H9：真实读取 PDF 并构建、验证，且零写入。
        rc, text = _quiet_main_report(
            ["--validate-only", "--pdf", str(pdf), "--json"])
        check(rc == 0, f"H6 --validate-only --pdf 真实构建成功（rc={rc}）")
        report = json.loads(text)
        check(report.get("mode") == "validate_only",
              "H7 报告显式标注 mode=validate_only（与 self-check 不同入口）")
        check(report.get("page_count") == 6,
              f"H8 validate-only 确实读取了该 PDF（page_count="
              f"{report.get('page_count')}）")
        check(report.get("writes") == []
              and report.get("identity_is_synthetic") is True,
              "H9 validate-only 声明零写入且身份为合成占位")
        check(not (d / "out.json").exists()
              and before == _snapshot(watch),
              "H10 validate-only 未创建输出文件，数据库/结果目录未变")

        # H11：--out 与 --validate-only 互斥（不得偷偷落盘）。
        out_path = d / "should_not_exist.json"
        rc, _ = _quiet_main_report(
            ["--validate-only", "--pdf", str(pdf), "--out", str(out_path)])
        check(rc not in (0, None) and not out_path.exists(),
              f"H11 --validate-only 拒绝 --out 且不落盘（rc={rc}）")

        # H12：同一 PDF 走 self-check 时**不**读它（report 里没有 page_count）。
        rc, text = _quiet_main_report(["--self-check", "--json"])
        self_report = json.loads(text)
        check(rc == 0 and "page_count" not in self_report,
              "H12 self-check 不读取 PDF（报告不含 page_count）")

        # H13：无文本层 PDF 必须 fail-closed 且给出显式状态，不是 traceback。
        blank = fitz.open()
        for _ in range(3):
            pg = blank.new_page(width=595.0, height=842.0)
            pg.draw_rect(fitz.Rect(50, 50, 300, 300), fill=(1, 1, 1))
        blank_path = d / "blank.pdf"
        blank.save(str(blank_path))
        blank.close()
        rc, text = _quiet_main_report(
            ["--validate-only", "--pdf", str(blank_path), "--json"])
        payload = json.loads(text)
        check(rc not in (0, None) and payload.get("ok") is False,
              f"H13 无文本层 PDF fail-closed（rc={rc}）")
        check(payload.get("failure_status") == "no_text_layer",
              f"H14 失败带显式状态 no_text_layer（得到 "
              f"{payload.get('failure_status')!r}）")

        # H14b：无 PDF 路径 / 目录路径同样 fail-closed，不得抛裸异常。
        rc, text = _quiet_main_report(
            ["--validate-only", "--pdf", str(d / "nope.pdf"), "--json"])
        payload = json.loads(text)
        check(rc not in (0, None) and payload.get("ok") is False
              and payload.get("failure_status") in LB.BUILD_FAILURE_STATUSES,
              f"H14b 不存在的 PDF fail-closed（{payload.get('failure_status')!r}）")

        # H15：损坏 / 非 PDF 文件同样 fail-closed。
        broken = d / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 not really a pdf")
        rc, text = _quiet_main_report(
            ["--validate-only", "--pdf", str(broken), "--json"])
        payload = json.loads(text)
        check(rc not in (0, None) and payload.get("ok") is False
              and payload.get("failure_status") in LB.BUILD_FAILURE_STATUSES,
              f"H15 损坏 PDF fail-closed 且状态属于显式词表"
              f"（{payload.get('failure_status')!r}）")

    check(before == _snapshot(watch),
          "H16 全部 CLI 调用后数据库 / 结果目录仍未变化")


# ---------------------------------------------------------------------------
# L（P1-1）：非重叠 occurrence 分配 —— 反例优先
# ---------------------------------------------------------------------------

def _assign(compact: str, page_text: str, min_piece: int = 2) -> dict:
    import evaluation.run_tree_layout_acceptance as RA
    return RA.displaced_evidence(compact, page_text, [], min_piece=min_piece)


def _test_nonoverlapping_assignment() -> None:
    """重复文本 / 重叠子串**不得**被重复消费；顺序颠倒允许判 column reorder。"""
    # L1：块里两次「主营业务」，页面只有一次 -> 不得全部解释。
    ev = _assign("主营业务主营业务", "xx主营业务yy")
    check(ev["pieces"] == [] and ev["leftover"] != "",
          "L1 页面只有一次「主营业务」时不得全部解释")
    check(ev["reason"] == "no_injective_assignment"
          and ev["no_injective_assignment"] is True,
          f"L1b 失败原因是非重叠映射不可建立（得到 {ev['reason']!r}）")
    check(ev["occurrence_deficit"].get("主营业务") == {"needed": 2,
                                                     "available": 1},
          f"L1c 报告重复文本的 occurrence 缺口（{ev['occurrence_deficit']}）")

    # L2：页面确有两次 -> 允许分别绑定到两个**不重叠**区间。
    ev = _assign("主营业务主营业务", "主营业务aa主营业务")
    spans = [(p["page_start"], p["page_end"]) for p in ev["pieces"]]
    check(ev["leftover"] == "" and len(ev["pieces"]) == 2,
          f"L2 页面确有两次时允许分别绑定（pieces={len(ev['pieces'])}）")
    check(len(spans) == 2 and spans[0][1] <= spans[1][0],
          f"L2b 两个来源区间两两不重叠（{spans}）")
    check(all(p["block_start"] < p["block_end"] for p in ev["pieces"]),
          "L2c 每个片段都带块内区间（可复核切分）")

    # L3：A/B 在页面中顺序颠倒 -> 允许判 column reorder。
    ev = _assign("BBBAAAA", "AAAAzzzBBB")
    check(ev["leftover"] == "" and len(ev["pieces"]) == 2,
          f"L3 列顺序重排（页面中 B 在 A 之后）允许被解释（pieces="
          f"{len(ev['pieces'])}）")
    check(ev["pieces"][0]["page_start"] > ev["pieces"][1]["page_start"],
          f"L3b 页面来源顺序与块内顺序相反也被接受"
          f"（{ev['pieces'][0]['page_start']} > "
          f"{ev['pieces'][1]['page_start']}）")

    # L4：重叠子串不得重复消费 —— 唯一可选的两个区间互相重叠时必须失败。
    #     块 "ABCDBCDE" 恰可切成 "ABCD"(页中 [0,4)) + "BCDE"(页中 [1,5))。
    ev = _assign("ABCDBCDE", "ABCDE", min_piece=4)
    check(ev["pieces"] == [] and ev["leftover"] != "",
          "L4 唯一可选来源区间互相重叠时不得重复消费")
    check(ev["reason"] == "no_injective_assignment",
          f"L4b 判定为非重叠映射失败（得到 {ev['reason']!r}）")

    # L5：同样内容，页面里另有一份独立副本 -> 才允许解释。
    ev = _assign("ABCDBCDE", "ABCDE..BCDE", min_piece=4)
    check(ev["leftover"] == "" and len(ev["pieces"]) == 2,
          f"L5 页面存在独立第二份副本时允许解释（pieces={len(ev['pieces'])}）")
    check(ev["pieces"][0]["page_end"] <= ev["pieces"][1]["page_start"],
          f"L5b 选中的两个来源区间不重叠"
          f"（{[ (p['page_start'], p['page_end']) for p in ev['pieces'] ]}）")

    # L6：确定性 —— 同一输入多次运行结果逐字节一致。
    first = json.dumps(_assign("主营业务主营业务", "主营业务aa主营业务"),
                       ensure_ascii=False, sort_keys=True)
    again = json.dumps(_assign("主营业务主营业务", "主营业务aa主营业务"),
                       ensure_ascii=False, sort_keys=True)
    third = json.dumps(_assign("主营业务主营业务", "主营业务aa主营业务"),
                       ensure_ascii=False, sort_keys=True)
    check(first == again == third, "L6 相同输入多次运行结果逐字节一致")

    # L7：无法切分时报 no_partition，且不谎报可定位字符。
    ev = _assign("营业収入XYZQQ", "完全不相干的页面文本", min_piece=2)
    check(ev["reason"] == "no_partition" and ev["pieces"] == [],
          f"L7 完全无法切分时报 no_partition（得到 {ev['reason']!r}）")


# ---------------------------------------------------------------------------
# M（P1-2）：engine artifact 封闭白名单 —— 反例优先
# ---------------------------------------------------------------------------

def _classify(layout, residue: str):
    import evaluation.run_tree_layout_acceptance as RA
    text = RA.page_layout_text(layout, 3)
    lines = RA.page_lines(layout, 3)
    _, spans = RA.page_text_spans(lines)
    return RA.classify_residue(layout, 3, text, spans, residue, lines)


#: 白名单**之外**的可见残差（含单字符）：一律必须 `unexplained`。
_NON_ARTIFACT_RESIDUES = (("汉字", "收"), ("数字", "7"), ("英文字母", "Z"),
                          ("标点", "。"), ("省略号", "…"),
                          ("私用区字形", chr(0xE000)), ("未知符号", "※"))

#: 有版本化依据的不可见字符：只有这些可以判 engine artifact。
_INVISIBLE_RESIDUES = (("软连字符", chr(0x00AD)), ("零宽空格", chr(0x200B)),
                       ("零宽非连接符", chr(0x200C)), ("零宽连接符", chr(0x200D)),
                       ("BOM", chr(0xFEFF)))


def _test_engine_artifact_whitelist() -> None:
    """只有版本化依据的白名单条目可以判 engine artifact；
    `width_fold`（全页任意搜索）按批准裁决删除，全角/半角差异一律 unexplained。"""
    import evaluation.run_tree_layout_acceptance as RA
    from document_structure.normalization import fold

    check(RA.ENGINE_ARTIFACT_RULES == ("blank", "invisible_codepoint"),
          f"M1 白名单恰为两条封闭规则（{RA.ENGINE_ARTIFACT_RULES}）")
    check("width_fold" not in RA.ENGINE_ARTIFACT_RULES,
          "M1b width_fold 已按批准裁决从白名单删除")
    check(not ({"single_char", "symbol_only"} & set(RA.ENGINE_ARTIFACT_RULES)),
          "M2 旧规则 single_char / symbol_only 已从白名单删除")
    check(RA.INVISIBLE_CODEPOINTS == (0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF),
          "M3 白名单依据的不可见码位来自版本化常量，未就地放宽")
    check(RA.NUMERIC_PIECE_MIN_LEN >= 8 and RA.COLUMN_REORDER_MIN_PIECE_LEN >= 4,
          "M4 白名单之外还有片段长度下界（单字符不可能靠巧合命中通过）")

    # M4b：接口层面就没有"事后全页搜索"的可能 —— 判定函数**不接受**任何页面文本。
    import inspect
    params = list(inspect.signature(RA.engine_artifact_subkind).parameters)
    check(params == ["compact"],
          f"M4b engine_artifact_subkind 只接受残差本身（得到 {params}）")
    src = inspect.getsource(RA.classify_residue)
    check("fold(" not in src,
          "M4c classify_residue 内不得再出现 fold(...) 的事后全页搜索")

    with _tmpdir() as d:
        # 页面**故意**同时含半角 "A"（"ACME"）与半角 "1234"，使"残差折叠后
        # 能在本页命中"这一巧合确实成立 —— 反例才有意义。补充行按页变化，
        # 避免被家具检测当成重复模板行。
        pdf = _make_pdf(d / "cls.pdf", [
            {"items": [(72.0, 40.0, "ACME CORP ANNUAL REPORT")]
             + _body(n, 6) + [(300.0, 810.0, f"- {n} -"),
                              (72.0, 700.0, f"SUBTOTAL 1234 UNITS PAGE {n}")]}
            for n in range(1, 7)])
        layout = LB.build_page_layout(pdf, _ctx())
        text = RA.page_layout_text(layout, 3)
        lines = RA.page_lines(layout, 3)
        _, spans = RA.page_text_spans(lines)
        folded = fold(text)   # 仅用于证明"旧的全页搜索本来会命中"，不参与分类

        # M5：白名单函数对可见字符（哪怕只有 1 个、哪怕页面上确实有同形字符）
        #     必须返回 None —— 不得靠"长度 ≤1"或"全是符号"来放行。
        for label, residue in _NON_ARTIFACT_RESIDUES:
            check(RA.engine_artifact_subkind(residue) is None,
                  f"M5 {label}（{residue!r}）不在 artifact 白名单内")
        for label, residue in _INVISIBLE_RESIDUES:
            check(RA.engine_artifact_subkind(residue) == "invisible_codepoint",
                  f"M6 已定义的{label}才判 invisible_codepoint")

        # M7（指令 §二 强制反例 1）：单个全角字符 `Ａ`，本页别处确有半角 `A`。
        #     前置条件先证明"旧路径本来会命中"，再证明本轮仍判 unexplained。
        check(fold("Ａ") in folded,
              "M7a 前置条件：`Ａ` 折叠后在本页**可命中**（旧 width_fold 规则本会放行）")
        check(RA.engine_artifact_subkind("Ａ") is None,
              "M7b 单个全角 `Ａ`（本页别处有 `A`）本轮必须不被判 artifact")
        klass, reason, subkind = RA.classify_residue(
            layout, 3, text, spans, "Ａ", lines)
        check(klass == "unexplained",
              f"M7c 端到端：`Ａ` 必须判 unexplained（得到 {klass}/{subkind}：{reason}）")

        # M8（指令 §二 强制反例 2）：全角数字 `１２３４`，本页别处确有 `1234`。
        check(fold("１２３４") in folded,
              "M8a 前置条件：`１２３４` 折叠后在本页**可命中**")
        check(RA.engine_artifact_subkind("１２３４") is None,
              "M8b 全角数字 `１２３４` 本轮必须不被判 artifact")
        klass, reason, subkind = RA.classify_residue(
            layout, 3, text, spans, "１２３４", lines)
        check(klass == "unexplained",
              "M8c 端到端：`１２３４` 必须判 unexplained（得到 "
              f"{klass}/{subkind}：{reason}）")

        # M9（指令 §二 强制反例 3）：`Ａ股` —— 无同位置可核实的映射，一律 unexplained。
        check(fold("Ａ股") not in folded,
              "M9a 前置条件：`Ａ股` 折叠后在本页**不可**命中（连巧合都没有）")
        check(RA.engine_artifact_subkind("Ａ股") is None,
              "M9b `Ａ股` 必须不被判 artifact")
        klass, reason, subkind = RA.classify_residue(
            layout, 3, text, spans, "Ａ股", lines)
        check(klass == "unexplained",
              f"M9c 端到端：`Ａ股` 必须判 unexplained（得到 {klass}/{subkind}：{reason}）")

        # M10：软连字符 / 零宽的判定**不因本轮删除 width_fold 而放松**。
        for label, residue in _INVISIBLE_RESIDUES:
            klass, _, subkind = RA.classify_residue(
                layout, 3, text, spans, residue, lines)
            check(klass == "engine_artifact"
                  and subkind == "invisible_codepoint",
                  f"M10 端到端{label}仍判 invisible_codepoint（得到 {klass}/{subkind}）")

        # M11：端到端分类 —— 可见单字符一律 unexplained，不可见字符才 artifact。
        for label, residue in _NON_ARTIFACT_RESIDUES:
            klass, reason, subkind = RA.classify_residue(
                layout, 3, text, spans, residue, lines)
            check(klass == "unexplained",
                  f"M11 缺少一个{label}必须是 unexplained（得到 {klass}/"
                  f"{subkind}：{reason}）")
            check(subkind not in ("single_char", "symbol_only", "width_fold"),
                  f"M11b 不得再产出已删除的子类 {subkind!r}")

        # M12：不得因为"残差很短"或"全是符号"就放行。
        for residue in ("。", "…", chr(0x3010), "。”", "1", "7"):
            klass, _, _ = RA.classify_residue(
                layout, 3, text, spans, residue, lines)
            check(klass != "engine_artifact",
                  f"M12 符号/单字符残差 {residue!r} 不得因长度或符号被判 artifact")


# ---------------------------------------------------------------------------
# N（P1-3）：阈值与残差资格正交 —— 三态不变量
# ---------------------------------------------------------------------------

def _fake_summary(records: list) -> dict:
    import evaluation.run_tree_layout_acceptance as RA
    return {"_records": records,
            "overall": {"thresholds": {str(t): {"count": 0, "ratio": 0.0}
                                       for t in RA.THRESHOLDS}}}


def _fake_record(coverage, classes, length=100, document_id=None) -> dict:
    """最小可用的诊断记录（只带三态统计用到的字段）。

    `is_citable_now` 与生产记录同口径：**确定性重算** verdict 后与
    `V.ALIGN_MIN` 比较，而不是自报 —— 否则 N 组就无法校验
    `citable_blocks_now == aligned` 这条不变式。
    """
    import evaluation.run_tree_layout_acceptance as RA
    unexplained = classes.get("unexplained", 0)
    dominant = RA._dominant(classes)
    gate = RA.verdict_residue_class(classes)
    verdict_now = S.compute_alignment_verdict(coverage, gate, V.ALIGN_MIN)
    return {"coverage": coverage, "unexplained_chars": unexplained,
            "residue_class": dominant,
            "verdict_residue_class": gate,
            "dominant_class_masks_unexplained":
                bool(unexplained) and dominant != "unexplained",
            "normalized_block_len": length,
            "has_unexplained": bool(unexplained),
            "unexplained_is_lenient_only": False,
            "residue_classes": {k: v for k, v in classes.items() if v},
            "residue_subkinds": {k: v for k, v in classes.items() if v},
            "unexplained_duplicate_chars": 0,
            "unexplained_search_exhausted_chars": 0,
            "verdict_now": verdict_now,
            "document_id": document_id or RA.DOCUMENTS[0][1],
            "page_number": 1, "block_index": 0, "evidence_id": "ev-fake",
            "is_citable_now": (V.ALIGN_MIN is not None
                               and verdict_now == "aligned")}


#: 描述性主类掩盖未解释残差的反例：全角差异 99 字符（温和类）压过缺字 18 字符。
_MASKED = {"engine_artifact": 99, "unexplained": 18}


def _test_three_state_statistics() -> None:
    """冻结阈值下的正式三态 + 四条不变式；只有 `aligned` 计入可引用面。"""
    import evaluation.run_tree_layout_acceptance as RA

    check(RA.verdict_residue_class({"engine_artifact": 99}) == "engine_artifact",
          "N0a 无未解释残差时门槛类取描述性主类")
    check(RA.verdict_residue_class(_MASKED) == "unexplained",
          "N0b 含任何未解释残差时门槛类必须是 unexplained（不得被更长的主类掩盖）")
    check(RA._dominant(_MASKED) == "engine_artifact",
          "N0c 描述性主类确实是 engine_artifact（反例的前提成立）")

    _DOC_A = RA.DOCUMENTS[0][1]
    _DOC_B = RA.DOCUMENTS[1][1]
    records = [
        _fake_record(0.99, {"column_reorder": 30}, document_id=_DOC_A),
        _fake_record(0.99, _MASKED, document_id=_DOC_A),   # 高覆盖 + 主类掩盖未解释
        _fake_record(0.96, {"unexplained": 5}, document_id=_DOC_A),
        _fake_record(0.92, {"column_reorder": 20}, document_id=_DOC_B),
        _fake_record(0.80, {"unexplained": 12}, document_id=_DOC_B),
        _fake_record(0.40, {"unexplained": 30}, document_id=_DOC_B),
        _fake_record(0.0, {"engine_artifact": 4}, document_id=_DOC_B),
    ]
    status = RA.align_min_status(_fake_summary(records))
    table = status["diagnostic_threshold_table"]

    check(all(e["invariant_holds"] for e in table),
          "N1 每个阈值都满足 aligned + partial + unaligned == total")
    for entry in table:
        total = entry["total_blocks"]
        check(entry["aligned_blocks"] + entry["partial_blocks"]
              + entry["unaligned_blocks"] == total,
              f"N2 T={entry['align_min']} 三态相加等于块总数 {total}")
        check(entry["aligned_blocks"] + entry["partial_blocks"]
              == entry["above_threshold_blocks"],
              f"N3 T={entry['align_min']} aligned+partial == above_threshold")
        check(entry["unaligned_blocks"] == entry["below_threshold_blocks"],
              f"N3b T={entry['align_min']} unaligned == below_threshold "
              f"（coverage<=0 也落在 below 里）")
        check(entry["aligned_ratio"] <= entry["above_threshold_ratio"],
              f"N4 T={entry['align_min']} aligned 占比不得超过 above 占比")
        # N4b：反例不变式 —— 放行面内不得存在"含未解释残差却算 aligned"的块。
        check(entry["aligned_with_unexplained"] == 0
              and entry["no_aligned_with_unexplained"] is True,
              f"N4b T={entry['align_min']} 放行面内没有含未解释残差的 aligned 块")
        # N4c（指令 §七 第 13 条）：第二条反向不变式 —— 低于阈值者必须是 unaligned。
        check(entry["below_threshold_but_not_unaligned"] == 0
              and entry["no_below_threshold_but_not_unaligned"] is True,
              f"N4c T={entry['align_min']} 低于阈值者一律 unaligned")
        # N4d：可引用面是**冻结阈值**下的概念，敏感度表不得携带该字段
        #      （否则 `citable_blocks_now` 会与 table 自己的 aligned 名实不符）。
        check("citable_blocks_now" not in entry
              and "citable_blocks_now_equals_aligned" not in entry,
              f"N4d T={entry['align_min']} 敏感度表不携带冻结阈值的可引用面字段")
        check(entry["partial_blocks_equals_above_with_unexplained"]
              and entry["aligned_blocks_equals_above_without_unexplained"],
              f"N4e T={entry['align_min']} 三态与 has_unexplained 业务定义一致")

    # N5：0.96 那一块与"主类被掩盖"的那一块都必须落在 partial 里，不得算 aligned。
    mid = next(e for e in table if e["align_min"] == 0.95)
    check(mid["partial_blocks"] == 2 and mid["aligned_blocks"] == 1,
          f"N5 高覆盖但含未解释残差（含主类被掩盖）的块计入 partial"
          f"（aligned={mid['aligned_blocks']} partial={mid['partial_blocks']}）")
    # N5b：即使阈值降到 0.50，被掩盖的那一块仍不得进入 aligned。
    low = next(e for e in table if e["align_min"] == 0.5)
    check(low["aligned_blocks"] == 2 and low["aligned_with_unexplained"] == 0,
          f"N5b T=0.50 时被掩盖的块仍不计入 aligned"
          f"（aligned={low['aligned_blocks']}）")

    # N5c（指令 §七 第 3、4、6 条）：0.899999 / 0.90 / 0.80 三档的边界与合计。
    formal = status["formal"]
    check(formal["align_min"] == 0.90,
          f"N5c 正式结果的阈值就是冻结值 0.90（得到 {formal['align_min']}）")
    check(formal["aligned_blocks_at_frozen_threshold"] == 2
          and formal["partial_blocks_at_frozen_threshold"] == 2
          and formal["unaligned_blocks_at_frozen_threshold"] == 3
          and formal["citable_blocks_now"] == 2,
          "N5d T=0.90 正式三态 = 2 aligned / 2 partial / 3 unaligned，"
          f"可引用 2（得到 {formal['aligned_blocks_at_frozen_threshold']}/"
          f"{formal['partial_blocks_at_frozen_threshold']}/"
          f"{formal['unaligned_blocks_at_frozen_threshold']}/"
          f"{formal['citable_blocks_now']}）")

    # N5e：逐块按批准真值表校验 T=0.90 的三态归属。
    expected_by_coverage = {
        0.99: ["aligned", "partially_aligned"],
        0.96: ["partially_aligned"],
        0.92: ["aligned"],
        0.80: ["unaligned"],
        0.40: ["unaligned"],
        0.0: ["unaligned"],
    }
    got: dict = {}
    for record in records:
        verdict = S.compute_alignment_verdict(
            record["coverage"], record["verdict_residue_class"], 0.90)
        got.setdefault(record["coverage"], []).append(verdict)
    for coverage, verdicts in expected_by_coverage.items():
        check(sorted(got.get(coverage, [])) == sorted(verdicts),
              f"N5e cov={coverage} 在 T=0.90 下应为 {verdicts}"
              f"（得到 {got.get(coverage)}）")
    check(got[0.80] == ["unaligned"] and got[0.40] == ["unaligned"],
          "N5f 低于阈值者一律 unaligned（不再有 below 却 partial 的档）")

    # N5g：逐文档三态分布必须覆盖全部三份文档，且与全局三态一致（不重不漏）。
    per_doc = formal["per_document"]
    check(sorted(per_doc) == sorted(d[1] for d in RA.DOCUMENTS),
          f"N5g 逐文档分布覆盖全部三份文档（得到 {sorted(per_doc)}）")
    empty = per_doc[RA.DOCUMENTS[2][1]]
    check(all(v == 0 or v is True for v in empty.values()),
          f"N5g2 未出现块的文档三态全为 0（得到 {empty}）")
    for key in ("total_blocks", "aligned_blocks_at_frozen_threshold",
                "partial_blocks_at_frozen_threshold",
                "unaligned_blocks_at_frozen_threshold",
                "citable_blocks_now"):
        check(sum(d[key] for d in per_doc.values()) == formal[key],
              f"N5h 逐文档 {key} 之和等于全局值 {formal[key]}")

    # N5i：四条正式不变式必须全部为 True。
    check(formal["invariants"] == {
        "three_state_conservation": True,
        "citable_blocks_now_equals_aligned": True,
        "aligned_with_unexplained_is_zero": True,
        "below_threshold_but_not_unaligned_is_zero": True,
        "all_hold": True},
        f"N5i 冻结阈值下四条不变式全部成立（得到 {formal['invariants']}）")

    # N6：旧口径字段与"执行器推荐"已全部删除。
    check("recommended_interval" not in status
          and "sensitivity_only_reference" not in status
          and "lowest_threshold_with_zero_above_threshold_unexplained"
          not in status,
          "N6 已删除 recommended_interval / sensitivity_only_reference / "
          "lowest_threshold_* 三处执行器推荐口径")
    check(not any("citable_blocks" in e for e in table),
          "N7 敏感度表不再输出无边界的 citable_blocks 字段")
    check("candidate_table" not in status
          and "diagnostic_threshold_table" in status,
          "N8 候选表已改名为 diagnostic_threshold_table（非推荐、非裁决依据）")

    # N9：ALIGN_MIN 已冻结 -> 状态、来源、取值必须一致。
    check(status["status"] == "ALIGN_MIN_FROZEN"
          and status["align_min"] == 0.90
          and status["decision_source"] == "USER_CODEX_APPROVED_2026_09_17"
          and status["align_min_frozen"] is True
          and status["decided_by_executor"] is False,
          "N9 状态为 ALIGN_MIN_FROZEN / 0.90 / USER_CODEX_APPROVED_2026_09_17")
    check(V.ALIGN_MIN == 0.90 and not hasattr(V, "ALIGN_MIN_DECIDED"),
          "N10 生产常量已冻结，且未引入 ALIGN_MIN_DECIDED 放行开关")
    check(status["formal"]["citable_blocks_now"]
          == status["formal"]["aligned_blocks_at_frozen_threshold"],
          "N10b 冻结阈值下 citable_blocks_now == aligned")

    # N11：判定语义确实复用 TS1 冻结函数（逐块、逐阈值重算一致）。
    for record in records:
        for threshold in (0.5, 0.9, 0.95, 0.99):
            verdict = S.compute_alignment_verdict(
                record["coverage"], record["verdict_residue_class"], threshold)
            if record["coverage"] <= 0:
                expected = "unaligned"
            elif record["coverage"] < threshold:
                expected = "unaligned"
            elif record["has_unexplained"]:
                expected = "partially_aligned"
            else:
                expected = "aligned"
            check(verdict == expected,
                  f"N11 verdict 按批准真值表重算（{verdict} == {expected}）")
    # N12：若误用描述性主类，被掩盖的块会被判 aligned —— 证明 N0b 的必要性。
    masked = [r for r in records if r["dominant_class_masks_unexplained"]]
    check(len(masked) == 1, "N12 反例夹具里恰有 1 个被主类掩盖的块")
    check(S.compute_alignment_verdict(
        masked[0]["coverage"], masked[0]["residue_class"], 0.95) == "aligned"
        and S.compute_alignment_verdict(
            masked[0]["coverage"], masked[0]["verdict_residue_class"],
            0.95) == "partially_aligned",
        "N12b 用描述性主类会误判 aligned，用门槛类才判 partially_aligned")

    # N13（指令 §七 第 3、4 条）：精确边界 0.899999 / 0.90。
    check(S.compute_alignment_verdict(0.899999, None, 0.90) == "unaligned",
          "N13 coverage=0.899999 且无 unexplained 必须判 unaligned")
    check(S.compute_alignment_verdict(0.90, None, 0.90) == "aligned",
          "N14 coverage=0.90 且无 unexplained 必须判 aligned")
    check(S.compute_alignment_verdict(0.95, "unexplained", 0.90)
          == "partially_aligned",
          "N15 coverage=0.95 但含 unexplained 必须判 partially_aligned")
    check(S.compute_alignment_verdict(0.80, "unexplained", 0.90) == "unaligned",
          "N16 coverage=0.80 且含 unexplained 必须判 unaligned（阈值优先）")

    # N17/N18：§七 第 3、4 条必须在**构造期**（真实记录）上也成立，而不只是纯函数。
    # coverage 按 `FLOAT_PRECISION` 量化，所以"刚好不到 0.90"在 3 位小数下是 0.899。
    def _boundary_record(covered):
        return S.TextAlignmentRecord.create(
            page_layout_id="pl-0123456789abcdef", evidence_set_version="set-01234567",
            page_number=1, block_index=19, evidence_block_id="ev-boundary-block",
            block_char_length=10000,
            residue=((covered, 10000, "page_furniture"),),
            char_map=((0, covered, 1, 0, 0, 0),))

    below_boundary = _boundary_record(8990)
    at_boundary = _boundary_record(9000)
    check(below_boundary.coverage == 0.899
          and below_boundary.verdict == "unaligned"
          and below_boundary.is_citable() is False,
          f"N17 coverage=0.899（刚好不到冻结阈值）的真实记录必须是 unaligned "
          f"且不可引用（得到 {below_boundary.verdict}）")
    check(at_boundary.coverage == 0.90 and at_boundary.verdict == "aligned"
          and at_boundary.is_citable() is True,
          f"N18 coverage=0.90（恰好达到冻结阈值）的真实记录必须是 aligned "
          f"且可引用（得到 {at_boundary.verdict}）")
    check(below_boundary.alignment_locator == at_boundary.alignment_locator
          and below_boundary.alignment_id != at_boundary.alignment_id,
          "N18b 同一 (页, 块) 上跨阈值的两条记录必须定位相同、revision 身份不同")


# ---------------------------------------------------------------------------
# N19：`width_fold` 删除诊断的**一致性修正**反例
# ---------------------------------------------------------------------------

#: 诊断文案里**禁止**出现的错误概括（会把 30/13/67 的真实分布说成"全部不可引用"）。
_FORBIDDEN_PROSE = (
    "这三类都不可引用",
    "同一位置证实",
    "同一位置**可证实**",
    "全部不可引用",
    "不构成放行理由",
)


def _check_prose(text: str, label: str) -> None:
    """诊断文案必须给出准确语义，不得回到被 Codex 判定为错误的表述。"""
    # 只对**结论性**表述做禁止（`不得概括为"受影响块全部不可引用"` 这句本身是在禁止
    # 该说法，属于允许出现的反例声明）。
    for bad in _FORBIDDEN_PROSE:
        check(bad not in text,
              f"N19k {label} 不得出现错误表述「{bad}」")
    for good in ("同页真实、两两不重叠的 occurrence",
                 "不等于同一逻辑位置",
                 "不会单独决定引用资格",
                 "aligned"):
        check(good in text,
              f"N19l {label} 必须给出准确语义（缺「{good}」）")


def _md_summary(records: list) -> dict:
    """给 `_align_min_md` 用的最小完整 summary（只由 records 确定性派生）。

    `_fake_summary` 只服务 `align_min_status`；Markdown 渲染还会读 `overall` 的
    分布 / 最差样本等字段，因此这里按同一 records 补齐，避免测试依赖真实产物。
    """
    import evaluation.run_tree_layout_acceptance as RA
    below = [r for r in records if r["coverage"] < V.ALIGN_MIN]
    classes: dict = {}
    for record in records:
        for klass, chars in (record.get("residue_classes") or {}).items():
            classes[klass] = classes.get(klass, 0) + chars
    unexplained_blocks = [r for r in records if r.get("has_unexplained")]
    worst = sorted(records, key=lambda r: (r["coverage"], r["evidence_id"]))[:5]
    return {
        "_records": records,
        "overall": {
            "block_count": len(records),
            "distribution": {"count": len(records)},
            "below_0_90": {
                "count": len(below),
                "ratio": (len(below) / len(records)) if records else 0.0,
                "with_unexplained_residue":
                    sum(1 for r in below if r.get("has_unexplained")),
            },
            "residue_chars_by_class": dict(sorted(classes.items())),
            "unexplained": {
                "chars": sum(r.get("unexplained_chars", 0) for r in records),
                "duplicate_assignment_chars": 0,
                "search_exhausted_chars": 0,
                "unlocatable_chars": 0,
            },
            "worst": [{**r, "samples": [], "subkinds": r.get("residue_subkinds", {})}
                      for r in worst],
            "most_unexplained": [
                {**r, "block_len": r.get("normalized_block_len", 0),
                 "subkinds": r.get("residue_subkinds", {}), "reasons": []}
                for r in sorted(unexplained_blocks,
                                key=lambda r: -r.get("unexplained_chars", 0))[:5]],
            "dominant_class_masks_unexplained": {
                "blocks": sum(1 for r in records
                              if r.get("dominant_class_masks_unexplained")),
                "ratio": 0.0,
            },
            "thresholds": {str(t): {"count": 0, "ratio": 0.0}
                           for t in RA.THRESHOLDS},
        },
    }


def _test_width_fold_removal_diagnostics() -> None:
    """`width_fold_removal()` 必须逐块派生受影响块的**真实终态**，不得概括成"都不可引用"。

    反例 1～3（指令 §必须修改.3）用**真实构造记录**证明 `column_reorder` 不单独决定
    引用资格；反例 4～6 用小样本 fixture（不硬编码任何真实数字）验证统计守恒、逐块
    一致性与文案。
    """
    import evaluation.run_tree_layout_acceptance as RA

    # -- 反例 1～3：`column_reorder` 的三种终态（真实记录，非纯函数） ------------
    def _cr_record(block_index, covered, residue):
        return S.TextAlignmentRecord.create(
            page_layout_id="pl-0123456789abcdef", evidence_set_version="set-01234567",
            page_number=1, block_index=block_index, evidence_block_id="ev-cr",
            block_char_length=100, residue=residue,
            char_map=((0, covered, 1, 0, 0, 0),))

    cr_aligned = _cr_record(1, 90, ((90, 100, "column_reorder"),))
    check(cr_aligned.residue_class == "column_reorder"
          and cr_aligned.coverage == 0.90 and cr_aligned.verdict == "aligned"
          and cr_aligned.is_citable() is True,
          f"N19a column_reorder + coverage=0.90 + 无 unexplained 必须 aligned/可引用"
          f"（得到 {cr_aligned.residue_class}/{cr_aligned.verdict}）")

    # §七 P1-E：分区必须完整（char_map ∪ residue 恰好覆盖 [0,100) 且互不重叠），
    # 因此"两种残差类别同时存在"要靠**不相交**的两段残差表达，而不是让 residue
    # 覆盖已被 char_map 消费的区间（那是分区错误，不是"含 unexplained"）。
    cr_partial = _cr_record(2, 90, ((90, 95, "column_reorder"),
                                    (95, 100, "unexplained")))
    check(cr_partial.coverage == 0.90 and cr_partial.verdict == "partially_aligned"
          and cr_partial.is_citable() is False,
          f"N19b column_reorder + 达到阈值但含 unexplained 必须 partial/不可引用"
          f"（得到 {cr_partial.verdict}）")

    cr_unaligned = _cr_record(3, 80, ((80, 100, "column_reorder"),))
    check(cr_unaligned.coverage == 0.80 and cr_unaligned.verdict == "unaligned"
          and cr_unaligned.is_citable() is False,
          f"N19c column_reorder + coverage<0.90 必须 unaligned/不可引用"
          f"（得到 {cr_unaligned.verdict}）")

    # -- 反例 4～6：小样本 fixture 上的聚合守恒、逐块一致性与文案 ----------------
    with _tmpdir() as d:
        prev_dir = d / "tree_structure_ts2_layout_FAKE_PREV_20260917T000000Z"
        prev_dir.mkdir(parents=True)
        # (evidence_id, 旧 width_fold 字符, 旧类别, 本轮 coverage, 本轮类别)
        spec = [
            ("ev-p1", 10, {"engine_artifact": 10}, 0.95, {"column_reorder": 10}),
            ("ev-p2", 8, {"engine_artifact": 8}, 0.95, {"unexplained": 8}),
            ("ev-p3", 5, {"engine_artifact": 5}, 0.70, {"column_reorder": 5}),
            ("ev-p4", 3, {"column_reorder": 3}, 0.99, {"column_reorder": 3}),
            ("ev-p5", 4, {"engine_artifact": 4}, 0.90,
             {"column_reorder": 4, "unexplained": 1}),
        ]
        (prev_dir / "coverage_blocks.json").write_text(json.dumps([
            {"evidence_id": eid, "residue_classes": dict(old_classes),
             "residue_subkinds": {"width_fold": chars}}
            for eid, chars, old_classes, _, _ in spec
        ], ensure_ascii=False), encoding="utf-8")

        records = []
        for i, (eid, _chars, _old, coverage, classes) in enumerate(spec):
            record = _fake_record(coverage, dict(classes), length=100)
            record.update({"evidence_id": eid, "page_number": 1, "block_index": i})
            record["residue_subkinds"] = dict(classes)
            records.append(record)

        original_root = RA.RESULTS_ROOT
        try:
            RA.RESULTS_ROOT = d
            removed = RA.width_fold_removal(records, d / "tree_structure_ts2_layout_FAKE_CUR")
        finally:
            RA.RESULTS_ROOT = original_root

        check(removed["comparable"] is True
              and removed["previous_run_is_last_rule_era"] is True,
              "N19d 必须选到仍在 width_fold 规则下的比对基线（fixture）")

        affected_count = removed["affected_block_count"]
        counts = removed["affected_verdict_counts"]
        check(affected_count == len(spec),
              f"N19e affected_block_count 必须等于受影响块数（得到 {affected_count}）")
        check(sum(counts.values()) == affected_count,
              f"N19f 三态计数之和必须等于 affected_block_count（{counts} vs "
              f"{affected_count}）")
        check(removed["affected_citable_count"]
              + removed["affected_noncitable_count"] == affected_count,
              "N19g 可引用 + 不可引用必须等于 affected_block_count")
        check(removed["affected_citable_count"] == counts.get("aligned", 0),
              "N19h 可引用数必须等于 aligned 终态数（fixture：2 块 aligned）")
        check(removed["affected_citable_count"] == 2
              and removed["affected_noncitable_count"] == 3
              and counts == {"aligned": 2, "partially_aligned": 2, "unaligned": 1},
              f"N19i fixture 真实终态必须是 2 可引用 / 3 不可引用、"
              f"aligned·partial·unaligned = 2·2·1（得到 {counts}）")
        check(removed["affected_has_unexplained_count"] == 2
              and removed["affected_below_threshold_count"] == 1,
              "N19j 含未解释 / 低于阈值计数必须逐块派生（fixture：2 / 1）")
        check(removed["affected_invariants"]["all_hold"] is True,
              f"N19j2 受影响块的不变式必须全部成立"
              f"（得到 {removed['affected_invariants']}）")

        # 逐块一致性：`affected_blocks` 每一行必须与输入的 records 同 Evidence id 一致，
        # 且聚合直方图必须能由这些行**重算**出来（= 与 coverage_blocks.json 同源）。
        by_id = {r["evidence_id"]: r for r in records}
        check(sorted(r["evidence_id"] for r in removed["affected_blocks"])
              == sorted(by_id),
              "N19m affected_blocks 必须恰好覆盖全部受影响块（按 Evidence id 对账）")
        recomputed: dict = {}
        for row in removed["affected_blocks"]:
            source = by_id[row["evidence_id"]]
            check(row["verdict_now"] == source["verdict_now"]
                  and row["is_citable_now"] == source["is_citable_now"]
                  and row["has_unexplained"] == source["has_unexplained"]
                  and row["coverage"] == source["coverage"],
                  f"N19n {row['evidence_id']} 的逐块终态必须与 coverage_blocks 同源")
            recomputed[row["verdict_now"]] = recomputed.get(row["verdict_now"], 0) + 1
        check(recomputed == counts,
              f"N19o 聚合直方图必须能由逐块行重算（{recomputed} vs {counts}）")

        # 文案反例：JSON note 与生成的 Markdown 都不得回到错误概括。
        _check_prose(removed["note"], "JSON note")
        summary = _md_summary(records)
        status = RA.align_min_status(summary)
        md = RA._align_min_md(status, summary, None, removed)
        _check_prose(md, "align_min_status.md")
        check(str(removed["affected_citable_count"]) in md
              and str(affected_count) in md
              and "affected_verdict_counts" in md,
              "N19p Markdown 必须明确展示受影响块的派生终态（含可引用数）")
        check("受影响块" in md and "可引用" in md,
              "N19q Markdown 必须给出受影响块的可引用/不可引用拆分")


# ---------------------------------------------------------------------------
# I. 页码家具为 TOC 映射提供真实标签（不伪造 DocumentOutline）
# ---------------------------------------------------------------------------

def _test_page_label_source() -> None:
    with _tmpdir() as d:
        p = _make_pdf(d / "toc.pdf", [
            {"items": [(72.0, 40.0, "ACME CORP ANNUAL REPORT")]
             + _body(n, 5)
             + [(300.0, 810.0, f"- {n} -")]}
            for n in range(1, 7)])
        layout = LB.build_page_layout(p, _ctx())
        labels: dict = {}
        for page in layout.pages:
            for ln in page.lines:
                if ln.furniture_kind == "page_number":
                    labels.setdefault(
                        S._page_label_of_furniture_text(ln.text), []
                    ).append(page.page_number)
        check(all(len(v) == 1 for v in labels.values()),
              "I1 页码标签 → 物理页映射唯一（TOC 映射前提）")
        check(sorted(v[0] for v in labels.values()) == list(range(1, 7)),
              "I2 每个物理页各有一个真实页码标签")
        check(S._resolve_page_label(layout, "3") == 3,
              "I3 既有 page-label 解析在 TS2 构建的布局上可用")

        # 本轮不得伪造 DocumentOutline / TS3 类型
        check(not hasattr(layout, "nodes") and not hasattr(layout, "outline_id"),
              "I4 PageLayout 上不得出现标题树字段")
        # I5/I6/I9 在 TS2 时是"未进入 TS3"的存在性守卫。TS3 交付 A/B 之后，守卫改为
        # **更强**的断言：生产核心必须已落地且版本与公共常量同源，评测侧**不得**再带
        # 自己的副本（`RA.x is aligner.x`，见 `evals/test_tree_aligner.py`）。守卫放宽、
        # 删除或改回"不存在"都不允许：它现在检查的是"唯一生产实现"这一更强的事实。
        check((_PKG_DIR / "aligner.py").exists(),
              "I6a TS3 生产对齐核心 document_structure/aligner.py 已落地")
        import document_structure.aligner as _AL  # noqa: PLC0415
        import document_structure.outline_builder as _OB  # noqa: PLC0415
        import document_structure.span_policy as _SP  # noqa: PLC0415
        import document_structure.versions as _V  # noqa: PLC0415
        import evaluation.run_tree_layout_acceptance as _RA  # noqa: PLC0415
        check(_RA.classify_residue is _AL.classify_residue
              and _RA.displaced_evidence is _AL.displaced_evidence
              and _RA._dominant is _AL.dominant_residue_class,
              "I6b 评测侧对齐原语与生产核心是同一对象（无第二份副本）")
        check(_AL.self_check().get("aligner_version") == _V.ALIGNER_VERSION
              and _V.classify_schema_version(
                  "ALIGNER_VERSION", _V.ALIGNER_VERSION) == "current",
              "I6c 生产对齐核心的引擎版本与 versions 的唯一来源一致（且为 current）")
        # I7 在 TS2 时是"未进入 TS4"的存在性守卫。TS4-A 交付后（§18.12.1：删除错误
        # 断言）守卫改为**更强**的断言：正式组合根必须已落地、阶段口径必须由
        # SPAN_CONFIDENCE_MIN **单点派生**且自洽，且不得出现第二套路由 / Store。
        # TS4-B 起阶段口径由 distribution_only 切到 threshold_enabled；本守卫因此按
        # 阶段参数化，但**不放宽**：A 阶段仍要求"阈值 None / 不得完成"，B 阶段额外
        # 要求"阈值即冻结值、A 版策略资产仍原样可解析"。
        check((_PKG_DIR / "span_builder.py").exists(),
              "I7a TS4 生产组合根 document_structure/span_builder.py 已落地")
        import document_structure.span_builder as _SB  # noqa: PLC0415
        _sb_check = _SB.self_check()
        _expect_stage = ("distribution_only" if _V.SPAN_CONFIDENCE_MIN is None
                         else "threshold_enabled")
        _expect_completion = _V.SPAN_CONFIDENCE_MIN is not None
        check(_sb_check["stage"] == _expect_stage
              and _sb_check["span_confidence_min"] == _V.SPAN_CONFIDENCE_MIN
              and _sb_check["completion_enabled"] is _expect_completion
              and _sb_check["problems"] == [],
              f"I7b TS4 组合根必须自报与 SPAN_CONFIDENCE_MIN 单点一致的阶段口径"
              f"（A：distribution_only / 阈值 None / 不得完成；"
              f"B：threshold_enabled / 阈值即冻结值 / 完成开关开启）且无问题"
              f"（得到 {_sb_check}，期望 stage={_expect_stage!r} / "
              f"span_confidence_min={_V.SPAN_CONFIDENCE_MIN!r}）")
        if _expect_stage == "threshold_enabled":
            import json as _json  # noqa: PLC0415
            from document_structure.span_schema import (  # noqa: PLC0415
                SpanQualificationPolicy as _SQP,
            )
            _frozen = _SP.resolve_frozen_policy()
            check(_sb_check["span_confidence_min"] == _frozen.span_confidence_min
                  and _sb_check["completion_enabled"]
                  is _frozen.completion_enabled,
                  "I7b-B 进入 B 阶段后组合根自报的阈值 / 完成开关必须与冻结策略一致")
            # A 版资产在 B 阶段**仍原样可解析**为 distribution_only：走注册表按字节直读，
            # 不经 `resolve_distribution_policy()`（那个入口被 `assert_distribution_only`
            # 有意关掉，A 阶段口径不得在任何新建路径上复活）。
            _a_entry = _SP.registry_entry_record(_SP.DEFAULT_POLICY_KEY)
            _a_policy = _SQP.from_dict(_json.loads(
                (_SP.POLICY_DIR / _a_entry["registry_entry"]["file"])
                .read_text(encoding="utf-8")))
            check(_a_policy.stage == "distribution_only"
                  and _a_policy.span_confidence_min is None
                  and _a_policy.completion_enabled is False
                  and _a_policy.set_complete_supported is False,
                  "I7b-B A 版分布策略资产在 B 阶段仍须原样为 distribution_only / "
                  "阈值 None / 不得完成（历史 A 身份不得被当前全局常量重写）")
            raises(_SP.resolve_distribution_policy, _SP.PolicyResolutionError,
                   "与当前 A/B 阶段",
                   "I7b-B 已进入 B 阶段后，A 版解析入口必须 fail-closed（不得复活）")
        check(all(callable(getattr(_SB, name, None)) for name in
                  ("build_span_snapshot", "issue_live_ts3_handoff", "self_check")),
              "I7c TS4 组合根暴露唯一公共入口（builder + 受信交接签发 + 自检）")
        _sb_src = _read_module("span_builder.py")
        check(not any(t in _sb_src for t in _FORBIDDEN_TOKENS),
              "I7d TS4 组合根不含公司名 / 公司代码 / gold / 固定页码特例")
        check(not (_imported_modules(_sb_src)
                   & {"harness", "sections", "sqlite3", "requests", "openai",
                      "anthropic", "bocha"}),
              "I7e TS4 组合根不得依赖 harness / sections / 第二套 Store 或网络 / LLM")
        # I8：TS5 已实现表格构建器，因此这里不再断言"它不存在"（那是 TS4 阶段的
        # 边界声明），改为断言它已落地**且仍守着同一条阶段边界**：不得依赖
        # harness / sections / 第二套 Store / 网络 / LLM，也不得含公司名、公司代码、
        # gold、固定页码特例。删掉这条会让"TS5 是否越过阶段边界"变成无人检查。
        check((_PKG_DIR / "table_builder.py").exists(),
              "I8 TS5 表格构建器 document_structure/table_builder.py 已落地")
        _tb_src = _read_module("table_builder.py")
        check(not any(t in _tb_src for t in _FORBIDDEN_TOKENS),
              "I8a TS5 表格构建器不含公司名 / 公司代码 / gold / 固定页码特例")
        check(not (_imported_modules(_tb_src)
                   & {"harness", "sections", "sqlite3", "requests", "openai",
                      "anthropic", "bocha"}),
              "I8b TS5 表格构建器不得依赖 harness / sections / 第二套 Store "
              "或网络 / LLM")
        check((_PKG_DIR / "outline_builder.py").exists(),
              "I9a TS3 生产标题树核心 document_structure/outline_builder.py 已落地")
        check(_OB.OUTLINE_BUILDER_VERSION == _V.OUTLINE_ALGORITHM_VERSION,
              "I9b 标题树构建器版本与公共 OUTLINE_ALGORITHM_VERSION 同源")
        check(all(callable(getattr(_OB, name, None)) for name in
                  ("load_source_context", "scan_heading_candidates",
                   "build_document_outline", "collect_toc_entries", "self_check")),
              "I9c 标题树构建器暴露确定性公共入口")
        import evaluation.run_tree_outline_acceptance as _RB  # noqa: PLC0415
        check(_RB.build_document_outline is _OB.build_document_outline
              and _RB.load_source_context is _OB.load_source_context,
              "I9d 标题树评测侧与生产核心是同一对象（无第二份副本）")


# ---------------------------------------------------------------------------
# J. 真实 PDF（与合成 fixture 分开报告）
# ---------------------------------------------------------------------------

def _test_real_pdfs() -> None:
    missing = [n for _, _, n, _ in _REAL_PDFS if not (_SAMPLE_DIR / n).exists()]
    if missing:
        plan("J ENVIRONMENT_DISCREPANCY 真实 PDF 缺失，未做任何真实构建："
             + ", ".join(missing))
        return
    for company_id, document_id, name, page_count in _REAL_PDFS:
        path = _SAMPLE_DIR / name
        layout = LB.build_page_layout(
            path, LB.LayoutBuildContext(company_id=company_id,
                                        document_id=document_id))
        ok = check(layout.page_count == page_count,
                   f"J1[real] {name} 页数 {layout.page_count} == DB 记录 {page_count}")
        if not ok:
            continue
        check(layout.source_file_sha256 == _sha256(path),
              f"J2[real] {name} 源文件哈希与磁盘一致")
        check(layout.document_version
              == "sha256-" + layout.source_file_sha256[:16],
              f"J3[real] {name} document_version 沿用既有身份规则")
        lines = _all_lines(layout)
        check(len(lines) > 0, f"J4[real] {name} 读取到文本行")
        check(all(ln.text.strip() for ln in lines),
              f"J5[real] {name} 无空行")
        check(len(lines) == len({(ln.line_index, p.page_number)
                                 for p in layout.pages for ln in p.lines}),
              f"J6[real] {name} 行地址唯一")
        audit = LB.audit_furniture(layout)
        check(audit["rules"]["running_header"]["page_count"] > 0,
              f"J7[real] {name} 检测到 running header")
        check(audit["rules"]["page_number"]["page_count"] > 0,
              f"J8[real] {name} 检测到 page_number")
        check(audit["totals"]["unmarked_lines"] > 0,
              f"J9[real] {name} 正文未被整体误标为家具")
        check(layout.furniture_lines("page_number")
              and layout.furniture_lines("header"),
              f"J10[real] {name} 家具行保留在 PageLayout 内（未被删除）")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> dict:
    groups = (
        _test_synthetic_readback,
        _test_reading_order,
        _test_furniture,
        _test_rotation,
        _test_fail_fast,
        _test_determinism,
        _test_company_independence,
        _test_cli_validate_only,
        _test_page_label_source,
        _test_real_pdfs,
        # TS2.1 P1-1 / P1-2 / P1-3 反例
        _test_nonoverlapping_assignment,
        _test_engine_artifact_whitelist,
        _test_three_state_statistics,
        # TS2 最后一次非生产诊断一致性修正：width_fold 删除诊断的真实终态
        _test_width_fold_removal_diagnostics,
    )
    for fn in groups:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 崩溃（该组后续反例未执行）："
                f"{type(e).__name__}: {e}")

    # TS1 回归：ReferenceOccurrence / TocSource 在 TS2 之后必须仍全绿
    try:
        import evals.test_tree_structure_focused as F
        r = F.main()
        check(r["failed"] == 0,
              f"K1 TS1 focused 回归在 TS2 之后仍全绿（failed={r['failed']}）")
        check(r["passed"] > 0, f"K2 TS1 focused 回归确实执行了断言（{r['passed']}）")
    except Exception as e:  # noqa: BLE001
        check(False, f"K1 TS1 focused 回归崩溃：{type(e).__name__}: {e}")
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

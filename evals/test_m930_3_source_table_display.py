# -*- coding: utf-8 -*-
"""Eval: §0.21 路径 (b) —— **原 PDF 表区的只读展示**（`std-1` / `stdp-1`）。

用法: python -m evals.test_m930_3_source_table_display

本模块钉住这条路径的**边界**，不是它的措辞：

1. **原件身份与哈希**：实测文件哈希与登记一致才可展示；不一致 ⇒
   `source_file_hash_mismatch` 且**不渲染**（否则展示的是另一版原件）；文件缺失 /
   非 PDF 各自成 typed 缺陷。**任何**缺陷都记成 `display_state="defect"`，绝不被读成
   「材料里没有这张表」——§0.21 要求「确有表格却未呈现」记**系统展示能力缺陷**。
2. **区域边界**：区域越出页媒体框、区域退化、页号越界，三条各自 typed。
3. **续表完整性**：前驱在前的链条可展示；前驱缺席 / 前驱本身是缺陷 / 前驱落在更靠后的页，
   一律 `continuation_out_of_order`——「只截半张」必须当场成立为缺陷。
4. **锚点复算**：表题锚点不在该页文字层 ⇒ `title_anchor_not_found`（页号指错地方）；
   关键行文字不在该区域文字层 ⇒ `row_anchor_not_found`（切到了别的表）。
5. **渲染**：真 PDF 渲染出 PNG（`\\x89PNG` 头、像素尺寸、渲染哈希与落盘字节逐字相等）；
   后端不可用与渲染抛错是两个 typed 缺陷。
6. **人工确认只有人能签**：初值恒为 `pending_human_confirmation`；`apply_human_confirmation`
   要确认人 + 时间 + 词表内结论，缺陷区域**不接受**签署，且不就地改写原记录。
7. **两条路径身份不可互换**：并列展示的 `report_version` **不**进展示集身份（同一批区域
   配不同正文版本号 ⇒ 指纹相同）；展示集里没有 material id / topic id / 格级数字权威，
   也不 import 写作链的任何模块。
8. **登记台账**：「采用」必须有展示区域、「不采用」必须给理由且不得挂区域；每个展示区域
   必须被一条「采用」登记认领。运行级记录（`templates/source_display/…yaml`）自检同一约束。

**全场本地**：不联网、不读数据库、不碰历史 run。真实 PDF 只出现在 §5 与 §9 的用例里，
且那份 PDF 是本模块用 fitz 现造的临时文件；§9 另做一次**运行级记录自检**（不打开 PDF）。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import source_table_display as STD  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RUN_RECORD = REPO / "templates" / "source_display" / \
    "m930_3_source_table_display_v1.yaml"


# ---------------------------------------------------------------------------
# 夹具：假页（只实现本模块用到的三样）+ 假渲染，避免每条用例都开真 PDF
# ---------------------------------------------------------------------------

class _FakeCrop:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePage:
    """`width`/`height` 是媒体框；`page_text` 是整页文字层；`region_text` 是区域内文字层。"""

    def __init__(self, *, width: float = 600.0, height: float = 800.0,
                 page_text: str = "", region_text: str = "") -> None:
        self.width = width
        self.height = height
        self._page_text = page_text
        self._region_text = region_text
        self.cropped: list[tuple] = []

    def extract_text(self) -> str:
        return self._page_text

    def crop(self, box) -> _FakeCrop:
        self.cropped.append(tuple(box))
        return _FakeCrop(self._region_text)


class _FakeReader:
    def __init__(self, pages) -> None:
        self.pages = list(pages)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Sandbox:
    """一次用例的落点：临时目录 + 假 PDF 读入 + 假渲染。用完即删。"""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="m930_3_std_")
        self.root = Path(self._tmp.name)
        self.out = self.root / "out"
        self.readers: dict[str, _FakeReader] = {}
        self.renders: list[tuple] = []
        self.render_raises: Exception | None = None
        self.sha_by_path: dict[str, str] = {}
        self._real_open = None
        self._real_render = None
        self._real_sha = None

    # ---- 环境接管 ----
    def __enter__(self) -> "_Sandbox":
        import pdfplumber
        self._real_open = pdfplumber.open
        self._real_render = STD._render_region_to_png
        self._real_sha = STD._sha256_file

        def fake_open(path):
            reader = self.readers.get(str(path))
            if reader is None:
                raise AssertionError(f"用例没有登记这个路径：{path}")
            return reader

        def fake_render(pdf_path, page_index, points, scale):
            self.renders.append((str(pdf_path), int(page_index), tuple(points), scale))
            if self.render_raises is not None:
                raise self.render_raises
            return f"PNG:{Path(pdf_path).name}:{page_index}:{points}".encode()

        pdfplumber.open = fake_open
        STD._render_region_to_png = fake_render
        STD._sha256_file = lambda p: self.sha_by_path.get(str(p), "sha-" + Path(p).name)
        return self

    def __exit__(self, *exc) -> None:
        import pdfplumber
        pdfplumber.open = self._real_open
        STD._render_region_to_png = self._real_render
        STD._sha256_file = self._real_sha
        self._tmp.cleanup()
        return None

    # ---- 便利 ----
    def install(self, *, name: str = "DOC_A.pdf", pages=None,
                actual_sha: str = "REGISTERED") -> Path:
        target = self.root / name
        target.write_bytes(b"%PDF-1.4 fake")
        self.sha_by_path[str(target)] = actual_sha
        self.readers[str(target)] = _FakeReader(pages or [_FakePage()])
        return target


def _region(**kw) -> STD.SourceDisplayRegion:
    base = dict(
        region_key="r1", rail="收入构成", document_id="DOC_A",
        document_version="v1", page_number=1,
        region_points=(10.0, 20.0, 300.0, 200.0), title="营业收入构成",
        period_label="2025年", unit_label="千元",
        title_anchor="营业收入构成", row_anchors=("营业收入合计",))
    base.update(kw)
    return STD.SourceDisplayRegion(**base)


def _build(regions, path, *, actual_sha="REGISTERED", declared="REGISTERED",
           registrations=(), paired="", **kw):
    return STD.build_source_table_display_set(
        regions=regions, task_id="t1", section_id="company",
        source_record_id="rec1", registered_source_id="manifest-1",
        output_dir=Path(path).parent / "out",
        resolve_source_path={"DOC_A": str(path)},
        registered_sha256={"DOC_A": declared}, registrations=registrations,
        paired_report_version=paired, **kw)


def _expect_error(fn, *, token: str = "") -> str:
    try:
        fn()
    except STD.SourceTableDisplayError as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(
                f"异常消息里没有 {token!r}（无法据此定位）：{message}") from None
        return message
    raise AssertionError(f"这一路必须 fail-closed，但它通过了（期望含 {token!r}）")


def _kinds(record) -> list[str]:
    return [d.kind for d in record.defects]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ================================================================== §1 原件身份与哈希
    details.append("## §1 原件身份与哈希")
    with _Sandbox() as box:
        page = _FakePage(page_text="营业收入构成 项目 2025年",
                         region_text="营业收入合计 100 200\n动力电池系统 1 2")
        path = box.install(pages=[page])
        display = _build([_region(row_anchors=("营业收入合计", "动力电池系统"))], path)
        (record,) = display.regions
        check(record.displayable and not record.defects,
              "登记哈希一致 ⇒ 区域可展示（正例）")
        check(record.file_hash_verified and record.file_sha256_actual == "REGISTERED",
              "原件哈希**实测**一致并记进读数（不是抄一遍登记值）")
        check(record.page_media_box == (0.0, 0.0, 600.0, 800.0),
              "页媒体框记进读数（区域越界判据的基准）")
        check(record.render_sha256 == hashlib.sha256(
            b"PNG:DOC_A.pdf:0:(10.0, 20.0, 300.0, 200.0)").hexdigest(),
            "渲染哈希 = 渲染字节的 sha256（逐字可复算）")
        check((Path(box.out) / record.render_relpath).is_file()
              and record.render_relpath.endswith(".png"),
              "渲染产物落盘且可回查")
        check(record.region_char_count == len("营业收入合计100200动力电池系统12"),
              "区域文字层摘要按**空白归一**口径计数（可复算凭证）")
        check(record.confirmation_state == "pending_human_confirmation"
              and not record.human_confirmed,
              "初值恒为「待人工确认」——本模块不代替人签署")
        check(display.display_set_id().startswith("std_"),
              "展示集有自己的身份（不与写作链共用 id 空间）")
        details.append("NOTE 正例 1a：原件身份、页/区域、渲染哈希、区域摘要四样同时在场。")

        # 反例：哈希不符
        renders_before = len(box.renders)
        box.sha_by_path[str(path)] = "ACTUAL"
        bad = _build([_region()], path, declared="REGISTERED")
        (rec_bad,) = bad.regions
        check(_kinds(rec_bad) == ["source_file_hash_mismatch"]
              and rec_bad.display_state == "defect",
              "实测哈希与登记不符 ⇒ `source_file_hash_mismatch`（反例）")
        check(not rec_bad.file_hash_verified and not rec_bad.render_sha256,
              "哈希不符时**不渲染**：渲染出来就会是另一版原件")
        check(len(box.renders) == renders_before,
              "…且这一次渲染函数一次都没被调用（不是「渲染了再丢弃」）")
        details.append("NOTE 反例 1b：这一条是 fail-closed 的实质——宁可不出图，不出错图。")

    with _Sandbox() as box:
        missing = box.root / "nope.pdf"
        box.sha_by_path[str(missing)] = "X"
        display = _build([_region()], missing, declared="X")
        check(_kinds(display.regions[0]) == ["source_file_missing"],
              "登记路径取不到文件 ⇒ `source_file_missing`（反例）")
        check(display.regions[0].display_state == "defect"
              and not display.displayable_regions,
              "…且这是**展示缺陷**，不是「材料里没有这张表」")
    with _Sandbox() as box:
        docx = box.root / "DOC_A.docx"
        docx.write_bytes(b"not a pdf")
        box.sha_by_path[str(docx)] = "X"
        display = _build([_region(source_name="DOC_A.docx")], docx, declared="X")
        check(_kinds(display.regions[0]) == ["source_not_pdf"],
              "登记来源不是 PDF ⇒ `source_not_pdf`（没有可渲染原件）")

    # ================================================================== §2 区域边界
    details.append("## §2 区域边界")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(width=600.0, height=800.0)])
        display = _build([_region(region_points=(10.0, 20.0, 900.0, 200.0))], path)
        check(_kinds(display.regions[0]) == ["region_out_of_page"],
              "区域越出页媒体框 ⇒ `region_out_of_page`（反例）")
        display = _build([_region(region_points=(10.0, 20.0, 10.0, 200.0))], path)
        check(_kinds(display.regions[0]) == ["region_degenerate"],
              "区域宽为零 ⇒ `region_degenerate`（反例）")
        display = _build([_region(page_number=7)], path)
        check(_kinds(display.regions[0]) == ["page_out_of_range"],
              "页号越出文档页数 ⇒ `page_out_of_range`（反例）")

    # ================================================================== §3 锚点复算
    details.append("## §3 锚点复算")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="别的内容",
                                            region_text="营业收入合计 1")])
        display = _build([_region()], path)
        check(_kinds(display.regions[0]) == ["title_anchor_not_found"],
              "表题锚点不在**该页**文字层 ⇒ `title_anchor_not_found`（页号指错地方）")
        path = box.install(name="DOC_B.pdf",
                           pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 100")])
        display = _build([_region(row_anchors=("营业收入合计", "境外"))], path)
        check(_kinds(display.regions[0]) == ["row_anchor_not_found"],
              "关键行文字不在**该区域**文字层 ⇒ `row_anchor_not_found`（只切了半张表）")
        details.append("NOTE 两级锚点分工：页级管「这一页对不对」，区域级管「这一块全不全」。")

    # ================================================================== §4 续表完整性
    details.append("## §4 续表完整性")
    with _Sandbox() as box:
        reader_pages = [_FakePage(page_text="营业收入构成", region_text="营业收入合计 1"),
                        _FakePage(page_text="境外 2", region_text="境外 2")]
        path = box.install(pages=reader_pages)
        first = _region(region_key="r1", page_number=1, row_anchors=("营业收入合计",))
        second = _region(region_key="r2", page_number=2,
                         region_points=(10.0, 20.0, 300.0, 400.0),
                         title="（续表）营业收入构成", title_anchor="境外",
                         row_anchors=("境外",), continuation_of="r1")
        display = _build([first, second], path)
        check([r.displayable for r in display.regions] == [True, True],
              "前驱在前、两页都成立 ⇒ 正张表（含续表）一起可展示（正例）")

        # 前驱本身是缺陷 ⇒ 续表不成立
        bad_first = _region(region_key="r1", page_number=1, row_anchors=("没这一行",))
        display = _build([bad_first, second], path)
        check(_kinds(display.regions[1]) == ["continuation_out_of_order"],
              "前驱本身就是缺陷区域 ⇒ 续表 `continuation_out_of_order`（反例）")
        details.append("NOTE 反例 4a：前驱没出图，续表的「完整」这句话就没有立足点。")

        # 前驱落在更靠后的页 ⇒ 顺序被说反了
        swapped_first = _region(region_key="r1", page_number=2, title_anchor="境外",
                                row_anchors=("境外",))
        swapped_second = _region(region_key="r2", page_number=1, title_anchor="营业收入构成",
                                 title="（续表）", row_anchors=("营业收入合计",),
                                 continuation_of="r1")
        display = _build([swapped_first, swapped_second], path)
        check(_kinds(display.regions[1]) == ["continuation_out_of_order"],
              "前驱落在更靠后的页 ⇒ 展示顺序把同一张表说反了（反例）")
        check("自己当成前驱" in _expect_error(
            lambda: _region(region_key="rX", continuation_of="rX")),
            "反例 4c：续表把自己当成前驱 ⇒ 当场拒（结构上不可能成立）")

    details.append("## §4b 装载期拒收")
    with _Sandbox() as box:
        chain = box.root / "chain.yaml"
        chain.write_text(
            "regions:\n"
            "  - region_key: r1\n    rail: 收入构成\n    document_id: D\n"
            "    document_version: v\n    page_number: 1\n"
            "    region_points: [0, 0, 10, 10]\n    title: 表\n"
            "  - region_key: r2\n    rail: 收入构成\n    document_id: D\n"
            "    document_version: v\n    page_number: 2\n"
            "    region_points: [0, 0, 10, 10]\n    title: 续表\n"
            "    continuation_of: rX\n", encoding="utf-8")
        msg = _expect_error(lambda: STD.load_source_display_specs(chain),
                            token="续表前驱")
        details.append(f"NOTE 反例 4b：{msg}")
        check("rX" in msg, "装载期就拒续表前驱不在它**之前**的记录（展示顺序即续表顺序）")

        dup = box.root / "dup.yaml"
        dup.write_text(
            "regions:\n"
            "  - region_key: r1\n    rail: x\n    document_id: D\n"
            "    document_version: v\n    page_number: 1\n"
            "    region_points: [0, 0, 10, 10]\n    title: t\n"
            "  - region_key: r1\n    rail: x\n    document_id: D\n"
            "    document_version: v\n    page_number: 2\n"
            "    region_points: [0, 0, 10, 10]\n    title: t\n", encoding="utf-8")
        check("重复" in _expect_error(lambda: STD.load_source_display_specs(dup)),
              "区域键重复 ⇒ 装载期拒（展示顺序无法唯一）")
        incomplete = box.root / "inc.yaml"
        incomplete.write_text(
            "regions:\n"
            "  - region_key: r1\n    rail: x\n    document_id: D\n"
            "    document_version: v\n    page_number: 1\n"
            "    region_points: [0, 0, 10, 10]\n", encoding="utf-8")
        check("缺字段" in _expect_error(
            lambda: STD.load_source_display_specs(incomplete)),
            "区域记录缺表题/期间/单位等必填字段 ⇒ 装载期拒，不静默补默认值")

    # ================================================================== §5 渲染
    details.append("## §5 渲染")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 1")])
        box.render_raises = RuntimeError("boom")
        display = _build([_region()], path)
        check(_kinds(display.regions[0]) == ["render_failed"]
              and display.regions[0].render_sha256 == "",
              "渲染抛错 ⇒ `render_failed`，且**没有**渲染哈希（不伪造空产物）（反例）")
        box.render_raises = ImportError("no pypdfium2")
        display = _build([_region()], path)
        check(_kinds(display.regions[0]) == ["render_backend_unavailable"],
              "渲染后端缺席与渲染出错是两个 typed 缺陷，不得合并（反例）")

    details.append("## §5b 真渲染后端")
    try:
        import fitz
    except ImportError:
        fitz = None
    if fitz is None:
        skipped += 1
        details.append("SKIP: 本机没有 PyMuPDF，真渲染后端一次都没跑（不伪造通过）")
    else:
        with _Sandbox() as box:
            # 这一条整条走**真**路径：真哈希、真 pdfplumber 读入、真渲染后端。
            import pdfplumber as _pdfplumber
            STD._sha256_file = box._real_sha
            STD._render_region_to_png = box._real_render
            _pdfplumber.open = box._real_open
            doc = fitz.open()
            page = doc.new_page(width=400, height=300)
            page.insert_text((50, 100), "Revenue 2025 2024", fontsize=12)
            page.insert_text((50, 140), "Total 1000 900", fontsize=12)
            pdf = box.root / "tiny.pdf"
            doc.save(str(pdf))
            doc.close()
            real_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
            display = STD.build_source_table_display_set(
                regions=[_region(document_id="TINY", source_name="tiny.pdf",
                                 page_number=1, region_points=(10.0, 10.0, 390.0, 290.0),
                                 title="Revenue", title_anchor="Revenue",
                                 row_anchors=("Total",))],
                task_id="t", section_id="s", source_record_id="r",
                registered_source_id="m", output_dir=box.out,
                resolve_source_path={"TINY": str(pdf)},
                registered_sha256={"TINY": real_sha}, render_scale=2.0)
            (record,) = display.regions
            check(record.displayable,
                  "真 PDF 的一块区域渲染成图（缺陷：" + str([
                      (d.kind, d.detail) for d in record.defects]) + "）")
            check(record.render_pixel_size == (760, 560),
                  f"像素尺寸 = 区域点数 × 缩放（2.0 ⇒ 760×560，实测 "
                  f"{record.render_pixel_size}）")
            payload = (box.out / record.render_relpath).read_bytes()
            check(payload[:8] == b"\x89PNG\r\n\x1a\n"
                  and hashlib.sha256(payload).hexdigest() == record.render_sha256,
                  f"落盘字节是真 PNG，且与记录的渲染哈希逐字相等"
                  f"（头 {payload[:8]!r}，"
                  f"实测 {hashlib.sha256(payload).hexdigest()[:12]} vs "
                  f"记录 {record.render_sha256[:12]}）")
            details.append("NOTE 正例 5b：渲染是**原件像素**，不做 OCR、不做截图识别、"
                           "不重新抽取平铺文本。")

    # ================================================================== §6 人工确认
    details.append("## §6 人工确认只有人能签")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 1")])
        display = _build([_region()], path)
        (record,) = display.regions
        check(record.confirmation_state == "pending_human_confirmation"
              and not display.confirmed_regions,
              "正例 6a：一批区域跑完，**没有一个**取得人工确认")
        for kwargs, why in (
                (dict(confirmer="", confirmed_at="2026-10-01T00:00:00",
                      conclusion="complete_clear_faithful"), "缺确认人"),
                (dict(confirmer="张三", confirmed_at="",
                      conclusion="complete_clear_faithful"), "缺确认时间"),
                (dict(confirmer="张三", confirmed_at="2026-10-01T00:00:00",
                      conclusion="looks_good"), "结论不在词表里")):
            check("人工确认" in _expect_error(
                lambda kw=kwargs: STD.HumanConfirmation(**kw)),
                f"反例 6b：{why} ⇒ 拒收（确认不是一次匿名点击）")
        confirmed = STD.apply_human_confirmation(record, STD.HumanConfirmation(
            confirmer="张三", confirmed_at="2026-10-01T00:00:00",
            conclusion="complete_clear_faithful"))
        check(confirmed.human_confirmed and confirmed.confirmer == "张三",
              "正例 6c：带齐确认人/时间/词表结论 ⇒ 记 `human_confirmed`")
        check(record.confirmation_state == "pending_human_confirmation",
              "…且**不就地改写**原记录（确认是加一份签名，不是改历史读数）")
        rejected = STD.apply_human_confirmation(record, STD.HumanConfirmation(
            confirmer="张三", confirmed_at="2026-10-01T00:00:00",
            conclusion="incomplete_or_unclear_or_unfaithful"))
        check(rejected.confirmation_state == "human_rejected",
              "正例 6d：人也可以判「不完整/不清晰/不忠实」，两种结论各有档位")

    with _Sandbox() as box:
        missing = box.root / "nope.pdf"
        box.sha_by_path[str(missing)] = "X"
        display = _build([_region()], missing, declared="X")
        check("缺陷区域" in _expect_error(lambda: STD.apply_human_confirmation(
            display.regions[0], STD.HumanConfirmation(
                confirmer="张三", confirmed_at="2026-10-01T00:00:00",
                conclusion="complete_clear_faithful"))),
            "反例 6e：没出图的区域不能被确认为「完整清晰忠实」——那句话无从成立")

    # ================================================================== §7 两条路径身份不可互换
    details.append("## §7 两条路径身份不可互换")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 1")])
        plain = _build([_region()], path)
        paired = _build([_region()], path, paired="crpv_deadbeef")
        check(plain.fingerprint() == paired.fingerprint(),
              "并列展示的正文版本号**不**进展示集身份（同一批区域配不同版本号 ⇒ 同一指纹）")
        check("crpv_deadbeef" in STD.render_source_table_display_markdown(paired),
              "…但读者面上照常并列写出「这一页与哪一版正文一起看」")
        check("pack_id" not in json.dumps(plain.to_dict())
              and "material_id" not in json.dumps(plain.to_dict())
              and "topic_id" not in json.dumps(plain.to_dict()),
              "展示集里没有 material id / topic id ——它不是 Pack 的第二条投递通道")
        blob = json.dumps(plain.to_dict(), ensure_ascii=False)
        check(all(name not in blob for name in
                  ("value_text", "row_label", "column_header", "fact_id",
                   "cells", "numeric_authority", "set_complete")),
              "展示集里没有格级 / 事实级 / 资格字段 —— 它不产生任何数字权威")
        source = (REPO / "sections" / "source_table_display.py").read_text(
            encoding="utf-8")
        for forbidden in ("from sections import cited_writer",
                          "from sections import pack_writer",
                          "from sections import narrative_schema",
                          "import material_context"):
            check(forbidden not in source,
                  f"本模块不得 import 写作链（{forbidden}）：两条路径在代码上也不许有近路")
        details.append("NOTE §7：身份不可互换必须同时成立于数据面与 import 面。")

    # ================================================================== §8 登记台账
    details.append("## §8 候选表区登记台账")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 1")])
        check("必须写明" in _expect_error(lambda: STD.SourceDisplayRegistration(
            document_id="D", area_label="表A", decision="not_adopted", reason="")),
            "反例 8a：不采用却不给理由 ⇒ 拒（否则「没展示」会被读成「没有这张表」）")
        check("采用却没有" in _expect_error(lambda: STD.SourceDisplayRegistration(
            document_id="D", area_label="表A", decision="adopted", reason="相关")),
            "反例 8b：采用却没有任何展示区域 ⇒ 拒")
        check("不采用却挂了" in _expect_error(lambda: STD.SourceDisplayRegistration(
            document_id="D", area_label="表A", decision="not_adopted",
            reason="不相关", region_keys=("r1",))),
            "反例 8c：不采用却挂了展示区域 ⇒ 拒")
        good = STD.SourceDisplayRegistration(
            document_id="DOC_A", area_label="表A", decision="adopted",
            reason="演示三族之一", region_keys=("r1",))
        display = _build([_region()], path, registrations=(good,))
        check(display.registrations and display.displayable_regions,
              "正例 8d：区域被一条「采用」登记认领 ⇒ 展示集成立")
        check("没有任何一条「采用」登记认领" in _expect_error(
            lambda: STD.SourceTableDisplaySet(
                task_id="t", section_id="s", source_record_id="r",
                registered_source_id="m", regions=display.regions,
                registrations=(STD.SourceDisplayRegistration(
                    document_id="DOC_A", area_label="别的表", decision="adopted",
                    reason="相关", region_keys=("rX",)),))),
            "反例 8e：展示区域没有被任何「采用」登记认领（或登记指向不存在的区域）⇒ 拒")

    # ================================================================== §9 运行级记录自检
    details.append("## §9 运行级来源展示记录自检（数据面，不打开 PDF）")
    if not RUN_RECORD.is_file():
        skipped += 1
        details.append(f"SKIP: {RUN_RECORD} 不在本工作区")
    else:
        specs = STD.load_source_display_specs(RUN_RECORD)
        regs = STD.load_source_display_registrations(RUN_RECORD)
        check(len(specs) >= 6 and len(regs) >= 6,
              f"记录有 {len(specs)} 个区域、{len(regs)} 条候选登记")
        adopted = {k for r in regs if r.decision == "adopted" for k in r.region_keys}
        check(adopted == {s.region_key for s in specs},
              "每个区域恰被一条「采用」登记认领，且没有登记指向不存在的区域")
        check(any(r.decision == "not_adopted" for r in regs),
              "记录里**也有**「不采用」的候选表区，逐条写了理由（不只是展示了的那些）")
        check(all(r.reason.strip() for r in regs), "每条登记都有理由")
        rails = {s.rail for s in specs}
        check(any("收入构成" in r for r in rails)
              and any("营业成本" in r for r in rails)
              and any("毛利" in r for r in rails),
              "三个展示族（收入构成 / 营业成本 / 毛利及毛利率）都在记录里")
        check(any(s.continuation_of for s in specs),
              "记录里至少有一条**续表**：跨页表必须按顺序给出全部区域")
        check(all(s.region_points[2] > s.region_points[0]
                  and s.region_points[3] > s.region_points[1] for s in specs),
              "每个区域都不是退化区域")
        check(all(s.document_version and s.title_anchor and s.title for s in specs),
              "每条区域都带文档版本、表题与可复算锚点")
        src = RUN_RECORD.read_text(encoding="utf-8")
        check(all(name not in src for name in
                  ("宁德时代", "300750", "CATL", "Contemporary Amperex")),
              "记录里没有公司名/证券代码：页与区域是**运行级数据**，不是生产规则特判")
        details.append(
            "NOTE §9：这份记录只证明坐标清单自洽；「区域在真 PDF 上到底对不对」由运行期"
            "的哈希核对 + 页级/区域级锚点复算判，不由本模块的骨架断言代替。")

    # ================================================================== §10 读者面的哈希行
    details.append("## §10 读者面「原件哈希」行：印的是**真正比对过**的那个值")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 1")])
        display = _build([_region()], path)
        (record,) = display.regions
        # 区域记录自己抄的那一份可以是空的（现实里登记清单在世时它常常就是空的）；
        # 读者面必须印登记清单里**实际参与比对**的那一个，并在没有可比哈希时如实说「未比对」。
        check(record.region.file_sha256 == "",
              "夹具前提：区域记录自己不带 `file_sha256`（登记清单才是那份值的来源）")
        with_registered = STD.render_source_table_display_markdown(
            display, registered_sha256={"DOC_A": "REGISTERED"})
        check("登记 `REGISTERED`" in with_registered and "一致" in with_registered,
              "正例 10a：登记清单里的哈希被印出来，并给出「一致」的结论")
        check("原件哈希：``" not in with_registered,
              "正例 10b：不再印出一个**空的**「原件哈希」反引号对（旧读法会被读成「没比对过」）")
        without_registered = STD.render_source_table_display_markdown(display)
        check("**未比对**" in without_registered,
              "反例 10c：两侧都没有可比哈希时印「**未比对**」，"
              "不把「没发现不符」冒充成「一致」")
        box.sha_by_path[str(path)] = "TAMPERED"
        tampered = _build([_region()], path, declared="REGISTERED")
        check("**不一致**" in STD.render_source_table_display_markdown(
            tampered, registered_sha256={"DOC_A": "REGISTERED"}),
            "反例 10d：实测与登记不符 ⇒ 读者面印「**不一致**」")
        details.append(
            "NOTE §10：`file_hash_verified` 只说明「没有发现不符」；"
            "「比对确实发生过」要由**印出来的登记值**证明，两者不是同一件事。")

    # ================================================================== §11 原表图直接可见
    details.append("## §11 读者面：原表图**直接可见**（不是只印文件名）")
    with _Sandbox() as box:
        p1 = _FakePage(page_text="营业收入构成 项目 2025年",
                       region_text="营业收入合计 100 200")
        p2 = _FakePage(page_text="（续表）营业收入构成 项目 2025年",
                       region_text="动力电池系统 10 20")
        path = box.install(pages=[p1, p2])
        display = _build([
            _region(row_anchors=("营业收入合计",)),
            _region(region_key="r2", rail="收入构成", page_number=2,
                    title_anchor="（续表）营业收入构成", continuation_of="r1",
                    row_anchors=("动力电池系统",))], path)
        r1, r2 = display.regions
        check(r1.displayable and r1.render_relpath and r2.displayable,
              "夹具前提：主表与续表都可展示且已落盘渲染文件")
        embedded = STD.render_source_table_display_markdown(
            display, image_base="source_display")
        check(f"![收入构成 第1页 区域](source_display/{r1.render_relpath})"
              in embedded,
              "正例 11a：给了图片基准 ⇒ 就地嵌入原件像素（`![…](…)`），"
              "读者在同一页上直接看得见，不必自己去找文件目录")
        check(f"[{r1.render_relpath}](source_display/{r1.render_relpath})"
              in embedded,
              "正例 11b：同时给一条可点击的本地链接（内嵌图打不开时的退路）")
        check(embedded.index(r1.render_relpath) < embedded.index(r2.render_relpath),
              "正例 11c：主表图在续表图**之前**——展示顺序 = 登记顺序（表与续表相邻）")
        check(f"- 渲染文件：`{r1.render_relpath}`　渲染哈希：`{r1.render_sha256}`"
              in embedded,
              "正例 11d：身份行（渲染文件 / 渲染哈希）一个字未改——图片是**加**的，"
              "不是拿图片替换掉身份与哈希")
        no_base = STD.render_source_table_display_markdown(display)
        check("![收入构成" not in no_base and "**未嵌入**" in no_base,
              "反例 11e：没给图片基准 ⇒ **不**造指向空气的相对链接，如实写「未嵌入」"
              "（宁可说没嵌入，也不给一个点不开的图）")
        box.sha_by_path[str(path)] = "TAMPERED"
        tampered = _build([_region(row_anchors=("营业收入合计",))], path,
                          declared="REGISTERED")
        check("![" not in STD.render_source_table_display_markdown(
            tampered, image_base="source_display"),
            "反例 11f：有缺陷的区域**不**嵌入图片——哈希不符时展示的是**另一版原件**，"
            "把它当成「原件长这样」直接给读者看正是要防的事")
        details.append(
            "NOTE §11：嵌图只解决「读者点得开、看得见」；它**不**给区域里的数字任何"
            "格级或事实级资格，也不进 Pack/Writer。")

    # ======================================================== §12 读者面的「请人核对」界面
    details.append("## §12 读者面：区域图旁边的「待人工确认」三问与两种结论")
    with _Sandbox() as box:
        path = box.install(pages=[_FakePage(page_text="营业收入构成",
                                            region_text="营业收入合计 100 200")])
        display = _build([_region()], path)
        (record,) = display.regions
        face = STD.render_source_table_display_markdown(display)
        check("**待人工确认**" in face,
              "正例 12a：读者面逐块给出「待人工确认」，而不是只丢一个内部状态词")
        check(all(q in face for q in STD.CONFIRMATION_QUESTIONS),
              "正例 12b：三个问题（完整 / 清晰 / 忠实）逐条印在区域图旁边，读者知道要看什么")
        check(all(c in face for c in STD.HUMAN_CONFIRMATION_CONCLUSIONS),
              "正例 12c：两种可得结论（完整清晰忠实 / 不完整不清晰不忠实）逐字给出，"
              "不开放第三种说法")
        check("程序不会代签" in face and "由人签署" in face,
              "正例 12d：读者面明说这一步要人来签、程序不代签")

        confirmed = STD.apply_human_confirmation(record, STD.HumanConfirmation(
            confirmer="张三", confirmed_at="2026-10-01T00:00:00",
            conclusion="complete_clear_faithful"))
        confirmed_face = STD.render_source_table_display_markdown(
            STD.SourceTableDisplaySet(
                task_id=display.task_id, section_id=display.section_id,
                source_record_id=display.source_record_id,
                registered_source_id=display.registered_source_id,
                regions=(confirmed,), registrations=display.registrations))
        check("**已由人确认**" in confirmed_face
              and "**待人工确认**" not in confirmed_face,
              "正例 12e：人签过之后这一块换成人话的「已由人确认」，"
              "不再重复问一遍")
        check("不给区域里的任何数字格级或事实级资格" in confirmed_face,
              "反例 12f（边界）：即便人签了，读者面那句确认**只覆盖区域本身**，"
              "不越界成任何数字资格")

    details.append(
        "NOTE §12：这一段加的是**读者能照着做**的问法与结论词；"
        "产生 `human_confirmed` 的仍只有 `apply_human_confirmation` 一条路，"
        "本模块没有任何一处把渲染、比较或自动检查写成「已确认」。")

    details.append(
        "NOTE 本模块只证明展示路径的**边界**；它不产生任何数字权威、不证明 Contract "
        "`set_complete`、不构成 TS5 通过或系统放行。")
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

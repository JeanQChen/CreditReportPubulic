"""来源归属轴专项评测（sections/source_attribution.py，`srattr-1`）。

纯离线：不调 LLM、不联网、不写任何库。覆盖指令 D §二「三条日期轴」里 (b) 来源归属这一轴的
全部纪律，以及它与 (a) 事实适用期、(c) `report_as_of` 的**不可互相顶替**：

- 归属语只由系统从**已登记**身份渲染（写者一个字也不能写）；
- 披露日只在 `DisclosureDateState.state == "verified"` 时才有值，且必须是日粒度；
- 不可核实就渲染成「披露日未知」，**不得**用入库时间 / PDF 元数据 / 上传时间 / 财务期末顶替；
- 月粒度线索不得升格成披露日；
- 逐条跳过：一条查不到不影响其它条渲染出来，且跳过的那条不伪造归属语；
- `render_citation` 的**兼容性**：不传归属语时渲染结果与旧版逐字节相同（旧的调用方零影响）。

用法: python -m evals.test_source_attribution
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import source_manifest as SM  # noqa: E402
from harness.schema import CitationRef  # noqa: E402
from sections import publishable_report as PR  # noqa: E402
from sections import source_attribution as SRA  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
    try:
        fn()
    except exc as e:  # noqa: BLE001
        if needle and needle not in str(e):
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {msg}：异常信息里没有 {needle!r}（实际 {str(e)[:160]!r}）")
        else:
            _results["passed"] += 1
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg}：抛了 {type(e).__name__}（应为 {exc.__name__}）：{str(e)[:160]}")
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL {msg}：未拒绝")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

VERIFIED = SM.DisclosureDateState(
    date="2025-04-15", state="verified", period_hint=None, period_hint_precision=None,
    basis="文档封面 p1 b0 原文：2025年4月15日", ingestion_time="2026-01-02T03:04:05Z")

UNKNOWN_MONTH_ONLY = SM.DisclosureDateState(
    date=None, state="unknown", period_hint="2025-04", period_hint_precision="month",
    basis="封面仅给出月粒度；入库时间 / PDF 元数据不得冒充披露日",
    ingestion_time="2026-01-02T03:04:05Z")


def _attr(disclosure, *, page: int = 12, document_id: str = "doc_ndsd_year",
          version: str = "v1", label: str = "NDSD_year.pdf（年度报告）"):
    return SRA.build_source_attribution(
        document_id=document_id, document_version=version, document_label=label,
        page_number=page, disclosure=disclosure)


# ============================================================ §1 版本与封闭词表 pin

check(SRA.SOURCE_ATTRIBUTION_VERSION == "srattr-1",
      f"归属语口径版本必须是 'srattr-1'（实际 {SRA.SOURCE_ATTRIBUTION_VERSION!r}）")
check(SRA.ATTRIBUTION_DISCLOSURE_STATES == ("verified", "unknown"),
      "披露状态必须与 `DisclosureDateState.state` 同域（不另立一套）")
check(SRA.DISCLOSURE_UNKNOWN_LABEL == "披露日未知",
      "不可核实时的固定表述必须是「披露日未知」（不得换成暗示有日期的写法）")
for name in ("ingestion_time", "pdf_metadata", "upload_time", "content_report_period_end"):
    check(name in SRA.FORBIDDEN_DISCLOSURE_SUBSTITUTES,
          f"被禁的披露日替代物必须逐条点名 {name!r}"
          "（AGENTS.md §5：上传日 / PDF 元数据 / 财务期末不得冒充披露日）")

# ============================================================ §2 verified 渲染

a = _attr(VERIFIED)
check(a.disclosure_state == "verified" and a.disclosure_date == "2025-04-15",
      "可核实的披露日必须原样带出")
check(a.page_number == 12 and a.document_version == "v1",
      "归属语必须带出精确页码与版本（读者要能判断「这是哪一版哪一页」）")
text = SRA.render_source_attribution(a)
check("2025-04-15" in text and "第 12 页" in text and "doc_ndsd_year@v1" in text,
      f"渲染必须同时出现披露日、页码与登记身份（实际 {text!r}）")
check(text.startswith("据 "), f"归属语必须以「据 」开头（实际 {text!r}）")
check(SRA.render_source_attribution(a) == text,
      "同一对象两次渲染必须逐字节相同（确定性）")

# ============================================================ §3 unknown 渲染

b = _attr(UNKNOWN_MONTH_ONLY, page=7, document_id="doc_old", version="v2",
          label="OLD_year.pdf")
check(b.disclosure_state == "unknown" and b.disclosure_date == "",
      "不可核实时不得带日期")
text_b = SRA.render_source_attribution(b)
check(SRA.DISCLOSURE_UNKNOWN_LABEL in text_b,
      f"不可核实必须渲染成「披露日未知」（实际 {text_b!r}）")
check("披露日 披露日未知" not in text_b,
      f"不得出现「披露日 披露日未知」这种叠字（实际 {text_b!r}）")
check("2025-04" not in text_b,
      "月粒度线索不得出现在归属语里（它没有升格成披露日）")
check(b.page_number == 7 and "doc_old@v2" in text_b,
      "不可核实只影响日期那一段，页码与版本照常带出")

# ============================================================ §4 逐条拒绝：替代物与升格

# 月粒度线索不得升格成披露日（"2025-04" 只有月，没有日）
expect_error(
    lambda: _attr(SM.DisclosureDateState(
        date="2025-04", state="verified", period_hint=None, period_hint_precision=None,
        basis="x", ingestion_time="2026-01-02T03:04:05Z")),
    SRA.SourceAttributionError, "verified 的日期不是日粒度必须被拒", needle="日粒度")
expect_error(
    lambda: _attr(SM.DisclosureDateState(
        date="2025年4月15日", state="verified", period_hint=None,
        period_hint_precision=None, basis="x", ingestion_time="2026-01-02T03:04:05Z")),
    SRA.SourceAttributionError, "中文写法的日期不得直接当披露日", needle="日粒度")

# state=unknown 却带日期：判断结果自身先被唯一实现处拒（不另立第二套判据）
expect_error(
    lambda: _attr(SM.DisclosureDateState(
        date="2025-04-15", state="unknown", period_hint=None, period_hint_precision=None,
        basis="入库时间 / PDF 元数据不得冒充", ingestion_time="2026-01-02T03:04:05Z")),
    ValueError, "unknown 却带日期必须被拒", needle="升格")

# 同一纪律在值对象层也必须成立（绕过构造器直接构造也拒）
expect_error(
    lambda: SRA.SourceAttribution(
        attribution_id="x", attribution_version="srattr-1", document_id="d",
        document_version="v", document_label="L", page_number=1,
        disclosure_date="2025-04-15", disclosure_state="unknown", disclosure_basis="b"),
    SRA.SourceAttributionError, "值对象层 unknown 却带日期必须被拒",
    needle="不得渲染出一个日期")
expect_error(
    lambda: SRA.SourceAttribution(
        attribution_id="x", attribution_version="srattr-1", document_id="d",
        document_version="v", document_label="L", page_number=1,
        disclosure_date="", disclosure_state="verified", disclosure_basis="b"),
    SRA.SourceAttributionError, "值对象层 verified 却无日期必须被拒", needle="date 为空")

# unknown 的 basis 必须写明「入库时间 / PDF 元数据不得冒充」（沿用唯一实现处的纪律）
expect_error(
    lambda: _attr(SM.DisclosureDateState(
        date=None, state="unknown", period_hint=None, period_hint_precision=None,
        basis="不知道", ingestion_time="2026-01-02T03:04:05Z")),
    ValueError, "unknown 的 basis 未写明不可核实的理由必须被拒")

# 披露日与入库时间相同 ⇒ 入库时间冒充披露日
expect_error(
    lambda: _attr(SM.DisclosureDateState(
        date="2026-01-02", state="verified", period_hint=None, period_hint_precision=None,
        basis="x", ingestion_time="2026-01-02")),
    SRA.SourceAttributionError, "披露日等于入库时间必须被拒", needle="入库时间")

# 不得用裸字符串（或别的字段）顶替披露日
expect_error(
    lambda: SRA.build_source_attribution(
        document_id="d", document_version="v", document_label="L", page_number=1,
        disclosure="2025-04-15"),
    SRA.SourceAttributionError, "布尔/裸串顶替 DisclosureDateState 必须被拒",
    needle="DisclosureDateState")

# 页码必须精确：0 / 负数 / 布尔 / 缺页都不构造
for bad_page in (0, -1, True, "12", None):
    expect_error(
        lambda p=bad_page: _attr(VERIFIED, page=p),
        SRA.SourceAttributionError, f"页码 {bad_page!r} 必须被拒（没有精确页码就不构造归属语）",
        needle="page_number")

# 文档身份不得是路径或文件名（内容寻址）
expect_error(
    lambda: _attr(VERIFIED, document_id="data/raw/x.pdf"),
    SRA.SourceAttributionError, "document_id 是路径必须被拒", needle="路径")
expect_error(
    lambda: _attr(VERIFIED, label=""),
    SRA.SourceAttributionError, "空材料名必须被拒", needle="document_label")

# ============================================================ §5 身份：内容寻址 + 回读

check(SRA.derive_attribution_id(a) == a.attribution_id,
      "attribution_id 必须与内容自洽（内容寻址）")
check(a.attribution_id.startswith("srattr_") and len(a.attribution_id) == 31,
      f"attribution_id 前缀/长度必须稳定（实际 {a.attribution_id!r}）")
a2 = _attr(VERIFIED)
check(a2.attribution_id == a.attribution_id,
      "同内容必得同 id（确定性）")
a3 = _attr(VERIFIED, page=13)
check(a3.attribution_id != a.attribution_id,
      "改一个字节（页码）必得新 id")
check(SRA.SourceAttribution.from_dict(a.to_dict()) == a,
      "to_dict → from_dict 必须逐字段回读一致")
expect_error(lambda: SRA.SourceAttribution.from_dict({**a.to_dict(), "extra": 1}),
             SRA.SourceAttributionError, "未登记字段必须被拒", needle="未登记")
expect_error(
    lambda: SRA.SourceAttribution(
        attribution_id="srattr_" + "0" * 24, attribution_version="srattr-1",
        document_id="d", document_version="v", document_label="L", page_number=1,
        disclosure_date="2025-04-15", disclosure_state="verified", disclosure_basis="b"),
    SRA.SourceAttributionError, "伪造 attribution_id 必须被拒", needle="与内容不符")
expect_error(
    lambda: SRA.SourceAttribution(
        attribution_id="x", attribution_version="srattr-0",
        document_id="d", document_version="v", document_label="L", page_number=1,
        disclosure_date="2025-04-15", disclosure_state="verified", disclosure_basis="b"),
    SRA.SourceAttributionError, "旧口径版本必须被拒", needle="不是当前口径")

# ============================================================ §6 逐条折表：查不到就跳过

entry_verified = SM.SourceManifestEntry(
    company_id="c", document_id="doc_ndsd_year", document_version="v1", file_sha256="a" * 64,
    file_size=1, page_count=10, source_name="NDSD_year.pdf", source_path=None,
    registry_status="registered", material_group="g", registered_source_type="annual_report",
    evidence_set_version="s1", type_judgment=None, disclosure=VERIFIED,
    content_report_period=None, policy_effect=None, eligibility="eligible_current",
    eligibility_reason="")

entries = {("doc_ndsd_year", "v1"): entry_verified}
rows = [
    ("ev_1", "doc_ndsd_year", "v1", "NDSD_year.pdf", 12),
    ("ev_2", "doc_missing", "v9", "MISSING.pdf", 3),
    ("ev_3", "doc_ndsd_year", "v1", "NDSD_year.pdf", None),
]
out = SRA.attributions_by_evidence(rows, entries_by_document=entries)
check(set(out) == {"ev_1"},
      f"逐条跳过：登记缺失与缺页码的两条不渲染，其余照常（实际 {sorted(out)}）")
check("ev_1" in out and out["ev_1"].disclosure_date == "2025-04-15",
      "折表里的归属语与单条构造必须同一口径")
# 同一实现、另一个键轴（M930-3 读者面按 `material_id` 键）：不是第二套口径。
out_by_material = SRA.attributions_by_key(
    [("mat-1", "doc_ndsd_year", "v1", "NDSD_year.pdf", 12),
     ("mat-2", "doc_missing", "v9", "MISSING.pdf", 3)],
    entries_by_document=entries)
check(out_by_material["mat-1"].identity_body() == out["ev_1"].identity_body(),
      "换键轴不得换口径：`material_id` 键与 `evidence_id` 键渲染出的是同一条归属语")

# ============================================================ §7 只读登记入口（已落盘 JSON）

manifest_document = {
    "policy_version": "sm-2",
    "entries": [
        {"document_id": "doc_ndsd_year", "document_version": "v1",
         "source_name": "NDSD_year.pdf",
         "disclosure": {"date": "2025-04-15", "state": "verified",
                        "period_hint": None, "period_hint_precision": None,
                        "basis": VERIFIED.basis, "ingestion_time": VERIFIED.ingestion_time}},
        {"document_id": "doc_bad", "document_version": "v1", "source_name": "BAD.pdf"},
    ],
}
registry = SRA.source_registry_from_manifest_document(manifest_document)
check(set(registry) == {("doc_ndsd_year", "v1")},
      f"只读入口逐条读入：缺 disclosure 的那条只丢它自己（实际 {sorted(registry)}）")
readonly_out = SRA.attributions_by_key(
    [("mat-1", "doc_ndsd_year", "v1", "", 12)], entries_by_document=registry)
check(readonly_out["mat-1"].document_label == "NDSD_year.pdf",
      "行里没给材料名时退回登记条目的 `source_name`（不自造名字）")
check(readonly_out["mat-1"].identity_body() == out["ev_1"].identity_body(),
      "只读入口与活对象入口必须渲染出**同一条**归属语（同一个真值）")
expect_error(
    lambda: SRA.RegisteredSourceIdentity.from_entry_dict(
        {"document_id": "d", "document_version": "v"}),
    SRA.SourceAttributionError, "缺字段的登记条目必须被拒", needle="缺字段")
expect_error(
    lambda: SRA.source_registry_from_manifest_document({"entries": "x"}),
    SRA.SourceAttributionError, "entries 不是序列必须被拒", needle="序列")

# ============================================================ §7 render_citation 兼容与接线

check(PR.CITATION_DISPLAY_VERSION == "p4-citation-display-2",
      f"渲染口径变了，引用显示版本必须前进（实际 {PR.CITATION_DISPLAY_VERSION!r}）")

ref = CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=12)
legacy = PR.render_citation(ref, doc_names={"ev_1": "NDSD_year.pdf（年度报告）"})
legacy_explicit = PR.render_citation(
    ref, doc_names={"ev_1": "NDSD_year.pdf（年度报告）"}, attributions={})
check(legacy == legacy_explicit,
      "不传归属语与传空归属语必须渲染出同一结果（兼容路径逐字节相同）")
check(legacy == "NDSD_year.pdf（年度报告） · 第12页",
      f"旧的中性渲染必须一字未改（实际 {legacy!r}）")

with_attr = PR.render_citation(
    ref, doc_names={"ev_1": "NDSD_year.pdf（年度报告）"}, attributions=out)
check(with_attr == SRA.render_source_attribution(out["ev_1"]),
      "给了归属语就用归属语渲染（材料名 + 身份 + 版本 + 披露日 + 页码）")
check("2025-04-15" in with_attr and "doc_ndsd_year@v1" in with_attr,
      f"读者面必须能看到可核实披露日与登记身份（实际 {with_attr!r}）")

# 未登记的引用**逐条**退回中性渲染，不构造半条归属语
ref_missing = CitationRef(ref_type="evidence", evidence_id="ev_2", page_number=3)
check(PR.render_citation(ref_missing, doc_names={}, attributions=out) == "本地披露材料 · PDF物理页 3",
      "归属语里没有的那条引用必须走既有中性渲染")

# 结构化 / 外部引用不受本轴影响（它们各有自己的作者与口径）
struct = CitationRef(ref_type="structured", formula_id="SOLV_CURRENT_RATIO",
                     period="2025-12-31", snapshot_id="snap-x")
check(PR.render_citation(struct, doc_names={}, attributions=out).startswith("财务快照 "),
      "结构化引用仍走财务快照渲染（归属语不参与）")
check(PR.render_citation(CitationRef(ref_type="external", source_snapshot_id="sb" * 6),
                         doc_names={}, attributions=out).startswith("外部来源 "),
      "外部引用仍走外部来源渲染（归属语不参与）")

# `build_publication` 的开关：不给登记条目就不渲染归属语（绝不另猜一个披露日）
import inspect  # noqa: E402

sig = inspect.signature(PR.build_publication)
check("source_entries" in sig.parameters,
      "build_publication 必须接受登记条目（披露状态的真值来源在 SourceManifestEntry.disclosure）")
check(PR._ro_evidence_attribution_rows.__doc__ and
      "只读" in PR._ro_evidence_attribution_rows.__doc__,
      "取归属语行的那条查询必须是只读查询（mode=ro，不建表、不迁移）")

def main() -> dict:
    """套件入口：把本模块的检查结果交回运行器（`evals/run_evals.py`）。

    **检查体在导入期执行**（本模块是脚本式写法：夹具与 `check(...)` 都在模块顶层）。
    因此本函数**只交付结果，不得再跑一遍**——重复执行会把同一次运行数成两遍。

    **为什么必须存在这个函数**：运行器对每个模块先 `import_module` 再取 `mod.main()`。
    没有它，模块要么在导入期就 `AttributeError`（被记成 CRASH），要么——更糟——沿用一个
    **模块级**的 `sys.exit(...)`：`SystemExit` 是 `BaseException`，运行器的
    `except Exception` 接不住，套件会在**不打印 `TOTAL`** 的情况下以退出码 0 结束，
    它之后还没跑的模块静默消失，日志看起来仍像「基本跑完」。这条纪律与
    `evals/test_m930_3_r8_offline_replay.py` 钉的是同一条（那里也有一个反例）。
    """
    return _results


if __name__ == "__main__":
    print(json.dumps(_results, ensure_ascii=False, indent=2))
    sys.exit(1 if _results["failed"] else 0)

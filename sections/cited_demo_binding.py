"""Read-only, versioned **display binding** for the side-by-side M930-4/5 demo.

What this is
------------

A single, explicit, hash-pinned statement of *which two already-persisted
artifacts the demo page shows together*, and nothing more:

* the **company section** comes from the single-section flat run
  ``m930_3_cited_real_ndc5_company_r1``;
* the **financial A2** (deterministic balance-sheet presentation) comes from
  ``m930_3_cited_real_dual_v2_r1/financial``.

These are **two different runs**. The binding is a *page display session*: it is
not a new dual-section run, not a ``report_version``, not a system-release
decision, and it never copies, concatenates or writes back any historical
artifact. Every artifact it reads is pinned by SHA-256 down to the byte, so a
re-signed or edited historical file refuses to load rather than silently
degrading into "still fine".

What this is not
----------------

* It does **not** loosen ``sections.cited_demo_loader`` identity checks. That
  loader is untouched apart from an optional image-directory prefix, and this
  module re-uses its decoders and its display-set validation verbatim.
* It does **not** re-bound the M930-4 r6 sidecar. That sidecar belongs to
  ``m930_3_cited_real_dual_v2_r1`` and its cross-section / unused-material
  opinions are **not** applied to the ``ndc5`` company prose. The only thing
  this module reads out of it is the ``financial_a2`` scope identity, and only
  after recomputing that identity from the A2 bytes on disk and requiring a
  character-for-character match.

Self-check: ``python -m sections.cited_demo_binding``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from sections import cited_demo_loader as _loader
from sections import cited_financial_table as financial_table
from sections import cited_report
from sections import cited_review
from sections import cited_writer
from sections import sentence_check


#: 展示绑定的版本号。它**不是** `report_version`，也不是任何产物的发布期：产物升级时它不动，
#: 「页面把哪两个来源并列、按哪条规则核对」这件事本身变了才动。
BINDING_VERSION = "cdb-1"

A2_FILENAME = "cited_balance_structure__fin_balance_structure.json"
A2_MARKDOWN_FILENAME = "cited_balance_structure__fin_balance_structure.md"


class DisplayBindingError(ValueError):
    """The pinned display binding cannot be honoured; the page must refuse to show it."""


@dataclass(frozen=True)
class DisplaySource:
    """一个并列来源：哪一个 run、读哪些文件、每个文件钉在哪个字节哈希上。"""

    key: str
    run_id: str
    subdir: str
    label: str
    producer: str
    artifacts: tuple[tuple[str, str], ...]

    @property
    def run_relpath(self) -> str:
        return f"{self.run_id}/{self.subdir}" if self.subdir else self.run_id


@dataclass(frozen=True)
class SourceDocument:
    """已保存案例的一份来源 PDF。哈希取自磁盘实测，不是从页面上抄来的。"""

    document_id: str
    filename: str
    sha256: str
    size_bytes: int
    role: str


#: 两个来源各自的产物清单（相对各自目录）。哈希为 2026-10-05 从磁盘实测。
COMPANY_SOURCE = DisplaySource(
    key="company",
    run_id="m930_3_cited_real_ndc5_company_r1",
    subdir="",
    label="公司节 · 主营业务正文（单节扁平运行）",
    producer="cited_prose_writing + cited_prose_rework + cited_prose_review（真实模型，3 次调用）",
    artifacts=(
        ("cited_report_version.json",
         "FAFCFF7D7D6901FD2DF4FF2B57F5659C638DB8390A17A6308EC915EB6FE5EF10"),
        ("cited_input_manifest.json",
         "62331DEB788288641AB84F4B85B15DD7E93FA8B7D97704F716B96F8820A2DD54"),
        ("cited_prose_reworked.json",
         "BB4D747C5AB63AE0B3A79FD9F53299E774A99C0D289C830DD25857469E1B0E9B"),
        ("cited_prose.json",
         "DB87220363FB6C418060B4B8FCF7BE963E96BE1209F285144A528E50317CF170"),
        ("sentence_checks.json",
         "5D135671818958BA9F082F117437D14B725F5BE986488D3C9D2CE72B9518CC32"),
        ("review_issues.json",
         "01380F01ACF4ED51DCD6FE7686BEB1D61A0A24430E892DD629B860FA541193BA"),
        ("cited_metric_tables.json",
         "DFC6421CC827AF671291E9A07602F15D98A57CB950847AE83CCC438BF18D4BB5"),
        ("source_table_display.json",
         "848DE3224ED5331B1D6E94BECBD92EF4A94E1A43D591AD163C806164B393B57E"),
        ("cited_gap_bins.json",
         "54DA18B0038AA293F51DEA21FA4F46DA492F9560221433345F280359112DBA4E"),
        ("withheld_candidates.json",
         "D435575F2D5DBD1ED59AB01AE495AA156B01D413AE8D744DC74B6398ED1AEDC0"),
        ("cited_rework.json",
         "C7B62E2A3DA36D3ACB55B1C622E3C983672BEC1DD0095485F48FF06EA46100F1"),
        ("cited_preview.md",
         "D5D66C10D942633AA60568185F63957ADB8BA12DB485644F37B913A988BA13BD"),
        ("demo_page.md",
         "F854045DCE707449A8EA0A38BA32B8FED852F315FE5093842A24F58A3156A782"),
        ("cited_call_ledger.json",
         "9CE88972BE50D2FC604F5562467C3698E212F506E76B4B3D5D7BFD1DA61234A2"),
    ),
)

FINANCIAL_SOURCE = DisplaySource(
    key="financial",
    run_id="m930_3_cited_real_dual_v2_r1",
    subdir="financial",
    label="财务 A2 · 资产负债结构确定性呈现",
    producer="deterministic_presentation（权威财务事实 + Decimal，模型调用 0 次）",
    artifacts=(
        (A2_MARKDOWN_FILENAME,
         "502A9B68C0075A3C47B273BAD98FA376732F5B0E7B0B6BF51C185791749FC728"),
        (A2_FILENAME,
         "C4E1C68C5C4E2E98CEDCBFCAA9B937B6566DA3F472C1EAE346792D9E226705ED"),
        ("cited_report_version.json",
         "F3F8F4990E582BA57036E57184115A7F69469654D0B88BD559420BB77E169D6C"),
    ),
)

#: 已保存案例的三份电子来源。页面第一屏只按**内存里算出的** SHA-256 与这里逐一核对。
SOURCE_DOCUMENTS = (
    SourceDocument(document_id="NDSD_2025_year", filename="NDSD_2025_year.pdf",
                   sha256="c15272977147dee7e6935a38ea0e4fd6855370aabb106f54cfe20f7cf6048ec9",
                   size_bytes=2043710, role="最近一期年度报告（当期口径主来源）"),
    SourceDocument(document_id="NDSD_2024_year", filename="NDSD_2024_year.pdf",
                   sha256="b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad",
                   size_bytes=2070073, role="上一期年度报告（历史与冲突来源）"),
    SourceDocument(document_id="NDSD_KCZ_2026", filename="NDSD_KCZ_2026.pdf",
                   sha256="2b3a1fb3de97f23c9e6fc8c0d54adf0633265bb1b452a881d15bd3a0ba286dae",
                   size_bytes=1558483, role="募集说明书（本次表中区来源）"),
)


@dataclass(frozen=True)
class CompanyDisplay:
    """公司节的**有效稿**及其同 run 核对/审阅/展示集。字段名与 `CitedDemoSection` 对齐。"""

    report_version: dict[str, Any]
    manifest: dict[str, Any]
    draft: dict[str, Any]
    checks: dict[str, Any]
    review: dict[str, Any]
    metric_tables: dict[str, Any]
    source_display: dict[str, Any]
    preview_markdown: str
    demo_markdown: str
    gap_bins: dict[str, Any]
    withheld: dict[str, Any]
    rework: dict[str, Any]
    initial_draft: dict[str, Any]
    source_regions: tuple[dict[str, Any], ...]
    source_images: dict[str, bytes]
    ledger: dict[str, Any] | None = None
    review_call_attested: bool = False
    section_id: str = "company"
    balance_structure: dict[str, Any] | None = None

    @property
    def hard_sentence_ids(self) -> tuple[str, ...]:
        """机械层**标红**的全部句子（事实安全族 ∪ 栏目覆盖族），与旧展示口径一致。"""
        return tuple(str(s["sentence_id"]) for s in self.checks["sentence_states"]
                     if s["verdict"] == "hard_error")

    @property
    def fact_safety_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.checks["fact_safety_sentence_ids"])

    @property
    def column_coverage_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.checks["column_coverage_sentence_ids"])

    @property
    def blocking_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.checks["blocked_sentence_ids"])

    @property
    def sentence_count(self) -> int:
        return int(self.report_version["sentence_count"])

    def sentence_texts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for subsection in self.draft["subsections"]:
            for paragraph in subsection["paragraphs"]:
                for sentence in paragraph["sentences"]:
                    out[str(sentence["sentence_id"])] = str(sentence["text"])
        return out


@dataclass(frozen=True)
class A2ReviewIdentity:
    """A2 既有审阅的**独立内容身份**核对结果。

    `verified` 为真当且仅当：从**当前盘上的 A2 产物**重新算出的 `financial_a2` scope 版本，
    与旧侧车里那份逐字相同。相同才允许把那份审阅单独列出来；否则页面必须写「未审」。
    """

    scope_kind: str
    recomputed_scope_version: str
    recorded_scope_version: str
    recorded_bundle_id: str
    sidecar_label: str
    sidecar_sha256: str
    verified: bool
    reason: str


@dataclass(frozen=True)
class FinancialDisplay:
    """财务 A2 的确定性呈现产物；`balance_structure` 直接喂给原有的 A2 渲染器。"""

    run_id: str
    subdir: str
    report_version: dict[str, Any]
    markdown: str
    balance_structure: dict[str, Any]
    initial_draft: dict[str, Any]
    decoded_version: cited_report.CitedReportVersion | None = None
    section_id: str = "financial"

    @property
    def fingerprint(self) -> str:
        return str(self.balance_structure["fingerprint"])

    @property
    def items(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.balance_structure["items"])

    @property
    def notes(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.balance_structure.get("notes") or ())

    @property
    def reading_count(self) -> int:
        return sum(len(item.get("readings") or ()) for item in self.items)

    @property
    def gap_count(self) -> int:
        return (sum(len(item.get("gaps") or ()) for item in self.items)
                + sum(1 for note in self.notes if note.get("state") == "gap"))


@dataclass(frozen=True)
class CitedDisplayBinding:
    binding_version: str
    company: CompanyDisplay
    financial: FinancialDisplay
    documents: tuple[SourceDocument, ...]
    artifact_hashes: dict[str, str]
    a2_review: A2ReviewIdentity | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def source(self, key: str) -> DisplaySource:
        if key == "company":
            return COMPANY_SOURCE
        if key == "financial":
            return FINANCIAL_SOURCE
        raise DisplayBindingError(f"未知来源：{key!r}")

    def document(self, document_id: str) -> SourceDocument | None:
        return next((d for d in self.documents
                     if d.document_id == document_id), None)


# ---------------------------------------------------------------- verification


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DisplayBindingError(message)


def _pin(snapshot: _loader._Snapshot, source: DisplaySource) -> dict[str, str]:
    """逐文件按**冻结哈希**核对；任何不符即拒，不给「降级为仍可用」这条路。"""
    verified: dict[str, str] = {}
    for relative, expected in source.artifacts:
        #: `artifacts` 里的路径是相对**来源自己那层目录**（`source.subdir`）写的；
        #: 快照却锚在整个 run 目录上，这里补回前缀。
        inside = f"{source.subdir}/{relative}" if source.subdir else relative
        try:
            data = snapshot.read(inside)
        except _loader.CitedDemoLoadError as exc:
            raise DisplayBindingError(
                f"{source.run_relpath}/{relative} 读不到：{exc}") from exc
        actual = _sha256(data)
        if actual != expected:
            raise DisplayBindingError(
                f"{source.run_relpath}/{relative} 与展示绑定的字节哈希不符："
                f"声明 {expected}，实测 {actual}。它已被改写或换过版本，"
                "本绑定拒绝展示（不降级为「仍可看」）。")
        verified[f"{source.run_relpath}/{relative}"] = actual
    return verified


def _source_dirs(results_root: str | Path) -> tuple[Path, Path, Path]:
    root = Path(results_root).resolve(strict=True)
    company_dir = _loader._safe_run_dir(results_root, COMPANY_SOURCE.run_id)
    financial_dir = _loader._safe_run_dir(results_root, FINANCIAL_SOURCE.run_id)
    return root, company_dir, financial_dir


def _load_company(snapshot: _loader._Snapshot) -> CompanyDisplay:
    report_version = snapshot.json("cited_report_version.json")
    manifest = snapshot.json("cited_input_manifest.json")
    initial = snapshot.json("cited_prose.json")
    reworked = snapshot.json("cited_prose_reworked.json")
    checks = snapshot.json("sentence_checks.json")
    review_raw = snapshot.json("review_issues.json")
    tables = snapshot.json("cited_metric_tables.json")
    display = snapshot.json("source_table_display.json")
    gap_bins = snapshot.json("cited_gap_bins.json")
    withheld = snapshot.json("withheld_candidates.json")
    rework = snapshot.json("cited_rework.json")
    preview = snapshot.text("cited_preview.md")
    demo = snapshot.text("demo_page.md")

    try:
        version = cited_report.CitedReportVersion.from_persisted_dict(report_version)
        writermanifest = cited_writer.CitedWriterInputManifest.from_dict(manifest)
        effective = cited_writer.CitedProseDraft.from_dict(reworked)
        initial_draft = cited_writer.CitedProseDraft.from_dict(initial["draft"])
        check = sentence_check.SentenceCheckReport.from_dict(checks)
        review = _loader._review_from_wire(review_raw)
        metric = financial_table.CitedMetricTableOutcome.from_dict(tables)
    except (_loader.CitedDemoLoadError, cited_report.CitedReportError,
            cited_writer.CitedWriterError, sentence_check.SentenceCheckError,
            financial_table.CitedMetricTableError,
            cited_review.CitedReviewError) as exc:
        raise DisplayBindingError(f"公司节产物不可按现行版本解码：{exc}") from exc

    #: 有效稿是 `cited_prose_reworked.json`——`cited_report_version.json` 的 `draft_id`
    #: 必须指向它。两份草稿在本次**逐字相同**（返修没有产生改写），但身份不同，
    #: 页面读的是被版本记录**实际绑定**的那一份。
    _require(version.section_id == "company", "公司节产物声明的章节不是 company")
    _require(version.draft_id == effective.draft_id,
             f"公司节 report_version 绑定的草稿是 {version.draft_id!r}，"
             f"而有效稿是 {effective.draft_id!r}；不得拿另一份草稿顶替")
    _require(version.input_manifest_id == writermanifest.manifest_id,
             "公司节 Writer 清单 ID 与 report_version 不符")
    _require(version.check_report_id == check.report_id, "公司节逐句核对 ID 不符")
    _require(version.review_bundle_id == review.bundle_id, "公司节独立审阅输入 ID 不符")
    _require(version.report_version == str(gap_bins.get("report_version") or ""),
             "公司节缺口账本绑的不是本节 report_version")
    _require(int(version.sentence_count) == len(effective.sentences()),
             "公司节句数与有效稿不符")
    _require(version.publishability == "not_publishable", "演示绑定不得展示可发布产物")

    try:
        _loader._validate_display(snapshot, "company", display, version.report_version,
                                  prefix="")
    except _loader.CitedDemoLoadError as exc:
        raise DisplayBindingError(f"公司节原 PDF 展示集校验失败：{exc}") from exc

    #: 这份逐句审阅是不是**本 run 自己账本里那次成功的调用**。是，页面才把它读作
    #: 「既有独立审阅」；否则只能读作未核验的历史文本。判定只看 `ndc5` 自己的账本，
    #: 不借任何别的 run 的读数。
    ledger = snapshot.json("cited_call_ledger.json")
    review_call = (review_raw.get("call") or {})
    call_id = str(review_call.get("call_id") or "")
    attested = bool(call_id) and any(
        str(attempt.get("call_id") or "") == call_id
        and attempt.get("category") == "cited_prose_review"
        and attempt.get("section_id") == "company"
        and attempt.get("status") == "ok"
        for attempt in ((ledger.get("call_budget") or {}).get("attempts") or ()))
    _require(attested,
             "公司节逐句审阅的调用没有落在本 run 的成功账本里；"
             "不得把它当成本 run 的既有独立审阅")

    regions = tuple(display["regions"])
    images = {str(r["region"]["region_key"]):
              snapshot.read(f"source_display/{r['render_relpath']}") for r in regions}
    return CompanyDisplay(
        report_version=report_version, manifest=manifest, draft=reworked,
        checks=checks, review=review_raw, metric_tables=tables,
        source_display=display, preview_markdown=preview, demo_markdown=demo,
        gap_bins=gap_bins, withheld=withheld, rework=rework, initial_draft=initial,
        source_regions=regions, source_images=images,
        ledger=ledger, review_call_attested=attested,
        # `_render_a1` 等既有渲染器按 `CitedDemoSection` 的字段名消费，都是字典字段。
        # `balance_structure` 只归财务节；公司节没有 A2。
    )


def _load_financial(snapshot: _loader._Snapshot) -> FinancialDisplay:
    prefix = "financial/"
    report_version = snapshot.json(prefix + "cited_report_version.json")
    initial = snapshot.json(prefix + "cited_prose.json")
    markdown = snapshot.text(prefix + A2_MARKDOWN_FILENAME)
    balance = snapshot.json(prefix + A2_FILENAME)
    _require(balance.get("section_id") == "financial", "A2 产物声明的章节不是 financial")
    _require(balance.get("producer_kind") == "deterministic_presentation",
             "A2 产物不是确定性呈现产出者")
    _require(int(balance.get("model_calls_issued", -1)) == 0,
             "A2 产物声明了模型调用，与「确定性呈现」矛盾")
    _require(balance.get("fingerprint") == _loader._canonical_sha(
        {k: v for k, v in balance.items() if k != "fingerprint"}), "A2 内容指纹不符")
    _require(markdown.strip().startswith("#"), "A2 markdown 产物为空或不合形状")
    try:
        decoded = cited_report.CitedReportVersion.from_persisted_dict(report_version)
    except Exception as exc:  # noqa: BLE001 - 解不开就当没有可核验的财务节版本
        decoded = None
    _require(decoded is not None and decoded.section_id == "financial",
             "财务节 report_version 解不开或章节不符")
    return FinancialDisplay(run_id=FINANCIAL_SOURCE.run_id,
                            subdir=FINANCIAL_SOURCE.subdir,
                            report_version=report_version,
                            markdown=markdown, balance_structure=balance,
                            initial_draft=initial, decoded_version=decoded)


def _a2_review_identity(financial: FinancialDisplay, *,
                        results_root: str | Path) -> A2ReviewIdentity | None:
    """核对「旧侧车里那份 A2 审阅」是否绑在**当前盘上这一份 A2** 上。

    做法不是比名字，而是把 `financial_a2` 的 scope 版本**从当前 A2 字节重算一遍**
    （`assurance.report_review._a2_scope` 是零调用的纯函数），再与侧车里那份逐字比对。
    侧车读不到或对不上，都返回 `None`，由页面写「未审」——不得靠同名栏目推断。
    """
    from assurance import report_review as RR

    try:
        run = _loader.load_cited_run(FINANCIAL_SOURCE.run_id,
                                     results_root=results_root)
        sidecar = _loader.load_assurance_sidecar(run, results_root=results_root)
    except Exception:  # noqa: BLE001 - 读不到就是「未审」，不是错误路径
        return None

    recorded = next((s for s in sidecar.reading.get("report_review_scopes") or ()
                     if s.get("scope_kind") == "financial_a2"), None)
    if recorded is None:
        return None
    try:
        facts = RR.SectionReviewFacts(section_id="financial", manifest=None, draft=None,
                                      version=financial.decoded_version,
                                      balance=financial.balance_structure)
        rebuilt = RR._a2_scope(report_id=FINANCIAL_SOURCE.run_id, facts=facts)
    except Exception:  # noqa: BLE001 - 重算不出就是没有可核验的独立身份
        return None
    same = rebuilt.scope_version == str(recorded.get("scope_version") or "")
    return A2ReviewIdentity(
        scope_kind="financial_a2",
        recomputed_scope_version=rebuilt.scope_version,
        recorded_scope_version=str(recorded.get("scope_version") or ""),
        recorded_bundle_id=str(recorded.get("bundle_id") or ""),
        sidecar_label=sidecar.label, sidecar_sha256=sidecar.sha256,
        verified=same,
        reason=("从当前 A2 产物重算出的内容版本与旧侧车逐字相同，"
                "可以只读单列该份 A2 审阅"
                if same else
                "从当前 A2 产物重算出的内容版本与旧侧车不同："
                "那份审阅绑的是**另一份** A2，本次不得显示为已审"),
    )


def load_display_binding(results_root: str | Path = _loader.DEFAULT_RESULTS_ROOT,
                         *, check_a2_review: bool = True) -> CitedDisplayBinding:
    """读回两个来源并逐字节核对；任何不符立刻抛 `DisplayBindingError`。"""
    root, company_dir, financial_dir = _source_dirs(results_root)
    company_snapshot = _loader._Snapshot(company_dir)
    financial_snapshot = _loader._Snapshot(financial_dir)

    hashes: dict[str, str] = {}
    hashes.update(_pin(company_snapshot, COMPANY_SOURCE))
    hashes.update(_pin(financial_snapshot, FINANCIAL_SOURCE))

    company = _load_company(company_snapshot)
    financial = _load_financial(financial_snapshot)

    a2_review = (_a2_review_identity(financial, results_root=root)
                 if check_a2_review else None)

    # 读前/读后逐文件重读：两次字节必须一致，否则页面显示的可能只是加载瞬间的样子。
    # 返回值只是快照自己的相对键，与上面按来源记的哈希表不是一套键，故不入账。
    company_snapshot.verify_unchanged()
    financial_snapshot.verify_unchanged()

    notes = (
        "这是**两个不同 run** 的已有产物并列展示；不是一次新生成、已审阅通过的双节合成报告。",
        "本组合**未**重新进行报告级审阅；旧 M930-4 侧车绑定的是 dual_v2_r1，"
        "其跨节与未用材料意见不适用于 ndc5 公司正文。",
        "原 PDF 区域只作来源回查，不授权正文数字，也不是合格结构化表。",
    )
    return CitedDisplayBinding(
        binding_version=BINDING_VERSION, company=company, financial=financial,
        documents=SOURCE_DOCUMENTS, artifact_hashes=hashes, a2_review=a2_review,
        notes=notes)


# ------------------------------------------------------- first-screen uploads


@dataclass(frozen=True)
class DocumentVerdict:
    document_id: str
    filename: str
    declared_sha256: str
    uploaded_sha256: str
    uploaded_name: str
    size_bytes: int
    matches: bool


@dataclass(frozen=True)
class DocumentVerification:
    verdicts: tuple[DocumentVerdict, ...]
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    accepted: bool
    message: str


def verify_source_documents(
        uploads: Sequence[tuple[str, bytes]],
        documents: Iterable[SourceDocument] = SOURCE_DOCUMENTS) -> DocumentVerification:
    """把用户选中的文件在**内存里**逐个算 SHA-256，与已保存案例的三份来源核对。

    不写盘、不建索引、不发请求。三份全部匹配才 `accepted`；有一份不符或缺席即拒绝进入。
    """
    wanted = tuple(documents)
    uploaded = tuple((str(name), bytes(data)) for name, data in uploads)
    #: 大小写只是十六进制的写法，不是第二份身份：产物自己写下的 `@sha256-` 前缀是**小写**，
    #: 页面展示的也是小写，而 `_sha256` 返回大写。这里统一压到小写再比，**不**放宽任何一位。
    by_hash = {_sha256(data).lower(): (name, data) for name, data in uploaded}
    verdicts: list[DocumentVerdict] = []
    for doc in wanted:
        hit = by_hash.get(doc.sha256.lower())
        verdicts.append(DocumentVerdict(
            document_id=doc.document_id, filename=doc.filename,
            declared_sha256=doc.sha256,
            uploaded_sha256=_sha256(hit[1]).lower() if hit else "",
            uploaded_name=hit[0] if hit else "",
            size_bytes=len(hit[1]) if hit else 0,
            matches=hit is not None))
    missing = tuple(d.document_id for d, v in zip(wanted, verdicts) if not v.matches)
    known = {d.sha256.lower() for d in wanted}
    unexpected = tuple(name for name, data in uploaded
                       if _sha256(data).lower() not in known)
    accepted = not missing
    if accepted:
        message = "材料与已保存案例一致，可查看运行回放"
    else:
        message = ("材料与已保存案例不一致，拒绝进入该案例："
                   + "、".join(missing)
                   + ("；另有未登记的额外文件 " + "、".join(unexpected) if unexpected
                      else ""))
    return DocumentVerification(verdicts=verdicts, missing=missing,
                                unexpected=unexpected, accepted=accepted,
                                message=message)


def manifest_document_hashes(company: CompanyDisplay) -> dict[str, str]:
    """从公司节 Writer 清单里取出「来源文档 ↔ 哈希前 12 位」的登记，用于交叉印证。

    产物的 `locator_ref.owner` 形如
    ``evidence_document:NDSD_2025_year@sha256-c15272977147dee7#block_span``。这是**产物
    自己**写下的来源身份，不是页面抄来的数字；第一屏核对时用它做第二重印证。
    """
    out: dict[str, str] = {}
    for material in company.manifest.get("materials") or ():
        document_id = material.get("document_id")
        locator = material.get("locator_ref") or {}
        owner = str(locator.get("owner") or "")
        marker = "@sha256-"
        if not document_id or marker not in owner:
            continue
        prefix = owner.split(marker, 1)[1].split("#", 1)[0]
        if len(prefix) >= 12:
            out.setdefault(str(document_id), prefix)
    return out


def document_role_summary(company: CompanyDisplay) -> tuple[dict[str, Any], ...]:
    """逐份来源 PDF 的登记读数：材料份数、来源角色、产物自述哈希前缀。"""
    counts: dict[str, int] = {}
    roles: dict[str, set[str]] = {}
    for material in company.manifest.get("materials") or ():
        document_id = str(material.get("document_id") or "")
        if not document_id:
            continue
        counts[document_id] = counts.get(document_id, 0) + 1
        role = material.get("source_role")
        if role:
            roles.setdefault(document_id, set()).add(str(role))
    declared = manifest_document_hashes(company)
    rows = []
    for doc in SOURCE_DOCUMENTS:
        rows.append({
            "document_id": doc.document_id,
            "filename": doc.filename,
            "role": doc.role,
            "materials": counts.get(doc.document_id, 0),
            "source_roles": "、".join(sorted(roles.get(doc.document_id, ()))) or "未登记",
            "manifest_hash_prefix": declared.get(doc.document_id, "未登记"),
            "sha256": doc.sha256,
        })
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the read-only M930-4/5 side-by-side display binding")
    parser.add_argument("--results-root", default=str(_loader.DEFAULT_RESULTS_ROOT))
    args = parser.parse_args(argv)
    binding = load_display_binding(args.results_root)
    company = binding.company
    print(json.dumps({
        "binding_version": binding.binding_version,
        "sources": [
            {"key": COMPANY_SOURCE.key, "run": COMPANY_SOURCE.run_relpath,
             "report_version": company.report_version["report_version"],
             "sentences": company.sentence_count,
             "fact_safety_hard_errors": len(company.fact_safety_sentence_ids),
             "column_coverage_pending": len(company.column_coverage_sentence_ids)},
            {"key": FINANCIAL_SOURCE.key, "run": FINANCIAL_SOURCE.run_relpath,
             "a2_fingerprint": binding.financial.fingerprint,
             "a2_items": len(binding.financial.items),
             "a2_readings": binding.financial.reading_count,
             "model_calls_issued": binding.financial.balance_structure["model_calls_issued"]},
        ],
        "source_documents": [d.document_id for d in binding.documents],
        "human_confirmed_regions": sum(
            r["confirmation_state"] == "human_confirmed"
            for r in company.source_regions),
        "source_region_count": len(company.source_regions),
        "verified_artifact_count": len(binding.artifact_hashes),
        "a2_review_identity": (None if binding.a2_review is None else {
            "verified": binding.a2_review.verified,
            "recomputed": binding.a2_review.recomputed_scope_version,
            "recorded": binding.a2_review.recorded_scope_version,
        }),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

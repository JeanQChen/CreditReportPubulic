"""Read-only loader for a persisted cited-writing demo run.

This is a presentation adapter, not a report builder or an Assurance decision.
It accepts only already-persisted artifacts, verifies their current wire identities
and cross-file references, and retains the exact bytes shown by the UI. Historical
wire versions that the current decoders cannot verify are rejected explicitly.

Self-check: ``python -m sections.cited_demo_loader --run-id <existing-run-id>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assurance import schema as assurance_schema
from sections import cited_financial_table as financial_table
from sections import cited_report
from sections import cited_review
from sections import cited_writer
from sections import sentence_check


DEFAULT_RESULTS_ROOT = Path("evaluation/results")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SECTIONS = ("company", "financial")
_SECTION_FILES = (
    "cited_report_version.json",
    "cited_input_manifest.json",
    "cited_prose.json",
    "sentence_checks.json",
    "review_issues.json",
    "cited_metric_tables.json",
    "source_table_display.json",
    "cited_preview.md",
    "demo_page.md",
)
_A2_FILE = "cited_balance_structure__fin_balance_structure.json"
_DISPLAY_IDENTITY_FIELDS = (
    "schema_version", "policy_version", "task_id", "section_id",
    "source_record_id", "registered_source_id", "registrations", "regions",
)


class CitedDemoLoadError(ValueError):
    """An untrusted or incomplete persisted run cannot be shown as a verified run."""


@dataclass(frozen=True)
class CitedDemoSection:
    section_id: str
    report_version: dict[str, Any]
    manifest: dict[str, Any]
    draft: dict[str, Any]
    checks: dict[str, Any]
    review: dict[str, Any]
    metric_tables: dict[str, Any]
    source_display: dict[str, Any]
    preview_markdown: str
    demo_markdown: str
    balance_structure: dict[str, Any] | None = None

    @property
    def hard_sentence_ids(self) -> tuple[str, ...]:
        return tuple(str(s["sentence_id"]) for s in self.checks["sentence_states"]
                     if s["verdict"] == "hard_error")


@dataclass(frozen=True)
class CitedDemoRun:
    run_id: str
    run_dir: Path
    ledger: dict[str, Any]
    sections: dict[str, CitedDemoSection]
    source_regions: tuple[dict[str, Any], ...]
    source_images: dict[str, bytes]
    file_hashes: dict[str, str]


class _Snapshot:
    """Read exact bytes, then re-read every file before returning a display model."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.content: dict[str, bytes] = {}
        self.paths: dict[str, Path] = {}

    def read(self, relative: str) -> bytes:
        rel = Path(relative)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
            raise CitedDemoLoadError(f"非法产物路径：{relative!r}")
        try:
            path = (self.run_dir / rel).resolve(strict=True)
            path.relative_to(self.run_dir)
        except (OSError, ValueError) as exc:
            raise CitedDemoLoadError(f"产物缺失或越界：{relative!r}") from exc
        if not path.is_file():
            raise CitedDemoLoadError(f"产物不是普通文件：{relative!r}")
        key = rel.as_posix()
        if key not in self.content:
            self.content[key] = path.read_bytes()
            self.paths[key] = path
        return self.content[key]

    def json(self, relative: str) -> dict[str, Any]:
        try:
            value = json.loads(self.read(relative).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CitedDemoLoadError(f"JSON 不可解码：{relative}") from exc
        if not isinstance(value, dict):
            raise CitedDemoLoadError(f"JSON 顶层不是对象：{relative}")
        return value

    def text(self, relative: str) -> str:
        try:
            return self.read(relative).decode("utf-8")
        except UnicodeError as exc:
            raise CitedDemoLoadError(f"文本不可解码：{relative}") from exc

    def verify_unchanged(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for relative, before in self.content.items():
            path = self.paths[relative]
            try:
                after = path.read_bytes()
            except OSError as exc:
                raise CitedDemoLoadError(f"加载期间产物消失：{relative}") from exc
            if before != after:
                raise CitedDemoLoadError(f"加载期间产物发生变化：{relative}")
            hashes[relative] = hashlib.sha256(before).hexdigest()
        return hashes


def _canonical_sha(body: dict[str, Any]) -> str:
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise CitedDemoLoadError(f"{label} 不一致：{actual!r} != {expected!r}")


def _review_from_wire(raw: dict[str, Any]) -> cited_review.CitedReviewOutcome:
    fields = set(cited_review.CitedReviewOutcome.__dataclass_fields__)
    derived = {"blocking_issue_ids", "semantic_counts"}
    if set(raw) != fields | derived:
        raise CitedDemoLoadError("review_issues.json 字段集不合约")
    values = {k: raw[k] for k in fields}
    values["issues"] = tuple(assurance_schema.ReviewIssue.from_dict(i)
                             for i in raw["issues"])
    for key in ("sentence_ids", "hard_error_sentence_ids",
                "excluded_uncited_sentence_ids", "hard_error_override_sentence_ids"):
        values[key] = tuple(raw[key])
    outcome = cited_review.CitedReviewOutcome(**values)
    _require_equal(outcome.to_dict(), raw, "审阅派生读数")
    return outcome


def _validate_display(snapshot: _Snapshot, section_id: str,
                      raw: dict[str, Any], report_version: str, *,
                      prefix: str | None = None) -> tuple[dict[str, Any], ...]:
    """校验一份原 PDF 展示集，并逐张读回渲染图核对 `render_sha256`。

    `prefix` 只决定**从哪个子目录**读渲染图，默认 `section_id`——双节 run 的
    `<section>/source_display/`。单节扁平 run（例如 `ndc5`）把 `source_display/` 放在 run 根，
    此时传 `prefix=""`。它**不**放宽任何一条身份校验：指纹、ID、并列正文版本、计数、
    区域唯一性、确认记录完整性与逐张渲染哈希全部照旧。
    """
    image_prefix = section_id if prefix is None else prefix
    try:
        body = {key: raw[key] for key in _DISPLAY_IDENTITY_FIELDS}
        fingerprint = _canonical_sha(body)
        _require_equal(raw["fingerprint"], fingerprint, "原 PDF 展示集指纹")
        _require_equal(raw["display_set_id"], "std_" + fingerprint[:24], "原 PDF 展示集 ID")
        _require_equal(raw["section_id"], section_id, "原 PDF 展示集章节")
        _require_equal(raw["paired_report_version"], report_version,
                       "原 PDF 展示集并列正文版本")
        regions = raw["regions"]
        if not isinstance(regions, list) or not regions:
            raise CitedDemoLoadError("原 PDF 展示集没有区域")
        _require_equal(raw["displayable_count"],
                       sum(r["display_state"] == "displayable" for r in regions),
                       "原 PDF 可展示计数")
        _require_equal(raw["defect_count"], sum(len(r["defects"]) for r in regions),
                       "原 PDF 缺陷计数")
        _require_equal(raw["human_confirmed_count"],
                       sum(r["confirmation_state"] == "human_confirmed" for r in regions),
                       "原 PDF 人工确认计数")
        keys: set[str] = set()
        for item in regions:
            region = item["region"]
            key = str(region["region_key"])
            if key in keys:
                raise CitedDemoLoadError(f"原 PDF 区域键重复：{key}")
            keys.add(key)
            if item["display_state"] != "displayable" or not item["file_hash_verified"]:
                raise CitedDemoLoadError(f"原 PDF 区域不可忠实展示：{key}")
            if item["confirmation_state"] == "human_confirmed" and not (
                    item["confirmer"] and item["confirmed_at"]
                    and item["confirmation_conclusion"]):
                raise CitedDemoLoadError(f"原 PDF 区域人工确认记录不完整：{key}")
            rel = str(item["render_relpath"])
            if not rel or Path(rel).name != rel or Path(rel).suffix.lower() != ".png":
                raise CitedDemoLoadError(f"原 PDF 区域缺 PNG：{key}")
            data = snapshot.read("/".join(p for p in (image_prefix, "source_display", rel)
                                          if p))
            _require_equal(hashlib.sha256(data).hexdigest(), item["render_sha256"],
                           f"原 PDF 区域 {key} 渲染哈希")
        return tuple(regions)
    except (KeyError, TypeError, IndexError) as exc:
        raise CitedDemoLoadError("原 PDF 展示集结构不完整") from exc


def _load_section(snapshot: _Snapshot, section_id: str) -> CitedDemoSection:
    prefix = section_id + "/"
    raw = {name: snapshot.json(prefix + name) if name.endswith(".json")
           else snapshot.text(prefix + name) for name in _SECTION_FILES}
    try:
        #: 从盘上读 `cited_report_version.json` **一律**走 `from_persisted_dict`（按文件自己
        #: 声明的发布期分派，见 `cited_report.CitedReportVersion.from_persisted_dict`）。直接
        #: `from_dict` 只适用于「刚由 `build_cited_report_version` 造出来、必然是当前版」的对象：
        #: 一次口径升版（`crpp-4 → crpp-6`）会让它在**每个**已落盘的 run 上抛错，把「读得出
        #: 旧产物」变成「旧产物一律打不开」。两条路径共用同一份 `_validate_body`，所以这里
        #: 换的只是**版本串相等**那一道闸，跨轴不变式与 `record_id` 复算一条不少。
        version = cited_report.CitedReportVersion.from_persisted_dict(
            raw["cited_report_version.json"])
        manifest = cited_writer.CitedWriterInputManifest.from_dict(raw["cited_input_manifest.json"])
        draft = cited_writer.CitedProseDraft.from_dict(raw["cited_prose.json"]["draft"])
        checks = sentence_check.SentenceCheckReport.from_dict(raw["sentence_checks.json"])
        review = _review_from_wire(raw["review_issues.json"])
        tables = financial_table.CitedMetricTableOutcome.from_dict(raw["cited_metric_tables.json"])
        _require_equal(version.section_id, section_id, "正文章节")
        _require_equal(manifest.section_id, section_id, "Writer 清单章节")
        _require_equal(draft.section_id, section_id, "草稿章节")
        _require_equal(tables.section_id, section_id, "指标表章节")
        _require_equal(version.input_manifest_id, manifest.manifest_id, "Writer 清单 ID")
        _require_equal(version.draft_id, draft.draft_id, "草稿 ID")
        _require_equal(version.check_report_id, checks.report_id, "逐句核对 ID")
        _require_equal(version.review_bundle_id, review.bundle_id, "独立审阅输入 ID")
        _require_equal(review.report_version, version.report_version, "审阅版本")
        _require_equal(review.draft_id, draft.draft_id, "审阅草稿 ID")
        _require_equal(review.input_manifest_id, manifest.manifest_id, "审阅清单 ID")
        #: 两条**独立**的 wire 表示：审阅侧车记的 id 集，与逐句核对文件里 `sentence_states` 的
        #: 硬错集。两者都按**两族并集**记（`scp-10` 起 `blocked_sentence_ids` 只管事实安全族，
        #: 但「机械层一共标了哪几句」这条口径没变）——因此这里对旧产物一字不变。
        _hard_from_raw = tuple(
            s["sentence_id"] for s in raw["sentence_checks.json"]["sentence_states"]
            if s["verdict"] == "hard_error")
        _require_equal(tuple(review.hard_error_sentence_ids), _hard_from_raw,
                       "审阅记录的机械硬错误集")
        _require_equal(tuple(checks.hard_error_sentence_ids), _hard_from_raw,
                       "逐句核对文件的机械硬错误集")
        table_ids, table_fingerprint = cited_report._metric_table_identity(
            tables.tables, manifest=manifest, section_id=section_id)
        _require_equal(tuple(version.metric_table_ids), table_ids, "指标表 ID 集合")
        _require_equal(version.metric_tables_fingerprint, table_fingerprint,
                       "指标表集合指纹")
        _require_equal(len(draft.sentences()), version.sentence_count,
                       "章节句数")
        #: 这里问的是**放行**口径（「系统按哪几句阻断」），不是「一共标了几句」：`scp-10` 起它
        #: 只收事实安全族。用解码后的报告读（它按**文件自己声明的** `policy_version` 取口径），
        #: 于是旧产物（并集口径）重算值仍与盘上值逐字相等。
        _require_equal(version.blocking_sentence_count, checks.blocked_sentence_count,
                       "章节机械阻断句数")
        _require_equal(version.publishability, "not_publishable", "演示不可发布边界")
        if version.report_version not in raw["cited_preview.md"]:
            raise CitedDemoLoadError("预览文本未标明本节 report_version")
        _validate_display(snapshot, section_id, raw["source_table_display.json"],
                          version.report_version)
        balance: dict[str, Any] | None = None
        if section_id == "financial":
            balance = snapshot.json(prefix + _A2_FILE)
            _require_equal(balance["fingerprint"],
                           _canonical_sha({k: v for k, v in balance.items()
                                           if k != "fingerprint"}), "财务 A2 指纹")
            _require_equal(balance["section_id"], section_id, "财务 A2 章节")
            _require_equal(balance["producer_kind"], "deterministic_presentation",
                           "财务 A2 产出者")
            _require_equal(balance["model_calls_issued"], 0, "财务 A2 模型调用数")
    except CitedDemoLoadError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError,
            cited_report.CitedReportError, cited_writer.CitedWriterError,
            sentence_check.SentenceCheckError, financial_table.CitedMetricTableError,
            cited_review.CitedReviewError,
            assurance_schema.AssuranceSchemaError) as exc:
        raise CitedDemoLoadError(
            f"{section_id} 当前版本产物身份或结构校验失败；旧版不隐式兼容：{exc}") from exc
    return CitedDemoSection(
        section_id=section_id, report_version=raw["cited_report_version.json"],
        manifest=raw["cited_input_manifest.json"], draft=raw["cited_prose.json"]["draft"],
        checks=raw["sentence_checks.json"], review=raw["review_issues.json"],
        metric_tables=raw["cited_metric_tables.json"],
        source_display=raw["source_table_display.json"],
        preview_markdown=raw["cited_preview.md"], demo_markdown=raw["demo_page.md"],
        balance_structure=balance)


def _safe_run_dir(results_root: str | Path, run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise CitedDemoLoadError(f"非法 run_id：{run_id!r}")
    try:
        root = Path(results_root).resolve(strict=True)
        target = (root / run_id).resolve(strict=True)
        target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CitedDemoLoadError(f"run 不存在或越界：{run_id!r}") from exc
    if not target.is_dir():
        raise CitedDemoLoadError(f"run 不是目录：{run_id!r}")
    return target


def list_cited_runs(results_root: str | Path = DEFAULT_RESULTS_ROOT) -> tuple[str, ...]:
    """Discover candidates only; each candidate is verified when loaded."""
    root = Path(results_root).resolve(strict=True)
    return tuple(sorted(p.name for p in root.iterdir()
                        if p.is_dir() and _RUN_ID.fullmatch(p.name)
                        and (p / "cited_call_ledger.json").is_file()
                        and (p / "company/cited_report_version.json").is_file()
                        and (p / "financial/cited_report_version.json").is_file()))


def default_cited_run(results_root: str | Path = DEFAULT_RESULTS_ROOT,
                      candidates: tuple[str, ...] | None = None) -> str | None:
    """Choose the newest strictly loadable real run, or a loadable offline fallback.

    Discovery alone proves only that a few files exist.  In particular, an old
    wire must never become the demo default just because its name sorts last.
    """
    root = Path(results_root).resolve(strict=True)
    names = candidates if candidates is not None else list_cited_runs(root)
    ordered = sorted(names, key=lambda name: (root / name).stat().st_mtime_ns,
                     reverse=True)
    offline_fallback: str | None = None
    for name in ordered:
        try:
            run = load_cited_run(name, results_root=root)
        except (CitedDemoLoadError, OSError, ValueError):
            continue
        if run.ledger["mode"] == "real":
            return name
        if offline_fallback is None:
            offline_fallback = name
    return offline_fallback


def load_cited_run(run_id: str, *, results_root: str | Path = DEFAULT_RESULTS_ROOT
                   ) -> CitedDemoRun:
    """Load an existing dual-section run without writes, network or model calls."""
    run_dir = _safe_run_dir(results_root, run_id)
    snapshot = _Snapshot(run_dir)
    ledger = snapshot.json("cited_call_ledger.json")
    snapshot.text("demo_page.md")
    if ledger.get("run_outcome") != "completed" or ledger.get("mode") not in ("real", "offline"):
        raise CitedDemoLoadError("仅加载已完成且明确标识 real/offline 的 run")
    sections = {sid: _load_section(snapshot, sid) for sid in _SECTIONS}
    left = sections[_SECTIONS[0]].source_display["regions"]
    right = sections[_SECTIONS[1]].source_display["regions"]
    # The two sections carry independent display-set identities because each is
    # paired to its own prose version.  Before showing only one copy, compare
    # every per-region field, including display defects and human-confirmation
    # state/person/time/conclusion; matching image hashes alone are insufficient.
    if left != right:
        raise CitedDemoLoadError("两节的原 PDF 区域或确认状态不同，不得合并计数")
    images = {str(r["region"]["region_key"]): snapshot.read(
        f"{_SECTIONS[0]}/source_display/{r['render_relpath']}") for r in left}
    hashes = snapshot.verify_unchanged()
    return CitedDemoRun(run_id=run_id, run_dir=run_dir, ledger=ledger,
                        sections=sections, source_regions=tuple(left),
                        source_images=images, file_hashes=hashes)


ASSURANCE_SIDECAR_NAME = "assurance_diagnostic.json"
ASSURANCE_DIR_ENV = "CITED_DEMO_ASSURANCE_DIR"
_SECTIONS_SET = frozenset(_SECTIONS)


@dataclass(frozen=True)
class AssuranceSidecar:
    """一份**已持久化**的 M930-4 读数，以及"它是从哪读来的"这条身份。

    `sha256` 是 sidecar 文件自身的字节哈希：页面显示的读数与磁盘上的那份文件由此对上。
    它**不是** `AssuranceResult` 的替代，也不携带任何放行含义——它是一份只读诊断的读回。
    """

    reading: dict[str, Any]
    directory: Path
    label: str
    sha256: str
    source_run: str
    superseded: tuple[str, ...] = ()

    @property
    def policy_version(self) -> str:
        return str(self.reading.get("policy_version") or "")

    def describe(self) -> str:
        text = (f"读取自 `{self.label}` · sha256 `{self.sha256[:16]}…` · "
                f"策略 `{self.policy_version or '未记录'}`")
        if self.superseded:
            text += (f"；同一 run 上另有 {len(self.superseded)} 份更早策略的读数已被取代，"
                     "未参与本页显示")
        return text


def _same_path(left: Any, right: Any) -> bool:
    """两个路径串是否指同一个位置（Windows 大小写/分隔符不敏感）。"""
    import os
    try:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
            os.path.abspath(str(right)))
    except (OSError, ValueError):  # pragma: no cover - 畸形路径
        return False


def _verify_sidecar(reading: dict[str, Any], run: "CitedDemoRun", source: Path) -> None:
    """sidecar 必须**绑在同一份源 run 上**：账本哈希、逐节产物哈希、正文版本逐条对账。

    只核 `source_run` 这个字符串是不够的——源 run 被改过之后，那份读数描述的是**另一份**
    内容。这里核的是字节：账本一份，每节产物的每一份。
    """
    if not isinstance(reading, dict):
        raise CitedDemoLoadError(f"{source} 不是一个 JSON 对象")
    _require_equal(reading.get("source_run", ""), str(run.run_dir),
                   "M930-4 侧车来源 run")
    _require_equal(reading.get("source_call_ledger_sha256"),
                   run.file_hashes.get("cited_call_ledger.json"), "M930-4 侧车调用账本哈希")
    _require_equal(reading.get("new_llm_calls"), 0,
                   "M930-4 侧车自行发起的调用数（只读读数必须是 0）")
    if reading.get("system_release_eligible") is not False:
        raise CitedDemoLoadError("M930-4 侧车不得声称系统放行")
    seen: set[str] = set()
    for section in reading.get("section_assessments") or ():
        sid = str(section.get("section_id") or "")
        if sid not in _SECTIONS_SET or sid in seen:
            raise CitedDemoLoadError(f"M930-4 侧车的章节集合不合约：{sid!r}")
        seen.add(sid)
        mine = run.sections[sid]
        _require_equal(section.get("report_version"),
                       mine.report_version["report_version"],
                       f"{sid} 侧车与页面正文版本")
        if section.get("assurance_result", {}).get("system_release_eligible") is not False:
            raise CitedDemoLoadError(f"{sid} 侧车不得声称系统放行")
        for name, digest in (section.get("source_artifact_sha256") or {}).items():
            _require_equal(run.file_hashes.get(f"{sid}/{name}"), digest,
                           f"{sid}/{name} 在侧车与页面快照之间")
    if seen != _SECTIONS_SET:
        raise CitedDemoLoadError("M930-4 侧车缺少章节")


def _scan_sidecars(root: Path, run: "CitedDemoRun") -> list[tuple[Path, dict[str, Any]]]:
    """扫 `root/*/assurance_diagnostic.json`，返回 `source_run` 指本 run 的那些。"""
    hits: list[tuple[Path, dict[str, Any]]] = []
    try:
        children = sorted(root.resolve(strict=True).iterdir())
    except (OSError, ValueError):
        return hits
    for child in children:
        candidate = child / ASSURANCE_SIDECAR_NAME
        if not candidate.is_file():
            continue
        try:
            head = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue  # 读不动的文件不是候选；选中它再由 _verify 报错
        if isinstance(head, dict) and _same_path(head.get("source_run", ""), run.run_dir):
            hits.append((candidate, head))
    return hits


def load_assurance_sidecar(run: "CitedDemoRun", *,
                           results_root: str | Path = DEFAULT_RESULTS_ROOT,
                           explicit_dir: str | Path | None = None,
                           environ: dict[str, str] | None = None) -> AssuranceSidecar:
    """读回**已持久化**的 M930-4 读数；找不到或核不过就抛（不现场重算）。

    查找顺序：`explicit_dir` → 环境变量 `CITED_DEMO_ASSURANCE_DIR` →
    在 `results_root/*/assurance_diagnostic.json` 里按 `source_run` 找。

    自动查找时，同一个源 run 上**允许多份**读数，但必须能按产物本身排定先后：
    声明当前控制器策略版本（`cited_controller.POLICY_VERSION`）的那一份是本次读数，
    更早的策略版本是同一 run 上被取代的旧读数——它们会列在 `superseded` 里，不当误差；
    若**同一策略版本**有两份，则抛错（页面显示哪一份不能由查找顺序决定），
    若一份都不声明当前版本，也抛错并列出候选，要求显式指定。
    """
    import os

    root = Path(results_root)
    chosen: Path
    superseded: tuple[str, ...] = ()
    if explicit_dir is not None or (environ if environ is not None
                                    else os.environ).get(ASSURANCE_DIR_ENV, "").strip():
        named = explicit_dir if explicit_dir is not None else (
            environ if environ is not None else os.environ)[ASSURANCE_DIR_ENV]
        named_dir = Path(str(named).strip())
        chosen = (named_dir if named_dir.suffix == ".json"
                  else named_dir / ASSURANCE_SIDECAR_NAME)
    else:
        hits = _scan_sidecars(root, run)
        if not hits:
            raise CitedDemoLoadError(
                f"未加载到已持久化的 M930-4 读数（在 {root} 下没有 {ASSURANCE_SIDECAR_NAME} "
                f"指向本 run，也没给 CITED_DEMO_ASSURANCE_DIR）")
        from assurance import cited_controller as _cc  # 只读一个策略常量

        current = _cc.POLICY_VERSION
        same_policy = [p for p, body in hits if str(body.get("policy_version") or "") == current]
        if len(same_policy) > 1:
            raise CitedDemoLoadError(
                f"同一个源 run 有 {len(same_policy)} 份 M930-4 读数都声明策略版本 "
                f"{current}：" + "、".join(str(p) for p in same_policy)
                + "；两份当前读数不能靠查找顺序挑，请显式指定要显示哪一份")
        if same_policy:
            chosen = same_policy[0]
            superseded = tuple(str(p) for p, _ in hits if p != chosen)
        else:
            raise CitedDemoLoadError(
                f"找到 {len(hits)} 份 M930-4 读数指向本 run，但没有一份声明当前策略版本 "
                f"{current}（各自声明：" + "、".join(
                    f"{str(b.get('policy_version') or '未记录')}@{p}" for p, b in hits)
                + "）；请显式指定要显示哪一份")

    try:
        raw = chosen.read_bytes()
        reading = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CitedDemoLoadError(f"无法读取 M930-4 读数 {chosen}：{exc}") from exc
    _verify_sidecar(reading, run, chosen)
    try:
        label = str(chosen.parent.resolve().relative_to(Path(root).resolve()))
    except (OSError, ValueError):
        label = str(chosen.parent)
    return AssuranceSidecar(reading=reading, directory=chosen.parent, label=label,
                            sha256=hashlib.sha256(raw).hexdigest(),
                            source_run=str(reading.get("source_run") or ""),
                            superseded=superseded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and summarize a persisted cited demo run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    args = parser.parse_args(argv)
    run = load_cited_run(args.run_id, results_root=args.results_root)
    print(json.dumps({
        "run_id": run.run_id, "mode": run.ledger["mode"],
        "sections": {sid: {"report_version": s.report_version["report_version"],
                           "sentence_count": s.report_version["sentence_count"],
                           "hard_error_count": len(s.hard_sentence_ids),
                           "publishability": s.report_version["publishability"]}
                     for sid, s in run.sections.items()},
        "source_region_count": len(run.source_regions),
        "human_confirmed_count": sum(r["confirmation_state"] == "human_confirmed"
                                     for r in run.source_regions),
        "verified_file_count": len(run.file_hashes),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

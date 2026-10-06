"""Read-only M930-4 deterministic check of a persisted cited demo run.

This is deliberately a *bounded* controller.  It reuses the real M930-3
sentence review as historical, prose-only evidence; it does not claim that
financial A2, unselected materials, or cross-section meaning were reviewed.
There is no LLM, research, human-acceptance, or formal-closure write path.

`m4cc-2` adds the two things the earlier reading only *named* as missing:

* **prepared report-level review inputs** (`assurance.report_review`): the three
  reader-visible blocks the A1 sentence review never spoke about — financial A2,
  cross-section reading, and selectivity over unused manifest members.  Each is
  isolated, read-only, content-addressed and version-bound.  They are *inputs*:
  every scope's state stays `review_not_run` and `new_llm_calls` stays 0.
* **seven separate states** (`report_states`): mechanical errors, review
  coverage, review opinions, preview, system release, human acceptance and
  formal phase closure are recorded side by side and none is derived from
  another.  `formal_phase_closure` is explicitly *not* computed here.

`m4cc-3` turns the *prepared inputs* into *aggregated opinions* without moving any
of the seven states:

* `assess_persisted_run(..., report_review_runs=...)` accepts already-persisted
  `ReportReviewRun` objects and binds each one to a scope by
  `scope_id` **and** `scope_version` **and** `bundle_id`.  A run formed on a
  different version of the same block is recorded under
  `stale_report_review_runs` and its opinions are **not** aggregated: once the
  prose or the input changes, an old reading stops being a reading of *this*
  content.  A run that binds to no scope at all is rejected outright.
* review coverage per scope is now factual rather than aspirational —
  `reviewed_by_real_call` / `reviewed_by_offline_echo` / `review_started_failed` /
  `review_skipped_reviewability_blocked` / `input_prepared_review_not_run` — and
  `review_opinions` carries the deterministic counts, the producer identity and
  the per-issue ids.
* opinions are **inert with respect to every other state**:
  `review_opinion_effect` states this as explicit booleans, and
  `mechanical` / `preview` / `system_release` / `human_acceptance` /
  `formal_phase_closure` are computed exactly as before.  `new_llm_calls` stays
  0 (this module still never calls a model); the calls that *were* made are
  reported separately as `report_review_calls_recorded`, so "this controller
  issued none" and "none were issued anywhere" cannot be confused.

`m4cc-4` fixes three things the `m4cc-3` reading got wrong about *how* those
opinions were produced, without moving any of the seven states:

* **the per-scope call cap is derived, not declared.**  The input face no longer
  carries a `call_ceiling` constant (the old `1` contradicted the real batching
  plan).  It declares the frozen `batching_rules` instead, and the cap is the
  batch count `plan_report_review_batches` actually produces on this input.  A
  cap that cannot be derived (a single unit exceeding capacity) is reported as
  `derivation_error`, not smoothed into a plausible number.
* **three unit counts that are not each other.**  `requested_unit_count` (put
  into a request, failed batches included), `covered_unit_count` (entered a
  batch whose reply parsed) and `reported_unit_count` (the model actually raised
  an issue about) are recorded side by side with an explicit
  `unit_count_meaning`.  The program can derive the first two from the request;
  it therefore **must not** be read as "the model checked every material".
  `supported_count` carries its own `supported_count_meaning`: under `irv-2` the
  review only reports findings, so it is normally 0 and an empty `issues` array
  means "this call reported nothing conclusion-changing", never correctness,
  release or acceptance.
* **a run from another report is rejected, not labelled stale.**  Staleness is
  for a run of *this* report on *another* version of the block; a run whose
  `report_id` is a different report is fail-closed at binding time.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from assurance import report_review as RR
from assurance import report_reviewer as RX
from assurance import schema as AS
from sections import cited_financial_table as CFT
from sections import cited_report as CRP
from sections import cited_writer as CW
from sections import sentence_check as SC


#: 聚合策略版本。**本批（`crpp-5` 口径修正）刻意不动它**，理由必须写明，不得含糊成
#: 「忘了升版」：
#:
#: * 本批在本模块只加了一条 **fail-closed 交叉核对**（:func:`_require_gap_count`：有
#:   `cited_gap_bins.json` 时逐条与草稿缺口对账，并按冻结 Contract 的**版本与内容指纹**
#:   验明这份分桶到底是在哪一份 Contract 上做的）。它对**每一份已经成立的**读数产出**逐字
#:   不变的评估字典——多出来的只有「不成立时当场拒绝」这一支。
#: * `POLICY_VERSION` 的读者面作用是**标识哪一份侧车是本次读数**（`scripts/CITED_DEMO_LAUNCH.md`
#:   的查找规则按它筛选）。**升版不要求任何真实模型调用**：`scripts/run_m930_4_assurance.py`
#:   是**离线确定性**侧车生成器（不联网、不发请求、不调模型），它从**已经落盘**的源 run 与
#:   报告级审阅记录重出一份，因此 `m4cc-*` 升版只意味着「在新目录里重跑一次离线生成」，
#:   不意味着「再买一次真实 run」。本批不升版（见下），也**不得**再写「升版要一次真实
#:   M930-4 运行」这句话——那是错的。
#: * 仍未做的两项（**都不阻塞本批**，逐项写明代价）：
#:   （a）缺口分桶（`cited_gap_bins.json` 的桶读数）**仍未**进本控制器的评估字典；
#:   （b）逐句机械结论的**分族计数**（`CRP.classify_check_report`：`fact_safety` /
#:   `column_coverage` / `criteria_diagnostic`）**仍未**进——本节字典里的 `mechanical`
#:   只有 `blocked_sentence_count` 与 `blocked_sentence_ids`，把「来源撑不住这句话」与
#:   「引用未登记本栏」加在同一个数字里。
#:   两项都是**新增键 ⇒ 形状变化 ⇒ 必须升 `m4cc-*`**。升版本身只是在新目录里重跑一次离线
#:   生成器（见上一条），代价**不是**一次真实调用；真正的代价是：盘上那份 `m4cc-4` 侧车会
#:   变成「已被取代」，演示页按 `POLICY_VERSION` 筛选时会暂时读不到当前读数，直到新的离线
#:   侧车落盘。分族读数本身**已经可以现算**（它只依赖 `sentence_checks.json` 与封闭的轴词表
#:   见 `sections/sentence_check.CHECK_FAMILY_BY_KIND`），读回里照此印。
POLICY_VERSION = "m4cc-4"
SECTIONS = ("company", "financial")
_A2_FILE = "cited_balance_structure__fin_balance_structure.json"


class CitedControllerError(ValueError):
    """A persisted input cannot be trusted for this deterministic assessment."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CitedControllerError(message)


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CitedControllerError(f"Cannot read {path}: {exc}") from exc
    _require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _frozen_contract_identity() -> tuple[str, str]:
    """当前盘上冻结 Contract 的 `(contract_version, 内容指纹)`——**写读两侧同一份实现**。

    直接复用 :func:`sections.cited_report.frozen_contract_identity`（写侧把这两个值记进
    `cited_gap_bins.json`，读侧拿它比对），不在本模块另写一遍加载与指纹算法：两处各写一份，
    「写侧记的指纹」与「读侧算的指纹」迟早会分叉到两套口径上。

    它离线、确定性、只读（不联网、不调模型、不写盘、不碰数据库）。加载/校验失败时把
    `CitedReportError` 翻译成本控制器自己的 typed 错——那时**没有**可比的 Contract 身份，
    宁可拒绝也不拿占位值去比。
    """
    try:
        return CRP.frozen_contract_identity()
    except CRP.CitedReportError as exc:
        raise CitedControllerError(f"frozen Contract identity unavailable: {exc}") from exc


def _require_gap_count(section_dir: Path, *, section_id: str, draft: CW.CitedProseDraft,
                       version: CRP.CitedReportVersion, hashes: dict[str, str]) -> None:
    """`required_gap_count` 与草稿缺口对账（两种纪年，**都不放宽**），并验明分桶的**依据**。

    * **有** `cited_gap_bins.json`（`crpp-5` 起）：逐条核对 —— 桶行必须与草稿的缺口
      **一一对应**（同一个 id 集合、无重复、条数相等），每行的 `gap_bin` 必须落在封闭词表里，
      且声明里的 `required_gap_count` 必须**等于从这些行重新数出来的**必需条数。自报的数与
      自报的行不一致时**当场拒绝**，不挑一个信。
    * **没有**（`crpp-4` 及更早的产物）：那时 `required_gap_count` 的口径就是「草稿缺口的
      条数」，照旧逐字核对。这不是「放行旧产物」——旧产物在那条口径下本来就该**恰好相等**。

    **它不重算分桶**（那要重新对冻结 Contract 逐栏判定，是写侧的活），但它**必须**验明这份
    分桶自称站在哪一份 Contract 上——否则「行与数自洽」只能证明**它跟自己一致**，不能证明
    它跟**冻结的那份 Contract** 一致。三条绑定：

    1. **版本与政策**：旁挂声明的 `policy_version` 必须**逐字等于**被读的
       `cited_report_version.json` 自己声明的那个（分桶政策与报告版本政策同源）；
    2. **封闭词表**：旁挂声明的 `bins` / `blocking_bins` / `not_applicable_policies`
       必须逐字等于**当前**代码里的封闭集合。词表变过就意味着旁挂是按**另一套**桶集分的，
       拿今天的闭合集合去读它会把「桶集不同」读成「桶相同」；
    3. **冻结 Contract 身份**：旁挂声明的 `contract_version` 与 `contract_fingerprint`
       必须等于**当前盘上冻结 Contract** 的（`:func:`_frozen_contract_identity`，
       经 `contracts.loader_v2` + `contracts.validator_v2` 加载校验后取内容指纹）。
       Contract 内容一动，`missing_policies` / `display_tier` / `blocking_policy` 就可能跟着动，
       于是**同一条缺口的分桶结论就可能不同**——这时旧旁挂的桶读数**不得**被当成当前 Contract
       下的结论。版本/指纹缺失（`crpp-5` 期写成的旁挂没有这两个字段）时**按缺省拒绝**：
       「没声明」不等于「声明一致」。

    三条都过了，本函数才说「这份分桶的内部一致性与**依据身份**都成立」；它**仍然不说**
    「分桶结论正确」——那是写侧在冻结 Contract 上判的，逐条依据（`policy_basis` + Contract
    栏 id + 政策串）摆在旁挂里供人复核。
    """
    path = section_dir / "cited_gap_bins.json"
    if not path.is_file():
        _require(version.required_gap_count == len(draft.gaps),
                 f"{section_id}: gap count mismatch")
        return
    rows, digest = _read_json(path)
    hashes["cited_gap_bins.json"] = digest
    gaps = tuple(draft.gaps)
    _require(rows.get("report_version") == version.report_version
             and rows.get("draft_id") == draft.draft_id,
             f"{section_id}: gap bins do not bind this draft/report version")
    # (1) 分桶政策必须与被读报告版本自己声明的那一期**逐字相同**。
    _require(rows.get("policy_version") == version.policy_version,
             f"{section_id}: gap bins were binned under a different policy than the "
             "report version they are attached to")
    # (2) 封闭词表必须逐字等于当前代码里的集合。
    _require(rows.get("bins") == list(CRP.CITED_GAP_BINS)
             and rows.get("blocking_bins") == list(CRP.CITED_GAP_BLOCKING_BINS)
             and rows.get("not_applicable_policies")
             == list(CRP.CITED_GAP_NOT_APPLICABLE_POLICIES),
             f"{section_id}: gap bin vocabulary differs from the current closed sets")
    # (3) 冻结 Contract 身份。
    frozen_version, frozen_fingerprint = _frozen_contract_identity()
    _require(rows.get("contract_version") == frozen_version
             and rows.get("contract_fingerprint") == frozen_fingerprint,
             f"{section_id}: gap bins are not bound to the frozen Contract "
             f"(declared {rows.get('contract_version')!r}/"
             f"{str(rows.get('contract_fingerprint') or '')[:16]!r}, frozen "
             f"{frozen_version!r}/{frozen_fingerprint[:16]!r})")
    listed = rows.get("gaps")
    _require(isinstance(listed, list) and len(listed) == len(gaps),
             f"{section_id}: gap bins do not cover each draft gap")
    expected_ids = [str(g.gap_id) for g in gaps]
    try:
        bins = tuple(CRP.CitedGapBin(**row) for row in listed)
    except (TypeError, CRP.CitedReportError) as exc:
        raise CitedControllerError(f"{section_id}: gap bin row is not decodable: {exc}") from exc
    observed_ids = [b.gap_id for b in bins]
    _require(sorted(observed_ids) == sorted(expected_ids)
             and len(set(observed_ids)) == len(observed_ids),
             f"{section_id}: gap bins are missing, duplicated, or extra")
    recomputed = CRP.required_gap_count_from_bins(bins)
    _require(rows.get("required_gap_count") == version.required_gap_count == recomputed,
             f"{section_id}: required gap count disagrees with the persisted bins")


def _decode_report_version(raw: dict[str, Any]) -> "CRP.CitedReportVersion":
    """解码一份**已持久化**的 `cited_report_version.json`（`m4cc-4`，事后追补的兼容入口）。

    本控制器的立场是「只判**已持久化**的产物」。它读的可能是**上一期预览政策**下写成的记录
    ——那不是缺陷，那是历史的形状。因此这里按记录**自己声明的**发布期分派：

    * 两个版本串都是**当前**常量 ⇒ :meth:`CitedReportVersion.from_dict`（写侧同一道闸）；
    * 落在 :data:`CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS` ⇒
      :meth:`CitedReportVersion.from_legacy_dict`（同一份 `_validate_body`，只免掉版本串相等）；
    * 其余 ⇒ 走 `from_dict`，让它抛**带 typed 原因**的错（不静默降级、不猜一期）。

    这条入口**不**放宽任何跨轴不变式，也**不**改历史身份：解出来的对象其
    `schema_version` / `policy_version` 仍是文件里写的那个。
    """
    pair = (str(raw.get("schema_version") or ""), str(raw.get("policy_version") or ""))
    current = (CRP.CITED_REPORT_VERSION_SCHEMA_VERSION, CRP.CITED_REPORT_POLICY_VERSION)
    if pair != current and pair in CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS:
        return CRP.CitedReportVersion.from_legacy_dict(raw)
    return CRP.CitedReportVersion.from_dict(raw)


def _typed_section(section_dir: Path, section_id: str) -> dict[str, Any]:
    names = ("cited_input_manifest.json", "cited_prose.json",
             "sentence_checks.json", "review_issues.json",
             "cited_report_version.json", "cited_metric_tables.json",
             "source_table_display.json")
    loaded = {name: _read_json(section_dir / name) for name in names}
    objects = {name: data for name, (data, _) in loaded.items()}
    hashes = {name: digest for name, (_, digest) in loaded.items()}
    try:
        preview_bytes = (section_dir / "cited_preview.md").read_bytes()
    except OSError as exc:
        raise CitedControllerError(
            f"{section_id}: cited preview is not readable: {exc}") from exc
    _require(bool(preview_bytes), f"{section_id}: cited preview is empty")
    hashes["cited_preview.md"] = hashlib.sha256(preview_bytes).hexdigest()
    try:
        preview_text = preview_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise CitedControllerError(
            f"{section_id}: cited preview is not readable text: {exc}") from exc
    balance: dict[str, Any] | None = None
    if section_id == "financial":
        balance, balance_digest = _read_json(section_dir / _A2_FILE)
        hashes[_A2_FILE] = balance_digest
    try:
        manifest = CW.CitedWriterInputManifest.from_dict(objects["cited_input_manifest.json"])
        prose = objects["cited_prose.json"]
        draft = CW.CitedProseDraft.from_dict(prose["draft"])
        check = SC.SentenceCheckReport.from_dict(objects["sentence_checks.json"])
        version = _decode_report_version(objects["cited_report_version.json"])
        metric = CFT.CitedMetricTableOutcome.from_dict(objects["cited_metric_tables.json"])
        issue_rows = objects["review_issues.json"]
        issues = tuple(AS.ReviewIssue.from_dict(row) for row in issue_rows["issues"])
    except (KeyError, TypeError, ValueError, AttributeError,
            AS.AssuranceSchemaError, CRP.CitedReportError,
            CW.CitedWriterError, SC.SentenceCheckError,
            CFT.CitedMetricTableError) as exc:
        raise CitedControllerError(f"{section_id}: typed artifact decode failed: {exc}") from exc

    _require(manifest.section_id == draft.section_id == version.section_id == section_id,
             f"{section_id}: section identity mismatch")
    _require(draft.input_manifest_id == manifest.manifest_id == version.input_manifest_id,
             f"{section_id}: manifest identity mismatch")
    # 版本锚按**记录自己声明的那一期政策**复算：这里问的是「这个锚与这份草稿自洽吗」。
    # 「它是不是**陈旧**（写于另一个政策期）」是另一个问题，由 `_report_states` 那套
    # 陈旧/失配读数回答——一次政策升版会把盘上每一份旧 run 都判成「伪造」，而它们一个
    # 字节都没被改过，那是把「陈旧」写成「伪造」。
    _require(version.report_version == CRP.derive_report_version(
        draft=draft, manifest=manifest, policy_version=version.policy_version),
        f"{section_id}: stale or forged report_version")
    _require(version.draft_id == draft.draft_id and version.draft_fingerprint == draft.fingerprint(),
             f"{section_id}: draft identity mismatch")
    _require(check.draft_id == draft.draft_id and check.input_manifest_id == manifest.manifest_id,
             f"{section_id}: mechanical check does not bind the draft/manifest")
    _require(version.check_report_id == check.report_id
             and version.check_report_fingerprint == check.fingerprint()
             and objects["sentence_checks.json"].get("report_fingerprint") == check.fingerprint(),
             f"{section_id}: mechanical check fingerprint mismatch")
    # 「阻断句」在 `scp-10` 改了**聚合口径**（并集 → 事实安全族），而 `mechanical_verdict`
    # 也随之多了一档 `column_coverage_only`。因此这一条**按报告自己声明的** `policy_version`
    # 复算：`scp-10` 之前的报告仍取并集，盘上历史的 `mechanical_state` /
    # `blocking_sentence_count` 与重算值**逐字仍相等**（见 `sections/sentence_check.
    # FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS`）。这是「陈旧」与「伪造」的分界：一次口径升版
    # **不得**把每一份旧 run 判成伪造。
    _require(version.mechanical_state == check.mechanical_verdict
             and version.blocking_sentence_count == len(check.blocked_sentence_ids),
             f"{section_id}: mechanical verdict/count mismatch "
             f"(declared {version.mechanical_state!r}/"
             f"{version.blocking_sentence_count}, recomputed "
             f"{check.mechanical_verdict!r}/{len(check.blocked_sentence_ids)} "
             f"under sentence-check policy {check.policy_version!r})")
    _require(version.sentence_count == len(draft.sentence_ids()),
             f"{section_id}: sentence count mismatch")
    _require_gap_count(section_dir, section_id=section_id, draft=draft, version=version,
                       hashes=hashes)
    metric_ids, metric_fingerprint = CRP._metric_table_identity(
        metric.tables if section_id == "financial" else (),
        manifest=manifest, section_id=section_id)
    _require(version.metric_table_ids == metric_ids
             and version.metric_tables_fingerprint == metric_fingerprint,
             f"{section_id}: metric table identity mismatch")

    _require(issue_rows.get("report_version") == version.report_version
             and issue_rows.get("draft_id") == draft.draft_id
             and issue_rows.get("input_manifest_id") == manifest.manifest_id
             and issue_rows.get("bundle_id") == version.review_bundle_id,
             f"{section_id}: review identity mismatch")
    _require(issue_rows.get("review_producer_kind") == "independent_llm_review"
             and version.review_producer_kind == "independent_llm_review"
             and issue_rows.get("outcome") == "reviewed",
             f"{section_id}: persisted review is not a completed independent call")
    cited_sentences = {
        sentence.sentence_id: sentence
        for subsection in draft.subsections for paragraph in subsection.paragraphs
        for sentence in paragraph.sentences if sentence.citations
    }
    cited_sentence_ids = set(cited_sentences)
    manifest_citation_keys = {
        entry.citation_key for entry in (*manifest.materials, *manifest.facts)}
    reviewed_ids = tuple(str(v) for v in issue_rows.get("sentence_ids", ()))
    _require(set(reviewed_ids) == cited_sentence_ids and len(reviewed_ids) == len(cited_sentence_ids),
             f"{section_id}: review did not cover each cited prose sentence")
    issue_sentence_ids = [issue.sentence_id for issue in issues]
    _require(set(issue_sentence_ids) == cited_sentence_ids
             and len(issue_sentence_ids) == len(cited_sentence_ids),
             f"{section_id}: missing, duplicate, or extra sentence review result")
    _require(all(issue.report_version == version.report_version for issue in issues),
             f"{section_id}: review issue points to another report_version")
    _require(all(
        issue.citation_id in cited_sentences[issue.sentence_id].citations
        and issue.citation_id in manifest_citation_keys
        and issue.unit_ref.unit_kind == "citation"
        and issue.unit_ref.unit_id == issue.citation_id
        for issue in issues),
        f"{section_id}: review issue does not point to its sentence's manifest source")
    _require(set(issue_rows.get("blocking_issue_ids", ()))
             == {issue.issue_id for issue in issues if issue.blocking}
             and version.blocking_issue_count == sum(issue.blocking for issue in issues),
             f"{section_id}: blocking review count mismatch")
    # 审阅旁挂的 `hard_error_sentence_ids` 记的是**全部**硬错句（两族并集）——审阅是逐句评论，
    # 它看到的「机械层标了哪几句」不因阻断口径收窄而变。因此这一条比的是
    # :attr:`~sections.sentence_check.SentenceCheckReport.hard_error_sentence_ids`（并集），
    # **不是**阻断集：拿阻断集去比，`scp-10` 之后每一份含栏目覆盖失败的产物都会在这里假红。
    _require(set(issue_rows.get("hard_error_sentence_ids", ()))
             == set(check.hard_error_sentence_ids),
             f"{section_id}: review's mechanical-hard-error sidecar diverges")
    call = issue_rows.get("call")
    _require(isinstance(call, dict) and call.get("status") == "ok"
             and isinstance(call.get("response_hash"), str)
             and len(call["response_hash"]) == 64,
             f"{section_id}: prior review call evidence is incomplete")
    reviewer_record = AS.ReviewerRunRecord.create(
        report_version=version.report_version, bundle_id=version.review_bundle_id,
        prompt_version=str(call.get("prompt_version") or ""),
        model_policy_id=str(call.get("model_policy") or ""), outcome="reviewed",
        covered_unit_keys={issue.unit_ref.key for issue in issues},
        issue_ids=(issue.issue_id for issue in issues),
        response_fingerprint=call["response_hash"])
    return {"manifest": manifest, "draft": draft, "check": check, "version": version,
            "metric": metric, "issues": issues, "reviewer_record": reviewer_record,
            "review_call": call, "display": objects["source_table_display.json"],
            "preview_text": preview_text, "balance": balance,
            "artifact_sha256": hashes}


def _issue(*, gate_kind: str, field: str, declared: str, observed: str,
           reason: str, reviewability: bool = False) -> AS.HardGateIssue:
    return AS.HardGateIssue.create(
        gate_kind=gate_kind, reviewability_blocking=reviewability,
        release_blocking=True, field=field, declared=declared, observed=observed,
        reason=reason)


def _verify_display(display: dict[str, Any], *, section_id: str,
                    report_version: str) -> tuple[int, int]:
    """Verify the independent PDF-display identity and count its real regions.

    The display is deliberately *not* part of the prose report_version.  A
    changed display needs a new display identity and human confirmation, not
    a rewritten prose identity.
    """
    _require(display.get("section_id") == section_id
             and display.get("paired_report_version") == report_version,
             f"{section_id}: PDF display is paired to another section/report")
    identity = {key: value for key, value in display.items() if key not in {
        "display_set_id", "fingerprint", "paired_report_version", "defect_count",
        "displayable_count", "human_confirmed_count"}}
    fingerprint = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    _require(display.get("fingerprint") == fingerprint
             and display.get("display_set_id") == f"std_{fingerprint[:24]}",
             f"{section_id}: PDF display identity mismatch")
    regions = display.get("regions")
    _require(isinstance(regions, list) and bool(regions),
             f"{section_id}: PDF display region inventory missing")
    _require(all(isinstance(region, dict) for region in regions),
             f"{section_id}: malformed PDF display region")
    actual_confirmed = sum(
        region.get("confirmation_state") == "human_confirmed"
        for region in regions)
    actual_displayable = sum(region.get("display_state") == "displayable"
                             for region in regions)
    actual_defects = sum(len(region.get("defects", ())) for region in regions)
    _require(display.get("human_confirmed_count") == actual_confirmed
             and display.get("displayable_count") == actual_displayable
             and display.get("defect_count") == actual_defects,
             f"{section_id}: PDF display derived counts mismatch")
    return actual_confirmed, len(regions)


def assess_section(section_dir: Path, section_id: str,
                   review_attempt: dict[str, Any] | None
                   ) -> tuple[dict[str, Any], RR.SectionReviewFacts]:
    """Verify existing typed artifacts and aggregate *diagnostic* M930-4 status.

    Returns the serializable assessment **and** the decoded read-only facts the
    report-level review inputs are built from.  The second element is not part of
    the artifact: it never reaches the JSON.
    """
    _require(section_id in SECTIONS, f"Unknown section: {section_id}")
    data = _typed_section(Path(section_dir), section_id)
    check: SC.SentenceCheckReport = data["check"]
    version: CRP.CitedReportVersion = data["version"]
    reviewer: AS.ReviewerRunRecord = data["reviewer_record"]
    findings: list[AS.HardGateIssue] = []
    call = data["review_call"]
    ledger_ok = bool(
        isinstance(review_attempt, dict)
        and review_attempt.get("section_id") == section_id
        and review_attempt.get("category") == "cited_prose_review"
        and review_attempt.get("status") == "ok"
        and review_attempt.get("call_id") == call.get("call_id")
        and review_attempt.get("prompt_version") == call.get("prompt_version")
        and review_attempt.get("model") == call.get("model"))
    if not ledger_ok:
        findings.append(_issue(
            gate_kind="artifact_index_integrity", field="review_issues.call↔cited_call_ledger",
            declared="matching successful call_id, section, prompt and model",
            observed="missing or mismatched review attempt",
            reason="Review call provenance cannot be trusted; skip its opinions.",
            reviewability=True))
    if check.blocked_sentence_ids:
        findings.append(_issue(
            gate_kind="citation_authority_locator", field="sentence_checks.blocked_sentence_ids",
            declared="0 mechanically blocked sentences",
            observed=f"{len(check.blocked_sentence_ids)}: {', '.join(check.blocked_sentence_ids)}",
            reason="Existing real prose contains deterministic hard errors; supported review cannot override them."))
    if version.required_gap_count:
        findings.append(_issue(
            gate_kind="required_gap_retention", field="cited_report_version.required_gap_count",
            declared="0 unresolved required gaps for system release",
            observed=str(version.required_gap_count),
            reason="Gaps remain visible and block system release; review may inspect the existing preview."))
    confirmed, total_regions = _verify_display(
        data["display"], section_id=section_id,
        report_version=version.report_version)
    if confirmed != total_regions:
        findings.append(_issue(
            gate_kind="demo_scope_contract_boundary", field="source_table_display.human_confirmed_count",
            declared=f"{total_regions} required PDF regions human-confirmed",
            observed=f"{confirmed}/{total_regions} confirmed",
            reason="Page checks are not fidelity/clarity/completeness confirmation."))
    a2_scope = "not_applicable"
    if section_id == "financial":
        balance = data["balance"]
        identity_body = {k: v for k, v in balance.items() if k != "fingerprint"}
        expected = hashlib.sha256(json.dumps(
            identity_body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        _require(balance.get("fingerprint") == expected,
                 "financial: A2 balance structure fingerprint mismatch")
        a2_scope = "review_not_run"
        findings.append(_issue(
            gate_kind="demo_scope_contract_boundary", field="financial.balance_structure_A2_review_scope",
            declared="all reader-visible financial analysis included in sentence review",
            observed="A2 deterministic asset/liability analysis excluded from cited prose reviewer",
            reason="A1's zero hard errors and supported opinions cannot certify A2 semantics or release."))
    hard = AS.HardGateResult.create(report_version=version.report_version, issues=findings)
    # A mismatched ledger makes the existing review unreadable as an attested
    # result.  Its claims may still be shown as historical text, never as an
    # Assurance input or a successful ReviewerRunRecord.
    accepted_issues = data["issues"] if ledger_ok else ()
    result = AS.AssuranceResult.create(
        report_version=version.report_version, hard_gate_result_id=hard.result_id,
        reviewer_run_record_id=reviewer.record_id if ledger_ok else "",
        issue_ids=(issue.issue_id for issue in accepted_issues),
        blocking_issue_count=sum(issue.blocking for issue in accepted_issues),
        process_state=version.process_state, preview_state=version.preview_state,
        system_review_state=("system_review_not_passed" if ledger_ok
                             else "system_review_not_run"),
        human_review_state=version.human_review_state,
        system_release_eligible=False)
    facts = RR.SectionReviewFacts(
        section_id=section_id, manifest=data["manifest"], draft=data["draft"],
        version=version, balance=data["balance"])
    return {
        "section_id": section_id, "report_version": version.report_version,
        "source_record_id": version.record_id,
        "hard_gate_result": hard.to_dict(),
        "reviewer_run_record": reviewer.to_dict() if ledger_ok else None,
        "assurance_result": result.to_dict(),
        "prior_review_scope": "cited_prose_only" if ledger_ok else "untrusted_call_ledger",
        "review_locator_check": "manifest_coordinate_only" if ledger_ok else "not_trusted",
        "review_response_integrity": "recorded_hash_not_recomputed",
        "a2_review_state": a2_scope,
        "a2_report_version_binding": "not_bound" if section_id == "financial"
                                     else "not_applicable",
        "new_llm_calls": 0,
        "blocked_sentence_ids": list(check.blocked_sentence_ids),
        "prior_supported_issue_count": sum(issue.category == "supported"
                                           for issue in accepted_issues),
        "prior_issue_count": len(accepted_issues),
        "prior_blocking_issue_count": sum(issue.blocking for issue in accepted_issues),
        "source_preview_file": str(Path(section_id) / "cited_preview.md"),
        "source_artifact_sha256": data["artifact_sha256"],
    }, facts


def _count_by(issues: Sequence[Any], attr: str) -> dict[str, int]:
    """按某个已封闭词表的字段计数（升序键），供确定性聚合读回。"""
    out: dict[str, int] = {}
    for issue in issues:
        key = str(getattr(issue, attr))
        out[key] = out.get(key, 0) + 1
    return {k: out[k] for k in sorted(out)}


def _bind_report_review_runs(
        scopes: tuple[RR.ReportReviewScope, ...],
        runs: Sequence[RX.ReportReviewRun], *,
        report_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把已持久化的报告级审阅记录绑到 scope 上：**版本不符即变为陈旧读**。

    四条判定，方向各不相同：

    * `report_id` 不同 ⇒ 抛错。那是**另一份报告**的读数；`scope_kind` 是全局封闭词表，
      所以外来读数和"本报告改过正文的旧读数"只差 identity 字段——不先比报告身份，
      它就会被错标成"本报告旧版"，读者会把别处的意见当成自己的历史。
    * `scope_id` + `scope_version` + `bundle_id` **全等** ⇒ 绑上。审阅是在这份内容上做的。
    * 同一 `scope_kind`（同一报告）但 `scope_id` 不同（或版本、输入包不符）⇒ **陈旧读**：
      记进 `stale_report_review_runs`，**意见不进聚合**，并写明动的是哪一层身份。
    * `scope_kind` 本身不在本报告里 ⇒ 抛错。那不是陈旧，是**别处**的读数被混进来了。

    「同一 kind 但 scope_id 不同」必须算陈旧而不是错误：`scope_id` 的内容身份体里**含**
    `scope_version`，所以「正文改一字 ⇒ 版本变 ⇒ scope_id 也变」是同步发生的——按
    `scope_id` 全等去匹配，改过正文之后那份旧读数**一次都匹配不上**，于是陈旧分支永远
    不可达，而「拿旧记录重跑控制器」会整份读数失败。那不是更严格，是把一次只读诊断变成
    了不能再跑。改过内容之后旧读数要能被**如实显示为已失效**，而不是让整份读数拿不到。

    同一个 scope 上出现两个**都绑在当前版本**的不同运行 ⇒ 抛错：哪一份意见代表当前内容
    无法确定，聚合必须是确定性的（同一条 `run_id` 的重复传入不算冲突）。
    """
    by_kind: dict[str, Any] = {}
    stale: list[dict[str, Any]] = []
    by_kind_of_scope = {scope.scope_kind: scope for scope in scopes}
    for run in runs:
        _require(isinstance(run, RX.ReportReviewRun),
                 "report_review_runs 只接受 ReportReviewRun")
        # **报告身份先于 scope 种类**。scope_kind 是全局封闭词表，因此「另一份报告」的
        # 同类 scope 会长得和本报告几乎一样：只有 scope_id/version 不同。若不先比 report_id，
        # 它就会被当成"本报告改过正文之后的旧读数"标成陈旧——那是把别处的读数说成本报告的
        # 历史。这两件事完全不同，必须分开：外来报告 ⇒ 拒；同一报告、身份移动 ⇒ 陈旧。
        if run.report_id != report_id:
            raise CitedControllerError(
                f"报告级审阅记录 {run.run_id} 属于**另一份报告**（report_id="
                f"{run.report_id!r}，本报告为 {report_id!r}）：不同报告的同类 scope "
                "不得被当作本报告的历史读数（更不得标成陈旧）——fail-closed")
        scope = by_kind_of_scope.get(run.scope_kind)
        if scope is None:
            raise CitedControllerError(
                f"报告级审阅记录 {run.run_id} 指向本次评估里不存在的 scope "
                f"({run.scope_kind}/{run.scope_id})：不属于本报告的读数不得混入")
        identity_moved = run.scope_id != scope.scope_id
        version_moved = run.scope_version != scope.scope_version
        bundle_moved = run.bundle_id != scope.bundle.bundle_id
        if identity_moved or version_moved or bundle_moved:
            moved = [name for name, flag in
                     (("内容身份 scope_id", identity_moved), ("内容版本", version_moved),
                      ("输入包 bundle_id", bundle_moved)) if flag]
            stale.append({
                "scope_kind": run.scope_kind, "scope_id": run.scope_id,
                "run_id": run.run_id, "outcome": run.outcome,
                "run_scope_version": run.scope_version,
                "current_scope_version": scope.scope_version,
                "run_bundle_id": run.bundle_id, "current_bundle_id": scope.bundle.bundle_id,
                "issue_count": len(run.issues),
                "moved_identity_layers": moved,
                "reason": ("这份审阅是在**另一份**输入/正文版本上做的（动的层："
                           + "、".join(moved)
                           + "）：内容改过之后它不再是对当前内容的审阅，"
                             "因此它的意见不进入本报告的聚合"),
            })
            continue
        prior = by_kind.get(run.scope_kind)
        if prior is not None and prior.run_id != run.run_id:
            raise CitedControllerError(
                f"{run.scope_kind}：有两个都绑在当前版本上的审阅记录"
                f"（{prior.run_id} / {run.run_id}），无法确定聚合哪一份（fail-closed）")
        by_kind[run.scope_kind] = run
    return by_kind, stale


#: 逐 scope 的三套单元计数各说的是什么。写成常量而不是散落各处，是因为这几个数**看起来**
#: 都能读成"覆盖了多少材料"，而其中只有一个是真的"进了请求"，没有一个能读成"被核实"。
_UNIT_COUNT_MEANING = (
    "requested_unit_count = 这些成员确实进了请求（含失败批）；"
    "covered_unit_count = 这些成员进的是一个**收到可解析回复**的批次；"
    "reported_unit_count = 模型**实际提出问题**的成员数。"
    "三者互不推导，且**没有一个**表示『逐份材料被核实』——本产物没有表达那件事的字段。")

#: `supported_count` 在业务审阅契约（`irv-2`）下几乎恒为 0：审阅只报发现，不逐条归档。
_SUPPORTED_COUNT_MEANING = (
    "模型自报 `supported` 的条数。`irv-2` 起审阅**只报发现**，该数通常为 0，"
    "**不能**读成『其余材料都已核实通过』——没被提到的成员只是没被提到。")


def _scope_summary(scope: RR.ReportReviewScope) -> dict[str, Any]:
    """输入面摘要 + **由分批规则推出的**调用上限。

    上限是**推出来的**，不是输入面自己声明的一个常数：输入面只声明分批规则
    （`batching_rules`），批数由 `plan_report_review_batches` 在这份输入上真的切一遍得到。
    推导失败（单个单元自身超容量）时如实写 `derivation_error`，而不是退回一个好看的数。
    """
    row = RR.summarize(scope)
    try:
        bound = RX.report_review_structural_bound(scope)
    except RX.ReportReviewError as exc:
        row["derived_batch_count"] = None
        row["derivation_error"] = str(exc)
    else:
        row["derived_batch_count"] = int(bound["batches"])
        row["derived_batch_units"] = int(bound["units"])
        row["derived_max_batch_chars"] = int(bound["max_batch_chars"])
        row["derived_max_batch_units"] = int(bound["max_batch_units"])
    return row


def _review_coverage_value(run: Any) -> str:
    """覆盖状态只描述**实际发生了什么**，不描述"应该有意见"。"""
    if run.outcome == "reviewed":
        return ("reviewed_by_real_call" if run.producer_kind == "independent_llm_review"
                else "reviewed_by_offline_echo")
    if run.outcome == "failed":
        return "review_started_failed"
    return "review_skipped_reviewability_blocked"


def _report_states(sections: list[dict[str, Any]],
                   scopes: tuple[RR.ReportReviewScope, ...],
                   bound_runs: dict[str, Any],
                   stale_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """七个状态**平铺分列**，互不推导，也不压成单一 `success`。

    这是「流程完成 + 预览可用 + 系统审核未通过 + 人工未复核」能够被如实表达的地方：
    机械错误、审阅覆盖、审阅意见、预览、系统放行、人工接受、正式阶段关闭各自一格。
    最后一格**不由本控制器计算**——它只是治理文档的只读快照。

    `m4cc-4`：报告级审阅的覆盖与意见来自**真实记录**（若调用方给了），但意见仍然
    **不移动**任何其它状态——见 `review_opinion_effect` 的显式布尔量。聚合是确定性的：
    意见按 `issue_id` 升序，计数只由已绑定的运行推出。三套单元计数（放进请求 / 收到
    可解析回复 / 模型提出问题）各自成键，并附 `coverage_meaning`；`supported_count`
    附 `supported_count_meaning`，因为它在 `irv-2` 下通常为 0 且容易被误读成"其余都过了"。
    """
    by_kind = {scope.scope_kind: scope for scope in scopes}
    coverage: dict[str, str] = {}
    opinions: dict[str, Any] = {}
    for item in sections:
        sid = str(item["section_id"])
        trusted = item["prior_review_scope"] == "cited_prose_only"
        coverage[f"{sid}_cited_prose"] = ("reviewed_by_prior_real_call" if trusted
                                          else "not_trusted_call_provenance")
        opinions[f"{sid}_cited_prose"] = {
            "issue_count": item["prior_issue_count"],
            "supported_count": item["prior_supported_issue_count"],
            "blocking_count": item["prior_blocking_issue_count"],
            "producer_kind": "independent_llm_review" if trusted else "untrusted",
            "trusted_as_assurance_input": trusted,
            "covered_by_scope": True,
        }
    for kind in RR.REPORT_REVIEW_SCOPE_KINDS:
        scope = by_kind.get(kind)
        if scope is None:  # pragma: no cover - build_report_review_scopes is total
            coverage[kind] = "input_not_prepared"
            continue
        run = bound_runs.get(kind)
        # 两个分支给出**同一个键集**：读产物的人不必先判断"这一格有没有运行"才能知道怎么读它
        # ——没有运行时 `run_id` 就是 `None`，而不是整块键消失。
        if run is None:
            coverage[kind] = "input_prepared_review_not_run"
            opinions[kind] = {
                "run_id": None, "report_id": None, "outcome": None,
                "issue_count": 0, "supported_count": 0, "blocking_count": 0,
                "by_category": {}, "by_severity": {},
                "producer_kind": None, "trusted_as_assurance_input": False,
                "declared_unit_count": len(scope.covered_unit_ids),
                "requested_unit_count": 0,
                "covered_unit_count": 0, "reported_unit_count": 0,
                "excluded_unit_count": len(scope.covered_unit_ids),
                "requested_batch_count": 0, "completed_batch_count": 0,
                "failed_batch_count": 0, "llm_call_count": 0,
                "prompt_version": None, "model_policy_id": None, "issue_ids": [],
                "note": "审阅输入已备，但本批没有发出任何调用，因此没有意见可聚合。",
                "coverage_meaning": _UNIT_COUNT_MEANING,
            }
            continue
        coverage[kind] = _review_coverage_value(run)
        issues = sorted(run.issues, key=lambda issue: issue.issue_id)
        by_category: dict[str, int] = {}
        for issue in issues:
            by_category[issue.category] = by_category.get(issue.category, 0) + 1
        opinions[kind] = {
            "run_id": run.run_id,
            "report_id": run.report_id,
            "outcome": run.outcome,
            "issue_count": len(issues),
            "supported_count": by_category.get("supported", 0),
            "supported_count_meaning": _SUPPORTED_COUNT_MEANING,
            "blocking_count": sum(1 for issue in issues if issue.blocking),
            "by_category": {k: by_category[k] for k in sorted(by_category)},
            "by_severity": _count_by(issues, "severity"),
            "producer_kind": run.producer_kind,
            "trusted_as_assurance_input": run.trusted_as_assurance_input,
            "declared_unit_count": len(run.declared_unit_ids),
            "requested_unit_count": len(run.requested_unit_ids),
            "covered_unit_count": len(run.covered_unit_ids),
            "reported_unit_count": len(run.reported_unit_ids),
            "excluded_unit_count": len(run.excluded_unit_ids),
            "requested_batch_count": len(run.requested_batch_ids),
            "completed_batch_count": len(run.completed_batch_ids),
            "failed_batch_count": len(run.failed_batch_ids),
            "llm_call_count": sum(record.llm_call_count for record in run.records),
            "prompt_version": run.prompt_version,
            "model_policy_id": run.model_policy_id,
            "issue_ids": [issue.issue_id for issue in issues],
            "coverage_meaning": _UNIT_COUNT_MEANING,
        }
    return {
        "review_opinion_effect": {
            "moves_mechanical": False,
            "moves_preview": False,
            "moves_system_release": False,
            "moves_human_acceptance": False,
            "moves_formal_phase_closure": False,
            "note": ("审阅意见只是独立读者的旁证：它不修正文、不补研究、不改机械硬错、"
                     "不放行、不代表人工接受，也不触及正式阶段关闭。这五项是显式布尔量，"
                     "读产物的人不必从散文里推断。"),
        },
        "stale_report_review_runs": list(stale_runs),
        "unit_count_meaning": _UNIT_COUNT_MEANING,
        "policy_version": POLICY_VERSION,
        "report_versions": {item["section_id"]: item["report_version"]
                            for item in sections},
        "mechanical": {
            item["section_id"]: {
                "blocked_sentence_count": len(item["blocked_sentence_ids"]),
                "blocked_sentence_ids": list(item["blocked_sentence_ids"]),
            } for item in sections},
        "review_coverage": coverage,
        "review_opinions": opinions,
        "preview": {item["section_id"]: item["assurance_result"]["preview_state"]
                    for item in sections},
        "system_release": {
            "eligible": False,
            "per_section": {item["section_id"]: {
                "system_review_state": item["assurance_result"]["system_review_state"],
                "release_eligible": item["assurance_result"]["system_release_eligible"],
            } for item in sections},
            # 放行与否**不由**审阅覆盖决定，所以 `eligible` 是常量 False，而理由要说清楚
            # 当前真实卡在哪里——包括"报告级审阅跑到了什么程度"，否则这段文字会在审阅真的
            # 发生之后变成一句陈旧的话。
            "reason": ("机械硬错与未解决的必需缺口阻断系统放行；报告级审阅当前覆盖："
                       + "、".join(f"{kind}={coverage[kind]}"
                                   for kind in RR.REPORT_REVIEW_SCOPE_KINDS
                                   if kind in coverage)
                       + "。预览仍可读，但这不等于放行。"),
        },
        "human_acceptance": {
            "state": sections[0]["assurance_result"]["human_review_state"],
            "write_api_available": False,
            "note": "本控制器没有修改人工接受状态的 API；该字段只被带过、不被计算。",
        },
        "formal_phase_closure": {
            "computed_by_controller": False,
            "value": "formal_closure_not_evaluated",
            "note": ("正式阶段关闭只来自治理文档与验收记录的独立只读快照，"
                     "不由本控制器计算，也不随 report_version 变化。"),
        },
    }


def assess_persisted_run(run_dir: Path, *,
                         report_review_runs: Sequence[RX.ReportReviewRun] = ()
                         ) -> dict[str, Any]:
    """Assess one persisted dual-section run without modifying or re-running it.

    `report_review_runs` 是调用方**已经读回**的报告级审阅记录（`report_reviewer
    .load_report_review_run`）。本函数不读盘外的东西、不发起调用、不写盘：它只做版本绑定与
    确定性聚合。传空元组 = 本批还没有真实调用，此时覆盖状态如实显示
    `input_prepared_review_not_run`——这正是"输入已备"与"审阅已发生"的区别，
    不能被一个默认值抹平。
    """
    run_dir = Path(run_dir).resolve(strict=True)
    ledger, ledger_sha256 = _read_json(run_dir / "cited_call_ledger.json")
    _require(ledger.get("mode") == "real" and ledger.get("run_outcome") == "completed",
             "Source run is not a completed real run")
    call_budget = ledger.get("call_budget")
    _require(isinstance(call_budget, dict), "Source run has no typed call budget")
    attempts = call_budget.get("attempts")
    _require(isinstance(attempts, list), "Call ledger attempts must be a list")
    sections = []
    facts: list[RR.SectionReviewFacts] = []
    previews: dict[str, str] = {}
    for section_id in SECTIONS:
        review_attempts = [attempt for attempt in attempts
                           if isinstance(attempt, dict)
                           and attempt.get("section_id") == section_id
                           and attempt.get("category") == "cited_prose_review"]
        assessment, section_facts = assess_section(
            run_dir / section_id, section_id,
            review_attempts[0] if len(review_attempts) == 1 else None)
        sections.append(assessment)
        facts.append(section_facts)
    _require(sections[0]["report_version"] != sections[1]["report_version"],
             "Two distinct sections must retain distinct report versions")
    for section_id in SECTIONS:
        try:
            previews[section_id] = (run_dir / section_id / "cited_preview.md").read_text(
                encoding="utf-8")
        except (OSError, UnicodeError) as exc:  # pragma: no cover - checked above
            raise CitedControllerError(
                f"{section_id}: cited preview unreadable for report review: {exc}") from exc
    try:
        scopes = RR.build_report_review_scopes(report_id=run_dir.name, sections=facts,
                                               previews=previews)
    except RR.ReportReviewError as exc:
        raise CitedControllerError(f"report-level review inputs cannot be trusted: {exc}") from exc
    try:
        bound_runs, stale_runs = _bind_report_review_runs(
            scopes, report_review_runs, report_id=run_dir.name)
    except (RX.ReportReviewError, AS.AssuranceSchemaError) as exc:
        raise CitedControllerError(
            f"已持久化的报告级审阅记录不能作为本报告的输入：{exc}") from exc
    states = _report_states(sections, scopes, bound_runs, stale_runs)
    # 覆盖状态与真实记录必须对得上：读到 `reviewed_by_real_call` 就一定有那份记录在产物里。
    for scope in scopes:
        value = states["review_coverage"].get(scope.scope_kind, "")
        if value in ("reviewed_by_real_call", "reviewed_by_offline_echo"):
            _require(scope.scope_kind in bound_runs,
                     f"{scope.scope_kind}：覆盖状态为 {value!r}，但没有绑定的审阅记录")
    return {
        "policy_version": POLICY_VERSION,
        "source_run": str(run_dir),
        "section_assessments": sections,
        "source_call_ledger_sha256": ledger_sha256,
        "cross_section_report_version": None,
        # `m4cc-2` 里这两格恒为字面量 "not_run"。现在它们**直接取**审阅覆盖里的同一个值：
        # 同一件事在两个地方用两套词说，读产物的人就得先证明它们指的是同一件事。
        "cross_section_semantic_review": states["review_coverage"]["cross_section"],
        "unused_material_selectivity_review": states["review_coverage"][
            "material_selectivity"],
        "report_review_scopes": [_scope_summary(scope) for scope in scopes],
        "report_review_inputs": [scope.to_dict() for scope in scopes],
        "report_review_runs": [run.to_dict() for _, run in sorted(bound_runs.items())],
        "report_states": states,
        "new_llm_calls": 0,
        "report_review_calls_recorded": sum(
            record.llm_call_count
            for run in bound_runs.values() for record in run.records),
        "formal_phase_closure": "formal_closure_not_evaluated",
        "system_release_eligible": False,
        "preview_source_preserved": True,
    }

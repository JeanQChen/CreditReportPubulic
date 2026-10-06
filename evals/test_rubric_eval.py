# -*- coding: utf-8 -*-
"""离线只读评测引擎（`evaluation/rubric_eval`）的聚焦回归。

跑法（无管道/无重定向）：``PYTHONIOENCODING=utf-8 python -m evals.test_rubric_eval``

## 这条回归盯的是「评测自己会不会撒谎」

被测对象不是一个业务模块，而是一套**读数引擎**。所以它的缺陷不会表现为崩溃，而会表现为
一个看起来很正常的数字：把「没测」写成 0、把「上游没跑」写成 0/0、把另一个 run 的产物
读成本 run 的、把离线回声的 `issues[]` 算成「本 run 被独立审阅覆盖」。这些都不会刷红，
只会让 readback 更好看。因此本模块的主干是**反例**，逐条钉住"必须拒/必须报未测"：

| 反例 | 钉住的行为 |
|---|---|
| 缺文件 | 缺件是结果不是异常：`json()` 回 `None`、行状态是 `NOT_RUN_UPSTREAM`，**不是** 0 |
| 错哈希 | 对象被改一字节 ⇒ P01 分子下降、`ok=False`，不得照抄声明哈希 |
| 跨 run 拼接 | 另一个 run 的正文不得进本 run 的读数；证据路径必须落在本 run 目录内 |
| 零分母 | `ratio(x, 0)` 回 `None`；`denominator=0` 的行 `value` 必须是 `None`，绝不是 100% |
| 上游失败 | 财务节没跑 ⇒ B04 报 `NOT_RUN_UPSTREAM` 并点名第一个缺件，不得报 0 分 |
| 离线回声冒充真实审阅 | `offline_diagnostic_echo` 的 `issues[]` **不**算独立审阅覆盖，P08 必须 fail-closed |

另有两面正向对照，防止上面每一条都能被"恒拒"糊弄过去：一份**合规**的合成 run 必须真的
测出 `MEASURED` 行；一套四件套必须真的落盘、每个样本恰好 24 行、且**只创建不覆盖**。

全程不联网、不发模型请求、不建 run、不写 `data/` 库、不改 `evaluation/results` 下的任何字节
（合成 run 全部落在临时目录里）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.rubric_eval import measures as M  # noqa: E402
from evaluation.rubric_eval.catalog import (  # noqa: E402
    BY_ID, CATALOG_VERSION, METRICS, assert_catalog_complete,
)
from evaluation.rubric_eval.evaluate import (  # noqa: E402
    Sample, build_catalog_snapshot, evaluate_sample, write_deliverables,
)
from evaluation.rubric_eval.measures import assert_all_metrics_covered, evaluate_run  # noqa: E402
from evaluation.rubric_eval.reader import RunReader  # noqa: E402
from evaluation.rubric_eval.result import MetricResult, measured, pending, ratio  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except BaseException:  # noqa: BLE001
        return False
    return False


# ============================================================ 合成 run 夹具
def _write_json(root: Path, relpath: str, payload) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_text(root: Path, relpath: str, text: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_GHOST_CALL = "ghost-call-not-in-ledger"


def _ledger(call_ids: tuple[str, ...], *, outcome: str = "completed") -> dict:
    return {
        "run_outcome": outcome,
        "mode": "real",
        "approved_model": "deepseek-v4-pro",
        "budget_policy_version": "cited-budget-3",
        "call_budget": {"attempts": [
            {"call_id": cid, "status": "ok",
             "prompt_version": ("cited_prose_review_v1@crr-10" if "review" in cid
                                else "cited_company_prose_v1@cp-26")}
            for cid in call_ids]},
    }


def _prose(sentence_ids: tuple[str, ...]) -> dict:
    return {"draft": {"subsections": [{"subsection_id": "sub-1", "paragraphs": [
        {"sentences": [{"sentence_id": sid, "text": f"第 {i + 1} 句。"}
                       for i, sid in enumerate(sentence_ids)]}]}]}}


def _checks(sentence_ids: tuple[str, ...], hard: tuple[str, ...] = ()) -> dict:
    return {"sentence_count": len(sentence_ids),
            "hard_error_sentence_ids": list(hard),
            "fact_safety_sentence_ids": list(hard),
            "column_coverage_sentence_ids": [],
            "blocked_sentence_ids": list(hard),
            "mechanical_verdict": "pass" if not hard else "fail",
            "publishability": "not_publishable",
            "policy_version": "sc-7"}


def _review(sentence_ids: tuple[str, ...], call_id: str, *,
            producer: str = "independent_llm_review", outcome: str | None = "reviewed") -> dict:
    payload = {"review_producer_kind": producer, "report_version": "crpv-3",
               "call": {"call_id": call_id, "status": "ok",
                        "prompt_version": "cited_prose_review_v1@crr-10"}}
    if outcome is not None:
        payload["outcome"] = outcome
        payload["sentence_ids"] = list(sentence_ids)
        payload["issues"] = [{"issue_id": f"i{n}", "sentence_id": sid,
                              "category": "supported", "severity": "info",
                              "blocking": False, "reason": "与材料一致"}
                             for n, sid in enumerate(sentence_ids)]
        payload["blocking_issue_ids"] = []
    else:
        payload["review_failure"] = "review_reply_unparsable"
        payload["sentence_ids"] = []
        payload["issues"] = []
    return payload


def _report(sentence_ids: tuple[str, ...], *, producer: str = "independent_llm_review") -> dict:
    return {"sentence_count": len(sentence_ids), "review_producer_kind": producer,
            "system_review_state": ("system_review_passed_awaiting_human" if producer
                                    else "system_review_not_run"),
            "publishability": "not_publishable", "human_review_state": "human_not_reviewed"}


def _manifest(citation_key: str, *, doc_id: str = "doc-a", sha: str = "a" * 64) -> dict:
    return {"materials": [{
        "citation_key": citation_key, "material_id": "mat-1",
        "authority_kind": "company_disclosure", "provenance_identity": "prov-1",
        "aspect_ids": ["company_business_main.main_business"],
        "content_qualification": {"is_material": True},
        "locator_ref": {"owner": f"evidence_document:{doc_id}@sha256-{sha[:16]}#p1",
                        "locator_kind": "outline_span"},
    }], "facts": [], "pack_set_fingerprint": "pk-1"}


#: 与真实 run 的 `presentation_routing.column_gaps` 同形（B01 的栏目全集与展示档就取这里）。
_ROUTING = {"column_gaps": [{
    "column": "company_business_main.main_business",
    "contract_display_tier": "required_body",
    "contract_requirement_text": "主营业务构成",
    "reason": "no_registered_metric_in_selected_facts",
}]}


def _make_run(root: Path, run_id: str, *, sections=("company", "financial"),
              producer: str = "independent_llm_review",
              review_call: str | None = None,
              ledger_calls: tuple[str, ...] = ("call-w", "call-r"),
              input_objects: tuple[tuple[str, str, str], ...] = ()) -> Path:
    """造一个最小的 run 目录。

    ``input_objects`` 是 ``(document_id, 落盘相对路径, 真实字节)``；声明的哈希由调用方写进
    `run_input_binding.json`，好让"错哈希"那一条能构造出**声明与实际不一致**的对象。
    """
    run_dir = root / "evaluation" / "results" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir, "cited_call_ledger.json", _ledger(ledger_calls))
    _write_text(run_dir, "run_progress.jsonl", "\n".join([
        json.dumps({"seq": 1, "stage": "writer_requested", "status": "ok"}, ensure_ascii=False),
        json.dumps({"seq": 2, "stage": "writer_replied", "status": "ok"}, ensure_ascii=False),
        json.dumps({"seq": 3, "stage": "sentence_checked", "status": "ok"}, ensure_ascii=False),
        json.dumps({"seq": 4, "stage": "review_requested", "status": "ok"}, ensure_ascii=False),
        json.dumps({"seq": 5, "stage": "review_replied", "status": "ok"}, ensure_ascii=False),
        json.dumps({"seq": 6, "stage": "report_version_written", "status": "ok"}, ensure_ascii=False),
        json.dumps({"seq": 7, "stage": "section_emitted", "status": "ok"}, ensure_ascii=False),
    ]) + "\n")

    for section in sections:
        sids = ("s1", "s2") if section == "company" else ("f1",)
        cid = review_call or ("call-r" if section == "company" else "call-r2")
        _write_json(run_dir, f"{section}/cited_prose.json", _prose(sids))
        _write_json(run_dir, f"{section}/sentence_checks.json", _checks(sids))
        _write_json(run_dir, f"{section}/review_issues.json", _review(sids, cid, producer=producer))
        _write_json(run_dir, f"{section}/cited_report_version.json", _report(sids, producer=producer))
        _write_json(run_dir, f"{section}/cited_input_manifest.json", _manifest(f"m-{section}"))
        _write_json(run_dir, f"{section}/source_table_display.json", {"regions": []})
        _write_json(run_dir, f"{section}/fact_placement.json", {"by_disposition": {}})
        _write_json(run_dir, f"{section}/presentation_routing.json", _ROUTING)
        _write_json(run_dir, f"{section}/cited_gap_bins.json", {"axes": {"by_bin": {}},
                                                               "not_applicable_policies": []})
        _write_json(run_dir, f"{section}/withheld_candidates.json", {"by_reason": {}})

    if input_objects:
        binding_docs = []
        for order, (doc_id, relpath, text) in enumerate(input_objects):
            obj = root / "data" / "run_inputs" / run_id / relpath
            obj.parent.mkdir(parents=True, exist_ok=True)
            obj.write_bytes(text.encode("utf-8"))
            import hashlib
            binding_docs.append({
                "document_id": doc_id, "object_relpath": relpath,
                "declared_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "declared_filename": f"{doc_id}.pdf", "size_bytes": len(text.encode("utf-8")),
                "upload_order": order})
        _write_json(root / "data" / "run_inputs" / run_id, "run_input_manifest.json",
                    {"documents": binding_docs})
        _write_json(run_dir, "run_input_binding.json", {"documents": binding_docs})
    return run_dir


def _rows_by_id(reader: RunReader) -> dict:
    return {r.metric_id: r.to_row() for r in evaluate_run(reader)}


# ============================================================ A. 目录与覆盖
def test_catalog() -> None:
    ids = assert_catalog_complete()
    check(len(ids) == 24, f"目录应当是 24 个指标，实为 {len(ids)}")
    check(len(set(ids)) == 24, "指标 ID 不得重复")
    check(CATALOG_VERSION == "rbeval-catalog-1", "目录版本号被改动")
    for spec in METRICS:
        check(bool(spec.definition and spec.method and spec.numerator_spec
                   and spec.denominator_spec and spec.required_evidence),
              f"{spec.metric_id} 的定义/判法/分子/分母/所需证据不得为空")
        check(spec.primary_dimension in spec.dimensions,
              f"{spec.metric_id} 的主归属维度不在自己的维度集合里")
    covered = assert_all_metrics_covered()
    check(set(covered) == set(ids), "实现面与目录面的指标集合不一致")
    snapshot = build_catalog_snapshot()
    check(snapshot["metric_count"] == 24, "目录快照的指标数不是 24")
    check(len(snapshot["metrics"]) == 24, "目录快照的行数不是 24")


# ============================================================ B. 形状纪律
def test_shape_discipline() -> None:
    check(ratio(0, 0) is None, "分母为 0 时比率必须是 None")
    check(ratio(3, 0) is None, "分母为 0 时比率必须是 None（不得回落成 100%）")
    check(ratio(None, 5) is None, "分子缺失时比率必须是 None")
    check(ratio(3, 4) == 0.75, "正常比率算错了")

    # 非 MEASURED 一律不许带数字：这是"不填 0/100%"落到类型上的那一条。
    forged = MetricResult(metric_id="B13", source_run_id="r", scope="s",
                          execution_state="DID_RUN", measurement_state="BENCHMARK_PENDING",
                          numerator=7, denominator=9, value=1.0)
    check(forged.numerator is None and forged.value is None,
          "BENCHMARK_PENDING 的行不得保留分子或数值")

    zero_den = measured(metric_id="P04", run_id="r", scope="s", numerator=0, denominator=0)
    check(zero_den.measurement_state == "MEASURED" and zero_den.denominator == 0
          and zero_den.value is None,
          "分母为 0 的 MEASURED 行必须保留 denominator=0 且 value=None")

    empty = measured(metric_id="P04", run_id="r", scope="s", numerator=5, denominator=0,
                     measured_but_empty=True)
    check(empty.numerator == 5 and empty.value is None,
          "measured_but_empty 不是清空分子，而是不给比率")

    check(_raises(lambda: pending(metric_id="B02", run_id="r", scope="s",
                                  measurement_state="MEASURED", execution_state="DID_RUN",
                                  verdict="v", first_missing="x"),
                  ValueError),
          "pending() 不得用来造 MEASURED 行")
    check(_raises(lambda: pending(metric_id="B02", run_id="r", scope="s",
                                  measurement_state="BENCHMARK_PENDING", execution_state="DID_RUN",
                                  verdict="v", first_missing=""),
                  ValueError),
          "未测成的行必须写明第一个缺的东西")
    check(_raises(lambda: MetricResult(metric_id="B02", source_run_id="r", scope="s",
                                       execution_state="DID_RUN",
                                       measurement_state="TOTALLY_FINE", numerator=1),
                  ValueError),
          "未知测量状态必须被拒")


# ============================================================ C. 缺文件
def test_missing_files(work: Path) -> None:
    reader = RunReader(repo_root=work, run_id="no-such-run")
    check(reader.json("company/cited_prose.json") is None, "缺文件时 json() 必须回 None")
    check(reader.has("company/cited_prose.json") is False, "缺文件时 has() 必须回 False")
    check(reader.ledger() is None, "缺账本时 ledger() 必须回 None")
    check(reader.section_dirs() == (), "不存在的 run 不得报出任何节")

    rows = _rows_by_id(reader)
    check(len(rows) == 24, f"缺件的 run 也必须给满 24 行，实为 {len(rows)}")
    bad = [mid for mid, row in rows.items()
           if row["measurement_state"] != "MEASURED" and row["value"] is not None]
    check(not bad, f"未测成的行不得带数值：{bad}")
    # 分母为 0 的 MEASURED 行只允许出现在 `measured_but_empty` 这类"确实算了、集合是空的"情形。
    empty_measured = [mid for mid, row in rows.items()
                      if row["measurement_state"] == "MEASURED" and not row["denominator"]]
    check(all(rows[mid]["value"] is None for mid in empty_measured),
          f"分母为 0 的行不得给出比率：{empty_measured}")
    # 不存在的 run 上只允许这几条「如实测出 0」的行：D02 的 4 条身份轴一条都定位不到、
    # P04/P05 的候选与材料集合确实是空集。其余指标必须报未运行/未测成，不得凭空给 0。
    measured = sorted(mid for mid, row in rows.items() if row["measurement_state"] == "MEASURED")
    check(measured == ["D02", "P04", "P05"],
          f"不存在的 run 上不该有这些 MEASURED 行：{measured}")
    check(rows["P07"]["measurement_state"] == "NOT_RUN_UPSTREAM",
          "没有正式正文时 P07 必须报上游未运行")
    check(rows["B13"]["measurement_state"] == "BENCHMARK_PENDING"
          and rows["B13"]["execution_state"] == "FAILED",
          "没有审阅时 B13 的执行面必须是 FAILED、测量面仍是待 Gold")


# ============================================================ D. 错哈希
def test_wrong_hash(work: Path) -> None:
    run_id = "hash-run"
    _make_run(work, run_id, sections=("company",), input_objects=(
        ("doc-a", "objects/aaa.pdf", "AAA-真实字节"),))
    # 声明与对象都对 ⇒ P01 应当满分。
    rows = _rows_by_id(RunReader(repo_root=work, run_id=run_id))
    check(rows["P01"]["numerator"] == 1 and rows["P01"]["denominator"] == 1,
          f"字节未改时 P01 应当 1/1，实为 {rows['P01']['numerator']}/{rows['P01']['denominator']}")

    # 改一字节：声明哈希不动，对象变了 ⇒ 必须掉分，且把不符的那一件列出来。
    obj = work / "data" / "run_inputs" / run_id / "objects" / "aaa.pdf"
    obj.write_bytes("AAA-真实字节被改动".encode("utf-8"))
    rows = _rows_by_id(RunReader(repo_root=work, run_id=run_id))
    check(rows["P01"]["numerator"] == 0 and rows["P01"]["denominator"] == 1,
          "对象被改一字节后 P01 不得满分")
    check(rows["P01"]["value"] == 0.0, "改一字节后 P01 的值应当是 0.0（分母不为 0，允许报 0）")
    mismatch = rows["P01"]["details"]["mismatches"][0]
    check(mismatch["actual_sha256"] != mismatch["declared_sha256"],
          "不符记录里实际哈希必须与声明哈希不同")
    check(mismatch["ok"] is False, "不符记录必须显式 ok=False")

    # 对象整个不在盘上：不是"哈希不符"，是"缺件"。
    obj.unlink()
    rows = _rows_by_id(RunReader(repo_root=work, run_id=run_id))
    item = rows["P01"]["details"]["items"][0]
    check(item["actual_sha256"] is None and item["ok"] is False,
          "对象不在盘上时实际哈希必须是 None（缺件 ≠ 哈希不符）")


# ============================================================ E. 跨 run 拼接
def test_cross_run_splicing(work: Path) -> None:
    _make_run(work, "run-a", sections=("company",))
    # run-b 只有空目录：正文全在 run-a 里。
    (work / "evaluation" / "results" / "run-b").mkdir(parents=True, exist_ok=True)
    _write_json(work, "evaluation/results/run-b/cited_call_ledger.json",
                _ledger(("call-w",)))

    reader_b = RunReader(repo_root=work, run_id="run-b")
    check(reader_b.json("company/cited_prose.json") is None,
          "run-b 不得读到 run-a 的正文")

    rows_a = _rows_by_id(RunReader(repo_root=work, run_id="run-a"))
    rows_b = _rows_by_id(reader_b)
    check(rows_a["P07"]["measurement_state"] == "MEASURED",
          "run-a 有正文，P07 应当能测")
    check(rows_b["P07"]["measurement_state"] == "NOT_RUN_UPSTREAM",
          "run-b 没有正文，P07 必须报上游未运行（不得拿 run-a 的读数顶替）")

    # 证据面：本 run 的每一条证据都必须落在本 run 自己的目录里。
    payload, refs = evaluate_sample(work, Sample(run_id="run-b", role="main", why="反例"), None)
    stray = [r for r in refs
             if not (r["path"].startswith("evaluation/results/run-b/")
                     or r["path"].startswith("data/run_inputs/run-b/")
                     or r["path"].startswith("run-input::"))]
    check(not stray, f"run-b 的证据里混进了别的 run 的路径：{[r['path'] for r in stray][:3]}")
    wrong_run = [r for r in refs if r["run_id"] != "run-b" and not str(r["run_id"]).startswith("dev-batch::")]
    check(not wrong_run, f"run-b 的证据里混进了别的 run_id：{[r['run_id'] for r in wrong_run][:3]}")
    check(payload["sample"]["run_id"] == "run-b", "结果面必须只描述本 run")
    check(payload["metric_count"] == 24, "每个样本都必须给满 24 行")


# ============================================================ F. 上游失败
def test_upstream_failure(work: Path) -> None:
    _make_run(work, "partial-run", sections=("company",))
    rows = _rows_by_id(RunReader(repo_root=work, run_id="partial-run"))
    check(rows["B04"]["measurement_state"] == "NOT_RUN_UPSTREAM",
          "财务节没跑时 B04 必须报上游未运行，而不是 0 分")
    check("financial" in rows["B04"]["first_missing"],
          f"B04 的第一个缺件必须点名财务侧文件，实为 {rows['B04']['first_missing']!r}")
    check(rows["B04"]["value"] is None and rows["B04"]["numerator"] is None,
          "上游未运行不得留下 0 分子")
    check(rows["B01"]["execution_state"] == "DID_RUN",
          "公司节跑过，B01 的执行面应当是 DID_RUN")
    check(rows["B01"]["measurement_state"] == "HUMAN_PENDING",
          "公司节必需栏目必须停在待人工，不得自动通过")


# ============================================================ G. 离线回声冒充真实审阅
def test_offline_echo_is_not_review(work: Path) -> None:
    run_id = "echo-run"
    _make_run(work, run_id, sections=("company",),
              producer="offline_diagnostic_echo", review_call=_GHOST_CALL,
              ledger_calls=("call-w",))
    rows = _rows_by_id(RunReader(repo_root=work, run_id=run_id))
    row = rows["P08"]
    check(row["measurement_state"] == "FAILED_TO_MEASURE",
          f"离线回声不得被算成独立审阅，P08 实为 {row['measurement_state']}")
    check(row["value"] is None and row["numerator"] is None,
          "离线回声冒充审阅时不得报出覆盖率")
    state = row["details"]["per_section"][0]["state"]
    check(state == "NOT_INDEPENDENT",
          f"离线回声的节必须记成 NOT_INDEPENDENT，实为 {state}")
    check("离线回声" in row["first_missing"],
          f"第一个缺的东西必须点名离线回声，实为 {row['first_missing']!r}")

    b13 = rows["B13"]
    check(b13["details"]["opinions"] == 0,
          f"离线回声的意见不得计入本 run 独立审阅意见，实为 {b13['details']['opinions']}")
    check(b13["execution_state"] == "FAILED",
          "只有离线回声时 B13 的执行面必须是 FAILED")

    # 对照：同一份产物换成真实产出者、且 call_id 在本 run 账本里 ⇒ 必须能测出 MEASURED。
    _make_run(work, "real-review-run", sections=("company",),
              producer="independent_llm_review", review_call="call-r",
              ledger_calls=("call-w", "call-r"))
    real = _rows_by_id(RunReader(repo_root=work, run_id="real-review-run"))
    check(real["P08"]["measurement_state"] == "MEASURED"
          and real["P08"]["numerator"] == 2 and real["P08"]["denominator"] == 2,
          f"真实独立审阅应当测出 2/2，实为 {real['P08']['numerator']}/{real['P08']['denominator']}")

    # 再一组对照：产出者说是真实的，但 call_id 不在本 run 账本里 ⇒ 仍不得算。
    _make_run(work, "ghost-call-run", sections=("company",),
              producer="independent_llm_review", review_call=_GHOST_CALL,
              ledger_calls=("call-w",))
    ghost = _rows_by_id(RunReader(repo_root=work, run_id="ghost-call-run"))
    check(ghost["P08"]["measurement_state"] == "FAILED_TO_MEASURE",
          "call_id 不在本 run 账本里的审阅不得算作独立审阅")


# ============================================================ H. 真实样本上的正向读数
def test_healthy_run(work: Path) -> None:
    _make_run(work, "healthy", sections=("company", "financial"))
    rows = _rows_by_id(RunReader(repo_root=work, run_id="healthy"))
    check(rows["P04"]["measurement_state"] == "MEASURED"
          and rows["P04"]["numerator"] == 2 and rows["P04"]["denominator"] == 2,
          f"两条带 typed 资格的材料应当测出 2/2，实为 {rows['P04']['numerator']}/{rows['P04']['denominator']}")
    check(rows["P05"]["measurement_state"] == "MEASURED", "P05 在有清单时应当能测")
    check(rows["P06"]["measurement_state"] == "MEASURED", "P06 在有阶段日志时应当能测")
    check(rows["P07"]["measurement_state"] == "MEASURED"
          and rows["P07"]["numerator"] == 0 and rows["P07"]["denominator"] == 3,
          f"三句零硬错应当测出 0/3，实为 {rows['P07']['numerator']}/{rows['P07']['denominator']}")
    check(rows["D01"]["measurement_state"] == "NOT_RUN_UPSTREAM",
          "没有开发批次时 D01 必须如实报未运行")
    check(rows["B01"]["measurement_state"] == "HUMAN_PENDING", "B01 必须停在待人工")
    check(rows["B13"]["measurement_state"] == "BENCHMARK_PENDING", "B13 必须停在待 Gold")

    # 硬错必须真的进分子。
    _make_run(work, "hard-error", sections=("company",))
    path = work / "evaluation" / "results" / "hard-error" / "company" / "sentence_checks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["hard_error_sentence_ids"] = ["s1"]
    payload["fact_safety_sentence_ids"] = ["s1"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rows = _rows_by_id(RunReader(repo_root=work, run_id="hard-error"))
    check(rows["P07"]["numerator"] == 1 and rows["P07"]["denominator"] == 2,
          f"一句硬错应当测出 1/2，实为 {rows['P07']['numerator']}/{rows['P07']['denominator']}")


# ============================================================ I. 交付物
def test_deliverables(work: Path) -> None:
    _make_run(work, "run-main", sections=("company", "financial"))
    _make_run(work, "run-failed", sections=("company",))
    out = work / "evaluation" / "rubric_runs" / "unit-test"
    samples = [Sample("run-main", "main", "正向"),
               Sample("run-failed", "failed", "反例")]
    outcome = write_deliverables(repo_root=work, out_dir=out, samples=samples, dev_batch=None)
    for name in ("rubric_catalog_snapshot.json", "run_evaluation.json",
                 "run_evaluation__failed.json", "evidence_index.json", "readback.md"):
        check(name in outcome["files"] and (out / name).is_file(), f"四件套缺 {name}")

    main_payload = json.loads((out / "run_evaluation.json").read_text(encoding="utf-8"))
    check(main_payload["metric_count"] == 24, "主样本结果不是 24 行")
    check(len({m["metric_id"] for m in main_payload["metrics"]}) == 24, "主样本结果有重复指标")
    check({m["metric_id"] for m in main_payload["metrics"]} == set(BY_ID), "主样本结果与目录不一致")
    failed_payload = json.loads((out / "run_evaluation__failed.json").read_text(encoding="utf-8"))
    check(failed_payload["sample"]["role"] == "failed", "失败样本必须单独成文件")
    check(failed_payload["metric_count"] == 24, "失败样本结果不是 24 行")
    for row in failed_payload["metrics"]:
        if row["measurement_state"] != "MEASURED":
            check(row["value"] is None and row["numerator"] is None,
                  f"失败样本的 {row['metric_id']} 未测成却带数值")

    readback = (out / "readback.md").read_text(encoding="utf-8")
    missing = [mid for mid in BY_ID if f"`{mid}`" not in readback]
    check(not missing, f"readback 没有覆盖这些指标：{missing}")
    check("不能由这份读数推出的结论" in readback, "readback 必须写明不能推出的结论")

    # 只创建不覆盖：同一个目录再写一次必须拒。
    check(_raises(lambda: write_deliverables(repo_root=work, out_dir=out, samples=samples,
                                             dev_batch=None),
                  FileExistsError),
          "已存在的评测输出目录必须被拒，不得覆盖历史读数")

    # 证据索引：每一条都要有哈希与存在位。
    evidence = json.loads((out / "evidence_index.json").read_text(encoding="utf-8"))
    for sample in evidence["samples"]:
        for ref in sample["refs"]:
            check("sha256" in ref and "exists" in ref and "field_path" in ref,
                  f"证据引用字段不全：{ref}")


# ============================================================ J. B04 的格值比较器
def test_cell_matcher() -> None:
    """B04 的 A1 格值比较器：无单位比率格值必须能匹配，但仍不许四舍五入。

    这条替一次**真发生过的假负**立案：原比较器只认 `(数字, 单位)` 元组，'1.57'、'3.26'、
    '1.4' 这类无单位比率格值永远匹配不上，A1 一度测出 68/84。那是读数实现缺陷，不是财务
    保真缺陷——差一点就被当成业务问题写进 readback。
    """
    match = M._cell_matches_fact
    check(match("1.57", "资产负债率 1.57"), "无单位比率格值必须能匹配")
    check(match("3.26", "流动比率 3.26 倍"), "无单位比率格值必须能匹配（事实侧带单位）")
    check(match("2.5 亿元", "营业收入 2.5 亿元"), "带单位格值必须能匹配")
    check(match("-9.94", "同比 -9.94"), "负数格值必须能匹配")
    check(not match("1.4", "资产负债率 1.40"), "不得接受四舍五入后相等（1.4 ≠ 1.40）")
    check(not match("2.5 亿元", "营业收入 250,000,000 元"), "单位不同不得自行换算")
    check(not match("", "营业收入 2.5 亿元"), "空格值不得匹配")
    check(not match("2.5 亿元", ""), "空命题不得匹配")
    check(not match("3.5 亿元", "营业收入 2.5 亿元"), "数值不同必须不匹配")


# ============================================================ J2. 证据索引自洽
def test_evidence_self_consistency(work: Path) -> None:
    """每条证据引用的 `exists`／`sha256` 必须与**那个路径上的真实字节**对得上。

    这条替一次真发生的断链立案：P01 取证时把已经补全的 `data/run_inputs/<run>/…` 又喂给了
    `ref(uploaded=True)`，前缀被加了两遍。「上传的原始字节」这条最要紧的证据因此全部
    `exists=False`——而 P01 的分子仍是 6/6（它自己按绝对路径重算），所以**读数看起来是对的**。
    证据链断了却不刷红，这正是指标要防的那类缺陷。
    """
    import hashlib

    run_id = "evidence-run"
    _make_run(work, run_id, sections=("company", "financial"), input_objects=(
        ("doc-a", "objects/aaa.pdf", "AAA"),
        ("doc-b", "objects/bbb.pdf", "BBB")))
    reader = RunReader(repo_root=work, run_id=run_id)
    evaluate_run(reader)
    refs = reader.evidence_index()
    check(len(refs) > 20, f"证据条数明显偏少：{len(refs)}")

    uploaded = [r for r in refs if r["path"].startswith("data/run_inputs/")]
    check(len(uploaded) >= 2, f"上传对象的取证没进索引：{len(uploaded)}")
    for ref in refs:
        path = work / ref["path"]
        check(not ref["path"].startswith(("data/data/", "evaluation/evaluation/")),
              f"证据路径被前缀了两次：{ref['path']}")
        check(ref["exists"] == path.is_file(),
              f"exists 与磁盘不符：{ref['path']} -> {ref['exists']}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        check(ref["sha256"] == actual,
              f"sha256 与磁盘不符：{ref['path']} -> {ref['sha256']}")
        if not ref["exists"]:
            check(ref["sha256"] is None, f"缺件不得带哈希：{ref['path']}")


# ============================================================ K. 开发批次的 stdout 纪律
def test_dev_batch_stdout_discipline() -> None:
    """开发测试模块自己往 stdout 打字的字节，绝不能污染批次台账那一份 JSON。

    这是一个**真发生过的**缺陷：`evals.test_m930_5_upload_run` 跑起来会往 stdout 打三行
    "demo scope profile=…"。混编跑时那三行跑到了台账 JSON 前面，调用方 `json.loads` 当场
    失败，**整批三个模块**被记成"未产出 JSON"，而它们其实全绿。
    """
    import contextlib
    import io
    import types

    from evaluation.rubric_eval.__main__ import _parse_batch_stdout
    from evaluation.rubric_eval.dev_batch import run_modules

    stub = types.ModuleType("rbeval_noisy_stub")

    def _noisy_main() -> dict:
        print("噪声行一：模块自己往 stdout 打字")
        print("噪声行二")
        return {"passed": 3, "failed": 0, "skipped": 0, "details": []}

    stub.main = _noisy_main  # type: ignore[attr-defined]
    sys.modules["rbeval_noisy_stub"] = stub
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            payload = run_modules(["rbeval_noisy_stub"])
    finally:
        sys.modules.pop("rbeval_noisy_stub", None)

    check(captured.getvalue() == "",
          f"模块自己的 stdout 漏进了批次台账的 stdout：{captured.getvalue()!r}")
    check(payload["total"] == {"passed": 3, "failed": 0, "skipped": 0},
          f"噪声不得影响汇总：{payload['total']}")
    check(payload["tests"][0]["crashed"] is False, "有噪声的模块不得因此被记成崩溃")
    check("噪声行一" in payload["tests"][0]["output_tail"],
          "模块自己的输出必须留在 output_tail 里当证据，而不是被丢掉")

    # 兜底解析：stdout 前置噪声时仍要能取到台账；纯垃圾则如实回 None。
    salvaged = _parse_batch_stdout('噪声\n更多噪声\n{\n "a": 1\n}\n')
    check(salvaged == {"a": 1}, f"前置噪声时兜底解析失败：{salvaged}")
    check(_parse_batch_stdout("这不是 JSON") is None, "纯垃圾必须回 None，不得猜出一个台账")

    # 模块抛 SystemExit（最阴的一种）也必须被接住并写进台账。这段替身源码**用字符串 exec**
    # 构造，不写成模块里的真实语句：套件形状回归会（正确地）把守卫外的 SystemExit 判成缺陷，
    # 而它走的是 AST，分不出「真的退出调用」与「这里只是被测夹具」。
    bad = types.ModuleType("rbeval_exiting_stub")
    exec("def main():\n    raise SystemExit(0)\n", bad.__dict__)  # noqa: S102
    sys.modules["rbeval_exiting_stub"] = bad
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            payload = run_modules(["rbeval_exiting_stub"])
    finally:
        sys.modules.pop("rbeval_exiting_stub", None)
    check(payload["tests"][0]["crashed"] is True
          and payload["tests"][0]["failed"] == 1
          and "SystemExit" in payload["tests"][0]["error"],
          "导入期 SystemExit 必须被接住并点名为崩溃")


# ============================================================ L. 缺陷台账必须指向真实测试
def test_defect_ledger() -> None:
    """D03 的台账条目必须逐条指向**真实存在**的测试方法，否则它会随重构悄悄变成空话。"""
    import importlib

    from evaluation.rubric_eval.dev_batch import DEFECTS

    check(bool(DEFECTS), "本批缺陷台账不得为空（这批确实修了三条）")
    for defect in DEFECTS:
        for field in ("id", "title", "detail", "counter_example", "positive_case"):
            check(bool(defect.get(field)), f"缺陷 {defect.get('id')} 缺字段 {field}")
        for role in ("counter_example", "positive_case"):
            target = defect[role]
            module_name, _, func_name = target.partition("::")
            check(bool(func_name), f"{target} 必须写成 module::test 的形式")
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:  # noqa: PERF203
                check(False, f"缺陷 {defect['id']} 指向的模块不存在：{exc}")
                continue
            check(hasattr(module, func_name),
                  f"缺陷 {defect['id']} 的 {role} 指向不存在的测试：{target}")

    # 台账要真的进 dev_batch 的输出，否则 D03 永远只有 0/0。这里**不能**拿本模块自己当样本
    # 去调 `run_modules`——那会把 `main()` 递归再跑一遍。
    import contextlib
    import io as _io
    import types

    from evaluation.rubric_eval.dev_batch import run_modules
    from evaluation.rubric_eval.reader import RunReader

    stub = types.ModuleType("rbeval_ledger_stub")
    stub.main = lambda: {"passed": 1, "failed": 0, "skipped": 0, "details": []}  # type: ignore[attr-defined]
    sys.modules["rbeval_ledger_stub"] = stub
    try:
        with contextlib.redirect_stdout(_io.StringIO()):
            payload = run_modules(["rbeval_ledger_stub"])
    finally:
        sys.modules.pop("rbeval_ledger_stub", None)
    check(len(payload["defects"]) == len(DEFECTS), "dev_batch 输出必须带上缺陷台账")

    dev = {"tests": payload["tests"], "defects": payload["defects"],
           "path": "x", "sha256": "y", "full_suite_run": False, "mock_llm": True}
    row = {r.metric_id: r.to_row() for r in evaluate_run(
        RunReader(repo_root=Path("."), run_id="ledger-probe", dev_batch=dev))}["D03"]
    check(row["measurement_state"] == "MEASURED" and row["numerator"] == len(DEFECTS)
          and row["denominator"] == len(DEFECTS),
          f"D03 应当测出 {len(DEFECTS)}/{len(DEFECTS)}，实为 "
          f"{row['numerator']}/{row['denominator']}（{row['measurement_state']}）")

    # 台账里少一条反例 ⇒ 该条必须掉出分子（不能靠「写了就算闭环」）。
    holed = [dict(d) for d in payload["defects"]]
    holed[0]["counter_example"] = ""
    dev2 = dict(dev, defects=holed)
    row2 = {r.metric_id: r.to_row() for r in evaluate_run(
        RunReader(repo_root=Path("."), run_id="ledger-probe", dev_batch=dev2))}["D03"]
    check(row2["numerator"] == len(DEFECTS) - 1 and row2["denominator"] == len(DEFECTS),
          f"缺反例的缺陷不得计入闭环：{row2['numerator']}/{row2['denominator']}")

    # 没有台账的批次必须如实报未运行，不得回落到 0/0 冒充「没有缺陷」。
    row3 = {r.metric_id: r.to_row() for r in evaluate_run(
        RunReader(repo_root=Path("."), run_id="ledger-probe", dev_batch=None))}["D03"]
    check(row3["measurement_state"] == "NOT_RUN_UPSTREAM" and row3["value"] is None,
          f"没有开发批次时 D03 必须报未运行，实为 {row3['measurement_state']}")


def main() -> dict:
    with tempfile.TemporaryDirectory(prefix="rbeval-test-") as tmp:
        work = Path(tmp)
        test_catalog()
        test_shape_discipline()
        test_missing_files(work)
        test_wrong_hash(work)
        test_cross_run_splicing(work)
        test_upstream_failure(work)
        test_offline_echo_is_not_review(work)
        test_healthy_run(work)
        test_deliverables(work)
    test_evidence_self_consistency(work)
    test_cell_matcher()
    test_dev_batch_stdout_discipline()
    test_defect_ledger()
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(1 if _results["failed"] else 0)

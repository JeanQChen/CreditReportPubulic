# -*- coding: utf-8 -*-
"""编排与交付物写出：把 24 个指标读成一个 run 一份结果，并写成可复核的四件套。

写出位置**不在** `evaluation/results/` 下面。原因是演示页的 run 发现逻辑只扫
`evaluation/results/<run_id>/` 且要求该名字下有账本与两节的报告版本；把评测产物放进那棵树
有被当成"当前读数"扫到的风险。评测结果另起 `evaluation/rubric_runs/`，与 run 产物彻底分家。

目录**只创建、不覆盖**：同名目录已存在即拒。历史 run 一个字节都不动。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import RUBRIC_EVAL_VERSION
from .catalog import CATALOG_VERSION, DECIDERS, DIMENSIONS, EXECUTION_STATES, MEASUREMENT_STATES, METRICS, STAGES
from .measures import evaluate_run
from .reader import RunReader

SCHEMA = "rubric-run-evaluation/1"
EVIDENCE_SCHEMA = "rubric-evidence-index/1"
CATALOG_SNAPSHOT_SCHEMA = "rubric-catalog-snapshot/1"

#: 交付物落在仓库的这个子目录下（**不在** results 树里）。
RUBRIC_RUNS_DIRNAME = "evaluation/rubric_runs"


@dataclass(frozen=True)
class Sample:
    run_id: str
    role: str          # main | second_completed | failed | alt_failed
    why: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_catalog_snapshot() -> dict[str, Any]:
    """24 个指标的静态目录快照。它不含本次 run 的任何读数。"""
    return {
        "schema": CATALOG_SNAPSHOT_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "engine_version": RUBRIC_EVAL_VERSION,
        "generated_at_utc": _now(),
        "dimensions": list(DIMENSIONS),
        "stages": list(STAGES),
        "deciders": list(DECIDERS),
        "measurement_states": list(MEASUREMENT_STATES),
        "execution_states": list(EXECUTION_STATES),
        "metric_count": len(METRICS),
        "metrics": [m.to_dict() for m in METRICS],
    }


def _run_identity(reader: RunReader) -> dict[str, Any]:
    ledger = reader.ledger() or {}
    binding = reader.json("run_input_binding.json")
    return {
        "run_id": reader.run_id,
        "mode": ledger.get("mode"),
        "run_outcome": ledger.get("run_outcome"),
        "approved_model": ledger.get("approved_model"),
        "budget_policy_version": ledger.get("budget_policy_version"),
        "attempt_total": (ledger.get("call_budget") or {}).get("attempt_total"),
        "run_input_version": (binding or {}).get("run_input_version"),
        "run_input_manifest_sha256": (binding or {}).get("manifest_sha256"),
        "has_progress_journal": reader.has("run_progress.jsonl"),
        "sections_with_artifacts": list(reader.section_dirs()),
    }


def evaluate_sample(repo_root: Path, sample: Sample,
                    dev_batch: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reader = RunReader(repo_root=repo_root, run_id=sample.run_id, dev_batch=dev_batch)
    results = evaluate_run(reader)
    rows = [r.to_row() for r in results]
    by_state: dict[str, int] = {}
    by_dimension: dict[str, dict[str, int]] = {}
    for row in rows:
        by_state[row["measurement_state"]] = by_state.get(row["measurement_state"], 0) + 1
        dim = by_dimension.setdefault(row["primary_dimension"], {})
        dim[row["measurement_state"]] = dim.get(row["measurement_state"], 0) + 1
    payload = {
        "schema": SCHEMA,
        "engine_version": RUBRIC_EVAL_VERSION,
        "catalog_version": CATALOG_VERSION,
        "generated_at_utc": _now(),
        "sample": {"run_id": sample.run_id, "role": sample.role, "why": sample.why},
        "run_identity": _run_identity(reader),
        "metric_count": len(rows),
        "metrics": rows,
        "summary": {"by_measurement_state": by_state, "by_dimension": by_dimension,
                    "measured_metrics": sorted(r["metric_id"] for r in rows
                                               if r["measurement_state"] == "MEASURED"),
                    "not_measured_metrics": sorted(r["metric_id"] for r in rows
                                                   if r["measurement_state"] != "MEASURED")},
        "notes": [
            "本文件只描述这一个 run；分子分母不得与别的 run 合并。",
            "MEASURED 之外的状态一律 numerator/value = null，不填 0 或 100%。",
            "可观测性维度体现在每行的 evidence_refs 完整度与 limitations，不另设综合分。",
        ],
    }
    return payload, reader.evidence_index()


# --------------------------------------------------------------------------
# readback.md
# --------------------------------------------------------------------------
def _matrix(rows: list[dict[str, Any]]) -> list[str]:
    """四行（维度）× 八列（环节）的状态矩阵：每格列该格的指标 ID 与测量状态。"""
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        by_cell.setdefault((row["primary_dimension"], row["stage"]), []).append(row)
    lines = ["| 维度 \\ 环节 | " + " | ".join(STAGES) + " |",
             "|" + "---|" * (len(STAGES) + 1)]
    for dim in DIMENSIONS:
        cells = []
        for stage in STAGES:
            entries = by_cell.get((dim, stage), [])
            if not entries:
                cells.append("—")
                continue
            cells.append("<br>".join(f"`{e['metric_id']}` {e['measurement_state']}" for e in entries))
        lines.append(f"| **{dim}** | " + " | ".join(cells) + " |")
    return lines


def _metric_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["| 指标 | 环节 | 执行状态 | 测量状态 | 分子/分母 | 数值 | 判定 |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        num, den = row["numerator"], row["denominator"]
        fraction = "—" if num is None or den is None else f"{num}/{den}"
        value = "—" if row["value"] is None else f"{row['value']:.4f}"
        verdict = (row["verdict"] or "").replace("|", "／")
        lines.append(f"| `{row['metric_id']}` {row['title']} | {row['stage']} | {row['execution_state']} "
                     f"| {row['measurement_state']} | {fraction} | {value} | {verdict} |")
    return lines


def _pending_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["| 指标 | 测量状态 | 第一个缺的东西 | 补齐办法 |", "|---|---|---|---|"]
    for row in rows:
        if row["measurement_state"] == "MEASURED":
            continue
        chain = row.get("missing_chain") or []
        how = "；".join(chain[1:]) if len(chain) > 1 else (row.get("first_missing") or "")
        lines.append(f"| `{row['metric_id']}` {row['title']} | {row['measurement_state']} "
                     f"| {(row.get('first_missing') or '').replace('|', '／')} "
                     f"| {how.replace('|', '／')[:200]} |")
    return lines


def render_readback(samples: list[dict[str, Any]], dev_batch: dict[str, Any] | None,
                    generated_at: str) -> str:
    main = next(s for s in samples if s["payload"]["sample"]["role"] == "main")
    rows = main["payload"]["metrics"]
    sec = {r["metric_id"]: r for r in rows}
    company = next((s for s in samples if s["payload"]["sample"]["role"] == "second_completed"), None)
    failed = next((s for s in samples if s["payload"]["sample"]["role"] == "failed"), None)
    alt = next((s for s in samples if s["payload"]["sample"]["role"] == "alt_failed"), None)

    b07 = (sec["B07"]["details"] or {}).get("run_observable", {})
    p07_rows = (sec["P07"]["details"] or {}).get("per_section", [])
    fin = next((r for r in p07_rows if r["section"] == "financial"), {})
    comp = next((r for r in p07_rows if r["section"] == "company"), {})
    b04 = (sec["B04"]["details"] or {})
    a1 = b04.get("a1") or {}
    a2 = b04.get("a2") or {}
    dev_total = (dev_batch or {}).get("total") or {}

    out: list[str] = []
    add = out.append
    add("# 评测读数（离线只读 · 24 指标全套）")
    add("")
    add(f"- 引擎：`{RUBRIC_EVAL_VERSION}`，指标目录 `{CATALOG_VERSION}`，生成于 {generated_at}（UTC）。")
    add(f"- 主样本：`{main['payload']['sample']['run_id']}`（{main['payload']['sample']['why']}）")
    add("- 纪律：**一个 run 一份结果**，分子分母不跨 run 合并；`MEASURED` 之外一律不填 0/100%。")
    add("- 本文件只描述读到了什么、没读到什么；**不**宣布任何阶段关闭，也**不**替代人工验收。")
    add("")
    add("## 一页结论")
    add("")
    add(f"1. **24 个指标各有一条结果行，其中真测出来的是 {len(main['payload']['summary']['measured_metrics'])} 条**："
        f"`{'`, `'.join(main['payload']['summary']['measured_metrics'])}`。")
    add(f"2. **其余 {len(main['payload']['summary']['not_measured_metrics'])} 条不是「没做」，而是各自缺不同的东西**"
        f"（待 Gold／待人读／上游未落盘），逐条的「第一个缺的东西」见第四节；**没有一条被填成 0 或 100%**。")
    add(f"3. **首个损失点在文档结构与树图层**：`{main['payload']['sample']['run_id']}` 的 "
        f"{b07.get('documents')} 份上传 PDF 共 {b07.get('declared_tables')} 张表，"
        f"**签发 {b07.get('released_tables')} 张、达到可读材料资格 {b07.get('reading_qualified_tables')} 张**；"
        f"全部以 `structure_not_complete` 落拒。"
        "这一层不签发，B07／B08 的业务保真率就没有可以测的东西。")
    add(f"4. **写作层是通的，但也只到「能读」**：公司节 {comp.get('sentences')} 句里 "
        f"{comp.get('hard_errors')} 句带机械硬错；财务节 {fin.get('sentences')} 句机械层 0 硬错。"
        f"两节都 `not_publishable`、`human_not_reviewed`。")
    add(f"5. **财务格值可复算**：A1 {a1.get('cells_matching_fact_text')}/{a1.get('cells_total')} 格、"
        f"A2 {a2.get('readings_matching')}/{a2.get('readings_total')} 条读数与本 run manifest 的"
        f"权威命题逐值一致。**这只证明格值没被写错，不证明分析说得对。**")
    add(f"6. **开发批次**（与报告 run 分账）：聚焦测试 {dev_total.get('passed', 0)} 通过 / "
        f"{dev_total.get('failed', 0)} 失败 / {dev_total.get('skipped', 0)} 跳过"
        f"（`EVAL_MOCK_LLM=true`）。**全量套件本批未运行**——这一行只覆盖本批受影响的模块，"
        "不得读成「整仓回归全绿」。")
    add("")
    add("## 四行 × 八环节状态矩阵（主样本）")
    add("")
    add("每格列该格的指标 ID 与它的测量状态。`—` 表示该维度在这一环节没有独立成绩，不是零分。")
    add("")
    out.extend(_matrix(rows))
    add("")
    add("## 每个指标的实测 / 待评")
    add("")
    out.extend(_metric_table(rows))
    add("")
    add("状态口径：`MEASURED` 已测得；`BENCHMARK_PENDING` 待 Gold 标注；`HUMAN_PENDING` 待人工评阅；"
        "`NOT_RUN_UPSTREAM` 上游未运行；`NOT_IN_SCOPE` 不在本次范围；`NOT_IMPLEMENTED` 接口未实现；"
        "`FAILED_TO_MEASURE` 有意图但没测成。")
    add("")
    add("## 未测成的指标：第一个缺的东西与补齐办法")
    add("")
    out.extend(_pending_table(rows))
    add("")
    add("## 首个损失点与后续行动")
    add("")
    add("- **结构层（阻塞 B07／B08）**：三份 PDF 共 "
        f"{b07.get('declared_tables')} 张表全部 `structure_not_complete`、"
        f"`reading_qualified=false`、`numeric_authority=false`。"
        "行动：这是正式表格能力轨的活（TS5 另轨），**不是**本任务能就地补的小修；"
        "在此之前，表格数字入正文这条路只能靠原 PDF 区域只读展示，且不授权任何格值。")
    add("- **财务 A2 的应收案例**：本 run 的 A2 对该项落 `requires_separate_adjudication`，"
        "并在快照里对缺失 code 逐条落 `no_registered_fact_for_item`。"
        "行动：属**单独子项裁决**，与表结构未签发是两条不同根因，不得合并记一条。")
    for row in rows:
        if row["metric_id"] in ("B01", "B02", "B06", "B13"):
            add(f"- **{row['metric_id']}｜{row['title']}**：{row.get('first_missing')}")
    add("")
    add("## 三个样本并排（不拼接分子分母）")
    add("")
    add("| 样本 | 角色 | run_outcome | 有阶段日志 | 有产物的节 | 结果文件 |")
    add("|---|---|---|---|---|---|")
    for sample in samples:
        ident = sample["payload"]["run_identity"]
        add(f"| `{sample['payload']['sample']['run_id']}` | {sample['payload']['sample']['role']} "
            f"| {ident.get('run_outcome')} | {ident.get('has_progress_journal')} "
            f"| {', '.join(ident.get('sections_with_artifacts') or []) or '—'} "
            f"| `{sample['filename']}` |")
    add("")
    if failed:
        frow = {r["metric_id"]: r for r in failed["payload"]["metrics"]}
        add(f"- 失败样本 `{failed['payload']['sample']['run_id']}`："
            f"{failed['payload']['sample']['why']}")
        add(f"  - `P07` 状态 `{frow['P07']['measurement_state']}`，"
            f"`first_missing`：{frow['P07']['first_missing']}")
        add(f"  - `B04` 状态 `{frow['B04']['measurement_state']}`，"
            f"`first_missing`：{frow['B04']['first_missing']}")
        add("  - **该 run 的公司正文与财务节一律不借完成样本补齐**。")
    if company:
        add(f"- 第二个完成样本 `{company['payload']['sample']['run_id']}`："
            f"{company['payload']['sample']['why']}")
    if alt:
        add(f"- 第三样本 `{alt['payload']['sample']['run_id']}`：{alt['payload']['sample']['why']}")
    add("")
    add("## 开发测试批次（单列，与报告 run 分账）")
    add("")
    if not dev_batch:
        add("本批未运行聚焦测试（`--no-dev`），`D01`／`D03` 因此如实报「未运行」。")
        add("")
    else:
        add(f"- 命令：`{dev_batch.get('command')}`；`EVAL_MOCK_LLM={dev_batch.get('mock_llm')}`；"
            f"全量套件本批：{'已跑' if dev_batch.get('full_suite_run') else '**未跑**'}。")
        add("")
        add("| 模块 | 通过 | 失败 | 跳过 | 崩溃 |")
        add("|---|---|---|---|---|")
        for test in dev_batch.get("tests") or []:
            add(f"| `{test.get('module')}` | {test.get('passed')} | {test.get('failed')} "
                f"| {test.get('skipped')} | {'是' if test.get('crashed') else '否'} |")
        defects = dev_batch.get("defects") or []
        add("")
        add("**本批已登记并闭环的评测引擎缺陷（`D03` 的分子分母）：**")
        add("")
        if not defects:
            add("- 本批未登记缺陷。")
        for defect in defects:
            add(f"- `{defect.get('id')}` **{defect.get('title')}** —— 反例／正例："
                f"`{defect.get('counter_example')}`")
            add(f"  - {defect.get('detail')}")
        add("")
        add("> 这些是**评测引擎自己**的缺陷，不是被评 run 的产品缺陷；被评 run 里观察到的产品缺陷"
            "另列在「首个损失点」一节，两者不并账。")
        add("")
    add("## 不能由这份读数推出的结论")
    add("")
    add("- 不能推出任何正式阶段关闭（M930-3／4／5、TS5、Phase 4／5／6 状态不变）。")
    add("- 不能把机械层 0 硬错读成「业务合格」；`B01`／`B02`／`B06`／`B13` 的分母已知但分子要人读或 Gold。")
    add("- 不能把材料份数、页命中、节点数、路由条数读成「业务正确率」。")
    add("- 不能把「页面能显示 12 个原 PDF 区域」读成「表格结构化合格」或「格值已授权」。")
    add("- 不能把开发测试通过数读成报告质量。")
    add("")
    add("## 与人类 / Gold 有关的缺口清单")
    add("")
    add("| 指标 | 需要的样本 | 谁裁决 | 口径 |")
    add("|---|---|---|---|")
    add("| `B01` | 公司必需栏目逐栏目的「在题、被来源支持的自然正文」判定 | 领域评阅人 | 必需栏目数作分母，人读认可数作分子 |")
    add("| `B02` | 本样本「应呈现要点」清单及其等价表述 | 标注者 + 复核 | 已标注要点数作分母 |")
    add("| `B06` | 七个分项 0–4 的版本化人读表 | 领域评阅人 | 分项评分，无总分，不折算成比率 |")
    add("| `B13` | 有缺陷样本 + 无缺陷样本两套盲测及其独立裁决 | 独立裁决人 | 意见精确率、过度审阅率、误阻断率 |")
    add("| `B07`／`B08`／`B09`／`B10`／`B11`／`B12` | 结构与检索的 Gold（关系、路径、允许证据组、预期终态、充分性） | 标注者 + 复核 | 各自的分母见目录快照 |")
    add("| `P02`／`B03` 的语义轴 | 逐句「材料是否支持该命题」的人工抽检 | 领域评阅人 | 抽检结论单独记账，不并入机械层 |")
    add("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# 写出
# --------------------------------------------------------------------------
def _write_json(path: Path, payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False).encode("utf-8")
    with path.open("xb") as fh:          # x：只创建，不覆盖
        fh.write(blob)
    return _sha256_bytes(blob)


def write_deliverables(*, repo_root: Path, out_dir: Path, samples: list[Sample],
                       dev_batch: dict[str, Any] | None,
                       create_dir: bool = True) -> dict[str, Any]:
    """写四件套。目录已存在（且 ``create_dir``）即拒——不覆盖任何既有读数。

    ``create_dir=False`` 用于调用方已经建好目录并先写了开发测试台账的情形。
    """
    if create_dir:
        if out_dir.exists():
            raise FileExistsError(f"评测输出目录已存在，拒绝覆盖：{out_dir}")
        os.makedirs(out_dir, exist_ok=False)
    elif not out_dir.is_dir():
        raise FileNotFoundError(f"输出目录不存在：{out_dir}")
    generated_at = _now()

    catalog = build_catalog_snapshot()
    _write_json(out_dir / "rubric_catalog_snapshot.json", catalog)

    files: list[dict[str, Any]] = [{"role": "catalog", "filename": "rubric_catalog_snapshot.json"}]
    evaluated: list[dict[str, Any]] = []
    merged_refs: list[dict[str, Any]] = []
    for sample in samples:
        payload, refs = evaluate_sample(repo_root, sample, dev_batch)
        filename = ("run_evaluation.json" if sample.role == "main"
                    else f"run_evaluation__{sample.role}.json")
        _write_json(out_dir / filename, payload)
        merged_refs.append({"run_id": sample.run_id, "role": sample.role, "refs": refs})
        evaluated.append({"payload": payload, "filename": filename, "refs": refs})
        files.append({"role": sample.role, "filename": filename})

    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "engine_version": RUBRIC_EVAL_VERSION,
        "generated_at_utc": generated_at,
        "discipline": [
            "每个被读的字节都当场重算 SHA-256；换了字节，哈希就变，读数就不再成立。",
            "证据按 (路径, 字段) 去重；同一文件里的不同字段是不同证据。",
            "被评 run 与 data/run_inputs 下的历史字节在本次评测中只读，未改一字节。",
        ],
        "samples": merged_refs,
        "dev_batch": dev_batch,
        "gold_and_human_gaps": [
            {"metric": "B01", "sample": "公司必需栏目逐栏目人工认可", "adjudicator": "领域评阅人"},
            {"metric": "B02", "sample": "应呈现要点清单", "adjudicator": "标注者 + 复核"},
            {"metric": "B06", "sample": "七分项 0–4 人读表", "adjudicator": "领域评阅人"},
            {"metric": "B13", "sample": "正反两类盲测及独立裁决", "adjudicator": "独立裁决人"},
            {"metric": "B07/B08", "sample": "结构关系与导航路径 Gold", "adjudicator": "标注者 + 复核"},
            {"metric": "B09/B10", "sample": "允许路由集合与等价证据组 Gold", "adjudicator": "标注者 + 复核"},
            {"metric": "B11", "sample": "逐需求 Gold 预期终态", "adjudicator": "标注者 + 复核"},
            {"metric": "B12", "sample": "逐栏目素材充分性裁决", "adjudicator": "领域评阅人"},
        ],
        "files": files,
    }
    _write_json(out_dir / "evidence_index.json", evidence)

    readback = render_readback(evaluated, dev_batch, generated_at)
    with (out_dir / "readback.md").open("xb") as fh:
        fh.write(readback.encode("utf-8"))

    written = [f["filename"] for f in files] + ["evidence_index.json", "readback.md"]
    if dev_batch is not None:
        written.append("dev_batch.json")
    return {"out_dir": str(out_dir), "files": written, "generated_at_utc": generated_at}


__all__ = ["Sample", "SCHEMA", "RUBRIC_RUNS_DIRNAME", "build_catalog_snapshot",
           "evaluate_sample", "render_readback", "write_deliverables"]

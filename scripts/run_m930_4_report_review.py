"""报告级审阅的**唯一真实运行入口**（缺省不发任何请求，写盘一律 create-only）。

用法::

    # 缺省 = 干跑：只算分批计划与所需上限，一次请求都不发、一个文件都不写
    python -X utf8 -m scripts.run_m930_4_report_review \
      --inputs evaluation/results/m930_4_cited_v2_r4_reportreview/report_review_inputs.json

    # 真实调用（**逐次单独授权**）：必须同时给出批准出处与输出目录
    python -X utf8 -m scripts.run_m930_4_report_review \
      --inputs <...>/report_review_inputs.json \
      --output evaluation/results/m930_4_report_review_v2_r1 \
      --approved --provenance "用户 2026-10-XX 授权：<逐 scope 上限>"

本命令做四件事，顺序不能换：

1. **装配**：从 `report_review_inputs.json` 逐字段复验三块输入（`ReportReviewScope.from_dict`），
   读不回来即停——输入面读不全就发请求，等于把审阅绑在一份来路不明的材料上；
2. **算上界**：在这份输入上真的切一遍批（`plan_report_review_batches`），得到逐 scope 的
   结构上界。**逐 scope 的调用上限就是这里的批数**，不是拍出来的；
3. **装门**：`LLMCallBudget(policy, enforce=True)` 在**第一请求之前**构造。未授权时它在
   构造期就 `ValueError`，所以"先花掉一部分额度、再撞在未批准的那次上"在结构上不可能。
   `assert_budget_covers_bounds` 再核对已批准上限盖得住上界——上限低于上界不是"更严格"，
   而是把一次注定中途撞门的运行伪装成可运行；
4. **逐 scope 跑**：任一批调用或解析失败即 `ReportReviewRunAborted`，把它**如实落成**
   `ReportReviewRun(outcome="failed")`（已完成的批次带着记录与意见一起留存，未覆盖的单元
   逐条记进 `excluded_unit_ids`），继续跑完其余 scope，最后返回非零。**不重试、不吞掉
   已完成批次、不把半截回复当意见。**

真实客户端**显式关闭推理**（`thinking={"type": "disabled"}`，见 `REPORT_REVIEW_THINKING_DISABLED`）：
这是「结构化意见 JSON」这一类输出，推理一旦把 8192 的输出额度吃光，正文一个字都还没开始写。
开关**只在这条入口**传；`llm.client` 的全局缺省仍是 `None`（不传、由 provider 默认），
本批也不动输出上限、模型与分批规则。

产出者身份由 client 本身推出（`report_review_producer_kind_of`），本命令**没有**声明它的参数：
离线替身跑出来的意见只会被标成 `offline_diagnostic_echo`，不会冒充独立审阅。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import config
from assurance import report_review as RR
from assurance import report_reviewer as RX
from llm import budget as LB


#: 建议的新 run-id 只是个**提案**；真实调用须逐次单独授权，本命令不替调用方创建它。
PROPOSED_REPORT_REVIEW_RUN_ID = "m930_4_report_review_v2_r1"

#: 本入口**显式**关闭推理，只写在这里、只给这条链用（`scripts/run_m930_3_cited_chain.py`
#: 的 `CITED_THINKING_DISABLED` 同理）。项目模型是推理模型，推理内容计入 `output_tokens`；
#: 本链要的是「一份结构化意见 JSON」这一类输出——推理一旦把 8192 的输出额度吃光，正文
#: 一个字符都还没开始写，产物上却只表现为「这次调用失败了」。不传开关（`thinking=None`）
#: 等于把这件事交给 provider 默认值：同一个模型、同一个 8192 上限，已经在 M930-3 上真出过
#: 「额度用尽但零可见正文」。**不改 `llm.client` 的全局缺省**：那会一次性改掉全仓每个调用点
#: 的行为，而它们各自的短输出处置方式并不相同。本批也不动输出上限、模型与分批规则。
REPORT_REVIEW_THINKING_DISABLED: dict = {"type": "disabled"}


class ReportReviewEntryError(RuntimeError):
    """入口自身的事前拒绝（输入读不回、上限盖不住上界、输出目录已存在）。"""


def load_scopes(inputs_path: Path) -> tuple[RR.ReportReviewScope, ...]:
    """读回三块输入并逐字段复验身份。字段集/指纹/内容 id 任一不符即抛。"""
    try:
        data = json.loads(Path(inputs_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReportReviewEntryError(f"无法读取审阅输入 {inputs_path}：{exc}") from exc
    raw = data.get("report_review_inputs") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ReportReviewEntryError(
            f"{inputs_path} 里没有 `report_review_inputs` 列表：没有输入就不得发请求")
    scopes = tuple(RR.ReportReviewScope.from_dict(item) for item in raw)
    kinds = sorted(scope.scope_kind for scope in scopes)
    expected = sorted(RR.REPORT_REVIEW_SCOPE_KINDS)
    if kinds != expected:
        raise ReportReviewEntryError(
            f"输入面只给出 {kinds}，本批应为 {expected}："
            "少跑一块内容 = 把「没审过的内容」留在报告里，fail-closed")
    if len({scope.bundle.report_id for scope in scopes}) != 1:
        raise ReportReviewEntryError("三块输入不属于同一份报告：不得合并成一次审阅")
    return scopes


def plan(scopes: Sequence[RR.ReportReviewScope]) -> dict[str, dict[str, Any]]:
    """逐 scope 的结构上界与逐批明细。**不发请求**，也不写盘。"""
    out: dict[str, dict[str, Any]] = {}
    for scope in scopes:
        batches = RX.plan_report_review_batches(scope)
        budget = RX.report_review_structural_bound(scope)
        out[scope.scope_kind] = {
            "scope_id": scope.scope_id, "scope_version": scope.scope_version,
            "bundle_id": scope.bundle.bundle_id, "bounds": budget,
            "batches": [batch.summarize() for batch in batches]}
    return out


def build_gate(*, scopes: Sequence[RR.ReportReviewScope], model: str, approved: bool,
               provenance: str) -> tuple[LB.CallBudgetPolicy, LB.LLMCallBudget]:
    """构造**已批准**的预算政策并装成强制门。未授权时在这里就停，不是等到请求期。"""
    bounds = {scope.scope_kind: RX.report_review_structural_bound(scope) for scope in scopes}
    if approved and not str(provenance).strip():
        raise ReportReviewEntryError(
            "`--approved` 必须同时给出 `--provenance`：批准出处要逐字写进 `AxisCap.basis`，"
            "读产物的人不该去翻对话记录")
    policy = RX.report_review_call_budget_policy(
        bounds=bounds, model=model, approved=approved, provenance=provenance or "未授权")
    RX.assert_budget_covers_bounds(policy, bounds)
    try:
        return policy, LB.LLMCallBudget(policy, enforce=True)
    except ValueError as exc:
        raise ReportReviewEntryError(
            f"以强制模式安装预算门被拒（未获批准就不发请求）：{exc}") from exc


def run_scopes(*, scopes: Sequence[RR.ReportReviewScope], client: Any,
               model_policy: str, out_dir: Path,
               budget: LB.LLMCallBudget | None) -> dict[str, Any]:
    """逐 scope 跑链并**逐个**落盘。任一批失败不中断其余 scope，但整体判失败。

    「继续跑完其余 scope」是刻意的：每个 scope 有自己的独立上限，一次失败不构成
    "其余内容也不必审"的理由；而"中断"会让未跑的 scope 连一条 `failed` 记录都没有，
    读者从产物上根本看不出它有没有被尝试过。
    """
    results: list[dict[str, Any]] = []
    recorded_calls = 0
    for scope in scopes:
        scope_scope = RX.REPORT_REVIEW_SCOPE_PREFIX + scope.scope_kind
        try:
            with LB.section_scope(scope_scope):
                run = RX.run_report_review(scope=scope, client=client,
                                           model_policy=model_policy)
        except RX.ReportReviewRunAborted as exc:
            run = RX.aborted_report_review_run(scope=scope, aborted=exc, model_policy=model_policy)
            failure = {"reason": exc.reason, "message": str(exc),
                       "attempted_batch_ids": list(exc.attempted_batch_ids),
                       "completed_batch_ids": list(exc.completed_batch_ids)}
        else:
            failure = None
        recorded_calls += sum(record.llm_call_count for record in run.records)
        path = RX.write_report_review_run(out_dir, run)
        results.append({
            "scope_kind": scope.scope_kind, "run_id": run.run_id,
            "outcome": run.outcome, "path": path.name,
            "producer_kind": run.producer_kind,
            "requested_batch_count": len(run.requested_batch_ids),
            "completed_batch_count": len(run.completed_batch_ids),
            "failed_batch_count": len(run.failed_batch_ids),
            "declared_unit_count": len(run.declared_unit_ids),
            "requested_unit_count": len(run.requested_unit_ids),
            "covered_unit_count": len(run.covered_unit_ids),
            "reported_unit_count": len(run.reported_unit_ids),
            "excluded_unit_count": len(run.excluded_unit_ids),
            "issue_count": len(run.issues),
            "failure": failure})
    ledger_attempts = 0 if budget is None else len(budget.attempts)
    return {"scopes": results, "recorded_call_count": recorded_calls,
            "ledger_attempt_count": ledger_attempts,
            # 两个数**分开写**，理由与 `cited_controller` 的 `new_llm_calls` /
            # `report_review_calls_recorded` 相同：离线替身不过预算门（这是 `llm.budget
            # .suspended` 的纪律——替身探针不占已批准额度），因此 `ledger_attempt_count`
            # 为 0 而 `recorded_call_count` 不为 0 **不是**记账缺失，而是"没有真实请求"。
            "call_count_meaning": (
                "ledger_attempt_count = 真的经过 `llm.budget` 门、发往 provider 的尝试数"
                "（离线替身不过门，恒为 0）；recorded_call_count = 产物里逐批记录声明的调用数"
                "（替身也记）。两者不相等**只**能读成『这一段没有真实请求』，不得读成"
                "『额度没花但账没记』。"),
            "ledger": (None if budget is None else budget.summary()),
            "all_reviewed": all(row["outcome"] == "reviewed" for row in results)}


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    parser = argparse.ArgumentParser(description="报告级审阅运行入口（缺省干跑）")
    parser.add_argument("--inputs", required=True, type=Path,
                        help="`report_review_inputs.json`（由 run_m930_4_assurance 写出）")
    parser.add_argument("--output", type=Path, default=None,
                        help="真实调用时的**新**目录（create-only）；干跑不需要")
    parser.add_argument("--model", default=config.LLM_MODEL,
                        help="获批模型；真实调用时写进预算政策并经 `approved_model` 校验")
    parser.add_argument("--approved", action="store_true",
                        help="声明已获真实调用授权；不给出处则拒绝")
    parser.add_argument("--provenance", default="",
                        help="批准出处，逐字写进 `AxisCap.basis`（随 `--approved` 必填）")
    parser.add_argument("--dry-run", action="store_true",
                        help="显式干跑（缺省行为）；与 `--approved` 互斥")
    args = parser.parse_args(argv)

    try:
        scopes = load_scopes(args.inputs.resolve(strict=True))
    except (ReportReviewEntryError, RR.ReportReviewError, OSError) as exc:
        parser.error(str(exc))
    plans = plan(scopes)
    required = "、".join(f"`report:{kind}` ≤ {row['bounds']['batches']}"
                         for kind, row in sorted(plans.items()))
    total = sum(row["bounds"]["batches"] for row in plans.values())

    if not args.approved:
        if args.output is not None:
            parser.error("干跑不接受 `--output`：干跑不写任何文件")
        print(json.dumps({
            "mode": "dry_run", "model": args.model,
            "proposed_run_id": PROPOSED_REPORT_REVIEW_RUN_ID,
            "required_caps": {kind: row["bounds"]["batches"]
                              for kind, row in sorted(plans.items())},
            "total_calls": total, "plans": plans, "llm_calls": 0,
        }, ensure_ascii=False, sort_keys=True, indent=2))
        print(f"\n干跑：未发出任何请求。逐 scope 上限 {required}；合计 {total} 次。", flush=True)
        return 0

    if args.dry_run:
        parser.error("`--dry-run` 与 `--approved` 互斥：二者不能同时声明")
    if args.output is None:
        parser.error("真实调用必须给出 `--output`（create-only 的新目录）")
    out_dir = args.output.resolve(strict=False)
    if out_dir.exists():
        parser.error(f"输出目录已存在（create-only）：{out_dir}")

    try:
        policy, budget = build_gate(scopes=scopes, model=args.model,
                                    approved=True, provenance=args.provenance)
    except (ReportReviewEntryError, RX.ReportReviewError, LB.LLMCallBudgetError) as exc:
        parser.error(str(exc))

    if client is None:
        client = RX.LlmReportReviewClient(
            model=args.model, thinking=REPORT_REVIEW_THINKING_DISABLED)

    out_dir.mkdir(parents=True, exist_ok=False)
    previous = LB.install(budget)
    try:
        report = run_scopes(scopes=scopes, client=client,
                            model_policy=policy.policy_version, out_dir=out_dir, budget=budget)
    finally:
        LB.uninstall()
        if previous is not None:
            LB.install(previous)

    summary = {
        "mode": "real", "model": args.model, "output": str(out_dir),
        "provenance": args.provenance,
        "budget_policy": policy.to_dict(),
        "producer_kind": RX.report_review_producer_kind_of(client),
        "required_caps": {kind: row["bounds"]["batches"]
                          for kind, row in sorted(plans.items())},
        "report": report}
    with (out_dir / "report_review_entry.json").open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    if not report["all_reviewed"]:
        print("\n有 scope **未完成**：失败批次与未覆盖单元已分别留账，本命令返回非零。", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

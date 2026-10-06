# -*- coding: utf-8 -*-
"""命令行入口：跑一整套离线评测并写出四件套。

    python -m evaluation.rubric_eval \
        --main m930_3_cited_upload_20261005T151012Z \
        --second m930_3_cited_upload_20261005T071143Z \
        --failed m930_3_cited_upload_20261005T131532Z \
        --alt-failed m930_3_cited_upload_20261005T161431Z

它**不**联网、**不**发请求、**不**建 run、**不**写 `data/` 下的库、**不**改历史 run。
默认还会跑一组聚焦测试，作为 D01／D03 的数据来源；用 `--no-dev` 可以跳过（那两行就会如实
报「未运行」）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .evaluate import RUBRIC_RUNS_DIRNAME, Sample, write_deliverables
from .reader import RunReader

#: 本批受影响的聚焦测试。它们与 runtime 无关，是可复算的离线断言。
DEFAULT_DEV_MODULES: tuple[str, ...] = (
    "evals.test_rubric_eval",
    "evals.test_eval_suite_shape",
    "evals.test_m930_5_upload_run",
)

#: 角色说明的模板。实际写进产物的会是「模板 + 本 run 的实测身份」，避免措辞与事实脱节。
_WHY = {
    "main": "评测主样本：同一 run_id 下的真实双节终局 run",
    "second_completed": "评测文档已引用的更早完成样例，仅用于并排核对",
    "failed": "失败样例：公司节写作返回未形成正式正文，财务节与下游审阅未运行",
    "alt_failed": "第三样例：两节正文都形成，但公司节独立审阅回复不外合约",
}


def _parse_batch_stdout(stdout: str) -> dict | None:
    """从子进程 stdout 里取出那份 JSON 台账。

    正常情况下 stdout 只有那一份 JSON。但测试模块里若有**绕过重定向**的写入（子进程、C 层
    输出），前面就会多出无关行。这里退回"从第一个顶格的 `{` 起"再解析一次，避免把"跑绿了
    但 stdout 有噪声"误报成"未产出 JSON"——那会把整批测试记成失败。
    """
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "{":
            try:
                return json.loads("\n".join(lines[index:]))
            except json.JSONDecodeError:
                continue
    return None


def _run_dev_batch(repo_root: Path, modules: list[str]) -> dict:
    """起子进程跑聚焦测试，把结果抄成 JSON。子进程失败不影响评测本体。"""
    cmd = [sys.executable, "-X", "utf8", "-m", "evaluation.rubric_eval.dev_batch", *modules]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    payload = _parse_batch_stdout(proc.stdout)
    if payload is None:
        payload = {"mock_llm": True, "command": " ".join(cmd),
                   "tests": [{"module": m, "passed": 0, "failed": 1, "skipped": 0,
                              "crashed": True,
                              "error": f"dev_batch 子进程未产出 JSON（退出码 {proc.returncode}）",
                              "details": [(proc.stderr or "")[-1200:]]} for m in modules],
                   "total": {"passed": 0, "failed": len(modules), "skipped": 0}}
    payload["full_suite_run"] = False
    payload["note"] = ("这是**聚焦批次**，不是全量套件；全量是否本批运行见 readback 的开发批次一节。")
    return payload


def _sample_with_identity(repo_root: Path, role: str, run_id: str,
                          dev_batch: dict | None) -> Sample:
    reader = RunReader(repo_root=repo_root, run_id=run_id, dev_batch=dev_batch)
    ledger = reader.ledger() or {}
    sections = reader.section_dirs()
    why = (f"{_WHY[role]}（实测 run_outcome={ledger.get('run_outcome')}，"
           f"mode={ledger.get('mode')}，有产物的节={list(sections) or '无'}）")
    return Sample(run_id=run_id, role=role, why=why)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.rubric_eval",
                                     description="离线只读评测：把 24 个指标落到已落盘的真实 run 上")
    parser.add_argument("--repo-root", default=".", help="仓库根（默认当前目录）")
    parser.add_argument("--out", default=None, help="输出目录；默认 evaluation/rubric_runs/<UTC 戳>_rubric_eval_v1")
    parser.add_argument("--main", required=True, help="主样本 run_id")
    parser.add_argument("--second", default=None, help="第二个完成样例 run_id")
    parser.add_argument("--failed", default=None, help="失败样例 run_id")
    parser.add_argument("--alt-failed", default=None, help="第三样例 run_id（可选）")
    parser.add_argument("--dev-module", action="append", default=None,
                        help="聚焦测试模块（可重复）；默认三个受影响模块")
    parser.add_argument("--no-dev", action="store_true", help="不跑聚焦测试（D01/D03 如实报未运行）")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else repo_root / RUBRIC_RUNS_DIRNAME / f"{stamp}_rubric_eval_v1"
    out_dir = out_dir.resolve()

    results_root = (repo_root / "evaluation" / "results").resolve()
    if results_root == out_dir or results_root in out_dir.parents:
        print(f"拒绝把评测产物写进 run 结果树：{out_dir}", file=sys.stderr)
        return 2
    if out_dir.exists():
        print(f"拒绝覆盖已存在的输出目录：{out_dir}", file=sys.stderr)
        return 2

    os.makedirs(out_dir, exist_ok=False)

    dev_batch = None
    if not args.no_dev:
        modules = args.dev_module or list(DEFAULT_DEV_MODULES)
        print(f"[评测] 跑聚焦测试：{' '.join(modules)}", file=sys.stderr)
        core = _run_dev_batch(repo_root, modules)
        blob = json.dumps(core, ensure_ascii=False, indent=1).encode("utf-8")
        with (out_dir / "dev_batch.json").open("xb") as fh:
            fh.write(blob)
        dev_batch = dict(core)
        dev_batch["path"] = f"{RUBRIC_RUNS_DIRNAME}/{out_dir.name}/dev_batch.json"
        dev_batch["sha256"] = hashlib.sha256(blob).hexdigest()
        print(f"[评测] 聚焦测试：{core['total']}", file=sys.stderr)

    samples: list[Sample] = [_sample_with_identity(repo_root, "main", args.main, dev_batch)]
    for role, run_id in (("second_completed", args.second), ("failed", args.failed),
                         ("alt_failed", args.alt_failed)):
        if run_id:
            samples.append(_sample_with_identity(repo_root, role, run_id, dev_batch))

    outcome = write_deliverables(repo_root=repo_root, out_dir=out_dir, samples=samples,
                                 dev_batch=dev_batch, create_dir=False)
    print(json.dumps(outcome, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

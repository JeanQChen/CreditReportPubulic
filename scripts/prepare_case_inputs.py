"""公开副本：从**空环境**重建本案例的输入权威（三份 PDF 登记 + 财务快照）。

存在的理由（TASK：公开副本可复现）：`data/evidence.db` 与 `data/financial_v2.db`
是运行期产物、不随副本分发，而构建它们所需的身份参数（PDF 的
document_id / source_type / material_group，财务的 workbooks 顺序 / scope / currency /
声明公司名）**不在任何随附文件中**。本模块把 `data/samples/300750/case_inputs.yaml`
里的声明变成可执行的准备步骤，并**不新增任何业务判据**——它只调用两个既有入口：

1. `python -m evidence.builder <pdf> --store`（登记 + 解析 + 构建 evidence_set）
2. `python -m scripts.prepare_financial_snapshot --db <db> --excel …`
   （内部复用 `scripts.run_financial_v2_chain.run` 建快照，再经 Registry 跑三组财务工具）

它不做的事：
- 不写任何业务规则、不推断 source_type（一律取声明值）、不硬编码金额或期间；
- 不读取任何历史数据库；旧库即使存在也只作只读对照，且不是本模块的输入；
- 不联网、不调用 LLM。

用法（在副本根目录执行）：

  python -m scripts.prepare_case_inputs --check    # 只校验六份文件哈希并打印将要执行的命令
  python -m scripts.prepare_case_inputs            # 校验通过后真的执行上述两步

目标库已存在时**默认拒绝**（见 `--allow-existing-trees` 的说明）：这两个库是运行期
共享权威，静默重建会让「这一次到底基于哪份输入」变得不可追溯。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

PROG = "python -m scripts.prepare_case_inputs"
CONFIG_RELPATH = Path("data/samples/300750/case_inputs.yaml")
CHUNK = 1 << 20


class PrepareCaseInputsError(RuntimeError):
    """具名失败：缺文件、哈希不符、目标库已存在、入口返回非零。"""


# ---------------------------------------------------------------------------
# 声明读取与校验
# ---------------------------------------------------------------------------

def load_config(root: Path) -> dict:
    path = root / CONFIG_RELPATH
    if not path.is_file():
        raise PrepareCaseInputsError(f"缺少输入身份声明：{CONFIG_RELPATH.as_posix()}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PrepareCaseInputsError(f"{CONFIG_RELPATH.as_posix()} 不是映射")
    for key in ("subject", "evidence", "financial"):
        if key not in data:
            raise PrepareCaseInputsError(f"{CONFIG_RELPATH.as_posix()} 缺少 `{key}`")
    return data


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _declared_entries(cfg: dict) -> list[tuple[str, str, int]]:
    """把声明归一成 (relpath, sha256, size_bytes) 三元组。

    六份文件（三份 PDF + 三份 XLSX）**一律**要求声明哈希与字节数：缺任何一项都是
    声明缺陷，不软失败、不回落成「只查存在」。财务工作簿曾以裸路径字符串书写，
    那种写法会被明确拒绝（见下方报错），而不是被静默接受。
    """
    declared: list[tuple[str, str, int]] = []
    problems: list[str] = []
    for item in cfg["evidence"]["documents"]:
        declared.append((str(item["filename"]), str(item["sha256"]), int(item["size_bytes"])))
    for wb in cfg["financial"]["workbooks"]:
        if not isinstance(wb, dict):
            problems.append(
                f"财务工作簿声明格式过旧（需要 filename/sha256/size_bytes 映射）：{wb!r}")
            continue
        declared.append((str(wb["filename"]), str(wb["sha256"]), int(wb["size_bytes"])))
    if problems:
        raise PrepareCaseInputsError("输入声明不可用：\n  - " + "\n  - ".join(problems))
    if len(declared) != 6:
        raise PrepareCaseInputsError(
            f"声明应为 6 份文件（3 PDF + 3 XLSX），实际 {len(declared)} 份")
    return declared


def verify_declared_files(root: Path, cfg: dict) -> list[dict]:
    """逐份复核文件存在、大小与 SHA-256。任一不符即具名失败（不软失败）。"""
    problems: list[str] = []
    checked: list[dict] = []
    for relpath, want_sha, want_size in _declared_entries(cfg):
        path = root / relpath
        if not path.is_file():
            problems.append(f"缺文件：{relpath}")
            continue
        size = path.stat().st_size
        if size != want_size:
            problems.append(f"大小不符：{relpath} 实际 {size}，声明 {want_size}（文件内容已变）")
            continue
        got = file_sha256(path)
        if got != want_sha:
            problems.append(f"SHA-256 不符：{relpath} 实际 {got}，声明 {want_sha}")
            continue
        checked.append({"filename": relpath, "sha256": got, "size_bytes": size})
    if problems:
        raise PrepareCaseInputsError("随附输入校验未通过：\n  - " + "\n  - ".join(problems))
    return checked


# ---------------------------------------------------------------------------
# 命令构造（与 README 中逐条列出的命令一一对应）
# ---------------------------------------------------------------------------

def evidence_commands(cfg: dict) -> list[list[str]]:
    ev = cfg["evidence"]
    commands = []
    for item in ev["documents"]:
        commands.append([
            sys.executable, "-X", "utf8", "-m", "evidence.builder",
            str(item["filename"]),
            "--company", str(cfg["subject"]),
            "--document-id", str(item["document_id"]),
            "--source-type", str(item["source_type"]),
            "--material-group", str(ev["material_group"]),
            "--store",
        ])
    return commands


def financial_command(cfg: dict) -> list[str]:
    fin = cfg["financial"]
    argv = [
        sys.executable, "-X", "utf8", "-m", "scripts.prepare_financial_snapshot",
        "--company", str(fin["company"]),
        "--db", str(fin["db"]),
        "--scope", str(fin["scope"]),
        "--currency", str(fin["currency"]),
    ]
    if fin.get("declared_name"):
        argv += ["--declared-name", str(fin["declared_name"])]
    if fin.get("detected_name"):
        argv += ["--detected-name", str(fin["detected_name"])]
    for wb in fin["workbooks"]:
        argv += ["--excel", str(wb["filename"])]
    return argv


def _render(argv: list[str]) -> str:
    return " ".join(argv)


def _guard_targets(root: Path, cfg: dict) -> None:
    existing = [
        str(rel) for rel in (cfg["evidence"]["db"], cfg["financial"]["db"])
        if (root / rel).exists()
    ]
    if existing:
        raise PrepareCaseInputsError(
            "目标库已存在，本模块不覆盖运行期权威：\n  - " + "\n  - ".join(existing)
            + "\n这两个库是运行期产物（不随副本分发）。要重建，请先把上面这些文件"
              "移走或删除；`--allow-existing-trees` 只跳过这道检查，不会清空它们。")


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def _run(root: Path, argv: list[str]) -> dict:
    proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        raise PrepareCaseInputsError(
            f"入口返回 {proc.returncode}：{_render(argv)}\n  " + "\n  ".join(tail))
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw_stdout_tail": (proc.stdout or "").strip().splitlines()[-5:]}


def prepare(root: Path, cfg: dict, *, check_only: bool) -> dict:
    checked = verify_declared_files(root, cfg)
    ev_cmds = evidence_commands(cfg)
    fin_cmd = financial_command(cfg)
    if check_only:
        return {
            "mode": "check-only",
            "verified_files": checked,
            "would_run": [_render(c) for c in ev_cmds] + [_render(fin_cmd)],
            "wrote_nothing": True,
        }

    _guard_targets(root, cfg)
    evidence_runs = [_run(root, argv) for argv in ev_cmds]
    financial = _run(root, fin_cmd)
    return {
        "mode": "prepared",
        "verified_files": checked,
        "evidence": evidence_runs,
        "financial": financial,
        "expected": cfg.get("expected", {}),
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="从空环境重建本案例输入权威（PDF 登记 + 财务快照）；不联网、不调用 LLM")
    parser.add_argument("--check", action="store_true",
                        help="只校验六份文件哈希并打印将要执行的命令，不写任何文件")
    parser.add_argument("--root", default=None,
                        help="副本根目录（缺省＝本文件的上上级目录）")
    parser.add_argument("--allow-existing-trees", action="store_true",
                        help="目标库已存在时也继续（不跳过校验，也不清空既有库）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    try:
        cfg = load_config(root)
        if args.allow_existing_trees and not args.check:
            result = prepare_with_override(root, cfg)
        else:
            result = prepare(root, cfg, check_only=args.check)
    except PrepareCaseInputsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def prepare_with_override(root: Path, cfg: dict) -> dict:
    """与 `prepare()` 相同，但跳过「目标库已存在」这道检查。"""
    checked = verify_declared_files(root, cfg)
    evidence_runs = [_run(root, argv) for argv in evidence_commands(cfg)]
    financial = _run(root, financial_command(cfg))
    return {
        "mode": "prepared (allow-existing-trees)",
        "verified_files": checked,
        "evidence": evidence_runs,
        "financial": financial,
        "expected": cfg.get("expected", {}),
    }


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

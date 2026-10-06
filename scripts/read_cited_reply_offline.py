"""从**留存的字节**做一次只读诊断：真实 Writer 返回 → 原稿人读页 + 离线派生诊断。

只读、只离线：**不联网、不调模型、不碰被读的那一轮目录**。输出写进一个**新的** create-only
目录，输入侧一个字节都不改。

用法（Windows 下独立运行需 `PYTHONIOENCODING=utf-8`）：

    python scripts/read_cited_reply_offline.py \
        --log logs/llm/<...>__<call_id>.jsonl \
        --out-dir evaluation/results/<新的诊断目录>

`--log` 指向 `logs/llm` 里的一行 JSON 记录（含 `messages` 与 `completion`）。请求面直接从
同一条记录里取——**不**允许用别的轮次的清单顶替（那正是「不能拿 r25 的清单冒充 r2」那条裁决）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sections import cited_reply_readback as CRRB  # noqa: E402


def _load_record(path: Path, call_id: str) -> dict:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    if call_id:
        records = [r for r in records if str(r.get("call_id", "")) == call_id]
    if not records:
        raise SystemExit(f"{path} 里没有可用的记录（call_id={call_id!r}）")
    if len(records) > 1:
        raise SystemExit(f"{path} 里有多条记录（{len(records)}）：请用 `--call-id` 指明一条。"
                         "一次诊断只对**一条**调用负责，混起来读等于把两次调用当成一次")
    return records[0]


def _request_face(record: dict) -> dict:
    messages = record.get("messages") or []
    if not messages:
        raise SystemExit("这条记录里没有 `messages`：没有请求面就没有「模型看到了什么」，"
                         "本诊断拒绝在缺输入的情况下评价输出")
    return json.loads(messages[0]["content"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="留存 Writer 返回的只读离线诊断")
    parser.add_argument("--log", required=True, type=Path,
                        help="logs/llm 里的一行 JSON 记录（只读）")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="新的诊断目录（create-only；已存在即拒绝）")
    parser.add_argument("--call-id", default="", help="同一条记录里有多次调用时指明 call_id")
    parser.add_argument("--analyst-notes", type=Path, default=None,
                        help="人工判断的 JSON 数组文件（可选）。每条是 "
                             '{"subject","judgment","sentence_ids","note","evidence"}；'
                             "`judgment` 必须取 ANALYST_JUDGMENTS 里的值。"
                             "它会被**原样**写进产物并标注为人工判断")
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    if out_dir.exists():
        raise SystemExit(f"输出目录已存在，拒绝覆盖：{out_dir}")

    record = _load_record(args.log, args.call_id)
    request_face = _request_face(record)
    call = {
        "call_id": str(record.get("call_id", "") or ""),
        "model": str(record.get("model", "") or ""),
        "prompt_version": str(record.get("prompt_version", "") or ""),
        "finish_reason": str(record.get("finish_reason", "") or ""),
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "latency_ms": record.get("latency_ms"),
        "thinking": record.get("thinking"),
        "status": "ok",
    }
    completion = record.get("completion")
    call["visible_chars"] = len(str(completion or ""))
    if not completion:
        raise SystemExit("这条记录里没有可见回复（`completion` 为空）：没有回复就没有可诊断的"
                         "正文——截断或零可见文本是**另一条**结论，不是本诊断的输入")

    notes = []
    if args.analyst_notes is not None:
        notes = json.loads(args.analyst_notes.read_text(encoding="utf-8"))
        if not isinstance(notes, list):
            raise SystemExit(f"{args.analyst_notes} 必须是一个 JSON 数组")

    diagnosis = CRRB.diagnose(reply_text=completion, request_face=request_face, call=call,
                              analyst_notes=notes)
    files = CRRB.diagnosis_files(diagnosis)

    out_dir.mkdir(parents=True)
    for name, body in files.items():
        (out_dir / name).write_text(body, encoding="utf-8")

    counts = diagnosis.verdict_counts()
    print(f"写出 {out_dir}")
    print(f"  原稿句数：{len(diagnosis.sentences())}；"
          + "；".join(f"{k} {v}" for k, v in counts.items() if v))
    print(f"  未判轴：{sum(1 for a in diagnosis.axes if a['status'] != CRRB.AXIS_ADJUDICATED)} 条"
          f"（逐条见 derived_diagnosis.md §3）")
    print(f"  人工判断：{len(diagnosis.analyst_notes)} 条"
          f"（**不是**机械判据，见 derived_diagnosis.md §4）")
    print(f"  缺口里越出「本次输入」范围的断言：{len(CRRB.gap_source_assertions(diagnosis))} 条"
          f"（须人工改写）")
    print("  提醒：这是**离线派生诊断**，不是那一轮的正式通过；正文不可发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

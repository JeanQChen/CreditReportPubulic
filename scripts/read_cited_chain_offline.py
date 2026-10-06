"""从一个写作运行目录做**离线**四段对账：清单 → 句子引用 → 核对 → 审阅输入。

只读、只离线：**不联网、不调模型、不碰被读的那个目录**。输出写进一个**新的** create-only
目录，输入侧一个字节都不改；缺件一律记「本轮不可判」，**不**拿别轮产物顶替。

用法（Windows 下独立运行需 `PYTHONIOENCODING=utf-8`）：

    python scripts/read_cited_chain_offline.py \
        --run-dir evaluation/results/<run-id> \
        --out-dir evaluation/results/<新的对账目录>

`--run-dir` 里按固定文件名找五份产物（见
:data:`sections.cited_chain_readback.CHAIN_LINK_FILES`）。真实 r2 那一轮只有两份，缺的三份会被
逐条列为「本轮不可判」——这正是本命令存在的意义。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sections import cited_chain_readback as CCRB  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写作运行目录的离线四段对账")
    parser.add_argument("--run-dir", required=True, type=Path,
                        help="写作运行目录（只读；不动它一个字节）")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="新的对账目录（create-only；已存在即拒绝）")
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    if out_dir.exists():
        raise SystemExit(f"输出目录已存在，拒绝覆盖：{out_dir}")
    if not args.run_dir.exists():
        raise SystemExit(f"运行目录不存在：{args.run_dir}")

    readback = CCRB.read_chain(args.run_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "chain_readback.md").write_text(
        CCRB.render_chain_markdown(readback), encoding="utf-8")
    (out_dir / "chain_readback.json").write_text(
        json.dumps(readback.to_dict(), ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")

    print(f"写出 {out_dir}")
    for link in readback.links:
        state = "在场" if link.present else "**缺失**"
        note = "；".join(link.problems) or link.detail
        print(f"  [{state}] {link.name}（{link.filename}）：{note}")
    print(f"  逐句四段对照：{len(readback.sentences)} 句")
    print(f"  四段齐备且自洽：{'是' if readback.complete else '否'}")
    if readback.unjudged_fields:
        print(f"  本轮不可判的字段：{list(readback.unjudged_fields)}")
    print("  提醒：本页**不是**验收门。四段自洽 ≠ 内容成立；合格与否仍由独立审阅与人工接受判。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""本批开发测试批次的运行器：D01/D03 的唯一数据来源。

**为什么单独一个文件。** 报告 run 的读数是"从产物重算"，开发测试的读数是"这批改动的
测试怎么跑的"，两本账不能混。把它们放在同一个 JSON 里，就等于允许用"测试全绿"回答
"报告写得好不好"。所以这里只产出开发侧那两行要用的东西。

跑法（评测命令自己起子进程，不在评测进程里 in-process 导测试模块）：

    python -m evaluation.rubric_eval.dev_batch evals.test_rubric_eval ...

它把每个模块的 ``main()`` 汇总（``passed`` / ``failed`` / ``skipped``）抄成 JSON 打到
stdout。**不**改环境里的库，**不**写被评 run，**不**联网（`EVAL_MOCK_LLM=true`）。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import traceback
from pathlib import Path

#: 本批评测引擎**自己**的缺陷台账（D03 的分子分母就取这里）。
#:
#: 每条必须同时给出反例与正例，且两者都是**真实存在**的测试方法名——台账不能靠描述成立。
#: `evals.test_rubric_eval` 里有一条回归逐个断言这些方法真的存在，否则这条台账会随重构悄悄失效。
#: 这里记的是**评测引擎的缺陷**，不是被评 run 的产品缺陷，两者不并账。
DEFECTS: tuple[dict[str, str], ...] = (
    {
        "id": "rbeval-p08-offline-echo",
        "title": "P08 曾把离线回声的 issues[] 算成本 run 独立审阅覆盖",
        "detail": ("`offline_diagnostic_echo` 没有任何独立语义判断，却照常写出 issues[] 与 "
                   "sentence_ids[]。原实现只看到 outcome=reviewed 就计入分子，一份离线回声即可把"
                   "「本 run 被独立审阅覆盖」刷成 100%。现要求 review_producer_kind 必须是"
                   " independent_llm_review 且 call_id 在本 run 账本里。"),
        "counter_example": "evals.test_rubric_eval::test_offline_echo_is_not_review",
        "positive_case": "evals.test_rubric_eval::test_offline_echo_is_not_review",
    },
    {
        "id": "rbeval-devbatch-stdout",
        "title": "测试模块自己的 stdout 曾污染批次台账 JSON，整批被记成「未产出 JSON」",
        "detail": ("`evals.test_m930_5_upload_run` 跑起来会往 stdout 打三行 'demo scope profile=…'。"
                   "混编运行时这三行落在台账 JSON 之前，调用方 json.loads 当场失败，**三个模块**"
                   "全被记成崩溃，而它们其实全绿。现逐模块重定向 stdout/stderr 到缓冲，并保留"
                   "从第一个顶格 `{` 起的兜底解析。"),
        "counter_example": "evals.test_rubric_eval::test_dev_batch_stdout_discipline",
        "positive_case": "evals.test_rubric_eval::test_dev_batch_stdout_discipline",
    },
    {
        "id": "rbeval-p01-evidence-prefix",
        "title": "P01 取证时输入目录前缀被加了两遍，「上传的原始字节」证据链全断",
        "detail": ("`_check_input` 给的 `relpath` 已经是 `data/run_inputs/<run_id>/…`，又被喂给 "
                   "`ref(uploaded=True)`，前缀再补一次 ⇒ 那六条最要紧的证据全部 `exists=False`、"
                   "`sha256=None`。P01 的分子仍是 6/6（它自己按绝对路径重算），所以读数看起来"
                   "正确、只有证据链是断的。现改为传输入目录内的相对路径，并加一条"
                   "「每条证据的 exists/sha256 必须与磁盘相符」的自洽回归。"),
        "counter_example": "evals.test_rubric_eval::test_evidence_self_consistency",
        "positive_case": "evals.test_rubric_eval::test_evidence_self_consistency",
    },
    {
        "id": "rbeval-b04-unitless-cell",
        "title": "B04 的 A1 格值比较器曾要求单位后缀，把无单位比率格值全判成不符",
        "detail": ("原比较器只认 (数字, 单位) 元组，'1.57'、'3.26'、'1.4' 这类无单位比率格值永远"
                   "匹配不上，A1 出现 68/84 的**假负**。这是读数实现缺陷、不是业务缺陷——差一点"
                   "就被写进 readback 当成财务保真问题。现允许无单位格值只比数字，且仍拒绝"
                   "四舍五入后相等。"),
        "counter_example": "evals.test_rubric_eval::test_cell_matcher",
        "positive_case": "evals.test_rubric_eval::test_cell_matcher",
    },
)


def run_modules(modules: list[str]) -> dict:
    """逐个导入并调用 ``main()``。任何异常都变成一条点名记录，不吞掉、不中止其余模块。

    **每个模块的 stdout/stderr 都被就地截流**：不少测试模块自己会 `print`（有的还直接在
    `main()` 里起 `unittest.TextTestRunner`，那是往 stderr 写的）。这些字节如果混进本函数
    的 stdout，调用方 `json.loads(proc.stdout)` 就会当场失败——**整批测试**被记成"未产出
    JSON"，而每个模块其实都跑绿了。所以这里逐模块重定向到缓冲，把尾巴放进记录里留证，
    stdout 上只剩本函数自己那一份 JSON。
    """
    if str(Path(__file__).resolve().parents[2]) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("EVAL_MOCK_LLM", "true")
    out = []
    for name in modules:
        record = {"module": name, "passed": 0, "failed": 0, "skipped": 0,
                  "crashed": False, "error": "", "details": [], "output_tail": ""}
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                module = importlib.import_module(name)
                result = module.main() or {}
            for key in ("passed", "failed", "skipped"):
                value = result.get(key)
                if not isinstance(value, int):
                    raise TypeError(f"模块汇总的 {key} 不是整数：{value!r}")
                record[key] = value
            record["details"] = [str(d)[:400] for d in (result.get("details") or [])][:40]
        except BaseException as exc:  # noqa: BLE001 —— 含 SystemExit，必须接住
            record["crashed"] = True
            record["failed"] = 1
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["details"] = [traceback.format_exc()[-1500:]]
        record["output_tail"] = buffer.getvalue()[-1500:]
        out.append(record)
    return {
        "mock_llm": os.environ.get("EVAL_MOCK_LLM") == "true",
        "command": "python -m evaluation.rubric_eval.dev_batch " + " ".join(modules),
        "tests": out,
        "total": {k: sum(r[k] for r in out) for k in ("passed", "failed", "skipped")},
        #: D03 的分子分母。空台账是**如实**的「本批未登记缺陷」，不是 0 分。
        "defects": [dict(d) for d in DEFECTS],
    }


def main() -> int:
    modules = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not modules:
        print("用法：python -m evaluation.rubric_eval.dev_batch evals.test_x [evals.test_y ...]",
              file=sys.stderr)
        return 2
    print(json.dumps(run_modules(modules), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

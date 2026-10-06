# -*- coding: utf-8 -*-
"""离线只读评测引擎（`rbeval-1`）：把 `EVALUATION_RUBRIC_WEBSITE_FORM.md` 的 24 个指标
落到**一个已落盘的真实 run** 上，产出机器可复核的读数。

三条纪律：

* **只读。** 本包不打开任何数据库（不 init / 不 migrate）、不写历史 run、不发请求、不建 run。
  它只读 `evaluation/results/<run_id>/` 与 `data/run_inputs/<run_id>/` 下已有的字节，并重算哈希。
* **不伪造。** `MEASURED` 才允许带数值；缺 Gold / 待人读 / 上游未运行分别用各自的测量状态，
  分母为 0 时 `value = None`，绝不填 0 或 100%。机械计数不得冒充业务正确率。
* **不合并。** 每个 run 一份结果，不跨 run 拼接分子分母；开发测试批次与报告 run 分账。

指标目录见 :mod:`evaluation.rubric_eval.catalog`，读数实现见
:mod:`evaluation.rubric_eval.measures`，交付物写出见 :mod:`evaluation.rubric_eval.evaluate`。
"""
from __future__ import annotations

RUBRIC_EVAL_VERSION = "rbeval-1"

__all__ = ["RUBRIC_EVAL_VERSION"]

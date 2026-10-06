"""树结构测试共用的**受控阶段环境**（只读开关，不写任何正式资产）。

TS4-A 的历史通道测试必须能在 TS4-B 阶段下继续运行：它们要造的是**A 版**产物，而生产
代码的阶段门（`span_builder.SpanPolicyProvider.current_for_new_build`、
`span_policy.assert_distribution_only` / `resolve_distribution_policy`）按设计**只认
当前阶段**。本模块提供的是一个显式、可审计的模拟环境，而不是让测试绕过那道门：

- `simulated_a_environment()` 只临时改写**一个**模块级读取点
  （`versions.SPAN_CONFIDENCE_MIN`），退出时逐字还原并**断言**还原成功；
- 正式 `policies/` 目录、注册表、A 策略记录与已导出的 approval / frozen 资产
  **一个字节都不改**；
- 当前本来就处于 A 阶段时它是**空操作**，因此同一套断言在 A / B 两个阶段下都成立，
  阶段不同不会被"跳过"掉。

用途与边界：只在测试里使用。生产代码不得 import 本模块。
"""

from __future__ import annotations

import contextlib

from document_structure import span_policy as SP
from document_structure import versions as V

__all__ = ["StageEnvironmentError", "current_stage", "next_stage",
           "simulated_a_environment"]


class StageEnvironmentError(AssertionError):
    """受控阶段环境未能生效或未能逐字还原时抛出（fail-closed）。"""


def current_stage() -> str:
    """当前阶段的唯一读数（由 `versions.SPAN_CONFIDENCE_MIN` 单点派生）。"""
    return SP.ab_gate_truth_table()["stage"]


def next_stage() -> str:
    """**另一个**阶段的字面量（用于构造跨阶段反例，与当前阶段无关地成立）。"""
    return ("threshold_enabled" if current_stage() == "distribution_only"
            else "distribution_only")


class _SimulatedAEnvironment:
    """把"当前阶段"临时拨回 `distribution_only`，退出时逐字还原。"""

    def __init__(self) -> None:
        self._original = None
        self.table: dict | None = None

    def __enter__(self) -> "_SimulatedAEnvironment":
        self._original = V.SPAN_CONFIDENCE_MIN
        V.SPAN_CONFIDENCE_MIN = None
        self.table = SP.ab_gate_truth_table()
        if self.table["stage"] != "distribution_only":
            V.SPAN_CONFIDENCE_MIN = self._original
            raise StageEnvironmentError(
                f"模拟 A 环境未能生效：真值表仍为 {self.table['stage']!r}")
        return self

    def __exit__(self, *_exc) -> bool:
        V.SPAN_CONFIDENCE_MIN = self._original
        if V.SPAN_CONFIDENCE_MIN != self._original:
            raise StageEnvironmentError(
                "模拟 A 环境必须逐字还原 versions.SPAN_CONFIDENCE_MIN："
                f"原值 {self._original!r}，得到 {V.SPAN_CONFIDENCE_MIN!r}")
        return False


def simulated_a_environment() -> contextlib.AbstractContextManager:
    """返回一个**受控模拟 TS4-A 环境**的上下文管理器（可嵌套，退出即还原）。"""
    return _SimulatedAEnvironment()

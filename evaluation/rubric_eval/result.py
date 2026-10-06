# -*- coding: utf-8 -*-
"""一条指标读数的统一形状。

一个 ``MetricResult`` 只描述**一个 run 上的一个指标**。它不允许出现"综合分"字段，
也不允许把"没测成"编码成 0：``MEASURED`` 之外的状态一律 ``numerator/value = None``。

字段里有两组容易混淆的东西，故意分开：

* ``execution_state`` 回答"这一环这次跑没跑"；
* ``measurement_state`` 回答"跑出来的东西能不能算成这个指标的分数"。

二者必须能各说各话：整节可能是 ``DID_RUN`` 而指标仍是 ``BENCHMARK_PENDING``（没有 Gold），
也可能是 ``FAILED`` 而指标 ``NOT_RUN_UPSTREAM``（下游根本没被调用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .catalog import BY_ID, EXECUTION_STATES, MEASUREMENT_STATES


@dataclass
class MetricResult:
    metric_id: str
    source_run_id: str
    scope: str
    execution_state: str
    measurement_state: str
    numerator: int | float | None = None
    denominator: int | float | None = None
    value: float | None = None
    unit: str = ""
    verdict: str = ""
    evidence_refs: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    # 额外但必要的诚实字段 ------------------------------------------------
    note: str = ""
    first_missing: str = ""
    missing_chain: tuple[str, ...] = ()
    pending_axes: tuple[dict[str, Any], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.measurement_state not in MEASUREMENT_STATES:
            raise ValueError(f"{self.metric_id}: 未知测量状态 {self.measurement_state!r}")
        if self.execution_state not in EXECUTION_STATES:
            raise ValueError(f"{self.metric_id}: 未知执行状态 {self.execution_state!r}")
        if self.metric_id not in BY_ID:
            raise ValueError(f"{self.metric_id}: 不在指标目录里")
        # 纪律：只有 MEASURED 允许带数值；其余状态绝不允许留下 0 冒充读数。
        if self.measurement_state != "MEASURED":
            self.numerator = None
            self.denominator = self.denominator if self.denominator else None
            self.value = None
        if self.denominator in (0, 0.0):
            self.value = None

    def to_row(self) -> dict[str, Any]:
        """交付物里的一行。目录侧的定义不在这里重复，只放这次读数。"""
        spec = BY_ID[self.metric_id]
        return {
            "metric_id": self.metric_id,
            "title": spec.title,
            "primary_dimension": spec.primary_dimension,
            "stage": spec.stage,
            "source_run_id": self.source_run_id,
            "scope": self.scope,
            "execution_state": self.execution_state,
            "measurement_state": self.measurement_state,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "unit": self.unit,
            "verdict": self.verdict,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "note": self.note,
            "first_missing": self.first_missing,
            "missing_chain": list(self.missing_chain),
            "pending_axes": list(self.pending_axes),
            "details": self.details,
        }


def ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    """分母为零（或缺失）时返回 ``None``——**绝不**回落成 0 或 100%。"""
    if numerator is None or not denominator:
        return None
    return round(float(numerator) / float(denominator), 6)


def measured(*, metric_id: str, run_id: str, scope: str, numerator: int | float | None,
             denominator: int | float | None, unit: str = "ratio", verdict: str = "",
             evidence_refs: tuple[dict[str, Any], ...] = (), limitations: tuple[str, ...] = (),
             note: str = "", execution_state: str = "DID_RUN",
             pending_axes: tuple[dict[str, Any], ...] = (),
             details: dict[str, Any] | None = None,
             measured_but_empty: bool = False) -> MetricResult:
    """构造一条真正测出来的读数。

    ``measured_but_empty=True`` 用于"这一环确实跑了、也确实算得出分母，但集合是空的"这种
    情形：状态仍是 ``MEASURED``，``denominator`` 如实写 0，``value`` 由 :func:`ratio`
    落成 ``None``。它和"没测"是两回事，不能合并。
    """
    value = ratio(numerator, denominator) if not measured_but_empty else None
    return MetricResult(
        metric_id=metric_id, source_run_id=run_id, scope=scope,
        execution_state=execution_state, measurement_state="MEASURED",
        numerator=numerator, denominator=denominator, value=value, unit=unit,
        verdict=verdict, evidence_refs=evidence_refs, limitations=limitations,
        note=note, pending_axes=pending_axes, details=details or {})


def pending(*, metric_id: str, run_id: str, scope: str, measurement_state: str,
            execution_state: str, verdict: str, first_missing: str,
            missing_chain: tuple[str, ...] = (), denominator: int | None = None,
            evidence_refs: tuple[dict[str, Any], ...] = (), limitations: tuple[str, ...] = (),
            note: str = "", pending_axes: tuple[dict[str, Any], ...] = (),
            details: dict[str, Any] | None = None) -> MetricResult:
    """构造一条"没测成"的读数。必须写清**第一个**缺的东西。"""
    if measurement_state == "MEASURED":
        raise ValueError(f"{metric_id}: pending() 不接受 MEASURED；测出来就请用 measured()")
    if not first_missing:
        raise ValueError(f"{metric_id}: 未测成的读数必须写明 first_missing")
    return MetricResult(
        metric_id=metric_id, source_run_id=run_id, scope=scope,
        execution_state=execution_state, measurement_state=measurement_state,
        numerator=None, denominator=denominator, value=None, unit="",
        verdict=verdict, evidence_refs=evidence_refs, limitations=limitations,
        note=note, first_missing=first_missing, missing_chain=missing_chain,
        pending_axes=pending_axes, details=details or {})


__all__ = ["MetricResult", "ratio", "measured", "pending"]

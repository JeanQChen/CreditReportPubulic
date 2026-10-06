"""报告时钟（M930-3 批次 A 一.4 / DESIGN_V2.md O-11）。

职责：把「一次运行只捕获一个时钟瞬间」落实为一个可复用、可测试的边界，
并从该瞬间**确定地**派生两个不同字段：

    generated_at  —— 该瞬间的 UTC 时间戳（轨迹/运行身份时间，不进入内容身份）
    report_as_of  —— **同一瞬间**按版本化时区资产换算的**报告生成日**（业务日期）

为什么必须有这个复用边界（而不是各处各取一次 now）：

    改动前 runner 里 `generated_at`、`started_at`、`report_as_of` 来自三次独立采样
    （且 `report_as_of` 还来自 `FinancialSnapshot.as_of_date`）。分别采样会在时区
    跨午夜时产生跨日错位：UTC 2026-09-23T16:30 在 UTC+8 已经是 2026-09-24，
    若 UTC 侧先取、时区侧后取（或反之）就会把两个不同的日子当成同一次运行。

    `FinancialSnapshot.as_of_date` 始终只表示**财务数据期末**，本模块不读它，
    也不允许任何调用方用它推导 `report_as_of`。

fail-closed：缺时区资产、缺 `report_timezone`、或不是可识别的 IANA 时区名 → 抛错。
不回落系统本地时区，不回落 UTC 猜测——猜出来的报告生成日是伪造的业务日期。

本模块公司无关、文件名无关、页码无关。
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

logger = logging.getLogger("harness.report_clock")

# 唯一资产身份（禁止 report_clock_v1 / report-clock-v1 混用）
POLICY_ID = "report_clock_v1"
POLICY_VERSION = "v1"
SCHEMA_VERSION = "report-clock-v1"

#: 仓库内默认资产路径（唯一默认取值点；调用方也可显式传入）。
DEFAULT_CLOCK_ASSET_PATH = Path("templates/policies/report_clock_v1.yaml")

#: `generated_at` 的序列化形状（UTC）。
UTC_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
#: `report_as_of` 的序列化形状（报告生成日，日期粒度）。
REPORT_DATE_FORMAT = "%Y-%m-%d"


class ReportClockError(RuntimeError):
    """时钟配置缺失/非法，或采样结果无法派生报告生成日。fail-closed。"""


@dataclass(frozen=True)
class ClockConfig:
    """版本化时钟配置（只读）。"""

    policy_id: str
    policy_version: str
    schema_version: str
    status: str
    created_at: str
    approved_at: str | None
    report_timezone: str
    report_timezone_basis: str
    raw: dict = field(repr=False)

    def zone(self) -> ZoneInfo:
        """已校验的时区对象（构造时校验过，这里不吞异常）。"""
        try:
            return ZoneInfo(self.report_timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:  # pragma: no cover - 构造期已挡
            raise ReportClockError(
                f"report_timezone 不是可识别的 IANA 时区名: "
                f"{self.report_timezone!r}（{type(e).__name__}）") from e


@dataclass(frozen=True)
class ClockConfigValidationResult:
    valid: bool
    errors: list
    warnings: list
    stats: dict


@dataclass(frozen=True)
class ReportClockInstant:
    """一次运行唯一时钟瞬间的派生化身。

    - `generated_at` / `report_as_of` 由**同一次采样**派生，不可能跨午夜错位。
    - `sampled_utc` 保留原始瞬间供审计/断言；不参与任何身份指纹。
    """

    generated_at: str
    report_as_of: str
    report_timezone: str
    sampled_utc: dt.datetime

    def __post_init__(self) -> None:
        if self.sampled_utc.tzinfo is None:
            raise ReportClockError("sampled_utc 必须带时区（naive datetime 不接受）")


def parse_clock_config(text: str) -> ClockConfig:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ReportClockError("时钟配置 YAML 顶层必须是 dict")
    return ClockConfig(
        policy_id=doc.get("policy_id", ""),
        policy_version=doc.get("policy_version", ""),
        schema_version=doc.get("schema_version", ""),
        status=doc.get("status", "candidate"),
        created_at=str(doc.get("created_at", "")),
        approved_at=doc.get("approved_at"),
        report_timezone=str(doc.get("report_timezone", "") or ""),
        report_timezone_basis=str(doc.get("report_timezone_basis", "") or ""),
        raw=dict(doc),
    )


def load_clock_config(path: str | Path | None = None) -> ClockConfig:
    p = Path(path) if path is not None else DEFAULT_CLOCK_ASSET_PATH
    if not p.exists():
        # fail-closed：不回落系统时区，也不造一个默认值出来。
        raise ReportClockError(f"时钟配置资产不存在，fail-closed: {p}")
    cfg = parse_clock_config(p.read_text(encoding="utf-8"))
    result = validate_clock_config(cfg)
    if not result.valid:
        raise ReportClockError(
            "时钟配置校验失败，fail-closed: " + "；".join(result.errors))
    return cfg


def validate_clock_config(cfg: ClockConfig) -> ClockConfigValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if cfg.policy_id != POLICY_ID:
        errors.append(f"policy_id 应为 {POLICY_ID!r}，实际 {cfg.policy_id!r}")
    if cfg.policy_version != POLICY_VERSION:
        errors.append(f"policy_version 应为 {POLICY_VERSION!r}，实际 {cfg.policy_version!r}")
    if cfg.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION!r}，实际 {cfg.schema_version!r}")

    if not cfg.report_timezone:
        errors.append("report_timezone 不能为空（缺配置必须 fail-closed，不得回落系统时区）")
    else:
        try:
            ZoneInfo(cfg.report_timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            errors.append(
                f"report_timezone 不是可识别的 IANA 时区名: "
                f"{cfg.report_timezone!r}（{type(e).__name__}）")
    if not cfg.report_timezone_basis:
        warnings.append("report_timezone_basis 为空：时区取值缺少审计依据")

    stats = {
        "policy_id": cfg.policy_id,
        "report_timezone": cfg.report_timezone,
    }
    return ClockConfigValidationResult(
        valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)


def capture_report_clock(
    *,
    config: ClockConfig | None = None,
    now_utc: dt.datetime | None = None,
) -> ReportClockInstant:
    """捕获一次运行的唯一时钟瞬间并派生两个字段。

    `now_utc` 只为确定性测试注入；生产路径不传即取当前 UTC 瞬间。
    无论是否注入，两个字段都只由**这一个**瞬间派生。
    """
    cfg = config if config is not None else load_clock_config()

    if now_utc is None:
        instant = dt.datetime.now(dt.timezone.utc)
    else:
        if now_utc.tzinfo is None:
            raise ReportClockError("now_utc 必须带时区（naive datetime 不接受）")
        instant = now_utc

    utc_instant = instant.astimezone(dt.timezone.utc)
    local_instant = utc_instant.astimezone(cfg.zone())

    return ReportClockInstant(
        generated_at=utc_instant.strftime(UTC_STAMP_FORMAT),
        report_as_of=local_instant.strftime(REPORT_DATE_FORMAT),
        report_timezone=cfg.report_timezone,
        sampled_utc=utc_instant,
    )


def run_stamp(instant: ReportClockInstant) -> str:
    """运行目录/run_id 的时间戳形态，取自**同一瞬间**的 UTC 值。

    单独提供而不是让调用方再取一次 now：调用方各取一次就会出现
    「目录名说是 09-23，report_as_of 却是 09-24」这类跨午夜错位。
    """
    return instant.sampled_utc.strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验报告时钟配置并演示一次采样")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_CLOCK_ASSET_PATH),
                        help="report_clock_v1.yaml 路径")
    args = parser.parse_args(argv)

    try:
        cfg = load_clock_config(args.path)
        result = validate_clock_config(cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("校验时钟配置失败")
        print(f"校验异常: {type(e).__name__}: {e}")
        return 1

    if not result.valid:
        print(f"校验失败（{len(result.errors)} 处）:")
        for err in result.errors:
            print(f"  - {err}")
        return 1

    instant = capture_report_clock(config=cfg)
    print(f"时钟配置校验通过: policy_id={cfg.policy_id} "
          f"timezone={cfg.report_timezone}")
    print(f"generated_at={instant.generated_at} "
          f"report_as_of={instant.report_as_of} run_stamp={run_stamp(instant)}")
    for w in result.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())

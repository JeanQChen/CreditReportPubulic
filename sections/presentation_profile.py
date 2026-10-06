"""PresentationProfile v1 的只读 schema / loader / validator。

载体：templates/presentation_profiles/interview_demo_v1.yaml。

职责：加载唯一版本化呈现配置（折叠层级、展示规则、截图区域、附录项），
做纯声明式校验，硬约束呈现边界——只允许 display/folding/screenshots/appendix，
禁止 fact_change/coverage_change/citation_change/business_judgment_change。

边界（R1-A §十）：本模块不接正式渲染 runtime，不改变事实、覆盖、引用或业务判断。

CLI：
    python -m sections.presentation_profile templates/presentation_profiles/interview_demo_v1.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from contracts import schema_v2 as S

logger = logging.getLogger("sections.presentation_profile")

PROFILE_ID = "interview_demo_v1"
SCHEMA_VERSION = "presentation-profile-v1"
WRITING_SPEC_REF = "credit_report_v1"

_ALLOWED_SCOPE = ("display", "folding", "screenshots", "appendix")
_FORBIDDEN_SCOPE = ("fact_change", "coverage_change",
                    "citation_change", "business_judgment_change")
_REQUIRED_DISPLAY_RULES = (
    "toc", "tables", "gap_marker", "unresolved_marker", "diagnostic_marker",
    "citation_display", "negative_event_body",
)


@dataclass(frozen=True)
class PresentationProfile:
    presentation_profile_id: str
    schema_version: str
    status: str
    created_at: str
    approved_at: str | None
    frozen_at: str | None
    profile_name: str
    writing_spec_ref: str
    contract_ref: dict
    scope: dict
    fold_levels: dict
    display_rules: dict
    screenshot_regions: list
    appendix_items: list
    raw: dict = field(repr=False)


class PresentationProfileError(Exception):
    """呈现规格侧的 typed fail-closed（视图字段表与 dataclass 不一致即拒）。"""


@dataclass(frozen=True)
class PresentationProfileValidationResult:
    valid: bool
    errors: list
    warnings: list
    stats: dict


#: 投给生成器的**只读呈现视图**包含的字段 = `PresentationProfile` 自己声明的字段集**减去** `raw`。
#: 用显式元组而不是 `__dataclass_fields__` 动态取：动态取会让「新增字段自动进入请求面」变成
#: 悄无声息的行为变更（wire 面变了却没有版本动作）；显式元组 + `assert_payload_fields_current()`
#: 则让「加了字段却忘了同步」当场失败，也让「这里凭空多出几个字段」当场失败。
_PRESENTATION_PAYLOAD_FIELDS = (
    "presentation_profile_id", "schema_version", "status", "created_at", "approved_at",
    "frozen_at", "profile_name", "writing_spec_ref", "contract_ref", "scope",
    "fold_levels", "display_rules", "screenshot_regions", "appendix_items",
)


def assert_payload_fields_current() -> None:
    """`_PRESENTATION_PAYLOAD_FIELDS` 必须恰好等于本类的字段集减去 `raw`（双向）。

    单向校验不够：只查「元组里的字段都存在」放得过「新增了一个字段但没进视图」，于是新字段
    会在请求面里静默消失；只查「类字段都已覆盖」则放得过「视图里混进了一个不存在的字段」，
    那正是 3.2 修复前的形态（`profile_id` / `tables` / `paragraphs` / `citation_style` /
    `period_language_policy` 这五个字段**在真实对象上一个都不存在**，于是 payload 恒为
    `{"schema_version": ...}`，而代码看起来「在做投影」）。
    """
    declared = set(PresentationProfile.__dataclass_fields__) - {"raw"}
    listed = set(_PRESENTATION_PAYLOAD_FIELDS)
    missing = sorted(declared - listed)
    extra = sorted(listed - declared)
    if missing or extra:
        raise PresentationProfileError(
            f"呈现视图字段表与实际 dataclass 不一致：漏 {missing}，多 {extra}"
            "（漏 = 新字段静默不进请求面；多 = 视图里有不存在的字段，等于假装投影）")


def presentation_payload(profile: "PresentationProfile | Any") -> dict:
    """`PresentationProfile` 的**只读**呈现视图 —— 两个调用点共用的**唯一**实现。

    为什么要收敛成一份：M930-3 §三 A / 3.2 之前，`pack_writer.build_narration_messages` 与
    `narrative_organizer`（门后 final Narrative 组织器）各写了一份字段名表，两份都取的是
    **不存在的**属性名，于是两个生成器看到的 `presentation_profile` 都恒等于
    `{"schema_version": ...}`。同一份呈现规格、两个投影、都投影成了空——分开写就必然会这样
    漂移，所以这里只留一份。

    边界（R1-A §十）：这是**只读视图**，不含也不得含任何事实/覆盖/引用/业务判断；本函数
    不校验、不改写、不补默认值，更不把缺失字段伪装成空串（那会让「规格里没有这一项」与
    「规格说这一项是空」变得不可区分）。没有该字段就**不出现**该键。

    对**形状替身**（没有这些字段的对象，如单元测试里的 `object()`）返回 `{}`：这如实表示
    「这份对象没有声明任何呈现字段」，而不是伪造一份内容。
    """
    # 每次投影前先自证字段表与 dataclass 同步：这样「加了字段忘了同步」不会等到读回时才
    # 表现为「请求面里少了一项」，而是在产生请求面那一刻就失败。
    assert_payload_fields_current()
    payload: dict = {}
    for name in _PRESENTATION_PAYLOAD_FIELDS:
        if hasattr(profile, name):
            payload[name] = getattr(profile, name)
    return payload


def load_presentation_profile(path: str) -> PresentationProfile:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"呈现配置文件不存在: {path}")
    return parse_presentation_profile(p.read_text(encoding="utf-8"))


def parse_presentation_profile(text: str) -> PresentationProfile:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("PresentationProfile YAML 顶层必须是 dict")
    return PresentationProfile(
        presentation_profile_id=doc.get("presentation_profile_id", ""),
        schema_version=doc.get("schema_version", ""),
        status=doc.get("status", "candidate"),
        created_at=doc.get("created_at", ""),
        approved_at=doc.get("approved_at"),
        frozen_at=doc.get("frozen_at"),
        profile_name=doc.get("profile_name", ""),
        writing_spec_ref=doc.get("writing_spec_ref", ""),
        contract_ref=doc.get("contract_ref", {}),
        scope=doc.get("scope", {}),
        fold_levels=doc.get("fold_levels", {}),
        display_rules=doc.get("display_rules", {}),
        screenshot_regions=list(doc.get("screenshot_regions", [])),
        appendix_items=list(doc.get("appendix_items", [])),
        raw=dict(doc),
    )


def validate_presentation_profile(
        prof: PresentationProfile) -> PresentationProfileValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if prof.presentation_profile_id != PROFILE_ID:
        errors.append(f"presentation_profile_id 应为 {PROFILE_ID!r}，实际 "
                      f"{prof.presentation_profile_id!r}")
    if prof.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION!r}")

    # 冻结资产生命周期 + 内容指纹（R1-A §九 / 冻结收口 §二）
    errors.extend(S.frozen_asset_errors(prof.raw))
    if prof.writing_spec_ref != WRITING_SPEC_REF:
        errors.append(f"writing_spec_ref 应为 {WRITING_SPEC_REF!r}，实际 "
                      f"{prof.writing_spec_ref!r}")
    if prof.contract_ref.get("contract_version") != S.CONTRACT_VERSION_V2:
        errors.append("contract_ref.contract_version 应为 v2")

    # 呈现边界（硬约束）：允许集/禁止集必须精确
    if tuple(prof.scope.get("allowed", [])) != _ALLOWED_SCOPE:
        errors.append(f"scope.allowed 应为 {list(_ALLOWED_SCOPE)}")
    if tuple(prof.scope.get("forbidden", [])) != _FORBIDDEN_SCOPE:
        errors.append(f"scope.forbidden 应为 {list(_FORBIDDEN_SCOPE)}")

    if prof.fold_levels.get("default") != "h2":
        errors.append("fold_levels.default 应为 h2")

    for rule in _REQUIRED_DISPLAY_RULES:
        if rule not in prof.display_rules:
            errors.append(f"display_rules 缺少 {rule!r}")

    for i, r in enumerate(prof.screenshot_regions):
        if not isinstance(r, dict) or "id" not in r or "label" not in r:
            errors.append(f"screenshot_regions[{i}] 缺 id/label")

    for i, item in enumerate(prof.appendix_items):
        if not isinstance(item, dict) or "id" not in item or "label" not in item:
            errors.append(f"appendix_items[{i}] 缺 id/label")

    stats = {
        "presentation_profile_id": prof.presentation_profile_id,
        "screenshot_regions": len(prof.screenshot_regions),
        "appendix_items": len(prof.appendix_items),
        "display_rules": len(prof.display_rules),
    }
    return PresentationProfileValidationResult(
        valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 PresentationProfile v1")
    parser.add_argument("path", help="interview_demo_v1.yaml 路径")
    args = parser.parse_args(argv)

    try:
        prof = load_presentation_profile(args.path)
        r = validate_presentation_profile(prof)
    except Exception as e:  # noqa: BLE001
        logger.exception("校验 PresentationProfile 失败")
        print(f"校验异常: {type(e).__name__}: {e}")
        return 1

    if not r.valid:
        print(f"校验失败（{len(r.errors)} 处）:")
        for err in r.errors:
            print(f"  - {err}")
        return 1
    print(f"PresentationProfile 校验通过: {r.stats['presentation_profile_id']} "
          f"截图区={r.stats['screenshot_regions']} 附录={r.stats['appendix_items']} "
          f"展示规则={r.stats['display_rules']}")
    for w in r.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())

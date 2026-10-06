"""M930-3 步骤 ③：冻结 aspect → 正式 `InformationNeed` 的**生产接线** + 外部授权的**执行前门**。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -X utf8 -m evals.test_m930_3_aspect_need_builder`

为什么有这一测
--------------
`acc-29` 之前，正式链路注入的 need 构造器是纯结构件（`evals.test_demo_topic_runtime._NeedBuilder`），
它把冻结 `EvidenceRequirementRef.source_classes` 整片丢掉 ⇒ `routing.router.requires_external_source()`
（外部路由的**唯一**判据）恒假，冻结 Contract 里 `external` 那条要求**从未离开 runner**。
`acc-30` 起注入 `harness.aspect_need_builder.FrozenAspectInformationNeedBuilder`，用**同一个**权威
派生器 `TS.derive_support_eligibility(...).required_source_classes` 把声明搬进正式 need。

同时，**开关**从"一行 prompt 文案 + 一个报告位"变成**真门**：`harness.policies.
external_research_unauthorized` 在 `harness.runtime` 的工具执行路径上、**执行前**拒掉三个外部动作。
没有这道门，接线生产构造器就等于静默放开真联网——因此两面必须一起测。

测的是六面
----------
1. **正面**：冻结 `external` ⇒ need 带 `external` ⇒ 真实 `_route()` 落到 `EXTERNAL_RESEARCH`；
   同一句问题若由旧结构件投出则**不**落外部路由（证明差异来自来源类，不是问题文字）。
2. **唯一真值**：need 的来源类必须与 `derive_support_eligibility` 逐 aspect **相等**（含 authority
   分支；`supplemental_only` 不得被抬成 required）。
3. **刻意不投的东西是具名的**：`evidence_kind` 不由 `source_classes` 反推、窗口 token 不得当日期
   （并证明这条限制**是活的**——真塞进去会被读成不可解析）、`depends_on` 不凭空连边。
4. **授权门**：三个外部动作在 `context is None` / 开关为假 / 畸形上下文下一律**执行前**拒绝；
   非外部动作一律不受影响；门覆盖的动作集必须与 `ACTION_ROUTES` 里路由到 `EXTERNAL_RESEARCH`
   的动作集**恰好相等**（将来新增外部动作不得漏门）。
5. **不得升格**：只声明本地来源类的 aspect 不得凭空得到 `external`；构造器**不可能**产出
   `ExternalFact`（不 import 工具/网络/LLM，模块代码体内不出现 `external` 字面量）。
6. **接线对账**：工厂只产生产实现、runtime 的门在 `registry.execute` **之前**且拒绝留痕
   （`source` 是封闭值）、F4 重复搜索拦截未被顶掉。

夹具全部是合成对象：不读真实库、不建真实 Pack、不调 LLM、**不联网**。`_route()` 是路由核心
（不落盘审计），因此本模块不写任何文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import aspect_need_builder as ANB
from harness import actions as A
from harness import policies as P
from harness import topic_schema as TS
from routing import router as RR
from routing import schema as RS

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "evaluation" / "run_m930_3_acceptance.py"
RUNTIME = REPO / "harness" / "runtime.py"
BUILDER = REPO / "harness" / "aspect_need_builder.py"
_SHA = "0" * 64

BUILDER_VERSION = "anb-1"

#: 冻结 time_scope 的真身：**窗口枚举 token**，不是日期。见 `harness/topic_schema.py`
#: 的 time_scope 枚举（`THREE_YEARS_PLUS_LATEST` 等）。
_FROZEN_WINDOW_TOKEN = "THREE_YEARS_PLUS_LATEST"


class _NS:
    """只读命名空间替身（构造器只按属性名读取快照/引用）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _ref(*, requirement_id: str, source_classes: tuple[str, ...] = (),
         authority=None) -> TS.EvidenceRequirementRef:
    """真 `EvidenceRequirementRef`（不手写 dict）：派生器读的正是它的 `source_classes`/`authority`。"""
    return TS.EvidenceRequirementRef(
        requirement_id=requirement_id, contract_sha256=_SHA,
        requirement_fingerprint=_SHA, schema_version="er-ref/1",
        source_classes=tuple(source_classes), authority=authority)


def _aspect(*, aspect_id: str, requirement_text: str,
            refs: tuple[TS.EvidenceRequirementRef, ...], topic_id: str = "t-industry") -> _NS:
    return _NS(aspect_id=aspect_id, topic_id=topic_id, requirement_text=requirement_text,
               evidence_requirement_ids=tuple(refs))


def _external_aspect(*, aspect_id: str = "ind.industry_scale",
                     requirement_text: str = "行业规模或代理指标") -> _NS:
    return _aspect(aspect_id=aspect_id, requirement_text=requirement_text,
                   refs=(_ref(requirement_id="er-ext", source_classes=("external",)),))


def _local_aspect(*, aspect_id: str = "ind.local",
                  requirement_text: str = "公司在该行业的经营情况") -> _NS:
    return _aspect(aspect_id=aspect_id, requirement_text=requirement_text,
                   refs=(_ref(requirement_id="er-local", source_classes=("company_industry",)),))


def _context(*, enabled: bool = False) -> RS.RouteContext:
    """真 `RouteContext`（路由校验会读它；不含任何样例专用字段）。"""
    return RS.RouteContext(
        company_id="c-1", report_as_of="2026-09-26",
        available_document_ids=[], available_source_types=[],
        supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=enabled,
        snapshot_as_of_date="2025-12-31",
        report_as_of_source=RS.REPORT_DATE_DECLARED)


def _build(aspect, *, builder=None, section_id: str = "industry",
           need_id: str = "n-1", report_as_of: str | None = "2026-09-26"):
    return (builder or ANB.FrozenAspectInformationNeedBuilder()).build(
        aspect, need_id=need_id, company_id="c-1", section_id=section_id,
        report_as_of=report_as_of)


def _route_of(need, context) -> tuple[str | None, str | None]:
    """真实路由核心的 (route, reason_code)。用 `_route`（不落盘审计）——本模块不写任何文件。"""
    result = RR._route(need, context)
    decision = result.decision
    if decision is None:
        return None, result.reason_code
    return decision.route, decision.reason_code


def _code_body(path: Path) -> str:
    """去掉**模块 docstring** 之后的代码体（docstring 里会提到历史/禁用形态，那不是实现）。"""
    src = path.read_text(encoding="utf-8")
    first = src.index('"""')
    return src[src.index('"""', first + 3) + 3:]


def main() -> dict:
    passed = 0
    failed = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    builder = ANB.FrozenAspectInformationNeedBuilder()
    runner_src = RUNNER.read_text(encoding="utf-8")
    runtime_src = RUNTIME.read_text(encoding="utf-8")

    # ==================================================================
    # 1. 正面：冻结 external 声明**真的**走到路由判定
    # ==================================================================
    check(ANB.ASPECT_NEED_BUILDER_VERSION == BUILDER_VERSION
          and builder.version == BUILDER_VERSION,
          "构造器版本必须是 anb-1（映射规则变了必须前进）")
    aspect = _external_aspect()
    need = _build(aspect)
    check(list(need.required_source_types) == ["external"],
          f"冻结 external 必须原样进入 need（实得 {list(need.required_source_types)}）")
    check(RR.requires_external_source(need) is True,
          "外部路由判据必须成立（这正是 acc-29 恒假的那一跳）")
    # 真实路由（`_route` 不落盘审计，离线安全）：问题文字里**没有**任何外部来源词，
    # 因此落到 EXTERNAL_RESEARCH 只可能来自显式来源类。
    route, reason = _route_of(need, _context())
    check(route == "EXTERNAL_RESEARCH" and reason == "EXPLICIT_EXTERNAL_RECENCY",
          f"显式外部来源类必须真的把路由落到 EXTERNAL_RESEARCH（实得 {route}/{reason}）")
    check(not any(t in need.question for t in RR._EXTERNAL_SOURCE_TERMS)
          and not any(t in need.question for t in RR._DEEP_TERMS),
          "本正例的问题文字里不得含外部来源词/深信号词（否则「来源类起了作用」这句话读不出来）")

    # 对照：同一句问题由**旧结构件**投出 ⇒ 不落外部路由。差异只能来自来源类。
    from evals.test_demo_topic_runtime import _NeedBuilder as _StructuralStub

    stub_need = _StructuralStub().build(aspect, need_id="n-stub", company_id="c-1",
                                       section_id="industry", report_as_of="2026-09-26")
    check(list(stub_need.required_source_types) == []
          and RR.requires_external_source(stub_need) is False
          and _route_of(stub_need, _context())[0] != "EXTERNAL_RESEARCH",
          "旧结构件投出的同一句问题**不得**落外部路由（对照面：差异来自来源类）")

    # 反例：只声明本地来源类 ⇒ 不得触发外部路由。
    local_need = _build(_local_aspect(), need_id="n-2")
    local_route, _ = _route_of(local_need, _context())
    check(list(local_need.required_source_types) == ["company_industry"]
          and RR.requires_external_source(local_need) is False
          and local_route == "STANDARD_RAG",
          f"本地来源类的 aspect 不得落外部路由（实得 {local_route}）")

    # 混合需求：同一声明里既有外部又有本地 ⇒ 两侧都带出，`is_mixed_need` 成立
    # （harness 才需要拆本地子 need，母 need 仍走外部）。
    mixed = _aspect(aspect_id="ind.mixed", requirement_text="行业规模与公司自身经营情况",
                    refs=(_ref(requirement_id="er-mix", source_classes=("external",
                                                                        "company_industry")),))
    mixed_need = _build(mixed, need_id="n-3")
    check(list(mixed_need.required_source_types) == ["company_industry", "external"]
          and RR.is_mixed_need(mixed_need, _context()) is True,
          f"混合来源类必须两侧都带出且 `is_mixed_need` 成立（实得 "
          f"{list(mixed_need.required_source_types)}）")

    # ==================================================================
    # 2. 唯一真值：逐 aspect 与权威派生器**相等**（不自建第二套判据）
    # ==================================================================
    authority_ext = TS.EvidenceAuthorityPolicy(
        required_any_of=(TS.SourceClassGroup(source_classes=("external",)),
                         TS.SourceClassGroup(source_classes=("announcement",))),
        supplemental_only=("company_industry",))
    cases = (
        ("仅 ref.source_classes", _external_aspect(aspect_id="a-r")),
        ("authority 分支", _aspect(aspect_id="a-auth", requirement_text="行业排名变化",
                                  refs=(_ref(requirement_id="er-auth", authority=authority_ext),))),
        ("本地来源类", _local_aspect(aspect_id="a-l")),
        ("无冻结引用", _aspect(aspect_id="a-none", requirement_text="未声明来源",
                              refs=())),
    )
    for label, snap in cases:
        derived = list(TS.derive_support_eligibility(snap).required_source_classes)
        got = _build(snap, need_id="n-" + label)
        check(list(got.required_source_types) == derived
              and got.metadata["required_source_classes"] == derived,
              f"[{label}] need 的来源类必须与派生器**逐字相等**（派生 {derived}，实得 "
              f"{list(got.required_source_types)}）")
    # authority 分支的实质：ref.source_classes 为空，来源类**只**能从 authority 组里来。
    auth_need = _build(cases[1][1], need_id="n-auth")
    check(list(auth_need.required_source_types) == ["announcement", "external"]
          and "company_industry" not in auth_need.required_source_types,
          f"authority 分支必须走 required_any_of 的并集，且 `supplemental_only` **不得**被抬成 "
          f"required（实得 {list(auth_need.required_source_types)}）")
    # 无引用 ⇒ 空来源类（不构造伪 need）。
    none_need = _build(cases[3][1], need_id="n-none")
    check(list(none_need.required_source_types) == []
          and list(none_need.required_evidence_types) == [],
          "无冻结引用的 aspect 必须保持空（不得构造伪 need）")

    # ==================================================================
    # 3. 刻意不投的东西必须是**具名**限制，且限制是活的
    # ==================================================================
    check(list(need.required_evidence_types) == [],
          "`required_evidence_types` 必须保持空：ref 不带 evidence_kind，禁止 `external → web` 手写映射")
    check(need.time_scope is None,
          "`time_scope` 必须保持 None：冻结值是窗口 token，不是可比日期")
    # 限制是**活的**：真把窗口 token 塞进 need，路由会读成「无法解析」并交 fallback——
    # 也就是说这条限制不是洁癖，静默填进去就是一个真实缺陷。
    leaked = RS.InformationNeed(
        need_id="n-leak", section_id="industry", question="行业规模或代理指标",
        required_evidence_types=[], required_source_types=["external"],
        time_scope=_FROZEN_WINDOW_TOKEN, priority="primary", depends_on=[], metadata={})
    check(RR._time_scope_unparseable(leaked) is True,
          "把冻结窗口 token 当日期塞进 need 必须被读成「无法解析」（限制是活的，不是洁癖）")
    check(list(need.depends_on) == [],
          "`depends_on` 必须保持空：冻结 aspect 不承载 need 间依赖，凭空连边会制造父子关系")
    # 三条具名限制必须随 need 的 metadata 走，读者不必去读源码才知道少了什么。
    check(tuple(need.metadata["limitations"]) == ANB.ASPECT_NEED_LIMITATION_CODES
          and ANB.ASPECT_NEED_LIMITATION_CODES == (
              "evidence_kind_not_carried_by_ref",
              "frozen_time_scope_is_window_token_not_date"),
          f"具名限制码必须是那两条封闭值（实得 {list(need.metadata['limitations'])}）")
    check(need.metadata["deriver"] == "TS.derive_support_eligibility"
          and need.metadata["source"] == "frozen_evidence_requirement_ref"
          and need.metadata["builder_version"] == BUILDER_VERSION,
          "metadata 必须写明派生器 / 来源 / 构造器版本（来源类从哪来必须可回查）")
    check(need.metadata["aspect_id"] == "ind.industry_scale"
          and need.metadata["topic_id"] == "t-industry"
          and need.metadata["company_id"] == "c-1",
          "metadata 必须带四轴身份（aspect / topic / company），不得只剩一个 builder 名")

    # ==================================================================
    # 4. 不得升格：本地来源类不得凭空变 external；构造器不可能产出 ExternalFact
    # ==================================================================
    check("external" not in _build(_local_aspect(aspect_id="a-local2"), need_id="n-l2")
          .required_source_types,
          "只声明本地来源类的 aspect 不得凭空得到 external（不得升格）")
    body = _code_body(BUILDER)
    check("external" not in body,
          "构造器代码体内不得出现 `external` 字面量（来源类只能来自派生器，不能硬编码）")
    for forbidden in ("tools", "requests", "urllib", "httpx", "bocha", "llm"):
        check(forbidden not in body.lower(),
              f"构造器不得触网/调 LLM/建 ExternalFact：代码体里不得出现 `{forbidden}`")
    check("ExternalFact" not in body and "external_facts" not in body,
          "构造器不得产出 ExternalFact（发行人在年报里转录的数字不得由此升格为外部事实）")

    # ==================================================================
    # 5. 授权门：三个外部动作执行前拒绝（离线预检，不发任何请求）
    # ==================================================================
    external_routed = tuple(sorted(a for a, routes in A.ACTION_ROUTES.items()
                                   if "EXTERNAL_RESEARCH" in routes))
    check(tuple(sorted(P.EXTERNAL_ACTIONS)) == external_routed,
          f"授权门覆盖的动作必须与 ACTION_ROUTES 里路由到 EXTERNAL_RESEARCH 的动作**恰好相等**"
          f"（路由表 {external_routed}，门 {tuple(sorted(P.EXTERNAL_ACTIONS))}）")
    for action in P.EXTERNAL_ACTIONS:
        check(P.external_research_unauthorized(action, None)
              == P.EXTERNAL_NOT_AUTHORIZED_REASON,
              f"[{action}] 无上下文（没有授权声明）必须拒绝：不能把「不知道」读成「允许」")
        check(P.external_research_unauthorized(action, _context(enabled=False))
              == P.EXTERNAL_NOT_AUTHORIZED_REASON,
              f"[{action}] 开关为假必须执行前拒绝")
        check(P.external_research_unauthorized(action, _context(enabled=True)) is None,
              f"[{action}] 开关为真时才允许（否则这道门是常量，读不出授权与否）")
    for action in ("SEARCH_LOCAL", "INSPECT_EVIDENCE", "LOOKUP_FINANCIAL_METRIC"):
        check(P.external_research_unauthorized(action, None) is None,
              f"[{action}] 本地动作不得被这道门波及（哪怕没有上下文）")
    check(P.external_research_unauthorized("SEARCH_EXTERNAL", _NS(company_id="c-1"))
          == P.EXTERNAL_NOT_AUTHORIZED_REASON,
          "畸形上下文（缺 `external_research_enabled` 字段）必须 fail-closed 拒绝，不得读成允许")
    check(P.external_research_unauthorized("SEARCH_EXTERNAL", _NS(external_research_enabled=1))
          == P.EXTERNAL_NOT_AUTHORIZED_REASON,
          "非 bool 的开关值必须 fail-closed 拒绝（`1 is not True`）")
    check(P.EXTERNAL_NOT_AUTHORIZED_SOURCE == "external_research_not_authorized"
          and P.EXTERNAL_NOT_AUTHORIZED_REASON == "EXTERNAL_RESEARCH_NOT_AUTHORIZED",
          "拦截点（source）与结论（reason）必须是分开的两个封闭值")
    # F4 未被顶掉：重复外部搜索拦截仍然在位。
    check(P.external_search_blocked(_NS(tool_history=())) is None
          and P.external_search_blocked(_NS(tool_history=(
              _NS(result=_NS(tool_name="search_external_sources",
                             data={"results": [{"url": "https://example.invalid/a"}]})),)))
          == "EXTERNAL_SEARCH_HAS_UNFETCHED_CANDIDATES",
          "F4「已有未 fetch 候选时禁止重复搜索」的规则不得被新门顶掉（两条规则各管各的）")

    # ==================================================================
    # 6. 现场接线对账（源码文本；对账的是本仓自己的代码）
    # ==================================================================
    check("unauthorized = P.external_research_unauthorized(action.action, context)"
          in runtime_src,
          "runtime 必须按**动作 + 现场上下文**调授权门（不得在 runtime 另写一个字面量判断）")
    gate_at = runtime_src.index("unauthorized = P.external_research_unauthorized(")
    check(gate_at < runtime_src.index("result = registry.execute(call, route=route"),
          "授权门必须在 `registry.execute` **之前**（执行前拒绝，不是执行后再停）")
    gate_block = runtime_src[gate_at:gate_at + 1400]
    check('"source": P.EXTERNAL_NOT_AUTHORIZED_SOURCE' in gate_block,
          "拒绝必须留痕，且 `source` 用封闭值（否则账本里出现「没调过」而无人认领）")
    check("continue" in gate_block,
          "拒绝后必须 `continue`（不得顺流执行）")
    check('T.emit(run_id, need.need_id, "EXTERNAL_RESEARCH_NOT_AUTHORIZED"' in gate_block,
          "拒绝必须发 trace（可复核，不靠日志反推）")
    # 工厂只产生产实现（acc-29 的断点不得悄悄回来）。
    factory_at = runner_src.index("def _information_need_builder")
    factory = runner_src[factory_at:factory_at + 1600]
    factory_code = factory[factory.index('"""', factory.index('"""') + 3) + 3:]
    check("from harness.aspect_need_builder import FrozenAspectInformationNeedBuilder"
          in factory_code and "_NeedBuilder" not in factory_code,
          "工厂必须只 import 生产实现（结构替身不得再出现在注入点）")
    check('"need_side"' in runner_src and "aspects_carrying_external_in_need" in runner_src,
          "报告必须回读需要侧（否则「要求到了没有」只能靠人读源码判断")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    sys.exit(1 if outcome["failed"] else 0)

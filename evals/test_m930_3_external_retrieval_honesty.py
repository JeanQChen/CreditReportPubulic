"""M930-3 §三/3 反例集：外部来源要求**到达了链路**、而本轮**未检索**，两件事必须分开写清楚。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_external_retrieval_honesty`

背景（`acc-29` 的现场）：冻结 Contract 声明某些 aspect 的来源类**只能是** `external`，而正式
`InformationNeed` **从未**把这条要求交给 Router/ToolRegistry（`r5` 的三份产物里 `external` 出现
**0** 次）。当时的两个原因是：(a) 开关为关；(b) 注入的 need 构造器是纯结构件，丢弃了冻结
`source_classes`。`acc-30`（定点返修步骤 ③）把 (b) **接上了**——注入换成生产实现
`harness.aspect_need_builder.FrozenAspectInformationNeedBuilder`，它用**同一个**权威派生器
`TS.derive_support_eligibility(...).required_source_classes` 把冻结来源类搬进正式 need。
(a) 仍在：本批无联网授权，且开关现在是**真门**（`harness.policies.external_research_unauthorized`
在工具执行前拒掉三个外部动作并留痕）。

测的是四面，缺任何一半都等于没修：

1. **声明侧**：冻结 `EvidenceRequirementRef` 说哪些 aspect 只能取外部来源（由
   `TS.derive_support_eligibility` **重算**，不硬编码任何主题/栏目名）。
2. **需要侧**（`acc-30` 新增）：那些声明**真的到达了 need** —— 用**同一个**注入构造器重建一次，
   读 `required_source_types` 与真实 `routing.router.requires_external_source()`。两侧必须同源
   （声明了 external ⟺ need 带出 external），不一致要如实报出来。
3. **尝试侧**：Pack 里有没有外部漏斗痕迹。没有 = 本轮**没有执行过**外部检索。
4. **终态是封闭枚举、二者互斥**：`not_retrieved` 与 `retrieved_none_adoptable`，后者必须有正面
   证据。原因码是**封闭集**（list，两条原因各自独立，不得压成一个字符串）。

反例面（本模块不通过即失败的那些）：把「未检索」写成「已证明没有」或写成「已检索未取得」；
把声明侧与需要侧与尝试侧合并成一条结论；让终态/原因字段变成常量；让已知结构替身**再次**
悄悄顶替生产实现而无人发现；让发行人转录的第三方数字升格为已验证外部事实。

夹具全部是合成对象：不读真实库、不建真实 Pack、不调 LLM、**不联网**。唯一的例外是**源码文本
对账**（注入点、报告接线、生产构造器的来源类判据），它们对账的是本仓自己的代码。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from harness import topic_schema as TS
from planning import schema as PS
from routing import router as RR
from sections import research_common as RC

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "evaluation" / "run_m930_3_acceptance.py"
_SHA = "0" * 64


class _NS:
    """只读命名空间替身（产物函数只按属性名读取容器，不要求真 Pack/真库对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _aspect(*, aspect_id: str, topic_id: str, requirement_text: str,
            source_classes: tuple[str, ...]) -> _NS:
    """一个 aspect 快照替身：`evidence_requirement_ids` 里放**真类**的冻结引用。

    用真 `EvidenceRequirementRef` 而不是手写 dict：`derive_support_eligibility` 读的正是它的
    `source_classes`，替身只负责「承载」，不替它决定读什么。
    """
    ref = TS.EvidenceRequirementRef(
        requirement_id="er-%s" % aspect_id, contract_sha256=_SHA,
        requirement_fingerprint=_SHA, schema_version="er-ref/1",
        source_classes=tuple(source_classes))
    return _NS(aspect_id=aspect_id, topic_id=topic_id, requirement_text=requirement_text,
               evidence_requirement_ids=(ref,))


def _pack(*, external_funnel=None, external_facts=()) -> _NS:
    return _NS(external_funnel=external_funnel, external_facts=tuple(external_facts))


def _state(*, aspects: tuple, packs: tuple, enabled: bool = False,
           builder: object = None) -> ACC.RunState:
    """只提供 `_external_retrieval_honesty` 真正读的那几个面（其余留空，不冒充真现场）。"""
    requirement = _NS(aspects=tuple(aspects))
    task = _NS(topic_ids=("t-industry",))
    inputs = _NS(tasks={"industry": task},
                 requirements={"t-industry": requirement},
                 pack_sets={"industry": _NS(packs=tuple(packs))},
                 external_research_enabled=enabled,
                 information_need_builder=(ACC._information_need_builder()
                                           if builder is None else builder))
    return ACC.RunState(inputs=inputs, run_dir=REPO, mode=ACC.MODE_OFFLINE,
                        generated_at="2026-09-26T00:00:00Z", policy=_NS())


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

    runner_src = RUNNER.read_text(encoding="utf-8")

    # ==================================================================
    # 1. 常量：原因码是**封闭集**，终态是**封闭枚举**，版本号是声明值
    # ==================================================================
    check(ACC.EXTERNAL_RESEARCH_ENABLED is False,
          "本轮 `EXTERNAL_RESEARCH_ENABLED` 必须是 False（授权范围：更窄，不是放宽）")
    check(ACC.EXTERNAL_RETRIEVAL_REASON_CODES == (
              "information_need_builder_stub_drops_source_classes",
              "external_research_not_authorized_this_run"),
          "原因码必须是那两条封闭值（换码即换语义，必须一起改测试与文档）")
    check(ACC.EXTERNAL_RETRIEVAL_NOT_CONNECTED
          == "information_need_builder_stub_drops_source_classes",
          "旧常量名必须保留并指向历史那条「那一跳没接」的码")
    check(ACC.EXTERNAL_TERMINAL_NOT_RETRIEVED == "not_retrieved"
          and ACC.EXTERNAL_TERMINAL_RETRIEVED_NONE_ADOPTABLE == "retrieved_none_adoptable",
          "终态必须是那两个封闭值")
    check(ACC.EXTERNAL_RETRIEVAL_SCHEMA_VERSION == "external-retrieval/2",
          "`external_retrieval` 块的 schema 版本必须是 external-retrieval/2"
          "（/1 → /2 的理由是读法变了：单一原因码 → 原因码封闭集 + 终态枚举）")
    check(ACC.RUNNER_VERSION == "m930-3-acc-40"
          and ACC.ACCEPTANCE_REPORT_SCHEMA_VERSION == "m930-3-acc-report-34",
          "本段版本号必须停在 acc-40 / report-34（接线生产 need 构造器 + 授权门之后依次是："
          "r7b 后定点返修第一段（A2 单元格判据改成已裁决的分量规则，acc-31 / report-30）；"
          "定点批 §一.2 的逐候选裁出（acc-32 / report-31）；定点批 §一.2 的读者面"
          "（主体名称可核实，acc-33 / report-32）；指令 D §二（b）来源归属轴的读者面"
          "（`srattr-1`：新增 `source_attribution.json` / `.md` 与报告侧指针块，acc-34 / "
          "report-33）；指令 D §三失败侧的门前留存与诊断（`pgr-1`："
          "`proposal-set-rejections/8` → `/9`，新增 `pre_gate_draft.json` / `.md` 与报告侧"
          "指针块，acc-35 / report-34）；业务取材纵链修复 §一 的逐条 typed 栏目未达原因"
          "（诊断逐 aspect 新增 `column_unmet`，`failure-diagnostics/2` → `/3`，acc-36 / "
          "report-34——报告载荷未变，故报告 schema 停在 34）；业务取材纵链修复 §二 的**表对象"
          "信道读法**（材料包读回不再把表对象印成「内容形态：读不出」，另成 `kind=table_object` "
          "一族并读同信封的 `reading_policy`，`material-pack/3` → `/4`，acc-37 / report-34"
          "——报告载荷同样未变）；业务取材纵链修复 §三 的**替身选材与组织**（过长原句按小句"
          "边界切成有界原子逐条过滤；候选原子必须自带陈述对象，无主语残片不再进正文并逐条记 "
          "typed 原因；材料按来源角色稳定重排后提案；组织侧每个接缝各取一个中性连接语。"
          "只改替身提案与行文，门一律不动，acc-38 / report-34——报告载荷同样未变）；"
          "§0.18 W8 单通道的**读取面**（目标表的正式材料改由图侧单通道产出，信封种类由 "
          "`tom-1` 换成 `gtm-1`，读回侧现同时认两条并带出实际观察到的 `envelope_kind`，"
          "`material-pack/4` → `/5`，acc-39 / report-34——报告载荷同样未变）；"
          "M930-3 定点业务纠正①的**勾选行栏目归属**（读法 `tmr-2` → `tmr-3`：所问事项**只**取行内"
          "前缀，行内没写主语时这一列留空，**不再**回指所在节点标题，空所问事项 fail-closed 且"
          "材料一份未删；报告载荷形状未变，报告 schema 仍停在 report-34。"
          "acc-40 / report-34——报告载荷同样未变）。"
          "**本模块检查的 `external_retrieval` 块在这些段里一字未改**，"
          "升版只是让读者知道同一份报告里 A2 的判读窗口、`subject_declaration`、来源归属指针"
          "与失败侧诊断换了一版）")

    # ==================================================================
    # 2. 需要侧：生产构造器**真的**把冻结来源类搬进了正式 need（acc-30 的核心）
    # ==================================================================
    from harness.aspect_need_builder import FrozenAspectInformationNeedBuilder
    from evals.test_demo_topic_runtime import _NeedBuilder as _StructuralStub

    aspect = _aspect(aspect_id="ind.industry_scale", topic_id="t-industry",
                     requirement_text="行业规模或代理指标", source_classes=("external",))
    local_aspect = _aspect(aspect_id="ind.local", topic_id="t-industry",
                           requirement_text="本地栏目", source_classes=("company_industry",))
    production = ACC._information_need_builder()
    check(isinstance(production, FrozenAspectInformationNeedBuilder),
          "工厂必须产出**生产实现**（不得再注入结构替身）")
    prod_need = production.build(aspect, need_id="n-1", company_id="c-1",
                                 section_id="industry", report_as_of="2026-09-26")
    check(list(prod_need.required_source_types) == ["external"],
          f"生产 need 必须带出冻结的 external 来源类（实得 {list(prod_need.required_source_types)}）")
    check(RR.requires_external_source(prod_need) is True,
          "生产 need 必须真的触发外部路由判据（这正是 acc-29「那一跳没接」的修复面）")
    # 反例①：只声明本地来源类的 aspect 不得凭空得到 external。
    local_need = production.build(local_aspect, need_id="n-2", company_id="c-1",
                                  section_id="industry", report_as_of="2026-09-26")
    check(list(local_need.required_source_types) == ["company_industry"]
          and RR.requires_external_source(local_need) is False,
          "本地来源类的 aspect 不得凭空带出 external（否则需要侧会虚高）")
    # 反例②：无冻结 ref 的 aspect 保持空 —— 不构造伪 need。
    bare = production.build(_NS(aspect_id="ind.bare", topic_id="t-industry",
                                requirement_text="无冻结引用", evidence_requirement_ids=()),
                            need_id="n-3", company_id="c-1", section_id="industry",
                            report_as_of="2026-09-26")
    check(list(bare.required_source_types) == [] and list(bare.required_evidence_types) == [],
          "无冻结引用的 aspect 必须保持空（不得构造伪 need）")
    # 反例③：`evidence_kind` **不得**由 `source_classes` 反推（external → web 是手写映射）。
    check(list(prod_need.required_evidence_types) == [],
          "`required_evidence_types` 必须保持空：冻结 ref 不带 evidence_kind，禁止手写映射")
    # 反例④：冻结 `time_scope` 是**窗口枚举 token**，不得当日期塞进 need。
    check(prod_need.time_scope is None,
          "`time_scope` 必须保持 None：冻结值是窗口 token，不是可比日期")
    check("frozen_time_scope_is_window_token_not_date" in prod_need.metadata["limitations"]
          and "evidence_kind_not_carried_by_ref" in prod_need.metadata["limitations"],
          "两条具名限制必须随 need 的 metadata 带出（否则读者会以为限制不存在）")
    check(prod_need.metadata["deriver"] == "TS.derive_support_eligibility",
          "metadata 必须写明来源类由哪个派生器给出（唯一的真值来源）")

    # 对照面：**已知结构替身仍然丢弃来源类** —— 历史那条原因码不是凭空的。
    stub_need = _StructuralStub().build(aspect, need_id="n-1", company_id="c-1",
                                       section_id="industry", report_as_of="2026-09-26")
    check(list(stub_need.required_source_types) == []
          and list(stub_need.required_evidence_types) == []
          and stub_need.time_scope is None,
          "结构替身必须**确实**丢弃来源类与证据类型（否则历史原因码是假的）")
    check(RR.requires_external_source(stub_need) is False,
          "结构替身产出的 need 不得触发外部路由（这正是 acc-29 那条原因的机制）")

    # 正例：**判据本身是好的** —— 同一句话若由真构造器投出，外部路由当场成立。
    # 少了这个正例，「判据恒假」会被读成「判据坏了」，修法就找错地方了。
    real_need = RC.build_need(
        PS.SectionTask(task_id="tk-industry", plan_id="p-1", section_id="industry",
                       title="行业", purpose="industry", research_policy="deep",
                       topic_ids=("t-industry",), questions=(), output_requirements=(),
                       evaluation_rule_ids=(), allowed_capabilities=(), blocking_rules=()),
        PS.PlannedQuestion(question_id="q-industry", question="行业规模是多少？",
                           priority="primary", topic_id="t-industry",
                           evidence_requirements=({"evidence_kind": "web",
                                                   "source_classes": ["external"]},)),
        report_as_of="2026-09-26")
    check(list(real_need.required_source_types) == ["external"]
          and RR.requires_external_source(real_need) is True,
          "章节侧真构造器投出的 need 必须带出 external 并触发外部路由（判据没坏）")

    # ==================================================================
    # 3. 声明侧：从**冻结引用重算**，不在报告里硬编码主题/栏目名
    # ==================================================================
    honesty = ACC._external_retrieval_honesty(_state(aspects=(aspect, local_aspect),
                                                    packs=(_pack(),)))
    check(honesty["schema_version"] == ACC.EXTERNAL_RETRIEVAL_SCHEMA_VERSION,
          "块里必须带自己的 schema 版本")
    check(honesty["aspects_declaring_external"] == ["ind.industry_scale"],
          "声明侧必须只列出**真的**声明了 external 的那些 aspect（实得 "
          f"{honesty['aspects_declaring_external']}）")
    rows = honesty["requirement_side"]["industry"]["rows_declaring_external"]
    check(len(rows) == 1 and rows[0]["aspect_id"] == "ind.industry_scale"
          and rows[0]["required_source_classes"] == ["external"]
          and rows[0]["source"] == "frozen_evidence_requirement_ref",
          "声明侧逐行必须是「从冻结引用重算」的读数，并写明来源是冻结引用")
    check(honesty["requirement_side"]["industry"]["aspect_count"] == 2,
          "声明侧必须报出本节 aspect 总数（否则「只有 1 个」与「只列了 1 个」分不开）")
    # 反例：本地来源类不得被算成 external（否则声明侧会虚高）
    check(all("external" in r["required_source_classes"] for r in rows),
          "`rows_declaring_external` 里不得混进不含 external 的行")

    # ==================================================================
    # 3b. 需要侧回读：声明侧与需要侧**同源**，且不一致要被报出来
    # ==================================================================
    check(honesty["aspects_carrying_external_in_need"] == ["ind.industry_scale"],
          "需要侧必须只列出 need 真的带出 external 的那些 aspect（实得 "
          f"{honesty['aspects_carrying_external_in_need']}）")
    need_side = honesty["need_side"]
    check(need_side["builder_is_production_implementation"] is True,
          "现场构造器是生产实现时必须如实报 True")
    check(need_side["declared_and_need_sides_agree"] is True
          and need_side["mismatches"] == [],
          "两侧同源时不得报出不一致")
    need_rows = need_side["sections"]["industry"]["rows"]
    check(len(need_rows) == 2
          and all(r["declared_external"] == r["requires_external_source"] for r in need_rows),
          "逐 aspect 必须满足「声明了 external ⟺ need 带出 external」")
    check(any(r["aspect_id"] == "ind.industry_scale"
              and r["required_source_types"] == ["external"] for r in need_rows),
          "需要侧逐行必须给出该 aspect 的 need 真实来源类")
    # 反例：把注入构造器换成会**谎报**来源类的替身 ⇒ 两侧不一致必须被点名（不是静默同义）。
    class _Lying:
        version = "lying-1"

        def build(self, aspect, *, need_id, company_id, section_id, report_as_of):
            return _NS(required_source_types=[], required_evidence_types=[], time_scope=None)

    _Lying.__module__ = "harness.aspect_need_builder"
    lying = ACC._external_retrieval_honesty(_state(aspects=(aspect,), packs=(_pack(),),
                                                   builder=_Lying()))
    check(lying["need_side"]["declared_and_need_sides_agree"] is False
          and len(lying["need_side"]["mismatches"]) == 1
          and lying["need_side"]["mismatches"][0]["aspect_id"] == "ind.industry_scale",
          "声明了 external 而 need 没带出来时必须**报出不一致**，不得静默放过")
    check(lying["aspects_carrying_external_in_need"] == [],
          "谎报替身下需要侧必须如实为空（不得沿用声明侧的值）")
    # 反例另一面：注入的不是生产实现时不回读（不对任意替身二次调用）。
    foreign = ACC._external_retrieval_honesty(_state(aspects=(aspect,), packs=(_pack(),),
                                                     builder=object()))
    check(foreign["need_side"]["builder_is_production_implementation"] is False
          and foreign["need_side"]["sections"]["industry"]["available"] is False
          and foreign["need_side"]["sections"]["industry"]["reason"]
          == "builder_is_not_the_production_implementation"
          and foreign["need_side"]["sections"]["industry"]["aspect_count_read_back"] == 0,
          "非生产实现必须如实记 available=false 且不回读（不得对任意替身二次调用）")
    check(foreign["aspects_carrying_external_in_need"] == [],
          "不回读时需要侧必须为空，不得用声明侧顶替")

    # ==================================================================
    # 4. 尝试侧：**无漏斗 = 本轮未检索**；有漏斗 = 已尝试（证明它不是常量）
    # ==================================================================
    check(honesty["external_retrieval_attempted"] is False,
          "Pack 里没有外部漏斗痕迹时，尝试侧必须是 False")
    check(honesty["attempt_side"]["industry"] == {
        "topic_count": 1, "packs_with_external_funnel": 0, "external_fact_count": 0},
          f"尝试侧必须逐节给出三个计数（实得 {honesty['attempt_side']}）")
    attempted = ACC._external_retrieval_honesty(_state(
        aspects=(aspect,), packs=(_pack(external_funnel=_NS(snapshot_id="ef-1")),)))
    check(attempted["external_retrieval_attempted"] is True
          and attempted["attempt_side"]["industry"]["packs_with_external_funnel"] == 1,
          "有外部漏斗痕迹时必须如实变成 True（否则这个字段是常量，读不出真假）")
    # 声明侧与尝试侧互不决定：同一个声明在两种尝试下都是同一条（否则两侧会被合并成一条结论）。
    check(attempted["aspects_declaring_external"] == honesty["aspects_declaring_external"],
          "尝试侧变了不得改动声明侧：两侧是两条各自可读的事实")

    # ==================================================================
    # 4b. 终态（封闭枚举，互斥）：`not_retrieved` 不得与任何「已检索」表述同时出现
    # ==================================================================
    check(honesty["terminal_state"] == ACC.EXTERNAL_TERMINAL_NOT_RETRIEVED,
          "没有执行痕迹时终态必须是 `not_retrieved`")
    check(attempted["terminal_state"] == ACC.EXTERNAL_TERMINAL_RETRIEVED_NONE_ADOPTABLE,
          "执行过但没有可采用事实时终态必须是 `retrieved_none_adoptable`（正面证据）")
    check(honesty["terminal_state"] != attempted["terminal_state"],
          "两个终态必须互斥（同一现场不得同时成立）")
    check("未检索" in honesty["statement"] and "执行过" not in honesty["statement"],
          "`not_retrieved` 时那句话不得含任何「执行过」的表述（否则两个终态被混成一句）")
    check("执行过" in attempted["statement"] and "已检索未取得" in attempted["statement"],
          "`retrieved_none_adoptable` 时那句话必须写明「已检索未取得」")
    check(attempted["statement"].startswith("本轮**执行过**外部检索"),
          "`retrieved_none_adoptable` 那句话必须以「本轮**执行过**外部检索」开头（首句即结论）")

    # ==================================================================
    # 5. 原因码是**封闭集**：两条原因各自独立，谁都不是常量
    # ==================================================================
    check(honesty["external_research_enabled"] is False,
          "块里必须原样报出本轮开关值")
    check(honesty["information_need_builder"].endswith(".FrozenAspectInformationNeedBuilder"),
          f"块里必须**指名**现场那个 need 构造器（实得 {honesty['information_need_builder']}）")
    check(honesty["not_connected_reason_codes"] == [ACC.EXTERNAL_RETRIEVAL_NOT_AUTHORIZED],
          "开关关 + 生产构造器时，原因码必须**只有**未授权那一条"
          f"（实得 {honesty['not_connected_reason_codes']}）")
    check(honesty["not_connected_reason_code_set"] == list(ACC.EXTERNAL_RETRIEVAL_REASON_CODES),
          "块里必须带出封闭取值集（读者据此判读现场值合不合法）")
    # 反例：把开关打开、构造器仍是**已知结构替身** ⇒ 另一条原因码单独成立（不是常量）。
    stub_only = ACC._external_retrieval_honesty(_state(aspects=(aspect,), packs=(_pack(),),
                                                       enabled=True,
                                                       builder=_StructuralStub()))
    check(stub_only["not_connected_reason_codes"]
          == [ACC.EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES],
          "开关开 + 结构替身时，原因码必须**只有**「那一跳没接」那一条"
          f"（实得 {stub_only['not_connected_reason_codes']}）")
    # 两条原因**可以同时成立**，且必须各自出现（合并成一条即丢信息）。
    both = ACC._external_retrieval_honesty(_state(aspects=(aspect,), packs=(_pack(),),
                                                  builder=_StructuralStub()))
    check(both["not_connected_reason_codes"] == list(ACC.EXTERNAL_RETRIEVAL_REASON_CODES),
          f"两条原因同时成立时必须都列出（实得 {both['not_connected_reason_codes']}）")
    # 反例另一面：生产构造器 + 开关打开 ⇒ 两条都没有（否则该字段恒非空，读不出「接上了没有」）。
    clear = ACC._external_retrieval_honesty(_state(aspects=(aspect,), packs=(_pack(),),
                                                   enabled=True))
    check(clear["not_connected_reason_codes"] == [],
          f"接上且授权时不得有任何原因码（实得 {clear['not_connected_reason_codes']}）")

    # 授权块：开关现在**是门**（执行前拒绝），不是一个报告位。
    auth = honesty["authorization"]
    check(auth["external_research_enabled"] is False
          and auth["external_actions_unauthorized"] is True,
          "授权块必须如实报出「本轮未授权」")
    check(auth["enforced_by"] == "harness.policies.external_research_unauthorized"
          and "harness.runtime" in auth["enforced_at"]
          and "执行前" in auth["enforced_at"],
          "授权块必须点名**执行前**的拦截点（否则「开关」会被读成装饰）")

    # ==================================================================
    # 6. 措辞：写的是「**本轮未检索**」，不是「已证明没有」
    # ==================================================================
    check(honesty["statement"].startswith("本轮**未检索**外部来源。")
          or honesty["statement"].startswith("本轮未检索外部来源。"),
          "主张那句话必须以「本轮未检索外部来源。」开头（首句即结论，不许绕）")
    check("不是" in honesty["statement"] and "已证明" in honesty["statement"],
          "同一句里必须就地挡住「已证明不存在可用外部来源」这个误读")
    check("行业没有可写内容" in honesty["statement"],
          "同一句里必须就地挡住「行业没有可写内容」这个误读")
    readings = "".join(honesty["readings"])
    check("本轮未检索" in readings and "不等于" in readings,
          "读法里必须写明「未检索 ≠ 已检索而没找到」")
    check("发行人" in readings and "转录" in readings and "不得" in readings,
          "读法里必须写明：发行人转录的第三方数字不得读成已验证的外部事实")
    check(ACC.EXTERNAL_RETRIEVAL_NOT_AUTHORIZED in readings
          and ACC.EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES in readings,
          "读法里必须点名封闭集里的两个码，并说明它们都不是「没有外部来源」的判定")
    check(any("声明" in r and "need" in r for r in honesty["readings"]),
          "读法里必须写明：声明侧与需要侧与尝试侧是三条各自可读的事实，不得合并成一条结论")
    # 块必须是可 JSON 往返的纯数据（要进 `manifest.json`）
    check(json.loads(json.dumps(honesty, ensure_ascii=False))["statement"] == honesty["statement"],
          "`external_retrieval` 块必须可 JSON 往返（落盘不许带 dataclass 实例）")

    # ==================================================================
    # 7. 现场接线（源码文本对账）：注入与报告**读同一处**，不出现第二个真值
    # ==================================================================
    check("external_research_enabled=EXTERNAL_RESEARCH_ENABLED)" in runner_src,
          "`RouteContext` 必须读模块常量，不许在注入点再写一个字面量 False")
    check("external_research_enabled=EXTERNAL_RESEARCH_ENABLED," in runner_src,
          "现场值必须随 `RealInputs` 带出（报告读现场，不读常量）")
    check("information_need_builder=need_builder," in runner_src,
          "注入点必须过工厂产出的那个实例（报告要按它的实际类型指名）")
    # acc-30：工厂必须产出**生产实现**，且结构替身不得再出现在注入点上。
    # 只看**代码体**（docstring 里会提到历史替身的名字，那不是接线）。
    factory_at = runner_src.index("def _information_need_builder")
    factory = runner_src[factory_at:factory_at + 1600]
    factory_code = factory[factory.index('"""', factory.index('"""') + 3) + 3:]
    check("FrozenAspectInformationNeedBuilder" in factory_code
          and "_NeedBuilder" not in factory_code,
          "工厂必须只产出生产实现，不得再 import 结构替身（否则 acc-29 的断点会悄悄回来）")
    check("from harness.aspect_need_builder import FrozenAspectInformationNeedBuilder"
          in factory_code,
          "生产实现必须从 `harness.aspect_need_builder` 取（唯一来源）")
    check(runner_src.count("_external_retrieval_honesty(state)") == 1,
          "`_external_retrieval_honesty(state)` 必须只算一次，两处产物读同一个结果")
    check('"external_retrieval": external_retrieval,' in runner_src,
          "清单里必须有 `external_retrieval` 块（工件指向同一份读数）")
    check("_external_retrieval_honesty(state)" in runner_src
          and runner_src.index("external_retrieval = _external_retrieval_honesty(state)")
          < runner_src.index("manifest = {"),
          "块必须在写 `manifest.json` **之前**算出来（否则清单只能读到一个 None）")

    result = {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    check(set(result) == {"passed", "failed", "skipped", "details"},
          "本模块的返回必须满足套件结果契约：四键齐备（缺 `skipped` 会中止整轮套件）")
    return result


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

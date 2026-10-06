"""M930-3D：`sections.company_worker` 写作相位（P15）的两条硬边界，逐条反例化。

§16.7.2 给本文件的口径是唯一两句话：**「Writer 相位不回写 Pack；`FollowUpNeed` 未裁决不得执行」**。
本文件因此只断言这两件事，不重复 `evals/test_demo_report_assembler.py`（组装器）与
`evals/test_demo_backbone_artifacts.py`（工件索引）已经覆盖的口径。

全部离线、确定性：权威输入由 `test_demo_report_assembler` 同族的**真实类型**合成工厂物化
（`VerifiedPackSet` / `TopicResearchPack` / `ResearchMaterial` / `TS.FollowUpNeed`），
生成器是返回结构化 `PW.NarrationResult` 的 stub；语义门是结构化 entailment stub。不读库、
不连网、不调真实 LLM、不新建第二套 Router/Harness/ToolRegistry/Store/LLM runtime。

    §0  静态边界：写作相位不持有第二套 runtime；写入面恰一处（§三 E 的 section 链落库入口，
        且必须经能力门取得），Pack store 在读门外零属性访问
    §1  Writer 相位不回写 Pack（权威对象逐字节不变 + 产物只引用输入容器）
    §2  `FollowUpNeed` 未裁决不得执行（相位级 + 相位分支级 fail-closed 全覆盖）
    §3  Harness 裁决层：被拒的 need 必须留下 typed 记录，不得静默丢弃
    §4  两个身份轴不得互填：`FollowUpNeed` ≠ gap，`FollowUpDecision` ≠ FND 去向
    §5  相位产物角色 ⊆ P14 `demo-artifact-index-v2` 的成员角色（版本齐步）

**如实记录的三处覆盖缺口**（不得当作已实现）：

  1. 「裁决通过 → 新 Pack → 基于新 Pack 有界重写一轮」的正向路径与「第二轮仍发出
     `FollowUpNeed` 即拒」的上界守卫，在**离线合成 Pack set** 上不可驱动：`store` 在位时
     `_assert_pack_set_is_current` 会先跑 `PSet.resolve_pack_set`，而读门会用
     `requirement.dependency_fingerprint()` 重算 Pack 的依赖指纹——合成 `VerifiedPackSet`
     携带的是测试常量 `"d"*64`，读门必然 block。本文件因此把「裁决能力」拆成两级：相位入口
     （§2.1，`store=None` 可驱动）+ 相位自己的分支函数（§2.2，绕过读门）+ Harness 裁决入口
     （§3，直接驱动）。这两条**不在本文件**覆盖，而在真库真 runtime 上各有独立证明：正向后继
     的完整性在 `evals/test_demo_pack_set.py` §6f，上界守卫在
     `evals/test_demo_writer_formal_chain.py` §3.5。**本文件不假装**跑过正向重写。
  2. §1 的「不回写」是**对象级**证明（权威对象逐字节不变 + 产物只回指输入容器），不是
     「store 在位时的 P4 复算也没写回」。`store` 在位的 P4 复算同样受缺口 1 的限制。
  3. **已修（§三 G）**：`sections/pack_set.py` 的 `resolve_pack_set` 曾先取 `store.resolver`
     再判 `None`，因此一个**根本没有** `resolver` 属性的 store 会抛 `AttributeError` 而不是
     docstring 承诺的 `PackSetError`。现在该取用收归 `pack_set.resolver_of`，三种情形
     （无 store / 无 resolver 能力 / resolver 为 None）统一走 typed `PackSetError`；§0 的
     「现状记录站」已改成正向断言，并新增写作相位入口的两条同纪律反例。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_report_assembler as FIXT
from harness import topic_runtime as TR
from harness import topic_schema as TS
from sections import backbone_artifacts as AB
from sections import company_worker as CW
from sections import narrative_schema as NS
from sections import pack_set as PSet
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import writing_spec as WS

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "sections" / "company_worker.py"
#: 写作相位区域的唯一分界串（完整赋值行，避免与 `phase_version=BACKBONE_WRITER_PHASE_VERSION`
#: 之类的使用点混淆）。
_WRITER_REGION_MARKER = 'BACKBONE_WRITER_PHASE_VERSION = "m930-3-writer-phase-v1"'


# ---------------------------------------------------------------------------
# 只读探针与离线注入替身（都不是第二套 runtime：没有 registry/路由/检索/Store 写入面）
# ---------------------------------------------------------------------------

def _budget() -> TR.ResearchBudgetPolicy:
    """冻结预算政策（本文件不靠放大预算换结论；上界逐项显式写死）。"""
    return TR.ResearchBudgetPolicy(
        policy_id="bp-m930-3-writer-phase", version="v1", tier="demo_backbone",
        max_need_rounds_per_aspect=2, max_need_rounds_per_topic=2,
        max_tool_calls_per_topic=8, max_tokens_per_topic=200000,
        max_llm_calls_per_topic=8, max_elapsed_ms_per_topic=900000,
        tree_max_spans_per_aspect=6, tree_max_chars_per_span=4000)


def _run_context(task, *, run_id: str = "m930-3-writer-phase-1") -> TR.TopicRunContext:
    """与 task/投影**同一身份**的真实 `TopicRunContext`（合成值，但类型与校验都是真的）。"""
    return TR.TopicRunContext(
        run_id=run_id, case_id=run_id + "-case", company_id=FIXT.COMPANY_ID,
        section_id=task.section_id, task_id=task.task_id,
        report_as_of=FIXT.REPORT_AS_OF,
        demo_scope_fingerprint=FIXT.SCOPE_INPUT_FINGERPRINT,
        projection_version=FIXT._projection_id(),
        document=TR.DocumentIdentity(
            company_id=FIXT.COMPANY_ID, document_id=FIXT.DOCUMENT_ID,
            document_version=FIXT.DOCUMENT_VERSION, evidence_set_version="esv-demo-1"),
        sources=TS.DocumentSourceSet.single_document(
            company_id=FIXT.COMPANY_ID, document_id=FIXT.DOCUMENT_ID,
            document_version=FIXT.DOCUMENT_VERSION, evidence_set_version="esv-demo-1"),
        budget_policy=_budget(), started_at="2026-09-22T00:00:00Z")


class _Sink:
    """只接收事件（`TraceSink` 协议面），不参与内容身份。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict) -> None:
        self.events.append((str(event_type), dict(payload)))


class _StoreSpy:
    """只实现边界③的**追加式**提交面，没有 Pack 写入面、没有 current 切换面。

    这本身就是「相位没有第二套 Store」的可观测证据：若相位试图提交 Pack / 切换 current /
    覆盖 need 行，这里没有对应方法，调用会立刻以 `AttributeError` 暴露，而不是静默成功。
    """

    def __init__(self) -> None:
        self.commits: list[tuple] = []

    def commit_follow_up_run(self, follow_up_run, needs, decisions):
        self.commits.append((follow_up_run, tuple(needs), tuple(decisions)))
        return follow_up_run.follow_up_run_id


class _ForbiddenResolver:
    """任何一次 `resolve` 都是缺陷：拒绝必须发生在**执行前**（预算/来源政策都还没被碰）。"""

    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, payload_ref):
        self.calls += 1
        raise AssertionError(
            "follow-up 在裁决阶段就被拒时，不得解析 SourcePolicy、不得执行任何研究")


def _deps(*, sink: _Sink, store, policy_resolver) -> TR.TopicRuntimeDependencies:
    """真实 `TopicRuntimeDependencies`，成员逐个显式注入（缺省即测试自身的失误）。"""
    return TR.TopicRuntimeDependencies(
        registry=None, llm=None, navigation=None, information_need_builder=None,
        route_context_builder=None, route_fn=None,
        budget_state=TR.TopicBudgetState(policy=_budget()),
        trace_sink=sink, store=store, payload_resolvers=(object(),),
        source_policy_resolver=policy_resolver,
        set_completeness_verifier=None, set_enumeration_verifier=None,
        clock=lambda: "2026-09-22T00:00:00Z")


def _writer_region_source() -> str:
    """`sections/company_worker.py` 的写作相位区域（从版本常量定义行到文件尾）。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    index = src.index(_WRITER_REGION_MARKER)
    return src[index:]


def _identity_snapshot(authority, task) -> dict:
    """权威输入的**只读**内容快照：容器身份 + 规范内容指纹 + 材料/事实逐条指纹 + 读视图。

    规范内容指纹走拥有者自己的规范口径（`PW._canonical_content_of` → `to_dict()` 的
    canonical 哈希），本文件不另造第二套「内容身份」。任何一处写回都会改变这里的比较结果。
    """
    packs: list[tuple] = []
    materials: list[tuple] = []
    facts: list[tuple] = []
    for pack in authority.pack_set.packs:
        packs.append((str(pack.pack_id), PW._canonical_content_of(pack)))
        for material in pack.materials:
            materials.append((str(pack.pack_id), str(material.material_id),
                              PW._canonical_content_of(material)))
        for fact in pack.facts:
            facts.append((str(pack.pack_id), str(fact.fact_id),
                          PW._canonical_content_of(fact)))
    scan = PW.scan_topic_pack(authority, task)
    return {
        "packs": tuple(packs), "materials": tuple(materials), "facts": tuple(facts),
        "authority_input_id": str(getattr(authority, "input_id", "") or ""),
        "pack_set_to_dict": PW._canonical_content_of(authority.pack_set),
        "fact_table": tuple((str(e.authority_kind), str(e.container_identity),
                             str(e.fact_id), str(e.material_id), str(e.text))
                            for e in scan.facts),
        "aspect_status": tuple(sorted((str(k), str(v))
                                      for k, v in dict(scan.aspect_status).items())),
    }


def _resolve_path(obj, dotted: str):
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


# ---------------------------------------------------------------------------
# 合成夹具：一个 section、一个 topic、三条 aspect（1 covered + 2 partial）
#
# 与 `test_demo_report_assembler.py` 的公司夹具同形（同族真实类型、同一身份口径），但
# 呈现更少事实：第三条 aspect 的权威事实**故意不呈现**，逼相位留下显式去向与缺口。
# ---------------------------------------------------------------------------

def _company_fixture() -> tuple:
    task = FIXT._task("company", (FIXT.TOPIC_BUSINESS,))
    aspects = (FIXT._AspectResult(FIXT.ASP_BUSINESS_MAIN, "covered"),
               FIXT._AspectResult(FIXT.ASP_BUSINESS_SALES, "partial"),
               FIXT._AspectResult(FIXT.ASP_BUSINESS_PROCUREMENT, "partial"))
    requirements = (FIXT._Req(FIXT.TOPIC_BUSINESS, (
        FIXT._AspectReq(FIXT.ASP_BUSINESS_MAIN, FIXT.TOPIC_BUSINESS, "q-company_business"),
        FIXT._AspectReq(FIXT.ASP_BUSINESS_SALES, FIXT.TOPIC_BUSINESS, "q-company_business"),
        FIXT._AspectReq(FIXT.ASP_BUSINESS_PROCUREMENT, FIXT.TOPIC_BUSINESS,
                        "q-company_business"),)),)
    facts = (FIXT._fact("f-main", "公司主营业务为动力电池系统的研发、生产与销售。",
                        (FIXT.ASP_BUSINESS_MAIN,)),
             FIXT._fact("f-sales", "公司销售模式以直销为主。", (FIXT.ASP_BUSINESS_SALES,)),
             FIXT._fact("f-procurement", "公司原材料采购以长期协议为主。",
                        (FIXT.ASP_BUSINESS_PROCUREMENT,)))
    authority = FIXT._authority(task, facts=facts, aspects=aspects,
                                requirements=requirements)
    return task, authority, PW.scan_topic_pack(authority, task)


def _presented_plan(scan, authority) -> dict:
    """**不**发出 `FollowUpNeed` 的门前提案束（合法章节）。"""
    pack_id = str(authority.pack_set.pack_for(FIXT.TOPIC_BUSINESS).pack_id)
    material_id = sorted(FIXT._material_ids_of(scan)[pack_id])[0]
    entries = FIXT._entries(scan)
    return FIXT._plan(
        candidates=[FIXT._cand("c-main", entries["f-main"].text,
                               FIXT._fact_edge(scan, "f-main")),
                    FIXT._cand("c-sales", entries["f-sales"].text,
                               FIXT._fact_edge(scan, "f-sales"))],
        units=[FIXT._unit("u-1", "本节就公司主营业务与销售模式作背景说明。",
                          context=[FIXT._context_edge(pack_id, material_id)])])


def _follow_up_need(scan, *, statement: str, revision: str = "r1",
                    source_class: str = "external", scope: tuple[str, ...] = ("subject",),
                    target_requirement_id: str = "") -> TS.FollowUpNeed:
    """真实 `TS.FollowUpNeed`（身份逐字由 `TS.derive_follow_up_need_id` 派生）。

    `budget_hint` 非空：检索侧（`TR.build_follow_up_focus`）对空白预算是 fail-closed 的，
    空串在这里不是「没有偏好」而是「这条诉求注定执行不了」——本文件要驱动的是**可裁决**的
    诉求，不是注定执行不了的那一种（那一种由 `evals/test_m930_3_rejection_retention.py` §6
    与 `evals/test_demo_pack_writer.py` §27d 逐条拒绝面覆盖）。
    """
    target = target_requirement_id or str(scan.requirement_ids[FIXT.TOPIC_BUSINESS])
    return TS.FollowUpNeed(
        need_id=TS.derive_follow_up_need_id(
            statement, target, FIXT.ASP_BUSINESS_PROCUREMENT, "company", revision),
        need_schema_version=TS.FOLLOW_UP_NEED_SCHEMA_VERSION, statement=statement,
        target_requirement_id=target, topic_id=FIXT.TOPIC_BUSINESS,
        question_id="q-company_business", aspect_id=FIXT.ASP_BUSINESS_PROCUREMENT,
        section_id="company", section_draft_revision=revision,
        contract_authorized_scope=tuple(scope), requiredness="required",
        expected_source_class=source_class, budget_hint="tree_inspect:1",
        writer_identity="wp-demo")


def _requesting_plan(scan, authority, *, statement: str) -> dict:
    """**发出** `FollowUpNeed` 的门前提案束（缺口不得无归属：申请指回真实需求）。

    `budget_hint` 非空，与请求示例和入站防线（`pw-19`）一致：空白预算的那一条会在写入
    边界被逐条 typed 拒绝，于是「相位必须拒绝未裁决的诉求」这条断言会因为**根本没有诉求**
    而假通过——本文件的样本必须是能走到裁决门的那一条。
    """
    plan = _presented_plan(scan, authority)
    plan["follow_up_needs"] = [{
        "statement": statement,
        "target_requirement_id": str(scan.requirement_ids[FIXT.TOPIC_BUSINESS]),
        "topic_id": FIXT.TOPIC_BUSINESS, "question_id": "q-company_business",
        "aspect_id": FIXT.ASP_BUSINESS_PROCUREMENT, "requiredness": "required",
        "expected_source_class": "external", "budget_hint": "tree_inspect:1"}]
    return plan


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:200]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:200]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    spec = WS.load_writing_spec(FIXT.SPEC_PATH)
    profile = PP.load_presentation_profile(FIXT.PROFILE_PATH)
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    module_src = MODULE_PATH.read_text(encoding="utf-8")
    writer_region = _writer_region_source()

    def _section_input(task, authority, **overrides) -> CW.BackboneWriterSectionInput:
        base = dict(task=task, authority=authority, projection=projection,
                    writing_spec=spec, presentation_profile=profile,
                    dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT)
        base.update(overrides)
        return CW.BackboneWriterSectionInput(**base)

    # ------------------------------------------------------------------
    # §0 静态边界：写作相位不持有第二套 runtime，也不持有任何写入面
    # ------------------------------------------------------------------
    import_lines = [ln.strip() for ln in module_src.splitlines()
                    if re.match(r"^\s*(from|import)\s", ln)]
    check(not any(re.search(r"ToolRegistry|tool_registry|\brouter\b|retriev|review_agent|"
                            r"assurance|requests|httpx|urllib|sqlite3|subprocess",
                            ln, re.IGNORECASE) for ln in import_lines),
          "写作相位所在模块不得 import Router/ToolRegistry/Retriever/Reviewer/Assurance/"
          "网络或 SQLite 入口")
    check(any(re.search(r"from\s+harness\s+import\s+topic_runtime|from\s+harness\.topic_runtime",
                        ln) for ln in import_lines),
          "写作相位只经**唯一** Harness 入口 `harness.topic_runtime` 裁决 follow-up")
    check(module_src.count("TR.run_follow_up_needs(") == 1,
          "裁决入口必须唯一：`TR.run_follow_up_needs(` 只允许出现一次")
    # §三 E 之后「相位从不触碰 store」这句话已不再成立：门后定稿完成时它**必须**把完整的
    # current 链交给 section store 恰一次。所以这里的口径改成**唯一写面**的正向断言；原先
    # 那句 `"store." not in writer_region` 是靠 `getattr(section_store, ...)` 侥幸成立的
    # （属性访问改成直接调用就会静默失效），不配当边界证据。
    #
    # (a) Pack store：读门外一次方法访问都不允许 —— Pack 只经 `PSet.resolve_pack_set` /
    #     `PSet.resolver_of` 取用，相位侧不存在 Pack 写入口。
    pack_store_access = re.findall(r"(?<![\w.])(?:store|pack_store)\.\w+", writer_region)
    check(not pack_store_access,
          f"Pack store 在只读复算门外不得被属性访问（实际 {sorted(set(pack_store_access))}）："
          f"Pack 只能被**当作值**传给 `PSet.resolve_pack_set` / `PSet.resolver_of`")
    # (b) section store：允许取的**能力名**必须恰是「链类型 + 提交并读回入口」这一对。多取
    #     一个能力就多一个未经审查的写面，因此这里断言集合**相等**，不是包含。
    store_capabilities = sorted({capability for receiver, capability in
                                 re.findall(r'getattr\(\s*(\w+)\s*,\s*"([A-Za-z_]\w*)"',
                                            writer_region) if "store" in receiver})
    check(store_capabilities == ["SectionChainV2", "commit_and_verify_section_chain_v2"],
          f"相位从注入 store 上取的唯一能力必须是 section 链落库入口（实际 {store_capabilities}）")
    # (c) 对 store 形参**不允许直接方法调用**：写面必须经上一步的能力门进来，这样「store 没有
    #     落库能力」才会走 typed fail-closed，而不是变成 AttributeError 或静默跳过。
    store_direct_calls = re.findall(r"(?<![\w.])(\w*store\w*)\.(\w+)\s*\(", writer_region)
    check(not store_direct_calls,
          f"不得对 store 形参直接方法调用（实际 {sorted(set(store_direct_calls))}）："
          f"写面必须经能力门，缺能力时 typed fail-closed")
    check(not any(tok in writer_region for tok in
                  ("write_text", "write_bytes", "open(", "os.remove", "commit_pack",
                   "switch_current", "json.dump")),
          "写作相位区域不得出现任何文件/Store 写入面")
    check("while " not in writer_region,
          "写作相位不得出现 while 循环：无界返修必须在类型/结构层不可表达")
    check(CW.MAX_FOLLOW_UP_ROUNDS == 1,
          f"有界重写轮数必须为显式常量 1（得到 {CW.MAX_FOLLOW_UP_ROUNDS!r}）")
    check(CW.BACKBONE_WRITER_PHASE_VERSION != CW.BACKBONE_PHASE_VERSION,
          "写作相位版本必须与研究相位版本分开命名（共用常量会让「相位版本」失去所指）")
    check(CW.BACKBONE_WRITER_PHASE_VERSION == "m930-3-writer-phase-v1",
          "写作相位版本常量被改动：必须同步更新本文件与工件索引（版本齐步）")

    # ------------------------------------------------------------------
    # §1 Writer 相位不回写 Pack
    # ------------------------------------------------------------------
    task, authority, scan = _company_fixture()

    # §三 G（已修）：缺 resolver 能力的 store 必须抛 **typed** `PackSetError`，不得泄漏裸
    # `AttributeError`——后者会把一次「能力不符」伪装成实现崩溃，调用方既拿不到读门的语义，
    # 也无法按类型收敛。本站在修复后改为正向断言（原「只记录不修」的现状站已作废）。
    try:
        PSet.resolve_pack_set(task, authority.pack_set.requirements, object())
    except PSet.PackSetError as exc:
        check("resolver" in str(exc),
              f"缺 resolver 能力的 store 必须以 PackSetError 点名缺失的能力（得到 {str(exc)[:80]}）")
    except AttributeError as exc:
        check(False, f"缺 resolver 能力的 store 泄漏了裸 AttributeError：{str(exc)[:80]}")
    else:
        check(False, "缺 resolver 能力的 store 未被拒绝（读门静默放宽）")
    # 同一纪律在写作相位入口：没有解析器就看不到正文，必须 typed fail-closed。
    expect_error(
        lambda: CW.run_backbone_writer_phase(
            (_section_input(task, authority),), llm_client=FIXT._StubLlm(_presented_plan(scan, authority)),
            entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
            final_sentence_llm_client=FIXT._stub_final_sentence_client()),
        CW.BackboneWriterPhaseError, "写作相位缺 material_resolver 必须 typed fail-closed",
        needle="material_resolver")
    expect_error(
        lambda: CW.run_backbone_writer_phase(
            (_section_input(task, authority),), llm_client=FIXT._StubLlm(_presented_plan(scan, authority)),
            entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
            final_sentence_llm_client=FIXT._stub_final_sentence_client(),
            store=object()),
        CW.BackboneWriterPhaseError,
        "store 缺 resolver 能力时写作相位必须 typed fail-closed（不得泄漏 AttributeError）",
        needle="材料正文解析器")

    presented = _presented_plan(scan, authority)
    before = _identity_snapshot(authority, task)
    phase = CW.run_backbone_writer_phase(
        (_section_input(task, authority),), llm_client=FIXT._StubLlm(presented),
        entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
        final_sentence_llm_client=FIXT._stub_final_sentence_client(),
        # §三 A：组合根注入材料正文解析器（与生产同一入口），否则相位 fail-closed。
        material_resolver=FIXT._payload_resolver())
    after = _identity_snapshot(authority, task)
    section = phase.sections[0]

    check(before == after,
          "写作相位**不得回写** Pack：权威 Pack / 材料 / 事实 / 读视图必须逐字节不变")
    check(before["packs"] == after["packs"] and before["materials"] == after["materials"]
          and before["facts"] == after["facts"],
          "权威容器、材料、事实的规范内容指纹在写作前后必须逐一相同")
    check(before["authority_input_id"] == after["authority_input_id"]
          and bool(before["authority_input_id"]),
          "权威输入的派生身份不得被相位改写（也不得被清空）")
    check(after["pack_set_to_dict"] == before["pack_set_to_dict"],
          "权威 Pack set 的规范内容身份不得被相位改写")
    # 探针灵敏度：快照必须对**内容变化**敏感，否则上面的「逐字节不变」不是证据。
    mutated_authority = FIXT._authority(
        task,
        facts=(FIXT._fact("f-main", "公司主营业务为动力电池系统的研发、生产与销售。",
                          (FIXT.ASP_BUSINESS_MAIN,)),
               FIXT._fact("f-sales", "公司销售模式以直销为主。", (FIXT.ASP_BUSINESS_SALES,)),
               FIXT._fact("f-procurement", "公司原材料采购以长期协议为主。",
                          (FIXT.ASP_BUSINESS_PROCUREMENT,)),
               FIXT._fact("f-extra", "公司另有一项权威事实。", (FIXT.ASP_BUSINESS_MAIN,))),
        aspects=(FIXT._AspectResult(FIXT.ASP_BUSINESS_MAIN, "covered"),
                 FIXT._AspectResult(FIXT.ASP_BUSINESS_SALES, "partial"),
                 FIXT._AspectResult(FIXT.ASP_BUSINESS_PROCUREMENT, "partial")),
        requirements=(FIXT._Req(FIXT.TOPIC_BUSINESS, (
            FIXT._AspectReq(FIXT.ASP_BUSINESS_MAIN, FIXT.TOPIC_BUSINESS, "q-company_business"),
            FIXT._AspectReq(FIXT.ASP_BUSINESS_SALES, FIXT.TOPIC_BUSINESS,
                            "q-company_business"),
            FIXT._AspectReq(FIXT.ASP_BUSINESS_PROCUREMENT, FIXT.TOPIC_BUSINESS,
                            "q-company_business"),)),))
    check(_identity_snapshot(mutated_authority, task) != before,
          "权威快照必须对内容变化敏感（探针灵敏度：多一条事实即不同）")

    check(not phase.follow_up_runs and not section.follow_up_needs,
          "合法章节不发出 FollowUpNeed，也不产生补件运行")
    check(section.rewrite_round == 0,
          f"未发出 FollowUpNeed 的章节重写轮数必须为 0（得到 {section.rewrite_round}）")
    check(section.follow_up_run_refs == (),
          "重写轮数为 0 时不得携带任何裁决/执行 ref")
    check(phase.phase_version == CW.BACKBONE_WRITER_PHASE_VERSION
          and phase.to_dict()["phase_version"] == CW.BACKBONE_WRITER_PHASE_VERSION,
          "相位自报版本必须与命名常量一致")

    # 产物只允许回指**输入**容器/事实：一个都不许多出来。
    input_pack_ids = {str(p.pack_id) for p in authority.pack_set.packs}
    input_fact_ids = {str(f.fact_id) for p in authority.pack_set.packs for f in p.facts}
    input_material_ids = {str(m.material_id) for p in authority.pack_set.packs
                          for m in p.materials}
    disposition_pack_ids = {str(d.pack_id) for d in section.dispositions}
    disposition_fact_ids = {str(d.fact_id) for d in section.dispositions}
    check(disposition_pack_ids and disposition_pack_ids <= input_pack_ids,
          f"门后去向不得引用输入之外的 Pack：{sorted(disposition_pack_ids - input_pack_ids)}")
    check(disposition_fact_ids and disposition_fact_ids <= input_fact_ids,
          f"门后去向不得引用输入之外的权威事实：{sorted(disposition_fact_ids - input_fact_ids)}")
    manifest_ids = {str(e.material_id) for e in section.draft.material_manifest.entries}
    check(manifest_ids and manifest_ids <= input_material_ids,
          f"材料清单必须是输入材料的子集：{sorted(manifest_ids - input_material_ids)}")
    check(len(section.dispositions) == len(input_fact_ids),
          f"每条输入权威事实恰一条去向：{len(section.dispositions)} != {len(input_fact_ids)}")

    # 缺口必须由**权威**派生，而不是被相位发明或被静默丢弃。
    expected_gap_ids = set(PW.expected_aspect_gap_ids(authority, task))
    actual_gap_ids = {str(u.unresolved_id) for u in section.result.unresolved}
    check(expected_gap_ids and expected_gap_ids <= actual_gap_ids,
          f"权威声明的非覆盖 aspect 缺口不得丢失：缺 {sorted(expected_gap_ids - actual_gap_ids)}")
    check(section.result.status == "COMPLETED_WITH_GAPS",
          f"本节按权威口径即为带缺口完成（得到 {section.result.status!r}）")
    unclaimed = [d for d in section.dispositions if str(d.disposition) != "claimed"]
    check([str(d.fact_id) for d in unclaimed] == ["f-procurement"],
          f"本节未呈现的权威事实必须留下显式非 claimed 去向（得到 "
          f"{[str(d.fact_id) for d in unclaimed]}）")
    # 非 claimed 去向必须带**封闭**原因码；缺口锚点要么缺省（该原因与缺口无关），要么指向
    # 本节 Result 里真实存在的缺口——不得指向一个不存在的 unresolved id。
    check(all(str(d.reason_code) in NS.DISPOSITION_REASON_CODES for d in unclaimed),
          f"非 claimed 去向的原因码必须取自封闭词表 DISPOSITION_REASON_CODES"
          f"（得到 {[str(d.reason_code) for d in unclaimed]}）")
    check(all(d.unresolved_id is None or str(d.unresolved_id) in actual_gap_ids
              for d in unclaimed),
          "非 claimed 去向的缺口锚点不得指向不存在的缺口")
    check(all(d.unresolved_id is None for d in unclaimed),
          "本节的未呈现事实属于「非缺口类原因」分支（`unresolved_id` 缺省）；若该行为改变，"
          "必须同步更新本节断言与 §十六 的缺口口径，不得默默放宽")

    # 决定链形状（与组装器参考夹具同形）：3 aggregate / 2 entailment / 3 accepted binding
    factual = tuple(b for b in section.acceptance.accepted_bindings
                    if str(b.support_semantics) == "factual")
    contextual = tuple(b for b in section.acceptance.accepted_bindings
                       if str(b.support_semantics) == "context")
    check(len(section.aggregate_decisions) == 3 and len(section.entailment_decisions) == 2
          and len(factual) == 2 and len(contextual) == 1,
          "决定链形状：factual 边 2 条（aggregate + entailment）、context 边 1 条（仅 aggregate）"
          f"（得到 aggregate={len(section.aggregate_decisions)} "
          f"entailment={len(section.entailment_decisions)} factual={len(factual)} "
          f"context={len(contextual)}）")
    aggregate_ids = [str(d.binding_decision_id) for d in section.aggregate_decisions]
    aggregate_subjects = [(str(d.subject_kind), str(d.subject_id))
                          for d in section.aggregate_decisions]
    check(aggregate_ids and len(set(aggregate_ids)) == len(aggregate_ids)
          and len(aggregate_subjects) == len(set(aggregate_subjects)),
          "每个 (subject_kind, subject_id, draft_revision) 恰一条 aggregate 决定"
          f"（得到 {aggregate_subjects}）")
    check(all(d.result == "pass" for d in section.aggregate_decisions),
          "本节决定链必须全 pass（否则不该有门后定稿产物）")
    check({str(b.binding_decision_id) for b in section.acceptance.accepted_bindings}
          <= set(aggregate_ids),
          "每条 accepted binding 的绑定决定必须是本节真实存在的 aggregate 决定")
    check(all(str(b.binding_subject_id) in {s for _k, s in aggregate_subjects}
              for b in section.acceptance.accepted_bindings),
          "accepted binding 的 subject 必须与 aggregate 决定的 subject 同一集合")
    check(all(str(b.entailment_decision_id) for b in factual),
          "每条 factual accepted binding 必须引用 entailment 决定")
    check(all(b.entailment_decision_id is None for b in contextual),
          "context accepted binding 不得引用 entailment 决定（context 不进语义门）")
    check(all(str(b.entailment_decision_id) in
              {str(d.entailment_decision_id) for d in section.entailment_decisions}
              for b in factual),
          "factual accepted binding 引用的蕴含决定必须是本节真实存在的那些")

    # 产物身份是确定性的：同一产物重复序列化必须逐字相同。
    check(json.dumps(phase.to_dict(), ensure_ascii=False)
          == json.dumps(phase.to_dict(), ensure_ascii=False),
          "相位产物序列化必须确定性（同内容同字节）")

    # ------------------------------------------------------------------
    # §2 `FollowUpNeed` 未裁决不得执行
    # ------------------------------------------------------------------
    statement = "需要补充该主题的权威材料以闭环采购模式的合同依据。"
    requested = _requesting_plan(scan, authority, statement=statement)

    # §2.1 相位入口（公开）：缺任一注入能力一律 fail-closed，且**不得**把它变成缺口。
    injection_variants = (
        ("四项全缺", {}),
        ("只给 requirements", {"requirements": authority.pack_set.requirements}),
        ("给 requirements + run_context", {"requirements": authority.pack_set.requirements,
                                            "run_context": _run_context(task)}),
    )
    for label, overrides in injection_variants:
        expect_error(
            lambda ov=overrides: CW.run_backbone_writer_phase(
                (_section_input(task, authority, **ov),),
                llm_client=FIXT._StubLlm(requested),
                entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
                final_sentence_llm_client=FIXT._stub_final_sentence_client(),
                material_resolver=FIXT._payload_resolver()),
            CW.BackboneWriterPhaseError,
            f"§2.1 {label}：Writer 发出 FollowUpNeed 时必须拒绝，不得静默丢弃",
            needle="没有注入裁决能力")
    # store 缺省时，即使另外三项齐备也必须拒绝（`store is None` 是四项里的一票否决）。
    expect_error(
        lambda: CW.run_backbone_writer_phase(
            (_section_input(task, authority,
                            requirements=authority.pack_set.requirements,
                            run_context=_run_context(task)),),
            llm_client=FIXT._StubLlm(requested),
            entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
            final_sentence_llm_client=FIXT._stub_final_sentence_client(),
            material_resolver=FIXT._payload_resolver(),
            dependencies_of=lambda requirement, run_context: _deps(
                sink=_Sink(), store=_StoreSpy(), policy_resolver=_ForbiddenResolver())),
        CW.BackboneWriterPhaseError,
        "§2.1 缺 store：即使 dependencies_of 在位也必须拒绝（未裁决不得执行）",
        needle="没有注入裁决能力")

    # §2.2 相位自己的裁决分支（`_follow_up_authority`）：`store` 在位时相位入口会先撞上
    # P4 只读复算门（合成 Pack set 的依赖指纹是测试常量，读门必然 block），因此把这一支
    # 单独驱动。**只**绕过读门，样本仍是同一 task/authority/need。
    need = _follow_up_need(scan, statement=statement)
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(task, authority), needs=(need,),
        dependencies_of=lambda requirement, run_context: _deps(
            sink=_Sink(), store=_StoreSpy(), policy_resolver=_ForbiddenResolver()),
        store=_StoreSpy()),
        CW.BackboneWriterPhaseError,
        "§2.2 缺 requirements：不得因为「其它三项都在」就放行",
        needle="没有注入裁决能力")
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(task, authority,
                                    requirements=authority.pack_set.requirements),
        needs=(need,),
        dependencies_of=lambda requirement, run_context: _deps(
            sink=_Sink(), store=_StoreSpy(), policy_resolver=_ForbiddenResolver()),
        store=_StoreSpy()),
        CW.BackboneWriterPhaseError,
        "§2.2 缺 run_context：不得用缺省上下文凑一次裁决",
        needle="没有注入裁决能力")
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(task, authority,
                                    requirements=authority.pack_set.requirements,
                                    run_context=_run_context(task)),
        needs=(need,), dependencies_of=None, store=_StoreSpy()),
        CW.BackboneWriterPhaseError,
        "§2.2 缺 dependencies_of：不得自造运行时依赖",
        needle="没有注入裁决能力")
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(task, authority,
                                    requirements=authority.pack_set.requirements,
                                    run_context=_run_context(task)),
        needs=(need,),
        dependencies_of=lambda requirement, run_context: _deps(
            sink=_Sink(), store=_StoreSpy(), policy_resolver=_ForbiddenResolver()),
        store=None),
        CW.BackboneWriterPhaseError,
        "§2.2 缺 store：不得在没有可提交裁决记录的 Store 时执行 follow-up",
        needle="没有注入裁决能力")
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(task, authority,
                                    requirements=authority.pack_set.requirements,
                                    run_context=_run_context(task)),
        needs=(need,), dependencies_of=lambda requirement, run_context: None,
        store=_StoreSpy()),
        CW.BackboneWriterPhaseError,
        "§2.2 dependencies_of 未返回依赖：不得退化成「没有依赖也能跑」",
        needle="未返回 FollowUpNeed 的运行时依赖")
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(
            task, authority,
            requirements=authority.pack_set.requirements * 2,
            run_context=_run_context(task)),
        needs=(need,),
        dependencies_of=lambda requirement, run_context: _deps(
            sink=_Sink(), store=_StoreSpy(), policy_resolver=_ForbiddenResolver()),
        store=_StoreSpy()),
        CW.BackboneWriterPhaseError,
        "§2.2 requirements 重复：requirement 目录必须无重复 id（重复即拒绝）",
        needle="重复的 requirement id")
    # `run_context` 不是真实类型时，拒绝发生在**唯一** Harness 入口内（相位不吞、不降级）。
    expect_error(lambda: CW._follow_up_authority(
        section_input=_section_input(task, authority,
                                    requirements=authority.pack_set.requirements,
                                    run_context=object()),
        needs=(need,),
        dependencies_of=lambda requirement, run_context: _deps(
            sink=_Sink(), store=_StoreSpy(), policy_resolver=_ForbiddenResolver()),
        store=_StoreSpy()),
        TR.TopicRuntimeError,
        "§2.2 伪 run_context：必须由 Harness 入口拒绝，不得静默降级成「无需裁决」",
        needle="需要 TopicRunContext")

    # ------------------------------------------------------------------
    # §3 Harness 裁决层：被拒的 need 必须留下 typed 记录，不得静默丢弃
    # ------------------------------------------------------------------
    requirement = authority.pack_set.requirements[0]
    requirements_by_id = {TR.topic_requirement_id(requirement): requirement}
    run_context = _run_context(task)

    def _adjudicate(needs, *, sink=None, store=None, policy_resolver=None, ctx=None):
        sink = sink if sink is not None else _Sink()
        store = store if store is not None else _StoreSpy()
        policy_resolver = (policy_resolver if policy_resolver is not None
                           else _ForbiddenResolver())
        run = TR.run_follow_up_needs(
            follow_up_needs=tuple(needs), requirements_by_id=requirements_by_id,
            run_context=ctx if ctx is not None else run_context,
            dependencies=_deps(sink=sink, store=store, policy_resolver=policy_resolver))
        return run, sink, store, policy_resolver

    rejected_need = _follow_up_need(scan, statement=statement, source_class="not_a_class")
    run, sink, store, policy_resolver = _adjudicate((rejected_need,))
    check(len(run.decisions) == 1 and run.decisions[0].verdict == "rejected"
          and run.decisions[0].reason == "unknown_source_class",
          f"未登记来源类必须留下 typed 拒绝决定（得到 "
          f"{[(d.verdict, d.reason) for d in run.decisions]}）")
    check(run.decisions[0].executed is False and run.decisions[0].new_pack_id is None,
          "被拒的 need 不得携带任何执行结果")
    check(run.new_pack_ids == ()
          and run.rejected_need_ids() == (rejected_need.need_id,)
          and run.accepted_need_ids() == (),
          "整批被拒时不得产生任何新 Pack 身份")
    check(policy_resolver.calls == 0,
          "拒绝必须发生在**执行前**：SourcePolicy 解析器一次都不得被调用")
    check(len(store.commits) == 1
          and len(store.commits[0][2]) == 1
          and store.commits[0][2][0].verdict == "rejected",
          "被拒的 need 必须**留下**追加式裁决记录（拒绝 ≠ 可以忽略）")
    check([e for e, _ in sink.events] == ["FOLLOW_UP_REJECTED"],
          f"拒绝必须留 trace 事件（得到 {[e for e, _ in sink.events]}）")

    out_of_scope_need = _follow_up_need(scan, statement=statement, scope=("nowhere",))
    run2, _s2, _st2, _p2 = _adjudicate((out_of_scope_need,))
    check([(d.verdict, d.reason) for d in run2.decisions]
          == [("rejected", "scope_not_authorized")],
          "越出冻结 aspect 授权范围的申请必须被拒（不得自动缩小范围后照跑）")

    ghost_need = _follow_up_need(scan, statement=statement,
                                 target_requirement_id="company::ghost_topic")
    run3, _s3, _st3, _p3 = _adjudicate((ghost_need,))
    check([(d.verdict, d.reason) for d in run3.decisions]
          == [("rejected", "target_requirement_unresolved")],
          "指向不存在需求的申请必须被拒（不得凭字符串猜一个需求）")

    expect_error(lambda: _adjudicate((_follow_up_need(scan, statement=statement),
                                      _follow_up_need(scan, statement=statement,
                                                      revision="r2"))),
                 TR.TopicRuntimeError,
                 "§3 混合批次（同一批内 draft revision 不唯一）必须 fail-closed",
                 needle="必须唯一")
    expect_error(lambda: _adjudicate((rejected_need, rejected_need)),
                 TR.TopicRuntimeError,
                 "§3 重复 need_id 必须 fail-closed",
                 needle="重复 need_id")
    expect_error(lambda: _adjudicate((rejected_need,), ctx=object()),
                 TR.TopicRuntimeError,
                 "§3 非 TopicRunContext 必须 fail-closed",
                 needle="需要 TopicRunContext")
    expect_error(lambda: _adjudicate((rejected_need,), store=object()),
                 AttributeError,
                 "§3 Store 没有追加式提交面时必须**报错**，而不是静默丢弃裁决记录")

    # ------------------------------------------------------------------
    # §4 两个身份轴不得互填：`FollowUpNeed` ≠ gap，`FollowUpDecision` ≠ FND 去向
    # ------------------------------------------------------------------
    need_fields = set(TS.FollowUpNeed.__dataclass_fields__)
    check(not ({"gap_id", "unresolved_id", "new_pack_id", "executed", "trace_refs",
                "decision_id", "section_result_id"} & need_fields),
          f"FollowUpNeed 不得承载缺口身份或执行结果："
          f"{sorted(need_fields & {'gap_id', 'unresolved_id', 'new_pack_id', 'executed'})}")
    decision_fields = set(TS.FollowUpDecision.__dataclass_fields__)
    check({"verdict", "executed", "new_pack_id"} <= decision_fields,
          "裁决结果只能由 FollowUpDecision 承载（verdict / executed / new_pack_id）")
    check("rejected" in TS.FOLLOW_UP_DECISION_VERDICTS
          and {"unknown_source_class", "scope_not_authorized", "budget_exhausted"}
          <= set(TR.FOLLOW_UP_REJECTION_REASONS),
          "拒绝必须是封闭词表里的一个显式取值，而不是自由文本")
    check(not (set(TR.FOLLOW_UP_REJECTION_REASONS) & set(NS.DISPOSITION_REASON_CODES)),
          "follow-up 拒绝码不得与 FND 去向原因码共用词表（两类身份不得互填）")
    check(not any(v in ("rejected", "rejection", "follow_up_rejected")
                  for v in NS.NARRATIVE_DISPOSITIONS),
          "『被拒的 follow-up』不得被写成正文去向：FND 词表里不得出现拒绝语义")
    check(not hasattr(section, "unresolved_from_follow_up")
          and not hasattr(phase, "unresolved_from_follow_up"),
          "`FollowUpNeed` 不得被折叠成缺口的第二个来源")

    # ------------------------------------------------------------------
    # §5 相位产物角色 ⊆ P14 `demo-artifact-index-v2` 的成员角色
    # ------------------------------------------------------------------
    artifact_roles = {
        "draft": "section_draft",
        "draft.claim_candidates": "claim_candidate",
        "draft.narrative_draft_units": "narrative_draft_unit",
        "aggregate_decisions": "claim_binding_decision",
        "entailment_decisions": "claim_entailment_decision",
        "acceptance.accepted_bindings": "accepted_support_binding",
        "claims": "section_claim",
        "narrative": "section_narrative",
        "result": "section_result",
        "dispositions": "fact_narrative_disposition",
        "follow_up_needs": "follow_up_need",
    }
    unknown_roles = sorted(r for r in artifact_roles.values()
                           if r not in AB.ARTIFACT_MEMBER_ROLES)
    check(not unknown_roles,
          f"相位产物角色必须已在 demo-artifact-index-v2 登记：未登记 {unknown_roles}")
    check("writer" in AB.ARTIFACT_PRODUCER_STEPS,
          "写作相位的生产者步骤必须已在索引里登记")
    registered_but_empty = sorted(
        path for path, _role in artifact_roles.items()
        if path != "follow_up_needs" and not _resolve_path(section, path))
    check(not registered_but_empty,
          f"已在索引登记的相位产物不得为空：{registered_but_empty}")
    check(not _resolve_path(section, "follow_up_needs"),
          "本节未发出 FollowUpNeed，`follow_up_need` 角色在本节必须为空（不得为对齐索引而造数）")
    check(AB.ARTIFACT_INDEX_SCHEMA_VERSION == "demo-artifact-index-v2",
          "工件索引 version 与相位产物角色表必须齐步（v1 已冻结为只读）")

    # ------------------------------------------------------------------
    # §6 如实记录的两处覆盖缺口
    # ------------------------------------------------------------------
    check("_assert_pack_set_is_current" in writer_region
          and "PSet.resolve_pack_set" in writer_region,
          "P4 只读复算门必须在相位里存在（它的真实覆盖在 evals/test_demo_pack_set.py）")
    skip("§1/缺口 1：『裁决通过 → 新 Pack → 有界重写一轮』与『第二轮仍发出 FollowUpNeed 即拒』"
         "在离线合成 Pack set 上不可驱动——`store` 在位时 `_assert_pack_set_is_current` 会先用"
         "`requirement.dependency_fingerprint()` 重算读门，而合成 `VerifiedPackSet` 携带测试"
         "常量依赖指纹，读门必然 block。**本文件因此不再声称覆盖这两条**：正向后继的"
         "aspect/question 完整性由 evals/test_demo_pack_set.py §6f（真库真 runtime）覆盖，"
         "上界守卫（第二轮仍发诉求即 typed 终止，且第 0 轮真的提交了后继 Pack）由"
         "evals/test_demo_writer_formal_chain.py §3.5（真库 + 真相位入口）覆盖")
    skip("§1/缺口 2：`store` 在位时的「P4 复算也没写回」同样受缺口 1 限制；本文件的不回写"
         "证明是**对象级**（权威对象逐字节不变 + 产物只回指输入容器），不是 store 级")
    # §0/缺口 3 已修（§三 G）：`pack_set.resolver_of` 让三种缺能力情形统一 typed fail-closed，
    # 本文件 §0 因此改成**正向断言**（含写作相位入口的两条同纪律反例），不再 skip。

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

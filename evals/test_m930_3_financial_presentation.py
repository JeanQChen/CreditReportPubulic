"""Eval: 财务**呈现层**的三件事——指标→栏目路由（`fpr-1`／`fpr-2`）、确定性来源/口径呈现（`css-1`）、
以及「真实模式换谁写、批了几次」这道门（`cited-budget-2`）。

用法: python -m evals.test_m930_3_financial_presentation

本模块钉的是本批（`DESIGN_V2.md` §0.20–§0.21）三个具体的错法，每一个都对应一条会被读错的
产物：

1. **指标错栏**（`fpr-1`；`fpr-2` 起覆盖两族栏目）。财务 workflow 至今没有 aspect 级归属（`pack_writer.scan_financial`
   逐条给事实的 `aspect_ids` 传空元组），因此「流动比率该在第几栏」这条链上**从来没有人
   说过**。本模块钉住：路由是一张**写明的、版本化的呈现层声明**；权威事实的 `aspect_ids`
   **照旧为空**（声明不得冒充权威登记）；冻结 Contract 有、而本次选中事实证明不了的栏
   （净资产水平 / 刚性债务结构）只留下**栏目缺口**，不拿别的栏的事实去顶。
2. **`fin_source_scope` 被静默跳过**（`css-1`）。它在阶段 A profile 里是**选中 topic**。
   本模块钉住：它的十条 Contract 要求逐条出现（取到的是读数、取不到的是 typed 缺口），
   **零模型调用**，且它不复制 `fin_solvency` 的偿债表充数。
3. **真实模式**。本模块在**不发任何请求**的前提下钉住：离线模式即使装了同一道门也**一次都不
   记账**（替身不碰账本）；真实模式下第一请求若会被门拒，就**确实没有发出去**，拒绝如实记在
   写作类别上；`config.LLM_MODEL` 之外的模型在**第一请求之前**被拒。

夹具走 `evals.test_demo_pack_writer` 的**真实** `FinancialPackArtifact` /
`FinancialAuthorityInput`；冻结 Contract 那一侧单独走**真实投影**（`planning.demo_scope` +
`contracts.loader_v2`），不手搭一份假 aspect 表充数。模块无公司代号、页码、表号或固定答案
关键词。不调 LLM、不联网、不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config                                                         # noqa: E402
from contracts.loader_v2 import load_contract_v2                      # noqa: E402
from evals import test_demo_pack_writer as T                          # noqa: E402
from llm import budget as LB                                          # noqa: E402
from planning import demo_scope as DS                                 # noqa: E402
from planning import schema as PS                                     # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN                   # noqa: E402
from sections import cited_budget as CB                               # noqa: E402
from sections import cited_financial_table as CFT                     # noqa: E402
from sections import cited_review as CR                               # noqa: E402
from sections import cited_source_scope as CSS                        # noqa: E402
from sections import cited_writer as CW                               # noqa: E402
from sections import financial_presentation_routing as FPR            # noqa: E402
from sections import financial_worker as FW                           # noqa: E402
from sections import material_context as MC                           # noqa: E402
from sections import narrative_schema as NS                           # noqa: E402
from sections import pack_writer as PW                                # noqa: E402
from sections import sentence_check as SC                             # noqa: E402

_FIN_SECTION = "financial"
_FIN_TASK_ID = "task-fin-presentation"
_SHORT_COLUMN = "fin_solvency.short_term_solvency"
_LONG_COLUMN = "fin_solvency.long_term_solvency"
_BEARING_COLUMN = "fin_solvency.interest_bearing_debt"
_PROXY_COLUMN = "fin_solvency.interest_expense_proxy"
_NET_ASSET_COLUMN = "fin_solvency.net_asset_level"
_RIGID_DEBT_COLUMN = "fin_solvency.rigid_debt_structure"

_P0 = "2023-12-31"
_P1 = "2024-12-31"
_T0 = "2023年末"
_T1 = "2024年末"
_UNIT = "ratio"
_CO = "示例股份"

#: 冻结 Contract 里 `fin_solvency` 的六栏（次序即 Contract 次序）。测试侧显式写出它们，是为了
#: 让「路由指向的栏在 Contract 里是什么档」这件事在夹具里可见；真实投影的那一份在 §0 逐条核对。
_SOLVENCY_COLUMNS = (
    (_SHORT_COLUMN, "required_body", "短期偿债能力"),
    (_LONG_COLUMN, "required_body", "长期偿债能力"),
    (_NET_ASSET_COLUMN, "required_body", "净资产水平"),
    (_RIGID_DEBT_COLUMN, "required_body", "刚性债务结构"),
    (_BEARING_COLUMN, "required_body", "有息负债（可靠取得时进入正文，不可靠时显式缺口）"),
    (_PROXY_COLUMN, "diagnostic_only", "利息费用代理（不可靠，仅进入诊断槽位）"),
)

#: `fin_balance_structure` 的三栏（测试侧规格；§0 与真实投影逐条核对）。三栏都是 `required_body`。
_BS_COLUMNS = (
    ("fin_balance_structure.balance_structure", "required_body", "资产负债结构"),
    ("fin_balance_structure.major_account_changes", "required_body", "重大科目变化"),
    ("fin_balance_structure.fifteen_pct_forced_analysis", "required_body", "15%以上科目强制分析"),
)

#: `css-1` 路由表登记的全部 aspect（Contract 那一侧必须与它逐条相等）。
_SCOPE_ROUTE_ASPECTS = tuple(a for a, _label, _reader in CSS.SOURCE_SCOPE_ASPECT_ROUTING)


@dataclasses.dataclass(frozen=True)
class _FF:
    """财务事实规格。比 `test_demo_pack_writer._FinFact` 多带**期间口径两件套**：

    `period_basis` 决定这一格是时点量还是期间量（`cmt` 的 `period_basis_undeclared` 就判在
    它上面），`period_label` 是给读者看的期间说法（列名逐字取自它）。少了这两件，夹具根本
    成不了表，测出来的也不会是「表怎么分层」。
    """

    fact_id: str
    label: str
    display: str
    period: str
    unit: str = ""
    value_text: str | None = "1.00"
    citation: dict | None = None
    kind: str = "ratio"
    code: str = ""
    status: str = "ok"
    reason_code: str | None = None
    note: str = ""
    period_basis: str = ""
    period_label: str = ""


def _ff(*, code, period, period_label, label, display, unit=_UNIT, basis="end", **kw) -> _FF:
    """一条财务事实规格。引用锚点带 `ref_type`：财务 citation 缺它会在扫描期被拒
    （`scan_financial` 不得臆造 locator），因此夹具必须给出一条**形状合法**的引用。"""
    kw.setdefault("citation", {"ref_type": "structured"})
    return _FF(fact_id=f"{code.lower()}-{period[:4]}", label=label, display=display,
               period=period, unit=unit, code=code, period_basis=basis,
               period_label=period_label, **kw)


def _facts():
    """两条指标各两期：一条**普通**（时点量）、一条**代理口径**（期间量）。

    两期是硬要求：`NS.FINANCIAL_TABLE_MIN_PERIODS == 2`，单期指标本来就该写在正文里而不成行。
    """
    return (
        _ff(code="SOLV_CURRENT_RATIO", period=_P0, period_label=_T0,
            label="流动比率", display="1.20 倍"),
        _ff(code="SOLV_CURRENT_RATIO", period=_P1, period_label=_T1,
            label="流动比率", display="1.35 倍"),
        _ff(code="SOLV_INTEREST_COVER", period=_P0, period_label=_T0, basis="flow",
            label="利息保障倍数", display="-9.94 倍",
            status=NS.PROXY_STATUS, note="代理口径（PROXY_FINANCE_EXPENSES）"),
        _ff(code="SOLV_INTEREST_COVER", period=_P1, period_label=_T1, basis="flow",
            label="利息保障倍数", display="-8.10 倍",
            status=NS.PROXY_STATUS, note="代理口径（PROXY_FINANCE_EXPENSES）"),
    )


def _solvency_requirement():
    """`fin_solvency` 的 Contract 投影（测试侧规格：与真实投影逐字段同形，§0 逐条核对）。"""
    return T._Req("fin_solvency", tuple(
        T._aspect(aspect_id, "fin_solvency", f"q-{aspect_id}", display_tier=tier,
                  requirement_text=text)
        for aspect_id, tier, text in _SOLVENCY_COLUMNS))


def _balance_requirement():
    """`fin_balance_structure` 的 Contract 投影（测试侧规格：与真实投影逐字段同形，§0 逐条核对）。

    `fpr-2` 起这条声明要同时认得这一族的栏目（见 `_BS_CODE_TO_COLUMN` 的理由），
    因此夹具必须给出它的投影——否则映射表里那 17 条 code 一条都登记不上。
    """
    return T._Req("fin_balance_structure", tuple(
        T._aspect(aspect_id, "fin_balance_structure", f"q-{aspect_id}", display_tier=tier,
                  requirement_text=text)
        for aspect_id, tier, text in _BS_COLUMNS))


def _scope_requirement():
    """`fin_source_scope` 的 Contract 投影：十条要求，与 `css-1` 路由表逐条对应。"""
    aspects = tuple(
        T._aspect(aspect_id, "fin_source_scope", f"q-{aspect_id}",
                  requirement_text=f"{label}（本栏的要求原文）")
        for aspect_id, label, _reader in CSS.SOURCE_SCOPE_ASPECT_ROUTING)
    return T._Req("fin_source_scope", aspects)


def _task():
    return T._task(_FIN_SECTION, (T.TOPIC_FIN_SCOPE, T.TOPIC_FIN_SOLVENCY),
                   task_id=_FIN_TASK_ID, title="财务信息")


def _artifact(*, facts=None, periods=(_P0, _P1), statements_available=()):
    specs = _facts() if facts is None else tuple(facts)
    return T._Artifact(task_id=_FIN_TASK_ID, facts=specs, periods=tuple(periods),
                       statements_available=tuple(statements_available))


def _authority(*, facts=None, periods=(_P0, _P1), statements_available=()):
    """两 topic 的财务任务 ⇒ `create` **必须**显式给出 `fact_topic_map`（否则当场拒绝）。"""
    task = _task()
    specs = _facts() if facts is None else tuple(facts)
    artifact = _artifact(facts=facts, periods=periods,
                         statements_available=statements_available)
    authority = T._financial_authority(
        task, artifact, note_gap=T._NoteGap(task_id=task.task_id), company_name=_CO,
        fact_topic_map=tuple((spec.fact_id, T.TOPIC_FIN_SOLVENCY) for spec in specs))
    return task, authority


class _Inputs:
    """`_build_cited_payload` / `_resolve_topic_ids` 只读这几处。"""

    def __init__(self, *, authority, task, requirements) -> None:
        self.authorities = {str(task.section_id): authority}
        self.tasks = {str(task.section_id): task}
        self.requirements = dict(requirements)
        self.resolver = None


def _inputs(*, authority=None, task=None, requirements=None):
    if task is None or authority is None:
        task, authority = _authority()
    if requirements is None:
        requirements = {"fin_solvency": _solvency_requirement(),
                        "fin_source_scope": _scope_requirement()}
    return _Inputs(authority=authority, task=task, requirements=requirements)


def _expect_error(fn, exc_type, *, token: str = ""):
    try:
        fn()
    except exc_type as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(
                f"异常消息里没有 {token!r}（无法据此定位）：{message}") from None
        return exc
    except Exception as exc:  # noqa: BLE001 —— 类型不对本身就是失败，不得被吞成「通过了」
        raise AssertionError(
            f"期望 {exc_type.__name__}，实际抛出 {type(exc).__name__}: {exc}") from None
    raise AssertionError(f"这一路必须 fail-closed，但它通过了（期望含 {token!r}）")


def _metric_tables(*, routing):
    """按本节清单构造确定性指标表（`cmt-4` / `cmtr-4`）。"""
    task, authority = _authority()
    scan = PW.scan_authority(authority, task)
    requirement = _solvency_requirement()
    manifest = CW.build_cited_writer_input(
        authority=authority,
        material_context=MC.empty_material_context_for_authority(authority),
        subsections=CHAIN._subsections_from_writing_spec(requirement),
        facts=scan.facts, section_title="财务信息",
        presentation_routing=None if routing is None else routing.to_dict())
    return manifest, CFT.build_cited_metric_tables(
        section_id=_FIN_SECTION, authority=authority, manifest=manifest,
        presentation_routing=routing)


def _routing_for(facts, requirement=None, *, balance=True):
    """生产形状（`fpr-2`）：写正文的 topic 给缺口账，同一节确定性呈现的 topic 给栏目认得出来。

    `balance=False` 用来构造「只选了 `fin_solvency`」的旧形状（v1 profile），
    从而证明两族 code 各自独立登记、互不冒充。
    """
    return FPR.build_presentation_routing(
        gap_scope_topic="fin_solvency",
        requirement=_solvency_requirement() if requirement is None else requirement,
        extra_route_requirements=({"fin_balance_structure": _balance_requirement()}
                                  if balance else {}),
        facts=facts)


def _routing_input(authority, scan):
    """路由声明的输入事实——走**生产链同一个**入口，不另写一份。"""
    return CHAIN._presentation_routing_facts(authority, scan)


def _routing_with_source(routing, source: str):
    """试着换掉来源标记；构造期应当拒绝，这里把「没拒绝」如实返回 `None`。"""
    try:
        return dataclasses.replace(routing, source=source)
    except FPR.PresentationRoutingError:
        return None


def _projection_for(spec):
    """把一个规格物化成**权威事实投影**（只为「未路由」那一支造输入）。"""
    return T._Artifact(task_id=_FIN_TASK_ID, facts=(spec,)).facts[0]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP: {msg}")

    # ============================================ §0 冻结 Contract 与真实投影
    details.append("## §0 阶段 A profile 真的选中 `fin_source_scope`（本批那条缺口的现场）")
    profile = DS.load_demo_scope_profile(CHAIN.DEFAULT_PROFILE)
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_m930_3_finpresentation", company_id="c-demo",
        company_name="演示主体", credit_type="general", report_as_of="2026-03-31",
        contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_3_finpresentation", "document_id": "doc-demo",
        "document_version": "sha256-demo", "raw_pdf_sha256": "0" * 64,
        "current_evidence_set_version": "evset-demo",
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in DS.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    projection = DS.project_contract_v2_scope(
        contract, profile,
        DS.build_scope_input_manifest(profile, business, source_inputs))
    real_reqs = {r.topic_id: r for r in projection.requirements}
    real_tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
    real_fin_topics = tuple(real_tasks["financial"].topic_ids)
    real_co_topics = tuple(real_tasks["company"].topic_ids)
    check("fin_source_scope" in real_fin_topics,
          f"真实冻结投影下财务节的选中 topic 是 {list(real_fin_topics)}：`fin_source_scope` "
          "**在**里面——这正是「入口缺省把它悄悄跳过」那条缺口的现场，夹具的 topic 集合必须"
          "与它同源")
    check(set(real_fin_topics) == {"fin_source_scope", "fin_solvency"}
          and real_co_topics == ("company_business",),
          "同一份投影下公司节是 `company_business`：本批三个 topic 的范围逐条可回查")

    real_scope_aspects = tuple(a.aspect_id for a in real_reqs["fin_source_scope"].aspects)
    check(set(real_scope_aspects) == set(_SCOPE_ROUTE_ASPECTS)
          and len(real_scope_aspects) == len(_SCOPE_ROUTE_ASPECTS) == 10,
          f"`fin_source_scope` 的冻结 aspect 集与 `ssr-1` 路由表**逐条相等**（Contract "
          f"{len(real_scope_aspects)} 条 / 路由 {len(_SCOPE_ROUTE_ASPECTS)} 条）：Contract "
          "新增一条要求而路由表没有登记读法时构造期会停住，不会少呈现一条")

    real_solv_tiers = {a.aspect_id: str(getattr(a, "display_tier", "") or "")
                       for a in real_reqs["fin_solvency"].aspects}
    #: `fpr-2` 起这条声明覆盖**两族**栏目：写正文的 `fin_solvency`，以及同一节确定性呈现的
    #: `fin_balance_structure`。断言因此从「都在 solvency 里」改成「都在**本次投影给出的**
    #: topic 里」——判据本身没有放宽，放宽的是本次真实范围内的 topic 数（profile v2）。
    #: 这一族从**冻结 Contract 本体**读（不由 profile 决定）：`fin_balance_structure` 是 Contract
    #: 的栏目，profile v1 只是**没有选中**它。拿测试夹具的三栏去对自己的映射表，等于自己对自己，
    #: 证明不了任何事。
    real_bs_tiers = {a.aspect_id: str(getattr(a, "display_tier", "") or "")
                     for sec in contract.sections for a in sec.all_aspects()
                     if str(getattr(a, "topic_id", "")) == "fin_balance_structure"}
    check({a for a, _t, _x in _BS_COLUMNS} == set(real_bs_tiers),
          f"测试侧那三栏与冻结 Contract 逐条相等：夹具 {sorted(a for a, _t, _x in _BS_COLUMNS)} "
          f"⇔ Contract {sorted(real_bs_tiers)}")
    check(set(FPR.METRIC_COLUMN_ROUTING.values()) <= set(real_solv_tiers) | set(real_bs_tiers),
          f"`fpr-2` 路由到的每一栏都在本次投影的 aspect 集里："
          f"{sorted(set(FPR.METRIC_COLUMN_ROUTING.values()))}")
    check({c for c in FPR.METRIC_COLUMN_ROUTING if c in FPR._BS_STRUCTURE_CODES
           or c in FPR._BS_ACCOUNT_CODES}
          == set(FW._BALANCE_SHEET_ITEMS),
          "科目两半（总量与结构 / 具体科目）**互斥且穷尽** `financial_worker._BALANCE_SHEET_ITEMS`："
          f"两表差集 {sorted(set(FW._BALANCE_SHEET_ITEMS) ^ set(FPR._BS_CODE_TO_COLUMN))}"
          "（少一条 code ⇒ 它在建表那一步整节停下来；多一条 ⇒ 本声明认领了一个不由本表管辖的科目）")
    check("fin_balance_structure.fifteen_pct_forced_analysis"
          not in set(FPR.METRIC_COLUMN_ROUTING.values())
          and "fin_balance_structure.fifteen_pct_forced_analysis" in real_bs_tiers,
          "第三栏 `fifteen_pct_forced_analysis` **故意**没有 code 落在它上面："
          "它的冻结 Contract 要求是「占资产或负债15%以上的科目强制分析」——"
          "它是**对同一组科目施加的筛选**，不是另有一组数字。给它编一组 code 出来，"
          "等于对读者说「有一批数字只属于这一栏」")
    check(real_solv_tiers.get(_PROXY_COLUMN) == "diagnostic_only"
          and real_solv_tiers.get(_SHORT_COLUMN) == "required_body",
          f"冻结 Contract 亲自把利息费用代理那一栏写成 `diagnostic_only`、把短期偿债写成 "
          f"`required_body`（实测 {real_solv_tiers.get(_PROXY_COLUMN)!r} / "
          f"{real_solv_tiers.get(_SHORT_COLUMN)!r}）："
          "「代理值不得进正文指标表」这句话来自 Contract，不是本层发明的口径")
    check(tuple(a.aspect_id for a in real_reqs["fin_solvency"].aspects)
          == tuple(c for c, _t, _x in _SOLVENCY_COLUMNS),
          "测试侧那份 `fin_solvency` 规格与冻结 Contract **逐条同序同名**："
          "缺口次序的断言才有意义（次序不同会掩盖「少了一栏」）")
    note("§0 只证明**路由表与冻结 Contract 对得上**；它不证明这两栏的内容已经写出来——"
         "那由离线读回与人工验收判。")

    # ============================================ §1 `fpr-2` 路由声明
    details.append("## §1 `fpr-2`：指标 → 栏目是**写明的声明**，且不冒充权威登记")
    task, authority = _authority()
    scan = PW.scan_authority(authority, task)
    check(scan.facts and all(not entry.aspect_ids for entry in scan.facts),
          f"前提：本节 {len(scan.facts)} 条财务权威事实的 `aspect_ids` **逐条为空**"
          "（`scan_financial` 不编造 aspect 状态）——路由**只能**走自己的那一路，"
          "不得借这个字段冒充权威认领")
    routing_input = _routing_input(authority, scan)
    check(tuple(str(f.fact_id) for f in routing_input)
          == tuple(sorted(str(e.fact_id) for e in scan.facts)),
          f"路由声明的输入事实**按事实身份**取自本节扫描读视图（{len(routing_input)} 条）："
          "扫描读视图 `AuthorityFactEntry` 不带指标 code，直接把读视图喂进声明会让"
          "`fact_columns` 恒为空、这条轴在真实数据上一次也不触发——这一条钉的就是那个错法")
    routing = _routing_for(routing_input)
    check({fid for fid, _c, _col in routing.fact_columns}
          == {str(f.fact_id) for f in routing_input},
          f"本节 {len(routing_input)} 条事实**逐条**落在某一栏（`fact_columns` 计数 "
          f"{len(routing.fact_columns)}），且 `unrouted_facts` 为空："
          f"实测 unrouted={[c for _f, c, _r in routing.unrouted_facts]}")
    check({code for _f, code, _col in routing.fact_columns}
          == {"SOLV_CURRENT_RATIO", "SOLV_INTEREST_COVER"},
          "落在栏里的是**指标 code**（从权威投影上读出来的那一批），不是扫描读视图的替代物")
    check(not any(code == str(getattr(e, "fact_id", "")) for _f, code, _c
                  in routing.fact_columns for e in scan.facts),
          "指标 code 与事实 id 是两回事：拿事实 id 充 code 会在栏归属上悄悄错位")
    check(routing is not None and routing.routing_version == "fpr-2"
          and routing.source == FPR.PRESENTATION_LAYER_SOURCE,
          "正例：本节选了 `fin_solvency` ⇒ 构造出**版本化**的路由声明，来源标记是呈现层"
          "（不是权威登记）")
    check(routing.contract_topic == "fin_solvency"
          and routing.route_topics == ("fin_solvency", "fin_balance_structure"),
          f"身份体记下**缺口账的 topic** 与**栏目可取自哪些 topic** 两件事，实测 "
          f"contract_topic={routing.contract_topic!r} route_topics={list(routing.route_topics)}："
          "一个声明覆盖两族栏目之后，只用一个单值 topic 是记不下这件事的")
    check(routing.column_for_metric_code("SOLV_CURRENT_RATIO") == _SHORT_COLUMN
          and routing.column_for_metric_code("SOLV_QUICK_RATIO") == _SHORT_COLUMN,
          "正例：流动比率 / 速动比率 → 短期偿债能力")
    check(routing.column_for_metric_code("SOLV_DEBT_RATIO") == _LONG_COLUMN
          and routing.column_for_metric_code("SOLV_EQUITY_MULT") == _LONG_COLUMN
          and routing.column_for_metric_code("SOLV_DEBT_RATIO_DELTA_PP") == _LONG_COLUMN,
          "正例：资产负债率 / 权益乘数 / 资产负债率的跨期变动 → 长期偿债（杠杆表述）"
          "（变动与它的输入指标同栏：变动说的是同一件事的走向，不是新的一栏）")
    check(routing.column_for_metric_code("INTEREST_BEARING_DEBT") == _BEARING_COLUMN,
          "正例：有息负债余额 → 有息负债")
    check(routing.column_for_metric_code("SOLV_INTEREST_COVER") == _PROXY_COLUMN
          and routing.display_tier_for_column(_PROXY_COLUMN) == "diagnostic_only",
          "正例：带 `PROXY_FINANCE_EXPENSES` 的利息保障倍数 → **诊断槽位**那一栏"
          "（该栏在 Contract 里就是 `diagnostic_only`），不混进正文主指标表")
    check(len(routing.routes) == len(FPR.METRIC_COLUMN_ROUTING),
          f"声明逐条落地：映射表 {len(FPR.METRIC_COLUMN_ROUTING)} 条 ⇔ 路由 "
          f"{len(routing.routes)} 条")
    check(tuple(routing.gap_columns) == (_NET_ASSET_COLUMN, _RIGID_DEBT_COLUMN),
          f"栏目缺口 = 冻结 Contract 的六栏 − 本次有指标可落的栏 = "
          f"{list(routing.gap_columns)}：净资产水平与刚性债务结构**如实留缺**"
          "（不得用流动比率充净资产，也不得用有息负债总额冒充完整债务结构）")
    check(all(g.reason == "no_registered_metric_in_selected_facts"
              and g.contract_requirement_text for g in routing.column_gaps),
          "每条缺口都带**冻结 Contract 的原文要求**与封闭原因码："
          "缺口说的话与要求说的话是同一句")
    check(not (set(routing.gap_columns)
               & {r.presentation_column for r in routing.routes}),
          "缺口栏与已路由栏**不重叠**：不得一边说「这栏没内容」一边把别的指标塞进去")
    check(all(col in routing.gap_columns
              or col in {r.presentation_column for r in routing.routes}
              for col in real_solv_tiers),
          "六栏每一条都**恰好**归入「已路由」或「缺口」之一：没有第三条去向"
          "（一种都不属于的栏会从读者面上凭空消失）")

    stray = _ff(code="CASH_ON_HAND", period=_P0, period_label=_T0,
                label="货币资金", display="30.80 元", unit="yuan")
    routing_stray = _routing_for((*routing_input, _projection_for(stray)))
    check(any(reason == "no_column_declared_for_metric_code"
              for _fid, code, reason in routing_stray.unrouted_facts
              if code == "CASH_ON_HAND")
          and routing_stray.column_for_metric_code("CASH_ON_HAND") is None,
          "反例 1a：映射表里没有的指标 code ⇒ 逐条记成 `unrouted_facts`，"
          "它**不属于任何一栏**（不得被写进其中任何一栏的正文）")
    check(not any(code == "CASH_ON_HAND" for _f, code, _c in routing_stray.fact_columns),
          "反例 1a-2：未路由事实**不进** `fact_columns`："
          "「这一栏有它」与「它不属于任何一栏」不能同时成立")

    check(FPR.build_presentation_routing(
              gap_scope_topic="fin_solvency", requirement=None, facts=routing_input) is None,
          "反例 1b：本节没有写正文的 topic ⇒ 返回 `None`（「没有这份声明」与「有一份空的"
          "声明」是两件事，后者会被读成「所有栏都缺」）")
    check(FPR.build_presentation_routing(
              gap_scope_topic="fin_solvency", requirement=_solvency_requirement(),
              facts=routing_input, extra_route_requirements=None)
          .column_for_metric_code("TOTAL_ASSETS") is None,
          "反例 1b-2：只给了 `fin_solvency` 的投影（v1 profile 的形状）⇒ 科目 code 那一族"
          "**不生效**，`TOTAL_ASSETS` 落回「没有为这个指标声明栏目」。两族各自独立登记，"
          "一族在场不等于另一族被认领")
    _expect_error(lambda: FPR.build_presentation_routing(
                      gap_scope_topic="", requirement=_solvency_requirement(), facts=()),
                  FPR.PresentationRoutingError, token="没有给出写正文的 topic")
    _expect_error(lambda: FPR.build_presentation_routing(
                      gap_scope_topic="fin_solvency",
                      requirement=T._Req("fin_solvency", ()), facts=()),
                  FPR.PresentationRoutingError, token="没有冻结 Contract 投影的栏目")
    note("反例 1c：没有写正文 topic 的名字、或它没有冻结 Contract 投影的栏目 ⇒ 构造期拒绝"
         "（没有栏目集合就无从判定「哪一栏是缺口」，不许凭空造一份声明）。")

    check("source" in routing.identity_body()
          and "routing_fingerprint" in routing.to_dict(),
          "来源标记与版本进身份体与 wire：读者能读回「这条栏目是哪来的」")
    check(_routing_with_source(routing, "authority_registration") is None,
          "反例 1d：把来源标记换成「权威登记」⇒ 构造期拒绝（声明不得冒充权威认领）")
    check(_routing_for(routing_input[:1]).fingerprint() != routing.fingerprint(),
          "事实集合一变，声明指纹就变（这份声明进本节清单的身份体，浅拷贝换不得）")

    # ============================================ §2 路由与展示层级逐行对上
    details.append("## §2 路由指向的那一栏在 Contract 里的档位，与事实自己的口径必须一致")
    manifest, tables = _metric_tables(routing=routing)
    check(SC.manifest_has_presentation_column_axis(manifest)
          and not SC.manifest_has_presentation_column_axis(_metric_tables(routing=None)[0]),
          "本节清单带**呈现层栏目归属轴**；没装声明的清单不带这一轴"
          "（轴在不在由清单自己说了算，不由读者猜）")
    tiers = {t.display_tier: t for t in tables.tables}
    check(set(tiers) == {"required_body", "diagnostic_only"},
          f"正例：普通指标进正文表、代理指标进诊断槽位，分成两张表（实测 {sorted(tiers)}；"
          f"refusals={[r.reason for r in tables.refusals]}）")
    check(tiers["diagnostic_only"].display_tier_basis
          == ("contract_display_tier", "authority_proxy_fact_marker"),
          f"诊断表的分层判据**两条都在**："
          f"{list(tiers['diagnostic_only'].display_tier_basis)}"
          "（Contract 说这一栏是诊断档 **且** 权威自己打了代理标记；"
          "只写一条会掩盖另一条是不是真的成立）")
    check(tiers["required_body"].display_tier_basis == (),
          "正文表**不带**分层判据：把「它凭什么进正文」写上去，等于给正文档也编一条判据")
    row_column = {row.label: row.presentation_column
                  for t in tables.tables for row in t.rows}
    check(row_column.get("流动比率") == _SHORT_COLUMN
          and row_column.get("利息保障倍数") == _PROXY_COLUMN,
          f"每一行都带它**按声明属于哪一栏**：{row_column}")
    check(row_column.get("流动比率") not in routing.gap_columns,
          "正文表里的行**不在**任何缺口栏里")
    cells = [c for t in tables.tables for row in t.rows for c in row.cells if c]
    check(len(cells) == 4 and all("倍" in c for c in cells),
          f"逐格渲染仍逐字取权威字段（{len(cells)} 格，全部含权威自己的单位「倍」）："
          "本批只加呈现层路由，不改格值、正负号、期间、单位与引用")

    bad_req = T._Req("fin_solvency", tuple(
        T._aspect(aspect_id, "fin_solvency", f"q-{aspect_id}",
                  display_tier=("diagnostic_only" if aspect_id == _SHORT_COLUMN else tier),
                  requirement_text=text)
        for aspect_id, tier, text in _SOLVENCY_COLUMNS))
    bad_routing = _routing_for(routing_input, requirement=bad_req)
    err = _expect_error(lambda: _metric_tables(routing=bad_routing),
                        CFT.CitedMetricTableError)
    check(getattr(err, "reason", "") == "display_tier_not_agreed_with_contract",
          f"反例 2a：Contract 把这一栏写成诊断档、而事实自己没有代理标记 ⇒ 构造期停下"
          f"（实测 reason={getattr(err, 'reason', '')!r}）："
          "两条判据对不上时不替任一方作决定")

    no_routing = _metric_tables(routing=None)[1]
    diag_without_routing = [t for t in no_routing.tables
                            if t.display_tier == "diagnostic_only"]
    check(len(diag_without_routing) == 1
          and diag_without_routing[0].display_tier_basis
          == ("authority_proxy_fact_marker",),
          "反例 2b：没有路由声明时，代理档仍由**权威自己的标记**判出来"
          "（不因缺声明而把代理行升进正文表），且判据如实只写「权威标记」这一条")

    # ============================================ §3 `css-1` 确定性来源呈现
    details.append("## §3 `css-1`：`fin_source_scope` 逐条出现、零模型调用、不复制偿债表")
    _, scope_authority = _authority(
        periods=(_P0, _P1, "2025-12-31"),
        statements_available=("balance_sheet", "income_statement"))
    scope_req = _scope_requirement()
    scope = CSS.build_cited_source_scope(
        section_id=_FIN_SECTION, topic_id="fin_source_scope", requirement=scope_req,
        artifact=scope_authority.artifact)
    check(tuple(i.aspect_id for i in scope.items)
          == tuple(a.aspect_id for a in scope_req.aspects)
          and len(scope.items) == len(scope_req.aspects),
          "逐条呈现，且次序**逐字等于**冻结 Contract 的 aspect 次序"
          "（不由路由表的次序决定）")
    check(scope.model_calls_issued == 0
          and scope.producer_kind == "deterministic_presentation"
          and scope.identity_body()["model_calls_issued"] == 0,
          "零模型调用进身份体：这一栏花过的模型额度恒等于 0，且是**产物上的读数字段**，"
          "不靠读者去别处推断")
    check(len(scope.determined) == 5 and len(scope.gaps) == 5,
          f"本次读数：已呈现 {len(scope.determined)} 条 / typed 缺口 {len(scope.gaps)} 条"
          "（三份材料都是报表本体，审计意见那三项在登记来源里没有承载字段；"
          "「金额单位」也不得由**币种**顶上来 —— 见下面的反例 3e）")

    by_aspect = {i.aspect_id: i for i in scope.items}
    bs = by_aspect["fin_statements_availability.balance_sheet_ready"]
    cash = by_aspect["fin_statements_availability.cashflow_statement_ready"]
    check(bs.state == "determined"
          and bs.authority_field == "artifact.statements_available"
          and bs.authority_value == "balance_sheet" and bs.reason == "",
          f"正例：资产负债表在本次登记的主表里 ⇒ 读数 + **取值字段** "
          f"（{bs.state} / {bs.authority_field} / {bs.authority_value!r}）")
    check(cash.state == "gap"
          and cash.reason == "not_provided_by_registered_sources" and not cash.statement,
          f"反例 3a：现金流量表不在登记集合里 ⇒ typed 缺口，且**不带呈现句** "
          f"（{cash.state} / {cash.reason}）：缺口不许用一句话把「没取到」写成「已经写了」")
    scope_col = by_aspect["fin_audit_opinion.consolidation_scope"]
    unit_col = by_aspect["fin_audit_opinion.amount_unit"]
    period_col = by_aspect["fin_audit_opinion.reporting_period"]
    check(scope_col.state == "determined"
          and scope_col.authority_field == "artifact.snapshot.scope"
          and scope_col.authority_value == "合并",
          "正例：合并范围逐字取自快照自己的 `scope` 字段")
    check(unit_col.state == "gap"
          and unit_col.reason == "not_provided_by_registered_sources"
          and not unit_col.statement,
          f"反例 3e（收紧后）：`fin_audit_opinion.amount_unit` **必须**落 typed 缺口"
          f"（得到 {unit_col.state} / {unit_col.reason}）：币种不是金额单位，"
          "本节权威没有承载「金额单位」的字段，这一栏就不许被判成「已呈现」")
    #: 币种这一点事实不得因为落缺口就消失：它写进缺口的**否定描述**字段里——那一段说的是
    #: 「本该承载它的位置是什么、本节权威在此处有什么、没有什么」，不是假字段名。
    check("币种" in unit_col.authority_field and "CNY" in unit_col.authority_field
          and "金额单位" in unit_col.authority_field
          and "统计口径" in unit_col.authority_field
          and "没有" in unit_col.authority_field
          and "币种不是金额单位" in unit_col.authority_field,
          f"反例 3e-2：缺口字段同句分列币种 / 金额单位 / 统计口径三根轴，写明币种 `CNY` "
          f"在册、后两者本节权威未承载（得到 {unit_col.authority_field!r}）")
    check(not unit_col.authority_field.startswith("artifact."),
          "反例 3e-3：缺口一侧的 `authority_field` 是**否定描述**，不许长得像一个真字段名"
          "（「字段存在但为空」会被读成已核对）")
    check(period_col.state == "determined"
          and period_col.authority_field == "artifact.periods"
          and "2025-12-31" in period_col.authority_value,
          "正例：报告期逐字取自快照声明的期间集合")
    audit_gaps = [i for i in scope.items
                  if i.aspect_id in ("fin_audit_opinion.audit_opinion_type",
                                     "fin_audit_opinion.accounting_firm",
                                     "fin_statements_availability.audit_opinion_ready")]
    check(len(audit_gaps) == 3
          and all(i.state == "gap" and not i.statement for i in audit_gaps),
          "审计意见齐备 / 类型 / 会计师事务所**逐条留缺**：本次材料是报表本体，"
          "这三个问题在登记来源里没有承载字段")
    check(not any(i.state == "determined" and i.authority_field.startswith("（")
                  for i in scope.items),
          "「已呈现」的每一条都必须报出一个**真实字段名**：缺口那一侧才写"
          "「本该承载它的位置」的否定描述，两者不共用形状")

    _expect_error(lambda: CSS.SourceScopeItem(
                      aspect_id="x", requirement_text="x", label="x", state="determined",
                      statement="", authority_field="artifact.periods"),
                  CSS.CitedSourceScopeError, token="必须有一句实际呈现")
    _expect_error(lambda: CSS.SourceScopeItem(
                      aspect_id="x", requirement_text="x", label="x", state="gap",
                      statement="这一项已经写出来了。", authority_field="",
                      reason="not_provided_by_registered_sources"),
                  CSS.CitedSourceScopeError, token="不得带呈现句")
    note("反例 3b：`determined` 不带呈现句、`gap` 带呈现句，两条都在构造期被拒"
         "（两种状态各有一条不许越界的边）。")
    _expect_error(lambda: CSS.build_cited_source_scope(
                      section_id=_FIN_SECTION, topic_id="fin_source_scope",
                      requirement=T._Req("fin_source_scope", (
                          T._aspect("fin_audit_opinion.unregistered", "fin_source_scope",
                                    "q-x", requirement_text="未登记的要求"),)),
                      artifact=scope_authority.artifact),
                  CSS.CitedSourceScopeError, token="没有登记读法")
    note("反例 3c：Contract 出现一条路由表没登记的要求 ⇒ 构造期停住"
         "（宁可不跑，也不许少呈现一条已要求的内容）。")
    _expect_error(lambda: CSS.CitedSourceScopePresentation(
                      section_id=_FIN_SECTION, topic_id="fin_source_scope",
                      schema_version=CSS.CITED_SOURCE_SCOPE_SCHEMA_VERSION,
                      policy_version=CSS.CITED_SOURCE_SCOPE_POLICY_VERSION,
                      routing_version=CSS.SOURCE_SCOPE_ROUTING_VERSION,
                      producer_kind="model_written", contract_topic_aspects=(),
                      items=()), CSS.CitedSourceScopeError, token="产出者身份")
    note("反例 3c-2：把产出者标成「模型写的」⇒ 构造期拒绝"
         "（这一栏的读数只能来自确定性呈现）。")

    md = CSS.render_cited_source_scope_markdown(scope)
    check("本栏**不经过模型**" in md and scope.topic_id in md
          and all(i.requirement_text in md for i in scope.items),
          "读者面写明「本栏不经过模型」，并逐条给出 Contract 原话")
    check(not any(name in md for name in ("流动比率", "利息保障倍数", "资产负债率", "倍")),
          "反例 3d：本栏**不**复制 `fin_solvency` 的偿债指标表充数——"
          "它问的是这些数值的来源与口径，拿偿债表的指标名和数值顶上去，"
          "等于用答案替掉问题")

    # ============================================ §4 真实模式：换谁写、批了几次
    details.append("## §4 真实模式的门：离线**一次都不记账**，真实模式拒绝时**没有发出去**")
    offline_gate, offline_model = CHAIN._cited_run_gate(mode="offline", model=None)
    check(offline_gate is None and offline_model is None,
          "离线模式不建预算门、不取模型身份：本模式下没有任何真实调用要批")

    from config import LLM_MODEL as PROJECT_WRITER_MODEL  # noqa: E402
    approved = str(PROJECT_WRITER_MODEL or "").strip()
    check(bool(approved),
          f"项目写作模型取自当前获批配置（`config.LLM_MODEL`，本机实测 {approved!r}）："
          "不取任何别的来源——把开发工具用的模型写进来，会以「这次跑的就是项目模型」"
          "的形态污染本 run 的全部读数")
    _expect_error(lambda: CHAIN._cited_run_gate(mode="real", model="some-other-model"),
                  SystemExit, token="与项目当前获批的写作模型")
    note("反例 4a：`--model` 与获批模型不一致 ⇒ **第一请求之前** `SystemExit`"
         "（换模型属于另一次裁决）。")

    real_gate, real_model = CHAIN._cited_run_gate(mode="real", model=None)
    summary0 = real_gate.summary()
    check(real_model == approved and summary0["attempt_total"] == 0
          and summary0["policy"]["total_max_attempts"] == CB.CITED_RUN_MAX_ATTEMPTS == 4
          and summary0["policy"]["approved_model"] == approved
          and summary0["policy"]["unapproved"] == [],
          f"正例：真实模式建出的门把上限表达成**整轮 4 次**、模型写死为获批模型、"
          f"无未批准量，且此刻一次都没记账（实测 {summary0['attempt_total']} 次）")
    per_category = {c["category"]: c for c in summary0["policy"]["categories"]}
    check(set(per_category) == {CB.CATEGORY_CITED_PROSE_WRITING,
                                CB.CATEGORY_CITED_PROSE_REVIEW}
          and per_category[CB.CATEGORY_CITED_PROSE_WRITING]["max_attempts_per_section"] == 1
          and per_category[CB.CATEGORY_CITED_PROSE_WRITING]["max_attempts_total"] == 2
          and per_category[CB.CATEGORY_CITED_PROSE_REVIEW]["max_attempts_per_section"] == 1
          and per_category[CB.CATEGORY_CITED_PROSE_REVIEW]["max_attempts_total"] == 2
          and summary0["policy"]["axes"] == [],
          "上限逐条落地：每节写作 1 / 写作共 2 / 每节审阅 1 / 审阅共 2，整轮 4，"
          "**返修不在类别集里**（本批未批），且**没有额外预算轴**"
          "（有轴就会多出一层可放大的额度）")
    check(CB.CATEGORY_CITED_PROSE_REWORK not in per_category
          and CB.cited_rework_approved() is False,
          "反例：返修类别**不在**真实模式的门上——它不是一个「上限为 0 的类别」，"
          "而是本批没有批这一类；两者在读者面上是两件事")
    check(CB.CITED_AUTOMATIC_RETRIES == 0,
          "自动重试为 0，且是模块常量而不是运行参数：重试额度不能在命令行上被放大")

    offline_payload = CHAIN._build_cited_payload(
        inputs=_inputs(), section_id=_FIN_SECTION,
        topic_ids=("fin_source_scope", "fin_solvency"),
        cited_topic_ids=("fin_solvency",), deterministic_topic_ids=("fin_source_scope",),
        mode="offline", approved_model=None, budget=real_gate)
    check(offline_payload["mode"] == "offline"
          and isinstance(offline_payload["prose_client"],
                         CHAIN.OfflineFactAssertionCitedProseClient)
          and isinstance(offline_payload["review_client"],
                         CHAIN.OfflineEchoCitedReviewClient),
          "正例：离线模式下两个客户端都是**本脚本内的替身**"
          "（替身就是替身，不算模型正文）")
    check(isinstance(offline_payload["presentation_routing"],
                     FPR.FinancialPresentationRouting)
          and [s.topic_id for s in offline_payload["source_scopes"]]
          == ["fin_source_scope"]
          and offline_payload["source_scopes"][0].model_calls_issued == 0,
          "离线链**一次读回就把两个 topic 都取到**：`fin_solvency` 走正文 + 路由声明，"
          "`fin_source_scope` 走确定性呈现且零模型调用")
    check(offline_payload["budget"] is real_gate
          and real_gate.summary()["attempt_total"] == 0,
          "**离线替身一次都没有碰账本**：整轮尝试仍为 0——替身不产生真实调用，"
          "因此「装了门」与「有调用」在账本上是两件事")

    has_key = bool(str(getattr(config, "DEEPSEEK_API_KEY", "") or "").strip())
    if not has_key:
        skip("§4b：本机没有配置模型凭据（`config.DEEPSEEK_API_KEY` 为空），"
             "真实客户端在 `get_client()` 处就会停住、走不到预算门——"
             "这一条反例在本机**没有真跑**，不得读成通过")
    else:
        with LB.section_scope(_FIN_SECTION):
            LB.install(real_gate)
            try:
                pre = LB.reserve_attempt(
                    call_id="fixture-pre-consumed",
                    prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                    model=approved, max_tokens=None)
                LB.settle_attempt(pre, status=LB.STATUS_OK)
            finally:
                LB.uninstall()
        check(real_gate.summary()["attempt_total"] == 1,
              "夹具前提：本节已消耗掉**唯一**一次写作额度（每节上限 1）")
        writer_err = _expect_error(
            lambda: CHAIN._build_cited_payload(
                inputs=_inputs(), section_id=_FIN_SECTION,
                topic_ids=("fin_source_scope", "fin_solvency"),
                cited_topic_ids=("fin_solvency",),
                deterministic_topic_ids=("fin_source_scope",),
                mode="real", approved_model=approved, budget=real_gate),
            CW.CitedWriterError)
        after = real_gate.summary()
        refused = after["refusals"][0] if after["refusals"] else {}
        check(after["attempt_total"] == 1,
              f"反例 4b：第二次写作请求**没有被记账**（尝试仍为 {after['attempt_total']} 次）"
              "——它在 `reserve` 处就被拒了，请求根本没有发出去")
        check(refused.get("category") == CB.CATEGORY_CITED_PROSE_WRITING
              and refused.get("section_id") == _FIN_SECTION
              and refused.get("reason") == "本节上限"
              and refused.get("prompt_version") == CW.CITED_WRITER_PROMPT_VERSION,
              f"拒绝如实记在**写作**类别与本节上（实测 {refused.get('category')!r} / "
              f"{refused.get('reason')!r} / {refused.get('prompt_version')!r}）："
              "**离线替身不发请求，因此它不可能在这条轴上留下记录**"
              "——这条记录本身就证明真实模式走的是 `LlmCitedProseClient`")
        check(getattr(writer_err, "reason", "") == "write_call_failed",
              "该调用失败以 `CitedWriterError`（`write_call_failed`）如实上抛："
              "不重试、不换模型、不静默继续")
        check(not any(row.get("category") == CB.CATEGORY_CITED_PROSE_REVIEW
                      for row in after["refusals"]),
              "反例 4c：写作一旦失败就**停在原地**，审阅额度没有被动用"
              "（一次失败不牵连另一栏）")

    check(CR.review_producer_kind_of(
              CHAIN.OfflineEchoCitedReviewClient(
                  check_report=offline_payload["check_report"]))
          == "offline_diagnostic_echo"
          and CR.review_producer_kind_of(CR.LlmCitedReviewClient(model=approved))
          == "independent_llm_review",
          "两种审阅客户端的**产出者身份**不同：离线回声永远够不上「独立审阅」，"
          "真实模式才走 `LlmCitedReviewClient`（身份由对象本身推出，调用方无从声明）")
    check(len(offline_payload["manifest"].facts) == len(_facts()),
          f"夹具前提：本节清单里的事实行就是那 {len(_facts())} 条")
    check(offline_payload["check_report"] is not None
          and offline_payload["review_outcome"] is not None,
          "离线链的两个替身产物都在：摘录/断言写作 + 回声审阅——"
          "它们只是**占位产物**，不构成内容验收")

    # ============================================ §5 读数乙：栏目数 ≠ 小节数
    details.append("## §5 读数乙：Contract 的**栏目**数与小节数是两个数（M930-3 定点纠正）")
    column_counts = CHAIN._contract_column_counts(
        offline_payload, offline_payload["source_scopes"])
    check(dict(column_counts) == {"fin_solvency": 6, "fin_source_scope": 10},
          f"读数乙读的是**冻结 Contract 的栏目数**（实测 {dict(column_counts)}）："
          "`fin_solvency` 6 栏、`fin_source_scope` 10 栏——不是写作小节的数")
    readings = CHAIN._financial_completeness_readings(
        metric_tables=offline_payload["metric_tables"], column_counts=column_counts,
        subsection_count=len(offline_payload["manifest"].subsections),
        region_count=0, confirmed_count=0, routing_column_gap_count=0)
    readings_text = "\n".join(readings)
    check("`fin_solvency` **6** 栏" in readings_text
          and "`fin_source_scope` **10** 栏" in readings_text
          and "写作**小节** **1** 个" in readings_text,
          "读者面**同句**印出 6 栏 / 10 栏与「写作小节 1 个」三个不同的数："
          "栏目轴与写作分段轴不得互相顶替")
    check("本主题 `1` 栏" not in readings_text,
          "反例 5a：不再有把**小节数**印成「本主题 N 栏」的旧写法"
          "（财务节的小节数恰为 1，会被读成「Contract 只有一栏」）")

    # ============================================ §5b 同一条纠正落进 §1 表下（`cfread-1`）
    details.append("## §5b `readback.md` §1 表下：小节数与 Contract 栏目数分开印，不再叫「栏目数」")
    # §1 的表**一行 = 一个写作小节**（列头是 `subsection_id`），表下原先只印一行
    # `- 栏目数：1`——财务节的小节数恰为 1，于是同一页既写着「1 栏」又列着 6 条 / 10 条
    # Contract 栏目要求与 2 条 typed 栏目缺口。这一条把纠正钉在**渲染出来的那两行**上。
    section1_lines = CHAIN._contract_columns_line(
        payload=offline_payload, source_scopes=offline_payload["source_scopes"],
        subsection_count=len(offline_payload["manifest"].subsections))
    section1_text = "\n".join(section1_lines)
    check("一行 = 一个写作小节" in section1_text and "**1** 个小节" in section1_text,
          f"§1 表下先说清表的**主语是小节**（实测 {section1_lines[0]!r}）："
          "列头就是 `subsection_id`，读的人不该把它读成 Contract 栏目")
    check("`fin_solvency` **6** 栏" in section1_text
          and "`fin_source_scope` **10** 栏" in section1_text,
          "同一段同句印出冻结 Contract 的 6 栏 / 10 栏：栏目数有它自己的出处，"
          "不由小节数推算")
    check("栏目数：**1**" not in section1_text and "栏目数：**" not in section1_text,
          "反例 5b：不再有「栏目数：N」这一行——它就是被误读的那一个写法")
    # 反例：换了 topic / 小节数，两行各随各的出处动，不是一对常数。
    other = CHAIN._contract_columns_line(
        payload=offline_payload, source_scopes=offline_payload["source_scopes"],
        subsection_count=7)
    other_text = "\n".join(other)
    check("**7** 个小节" in other_text and "`fin_solvency` **6** 栏" in other_text,
          "反例 5c：小节数换成 7，栏目数仍是 6 / 10——两行确实各取各的出处，"
          "不是把同一个数印两遍")

    note("本模块证明的只有**呈现层的路由与成本上限**。它不产生正式产物、不宣称财务内容门"
         "通过、不宣称 M930-3 / TS5 或任何正式阶段关闭；业务内容由离线读回与真实 run 后的"
         "人工读回判定。整个模块**没有发出任何模型请求**，也没有写库。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

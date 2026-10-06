"""M930-3 批次 B 反例集：整束被拒的 typed 审计（§二 3）与**待裁决**补件诉求的留存（§二 2.6）。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_rejection_retention`

本模块只测两件在真实 run 里被点名丢失的事，且都只测「不丢失」这一面——**不**声称任何内容质量：

1. **拒绝 ≠ 消失**（§二 3）：每一次未被采信的生成都必须留下**一条** typed 审计，含它那一束的
   完整有序身份与原因；被拒束不得被裁剪后冒充原提案集，也不得被当成缺口（缺口另有 Contract
   依据与检索范围）。特别是「结构合法、身份在权威侧闭不上」这一路：它与 `schema_invalid` 是
   两种不同的病，修法不同，因此**必须各有自己的 kind**，不得并成一类。
2. **诉求 ≠ 拒绝**（§二 2.6）：模型在**被拒那一轮**提出的补件诉求不得随候选一起蒸发，也不得
   被组合根自动执行。它落成 `follow_up_needs.json` 里的**待裁决提议**：状态
   `pending_adjudication`、`total_pending_needs`、逐节留存；已由 Harness 裁决并执行过的补件是
   另一套身份（`FollowUpExecutionResult`），两者不得混用，也不得把前者读成「已经补过件」。
3. **一条填错的申请 ≠ 整节消失**（§三/1）：本节**已经被采信**、Draft 已过硬门之后，模型提的
   补件申请里只要有一条既不成立（比如四个 id 跨行拼），过去会让**整节连同 Draft 一起消失**，
   而且产物里连「模型提过一条填错的申请」都读不到。现在不成立的那条照原样留成 typed 记录
   （封闭原因码 + 它填的四个 id + 它在自己那份响应里的序号 + 可读原因），成立的照常成为
   `FollowUpNeed`，硬门与 Draft 都不受影响。**校验一字未减**：需要「全有或全无」的入口仍
   原样抛出——本条测的就是这两条路并存（少一半就成了「把门放宽」）。
4. **分层绿 ≠ 组合绿**（§8）：§5/§7 用的是**替身**异常，§2 只到写侧边界。本节让一次**真实**
   的草稿闭合失败沿生产代码一路走到 run 级台账，证明相位入口不重包、驱动的取值口径与真实
   异常字段对齐。（§8 的**业务结论**一句也没有：它测的是去向不丢，不是内容合格。）

夹具全部是合成对象：不读真实库、不建真实 Pack、不调 LLM。例外的只有两处**源码文本对账**
（`_reject(` 的出口集合与 `raise UnadjudicatedFollowUpNeeds(` 的实参），它们对账的是本仓自己的
代码，不依赖任何环境；以及 §8 向 `test_m930_3_prose_occurrence` 借一个能触发真实闭合失败的
合成现场（单向引用），它借的仍是合成材料，不构成对真实 run 的任何读取。
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from harness import topic_schema as TS
from sections import company_worker as CW
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import source_role_scope as SRS

REPO = Path(__file__).resolve().parent.parent


class _NS:
    """只读命名空间替身（产物函数只按属性名读取容器，不要求真 Pack/真库对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _call_text(src: str, start: int) -> str:
    """从 `start`（某个调用的开头）取到括号配平为止的实参文本（不做语法分析，只配平括号）。"""
    depth = 0
    for index in range(start, len(src)):
        char = src[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return src[start:index + 1]
    raise AssertionError("括号未配平：源码文本对账夹具自身失效")


def _need(*, statement: str = "需要补充销售模式的一手口径材料。") -> TS.FollowUpNeed:
    """一个**真类**的 `FollowUpNeed`（走生产同一工厂与同一 schema 版本）。"""
    return TS.FollowUpNeed(
        need_id=TS.derive_follow_up_need_id(statement, "tr-1", "a1", "company", "dr-1"),
        need_schema_version=TS.FOLLOW_UP_NEED_SCHEMA_VERSION, statement=statement,
        target_requirement_id="tr-1", topic_id="t1", question_id="q1", aspect_id="a1",
        section_id="company", section_draft_revision="dr-1",
        contract_authorized_scope=("a1", "q1", "t1"), requiredness="required",
        expected_source_class="company_industry", budget_hint="", writer_identity="narr-x")


def _drive_inputs() -> _NS:
    """`_drive_into` 只从 `inputs` 上取四个注入面；本模块驱动的是**异常带出**那条路，
    因此四个注入面全部为 None（相位入口被替身接管，不落到真实研究/存储上）。"""
    return _NS(pack_store=None, resolver=None, dependencies_of=None)


def _state(*, pending: dict | None = None, runs: dict | None = None,
           sections: tuple = (), errors: dict | None = None,
           rejections: dict | None = None) -> ACC.RunState:
    state = ACC.RunState(inputs=_NS(), run_dir=REPO, mode=ACC.MODE_OFFLINE,
                         generated_at="2026-09-23T00:00:00Z", policy=_NS())
    state.pending_follow_up_needs = dict(pending or {})
    state.follow_up_runs = dict(runs or {})
    state.sections = dict.fromkeys(sections, object())
    state.section_errors = dict(errors or {})
    state.proposal_set_rejections = dict(rejections or {})
    return state


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

    # ==================================================================
    # 1. 产物与版本：两份新产物都必须被登记、被写盘、被诚实性说明引用
    # ==================================================================
    # 3.4：`proposal_set_rejections.json` 升到 /2（新增逐候选审计与栏目、`whole_set_rejected`）。
    # §二 分批：再升到 /3（每条被拒记录新增逐批调用读数 `batches`——**一次尝试不再等于一次调用**）。
    # 形状版本就是读者契约——加了字段却不升号，旧读者会把「只有整束 kind」当成审计的全部，
    # 或者把「一轮分批扫描被拒」读成「一次调用返回了一束坏 JSON」。
    # C3：再升到 /4（每条被拒记录新增 `answered_by_attempt` 与 `reproposal` 两个跨修订字段）。
    # 只加字段也要升号：旧读者会把 `reproposal=null` 读成「追过了、都还在」——这正是形状版本
    # 要拦下的误读。「没排定」与「排定而未成形」要分开，得两个字段一起读。
    # acc-24/①：再升到 /5（每条批记录新增 `parent_batch_id`）。它同样只加字段，但**读法变了**：
    # 「这次截断后来被缩批答回来了没有」在 /4 及更早**没有记录的父子关系**可读，于是 A7 只能退回
    # 「该节最终有没有 `SectionDraft`」这条**错的**判据（真实 run r4 实证：两次已经恢复的截断被
    # 记成结局缺口）。旧载荷并非无从判定（`label` 家族 + `shrink_depth` + `aspect_ids` 的保序
    # 划分足以**全或无**地重建父子关系，见 `_reconstruct_batch_children`），但那是**重建**而不是
    # 记录：旧读者据此会把「缩批答回了」与「走到尽头了」混成同一件事。
    # §三/1：再升到 /2（新增 `follow_up_rejected_applications` 与
    # `total_rejected_applications`）。升号的理由不是「多了字段」而是**读法变了**：/1 里
    # `follow_up_needs.json` 只有一种补件身份——「相位因此终止、等待裁决的诉求」。被采信节里
    # 模型提的、自己就不成立的那几条申请过去**没有身份**（它们让整节连同 Draft 一起消失），
    # 旧读者据此会把「本节一条待裁决诉求都没有」读成「模型没有提过任何补件」，把上面那个
    # 消失事件读成「本节本来就写不出东西」。
    # acc-31/第二段（C4）：再升到 /7（新增批次形状纠正的四个声明字段）。同样**只加字段**，
    # 但读法变了：/6 里「某一批被重问了一次」这件事在产物里**没有身份**——一次成功的前面
    # 可能已经重问过某一批（`shape_corrections`），旧读者会把「本轮的分批调用读数」当成
    # 「一次干净的扫描」，进而把由纠正换来的合格提案集读成「一次就成的」。同一批只纠正一次
    # 的上限与「与截断缩小共用额度、不新增预算」也必须落盘，否则复核者无法判断这次重问
    # 有没有靠抬高上限换结果。
    # §一.2（M930-3 定点批）：再升到 /8（新增逐候选裁出的 `carve_out` 与四个声明字段）。
    # 同样**只加字段**，但读法变了：/7 里「一束被拒」与「一束被**逐候选裁出**」在产物里完全
    # 同形——而后者恰恰不是「整束作废」，它让其余候选以新修订继续走链。旧读者会把本批要修掉的
    # **过度拒绝**读成正常结局。
    # 指令 D §三：再升到 /9（每条被拒记录新增 `retained_pre_gate` 门前留存）。同样**只加字段**，
    # 但读法变了：/8 里「一束被拒」只有**拒绝原因**可读，「这一轮到底写出过什么」读不出来
    # （真实 run r8 的前三批正文正是这样消失的，且不可恢复）。旧读者会把「这一节门前什么都没有」
    # 当成事实，而真相可能是「写出来了、但整束被拒」。
    check(ACC.FOLLOW_UP_NEEDS_SCHEMA_VERSION == "follow-up-needs/2"
          and ACC.PROPOSAL_SET_REJECTIONS_SCHEMA_VERSION == "proposal-set-rejections/9",
          f"两份产物的形状版本必须各自钉住（实为 {ACC.FOLLOW_UP_NEEDS_SCHEMA_VERSION} / "
          f"{ACC.PROPOSAL_SET_REJECTIONS_SCHEMA_VERSION}）")
    # C3：两个新字段的**判读窗口**必须出现在两处产物里（明细在产物、读数在报告），
    # 否则复核者要回头读 `pack_writer.py` 才知道 `reproposal` 是什么。
    _c3_payload = ACC._proposal_set_rejections_payload(_state())
    check(all(k in _c3_payload for k in ("candidate_reproposal_destinations",
                                         "reproposal_note_version",
                                         "reproposal_max_passes",
                                         "reproposal_trigger_kinds")),
          f"C3 的去向词表/说明版本/上限/触发 kind 必须落进产物顶层：{sorted(_c3_payload)}")
    # C4：批次形状纠正的**判读窗口**必须出现在产物顶层（版本 / 每批上限 / 触发码 / 共用额度），
    # 否则「同一批为什么被重问过」与「这次重问算不算预算」都只能回头读 `pack_writer.py`。
    check(all(k in _c3_payload for k in (
        "batch_shape_correction_note_version",
        "batch_shape_correction_max_per_batch",
        "batch_shape_correction_trigger_kind",
        "batch_shape_correction_slack_pool")),
        f"C4 的版本/上限/触发码/共用额度必须落进产物顶层：{sorted(_c3_payload)}")
    check(_c3_payload.get("batch_shape_correction_note_version")
          == PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION
          and _c3_payload.get("batch_shape_correction_trigger_kind")
          == PW.BATCH_SHAPE_CORRECTION_TRIGGER_KIND
          and _c3_payload.get("batch_shape_correction_max_per_batch")
          == PW.MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH,
          "产物里声明的纠正版本/触发码/每批上限必须与写手侧常量逐字相同（声明 ⟺ 实现）")
    _pool = dict(_c3_payload.get("batch_shape_correction_slack_pool") or {})
    check(_pool.get("new_budget") is False
          and _pool.get("shared_with") == "batch_truncated_shrink"
          and _pool.get("max_steps_per_round") == int(PW.MAX_SWEEP_SHRINK_STEPS),
          f"纠正必须声明为与截断缩小**共用**同一份额度（不新增预算）：{_pool}")
    for artifact in ("follow_up_needs.json", "proposal_set_rejections.json"):
        check(artifact in ACC.ARTIFACTS, f"{artifact} 必须登记在 ARTIFACTS 里")
    check(len(set(ACC.ARTIFACTS)) == len(ACC.ARTIFACTS),
          "ARTIFACTS 不得有重复项（产物清单即目录契约）")
    _runner_src = Path(ACC.__file__).read_text(encoding="utf-8")
    check(_runner_src.count('_write_json(state.run_dir / "follow_up_needs.json"') == 1,
          "待裁决诉求产物必须有**恰好一处**写盘点")
    check('"follow_up_needs": {' in _runner_src
          and '"artifact": "follow_up_needs.json"' in _runner_src,
          "manifest.json 必须有一个 `follow_up_needs` 块指向该产物（否则产物存在但无人引用）")
    check("_follow_up_needs_honesty(state)" in _runner_src,
          "待裁决诉求必须在报告顶部的诚实性说明里可见（不能只藏在一个 JSON 文件里）")
    check('"directed_reproposal"' in _runner_src
          and "**C3（定向重提案）**" in _runner_src,
          "C3 的判读窗口必须同时出现在产物载荷与报告的 `proposal_set_rejections` 块里")
    check(ACC.RUNNER_VERSION == "m930-3-acc-40",
          f"acc-24/① 把批次谱系带上产物（`proposal-set-rejections/4` → `/5`），acc-25/⑤ 再把"
          f"内容形态读法带上材料包与写手输入面（`material-pack/1` → `/2`、`pw-10` → `pw-11`），"
          f"acc-26/② 把读根资格（`anp-4` → `anp-5`、`anps-3` → `anps-4`）带上导航回读面，"
          f"acc-27/⑥ 再把「被拒之后的有界定向重组织」与正文第 9 条带上 A3 证据块"
          f"（新增 `self_reported_provenance`），acc-28/§三1 再把「**被采信那一轮**里自己不成立"
          f"的补件申请」带上产物与报告（`follow-up-needs/1` → `/2`、`failure-diagnostics/1` → "
          f"`/2`），acc-29/第二段 再把勾选表单行的**栏目归属**与**支撑资格**带上材料包与逐候选"
          f"拒绝审计（`material-pack/2` → `/3`、`proposal-set-rejections/5` → `/6`），"
          f"acc-30/①②③ 再把蕴含边的标点归一化镜像读法（`cer-3` → `cer-4`）与「生产 need 构造器 "
          f"+ 外部授权执行前门」带上链路，acc-31/r7b 后定点返修第一段再把 A2 的单元格判据从"
          f"「整格连续子串」换成已裁决的**分量**规则（数值 / 期间表达 / 完整限定语各自逐字"
          f"出现），第二段/②再把「批次形状纠正」这条**有界、只对失败批次**的重问路径与其"
          f"共用额度声明带上产物（`proposal-set-rejections/6` → `/7`），acc-32/M930-3 定点批 §一.2"
          f" 再把「一束被拒不必然整束作废」这条**逐候选裁出**出口与其零调用声明带上产物"
          f"（`proposal-set-rejections/7` → `/8`），acc-33/§一.2 读者面再把主体名称改由声明"
          f"给出并与权威来源文档登记逐字核对（`subj-1` → `subj-2`），acc-34/指令 D §二（b）"
          f"来源归属轴的读者面（`srattr-1`）再新增 `source_attribution.json` / `.md` 与报告侧"
          f"指针块（归属语只由**系统**从已登记身份渲染，写者一个字也不能写，且不改正文与正文"
          f"指纹），acc-35/指令 D §三 再把**失败侧的可读面**带上产物（`pgr-1` 门前留存："
          f"`proposal-set-rejections/8` → `/9`，逐批草稿正文 + 逐单元出处轴 + 三档来源；"
          f"新增 `pre_gate_draft.json` / `.md` 与报告侧 `pre_gate_draft` 指针块；判据一字未减，"
          f"它不参与任何门、不进正文与预览、也不放宽任何门），acc-36/业务取材纵链修复 §一 再把"
          f"**逐条 typed 栏目未达原因**带上诊断与人读页（`RealInputs.topic_results` 留一份运行"
          f"现场，逐 aspect 新增 `column_unmet`、逐节 `column_unmet_summary`，"
          f"`failure-diagnostics/2` → `/3`；判据一字未减：五条原因彼此不可互推，不得合并成笼统的"
          f"`coverage_gate_not_met`，也不得写成「语料里没有」），acc-37/业务取材纵链修复 §二 再把"
          f"**表对象信道的读法**带上材料包读回（表对象信封按设计不带 §二 2.3 的 "
          f"`content_qualification`，过去一律落 `None` ⇒ 已准入、已保留、已进 Writer 清单的"
          f"表对象被印成「内容形态：**读不出**」并落进 `unavailable`；现在另成 "
          f"`kind=table_object` 一族并读同信封的 `reading_policy`——`permitted_use` 与逐条"
          f"排除项，节级新增 `table_object_material_ids`，`material-pack/3` → `/4`；判据一字未减："
          f"「这张表能读」与「表里的数字能以它为准」是两条**正交**声明），acc-38/业务取材纵链"
          f"修复 §三 再把**替身选材与组织**改到位（过长原句按小句边界切成有界原子逐条过滤；"
          f"候选原子必须自带陈述对象——「并通过长期协议…」「能满足快充…」这类承接残片不再进"
          f"正文并逐条记 typed 未采用原因，不补主语；材料按来源角色稳定重排后提案，同一段原文"
          f"同时落在较新与较旧两份同类材料里时不再由较旧的那份先占住；组织侧每个接缝各取一个"
          f"中性连接语。判据一字未减：只改替身提案与行文，Pack、Writer 清单与各道门一律不动，"
          f"候选被拒仍即整节 fail-closed），acc-39/§0.18 W8 单通道再把**读取面**改到位"
          f"（目标表的正式材料改由图侧单通道产出，信封种类由 `tom-1` 换成 `gtm-1`；读回侧"
          f"过去只认 `tom-1` ⇒ 新信道的表材料落回「读不出形态」，与 acc-37 修掉的是同一个错；"
          f"现同时认两条并带出**实际观察到的** `envelope_kind`，`material-pack/4` → `/5`。"
          f"判据一字未减），acc-40/M930-3 定点业务纠正① 再把**勾选行的栏目归属**改到位"
          f"（读法 `tmr-2` → `tmr-3`：所问事项**只**取行内前缀，行内没写主语时这一列留空，"
          f"**不再**回指所在节点标题——回指会把**子项**的勾选状态锚到整个**栏目**上，真实反例是 "
          f"NDSD_2025 第 28 页那行 `□适用 不适用`；读回侧把「所在节点」标成**仅导航坐标**。"
          f"判据一字未减：空所问事项 fail-closed，材料一份未删，报告载荷形状未变、"
          f"报告 schema 仍停在 report-34），版本随之前进，实为 "
          f"{ACC.RUNNER_VERSION}")
    # 谱系的两档**依据**（记录 / 重建）与重建规则自身的版本号必须一起公开：只报
    # `recovered_by_shrink` 而不说这条结论建立在哪一档上，读者无法判断它能撑多重；
    # 而重建规则一改，历史判读结论的可信范围随之改变，因此规则也要有号。
    _trunc_policy = ACC.truncation_policy(
        _NS(budget=None, sections={}, section_errors={}, proposal_set_rejections={},
            rollback=None, mode=ACC.MODE_OFFLINE),
        narrator=_NS(calls=[]), entailment=_NS(calls=[]))
    check(_trunc_policy["lineage_bases"] == list(ACC.LINEAGE_BASES)
          and _trunc_policy["lineage_reconstruction_version"]
          == ACC.BATCH_LINEAGE_RECONSTRUCTION_VERSION
          and set(ACC.LINEAGE_BASES) == {"recorded_parent", "reconstructed_by_label"},
          f"截断读数必须公开谱系的两档依据与重建规则的版本号（实为 "
          f"{_trunc_policy.get('lineage_bases')} / "
          f"{_trunc_policy.get('lineage_reconstruction_version')!r}）")
    check("reconstructed_by_label" in _trunc_policy["note"]
          and "recorded_parent" in _trunc_policy["note"],
          "两档依据的口径必须在同一份 note 里讲清（否则 `lineage_basis` 只是一个裸字符串）")
    check(_trunc_policy["lineage_outcomes"] == list(ACC.TRUNCATION_LINEAGE_OUTCOMES),
          "三档结局词表必须与 `TRUNCATION_LINEAGE_OUTCOMES` 同源（不得各写一份）")

    # ==================================================================
    # 2. 空态：没有待裁决诉求时如实为空，且不发明条目
    # ==================================================================
    empty = _state(sections=("company", "industry", "financial"))
    empty_payload = ACC._follow_up_needs_payload(empty)
    check(empty_payload["status"] == "pending_adjudication"
          and empty_payload["total_pending_needs"] == 0
          and empty_payload["total_untypeable"] == 0
          and empty_payload["sections"] == {} and empty_payload["adjudicated_runs"] == {},
          f"没有待裁决诉求时必须如实为空（不得发明空条目），实为 {empty_payload}")
    check(ACC._pending_needs_total(empty) == 0,
          "`_pending_needs_total` 必须与载荷用同一个计数（两处计数各写一份必然漂移）")
    check("没有**待裁决**" in ACC._follow_up_needs_honesty(empty)
          and "没有" in ACC._follow_up_needs_honesty(empty),
          "空态也必须说清「没有待裁决诉求」，而不是什么都不说")

    # ==================================================================
    # 3. 非空态：逐节留存 + 与**已执行**的补件运行分开陈述
    # ==================================================================
    pending = {
        "company": {"follow_up_needs": [PW._jsonable(_need())],
                    "follow_up_untypeable": [{"attempt": 1, "statement": "x",
                                              "aspect_id": "", "topic_id": "topic-forged",
                                              "reason": "PackWriterError: 不属于本节权威输入"}],
                    "phase_error": "UnadjudicatedFollowUpNeeds: 不能裁决"},
        "industry": {"follow_up_needs": [PW._jsonable(_need(statement="需要补充行业口径材料。"))],
                     "follow_up_untypeable": [],
                     "phase_error": "UnadjudicatedFollowUpNeeds: 重写轮数用尽"},
    }
    state = _state(pending=pending,
                   runs={"company": (object(), object()), "financial": (object(),)},
                   sections=("company", "industry"),
                   errors={"industry": "UnadjudicatedFollowUpNeeds: 重写轮数用尽"})
    payload = ACC._follow_up_needs_payload(state)
    check(payload["total_pending_needs"] == 2 == ACC._pending_needs_total(state)
          and payload["total_untypeable"] == 1,
          f"逐条计数必须与逐节点数一致（实为 {payload['total_pending_needs']} / "
          f"{payload['total_untypeable']}）")
    check(sorted(payload["sections"]) == ["company", "industry"]
          and "financial" not in payload["sections"],
          "只有真的有待裁决诉求的节才出现在产物里（不得每节都写一个空壳）")
    check(payload["adjudicated_runs"] == {"company": 2, "financial": 1},
          f"**已执行**的补件运行是另一套身份，必须单独计数且不与待裁决混用，实为 "
          f"{payload['adjudicated_runs']}")
    check(payload["sections_completed"] == ["company", "industry"],
          "已完成的节必须如实登记（「有诉求」与「本节跑完了」是两件事）")
    check("pending_adjudication" == payload["status"] and "未被执行" in payload["note"]
          and "不是 gap" in payload["note"] and "FollowUpExecutionResult" in payload["note"]
          and "尚未被 Harness 裁决" in payload["note"],
          f"载荷必须自己说清身份：待裁决、未执行、不是 gap、与已执行补件分属两套身份，实为 "
          f"{payload['note']}")
    check(json.loads(json.dumps(payload, ensure_ascii=False)) == payload,
          "产物必须可 JSON 往返（落盘形态即读回形态）")
    honesty = ACC._follow_up_needs_honesty(state)
    check("2 条" in honesty and "待裁决" in honesty and "未执行" in honesty
          and "不是** gap**" not in honesty and "公司节" not in honesty,
          f"诚实性说明必须同时给出条数、待裁决/未执行的性质，且不写任何公司专用措辞，实为 "
          f"{honesty}")
    check("company" in honesty and "industry" in honesty,
          "诚实性说明必须逐节点名（只给总数会让「哪一节提出的」无从判断）")

    # ==================================================================
    # 4. 相位异常：诉求必须**结构化**带出，而不是只剩消息文本
    # ==================================================================
    check(issubclass(CW.UnadjudicatedFollowUpNeeds, CW.BackboneWriterPhaseError),
          "该终止态必须是相位错误的子类（调用方按覆盖写捕获，不需要知道新类型）")
    probe = CW.UnadjudicatedFollowUpNeeds("x", section_id="company",
                                          follow_up_needs=(_need(),))
    check(probe.section_id == "company" and len(probe.follow_up_needs) == 1
          and not hasattr(probe, "rejections"),
          "它只承载**待裁决诉求**：不得长成拒绝审计的别名（两件事两套字段）")
    _cw_src = Path(CW.__file__).read_text(encoding="utf-8")
    positions = []
    start = _cw_src.find("raise UnadjudicatedFollowUpNeeds(")
    while start != -1:
        positions.append(_call_text(_cw_src, start + len("raise UnadjudicatedFollowUpNeeds")))
        start = _cw_src.find("raise UnadjudicatedFollowUpNeeds(", start + 1)
    check(len(positions) >= 4,
          f"相位停止的每个出口都必须走这个 typed 终止态（实为 {len(positions)} 个出口）")
    check(all("follow_up_needs=" in text for text in positions),
          f"每一个出口都必须**带上**诉求本身（只写条数等于把诉求丢在异常里）："
          f"{[t[:90] for t in positions if 'follow_up_needs=' not in t]}")

    # ==================================================================
    # 5. 驱动侧：异常带出的诉求真的进了现场容器（留存 ≠ 执行）
    # ==================================================================
    real_section_input = ACC._section_input
    real_run_phase = CW.run_formal_m930_writer_phase
    need = _need()
    try:
        ACC._section_input = lambda inputs, section_id: _NS(section_id=section_id)

        def _boom(section_inputs, **kwargs):
            """只有 company 节带出诉求；其余两节是**普通的**相位错误——两个方向一起验。"""
            section_id = str(section_inputs[0].section_id)
            if section_id == "company":
                raise CW.UnadjudicatedFollowUpNeeds(
                    "本相位无法裁决：重写轮数为 0", section_id=section_id,
                    follow_up_needs=(need,))
            raise CW.BackboneWriterPhaseError(f"普通装配错误（{section_id}）")

        CW.run_formal_m930_writer_phase = _boom
        sections: dict = {}
        errors: dict = {}
        commits: dict = {}
        pending_out: dict = {}
        rejections_out: dict = {}
        ACC._drive_into(_drive_inputs(), sections=sections, errors=errors, commits=commits,
                        policy=_NS(), generated_at="2026-09-23T00:00:00Z",
                        llm_client=None, entailment_llm_client=None,
                        final_sentence_llm_client=None, section_store=None,
                        rejections=rejections_out, pending_follow_up=pending_out)
    finally:
        ACC._section_input = real_section_input
        CW.run_formal_m930_writer_phase = real_run_phase
    check(sorted(errors) == list(ACC.SECTION_ORDER),
          f"三个节都必须被驱动（一节失败不牵连别的节），实为 {sorted(errors)}")
    check(sorted(pending_out) == ["company"],
          f"只有真的带出诉求的节才写进现场（不得为每个失败节写空壳），实为 "
          f"{sorted(pending_out)}")
    captured = pending_out.get("company", {})
    check(captured.get("follow_up_needs") == [PW._jsonable(need)]
          and captured.get("follow_up_untypeable") == []
          and str(captured.get("phase_error", "")).startswith("UnadjudicatedFollowUpNeeds: "),
          f"现场必须留下诉求本身 + typed 终止原因，实为 {captured}（errors={errors}）")
    check(not rejections_out.get("company"),
          "**没有**被拒束时不得凭空写一条拒绝审计（留存的两件事不得互相填充）")

    # 反例不是恒有：没有诉求的失败节不得在现场留下条目。
    try:
        ACC._section_input = lambda inputs, section_id: _NS(section_id=section_id)
        CW.run_formal_m930_writer_phase = lambda sections, **kwargs: (_ for _ in ()).throw(
            CW.BackboneWriterPhaseError("普通的相位装配错误"))
        no_need_pending: dict = {}
        ACC._drive_into(_drive_inputs(), sections={}, errors={}, commits={}, policy=_NS(),
                        generated_at="2026-09-23T00:00:00Z", llm_client=None,
                        entailment_llm_client=None, final_sentence_llm_client=None,
                        section_store=None,
                        pending_follow_up=no_need_pending)
    finally:
        ACC._section_input = real_section_input
        CW.run_formal_m930_writer_phase = real_run_phase
    check(no_need_pending == {},
          f"没有诉求的失败不得写一条空的待裁决条目（实为 {no_need_pending}）")

    # ==================================================================
    # 6. 写侧：失败轮的诉求 → (可类型化, 不成立)，两桶都不丢
    # ==================================================================
    task = _NS(section_id="company", questions=(_NS(topic_id="t1", question_id="q1"),))
    authority = _NS(topic_ids=("t1",))
    scan = _NS(aspect_topic={"a1": "t1"}, aspect_question={"a1": "q1"},
               requirement_ids={"t1": "tr-1"}, aspect_impact={})
    policy = _NS(prompt_version="narr-x")
    good = {"statement": "需要补充销售模式的一手口径材料。", "target_requirement_id": "tr-1",
            "topic_id": "t1", "question_id": "q1", "aspect_id": "a1",
            "requiredness": "required", "expected_source_class": "company_industry",
            "budget_hint": "tree_inspect:1"}
    forged = dict(good, topic_id="topic-forged")
    truncated = {"statement": "缺字段的原始诉求"}
    typed, untypeable = PW._pending_follow_up_needs(
        captured=((1, (good, forged)), (2, (truncated,))), task=task, authority=authority,
        scan=scan, revision_for=lambda n: f"dr-{n}", policy=policy)
    check(len(typed) == 1 and isinstance(typed[0], TS.FollowUpNeed)
          and typed[0].aspect_id == "a1" and typed[0].section_draft_revision == "dr-1",
          f"可类型化的诉求必须真的做成真类 `FollowUpNeed`（不是原样 dict），实为 {typed}")
    check(len(untypeable) == 2
          and {u["attempt"] for u in untypeable} == {1, 2}
          and all(u["reason"] for u in untypeable)
          and any(u["topic_id"] == "topic-forged" for u in untypeable),
          f"不成立的诉求（含**缺字段**这种非 PackWriterError 的形态）必须逐条留存并写出原因，"
          f"实为 {untypeable}")
    check(all("topic-forged" in u["reason"] or "不在" in u["reason"]
              or "KeyError" in u["reason"] for u in untypeable),
          f"原因是可读的 typed 说明（不是空串也不是整段堆栈），实为 "
          f"{[u['reason'] for u in untypeable]}")
    check(PW._pending_follow_up_needs(
        captured=(), task=task, authority=authority, scan=scan,
        revision_for=lambda n: f"dr-{n}", policy=policy) == ((), ()),
        "没有任何失败轮诉求时两桶都为空（不得凭空造一条）")

    # 空预算提示：请求示例已经改成非空，但入站防线不能只靠示例自觉——模型仍可能给出空串，
    # 而检索侧（`TR.build_follow_up_focus`）对空白字段是 fail-closed 的。这一格必须在这一条
    # 诉求上**当场**变成 typed 拒绝并留下审计（`spec_index` 指回它在这次响应里的序号），
    # 不能等到 Harness 获批执行时才抛，也不能因此丢掉同一份响应里其它合法的申请。
    blank_budget = dict(good, statement="需要补充销售模式的一手口径材料。", budget_hint="")
    keep_b, dropped_b = PW._follow_up_needs_partition(
        specs=(good, blank_budget), task=task, authority=authority, scan=scan,
        draft_revision="dr-1", policy=policy, attempt=1)
    check(len(keep_b) == 1 and len(dropped_b) == 1
          and dropped_b[0]["code"] == "budget_hint_empty"
          and dropped_b[0]["spec_index"] == 1
          and dropped_b[0]["statement"] == blank_budget["statement"],
          f"空 `budget_hint` 必须逐条 typed 拒绝并指明序号、原样带回它自己写的诉求，"
          f"实为 {dropped_b}")
    check(len(keep_b) == 1 and keep_b[0].statement == good["statement"]
          and keep_b[0].budget_hint == "tree_inspect:1",
          f"同一条形状补上非空预算后必须照常成立（被拒的是**那一格**，不是这条诉求本身），"
          f"实为 {keep_b}")
    check("budget_hint_empty" in PW.FOLLOW_UP_REJECTION_CODES,
          "空预算提示的原因码必须在封闭词表内（不得随手造一个）")
    # 空白（不只是空串）同样落在这条门上：`\"   \"` 进检索和 `\"\"` 是同一种「注定执行不了」。
    blank_ws = dict(good, budget_hint="   ")
    _keep_w, dropped_w = PW._follow_up_needs_partition(
        specs=(blank_ws,), task=task, authority=authority, scan=scan,
        draft_revision="dr-1", policy=policy, attempt=1)
    check(not _keep_w and len(dropped_w) == 1
          and dropped_w[0]["code"] == "budget_hint_empty",
          f"只有空白的预算提示必须与空串同样被拒，实为 {dropped_w}")

    # 归属必须是**提出它的那一次生成**：同一句诉求在第 1 轮和第 3 轮提出，是两条不同的
    # 待裁决提议（修订不同 ⇒ need_id 不同）。给整节一个统一修订，会把「第 1 轮说的」与
    # 「第 3 轮说的」在产物里压成同一条，于是「它当时依据的是哪一束候选」无从回查。
    per_attempt, _ = PW._pending_follow_up_needs(
        captured=((1, (good,)), (3, (good,))), task=task, authority=authority, scan=scan,
        revision_for=lambda n: f"dr-{n}", policy=policy)
    check([n.section_draft_revision for n in per_attempt] == ["dr-1", "dr-3"]
          and len({n.need_id for n in per_attempt}) == 2,
          f"同一句诉求在不同轮次提出必须归属各自那一轮的修订、并给出不同 need_id，"
          f"实为 {[(n.section_draft_revision, n.need_id) for n in per_attempt]}")

    # 写入侧**不得**具备执行能力：`_pending_follow_up_needs` 只类型化，不裁决、不执行。
    _pw_src = Path(PW.__file__).read_text(encoding="utf-8")
    check("run_follow_up_needs(" not in _pw_src
          and "dependencies_of" not in _pw_src,
          "写入侧不得持有 Harness 的裁决入口（留存 ≠ 执行；越权执行等于绕开 Contract/预算/"
          "SourcePolicy 那条门）")

    # ==================================================================
    # 6b. §三/1：**被采信那一轮**里自己不成立的申请——typed 记录，且不吞掉本节
    # ==================================================================
    # 真实 run r5 的 financial：候选与草稿单元都成形、硬门已过，随后在门后构造补件时，
    # 一条申请的四个 id 跨行拼了（`topic_id` 取自一行、`aspect_id` 取自另一行），于是
    # `_follow_up_needs` 整节抛出——**已经把内容写出来的那一节连同 Draft 一起消失**，
    # 产物里连「模型提过一条填错的申请」都读不到（它长得像「本节写不出内容」）。
    # 下面这件事与 6 的区别是「相位后果」而不是「判定规则」：校验一字未减，变的是不成立的
    # 范围只到这一条。
    cross_scan = _NS(aspect_topic={"a1": "t1", "a2": "t2"},
                     aspect_question={"a1": "q1", "a2": "q2"},
                     requirement_ids={"t1": "tr-1", "t2": "tr-2"}, aspect_impact={})
    cross_task = _NS(section_id="company", questions=(_NS(topic_id="t1", question_id="q1"),
                                                      _NS(topic_id="t2", question_id="q2")))
    cross_authority = _NS(topic_ids=("t1", "t2"))
    # 对照面：topic/question/需求三件都是**合法的一行**（t2/q2/tr-2），只有 aspect 跨了行
    # （a1 属于 t1）。于是它只可能落在 `aspect_not_in_topic` 这一个原因码上。
    cross_mixed = {"statement": "需要补充另一主题下的口径材料。", "target_requirement_id": "tr-2",
                   "topic_id": "t2", "question_id": "q2", "aspect_id": "a1",
                   "requiredness": "required", "expected_source_class": "company_industry",
                   "budget_hint": "tree_inspect:1"}
    good_cross = {"statement": "需要补充本主题的一手口径材料。", "target_requirement_id": "tr-1",
                  "topic_id": "t1", "question_id": "q1", "aspect_id": "a1",
                  "requiredness": "required", "expected_source_class": "company_industry",
                  "budget_hint": "tree_inspect:1"}
    keep, dropped = PW._follow_up_needs_partition(
        specs=(good_cross, cross_mixed), task=cross_task, authority=cross_authority,
        scan=cross_scan, draft_revision="dr-1", policy=policy, attempt=3)
    check(len(keep) == 1 and keep[0].aspect_id == "a1" and keep[0].topic_id == "t1"
          and keep[0].section_draft_revision == "dr-1",
          f"同一次响应里成立的申请必须照常成为真类 `FollowUpNeed`（实为 {keep}）")
    check(len(dropped) == 1 and dropped[0]["code"] == "aspect_not_in_topic"
          and dropped[0]["spec_index"] == 1 and dropped[0]["attempt"] == 3,
          f"填错的那条必须留下**恰好一条** typed 记录并指明它在那份响应里的序号，"
          f"实为 {dropped}")
    check("同一行" in dropped[0]["reason"] and dropped[0]["aspect_id"] == "a1"
          and dropped[0]["topic_id"] == "t2",
          f"记录必须原样带上它自己填的四个 id 与可读原因（否则「哪条错、错在哪」只能猜），"
          f"实为 {dropped[0]}")

    # 全有或全无的入口在**同一份输入**上仍然抛：逐条隔离是新增的一条路，不是把老门放宽。
    # 少了这条对照，「不吞掉整节」与「校验被拆掉」在测试里长得一模一样。
    try:
        PW._follow_up_needs(specs=(good_cross, cross_mixed), task=cross_task,
                            authority=cross_authority, scan=cross_scan,
                            draft_revision="dr-1", policy=policy)
    except PW.FollowUpNeedRejected as exc:
        check(exc.code == "aspect_not_in_topic" and exc.spec_index == 1,
              f"`_follow_up_needs` 仍必须整节抛出，且带出原因码与序号（实为 "
              f"{exc.code}/{exc.spec_index}）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"`_follow_up_needs` 抛出了别的异常类型：{type(exc).__name__}")
    else:
        check(False, "`_follow_up_needs` 必须仍然 fail-closed（对照面：逐条不影响它）")

    # 缺字段那条：连语义校验都没走到。它不许被算成某个**语义**原因码（那会把「模型没填」写成
    # 「模型填错了内容」），也不许因为排在一条成立的申请后面就被丢掉。
    thin = {"statement": "缺字段的原始诉求"}
    keep2, dropped2 = PW._follow_up_needs_partition(
        specs=(good_cross, thin), task=cross_task, authority=cross_authority, scan=cross_scan,
        draft_revision="dr-9", policy=policy)
    check(len(keep2) == 1 and len(dropped2) == 1
          and dropped2[0]["code"] == "spec_field_missing"
          and dropped2[0]["attempt"] is None
          and dropped2[0]["reason"].split(":")[0].isidentifier(),
          f"缺字段形态落在 `spec_field_missing` 且原因带异常类型名（实为 {dropped2}）；"
          f"未提供尝试号时 `attempt` 如实为 None，不得默认成 0")

    # 原因码封闭：产物里出现的每个码都必须在封闭词表内；词表外的码必须当场被拒（而不是落盘）。
    _codes = {dropped[0]["code"], dropped2[0]["code"], dropped_b[0]["code"]}
    check(_codes and _codes <= set(PW.FOLLOW_UP_REJECTION_CODES),
          f"逐条记录的原因码必须取自封闭词表（实为 {_codes} vs "
          f"{list(PW.FOLLOW_UP_REJECTION_CODES)}）")
    check(len(set(PW.FOLLOW_UP_REJECTION_CODES)) == len(PW.FOLLOW_UP_REJECTION_CODES)
          and all(c and c.islower() for c in PW.FOLLOW_UP_REJECTION_CODES),
          "词表不得有重复项，且成员是规范的小写下划线码")
    _pw_src2 = Path(PW.__file__).read_text(encoding="utf-8")
    for code in PW.FOLLOW_UP_REJECTION_CODES:
        if code == "spec_field_missing":
            continue  # 它不是 `_reject(...)` 的出口，而是「非 FollowUpNeedRejected 形态」的落点
        check(f'"{code}"' in _pw_src2,
              f"词表里的 {code!r} 必须真的在某条 `_reject(...)` 出口上（词表不得预留死码）")
    try:
        PW.FollowUpNeedRejected("x", code="made_up_code", spec_index=0)
    except PW.PackWriterError:
        check(True, "")
    else:
        check(False, "词表外的原因码必须当场被拒（不得随手造一个原因码落盘）")

    # 记录的形状是读者的契约：键集**恰好**是这八个。多一个键（比如偷偷塞进 gap 语义的
    # `detail`）或漏掉序号，都会让「哪条错、错在哪、错在第几条」三者里少一个。
    check(set(dropped[0]) == {"attempt", "spec_index", "statement", "aspect_id", "topic_id",
                              "question_id", "code", "reason"},
          f"逐条拒绝记录的键集必须恰好固定（实为 {sorted(dropped[0])}）")
    check("gap" not in dropped[0] and "detail" not in dropped[0],
          "拒绝记录不得借用 gap 的字段名（它不是 gap：Contract 必需事实是否取得是另一回事）")

    # 成功节的这一半必须**落进产物与 sidecar**：留在内存里等于没留。
    _rej_field = {f.name: f for f in dataclasses.fields(PW.PackWriteOutcome)}
    check("follow_up_rejections" in _rej_field
          and _rej_field["follow_up_rejections"].default == ()
          and isinstance(_rej_field["follow_up_rejections"].default, tuple),
          "`PackWriteOutcome` 必须带 `follow_up_rejections`，缺省是**空元组**"
          "（缺省 None 会被读成「没读过」，与「确实一条都没有」混成一件事）")
    check('"follow_up_rejections": [dict(r) for r in self.follow_up_rejections]' in _pw_src2,
          "sidecar 必须原样带出这半（逐条 dict，不是计数）")
    _rejected_fields = set(vars(PW.ProposalSetRejectedError("probe")))
    check("follow_up_untypeable" in _rejected_fields
          and "follow_up_rejections" not in _rejected_fields
          and "follow_up_rejections" in _rej_field
          and "follow_up_untypeable" not in _rej_field,
          "「被拒那一轮的不成立申请」与「被采信那一轮的不成立申请」必须各自只出现在自己的"
          "那个容器上（前者随整束拒绝走、后者随成功节走）；两边都挂同一个名字，读者就再也"
          "分不清本节到底写没写出来")

    # ==================================================================
    # 7. 两个终态互不冒充：拒绝审计与待裁决诉求是不同字段
    # ==================================================================
    rejected = PW.ProposalSetRejectedError("x")
    rejected_fields = set(vars(rejected))
    check({"rejections", "follow_up_needs", "follow_up_untypeable"} <= rejected_fields
          and all(isinstance(getattr(rejected, name), tuple)
                  for name in ("rejections", "follow_up_needs", "follow_up_untypeable")),
          f"整束终止必须同时带出审计与诉求，且三者都是元组（缺省即空集，不是 None），实为 "
          f"{sorted(rejected_fields)}")
    check("rejections" not in set(vars(CW.UnadjudicatedFollowUpNeeds("x", section_id="c"))),
          "无法裁决的终止态不得夹带拒绝审计（两件事不得互相填充）")

    # ==================================================================
    # 8. §二 3 / 3.4：**逐候选** typed 审计（词表 / 双不变式 / 归属 / 落盘形状）
    # ==================================================================

    # 8.1 词表封闭，且「哪些 kind 必须带逐候选审计」本身也是封闭、可判定的。
    vocab = PW.PROPOSAL_SET_REJECTION_CANDIDATE_REASONS
    check(len(vocab) == len(set(vocab)) and vocab and all(vocab),
          f"逐候选原因词表必须非空、无重复、无空串，实为 {vocab}")
    check(set(PW.STRUCTURED_PROPOSAL_SET_REJECTION_KINDS) <= set(PW.PROPOSAL_SET_REJECTION_KINDS),
          "结构化 kind 表必须是整束 kind 表的子集（否则会出现「要求审计的 kind 本身不合法」）")
    try:
        PW._structured_rejection_kind("kind-that-does-not-exist")
        unknown_raised = False
    except PW.PackWriterError:
        unknown_raised = True
    check(unknown_raised,
          "未知 kind 必须当场拒绝，不得默认成任何一侧（默认 False 会让新 kind 悄悄免除审计，"
          "默认 True 会逼 `schema_invalid` 那一束编出逐候选原因）")
    check(all(PW._structured_rejection_kind(k)
              for k in PW.STRUCTURED_PROPOSAL_SET_REJECTION_KINDS)
          and not any(PW._structured_rejection_kind(k) for k in
                      ("schema_invalid", "proposal_identity_unresolvable")),
          "「整束已成结构化对象」的判定必须与 `source is None` 那两条路一一对应")

    # 8.2 逐候选审计自身的双向不变式：没原因 / 原因自相矛盾 / 明细悬空，三者都必须当场拒。
    def _audit_raises(**kw) -> bool:
        try:
            PW.RejectedCandidateAudit(**kw)
            return False
        except PW.PackWriterError:
            return True

    check(_audit_raises(candidate_id="c1", reasons=()),
          "「什么原因都没有」必须被拒——它在磁盘上与「被采信」不可区分")
    check(_audit_raises(candidate_id="", reasons=("not_individually_implicated",)),
          "候选身份为空必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("made_up_reason",)),
          "词表外的原因必须被拒（否则词表就只是一句注释）")
    check(_audit_raises(candidate_id="c1",
                        reasons=("gate_blocking", "gate_blocking"),
                        rule_ids=("r",)),
          "重复原因必须被拒")
    check(_audit_raises(candidate_id="c1",
                        reasons=("not_individually_implicated", "gate_blocking"),
                        rule_ids=("r",)),
          "「未被单独点名」与「有具体原因」必须互斥：同时出现会让「这条到底踩没踩线」无从判断")
    check(_audit_raises(candidate_id="c1", reasons=("gate_blocking",)),
          "有 `gate_blocking` 却没有 rule_ids = 无法复核，必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("not_individually_implicated",),
                        rule_ids=("r",)),
          "没有 `gate_blocking` 却带 rule_ids = 悬空明细，必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("path_b_high_risk_surface",)),
          "有 `path_b_high_risk_surface` 却没有 surfaces 必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("not_individually_implicated",),
                        surfaces=("12.5",)),
          "没有 `path_b_high_risk_surface` 却带 surfaces 必须被拒")
    ok_audit = PW.RejectedCandidateAudit(
        candidate_id="c1", reasons=("path_b_high_risk_surface", "gate_blocking"),
        rule_ids=("support_fact_unknown",), surfaces=("12.5",))
    check(ok_audit.to_dict() == {"candidate_id": "c1",
                                 "reasons": ["path_b_high_risk_surface", "gate_blocking"],
                                 "rule_ids": ["support_fact_unknown"], "surfaces": ["12.5"],
                                 "ineligible_member_refs": [],
                                 "history_only_member_refs": [],
                                 "unproven_current_state_member_refs": [],
                                 "unproven_current_state_cause_code": ""},
          f"正例必须六个明细字段齐全且可 JSON 化，实为 {ok_audit.to_dict()}")
    # §二 2.3：支撑资格（`selappl-1`）是第三条**独立**的逐候选原因，明细是**材料成员身份**
    # （不是逐字表面片段）：读的人要能拿同一份 manifest 复算「这条边到底绑了哪张表单行」。
    check(_audit_raises(candidate_id="c1", reasons=("path_b_ineligible_material_scope",)),
          "有 `path_b_ineligible_material_scope` 却没有明细必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("not_individually_implicated",),
                        ineligible_member_refs=("topicpack_x::mat_1",)),
          "没有 `path_b_ineligible_material_scope` 却带明细 = 悬空明细，必须被拒")
    ok_scope_audit = PW.RejectedCandidateAudit(
        candidate_id="c1", reasons=("path_b_ineligible_material_scope",),
        ineligible_member_refs=("topicpack_x::mat_1",))
    check(ok_scope_audit.to_dict()["ineligible_member_refs"] == ["topicpack_x::mat_1"],
          "支撑资格原因的明细必须原样落进产物（材料成员身份，不是一句理由）")
    # O-12（`srsc-1`）：期间/来源角色是**第四条**独立的逐候选原因，明细同样是**材料成员身份**
    # ——而且这里比上面那条更需要身份：读的人要拿同一份 manifest + 本节**有序来源集**复算
    # 「这几条边绑的是哪一份文档、那份文档在本节是不是当前状态锚」。它与高风险面、支撑资格
    # 是正交的轴：材料留着、身份不动，被纠正的只是「这条边最多能证明到哪个期间」。
    check(_audit_raises(candidate_id="c1", reasons=("path_b_history_only_current_state",)),
          "有 `path_b_history_only_current_state` 却没有明细必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("not_individually_implicated",),
                        history_only_member_refs=("topicpack_y::mat_9",)),
          "没有 `path_b_history_only_current_state` 却带明细 = 悬空明细，必须被拒")
    check(_audit_raises(candidate_id="c1",
                        reasons=("path_b_ineligible_material_scope",
                                 "path_b_history_only_current_state"),
                        ineligible_member_refs=("topicpack_x::mat_1",)),
          "两条原因各自都要自己的明细：只填一条、另一条空着必须被拒"
          "（空着就等于读的人复算不出这条边绑在哪）")
    ok_period_audit = PW.RejectedCandidateAudit(
        candidate_id="c1", reasons=("path_b_history_only_current_state",),
        history_only_member_refs=("topicpack_y::mat_9",))
    check(ok_period_audit.to_dict()["history_only_member_refs"] == ["topicpack_y::mat_9"],
          "期间/来源角色原因的明细必须原样落进产物（材料成员身份，不是一句理由）")
    # O-12（`srsc-2`）：**独立支撑结论**是第五条独立的逐候选原因，明细是那几条**当前锚边**绑的
    # 材料成员。它与上面那条**互斥**：上一条记的是「一条锚边都没有」时那几条历史边，这一条记的
    # 是「有锚边但没证出来」时那几条锚边——同一条候选不可能两边都是。读的人同样要能拿同一份
    # manifest + 来源集 + **已解析正文**复算「这几条边的正文里到底有没有这段话」。
    check(_audit_raises(candidate_id="c1", reasons=("path_b_unproven_current_state",)),
          "有 `path_b_unproven_current_state` 却没有明细必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("not_individually_implicated",),
                        unproven_current_state_member_refs=("topicpack_z::mat_7",)),
          "没有 `path_b_unproven_current_state` 却带明细 = 悬空明细，必须被拒")
    ok_unproven_audit = PW.RejectedCandidateAudit(
        candidate_id="c1", reasons=("path_b_unproven_current_state",),
        unproven_current_state_member_refs=("topicpack_z::mat_7",),
        unproven_current_state_cause_code="not_extractive_in_any_current_source")
    check(ok_unproven_audit.to_dict()["unproven_current_state_member_refs"]
          == ["topicpack_z::mat_7"],
          "独立支撑结论原因的明细必须原样落进产物（材料成员身份，不是一句理由）")
    # `srsc-3`：同一原因下还有**分级**（材料不足 vs 措辞口径变了）。它必须与原因码同进同出——
    # 只记成员身份的话，读的人无法判断这条该「改绑/撤下」还是「把限定语一起收回来」。
    check(_audit_raises(candidate_id="c1", reasons=("path_b_unproven_current_state",),
                        unproven_current_state_member_refs=("topicpack_z::mat_7",)),
          "有 `path_b_unproven_current_state` 却没有原因码必须被拒"
          "（两种来由的出路不同，缺了它就只能靠猜）")
    check(_audit_raises(candidate_id="c1", reasons=("not_individually_implicated",),
                        unproven_current_state_cause_code="source_period_scope_dropped"),
          "没有 `path_b_unproven_current_state` 却带原因码 = 悬空分级，必须被拒")
    check(_audit_raises(candidate_id="c1", reasons=("path_b_unproven_current_state",),
                        unproven_current_state_member_refs=("topicpack_z::mat_7",),
                        unproven_current_state_cause_code="extractive_contiguous_containment"),
          "原因码必须是**这条排除原因专属**的闭集：把「包含成立」写进一条「证不出来」的审计里"
          "必须当场被拒（否则词表就只是一句注释）")
    check(PW.UNPROVEN_CURRENT_STATE_CAUSES
          == ("not_extractive_in_any_current_source", "source_period_scope_dropped"),
          f"分级闭集恰好两个码，实为 {PW.UNPROVEN_CURRENT_STATE_CAUSES}")
    check(set(PW.UNPROVEN_CURRENT_STATE_CAUSES) <= set(SRS.CURRENT_STATE_SUPPORT_REASONS),
          "分级码必须是 `sections/source_role_scope.py` 判据原因码的**子集**："
          "本模块不得自造一个判据永远不会输出的码")
    ok_period_cause = PW.RejectedCandidateAudit(
        candidate_id="c1", reasons=("path_b_unproven_current_state",),
        unproven_current_state_member_refs=("topicpack_z::mat_7",),
        unproven_current_state_cause_code="source_period_scope_dropped")
    check(ok_period_cause.to_dict()["unproven_current_state_cause_code"]
          == "source_period_scope_dropped",
          "分级码必须原样落进产物（读的人据此知道这条还能靠「收回限定语」救回来）")

    # 8.3 记录级不变式：逐候选键集必须**恰好**等于该束的完整有序候选身份；非结构化 kind 必须为空。
    def _record_raises(**kw) -> bool:
        base = dict(attempt=1, rejection_kind="narrative_gate_blocking",
                    rejection_detail="x", candidate_ids=("c1", "c2"))
        base.update(kw)
        try:
            PW.ProposalSetRejectionRecord(**base)
            return False
        except PW.PackWriterError:
            return True

    full_audit = tuple(PW.RejectedCandidateAudit(
        candidate_id=c, reasons=("not_individually_implicated",)) for c in ("c1", "c2"))
    check(_record_raises(candidate_audit=full_audit[:1]),
          "逐候选审计**少一条**（裁剪成子集）必须被拒——这正是「删掉几个再冒充原集合」的形态")
    check(_record_raises(candidate_audit=tuple(reversed(full_audit))),
          "逐候选审计**重排**必须被拒（顺序就是模型当时的输出顺序）")
    check(_record_raises(candidate_audit=full_audit + (PW.RejectedCandidateAudit(
        candidate_id="c3", reasons=("not_individually_implicated",)),)),
          "逐候选审计**多一条**必须被拒")
    check(not _record_raises(candidate_audit=full_audit),
          "键集与整束身份逐一对应时必须通过（不变式不是恒假）")
    check(_record_raises(rejection_kind="schema_invalid", candidate_audit=full_audit),
          "整束从未成为结构化对象时**不得**凭空给出逐候选归属")
    check(not _record_raises(rejection_kind="schema_invalid", candidate_ids=("k1",)),
          "`schema_invalid` 的空审计是合法表达（身份由模型自报标签 + kind 自己说明）")
    check(_record_raises(named_subsections=("co-h3",)),
          "栏目只在 `subsection_uncovered` 上有意义，别的 kind 带它必须被拒")
    check(_record_raises(rejection_kind="subsection_uncovered", candidate_audit=full_audit),
          "`subsection_uncovered` 必须**结构化**给出被点名的栏目（不能只写在散文 detail 里）")
    uncovered_rec = PW.ProposalSetRejectionRecord(
        attempt=2, rejection_kind="subsection_uncovered", rejection_detail="缺栏目",
        candidate_ids=("c1", "c2"), candidate_audit=full_audit,
        named_subsections=("co-h3",))
    check(uncovered_rec.to_dict()["named_subsections"] == ["co-h3"]
          and uncovered_rec.to_dict()["whole_set_rejected"] is True
          and [a["reasons"] for a in uncovered_rec.to_dict()["candidate_audit"]]
          == [["not_individually_implicated"], ["not_individually_implicated"]],
          f"落盘形态必须显式声明「整束被拒」，实为 {uncovered_rec.to_dict()}")

    # 8.4 归属是**查表**，不是文本匹配：候选 id / proposal id 归属，草稿单元与事实坐标不归属。
    bundle = _NS(
        candidates=tuple(_NS(candidate_id=c) for c in ("c1", "c2", "c3")),
        units=(_NS(draft_unit_id="u1"),),
        proposals=(_NS(proposed_support_id="p1", binding_subject_kind="claim_candidate",
                       binding_subject_id="c1"),
                   _NS(proposed_support_id="p2", binding_subject_kind="claim_candidate",
                       binding_subject_id="c2")),
        follow_up_specs=())
    gate_issues = (
        NS.NarrativeGateIssue(rule_id="rule-on-c2", severity="blocking", location="c2",
                              detail="点名候选本身"),
        NS.NarrativeGateIssue(rule_id="rule-on-p1", severity="blocking", location="p1",
                              detail="点名 c1 的支撑边"),
        NS.NarrativeGateIssue(rule_id="rule-on-unit", severity="blocking", location="u1",
                              detail="这是草稿单元的问题，不是任何候选的断言"),
        NS.NarrativeGateIssue(rule_id="rule-on-fact", severity="blocking",
                              location="container-a:fact-1",
                              detail="这是事实坐标的问题"),
    )
    audit = PW._candidate_audit(bundle=bundle, high_risk={"c3": ("12.5",)},
                               gate_issues=gate_issues)
    check(tuple(a.candidate_id for a in audit) == ("c1", "c2", "c3"),
          f"逐候选审计的顺序必须等于该束的候选顺序，实为 {[a.candidate_id for a in audit]}")
    by_id = {a.candidate_id: a for a in audit}
    check(by_id["c1"].reasons == ("gate_blocking",)
          and by_id["c1"].rule_ids == ("rule-on-p1",)
          and by_id["c1"].surfaces == (),
          f"proposal 上的门问题必须归属到它绑定的那条候选，实为 {by_id['c1'].to_dict()}")
    check(by_id["c2"].reasons == ("gate_blocking",)
          and by_id["c2"].rule_ids == ("rule-on-c2",),
          f"候选 id 上的门问题必须归属到它自己，实为 {by_id['c2'].to_dict()}")
    check(by_id["c3"].reasons == ("path_b_high_risk_surface",)
          and by_id["c3"].surfaces == ("12.5",) and by_id["c3"].rule_ids == (),
          f"路径 B 高风险面必须逐字落在它自己那条候选上，实为 {by_id['c3'].to_dict()}")
    check(not any("rule-on-unit" in a.rule_ids or "rule-on-fact" in a.rule_ids
                  for a in audit),
          "归属不上的门问题**不得**挂到任何候选头上（那正是发明归属）")
    unattributed = PW._gate_issue_rules_by_candidate(bundle=bundle, issues=gate_issues)
    check(sorted(unattributed) == ["c1", "c2"],
          f"归属函数只返回真的被点名的候选，实为 {sorted(unattributed)}")
    check([a.reasons for a in PW._candidate_audit(bundle=bundle)] ==
          [("not_individually_implicated",)] * 3,
          "没有任何点名时每条候选都要有**一条**原因：没原因 = 与「被采信」不可区分")
    check(all(a.rule_ids == () and a.surfaces == ()
              for a in PW._candidate_audit(bundle=bundle)),
          "未被点名时两个明细字段都必须为空（不得塞占位值）")

    # 8.5 产物侧：词表随盘落盘，逐候选审计随记录落盘，且读法写死在 note 里。
    psr_state = _state(sections=("company",),
                       rejections={"company": [uncovered_rec.to_dict()]})
    psr = ACC._proposal_set_rejections_payload(psr_state)
    check(psr["candidate_reason_vocabulary"] == list(vocab)
          and psr["structured_kinds"] == list(PW.STRUCTURED_PROPOSAL_SET_REJECTION_KINDS),
          "逐候选原因词表与结构化 kind 表必须随产物落盘（读者不该为了判读去读源码）")
    check(psr["total_rejected_bundles"] == ACC._psr_total(psr_state) == 1
          and ACC._candidate_audit_total(psr_state) == len(uncovered_rec.candidate_ids) == 2,
          f"束计数与逐候选条目计数必须来自同一个现场，且逐候选条目数 = 逐束候选身份数之和"
          f"（两处各写一份 sum 必然漂移），实为 {psr['total_rejected_bundles']} / "
          f"{ACC._candidate_audit_total(psr_state)}")
    check("不得" in psr["note"] and "读成" in psr["note"]
          and "not_individually_implicated" in psr["note"],
          f"产物的读法必须写死在 note 里：逐候选审计是「整束为什么被拒」的解释，不是幸存者名单，"
          f"实为 {psr['note']}")
    check(json.loads(json.dumps(psr, ensure_ascii=False)) == psr,
          "含逐候选审计的产物必须可 JSON 往返")
    honesty_psr = ACC._psr_honesty(psr_state)
    check(f"{ACC._candidate_audit_total(psr_state)} 条" in honesty_psr
          and "留用" in honesty_psr and "没有通过侧" in honesty_psr,
          f"报告的诚实性说明也必须讲清逐候选审计的读法，实为 {honesty_psr}")
    check("逐候选" not in ACC._psr_honesty(_state()),
          "空态不必提逐候选审计（没有束时讲它是多余的）")

    # ==================================================================
    # 8.6 C3：定向重提案的产物侧读数——**排定**与**真的产出下一修订**是两个数，
    #     且 `reproposal=null` 只能读作「本轮没有形成可核验的重提案去向」，
    #     「没排定」/「排定而未成形」由 `answered_by_attempt` 分辨。
    # ==================================================================
    dest = PW.CandidateReproposalDestination(candidate_id="c1", claim_text="甲",
                                            fact_type="authority_fact",
                                            destination="not_reexpressed")
    trace = PW.RejectionReproposalTrace(
        from_attempt=1, to_attempt=2, note_version=PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION,
        original_candidate_ids=("c1",), next_candidate_ids=("n1",), destinations=(dest,))

    def _risk_rec(**kw) -> dict:
        """造一条**真的**触发类拒绝记录并取其 `to_dict()`——不手写字典。

        手写字典会让「产物键名」与「记录字段名」各改各的而测试仍绿；经 `to_dict()` 取，
        两个名字不一致时这里就红。
        """
        return PW.ProposalSetRejectionRecord(
            attempt=kw.pop("attempt", 1), rejection_kind="path_b_high_risk_surface",
            rejection_detail="路径 B 候选携带高风险表面",
            candidate_ids=("c1",),
            candidate_audit=(PW.RejectedCandidateAudit(
                candidate_id="c1", reasons=("path_b_high_risk_surface",), surfaces=("12.5",)),),
            **kw).to_dict()

    # 排定且成形：两个数都是 1，且去向明细只存在于产物里（报告侧只放读数）。
    traced_state = _state(sections=("company",), rejections={
        "company": [_risk_rec(answered_by_attempt=2, reproposal=trace)]})
    traced_counts = ACC._directed_reproposal_total(traced_state)
    check(traced_counts == {"scheduled": 1, "traced": 1},
          f"排定且成形的束必须同时计入两个数，实为 {traced_counts}")
    traced_payload = ACC._proposal_set_rejections_payload(traced_state)
    check(traced_payload["candidate_reproposal_destinations"]
          == list(PW.CANDIDATE_REPROPOSAL_DESTINATIONS)
          and traced_payload["reproposal_max_passes"] == PW.MAX_DIRECTED_REPROPOSAL_PASSES
          and traced_payload["reproposal_trigger_kinds"] == list(PW.REPROPOSAL_TRIGGER_KINDS)
          and traced_payload["reproposal_note_version"] == PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION,
          "去向词表 / 上限 / 触发 kind / 说明版本必须随产物落盘，读者不该为判读去读源码")
    check(traced_payload["sections"]["company"][0]["reproposal"]["from_attempt"] == 1,
          "跨修订去向必须逐条随被拒记录落盘（报告只给读数，明细不复制）")

    # 排定但那一轮没成形：`traced` 必须是 0——这是**诚实的 None**，不是「没排定」。
    pending_state = _state(sections=("company",),
                           rejections={"company": [_risk_rec(answered_by_attempt=2)]})
    check(ACC._directed_reproposal_total(pending_state) == {"scheduled": 1, "traced": 0},
          "已排定但未产出结构化提案集时，trace 必须缺席（不得用空 trace 冒充「逐条都追过了」），"
          f"实为 {ACC._directed_reproposal_total(pending_state)}")

    # 根本没有排定：两个数都是 0，且诚实性说明**不得**提定向重提案（没有这事就别讲）。
    plain_state = _state(sections=("company",), rejections={"company": [_risk_rec()]})
    check(ACC._directed_reproposal_total(plain_state) == {"scheduled": 0, "traced": 0},
          "未排定时两个数都必须是 0，实为 "
          f"{ACC._directed_reproposal_total(plain_state)}")
    check("定向重提案" not in ACC._psr_honesty(plain_state),
          "没有定向重提案时不得在诚实性说明里提它（提了就是把没发生的事说成发生了）")

    # 诚实性说明：有排定时必须讲清两个读数，并把 `reproposal=null` 的读法**限定在产物支得住
    # 的那一句**上——它只说「本轮没有形成可核验的重提案去向」，「没排定」与「排定而未成形」
    # 由 `answered_by_attempt` 分辨。把这两种现场压成「没有可写的事实」是一个产物支不住的结论。
    honesty_directed = ACC._psr_honesty(pending_state)
    check("定向重提案" in honesty_directed and "不新增调用额度" in honesty_directed
          and "没有形成可核验的重提案去向" in honesty_directed
          and "answered_by_attempt" in honesty_directed,
          f"有排定时必须讲清两个读数与 null 的读法，实为 {honesty_directed}")
    check("一律读作" not in honesty_directed,
          f"不得再把 `reproposal=null` 写成「一律读作」某一句结论——`null` 合并了两种现场，"
          f"任何单一结论都会在另一种现场上说反话（实为 {honesty_directed}）")

    # 产物自相矛盾：带着跨修订去向却没有排定轮次。两层都要拦——
    # 上层（记录构造）拦得住新写入，下层（产物读数）拦得住磁盘上的旧/外来产物。
    record_raised = False
    try:
        PW.ProposalSetRejectionRecord(
            attempt=1, rejection_kind="path_b_high_risk_surface", rejection_detail="x",
            candidate_ids=("c1",),
            candidate_audit=(PW.RejectedCandidateAudit(
                candidate_id="c1", reasons=("path_b_high_risk_surface",), surfaces=("12.5",)),),
            reproposal=trace)
    except PW.PackWriterError:
        record_raised = True
    check(record_raised,
          "有跨修订去向却没有排定轮次时，记录构造本身就该拒（不能只在读产物时才被发现）")
    contradiction_row = _risk_rec()          # 没有排定
    contradiction_row["reproposal"] = trace.to_dict()   # 人为塞进去向（模拟外来/陈旧产物）
    counts_raised = False
    try:
        ACC._directed_reproposal_total(
            _state(sections=("company",), rejections={"company": [contradiction_row]}))
    except AssertionError:
        counts_raised = True
    check(counts_raised,
          "traced 必须蕴含 scheduled：产物自相矛盾时计数函数必须抛，而不是照数")

    # JSON 往返：两个新字段都是可序列化的（`reproposal` 是嵌套 dict，不是 dataclass 实例）。
    check(json.loads(json.dumps(traced_payload, ensure_ascii=False)) == traced_payload,
          "含定向重提案读数的产物必须可 JSON 往返")
    check(isinstance(traced_payload["sections"]["company"][0]["reproposal"], dict),
          "落盘的 `reproposal` 必须是 dict（dataclass 实例落盘会在 json.dumps 处炸）")

    # ==================================================================
    # 8. 组合面：**真实**闭合失败 → 相位入口 → run 级台账
    # ==================================================================
    # 上面各节是分层的：§5 用**替身**相位证明驱动侧会把异常里的诉求收进现场，§2/§6 用写侧夹具
    # 证明被拒整束带出 typed 诉求，§7 用**替身**异常证明台账载荷的形状。三处各自绿过，仍不等于
    # 「一次真实的草稿闭合失败会以**它自己**的身份走到 run 级台账」：中间还隔着两道缝——
    # (a) 相位入口会不会把它重新包成 `BackboneWriterPhaseError`（包了，`follow_up_needs` 就
    # 不在调用方拿到的那个对象上了）；(b) 驱动的取值口径（`follow_up_needs` /
    # `follow_up_untypeable` 两个字段名）与真实异常对象是否真的对齐。本节把这两道缝合成一条：
    # 夹具仍是合成的，但**断点往后的每一个环节都是生产代码**，且异常对象是真实产生的那一个
    # （不是手工构造的替身）。
    #
    # 跨模块取夹具是**单向**的：本模块不反向被那个模块引用。它只提供「一份能触发真实闭合失败的
    # 两材料/两草稿现场」，判据本身仍归 `test_m930_3_prose_occurrence` §2 所有（那里逐条断言
    # typed 原因文本），这里不复述、不改判据。
    from evals import test_m930_3_prose_occurrence as PO
    from evals import test_demo_pack_writer as T

    task0, authority, specs = PO._occurrence_case()
    # 相位入口按 **section 级** research_policy 分支；夹具本体走 `PW.write_section` 直呼、
    # 不经相位，因此这里只把政策字面量换成该分支的登记值（`topic_research` 是派生章节政策，
    # 本相位按设计不服务它——那是另一条判据，不在本节要测的范围内）。
    real_task = dataclasses.replace(task0, research_policy="harness")
    pack_id = specs["pack_id"]
    # 错来源：共享原子 `C_PROC` 只有落在材料 A 上的一条边，草稿却在 B 那段里又声明了它。
    real_plan = T._plan(
        candidates=[
            T._cand("c-agree", PO.C_AGREE, T._material_edge(pack_id, "m-occ-a")),
            T._cand("c-proc", PO.C_PROC, T._material_edge(pack_id, "m-occ-a")),
            T._cand("c-prod", PO.C_PROD, T._material_edge(pack_id, "m-occ-b")),
        ],
        prose=[T._prose("p-a", PO.DRAFT_A, members=[specs["ref_a"]],
                        atoms=["c-agree", "c-proc"]),
               T._prose("p-b", PO.DRAFT_B, members=[specs["ref_b"]],
                        atoms=["c-proc", "c-prod"])],
        units=[T._unit("u-system", PO.UNIT_TEXT)])
    real_plan["follow_up_needs"] = [dict(T._follow_up(
        "需要补充采购模式的更多材料。",
        target_requirement_id=PW.scan_authority(authority, real_task
                                                ).requirement_ids[T.TOPIC_BUSINESS],
        topic_id=T.TOPIC_BUSINESS, question_id=T._question_id(T.TOPIC_BUSINESS),
        aspect_id=PO.ASP_PROCUREMENT))]

    section_input = CW.BackboneWriterSectionInput(
        task=real_task, authority=authority, projection=PO._PROJECTIONS["company"],
        writing_spec=PO._SPEC, presentation_profile=PO._PROFILE,
        dependency_fingerprint=T.DEPENDENCY_FINGERPRINT, requirements=())
    real_exc: PW.ProposalSetRejectedError | None = None
    try:
        CW.run_backbone_writer_phase(
            (section_input,), llm_client=T._StubLlm(real_plan),
            # 语义门与最终句门在**本路径上根本走不到**（门前闭合核对先拒），但相位的注入校验
            # 先于一切：缺注入会被相位按「接线没接好」提前拒掉，那个结论会盖掉本节要测的
            # 「真实闭合失败」。故按真实组合根的形状注入，而不是传 None。
            entailment_llm_client=object(), final_sentence_llm_client=object(),
            material_resolver=T._StubPayloadResolver())
    except PW.ProposalSetRejectedError as exc:
        real_exc = exc
    except Exception as exc:  # noqa: BLE001
        check(False,
              f"真实闭合失败必须以**写侧**的 typed 整束拒绝抛出，实为 "
              f"{type(exc).__name__}: {str(exc)[:200]}")
    check(real_exc is not None, "真实闭合失败必须抛出 typed 整束拒绝（不得被采信）")
    if real_exc is None:
        return {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    check(not isinstance(real_exc, CW.BackboneWriterPhaseError),
          "相位入口**不得**把它重新包成自己的装配错误：一包，诉求就不在调用方拿到的对象上了")
    check(tuple(str(r.rejection_kind) for r in real_exc.rejections)
          == ("natural_draft_not_closed",),
          f"真实失败的原因必须是草稿闭合（实为 "
          f"{[r.rejection_kind for r in real_exc.rejections]}）")
    real_needs = tuple(real_exc.follow_up_needs)
    check(len(real_needs) == 1 and isinstance(real_needs[0], TS.FollowUpNeed),
          f"模型提出的诉求必须随真实异常带出、且是真类（实为 {real_needs}）")
    check(tuple(real_exc.follow_up_untypeable) == (),
          "本夹具的诉求合法，因此不得出现「不成立」那一类")
    check(real_exc.rejections[-1].follow_up_count == 1,
          "记录里的诉求条数必须与带出的诉求集一致（对账两个不同来源的同一个数）")
    check(json.loads(json.dumps(PW._jsonable(real_needs[0]), ensure_ascii=False))
          == PW._jsonable(real_needs[0]),
          "这条诉求必须可 JSON 往返（台账落盘形态即读回形态）")

    # 8.2 同一个真实异常对象交给**真实的**驱动侧：它必须逐字进现场容器。
    real_section_input = ACC._section_input
    real_run_phase = CW.run_formal_m930_writer_phase
    try:
        ACC._section_input = lambda inputs, section_id: _NS(section_id=section_id)

        def _replay_real(section_inputs, **kwargs):
            """只有 company 节抛**那个真实对象**；其余两节是普通相位错误（两个方向一起验）。"""
            section_id = str(section_inputs[0].section_id)
            if section_id == "company":
                raise real_exc
            raise CW.BackboneWriterPhaseError(f"普通装配错误（{section_id}）")

        CW.run_formal_m930_writer_phase = _replay_real
        sections: dict = {}
        errors: dict = {}
        commits: dict = {}
        pending_out: dict = {}
        rejections_out: dict = {}
        ACC._drive_into(_drive_inputs(), sections=sections, errors=errors, commits=commits,
                        policy=_NS(), generated_at="2026-09-23T00:00:00Z",
                        llm_client=None, entailment_llm_client=None,
                        final_sentence_llm_client=None, section_store=None,
                        rejections=rejections_out, pending_follow_up=pending_out)
    finally:
        ACC._section_input = real_section_input
        CW.run_formal_m930_writer_phase = real_run_phase
    # 两件事的「空」不同形：待裁决诉求**没有就不写这一节**（不得发明空条目），而被拒束审计
    # 每节都有一行、没被拒就如实是空列表（「本次没被拒」与「这一节没跑到」不是一回事）。
    check(sorted(pending_out) == ["company"]
          and sorted(k for k, v in rejections_out.items() if v) == ["company"]
          and sorted(rejections_out) == sorted(ACC.SECTION_ORDER),
          f"只有真实出错的那一节带诉求；被拒审计则逐节在册、非空的那一节恰是 company，"
          f"实为 {sorted(pending_out)}/{ {k: len(v) for k, v in rejections_out.items()} }")
    captured_needs = pending_out["company"]["follow_up_needs"]
    check(captured_needs == [PW._jsonable(real_needs[0])]
          and pending_out["company"]["follow_up_untypeable"] == [],
          f"现场必须留下**诉求本身**（不是条数、不是消息文本），实为 {pending_out['company']}")
    # 逐字段落地：现场那一份必须**带着模型当时写的诉求内容与归属**（只比一个恒等表达式等于
    # 自证——这里换成从现场那一份里读出内容，与夹具声明的字面量对账）。
    check(len(captured_needs) == 1
          and captured_needs[0].get("statement") == real_plan["follow_up_needs"][0]["statement"]
          and captured_needs[0].get("aspect_id") == PO.ASP_PROCUREMENT
          and str(captured_needs[0].get("section_id")) == "company",
          f"现场那一份必须逐字段带着诉求内容与归属，实为 {captured_needs}")
    check(str(pending_out["company"]["phase_error"]).startswith(
        "ProposalSetRejectedError: "),
          f"typed 终止原因必须点名真实的异常类别，实为 "
          f"{str(pending_out['company']['phase_error'])[:80]!r}")
    check([r["rejection_kind"] for r in rejections_out["company"]]
          == ["natural_draft_not_closed"],
          "被拒整束的审计与诉求**都在同一份现场**里可回查（留存的两件事不得互相顶替）")
    check("company" not in sections and "company" in errors,
          "真实失败的那一节不得出现在 sections 里（失败就是失败，不留半截产物）")

    # 8.3 现场 → run 级台账：一节失败 ≠ 整本无账；且台账**不**伪造正式版本、不冒充缺口。
    ledger_state = _state(pending=pending_out, rejections=rejections_out,
                          sections=("financial", "industry"), errors=errors)
    ledger = ACC._follow_up_needs_payload(ledger_state)
    check(ledger["schema_version"] == ACC.FOLLOW_UP_NEEDS_SCHEMA_VERSION
          and ledger["status"] == "pending_adjudication",
          "台账形状版本与状态必须是登记的那一对（待裁决 = 未执行）")
    check(ledger["total_pending_needs"] == 1 == ACC._pending_needs_total(ledger_state)
          and ledger["total_untypeable"] == 0,
          f"逐条计数必须与现场逐节点数一致（实为 {ledger['total_pending_needs']}）")
    check(sorted(ledger["sections"]) == ["company"]
          and ledger["adjudicated_runs"] == {}
          and ledger["sections_completed"] == ["financial", "industry"],
          f"逐节留存必须点名 company，且「已执行补件」为空、「跑完了的节」不含 company，"
          f"实为 {sorted(ledger['sections'])}/{ledger['adjudicated_runs']}/"
          f"{ledger['sections_completed']}")
    check("company" in ACC._follow_up_needs_honesty(ledger_state),
          "诚实性说明必须逐节点名（只给总数会让「哪一节提出的」无从判断）")
    ledger_text = json.dumps(ledger, ensure_ascii=False)
    check("report_version" not in ledger_text and "SectionResult" not in ledger_text,
          "台账**不得**伪造正式版本或章节结果身份（它记的是待裁决提议，不是已发布产物）")
    check("未被执行" in ledger["note"] and "不是 gap" in ledger["note"],
          f"台账必须自己说清身份：待裁决、未执行、不是 gap，实为 {ledger['note'][:120]}")
    check(json.loads(json.dumps(ledger, ensure_ascii=False)) == ledger,
          "真实路径产出的台账必须可 JSON 往返（落盘形态即读回形态）")

    # 套件结果契约（`evals/run_evals.py` 的 main 逐模块读 `passed/failed/skipped`）：
    # 本模块只有断言、没有 skip 分支，`skipped` 恒为 0，但字段必须存在——缺了它，
    # 运行器会在自己的报告行上抛 `KeyError: 'skipped'` 并中止**整轮**套件，后面的模块
    # 根本不会跑（本批次实测发生过一次）。空值要如实上报，不能靠运行器兜底。
    result = {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    check(set(result) == {"passed", "failed", "skipped", "details"},
          "本模块的返回必须满足套件结果契约：四键齐备（缺 `skipped` 会中止整轮套件）")
    result["passed"] = passed
    return result


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

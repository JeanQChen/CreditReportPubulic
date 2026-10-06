"""M930 演示工作台：**上传六份材料 → 同一个新 run → 本次正文与审核**。

从仓库根启动：``streamlit run scripts/cited_demo_app.py``。

三屏，外加一个明确标注的历史对照区：

1. **上传材料** — 操作者真的选**六份**材料：三份电子 PDF（公司节）与三份 XLSX（财务节）。
   页面先在**内存里**按哈希核对两组；核对通过后点「开始生成」会做四件事，且**只在这一刻做
   一次**：建立 create-only 的 run-id；把本次上传的 PDF 原始字节落到 `data/run_inputs/<run_id>/`
   （`sections.cited_run_input`，`cri-1`，不可覆盖）；把三份 XLSX 与库里**当前有效**的权威
   快照逐份同字节核对后落进**同一个**目录（`sections.cited_financial_input`，`cfi-1`，两组
   绑定互不覆盖）；拉起**同一条正式链** `scripts/run_m930_3_cited_chain.py --run-input …
   --financial-input … --section company,financial`。上传入口是唯一的，没有第二套运行时，
   也没有「跳过校验继续」。财务那一半没建立起来时，刚建出的运行输入目录整个撤掉——一次点
   不出半份输入。
2. **生成过程** — 只读**本 run 自己**的 `run_progress.jsonl`（`rj-1`）：真实阶段边界、真实
   UTC 时间戳、真实失败。没有可恢复 `checkpoint_id` 就写「不可恢复」；没有日志就是没有日志。
   **不**显示推算出来的百分比、耗时或「正在调用模型」。
3. **报告与审核** — 只加载**本 run** 的产物（`sections.cited_upload_view`，`cuv-1`），逐条读回
   它自己的 `report_version` / 清单 / 草稿 / 逐句核对 / 独立审阅 / 缺口 / 原 PDF 展示集，以及
   财务节的 A2 确定性结构。三块（公司 A1、财务 A1、财务 A2）各自四条状态轴**分开**写。
   左约四分之三为正文与来源，右约四分之一为**分开写**的状态轴。账本记 `completed` 却少了
   请求的节或财务 A2 时，本屏**显著失败**，不跳过缺失的那一块去显示另一块的成功。
4. **历史对照 · 非本次生成** — 上批的 `cdb-1` 并列展示基线原样保留，标题写明它**不是**本次
   运行的结果；旧财务 A2 只在这里出现，也不得被读成本次运行产物。

本页**不**改写任何冻结产物，**不**写 `data/` 下的库（财务节只**核对并复用**库里当前有效的
权威快照，不重抽取工作簿），**不**替操作者做人工确认，也不会拿历史回放顶替本次运行。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sections import cited_budget as CB  # noqa: E402
from sections import cited_demo_binding as binding_mod  # noqa: E402
from sections import cited_demo_loader as demo_loader  # noqa: E402
from sections import cited_financial_input as fin_input_mod  # noqa: E402
from sections import cited_review as CR  # noqa: E402
from sections import cited_run_authorization as run_auth  # noqa: E402
from sections import cited_run_input as run_input_mod  # noqa: E402
from sections import cited_run_journal as run_journal  # noqa: E402
from sections import cited_upload_view as upload_view  # noqa: E402
from sections import cited_writer as CW  # noqa: E402


st.set_page_config(page_title="授信报告生成 · 演示工作台", layout="wide")

_REPO = Path(__file__).resolve().parent.parent

_VIEW_OPTIONS = ("① 上传材料", "② 生成过程", "③ 报告与审核", "历史对照 · 非本次生成")
#: 历史对照区自己的子视图。旧的回放屏在这里原样保留，**不**与本次运行的两屏混在一起。
_HISTORY_VIEWS = ("材料与上传", "生成过程回放", "报告与审核", "审计详情")

#: 本次演示的节集合：**同一次上传创建同一个 run，两节都产出**。公司节的三份 PDF 与财务节的
#: 三份 XLSX 是**两组不同材料**，各走各的绑定（`cri-1` / `cfi-1`），谁都不冒充谁；财务节复用的
#: 是库里**当前有效**的权威快照，本次只核对并复用，不重抽取、不写库。
_NEW_RUN_SECTIONS = ("company", "financial")
#: 新 run 的 id 前缀。它同时是「这一类运行由上传入口创建」的可读标记，页面按它之外的口径
#: **不**筛选——真正的判据是产物里有没有 `run_input_binding.json`。
_NEW_RUN_PREFIX = "m930_3_cited_upload_"
_MODE_OPTIONS = ("offline", "real")
_RUN_INPUT_ROOT = _REPO / "data" / "run_inputs"
_AUTHORIZATION_ROOT = _REPO / "data" / run_auth.AUTHORIZATION_DIRNAME
_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FINANCIAL_DB = _REPO / "data" / "financial_v2.db"
#: 本次真实运行必须声明的 demo scope：v2 profile 相对 v1 **只增** `fin_balance_structure`。
#: 页面把它写进请求面，授权凭据绑的就是这一版范围的指纹。
_DEMO_PROFILE = "templates/demo_scopes/phase_a_business_finance_v2.yaml"

#: 第一屏里公司名称/代码这两个字段在**冻结产物里没有对应字段**。原型把它们画成「已核对」的
#: 表单值；这里照画布局，但值只写得出「来自已保存案例目录」这一层，且必须显式标注，
#: 免得观众把它读成「已进入生成输入」。
_CREDIT_TYPE_OPTIONS = ("流动资金贷款", "贸易融资", "固定资产贷款", "项目贷款", "其他")
_SUBJECT_NOTE = ("本演示的两个冻结产物里**没有**主体全称字段：`300750` 只来自已保存案例目录名，"
                 "企业全称未在产物中登记。页面不把它显示为「已核对」。")
_CREDIT_NOTE = ("授信类型、金额、期限、增信措施在本演示里**只是表单**。两个冻结产物的可核验输入"
                "里没有这些字段，因此它们**没有**用于生成，也不会被写进任何产物。")


def _render_styles() -> None:
    st.markdown("""
<style>
  .report-paragraph {font-size: 1.08rem; line-height: 1.9; margin: 0.65rem 0 1rem;}
  .report-sentence-error {background: #fff1f0; border-bottom: 2px solid #d92d20;}
  .report-citation {color: #667085; font-size: 0.75rem; white-space: nowrap;}
  .audit-kicker {font-size: .8rem; color: #667085; margin-bottom: .25rem;}
</style>""", unsafe_allow_html=True)

_HARD_REASON_ZH = {
    "numeric_basis_not_qualified": "数字缺少相应的合格事实权威",
    "sentence_aspect_not_registered": "引用材料未登记到本句所属栏目",
    "unsourced_subject_surface": "句中主体表述未在所引原文找到对应表面",
}

_SCOPE_ZH = {
    "financial_a2": "财务 A2 资产负债结构",
    "cross_section": "两节合读",
    "material_selectivity": "未用材料的选择性遗漏",
}

#: 覆盖状态说的是**实际发生了什么**：真实调用跑过、离线回声跑过、跑失败、被可审性挡住、
#: 还是输入备好了但根本没跑。五种说法互不替代——尤其「离线回声」不是「真实审阅」。
_COVERAGE_ZH = {
    "reviewed_by_prior_real_call": "既有 A1 真实调用（仅逐句正文）",
    "reviewed_by_real_call": "真实调用已审阅",
    "reviewed_by_offline_echo": "离线回声，**非**真实审阅",
    "review_started_failed": "审阅已开始但**未跑完**（失败批见下表）",
    "review_skipped_reviewability_blocked": "因不可审阅而跳过",
    "input_prepared_review_not_run": "输入已备，审阅未运行",
    "input_not_prepared": "输入未准备",
    "not_trusted_call_provenance": "调用来源不可信",
}

#: 报告级审阅读数的读法。原文照抄自 `report_states.unit_count_meaning`，此处只是把它
#: 摆在数字**旁边**——读那三列时最容易犯的错，正是把其中任何一个读成「逐份材料被核实」。
_UNIT_COUNT_CAPTION = (
    "「放进请求」= 确实进了某次请求的成员（**含失败批**）；「收到可解析回复」= 进的是一个"
    "回复能解析的批次；「模型提出问题」= 模型**真的对它提了意见**。三者互不推导，"
    "**没有一个**表示「逐份材料被核实」——产物里没有表达那件事的字段。"
    "空意见列表只说明**这次调用没有报告会实质改变结论的问题**，不说明内容正确、"
    "不说明系统放行、也不说明人工接受。")

#: 逐条读那 7 条意见时要同时读到的话。三条要点都来自**产物本身**（f51–f56 在财务节的
#: 合格事实里、三条 high 指向同一个 `target_ref`、`suggested_target` 只是「建议补到哪」），
#: 不是对意见的再解释。
_ISSUE_READING_NOTES = (
    "**这七条原始意见一字未改**，读它们时要同时读到三件事：\n\n"
    "1. 它们说的是**正文没有把该数字写进相应分析**，不是**整份报告缺这些事实**——"
    "`f51`–`f56` 等数值已经在财务节的确定性权威指标表里展示，问题在于 A1 正文没有引用它们"
    "做分析。不要把「正文未采用」读成「来源没有」。\n"
    "2. 判为 `high` 的三条（`rvi_c0733c65a6dc9248cdbac7e7`、`rvi_9f3fc9ea76230e4898ca46f6`、"
    "`rvi_02d12bf92fbe5aff8e4327b3`）指向**同一个**栏目 `fin_solvency.net_asset_level` 的"
    "**同一个**段落缺口，是同一件事的三次表达，而非三个独立缺陷；原始意见不合并、不删除，"
    "此处只说明它们之间的关系。\n"
    "3. 单条材料被指「未被采用」**不等于**该 Contract 栏目只差这一个科目就算完成——"
    "栏目是否成立仍由 Contract 完成规则与合格事实判定，`suggested_target` 只是"
    "「建议补到哪一栏」，不是栏目完成证明。财务权威与栏目路由不因这些意见改变。")


def _hard_reason_label(code: str) -> str:
    return f"{_HARD_REASON_ZH.get(code, '机械核对未通过')}（{code}）"


def _citation_index(section: demo_loader.CitedDemoSection) -> dict[str, dict[str, Any]]:
    return {str(entry["citation_key"]): entry
            for entry in section.manifest["materials"] + section.manifest["facts"]}


def _sentence_index(section: demo_loader.CitedDemoSection) -> dict[str, dict[str, Any]]:
    return {str(row["sentence_id"]): row for row in section.checks["sentence_states"]}


def _review_issues_of(section: Any) -> list[dict[str, Any]]:
    """本节留存下来的审阅意见——**只有确实产出了意见**时才是非空的。

    两个结构孪生：本 run 的 `UploadRunSection.review_issues`（**失败形状下是空元组**，见
    `review_ran_without_opinions`）与历史展示绑定那一节的 `review["issues"]`（装载时已由
    `_review_from_wire` 保证有 `issues` 键）。

    **刻意不写 `review.get("issues", [])`**：那会把「这次审阅没有产出意见」与「产物缺了
    `issues` 键」折成同一件事，把一个坏产物静默显示成零意见——正是本批要挡掉的那种降级。
    两种结构都读不到时**抛**，由调用方按块隔离地把错误显示出来。
    """
    issues = getattr(section, "review_issues", None)
    if issues is not None:
        return [dict(issue) for issue in issues]
    review = getattr(section, "review", None)
    if not isinstance(review, dict) or "issues" not in review:
        raise TypeError(
            "这一节的审阅读不出来：既没有 `review_issues`，`review` 里也没有 `issues` 键——"
            "不能把它当成「零条意见」")
    return [dict(issue) for issue in review["issues"]]


def _review_index(section: Any) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for issue in _review_issues_of(section):
        out.setdefault(str(issue["sentence_id"]), []).append(issue)
    return out


def _load_assurance(run: demo_loader.CitedDemoRun
                    ) -> tuple[dict[str, Any] | None, str]:
    """读回**已持久化**的 M930-4 读数；本页不现场重算，也不发任何请求。

    返回 `(读数, 说明)`：读数非空时 `说明` 是它的来源身份（「读取自 … · sha256 …」），
    为空时 `说明` 是为什么读不到。两种情况下页面都不把「读不到」写成通过。
    """
    if run.ledger["mode"] != "real":
        return None, "离线替身运行不适用真实审阅复核"
    try:
        sidecar = demo_loader.load_assurance_sidecar(
            run, results_root=os.environ.get("CITED_DEMO_RESULTS_ROOT")
            or demo_loader.DEFAULT_RESULTS_ROOT)
    except Exception as exc:
        return None, str(exc)
    return sidecar.reading, sidecar.describe()


def _review_attested(assurance: dict[str, Any] | None, section_id: str) -> bool:
    if assurance is None:
        return False
    return any(item["section_id"] == section_id
               and item["prior_review_scope"] == "cited_prose_only"
               and item["reviewer_run_record"] is not None
               for item in assurance["section_assessments"])


def _scope_review_rows(assurance: dict[str, Any]) -> list[dict[str, Any]]:
    """逐 scope 的**审阅读数**：跑没跑、发了几批、成几批、放进多少、回来多少、提了几条。

    「跑没跑」的判据是 `review_opinions.<kind>.run_id is not None`，**不是**覆盖状态
    是否以 `reviewed_by_` 开头：`material_selectivity` 整体判 `failed`，但它的前四批
    真的跑完并提出了 7 条意见——按覆盖状态去门控，就会把已经发生过的意见显示成「无」。
    """
    states = assurance.get("report_states") or {}
    coverage = states.get("review_coverage") or {}
    opinions = states.get("review_opinions") or {}
    rows: list[dict[str, Any]] = []
    for scope in assurance.get("report_review_scopes") or []:
        kind = scope["scope_kind"]
        opinion = opinions.get(kind) or {}
        ran = opinion.get("run_id") is not None
        cover = coverage.get(kind)
        rows.append({
            "内容块": _SCOPE_ZH.get(kind, kind),
            "内容版本": scope["scope_version"],
            "声明单元": opinion.get("declared_unit_count", scope["unit_count"]) if ran
            else scope["unit_count"],
            "审阅覆盖": _COVERAGE_ZH.get(cover, cover or "未记录"),
            "已发起批次": opinion.get("requested_batch_count", 0) if ran else 0,
            "已落批记录": opinion.get("completed_batch_count", 0) if ran else 0,
            "失败批次": opinion.get("failed_batch_count", 0) if ran else 0,
            "放进请求": opinion.get("requested_unit_count", 0) if ran else 0,
            "收到可解析回复": opinion.get("covered_unit_count", 0) if ran else 0,
            "模型提出问题": opinion.get("reported_unit_count", 0) if ran else 0,
            "未覆盖单元": (opinion.get("excluded_unit_count", 0) if ran
                          else scope["unit_count"]),
            "意见": f"{opinion.get('issue_count', 0)} 条" if ran else "无（审阅未运行）",
            "产出者": opinion.get("producer_kind") or "—",
            "可作审阅输入": ("是" if opinion.get("trusted_as_assurance_input") else "否")
            if ran else "—",
        })
    return rows


def _report_review_issue_rows(assurance: dict[str, Any]) -> list[dict[str, Any]]:
    """已绑定的报告级意见摊成展示行。**字段原样搬运**，不做分类、合并或改写。"""
    rows: list[dict[str, Any]] = []
    for run in assurance.get("report_review_runs") or []:
        for issue in run.get("issues") or []:
            unit = issue.get("unit_ref") or {}
            target = issue.get("suggested_target") or {}
            rows.append({
                "内容块": _SCOPE_ZH.get(run.get("scope_kind"), run.get("scope_kind")),
                "意见 id": issue.get("issue_id"),
                "类别": issue.get("category"),
                "严重度": issue.get("severity"),
                "阻断放行": "是" if issue.get("blocking") else "否",
                "指向单元": f"{unit.get('unit_kind')}:{unit.get('unit_id')}",
                "证据出处": "、".join(issue.get("evidence_refs") or []),
                "建议补到": target.get("target_ref") or "—",
                "理由": issue.get("reason"),
            })
    return rows


def _unreviewed_units(assurance: dict[str, Any]
                      ) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """逐 scope 把「未获得可解析审阅」的成员**分成两拨**。

    判据只用产物里已有的两本名单，不读第二个产物：
    `requested_unit_ids` = 确实进过某次请求（含失败批）的成员；
    `excluded_unit_ids` = 没拿到可解析回复的成员。两者**相交**的那部分，就是
    「发出去了、但那一批的回复不可解析」的成员；`excluded` 里剩下的，才是
    「连请求都没进过」的成员。

    这两件事的处置完全不同：前者那一批的内容真的被送到了模型面前（只是回复不可用），
    后者根本没有离开本机。把它们合并成一句「N 个成员从未进入任何请求」，会让读者
    以为全部都没发出去——而其中一半是真的发出去过的。
    """
    out: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for run in assurance.get("report_review_runs") or []:
        excluded = tuple(str(x) for x in (run.get("excluded_unit_ids") or ()))
        if not excluded:
            continue
        requested = {str(x) for x in (run.get("requested_unit_ids") or ())}
        entered = tuple(x for x in excluded if x in requested)
        never = tuple(x for x in excluded if x not in requested)
        out.append((str(run.get("scope_kind")), entered, never))
    return out


def _render_status(run: demo_loader.CitedDemoRun,
                   assurance: dict[str, Any] | None,
                   assurance_note: str) -> None:
    st.error("不可发布 · 未经人工接受 · M930-3 内容门与正式阶段门未关闭")
    st.caption(
        f"已存 run：`{run.run_id}` · 模式：`{run.ledger['mode']}` · "
        f"流程：`{run.ledger['run_outcome']}` · 本页从已落盘文件加载，不发模型或网络请求。"
    )
    st.info("正式门未关闭；本 UI 不作治理裁决。若报告级 Assurance 侧车未加载，"
            "下面仅展示原运行的节级状态，不能把节级审阅视为整份报告通过。")
    if assurance is None:
        st.warning(f"未加载到已持久化的 M930-4 读数：{assurance_note}。"
                   "以下仅为原运行状态，不得视为系统放行。")
    else:
        st.caption(f"本页显示的是**已持久化**的 M930-4 读数：{assurance_note}。"
                   "本页不现场重算，也不发请求。")
        coverage = (assurance.get("report_states") or {}).get("review_coverage") or {}
        rows = []
        for item in assurance["section_assessments"]:
            row = {
                "章节": "主营业务" if item["section_id"] == "company" else "财务分析",
                "限定复核状态": item["assurance_result"]["system_review_state"],
                "可系统放行": "否",
                "机械阻断句": len(item["blocked_sentence_ids"]),
                "A2 审阅（**逐句**轴）": item["a2_review_state"],
            }
            key = f"{item['section_id']}_cited_prose"
            if key in coverage:
                row["审阅状态"] = _COVERAGE_ZH.get(coverage[key], coverage[key])
            rows.append(row)
        st.error("M930-4 只读限定复核：两节均不可系统放行。"
                 "报告级三块内容**跑到了什么程度**见下面两张表——审阅跑过、甚至跑完，"
                 "都只提意见：不放行、不改稿、不代表人工接受。")
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption(
            "**「A2 审阅」列名带「逐句轴」，是为了和下面那张表分开。** 同一份内容在两个轴上"
            "各有一格，名字像、范围不同，**不是自相矛盾**：\n\n"
            "- 本表的 `A2 审阅（逐句轴）` 说的是**逐句独立审阅**这条轴有没有覆盖财务 A2——"
            "**没有**，所以是 `review_not_run`。这一格描述的是句级审阅的边界。\n"
            "- 下面「报告级审阅：逐块读数」里的 `财务 A2 资产负债结构` 说的是**报告级审阅**"
            "这条轴，它**已经跑完**（1 批 / 1 条记录、5 个单元全部收到可解析回复、0 条意见）。"
            "这一格描述的是那三块「读者看得到、逐句审阅从未表态」的内容有没有被另一条链看到。\n\n"
            "两条轴各自成立，谁也不推翻谁。**两者都不等于人工接受，也不等于系统放行**；"
            "报告级那一块的 0 条意见只说明**这次调用没有报告问题**，"
            "**不说明材料被逐条核实、不说明核实通过**。")
        scopes = assurance.get("report_review_scopes") or []
        if scopes:
            st.markdown("#### 报告级审阅：逐块读数")
            st.dataframe(_scope_review_rows(assurance), hide_index=True, width="stretch")
            opinion_rows = _report_review_issue_rows(assurance)
            attempts = sum(row["已发起批次"] for row in _scope_review_rows(assurance))
            recorded = assurance.get("report_review_calls_recorded", 0)
            st.caption(
                f"**调用次数分两列写**：**预算门尝试 {attempts}** 次（= 各块已发起的批次数"
                f"之和，含失败批）与**已落批记录 {recorded}** 条。两者不必相等——"
                "发起过的批会被预算门记账并真的发往 provider，但回复不可用的那一批"
                "**不留成功批记录**（失败尝试仍在调用账本），于是它的成员退出「收到可解析回复」、"
                "落进「未覆盖单元」。相差的 "
                f"{attempts - recorded} 次就属于这种情形。"
                "「调用上限（推出）」那一列见版本与审计页：它由冻结分批规则在这份输入上"
                "真的切一遍推出，不是输入面声明的常数。\n\n"
                "**审阅覆盖**说的是这次审阅**实际停在哪里**；"
                "「输入已备」不等于「已审阅」，而「已审阅」不等于「已通过」。")
            st.caption(_UNIT_COUNT_CAPTION)
            for kind, entered, never in _unreviewed_units(assurance):
                label = _SCOPE_ZH.get(kind, kind)
                total = len(entered) + len(never)
                st.error(
                    f"{label}：**{total} 个成员未获得可解析审阅**，而且它们不是同一种情况，"
                    "必须分开读：")
                if entered:
                    st.error(
                        f"· **{len(entered)} 个真的进了请求**（`{entered[0]}` … "
                        f"`{entered[-1]}`）：它们所在的那一批发了出去，但回复不可解析，"
                        "所以没有留下成功批记录、也没有回到「收到可解析回复」。"
                        "**未取得可采信的审阅结果；调用与失败均已留账。**")
                if never:
                    st.error(
                        f"· **{len(never)} 个从未发进请求**（`{never[0]}` … "
                        f"`{never[-1]}`）：审阅在它们之前就中止了，它们连请求都没进过。")
                st.error(
                    "这一块**没有跑完**，不得读成「已审阅、没问题」；也不得把这 "
                    f"{total} 个合并成一句「都从未发出」——上面第一拨是真的发出去了的。")
            if opinion_rows:
                st.markdown("#### 报告级审阅意见（原文照登，逐条保留）")
                st.dataframe(opinion_rows, hide_index=True, width="stretch")
                st.caption(_ISSUE_READING_NOTES)
            else:
                st.info("已绑定的报告级审阅记录里**没有**任何意见。"
                        "这只能读成「这几次调用没有报告问题」，"
                        "不能读成材料被逐条核实、也不能读成放行或人工接受。")
    rows = []
    for sid, section in run.sections.items():
        v = section.report_version
        review_state = str(v["system_review_state"])
        if sid == "financial":
            review_state = f"仅 A1：{review_state}；A2 未逐句审阅，整节不放行"
        rows.append({
            "章节": "主营业务" if sid == "company" else "财务分析",
            "流程": v["process_state"], "预览": v["preview_state"],
            "机械硬错": f"{len(section.hard_sentence_ids)}/{v['sentence_count']}",
            "节级系统审阅": review_state,
            "人工接受": v["human_review_state"],
            "发布资格": v["publishability"],
        })
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("这些是彼此独立的状态：流程走完、预览可读，不等于系统放行或人工接受。")


def _render_citation(key: str, item: dict[str, Any] | None) -> None:
    if item is None:
        st.error(f"引用 `{key}` 未在本节清单中。")
        return
    if key.startswith("m"):
        st.caption(
            f"`{key}` · {item.get('document_id') or '来源未登记'} · "
            f"角色 `{item.get('source_role') or '未知'}` · "
            f"定位 `{item.get('locator_ref') or '未给出'}`"
        )
        st.text(str(item.get("reading_view") or ""))
    else:
        st.caption(f"`{key}` · 合格事实 `{item.get('fact_id') or '未登记'}` · "
                   f"期间 `{item.get('period') or '未登记'}` · "
                   f"来源 `{item.get('source_identity') or '未登记'}`")
        st.text(str(item.get("text") or ""))


def _render_a1(section: demo_loader.CitedDemoSection,
               review_attested: bool) -> None:
    st.subheader("报告正文")
    st.caption("按自然段阅读；红色下划线标出机械硬错。每段下方可展开逐句依据与原文。")
    citations = _citation_index(section)
    checks = _sentence_index(section)
    reviews = _review_index(section)
    for subsection in section.draft["subsections"]:
        st.markdown(f"#### {subsection['title']}")
        for paragraph in subsection["paragraphs"]:
            rendered_sentences = []
            for sentence in paragraph["sentences"]:
                sid = str(sentence["sentence_id"])
                verdict = checks[sid]["verdict"]
                cls = "report-sentence-error" if verdict == "hard_error" else ""
                keys = "、".join(str(key) for key in sentence["citations"])
                rendered_sentences.append(
                    f'<span class="{cls}" title="{escape(sid)}">'
                    f'{escape(str(sentence["text"]))}</span>'
                    f'<sup class="report-citation">[{escape(keys)}]</sup>')
            st.markdown('<p class="report-paragraph">' + "".join(rendered_sentences)
                        + '</p>', unsafe_allow_html=True)
            with st.expander(f"本段逐句核对与来源 · {len(paragraph['sentences'])} 句",
                             expanded=False):
                for sentence in paragraph["sentences"]:
                    sid = str(sentence["sentence_id"])
                    verdict = checks[sid]["verdict"]
                    st.markdown(f"**{sid}** · {sentence['text']}")
                    if verdict == "hard_error":
                        reasons = "；".join(_hard_reason_label(str(x))
                                          for x in checks[sid]["failure_reasons"])
                        st.error(f"机械硬错误：{reasons}")
                    else:
                        st.caption("机械核对通过；不等于句义已充分支持。")
                    for issue in reviews.get(sid, []):
                        label = "既有独立审阅" if review_attested else "未核验的历史审阅文本"
                        st.caption(f"{label}：`{issue['category']}`"
                                   f" · {issue.get('reason') or '未给出说明'}")
                    for key in sentence["citations"]:
                        _render_citation(str(key), citations.get(str(key)))
    st.markdown("#### 缺口与补件")
    for gap in section.draft["gaps"]:
        st.warning(f"{gap['requirement_text']}：{gap['detail']}（{gap['reason']}）")
    if not section.draft["gaps"]:
        st.caption("本节未声明结构化缺口；这不等于 Contract 全部内容已覆盖。")


def _render_review(section: demo_loader.CitedDemoSection,
                   review_attested: bool) -> None:
    issues = section.review["issues"]
    supported = sum(i["category"] == "supported" for i in issues)
    st.markdown("#### 既有独立审阅意见（仅 A1 逐句）" if review_attested
                else "#### 未核验的历史审阅文本（不计为审阅完成）")
    if not review_attested:
        st.warning("M930-4 未能核实这份审阅与调用账本的一致性；以下内容仅作历史文本查看。")
    st.caption(f"产出者 `{section.review['review_producer_kind']}` · "
               f"留存文本 {len(issues)} 条，其中自报 supported {supported} 条；"
               "它不覆盖财务 A2，也不能推翻机械硬错误。")
    st.dataframe([{"句": i["sentence_id"], "类别": i["category"],
                   "严重度": i["severity"], "阻断": i["blocking"],
                   "理由": i["reason"]} for i in issues],
                 hide_index=True, width="stretch")
    reviewed_as_supported = {str(i["sentence_id"]) for i in issues
                             if i["category"] == "supported"}
    if set(section.hard_sentence_ids) & reviewed_as_supported:
        st.warning("Reviewer 对部分机械硬错误句也给出 supported；确定性硬错仍阻断放行。")


def _a2_gap_totals(balance: dict[str, Any] | None) -> dict[str, int]:
    """A2 的三类读数：结构项／读数条数、项目级 typed 缺口、notes 缺口注记。

    「模型调用 0 次」说的是这一栏**没有经过模型**，**不是**「这一栏没有缺口」——两者是不同
    的轴。把它们并排给出，页面才不会把一次零模型调用读成「A2 已完整」。缺口只数**真正落在
    项目上**的（`items[].gaps`）与**状态为缺口的注记**（`notes[].state == "gap"`）两类。
    """
    items = (balance or {}).get("items") or []
    notes = (balance or {}).get("notes") or []
    return {
        "items": len(items),
        "readings": sum(len(item.get("readings") or []) for item in items),
        "item_gaps": sum(len(item.get("gaps") or []) for item in items),
        "note_gaps": sum(1 for note in notes if note.get("state") == "gap"),
    }


def _a2_gap_rows(balance: dict[str, Any] | None) -> list[dict[str, str]]:
    """逐条展开的 A2 项目级缺口：所属结构项 + 代码 + 期间 + 原因 + 说明。

    只列原因，**不**把缺口改写成结论、**不**替任何一条缺口补数。渲染器与页面共用这一份，
    免得两处对「一共有几条缺口」给出不同答案。
    """
    rows: list[dict[str, str]] = []
    for item in (balance or {}).get("items") or []:
        for gap in item.get("gaps") or []:
            rows.append({"结构项": str(item.get("label") or ""),
                         "代码": str(gap.get("code") or "—"),
                         "期间": str(gap.get("period") or "—"),
                         "原因": str(gap.get("reason") or "—"),
                         "说明": str(gap.get("detail") or "")})
    return rows


def _render_balance(section: demo_loader.CitedDemoSection) -> None:
    st.subheader("A2 · 财务资产负债结构（确定性展示）")
    st.warning("A2 使用权威财务事实与 Decimal 确定性计算，**不是** A1 Writer 逐句正文，"
               "也**未纳入**上面的逐句独立语义审阅或节级零硬错结论。")
    balance = section.balance_structure
    if balance is None:
        st.error("本次没有 A2 结构产物。")
        return
    st.caption(f"指纹 `{balance['fingerprint'][:20]}…` · "
               f"产出者 `{balance['producer_kind']}` · 模型调用 {balance['model_calls_issued']} 次")
    totals = _a2_gap_totals(balance)
    st.caption(f"{totals['items']} 条结构项 · {totals['readings']} 条读数 · "
               f"项目级 typed 缺口 **{totals['item_gaps']}** 条 · "
               f"notes 缺口注记 **{totals['note_gaps']}** 条")
    st.info("「模型调用 0 次」说的是这一栏**没有经过模型**——它**不是**「A2 零缺口」，"
            "也不表示应收附注案例已经做完。本栏的缺口逐条落 typed 读数，展开可见每条的"
            "代码、期间与原因；缺口注记另列在末尾。")
    for item in balance["items"]:
        st.markdown(f"#### {item['label']}")
        if item["state"] == "gap":
            st.warning(str(item.get("reason") or "未取得"))
        if item.get("statement"):
            st.markdown(str(item["statement"]))
        gaps = item.get("gaps") or []
        if gaps:
            st.warning(f"本项 **{len(gaps)}** 条项目级 typed 缺口；展开看逐条原因。")
            with st.expander(f"{item['label']} · {len(gaps)} 条项目级缺口", expanded=False):
                st.dataframe(_a2_gap_rows({"items": [item]}), hide_index=True, width="stretch")
        with st.expander("计算与事实坐标", expanded=False):
            st.json(item.get("readings") or [], expanded=False)
    for note in balance["notes"]:
        if note["state"] == "gap":
            st.warning(f"{note['label']}：{note['detail']}")


def _render_metric_tables(section: demo_loader.CitedDemoSection) -> None:
    tables = section.metric_tables["tables"]
    st.subheader("财务权威指标表")
    st.caption(f"持久化的确定性表 {len(tables)} 张；代理口径诊断表与正文表分层呈现。")
    for table in tables:
        diagnostic = table["display_tier"] == "diagnostic_only"
        if diagnostic:
            st.warning("诊断槽位：代理口径，不并入普通正文表。")
        st.markdown(f"#### {table['caption']}")
        st.dataframe([{"指标": row["label"],
                       **{str(period): value for period, value in zip(
                           row["period_texts"], row["cells"])}
                       } for row in table["rows"]],
                     hide_index=True, width="stretch")
        with st.expander("逐格事实与引用键", expanded=False):
            st.json([{"指标": row["label"], "fact_ids": row["fact_ids"],
                      "citation_keys": row["citation_keys"]} for row in table["rows"]],
                    expanded=False)


def _render_source_regions(run: demo_loader.CitedDemoRun) -> None:
    st.subheader("原 PDF 表格区域 · 只读来源展示")
    confirmed = sum(r["confirmation_state"] == "human_confirmed"
                    for r in run.source_regions)
    st.warning(f"按文档／页／区域去重后 {len(run.source_regions)} 个区域，"
               f"人工完整、清晰、忠实确认 {confirmed} 个。"
               "用户此前只核过页码；页码核对不等于这三项确认。")
    st.caption("这些图不是合格结构化表，也不授权 Writer 使用图中的数字；本页没有确认按钮。")
    for item in run.source_regions:
        region = item["region"]
        key = str(region["region_key"])
        title = f"{region['source_name']} · 第 {region['page_number']} 页 · {region['title']}"
        with st.expander(title, expanded=False):
            st.image(run.source_images[key], caption=title, width="stretch")
            st.caption(f"单位：{region['unit_label'] or '未登记'} · "
                       f"期间：{region['period_label'] or '未登记'} · "
                       f"续表前驱：{region['continuation_of'] or '无'}")
            st.caption(f"渲染 SHA256：`{item['render_sha256']}` · "
                       f"人工确认：`{item['confirmation_state']}`")


def _render_trace(run: demo_loader.CitedDemoRun,
                  assurance: dict[str, Any] | None) -> None:
    st.subheader("来源、版本与审计轨迹")
    st.caption("根 run 没有跨节合并 report_version；两节各自绑定一套写作、核对和审阅身份。")
    rows = []
    for sid, section in run.sections.items():
        rows.append({"节": sid,
                     "report_version": section.report_version["report_version"],
                     "Writer 清单": section.manifest["manifest_id"],
                     "草稿": section.draft["draft_id"],
                     "逐句核对": section.checks["report_id"],
                     "独立审阅": section.review["bundle_id"],
                     "原 PDF 展示集": section.source_display["display_set_id"]})
    st.dataframe(rows, hide_index=True, width="stretch")
    st.markdown("#### 持久化文件完整性")
    st.caption(f"加载前后逐文件字节一致，验证文件 {len(run.file_hashes)} 个。"
               "此校验不把历史产物重新签发为可发布报告。")
    with st.expander("文件 SHA256 与调用账本", expanded=False):
        st.json({"file_hashes": run.file_hashes,
                 "call_ledger": run.ledger}, expanded=False)
    if assurance is not None:
        scopes = assurance.get("report_review_scopes") or []
        st.markdown("#### 报告级审阅输入与覆盖")
        st.caption("正文版本与内容版本是两条独立失效轴：正文改一字使 `cross_section` 失效；"
                   "A2 内容改一字使 `financial_a2` 失效，而两节正文版本不动。"
                   "「状态」永远是**输入**的状态；真实有没有被审阅看「覆盖」，"
                   "跑到哪一批看「已发起/已落批/失败批」。")
        coverage = (assurance.get("report_states") or {}).get("review_coverage") or {}
        opinions = (assurance.get("report_states") or {}).get("review_opinions") or {}
        if scopes:
            st.dataframe([{
                "内容块": _SCOPE_ZH.get(s["scope_kind"], s["scope_kind"]),
                "内容版本": s["scope_version"],
                "输入包": s["bundle_id"],
                "并列正文版本": "、".join(s["parent_report_versions"]),
                "状态": s["state"],
                "覆盖": _COVERAGE_ZH.get(coverage.get(s["scope_kind"]),
                                        coverage.get(s["scope_kind"], "未记录")),
                "已发起批": (opinions.get(s["scope_kind"]) or {}).get(
                    "requested_batch_count", 0),
                "已落批记录": (opinions.get(s["scope_kind"]) or {}).get(
                    "completed_batch_count", 0),
                "失败批": (opinions.get(s["scope_kind"]) or {}).get("failed_batch_count", 0),
                "意见": (opinions.get(s["scope_kind"]) or {}).get("issue_count", 0),
                "调用上限（推出）": (s.get("derived_batch_count")
                                    if s.get("derived_batch_count") is not None
                                    else f"推导失败：{s.get('derivation_error')}"),
            } for s in scopes], hide_index=True, width="stretch")
            st.caption("「调用上限（推出）」= 冻结分批规则在 `report_review_inputs.json` 的"
                       "这份输入上切出的批数；输入面只声明分批规则，不声明批数。"
                       "`irv-2` 起审阅**只报发现**，`supported_count` 通常为 0，"
                       "且不代表材料被核实——一条意见都没有的块只是「这次没报告问题」。")
        with st.expander("M930-4 只读限定复核详情", expanded=False):
            st.json(assurance, expanded=False)


def _render_assurance_rail(run: demo_loader.CitedDemoRun,
                           assurance: dict[str, Any] | None,
                           assurance_note: str) -> None:
    """报告旁的简明审核声明；完整证据仍在「审计详情」中。"""
    st.markdown("### 审核结果")
    st.error("不可发布 · 未经人工接受")
    st.caption("这是对当前已存版本的状态声明，不是授信审批结论。")
    st.markdown("**流程**　已结束" if run.ledger["run_outcome"] == "completed"
                else "**流程**　未结束")
    st.markdown("**预览**　可阅读" if all(
        section.report_version["preview_state"] == "draft_previewable"
        for section in run.sections.values()) else "**预览**　未完整就绪")
    st.markdown("**系统审核**　未通过" if assurance is not None and not
                assurance.get("system_release_eligible", False)
                else "**系统审核**　未取得可核验放行结果")
    st.markdown("**人工接受**　未复核")
    st.divider()
    st.markdown("**逐节机械核对**")
    for sid, section in run.sections.items():
        title = "主营业务" if sid == "company" else "财务分析"
        count = len(section.hard_sentence_ids)
        st.markdown(f"{title}：**{count}/{section.report_version['sentence_count']} 句硬错**")
    st.caption("审阅给出 supported 也不能抵消机械硬错。")
    st.divider()
    if assurance is None:
        st.warning(f"报告级审核读数未加载：{assurance_note}。不能据此认定已审阅。")
    else:
        st.markdown("**报告级独立审阅**")
        for row in _scope_review_rows(assurance):
            st.markdown(f"{row['内容块']}：{row['审阅覆盖']}")
            st.caption(f"请求 {row['放进请求']} · 可解析覆盖 {row['收到可解析回复']}"
                       f" · 意见 {row['模型提出问题']} · 未覆盖 {row['未覆盖单元']}")
        count = len(_report_review_issue_rows(assurance))
        st.warning(f"已记录报告级意见 {count} 条；意见不修改正文、不自动放行。")
    confirmed = sum(item["confirmation_state"] == "human_confirmed"
                    for item in run.source_regions)
    st.caption(f"原 PDF 区域人工完整/清晰/忠实确认：{confirmed}/{len(run.source_regions)}。")
    st.caption("正式阶段关闭另行治理；本页不作关闭裁决。")


def _render_start(run: demo_loader.CitedDemoRun) -> None:
    st.header("启动与输入")
    st.info("这是已存运行的只读演示入口。选择运行后可查看生成过程和报告；"
            "这里不会重新生成、发送模型请求或联网检索。")
    documents = sorted({str(item.get("document_id"))
                        for section in run.sections.values()
                        for item in section.manifest["materials"]
                        if item.get("document_id")})
    st.markdown(f"**已选择运行**　`{run.run_id}`")
    st.markdown(f"**运行模式**　`{run.ledger['mode']}`　　"
                f"**运行结果**　`{run.ledger['run_outcome']}`")
    st.markdown("**本次范围**　主营业务、财务分析；其他主题未由本次演示证明。")
    st.markdown("**已登记来源文档**　" + ("、".join(documents) or "本次清单未登记文档"))
    st.caption("来源名称来自已保存的 Writer 清单；报告时点、上传校验和运行中"
               "checkpoint 若未与此 run 绑定，本页不推测。")
    st.button("查看生成过程", type="primary", use_container_width=True,
              on_click=lambda: st.session_state.update({"cited_demo_view": "生成过程"}))
    st.button("直接阅读报告", use_container_width=True,
              on_click=lambda: st.session_state.update({"cited_demo_view": "报告预览"}))


def _progress_rows(run: demo_loader.CitedDemoRun,
                   assurance: dict[str, Any] | None) -> list[dict[str, str]]:
    """只从已经验证的产物派生保存节点，不把它们称为恢复 checkpoint。"""
    company = run.sections["company"]
    financial = run.sections["financial"]
    rows = [
        {"环节": "材料送达 Writer", "状态": "产物已保存",
         "可回查身份": f"{company.manifest['manifest_id']} / {financial.manifest['manifest_id']}",
         "实际读数": (f"公司 {len(company.manifest['materials'])} 份材料；"
                  f"财务 {len(financial.manifest['facts'])} 条事实")},
        {"环节": "Writer 正文", "状态": "产物已保存",
         "可回查身份": f"{company.draft['draft_id']} / {financial.draft['draft_id']}",
         "实际读数": (f"公司 {company.report_version['sentence_count']} 句；"
                  f"财务 {financial.report_version['sentence_count']} 句")},
        {"环节": "确定性逐句核对", "状态": "产物已保存",
         "可回查身份": (f"{company.checks['report_id']} / "
                  f"{financial.checks['report_id']}"),
         "实际读数": (f"公司 {len(company.hard_sentence_ids)} 句硬错；"
                  f"财务 {len(financial.hard_sentence_ids)} 句硬错")},
        {"环节": "逐句独立审阅", "状态": "回复已保存；效力见审核栏",
         "可回查身份": (f"{company.review['bundle_id']} / "
                  f"{financial.review['bundle_id']}"),
         "实际读数": "仅 A1 逐句正文；不覆盖财务 A2"},
        {"环节": "报告预览与表格", "状态": "产物已保存",
         "可回查身份": (f"{company.report_version['report_version']} / "
                  f"{financial.report_version['report_version']}"),
         "实际读数": f"原 PDF 区域 {len(run.source_regions)} 个，未替代数字权威"},
    ]
    if assurance is None:
        rows.append({"环节": "报告级审核", "状态": "未加载可核验读数",
                     "可回查身份": "—", "实际读数": "不能据此判断已审阅"})
    else:
        scope_rows = _scope_review_rows(assurance)
        rows.append({"环节": "报告级审核", "状态": "部分完成" if any(
            row["失败批次"] or row["未覆盖单元"] for row in scope_rows) else "读数已保存",
                     "可回查身份": str(assurance.get("policy_version") or "未登记"),
                     "实际读数": (f"发起 {sum(row['已发起批次'] for row in scope_rows)} 批；"
                              f"成功落批 {assurance.get('report_review_calls_recorded', 0)} 条")})
    return rows


def _render_progress(run: demo_loader.CitedDemoRun,
                     assurance: dict[str, Any] | None) -> None:
    st.header("生成过程 · 已存运行回放")
    st.caption("这是产物提交后的只读状态，不是实时任务监控。没有阶段时间与总任务量时，"
               "不显示百分比或估计耗时。")
    st.dataframe(_progress_rows(run, assurance), hide_index=True, width="stretch")
    st.markdown("#### Checkpoint 与恢复位置")
    st.warning("当前 cited run 没有与同一运行身份绑定的 `ProgressEvent`、"
               "恢复用 `checkpoint_id` 和创建时间，因此**最近 checkpoint 无法核验**。"
               "上表是已校验的产物保存节点，不是可恢复 checkpoint；"
               "不能拿 Pack ID、文件修改时间或另一运行的进度代替。")
    with st.expander("本次调用账本（只读）", expanded=False):
        st.json(run.ledger.get("call_budget") or {}, expanded=False)


def _render_report(run: demo_loader.CitedDemoRun,
                   assurance: dict[str, Any] | None,
                   assurance_note: str) -> None:
    report_column, audit_column = st.columns([3, 1], gap="large")
    with report_column:
        st.header("授信报告 · 不可发布预览")
        st.caption("左侧以正文和表格为主；原 PDF 区域只作来源回查。")
        tabs = st.tabs(["主营业务", "财务分析", f"原 PDF {len(run.source_regions)} 区"])
        with tabs[0]:
            _render_a1(run.sections["company"], _review_attested(assurance, "company"))
        with tabs[1]:
            _render_a1(run.sections["financial"], _review_attested(assurance, "financial"))
            _render_balance(run.sections["financial"])
            _render_metric_tables(run.sections["financial"])
        with tabs[2]:
            _render_source_regions(run)
    with audit_column:
        _render_assurance_rail(run, assurance, assurance_note)


# ============================================================ 本次运行（上传 → 新 run）


def _results_root() -> Path:
    """本次运行产物落在哪。缺省与链的 `--results-root` 同一个目录。"""
    raw = os.environ.get("CITED_DEMO_RESULTS_ROOT")
    return Path(raw).resolve(strict=False) if raw else (_REPO / "evaluation" / "results")


def _run_input_root() -> Path:
    """本次上传的原始字节落在哪（`.gitignore` 里整目录不入 git）。"""
    raw = os.environ.get("CITED_DEMO_RUN_INPUT_ROOT")
    return Path(raw).resolve(strict=False) if raw else _RUN_INPUT_ROOT


def _authorization_root() -> Path:
    """一次性运行授权的凭据落在哪。页面**只读**它——写它只有 `scripts/authorize_cited_run.py`
    一条路，也就是只有人能给。"""
    raw = os.environ.get("CITED_DEMO_AUTHORIZATION_ROOT")
    return Path(raw).resolve(strict=False) if raw else _AUTHORIZATION_ROOT


def _requested_mode(run_id: str) -> str:
    """本 run 被请求的模式，从**请求面**读。

    页面刷新之后 `st.session_state` 会丢，但「这一次请求的是什么模式」是盘上的事实。用会话状态
    去代替它，会让一次 `real` 运行在刷新后显示成离线——那正是「刷新后读不到真实进度」。
    """
    try:
        request = json.loads((_run_input_root() / run_id / run_input_mod.REQUEST_NAME)
                             .read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(request.get("mode") or "")


def _live_authorization_identity(staged: Path, run_id: str, mode: str) -> dict[str, Any] | None:
    """本次运行**实际**的身份，供页面与凭据逐字段比对。读不出来就返回 `None`（页面照实写）。

    这一次给出的身份包含**三组**绑定：三份上传 PDF（`cri-1`）、三份上传 XLSX 与它们对应的
    权威快照（`cfi-1`）、以及请求面声明的 demo scope profile。缺任何一组都返回 `None`——
    凭据是拿这三组一起批的，页面不该用其中两组去「差不多对上」一份三组的凭据。
    """
    try:
        binding = run_input_mod.load_run_input(staged)
        financial_binding = fin_input_mod.load_financial_input(staged)
        request = json.loads((staged / run_input_mod.REQUEST_NAME).read_text("utf-8"))
    except (run_input_mod.CitedRunInputError, fin_input_mod.CitedFinancialInputError,
            OSError, json.JSONDecodeError):
        return None
    from config import LLM_MODEL as PROJECT_WRITER_MODEL
    model = str(PROJECT_WRITER_MODEL or "").strip()
    if not model:
        return None
    try:
        policy = CB.cited_call_budget_policy(approved_model=model)
    except CB.CitedBudgetError:
        return None
    profile_path = str(request.get("profile_path") or "").strip()
    if not profile_path:
        return None
    try:
        from planning import demo_scope as SC_scope
        profile = SC_scope.load_demo_scope_profile(
            Path(profile_path) if Path(profile_path).is_absolute() else _REPO / profile_path)
    except Exception:  # noqa: BLE001 - 读不出 profile 就没有可绑定的范围身份
        return None
    try:
        return run_auth.describe_live(
            run_id=run_id, mode=mode, subject=str(request.get("subject") or ""),
            section_ids=tuple(str(s) for s in (request.get("section_ids") or ())),
            model=model, policy=policy, binding=binding,
            financial_binding=financial_binding, profile=profile)
    except run_auth.CitedAuthorizationError:
        return None


def _authorization_reading(staged: Path, run_id: str, mode: str) -> tuple[str, str]:
    """页面要的那一个读数：`(状态, 说明)`。

    状态只有四个值，且**不含**「已放行」这种承诺——页面能证明的是「盘上有一份逐字段对上的
    凭据」，不是「系统认为应该放行」：

    * `absent`  —— 没有凭据：真实模式会在第一个请求之前停下；
    * `consumed`—— 凭据已被消费：本 run 的那一次用掉了，再点也消费不动；
    * `mismatch`—— 有凭据但字段对不上，附上链会给出的同一条理由；
    * `granted` —— 有一份逐字段对上的、尚未消费的凭据。
    """
    root = _authorization_root()
    state = run_auth.authorization_state(root, run_id=run_id)
    if not state["present"]:
        return "absent", "尚无本次运行的一次性授权。真实模式会在**第一个模型请求之前**停下。"
    if state["consumed"]:
        used = run_auth.read_consumption(root, run_id=run_id) or {}
        return "consumed", (f"本次运行的授权已被消费（{used.get('authorization_id', '?')}，"
                            f"{used.get('consumed_at_utc', '?')}）：一次性授权用掉就没了，"
                            "重复点击或另一个会话都不能再消费同一份。")
    try:
        authorization = run_auth.load_authorization(root, run_id=run_id)
    except run_auth.CitedAuthorizationError as exc:
        return "mismatch", f"授权读不开：{exc}"
    live = _live_authorization_identity(staged, run_id, mode)
    if live is None:
        return "mismatch", "本 run 的运行身份读不出来（上传绑定或请求面缺失），无法与授权比对。"
    try:
        run_auth.check_authorization(authorization, live=live)
    except run_auth.CitedAuthorizationError as exc:
        return "mismatch", f"授权与本次运行对不上：{exc}"
    return "granted", (f"已有一份逐字段对上的授权（{authorization.authorization_id}，"
                       f"批准人 {authorization.granted_by}）：它尚未被消费，"
                       "启动时由链原子地消费一次；消费之后本行会变成「已被消费」。")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id(results_root: Path, run_input_root: Path) -> str:
    """铸一个**此前不存在**的 run-id。冲突就顺延序号，不覆盖任何已有目录。

    只铸一次：调用点是「开始生成」的点击回调，Streamlit 的每次重绘都不会再走到这里。
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for attempt in range(100):
        candidate = f"{_NEW_RUN_PREFIX}{stamp}" + ("" if attempt == 0 else f"_{attempt + 1}")
        if not (results_root / candidate).exists() and not (run_input_root / candidate).exists():
            return candidate
    raise RuntimeError(f"时间戳 {stamp} 下连续 100 个 run-id 都已存在：请稍后重试")


def _subject_candidates() -> tuple[str, ...]:
    """可声明的主体候选：只读财务库的 current 快照表。库里没有就返回空，不猜。"""
    try:
        return run_input_mod.current_subjects(_REPO / "data" / "financial_v2.db")
    except Exception:  # noqa: BLE001 - 读不到就没有候选，页面如实说没有
        return ()


def _declaration_for(subject: str) -> tuple[run_input_mod.DeclaredDocument, ...]:
    """当前 Evidence 登记声明这一家公司要上传的那几份材料。**只读**，不写库、不迁移。"""
    return upload_view.declared_documents_for(subject, evidence_db=_EVIDENCE_DB)


def _registered_subject_name(subject: str) -> tuple[str, str]:
    """这一家在财务库里**登记过**的主体名称：`(名称, 说明)`。

    `subj-2` 要的是「声明 + 核对」：读者面写的公司名称必须由权威自己的登记去核，页面不编一个。
    因此这里只读登记：恰好一个才给出可声明的名称；零个或多个都返回空串，并把原因写进说明——
    「登记缺失」与「登记互相矛盾」是两种不同的缺陷，页面不该把它们合并成一句「读不到」。
    """
    try:
        names = fin_input_mod.registered_subject_names(_FINANCIAL_DB, subject=subject)
    except Exception as exc:  # noqa: BLE001 - 读不到就没有可声明的名称，页面如实说
        return "", f"财务库读不到：{type(exc).__name__}: {exc}"
    if not names:
        return "", "财务库里没有该主体的登记名称：名称是「声明 + 核对」，页面不替它编一个。"
    if len(names) > 1:
        return "", (f"财务库登记了不止一个主体名称 {list(names)}：来源侧自相矛盾，"
                    "页面不任选一个。")
    return names[0], ""


def _financial_declaration(subject: str, subject_name: str) -> tuple[
        "fin_input_mod.SnapshotIdentity | None",
        tuple["fin_input_mod.DeclaredFinancialSource", ...], str]:
    """本次财务上传要核对的**权威侧**：当前有效快照身份 + 它的来源版本。只读。

    顺序是**先取快照、再要求上传对上它**。反过来（先信上传、再去库里找一条「差不多」的快照）
    会把「A 快照被 B 快照顶替」写成一次成功上传。
    """
    if not subject or not subject_name:
        return None, (), "尚无可声明的财务主体名称：财务上传无从核对。"
    try:
        snapshot = fin_input_mod.current_snapshot_identity(
            _FINANCIAL_DB, subject=subject, subject_name=subject_name)
        sources = fin_input_mod.declared_financial_sources(
            _FINANCIAL_DB, version_ids=[v for v, _ in snapshot.source_versions])
    except Exception as exc:  # noqa: BLE001 - 取不到权威侧就没有可核对的声明
        return None, (), f"财务权威快照读不出来（fail-closed）：{type(exc).__name__}: {exc}"
    return snapshot, sources, ""


def _verify_financial_uploads(payload: list[tuple[str, bytes]],
                              declared: tuple["fin_input_mod.DeclaredFinancialSource", ...]
                              ) -> list[dict[str, Any]]:
    """把**本次上传的 XLSX 字节**与声明的来源版本逐份对上。**只看内容哈希，不看文件名。**

    与 `stage_financial_input` 同一条判据（先按 `declared_sha256` 匹配，再把上传名只当凭据），
    因此页面这几格显示的「一致 / 不一致」与实际建立绑定时的判据同源。
    """
    by_sha: dict[str, list[tuple[str, bytes]]] = {}
    for name, data in payload:
        by_sha.setdefault(run_input_mod.sha256_bytes(data), []).append((name, data))
    rows: list[dict[str, Any]] = []
    for source in declared:
        hits = by_sha.get(source.file_sha256.lower(), [])
        rows.append({
            "快照来源版本": source.source_version,
            "登记工作簿（仅凭据）": source.source_name,
            "登记 SHA-256": source.file_sha256,
            "你选择的文件": "、".join(name for name, _ in hits) or "未匹配",
            "逐个核对": "一致" if len(hits) == 1 else ("重复" if hits else "不一致"),
        })
    declared_shas = {s.file_sha256.lower() for s in declared}
    for name, data in payload:
        if run_input_mod.sha256_bytes(data).lower() not in declared_shas:
            rows.append({"快照来源版本": "（不在快照声明内）", "登记工作簿（仅凭据）": "—",
                         "登记 SHA-256": "—", "你选择的文件": name, "逐个核对": "不采信"})
    return rows


def _financial_uploads_accepted(payload: list[tuple[str, bytes]],
                                declared: tuple["fin_input_mod.DeclaredFinancialSource",
                                                ...]) -> bool:
    """三份上传是不是**恰好**覆盖声明的三条来源版本，且没有多余文件。"""
    if not declared:
        return False
    declared_shas = sorted(s.file_sha256.lower() for s in declared)
    uploaded = sorted(run_input_mod.sha256_bytes(data).lower() for _, data in payload)
    return uploaded == declared_shas


def _financial_upload_error(payload: list[tuple[str, bytes]],
                            declared: tuple["fin_input_mod.DeclaredFinancialSource", ...]
                            ) -> str:
    """不通过时给一句**具名**的原因；通过时给空串。"""
    if not declared:
        return "尚无可核对的快照声明"
    declared_shas = sorted(s.file_sha256.lower() for s in declared)
    uploaded = sorted(run_input_mod.sha256_bytes(data).lower() for _, data in payload)
    if uploaded == declared_shas:
        return ""
    extra = [name for name, data in payload
             if run_input_mod.sha256_bytes(data).lower() not in set(declared_shas)]
    if extra:
        return "混入了快照声明之外的文件：" + "、".join(extra)
    missing = len(declared_shas) - len(set(uploaded) & set(declared_shas))
    return f"缺 {missing} 份（或某一份字节与快照声明不符）"


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


def _process_started_at_utc() -> str:
    """**本进程**的启动时刻，直接问操作系统取本进程句柄（不读文件、不问别的解释器）。

    为什么不用模块级 `datetime.now()`：Streamlit 每次重绘都重跑整个脚本，模块级的时间戳
    会被改写成「最近一次重绘」而不是「进程启动」。进程启动时刻只能从 OS 拿。
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        #: `argtypes` 必须显式写：`GetCurrentProcess()` 返回的是伪句柄 `-1`（无符号 64 位全 1），
        #: 不给 argtypes 时 ctypes 按默认的 32 位 `int` 传参，当场 `OverflowError`——页面于是
        #: 只能写「未取得」。这不是拿不到，是参数类型没声明。
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FILETIME),
                                        ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
                                        ctypes.POINTER(_FILETIME)]
        k32.GetProcessTimes.restype = wintypes.BOOL
        creation, exit_t, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        ok = k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(creation),
                                 ctypes.byref(exit_t), ctypes.byref(kernel),
                                 ctypes.byref(user))
        if not ok:
            return ""
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        epoch = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)
        return epoch.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001 - 拿不到就照实留空，不编一个时间
        return ""


def _page_process_identity() -> dict[str, str]:
    """**本页面进程内存里**的读数，供操作员在上传前核对。

    这些值全部取自本进程 `sys.modules` 里**已经导入**的模块对象，加上 `os.getpid()` 与 OS
    给的进程启动时刻——**不**读盘、**不**另起解释器、**不**用 `python -c` 代替。因此它证明的
    是「**这一个**页面进程此刻持的是哪一版」，而不是「盘上现在是哪一版」。

    为什么要分开：长驻的 Streamlit 进程（`--server.fileWatcherType none`）每次重绘都会重跑
    脚本，但**复用 `sys.modules`**——模块级常量在服务启动时就定型了。改完提示词再重启前，
    独立探针读到的是盘上的新版本，页面却仍持旧版本；这种不一致只能靠**页面自己报内存读数**
    暴露。这不是授权、不是报告身份，也不参与任何业务判据。
    """
    from config import LLM_MODEL
    return {
        "页面进程 PID": str(os.getpid()),
        "进程启动（UTC）": _process_started_at_utc() or "未取得",
        "项目路径": str(_REPO),
        "解释器": sys.executable,
        "Writer 提示词版本": f"{CW.CITED_WRITER_PROMPT_ASSET}@{CW.CITED_WRITER_PROMPT_REVISION}",
        "Reviewer 提示词版本": f"{CR.CITED_REVIEW_PROMPT_ASSET}@{CR.CITED_REVIEW_PROMPT_REVISION}",
        "预算策略版本": CB.CITED_BUDGET_POLICY_VERSION,
        "模型": str(LLM_MODEL or ""),
    }


def _render_process_identity() -> None:
    """第一屏的只读预检：把**本页面进程内存里**的四项版本与 PID 画出来。"""
    with st.container(border=True):
        st.markdown("#### ⑥ 页面进程只读预检（操作员读数 · 不参与授权与判据）")
        st.caption("下面每一格都取自**本页面进程已导入的模块对象**（不是另起解释器、不是读盘）。"
                   "重启服务前，长驻进程会一直持启动那一刻的版本；这一屏就是给操作员在上传前"
                   "肉眼核对「页面手里这一版」用的。")
        identity = _page_process_identity()
        #: 用 `st.table`（真 HTML 表）而不是 `st.dataframe`（glide 画布）：这两处都要读得到——
        #: 操作员在录像里肉眼读，以及 CDP 从可访问层逐格读。画布表格的文字不在 DOM 里。
        st.table([{"读数": k, "本页面进程内存中的值": v} for k, v in identity.items()])
        st.caption("对照关系：Writer 提示词版本就是链会写进请求面的 `prompt_version`，"
                   "Reviewer 提示词版本与预算策略版本同上，模型是 `config.LLM_MODEL`。"
                   "任一格与本次运行被批准的值不同，**不要**上传——先重启页面进程再核对。")


def _render_new_run_upload() -> None:
    """第一屏：上传三份 PDF + 三份 XLSX → （点击时）建立**一次**双节真实运行。"""
    st.header("第 1 步 / 共 3 步 · 新建授信报告")
    st.info("本屏与「历史对照」不同：**六份**材料逐字节核对通过后，点击「开始生成」会建立"
            "**一次** create-only 运行——把本次上传的原始字节落进两个各自不可覆盖的输入目录"
            "（公司节的三份 PDF、财务节的三份 XLSX），再拉起**同一条正式链**真的跑一遍，"
            "同一次运行里同时产出 `company` 与 `financial`。页面不复用任何历史 run 的"
            "正文、引用或审阅结果。")

    refused = st.session_state.get("cited_deeplink_refused")
    if refused:
        del st.session_state["cited_deeplink_refused"]
        st.warning(f"深链 `?view={refused}` 未伴随本次上传：已拒绝直接进入第 2、3 屏并回到本屏。"
                   "页面**不会**回落到任何历史 run 的内容。")

    left, right = st.columns([2, 1], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("#### ① 借款主体")
            subjects = _subject_candidates()
            if subjects:
                subject = st.selectbox("证券代码", list(subjects), index=0, key="cur_subject")
                st.caption("候选只读自 `data/financial_v2.db` 的 current 快照表；要上传的 PDF"
                           "另从 `data/evidence.db` 的**当前**登记读取，要上传的 XLSX 与"
                           "快照的 `source_versions` 逐份核对。")
            else:
                subject = ""
                st.error("财务库里没有任何 current 快照主体：本页不给可声明的主体，"
                         "也不拿证券代码或文件名冒充。")
            subject_name, name_note = (_registered_subject_name(subject) if subject
                                       else ("", "尚未选择主体"))
            st.text_input("企业全称（来自财务库唯一登记，声明后由链逐字核对）",
                          value=subject_name, disabled=True)
            if subject and not subject_name:
                st.error(f"没有可声明的企业全称：{name_note}")

        with st.container(border=True):
            st.markdown("#### ② 授信类型")
            st.radio("授信类型", _CREDIT_TYPE_OPTIONS, index=0,
                     key="cur_credit_type", horizontal=True)
            st.caption(_CREDIT_NOTE)

        with st.container(border=True):
            st.markdown("#### ③ 拟定授信方案")
            cols = st.columns(3)
            cols[0].text_input("授信金额（万元）", key="cur_amount", placeholder="未绑定")
            cols[1].text_input("授信期限（月）", key="cur_term", placeholder="未绑定")
            cols[2].text_input("增信措施", key="cur_guarantee", placeholder="未绑定")
            st.caption(_CREDIT_NOTE)

        with st.container(border=True):
            st.markdown("#### ④ 上传业务材料（三份电子 PDF · 公司节）")
            uploads = st.file_uploader("选择本案例的三份来源 PDF", type=["pdf"],
                                       accept_multiple_files=True, key="cur_uploader")
            payload = [(getattr(f, "name", "未命名"), f.getvalue()) for f in (uploads or [])]
            declared: tuple[run_input_mod.DeclaredDocument, ...] = ()
            verification = None
            if subject:
                try:
                    declared = _declaration_for(subject)
                except Exception as exc:  # noqa: BLE001 - 读不到登记就不给可上传的声明
                    st.error(f"读不到当前 Evidence 登记，本次不给可上传的声明："
                             f"{type(exc).__name__}: {exc}")
                if declared:
                    #: 声明侧取自**当前 Evidence 登记**（与 `stage_run_input` 同一份权威），
                    #: 因此页面这几格显示的「一致 / 不一致」与实际建立运行输入时的判据同源。
                    verification = binding_mod.verify_source_documents(payload, declared)
            if verification is not None:
                st.dataframe([{
                    "登记文档": v.document_id,
                    "来源角色": run_input_mod.EVIDENCE_SOURCE_ROLE,
                    "你选择的文件": v.uploaded_name or "未匹配",
                    "登记 SHA-256": v.declared_sha256,
                    "本次内存哈希": v.uploaded_sha256 or "—",
                    "字节": v.size_bytes,
                    "逐个核对": "一致" if v.matches else "不一致",
                } for v in verification.verdicts], hide_index=True, width="stretch")
                if verification.unexpected:
                    st.error("以下文件不在本案例声明的来源里，**不采信也不忽略**："
                             + "、".join(verification.unexpected))
                if verification.accepted and not verification.unexpected:
                    st.success("三份材料逐字节与当前登记一致，且没有多出别的文件。")
                else:
                    st.error("材料与当前登记不符，或混入了登记之外的文件：本次不能开始生成。")
                    st.caption("哈希不一致的候选**可能只是同名文件**。本页不提供"
                               "「跳过校验继续」的入口，也不会回退去读 `data/samples` 里的同名文件。")

        with st.container(border=True):
            st.markdown("#### ⑤ 上传财务材料（三份 XLSX · 财务节）")
            st.caption("这三份是**财务来源**，与上面三份 PDF 是**两组不同材料**：各自一条"
                       "类型化绑定（PDF 走 `cri-1`，XLSX 走 `cfi-1`），互不冒充。本批**不**重抽取"
                       "工作簿、**不**写共享财务库——财务节复用的是库里**当前有效**的权威快照，"
                       "本次只核对并复用它。")
            fin_uploads = st.file_uploader("选择本次核对的三份财务工作簿", type=["xlsx"],
                                           accept_multiple_files=True, key="cur_fin_uploader")
            fin_payload = [(getattr(f, "name", "未命名"), f.getvalue())
                           for f in (fin_uploads or [])]
            snapshot = None
            fin_declared: tuple["fin_input_mod.DeclaredFinancialSource", ...] = ()
            fin_note = "尚未选择主体"
            if subject:
                snapshot, fin_declared, fin_note = _financial_declaration(subject, subject_name)
            if snapshot is not None:
                st.dataframe([{
                    "快照": snapshot.snapshot_id,
                    "主体": f"{snapshot.company_id}／{snapshot.company_name}",
                    "期末": snapshot.as_of_date, "口径": snapshot.scope,
                    "币种": snapshot.currency, "用途": snapshot.purpose,
                    "有效性": snapshot.validity,
                    "来源版本": len(snapshot.source_versions),
                }], hide_index=True, width="stretch")
                st.caption("快照是只读选取的**当前有效**那一条；下面的三份上传要与它的"
                           "`source_versions` **逐份同字节**（比对的是内容哈希，不是文件名）。")
                st.dataframe(_verify_financial_uploads(fin_payload, fin_declared),
                             hide_index=True, width="stretch")
            elif fin_note:
                st.error(fin_note)

        _render_process_identity()

    fin_ready = bool(subject_name and snapshot is not None
                     and _financial_uploads_accepted(fin_payload, fin_declared))
    ready = bool(verification is not None and verification.accepted
                 and not verification.unexpected and fin_ready)
    busy = _run_in_flight()
    with right:
        st.markdown("### 本次范围")
        st.markdown("**公司节（主营业务）**　本次生成（三份上传 PDF）")
        st.markdown("**财务节**　本次生成（三份上传 XLSX + 复用权威快照）")
        st.caption("同一次上传创建的是**一个** run，两节在同一次运行里产出：公司节走 A1 正文，"
                   "财务节同时产出 A1 正文与 A2 确定性结构。两节的审阅、机械核对与放行状态"
                   "在第 3 屏**分开**列出。")
        st.divider()
        st.markdown("### 运行方式")
        mode = st.radio("运行模式", _MODE_OPTIONS, index=0, key="cur_mode", horizontal=True,
                        help="`offline`：写作、返修与审阅全部由离线替身产出，并装上断网 guard。"
                             "`real`：只把写作、返修与审阅换成真实模型客户端，其余逐字不变——"
                             "链会在建立结果目录**之前**核对两节是否都已获批预算、六份材料哈希"
                             "与快照身份是否都对得上。")
        st.caption("**默认是 `offline`**：不改这一项就按离线替身跑。**拿到一份获批凭据不会**"
                   "自动把这里改成 `real`——模式仍然由上面这个选择决定，授权只在真的选了 "
                   "`real` 之后才会被链核对并消费。")
        if mode == "real":
            st.warning("真实模式：链会在第一请求之前核对两节是否都已获批、六份上传与快照身份"
                       "是否对得上；任一条不符就停下。本页不代替那次授权，也不放宽任何门。")
        st.divider()
        st.markdown("### 材料核对")
        if verification is None:
            st.error("尚无可核对的 PDF 声明")
        elif verification.accepted and not verification.unexpected:
            st.success("公司节 3 / 3 一致，且无额外文件")
        else:
            matched = sum(1 for v in verification.verdicts if v.matches)
            st.error(f"公司节 {matched} / {len(verification.verdicts)} 一致")
        if snapshot is None:
            st.error("财务快照尚未读取")
        elif fin_ready:
            st.success(f"财务节 3 / 3 与快照 `{snapshot.snapshot_id}` 逐份同字节")
        else:
            st.error(f"财务节未通过：{_financial_upload_error(fin_payload, fin_declared)}")
        st.button("开始生成", type="primary", use_container_width=True,
                  disabled=(not ready or busy),
                  on_click=_launch_new_run,
                  args=(payload, declared, fin_payload, subject, subject_name, mode))
        if busy:
            st.caption(f"上一次运行仍在进行：`{st.session_state.get('upload_run_id')}`。"
                       "同一时刻只跑一次。")
        else:
            st.caption("点击后：铸 run-id → 落盘六份上传字节（各自不可覆盖）→ 拉起正式链。"
                       "每次点击是**一次新的** create-only 运行。")

    note = st.session_state.get("upload_launch_note")
    if note:
        del st.session_state["upload_launch_note"]
        kind, message = note
        #: `blocked` 与 `error` 分开：前者是「**没有**发起请求，等一次授权」，后者是「试了但
        #: 起不来」。把两者画成同一个红框，读者会把「等着被批准」读成「已经失败了」。
        {"ok": st.success, "blocked": st.warning}.get(kind, st.error)(message)
    if st.session_state.get("upload_run_id"):
        st.caption(f"本会话当前的运行：`{st.session_state['upload_run_id']}`"
                   f"（{st.session_state.get('upload_mode', '')}）。"
                   "用上方页面切换器进入第 2、3 屏。")


def _run_in_flight() -> bool:
    proc = st.session_state.get("upload_process")
    return bool(proc is not None and proc.poll() is None)


def _launch_new_run(payload: list[tuple[str, bytes]],
                    declared: tuple[run_input_mod.DeclaredDocument, ...],
                    fin_payload: list[tuple[str, bytes]],
                    subject: str, subject_name: str, mode: str) -> None:
    """「开始生成」的点击回调：**唯一**一处铸 run-id、落盘六份上传、拉起运行。

    它是 Streamlit 的 `on_click` 回调，因此只在真的被点的那一次执行——页面重绘不会重复落盘，
    也就不会产生孤儿输入。这里只写 `session_state`，不调用任何 `st.*` 渲染命令：回调里画出来
    的东西不在正文流里，读者会看漏。

    **两组材料各走各的绑定**：三份 PDF 由 `cri-1` 落盘，三份 XLSX 由 `cfi-1` 落进**同一个**
    运行输入目录（但互不覆盖对方的文件）。财务那一份只在**先取到当前有效快照、再把上传逐份
    对上它**之后才建立；任一步失败就把这个刚建出来的目录整个撤掉——一次点不出半份输入。
    """
    results_root = _results_root()
    run_input_root = _run_input_root()
    run_id = _new_run_id(results_root, run_input_root)
    staged = run_input_root / run_id
    try:
        staged_binding = run_input_mod.stage_run_input(payload, staged, declared=declared,
                                                       run_id=run_id)
    except run_input_mod.CitedRunInputError as exc:
        st.session_state["upload_launch_note"] = (
            "error", f"运行输入未建立，本次**没有**创建任何 run（也不回退到登记路径）：{exc}")
        return

    try:
        snapshot, fin_declared, note = _financial_declaration(subject, subject_name)
        if snapshot is None:
            raise fin_input_mod.CitedFinancialInputError(note or "无可核对的财务快照")
        fin_binding = fin_input_mod.stage_financial_input(
            fin_payload, staged, declared=fin_declared, snapshot=snapshot, run_id=run_id)
    except (fin_input_mod.CitedFinancialInputError, OSError) as exc:
        #: 财务那一半没建立起来：把刚建出来的 PDF 输入目录整个撤掉。留一个只含 PDF 的
        #: 「运行输入目录」会让下一次点击、或一个按 run-id 读取的人，读到一个**半份**输入。
        shutil.rmtree(staged, ignore_errors=True)
        st.session_state["upload_launch_note"] = (
            "error", f"财务输入未建立，本次**没有**创建任何 run（也不回退到 data/samples 或旧"
                     f"快照）：{exc}")
        return

    run_input_mod.write_run_request(
        staged, run_id=run_id, mode=mode, section_ids=_NEW_RUN_SECTIONS, subject=subject,
        subject_name=subject_name, profile_path=_DEMO_PROFILE)
    for key, value in (("upload_run_id", run_id), ("upload_staged_dir", str(staged)),
                       ("upload_results_root", str(results_root)), ("upload_mode", mode),
                       ("upload_subject", subject), ("upload_subject_name", subject_name)):
        st.session_state[key] = value
    st.session_state["cited_demo_view"] = _VIEW_OPTIONS[1]

    #: 真实模式**先**看盘上有没有一份逐字段对上的授权。没有就不拉起进程——不是「拉起来再说」，
    #: 而是根本不让这一次运行进入会发请求的那条路径。页面这一层是体验，真正的门在链上
    #: （链在第一请求之前自己再查一遍），因此绕过页面直接调 CLI 也拿不到请求。
    if mode == "real":
        state, detail = _authorization_reading(staged, run_id, mode)
        if state != "granted":
            st.session_state["upload_launch_note"] = (
                "blocked", f"本次运行 `{run_id}` 已落盘，但**未发起任何请求**：{detail}"
                           f"（授权状态 `{state}`）。批准后在第 2 屏启动它。")
            return
    _spawn_new_run(staged, run_id, mode,
                   manifest_prefix=staged_binding.manifest_sha256[:16])


def _spawn_new_run(staged: Path, run_id: str, mode: str, *,
                   manifest_prefix: str | None = None) -> bool:
    """把正式链拉起来（**同一个** CLI 入口，不是第二套运行时）。返回是否拉起来了。"""
    prefix = manifest_prefix
    if prefix is None:
        try:
            prefix = run_input_mod.load_run_input(staged).manifest_sha256[:16]
        except run_input_mod.CitedRunInputError:
            prefix = "?"
    subject = str(st.session_state.get("upload_subject") or "")
    if not subject:
        st.session_state["upload_launch_note"] = (
            "error", "本会话没有记下本次运行的主体：不猜、不回落，因此不拉起任何进程。"
                     "请重新走一次「上传 → 开始生成」。")
        return False
    subject_name = str(st.session_state.get("upload_subject_name") or "")
    if not subject_name:
        st.session_state["upload_launch_note"] = (
            "error", "本会话没有记下本次运行声明的**主体名称**：`subj-2` 的名称核对无从发生，"
                     "因此不拉起任何进程。请重新走一次「上传 → 开始生成」。")
        return False
    command = [sys.executable, "-X", "utf8",
               str(_REPO / "scripts" / "run_m930_3_cited_chain.py"),
               "--run-input", str(staged), "--run-id", run_id,
               "--financial-input", str(staged),
               "--demo-scope-profile", _DEMO_PROFILE,
               "--section", ",".join(_NEW_RUN_SECTIONS), "--mode", mode,
               "--subject", subject, "--subject-name", subject_name,
               "--authorization-root", str(_authorization_root())]
    try:
        with open(staged / "chain.log", "wb") as log:
            proc = subprocess.Popen(command, cwd=str(_REPO), stdout=log,
                                    stderr=subprocess.STDOUT,
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except OSError as exc:
        st.session_state["upload_launch_note"] = (
            "error", f"无法拉起运行进程（{type(exc).__name__}: {exc}）。上传字节与请求面已落在 "
                     f"`{staged}`；链从未启动，因此结果目录**没有**建立。")
        return False
    st.session_state["upload_process"] = proc
    st.session_state["upload_launched_at"] = _utc_now()
    st.session_state["upload_launch_note"] = (
        "ok", f"已启动本次运行 `{run_id}`（{mode}）。上传清单指纹 `{prefix}…`；"
              "下面进入第 2 屏看它**真实**的阶段事件。")
    return True


def _active_run_id() -> str | None:
    """本会话当前在看的 run。没有就是 `None`——**不**回落到任何历史 run。"""
    value = st.session_state.get("upload_run_id")
    return value if isinstance(value, str) and value else None


def _pick_run(*, key: str) -> str | None:
    """取本次会话的 run；没有时给一个**显式**按 run-id 读取的入口。

    显式读入的 run 同样要过 `run_input_binding.json` 那道门，因此它不是绕过上传的旁路：
    历史 run 没有这份绑定，在这里一律进不来。
    """
    run_id = _active_run_id()
    if run_id:
        return run_id
    st.warning("本会话还没有创建过运行。请先在「① 上传材料」上传三份来源 PDF 并点击「开始生成」。"
               "页面**不会**回落到任何历史 run。")
    #: 用 `st.form` 而不是裸 `st.text_input` + `st.button`：后者要写成 `if typed and st.button(…)`，
    #: 于是**按钮在输入框为空时根本不渲染**——页面上写着「按 run-id 读取一次」，却没有任何可点的
    #: 东西；真去点的人只能先回车一次把它召唤出来。表单把这两步合成一步（回车或点按钮都提交），
    #: 按钮也始终在那儿。这不放宽任何门：读回来的 run 仍要过下面那道绑定检查。
    with st.form(key=f"{key}_load", border=False):
        typed = str(st.text_input("或按 run-id 读取一次由本入口创建过的运行",
                                  key=f"{key}_input",
                                  placeholder=f"{_NEW_RUN_PREFIX}…") or "").strip()
        submitted = st.form_submit_button("读取该 run")
    if submitted and typed:
        st.session_state["upload_run_id"] = typed
        st.session_state["upload_results_root"] = str(_results_root())
        st.session_state["upload_staged_dir"] = str(_run_input_root() / typed)
        return typed
    if submitted:
        st.caption("上面那一行还是空的：本页不猜 run-id，也不回落到任何历史 run。")
    return None


def _require_bound_run(run_id: str) -> bool:
    """这个 run 是不是「本次上传产生、并被本次运行读过」的那一类。

    两条判据都在产物侧：运行目录里有 `run_input_binding.json`，且暂存目录里的 `cri-1` 清单能
    重新读回并逐字节复核。历史 run 两样都没有 —— 于是在这里**进不来**，不是「显示得少一点」，
    也不是降级成历史回放。
    """
    run_dir = _results_root() / run_id
    if not run_dir.is_dir():
        st.error(f"运行目录不存在：`{run_id}`")
        return False
    if not (run_dir / run_input_mod.BINDING_NAME).is_file():
        st.error(f"`{run_id}` 的运行目录里没有 `{run_input_mod.BINDING_NAME}`：它不是由本页"
                 "「上传三份 PDF → 开始生成」创建的一次运行。本屏只展示**本次上传**产生的运行，"
                 "不回落显示历史 run。")
        return False
    try:
        run_input_mod.load_run_input(_run_input_root() / run_id)
    except run_input_mod.CitedRunInputError as exc:
        st.error(f"`{run_id}` 的上传输入无法读回（fail-closed）：{exc}")
        return False
    return True


def _render_launch_meta(run_id: str) -> None:
    """本次运行的发起面：谁点的、什么模式、上传落在哪、链的日志在哪。"""
    staged = Path(str(st.session_state.get("upload_staged_dir") or (_run_input_root() / run_id)))
    mode_shown = (str(st.session_state.get("upload_mode") or "") or _requested_mode(run_id)
                  or "（请求面不可读）")
    st.markdown(f"**本次运行**　`{run_id}`　·　模式 `{mode_shown}`")
    st.caption(f"上传输入目录 `{staged}`　·　链日志 `{staged / 'chain.log'}`")
    try:
        binding = run_input_mod.load_run_input(staged)
    except run_input_mod.CitedRunInputError as exc:
        st.error(f"上传输入无法读回：{exc}")
        return
    rows = [{
        "来源角色": binding.source_role,
        "登记身份": obj.document_id,
        "上传时的文件名（仅凭据）": obj.uploaded_name,
        "上传字节 SHA-256": obj.declared_sha256,
        "字节": obj.size_bytes,
        "落盘对象": obj.object_relpath,
    } for obj in binding.documents]
    try:
        fin_binding = fin_input_mod.load_financial_input(staged)
    except fin_input_mod.CitedFinancialInputError as exc:
        st.error(f"财务输入无法读回：{exc}")
        return
    rows += [{
        "来源角色": obj.source_role,
        "登记身份": f"{obj.source_version}（快照 {fin_binding.snapshot.snapshot_id}）",
        "上传时的文件名（仅凭据）": obj.uploaded_name,
        "上传字节 SHA-256": obj.declared_sha256,
        "字节": obj.size_bytes,
        "落盘对象": obj.object_relpath,
    } for obj in fin_binding.sources]
    st.markdown(f"**本次上传的 {len(rows)} 份对象**（三份业务 PDF + 三份财务 XLSX，各自一条"
                "类型化绑定）")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("解析一律按内容哈希命中，**文件名不参与查找**；两组绑定分属两个来源角色，"
               "谁都不冒充谁。"
               f"PDF 清单指纹 `{binding.manifest_sha256[:16]}…`；"
               f"财务绑定指纹 `{fin_binding.binding_sha256[:16]}…`（快照 "
               f"`{fin_binding.snapshot.snapshot_id}`，本次**核对并复用**，未重抽取、未写库）。")


def _journal_rows(events: tuple[run_journal.RunProgressEvent, ...]) -> list[dict[str, Any]]:
    return [{"#": event.seq, "阶段": event.stage, "读数": event.label, "状态": event.status,
             "节": event.section_id or "—", "时间（UTC）": event.at_utc,
             "细节": event.detail or "—"} for event in events]


def _render_journal_table(events: tuple[run_journal.RunProgressEvent, ...]) -> None:
    if not events:
        return
    st.dataframe(_journal_rows(events), hide_index=True, width="stretch")


def _require_staged_run(run_id: str) -> bool:
    """「已落盘、可能还没启动」的那一类 run。

    第 2 屏比第 3 屏宽：一个刚被上传创建、还没启动（或被授权门拦下）的 run 也要能看到，
    否则观众在「点了开始生成」与「链写出第一份产物」之间什么都看不到。判据仍是产物侧的
    `cri-1` 清单能重新读回并逐字节复核——历史 run 没有它，因此照样进不来。
    """
    staged = _run_input_root() / run_id
    if not staged.is_dir():
        st.error(f"运行输入目录不存在：`{staged}`。本屏只展示由本页「上传三份 PDF → 开始生成」"
                 "创建过的运行。")
        return False
    try:
        run_input_mod.load_run_input(staged)
    except run_input_mod.CitedRunInputError as exc:
        st.error(f"`{run_id}` 的上传输入无法读回（fail-closed）：{exc}")
        return False
    return True


def _render_authorization_panel(run_id: str, staged: Path, mode: str) -> str:
    """真实模式的一次性授权面板。返回当前读数，供调用方决定要不要给启动入口。"""
    state, detail = _authorization_reading(staged, run_id, mode)
    st.markdown("**一次性运行授权**")
    if state == "granted":
        st.success(detail)
    elif state == "consumed":
        st.info(detail)
    elif state == "absent":
        st.warning(f"**未授权**　{detail}")
        st.code(f"python scripts/authorize_cited_run.py --run-input {staged} "
                f"--granted-by \"<批准人>\"", language="bash")
        st.caption("授权由**人**写（`scripts/authorize_cited_run.py`），页面只读它。"
                   "本页不提供「批准」按钮：一次可追溯的人的动作不该藏在一个 UI 控件后面。")
    else:
        st.error(detail)
    return state


def _spawned_by_this_session(run_id: str) -> bool:
    """这一次运行是不是**本会话**拉起来的（哪怕它还没建出结果目录）。

    它决定第 2 屏该说哪句话。链从「进程起来」到「`run_dir.mkdir`」之间有一两秒，页面在这段
    窗口里重新渲染时，结果目录确实还不存在——但那是「**已经启动、产物尚未落盘**」，不是
    「尚未启动」。把两者画成同一屏不只是读错：那一屏上挂着「启动本次运行」按钮，再点一次会在
    **同一个 run-id** 上拉起第二个进程，而它会以 `"wb"` 打开同一份 `chain.log`、把第一份截断；
    第二个进程随后在 `run_dir.mkdir` 上被拒（create-only），于是页面上留下的正是最不该有的东西
    ——第一份日志没了，第二份什么也没写。

    判据用 `_run_in_flight()`（真的 `poll()` 过），不只是「句柄还在会话里」：一个**已经退出**的
    句柄不该让页面永远说「已启动」——那种情形下它什么也没建出来，读者需要一个能重来的入口。
    """
    return _active_run_id() == run_id and _run_in_flight()


def _render_pending_run(run_id: str, staged: Path, mode: str) -> None:
    """本 run 已落盘、但链还没有写出任何产物的那一段。"""
    if _spawned_by_this_session(run_id):
        st.info(f"**已启动、产物尚未落盘**：本 run 的链已由本页面以 `{mode}` 模式拉起，"
                "但结果目录还没有建立，因此这里暂时读不到阶段事件。这**不**表示它没在跑；"
                "它同样**不是**「可从此处恢复」。本屏不重放、不估算，要读新事件请刷新。")
        if st.button("刷新", key=f"cur_process_refresh_{run_id}"):
            st.rerun()
        return
    st.warning("**尚未启动**：本 run 的上传对象与请求面已落盘，但结果目录还没有建立——"
               "链没有写出任何产物，因此这里**没有**阶段事件可读，也**不是**「可从此处恢复」。")
    if mode != "real":
        if st.button("启动本次运行", key=f"cur_process_start_{run_id}", type="primary"):
            if _spawn_new_run(staged, run_id, mode):
                st.rerun()
        return
    state = _render_authorization_panel(run_id, staged, mode)
    if state == "granted":
        if st.button("启动已授权的运行", key=f"cur_process_start_{run_id}", type="primary"):
            #: 链会**原子地**消费这份授权；按钮点两次时第二次由链拒绝，不是靠这里灰掉。
            if _spawn_new_run(staged, run_id, mode):
                st.rerun()
        st.caption("启动后本 run 的那一份授权即被消费：再点这个按钮、或换一个浏览器会话，"
                   "都消费不动同一份。要再跑一次必须**重新批准**一次新的运行。")
    else:
        st.button("启动已授权的运行", key=f"cur_process_start_{run_id}", type="primary",
                  disabled=True, help="尚无一份逐字段对上的、未被消费的授权。")


def _render_new_run_process() -> None:
    """第二屏：只读**本 run 自己**的阶段日志。不冒充实时监控，也不造恢复点。"""
    st.header("第 2 步 / 共 3 步 · 本次运行的生成过程")
    run_id = _pick_run(key="cur_process_run")
    if run_id is None:
        return
    run_dir = _results_root() / run_id
    if run_dir.is_dir():
        if not _require_bound_run(run_id):
            return
    elif not _require_staged_run(run_id):
        return
    _render_launch_meta(run_id)
    st.divider()
    if not run_dir.is_dir():
        _render_pending_run(run_id, _run_input_root() / run_id,
                            str(st.session_state.get("upload_mode") or ""
                                or _requested_mode(run_id)))
        return

    try:
        events = run_journal.read_run_progress(run_dir)
    except run_journal.CitedRunJournalError as exc:
        st.error(f"阶段日志读不回来：{exc}。页面**不**用一个更短的列表代替它。")
        return
    if not events:
        st.warning("本 run 还没有落盘任何阶段事件：**进度日志未落盘**。"
                   "这既不是「跑完了」，也不是「可从此处恢复」。")
        _render_failure_note(run_dir)
        return

    in_flight = _run_in_flight() and _active_run_id() == run_id
    st.markdown(f"**运行进程**　{'仍在运行' if in_flight else '已结束（或本会话不再持有它的句柄）'}")
    if st.button("刷新进度", key=f"cur_process_refresh_{run_id}"):
        st.rerun()
    _render_ledger_outcome(run_dir)

    total = len(events)
    index_key = f"cur_event_index_{run_id}"
    index = max(0, min(total - 1, int(st.session_state.get(index_key, total - 1))))
    controls = st.columns(5)
    if controls[0].button("⏮ 从头", use_container_width=True):
        st.session_state[index_key] = 0
        st.rerun()
    if controls[1].button("◀ 上一事件", use_container_width=True, disabled=index == 0):
        st.session_state[index_key] = index - 1
        st.rerun()
    play = controls[2].button("▶ 播放", use_container_width=True)
    if controls[3].button("下一事件 ▶", use_container_width=True, disabled=index >= total - 1):
        st.session_state[index_key] = index + 1
        st.rerun()
    if controls[4].button("⏭ 跳到最新", use_container_width=True, disabled=index >= total - 1):
        st.session_state[index_key] = total - 1
        st.rerun()

    def paint(step: int) -> None:
        event = events[step]
        st.markdown(f"#### 事件 {event.seq} / {total} · {event.stage}")
        st.markdown(f"**读数**　{event.label}")
        st.markdown(f"**状态**　`{event.status}`　·　**时间（UTC）**　`{event.at_utc}`")
        if event.section_id:
            st.markdown(f"**节**　`{event.section_id}`")
        if event.detail:
            st.markdown(f"**细节**　{event.detail}")

    if play:
        for step in range(index, total):
            paint(step)
            st.session_state[index_key] = step
            time.sleep(0.4)
        st.rerun()
    else:
        paint(index)

    st.progress((index + 1) / total, text=f"已落盘事件 {index + 1} / {total}")
    st.caption("这条进度条数的是**已经落盘的阶段事件**，不是推算出来的完成百分比；"
               "本页不显示估计耗时，也不显示「正在调用模型」。")
    st.divider()
    _render_journal_table(events)

    failed = [event for event in events if event.status == "failed"]
    if failed:
        last = failed[-1]
        st.error(f"第 {last.seq} 个事件失败：{last.label}。{last.detail}。"
                 "**已完成产物保留在原处，页面不会把它自动转成成功。**")
        _render_failure_note(run_dir)
    st.warning("本 run 的事件里 `resumable` 恒为 `false`、`checkpoint_id` 恒为空："
               "**不可恢复**。Evidence 阶段的数据检查点不是整份报告的恢复点，"
               "不能拿来当作「可从此处继续」。")
    st.caption("「播放」只是按顺序翻已经落盘的事件；它**不**重新执行任何一步，"
               "也不代表每一步在本次会话里又跑了一遍。")


def _render_ledger_outcome(run_dir: Path) -> None:
    """②屏：账本落盘后**明确**写整轮 `run_outcome`。

    **不**用「两节的 `section_emitted` 都是 `completed`」推断整轮完成——那是阶段事件，不是
    整轮结果；本次复现的 run 里两者正好相反（两节都 `section_emitted completed`，而账本记
    `failed`）。账本还没落盘时同样明说，并指出此时**不能**从阶段事件推出完成。
    """
    ledger_path = run_dir / "cited_call_ledger.json"
    if not ledger_path.is_file():
        st.info("调用账本**尚未落盘**：整轮结果还无从读出。此时阶段事件里出现 "
                "`section_emitted completed` **不**等于整轮完成。")
        return
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.error(f"调用账本不可解码：{exc}")
        return
    outcome = str(ledger.get("run_outcome") or "")
    if outcome == "completed":
        st.success("**整轮结果（读自调用账本）**　`run_outcome=completed`")
    elif outcome == "failed":
        failure = ledger.get("failure") if isinstance(ledger.get("failure"), dict) else {}
        detail = " · ".join(str(x) for x in (failure.get("error_type"), failure.get("error"))
                            if x) or "账本没有记下失败细节"
        st.error(f"## ✖ 整轮结果（读自调用账本）　`run_outcome=failed`\n\n{detail}")
    else:
        st.error(f"**整轮结果（读自调用账本）**　`run_outcome={outcome or '未记录'}`："
                 "既不是 `completed` 也不是 `failed`，本页不替它补一个说法。")
    st.caption("这一行读的是 `cited_call_ledger.json` 的**整轮结果**；不由「两节 "
               "`section_emitted completed`」推断，也不由进度条位置推断。")


def _render_failure_note(run_dir: Path) -> None:
    ledger_path = run_dir / "cited_call_ledger.json"
    if not ledger_path.is_file():
        st.caption("调用账本尚未落盘：本 run 还没有走到收尾（或根本没能启动）。")
        return
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.error(f"调用账本不可解码：{exc}")
        return
    st.markdown(f"**调用账本**　运行结果 `{ledger.get('run_outcome')}`　·　模式 "
                f"`{ledger.get('mode')}`")
    failure = ledger.get("failure")
    if isinstance(failure, dict):
        st.error(f"失败记录：`{failure.get('error_type')}` · {failure.get('error')}")
    if ledger.get("note"):
        st.caption(str(ledger["note"]))


def _review_ran_without_opinions(section: Any) -> bool:
    """本 run 发过这次审阅调用、但没有产出可用意见（回复不合约、整批作废）。

    优先读 `UploadRunSection` 自己的派生属性；结构孪生件（历史节、测试替身）没有它时，按
    **同一条判据**现算：「没有意见」+「账本里有这次调用」。两个轴不合并。
    """
    value = getattr(section, "review_ran_without_opinions", None)
    if isinstance(value, bool):
        return value
    return (not bool(getattr(section, "review_available", False))
            and bool(getattr(section, "review_attested", False)))


def _section_review_state_text(section: Any) -> str:
    """一节审阅的**三档**定性，互斥。**调用成功不等于审阅成功**，两档分开写。"""
    if getattr(section, "review_available", False):
        kind = ("真实独立 LLM 审阅" if getattr(section, "review_is_model", False)
                else "离线回声（**不是**真实审阅）")
        attested = ("已记入本 run 账本" if getattr(section, "review_attested", False)
                    else "本 run 账本无此次调用")
        return (f"已产出意见 {len(section.review_issues)} 条 · 产出者 {kind} · {attested}")
    if _review_ran_without_opinions(section):
        code = str(getattr(section, "review_failure", "") or "未记录失败码")
        return (f"**真实审阅调用已发生**（本 run 账本里有这次调用，`status=ok`），"
                f"但回复**不外合约**（`{code}`），**没有有效审阅意见**——"
                f"这不等于「审阅通过」，也不等于「审阅未调用」")
    return "本次**没有**这次审阅调用（本 run 账本里没有它）：审阅未运行"


def _render_run_outcome_banner(view: upload_view.UploadRunView,
                               sections: tuple[tuple[str, str,
                                                   upload_view.UploadRunSection | None], ...]
                               ) -> None:
    """③屏顶部：整轮结果**按账本**写；**不拿** `section_emitted completed` 推断整体完成。"""
    if view.failed:
        failure = view.failure or {}
        detail = " · ".join(str(x) for x in (failure.get("error_type"), failure.get("error"))
                            if x)
        st.error("## ✖ 本次运行**整体失败**（`run_outcome=failed`）\n\n"
                 f"调用账本 `cited_call_ledger.json` 记的就是 `failed`，**不是** `completed`。"
                 f"{detail}。\n\n本次**不宣布**内容可发布，也不宣布任何阶段关闭；"
                 "已经落盘的正文、引用、逐句机械核对与缺口**照常展示**，供排查——"
                 "把它们读成「本次运行完成」是错的。")
    else:
        st.success(f"## 本次运行 `run_outcome={view.run_outcome or '未记录'}`\n\n"
                   "整轮结果**读自调用账本**，不由本节或某一节的阶段事件推断。")
    st.dataframe([{
        "节": label,
        "本节产出": ("未产出" if section is None else
                     f"{section.sentence_count} 句 · report_version "
                     f"`{section.report_version['report_version']}`"),
        "机械标红": ("—" if section is None else
                     f"{len(section.hard_sentence_ids)} / {section.sentence_count} 句"),
        "审阅": ("—" if section is None else _section_review_state_text(section)),
        "发布资格": ("—" if section is None
                     else str(section.report_version["publishability"])),
        "人工状态": ("—" if section is None
                     else str(section.report_version["human_review_state"])),
    } for label, _sid, section in sections], hide_index=True, width="stretch")
    st.caption("「机械标红」逐句现读自本节 `sentence_checks.json`（事实安全族 ∪ 栏目覆盖族），"
               "**不是**放行阻断子集，也不拿它的数字推算任何别的结论。")


def _render_isolated(label: str, render: Any) -> None:
    """一个标签页独立渲染：**这一块**坏了不连坐其它块。

    Streamlit 的 `st.tabs` 在同一次脚本运行里依次执行**每一个** panel，任何一个 panel 抛异常
    都会 abort 掉本次运行里其余 panel 的渲染。这里把每一块各自包一层：出错时把**具名的错误**
    显示在**这一块**里，其余块照常渲染。

    这**不是**把坏产物伪装成零意见——错误仍然显著出现；区别只是不再让一块的缺陷抹掉另外
    两块已经落盘、可读的正文与读数。
    """
    try:
        render()
    except Exception as exc:  # noqa: BLE001 - 这里要的正是「拦截并**如实显示**」，不是吞掉
        st.error(f"## ✖ 这一块渲染失败（其它块不受影响）\n\n"
                 f"`{type(exc).__name__}`：{exc}")
        st.caption("本页不把这一块静默跳过、也不拿别的内容顶替它；"
                   "上面这条错误就是它的真实读数。")


def _render_new_run_deliverable() -> None:
    """第三屏：只加载**本 run** 的产物。公司 A1、财务 A1、财务 A2 三块并排，各自四轴分开写。"""
    st.header("第 3 步 / 共 3 步 · 本次运行的报告与审核")
    run_id = _pick_run(key="cur_deliverable_run")
    if run_id is None or not _require_bound_run(run_id):
        return
    results_root = _results_root()
    try:
        view = upload_view.load_upload_run(run_id, results_root=results_root,
                                           run_input_root=_run_input_root(),
                                           section_ids=_NEW_RUN_SECTIONS)
    except upload_view.UploadRunIncomplete as exc:
        _render_incomplete_run(run_id, exc)
        return
    except upload_view.CitedUploadViewError as exc:
        _render_run_integrity_failure(run_id, exc)
        return

    company = view.sections.get("company")
    financial = view.sections.get("financial")
    sections = tuple((("公司节（主营业务）" if sid == "company" else "财务节"), sid,
                      view.sections.get(sid)) for sid in _NEW_RUN_SECTIONS)
    _render_run_outcome_banner(view, sections)
    report_column, audit_column = st.columns([3, 1], gap="large")
    with report_column:
        st.header("授信报告 · 本次运行（不可发布预览）")
        st.caption(f"本屏的正文、引用、缺口、机械核对与审阅读数**全部**来自本次运行 "
                   f"`{view.run_id}`，没有一条来自别的 run。三块（公司 A1、财务 A1、财务 A2）"
                   "各自成套，四条状态轴也各自分开写，不互相推导。"
                   "四个标签页彼此隔离：一块渲染失败**不**使其余块空白。")
        _render_artifact_triplet(view, company, financial)
        tabs = st.tabs(["一、主营业务 A1 正文（本次生成）", "二、财务分析 A1 正文（本次生成）",
                        "三、财务分析 A2 结构（确定性呈现）",
                        f"附：原始 PDF 表格 {len(view.source_regions)} 区"])

        def _tab_company() -> None:
            if company is None:
                st.error("本节**没有产出**：本屏如实说没有，不拿别的内容顶替。")
                return
            st.error(f"本节共 {company.sentence_count} 句：机械层标红 "
                     f"{len(company.hard_sentence_ids)} 句（事实安全 "
                     f"{len(company.fact_safety_sentence_ids)} 句、栏目覆盖待修 "
                     f"{len(company.column_coverage_sentence_ids)} 句）。"
                     "带标记的草稿可以阅读，但**不可发布**。")
            st.info(f"本节审阅：{_section_review_state_text(company)}")
            _render_a1(company, company.review_attested)
            _render_section_artifacts(view, company)

        def _tab_financial_a1() -> None:
            if financial is None:
                st.error("本节**没有产出**：本屏如实说没有，不拿别的内容顶替。")
                return
            st.error(f"本节共 {financial.sentence_count} 句：机械层标红 "
                     f"{len(financial.hard_sentence_ids)} 句（事实安全 "
                     f"{len(financial.fact_safety_sentence_ids)} 句、栏目覆盖待修 "
                     f"{len(financial.column_coverage_sentence_ids)} 句）。"
                     "带标记的草稿可以阅读，但**不可发布**。")
            st.info(f"本节审阅：{_section_review_state_text(financial)}")
            _render_a1(financial, financial.review_attested)
            _render_section_artifacts(view, financial)

        def _tab_financial_a2() -> None:
            if financial is None:
                st.error("本节**没有产出**，因此也没有 A2 结构产物。")
                return
            _render_balance(financial)
            st.caption("A2 与 A1 是两条不同的产出路径：A2 用权威财务事实与 Decimal "
                       "确定性计算，零模型调用；它**不**经过 A1 的逐句写作，也**不**进入"
                       "A1 的逐句语义审阅。上面的状态表把它们分开列。"
                       "A2 的放行与人工状态**不得**拿公司 A1 或财务 A1 的审阅读数顶替。")

        for tab, label, render in ((tabs[0], "公司 A1 正文", _tab_company),
                                   (tabs[1], "财务 A1 正文", _tab_financial_a1),
                                   (tabs[2], "财务 A2 结构", _tab_financial_a2),
                                   (tabs[3], "原始 PDF 表格回查",
                                    lambda: _render_source_regions(view))):
            with tab:
                _render_isolated(label, render)
    with audit_column:
        _render_new_run_rail(view)


def _artifact_state_rows(view: upload_view.UploadRunView, *,
                         section: upload_view.UploadRunSection | None,
                         label: str, include_a2: bool) -> list[dict[str, str]]:
    """一块产物的四条状态轴，**分开**列；缺产物时每一格都写「未产出」，不推出一条结论。"""
    if section is None:
        return [{"产物": label, "状态轴": axis, "读数": "本节未产出"}
                for axis in ("机械核对", "审阅", "系统放行", "人工状态")]
    hard = len(section.hard_sentence_ids)
    version = section.report_version
    rows = [
        {"产物": label, "状态轴": "机械核对",
         "读数": f"{hard} / {section.sentence_count} 句硬错"},
        {"产物": label, "状态轴": "审阅",
         "读数": _section_review_state_text(section)},
        {"产物": label, "状态轴": "系统放行",
         "读数": str(version["system_review_state"])},
        {"产物": label, "状态轴": "人工状态",
         "读数": str(version["human_review_state"])},
    ]
    if include_a2:
        a2 = section.balance_structure
        rows.append({"产物": label, "状态轴": "A2 结构",
                     "读数": ("未产出" if a2 is None else
                              f"已产出 · 零模型调用 {a2.get('model_calls_issued')} 次 · "
                              f"指纹 {(a2.get('fingerprint') or '')[:16]}…")})
    return rows


def _render_artifact_triplet(view: upload_view.UploadRunView,
                             company: upload_view.UploadRunSection | None,
                             financial: upload_view.UploadRunSection | None) -> None:
    """三块产物并排：公司 A1、财务 A1、财务 A2，各自四条状态轴**分开**列。"""
    a1_rows = (_artifact_state_rows(view, section=company, label="公司 A1", include_a2=False)
               + _artifact_state_rows(view, section=financial, label="财务 A1", include_a2=True))
    a2 = financial.balance_structure if financial is not None else None
    a2_totals = _a2_gap_totals(a2)
    a2_rows = [{
        "产物": "财务 A2",
        "状态轴": "确定性呈现",
        "读数": ("未产出" if a2 is None else
                 f"{a2_totals['items']} 条结构项 · {a2_totals['readings']} 条读数 · 模型调用 "
                 f"{a2.get('model_calls_issued')} 次 · 项目级 typed 缺口 "
                 f"{a2_totals['item_gaps']} 条 · notes 缺口注记 {a2_totals['note_gaps']} 条 · "
                 f"产出者 `{a2.get('producer_kind')}`"),
    }, {
        "产物": "财务 A2", "状态轴": "逐句审阅",
        "读数": "不适用：A2 不是 Writer 逐句正文，不进入 A1 的逐句语义审阅",
    }, {
        "产物": "财务 A2", "状态轴": "系统放行",
        "读数": "未取得可核验放行结果",
    }, {
        "产物": "财务 A2", "状态轴": "人工状态",
        "读数": str(financial.report_version["human_review_state"]) if financial else "本节未产出",
    }]
    st.markdown("#### 三块产物 · 四条状态轴分开列")
    st.dataframe(a1_rows + a2_rows, hide_index=True, width="stretch")
    st.caption("「有审阅意见」不等于「审阅通过」，「本节有意见」也不等于「报告级已审」；"
               "四条轴彼此独立，本页不从任何一条推出另一条。")


def _render_incomplete_run(run_id: str, exc: Exception) -> None:
    """产物还没齐：如实说「还没齐」，并把已经落盘的阶段与失败原因摆出来。"""
    st.warning(f"本次运行 `{run_id}` 尚未产出完整结果：{exc}")
    run_dir = _results_root() / run_id
    try:
        events = run_journal.read_run_progress(run_dir)
    except run_journal.CitedRunJournalError as journal_exc:
        st.error(f"阶段日志读不回来：{journal_exc}")
        events = ()
    if events:
        _render_journal_table(events)
        st.markdown(f"**最后一个已落盘事件**　`{events[-1].stage}` · {events[-1].at_utc}")
    _render_failure_note(run_dir)
    st.caption("运行中途页面会一直读到这个状态；已经落盘的产物保留在原处。"
               "页面不把半成品当作完成品，也不会因为它「写了几个文件」就报成功。")


def _render_run_integrity_failure(run_id: str, exc: Exception) -> None:
    """`run_outcome=completed` 却有节或 A2 缺失：**显著**失败，不跳过、不画成双节成功。"""
    st.error(f"## ✖ 本次运行**不完整**，不得读作双节成功\n\n`{run_id}`：{exc}")
    st.caption("账本记 `completed`，但请求的节或财务 A2 没有齐。本页**不**跳过缺失的那一块去"
               "显示另一块的成功，也不回落显示历史 run。已经落盘的部分保留在原处，"
               "供排查；但它们不构成本次运行的完整产出。")
    _render_failure_note(_results_root() / run_id)


def _render_section_artifacts(view: upload_view.UploadRunView,
                              section: upload_view.UploadRunSection) -> None:
    """本节产物的身份表——每一条都能在运行目录里指得出文件名。"""
    version = section.report_version
    st.markdown("#### 本节产物身份")
    st.dataframe([{
        "report_version": version["report_version"],
        "Writer 清单": section.manifest["manifest_id"],
        "有效草稿": section.draft["draft_id"],
        "有效稿来源文件": section.draft_source,
        "逐句核对": section.checks["report_id"],
        "独立审阅输入": section.review.get("bundle_id") or "未产出",
        "原 PDF 展示集": section.source_display["display_set_id"],
    }], hide_index=True, width="stretch")
    st.caption(f"有效稿由 `report_version` 绑定的 `draft_id` **指定**：本次命中 "
               f"`{section.draft_source}`（另一份草稿若存在，只是留档，不是读者面那一稿）。")
    if section.readback_markdown:
        with st.expander("本节读回自述（`readback.md`，逐字）", expanded=False):
            st.markdown(section.readback_markdown)
    with st.expander("本次运行的文件完整性", expanded=False):
        st.json(view.file_hashes, expanded=False)


def _render_new_run_rail(view: upload_view.UploadRunView) -> None:
    """右栏：本次运行的审核与状态声明。**逐节**六轴分开写，不互相推导，也不跨节合并。"""
    st.markdown("### 本次运行的审核与状态")
    st.error("不可发布 · 未经人工接受")
    missing = [sid for sid in _NEW_RUN_SECTIONS if sid not in view.sections]
    if missing:
        st.error("请求的节**没有全部产出**：" + "、".join(missing)
                 + "。本页不把已产出的那一节读成双节成功。")
    for section_id in _NEW_RUN_SECTIONS:
        section = view.sections.get(section_id)
        title = "公司节（主营业务）" if section_id == "company" else "财务节"
        st.divider()
        st.markdown(f"**{title}**　`{section_id}`")
        if section is None:
            st.warning("本节未产出，因此没有任何本节状态可读。")
            continue
        version = section.report_version
        st.dataframe([
            {"状态轴": "流程", "读数": version["process_state"]},
            {"状态轴": "预览", "读数": version["preview_state"]},
            {"状态轴": "机械核对",
             "读数": f"{len(section.hard_sentence_ids)} / {section.sentence_count} 句硬错"},
            {"状态轴": "系统审核", "读数": version["system_review_state"]},
            {"状态轴": "人工接受", "读数": version["human_review_state"]},
            {"状态轴": "发布资格", "读数": version["publishability"]},
        ], hide_index=True, width="stretch")
        producer = getattr(section, "writer_producer", upload_view.PRODUCER_UNVERIFIABLE)
        if producer == upload_view.PRODUCER_MODEL:
            st.success("A1 正文：**真实模型产出** —— 写作调用记录、调用日志的输入策略与"
                       "本 run 成功账本里同一 `call_id`／节／类别／模型／成功状态三处一致。")
        elif producer == upload_view.PRODUCER_STANDIN:
            st.warning("A1 正文：**离线替身产出**，只证明接线接通，**不**代表真实写作质量。")
        else:
            st.error("A1 正文：**产出者无法核实** —— 写作调用记录、调用日志、本 run 账本三处"
                     "证据缺失或互相矛盾，本页**不**把它写成「真实模型」也**不**写成"
                     "「离线替身」。以下正文按不可发布草稿阅读。")
        if section.review_is_model:
            st.success("A1 逐句审阅：真实独立 LLM 审阅已产出")
        elif section.review_available:
            st.warning("A1 逐句审阅：离线回声产出，**不是**真实审阅。")
        elif _review_ran_without_opinions(section):
            #: **调用成功 ≠ 审阅成功**：这一档最容易被误读成「没跑」或「通过了」，两条都写死。
            st.error(f"A1 逐句审阅：**真实审阅调用已发生**（本 run 账本里有这次调用，"
                     f"`status=ok`），但**回复不外合约**"
                     f"（`{getattr(section, 'review_failure', '') or '未记录失败码'}`），"
                     f"**没有产出任何有效审阅意见**。这不等于「审阅通过」，"
                     f"也不等于「审阅未调用」。")
        else:
            st.warning("A1 逐句审阅：本次**没有**产出审阅意见。")
        if _review_ran_without_opinions(section):
            diagnostic = getattr(section, "review_diagnostic", None)
            if isinstance(diagnostic, dict):
                with st.expander("审阅回复在哪里不合约（诊断原文，逐字）", expanded=False):
                    st.json(diagnostic, expanded=True)
            st.caption("诊断归诊断：本次真实审阅**没有**给出可用意见，因此本节在审阅这一轴上"
                       "没有任何可读的通过结论。")
        elif not section.review_attested:
            st.error("**本次真实审阅未运行**：本 run 的调用账本里没有这次审阅调用，"
                     "因此「审阅已通过」在本次运行里无从成立。")
        if section.review_available:
            supported = sum(issue["category"] == "supported"
                            for issue in section.review_issues)
            st.caption(f"留存意见 {len(section.review_issues)} 条（自报 supported "
                       f"{supported} 条）；审阅给 supported 也**不能**抵消机械硬错。")
        if section_id == "financial":
            a2 = section.balance_structure
            st.markdown("**A2 确定性结构**")
            if a2 is None:
                st.error("本次**没有**产出 A2 结构产物。")
            else:
                totals = _a2_gap_totals(a2)
                st.caption(f"{totals['items']} 条结构项 · {totals['readings']} 条读数 · 模型调用 "
                           f"{a2.get('model_calls_issued')} 次 · 产出者 `{a2.get('producer_kind')}`"
                           f" · 指纹 `{(a2.get('fingerprint') or '')[:16]}…`")
                st.warning(f"项目级 typed 缺口 **{totals['item_gaps']}** 条、notes 缺口注记 "
                           f"**{totals['note_gaps']}** 条。**「零模型调用」不是「零缺口」**，"
                           "也不表示应收附注案例已经做完；逐条原因见下列展开。")
                if totals["item_gaps"]:
                    with st.expander(f"A2 逐项缺口原因（{totals['item_gaps']} 条）",
                                     expanded=False):
                        st.dataframe(_a2_gap_rows(a2), hide_index=True, width="stretch")
                st.caption("A2 零模型调用、不进 A1 的逐句语义审阅；它的放行与人工状态另计。")

    st.divider()
    st.markdown("**报告级审阅（M930-4）**")
    st.error("**未审**：本 run 没有报告级审阅读数。本页不从本节审阅意见推出一份报告级结论，"
             "也不套用任何历史 run 的报告级侧车。")
    st.caption("「本节有审阅意见」与「整份报告已审」是两件事；人工接受与系统放行另计，"
               "本页也不代替任何一次授权。")

    st.divider()
    st.markdown("**本次上传被本次运行读过吗**")
    if view.run_input_verified:
        st.success("业务 PDF：已证明上传字节 == 本 run 实读对象")
        st.caption(view.run_input_note)
    else:
        st.error(f"业务 PDF：未能证明 —— {view.run_input_note}")
    if view.financial_input_verified:
        st.success("财务 XLSX：已证明上传字节 == 快照 source_versions 的同一批对象")
        st.caption(view.financial_input_note)
    else:
        st.error(f"财务 XLSX：未能证明 —— {view.financial_input_note}")

    st.divider()
    confirmed = sum(region["confirmation_state"] == "human_confirmed"
                    for region in view.source_regions)
    st.markdown(f"**原 PDF 区域人工确认**　{confirmed} / {len(view.source_regions)}")
    st.caption("这些图只作来源回查，不授权正文数字，也不是合格结构化表；本页没有确认按钮。")

    st.divider()
    st.caption("历史 run 的并列基线只出现在「历史对照 · 非本次生成」区，且**不**算作本次"
               "运行的结果。正式阶段门、TS5 与内容门均未关闭；本页不作治理裁决。")


def _render_historical(binding: binding_mod.CitedDisplayBinding | None, binding_error: str,
                       results_root: Path) -> None:
    """历史对照区：上批的并列展示基线原样保留，**只**作历史记录出现。"""
    st.header("历史对照 · 非本次生成")
    st.error("本区展示的是两个**已有产物**（展示绑定 `cdb-1`），**不是**本次上传运行的结果。"
             "它们不参与第 2、3 屏的任何读数，也不得被读成本次生成的正文或审阅结论。")
    if binding is None:
        st.error(f"历史展示绑定无法建立：{binding_error}")
        st.caption("绑定按字节哈希钉住两个来源；任一文件被改写、换版或缺失都会走到这里——"
                   "不会降级成「仍然可以看」。本区不显示，也不影响第 2、3 屏读本次运行。")
        return
    if "cited_history_view" not in st.session_state:
        st.session_state["cited_history_view"] = _HISTORY_VIEWS[3]
    picked = st.radio("历史对照", _HISTORY_VIEWS, horizontal=True, key="cited_history_view")
    if picked == _HISTORY_VIEWS[0]:
        _render_upload(binding)
    elif picked == _HISTORY_VIEWS[1]:
        _render_process(binding)
    elif picked == _HISTORY_VIEWS[2]:
        _render_deliverable(binding)
    else:
        _render_audit_detail(binding, str(results_root))


def _render_document_rows(binding: binding_mod.CitedDisplayBinding) -> None:
    """已保存案例的三份来源与它们在 Writer 清单里的登记读数。"""
    rows = []
    for row in binding_mod.document_role_summary(binding.company):
        rows.append({
            "来源文件": row["filename"],
            "登记文档": row["document_id"],
            "本次角色": row["role"],
            "登记材料数": row["materials"],
            "产物登记的来源角色": row["source_roles"],
            "产物自述哈希前缀": f"sha256-{row['manifest_hash_prefix']}",
            "案例完整 SHA-256": row["sha256"],
        })
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("「产物自述哈希前缀」取自 Writer 清单里 `locator_ref.owner` 自己写下的来源身份"
               "（`evidence_document:<文档>@sha256-<12 位>`）；「案例完整 SHA-256」是本次"
               "从磁盘实测的完整值。两者必须是同一个文档的同一串字节。")


def _render_upload(binding: binding_mod.CitedDisplayBinding) -> None:
    """历史对照区的「材料与上传」屏：只在内存里算哈希，不写库、不启动研究、不发请求。"""
    st.header("第 1 步 / 共 3 步 · 新建授信报告")
    st.info("这一屏只用**内存**核对材料：算 SHA-256、比对已保存案例，然后允许查看已存回放。"
            "不写入研究库、不建立索引、不启动研究，也不发任何模型或网络请求。")

    left, right = st.columns([2, 1], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("#### ① 借款主体")
            st.text_input("证券代码", value="300750", disabled=True,
                          help="来自已保存案例目录 `data/samples/300750/`。")
            st.text_input("企业全称", value="", placeholder="冻结产物未登记主体全称",
                          disabled=True)
            st.caption(_SUBJECT_NOTE)

        with st.container(border=True):
            st.markdown("#### ② 授信类型")
            st.radio("授信类型", _CREDIT_TYPE_OPTIONS, index=0,
                     key="cdb_credit_type", horizontal=True)
            st.caption(_CREDIT_NOTE)

        with st.container(border=True):
            st.markdown("#### ③ 拟定授信方案")
            cols = st.columns(3)
            cols[0].text_input("授信金额（万元）", key="cdb_amount", placeholder="未绑定")
            cols[1].text_input("授信期限（月）", key="cdb_term", placeholder="未绑定")
            cols[2].text_input("增信措施", key="cdb_guarantee", placeholder="未绑定")
            st.caption(_CREDIT_NOTE)

        with st.container(border=True):
            st.markdown("#### ④ 上传材料（三份电子 PDF）")
            uploads = st.file_uploader(
                "选择本案例的三份来源 PDF", type=["pdf"],
                accept_multiple_files=True, key="cdb_uploader")
            payload = [(getattr(f, "name", "未命名"), f.getvalue()) for f in (uploads or [])]
            verification = binding_mod.verify_source_documents(payload)
            st.dataframe([{
                "案例文件": v.filename,
                "你选择的文件": v.uploaded_name or "未匹配",
                "案例 SHA-256": v.declared_sha256,
                "本次内存哈希": v.uploaded_sha256 or "—",
                "字节": v.size_bytes,
                "逐个核对": "一致" if v.matches else "不一致",
            } for v in verification.verdicts], hide_index=True, width="stretch")
            if verification.unexpected:
                st.warning("以下文件不在本案例的三份来源里，未被采信："
                           + "、".join(verification.unexpected))
            if verification.accepted:
                st.success(verification.message)
            else:
                st.error(verification.message)
                st.caption("哈希不一致的候选**可能只是同名文件**。本页拒绝进入该案例，"
                           "也不提供「跳过校验继续」的入口。")

    with right:
        st.markdown("### 本次范围")
        st.markdown("**导入的已存运行**（两个不同 run）")
        st.markdown(f"- 公司节 `{binding_mod.COMPANY_SOURCE.run_id}`")
        st.markdown(f"- 财务 A2 `{binding_mod.FINANCIAL_SOURCE.run_id}`")
        st.divider()
        st.markdown("### 运行方式")
        st.markdown("**只读回放**　不现场重新生成")
        st.caption("两个来源的产物都按字节哈希钉住；任一文件被改写或换版，本页拒绝展示。")
        st.divider()
        st.markdown("### 材料核对")
        if verification.accepted:
            st.success("3 / 3 一致")
        else:
            st.error(f"{len(verification.verdicts) - len(verification.missing)} / "
                     f"{len(verification.verdicts)} 一致")
        st.divider()
        st.button("查看已存运行过程 →", type="primary", use_container_width=True,
                  disabled=not verification.accepted,
                  on_click=lambda: st.session_state.update(
                      {"cited_history_view": _HISTORY_VIEWS[1]}))
        st.button("直接阅读报告与审核 →", use_container_width=True,
                  disabled=not verification.accepted,
                  on_click=lambda: st.session_state.update(
                      {"cited_history_view": _HISTORY_VIEWS[2]}))
        if not verification.accepted:
            st.caption("三份材料全部匹配后才能进入；这是**查看已存产物**，"
                       "不是现场重新生成。")


def _company_nodes(binding: binding_mod.CitedDisplayBinding) -> list[dict[str, str]]:
    c = binding.company
    return [
        {"保存节点": "来源材料送达 Writer", "来源 run": binding_mod.COMPANY_SOURCE.run_id,
         "可回查身份": str(c.manifest["manifest_id"]),
         "实际读数": f"3 份 PDF · {len(c.manifest['materials'])} 份材料 · "
                 f"{len(c.manifest['facts'])} 条事实"},
        {"保存节点": "Writer 有效正文", "来源 run": binding_mod.COMPANY_SOURCE.run_id,
         "可回查身份": str(c.report_version["draft_id"]),
         "实际读数": f"{c.sentence_count} 句；有效稿 = `cited_prose_reworked.json`"},
        {"保存节点": "确定性逐句核对", "来源 run": binding_mod.COMPANY_SOURCE.run_id,
         "可回查身份": str(c.checks["report_id"]),
         "实际读数": (f"{len(c.fact_safety_sentence_ids)} 句事实安全硬错 · "
                  f"{len(c.column_coverage_sentence_ids)} 句栏目覆盖待修")},
        {"保存节点": "独立 LLM 审阅（逐句）", "来源 run": binding_mod.COMPANY_SOURCE.run_id,
         "可回查身份": str(c.review["bundle_id"]),
         "实际读数": f"{len(c.review['issues'])} 条留存意见；不覆盖财务 A2"},
        {"保存节点": "报告预览", "来源 run": binding_mod.COMPANY_SOURCE.run_id,
         "可回查身份": str(c.report_version["report_version"]),
         "实际读数": "`draft_previewable` / `not_publishable`"},
        {"保存节点": "原 PDF 表格区域展示集", "来源 run": binding_mod.COMPANY_SOURCE.run_id,
         "可回查身份": str(c.source_display["display_set_id"]),
         "实际读数": f"{len(c.source_regions)} 个区域；人工确认 "
                 f"{sum(r['confirmation_state'] == 'human_confirmed' for r in c.source_regions)} 个"},
    ]


def _financial_nodes(binding: binding_mod.CitedDisplayBinding) -> list[dict[str, str]]:
    f = binding.financial
    balance = f.balance_structure
    return [
        {"保存节点": "财务权威快照与路由表", "来源 run": binding_mod.FINANCIAL_SOURCE.run_id,
         "可回查身份": str(balance["routing_version"]),
         "实际读数": str(balance["fact_scope"])},
        {"保存节点": "A2 确定性呈现产物", "来源 run": binding_mod.FINANCIAL_SOURCE.run_id,
         "可回查身份": f"`{f.fingerprint[:24]}…`",
         "实际读数": (f"{len(f.items)} 栏 · {f.reading_count} 条逐条读数 · "
                  f"{f.gap_count} 条 typed 缺口")},
        {"保存节点": "A2 产物政策与 schema", "来源 run": binding_mod.FINANCIAL_SOURCE.run_id,
         "可回查身份": str(balance["policy_version"]),
         "实际读数": (f"schema `{balance['schema_version']}` · 产出者 "
                  f"`{balance['producer_kind']}` · 模型调用 "
                  f"{balance['model_calls_issued']} 次")},
        {"保存节点": "财务节 A1 版本记录（对照，不在本次并列展示内）",
         "来源 run": binding_mod.FINANCIAL_SOURCE.run_id,
         "可回查身份": str(f.report_version["report_version"]),
         "实际读数": "A2 的改善**不**等于 A1 已写好；A1 未纳入本屏"},
    ]


_NODE_SOURCES = {
    "company": ("公司节 · ndc5", _company_nodes),
    "financial": ("财务 A2 · dual_v2_r1", _financial_nodes),
}


def _render_process(binding: binding_mod.CitedDisplayBinding) -> None:
    """第二屏：生成过程。节点全部取自已保存产物，不冒充实时监控或可恢复 checkpoint。"""
    st.header("生成过程 · 已存运行过程回放")
    st.warning("本屏是**产物提交后的只读回放**，不是实时任务监控。两个来源来自**不同 run**，"
               "下面按来源分别列出，不画成一次联合运行的单一时间线。")
    st.caption("当前没有与同一运行身份绑定的 `ProgressEvent` 或可恢复 `checkpoint_id`，"
               "因此**不显示**实时百分比、耗时、正在调用模型或「可从此处恢复」。"
               "下表每一项都是**保存节点**。")

    key = "cdb_process_source"
    picked = st.radio("选择来源", list(_NODE_SOURCES),
                      format_func=lambda k: _NODE_SOURCES[k][0],
                      index=0, horizontal=True, key=key)
    label, builder = _NODE_SOURCES[picked]
    nodes = builder(binding)
    total = len(nodes)
    index_key = f"cdb_node_index_{picked}"
    index = int(st.session_state.get(index_key, 0))
    index = max(0, min(total - 1, index))

    controls = st.columns(5)
    if controls[0].button("⏮ 从头", use_container_width=True):
        st.session_state[index_key] = 0
        st.rerun()
    if controls[1].button("◀ 上一节点", use_container_width=True, disabled=index == 0):
        st.session_state[index_key] = index - 1
        st.rerun()
    play = controls[2].button("▶ 播放", use_container_width=True)
    if controls[3].button("下一节点 ▶", use_container_width=True, disabled=index >= total - 1):
        st.session_state[index_key] = index + 1
        st.rerun()
    if controls[4].button("⏭ 跳到结果", use_container_width=True, disabled=index >= total - 1):
        st.session_state[index_key] = total - 1
        st.rerun()

    st.progress((index + 1) / total,
                text=f"回放位置 {index + 1} / {total} · 来源 {label}")

    table = st.empty()
    current = st.empty()

    def paint(i: int) -> None:
        rows = [{"序号": n + 1, **node, "回放位置": "◀ 当前" if n == i else ""}
                for n, node in enumerate(nodes)]
        table.dataframe(rows, hide_index=True, width="stretch")
        node = nodes[i]
        with current.container():
            st.markdown(f"#### 当前节点 {i + 1} / {total} · {node['保存节点']}")
            st.markdown(f"**来源 run**　`{node['来源 run']}`")
            st.markdown(f"**可回查身份**　{node['可回查身份']}")
            st.markdown(f"**实际读数**　{node['实际读数']}")

    if play:
        for step in range(index, total):
            paint(step)
            st.session_state[index_key] = step
            time.sleep(0.7)
        st.rerun()
    else:
        paint(index)

    st.caption("「播放」只是按顺序翻已保存的产物节点；它**不**重新执行任何一步，"
               "也不代表每一步在本次会话里又跑了一遍。")


def _render_binding_rail(binding: binding_mod.CitedDisplayBinding) -> None:
    """右栏：始终可见的审核与状态声明。它与左侧内容同屏，永远不被正文顶掉。"""
    company = binding.company
    financial = binding.financial
    st.markdown("### 审核与状态声明")
    st.error("并列演示基线 · 不可发布 · 未经人工接受")
    st.caption("这是对**两个已有产物**的状态声明，不是授信审批结论，也不是新的 `report_version`。")
    st.markdown("**并列来源**　两个**不同 run** 的产物并列展示")
    st.markdown(f"- 公司节　`{binding_mod.COMPANY_SOURCE.run_id}`")
    st.markdown(f"　　`{company.report_version['report_version']}`")
    st.markdown(f"- 财务 A2　`{binding_mod.FINANCIAL_SOURCE.run_id}/financial`")
    st.markdown(f"　　`{financial.fingerprint[:20]}…`")
    st.divider()
    st.markdown("**逐句机械核对（公司节）**")
    st.markdown(f"事实安全硬错 **{len(company.fact_safety_sentence_ids)} / "
                f"{company.sentence_count}** 句")
    st.markdown(f"栏目覆盖待修 **{len(company.column_coverage_sentence_ids)}** 句")
    st.caption("栏目覆盖待修不构成事实安全阻断，但**不计入**所声明栏目的覆盖。"
               "机械层标红共 "
               f"{len(company.hard_sentence_ids)} 句。"
               "结构化缺口 `total_gap_count = "
               f"{company.gap_bins['axes'].get('total_gap_count', 0)}` "
               "**不等于**「全部栏目已覆盖」。")
    st.divider()
    st.markdown("**财务 A2**")
    st.caption("确定性呈现（模型调用 0 次）。**A2 不是财务节 Writer 正文 A1**；"
               "A2 的改善不等于 A1 已经写好，应收附注案例仍未完成。")
    if binding.a2_review is None:
        st.warning("A2 未审：读不到可核验的既有审阅读数。不得据此认定已审阅。")
    elif binding.a2_review.verified:
        st.success("A2 独立内容身份核对通过")
        st.caption(f"从当前 A2 产物重算的内容版本 `"
                   f"{binding.a2_review.recomputed_scope_version}` 与既有读数逐字相同"
                   f"（读数来自 `{binding.a2_review.sidecar_label}`）。仅单列这一份 A2 审阅；"
                   "它不代表整份报告被审阅。")
    else:
        st.error("A2 未审：既有读数绑的是另一份 A2 内容")
        st.caption(binding.a2_review.reason)
    st.divider()
    st.warning("本组合**未**重新进行报告级审阅")
    st.caption("旧 M930-4 侧车绑定的是 `dual_v2_r1`。它的跨节合读与未用材料选择性遗漏"
               "**不适用**于 ndc5 公司正文；那些意见只在「审计详情」里标为原 run 历史记录展示。")
    confirmed = sum(r["confirmation_state"] == "human_confirmed"
                    for r in company.source_regions)
    st.markdown(f"**原 PDF 区域人工确认**　{confirmed} / {len(company.source_regions)}")
    st.caption("图片只作来源回查，不授权正文数字，也不是合格结构化表。")
    st.divider()
    st.caption("正式阶段门、TS5 与内容门均未关闭；本页不作治理裁决。")


def _render_deliverable(binding: binding_mod.CitedDisplayBinding) -> None:
    report_column, audit_column = st.columns([3, 1], gap="large")
    with report_column:
        st.header("授信报告 · 并列演示基线（不可发布）")
        st.caption("左侧约四分之三为正文、财务 A2 与原 PDF 回查；右侧约四分之一始终显示"
                   "审核声明。公司节来自 `ndc5`，财务 A2 来自 `dual_v2_r1/financial`。")
        company = binding.company
        tabs = st.tabs(["一、主营业务", "二、财务分析（A2）",
                        f"附：原始表格 {len(company.source_regions)} 区"])
        with tabs[0]:
            st.error(f"本节目录来自 `{binding_mod.COMPANY_SOURCE.run_id}`："
                     f"{company.sentence_count} 句中 **{len(company.fact_safety_sentence_ids)} 句"
                     f"事实安全硬错**、另 **{len(company.column_coverage_sentence_ids)} 句"
                     "栏目覆盖待修**。这不是旧双节 run 的那份公司正文。")
            #: `review_attested=True` 说的是「这份逐句审阅是同 run 自己账本里那次调用」，
            #: 由绑定层按 ndc5 的 `cited_call_ledger.json` 核过；它**不**表示本组合被审阅。
            _render_a1(company, company.review_call_attested)
        with tabs[1]:
            st.caption(f"本栏只展示 `{binding_mod.FINANCIAL_SOURCE.run_id}/financial` 的 "
                       "A2 确定性呈现；财务节 Writer 正文 A1 不在本次并列展示范围内。")
            _render_balance(binding.financial)
            with st.expander("A2 产物原文（逐字，`"
                             f"{binding_mod.A2_MARKDOWN_FILENAME}`）", expanded=False):
                st.markdown(binding.financial.markdown)
        with tabs[2]:
            _render_source_regions(company)
    with audit_column:
        _render_binding_rail(binding)


def _render_historical_audit(results_root: str) -> None:
    """原本 run 的历史审计区。旧意见只在这里出现，且标明属于哪一份 run。"""
    st.subheader("原 run 历史审计（`m930_3_cited_real_dual_v2_r1`）")
    st.warning("以下全部读数属于**原本 run 的原始交付**，未套用到本次并列展示，"
               "也**未**为本次组合重新计算。本区不是本次组合的审阅结论。")
    if demo_loader is None:  # pragma: no cover - 仅为可读性保留
        return
    try:
        run = demo_loader.load_cited_run(binding_mod.FINANCIAL_SOURCE.run_id,
                                         results_root=results_root)
    except demo_loader.CitedDemoLoadError as exc:
        st.error(f"原本 run 无法安全加载：{exc}")
        return
    assurance, assurance_note = _load_assurance(run)
    _render_status(run, assurance, assurance_note)
    _render_progress(run, assurance)
    st.markdown("#### 原 run 的两节报告（历史对照）")
    _render_report(run, assurance, assurance_note)


def main() -> None:
    _render_styles()
    st.title("授信报告生成 · 演示工作台")
    st.caption("① 上传材料 → ② 生成过程 → ③ 报告与审核。第一屏上传三份电子 PDF 并点击"
               "「开始生成」后，本页会**新建一次运行**、把本次上传的字节交给同一条正式链，"
               "并且**只**展示这一次运行的产物；第四屏「历史对照 · 非本次生成」另存两个历史"
               "产物，不参与第 2、3 屏的任何读数。")
    results_root = _results_root()
    #: 历史对照的绑定失败**不**再挡住整个页面：前三屏读的是本次运行，与 `cdb-1` 无关。
    binding: binding_mod.CitedDisplayBinding | None = None
    binding_error = ""
    try:
        binding = binding_mod.load_display_binding(results_root)
    except (binding_mod.DisplayBindingError, OSError, ValueError) as exc:
        binding_error = str(exc)
    #: 深链：`?view=2` 或 `?view=<选项原文>` 直接落在某一屏。它**只**决定初始选中项，
    #: 不改变任一屏的读数或门控；而第 2、3 屏的内容必须来自本会话上传产生并绑定的 run——
    #: 没有就**拒绝进入**（回到第 1 屏并说明），不回落显示历史 run。
    if "cited_demo_view" not in st.session_state:
        wanted = str(st.query_params.get("view") or "").strip()
        picked = next((opt for i, opt in enumerate(_VIEW_OPTIONS)
                       if wanted in (str(i + 1), opt)), _VIEW_OPTIONS[0])
        if picked in _VIEW_OPTIONS[1:3] and _active_run_id() is None:
            st.session_state["cited_deeplink_refused"] = wanted
            picked = _VIEW_OPTIONS[0]
        st.session_state["cited_demo_view"] = picked
    view = st.radio("页面", _VIEW_OPTIONS, horizontal=True,
                    key="cited_demo_view", label_visibility="collapsed")
    if view == _VIEW_OPTIONS[0]:
        _render_new_run_upload()
    elif view == _VIEW_OPTIONS[1]:
        _render_new_run_process()
    elif view == _VIEW_OPTIONS[2]:
        _render_new_run_deliverable()
    else:
        _render_historical(binding, binding_error, results_root)


def _render_audit_detail(binding: binding_mod.CitedDisplayBinding,
                         results_root: str) -> None:
    """第四屏：绑定与版本痕迹 + **原本 run** 的历史审计区。"""
    st.header("审计详情 · 展示绑定与版本痕迹")
    st.caption(f"展示绑定 `{binding.binding_version}`：只读、逐字节钉住；它不是 `report_version`，"
               "也不是放行决定。")
    st.dataframe([
        {"来源": binding_mod.COMPANY_SOURCE.label, "run": binding_mod.COMPANY_SOURCE.run_id,
         "内容身份": binding.company.report_version["report_version"],
         "句数": binding.company.sentence_count},
        {"来源": binding_mod.FINANCIAL_SOURCE.label,
         "run": f"{binding_mod.FINANCIAL_SOURCE.run_id}/financial",
         "内容身份": binding.financial.fingerprint,
         "句数": f"{len(binding.financial.items)} 栏"},
    ], hide_index=True, width="stretch")
    with st.expander(f"绑定核过的产物哈希（{len(binding.artifact_hashes)} 个文件）",
                     expanded=False):
        st.json(binding.artifact_hashes, expanded=False)
    for note in binding.notes:
        st.caption(note)
    if binding.a2_review is not None:
        st.markdown("#### A2 既有审阅的独立内容身份")
        st.markdown(f"- 从当前 A2 产物重算：`{binding.a2_review.recomputed_scope_version}`")
        st.markdown(f"- 既有读数所记：　　`{binding.a2_review.recorded_scope_version}`")
        st.markdown(f"- 读数来源：`{binding.a2_review.sidecar_label}` · "
                    f"sha256 `{binding.a2_review.sidecar_sha256[:16]}…`")
        st.caption(binding.a2_review.reason)
    st.divider()
    _render_historical_audit(results_root)


if __name__ == "__main__":
    main()

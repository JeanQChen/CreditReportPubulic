"""Offline, create-only M930-4 deterministic assessment of a persisted cited run.

Example:
  python -m scripts.run_m930_4_assurance \
    --source-run evaluation/results/m930_3_cited_real_dual_v2_r1 \
    --report-review-run <dir>/report_review_run__material_selectivity.json \
    --output evaluation/results/m930_4_cited_v2_r2_reportreview

This command never calls a model, the research runtime, or a network service.
It writes three create-only files:

* ``assurance_diagnostic.json`` —— 确定性读数（含七态分列与已绑定的审阅意见）；
* ``report_review_inputs.json`` —— 三块报告级内容的**已备审阅输入** + 真实调用所需的分批计划；
* ``readback.md`` —— 人读读回，含调用授权门区块（真实调用的完整申请）。

``--report-review-run`` 可重复；每个文件经 ``report_reviewer.load_report_review_run``
**逐字段复验身份**后才进聚合，读不出来的记录让整条命令失败（不静默忽略：一份读不回来的
审阅记录如果被跳过，产物会看起来"这些内容没被审阅过"，而事实相反）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import config
from assurance import report_review as RR
from assurance import report_reviewer as RX
from assurance.cited_controller import assess_persisted_run, CitedControllerError


#: 建议的新 run-id 只是个**提案**，本命令绝不创建它；真实调用须逐次单独授权。
PROPOSED_REPORT_REVIEW_RUN_ID = "m930_4_report_review_v2_r1"


def _batch_plans(result: dict) -> dict:
    """逐 scope 的**真实调用后勤**：分批计划、每批容量、结构上界。

    它由已备输入**确定性地**推出（同一份输入永远得到同一批 `rrb_*`），因此可以随输入一起
    落盘：授权之后要跑的就是这些批次，不多不少。本命令只算不发。
    """
    plans: dict[str, dict] = {}
    for scope_dict in result["report_review_inputs"]:
        scope = RR.ReportReviewScope.from_dict(scope_dict)
        batches = RX.plan_report_review_batches(scope)
        plans[scope.scope_kind] = {
            "scope_id": scope.scope_id,
            "scope_version": scope.scope_version,
            "bundle_id": scope.bundle.bundle_id,
            "structural_bound": RX.report_review_structural_bound(scope),
            "batches": [batch.summarize() for batch in batches],
        }
    return plans


def _readback(result: dict, plans: dict) -> str:
    lines = [
        "# M930-4 确定性审核读回（非正式发布）",
        "",
        f"- 来源 run：`{result['source_run']}`（只读，一字未改）",
        f"- 本命令发出的模型调用：**{result['new_llm_calls']}**；"
        "既有 A1 逐句审阅仅作只读复核。",
        "- 报告级三块内容的运行状态（不是它们的输入状态）："
        f"跨节 `{result['cross_section_semantic_review']}`；"
        f"未采用材料的选择性遗漏 `{result['unused_material_selectivity_review']}`；"
        f"财务 A2 `{result['report_states']['review_coverage']['financial_a2']}`。",
        "- 正式阶段关闭：**本控制器未裁定**；人工接受没有被修改。",
        "- 跨节 report_version：**未伪造**，以下是各节自己的身份。",
        "",
        "## 1. 逐节读数",
        "",
        "| 节 | report_version | A1 硬错 | 既有审阅范围 | A2 审阅 | M930-4 系统状态 |",
        "|---|---|---:|---|---|---|",
    ]
    for item in result["section_assessments"]:
        a = item["assurance_result"]
        lines.append(
            f"| {item['section_id']} | `{item['report_version']}` | "
            f"{len(item['blocked_sentence_ids'])} | {item['prior_review_scope']} | "
            f"{item['a2_review_state']} | `{a['system_review_state']}` |")
    lines.extend([
        "",
        "两节均只可预览，`system_release_eligible=false`。公司句级硬错不能被既有 "
        "`supported` 意见覆盖；财务 A2 未进入该次逐句审阅，不能凭 A1 的零硬错放行。",
        "",
        "## 2. 报告级审阅输入（已备）",
        "",
        "这三块是读者实际看得到、而 A1 逐句审阅从未表态的内容。下表是**输入**，不是审阅结果："
        "`state` 描述的永远是**输入**的状态；这些内容有没有真的被审阅、由谁审阅，"
        "看 §3 的审阅覆盖与审阅意见两格。",
        "",
        "| scope | scope_version | 单元 | 覆盖句 | 状态 | 调用上限（推出） | 请求面字符 | 请求面 sha256 |",
        "|---|---|---:|---:|---|---:|---:|---|",
    ])
    for scope in result["report_review_scopes"]:
        ceiling = scope.get("derived_batch_count")
        ceiling_text = ("推导失败：" + str(scope.get("derivation_error")))
        if ceiling is not None:
            ceiling_text = str(ceiling)
        lines.append(
            f"| `{scope['scope_kind']}` | `{scope['scope_version']}` | "
            f"{scope['unit_count']} | {scope['covered_sentence_count']} | "
            f"`{scope['state']}` | {ceiling_text} | {scope['request_char_count']} | "
            f"`{scope['request_fingerprint'][:16]}…` |")
    lines.extend([
        "",
        "| scope | bundle_id | 并列的正文版本（仅链接，不进内容身份） |",
        "|---|---|---|",
    ])
    for scope in result["report_review_scopes"]:
        parents = "、".join(f"`{v}`" for v in scope["parent_report_versions"])
        lines.append(f"| `{scope['scope_kind']}` | `{scope['bundle_id']}` | {parents} |")
    lines.extend([
        "",
        "财务 A2 的内容身份独立于本次逐节 `crpv_*`：`financial_a2` 的 `scope_version` 只由 A2 "
        "产物自己的字节派生，正文版本只作并列链接；因此「正文改一字」与「A2 改一字」是两条"
        "各自独立的失效轴。既有审阅的原始响应哈希仅作留存读数，未复算 provider 原始响应字节。",
        "",
        "### 2.1 真实调用的分批计划（**输入面推出的上限**）",
        "",
        "单元数与输入体量决定这些批次，不是拍出来的：单元数上限由**输出**档位决定"
        "（单次 `max_tokens` 8192，而意见条数随单元数增长），字符上限只是别把整块塞进一次"
        "请求。同一份输入永远得到同一批 `rrb_*`（内容是地址）。**逐 scope 的调用上限就是"
        "这里的批数**，由 `report_review_inputs.json` 里那几条冻结分批规则在这份输入上真的"
        "切一遍推出——输入面只声明规则，不声明批数。",
        "",
        "| scope | 单元 | 批数 | 每批单元 | 每批字符 | 每批估入 token | 每批出 token 上限 |",
        "|---|---:|---:|---|---:|---:|---:|",
    ])
    for kind in sorted(plans):
        plan = plans[kind]
        bound = plan["structural_bound"]
        lines.append(
            f"| `{kind}` | {bound['units']} | {bound['batches']} | "
            f"≤{bound['max_batch_units']} | ≤{bound['max_batch_chars']} | "
            f"≤{max(b['estimated_input_tokens'] for b in plan['batches'])} | "
            f"{bound['max_output_tokens']} |")
    total_batches = sum(p["structural_bound"]["batches"] for p in plans.values())
    bound_runs = result.get("report_review_runs") or []
    opinions = result["report_states"]["review_opinions"]
    attempted = sum(row["requested_batch_count"] for row in opinions.values()
                    if row.get("run_id") is not None)
    recorded = result["report_review_calls_recorded"]
    lines.extend([
        "",
        "逐批明细（含 `batch_id` 与请求面 sha256）见同目录 `report_review_inputs.json` 的 "
        "`report_review_batch_plans`。",
        "",
    ])
    if bound_runs:
        lines.extend([
            f"**计划与实绩要分账。** 输入面推出的上限是 **{total_batches} 批**；"
            f"实际**发起 {attempted} 批**，其中**成功落批 {recorded} 条**"
            f"（差 {attempted - recorded} 批：发出去了，但回复不可用，因此没有意见记录）。"
            "上表的批数是**上限**，不是「已经跑完的批数」；实绩也不因为小于上限就等于"
            "「内容已覆盖」——哪些成员真的进过请求、哪些从未发出，见 §4。",
        ])
    else:
        lines.extend([
            f"合计 **{total_batches} 次**调用（= 上表批数之和）。这三块内容"
            "**尚未发出**任何调用——产物里没有任何绑定记录，上面的批数只是计划。",
        ])
    lines.extend([
        "",
        "## 3. 七个状态（互不推导）",
        "",
        "| 状态 | 读数 |",
        "|---|---|",
    ])
    states = result["report_states"]
    for sid, row in sorted(states["mechanical"].items()):
        lines.append(f"| 机械错误 · {sid} | {row['blocked_sentence_count']} 句"
                     f"（{', '.join(row['blocked_sentence_ids']) or '无'}） |")
    for key, value in sorted(states["review_coverage"].items()):
        lines.append(f"| 审阅覆盖 · {key} | `{value}` |")
    for key, row in sorted(states["review_opinions"].items()):
        if "run_id" in row:  # 报告级 scope：有运行才有意见，且"跑过但非独立审阅"要如实说
            if row["run_id"] is None:
                lines.append(f"| 审阅意见 · {key} | 无（审阅**未运行**，输入已备） |")
            else:
                lines.append(
                    f"| 审阅意见 · {key} | {row['issue_count']} 条"
                    f"（blocking {row['blocking_count']}；"
                    f"放进请求 {row['requested_unit_count']} 单元 / "
                    f"收到可解析回复 {row['covered_unit_count']} 单元 / "
                    f"模型提出问题 {row['reported_unit_count']} 单元"
                    f"（共声明 {row['declared_unit_count']}）；"
                    f"批 试/成/败 = {row['requested_batch_count']}/"
                    f"{row['completed_batch_count']}/{row['failed_batch_count']}，"
                    f"{row['llm_call_count']} 次调用；"
                    f"产出者 `{row['producer_kind']}`，"
                    f"可作审阅输入={row['trusted_as_assurance_input']}） |")
        elif row.get("trusted_as_assurance_input"):
            lines.append(f"| 审阅意见 · {key} | {row['issue_count']} 条"
                         f"（supported {row['supported_count']}，"
                         f"blocking {row['blocking_count']}；"
                         f"以下两节逐句审阅是 `irv-1` 时期的**历史**产物，那时逐单元表态；"
                         f"`irv-2` 起审阅只报发现，故报告级三块不会有 `supported` 计数） |")
        else:
            lines.append(f"| 审阅意见 · {key} | 无（审阅未运行） |")
    for row in states["stale_report_review_runs"]:
        lines.append(f"| 陈旧审阅（**未聚合**） · {row['scope_kind']} | "
                     f"`{row['run_id']}` 绑在 `{row['run_scope_version']}`，"
                     f"当前为 `{row['current_scope_version']}`（{row['issue_count']} 条意见未计入） |")
    for sid, value in sorted(states["preview"].items()):
        lines.append(f"| 预览可用 · {sid} | `{value}` |")
    lines.append(f"| 系统放行 | `{states['system_release']['eligible']}`"
                 f"（两节 `system_review_not_passed`） |")
    lines.append(f"| 人工接受 | `{states['human_acceptance']['state']}`"
                 f"（无可写接口） |")
    lines.append(f"| 正式阶段关闭 | **未由本控制器计算**"
                 f"（`{states['formal_phase_closure']['value']}`） |")
    lines.extend([
        "",
        "机械错误 / 审阅是否覆盖 / 审阅意见 / 预览可用 / 系统放行 / 人工接受 / 正式阶段关闭 "
        "是七个各自独立的格子，互不推导，不得压成一个 `success`。审阅意见**不移动**其余六格，"
        "这在 `report_states.review_opinion_effect` 里是五个显式布尔量，不必从散文里推断。",
        "",
        "报告级那三行里的数字要按 `report_states.unit_count_meaning` 读："
        "**放进请求**（含失败批）、**进入收到可解析回复的批次**、**模型实际提出问题**，"
        "三者互不推导。程序能从请求推出前两项，因此**不得**把它转述成「模型逐条核实了每份"
        "材料」——产物里没有表达那件事的字段。`supported_count` 见 "
        "`report_states.review_opinions.<scope>.supported_count_meaning`：`irv-2` 起审阅"
        "只报发现，该数通常为 0，**空意见列表**只表示「本次调用没有报告会实质改变结论的问题」，"
        "不表示材料被核实、不表示系统放行、不表示人工接受。",
        "",
        f"本控制器自身发出的模型调用：**{result['new_llm_calls']}** 次；产物里**已记录**的"
        f"报告级审阅调用：**{result['report_review_calls_recorded']}** 次。两者不必相等，"
        "也不用相等：前者说本命令没有发请求，后者说已持久化的记录里有多少次调用，"
        "两个数分开写是为了不让「这儿没发」被读成「哪儿都没发」。",
        "",
    ])
    lines.extend(_section_real_calls(result, plans, total_batches))
    lines.extend(_section_closing(bool(result.get("report_review_runs"))))
    return "\n".join(lines)


#: 审阅的权限边界。放在**编号独立**的一节里，是因为它在两支读数里都成立，
#: 而两支读数各自已经用掉了 §4 下面不同数量的子节号。
_REVIEW_POWERS = [
    "审阅**只提意见**：不改稿、不补研究、不重算任何数字、不覆盖机械硬错、不放行、"
    "不代表人工接受。意见的 schema 是 `rvi-1`（目标身份由 `unit_ref` 承载），"
    "因为这三块内容的单元不是句子；`rvi-2` 会强制一个不存在的句 id。",
]


def _section_closing(has_runs: bool) -> list[str]:
    """收尾两节。编号跟着上一节实际用到哪一号走，不然「§5.5」会挂在一个不存在的 §5 底下。"""
    if has_runs:
        return [
            "## 5. 审阅能做什么、不能做什么",
            "",
            *_REVIEW_POWERS,
            "",
            "## 6. 其它边界",
            "",
            "PDF 表区只核过页码，完整、清晰、忠实的人工确认未由本控制器代签。",
            "源章节预览与历史 run 保持原样，M930-3 内容门未因此关闭。",
            "",
        ]
    return [
        "## 5. 其它边界",
        "",
        "PDF 表区只核过页码，完整、清晰、忠实的人工确认未由本控制器代签。",
        "源章节预览与历史 run 保持原样，M930-3 内容门未因此关闭。",
        "",
    ]


#: 逐条读报告级意见时要同时读到的话。与页面上那段同源：**原始意见不改**，
#: 只把「正文未采用」与「来源缺失」两件事分开，并说明三条同栏目意见之间的关系。
_ISSUE_NOTES = (
    "上表是**原始意见**，一字未改。读它们时同时读到三件事：\n\n"
    "1. 意见说的是**正文没有把该数字写进相应分析**，不是**整份报告缺这些事实**——"
    "`f51`–`f56` 等数值已经在财务节的确定性权威指标表里展示，问题在于 A1 正文没有引用"
    "它们做分析。不要把「正文未采用」读成「来源没有」。\n"
    "2. 判为 `high` 的三条指向**同一个**栏目 `fin_solvency.net_asset_level` 的**同一个**"
    "段落缺口，是同一件事的三次表达，不是三个独立缺陷；原始意见不合并、不删除，"
    "此处只说明关系。\n"
    "3. 单条材料被指「未被采用」**不等于**该 Contract 栏目只差这一个科目就算完成——"
    "`suggested_target` 只是「建议补到哪一栏」，不是栏目完成证明。财务权威与栏目路由"
    "不因这些意见改变。")


def _issue_rows(result: dict) -> list[dict]:
    rows: list[dict] = []
    for run in result.get("report_review_runs") or []:
        for issue in run["issues"]:
            unit = issue["unit_ref"]
            target = issue.get("suggested_target") or {}
            rows.append({
                "scope": run["scope_kind"],
                "issue_id": issue["issue_id"],
                "category": issue["category"],
                "severity": issue["severity"],
                "blocking": issue["blocking"],
                "unit": f"{unit['unit_kind']}:{unit['unit_id']}",
                "evidence": "、".join(issue["evidence_refs"]),
                "target": target.get("target_ref") or "—",
                "reason": issue["reason"],
            })
    return rows


def _section_real_calls(result: dict, plans: dict, total_batches: int) -> list[str]:
    """§4：真实调用**已经发生**与**尚未发生**是两段完全不同的文字。

    这一节以前每份读数都写「未发起任何调用，停在这里等逐次授权」。审阅真的跑过之后，
    那句话就变成一句**陈旧**的话——读者会把这些意见读成「还没跑」。因此按产物里
    有没有绑定记录分成两支，两支各自把数说清。
    """
    bound = result.get("report_review_runs") or []
    if not bound:
        return _section_pending_calls(plans, total_batches)
    return _section_real_calls_done(result, bound)


def _section_real_calls_done(result: dict, bound: list[dict]) -> list[str]:
    states = result["report_states"]
    opinions = states["review_opinions"]
    attempts = sum(row["requested_batch_count"] for _, row in sorted(opinions.items())
                   if row.get("run_id") is not None)
    recorded = result["report_review_calls_recorded"]
    lines = [
        "## 4. 真实调用：逐 scope 实际读数",
        "",
        "**这一节说的是已经发生的事，不是申请。** 三块内容里哪些跑过、跑到第几批、"
        "提出了什么、哪里失败、哪些成员没进过请求，都在这张表里。",
        "",
        "| scope | 结果 | 已发起批 | 已落批记录 | 失败批 | 放进请求 | 收到可解析回复 | "
        "模型提出问题 | 未覆盖单元 | 意见 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in sorted(bound, key=lambda item: item["scope_kind"]):
        kind = run["scope_kind"]
        row = opinions[kind]
        lines.append(
            f"| `{kind}` | `{run['outcome']}` | {row['requested_batch_count']} | "
            f"{row['completed_batch_count']} | {row['failed_batch_count']} | "
            f"{row['requested_unit_count']} | {row['covered_unit_count']} | "
            f"{row['reported_unit_count']} | {row['excluded_unit_count']} | "
            f"{row['issue_count']} 条 |")
    lines.extend([
        "",
        f"**调用次数分两本账**：**预算门尝试 {attempts}** 次（= 各 scope 已发起的批次数之和，"
        f"含失败批）与**已落批记录 {recorded}** 条。两者相差 {attempts - recorded} 次，"
        "就是从预算门记账、真的发往了 provider、但回复不可用的那一批——"
        "半截或不可解析的回复**不是意见**，不给它留记录，于是它的成员退出"
        "「收到可解析回复」、落进「未覆盖单元」。"
        "（本次运行 `自动重试 0`，因此「已发起批次数」与预算门流水里的尝试数相等；"
        "一旦开重试，两个数就会分开，届时以调用账本流水为准。）",
        "",
        "`unit_count_meaning`：**放进请求**（含失败批）/ **收到可解析回复** / "
        "**模型提出问题**三者互不推导，**没有一个**表示「逐份材料被核实」。"
        "`supported_count` 在 `irv-2` 下通常为 0，**空意见列表**只表示"
        "「本次调用没有报告会实质改变结论的问题」。",
        "",
    ])
    for run in sorted(bound, key=lambda item: item["scope_kind"]):
        kind = run["scope_kind"]
        excluded = [str(x) for x in (run.get("excluded_unit_ids") or [])]
        failed = run.get("failed_batch_ids") or []
        if run["outcome"] != "reviewed":
            requested = {str(x) for x in (run.get("requested_unit_ids") or [])}
            entered = [x for x in excluded if x in requested]
            never = [x for x in excluded if x not in requested]
            lines.append(
                f"- **`{kind}` 未跑完**：失败批 {len(failed)} 个，"
                f"**{len(excluded)} 个成员未获得可解析审阅**。"
                f"这 {len(excluded)} 个不是同一种情况，必须分开读：")
            if entered:
                lines.append(
                    f"  - **{len(entered)} 个真的进了请求**"
                    f"（`{entered[0]}` … `{entered[-1]}`）：它们所在的那一批发了出去，"
                    "但回复不可解析，所以既没有留下意见记录、也没有回到「收到可解析回复」。"
                    "**这一批的内容没有人读到过。**")
            if never:
                lines.append(
                    f"  - **{len(never)} 个从未发进请求**"
                    f"（`{never[0]}` … `{never[-1]}`）：审阅在它们之前就中止了，"
                    "它们连请求都没进过。")
            lines.append(
                "  这一块不得读成「已审阅、没问题」，也不得把这 "
                f"{len(excluded)} 个合并成一句「都从未发出」——"
                "第一拨是真的发出去了的。")
        else:
            lines.append(f"- `{kind}` 已跑完：{len(run['covered_unit_ids'])} 个成员全部"
                         f"进入过请求，{len(run['issues'])} 条意见。")
    rows = _issue_rows(result)
    lines.append("")
    if rows:
        lines.extend([
            "### 4.1 提出的意见（原文照登，逐条保留）",
            "",
            "| scope | issue_id | 类别 | 严重度 | 阻断 | 指向单元 | 证据出处 | 建议补到 |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for row in rows:
            lines.append(
                f"| `{row['scope']}` | `{row['issue_id']}` | `{row['category']}` | "
                f"`{row['severity']}` | {'**是**' if row['blocking'] else '否'} | "
                f"`{row['unit']}` | {row['evidence']} | `{row['target']}` |")
        lines.extend(["", "理由原文：", ""])
        for row in rows:
            lines.append(f"- `{row['issue_id']}`（`{row['severity']}`"
                         f"{'，阻断' if row['blocking'] else ''}）：{row['reason']}")
        lines.extend(["", _ISSUE_NOTES])
    else:
        lines.extend([
            "### 4.1 提出的意见",
            "",
            "**没有任何意见。** 这只说明这几次调用没有报告问题，不说明材料被逐条核实，"
            "也不说明放行或人工接受。",
        ])
    lines.extend([
        "",
        "### 4.2 权限与预算（本次运行的事实，不是申请）",
        "",
        f"- 实际用的模型策略：`{bound[0]['model_policy_id']}`；提示词版本："
        f"`{bound[0]['prompt_version']}`；",
        f"- 链策略版本：`{RX.REPORT_REVIEW_CHAIN_POLICY_VERSION}`；"
        f"输入准备策略：`{RR.REPORT_REVIEW_POLICY_VERSION}`；",
        "- 每一次尝试在**发请求之前**记账：失败尝试同样占一次额度，流水里不会消失；",
        "- 任一批失败即抛出并**带回已完成的批次**：`aborted_report_review_run` 把它如实落成 "
        "`ReportReviewRun(outcome=\"failed\")`，已跑完的批次带着自己的记录与意见，"
        "未获得可解析审阅的单元逐条记进 `excluded_unit_ids`——"
        "这本名单**混着两种成员**：真的进了失败批的（`requested_unit_ids` ∩ "
        "`excluded_unit_ids`）与压根没发出去的（`excluded_unit_ids` 的其余部分），"
        "要看 §4 的分拨；",
        "- 落盘是 **create-only**（`x` 模式）：同名的 `report_review_run__<scope_kind>.json` "
        "已存在即失败，绝不覆盖已经发生过的调用记录。",
        "",
    ])
    return lines


def _section_pending_calls(plans: dict, total_batches: int) -> list[str]:
    """一份**还没有**任何绑定记录的读数：保留原来的「申请」形态。"""
    lines = [
        "## 4. 真实调用申请（**本命令未发起任何调用，停在这里等逐次授权**）",
        "",
        "### 4.1 要跑什么",
        "",
        f"- 模型：`{config.LLM_MODEL}`（逐次授权时绑定；未授权时预算门装不上强制模式）；",
        f"- prompt 资产：`{RX.REPORT_REVIEW_PROMPT_ASSET}`，版本 "
        f"`{RX.REPORT_REVIEW_PROMPT_VERSION}`（加载即与冻结常量对账，改修订号不改头部即失败）；",
        f"- 链策略版本：`{RX.REPORT_REVIEW_CHAIN_POLICY_VERSION}`；"
        f"输入准备策略：`{RR.REPORT_REVIEW_POLICY_VERSION}`（由 `scope_id` 绑定）；",
        f"- 建议新 run-id（**未创建**）：`{PROPOSED_REPORT_REVIEW_RUN_ID}`；",
        f"- 调用次数：**{total_batches} 次**（逐 scope 见 2.1）。",
        "",
        "### 4.2 每批预算",
        "",
        "| scope | 批 | 单元 | 输入字符 | 估入 token | `max_tokens` |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for kind in sorted(plans):
        for index, batch in enumerate(plans[kind]["batches"], start=1):
            lines.append(
                f"| `{kind}` | {index} | {batch['unit_count']} | {batch['char_count']} | "
                f"{batch['estimated_input_tokens']} | {batch['max_output_tokens']} |")
    lines.extend([
        "",
        "容量依据（只读自本仓 `logs/llm`，未发请求）：" + RX.REPORT_REVIEW_CAPACITY_BASIS,
        "",
        "### 4.3 事前预算门（未授权则整轮发不出去）",
        "",
        "预算走 `llm.budget` 的既有强制门，**不新建第二套账本**：类别 `report_review` 挂在"
        "独立轴 `report_review` 上，逐 scope 上限由 2.1 的分批数**推出**（不是拍的数），"
        "整轴上限是各 scope 之和。未获授权时 `LLMCallBudget(enforce=True)` 在**构造期**就抛错，"
        "因此「先花掉一部分额度、再撞在未批准的那次上」在结构上不可能。",
        "",
        f"需要在授权里逐条给出的数："
        + "、".join(f"`report:{k}` ≤ {plans[k]['structural_bound']['batches']}"
                    for k in sorted(plans))
        + f"；整轴 ≤ {total_batches}；模型 = `{config.LLM_MODEL}`。",
        "若授权给的上限**小于**结构上界，链会在发请求之前整体拒绝"
        "（`assert_budget_covers_bounds`）——上限低于上界不是「更严格」，而是把一次注定中途"
        "撞门的运行伪装成可运行。",
        "",
        "### 4.4 失败如何留存",
        "",
        "- 每一次尝试在**发请求之前**记账：失败尝试同样占一次额度，流水里不会消失；",
        "- 任一批失败即抛出并**带回已完成的批次**：`aborted_report_review_run` 把它如实落成 "
        "`ReportReviewRun(outcome=\"failed\")`，已跑完的批次带着自己的记录与意见，"
        "未覆盖的单元逐条记进 `excluded_unit_ids`；",
        "- 截断（`reject_truncated=True`）先记一条失败流水再抛：那条记录**没有** `text` / "
        "`response_hash`——半截内容不是意见，不给它算响应哈希；",
        "- 落盘是 **create-only**（`x` 模式）：同名的 `report_review_run__<scope_kind>.json` "
        "已存在即失败，绝不覆盖已经发生过的调用记录。",
        "",
        "未获授权前，上面的三块内容**未受审阅**，不得写成通过。",
        "",
    ])
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument(
        "--report-review-run", action="append", type=Path, default=[], metavar="PATH",
        help=("已持久化的报告级审阅记录（可重复）。每份都经 load_report_review_run "
              "逐字段复验身份；读不回来即失败，不静默跳过。"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    source = args.source_run.resolve(strict=True)
    output = args.output.resolve(strict=False)
    if output == source or source in output.parents:
        parser.error("Output must not replace or sit inside the immutable source run")
    if output.exists():
        parser.error(f"Create-only output already exists: {output}")
    review_runs = []
    for path in args.report_review_run:
        try:
            review_runs.append(RX.load_report_review_run(path.resolve(strict=True)))
        except (RX.ReportReviewError, OSError) as exc:
            parser.error(f"--report-review-run {path}: {exc}")
    try:
        result = assess_persisted_run(source, report_review_runs=tuple(review_runs))
    except CitedControllerError as exc:
        parser.error(str(exc))
    plans = _batch_plans(result)
    output.mkdir(parents=False, exist_ok=False)
    diagnostic = {key: value for key, value in result.items()
                  if key != "report_review_inputs"}
    with (output / "assurance_diagnostic.json").open("x", encoding="utf-8") as handle:
        json.dump(diagnostic, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    with (output / "report_review_inputs.json").open("x", encoding="utf-8") as handle:
        json.dump({"report_review_inputs": result["report_review_inputs"],
                   "report_review_batch_plans": plans},
                  handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    with (output / "readback.md").open("x", encoding="utf-8") as handle:
        handle.write(_readback(result, plans))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
